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
from dataclasses import dataclass, field
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


def _seconds_at(grid: list[GridBeat], bpm: float, beat: float) -> float:
    """Seconds into the track at a (fractional) 1-based beat."""
    i = int(beat) - 1
    if grid and 0 <= i < len(grid) - 1:
        a, b = grid[i].time_ms, grid[i + 1].time_ms
        return (a + (beat - int(beat)) * (b - a)) / 1000
    return (beat - 1) * 60 / (bpm or 120)


def _entries(grid: list[GridBeat], bpm: float, b0: float, b1: float, n: int) -> tuple[int, int]:
    lo = int(_seconds_at(grid, bpm, b0) * ENTRIES_PER_SECOND)
    hi = int(_seconds_at(grid, bpm, b1) * ENTRIES_PER_SECOND)
    lo, hi = max(0, lo), min(n, max(hi, lo + 1))
    return lo, hi


def gap_before(bands: Bands, grid: list[GridBeat], drop_beat: int, bpm: float,
               max_beats: float = 8.0, quiet: float = 0.3) -> float:
    """How many beats of near-silence come right before `drop_beat` (in half beats,
    0 when there's no gap). Compares each half beat before the drop with the
    level of the 8 beats before that stretch."""
    level = bands.low.astype(float) + bands.mid + bands.high
    if not len(level) or drop_beat < 3:
        return 0.0

    def mean(b0: float, b1: float) -> float:
        lo, hi = _entries(grid, bpm, b0, b1, len(level))
        return float(level[lo:hi].mean()) if hi > lo else 0.0

    gap = 0.0
    while gap < max_beats and drop_beat - gap - 0.5 >= 1:
        start = drop_beat - gap - 0.5
        ref = mean(max(1, start - 8), start)
        if ref <= 0 or mean(start, start + 0.5) > quiet * ref:
            break
        gap += 0.5
    return gap


# --------------------------------------------------------------------------
# The bars right before a drop: silence, synth stabs, a snare roll, a riser
# --------------------------------------------------------------------------

FILL_BARS = 4  # how far back from the drop to look
SILENCE, HITS, ROLL, RISER, GROOVE = "silence", "hits", "roll", "riser", "groove"


@dataclass
class DropFill:
    """What the waveform shows in the bars before one drop."""
    bars: list[str]                 # each bar's kind, oldest first (GROOVE = the normal beat)
    hits: list[float] = field(default_factory=list)  # track beats (fractional) of each hit to flash on
    roll_start: Optional[float] = None               # first beat of a roll, for a strobe
    per_beat: float = 0.0           # fastest hits per beat in the fill (a 1/16 roll is 4)

    @property
    def kind(self) -> str:
        """The bar right before the drop."""
        return self.bars[-1] if self.bars else GROOVE

    def describe(self) -> str:
        if self.kind == GROOVE:
            return "no fill"
        if self.kind == ROLL:
            return f"roll, {len(self.hits)} hits" + (f" ({self.per_beat:g} per beat)" if self.per_beat else "")
        if self.kind == HITS:
            return f"{len(self.hits)} hit{'s' if len(self.hits) != 1 else ''}"
        return {SILENCE: "silence", RISER: "riser"}[self.kind]


def _rises(env: np.ndarray) -> np.ndarray:
    """How far each entry jumps above the lowest of the 4 before it."""
    rise = np.zeros(len(env))
    for i in range(1, len(env)):
        rise[i] = env[i] - env[max(0, i - 4):i].min()
    return rise


def _onsets(env: np.ndarray, floor: float, min_gap: int) -> list[int]:
    """Entries where `env` jumps up by at least `floor` (a hit), at least `min_gap` apart."""
    if len(env) < 4:
        return []
    rise = _rises(env)
    picked: list[int] = []
    for i in np.argsort(-rise, kind="stable"):
        if rise[i] < floor:
            break
        if all(abs(int(i) - j) >= min_gap for j in picked):
            picked.append(int(i))
    return sorted(picked)


class _HitScanner:
    """Finds hits (snare, clap, stab, a roll's strokes) in the mids and highs of a
    stretch of track, measured against the drop that follows: how loud it is,
    how hard it hits and where its own groove hits."""

    def __init__(self, bands: Bands, grid: list[GridBeat], bpm: float, drop_beat: int):
        self.grid, self.bpm = grid, bpm
        self.low = bands.low.astype(float)
        self.hi_env = bands.mid.astype(float) + bands.high
        self.level = self.low + self.hi_env
        self.n = n = len(self.level)
        self.step = 60.0 / (bpm or 120) / 8  # a 1/32 note, in seconds
        self.min_gap = max(2, int(self.step * ENTRIES_PER_SECOND * 0.7))
        self.ok = n > 0 and drop_beat > 4 and self.entries(drop_beat, drop_beat + 8)[1] < n - 1
        if not self.ok:
            return
        self.ref_level = float(np.median(self.beat_peaks(self.level, drop_beat, 8))) or 1.0
        # Hits are found in the mids and the highs separately, so a snare roll still
        # shows over a loud white-noise riser. Each is a jump above the sound just
        # before it, compared with how hard the drop hits in that band.
        self.hit_bands = [bands.mid.astype(float), bands.high.astype(float)]
        d0, d1 = self.entries(drop_beat, drop_beat + 8)
        self.ref_rise = [float(np.percentile(_rises(b[d0:d1]), 99)) or 1.0 for b in self.hit_bands]
        self.groove_bands: list[set] = [set(), set()]  # empty while the drop itself is scanned
        drop_bars = [self.hits_by_band(drop_beat + 4 * k, drop_beat + 4 * k + 4) for k in (0, 1)]
        for k, bar in enumerate(drop_bars):
            for h, bands_hit in bar:
                for b in bands_hit:
                    self.groove_bands[b].add(int(round((h - drop_beat - 4 * k) * 8)) % 32)
        self.groove = set().union(*self.groove_bands)
        self.groove_hits = max(len(h) for h in drop_bars)

    def entries(self, b0: float, b1: float) -> tuple[int, int]:
        return _entries(self.grid, self.bpm, b0, b1, self.n)

    def beat_peaks(self, arr: np.ndarray, b0: float, beats: int) -> np.ndarray:
        out = []
        for b in range(beats):
            lo, hi = self.entries(b0 + b, b0 + b + 1)
            out.append(float(arr[lo:hi].max()) if hi > lo else 0.0)
        return np.array(out)

    def hits(self, b0: float, b1: float) -> list[float]:
        """Track beats (fractional, on the 1/32 grid) of the hits from b0 up to b1 that
        are worth a flash: everything except hi-hats where the drop has its hi-hats."""
        return [h for h, bands_hit in self.hits_by_band(b0, b1)
                if not (bands_hit == {1} and self.is_groove(b0, h, bands_hit))]

    def hits_by_band(self, b0: float, b1: float) -> list[tuple[float, frozenset]]:
        """Each hit from b0 up to b1 and which bands it showed in (0 = mids, 1 = highs)."""
        lo, hi = self.entries(b0, b1)
        lo2 = max(0, lo - 4)  # a little before, so a hit right on b0 shows as a rise
        start = _seconds_at(self.grid, self.bpm, b0)
        beat_entries = int(8 * self.step * ENTRIES_PER_SECOND)
        found: dict[float, set] = {}
        for k, (band, ref) in enumerate(zip(self.hit_bands, self.ref_rise)):
            env = band[lo2:hi]
            if len(env) < 4:
                continue
            rise = _rises(env)
            # Loud enough for this band, and among the strongest in this stretch.
            floor = max(0.3 * ref, 0.45 * float(rise.max()), 0.12 * self.ref_level)
            onsets = _onsets(env, floor, self.min_gap)
            for j, i in enumerate(onsets):
                end = min(onsets[j + 1] if j + 1 < len(onsets) else len(env), i + beat_entries)
                peak = float(env[i:i + 3].max())
                if rise[i] < 0.45 * peak:  # a wobble on a loud sound, not a hit
                    continue
                # A hit dies away again; a sound that rises and stays is a riser.
                if end > i + 3 and env[i + 3:end].min() > peak - 0.6 * rise[i]:
                    continue
                q = round(round((lo2 + i) / ENTRIES_PER_SECOND - start, 6) / self.step) / 8
                if not 0 <= q < b1 - b0:
                    continue
                # One sound often shows in both bands: on the same 1/32 step as a hit
                # the other band found, it's the same hit.
                same = next((f for f in found if abs(f - (b0 + q)) < 0.07 and k not in found[f]), None)
                found.setdefault(b0 + q if same is None else same, set()).add(k)
        return sorted((h, frozenset(b)) for h, b in found.items())

    def is_groove(self, b0: float, hit: float, bands: frozenset) -> bool:
        """Does this hit sit where the drop's groove hits in the same band (a hat, a clap)?"""
        step = int(round((hit - b0) * 8)) % 32
        return all(any((step + d) % 32 in self.groove_bands[k] for d in (-1, 0, 1)) for k in bands)

    @staticmethod
    def pattern(b0: float, hits: list[float]) -> set[int]:
        """Where in the bar the hits are, as 1/32 steps (0-31)."""
        return {int(round((h - b0) * 8)) % 32 for h in hits}

    def bar_kind(self, b0: float, hits: list[float]) -> str:
        lo, hi = self.entries(b0, b0 + 4)
        loudest = float(np.percentile(self.level[lo:hi], 95))
        pat = self.pattern(b0, hits)
        # Share of this bar's hits that sit where the drop's own groove hits (hats,
        # claps), give or take a 1/32 step.
        near = {(g + d) % 32 for g in self.groove for d in (-1, 0, 1)}
        on_groove = len(pat & near) / len(pat) if pat else 0.0
        peak = float(np.percentile(self.level[lo:hi], 99)) if hi > lo else 0.0  # not the next bar's first hit
        if loudest < 0.2 * self.ref_level and peak < 0.35 * self.ref_level and not hits:
            return SILENCE
        if on_groove >= 0.8 and len(hits) <= self.groove_hits + 1:
            return GROOVE
        if len(hits) >= 8:
            return ROLL
        if hits:
            return HITS
        if float(self.hi_env[lo:hi].mean()) >= 0.15 * self.ref_level:  # sound, but nothing hits
            return RISER
        return GROOVE


def drop_fill(bands: Bands, grid: list[GridBeat], drop_beat: int, bpm: float,
              bars: int = FILL_BARS) -> DropFill:
    """Read the last `bars` bars before `drop_beat` and sort each into silence, hits
    (separate stabs), a roll (8+ fast hits), a riser (rising sound with no hits) or
    the normal groove. Hits are measured on the mids and highs, so the kick
    doesn't count; a bar whose hits sit on the drop's own pattern (its hats and
    claps) is the normal groove, with or without the kick."""
    scan = _HitScanner(bands, grid, bpm, drop_beat)
    if not scan.ok:
        return DropFill([])
    kinds, hits_by_bar = [], []
    for k in range(bars, 0, -1):
        b0 = drop_beat - 4 * k
        if b0 < 1:
            continue
        hits = scan.hits(b0, b0 + 4)
        kinds.append(scan.bar_kind(b0, hits))
        hits_by_bar.append((b0, hits))

    fill = DropFill(kinds)
    # The fill is the run of silent, hit or roll bars that ends at the drop (a
    # build's riser before it isn't part of it).
    run = 0
    while run < len(kinds) and kinds[-1 - run] in (SILENCE, HITS, ROLL):
        run += 1
    for (b0, hits), kind in list(zip(hits_by_bar, kinds))[len(kinds) - run:]:
        if kind in (HITS, ROLL):
            fill.hits.extend(hits)
        if kind == ROLL and fill.roll_start is None:
            fill.roll_start = b0
    if fill.hits:
        per_beat: dict[int, int] = {}
        for h in fill.hits:
            per_beat[int(h)] = per_beat.get(int(h), 0) + 1
        fill.per_beat = float(max(per_beat.values()))
    return fill


# --------------------------------------------------------------------------
# Build-ups: how fast the snare roll goes, how loud the riser gets
# --------------------------------------------------------------------------

ROLL_RATES = (1, 2, 4, 8)  # hits per beat: 1/4, 1/8, 1/16 and 1/32 notes
RATE_NAMES = {1: "1/4", 2: "1/8", 4: "1/16", 8: "1/32"}


@dataclass
class BuildCurve:
    """A build-up read from the waveform, beat by beat, for driving effect speed."""
    start: int                  # first beat of the build
    end: int                    # the beat after it (usually the drop)
    rate: list[int]             # each beat: snare hits per beat (0 = no roll, else 1/2/4/8)
    intensity: list[float]      # each quarter beat: the riser's level, 0-1 across the build

    def speed(self, beat: float) -> float:
        """0-1 for an effect speed fader: no roll 0, then 1/4 0.25, 1/8 0.5, 1/16 0.75, 1/32 1."""
        i = int(beat) - self.start
        r = self.rate[i] if 0 <= i < len(self.rate) else 0
        return 0.0 if not r else (ROLL_RATES.index(r) + 1) / len(ROLL_RATES)

    def intensity_at(self, beat: float) -> float:
        i = int(round((beat - self.start) * 4))
        if not self.intensity:
            return 0.0
        return self.intensity[max(0, min(len(self.intensity) - 1, i))]

    def describe(self) -> str:
        """e.g. "roll 1/8 from bar 5, 1/16 from bar 7, 1/32 from bar 8" (bars of the build)."""
        parts, last = [], 0
        for i, r in enumerate(self.rate):
            if r != last and not (r == 0 and i >= len(self.rate) - 2):  # a silent beat before the drop
                parts.append(f"{RATE_NAMES[r]} from bar {i // 4 + 1}" if r else f"stops at bar {i // 4 + 1}")
                last = r
        return "roll " + ", ".join(parts) if parts else "no roll"


def _roll_rate(hits: list[tuple[float, bool]]) -> int:
    """Snare hits per beat (0, 1, 2, 4 or 8) from one beat's hits: (position in the
    beat 0-1, sits on the groove). The fastest grid that's mostly filled wins, but
    only if the positions it adds over the next slower one hold a hit that isn't
    the groove: hats on the off-beat under a 1/4 roll don't make it a 1/8 roll.
    -1: the only hit is on the beat where the groove hits too (a snare on 2 and 4
    where the drop has its clap), so the roll's speed comes from the beats around it."""
    best = 0
    for r in ROLL_RATES:
        tol = 0.01 if r == 8 else 0.13
        slots = [k / r for k in range(r)]
        filled = [any(abs(p - s) <= tol for p, _ in hits) for s in slots]
        if sum(filled) < 0.75 * r:
            break
        new = slots if r == 1 else slots[1::2]  # the positions this speed adds
        if any(any(abs(p - s) <= tol and not g for p, g in hits) for s in new):
            best = r
        elif r == 1:
            best = -1  # beat 1's hit is the groove (a clap): a faster roll can still show
    return best


def build_curve(bands: Bands, grid: list[GridBeat], start: int, end: int, bpm: float) -> Optional[BuildCurve]:
    """Read the build-up from `start` up to `end` (the drop): the snare roll's speed on
    every beat, and the riser's level every quarter beat."""
    scan = _HitScanner(bands, grid, bpm, end)
    if not scan.ok or end - start < 4:
        return None
    rate: list[int] = []
    for b0 in range(start, end, 4):
        b1 = min(b0 + 4, end)
        hits = scan.hits_by_band(b0, b1)
        if b1 - b0 == 4 and scan.bar_kind(b0, scan.hits(b0, b1)) not in (ROLL, HITS):
            rate.extend([0] * 4)  # the groove, a riser or silence: no roll here
            continue
        bar = [(h, scan.is_groove(b0, h, bands_hit)) for h, bands_hit in hits]
        rate.extend(_roll_rate([(h - b, g) for h, g in bar if b <= h < b + 1]) for b in range(b0, b1))
    # A hit that's also where the groove hits takes the speed of the roll around it.
    for i in range(len(rate)):
        if rate[i] == -1:
            near = [rate[j] for j in (i - 1, i + 1) if 0 <= j < len(rate) and rate[j] > 0]
            rate[i] = near[0] if near else 0
    # A roll doesn't skip a beat: a lone missed beat inside a roll takes its neighbours' speed.
    for i in range(1, len(rate) - 1):
        if rate[i] < min(rate[i - 1], rate[i + 1]):
            rate[i] = min(rate[i - 1], rate[i + 1])
    # A lone "roll" beat with no roll around it is a stray hit.
    for i in range(len(rate)):
        if rate[i] and not any(rate[j] for j in (i - 1, i + 1) if 0 <= j < len(rate)):
            rate[i] = 0

    # The riser: mids and highs, a quarter beat at a time, smoothed over a beat and
    # scaled from the quietest to the loudest point of the build.
    steps = [scan.entries(start + q / 4, start + (q + 1) / 4) for q in range((end - start) * 4)]
    raw = np.array([float(scan.hi_env[lo:hi].mean()) if hi > lo else 0.0 for lo, hi in steps])
    padded = np.concatenate([raw[:1].repeat(2), raw, raw[-1:].repeat(1)]) if len(raw) else raw
    smooth = np.convolve(padded, np.ones(4) / 4, mode="valid") if len(raw) else raw
    lo, hi = float(np.percentile(smooth, 5)), float(np.percentile(smooth, 98))
    span = max(hi - lo, 0.15 * scan.ref_level)
    intensity = [round(float(v), 3) for v in np.clip((smooth - lo) / span, 0, 1)]
    return BuildCurve(start, end, rate, intensity)
