# swing: Global Swing

A patch for Polyend Tracker+ firmware 1.2.0. Stock firmware sets swing only with the
Swing FX (I, 25 to 75, 50 is no swing). That has two catches:

- **Swing carries over.** Starting playback doesn't reset swing, so a take starts with
  whatever swing played last, from this pattern, another pattern or another take.
- **A muted Swing FX is skipped.** A Swing FX on a muted track never runs, so muting the
  track that carries it leaves the swing from before.

With Global Swing on, every take and every pattern starts with the project's own Swing
amount, the same every time. Installed on its own, Config shows the firmware version as
`1.2.0-sw2`.

**Status:** tested on hardware, on its own and together with colorsong, humanize,
interpolate and masterfx.

## Using it

Config → Project Settings → **Global Swing**:

| Setting | What it does |
|---|---|
| Global swing | Off (the default): swing works as in stock firmware. On: the Swing amount below is used |
| Swing amount | 25 to 75, as the Swing FX (the default is 50, no swing). Above 50, every second step comes later (a "shuffle"); below 50, earlier |

Both are saved per project (in `swing.bin` in the project's folder), a moment after you
change them. A project without that file, and every project in stock firmware, has
Global swing Off. Save As keeps them: the project goes on with them under its new name,
and they are written to its new folder (also on a new project's first save, and on Save
As over another project). A new project starts with the defaults.

With Global swing On:

- **Every take** starts at the Swing amount, on the long step of the pair, whatever
  played before.
- **Every pattern start** goes back to the Swing amount: each new pass of a looping
  pattern, and each new pattern in song mode.
- **A Swing FX still works** for the rest of its pattern: put one on a step to swing a
  pattern, or part of one, differently. At the next pattern start the swing is back at
  the Swing amount. A Swing FX on a muted track does nothing.
- **Changing the settings while playing** takes effect at once. Turning Global swing
  Off leaves no swing until a Swing FX sets some, as in stock firmware.
- **Rendering to a file** swings the same way.
- **Performance mode**: a take still starts at the Swing amount, but as its tracks can
  play backwards or at random, the swing isn't reset at pattern starts there.

## How it works

The sequencer keeps swing in three fields of its object (`0x20017274`): on (`+0x60`),
which step of the pair it is on (`+0x61`, toggled when track 16 ends a step), and the
amount (`+0x64`, a float). The tick period then alternates (s + 50) and (150 − s) per
step. The Swing FX handler (ITCM `0x2f10`) only turns swing on and stores the amount;
nothing turns it off or resets the pair when playback starts.

| Hook | Where | What |
|---|---|---|
| `mt_sw_seq_advance` | `bl seq_advance` in `app_audio_block` (`0x600aa7f8`, chained) | Before the sequencer runs, once per audio block: if playback has just started, sets swing to the Swing amount from the long step; if Global swing has just been turned on or the amount changed, sets the amount; if it has just been turned off, turns swing off |
| `mt_sw_render_advance` | `bl seq_advance` in the render loop (`0x600bc27a`, chained) | The same while rendering |
| `mt_sw_advance_step` | the word at `0x601acf00` (chained): the ITCM image's jump to `seq_advance_step` (`0x600ad011`), copied to ITCM `0x1f81c` at boot | After `seq_advance_step(seq, track)` ends a step: when track 16 has wrapped back to the pattern start (a new pass, or the next pattern in song mode), sets swing to the Swing amount from the long step, before the next step's FX run |
| `mt_sw_settings_folder` | the Config menu constructor's Project Settings folder (`0x60118b00`, chained) | Room for one more entry |
| `mt_sw_last_item` | the Config menu constructor's last `bl cfg_menu_item` (`0x601193e6`, chained) | Adds the Global Swing folder with its two items |
| `mt_sw_pattern_update` | Pattern screen vtable slot `0x600403dc` (chained) | Reads and writes `swing.bin` |
| `mt_sw_config_update` | Config screen vtable slot `0x6003b5f8` (chained) | Reads and writes `swing.bin` |
| `mt_sw_save_name` | `bl strncpy` at `0x60114eaa` in the save request `0x60114e34` (the new name; Save As only; chained) | Marks the Save As, so the settings go with the project to its new name and are written there |

- **`swing.bin`:** 12 bytes in the project's folder: `HSW1`, Global swing (0 or 1), the
  Swing amount − 25 (0 to 50), two zero bytes and a checksum. A missing or damaged file
  means the defaults (Off, 50). It is read and written only while the file manager is
  idle. A project with no folder yet (the new project "New Project", which has no path)
  writes no file; it gets one when it is first saved. The firmware's Save As copies only
  its own files, so the patch writes `swing.bin` to the new folder itself.
- **Memory:** 32 bytes of RAM (the settings and what the audio side last saw), on-chip;
  1,737 bytes of code.
- **CPU:** a few instructions per audio block and per step end.

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/swing/patch.json build/TrackerPlus_sw1.ptf
```

To combine it with other patches, list `../swing/patch.json` in a bundle (see
"Choosing patches" in [docs/PATCHING.md](../../docs/PATCHING.md)). Its hooks are all
chained, so it combines with all the other patches here.

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder):

```bash
patch/swing/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_swing.py build/TrackerPlus_sw1.ptf path/to/TrackerPlus_1.2.0.ptf
```

It runs the sequencer in time, as the audio engine does (the patched `seq_advance` call,
0.5 ms at a time), with a MIDI note on every step marking when each step starts, and
checks:

- **Off:** the same step times as stock firmware, with swing left over from before.
- **On:** a take starts on the long step at the Swing amount whatever swing was left
  over; a Swing FX changes it until the pattern starts again, and the next pass swings
  at the Swing amount again; a Swing FX on a muted track changes nothing; in song mode
  the next pattern starts at the Swing amount; changing the amount, or turning Global
  swing off, while playing; the render loop's call.
- **Menu:** the stock menu constructor builds the folder and its two items (an Off/On
  item, and a number item showing 25 to 75), and the stock Apply stores the values.
- **File:** `swing.bin` through the screens' update hooks with a fake SD card:
  defaults, one write a while after a change, per-project values, a damaged file, a
  project without a name; Save As and new projects through the firmware's own save
  request (`tools/emu/project_save.py`).

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-sw2 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Projects play with the Swing FX
only again; their `swing.bin` files stay in their folders and do nothing in stock
firmware (delete them if you like).

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
