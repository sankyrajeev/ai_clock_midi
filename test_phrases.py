import socket
import struct
import threading
import time

import pytest

import phrases as ph
from phrases import (BAR, BREAKDOWN, BUILD, DROP, HIGH, INTRO, MID, NEW_TRACK, OUTRO, PHRASE,
                     PRE_DROP, PhraseTracker, build_pssi, build_status_packet, parse_pssi,
                     parse_status)

MAC = bytes.fromhex("0a0b0c0d0e0f")
IP = socket.inet_aton("169.254.10.20")


# --- packets ---------------------------------------------------------------

def beatlink(template_tail, kind):
    """beat-link's VirtualCdj templates (name 'beat-link'), for byte comparison."""
    head = b"Qspt1WmJOL" + bytes([kind, 0]) + b"beat-link".ljust(20, b"\x00")
    return head + bytes(template_tail)


def test_startup_packets_match_beat_link_templates():
    assert ph.hello_packet("beat-link") == beatlink([0x01, 0x04, 0x00, 0x26, 0x01, 0x40], 0x0A)
    assert ph.claim1_packet("beat-link", MAC, 2) == beatlink([0x01, 0x03, 0x00, 0x2C, 2, 0x01] + list(MAC), 0x00)
    assert ph.claim2_packet("beat-link", IP, MAC, 7, 3, auto=True) == beatlink(
        [0x01, 0x03, 0x00, 0x32] + list(IP) + list(MAC) + [7, 3, 0x01, 0x01], 0x02)
    assert ph.claim3_packet("beat-link", 7, 1) == beatlink([0x01, 0x03, 0x00, 0x26, 7, 1], 0x04)
    ka = ph.keepalive_packet("beat-link", 7, MAC, IP, 2)
    assert ka == beatlink([0x01, 0x02, 0x00, 0x36, 7, 0x01] + list(MAC) + list(IP) + [2, 0, 0, 0, 0x01, 0x64], 0x06)
    assert len(ka) == 0x36
    dev = ph.parse_keepalive(ka, 1.0)
    assert (dev.number, dev.name, dev.ip) == (7, "beat-link", "169.254.10.20")


def test_status_roundtrip():
    pkt = build_status_packet(2, 3, 3, 4242, beat=129, beat_in_bar=1, playing=True, master=True, bpm=126.5)
    st = parse_status(pkt, 5.0)
    assert (st.device, st.track_player, st.slot, st.rekordbox_id, st.beat, st.beat_in_bar) == (2, 3, 3, 4242, 129, 1)
    assert st.playing and st.master and st.on_air and st.bpm == 126.5
    assert st.track_key == (3, 3, 4242)
    idle = parse_status(build_status_packet(1, 0, 0, 0, 0xFFFFFFFF, 0, playing=False), 0)
    assert idle.track_key is None and idle.beat is None and not idle.playing


# --- phrase analysis -------------------------------------------------------

EDM = [(1, 1), (33, 2), (65, 5), (129, 3), (161, 2), (193, 5), (257, 6)]


@pytest.mark.parametrize("masked", [True, False])
def test_pssi_parse_high_mood(masked):
    tp = parse_pssi(build_pssi(HIGH, EDM, 289, masked=masked))
    assert tp.mood == HIGH and tp.end_beat == 289
    assert [p.cue for p in tp.phrases] == [INTRO, BUILD, DROP, BREAKDOWN, BUILD, DROP, OUTRO]
    assert [p.label for p in tp.phrases] == ["Intro", "Up", "Chorus", "Down", "Up", "Chorus", "Outro"]
    assert tp.index_at(1) == 0 and tp.index_at(64) == 1 and tp.index_at(65) == 2
    assert tp.index_at(300) is None


def test_pssi_masking_is_real_and_dbserver_prefix_accepted():
    raw = build_pssi(HIGH, EDM, 289, masked=True)
    assert struct.unpack_from(">H", raw, 0x12)[0] > 20  # scrambled like rekordbox 6 exports
    blob = struct.pack(">I", len(raw)) + raw  # dbserver blobs carry a length prefix
    assert parse_pssi(blob).phrases == parse_pssi(raw).phrases


def test_pssi_mid_mood_labels():
    tp = parse_pssi(build_pssi(MID, [(1, 1), (17, 2), (49, 8), (65, 9), (97, 10)], 129))
    assert [p.label for p in tp.phrases] == ["Intro", "Verse 1", "Bridge", "Chorus", "Outro"]
    assert tp.phrases[3].cue == DROP


# --- dbserver --------------------------------------------------------------

class FakeDbServer:
    """Speaks just enough of the dbserver protocol to serve one PSSI tag."""

    def __init__(self, tags, grids=None):
        self.tags = tags  # {(slot, rekordbox_id): PSSI bytes} or {(slot, rekordbox_id, "PWV7"): bytes}
        self.grids = grids or {}  # {(slot, rekordbox_id): beat grid bytes}
        self.requests = []
        self.query = socket.socket()
        self.query.bind(("127.0.0.1", 0))
        self.query.listen()
        self.db = socket.socket()
        self.db.bind(("127.0.0.1", 0))
        self.db.listen()
        self.query_port = self.query.getsockname()[1]
        threading.Thread(target=self._query_loop, daemon=True).start()
        threading.Thread(target=self._db_loop, daemon=True).start()

    def _query_loop(self):
        while True:
            c, _ = self.query.accept()
            with c:
                assert ph._Reader(c).exact(19) == struct.pack(">I", 15) + b"RemoteDBServer\x00"
                c.sendall(struct.pack(">H", self.db.getsockname()[1]))

    def _db_loop(self):
        while True:
            c, _ = self.db.accept()
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        rd = ph._Reader(c)
        with c:
            assert rd.field() == 1
            c.sendall(ph._num(1))
            try:
                while True:
                    txid, kind, args = rd.message()
                    self.requests.append((kind, args))
                    if kind == 0x0000:
                        c.sendall(self._reply(txid, 0x4000, [0, 3]))
                    elif kind == 0x2C04:
                        dmst, rbid, tag, ext = args
                        slot = (dmst >> 8) & 0xFF
                        name = tag.to_bytes(4, "big")[::-1].decode()
                        key = (slot, rbid) if name == "PSSI" else (slot, rbid, name)
                        c.sendall(self._tag_reply(txid, self.tags.get(key, b"")))
                    elif kind == 0x2204:
                        dmst, rbid = args
                        grid = self.grids.get(((dmst >> 8) & 0xFF, rbid), b"")
                        c.sendall(self._tag_reply(txid, grid, kind=0x4602, req=0x2204, prefix=False))
                    elif kind == 0x0100:
                        return
            except ConnectionError:
                return

    @staticmethod
    def _reply(txid, kind, nums):
        return ph.encode_message(txid, kind, nums)

    @staticmethod
    def _tag_reply(txid, data, kind=0x4F02, req=0x2C04, prefix=True):
        blob = (struct.pack(">I", len(data)) + data if prefix else data) if data else b""
        types = bytes([6, 6, 6, 3, 6]).ljust(12, b"\x00")
        out = (ph._num(ph.MSG_MAGIC) + ph._num(txid) + ph._num(kind, 2) + ph._num(5, 1) + ph._blob(types)
               + ph._num(req) + ph._num(0) + ph._num(len(blob)))
        if blob:
            out += ph._blob(blob)
        return out + ph._num(0)


def test_dbclient_downloads_phrases():
    tag = build_pssi(HIGH, EDM, 289)
    srv = FakeDbServer({(3, 4242): tag})
    client = ph.DbClient("127.0.0.1", posing_as=2, query_port=srv.query_port)
    blob = client.anlz_tag(3, 1, 4242)
    missing = client.anlz_tag(3, 1, 1)
    client.close()
    assert parse_pssi(blob).phrases == parse_pssi(tag).phrases
    assert missing is None
    kind, args = srv.requests[1]
    assert kind == 0x2C04
    assert args == [(2 << 24) | (1 << 16) | (3 << 8) | 1, 4242, 0x49535350, 0x00545845]


# --- tracker ---------------------------------------------------------------

class Rig:
    """Drives a PhraseTracker like a deck would: beat packets plus status packets."""

    def __init__(self, lead=0.0, follow="master"):
        self.events = []
        self.levels = []
        self.tracker = PhraseTracker(lambda e: self.events.append((self.beat_now, e)),
                                     lambda cc, v: self.levels.append((cc, v)), follow=follow, lead=lead)
        self.loads = []
        self.tracker.on_track = self.loads.append
        self.beat_now = 0
        self.t = 100.0

    def status(self, device, beat, rbid=4242, master=True, playing=True, on_air=True, player=None):
        bib = (beat - 1) % 4 + 1 if beat else 0
        pkt = build_status_packet(device, player or device, 3, rbid, beat, bib, playing=playing,
                                  master=master, on_air=on_air)
        self.tracker.on_status(parse_status(pkt, self.t))

    def play(self, device, beats, **kw):
        """Play `beats` beats on `device`: a beat packet, then a status 0.2 s later."""
        for _ in range(beats):
            self.t += 0.47
            deck = self.tracker.decks[device]
            self.beat_now = deck.beat + 1
            self.tracker.on_beat(device, (self.beat_now - 1) % 4 + 1, 128.0, self.t)
            self.t += 0.2
            self.status(device, self.beat_now, **kw)
            self.t -= 0.2


def loaded_rig(**kw):
    rig = Rig(**kw)
    rig.status(1, 1)
    assert rig.loads and rig.loads[0].track_key == (1, 3, 4242)
    rig.tracker.set_analysis((1, 3, 4242), parse_pssi(build_pssi(HIGH, EDM, 289)))
    return rig


def cues(rig, names=(INTRO, BUILD, DROP, BREAKDOWN, OUTRO, PRE_DROP)):
    return [(b, e) for b, e in rig.events if e in names]


def test_phrases_fire_on_their_first_beat():
    rig = loaded_rig()
    rig.play(1, 288)
    got = cues(rig)
    assert got == [(0, INTRO), (33, BUILD), (61, PRE_DROP), (65, DROP), (129, BREAKDOWN),
                   (161, BUILD), (189, PRE_DROP), (193, DROP), (257, OUTRO)]
    assert (0, NEW_TRACK) in rig.events
    bars = [b for b, e in rig.events if e == BAR]
    assert bars[:3] == [5, 9, 13]
    phrase_marks = [b for b, e in rig.events if e == PHRASE]
    assert 97 in phrase_marks and 225 in phrase_marks  # 8 bars into each drop


def test_build_progress_and_energy_levels():
    rig = loaded_rig()
    rig.play(1, 70)
    build = [v for cc, v in rig.levels if cc == ph.CC_BUILD]
    assert max(build) > 0.9 and build[-1] == 0.0  # ramps through the Up, resets at the drop
    energy = [v for cc, v in rig.levels if cc == ph.CC_ENERGY]
    assert energy[-1] == 1.0


def test_hot_cue_jump_announces_new_section_immediately():
    rig = loaded_rig()
    rig.play(1, 10)
    rig.events.clear()
    rig.t += 0.1
    rig.status(1, 129)  # DJ jumps to the breakdown
    assert cues(rig) == [(rig.beat_now, BREAKDOWN)]


def test_follows_master_handoff_to_other_deck():
    rig = loaded_rig()
    rig.play(1, 40)
    rig.status(2, 1, rbid=777, master=False, player=2)
    rig.tracker.set_analysis((2, 3, 777), parse_pssi(build_pssi(HIGH, EDM, 289)))
    rig.events.clear()
    rig.t += 0.05
    rig.status(1, 41, master=False)  # deck 1 hands master over
    rig.status(2, 1, rbid=777, master=True, player=2)
    assert rig.tracker.followed == 2
    assert [e for _, e in rig.events] == [NEW_TRACK, INTRO]


def test_status_racing_ahead_of_beat_packet_does_not_skip_a_beat():
    rig = loaded_rig()
    rig.play(1, 30)
    deck = rig.tracker.decks[1]
    rig.t += 0.4
    rig.status(1, 32)  # status for beat 32 arrives a hair before its beat packet
    rig.t += 0.03
    rig.tracker.on_beat(1, 4, 128.0, rig.t)
    assert deck.beat == 32


def test_no_phrase_data_means_audio_fallback_allowed():
    rig = Rig()
    rig.status(1, 1)
    rig.tracker.set_analysis((1, 3, 4242), None)
    assert not rig.tracker.has_phrases()
    rig.tracker.set_analysis((1, 3, 4242), parse_pssi(build_pssi(HIGH, EDM, 289)))
    assert rig.tracker.has_phrases()


def test_lead_fires_cue_early_by_the_latency():
    fired = {}
    tracker = PhraseTracker(lambda e: fired.setdefault(e, time.monotonic()), lambda cc, v: None, lead=0.04)
    tracker.set_analysis((1, 3, 1), parse_pssi(build_pssi(HIGH, [(1, 1), (5, 5)], 64)))
    bpm, beat_len = 300.0, 0.2
    start = time.monotonic()
    tracker.on_status(parse_status(build_status_packet(1, 1, 3, 1, 1, 1, master=True, bpm=bpm), start))
    for beat in range(2, 6):
        target = start + (beat - 1) * beat_len
        while time.monotonic() < target:
            time.sleep(0.001)
        tracker.on_beat(1, (beat - 1) % 4 + 1, bpm, time.monotonic())
    time.sleep(0.25)
    drop_beat_time = start + 4 * beat_len
    assert DROP in fired
    assert -0.06 < fired[DROP] - drop_beat_time < -0.02  # about 40 ms early


# --- MIDI gate ---------------------------------------------------------------

def test_audio_cues_suppressed_while_phrases_available(monkeypatch):
    import mido
    sent = []

    class Port:
        def send(self, m):
            sent.append(m)

        def close(self):
            pass

    monkeypatch.setattr(mido, "open_output", lambda name, virtual=False: Port())
    have = [True]
    midi = ph.CueMidi("ProLink Cues", False, gate=lambda: have[0])
    midi.audio_event(DROP)
    midi.audio_levels({"energy": 0.5, "bass": 0.5, "high": 0.5, "build": 0.5})
    notes = [m for m in sent if m.type == "note_on"]
    assert notes == [] and {m.control for m in sent} == {21, 22}
    have[0] = False
    midi.audio_event(DROP)
    assert [m.note for m in sent if m.type == "note_on"] == [ph.NOTES[DROP]]


# --- end to end over loopback ------------------------------------------------

def test_virtual_cdj_end_to_end(monkeypatch):
    """A fake CDJ on 127.0.0.1: keep-alives, status packets and a dbserver.
    The virtual player joins, sees the track, downloads its phrases and cues them."""
    tag = build_pssi(HIGH, EDM, 289)
    srv = FakeDbServer({(3, 4242): tag})
    events = []
    tracker = PhraseTracker(events.append, lambda cc, v: None)
    try:
        vcdj = ph.VirtualCdj(tracker, bind="127.0.0.1", broadcast="127.0.0.1")
    except OSError as e:
        pytest.skip(f"cannot bind DJ Link ports here: {e}")
    vcdj.startup_wait, vcdj.claim_interval, vcdj.db_query_port = 0.1, 0.01, srv.query_port
    vcdj.start()
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        cdj_ip = socket.inet_aton("127.0.0.1")
        deadline = time.monotonic() + 10
        beat = 1
        while time.monotonic() < deadline and vcdj.number == 0:
            out.sendto(ph.keepalive_packet("CDJ-3000X", 1, MAC, cdj_ip, 1), ("127.0.0.1", ph.ANNOUNCE_PORT))
            time.sleep(0.05)
        assert 7 <= vcdj.number <= 15
        while time.monotonic() < deadline and not tracker.has_phrases():
            out.sendto(build_status_packet(1, 1, 3, 4242, beat, 1, master=True), ("127.0.0.1", ph.STATUS_PORT))
            time.sleep(0.05)
        assert tracker.has_phrases()
        assert INTRO in events
        # posing as the source player's own number (CDJ-3000 behaviour)
        setup = next(args for kind, args in srv.requests if kind == 0x0000)
        assert setup == [1]
    finally:
        vcdj.close()
        out.close()
