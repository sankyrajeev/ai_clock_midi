# Mapping Lumière Link to Lightkey

This guide connects everything Lumière Link sends to your Lightkey project:
the tempo, your sequences, the blackout, the hits and rolls before a drop,
and the build-up faders. You do it **once per Lightkey project**, at home,
with no CDJs needed.

---

## 1. Before you start

1. Open **Lightkey** with the project you want to map.
2. Start **Lumière Link** (in Terminal, in the Lumière Link folder):
   ```bash
   .venv/bin/python3 lumiere_app.py
   ```
3. Check the project name at the top of Lumière Link matches your Lightkey
   project. It switches by itself when you open a project in Lightkey.

Lumière Link shows up in Lightkey as two MIDI inputs:

| MIDI input | What it carries |
|---|---|
| **Lumiere Clock** | the tempo (BPM), following the DJ |
| **Lumiere Cues** | everything else: sequences, blackout, hits, rolls, faders |

---

## 2. The one trick behind every mapping

Every mapping below works the same way:

1. **In Lightkey:** open the **External Control** setting of the thing you
   want to control (a cue, a sequence, a fader) and start **MIDI Learn**.
2. **In Lumière Link:** open **Sequences**, switch on **Learn** (top right),
   and click the matching tile or button.
3. Lightkey learns it, and the button in Lumière Link gets a ✓ and turns gold
   ("MAPPED").

With **Learn** off, clicking a tile or button just plays it, which is handy for
checking a mapping.

Save the Lightkey project afterwards (**⌘ S**). Lumière Link saves its ✓ marks
by itself.

---

## 3. Tempo (do this first)

1. In Lightkey, open the **tempo / BPM settings**.
2. Set the tempo source to **MIDI Clock**.
3. Choose the input **Lumiere Clock**.

Lightkey's BPM now follows the deck that's playing, pitch fader included. All
BPM-based effects (chases, strobes, colour changes) follow the DJ.

---

## 4. Your sequences (the pools)

A **pool** is a set of looks for one kind of section. When a drop comes,
Lumière Link plays one of your drop sequences (shuffled, so no repeats in a
row), and the same for the other kinds of section.

**Step 1: say how many you made.** In **Sequences**, use **– / +** on each pool
on the left to set how many sequences you made in Lightkey for it. Leave a pool
at **0** to switch it off.

**Step 2: map each one.**
1. Click a pool on the left, e.g. **Drop**.
2. Switch on **Learn**.
3. In Lightkey: **External Control** of your first drop sequence → MIDI Learn.
4. In Lumière Link: click tile **1**.
5. Repeat for tile 2, 3 … then for the other pools.

| Pool | MIDI channel | Tile 1 | Tile *n* |
|---|---|---|---|
| Drop | 2 | C1 (note 36) | note 35 + *n* |
| Build | 3 | C1 | |
| Breakdown | 4 | C1 | |
| Pre-drop | 5 | C1 | |
| Intro | 6 | C1 | |
| Outro | 7 | C1 | |
| Verse | 8 | C1 | |
| Bridge | 9 | C1 | |

The pools use the same notes; only the channel differs. Make sure Lightkey
learns the **channel** too, not just the note (see Troubleshooting).

**Options under the tiles** (per pool):

| Option | What it does |
|---|---|
| **New look every** 4 / 8 / 16 / 32 bars | in a long section, moves to the next sequence from the same pool every N bars |
| **Shuffle / Random / In order** | how the next sequence is picked |
| **Pair builds with drops** | build 7 always leads into drop 7 |

---

## 5. Drop page extras

Click the **Drop** pool to see these. Each has its own button to map.

### Blackout before the drop
Make one Lightkey cue with **everything off**. Map it to the **Blackout cue**
button (channel 10, C3).

| Setting | When it fires |
|---|---|
| **Fixed length** | always ½ beat, 1 beat, 2 beats or 1 bar before the drop |
| **Follow the music** | exactly when the track goes silent before the drop (no silence, no blackout) |

### Hits & rolls before the drop
Read from the waveform: synth stabs, snare hits and rolls in the last bars
before a drop.

| Button | Channel 10 | Fires | Map it to |
|---|---|---|---|
| **Hit cue** | C#3 | on every hit, in time | a **flash or bump** (a short, bright cue) |
| **Roll cue** | D3 | once, when a snare roll starts | a **strobe** |

Switch the row to **Flash & strobe** to turn it on. The small line under the
title shows what the next drop has, e.g. "Next drop (bar 17): 3 hits".

---

## 6. Build page: faders that follow the build-up

Click the **Build** pool. Under **Follow the build** there are two faders that
Lumière Link moves for you through every build-up, so you don't have to push
them by hand.

| Button | MIDI | Follows | Map it to |
|---|---|---|---|
| **Speed fader** | CC 25 | the snare roll's speed: none 0 %, 1/4 notes 25 %, 1/8 50 %, 1/16 75 %, 1/32 100 % | strobe speed, or how fast movers go up and down |
| **Intensity fader** | CC 26 | how loud the riser gets, 0–100 % through the build | a dimmer, effect size or brightness |

To map one:
1. In Lightkey, open **External Control** for the speed (or size, dimmer)
   control you'd normally push, and start MIDI Learn.
2. In Lumière Link, with **Learn** on, click **Speed fader** (or **Intensity
   fader**). It sweeps the fader up and back down, and Lightkey learns it.

Both faders drop back to zero the moment the drop lands. The line under
**Follow the build** shows what the current or next build does, e.g.
"Next build (bar 9): roll 1/8 from bar 5, 1/16 from bar 7".

---

## 7. Unmapping and changing a mapping

- **Remove a ✓:** with **Learn** on, click the small **✕** on a mapped tile or
  button. That clears it in Lumière Link only. Also clear it in Lightkey's
  External Control.
- **Change a mapping:** just map it again in Lightkey; the message Lumière Link
  sends never changes.
- **New show:** duplicate an already-mapped Lightkey project instead of
  starting over. Lumière Link always sends the same messages, so the copy works
  straight away. Then set the pool counts for the new project.

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| Nothing happens when I click a tile | Check Lightkey's External Control uses the input **Lumiere Cues**, and that Lumière Link is running. |
| A drop happens but the look doesn't change | The sequence it picked isn't mapped, or the pool count is higher than the sequences you made. Set the count right and map the missing tiles. |
| A drop sequence plays on a breakdown | Lightkey matched the note but not the channel. Re-learn that sequence and check the channel shows in Lightkey's External Control. Tell me if it keeps happening. |
| Lightkey's BPM doesn't move | Tempo source must be **MIDI Clock** with input **Lumiere Clock**. |
| Hits or the build faders never fire | They need the track's waveform from the CDJ. Check the row is switched on, and look at the line under it: it should describe the next drop or build. |

---

## 9. Everything Lumière Link sends

| What | Channel | Message |
|---|---|---|
| Tempo | | MIDI clock on **Lumiere Clock** |
| Section cues | 1 | notes 36–46 (Breakdown 36, Build 37, Drop 38, every 8 bars 39, every bar 40, Intro 41, Outro 42, Verse 43, Bridge 44, Pre-drop 45, New track 46) |
| Section energy | 1 | CC 20 |
| Build progress (steady ramp) | 1 | CC 23 |
| Roll speed in the fill | 1 | CC 24 |
| Build speed (snare roll) | 1 | CC 25 |
| Build intensity (riser) | 1 | CC 26 |
| Sequence pools | 2–9 | sequence *n* = note 35 + *n* (C1 = sequence 1) |
| Blackout | 10 | C3 (note 60) |
| Fill hit | 10 | C#3 (note 61) |
| Roll starts | 10 | D3 (note 62) |
