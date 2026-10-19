# freeverb: Freeverb as the master reverb

A patch for Polyend Tracker+ firmware 1.2.0. It replaces the stock master reverb with
**Freeverb**, Jezar at Dreampoint's classic public-domain reverb: eight damped comb
filters and four allpasses per channel, a darker and denser room than the stock plate.
The reverb send, the Master Reverb page and its knobs work as before; only the sound of
the reverb changes. Installed on its own, Config shows the firmware version as
`1.2.0-fv1`.

**Status:** emulator-tested; not yet tested on hardware.

## Using it

Nothing to set up. The reverb send FX (`t`) and the instruments' and synths' Reverb
Send work as always, and Master → Reverb sets it:

| Knob | Freeverb |
|---|---|
| Size | Room size: how long the tail rings (comb feedback 0.70–0.98) |
| Damping | How much of the tail's high end is absorbed |
| Predelay | Time before the reverb starts, 0–113 ms |
| Diffusion | How smeared the reflections are (allpass feedback 0.30–0.72) |

Projects are unchanged: the knob values are the stock ones, so a project made with this
patch opens in stock firmware with the stock reverb at the same settings.

## How it works

The Tracker+ audio engine is built like the Teensy Audio Library: audio objects with an
`update()` method, run once per block of 128 samples. The master reverb is one of them
(`0x20019014`): its `update()` takes the stereo reverb send, processes it in place and
sends it on as the reverb return.

| Hook | Where | What |
|---|---|---|
| `mt_fv_update` | the reverb object's vtable, `update()` slot `0x60042f94` (stock `0x60031e5d`) | Takes the two send blocks, runs Freeverb on them in place, transmits and releases them, as the stock `update()` does |

- **Signal:** the send is summed to mono, delayed by the Predelay, and fed to 8 comb
  filters (1116–1617 samples, a one-pole lowpass in each feedback path) and 4 allpasses
  (556, 441, 341, 225) per channel; the right channel's delays are 23 samples longer, for
  stereo width. These are Freeverb's own lengths for 44.1 kHz.
- **Mix:** the return is `send + (wet − send) × amount`, with the stock reverb's own
  amount (0.8) and input gain, so it sits at a similar level.
- **Knobs:** the Master Reverb setters keep writing the reverb object (Size `+0x54`,
  1 − Damping `+0x5c`, Diffusion `+0x58`, Predelay `+0x6c` in samples); Freeverb reads
  them every block.
- **Memory:** the delay lines are stored as 16-bit samples in the stock reverb's two
  32 KB SDRAM buffers (`+0x24`, `+0x28`), which the stock reverb doesn't use any more:
  left channel in one, right in the other, cleared before first use. The patch's own RAM
  (10,288 bytes: the predelay line and the filter states) goes in the free top of SDRAM.
- **Also kept:** the bypass flag (`+0x80`) and the level value the stock reverb writes
  after each block (`+0x70`).

The code is 1,277 bytes (built with `-O2`, as it runs in the audio interrupt); the
patcher places it after the stock image or after the patches listed before it.

**CPU:** each filter runs over the whole 128-sample block at a time. In the emulator
Freeverb takes about 75,000 instructions per block against about 34,000 for the stock
reverb (Freeverb has 24 filters per sample, the stock plate about a dozen): roughly 2 %
more of the processor while the reverb runs. Firmware details are in
[docs/FINDINGS.md](../../docs/FINDINGS.md) (section 19).

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/freeverb/patch.json build/TrackerPlus_fv1.ptf
```

To combine it with other patches, list `../freeverb/patch.json` in a bundle (see
"Choosing patches" in [docs/PATCHING.md](../../docs/PATCHING.md)).

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder):

```bash
patch/freeverb/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_freeverb.py build/TrackerPlus_fv1.ptf path/to/TrackerPlus_1.2.0.ptf out/reverb_wav
```

It calls the reverb object's `update()` through its vtable, as the audio engine does,
with the audio block helpers stubbed, and checks: the block handling (bypass, missing
blocks, both blocks sent and released), that the stock buffers are cleared before first
use, that amount 0 passes the send through, that Size lengthens the tail, Damping
darkens it, Predelay delays it by the right number of samples and Diffusion changes it,
the level value, the loudness against the stock reverb (within 6 dB) and the cost per
block. With a folder given, it also writes WAV files of the same test sounds through
the stock reverb and Freeverb, for listening.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-fv1 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Projects keep their reverb
settings and play with the stock reverb again.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
