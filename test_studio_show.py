"""Lumière Studio step 2: effects, the pre-wired console and Lumière Link's MIDI in."""

import xml.etree.ElementTree as ET

import pytest

import studio
import studio_show as sh

WS = "{http://www.qlcplus.org/Workspace}"


def amber_white_rig():
    """The venue's real lights: amber/white bars (no dimmer) and an RGB par."""
    rig = studio.Rig()
    bar = studio.Profile.from_json({"name": "Warm wash", "kind": "bar",
                                    "channels": [{"role": r, "segment": s} for s in range(1, 9) for r in ("amber", "white")]})
    par = studio.from_template("par7", "Party Par")
    rig.profiles = {bar.name: bar, par.name: par}
    rig.add("Bar", "Warm wash", 1, "Bars", 2)          # 1-16, 17-32
    rig.add("Par", "Party Par", 40, "Pars", 2)         # 40-46, 47-53
    return rig


def test_colours_fall_back_to_what_a_light_can_make():
    assert sh.colour_values("amber", {"amber", "white"}) == {"amber": 255}
    assert sh.colour_values("red", {"amber", "white"}) == {"amber": 255}           # can't: amber instead
    assert sh.colour_values("amber", {"red", "green", "blue"}) == {"red": 255, "green": 100}
    assert sh.colour_values("white", {"red", "green", "blue"}) == {"red": 255, "green": 255, "blue": 255}
    assert sh.palette(amber_white_rig())[:3] == ["amber", "white", "warm"]


def test_chase_lights_one_segment_at_a_time_in_every_group():
    rig = amber_white_rig()
    e = sh.make_effect(rig, "chase:amber")
    assert len(e.steps) == 16                       # 2 bars x 8 segments in the longest group
    bar1, bar2, par1 = (l.id for l in rig.lights[:3])
    step0 = e.steps[0]
    assert step0[bar1][:4] == [255, 0, 0, 0]          # segment 1 amber on, segment 2 off
    assert sum(step0[bar2]) == 0
    assert step0[par1][:4] == [255, 255, 100, 0]      # the pars chase too: dimmer + amber from RGB
    assert e.steps[9][bar2][2:4] == [255, 0]          # step 10: second bar, segment 2
    assert e.duration == sh.EIGHTH


def test_pulse_does_not_dim_a_bar_twice():
    rig = studio.Rig()
    p = studio.from_template("bar", "RGB bar", segments=2)          # master dimmer, strobe, RGB x2... with seg dimmers? no
    rig.profiles = {p.name: p}
    rig.add("B", "RGB bar", 1)
    low = sh.make_effect(rig, "pulse:red").steps[1][0]
    assert low[0] == 255 and low[2] == int(255 * 0.25)   # master full, the colour sets the level


def test_show_build_wires_every_slot_to_lumiere_link_notes():
    rig = amber_white_rig()
    rig.show = {"pools": {"drop": ["chase:amber", "bounce:white"], "intro": ["solid:warm"]}}
    b = sh.build(rig, rig.show)
    assert b.counts["drop"] == 2 and b.counts["intro"] == 2 and b.counts["build"] == 0   # one look -> two slots
    xml = f"<Workspace><Engine>{b.functions}</Engine>{b.console}</Workspace>"
    root = ET.fromstring(xml)
    buttons = {x.get("Caption").split(" · ")[0]: x for x in root.iter("Button")}
    drop1 = buttons["Drop 1"].find("Input")
    assert int(drop1.get("Channel")) == (1 << 12) | (128 + 36) and drop1.get("Universe") == "0"
    assert int(buttons["Drop 2"].find("Input").get("Channel")) == (1 << 12) | (128 + 37)
    assert int(buttons["Intro 2"].find("Input").get("Channel")) == (5 << 12) | (128 + 37)
    assert int(buttons["Hit flash"].find("Input").get("Channel")) == (9 << 12) | (128 + 61)
    assert buttons["Hit flash"].find("Action").text == "Flash"
    assert int(buttons["Blackout"].find("Input").get("Channel")) == (9 << 12) | (128 + 60)
    # intro 1 and 2 are separate copies, so pressing one never switches the other off
    assert b.ids["intro"][0] != b.ids["intro"][1]
    funcs = {f.get("ID"): f for f in root.find("Engine").iter("Function")}
    chase = funcs[str(b.ids["drop"][0])]
    assert chase.get("Type") == "Chaser" and chase.find("Tempo").text == "Beats"
    assert len(chase.findall("Step")) == 16 and all(s.text in funcs for s in chase.findall("Step"))
    solo = root.find(".//SoloFrame")
    assert "Hit flash" not in [x.get("Caption") for x in solo.iter("Button")]


def test_lumiere_inputs_and_beat_from_the_clock():
    io = sh.with_lumiere_inputs('<InputOutputMap>\n  <BeatGenerator BeatType="Disabled" BPM="0"/>\n'
                                '  <Universe Name="Universe 1" ID="0">\n   <Input Plugin="MIDI" UID="x" Line="3"/>\n'
                                '   <Output Plugin="ArtNet" Name="2.0.0.2" UID="2.0.0.2" Line="0"/>\n  </Universe>\n </InputOutputMap>')
    root = ET.fromstring(io)
    assert root.find("BeatGenerator").get("BeatType") == "Plugin" and len(root.findall("BeatGenerator")) == 1
    u0, u1 = root.findall("Universe")
    assert u0.find("Input").get("Name") == "Lumiere Cues" and u0.find("Input/PluginParameters").get("midichannel") == "16"
    assert u0.find("Output").get("Name") == "2.0.0.2" and len(u0.findall("Input")) == 1
    assert u1.get("ID") == "1" and u1.find("Input").get("Name") == "Lumiere Studio Clock"


def test_auto_fill_uses_only_known_effects():
    rig = amber_white_rig()
    show = sh.auto_fill(rig)
    known = {g["key"] for g in sh.gallery(rig)}
    assert all(k in known for keys in show.values() for k in keys)
    assert set(show) == set(sh.POOL_CHANNELS)


def test_send_writes_show_sets_pools_and_rerun_needs_no_confirm(tmp_path):
    from test_studio import FakeLink
    got = {}
    desk = studio.StudioDesk(tmp_path / "d", link=FakeLink(), show_path=tmp_path / "s" / "show.qxw",
                             fixtures_dir=tmp_path / "fx", restart=lambda: True, set_pools=lambda counts, extras: got.update(counts))
    desk.rig = amber_white_rig()
    desk.handle("auto_fill")
    out = desk.handle("apply")
    assert got["drop"] == 4 and got["intro"] == 2 and out["ids"]["hit"]
    text = (tmp_path / "s" / "show.qxw").read_text()
    assert "<VirtualConsole>" in text and 'BeatType="Plugin"' in text
    again = desk.handle("apply")              # Studio's own functions don't count as "made in QLC+"
    assert "confirm" not in again
    st = desk.state()["show"]
    assert st["applied"]["drop"] and st["slots"]["drop"]


def test_set_pool_keeps_only_real_effects(tmp_path):
    from test_studio import FakeLink
    desk = studio.StudioDesk(tmp_path / "d", link=FakeLink(), show_path=tmp_path / "show.qxw",
                             fixtures_dir=tmp_path / "fx", restart=lambda: True)
    desk.rig = amber_white_rig()
    desk.handle("set_pool", pool="drop", keys=["chase:amber", "nonsense:x"])
    assert desk.rig.show["pools"]["drop"] == ["chase:amber"]
    with pytest.raises(ValueError):
        desk.handle("set_pool", pool="nope", keys=[])


def test_engine_sets_pools_to_match_the_show(tmp_path):
    from engine import LumiereEngine
    eng = LumiereEngine(data_dir=tmp_path / "data", projects_dir=tmp_path / "projects", demo=True)
    eng.do_set_pairing(True)
    eng.do_set_pool_count("build", 5)
    eng._update_config(lambda cfg: cfg["pools"].__setitem__("drop", {"count": 1, "mode": "order"}))
    eng.do_set_fills(False)
    eng._studio_pools({"drop": 4, "intro": 2, "build": 0}, {"hit": True, "roll": True, "blackout": True})
    pools = eng.manager.pools
    assert pools.pools["drop"].count == 4 and pools.pools["intro"].count == 2 and "build" not in pools.pools
    assert pools.pools["drop"].mode == "shuffle" and pools.pools["intro"].mode == "shuffle" and pools.pair is False
    cfg = eng.manager.project.config
    assert cfg["fills"] is True and cfg["blackout"]["mode"] == "music"
    eng.do_set_blackout(mode="fixed", beats=2.0)
    eng._studio_pools({"drop": 4}, {"blackout": True})
    assert eng.manager.project.config["blackout"] == {"mode": "fixed", "beats": 2.0}
