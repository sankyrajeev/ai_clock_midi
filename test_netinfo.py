import socket
import threading
import time

import pytest

import netinfo as ni
import phrases as ph

IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
	inet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
	ether 3c:22:fb:00:11:22
	inet 192.168.0.42 netmask 0xffffff00 broadcast 192.168.0.255
	status: active
en5: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
	ether a0:ce:c8:12:34:56
	inet 169.254.109.134 netmask 0xffff0000 broadcast 169.254.255.255
	media: autoselect (1000baseT <full-duplex>)
	status: active
en6: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
	ether a0:ce:c8:99:99:99
	status: inactive
"""
PORTS = """
Hardware Port: Wi-Fi
Device: en0
Ethernet Address: 3c:22:fb:00:11:22

Hardware Port: USB 10/100/1000 LAN
Device: en5
Ethernet Address: a0:ce:c8:12:34:56

Hardware Port: Thunderbolt Ethernet
Device: en6
Ethernet Address: a0:ce:c8:99:99:99
"""


def mac_ifaces():
    ifaces = ni.parse_ifconfig(IFCONFIG)
    ports = ni.parse_hardware_ports(PORTS)
    for i in ifaces:
        i.port = ports.get(i.name, "")
    return ifaces


def test_parse_macos_ifconfig():
    by = {i.name: i for i in mac_ifaces()}
    en5 = by["en5"]
    assert en5.wired and en5.has_link and en5.port == "USB 10/100/1000 LAN"
    assert en5.mac == bytes.fromhex("a0cec8123456")
    assert en5.addrs == [ni.Addr("169.254.109.134", "255.255.0.0", "169.254.255.255")]
    assert not by["en0"].wired  # Wi-Fi
    assert by["en6"].wired and not by["en6"].has_link
    assert by["lo0"].addrs[0].netmask == "255.0.0.0"


def test_find_addr_prefers_the_wired_port_on_the_players_subnet():
    iface, addr = ni.find_addr_for("169.254.7.140", mac_ifaces())
    assert iface.name == "en5" and addr.ip == "169.254.109.134"
    assert ni.find_addr_for("10.0.0.5", mac_ifaces()) == (None, None)


def test_alias_choice_stays_on_the_players_network_and_avoids_taken():
    ip, mask = ni.choose_alias_ip("192.168.1.23", {"192.168.1.254"})
    assert ip == "192.168.1.253" and mask == "255.255.255.0"
    ip, mask = ni.choose_alias_ip("169.254.7.140", set())
    assert ip.startswith("169.254.") and mask == "255.255.0.0" and ip != "169.254.7.140"


def test_nothing_found_advice_points_at_the_physical_problem():
    ifaces = mac_ifaces()
    assert "no CDJs or mixer are answering" in ni.nothing_found_advice(ifaces)
    for i in ifaces:
        if i.name == "en5":
            i.active = False
    msg = ni.nothing_found_advice(ifaces)
    assert msg.startswith("No Ethernet link") and "cable between this Mac and the switch" in msg
    assert "No Ethernet port" in ni.nothing_found_advice([i for i in ifaces if not i.wired])


def test_add_alias_rejects_odd_interface_names():
    ok, msg = ni.add_alias("en5; rm -rf /", "169.254.1.1", "255.255.0.0")
    assert not ok and "refusing" in msg


# --- the supervisor ----------------------------------------------------------

class FakeNet:
    """Mutable interface list, plus an add_alias that 'configures' it."""

    def __init__(self, ifaces):
        self.ifaces = ifaces
        self.alias_calls = []

    def list(self):
        return self.ifaces

    def add_alias(self, name, ip, mask):
        self.alias_calls.append((name, ip, mask))
        for i in self.ifaces:
            if i.name == name:
                bcast = str(ni.ipaddress.ip_network(f"{ip}/{mask}", strict=False).broadcast_address)
                i.addrs.append(ni.Addr(ip, mask, bcast))
        return True, "ok"


def make_vcdj(net, **kw):
    tracker = ph.PhraseTracker(lambda e: None, lambda cc, v: None)
    try:
        v = ph.VirtualCdj(tracker, bind="127.0.0.1", broadcast="127.0.0.1", **kw)
    except OSError as e:
        pytest.skip(f"cannot bind DJ Link ports here: {e}")
    v.startup_wait, v.claim_interval, v.search_grace = 0.05, 0.005, 0.3
    v.list_ifaces, v.add_alias = net.list, net.add_alias
    return v


def keep_announcing(v, devices, stop):
    while not stop.is_set():
        for d in devices():
            v.devices[d.number] = ph.Device(d.number, d.name, d.ip, time.monotonic())
        time.sleep(0.1)


def wait_for(cond, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_cdj_stuck_on_old_ip_gets_reached_through_an_alias():
    net = FakeNet(mac_ifaces())
    v = make_vcdj(net)
    stop = threading.Event()
    cdj = ph.Device(2, "CDJ-3000X", "192.168.1.23", 0)
    threading.Thread(target=keep_announcing, args=(v, lambda: [cdj], stop), daemon=True).start()
    v.start()
    try:
        assert wait_for(lambda: v.number != 0)
        assert net.alias_calls and net.alias_calls[0][0] == "en5"  # the wired port, not Wi-Fi
        assert v.local_ip == net.alias_calls[0][1] and v.local_ip.startswith("192.168.1.")
        assert len(net.alias_calls) == 1
    finally:
        stop.set()
        v.close()


def test_refuses_to_fix_when_disabled_and_explains():
    net = FakeNet(mac_ifaces())
    v = make_vcdj(net, fix_network=False)
    stop = threading.Event()
    cdj = ph.Device(2, "CDJ-3000X", "192.168.1.23", 0)
    threading.Thread(target=keep_announcing, args=(v, lambda: [cdj], stop), daemon=True).start()
    v.start()
    try:
        assert wait_for(lambda: v.problem is not None)
        assert "192.168.1.23" in v.problem and "Restart the player" in v.problem
        assert not net.alias_calls and v.number == 0
    finally:
        stop.set()
        v.close()


def test_unplugged_then_replugged_reconnects_without_restart():
    net = FakeNet(mac_ifaces())
    v = make_vcdj(net)
    v.device_timeout = 0.4
    stop = threading.Event()
    present = [True]
    cdj = ph.Device(2, "CDJ-3000X", "169.254.7.140", 0)
    threading.Thread(target=keep_announcing, args=(v, lambda: [cdj] if present[0] else [], stop),
                     daemon=True).start()
    v.start()
    try:
        assert wait_for(lambda: v.number != 0)
        first = v.number
        # cable pulled: players vanish, the Mac's port loses link
        present[0] = False
        for i in net.ifaces:
            if i.name == "en5":
                i.active = False
        assert wait_for(lambda: v.number == 0)
        assert wait_for(lambda: v.problem is not None and "No Ethernet link" in v.problem)
        # cable back in
        for i in net.ifaces:
            if i.name == "en5":
                i.active = True
        present[0] = True
        assert wait_for(lambda: v.number != 0)
        assert v.number == first  # same device number as before, when it's free
        assert v.problem is None or "Not receiving" in v.problem
    finally:
        stop.set()
        v.close()


def test_mac_address_change_triggers_rejoin():
    net = FakeNet(mac_ifaces())
    v = make_vcdj(net)
    stop = threading.Event()
    cdj = ph.Device(2, "CDJ-3000X", "169.254.7.140", 0)
    threading.Thread(target=keep_announcing, args=(v, lambda: [cdj], stop), daemon=True).start()
    v.start()
    try:
        assert wait_for(lambda: v.local_ip == "169.254.109.134")
        for i in net.ifaces:
            if i.name == "en5":
                i.addrs = [ni.Addr("169.254.20.30", "255.255.0.0", "169.254.255.255")]
        assert wait_for(lambda: v.local_ip == "169.254.20.30")
    finally:
        stop.set()
        v.close()


def test_local_network_permission_error_is_explained():
    err = OSError(65, "No route to host")
    assert "Local Network" in ph.VirtualCdj._send_error_advice(err)


def test_simulated_booth_serves_phrases_and_waveforms():
    """The pretend booth: deck 2 has rekordbox phrases, deck 1 only a waveform."""
    import simrig
    try:
        rig = simrig.SimRig()
    except OSError as e:
        pytest.skip(f"cannot start the simulated booth here: {e}")
    tracker = ph.PhraseTracker(lambda e: None, lambda cc, v: None)
    net = FakeNet([ni.Iface("lo0", up=True, addrs=[ni.Addr("127.0.0.1", "255.0.0.0", "127.255.255.255")])])
    v = make_vcdj(net)
    v.db_query_port = rig.db.query.getsockname()[1]
    rig.start()
    v.start()
    try:
        assert wait_for(lambda: len(tracker_analyses(v)) == 2, timeout=10)
        sources = {key[0]: tp.source for key, tp in tracker_analyses(v).items()}
        assert sources == {1: "waveform", 2: "rekordbox"}
        assert len(v.tracker.waveforms) == 2  # kept for drawing on screen
        cues = [p.cue for p in tracker_analyses(v)[(1, 3, 1002)].phrases]
        assert cues == [ph.INTRO, ph.DROP, ph.BREAKDOWN, ph.BUILD, ph.DROP, ph.OUTRO]
    finally:
        v.close()
        rig.stop()


def tracker_analyses(v):
    return {k: tp for k, tp in v.tracker.analysis.items() if tp}
