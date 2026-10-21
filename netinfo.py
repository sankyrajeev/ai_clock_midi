"""This Mac's network interfaces, and fixing a CDJ that sits on another subnet.

Pro DJ Link gear normally self-assigns 169.254.x.x addresses on a plain
switch. Things go wrong when a player keeps an old address, for example
192.168.1.23 left over from a venue router, while the Mac is on 169.254.x.x.
Broadcast beats still arrive, but the players' status and the track
database don't, because those are addressed directly and the two ends can't
route to each other. Instead of restarting the CDJ, we give the Mac's
Ethernet port a second address on the player's network.
"""

from __future__ import annotations

import ipaddress
import random
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")


@dataclass
class Addr:
    ip: str
    netmask: str
    broadcast: str

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(f"{self.ip}/{self.netmask}", strict=False)


@dataclass
class Iface:
    name: str
    mac: Optional[bytes] = None
    up: bool = False
    active: Optional[bool] = None     # link status, when the OS reports it
    port: str = ""                    # e.g. "Wi-Fi", "USB 10/100/1000 LAN"
    addrs: list[Addr] = field(default_factory=list)

    @property
    def wired(self) -> bool:
        if not re.fullmatch(r"en\d+", self.name):
            return False
        return "wi-fi" not in self.port.lower() and "airport" not in self.port.lower()

    @property
    def has_link(self) -> bool:
        return self.up and self.active is not False

    @property
    def label(self) -> str:
        return f"{self.port} ({self.name})" if self.port else self.name


def _mask(text: str) -> str:
    return str(ipaddress.IPv4Address(int(text, 16))) if text.startswith("0x") else text


def parse_ifconfig(text: str) -> list[Iface]:
    """Parse macOS/BSD `ifconfig` output."""
    ifaces = []
    for block in re.split(r"\n(?=\S)", text.strip()):
        head = re.match(r"([\w.]+): flags=\w+<([^>]*)>", block)
        if not head:
            continue
        iface = Iface(name=head.group(1), up="UP" in head.group(2).split(","))
        if m := re.search(r"\bether ([0-9a-f:]{17})", block):
            iface.mac = bytes.fromhex(m.group(1).replace(":", ""))
        if m := re.search(r"\bstatus: (\w+)", block):
            iface.active = m.group(1) == "active"
        for m in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+) netmask (\S+)(?: broadcast (\d+\.\d+\.\d+\.\d+))?", block):
            ip, mask = m.group(1), _mask(m.group(2))
            bcast = m.group(3) or str(ipaddress.ip_network(f"{ip}/{mask}", strict=False).broadcast_address)
            iface.addrs.append(Addr(ip, mask, bcast))
        ifaces.append(iface)
    return ifaces


def parse_hardware_ports(text: str) -> dict[str, str]:
    """`networksetup -listallhardwareports` -> {device: port name}."""
    ports = {}
    for m in re.finditer(r"Hardware Port: (.+)\nDevice: (\S+)", text):
        ports[m.group(2)] = m.group(1).strip()
    return ports


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def interfaces() -> list[Iface]:
    ifaces = parse_ifconfig(_run(["ifconfig"]))
    ports = parse_hardware_ports(_run(["networksetup", "-listallhardwareports"]))
    for i in ifaces:
        i.port = ports.get(i.name, "")
    return ifaces


def local_ips(ifaces: list[Iface]) -> set[str]:
    return {a.ip for i in ifaces for a in i.addrs}


def find_addr_for(peer_ip: str, ifaces: list[Iface]) -> tuple[Optional[Iface], Optional[Addr]]:
    """The interface and address on the same subnet as peer_ip, preferring wired ports."""
    peer = ipaddress.ip_address(peer_ip)
    matches = [(i, a) for i in ifaces if i.has_link for a in i.addrs
               if peer in a.network and not a.ip.startswith("127.")]
    if not matches:
        return None, None
    matches.sort(key=lambda m: (not m[0].wired,))
    return matches[0]


def guess_network(peer_ip: str) -> ipaddress.IPv4Network:
    """Best guess at a player's subnet when we can't ask it."""
    ip = ipaddress.ip_address(peer_ip)
    if ip in LINK_LOCAL:
        return LINK_LOCAL
    return ipaddress.ip_network(f"{peer_ip}/24", strict=False)


def choose_alias_ip(peer_ip: str, taken: set[str]) -> tuple[str, str]:
    """A free address (and netmask) on the player's network for the Mac to use."""
    net = guess_network(peer_ip)
    if net == LINK_LOCAL:
        rng = random.Random(peer_ip)
        while True:
            ip = f"169.254.{rng.randint(1, 254)}.{rng.randint(1, 254)}"
            if ip not in taken:
                return ip, str(net.netmask)
    hosts = list(net.hosts())
    for host in reversed(hosts[-60:]):  # top of the range, away from routers and DHCP pools
        if str(host) not in taken:
            return str(host), str(net.netmask)
    raise LookupError(f"no free address on {net}")


def wired_with_link(ifaces: list[Iface]) -> list[Iface]:
    return [i for i in ifaces if i.wired and i.has_link]


def add_alias(iface: str, ip: str, netmask: str) -> tuple[bool, str]:
    """Give `iface` an extra address. Asks for an admin password with the normal macOS dialog."""
    if not re.fullmatch(r"en\d+", iface):
        return False, f"refusing to change unexpected interface {iface!r}"
    ipaddress.IPv4Address(ip)
    ipaddress.IPv4Address(netmask)
    if sys.platform != "darwin":
        return False, f"automatic fix only works on macOS; run: sudo ifconfig {iface} alias {ip} netmask {netmask}"
    command = f"/sbin/ifconfig {iface} alias {ip} netmask {netmask}"
    prompt = "ProLink Sync needs to add a network address so it can talk to your CDJs."
    script = f'do shell script "{command}" with administrator privileges with prompt "{prompt}"'
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if result.returncode != 0:
        return False, result.stderr.strip() or "cancelled"
    return True, f"added {ip} to {iface}"


def nothing_found_advice(ifaces: list[Iface]) -> str:
    """Why no DJ Link devices are answering, in terms of what to check physically."""
    wired = [i for i in ifaces if i.wired]
    if not wired:
        return ("No Ethernet port found on this Mac. Connect the switch with an Ethernet "
                "adapter (USB or Thunderbolt).")
    linked = [i for i in wired if i.has_link]
    if not linked:
        names = ", ".join(i.label for i in wired)
        return (f"No Ethernet link on {names}. Check the cable between this Mac and the switch, "
                "and that the switch is powered on.")
    if not any(a for i in linked for a in i.addrs):
        return (f"{linked[0].label} is connected but has no network address yet. macOS usually "
                "assigns one within 10 seconds. If it never does, set Configure IPv4 to "
                "\"Using DHCP\" for that port in System Settings > Network.")
    return ("This Mac is on the network but no CDJs or mixer are answering. Check the cables "
            "from the players to the switch, and that the players are switched on.")
