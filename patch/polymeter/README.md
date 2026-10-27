# polymeter: a length per track

> **In development: not released.** It hasn't been tested on hardware yet, and it isn't
> in patch/all or the web patcher. How it works and how it saves lengths may still change.

A patch for Polyend Tracker+ firmware 1.2.0 that lets each track of a pattern have its
own length. A track shorter than the pattern plays its own steps over and over, and every
track starts again together when the pattern ends: in a 16-step pattern a track of
length 6 plays steps 1–6, 1–6, 1–4, then the pattern starts again. Installed on its own,
Config shows the firmware version as `1.2.0-pm1`.

The idea comes from a custom firmware for the Tracker Mini shown on r/PolyendTracker.
This is a re-implementation from its description; nothing of it was seen or used.
[docs/POLYMETER_PLAN.md](../../docs/POLYMETER_PLAN.md) has the investigation and the plan.

**What this build does so far.** It plays the track lengths, sets them with the
Trk Len button, dims the steps past them in the grid, records live notes onto a short
track's own steps and saves the lengths with the project. Emulator-tested, alone and in
bundles with patch/all's patches in both orders. Not tested on hardware.

## Using it

On the Pattern screen, press More twice: the first free button reads **Trk Len**. That's
the third button on its own, the fourth with Humanize or Copy to New installed, and the
fifth with both (they keep theirs). Hold it and turn the encoder to change the length
of the track the cursor is on, in the current pattern.
While held, the button's second line shows the length, or `--` when the track plays the
whole pattern. Turning it up to the pattern's length sets it back to `--`. Released,
the encoder works as usual. It works in and out of record mode.

In the grid, a short track's steps past its length are drawn in the dim color stock uses
for an empty field: they stay in the pattern, but don't play. Muted tracks, the cursor
and a selection keep their own colors.

Recording while playing works as usual. On a short track a note (or note-off) lands on
the step the track is playing, not on a dimmed step: with length 6, a hit during the
pattern's step 11 goes to step 5.

The lengths are saved with the project, in `polymeter.bin` in its folder, a moment after
each change (and the file is removed when no length is left). Opening a project loads its
lengths; Save As takes them to the new name. A new project keeps them until its first
save. Stock firmware ignores the file: there every track plays the whole pattern.

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

The grid draws each cell's text through one function, called from 5 places (note,
instrument twice, FX1, FX2), after picking the cell's color. The patch hooks those 5
calls: from the text's address it finds the cell's track and row, and from the row the
step it shows. When that step is at or past the track's length and the cell has its
column's normal color, it draws the cell in the column's empty color instead.

Live recording has two writers: one for notes (reached through 8 small jumps in flash)
and one for note-offs (called from 18 places, 2 of them tail calls, which the patcher
writes as `b.w` with `"tail": true`). Each picks its row itself, so the patch wraps the
whole call. Before it, each short track's rows the call can reach (the position and the
3 after it) that lie past the length get the contents of the step they play, so stock's
rules (a note-off only onto an empty step) see the real step. After it, a row stock
changed is copied to that step, and the dimmed rows get their own contents back. The
copying is done with the audio interrupt masked; the call itself runs as in stock
firmware, which masks and unmasks the interrupt itself. With no lengths set the call goes
straight through.

The file is read and written from the Pattern and Config screens' update methods
(chained, as the other patches' files are), one read or write of the whole file per
update, in pieces of at most 256 bytes, only while the file manager is idle. It holds a
`PMT1` header, one 17-byte entry for each pattern that has a length (its number and the 16
lengths) and a checksum; at most 4,347 bytes. When another project opens, the lengths are
cleared at once and then read from its file, which is checked whole before any of it is
used: a damaged file gives no lengths. Save As is recognised by the save request's name
copy (`0x60114eaa`), as the other patches do, and the lengths are written to the new
folder.

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

For the grid it runs the stock grid code and compares every cell's color with stock
firmware drawing the same pattern with the short tracks' extra steps left empty: in all
three row settings, scrolled, with an overlay or a side panel open, in the single-column
views, and with the cursor and a muted track on dimmed steps.

For recording it compares with stock firmware recording the same hits onto a pattern with
the short tracks' first steps written out to the end: early and late hits (also on the
last step), note-offs (also onto a step holding a note), length 1, 128 steps, two short
tracks, a track without a length; every one of the 26 call sites; and that the patch's
own writes happen with the audio interrupt masked.

For the file it uses a fake SD card: a change written once a moment later, every pattern
with lengths written and read back, damaged files of every kind ignored, the file removed
when no length is left, nothing read while the file manager is busy, and Save As and new
projects as for the other patches' files.

## Reverting

Install the stock `TrackerPlus_1.2.0.ptf` (or your usual firmware) through Config →
Firmware.
