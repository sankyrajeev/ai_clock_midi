"""Lumière Studio, step 2: the show that plays by itself.

Studio builds looks and chases from the venue's lights and writes them into the
QLC+ show, with a virtual console button for every sequence slot of Lumière
Link's pools, already wired to the note Lumière Link sends for it. QLC+ follows
the BPM from the "Lumiere Clock" MIDI port, so every chase runs on the beat.

How it plays:
  * Each pool slot (Drop 1, Drop 2 ... Build 1 ...) is one button in a solo frame:
    starting one stops the one before. Lumière Link picks a slot per section.
  * Lumière Link sends a short note (on, then off 80 ms later). A toggle button
    would stop its look if pressed again while it runs, so every slot gets its
    own copy of an effect, a pool always has at least two slots, and the pools
    pick in "shuffle" order (never the same slot twice in a row).
  * Hits before the drop (channel 10 note 61) are a flash button: full white for
    as long as the note is held. The snare roll (note 62) starts a strobe and the
    blackout (note 60) an all-off look, both in the solo frame, so the drop that
    follows replaces them.

Effects are worked out here step by step (each step a QLC+ scene, a chaser plays
the steps): that works for any mix of colours (amber/white bars, RGB pars ...),
not only RGB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from xml.sax.saxutils import escape

# Lumière Link's pools: name -> MIDI channel (pools.py) and the notes before a drop
POOL_CHANNELS = {"drop": 2, "build": 3, "breakdown": 4, "pre_drop": 5,
                 "intro": 6, "outro": 7, "verse": 8, "bridge": 9}
POOL_LABELS = {"drop": "Drop", "build": "Build", "breakdown": "Breakdown", "pre_drop": "Pre-drop",
               "intro": "Intro", "outro": "Outro", "verse": "Verse", "bridge": "Bridge"}
FIRST_NOTE = 36
CUE_CHANNEL = 10
BLACKOUT_NOTE, HIT_NOTE, ROLL_NOTE = 60, 61, 62
MAX_SLOTS = 24

# QLC+ takes its beat from Lumière Link's double-speed clock, so a QLC+ "beat" is an
# 8th note. (QLC+ chasers move at most one step per beat; at the real beat a drop
# chase would crawl.)
CUES_PORT, CLOCK_PORT = "Lumiere Cues", "Lumiere Studio Clock"
FOLDER = "Lumiere"          # QLC+ function folder for everything Studio makes
EIGHTH = 1000               # QLC+ beat units: 1000 = one QLC+ beat = an 8th note
BEAT = 2 * EIGHTH           # a real beat
STROBE_MS = 50              # the strobe runs on time, not on the beat

COLOUR_ROLES = ("red", "green", "blue", "white", "amber", "uv")
# Each colour: ways to make it, best first; a light uses the first it has channels for
COLOURS = {
    "amber":   [{"amber": 255}, {"red": 255, "green": 100}],
    "white":   [{"white": 255}, {"red": 255, "green": 255, "blue": 255}],
    "warm":    [{"amber": 255, "white": 110}, {"red": 255, "green": 150, "blue": 40}],
    "red":     [{"red": 255}],
    "blue":    [{"blue": 255}],
    "magenta": [{"red": 255, "blue": 255}],
    "cyan":    [{"green": 255, "blue": 255}],
    "green":   [{"green": 255}],
    "uv":      [{"uv": 255}, {"red": 70, "blue": 255}],
}
COLOUR_LABELS = {"amber": "Amber", "white": "White", "warm": "Warm", "red": "Red", "blue": "Blue",
                 "magenta": "Magenta", "cyan": "Cyan", "green": "Green", "uv": "UV"}
# Rest positions for channels that aren't colour or dimmer
REST = {"pan": 128, "tilt": 128}

EFFECTS = {
    # kind: (label, what it suits)
    "solid": ("Solid", "intro outro breakdown verse"),
    "pulse": ("Pulse", "breakdown intro outro bridge verse"),
    "chase": ("Chase", "drop build verse"),
    "bounce": ("Bounce", "drop pre_drop"),
    "alternate": ("Alternate", "drop verse bridge"),
    "fill": ("Fill", "build pre_drop"),
    "strobe": ("Strobe", "pre_drop"),
}


# ----------------------------------------------------------------- colours
def colour_values(colour: str, roles: set[str]) -> dict[str, int]:
    """How a light with these colour channels makes `colour`. A colour it can't
    make falls back to amber, then white, then every colour channel it has, so a
    light never stays dark in a look."""
    for name in (colour, "amber", "white"):
        for way in COLOURS.get(name, []):
            if set(way) <= roles:
                return way
    return {r: 255 for r in roles if r in COLOUR_ROLES}


def can_make(colour: str, roles: set[str]) -> bool:
    return any(set(way) <= roles for way in COLOURS[colour])


def palette(rig) -> list[str]:
    """The colours the venue's lights can really make, most useful first."""
    out = []
    for c in COLOURS:
        if any(can_make(c, {ch.role for ch in rig.profiles[l.profile].channels}) for l in rig.lights):
            out.append(c)
    return out


# ----------------------------------------------------------------- units
@dataclass
class Unit:
    """One thing that lights up on its own: a par, or one segment of a bar."""
    light: object
    segment: Optional[int]   # None for a light without segments


def units_by_group(rig) -> list[list[Unit]]:
    """Each group's units in order along the room (address, then segment)."""
    out = []
    for members in rig.groups().values():
        units = []
        for l in members:
            segs = rig.profiles[l.profile].segments
            units += [Unit(l, s) for s in range(1, segs + 1)] if segs else [Unit(l, None)]
        out.append(units)
    return out


def light_values(rig, light, on: dict[Optional[int], tuple[str, float]]) -> list[int]:
    """Every channel value of a light, with `on`: segment -> (colour, level 0-1);
    the segment None means the whole light. Parts not in `on` are off."""
    prof = rig.profiles[light.profile]
    roles_of: dict[Optional[int], set[str]] = {}
    for c in prof.channels:
        roles_of.setdefault(c.segment, set()).add(c.role)
    whole = on.get(None)
    values = []
    for c in prof.channels:
        v = REST.get(c.role, 0)
        state = whole if c.segment is None else (on.get(c.segment) or whole)
        if c.segment is None and c.role == "dimmer":
            # a bar's master dimmer stays full while any of it is on (the segments
            # set the level); a plain light's dimmer sets its level
            if prof.segments:
                v = 255 if on else 0
            elif state:
                v = int(255 * state[1])
        elif state and c.role == "dimmer":
            v = int(255 * state[1])
        elif state and c.role in COLOUR_ROLES:
            colour, level = state
            v = int(colour_values(colour, roles_of.get(c.segment, set())).get(c.role, 0) * level)
        values.append(max(0, min(255, v)))
    return values


def frame(rig, lit: dict[int, dict[Optional[int], tuple[str, float]]]) -> dict[int, list[int]]:
    """Values for every light: `lit` = light id -> what's on in it."""
    return {l.id: light_values(rig, l, lit.get(l.id, {})) for l in rig.lights}


def _all(rig, colour: str, level: float = 1.0):
    return {l.id: {None: (colour, level)} for l in rig.lights}


def _units_on(units: list[Unit], colour_of) -> dict:
    lit: dict = {}
    for i, u in enumerate(units):
        c = colour_of(i, u)
        if c:
            lit.setdefault(u.light.id, {})[u.segment] = c
    return lit


def _merge(*parts: dict) -> dict:
    out: dict = {}
    for p in parts:
        for lid, segs in p.items():
            out.setdefault(lid, {}).update(segs)
    return out


# ----------------------------------------------------------------- effects
@dataclass
class Effect:
    key: str                  # "chase:amber", "alternate:amber/white" ...
    label: str
    steps: list[dict]         # each a frame: light id -> channel values
    duration: int = BEAT      # per step, in QLC+ beat units (or ms when tempo is "Time")
    fade: int = 0
    run_order: str = "Loop"
    tempo: str = "Beats"

    @property
    def is_scene(self) -> bool:
        return len(self.steps) == 1


def make_effect(rig, key: str) -> Effect:
    kind, _, colours = key.partition(":")
    cs = (colours or "white").split("/")
    c1, c2 = cs[0], (cs[1] if len(cs) > 1 else cs[0])
    groups = units_by_group(rig)
    n = max((len(g) for g in groups), default=1)
    label = f"{COLOUR_LABELS.get(c1, c1)}{' / ' + COLOUR_LABELS.get(c2, c2) if c2 != c1 else ''} {EFFECTS[kind][0].lower()}"
    if kind == "solid":
        return Effect(key, label, [frame(rig, _all(rig, c1))])
    if kind == "pulse":
        return Effect(key, label, [frame(rig, _all(rig, c1)), frame(rig, _all(rig, c1, 0.25))],
                      duration=2 * BEAT, fade=2 * BEAT)
    if kind in ("chase", "bounce"):
        steps = [frame(rig, _merge(*[_units_on(g, lambda i, u, k=k, g=g: (c1, 1.0) if i == k % len(g) else None)
                                     for g in groups])) for k in range(n)]
        return Effect(key, label, steps, duration=EIGHTH,
                      run_order="Loop" if kind == "chase" else "PingPong")
    if kind == "alternate":
        steps = [frame(rig, _merge(*[_units_on(g, lambda i, u, s=s: (c1, 1.0) if (i + s) % 2 == 0 else (c2, 1.0) if c2 != c1 else None)
                                     for g in groups])) for s in (0, 1)]
        return Effect(key, label, steps, duration=BEAT)
    if kind == "fill":
        steps = [frame(rig, _merge(*[_units_on(g, lambda i, u, k=k, g=g: (c1, 1.0) if i < round((k + 1) * len(g) / n) else None)
                                     for g in groups])) for k in range(n)]
        return Effect(key, label, steps, duration=EIGHTH)
    if kind == "strobe":
        return Effect(key, label, [frame(rig, _all(rig, c1)), frame(rig, {})], duration=STROBE_MS, tempo="Time")
    raise ValueError(f"unknown effect {kind!r}")


def gallery(rig) -> list[dict]:
    """Every effect Studio can make for this rig, for the screen."""
    pal = palette(rig) or ["white"]
    keys = []
    for kind in ("solid", "pulse", "chase", "bounce", "fill"):
        keys += [f"{kind}:{c}" for c in pal]
    keys += [f"alternate:{a}/{b}" for i, a in enumerate(pal) for b in pal[i + 1:]]
    keys += ["strobe:white"]
    out = []
    for k in keys:
        kind, _, cs = k.partition(":")
        names = [COLOUR_LABELS.get(c, c) for c in cs.split("/")]
        out.append({"key": k, "kind": kind, "label": f"{' / '.join(names)} {EFFECTS[kind][0].lower()}",
                    "suits": EFFECTS[kind][1].split()})
    return out


def auto_fill(rig) -> dict[str, list[str]]:
    """A sensible show from the rig's colours: calm looks for intros and
    breakdowns, building effects for builds, the busiest for drops."""
    pal = palette(rig) or ["white"]
    a = pal[0]
    b = next((c for c in pal if c != a), a)
    two = f"{a}/{b}" if b != a else None
    warm = "warm" if "warm" in pal else a
    drop = [f"chase:{a}", f"bounce:{b}", f"chase:{b}"] + ([f"alternate:{two}"] if two else [f"bounce:{a}"])
    return {
        "intro": [f"pulse:{warm}", f"solid:{a}"],
        "build": [f"fill:{a}", f"chase:{b}"],
        "pre_drop": [f"bounce:{a}", "strobe:white"],
        "drop": drop,
        "breakdown": [f"pulse:{b}", f"solid:{warm}"],
        "verse": [f"alternate:{two}" if two else f"chase:{a}", f"solid:{b}"],
        "bridge": [f"pulse:{a}", f"pulse:{b}"],
        "outro": [f"pulse:{warm}", f"solid:{b}"],
    }


def slots(show: dict) -> dict[str, list[str]]:
    """Pool slots as Lumière Link will pick them: at least two per pool in use
    (one effect is used twice), so the same button is never pressed twice in a row."""
    out = {}
    for pool, keys in (show.get("pools") or {}).items():
        keys = [k for k in keys if k][:MAX_SLOTS]
        if pool in POOL_CHANNELS and keys:
            out[pool] = keys * 2 if len(keys) == 1 else keys
    return out


def input_channel(midi_channel: int, note: int) -> int:
    """QLC+'s input channel number for a MIDI note when the input listens on all
    MIDI channels (omni): the MIDI channel in the top bits, then 128 + note."""
    return ((midi_channel - 1) << 12) | (128 + note)


# ----------------------------------------------------------------- QLC+ XML
class _Ids:
    def __init__(self, start: int = 1):
        self.n = start - 1

    def __call__(self) -> int:
        self.n += 1
        return self.n


def _scene_xml(fid: int, name: str, values: dict[int, list[int]], path: str) -> str:
    fx = "".join(f'\n   <FixtureVal ID="{lid}">{",".join(f"{i},{v}" for i, v in enumerate(vals))}</FixtureVal>'
                 for lid, vals in sorted(values.items()))
    return (f'  <Function ID="{fid}" Type="Scene" Name="{escape(name)}" Path="{escape(path)}">\n'
            f'   <Speed FadeIn="0" FadeOut="0" Duration="0"/>{fx}\n  </Function>')


def _chaser_xml(fid: int, name: str, step_ids: list[int], e: Effect, path: str) -> str:
    steps = "".join(f'\n   <Step Number="{i}" FadeIn="0" Hold="0" FadeOut="0">{sid}</Step>'
                    for i, sid in enumerate(step_ids))
    return (f'  <Function ID="{fid}" Type="Chaser" Name="{escape(name)}" Path="{escape(path)}">\n'
            + ('   <Tempo>Beats</Tempo>\n' if e.tempo == "Beats" else "") +
            f'   <Speed FadeIn="{e.fade}" FadeOut="0" Duration="{e.duration}"/>\n'
            f'   <Direction>Forward</Direction>\n   <RunOrder>{e.run_order}</RunOrder>\n'
            f'   <SpeedModes FadeIn="Common" FadeOut="Common" Duration="Common"/>{steps}\n  </Function>')


def _button_xml(wid: int, caption: str, fid: int, x: int, y: int, w: int, h: int,
                action: str = "Toggle", channel: Optional[int] = None) -> str:
    flash = ' Override="0" ForceLTP="0"' if action == "Flash" else ""
    inp = f'\n    <Input ID="0" Universe="0" Channel="{channel}"/>' if channel is not None else ""
    return (f'   <Button Caption="{escape(caption)}" ID="{wid}">\n'
            f'    <WindowState Visible="True" X="{x}" Y="{y}" Width="{w}" Height="{h}"/>\n'
            f'    <Function ID="{fid}"/>\n    <Action{flash}>{action}</Action>{inp}\n   </Button>')


@dataclass
class Built:
    functions: str          # <Function> elements for the Engine
    console: str            # the <VirtualConsole>
    ids: dict               # what each slot/cue plays: {"drop": [fid, ...], "hit": fid, ...}
    counts: dict            # pool -> number of slots, for Lumière Link


def build(rig, show: dict) -> Built:
    """Everything the show needs in QLC+: functions and the pre-wired console."""
    fid = _Ids(1)
    wid = _Ids(1)
    funcs: list[str] = []
    ids: dict = {}
    cache: dict[str, Effect] = {}

    def add_effect(key: str, name: str) -> int:
        e = cache.get(key) or cache.setdefault(key, make_effect(rig, key))
        if e.is_scene:
            sid = fid()
            funcs.append(_scene_xml(sid, name, e.steps[0], FOLDER))
            return sid
        step_ids = []
        for i, values in enumerate(e.steps):
            sid = fid()
            funcs.append(_scene_xml(sid, f"{name} · step {i + 1}", values, f"{FOLDER}/steps"))
            step_ids.append(sid)
        cid = fid()
        funcs.append(_chaser_xml(cid, name, step_ids, e, FOLDER))
        return cid

    buttons: list[str] = []
    W, H, GAP = 150, 56, 6
    y = 40
    counts = {p: 0 for p in POOL_CHANNELS}
    for pool, keys in slots(show).items():
        ids[pool] = []
        for i, key in enumerate(keys):
            label = make_label(key)
            f = add_effect(key, f"{POOL_LABELS[pool]} {i + 1} · {label}")
            ids[pool].append(f)
            buttons.append(_button_xml(wid(), f"{POOL_LABELS[pool]} {i + 1} · {label}", f,
                                       10 + (i % 8) * (W + GAP), y + (i // 8) * (H + GAP), W, H,
                                       channel=input_channel(POOL_CHANNELS[pool], FIRST_NOTE + i)))
        counts[pool] = len(keys)
        y += ((len(keys) - 1) // 8 + 1) * (H + GAP) + GAP
    # before the drop: blackout and roll in the solo frame (the drop replaces them)
    if show.get("blackout", True):
        f = fid()
        funcs.append(_scene_xml(f, "Blackout", frame(rig, {}), FOLDER))
        ids["blackout"] = f
        buttons.append(_button_xml(wid(), "Blackout", f, 10, y, W, H,
                                   channel=input_channel(CUE_CHANNEL, BLACKOUT_NOTE)))
    if show.get("roll", True):
        f = add_effect("strobe:white", "Snare roll strobe")
        ids["roll"] = f
        buttons.append(_button_xml(wid(), "Snare roll strobe", f, 10 + W + GAP, y, W, H,
                                   channel=input_channel(CUE_CHANNEL, ROLL_NOTE)))
    y += H + GAP
    solo_h = y + 10
    solo = (f'  <SoloFrame Caption="Lumière auto play" ID="{wid()}">\n'
            f'   <WindowState Visible="True" X="10" Y="10" Width="{8 * (W + GAP) + 20}" Height="{solo_h}"/>\n'
            f'   <ShowHeader>True</ShowHeader>\n'
            f'   <ShowEnableButton>False</ShowEnableButton>\n   <Collapsed>False</Collapsed>\n'
            + "\n".join(buttons) + "\n  </SoloFrame>")
    extra = ""
    if show.get("hit", True):   # outside the solo frame: a flash on top of what plays
        f = fid()
        funcs.append(_scene_xml(f, "Hit flash", frame(rig, _all(rig, "white")), FOLDER))
        ids["hit"] = f
        extra = "\n" + _button_xml(wid(), "Hit flash", f, 10, solo_h + 20, W, H, action="Flash",
                                   channel=input_channel(CUE_CHANNEL, HIT_NOTE)).replace("\n   ", "\n  ")
    console = (' <VirtualConsole>\n  <Frame Caption="Lumière" ID="0">\n'
               '   <ShowHeader>False</ShowHeader>\n'
               '   <ShowEnableButton>False</ShowEnableButton>\n   <Collapsed>False</Collapsed>\n'
               + "\n".join("  " + line for line in solo.split("\n")) + extra
               + "\n  </Frame>\n </VirtualConsole>")
    return Built("\n".join(funcs), console, ids, counts)


def make_label(key: str) -> str:
    kind, _, cs = key.partition(":")
    names = [COLOUR_LABELS.get(c, c) for c in (cs or "white").split("/")]
    return f"{' / '.join(names)} {EFFECTS[kind][0].lower()}"


def with_lumiere_inputs(io_map: str) -> str:
    """The show's <InputOutputMap> with Lumière Link's MIDI in: the cues on
    universe 1 (listening on every MIDI channel) and the clock on universe 2, and
    QLC+'s beat taken from that clock. Outputs are left as they are."""
    import re
    if not io_map:
        io_map = "<InputOutputMap>\n </InputOutputMap>"
    if io_map.endswith("/>"):
        io_map = io_map[:-2].rstrip() + ">\n </InputOutputMap>"
    io_map = re.sub(r"\s*<BeatGenerator\b[^>]*/>", "", io_map)
    io_map = io_map.replace("<InputOutputMap>", '<InputOutputMap>\n  <BeatGenerator BeatType="Plugin" BPM="0"/>', 1)
    for uid, port, extra in ((0, CUES_PORT, ' midichannel="16"'), (1, CLOCK_PORT, "")):
        inp = (f'<Input Plugin="MIDI" Name="{port}" UID="" Line="0">\n'
               f'     <PluginParameters{extra}/>\n    </Input>' if extra else
               f'<Input Plugin="MIDI" Name="{port}" UID="" Line="0"/>')
        m = re.search(rf'<Universe\b[^>]*\bID="{uid}"[^>]*?(/>|>(.*?)</Universe>)', io_map, re.S)
        if not m:
            uni = f'<Universe Name="Universe {uid + 1}" ID="{uid}">\n    {inp}\n   </Universe>'
            io_map = io_map.replace("</InputOutputMap>", f"  {uni}\n </InputOutputMap>", 1)
            continue
        head = re.match(r"<Universe\b[^>]*?(?=/?>)", m.group(0)).group(0)
        inner = re.sub(r"<Input\b[^>]*/>|<Input\b[^>]*[^/]>.*?</Input>", "", m.group(2) or "", flags=re.S)
        inner = re.sub(r"\n\s*\n", "\n", inner).rstrip()
        io_map = io_map[:m.start()] + f"{head}>\n    {inp}{inner}\n   </Universe>" + io_map[m.end():]
    return io_map
