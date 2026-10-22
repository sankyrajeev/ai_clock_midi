#!/usr/bin/env python3
"""Pro DJ Link -> MIDI Clock / Ableton Link bridge for Lightkey.

Listens passively for Pro DJ Link beat packets (UDP 50001) from CDJs and the
DJM mixer, follows the tempo master, and drives:

  * a virtual MIDI port ("Lumiere Clock") emitting 24 ppqn MIDI clock that is
    phase-locked to the beats coming off the decks, with Start on a downbeat
    so bars line up, and
  * optionally an Ableton Link session with the same tempo and bar phase.

Point Lightkey's tempo source at either one.

Beat packet layout follows the Deep Symmetry "dysentery" protocol analysis:
https://djl-analysis.deepsymmetry.org/djl-analysis/beats.html
"""

from __future__ import annotations

import argparse
import logging
import shutil
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("prolink_sync")

BEAT_PORT = 50001
MAGIC = b"Qspt1WmJOL"
PKT_BEAT = 0x28
PITCH_NORMAL = 0x100000
MIXER_MIN_DEVICE = 0x21  # DJM mixers report device number 33 and up

TICKS_PER_BEAT = 24
MIDI_CLOCK, MIDI_START, MIDI_STOP = 0xF8, 0xFA, 0xFC


# --------------------------------------------------------------------------
# Packet parsing
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Beat:
    device: int
    name: str
    track_bpm: float      # BPM of the track at 0% pitch
    pitch: float          # multiplier, 1.0 == 0%
    beat_in_bar: int      # 1..4
    received: float       # time.monotonic() at arrival

    @property
    def bpm(self) -> float:
        """Effective (pitched) tempo actually being played."""
        return self.track_bpm * self.pitch

    @property
    def is_mixer(self) -> bool:
        return self.device >= MIXER_MIN_DEVICE


def parse_beat(data: bytes, received: float) -> Optional[Beat]:
    if len(data) < 0x60 or data[:10] != MAGIC or data[0x0A] != PKT_BEAT:
        return None
    name = data[0x0B:0x1F].split(b"\x00", 1)[0].decode("ascii", "replace")
    device = data[0x21]
    pitch_raw = struct.unpack_from(">I", data, 0x54)[0]
    bpm_raw = struct.unpack_from(">H", data, 0x5A)[0]
    if bpm_raw in (0, 0xFFFF):  # no track loaded / no beat grid
        return None
    return Beat(
        device=device,
        name=name,
        track_bpm=bpm_raw / 100.0,
        pitch=pitch_raw / PITCH_NORMAL,
        beat_in_bar=data[0x5C],
        received=received,
    )


def build_beat_packet(device: int, bpm: float, beat_in_bar: int,
                      pitch: float = 1.0, name: str = "CDJ-3000X") -> bytes:
    """Build a beat packet; used by --simulate and the tests."""
    pkt = bytearray(0x60)
    pkt[:10] = MAGIC
    pkt[0x0A] = PKT_BEAT
    pkt[0x0B:0x0B + len(name)] = name.encode("ascii")[:20]
    pkt[0x1F] = 0x01
    pkt[0x21] = device
    struct.pack_into(">H", pkt, 0x22, 0x3C)
    beat_ms = int(60000 / (bpm * pitch))
    for i, mult in enumerate((1, 2, 4 - beat_in_bar + 1, 4, 8, 8)):
        struct.pack_into(">I", pkt, 0x24 + 4 * i, beat_ms * mult)
    pkt[0x3C:0x54] = b"\xff" * 0x18
    struct.pack_into(">I", pkt, 0x54, int(round(pitch * PITCH_NORMAL)))
    struct.pack_into(">H", pkt, 0x5A, int(round(bpm * 100)))
    pkt[0x5C] = beat_in_bar
    pkt[0x5F] = device
    return bytes(pkt)


# --------------------------------------------------------------------------
# Choosing which device's beats to follow
# --------------------------------------------------------------------------

class SourceSelector:
    """Decides whether an incoming beat comes from the device we follow.

    mode "auto":   follow the mixer (it emits beats tracking the tempo master,
                   so it follows master hand-offs between decks for free); if
                   the mixer goes quiet, fall back to the most recently heard
                   player until the mixer comes back.
    mode "mixer":  only the mixer.
    mode <int>:    only that player number (1-6).
    """

    def __init__(self, mode: str = "auto", stale_after: float = 2.0):
        self.mode = mode
        self.stale_after = stale_after
        self._last_mixer = -1e9
        self._current: Optional[int] = None
        self._last_current = -1e9

    def accept(self, beat: Beat) -> bool:
        if self.mode == "mixer":
            ok = beat.is_mixer
        elif self.mode != "auto":
            ok = beat.device == int(self.mode)
        elif beat.is_mixer:
            self._last_mixer = beat.received
            ok = True
        elif beat.received - self._last_mixer < self.stale_after:
            ok = False  # mixer is talking; ignore individual decks
        else:
            # No mixer: stick with one player while it keeps beating, and
            # move to another only once the current one has gone quiet.
            ok = (self._current is None or beat.device == self._current
                  or beat.received - self._last_current > self.stale_after)
        if ok:
            self._last_current = beat.received
        if ok and beat.device != self._current:
            log.info("Following %s (device %d)", beat.name, beat.device)
            self._current = beat.device
        return ok


# --------------------------------------------------------------------------
# Phase-locked MIDI clock
# --------------------------------------------------------------------------

class ClockPLL:
    """Generates 24 ppqn tick times that lock to externally observed beats.

    On each beat we pick the tick period that makes the next quarter-note
    boundary land exactly one beat after the observed beat, so drift and
    tempo changes are absorbed smoothly instead of skipping/bursting ticks.
    Large errors (first lock, track change, nudges) snap immediately.
    """

    MAX_SLEW = 0.25  # max deviation of a tick period from nominal

    def __init__(self, latency: float = 0.0):
        self.latency = latency
        self.bpm: Optional[float] = None
        self.period = 0.0
        self.next_tick = 0.0
        self.phase = 0          # ticks emitted since the last boundary
        self.beat_in_bar = 1    # bar position of the beat at the last boundary
        self._pending_bar: Optional[int] = None

    def on_beat(self, bpm: float, beat_in_bar: int, at: float) -> bool:
        """Feed an observed beat. Returns True if the clock hard-resynced."""
        at -= self.latency
        beat_len = 60.0 / bpm
        nominal = beat_len / TICKS_PER_BEAT
        self.bpm = bpm

        if self.period == 0.0:
            return self._snap(nominal, beat_in_bar, at)

        # Where should the next boundary be? The beat we just saw *is* a
        # boundary; decide whether it belongs to the one we last emitted
        # (we're slightly ahead) or the upcoming one (we're behind).
        if self.phase < TICKS_PER_BEAT // 2:
            intervals = TICKS_PER_BEAT - self.phase
            next_bar = beat_in_bar % 4 + 1
        else:
            intervals = 2 * TICKS_PER_BEAT - self.phase
            next_bar = beat_in_bar  # the boundary we're about to hit is this beat
        target = at + beat_len
        period = (target - self.next_tick) / intervals

        if not (nominal * (1 - self.MAX_SLEW) <= period <= nominal * (1 + self.MAX_SLEW)):
            return self._snap(nominal, beat_in_bar, at)
        self.period = period
        self._pending_bar = next_bar
        return False

    def _snap(self, nominal: float, beat_in_bar: int, at: float) -> bool:
        self.period = nominal
        self.next_tick = max(at, time.monotonic())
        self.phase = 0
        self.beat_in_bar = beat_in_bar
        self._pending_bar = None
        return True

    def tick(self) -> tuple[bool, int]:
        """Advance one tick. Returns (is_boundary, beat_in_bar_at_tick)."""
        boundary = self.phase == 0
        self.next_tick += self.period
        self.phase = (self.phase + 1) % TICKS_PER_BEAT
        if self.phase == 0:
            self.beat_in_bar = self._pending_bar or self.beat_in_bar % 4 + 1
            self._pending_bar = None
        return boundary, self.beat_in_bar


class MidiClockOutput:
    def __init__(self, port_name: str, use_existing: bool, transport: bool):
        import mido  # imported lazily so --monitor works without MIDI deps

        from phrases import MIDI_LOCK

        self._mido = mido
        self._lock = MIDI_LOCK
        if use_existing:
            self.port = mido.open_output(port_name)
        else:
            self.port = mido.open_output(port_name, virtual=True)
        self.transport = transport
        self.running = False
        self._closed = False
        log.info("MIDI clock on port %r", port_name)

    def _send(self, status: int) -> None:
        with self._lock:
            if not self._closed:
                self.port.send(self._mido.Message.from_bytes([status]))

    def clock(self) -> None:
        self._send(MIDI_CLOCK)

    def start(self) -> None:
        if self.transport and not self.running:
            self._send(MIDI_START)
        self.running = True

    def stop(self) -> None:
        if self.transport and self.running:
            self._send(MIDI_STOP)
        self.running = False

    def close(self) -> None:
        with self._lock:
            self.stop()
            self._closed = True
            self.port.close()


class LinkOutput:
    """Ableton Link via the LinkPython-extern package (`import link`)."""

    def __init__(self, quantum: float = 4.0):
        import link  # type: ignore

        self.quantum = quantum
        self.link = link.Link(120.0)
        self.link.enabled = True
        self.link.startStopSyncEnabled = True
        log.info("Ableton Link enabled (quantum %g)", quantum)

    def on_beat(self, bpm: float, beat_in_bar: int) -> None:
        clock = self.link.clock()
        now = clock.micros()
        state = self.link.captureSessionState()
        state.setTempo(bpm, now)
        # Put Link's bar phase on the deck's beat: beat_in_bar 1 == phase 0.
        beat = state.beatAtTime(now, self.quantum)
        bar_start = beat - (beat % self.quantum)
        target = bar_start + (beat_in_bar - 1)
        if target < beat - self.quantum / 2:
            target += self.quantum
        state.forceBeatAtTime(target, now, self.quantum)
        state.setIsPlaying(True, now)
        self.link.commitSessionState(state)

    def stop(self) -> None:
        state = self.link.captureSessionState()
        state.setIsPlaying(False, self.link.clock().micros())
        self.link.commitSessionState(state)

    def close(self) -> None:
        self.stop()
        self.link.enabled = False


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

class Engine:
    def __init__(self, selector: SourceSelector, midi: Optional[MidiClockOutput],
                 link: Optional[LinkOutput], latency: float,
                 stop_after: Optional[float], on_status: Callable[[str], None],
                 on_beat: Optional[Callable[[Beat], None]] = None):
        self.selector = selector
        self.on_beat = on_beat
        self.midi = midi
        self.link = link
        self.pll = ClockPLL(latency)
        self.stop_after = stop_after
        self.on_status = on_status
        self.lock = threading.Condition()
        self.last_beat = 0.0
        self.waiting_for_downbeat = True
        self.alive = True

    def feed(self, beat: Beat) -> None:
        if not self.selector.accept(beat):
            return
        with self.lock:
            self.last_beat = beat.received
            snapped = self.pll.on_beat(beat.bpm, beat.beat_in_bar, beat.received)
            if snapped:
                log.debug("clock resync at beat %d", beat.beat_in_bar)
            self.lock.notify()
        if self.link:
            self.link.on_beat(beat.bpm, beat.beat_in_bar)
        if self.on_beat:
            self.on_beat(beat)
        self.on_status(
            f"{beat.name:<10} dev {beat.device:<2} beat {beat.beat_in_bar}  "
            f"{beat.bpm:7.2f} BPM  (track {beat.track_bpm:.2f}, pitch {(beat.pitch - 1) * 100:+.2f}%)"
        )

    def run_clock(self) -> None:
        """Clock thread: sleeps until each tick and emits MIDI."""
        while self.alive:
            with self.lock:
                if self.pll.bpm is None:
                    self.lock.wait(0.25)
                    continue
                if self.stop_after and time.monotonic() - self.last_beat > self.stop_after:
                    if self.midi and self.midi.running:
                        log.info("No beats for %.1fs, stopping clock", self.stop_after)
                        self.midi.stop()
                    if self.link:
                        self.link.stop()
                    self.pll = ClockPLL(self.pll.latency)
                    self.waiting_for_downbeat = True
                    continue
                due = self.pll.next_tick
            delay = due - time.monotonic()
            if delay > 0.002:
                time.sleep(delay - 0.0015)
            while time.monotonic() < due:  # spin the last ~1.5ms for jitter
                pass
            with self.lock:
                if self.pll.next_tick != due:  # PLL snapped while we slept
                    continue
                boundary, bar_pos = self.pll.tick()
            if not self.midi:
                continue
            if self.waiting_for_downbeat:
                # Start on a downbeat so Lightkey's bar counter lines up.
                if boundary and bar_pos == 1:
                    self.midi.start()
                    self.waiting_for_downbeat = False
                else:
                    self.midi.clock()  # keep tempo flowing before Start
                    continue
            self.midi.clock()

    def close(self) -> None:
        self.alive = False
        if self.midi:
            self.midi.close()
        if self.link:
            self.link.close()


def open_beat_socket(bind: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        # Lets this coexist with PRO DJ LINK Bridge / rekordbox on one machine.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((bind, BEAT_PORT))
    return sock


def simulate(bpm: float, device: int, stop: threading.Event) -> None:
    """Broadcast fake beat packets on localhost for testing without decks."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    beat, nxt = 1, time.monotonic()
    while not stop.is_set():
        sock.sendto(build_beat_packet(device, bpm, beat), ("127.0.0.1", BEAT_PORT))
        beat = beat % 4 + 1
        nxt += 60.0 / bpm
        time.sleep(max(0.0, nxt - time.monotonic()))


def _open_project(args):
    """Pick the starting Lumière project. Returns (ProjectManager or None, follow Lightkey?)."""
    if args.no_pools:
        return None, False
    from pools import load_config
    from projects import NAME_TIP, Project, ProjectManager, ProjectStore

    store = ProjectStore(Path(args.projects_dir))
    legacy = Path(__file__).with_name("pools.json")
    if args.pools:  # a one-off settings file, not a saved project
        manager = ProjectManager(store, Project(Path(args.pools).stem, load_config(Path(args.pools))))
        manager.persist = False
        return manager, False
    if args.project:
        project = store.load(args.project)
        if project is None:
            project = store.create(args.project, base=store.ensure_default(legacy))
            log.info("Created Lumière project '%s' in %s. %s", project.name, store.dir, NAME_TIP)
        return ProjectManager(store, project), False
    return ProjectManager(store, store.ensure_default(legacy)), not args.no_follow_lightkey


def _learn(args, manager, follow: bool) -> int:
    """Interactive MIDI Learn helper: sends each pool sequence's note on request."""
    import pools as pl
    from phrases import CueMidi

    if manager and follow:
        from lightkey import read_open_project
        manager.on_lightkey(read_open_project())  # learn into the project Lightkey has open
    if manager is None or not manager.pools.pools:
        log.error("No sequence pools are switched on in this project")
        return 1
    try:
        out = CueMidi(args.cue_port, args.existing_port)
    except Exception as e:  # noqa: BLE001 - surface any backend error plainly
        log.error("Could not open MIDI port %r: %s", args.cue_port, e)
        return 1
    print(f"\nMIDI Learn helper for Lumière project '{manager.project.name}' ({manager.project.summary()}).")
    print(f"In Lightkey, make sure the input {args.cue_port!r} is enabled.")
    print("For each sequence: put it into MIDI Learn in Lightkey, then press Enter here.")
    try:
        pl.learn(out.note, manager.pools, None if args.learn == "all" else args.learn,
                 mark=manager.mark_learned)
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        time.sleep(0.2)  # let the last note-off go out
        out.close()
    return 0


def _with_loopback(list_ifaces):
    """The simulated booth lives on 127.0.0.1; make sure that interface is listed."""
    import netinfo

    def wrapped():
        ifaces = list_ifaces()
        if not any(a.ip == "127.0.0.1" for i in ifaces for a in i.addrs):
            ifaces.append(netinfo.Iface("lo0", up=True,
                                        addrs=[netinfo.Addr("127.0.0.1", "255.0.0.0", "127.255.255.255")]))
        return ifaces
    return wrapped


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="auto",
                    help="auto (default: mixer, else a playing deck), mixer, or player number 1-6")
    ap.add_argument("--midi-port", default="Lumiere Clock",
                    help="MIDI output port name (default: %(default)s)")
    ap.add_argument("--existing-port", action="store_true",
                    help="send to an existing port (e.g. 'IAC Driver Bus 1') instead of creating a virtual one")
    ap.add_argument("--no-midi", action="store_true", help="disable MIDI clock output")
    ap.add_argument("--no-transport", action="store_true",
                    help="never send MIDI Start/Stop, only clock ticks")
    ap.add_argument("--link", action="store_true",
                    help="also publish tempo/phase on Ableton Link (pip install LinkPython-extern)")
    ap.add_argument("--latency-ms", type=float, default=0.0,
                    help="shift the clock earlier by this many ms to compensate for light/DMX latency")
    ap.add_argument("--stop-after", type=float, default=0.0,
                    help="send MIDI Stop after N seconds with no beats (default 0 = keep last tempo running)")
    ap.add_argument("--bind", default="0.0.0.0", help="interface address to listen on")
    ap.add_argument("--monitor", action="store_true",
                    help="just print every beat packet from every device, no output")
    ap.add_argument("--simulate", type=float, metavar="BPM",
                    help="generate fake beat packets at BPM locally (testing without gear)")
    pool = ap.add_argument_group("sequence pools (each drop, build, ... picks one of up to 24 sequences)")
    pool.add_argument("--project", metavar="NAME",
                      help="use this Lumière project (created if new) instead of following Lightkey")
    pool.add_argument("--projects", action="store_true", help="list your Lumière projects and exit")
    pool.add_argument("--projects-dir", default=str(Path.home() / "Documents" / "Lumiere Link"),
                      help="where projects are kept (default: %(default)s)")
    pool.add_argument("--no-follow-lightkey", action="store_true",
                      help="don't switch projects to match the project open in Lightkey")
    pool.add_argument("--lightkey-check", action="store_true",
                      help="show which project Lightkey has open (checks the macOS permission), then exit")
    pool.add_argument("--pools", metavar="FILE", help="use a stand-alone pool settings file instead of a project")
    pool.add_argument("--no-pools", action="store_true", help="send only the single cue note per section")
    pool.add_argument("--learn", nargs="?", const="all", metavar="POOL",
                      help="step through every sequence so Lightkey can MIDI-learn it, then exit "
                           "(optionally just one pool, e.g. --learn drop)")
    net = ap.add_argument_group("phrase cues over the network (rekordbox phrase analysis, no audio cable)")
    net.add_argument("--phrases", action="store_true",
                     help="join the DJ Link network and cue lights from each track's rekordbox phrases")
    net.add_argument("--follow", default="master",
                     help="deck to follow: master (default), on-air, or a player number")
    net.add_argument("--player-number", type=int, default=0,
                     help="device number to claim (default: first free from 7 up)")
    net.add_argument("--pose-as", type=int, default=0,
                     help="player number to present to the CDJ database (default: automatic)")
    net.add_argument("--broadcast", help="DJ Link broadcast address (default: detected)")
    net.add_argument("--no-fix-network", action="store_true",
                     help="don't add a matching network address when a player is on another subnet")
    net.add_argument("--simulate-booth", action="store_true",
                     help="run a pretend booth (2 x CDJ-3000X + DJM-V10) on this Mac and connect to it")
    net.add_argument("--simulate-dropout", type=float, metavar="SECONDS",
                     help="with --simulate-booth: pull the pretend network cable for 10 s after this many seconds")
    net.add_argument("--demo", action="store_true",
                     help="play a fake 128 BPM track through the phrase cues (MIDI-learn without decks)")
    cues = ap.add_argument_group("section cues from the mixer's audio over USB (backup / fallback)")
    cues.add_argument("--cues", action="store_true",
                      help="listen to the mixer audio and send section cues + levels as MIDI")
    cues.add_argument("--cue-port", default="Lumiere Cues",
                      help="MIDI port for cues (default: %(default)s)")
    cues.add_argument("--audio-device", help="audio input name or number (default: a DJM if attached)")
    cues.add_argument("--audio-channels", default="1,2",
                      help="input channels carrying the master, 1-based (default: %(default)s)")
    cues.add_argument("--list-audio", action="store_true", help="list audio inputs and exit")
    cues.add_argument("--analyze", metavar="WAV",
                      help="print the sections detected in a recorded WAV file and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if sys.stderr.isatty():  # clear the live status line before each log message
        for handler in logging.getLogger().handlers:
            handler.terminator = "\n"
            fmt = handler.formatter
            handler.setFormatter(logging.Formatter("\r\033[K" + fmt._fmt, fmt.datefmt))

    if args.lightkey_check:
        from lightkey import probe
        return probe()
    manager, follow = _open_project(args)
    if args.projects:
        names = manager.store.names() if manager else []
        print("\n".join(names) if names else "No projects yet.")
        return 0
    seq_pools = manager.pools if manager else None
    if args.learn:
        return _learn(args, manager, follow)

    if args.list_audio:
        from sections import list_audio_devices
        list_audio_devices()
        return 0
    if args.analyze:
        from sections import analyze_file
        for t, event in analyze_file(args.analyze):
            print(f"{int(t // 60):3d}:{t % 60:05.2f}  {event.upper()}")
        return 0

    try:
        sock = open_beat_socket(args.bind)
    except OSError as e:
        log.error("Cannot listen on UDP %d: %s. Another app holds the port without "
                  "sharing it; quit it (this tool does not need it) and retry.", BEAT_PORT, e)
        return 1

    stop = threading.Event()
    if args.simulate:
        threading.Thread(target=simulate, args=(args.simulate, 1, stop), daemon=True).start()
        log.info("Simulating a deck at %.2f BPM", args.simulate)

    if args.monitor:
        log.info("Monitoring beat packets on UDP %d (Ctrl-C to quit)", BEAT_PORT)
        try:
            while True:
                data, addr = sock.recvfrom(2048)
                beat = parse_beat(data, time.monotonic())
                if beat:
                    kind = "mixer " if beat.is_mixer else "player"
                    print(f"{addr[0]:<15} {kind} {beat.device:<2} {beat.name:<12} "
                          f"beat {beat.beat_in_bar}  {beat.bpm:7.2f} BPM", flush=True)
        except KeyboardInterrupt:
            return 0

    midi = None
    if not args.no_midi:
        try:
            midi = MidiClockOutput(args.midi_port, args.existing_port, not args.no_transport)
        except Exception as e:  # noqa: BLE001 - surface any backend error plainly
            log.error("Could not open MIDI port %r: %s", args.midi_port, e)
            return 1
    link = None
    if args.link:
        try:
            link = LinkOutput()
        except ImportError:
            log.error("--link needs the LinkPython-extern package: pip install LinkPython-extern")
            return 1
    if not midi and not link:
        log.error("Nothing to output: enable MIDI and/or --link")
        return 1

    audio = cue_out = tracker = vcdj = None
    rig = None
    if args.simulate_booth:
        from simrig import SimRig
        args.phrases = True
        args.broadcast = args.broadcast or "127.0.0.1"
        try:
            rig = SimRig(dropout_after=args.simulate_dropout)
        except OSError as e:
            log.error("Could not start the simulated booth: %s", e)
            return 1
        rig.start()
    if args.phrases or args.demo:
        try:
            from phrases import CueMidi, PhraseTracker, VirtualCdj, run_demo
            gate = (lambda: tracker.has_phrases()) if args.cues else None
            cue_out = CueMidi(args.cue_port, args.existing_port, gate=gate, pools=seq_pools)
            tracker = PhraseTracker(cue_out.event, cue_out.level, follow=args.follow,
                                    lead=args.latency_ms / 1000.0)
            if args.demo:
                threading.Thread(target=run_demo, args=(tracker, 128.0, stop), daemon=True).start()
                log.info("Demo: fake track Intro > Up > Chorus > Down > Up > Chorus > Outro at 128 BPM")
            else:
                vcdj = VirtualCdj(tracker, number=args.player_number, posing_as=args.pose_as,
                                  bind=args.bind, broadcast=args.broadcast,
                                  fix_network=not args.no_fix_network)
                if rig:
                    vcdj.list_ifaces = _with_loopback(vcdj.list_ifaces)
                vcdj.start()
            if args.cues:
                from sections import AudioInput, SectionDetector
                channels = [int(c) for c in args.audio_channels.split(",")]
                audio = AudioInput(args.audio_device, channels,
                                   lambda sr: SectionDetector(sr, cue_out.audio_event, cue_out.audio_levels))
                audio.start()
                log.info("USB audio detection is the fallback for tracks without phrase data")
        except OSError as e:
            log.error("Could not start phrase cues: %s (is another DJ Link app using ports 50000/50002?)", e)
            return 1
    elif args.cues:
        try:
            from phrases import CueMidi
            from sections import AudioInput, SectionDetector
            cue_out = CueMidi(args.cue_port, args.existing_port, pools=seq_pools)
            channels = [int(c) for c in args.audio_channels.split(",")]
            audio = AudioInput(args.audio_device, channels,
                               lambda sr: SectionDetector(sr, cue_out.audio_event, cue_out.audio_levels))
            audio.start()
        except ImportError as e:
            log.error("--cues needs numpy and sounddevice (pip install -r requirements.txt): %s", e)
            return 1
        except Exception as e:  # noqa: BLE001 - audio backends raise assorted errors
            log.error("Could not start section cues: %s", e)
            return 1

    last_status = [""]

    def status(line: str) -> None:
        if vcdj and vcdj.problem:
            line = "[network problem: see message above]  " + line
        elif tracker:
            pick = getattr(cue_out, "last_pick", "")
            proj = f"{manager.project.name} · " if manager else ""
            line = f"[{proj}{tracker.current()}{' · ' + pick if pick else ''}]  " + line
        elif audio:
            line += f"  [{(audio.detector.state or 'listening').upper()}]"
        if sys.stdout.isatty():
            # Keep it on one row: a line wider than the window wraps and \r can't rewind it.
            width = shutil.get_terminal_size((100, 20)).columns - 1
            if len(line) > width:
                line = line[:width - 1] + "…"
            print("\r\033[K" + line, end="", flush=True)
            last_status[0] = line

    engine = Engine(SourceSelector(args.source), midi, link,
                    latency=args.latency_ms / 1000.0,
                    stop_after=args.stop_after or None, on_status=status,
                    on_beat=(lambda b: audio.detector.on_beat(b.beat_in_bar, b.received)) if audio else None)
    clock_thread = threading.Thread(target=engine.run_clock, daemon=True)
    clock_thread.start()

    watcher = None
    if manager:
        if cue_out is not None and hasattr(cue_out, "set_pools"):
            manager.on_switch = lambda project, pools: cue_out.set_pools(pools)
        if follow:
            from lightkey import LightkeyWatcher
            watcher = LightkeyWatcher(manager.on_lightkey)
            watcher.start()
        else:
            log.info("Lumière project: '%s' (%s)", manager.project.name, manager.project.summary())

    log.info("Waiting for beats on UDP %d (source: %s). Press play on a deck.", BEAT_PORT, args.source)
    try:
        while True:
            data, _ = sock.recvfrom(2048)
            beat = parse_beat(data, time.monotonic())
            if beat:
                if tracker and not args.demo and not beat.is_mixer:
                    tracker.on_beat(beat.device, beat.beat_in_bar, beat.bpm, beat.received)
                engine.feed(beat)
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        engine.close()
        if audio:
            audio.close()
        if vcdj:
            vcdj.close()
        if rig:
            rig.stop()
        if watcher:
            watcher.stop()
        if cue_out:
            cue_out.close()
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
