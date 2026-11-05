# Lumière Resident: the Pi

The Resident is a Raspberry Pi 4 that runs **Lumière Link** and **QLC+** on
its own at a venue, sending Art-Net to the **Lumière Node** (the DMX box).

```
CDJs + DJM ──┐                           ┌── Lumière Node (2.0.0.10) ── XLR ── lights
             ├── switch ── Pi (eth0) ────┘
             │      Lumière Link ──MIDI──▶ QLC+ ──Art-Net──▶
```

## Setting up a new Pi

1. Write **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager: hostname
   `lumiere-resident`, a username and password, your Wi-Fi (country set), and
   **SSH on**.
2. Log in from the Mac: `ssh <username>@lumiere-resident.local`
3. Run:
   ```bash
   sudo apt-get install -y git
   git clone https://github.com/sankyrajeev/ai_clock_midi ~/ai_clock_midi
   cd ~/ai_clock_midi && git checkout beta-resident && bash resident/install.sh
   ```
4. It prints the **Lumière Link screen link**: bookmark it on your phone.

To update later: `cd ~/ai_clock_midi && git pull && bash resident/install.sh`

## What the setup does

| | |
|---|---|
| Lumière Link | runs with no screen (`--headless`), screen on port 8080 with a fixed link, starts at power-on, restarts within 2 s if it stops |
| QLC+ | runs with no screen, web page on port 9999, opens `~/Lumiere/show.qxw` in operate mode if it's there |
| Ethernet (eth0) | fixed addresses 169.254.77.1 (CDJs) and 2.0.0.2 (the Node); Wi-Fi is left alone |
| NET LED (GPIO17, pin 11) | solid: players connected · blinking: running, no players · off: Lumière Link not running |
| Watchdog | the Pi restarts itself if it freezes for 15 s |

## Checking on it

```bash
systemctl status lumiere-link lumiere-qlcplus
journalctl -u lumiere-link -f        # live log, Ctrl+C to leave
cat ~/.config/lumiere-link/screen-link.txt
```
