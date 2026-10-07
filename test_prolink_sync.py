import time

import pytest

from prolink_sync import (Beat, ClockPLL, SourceSelector, TICKS_PER_BEAT,
                          build_beat_packet, parse_beat)


def test_parse_roundtrip():
    pkt = build_beat_packet(device=2, bpm=128.0, beat_in_bar=3, pitch=1.05)
    beat = parse_beat(pkt, 1.0)
    assert beat.device == 2
    assert beat.name == "CDJ-3000X"
    assert beat.track_bpm == pytest.approx(128.0)
    assert beat.bpm == pytest.approx(134.4, abs=0.01)
    assert beat.beat_in_bar == 3
    assert not beat.is_mixer


def test_parse_rejects_other_packets():
    pkt = bytearray(build_beat_packet(1, 120, 1))
    pkt[0x0A] = 0x0A  # status packet type
    assert parse_beat(bytes(pkt), 0) is None
    assert parse_beat(b"junk", 0) is None


def mixer_beat(t, bar=1, bpm=124.0, device=33):
    return Beat(device, "DJM-V10", bpm, 1.0, bar, t)


def deck_beat(t, device, bar=1, bpm=124.0):
    return Beat(device, "CDJ-3000X", bpm, 1.0, bar, t)


def test_selector_prefers_mixer_then_falls_back():
    sel = SourceSelector("auto", stale_after=2.0)
    assert sel.accept(mixer_beat(0.0))
    assert not sel.accept(deck_beat(0.1, 1))
    # Mixer silent for >2s: a deck takes over, and sticks.
    assert sel.accept(deck_beat(3.0, 1))
    assert not sel.accept(deck_beat(3.1, 2))
    assert sel.accept(deck_beat(3.5, 1))
    # Deck 1 stops; deck 2 takes over after it goes stale.
    assert sel.accept(deck_beat(6.0, 2))


def test_selector_fixed_player():
    sel = SourceSelector("2")
    assert not sel.accept(mixer_beat(0))
    assert not sel.accept(deck_beat(0, 1))
    assert sel.accept(deck_beat(0, 2))


def run_pll(pll, beats, until):
    """Drive the PLL with (time, bpm, bar) beats; return emitted boundary times/bars."""
    boundaries = []
    beats = list(beats)
    while True:
        # Deliver every beat that arrives before the next tick fires.
        while beats and (pll.bpm is None or beats[0][0] <= pll.next_tick):
            t, bpm, bar = beats.pop(0)
            pll.on_beat(bpm, bar, t)
        if pll.next_tick > until:
            return boundaries
        t = pll.next_tick
        boundary, bar = pll.tick()
        if boundary:
            boundaries.append((t, bar))


def beat_train(start, bpm, n, bar=1):
    for i in range(n):
        yield start + i * 60.0 / bpm, bpm, (bar - 1 + i) % 4 + 1


@pytest.mark.parametrize("offset", [0.03, -0.03, 0.2])
def test_pll_locks_to_beats_after_disturbance(monkeypatch, offset):
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    pll = ClockPLL()
    bpm = 126.0
    beat_len = 60.0 / bpm
    # Lock, then the deck's phase jumps by `offset` (DJ nudges the jog wheel).
    beats = list(beat_train(0.0, bpm, 8))
    beats += [(t + offset, b, bar) for t, b, bar in beat_train(8 * beat_len, bpm, 24, bar=1)]
    out = run_pll(pll, beats, until=beats[-1][0])
    # Last several clock boundaries line up with the actual beats and bars.
    actual = {round(t, 6): bar for t, _, bar in beats}
    for t, bar in out[-6:]:
        nearest = min(actual, key=lambda a: abs(a - t))
        assert abs(nearest - t) < 0.002, (t, nearest)
        assert actual[nearest] == bar


def test_pll_follows_tempo_change(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    pll = ClockPLL()
    first = list(beat_train(0.0, 120.0, 8))
    t0 = first[-1][0] + 60.0 / 120.0
    second = list(beat_train(t0, 128.0, 16))
    run_pll(pll, first + second, until=second[-1][0])
    assert pll.period * TICKS_PER_BEAT == pytest.approx(60.0 / 128.0, rel=0.002)
