#!/usr/bin/env bash
# Lumière Resident: set up a Raspberry Pi (Raspberry Pi OS Lite) to run the
# venue box on its own. Run it from the Lumière Link folder, as your normal user:
#
#   bash resident/install.sh
#
# It runs QLC+ 5 (built from source, the same version as on the Mac) on a
# virtual screen you can see from the Mac with Screen Sharing. QLC=4 bash
# resident/install.sh uses Debian's QLC+ 4 instead, with no screen at all.
#
# Safe to run again (after a git pull, say): it only changes what needs changing.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ME="$(id -un)"
SHOW_DIR="$HOME/Lumiere"
SCREEN_PORT=8080
NET_LED_GPIO=17   # physical pin 11
QLC="${QLC:-5}"

step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

step "1/7  Installing Lumière Link's parts"
sudo apt-get update -q
sudo apt-get install -y -q python3-mido python3-rtmidi python3-numpy python3-gpiozero python3-lgpio avahi-daemon
# MIDI between Lumière Link and QLC+, and the LED pins
sudo usermod -aG audio,gpio "$ME"

step "2/7  QLC+ $QLC"
if [ "$QLC" = 4 ]; then
    sudo apt-get install -y -q qlcplus
else
    # The virtual screen QLC+ 5 draws on, and Screen Sharing (VNC) to see it from the Mac
    sudo apt-get install -y -q xvfb x11vnc matchbox-window-manager libgl1-mesa-dri
    bash "$REPO/resident/build-qlcplus5.sh"
    if [ ! -s "$HOME/.vnc/passwd" ]; then
        echo
        echo "Choose a password for Screen Sharing (seeing QLC+ on the Pi from the Mac), 8 characters at most:"
        mkdir -p "$HOME/.vnc"
        x11vnc -storepasswd "$HOME/.vnc/passwd"
    fi
fi

step "3/7  Ethernet: CDJs on 169.254.77.1 and the Lumière Node (Art-Net) on 2.0.0.2"
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

step "4/7  The show folder for QLC+: $SHOW_DIR"
mkdir -p "$SHOW_DIR"

step "5/7  Starting by themselves at power-on, and restarting if they ever stop"
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

if [ "$QLC" = 4 ]; then
    QLC_START="$REPO/resident/run-qlcplus.sh $SHOW_DIR/show.qxw"
    QLC_ENV="QT_QPA_PLATFORM=offscreen"
    QLC_AFTER="lumiere-link.service"
    sudo systemctl disable --now lumiere-display.service lumiere-wm.service lumiere-vnc.service 2>/dev/null || true
else
    QLC_START="$REPO/resident/run-qlcplus5.sh $SHOW_DIR/show.qxw"
    QLC_ENV="DISPLAY=:1"
    QLC_AFTER="lumiere-link.service lumiere-display.service lumiere-wm.service"
    sudo tee /etc/systemd/system/lumiere-display.service >/dev/null <<UNIT
[Unit]
Description=Virtual screen for QLC+ (display :1)

[Service]
User=$ME
ExecStart=/usr/bin/Xvfb :1 -screen 0 1920x1080x24 -nolisten tcp
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
    # A kiosk window manager on the virtual screen: it makes QLC+ fill the screen
    # (without one, "maximise" and "full screen" have nothing to act on).
    sudo tee /etc/systemd/system/lumiere-wm.service >/dev/null <<UNIT
[Unit]
Description=Window manager for the QLC+ screen
After=lumiere-display.service
Requires=lumiere-display.service

[Service]
User=$ME
Environment=DISPLAY=:1
ExecStartPre=/bin/sleep 2
ExecStart=/usr/bin/matchbox-window-manager -use_titlebar no
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
    sudo tee /etc/systemd/system/lumiere-vnc.service >/dev/null <<UNIT
[Unit]
Description=Screen Sharing (VNC) for the QLC+ screen
After=lumiere-display.service
Requires=lumiere-display.service

[Service]
User=$ME
ExecStart=/usr/bin/x11vnc -display :1 -rfbauth $HOME/.vnc/passwd -rfbport 5900 -forever -shared -noxdamage -quiet
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
fi

sudo tee /etc/systemd/system/lumiere-qlcplus.service >/dev/null <<UNIT
[Unit]
Description=QLC+ for Lumière Resident (MIDI in from Lumière Link, Art-Net out to the Node)
After=$QLC_AFTER
Wants=$QLC_AFTER

[Service]
User=$ME
WorkingDirectory=$SHOW_DIR
ExecStartPre=/bin/sleep 5
ExecStart=$QLC_START
Environment=$QLC_ENV
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
chmod +x "$REPO"/resident/*.sh

step "6/7  Hardware watchdog: the Pi restarts itself if it ever freezes"
sudo mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15s\n' | sudo tee /etc/systemd/system.conf.d/lumiere-watchdog.conf >/dev/null

step "7/7  Starting everything"
sudo systemctl daemon-reload
SERVICES="lumiere-link.service lumiere-qlcplus.service"
[ "$QLC" = 4 ] || SERVICES="lumiere-display.service lumiere-wm.service lumiere-vnc.service $SERVICES"
sudo systemctl enable $SERVICES
for svc in $SERVICES; do sudo systemctl restart "$svc"; done
sudo nmcli connection up lumiere-eth >/dev/null 2>&1 || true  # fine if no cable is in yet

LINK_FILE="$HOME/.config/lumiere-link/screen-link.txt"
for _ in $(seq 1 20); do [ -s "$LINK_FILE" ] && break; sleep 1; done
echo
echo "Lumière Resident is set up."
echo
echo "  Lumière Link screen (bookmark it on your phone):"
echo "    $(cat "$LINK_FILE" 2>/dev/null || echo "not ready yet: run  cat $LINK_FILE  in a minute")"
echo "  QLC+ web page:  http://$(hostname).local:9999"
[ "$QLC" = 4 ] || echo "  QLC+ screen from the Mac: Finder > Go > Connect to Server > vnc://$(hostname).local"
echo
echo "  Status:  systemctl status lumiere-link lumiere-qlcplus"
echo "  Logs:    journalctl -u lumiere-link -f      (Ctrl+C to leave)"
