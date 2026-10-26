# polymeter: a length per track

A patch for Polyend Tracker+ firmware 1.2.0 that lets each track of a pattern have its
own length. A track shorter than the pattern plays its own steps over and over, and every
track starts again together when the pattern ends: in a 16-step pattern a track of
length 6 plays steps 1–6, 1–6, 1–4, then the pattern starts again. Installed on its own,
Config shows the firmware version as `1.2.0-pm1`.

The idea comes from a custom firmware for the Tracker Mini shown on r/PolyendTracker.
This is a re-implementation from its description; nothing of it was seen or used.
[docs/POLYMETER_PLAN.md](../../docs/POLYMETER_PLAN.md) has the investigation and the plan.

**Status: in development.** This build plays the track lengths and sets them with the
Trk Len button. It doesn't yet show them in the grid, record live notes into a short
track's loop, or save them: lengths are lost when the unit is switched off (the next
phases of the plan). Emulator-tested, alone and in bundles with patch/all's patches in
both orders. Not tested on hardware.

## Using it

On the Pattern screen, press More twice: the first free button reads **Trk Len**. That's
the third button on its own, the fourth with Humanize or Copy to New installed, and the
fifth with both (they keep theirs). Hold it and turn the encoder to change the length
of the track the cursor is on, in the current pattern.
While held, the button's second line shows the length, or `--` when the track plays the
whole pattern. Turning it up to the pattern's length sets it back to `--`. Released,
the encoder works as usual. It works in and out of record mode.

## How it works

The sequencer is left alone. Every track's position still counts the whole pattern, so
the pattern's end, song mode's next pattern, the metronome and the Pattern screen's play
row behave as in stock firmware. Only the reads of a track's steps change:

- each step, the sequencer copies the track's step into its work record (ITCM `0x2170`);
  for a track with its own length E the patch copies step (position mod E) instead.
  Every play mode goes through it: pattern loop, song mode, Play from a position, a
  looping selection, performance mode;
- Roll looks ahead for the next note to know how long it lasts (ITCM `0x33a2` and
  `0x3520`); for a short track the patch looks at the same steps mod E, up to the
  pattern's end.

A length counts when it is 1 to the pattern's length − 1; otherwise (none, or a length
the pattern has since been shortened below) the track plays as in stock firmware.

The button goes on page 3's first button that stock firmware and the other patches
leave free, as Copy to New does (buttons count from 0 here): button 2, or the one
after Humanize's (always button 2) and Copy to New's (the next free one) when they are
installed, so 2, 3 or 4. It reads whether the label code's calls for buttons 2 and 3
(`0x600e5884`, `0x600e5890`, the ones those two patches hook) still go to stock. That
comes from the firmware itself, so the result doesn't depend on the order the patches
were applied in. It binds the button with page 3's other buttons and gives it its
label, colors and second line. While it is held, the patch takes the Pattern screen's
encoder; at any other time the encoder goes straight to stock.

## Building and checking it

```bash
patch/polymeter/build.sh                    # optional: prebuilt/ is used without a build
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/polymeter/patch.json build/TrackerPlus_pm1.ptf
python tools/emu/test_polymeter.py build/TrackerPlus_pm1.ptf path/to/TrackerPlus_1.2.0.ptf
```

The test checks that without lengths the firmware plays exactly as stock, and that a
short track plays exactly what stock firmware plays for the same track with its first E
steps written out to the pattern's end: for several lengths, all 16 tracks at once, a
128-step pattern, Roll, Micro-move, Break, a sample instrument, Play from a position, a
looping selection, performance mode and song mode. It also checks Roll's look-ahead
step by step and the cost (at most 50 instructions per track and step).

For the button it runs the stock page-3 bind and label code: the bind, the labels, the
encoder while held (lengths, clamping, the second line), the encoder going back to stock
on release (also when the page changed or an overlay is open), and that without the
hold the encoder leaves the same RAM as stock firmware.

## Reverting

Install the stock `TrackerPlus_1.2.0.ptf` (or your usual firmware) through Config →
Firmware.
