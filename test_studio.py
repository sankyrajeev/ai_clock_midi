"""Lumière Studio: light profiles, the patch, and the QLC+ files it writes."""

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

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
