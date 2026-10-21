# condtrig: conditional trigs

A patch for Polyend Tracker+ firmware 1.2.0 that gives the Chance FX (C) conditions on
how many times the pattern has played, so a step can play only on the first pass, on
every second pass, on the third of every four, and so on. Installed on its own, Config
shows the firmware version as `1.2.0-ct1`.

The idea comes from **Psygoo**'s
[Tracker+ Conditional Trigs](https://github.com/Psygoo/TrackerPlus-CT-Web/) (CT6). This
is a re-implementation from its behaviour; no code is shared.
[docs/CONDITIONAL_TRIGS_PLAN.md](../../docs/CONDITIONAL_TRIGS_PLAN.md) has the
investigation and the plan.

**Status:** emulator-tested, alone and in bundles with colorsong, humanize,
interpolate, masterfx, swing and visualizer in both orders. Tested on hardware on its
own, through the checklist below.

## Using it

1. Put a Chance FX (C) on a step, in FX1 or FX2, as in stock firmware.
2. Turn the encoder on its value. Past 100 it goes on to `1st`, `!1s`, `1:2`, `2:2`,
   `1:3`, `2:3`, `3:3`, `1:4` … `8:8`, and stops there. Turning left goes back to 100 and
   the percentages. The pattern shows the condition in the value cell (for example
   `C1:4`).
3. Play. The step plays only on the passes its condition picks (below).

Only the encoder enters conditions. The pads, Fill, random fill and recording give
percentages (0–100), as in stock firmware.

## The conditions

Chance values 0 to 100 are percentages, as in stock firmware. Above 100 they are
conditions on the track's pass count n, which is 0 on the first pass:

| Value | Shown | Plays when |
|---|---|---|
| 101 | `1st` | n = 0: only the first pass |
| 102 | `!1s` | n ≥ 1: every pass but the first |
| 103–137 | `1:2`, `2:2`, `1:3` … `8:8` | n mod B = A − 1 for A:B: the A-th pass of every B |

A step whose condition doesn't hold is skipped as a failed Chance is: no note and none
of the step's FX. A step with two Chance FX plays only when both pass, so `1:4` and
C 50 together play on half of every fourth pass.

**How passes are counted.**
- **Per track.** A track starts a new pass each time it starts its loop again: at the
  end of the pattern, after a Break FX, at the end of a looping selection, and at its
  own length and play mode in performance mode.
- **Back to 0** whenever playback starts (Play, Play again while playing, a count-in,
  Preview, rendering), and when another pattern starts playing.
- **Song mode.** A pattern repeated in the next song slot keeps counting, so four slots
  of the same pattern can play `1:4`, `2:4`, `3:4` and `4:4` steps in turn.

These differ from CT6: CT6's values 102 to 107 (its 1:2, 2:2, 1:4, 2:4, 3:4, 4:4) mean
other conditions here, and CT6 counts one pass for all tracks together.

**In stock firmware**, values above 100 play on every pass, and the pattern shows the
number. Touching such a value with the encoder makes it C 100.

## How it works

| Hook | Where | What |
|---|---|---|
| `mt_ct_chance` | ITCM `0x30ce`: `bl rand_range_q` in the Chance FX check of `seq_process_track`, through an ITCM stub that passes the FX column (r7) and the track (r8) | For values 101–137, returns 0 (plays) or a number above any value (skipped) by the track's pass count; for anything else, calls `rand_range_q` as stock firmware does |
| `mt_ct_advance_step` | the word at `0x601acf00` (chained): the ITCM image's jump to `seq_advance_step` | After a track ends a step, its pass count goes up when it starts its loop again (track `+0x8a` back to 0), back to 0 when its own queued pattern starts; after track 16, all counts back to 0 when another pattern plays (`project+0x6c8` changed) |
| `mt_ct_seq_advance`, `mt_ct_render_advance` | `bl seq_advance` in `app_audio_block` (`0x600aa7f8`) and in the render loop (`0x600bc27a`), chained | Before the sequencer runs: all counts back to 0 when playback has just started |
| `mt_ct_count_in` | ITCM `0x3752`: `bl seq_play_from` at the end of a count-in, through an ITCM stub | All counts back to 0, then playback starts |
| `mt_ct_cell_max` | `bl fx_max` (veneer `0x600af2c8`) at `0x600af1b2` in `seq_change_selection_fx_value`, the encoder edit of FX values (chained) | For Chance, the highest value is 137 (`8:8`) instead of 100; the edit sets min(highest, value + turn), so turning goes on into the conditions in order. Every other FX type keeps its stock limit |
| `mt_ct_fx1_text`, `mt_ct_fx2_text` | `bl fx_value_text_cell` at `0x600df748` (FX1) and `0x600df77c` (FX2) in the pattern grid's row fill (chained) | A Chance value of 101–137 shows as its condition's name (3 characters); everything else as in stock firmware |
| `mt_ct_play_from_a`…`_d` | the literals of the four flash veneers to `seq_play_from` (`0x600ac604`, `0x600ac694`, `0x600acd34`, `0x600ad544`), chained | The same, for playback started from the main loop (also while already playing) |

Only the audio interrupt changes the counts; the main loop only asks for them to go
back to 0. The patch's RAM is 48 bytes. With conditions on every track, the sequencer
runs fewer instructions than stock firmware with Chance percentages, as a condition
needs no random number.

No other screen shows step FX values: the Fill popup shows its from and to values with
the same text function, but only percentages reach it.

## Building and checking it

```bash
patch/condtrig/build.sh                     # optional: prebuilt/ is used without a build
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/condtrig/patch.json build/TrackerPlus_ct1.ptf
python tools/emu/test_condtrig.py build/TrackerPlus_ct1.ptf path/to/TrackerPlus_1.2.0.ptf
```

The test plays every condition in both FX columns, checks what a skipped step drops
(its note, the CC in its other column, a Micro-move's delayed note, sample and synth
notes), the counting in every play mode and the resets, the encoder and the grid text,
that Chance 0–100 plays exactly as in stock firmware, and the cost.

## Testing it on the device

1. Back up the SD card, and use a copy of a project. Keep the stock
   `TrackerPlus_1.2.0.ptf` in `/Firmware`.
2. Install `TrackerPlus_ct1.ptf` through Config → Firmware. Config shows `1.2.0-ct1`.
3. On a 4-step pattern, put notes with `1st` on step 1, `1:2` on step 2, `2:2` on step
   3 and `1:4` on step 4. Check the cells show those names.
4. Play: step 1 only on the first pass, step 2 on passes 1, 3, 5…, step 3 on passes 2,
   4, 6…, step 4 on passes 1, 5, 9…. Stop and play again: it starts over. The same with
   Record held, then Play (count-in), and with Preview.
5. Song mode: the same pattern in two slots in a row, then another: the second slot
   carries on counting (step 1 silent, step 3 plays), the other pattern starts over.
6. Check a few percentages (C 50) still skip about half the time.

## Reverting

Install the stock `TrackerPlus_1.2.0.ptf` (or your usual firmware) through Config →
Firmware. Patterns keep their conditions as Chance values above 100, which stock
firmware plays on every pass.
