"""The private link between the app window and the engine.

A tiny web server on 127.0.0.1 (this Mac only) serves the screen and a JSON
API. Every API call must carry a random token that only the app's own
window knows, so other software on the Mac can't send it commands.

    GET  /                      the screen (ui/index.html)
    GET  /assets/...            fonts, logos
    GET  /api/state             snapshot of everything on screen
    GET  /api/waveform?key=...  a deck's waveform and beat grid
    POST /api/action            {"name": "...", "args": {...}}
"""

from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("prolink_sync.server")

UI_DIR = Path(__file__).with_name("ui")
ASSET_DIRS = {"assets": Path(__file__).with_name("assets"), "fonts": UI_DIR / "fonts"}


class AppServer:
    def __init__(self, engine, port: int = 0, token: Optional[str] = None):
        self.engine = engine
        self.token = token or secrets.token_urlsafe(24)
        handler = self._handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def start(self) -> None:
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # keep the console quiet
                log.debug("http: " + fmt, *args)

            def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, data) -> None:
                self._send(code, json.dumps(data).encode())

            def _authorised(self) -> bool:
                if secrets.compare_digest(self.headers.get("X-Token", ""), server.token):
                    return True
                self._json(403, {"error": "not allowed"})
                return False

            def do_GET(self):  # noqa: N802
                try:
                    self._get()
                except Exception:  # noqa: BLE001 - never let one bad request go unexplained
                    log.exception("Error answering %s", self.path)
                    self._json(500, {"error": "internal error, see lumiere.log"})

            def _get(self):
                url = urlparse(self.path)
                if url.path == "/favicon.ico":
                    return self._send(200, (ASSET_DIRS["assets"] / "brand" / "va-mark-white.png").read_bytes(), "image/png")
                if url.path in ("/", "/index.html"):
                    page = (UI_DIR / "index.html").read_bytes()
                    return self._send(200, page, "text/html; charset=utf-8")
                parts = url.path.strip("/").split("/", 1)
                if parts[0] in ASSET_DIRS and len(parts) == 2:
                    base = ASSET_DIRS[parts[0]].resolve()
                    target = (base / parts[1]).resolve()
                    if base in target.parents and target.is_file():
                        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                        return self._send(200, target.read_bytes(), ctype)
                    return self._json(404, {"error": "not found"})
                if url.path == "/api/state":
                    if self._authorised():
                        self._json(200, server.engine.snapshot())
                    return
                if url.path == "/api/waveform":
                    if self._authorised():
                        key = parse_qs(url.query).get("key", [""])[0]
                        data = server.engine.waveform(key)
                        self._json(200 if data else 404, data or {"error": "no waveform yet"})
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                if urlparse(self.path).path != "/api/action":
                    return self._json(404, {"error": "not found"})
                if not self._authorised():
                    return
                try:
                    length = min(int(self.headers.get("Content-Length", 0)), 64 * 1024)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    result = server.engine.action(body["name"], body.get("args") or {})
                    self._json(200, {"ok": True, "result": result})
                except (ValueError, KeyError, TypeError) as e:
                    self._json(400, {"ok": False, "error": str(e)})
                except Exception as e:  # noqa: BLE001
                    log.exception("Action %r failed", self.path)
                    self._json(500, {"ok": False, "error": f"Something went wrong ({e})"})

        return Handler
