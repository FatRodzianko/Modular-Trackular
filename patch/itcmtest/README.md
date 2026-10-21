# itcmtest: a no-op test of ITCM hooks

A test patch for Polyend Tracker+ firmware 1.2.0. It changes nothing you can hear or
see except the version text: Config shows `1.2.0-it1`. What it tests is the way future
patches (conditional trigs first, see
[docs/CONDITIONAL_TRIGS_PLAN.md](../../docs/CONDITIONAL_TRIGS_PLAN.md)) hook the
sequencer's own code, which no released patch has done so far.

It's a tool, not a feature: install it on its own on top of stock firmware, check that
the unit boots and plays as usual, then go back to your usual firmware.

**Status:** emulator-tested alone, in bundles with colorsong, humanize, interpolate,
masterfx, swing and visualizer in both orders,
and chained with a copy of itself. Tested on hardware on its own: it boots, and
Chance FX and the count-in (Record held, then Play) work as in stock firmware.

## Why it exists

The sequencer's time-critical code runs from ITCM, a fast RAM that the firmware fills
from flash at boot. Patches live in flash, and ITCM code can't call that far, so the
patcher puts a tiny stub (8 or 12 bytes) at the start of the free ITCM tail
(`0x1fb40`) for each hooked call, and the call goes to the stub, which jumps to the
patch. The stubs are copied to ITCM at boot by the firmware's own startup copy table:
the patcher fills its unused fourth entry. That touches the boot path, so this patch
proves it on the device before any patch that does something relies on it.

## How it works

| Hook | Where | What |
|---|---|---|
| `mt_it_chance` | ITCM `0x30ce`: `bl rand_range_q` in the Chance FX check of `seq_process_track` | Counts the call, keeps the FX column and track the stub passes it (r7, r8), and calls `rand_range_q` as stock firmware does |
| `mt_it_play_from` | ITCM `0x3752`: `bl seq_play_from` at the end of a count-in, in `seq_tick` | Counts the call and starts playback as stock firmware does |

Both hooks chain (`next`), so other patches can hook the same calls. The patcher also
sets startup copy table entry 3 (`0x600302c4`, stock: unused, length 0) to copy the
20 bytes of stubs from flash, right after the patch code, to ITCM `0x1fb40`. The counts
(32 bytes of patch RAM) are only for the emulator test.

## Building and checking it

```bash
patch/itcmtest/build.sh                     # optional: prebuilt/ is used without a build
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/itcmtest/patch.json build/TrackerPlus_it1.ptf
python tools/emu/test_itcmtest.py build/TrackerPlus_it1.ptf path/to/TrackerPlus_1.2.0.ptf
```

The test checks that the stubs are in ITCM after the boot copy, that every other byte
of ITCM is as in stock firmware, that Chance FX play exactly as in stock firmware
(same notes at the same ticks over 40 passes, the hook called once per Chance FX) with
the right column and track passed, and that a count-in starts playback at the same
tick as stock, through the hook.

## Testing it on the device

1. Back up the SD card. Keep the stock `TrackerPlus_1.2.0.ptf` in `/Firmware`.
2. Install `TrackerPlus_it1.ptf` through Config → Firmware. Config shows `1.2.0-it1`.
3. Play a project with Chance (C) FX on some steps: they should play as usual.
4. Turn on the count-in and start playback: it should count in and start as usual.
5. Switch back to your usual firmware.

## Reverting

Install the stock `TrackerPlus_1.2.0.ptf` (or your usual firmware) through Config →
Firmware. The patch stores nothing on the SD card. If the unit doesn't boot, use the
steps in "If it doesn't boot" in [docs/PATCHING.md](../../docs/PATCHING.md).
