# legato: legato for MIDI instruments and synth slots

A patch for Polyend Tracker+ firmware 1.2.0. A Glide FX (`G`) on a MIDI-instrument note
or a synth note (S1–S3) makes that note play legato: the Tracker sends the new note-on
before the previous note's note-off, so a mono synth slides into it (legato
portamento) or keeps its envelopes going instead of retriggering. Installed on its own,
Config shows the firmware version as `1.2.0-lg2`.
[docs/LEGATO_ANALYSIS.md](../../docs/LEGATO_ANALYSIS.md) and
[docs/LEGATO_PLAN.md](../../docs/LEGATO_PLAN.md) have the investigation and the plan.

**Status:** emulator-tested, alone and in patch/all. Tested on hardware:
- MIDI (`lg1`, the same code for MIDI notes), on its own and with patch/all's mods: with
  an external synth whose filter envelope stays open on legato notes, steps without `G`
  retrigger the envelope and steps with `G` don't. Stock firmware does nothing with `G`
  on MIDI notes (no legato, no glide).
- Synth slots (`lg2`), on its own: with Glide Mode Legato or Legato O, `G` steps don't
  retrigger the envelopes; with Always or Overlap they do, exactly as when playing the
  synth from the pads or an external MIDI controller, in this firmware and in stock
  1.2.0 alike.
- `lg2` in patch/all (`1.2.0-ML2`) loads and runs as expected.

`G` sends no glide or portamento message: its value is ignored. A pitch slide comes
from the synth's own portamento or Glide Time, which the legato order then triggers.

## Using it

1. Put a note with a MIDI instrument (`M01`–`M16`) or a synth slot (`S1`–`S3`) on a
   step.
2. Put `G` (Glide) in FX1 or FX2 of that step. Its value doesn't matter.
3. Play. If the track's previous note is still sounding on the same kind of instrument
   (any MIDI instrument, or the same synth slot), the two notes are played legato.

Stock firmware ends the previous note first: note-off, then note-on, at the same moment.
That's why the usual workaround for MIDI is to alternate the notes between two tracks.
With the patch, a `G` step sends note-on, then note-off. Steps without `G` stay as in
stock firmware.

**Synth slots.** A synth keeps its envelopes going on a `G` note only when its slot has
**Polyphony 1** and **Glide Mode Legato or Legato O** (Instrument → Voice). The patch
doesn't change the glide itself: the synth's Glide Mode decides it, as when you hold
one pad and press the next (with Glide Time above 0, Polyphony 1):

| Glide Mode | Pitch glide without `G` | With `G` | Envelopes with `G` |
|---|---|---|---|
| Always | glides | glides | restart |
| Overlap | none (the notes never overlap) | glides (the notes overlap) | restart |
| Legato | glides | glides | go on |
| Legato O | none (the notes never overlap) | glides (the notes overlap) | go on |

This is how the synths themselves behave: in stock firmware too, holding one pad (or a
key on an external MIDI controller) and playing the next restarts the envelopes in
Always and Overlap and keeps them going in Legato and Legato O (checked on hardware).
A `G` step plays the two notes the way held pads do; it doesn't change the Glide Modes.
Overlap and Legato O glide only between overlapping notes, so they glide on a `G` step.
For legato without a glide, use Glide Time 0. With Polyphony above 1 the new note takes
another voice, and `G` changes nothing you can hear. This holds for ACD, FAT, VAP, WTFM
and WAVE (`patch/synths`). PERC ignores note-offs, so it sounds exactly the same with
`G` as without.

**When `G` doesn't change anything** (the order stays note-off, then note-on):
- the new note has the same pitch and instrument as the previous one: "on 60, off 60"
  would end the new note. For a held note, leave the next step empty: a note keeps
  sounding until the track's next note;
- the previous note has already ended: its Gate (`q`) ran out, or an OFF step ended it;
- either note has Chord (`0`) or Arp (`A`);
- the two notes are on different kinds of instrument (a MIDI instrument and a synth
  slot, or two different synth slots), or either is a sample instrument;
- the step has no note.

**With other FX:** Micro-move and the instrument's delay move both messages together.
Volume, CCs and Random note work as usual. A step that a Chance FX (or a condtrig
condition) skips sends nothing, as in stock firmware. Stop, mute and pattern changes
end notes as in stock firmware.

**In stock firmware**, `G` on a MIDI or synth note does nothing, so such patterns play
as before (without legato) and still show the `G`.

## How it works

| Hook | Where | What |
|---|---|---|
| `mt_lg_note_off` | ITCM `0x24da`: `bl seq_note_off_q` in `seq_process_track`, the previous note's note-off just before a new note plays, through an ITCM stub | For a legato step, keeps a copy of the previous note instead of ending it; otherwise ends it as stock firmware does |
| `mt_lg_trigger` | ITCM `0x2fd8`: `bl seq_trigger_note` in `seq_process_track`, the step's note, through an ITCM stub | Plays the note, then ends the kept note (for the same pitch, ends it first) |
| `mt_lg_seq_advance`, `mt_lg_render_advance` | `bl seq_advance` in `app_audio_block` (`0x600aa7f8`) and in the render loop (`0x600bc27a`), chained | A guard: after the sequencer has run, ends a kept note if one is left, so none outlives an audio block |

MIDI and synth notes take the same path through the sequencer, so both use these hooks.
The copy lives only between the two calls, in the same pass of the sequencer. The
patch's RAM is 16 bytes. It adds about 140 instructions per track to the busiest tick
(16 MIDI tracks with `G` on every note).

## Building and checking it

```bash
patch/legato/build.sh                     # optional: prebuilt/ is used without a build
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/legato/patch.json build/TrackerPlus_lg2.ptf
python tools/emu/test_legato.py build/TrackerPlus_lg2.ptf path/to/TrackerPlus_1.2.0.ptf
```

The test compares random patterns with stock firmware (identical without `G`; with `G`
the same messages at the same ticks, only reordered, and no more notes left on after
stop), checks the legato order and every case that keeps the stock order, the other FX,
stuck notes, two tracks on one channel, and the cost. For the synth slots it plays notes
through the sequencer into the real synth system and checks, from the rendered sound,
which settings keep the envelopes going (every model, and WAVE on firmware with
`patch/synths`), and that PERC's output doesn't change.

## Testing it on the device

1. Back up the SD card, and use a copy of a project. Keep the stock
   `TrackerPlus_1.2.0.ptf` in `/Firmware`.
2. Install `TrackerPlus_lg2.ptf` through Config → Firmware. Config shows `1.2.0-lg2`.
3. Connect a mono synth (DIN or USB) set to legato or fingered portamento. On one track,
   put notes on steps 1–4 with its MIDI instrument, and `G` on steps 2 and 3.
4. Play: steps 2 and 3 slide (or don't retrigger the envelope); step 4 retriggers.
5. The same with a synth slot at Polyphony 1 and Glide Mode Legato: steps 2 and 3 keep
   the envelopes going (with Glide Time above 0 they also glide), step 4 retriggers.
6. Stop in the middle of a `G` note, mute the track while it plays, and change pattern
   in song mode with `G` on the new pattern's first note: no notes hang.
7. Try `G` with Micro-move, after a gated note (`q`), and on the same pitch twice.
8. Open the project in stock firmware: it still plays, without legato.

## Reverting

Install the stock `TrackerPlus_1.2.0.ptf` (or your usual firmware) through Config →
Firmware. Patterns keep their `G` FX, which stock firmware ignores on MIDI and synth
notes.
