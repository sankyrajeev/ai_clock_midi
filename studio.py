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
import sys
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


def auto_segments(channels: list[Channel]) -> Optional[list[Channel]]:
    """Work out the segments of an LED bar from repeated channels (what the light
    finder gives: Dimmer, Amber, Dimmer, Amber ...). The repeats set
    how many segments there are (the fewest repeats of any repeated role); each
    repeat starts the next segment. A segment role
    found once (or once more than the others, first) is for the whole light, like a
    master dimmer. None if the pattern doesn't add up."""
    counts: dict[str, int] = {}
    for c in channels:
        if c.role in SEGMENT_ROLES:
            counts[c.role] = counts.get(c.role, 0) + 1
    k = min((n for n in counts.values() if n >= 2), default=0)
    if k < 2:
        return None
    whole_first = {r for r, n in counts.items() if n == k + 1}
    if any(n not in (1, k, k + 1) for n in counts.values()):
        return None
    seen: dict[str, int] = {}
    out = []
    for c in channels:
        n = counts.get(c.role, 0)
        if c.role not in SEGMENT_ROLES or n == 1:
            out.append(Channel(c.role))
            continue
        seen[c.role] = seen.get(c.role, 0) + 1
        i = seen[c.role] - (1 if c.role in whole_first else 0)
        out.append(Channel(c.role, i if i >= 1 else None))
    return out


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


NODE_IP = "2.0.0.10"       # the Lumière Node: Art-Net in, DMX out


def local_ip_for(target: str) -> Optional[str]:
    """This computer's address on the network that reaches `target` (no packet is
    sent: the system just picks the route)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target, 6454))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


def with_node_output(io_map: str, local_ip: str, node_ip: str = NODE_IP) -> str:
    """The show's <InputOutputMap> with universe 1 sending Art-Net to the Node from
    `local_ip` (QLC+ finds the network port by its address), all 512 channels.
    Other universes and every input (MIDI from Lumière Link) are kept."""
    out = (f'<Output Plugin="ArtNet" Name="{local_ip}" UID="{local_ip}" Line="0">\n'
           f'     <PluginParameters outputIP="{node_ip}"/>\n    </Output>')
    if not io_map:
        io_map = "<InputOutputMap>\n </InputOutputMap>"
    if io_map.endswith("/>"):
        io_map = io_map[:-2].rstrip() + ">\n </InputOutputMap>"
    m = re.search(r'<Universe\b[^>]*\bID="0"[^>]*?(/>|>(.*?)</Universe>)', io_map, re.S)
    if not m:
        uni = f'<Universe Name="Universe 1" ID="0">\n    {out}\n   </Universe>'
        return io_map.replace("</InputOutputMap>", f"  {uni}\n </InputOutputMap>", 1)
    head = re.match(r"<Universe\b[^>]*?(?=/?>)", m.group(0)).group(0)
    inner = re.sub(r"<Output\b[^>]*/>|<Output\b[^>]*[^/]>.*?</Output>", "", m.group(2) or "", flags=re.S)
    inner = re.sub(r"\n\s*\n", "\n", inner).rstrip()
    return io_map[:m.start()] + f"{head}>{inner}\n    {out}\n   </Universe>" + io_map[m.end():]


def write_show(rig: Rig, show_path: Path, fixtures_dir: Path, local_ip: Optional[str] = None) -> dict:
    """Write the fixture definitions and the show for QLC+, keeping the current
    show's inputs and outputs and a backup of it. Returns what was written."""
    show_path, fixtures_dir = Path(show_path), Path(fixtures_dir)
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    show_path.parent.mkdir(parents=True, exist_ok=True)
    written = []
    for p in rig.profiles.values():
        # QLC+'s own fixtures folder, plus a copy next to the show: QLC+ looks there
        # when a definition isn't in its folders (e.g. when it runs as root).
        for folder in dict.fromkeys((fixtures_dir, show_path.parent)):
            (folder / f"{p.file_stem}.qxf").write_text(p.to_qxf())
        written.append(f"{p.file_stem}.qxf")
    io_map, backup = "", None
    if show_path.exists():
        old = show_path.read_text(errors="replace")
        io_map = io_map_of(old)
        backup = show_path.with_name(f"{show_path.stem}-before-studio-{time.strftime('%Y%m%d-%H%M%S')}.qxw")
        shutil.copy2(show_path, backup)
    kept_io = bool(io_map)
    local_ip = local_ip or local_ip_for(NODE_IP)
    if local_ip:   # always send to the Node through the port that reaches it
        io_map = with_node_output(io_map, local_ip)
    show_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = show_path.with_suffix(".tmp")
    tmp.write_text(rig.to_qxw(io_map))
    tmp.replace(show_path)
    return {"fixtures": written, "show": str(show_path), "backup": str(backup) if backup else None,
            "kept_io": kept_io, "node_output": f"{local_ip} → {NODE_IP}" if local_ip else None}


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


# ----------------------------------------------------------------- the desk
def qlc_fixtures_dir() -> Path:
    """Where QLC+ reads user fixture definitions (only when it starts)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "QLC+" / "Fixtures"
    if sys.platform.startswith("win"):
        return Path.home() / "QLC+" / "Fixtures"
    return Path.home() / ".qlcplus" / "fixtures"


DEFAULT_SHOW = Path.home() / "Lumiere" / "show.qxw"

# Test looks: value per role; roles not named go to 0
LOOKS = {
    "off": {},
    "white": {"dimmer": 255, "red": 255, "green": 255, "blue": 255, "white": 255, "amber": 255},
    "red": {"dimmer": 255, "red": 255},
    "green": {"dimmer": 255, "green": 255},
    "blue": {"dimmer": 255, "blue": 255},
    "amber": {"dimmer": 255, "red": 255, "green": 90, "amber": 255},
    "uv": {"dimmer": 255, "red": 60, "blue": 255, "uv": 255},
    "strobe": {"dimmer": 255, "red": 255, "green": 255, "blue": 255, "white": 255, "amber": 255, "strobe": 220},
}
COLOUR_ROLES = ("red", "green", "blue", "white", "amber", "uv")


class StudioDesk:
    """What the Studio screen talks to: keeps the rig, writes the QLC+ show and
    drives QLC+'s Simple Desk for test looks and the light finder."""

    def __init__(self, data_dir: Path, link=None, show_path: Optional[Path] = None,
                 fixtures_dir: Optional[Path] = None, restart=None):
        from qlclink import QlcLink, qlcplus_service_active, restart_qlcplus
        self.path = Path(data_dir) / "studio-rig.json"
        self.rig = load_rig(self.path)
        self.link = link or QlcLink()
        self.show_path = Path(show_path) if show_path else DEFAULT_SHOW
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else qlc_fixtures_dir()
        # Only stop QLC+ when its service will start it again (the Resident); on a
        # laptop that would just close it
        self.restart = restart or (lambda: qlcplus_service_active() and restart_qlcplus())
        self.testing = False

    def _save(self) -> None:
        save_rig(self.rig, self.path)

    def _light(self, light_id) -> Light:
        for l in self.rig.lights:
            if l.id == int(light_id):
                return l
        raise ValueError("That light isn't in the patch any more.")

    def state(self) -> dict:
        return {
            "rig": self.rig.to_json(),
            "templates": {k: {"label": v[0], "kind": v[1]} for k, v in TEMPLATES.items()},
            "roles": {k: v[0] for k, v in ROLES.items()},
            "kinds": KINDS,
            "looks": list(LOOKS),
            "groups": list(self.rig.groups()),
            "used": len(self.rig.used()),
            "next_free": {p.name: self.rig.next_free(p.count) for p in self.rig.profiles.values()},
            "show": str(self.show_path),
        "show_name": self.show_path.name,
            "qlc": self.link.running(),
            "testing": self.testing,
        }

    def handle(self, op: str, **args):
        fn = getattr(self, "op_" + op, None)
        if fn is None:
            raise ValueError(f"unknown Studio action {op!r}")
        out = fn(**args)
        return self.state() if out is None else out

    # -- profiles
    def op_state(self):
        return None

    def op_new_profile(self, template: str, name: str = "", **params):
        if template not in TEMPLATES:
            raise ValueError("Pick a kind of light.")
        p = from_template(template, name.strip(), **{k: v for k, v in params.items() if v is not None})
        return self.op_save_profile(p.to_json())

    def op_save_profile(self, profile: dict, old_name: Optional[str] = None):
        p = Profile.from_json(profile)
        p.name = p.name.strip()
        if not any(c.segment for c in p.channels) and p.check():
            segs = auto_segments(p.channels)   # repeated colours: a bar with segments
            if segs:
                p.channels = segs
                if p.kind in ("par", "other"):
                    p.kind = "bar"
        problems = p.check()
        if problems:
            raise ValueError(problems[0])
        if p.name in self.rig.profiles and p.name != old_name:
            raise ValueError(f"There's already a light called {p.name!r}.")
        before = self.rig.to_json()
        if old_name and old_name != p.name:
            self.rig.profiles.pop(old_name, None)
            for l in self.rig.lights:
                if l.profile == old_name:
                    l.profile = p.name
        self.rig.profiles[p.name] = p
        clashes = [m for l in self.rig.lights if l.profile == p.name for m in self.rig.check_light(l)]
        if clashes:   # more channels now run into the next light: put everything back
            self.rig = Rig.from_json(before)
            raise ValueError("With that many channels the lights would overlap. " + clashes[0])
        self._save()

    def op_delete_profile(self, name: str):
        if any(l.profile == name for l in self.rig.lights):
            raise ValueError("Remove the lights using this profile from the patch first.")
        self.rig.profiles.pop(name, None)
        self._save()

    # -- the patch
    def op_add_lights(self, name: str, profile: str, address=None, group: str = "Lights", count: int = 1):
        count = max(1, min(64, int(count)))
        address = int(address) if address not in (None, "", 0) else None
        before = list(self.rig.lights)
        try:
            self.rig.add(name.strip() or profile, profile, address, group.strip() or "Lights", count)
        except ValueError:
            self.rig.lights = before
            raise
        self._save()

    def op_update_light(self, id, **fields):
        light = self._light(id)
        old = asdict(light)
        for k in ("name", "group", "profile"):
            if k in fields and str(fields[k]).strip():
                setattr(light, k, str(fields[k]).strip())
        if "address" in fields:
            light.address = int(fields["address"])
        problems = self.rig.check_light(light)
        if problems:
            for k, v in old.items():
                setattr(light, k, v)
            raise ValueError(problems[0])
        self._save()

    def op_remove_light(self, id):
        light = self._light(id)
        self.rig.lights.remove(light)
        self._save()

    # -- QLC+
    def op_apply(self, replace: bool = False):
        if not self.rig.lights:
            raise ValueError("Patch at least one light first.")
        if not replace and self.show_path.exists():
            made = len(re.findall(r"<Function\b", self.show_path.read_text(errors="replace")))
            if made:
                return {"confirm": f"The QLC+ show has {made} scenes, chases or other functions made in QLC+. "
                                   "Studio's show replaces them (the old show is saved first). Go ahead?"}
        out = write_show(self.rig, self.show_path, self.fixtures_dir)
        self.link.close()
        out["restarted"] = bool(self.restart())
        if not out["restarted"]:
            out["note"] = f"Close QLC+ and open {self.show_path} again: QLC+ only reads new lights when it starts."
        return out

    def op_look(self, look: str, light=None, group: Optional[str] = None, lights: Optional[list] = None):
        """A test look on all lights, one group, one light or a chosen few (`lights`:
        ids); every other light goes off."""
        if look not in LOOKS:
            raise ValueError("unknown look")
        values = self.rig.channels_for({}, None)   # everything to 0 first
        if lights is not None:
            targets = [l for l in self.rig.lights if l.id in {int(i) for i in lights}]
        elif light is not None:
            targets = [self._light(light)]
        else:
            targets = self.rig.groups().get(group, []) if group else self.rig.lights
        for l in targets:
            roles = LOOKS[look]
            has = {c.role for c in self.rig.profiles[l.profile].channels}
            if look != "off" and not has & {r for r in roles if r in COLOUR_ROLES}:
                # a colour this light can't make (red on an amber-only bar): show it
                # in its own colours, so every light still answers the test
                roles = {**LOOKS["white"], **({"strobe": roles["strobe"]} if "strobe" in roles else {})}
            values.update(self.rig.channels_for(roles, l))
        self._send(values)

    def op_identify(self, light):
        return self.op_look("white", light=light)

    def op_probe(self, address, values: list):
        """The light finder: set channels from `address` on (one value each)."""
        address = int(address)
        if not 1 <= address <= DMX_CHANNELS:
            raise ValueError("Address must be 1-512.")
        self._send({address + i: int(v) for i, v in enumerate(values[:DMX_CHANNELS]) if address + i <= DMX_CHANNELS})

    def op_release(self):
        self.link.release(0)
        self.testing = False

    def _send(self, values: dict[int, int]) -> None:
        try:
            self.link.set_channels(values)
        except OSError as e:
            raise ValueError("Can't reach QLC+. Is it running with its web page on (--web)?") from e
        self.testing = True
