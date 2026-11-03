# Lumière Node: firmware for the WT32-ETH01

The Node takes **Art-Net** (lighting over the network, from Lightkey, QLC+ or
the Lumière Resident) and sends **DMX** out of the XLR socket. One universe:
512 channels.

| It does | How |
|---|---|
| Art-Net in, DMX out | about 40 DMX frames a second |
| Never goes dark by accident | Art-Net stops → holds the last look for 10 s → fades to the **house look** over 3 s. Power-on starts in the house look. |
| **HOUSE** button | press: fade to the house look / back to live. **Hold 3 s:** what's on the lights now becomes the house look (the LED flashes 3 times). |
| **DMX** LED | solid = live · 1 blink a second = holding or fading · short flash every 2 s = house look · fast blinking = no network cable |
| Web page | `http://lumiere-node.local` (or its address): status, House button, settings |
| Found by Lightkey / QLC+ | answers Art-Net discovery (ArtPoll) as "Lumiere Node" |
| Network address | from DHCP if there is a server; otherwise **2.0.0.10** (netmask 255.0.0.0), the usual Art-Net address. Changeable on the web page. |
| Updates | after the first time, over the network |
| Self-healing | a watchdog restarts it within 8 s if the program ever hangs |

Pins are as in [hardware/WIRING.md](../../hardware/WIRING.md): DMX data IO17,
transmit enable IO33, DMX LED IO4, HOUSE button IO14.

---

## 1. Loading the firmware the first time

The WT32-ETH01 has no USB socket, so the first time needs a **USB-to-serial
adapter** (FTDI, CP2102 or CH340, set to **3.3 V**) and 5 jumper wires. Do this
on the desk, with the board **not** wired into the Resident.

**Wiring for loading:**

| Adapter | WT32-ETH01 |
|---|---|
| GND | GND |
| 5V (or VCC) | 5V |
| TX | **RX0** |
| RX | **TX0** |
| — | **IO0 → GND** (a jumper between IO0 and a GND pin: "load mode") |

TX goes to RX and RX to TX: they cross.

**No adapter? Use any old ESP32 board with a USB socket** as the adapter. Its
USB chip passes everything through while its own ESP32 is held off:

| Old ESP32 board | WT32-ETH01 |
|---|---|
| **EN → a GND pin on the old board itself** | (holds the old chip off) |
| GND | GND |
| VIN / 5V (or power the WT32 from elsewhere) | 5V |
| **TX** | **TX0** (not crossed this time) |
| **RX** | **RX0** |
| | **IO0 → GND** for loading |

The old board's EN must go to **its own GND**, never to the WT32's EN: otherwise
the old chip stays awake, the firmware loads into it instead, and it reports
Ethernet errors because it has no Ethernet.

**Then, in Terminal on the Mac:**

```bash
cd ~/ai_clock_midi && git fetch && git checkout beta-node && git pull
.venv/bin/pip install platformio
cd firmware/lumiere-node
../../.venv/bin/pio run -e usb -t upload
```

1. Plug in the adapter (with the IO0 jumper in place) **before** running the last
   line. The first run downloads the ESP32 tools (a few minutes).
2. If it says it can't connect, unplug the adapter's USB, plug it back in, and run
   the last line again: the board only enters load mode at power-up with IO0 on GND.
3. If it finds more than one port, add the adapter's port:
   `../../.venv/bin/pio run -e usb -t upload --upload-port /dev/cu.usbserial-XXXX`
   (`ls /dev/cu.*` lists them).
4. When it says **SUCCESS**: unplug, **remove the IO0 jumper**, plug back in.

To see what it's doing (address, Art-Net, button): `../../.venv/bin/pio device monitor`
(Ctrl+C to leave).

**Getting into load mode:** IO0 must already be on GND at the moment the board
restarts. Put the IO0 jumper on first, then tap **EN** to GND (or power it up).
"Wrong boot mode detected (0x13)" means it restarted without IO0 on GND; tap EN
again while the upload shows `Connecting....`.

**IO0 must be completely bare when running.** On the WT32-ETH01, IO0 carries the
Ethernet chip's 50 MHz clock: even a loose wire left on it stops Ethernet
("emac reset timeout"). For the same reason the debug text on TX0 (right next to
IO0) can turn to garbage once Ethernet starts; that's the jumper wire picking up
the clock, not a fault. Use the web page instead.

## 2. Later updates, over the network

With the Node on the same network as the Mac:

```bash
cd ~/ai_clock_midi/firmware/lumiere-node && git pull && ../../.venv/bin/pio run -e network -t upload
```

The update password is `lumiere-node` (change it on the web page; then change
`--auth=` in `platformio.ini` to match).

---

## 3. Testing it with Lightkey (no Pi needed)

1. Wire the Node's MAX485 and XLR as in WIRING.md, and plug a DMX light into the XLR.
2. Power the Node (5 V) and connect its Ethernet to the Mac (directly or through
   the switch). With no DHCP server it takes **2.0.0.10** after about 6 seconds.
3. Give the Mac an address on that network: **System Settings → Network →
   Ethernet → Details → TCP/IP → Configure IPv4: Manually**, IP address
   `2.0.0.1`, subnet mask `255.0.0.0`.
4. Open **http://2.0.0.10** in a browser: the Node's page shows "house look"
   and "nothing arriving".
5. In Lightkey, add an **Art-Net** output to **Lumiere Node** (it should appear
   by itself; otherwise enter 2.0.0.10), universe 0 / "Universe 1". Patch your
   light on that universe.
6. Move a fader in Lightkey: the light follows, the DMX LED stays on, and the page
   says "live".

**Then test the safety net:** quit Lightkey (or pull the Mac's cable). The light
holds its look for 10 seconds, then fades to the house look. Set a house look
with Lightkey running: hold the **HOUSE** button for 3 seconds.

---

## For developers

- `src/artnet.h`: ArtDmx parsing and the ArtPollReply.
- `src/show.h`: live / hold / fade / house, the LED patterns and the button.
- `src/main.cpp`: Ethernet, UDP, DMX output (its own task; the break is a 0 byte
  at 83.3 kbaud), the web page, network updates, the watchdog.
- `test/test_logic.cpp`: tests for the two headers; `pytest test_node.py` at the
  repository root builds and runs them with the computer's compiler.
- GitHub Actions (`.github/workflows/node-firmware.yml`) builds the ESP32 image on
  every change and keeps the files as a download (`lumiere-node-firmware`).
