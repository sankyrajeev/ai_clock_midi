import json
from collections import Counter

import pytest

import phrases as ph
import pools as pl


def make(**cfg):
    base = {"mode": "shuffle", "pair_builds_with_drops": True, "pools": {"drop": 24, "build": 24}}
    base.update(cfg)
    return pl.SequencePools(base, seed=3)


def test_shuffle_plays_every_sequence_before_repeating_and_never_twice_in_a_row():
    pools = make(pair_builds_with_drops=False)
    picks = [pools.choose("drop")[2] for _ in range(24 * 6)]
    for start in range(0, len(picks), 24):
        assert sorted(picks[start:start + 24]) == list(range(1, 25))
    assert all(a != b for a, b in zip(picks, picks[1:]))


def test_channels_and_notes():
    pools = make(pair_builds_with_drops=False)
    ch, note, seq = pools.choose("drop")
    assert ch == 2 and note == 35 + seq
    ch, note, seq = pools.choose("build")
    assert ch == 3
    assert pl.note_for(1) == 36 and pl.note_for(24) == 59
    assert pl.note_name(36) == "C1" and pl.note_name(59) == "B2"
    assert pools.choose("bar") is None  # bars and phrase markers have no pool


def test_build_and_drop_are_paired():
    pools = make()
    for _ in range(30):
        build = pools.choose("build")[2]
        assert pools.choose("drop")[2] == build


def test_pairing_off_or_drop_without_build():
    pools = make()
    pools.choose("build")
    pools.choose("breakdown")  # no pool here, but a breakdown still ends the pairing
    pools.choose("drop")
    pools2 = make(pair_builds_with_drops=False)
    same = sum(pools2.choose("build")[2] == pools2.choose("drop")[2] for _ in range(200))
    assert same < 40  # random agreement only


def test_pairing_with_a_smaller_drop_pool_falls_back_to_shuffle():
    pools = make(pools={"drop": 8, "build": 24})
    for _ in range(50):
        pools.choose("build")
        assert 1 <= pools.choose("drop")[2] <= 8


def test_order_and_random_modes():
    pools = make(mode="order", pair_builds_with_drops=False, pools={"drop": 3})
    assert [pools.choose("drop")[2] for _ in range(7)] == [1, 2, 3, 1, 2, 3, 1]
    pools = make(mode="random", pair_builds_with_drops=False, pools={"drop": 24})
    seen = Counter(pools.choose("drop")[2] for _ in range(2000))
    assert set(seen) == set(range(1, 25))


def test_config_limits_and_per_pool_settings():
    pools = make(pools={"drop": 99, "build": 0, "intro": {"count": 4, "mode": "order"}, "nonsense": 5})
    assert pools.pools["drop"].count == 24  # 24 max
    assert "build" not in pools.pools  # 0 switches a pool off
    assert pools.pools["intro"].mode == "order" and pools.pools["intro"].count == 4
    assert "nonsense" not in pools.pools


def test_config_file_is_created_with_defaults(tmp_path):
    path = tmp_path / "pools.json"
    cfg = pl.load_config(path)
    assert path.exists() and json.loads(path.read_text())["pools"]["drop"] == 24
    assert cfg["mode"] == "shuffle" and cfg["pair_builds_with_drops"] is True
    path.write_text("{ not json")
    assert pl.load_config(path)["mode"] == "shuffle"  # falls back instead of crashing


def test_cue_midi_sends_the_section_note_and_the_pool_sequence(monkeypatch):
    import mido
    sent = []

    class Port:
        def send(self, m):
            sent.append(m)

        def close(self):
            pass

    monkeypatch.setattr(mido, "open_output", lambda name, virtual=False: Port())
    midi = ph.CueMidi("Lumiere Cues", False, pools=make(pair_builds_with_drops=False))
    midi.event(ph.DROP)
    ons = [(m.channel + 1, m.note) for m in sent if m.type == "note_on"]
    assert ons[0] == (1, ph.NOTES[ph.DROP])  # the plain section cue on channel 1
    assert ons[1][0] == 2 and 36 <= ons[1][1] <= 59  # plus one drop sequence on channel 2
    assert midi.last_pick.startswith("drop ") and midi.last_pick.endswith("/24")
    midi.event(ph.BAR)
    assert [(m.channel + 1) for m in sent if m.type == "note_on"][-1] == 1  # bars: no pool


def test_learn_helper_steps_through_each_sequence():
    pools = make(pools={"drop": 3, "build": 2})
    sent, lines = [], []
    answers = iter(["", "", "", "", "s"])
    pl.learn(lambda ch, note: sent.append((ch, note)), pools, ask=lambda q: next(answers), say=lines.append)
    assert sent == [(2, 36), (2, 37), (2, 38), (3, 36)]
    assert any("Drop: 3 sequences on MIDI channel 2" in l for l in lines)
    sent.clear()
    pl.learn(lambda ch, note: sent.append((ch, note)), pools, only="build", ask=lambda q: "", say=lines.append)
    assert sent == [(3, 36), (3, 37)]
