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

Use `patch/humanize/patch.json` for Humanize alone, or `patch/all/patch.json` for
colorsong and Humanize in one firmware (see "Choosing patches" below for your own
mix).

The patcher checks the stock image, checks the original bytes at every patched
address, writes the new `.ptf`, reads it back, and prints its SHA-256. The output
must be byte-identical to these builds:

| Patch | Output file | Version text in Config | On hardware | Output SHA-256 |
|---|---|---|---|---|
| colorsong | `TrackerPlus_c10.ptf` | `1.2.0-c10` | tested | `5d82977eadf595b6a491b90c793dcd1bfc644cec74920a98897d86444e28cb4a` |
| humanize | `TrackerPlus_hz5.ptf` | `1.2.0-hz5` | tested | `58722f49ccc6cecc2ffa93ac4e649a9364f596e7464d93d44925793908096b54` |
| all (colorsong + humanize) | `TrackerPlus_MT5.ptf` | `1.2.0-MT5` | tested | `b1753f2988901eb388649ae4d15cb4baa7b533b1592b11b22ff64c60d31b3b8b` |
| ramdiag (diagnostic, on its own) | `TrackerPlus_RD1.ptf` | `1.2.0-RD1` | not yet | `ab238ea32b68cdea8b316a110eb238909ea4805dac0b1ca9d7d450c256472b67` |
| versiontest | `TrackerPlus_MT0.ptf` | `1.2.0-MT0` | tested | `f45510a17f8b9b120c0e699f239a737a145c3d57bbaf596046fb7a8bc643ffff` |

**Name the output `TrackerPlus<anything>.ptf`.** Config's firmware list only shows
files that start with `TrackerPlus` and end with `.ptf`. Keep names short: on the
original Tracker, names longer than 31 characters showed garbage.

### Choosing patches: one, several, or your own mix

Each patch can be installed on its own, or several can go into one firmware:

- **One patch:** use its own spec. `patch/colorsong/patch.json` gives colorsong only,
  `patch/humanize/patch.json` Humanize only.
- **Several patches:** use a bundle spec, which lists the patches to combine.
  `patch/all/patch.json` is the bundle that ships (colorsong + Humanize):

  ```json
  "include": ["../colorsong/patch.json", "../humanize/patch.json"],
  "version": "MT5",
  ```

To make your own mix, copy `patch/all/` to a new folder (for example `patch/mymix/`)
and edit its `patch.json`:

1. List the patches you want in `include`, as paths relative to the bundle's folder.
   They are placed in flash in that order. Your own patches can be listed too.
2. Change `name`, and set `version` to 1–3 characters that tell your build apart.
   Config shows `1.2.0-<version>`; longer text doesn't show.
3. Leave `stock_image_sha256` and `version_word` as they are.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/mymix/patch.json build/TrackerPlus_mymix.ptf
```

The patches in one firmware have to fit together:

- **Flash: handled for you.** The patcher puts the patches after the stock image in
  the order listed, each right after the one before (16-byte aligned), and moves
  each one's code to where it lands, using the relocation table its build makes
  (`prebuilt/<name>.rel`). Listing `["../humanize/patch.json",
  "../colorsong/patch.json"]` puts Humanize first (both orders are tested on
  hardware). A patch whose spec has a fixed
  `base` and no relocation table (like versiontest) stays exactly there, and must not
  overlap what comes before it.
- **Hook sites:** two patches can't hook the same call or data word. If they both need
  the same one, one patch has to call the other, which takes changes to their code.
- **RAM:** each patch keeps its state at fixed addresses in its source code. The free
  on-chip RAM is taken: colorsong uses `0x20205000`–`0x20207fbf` and Humanize
  `0x20207fc0`–`0x20207fff`. A new patch has to use the firmware's heap (as Humanize
  does for its menu entries) or another region that is proven unused and exists on
  the device (FINDINGS sections 8 and 11).
- **Firmware:** every patch must be for the same stock firmware
  (`stock_image_sha256`).

The patcher refuses to build when code ranges overlap, two patches hook the same site,
a patch is for another firmware, or an original byte doesn't match. It **can't**
detect RAM overlaps, or two patches that each work alone but disturb each other.

A new combination, or the same patches in another order, is a firmware nobody has
tested, and its SHA-256 isn't in the table above. Before installing it, run the
emulator tests of every patch it contains against the combined `.ptf` (below), and
keep the stock `.ptf` in `/Firmware`. `tools/test_layout.py` checks the placement
itself (below).

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
python tools/emu/test_humanize.py build/TrackerPlus_hz5.ptf
```

Each test works on any firmware that contains its patch, so for a bundle run every
contained patch's tests on the bundle's `.ptf`.

`tools/test_layout.py` checks the patcher's placement without the emulator. It builds
each patch alone, `patch/all`, and the same two patches in the other order, and
checks that each patch sits where it should, that exactly the words in its
relocation table moved, and that every hook points into it:

```bash
python tools/test_layout.py path/to/TrackerPlus_1.2.0.ptf build/layout
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

- C/assembly code, linked at any address after the stock image (`link.ld`). No
  initialised RAM. State lives at fixed addresses that are known to be unused and
  **known to exist** (FINDINGS sections 2, 8 and 11).
- A relocation table, so the patcher can move the code: `build.sh` links it twice,
  64 KB apart, and `tools/mkrel.py` lists the words that differ by exactly that
  much (pointers to the patch's own code and data). It refuses code it can't move
  this way. The colorsong and Humanize build scripts show how.
- `patch.json`: the stock image hash, the build outputs (`bin`, `sym`, `rel`, and
  their `prebuilt_` copies), Thumb-2 `BL` sites to retarget (with their original
  bytes), and data words to replace (literal pools, vtable slots with
  `"thumb": true`). Instead of a relocation table, a spec can give a fixed `base`.
- `tools/patch_ptf.py` checks everything, appends the code without gaps, and keeps the
  stock Intel HEX layout, so an unmodified image re-encodes to the exact stock file.
- To combine it with other patches, give it its own hook sites and RAM,
  and mark its version-text word with `"version": true` so a bundle can replace it
  (see "Choosing patches" in section 2).
