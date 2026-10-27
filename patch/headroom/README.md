# headroom: how much of each audio block the firmware uses

A diagnostic patch for Polyend Tracker+ firmware 1.2.0. It measures, on the device, how
much of each audio block's time the firmware uses, as a share of the block's real
length. It also measures how much of that is the sequencer, with every patch hooked on
it. The numbers set the cost limits in the emulator tests
([docs/TIMING_HEADROOM_PLAN.md](../../docs/TIMING_HEADROOM_PLAN.md)). Config shows the
firmware version as `1.2.0-hr1`; the two test bundles show `1.2.0-MH1` and
`1.2.0-MH2`.

It's a tool, not a feature: install one of its builds for a test session, then go back
to your usual firmware.

**Status:** emulator-tested, alone and listed last in both bundles. Not yet tested on
hardware.

## The builds

| Spec | Version | What |
|---|---|---|
| `patch/headroom/patch.json` | `hr1` | headroom alone, on stock firmware |
| `patch/headroom/all_headroom.json` | `MH1` | patch/all's patches, then headroom |
| `patch/headroom/all_polymeter_headroom.json` | `MH2` | patch/all's patches, polymeter, then headroom |

Headroom must be **listed last** in a bundle. Its sequencer hook then runs before the
other patches' hooks on the same call, so its figure includes them.
`tools/emu/test_headroom.py` fails on a bundle where it isn't last.

## Reading it

1. Put an empty file named `.develop` in the SD card's root (from a computer). This turns
   on the stock firmware's developer lines in Config.
2. Install the build. Open a project and play.
3. Open Config. Once a second, the two CPU lines that stock shows there read:

   ```
   blk 61.3% seq 4.1%
   1s 40.2% late 0
   ```

   | Figure | Meaning |
   |---|---|
   | `blk` | the worst audio block since playback started, in % of a block's length |
   | `seq` | the worst sequencer share of a block (with every patch hooked on it) since then |
   | `1s` | the worst block of about the last second |
   | `late` | blocks that started over 12.5 % late since playback started: the block before took too long, or something held the audio interrupt off. Anything above 0 means a likely click |

   - Over 100 % means a block took longer than the time it has. One such block doesn't
     drop out (the output has about a block of slack); several close together do, and
     the clicks then come with the busiest steps. Stock firmware warns from 84 %.
   - The figures reset each time playback starts, so loading a project doesn't count.
   - An export is never counted: the firmware renders it outside the audio blocks.
   - `headroom: waiting` means no block has been timed yet. `no cycle counter` means
     the chip's cycle counter is off; stock firmware turns it on at boot, so this
     shouldn't happen.

"free heap" and "max stack" below stay as in stock firmware.

## How it works

The Cortex-M7 cycle counter (DWT `CYCCNT`) is already running: stock firmware starts it
at boot and its own CPU meter reads it twice per block. Headroom reads it too, at three
points:

| Hook | Where | What |
|---|---|---|
| `mt_hr_block` | app vtable `+8`, the word at `0x60038724` (chained) | The whole audio block (`app_audio_block`), and the period from one block's start to the next |
| `mt_hr_seq_advance` | `bl seq_advance` at `0x600aa7f8` (chained) | The sequencer's share |
| `mt_hr_line1`, `mt_hr_line2` | the two `bl snprintf` of the Config developer lines (`0x600cb570`, `0x600cb590`, chained) | Writes the two lines above instead of stock's CPU lines |

- **Percentages** are of the measured period, a smoothed average of the time between
  block starts, so no clock rate is assumed.
- **Late:** a period over 12.5 % above that average counts as a late block.
- **Audio-path cost:** about 80 instructions per block for both hooks, which is under
  0.01 % of a block. Everything else happens in Config's once-a-second update.
- **Never written:** debug registers, flash, files, settings.
- **Size:** the code is 757 bytes. The state (48 bytes) is placed by the patcher,
  outside SDRAM (`"ram_startup": true`).

## Build

No compiler needed (the compiled patch is in `prebuilt/`):

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/headroom/patch.json build/TrackerPlus_hr1.ptf
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/headroom/all_headroom.json build/TrackerPlus_MH1.ptf
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/headroom/all_polymeter_headroom.json build/TrackerPlus_MH2.ptf
```

From source, with the Arm GNU Toolchain: `patch/headroom/build.sh`.

Emulator test (`pip install unicorn capstone numpy`):

```bash
python tools/emu/test_headroom.py build/TrackerPlus_hr1.ptf
```

It runs whole audio blocks through the app vtable with a fake cycle counter (the
instruction count, with a clock the test sets per block). It checks:
- that the patch is first on its sites;
- that MIDI and sequencer state are unchanged by it;
- that its figures equal the instructions of the calls it times;
- the late count, the reset at playback start, the one-second window and export;
- its own cost, that it never writes debug registers, and the Config text.

## Risks and reverting

It only reads the cycle counter and writes its own 48 bytes of RAM. To go back,
install your usual firmware through Config → Firmware. If the unit doesn't boot, put
only the stock `TrackerPlus_1.2.0.ptf` in `/Firmware`, power on with the card in, and
press the pinhole button next to the USB-C port.
