# masterfx: choose the master reverb and delay per project

A patch for Polyend Tracker+ firmware 1.2.0. Each project chooses its own master reverb
and master delay:

- **Reverb:** Stock (the default), Freeverb, Plate, Spring, Shimmer, "Gated" (a gated
  reverb burst) or Reverse (the same burst, swelling up instead of cutting in).
- **Delay:** Stock (the default), Tape or BBD.

(Before mf7, "Gated" sat in the Delay list. In the Reverb list it and Shimmer, the two
heaviest effects, can no longer run at once, which caps the worst-case processor load;
the gate now spans 10-368 ms - the classic units' range - set by Size instead of Time.
A project whose file still says Delay "Gated" plays the Stock delay; choose "Gated" as
the Reverb to get the burst back.)

The sends work as always, and the Master Reverb and Master Delay pages set the chosen
effect. Each effect gives the knobs its own meaning, described below. Installed on its
own, Config shows the firmware version as `1.2.0-mf7`.

**Status:** tested on hardware, on its own and together with colorsong, humanize and
interpolate (in both orders): every reverb and delay, switching, also during playback,
and the choices saved per project.

## Using it

1. Config → Project Settings → **Master FX** → **Reverb** and **Delay**. A new project,
   and any project that never had them changed, uses Stock for both.
2. The choices are saved with the project (in `masterfx.bin` in the project's folder), a
   moment after you change them. Loading another project switches to that project's
   effects. Save As keeps the choices for the project's new name and writes them to its
   new folder (also on a new project's first save, and on Save As over another
   project).
3. Send to the effects as usual (the reverb and delay send FX, the instruments' and
   synths' Reverb and Delay Sends), and set them on Master → Reverb and Master → Delay
   with the knobs below.

When an effect changes (from the menu, or by loading a project with another choice),
its return is silent for a moment while the switch clears its memory, so a tail that is
still ringing stops: about 23 ms for the reverb, and 0.1 s (to Tape or BBD) or
0.14 s (to Stock) for the delay. The dry sound isn't affected.

The effects other than Stock take more of the processor than the stock ones. Measured
on a Tracker+, in % of each audio block: Shimmer about 7 more than the stock reverb,
"Gated" (and Reverse) about 10. From the
emulator, scaled the same way: Freeverb about 6, Plate 4, Spring 2, Tape 4, BBD 5 -
so with "Gated" in the Reverb list the heaviest possible pair is about 11 over stock.
That only matters in a project that already loads the processor heavily, mostly through
synth voices: each allocated voice costs about 5 % even when silent. If Config's CPU
reading gets near stock firmware's 84 % warning, or you hear clicks, choose a lighter
effect or Stock, or lower the synths' Polyphony.

Projects are otherwise unchanged: the knob values are the stock ones, and stock
firmware ignores `masterfx.bin`, so a project opens in stock firmware with the stock
effects at the same settings.

## The effects and their knobs

The sound samples are 16-bit WAV files (GitHub offers them as downloads). All but one
are short renders from the emulator; the last "Gated" one is recorded from a Tracker+.
The reverb samples are the reverb return only, from the same plucked
notes ([dry send](samples/reverb-dry-send.wav)) at the default settings; the delay
samples are the dry notes with the delay return added; the "Gated" samples are a drum
loop ([dry](samples/gated-dry-drums.wav)) with the return added.

### Reverbs (Master → Reverb)

**Stock**: the Tracker+'s own reverb, exactly as without the patch.
[Listen](samples/reverb-stock.wav).

**Freeverb**: Jezar at Dreampoint's public-domain reverb: a darker, denser room than the
stock plate, and it doesn't crackle with very large sends. [Listen](samples/reverb-freeverb.wav).

| Knob | What it does |
|---|---|
| Size | The size of the room: how long the tail rings (about 0.4 s at 0.2 to 1.4 s at 0.9, to −40 dB) |
| Damping | How much of the tail's high end is absorbed: up for a darker tail |
| Predelay | Time before the reverb starts, 0–113 ms |
| Diffusion | How smeared the reflections are: low for distinct early echoes, high for a smooth wash |

**Plate**: Jon Dattorro's plate reverb (1997): smooth, bright and wide, the classic
studio plate sound. [Listen](samples/reverb-plate.wav).

| Knob | What it does |
|---|---|
| Size | Decay: how long the plate rings (about 0.55 s at 0.2 to 2.5 s at the top) |
| Damping | How quickly the high end dies away inside the plate: up for a darker tail |
| Predelay | Time before the reverb starts, 0–113 ms |
| Diffusion | How smeared the attack is: low keeps the start of the tail grainier, high makes it smooth at once |

**Spring**: a two-spring tank, the reverb in a guitar amp: the "drip" and "boing",
with a little resonance around 1 kHz. Springs are band-limited (nothing much above
5 kHz, and thin in the bass), as real ones are. [Listen](samples/reverb-spring.wav).

| Knob | What it does |
|---|---|
| Size | How long the springs ring (about 0.35 s at 0.2 to 1.5 s at the top) |
| Damping | Darker repeats: the springs' high end dies away faster |
| Predelay | Time before the springs start, 0–113 ms |
| Diffusion | The drip: low for a cleaner sound, high for more of the falling "chirp" |

**Shimmer**: the plate with an octave-up pitch shifter in its feedback: every pass adds
a layer an octave higher, so the tail rises into a bright, organ-like wash.
[Listen](samples/reverb-shimmer.wav), and with Size 0.9:
[listen](samples/reverb-shimmer-large.wav).

| Knob | What it does |
|---|---|
| Size | Decay: how long it rings (about 0.5 s at 0.2 to 2.25 s at the top; with a lot of shimmer the octaves go on longer) |
| Damping | Tone: up for a darker tail (it also tames the octaves) |
| Predelay | Time before the reverb starts, 0–113 ms |
| Diffusion | **The shimmer amount**: 0 is a plain plate, and turning it up adds more and higher octaves (at the default, about a third of the tail is an octave up; at the top, most of it is one or two octaves up) |

**"Gated"**: a gated reverb, in quotes because it isn't a reverb behind a noise gate but
what the classic digital units did (the AMS RMX16's "non-linear" programs): a dense
burst of reverb that lasts exactly as long as Size sets and then stops. There is no
tail beyond the gate: each hit gives one burst.
[Listen at 300 ms](samples/gated-300ms.wav), with a release (Diffusion 60 % of the way up):
[listen](samples/gated-300ms-release.wav).

| Knob | What it does |
|---|---|
| Size | **The gate length**: how long each burst lasts, 10 ms at 0 to 368 ms at the top |
| Damping | Tone: a lowpass on the burst; higher is darker |
| Predelay | Time before the burst starts, 0-113 ms (at the top of Size and Predelay together, the gate shortens so both fit the line) |
| Diffusion | **The release**: 0 cuts the burst off hard at the end; turning it up fades the end out, over the whole length at the top |

Each burst fades in over 50 ms (or a quarter of the length, if that is shorter).

**Reverse**: the same burst, with the gate's shape reversed: each hit's cloud of
echoes swells up from almost nothing to full level over the gate length and then cuts
off - reverb sucked in backwards before the cut (the audio itself is not reversed).
Tune Size so the cut lands on the next beat. The knobs are "Gated"'s.
[Listen at 360 ms](samples/gated-360ms-reverse.wav).

Both are at their most classic on one or two hits, such as a snare, or a kick and a
snare: each hit gets its own burst that cuts off. Send many instruments at once and the
bursts overlap into a denser, rhythmic texture instead, less like a gated reverb but
interesting in its own right. Recorded from a Tracker+ (while "Gated" ran on the
delay, where its length could follow the tempo), a drum loop played four times:
dry; then kick, snare and hats sent to "Gated"; then
kick and snare only; then the snare only. [Listen](samples/gated-drum-loop-hardware.wav).

### Delays (Master → Delay)

For every delay, **Time** (in milliseconds) or **Sync** with **Rate** (in note values
of the tempo, when Sync is on) sets the time, as on the stock delay, up to 3.5 s.

**Stock**: the Tracker+'s own delay, exactly as without the patch.
[Listen](samples/delay-stock.wav).

**Tape**: a tape echo in the style of a Roland Space Echo: each repeat a little darker
and thinner, a slight wow and flutter, warm saturation, and the pitch bends when the
time changes. [Listen](samples/delay-tape.wav), and with Time changed while it plays:
[listen](samples/delay-tape-time-sweep.wav).

| Knob | What it does |
|---|---|
| Time, or Sync and Rate | The tape speed: the time between echoes. A change glides there (about 0.15 s), so the echoes swoop down or up in pitch, as on a tape machine |
| Feedback | Intensity: how many repeats. Near the top the echoes build up and sustain on their own, held back by the tape's saturation |
| Filter | Tone: a lowpass on every echo; lower is darker |
| PingPong | Off: the same echo on both sides, like the mono original. On: the repeats alternate right and left |

**BBD**: an analog bucket-brigade delay, as in pedals like the Boss DM-2: the longer the
time, the darker and grittier the echoes (as the chip's clock slows down).
[Listen](samples/delay-bbd.wav), and with Time changed while it plays:
[listen](samples/delay-bbd-time-sweep.wav).

| Knob | What it does |
|---|---|
| Time, or Sync and Rate | The time between echoes, and with it the darkness: bright at short times (about 7 kHz of bandwidth at 100 ms), dark at long ones (2.4 kHz at 300 ms). A change sweeps there quickly, with a short pitch bend |
| Feedback | How many repeats. Near the top the echoes build up and sustain, held back by the chip's clipping |
| Filter | Tone: a lowpass on every echo, on top of the BBD's own darkening |
| PingPong | Off: the same echo on both sides. On: the repeats alternate right and left |

## How it works

The Tracker+ audio engine is built like the Teensy Audio Library: audio objects with an
`update()` method, run once per block of 128 samples. The master reverb is one of them
(`0x20019014`): its `update()` takes the stereo reverb send, processes it in place and
sends it on as the reverb return. The master delay is another (`0x20018fc0`): its
`update()` takes the mono delay send and sends a stereo return (the echoes only).

| Hook | Where | What |
|---|---|---|
| `mt_mfx_reverb_update` | the reverb object's vtable, `update()` slot `0x60042f94` (stock `0x60031e5d`) | Stock chosen: calls the stock `update()`. Otherwise: takes the two send blocks, runs the chosen reverb on them in place, transmits and releases them, as the stock `update()` does |
| `mt_mfx_delay_update` | the delay object's vtable, `update()` slot `0x60042fec` (stock `0x60032405`) | Stock chosen: calls the stock `update()`. Otherwise: takes the send block and two new blocks, runs the chosen delay, transmits the two as left and right and releases all three, as the stock `update()` does |
| `mt_mfx_settings_folder` | the Config menu constructor's Project Settings folder (`0x60118b00`, chained) | Room for one more entry |
| `mt_mfx_last_item` | the Config menu constructor's last `bl cfg_menu_item` (`0x601193e6`, chained) | Adds the Master FX folder with the Reverb and Delay items; runs at every start, so it also puts both effects back to stock until the Pattern screen runs |
| `mt_mfx_pattern_update` | Pattern screen vtable slot `0x600403dc` (chained) | Reads and writes `masterfx.bin`; the unit has booted, so the chosen effects may run |
| `mt_mfx_config_update` | Config screen vtable slot `0x6003b5f8` (chained) | Reads and writes `masterfx.bin` |
| `mt_mfx_save_name` | `bl strncpy` at `0x60114eaa` in the save request `0x60114e34` (the new name; Save As only; chained) | Marks the Save As, so the choices go with the project to its new name and are written there |

- **Stock:** the hooks call the stock `update()` and nothing else, so with Stock chosen
  the reverb and the delay are exactly the stock ones (the emulator tests compare them
  sample for sample).
- **The reverbs** all take the stereo send summed to mono, delay it by the Predelay, and
  mix their return as the stock reverb does: `send + (wet − send) × amount`, with the
  stock reverb's own amount (0.8) and input gain, so they sit at a similar level. Their
  lines are 16-bit samples in the stock reverb's two 32 KB buffers (`+0x24`, `+0x28`).
  The knobs are read from the stock reverb's fields every block: Size (`+0x54`),
  1 − Damping (`+0x5c`), Diffusion (`+0x58`, 0–0.85) and Predelay (`+0x6c`, in samples).
  - **Freeverb** (`freeverb_dsp.h`): 8 comb filters (1116–1617 samples, a one-pole
    lowpass in each feedback path) and 4 allpasses (556, 441, 341, 225) per channel;
    the right channel's delays are 23 samples longer, for stereo width. These are
    Freeverb's own lengths for 44.1 kHz. Size sets the comb feedback (0.70–0.98),
    Damping their lowpass, Diffusion the allpass feedback (0.30–0.72). The filters run
    over the whole block at a time, each pass split where one of its delay lines wraps.
  - **Plate** (`plate_dsp.h`), written from Dattorro's paper ("Effect Design, Part 1",
    J. Audio Eng. Soc., 1997): a bandwidth lowpass and four input allpasses, then a
    "tank" of two cross-coupled halves, each a modulated allpass, a delay, a damping
    lowpass, the decay gain, a second allpass and a second delay; the left and right
    outputs are each the sum of seven taps across the tank, as in the paper. The paper's
    delays are for 29.761 kHz and would be 1.48 times longer at 44.1 kHz; together with
    the Predelay that doesn't fit the two buffers, so they are 1.2 times the paper's: a
    plate about a fifth smaller. As in Mutable Instruments' FxEngine, the lines of each
    buffer share one 16,384-sample ring with one moving write position, so every access
    is a constant offset and a mask. Size sets the decay (0.35–0.97), Damping the
    tank's damping (0–0.85), Diffusion the input allpasses (1.2 × the knob, the stock
    default giving the paper's 0.75 and 0.625) and the first tank allpasses.
  - **Spring** (`spring_dsp.h`), after the structure in Välimäki, Parker and Abel,
    "Parametric Spring Reverberation Effect" (J. Audio Eng. Soc., 2010): each of two
    springs (left and right, 37 and 43 ms) is a delay loop around a chain of 32
    allpass filters that delay high frequencies more than low ones, so each pass smears
    the sound further into a falling chirp; a lowpass in the loop darkens the repeats.
    Springs carry little above 4–5 kHz, so the springs run at a quarter of the sample
    rate (the send is filtered and averaged down, the output interpolated back up and
    smoothed); the send is also high-passed at 120 Hz. The loops move by about a sample
    (0.7 and 0.93 Hz), and 20 % of each spring goes to the other side. A gentle
    resonance around 1 kHz gives the "boing": a +0.5 dB peak in each loop (with the loop
    gain scaled down by as much, so it can't run away) and +3 dB on each spring's
    output. Size sets the loop gain (0.63–0.98 at the resonance), Damping the loop
    lowpass (5 kHz down to 1.2 kHz), Diffusion the allpass coefficient (0.55–0.85).
  - **Shimmer** (`shimmer_dsp.h`): the plate at the paper's own lengths (which leaves
    room for the shifter's line) with its diffusion fixed at the paper's values. Every
    sample, the plate's output (both sides) is written into a 2,048-sample (46 ms) line;
    two read heads move through it at twice the speed it is written, half a line apart,
    each faded in and out with a triangle window as it wraps, so together they play it
    back an octave up without gaps. That is band-limited (200 Hz–6 kHz), limited (so the
    loop can't run away, whatever the knobs), scaled by the shimmer amount (Diffusion:
    0 to 0.6) and added to the plate's input. Size sets the decay (0.35–0.97), Damping
    the tank's damping.
- **The delays** read the stock delay's own fields, so the knobs and Sync work as for
  the stock delay: Time (`+0x28`, in samples, set by Time or by Sync and Rate),
  Feedback (`+0x2c`), the Filter's lowpass (its coefficient at `+0x38`), PingPong
  (`+0x2e`) and the bypass flag (`+0x2d`). Their lines are in the stock delay's first
  buffer (2^18 16-bit samples, about 5.9 s) and its second. A missing send block counts
  as silence, so the echoes carry on.
  - **Tape** (`tape_dsp.h`): one record head and one playback head on a loop of tape,
    and with PingPong two tapes feeding each other. When Time changes, the read point
    glides there (about 0.15 s; at most 0.9 samples per sample longer and 3 shorter),
    which bends the pitch as a tape machine does. Wow (0.55 Hz), flutter (6.3 Hz) and
    drift (0.13 Hz) move the read point by about 0.35 %, 0.18 % and 0.1 % of pitch (6, 3
    and 1.7 cents). The record head has the tape's high-frequency loss (a one-pole
    lowpass at 7 kHz, so every pass is darker) and is driven 1.7 times into a soft clip
    (`u − 4u³/27`, flat from 1.5) and scaled back. Feedback sets the loop gain up to
    1.04, and a 70 Hz low cut in the feedback thins the repeats.
  - **BBD** (`bbd_dsp.h`): a 4096-stage bucket-brigade chip whose clock is set by the
    delay, clock = 4096 / (2 × time); an anti-alias lowpass before the chip and a
    reconstruction lowpass after it (2-pole Butterworth) at about a third of the clock
    (600 Hz at the longest). The chip samples its input at the clock (a sample and
    hold), which leaves a little grit. A change of Time sweeps the clock there (about
    45 ms; at most 0.9 samples per sample longer and 8 shorter); a slow drift (0.3 Hz)
    keeps it alive. The chip's input clips softly (driven 1.2 times), Feedback goes up
    to a loop gain of 1.02, and a 40 Hz low cut keeps the feedback from drifting.
  - **"Gated" and Reverse** (`gated_dsp.h`): the mono send goes through three short
    allpass diffusers (37, 53, 71 samples) onto a delay line (the reverb's first
    buffer); 20 taps per side, spread unevenly over the gate length (different on each
    side), add up the burst, each at the gate's level at its place in the length (the
    attack, the hold and the release, or the swell for Reverse); each side is smeared
    by three more short allpasses (89, 131, 173 samples left; 97, 149, 197 right) into
    a dense wash. The diffusers are short and light so the burst still stops sharply
    (it rings on for about 50-60 ms). The taps are summed in integers, five at a time
    over the block.
- **Switching:** each effect keeps its memory in the stock effect's buffers, so a switch
  first clears them a piece per block (silent meanwhile); otherwise each one would play
  back what the other left there. Reverb: the two 32 KB buffers over 8 blocks, and a
  switch to the stock reverb also zeroes its three filter states. Delay: to Tape or
  BBD, the two lines (1 MB) over 32 blocks; to Stock, all three of the stock
  delay's 512 KB buffers over 48 blocks, and its filter states are zeroed.
- **Start-up:** whatever the project chose, the stock effects run until the Pattern
  screen first appears, because running Freeverb while the unit boots hangs the boot
  (the other effects are held to the same rule); then the project's choices switch in.
- **`masterfx.bin`:** 12 bytes in the project's folder: `HMF1`, the reverb (0 Stock,
  1 Freeverb, 2 Plate, 3 Spring, 4 Shimmer, 5 "Gated", 6 Reverse), the delay (0 Stock,
  1 Tape, 2 BBD), two zero bytes and a checksum. A file from before mf7 whose delay
  byte is 3 ("Gated" when it was a delay) keeps its reverb and plays the Stock delay.
  A missing or damaged file, or one naming an
  effect this firmware doesn't have, means Stock for both (and the file is left as it
  is). It is read and written only while the file manager is idle. A project with no
  folder yet (the new project "New Project", which has no path) writes no file; it gets
  one when it is first saved. The firmware's Save As copies only its own files, so the
  patch writes `masterfx.bin` to the new folder itself.
- **Memory:** the patch's RAM (816 bytes: the choices, and each effect's positions and
  filter states) is on-chip (the OCRAM tail, or the DTCM gap above the heap in a
  bundle). The Master FX folder and its items come from the firmware's heap.

The code is about 19.5 KB (built with `-O2`, as the effects run in the audio interrupt);
the patcher places it after the stock image or after the patches listed before it.

**CPU.** With Stock chosen the hooks add a few dozen instructions per block. In the
emulator, per block (128 samples):

| | Stock | masterfx |
|---|---|---|
| Reverb | about 34,000 | Freeverb 47,000; Plate 39,000; Spring 36,000; Shimmer 49,000; "Gated"/Reverse 42,000 |
| Delay | about 8,000 | Tape 19,000 (29,000 with PingPong); BBD 24,000 (37,000 with PingPong) |

The reverb and the delay run side by side, so a project's cost is its reverb's plus its
delay's. On the device, a deliberately heavy pattern (every drum hit and all three
synths, 8 voices each, sending fully to the reverb) showed 64–67 % average and 84 % peak
CPU with Freeverb, against 61 % and 84 % with the stock reverb. Firmware details are in
[docs/FINDINGS.md](../../docs/FINDINGS.md) (section 19).

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/masterfx/patch.json build/TrackerPlus_mf4.ptf
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
python tools/emu/test_masterfx.py build/TrackerPlus_mf4.ptf path/to/TrackerPlus_1.2.0.ptf out/fx_wav
```

It calls the reverb and delay objects' `update()` through their vtables, as the audio
engine does, with the audio block helpers stubbed (the delay's buffers, allocated at
runtime on the unit, are given). The patch's code holds a small table of where its state
is (`mt_mfx_ram_map`), which the tests read. It checks:

- **Stock:** the output equals stock firmware's, sample for sample, before the Pattern
  screen (even with another effect chosen) and after it with Stock chosen; for the
  delay over several Time, Feedback, Filter and PingPong settings.
- **Switching:** every switch gives silent blocks that clear the buffers, then the
  chosen effect, through all the reverbs (which all sound different) and all the
  delays. Choosing Stock again gives clear buffers and zeroed filter states; then the
  stock reverb is silent with silence in and has a stock impulse response, and the
  stock delay's impulse response is a fresh one's. An out-of-range choice counts as
  Stock; the start-up reset.
- **Each reverb (Freeverb, Plate, Spring, Shimmer):** the block handling (bypass,
  missing blocks, both blocks sent and released), amount 0 passes the send through,
  Size lengthens the tail, Damping darkens it, Predelay delays it by the right number of
  samples and Diffusion changes it, the two sides differ, the level value, and the
  loudness against the stock reverb (within 6 dB). The Shimmer's octaves (none with
  Diffusion at 0, most of the tail at the top), and at the top of every knob with loud
  noise, a bounded level and a tail that still dies away.
- **Tape and BBD:** echoes at Time and twice Time, each repeat about half the one before
  at Feedback 0.5, the first echo of a tone at the stock delay's level (within 3 dB),
  one echo at Feedback 0, sustained echoes and no NaN at Feedback 0.99, PingPong's
  right-then-left echoes, duller echoes with the Filter down, the pitch bend while Time
  changes and the return to pitch, silence after the tail, echoes carrying on without a
  send block, and the block handling (bypass, a missing output block); the Tape's wow
  and flutter on a steady tone, the BBD's steady pitch and its duller echoes at longer
  Times.
- **"Gated" and Reverse:** the burst lasts as long as Size sets and then stops (at 150
  and 300 ms), silence after it, Diffusion fading the end, Reverse swelling it up,
  Damping darkening it, Predelay moving it later, its level against the send (within
  8 dB), Amount 0 passing the send through, and the block handling.
- **Menu:** the stock menu constructor builds the folder and its two items, and the
  stock Apply stores every choice.
- **File:** `masterfx.bin` through the screens' update hooks with a fake SD card:
  per-project choices, one write a while after a change, damaged and unknown files, a
  project without a name; Save As and new projects through the firmware's own save
  request (`tools/emu/project_save.py`).
- **Cost:** instructions per block for each effect, and for a switch's clearing blocks.

With a folder given, it also writes the WAV files the samples here were cut from: the
same test sounds through every reverb (the return) and every delay (the dry sound with
the return added), and a drum loop through "Gated".

A stress test runs long randomized sessions (here 4 of 20,000 blocks, about a minute of
audio each) through the reverb, or with `--delay` through the delay: changing sends, the
knobs set to random values and the fields to garbage (NaN, infinities, out-of-range
values), bypass toggles, missing blocks, moved buffers, the Pattern-screen start, the
start-up reset and the effect choice (any of them, or a value out of range) at random.
A write watch fails the run if the patch writes anywhere but its own RAM, the effect's
buffers, the audio blocks, the effect's own fields it is meant to write or the stack; it
also checks each effect's state after every block, that blocks are released exactly
once, and that an effect sounds again once normal settings return:

```bash
python tools/emu/stress_masterfx.py build/TrackerPlus_mf4.ptf 20000 11 12 13 14
python tools/emu/stress_masterfx.py build/TrackerPlus_mf4.ptf 20000 11 12 13 14 --delay
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
Config → Firmware → the patched file. Config should show `1.2.0-mf7 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. Projects play with the stock
effects again; their `masterfx.bin` files stay in their folders and do nothing in stock
firmware (delete them if you like).

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
