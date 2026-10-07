# Pro DJ Link → Lightkey tempo sync

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

## Setup (macOS)

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
