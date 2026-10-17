import numpy as np
import pytest

from sections import BAR as BAR_EVENT, BREAKDOWN, BUILD, DROP, PHRASE, SectionDetector, analyze_file

SR = 44100
BPM = 128.0
BEAT = 60.0 / BPM


BAR = 4 * BEAT


def synth_track(sections, seed=0):
    """sections: list of (bars, kind) with kind in full/breakdown/build."""
    rng = np.random.default_rng(seed)
    out = []
    t0 = 0.0
    for bars, kind in sections:
        dur = bars * BAR
        n = int(round(dur * SR))
        t = t0 + np.arange(n) / SR
        pad = 0.08 * (np.sin(2 * np.pi * 440 * t) + np.sin(2 * np.pi * 554 * t))
        sig = pad
        if kind == "full":
            phase = np.mod(t, BEAT)
            kick = 0.8 * np.sin(2 * np.pi * 55 * phase) * np.exp(-phase / 0.12)
            hats = np.diff(rng.standard_normal(n + 1)) * 0.05 * (np.mod(t + BEAT / 2, BEAT) < 0.05)
            sig = sig + kick + hats
        elif kind == "build":
            ramp = np.linspace(0.005, 0.25, n)
            riser = np.diff(rng.standard_normal(n + 1)) * ramp
            sig = sig + riser
        out.append(sig.astype(np.float32))
        t0 += dur
    return np.concatenate(out)


def run(audio, beats=False):
    events = []
    det = SectionDetector(SR, lambda e, t: events.append((round(t, 2), e)))
    block = 1024
    next_beat, beat_no = 0.0, 0
    for start in range(0, len(audio) - block + 1, block):
        t = (start + block) / SR
        if beats:
            while next_beat <= t:
                det.on_beat(beat_no % 4 + 1, next_beat)
                beat_no += 1
                next_beat = beat_no * BEAT
        det.process(audio[start:start + block], t)
    return events


def sections_only(events):
    return [(t, e) for t, e in events if e in (BREAKDOWN, BUILD, DROP)]


def test_breakdown_build_drop_sequence():
    audio = synth_track([(16, "full"), (8, "breakdown"), (8, "build"), (10, "full")])
    ev = sections_only(run(audio))
    names = [e for _, e in ev]
    assert names == [BREAKDOWN, BUILD, DROP], ev
    t_break, t_build, t_drop = (t for t, _ in ev)
    assert 16 * BAR < t_break < 16 * BAR + 5
    assert 24 * BAR < t_build < 32 * BAR
    assert 0 <= t_drop - 32 * BAR < 0.1  # the drop fires within one kick


def test_breakdown_straight_to_drop_without_build():
    audio = synth_track([(10, "full"), (6, "breakdown"), (5, "full")])
    names = [e for _, e in sections_only(run(audio))]
    assert names == [BREAKDOWN, DROP]


def test_short_kick_dropout_is_not_a_breakdown():
    audio = synth_track([(10, "full"), (0.5, "breakdown"), (5, "full")])
    assert sections_only(run(audio)) == []


def test_steady_track_and_level_changes_fire_nothing():
    audio = synth_track([(21, "full")])
    audio[int(20 * SR):] *= 0.5  # DJ pulls the fader down 6 dB
    assert sections_only(run(audio)) == []


def test_bars_and_phrases_count_from_drop():
    audio = synth_track([(10, "full"), (6, "breakdown"), (21, "full")])
    ev = run(audio, beats=True)
    drop_t = next(t for t, e in ev if e == DROP)
    phrases = [t for t, e in ev if e == PHRASE and t > drop_t]
    bar_len = BAR
    assert phrases, ev
    # first phrase marker lands 8 bars after the drop's downbeat
    assert phrases[0] == pytest.approx(16 * bar_len + 8 * bar_len, abs=0.02)
    assert any(e == BAR_EVENT for _, e in ev)


def test_analyze_file_roundtrip(tmp_path):
    import wave
    audio = synth_track([(10, "full"), (6, "breakdown"), (5, "full")])
    path = tmp_path / "set.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    names = [e for _, e in analyze_file(str(path))]
    assert names == [BREAKDOWN, DROP]


def _add(audio, start_bar, end_bar, fn):
    n0, n1 = int(start_bar * BAR * SR), int(end_bar * BAR * SR)
    t = np.arange(n1 - n0) / SR
    audio[n0:n1] += fn(t).astype(np.float32)


@pytest.mark.parametrize("amp", [0.12, 0.3])
def test_breakdown_with_sub_bass_still_detected(amp):
    """Sustained sub/bassline without a kick is still a breakdown."""
    audio = synth_track([(16, "full"), (8, "breakdown"), (10, "full")])
    _add(audio, 16, 24, lambda t: amp * np.sin(2 * np.pi * 45 * t))
    ev = sections_only(run(audio))
    assert [e for _, e in ev] == [BREAKDOWN, DROP]
    assert 0 <= ev[-1][0] - 24 * BAR < 0.1


def test_kick_roll_before_drop_does_not_fire_early():
    audio = synth_track([(16, "full"), (8, "breakdown"), (10, "full")])

    def roll(t):
        ph = np.mod(t, BEAT / 4)
        return 0.35 * np.sin(2 * np.pi * 70 * ph) * np.exp(-ph / 0.04)

    _add(audio, 23, 24, roll)
    ev = sections_only(run(audio))
    assert [e for _, e in ev] == [BREAKDOWN, DROP]
    assert ev[-1][0] >= 24 * BAR


def test_audio_to_midi_wiring(monkeypatch):
    """AudioInput -> SectionDetector -> CueOutput, with fake sound card and MIDI port."""
    import sys
    import time
    import types

    sent = []

    class FakePort:
        def send(self, msg):
            sent.append(msg)

        def close(self):
            pass

    import mido
    monkeypatch.setattr(mido, "open_output", lambda name, virtual=False: FakePort())

    streams = []

    class FakeStream:
        def __init__(self, **kw):
            self.kw = kw
            streams.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    fake_sd = types.SimpleNamespace(
        query_devices=lambda: [
            {"name": "MacBook Air Microphone", "max_input_channels": 1, "default_samplerate": 48000.0},
            {"name": "DJM-V10", "max_input_channels": 4, "default_samplerate": float(SR)},
        ],
        default=types.SimpleNamespace(device=(0, 0)),
        InputStream=FakeStream,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    import sections
    cues = sections.CueOutput("ProLink Cues", use_existing=False)
    audio = sections.AudioInput(None, [1, 2], lambda sr: SectionDetector(sr, cues.event, cues.levels))
    assert streams[0].kw["device"] == 1  # picked the DJM automatically

    # Feed a breakdown -> drop as 2-channel blocks through the stream callback,
    # pretending each block arrives in real time.
    track = synth_track([(10, "full"), (6, "breakdown"), (3, "full")])
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    cb = streams[0].kw["callback"]
    block = 1024
    for start in range(0, len(track) - block + 1, block):
        clock[0] = (start + block) / SR
        stereo = np.repeat(track[start:start + block, None], 4, axis=1)
        cb(stereo, block, None, None)
    time.sleep(0.15)  # let note-off timers fire

    notes = [m.note for m in sent if m.type == "note_on" and m.velocity > 0]
    assert notes == [sections.NOTES[BREAKDOWN], sections.NOTES[DROP]]
    assert any(m.type == "note_off" for m in sent)
    ccs = {m.control for m in sent if m.type == "control_change"}
    assert ccs == set(sections.CCS.values())
