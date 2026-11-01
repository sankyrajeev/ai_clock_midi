"""The Lumière Link engine as one object, for the app window.

It runs the same parts as the command-line tool (MIDI clock, network phrase
cues, sequence pools, projects that follow Lightkey, optional live audio)
in background threads, keeps a snapshot of everything the screen shows, and
takes the screen's actions: live / paused, light delay, which deck to follow,
pads, sequence pools, projects, demo mode.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

import pools as pl
import prolink_sync as ps
import waveform as wf
from phrases import (AGAIN, BLACKOUT, BLACKOUT_CHANNEL, BLACKOUT_NOTE, BUILD, CC_BUILD_INTENSITY, CC_BUILD_SPEED,
                     DROP, FADER_REST, FILL_HIT, FILL_HIT_NOTE, FILL_ROLL, FILL_ROLL_NOTE, CueMidi, PhraseTracker,
                     VirtualCdj)

BUILD_FADERS = {"build_speed": CC_BUILD_SPEED, "build_intensity": CC_BUILD_INTENSITY}
# Where each build fader may go, in MIDI values 0-127: no roll / a quiet riser sits at
# "low", the fastest roll / loudest riser reaches "high", and once the build is over
# (the drop) it goes to "drop". Never 0 by default: Lightkey's slowest speed freezes the lights.
FADER_RANGE = {"low": 64, "high": 127, "drop": 64}
from projects import NAME_TIP, ProjectManager, ProjectStore

log = logging.getLogger("prolink_sync.app")

CLOCK_PORT, CUE_PORT = "Lumiere Clock", "Lumiere Cues"
try:
    VERSION = Path(__file__).with_name("VERSION").read_text().strip()
except OSError:
    VERSION = "dev"
SOURCE_OF = {"rekordbox": "rekordbox", "waveform": "waveform"}


def app_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Lumiere Link"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lumiere-link"


@dataclass
class Settings:
    latency_ms: int = 0
    follow: str = "master"          # master | on-air | a player number as text
    clock: bool = True
    phrases: bool = True
    live_audio: bool = False
    audio_device: str = ""
    link: bool = False
    follow_lightkey: bool = True
    demo: bool = False
    guide_seen: bool = False

    @classmethod
    def load(cls, path: Path) -> "Settings":
        try:
            data = json.loads(path.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        os.replace(tmp, path)


@dataclass
class LogEntry:
    id: int
    t: float
    cue: str
    where: str
    seq: int = 0
    count: int = 0


class LumiereEngine:
    def __init__(self, data_dir: Optional[Path] = None, projects_dir: Optional[Path] = None,
                 demo: Optional[bool] = None, beat_bind: str = "0.0.0.0"):
        self.data_dir = Path(data_dir) if data_dir else app_data_dir()
        self.settings_path = self.data_dir / "settings.json"
        self.settings = Settings.load(self.settings_path)
        if demo is not None:
            self.settings.demo = demo
        self.beat_bind = beat_bind
        self.lock = threading.RLock()
        self.live = True
        self.store = ProjectStore(Path(projects_dir) if projects_dir else ProjectStore().dir)
        self.manager = ProjectManager(self.store, self.store.ensure_default(Path(__file__).with_name("pools.json")))
        self.log: deque[LogEntry] = deque(maxlen=60)
        self.flashes: deque[tuple[int, str]] = deque(maxlen=40)
        self._next_id = 0
        self.beats: dict[int, ps.Beat] = {}
        self.errors: dict[str, str] = {}
        self.clock_out = self.cue_out = None
        self.clock: Optional[ps.Engine] = None
        self.tracker: Optional[PhraseTracker] = None
        self.vcdj: Optional[VirtualCdj] = None
        self.rig = None
        self.sock: Optional[socket.socket] = None
        self.audio = None
        self.watcher = None
        self.link = None
        self._net_alive = False
        self.started = False

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        try:
            self.clock_out = ps.MidiClockOutput(CLOCK_PORT, False, True)
            self.cue_out = CueMidi(CUE_PORT, False, gate=self._has_phrases, pools=self.manager.pools)
        except Exception as e:  # noqa: BLE001 - CoreMIDI errors come in many shapes
            self.errors["midi"] = f"Couldn't create the MIDI inputs for Lightkey ({e})."
            log.error(self.errors["midi"])
        self.manager.on_switch = self._on_project_switch
        selector = ps.SourceSelector("auto")
        selector.prefer = lambda: self.tracker.followed if self.tracker else None
        self.clock = ps.Engine(selector, self._clock_midi(), None,
                               latency=self.settings.latency_ms / 1000.0, stop_after=None,
                               on_status=lambda line: None)
        threading.Thread(target=self.clock.run_clock, daemon=True).start()
        if self.settings.link:
            self._set_link(True)
        self._start_network()
        if self.settings.follow_lightkey:
            self._start_watcher()
        if self.settings.live_audio:
            self._start_audio()
        self.started = True

    def stop(self) -> None:
        self._stop_network()
        if self.watcher:
            self.watcher.stop()
        if self.audio:
            self.audio.close()
        if self.clock:
            self.clock.close()
        if self.cue_out:
            self.cue_out.close()

    def _clock_midi(self):
        return self.clock_out if (self.settings.clock and self.live) else None

    def _start_network(self) -> None:
        self.tracker = PhraseTracker(self._emit, self._level, follow=self.settings.follow,
                                     lead=self.settings.latency_ms / 1000.0)
        self.tracker.change_every = self._change_every
        self.tracker.blackout_beats = self._blackout_beats
        self.tracker.drop_fill = self._drop_fill
        self.tracker.build_curve = self._build_curve
        self._curves: dict = {}
        self._gaps: dict = {}
        self._fills: dict = {}
        self._fill_logged = -1e9
        broadcast = None
        if self.settings.demo:
            from simrig import SimRig
            try:
                self.rig = SimRig()
                self.rig.start()
                broadcast = "127.0.0.1"
            except OSError as e:
                self.errors["demo"] = f"Couldn't start demo mode ({e})."
        try:
            self.sock = ps.open_beat_socket(self.beat_bind)
            self.sock.settimeout(0.5)
            self.errors.pop("network", None)
        except OSError as e:
            self.errors["network"] = ("Another DJ Link app on this Mac is using the network port. Quit "
                                      f"PRO DJ LINK Bridge, ShowKontrol or rekordbox, then restart. ({e})")
            log.error(self.errors["network"])
            return
        try:
            self.vcdj = VirtualCdj(self.tracker, bind=self.beat_bind, broadcast=broadcast)
            if self.rig:
                self.vcdj.list_ifaces = ps._with_loopback(self.vcdj.list_ifaces)
            self.vcdj.start()
        except OSError as e:
            self.errors["network"] = f"Couldn't join the DJ Link network ({e})."
        self._net_alive = True
        threading.Thread(target=self._beat_loop, args=(self.sock,), daemon=True).start()

    def _stop_network(self) -> None:
        self._net_alive = False
        if self.vcdj:
            self.vcdj.close()
            self.vcdj = None
        if self.sock:
            self.sock.close()
            self.sock = None
        if self.rig:
            self.rig.stop()
            self.rig = None
        self.beats.clear()

    def _beat_loop(self, sock: socket.socket) -> None:
        while self._net_alive and self.sock is sock:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            beat = ps.parse_beat(data, time.monotonic())
            if not beat:
                continue
            self.beats[beat.device] = beat
            if self.tracker and not beat.is_mixer:
                self.tracker.on_beat(beat.device, beat.beat_in_bar, beat.bpm, beat.received)
            if self.clock:
                self.clock.feed(beat)

    def _start_watcher(self) -> None:
        from lightkey import LightkeyWatcher
        self.watcher = LightkeyWatcher(self.manager.on_lightkey)
        self.watcher.start()

    def _start_audio(self) -> None:
        try:
            from sections import AudioInput, SectionDetector
            self.audio = AudioInput(self.settings.audio_device or None, [1, 2],
                                    lambda sr: SectionDetector(sr, self._audio_event, self._audio_levels))
            self.audio.start()
            self.errors.pop("audio", None)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - audio backends raise assorted errors
            self.audio = None
            self.errors["audio"] = f"Live audio backup isn't running: {e}"

    def _set_link(self, on: bool) -> None:
        if on and self.link is None:
            try:
                self.link = ps.LinkOutput()
                self.clock.link = self.link
                self.errors.pop("link", None)
            except ImportError:
                self.errors["link"] = "Ableton Link isn't installed in this build."
        elif not on and self.link is not None:
            self.link.close()
            self.clock.link = self.link = None

    # ------------------------------------------------------------------ cues

    def _has_phrases(self) -> bool:
        return bool(self.tracker and self.tracker.has_phrases())

    def _where(self) -> str:
        t = self.tracker
        deck = t.decks.get(t.followed) if t and t.followed else None
        if not deck or deck.beat is None:
            return ""
        return f"CDJ {t.followed} · bar {(deck.beat - 1) // 4 + 1}"

    def _record(self, cue: str, where: str, pick=None) -> None:
        with self.lock:
            self._next_id += 1
            self.flashes.append((self._next_id, cue))
            if cue != "bar":
                seq, count = pick or (0, 0)
                self.log.appendleft(LogEntry(self._next_id, time.time(), cue, where, seq, count))

    def _emit(self, cue: str) -> None:
        if not (self.live and self.settings.phrases and self.cue_out):
            return
        if cue == BLACKOUT:
            self.cue_out.blackout()
            self._record(cue, self._where() + " · before the drop")
        elif cue in (FILL_HIT, FILL_ROLL):
            self.cue_out.fill(cue)
            # One log line per fill, not one per hit (a roll can be 16+ hits).
            if cue == FILL_ROLL or time.monotonic() - self._fill_logged > 4.0:
                self._record(cue, self._where() + " · before the drop")
            else:
                with self.lock:
                    self._next_id += 1
                    self.flashes.append((self._next_id, cue))
            self._fill_logged = time.monotonic()
        elif cue.startswith(AGAIN):
            name = cue[len(AGAIN):]
            pick = self.cue_out.event(name, section_note=False)
            if pick:
                self._record(name, self._where() + " · next look", pick)
        else:
            pick = self.cue_out.event(cue)
            self._record(cue, self._where(), pick)

    # -- settings the phrase tracker asks for, beat by beat

    def _change_every(self, cue: str) -> int:
        cfg = self.manager.project.config
        if cue not in self.manager.pools.pools:  # no sequences to change to
            return 0
        return int((cfg.get("change_every") or {}).get(cue, 0) or 0)

    def _blackout_beats(self, key: tuple, drop_beat: int) -> float:
        bo = self.manager.project.config.get("blackout") or {}
        mode = bo.get("mode", "off")
        if mode == "fixed":
            return float(bo.get("beats", 1.0))
        if mode != "music" or not self.tracker:
            return 0.0
        if (key, drop_beat) not in self._gaps:
            bands = self.tracker.waveforms.get(key)
            if bands is None:
                return 0.0  # no waveform yet: ask again next beat
            grid = self.tracker.grids.get(key, [])
            bpm = grid[0].bpm if grid else 120.0
            self._gaps[(key, drop_beat)] = wf.gap_before(bands, grid, drop_beat, bpm)
            log.info("Gap before the drop at beat %d: %.1f beats", drop_beat, self._gaps[(key, drop_beat)])
        return self._gaps[(key, drop_beat)]

    def _drop_fill(self, key: tuple, drop_beat: int):
        if not self.manager.project.config.get("fills", True) or not self.tracker:
            return None
        if (key, drop_beat) not in self._fills:
            bands = self.tracker.waveforms.get(key)
            if bands is None:
                return None  # no waveform yet: ask again next beat
            grid = self.tracker.grids.get(key, [])
            bpm = grid[0].bpm if grid else 120.0
            fill = wf.drop_fill(bands, grid, drop_beat, bpm)
            self._fills[(key, drop_beat)] = fill
            log.info("Before the drop at beat %d: %s (bars: %s)", drop_beat, fill.describe(), ", ".join(fill.bars))
        return self._fills[(key, drop_beat)]

    def _build_curve(self, key: tuple, start: int, end: int):
        if not self.manager.project.config.get("build_fx", True) or not self.tracker:
            return None
        if (key, start, end) not in self._curves:
            bands = self.tracker.waveforms.get(key)
            if bands is None:
                return None  # no waveform yet: ask again next beat
            grid = self.tracker.grids.get(key, [])
            bpm = grid[0].bpm if grid else 120.0
            curve = wf.build_curve(bands, grid, start, end, bpm)
            self._curves[(key, start, end)] = curve
            if curve:
                log.info("Build-up at beat %d: %s", start, curve.describe())
        return self._curves[(key, start, end)]

    def _build_now(self) -> str:
        """The build-up playing now, or the next one, on the deck the lights follow."""
        t = self.tracker
        if not t:
            return ""
        with t.lock:
            deck = t.decks.get(t.followed) if t.followed else None
            tp = t._followed_phrases()
            if not deck or not tp or deck.beat is None:
                return ""
            key, ph = deck.status.track_key, tp.phrases
            spans = [(p.beat, ph[i + 1].beat if i + 1 < len(ph) else tp.end_beat) for i, p in enumerate(ph)
                     if p.cue == BUILD or (i + 1 < len(ph) and ph[i + 1].cue == DROP and p.cue != DROP)]
            span = next(((a, b) for a, b in spans if b > deck.beat), None)
            beat = deck.beat
        if span is None or key not in t.waveforms:
            return ""
        curve = self._build_curve(key, *span)
        if not curve:
            return ""
        if span[0] <= beat:
            return f"Now: {curve.describe()} · speed {round(curve.speed(beat) * 100)}%"
        return f"Next build (bar {(span[0] - 1) // 4 + 1}): {curve.describe()}"

    def _next_fill(self) -> str:
        """What's before the next drop on the deck the lights follow, for the screen."""
        t = self.tracker
        if not t:
            return ""
        with t.lock:
            deck = t.decks.get(t.followed) if t.followed else None
            tp = t._followed_phrases()
            if not deck or not tp or deck.beat is None:
                return ""
            key = deck.status.track_key
            drop = next((p.beat for i, p in enumerate(tp.phrases)
                         if p.cue == "drop" and p.beat > deck.beat and i and tp.phrases[i - 1].cue != "drop"), None)
        if drop is None or key not in t.waveforms:
            return ""
        fill = self._drop_fill(key, drop)
        return f"Next drop (bar {(drop - 1) // 4 + 1}): {fill.describe()}" if fill else ""

    def fader_range(self, fader: str) -> dict:
        return {**FADER_RANGE, **((self.manager.project.config.get("build_ranges") or {}).get(fader) or {})}

    def _level(self, cc: int, value: float) -> None:
        if not (self.live and self.settings.phrases and self.cue_out):
            return
        fader = next((name for name, c in BUILD_FADERS.items() if c == cc), None)
        if fader:
            r = self.fader_range(fader)
            value = (r["drop"] if value == FADER_REST else r["low"] + value * (r["high"] - r["low"])) / 127
        self.cue_out.level(cc, value)

    def _audio_event(self, cue: str, _t: float = 0.0) -> None:
        if not self._has_phrases():
            self._emit(cue)

    def _audio_levels(self, levels: dict) -> None:
        if self.live and self.cue_out:
            self.cue_out.audio_levels(levels)

    def _on_project_switch(self, project, pools) -> None:
        if self.cue_out:
            self.cue_out.set_pools(pools)
        self._record("project", project.name)

    # ------------------------------------------------------------------ actions (from the screen)

    def action(self, action: str, args: Optional[dict] = None):
        fn = getattr(self, "do_" + action, None)
        if fn is None:
            raise ValueError(f"unknown action {action!r}")
        result = fn(**(args or {}))
        self.settings.save(self.settings_path)
        return result

    def do_set_live(self, on: bool) -> None:
        self.live = bool(on)
        if self.clock:
            if not self.live and self.clock_out and self.clock_out.running:
                self.clock_out.stop()
            self.clock.midi = self._clock_midi()
            self.clock.waiting_for_downbeat = True

    def do_set_latency(self, ms: int) -> None:
        ms = max(0, min(120, int(ms)))
        self.settings.latency_ms = ms
        if self.clock:
            self.clock.pll.latency = ms / 1000.0
        if self.tracker:
            self.tracker.lead = ms / 1000.0

    def do_set_follow(self, mode: str) -> None:
        if mode not in ("master", "on-air") and not str(mode).isdigit():
            raise ValueError("follow must be master, on-air or a player number")
        self.settings.follow = str(mode)
        if self.tracker:
            with self.tracker.lock:
                self.tracker.follow = str(mode)

    def do_set_switch(self, name: str, on: bool) -> None:
        on = bool(on)
        if name == "clock":
            self.settings.clock = on
            if self.clock:
                if not on and self.clock_out and self.clock_out.running:
                    self.clock_out.stop()
                self.clock.midi = self._clock_midi()
        elif name == "phrases":
            self.settings.phrases = on
        elif name == "live_audio":
            self.settings.live_audio = on
            if on and not self.audio:
                self._start_audio()
            elif not on and self.audio:
                self.audio.close()
                self.audio = None
                self.errors.pop("audio", None)
        elif name == "link":
            self.settings.link = on
            self._set_link(on)
        elif name == "follow_lightkey":
            self.settings.follow_lightkey = on
            if on and not self.watcher:
                self._start_watcher()
            elif not on and self.watcher:
                self.watcher.stop()
                self.watcher = None
        elif name == "demo":
            if on != self.settings.demo:
                self.settings.demo = on
                self._stop_network()
                self._start_network()
        elif name == "guide_seen":
            self.settings.guide_seen = on
        else:
            raise ValueError(f"unknown switch {name!r}")

    def do_set_audio_device(self, device: str) -> None:
        self.settings.audio_device = device
        if self.audio:
            self.audio.close()
            self.audio = None
            self._start_audio()

    def do_fire(self, cue: str) -> None:
        """A pad pressed by hand: sends even while paused."""
        if cue == BLACKOUT:
            return self.do_send_blackout()
        if cue in (FILL_HIT, FILL_ROLL):
            return self.do_send_fill(cue)
        if self.cue_out:
            pick = self.cue_out.event(cue)
            self._record(cue, "by hand", pick)

    def do_send_sequence(self, pool: str, seq: int, learn: bool = False) -> None:
        p = self.manager.pools.pools.get(pool)
        if p is None or not 1 <= int(seq) <= p.count:
            raise ValueError("no such sequence")
        if self.cue_out:
            self.cue_out.note(p.channel, pl.note_for(int(seq)))
        p.last = int(seq)
        if learn:
            self.manager.mark_learned(pool, int(seq))
        self._record(pool, "for MIDI Learn" if learn else "by hand", (int(seq), p.count))

    def do_send_blackout(self, learn: bool = False) -> None:
        if self.cue_out:
            self.cue_out.blackout()
        if learn:
            self.manager.mark_learned(BLACKOUT, 1)
        self._record(BLACKOUT, "for MIDI Learn" if learn else "by hand")

    def do_send_fill(self, cue: str, learn: bool = False) -> None:
        if cue not in (FILL_HIT, FILL_ROLL):
            raise ValueError("no such cue")
        if self.cue_out:
            self.cue_out.fill(cue)
        if learn:
            self.manager.mark_learned(cue, 1)
        self._record(cue, "for MIDI Learn" if learn else "by hand")

    def do_send_build_fader(self, fader: str, learn: bool = False) -> None:
        """Sweep a build fader up and back down, so Lightkey's MIDI Learn picks it up."""
        cc = BUILD_FADERS.get(fader)
        if cc is None:
            raise ValueError("no such fader")
        if self.cue_out:
            out, r = self.cue_out, self.fader_range(fader)

            def sweep():  # within the fader's range, ending where it rests
                for v in (r["low"], (r["low"] + r["high"]) / 2, r["high"], r["drop"]):
                    out.level(cc, v / 127)
                    time.sleep(0.05)
            threading.Thread(target=sweep, daemon=True).start()
        if learn:
            self.manager.mark_learned(fader, 1)
        self._record(fader, "for MIDI Learn" if learn else "by hand")

    def do_set_build_range(self, fader: str, which: str, value: int) -> None:
        """Set a build fader's lowest, highest or at-the-drop value (0-127), and send it
        straight away so Lightkey shows where that lands (e.g. its beat multiplier)."""
        if fader not in BUILD_FADERS or which not in FADER_RANGE:
            raise ValueError("no such fader setting")
        v = max(0, min(127, int(value)))

        def change(cfg):
            r = cfg.setdefault("build_ranges", {}).setdefault(fader, dict(FADER_RANGE))
            r[which] = v
            if which == "low" and r.get("high", 127) < v:
                r["high"] = v
            if which == "high" and r.get("low", 0) > v:
                r["low"] = v
        self._update_config(change)
        if self.cue_out:
            self.cue_out.level(BUILD_FADERS[fader], v / 127)

    def do_set_build_fx(self, on: bool) -> None:
        self._update_config(lambda cfg: cfg.__setitem__("build_fx", bool(on)))

    def do_set_fills(self, on: bool) -> None:
        self._update_config(lambda cfg: cfg.__setitem__("fills", bool(on)))

    def do_set_change_every(self, pool: str, bars: int) -> None:
        if pool not in pl.POOL_CHANNELS or int(bars) not in pl.CHANGE_EVERY:
            raise ValueError("choose 0, 4, 8, 16 or 32 bars")
        self._update_config(lambda cfg: cfg.setdefault("change_every", {}).__setitem__(pool, int(bars)))

    def do_set_blackout(self, mode: Optional[str] = None, beats: Optional[float] = None) -> None:
        if mode is not None and mode not in pl.BLACKOUT_MODES:
            raise ValueError("unknown blackout mode")
        if beats is not None and float(beats) not in pl.BLACKOUT_BEATS:
            raise ValueError("unknown blackout length")

        def change(cfg):
            bo = cfg.setdefault("blackout", {"mode": "off", "beats": 1.0})
            if mode is not None:
                bo["mode"] = mode
            if beats is not None:
                bo["beats"] = float(beats)
        self._update_config(change)

    def do_unmap(self, pool: str, seq: int) -> None:
        """Clear a sequence's MAPPED mark (Lightkey's own mapping is cleared in Lightkey)."""
        if pool in (BLACKOUT, FILL_HIT, FILL_ROLL, *BUILD_FADERS):
            self.manager.unmark_learned(pool, 1)
            self._record(pool, "unmapped")
            return
        if pool not in pl.POOL_CHANNELS:
            raise ValueError("no such pool")
        self.manager.unmark_learned(pool, int(seq))
        self._record(pool, "unmapped", (int(seq), self.manager.pools.pools[pool].count
                                        if pool in self.manager.pools.pools else 0))

    def _update_config(self, change) -> None:
        with self.manager.lock:
            change(self.manager.project.config)
            self.manager.pools = pl.SequencePools(self.manager.project.config)
            if self.manager.persist:
                self.store.save(self.manager.project)
        if self.cue_out:
            self.cue_out.set_pools(self.manager.pools)

    def do_set_pool_count(self, pool: str, count: int) -> None:
        if pool not in pl.POOL_CHANNELS:
            raise ValueError("unknown pool")
        count = max(0, min(pl.MAX_SEQUENCES, int(count)))

        def change(cfg):
            pools = cfg.setdefault("pools", {})
            if isinstance(pools.get(pool), dict):
                pools[pool]["count"] = count
            else:
                pools[pool] = count
        self._update_config(change)

    def do_set_pick_mode(self, mode: str) -> None:
        if mode not in pl.MODES:
            raise ValueError("unknown mode")
        self._update_config(lambda cfg: cfg.__setitem__("mode", mode))

    def do_set_pairing(self, on: bool) -> None:
        self._update_config(lambda cfg: cfg.__setitem__("pair_builds_with_drops", bool(on)))

    def do_use_project(self, name: str) -> None:
        project = self.store.load(name)
        if project is None:
            raise ValueError(f"no project called {name!r}")
        self.manager.switch(project, "Chosen")

    def do_new_project(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("give the project a name")
        if self.store.load(name):
            raise ValueError(f"there's already a project called {name!r}")
        project = self.store.create(name, base=self.manager.project)
        self.manager.switch(project, "Created")
        return {"tip": NAME_TIP}

    def do_ui_error(self, message: str) -> None:
        """The screen hit a bug; keep it in lumiere.log so it can be reported."""
        log.error("Screen error: %s", str(message)[:2000])

    def do_list_audio_inputs(self) -> list[str]:
        try:
            import sounddevice as sd
            return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------ state for the screen

    def snapshot(self) -> dict:
        t = self.tracker
        vc = self.vcdj
        now = time.monotonic()
        devices = []
        if vc:
            for d in sorted(list(vc.devices.values()), key=lambda d: d.number):
                kind = "mixer" if 33 <= d.number <= 40 else "cdj" if 1 <= d.number <= 6 else "other"
                devices.append({"number": d.number, "name": d.name, "ip": d.ip, "kind": kind})
        decks = []
        if t:
            with t.lock:
                followed = t.followed
                numbers = sorted({d["number"] for d in devices if d["kind"] == "cdj"} | set(t.decks))
                for n in numbers:
                    decks.append(self._deck_state(n, now))
        else:
            followed = None
        problem = (vc.problem if vc else None) or next(iter(self.errors.values()), None)
        project = self.manager.project
        pools_state = {}
        for name in pl.POOL_CHANNELS:
            raw = project.config.get("pools", {}).get(name, 0)
            count = raw.get("count", 0) if isinstance(raw, dict) else int(raw)
            live_pool = self.manager.pools.pools.get(name)
            pools_state[name] = {"count": count, "channel": pl.POOL_CHANNELS[name],
                                 "every": int((project.config.get("change_every") or {}).get(name, 0) or 0),
                                 "learned": sorted(n for n in project.learned.get(name, []) if n <= count),
                                 "last": live_pool.last if live_pool else None}
        with self.lock:
            log_entries = [vars(e) for e in list(self.log)[:40]]
            flashes = list(self.flashes)
        master = next((d for d in decks if d["master"]), None)
        clock_beat = self.beats.get(self.clock.selector._current) if self.clock and self.clock.selector._current else None
        return {
            "version": VERSION,
            "live": self.live,
            "settings": asdict(self.settings),
            "problem": problem,
            "lightkey_note": self.manager.lightkey_note,
            "devices": devices,
            "joined": bool(vc and vc.number),
            "device_number": vc.number if vc else 0,
            "decks": decks,
            "followed": followed,
            "clock": {"bpm": clock_beat.bpm if clock_beat else (master or {}).get("bpm", 0.0),
                      "source": clock_beat.name if clock_beat else ""},
            "project": {"name": project.name, "names": self.store.names(),
                        "lightkey_name": project.lightkey_name},
            "pools": {"mode": project.config.get("mode", "shuffle"),
                      "pair": bool(project.config.get("pair_builds_with_drops", False)),
                      "pools": pools_state,
                      "blackout": {**{"mode": "off", "beats": 1.0}, **(project.config.get("blackout") or {}),
                                   "learned": 1 in project.learned.get(BLACKOUT, []),
                                   "channel": BLACKOUT_CHANNEL, "note": BLACKOUT_NOTE},
                      "fills": {"on": bool(project.config.get("fills", True)), "channel": BLACKOUT_CHANNEL,
                                "hit": {"note": FILL_HIT_NOTE, "learned": 1 in project.learned.get(FILL_HIT, [])},
                                "roll": {"note": FILL_ROLL_NOTE, "learned": 1 in project.learned.get(FILL_ROLL, [])},
                                "next": self._next_fill()},
                      "build_fx": {"on": bool(project.config.get("build_fx", True)), "now": self._build_now(),
                                   **{name: {"cc": cc, "learned": 1 in project.learned.get(name, []),
                                             "range": self.fader_range(name)}
                                      for name, cc in BUILD_FADERS.items()}}},
            "log": log_entries,
            "flashes": flashes,
            "ports": {"clock": CLOCK_PORT, "cues": CUE_PORT, "ok": "midi" not in self.errors},
            "audio": {"running": bool(self.audio),
                      "section": (self.audio.detector.state if self.audio else None)},
        }

    def _deck_state(self, n: int, now: float) -> dict:
        t = self.tracker
        deck = t.decks.get(n)
        st = deck.status if deck else None
        key = st.track_key if st else None
        tp = t.analysis.get(key) if key else None
        meta = t.meta.get(key, {}) if key else {}
        beat = self.beats.get(n)
        fresh = bool(st and now - st.received < 3.0)
        return {
            "n": n,
            "loaded": bool(key),
            "key": "-".join(map(str, key)) if key else "",
            "title": meta.get("title") or (f"Track {key[2]}" if key else ""),
            "artist": meta.get("artist", ""),
            "musical_key": meta.get("key", ""),
            "bpm": round(beat.bpm, 2) if beat else (st.bpm if st else 0.0),
            "track_bpm": round(beat.track_bpm, 2) if beat else (st.bpm if st else 0.0),
            "pitch": round((beat.pitch - 1) * 100, 2) if beat else 0.0,
            "beat": deck.beat if deck else None,
            "beat_in_bar": deck.beat_in_bar if deck else 0,
            "playing": bool(st and st.playing and fresh),
            "master": bool(st and st.master and fresh),
            "on_air": bool(st and st.on_air and fresh),
            "source": tp.source if tp else None,
            # rekordbox's own read of the track: club (build/drop) or song (verse/chorus)
            "style": (("club" if tp.mood == 1 else "song") if tp and tp.source == "rekordbox" else None),
            "analysing": bool(key and key not in t.analysis),
            "phrases": [{"beat": p.beat, "cue": p.cue, "label": p.label} for p in tp.phrases] if tp else [],
            "end_beat": tp.end_beat if tp else 0,
            "has_waveform": bool(key and key in t.waveforms),
        }

    def waveform(self, key_text: str, per_second: int = 50) -> Optional[dict]:
        """A deck's waveform, thinned out for drawing, plus its beat grid."""
        t = self.tracker
        if not t:
            return None
        try:
            key = tuple(int(x) for x in key_text.split("-"))
        except ValueError:
            return None
        bands = t.waveforms.get(key)
        if bands is None:
            return None
        step = max(1, 150 // per_second)
        n = len(bands.low) // step * step

        def thin(a):
            return a[:n].reshape(-1, step).max(axis=1) if n else np.zeros(0)
        low, mid, high = thin(bands.low), thin(bands.mid), thin(bands.high)
        peak = float(max(low.max(initial=0), mid.max(initial=0), high.max(initial=0))) or 1.0
        scale = lambda a: np.clip(a / peak * 255, 0, 255).astype(int).tolist()  # noqa: E731
        grid = t.grids.get(key, [])
        return {"per_second": 150 / step, "low": scale(low), "mid": scale(mid), "high": scale(high),
                "grid_ms": [g.time_ms for g in grid], "duration_s": len(bands.low) / 150.0}
