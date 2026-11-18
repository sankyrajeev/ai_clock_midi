"""Lumière Studio: light profiles, the patch, and the QLC+ files it writes."""

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import studio
import studio as st

XSD = Path(__file__).parent / "tests_data" / "qlcplus-fixture.xsd"
FX = "{http://www.qlcplus.org/FixtureDefinition}"


def rig_with_bars_and_pars():
    rig = st.Rig()
    for p in (st.from_template("par7", "Club par"), st.from_template("bar", "Pixel bar", segments=8),
              st.from_template("strobe2", "Strobe")):
        rig.profiles[p.name] = p
    rig.add("Par", "Club par", 1, "Pars", count=4)          # 1-28
    rig.add("Bar", "Pixel bar", None, "Bars", count=2)      # next free: 29, then 55
    rig.add("Strobe", "Strobe", 101, "Strobes")
    return rig


def test_templates_have_the_usual_channel_counts():
    assert st.from_template("rgb3", "").count == 3
    assert st.from_template("par7", "").count == 7
    assert st.from_template("par8", "").count == 8
    bar = st.from_template("bar", "Bar", segments=12, white=True)
    assert bar.count == 2 + 12 * 4 and bar.segments == 12
    assert [c.label for c in bar.channels[:4]] == ["Dimmer", "Strobe", "Red 1", "Green 1"]
    for key in st.TEMPLATES:
        assert st.from_template(key, "x").check() == []


@pytest.mark.skipif(shutil.which("xmllint") is None, reason="needs xmllint")
def test_fixture_definitions_pass_qlcplus_own_schema(tmp_path):
    for key in st.TEMPLATES:
        p = st.from_template(key, f"Test {key}", segments=4)
        f = tmp_path / f"{p.file_stem}.qxf"
        f.write_text(p.to_qxf())
        r = subprocess.run(["xmllint", "--noout", "--schema", str(XSD), str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_bar_segments_become_qlcplus_heads():
    p = st.from_template("bar", "Pixel bar", segments=3)
    root = ET.fromstring(p.to_qxf().split("\n", 2)[2])
    mode = root.find(f"{FX}Mode")
    heads = mode.findall(f"{FX}Head")
    assert len(heads) == 3
    assert [int(c.text) for c in heads[0]] == [2, 3, 4]       # red, green, blue of segment 1
    assert len(mode.findall(f"{FX}Channel")) == 2 + 3 * 3


def test_patching_finds_free_addresses_and_refuses_overlaps():
    rig = rig_with_bars_and_pars()
    addrs = {l.name: l.address for l in rig.lights}
    assert addrs == {"Par 1": 1, "Par 2": 8, "Par 3": 15, "Par 4": 22, "Bar 1": 29, "Bar 2": 55, "Strobe": 101}
    with pytest.raises(ValueError, match="already used by Par 2"):
        rig.add("Clash", "Strobe", 10, "Strobes")
    with pytest.raises(ValueError, match="doesn't fit"):
        rig.add("Late", "Pixel bar", 500, "Bars")
    assert rig.next_free(7) == 81


def test_show_patches_every_light_and_keeps_the_machines_io(tmp_path):
    rig = rig_with_bars_and_pars()
    show = tmp_path / "show.qxw"
    old_io = '<InputOutputMap><Universe Name="Universe 1" ID="0"><Output Plugin="ArtNet" Line="1"/></Universe></InputOutputMap>'
    show.write_text(f'<?xml version="1.0"?><Workspace><Engine>{old_io}</Engine></Workspace>')
    result = st.write_show(rig, show, tmp_path / "fixtures")
    assert result["kept_io"] and result["backup"] and Path(result["backup"]).exists()
    assert sorted(result["fixtures"]) == ["Lumiere-Club-par.qxf", "Lumiere-Pixel-bar.qxf", "Lumiere-Strobe.qxf"]
    root = ET.fromstring(show.read_text().split("\n", 2)[2])
    ns = "{http://www.qlcplus.org/Workspace}"
    engine = root.find(f"{ns}Engine")
    assert engine.find("InputOutputMap") is not None or engine.find(f"{ns}InputOutputMap") is not None
    fixtures = engine.findall(f"{ns}Fixture")
    assert len(fixtures) == 7
    bar = next(f for f in fixtures if f.find(f"{ns}Name").text == "Bar 1")
    assert bar.find(f"{ns}Address").text == "28" and bar.find(f"{ns}Channels").text == "26"   # QLC+ counts from 0
    groups = {g.find(f"{ns}Name").text: g for g in engine.findall(f"{ns}FixtureGroup")}
    assert set(groups) == {"Pars", "Bars", "Strobes"}
    assert groups["Bars"].find(f"{ns}Size").get("X") == "16"       # 2 bars x 8 segments in a row


def test_test_looks_set_each_role():
    rig = rig_with_bars_and_pars()
    vals = rig.channels_for({"dimmer": 255, "red": 255})
    par = rig.lights[0]
    assert [vals[par.address + i] for i in range(7)] == [255, 255, 0, 0, 0, 0, 0]
    bar = next(l for l in rig.lights if l.name == "Bar 1")
    assert vals[bar.address] == 255 and vals[bar.address + 2] == 255 and vals[bar.address + 3] == 0


def test_rig_round_trips_through_json(tmp_path):
    rig = rig_with_bars_and_pars()
    st.save_rig(rig, tmp_path / "rig.json")
    again = st.load_rig(tmp_path / "rig.json")
    assert again.to_json() == rig.to_json()


# ----------------------------------------------------------------- the desk
class FakeLink:
    def __init__(self):
        self.sent, self.released, self.up = {}, 0, True

    def set_channels(self, values):
        self.sent.update(values)

    def release(self, universe=0):
        self.released += 1

    def running(self):
        return self.up

    def close(self):
        pass


def make_desk(tmp_path):
    link = FakeLink()
    desk = studio.StudioDesk(tmp_path / "data", link=link, show_path=tmp_path / "show" / "show.qxw",
                             fixtures_dir=tmp_path / "fixtures", restart=lambda: True)
    return desk, link


def test_desk_builds_a_rig_and_keeps_it(tmp_path):
    desk, _ = make_desk(tmp_path)
    desk.handle("new_profile", template="par7", name="Party Par")
    desk.handle("new_profile", template="bar", name="LED Bar", segments=8)
    st = desk.handle("add_lights", name="Par", profile="Party Par", group="Pars", count=4)
    assert [l["address"] for l in st["rig"]["lights"]] == [1, 8, 15, 22]
    st = desk.handle("add_lights", name="Bar", profile="LED Bar", address=50, group="Bars", count=2)
    assert [l["address"] for l in st["rig"]["lights"][-2:]] == [50, 76]
    assert st["groups"] == ["Pars", "Bars"]
    again = studio.StudioDesk(tmp_path / "data", link=FakeLink())
    assert len(again.rig.lights) == 6 and set(again.rig.profiles) == {"Party Par", "LED Bar"}


def test_desk_explains_clashes_and_changes_nothing(tmp_path):
    desk, _ = make_desk(tmp_path)
    desk.handle("new_profile", template="par7", name="Par")
    desk.handle("add_lights", name="A", profile="Par", address=1)
    desk.handle("add_lights", name="B", profile="Par", address=8)
    with pytest.raises(ValueError, match="already used by A"):
        desk.handle("add_lights", name="C", profile="Par", address=5, count=3)
    assert len(desk.rig.lights) == 2
    with pytest.raises(ValueError, match="already used by B"):
        desk.handle("update_light", id=0, address=3)
    assert desk.rig.lights[0].address == 1
    # growing the profile to 8 channels would run light A into light B
    big = studio.from_template("par8", "Par").to_json()
    with pytest.raises(ValueError, match="overlap"):
        desk.handle("save_profile", profile=big, old_name="Par")
    assert desk.rig.profiles["Par"].count == 7
    with pytest.raises(ValueError, match="Remove the lights"):
        desk.handle("delete_profile", name="Par")


def test_desk_renames_a_profile_with_its_lights(tmp_path):
    desk, _ = make_desk(tmp_path)
    desk.handle("new_profile", template="rgb3", name="Cheap")
    desk.handle("add_lights", name="P", profile="Cheap", count=2)
    prof = desk.rig.profiles["Cheap"].to_json() | {"name": "Wash"}
    desk.handle("save_profile", profile=prof, old_name="Cheap")
    assert set(desk.rig.profiles) == {"Wash"} and {l.profile for l in desk.rig.lights} == {"Wash"}


def test_desk_test_looks_and_finder(tmp_path):
    desk, link = make_desk(tmp_path)
    desk.handle("new_profile", template="par7", name="Par")
    desk.handle("new_profile", template="strobe2", name="Strobe")
    desk.handle("add_lights", name="P", profile="Par", group="Pars", count=2)
    desk.handle("add_lights", name="S", profile="Strobe", group="Strobes")
    desk.handle("look", look="red", group="Pars")
    assert [link.sent[a] for a in range(1, 8)] == [255, 255, 0, 0, 0, 0, 0]
    assert link.sent[15] == 0 and link.sent[16] == 0      # the strobe stays off
    desk.handle("identify", light=1)
    assert link.sent[1] == 0 and link.sent[8] == 255 and link.sent[9] == 255
    desk.handle("probe", address=100, values=[255, 0, 128])
    assert (link.sent[100], link.sent[101], link.sent[102]) == (255, 0, 128)
    assert desk.state()["testing"]
    desk.handle("release")
    assert link.released == 1 and not desk.state()["testing"]


def test_desk_apply_writes_the_show_and_restarts_qlcplus(tmp_path):
    desk, _ = make_desk(tmp_path)
    with pytest.raises(ValueError, match="Patch at least one"):
        desk.handle("apply")
    desk.handle("new_profile", template="rgbw4", name="Wash")
    desk.handle("add_lights", name="W", profile="Wash", count=3)
    out = desk.handle("apply")
    assert (tmp_path / "fixtures" / "Lumiere-Wash.qxf").exists()
    assert (tmp_path / "show" / "Lumiere-Wash.qxf").exists()   # QLC+ also looks next to the show
    assert "<Fixture>" in (tmp_path / "show" / "show.qxw").read_text()
    assert out["fixtures"] == ["Lumiere-Wash.qxf"]


def test_engine_passes_studio_actions(tmp_path):
    from engine import LumiereEngine
    eng = LumiereEngine(data_dir=tmp_path / "data", projects_dir=tmp_path / "projects", demo=True)
    st = eng.action("studio", {"op": "state"})
    assert "par7" in st["templates"] and st["rig"]["lights"] == []
    with pytest.raises(ValueError):
        eng.action("studio", {"op": "nope"})


def test_desk_asks_before_replacing_a_show_made_in_qlcplus(tmp_path):
    desk, _ = make_desk(tmp_path)
    desk.handle("new_profile", template="rgb3", name="Par")
    desk.handle("add_lights", name="P", profile="Par")
    desk.show_path.parent.mkdir(parents=True)
    desk.show_path.write_text('<Workspace><Engine><Function ID="0" Type="Scene" Name="Red"/></Engine></Workspace>')
    out = desk.handle("apply")
    assert "1 scenes" in out["confirm"] and "<Function" in desk.show_path.read_text()
    out = desk.handle("apply", replace=True)
    assert out["restarted"] and "<Function" not in desk.show_path.read_text()
    assert "<Function" in Path(out["backup"]).read_text()


def test_finder_light_with_repeated_channels_becomes_a_bar_with_segments(tmp_path):
    desk, _ = make_desk(tmp_path)
    chans = [{"role": r} for r in ["dimmer", "amber"] * 10]
    desk.handle("save_profile", profile={"name": "Amber bar", "kind": "par", "channels": chans})
    p = desk.rig.profiles["Amber bar"]
    assert p.kind == "bar" and p.segments == 10
    assert [(c.role, c.segment) for c in p.channels[:4]] == [("dimmer", 1), ("amber", 1), ("dimmer", 2), ("amber", 2)]
    # a master dimmer and strobe up front, then RGB per segment
    chans = [{"role": r} for r in ["dimmer", "strobe"] + ["dimmer", "red", "green", "blue"] * 4]
    desk.handle("save_profile", profile={"name": "RGB bar", "kind": "par", "channels": chans})
    p = desk.rig.profiles["RGB bar"]
    assert [(c.role, c.segment) for c in p.channels[:4]] == [("dimmer", None), ("strobe", None), ("dimmer", 1), ("red", 1)]
    assert p.segments == 4 and not p.check()
    assert "<Head>" in p.to_qxf()
    # a pattern that doesn't add up still explains itself
    with pytest.raises(ValueError, match="appear twice"):
        desk.handle("save_profile", profile={"name": "Odd", "channels": [{"role": r} for r in ["red", "red", "red", "red", "green", "green"]]})


def test_looks_light_up_single_colour_lights(tmp_path):
    desk, link = make_desk(tmp_path)
    chans = [{"role": r} for r in ["dimmer", "amber"] * 10]
    desk.handle("save_profile", profile={"name": "Amber bar", "channels": chans})
    desk.handle("new_profile", template="rgb3", name="RGB")
    desk.handle("add_lights", name="Bar", profile="Amber bar", address=1)
    desk.handle("add_lights", name="Par", profile="RGB", address=30)
    for look in ("white", "red", "blue", "amber", "strobe"):
        desk.handle("look", look=look)
        assert [link.sent[a] for a in range(1, 5)] == [255, 255, 255, 255], look
    desk.handle("look", look="red")
    assert [link.sent[a] for a in (30, 31, 32)] == [255, 0, 0]     # the RGB par is really red
    desk.handle("look", look="amber")
    assert link.sent[30] == 255 and link.sent[31] == 90             # and amber-ish
    desk.handle("look", look="off")
    assert not any(link.sent[a] for a in range(1, 21))


THEIR_IO = '''<InputOutputMap>
  <BeatGenerator BeatType="Disabled" BPM="0"/>
  <Universe Name="Universe 1" ID="0">
   <Input Plugin="MIDI" UID="Lumiere Cues" Line="1"/>
   <Output Plugin="ArtNet" Name="2.0.0.1" UID="" Line="2">
    <PluginParameters UniverseChannels="26" outputIP="2.0.0.1"/>
   </Output>
  </Universe>
  <Universe Name="Universe 2" ID="1"/>
 </InputOutputMap>'''


def test_node_output_replaces_a_wrong_one_and_keeps_inputs():
    io = st.with_node_output(THEIR_IO, "2.0.0.2")
    assert io.count("<Output") == 1 and 'Name="2.0.0.2" UID="2.0.0.2"' in io and 'outputIP="2.0.0.10"' in io
    assert "UniverseChannels" not in io and "2.0.0.1\"" not in io
    assert '<Input Plugin="MIDI" UID="Lumiere Cues" Line="1"/>' in io
    assert '<Universe Name="Universe 2" ID="1"/>' in io and "BeatGenerator" in io
    ET.fromstring(io)
    for empty in ("", "<InputOutputMap/>", '<InputOutputMap>\n <Universe Name="U" ID="0"/>\n</InputOutputMap>'):
        io = st.with_node_output(empty, "2.0.0.2")
        ET.fromstring(io)
        assert io.count('outputIP="2.0.0.10"') == 1


def test_written_show_sends_to_the_node(tmp_path):
    rig = rig_with_bars_and_pars()
    show = tmp_path / "show.qxw"
    show.write_text(f"<Workspace><Engine>{THEIR_IO}</Engine></Workspace>")
    out = st.write_show(rig, show, tmp_path / "fx", local_ip="2.0.0.2")
    text = show.read_text()
    assert out["node_output"] == "2.0.0.2 → 2.0.0.10" and 'outputIP="2.0.0.10"' in text and "Lumiere Cues" in text
    ET.fromstring(text.split("\n", 2)[2])   # still well-formed after the DOCTYPE


def test_look_on_picked_lights_only(tmp_path):
    desk, link = make_desk(tmp_path)
    desk.handle("new_profile", template="rgb3", name="RGB")
    desk.handle("add_lights", name="P", profile="RGB", count=3)        # 1, 4, 7
    desk.handle("look", look="red", lights=[0, 2])
    assert [link.sent[a] for a in (1, 4, 7)] == [255, 0, 255]
    desk.handle("look", look="blue", lights=[])
    assert not any(link.sent.values())
