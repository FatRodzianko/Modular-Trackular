# copypattern: copy the current pattern to a new one

A patch for Polyend Tracker+ firmware 1.2.0. It adds a **Copy to New** button to the
Pattern screen. Pressing it copies the pattern you're on into the next empty pattern:
every track, every step and each track's length, including edits you haven't saved
yet. You stay on the pattern you're on, whether the Tracker+ is playing or stopped;
switch to the copy yourself when you want it. Installed on its own, Config shows the
firmware version as `1.2.0-cp4`.

**Status:** tested on hardware, on its own (third button) and with humanize (fourth
button, Humanize on the third). Not part of `patch/all`.

## Using it

1. On the Pattern screen, turn on record mode (the record button), as for the other
   editing buttons. Without it, Copy to New is greyed out and does nothing.
2. Press **More** twice. The third page of the bottom bar shows Pattern Name, Track
   Name and **Copy to New**: the third button on its own, or the fourth when Humanize is
   installed (Humanize keeps the third).
3. Press **Copy to New**. The message bar shows **Copied to pattern N**.

Example: you're on pattern 1, 128 steps long. Patterns 2 and 3 have notes in them;
pattern 4 has never been used, so it would start at 32 steps. Copy to New makes
pattern 4 a 128-step copy of pattern 1 and leaves you on pattern 1.

Other messages:

| Message | Meaning |
|---|---|
| No empty pattern left | Every other pattern (up to 255) has something in it, or is in the song. Nothing was written. |
| Pattern changed - try again | The pattern changed between the press and the copy (you switched, or in song mode the song moved on to the next pattern at that moment). Nothing was written. Press again. |
| Busy - try again | The firmware was busy with the card (loading or saving) for a long time. Nothing was written. |
| Copy failed - SD card | Writing the copy failed. The half-written file was removed, so the target stays empty. |

The copy is part of the project like any edited pattern: the project shows unsaved
changes, and saving the project saves the copy.

## Which pattern counts as empty

The search starts at the pattern after the current one, goes up to 255, then wraps
round to 1. A pattern is used, and skipped, when any of these is true:

- the Song screen shows a block for it (its track-usage data isn't empty);
- it is in the song's playlist, or queued to play next;
- its file has a step with a note (including note off, cut or fade), an instrument,
  or an FX in FX1 or FX2, on any of the 128 steps of any track, also past the pattern
  length;
- its file can't be read, is too short, or is from an older firmware version (not
  converted yet).

A pattern without a file (never edited) is empty, and so is one whose file only has
empty steps, whatever its length. An FX value without an FX type doesn't count.

The playlist rule goes a little beyond "has no steps": an empty pattern that the song
plays (as a silent bar, say) is left alone, so the copy never changes what the song
plays. It also avoids a timing problem: in song mode the firmware reads the next
pattern from the card shortly before it plays it, and a copy written into that
pattern at that moment could be overwritten later. A pattern's name (Pattern Name)
doesn't make it used; a named empty pattern keeps its name and gets the copy.

## How it works

The Pattern screen's bottom bar has three pages of 8 buttons, and buttons 2–6 of the
third page are unused in stock firmware (buttons count from 0 here). Humanize always
uses button 2. This patch uses button 2 when it's free and button 3 otherwise: it reads
where the label code's call for button 2's text (`0x600e5884`) goes, the stock
function or another patch's hook (Humanize's). Its hook sites are its own, none of
Humanize's, and its label hooks run after button 2's calls, so the two work alone,
together, and in either order.

| Hook | Where | What |
|---|---|---|
| `mt_cp_bind` | `bl btn_bind` at `0x600e29c6` (page 3 binding, button 1) | Binds button 1 as before, then, in record mode, its button (2 or 3) to `mt_cp_button` |
| `mt_cp_label` | `bl disp_ctrl_set_text` at `0x600e5890` (button 3's empty text) | "Copy to" on its button (on button 2 it replaces the empty text set just before) |
| `mt_cp_label2` | `bl disp_ctrl_set_ptr38` at `0x600e58f0` (button 3's empty second line) | "New", the same way |
| `mt_cp_colors` | `bl disp_ctrl_set_colors` at `0x600e592e` (Pattern Name's) | Pattern Name's colors; its button gets the active color set in record mode and the greyed-out one otherwise |
| `mt_cp_data` | `bl disp_ctrl_set_data` at `0x600e5958` (Pattern Name's) | Pattern Name's data, also given to its button |
| `mt_cp_pattern_update` | Pattern screen vtable slot `0x600403dc` (update `0x600e1f98`) | The search and the copy, then the stock update |

All six hooks are chained: each calls whatever the site pointed to before (the stock
function, or another patch's hook), so other patches can hook the same sites.
Humanize, interpolate, masterfx and swing chain the update slot too.

- **The press** (`mt_cp_button`) only notes the current pattern and starts the
  search. It touches no files.
- **The search** runs in the Pattern screen's update method, where the stock code does
  its own file work. Patterns ruled out by what's in RAM (track-usage data, playlist,
  queue) cost nothing. For each remaining one the patch asks the card whether
  `Workspace/patterns/pattern_NN.mtp` exists and, if it does, reads it in pieces of
  256 bytes and checks every step. That's at most one pattern file per update, so a
  project with many cleared patterns takes a few moments longer.
- **The copy**, in the same update that found the pattern empty (so nothing can
  change it in between): if you're still on the same pattern, the stock
  pattern writer (`0x600c1fb4`) writes the sequencer's current pattern buffer (the
  one the editor edits, so unsaved edits are in it) as the empty pattern's file, with
  the stock header and checksum. It writes the file in one go from that buffer, exactly
  as the stock pattern save does; it's the one write the patches here make in more
  than 256-byte pieces. Then the stock usage update (`0x600c3998`) fills in the Song
  screen's blocks for the new pattern, and the project is marked as having unsaved
  changes, as after a stock pattern save. Nothing in the sequencer changes, so playback
  and the song go on untouched.
- Each step waits while the firmware's file manager is busy (a pattern load, a project
  save or open), and gives up after about 2,000 updates. If another project is opened
  before the copy is done (only possible if you leave the Pattern screen right after
  the press), the copy is dropped without a message.
- **Undo** doesn't apply: the current pattern isn't changed, and the target was empty.
  To undo a copy, clear the new pattern.
- With colorsong, the new pattern gets its colors when it first appears in the song:
  colorsong reads it from the card on the next Song screen visit.

The code is 1,597 bytes. Its state is 48 bytes (the search position and the message
text), which the patcher places in free RAM: the OCRAM tail on its own, `sdram_top`
with all the other mods (the on-chip regions are full then). It's used only from the
Pattern screen, after the unit has started. The patcher moves the code's 15
code-address words and 4 RAM-address words to match (`prebuilt/copypattern.rel`).
Firmware addresses and the pattern file format are in
[docs/COPY_PATTERN_PLAN.md](../../docs/COPY_PATTERN_PLAN.md) and
[docs/FINDINGS.md](../../docs/FINDINGS.md) (sections 6, 10, 12, 13 and 25).

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/copypattern/patch.json build/TrackerPlus_cp4.ptf
```

To combine it with other mods, list it in a bundle spec (see "Choosing patches" in
[docs/PATCHING.md](../../docs/PATCHING.md)).

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder).
`build.sh` writes `patch/copypattern/build/`, which the patcher then uses instead of
`prebuilt/`:

```bash
patch/copypattern/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_copypattern.py build/TrackerPlus_cp4.ptf
```

It runs the stock code with the patch in place, with a fake SD card:

- **Button:** the page-3 binding and labels, in and outside record mode: button 2
  on its own, button 3 when humanize is in the build (Humanize's still on button 2),
  the other buttons unchanged.
- **Copy:** stopped and while playing in song mode (the sequencer running between
  updates): the file is what the stock writer writes for the pattern in RAM (unsaved
  edits included, not the older file), the stock loader reads it back identical, every
  track's length is kept (16, 32, 64, 128 and mixed), the Song screen data and unsaved
  flags are set, and the current pattern, both pattern buffers and the song position
  are unchanged.
- **Search:** each reason to skip a pattern, an empty file and a missing one,
  wrap-around past 255, and no empty pattern left.
- **Failures:** the pattern changing before the copy (by hand, and by song mode's own
  switch), another project opened meanwhile, the file manager busy, a write error.
- **File rules:** nothing on the press, at most one pattern file per update, reads of
  at most 256 bytes, the stock update running every time.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-cp4 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Copied patterns are ordinary
patterns and stay in your projects. The patch keeps no files of its own.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
