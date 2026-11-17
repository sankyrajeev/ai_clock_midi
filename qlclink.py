"""Talking to the running QLC+ (4 or 5) through its web interface.

QLC+ started with --web listens on port 9999. Its WebSocket (/qlcplusWS) takes
"CH|<address>|<value>" to set a DMX channel on its Simple Desk (the address
counts on across universes: universe 2 channel 1 is 513), and "QLC+API|..."
requests. Studio uses it for test looks and the light finder; the show itself is
loaded by writing it to disk and restarting QLC+ (custom fixture definitions are
only read when QLC+ starts).
"""

from __future__ import annotations

import base64
import os
import signal
import socket
import struct
import subprocess
import time
from typing import Optional


class QlcLink:
    def __init__(self, host: str = "127.0.0.1", port: int = 9999, timeout: float = 3.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock: Optional[socket.socket] = None

    # -- the WebSocket, kept open between commands
    def _connect(self) -> socket.socket:
        if self.sock:
            return self.sock
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET /qlcplusWS HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = s.recv(1024)
            if not chunk:
                break
            reply += chunk
        if b" 101 " not in reply.split(b"\r\n", 1)[0]:
            s.close()
            raise ConnectionError("QLC+ didn't accept the connection (is it running with its web page on?)")
        self.sock = s
        return s

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send(self, text: str) -> None:
        """Send one text message; reconnects once if QLC+ was restarted."""
        payload = text.encode()
        head = bytes([0x81])
        n = len(payload)
        head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
        mask = os.urandom(4)
        frame = head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        for attempt in (1, 2):
            try:
                self._connect().sendall(frame)
                return
            except OSError:
                self.close()
                if attempt == 2:
                    raise

    def set_channels(self, values: dict[int, int]) -> None:
        """Set DMX channels (absolute address from 1 -> 0-255) on QLC+'s Simple Desk."""
        for addr, value in sorted(values.items()):
            self.send(f"CH|{addr}|{max(0, min(255, int(value)))}")

    def release(self, universe: int = 0) -> None:
        """Hand the channels back to the show (clear the Simple Desk for a universe)."""
        self.send(f"QLC+API|sdResetUniverse|{universe}")

    def running(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=1.0):
                return True
        except OSError:
            return False


def restart_qlcplus(names: tuple[str, ...] = ("qlcplus5", "qlcplus")) -> bool:
    """Stop the running QLC+ so its service starts it again with the new show and
    fixture definitions (the Resident's lumiere-qlcplus service restarts it within
    3 s). Only touches QLC+ processes of this user. True if one was stopped."""
    try:
        out = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "|".join(names)],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    pids = [int(p) for p in out.split()]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    return bool(pids)


def wait_for_qlcplus(link: QlcLink, seconds: float = 30.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if link.running():
            return True
        time.sleep(0.5)
    return False
