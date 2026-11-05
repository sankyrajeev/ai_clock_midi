"""SVG drawings for the Lumière Resident product sheet, generated from enclosure.py's
own numbers so the sheet matches the printed part exactly. Colours come from CSS classes
(theme tokens on the page)."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "enclosure"))
import enclosure as e

S = 4.0  # px per mm
HT = e.H + e.LID  # 44 mm with the lid on


def f(v):
    return f"{v:.1f}"


def dim_h(x0, x1, y, text, above=True):
    """Horizontal dimension line between x0 and x1 (px) at y (px)."""
    t = y - 6 if above else y + 14
    return (f'<g class="dim"><line x1="{f(x0)}" y1="{f(y)}" x2="{f(x1)}" y2="{f(y)}"/>'
            f'<line x1="{f(x0)}" y1="{f(y-5)}" x2="{f(x0)}" y2="{f(y+5)}"/><line x1="{f(x1)}" y1="{f(y-5)}" x2="{f(x1)}" y2="{f(y+5)}"/>'
            f'<text x="{f((x0+x1)/2)}" y="{f(t)}" text-anchor="middle">{text}</text></g>')


def dim_v(x, y0, y1, text):
    return (f'<g class="dim"><line x1="{f(x)}" y1="{f(y0)}" x2="{f(x)}" y2="{f(y1)}"/>'
            f'<line x1="{f(x-5)}" y1="{f(y0)}" x2="{f(x+5)}" y2="{f(y0)}"/><line x1="{f(x-5)}" y1="{f(y1)}" x2="{f(x+5)}" y2="{f(y1)}"/>'
            f'<text x="{f(x+8)}" y="{f((y0+y1)/2+4)}">{text}</text></g>')


def callout(x, y, lx, ly, text, anchor="start"):
    return (f'<g class="call"><line x1="{f(x)}" y1="{f(y)}" x2="{f(lx)}" y2="{f(ly)}"/><circle cx="{f(x)}" cy="{f(y)}" r="2.2"/>'
            f'<text x="{f(lx + (6 if anchor == "start" else -6))}" y="{f(ly+4)}" text-anchor="{anchor}">{text}</text></g>')


def panel(width_mm, items, title, pad=(70, 70, 70, 60)):
    """A wall seen face on: width_mm wide, HT tall. items: callables returning SVG with (ox, oy)."""
    l, r, t, b = pad
    w, h = width_mm * S, HT * S
    ox, oy = l, t
    body = [f'<rect class="face" x="{ox}" y="{oy}" width="{f(w)}" height="{f(h)}" rx="{f(e.CORNER_R*S*0.35)}"/>',
            f'<line class="seam" x1="{ox}" y1="{f(oy + e.LID*S)}" x2="{f(ox+w)}" y2="{f(oy + e.LID*S)}"/>']
    for it in items:
        body.append(it(ox, oy))
    body.append(dim_h(ox, ox + w, oy + h + 26, f"{width_mm:.0f} mm", above=False))
    body.append(dim_v(ox + w + 18, oy, oy + h, f"{HT:.0f}"))
    vw, vh = l + w + r, t + h + b
    return (f'<svg viewBox="0 0 {f(vw)} {f(vh)}" role="img" aria-label="{title}" class="drawing">'
            + "".join(body) + "</svg>")


def zy(oy, z):
    """Height z (mm from the bottom) -> svg y."""
    return oy + (HT - z) * S


def front():
    items = []
    for name, x in zip(("NET", "DMX"), e.LED_X):
        items.append(lambda ox, oy, x=x, name=name: (
            f'<circle class="hole led-{name.lower()}" cx="{f(ox+x*S)}" cy="{f(zy(oy, e.CONTROLS_Z))}" r="{f(e.LED_HOLE/2*S)}"/>'
            f'<text class="eng" x="{f(ox+x*S)}" y="{f(zy(oy, e.CONTROLS_Z-8.0)+5)}" text-anchor="middle">{name}</text>'))
    items.append(lambda ox, oy: (
        f'<circle class="hole" cx="{f(ox+e.BUTTON_X*S)}" cy="{f(zy(oy, e.CONTROLS_Z))}" r="{f(e.BUTTON_HOLE/2*S)}"/>'
        f'<circle class="cap" cx="{f(ox+e.BUTTON_X*S)}" cy="{f(zy(oy, e.CONTROLS_Z))}" r="{f(e.BUTTON_HOLE/2*S-6)}"/>'
        f'<text class="eng" x="{f(ox+e.BUTTON_X*S)}" y="{f(zy(oy, e.CONTROLS_Z-13.5)+5)}" text-anchor="middle">HOUSE</text>'))
    items.append(lambda ox, oy: (
        f'<text class="wordmark" x="{f(ox+122*S)}" y="{f(zy(oy, e.CONTROLS_Z)+12)}" text-anchor="middle">Lumière Resident</text>'
        f'<line class="groove" x1="{f(ox+10*S)}" y1="{f(zy(oy, 6.3))}" x2="{f(ox+(e.W-10)*S)}" y2="{f(zy(oy, 6.3))}"/>'))
    items.append(lambda ox, oy: callout(ox + e.LED_X[0]*S, zy(oy, e.CONTROLS_Z), ox + 6, oy - 50, "NET · green · Pi GPIO17"))
    items.append(lambda ox, oy: callout(ox + e.LED_X[1]*S, zy(oy, e.CONTROLS_Z), ox + 120, oy - 24, "DMX · amber · WT32 IO4"))
    items.append(lambda ox, oy: callout(ox + e.BUTTON_X*S + 22, zy(oy, e.CONTROLS_Z) - 22, ox + 360, oy - 24, "HOUSE · 16 mm momentary"))
    items.append(lambda ox, oy: dim_h(ox, ox + e.BUTTON_X*S, oy + HT*S + 4, f"{e.BUTTON_X:.0f}", above=False) if False else "")
    return panel(e.W, items, "Front panel", pad=(40, 70, 84, 64))


def back():
    """Seen from behind: the box's X runs right to left."""
    def bx(ox, x):
        return ox + (e.W - x) * S
    zb = e.PI_Z + e.PI_PCB
    ez = e.FLOOR + e.ESP_BED + 1.6
    usb_x = [e.pi_xy(0, a)[0] for a in e.PI_USB_AT]
    eth_x = e.pi_xy(0, e.PI_ETH_AT)[0]

    def rect(ox, oy, x0, x1, z0, z1, cls="hole"):
        return (f'<rect class="{cls}" x="{f(bx(ox, x1))}" y="{f(zy(oy, z1))}" width="{f((x1-x0)*S)}" '
                f'height="{f((z1-z0)*S)}" rx="3"/>')
    items = [
        lambda ox, oy: rect(ox, oy, min(usb_x)-7.6, max(usb_x)+7.6, zb-0.6, zb+e.PI_USB_H+0.8),
        lambda ox, oy: rect(ox, oy, eth_x-8.3, eth_x+8.3, zb-0.6, zb+e.PI_ETH_H+0.8),
        lambda ox, oy: rect(ox, oy, e.ESP_CX-e.ESP_RJ45_W/2, e.ESP_CX+e.ESP_RJ45_W/2, ez-0.6, ez+e.ESP_RJ45_H),
    ]
    for n, cx in enumerate(e.XLR_CX, 1):
        def xlr(ox, oy, cx=cx, n=n):
            c = [f'<rect class="flange" x="{f(bx(ox, cx)-13*S)}" y="{f(zy(oy, e.XLR_CZ)-15.5*S)}" width="{f(26*S)}" height="{f(31*S)}" rx="6"/>',
                 f'<circle class="hole" cx="{f(bx(ox, cx))}" cy="{f(zy(oy, e.XLR_CZ))}" r="{f(e.XLR_HOLE/2*S)}"/>']
            for sx, sz in ((-e.XLR_SCREW_OFF[0], e.XLR_SCREW_OFF[1]), (e.XLR_SCREW_OFF[0], -e.XLR_SCREW_OFF[1])):
                c.append(f'<circle class="hole" cx="{f(bx(ox, cx+sx))}" cy="{f(zy(oy, e.XLR_CZ+sz))}" r="{f(e.XLR_SCREW/2*S)}"/>')
            for k, (px, pz) in enumerate(((-4.5, 2.5), (4.5, 2.5), (0, -4.5)), 1):
                c.append(f'<circle class="pin" cx="{f(bx(ox, cx)+px*S)}" cy="{f(zy(oy, e.XLR_CZ)+pz*S)}" r="5"/>')
            c.append(f'<text class="eng" x="{f(bx(ox, cx))}" y="{f(zy(oy, 3.2)+5)}" text-anchor="middle">DMX {n}</text>')
            return "".join(c)
        items.append(xlr)
    items += [
        lambda ox, oy: f'<text class="eng" x="{f(bx(ox, (min(usb_x)+max(usb_x))/2))}" y="{f(zy(oy, zb+e.PI_USB_H+4)+5)}" text-anchor="middle">USB</text>',
        lambda ox, oy: f'<text class="eng" x="{f(bx(ox, eth_x))}" y="{f(zy(oy, zb+e.PI_ETH_H+4)+5)}" text-anchor="middle">DJ LINK</text>',
        lambda ox, oy: f'<text class="eng" x="{f(bx(ox, e.ESP_CX))}" y="{f(zy(oy, ez+e.ESP_RJ45_H+3.4)+5)}" text-anchor="middle">ART-NET</text>',
        lambda ox, oy: callout(bx(ox, eth_x), zy(oy, zb + 6), ox + 6, oy - 50, "DJ LINK · Pi Ethernet → booth switch"),
        lambda ox, oy: callout(bx(ox, e.ESP_CX), zy(oy, e.ESP_BED + e.FLOOR + 8), bx(ox, e.ESP_CX) + 10, oy - 24, "ART-NET · Node Ethernet → booth switch"),
        lambda ox, oy: callout(bx(ox, e.XLR_CX[0]) + 12, zy(oy, e.XLR_CZ) + 50, bx(ox, e.XLR_CX[0]) - 60, oy + HT*S + 62, "DMX 1 · female XLR-3 · 1 shield · 2 data− · 3 data+", "end"),
    ]
    return panel(e.W, items, "Back panel, seen from behind", pad=(40, 70, 84, 96))


def right():
    """Right wall seen from outside: front on the left, back on the right."""
    zb = e.PI_Z + e.PI_PCB
    uy = e.PI_Y0 + e.PI_USBC_AT
    items = [
        lambda ox, oy: (f'<rect class="hole" x="{f(ox+(uy-6.5)*S)}" y="{f(zy(oy, zb+1.6+4))}" width="{f(13*S)}" height="{f(8*S)}" rx="{f(3.5*S)}"/>'
                        f'<text class="eng" x="{f(ox+uy*S)}" y="{f(zy(oy, zb+13)+5)}" text-anchor="middle">POWER</text>'),
    ]
    for i in range(e.VENT_N):
        y = e.VENT_Y0 + i * e.VENT_PITCH
        items.append(lambda ox, oy, y=y: f'<rect class="hole" x="{f(ox+(y-1.3)*S)}" y="{f(zy(oy, e.FLOOR+14+8))}" width="{f(2.6*S)}" height="{f(16*S)}" rx="{f(1.3*S)}"/>')
    items.append(lambda ox, oy: callout(ox + uy*S, zy(oy, zb + 5), ox + 6, oy - 50, "POWER · USB-C, 5.1 V 3 A"))
    items.append(lambda ox, oy: callout(ox + (e.VENT_Y0 + 5*e.VENT_PITCH)*S, zy(oy, e.FLOOR + 22), ox + e.D*S - 6, oy - 24, "Air in (both sides)", "end"))
    items.append(lambda ox, oy: f'<text class="axis" x="{ox}" y="{f(oy+HT*S+48)}">FRONT</text><text class="axis" x="{f(ox+e.D*S)}" y="{f(oy+HT*S+48)}" text-anchor="end">BACK</text>')
    return panel(e.D, items, "Right side", pad=(40, 70, 84, 60))


def plan():
    """Top view with the lid off: back at the top."""
    l, t = 40, 30
    def X(x): return l + x * S
    def Y(y): return t + (e.D - y) * S
    out = [f'<rect class="face" x="{X(0)}" y="{Y(e.D)}" width="{f(e.W*S)}" height="{f(e.D*S)}" rx="{f(e.CORNER_R*S)}"/>',
           f'<rect class="cavity" x="{X(e.WALL)}" y="{Y(e.D-e.WALL)}" width="{f(e.IN_W*S)}" height="{f(e.IN_D*S)}" rx="{f((e.CORNER_R-e.WALL)*S)}"/>']

    def r(x0, y0, x1, y1, cls, label=None, sub=None):
        s = f'<rect class="{cls}" x="{f(X(x0))}" y="{f(Y(y1))}" width="{f((x1-x0)*S)}" height="{f((y1-y0)*S)}" rx="4"/>'
        if label:
            s += f'<text class="part" x="{f(X((x0+x1)/2))}" y="{f(Y((y0+y1)/2)-2)}" text-anchor="middle">{label}</text>'
        if sub:
            s += f'<text class="partsub" x="{f(X((x0+x1)/2))}" y="{f(Y((y0+y1)/2)+16)}" text-anchor="middle">{sub}</text>'
        return s
    # Pi and its parts
    out.append(r(e.PI_X0, e.PI_Y0, e.PI_X1, e.PI_Y1, "board pi", "Raspberry Pi 4", "85 × 56 mm"))
    eth = e.pi_xy(0, e.PI_ETH_AT)[0]
    out.append(r(eth - 8, e.PI_Y1 - 21, eth + 8, e.PI_Y1 + e.PI_PORT_OVERHANG, "port"))
    for a in e.PI_USB_AT:
        x = e.pi_xy(0, a)[0]
        out.append(r(x - 7, e.PI_Y1 - 17, x + 7, e.PI_Y1 + e.PI_PORT_OVERHANG, "port"))
    hx = e.pi_xy(0, 52)[0]
    out.append(r(hx - 2.5, e.PI_Y0 + 7, hx + 2.5, e.PI_Y0 + 58, "header"))
    fx, fy = e.pi_xy(*e.FAN["at"])
    out.append(f'<circle class="fan" cx="{f(X(fx))}" cy="{f(Y(fy))}" r="{f(15*S)}"/>')
    out.append(f'<text class="partsub" x="{f(X(fx))}" y="{f(Y(fy)+4)}" text-anchor="middle">30 mm fan (in lid)</text>')
    for a85, a56 in e.PI_HOLES:
        x, y = e.pi_xy(a85, a56)
        out.append(f'<circle class="post" cx="{f(X(x))}" cy="{f(Y(y))}" r="{f(3*S)}"/>')
    # WT32, RS-485, XLR, controls, columns
    ey1 = e.D - e.WALL
    out.append(r(e.ESP_CX - 12.5, ey1 - 60, e.ESP_CX + 12.5, ey1, "board node", "WT32", "ETH01"))
    out.append(r(e.ESP_CX - 8, ey1 - 21, e.ESP_CX + 8, ey1 + 0.5, "port"))
    rx0, ry0 = e.XLR_CX[0] - 12, e.D - e.WALL - 26 - e.RS485_WID - 6
    out.append(r(rx0 + 1, ry0 + 1, rx0 + 45, ry0 + 15, "board rs", "MAX485"))
    for n, cx in enumerate(e.XLR_CX, 1):
        out.append(r(cx - 11.5, e.D - e.WALL - 21, cx + 11.5, e.D - e.WALL, "metal", f"DMX {n}"))
    out.append(r(e.BUTTON_X - 8, e.WALL, e.BUTTON_X + 8, e.WALL + 28, "metal", "HOUSE"))
    for x in e.LED_X:
        out.append(r(x - 2.6, e.WALL, x + 2.6, e.WALL + 9, "led"))
    for x, y in e.COLUMNS:
        out.append(f'<circle class="post" cx="{f(X(x))}" cy="{f(Y(y))}" r="{f(e.COLUMN_D/2*S)}"/>')
    # zones
    split = e.PI_X0 - 5.5
    out.append(f'<line class="zone" x1="{f(X(split))}" y1="{f(Y(e.D-e.WALL)+10)}" x2="{f(X(split))}" y2="{f(Y(e.WALL)-10)}"/>')
    out.append(f'<text class="zonelabel" x="{f(X(e.WALL))}" y="{f(Y(e.D)-8)}">LIGHTS SIDE</text>')
    out.append(f'<text class="zonelabel" x="{f(X(e.W-e.WALL))}" y="{f(Y(e.D)-8)}" text-anchor="end">CONTROL SIDE</text>')
    out.append(f'<text class="axis" x="{f(X(e.W/2))}" y="{f(Y(e.D)-8)}" text-anchor="middle">BACK</text>')
    out.append(f'<text class="axis" x="{f(X(e.W/2))}" y="{f(Y(0)+62)}" text-anchor="middle">FRONT</text>')
    out.append(f'<text class="axis" x="{f(X(e.W)+10)}" y="{f(Y(e.PI_Y0+e.PI_USBC_AT)+4)}">◀ POWER</text>')
    out.append(dim_h(X(0), X(e.W), Y(0) + 20, f"{e.W:.0f} mm", above=False))
    out.append(dim_v(X(e.W) + 70, Y(e.D), Y(0), f"{e.D:.0f}"))
    vw, vh = l + e.W * S + 120, t + e.D * S + 76
    return f'<svg viewBox="0 0 {f(vw)} {f(vh)}" role="img" aria-label="Inside, top view" class="drawing">' + "".join(out) + "</svg>"


def schematic():
    """The whole box's wiring as a schematic. Each part's pins face the part they connect
    to; grounds use the ground symbol, labelled with the Pi pin they go to."""
    o = []

    def box_(x, y, w, h, title, sub, pins, cls="ic"):
        """pins: (key, label, side) with side "l" or "r"; each side numbered from the top."""
        s_ = [f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="6"/>',
              f'<text class="ictitle" x="{x+12}" y="{y+22}">{title}</text>',
              f'<text class="icsub" x="{x+12}" y="{y+38}">{sub}</text>']
        pts, row = {}, {"l": 0, "r": 0}
        for key, label, side in pins:
            py = y + 62 + row[side] * 30
            row[side] += 1
            if side == "r":
                px = x + w
                s_.append(f'<line class="pinline" x1="{px}" y1="{py}" x2="{px+18}" y2="{py}"/><text class="pinl" x="{px-8}" y="{py+4}" text-anchor="end">{label}</text>')
                pts[key] = (px + 18, py)
            else:
                s_.append(f'<line class="pinline" x1="{x}" y1="{py}" x2="{x-18}" y2="{py}"/><text class="pinl" x="{x+8}" y="{py+4}">{label}</text>')
                pts[key] = (x - 18, py)
        o.append("".join(s_))
        return pts

    def wire(points, cls="w sig"):
        o.append(f'<polyline class="{cls}" points="{" ".join(f"{a},{b}" for a, b in points)}"/>')

    def gnd(x, y, note="", dx=14):
        o.append(f'<g class="gnd"><line x1="{x}" y1="{y}" x2="{x}" y2="{y+10}"/><line x1="{x-10}" y1="{y+10}" x2="{x+10}" y2="{y+10}"/>'
                 f'<line x1="{x-6}" y1="{y+14}" x2="{x+6}" y2="{y+14}"/><line x1="{x-2}" y1="{y+18}" x2="{x+2}" y2="{y+18}"/></g>')
        if note:
            o.append(f'<text class="net" x="{x+dx}" y="{y+17}">{note}</text>')

    def flag(x, y, text):
        o.append(f'<g class="v5"><line x1="{x}" y1="{y}" x2="{x}" y2="{y-12}"/><polygon points="{x-8},{y-12} {x+8},{y-12} {x},{y-24}"/></g>'
                 f'<text class="net" x="{x}" y="{y-30}" text-anchor="middle">{text}</text>')

    def resistor(x, y):
        o.append(f'<rect class="res" x="{x}" y="{y-7}" width="34" height="14" rx="2"/><text class="val" x="{x+17}" y="{y-12}" text-anchor="middle">220 Ω</text>')
        return (x, y), (x + 34, y)

    def led(x, y, cls):
        o.append(f'<g class="ledsym {cls}"><polygon points="{x},{y-10} {x},{y+10} {x+18},{y}"/><line x1="{x+18}" y1="{y-10}" x2="{x+18}" y2="{y+10}"/>'
                 f'<line x1="{x+12}" y1="{y-14}" x2="{x+20}" y2="{y-22}"/><line x1="{x+18}" y1="{y-16}" x2="{x+26}" y2="{y-24}"/></g>')
        return (x, y), (x + 18, y)

    def button(x, y):
        o.append(f'<g class="btn"><line x1="{x}" y1="{y}" x2="{x+14}" y2="{y}"/><line x1="{x+14}" y1="{y}" x2="{x+40}" y2="{y-12}"/>'
                 f'<line x1="{x+40}" y1="{y}" x2="{x+54}" y2="{y}"/><line x1="{x+27}" y1="{y-10}" x2="{x+27}" y2="{y-22}"/><line x1="{x+17}" y1="{y-22}" x2="{x+37}" y2="{y-22}"/></g>')
        return (x, y), (x + 54, y)

    pi = box_(40, 40, 210, 404, "Raspberry Pi 4", "40-pin header · pins used", [
        ("1", "1 · 3V3", "r"), ("2", "2 · 5V", "r"), ("4", "4 · 5V", "r"), ("6", "6 · GND", "r"), ("9", "9 · GND", "r"),
        ("11", "11 · GPIO17", "r"), ("14", "14 · GND", "r"), ("20", "20 · GND", "r"), ("25", "25 · GND", "r"),
        ("30", "30 · GND", "r"), ("34", "34 · GND", "r")])
    o.append('<text class="icsub" x="52" y="470">Powered by its own USB-C supply</text><text class="icsub" x="52" y="486">(5.1 V, 3 A); it feeds everything else.</text>')
    notes = {"2": "WT32 5V", "4": "MAX485 VCC", "9": "NET LED −", "14": "WT32 GND", "20": "MAX485 GND",
             "25": "XLR pin 1", "30": "DMX LED −", "34": "HOUSE button"}
    for k, txt in notes.items():
        x, y = pi[k]
        o.append(f'<text class="netdim" x="{x+8}" y="{y-6}">→ {txt}</text>')

    # fan on 3V3 (pin 1) and GND (pin 6)
    y1, y6 = pi["1"][1], pi["6"][1]
    wire([pi["1"], (440, y1), (440, y1 + 22)], "w pwr")
    o.append(f'<circle class="motor" cx="440" cy="{y1+42}" r="20"/><text class="val" x="440" y="{y1+47}" text-anchor="middle">M</text>')
    wire([(440, y1 + 62), (440, y6), pi["6"]], "w gndw")
    o.append(f'<text class="net" x="470" y="{y1+38}">30 mm fan</text><text class="net" x="470" y="{y1+54}">on 3.3 V</text>')

    # NET LED: pin 11 -> 220R -> LED -> GND (pin 9)
    y = pi["11"][1]
    wire([pi["11"], (330, y)])
    _, rb = resistor(330, y)
    la, lb = led(386, y, "net")
    wire([rb, la]); wire([lb, (424, y)], "w gndw"); gnd(424, y)
    o.append(f'<text class="net" x="446" y="{y+4}">NET LED, green</text>')

    # WT32: power on the left (toward the Pi), signals on the right (toward the MAX485)
    wt = box_(560, 300, 200, 224, "WT32-ETH01", "Lumière Node · ESP32 + Ethernet", [
        ("5v", "5V", "l"), ("gnd", "GND", "l"),
        ("txd", "TXD · IO17", "r"), ("en", "485_EN · IO33", "r"), ("io4", "IO4", "r"), ("io14", "IO14", "r")], cls="ic node")
    flag(wt["5v"][0] - 34, wt["5v"][1], "+5V · Pi 2"); wire([wt["5v"], (wt["5v"][0] - 34, wt["5v"][1])], "w pwr")
    wire([wt["gnd"], (wt["gnd"][0] - 34, wt["gnd"][1])], "w gndw"); gnd(wt["gnd"][0] - 34, wt["gnd"][1], "Pi 14", -54)
    o.append('<text class="icsub" x="572" y="512">IO0 stays bare: Ethernet clock</text>')

    # MAX485: inputs on the left, A/B outputs on the right
    rs = box_(900, 40, 170, 220, "MAX485 module", "RS-485 transceiver", [
        ("vcc", "VCC", "l"), ("gnd", "GND", "l"), ("di", "DI", "l"), ("de", "DE", "l"), ("re", "RE", "l"),
        ("a", "A", "r"), ("b", "B", "r")])
    flag(rs["vcc"][0] - 34, rs["vcc"][1], "+5V · Pi 4"); wire([rs["vcc"], (rs["vcc"][0] - 34, rs["vcc"][1])], "w pwr")
    wire([rs["gnd"], (rs["gnd"][0] - 34, rs["gnd"][1])], "w gndw"); gnd(rs["gnd"][0] - 34, rs["gnd"][1], "Pi 20", -54)
    # WT32 -> MAX485
    wire([wt["txd"], (815, wt["txd"][1]), (815, rs["di"][1]), rs["di"]])
    wire([wt["en"], (835, wt["en"][1]), (835, rs["de"][1]), rs["de"]])
    o.append(f'<circle class="dot" cx="{rs["de"][0]+8}" cy="{rs["de"][1]}" r="3.5"/>')
    wire([(rs["de"][0] + 8, rs["de"][1]), (rs["de"][0] + 8, rs["re"][1]), rs["re"]])
    o.append(f'<text class="net" x="{rs["re"][0]-128}" y="{rs["re"][1]+4}">DE–RE bridged</text>')
    # DMX LED and HOUSE button off the WT32
    y = wt["io4"][1]
    wire([wt["io4"], (800, y)])
    _, rb = resistor(800, y); la, lb = led(852, y, "dmx")
    wire([rb, la]); wire([lb, (890, y)], "w gndw"); gnd(890, y)
    o.append(f'<text class="net" x="912" y="{y+4}">DMX LED, amber · GND Pi 30</text>')
    y = wt["io14"][1]
    wire([wt["io14"], (800, y)])
    ba, bb = button(800, y)
    wire([bb, (890, y)], "w gndw"); gnd(890, y)
    o.append(f'<text class="net" x="912" y="{y+4}">HOUSE button · GND Pi 34</text>')

    # XLR (pins listed 3, 2, 1 from the top so A and B run straight)
    o.append('<rect class="ic xlr" x="1150" y="60" width="140" height="190" rx="70"/>')
    o.append('<text class="ictitle" x="1220" y="104" text-anchor="middle">DMX 1</text><text class="icsub" x="1220" y="120" text-anchor="middle">female XLR-3</text>')
    xl = {"3": (1150, 150), "2": (1150, 176), "1": (1150, 202)}
    for k, (x, yy) in xl.items():
        o.append(f'<text class="pinl" x="{x+14}" y="{yy+4}">pin {k}</text>')
    wire([rs["a"], (1110, rs["a"][1]), (1110, xl["3"][1]), xl["3"]], "w dmx")
    wire([rs["b"], (1096, rs["b"][1]), (1096, xl["2"][1]), xl["2"]], "w dmx2")
    wire([xl["1"], (1124, xl["1"][1]), (1124, 230)], "w gndw"); gnd(1124, 230, "Pi 25 · shield", -40)
    o.append(f'<text class="net" x="1104" y="{rs["a"][1]-8}">A = data +</text><text class="net" x="1104" y="290">B = data −</text>')
    return '<svg viewBox="0 0 1310 560" role="img" aria-label="Wiring schematic" class="drawing schem">' + "".join(o) + "</svg>"


if __name__ == "__main__":
    import json
    print(json.dumps({"front": front(), "back": back(), "right": right(), "plan": plan(), "schematic": schematic()}))
