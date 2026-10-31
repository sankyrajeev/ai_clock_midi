"""Generated tracks with known build-ups, for testing the build and fill analysis.

Each track: 8 bars of groove, a build-up, a 16-bar drop and 4 bars of outro,
as a CDJ-3000 style 3-band waveform with a beat grid. Every build gets a
random snare roll schedule (or none), with or without the kick, hats and a
white-noise riser underneath, at a random tempo, noise level and grid offset.
The true roll speed on every beat comes with it, so the analysis can be scored.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import waveform as wf

EPS = wf.ENTRIES_PER_SECOND
SCHEDULES = ("none", "classic", "steady16", "eighths_then_16", "sudden", "quarters_all", "early_stop")


@dataclass
class CorpusTrack:
    name: str
    bands: wf.Bands
    grid: list
    bpm: float
    build_start: int      # first beat of the build
    drop: int             # first beat of the drop
    rate: list[int]       # true snare hits per beat through the build
    riser: bool
    silent_last_beat: bool


def _schedule(kind: str, bars: int, rng) -> list[int]:
    r = [0] * (bars * 4)

    def put(b0, b1, rate):
        for i in range(int(b0 * 4), int(b1 * 4)):
            r[i] = rate

    if kind == "classic":
        put(bars / 2, bars * 3 / 4, 1)
        put(bars * 3 / 4, bars * 7 / 8, 2)
        put(bars * 7 / 8, bars - 1, 4)
        put(bars - 1, bars, 8 if rng.random() < 0.5 else 4)
    elif kind == "steady16":
        put(bars - 4, bars, 4)
    elif kind == "eighths_then_16":
        put(bars - 4, bars - 2, 2)
        put(bars - 2, bars, 4)
    elif kind == "sudden":
        put(bars - 2, bars - 1, 4)
        put(bars - 1, bars, 8)
    elif kind == "quarters_all":
        put(0, bars, 1)
    elif kind == "early_stop":
        put(0, bars / 2, 2)
    return r


def make(seed: int, hard: bool = False) -> CorpusTrack:
    """`hard`: more noise, quieter snares, louder risers and slower-decaying hits."""
    rng = np.random.default_rng(seed)
    bpm = float(rng.uniform(160, 175) if rng.random() < 0.15 else rng.uniform(118, 150))
    build_bars = int(rng.choice([8, 16]))
    kind = SCHEDULES[seed % len(SCHEDULES)]
    intro, drop_bars, outro = 8, 16, 4
    build_start = intro * 4 + 1
    drop = build_start + build_bars * 4
    beats = (intro + build_bars + drop_bars + outro) * 4
    offset = float(rng.uniform(0, 0.4))  # where beat 1 sits, in seconds
    beat_len = 60.0 / bpm
    n = int((offset + (beats + 1) * beat_len) * EPS)
    noise = float(rng.uniform(6, 16) if hard else rng.uniform(2, 8))
    low, mid, high = (rng.uniform(0, noise, n) for _ in range(3))
    at = lambda beat: int((offset + (beat - 1) * beat_len) * EPS)

    def hit(band, beat, amp, decay):
        i = at(beat)
        for k in range(14):
            if 0 <= i + k < n:
                band[i + k] = max(band[i + k], amp * decay ** k)

    def bed(band, b0, b1, level):
        band[at(b0):at(b1)] += level * (1 + 0.06 * rng.standard_normal(at(b1) - at(b0)))

    kick_in, hats_in = rng.random() < 0.5, rng.random() < 0.6
    riser = rng.random() < 0.6
    silent = rng.random() < 0.25
    rate = _schedule(kind, build_bars, rng)

    for b in range(1, beats + 1):
        in_build = build_start <= b < drop
        loud = 1.0 if b >= drop else 0.75
        if in_build and silent and b == drop - 1:
            continue
        if not in_build or kick_in:
            hit(low, b, 200 * loud, 0.6)
        if not in_build or hats_in:
            hit(high, b + 0.5, 110 * loud, 0.55)
        if not in_build and (b - 1) % 4 in (1, 3):
            hit(mid, b, 150 * loud, 0.6)
        if not in_build:
            bed(low, b, b + 1, 30 * loud)
            bed(mid, b, b + 1, 25 * loud)
        else:
            bed(mid, b, b + 1, 20)
    if riser:
        i0, i1 = at(build_start), at(drop - (1 if silent else 0))
        top = float(rng.uniform(180, 250) if hard else rng.uniform(120, 220))
        high[i0:i1] += np.linspace(30, top, i1 - i0) * (1 + 0.05 * rng.standard_normal(i1 - i0))
    amp = float(rng.uniform(70, 130) if hard else rng.uniform(110, 190))
    decay = float(rng.uniform(0.6, 0.78) if hard else rng.uniform(0.45, 0.7))
    for i, r in enumerate(rate):
        b = build_start + i
        if not r or (silent and b == drop - 1):
            continue
        for k in range(r):
            a = amp * float(rng.uniform(0.85, 1.15)) * (0.8 + 0.4 * i / len(rate))
            hit(mid, b + k / r, a, decay)
            hit(high, b + k / r, 0.4 * a, decay)
    if silent:
        rate[-4:] = rate[-4:-1] + [0]
    bands = wf.Bands(low=np.clip(low, 0, 255), mid=np.clip(mid, 0, 255), high=np.clip(high, 0, 255), kind="PWV7")
    grid = wf.parse_beat_grid(wf.build_beat_grid(bpm, beats + 1, first_ms=int(offset * 1000)))
    return CorpusTrack(f"#{seed:03d} {kind} {build_bars} bars {bpm:.0f} BPM"
                       f"{' kick' if kick_in else ''}{' hats' if hats_in else ''}{' riser' if riser else ''}"
                       f"{' gap' if silent else ''}",
                       bands, grid, bpm, build_start, drop, rate, riser, silent)


def score(tracks: list[CorpusTrack]) -> dict:
    """Run the analysis over `tracks` and compare it with the truth."""
    beats = exact = false_pos = no_roll_beats = top_ok = rise_ok = risers = roll_fill_ok = 0
    misses = []
    for t in tracks:
        c = wf.build_curve(t.bands, t.grid, t.build_start, t.drop, t.bpm)
        got = c.rate if c else [0] * len(t.rate)
        ok = sum(a == b for a, b in zip(got, t.rate))
        beats += len(t.rate)
        exact += ok
        no_roll_beats += sum(1 for r in t.rate if r == 0)
        false_pos += sum(1 for a, b in zip(got, t.rate) if b == 0 and a > 0)
        top_ok += max(got) == max(t.rate)
        if ok < len(t.rate):
            misses.append((t.name, len(t.rate) - ok))
        if t.riser and c:
            risers += 1
            curve = np.array(c.intensity[: max(4, len(c.intensity) - 8)])
            truth = np.linspace(0, 1, len(curve))
            rise_ok += float(np.corrcoef(curve, truth)[0, 1]) > 0.8
        fill = wf.drop_fill(t.bands, t.grid, t.drop, t.bpm)
        rolling = sum(t.rate[-4:]) >= 8  # 8+ snare hits in the last bar
        roll_fill_ok += (fill.kind == wf.ROLL) == rolling
    return {"tracks": len(tracks), "beats": beats, "beat_accuracy": exact / beats,
            "false_roll_beats": false_pos / max(1, no_roll_beats), "top_speed_right": top_ok / len(tracks),
            "riser_followed": rise_ok / max(1, risers), "fill_roll_right": roll_fill_ok / len(tracks),
            "misses": misses}
