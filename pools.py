"""Sequence pools: each kind of section picks one of several lighting sequences.

Instead of every drop firing the same look, each section type (drop, build,
breakdown, ...) has a pool of up to 24 sequences. When the section starts,
one sequence is chosen from its pool and sent on that pool's own MIDI channel,
with the sequence number as the note:

    pool        MIDI channel    sequence 1 ... 24
    drop             2          note 36 (C1) ... 59 (B2)
    build            3          note 36 ... 59
    breakdown        4
    pre_drop         5
    intro            6
    outro            7
    verse            8
    bridge           9

Choosing:
    shuffle  random, but every sequence plays once before any repeats, and
             never the same one twice in a row (default)
    random   any sequence each time
    order    1, 2, 3 ... then back to 1

With pairing on, a drop uses the same number as the build that led into it
(build 7 -> drop 7), so builds and drops designed as pairs stay together.

Settings belong to a Lumière project (projects.py); a stand-alone pools.json can
still be loaded with load_config().
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("prolink_sync.pools")

MAX_SEQUENCES = 24
FIRST_NOTE = 36  # sequence 1 = C1, so 24 sequences span C1..B2
POOL_CHANNELS = {"drop": 2, "build": 3, "breakdown": 4, "pre_drop": 5,
                 "intro": 6, "outro": 7, "verse": 8, "bridge": 9, "chorus": 11}  # 10 is the blackout
LABELS = {"drop": "Drop", "build": "Build", "breakdown": "Breakdown", "pre_drop": "Pre-drop",
          "intro": "Intro", "outro": "Outro", "verse": "Verse", "bridge": "Bridge", "chorus": "Chorus"}
MODES = ("shuffle", "random", "order")
CHANGE_EVERY = (0, 4, 8, 16, 32)         # bars
BLACKOUT_MODES = ("off", "fixed", "music")
BLACKOUT_BEATS = (0.5, 1.0, 2.0, 4.0)

DEFAULT_CONFIG = {
    "_help": "How many lighting sequences you made in Lightkey for each section (0-24), and how to pick "
             "one. mode: shuffle | random | order. Set a count to 0 to switch that pool off. "
             "See README.md, 'Sequence pools'.",
    "mode": "shuffle",
    "pair_builds_with_drops": False,
    # Every pool starts empty: set each to the number of sequences you actually made.
    "pools": {name: 0 for name in POOL_CHANNELS},
    # Bars between new looks within one long section (0 = one look per section).
    "change_every": {name: 0 for name in POOL_CHANNELS},
    # Blackout before each drop: off, fixed (beats) or music (the track's own silent gap).
    "blackout": {"mode": "off", "beats": 1.0},
}


def note_for(sequence: int) -> int:
    """MIDI note for a 1-based sequence number."""
    return FIRST_NOTE + sequence - 1


def note_name(note: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[note % 12]}{note // 12 - 2}"


@dataclass
class Pool:
    name: str
    count: int
    mode: str = "shuffle"
    rng: random.Random = field(default_factory=random.Random)
    _bag: list[int] = field(default_factory=list)
    last: Optional[int] = None
    _next_in_order: int = 1

    @property
    def channel(self) -> int:
        """1-based MIDI channel (as Lightkey shows it)."""
        return POOL_CHANNELS[self.name]

    def pick(self, prefer: Optional[int] = None) -> int:
        """Choose the next sequence (1-based). `prefer` is honoured when valid (pairing)."""
        if prefer is not None and 1 <= prefer <= self.count:
            choice = prefer
            if choice in self._bag:
                self._bag.remove(choice)
        elif self.mode == "order":
            choice = self._next_in_order
            self._next_in_order = choice % self.count + 1
        elif self.mode == "random":
            choice = self.rng.randint(1, self.count)
        else:
            if not self._bag:
                self._bag = list(range(1, self.count + 1))
                self.rng.shuffle(self._bag)
                # never the same sequence twice in a row across a reshuffle
                if self.count > 1 and self._bag[-1] == self.last:
                    self._bag[0], self._bag[-1] = self._bag[-1], self._bag[0]
            choice = self._bag.pop()
        self.last = choice
        return choice


class SequencePools:
    def __init__(self, config: Optional[dict] = None, seed: Optional[int] = None):
        cfg = config or DEFAULT_CONFIG
        rng = random.Random(seed)
        default_mode = cfg.get("mode", "shuffle")
        if default_mode not in MODES:
            log.warning("Unknown pool mode %r, using shuffle", default_mode)
            default_mode = "shuffle"
        self.pair = bool(cfg.get("pair_builds_with_drops", False))
        self.pools: dict[str, Pool] = {}
        for name, value in (cfg.get("pools") or {}).items():
            if name not in POOL_CHANNELS:
                log.warning("Ignoring unknown pool %r (known: %s)", name, ", ".join(POOL_CHANNELS))
                continue
            count, mode = (value.get("count", 0), value.get("mode", default_mode)) if isinstance(value, dict) \
                else (value, default_mode)
            count = max(0, min(MAX_SEQUENCES, int(count)))
            if mode not in MODES:
                log.warning("Pool %s: unknown mode %r, using %s", name, mode, default_mode)
                mode = default_mode
            if count:
                self.pools[name] = Pool(name, count, mode, random.Random(rng.random()))
        self._build_seq: Optional[int] = None  # the build that may lead into the next drop

    def choose(self, cue: str) -> Optional[tuple[int, int, int]]:
        """For a section cue, return (channel, note, sequence) to send, or None if it has no pool."""
        if cue == "chorus" and "chorus" not in self.pools:
            cue = "drop"  # no song looks made yet: a chorus borrows the drop looks
        pool = self.pools.get(cue)
        if pool is None:
            return None
        prefer = self._build_seq if (cue == "drop" and self.pair) else None
        seq = pool.pick(prefer)
        if cue == "build":
            self._build_seq = seq
        elif cue in ("drop", "breakdown", "intro", "outro"):
            self._build_seq = None  # pairing only spans build -> drop
        return pool.channel, note_for(seq), seq

    def describe(self) -> str:
        if not self.pools:
            return "no sequence pools"
        parts = [f"{LABELS[p.name]} {p.count} (ch {p.channel})" for p in self.pools.values()]
        return ", ".join(parts) + (" · builds paired with drops" if self.pair else "")


def load_config(path: Path) -> dict:
    """Read pools.json, creating it with the defaults the first time."""
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        log.info("Created %s with every pool at 0; set each to how many sequences you made", path)
        return DEFAULT_CONFIG
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        log.error("Could not read %s (%s); using the defaults", path, e)
        return DEFAULT_CONFIG


def learn(send, pools: SequencePools, only: Optional[str] = None, ask=input, say=print, mark=None) -> None:
    """Step through every sequence so each can be MIDI-learned in Lightkey.

    `send(channel, note)` sends one note; `ask` waits for the user between notes;
    `mark(pool, sequence)`, if given, records each sequence as learned."""
    names = [only] if only else list(pools.pools)
    for name in names:
        pool = pools.pools.get(name)
        if pool is None:
            say(f"No pool called {name!r}. Pools: {', '.join(pools.pools) or 'none'}")
            continue
        say(f"\n{LABELS[name]}: {pool.count} sequences on MIDI channel {pool.channel}")
        for seq in range(1, pool.count + 1):
            note = note_for(seq)
            reply = ask(f"  In Lightkey, start MIDI Learn on {LABELS[name]} sequence {seq}, then press Enter "
                        f"(s = skip this pool, q = quit): ").strip().lower()
            if reply == "q":
                return
            if reply == "s":
                break
            send(pool.channel, note)
            if mark:
                mark(name, seq)
            say(f"    sent {LABELS[name]} {seq}: channel {pool.channel}, note {note} ({note_name(note)})")
    say("\nDone. Every learned sequence will now be picked automatically during the show.")
