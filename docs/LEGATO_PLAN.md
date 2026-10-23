# Legato for MIDI instruments: plan

Status: phase 1 built and emulator-tested (`patch/legato`, `tools/emu/test_legato.py`,
`tools/test_layout.py` scenarios: alone and with patch/all's patches in both orders);
the patch README is written (phase 2's root `README.md` and `docs/PATCHING.md` entries
are not). Tested on hardware, alone (`lg1`) and with patch/all's mods (`MLG`): an
external synth's filter envelope retriggers without `G` and stays open with `G`, and
the other mods work. Stock firmware confirmed to ignore `G` on MIDI notes, and to allow
it there. Firmware 1.2.0. The investigation is in
`docs/LEGATO_ANALYSIS.md`; the firmware facts are in FINDINGS section 24. Chosen
design: a Glide FX (`G`) on a MIDI-instrument note plays that note legato (option B1 in
the analysis). Instruments are numbered as the pattern stores them: MIDI instruments are
48–63 (channels 1–16; the manual calls them 51–66).

## 1. What it does

- Put `G` (Glide), any value, in FX1 or FX2 of a step with a note on a MIDI instrument.
- When that note plays while the track's previous note is still sounding, the Tracker
  sends **the new note-on first, then the old note's note-off**. A mono synth with
  legato or fingered portamento then slides or keeps its envelope going instead of
  retriggering.
- Without `G`, nothing changes: note-off, then note-on, as in stock firmware.
- Stock order is kept when:
  - the new note has the same pitch and channel as the old one (a legato "on 60, off 60"
    would end the new note; for a tie, leave the step empty, as in stock firmware);
  - the old note isn't sounding any more (its gate `q` ended: track `+0x80` is clear),
    since there is nothing to hold;
  - either note has Chord (`0`) or Arp (`A`);
  - either note isn't a MIDI instrument (samples and the synth slots S1–S3 keep stock
    behaviour; the synths have their own Glide mode with a Legato option).
- Everything else is stock: Micro-move and the instrument's MIDI delay move both
  messages together; a failed Chance or condtrig condition plays nothing; stop, mute,
  OFF/CUT/FADE steps and gates send their note-offs as before.
- The `G` value isn't used (reserved for later, e.g. a portamento time sent as a CC).
- No Config items, no settings file, no pattern format change.

Installed on its own, Config shows `1.2.0-lg1`.

## 2. Hooks

| Site | Kind | Shared? | Hook |
|---|---|---|---|
| ITCM `0x24da` `bl seq_note_off_q` (flash `0x6018fbbe`, old `fef763fe`) | `itcm_branches`, no `args`, `next` | no | `mt_lg_note_off(seq, t, last, mode)` |
| ITCM `0x2fd8` `bl seq_trigger_note` (flash `0x601906bc`, old `fef760fa`) | `itcm_branches`, no `args`, `next` | no | `mt_lg_trigger(seq, t, work)` |
| `0x600aa7f8` `bl seq_advance` (audio block, old `00f02ef8`) | branch, chained | condtrig, interpolate, swing, visualizer | `mt_lg_seq_advance`: `next`, then the guard flush |
| `0x600bc27a` `bl seq_advance` (render loop, old `00f02df8`) | branch, chained | the same | `mt_lg_render_advance`: the same |
| `0x600cbf9c` | version word | all | `1.2.0-lg1` |

Without `args` the patcher's 8-byte stub doesn't touch r0–r3, so both hooks get the
stock call's own arguments (FINDINGS 24, checked in the emulator: at `0x24da` r2 is
`seq + 0x68·t + 0x68`, the last record; at `0x2fd8` r2 is `seq + 0x68·t + 0x6f`, the work
record).

**`mt_lg_note_off(seq, t, last, mode)`** (ITCM `0x24da`; runs only when the track has
played a note and a new note is about to play):
1. Flush any copy left from an earlier call (section 3).
2. Legato applies when all hold:
   - `t` < 16;
   - the work record (`seq + 0x68·t + 0x6f`: note, instrument, FX1 type, FX1 value, FX2
     type, FX2 value, volume) has instrument 48–63 and FX type 19 in FX1 or FX2;
   - the last record has instrument 48–63;
   - neither record has FX type 14 (Chord) or 21 (Arp) in either column;
   - the gate is still open: track `+0x80` ≠ 0.
3. If so: copy the 7-byte last record into patch RAM with the track number, and return
   without calling `next`. Stock code then goes on as after the note-off (it clears
   `+0x7f`, `+0x80` and the roll and arp state).
4. Otherwise: `next(seq, t, last, mode)`.

**`mt_lg_trigger(seq, t, work)`** (ITCM `0x2fd8`):
1. No copy, or a copy for another track: flush it, then `next(seq, t, work)`.
2. A copy for this track:
   - same pitch and channel as the work record (the note checked here, after Random
     note and performance transpose have changed it): `seq_note_off_q(seq, t, copy, −4)`
     first, then `next`; stock order.
   - otherwise `next(seq, t, work)`, then `seq_note_off_q(seq, t, copy, −4)`.
   - clear the copy.

`seq_note_off_q` is called at its ITCM address (`0x11a5`) from flash code, through a
function pointer, as condtrig calls `rand_range_q`.

## 3. State and the guard

- `.mt_ram`, magic-initialized, any region (not used before `main`): magic, the copy's
  track (`0xff` = none) and the 7-byte record. 16 bytes. Garbage at power-on can't send
  anything: without the magic the state is reset to "no copy" first.
- Only the sequencer touches it: both hooks and the guard run inside `seq_advance`, in
  the audio interrupt or the render loop.
- **The guard.** A copy is made at `0x24da` and sent at `0x2fd8` in the same
  `seq_process_track` call. The emulator found no path that skips the trigger: the
  cases in the analysis (section 5), and a scratch fuzz run of 60 random patterns on
  tracks 9–16 with all FX types and random values: 290 deferred note-offs, none left
  over, no more notes left on after stop than stock, and the registers at both sites as
  described in section 2.
  Still, any leftover copy is sent:
  - at the next `mt_lg_note_off` or `mt_lg_trigger` call, and
  - at the end of every audio block and render block (`mt_lg_seq_advance` /
    `mt_lg_render_advance` after `next` returns).

  So no copy outlives one audio block (about 2.9 ms), and stop can't strand one: stop
  runs from the main loop, between blocks. While rendering, MIDI out is suppressed by
  stock code; the flush just clears the copy.

## 4. Conflicts and chaining

| Patch | Shared sites | Interaction |
|---|---|---|
| condtrig | `0x600aa7f8`, `0x600bc27a` (chained) | A failed condition skips both legato sites, as stock Chance does: nothing deferred. Different ITCM sites (`0x30ce`, `0x3752`) |
| swing, visualizer, interpolate | `0x600aa7f8` (all), `0x600bc27a` (swing, interpolate) | None. The guard only acts after `next` |
| humanize | none | `G` takes an FX slot: on a legato step humanize adds only one of Micro-move and Volume (it fills the first free slot). Micro-move and Volume work with legato |
| synths | none | Synth instruments 64–66 stay stock |
| colorsong, masterfx, copypattern | none | none |

ITCM tail: two more 8-byte stubs (condtrig has two). The order in a bundle doesn't
matter.

**Compatibility.**
- Stock firmware with such a pattern plays the `G` steps normally (off, then on), shows
  `G nn` and keeps the bytes. Removing the mod loses only the legato.
- Existing projects that already have `G` on MIDI notes (it did nothing before) start
  playing those notes legato.

## 5. RAM, flash, CPU

- RAM 16 bytes; ITCM 16 bytes of stubs; flash an estimated 400–600 bytes.
- CPU: a few tens of instructions per played MIDI note (two record reads at `0x24da`,
  one check at `0x2fd8`) and a byte test per audio block. Measured in phase 1 against
  stock, with a note on every track and step.

## 6. Emulator test plan (`tools/emu/test_legato.py <patched.ptf> <stock.ptf>`)

Like `test_condtrig.py`: the real sequencer through `seq_timing`, MIDI port fakes that
record each message and its tick, the patched `seq_advance` call run as the audio block
runs it. Code and RAM locations come from `layout.py`, so it works on the single build
and on bundles. PASS/FAIL lines and a failure count.

1. **Sites.** ITCM `0x24da` and `0x2fd8` BL to their stubs after the boot copy; the
   stubs' literals are the hooks; the rest of the ITCM image is stock.
2. **No `G`: identical to stock**, message for message and tick for tick, on patterns
   with every FX type 1–42 and random values (the fuzz above, against the stock `.ptf`),
   USB, DIN and both.
3. **Legato order.** With `G` in FX1 and in FX2, any value (0, 50, 100, 255): on, then
   off, at the same tick, for adjacent steps, after empty steps, across a song-mode
   pattern change, on a looping 2-step pattern, with a channel change.
4. **Stock order kept:** same pitch and channel; old note gated (`q` 50, `q` 100) or
   rolled; Chord or Arp in either record; a sample or synth instrument in either record;
   `G` on a step without a note.
5. **Together with other FX:** Micro-move (both messages at the delayed micro-step),
   instrument MIDI delay, Volume (velocity unchanged), Random note (same-pitch check after
   the random note), performance transpose, CC FX in the other column (still sent at
   micro-step 1), Chance C 0 / C 100.
6. **No stuck notes.** After stop, and after 50 random patterns: the notes left on per
   channel and pitch are never more than stock's. The guard: a copy planted by the test
   is sent at the end of the next audio block, and by the next hook call.
7. **Two tracks on one channel**: stock cross-track behaviour unchanged.
8. **Cost**: instructions per micro-step with 16 busy MIDI tracks, `G` on every step,
   against stock; within a few percent.
9. **Bundles**: with condtrig in a bundle, condtrig's test passes and this one passes
   with a condition on a legato step.

## 7. Build phases

1. **Patch and tests.** `patch/legato/` (`legato.c`, `link.ld`, `build.sh`,
   `patch.json`, `prebuilt/`), `tools/emu/test_legato.py` (all of section 6), and
   `tools/test_layout.py` scenarios: legato alone, after and before patch/all's
   patches, and with condtrig in both orders.
2. **Docs.** `patch/legato/README.md` (what it is, using it, the stock-order cases, how
   to build and revert), root `README.md` (mod list, layout table), `docs/PATCHING.md`
   (status table, commands).
3. **Hardware**, on its own (`TrackerPlus_lg1.ptf`), the user's checklist:
   - put `G` on a MIDI step (confirms the FX list allows it on MIDI);
   - with a mono synth in legato/portamento mode: `G` steps slide, plain steps
     retrigger, over DIN and USB;
   - stop in the middle of a legato phrase, mute the track, change pattern in song
     mode: no hanging notes;
   - `G` with Micro-move, with a gate on the note before, on the same pitch;
   - the project opened in stock firmware still plays.
4. **Later, decided after hardware:** joining `patch/all` and the web patcher
   (`web/expected.json` gains the new builds), and the optional per-project setting
   (analysis option C), which would reuse these hooks.

## 8. Risks and open points

- **`G` on MIDI steps in the FX list.** Settled on hardware: stock firmware offers `G`
  on MIDI steps and ignores it there.
- **ITCM hooks** are hardware-tested (itcmtest, condtrig). Two more sites, `old` bytes
  checked by the patcher.
- **Receivers.** Synths that treat "note-on, then note-off of another pitch" oddly
  (for example a poly synth in a unison mode) would hear the old note end a moment
  later instead of a moment earlier. That only happens on `G` steps.
- **Duplicate note-off after a gate** stays as in stock firmware (sent before the new
  note, since a gated note isn't legato).
