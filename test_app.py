import json
import time
import urllib.error
import urllib.request

import pytest

import phrases as ph


class Port:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def close(self):
        pass


@pytest.fixture
def app(tmp_path, monkeypatch):
    import mido
    ports = {}
    monkeypatch.setattr(mido, "open_output", lambda name, virtual=False: ports.setdefault(name, Port()))
    import simrig
    monkeypatch.setattr(simrig.SimRig, "__init__", _rig_any_port(simrig.SimRig.__init__))
    from appserver import AppServer
    from engine import LumiereEngine
    eng = LumiereEngine(data_dir=tmp_path / "data", projects_dir=tmp_path / "projects", demo=True,
                        beat_bind="127.0.0.1")
    try:
        eng.start()
    except OSError as e:
        pytest.skip(f"cannot bind DJ Link ports here: {e}")
    eng.vcdj.startup_wait, eng.vcdj.claim_interval = 0.05, 0.01
    eng.vcdj.db_query_port = eng.rig.db.query.getsockname()[1]
    srv = AppServer(eng)
    srv.start()
    yield eng, srv, ports, tmp_path
    srv.stop()
    eng.stop()


def _rig_any_port(init):
    def wrapped(self, *a, **kw):
        kw.setdefault("query_port", 0)
        init(self, *a, **kw)
    return wrapped


def call(srv, path, body=None, token=True):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Token": srv.token if token else "wrong",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_api_needs_the_token(app):
    eng, srv, ports, _ = app
    assert call(srv, "/api/state", token=False)[0] == 403
    assert call(srv, "/api/action", {"name": "set_live", "args": {"on": False}}, token=False)[0] == 403
    assert eng.live is True
    status, page = 200, urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/").read()
    assert b"Lumi" in page and b"googleapis" not in page  # fonts are bundled, works offline
    assert urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/fonts/Manrope.ttf").status == 200
    assert call(srv, "/assets/../engine.py")[0] == 404  # no wandering outside the asset folders


def test_state_shows_the_demo_booth(app):
    eng, srv, ports, _ = app
    assert wait(lambda: len([d for d in call(srv, "/api/state")[1]["decks"] if d["source"]]) == 2)
    st = call(srv, "/api/state")[1]
    titles = {d["n"]: (d["title"], d["source"]) for d in st["decks"]}
    assert titles == {1: ("Low Tide (Extended Mix)", "waveform"), 2: ("Rush Hour", "rekordbox")}
    assert st["project"]["name"] == "Default" and st["ports"]["ok"]
    key = next(d["key"] for d in st["decks"] if d["n"] == 2)
    status, wave = call(srv, f"/api/waveform?key={key}")
    assert status == 200 and len(wave["low"]) > 1000 and len(wave["grid_ms"]) > 100


def test_actions_change_and_save_the_project(app):
    eng, srv, ports, tmp = app
    assert call(srv, "/api/action", {"name": "set_pool_count", "args": {"pool": "drop", "count": 30}})[1]["ok"]
    assert json.loads((tmp / "projects" / "Default.lumiere").read_text())["pools"]["drop"] == 24  # capped
    call(srv, "/api/action", {"name": "send_sequence", "args": {"pool": "drop", "seq": 3, "learn": True}})
    assert [(m.channel + 1, m.note) for m in ports["Lumiere Cues"].sent if m.type == "note_on"][-1] == (2, 38)
    assert call(srv, "/api/state")[1]["pools"]["pools"]["drop"]["learned"] == [3]
    status, body = call(srv, "/api/action", {"name": "new_project", "args": {"name": "Club Night"}})
    assert status == 200 and "same name as your Lightkey project" in body["result"]["tip"]
    assert call(srv, "/api/action", {"name": "new_project", "args": {"name": "club night"}})[0] == 400
    assert call(srv, "/api/action", {"name": "nonsense"})[0] == 400


def test_paused_sends_no_automatic_cues_but_pads_still_work(app):
    eng, srv, ports, _ = app
    call(srv, "/api/action", {"name": "set_live", "args": {"on": False}})
    before = len(ports["Lumiere Cues"].sent)
    eng._emit(ph.DROP)
    assert len(ports["Lumiere Cues"].sent) == before
    call(srv, "/api/action", {"name": "fire", "args": {"cue": "drop"}})
    assert len(ports["Lumiere Cues"].sent) > before
    settings = json.loads((eng.settings_path).read_text())
    assert settings["demo"] is True
