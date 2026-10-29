#!/usr/bin/env python3
"""Lumière Link venue box: a 3D-printable enclosure for everything.

Holds a Raspberry Pi 4 (with its 30 mm fan), a WT32-ETH01 (ESP32 with
Ethernet), a MAX485 RS-485 module, two XLR DMX sockets (Neutrik D-size), a
16 mm "HOUSE" button and two 5 mm status LEDs.

    python3 enclosure.py            -> out/base.stl, out/lid.stl, out/*.png previews

Every size is a number in the PARAMETERS section: measure your parts with
calipers and adjust before printing. Units are millimetres. X = width (left to
right), Y = depth (front to back), Z = up. The base prints open side up; the
lid prints top face down.
"""

from __future__ import annotations

import math
from pathlib import Path

import manifold3d as mf
import numpy as np

M, CS = mf.Manifold, mf.CrossSection
mf.set_circular_segments(64)
HERE = Path(__file__).resolve().parent
FONTS = HERE.parent.parent / "ui" / "fonts"

# ----------------------------------------------------------------- PARAMETERS
WALL = 3.0             # side walls
FLOOR = 3.0            # base floor
LID = 3.0              # lid plate
CORNER_R = 7.0         # rounded vertical corners
FIT = 0.3              # print clearance for anything that slides into something

# Inside size
IN_W, IN_D, IN_H = 164.0, 100.0, 38.0

# Raspberry Pi 4: 85 x 56 board, ports on the 85 mm edge face the back
PI_LEN, PI_WID, PI_PCB = 85.0, 56.0, 1.5
PI_HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]  # (along 85, along 56)
PI_STANDOFF = 6.0
PI_PORT_OVERHANG = 2.5  # Ethernet/USB stick out past the board edge
PI_USBC_AT = 3.5 + 7.7  # USB-C centre, along the 85 mm edge from the board's front
PI_ETH_AT, PI_USB_AT = 45.75, (9.0, 27.0)  # along the 56 mm edge
PI_ETH_H, PI_USB_H = 13.5, 16.0
FAN = dict(size=30.0, hole_spacing=24.0, at=(32.0, 30.0))  # fan centre on the board (along 85, along 56)

# WT32-ETH01: sellers list 55-63 x 25 mm, so it sits in a roomy cradle (foam tape)
ESP_LEN, ESP_WID = 64.0, 27.0
ESP_BED = 9.0          # board height above the floor (clears bottom pin headers)
ESP_RJ45_W, ESP_RJ45_H = 17.0, 15.0

# MAX485 module 44 x 14 mm (foam tape in a shallow tray)
RS485_LEN, RS485_WID = 46.0, 16.0

# Neutrik D-size XLR: 24 mm hole, M3 holes on a 19 x 24 mm diagonal, flange 26 x 31
XLR_HOLE, XLR_SCREW, XLR_SCREW_OFF = 24.0, 3.2, (9.5, 12.0)

BUTTON_HOLE, LED_HOLE = 16.2, 5.2
INSERT_HOLE, INSERT_DEPTH = 4.2, 5.5   # M3 heat-set inserts in the corner columns
SCREW_HOLE, SCREW_HEAD = 3.4, 6.4       # M3 countersunk screws through the lid

# ----------------------------------------------------------------- derived layout
W, D, H = IN_W + 2 * WALL, IN_D + 2 * WALL, FLOOR + IN_H  # base outer size (lid on top)
PI_X0 = WALL + 2.0                                         # board edge with the USB-C, 2 mm from the left wall
PI_Y1 = D - WALL - PI_PORT_OVERHANG - 0.5                  # board's back edge
PI_Y0 = PI_Y1 - PI_LEN
PI_Z = FLOOR + PI_STANDOFF                                 # underside of the Pi board
ESP_CX = PI_X0 + PI_WID + 10 + ESP_WID / 2                 # RJ45 centred on the WT32's width
XLR_CX = (ESP_CX + 30.0, ESP_CX + 61.0)
XLR_CZ = H / 2 + 1.0
COLUMN_D = 9.0
# Lid screw posts. The back-left one sits between the Pi and the WT32: the Pi's
# corner fills that corner of the box.
COLUMNS = [(WALL + 4.0, WALL + 4.0), (W - WALL - 4.0, WALL + 4.0),
           (PI_X0 + PI_WID + 5.0, D - WALL - 4.0), (W - WALL - 4.0, D - WALL - 4.0)]


def pi_xy(along85: float, along56: float) -> tuple[float, float]:
    """A point on the Pi board -> box coordinates."""
    return PI_X0 + along56, PI_Y0 + along85


# ----------------------------------------------------------------- helpers
def box(x0, y0, z0, x1, y1, z1) -> M:
    return M.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def rounded_rect(w, d, r) -> CS:
    return CS.square([w - 2 * r, d - 2 * r]).translate([r, r]).offset(r, mf.JoinType.Round)


def cyl(x, y, z0, h, d) -> M:
    return M.cylinder(h, d / 2).translate([x, y, z0])


def stadium(length, width) -> CS:
    """A slot with round ends, centred on the origin, long side along X."""
    r = width / 2
    ends = [CS.circle(r).translate([dx, 0]) for dx in (-length / 2 + r, length / 2 - r)]
    return CS.batch_hull(ends) if length > width else CS.circle(r)


def text_cs(txt: str, font: str, height: float) -> CS:
    """Text outline as a CrossSection, cap height ~`height`, centred on the origin."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    path = TextPath((0, 0), txt, size=100, prop=FontProperties(fname=str(FONTS / font)))
    polys = [np.asarray(p, dtype=float) for p in path.to_polygons() if len(p) > 2]
    cs = CS([p.tolist() for p in polys], mf.FillRule.EvenOdd)
    (x0, y0), (x1, y1) = (lambda b: ((b[0], b[1]), (b[2], b[3])))(cs.bounds())
    s = height / max(1e-6, (y1 - y0))
    cs = cs.scale([s, s]).translate([-(x0 + x1) / 2 * s, -(y0 + y1) / 2 * s])
    # Fonts have outlines that touch at single points (accents, serifs); a hair of
    # extra weight closes those pinches so the printed part is cleanly watertight.
    return cs.offset(0.04, mf.JoinType.Round)


def engrave_front(cs: CS, x, z, depth=0.7) -> M:
    """Text cut into the front face (Y = 0), readable from the front."""
    return cs.extrude(depth + 1).rotate([90, 0, 0]).translate([x, depth, z])


def engrave_back(cs: CS, x, z, depth=0.6) -> M:
    """Text cut into the back face (Y = D), readable from behind."""
    return cs.mirror([1, 0]).extrude(depth + 1).rotate([90, 0, 0]).translate([x, D + 1, z])


def engrave_left(cs: CS, y, z, depth=0.6) -> M:
    """Text cut into the left face (X = 0), readable from the left."""
    return cs.extrude(depth + 1).rotate([90, 0, 0]).rotate([0, 0, -90]).translate([depth, y, z])


# ----------------------------------------------------------------- base
def base() -> M:
    shell = rounded_rect(W, D, CORNER_R).extrude(H)
    cavity = rounded_rect(IN_W, IN_D, CORNER_R - WALL).extrude(H).translate([WALL, WALL, FLOOR])
    body = shell - cavity

    # corner columns for the lid screws (heat-set inserts in the top)
    for x, y in COLUMNS:
        body += cyl(x, y, 0, H, COLUMN_D)
    for x, y in COLUMNS:
        body -= cyl(x, y, H - INSERT_DEPTH, INSERT_DEPTH + 1, INSERT_HOLE)

    # Raspberry Pi standoffs (M2.5 self-tapping, 2.5 mm pilot)
    for a85, a56 in PI_HOLES:
        x, y = pi_xy(a85, a56)
        body += cyl(x, y, FLOOR - 0.1, PI_STANDOFF + 0.1, 6.0)
        body -= cyl(x, y, FLOOR - 1.5, PI_STANDOFF + 2, 2.5)

    # WT32-ETH01 holder. Its pin headers hang down along both long edges, so the
    # board rests on a centre rib and a bar under the Ethernet end, with corner
    # posts beside it to locate it. Fix it with foam tape.
    ex0, ex1 = ESP_CX - ESP_WID / 2, ESP_CX + ESP_WID / 2
    ey1 = D - WALL
    ey0 = ey1 - ESP_LEN
    top = FLOOR + ESP_BED
    body += box(ESP_CX - 5, ey0, FLOOR - 0.1, ESP_CX + 5, ey1, top)          # centre rib
    body += box(ESP_CX - 8.5, ey1 - 6, FLOOR - 0.1, ESP_CX + 8.5, ey1, top)  # under the RJ45
    for px in (ex0 - 2.5, ex1):                                              # locating posts
        for py in (ey0, ey1 - 8):
            body += box(px, py, FLOOR - 0.1, px + 2.5, py + 8, top + 3)

    # MAX485 tray, in front of the XLR sockets
    rx0, ry0 = XLR_CX[0] - 12, D - WALL - 26 - RS485_WID - 6
    body += box(rx0 - 2, ry0 - 2, FLOOR - 0.1, rx0 + RS485_LEN + 2, ry0 + RS485_WID + 2, FLOOR + 3)
    body -= box(rx0, ry0, FLOOR + 1.5, rx0 + RS485_LEN, ry0 + RS485_WID, FLOOR + 3.1)

    # --- back panel: Pi USB + Ethernet, WT32 RJ45, 2 x XLR
    zb = PI_Z + PI_PCB
    usb_x = [pi_xy(0, a)[0] for a in PI_USB_AT]
    body -= box(min(usb_x) - 7.6, D - WALL - 1, zb - 0.6, max(usb_x) + 7.6, D + 1, zb + PI_USB_H + 0.8)
    ex, _ = pi_xy(0, PI_ETH_AT)
    body -= box(ex - 8.3, D - WALL - 1, zb - 0.6, ex + 8.3, D + 1, zb + PI_ETH_H + 0.8)
    ez = FLOOR + ESP_BED + 1.6  # top of the WT32 board
    body -= box(ESP_CX - ESP_RJ45_W / 2, D - WALL - 1, ez - 0.6, ESP_CX + ESP_RJ45_W / 2, D + 1, ez + ESP_RJ45_H)
    for cx in XLR_CX:
        hole = M.cylinder(WALL + 2, XLR_HOLE / 2).rotate([-90, 0, 0])
        body -= hole.translate([cx, D - WALL - 1, XLR_CZ])
        for sx, sz in ((-XLR_SCREW_OFF[0], XLR_SCREW_OFF[1]), (XLR_SCREW_OFF[0], -XLR_SCREW_OFF[1])):
            body -= M.cylinder(WALL + 2, XLR_SCREW / 2).rotate([-90, 0, 0]).translate([cx + sx, D - WALL - 1, XLR_CZ + sz])

    # --- left side: USB-C power for the Pi
    uy = PI_Y0 + PI_USBC_AT
    usbc = rounded_rect(8.0, 13.0, 3.5).translate([-4.0, -6.5]).extrude(WALL + 2).rotate([0, 90, 0])
    body -= usbc.translate([-1, uy, zb + 1.6])  # 13 x 8 opening: room for the plug's moulding

    # --- front: HOUSE button and two status LEDs
    bx, bz = W - 34.0, H / 2 + 1
    body -= M.cylinder(WALL + 2, BUTTON_HOLE / 2).rotate([-90, 0, 0]).translate([bx, -1, bz])
    for lx in (W - 70.0, W - 58.0):
        body -= M.cylinder(WALL + 2, LED_HOLE / 2).rotate([-90, 0, 0]).translate([lx, -1, bz])

    # --- vents: slim slots low on both sides (fan in the lid pulls air through)
    slot = stadium(16.0, 2.6).extrude(WALL + 2).rotate([0, 90, 0])  # upright slot through a side wall
    for i in range(8):
        y = 34 + i * 7.0
        body -= slot.translate([-1, y, FLOOR + 14])
        body -= slot.translate([W - WALL - 1, y, FLOOR + 14])

    # --- labels
    small = "Manrope.ttf"
    body -= engrave_back(text_cs("USB", small, 3.6), (min(usb_x) + max(usb_x)) / 2, zb + PI_USB_H + 4.0)
    body -= engrave_back(text_cs("DJ LINK", small, 3.6), ex, zb + PI_ETH_H + 4.0)
    body -= engrave_back(text_cs("ART-NET", small, 3.6), ESP_CX, ez + ESP_RJ45_H + 3.4)
    for n, cx in enumerate(XLR_CX, 1):
        body -= engrave_back(text_cs(f"DMX {n}", small, 3.0), cx, 3.2)  # under the socket's flange
    body -= engrave_left(text_cs("POWER", small, 3.6), uy, zb + 13.0)
    body -= engrave_front(text_cs("HOUSE", small, 3.4), bx, bz - 13.5)
    body -= engrave_front(text_cs("NET", small, 3.0), W - 70.0, bz - 8.0)
    body -= engrave_front(text_cs("DMX", small, 3.0), W - 58.0, bz - 8.0)
    body -= engrave_front(text_cs("Lumière Link", "BodoniModa-Italic.ttf", 9.0), 52.0, H / 2 + 1)
    # a fine line along the front, like a rack panel
    body -= box(10, -1, 6.0, W - 10, 0.5, 6.6)

    # feet recesses (10 mm rubber feet) and screw-free bottom
    for x, y in ((14, 14), (W - 14, 14), (14, D - 14), (W - 14, D - 14)):
        body -= cyl(x, y, -0.1, 1.1, 10.4)
    return body


# ----------------------------------------------------------------- lid
def lid() -> M:
    plate = rounded_rect(W, D, CORNER_R).extrude(LID)
    # locating rim that drops inside the walls
    rim = rounded_rect(IN_W - 2 * FIT, IN_D - 2 * FIT, CORNER_R - WALL - FIT).extrude(2.0)
    rim -= rounded_rect(IN_W - 2 * FIT - 3.2, IN_D - 2 * FIT - 3.2, max(0.5, CORNER_R - WALL - FIT - 1.6)) \
        .extrude(2.0).translate([1.6, 1.6, 0])
    rim = rim.translate([WALL + FIT, WALL + FIT, -2.0])
    part = plate + rim
    # clear the rim around the corner columns
    for x, y in COLUMNS:
        part -= cyl(x, y, -2.1, 2.1, COLUMN_D + 1.0)
    # countersunk M3 screws into the column inserts
    for x, y in COLUMNS:
        part -= cyl(x, y, -2.5, LID + 3, SCREW_HOLE)
        part -= M.cylinder(1.6, SCREW_HOLE / 2, SCREW_HEAD / 2).translate([x, y, LID - 1.6 + 0.01])
    # fan: grille of round holes over the Pi's processor, 4 fan screws
    fx, fy = pi_xy(*FAN["at"])
    r_out = FAN["size"] / 2 - 2.0
    pitch, hole = 3.7, 2.7
    for i in range(-6, 7):
        for j in range(-6, 7):
            hx = fx + i * pitch + (pitch / 2 if j % 2 else 0)
            hy = fy + j * pitch * math.sqrt(3) / 2
            if math.hypot(hx - fx, hy - fy) <= r_out + 0.5 - hole / 2:
                part -= cyl(hx, hy, -2.5, LID + 3, hole)
    s = FAN["hole_spacing"] / 2
    for dx in (-s, s):
        for dy in (-s, s):
            part -= cyl(fx + dx, fy + dy, -2.5, LID + 3, 2.8)
    # a ring engraved around the grille
    ring = CS.circle(FAN["size"] / 2 + 3.2) - CS.circle(FAN["size"] / 2 + 2.4)
    part -= ring.extrude(1.0).translate([fx, fy, LID - 0.6])
    # brand on top
    tx = (pi_xy(0, PI_WID)[0] + W) / 2 + 2
    title = text_cs("Lumière Link", "BodoniModa-Italic.ttf", 12.0)
    part -= title.extrude(1.0).translate([tx, D / 2 + 6, LID - 0.7])
    by = text_cs("VITAL  ACOUSTICA", "Manrope.ttf", 3.4)
    part -= by.extrude(1.0).translate([tx, D / 2 - 9, LID - 0.5])
    return part


# ----------------------------------------------------------------- parts for checking the fit
def dummies() -> dict[str, M]:
    """Rough stand-ins for the electronics, used to check nothing collides."""
    zb = PI_Z
    pi = box(PI_X0, PI_Y0, zb, PI_X0 + PI_WID, PI_Y1, zb + PI_PCB)
    pi += box(pi_xy(0, PI_ETH_AT)[0] - 8, PI_Y1 - 21, zb + PI_PCB, pi_xy(0, PI_ETH_AT)[0] + 8, PI_Y1 + PI_PORT_OVERHANG, zb + PI_PCB + PI_ETH_H)
    for a in PI_USB_AT:
        x = pi_xy(0, a)[0]
        pi += box(x - 7, PI_Y1 - 17, zb + PI_PCB, x + 7, PI_Y1 + PI_PORT_OVERHANG, zb + PI_PCB + PI_USB_H)
    fx, fy = pi_xy(*FAN["at"])
    fan = box(fx - 15, fy - 15, H - 2.0 - 8, fx + 15, fy + 15, H - 2.0)
    ey1 = D - WALL
    bz = FLOOR + ESP_BED
    esp = box(ESP_CX - 12.5, ey1 - 60, bz, ESP_CX + 12.5, ey1, bz + 1.6)                      # board
    esp += box(ESP_CX - 8, ey1 - 21, bz + 1.6, ESP_CX + 8, ey1 + 0.5, bz + 1.6 + 13.5)          # RJ45
    for hx in (ESP_CX - 11.5, ESP_CX + 11.5):                                                  # pin headers
        esp += box(hx - 1.3, ey1 - 58, bz - 8.5, hx + 1.3, ey1 - 25, bz)
    xlr = M()
    for cx in XLR_CX:
        xlr += M.cylinder(21, 11.5).rotate([90, 0, 0]).translate([cx, D - WALL, XLR_CZ])
    rx0, ry0 = XLR_CX[0] - 12, D - WALL - 26 - RS485_WID - 6
    rs = box(rx0 + 1, ry0 + 1, FLOOR + 1.5, rx0 + 45, ry0 + 15, FLOOR + 1.5 + 12)
    button = M.cylinder(28, 8).rotate([-90, 0, 0]).translate([W - 34.0, WALL, H / 2 + 1])
    return {"Raspberry Pi 4": pi, "Pi fan": fan, "WT32-ETH01": esp, "XLR sockets": xlr,
            "MAX485": rs, "HOUSE button": button}


# ----------------------------------------------------------------- output
def to_trimesh(part: M):
    import trimesh

    mesh = part.to_mesh()
    # process=False keeps the exact topology manifold3d made (watertight); merging would break it
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts), process=False)


def check(b: M, l: M) -> list[str]:
    problems = []
    lid_placed = l.translate([0, 0, H])
    for name, part in dummies().items():
        for shell, what in ((b, "base"), (lid_placed, "lid")):
            v = (part ^ shell).volume()
            if v > 1.0:
                problems.append(f"{name} hits the {what} ({v:.0f} mm³)")
    return problems


def render(parts: list[tuple[M, str]], path: Path, elev=28, azim=-58, title="") -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    fig = plt.figure(figsize=(12, 8), dpi=110, facecolor="#101010")
    ax = fig.add_subplot(111, projection="3d", facecolor="#101010")
    light = np.array([0.4, -0.6, 0.7]) / np.linalg.norm([0.4, -0.6, 0.7])
    lo, hi = np.full(3, 1e9), np.full(3, -1e9)
    for part, colour in parts:
        tm = to_trimesh(part)
        tris = tm.vertices[tm.faces]
        shade = np.clip(0.35 + 0.65 * np.abs(tm.face_normals @ light), 0, 1)
        base_rgb = np.array(matplotlib.colors.to_rgb(colour))
        cols = np.clip(base_rgb[None, :] * shade[:, None], 0, 1)
        ax.add_collection3d(Poly3DCollection(tris, facecolors=cols, edgecolors="none"))
        lo, hi = np.minimum(lo, tm.vertices.min(0)), np.maximum(hi, tm.vertices.max(0))
    span = (hi - lo).max() / 2
    mid = (hi + lo) / 2
    ax.set_xlim(mid[0] - span, mid[0] + span); ax.set_ylim(mid[1] - span, mid[1] + span); ax.set_zlim(mid[2] - span, mid[2] + span)
    ax.set_box_aspect([1, 1, 1]); ax.view_init(elev=elev, azim=azim); ax.set_axis_off()
    if title:
        fig.text(0.5, 0.93, title, color="#EFE6CF", ha="center", fontsize=16)
    fig.savefig(path, facecolor="#101010", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    b, l = base(), lid()
    printable_lid = l.rotate([180, 0, 0]).translate([0, D, LID])  # upside down, top face on the bed
    for part, name in ((b, "base"), (printable_lid, "lid")):
        assert part.status() == mf.Error.NoError, name
        to_trimesh(part).export(out / f"lumiere-venue-box-{name}.stl")
    problems = check(b, l)
    print("Outer size: %.0f x %.0f x %.0f mm" % (W, D, H + LID))
    print("Fit check:", "OK, nothing collides" if not problems else "; ".join(problems))
    brass, body, pcb = "#B8A47B", "#2A2926", "#3E7A4E"
    render([(b, body), (l.translate([0, 0, H + 28]), body)], out / "preview-exploded.png", title="Lumière Link venue box")
    render([(b, body)] + [(p, pcb if "Pi" in n or "WT32" in n or "MAX" in n else brass) for n, p in dummies().items()],
           out / "preview-inside.png", elev=55, azim=-70, title="Inside: Pi 4 · WT32-ETH01 · MAX485 · 2 × XLR")
    render([(b, body), (l.translate([0, 0, H]), body)], out / "preview-back.png", elev=18, azim=118, title="Back panel")
    render([(b, body), (l.translate([0, 0, H]), body)], out / "preview-front.png", elev=18, azim=-62, title="Front")


if __name__ == "__main__":
    main()
