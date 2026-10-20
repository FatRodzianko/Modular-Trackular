# masterfx: choose the master reverb per project

A patch for Polyend Tracker+ firmware 1.2.0. It lets each project choose its master
reverb: **Stock** (the Tracker+'s own reverb, the default) or **Freeverb** (Jezar at
Dreampoint's public-domain reverb: eight damped comb filters and four allpasses per
channel, a darker and denser room than the stock plate). The reverb send, the Master
Reverb page and its knobs work the same for both. Installed on its own, Config shows
the firmware version as `1.2.0-mf1`.

**Status:** tested on hardware, on its own and together with colorsong, humanize and
interpolate (in both orders): switching, also during playback, and the choice saved per
project.

## Using it

1. Config → Project Settings → **Master FX** → **Reverb**: Stock or Freeverb. A new
   project, and any project that never had it changed, uses Stock.
2. The choice is saved with the project (in `masterfx.bin` in the project's folder), a
   moment after you change it. Loading another project switches to that project's
   reverb.

The reverb send FX (`t`) and the instruments' and synths' Reverb Send work as always,
and Master → Reverb sets either reverb:

| Knob | Stock | Freeverb |
|---|---|---|
| Size | as stock | Room size: how long the tail rings (comb feedback 0.70–0.98) |
| Damping | as stock | How much of the tail's high end is absorbed |
| Predelay | as stock | Time before the reverb starts, 0–113 ms |
| Diffusion | as stock | How smeared the reflections are (allpass feedback 0.30–0.72) |

Projects are otherwise unchanged: the knob values are the stock ones, and stock
firmware ignores `masterfx.bin`, so a project opens in stock firmware with the stock
reverb at the same settings.

When the reverb changes (from the menu, or by loading a project with the other
choice), the reverb return is silent for about 23 ms while the switch clears the
reverb's memory, so a tail that is still ringing stops. The dry sound isn't affected.

## How it works

The Tracker+ audio engine is built like the Teensy Audio Library: audio objects with an
`update()` method, run once per block of 128 samples. The master reverb is one of them
(`0x20019014`): its `update()` takes the stereo reverb send, processes it in place and
sends it on as the reverb return.

| Hook | Where | What |
|---|---|---|
| `mt_mfx_reverb_update` | the reverb object's vtable, `update()` slot `0x60042f94` (stock `0x60031e5d`) | Stock chosen: calls the stock `update()`. Freeverb chosen: takes the two send blocks, runs Freeverb on them in place, transmits and releases them, as the stock `update()` does |
| `mt_mfx_settings_folder` | the Config menu constructor's Project Settings folder (`0x60118b00`, chained) | Room for one more entry |
| `mt_mfx_last_item` | the Config menu constructor's last `bl cfg_menu_item` (`0x601193e6`, chained) | Adds the Master FX folder with the Reverb item; runs at every start, so it also puts the reverb back to stock until the Pattern screen runs |
| `mt_mfx_pattern_update` | Pattern screen vtable slot `0x600403dc` (chained) | Reads and writes `masterfx.bin`; the unit has booted, so Freeverb may run |
| `mt_mfx_config_update` | Config screen vtable slot `0x6003b5f8` (chained) | Reads and writes `masterfx.bin` |

- **Stock:** the hook calls the stock `update()` and nothing else, so with Stock chosen
  the reverb is exactly the stock one (the emulator tests compare them sample for
  sample).
- **Freeverb:** `freeverb_dsp.h`. The send is summed to mono, delayed by the Predelay,
  and fed to 8 comb filters (1116–1617 samples, a one-pole lowpass in each feedback
  path) and 4 allpasses (556, 441, 341, 225) per channel; the right channel's delays
  are 23 samples longer, for stereo width. These are Freeverb's own lengths for
  44.1 kHz. The return is `send + (wet − send) × amount`, with the stock reverb's own
  amount (0.8) and input gain, so it sits at a similar level. The filters run over the
  whole 128-sample block at a time: the combs four at a time and the four allpasses
  together, each pass split where one of its delay lines wraps around, so the inner
  loops have no per-sample position checks. The delay lines are 16-bit samples in the
  stock reverb's two 32 KB buffers (left in one, right in the other, the 5,001-sample
  predelay line in what is left at the end of the two).
- **Switching:** both reverbs keep their delay lines in the stock reverb's two 32 KB
  buffers, so a switch first clears them, a piece per block over 8 blocks (silent
  meanwhile); otherwise each reverb would play back what the other left there. A
  switch to the stock reverb also zeroes its three filter states.
- **Start-up:** whatever the project chose, the stock reverb runs until the Pattern
  screen first appears, because running Freeverb while the unit boots hangs the boot;
  then a project with Freeverb switches to it.
- **`masterfx.bin`:** 12 bytes in the project's folder: `HMF1`, the reverb (0 Stock,
  1 Freeverb), a byte kept for a later delay choice, two zero bytes and a checksum. A
  missing or damaged file, or one naming a reverb this firmware doesn't have, means
  Stock (and the file is left as it is). A project without a name (not saved yet)
  writes no file; it gets one when it is saved.
- **Memory:** the patch's RAM (320 bytes: the choice and Freeverb's positions and
  filter states) is on-chip (the OCRAM tail, or the DTCM gap above the heap in a
  bundle). The Master FX folder and its item come from the firmware's heap.

The code is 4,180 bytes (built with `-O2`, as the reverb part runs in the audio
interrupt); the patcher places it after the stock image or after the patches listed
before it. With Stock chosen the hook adds a few dozen instructions per block; with
Freeverb, about 54,000 instructions per block in the emulator against about 34,000 for
the stock reverb (Freeverb has 24 filters per sample, the stock plate about a dozen).
On the device, a deliberately heavy pattern (every drum hit and all three synths, 8
voices each, sending fully to the reverb) showed 64–67 % average and 84 % peak CPU with
Freeverb, against 61 % and 84 % with the stock reverb; with the synths off and only the
drum samples sending, about 23 % average and 43 % peak. With such large sends the stock
reverb crackles; Freeverb doesn't. Firmware details are in
[docs/FINDINGS.md](../../docs/FINDINGS.md) (section 19).

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/masterfx/patch.json build/TrackerPlus_mf1.ptf
```

To combine it with other patches, list `../masterfx/patch.json` in a bundle (see
"Choosing patches" in [docs/PATCHING.md](../../docs/PATCHING.md)).

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder):

```bash
patch/masterfx/build.sh
```

### Emulator tests

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_masterfx.py build/TrackerPlus_mf1.ptf path/to/TrackerPlus_1.2.0.ptf out/reverb_wav
```

It calls the reverb object's `update()` through its vtable, as the audio engine does,
with the audio block helpers stubbed, and checks:

- **Stock:** the output equals stock firmware's, sample for sample, before the Pattern
  screen (even with Freeverb chosen) and after it with Stock chosen.
- **Switching:** choosing Freeverb gives 8 silent blocks and clear buffers, then
  Freeverb. Choosing Stock again gives 8 silent blocks and zeroed filter states, then a
  stock reverb that is silent with silence in and has a stock impulse response.
- **Other cases:** an out-of-range choice counts as Stock; the start-up reset.
- **Freeverb:** the block handling (bypass, missing blocks, both blocks sent and
  released), amount 0 passes the send through, Size lengthens the tail, Damping darkens
  it, Predelay delays it by the right number of samples and Diffusion changes it, the
  level value, and the loudness against the stock reverb (within 6 dB).
- **Menu:** the stock menu constructor builds the folder and its item, and the stock
  Apply stores both choices.
- **File:** `masterfx.bin` through the screens' update hooks with a fake SD card:
  per-project choices, one write a while after a change, damaged and unknown files,
  the kept delay byte, a project without a name.
- **Cost:** instructions per block for both reverbs.

With a folder given, it also writes WAV files of the same test sounds through the stock
reverb and Freeverb, for listening.

A stress test runs long randomized sessions (here 4 of 20,000 blocks, about a minute of
audio each): changing sends, Master Reverb fields set to random values and to garbage
(NaN, infinities, out-of-range Predelay), bypass toggles, missing blocks, moved buffers,
the Pattern-screen start, the start-up reset and the reverb choice (Stock, Freeverb, or a
value out of range) at random. A write watch fails the run if the patch writes anywhere
but its own RAM, the reverb buffers, the audio blocks, the reverb's level field and
filter states or the stack; it also checks the state after every block, that blocks are
released exactly once, and that Freeverb sounds again once normal settings return:

```bash
python tools/emu/stress_masterfx.py build/TrackerPlus_mf1.ptf 20000 11 12 13 14
```

To compare two builds, `compare_masterfx.py` runs the same randomized session through
both (Freeverb chosen, switched to Stock and back now and then) and checks that every
output sample is equal (or within `--tolerance N`), and measures both:

```bash
python tools/emu/compare_masterfx.py build/TrackerPlus_a.ptf build/TrackerPlus_b.ptf 3000 1 2 3
```

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-mf1 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Projects play with the stock
reverb again; their `masterfx.bin` files stay in their folders and do nothing in stock
firmware (delete them if you like).

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
