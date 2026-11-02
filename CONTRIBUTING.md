# Contributing to Modular-Trackular

New mods, fixes and notes are welcome, whether you write them yourself or with an AI
coding agent. This page says what a pull request needs, so it can be checked and merged
quickly.

**Before you build something big**, open an issue describing the mod. Say which screens
it changes, which stock functions you plan to hook, and how much RAM it needs. Several
hook sites and most of the fast RAM are already shared (see "Rules for patches" below),
so agreeing on the design first saves you a rewrite.

## The rules that can't bend

- **No Polyend code or assets.** Never put Polyend firmware, disassembly, decompiled
  code, images or other files from the firmware into the repository, and never share a
  modified `.ptf`. Patches are your own code. Notes in `docs/FINDINGS.md` describe what
  the firmware does, in your own words, with addresses.
- **Your own code.** Don't copy code from other Tracker mods. Write your own and credit
  any project whose ideas you used in the README's "Credits and related work".
- **Emulator first.** Every patch has an emulator test, and it passes before anyone
  flashes it. Mapping memory the device doesn't have once froze a unit (FINDINGS
  section 11). The emulator maps only the memory the device has, so keep it that way.
- **No personal data.** Committed files must not contain local absolute paths (like a
  home folder) or the names of your own projects. Repository-relative paths are fine.
- **License.** Contributions are under the repository's [MIT license](LICENSE).

## What a new mod looks like

A new mod comes in as a **standalone patch**: its own folder, its own test, and its own
build (for example `1.2.0-ch2`). It joins the `patch/all` bundle and the web patcher
later, as a separate step the maintainer does after the mod has been tested on
hardware. Joining changes every bundle's layout and the web patcher's tests, so it is
kept apart from the mod itself.

A pull request for a new mod adds or changes:

| What | Where |
|---|---|
| The patch | `patch/<name>/`: `<name>.c`, `link.ld`, `build.sh`, `patch.json`, `prebuilt/` (the built `.bin`, `.sym`, `.rel`) and a `README.md` |
| The emulator test | `tools/emu/test_<name>.py` |
| Test runner entries | `tools/run_tests.py`: the patch in `NOT_IN_WEB`, and in `STOCK_ARG` if its test also takes the stock `.ptf` |
| Layout checks | `tools/test_layout.py`: the patch alone, after `patch/all`'s patches, and first with them after it (see how chords is listed) |
| Docs | the root `README.md` (mod list, layout table, tools table) and the tested-status table in `docs/PATCHING.md` |

`patch/colorsong/` is a complete example of a patch, and `patch/chords/` is the most
recent one to arrive as a standalone patch. How patches work (hooks, relocation, RAM,
`patch.json`) is in [docs/PATCHING.md section 6](docs/PATCHING.md#6-writing-your-own-patch),
and the firmware itself in [docs/FINDINGS.md](docs/FINDINGS.md).

The patch's README says what it does, how it works, how to build it and how to go back
to stock. It has no version history; that's what git is for.

## Rules for patches

- **Shared hook sites must chain.** Several patches hook the same calls (for example
  the Pattern screen update slot `0x600403DC` and the Config menu constructor sites).
  A hook on a site another patch uses gets `"next"` in `patch.json` and calls through
  its chain word, never the stock address. The patcher refuses a site two patches
  hook unless both chain.
- **Keep RAM small.** Patch state goes in `.mt_ram` (no `.data` or `.bss`). It isn't
  zeroed, so check a magic word and set it up yourself. With every patch bundled, the
  RAM regions that are ready at startup are almost full. Use `"late_ram"` for anything
  only needed after boot, and say in the pull request how much RAM the patch needs.
- **Buttons go on the first free slot,** found at run time (as copypattern does), never
  at a fixed position, so patches don't land on each other's buttons.
- **SD card I/O** only from a screen's update method, in pieces of at most 256 bytes.
- **A per-project settings file must survive Save As and New Project.** If your patch
  saves a file in the project folder, its test runs the firmware's own save requests
  through `save_as_checks` in `tools/emu/project_save.py`, as swing, humanize,
  interpolate and masterfx do.
- **Version text**: give the patch its own 1 to 3 characters (Config shows
  `1.2.0-<text>`), not used by any other patch or build in `docs/PATCHING.md`, and mark
  the word with `"version": true`.
- **No Break FX or performance mode surprises.** If your patch touches the sequencer,
  its test covers performance mode and Break FX too, as the existing sequencer tests do.

## Tests to run

Set up the tools as in the README's "Set up for reverse engineering":
- Python with `unicorn`, `capstone` and `numpy`;
- `arm-none-eabi-gcc` to build the patch;
- your own stock `TrackerPlus_1.2.0.ptf`;
- Node.js, for the release tier.

`tools/run_tests.py` runs everything in parallel and skips what already passed. It
prints a time estimate before the long part; `--jobs N` limits how many tests run at
once.

```bash
# a new standalone mod, or a change inside one patch's own folder
python tools/run_tests.py path/to/TrackerPlus_1.2.0.ptf --tier quick

# anything that changes other patches, patch/all, tools/patch_ptf.py, web/ or shared emulator code
python tools/run_tests.py path/to/TrackerPlus_1.2.0.ptf --tier release
```

- **quick** runs the layout checks and every patch's emulator test on each patch alone
  and on `patch/all`, including standalone mods like yours. It is the required run for a
  new standalone mod.
- **release** adds the web patcher harness and the tests on the cover set of web
  mixes. It takes longer, so it's only required when your change reaches beyond your
  own patch folder. The maintainer runs it before merging anyway.
- The results are in `build/testrun/summary.txt` (or under `--out`). Both tiers end
  with a line like `tier quick: 81 jobs, 0 failed, ...`.

## What to put in the pull request

The pull request template asks for these. Filling them in is what makes a pull request
quick to verify: the maintainer reruns the tests and compares, instead of working out
what to test.

1. **What the mod does** and how to use it on the device: buttons, screens, settings.
2. **What it hooks**: the stock sites from `patch.json`, and which ones other patches
   share.
3. **RAM**: how many bytes, and whether it's needed at startup. The `.map.json` that
   `tools/patch_ptf.py` writes next to the output shows where it landed.
4. **Test results**:
   - the tier you ran and its summary line;
   - your patch's own test output (its PASS lines and failure count);
   - `build/testrun/summary.txt` attached, if you can.
5. **The build you tested**: its version text (`python tools/emu/check_version.py
   <your.ptf>`) and the SHA-256 of the `.ptf` that `tools/patch_ptf.py` made from the
   stock file and your `patch.json`. The maintainer rebuilds it from your pull request
   and checks the hash matches, which shows the `prebuilt/` files are the ones you
   tested.
6. **Hardware**: whether you flashed it on a Tracker+, what you tried, and that you
   could switch back to stock firmware through Config afterwards. Hardware testing
   isn't required for a pull request. If you haven't flashed it, say so; the maintainer
   tests on hardware before release.
7. **Anything you changed outside your patch folder**, and why. Those changes get the
   closest look.

## For AI coding agents

[CLAUDE.md](CLAUDE.md) holds the same rules in the form agents read: the build
commands, the patch anatomy, the test tiers and the repository rules. Point your agent
at it and at this file before it starts. Ask it for the evidence above, taken from the
runner's own output, not summarised from memory.

## After you open it

The maintainer:
- rebuilds your patch from `prebuilt/` and checks the SHA-256 you gave;
- reruns the tests;
- reviews the hooks and RAM use;
- tests the build on hardware.

A pull request may get questions or requested changes. After hardware testing it's
merged as a standalone mod, and it can join `patch/all` and the web patcher in a later
step.
