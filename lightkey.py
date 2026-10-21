"""Which project Lightkey has open, read through macOS System Events.

Lightkey is a standard macOS document app, so its project window carries the
project's name as the window title and the file's location in the window's
AXDocument accessibility attribute. Reading them needs the user's one-time
OK in the macOS permission prompt (Automation, and possibly Accessibility).
We only read: Lightkey's files are never touched.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

log = logging.getLogger("prolink_sync.lightkey")

OPEN, NOT_RUNNING, NO_WINDOW, PERMISSION, UNSUPPORTED, ERROR = (
    "open", "not_running", "no_window", "permission", "unsupported", "error")

# Prefer a window that has a document (palettes and inspectors have none).
SCRIPT = '''
tell application "System Events"
    if not (exists process "Lightkey") then return "NOT_RUNNING"
    tell process "Lightkey"
        if (count of windows) is 0 then return "NO_WINDOW"
        repeat with w in windows
            try
                set d to value of attribute "AXDocument" of w
                if d is not missing value and d is not "" then return (name of w) & linefeed & d
            end try
        end repeat
        return (name of window 1) & linefeed & ""
    end tell
end tell
'''

PERMISSION_ADVICE = (
    "macOS hasn't allowed reading which Lightkey project is open. Open System Settings > "
    "Privacy & Security > Automation and switch on System Events under Terminal (or Lumière Link), "
    "and check Accessibility too. Until then, pick the project yourself.")


@dataclass(frozen=True)
class LightkeyState:
    status: str
    name: str = ""      # project name, cleaned up
    path: str = ""      # project file path, when macOS reports it
    detail: str = ""    # raw title or error text, for the probe

    @property
    def key(self) -> tuple:
        return (self.status, self.name, self.path)


def clean_name(title: str) -> str:
    """'Club Night.lightkey — Edited' -> 'Club Night'."""
    name = re.sub(r"\s+[—–-]\s+(Edited|Bearbeitet|Modifié|Modificado)\s*$", "", title.strip(), flags=re.I)
    name = re.sub(r"\.lightkey(project)?$", "", name, flags=re.I)
    return name.strip()


def parse(stdout: str) -> LightkeyState:
    out = stdout.strip("\n")
    if out == "NOT_RUNNING":
        return LightkeyState(NOT_RUNNING)
    if out == "NO_WINDOW":
        return LightkeyState(NO_WINDOW)
    title, _, doc = out.partition("\n")
    path = ""
    doc = doc.strip()
    if doc and doc != "missing value":
        path = unquote(urlparse(doc).path) if doc.startswith("file:") else doc
    name = Path(path).stem if path else clean_name(title)
    if not name:
        return LightkeyState(NO_WINDOW, detail=title)
    return LightkeyState(OPEN, name, path, detail=title)


def _osascript(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=4)


def read_open_project(run: Callable[[str], subprocess.CompletedProcess] = _osascript,
                      platform: str = sys.platform) -> LightkeyState:
    if platform != "darwin":
        return LightkeyState(UNSUPPORTED, detail="only available on macOS")
    try:
        result = run(SCRIPT)
    except (OSError, subprocess.SubprocessError) as e:
        return LightkeyState(ERROR, detail=str(e))
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        # -1743: not allowed to send Apple events; -1719 / -25211: no accessibility access
        if re.search(r"-1743|-1719|-25211|not allowed|assistive access|not authori[sz]ed", err, re.I):
            return LightkeyState(PERMISSION, detail=err)
        return LightkeyState(ERROR, detail=err)
    return parse(result.stdout)


class LightkeyWatcher:
    """Polls Lightkey every few seconds and reports when the open project changes."""

    def __init__(self, on_change: Callable[[LightkeyState], None], interval: float = 3.0,
                 read: Callable[[], LightkeyState] = read_open_project):
        self.on_change = on_change
        self.interval = interval
        self.read = read
        self.alive = True
        self.last: Optional[LightkeyState] = None
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self.alive = False
        self._stop.set()

    def poll_once(self) -> LightkeyState:
        state = self.read()
        if self.last is None or state.key != self.last.key:
            self.last = state
            self.on_change(state)
        return state

    def _run(self) -> None:
        while self.alive:
            state = self.poll_once()
            if state.status in (PERMISSION, UNSUPPORTED):
                return  # report once; no point asking again and again
            self._stop.wait(self.interval)


def probe() -> int:
    """Print exactly what macOS reports, for checking a new Mac or Lightkey version."""
    state = read_open_project()
    print(f"Status:  {state.status}")
    if state.status == OPEN:
        print(f"Project: {state.name}")
        print(f"File:    {state.path or '(macOS did not report the file path; the name is enough)'}")
        print(f"Window:  {state.detail}")
        print("\nAll good: Lumière Link can follow your Lightkey projects.")
    elif state.status == NOT_RUNNING:
        print("Lightkey isn't running. Open a project in Lightkey and run this again.")
    elif state.status == NO_WINDOW:
        print("Lightkey is running but has no project window open. Open a project and run this again.")
    elif state.status == PERMISSION:
        print(PERMISSION_ADVICE)
        print(f"\n(macOS said: {state.detail})")
    else:
        print(f"Couldn't read Lightkey: {state.detail}")
    return 0 if state.status == OPEN else 1
