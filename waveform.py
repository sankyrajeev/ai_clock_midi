"""Track structure from the CDJ's waveform, for tracks without phrase analysis.

Every track exported with rekordbox carries detailed waveforms, and a CDJ-3000
also builds beat grids for tracks rekordbox never touched. Both can be read
over the network, so as soon as a track loads we can analyse the whole thing
bar by bar and work out its intro, builds, drops, breakdowns and outro ahead
of time, with no audio cable. The result has the same shape as rekordbox's
phrase analysis, so the cue engine treats it identically.

Waveform formats (150 entries per second), per crate-digger's ANLZ analysis:
  PWV7 (.2EX, CDJ-3000)  3 bytes: mid, high, low
  PWV5 (.EXT, nxs2+)     16 bits: red 3 | green 3 | blue 3 | height 5 | 2 unused
  PWV3 (.EXT)            1 byte:  whiteness 3 | height 5
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

import numpy as np

from phrases import BREAKDOWN, BUILD, DROP, HIGH, INTRO, OUTRO, VERSE, Phrase, TrackPhrases

ENTRIES_PER_SECOND = 150

# Waveform tags to try, best first: (tag, file extension)
WAVEFORM_TAGS = [("PWV7", "2EX"), ("PWV5", "EXT"), ("PWV3", "EXT")]


@dataclass
class Bands:
    low: np.ndarray
    mid: np.ndarray
    high: np.ndarray
    kind: str  # which tag it came from


@dataclass
class GridBeat:
    beat_in_bar: int
    bpm: float
    time_ms: int


def parse_beat_grid(blob: bytes) -> list[GridBeat]:
    """dbserver beat grid (response 4602): 0x14-byte header, then 16-byte
    little-endian entries of beat-in-bar, tempo x100, time in ms."""
    beats = []
    for off in range(0x14, len(blob) - 7, 16):
        bib, tempo, ms = struct.unpack_from("<HHI", blob, off)
        if not 1 <= bib <= 4:
            break
        beats.append(GridBeat(bib, tempo / 100.0, ms))
    return beats


def build_beat_grid(bpm: float, n_beats: int, first_ms: int = 0, first_bib: int = 1) -> bytes:
    """Inverse of parse_beat_grid, for tests and the demo."""
    out = bytearray(0x14)
    for i in range(n_beats):
        ms = int(round(first_ms + i * 60000.0 / bpm))
        out += struct.pack("<HHI", (first_bib - 1 + i) % 4 + 1, int(round(bpm * 100)), ms) + bytes(8)
    return bytes(out)


def parse_waveform(data: bytes) -> Optional[Bands]:
    """Find a detail waveform tag in a dbserver blob and split it into bands."""
    for tag in ("PWV7", "PWV5", "PWV3"):
        start = data.find(tag.encode())
        if start < 0:
            continue
        t = data[start:]
        len_header, len_tag = struct.unpack_from(">II", t, 4)
        entry_bytes, count = struct.unpack_from(">II", t, 12)
        body = np.frombuffer(t[len_header:len_header + entry_bytes * count], dtype=np.uint8)
        if tag == "PWV7":
            e = body.reshape(-1, 3).astype(np.float32)
            return Bands(low=e[:, 2], mid=e[:, 0], high=e[:, 1], kind=tag)
        if tag == "PWV5":
            v = body.reshape(-1, 2).astype(np.uint16)
            v = (v[:, 0] << 8) | v[:, 1]
            height = ((v >> 2) & 0x1F).astype(np.float32)
            r, g, b = ((v >> 13) & 7) / 7.0, ((v >> 10) & 7) / 7.0, ((v >> 7) & 7) / 7.0
            # rekordbox colours lows red, mids green, highs blue
            return Bands(low=r * height, mid=g * height, high=b * height, kind=tag)
        height = (body & 0x1F).astype(np.float32)
        white = ((body >> 5) & 7) / 7.0
        return Bands(low=height * (1 - white), mid=height * 0.5, high=height * white, kind=tag)
    return None


def build_waveform_tag(low: np.ndarray, mid: np.ndarray, high: np.ndarray) -> bytes:
    """A PWV7 tag from band arrays (0-255), for tests and the demo."""
    body = np.stack([mid, high, low], axis=1).clip(0, 255).astype(np.uint8).tobytes()
    head = b"PWV7" + struct.pack(">II", 0x18, 0x18 + len(body)) + struct.pack(">II", 3, len(low)) + struct.pack(">I", 0x00960000)
    return head + body


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

LOW, MIDLVL, PEAK = 0, 1, 2


def _bar_features(bands: Bands, grid: list[GridBeat]):
    """Average band energy per bar. Returns (bar start beat numbers, low, high, total)."""
    times = np.array([b.time_ms for b in grid], dtype=np.float64) / 1000.0
    idx = (times * ENTRIES_PER_SECOND).astype(int).clip(0, len(bands.low))
    downbeats = [i for i, b in enumerate(grid) if b.beat_in_bar == 1]
    starts, low, high, total = [], [], [], []
    for j, i in enumerate(downbeats):
        end_beat = downbeats[j + 1] if j + 1 < len(downbeats) else min(i + 4, len(grid))
        a = idx[i]
        b = idx[end_beat] if end_beat < len(idx) else len(bands.low)
        if b <= a:
            continue
        lo, mi, hi = bands.low[a:b].mean(), bands.mid[a:b].mean(), bands.high[a:b].mean()
        starts.append(i + 1)  # 1-based beat number, as CDJ status packets count
        low.append(lo)
        high.append(hi)
        total.append(lo + mi + hi)
    return np.array(starts), np.array(low), np.array(high), np.array(total)


def _segments(levels: list[int], min_len: int) -> list[list[int]]:
    """Group bars into [level, start, end) runs, absorbing runs shorter than min_len."""
    runs = []
    for i, lv in enumerate(levels):
        if runs and runs[-1][0] == lv:
            runs[-1][2] = i + 1
        else:
            runs.append([lv, i, i + 1])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for k, (lv, a, b) in enumerate(runs):
            if b - a >= min_len:
                continue
            # merge into the neighbour whose level is closest (prefer the longer one on ties)
            left = runs[k - 1] if k > 0 else None
            right = runs[k + 1] if k + 1 < len(runs) else None
            pick = min((n for n in (left, right) if n), key=lambda n: (abs(n[0] - lv), -(n[2] - n[1])))
            pick[1], pick[2] = min(pick[1], a), max(pick[2], b)
            runs.pop(k)
            changed = True
            break
    merged = []
    for r in runs:
        if merged and merged[-1][0] == r[0]:
            merged[-1][2] = r[2]
        else:
            merged.append(r)
    return merged


def _snap(bar: int, origin: int, n_bars: int) -> int:
    """Pull a section boundary onto a 4-bar phrase line if it is within a bar of one."""
    rel = bar - origin
    near = round(rel / 4) * 4
    return max(0, min(n_bars, origin + near)) if abs(near - rel) <= 1 else bar


def analyse(bands: Bands, grid: list[GridBeat], min_bars: int = 4) -> Optional[TrackPhrases]:
    """Work out the track's sections from its waveform and beat grid."""
    starts, low, high, total = _bar_features(bands, grid)
    n = len(starts)
    if n < 8:
        return None
    low_ref = np.percentile(low, 90) or 1.0
    tot_ref = np.percentile(total, 90) or 1.0
    low_rel, tot_rel = low / low_ref, total / tot_ref

    levels = []
    for lr, tr in zip(low_rel, tot_rel):
        if lr < 0.35:
            levels.append(LOW)           # kick and bass gone
        elif lr >= 0.6 and tr >= 0.82:
            levels.append(PEAK)          # full energy
        else:
            levels.append(MIDLVL)
    runs = _segments(levels, min_bars)

    # Snap boundaries to 4-bar phrase lines counted from the first boundary.
    origin = runs[1][1] if len(runs) > 1 else 0
    for k in range(1, len(runs)):
        b = _snap(runs[k][1], origin, n)
        if runs[k - 1][1] < b < runs[k][2]:
            runs[k - 1][2] = runs[k][1] = b

    first_peak = next((k for k, r in enumerate(runs) if r[0] == PEAK), None)
    last_peak = max((k for k, r in enumerate(runs) if r[0] == PEAK), default=None)
    sections: list[tuple[int, str, str]] = []  # (bar index, cue, label)
    for k, (lv, a, b) in enumerate(runs):
        nxt = runs[k + 1][0] if k + 1 < len(runs) else None
        at_start, at_end = k == 0, k == len(runs) - 1
        if lv == PEAK:
            sections.append((a, DROP, "Drop"))
            continue
        if at_start and (first_peak is None or k < first_peak):
            sections.append((a, INTRO, "Intro"))
            continue
        if at_end and last_peak is not None and k > last_peak:
            sections.append((a, OUTRO, "Outro"))
            continue
        if nxt == PEAK:
            # Find where the highs start climbing towards the drop: that's the build.
            split = _build_start(high[a:b])
            if lv == MIDLVL and split == 0:
                sections.append((a, BUILD, "Build"))
            elif split is not None and split < b - a:
                if split > 0:
                    sections.append((a, BREAKDOWN if lv == LOW else VERSE, "Breakdown" if lv == LOW else "Verse"))
                sections.append((a + split, BUILD, "Build"))
            else:
                sections.append((a, BREAKDOWN if lv == LOW else BUILD, "Breakdown" if lv == LOW else "Build"))
            continue
        sections.append((a, BREAKDOWN if lv == LOW else VERSE, "Breakdown" if lv == LOW else "Verse"))

    phrases = [Phrase(int(starts[bar]), cue, label) for bar, cue, label in sections]
    end_beat = len(grid) + 1
    return TrackPhrases(HIGH, phrases, end_beat, source="waveform")


def _build_start(high: np.ndarray) -> Optional[int]:
    """Bar offset within a pre-drop section where the high end starts climbing
    (fit "flat, then a straight ramp" and pick the best knee), or None if the
    highs never clearly rise."""
    n = len(high)
    if n < 2:
        return None
    x = np.arange(n, dtype=np.float64)
    best, best_err = None, np.inf
    for k in range(0, n - 1):
        flat, ramp = high[:k], high[k:]
        err = ((flat - flat.mean()) ** 2).sum() if k else 0.0
        coef = np.polyfit(x[k:], ramp, 1)
        err += ((np.polyval(coef, x[k:]) - ramp) ** 2).sum()
        if coef[0] > 0 and err < best_err:
            best, best_err = k, err
    if best is None:
        return None
    ramp = high[best:]
    if ramp[-1] / max(ramp[0], 1e-6) < 1.4:
        return None
    near = round(best / 4) * 4  # builds start on phrase lines
    return near if abs(near - best) <= 1 and near < n else best


def gap_before(bands: Bands, grid: list[GridBeat], drop_beat: int, bpm: float,
               max_beats: float = 8.0, quiet: float = 0.3) -> float:
    """How many beats of near-silence come right before `drop_beat` (in half beats,
    0 when there's no gap). Compares each half beat before the drop with the
    level of the 8 beats before that stretch."""
    level = bands.low.astype(float) + bands.mid + bands.high
    if not len(level) or drop_beat < 3:
        return 0.0

    def at(beat: float) -> float:  # seconds at a (fractional) 1-based beat
        i = int(beat) - 1
        if grid and 0 <= i < len(grid) - 1:
            a, b = grid[i].time_ms, grid[i + 1].time_ms
            return (a + (beat - int(beat)) * (b - a)) / 1000
        return (beat - 1) * 60 / (bpm or 120)

    def mean(b0: float, b1: float) -> float:
        lo, hi = int(at(b0) * ENTRIES_PER_SECOND), int(at(b1) * ENTRIES_PER_SECOND)
        lo, hi = max(0, lo), min(len(level), max(hi, lo + 1))
        return float(level[lo:hi].mean()) if hi > lo else 0.0

    gap = 0.0
    while gap < max_beats and drop_beat - gap - 0.5 >= 1:
        start = drop_beat - gap - 0.5
        ref = mean(max(1, start - 8), start)
        if ref <= 0 or mean(start, start + 0.5) > quiet * ref:
            break
        gap += 0.5
    return gap
