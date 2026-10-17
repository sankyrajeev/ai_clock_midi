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
import re
import socket
import struct
import subprocess
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

def _interface_for(peer_ip: str) -> tuple[str, bytes, str]:
    """(local IP, MAC, broadcast address) of the interface that reaches peer_ip."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((peer_ip, ANNOUNCE_PORT))
        local_ip = s.getsockname()[0]
    mac = uuid.getnode().to_bytes(6, "big")
    broadcast = None
    try:  # macOS / BSD style ifconfig gives us the real MAC and broadcast address
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3).stdout
        for block in re.split(r"\n(?=\S)", out):
            if re.search(rf"inet {re.escape(local_ip)}\b", block):
                if m := re.search(r"broadcast (\d+\.\d+\.\d+\.\d+)", block):
                    broadcast = m.group(1)
                if m := re.search(r"ether ([0-9a-f:]{17})", block):
                    mac = bytes.fromhex(m.group(1).replace(":", ""))
    except (OSError, subprocess.SubprocessError):
        pass
    if broadcast is None:
        parts = local_ip.split(".")
        broadcast = "169.254.255.255" if local_ip.startswith("169.254.") else ".".join(parts[:3] + ["255"])
    return local_ip, mac, broadcast


class VirtualCdj:
    """Joins the DJ Link network as a player so CDJs send us their status, and
    downloads phrase analysis for each track that gets loaded."""

    NAME = "ProLink Sync"
    startup_wait = 2.5      # seconds to watch the network before claiming a number
    claim_interval = 0.3    # spacing of the claim packets, as real players do
    db_query_port = DB_QUERY_PORT
    status_timeout = 8.0    # seconds after joining before warning that no status arrives

    def __init__(self, tracker: PhraseTracker, number: int = 0, posing_as: int = 0,
                 bind: str = "0.0.0.0", broadcast: Optional[str] = None):
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
        for target in (self._announce_listener, self._status_listener, self._join_and_keepalive, self._fetcher):
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
                if dev.number not in self.devices:
                    log.info("Found %s (device %d) at %s", dev.name, dev.number, dev.ip)
                self.devices[dev.number] = dev

    _claiming = 0

    def _join_and_keepalive(self) -> None:
        log.info("Looking for Pro DJ Link devices...")
        while self.alive and not self.devices:
            time.sleep(0.5)
        if not self.alive:
            return
        peer = next(iter(self.devices.values()))
        self.local_ip, self.mac, auto_bcast = _interface_for(peer.ip)
        bcast = self.broadcast or auto_bcast
        ip = socket.inet_aton(self.local_ip)
        time.sleep(self.startup_wait)  # let every device announce itself before picking a number
        self._claim(ip, bcast)
        log.info("Joined the network as device %d (%s, broadcast %s)", self.number, self.local_ip, bcast)
        joined, warned = time.monotonic(), False
        while self.alive:
            pkt = keepalive_packet(self.NAME, self.number, self.mac, ip, max(1, len(self.devices)))
            try:
                self.announce_sock.sendto(pkt, (bcast, ANNOUNCE_PORT))
            except OSError as e:
                log.warning("keep-alive failed: %s", e)
            if not warned and not self.status_seen and time.monotonic() - joined > self.status_timeout:
                warned = True
                log.warning(self._no_status_advice())
            time.sleep(1.5)

    def _no_status_advice(self) -> str:
        """Explain why no player status is arriving, naming any app sharing this Mac."""
        neighbours = sorted({d.name for d in self.devices.values()
                             if d.ip == self.local_ip and d.number != self.number})
        if neighbours:
            return (f"Not receiving player status. {', '.join(neighbours)} is running on this Mac and "
                    "is probably taking the status packets. Quit it, then restart this app.")
        return ("Not receiving player status. Check that no other DJ Link app (rekordbox, Bridge, "
                "ShowKontrol) is running on this Mac, then restart this app.")

    def _claim(self, ip: bytes, bcast: str) -> None:
        send = lambda pkt: self.announce_sock.sendto(pkt, (bcast, ANNOUNCE_PORT))  # noqa: E731
        for _ in range(3):
            send(hello_packet(self.NAME))
            time.sleep(self.claim_interval)
        candidates = [self.requested_number] if self.requested_number else list(range(7, 16))
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
                return
            log.info("Device number %d is taken, trying another", number)
        raise SystemExit("Could not claim a device number on the DJ Link network")

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
            if st.track_type == 1:
                blob = client.anlz_tag(st.slot, st.track_type, st.rekordbox_id, "PSSI", "EXT")
                if blob:
                    tp = parse_pssi(blob)
                    if tp.phrases:
                        return tp
            return self._from_waveform(client, st)
        finally:
            client.close()

    @staticmethod
    def _from_waveform(client: "DbClient", st: CdjStatus) -> Optional[TrackPhrases]:
        """No phrase analysis: work the sections out from the waveform instead."""
        import waveform

        grid_blob = client.beat_grid(st.slot, st.track_type, st.rekordbox_id)
        grid = waveform.parse_beat_grid(grid_blob) if grid_blob else []
        if not grid:
            return None
        for tag, ext in waveform.WAVEFORM_TAGS:
            blob = client.anlz_tag(st.slot, st.track_type, st.rekordbox_id, tag, ext)
            bands = waveform.parse_waveform(blob) if blob else None
            if bands is not None and len(bands.low):
                log.debug("analysing %s waveform (%d entries)", tag, len(bands.low))
                return waveform.analyse(bands, grid)
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
