# More MIDI chords (inversions): investigation and plan (firmware 1.2.0)

The MIDI Chord step FX (`0`, FX type 14) plays a step's note with up to three more notes
above it, from a fixed list of 29 chords. The goal is to make inversions possible: for
example G3–C4–E4, which is G3 with the intervals 5 and 9 ("59"), not in the list.

All addresses are firmware 1.2.0. The RE facts are in FINDINGS section 32. Scratch proofs
were run in the emulator against the stock image.

Status (2026-10-30): `ch1` is built without the Arp (`patch/chords`, phases 1–3 and 5 of
section 9), on its own: not in `patch/all` or the web patcher. It is emulator-tested
(`tools/emu/test_chords.py`) and passed the hardware check (user, 2026-10-30: also
together with legato and condtrig). `ch2` adds the Arp (phase 4, section 12) and passed the
hardware check too (user, 2026-10-31). Nothing is committed.

## 1. Verdict

**Stock 1.2.0 can't do it, and you're not overlooking anything.** The chord value is an
index (0–29) into a fixed table of 30 interval sets, all built upward from the step's
note. There is no inversion option, no below-root interval, no transpose or octave
setting for chord notes, no hidden setting, and no FX that combines with Chord to reorder
its notes. The manual (section 7.39) describes the same "defined library of chords" and
lists the same 29. The only stock way to an inversion is to use two tracks on the same
MIDI channel (section 2.6).

**Feasible, and cheap: no patcher changes, no patch RAM, no settings file.** The
evidence:

1. **The FX value is a byte, and stock uses only 0–29 of it.** Values 30–255 are free.
   Stock firmware already plays them safely: just the step's note, with its note-off
   (emulator, every value 30–255). The stock Pattern grid draws them without a fault
   (emulator, all 226 values), as odd text. So a project made with the patch still opens
   and plays in stock firmware. Extra chords there sound as single notes.
2. **The playback path for a new chord can reuse stock code exactly.** A stock chord sends
   the same messages as one ordinary stock note-on (or note-off) per chord note, on a
   copy of the step's record with the note moved up and the chord value set to 0. This
   was proven in the emulator over 6,000 random cases: all 29 stock chords, MIDI channels
   and synths, every velocity source, the MIDI octave setting, roots up to 127, FX1 or
   FX2. All 37,496 compared messages matched, and a deliberately broken version failed.
   So a new chord can be played as "one stock call per note", with no reimplemented MIDI
   logic.
3. **Every way into the chord code is hookable with the existing tools.**
   - Note-ons: `seq_trigger_note` has exactly three callers, the ITCM BLs at `0x22f8`,
     `0x285c` and `0x2fd8` (legato already chains `0x2fd8`).
   - Note-offs: every chord note-off goes through one flash word, the ITCM veneer's
     target at `0x601acd80`. That covers the next note, OFF/CUT/FADE, the gate end, mute
     and stop.
   - Arp: it uses the same table through the ITCM literal at `0xcc0`. Its two callers
     are the ITCM BLs at `0x2428` and `0x32ce`. Borrowing a stock table slot for the
     length of one call makes an extra chord arpeggiate exactly like a stock one: 957
     chord × Arp-value cases, 0 mismatches (section 4.3).
   - The encoder and the cell text: the same two chained sites condtrig already uses for
     Chance.

**Recommended: one fixed, complete chord library (option A and option B combined, section
3).** Values 30–236 hold, in this order (section 4.1):
- the inversions you asked for, in order of importance: major and minor triads, then
  maj7, min7 (and dom7), right after the stock list;
- the other inversions of stock chords (27);
- every other chord of one to three extra notes that fits inside an octave (167);
- 237–255 stay free for later.

Turning the encoder right past the last stock chord (`57B`) goes straight to ` 38`,
` 59`, ` 49`, ` 58`: the major and minor triad inversions. The cell shows the digits,
like stock. Per-project custom chords (option C) are not recommended (section 3.3).

## 2. What stock firmware does

### 2.1 The value and the table

- **Storage.** Byte 3 (FX1) or byte 5 (FX2) of the 6-byte step, as for every FX. The
  value is 0–29: `fx_max(14)` = 29 and min 0, so the encoder stops at 29.
- **The table.** 30 `int16` interval bitmasks at `0x60039bfc`. Bit i set means "also send
  the root + i semitones". The engine's loop runs over i = 0..14. Every entry has bit 0
  (the root). Entry 0 is the root alone (shown `---`).
- **The labels** are a separate table of thirty 4-byte strings at `0x60039d90`: `---`,
  ` 27`, ` 28`, ` 36` … `57B`. They are not computed from the masks. `fx_value_text`
  (`0x600afb48`, chord case `0x600afc56`) indexes it with the value **without a bound**,
  so value v ≥ 30 reads past it into the next tables.

| Value | Label | Notes (semitones over the step's note) | Value | Label | Notes |
|---|---|---|---|---|---|
| 0 | `---` | 0 | 15 | `27B` | 0 2 7 11 |
| 1 | ` 27` | 0 2 7 | 16 | `369` | 0 3 6 9 |
| 2 | ` 28` | 0 2 8 | 17 | `36A` | 0 3 6 10 |
| 3 | ` 36` | 0 3 6 | 18 | `378` | 0 3 7 8 |
| 4 | ` 37` | 0 3 7 | 19 | `379` | 0 3 7 9 |
| 5 | ` 47` | 0 4 7 | 20 | `37A` | 0 3 7 10 |
| 6 | ` 48` | 0 4 8 | 21 | `37B` | 0 3 7 11 |
| 7 | ` 57` | 0 5 7 | 22 | `479` | 0 4 7 9 |
| 8 | ` 5A` | 0 5 10 | 23 | `47A` | 0 4 7 10 |
| 9 | ` 5C` | 0 5 12 | 24 | `47B` | 0 4 7 11 |
| 10 | ` 67` | 0 6 7 | 25 | `489` | 0 4 8 9 |
| 11 | ` 7C` | 0 7 12 | 26 | `48A` | 0 4 8 10 |
| 12 | ` 7E` | 0 7 14 | 27 | `48B` | 0 4 8 11 |
| 13 | `279` | 0 2 7 9 | 28 | `57A` | 0 5 7 10 |
| 14 | `27A` | 0 2 7 10 | 29 | `57B` | 0 5 7 11 |

So your reading is right: the value's label is the hex intervals above the step's note,
and a chord has at most three extra notes. The "0" before the digits is the FX letter,
not part of the value. The value itself is a list index, not encoded digits.

### 2.2 How chord notes are sent (emulator)

- **Which instruments.** MIDI instruments (channels 1–16) and the synths (S1–S3). This
  matches the manual's "Affects MIDI / Synth". On a sample instrument (tracks 1–8) the
  chord does nothing: one voice note-on with the root, and no MIDI.
- **Which column.** FX1 wins if both columns are Chord. A chord counts only if the other
  column isn't Arp (with Arp, the step's note is arpeggiated instead; section 2.4).
- **Notes.** Ascending, one note-on per set bit, all at the same tick. Notes above 127
  are skipped: note 122 with ` 47` sends 122 and 126, not 129.
- **Velocity and channel.** Every chord note gets the same velocity as a plain note would:
  the step's `V` × 127 / 100, or the instrument's default, plus the performance velocity
  offset. Channel: the instrument's.
- **Note-offs.** One per chord note, in the same order, when the track's note ends: the
  next note, an OFF/CUT/FADE step, the gate end (`q`), mute, or stop. They are computed
  from the same stored record, so they always match the note-ons.
- **Synths.** One host note-on per chord note (a maj7 gave four). How many sound depends
  on the synth's Polyphony.

### 2.3 Values above 29 in stock firmware (emulator)

| Where | Values 30–255 |
|---|---|
| Playback (trigger and note-off) | the step's note alone, with its note-off (both guarded by `cmp #0x1d`) |
| Arp over the chord | chromatic, like value 0 |
| Pattern grid text | text from the next tables in flash: `128`, ` 96`, `1/3`, `/24`, `spa`, blanks, and some control bytes. Drawn without a fault for all 226 values, and never past the 4-byte cell buffer |
| Encoder | one turn sets min(29, value ± n): the cell jumps to `57B` |
| Random FX value (`x`, 16) on a chord | clamped to 0..29 (ITCM `fx_max`): it never produces 30+ |
| Pattern files | values are plain bytes. `pattern_read_file` doesn't check them (FINDINGS section 25) |

So stock firmware can open a project with the new chords. They sound as single notes and
show odd text. Section 9 phase 1 adds a file round trip and a stock grid render to the
tests. Control bytes in a cell on the real display were not checked on hardware
(section 11).

### 2.4 The Arpeggiator uses the chord table

Arp (`A`, type 21) needs a Chord in the other column (manual 7.15). At the arp's start,
the step's chord value is copied into the track's arp state (ITCM `0x327e`, track
`+0xac`). `arp_next_note_q` (ITCM `0xb74`) then reads the same table through its own
literal (ITCM `0xcc0`):
- values 1–29: that chord's notes, folded into one octave above the step's note (`5C`
  arpeggiates C, F);
- 0 or ≥ 30: all 12 semitones (manual: "value 0 will play notes through a chromatic
  scale").

So the patch has to cover the Arp too, or a new chord under an Arp would play chromatic.

### 2.5 Entering values

- **Encoder** (`seq_change_selection_fx_value`): the cell gets min(`fx_max`, value + Δ).
  The max comes from the BL at `0x600af1b2` (condtrig raises Chance's there).
- **Pads, Fill, random fill:** scaled into `fx_min`..`fx_max` through other call sites,
  so stock chords only, as condtrig found for Chance.
- **Live recording:** writes no chord values.

### 2.6 What stock can do toward inversions (the workaround)

- **Two tracks on the same MIDI channel.** For example G3 plain on one track and C4 ` 47`
  on another give G3 C4 E4 G4: the 2nd inversion with the fifth doubled. E3 plain under
  G3 ` 5C` gives E3 G3 C4 G4. Notes of different pitches from two tracks are independent
  (FINDINGS section 24), so this works. But it costs a track, and it can't drop the
  doubled note.
- **Some 4-note entries happen to be inversions of other chords.** Each is a full chord
  of its own, not a triad inversion:
  - `479` (C6) = Am7 over C;
  - `379` (Cm6) = Am7♭5 over C;
  - `378` = Abmaj7 over C;
  - `489` = AmMaj7 over C;
  - ` 27` and ` 57` (sus2, sus4) are inversions of each other.
- **Missing:** all four major and minor triad inversions (` 38`, ` 59`, ` 49`, ` 58`),
  both diminished ones (` 39`, ` 69`), and most 7th-chord inversions.

## 3. The options

### 3.1 Option A: more built-in chords

- **The table can't be extended in place.** It's in flash, and the next table follows it
  directly. Both guards (`cmp #0x1d`, three of them) are instructions in the middle of
  functions, two of them in ITCM. Changing them would take a new kind of patch, "replace
  these instruction bytes", in `patch_ptf.py` and the web patcher. That isn't needed.
- **Remapping stock entries** would change what existing projects play. No.
- **Values 30–255 with hooks around the stock code** work (section 4). The FX value's
  limit is 256 values in total, 226 of them free.
- **Stock firmware with such a project:** see 2.3. It doesn't crash; the new chords play
  as single notes.

### 3.2 Option B: free entry of the digits

- **Three digits 0–F don't fit in a byte.** Sets of one to three distinct intervals from
  1–15 number 575, but a byte has 256 values and stock owns 30 of them. There is no other
  free byte in the step.
- **But every chord inside one octave does fit.** Sets of one to three intervals from 1–11
  number 231; 26 of them are stock chords, which leaves 205 new. That is every
  close-position chord of two to four notes, and therefore every inversion of every
  stock triad and 7th chord. That leaves 25 values; 19 of them stay free (4.1).
- **Typing digits directly isn't practical.** The FX cell has no digit cursor. The pads
  map linearly onto the value range. A digit editor (for example the pads as a chromatic
  keyboard) would need new pad and screen RE and a new editing mode. That is a much
  bigger patch than the gain over scrolling a complete list.

So "free entry" becomes "a complete list". You still pick by the digits you see, and
nothing within an octave is missing.

### 3.3 Option C: per-project custom chords

This works, but it costs much more than it gives:
- **A Config → Project Settings page** to edit chord slots. Three digit lists per slot, or
  one list per slot picking from the catalog anyway. That means hooks on the shared
  Config constructor sites.
- **A checksummed `chords.bin`** in the project folder, SD I/O from an update method, and
  the Save As / New Project handling (FINDINGS on Save As; the bt4, sw2 and sy9 lessons).
- **About 40 bytes of RAM**, plus file state.
- **A step's chord would then depend on a side file.** Copying a pattern to another
  project, or editing a slot, silently changes what earlier steps play.
- **The only gain over the fixed library** is voicings wider than an octave.

Not recommended now. If you later want particular wide voicings, the free values
237–255 can take them, a one-line edit each.

## 4. Design (recommended)

`patch/chords`, symbol prefix `mt_ch_`, version text `1.2.0-ch1`.

### 4.1 The library (values 30–236)

The library is a 207-entry `uint16` mask table in the patch's flash. It is our own
content, computed from music theory, not copied from stock. Labels are computed from the
masks. The ids are fixed forever once released, because saved projects store them.

- **30–42, the inversions you asked for, in order of importance** (user, 2026-10-30):

  | Id | Shows | Chord | Example (from the step's note) |
  |---|---|---|---|
  | 30 | ` 38` | major, 1st inversion | E: E G C |
  | 31 | ` 59` | major, 2nd inversion | G: G C E |
  | 32 | ` 49` | minor, 1st inversion | E♭: E♭ G C |
  | 33 | ` 58` | minor, 2nd inversion | G: G C E♭ |
  | 34 | `378` | maj7, 1st inversion (the same notes as stock `378`) | E: E G B C |
  | 35 | `459` | maj7, 2nd inversion | G: G B C E |
  | 36 | `158` | maj7, 3rd inversion | B: B C E G |
  | 37 | `479` | min7, 1st inversion (the same notes as stock `479`) | E♭: E♭ G B♭ C |
  | 38 | `358` | min7, 2nd inversion | G: G B♭ C E♭ |
  | 39 | `259` | min7, 3rd inversion | B♭: B♭ C E♭ G |
  | 40 | `368` | dom7, 1st inversion (extra: not asked for) | E: E G B♭ C |
  | 41 | `359` | dom7, 2nd inversion (extra) | G: G B♭ C E |
  | 42 | `269` | dom7, 3rd inversion (extra) | B♭: B♭ C E G |

  - **Sus2 and sus4 need no new entries.** Every inversion of one is a stock chord: Csus2's
    inversions are ` 5A` and ` 57`, and Csus4's are ` 27` and ` 5A`.
  - **Ids 34 and 37 repeat a stock chord**, so each 7th family is complete in one place.
    The alternative is to leave them out and use stock `378` and `479`; the list then
    shifts up by two.
- **43–69: the other 27 inversions of stock chords:**
  - triads: diminished ` 39` ` 69`, sus2♯5 ` 6A` ` 46`, sus♯4 ` 16` ` 5B`;
  - m7♭5 `469` `258`, mMaj7 `458` `148`;
  - aug7 `468` `248` `26A`, augMaj7 `478` `348` `159`;
  - sus2add6 / 7sus4 `257` `35A`, sus2♭7 `58A` `357` `249`, sus2maj7 `59A` `457`
    `138`, sus4maj7 `267` `45A` `168`.
- **70–236: every other chord inside an octave** (167), sorted like stock: single
  intervals (`  1` … `  B`), then two-interval sets, then three, each group in digit
  order.
- **237–255: reserved, not used in `ch1`.** The encoder stops at 236. The wider voicings
  in the first draft are dropped. Keeping these values free means none of them is fixed
  forever until there's a real use for it.

The chord shows exactly as stock shows chords: the hex intervals, right-aligned in three
characters (` 59`, `  7`, `459`).

### 4.2 Playback: note-ons and note-offs

- **Note-on, `mt_ch_trigger(seq, t, rec)`.** On the three `seq_trigger_note` BLs (ITCM
  `0x22f8`, `0x285c`, `0x2fd8`; chained, no `args`, so r0–r2 arrive untouched).
  1. The hook works out the effective chord value the way stock does. Then it chains
     unchanged unless all of these hold:
     - the instrument is 48–66;
     - FX1 is Chord, or FX2 is Chord and FX1 isn't Arp, and the other column isn't Arp;
     - the value (FX1's if FX1 is Chord, otherwise FX2's) is ≥ 30.
  2. Otherwise it copies the record onto its stack and, for each interval i of the mask
     in ascending order (root first) with root + i ≤ 127, calls `next(seq, t, copy)` with
     `copy.note = root + i` and `copy.chord value = 0`.

  Value 0 keeps the record a Chord to every other patch's checks (legato's "Chord on
  either note"). The stock code plays it as the single note through its own chord path,
  with its own velocity, channel, synth and render-mode handling. This is the proven
  equivalence of section 1, point 2.
- **Note-off, `mt_ch_note_off(r0, r1, rec, r3)`.** On the flash word `0x601acd80` (ITCM
  veneer `0x1f698` → `seq_chord_note_off_q`; chained `words` hook).
  - Stock calls it only for MIDI and synth chord records without Arp.
  - The same rule picks the value. If it's ≥ 30, the hook calls next once per interval
    on a copy, as above. Otherwise it chains unchanged.
  - Every note-off path goes through here: the next note (`0x24da`), OFF/CUT/FADE
    (`0x296e`), the gate and mute (`0x20ca`), stop and all-tracks-off (`0x600ac5a8`,
    `0x1242`), and `0x33f2`.
- **Why notes can't hang.** The note-off reads the same stored last record as stock does,
  and the library is constant, so the offs always match the ons. Editing the cell while
  the chord sounds doesn't matter: the offs come from the record captured at the note-on,
  as in stock.

### 4.3 The Arp

On the two `arp_next_note_q` BLs (ITCM `0x2428`, `0x32ce`, chained), the hook
`mt_ch_arp(seq, state)` reads the arp's chord value (state `+0x44`, track `+0xac`):
- **below 30:** it chains unchanged;
- **30 or more:**
  1. build a 2-entry table on its own stack whose entry 1 is the new chord's mask;
  2. point the ITCM literal at `0xcc0` at it and set the arp's chord value to 1;
  3. call next;
  4. put both back.

The stock arp code then does everything itself: up, down, random, rate and octave
folding. Emulator proof, with the hook modelled by Unicorn hooks: an extended id carrying
stock chord k's mask arpeggiates exactly like stock chord k, for all 29 chords × all 33
Arp values, including Random (957 cases, 0 mismatches). The literal is back to
`0x60039bfc` after every call.
- Only the audio interrupt calls `arp_next_note_q`, so nothing else sees the borrowed
  slot.
- The literal is a data word in ITCM, which MPU region 5 maps RW Normal (FINDINGS
  section 8). No patch has written ITCM at run time before, so the hardware check covers
  the Arp (section 10).
- The fallback, if that is unwanted, is a clean-room `arp_next` for extended values only,
  checked against stock the same way. It's bigger and riskier, so it's not the plan.

### 4.4 Entering and showing

- **Encoder, `mt_ch_cell_max(selection, type, value)`.** On the BL at `0x600af1b2`,
  chained (condtrig is there too). Type 14 gets 236 (the last id); other types chain. Turning right
  from `57B` goes to ` 38`, ` 59` … and stops at the last entry. Turning left goes back
  into the stock list.
- **Pads, Fill, random fill, Random FX:** stay in the stock list (0–29). This is the same
  choice condtrig made for Chance, and it keeps the patch small. Random FX on a new chord
  picks stock chords, because the ITCM clamp is to 29 (section 8, a later option).
- **Cell text, `mt_ch_fx1_text` / `mt_ch_fx2_text`.** On the BLs at `0x600df748` and
  `0x600df77c`, chained (condtrig is there too). A Chord cell with value ≥ 30 gets the
  computed digits; everything else chains. Only the pattern grid shows step FX values
  (condtrig's survey); the Fill popup only ever shows 0–29.

### 4.5 Unchanged

Stock chords 0–29 keep their behaviour exactly: every hook chains below 30. Also
unchanged:
- sample instruments;
- velocity, channel and note order rules;
- Chord + Arp on the trigger path (the arp's single notes);
- copy, paste, undo, pattern files.

## 5. Fit with the other patches

| Patch | Shared | Effect |
|---|---|---|
| legato | ITCM `0x2fd8` (chained by both) | Either order works. Legato checks "Chord (14) in either column", and every sub-call still has type 14, so legato always keeps stock order around chords. Tested in both bundle orders (phase 2). |
| condtrig | `0x600af1b2` (encoder max), `0x600df748` / `0x600df77c` (cell text), all chained | Each handles only its own FX type (Chance, Chord) and chains the rest. A failed Chance skips the step before any trigger, as in stock. |
| tracklen (polymeter) | none | Its step copy (`0x2170`) fills the work record that the chord hook then reads. |
| songloop | none | Pattern changes and stop send the chord offs through the note-off word. |
| interpolate | none | It only ramps CC a–f. |
| humanize | none | It may add `V` or `m` to a free FX slot of a chord step. Chord notes take that velocity or delay, as in stock. |
| visualizer, masterfx, synths, swing, colorsong, copypattern, slicethru, resample | none | Synth chords go through the stock host calls, so WAVE and PHASE are covered. |

ITCM tail: four new stub sites (`0x22f8`, `0x285c`, `0x2428`, `0x32ce`) of 8 bytes each,
32 bytes. `0x2fd8` already has a stub. The tail has 1,216 bytes, and today's stubs use 80
in `patch/all` (`MR8` map).

## 6. Hooks

| Site | Kind | Old bytes | Hook | Chained with |
|---|---|---|---|---|
| ITCM `0x22f8` | `itcm_branches` | `fff7d0f8` | `mt_ch_trigger` (performance step repeat) | — |
| ITCM `0x285c` | `itcm_branches` | `fef71efe` | `mt_ch_trigger` (Roll's repeats) | — |
| ITCM `0x2fd8` | `itcm_branches` | `fef760fa` | `mt_ch_trigger` (the step's note) | legato |
| `0x601acd80` | `words`, thumb | `09c50a60` | `mt_ch_note_off` | — |
| ITCM `0x2428` | `itcm_branches` | `fef7a4fb` | `mt_ch_arp` | — |
| ITCM `0x32ce` | `itcm_branches` | `fdf751fc` | `mt_ch_arp` (arp start) | — |
| `0x600af1b2` | `branches` | `00f089f8` | `mt_ch_cell_max` | condtrig |
| `0x600df748` | `branches` | `d0f73efb` | `mt_ch_fx1_text` | condtrig |
| `0x600df77c` | `branches` | `d0f724fb` | `mt_ch_fx2_text` | condtrig |
| `0x600cbf9c` | version word | — | `mt_version_fmt` | (bundles replace it) |

Each site gets its own entry point and chain word (`mt_ch_trigger_a/_b/_c` with
`mt_ch_next_trigger_a/_b/_c`, the same for the two Arp sites), sharing one body, as
tracklen does for its two roll sites. Legato's stub at `0x2fd8` passes no `args` either,
so the shared stub is allowed.

## 7. RAM and CPU

- **RAM: none.** The library and labels are in flash. The trigger and note-off copies and
  the Arp's 2-entry table live on the stack, only for the call. If the build tools need a
  `.mt_ram` block, it's 16 bytes. Either way it fits the all bundle.
- **CPU:**
  - every MIDI or synth note pays a few compares;
  - a new chord costs what a stock chord of the same size costs, plus one call per note
    (about 30 instructions each);
  - an Arp step on a new chord costs four extra stores.

  Measured in phase 2 with `test_bundle_cost.py` (800 BPM, chords on every step of 16
  MIDI tracks), with the 1 % of a block bound the other patches use.

## 8. Bundle, web patcher, version

- **Version text:** `1.2.0-ch1`. `patch/all` gets a new name when it joins, checked
  against the web mix names (an `M` + non-hex letter name, as before).
- **The web patcher has no room for a 15th mod.** `planSelection` throws "selection code
  has no room for bit 14", and the 14 mods use every mix-name character. Joining needs:
  - **A naming change for bit 14**, your choice. One idea is that the middle digit becomes
    a letter, as bit 13 does for the last one. But the bundle names (`MR8`, `MVL` …) use
    those letters, so the bundle naming would have to move too. The web patcher session
    should design this.
  - **The harness doubles:** 16,383 → 32,767 selections. Its per-selection cache redoes
    only the new half, but that half alone is as big as the whole run today. Before the
    run, the runner gives the measured estimate (expect well over an hour; worth deciding
    first).
- **Options:**
  1. **(recommended)** Ship `ch1` on its own first: a standalone `.ptf`, emulator-tested,
     then hardware-tested. Join `patch/all` and the web patcher in a later step, once the
     bit-14 naming is decided.
  2. Add it to `patch/all` only, not to the web patcher's selectable mods. Not possible
     today: the web patcher refuses an `all` that includes a mod missing from its
     manifest.
- **Later options (not in this plan):** Random FX staying inside the new library (hook
  ITCM `fx_max` at the Random FX pre-pass); pad entry reaching the new chords.

## 9. Phases

One patch, built in this order. Each phase ends with its emulator tests passing. Nothing
is committed until you say so.

**Phase 1: stock checks and the test scaffold** (`tools/emu/test_chords.py <ptf> <stock>`)
- Stock: the 2.1 table, values 30–255 = root and off (as proven), the Arp chromatic,
  the grid drawn without a fault (as proven), and an encoder turn sending a cell to 29.
- New: a pattern file with values 30–255 written and read back by the stock file code
  (`fakefs`, `project_save.py`) stays byte-identical.
- The jog's delta: what one detent and a fast turn pass to
  `seq_change_selection_fx_value`. This sets how long scrolling the list takes.

**Phase 2: playback** (`chords.c`: the library, `mt_ch_trigger`, `mt_ch_note_off`)
- **The library:** for every id 30–236, a step plays exactly root + its intervals (≤ 127)
  and the matching offs. Ids 30–42 are the list in 4.1, the masks are unique (apart from
  the two deliberate repeats, ids 34 and 37), and 30–236 together with the stock chords
  holds every set of one to three intervals within 1–11. Ids 237–255 play as stock
  firmware plays them (the root alone).
- **The oracle.** The emulator writes stock chord k's mask into an extended id's slot of
  the patched image. Then, over fuzzed patterns (legato's `fuzz_steps` style: random
  notes, OFF steps, every FX type except Arp, both columns), the patched firmware with
  that id must send exactly the messages, at the same ticks, as stock with id k. Also:
  - the patched firmware with ids 0–29 is identical to stock;
  - specific cases: Micro-move and MIDI delay, Roll (the `0x285c` path), the performance
    step repeat (the `0x22f8` path),
    gate, OFF/CUT/FADE, mute, stop, a song-mode pattern change, a failed Chance, Random
    note, `V`, and roots near 127.
- **No stuck notes:** USB and DIN on/off balance after every case.
- **Synths** (`synth_rig`): an extended chord on S1 with Polyphony 4 gives four voices,
  the same as the stock chord with the same mask.
- **Legato in both bundle orders:** chord steps with `G` in the other column keep stock
  order.
- **Cost:** `test_bundle_cost.py`.

**Phase 3: entering and showing** (`mt_ch_cell_max`, the cell texts)
- **Encoder** (condtrig's `encoder()` helper): 29 +1 → 30, 30 −1 → 29, 236 +1 → 236,
  0 −1 → 0, a mixed selection (Chord and another FX).
- **With condtrig in the build:** Chance conditions still work, and a selection holding
  both moves each in its own range.
- **Pads, Fill:** still 0–29.
- **Cell text:** every id 30–236 shows its digits; 237–255 show as stock. Ids 0–29, other FX and empty cells
  are byte-identical to stock, with and without condtrig.

**Phase 4: the Arp** (`mt_ch_arp`)
- The oracle again: an id carrying stock chord k's mask ≡ stock k, for all k and all 33
  Arp values, Arp in FX1 or FX2.
- The library's own ids: an arpeggio is the mask's notes folded into one octave.
- The literal is restored after every call: checked after every tick of the runs.
- Arp notes and their offs still go the plain-note way: no chord decomposition on arp
  steps.

**Phase 5: shipping it alone**
- `patch/chords/README.md`, `docs/PATCHING.md` (status table), the root `README.md`,
  `tools/test_layout.py` (chords alone; with legato and condtrig in both orders; `all`
  + chords), and `tools/run_tests.py` registration.
- The quick tier, then a standalone `TrackerPlus_ch1.ptf` for the hardware check
  (section 10).

**Phase 6 (after your naming decision): `patch/all` and the web patcher**
- Manifest, the bit-14 naming, the cover set, and `--tier release --update`, with the
  runner's estimate first.

## 10. Hardware check (for you, on `ch1` alone first)

Set up: MIDI out to a synth, or a DAW with a MIDI monitor. Use a MIDI instrument on
channel 1, and do the last step on S1 (a synth with Polyphony 4).

1. Step 1: C4 with chord ` 47`. You hear C E G, as before.
2. Turn the encoder right past `57B`: the cell shows ` 38`, then ` 59`. Put G3 with ` 59`
   on step 5: you hear G3 C4 E4. Put an OFF on step 9: all three stop (the MIDI monitor
   shows three note-offs).
3. A 4-note inversion: E3 with `368` (C7 over E). Turn left from ` 38`: back to `57B`.
4. Arp (`ch2`; `ch1` has no Arp support and plays a chromatic run here): G3, FX1
   ` 59`, FX2 Arp `/ 4` (or any rate). You hear G C E arpeggiated, not a chromatic run.
5. Press Stop while a chord sounds: nothing keeps ringing.
6. Save the project, load another, load it back: the cells still show ` 59`, `368`, and
   play the same.
7. The same ` 59` step on S1: three voices sound.

No Break FX and no performance mode checks (emulator only).

## 11. Risks

- **The Arp's ITCM write** (4.3): the first run-time write into ITCM. The MPU allows it
  (region 5, RW Normal) and the emulator proves the logic, but on hardware only step 4 of
  section 10 shows it. The fallback is the reimplemented `arp_next` (4.3).
- **Odd text in stock firmware.** Opening such a project in stock firmware shows junk in
  those cells, some of it control bytes. The emulator draws them without a fault. The
  real display hasn't been checked. Only matters if you go back to stock firmware with
  such a project.
- **Scrolling length.** The list is 237 entries long. Your inversions are first, a few
  clicks past `57B`, but the far end is a long turn. Phase 1 measures the jog's step.
- **Ids are permanent.** Once `ch1` ships, a released id must never change meaning, or
  saved projects change. So the order and contents of the list (section 12, questions 1–3)
  must be settled before release; the free values 237–255 can be added to later.
- **The web patcher's 15th mod** (section 8): a naming change and a much longer harness
  run.
- **Random FX, pads and Fill stay in the stock list** (by design, as for condtrig). Say so
  in the README.

## 12. As built (`ch1`, 2026-10-30)

- **Callers.** `0x22f8` is performance mode's step repeat, not a delayed note; `0x285c`
  is Roll's repeats; Micro-move and the MIDI delay go through `0x2fd8` (FINDINGS
  section 32). The test covers all three.
- **Random FX value** on a library chord randomizes around the chord's own value and
  clamps to 29, so it gives a random stock chord, exactly as stock firmware does for
  that value. Tested directly against stock firmware (the stand-in ids can't model it).
- **Cost.** 3,408 of the patch's own instructions per audio block at worst (16 MIDI
  tracks with a 4-note library chord on every step, 800 BPM); the bound is 3,800. The
  helpers are inlined and the record copy unrolled to get there.
- **Tested.** `test_chords.py` (about 80 s) passes on `ch1`, on chords + condtrig + legato
  in both orders, and on all of patch/all's mods with chords last and first.
  `test_condtrig.py` passes on both three-patch bundles; `test_layout.py` passes with
  chords alone and in both orders with patch/all's patches.
- **Before chords joins a bundle with legato (phase 6), `test_legato.py` needs two test
  changes** (the firmware is fine):
  1. its random patterns use Chord values up to 255; cap them at 29, as it caps Chance at
     100 for condtrig (with the cap, 30 of 30 patterns match stock both ways round);
  2. its site check wants legato's own stub at ITCM `0x2fd8`; with chords after legato
     the stub goes to chords, which chains to legato, so the check must follow the chain.

### `ch2`: the Arp (2026-10-30)

- **As planned (4.3).** `mt_ch_arp_a` / `_b` on ITCM `0x2428` / `0x32ce`, chained, sharing
  one body: for a library value at state `+0x44` they point the literal at ITCM `0xcc0`
  at a 2-entry table on the stack (entry 1 = the library mask), set the value to 1, call
  next, and put both back. 1,305 bytes in all, still no RAM. Two more ITCM stubs (40
  bytes of stubs alone).
- **Tested** (`test_chords.py`, now about 135 s): ids carrying stock chords 1–29 under
  every Arp value 1–33, Arp in FX1 and FX2, all messages (notes and offs) as stock (1,914
  cases); all 207 library ids under Arp 9 and 20 in both columns play only the step's
  note and their intervals; after each of about 31,000 `arp_next_note_q` calls the
  literal is stock's and the state's value restored; 237 and 255 stay chromatic.
- **Emulator note.** Unicorn itself crashes (an access violation in the host process)
  after about 1,300–1,500 four-step Arp runs on one emulator, with stock firmware as
  well as with `ch2` (control, 2,500 runs each: stock at run 1,447, `ch2` at 1,343). So
  it is an emulator limit, not the patch. The test runs 16 cases per run, one per
  track, which keeps it at about 200 runs.

## 13. Questions for you

Answered 2026-10-30: the new list starts (id 30, right after stock `57B`) with the
major and minor triad inversions, then maj7 and min7 inversions, in order of
importance; sus2 and sus4 inversions are already stock chords. The wide voicings are
dropped (4.1). The full list is kept (user, 2026-10-30), exactly as 4.1 lays it out:
- the dom7 inversions at ids 40–42;
- the repeats `378` and `479` at ids 34 and 37;
- the other inversions at ids 43–69;
- every other chord inside an octave at ids 70–236.

Answered 2026-10-30 (user): build `ch1` without the Arp, on its own first; once it works
on hardware, add the Arp (4.3) as a separate step. `patch/all` and the web patcher wait
for the bit-14 naming decision (section 8).

Answered 2026-10-30 (user): `ch1` passed the hardware check, also together with legato and
condtrig; the Arp support (4.3) is built as `ch2`, which passed the hardware check
(2026-10-31).

The dom7 inversions and the rest of the library were extras; they are now chosen.
