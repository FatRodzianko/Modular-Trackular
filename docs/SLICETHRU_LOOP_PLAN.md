# Beat Slice Thru Loop: plan (firmware 1.2.0)

A Loop switch for Beat Slice Thru instruments, on the second page (More) of the
Sample Playback screen's slice buttons. With Loop on, a note starts at its slice, plays
to the end of the sample, jumps back to the start of the sample (frame 0) and keeps
looping the whole sample until something stops it: the track's next note, OFF, the end
of a gate, CUT, FADE, stopping playback or muting the track. With Loop off, it plays as
Beat Slice Thru does now.

All addresses are firmware 1.2.0. `ins(i)` = instrument `i` at `0x20200000 + 0x174·i`;
`voice` = a sample voice; `scr` = the Sample Playback screen object `0x200254b4`. The
facts behind slicethru are in FINDINGS section 27 and
[BEAT_SLICE_THROUGH_PLAN.md](BEAT_SLICE_THROUGH_PLAN.md). The new facts are in FINDINGS
section 27, "A 1-shot voice's end, and looping it".

Status: phase 1 (the emulator facts) done on 2026-10-25 (section 8). Nothing is built.

## 1. Verdict

**Feasible, as an extension of `patch/slicethru` (`1.2.0-bt5`), with 2 new chained
hooks, 1 new site in the render, and a small patcher addition.** The evidence:

1. **The end of a 1-shot voice is handled in one 4-byte spot that runs once.** Beat
   Slice voices render with the forward 1-shot loop `0x600353fc` (or the reverse one,
   `0x600355d4`, with the `r` FX). For each of the block's 128 frames, it advances the
   position `voice+0x7c` (frames from the note's start frame `voice+0x34`). When the
   position reaches the frames to play (`voice+0x50`), it sets the position to
   `voice+0x1c`, which makes the next frame silent and clears "playing" (`voice+0x20`).
   That is two instructions at `0x60035522`, `ldr r3, [r4, #0x1c]; str r3, [r4, #0x7c]`
   (bytes `e369e367`). They run only when the voice reaches its end. Nothing branches
   into them (a scan of `0x60034000`..`0x60037000`), and `lr` is free there.
   - A BL over those 4 bytes can do the stock thing, or, for a looping voice, wrap:
     position − (`voice+0x50` + `voice+0x34`), the same frame counted from frame 0.
     The render goes on with no other change. Its indexed read is signed, so a
     position below the start frame reads the right frame.
   - It costs about 15 instructions once per pass through the sample. Every other
     block renders exactly as now: the same 4,916 instructions per block measured for
     slicethru.
2. **The second page has two blank buttons.** The label function `0x600f7c54`
   (the same one slicethru hooks for the list) labels page 2 (`scr+0xf0` ≠ 0) of
   Slice and Beat Slice like this:

   | Button | 1st | 2nd | 3rd | 4th | 5th | 6th |
   |---|---|---|---|---|---|---|
   | Label | Slice / Auto | Slice / Evenly | No of Slices | (blank) | (blank) | More |

   The 4th and 5th buttons (label slots `scr+0xc7`, `scr+0xc8`) are set to the empty
   string `0x60038660` by `bl 0x60101d5c` at `0x600f820a` and `0x600f8216`. The second
   line (Auto, Evenly) is set by `0x60101d74`. A "Loop" label with "On" or "Off" under
   it is the same two calls.
3. **The 4th button's press has its own callback word.** The screen's start code
   (`0x600f5018`) registers one callback per screen button. The 4th button's,
   `0x600f4b55`, is the word at `0x600f5158` (the 5th's is at `0x600f515c`). They are
   the only references, in the same pool as the encoder and ± words slicethru already
   chains. A chained word hook can take the press when it is the Loop button and pass
   everything else on. Phase 1 confirms that stock does nothing with the 4th button on
   page 2 of Beat Slice.
4. **A looping voice can be recognised at the wrap without new per-voice state.**
   Every play mode's note-on writes the instrument's index to `voice+0x6f`. The wrap
   hook loops only if the voice is in Beat Slice mode (`voice+0x21` == 5) and that
   instrument is Beat Slice with both the Thru and the Loop flag set. A voice later
   reused by another instrument can't loop by mistake, because each note-on rewrites
   `+0x6f` and the check is redone at each wrap.

**Nothing changes for any other play mode or instrument.** Without the Loop flag, the
render site does exactly the two stock instructions, and the note-on is unchanged.

## 2. What the user gets

1. On a Beat Slice Thru instrument, Sample Playback → More: the 4th button reads
   **Loop** with **Off** under it. Pressing it toggles On/Off. On a plain Beat Slice or
   Slice instrument, the button stays blank and does what stock does.
2. With Loop on, every note of that instrument plays from its slice to the end of the
   sample, then loops the whole sample (start to end) until something stops it, as
   listed at the top. With the amp envelope on, OFF and the end of a gate release it as
   usual.
3. Switching Loop off while a note plays lets that pass finish, then the note stops at
   the end of the sample.
4. Loop is saved per instrument with the Thru flag, in `slicethru.bin` in the project
   folder. Stock firmware and older slicethru builds play the instrument as Beat Slice
   or Beat Slice Thru, without the loop.
5. A pad press or a pattern note behaves the same. A pad preview in Sample Playback
   should stop on release, as a Forward Loop preview does in stock. Phase 1 didn't
   settle this; the phase 4 screen tests and the hardware check (section 10) cover it.

**What it doesn't do:**

- No crossfade at the loop point. The loop jumps from the last frame to frame 0, as
  stock's Forward Loop does at its loop points. A sample that doesn't loop cleanly
  clicks there.
- Loop applies to Beat Slice Thru only, not to plain Beat Slice (which stops at the
  next slice).

## 3. Choices for you

- **Reverse (`r` FX) with Loop on. Decided (2026-10-25): option (a).** With `r`, the
  note plays once (end of sample → slice start) as now, and doesn't loop. The reverse
  render (`0x600355d4`) stops at position 0 inside an `it le` pair and has no clean
  4-byte end spot like the forward one. Option (b), looping in reverse through a second
  hook in its stop path (`0x60035716`), is not planned.
- **The Loop button:** a press toggles On/Off (recommended). The alternative is a
  stock-style focus (press, then the encoder or ±), which needs more hooks.
- **The loop start:** the start of the sample (frame 0), as you described. The
  instrument's Start point isn't used, as in Beat Slice. The other choice would be the
  slice's own start.

## 4. Design

### 4.1 Hooks (new ones)

| Hook | Where | What |
|---|---|---|
| `mt_bt_wrap` | `0x60035522`, the 4 bytes `e369e367` (not a BL: see 4.3) | Looping voice: position − (`+0x50` + `+0x34`), stored to `+0x7c`. Otherwise the stock two instructions |
| `mt_bt_page2_label` | `bl 0x60101d5c` at `0x600f820a` (4th button's label, page 2) | Beat Slice Thru: "Loop", and "On"/"Off" as its second line. Otherwise the stock blank |
| `mt_bt_button4` | callback word `0x600f5158` (4th button, `0x600f4b55`) | Beat Slice Thru on page 2, press: toggle the Loop flag, relabel the button, mark the file to save. Otherwise the stock callback |

The existing hooks change only a little:
- **The list's mode step** (Beat Slice ↔ Thru) already reruns the label function and
  returns to page 1, so the Loop button can't be left showing on the wrong mode
  (emulator, every input, both pages). Page 2 moves the encoder's focus to No of
  Slices; the Loop button doesn't need the focus, because a press toggles it.
- **The file code** reads and writes the second mask.
- **The stale-flag clean-up** also clears the Loop flags.

The Beat Slice note-on hooks stay as they are: a looping note starts exactly like a
Thru note.

### 4.2 State and file

- **Builds on bt4** (2026-10-25, the Save As fix): the flags go with a project through
  Save As (the hook on the save request's name copy, `0x60114eaa`), and a new project
  ("New Project", no path) writes nothing. The Loop mask follows the same rules.
- **RAM:** a 48-bit Loop mask and its saved copy, 12 bytes more (32 → 44).
- **`slicethru.bin` becomes `BTS2`:** magic, the Thru mask, the Loop mask, checksum
  (24 bytes).
  - A `BTS1` file still loads, with every Loop off.
  - Older slicethru builds read a `BTS2` file as damaged and ignore it, so those
    instruments play plain Beat Slice there. This only matters when going back to
    `bt4` or older.
- **The version:** `1.2.0-bt5`. `patch/all` (`1.2.0-MT4` with bt4) needs a new version
  text too.

### 4.3 The patcher

Before this work, `branches` were BL sites, and with `next` the patcher decodes the
old BL to chain it. The wrap site holds two ordinary instructions, so a branch entry
now takes `"replaces": "insns"` (built in phase 2). It means the old bytes aren't a
BL, refuses `next`, and is claimed like any other site, so a second patch hooking it
is refused. Both patchers also refuse two sites that overlap (one starting 1 to 3
bytes into another), which matters only now that a site can start mid-pair.

The hook itself is a few lines of Thumb assembly in `slicethru.c`. It keeps every
register but `r3` (r0–r2, ip and the rest are live in the render loop) and then calls
the C check.

## 5. Interactions

- **Other patches:** none hooks the render, the page-2 labels or the 4th button's
  word today. The label site and the button word are chained anyway, like slicethru's
  other UI hooks.
- **Other sessions' work in progress:**
  - pd-synth's PHASE work (MS8) and live resampling (`patch/all` `1.2.0-MR4`) are
    committed. The slicethru Save As fix (bt4, `patch/all` `1.2.0-MT4`) comes before
    this work, so phase 5 builds on MT4.

## 6. Budget

- **Code:** about 350–450 bytes more than slicethru's 1,813 (estimate, from the
  hook count).
- **RAM:** 12 bytes more.
- **Cost:**
  - the note-on is unchanged (54 instructions over stock);
  - a render block is unchanged except the one block per pass where the voice wraps
    (about 20 instructions);
  - no extra voices: a looping note holds its voice until stopped, as a Forward Loop
    note does.

## 7. Risks and open points

- **Settled in phase 1:**
  - The playhead reads (`+0x34` + `+0x7c`), which is the true frame after a wrap
    (`0x60100ab0`), so it needs no fix.
  - The search by sample address is gone: the voice keeps its instrument at
    `+0x6f`.
  - A note with no end (Loop on, amp envelope off, no OFF) loops until the next note
    on its track, CUT, FADE, OFF or stop, as asked. Each of those ends a looping voice
    in the emulator.
- **Settled in phase 2:** the non-BL site, the first in the repo. Both patchers
  place it and refuse a second patch on it, plain or chained, and the layout test and
  the web harness check both.
- **Still open:** the pad preview's release (section 2, item 5).

## 8. Build phases

1. **Emulator facts (no patch). Done 2026-10-25,** with scratch scripts on a bt3
   build. The facts are in FINDINGS section 27:
   - A wrap forced at `0x60035522` loops sample-exact, for three passes from slices
     1, 2 and 4, at pitch 1×, 2× and 3×. Stereo partners stay in step, and the site
     never runs before the end.
   - OFF, CUT and FADE, with the amp envelope on and off, end a looping voice through
     the real voice note-off. Stop takes the OFF path.
   - The playhead accessor and `voice+0x6f` (the instrument), as in section 7.
   - The page-2 labels are as in section 1. The stock 4th and 5th buttons change
     nothing on page 2. A mode step reruns the labels and returns to page 1.
2. **The patcher. Done 2026-10-25:** `"replaces"` in `patch_ptf.py` and
   `web/patcher.js`, documented in `docs/PATCHING.md` and `CLAUDE.md`. The tests use a
   copy of slicethru with the site added:
   - `test_layout.py` places it alone and in slicethru's place in `patch/all`, and
     checks the site's 4 bytes are a BL to the hook with the bytes around it stock.
   - It checks five refusals: `next` on the site, a `replaces` other than `insns`, a
     second patch on the site (plain, or chaining it), and a site 2 bytes into it.
   - `test_web_patcher.py` checks the web patcher makes the same bytes for both
     placements and refuses the same five.
3. **The render hook,** with its emulator test.
4. **The UI and file:** the label, the button, `BTS2`, the clean-up. Emulator tests
   on the real screen and the fake SD card.
5. **Bundle and web** (after pd-synth is committed):
   - `patch/all` version;
   - the slicethru description in `web/manifest.json`;
   - `--tier release --update`;
   - README, PATCHING.md.
6. **Hardware check** (you, about 15 minutes, section 10).

## 9. Emulator tests (additions to `tools/emu/test_slicethru.py`)

- **No Loop flag:** every play mode's render is byte-identical to stock, including
  the end of the sample.
- **Loop on:**
  - notes from slices 1, 2 and the last: the output after the end equals the sample
    from frame 0, for three passes;
  - stereo voices stay in step;
  - reverse with Loop on plays once (option a);
  - a block without a wrap costs exactly as stock.
- **Stopping:** Loop off mid-note stops at the next end. A voice reused by another
  instrument or another play mode doesn't loop. Next note, OFF with and without the
  amp envelope, CUT, and stop all end a looping voice.
- **Screen:**
  - page 2 shows Loop On/Off only for Thru instruments, and blank for Beat Slice and
    Slice;
  - a press toggles it and relabels the button;
  - the other buttons and pages behave as stock;
  - a mode step relabels.
- **File:** `BTS2` written once after a change, `BTS1` loads with Loop off, damaged
  files ignored, stale Loop flags cleared.

## 10. Hardware check (you)

1. Load a drum loop, set Beat Slice Thru, slice it evenly into 4. More → Loop: On.
2. Put one note on slice 2 at step 1 of an empty pattern and play. It should play
   slices 2–4, then loop the whole sample with no gap, until you stop.
3. Add an OFF later in the pattern; with the amp envelope on it releases, with it off
   it stops at once.
4. Loop: Off while it plays. It should finish the pass and stop at the sample end.
5. Save, reload the project: Loop is still On. Plain Beat Slice instruments show no
   Loop button.
6. Reverse (`r`) on that note: plays once backwards, no loop (option a).
7. In Sample Playback, hold a pad of that instrument past the sample's end, then
   release it: it loops while held and stops on release.
