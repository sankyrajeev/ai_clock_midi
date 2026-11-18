# Factory check: flash and test each box

Vital Acoustica loads the software on every box; the manufacturing partner
delivers assembled boxes that pass the hardware test. Both use the same script,
`lumiere_factory.py`, on a laptop.

## The test station (once)

1. A small network switch, the laptop plugged into it (a USB-Ethernet adapter is
   fine), and a DMX tester or one test light on an XLR cable.
2. Give the laptop's Ethernet a fixed address on the Node's network:
   **Mac:** System Settings → Network → the Ethernet adapter → Details → TCP/IP →
   Configure IPv4: **Manually**, IP address **2.0.0.50**, Subnet mask **255.0.0.0**.
3. Python 3 and esptool: `python3 -m pip install esptool`
4. The Node firmware: from GitHub → Actions → "Build the Lumière Node firmware" →
   the latest run → download **lumiere-node-firmware** and unzip it into a folder
   named `node-firmware` next to the script. It holds `bootloader.bin`,
   `partitions.bin`, `boot_app0.bin` and `firmware.bin` (compiled; safe to give
   to the partner).
5. A USB-serial adapter (3.3 V) for the programming header:
   adapter TX → **RX0**, adapter RX → **TX0**, GND → **GND**. To flash, the ESP32
   must start in download mode: hold **IO0** to **GND** while the box powers up,
   then release (an auto-reset adapter wired to EN and IO0 does this itself).

## Each box: the partner (hardware test)

With a stock Raspberry Pi OS card in the Pi (any recent one; it only has to boot):

```bash
python3 lumiere_factory.py --port /dev/cu.usbserial-0001 --firmware node-firmware
```

It asks for the serial number, flashes the Node, finds it at 2.0.0.10, sends a
full-white look to DMX 1 (you confirm the tester or light), checks the DMX LED,
and asks you to press HOUSE twice. One row per box goes into `factory-log.csv`;
the box passes only if every check says PASS. Send us the CSV with the boxes.

## Each box: Vital Acoustica (software and final check)

1. **Pi:** write the master image (below) to the box's microSD card with Raspberry
   Pi Imager → Choose OS → *Use custom* → `lumiere-resident-master.img`.
2. Boot the box with both RJ45s on the test switch, then:
   ```bash
   python3 lumiere_factory.py --port /dev/cu.usbserial-0001 --firmware node-firmware --resident lumiere-resident.local
   ```
   The `--resident` check confirms Lumière Link and QLC+ are running and asks about
   the NET LED. (The laptop needs to reach the Pi too: Wi-Fi to the same network,
   or add the Pi's network to the laptop.)
3. Burn-in: leave the box running the test show for 4 hours, closed.
4. Note the box's own screen link for the customer:
   `ssh lumiere-resident@lumiere-resident.local cat ~/.config/lumiere-link/screen-link.txt`

Test one box at a time: every Node starts at 2.0.0.10 and every Pi at
lumiere-resident.local.

## Making the master image (once per software release)

1. Set up one Resident completely with `resident/install.sh` and check it works.
2. On it, run `bash resident/prepare-master.sh`. It removes what must differ per
   box (screen password, SSH keys, machine id, logs) and shuts the Pi down; each
   copy makes its own on first start.
3. Put its card in the Mac and copy it to a file (replace `N` with the card's disk
   number from `diskutil list`):
   ```bash
   diskutil unmountDisk /dev/diskN
   sudo dd if=/dev/rdiskN of=lumiere-resident-master.img bs=4m status=progress
   ```
4. Keep the image with the software version in its name, e.g.
   `lumiere-resident-1.3.0-master.img`.
