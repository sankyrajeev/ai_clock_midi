#!/usr/bin/env python3
"""Lumière Link: the app.

Starts the engine and the private screen server, then opens the screen in a
fixed-size native window (pywebview). `--browser` opens it in the web browser
instead, which is handy for development. `--headless` runs with no window at
all, as on the Lumière Resident's Pi: the screen is then opened from a phone or
laptop on the same network, with a link that stays the same across restarts.
"""

from __future__ import annotations

import argparse
import logging
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from appserver import AppServer
from engine import VERSION, LumiereEngine, app_data_dir

WINDOW_SIZE = (1400, 830)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lumière Link")
    ap.add_argument("--demo", action="store_true", help="start in demo mode (a pretend booth)")
    ap.add_argument("--browser", action="store_true", help="open the screen in the web browser")
    ap.add_argument("--port", type=int, default=0, help="screen server port (default: any free port)")
    ap.add_argument("--headless", action="store_true",
                    help="no window: serve the screen to other devices on the network (Lumière Resident)")
    ap.add_argument("--host", default=None, help="address to serve the screen on (default: this computer only; "
                                                 "0.0.0.0 with --headless)")
    ap.add_argument("--net-led", type=int, default=None, metavar="GPIO",
                    help="drive a status LED on this Raspberry Pi GPIO (BCM number, e.g. 17)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    log_dir = app_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(log_dir / "lumiere.log"), logging.StreamHandler()])

    engine = LumiereEngine(demo=True if args.demo else None)
    engine.start()
    host = args.host or ("0.0.0.0" if args.headless else "127.0.0.1")
    token = screen_token(log_dir) if args.headless else None
    server = AppServer(engine, port=args.port, token=token, host=host)
    server.start()
    logging.info("Lumière Link %s is running (screen at %s:%d)", VERSION, host, server.port)
    if args.net_led is not None:
        NetLed(engine, args.net_led).start()

    try:
        if args.headless:
            url = f"http://{socket.gethostname()}.local:{server.port}/?t={server.token}"
            logging.info("Open the screen from a phone or laptop on this network: %s", url)
            (log_dir / "screen-link.txt").write_text(url + "\n")
            while True:
                time.sleep(3600)
        elif args.browser:
            webbrowser.open(server.url)
            print(f"Open {server.url} if the browser didn't. Press Ctrl-C to quit.")
            while True:
                time.sleep(3600)
        else:
            import webview  # pywebview

            webview.create_window("Lumière Link", server.url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1],
                                  resizable=False, background_color="#101010", text_select=False)
            webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        engine.stop()
    return 0


def screen_token(data_dir: Path) -> str:
    """The screen's password, kept across restarts so a bookmarked link keeps working."""
    path = data_dir / "screen-token"
    try:
        token = path.read_text().strip()
        if len(token) >= 16:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(18)
    path.write_text(token + "\n")
    path.chmod(0o600)
    return token


class NetLed:
    """The Resident's NET LED: solid while players are sending their status (Lumière
    Link is following the music), blinking while it runs but sees no players, off
    when Lumière Link isn't running (the service turns it off on exit)."""

    def __init__(self, engine, gpio: int):
        self.engine, self.gpio = engine, gpio

    def start(self) -> None:
        try:
            from gpiozero import LED
            self.led = LED(self.gpio)
        except Exception as e:  # noqa: BLE001 - no GPIO here (not a Pi): just say so
            logging.warning("NET LED on GPIO %d unavailable: %s", self.gpio, e)
            return
        threading.Thread(target=self._run, daemon=True).start()

    def players_seen(self) -> bool:
        vcdj = getattr(self.engine, "vcdj", None)
        return bool(vcdj and vcdj.status_seen and any(1 <= n <= 6 for n in vcdj.devices))

    def _run(self) -> None:
        state = None
        while True:
            want = "on" if self.players_seen() else "blink"
            if want != state:
                state = want
                if want == "on":
                    self.led.on()
                else:
                    self.led.blink(on_time=0.5, off_time=0.5)
            time.sleep(1.0)


def self_test() -> int:
    """Used by the build: checks the bundled app has everything it needs."""
    import json
    import tempfile
    import time
    import urllib.request
    from pathlib import Path

    import rtmidi  # noqa: F401  (MIDI ports for Lightkey)
    import webview  # noqa: F401  (the window library must be bundled)

    tmp = Path(tempfile.mkdtemp())
    engine = LumiereEngine(data_dir=tmp / "data", projects_dir=tmp / "projects", demo=True)
    engine.start()
    server = AppServer(engine)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Lumi" in page, "screen missing"
        for asset in ("/fonts/Manrope.ttf", "/assets/brand/va-logo-white.png"):
            urllib.request.urlopen(base + asset).read()
        req = urllib.request.Request(base + "/api/state", headers={"X-Token": server.token})
        for _ in range(40):
            state = json.loads(urllib.request.urlopen(req).read())
            if any(d.get("title") for d in state["decks"]):
                break
            time.sleep(0.5)
        else:
            raise AssertionError("demo decks never loaded a track")
        print("self-test ok:", [d["title"] for d in state["decks"] if d.get("title")])
        return 0
    finally:
        server.stop()
        engine.stop()


if __name__ == "__main__":
    sys.exit(main())
