# humanize: less mechanical timing and levels

A patch for Polyend Tracker+ firmware 1.2.0. It adds a **Humanize** button to the
Pattern screen. Pressing it gives the notes in the selection small random changes in
timing (Micro-move, `m`) and level (Volume/Velocity, `V`), so programmed parts sound
less robotic. Installed on its own, Config shows the firmware version as `1.2.0-hz2`.
Together with colorsong ([`patch/all`](../all/patch.json)) it shows `1.2.0-MT2`.

**Status:** tested on hardware. The record-mode check is tested in the emulator only.

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

## What it does

For every step with a note (empty steps, note off, cut and fade are left alone):

- An existing **Volume/Velocity** FX moves by a random −5..+5 and stays within 1..100.
  A `V 0` (a deliberately silent note) stays 0.
- An existing **Micro-move** FX moves by a random −5..+5 and stays within 0..100.
- If the step has no Micro-move, one is added in the first free FX slot, with a random
  value of 0..10.
- If the step has no Volume/Velocity, one is then added in a free FX slot, with a
  random value of 45..55.
- A step with no free FX slot keeps its FX. With one free slot and neither FX, the slot
  gets the Micro-move.

Notes, instruments and all other FX never change. The amount is fixed: changes of up
to ±5, new Micro-move up to 10. Micro-move only delays a note (0..100 % of a step), so
humanized notes move later, never earlier.

The result is ordinary FX data in the pattern: it is saved with the project, and stock
firmware plays it the same way.

## How it works

The Pattern screen's bottom bar has three pages of 8 buttons, and buttons 2–6 of the
third page are unused. The patch binds button 2 and gives it a label:

| Hook | Where | What |
|---|---|---|
| `mt_hz_bind` | `bl btn_bind` at `0x600e29ba` (page 3 binding, button 0) | Binds button 0 as before, then, in record mode, button 2 to `mt_hz_button` |
| `mt_hz_label` | `bl disp_ctrl_set_text` at `0x600e5884` (page 3 labels) | Button 2's empty text becomes "Humanize" |
| `mt_hz_colors` | `bl disp_ctrl_set_colors` at `0x600e593c` | Track Name's colors; button 2 gets the active color set in record mode and the greyed-out one otherwise |
| `mt_hz_data` | `bl disp_ctrl_set_data` at `0x600e5966` | Track Name's data, also given to button 2 |

Like the stock editing buttons (Fill, Undo, Redo and the second page), Humanize only
works in record mode. Toggling record redraws the current page of the bar, which runs
the hooks again, so the button turns active or grey right away.

`mt_hz_button` uses the same stock steps as the Invert button: it pushes the pattern
onto the undo ring (so Undo reverts it), turns the editor's selection into the
sequencer's step/track rectangle, edits the steps, rebuilds the grid rows and marks the
pattern changed (so it gets saved). Random numbers come from the firmware's own
generator, the one its random FX use. The code is 590 bytes at `0x601bc000`, the first
4 KB flash sector after colorsong's code, so both fit in one firmware. It uses no RAM
of its own. Firmware addresses and structures are in
[docs/FINDINGS.md](../../docs/FINDINGS.md) (section 13).

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/humanize/patch.json build/TrackerPlus_hz2.ptf
```

Both mods in one firmware:

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/all/patch.json build/TrackerPlus_MT2.ptf
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
python tools/emu/test_humanize.py build/TrackerPlus_hz2.ptf
```

It runs the stock page-3 code with the patch in place and checks that button 2 is
bound and active in record mode, unbound and greyed out outside it, and that the
other buttons keep their stock bindings and labels. A press outside record mode
must change nothing. Then
it presses the button on a test pattern, through the stock selection, undo and
changed-flag code, and checks every rule above on every step: with and without a
selection, steps without notes, full and partly full FX slots, values near the limits
and `V 0`. It also checks that the undo ring holds the original pattern, and that 300
presses produce every value in each range. The same test runs on `TrackerPlus_MT2.ptf`.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-hz2 Build 1504` (or
`1.2.0-MT2 Build 1504` for both mods).

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Humanized patterns keep their
FX and play the same on stock firmware.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, put only the stock `TrackerPlus_1.2.0.ptf` in `/Firmware` (from a computer),
power on with the card in, and press the pinhole button next to the USB-C port. The
bootloader reinstalls the stock firmware.
