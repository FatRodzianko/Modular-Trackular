<!-- See CONTRIBUTING.md. Fill in what applies; say "n/a" or "not done" rather than deleting a section. -->

## What it does

<!-- The mod or fix, and how to use it on the device: buttons, screens, settings. -->

## What it hooks

<!-- The stock sites from patch.json, and which ones other patches share (chained with "next"). -->

## RAM

<!-- Bytes used, needed at startup or not, and where it landed (the .map.json). -->

## Tests

- Tier run (`quick` or `release`):
- Summary line (`tier ...: N jobs, 0 failed, ...`):
- Your patch's own test output (PASS lines and failure count):

<!-- Attach build/testrun/summary.txt if you can. -->

## The build you tested

- Version text (`python tools/emu/check_version.py <your.ptf>`):
- SHA-256 of the `.ptf` made by `tools/patch_ptf.py` from the stock file and your `patch.json`:

## Hardware

<!-- Flashed on a Tracker+? What you tried, and that switching back to stock through Config worked. "Not flashed" is fine. -->

## Checklist

- [ ] No Polyend code, disassembly or firmware files; my own code
- [ ] No local absolute paths or personal project names in committed files
- [ ] Shared hook sites chain through `next`
- [ ] `prebuilt/` holds the build I tested
- [ ] Emulator test `tools/emu/test_<name>.py` added or updated, and it passes
- [ ] `tools/run_tests.py` (`NOT_IN_WEB`, `STOCK_ARG`) and `tools/test_layout.py` updated for a new patch
- [ ] README (mod list, layout and tools tables), `docs/PATCHING.md` table and the patch's own README updated
- [ ] Changes outside my patch folder are listed above, with why
