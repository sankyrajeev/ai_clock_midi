#!/usr/bin/env python3
"""Lumière factory check: flash and test one Lumière Resident / Node, and log it.

Run on a laptop plugged into the same network switch as the box under test, with
the laptop's Ethernet set to a fixed address on the Node's network (for example
2.0.0.50, mask 255.0.0.0). Needs Python 3.9+; flashing also needs esptool
(`pip install esptool`) and a USB-serial adapter on the Node's programming header.

  # flash the Node firmware, then test it (partner hardware test / our final check)
  python3 lumiere_factory.py --port /dev/cu.usbserial-0001 --firmware node-firmware/

  # test only (Node already flashed); add the Resident's software check
  python3 lumiere_factory.py --resident lumiere-resident.local

Every box gets one row in factory-log.csv: serial number, MAC address, firmware
version and each check as PASS / FAIL.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

NODE_IP = "2.0.0.10"
ARTNET_PORT = 6454
# Where each file of the Node firmware goes in the ESP32's flash (the CI build's files)
FLASH_MAP = (("0x1000", "bootloader.bin"), ("0x8000", "partitions.bin"),
             ("0xe000", "boot_app0.bin"), ("0x10000", "firmware.bin"))
LOG_FIELDS = ["time", "serial", "mac", "node_version", "flash", "network", "dmx",
              "dmx_led", "house_button", "resident", "result", "notes"]

GREEN, RED, BOLD, END = ("\033[32m", "\033[31m", "\033[1m", "\033[0m") if sys.stdout.isatty() else ("",) * 4


# ----------------------------------------------------------------- Art-Net
def artdmx(levels: bytes, universe: int = 0, sequence: int = 0) -> bytes:
    """An Art-Net ArtDmx packet carrying `levels` (up to 512 channels)."""
    data = bytes(levels[:512])
    if len(data) % 2:
        data += b"\x00"           # Art-Net wants an even length
    return (b"Art-Net\x00" + (0x5000).to_bytes(2, "little") + (14).to_bytes(2, "big")
            + bytes([sequence & 0xFF, 0, universe & 0xFF, (universe >> 8) & 0x7F])
            + len(data).to_bytes(2, "big") + data)


class ArtNetSender:
    """Sends one look to the Node, 30 times a second, until stopped."""

    def __init__(self, ip: str, levels: bytes, universe: int = 0):
        self.ip, self.levels, self.universe = ip, levels, universe
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        host = self.ip.split(":")[0]   # --node may carry a web port; Art-Net has its own
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            seq = 1
            while not self._stop.is_set():
                s.sendto(artdmx(self.levels, self.universe, seq), (host, ARTNET_PORT))
                seq = seq % 255 + 1
                self._stop.wait(1 / 30)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=1)


# ----------------------------------------------------------------- the Node
def node_status(ip: str, timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"http://{ip}/status", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (OSError, ValueError):
        return None


def wait_for(fn: Callable[[], Optional[dict]], ok: Callable[[dict], bool], seconds: float,
             every: float = 0.5) -> Optional[dict]:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        st = fn()
        if st is not None and ok(st):
            return st
        time.sleep(every)
    return None


def esptool(args: list[str], timeout: float = 180) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "esptool", *args], capture_output=True, text=True, timeout=timeout)


def read_mac(port: str) -> Optional[str]:
    out = esptool(["--chip", "esp32", "--port", port, "read_mac"], timeout=60)
    m = re.search(r"MAC:\s*([0-9a-f:]{17})", out.stdout + out.stderr, re.I)
    return m.group(1).lower() if m else None


def flash(port: str, firmware: Path) -> tuple[bool, str]:
    missing = [name for _, name in FLASH_MAP if not (firmware / name).is_file()]
    if missing:
        return False, f"missing in {firmware}: {', '.join(missing)}"
    args = ["--chip", "esp32", "--port", port, "--baud", "460800", "write_flash"]
    for offset, name in FLASH_MAP:
        args += [offset, str(firmware / name)]
    out = esptool(args)
    if out.returncode != 0:
        lines = (out.stderr or out.stdout or "esptool failed").strip().splitlines()
        return False, lines[-1] if lines else "esptool failed"
    return True, "flashed"


# ----------------------------------------------------------------- the Resident
def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def resident_running(host: str) -> tuple[bool, str]:
    """Lumière Link's screen (8080) and QLC+'s web page (9999) both answer."""
    link, qlc = port_open(host, 8080), port_open(host, 9999)
    if link and qlc:
        return True, "Lumière Link and QLC+ running"
    return False, f"Lumière Link {'up' if link else 'NOT answering'} (8080), QLC+ {'up' if qlc else 'NOT answering'} (9999)"


# ----------------------------------------------------------------- the run
class Check:
    def __init__(self, ask: Callable[[str], str] = input, say: Callable[[str], None] = print):
        self.ask, self.say = ask, say
        self.row = {k: "" for k in LOG_FIELDS}
        self.notes: list[str] = []

    def result(self, field: str, ok: bool, detail: str = "") -> bool:
        self.row[field] = "PASS" if ok else "FAIL"
        self.say(f"  {(GREEN + 'PASS') if ok else (RED + 'FAIL')}{END}  {field.replace('_', ' ')}"
                 + (f": {detail}" if detail else ""))
        if not ok and detail:
            self.notes.append(f"{field}: {detail}")
        return ok

    def yes(self, question: str) -> bool:
        return self.ask(f"  ? {question} [y/n] ").strip().lower().startswith("y")


def run(args, check: Optional[Check] = None) -> dict:
    c = check or Check()
    say = c.say
    c.row["time"] = datetime.now().isoformat(timespec="seconds")
    c.row["serial"] = args.serial or c.ask("Serial number of this box: ").strip()
    say(f"{BOLD}Lumière factory check · {c.row['serial']}{END}")

    if args.port:
        say("Flashing the Node. It must be in download mode: IO0 held to GND while it powers up "
            "(an auto-reset adapter does this for you)…")
        c.row["mac"] = read_mac(args.port) or ""
        ok, detail = flash(args.port, Path(args.firmware))
        c.result("flash", ok, detail)
        if not ok:
            return finish(c, args)
        say("  If the Node doesn't come up, power the box off and on.")

    say(f"Waiting for the Node at {args.node} (up to {args.wait:.0f} s)…")
    st = wait_for(lambda: node_status(args.node), lambda s: True, args.wait)
    if not c.result("network", st is not None, f"version {st.get('version')}" if st else
                    f"no answer at {args.node}: check the ART-NET cable, and that this laptop has an address like 2.0.0.50 (mask 255.0.0.0)"):
        return finish(c, args)
    c.row["node_version"] = st.get("version", "")

    if st.get("house_forced"):
        say("  The Node is held in HOUSE: press the HOUSE button once to release it.")
        wait_for(lambda: node_status(args.node), lambda s: not s.get("house_forced"), 20)

    say("Sending a full-white test look on DMX 1 (every channel at full)…")
    with ArtNetSender(args.node, bytes([255]) * 512):
        live = wait_for(lambda: node_status(args.node), lambda s: s.get("signal") and s.get("mode") == "LIVE", 5)
        got = c.result("dmx", bool(live) and c.yes("Are the test lights (or the DMX tester) at full on DMX 1?"),
                       "" if live else "the Node isn't receiving the test look")
        if got:
            c.result("dmx_led", c.yes("Is the amber DMX LED on, steady?"))

        say("HOUSE button: press it once now.")
        pressed = wait_for(lambda: node_status(args.node), lambda s: s.get("house_forced"), args.button_wait)
        released = None
        if pressed:
            say("  Seen. Press it once more to go back to the show.")
            released = wait_for(lambda: node_status(args.node), lambda s: not s.get("house_forced"), args.button_wait)
        c.result("house_button", bool(pressed and released),
                 "" if pressed and released else "no press seen" if not pressed else "second press not seen")

    if args.resident:
        ok, detail = resident_running(args.resident)
        if ok:
            ok = c.yes("Is the green NET LED on (steady or blinking)?")
            detail = detail if ok else "NET LED not on"
        c.result("resident", ok, detail)
    return finish(c, args)


def finish(c: Check, args) -> dict:
    checks = [c.row[k] for k in ("flash", "network", "dmx", "dmx_led", "house_button", "resident") if c.row[k]]
    c.row["result"] = "PASS" if checks and all(v == "PASS" for v in checks) else "FAIL"
    c.row["notes"] = "; ".join(c.notes)
    log = Path(args.log)
    new = not log.exists()
    with log.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(c.row)
    colour = GREEN if c.row["result"] == "PASS" else RED
    c.say(f"\n{BOLD}{colour}{c.row['result']}{END}  {c.row['serial']}  (saved to {log})")
    return c.row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Flash and test one Lumière box, and log the result.")
    ap.add_argument("--serial", help="serial number (asked if left out)")
    ap.add_argument("--port", help="USB-serial port of the Node's programming header: flash it first")
    ap.add_argument("--firmware", default="node-firmware",
                    help="folder with bootloader.bin, partitions.bin, boot_app0.bin, firmware.bin")
    ap.add_argument("--node", default=NODE_IP, help="the Node's address (default 2.0.0.10)")
    ap.add_argument("--resident", help="also check the Resident's software, e.g. lumiere-resident.local")
    ap.add_argument("--log", default="factory-log.csv", help="CSV file to add the result to")
    ap.add_argument("--wait", type=float, default=60, help="seconds to wait for the Node to answer")
    ap.add_argument("--button-wait", type=float, default=20, help="seconds to wait for each button press")
    args = ap.parse_args(argv)
    return 0 if run(args)["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
