# legato: legato for MIDI instruments

A patch for Polyend Tracker+ firmware 1.2.0. A Glide FX (`G`) on a MIDI-instrument note
makes that note play legato: the Tracker sends the new note-on before the previous
note's note-off, so a mono synth slides into it (legato portamento) or keeps its
envelope going instead of retriggering. Installed on its own, Config shows the firmware
version as `1.2.0-lg1`.
[docs/LEGATO_ANALYSIS.md](../../docs/LEGATO_ANALYSIS.md) and
[docs/LEGATO_PLAN.md](../../docs/LEGATO_PLAN.md) have the investigation and the plan.

**Status:** emulator-tested, alone and in bundles with all of patch/all's patches in
both orders. Tested on hardware, on its own and together with patch/all's mods: with an
external synth whose filter envelope stays open on legato notes, steps without `G`
retrigger the envelope and steps with `G` don't, and the other mods work as before.
Stock firmware was checked to do nothing with `G` on MIDI notes (no legato, no glide).

`G` sends no glide or portamento message: its value is ignored. A pitch slide comes
from the synth's own portamento, set to legato (fingered) mode, which the legato order
then triggers.

## Using it

1. Put a note with a MIDI instrument (`M01`–`M16`) on a step.
2. Put `G` (Glide) in FX1 or FX2 of that step. Its value doesn't matter.
3. Play. If the track's previous note is still sounding, the two notes are played
   legato.

Stock firmware ends the previous note first: note-off, then note-on, at the same moment.
That's why the usual workaround is to alternate the notes between two tracks. With the
patch, a `G` step sends note-on, then note-off. Steps without `G` stay as in stock
firmware.

**When `G` doesn't change anything** (the order stays note-off, then note-on):
- the new note has the same pitch and instrument as the previous one: "on 60, off 60"
  would end the new note. For a held note, leave the next step empty: a MIDI note keeps
  sounding until the track's next note;
- the previous note has already ended: its Gate (`q`) ran out, or an OFF step ended it;
- either note has Chord (`0`) or Arp (`A`);
- either note isn't a MIDI instrument (samples and the synth slots S1–S3; the synths
  have their own Glide mode with a Legato option);
- the step has no note.

**With other FX:** Micro-move and the MIDI instrument's delay move both messages
together. Volume, CCs and Random note work as usual. A step that a Chance FX (or a
condtrig condition) skips sends nothing, as in stock firmware. Stop, mute and pattern
changes end notes as in stock firmware.

**In stock firmware**, `G` on a MIDI note does nothing, so such patterns play as before
(without legato) and still show the `G`.

## How it works

| Hook | Where | What |
|---|---|---|
| `mt_lg_note_off` | ITCM `0x24da`: `bl seq_note_off_q` in `seq_process_track`, the previous note's note-off just before a new note plays, through an ITCM stub | For a legato step, keeps a copy of the previous note instead of ending it; otherwise ends it as stock firmware does |
| `mt_lg_trigger` | ITCM `0x2fd8`: `bl seq_trigger_note` in `seq_process_track`, the step's note, through an ITCM stub | Plays the note, then ends the kept note (for the same pitch, ends it first) |
| `mt_lg_seq_advance`, `mt_lg_render_advance` | `bl seq_advance` in `app_audio_block` (`0x600aa7f8`) and in the render loop (`0x600bc27a`), chained | A guard: after the sequencer has run, ends a kept note if one is left, so none outlives an audio block |

The copy lives only between the two calls, in the same pass of the sequencer. The
patch's RAM is 16 bytes. It adds about 140 instructions per track to the busiest tick
(16 MIDI tracks with `G` on every note).

## Building and checking it

```bash
patch/legato/build.sh                     # optional: prebuilt/ is used without a build
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/legato/patch.json build/TrackerPlus_lg1.ptf
python tools/emu/test_legato.py build/TrackerPlus_lg1.ptf path/to/TrackerPlus_1.2.0.ptf
```

The test compares random patterns with stock firmware (identical without `G`; with `G`
the same messages at the same ticks, only reordered, and no more notes left on after
stop), checks the legato order and every case that keeps the stock order, the other FX,
stuck notes, two tracks on one channel, and the cost.

## Testing it on the device

1. Back up the SD card, and use a copy of a project. Keep the stock
   `TrackerPlus_1.2.0.ptf` in `/Firmware`.
2. Install `TrackerPlus_lg1.ptf` through Config → Firmware. Config shows `1.2.0-lg1`.
3. Connect a mono synth (DIN or USB) set to legato or fingered portamento. On one track,
   put notes on steps 1–4 with its MIDI instrument, and `G` on steps 2 and 3.
4. Play: steps 2 and 3 slide (or don't retrigger the envelope); step 4 retriggers.
5. Stop in the middle of a `G` note, mute the track while it plays, and change pattern
   in song mode with `G` on the new pattern's first note: no notes hang.
6. Try `G` with Micro-move, after a gated note (`q`), and on the same pitch twice.
7. Open the project in stock firmware: it still plays, without legato.

## Reverting

Install the stock `TrackerPlus_1.2.0.ptf` (or your usual firmware) through Config →
Firmware. Patterns keep their `G` FX, which stock firmware ignores on MIDI notes.
