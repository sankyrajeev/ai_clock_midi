import socket
import time

import numpy as np
import pytest

import phrases as ph
import waveform as wf
from phrases import BREAKDOWN, BUILD, DROP, INTRO, OUTRO, VERSE, PhraseTracker, build_status_packet, parse_status
from test_phrases import MAC, FakeDbServer, loopback_ifaces

BPM = 128.0
EPS = wf.ENTRIES_PER_SECOND


def synth(sections, bpm=BPM, seed=1, noise=0.1):
    """Band arrays at 150/s for [(bars, kind)], plus a matching beat grid."""
    rng = np.random.default_rng(seed)
    per_bar = int(round(4 * 60.0 / bpm * EPS))
    low, mid, high = [], [], []
    for bars, kind in sections:
        n = bars * per_bar
        ramp = np.linspace(0, 1, n)
        lo, mi, hi = {
            "intro":     (120, 60, 40),
            "drop":      (200, 120, 120),
            "breakdown": (15, 70, 30),
            "build":     (20, 60, 40 + 140 * ramp),
            "verse":     (130, 90, 60),
            "outro":     (120, 60, 40),
        }[kind]
        for arr, v in ((low, lo), (mid, mi), (high, hi)):
            base = np.broadcast_to(np.asarray(v, dtype=np.float32), (n,)).copy()
            arr.append(base * (1 + noise * rng.standard_normal(n)))
    total_bars = sum(b for b, _ in sections)
    grid = wf.parse_beat_grid(wf.build_beat_grid(bpm, total_bars * 4))
    return wf.Bands(np.concatenate(low), np.concatenate(mid), np.concatenate(high), "PWV7"), grid


EDM = [(16, "intro"), (8, "build"), (32, "drop"), (16, "breakdown"), (8, "build"), (32, "drop"), (16, "outro")]


def bar_of(beat):
    return (beat - 1) // 4


def test_edm_track_structure():
    bands, grid = synth(EDM)
    tp = wf.analyse(bands, grid)
    assert tp.source == "waveform"
    got = [(bar_of(p.beat), p.cue) for p in tp.phrases]
    assert got == [(0, INTRO), (16, BUILD), (24, DROP), (56, BREAKDOWN), (72, BUILD), (80, DROP), (112, OUTRO)]


def test_breakdown_straight_into_drop_without_riser():
    bands, grid = synth([(16, "intro"), (32, "drop"), (16, "breakdown"), (32, "drop"), (8, "outro")])
    got = [(bar_of(p.beat), p.cue) for p in wf.analyse(bands, grid).phrases]
    assert got == [(0, INTRO), (16, DROP), (48, BREAKDOWN), (64, DROP), (96, OUTRO)]


def test_short_fill_does_not_become_a_section():
    sections = [(16, "intro"), (16, "drop"), (1, "breakdown"), (15, "drop"), (8, "outro")]
    bands, grid = synth(sections)
    cues = [p.cue for p in wf.analyse(bands, grid).phrases]
    assert cues == [INTRO, DROP, OUTRO]


def test_vocal_verse_between_drops():
    bands, grid = synth([(8, "intro"), (16, "drop"), (16, "verse"), (8, "build"), (16, "drop"), (8, "outro")])
    got = [(bar_of(p.beat), p.cue) for p in wf.analyse(bands, grid).phrases]
    assert got == [(0, INTRO), (8, DROP), (24, VERSE), (40, BUILD), (48, DROP), (64, OUTRO)]


def test_noisy_and_offset_grid_still_lines_up_on_bars():
    """First downbeat is late in the file (silence + pickup), and the audio is noisy."""
    bands, _ = synth(EDM, noise=0.25, seed=7)
    pad = int(0.75 * EPS)
    bands = wf.Bands(*(np.concatenate([np.zeros(pad, np.float32), a]) for a in (bands.low, bands.mid, bands.high)), "PWV7")
    grid = wf.parse_beat_grid(wf.build_beat_grid(BPM, 128 * 4, first_ms=750))
    tp = wf.analyse(bands, grid)
    assert [p.cue for p in tp.phrases] == [INTRO, BUILD, DROP, BREAKDOWN, BUILD, DROP, OUTRO]
    assert all((p.beat - 1) % 4 == 0 for p in tp.phrases)  # every section starts on a downbeat


def test_waveform_tag_formats():
    low = np.array([200, 10], np.float32)
    mid = np.array([50, 50], np.float32)
    high = np.array([20, 180], np.float32)
    b7 = wf.parse_waveform(b"\x00\x00\x00\x10" + wf.build_waveform_tag(low, mid, high))
    assert b7.kind == "PWV7" and list(b7.low) == [200, 10] and list(b7.high) == [20, 180]

    # PWV5: red=7 (bass), height 31 vs blue=7 (highs), height 31
    def pwv5(entries):
        body = b"".join(int(e).to_bytes(2, "big") for e in entries)
        return b"PWV5" + (0x18).to_bytes(4, "big") + (0x18 + len(body)).to_bytes(4, "big") + \
            (2).to_bytes(4, "big") + len(entries).to_bytes(4, "big") + bytes(4) + body
    red = (7 << 13) | (31 << 2)
    blue = (7 << 7) | (31 << 2)
    b5 = wf.parse_waveform(pwv5([red, blue]))
    assert b5.kind == "PWV5" and b5.low[0] == 31 and b5.high[0] == 0 and b5.high[1] == 31 and b5.low[1] == 0


def test_beat_grid_roundtrip():
    grid = wf.parse_beat_grid(wf.build_beat_grid(126.0, 9, first_ms=120, first_bib=3))
    assert [g.beat_in_bar for g in grid] == [3, 4, 1, 2, 3, 4, 1, 2, 3]
    assert grid[0].time_ms == 120 and grid[4].time_ms == round(120 + 4 * 60000 / 126)
    assert grid[0].bpm == 126.0


def test_virtual_cdj_falls_back_to_waveform_when_no_phrases():
    """Track without phrase analysis: the player only has a beat grid and waveform."""
    bands, grid = synth(EDM)
    tag = wf.build_waveform_tag(bands.low, bands.mid, bands.high)
    grid_blob = wf.build_beat_grid(BPM, 128 * 4)
    srv = FakeDbServer({(3, 555, "PWV7"): tag}, grids={(3, 555): grid_blob})
    events = []
    tracker = PhraseTracker(events.append, lambda cc, v: None)
    try:
        vcdj = ph.VirtualCdj(tracker, bind="127.0.0.1", broadcast="127.0.0.1")
    except OSError as e:
        pytest.skip(f"cannot bind DJ Link ports here: {e}")
    vcdj.startup_wait, vcdj.claim_interval, vcdj.db_query_port = 0.1, 0.01, srv.query_port
    vcdj.list_ifaces = loopback_ifaces
    vcdj.start()
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ip = socket.inet_aton("127.0.0.1")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and vcdj.number == 0:
            out.sendto(ph.keepalive_packet("CDJ-3000X", 1, MAC, ip, 1), ("127.0.0.1", ph.ANNOUNCE_PORT))
            time.sleep(0.05)
        while time.monotonic() < deadline and not tracker.has_phrases():
            out.sendto(build_status_packet(1, 1, 3, 555, 97, 1, master=True), ("127.0.0.1", ph.STATUS_PORT))
            time.sleep(0.05)
        assert tracker.has_phrases()
        assert "(waveform)" in tracker.current()
        assert DROP in events  # beat 97 = bar 24, the first drop
        kinds = [k for k, _ in srv.requests]
        assert 0x2204 in kinds  # asked for the beat grid
    finally:
        vcdj.close()
        out.close()


# --- what happens right before a drop --------------------------------------

def fill_track(last_bars, bpm=128.0, beats=96, drop=65, seed=3):
    """A groove (kick on every beat, claps on 2 and 4, off-beat hats) with a drop at
    `drop`; `last_bars` maps a bar's first beat to what plays there instead."""
    rng = np.random.default_rng(seed)
    n = int((beats + 1) * 60 / bpm * wf.ENTRIES_PER_SECOND)
    low, mid, high = (rng.uniform(0, 3, n) for _ in range(3))

    def hit(band, beat, amp, decay=0.6):
        i = int((beat - 1) * 60 / bpm * wf.ENTRIES_PER_SECOND)
        for k in range(12):
            if i + k < n:
                band[i + k] = max(band[i + k], amp * decay ** k)

    replaced = {b + j for b in last_bars for j in range(4)}
    beat_len = 60 / bpm * wf.ENTRIES_PER_SECOND
    for b in range(1, beats + 1):  # bass and pads under everything except a silent bar
        if b not in replaced or last_bars[max(k for k in last_bars if k <= b)] != "silence":
            i = int((b - 1) * beat_len)
            low[i:i + int(beat_len) + 1] += 30
            mid[i:i + int(beat_len) + 1] += 25
    for b in range(1, beats + 1):
        if b in replaced:
            continue
        loud = 1.0 if b >= drop else 0.8
        hit(low, b, 220 * loud)
        hit(high, b + 0.5, 120 * loud)
        if (b - 1) % 4 in (1, 3):
            hit(mid, b, 160 * loud)
    for b0, what in last_bars.items():
        if what == "stabs":
            for j in range(4):
                hit(mid, b0 + j, 200, decay=0.75)
        elif what in ("roll16", "roll8"):
            step = 0.25 if what == "roll16" else 0.5
            for j in range(int(4 / step)):
                hit(mid, b0 + j * step, 150)
                hit(high, b0 + j * step, 90)
            for j in range(4):
                hit(low, b0 + j, 200)  # the kick keeps going under the roll
        elif what == "riser":
            i0 = int((b0 - 1) * 60 / bpm * wf.ENTRIES_PER_SECOND)
            i1 = int((b0 + 3) * 60 / bpm * wf.ENTRIES_PER_SECOND)
            high[i0:i1] = np.linspace(40, 140, i1 - i0) + rng.uniform(0, 4, i1 - i0)
    bands = wf.Bands(low=low, mid=mid, high=high, kind="PWV7")
    grid = wf.parse_beat_grid(wf.build_beat_grid(bpm, beats + 1))
    return wf.drop_fill(bands, grid, drop, bpm)


def test_normal_groove_before_the_drop_is_left_alone():
    fill = fill_track({})
    assert fill.kind == wf.GROOVE and fill.hits == [] and fill.describe() == "no fill"


def test_silent_bar_before_the_drop():
    assert fill_track({61: "silence"}).kind == wf.SILENCE


def test_four_synth_stabs_land_on_their_beats():
    fill = fill_track({61: "stabs"})
    assert fill.kind == wf.HITS
    assert fill.hits == [61.0, 62.0, 63.0, 64.0]
    assert fill.describe() == "4 hits"


def test_snare_roll_with_the_kick_still_in():
    fill = fill_track({61: "roll16"})
    assert fill.kind == wf.ROLL and fill.roll_start == 61
    assert len(fill.hits) == 16 and fill.hits[:3] == [61.0, 61.25, 61.5]
    assert fill.per_beat == 4


def test_roll_that_speeds_up_over_two_bars():
    fill = fill_track({57: "roll8", 61: "roll16"})
    assert fill.bars[-2:] == [wf.ROLL, wf.ROLL]
    assert fill.roll_start == 57 and len(fill.hits) == 8 + 16


def test_riser_without_hits():
    assert fill_track({61: "riser"}).kind == wf.RISER


def test_stabs_after_a_silent_bar_make_one_fill():
    fill = fill_track({57: "silence", 61: "stabs"})
    assert fill.bars[-2:] == [wf.SILENCE, wf.HITS] and fill.hits == [61.0, 62.0, 63.0, 64.0]
