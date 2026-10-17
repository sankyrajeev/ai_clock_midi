"""Live song-section detection for lighting cues: breakdown, build, drop.

Listens to the mixer's master audio (e.g. the DJM-V10's USB send), tracks
how much bass and high-end energy is present relative to the track's own
"full" level, and fires events as the music changes:

  breakdown  the kick/bass drops out for a sustained period
  build      during a breakdown, the high end climbs steadily (risers, rolls)
  drop       the bass slams back in after a breakdown/build (fired at once)
  bar        every downbeat (needs Pro DJ Link beats)
  phrase     every 8 bars counted from the last drop

plus continuous levels (overall energy, bass, highs, build progress) that are
handy for driving intensity or effect speed.

Everything here is relative to adaptive peak followers, so it works at any
mixer level and across genres without calibration; thresholds are tunable.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("prolink_sync.sections")

FULL, BREAKDOWN, BUILD, DROP, BAR, PHRASE = "full", "breakdown", "build", "drop", "bar", "phrase"

# Default MIDI mapping (channel 1). Pick these up with Lightkey's MIDI learn.
NOTES = {BREAKDOWN: 36, BUILD: 37, DROP: 38, PHRASE: 39, BAR: 40}
CCS = {"energy": 20, "bass": 21, "high": 22, "build": 23}


@dataclass
class Tuning:
    break_db: float = 12.0      # bass or kick punch this far under full level = no kick
    break_hold: float = 2.0     # ...for this many seconds = breakdown
    drop_db: float = 5.0        # bass back within this of full level = drop
    drop_confirm: float = 0.04  # seconds the bass must stay up to confirm a drop
    build_window: float = 6.0   # seconds of highs used to judge a rising trend
    build_slope: float = 0.35   # dB/s rise in highs that counts as a build
    build_rise: float = 4.0     # total dB the highs must have climbed in the breakdown
    build_floor_db: float = 24.0  # highs must be within this of their full level to count
    silence_db: float = -60.0   # below this the input is treated as silent


def _ema_coef(dt: float, tau: float) -> float:
    return 1.0 - np.exp(-dt / tau)


def _db(x: float) -> float:
    return 10.0 * np.log10(x + 1e-12)


class PeakFollower:
    """Tracks the recent loud level: jumps up instantly, decays slowly."""

    def __init__(self, decay_db_per_s: float):
        self.decay = decay_db_per_s
        self.value: Optional[float] = None

    def update(self, x: float, dt: float) -> float:
        if self.value is None or x > self.value:
            self.value = x
        else:
            self.value -= self.decay * dt
        return self.value


class SectionDetector:
    def __init__(self, samplerate: int, on_event: Callable[[str, float], None],
                 on_levels: Optional[Callable[[dict], None]] = None,
                 tuning: Optional[Tuning] = None, fft_size: int = 2048):
        self.sr = samplerate
        self.on_event = on_event
        self.on_levels = on_levels
        self.tune = tuning or Tuning()
        self.n = fft_size
        self.buf = np.zeros(fft_size, dtype=np.float32)
        self.window = np.hanning(fft_size).astype(np.float32)
        freqs = np.fft.rfftfreq(fft_size, 1.0 / samplerate)
        self.bass_bins = (freqs >= 30) & (freqs < 150)
        self.high_bins = (freqs >= 4000) & (freqs < 14000)
        self.lock = threading.Lock()

        self.bass_fast = self.bass_slow = self.high_slow = self.rms_slow = 0.0
        self.prev_bass = 0.0
        self.kick_slow = 0.0        # smoothed low-end onset strength ("punch")
        self.last_hit = -1e9        # time of the last strong kick transient
        self.bass_ref = PeakFollower(0.05)
        self.kick_ref = PeakFollower(0.05)
        self.hit_ref = PeakFollower(0.1)
        self.high_ref = PeakFollower(0.3)
        self.rms_ref = PeakFollower(0.1)

        self.state: Optional[str] = None
        self.low_since: Optional[float] = None
        self.up_since: Optional[float] = None
        self.highs: deque[tuple[float, float]] = deque()
        self.high_min = 0.0
        self.last_t: Optional[float] = None

        self.bar_count = 0          # bars since the last drop
        self.last_downbeat = -1e9

    # -- audio --------------------------------------------------------------

    def process(self, block: np.ndarray, t: float) -> None:
        """Feed a mono float block whose last sample was captured at time t."""
        with self.lock:
            self._process(block, t)

    def _process(self, block: np.ndarray, t: float) -> None:
        k = len(block)
        if k >= self.n:
            self.buf[:] = block[-self.n:]
        else:
            self.buf[:-k] = self.buf[k:]
            self.buf[-k:] = block
        dt = k / self.sr if self.last_t is None else max(1e-4, t - self.last_t)
        self.last_t = t

        spec = np.abs(np.fft.rfft(self.buf * self.window)) ** 2 / self.n
        bass = float(spec[self.bass_bins].sum())
        high = float(spec[self.high_bins].sum())
        rms = float(np.mean(block.astype(np.float64) ** 2))

        # Kicks are sudden jumps in low-end energy; a sustained sub bass isn't.
        flux = max(0.0, bass - self.prev_bass)
        self.prev_bass = bass
        self.kick_slow += _ema_coef(dt, 0.8) * (flux - self.kick_slow)
        self.bass_fast += _ema_coef(dt, 0.08) * (bass - self.bass_fast)
        self.bass_slow += _ema_coef(dt, 0.5) * (bass - self.bass_slow)
        self.high_slow += _ema_coef(dt, 0.4) * (high - self.high_slow)
        self.rms_slow += _ema_coef(dt, 0.3) * (rms - self.rms_slow)

        rms_db = _db(self.rms_slow)
        if rms_db < self.tune.silence_db:
            self.low_since = self.up_since = None
            return

        bass_fast_db, bass_slow_db, high_db = _db(self.bass_fast), _db(self.bass_slow), _db(self.high_slow)
        ref = self.bass_ref.update(bass_slow_db, dt)
        high_ref = self.high_ref.update(high_db, dt)
        rms_ref = self.rms_ref.update(rms_db, dt)
        rel_fast, rel_slow = bass_fast_db - ref, bass_slow_db - ref
        kick_rel = _db(self.kick_slow) - self.kick_ref.update(_db(self.kick_slow), dt)
        hit_db = _db(flux)
        if hit_db > self.hit_ref.update(hit_db, dt) - 10.0:
            self.last_hit = t

        self._step(t, rel_fast, min(rel_slow, kick_rel), high_db)

        if self.on_levels:
            build = 0.0
            if self.state in (BREAKDOWN, BUILD):
                build = min(1.0, max(0.0, (high_db - self.high_min) / 12.0))
            self.on_levels({
                "energy": _norm(rms_db - rms_ref, 30),
                "bass": _norm(rel_slow, 24),
                "high": _norm(high_db - high_ref, 24),
                "build": build,
            })

    def _step(self, t: float, rel_fast: float, rel_slow: float, high_db: float) -> None:
        """rel_fast: fast bass level; rel_slow: the lower of slow bass and kick punch."""
        tu = self.tune
        if self.state in (None, FULL):
            if rel_slow < -tu.break_db:
                if self.low_since is None:
                    self.low_since = t
                if self.state == FULL and t - self.low_since >= tu.break_hold:
                    self._enter(BREAKDOWN, t)
                    self.highs.clear()
                    self.high_min = high_db
            else:
                self.low_since = None
                if self.state is None and rel_slow > -tu.drop_db:
                    self.state = FULL  # first time we hear the track at full level
            return

        # In a breakdown or build: watch for the drop (bass back *with* a kick).
        if rel_fast > -tu.drop_db and t - self.last_hit < 0.15:
            if self.up_since is None:
                self.up_since = t
            if t - self.up_since >= tu.drop_confirm:
                self._enter(DROP, t)
                self.state = FULL
                self.low_since = self.up_since = None
                self.bar_count = 1 if t - self.last_downbeat < 0.3 else 0
                return
        else:
            self.up_since = None

        self.high_min = min(self.high_min, high_db)
        self.highs.append((t, high_db))
        while self.highs and t - self.highs[0][0] > tu.build_window:
            self.highs.popleft()
        if (self.state == BREAKDOWN and len(self.highs) > 8
                and t - self.highs[0][0] >= 0.9 * tu.build_window):
            ts, hs = zip(*self.highs)
            slope = np.polyfit(np.asarray(ts) - ts[0], hs, 1)[0]
            audible = high_db > (self.high_ref.value or high_db) - tu.build_floor_db
            if slope > tu.build_slope and high_db - self.high_min > tu.build_rise and audible:
                self._enter(BUILD, t)

    def _enter(self, event: str, t: float) -> None:
        log.debug("section: %s", event.upper())
        if event != DROP:
            self.state = event
        self.on_event(event, t)

    # -- beats --------------------------------------------------------------

    def on_beat(self, beat_in_bar: int, t: float) -> None:
        """Feed Pro DJ Link beats so bar/phrase events line up with the music."""
        if beat_in_bar != 1:
            return
        with self.lock:
            self.last_downbeat = t
            self.bar_count += 1
            self.on_event(BAR, t)
            if self.bar_count > 1 and self.bar_count % 8 == 1:
                self.on_event(PHRASE, t)


def _norm(rel_db: float, span_db: float) -> float:
    """Map a level from -span..0 dB relative to its peak onto 0..1."""
    return min(1.0, max(0.0, 1.0 + rel_db / span_db))


# --------------------------------------------------------------------------
# I/O: audio input and MIDI cue output
# --------------------------------------------------------------------------

class CueOutput:
    """Sends section events as MIDI notes and levels as CCs to Lightkey."""

    def __init__(self, port_name: str, use_existing: bool, channel: int = 0):
        import mido

        self._mido = mido
        self.port = mido.open_output(port_name, virtual=not use_existing)
        self.channel = channel
        self._last_cc: dict[int, int] = {}
        self._lock = threading.Lock()
        log.info("MIDI cues on port %r (notes %s, CCs %s)", port_name, NOTES, CCS)

    def event(self, name: str, _t: float = 0.0) -> None:
        note = NOTES.get(name)
        if note is None:
            return
        self._send("note_on", note=note, velocity=127)
        threading.Timer(0.08, self._send, ("note_off",), {"note": note, "velocity": 0}).start()

    def levels(self, lv: dict) -> None:
        for name, cc in CCS.items():
            value = int(round(lv[name] * 127))
            last = self._last_cc.get(cc)
            if value != last and (last is None or abs(value - last) >= 2 or value in (0, 127)):
                self._last_cc[cc] = value
                self._send("control_change", control=cc, value=value)

    def _send(self, kind: str, **kw) -> None:
        with self._lock:
            self.port.send(self._mido.Message(kind, channel=self.channel, **kw))

    def close(self) -> None:
        self.port.close()


def find_audio_device(query: Optional[str]):
    """Return (device index, info). Defaults to a Pioneer DJM if one is attached."""
    import sounddevice as sd

    devices = sd.query_devices()
    inputs = [(i, d) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    if query is not None:
        if query.isdigit():
            i = int(query)
            return i, devices[i]
        for i, d in inputs:
            if query.lower() in d["name"].lower():
                return i, d
        raise SystemExit(f"No audio input matching {query!r}; see --list-audio")
    for i, d in inputs:
        if "djm" in d["name"].lower():
            return i, d
    i = sd.default.device[0]
    if i is None or i < 0:
        raise SystemExit("No audio input found; see --list-audio")
    return i, devices[i]


def list_audio_devices() -> None:
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"{i:3d}  {d['name']}  ({d['max_input_channels']} in, {int(d['default_samplerate'])} Hz)")


class AudioInput:
    """Streams an input device's channels (mixed to mono) into a detector."""

    def __init__(self, device: Optional[str], channels: list[int],
                 make_detector: Callable[[int], SectionDetector], blocksize: int = 1024):
        import sounddevice as sd

        index, info = find_audio_device(device)
        need = max(channels)
        if need > info["max_input_channels"]:
            raise SystemExit(f"{info['name']} has only {info['max_input_channels']} input channels")
        sr = int(info["default_samplerate"])
        self.detector = make_detector(sr)
        self.cols = [c - 1 for c in channels]

        def callback(indata, frames, tinfo, status):  # runs on the audio thread
            if status:
                log.debug("audio: %s", status)
            mono = indata[:, self.cols].mean(axis=1)
            self.detector.process(mono, time.monotonic())

        self.stream = sd.InputStream(device=index, channels=need, samplerate=sr,
                                     blocksize=blocksize, dtype="float32", callback=callback)
        log.info("Listening to %r, channels %s at %d Hz", info["name"], channels, sr)

    def start(self) -> None:
        self.stream.start()

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Read a PCM WAV (16/24/32-bit) as mono float32 using only the stdlib."""
    import wave

    with wave.open(path, "rb") as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        data = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        ints = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8)
                | (b[:, 2].astype(np.int32) << 16))
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
        data = ints.astype(np.float32) / float(1 << 23)
    elif width == 4:
        data = np.frombuffer(raw, "<i4").astype(np.float32) / float(1 << 31)
    else:
        raise SystemExit(f"Unsupported WAV sample width: {width * 8} bit")
    return data.reshape(-1, ch).mean(axis=1), sr


def analyze_file(path: str, tuning: Optional[Tuning] = None, blocksize: int = 1024) -> list[tuple[float, str]]:
    """Run the detector over a WAV file; returns [(seconds, event)]."""
    audio, sr = read_wav(path)
    events: list[tuple[float, str]] = []
    det = SectionDetector(sr, lambda e, t: events.append((t, e)), tuning=tuning)
    for start in range(0, len(audio) - blocksize + 1, blocksize):
        det.process(audio[start:start + blocksize], (start + blocksize) / sr)
    return events
