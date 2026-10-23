import json

import pools as pl
import projects as pr
from lightkey import NOT_RUNNING, OPEN, PERMISSION, LightkeyState


def store(tmp_path):
    return pr.ProjectStore(tmp_path / "Lumiere Link")


def test_save_load_roundtrip_and_file_format(tmp_path):
    s = store(tmp_path)
    p = pr.Project("Club Night", {"mode": "order", "pair_builds_with_drops": False, "pools": {"drop": 8}},
                   {"drop": [3, 1, 3]}, "Club Night", "/Shows/Club Night.lightkey")
    path = s.save(p)
    assert path.name == "Club Night.lumiere"
    data = json.loads(path.read_text())
    assert data["pools"] == {"drop": 8} and data["learned"] == {"drop": [1, 3]}
    back = s.load("club night")  # names compare loosely
    assert back.config["mode"] == "order" and back.lightkey_path == "/Shows/Club Night.lightkey"
    assert not list(s.dir.glob(".saving-*"))  # no temp files left behind


def test_find_for_prefers_path_then_name(tmp_path):
    s = store(tmp_path)
    s.save(pr.Project("Main Room", lightkey_name="Old Name", lightkey_path="/Shows/Main.lightkey"))
    s.save(pr.Project("Wedding"))
    assert s.find_for("Renamed in Lightkey", "/Shows/Main.lightkey").name == "Main Room"
    assert s.find_for("wedding — Edited").name == "Wedding"
    assert s.find_for("Old Name").name == "Main Room"
    assert s.find_for("Nothing like it") is None


def test_unsafe_names_become_safe_files(tmp_path):
    s = store(tmp_path)
    path = s.save(pr.Project("Fri 12/05: Main"))
    assert path.name == "Fri 12-05- Main.lumiere" and s.load("Fri 12/05: Main") is not None


def test_default_project_takes_over_an_old_pools_json(tmp_path):
    legacy = tmp_path / "pools.json"
    legacy.write_text(json.dumps({"mode": "random", "pools": {"drop": 10, "build": 0}}))
    s = store(tmp_path)
    d = s.ensure_default(legacy)
    assert d.name == "Default" and d.config["mode"] == "random" and d.config["pools"]["drop"] == 10
    assert s.ensure_default(legacy).name == "Default" and s.names() == ["Default"]


def manager(tmp_path, switches=None):
    s = store(tmp_path)
    base = s.ensure_default()
    base.config["pools"] = {"drop": 6, "build": 6}
    base.learned = {"drop": [1, 2]}
    s.save(base)
    return pr.ProjectManager(s, base, on_switch=(lambda p, pools: switches.append((p.name, pools))) if switches is not None else None)


def test_lightkey_project_creates_a_matching_lumiere_project(tmp_path):
    switches = []
    m = manager(tmp_path, switches)
    m.on_lightkey(LightkeyState(OPEN, "Club Night", "/Shows/Club Night.lightkey"))
    assert m.project.name == "Club Night"
    # starts empty: another show's counts and ✓ marks don't belong to this one
    assert set(m.project.config["pools"].values()) == {0}
    assert m.project.learned == {}
    assert m.project.config["pair_builds_with_drops"] is False
    saved = m.store.load("Club Night")
    assert saved and saved.lightkey_path == "/Shows/Club Night.lightkey"
    assert switches and switches[-1][0] == "Club Night"


def test_switching_between_lightkey_projects_swaps_pools(tmp_path):
    switches = []
    m = manager(tmp_path, switches)
    m.store.save(pr.Project("Wedding", {"mode": "shuffle", "pair_builds_with_drops": True, "pools": {"drop": 3}}))
    m.on_lightkey(LightkeyState(OPEN, "Wedding"))
    assert m.pools.pools["drop"].count == 3 and "build" not in m.pools.pools
    picks = {m.pools.choose("drop")[2] for _ in range(30)}
    assert picks == {1, 2, 3}  # only the wedding's three drop sequences
    m.on_lightkey(LightkeyState(OPEN, "Wedding"))  # same project again: shuffle carries on
    assert len(switches) == 1


def test_permission_or_lightkey_closed_keeps_current_project(tmp_path):
    m = manager(tmp_path)
    m.on_lightkey(LightkeyState(PERMISSION, detail="-1743"))
    assert m.project.name == "Default" and m.lightkey_note
    m.on_lightkey(LightkeyState(NOT_RUNNING))
    assert m.project.name == "Default"


def test_learn_marks_sequences_in_the_project(tmp_path):
    m = manager(tmp_path)
    answers = iter(["", "", "q"])
    pl.learn(lambda ch, n: None, m.pools, only="build", ask=lambda q: next(answers), say=lambda *_: None,
             mark=m.mark_learned)
    assert m.store.load("Default").learned["build"] == [1, 2]


def test_one_off_settings_file_is_not_saved(tmp_path):
    m = manager(tmp_path)
    m.project = pr.Project("scratch")
    m.persist = False
    m.mark_learned("drop", 5)
    assert m.store.load("scratch") is None


def test_cue_midi_uses_the_new_pools_after_a_switch(tmp_path, monkeypatch):
    import mido
    import phrases as ph
    sent = []

    class Port:
        def send(self, msg):
            sent.append(msg)

        def close(self):
            pass

    monkeypatch.setattr(mido, "open_output", lambda name, virtual=False: Port())
    m = manager(tmp_path)
    midi = ph.CueMidi("Lumiere Cues", False, pools=m.pools)
    m.on_switch = lambda p, pools: midi.set_pools(pools)
    m.store.save(pr.Project("Tiny", {"mode": "order", "pair_builds_with_drops": False, "pools": {"drop": 1}}))
    m.on_lightkey(LightkeyState(OPEN, "Tiny"))
    midi.event(ph.DROP)
    pool_notes = [(msg.channel + 1, msg.note) for msg in sent if msg.type == "note_on" and msg.channel == 1]
    assert pool_notes == [(2, 36)]  # the only drop sequence in 'Tiny'
