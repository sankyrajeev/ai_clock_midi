# Open-source software in Lumière Resident and Lumière Node

Lumière Resident and Lumière Node by Vital Acoustica contain open-source software
made by other people. This file lists it, with its licenses and where its source
code is. The full license texts are in the `texts` folder next to this file.

Lumière Link and Lumière Studio (the Vital Acoustica software) are separate
programs. They talk to QLC+ over MIDI and its web interface and write its show
files; they don't include or change any of the software below.

The software below comes with **no warranty**, as its licenses state.

---

## Lumière Resident (Raspberry Pi 4)

### QLC+ 5.3.0 (lighting control)
- Q Light Controller Plus, by Massimo Callegari and contributors:
  https://www.qlcplus.org, https://github.com/mcallegari/qlcplus
- License: **Apache License 2.0** (`texts/QLCplus-COPYING.txt`,
  `texts/Apache-2.0.txt`).
- Built unchanged from the QLC+ source, tag `QLC+_5.3.0`, with
  `build-qlcplus5.sh` (in `source/` on the Resident).
- QLC+ isn't made, endorsed or supported by its authors for this product.
  "QLC+" is used only to name the software.
- This QLC+ program is linked with **FFTW 3**, licensed under the **GNU GPL
  version 2 or later**. The QLC+ program as installed on this product is
  therefore provided under the terms of the **GNU GPL version 3** (`texts/GPL-3.txt`;
  Apache 2.0 is compatible with GPL version 3). Its complete source code is on the
  Resident: see *Source code* below.

### Libraries QLC+ uses
| Software | License | Text |
|---|---|---|
| Qt 6 (Core, Gui, Widgets, Qml, Quick, Svg, Network, WebSockets, Multimedia, PrintSupport, SerialPort, 3D), The Qt Company and contributors | GNU LGPL v3 | `texts/LGPL-3.txt` |
| FFTW 3, Matteo Frigo and the MIT | GNU GPL v2 or later | `texts/GPL-2.txt`, `texts/GPL-3.txt` |
| ALSA library (alsa-lib) | GNU LGPL v2.1 or later | `texts/LGPL-2.1.txt` |
| libusb 1.0 | GNU LGPL v2.1 or later | `texts/LGPL-2.1.txt` |
| libftdi1 | GNU LGPL v2.1 | `texts/LGPL-2.1.txt` |
| libsndfile | GNU LGPL v2.1 or later | `texts/LGPL-2.1.txt` |
| libudev (systemd) | GNU LGPL v2.1 or later | `texts/LGPL-2.1.txt` |

These libraries are the unchanged Raspberry Pi OS (Debian) packages, installed as
separate shared libraries. You may replace them with your own versions: the
Resident gives full access over SSH (user `lumiere-resident`), and QLC+ loads them
from `/usr/lib` when it starts.

### Libraries Lumière Link uses
| Software | License | Text |
|---|---|---|
| Python 3 | Python Software Foundation License | https://docs.python.org/3/license.html |
| mido | MIT | `texts/MIT.txt` |
| python-rtmidi (includes RtMidi) | MIT | `texts/MIT.txt` |
| NumPy | BSD 3-Clause | `texts/BSD-3-Clause.txt` |
| gpiozero | BSD 3-Clause | `texts/BSD-3-Clause.txt` |
| lgpio | Unlicense (public domain) | https://unlicense.org |

### Operating system
The Resident runs **Raspberry Pi OS** (based on Debian), with Xvfb, x11vnc,
matchbox-window-manager, Avahi and other standard packages. These are unchanged
packages under their own licenses (mostly GNU GPL and LGPL); each package's
license is in `/usr/share/doc/<package>/copyright` on the Resident. Their source
code is available from Raspberry Pi (https://archive.raspberrypi.com) and Debian
(https://sources.debian.org), and from Vital Acoustica on request (see below).

---

## Lumière Node (WT32-ETH01)

The Node's firmware is Vital Acoustica's program built with:

| Software | License | Text |
|---|---|---|
| Arduino core for the ESP32 (arduino-esp32 2.0.x), Espressif Systems | GNU LGPL v2.1 | `texts/LGPL-2.1.txt` |
| ESP-IDF 4.4 (with FreeRTOS, lwIP, mbedTLS), Espressif Systems and contributors | Apache 2.0 (FreeRTOS: MIT; lwIP: BSD) | `texts/Apache-2.0.txt`, `texts/MIT.txt`, `texts/BSD-3-Clause.txt` |

The Arduino core is built into the firmware. As the GNU LGPL v2.1 allows, Vital
Acoustica will provide on request the files needed to rebuild the Node's firmware
with a changed version of the Arduino core (the object files of its own program),
and the source code of the Arduino core version used.

---

## Source code

- **On the Resident:** `~/Lumiere/Licenses/source/` holds the complete source
  code of the QLC+ version installed (`qlcplus-5.3.0-source.tar.gz`) and the
  script that built it (`build-qlcplus5.sh`).
- **Written offer:** for at least three years after we last ship this product,
  Vital Acoustica will give anyone who asks a complete copy of the source code of
  the GPL and LGPL software in it, for no more than the cost of providing it.
  Ask at: **[CONTACT EMAIL]**.
