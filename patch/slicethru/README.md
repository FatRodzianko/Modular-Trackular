# slicethru: Beat Slice (Play Through)

A patch for Polyend Tracker+ firmware 1.2.0. It adds a play mode, **Beat Slice Thru**,
to the Sample Playback screen's Play Mode list, right after Beat Slice. You slice a
sample exactly as in Beat Slice (Slice, Adjust, Add, Remove, Auto, Evenly), and the
pads and notes pick slices the same way. The difference is playback: a note starts at
its slice and plays on to the end of the sample, instead of stopping where the next
slice starts. With **Loop** on, the note then loops the whole sample until something
stops it. Installed on its own, Config shows the firmware version as `1.2.0-bt5`.
[docs/BEAT_SLICE_THROUGH_PLAN.md](../../docs/BEAT_SLICE_THROUGH_PLAN.md) and
[docs/SLICETHRU_LOOP_PLAN.md](../../docs/SLICETHRU_LOOP_PLAN.md) have the
investigations and the plans.

**Status:** tested on hardware, on its own (`1.2.0-bt5`) and in `patch/all`
(`1.2.0-MT5`), Loop included, as are Save As and a new project's first save. A project
saved with Beat Slice Thru instruments plays them as Beat Slice in stock firmware, and
as Beat Slice Thru again back on this firmware.

## Using it

1. Load a sample into an instrument and open **Sample Playback**.
2. Choose **Beat Slice Thru** in the Play Mode list (the 7th and 8th screen buttons, the
   arrows or the encoder, as for the other modes). It sits between Beat Slice and
   Wavetable.
3. Slice the sample as in Beat Slice. The pads and notes pick slices exactly as in
   Beat Slice: pad 1 (and its note) plays from slice 1, pad 2 from slice 2, and so on.

Each note plays from its slice to the end of the sample, until one of these stops it:

- the track's next note: each track plays one sample note at a time, so a new note
  ends the previous one, as in every play mode;
- an OFF step, or the end of a gate (`q`): the amp envelope's release, or at once when
  the amp envelope is off;
- a CUT or FADE step, stopping playback, or muting the track;
- the end of the sample.

So with a note on every step, Beat Slice Thru sounds like Beat Slice. It makes a
difference when there's room after a note: a note on step 1 and the next on step 9 plays
on from its slice for eight steps.

### Loop

On a Beat Slice Thru instrument, press **More** in Sample Playback. The 4th button
reads **Loop**, with **Off** or **On** under it; a press switches it. With Loop on, a
note plays from its slice to the end of the sample, then jumps to the start of the
sample (its first frame) and plays the whole sample again, over and over, until the
next note, OFF, the end of a gate, CUT, FADE, stopping playback or muting the track
stops it. A held pad loops until you let go.

- The loop jumps straight from the last frame to the first, as stock's Forward Loop
  does at its loop points; there's no crossfade. A sample that doesn't loop cleanly
  clicks there.
- With reverse (`r`) the note plays once, from the sample end back to its slice, as
  with Loop off.
- Switching Loop off while a note plays lets that pass finish; the note then stops at
  the end of the sample.
- Loop belongs to Beat Slice Thru: switching the instrument to another mode turns its
  Loop off. Plain Beat Slice and Slice instruments show no Loop button.

Other things work as in Beat Slice:

- **The Slice FX (`S`)** picks the slice to start from.
- **Reverse (`r`)** plays from the end of the sample back to the slice's start.
- Tune, finetune, volume, filter, envelopes and the other instrument settings are
  the same. The pitch doesn't follow the note, as in Beat Slice.
- The Instrument End point isn't used, as in Beat Slice: the last slice, and every
  slice here, runs to the end of the sample.

The choices (Beat Slice Thru and Loop) are saved per project, in `slicethru.bin` in
the project's folder, a moment after you change them. A project without that file, and every project in stock firmware,
has no Beat Slice Thru instruments: there the instruments play Beat Slice, with their
slices intact.

**Limits:**

- The instrument itself is stored as a Beat Slice instrument. An instrument (`.pti`)
  saved and loaded into another project, or copied to another slot, plays Beat
  Slice there. Choose Beat Slice Thru again.
- `slicethru.bin` belongs to the project folder, and the firmware's Save As copies
  only its own files (checked on hardware). So the patch writes the choice into the new
  folder itself: after Save As, and after a new project's first save, the project keeps
  its Beat Slice Thru instruments.
- If a slot gets another instrument that is also Beat Slice (for example a `.pti`
  loaded over it) while it was Beat Slice Thru, it shows as Beat Slice Thru. The list
  shows which; switch it back if you like. Loading any instrument that isn't Beat
  Slice clears the choice.

## How it works

The firmware keeps the play mode in each instrument (one byte, `+0x3c`; Beat Slice is
5). Beat Slice code all over the firmware (the slice editor, the pads, the pad
lights, live recording, the pattern screen) tests "mode == 5" directly, 31 places in
26 functions. So this patch leaves the mode at 5 and keeps a play-through flag per
instrument next to it. Every part of the firmware then sees an ordinary Beat Slice
instrument, and stock firmware does too. Only the slice's end point and the Play Mode
list change.

Every Beat Slice note-on, from the sequencer, the pads or the restart after a fade,
goes through the play-mode dispatcher (`0x60032abc`). It calls the Beat Slice note-on
(`0x60032b50`), which turns the slice's start and end points (0..65535 of the
sample) into frames, the end at `0x60032c62`, and works out the rest from them: the
frames to play, the reverse start, and the copy a stereo sample's right voice takes.

| Hook | Where | What |
|---|---|---|
| `mt_bt_bs_note_on` | `bl 0x60032b50` at `0x60032b3a` (the dispatcher's Beat Slice case) | Marks the call as playing through if its instrument's flag is set and it is a Beat Slice instrument; the mark is saved and restored around the call, so a note-on from the audio interrupt that cuts into a pad preview keeps its own |
| `mt_bt_end_frames` | `bl 0x60100694` at `0x60032c62` (the end point to frames) | While the call plays through: the end point is the sample end (`0xffff`) |
| `mt_bt_list_data` | `bl disp_ctrl_set_data` at `0x600f7cac` (the Play Mode list's data, in `0x600f7c54`) | 9 names (the 8 stock ones and Beat Slice Thru) and the instrument's row |
| `mt_bt_encoder`, `mt_bt_plus`, `mt_bt_minus` | the encoder, +1 and −1 callback words in the Sample Playback screen's callback pool (`0x600f5178`, `0x600f5144`, `0x600f5140`) | With the Play Mode list focused: a step of the 9-row list; otherwise the stock callback |
| `mt_bt_button_plus`, `mt_bt_button_minus` | `bl 0x600f5b98` at `0x600f5e80` and `0x600f5e8a` (the 8th and 7th screen buttons' step) | A step of the 9-row list |
| `mt_bt_playback_update` | Sample Playback screen vtable slot `0x60042698` (update `0x600f6070`) | `slicethru.bin` load and save; the list redrawn after a load |
| `mt_bt_pattern_update` | Pattern screen vtable slot `0x600403dc` (update `0x600e1f98`) | `slicethru.bin` load and save |
| `mt_bt_save_name` | `bl strncpy` at `0x60114eaa` in the save request `0x60114e34` (the new name; Save As only) | Marks the Save As, so the flags go with the project to its new name |
| `mt_bt_wrap` | the 4 bytes at `0x60035522` in the forward 1-shot render `0x600353fc`: two instructions, not a BL (`"replaces": "insns"`), run only when a voice reaches its end | A Beat Slice Thru note with Loop on: its position goes on from the sample's first frame. Otherwise the two stock instructions (the stop) |
| `mt_bt_page2_label` | `bl disp_ctrl_set_text` at `0x600f820a` (the 4th button on page 2 of Slice and Beat Slice) | On a Beat Slice Thru instrument: "Loop", and "On" or "Off" under it |
| `mt_bt_button4` | the 4th button's callback word `0x600f5158` (`0x600f4b55`) | Page 2 of a Beat Slice Thru instrument, no pop-up: a press toggles Loop and relabels the button; then the stock callback (which does nothing there) |

The hooks are chained: each calls whatever the site pointed to before (the stock
function, or another patch's hook), so other patches can hook the same sites. The
Pattern screen's update slot is shared with most of the other mods. The exception is
`mt_bt_wrap`: its site isn't a call, so nothing can chain it, and the patcher refuses a
second patch there.

- **The loop** happens where the render stops a voice at its end. Instead of the stop
  position, the hook sets the position back by the frames played plus the start
  frame, which is the same frame counted from the sample's start, and the render goes
  on. It loops only when all of these hold:
  - the voice's play mode is Beat Slice and its instrument (the voice keeps both) is a
    Beat Slice instrument with Thru and Loop on;
  - the voice's end is the sample's end, so a note started before Thru was chosen still
    stops at its slice.

  It costs about 20 instructions once per pass; every other block renders exactly as
  stock.

- **A step of the list** moves the row by the step (the encoder's at most 4 rows, as
  in stock), sets or clears the flag, and calls the stock mode-change handler
  (`0x600f5b98`) with the mode's step. Between Beat Slice and Beat Slice Thru that is
  a step of 0, which changes nothing in the instrument. A step that doesn't start or
  end on Beat Slice Thru goes to the stock handler unchanged.
- **The file** is 24 bytes: `BTS2`, the Thru and Loop masks (48 bits each, one per
  instrument), and a checksum. A 16-byte `BTS1` file from bt4 and older still loads,
  with every Loop off; those builds ignore a `BTS2` file (no Beat Slice Thru there). A
  Loop bit without its Thru bit is ignored. It is read and written only from the two screens' update methods, where
  the stock code does its own file work, and only while the firmware's file manager
  is idle. On another project the flags are cleared at once, then read from that
  project's file. A Save As keeps them for the project's new name and writes them
  there; the save request's name copy (`0x60114eaa`, Save As only) marks it, from any
  screen. A new project ("New Project" with no path) has no folder: nothing is
  written until its first save. A
  flag whose instrument isn't an active Beat Slice instrument any more is cleared
  (its Loop too), only while the file manager is idle (never in the middle of a
  project load).
- **The cost:** a Beat Slice note-on runs 54 instructions more than stock; rendering
  (per 128-frame block) is unchanged. Voices play longer, but never more of them than
  stock: one per sample track (two for a stereo sample), as a 1-Shot sample can.

The code is 2,597 bytes. Its state is 48 bytes (the Thru and Loop flags, their saved
copies, the project they belong to), which the patcher places in free RAM: the OCRAM
tail on its own, `sdram_top` with all the other mods. The 9-name table is in flash. The
patcher moves the code's 19 code-address words and 13 RAM-address words to match
(`prebuilt/slicethru.rel`). Firmware addresses are in
[docs/FINDINGS.md](../../docs/FINDINGS.md) section 27.

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/slicethru/patch.json build/TrackerPlus_bt5.ptf
```

To combine it with other mods, list it in a bundle spec (see "Choosing patches" in
[docs/PATCHING.md](../../docs/PATCHING.md)).

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder).
`build.sh` writes `patch/slicethru/build/`, which the patcher then uses instead of
`prebuilt/`:

```bash
patch/slicethru/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_slicethru.py build/TrackerPlus_bt5.ptf path/to/TrackerPlus_1.2.0.ptf
```

It runs the stock code with the patch in place, next to stock firmware:

- **Voice:**
  - with no flag set, every note-on in every play mode leaves the voice exactly as
    stock does;
  - with a flag, each note starts where stock starts it and ends at the sample end,
    for 1, 4 and 48 slices and none;
  - the voice renders to the end at the same cost per block;
  - the Slice FX, reverse, tune, stereo samples and a note-on inside another all
    behave correctly;
  - the patch writes nothing outside its own RAM.
- **Loop** (the render the mixer calls): notes from slices 1, 2 and 4 play to the end,
  then the sample from its first frame, sample-exact for three passes, also at double
  and triple pitch; stereo voices in step. No loop with Thru or Loop off, on another
  instrument, a 1-Shot instrument, a note started before the flags, or reverse. Loop
  off during a pass stops at its end. OFF, CUT and FADE (amp envelope on and off) end
  a looping note; a held pad loops and its release stops it. A block that doesn't
  reach the end costs what stock's does.
- **Screen** (the real Sample Playback screen): the list draws 9 names with the right
  row highlighted. Every input from every row moves to the right mode; steps that
  don't involve Beat Slice Thru do what stock does. With another control focused, the
  inputs behave as stock. Page 2 of a Beat Slice Thru instrument shows Loop On/Off and
  a press toggles it; Beat Slice, Slice, page 1 and a pop-up behave as stock.
- **File** (a fake SD card): written once after a change (`BTS2`, Loop included), read
  on a project change, a `BTS1` file loading with Loop off,
  damaged files ignored, nothing read or written while the file manager is busy, stale
  flags cleared; a new project as the firmware makes it writes nothing; Save As through
  the firmware's own save request (a new project's first save, and from a saved
  project) keeps the flags and writes them to the new folder; a plain save; a project
  opened afterwards gets its own file.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-bt5 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Beat Slice Thru instruments
play Beat Slice in stock firmware, with their slices intact; `slicethru.bin` files stay
in your project folders and do nothing there (delete them if you like).

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
