# humanize: less mechanical timing and levels

A patch for Polyend Tracker+ firmware 1.2.0. It adds a **Humanize** button to the
Pattern screen. Pressing it gives the notes in the selection small random changes in
timing (Micro-move, `m`) and level (Volume/Velocity, `V`), so programmed parts sound
less robotic. How much (separately for timing and level), and whether levels change
at all, is set per project under
Config → Project Settings → Humanize. Installed on its own, Config shows the firmware
version as `1.2.0-hz9`. Together with all the other mods
([`patch/all`](../all/patch.json)) it shows `1.2.0-SLA`.

**Status:** tested on hardware.

## Using it

1. On the Pattern screen, turn on record mode (the record button), as for the other
   editing buttons. Without it, Humanize is greyed out and does nothing.
2. Press **More** twice. The third page of the bottom bar shows Pattern Name, Track
   Name and **Humanize**.
3. Select the steps to humanize with the usual selection tools: part of a track,
   several tracks, or whole tracks. Without a selection, Humanize works on the
   cursor's whole track (up to the pattern length).
4. Press **Humanize**. Press it again for more variation. **Undo** (on the first page)
   reverts it, even for large selections.

## Settings

Config → Project Settings → **Humanize**:

| Setting | Values | Default | What it does |
|---|---|---|---|
| Timing range | 1–100 | 10 | How far Micro-move values move (T below) |
| Volume/velocity | On / Off | On | Off: Volume/Velocity FX are neither changed nor added |
| Volume range | 1–100 | 10 | How far Volume/Velocity values move (R below) |
| MIDI velocity | 1–100 | 50 | Center for a new V on MIDI and synth instruments |
| Sample volume | 1–100 | 50 | Center for a new V on sample instruments (1–48) |

Without a V FX, a note plays as if it had V 50 (for MIDI that is the note velocity),
which is why both defaults are 50. Raise MIDI velocity for velocity-sensitive gear.

Each project keeps its own settings, saved as `humanize.bin` in the project folder a
moment after you change one. A project without that file uses the defaults, and so
does a new project. Save As keeps the settings: the project goes on with them under its
new name, and they are written to its new folder (also on a new project's first save,
and on Save As over another project).

## What it does

With timing range T and volume range R, for every step with a note (empty steps, note
off, cut and fade are left alone):

- An existing **Micro-move** FX gets a random value in a window of T around it
  (T = 10: 45..55 around 50), within 0..100.
- An existing **Volume/Velocity** FX gets one in a window of R around it, within
  1..100. A `V 0` (a deliberately silent note) stays 0.
- If the step has no Micro-move, one is added in the first free FX slot, with a random
  value of 0..T.
- If the step has no Volume/Velocity, one is then added in a free FX slot, in a window
  of R around MIDI velocity (MIDI channels and synths) or Sample volume (samples).
- Near a limit the window slides to keep its width: with R = 10, a V of 98 or a center
  of 100 gives 90..100, and with T = 10 a Micro-move of 1 gives 0..10.
- A step with no free FX slot keeps its FX. With one free slot and neither FX, the slot
  gets the Micro-move.

Notes, instruments and all other FX never change. Micro-move only delays a note (0..100
% of a step), so humanized notes move later, never earlier. The result is ordinary FX
data in the pattern: it is saved with the project, and stock firmware plays it the
same way.

## How it works

The Pattern screen's bottom bar has three pages of 8 buttons, and buttons 2–6 of the
third page are unused. The Config menu is a tree of folder and item nodes built at
startup (`0x60118acc`); Project Settings is created with room for exactly its 10 items.

| Hook | Where | What |
|---|---|---|
| `mt_hz_bind` | `bl btn_bind` at `0x600e29ba` (page 3 binding, button 0) | Binds button 0 as before, then, in record mode, button 2 to `mt_hz_button` |
| `mt_hz_label` | `bl disp_ctrl_set_text` at `0x600e5884` (page 3 labels) | Button 2's empty text becomes "Humanize" |
| `mt_hz_colors` | `bl disp_ctrl_set_colors` at `0x600e593c` | Track Name's colors; button 2 gets the active color set in record mode and the greyed-out one otherwise |
| `mt_hz_data` | `bl disp_ctrl_set_data` at `0x600e5966` | Track Name's data, also given to button 2 |
| `mt_hz_settings_folder` | `bl cfg_menu_folder` at `0x60118b00` | Creates Project Settings with room for 11 items instead of 10 |
| `mt_hz_last_item` | the menu constructor's last `bl cfg_menu_item` (`0x601193e6`) | Adds the Humanize folder and its 5 items to Project Settings, as stock number and Off/On items (the Off/On item with an apply callback, which the menu calls unchecked) |
| `mt_hz_pattern_update` | Pattern screen vtable slot `0x600403dc` (update `0x600e1f98`) | Loads `humanize.bin` when the project changes, saves it after a change, then runs the stock update |
| `mt_hz_config_update` | Config screen vtable slot `0x6003b5f8` (update `0x600c6474`) | The same, while Config is open |
| `mt_hz_save_name` | `bl strncpy` at `0x60114eaa` in the save request `0x60114e34` (the new name; Save As only; chained) | Marks the Save As, so the settings go with the project to its new name and are written there |

The menu, update-method and Save As hooks are chained: each calls whatever the site pointed to
before (the stock function, or another patch's hook, such as interpolate's), so other
patches can hook the same sites.

Like the stock editing buttons (Fill, Undo, Redo and the second page), Humanize only
works in record mode. Toggling record redraws the current page of the bar, which runs
the hooks again, so the button turns active or grey right away.

`mt_hz_button` uses the same stock steps as the Invert button: it pushes the pattern
onto the undo ring (so Undo reverts it), turns the editor's selection into the
sequencer's step/track rectangle, edits the steps, rebuilds the grid rows and marks the
pattern changed (so it gets saved). Random numbers come from the firmware's own
generator, the one its random FX use.

The settings take 32 bytes of RAM, which the patcher assigns (in the free OCRAM tail:
`0x20205000` on its own, right after colorsong's table in `patch/all`). The menu
items edit them directly; the stock number editor stores a list index, so 1–100 is
kept as 0–99 and shown + 1. The settings file is read and written from the two
screens' update methods, where the stock code does its own file work, in the same way
colorsong saves its colors: one 16-byte file (`HHZ2`, the 5 values, 3 zero bytes, a
checksum), written only when a value has changed, and only while the file manager is
idle. A 12-byte `HHZ1` file, with a single range, is read too: both ranges get its
value. The firmware's Save As copies only its own files, so the patch writes
`humanize.bin` to the new folder itself; a new project ("New Project", no path) writes
nothing until it has a folder. The menu nodes come from the
firmware's heap. The code is 2,488 bytes. The patcher places it after the stock image
or after the patches listed before it, and moves its 23 code-address words and 11
RAM-address words to match (`prebuilt/humanize.rel`). The settings are used by
the Config menu constructor before `main`, so the spec has `"ram_startup": true` and
never gets external SDRAM. Firmware addresses and
structures are in [docs/FINDINGS.md](../../docs/FINDINGS.md) (sections 13 and 14).

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/humanize/patch.json build/TrackerPlus_hz8.ptf
```

With all the other mods in one firmware (`patch/all`; to choose your own set of patches, see
"Choosing patches" in [docs/PATCHING.md](../../docs/PATCHING.md)):

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/all/patch.json build/TrackerPlus_MTE.ptf
```

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder).
`build.sh` writes `patch/humanize/build/`, which the patcher then uses instead of
`prebuilt/`:

```bash
patch/humanize/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_humanize.py build/TrackerPlus_hz8.ptf
```

It runs the stock code with the patch in place:

- **Button:** the page-3 binding and labels, in and outside record mode.
- **Edit:** presses on test patterns through the stock selection, undo and
  changed-flag code, checking every rule above on every step (with and without a
  selection, full and partly full FX slots, values near the limits, `V 0`), and that
  every value of each window shows up over 300 presses, with the default settings and
  with others (both ranges 20, one range 20 with the other 4, Volume/velocity off,
  MIDI velocity 100, Sample volume 30).
- **Config menu:** the stock menu constructor, then the stock select, back, value
  display, editor and Apply code on the Humanize folder and each of its items.
- **Settings file:** the two update hooks with a fake SD card: defaults without a file,
  one write a while after a change, per-project values, a damaged file, a
  single-range `HHZ1` file; Save As and new projects through the firmware's own save
  request (`tools/emu/project_save.py`).

The same test runs on `TrackerPlus_MTE.ptf`.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-hz9 Build 1504` (or
`1.2.0-SLA Build 1504` for all mods).

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Humanized patterns keep their
FX and play the same on stock firmware. Stock firmware ignores the `humanize.bin`
files; you can delete them.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
