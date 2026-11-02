# Contributing to Modular-Trackular

Modular-Trackular is feature-complete for Tracker+ firmware 1.2.0. It takes bug reports
and bug fixes, not new mods. New mods go to
[Modular-Trackular-Plus](https://github.com/FatRodzianko/Modular-Trackular-Plus), which
builds on Polyend's Tracker-CE firmware.

## Reporting a bug

Open an issue with:
- the version text Config shows (it names the exact build, for example `1.2.0-MPL`);
- whether you installed it from the web patcher or from a patch in this repository;
- what you did, what you expected, and what happened;
- whether stock 1.2.0 does the same.

## Fixing a bug

Fixes are welcome, whether you write them yourself or with an AI coding agent. This
page says what a pull request needs, so it can be checked and merged quickly.

**Before you change anything big**, comment on the issue with the fix you plan: which
patch, which stock functions it hooks, and whether its RAM use changes. Several hook
sites and most of the fast RAM are shared (see "Rules for patches" below), so agreeing
on the fix first saves you a rewrite.

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

## What a fix looks like

A pull request for a fix changes:

| What | Where |
|---|---|
| The patch | `patch/<name>/`: the source, and `prebuilt/` (the built `.bin`, `.sym`, `.rel`) rebuilt from it |
| Its version text | the patch's own text moves on (for example `ch2` to `ch3`), so a bug report's version text shows whether it has the fix |
| The emulator test | `tools/emu/test_<name>.py`: a check that fails without the fix and passes with it |
| Docs | the patch's `README.md` if what it does changed, and the tested-status table in `docs/PATCHING.md` |

A fix to a patch in `patch/all` changes that bundle and the web patcher's builds too.
The maintainer gives `patch/all` its new version text and updates `web/expected.json`,
or you can, with the release tier's `--update` (see "Tests to run").

How patches work (hooks, relocation, RAM, `patch.json`) is in
[docs/PATCHING.md section 6](docs/PATCHING.md#6-writing-your-own-patch), and the
firmware itself in [docs/FINDINGS.md](docs/FINDINGS.md).

A patch's README says what it does, how it works, how to build it and how to go back
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
# while working on a fix
python tools/run_tests.py path/to/TrackerPlus_1.2.0.ptf --tier quick

# a fix to a patch in patch/all, or anything that changes patch/all, tools/patch_ptf.py, web/ or shared emulator code
python tools/run_tests.py path/to/TrackerPlus_1.2.0.ptf --tier release
```

- **quick** runs the layout checks and every patch's emulator test on each patch alone
  and on `patch/all`. It is enough for a fix to a standalone patch (chords and the
  diagnostics).
- **release** adds the web patcher harness and the tests on the cover set of web
  mixes. It takes longer, and it's required for a fix to any patch in `patch/all`,
  because that changes the web patcher's builds. The maintainer runs it before merging
  anyway.
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
merged, and the web patcher picks it up from `master`.
