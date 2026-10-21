#!/usr/bin/env python3
"""Lumière Link: the app.

Starts the engine and the private screen server, then opens the screen in a
fixed-size native window (pywebview). `--browser` opens it in the web browser
instead, which is handy for development.
"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

from appserver import AppServer
from engine import LumiereEngine, app_data_dir

WINDOW_SIZE = (1400, 830)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lumière Link")
    ap.add_argument("--demo", action="store_true", help="start in demo mode (a pretend booth)")
    ap.add_argument("--browser", action="store_true", help="open the screen in the web browser")
    ap.add_argument("--port", type=int, default=0, help="screen server port (default: any free port)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    log_dir = app_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(log_dir / "lumiere.log"), logging.StreamHandler()])

    engine = LumiereEngine(demo=True if args.demo else None)
    engine.start()
    server = AppServer(engine, port=args.port)
    server.start()
    logging.info("Lumière Link is running (screen at 127.0.0.1:%d)", server.port)

    try:
        if args.browser:
            webbrowser.open(server.url)
            print(f"Open {server.url} if the browser didn't. Press Ctrl-C to quit.")
            import time
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


if __name__ == "__main__":
    sys.exit(main())
