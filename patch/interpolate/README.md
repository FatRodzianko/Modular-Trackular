# interpolate: CC and Macro ramps between steps

A patch for Polyend Tracker+ firmware 1.2.0. Stock firmware applies a CC FX (a–f) once,
at the start of its step: on a MIDI track it sends a MIDI CC, on a synth track (S1–S3)
it sets that Macro. So the value changes in jumps. With this patch it ramps smoothly
to the next step on that track with the same letter. CC A 0 on step 1 and CC A 127 on
step 17 becomes a 16-step sweep; the steps in between need no FX.
Installed on its own, Config shows the firmware version as `1.2.0-ip6`. Together with
all the other mods ([`patch/all`](../all/patch.json)) it shows `1.2.0-MTS`.

**Status:** tested on hardware (in a firmware with colorsong and humanize), MIDI and synths.

## Using it

1. Config → Project Settings → **FX Interpolation** has two Off/On settings, both Off
   by default, so existing projects play as before until you turn one on. Both are
   saved per project:
   - **MIDI CC**: ramps on MIDI tracks (M01–M16).
   - **Synths**: ramps on synth tracks (S1–S3), also when rendering.

   Turn on either one, or both.
2. On a MIDI track (instruments M01–M16) or a synth track (S1–S3), put CC FX (a–f)
   on the steps where the value should be, for example CC A 0 on step 1 and CC A 127
   on step 17.
3. Play. Between two steps with the same CC letter the value ramps from the first to
   the second.

## What it does

For each MIDI or synth track and CC letter (a–f), while the sequencer plays:

- The ramp runs from the last step at or before the current one with that CC letter
  (value *from*) to the next step after it with the same letter (value *to*), on the
  same track. Steps in between without that letter don't stop it.
- Value = from + (to − from) × position, rounded, where position goes from 0 at the
  start of the first step to 1 at the start of the second, in 48 micro-steps per step.
  The stock firmware still applies the value of each CC step at its start, so ramps
  end exactly on the written value.
- Both steps must use the same instrument: the same MIDI channel, or the same synth.
  On a MIDI channel the letter must be mapped to a CC number (not to program change
  or pressure). Steps on sample instruments are left alone.
- On a synth the ramp sets the Macro (CC A = Macro 1 … CC F = Macro 6) the same way
  the stock code sets it at a step start. It is set again at every new step of a ramp,
  in case a note in between resets it.
- Rendering (Render Selection, export) ramps synth tracks the same way, so a rendered
  sample matches what you heard. MIDI ramps aren't sent while rendering, like all
  MIDI.
- In Pattern mode the ramp wraps around the pattern: from the last CC of the pattern
  back to the first one. In Song mode, or with the next pattern queued, the value
  holds after the last CC of the pattern, because the next pattern isn't loaded yet.
- Preview (playing a selection on the Pattern screen) ramps the same way, so the
  previewed steps sound as they will in normal playback, and only the selected tracks
  send ramp CCs. When a looping preview jumps back to its first step, the ramp picks
  up at the value normal playback has there.
- In Performance mode CCs stay stepped, as in stock firmware.
- Muted tracks send nothing.

**MIDI bandwidth.** A ramp CC is sent only when the 7-bit value changes. Ramp CCs are
capped per audio block (128 samples, about 2.9 ms) and shared fairly between all
ramps: 1 per block (about 345 a second) when MIDI out includes the DIN jack, 4 per
block when it is USB only. While the jack's send buffer holds more than 128 bytes, no
ramp CCs go out at all, so notes and clock always get through. Many fast ramps on the
DIN jack therefore move in coarser steps than on USB.

**Synth Macros at their extremes.** Ramping a Macro all the way to a parameter's fully
open or fully closed value can cause unexpected results. For example, a Macro that
opens the filter cutoff fully switches the filter off, which gives a loud click. To
avoid this, don't ramp a Macro all the way to the end of its range: stop just short of
it.

## How it works

| Hook | Where | What |
|---|---|---|
| `mt_ip_seq_advance` | `bl seq_advance` at `0x600aa7f8` (the audio block, `app_audio_block`) | Runs the sequencer as before, then the ramp pass, in the same interrupt |
| `mt_ip_render_advance` | `bl seq_advance` at `0x600bc27a` (the render loop) | The same while rendering to a file |
| `mt_ip_settings_folder` | `bl cfg_menu_folder` at `0x60118b00` | Creates Project Settings with room for one more item |
| `mt_ip_last_item` | the menu constructor's last `bl cfg_menu_item` (`0x601193e6`) | Adds the FX Interpolation folder (MIDI CC and Synths Off/On items) to Project Settings, in nodes from the firmware's heap |
| `mt_ip_pattern_update` | Pattern screen vtable slot `0x600403dc` | Loads `interpolate.bin` when the project changes, saves it after a change, then runs the next update method |
| `mt_ip_config_update` | Config screen vtable slot `0x6003b5f8` | The same, while Config is open |
| `mt_ip_save_name` | `bl strncpy` at `0x60114eaa` in the save request `0x60114e34` (the new name; Save As only; chained) | Marks the Save As, so the settings go with the project to its new name and are written there |

All seven hooks are chained: each calls whatever the site pointed to before (the stock
function, or another patch's hook), so the patch combines with humanize, which hooks
the same menu and update sites.

The ramp pass runs once per audio block. Per track it keeps, for each CC letter, the
start step, the target step, both values, the CC number and channel, and the last value
sent. When a track moves to the next step, its ramps move along with it. The full
search (backwards and forwards through the pattern) runs again for at most 2 tracks per
block. With 16 MIDI tracks of 128 steps the patch runs under 24,000 instructions in its
worst block and about 7,000 on average (emulator counts; the stock sequencer's busiest
step start is about 9,100). CCs go out through the stock `midi_send_cc` (ITCM `0xacc`),
so the MIDI out setting applies and nothing is sent while rendering. Mutes are checked
with the stock code (`seq_track_muted_q`, ITCM `0x3994`). On synths the patch calls the stock
`synth_apply_fx_q` (ITCM `0x5070`) with the ramp value and the step's FX column, as the
stock code does at a step start; it converts the value to the Macro's range and stores
it as a pending parameter that the synth engine picks up when it renders. At most 8 of
these calls go out per block (about 700 instructions each in the emulator). The jack's send buffer is a
512-byte ring that the stock code never checks for overflow; the patch reads its write
and read positions before sending.

The two settings are 2 bytes of the patch's 1,504 bytes of RAM, which the patcher
assigns (the free OCRAM tail on its own; the DTCM gap above the heap in `patch/all`).
They are saved as `interpolate.bin` (12 bytes: `HIP1`, MIDI CC, synths, 2 zero bytes, a
checksum) in the project folder, read and written from the two screens' update methods,
like humanize's settings. A file without the synths byte set reads as synths Off. Save
As keeps the settings for the project's new name and writes them to its new folder (the
firmware copies only its own files), also on a new project's first save; a new project
starts with both Off. The
code is 3,041 bytes; the patcher places it after the stock image or after the patches
listed before it, and moves its 20 code-address words and 8 RAM-address words to
match (`prebuilt/interpolate.rel`). Firmware addresses are in
[docs/FINDINGS.md](../../docs/FINDINGS.md) (section 12).

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/interpolate/patch.json build/TrackerPlus_ip5.ptf
```

With all the other mods in one firmware (`patch/all`; to choose your own set of
patches, see "Choosing patches" in [docs/PATCHING.md](../../docs/PATCHING.md)):

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/all/patch.json build/TrackerPlus_MTE.ptf
```

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder).
`build.sh` writes `patch/interpolate/build/`, which the patcher then uses instead of
`prebuilt/`:

```bash
patch/interpolate/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_interpolate.py build/TrackerPlus_ip5.ptf
```

It runs the stock sequencer through the real audio block, with fake MIDI ports that log
every CC with the track's step and micro-step, and checks every CC against a separate
model of the rules above: rising and falling ramps (also across the pattern loop),
Song mode, Preview (once and looping), synth Macros (with the per-step re-apply, the
FX2 column, the per-block cap and the render loop), two letters on one track, a CC in
FX2, sample instruments, a channel change,
a program-change mapping, equal values, a muted track, stopping, the per-block caps,
sharing between ramps, and the jack's send buffer. It also checks that each setting works on its own, counts the patch's instructions
per block, and checks the FX Interpolation folder and its two items, and the settings file. The same test
runs on `TrackerPlus_MTE.ptf`.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-ip6 Build 1504` (or
`1.2.0-MTS Build 1504` for all mods).

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Patterns are unchanged (the
patch writes nothing into them), so they play as before, with stepped CCs. Stock
firmware ignores the `interpolate.bin` files; you can delete them.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
