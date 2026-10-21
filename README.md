# Lumière Link

**by Vital Acoustica · Pro Audio • Lighting • Rentals**

Lumière Link makes your lights follow the DJ automatically. It listens to the
Pioneer CDJs over a network cable, knows the tempo and exactly where each track
is, and tells Lightkey when a **build**, **drop** or **breakdown** starts. Each
time, it picks one of your own lighting sequences.

This guide assumes you've never done this before. Follow it top to bottom the
first time. After that, the **Gig-day checklist** (part 8) is all you need.

> **Where things stand:** the engine is finished and has been tested on a real
> CDJ‑3000X. For now you start it by double-clicking a file and it runs in a
> Terminal window that shows text. The app with the full screen (deck rack,
> waveform, cue pads) is coming next and will work the same way.

---

## Contents

1. [What you need](#1-what-you-need)
2. [How it fits together](#2-how-it-fits-together)
3. [Install it (once)](#3-install-it-once)
4. [Try it without any gear](#4-try-it-without-any-gear)
5. [Set up Lightkey: tempo](#5-set-up-lightkey-tempo)
6. [Set up your sequence pools](#6-set-up-your-sequence-pools)
7. [Connect the real gear](#7-connect-the-real-gear)
8. [Gig-day checklist](#8-gig-day-checklist)
9. [Reading the Terminal window](#9-reading-the-terminal-window)
10. [Fine-tuning](#10-fine-tuning)
11. [When something's wrong](#11-when-somethings-wrong)
12. [Questions people ask](#12-questions-people-ask)
13. [MIDI reference](#13-midi-reference)

---

## 1. What you need

| Item | Notes |
|---|---|
| **A Mac** | The same Mac that runs Lightkey. |
| **Lightkey** | Your lighting software, with your fixtures already set up. |
| **An Ethernet adapter** for the Mac | USB‑C or Thunderbolt to Ethernet. Most MacBooks don't have an Ethernet port. |
| **A network switch** | Any plain switch (5 or 8 ports). No router or internet needed. |
| **Ethernet cables** | One per CDJ, one for the mixer, one for the Mac. The Mac's cable can be long (up to 100 m), so FOH is fine. |
| **Pioneer CDJs** | Built and tested for **CDJ‑3000 / CDJ‑3000X**. Up to 6 players. |
| **DJM mixer** (e.g. DJM‑V10) | Optional but recommended. |
| **DJs' USB sticks** | Prepared in **rekordbox** as usual. Phrase analysis switched on gives the best results, but it isn't required (see part 12). |

Optional, only for tracks that have no rekordbox data at all (e.g. streaming):
a spare **aux output from the FOH desk** into a small **USB audio interface**
on the Mac.

---

## 2. How it fits together

```
 CDJ 1   CDJ 2   CDJ 3   DJM-V10          (up to 6 CDJs)
   │       │       │       │
   └───────┴───┬───┴───────┘   Ethernet, from each unit's LINK port
               │
        [ Network switch ]
               │                Ethernet (long run to FOH is fine)
               │
   ┌───────────┴─────────────────────────────┐
   │  Mac                                    │
   │   Lumière Link ──MIDI──► Lightkey ──────┼──► DMX / Art-Net ──► Lights
   └─────────────────────────────────────────┘
```

- The CDJs share **tempo, beat position and the track's song structure** over
  the network cable. No audio cable is needed.
- Lumière Link turns that into two MIDI inputs on the Mac:
  - **Lumiere Clock**: the tempo. Lightkey's BPM follows the DJ, pitch fader included.
  - **Lumiere Cues**: messages such as "the drop starts now, play drop sequence 7".
- Lightkey runs your sequences when those messages arrive.

---

## 3. Install it (once)

### 3.1 Download

On the Mac, open **Terminal**: press ⌘ Space, type `Terminal`, press Enter.
Copy this line in, press Enter, and wait for it to finish:

```bash
git clone https://github.com/sankyrajeev/ai_clock_midi ~/LumiereLink
```

> If macOS asks to install "command line developer tools", click **Install**,
> wait for it to finish, then run the line again.

That creates a folder called **LumiereLink** in your home folder. In Finder,
choose **Go › Home** and open **LumiereLink**.

> **Already installed it earlier** as `ai_clock_midi`? You don't need to download
> again. Use that folder instead, and update it with
> `cd ~/ai_clock_midi && git pull`. Wherever this guide says LumiereLink, read
> ai_clock_midi.

### 3.2 First launch

1. In the LumiereLink folder, double-click **`Simulate Booth (no gear).command`**.
2. If macOS says it "can't be opened because it is from an unidentified
   developer": **right-click** the file › **Open** › **Open**. You only need to
   do this once per file.
3. A Terminal window opens and says **"Setting up (this takes a minute)…"**.
   This happens once. It downloads the parts the app needs.
4. If macOS asks whether the app may **find devices on your local network**,
   click **Allow**. Without it the app can't see the CDJs.

### 3.3 Getting updates

Whenever there's a new version, open Terminal and run:

```bash
cd ~/LumiereLink && git pull
```

Your own settings (`pools.json`) are never overwritten by an update.

---

## 4. Try it without any gear

**`Simulate Booth (no gear).command`** pretends to be a booth with two
CDJ‑3000Xs and a DJM‑V10, right there on your Mac. Use it to learn the app and
to set up Lightkey at home.

Double-click it and watch the Terminal window. Within a few seconds you'll see:

```
Found CDJ-3000X (device 1) at 127.0.0.1
Found CDJ-3000X (device 2) at 127.0.0.1
Found DJM-V10 (device 33) at 127.0.0.1
Deck 2: 7 sections from rekordbox (Intro / Up / Chorus / Down / Up / Chorus / Outro)
Deck 1: 6 sections from waveform (Intro / Drop / Breakdown / Build / Drop / Outro)
Joined the network as device 7 ...
Intro: sequence 16 of 24 (channel 6, note 51)
Build: sequence 5 of 24 (channel 3, note 40)
Pre-drop: sequence 17 of 24 (channel 5, note 52)
Drop: sequence 5 of 24 (channel 2, note 40)
```

That means it found the (pretend) decks, worked out each track's sections,
and is choosing sequences as the music plays. To stop, close the window.

---

## 5. Set up Lightkey: tempo

With a Lumière Link window running (the simulated booth or the real thing):

1. In Lightkey, open the **tempo / BPM settings**.
2. Set the tempo source to **MIDI Clock**.
3. Choose the input **Lumiere Clock**.
4. Lightkey's BPM should now match the deck: 128 BPM in the simulated booth.

Your BPM-based effects (chases, strobes, colour changes) now follow the DJ.

> Menu names can differ slightly between Lightkey versions. Look for where
> Lightkey lets you pick **MIDI Clock** or **Ableton Link** as the tempo source.

---

## 6. Set up your sequence pools

### 6.1 The idea

Instead of every drop looking the same, you build several looks for each kind
of moment, for example **24 drop sequences** and **24 build sequences**.
Lumière Link picks one each time:

- **Shuffle** (the default): random, but it plays all 24 before repeating any,
  and never plays the same one twice in a row.
- **Builds and drops are paired**: if build sequence 7 played, the drop that
  follows uses drop sequence 7. Design build 7 and drop 7 to belong together,
  and they'll always arrive together.

There are 8 pools, each with up to 24 sequences:

| Pool | When it fires |
|---|---|
| **Drop** | the drop / chorus hits |
| **Build** | the build-up starts |
| **Breakdown** | the beat drops out |
| **Pre-drop** | one bar **before** each drop (great for a blackout or riser) |
| **Intro** | a track's intro starts |
| **Outro** | a track's outro starts |
| **Verse** | verse sections (vocal / lower-energy tracks) |
| **Bridge** | bridge sections |

### 6.2 Make the sequences in Lightkey

Create your sequences (cues) in Lightkey: for example **Drop 1 … Drop 24** and
**Build 1 … Build 24**. You don't need 24 of each. Make as many as you like, up
to 24.

> **Make sure only one look plays at a time.** When drop sequence 9 starts,
> drop sequence 4 should stop. Check how your Lightkey cues are set to behave
> when a new cue is triggered. Each pool should replace whatever was running,
> not stack on top of it.

### 6.3 Tell Lumière Link how many you made

The first time Lumière Link runs, it creates a settings file called
**`pools.json`** in the LumiereLink folder. Open it with **TextEdit**
(right-click › Open With › TextEdit). It looks like this:

```json
{
  "mode": "shuffle",
  "pair_builds_with_drops": true,
  "pools": {
    "drop": 24,
    "build": 24,
    "breakdown": 24,
    "pre_drop": 24,
    "intro": 24,
    "outro": 24,
    "verse": 24,
    "bridge": 24
  }
}
```

Change each number to **how many sequences you actually made** for that pool:

- Made 24 drops and 24 builds but only 6 breakdowns → `"breakdown": 6`
- Don't want intro sequences at all → `"intro": 0` (0 switches a pool off)
- The maximum is 24.

**This matters:** if the file says 24 but you only made 10, then 14 times out
of 24 the app picks a sequence that doesn't exist, and nothing happens on
that drop.

Save the file (⌘ S). The next time you start Lumière Link it uses the new
numbers, and the Terminal window confirms them at the top, e.g.
`Sequence pools: Drop 24 (ch 2), Build 24 (ch 3), Breakdown 6 (ch 4) …`

Other settings in the same file:
- `"mode"`: `"shuffle"` (recommended), `"random"` (may repeat), or `"order"` (1, 2, 3 … in turn).
- `"pair_builds_with_drops"`: `true` or `false`.

### 6.4 Connect each sequence to its MIDI note (MIDI Learn)

Lightkey needs to know which MIDI message starts which sequence. The **MIDI
Learn helper** sends each one for you, one at a time:

1. Open Lightkey.
2. Double-click **`Learn Sequences (MIDI Learn helper).command`**.
3. The window says, for example:
   `In Lightkey, start MIDI Learn on Drop sequence 1, then press Enter`
4. In Lightkey, put **Drop 1** into **MIDI Learn** mode, so it waits for a MIDI
   message.
5. Click back on the Terminal window and press **Enter**. Lumière Link sends
   Drop 1's note, and Lightkey learns it.
6. It moves on to **Drop sequence 2**. Repeat steps 4–5.
7. When the Drop pool is done it continues with Build, then the others.

Shortcuts while it runs: type **`s`** then Enter to **skip** the rest of a pool,
or **`q`** then Enter to **quit**.

You only do this once. Lightkey remembers the mappings.

> Each pool has its own MIDI channel, and the sequence number decides the note
> (sequence 1 = C1 … sequence 24 = B2). The full table is in part 13. You never
> need to type these in yourself; the helper does it.

---

## 7. Connect the real gear

1. **Quit PRO DJ LINK Bridge, ShowKontrol and rekordbox** on this Mac. Only one
   DJ Link app can listen on a Mac at a time, and Lumière Link replaces Bridge
   for your lights.
2. Plug every **CDJ** into the switch, using the port labelled **LINK** on the back.
3. Plug the **DJM** into the switch the same way.
4. Plug the **Mac** into the switch with your Ethernet adapter.
5. Power everything on, in any order.
6. Double-click **`Start Lumiere Link.command`**.

Within a few seconds you should see a `Found CDJ-3000X …` line for each player,
then `Joined the network as device 7`. Load a track and press play, and a line
like `Deck 2: 7 sections from rekordbox …` appears.

> **A CDJ stuck on an old IP address?** Sometimes a player keeps an address from
> a previous venue. Lumière Link spots this, says so, and offers to fix it from
> the Mac side, so you don't have to restart the CDJ. macOS asks for your
> password first; nothing changes without it. It **adds** an extra address to
> the Mac's Ethernet port and doesn't touch your normal address or Wi‑Fi. It
> disappears when you unplug the adapter or restart the Mac.

---

## 8. Gig-day checklist

1. ☐ Bridge, ShowKontrol and rekordbox are **closed** on the Mac.
2. ☐ CDJs + DJM + Mac all plugged into the **same switch** (LINK ports).
3. ☐ Open **Lightkey**.
4. ☐ Double-click **`Start Lumiere Link.command`**.
5. ☐ Terminal shows **Found …** for every player, and **Joined the network**.
6. ☐ Lightkey's BPM matches the CDJ.
7. ☐ Play a track: the status line shows the section, e.g. `[deck 2: Up (rekordbox) · build 5/24]`.
8. ☐ Leave the Terminal window open all night. **Closing it stops the lights following.**

---

## 9. Reading the Terminal window

The last line updates on every beat:

```
[deck 2: Chorus (rekordbox) · drop 5/24]  DJM-V10  dev 33 beat 1  128.00 BPM  (track 128.00, pitch +0.00%)
```

| Part | Meaning |
|---|---|
| `deck 2` | which CDJ the lights are following (the tempo master) |
| `Chorus` | the section playing now (rekordbox calls a drop "Chorus", a build "Up", a breakdown "Down") |
| `(rekordbox)` / `(waveform)` | where the section information came from (see part 12) |
| `drop 5/24` | the last sequence picked from your pools |
| `beat 1` | position in the bar, counting 1 2 3 4 |
| `128.00 BPM` | the tempo being sent to Lightkey |

Lines above it are events: decks found, tracks analysed, sequences picked,
and any problem messages (part 11).

---

## 10. Fine-tuning

Options go in the **`ARGS=(...)`** line near the top of
`Start Lumiere Link.command` (right-click › Open With › TextEdit). Example:

```bash
ARGS=(--phrases --latency-ms 30)
```

| Want this? | Add |
|---|---|
| Lights look a touch **late** | `--latency-ms 30` (try 20–40). Cues and the clock fire that many milliseconds early. |
| Follow the deck whose **fader is up**, not the tempo master | `--follow on-air` |
| Always follow **one particular player** | `--follow 2` |
| Use the **live audio backup** for tracks with no rekordbox data | `--cues` (needs the audio interface from part 1) |
| Also share tempo over **Ableton Link** | `--link` |

Keep `--phrases` in the list. It's what turns on the cues.

---

## 11. When something's wrong

The app tells you what to check. The usual messages:

| Message | What to do |
|---|---|
| **No Ethernet link on …** | The cable between the Mac and the switch is loose or faulty, or the switch has no power. |
| **No CDJs or mixer are answering** | Check the cables from the players to the switch, and that the players are on. |
| **No Ethernet port found** | Plug in the Mac's Ethernet adapter. |
| **macOS is blocking this app from the local network** | System Settings › Privacy & Security › **Local Network** › switch on **Terminal**. |
| **… is running on this Mac and is probably taking the status packets** | Quit that app (Bridge / ShowKontrol / rekordbox), then restart Lumière Link. |
| **CDJ … is at 192.168.x.x, but this Mac's Ethernet is on …** | A player is stuck on an old address. Accept the password prompt to fix it, or restart that CDJ. |
| **no phrase or waveform data for this track** | Typical for streaming. Use the live audio backup (`--cues`) or play from a rekordbox USB. |

Other problems:

- **Lightkey's BPM doesn't move:** check the tempo source is MIDI Clock with input **Lumiere Clock**, and that the Lumière Link window is open.
- **A drop happens but nothing changes:** the picked sequence probably isn't mapped. Check the count in `pools.json` matches how many sequences you made, and re-run the MIDI Learn helper for that pool.
- **Looks stack up instead of replacing each other:** see the note in 6.2.
- **Unplugged a cable mid-set?** Just plug it back in. Lumière Link reconnects by itself within a few seconds.

---

## 12. Questions people ask

**Does the DJ need to do anything special?**
No. They play as normal. Best results come from tracks analysed in rekordbox
with **phrase analysis** on (it's on by default in rekordbox 6 and 7).

**What if a guest DJ never ran phrase analysis?**
It still works. When a track has no phrase information, Lumière Link reads the
track's **waveform** from the CDJ as soon as it loads and works out the
builds, drops and breakdowns itself. Cues still land exactly on the beat. The
status line shows `(waveform)` instead of `(rekordbox)`.

**What about streaming tracks?**
Those may carry no analysis at all. That's what the optional live audio backup
(`--cues` with a FOH aux) is for.

**How many CDJs can I use?**
Up to 6 players plus the mixer. The lights follow the **tempo master**, so when
the DJ hands master to the next deck, the lights move with them.

**Do I still need PRO DJ LINK Bridge?**
Not for Lightkey. Lumière Link talks to the CDJs directly. Keep Bridge closed
while Lumière Link runs.

**Will it change my Mac's network settings?**
Only in the stuck-IP case in part 7, only after you enter your password, and
only by adding a temporary extra address.

**Can I test without the decks?**
Yes: **`Simulate Booth (no gear).command`** (part 4).

---

## 13. MIDI reference

**Lumiere Clock:** MIDI clock (24 per beat), plus Start on the downbeat.

**Lumiere Cues, sequence pools:** sequence *n* is note 35 + *n*.

| Pool | Channel | Sequence 1 | Sequence 24 |
|---|---|---|---|
| Drop | 2 | note 36 (C1) | note 59 (B2) |
| Build | 3 | 36 | 59 |
| Breakdown | 4 | 36 | 59 |
| Pre-drop | 5 | 36 | 59 |
| Intro | 6 | 36 | 59 |
| Outro | 7 | 36 | 59 |
| Verse | 8 | 36 | 59 |
| Bridge | 9 | 36 | 59 |

**Lumiere Cues, channel 1** (sent on every section, whether or not pools are on):

| Note | Cue | | Note | Cue |
|---|---|---|---|---|
| 36 | Breakdown | | 42 | Outro |
| 37 | Build | | 43 | Verse |
| 38 | Drop | | 44 | Bridge |
| 39 | Every 8 bars | | 45 | Pre-drop |
| 40 | Every bar | | 46 | New track |
| 41 | Intro | | | |

| CC (channel 1) | Value |
|---|---|
| 20 | Section energy (breakdown low … drop full) |
| 23 | Build progress, rising through each build |

For how it all works inside, every command-line option, and the USB-audio
detector, see [TECHNICAL.md](TECHNICAL.md).
