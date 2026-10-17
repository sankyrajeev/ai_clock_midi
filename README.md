# Pro DJ Link → Lightkey tempo sync + section cues

Turns the beats coming off a **CDJ‑3000X / DJM‑V10** Pro DJ Link network into a
**phase‑locked MIDI clock** (and optionally **Ableton Link**) that Lightkey can
follow. Your lights then track the BPM of whichever deck is the tempo master,
including pitch-fader changes and master hand-offs, and stay on the bar.

```
CDJ-3000X ─┐                     UDP 50001 beat packets
           ├─ switch ── Mac ──►  prolink_sync.py ──► virtual MIDI "ProLink Clock" ──► Lightkey
DJM-V10  ──┘                                     └─► Ableton Link (optional)     ──► Lightkey
```

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

## Quick start (double-click)

1. Download the repo: `git clone https://github.com/sankyrajeev/ai_clock_midi`,
   or use **Code → Download ZIP** on GitHub and unzip it.
2. In Finder, double-click **`Check Beats.command`** and press play on a deck.
   Beat lines should appear.
3. Double-click **`Start ProLink Clock.command`** and leave the window open.
   In Lightkey, set the tempo source to **MIDI Clock → ProLink Clock**.
   Or use **`Start Clock + Cues.command`** to also get drop/build/breakdown
   cues from the mixer audio (see below).

The first launch sets everything up by itself, which takes about a minute. If
macOS says the file "can't be opened because it is from an unidentified
developer" (this happens with the ZIP download), right-click it → **Open** →
**Open**. You only need to do that once. To always pass options such as
`--link` or `--source 1`, edit the `ARGS=(...)` line in the launcher.
After a `git pull` the launchers install any new requirements by themselves.

## Section cues: drops, builds, breakdowns

With `--cues` (or **`Start Clock + Cues.command`**) the tool also listens to the
**mixer's master audio** and tells Lightkey what the music is doing:

| Event | When it fires | MIDI (ch 1, port **ProLink Cues**) |
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

Keep the tempo source on **MIDI Clock → ProLink Clock**. Then use MIDI learn on
whatever should react, triggering it from **ProLink Cues**:

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
   python3 prolink_sync.py            # MIDI clock on virtual port "ProLink Clock"
   python3 prolink_sync.py --link     # ...plus Ableton Link
   ```
   The status line shows the followed device, beat in bar and BPM.

### Lightkey

- **MIDI Clock:** open Lightkey's tempo/BPM settings, set the tempo source to
  **MIDI Clock** and choose the input **ProLink Clock**.
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
| `--latency-ms N` | Fire the clock N ms early to make up for DMX/fixture latency |
| `--stop-after S` | Send MIDI Stop after S seconds with no beats (default: keep running at the last tempo) |
| `--monitor` | Print every beat packet from every device |
| `--simulate BPM` | Fake a deck locally to test the Lightkey side without the gear |

## Troubleshooting

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
