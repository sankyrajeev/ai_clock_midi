# Lumière Link

**by Vital Acoustica · Pro Audio • Lighting • Rentals**

Lumière Link makes your lights follow the DJ automatically. It listens to the
Pioneer CDJs over a network cable, knows the tempo and exactly where each track
is, and tells Lightkey when a **build**, **drop** or **breakdown** starts. Each
time, it picks one of your own lighting sequences.

This guide assumes you've never done this before. Follow it top to bottom the
first time. After that, the **Gig-day checklist** (part 8) is all you need.

> **It's one app.** You download it, drag it into Applications and open it.
> Everything (decks, waveform, sequences, projects, settings) is on one screen.
> No Terminal and no text files.

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
9. [Reading the screen](#9-reading-the-screen)
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

1. Go to **https://github.com/sankyrajeev/ai_clock_midi/releases/tag/app-latest**.
2. Under **Assets**, download the file for your Mac:
   - **`…-Apple-Silicon-M1-M2-M3-M4.dmg`** for Macs with an Apple M chip
   - **`…-Intel.dmg`** for older Intel Macs

   Not sure? Apple menu › **About This Mac**: "Chip: Apple M…" means Apple silicon.
   The Intel version does run on an M Mac, but slower and less reliably, so pick the right one.
3. Open the downloaded `.dmg` and drag **Lumière Link** onto **Applications**.
   Then eject the disk image and always open the app **from Applications**,
   not from the disk image window.

### 3.2 First launch

The app isn't sold through the App Store, so macOS asks you to confirm it once:

1. Open **Applications**, **right-click** Lumière Link › **Open** › **Open**.
2. On newer macOS (Sequoia and later) that button may be missing. Then go to
   System Settings › **Privacy & Security**, scroll down to
   *"Lumière Link was blocked…"* and click **Open Anyway**.

macOS then asks a few questions. Answer them like this:

| Question | Click | Why |
|---|---|---|
| Find devices on your **local network**? | **Allow** | Without it the app can't see the CDJs. |
| Lumière Link wants to control **System Events**? | **OK** | So it can read which Lightkey project is open (part 6.3). |
| Access the **microphone**? | **OK** (only appears if you turn on Live audio backup) | For the optional FOH aux input. |

The first time, the **How it works** guide opens. It shows the connections.
You can open it again any time with the **How it works** button at the top.

### 3.3 Getting updates

Download the newest `.dmg` from the same link and drag it into Applications
again (choose **Replace**). Your Lumière projects in **Documents › Lumiere Link**
and your settings are kept.

---

## 4. Try it without any gear

Switch on **Demo mode** (right-hand column), or click **Try demo mode** on the
start screen. It pretends to be a booth with two CDJ‑3000Xs and a DJM‑V10.
Use it to learn the app and to set up Lightkey at home.

Within a few seconds you'll see:

- two decks in the **Decks** rack on the left, with titles, BPM and a green dot
  for the one the lights follow,
- the **phrase map** (Intro / Build / Drop …) and the **waveform** of that track,
- **Now / Next** with a countdown ring to the next section,
- the cue pads lighting up and the **Cue log** filling with lines like
  `Drop · sequence 5 of 24`.

Switch Demo mode off before the gig.

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
- **Pair builds with drops** (off unless you switch it on): if build sequence 7
  played, the drop that follows uses drop sequence 7. Design build 7 and drop 7
  to belong together, and they'll always arrive together.

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

### 6.3 One Lumière project per Lightkey project

Lumière Link keeps your settings in **projects**, one for each show, just like
Lightkey. A project remembers:

- how many sequences each pool has,
- Shuffle / Random / In order, and build → drop pairing,
- which sequences you've already connected to Lightkey (the ✓ marks).

The project name is at the top of the screen, next to the logo. Click it to
switch, or to make a **New project…**

**It follows Lightkey by itself** (the **Follow Lightkey project** switch in
that menu). Every few seconds Lumière Link checks which project Lightkey has
open and uses the Lumière project with the **same name**:

- Open **"Club Night"** in Lightkey → Lumière Link uses its **"Club Night"** project.
- Lightkey project with no Lumière project yet → Lumière Link **creates one with
  the same name**. It starts empty (every pool at 0), ready for that show.
- Switch Lightkey to another project mid-show → the next cue uses the new
  project's sequences.
- Lightkey's other windows (External Control, Preferences, Save…) are ignored,
  so mapping a cue or moving between windows never changes your project.

A small **LIGHTKEY** tag next to the project name means it's following.

It only reads the project's name; it never changes your Lightkey files.
If you clicked "Don't Allow" for System Events: System Settings › Privacy &
Security › **Automation** › Lumière Link › switch on **System Events**.

Making a project by hand? **Use the same name as your Lightkey project**, so
they pair up by themselves later.

Projects are saved in **Documents › Lumiere Link**, one file per show
(e.g. `Club Night.lumiere`). Copy that folder to take your shows to another Mac.

### 6.4 Tell each project how many sequences you made

Click **Sequences** (above the cue pads). On the left is each pool with
**– / +** buttons. Set each to **how many sequences you actually made** in that
Lightkey project. 0 switches a pool off; the maximum is 24. Below the tiles you
choose **Shuffle / Random / In order** and **Pair builds with drops**.

Everything saves by itself into the current project ("Saved automatically").

**This matters:** if it says 24 but you only made 10, then sometimes the app
picks a sequence that doesn't exist, and nothing happens on that drop.

### 6.5 Connect each sequence to Lightkey (MIDI Learn)

Lightkey needs to know which MIDI message starts which sequence. In the same
**Sequences** screen:

1. Open your project in Lightkey (Lumière Link switches to the matching project).
2. In Lumière Link, pick a pool (e.g. **Drop**) and switch on **Learn**.
3. In Lightkey, open the **External Control** setting of Drop 1 and start MIDI Learn.
4. In Lumière Link, click tile **1**. Lightkey learns it, and the tile gets a ✓.
5. Repeat for the other tiles, then the other pools.

So yes: in Lightkey you pick the cue, choose External Control / MIDI Learn, then
click the tile in Lumière Link.

**You do this once per Lightkey project, not once per show night.** Lightkey
saves the mappings in its project (save with ⌘ S as usual) and Lumière Link
saves the ✓ marks by itself. Restarting the Mac changes nothing.

> **Tip:** for a new show, **duplicate an already-mapped Lightkey project**
> instead of starting from scratch. Lumière Link always uses the same notes
> (Drop sequence 1 is always channel 2, note C1), so the copy's mappings work
> straight away. In the new Lumière project, set the counts again and tick the
> tiles (Learn on, click each tile once).

With Learn switched off, clicking a tile just plays that sequence, which is a
quick way to check it.

### 6.6 New looks in long sections, and a blackout before the drop

Both are in the **Sequences** window, under the tiles, and start **off**.

**New look every 8 / 16 / 32 bars** (for each pool). A long drop doesn't have to
stay on one look: with **8 bars**, a 32-bar drop plays four different drop
sequences, changing exactly on the bar line. They come from the same pool (with
Shuffle, no repeats). If the section is nearly over, it waits for the next one
instead of changing at the last moment.

**Blackout before the drop** (on the Drop pool). Many tracks go silent for a
moment right before the drop. Lumière Link can black the lights out there, then
fire the drop sequence on the first beat of the drop:

- **Fixed length**: always ½ beat, 1 beat, 2 beats or 1 bar before the drop.
- **Follow the music**: it finds the silence in the track's waveform and blacks
  out exactly when the music stops. A track with no gap gets no blackout.

In Lightkey, make one **Blackout** cue (everything off) and map it once: with
**Learn** on, click the **Blackout cue** button next to these options. It uses
its own message (channel 10, note C3), so it never clashes with a sequence.
The **Blackout** pad under Lighting cues fires it by hand.

---

## 7. Connect the real gear

1. **Quit PRO DJ LINK Bridge, ShowKontrol and rekordbox** on this Mac. Only one
   DJ Link app can listen on a Mac at a time, and Lumière Link replaces Bridge
   for your lights.
2. Plug every **CDJ** into the switch, using the port labelled **LINK** on the back.
3. Plug the **DJM** into the switch the same way.
4. Plug the **Mac** into the switch with your Ethernet adapter.
5. Power everything on, in any order.
6. Open **Lumière Link** (with Demo mode off).

Within a few seconds each player appears in the **Decks** rack and the dots at
the top light up for each device found. Load a track and it shows the title,
the phrase map and the waveform. The small tag says **rekordbox** or
**waveform**, depending on where the sections came from.

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
3. ☐ Open **Lightkey**, then open **Lumière Link**. Demo mode is **off**.
4. ☐ Every player shows up in the **Decks** rack.
5. ☐ The project name at the top matches your Lightkey project.
6. ☐ Lightkey's BPM matches the CDJ.
7. ☐ The button at the top says **Live** (not Paused).
8. ☐ Leave Lumière Link open all night. **Quitting it stops the lights following.**

---

## 9. Reading the screen

| Where | What it tells you |
|---|---|
| **Dots at the top** | one per device on the network (CDJs, the mixer). |
| **Live / Paused** | Paused stops the automatic cues; the clock keeps running and the pads still work by hand. |
| **Decks** (left) | every player. The highlighted one is the deck the lights follow. Click a deck to follow just that one. |
| **Tag next to the title** | `rekordbox` or `waveform`: where the sections came from (part 12). |
| **Phrase map and waveform** | the track's sections and where the DJ is in it. |
| **Now / Next** | the section playing, the next one, and a ring counting down to it. |
| **Lighting cues** pads | light up as cues go out, with the sequence number picked. Tap one to fire it by hand. |
| **Cue log** (right) | everything that was sent, newest at the top. |
| **Orange banner** | a problem, with what to do (part 11). |

---

## 10. Fine-tuning

All in the **Controls** column on the right. Changes are remembered.

| Want this? | Do this |
|---|---|
| Lights look a touch **late** | Drag the **Light delay** knob to 20–40 ms. Cues and the clock fire that much early. |
| Follow the deck whose **fader is up**, not the tempo master | **Follow › On air** |
| Always follow **one particular player** | Click that deck in the rack (**Follow › One deck**) |
| Tracks with no rekordbox data (streaming) | Switch on **Live audio backup** and choose the audio interface (needs the FOH aux from part 1). |
| Lightkey tempo only, no section cues | Switch off **Phrase cues**. |

---

## 11. When something's wrong

The app tells you what to check. The usual messages:

| Message | What to do |
|---|---|
| **No Ethernet link on …** | The cable between the Mac and the switch is loose or faulty, or the switch has no power. |
| **No CDJs or mixer are answering** | Check the cables from the players to the switch, and that the players are on. |
| **No Ethernet port found** | Plug in the Mac's Ethernet adapter. |
| **macOS is blocking this app from the local network** | System Settings › Privacy & Security › **Local Network** › switch on **Lumière Link**. |
| **… is running on this Mac and is probably taking the status packets** | Quit that app (Bridge / ShowKontrol / rekordbox), then restart Lumière Link. |
| **CDJ … is at 192.168.x.x, but this Mac's Ethernet is on …** | A player is stuck on an old address. Accept the password prompt to fix it, or restart that CDJ. |
| **macOS hasn't allowed reading which Lightkey project is open** | System Settings › Privacy & Security › Automation › Lumière Link › switch on System Events (part 6.3). |
| **no phrase or waveform data for this track** | Typical for streaming. Switch on **Live audio backup**, or play from a rekordbox USB. |

Other problems:

- **Lightkey's BPM doesn't move:** check the tempo source is MIDI Clock with input **Lumiere Clock**, and that Lumière Link is open.
- **A drop happens but nothing changes:** the picked sequence probably isn't mapped. Check the counts in **Sequences** (part 6.4) match how many sequences you made, and learn that tile again.
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
deck shows **waveform** instead of **rekordbox**.

**What about streaming tracks?**
Those may carry no analysis at all. That's what the optional live audio backup
(**Live audio backup** with a FOH aux) is for.

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
Yes: **Demo mode** (part 4).

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

**Lumiere Cues, blackout before the drop:** channel 10, note 60 (C3).

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

---

## Appendix: running without the app (Terminal)

The older way still works and is kept as a backup. Download the folder with
`git clone https://github.com/sankyrajeev/ai_clock_midi ~/LumiereLink`, then
double-click **`Start Lumiere Link.command`** (real gear) or
**`Simulate Booth (no gear).command`**. Both share the same Lumière projects as
the app. Use one or the other, not both at once. The USB-audio version lives on
the `backup-usb-audio` branch.
