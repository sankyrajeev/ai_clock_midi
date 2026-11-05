#!/usr/bin/env bash
# Lumière Resident: set up a Raspberry Pi (Raspberry Pi OS Lite) to run the
# venue box on its own. Run it from the Lumière Link folder, as your normal user:
#
#   bash resident/install.sh
#
# Safe to run again (after a git pull, say): it only changes what needs changing.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ME="$(id -un)"
SHOW_DIR="$HOME/Lumiere"
SCREEN_PORT=8080
NET_LED_GPIO=17   # physical pin 11

step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

step "1/6  Installing Lumière Link's parts and QLC+"
sudo apt-get update -q
sudo apt-get install -y -q python3-mido python3-rtmidi python3-numpy python3-gpiozero python3-lgpio \
    qlcplus avahi-daemon
# MIDI between Lumière Link and QLC+, and the LED pins
sudo usermod -aG audio,gpio "$ME"

step "2/6  Ethernet: CDJs on 169.254.77.1 and the Lumière Node (Art-Net) on 2.0.0.2"
# One fixed setup on the Ethernet port. CDJs on a plain switch give themselves
# 169.254.x.x addresses; the Node sits on 2.0.0.10. Wi-Fi stays as it is, for
# setting up and updates. If players turn up on another network (a venue router),
# Lumière Link adds an address next to them by itself (the sudo rule below).
if ! nmcli -t -f NAME connection show | grep -qx lumiere-eth; then
    sudo nmcli connection add type ethernet ifname eth0 con-name lumiere-eth \
        ipv4.method manual ipv4.addresses "169.254.77.1/16,2.0.0.2/8" ipv4.never-default yes \
        ipv6.method link-local connection.autoconnect yes connection.autoconnect-priority 100
fi
echo "$ME ALL=(root) NOPASSWD: /usr/sbin/ip addr add * dev eth0" | sudo tee /etc/sudoers.d/lumiere-link >/dev/null
sudo chmod 440 /etc/sudoers.d/lumiere-link
sudo visudo -cf /etc/sudoers.d/lumiere-link >/dev/null

step "3/6  The show folder for QLC+: $SHOW_DIR"
mkdir -p "$SHOW_DIR"

step "4/6  Starting by themselves at power-on, and restarting if they ever stop"
sudo tee /etc/systemd/system/lumiere-link.service >/dev/null <<UNIT
[Unit]
Description=Lumière Link (follows the CDJs, sends MIDI to QLC+)
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=$ME
WorkingDirectory=$REPO
ExecStart=/usr/bin/python3 $REPO/lumiere_app.py --headless --port $SCREEN_PORT --net-led $NET_LED_GPIO
ExecStopPost=-/usr/bin/pinctrl set $NET_LED_GPIO op dl
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/lumiere-qlcplus.service >/dev/null <<UNIT
[Unit]
Description=QLC+ for Lumière Resident (MIDI in from Lumière Link, Art-Net out to the Node)
After=lumiere-link.service
Wants=lumiere-link.service

[Service]
User=$ME
WorkingDirectory=$SHOW_DIR
ExecStartPre=/bin/sleep 5
ExecStart=$REPO/resident/run-qlcplus.sh $SHOW_DIR/show.qxw
Environment=QT_QPA_PLATFORM=offscreen
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
chmod +x "$REPO/resident/run-qlcplus.sh"

step "5/6  Hardware watchdog: the Pi restarts itself if it ever freezes"
sudo mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15s\n' | sudo tee /etc/systemd/system.conf.d/lumiere-watchdog.conf >/dev/null

step "6/6  Starting everything"
sudo systemctl daemon-reload
sudo systemctl enable lumiere-link.service lumiere-qlcplus.service
sudo systemctl restart lumiere-link.service
sudo systemctl restart lumiere-qlcplus.service
sudo nmcli connection up lumiere-eth >/dev/null 2>&1 || true  # fine if no cable is in yet

LINK_FILE="$HOME/.config/lumiere-link/screen-link.txt"
for _ in $(seq 1 20); do [ -s "$LINK_FILE" ] && break; sleep 1; done
echo
echo "Lumière Resident is set up."
echo
echo "  Lumière Link screen (bookmark it on your phone):"
echo "    $(cat "$LINK_FILE" 2>/dev/null || echo "not ready yet: run  cat $LINK_FILE  in a minute")"
echo "  QLC+ web page:  http://$(hostname).local:9999"
echo
echo "  Status:  systemctl status lumiere-link lumiere-qlcplus"
echo "  Logs:    journalctl -u lumiere-link -f      (Ctrl+C to leave)"
