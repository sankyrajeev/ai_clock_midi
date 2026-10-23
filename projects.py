"""Lumière Link projects: one per show, paired with a Lightkey project.

Each project is a small JSON file ending in .lumiere, kept in
~/Documents/Lumiere Link/ so it's easy to find, back up or copy to another
Mac. It holds everything that belongs to one show: how many sequences each
pool has, how they're picked, which ones have been MIDI-learned, and which
Lightkey project it goes with. Changes are saved straight away.

When Lightkey's open project can be read (see lightkey.py), the matching
Lumière project is chosen automatically, and created if it doesn't exist yet.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pools as pl
from lightkey import NO_WINDOW, NOT_RUNNING, OPEN, PERMISSION, PERMISSION_ADVICE, LightkeyState

log = logging.getLogger("prolink_sync.projects")

EXT = ".lumiere"
DEFAULT_DIR = Path.home() / "Documents" / "Lumiere Link"
NAME_TIP = "Tip: give it the same name as your Lightkey project, so the two stay paired."


@dataclass
class Project:
    name: str
    config: dict = field(default_factory=lambda: copy.deepcopy(pl.DEFAULT_CONFIG))
    learned: dict[str, list[int]] = field(default_factory=dict)
    lightkey_name: str = ""
    lightkey_path: str = ""

    def to_json(self) -> dict:
        return {
            "_help": "Lumière Link project. Edit it in the app (or carefully here). "
                     "See README.md, 'Projects'.",
            "name": self.name,
            "lightkey": {"project_name": self.lightkey_name, "project_path": self.lightkey_path},
            "mode": self.config.get("mode", "shuffle"),
            "pair_builds_with_drops": self.config.get("pair_builds_with_drops", False),
            "pools": self.config.get("pools", {}),
            "learned": {k: sorted(set(v)) for k, v in self.learned.items() if v},
        }

    @classmethod
    def from_json(cls, data: dict, fallback_name: str) -> "Project":
        lk = data.get("lightkey") or {}
        config = {"mode": data.get("mode", "shuffle"),
                  "pair_builds_with_drops": data.get("pair_builds_with_drops", False),
                  "pools": data.get("pools", copy.deepcopy(pl.DEFAULT_CONFIG["pools"]))}
        learned = {k: [int(n) for n in v] for k, v in (data.get("learned") or {}).items()}
        return cls(data.get("name") or fallback_name, config, learned,
                   lk.get("project_name", ""), lk.get("project_path", ""))

    def summary(self) -> str:
        counts = [(pl.LABELS[k], v if isinstance(v, int) else v.get("count", 0))
                  for k, v in self.config.get("pools", {}).items() if k in pl.LABELS]
        on = [f"{label} {n}" for label, n in counts if n]
        return ", ".join(on) if on else "no sequence pools"


def normalise(name: str) -> str:
    """Compare names the way a person would: case, extension and ' — Edited' don't matter."""
    from lightkey import clean_name
    return re.sub(r"\s+", " ", clean_name(name)).casefold()


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "-", name).strip(" .") or "Untitled"
    return cleaned[:120]


class ProjectStore:
    def __init__(self, directory: Path = DEFAULT_DIR):
        self.dir = Path(directory)

    def path_for(self, name: str) -> Path:
        return self.dir / (safe_filename(name) + EXT)

    def all(self) -> list[Project]:
        if not self.dir.is_dir():
            return []
        projects = []
        for f in sorted(self.dir.glob("*" + EXT)):
            p = self._read(f)
            if p:
                projects.append(p)
        return projects

    def names(self) -> list[str]:
        return [p.name for p in self.all()]

    def _read(self, path: Path) -> Optional[Project]:
        try:
            return Project.from_json(json.loads(path.read_text()), path.stem)
        except (OSError, ValueError) as e:
            log.warning("Skipping unreadable project %s (%s)", path.name, e)
            return None

    def load(self, name: str) -> Optional[Project]:
        exact = self.path_for(name)
        if exact.exists():
            return self._read(exact)
        want = normalise(name)
        return next((p for p in self.all() if normalise(p.name) == want), None)

    def save(self, project: Project) -> Path:
        """Write atomically: a crash mid-save never leaves a half-written project."""
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.path_for(project.name)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".saving-", suffix=EXT)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(project.to_json(), f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return target

    def create(self, name: str, base: Optional[Project] = None,
               lightkey_name: str = "", lightkey_path: str = "") -> Project:
        project = Project(name,
                          copy.deepcopy(base.config) if base else copy.deepcopy(pl.DEFAULT_CONFIG),
                          copy.deepcopy(base.learned) if base else {},
                          lightkey_name, lightkey_path)
        self.save(project)
        return project

    def find_for(self, lightkey_name: str, lightkey_path: str = "") -> Optional[Project]:
        """The project paired with a Lightkey project: by file path first, then by name."""
        projects = self.all()
        if lightkey_path:
            for p in projects:
                if p.lightkey_path and p.lightkey_path == lightkey_path:
                    return p
        want = normalise(lightkey_name)
        for p in projects:
            if normalise(p.lightkey_name or p.name) == want or normalise(p.name) == want:
                return p
        return None

    def ensure_default(self, legacy_pools_json: Optional[Path] = None) -> Project:
        """First run: turn an old pools.json into a project called 'Default'."""
        existing = self.load("Default")
        if existing:
            return existing
        config = copy.deepcopy(pl.DEFAULT_CONFIG)
        if legacy_pools_json and legacy_pools_json.exists():
            try:
                old = json.loads(legacy_pools_json.read_text())
                config.update({k: old[k] for k in ("mode", "pair_builds_with_drops", "pools") if k in old})
                log.info("Moved your pool settings from %s into the project 'Default'", legacy_pools_json.name)
            except (OSError, ValueError):
                pass
        project = Project("Default", config)
        self.save(project)
        return project


class ProjectManager:
    """Holds the active project and its sequence pools, and switches when Lightkey does."""

    def __init__(self, store: ProjectStore, project: Project,
                 on_switch: Optional[Callable[[Project, "pl.SequencePools"], None]] = None):
        self.store = store
        self.lock = threading.Lock()
        self.on_switch = on_switch
        self.project = project
        self.pools = pl.SequencePools(project.config)
        self.lightkey_note = ""  # short status for the status line
        self.persist = True      # False for a one-off settings file that isn't a saved project

    def switch(self, project: Project, reason: str) -> None:
        with self.lock:
            if project.name == self.project.name:
                self.project = project  # same show: keep the shuffle where it is
                return
            self.project = project
            self.pools = pl.SequencePools(project.config)  # fresh shuffle for the new show
        log.info("%s: using Lumière project '%s' (%s)", reason, project.name, project.summary())
        if self.on_switch:
            self.on_switch(project, self.pools)

    def on_lightkey(self, state: LightkeyState) -> None:
        if state.status == OPEN:
            self.lightkey_note = ""
            match = self.store.find_for(state.name, state.path)
            if match is None:
                # Starts empty: another show's counts and ✓ marks mean nothing in this one.
                match = self.store.create(state.name, lightkey_name=state.name, lightkey_path=state.path)
                log.info("Lightkey opened '%s'. Created an empty Lumière project with the same name "
                         "(saved in %s)", state.name, self.store.dir)
            elif (match.lightkey_path != state.path and state.path) or match.lightkey_name != state.name:
                match.lightkey_name, match.lightkey_path = state.name, state.path or match.lightkey_path
                self.store.save(match)
            self.switch(match, f"Lightkey opened '{state.name}'")
        elif state.status == PERMISSION:
            self.lightkey_note = "pick project manually"
            log.warning("%s Using Lumière project '%s'.", PERMISSION_ADVICE, self.project.name)
        elif state.status in (NOT_RUNNING, NO_WINDOW):
            self.lightkey_note = ""
            log.info("Lightkey %s; using Lumière project '%s' for now",
                     "isn't open" if state.status == NOT_RUNNING else "has no project open", self.project.name)
        else:
            self.lightkey_note = "pick project manually"
            log.info("Can't read Lightkey's project (%s); using '%s'", state.detail or state.status, self.project.name)

    def unmark_learned(self, pool: str, sequence: int) -> None:
        with self.lock:
            done = self.project.learned.get(pool, [])
            if sequence in done:
                done.remove(sequence)
            if self.persist:
                self.store.save(self.project)

    def mark_learned(self, pool: str, sequence: int) -> None:
        with self.lock:
            done = self.project.learned.setdefault(pool, [])
            if sequence not in done:
                done.append(sequence)
            if self.persist:
                self.store.save(self.project)
