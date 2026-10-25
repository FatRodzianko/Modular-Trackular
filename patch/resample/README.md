# resample: live resampling

A patch for Polyend Tracker+ firmware 1.2.0. It keeps the tracker's own sound (sample
and synth instruments, with their delay and reverb) playing while the Sample Recorder
records, so you can play the pattern and record your input over it. The sound comes on
during a recording only when you ask for it: press **Play** after the recording has
started, or start the recording with **Shift+Rec**. Otherwise the recorder behaves as
stock. Installed on its own, Config shows the firmware version as `1.2.0-rs4`.
[docs/LIVE_RESAMPLING_PLAN.md](../../docs/LIVE_RESAMPLING_PLAN.md) has the
investigation and the plan.

**Status:** tested on hardware, on its own and in `patch/all` (`1.2.0-MR4`). It is also
in the web patcher.

## Using it

Open the Sample Recorder as usual and pick the source (Line In, Radio, Mic, USB).

- **Rec, then Play:** Rec starts the recording as in stock. Press Play while it records,
  and the pattern plays with all its instruments, at the project's mix levels, with
  delay and reverb. MIDI goes out as in stock.
- **Shift+Rec:** hold Shift and press Rec. With Pre Roll on, the count-in runs first and
  the pattern is silent; the recording and the pattern then start together. With Pre
  Roll off, or with the Radio source (which has no count-in), both start at once.
- **Play during the count-in** does the same as Shift+Rec: the pattern starts with the
  recording, not before it.
- **Rec during the count-in** lets the count-in run on, as in stock. Shift+Rec there
  also arms the pattern; a plain Rec keeps whatever the count-in started with.
- **Stopping the recording stops the pattern.** Notes still sounding end on their
  release, as after a normal stop; the delay and reverb stop with the recording, as in
  the stock recorder. The crop and save screens then work as in stock.
- **Monitor** works as in stock: with Monitor off you don't hear the input, with
  Monitor on you hear it at the master. The recording holds the input only, never the
  pattern.

**Feedback:** if the tracker's output is cabled back to its own input and Monitor is
on, the input reaches the output at full level, as in the stock recorder, so the loop
builds up when the cable path (output volume and input gain) is at unity or more.
Keep Monitor off for that setup: with it off, no input reaches the output.

**Limits:**

- **The longest recording is the free part of the sample memory.** Stock records into
  the start of the sample memory, over the project's loaded samples (that is why the
  stock recorder keeps the sample instruments silent). This patch records after the
  loaded samples instead, for every recording, so the samples stay intact. With nothing
  loaded nothing changes (about 713 s mono); with 30 MB of samples loaded it is about
  6 minutes mono or 3 minutes stereo. If less than 10 seconds would fit, the recording goes where stock
  puts it, and the pattern stays silent for that recording.
- A pattern started by external MIDI start or clock during a recording doesn't pass the
  Play button, so it stays silent, as in stock.
- Shift+Rec while the pattern is already playing silently in the recorder's setup (Play
  pressed there, which stock allows) plays it again from its start, so the recording
  and the pattern still start together.
- What is recorded is the selected input. The tracker's own output isn't a source; to
  record the pattern, cable the output to the input (with Monitor off).
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
| `mt_rs_rec_button` | the Rec handler's pool word `0x600f9000` (`0x600d0620`) | In the record setup: arm "start the pattern" if Shift is held (a plain Rec disarms only outside the count-in), then the stock handler; disarm if the press started nothing |
| `mt_rs_rec_started` | `bl 0x6010c890` at `0x600f90d8` (the record queues' start, reached from Rec and from the count-in's end alike) | If armed and the buffer was moved: open the gates and play the pattern |
| `mt_rs_play_button` | the Play handler's pool word `0x600fa4ac` (`0x600d07b4`) | During the count-in: arm instead of starting the pattern. During a moved recording, with the pattern stopped: open the gates, then the stock handler |
| `mt_rs_update` | Sample Recorder vtable slot `0x60042c54` (update `0x600fafe8`) | Once the recording has stopped: stop the pattern, close the gates. Disarm when nothing is counting in or recording |
| `mt_rs_is_recording` | the ITCM image's word `0x601acc28` (the literal of the thunk at ITCM `0x1f540`, loaded at boot) | `engine_block_update` asks it whether to return early: "no" while the patch has the gates open and the recorder records into the buffer it moved |

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
- **The record copy** runs in the audio interrupt before the per-block update, as in
  stock, so the recording is the input exactly.
- **The cost:** a block of an engine-on recording costs 0.7% more than normal playback
  (the copy); the patch's own share is 14 instructions per block.

No codec, DMA or ITCM code changes; the ITCM image changes in one word only. The code is
1,089 bytes. Its state is 16 bytes (a magic word, the moved / opened / armed flags, the
last buffer and size), which the patcher places in free RAM. The patcher moves the
code's 6 code-address words and 9 RAM-address words to match
(`prebuilt/resample.rel`). Nothing is saved: no settings, no file. Firmware addresses
are in [docs/FINDINGS.md](../../docs/FINDINGS.md) section 28.

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/resample/patch.json build/TrackerPlus_rs4.ptf
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
python tools/emu/test_resample.py build/TrackerPlus_rs4.ptf path/to/TrackerPlus_1.2.0.ptf
```

It runs the stock code with the patch in place, next to stock firmware (about 4
minutes):

- **Buffer:** placement in 15 cases (empty, mono, stereo, the 10 s limit, a full bank,
  fields holding garbage), read back through the recorder's own accessors; a recording
  leaves the loaded samples byte-identical, where stock writes over them.
- **Play during a recording** (two sample tracks, two synths, a MIDI track): the output
  is bit-identical to stock given the same steps by hand, MIDI too, and within -61 dB
  of normal playback. With Monitor off an input changes no output sample; with Monitor
  on it reaches the master as in stock.
- **Stop:** the pattern stops, the releases end, then silence, with the recorder's state
  as in stock.
- **Shift+Rec**, through the real Rec handler: with and without Pre Roll, Radio, Play
  during the count-in, a second Rec (plain or with Shift) during the count-in; the
  first note comes one micro-step after the record start, as after any Play. Plain Rec, a full bank, a count-in that ends without a recording,
  garbage RAM and a stale arm leave the pattern silent.
- **Stock where not asked for**, writes outside the patch's RAM, and cost.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-rs4 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. The patch saves nothing, so
there is nothing to clean up.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
