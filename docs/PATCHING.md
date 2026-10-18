# Patching Tracker+ firmware 1.2.0

How to turn your own copy of the stock firmware into a modded one, install it, and get
back to stock. Nothing here contains or redistributes Polyend's firmware: you download
it from Polyend and the patcher modifies your copy locally.

**Use at your own risk.** The mods here were tested on one Tracker+, and
switching back to stock always worked. That is not a guarantee for your unit.

## 1. Get and check the stock firmware

1. Download **Tracker+ firmware 1.2.0** from Polyend's website (the Tracker+
   firmware downloads). The file is `TrackerPlus_1.2.0.ptf`.
2. Check it:

   | | |
   |---|---|
   | Size | 4,586,889 bytes |
   | SHA-256 | `d4616304f33d6d0b56516c658f34fa22517c1dd1630021c1ca89afca7b618ccd` |

   ```bash
   sha256sum TrackerPlus_1.2.0.ptf                      # Linux / Git Bash
   Get-FileHash TrackerPlus_1.2.0.ptf                   # Windows PowerShell
   shasum -a 256 TrackerPlus_1.2.0.ptf                  # macOS
   ```

   The patcher also checks the decoded image (SHA-256 `0d924ba9…1f3b`) and refuses
   anything else, so a different firmware version can't be patched by mistake.

## 2. Build the patched firmware

You only need **Python 3** (standard library only, no compiler; tested with 3.15). The repository ships
the compiled patch code in `patch/<name>/prebuilt/`, so this works out of the box:

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/colorsong/patch.json build/TrackerPlus_c10.ptf
```

Use `patch/humanize/patch.json` for humanize alone, or `patch/all/patch.json` for
colorsong and humanize in one firmware.

The patcher checks the stock image, checks the original bytes at every patched
address, writes the new `.ptf`, reads it back, and prints its SHA-256. The output
must be byte-identical to these builds:

| Patch | Output file | Version text in Config | On hardware | Output SHA-256 |
|---|---|---|---|---|
| colorsong | `TrackerPlus_c10.ptf` | `1.2.0-c10` | tested | `5d82977eadf595b6a491b90c793dcd1bfc644cec74920a98897d86444e28cb4a` |
| humanize | `TrackerPlus_hz3.ptf` | `1.2.0-hz3` | button tested, settings not yet | `f5b886cb22e1bb4a9104a99338cdbb625aa2d54fa12af6636505839e7cf00d97` |
| all (colorsong + humanize) | `TrackerPlus_MT3.ptf` | `1.2.0-MT3` | button tested, settings not yet | `6026fa4dbac11c0f83df68addc84c41baaccaf892cc053079a0621f340119eeb` |
| versiontest | `TrackerPlus_MT0.ptf` | `1.2.0-MT0` | tested | `f45510a17f8b9b120c0e699f239a737a145c3d57bbaf596046fb7a8bc643ffff` |

**Name the output `TrackerPlus<anything>.ptf`.** Config's firmware list only shows
files that start with `TrackerPlus` and end with `.ptf`. Keep names short: on the
original Tracker, names longer than 31 characters showed garbage.

### Building the patch code yourself (optional)

With the Arm GNU Toolchain installed (`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN`
set to its `bin` folder):

```bash
patch/colorsong/build.sh                                # writes patch/colorsong/build/
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/colorsong/patch.json build/TrackerPlus_c10.ptf
```

When `patch/<name>/build/` exists, the patcher uses it instead of `prebuilt/`. The
tested build used Arm GNU Toolchain 15.3.rel2. A different compiler may produce
different (but equivalent) code and therefore a different hash.

### Checking it in the emulator first (optional, recommended for your own changes)

```bash
python -m pip install unicorn capstone numpy
python tools/emu/check_version.py build/TrackerPlus_c10.ptf          # -> '1.2.0-c10'
python tools/emu/test_colorsong.py build/TrackerPlus_c10.ptf
python tools/emu/test_rescan.py build/TrackerPlus_c10.ptf "path/to/<project A>" "path/to/<project B>"
python tools/emu/test_humanize.py build/TrackerPlus_hz3.ptf
```

The test projects are copies of project folders from the SD card (`Projects/...`).

## 3. Install

1. **Back up your SD card** (the whole card, including the `Workspace` folder).
2. In the card's `/Firmware` folder, keep the stock `TrackerPlus_1.2.0.ptf` and add the
   patched file. Remove other old `.ptf` versions, so an emergency reflash (below) can
   only pick one of these two.
3. On the Tracker+: **Config → Firmware**, choose the patched file, confirm. The unit
   updates and reboots.
4. Config should now show the patch's version text, e.g. `1.2.0-c10 Build 1504`.

## 4. Back to stock

Config → Firmware → `TrackerPlus_1.2.0.ptf` → confirm. Config shows `1.2.0 Build 1504`
again.

colorsong saves each project's colors as `colorsong.bin` inside the project folder.
Stock firmware ignores these files, and you can delete them (deleting one also resets
that project's colors to song order).

## 5. If it doesn't boot

The bootloader is separate from the firmware and isn't touched by patches.

1. On a computer, remove the patched `.ptf` from `/Firmware`, so only the stock
   `TrackerPlus_1.2.0.ptf` is left.
2. Put the card back, power on, and press the **pinhole button next to the USB-C
   port** with a pin. The bootloader shows "Updating..." and installs the `.ptf` from
   `/Firmware`.

This worked on the test unit (with the unit on and the card in). If even that fails,
contact Polyend support.

If the unit only freezes in one place (e.g. a mod's screen) but boots, pull the power,
boot, and switch back to stock through Config.

## 6. Writing your own patch

See `patch/colorsong/` for a complete example and `docs/FINDINGS.md` for the firmware
details. A patch is:

- C/assembly code linked to run right after the stock image (`0x601bab80` onwards, in
  flash). No initialised RAM. State lives at fixed addresses that are known to be
  unused and **known to exist** (FINDINGS sections 2, 8 and 11).
- `patch.json`: the stock image hash, the code's base address, Thumb-2 `BL` sites to
  retarget (with their original bytes), and data words to replace (literal pools,
  vtable slots with `"thumb": true`).
- `tools/patch_ptf.py` checks everything, appends the code without gaps, and keeps the
  stock Intel HEX layout, so an unmodified image re-encodes to the exact stock file.
