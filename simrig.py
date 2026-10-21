"""A pretend DJ booth on this Mac: two CDJ-3000Xs and a DJM-V10 on Pro DJ Link.

It speaks the real protocol on the loopback interface: keep-alives, beat
packets, player status, and a database server that hands out phrase
analysis, beat grids and waveforms. The app connects to it exactly as it
would to real gear, so everything from joining the network to firing cues
can be tried without decks.

The set: deck 2 plays a track that has rekordbox phrase analysis. Near its
end, deck 1 starts a track that has none (so the app has to read its
waveform), takes over as tempo master, and the decks keep alternating.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

import phrases as ph
import waveform as wf
from prolink_sync import build_beat_packet

log = logging.getLogger("prolink_sync.sim")

HOST = "127.0.0.1"
EPS = wf.ENTRIES_PER_SECOND

# Section layouts in bars: (bars, kind). Kept short so every cue comes round quickly.
SET = [
    {"title": "Rush Hour", "artist": "Mira Vale", "key": "8A", "bpm": 128.0, "phrases": True,
     "sections": [(8, "intro"), (8, "up"), (16, "drop"), (8, "down"), (8, "up"), (16, "drop"), (8, "outro")]},
    {"title": "Low Tide (Extended Mix)", "artist": "Okan & Sela", "key": "9A", "bpm": 126.0, "phrases": False,
     "sections": [(8, "intro"), (16, "drop"), (8, "down"), (8, "up"), (16, "drop"), (8, "outro")]},
]
PSSI_KIND = {"intro": 1, "up": 2, "down": 3, "drop": 5, "outro": 6}
BAND_LEVELS = {"intro": (120, 60, 40), "up": (20, 60, None), "drop": (200, 120, 120),
               "down": (15, 70, 30), "outro": (110, 55, 35)}


@dataclass
class SimTrack:
    rekordbox_id: int
    title: str
    artist: str
    key: str
    bpm: float
    beats: int
    pssi: Optional[bytes]
    grid: bytes
    waveform: bytes


def make_track(spec: dict, rekordbox_id: int, seed: int = 0) -> SimTrack:
    """Phrase tag (if any), beat grid and a CDJ-3000 style 3-band waveform with real kick pulses."""
    rng = np.random.default_rng(seed)
    bpm = spec["bpm"]
    beat_len = 60.0 / bpm
    lows, mids, highs = [], [], []
    starts, beat = [], 1
    for bars, kind in spec["sections"]:
        starts.append((beat, PSSI_KIND[kind]))
        n = int(round(bars * 4 * beat_len * EPS))
        t = np.arange(n) / EPS
        phase = (t % beat_len) / beat_len
        lo, mi, hi = BAND_LEVELS[kind]
        if hi is None:  # a riser: highs climb through the build
            hi = 40 + 160 * np.linspace(0, 1, n)
        kick = np.exp(-phase * 9) if lo > 60 else 0.3 * np.exp(-phase * 4)
        lows.append(lo * (0.45 + 0.75 * kick) * (1 + 0.08 * rng.standard_normal(n)))
        mids.append(mi * (0.85 + 0.15 * np.sin(2 * np.pi * t * 0.5)) * (1 + 0.1 * rng.standard_normal(n)))
        hat = 0.6 + 0.4 * (np.abs(phase - 0.5) < 0.08)
        highs.append(hi * hat * (1 + 0.1 * rng.standard_normal(n)))
        beat += bars * 4
    beats = beat - 1
    pssi = ph.build_pssi(ph.HIGH, starts, beats + 1) if spec["phrases"] else None
    return SimTrack(rekordbox_id, spec["title"], spec.get("artist", ""), spec.get("key", ""), bpm, beats, pssi,
                    wf.build_beat_grid(bpm, beats),
                    wf.build_waveform_tag(np.concatenate(lows), np.concatenate(mids), np.concatenate(highs)))


class SimDbServer:
    """The players' database server: answers port queries, setup, ANLZ tag and beat grid requests."""

    def __init__(self, tracks: dict[int, SimTrack], query_port: int = ph.DB_QUERY_PORT):
        self.tracks = tracks
        self.alive = True
        self.query = socket.socket()
        self.query.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.query.bind((HOST, query_port))
        self.query.listen()
        self.db = socket.socket()
        self.db.bind((HOST, 0))
        self.db.listen()
        threading.Thread(target=self._accept, args=(self.query, self._answer_port), daemon=True).start()
        threading.Thread(target=self._accept, args=(self.db, self._serve), daemon=True).start()

    def close(self) -> None:
        self.alive = False
        self.query.close()
        self.db.close()

    def _accept(self, server: socket.socket, handler) -> None:
        while self.alive:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    def _answer_port(self, conn: socket.socket) -> None:
        with conn:
            try:
                ph._Reader(conn).exact(19)
                conn.sendall(struct.pack(">H", self.db.getsockname()[1]))
            except (OSError, ConnectionError):
                pass

    def _serve(self, conn: socket.socket) -> None:
        rd = ph._Reader(conn)
        with conn:
            try:
                rd.field()
                conn.sendall(ph._num(1))
                pending = None
                while True:
                    txid, kind, args = rd.message()
                    if kind == 0x0000:
                        conn.sendall(ph.encode_message(txid, 0x4000, [0, 2]))
                    elif kind == 0x2C04:
                        _, rbid, tag, _ = args
                        name = tag.to_bytes(4, "big")[::-1].decode()
                        track = self.tracks.get(rbid)
                        data = b""
                        if track and name == "PSSI" and track.pssi:
                            data = track.pssi
                        elif track and name == "PWV7":
                            data = track.waveform
                        conn.sendall(self._reply(txid, 0x4F02, 0x2C04, data, prefix=True))
                    elif kind in (0x2002, 0x2202):
                        pending = self.tracks.get(args[1])
                        conn.sendall(ph.encode_message(txid, 0x4000, [kind, 4 if pending else 0xFFFFFFFF]))
                    elif kind == 0x3000 and pending:
                        conn.sendall(self._menu(txid, pending))
                    elif kind == 0x2204:
                        track = self.tracks.get(args[1])
                        conn.sendall(self._reply(txid, 0x4602, 0x2204, track.grid if track else b"", prefix=False))
                    elif kind == 0x0100:
                        return
            except (OSError, ConnectionError, ValueError):
                return

    @staticmethod
    def _menu(txid: int, track: SimTrack) -> bytes:
        """Menu header, title / artist / key / tempo items, footer: what a CDJ sends for metadata."""
        def string(text: str) -> bytes:
            raw = (text + "\x00").encode("utf-16-be")
            return b"\x26" + struct.pack(">I", len(raw) // 2) + raw

        def item(kind: int, main: int, label: str) -> bytes:
            types = bytes([6, 6, 6, 2, 6, 2, 6, 6, 6, 6, 6, 6])
            label_bytes = len((label + "\x00").encode("utf-16-be"))
            return (ph._num(ph.MSG_MAGIC) + ph._num(txid) + ph._num(0x4101, 2) + ph._num(12, 1) + ph._blob(types)
                    + ph._num(0) + ph._num(main) + ph._num(label_bytes) + string(label) + ph._num(2) + string("")
                    + ph._num(kind) + ph._num(0) + ph._num(0) + ph._num(0) + ph._num(0) + ph._num(0))

        header = ph.encode_message(txid, 0x4001, [])
        footer = ph.encode_message(txid, 0x4201, [])
        return (header + item(0x04, track.rekordbox_id, track.title) + item(0x07, 1, track.artist)
                + item(0x0F, 0, track.key) + item(0x0D, int(track.bpm * 100), "") + footer)

    @staticmethod
    def _reply(txid: int, kind: int, req: int, data: bytes, prefix: bool) -> bytes:
        blob = (struct.pack(">I", len(data)) + data if prefix else data) if data else b""
        types = bytes([6, 6, 6, 3, 6]).ljust(12, b"\x00")
        out = (ph._num(ph.MSG_MAGIC) + ph._num(txid) + ph._num(kind, 2) + ph._num(5, 1) + ph._blob(types)
               + ph._num(req) + ph._num(0) + ph._num(len(blob)))
        return out + (ph._blob(blob) if blob else b"") + ph._num(0)


@dataclass
class Deck:
    number: int
    track: Optional[SimTrack] = None
    beat: int = 0          # current beat, 1-based; 0 = cued at the start
    playing: bool = False
    master: bool = False
    pitch: float = 0.0     # percent


class SimRig:
    """Runs the pretend booth until stop() is called."""

    MIXER = 33

    def __init__(self, dropout_after: Optional[float] = None, query_port: int = ph.DB_QUERY_PORT):
        self.tracks = [make_track(spec, 1001 + i, seed=i) for i, spec in enumerate(SET)]
        self.db = SimDbServer({t.rekordbox_id: t for t in self.tracks}, query_port=query_port)
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.decks = {1: Deck(1), 2: Deck(2)}
        self.alive = True
        self.dropout_after = dropout_after   # seconds into the run to "pull the cable" for 10 s
        self.silent = False
        self.next_track = 0

    def start(self) -> None:
        self._load(2, start_beat=1)
        self._load(1)
        d2 = self.decks[2]
        d2.playing, d2.master, d2.pitch = True, True, 0.0
        for target in (self._announce, self._status, self._play):
            threading.Thread(target=target, daemon=True).start()
        log.info("Simulated booth running: CDJ-3000X (players 1 and 2) and DJM-V10 on %s", HOST)

    def stop(self) -> None:
        self.alive = False
        self.db.close()
        self.out.close()

    def _send(self, pkt: bytes, port: int) -> None:
        if not self.silent:
            try:
                self.out.sendto(pkt, (HOST, port))
            except OSError:
                pass

    def _load(self, deck: int, start_beat: int = 1) -> None:
        d = self.decks[deck]
        d.track = self.tracks[self.next_track % len(self.tracks)]
        self.next_track += 1
        d.beat, d.playing = start_beat, False

    def _announce(self) -> None:
        mac = bytes.fromhex("c8bcc8000000")
        ip = socket.inet_aton(HOST)
        while self.alive:
            for n in (1, 2):
                self._send(ph.keepalive_packet("CDJ-3000X", n, mac[:5] + bytes([n]), ip, 3), ph.ANNOUNCE_PORT)
            mixer = bytearray(ph.keepalive_packet("DJM-V10", self.MIXER, mac[:5] + b"\x21", ip, 3))
            mixer[0x34] = 0x02  # mixers mark themselves with 02 here
            self._send(bytes(mixer), ph.ANNOUNCE_PORT)
            time.sleep(1.5)

    def _status(self) -> None:
        while self.alive:
            for d in self.decks.values():
                t = d.track
                bib = (d.beat - 1) % 4 + 1 if d.beat else 0
                pkt = ph.build_status_packet(d.number, d.number, 3, t.rekordbox_id if t else 0, d.beat, bib,
                                             playing=d.playing, master=d.master, on_air=d.playing,
                                             bpm=t.bpm if t else 0)
                self._send(pkt, ph.STATUS_PORT)
            time.sleep(0.2)

    def _play(self) -> None:
        started = time.monotonic()
        nxt = time.monotonic()
        while self.alive:
            if self.dropout_after and time.monotonic() - started > self.dropout_after:
                self.dropout_after = None  # once only
                log.info("Simulating a pulled network cable for 10 seconds")
                self.silent = True
                threading.Timer(10.0, self._reconnect).start()
            master = next((d for d in self.decks.values() if d.master), self.decks[2])
            bpm = master.track.bpm * (1 + master.pitch / 100)
            for d in self.decks.values():
                if not d.playing:
                    continue
                d.beat += 1
                if d.beat > d.track.beats:
                    d.playing, d.master = False, False
                    self._load(d.number)
                    continue
                bib = (d.beat - 1) % 4 + 1
                self._send(build_beat_packet(d.number, d.track.bpm, bib, pitch=1 + d.pitch / 100), 50001)
                if d.master:
                    self._send(build_beat_packet(self.MIXER, d.track.bpm, bib, pitch=1 + d.pitch / 100,
                                                 name="DJM-V10"), 50001)
            self._mix()
            nxt += 60.0 / bpm
            time.sleep(max(0.0, nxt - time.monotonic()))

    def _reconnect(self) -> None:
        self.silent = False
        log.info("Simulated network cable plugged back in")

    def _mix(self) -> None:
        """Start the other deck 16 bars before the master's track ends, hand over master at the end,
        pitched to match the outgoing tempo like a DJ would."""
        master = next((d for d in self.decks.values() if d.master), None)
        other = next(d for d in self.decks.values() if d is not master)
        if master and master.playing and not other.playing and master.track.beats - master.beat == 64:
            other.pitch = (master.track.bpm * (1 + master.pitch / 100) / other.track.bpm - 1) * 100
            other.beat, other.playing = 1, True
            log.info("Deck %d starts mixing in", other.number)
        if master and master.track.beats - master.beat == 1 and other.playing:
            master.master, other.master = False, True
            log.info("Deck %d takes over as tempo master", other.number)
