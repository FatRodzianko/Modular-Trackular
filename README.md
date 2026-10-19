# Modular-Trackular

Reverse engineering notes, tools and firmware mods for the **Polyend Tracker+**
(the 2024 "plus" model, not the original Tracker). Target: stock firmware **1.2.0**.

**This repository contains no Polyend code.** Mods are distributed as patches that you
apply to your own copy of the stock firmware from polyend.com. Do not share modified
`.ptf` files.

## What's here

- **colorsong**: colors the Song screen's track blocks by their content. Blocks with
  the same instruments share a color family, and different variants of them (a fill,
  other notes, other FX) get clearly different shades. Identical content always gets
  the identical color, in any column. Colors are saved per project. Micro-move and
  Volume FX are ignored, so humanizing a track keeps its color.
  Tested on hardware. See [patch/colorsong/README.md](patch/colorsong/README.md).
- **humanize**: a Humanize button on the Pattern screen gives the notes in the
  selection (or the cursor's track) small random Micro-move and Volume/Velocity
  FX, so programmed parts sound less mechanical. Works in record mode, and Undo
  reverts it. Timing and volume ranges and default velocities are set per project under Config →
  Project Settings → Humanize. Tested on hardware. See [patch/humanize/README.md](patch/humanize/README.md).
- **interpolate**: a CC FX on a MIDI track, or a Macro on a synth (S1–S3), ramps
  smoothly to the next step with the same FX instead of jumping there, with the MIDI
  rate capped so notes always get through. MIDI and synths each have their own
  per-project setting under Config → Project Settings → FX Interpolation (both Off by
  default). Tested on hardware. See [patch/interpolate/README.md](patch/interpolate/README.md).
- **freeverb**: replaces the stock master reverb with Freeverb (public domain), driven
  by the same Master Reverb knobs; projects are unchanged. Emulator-tested; not yet
  tested on hardware. See [patch/freeverb/README.md](patch/freeverb/README.md).
- **all**: colorsong, humanize and interpolate in one firmware (version text
  `1.2.0-MTC`), from [patch/all/patch.json](patch/all/patch.json).
- **ramdiag**: a diagnostic, installed on its own, that checks on the device which
  RAM regions stay untouched in use and how far the heap grows, to find room for
  future patches. See [patch/ramdiag/README.md](patch/ramdiag/README.md).
- **versiontest**: the smallest possible mod (version text `1.2.0-MT0`), a harmless
  first check that your unit installs patched firmware. See
  [patch/versiontest/README.md](patch/versiontest/README.md).
- **Reverse engineering notes**: [docs/FINDINGS.md](docs/FINDINGS.md) covers the
  `.ptf` format, chip and memory map, MPU, update path and recovery, EVE display
  layer and widget library, screens, the Song screen, pattern and project file
  formats, the SD-card/FatFs API, the hardware lessons learned, the sequencer and FX
  timing, the Pattern screen's buttons, selection and undo, the Config menu, and a
  map of all RAM with the regions free for patch state, what disabling the
  built-in NES games would free, and what adding a synth engine or custom step FX,
  or swapping the master reverb or delay, would take.
  Function names
  with notes are in [ghidra/names_1.2.0.tsv](ghidra/names_1.2.0.tsv).
- **Tools**: `.ptf` conversion and patching, Ghidra headless scripts, and a Unicorn
  emulator harness that runs the stock firmware's own code (drawing, pattern
  analysis, sprintf, …) with a fake SD card, so patches are tested before they go
  near hardware.

## Install a mod on your Tracker+

Follow [docs/PATCHING.md](docs/PATCHING.md). In short: download the stock 1.2.0
firmware from Polyend, check its SHA-256, run one Python command (no compiler
needed), copy the result to `/Firmware` on the SD card, and install it through
Config → Firmware. You can return to stock the same way.

Each mod can be installed on its own, or several can be combined into one firmware.
`patch/all` combines colorsong and Humanize, and you can make your own mix, including
patches of your own: see "Choosing patches" in [docs/PATCHING.md](docs/PATCHING.md).

## Layout

| Path | What |
|---|---|
| `docs/FINDINGS.md` | Reverse engineering notes, with addresses (firmware 1.2.0) |
| `docs/PATCHING.md` | How to build, verify, install and remove a patched firmware |
| `ghidra/names_1.2.0.tsv` | Function names worked out so far, with notes |
| `ghidra/scripts/` | Ghidra scripts: memory map setup, vtable functions, names, index export, decompile, callers, references in a range (byte by byte, or summarized for large ranges), whole-program reference graph |
| `patch/colorsong/` | Colored Song blocks: C + assembly shim, linker script, build script, patch spec, prebuilt binary |
| `patch/humanize/` | Humanize button: C, linker script, build script, patch spec, prebuilt binary |
| `patch/interpolate/` | CC and Macro ramps between steps |
| `patch/freeverb/` | Freeverb as the master reverb |
| `patch/all/` | Bundle spec: colorsong + humanize + interpolate in one firmware |
| `patch/ramdiag/` | RAM diagnostic: which candidate regions stay untouched, heap high-water mark |
| `patch/versiontest/` | Version-text test patch |
| `tools/ptf.py` | Read / convert / re-encode `.ptf` (Intel HEX), with a byte-exact round-trip check |
| `tools/patch_ptf.py` | Apply a patch or bundle spec to a stock `.ptf`: places the patches in the order listed, assigns their RAM and relocates them (stock hash + original-byte checks, gap-free append) |
| `tools/mkrel.py` | Make a patch's relocation table (code and RAM addresses) from three links of it (used by the build scripts) |
| `tools/test_layout.py` | Check the patcher's code and RAM placement, relocation and hook chains for each patch alone and both bundle orders |
| `tools/thumbdis.py` | Quick Thumb disassembly with literal-pool values and strings |
| `tools/movw_refs.py` | Find literal and MOVW/MOVT references to an address |
| `tools/vtables.py` | Find C++ vtables and list their method pointers |
| `tools/ghidra.sh` | Headless Ghidra wrapper: import + analyse, or run a script |
| `tools/emu/machine.py` | Unicorn harness: the stock image with the unit's real memory map; call firmware functions |
| `tools/emu/eve.py` | Decode and render EVE (FT81x) display lists to PNG |
| `tools/emu/fakefs.py` | Fake SD card behind the firmware's file system object (read and write) |
| `tools/emu/song_grid.py`, `render_project.py` | Render a project's Song grid with the firmware's own drawing code |
| `tools/emu/test_colorsong.py`, `test_rescan.py` | colorsong rule and scenario tests on the patched firmware |
| `tools/emu/test_ramdiag.py` | ramdiag menu, fill, scan and report tests on the patched firmware |
| `tools/emu/test_humanize.py` | humanize button, edit-rule, Config menu and settings-file tests on the patched firmware |
| `tools/emu/test_freeverb.py` | freeverb block handling, knob, level and cost tests through the reverb object's update(), and WAV renders next to the stock reverb |
| `tools/emu/test_interpolate.py` | interpolate ramp, MIDI-rate, cost, Config menu and settings-file tests, through the real audio block |
| `tools/emu/seq_timing.py` | Run the stock sequencer on test patterns and print the MIDI it sends, with timing |
| `tools/emu/check_version.py` | Print the version text a `.ptf` would show |
| `tools/emu/layout.py` | Read where the patcher put each patch's RAM (`<firmware>.map.json`), for the tests |

Local-only folders (ignored by git): `work/` (converted images, exports), `out/`
(renders), `build/` (patched `.ptf` files), `patch/*/build/`, `ghidra/projects/`.

## Set up for reverse engineering

Requirements: Python 3 (tested with 3.15), Ghidra 12.x with a 64-bit JDK 21+, and the Arm GNU
Toolchain (`arm-none-eabi-gcc`) for building patches. On Windows, run the shell
scripts from Git Bash.

```bash
python -m venv .venv
.venv/bin/python -m pip install unicorn capstone numpy        # Windows: .venv/Scripts/python
python tools/ptf.py check path/to/TrackerPlus_1.2.0.ptf
python tools/ptf.py tobin path/to/TrackerPlus_1.2.0.ptf work/tp_1.2.0.bin

export GHIDRA_HOME=/path/to/ghidra_12.x_PUBLIC                 # JAVA_HOME too, if needed
tools/ghidra.sh import 1.2.0                                   # ~4 minutes
python tools/vtables.py work/tp_1.2.0.bin --methods work/vtable_methods.txt > work/vtables.txt
tools/ghidra.sh run 1.2.0 CreateFunctions.java "$PWD/work/vtable_methods.txt"
tools/ghidra.sh run 1.2.0 ApplyNames.java "$PWD/ghidra/names_1.2.0.tsv"
tools/ghidra.sh run 1.2.0 ExportIndex.java "$PWD/work/index_1.2.0"
tools/ghidra.sh run 1.2.0 Decompile.java "$PWD/work/dec" 0x600a1d94
```

The Ghidra project lands in `ghidra/projects/TrackerPlus.gpr` and opens in the GUI too.
Without `GHIDRA_HOME`, the scripts look for a `ghidra_*_PUBLIC` folder next to the
repo. Without `arm-none-eabi-gcc` on `PATH` or `ARM_GCC_BIN`, they look for an
`arm-gnu-toolchain-*` folder next to it.

Emulator examples (a project folder is a copy of one from the SD card's
`Projects/` folder):

```bash
python tools/emu/song_grid.py TrackerPlus_1.2.0.ptf "path/to/Projects/User/<project>" out/song.png
python tools/emu/test_colorsong.py build/TrackerPlus_c11.ptf out/test_colorsong.png
python tools/emu/test_rescan.py build/TrackerPlus_c11.ptf "path/to/<project A>" "path/to/<project B>"
```

## Hard-won rules

- Test every patch in the emulator first, with the emulator mapping **only the memory
  the device has** (see FINDINGS section 11: a table at a non-existent address froze
  the unit).
- Keep version markers to 9 characters (`1.2.0-c11`). Longer text didn't show in
  Config.
- Do SD I/O only where the stock firmware does (a screen's update method), in pieces
  of at most 256 bytes.
- Ship a pass-through build (every hook just calls the original) next to a risky one,
  to tell hooking problems from logic problems.
- Before flashing anything new, confirm you can get back: switching firmware through
  Config, and the emergency reflash (FINDINGS section 3).

## Credits and related work

Polyend's [tracker-lib](https://github.com/polyend/tracker-lib) (file formats), and
original-Tracker projects that showed the way:
[RETracker](https://github.com/patois/RETracker),
[Tracker-Firmware-Mod](https://github.com/mranthropology/Tracker-Firmware-Mod),
[polyend-tracker-re](https://github.com/sandlbn/polyend-tracker-re) and
[SidTracker](https://github.com/sandlbn/SidTracker).

## License

[MIT](LICENSE), for this repository's code and notes. It does not cover Polyend's
firmware, which is not included here.

Polyend and Tracker+ are trademarks of Polyend. This project is not affiliated with
or endorsed by Polyend. Modified firmware is used at your own risk.
