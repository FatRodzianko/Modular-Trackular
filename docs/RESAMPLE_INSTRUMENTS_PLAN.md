# Plan: any instrument in the Sample Recorder

**Status:** shelved by the user (2026-10-31) after phase 1 (section 2.7): too many
interacting cases to change with confidence. Kept as a record; not built.
Original status: plan (2026-10-31), not built. An extension of `patch/resample` (`rs8`), as
`rs9`. Static analysis plus one emulator run (section 2.4); the rest is to be
checked in phase 1.

## 1. What the user gets

Today the Sample Recorder lets you pick only MIDI instruments (M01–M16): holding the
instrument button opens a MIDI-only list, and the pads and a MIDI controller play the
chosen channel. The goal is to pick any instrument there (sample instruments 1–48,
the three synths, MIDI) and play it live while you record. With Trk Record on, the
take then holds Line In, the pattern and what you play, all inside the unit.

- **Only with Trk Audio on.** Sample and synth instruments sound only when the
  recorder lets the tracker be heard, which is what Trk Audio does. With Trk Audio
  off the recorder stays stock: MIDI only.
- **Trk Record** decides, as now, whether what you hear is also recorded.
- The recorder keeps stock's habit of giving the instrument back when you leave:
  leaving restores the instrument that was selected before you entered.

## 2. Findings (1.2.0)

### 2.1 Why the list is MIDI only

The instrument button opens a picker through a per-screen callback, `(open)` with
r0 = 1, `(close)` with r0 = 0. The recorder's is `0x600d4675`, reached through the
pool word **`0x600fa4a8`** (one reference):

- **Open:** clamps `project+0x6c5` (the selected instrument, `0x202046c5`) to
  0x30–0x3f and calls `picker_open(0x20020880, mode 4, instrument)` (`0x600cf2b8`).
- **Close** (`0x600d46a2`): rebinds the recorder's buttons (`0x600f8e3c`), redraws
  (`0x600fc998`) and copies `project+0x6c5` into `scr+0xbe9` (`scr` = `0x20026100`).

The picker's mode sets the list (`0x600cd140`, instrument → row):

| Mode | List | Row | Used by |
|---|---|---|---|
| 2 | synths, samples, MIDI | synth 64–66 → 0–2; 0–63 → +3 | Pattern (`0x600d43fa`), Instrument screen (`0x600d4612`), `0x600d4662` |
| 4 | MIDI only | 48–63 → 0–15 | Sample Recorder |
| 5 | synths and MIDI | synth → 0–2; MIDI → −45 | not traced |

So the change is: open the picker in mode 2, without the clamp.

### 2.2 The recorder's own instrument

- **Start** (vtable start `0x600fa228`, hooked by resample as `mt_rs_start`): saves
  `project+0x6c5` in `scr+0xbe8`. If that is a MIDI instrument it becomes the
  recorder's (`scr+0xbe9`); otherwise the recorder takes its previous MIDI instrument
  (`scr+0xbe9`, clamped 0x30–0x3f, `0x600fa446`) and writes it to `project+0x6c5`.
  `scr+0xbe9` starts at 0x30 (`0x600fa932`).
- **Leave** (`0x600f8bd0`): `project+0x6c5` = `scr+0xbe8`, so the selection is given
  back.

With the feature, the start keeps whatever instrument was selected (after the stock
start, `mt_rs_start` writes `scr+0xbe8` back to `project+0x6c5` and `scr+0xbe9`).

### 2.3 How a live note plays

The pads (`0x600fabf6`, a `b.w` to the veneer) and MIDI in go to
`seq_record_live_note` (ITCM `0x15c8`). It plays the note on one of the sequencer's
tracks, with the selected instrument (`project+0x6c5`):

- It tries tracks from `seq[1]` on and takes the first that isn't muted and has no
  note sounding (`+0x80`). For a sample instrument (≤ 47) only tracks 1–8 qualify
  (the sample voices); for MIDI and synths all 16.
- **If none qualifies, the note is dropped.** With all 8 sample tracks sounding (a
  busy pattern with long samples), a live sample note doesn't play. Stock Pattern
  view behaves the same.
- The chosen track's voice plays the note, so the live note and that track's pattern
  notes share a voice: the next pattern note on that track cuts the live note.
- **With Pattern record mode on** (`0x20022770+0x254`, which stays set after you leave
  the Pattern screen) the test at ITCM `0x166a` looks only at `+0x81`, so the live
  note can take over a track whose note is still sounding: its instrument becomes the
  live one until that track's next step (the arp takeover, section 2.4).

### 2.4 The record-mode takeover (stock bug, emulator and hardware)

With an M01 Chord + Arp in the pattern and record mode on, a pad note with M02
selected takes over the arp's track: the arp continues on M02, at its original
pitches, until its step comes round again (user's hardware check, 2026-10-31; the
emulator shows the same on stock). With sample instruments the same takeover would
hit any sounding sample track.

Fix (emulator-tried on stock): after `bl 0x1faa8` at ITCM `0x166a`, record mode
counts only with the Pattern screen in front (screen manager `0x200207c0+8` =
`0x20021770`). Outside it nothing is recorded anyway (the write needs the Pattern
screen, `0x16f6`), so the only change is that live notes no longer take a sounding
track. Results: recorder with the fix, the M02 note plays once and the M01 arp runs
on; Pattern view unchanged. One `itcm_branches` entry (chained); no other patch hooks
that site (tracklen wraps the function from outside, at its veneers).

### 2.5 Hearing sample and synth notes in the recorder

The recorder's entry blocks the sample and synth note-ons (and bypasses the delay,
zeroes the sends); resample's "gates" undo that with the stock leave path's calls.
Today they open for Play in the setup and for a recording into the moved buffer.
For live playing they must also be open in the setup whenever Trk Audio is on and a
sample or synth instrument is selected. The memory rule stays: a recording that
can't be moved after the loaded samples (less than 10 s free) closes the gates
before it starts, so a sample instrument then goes silent for that take.

### 2.6 A take that fills its space (rs8, emulator, 2026-10-31)

The stop at the end of the moved space wasn't hardware-tested (no take that long). The
recorder's own check (`0x6010c5b8`: stop once fewer than 128 frames of room are left,
write position `REC+0x30`) stops it there: with 10 s left, and with Record Tracker
(Monitor off and on), the take stops at its size − 128 frames, nothing after the space
changes (also 5 blocks later), the loaded samples are kept, and the update then stops
the mix and the pattern. Stock behaves the same with its full-size buffer.
`test_resample.py` section 4 (`full_take_checks`). No hardware check of a full take:
the user declined it (2026-10-31); the emulator check stands as the coverage.

### 2.7 Phase 1 results (2026-10-31, static and emulator on MR8)

- **The full picker is safe in the recorder.** Modes 2, 4 and 5 bind the same buttons
  (`0x600cd178`: arrows `0x600cd715`..`0x600cd745` and the jog `0x600cd705`, all
  `picker_move` `0x600cd558`, and the pads `0x600cd469`); only mode 3 adds buttons 6/7.
  Mode 2's move (`0x600cd692`) only sets `project+0x6c5` (rows 0–0x42: synths, samples,
  MIDI). Its pads play a preview note through the live-note path (note 60 outside the
  Pattern screen, `0x600cd49c`; note-off through `0x600ac700`); mode 4's pads do
  nothing. No load or edit action: risk 1 of section 6 is cleared.
- **The start track** is `seq[1]`, the first track of the Pattern screen's selection
  (or the cursor cell; `seq_set_selection` `0x600aeee4`, `ed_selection_to_seq`
  `0x600e0b58`). `seq_record_live_note` first checks that `seq[0..3]` is a valid
  selection (`0x600aee10`), else it plays nothing.
- **A track counts as sounding from its note until a note-off or its next note**, not
  until the sample ends: in a take with 0.1 s samples on tracks 1–2, both keep `+0x80`
  = 1 for the whole bar. So with the strict rule (record mode off, or the takeover
  fix), a live note gets only a track that hasn't played a note yet.
- **Live notes in a take** (gates open, Shift+Rec), stock rule with record mode off:
  a sample note with tracks 1–5 used takes track 6 and is heard; from cursor track 12
  it wraps to track 6 too; a synth note takes track 6 and is heard. With notes on all
  8 sample tracks it is dropped. With record mode on (stock's takeover) it takes
  track 1, cutting that track's sound until its next note.

So decision 2 and 3 (section 4) trade against each other: the takeover fix keeps live
notes from disturbing the pattern, but then a pattern that uses all 8 sample tracks
leaves no track for a live sample note (silent). Back to the user.

## 3. The design (rs9)

| Hook | Where | What |
|---|---|---|
| `mt_rs_picker` (new) | pool word `0x600fa4a8` (`0x600d4675`) | Open with Trk Audio on: `picker_open(mgr, 2, instrument)`, no clamp. Otherwise, and for close, the chain (stock) |
| `mt_rs_start` (extended) | vtable start `0x60042c58` | With Trk Audio on: after the stock start, put the entry instrument (`scr+0xbe8`) back in `project+0x6c5` and `scr+0xbe9` |
| `mt_rs_update` (extended) | recorder update | Gates open in the setup while Trk Audio is on and the instrument is a sample or synth (close them when it goes back to MIDI or Trk Audio goes off, unless Play holds them open) |
| `mt_rs_live_track` (new) | `itcm_branches` ITCM `0x166a` (`bl 0x1faa8`) | Record mode counts only with the Pattern screen in front |
| Trk Audio off (existing toggle) | | If the instrument isn't MIDI, put the recorder's MIDI instrument back (`scr+0xbe9` clamped), as stock's start does |

RAM: none new. Version `rs9`; `patch/all` bumps.

## 4. Questions for the user

Answered 2026-10-31: the recommendations for all three (the full list only with Trk
Audio on; the takeover fix in resample; dropped notes accepted).

1. **Trk Audio as the switch** (1): the full list only with Trk Audio on. Alternative:
   always the full list, with sample and synth instruments silent while Trk Audio is
   off (confusing; not recommended).
2. **The takeover fix in resample** (2.4): it changes every non-Pattern screen with
   record mode on, not just the recorder (any screen whose pads play notes). It is a
   stock bug fix; the alternative is a separate patch, which needs a web patcher bit
   (the bit naming question is still open for chords).
3. **Dropped notes on a busy pattern** (2.3): accept stock's behaviour (recommended),
   or later pick a voice by another rule (a bigger change: voice stealing).

## 5. Phases

1. **Emulator checks before code** (scratch, stock): the mode-2 picker opened from
   the recorder draws and picks (EVE display list) and its close callback still runs;
   a live sample note in the recorder with the gates open sounds on a free track and
   is dropped with 8 busy tracks; a synth note sounds; nothing reads or writes the
   moved record buffer.
2. **Build rs9** with the hooks in section 3; `test_resample.py` extended: the picker
   mode by Trk Audio, the instrument kept and given back, the gates in the setup, a
   live sample and synth note in a Trk Record take (frame for frame against the
   jacks), the takeover fixed outside the Pattern screen and unchanged inside it,
   Trk Audio off = stock.
3. **Quick tier**, then the user's hardware check (about 10 minutes): pick a sample,
   a synth and a MIDI instrument in the recorder; play pads with and without the
   pattern; a Trk Record take with a live sample; record mode on, the arp case from
   2.4; leave and check the Pattern screen's instrument; Trk Audio off = MIDI only.
4. **Ship:** `prebuilt/`, `patch/all`, the web patcher, the docs, the release tier
   with `--update`; commit on the user's word.

## 6. Risks

- **The picker in mode 2 over the recorder.** Mode 2 may offer actions meant for the
  Pattern or Instrument screens (opening an instrument's editor, loading a sample).
  Loading a sample while the recorder holds the moved buffer could overwrite it. To
  check in phase 1; if so, the patch blocks those actions in the recorder or uses
  mode 5 plus the samples.
- **Saving a take over the instrument being played.** Stock's save stops nothing
  playing; to check that no voice reads a sample while its slot is replaced.
- **CPU:** live notes add voices the budget already assumes (all 8 sample voices,
  the synth voices).
