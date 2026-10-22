# visualizer: a pad visualizer

A patch for Polyend Tracker+ firmware 1.2.0. While a pattern plays, the 48 pads can show
an animation driven by the music: dim smoke drifting slowly across the pads, with soft
ripples swelling and spreading out from the notes that play. It uses brightness only, as the pads
are single colour. Installed on its own, Config shows the firmware version as
`1.2.0-vz5`.

**Status:** tested on hardware.

## Using it

- **On:** while a pattern plays in the Pattern screen with record off, hold **Note**,
  **Instrument**, **FX1** and **FX2** together. The column that was selected before you
  pressed them stays selected, and none of the four's long-press actions fire.
- **Off:** press any of the four (or all four) again. That press does nothing else, and
  the column stays as it was. The pads go back to what stock firmware shows.
- **When it shows:** only where stock firmware lights the pads during playback: playing,
  in the Pattern screen, record off, not in performance mode. Stop, switch record on,
  start performance mode or go to another screen, and the pads are stock again. Hold
  **Pattern** (or another bottom-row button that shows its own pads) and the pads show
  what stock firmware shows there, the patterns; let go and the visualizer is back. It
  stays switched on, so it comes back when you return to playing in the Pattern
  screen. A press of one of the four while it's hidden switches it off and works as
  usual.
- **Brightness** follows the pads brightness setting.

What drives it:

- **Notes** set where a ripple starts. The column comes from the note in the octave, the
  row from the octave. A new ripple starts at most every half beat, and only when one of
  the 12 has faded out, so a busy pattern gives a steady flow, and no ripple is cut
  short. The starting points drift sideways over time, and the ripples
  wrap around from one side to the other.
- **Instrument** sets how fast the ripple spreads (about half a pad to one and a quarter
  pads a second at 120 BPM). A ripple fades in over half a second and lasts 4.5 to 6.3
  seconds.
- **Track** sets its shape: a ring, a ring with a glow inside, or two rings.
- **Velocity FX** (V) sets how bright it is.
- **Other FX** set how wide the ring is and how stretched sideways, by their type and
  value.
- **The smoke** is three slow waves bent into each other. Every step that plays (note
  or FX) nudges it, and it eases towards the nudge over a few seconds. It moves with the
  tempo.
- **Overlaps** glow brighter but are limited softly, so they don't flatten into full
  brightness.

**The same take looks the same.** Everything starts again when playback starts and
follows only the music and the sequencer's own clock. So a fresh play of a pattern
looks the same each time, to within a level on a few pads, as a step can land one audio
block (2 ms) apart. The smoke and the drift keep their history, so the second pass of a
loop looks different from the first, and so does a pattern played after other ones in
song mode.

## How it works

| Hook | Where | What |
|---|---|---|
| `mt_vz_seq_advance` | `bl seq_advance` in `app_audio_block` (`0x600aa7f8`, chained) | Once per audio block: at a take's start, resets the animation. While playing, advances its clock by the block's length and eases the smoke |
| `mt_vz_advance_step` | the word at `0x601acf00` (chained): the ITCM image's jump to `seq_advance_step`, copied to ITCM `0x1f81c` at boot | When a track moves to a step: reads the step from the playing pattern (muted tracks skipped) and turns its note into a ripple, and its note, instrument and FX into a nudge of the smoke |
| `mt_vz_set_pad` | the pad LED object's one vtable entry, setPad (`0x60038298`, chained) | While the visualizer shows, drops the firmware's own pad writes. The firmware's pad manager still keeps its levels, so the stock pads come back exactly as they should |
| `mt_vz_pattern_update` | Pattern screen vtable slot `0x600403dc` (chained) | Shows or hides it (it hides while the screen's pad mode at +`0x1354`, set while Pattern or another bottom-row button is held, isn't −1), and draws a frame every 30 ms while it shows |
| `mt_vz_flush` | the pad LED driver's one vtable entry, its flush (`0x600373b8`, chained) | While the visualizer shows, sends only the brightness bytes that changed (see below) |
| `mt_vz_note`, `mt_vz_instrument`, `mt_vz_fx1`, `mt_vz_fx2` | the Pattern screen's column button callbacks (`0x600df2dc`–`0x600df2e8`, chained) | The four-button switch |

- **The pads:** the pad LED driver (an IS31FL3731) maps the 32 brightness levels through
  the firmware's gamma table. The visualizer writes through the original setPad, and
  only the pads whose level changed.
- **Keeping the screen fast:** the driver sends the pads to the chip from the main loop
  (the same loop that draws the screen), over I2C at 400 kHz, and waits for it. Its
  stock flush first reads back and checks 13 of the chip's control registers, one
  transaction each, then sends all 180 bytes: about 28 transactions and 260 bytes. That
  suits the stock pads, which change now and then, but at a frame every few tens of ms
  it kept the main loop busy enough to make the screen lag. So while the visualizer
  shows, every pad stays enabled (dark is brightness 0), and the flush sends only the
  run of brightness bytes that changed, in one burst after the page select: two
  transactions. In the emulator that keeps the pad bus busy about 4% of the time,
  against about 23% for the stock flush at the same frame rate. The first flush after
  the visualizer appears, every flush while it's hidden, and the flush after a failed
  write are the stock one.
- **Switching on:** with two or more of the four held while it could show, the screen's
  "held" marks are cleared, so no long-press action fires. The fourth press switches
  it on and is not passed on. If the first presses changed the column, the patch then
  calls the button of the column from before, which selects it again.
- **Switching off:** while it shows, a press of any of the four switches it off and is
  not passed on. Nor are further presses, until all four are up. Their releases aren't
  passed on either.
- **Leaving the screen:** if the Pattern screen stops updating for 200 ms, the next
  firmware pad write gives the pads back first.
- **Memory:** 560 bytes of RAM, on-chip (the animation, 12 ripples, the button state,
  which pad bytes changed); 3,620 bytes of code.
- **CPU:** a few dozen instructions per audio block and per step. Each 20 ms frame
  draws 48 pads from three sine waves and up to 12 ripples, which is well under 0.1 ms.

## Build

No compiler needed: the compiled patch is in `prebuilt/`. See
[docs/PATCHING.md](../../docs/PATCHING.md) for getting and checking the stock firmware.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/visualizer/patch.json build/TrackerPlus_vz5.ptf
```

To combine it with other patches, list `../visualizer/patch.json` in a bundle (see
"Choosing patches" in [docs/PATCHING.md](../../docs/PATCHING.md)). Its hooks are all
chained, so it combines with all the other patches here.

To build the patch code from source, you need the Arm GNU Toolchain
(`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN` set to its `bin` folder):

```bash
patch/visualizer/build.sh
```

### Emulator test

Needs `pip install unicorn capstone numpy`:

```bash
python tools/emu/test_visualizer.py build/TrackerPlus_vz5.ptf
```

It runs the sequencer as the audio engine does (the patched `seq_advance` call, 2 ms at
a time) on a test pattern, calls the Pattern screen's update every 10 ms, and reads what
the pads show back from the real LED driver's PWM frame. It checks:

- **Stopped:** the four buttons work as in stock firmware.
- **Switching on and off:** the fourth press switches it on without being passed on, no
  long-press mark is left, and the column from before is selected. A press switches it
  off and isn't passed on, nor is the rest of that chord. Then the buttons work again.
- **The pads:** they animate within the brightness setting, gradually (no pad changes
  by more than 2 of the 32 levels from one 20 ms frame to the next). The firmware's own pad
  writes are dropped while it shows and come back when it stops, including the ones
  made meanwhile.
- **Hiding:** record on, Pattern held, performance mode, stopping and leaving the screen.
- **I2C:** a fake bus under the LED driver logs every transaction and keeps what the
  chip would hold. While it shows, each flush after the first is at most two writes
  with no reads, the chip ends up with exactly the driver's frame and every pad enabled,
  and the bus is busy a small share of the time; hidden, the stock flush runs.
- **Repeatable:** two fresh plays give the same frames (to a level), and the second
  pass of the loop gives different ones.

## Install and revert

Install as described in [docs/PATCHING.md](../../docs/PATCHING.md): copy the patched
`.ptf` to `/Firmware` on the SD card next to the stock `TrackerPlus_1.2.0.ptf`, then
Config → Firmware → the patched file. Config should show `1.2.0-vz5 Build 1504`.

To revert: Config → Firmware → `TrackerPlus_1.2.0.ptf`. The visualizer keeps nothing on
the card.

If the unit freezes, pull the power, boot, and revert through Config. If it doesn't
boot, or Config itself doesn't work, put only the stock `TrackerPlus_1.2.0.ptf` in
`/Firmware` (from a computer), power on with the card in, and press the pinhole button
next to the USB-C port. The bootloader reinstalls the stock firmware.
