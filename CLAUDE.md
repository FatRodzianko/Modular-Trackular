# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Firmware mods for the **Polyend Tracker+**, stock firmware **1.2.0** only. The repository holds no Polyend code. Each mod is a small C patch (Cortex-M7, Thumb-2). `tools/patch_ptf.py` applies it to the user's own stock `.ptf`: it appends the code after the image and retargets BL sites and pointer words in flash. All addresses in the code and docs are for 1.2.0.

The stock firmware lives outside the repo, at `../firmware_polyend/TrackerPlus_1.2.0.ptf`. Its SHA-256 is checked against `stock_image_sha256` in every `patch.json`.

## Commands

Python comes from the repo venv: `.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere. Install its packages with `pip install unicorn capstone numpy`. Run the shell scripts from Git Bash.

```bash
# build one patch's code (needs arm-none-eabi-gcc on PATH, ARM_GCC_BIN, or ../arm-gnu-toolchain-*/bin)
patch/<name>/build.sh                      # -> patch/<name>/build/<name>.{bin,sym,rel}; copy to prebuilt/ when shipping

# make a firmware: one patch, or a bundle spec that "include"s several
python tools/patch_ptf.py ../firmware_polyend/TrackerPlus_1.2.0.ptf patch/swing/patch.json build/TrackerPlus_sw1.ptf

# all the checks, in parallel, skipping what already passed (see "Testing" below)
python tools/run_tests.py ../firmware_polyend/TrackerPlus_1.2.0.ptf --tier quick      # or --tier release

# patcher placement / relocation / hook-chain checks (all patches alone and in bundles)
python tools/test_layout.py ../firmware_polyend/TrackerPlus_1.2.0.ptf build/layout

# emulator tests, one per patch (some also take the stock .ptf as a reference)
python tools/emu/test_swing.py build/TrackerPlus_sw1.ptf ../firmware_polyend/TrackerPlus_1.2.0.ptf
python tools/emu/test_humanize.py build/TrackerPlus_hz8.ptf
python tools/emu/check_version.py build/TrackerPlus_sw1.ptf      # version text Config will show
```

The emulator tests print `PASS` and `FAIL` lines and end with a failure count. To check a bundle, run every patch's test against that bundle's `.ptf`.

Reverse-engineering helpers:
- `tools/thumbdis.py <bin> 0x60030000 <start> <end>`: disassembly with literal-pool values.
- `tools/movw_refs.py`: finds references to an address.
- `tools/vtables.py`: finds C++ vtables.
- `tools/ghidra.sh`: headless Ghidra.
- `python tools/ptf.py tobin <ptf> <bin>`: makes a flat image (load address `0x60030000`).

## Architecture

**Patch anatomy** (`patch/<name>/`):
- `<name>.c` plus a `link.ld`. The link address is a placeholder.
- `build.sh` links three times: normal, code moved +64 KB, RAM moved +64 KB. `tools/mkrel.py` diffs the three into a relocation table, so the patcher can place code and RAM anywhere.
- `patch.json` names the hooks:
  - `branches`: Thumb-2 BL sites to retarget, each with its `old` bytes checked.
  - `words`: aligned 32-bit literals such as vtable slots, callback pointers or ITCM jump words. `"thumb": true` sets bit 0.
  - `itcm_branches`: BL sites inside the sequencer's ITCM code (address in ITCM). ITCM can't BL to flash, so the patcher puts a stub per site at the start of the ITCM tail (`0x1fb40`), loaded at boot through the startup copy table's unused entry 3. `args` passes up to two of the caller's registers to the hook in r2/r3. See `patch/itcmtest` and `patch/condtrig`.
  - A `version` word.
- `prebuilt/` holds the compiled output, so users need no compiler.

**Chained hooks.** A hook with `"next": "<symbol>"` gets a `CHAIN_WORD` in the patch, linked as 0. The patcher fills it with whatever the site pointed to before: the stock target, or the previous patch's hook. The patch must call through that word, never the stock address. A site hooked by two patches must be chained by both, or the patcher refuses. Many patches share sites, for example the Pattern screen update slot `0x600403DC` and the Config menu constructor sites. So new hooks on shared sites must chain.

**Patch RAM.** No `.data` or `.bss`; the linker scripts assert this. State goes in a `.mt_ram` section that is neither loaded nor zeroed. The patcher assigns it from `RAM_REGIONS` in `tools/patch_ptf.py`, the ramdiag-verified free regions listed in FINDINGS section 15. State therefore starts as garbage: every patch checks a magic word and initializes itself. `"ram_startup": true` (state used before `main`) keeps a patch out of SDRAM. Free RAM is scarce once all patches are bundled, so keep state small.

**Code placement.** Patches are placed back to back after the stock image, in the order listed. `<out>.map.json` records where each patch's code and RAM landed. Tests read it through `tools/emu/layout.py` (`patch_code`). Don't hardcode code ranges in tests.

**Per-project settings.** These patches add items to Config → Project Settings by hooking the Config menu constructor: humanize, interpolate, masterfx, swing. They save a small checksummed file (e.g. `swing.bin`) in the project folder. SD I/O happens only from a screen's update method (Pattern or Config screen vtable slot), in pieces of at most 256 bytes.

**Emulator** (`tools/emu/`):
- `machine.py` runs the real stock or patched image under Unicorn. It maps only the memory the device has, applies the startup copy table (ITCM/DTCM), and offers `call`, `stub_function`, `alloc`, and fake objects.
- `seq_timing.make()` also runs the static constructors and fakes the MIDI ports, so tests can drive the sequencer (`seq_tick`, `seq_play_from`).
- `fakefs.py` fakes the SD card.
- `eve.py` decodes and renders the EVE display lists that drawing code emits.
- Nothing runs the real boot path. Tests set up the RAM state a function needs and call it directly.

**Docs.**
- `docs/FINDINGS.md`: the RE notes (memory map, sequencer, screens, file formats, free RAM).
- `docs/PATCHING.md`: the user-facing build and install guide, including the per-patch tested status table and the test commands.
- `ghidra/names_1.2.0.tsv`: function names.

When adding a patch, update the root `README.md` (mod list, layout table, tools table), `docs/PATCHING.md`, and `tools/test_layout.py`.

## Testing

Use `tools/run_tests.py`; don't hand-roll test loops over many firmware files.
- `--tier quick` while working on a patch: layout test, every patch's test on each patch alone and on `patch/all`.
- `--tier release` before any commit that changes what users get (a patch's prebuilt, `patch/all`, `patch_ptf.py`, `web/`): adds the web patcher harness (all selections byte-identical) and every patch's test on the cover set of web mixes and the reordered bundles; full masterfx and synths on `patch/all`.
- `--tier deep` only when masterfx's or synths' audio code changes.
- The cover set replaces "test every mix": each new patch doubles the web patcher's mixes (1023 for ten), so never run the emulator tests on all of them. Byte identity and layout checks do cover all of them.
- masterfx and synths take `--quick` (mix-dependent checks only); their full runs take ~6 and ~22 minutes.
- Its cache (`build/test_cache.json`) skips tests already passed on byte-identical files with the same test code. A change to `tools/emu/` invalidates everything, as it should.

Before starting anything expected to take more than 10 minutes, tell the user the measured estimate (the runner prints one); ask before anything over 45 minutes. Take estimates from the runner or from a timed run, never guess.

## Rules for this repo

- **Emulator first.** Every change is emulator-tested (`tools/run_tests.py`, release tier before anything users get) before the user tries it on hardware. The user has low risk tolerance. Mapping memory the device lacks once froze a unit (FINDINGS section 11).
- **Version text.** At most 3 characters after `1.2.0-` (9 in total), or Config doesn't show it.
- **Git.** Commit only when the user explicitly says so. Never push. Commit author email: `46849520+FatRodzianko@users.noreply.github.com`.
- **Privacy.** Committed files must not contain local absolute paths (user home directories) or the user's own project names. Repo-relative paths and local file names are fine.
- **Patch READMEs.** Say what the patch is, how it works, how to build it and how to revert. No version history.
- **Clean room.** Never copy Polyend code or assets into the repo. Distribute only patches, never modified `.ptf` files.
- Don't install packages or download files without asking.
