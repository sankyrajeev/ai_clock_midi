# Lumière Studio (beta)

Set up the venue's lights in a few clicks. Studio writes the QLC+ show for you;
QLC+ still sends the DMX (Art-Net to the Lumière Node).

Open it with the **Studio** button at the top of Lumière Link.
On the Resident: `http://lumiere-resident.local:8080/?t=<your token>` (see `screen-link.txt`).

## 1 · Lights: the kinds of light in the venue

Add each **kind** of light once (not each light).

1. **+ Add a kind of light** → pick what it's closest to:

   | Template | Channels |
   |---|---|
   | RGB par, 3 ch | red, green, blue |
   | RGBW par, 4 ch | red, green, blue, white |
   | RGB par, 7 ch | dimmer, red, green, blue, strobe, mode, speed |
   | RGBW par, 8 ch | dimmer, red, green, blue, white, strobe, mode, speed |
   | LED bar with segments | dimmer, strobe, then red/green/blue (and white) per segment |
   | Strobe, 2 ch | dimmer, strobe |
   | Moving head, 9 ch | pan, tilt, speed, dimmer, strobe, red, green, blue, white |

   Match the channel count the light is set to (its menu or DIP switches, e.g. **7CH** or **d-7**).
2. Check each channel and change any that are different. **Save**.

**No manual? Use the Light finder.**
1. Set the light to an address (e.g. 200) and type the same address in the finder.
2. Set how many channels it has (a guess is fine; add more if needed).
3. Press **Try** on channel 1: it goes to full. Pick what the light did (Red, Dimmer, Strobe…).
   Channels you've called **Dimmer** stay up while you try the others, so colours show.
4. Name it and press **Save as a kind of light**.

## 2 · Patch: every light, its address and group

- Choose the kind, how many, a name, and a **group** (e.g. *Front pars*, *Bars*).
- Leave **First address** empty to use the next free one. Several lights of the same
  kind get addresses one after the other. Set each real light to the address shown.
- **Find it** puts that one light on full white, so you can see which it is.
- Clashes are refused with the reason ("Channels 5-7 are already used by Par 1").

## 3 · Test & send to QLC+

- Pick **All lights** or a group, then a look (White, Red, Blue, Strobe…). This goes
  straight to the lights through QLC+. **Give the lights back to the show** when done.
- **Send to QLC+** writes a light file for each kind and patches every light, with a
  QLC+ fixture group for each group.
  - The show's output setup (Art-Net to the Node, 2.0.0.10) is kept.
  - The old show is saved next to it as `show-before-studio-<date>.qxw`.
  - If the show has scenes or chases made in QLC+, Studio asks before replacing it.
  - On the Resident, QLC+ restarts with the new patch by itself (a few seconds).
    On a laptop, close QLC+ and open the show again.

## Where things go

| What | Where |
|---|---|
| Studio's rig | `~/.config/lumiere-link/studio-rig.json` (Mac: `~/Library/Application Support/Lumiere Link/`) |
| QLC+ show | `~/Lumiere/show.qxw` |
| Light files | `~/.qlcplus/fixtures/Lumiere-*.qxf` (Mac: `~/Library/Application Support/QLC+/Fixtures/`), plus a copy next to the show |

QLC+ must run with its web page on (`--web`); the Resident does this already.

## 4 · Show: the lights play by themselves

For each kind of section (intro, verse, build, pre-drop, drop, breakdown, bridge,
outro) choose what plays. **Fill from my lights** makes a whole show from the
colours your lights can make; change it with **+ Add…** and **✕**.

| Effect | What it does | Speed |
|---|---|---|
| Solid | one colour on everything | – |
| Pulse | fades between full and low | 2 beats each way |
| Chase | one light (or bar segment) at a time, along each group | an 8th note a step |
| Bounce | a chase that goes back and forth | an 8th note a step |
| Alternate | every other light/segment in two colours, swapping | every beat |
| Fill | lights fill up along each group | an 8th note a step |
| Strobe | on/off | 10 flashes a second |

Plus, before the drop: **Hit flash** (white on every hit), **Snare roll strobe**
(from the start of the roll to the drop) and **Blackout** (the gap before the drop;
its length is set in Sequences → Drop).

Press **Send to QLC+**. Studio writes every effect into the QLC+ show with a button
for each, already wired to the note Lumière Link sends for it, sets QLC+'s MIDI
inputs, and sets Lumière Link's pools to match. Nothing to map by hand. After
sending, **▶** previews an effect on the lights.

How it plays:
- When a section starts, Lumière Link picks one of its effects (shuffled, never the
  same one twice in a row) and QLC+ starts it, stopping the one before.
- The beat: Lumière Link sends QLC+ its own clock, **Lumiere Studio Clock**, at
  double speed, so QLC+'s chases can step on 8th notes, locked to the DJ.
- A section with nothing chosen keeps the last look.

On the Resident, QLC+ starts once Lumière Link's MIDI ports are there and
restarts whenever Lumière Link does (QLC+ only finds MIDI ports when it starts).

Studio's show runs on the Resident (Linux). On a Mac, QLC+ gets no double-speed
clock, so its chases won't move.
