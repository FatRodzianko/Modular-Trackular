# Conditional Trigs: investigation and plan

Status: phase 1 (the patcher's ITCM hooks, `patch/itcmtest`) is built, passes
its emulator tests, and is tested on hardware. Phases 2 and 3 (`patch/condtrig`:
playback, the encoder and the grid text) are built and pass their emulator tests, alone
and in bundles; the patch is tested on hardware on its own. Phase 4's docs are done, its
hardware checklist is in the patch README. The value
layout is decided: the Elektron order in section 5. Firmware 1.2.0. FINDINGS section 22
has the firmware facts this plan relies on.

The idea comes from **Psygoo**'s *Tracker+ Conditional Trigs* (CT6,
https://github.com/Psygoo/TrackerPlus-CT-Web/), a mod built on this repository's
patcher and tools. It stores loop conditions in the Chance FX. This plan uses that idea
and re-implements it from its behaviour, in this repository's patch format.

## 1. Verdict

**Feasible, as a normal C patch.** It needs one new patcher capability: BL hooks inside
ITCM code (section 4.1). The evidence:

- **One stock decision point.** The Chance FX (type 4) already decides whether a step plays.
  At micro-step 1, `seq_process_track` checks FX2, then FX1. A Chance column calls
  `rand_range_q(0, fx_max)` with the BL at ITCM `0x30ce`, and the step fails when the
  result is above the stored value. A failed step sets track `+0x98`, which skips the
  note (ITCM `0x29b2`) and every FX of the step (ITCM `0x2650`).
  - Redirecting that one BL decides pass or fail for the whole step, with the stock
    code doing the rest.
  - The emulator confirms this. A scratch build wrote a 12-byte ITCM stub at `0x1fb40`
    and pointed `0x30ce` at it, and the stub returned 0 (pass) or 255 (fail):
    - 1:2 played on loops 1 and 3, and 2:2 on loops 2 and 4.
    - A failed step also dropped the CC in its other column.
- **Stock firmware stays safe with such patterns** (emulator, stock 1.2.0):
  - Chance values above 100 always play, in FX1 and in FX2 (20 of 20 loops each for
    101, 102, 107, 137, 200 and 255).
  - `fx_value_text` shows them as plain numbers ("105").
  - Any encoder edit of such a cell snaps it to 100.
  - Nothing crashes, and the pattern file keeps the raw byte.
- **Small hooks.** Two BLs in ITCM, two BLs in UI code, the seq_advance BLs and the
  seq_advance_step word that swing, visualizer and interpolate already chain, plus a few
  words. About 50 bytes of patch RAM and 20 bytes of ITCM. The CPU cost is a few dozen
  instructions per Chance FX and per track step.

What it costs is that new patcher capability. ITCM code can't BL to flash, and all 192
ITCM veneers are in use. So the patch brings a small ITCM stub. The patcher places the
stub in the ITCM tail and loads it through the unused entry 3 of the startup copy table.
That changes the reset path, so it gets its own emulator tests and a no-op hardware
build first (section 9).

## 2. Psygoo's CT6 (what it is, how it's built)

### For the user

- Six conditions: 1:2, 2:2, 1:4, 2:4, 3:4 and 4:4. "A:B" means the step plays on the
  A-th pass of every B passes.
- **Entering them.** Turn the encoder on a Chance (C) cell past 100. The stored values
  are 102..107, and 101 is skipped.
- **Display.** The cell shows "1:2" and so on.
- **Pads.** On a cell that already holds a condition, the 48 pads pick one of the six
  conditions (8 pads each).
- **Choosing Chance again** in the FX list keeps the condition.
- **The count.** A single global pass counter, modulo 4. It goes up when track 16
  wraps, and resets on take start, at boot, and when the current pattern number
  changes. So in song mode the same pattern repeated in a row keeps counting.
- **Data.** The Chance FX byte in the pattern, so there is no new file.
- **Limits it states itself.**
  - Song mode is "NOT YET HARDWARE-VALIDATED".
  - Preview is "outside the playback contract".
  - 1st, PRE, NEI, and cycles up to 8:8 are "future research goals".

### Implementation

- **Target.** Stock 1.2.0 only: the input `.ptf` and the output are checked by SHA-256.
  The patcher runs in the browser (`patcher.js`, `ct6-program.js`).
- **Code.** 1,100 bytes of pre-assembled Thumb, given as a halfword array, at a fixed
  address (`0x601bab80`, right after the stock image, which ends at `0x601bab78`).
- **RAM.** One word at a fixed `0x20205000`: the start of `ocram_tail`, which bundles
  here give to other patches.
- It can't be combined with this repository's patches: it uses fixed placement, no
  relocation and no chaining.
- **Hooks.** 21 inline hooks, not this repository's BL/word format. Each one overwrites
  4–10 bytes of stock code with a `B.W`, or with `ldr.w pc,[pc]` plus a literal. The
  overwritten instructions are re-executed in the patch code. The sites:

| Hook | Site | Stock code there | What it does |
|---|---|---|---|
| chance | ITCM `0x30c4` (copy source `0x601907a8`) | Chance pre-pass, before `fx_max`/`rand_range_q` | value 102..107: pass if the counter matches, else fail |
| start, start_lock, start_unlock | ITCM `0x1250`, `0x129c`, `0x12dc` | `seq_play_from` | counter = 0; IRQs off around `seq_reset_positions` |
| boundary | `0x600ad1ea` | `seq_advance_step`, after the pattern-end flag `+0x33` | counter = (counter + 1) & 3 |
| swap_commit, swap_lock | `0x600accae`, `0x600acc68` | `seq_swap_pattern_buffers` | counter = 0 if `project+0x6c8` changes; IRQs off |
| play_lock, play_start, play_same, mode40_*, mode68_* | `0x600accf8`–`0x600acd7e` | play-start code around `0x600c3964` | IRQs off around song-position calls ("atomic transport") |
| defer_id_delta, defer_id_reload | `0x600e17e2`, `0x600e182e` | `project_collect_used_patterns_q` | jump straight back (no-ops as published) |
| encoder | `0x600af17e` | `seq_change_selection_fx_value` | type 4: 0..100, then 102..107 |
| formatter | `0x600afb48` | `fx_value_text` entry | type 4, 102..107: "1:2" ... "4:4" |
| absolute | `0x600afa16` | value-from-pad (`0x600af8e8`) | pads map to the six conditions |
| reselect | `0x600af57c` | `seq_set_selection_fx_type_q` | keep a condition when Chance is chosen again |
| boot_init | `0x60030332` | reset handler, before the copy table | counter = 0 |

### License and provenance

- **License.** MIT. The `LICENSE` is this repository's own, kept verbatim ("Copyright
  (c) 2026 Modular-Trackular contributors"). `NOTICE.md` credits this repository for
  `ptf.py`, `patch_ptf.py` and the emulator.
- **Reuse.** MIT is compatible, so the code could legally be reused with credit. There
  is nothing worth reusing, though:
  - The public repository has no source for the patch, only the emitted machine code.
    Its development repository is private.
  - That machine code contains Polyend instructions. To re-execute what the hooks
    overwrite, it carries about a dozen stock instructions, copied from the stock
    firmware's own code. Examples:
    - the `seq_play_from` entry `cmp r1,#0x7f; push.w {r3-r9,lr}; it ge; movge r1,#0x7f`;
    - the `fx_value_text` entry `push {r4-r6,lr}; mov r5,r2`;
    - `strb.w r3,[r5,#0x6c8]`;
    - the copy-table loads in the reset handler.

    `NOTICE.md` says the program "replays the small displaced instruction sequences".
    This repository must never include Polyend code, so **the CT6 binary can't be taken
    over, even in part.**
- **Approach.** A clean re-implementation from the behaviour described above, written
  in C. Credit Psygoo for the idea (Chance values above 100 as loop conditions, and
  counting repeats of the same pattern in song mode) in the patch README and the root
  README.

## 3. What the feature will do

**The conditions.** They live in the Chance FX. The encoder goes past 100 into
conditions:

| Shown | Plays when the track's pass count n (0 = first pass) ... |
|---|---|
| `1st` | n = 0 |
| `!1s` | n ≥ 1 (not the first pass) |
| `A:B`, B = 2..8, A = 1..B | n mod B = A − 1 |

That is 37 conditions in all. Probability stays what Chance 0..100 already is.

**Combining.** A step with two Chance FX passes only if both pass, and the stock code
already works that way. So a step can be 1:4 *and* 50 %. This needs a test (section 8).

**A failed condition** is exactly a failed Chance: no note and none of the step's FX.
The previous note keeps its gate.

**Counting is per track.** A track's count goes up each time the track returns to the
start of its loop:
- the pattern's end, or a Break FX;
- the start of a looping selection;
- in performance mode, the track's own length and play mode.

In normal mode all tracks wrap together, so this matches a global count. In performance
mode it gives polymetric conditions.

**Resetting.**
- Every take starts at n = 0, including Preview and rendering.
- The counts reset when the playing pattern changes: song mode moving to a different
  pattern, a queued pattern, or a per-track queued pattern for that track.
- A pattern repeated in song mode keeps counting, as CT6 does. Four slots of the same
  pattern then give 1:4..4:4 variation.

**Not in v1:**
- PRE / NEI, which need a "previous condition" result per track.
- FILL, which needs a held control.
- Pad entry of conditions.
- Keeping the condition when Chance is chosen again.

## 4. Hook design

### 4.1 New patcher capability: ITCM branches

ITCM code is copied at boot from flash (`0x6018d6e4 + address`; the stock word hook at
`0x601acf00` already relies on this). ITCM `BL`s reach only ±16 MB, so they can't reach
patch code in flash.

New `patch.json` key:

```json
"itcm_branches": [
  {"at": "0x30CE", "old": "<4 bytes>", "to": "mt_ct_chance", "next": "mt_ct_next_chance",
   "args": ["r7", "r8"], "note": "..."}
]
```

**What the patcher does:**
- For each entry, it builds a stub in a block it reserves at the start of the
  `itcm_tail` region (`0x1fb40`; no patch RAM goes there):
  - `mov r2, <args[0]>; mov r3, <args[1]>` (only for the listed args);
  - then `ldr.w pc, [pc, #0]` and the word `to | 1`;
  - 8 bytes without args, 12 bytes with two.
- It appends the stub block to the patch code in flash.
- It rewrites the BL's bytes in the ITCM copy source, at flash `0x6018d6e4 + at`, as a BL
  from `at` to the stub. Both addresses are ITCM, and the `old` bytes are checked.
- It points startup copy-table entry 3 at the block:
  - the entry is at `0x600302c4`: load `0x601ad21c`, exec `0x80000000`, length 0;
  - it is checked against those values, then set to {flash block, `0x1fb40`, block
    length}.
- It fills `next` with the old BL target, `| 1`: the stock ITCM function, which patch
  code calls by absolute address, or the hook of the patch listed before.

**Rules:**
- The stub jumps with `ldr pc`, so LR still points into the ITCM caller. The C function
  returns straight there, and r0 is its result.
- There is one stub per site, for the last patch listed. A chained `next` holds the
  previous patch's hook itself (not its stub), and the hook calls it with all four
  arguments. So every patch on one site must name the same `args`, and the patcher
  refuses a site hooked with different ones.

**Why this is sound:**
- The reset handler copies whole words while the offset is below the length.
- `machine.py` runs the same table from the image, so emulator tests cover the boot copy.
- ITCM above 64 KB, which includes the stock veneers at `0x1f530`–`0x1fb38` and the
  tail, executes under MPU region 4 (RW, exec). The veneers run there on hardware (the
  swing hook at `0x1f818` is hardware-tested).
- The `itcm_tail` region is already listed as free and unused in section 15 of FINDINGS.

The same mechanism would also serve negative Micro-move (FINDINGS section 12),
replacing the LR-dispatch plan on veneer `0x1f818`. That word is now chained by swing
and visualizer, which would make LR dispatch fragile.

`test_layout.py` checks:
- the copy-table entry;
- that no RAM block overlaps the stub block;
- that each ITCM BL decodes to its stub;
- that each stub's literal is the hook;
- that `itcm_branches` chain correctly in bundles.

### 4.2 The patch's hooks

| Site | Kind | Shared? | Hook |
|---|---|---|---|
| ITCM `0x30ce` (`bl rand_range_q` in the Chance pre-pass) | itcm_branch, args r7, r8 | no | `mt_ct_chance(lo, hi, col, track)` |
| ITCM `0x3752` (`bl seq_play_from` at count-in end, in `seq_tick`) | itcm_branch | no | `mt_ct_play_from`: reset pending, then `next` |
| `0x601acf00` (word: ITCM veneer → `seq_advance_step`) | word, chained | swing, visualizer | `mt_ct_advance_step`: pass counting, pattern-change reset |
| `0x600aa7f8` / `0x600bc27a` (`bl seq_advance`, audio / render) | branch, chained | swing, visualizer, interpolate | `mt_ct_seq_advance`: reset on a take's start |
| `0x600ac604`, `0x600ac694`, `0x600acd34`, `0x600ad544` (literals of the 4 flash veneers to `seq_play_from`) | words, chained | no | reset pending, then `next` (restart without a stop) |
| `0x600af1b2` (`bl` veneer → `fx_max`, only caller: encoder edit) | branch, chained | no | type 4 → `CT_LAST` |
| `0x600df748`, `0x600df77c` (pattern grid → `fx_value_text_cell`, FX1 / FX2) | branches, chained | no | type 4 with a condition → its text |
| `0x600cbf9c` | version word | all | `1.2.0-ct1` |

**`mt_ct_chance(lo, hi, col, track)`.**
- `col` = r7 = seq + 0x68·track for FX2, minus 2 for FX1. The column's type is at
  `col + 0x73` (always 4 here) and its value at `col + 0x74`.
- A value of 0..100, or anything above `CT_LAST`, or state that isn't initialized
  returns `next(lo, hi)`.
  - That is the stock random call, made exactly once as stock firmware makes it, so
    Chance percentages and the random sequence stay identical to stock.
  - Uninitialized state therefore behaves as stock (always play).
- A condition returns 0 (pass, since 0 ≤ value) or `0x7fffffff` (fail). The stock code
  compares with `cmp r0, value; ble pass`.

**`mt_ct_advance_step(seq, t)`.**
- It reads `project+0x6c8` and track t's `+0xbc`, calls `next`, then compares.
- **The track wrapped** when its "steps since the loop start" counter `+0x8a` is 0
  after the call. Phase 2 found this holds in every play mode (forward, Break, backward,
  random, pattern tables, performance-mode lengths) and when playback starts from a
  later step (FINDINGS section 22). Then `n[t]++`.
- **The pattern number changed** on t = 15: all n = 0. This runs after tracks 0..14
  have advanced, but before any track's micro-step 1 of the new pattern.
- **`+0xbc` went from ≥ 0 to −1** (a per-track queued pattern switched): n[t] = 0.

**Take start.**
- `mt_ct_seq_advance` sees `playing` (`seq+0x2a`) go from 0 to 1 at the start of an
  audio block, before that block's ticks, and resets. This is what swing does, and it
  covers play, Preview/selection playback (`seq_play_selection` has 11 callers, 9 of
  them tail calls), rendering, and MIDI start.
- `seq_play_from` itself processes no step: after it, micro-step 1 of step 0 is still
  to come (emulator). So a reset in the next block is in time.
- Restarting while already playing has no rising edge. The 4 flash veneers and the
  count-in BL set a `pending` byte instead. `mt_ct_chance` and `mt_ct_advance_step`
  check it first, so a reset is never late, even when the count-in starts playback in
  the middle of a block.
- Only the audio IRQ writes the counts. The main loop writes only `pending`, a single
  byte that the IRQ reads and clears and that can't be interrupted by the main loop. So
  none of CT6's IRQ masking around song transport is needed.

**UI:**
- **Encoder.** `seq_change_selection_fx_value` computes min(max, value + delta) (0 when
  negative). The hook returns `CT_LAST` as the max for type 4 and calls `next` for
  every other type. Turning right from 100 then steps through the conditions in stored
  order, and turning left from `1st` gives 100.
  - Every other writer keeps the stock 0..100: the Fill popup, random fill, pad entry
    and live recording use other `fx_max` call sites. So conditions only ever come from
    the encoder.
- **Grid text.** Wrap `fx_value_text_cell(seq, buf, 4, column, track, step)`:
  - look up the cell in `*(seq+0x14)`;
  - for type 4 with value 101..`CT_LAST`, write the 3-character text and a NUL;
  - otherwise call `next`.
- **Not hooked.** The Fill popup's from and to texts (`patfill_show_from_q`,
  `patfill_show_to_q`) call `fx_value_text` directly at `0x600e3fd0` and `0x600e425c`,
  but only with values ≤ 100.
- **Phase 1 check.** Render the Pattern screen with `eve.py` while editing a C cell,
  to find any other place that shows the raw value.

### 4.3 Conflicts and chaining

| Patch | Shared sites | Interaction |
|---|---|---|
| swing | `0x601acf00`, `0x600aa7f8`, `0x600bc27a` (all chained) | None. A Swing FX on a failed step is skipped, as with a failed Chance |
| visualizer | `0x601acf00`, `0x600aa7f8` (chained) | It ripples from the step's pattern data, so a failed step still ripples, as a failed Chance does today. Possible follow-up: it could check `+0x98` |
| interpolate | `0x600aa7f8`, `0x600bc27a` (chained) | It ramps from and to CC values whether or not the step played, as with a failed Chance today. Possible follow-up for both: skip steps with `+0x98` set |
| humanize | none | It only fills free FX slots and changes m and V values. A Chance condition is kept; with one free slot the step gains m only. On a failed step the m isn't applied (the FX pass is skipped) |
| colorsong | none | A C FX counts as content, as before |
| masterfx, synths | none | none |

Bundle order doesn't matter. Every shared site is chained, and the new hook only acts
after `next` returns: `mt_ct_advance_step` counts after the stock advance has run, and
the take-start check comes before `next` but touches only CT state. `test_layout.py`
gets the patch alone and in bundles with every released patch, in both orders (synths
stays out until it is ready).

## 5. Data format

- Pattern files are unchanged. A condition is the Chance FX (type 4) with a value byte
  101..137, in FX1 or FX2.
- No settings file: the feature is used per step and needs no switch.
- Recommended layout, in Elektron order:

| Value | Condition | Value | Condition |
|---|---|---|---|
| 101 | `1st` | 112–116 | 1:5 … 5:5 |
| 102 | `!1s` | 117–122 | 1:6 … 6:6 |
| 103–104 | 1:2, 2:2 | 123–129 | 1:7 … 7:7 |
| 105–107 | 1:3 … 3:3 | 130–137 | 1:8 … 8:8 |
| 108–111 | 1:4 … 4:4 | | |

- In general, A:B = 103 + (B − 1)·B/2 − 1 + (A − 1), and `CT_LAST` = 137.
- **CT6 compatibility (considered, not chosen: the layout above is the one to build).**
  CT6 stores 1:2, 2:2, 1:4, 2:4, 3:4 and 4:4
  as 102–107. A compatible layout would keep 102–107 as those and put `1st` at 101,
  `!1s` at 108, the x:3 conditions at 109–111 and the rest as above. CT6 patterns would
  then play the same here. The cost is the encoder order, which follows the stored
  value: 100, 1st, 1:2, 2:2, 1:4…4:4, !1s, 1:3….
- **Stock firmware** with such a pattern:
  - conditions play every time;
  - the cell shows "C 105";
  - touching the value with the encoder makes it C 100;
  - Fill and random fill never produce such values.

  Removing the mod therefore makes conditional steps play on every pass, louder but
  harmless. It is only lossy where a cell is edited on stock firmware.
- **Random FX value (x, type 16)** in the other column leaves a C value alone (checked
  in phase 2), so a condition stays a condition.

## 6. UI

- Put C (Chance) in a cell and turn the encoder. Past 100 it shows `1st`, `!1s`, `1:2`,
  `2:2`, `1:3` … `8:8` and stops at `8:8`. Turning left goes back to 100 and the
  percentages.
- The grid shows the condition in the 3-character value cell, for example `C1:4` or
  `C1st`.
- The pads, Fill, random fill and live recording work as on stock firmware (percentages
  only).
- No Config items.

## 7. RAM and CPU

- **Patch RAM** (`.mt_ram`, magic-initialized, any region; it isn't used before `main`):
  magic 4 B, counts 16 × 2 B, `pending`, `was_playing`, and the pattern number seen
  last. About 44 B.
- **ITCM:** the 2 stubs, 12 + 8 B, in a 32-byte reserved block at `0x1fb40`.
- **Flash:** an estimated 1–1.5 KB of code and tables.
- **CPU:**
  - `mt_ct_chance` runs only on steps with a C FX: tens of instructions, plus the stock
    random call for percentages.
  - `mt_ct_advance_step` adds about 20–30 instructions per track step.
  - The take-start check is a few instructions per audio block.
  - For comparison, stock costs about 2,900 instructions per micro-step with 16 busy
    tracks. Measure it in phase 2.

## 8. Emulator test plan (`tools/emu/test_condtrig.py <patched.ptf> <stock.ptf>`)

Like `test_swing.py`, the test drives the real sequencer with `seq_timing`, with MIDI
port fakes recording when notes and CCs go out, and calls the patched `seq_advance`
(and the render call) as `app_audio_block` does. It reads the code and RAM locations
from `layout.py`, works on the single-patch build and on bundles, prints PASS and FAIL
lines and ends with a failure count.

1. **Boot and sites:**
   - the startup copy table's entry 3 holds {stub block, `0x1fb40`, length};
   - after `Machine` runs the table, ITCM `0x1fb40` holds the stubs;
   - ITCM `0x30ce` and `0x3752` BL to them, and each stub's literal is its hook;
   - the stock bytes elsewhere in the ITCM image are unchanged.
2. **Stock percentages unchanged.** A pattern with C 0, 25, 50, 75 and 100 in FX1 and
   FX2, on 4 tracks, played for 50 loops. The note and CC events are identical to stock
   firmware, event for event, which shows the random sequence is untouched.
3. **Every condition.** One-step patterns, so a loop is one step. On 16 tracks, values
   101..137 in FX1 and in FX2 (5 runs), 16 loops each. Each track's note-ons match the
   rule in section 3 exactly. Values 138..255 always play, as on stock.
4. **What a failed step drops**, on a MIDI track, a sample track (voice note-on stub)
   and a synth track:
   - the note;
   - a CC in the other column;
   - Micro-move (no delayed note later);
   - Volume.

   A passing step behaves as without the condition. Two C FX on one step: 1:2 with
   C 0 never plays, and 1:2 with C 100 plays like 1:2.
5. **Counting:**
   - forward patterns of 4 steps;
   - a Break FX ending the pattern early (counts as a pass);
   - a looping selection;
   - performance mode with different track lengths (independent counts);
   - backward play mode;
   - random and table modes (as decided in phase 1).
6. **Resets:**
   - stop and play: `1st` plays again;
   - play again while playing, through each `seq_play_from` veneer;
   - count-in start: n = 0 at step 0;
   - Preview with a loop;
   - song mode: A A B — the counts run 0, 1 on A, then 0 on B;
   - a queued pattern;
   - a per-track queued pattern in performance mode;
   - the render loop: a render starts at n = 0.
7. **UI:**
   - encoder edits through `seq_change_selection_fx_value`: 100 +1 → 101, 136 +5 → 137,
     101 −1 → 100, 105 −10 → 95;
   - other FX types keep their stock limits (m, V, a–f, I, T);
   - a mixed selection;
   - Fill popup and random fill on C stay ≤ 100;
   - grid text through the hooked `fx_value_text_cell` calls: `1st`, `!1s`, `1:2`,
     `8:8`, and for other values the stock text ("50", "---").
8. **Stock firmware reference** (the stock `.ptf` argument): values 101..137 always
   play, show as numbers, and an encoder edit gives 100. This guards what the doc
   promises.
9. **Cost.** Instructions per micro-step and in the worst audio block: stock, the patch
   with no conditions, and the patch with a condition on every track. They must stay
   within a few percent of stock.
10. **Bundles.** `test_layout.py` passes. In an all-patch bundle, in both orders, this
    test and the swing, visualizer, interpolate and humanize tests all pass.

## 9. Build phases

1. **Patcher: ITCM branches.** Built and emulator-tested:
   - `tools/patch_ptf.py`: `itcm_branches`, the stub block, copy-table entry 3, and
     reserving `0x1fb40` from `itcm_tail`. Builds without ITCM hooks are byte-identical
     to before.
   - `tools/test_layout.py`: its checks, with itcmtest alone, in bundles with every
     released patch in both orders, chained with a copy of itself, and the two refusals.
   - `patch/itcmtest`, a no-op patch on `0x30ce` (with r7, r8) and `0x3752`, and
     `tools/emu/test_itcmtest.py`: the stubs in ITCM after the boot copy, playback
     identical to stock (Chance FX and count-in), the right arguments reaching the
     hook, also through a chain.
   - **Hardware: tested.** `TrackerPlus_it1.ptf` boots, and C 50 steps skip about half
     the time in normal play, with record on, and after a count-in, as in stock
     firmware.
2. **Playback core.** Built and emulator-tested: `patch/condtrig/condtrig.c`
   (`mt_ct_chance`, counting, resets) and `tools/emu/test_condtrig.py` (tests 1–6 and
   9). It passes alone, in bundles with every released patch in both orders (with each
   patch's test; synths stays out until it is ready), and after itcmtest on the same ITCM sites. Settled on the way: the wrap rule
   (`+0x8a`), Random FX value. The four `seq_play_from` veneers are hooked whichever UI
   actions use them. Measured cost: a condition costs fewer instructions than a stock
   Chance percentage; the hooks add about 30 instructions per track at the busiest
   tick. Not on hardware: there is no way to enter a condition before phase 3.
3. **UI.** Built and emulator-tested: `mt_ct_cell_max` on the encoder edit's `fx_max`
   call, `mt_ct_fx1_text` / `mt_ct_fx2_text` on the grid's two value-text calls, and
   tests 7 and 8 in `test_condtrig.py` (encoder steps and limits against stock, a mixed
   selection, pad entry staying 0..100, grid text for every condition and stock text for
   everything else, stock firmware's view of such cells). The search for other places
   that show an FX value found none: values reach the screen only through
   `fx_value_text`, called by the grid (through `fx_value_text_cell`) and by the Fill
   popup's from/to texts, which only get 0..100; `fx_display_value` is used only during
   playback; the other users of the FX name table show names. Not checked on hardware:
   that the grid's font has `:` and `!`.
4. **Integration.**
   - bundle tests;
   - `patch/condtrig/README.md`, crediting Psygoo;
   - the root `README.md` (mod list, layout table, credit);
   - `docs/PATCHING.md` (status table, commands);
   - `prebuilt/`;
   - a hardware checklist for the user: pattern mode, song mode with repeats, Preview,
     render, performance mode, stock-firmware round trip.
5. **Later, optional.** Pad entry of conditions; keep the condition when Chance is
   chosen again; PRE/NEI; visualizer and interpolate skipping failed steps (`+0x98`).

## 10. Risks

- **The reset path.** A wrong copy-table entry or stub stops the firmware from booting.
  - The emulator runs the real table.
  - Phase 1 is a no-op build.
  - Recovery is the separate bootloader (FINDINGS section 3).
  - The patcher checks the entry's old values and keeps the stub inside `itcm_tail`.
- **ITCM code edits** are new for this repository. Only one BL changes per site, and its
  `old` bytes are checked. No stock instruction is copied or moved, unlike CT6's inline
  hooks.
- **Missed resets** if some path starts playback without `seq_play_from` and without a
  rising `playing` edge. Phase 1 lists the callers, and test 6 covers each one.
- **Musical interactions**, as with stock Chance today:
  - interpolate ramps through failed steps;
  - visualizer ripples on them;
  - the stock delayed-note quirk (FINDINGS section 12) is unchanged.
- **Stock firmware or a removed mod:** conditions play every pass, and an encoder touch
  on stock firmware makes them C 100.
- **CT6 users:** their 102–107 mean other conditions here (section 5); the patch README
  should say so.
- **Not verified on hardware:** everything above is emulator evidence.
