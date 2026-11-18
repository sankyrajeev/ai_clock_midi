"""The factory check against a pretend Node (HTTP status + Art-Net in) and Resident."""

import csv
import json
import socket
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer

import lumiere_factory as lf


def test_artdmx_packet():
    pkt = lf.artdmx(bytes([255, 128, 7]), universe=0x123, sequence=5)
    assert pkt[:8] == b"Art-Net\x00" and pkt[8:10] == b"\x00\x50" and pkt[10:12] == b"\x00\x0e"
    assert pkt[12] == 5 and pkt[14] == 0x23 and pkt[15] == 0x01
    assert int.from_bytes(pkt[16:18], "big") == 4 and pkt[18:] == bytes([255, 128, 7, 0])


class FakeNode:
    """Answers /status like the Node; Art-Net makes it LIVE; press() toggles HOUSE."""

    def __init__(self):
        self.state = {"mode": "HOUSE", "signal": False, "house_forced": False, "version": "1.0.7"}
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.udp.settimeout(0.2)
        node = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps(node.state).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

        self.http = HTTPServer(("127.0.0.1", 0), H)
        self.stop = False
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        threading.Thread(target=self._artnet, daemon=True).start()

    def _artnet(self):
        while not self.stop:
            try:
                pkt, _ = self.udp.recvfrom(1000)
            except socket.timeout:
                continue
            if pkt.startswith(b"Art-Net\x00") and pkt[18] == 255:
                self.state.update(signal=True, mode="LIVE")

    def press(self):
        self.state["house_forced"] = not self.state["house_forced"]

    def close(self):
        self.stop = True
        self.http.shutdown()


def args_for(node, tmp_path, **kw):
    a = dict(serial="LR-0001", port=None, firmware="x", node=f"127.0.0.1:{node.http.server_port}",
             resident=None, log=str(tmp_path / "log.csv"), wait=3, button_wait=3)
    a.update(kw)
    return types.SimpleNamespace(**a)


def test_a_good_box_passes_and_is_logged(tmp_path, monkeypatch):
    node = FakeNode()
    # Art-Net goes to the fake node's UDP port
    monkeypatch.setattr(lf, "ARTNET_PORT", node.udp.getsockname()[1])
    said = []

    def ask(q):
        if "HOUSE" in q:
            return ""
        return "y"

    def say(line):
        said.append(line)
        if "press it once" in line or "Press it once more" in line:
            threading.Timer(0.3, node.press).start()

    try:
        row = lf.run(args_for(node, tmp_path), lf.Check(ask=ask, say=say))
    finally:
        node.close()
    assert row["result"] == "PASS", said
    assert row["network"] == row["dmx"] == row["dmx_led"] == row["house_button"] == "PASS"
    rows = list(csv.DictReader(open(tmp_path / "log.csv")))
    assert rows[0]["serial"] == "LR-0001" and rows[0]["node_version"] == "1.0.7"


def test_no_button_press_fails(tmp_path, monkeypatch):
    node = FakeNode()
    monkeypatch.setattr(lf, "ARTNET_PORT", node.udp.getsockname()[1])
    try:
        row = lf.run(args_for(node, tmp_path, button_wait=1), lf.Check(ask=lambda q: "y", say=lambda s: None))
    finally:
        node.close()
    assert row["house_button"] == "FAIL" and row["result"] == "FAIL" and "no press seen" in row["notes"]


def test_missing_node_fails_with_a_hint(tmp_path):
    a = types.SimpleNamespace(serial="LR-0002", port=None, firmware="x", node="127.0.0.1:9", resident=None,
                              log=str(tmp_path / "log.csv"), wait=1, button_wait=1)
    row = lf.run(a, lf.Check(ask=lambda q: "y", say=lambda s: None))
    assert row["network"] == "FAIL" and "2.0.0.50" in row["notes"]


def test_flash_reports_missing_files(tmp_path):
    ok, detail = lf.flash("/dev/null", tmp_path)
    assert not ok and "firmware.bin" in detail
