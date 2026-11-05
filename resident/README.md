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
   The first time it **builds QLC+ 5 from source (1-2 hours)** and then asks
   for a **Screen Sharing password** (8 characters at most).
4. It prints the **Lumière Link screen link**: bookmark it on your phone.

To update later: `cd ~/ai_clock_midi && git pull && bash resident/install.sh`

## What the setup does

| | |
|---|---|
| Lumière Link | runs with no screen (`--headless`), screen on port 8080 with a fixed link, starts at power-on, restarts within 2 s if it stops |
| QLC+ 5 | built from QLC+'s source at the Mac's version (5.3.0), runs full screen on a virtual screen (display :1), web page on port 9999, opens `~/Lumiere/show.qxw` at power-on |
| Screen Sharing | see and use QLC+ on the Pi from the Mac: Finder › Go › Connect to Server › `vnc://lumiere-resident.local` |
| Ethernet (eth0) | fixed addresses 169.254.77.1 (CDJs) and 2.0.0.2 (the Node); Wi-Fi is left alone |
| NET LED (GPIO17, pin 11) | solid: players connected · blinking: running, no players · off: Lumière Link not running |
| Watchdog | the Pi restarts itself if it freezes for 15 s |

## Putting a show on the Resident

Build it in **QLC+ 5.3 on the Mac** (the same version as the Pi), with every
Virtual Console button mapped to Lumière Link's cues. Then from the Mac:

```bash
scp ~/Desktop/venue.qxw <username>@lumiere-resident.local:~/Lumiere/show.qxw
ssh <username>@lumiere-resident.local sudo systemctl restart lumiere-qlcplus
```

Open the Pi's QLC+ with Screen Sharing once to point the inputs and outputs at
the Pi's own: **MIDI input "Lumiere Cues"** (and "Lumiere Clock" for tempo),
**Art-Net output on 2.0.0.2**. Click away any pop-up QLC+ shows on opening the
show (e.g. "2D Point of view selection"), then save (File › Save): it opens
cleanly at every power-on after that.

## Checking on it

```bash
systemctl status lumiere-link lumiere-qlcplus lumiere-display lumiere-vnc
journalctl -u lumiere-link -f        # live log, Ctrl+C to leave
cat ~/.config/lumiere-link/screen-link.txt
```
