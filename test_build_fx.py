"""Build-up and fill analysis scored on generated tracks with known answers, and
the USB-stick beta-test tool run on rekordbox-style analysis files."""

import struct

import numpy as np

import analyse_tracks as at
import buildcorpus as bc
import phrases as ph
import waveform as wf


def test_200_generated_tracks():
    r = bc.score([bc.make(seed) for seed in range(200)])
    assert r["beat_accuracy"] >= 0.99, r["misses"][:10]
    assert r["false_roll_beats"] <= 0.005
    assert r["top_speed_right"] >= 0.98
    assert r["riser_followed"] >= 0.95
    assert r["fill_roll_right"] >= 0.98


def test_200_hard_tracks():
    """More noise, quieter snares under louder risers, slower-decaying hits."""
    r = bc.score([bc.make(seed, hard=True) for seed in range(2000, 2200)])
    assert r["beat_accuracy"] >= 0.97, r["misses"][:10]
    assert r["false_roll_beats"] <= 0.01
    assert r["top_speed_right"] >= 0.95
    assert r["fill_roll_right"] >= 0.95


def test_speed_steps_and_description():
    c = wf.BuildCurve(1, 33, [0] * 16 + [1] * 8 + [2] * 4 + [4] * 2 + [8, 8], [0.5] * 128)
    assert [c.speed(b) for b in (1, 17, 25, 29, 31)] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert c.describe() == "roll 1/4 from bar 5, 1/8 from bar 7, 1/16 from bar 8, 1/32 from bar 8"


def test_faders_follow_the_build_and_drop_back_at_the_drop():
    from test_phrases import loaded_rig
    rig = loaded_rig()  # EDM layout: Up at beat 33, drop at 65
    curve = wf.BuildCurve(33, 65, [0] * 16 + [2] * 8 + [4] * 8, [i / 128 for i in range(128)])
    rig.tracker.build_curve = lambda key, s, e: curve if (s, e) == (33, 65) else None
    rig.play(1, 66)
    speed = [v for cc, v in rig.levels if cc == ph.CC_BUILD_SPEED]
    assert 0.5 in speed and 0.75 in speed and speed[-1] == 0.0
    intensity = [v for cc, v in rig.levels if cc == ph.CC_BUILD_INTENSITY]
    assert max(intensity) > 0.9 and intensity[-1] == 0.0


# --- the USB-stick tool -------------------------------------------------------

def _tag(fourcc: bytes, header: bytes, body: bytes) -> bytes:
    len_header = 12 + len(header)
    return fourcc + struct.pack(">II", len_header, len_header + len(body)) + header + body


def write_anlz(folder, t: bc.CorpusTrack, name: str, phrases: bool = True) -> None:
    folder.mkdir(parents=True)
    path = ("/Contents/" + name).encode("utf-16-be") + b"\x00\x00"
    ppth = _tag(b"PPTH", struct.pack(">I", len(path)), path)
    beats = b"".join(struct.pack(">HHI", g.beat_in_bar, int(round(g.bpm * 100)), g.time_ms) for g in t.grid)
    pqtz = _tag(b"PQTZ", struct.pack(">III", 0, 0x80000, len(t.grid)), beats)
    (folder / "ANLZ0000.DAT").write_bytes(b"PMAI" + struct.pack(">II", 28, 0) + bytes(16) + ppth + pqtz)
    pssi = ph.build_pssi(ph.HIGH, [(1, 1), (t.build_start, 2), (t.drop, 5), (t.drop + 64, 6)], t.drop + 80)
    (folder / "ANLZ0000.EXT").write_bytes(b"PMAI" + bytes(24) + (pssi if phrases else b""))
    pwv7 = wf.build_waveform_tag(t.bands.low, t.bands.mid, t.bands.high)
    (folder / "ANLZ0000.2EX").write_bytes(b"PMAI" + bytes(24) + pwv7)


def test_usb_tool_reads_rekordbox_files_and_reports(tmp_path, capsys):
    usb = tmp_path / "USB" / "PIONEER" / "USBANLZ"
    tracks = [bc.make(seed) for seed in range(8)]
    for i, t in enumerate(tracks):
        write_anlz(usb / f"P{i:03d}" / "0000ABCD", t, f"Track {i}.mp3", phrases=i != 3)
    out = tmp_path / "report.csv"
    assert at.main([str(tmp_path / "USB"), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "8 tracks" in text and "Build-ups: " in text
    report = out.read_text()
    assert "Track 0.mp3" in report and "rekordbox" in report
    for i, t in enumerate(tracks):
        if i == 3:
            continue  # sections from the waveform: its own build boundaries
        row = at.analyse_track(next((usb / f"P{i:03d}").rglob("ANLZ0000.DAT")))
        start, _, curve = row["builds"][0]
        assert start == t.build_start and curve.rate == t.rate
