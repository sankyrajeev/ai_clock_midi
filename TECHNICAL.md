# Lumière Link: technical notes

New here? Start with the step-by-step guide in [README.md](README.md). This page is the
detailed reference: how it works inside, every option, and the MIDI it sends.

Turns the beats coming off a **CDJ‑3000X / DJM‑V10** Pro DJ Link network into a
**phase‑locked MIDI clock** (and optionally **Ableton Link**) that Lightkey can
follow. Your lights then track the BPM of whichever deck is the tempo master,
including pitch-fader changes and master hand-offs, and stay on the bar.

```
CDJ-3000X ─┐                     UDP 50001 beat packets
           ├─ switch ── Mac ──►  prolink_sync.py ──► virtual MIDI "Lumiere Clock" ──► Lightkey
DJM-V10  ──┘                                     ├─► Ableton Link (optional)     ──► Lightkey
                                                 └─► MIDI "Lumiere Cues": intro / build / drop /
                                                     breakdown / outro from rekordbox phrases
```

It also fires **lighting cues for each part of the track**, using rekordbox's
phrase analysis read straight off the CDJs over the same network cable (see
[Phrase cues](#phrase-cues-over-the-network-recommended)). The older
[USB-audio detector](#usb-audio-backup-section-cues-from-the-mixer-audio) is
kept as a backup and as a fallback for tracks without phrase data. A snapshot
of the project from before the network version is on the `backup-usb-audio`
branch.

## How it works

- It listens **passively** for Pro DJ Link beat packets (UDP 50001). It doesn't
  take a player slot, so PRO DJ LINK Bridge / rekordbox can keep running.
- **Source selection (`--source auto`)**: the DJM sends its own beat packets,
  which follow the current tempo master, so by default the tool follows the
  mixer. If no mixer beats are heard, it locks to one playing deck and switches
  only after that deck goes quiet. You can force `--source mixer` or
  `--source 1`…`6`.
- Effective BPM = track BPM × pitch, so the pitch fader is taken into account.
- The clock is a **PLL**. Each incoming beat re-aims the next quarter-note
  boundary, so drift and tempo changes get absorbed smoothly with no tick
  bursts. Big jumps (track change, jog nudge) snap straight to the new phase.
- **MIDI Start** goes out on a downbeat (beat 1 of the bar), so Lightkey's bar
  position matches the decks.

## The app

`lumiere_app.py` is the Mac app. It starts `engine.LumiereEngine` (network,
phrase tracker, MIDI clock, cue pools, projects, Lightkey watcher, optional
live audio, all in background threads) and `appserver.AppServer`, a small HTTP
server bound to 127.0.0.1 on a free port. A pywebview window (1400×830, not
resizable) loads `ui/index.html` from it.

- `GET /api/state`: snapshot polled every 125 ms; `GET /api/waveform?key=p-s-id`;
  `POST /api/action {"name", "args"}` → `LumiereEngine.do_<name>`.
- Every API call needs the `X-Token` header (a random token per launch, passed
  to the window in its URL); anything else gets 403.
- Settings: `~/Library/Application Support/Lumiere Link/settings.json`; log:
  `lumiere.log` in the same folder. Projects: `~/Documents/Lumiere Link/*.lumiere`.
- `python3 lumiere_app.py --browser --demo` runs it in a browser for development.

Build: `packaging/build_mac.sh` (PyInstaller, `packaging/LumiereLink.spec`,
ad-hoc codesign, `.dmg`). GitHub Actions (`.github/workflows/build-mac.yml`)
runs the tests, builds arm64 and x86_64 DMGs and publishes them to the
`app-latest` release.

### Signing and notarization (removes the macOS warning)

Without an Apple Developer ID the build is ad-hoc signed, so macOS warns on
first open. With an Apple Developer Program membership, add these repository
secrets (GitHub › Settings › Secrets and variables › Actions) and every build
is signed, notarized by Apple and stapled automatically:

| Secret | What it is |
|---|---|
| `MACOS_CERT_P12` | The **Developer ID Application** certificate exported from Keychain Access as a .p12, base64-encoded (`base64 -i cert.p12 \| pbcopy`) |
| `MACOS_CERT_PASSWORD` | The password chosen when exporting the .p12 |
| `APPLE_ID` | The Apple ID email of the developer account |
| `APPLE_TEAM_ID` | The 10-character Team ID (developer.apple.com › Membership) |
| `APPLE_APP_PASSWORD` | An app-specific password from account.apple.com › Sign-In and Security |

Entitlements for the hardened runtime are in `packaging/entitlements.plist`.

## Quick start (double-click)

1. Download the repo: `git clone https://github.com/sankyrajeev/ai_clock_midi`,
   or use **Code → Download ZIP** on GitHub and unzip it.
2. In Finder, double-click **`Check Beats.command`** and press play on a deck.
   Beat lines should appear. No decks to hand? **`Simulate Booth (no gear).command`**
   runs a pretend booth on the Mac instead.
3. Double-click **`Start Lumiere Link.command`** and leave the
   window open. In Lightkey, set the tempo source to **MIDI Clock → Lumiere
   Clock** and MIDI-learn the cues from **Lumiere Cues**.

| Launcher | What it does |
|---|---|
| `Start Lumiere Link.command` | **Recommended.** Clock + phrase cues over the DJ Link network |
| `Simulate Booth (no gear).command` | A pretend booth (2 × CDJ-3000X + DJM-V10) on this Mac, for testing and MIDI-learning without decks |
| `Start Lumiere Clock.command` | Clock only |
| `Start Clock + Cues (USB backup).command` | Clock + cues detected from the DJM's audio over USB |
| `Check Beats.command` | Shows the beat packets arriving from the decks |

The first launch sets everything up by itself, which takes about a minute. If
macOS says the file "can't be opened because it is from an unidentified
developer" (this happens with the ZIP download), right-click it → **Open** →
**Open**. You only need to do that once. To always pass options such as
`--link` or `--source 1`, edit the `ARGS=(...)` line in the launcher.
After a `git pull` the launchers install any new requirements by themselves.

## Projects

Pool settings belong to a **Lumière project**: one JSON file per show,
`~/Documents/Lumiere Link/<name>.lumiere`, holding the pool counts, mode,
pairing, the sequences already MIDI-learned, and the paired Lightkey project
(name and file path). Saves are atomic and happen on every change.

By default the app follows Lightkey (`lightkey.py`): every 3 s it asks System
Events for Lightkey's document window title and its `AXDocument` (file path),
then picks the Lumière project paired by path, else by name (case, extension
and " — Edited" ignored), creating one from the current settings if none
matches. Reading is the only thing it does with Lightkey. macOS asks once for
Automation permission; if it's refused, the app says so once and stays on the
current project. On first run an old `pools.json` next to the app becomes the
project "Default".

| Option | Effect |
|---|---|
| `--project NAME` | use (or create) that project, don't follow Lightkey |
| `--projects` | list projects |
| `--projects-dir DIR` | keep projects somewhere else |
| `--no-follow-lightkey` | stay on "Default" |
| `--lightkey-check` | print what Lightkey reports, then exit (`Check Lightkey Project.command`) |
| `--pools FILE` | use a stand-alone settings file instead of a project |

## Sequence pools

Each section type has a pool of up to 24 lighting sequences, and every time
that section starts one of them is picked and sent on the pool's own MIDI
channel, with the sequence number as the note (sequence 1 = note 36 / C1 …
sequence 24 = note 59 / B2):

| Pool | Channel | | Pool | Channel |
|---|---|---|---|---|
| Drop | 2 | | Intro | 6 |
| Build | 3 | | Outro | 7 |
| Breakdown | 4 | | Verse | 8 |
| Pre-drop | 5 | | Bridge | 9 |

The plain section note on channel 1 (table below) is still sent as well.
Settings are stored in the active project (above); the format is:

```json
{
  "mode": "shuffle",
  "pair_builds_with_drops": false,
  "pools": { "drop": 24, "build": 24, "breakdown": 12, "intro": {"count": 4, "mode": "order"}, ... }
}
```

- `mode`: `shuffle` (default: every sequence once before repeats, never the
  same twice in a row), `random`, or `order`. Can be set per pool.
- A count of `0` switches that pool off. Counts above 24 are capped at 24.
- `pair_builds_with_drops`: a drop uses the number of the build that led into
  it, when the drop pool is that big.
- `--learn` (or `Learn Sequences (MIDI Learn helper).command`) steps through
  every sequence note for MIDI-learning; `--learn drop` does one pool.
- `--no-pools` turns pools off.

## Phrase cues over the network (recommended)

With `--phrases` (or **`Start Lumiere Link.command`**) the tool
joins the Pro DJ Link network as a virtual player, the same way rekordbox or
PRO DJ LINK Bridge do. For each track that gets loaded it downloads rekordbox's
**phrase analysis** from the CDJ, then follows the playhead of the master deck
and fires a cue **on the exact beat** each phrase starts. There's no audio
cable and no detection delay. `--latency-ms 30` fires cues (and the clock)
30 ms early to make up for DMX and fixture lag.

| Cue | rekordbox phrase | MIDI (ch 1, port **Lumiere Cues**) |
|---|---|---|
| **Breakdown** | Down | note 36 |
| **Build** | Up | note 37 |
| **Drop** | Chorus | note 38 |
| **Phrase** | every 8 bars into a section | note 39 |
| **Bar** | every downbeat | note 40 |
| **Intro** / **Outro** | Intro / Outro | notes 41 / 42 |
| **Verse** / **Bridge** | (mid/low-energy tracks) | notes 43 / 44 |
| **Pre-drop** | one bar before a Chorus (good for a blackout) | note 45 |
| **New track** | the followed deck changes track | note 46 |
| Section energy | Down 25%, Intro 35%, Up 60% … Chorus 100% | CC 20 |
| Build progress | ramps 0→127 through each Up section | CC 23 |

Notes 36–40 and CCs 20/23 mean the same thing as in the USB-audio mode, so one
Lightkey mapping works for both.

### Where the sections come from

The app picks the best source for each track by itself:

| | Source | Used when | Timing |
|---|---|---|---|
| 1 | **rekordbox phrases** | the DJ ran phrase analysis | exact, on the beat |
| 2 | **Waveform analysis** | no phrase data, but the track came from a rekordbox export (or a CDJ-3000 analysed the file itself) | exact, on the beat, worked out when the track loads |
| 3 | **Live audio** (`--cues`) | anything else, e.g. streaming | live, a moment after it happens |

**Waveform analysis** covers guest DJs who never turned on phrase analysis.
When a track loads, the app downloads its beat grid and its detailed waveform
from the CDJ: the CDJ-3000's 3-band waveform where it exists, otherwise the
colour or mono one. It then goes through the track bar by bar. Kick and bass
gone means a breakdown, highs climbing towards a drop means a build, and full
energy means a drop. Section starts are snapped to phrase lines. The result
feeds the same cue engine, so cues still land on the exact beat. The status
line shows `(rekordbox)` or `(waveform)` next to the current section.

**Requirements and behaviour:**

- Best results come from tracks analysed in rekordbox with phrase analysis on.
  That's the coloured phrase bar above the waveform, and it's on by default in
  rekordbox 6/7. Without it, waveform analysis takes over automatically.
- It follows the **tempo master** deck (`--follow on-air` or `--follow 2` to
  change). On a master hand-off it announces where the new deck is. Hot-cue
  jumps announce the new section immediately.
- It takes a free device number from 7 up, so all six player slots stay free.
  Pick one with `--player-number`.
- To run the USB-audio detector as a fallback for tracks with no phrase data,
  add `--cues` (edit `ARGS` in the launcher to `ARGS=(--phrases --cues)`).
- PRO DJ LINK Bridge isn't needed. If it runs on the same Mac, the two may
  fight over the status port. Quit Bridge if decks don't show up.

## USB-audio backup: section cues from the mixer audio

With `--cues` (or **`Start Clock + Cues (USB backup).command`**) the tool
listens to the **mixer's master audio** and tells Lightkey what the music is
doing:

| Event | When it fires | MIDI (ch 1, port **Lumiere Cues**) |
|---|---|---|
| **Breakdown** | kick/bass gone for ~2 s | note 36 (C1) |
| **Build** | during a breakdown, the highs climb steadily (risers, snare rolls) | note 37 |
| **Drop** | the kick and bass slam back in, ~70 ms after the hit | note 38 |
| **Phrase** | every 8 bars after a drop (on the downbeat, from the CDJ beat grid) | note 39 |
| **Bar** | every downbeat | note 40 |
| Energy / Bass / Highs / Build progress | continuously, 0–127 | CC 20 / 21 / 22 / 23 |

It watches for the kick's **punch**, not just bass level. A breakdown that
keeps a sub-bass or bassline still counts, and a kick roll at the end of a
build doesn't fire the drop early. Every level is relative to the track's own
loud parts, so fader moves and quieter tracks don't set it off.

### Getting the audio in

The DJM‑V10 is also a USB audio interface. Connect its USB port to the Mac.
The tool picks any input with "DJM" in its name automatically. In the DJM's
settings utility, set the **USB send / mixer audio output** for the first pair
of channels to **Master** (or REC OUT). If the master arrives on other
channels, pass e.g. `--audio-channels 13,14`. `--list-audio` shows every input
and its channel count. The first time, macOS asks whether Terminal may use the
microphone (audio input). Click **Allow**.

### In Lightkey

Keep the tempo source on **MIDI Clock → Lumiere Clock**. Then use MIDI learn on
whatever should react, triggering it from **Lumiere Cues**:

- Drop → your big full-rig cue (strobes, all colours, fast chases)
- Breakdown → slow, dim, single-colour wash
- Build → a cue that ramps; map **CC 23 (build progress)** to intensity or
  strobe speed so it climbs with the riser
- Phrase → step to the next look every 8 bars so the drop section doesn't go stale
- CC 20 (energy) → master dimmer or effect size

### Tuning on a recording

Record a mix as WAV and see what it would have fired:

```bash
python3 prolink_sync.py --analyze my_set.wav
  0:33.13  BREAKDOWN
  0:45.07  BUILD
  1:00.07  DROP
```

The thresholds live in `Tuning` at the top of `sections.py`, e.g.
`break_hold` (seconds without kick before a breakdown) and `build_slope`.

## Setup (manual, macOS)

```bash
cd ai_clock_midi
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# optional Ableton Link output:
pip install LinkPython-extern
```

1. Connect the CDJ‑3000X, the DJM‑V10 and the Mac to the same network switch,
   the same way you already do for PRO DJ LINK Bridge.
2. Check that beats arrive (press play on a deck):
   ```bash
   python3 prolink_sync.py --monitor
   ```
   You should see lines from the players and from the mixer (device 33).
3. Run the bridge:
   ```bash
   python3 prolink_sync.py            # MIDI clock on virtual port "Lumiere Clock"
   python3 prolink_sync.py --link     # ...plus Ableton Link
   ```
   The status line shows the followed device, beat in bar and BPM.

### Lightkey

- **MIDI Clock:** open Lightkey's tempo/BPM settings, set the tempo source to
  **MIDI Clock** and choose the input **Lumiere Clock**.
- **Ableton Link (alternative):** run with `--link` and enable **Ableton Link**
  as the tempo source in Lightkey. Pick only one of the two sources.

Then use beat-synced effects / BPM-based cue timing in Lightkey as usual.

## Options

| Flag | Purpose |
|---|---|
| `--source auto\|mixer\|N` | Which device to follow (default `auto`) |
| `--link` | Also publish tempo + bar phase on Ableton Link |
| `--no-midi` | Link only |
| `--midi-port NAME` / `--existing-port` | Port name, or send to an existing port such as `IAC Driver Bus 1` |
| `--no-transport` | Clock ticks only, no Start/Stop |
| `--latency-ms N` | Fire the clock and phrase cues N ms early to make up for DMX/fixture latency |
| `--stop-after S` | Send MIDI Stop after S seconds with no beats (default: keep running at the last tempo) |
| `--monitor` | Print every beat packet from every device |
| `--simulate BPM` | Fake a deck locally to test the Lightkey side without the gear |
| `--phrases` | Phrase cues from rekordbox analysis over the network |
| `--follow master\|on-air\|N` | Which deck drives the phrase cues (default `master`) |
| `--player-number N` / `--pose-as N` | Device number to claim / player number shown to the CDJ database |
| `--no-fix-network` | Don't add a matching Mac address when a player is stuck on another subnet |
| `--simulate-booth` | Run a pretend booth on this Mac and connect to it over the real protocol |
| `--simulate-dropout S` | With `--simulate-booth`: pull the pretend network cable for 10 s after S seconds |
| `--demo` | Fake track straight into the cue engine (no network), for MIDI-learning |
| `--cues` | Cues from the mixer audio over USB (fallback when used with `--phrases`) |

## Connecting

Plug the Mac and the players into the same switch, in any order, and start the
app. It finds the players, joins the network, and keeps watching:

- **Unplugged or rebooted gear**: players that go quiet are dropped. When they
  come back the app rejoins by itself, keeping the same device number.
- **A CDJ stuck on an old IP address** (for example one left over from a venue
  router): the players' beats still arrive, but nothing else can reach the
  Mac. The app gives the Mac's Ethernet port a second address on the player's
  network, so you don't have to restart the CDJ. macOS asks for your password
  the first time. Turn this off with `--no-fix-network`.
- **This Mac's address changes**: it rejoins on the new address.

The only errors you should see point at something physical:

| Message | What to check |
|---|---|
| No Ethernet link on … | the cable from the Mac to the switch, and that the switch has power |
| No CDJs or mixer are answering | the cables from the players to the switch, and that the players are on |
| No Ethernet port found | plug in your USB or Thunderbolt Ethernet adapter |
| macOS is blocking this app from the local network | System Settings > Privacy & Security > Local Network > switch on Terminal (or Lumière Link) |
| … is running on this Mac and is probably taking the status packets | quit that app (e.g. PRO DJ LINK Bridge) |

## Troubleshooting

- **"no phrase or waveform data for this track"**: the player had nothing to
  send for that track, which is typical for streaming. Use the live-audio
  fallback (`--cues`) for those.
- **Phrase cues: decks never show up**: quit PRO DJ LINK Bridge / rekordbox on
  the Mac and restart the tool. Run with `-v` to see what's happening.

- **"Cannot listen on UDP 50001"**: another app has the port without sharing
  it. Quit that app; this tool doesn't need Bridge to be running.
- **No packets in `--monitor`**: make sure the Mac is on the same subnet as the
  players (link-local 169.254.x.x is fine if everything is on one switch), and
  allow incoming connections for Python in the macOS firewall.
- **Lights a little behind the beat**: raise `--latency-ms` (try 20–40).
- **Mixer not showing up**: use `--source N` for the master deck number.

## Tests

```bash
pip install pytest && python3 -m pytest -q
```
