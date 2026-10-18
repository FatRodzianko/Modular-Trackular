# versiontest: version-text test patch

The smallest possible patch for Polyend Tracker+ firmware 1.2.0. All it changes is
the version text in Config, which reads `1.2.0-MT0`. It's a harmless first check
that your unit installs patched firmware and switches back to stock, before you try a
real patch.

## How it works

One literal-pool word changes: the pointer to the version format string `"%d.%d.%d"`
(at `0x600cbf9c`, used by `sprintf` in `0x600cbe88`). It now points to
`"%d.%d.%d-MT0"`, stored at `0x601bb100` in the next 4 KB flash sector after the
stock image (the stock image ends inside sector `0x601ba000`). The gap is written as
`0xFF` data, so the file has no hole. No new code runs.

If Config shows `1.2.0-MT0`, the bootloader has accepted the three things every real
patch depends on:

1. a `.ptf` whose records differ from Polyend's,
2. an image that is longer than the stock one, and
3. data in a flash sector the stock image never uses.

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/versiontest/patch.json build/TrackerPlus_MT0.ptf
```

To build it from source with the Arm GNU Toolchain (`build.sh` writes
`patch/versiontest/build/`, which the patcher then uses instead of `prebuilt/`):

```bash
patch/versiontest/build.sh
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/versiontest/patch.json build/TrackerPlus_MT0.ptf
```

To see the version text a `.ptf` will show, using the firmware's own `sprintf` in the
emulator (needs `pip install unicorn capstone numpy`):

```bash
python tools/emu/check_version.py build/TrackerPlus_MT0.ptf   # -> '1.2.0-MT0'
```

## Install and revert

1. Back up the whole SD card.
2. In `/Firmware`, keep only the stock `TrackerPlus_1.2.0.ptf` and
   `TrackerPlus_MT0.ptf`, so an emergency reflash can only pick one of these two.
   Config's list shows files whose names start with `TrackerPlus` and end with `.ptf`.
3. Config → Firmware → `TrackerPlus_MT0.ptf` → confirm.
4. After the reboot, Config should show `1.2.0-MT0 Build 1504`.
5. To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Config shows `1.2.0` again.

If it doesn't boot, put only the stock `TrackerPlus_1.2.0.ptf` in `/Firmware` (from a
computer), power on with the card in, and press the pinhole button next to the USB-C
port. The bootloader reinstalls the stock firmware.
