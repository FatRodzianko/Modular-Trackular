# Polymeter (a length per track): plan (firmware 1.2.0)

Each track gets its own length inside a pattern. A shorter track loops its own steps
until the pattern ends, and then every track starts again together. In a 16-step
pattern, a track of length 6 plays steps 0–5, 0–5, 0–3, then all tracks restart. Steps
past a track's end stay in the pattern, shown dimmed, as stock keeps the steps past a
shortened pattern's end.

All addresses are firmware 1.2.0. `seq` = the sequencer object `0x20017274`; track t's
fields are at `seq + 0x68·t + offset` (FINDINGS section 12); `ed` = `0x20022770`, the
Pattern screen's editor state (section 13). The new facts are in FINDINGS section 29.

Status: plan approved 2026-10-26: short tracks loop until the pattern ends, lengths per
pattern, a Trk Len button on page 3. The optional extras (section 12, question 4) are not
built unless asked. The web patcher choice (section 8) is still open. Phase 1 (RE
confirmations) is done: its results are in section 9, in FINDINGS section 29, and below.
Phase 2 (the playback core, `patch/polymeter`, `pm1`) and phase 3 (the Trk Len button
and the encoder) are built, emulator-tested and committed (section 9). Phase 4 (the
grid) and phase 5 (live recording, with the patcher's tail calls) are built and
emulator-tested (section 9). Not tested on hardware.

## 1. Verdict

**Feasible.** The playback core is small and proven in the emulator. The work is in the
parts around it: the edit control, the dimmed grid, live recording and the file.

1. **Every track reads its step's data in one place.** The step's 6 bytes are copied into
   the work record at micro-step 1, by `bl memcpy_small` at ITCM `0x2170` (r4 = seq,
   r5 = track). An ITCM hook there that reads step mod L instead of the step is the whole
   playback change. The only other reads of step data are Roll's two look-aheads
   (`0x33a2`, `0x3520`).
2. **Nothing else has to move.** The tracks' play positions (`+0x88`) keep counting the
   pattern as stock does. So these are all unchanged:
   - the pattern end (track 16's wrap);
   - song mode's next-pattern preload;
   - the metronome;
   - the Pattern screen's play row (all of which read track 1's position);
   - swing and condtrig.

   Every track restarts together at the pattern end because they all do already.
3. **Emulator proof** (scratch hook at `0x2170`, stock firmware otherwise; section 3.2):
   - with no lengths set, the output is identical to stock;
   - a 6-step track in a 16-step pattern plays 0–5 0–5 0–3 and realigns;
   - a short track 16 doesn't end the pattern early;
   - Play from step 9 starts the 6-step track on its step 3;
   - song mode still loads and starts the next pattern on time;
   - a looping selection and performance mode both play (their step) mod L.

**Recommended:**
- lengths **per pattern** (each pattern has its own 16 track lengths), saved in
  `<project>/polymeter.bin`;
- edited with a new **"Trk Len" button on page 3** of the Pattern screen's bottom bar:
  hold it and turn the encoder, as stock's Length button works;
- steps past a track's end drawn in the grid's dim color.

Version `pm1`.

**What it costs** (detail in sections 6 and 7):
- 3 ITCM hooks (`0x2170`, `0x33a2`, `0x3520`);
- 14 flash hooks for the button, the encoder, the grid colors, the file and the version;
- 26 for live recording: 8 veneer words and 18 calls (section 4.3);
- about 4.2 KB of RAM, which goes to the SDRAM top in `patch/all`.

**Two things the user should know before saying go:**
1. **The web patcher.** Joining `patch/all` makes this the web patcher's 13th mod. Its
   mix names run out at 12 mods, and the release harness doubles to about 46 minutes and
   about 42 GB of disk, unless the harness-disk plan in the shared notes is done first.
   Section 8 has the options.
2. **Interpolate and the visualizer.** They read each track's step themselves, so on a
   track with its own length:
   - interpolate's CC ramps follow the pattern position, not the track's loop;
   - the visualizer's ripples come from the pattern position.

   Both need a small update later (separate patches, one at a time; section 5). Until
   then, a track that uses both interpolation and its own length ramps oddly.

## 2. The idea, and what we know about the original

- **Source.** A custom firmware posted on r/PolyendTracker. The post's URL calls it
  "firmware mods for the tracker mini", so it is for the **Tracker Mini**. Its
  description, as the user quoted it: each track has its own length, set by holding a
  "Track len" control and turning the encoder; steps past the end are dimmed; all
  tracks line up again at the end of the pattern.
- **Not verified.** Reddit is blocked for this session (in the built-in browser and for
  web fetches), and a web search found nothing. So neither the post nor the video was
  seen. Nothing of theirs was used, only the description above. The user's reading of
  the video (short tracks keep looping and everything realigns at the pattern end or the
  next pattern) is what this plan builds. It is also the only reading consistent with
  "all tracks line up again at the end of the pattern", but the alternative (a short
  track stops at its end and waits) isn't ruled out. **Worth a look at the video by the
  user; the alternative would be a small change in the hook (play nothing past L
  instead of wrapping).**
- **The Tracker+ has no "Track len" key**, so the control has to be a screen button or a
  combination (section 4.4).

## 3. What stock firmware does

### 3.1 The sequencer (FINDINGS sections 12, 22, 29)

- **One position per track, kept equal.** Each track has its own step (`+0x88`) and a
  count of steps since its loop started (`+0x8a`). In pattern and song mode,
  `seq_advance_step` (`0x600ad010`, after each track's micro-step 48) moves every track
  by the same rule against the same last step: track 1's length byte, `seq+0x36`, or a
  looping selection's end. So all 16 stay equal. Only performance mode (tracks 1–8, own
  lengths and play modes) makes them differ.
- **The pattern ends when track 16 wraps** (`seq+0x33`). Then song mode moves on, a
  queued pattern starts, and every track goes back to step 0.
- **Track 1's step is also the master for three readers:**
  - the next-pattern preload request (ITCM `0x2396`/`0x2574`, at micro-step 1 of the last
    step);
  - the metronome (`0x2346`);
  - the Pattern screen's play row (`0x600e208e` and two more).

  This rules out the obvious design of making each track's `+0x88` wrap at its own
  length. With a short track 1, song mode would never request the next pattern, the
  play row would loop, and the metronome would follow track 1.
- **Step data is read in one place per track.** The 6-byte copy into the work record at
  micro-step 1 is at ITCM `0x2170` (`bl memcpy_small`, r1 = `*(seq+0x14) + 0x1d +
  0x301·t + 6·step`). Roll's look-ahead (`seq_distance_to_next_note_q`, ITCM `0x3958`,
  called at `0x33a2` and `0x3520`) also reads step data: it counts the steps to the next
  note, up to the pattern's end.
- **Track lengths in the `.mtp`.** Each track in a pattern file has its own last-step
  byte, but stock reads only track 1's. Its length edit writes tracks 1–8's bytes and
  Duplicate writes all 16. So a mod can't keep its lengths there: stock would overwrite
  or ignore them (section 29).

### 3.2 The emulator proof

Scratch script (not in the repo): stock 1.2.0, `seq_timing.make()`, MIDI instruments on
tracks 1, 2 and 16, one note per step whose pitch is the step number. A Python code hook
at ITCM `0x2170` rewrote r1 to step mod L(t). Everything else ran stock code.

| Case | Result |
|---|---|
| No lengths set | the same 194 MIDI events as stock over 3 passes |
| 16 steps, track 1 L = 6, track 16 L = 5, 3 passes | track 1: 0–5 0–5 0–3, three times; track 16: 0–4 ×3, 0, three times; track 2: 0–15 three times |
| Track 16 short | the pattern still ends after 16 steps (track 16's `+0x8a` still reaches 15) |
| Play positions | `+0x88` of every track stays 0..15: play row, preload and metronome unchanged |
| Play from step 9, L = 6 | 3 4 5 0 1 2 3, then everything restarts at 0 on step 16 |
| Song mode A → B, A's track 1 L = 6 | preload requested on step 15 as stock; A plays 0–5 0–5 0–3, then B starts on its step 0 |
| Looping selection 8..11, L = 6 | plays steps 2..5, which is (8..11) mod 6 |
| Performance mode, track 1 backward, performance length 16, L = 6 | (0, 15, 14, 13, …) mod 6: 0 3 2 1 0 5 4 … |

## 4. Design

### 4.1 Playback

A track's **effective length** E(t) = its length L(t) when 1 ≤ L(t) < the pattern's
length, otherwise the pattern's length (L = 0 means "no length of its own"). A track
whose E is the pattern's length behaves exactly as stock, so a pattern with no lengths
plays as stock.

**The step-copy hook** (ITCM `0x2170`, args r4, r5): `mt_pm_step_copy(dst, src, seq, t)`
- computes s = the track's `+0x88` mod E(t), and src = the play buffer + `0x1d + 0x301·t
  + 6·s`;
- then calls the chain (stock `memcpy_small`) with (dst, src, 6).

This is the hottest path in the sequencer (every track, every step). The hook is a table
lookup and a modulo, about 30 instructions; 16 per step against the worst stock step's
9,100. The lengths come from the table of the pattern whose number is `project+0x6c8`,
the playing pattern, which the buffer swap sets.

What follows from it, all stock behaviour unchanged:

| Situation | What a track with its own length does |
|---|---|
| Pattern loop (pattern mode) | loops its E steps; at the pattern end every track restarts on step 0 |
| Song mode, a queued pattern | the same; the next pattern's lengths apply from its first step |
| Play from a position p | starts on step p mod E (as if the pattern had played from 0) |
| Looping or once-only selection | plays (the selection's steps) mod E; a selection inside the track's first E steps plays exactly |
| Performance mode | plays (whatever step the performance play mode picks) mod E; tracks 1–8 don't realign at the pattern end, as in stock performance mode |
| Break FX on a short track's step | acts each time that step plays; the first time ends the pattern |
| Micro-move, gate, roll, arp, chord, Chance | unchanged; they act on the work record that the copy fills |
| Tempo and Swing FX | act when their step plays, so they repeat with the track's loop |

**Known limit:** performance mode's per-track pattern change (Remix) plays another
pattern's track with the playing pattern's lengths.

### 4.2 Roll's look-ahead

`0x33a2` and `0x3520` (`bl seq_distance_to_next_note_q(seq, t, step)`, no args needed).
For a track with its own length, the hook counts from s = step mod E up to E − 1. Stock
counts to the pattern's last step, and the steps past E would otherwise make a Roll last
too long. Otherwise it calls the chain.

### 4.3 Live recording

Recording while playing writes to the track's `+0x88` step, or the next step when the
hit is late, wrapping at the pattern length. On a short track that lands in the dimmed
steps. The two writers:
- `seq_record_live_note` (ITCM `0x15c8`, through 8 flash veneers whose literal is
  `0x15c9`);
- `seq_record_note_off_q` (`0x600ac700`).

Both also apply their own rules (the step a note was recorded on, `+0x82`; recording
options).

**Phase 1 results** (FINDINGS section 29):
- **No single place picks the row.** Both functions compute the step inline and write
  it from several stores, so the change has to wrap the whole call.
- **The callers are a closed set:**
  - `seq_record_live_note` is reached only through its 8 veneers, from 14 callers: the
    Pattern pads, the sequencer module's input path and several other screens. One hook
    on each veneer's literal word (`words`, chained) covers them all.
  - `seq_record_note_off_q` has 18 callers: 16 `bl` and 2 tail calls (`b.w` at
    `0x600f5500` and `0x600f9418`), with no pointer to it.
- **Arguments:** both take `(seq, source, note, r3, a 16-bit stack argument)`, so the
  wrapper is a 5-argument C function. Both record only while `settings+0xce` is 2 or 3.
- **The two tail calls need a small patcher addition.** `tools/patch_ptf.py` writes a
  `bl` at every branch site, which would break a tail call. A `"tail": true` flag on a
  branch would write `b.w` instead (same encoding with bit 14 clear; chaining reads the
  old target the same way). It is a change to the patcher, so it is tested by
  `tools/test_layout.py` and the release tier, and is its own commit before phase 5.

**Approach: move the write, don't recompute it.** A wrapper around each entry point:
1. masks the audio interrupt, as stock's `voice_apply_fx` does in the main loop. MIDI
   input recording already runs inside the interrupt;
2. saves, for each track with its own length, the rows at step s and s + 1 (mod the
   pattern length), the only rows stock can write;
3. calls the chain;
4. moves any row that changed to (that row) mod E, and puts back the old contents of the
   dimmed row;
5. unmasks.

This works whatever the stock rules decide, so it doesn't depend on internals such as
the quantize flag. The wrapper exits at once when recording is off or no track has its
own length, so with no lengths set recording is exactly stock.

### 4.4 Setting a track's length (UI)

| Option | How | Hooks | Risk |
|---|---|---|---|
| **A. "Trk Len" on page 3 (recommended)** | Pattern screen, More twice; hold Trk Len and turn the encoder: the cursor's track gets shorter or longer. Optional: hold Trk Len and press a pad (pad n = length n + 1). The button's second line shows the track's length while held ("6", or "--" when it follows the pattern) | page-3 bind (chain `0x600e29c6`), label text and second line for button 6 (`0x600e58b4`, `0x600e5914`), colors and data chained onto button 7's (`0x600e594a`, `0x600e5976`), the encoder word `0x600df2f0` (chained); pads word `0x600df2f8` if wanted | low. Button 6 is free whatever humanize (2) and copypattern (2 or 3) do. The encoder is intercepted only while the button is held |
| B. Shift + hold Length + turn | changes the cursor's track instead of the pattern | one `bl` at `0x600c9172` | high: on the Pattern screen Shift + a screen button mutes that track (the manual), so pressing Shift before Length mutes track 2. Order-dependent and easy to trip |
| C. Config → Project Settings, 16 track lengths | menu items as swing and humanize have | Config constructor hooks (shared, chained) | fits only per-project lengths (section 4.6); slow to use while playing. Not recommended |

**Recommended: A.** The rules:
- The length runs 1..the pattern's length. Reaching the pattern's length means "no
  length of its own" (L = 0), so lengthening the pattern later carries such a track
  along.
- A length is kept when the pattern is shortened below it and applies again when the
  pattern grows, as stock keeps the steps past a pattern's end.
- With a selection spanning several tracks, all of them change: an **extra**, optional.
- Works in and out of record mode, as Length does.
- Marks the pattern's lengths changed, for the file (section 4.6).

### 4.5 Showing it

- **The Pattern grid.** Rows from E onwards in a track's column are drawn in that
  column's empty color, the one stock uses for a "-" field:
  - note `+0x08` → `+0x18`;
  - instrument `+0x0c` → `+0x1c`;
  - FX1 `+0x10` → `+0x20`;
  - FX2 `+0x14` → `+0x24`.

  So a dimmed step reads as "nothing plays here" while its contents stay visible.

  The grid control picks each cell's color into ctrl `+0x44`, then calls
  `patgrid_draw_cell` (`0x6010b204`) from 5 sites, one per column:
  - `0x6010b3de` note;
  - `0x6010b62c` and `0x6010b5ae` instrument (2- and 3-character text);
  - `0x6010b7be` FX1;
  - `0x6010b9c4` FX2.

  A chained hook on each:
  - finds the cell's track, row and column from the text pointer (data + 0x11d·t +
    0x13·row + 0, 5, 9 or 0xe);
  - finds the row's step from `ed+0x1e8` − 7 + row, wrapped round when `settings+0xd2`
    is 0 (otherwise rows outside the pattern are blank and stay untouched);
  - swaps in the column's empty color when step ≥ E(t) and the cell has the column's
    normal color, calls through, and restores the color.

  Muted tracks keep stock's muted color and the cursor and selection keep the highlight,
  because only the normal color is swapped. Phase 1 confirmed the mapping in all three
  `settings+0xd2` modes, and that a swapped color reaches the display list.
- **Extra, optional: the playing step of a short track.** The play row is the pattern
  position, so on a short track it moves through dimmed rows. The same hook can draw
  that track's real playing row (play row mod E) in the highlight color. Small, but not
  asked for.
- **Pads.** While Trk Len is held, light pads 1..E of the cursor's track. Stock's Length
  view lights its own pattern-length view. Optional, together with pad entry.
- **Song screen and colorsong:** unchanged. A short track's dimmed steps still count as
  "used" in the Song screen's grid, which is cosmetic.

### 4.6 Saving

**Recommended: per pattern.** Each of the 255 patterns has 16 lengths.
- Polymeter is part of a pattern's rhythm, as the pattern length already is (stock
  keeps the length per pattern).
- Per project, every pattern in a song would get the same short tracks. That breaks the
  common case of one polymetric pattern among normal ones, and there is no way to say
  "only in pattern 3".
- It costs more RAM than per project would: a 4,080-byte table against 16 bytes.

**The file**, `<project folder>/polymeter.bin`, as the other patches' files:
- magic `PMT1`;
- a count;
- one 17-byte entry per pattern that has any length (the pattern's number, 16 lengths);
- a checksum.

Most projects have a handful of entries; 255 at most (about 4.3 KB). It is read and
written from the Pattern screen's update method (the shared word `0x600403DC`, chained),
in pieces of at most 256 bytes, only while the file manager is idle, as slicethru does:
- **another project:** the table is cleared at once, then read from that project's file;
- **a change:** written a moment later;
- **Save As and a new project's first save** (the Save As lesson): chain the Save As name
  copy at `0x60114eaa`, keep the table for the new name, and write it to the new folder.
  A new project with no path keeps its lengths in RAM until that first save. The tests
  cover both, with the empty-path "New Project" (FINDINGS section 25), not an empty name;
- **stock firmware** never reads the file, so it opens the project normally and plays
  every track at the pattern's length. The patterns' own files are untouched.

**Limits** (all optional fixes later, none needed for a first version):
- Copying a pattern to another number (copypattern, the Song screen's copy) doesn't copy
  its lengths; the target keeps its own.
- Expand and Shrink stretch a pattern's steps but not its track lengths.
- Undo doesn't undo a length change.
- Like the other mods' files, the lengths are written to the project folder when they
  change, not only on Save.

## 5. Fit with the other patches

| Patch | Interaction | Change needed |
|---|---|---|
| swing | its hook on the step-advance word checks track 16's position, which stays the pattern's | none |
| humanize | edits steps in the pattern; page-3 button 2 | none (Trk Len takes the first free button after humanize's and copypattern's) |
| interpolate | reads each track's `+0x88` and the pattern's length to find its CC ramps | on a short track its ramps follow the pattern position. Needs **ip7**: read the lengths (below) and use step mod E and E. Separate patch, after polymeter |
| legato | works on note-on and note-off order | none |
| condtrig | counts a track's passes in `+0x8a`, which counts pattern passes | none. **A short track's conditions count pattern passes, not its own loops.** On a 6-step track in 16, "1:2" plays on every other pattern pass, and within one pass the track's repeats share the count. Counting its own loops would give partial loops at every realign, so the pattern count is the better rule. The Chance hook (`0x30ce`) acts on the copied step, so conditions on dimmed steps never run |
| visualizer | reads `+0x88` to make ripples from the step's note | ripples from the pattern position on a short track (cosmetic). Optional **vz6** later |
| colorsong | Song-screen usage counts dimmed steps | none (cosmetic) |
| copypattern | page-3 button 2 or 3; copies pattern files | none for the button. Lengths not copied (section 4.6) |
| slicethru, synths, masterfx, resample | not on the sequencer's step path | none. Shares the Pattern update word and the Save As name copy, both chained |

**Shared sites polymeter joins (all chained):** the Pattern update word `0x600403DC`, the
Save As name copy `0x60114EAA`, page-3 bind `0x600e29c6` (copypattern) and the label
colors `0x600e594a`. Its ITCM sites (`0x2170`, `0x33a2`, `0x3520`) and the grid, encoder
and recording sites are used by no other patch yet, but are declared with `next` so later
patches can chain them.

**Finding the lengths from another patch** (for ip7 and vz6; optional, a later phase).
Patches can't link to each other. As copypattern finds humanize by a call target, a
patch can follow ITCM `0x2170`'s `bl` to the stub, and the stub to polymeter's hook.
Polymeter would put a small descriptor right before that hook: a magic word and a
pointer to `uint32_t mt_pm_effective_length(uint32_t track)`. No descriptor means no
polymeter.

## 6. Hooks

| Site | Kind | Old | Purpose |
|---|---|---|---|
| ITCM `0x2170` | itcm_branch, args r4, r5, chained | `1df0d0f9` | the step copy: step mod E |
| ITCM `0x33a2` | itcm_branch, chained | `00f0d9fa` | Roll look-ahead (case one) |
| ITCM `0x3520` | itcm_branch, chained | `00f01afa` | Roll look-ahead (case two) |
| `0x600df2f0` | word, chained | `a1900c60` | Pattern screen encoder: Trk Len held → edit, else chain |
| `0x600df2f8` | word, chained (optional) | `d96b0c60` | Pattern screen pads: Trk Len held → pad sets the length |
| `0x600e29c6` | branch, chained (copypattern) | `ecf791fe` | page 3 bind: also bind Trk Len's button (kind 3, press and release) |
| `0x600e58b4` | branch, chained | `1cf052fa` | button 6's text, the last of 2..6; then Trk Len's button's: "Trk Len" |
| `0x600e5914` | branch, chained | `1cf02efa` | button 6's second line; then Trk Len's: the length while held |
| `0x600e594a` | branch, chained | `1cf02bfa` | button 7's colors; also sets Trk Len's (page 3 sets only 0, 1, 7) |
| `0x600e5976` | branch, chained | `1cf03ffa` | button 7's data; also sets Trk Len's |
| `0x6010b3de`, `0x6010b5ae`, `0x6010b62c`, `0x6010b7be`, `0x6010b9c4` | branches, chained | `fff711ff`, `fff729fe`, `fff7eafd`, `fff721fd`, `fff71efc` | grid cells past E in the column's empty color (note, instrument ×2, FX1, FX2) |
| 8 veneer literals `0x15c9` (`0x600ab69c`, `0x600c73cc`, `0x600cd554`, `0x600d4b34`, `0x600f1924`, `0x600f54ec`, `0x600fad84`, `0x6011a0fc`) | words, chained | `c9150000` | live recording wrapper (`seq_record_live_note`) |
| 16 `bl 0x600ac700` (`0x600ab514`, `0x600c6afc`, `0x600c6b78`, `0x600c6cde`, `0x600c6ece`, `0x600c6f96`, `0x600c71ee`, `0x600c7348`, `0x600cd4a6`, `0x600d4792`, `0x600d491e`, `0x600d4adc`, `0x600f0396`, `0x600f1904`, `0x600f57aa`, `0x6011a0a8`) | branches, chained | per site | live recording wrapper (note-off) |
| `b.w 0x600ac700` at `0x600f5500`, `0x600f9418` | branches, chained, `"tail": true` (new) | `b7f7feb8`, `b3f772b9` | the same, for the two tail calls |
| `0x600403DC` | word, chained | `991f0e60` | file I/O from the Pattern screen's update |
| `0x60114EAA` | branch, chained | `52f094fa` | Save As: keep the lengths for the new name |
| `0x600CBF9C` | version word | `34b80360` | `1.2.0-pm1` |

The old bytes are from stock 1.2.0, read for this plan; phase 2's patch.json checks them.
Phase 1 confirmed the label, grid and recording sites (FINDINGS section 29).

## 7. RAM

- **State:**
  - the lengths table: 255 × 16 = 4,080 bytes;
  - a small block (magic, the file state machine, "held" and "dirty" flags, the
    project name it belongs to, as slicethru keeps): about 100 bytes.

  About 4.2 KB in one `.mt_ram` block, no `ram_startup`. It is used only after boot, from
  the audio interrupt (reads) and the main loop.
- **Where it lands.** Alone: the OCRAM tail. In `patch/all` (MTW layout, measured from
  `build/TrackerPlus_MTW.map.json`): the OCRAM tail and the DTCM gap are full, and
  `sdram_top` has about 19 KB free after synths' late block (ends `0x83ffb4e0`). So it
  goes to the SDRAM top, which is cached. colorsong's table and synths' audio state
  already run from there on hardware.
- **The interrupt reads one byte per track per step.** A table write from the main loop
  is one byte, so a torn read is impossible. A file load fills a scratch copy, then
  copies it in.
- **ITCM tail:** the stubs grow from 36 to about 64 bytes, of 1,216.

## 8. Bundle, web patcher, version

- **Version text:** `1.2.0-pm1` (3 characters). `patch/all` becomes a new name with a
  non-hex second letter, e.g. `MPM` (not a mix name).
- **`patch/all` and the web patcher are tied.** The web patcher refuses an `all` that
  includes a mod missing from its manifest (`allIds`). So joining `patch/all` means:
  - **Bit 12, and the mix names past it.** Today a mix's first letter carries bits 8–11
    (`M`…`Z`, then `A`, `B`), and `planSelection` stops at "a letter past B". Bit 12 needs
    16 more first characters for the masks with it. Proposal: `C`…`L` for 16..25 and
    `0`…`5` for 26..31. Mix names without bit 12 don't change, and none can collide with a
    patch version (lowercase letter, lowercase letter, digit), with colorsong's `c11`, or
    with an `M`+non-hex bundle name. The web plan's text and `web/patcher.js` change; the
    harness checks every name.
  - **The harness doubles: 8,191 selections.** Measured 2026-10-25: 23 minutes and 21 GB
    for 4,095. Expect about 46 minutes for the harness alone, and about 42 GB of disk.
    The release tier's goal is about 60 minutes (CLAUDE.md), so this is worth
    reporting.

    The harness-disk plan in the shared notes (keep hashes and maps, not every `.ptf`)
    brings the disk down to about 240 MB. It doesn't fix the time; its own follow-up
    (sampled byte identity when only one patch changed) would.
- **Options:**
  1. **(recommended)** Do the harness-disk change first (its own session and commit),
     then add polymeter to `patch/all` and the web patcher with the naming change. Run the
     first full release tier once, with the user's OK for the time.
  2. Ship `pm1` on its own first (standalone `.ptf`, hardware-tested), and join
     `patch/all` and the web patcher afterwards as option 1.
  3. Keep it out of `patch/all` for good. Not recommended: the bundle is how the user
     runs the mods.

## 9. Phases

Each phase ends with the emulator tests named, and `tools/run_tests.py --tier quick`
passing. The release tier runs before any commit that changes what users get. One patch
at a time: nothing here starts until the synths Save As work is committed and the user
says go.

**Phase 1: RE confirmations (scratch only, no patch). Done 2026-10-26.**
- **The grid rig.** A scratch rig builds the grid control as the Pattern screen does:
  stock `patview_fill_rows_q`, then `patgrid_ctor` (`0x6010a3d4`) with the screen's
  descriptor, then its 4 column methods. Results:
  - every cell's text pointer decoded to the right track, row and column;
  - the 5 draw sites are one per column (the instrument has two, by text width);
  - the row → step rule held in all three row modes and with the grid scrolled to
    tracks 13–16;
  - a color swapped at the cell call reached the display list as `COLOR_RGB` at
    exactly the dimmed rows.

  Glyphs aren't emitted in the emulator (the SDRAM font table is empty), so phase 4
  checks colors, not pixels.
- **Button 6.** A kind-3 binding (`btn_bind_kind3`, `0x600cf74c`) gets r0 = 1 on press
  and 0 on release, and every stock page bind replaces or clears it.
  - Free label sites: text `0x600e58b4`, second line `0x600e5914`.
  - Colors and data have no button-6 call; chain onto button 7's (`0x600e594a`,
    `0x600e5976`).
  - The bind hook also clears the "held" flag, so a release lost to a screen change
    can't leave the encoder captured.
  - The encoder callback gets the signed delta in r0.
- **Live recording:** the closed set of 26 entry sites and their arguments (section
  4.3). Two are tail calls, which need the patcher's new `"tail": true`.
- **The scratch rigs become `tools/emu/test_polymeter.py`'s grid and button helpers in
  phases 3 and 4.** FINDINGS section 29 and the names file are updated.

**Phase 2: playback core (`patch/polymeter`, `pm1` dev builds).**
- `polymeter.c` with the table, E(t), the `0x2170` hook and the two Roll hooks.
  `patch.json`, `build.sh`, `link.ld`, a README.
- `tools/emu/test_polymeter.py`; the test sets lengths by writing the table, found
  through `.map.json`. It checks:
  - every case in section 3.2, as PASS lines;
  - no lengths = identical to stock;
  - L ≥ pattern length = stock;
  - L = 1;
  - pattern length 128;
  - all 16 tracks short;
  - Break on a short track;
  - Micro-move m100 on a short track's last step (the delayed note crosses the wrap);
  - Roll's duration on a short track;
  - sample-track and synth notes (stubs as `test_condtrig`);
  - rendering (the render loop runs the same code).
- Cost: instructions per step against stock, with 16 short tracks (limit: about 50 per
  track per step).
- `tools/test_layout.py` scenarios: polymeter alone; `all` + polymeter; reversed.

**Phase 2 results (2026-10-26).**
- **The patch.** `patch/polymeter`: 261 bytes of code and 4,112 bytes of RAM (magic,
  then 256 patterns × 16 lengths). The RAM lands in the OCRAM tail alone, and in the
  SDRAM top after patch/all's patches. The three ITCM hooks are chained.
- **The test's oracle.** `tools/emu/test_polymeter.py` checks everything against one
  rule: a track of length E must play exactly what stock firmware plays for the same
  track with its first E steps written out to the pattern's end. The short track's own
  later steps hold other notes, which must never sound. That covers every list item
  above except rendering, which runs the same ITCM code and isn't checked separately.
  Roll's look-ahead is also compared step by step with the stock function on the
  written-out pattern.
- **Results:** all checks pass on `pm1`, and on patch/all's patches + polymeter in both
  orders, including a sample instrument and a synth on a short track.
- **Cost:** counted inside the patch's code and stubs only, so it is the same in any
  bundle:
  - 22 instructions per track and step with no lengths;
  - 34 with all 16 tracks short.
- **The layout test** passes its three new scenarios, and the quick tier passes (27
  jobs). On the PMA bundle (patch/all's
  patches + polymeter), the tests of condtrig, swing, slicethru, interpolate,
  visualizer, humanize, copypattern and colorsong pass.
- **For phase 7: legato's cost check** fails on that bundle, at 151 instructions per
  track against its limit of 150.
  - That check measures the whole bundle's sequencer cost against stock, not legato's
    own. patch/all alone already measures 137 per track; polymeter adds 14.
  - Before polymeter joins patch/all, either legato's check should count only legato's
    code (as polymeter's does), or its limit should be raised. That is the user's call;
    it is unchanged so far.
  - No current test run sees it: run_tests emulator-tests only bundles of the web
    patcher's mods.
- **Docs.** The root README and `docs/PATCHING.md` don't list polymeter yet. This build
  can't set a length on the device, so it plays as stock there. They get it once the
  patch does something a user can try.

**Phase 3: the Trk Len button and the encoder.**
- Tests:
  - the bind and label with humanize, copypattern, both, and neither, and in reversed
    bundles;
  - hold + encoder changes the cursor's track (clamped 1..length; reaching the length →
    "--");
  - release returns the encoder to stock;
  - stock's own encoder behaviour is unchanged without the hold (cursor moves and edits
    produce the same RAM as stock);
  - the label's second line.

**Phase 3 results (2026-10-26).**
- **The button's place** (the user's change, 2026-10-26): Trk Len isn't fixed on button
  6 but goes on the first free one, as copypattern places itself. Humanize takes button
  2, copypattern the next free one, Trk Len the one after: 2 alone, 3 with either, 4
  with both. The patch reads that from the flash at run time (does a BL at humanize's
  site `0x600e5884`, or at copypattern's `0x600e5890`, still go to stock's
  `disp_ctrl_set_text`?), as copypattern finds humanize. So the answer is the same in
  any bundle order.
- **The hooks**, all chained, with the old bytes checked:
  - page 3's bind `0x600e29c6`, shared with copypattern;
  - button 6's text and second line, `0x600e58b4` and `0x600e5914`. They are the last of
    buttons 2..6: the hooks pass button 6's empty texts on, then set Trk Len's button's
    texts over the empty ones stock set just before;
  - More's colors and data, `0x600e594a` and `0x600e5976`;
  - the encoder word `0x600df2f0`.

  The code grows to 1,313 bytes. The RAM is 4,112 bytes as before: a held flag and the
  label's 4 bytes fit in the old padding.
- **The rules as built:**
  - a press sets up the table, if it isn't yet;
  - the encoder works only while Trk Len's button is still bound to the patch and stock's own
    guards (an overlay, `0x6010e0f8`) are clear. Otherwise it goes to stock;
  - page 3's bind clears the held flag, so a release that went to another page can't
    leave it set.
- **Tests:** `test_polymeter` gained the bind, labels, hold, release and stock-encoder
  checks.
  - Without the hold, alone: 8 encoder turns (record off, record on in each column)
    leave the same RAM and display calls as stock. Also after a press and release.
  - In bundles this comparison is skipped: other patches change the RAM there.
  - The rig stubs stock's pad refresh and two queries the rig has no objects for. The
    instrument column uses sample instruments: past 63 the label code needs the synth
    system.
- **Other patches' tests.** `test_humanize` and `test_copypattern` expected every
  button after theirs to stay unbound, and copypattern's also buttons 2 and 3 outside
  record mode. They now leave out the next one, Trk Len's (bound in both modes), when
  polymeter is in the build.
- **Bundles.** `tools/test_layout.py` adds humanize, copypattern and both with
  polymeter, in both orders. It passes. The emulator tests pass on all of them:
  - polymeter's, humanize's and copypattern's on each of the six;
  - every patch's on patch/all's patches + polymeter, in both orders.

  The one exception is legato's cost check, as in phase 2 and still 2,416 in all: the
  button adds nothing to the sequencer's cost.
- **Not in the plan's list, noticed while building:**
  - the second line updates on a press and on each turn, not when the cursor moves to
    another track while the button is held (arrow buttons). Releasing and pressing
    again shows the new track's length. Optional to fix: phase 6 hooks the Pattern
    screen's update, which could refresh it;
  - lengths aren't marked changed for the file yet (phase 6).

**Phase 4: the grid.**
- Tests on the rig from phase 1:
  - cells past E get the dim color;
  - the others are byte-identical to stock's display list;
  - muted tracks and the cursor keep their colors;
  - the wrapped-rows setting;
  - the single-parameter views.
- Then the optional play-row marker, if the user wants it.

**Phase 4 results (2026-10-26).**
- **Hooks.** The 5 `bl patgrid_draw_cell` sites (old bytes as in section 6), each chained
  (`mt_pm_cell_note`, `_instr3`, `_instr`, `_fx1`, `_fx2`, one chain word each). Each
  decodes the cell from the text pointer against ctrl `+0x2c`, maps the row to its step
  with `ed+0x1e8` and `ed+0x1ea` (what `patview_fill_rows_q` filled the rows with), wraps
  it round when `settings+0xd2` is 0 (a true modulo, so a 4-step pattern's rows wrap
  more than once), and swaps ctrl `+0x44` to the column's empty color only when it holds
  the column's normal color. It puts the old color back after the call.
- **The encoder** held on Trk Len also refreshes the grid control (`ed+0x29b`), so the
  dimming follows each turn.
- **Code** 1,885 bytes (572 more); RAM unchanged (4,112 bytes).
- **Layouts, found while testing** (FINDINGS section 29): the grid's method 0 sets its
  layout from `ed+0x1ff` and ctrl `+0x18`. Bit 0 of `ed+0x1ff` (an overlay open) draws 8
  rows; bits 1 and 2 (a side panel) draw 3 or 2 tracks; a single-column view (ctrl
  `+0x18`, from `ed+0x4dc`, one bit per column) draws 8 tracks of that column. All of
  them draw a subset of the same row texts, so one row → step rule covers them.
- **Tests** (`test_polymeter.py` 15–16), against stock firmware drawing the same pattern
  with the short tracks' steps E.. left empty, cell by cell and the whole display list:
  - lengths 1, 6, 15 with the cursor highlight and a muted track on dimmed steps;
  - the three row settings at both ends of the pattern;
  - a 4-step pattern; 128 steps with length 100;
  - scrolled to tracks 13–16;
  - the overlay and side-panel layouts and the 4 single-column views;
  - 3-character instrument texts (the fifth site); every site dims at least one cell;
  - no lengths, a table not set up, lengths for another pattern, lengths at or above
    the pattern's: every cell and the display list as stock;
  - the grid refresh on a turn.

  A variant built without the normal-color check fails the cursor-and-mute case, so
  the oracle catches a swap that overrides stock's own colors.
- **Not covered by the emulator:** glyphs (the SDRAM font table is empty there), so the
  look on the screen is for the hardware check.
- **Results:** pm1 0 failures (100 checks); the six humanize/copypattern combinations and
  patch/all + polymeter in both orders pass every patch's test except legato's known
  whole-bundle cost check (still 2,416 in all: the grid isn't on the sequencer's path);
  the layout test 0 failures; the quick tier passes (27 jobs, 8.4 min).

**Phase 5: live recording.**
- Tests:
  - recording during play onto a short track, early and late in a step;
  - on the last step of E (a late hit goes to step 0, not E);
  - note-off recording;
  - MIDI-in recording from the interrupt path;
  - a track without a length records exactly as stock;
  - the dimmed rows keep their old contents.

**Phase 5 results (2026-10-26).**
- **The patcher's tail calls.** `"tail": true` on a branch writes a `b.w` (same
  encoding as a BL with bit 14 clear), chained as a BL is. A site's old bytes must be a
  BL, or a `b.w` with `"tail"`; `"tail"` with `"replaces"` and a `"tail"` other than
  true are refused. The same in `web/patcher.js`. `tools/test_layout.py` checks every
  branch site keeps its form, chains a copy of polymeter on its two `b.w` sites, and
  checks the four refusals; `tools/test_web_patcher.py` checks the web patcher makes the
  same bytes (polymeter alone and chained) and refuses the same four.
- **Hooks.** The 8 veneer words of `seq_record_live_note` (`words`, chained) and the 18
  calls of `seq_record_note_off_q` (16 `bl`, 2 `"tail"`), one hook and chain word each,
  all through one wrapper.
- **The wrapper.** When no track of the pattern has a length it calls straight through.
  Otherwise, with the audio interrupt masked, each short track's rows position ..
  position + 3 that lie past E get their step mod E's contents, and their own contents
  are kept on the stack; then it calls the chain with the interrupt as the caller left
  it; then, masked again, a row stock changed goes to its step mod E and each dimmed
  row gets its own contents back.
  - **Changed from section 4.3, found while testing** (FINDINGS section 29): stock's
    recorders mask and enable the audio interrupt themselves, so it can't stay masked
    across the call. The wrapper masks only its own copying. Since the position can then
    move on by one during the call, it maps 4 rows, not 2.
  - Stock chooses the track by "no note sounding", not by an empty step, so the map-in
    matters mainly for note-offs: stock writes one only onto an empty row, and the
    map-in makes that the row's real step.
- **Code** 3,561 bytes (1,676 more); RAM unchanged (4,112 bytes); its copies take 832 bytes
  of stack while recording onto short tracks (the stack has 20 KB, at most 8 KB of it
  seen in use).
- **Tests** (`test_polymeter.py` 17), against stock recording the same hits onto the
  pattern with each short track's first E steps written out to the end:
  - early and late hits; a late hit on E's last step and on the pattern's last step
    (both go to step 0); a hit while a note sounds (goes to the next track);
  - note-offs in the same step and two steps later, and onto a step holding a note
    (kept);
  - length 1; 128 steps with length 100; two short tracks; a track without a length and
    no lengths at all (as stock, the interrupt untouched);
  - every veneer and note-off site (2 tail calls) gives the same result;
  - the patch's 54 writes to the pattern all happen with the interrupt masked; stock is
    entered with the interrupt as the caller left it, enabled or masked.

  A variant built without the map-in fails 11 of the 12 cases, so the oracle catches it.
- **Not covered by the emulator:** the audio interrupt really arriving during the call
  (the emulator runs no interrupts), and MIDI-in recording from inside the interrupt;
  the "masked by the caller" case stands in for that path.
- **Results:** pm1 0 failures (137 checks); the layout test 0 failures; the web
  patcher harness 0 failures (every selection rerun, 33 min); the six page-3
  combinations and patch/all + polymeter in both orders pass every patch's test except
  legato's known whole-bundle cost check (still 2,416: recording isn't on the
  sequencer's tick).

**Phase 6: the file.**
- `fakefs` tests, as slicethru's and swing's:
  - write and read back;
  - a bad checksum loads nothing;
  - another project clears and loads;
  - Save As and a new project's first save (empty path) keep the lengths and write
    them to the new folder;
  - writes only while the file manager is idle, in pieces of at most 256 bytes;
  - stock firmware with the file present opens and plays normally.

**Phase 7: `patch/all` and the web patcher.**
- Gated by section 8: the harness-disk change and the naming change, each its own
  commit.
- Then:
  - polymeter joins `patch/all`;
  - `web/manifest.json` gets bit 12;
  - `--tier release --update`, with the runner's measured estimate stated first;
  - the README, `docs/PATCHING.md` and `tools/test_layout.py`.

**Later, separate patches (optional):**
- ip7 (interpolate follows track lengths) and vz6 (visualizer ripples), through the
  descriptor in section 5;
- copying lengths with copypattern;
- scaling lengths on Expand and Shrink.

## 10. Hardware check (for the user, on `pm1` alone first)

1. Boot; Config shows `1.2.0-pm1`.
2. **Set a length.** A 16-step pattern with a note on every step of tracks 1 and 2. On
   page 3, hold Trk Len, turn the encoder until track 1 shows 6. Steps 7–16 of track 1
   are dimmed.
3. **Play.** Track 1 repeats its first 6 steps (6, 6, then 4) while track 2 plays 16;
   both start together at the top of every pass. The play row keeps moving down all 16
   rows.
4. **Song mode.** Two patterns in the song, the first with the short track: the second
   starts on time and plays normally.
5. **Play from a later step** (move the cursor to step 10, Play from position): track 1
   starts on step 4 (10 mod 6).
6. **Record live** on track 1 while playing: the notes land in its first 6 steps, never
   in the dimmed ones.
7. **Save, reopen, Save As** to a new name: the lengths come back in both. Open the
   project with stock firmware: it plays all tracks at 16.
8. **Mute, swing, a Chance condition** on the short track (if condtrig is in the build):
   all behave as before.

## 11. Risks

- **The ITCM hook runs on every track's every step.** A bug there stops or garbles all
  playback, and a crash in the audio interrupt freezes the unit. Mitigation:
  - the hook is tiny;
  - the emulator tests cover every play mode above, with stock-identical output when no
    length is set;
  - `pm1` goes to hardware alone before any bundle.
- **Live recording has 26 entry sites** (phase 1). Two of them need the patcher's new
  tail-call sites. The wrapper masks the audio interrupt for the length of one record
  call. A missed site would only mean a note recorded into a dimmed step from that path,
  never a crash. If it can't be made exact, the fallback is to say so and ask: the rest
  of the patch doesn't depend on it.
- **The grid hook depends on the control's data layout** (rows of 0x13 bytes, 15 rows
  per track, column offsets 0/5/9/0xe), confirmed in phase 1. A cell that doesn't decode
  to a track and row in range is left as stock draws it, so a view the rig didn't cover
  stays undimmed rather than wrong.
- **SDRAM state read from the interrupt**: cached, and already done by synths and
  colorsong on hardware.
- **The web harness** (section 8): time and disk.
- **The original wasn't seen** (section 2): the loop-until-realign reading is the user's
  and fits the description, but it isn't confirmed.

## 12. Questions for the user

1. **Behaviour:** short tracks loop until the pattern ends, then everyone restarts (as
   planned). Or should a short track stop at its end and wait? The video would settle
   it.
2. **Storage:** per pattern (recommended), or per project?
3. **Control:** a "Trk Len" button on page 3 (recommended), or something else?
4. **Extras** (each optional, none built unless asked):
   - pad entry and a pad view while Trk Len is held;
   - several tracks at once with a selection;
   - a marker for a short track's real playing row.
5. **Web patcher:** do the harness-disk change first and then join everything (option
   1), or ship `pm1` alone first (option 2)?
