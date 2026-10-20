# freeverb: Freeverb as the master reverb

A patch for Polyend Tracker+ firmware 1.2.0. It replaces the stock master reverb with
**Freeverb**, Jezar at Dreampoint's classic public-domain reverb: eight damped comb
filters and four allpasses per channel, a darker and denser room than the stock plate.
The reverb send, the Master Reverb page and its knobs work as before; only the sound of
the reverb changes. Installed on its own, Config shows the firmware version as
`1.2.0-fv5`.

**Status:** tested on hardware.

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
| `mt_fv_pattern_update` | Pattern screen vtable slot `0x600403dc` (chained) | The unit has booted: Freeverb may take over |
| `mt_fv_last_item` | the Config menu constructor's last `bl cfg_menu_item` (`0x601193e6`, chained) | Runs at every start: back to the stock reverb until the Pattern screen runs |

**Start-up.** Freeverb only takes over once the unit has booted. Until the Pattern
screen first appears, the hook runs the stock reverb; then it clears its buffers a piece
at a time over 8 blocks (about 23 ms, silent) and switches to Freeverb. Running
Freeverb while the unit boots hangs the boot.

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
  left channel in one, right in the other, cleared before first use. The predelay line
  (5,001 samples) uses what is left at the end of the two buffers. The patch's own RAM
  (288 bytes: positions and filter states) is on-chip (the OCRAM tail, or the DTCM gap
  above the heap in a bundle). It stays out of the free top of SDRAM, which isn't safe
  for state written from the audio interrupt while the unit boots.
- **Also kept:** the bypass flag (`+0x80`) and the level value the stock reverb writes
  after each block (`+0x70`).

The code is 2,641 bytes (built with `-O2`, as it runs in the audio interrupt); the
patcher places it after the stock image or after the patches listed before it.

**CPU:** each filter runs over the whole 128-sample block at a time: the combs four at a
time and the four allpasses together, each pass split where one of its delay lines wraps
around, so the inner loops have no per-sample position checks. The comb outputs are
added in the same order as one comb at a time would, so the result is the same to the
bit. In the emulator Freeverb takes about 54,000 instructions per block against about
34,000 for the stock reverb (Freeverb has 24 filters per sample, the stock plate about a
dozen). On the device, a deliberately heavy pattern
(every drum hit and all three synths, 8 voices each, sending fully to the reverb) showed
64–67 % average and 84 % peak CPU with Freeverb, against 61 % and 84 % with the stock
reverb. With the synths off and only the drum samples sending to the reverb, it showed
about 23 % average and 43 % peak. With such large sends the stock reverb crackles; Freeverb doesn't. Firmware details are in
[docs/FINDINGS.md](../../docs/FINDINGS.md) (section 19).

Other builds (`FV_DEFS='-D<name> -DFREEVERB_VERSION="<text>"' patch/freeverb/build.sh`):
`FV_PASSTHROUGH` only calls the stock `update()`; `FV_DIAG_CLEAR` runs the stock reverb but
clears the two buffers at once on the first block, from boot on; `FV_DIAG_MUTED` runs
Freeverb from boot on without clearing and sends the send back unchanged. `FV_FIXED`
runs the combs and allpasses in integer arithmetic instead of float. It sounds nearly
the same (samples differ by rounding, at most about 110 of 32,768) but is slower, about
57,000 instructions per block: the Tracker+'s Cortex-M7 does a float multiply-add in one
cycle, so fixed point gains nothing there.

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/freeverb/patch.json build/TrackerPlus_fv5.ptf
```

To combine it with other patches, list `../freeverb/patch.json` in a bundle (see
"Choosing patches" in [docs/PATCHING.md](../../docs/PATCHING.md)).

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder):

```bash
patch/freeverb/build.sh
```

### Emulator tests

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_freeverb.py build/TrackerPlus_fv5.ptf path/to/TrackerPlus_1.2.0.ptf out/reverb_wav
```

It calls the reverb object's `update()` through its vtable, as the audio engine does,
with the audio block helpers stubbed, and checks: the block handling (bypass, missing
blocks, both blocks sent and released), that the stock buffers are cleared before first
use, that amount 0 passes the send through, that Size lengthens the tail, Damping
darkens it, Predelay delays it by the right number of samples and Diffusion changes it,
the level value, the loudness against the stock reverb (within 6 dB) and the cost per
block. With a folder given, it also writes WAV files of the same test sounds through
the stock reverb and Freeverb, for listening.

A stress test runs long randomized sessions (here 4 of 20,000 blocks, about a minute of
audio each): changing sends, Master Reverb fields set to random values and to garbage
(NaN, infinities, out-of-range Predelay), bypass toggles, missing blocks, moved buffers,
the Pattern-screen start and the start-up reset at random. A write watch fails the run
if the patch writes anywhere but its own RAM, the reverb buffers, the audio blocks, the
level field or the stack; it also checks the state after every block, that blocks are
released exactly once, and that the reverb sounds again once normal settings return:

```bash
python tools/emu/stress_freeverb.py build/TrackerPlus_fv5.ptf 20000 11 12 13 14
```

To compare two builds, `compare_freeverb.py` runs the same randomized session through
both and checks that every output sample is equal (or within `--tolerance N`), and
measures both:

```bash
python tools/emu/compare_freeverb.py build/TrackerPlus_a.ptf build/TrackerPlus_b.ptf 3000 1 2 3
```

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-fv5 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Projects keep their reverb
settings and play with the stock reverb again.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
