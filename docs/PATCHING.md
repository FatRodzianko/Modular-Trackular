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
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/colorsong/patch.json build/TrackerPlus_c11.ptf
```

Use `patch/humanize/patch.json` for Humanize alone, or `patch/all/patch.json` for
colorsong and Humanize in one firmware (see "Choosing patches" below for your own
mix).

The patcher checks the stock image, checks the original bytes at every patched
address, writes the new `.ptf`, reads it back, and prints its SHA-256. The output
must be byte-identical to these builds:

| Patch | Output file | Version text in Config | On hardware | Output SHA-256 |
|---|---|---|---|---|
| colorsong | `TrackerPlus_c11.ptf` | `1.2.0-c11` | tested | `cc9119abe627d994ba6010bfe0db59129e3f494611dbc8c1e5c68fcf0653ebc6` |
| humanize | `TrackerPlus_hz7.ptf` | `1.2.0-hz7` | tested | `d74c3fe62ed3feb16bca6ca046b9ed5ea62f85de61a74f02bc36bd82b343b17c` |
| all (colorsong + humanize) | `TrackerPlus_MT7.ptf` | `1.2.0-MT7` | tested | `0397f2ba91b0f6e41cc56b068c64c83b79e48898dcf702cfd7f25c6035ad6b4e` |
| ramdiag (diagnostic, on its own) | `TrackerPlus_RD2.ptf` | `1.2.0-RD2` | tested | `84a33f1858bdd3c908eeea62227d84f68fe450dc4e3e21fb21077292fef1b82e` |
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
  "version": "MT7",
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
  "../colorsong/patch.json"]` puts Humanize first (`tools/test_layout.py` checks
  both orders). A patch whose spec has a fixed
  `base` and no relocation table (like versiontest) stays exactly there, and must not
  overlap what comes before it.
- **Hook sites:** two patches can't hook the same call or data word. If they both need
  the same one, one patch has to call the other, which takes changes to their code.
- **RAM: handled for you.** Each patch's state variables are one block (its
  `.mt_ram` section). The patcher hands the blocks out from the free RAM regions
  below, in the order the patches are listed: each block goes into the first region
  (in the patch's order) that still has room, 16-byte aligned, and the patch's code
  is moved to match. It prints where each block went and writes it, with the code
  ranges, to `<output>.map.json` next to the `.ptf`.

  | Region | Addresses | Size | Notes |
  |---|---|---|---|
  | `ocram_tail` | `0x20205000`–`0x20207fff` | 12,288 B | on-chip |
  | `dtcm_above_heap` | `0x20051c70`–`0x20052fff` | 5,008 B | on-chip, below the stack |
  | `sdram_top` | `0x83ff9bf0`–`0x83ffffff` | 25,616 B | external SDRAM; not before `main` |
  | `dtcm_after_audio` | `0x200094c0`–`0x200097ff` | 832 B | on-chip |
  | `itcm_tail` | `0x0001fb40`–`0x0001ffff` | 1,216 B | only when a spec names it; aligned accesses only |

  The default order is the first four, top to bottom. colorsong (12,208 B) and
  Humanize (32 B) both fit in `ocram_tail`. If a patch's block fits nowhere, the
  patcher refuses to build.
- **Firmware:** every patch must be for the same stock firmware
  (`stock_image_sha256`).

The patcher refuses to build when code ranges overlap, two patches hook the same site,
a patch is for another firmware, an original byte doesn't match, the RAM runs out, or
a bundle includes a patch marked `standalone` (the ramdiag diagnostic). It **can't**
detect two patches that each work alone but disturb each other.

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
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/colorsong/patch.json build/TrackerPlus_c11.ptf
```

When `patch/<name>/build/` exists, the patcher uses it instead of `prebuilt/`. The
tested build used Arm GNU Toolchain 15.3.rel2. A different compiler may produce
different (but equivalent) code and therefore a different hash.

### Checking it in the emulator first (optional, recommended for your own changes)

```bash
python -m pip install unicorn capstone numpy
python tools/emu/check_version.py build/TrackerPlus_c11.ptf          # -> '1.2.0-c11'
python tools/emu/test_colorsong.py build/TrackerPlus_c11.ptf
python tools/emu/test_rescan.py build/TrackerPlus_c11.ptf "path/to/<project A>" "path/to/<project B>"
python tools/emu/test_humanize.py build/TrackerPlus_hz7.ptf
```

Each test works on any firmware that contains its patch, so for a bundle run every
contained patch's tests on the bundle's `.ptf`. The tests read where the patch's RAM
went from the `.map.json` the patcher wrote next to the `.ptf`, so keep the two
together.

`tools/test_layout.py` checks the patcher's placement without the emulator. It builds
each patch alone, `patch/all`, and the same two patches in the other order, and
checks that each patch's code and RAM sit where they should (RAM in a region the
patch may use, no two blocks overlapping), that exactly the words in its relocation
table moved, and that every hook points into it:

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
4. Config should now show the patch's version text, e.g. `1.2.0-c11 Build 1504`.

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

- C/assembly code, linked at any address after the stock image (`link.ld`).
- State variables in the `.mt_ram` section:
  `static struct my_state state __attribute__((section(".mt_ram")));`. It isn't
  initialised (no zeroing, no initial values): the patch has to set it up itself,
  for example with a magic word checked on first use. The patcher picks the address.
- A relocation table, so the patcher can move the code and the RAM: `build.sh` links
  the patch three times (as built, code moved 64 KB, RAM moved 64 KB), and
  `tools/mkrel.py` lists the words that differ by exactly that much (pointers to the
  patch's own code and data, and to its RAM). It refuses code it can't move this way.
  The colorsong and Humanize build scripts show how.
- `patch.json`: the stock image hash, the build outputs (`bin`, `sym`, `rel`, and
  their `prebuilt_` copies), Thumb-2 `BL` sites to retarget (with their original
  bytes), and data words to replace (literal pools, vtable slots with
  `"thumb": true`). Instead of a relocation table, a spec can give a fixed `base`.
  Optional RAM settings:
  - `"ram_startup": true` when the state is used before `main` (for example from the
    Config menu constructor, as Humanize does). The patch then never gets
    `sdram_top`, which may not be set up that early.
  - `"ram_regions": [...]` to choose the regions and their order instead of the
    default. Name `itcm_tail` here to allow it (32-bit aligned accesses only).
  - `"standalone": true` for a patch that must not be combined with others.
- `tools/patch_ptf.py` checks everything, appends the code without gaps, and keeps the
  stock Intel HEX layout, so an unmodified image re-encodes to the exact stock file.
- To combine it with other patches, give it its own hook sites, and mark its version-text word with `"version": true` so a bundle can replace it
  (see "Choosing patches" in section 2).
