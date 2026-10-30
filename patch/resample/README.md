# resample: live resampling

A patch for Polyend Tracker+ firmware 1.2.0. It adds two buttons to the Sample
Recorder's setup:

- **Trk Audio** (Tracker Audio): hear the pattern in the recorder. Press Play in the
  setup to set levels; press Play during a recording, or start it with Shift+Rec, and
  the pattern plays with its sample and synth instruments, delay and reverb.
- **Trk Record** (Record Tracker): record the pattern too, with no cable. The recording
  holds Line In plus the tracker's own sound.

Both are Off on a new project, and each project keeps its own (in `resample.bin` in its
folder). With both Off the recorder behaves as stock. Every recording goes after the
loaded samples, so they stay intact. Installed on its own, Config shows the firmware
version as `1.2.0-rs8`.
[docs/RESAMPLE_SOURCE_PLAN.md](../../docs/RESAMPLE_SOURCE_PLAN.md) and
[docs/LIVE_RESAMPLING_PLAN.md](../../docs/LIVE_RESAMPLING_PLAN.md) have the
investigation and the plan.

**Status:** tested on hardware, on its own and in `patch/all` (`1.2.0-MR8`). It is also
in the web patcher.

## Using it

Open the Sample Recorder and pick the source. The two buttons are in the setup's
second and third slots, after Source; press a slot's button to switch it between Off
and On.

| Button | Shows for | Off | On |
|---|---|---|---|
| **Trk Audio** | Line In, Mic LG, Mic HG, USB | the pattern is silent in the recorder, as stock | the pattern is heard: Play in the setup, Play during a recording, Shift+Rec |
| **Trk Record** | Line In | the recording holds Line In only, as stock | the recording holds Line In plus the tracker |

Radio shows its own controls in those slots, so neither button is there and the
recorder is stock with Radio. Trk Record needs Trk Audio: turning Trk Record on also
turns Trk Audio on, and turning Trk Audio off also turns Trk Record off (and stops a
pattern that Play started in the setup).

With **Trk Audio on**:

- **Play in the setup** plays the pattern and you hear it. Rec then records with it
  running.
- **Rec, then Play:** Rec starts the recording as in stock; Play starts the pattern.
- **Shift+Rec:** the recording and the pattern start together. With Pre Roll on, the
  count-in runs first with the pattern silent. With Pre Roll off, both start at once.
- **Play during the count-in** does the same as Shift+Rec.
- **Rec during the count-in** lets the count-in run on, as in stock. Shift+Rec there
  also arms the pattern; a plain Rec keeps whatever the count-in started with.
- **Stopping the recording stops the pattern.** Notes still sounding end on their
  release; the delay and reverb stop with the recording, as in the stock recorder.

With **Trk Record on** (Line In):

- **Monitor off:** the recording is Line In plus the tracker's sound (the master, as
  the output jacks get it), added sample by sample and clipped at full scale.
- **Monitor on:** Monitor already routes Line In through the master, so the recording
  is the master alone and Line In isn't doubled.
- **Monitor** only decides whether you hear Line In, as in stock.
- **The count-in's last click** isn't recorded. It starts as the recording starts, so
  with Trk Record on it is muted at the count-in's end, and you don't hear it either.
  The other clicks sound as usual.

With Trk Audio off, Shift+Rec is a plain Rec and Play in the recorder stays silent, as
in stock.

**Feedback:** if the tracker's output is cabled back to its own input and Monitor is
on, the input reaches the output at full level, as in the stock recorder, so the loop
builds up when the cable path (output volume and input gain) is at unity or more. With
Trk Record you don't need the cable.

**Limits:**

- **The longest recording is the free part of the sample memory.** Stock records into
  the start of the sample memory, over the project's loaded samples (that is why the
  stock recorder keeps the sample instruments silent). This patch records after the
  loaded samples instead, for every recording, so the samples stay intact. With nothing
  loaded nothing changes (about 713 s mono); with 30 MB of samples loaded it is about
  6 minutes mono or 3 minutes stereo. If less than 10 seconds would fit, the recording
  goes where stock puts it, and the pattern stays silent for that recording.
- **Trk Record is for Line In only** (the stereo record path). Mic and USB record the
  input only.
- **The Volume knob doesn't reach the recording**: the tracker is recorded before it.
  The level meter shows the input, not the mix.
- A pattern started by external MIDI start or clock during a recording doesn't pass the
  Play button, so it stays silent, as in stock.
- Shift+Rec while the pattern is already playing in the setup plays it again from its
  start, so the recording and the pattern start together.
- The first engine block after Play runs with the delay and reverb sends from before
  (the firmware recomputes them in that block). The difference is below -60 dB and
  lasts a few milliseconds.

## How it works

Entering the Sample Recorder doesn't mute the output. It blocks the sample and synth
note-ons, bypasses the delay, sets the sends to 0 and the mix to 0 dB, and the engine's
per-block update skips its second half while a recording runs. The reason is memory:
stock records at the start of the sample bank, where the loaded samples are.

| Hook | Where | What |
|---|---|---|
| `mt_rs_rec_buffer` | `bl 0x6010cb90` at `0x600f90d2` (the record start's buffer setup) | The buffer starts after the loaded samples (bytes used, rounded up to 32 bytes) and takes the rest of the bank (rounded down to 256 bytes). Below 10 s, or for a call that isn't stock's, stock's buffer |
| `mt_rs_rec_button` | the Rec handler's pool word `0x600f9000` (`0x600d0620`) | With Trk Audio on, in the setup: arm "start the pattern" if Shift is held, then the stock handler |
| `mt_rs_rec_started` | `bl 0x6010c890` at `0x600f90d8` (the record queues' start, reached from Rec and from the count-in's end alike) | With Trk Record on and Line In: start taking the master's blocks with the input's. If armed and the buffer was moved: open the gates and play the pattern |
| `mt_rs_play_button` | the Play handler's pool word `0x600fa4ac` (`0x600d07b4`) | With Trk Audio on: during the count-in, arm instead of starting the pattern; in the setup or during a moved recording, open the gates, then the stock handler |
| `mt_rs_update` | Sample Recorder vtable slot `0x60042c54` (update `0x600fafe8`) | Once the recording has stopped: stop taking the master's blocks, stop the pattern, close the gates. Disarm when nothing is counting in or recording. Reads and writes `resample.bin` outside a recording and its count-in |
| `mt_rs_start` | Sample Recorder vtable slot `0x60042c58` (start `0x600fa228`) | A new visit starts with the gates shut and nothing armed |
| `mt_rs_labels1`-`4` | `bl 0x600fbc40` at `0x600f99e2`, `0x600f9a3a`, `0x600f9ab6`, `0x600fcbd4` (the setup's slot labels) | Draw Trk Audio in slot 2 and Trk Record in slot 3 (Line In) after stock empties them |
| `mt_rs_slot2_button`, `mt_rs_slot3_button` | the setup's button binder `0x600f8db4`, pool words `0x600f8e24` and `0x600f8e28` | A press in the setup toggles the setting (stock acts there only for Radio) |
| `mt_rs_copy` | `bl 0x6010c7c8` at `0x6010c6f6` (the record copy of one input block, audio interrupt) | With Trk Record: adds the matching block of the master's record queues (`E+0x1e58`/`E+0x2248`, which only Export uses otherwise), or with Monitor on takes it alone |
| `mt_rs_countin_done` | the recorder start's pool word `0x600fa490` (the count-in's end callback `0x600f8605`) | With Trk Record: mute the preview player's master input, which carries the count-in's clicks, until the recording ends |
| `mt_rs_is_recording` | the ITCM image's word `0x601acc28` (the literal of the thunk at ITCM `0x1f540`, loaded at boot) | `engine_block_update` asks it whether to return early: "no" while the patch has the gates open and the recorder records into the buffer it moved |
| `mt_rs_pattern_update` | Pattern screen vtable slot `0x600403dc` (update `0x600e1f98`) | Reads and writes `resample.bin` |
| `mt_rs_project_update` | project screen vtable slot `0x6004193c` (update `0x600ead58`) | Reads and writes `resample.bin`, so New Project and Open are seen before a Save As |
| `mt_rs_save_name` | `bl strncpy` at `0x60114eaa` (the save request's name copy, Save As only) | Marks a Save As: the settings go with the project to its new folder |

All the hooks are chained: each calls whatever the site pointed to before (the stock
function, or another patch's hook), so other patches can hook the same sites.

- **Opening the gates** undoes the recorder's entry with the stock leave path's own
  calls: sample and synth note-ons back (`0x600b39e4`, `0x600ad600`), the delay bypass
  off (`0x600b2de4`), the eleven mix levels back (`0x600b3ae4`), the limiter from the
  project (`0x600b4128`). It then marks every track's and synth's sends to be recomputed,
  with the audio interrupt masked. The line-in passthrough and the Monitor level stay as
  the recorder set them.
- **Closing the gates** stops the pattern and re-applies the recorder's entry calls in
  its order.
- **Recording the tracker:** the master mixer feeds, besides the output jacks, a pair of
  record queues that only Export uses. At the record start the patch begins them
  together with the input queues, with the audio interrupt masked, so both start on the
  same block. The queues update before the master mixers, so the recording is in step
  with what the output jacks play. The mix costs about 1,000 instructions per audio
  block (the M7's saturating 16-bit add).
- **The count-in's clicks** play through the preview player (master input 5); the last
  one starts in the tick that ends the count-in. Its gain is set to 0 from then until
  the recording ends, and given back after.
- **Per project:** `resample.bin` (12 bytes: `RSM1`, the two settings, a checksum) is
  read and written from the Pattern screen's, the project screen's and the recorder's
  updates, a while after a change, only while the file manager is idle and never
  during a recording or its count-in. Another project: both Off at once, then its file.
  A new project (`New Project` with no folder yet): both Off, nothing written. Save As
  (and a new project's first save, which is one) writes the settings to the new folder.

No codec, DMA or ITCM code changes; the ITCM image changes in one word only. The code is
3,469 bytes. Its state is 40 bytes (a magic word, the flags, the last buffer and size,
the two settings, the file's bookkeeping, the muted gains), which the patcher places in
free RAM. The patcher moves the code's code-address and RAM-address words to match
(`prebuilt/resample.rel`). Firmware addresses are in
[docs/FINDINGS.md](../../docs/FINDINGS.md) section 28.

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/resample/patch.json build/TrackerPlus_rs8.ptf
```

To combine it with other mods, list it in a bundle spec (see "Choosing patches" in
[docs/PATCHING.md](../../docs/PATCHING.md)).

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder).
`build.sh` writes `patch/resample/build/`, which the patcher then uses instead of
`prebuilt/`:

```bash
patch/resample/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_resample.py build/TrackerPlus_rs8.ptf path/to/TrackerPlus_1.2.0.ptf
```

It runs the stock code with the patch in place, next to stock firmware (about 7
minutes):

- **Buffer:** placement in 15 cases (empty, mono, stereo, the 10 s limit, a full bank,
  fields holding garbage), read back through the recorder's own accessors; a recording
  leaves the loaded samples byte-identical, where stock writes over them.
- **Play during a recording** (two sample tracks, two synths, a MIDI track), with Trk
  Audio on: the output is bit-identical to stock given the same steps by hand, MIDI too,
  and within -61 dB of normal playback. With Monitor off an input changes no output
  sample; with Monitor on it reaches the master as in stock.
- **Shift+Rec**, through the real Rec handler: with and without Pre Roll, Radio, Play
  during the count-in, a second Rec during the count-in. Trk Audio off: stock.
- **The buttons**, bound by the recorder's own binder and pressed through the stock
  dispatcher: the labels for each source, the toggles, pop-ups and the count-in left to
  stock, Play in the setup.
- **Trk Record:** the recording is Line In plus the output jacks' signal frame for frame
  from its first frame (Monitor on: the jacks' signal), a loud sum clips instead of
  wrapping, the output is unchanged; Mic is not mixed; the master's queues are handed
  back at the stop. With the metronome on, the recording after a count-in is the same
  as with it off.
- **`resample.bin`** through the screens' update hooks and a fake SD card: another
  project, a damaged file, no file I/O during a take, New Project then the recorder or
  an immediate Save As, Save As through the firmware's own save request.
- **Writes** outside the patch's RAM, and cost.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-rs8 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Stock firmware ignores the
`resample.bin` files in the project folders; delete them if you like.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
