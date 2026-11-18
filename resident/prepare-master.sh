#!/usr/bin/env bash
# Run on a fully set-up Resident just before you shut it down to copy its microSD
# card as the master image for production. It removes what must be different on
# every box, which each box then makes for itself the first time it starts:
#   - the screen link's password (Lumière Link makes a new one)
#   - the SSH host keys and the machine id (remade on first boot)
#   - logs, shell history and the Studio rig of this test box
# Then it shuts the Pi down. Usage:  bash resident/prepare-master.sh
set -euo pipefail
DATA="${XDG_CONFIG_HOME:-$HOME/.config}/lumiere-link"

echo "This prepares this Pi's card as the master image and shuts the Pi down."
read -r -p "Type MASTER to go on: " answer
[ "$answer" = "MASTER" ] || { echo "Stopped, nothing changed."; exit 1; }

# A one-off job for the next start: new SSH keys and machine id on each box
sudo tee /etc/systemd/system/lumiere-firstboot.service >/dev/null <<'UNIT'
[Unit]
Description=Lumière: give this box its own SSH keys and machine id (first start of a copied card)
Before=ssh.service
ConditionPathExists=!/etc/ssh/ssh_host_ed25519_key

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A
ExecStart=/bin/sh -c 'rm -f /etc/machine-id /var/lib/dbus/machine-id && systemd-machine-id-setup && ln -sf /etc/machine-id /var/lib/dbus/machine-id'

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl enable lumiere-firstboot.service

sudo systemctl stop lumiere-link.service lumiere-qlcplus.service 2>/dev/null || true
rm -f "$DATA/screen-token" "$DATA/screen-link.txt" "$DATA/studio-rig.json" "$DATA"/*.log
sudo rm -f /etc/ssh/ssh_host_*
sudo journalctl --rotate >/dev/null 2>&1 && sudo journalctl --vacuum-time=1s >/dev/null 2>&1 || true
rm -f "$HOME/.bash_history"
echo "Done. Shutting down: copy the card once the green light stops flashing."
sudo shutdown -h now
