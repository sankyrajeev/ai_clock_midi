"""Network phrase sync: rekordbox phrase analysis -> lighting cues over Pro DJ Link.

No audio cable needed. This joins the Pro DJ Link network as a virtual player,
watches which track each CDJ has loaded and where it is, downloads the track's
rekordbox phrase analysis (Intro / Up / Down / Chorus / Outro, or Verse /
Bridge for lower-energy tracks) straight from the player over the network,
and fires MIDI cues on the exact beat each phrase starts, optionally a few
milliseconds early to absorb DMX/fixture latency.

Protocol details follow Deep Symmetry's reverse engineering (dysentery,
crate-digger and beat-link):
  https://djl-analysis.deepsymmetry.org/
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Callable, Optional

log = logging.getLogger("prolink_sync.phrases")

MAGIC = b"Qspt1WmJOL"
ANNOUNCE_PORT, STATUS_PORT, DB_QUERY_PORT = 50000, 50002, 12523

# Cue names. The first five match the USB-audio detector, so one Lightkey
# mapping works for both modes.
BREAKDOWN, BUILD, DROP, PHRASE, BAR = "breakdown", "build", "drop", "phrase", "bar"
INTRO, OUTRO, VERSE, BRIDGE, PRE_DROP, NEW_TRACK = "intro", "outro", "verse", "bridge", "pre_drop", "new_track"

NOTES = {BREAKDOWN: 36, BUILD: 37, DROP: 38, PHRASE: 39, BAR: 40,
         INTRO: 41, OUTRO: 42, VERSE: 43, BRIDGE: 44, PRE_DROP: 45, NEW_TRACK: 46}
CC_ENERGY, CC_BUILD = 20, 23

# How "big" each kind of section is, sent as CC 20 so one fader mapping can
# follow the energy of the track.
INTENSITY = {INTRO: 0.35, VERSE: 0.55, BUILD: 0.6, BREAKDOWN: 0.25, BRIDGE: 0.4, DROP: 1.0, OUTRO: 0.35}


# --------------------------------------------------------------------------
# Port 50000: announcing ourselves as a player
# --------------------------------------------------------------------------

def _startup_packet(kind: int, name: str, body: bytes) -> bytes:
    pkt = bytearray(MAGIC + bytes([kind, 0]))
    pkt += name.encode("ascii")[:20].ljust(20, b"\x00")
    pkt += body
    return bytes(pkt)


def hello_packet(name: str) -> bytes:
    return _startup_packet(0x0A, name, bytes([0x01, 0x04, 0x00, 0x26, 0x01, 0x40]))


def claim1_packet(name: str, mac: bytes, n: int) -> bytes:
    return _startup_packet(0x00, name, bytes([0x01, 0x03, 0x00, 0x2C, n, 0x01]) + mac)


def claim2_packet(name: str, ip: bytes, mac: bytes, number: int, n: int, auto: bool) -> bytes:
    return _startup_packet(0x02, name, bytes([0x01, 0x03, 0x00, 0x32]) + ip + mac
                           + bytes([number, n, 0x01, 0x01 if auto else 0x02]))


def claim3_packet(name: str, number: int, n: int) -> bytes:
    return _startup_packet(0x04, name, bytes([0x01, 0x03, 0x00, 0x26, number, n]))


def keepalive_packet(name: str, number: int, mac: bytes, ip: bytes, peers: int) -> bytes:
    # CDJ-3000-compatible variant (0x64 at the end keeps players 5/6 happy).
    return _startup_packet(0x06, name, bytes([0x01, 0x02, 0x00, 0x36, number, 0x01]) + mac + ip
                           + bytes([peers, 0x00, 0x00, 0x00, 0x01, 0x64]))


@dataclass
class Device:
    number: int
    name: str
    ip: str
    seen: float


def parse_keepalive(data: bytes, received: float) -> Optional[Device]:
    if len(data) < 0x36 or data[:10] != MAGIC or data[0x0A] != 0x06:
        return None
    name = data[0x0C:0x20].split(b"\x00", 1)[0].decode("ascii", "replace")
    return Device(data[0x24], name, socket.inet_ntoa(data[0x2C:0x30]), received)


# --------------------------------------------------------------------------
# Port 50002: CDJ status packets
# --------------------------------------------------------------------------

@dataclass
class CdjStatus:
    device: int
    track_player: int   # device the track was loaded from
    slot: int           # 1 CD, 2 SD, 3 USB, 4 rekordbox laptop
    track_type: int     # 1 = rekordbox-analysed
    rekordbox_id: int
    play_state: int
    flags: int
    bpm: float
    beat: Optional[int]  # beat number within the track, 1-based
    beat_in_bar: int
    received: float

    @property
    def playing(self) -> bool:
        return bool(self.flags & 0x40)

    @property
    def master(self) -> bool:
        return bool(self.flags & 0x20)

    @property
    def on_air(self) -> bool:
        return bool(self.flags & 0x08)

    @property
    def track_key(self) -> Optional[tuple[int, int, int]]:
        # 1 = rekordbox track; 2 = unanalysed file (a CDJ-3000 still builds a beat grid for it)
        if self.track_type not in (1, 2) or self.rekordbox_id == 0 or self.track_player == 0:
            return None
        return (self.track_player, self.slot, self.rekordbox_id)


def parse_status(data: bytes, received: float) -> Optional[CdjStatus]:
    if len(data) < 0xA7 or data[:10] != MAGIC or data[0x0A] != 0x0A:
        return None
    beat = struct.unpack_from(">I", data, 0xA0)[0]
    bpm_raw = struct.unpack_from(">H", data, 0x92)[0]
    return CdjStatus(
        device=data[0x21], track_player=data[0x28], slot=data[0x29], track_type=data[0x2A],
        rekordbox_id=struct.unpack_from(">I", data, 0x2C)[0], play_state=data[0x7B],
        flags=data[0x89], bpm=0.0 if bpm_raw == 0xFFFF else bpm_raw / 100.0,
        beat=None if beat == 0xFFFFFFFF else max(1, beat), beat_in_bar=data[0xA6], received=received,
    )


def build_status_packet(device: int, track_player: int, slot: int, rekordbox_id: int, beat: int,
                        beat_in_bar: int, playing=True, master=False, on_air=True, bpm=128.0) -> bytes:
    """A minimal CDJ status packet, for tests and the simulator."""
    pkt = bytearray(0x200)
    pkt[:10] = MAGIC
    pkt[0x0A] = 0x0A
    pkt[0x0B:0x0B + 9] = b"CDJ-3000X"
    pkt[0x1F], pkt[0x20], pkt[0x21] = 0x01, 0x06, device
    struct.pack_into(">H", pkt, 0x22, 0x200 - 0x24)
    pkt[0x24], pkt[0x27] = device, 1 if playing else 0
    pkt[0x28], pkt[0x29], pkt[0x2A] = track_player, slot, 1
    struct.pack_into(">I", pkt, 0x2C, rekordbox_id)
    pkt[0x7B] = 0x03 if playing else 0x05
    pkt[0x89] = 0x84 | (0x40 if playing else 0) | (0x20 if master else 0) | (0x08 if on_air else 0)
    struct.pack_into(">H", pkt, 0x92, int(round(bpm * 100)))
    struct.pack_into(">I", pkt, 0xA0, beat)
    pkt[0xA6] = beat_in_bar
    return bytes(pkt)


# --------------------------------------------------------------------------
# dbserver: downloading the phrase analysis from a player
# --------------------------------------------------------------------------

MSG_MAGIC = 0x872349AE
ARG_NUMBER, ARG_BLOB, ARG_STRING = 0x06, 0x03, 0x02


def _num(value: int, size: int = 4) -> bytes:
    tag, fmt = {1: (0x0F, ">B"), 2: (0x10, ">H"), 4: (0x11, ">I")}[size]
    return bytes([tag]) + struct.pack(fmt, value)


def _blob(data: bytes) -> bytes:
    return b"\x14" + struct.pack(">I", len(data)) + data


def encode_message(txid: int, kind: int, args: list[int]) -> bytes:
    """Encode a request whose arguments are all 4-byte numbers."""
    types = bytes([ARG_NUMBER] * len(args)).ljust(12, b"\x00")
    return (_num(MSG_MAGIC) + _num(txid) + _num(kind, 2) + _num(len(args), 1) + _blob(types)
            + b"".join(_num(a) for a in args))


def fourcc_number(text: str) -> int:
    """Tag/extension names travel as byte-reversed ASCII numbers ("PSSI" -> 'ISSP')."""
    raw = text.encode("ascii").ljust(4, b"\x00")[:4]
    return int.from_bytes(raw[::-1], "big")


class _Reader:
    def __init__(self, sock: socket.socket):
        self.sock = sock

    def exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("dbserver closed the connection")
            buf += chunk
        return bytes(buf)

    def field(self):
        tag = self.exact(1)[0]
        if tag == 0x0F:
            return self.exact(1)[0]
        if tag == 0x10:
            return struct.unpack(">H", self.exact(2))[0]
        if tag == 0x11:
            return struct.unpack(">I", self.exact(4))[0]
        if tag == 0x14:
            return self.exact(struct.unpack(">I", self.exact(4))[0])
        if tag == 0x26:
            n = struct.unpack(">I", self.exact(4))[0]
            return self.exact(2 * n).decode("utf-16-be").rstrip("\x00")
        raise ValueError(f"unknown dbserver field type {tag:#x}")

    def message(self) -> tuple[int, int, list]:
        if self.field() != MSG_MAGIC:
            raise ValueError("bad dbserver message magic")
        txid, kind, argc, types = self.field(), self.field(), self.field(), self.field()
        args: list = []
        for i in range(argc):
            # A zero-length blob is omitted entirely, after the number that gives its length.
            if types[i] == ARG_BLOB and args and args[-1] == 0:
                args.append(b"")
                continue
            args.append(self.field())
        return txid, kind, args


class DbClient:
    """One short-lived connection to a player's database server."""

    def __init__(self, ip: str, posing_as: int, timeout: float = 4.0, query_port: int = DB_QUERY_PORT):
        port = self._db_port(ip, query_port, timeout)
        self.sock = socket.create_connection((ip, port), timeout=timeout)
        self.rd = _Reader(self.sock)
        self.sock.sendall(_num(1))
        if self.rd.field() != 1:
            raise ConnectionError("dbserver greeting failed")
        self.posing_as = posing_as
        self.txid = 0
        self.sock.sendall(encode_message(0xFFFFFFFE, 0x0000, [posing_as]))
        _, kind, _ = self.rd.message()
        if kind != 0x4000:
            raise ConnectionError(f"dbserver setup refused (response {kind:#06x})")

    @staticmethod
    def _db_port(ip: str, query_port: int, timeout: float) -> int:
        with socket.create_connection((ip, query_port), timeout=timeout) as s:
            s.sendall(struct.pack(">I", 15) + b"RemoteDBServer\x00")
            data = _Reader(s).exact(2)
        return struct.unpack(">H", data)[0]

    def anlz_tag(self, slot: int, track_type: int, rekordbox_id: int,
                 tag: str = "PSSI", ext: str = "EXT") -> Optional[bytes]:
        self.txid += 1
        dmst = (self.posing_as << 24) | (1 << 16) | (slot << 8) | track_type
        self.sock.sendall(encode_message(self.txid, 0x2C04,
                                         [dmst, rekordbox_id, fourcc_number(tag), fourcc_number(ext)]))
        _, kind, args = self.rd.message()
        if kind != 0x4F02 or len(args) < 4 or not args[2]:
            return None
        return args[3]

    def beat_grid(self, slot: int, track_type: int, rekordbox_id: int) -> Optional[bytes]:
        self.txid += 1
        dmst = (self.posing_as << 24) | (8 << 16) | (slot << 8) | track_type
        self.sock.sendall(encode_message(self.txid, 0x2204, [dmst, rekordbox_id]))
        _, kind, args = self.rd.message()
        if kind != 0x4602 or len(args) < 4 or not args[2]:
            return None
        return args[3]

    def close(self) -> None:
        try:
            self.sock.sendall(encode_message(0xFFFFFFFE, 0x0100, []))
        except OSError:
            pass
        self.sock.close()


# --------------------------------------------------------------------------
# Phrase analysis (PSSI tag of the rekordbox ANLZ .EXT file)
# --------------------------------------------------------------------------

_PSSI_MASK = bytes([0xCB, 0xE1, 0xEE, 0xFA, 0xE5, 0xEE, 0xAD, 0xEE, 0xE9, 0xD2,
                    0xE9, 0xEB, 0xE1, 0xE9, 0xF3, 0xE8, 0xE9, 0xF4, 0xE1])

HIGH, MID, LOW = 1, 2, 3
_HIGH_KINDS = {1: INTRO, 2: BUILD, 3: BREAKDOWN, 5: DROP, 6: OUTRO}
_HIGH_LABELS = {1: "Intro", 2: "Up", 3: "Down", 5: "Chorus", 6: "Outro"}
_MIDLOW_LABELS = {1: "Intro", 8: "Bridge", 9: "Chorus", 10: "Outro"}


@dataclass(frozen=True)
class Phrase:
    beat: int      # first beat of the phrase
    cue: str       # one of the cue names above
    label: str     # what rekordbox shows


@dataclass
class TrackPhrases:
    mood: int
    phrases: list[Phrase]
    end_beat: int
    source: str = "rekordbox"   # "rekordbox" phrases, or "waveform" analysis

    def index_at(self, beat: int) -> Optional[int]:
        """Index of the phrase playing at `beat`, or None before the first / after the end."""
        if beat >= self.end_beat:
            return None
        found = None
        for i, p in enumerate(self.phrases):
            if p.beat <= beat:
                found = i
            else:
                break
        return found


def _mask(body: bytes, n: int) -> bytes:
    return bytes(b ^ ((_PSSI_MASK[i % len(_PSSI_MASK)] + n) & 0xFF) for i, b in enumerate(body))


def _phrase_cue(mood: int, kind: int) -> tuple[str, str]:
    if mood == HIGH:
        return _HIGH_KINDS.get(kind, VERSE), _HIGH_LABELS.get(kind, f"Kind {kind}")
    if kind in (1, 10):
        return (INTRO if kind == 1 else OUTRO), _MIDLOW_LABELS[kind]
    if kind == 8:
        return BRIDGE, "Bridge"
    if kind == 9:
        return DROP, "Chorus"
    if mood == MID:
        return VERSE, f"Verse {kind - 1}"
    return VERSE, "Verse 1" if kind <= 4 else "Verse 2"


def parse_pssi(data: bytes) -> TrackPhrases:
    """Parse a PSSI tag. Accepts the dbserver blob (4-byte length prefix) or a bare tag."""
    start = data.find(b"PSSI")
    if start < 0:
        raise ValueError("no PSSI tag in data")
    tag = data[start:]
    len_tag = struct.unpack_from(">I", tag, 8)[0]
    tag = tag[:len_tag]
    entry_size = struct.unpack_from(">I", tag, 0x0C)[0]
    count = struct.unpack_from(">H", tag, 0x10)[0]
    body = tag[0x12:]
    if struct.unpack_from(">H", body, 0)[0] > 20:  # rekordbox 6+ scrambles everything after the count
        body = _mask(body, count)
    mood = struct.unpack_from(">H", body, 0)[0]
    end_beat = struct.unpack_from(">H", body, 8)[0]
    phrases = []
    for i in range(count):
        off = 0x0E + i * entry_size
        beat, kind = struct.unpack_from(">HH", body, off + 2)
        cue, label = _phrase_cue(mood, kind)
        phrases.append(Phrase(beat, cue, label))
    phrases.sort(key=lambda p: p.beat)
    return TrackPhrases(mood, phrases, end_beat)


def build_pssi(mood: int, phrases: list[tuple[int, int]], end_beat: int, masked: bool = True) -> bytes:
    """Build a PSSI tag from [(start_beat, kind)]; used by tests and the demo."""
    body = bytearray(struct.pack(">H", mood) + bytes(6) + struct.pack(">H", end_beat) + bytes(2) + b"\x00\x00")
    for i, (beat, kind) in enumerate(phrases, 1):
        body += struct.pack(">HHH", i, beat, kind) + bytes(18)
    if masked:
        body = bytearray(_mask(bytes(body), len(phrases)))
    head = b"PSSI" + struct.pack(">II", 0x20, 0x12 + len(body)) + struct.pack(">IH", 24, len(phrases))
    return head + bytes(body)


# --------------------------------------------------------------------------
# Following the music and firing cues
# --------------------------------------------------------------------------

@dataclass
class Deck:
    status: Optional[CdjStatus] = None
    beat: Optional[int] = None
    beat_in_bar: int = 0
    last_beat_packet: float = -1e9
    status_bump: float = -1e9   # when a status packet last moved the beat forward
    bpm: float = 0.0


class PhraseTracker:
    """Turns deck status + beats + phrase data into cue events."""

    def __init__(self, emit: Callable[[str], None], level: Callable[[int, float], None],
                 follow: str = "master", lead: float = 0.0):
        self.emit = emit
        self.level = level
        self.follow = follow
        self.lead = lead
        self.decks: dict[int, Deck] = {}
        self.analysis: dict[tuple[int, int, int], Optional[TrackPhrases]] = {}
        self.waveforms: dict[tuple[int, int, int], object] = {}  # waveform.Bands, for the screen
        self.lock = threading.RLock()
        self.followed: Optional[int] = None
        self.announced: Optional[tuple] = None   # (deck, track_key, phrase index) last announced
        self.timers: list[threading.Timer] = []
        self.on_track: Optional[Callable[[CdjStatus], None]] = None  # asks for analysis downloads

    # -- inputs -------------------------------------------------------------

    def on_status(self, st: CdjStatus) -> None:
        with self.lock:
            deck = self.decks.setdefault(st.device, Deck())
            old_key = deck.status.track_key if deck.status else None
            deck.status = st
            if st.bpm:
                deck.bpm = st.bpm
            if st.track_key != old_key:
                deck.beat = st.beat
                if st.track_key and st.track_key not in self.analysis and self.on_track:
                    self.on_track(st)
            elif st.beat is not None:
                recent_packet = st.received - deck.last_beat_packet < 0.25
                # A status sent just before the beat packet lags by one; ignore that.
                if deck.beat is None or not (recent_packet and st.beat == deck.beat - 1):
                    if deck.beat is not None and st.beat == deck.beat + 1:
                        deck.status_bump = st.received  # it beat the beat packet here
                    deck.beat = st.beat
            if st.beat_in_bar:
                deck.beat_in_bar = st.beat_in_bar
            self._choose_deck(st.received)
            self._check_position(st.received)

    def on_beat(self, device: int, beat_in_bar: int, bpm: float, t: float) -> None:
        with self.lock:
            deck = self.decks.get(device)
            if deck is None or deck.beat is None:
                return
            if t - deck.status_bump > 0.15:  # otherwise a status already counted this beat
                deck.beat += 1
            deck.status_bump = -1e9
            deck.beat_in_bar = beat_in_bar
            deck.last_beat_packet = t
            deck.bpm = bpm
            if device != self.followed:
                return
            if self.lead > 0:
                delay = max(0.0, 60.0 / bpm - self.lead)
                self._schedule(delay, device, deck.beat + 1, beat_in_bar % 4 + 1)
            else:
                self._fire_beat(device, deck.beat, beat_in_bar)

    def set_analysis(self, key: tuple[int, int, int], phrases: Optional[TrackPhrases]) -> None:
        with self.lock:
            self.analysis[key] = phrases
            self._check_position(time.monotonic())

    def has_phrases(self) -> bool:
        """True when the deck we follow has phrase data (used to gate the audio fallback)."""
        with self.lock:
            return self._followed_phrases() is not None

    def current(self) -> str:
        with self.lock:
            tp = self._followed_phrases()
            deck = self.decks.get(self.followed) if self.followed else None
            if not tp or not deck or deck.beat is None:
                return "no phrase data" if self.followed else "waiting"
            i = tp.index_at(deck.beat)
            return f"deck {self.followed}: {tp.phrases[i].label if i is not None else '-'} ({tp.source})"

    # -- internals ----------------------------------------------------------

    def _followed_phrases(self) -> Optional[TrackPhrases]:
        deck = self.decks.get(self.followed) if self.followed else None
        if not deck or not deck.status or not deck.status.track_key:
            return None
        return self.analysis.get(deck.status.track_key)

    def _choose_deck(self, now: float) -> None:
        fresh = {n: d for n, d in self.decks.items() if d.status and now - d.status.received < 2.0}
        choice = None
        if self.follow.isdigit():
            choice = int(self.follow) if int(self.follow) in fresh else None
        else:
            playing = sorted(n for n, d in fresh.items() if d.status.playing)
            masters = [n for n in playing if fresh[n].status.master]
            on_air = [n for n in playing if fresh[n].status.on_air]
            if self.follow == "on-air":
                choice = (on_air or masters or playing or [None])[0]
            else:
                choice = (masters or on_air or playing or [None])[0]
            if choice is None and self.followed in fresh:
                choice = self.followed  # keep following a paused deck
        if choice != self.followed:
            if choice is not None:
                log.info("Following deck %s for phrases", choice)
            self.followed = choice

    def _check_position(self, now: float) -> None:
        """Announce the current phrase if it changed outside the normal beat flow
        (deck switch, track load, hot-cue jump, analysis just arrived)."""
        deck = self.decks.get(self.followed) if self.followed else None
        tp = self._followed_phrases()
        if not deck or not tp or deck.beat is None:
            return
        key = deck.status.track_key
        idx = tp.index_at(deck.beat)
        if self.announced is None or self.announced[:2] != (self.followed, key):
            self.emit(NEW_TRACK)
        if idx is not None and self.announced != (self.followed, key, idx):
            self._announce(tp, idx, deck.beat)
        self.announced = (self.followed, key, idx)

    def _schedule(self, delay: float, device: int, beat: int, beat_in_bar: int) -> None:
        self.timers = [t for t in self.timers if t.is_alive()]
        timer = threading.Timer(delay, self._fire_beat_locked, (device, beat, beat_in_bar))
        timer.daemon = True
        self.timers.append(timer)
        timer.start()

    def _fire_beat_locked(self, device: int, beat: int, beat_in_bar: int) -> None:
        with self.lock:
            deck = self.decks.get(device)
            # Drop it if the deck jumped or stopped in the meantime.
            if device == self.followed and deck and deck.beat is not None and abs(deck.beat - beat) <= 1:
                self._fire_beat(device, beat, beat_in_bar)

    def _fire_beat(self, device: int, beat: int, beat_in_bar: int) -> None:
        """Everything that happens on `beat` of the followed deck."""
        if beat_in_bar == 1:
            self.emit(BAR)
        tp = self._followed_phrases()
        if tp is None:
            return
        key = self.decks[device].status.track_key
        idx = tp.index_at(beat)
        if idx is None:
            self.announced = (device, key, None)
            return
        phrase = tp.phrases[idx]
        if self.announced != (device, key, idx):
            if self.announced is None or self.announced[:2] != (device, key):
                self.emit(NEW_TRACK)
            self._announce(tp, idx, beat)
            self.announced = (device, key, idx)
        else:
            into = beat - phrase.beat
            if into > 0 and into % 32 == 0:
                self.emit(PHRASE)
        nxt = tp.phrases[idx + 1] if idx + 1 < len(tp.phrases) else None
        if nxt and nxt.cue == DROP and phrase.cue != DROP and nxt.beat - beat == 4:
            self.emit(PRE_DROP)
        if phrase.cue == BUILD:
            end = nxt.beat if nxt else tp.end_beat
            self.level(CC_BUILD, (beat - phrase.beat) / max(1, end - phrase.beat))

    def _announce(self, tp: TrackPhrases, idx: int, beat: int) -> None:
        phrase = tp.phrases[idx]
        log.debug("phrase %s (%s) at beat %d", phrase.label, phrase.cue, beat)
        self.emit(phrase.cue)
        self.level(CC_ENERGY, INTENSITY.get(phrase.cue, 0.5))
        if phrase.cue != BUILD:
            self.level(CC_BUILD, 0.0)


# --------------------------------------------------------------------------
# The virtual player: networking glue
# --------------------------------------------------------------------------

class VirtualCdj:
    """Joins the DJ Link network as a player so CDJs send us their status, and
    downloads phrase analysis for each track that gets loaded."""

    NAME = "ProLink Sync"
    startup_wait = 2.5      # seconds to watch the network before claiming a number
    claim_interval = 0.3    # spacing of the claim packets, as real players do
    db_query_port = DB_QUERY_PORT
    status_timeout = 8.0    # seconds after joining before warning that no status arrives
    device_timeout = 6.0    # players announce every ~2 s; silence this long means gone
    search_grace = 10.0     # how long to look before saying nothing was found

    def __init__(self, tracker: PhraseTracker, number: int = 0, posing_as: int = 0,
                 bind: str = "0.0.0.0", broadcast: Optional[str] = None, fix_network: bool = True):
        import netinfo

        self.net = netinfo
        self.list_ifaces = netinfo.interfaces   # swapped out in tests
        self.add_alias = netinfo.add_alias
        self.fix_network = fix_network
        self.fix_attempted: set[str] = set()
        self.problem: Optional[str] = None
        self.tracker = tracker
        self.requested_number = number
        self.number = 0
        self.posing_as = posing_as
        self.bind = bind
        self.broadcast = broadcast
        self.devices: dict[int, Device] = {}
        self.local_ip: Optional[str] = None
        self.mac = b"\x00" * 6
        self.alive = True
        self.claim_rejected = False
        self.fetch_queue: Queue = Queue()
        self.status_seen: set[int] = set()
        self.db_ports: dict[str, int] = {}
        tracker.on_track = lambda st: self.fetch_queue.put(st)

        self.announce_sock = self._udp(ANNOUNCE_PORT)
        self.status_sock = self._udp(STATUS_PORT)

    def _udp(self, port: int) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind((self.bind, port))
        s.settimeout(0.5)
        return s

    def start(self) -> None:
        for target in (self._announce_listener, self._status_listener, self._supervise, self._fetcher):
            threading.Thread(target=target, daemon=True).start()

    def close(self) -> None:
        self.alive = False
        self.announce_sock.close()
        self.status_sock.close()

    # -- joining -------------------------------------------------------------

    def _announce_listener(self) -> None:
        while self.alive:
            try:
                data, addr = self.announce_sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            if data[:10] != MAGIC:
                continue
            if data[0x0A] == 0x08 and len(data) >= 0x25 and data[0x24] == self._claiming:
                self.claim_rejected = True  # someone is defending the number we want
                continue
            dev = parse_keepalive(data, time.monotonic())
            if dev and not (dev.ip == self.local_ip and dev.number == self.number):
                old = self.devices.get(dev.number)
                if old is None:
                    log.info("Found %s (device %d) at %s", dev.name, dev.number, dev.ip)
                elif old.ip != dev.ip:
                    log.info("%s (device %d) moved to %s", dev.name, dev.number, dev.ip)
                self.devices[dev.number] = dev

    _claiming = 0

    def _set_problem(self, text: Optional[str]) -> None:
        if text == self.problem:
            return
        if text:
            log.warning(text)
        elif self.problem:
            log.info("Connection OK again")
        self.problem = text

    def _expire_devices(self, now: float) -> None:
        for number, dev in list(self.devices.items()):
            if now - dev.seen > self.device_timeout:
                self.devices.pop(number, None)
                log.info("Lost %s (device %d)", dev.name, number)

    def _supervise(self) -> None:
        """Join the network, keep the connection alive, and repair it when the
        cabling, the players or this Mac's address change."""
        log.info("Looking for Pro DJ Link devices...")
        searching_since = time.monotonic()
        first_join = True
        bcast, ip_bytes = "", b""
        joined = next_keepalive = 0.0
        warned_status = False
        while self.alive:
            now = time.monotonic()
            self._expire_devices(now)
            ifaces = self.list_ifaces()
            ours = self.net.local_ips(ifaces) | ({self.local_ip} if self.local_ip else set())
            devices = sorted(self.devices.values(), key=lambda d: d.number)
            peers = [d for d in devices if d.ip not in ours] or devices

            if not peers:
                if self.number:
                    log.info("All DJ Link devices are gone; waiting for them to come back")
                    self.number, self.local_ip = 0, None
                    searching_since = now
                if now - searching_since > self.search_grace:
                    self._set_problem(self.net.nothing_found_advice(ifaces))
                time.sleep(0.5)
                continue

            peer = peers[0]
            iface, addr = self.net.find_addr_for(peer.ip, ifaces)
            if addr is None and peer.ip in ours:  # only other apps on this Mac (and tests)
                iface, addr = next(((i, a) for i in ifaces for a in i.addrs if a.ip == peer.ip), (None, None))
            if addr is None:
                self._repair_subnet(peer, ifaces)
                time.sleep(1.0)
                continue

            if self.number == 0 or addr.ip != self.local_ip:
                if self.number:
                    log.info("This Mac's network address changed to %s; rejoining", addr.ip)
                if first_join:
                    time.sleep(self.startup_wait)  # let every device announce itself first
                self.local_ip = addr.ip
                self.mac = (iface.mac if iface and iface.mac else uuid.getnode().to_bytes(6, "big"))
                bcast = self.broadcast or addr.broadcast
                ip_bytes = socket.inet_aton(addr.ip)
                previous = self.number
                if not self._claim(ip_bytes, bcast, prefer=previous):
                    self._set_problem("Could not get a device number on the DJ Link network: "
                                      "too many devices are connected.")
                    time.sleep(2.0)
                    continue
                first_join = False
                joined, warned_status, next_keepalive = time.monotonic(), False, 0.0
                self.status_seen.clear()
                log.info("Joined the network as device %d (%s on %s, broadcast %s)", self.number,
                         addr.ip, iface.label if iface else "?", bcast)
                self._set_problem(None)

            if now >= next_keepalive:
                next_keepalive = now + 1.5
                pkt = keepalive_packet(self.NAME, self.number, self.mac, ip_bytes, max(1, len(self.devices)))
                try:
                    self.announce_sock.sendto(pkt, (bcast, ANNOUNCE_PORT))
                    if self.problem and self.problem.startswith("macOS is blocking"):
                        self._set_problem(None)
                except OSError as e:
                    self._set_problem(self._send_error_advice(e))
            if not warned_status and not self.status_seen and now - joined > self.status_timeout:
                warned_status = True
                self._set_problem(self._no_status_advice())
            elif self.status_seen and self.problem and self.problem.startswith("Not receiving"):
                self._set_problem(None)
            time.sleep(0.25)

    def _repair_subnet(self, peer: Device, ifaces: list) -> None:
        """A player is on a network this Mac has no address on (typically a CDJ
        stuck on an old IP). Give the Mac's Ethernet port an address next to it."""
        net = self.net.guess_network(peer.ip)
        wired = self.net.wired_with_link(ifaces)
        mine = ", ".join(a.ip for i in wired for a in i.addrs) or "no address"
        base = (f"{peer.name} is at {peer.ip}, but this Mac's Ethernet is on {mine}, "
                "so they can't talk directly.")
        if not wired:
            self._set_problem(base + " " + self.net.nothing_found_advice(ifaces))
            return
        if not self.fix_network:
            self._set_problem(base + f" Restart the player, or run: sudo ifconfig {wired[0].name} alias "
                              f"<free address on {net}> netmask {net.netmask}")
            return
        if str(net) in self.fix_attempted:
            return
        self.fix_attempted.add(str(net))
        taken = {d.ip for d in self.devices.values()} | self.net.local_ips(ifaces)
        ip, mask = self.net.choose_alias_ip(peer.ip, taken)
        log.info("%s Adding %s to %s so this Mac can reach it (macOS will ask for your password)...",
                 base, ip, wired[0].label)
        ok, detail = self.add_alias(wired[0].name, ip, mask)
        if ok:
            log.info("Network fixed: this Mac now also has %s on %s", ip, wired[0].label)
            self._set_problem(None)
        else:
            self._set_problem(f"{base} Automatic fix failed ({detail}). Restart the player so it picks "
                              "up a new address, or check that the Mac and players share one switch.")

    @staticmethod
    def _send_error_advice(err: OSError) -> str:
        if err.errno in (65, 13):  # EHOSTUNREACH / EACCES: macOS Local Network privacy
            return ("macOS is blocking this app from the local network. Open System Settings > "
                    "Privacy & Security > Local Network and switch on Terminal (or ProLink Sync).")
        return f"Can't send to the DJ Link network ({err.strerror}). Check the Ethernet cable and switch."

    def _no_status_advice(self) -> str:
        """Explain why no player status is arriving, naming any app sharing this Mac."""
        neighbours = sorted({d.name for d in self.devices.values()
                             if d.ip == self.local_ip and d.number != self.number})
        if neighbours:
            return (f"Not receiving player status. {', '.join(neighbours)} is running on this Mac and "
                    "is probably taking the status packets. Quit it, then restart this app.")
        return ("Not receiving player status. Check that no other DJ Link app (rekordbox, Bridge, "
                "ShowKontrol) is running on this Mac, then restart this app.")

    def _claim(self, ip: bytes, bcast: str, prefer: int = 0) -> bool:
        def send(pkt: bytes) -> None:
            try:
                self.announce_sock.sendto(pkt, (bcast, ANNOUNCE_PORT))
            except OSError as e:
                self._set_problem(self._send_error_advice(e))

        for _ in range(3):
            send(hello_packet(self.NAME))
            time.sleep(self.claim_interval)
        if self.requested_number:
            candidates = [self.requested_number]
        else:
            candidates = ([prefer] if prefer else []) + [n for n in range(7, 16) if n != prefer]
        for number in candidates:
            if number in self.devices and not self.requested_number:
                continue
            self._claiming, self.claim_rejected = number, False
            stages = ([claim1_packet(self.NAME, self.mac, n) for n in (1, 2, 3)]
                      + [claim2_packet(self.NAME, ip, self.mac, number, n, auto=not self.requested_number)
                         for n in (1, 2, 3)]
                      + [claim3_packet(self.NAME, number, n) for n in (1, 2, 3)])
            for pkt in stages:
                send(pkt)
                time.sleep(self.claim_interval)
                if self.claim_rejected:
                    break
            if not self.claim_rejected:
                self.number = number
                return True
            log.info("Device number %d is taken, trying another", number)
        return False

    # -- status --------------------------------------------------------------

    def _status_listener(self) -> None:
        while self.alive:
            try:
                data, _ = self.status_sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            st = parse_status(data, time.monotonic())
            if st and 1 <= st.device <= 6:
                if st.device not in self.status_seen:
                    self.status_seen.add(st.device)
                    log.info("Receiving status from player %d", st.device)
                self.tracker.on_status(st)

    # -- analysis downloads ----------------------------------------------------

    def _fetcher(self) -> None:
        while self.alive:
            try:
                st: CdjStatus = self.fetch_queue.get(timeout=0.5)
            except Empty:
                continue
            key = st.track_key
            if key is None or key in self.tracker.analysis:
                continue
            phrases = None
            for attempt in range(3):
                try:
                    phrases = self._fetch(st)
                    break
                except Exception as e:  # noqa: BLE001 - network/protocol errors; retry then give up
                    log.debug("phrase download attempt %d for %s failed: %s", attempt + 1, key, e)
                    time.sleep(1.0)
            if phrases:
                log.info("Deck %d: %d sections from %s (%s)", st.device, len(phrases.phrases), phrases.source,
                         " / ".join(p.label for p in phrases.phrases[:8]))
            else:
                log.info("Deck %d: no phrase or waveform data for this track (audio fallback if enabled)", st.device)
            self.tracker.set_analysis(key, phrases)

    def _fetch(self, st: CdjStatus) -> Optional[TrackPhrases]:
        source = self.devices.get(st.track_player)
        if source is None:
            raise LookupError(f"unknown source player {st.track_player}")
        # CDJ-3000s answer a virtual player posing as the source player's own number.
        posing = self.posing_as or (self.number if 1 <= self.number <= 4 else st.track_player)
        client = DbClient(source.ip, posing, query_port=self.db_query_port)
        try:
            phrases = None
            if st.track_type == 1:
                blob = client.anlz_tag(st.slot, st.track_type, st.rekordbox_id, "PSSI", "EXT")
                if blob:
                    tp = parse_pssi(blob)
                    phrases = tp if tp.phrases else None
            from_wave = self._from_waveform(client, st)  # also keeps the waveform for display
            return phrases or from_wave
        finally:
            client.close()

    def _from_waveform(self, client: "DbClient", st: CdjStatus) -> Optional[TrackPhrases]:
        """Download the track's waveform (kept for the screen) and work the sections out
        from it, for when there is no phrase analysis."""
        import waveform

        grid_blob = client.beat_grid(st.slot, st.track_type, st.rekordbox_id)
        grid = waveform.parse_beat_grid(grid_blob) if grid_blob else []
        for tag, ext in waveform.WAVEFORM_TAGS:
            blob = client.anlz_tag(st.slot, st.track_type, st.rekordbox_id, tag, ext)
            bands = waveform.parse_waveform(blob) if blob else None
            if bands is not None and len(bands.low):
                self.tracker.waveforms[st.track_key] = bands
                log.debug("got %s waveform (%d entries)", tag, len(bands.low))
                return waveform.analyse(bands, grid) if grid else None
        return None


# --------------------------------------------------------------------------
# MIDI out and the demo
# --------------------------------------------------------------------------

class CueMidi:
    """MIDI port for cues; also accepts events from the USB-audio detector."""

    def __init__(self, port_name: str, use_existing: bool, gate: Optional[Callable[[], bool]] = None):
        import mido

        self._mido = mido
        self.port = mido.open_output(port_name, virtual=not use_existing)
        self.gate = gate  # when it returns True, audio-detector cues are suppressed
        self._lock = threading.Lock()
        self._last_cc: dict[int, int] = {}
        log.info("MIDI cues on port %r: %s", port_name,
                 ", ".join(f"{k}={v}" for k, v in NOTES.items()))

    def event(self, name: str) -> None:
        note = NOTES.get(name)
        if note is None:
            return
        self._send("note_on", note=note, velocity=127)
        threading.Timer(0.08, self._send, ("note_off",), {"note": note, "velocity": 0}).start()

    def level(self, cc: int, value: float) -> None:
        v = max(0, min(127, int(round(value * 127))))
        if self._last_cc.get(cc) != v:
            self._last_cc[cc] = v
            self._send("control_change", control=cc, value=v)

    # Adapters for the USB-audio SectionDetector (sections.py).
    def audio_event(self, name: str, _t: float = 0.0) -> None:
        if not (self.gate and self.gate() and name in (BREAKDOWN, BUILD, DROP, PHRASE, BAR)):
            self.event(name)

    def audio_levels(self, lv: dict) -> None:
        phrases = bool(self.gate and self.gate())
        for name, cc in (("energy", 20), ("bass", 21), ("high", 22), ("build", 23)):
            if not (phrases and cc in (CC_ENERGY, CC_BUILD)):
                self.level(cc, lv[name])

    def _send(self, kind: str, **kw) -> None:
        with self._lock:
            self.port.send(self._mido.Message(kind, channel=0, **kw))

    def close(self) -> None:
        self.port.close()


# Short sections so every cue comes round quickly while MIDI-learning.
DEMO_TRACK = build_pssi(HIGH, [(1, 1), (17, 2), (33, 5), (65, 3), (81, 2), (97, 5), (129, 6)], 145)


def run_demo(tracker: PhraseTracker, bpm: float = 128.0, stop: Optional[threading.Event] = None) -> None:
    """Play a fake 128 BPM track (Intro/Up/Chorus/Down/Up/Chorus/Outro) through the
    tracker so cues can be MIDI-learned in Lightkey without any decks."""
    key = (1, 3, 999999)
    tracker.set_analysis(key, parse_pssi(DEMO_TRACK))
    beat_len = 60.0 / bpm
    beat, nxt = 1, time.monotonic()
    while stop is None or not stop.is_set():
        now = time.monotonic()
        bib = (beat - 1) % 4 + 1
        if beat > 1:
            tracker.on_beat(1, bib, bpm, now)  # beat packet first, like a real deck
        tracker.on_status(parse_status(
            build_status_packet(1, 1, 3, key[2], beat, bib, master=True, bpm=bpm), now))
        beat = beat % 144 + 1
        nxt += beat_len
        time.sleep(max(0.0, nxt - time.monotonic()))
