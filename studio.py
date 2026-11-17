"""Lumière Studio: set up the venue's lights simply, and let QLC+ do the output.

The technician describes each kind of light once (a "profile": what each DMX
channel does), patches the lights (address and group), and Studio writes the
QLC+ files: a fixture definition per profile and the show with every light
patched. QLC+ still sends the DMX (Art-Net to the Lumière Node).

Unbranded lights rarely come with a manual, so profiles start from templates
for the common kinds (RGB pars, RGBW pars, LED bars, strobes, basic moving
heads), or are found channel by channel with the light finder.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

MANUFACTURER = "Lumiere"
DMX_CHANNELS = 512

# What a channel can do: label, QLC+ preset (or None) and QLC+ group for channels
# without a preset.
ROLES = {
    "dimmer": ("Dimmer", "IntensityMasterDimmer", None),
    "red": ("Red", "IntensityRed", None),
    "green": ("Green", "IntensityGreen", None),
    "blue": ("Blue", "IntensityBlue", None),
    "white": ("White", "IntensityWhite", None),
    "amber": ("Amber", "IntensityAmber", None),
    "uv": ("UV", "IntensityUV", None),
    "strobe": ("Strobe", "ShutterStrobeSlowFast", None),
    "speed": ("Speed", None, "Speed"),
    "mode": ("Mode / programs", None, "Effect"),
    "pan": ("Pan", "PositionPan", None),
    "tilt": ("Tilt", "PositionTilt", None),
    "colour_wheel": ("Colour wheel", None, "Colour"),
    "gobo": ("Gobo", None, "Gobo"),
    "nothing": ("Nothing", None, "Nothing"),
}
# Roles that make up a segment (a "head") of a multi-segment light
SEGMENT_ROLES = ("dimmer", "red", "green", "blue", "white", "amber", "uv")
KINDS = {"par": "Color Changer", "bar": "LED Bar (Pixels)", "strobe": "Strobe",
         "moving_head": "Moving Head", "other": "Other"}


@dataclass
class Channel:
    role: str
    segment: Optional[int] = None   # 1-based segment of a bar, None for whole-light channels

    @property
    def label(self) -> str:
        name = ROLES[self.role][0]
        return f"{name} {self.segment}" if self.segment else name


@dataclass
class Profile:
    """One kind of light: its name and what each DMX channel does, in order."""
    name: str
    kind: str = "par"
    channels: list[Channel] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.channels)

    @property
    def segments(self) -> int:
        return max((c.segment or 0 for c in self.channels), default=0)

    def check(self) -> list[str]:
        problems = []
        if not self.name.strip():
            problems.append("Give the light a name.")
        if not 1 <= self.count <= DMX_CHANNELS:
            problems.append("A light needs between 1 and 512 channels.")
        if self.kind not in KINDS:
            problems.append(f"Unknown kind of light: {self.kind}")
        for i, c in enumerate(self.channels, 1):
            if c.role not in ROLES:
                problems.append(f"Channel {i}: unknown role {c.role!r}")
        labels = [c.label for c in self.channels if c.role != "nothing"]
        dupes = sorted({x for x in labels if labels.count(x) > 1})
        if dupes:
            problems.append("These appear twice: " + ", ".join(dupes))
        return problems

    def to_json(self) -> dict:
        return {"name": self.name, "kind": self.kind,
                "channels": [{"role": c.role, **({"segment": c.segment} if c.segment else {})} for c in self.channels]}

    @classmethod
    def from_json(cls, data: dict) -> "Profile":
        return cls(data["name"], data.get("kind", "par"),
                   [Channel(c["role"], c.get("segment")) for c in data.get("channels", [])])

    @property
    def file_stem(self) -> str:
        return MANUFACTURER + "-" + re.sub(r"[^A-Za-z0-9]+", "-", self.name).strip("-")

    def to_qxf(self) -> str:
        """The profile as a QLC+ fixture definition."""
        names, out = [], []
        for i, c in enumerate(self.channels):
            name = c.label if c.role != "nothing" else f"Unused {i + 1}"
            names.append(name)
            _, preset, group = ROLES[c.role]
            if c.role == "dimmer" and c.segment:
                preset = "IntensityDimmer"
            if preset:
                out.append(f' <Channel Name="{escape(name)}" Preset="{preset}"/>')
            else:
                out.append(f' <Channel Name="{escape(name)}">\n  <Group Byte="0">{group}</Group>\n'
                           f'  <Capability Min="0" Max="255">{escape(ROLES[c.role][0])}</Capability>\n </Channel>')
        mode = [f' <Mode Name="{self.count} channels">']
        mode += [f'  <Channel Number="{i}">{escape(n)}</Channel>' for i, n in enumerate(names)]
        for seg in range(1, self.segments + 1):
            mode.append("  <Head>")
            mode += [f"   <Channel>{i}</Channel>" for i, c in enumerate(self.channels) if c.segment == seg]
            mode.append("  </Head>")
        mode.append(" </Mode>")
        width = max(1, self.segments)
        return "\n".join([
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<!DOCTYPE FixtureDefinition>",
            '<FixtureDefinition xmlns="http://www.qlcplus.org/FixtureDefinition">',
            " <Creator>\n  <Name>Lumière Studio</Name>\n  <Version>1</Version>\n  <Author>Lumière Link</Author>\n </Creator>",
            f" <Manufacturer>{MANUFACTURER}</Manufacturer>",
            f" <Model>{escape(self.name)}</Model>",
            f" <Type>{KINDS[self.kind]}</Type>",
            *out, *mode,
            f' <Physical>\n  <Bulb Type="LED" Lumens="0" ColourTemperature="0"/>\n'
            f'  <Dimensions Weight="2" Width="{200 if width == 1 else 1000}" Height="200" Depth="150"/>\n'
            f'  <Lens Name="Other" DegreesMin="0" DegreesMax="0"/>\n'
            f'  <Focus Type="Fixed" PanMax="0" TiltMax="0"/>\n'
            f'  <Layout Width="{width}" Height="1"/>\n </Physical>',
            "</FixtureDefinition>", ""])


# ----------------------------------------------------------------- templates
def _seg(roles: list[str], segments: int) -> list[Channel]:
    return [Channel(r, s) for s in range(1, segments + 1) for r in roles]


TEMPLATES = {
    "rgb3": ("RGB par, 3 channels", "par", lambda **k: [Channel(r) for r in ("red", "green", "blue")]),
    "rgbw4": ("RGBW par, 4 channels", "par", lambda **k: [Channel(r) for r in ("red", "green", "blue", "white")]),
    "par7": ("RGB par, 7 channels", "par",
             lambda **k: [Channel(r) for r in ("dimmer", "red", "green", "blue", "strobe", "mode", "speed")]),
    "par8": ("RGBW par, 8 channels", "par",
             lambda **k: [Channel(r) for r in ("dimmer", "red", "green", "blue", "white", "strobe", "mode", "speed")]),
    "bar": ("LED bar with segments", "bar",
            lambda segments=8, white=False, header=True, **k:
            ([Channel("dimmer"), Channel("strobe")] if header else [])
            + _seg(["red", "green", "blue"] + (["white"] if white else []), int(segments))),
    "strobe2": ("Strobe, 2 channels", "strobe", lambda **k: [Channel("dimmer"), Channel("strobe")]),
    "moving9": ("Moving head, 9 channels", "moving_head",
                lambda **k: [Channel(r) for r in ("pan", "tilt", "speed", "dimmer", "strobe",
                                                  "red", "green", "blue", "white")]),
}


def from_template(template: str, name: str, **params) -> Profile:
    label, kind, make = TEMPLATES[template]
    return Profile(name or label, kind, make(**params))


# ----------------------------------------------------------------- the patch
@dataclass
class Light:
    id: int
    name: str
    profile: str          # profile name
    address: int          # 1-512
    group: str = "Lights"
    universe: int = 0


@dataclass
class Rig:
    """The venue's lights: profiles (kinds of light) and the patch."""
    profiles: dict[str, Profile] = field(default_factory=dict)
    lights: list[Light] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"profiles": [p.to_json() for p in self.profiles.values()],
                "lights": [asdict(l) for l in self.lights]}

    @classmethod
    def from_json(cls, data: dict) -> "Rig":
        rig = cls()
        for p in (data or {}).get("profiles", []):
            prof = Profile.from_json(p)
            rig.profiles[prof.name] = prof
        rig.lights = [Light(**l) for l in (data or {}).get("lights", [])]
        return rig

    def span(self, light: Light) -> range:
        return range(light.address, light.address + self.profiles[light.profile].count)

    def used(self, skip: Optional[int] = None) -> set[int]:
        return {a for l in self.lights if l.id != skip for a in self.span(l)}

    def next_free(self, count: int) -> Optional[int]:
        used = self.used()
        for start in range(1, DMX_CHANNELS - count + 2):
            if not any(a in used for a in range(start, start + count)):
                return start
        return None

    def check_light(self, light: Light) -> list[str]:
        if light.profile not in self.profiles:
            return [f"There's no light profile called {light.profile!r}."]
        n = self.profiles[light.profile].count
        if not 1 <= light.address <= DMX_CHANNELS - n + 1:
            return [f"Address {light.address} doesn't fit: this light uses {n} channels, so it has to "
                    f"start between 1 and {DMX_CHANNELS - n + 1}."]
        clash = sorted(set(self.span(light)) & self.used(skip=light.id))
        if clash:
            who = [l.name for l in self.lights if l.id != light.id and set(self.span(l)) & set(clash)]
            return [f"Channels {clash[0]}-{clash[-1]} are already used by {', '.join(who)}."]
        return []

    def add(self, name: str, profile: str, address: Optional[int], group: str = "Lights", count: int = 1) -> list[Light]:
        """Patch `count` lights of one profile, one after the other from `address`
        (the next free address if None). Raises ValueError with a readable reason."""
        if profile not in self.profiles:
            raise ValueError(f"There's no light profile called {profile!r}.")
        n = self.profiles[profile].count
        added = []
        for i in range(count):
            addr = (address + i * n) if address else self.next_free(n)
            if addr is None:
                raise ValueError("No room left in the universe for another light like this.")
            light = Light(max([l.id for l in self.lights], default=-1) + 1,
                          f"{name} {i + 1}" if count > 1 else name, profile, addr, group or "Lights")
            problems = self.check_light(light)
            if problems:
                raise ValueError(problems[0])
            self.lights.append(light)
            added.append(light)
        return added

    def groups(self) -> dict[str, list[Light]]:
        out: dict[str, list[Light]] = {}
        for l in sorted(self.lights, key=lambda l: l.address):
            out.setdefault(l.group, []).append(l)
        return out

    # -- QLC+ show
    def to_qxw(self, io_map: str = "") -> str:
        """The QLC+ show with every light patched and a fixture group per group. The
        show's inputs and outputs (`io_map`, the <InputOutputMap> of the current show)
        are kept as they were set up on this machine."""
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<!DOCTYPE Workspace>",
                 '<Workspace xmlns="http://www.qlcplus.org/Workspace" CurrentWindow="VirtualConsole">',
                 " <Creator>\n  <Name>Q Light Controller Plus</Name>\n  <Version>5.3.0</Version>\n"
                 "  <Author>Lumière Studio</Author>\n </Creator>", " <Engine>"]
        if io_map:
            lines.append("  " + io_map.strip())
        for l in sorted(self.lights, key=lambda l: l.id):
            p = self.profiles[l.profile]
            lines.append(f"  <Fixture>\n   <Manufacturer>{MANUFACTURER}</Manufacturer>\n   <Model>{escape(p.name)}</Model>\n"
                         f"   <Mode>{p.count} channels</Mode>\n   <ID>{l.id}</ID>\n   <Name>{escape(l.name)}</Name>\n"
                         f"   <Universe>{l.universe}</Universe>\n   <Address>{l.address - 1}</Address>\n"
                         f"   <Channels>{p.count}</Channels>\n  </Fixture>")
        for gid, (group, members) in enumerate(self.groups().items()):
            heads, x = [], 0
            for l in members:
                for h in range(max(1, self.profiles[l.profile].segments)):
                    heads.append(f'   <Head X="{x}" Y="0" Fixture="{l.id}">{h}</Head>')
                    x += 1
            lines.append(f'  <FixtureGroup ID="{gid}">\n   <Name>{escape(group)}</Name>\n'
                         f'   <Size X="{max(1, x)}" Y="1"/>\n' + "\n".join(heads) + "\n  </FixtureGroup>")
        lines += [" </Engine>", "</Workspace>", ""]
        return "\n".join(lines)

    def channels_for(self, roles: dict[str, int], light: Optional[Light] = None) -> dict[int, int]:
        """DMX values (absolute address -> value) that set each matching channel of the
        lights (or one light) to the value for its role: {"dimmer": 255, "red": 255}."""
        out = {}
        for l in ([light] if light else self.lights):
            for i, c in enumerate(self.profiles[l.profile].channels):
                out[l.address + i] = roles.get(c.role, 0)
        return out


def io_map_of(show_xml: str) -> str:
    """The <InputOutputMap> section of a QLC+ show, or "" if it has none."""
    m = re.search(r"<InputOutputMap\b.*?</InputOutputMap>|<InputOutputMap\b[^>]*/>", show_xml, re.S)
    return m.group(0) if m else ""


def write_show(rig: Rig, show_path: Path, fixtures_dir: Path) -> dict:
    """Write the fixture definitions and the show for QLC+, keeping the current
    show's inputs and outputs and a backup of it. Returns what was written."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for p in rig.profiles.values():
        path = fixtures_dir / f"{p.file_stem}.qxf"
        path.write_text(p.to_qxf())
        written.append(path.name)
    io_map, backup = "", None
    if show_path.exists():
        old = show_path.read_text(errors="replace")
        io_map = io_map_of(old)
        backup = show_path.with_name(f"{show_path.stem}-before-studio-{time.strftime('%Y%m%d-%H%M%S')}.qxw")
        shutil.copy2(show_path, backup)
    show_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = show_path.with_suffix(".tmp")
    tmp.write_text(rig.to_qxw(io_map))
    tmp.replace(show_path)
    return {"fixtures": written, "show": str(show_path), "backup": str(backup) if backup else None,
            "kept_io": bool(io_map)}


def load_rig(path: Path) -> Rig:
    try:
        return Rig.from_json(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError, TypeError):
        return Rig()


def save_rig(rig: Rig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rig.to_json(), indent=1))
    tmp.replace(path)
