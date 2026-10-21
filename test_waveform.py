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
