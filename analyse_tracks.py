#!/usr/bin/env python3
"""Beta test: analyse every track on a rekordbox USB stick (or in rekordbox's own
library on this Mac) the way Lumière Link does live, without any CDJs.

    .venv/bin/python3 analyse_tracks.py                 # every USB stick, plus rekordbox on this Mac
    .venv/bin/python3 analyse_tracks.py /Volumes/MYUSB  # just this stick or folder

rekordbox keeps each track's analysis in PIONEER/USBANLZ: the beat grid and file
path (.DAT), phrases and colour waveform (.EXT) and the CDJ-3000 waveform
(.2EX). For every track this reads them, finds the build-ups and drops, and
reports the snare roll speed through each build and what comes right before
each drop. It writes everything to lumiere-track-report.csv and prints a
summary, plus a few tracks worth checking by ear.
"""

from __future__ import annotations

import argparse
import csv
import struct
import sys
import time
from pathlib import Path

import waveform as wf
from phrases import BUILD, DROP, parse_pssi


def parse_pqtz(data: bytes) -> list[wf.GridBeat]:
    """The beat grid tag in a .DAT file: 8-byte big-endian entries of beat in bar,
    tempo x100 and time in ms, after a 24-byte header."""
    start = data.find(b"PQTZ")
    if start < 0:
        return []
    len_header, len_tag = struct.unpack_from(">II", data, start + 4)
    count = struct.unpack_from(">I", data, start + 20)[0]
    beats = []
    for i in range(count):
        off = start + len_header + 8 * i
        if off + 8 > start + len_tag:
            break
        bib, tempo, ms = struct.unpack_from(">HHI", data, off)
        beats.append(wf.GridBeat(bib, tempo / 100.0, ms))
    return beats


def parse_ppth(data: bytes) -> str:
    """The audio file's path, from the PPTH tag in a .DAT file (UTF-16)."""
    start = data.find(b"PPTH")
    if start < 0:
        return ""
    len_header = struct.unpack_from(">I", data, start + 4)[0]
    length = struct.unpack_from(">I", data, start + 12)[0]
    raw = data[start + len_header:start + len_header + length]
    return raw.decode("utf-16-be", errors="replace").rstrip("\x00")


def analyse_track(dat: Path) -> dict:
    """Everything Lumière Link would work out for one track."""
    data = dat.read_bytes()
    ext = dat.with_suffix(".EXT").read_bytes() if dat.with_suffix(".EXT").exists() else b""
    two = dat.with_suffix(".2EX").read_bytes() if dat.with_suffix(".2EX").exists() else b""
    name = Path(parse_ppth(data)).name or str(dat.parent)
    grid = parse_pqtz(data)
    bands = wf.parse_waveform(two + ext)
    row = {"track": name, "bpm": round(grid[0].bpm, 1) if grid else "", "phrases": "", "waveform": "",
           "builds": [], "drops": [], "problem": ""}
    if not grid or bands is None:
        row["problem"] = "no beat grid" if not grid else "no waveform"
        return row
    row["waveform"] = bands.kind
    tp = None
    if b"PSSI" in ext:
        try:
            tp = parse_pssi(ext)
            row["phrases"] = "rekordbox"
        except Exception as e:  # noqa: BLE001 - report it, keep going
            row["problem"] = f"phrases unreadable ({e})"
    if tp is None:
        tp = wf.analyse(bands, grid)
        row["phrases"] = "waveform" if tp else "none"
    if tp is None:
        return row
    bpm = grid[0].bpm
    ph = tp.phrases
    for i, p in enumerate(ph):
        end = ph[i + 1].beat if i + 1 < len(ph) else tp.end_beat
        into_drop = i + 1 < len(ph) and ph[i + 1].cue == DROP and p.cue != DROP
        if p.cue == BUILD or into_drop:
            curve = wf.build_curve(bands, grid, p.beat, end, bpm)
            if curve:
                row["builds"].append((p.beat, p.label, curve))
        if into_drop:
            row["drops"].append((end, wf.drop_fill(bands, grid, end, bpm)))
    return row


def find_dats(paths: list[Path]) -> list[Path]:
    found = []
    for root in paths:
        found += sorted(root.rglob("ANLZ*.DAT")) if root.is_dir() else []
    return found


def default_places() -> list[Path]:
    places = [p / "PIONEER" / "USBANLZ" for p in Path("/Volumes").glob("*")] if Path("/Volumes").exists() else []
    places.append(Path.home() / "Library" / "Pioneer" / "rekordbox" / "share" / "PIONEER" / "USBANLZ")
    return [p for p in places if p.is_dir()]


def bar(beat: int, start: int = 1) -> int:
    return (beat - start) // 4 + 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", type=Path, help="USB sticks or folders (default: all found)")
    ap.add_argument("--out", type=Path, default=Path("lumiere-track-report.csv"))
    ap.add_argument("--limit", type=int, default=0, help="stop after this many tracks")
    args = ap.parse_args(argv)
    places = args.paths or default_places()
    if not places:
        print("No rekordbox USB stick or library found. Plug in a stick, or give its path.")
        return 1
    dats = find_dats(places)
    if args.limit:
        dats = dats[:args.limit]
    print(f"Analysing {len(dats)} tracks from {', '.join(str(p) for p in places)} ...")
    started = time.monotonic()
    rows, speeds, fills, problems = [], {}, {}, 0
    with args.out.open("w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["track", "bpm", "sections from", "waveform", "what", "bar", "result"])
        for n, dat in enumerate(dats, 1):
            try:
                row = analyse_track(dat)
            except Exception as e:  # noqa: BLE001 - one broken file shouldn't stop the run
                row = {"track": str(dat), "bpm": "", "phrases": "", "waveform": "", "builds": [], "drops": [],
                       "problem": f"crashed: {e}"}
            rows.append(row)
            base = [row["track"], row["bpm"], row["phrases"], row["waveform"]]
            if row["problem"]:
                problems += 1
                out.writerow(base + ["problem", "", row["problem"]])
            for start, label, curve in row["builds"]:
                top = max(curve.rate)
                speeds[top] = speeds.get(top, 0) + 1
                out.writerow(base + [f"build ({label})", bar(start), curve.describe()])
            for drop, fill in row["drops"]:
                fills[fill.kind] = fills.get(fill.kind, 0) + 1
                out.writerow(base + ["before drop", bar(drop), fill.describe()])
            if n % 25 == 0:
                print(f"  {n} tracks ...")
    took = time.monotonic() - started

    builds = sum(speeds.values())
    print(f"\nDone: {len(rows)} tracks in {took:.0f} s ({took / max(1, len(rows)) * 1000:.0f} ms each).")
    print(f"Sections from rekordbox phrases: {sum(r['phrases'] == 'rekordbox' for r in rows)}, "
          f"from the waveform: {sum(r['phrases'] == 'waveform' for r in rows)}, "
          f"problems: {problems}")
    print(f"\nBuild-ups: {builds}")
    for r in (0, *wf.ROLL_RATES):
        if speeds.get(r):
            print(f"  fastest roll {wf.RATE_NAMES.get(r, 'none'):>4}: {speeds[r]:4d}  ({speeds[r] / builds:.0%})")
    print(f"\nRight before the drop: {sum(fills.values())} drops")
    for k, v in sorted(fills.items(), key=lambda kv: -kv[1]):
        print(f"  {k:8s} {v:4d}")
    picks = [(r, b) for r in rows for b in r["builds"] if max(b[2].rate) >= 4][:5]
    picks += [(r, d) for r in rows for d in r["drops"] if d[1].kind in (wf.HITS, wf.SILENCE)][:5]
    if picks:
        print("\nWorth checking by ear (does it match what you hear?):")
        for r, item in picks:
            if isinstance(item[1], wf.DropFill):
                print(f"  {r['track']}: before the drop at bar {bar(item[0])}: {item[1].describe()}")
            else:
                print(f"  {r['track']}: build at bar {bar(item[0])}: {item[2].describe()}")
    print(f"\nFull report: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
