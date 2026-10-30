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
- **masterfx**: each project chooses its master effects under Config → Project Settings →
  Master FX: Reverb, Stock (default), Freeverb (public domain), Plate (Dattorro's plate),
  Spring, Shimmer, "Gated" (a gated reverb burst) or Reverse (the burst swelling up
  instead), and Delay, Stock (default), Tape (a Space Echo style tape echo) or BBD
  (an analog bucket-brigade delay), driven by the same Master Reverb and Master Delay
  knobs. The README says
  what each knob does for each effect, with sound samples. Tested on hardware. See
  [patch/masterfx/README.md](patch/masterfx/README.md).
- **swing**: Global Swing. Under Config → Project Settings → Global Swing, each project
  can set a Swing amount (25–75) that every take and every pattern start uses, the same
  every time; a Swing FX still changes it until the pattern starts again. Off by
  default. Tested on hardware. See [patch/swing/README.md](patch/swing/README.md).
- **visualizer**: a pad visualizer. Hold Note, Instrument, FX1 and FX2 together while a
  pattern plays and the pads show slowly drifting smoke with ripples spreading from the
  notes; press any of the four to go back. Tested on hardware. See
  [patch/visualizer/README.md](patch/visualizer/README.md).
- **condtrig**: conditional trigs. Turn a Chance FX (C) past 100 and it becomes a
  condition on how many times the pattern has played: `1st`, `!1s` (not the first), or
  `1:2` … `8:8` (the A-th pass of every B), counted per track from the start of
  playback or of the pattern; on a track with its own length (tracklen), per loop of
  that track. The idea comes from Psygoo's CT6. Tested on hardware. See [patch/condtrig/README.md](patch/condtrig/README.md).
- **synths**: two synths, WAVE and PHASE, each taking the place of one of the five
  synth models per project (Config → Project Settings → Synth Engines → WAVE replaces,
  PHASE replaces). The Model list then shows them, with their own controls:
  - WAVE: two oscillators on 7 wavetable banks, a super saw or a saw, Digital mode,
    ladder, OB and SVF filters with drive, and three envelopes.
  - PHASE: Casio CZ-style phase distortion: two DCOs on 8 shapes (pairs too), DCW and
    DCA envelopes, a pitch envelope, ring and noise mod.
  - Both: two LFOs (bipolar or unipolar), a 6-slot Mod Matrix and a stereo chorus.

  Their patches save and load like any model's. Tested on hardware. See
  [patch/synths/README.md](patch/synths/README.md).
- **legato**: legato for MIDI instruments and synth slots. A Glide FX (`G`, any value)
  on a MIDI-instrument or synth (S1–S3) note sends its note-on before the previous
  note's note-off, so a mono synth, or a synth slot at Polyphony 1 with Glide Mode
  Legato, plays the two notes legato instead of retriggering its envelopes, without a
  second track. Stock firmware ignores `G` on these notes. Tested on hardware. See
  [patch/legato/README.md](patch/legato/README.md).
- **copypattern**: a Copy to New button on the Pattern screen (record mode, next to
  Humanize) copies the current pattern, with its unsaved edits and every track's
  length, into the next empty pattern, playing or stopped, and stays on the current
  pattern. Its button sits next to Humanize's, or in Humanize's place without it.
  Tested on hardware. See
  [patch/copypattern/README.md](patch/copypattern/README.md).
- **slicethru**: Beat Slice (Play Through), a ninth play mode in the Sample Playback
  screen's Play Mode list, after Beat Slice. You slice a sample as in Beat Slice, and
  each note plays from its slice to the end of the sample instead of stopping at the
  next slice. With Loop on (More, the 4th button) it then loops the whole sample until
  stopped. The instrument stays a Beat Slice instrument, so stock firmware plays it as
  Beat Slice; the choices are saved per project. Tested on hardware, Loop included. See
  [patch/slicethru/README.md](patch/slicethru/README.md).
- **resample**: live resampling in the Sample Recorder. Two buttons in the recorder's
  setup: Trk Audio plays the pattern there (Play, or Shift+Rec to start it with the
  recording) with its sample and synth instruments, delay and reverb; Trk Record
  records it too, Line In plus the tracker, with no cable. Saved per project.
  Recordings go into the free part of the sample memory, so the loaded samples stay
  intact. With both Off the recorder is stock. Tested on hardware on its own. See
  [patch/resample/README.md](patch/resample/README.md).
- **tracklen**: Variable Track Length. Each track of a pattern can have its own length
  (Pattern screen, More twice, hold Trk Len and turn the encoder). A shorter track
  loops its own steps and every track starts again together when the pattern ends; the
  grid shows the loop going on past the length in grey. Live recording lands on the
  step the track is playing, and conditional trigs count the track's own loops. Saved
  per project; stock firmware plays every track the whole pattern. Tested on hardware
  in patch/all. See [patch/tracklen/README.md](patch/tracklen/README.md).
- **songloop**: loop a selection of Song positions. In the Song screen's pattern
  column, with Rec on, Shift + Down selects a block of positions; Play Selection loops
  it (from its start when stopped, or once the song reaches it), Up/Down move it while
  it loops, Continue Song plays it out and goes on. Repeat signs in the status bar and
  the Song list; Performance stays on while the block loops (as Loop Performance does
  for one pattern). Tested on hardware, on its own and in patch/all. See
  [patch/songloop/README.md](patch/songloop/README.md).
- **all**: all fourteen mods above in one firmware (version text `1.2.0-MNP`), from
  [patch/all/patch.json](patch/all/patch.json). Tested on hardware.
- **ramdiag**: a diagnostic, installed on its own, that checks on the device which
  RAM regions stay untouched in use and how far the heap grows, to find room for
  future patches. See [patch/ramdiag/README.md](patch/ramdiag/README.md).
- **headroom**: a diagnostic that measures on the device how much of each audio block's
  time the firmware uses, and the sequencer's share, on Config's developer lines. On its
  own, or listed last after patch/all's patches (with or without Variable Track Length). See
  [patch/headroom/README.md](patch/headroom/README.md).
- **versiontest**: the smallest possible mod (version text `1.2.0-MT0`), a harmless
  first check that your unit installs patched firmware. See
  [patch/versiontest/README.md](patch/versiontest/README.md).
- **itcmtest**: a no-op test (version text `1.2.0-it1`) of hooks in the sequencer's
  ITCM code, which upcoming patches (conditional trigs) need: it passes the Chance FX
  and the count-in through such hooks and changes nothing else. Tested on hardware. See [patch/itcmtest/README.md](patch/itcmtest/README.md).
- **Reverse engineering notes**: [docs/FINDINGS.md](docs/FINDINGS.md) covers the
  `.ptf` format, chip and memory map, MPU, update path and recovery, EVE display
  layer and widget library, screens, the Song screen, pattern and project file
  formats, the SD-card/FatFs API, the hardware lessons learned, the sequencer and FX
  timing, the Pattern screen's buttons, selection and undo, the Config menu, and a
  map of all RAM with the regions free for patch state, what disabling the
  built-in NES games would free, what adding a synth engine or custom step FX would
  take, and how the master reverb and delay work and are swapped.
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

**No Python?** The [web patcher](https://fatrodzianko.github.io/Modular-Trackular/) does
the same in your browser: choose your stock `TrackerPlus_1.2.0.ptf`, tick the mods you
want, and download the patched file. Your firmware isn't uploaded anywhere, and the
page only offers a download when its output is byte-identical to what
`tools/patch_ptf.py` makes. See "In your browser" in [docs/PATCHING.md](docs/PATCHING.md).

Each mod can be installed on its own, or several can be combined into one firmware.
`patch/all` combines all fourteen, and you can make your own mix, including
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
| `patch/masterfx/` | Master reverb and delay per project: Stock, Freeverb, Plate, Spring, Shimmer, "Gated" or Reverse; Stock, Tape or BBD; sound samples in `samples/` |
| `patch/swing/` | Global Swing per project |
| `patch/visualizer/` | Pad visualizer during playback |
| `patch/condtrig/` | Conditional trigs: Chance FX values above 100 as conditions on the pass count |
| `patch/synths/` | WAVE (wavetables) and PHASE (phase distortion), synths in place of synth models per project; their table generators `gen_wavetables.py` and `gen_phase_tables.py` |
| `patch/legato/` | Legato: a Glide FX on a MIDI or synth note sends its note-on before the previous note's note-off |
| `patch/copypattern/` | Copy to New: copies the current pattern into the next empty one |
| `patch/slicethru/` | Beat Slice Thru: a play mode whose notes play from their slice to the sample end, and can loop the whole sample |
| `patch/resample/` | Live resampling: Trk Audio and Trk Record in the Sample Recorder (hear the pattern; record Line In plus the tracker), saved per project; recordings after the loaded samples |
| `patch/tracklen/` | Variable Track Length: a length per track and pattern, set with the Trk Len button, saved per project |
| `patch/songloop/` | Song Loop: select a block of Song positions and loop it (Play Selection, Continue Song) |
| `patch/all/` | Bundle spec: colorsong + humanize + interpolate + masterfx + swing + visualizer + condtrig + synths + legato + copypattern + slicethru + resample + tracklen + songloop in one firmware |
| `patch/ramdiag/` | RAM diagnostic: which candidate regions stay untouched, heap high-water mark |
| `patch/headroom/` | Timing diagnostic: worst audio block and sequencer share in % of the measured block length, late blocks; its bundle specs (`all_headroom.json`, `all_tracklen_headroom.json`) |
| `patch/versiontest/` | Version-text test patch |
| `patch/itcmtest/` | No-op test of ITCM hooks (stubs in the ITCM tail, loaded by the startup copy table) |
| `tools/ptf.py` | Read / convert / re-encode `.ptf` (Intel HEX), with a byte-exact round-trip check |
| `tools/patch_ptf.py` | Apply a patch or bundle spec to a stock `.ptf`: places the patches in the order listed, assigns their RAM (and late RAM blocks, for state used after boot) and relocates them, and makes the ITCM stubs for hooks in ITCM code (stock hash + original-byte checks, gap-free append) |
| `web/` | The web patcher: `patcher.js` (a port of `ptf.py` and `patch_ptf.py`), the page, `manifest.json` (the mods it offers), `expected.json` (the output hashes it accepts), `stage.py` (assembles the site, named per build so a browser never runs an earlier build's code) |
| `.github/workflows/pages.yml` | Publishes the staged web patcher to GitHub Pages |
| `tools/test_web_patcher.py`, `tools/web_patcher_node.mjs` | Check the web patcher's output is byte-identical to `patch_ptf.py`'s for every selection it offers (needs Node) |
| `tools/mkrel.py` | Make a patch's relocation table (code and RAM addresses) from three links of it (used by the build scripts) |
| `tools/run_tests.py` | Run the tests at a chosen depth (quick, release, deep) in parallel, with a time estimate and a cache that skips what already passed; release adds the web patcher check and a cover set of web mixes (every RAM edge, hook-chain neighbour and padding case) instead of all of them |
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
| `tools/emu/test_headroom.py` | headroom order, transparency, measurement against instruction counts (fake cycle counter), statistics, cost and Config line tests |
| `tools/emu/test_bundle_cost.py` | What a bundle adds to the audio block's work over stock, in the timing runs' worst-case project with every mod switched on: sequencer side (every bundle) and whole block with the real render (patch/all), against limits from the hardware runs |
| `tools/make_headroom_project.py` | Write the timing runs' worst-case project into a copy of a project made on the device, through the firmware's own writers in the emulator |
| `tools/emu/test_humanize.py` | humanize button, edit-rule, Config menu and settings-file tests on the patched firmware |
| `tools/emu/test_copypattern.py` | copypattern button, the search for an empty pattern, the copy (stopped and playing in song mode) against the stock writer and loader, failure cases and file rules |
| `tools/emu/reverb_rig.py` | The master reverb and delay objects driven block by block through their update(), for the masterfx tools |
| `tools/emu/test_masterfx.py` | masterfx: Stock identical to stock firmware, switching, start-up, each effect's block handling, knob, level and cost tests, Config menu and settings file, and WAV renders next to the stock effects |
| `tools/emu/stress_masterfx.py` | masterfx stress: long randomized sessions through the reverb or the delay (garbage settings, missing blocks, restarts, effect choices) with a write watch and state checks |
| `tools/emu/compare_masterfx.py` | masterfx: runs the same randomized session through two builds and compares every output sample, and measures both |
| `tools/emu/test_swing.py` | swing timing (the sequencer run in time, step lengths measured), Config menu and settings-file tests |
| `tools/emu/test_visualizer.py` | visualizer: the four-button switch, column kept, pads handed over and back, hiding, frames per take |
| `tools/emu/test_condtrig.py` | condtrig: every condition in both columns, what a skipped step drops, counting and resets, encoder and grid text, Chance 0–100 as stock, cost |
| `tools/emu/synth_rig.py` | The firmware's synth system (host, slots, voices, the audio graph) driven block by block, for the synth tools |
| `tools/emu/test_synths.py` | synths: stock output unchanged, the swap, the WAVE model and its pages, Model list and Edit Patch drawing, settings and patch files, oscillators, filters, envelopes, LFOs and Mod Matrix, banks, voices, stress, and WAV renders |
| `tools/emu/test_phase.py` | synths' PHASE: WAVE unchanged, the PHASE model, tiles, swaps with both engines, settings and patch files; the shapes against their formulas, aliasing, pairs, ring and noise mod, envelopes, pitch, Mod Matrix, level, voices, cost, stress, and WAV renders |
| `tools/emu/test_slicethru.py` | slicethru: every play mode's note-on as stock without the flag, the slice-to-end span, render cost, `S`, reverse, stereo and re-entry with it; Loop (the whole sample from frame 0, sample-exact, and what ends it); the Play Mode list, the Loop button and every input on the real Sample Playback screen; the settings file |
| `tools/emu/test_resample.py` | resample: the record buffer's placement and the loaded samples kept; Play during a recording against stock and normal playback, the input and Monitor, the stop; Shift+Rec and Play in the count-in through the real Rec handler; the Trk Audio and Trk Record buttons through the real binder; the recording with the tracker, frame for frame, and the count-in's click; resample.bin per project; stock where not asked for; cost |
| `tools/emu/test_songloop.py` | songloop: every hooked site, selecting downwards, Play Selection stopped and playing (block ahead, behind, inside), the preload and the pattern end through the ITCM stubs against stock, moving the block, Continue Song, stop, the locked softkeys, labels and the play icon with Performance, the status bar icons, the fonts, the frame, Performance held while looping, the preload with track 1 not on Fwd |
| `tools/emu/test_legato.py` | legato: random patterns against stock (identical without `G`, only reordered with it), the legato order and every case that keeps the stock order, other FX, stuck notes, cost; synth slots: the order, envelopes on every model and WAVE, PERC unchanged |
| `tools/emu/test_itcmtest.py` | itcmtest: the ITCM stubs after the boot copy, Chance FX and count-in playing exactly as stock through the hooks |
| `tools/emu/test_interpolate.py` | interpolate ramp, MIDI-rate, cost, Config menu and settings-file tests, through the real audio block |
| `tools/emu/seq_timing.py` | Run the stock sequencer on test patterns and print the MIDI it sends, with timing |
| `tools/emu/check_version.py` | Print the version text a `.ptf` would show |
| `tools/emu/layout.py` | Read where the patcher put each patch's code and RAM (`<firmware>.map.json`), for the tests |

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

[TrackerPlus-CT-Web](https://github.com/Psygoo/TrackerPlus-CT-Web/) by Psygoo built
Conditional Trigs for the Tracker+ on this project's tools, with a web patcher that
inspired this repository's own (it was in turn based on the
[Modded-Cycles flasher](https://18nelli18.github.io/Modded-Cycles/flasher/) for the
Elektron Model:Cycles).

## License

[MIT](LICENSE), for this repository's code and notes. It does not cover Polyend's
firmware, which is not included here.

Polyend and Tracker+ are trademarks of Polyend. This project is not affiliated with
or endorsed by Polyend. Modified firmware is used at your own risk.
