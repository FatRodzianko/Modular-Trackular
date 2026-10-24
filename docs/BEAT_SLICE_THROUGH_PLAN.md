# Beat Slice (Play Through): plan (firmware 1.2.0)

A new entry in the Sample Playback screen's Play Mode list. An instrument in this mode
is sliced, edited and played from the pads exactly like Beat Slice. The difference is
in playback: a note starts at its slice and plays on to the end of the sample, instead
of stopping at the next slice.

All addresses are firmware 1.2.0. `ins(i)` = instrument `i` (0..47) at
`0x20200000 + 0x174·i`; `voice` = a sample voice object (section 2.3); `scr` = the
Sample Playback screen object `0x200254b4`. The facts are in FINDINGS section 27.

Status: phases 1 to 5 done (section 8): `patch/slicethru` (`1.2.0-bt3`) with its
voice hooks, Play Mode list entry and settings file, emulator-tested, documented and
in the layout test. Next: the hardware check (section 10), then phase 6.

## 1. Verdict

**Feasible, with 9 small chained hooks, no ITCM hooks, no patcher changes and no new
mode value on disk.** The evidence:

1. **The slice end is chosen in one place.** Every Beat Slice note goes through the
   play-mode dispatcher `0x60032abc` to the Beat Slice note-on `0x60032b50` (one call
   site, `0x60032b3a`). That function reads the slice start, and then the end: the next
   slice's start, or `0xffff` (the sample end) for the last slice. It turns both into
   frames with `0x60100694`. The end-point call is a BL at `0x60032c62`. If that call
   gets `0xffff`, the rest of the stock code (the length, the reverse start, the stereo
   partner) works it out itself.
2. **The render needs no change.** Beat Slice voices render with the 1-shot loop
   (render switch `0x600327fa`, cases 0, 4 and 5 all go to `0x600357b4`). That loop stops
   at the stored end. In the emulator, a quarter-sample slice stopped after 11,025
   frames. With the end forced to `0xffff`, the same note played 33,075 frames from
   slice 2 and 44,100 from slice 1. Both cost 4,916 instructions per 128-frame block.
3. **Every other Beat Slice behaviour tests "mode == 5" directly.** That covers the
   slice editor, auto slice, the pads, the pad lights, live recording, the Pattern
   screen and the pitch lock. The static scan finds 31 such tests in 26 functions, plus
   one on the voice's latched copy of the mode. Most of them are compares in the middle
   of a function, which a BL or word hook can't reach. So a new mode value (option a)
   can't be done with this patch system. If the stored mode stays 5 and the play-through
   choice is kept beside it, all 31 tests work unchanged, and stock firmware simply
   plays such an instrument as Beat Slice.

**Recommended: option (c), a ninth list entry backed by a per-instrument flag**
(section 3). The user gets the play mode they asked for, per instrument, in the Play
Mode list. The instrument stays a Beat Slice instrument everywhere else and on disk.
The flag lives in a small checksummed file in the project folder, like the other
patches' settings.

Phase 1 settled the three unknowns about the list (FINDINGS section 27, "The Sample
Playback screen"):
- **The data block's fields:** `+0` selected row, `+2` item count, `+4` visible rows,
  `+0x14` names. Stock already gives 9 visible rows.
- **Nine rows fit with no scroll bar.** A simulated hook at `0x600f7cac` (count 9,
  nine names) draws all nine, y 46..262, inside the control's 29..423.
- **"Beat Slice Thru" fits.** It takes at most 150 px in the firmware's own font
  widths; the list has 178 px for text, and "Pingpong Loop" takes 130.

## 2. What stock firmware does

### 2.1 The play modes

| Value (`ins+0x3c`) | List name (15-byte slots at `0x60042618`) | Note-on (from dispatcher `0x60032abc`) | Render (switch `0x600327fa` on `voice+0x21`) |
|---|---|---|---|
| 0 | 1-Shot | `0x60035310` | `0x600357b4` (forward `0x600353fc`, reverse `0x600355d4`) |
| 1 | Forward Loop | `0x60034888` | `0x60034dc8` |
| 2 | Backward Loop | `0x60034358` (veneer to `0x60034888`) | `0x60034878` |
| 3 | Pingpong Loop | `0x60034dd8` (veneer to `0x60034888`) | `0x60035300` |
| 4 | Slice | `0x600357c4` | `0x600357b4` (as 1-Shot) |
| 5 | Beat Slice | `0x60032b50` | `0x600357b4` (as 1-Shot) |
| 6 | Wavetable | `0x60035904` | `0x60035a2c` |
| 7 | Granular | `0x60032d40` | `0x60034318` (by grain loop type `voice+0x9c`) |

- **Storage.** One byte per instrument at `ins+0x3c`, in the 0x174-byte instrument
  struct. Tracker+ keeps the original Tracker's layout. The struct is saved raw in each
  `Workspace/instruments/instrument_NN.pti`: `0x600c19d8` writes a 16-byte header
  (`u16 0x174` at `+0xc`), the struct, and a CRC at `+0x184`, so the header is 0x188
  bytes and the sample data follows. `0x600c1990` loads the struct back with one
  0x174-byte copy, upgrading files older than version 9 first (`0x600c5a48`). Copying
  an instrument (`0x60113c80`) is also one 0x174-byte copy.
- **The list.** The screen's start method `0x600f57f0` fills 8 name pointers at
  `scr+0xb70`. `0x600f7c54` gives the list its data block at `scr+0xb58`:
  - `+0` = the mode;
  - `+2` = 8;
  - `+4` = 9;
  - `+0x14` = pointer to the names.

  The data reaches the list through the BL to `disp_ctrl_set_data` at `0x600f7cac`.
  The name table has no other reader.
- **Changing the mode.** `0x600f5b98(step)` sets mode + step, clamped to 0..7. It sets
  up Wavetable (sample type `ins+4`, window size), fixes the loop points, marks the
  instrument changed (`0x600bccec`), and redraws the screen through `0x600f7c54`. It has
  five callers, all for the Play Mode list (focus `scr+0xd6 == 6`, no pop-up
  `scr+0x70 == 0`):
  - the encoder callback `0x600c92e0`, with the encoder's step (tail call `0x600c9322`);
  - the +1 callback `0x600c9d84` (`0x600c9dc6`);
  - the −1 callback `0x600ca7a4` (`0x600ca7e6`);
  - screen buttons 7 and 8, `0x600f5e1c`, which call it with −1 (`0x600f5e8a`) or
    +1 (`0x600f5e80`).

  The screen's start method registers the three callbacks from a literal pool at
  `0x600f512c`: −1 at `0x600f5140`, +1 at `0x600f5144`, buttons at `0x600f5164` and the
  encoder at `0x600f5178`. These pool words are their only references.

### 2.2 Beat Slice: slices, editing, note to slice

- **Slices.** `ins+0x108` holds 48 `u16` start points (0..65535 = the whole sample),
  `ins+0x168` the count (0..48) and `ins+0x169` the selected slice.
- **The slice editor** (Slice page; Slice, Adjust, Add and Remove; page 2 Auto, Evenly
  and No of Slices):
  - add after the selected slice, half-way to the next start or to `0xffff`
    (`0x6010f000`);
  - remove the selected slice (`0x6010f094`);
  - adjust, which can't pass the neighbouring slices (`0x6010f100`);
  - auto: clear the table, then transient detection `0x600aff4c` over the sample
    (`0x6010f1b8`);
  - equal slices, i × 65535 / n (`0x6010f1f0`);
  - the confirmation "rewrite the current slices" (`0x600f76e8`).

  They work on the instrument's table only and don't depend on the playback.
- **Note to slice** (`0x60032b50`, emulator): note n plays slice n − 24, so 24 is slice
  1. Notes below 24 play slice 1, and notes past the last slice play the last slice.
  The voice's pitch is fixed at note 60, and the note only picks the slice. The pitch
  code tests `voice+0x21 == 5` at `0x601001bc` for the same reason. The pads in Beat
  Slice map pad → slice note (`0x6010eea8`, given "mode == 5").
- **The Slice FX `S`** (id 25, 0..47) sets `voice+0xae` and the slice in `voice+0xc4`.
  When set, it wins over the note.

### 2.3 How a voice plays a slice

The player for track t is `0x2001a138 + 0x848·t` (tracks 1–8). Its sample voices are
the vector at `player+0x73c`..`+0x740`: one voice, or two for a stereo sample
(`voice+0x28` = channel). `voice_note_on_q` (ITCM `0x5c04`) calls `0x60030d50` per
voice, which starts the amp envelope and calls the dispatcher. When the voice is
already playing, it first fades fast (state 7) and starts the new note after the fade,
in `0x60031068` → dispatcher.

`0x60100b50` (called by every note-on) latches the instrument into the voice:
- the mode into `voice+0x21`;
- the length in frames (`ins+0x2c`) into `voice+0x68`;
- the sample address into `voice+0x24` (the table at `0x20204dc4 + 8·i + 4·channel`).

The Beat Slice note-on then sets:

| Field | Value |
|---|---|
| `voice+0x34` / `+0x38` | start and end, in frames |
| `+0x50` | end − start (the frames to play) |
| `+0x18` | sample address + 2·start |
| `+0x1c` | length − start |
| `+0x7c` | the reverse start (end − start − 1), or 0 |
| `+0x20` | 1 (playing) |

A second (right-channel) voice with `voice+0x118` set copies `+0x34`/`+0x38` from that
partner instead of computing them.

**What stops it:**
- **the end:** the 1-shot render stops at `+0x50` frames and clears `+0x20`;
- **a new note on the same track:** a fast fade, then the new note. Each track plays one
  note at a time, so a play-through tail lasts at most until the track's next note;
- **an OFF step or a gate (`q`) end:** the voice note-off ITCM `0x5678` → `0x54b4` →
  `0x60030f9c` starts the amp envelope's release. With the amp envelope off
  (`voice+0xee`), it stops at once. Read from the code, not run;
- **CUT (−3):** stops at once (`0x60030f9c`, `0x60031040`);
- **FADE (−2):** a timed fade (`0x60030c80`);
- **stop and mute:** as in the other modes.

**Other settings:**
- loop points don't apply to Beat Slice;
- the instrument's End point (`ins+0x44`) is ignored: the last slice ends at `0xffff`;
- Reverse (`voice+0xb0`, the `r` FX) plays the same span backwards;
- Position (`p`, `k`) doesn't apply;
- tune, finetune and the `ins+0x173` offset still set the pitch (`0x60100b50`).

## 3. The options

| | (a) New mode value 8 | (b) Project setting | (c) List entry + flag (recommended) |
|---|---|---|---|
| What the user sees | A ninth list entry | Config → Project Settings → "Beat Slice: play through" On/Off, for every Beat Slice instrument in the project | A ninth list entry, "Beat Slice Thru", per instrument |
| Stored mode | 8 in `ins+0x3c` and the `.pti` | 5 | 5 |
| Where the choice lives | The instrument | `<project>/slicethru.bin` (1 bit) | `<project>/slicethru.bin` (1 bit per instrument) |
| Code sites | Every "mode == 5" and "mode 4..5" test (31 in 26 functions), the tests on `voice+0x21` (7, `0x601001bc` included), the 8-entry name table, the 0..7 clamp, the dispatcher's and the render's jump tables, every `cmp #7` bound. Most are mid-function compares: not hookable | 2 voice hooks, 2 Config menu sites (chained, as six patches do), the file | 2 voice hooks, 1 list-data hook, 5 input hooks, 2 screen-update hooks (file) |
| Stock firmware with the project | Breaks (below) | Plays Beat Slice | Plays Beat Slice |
| Risk | Not buildable | Lowest | Low; the list hooks are new ground (phase 1 checks them) |

**Option (a) is out.** It would need about 40 code changes, most of them single
compare instructions that can't take two values. Stock firmware with a mode-8
instrument (emulator plus static reading):
- the dispatcher returns 0 without setting the voice up (emulator: `0x60032abc` with
  mode 8 returns 0), so the voice keeps the previous note's setup or stays silent;
- the render switch is silent for values above 7;
- the change handler clamps the value to 7;
- the list would look for a ninth name pointer at `scr+0xb90`, which isn't one. That
  last point is not run in the emulator, and it could fault in the text drawing.

The `.pti` files would carry the 8 into other projects and into any future official
firmware.

**Option (b) is the fallback** if phase 1 finds that the list can't take a ninth entry
cleanly. Same voice hooks, a well-trodden Config path, but one switch for the whole
project and not where the user looks for play modes.

**Option (c)** keeps the instrument a Beat Slice instrument (mode 5) everywhere, so
the slicing, the auto slice, the pads and the pattern code are stock. It only changes
the list (show nine entries, map the selection) and the slice end. A spare byte in the
instrument struct would let the flag travel with `.pti` files, but Tracker+ already
uses bytes the original Tracker left free (`ins+0x173` is a pitch offset, `ins+0x102`
is read as a `u16` by `0x60100b50`). A future official firmware could claim any other
one, so the flag stays out of the instrument.

## 4. Design (option c)

Patch `patch/slicethru`, symbols `mt_bt_*`, version text `1.2.0-bt1`.

### 4.1 The list

The list order: 1-Shot, Forward Loop, Backward Loop, Pingpong Loop, Slice, Beat Slice,
**Beat Slice Thru**, Wavetable, Granular. Virtual index V:

| V | 0–5 | 6 | 7 | 8 |
|---|---|---|---|---|
| mode, flag | V, off | 5, on | 6, off | 7, off |

The new entry sits next to Beat Slice, so moving between the two never passes
through Wavetable. Each step through Wavetable would run stock's Wavetable setup on
the instrument.

**A step** (encoder ±n, +1, −1, buttons 7/8):
1. V' = clamp(V + step, 0, 8).
2. Map V' to (mode', flag'), as in the table above.
3. Store flag' for the instrument.
4. Call the stock handler `0x600f5b98(mode' − mode)`.

Beat Slice ↔ Beat Slice Thru is a step of 0. Stock then re-applies Beat Slice (loop
fix-up, changed mark, redraw), as it does on every change to Beat Slice. The redraw
ends in `0x600f7c54`, where the list-data hook shows V'.

**Name:** "Beat Slice Thru" (15 characters; the longest stock name has 13). Phase 1
measured it at 150 px at most, against 178 px of room.

**What phase 1 showed about the stock list:**
- **Steps** (emulator): the encoder's step is clamped to ±4. Button index 6 (the
  7th button) steps −1 and index 7 (the 8th) steps +1. A button press with the list
  not focused only focuses it.
- **Selection:** stock always jumps straight to the mode's row, with no animation.
  The handler's `disp_ctrl_set_value(list, mode + ins+4)` is overridden by the
  set-data right after it.
- **A step of 0** on a Beat Slice instrument leaves the instrument byte-identical, so
  the Beat Slice ↔ Thru toggle through the stock handler changes nothing but the
  patch's flag.

### 4.2 Hooks

All chained (`"next"`), so a later patch on the same site works.

| Hook | Site (old bytes) | Kind | What |
|---|---|---|---|
| `mt_bt_bs_note_on` | `0x60032B3A` `bl 0x60032b50` (`00f009f8`), the dispatcher's Beat Slice case | branch | `prev = cur; cur = flag(i) && ins(i).mode == 5; next(voice, i, note); cur = prev` (i clamped to 47 as stock does) |
| `mt_bt_end_frames` | `0x60032C62` `bl 0x60100694` (`cdf017fd`), the Beat Slice end point | branch | if `cur`: point = `0xffff`; `return next(point, len, addr)` |
| `mt_bt_list_data` | `0x600F7CAC` `bl disp_ctrl_set_data` (`0af0a4f8`), the Play Mode list's data | branch | for the screen's instrument: selection = V, count = 9, names = the patch's 9-entry table; then `next` |
| `mt_bt_encoder` | word `0x600F5178` (`0x600c92e1`) | word | list focused, no pop-up: step(encoder step); else `next` |
| `mt_bt_plus` | word `0x600F5144` (`0x600c9d85`) | word | the same, step +1 |
| `mt_bt_minus` | word `0x600F5140` (`0x600ca7a5`) | word | the same, step −1 |
| `mt_bt_button_plus` / `_minus` | `0x600F5E80` / `0x600F5E8A` `bl 0x600f5b98` (`fff78afe` / `fff785fe`) | branch | step(+1) / step(−1) in place of the stock call; the stock code after it runs as before |
| `mt_bt_playback_update` | word `0x60042698`, the Sample Playback screen vtable's update (`0x600f6071`) | word | file tick |
| `mt_bt_pattern_update` | word `0x600403DC`, the Pattern screen update (`0x600e1f99`, shared with 8 patches) | word | file tick |
| version | word `0x600CBF9C` | version | `1.2.0-bt1` |

**Why two voice hooks rather than one.** One hook after `0x60032b50` would have to
recompute four fields. It would also run after stock has unmasked the audio interrupt,
so a block could render the short slice first. With the flag set before the call and
read inside it, stock computes everything from the new end while the interrupt is
masked.

**Re-entry.** A note-on from the main loop (pad preview, `0x60100ed4`) can be cut into
by the audio interrupt before stock masks it. That is why `cur` is saved and restored:
the interrupt's note-on sets and restores its own value.

**The stereo partner.** The right voice copies the left voice's start and end (already
extended) and never reaches `0x60032c62`.

**The input hooks** test exactly what stock tests before it calls the handler
(`scr+0x70 == 0`, `scr+0xd6 == 6`). In every other state they only call `next`.

### 4.3 State and file

RAM (`.mt_ram`, 32 bytes as built):
- magic;
- project session (the FNV-1a hash of name and path, as swing);
- the flags as a 48-bit mask in a `u64`, and the saved copy;
- `cur`;
- save-delay counter, unnamed and valid bytes;
- the 9 name pointers: a const table in flash. The pointers to stock strings are
  absolute; ours is relocated.

`slicethru.bin` in the project folder, 16 bytes:

| Offset | Size | Content |
|---|---|---|
| 0 | 4 | magic `BTS1` |
| 4 | 8 | mask, little-endian: bit i = instrument i + 1 is Beat Slice Thru; bits 48–63 zero |
| 12 | 4 | FNV-1a of bytes 4..11 |

- **Load:** when the session changes (project opened, named), from a screen update.
  A missing file or a bad magic, checksum or high bits means no Thru instruments.
- **Save:** a moment after a change (swing's delay), one read or write of 16 bytes per
  call, only from the two update methods. A new unnamed project keeps its flags until
  its first save, as swing does.
- **Stale flags:** a flag counts only while its instrument's mode is 5. The file tick
  clears a flag whose instrument isn't Beat Slice (another sample loaded into the slot,
  the mode changed elsewhere). So the only stale case left is a slot replaced by
  another Beat Slice instrument: the list then shows Beat Slice Thru, visibly.
- **Stock firmware** never reads the file: the project plays plain Beat Slice. Patched
  firmware reading a project saved by stock sees no file or the old one. If the user
  set slices in stock meanwhile, the flags still apply only to instruments that are
  Beat Slice.

### 4.4 What the user gets

- Choose "Beat Slice Thru" in the Play Mode list. Slicing, auto slice, Evenly, Add,
  Remove, Adjust and the pads are all Beat Slice's.
- A note (or a pad, or the `S` FX) starts at its slice and plays to the end of the
  sample, until the track's next note, an OFF, CUT or FADE step, a gate end, or stop.
- Reverse plays from the sample end back to the slice start.
- **Limits:**
  - a `.pti` saved from the Sample Playback screen and loaded elsewhere is plain Beat
    Slice;
  - copying the instrument doesn't copy the Thru choice;
  - Save As under a new name counts as another project, as for swing.bin and the
    other settings files. So the choice carries over only if the firmware copies the
    project's extra files. Not checked in the emulator; part of the hardware check.

## 5. Interactions with the other patches

| Patch | Shared sites | Effect |
|---|---|---|
| colorsong | none | Its colours follow pattern content (instrument numbers in steps). This patch changes no pattern or instrument data |
| copypattern | Pattern update `0x600403DC` (chained) | none; copies patterns only |
| synths | Pattern update (chained). Its list-draw hook (`0x60106606`, inside the list draw `0x6010652c`) only runs for lists with image ids; it marks only its own rows | Synth instruments (64–66) never reach the sample dispatcher. The bundle test renders the Play Mode list with synths in |
| legato | none | It reorders MIDI and synth note-offs (instruments 48–66) in ITCM; sample voices don't go there |
| humanize, interpolate, swing, masterfx, condtrig, visualizer | Pattern update (chained; masterfx, swing, synths, visualizer, copypattern, humanize, interpolate, condtrig already chain it) | none found. The visualizer's pad writes: Beat Slice pad lights are stock code |
| Pattern FX | — | `S` picks the start slice (tested in the emulator, section 2.2); `r` reverses (tested); `p`/`k` don't apply, as in stock |

## 6. Budget

- **Flash:** 1,813 bytes as built in phase 4. For comparison, swing is 1.7 KB and
  condtrig 1.0 KB in patch/all.
- **RAM:** 32 bytes. In the local patch/all + slicethru bundle it lands in `sdram_top`.
  `ocram_tail` is full in patch/all; `sdram_top` has about
  25 KB left and `dtcm_after_audio` 832 bytes. The voice hook reads it once per
  note-on, so SDRAM is fine.
- **CPU per voice:**
  - per rendered block, unchanged: 4,916 instructions with and without play-through
    (emulator, measured);
  - per Beat Slice note-on, 54 instructions more (310 against stock's 256, measured with
    phase 3's code);
  - per screen update, the file tick: tens of instructions, plus a 16-byte SD access a
    moment after a change.

  Voices live longer, but the worst case doesn't grow. It is still one voice (two for
  stereo) per sample track, which a 1-Shot sample can already keep busy to its end.

## 7. Risks and open points

| # | Risk | How it is handled |
|---|---|---|
| 1 | The list's data fields, the handler's `set_value`, the update slot | Retired (phase 1, emulator): fields as in section 1; `set_value` is overridden by the set-data that follows; word `0x60042698` is the screen's periodic update (`0x600f6071`) and runs in the rig |
| 2 | Nine rows may not fit, or the name may be too wide | Retired (phase 1): nine rows drawn, no scroll bar; "Beat Slice Thru" at most 150 of 178 px |
| 3 | An input path that changes the mode and isn't hooked | `0x600f5b98` is the only UI writer of `ins+0x3c`, and all 5 of its callers are covered. Other writers are loads and copies (whole struct). Phase 3 tests every path |
| 4 | Interrupt timing in the voice hooks | Flag set before, read inside the masked region, saved and restored; a re-entry test |
| 5 | Stereo samples | The partner path copies the extended end; a test with a two-voice player |
| 6 | Stale flag after a slot is replaced by another Beat Slice instrument | Visible in the list; documented. Optional later: clear on the load and copy paths (`0x600c1990` callers, `0x60113c80`) |
| 7 | The settings file and Save As | As the other patches; checked and documented in phase 4 |
| 8 | Release-tier time | An 11th patch doubles the web patcher's mixes (1,023 → 2,047). The last release run took 35.7 minutes, 20.6 of them the web harness, so expect about 55 minutes (estimate). Over 45 minutes: ask first |
| 9 | Another patch is mid-way | synths has uncommitted changes in the tree. One patch at a time: nothing here starts until that is committed and the user says go |

## 8. Build phases

Each phase ends with its emulator checks passing; the user decides when the next one
starts.

1. **Screen rig, stock only (no patch code). Done.**
   - The rig is built on `synth_rig.SynthRig`. It sets the globals the synth screen
     tests set, plus a fake for the text list's clock (`0x600aa860` → `+0x14` →
     `+0x30`), and stubs `0x600f5508` (pad lights).
   - It runs create-controls and start on a Beat Slice instrument and draws the Play
     Mode list. It drives the encoder, +1, −1 and button 7/8 callbacks, recording
     mode, list fields and the drawn row each time. Then it simulates the list-data
     hook.
   - Each scratch script runs in 1.8–1.9 s, setup included.
   - The facts are in FINDINGS section 27. The rig moves into
     `tools/emu/test_slicethru.py` with phase 3.
2. **Voice hooks. Done.** `patch/slicethru` (version text `1.2.0-bt1`) has
   `mt_bt_bs_note_on` (`0x60032B3A`) and `mt_bt_end_frames` (`0x60032C62`), both
   chained.
   - **Size:** 169 bytes of code and 16 bytes of RAM (magic, the 48 flags, `cur`).
   - **Test:** `tools/emu/test_slicethru.py <patched> <stock>` sets the flags in the
     patch's RAM and checks 2.1–2.6 plus the sites, the version, stray writes and the
     cost. It runs in 5.3 s and passes on the patch alone.
   - **Bundle:** it also passes on a test bundle of patch/all plus slicethru (built
     locally, not committed), where the RAM lands in the last 16 bytes of
     `dtcm_above_heap`.
   - **Re-entry (2.6):** checked by starting a note-on with `cur` already set, as an
     interrupted outer note-on leaves it. Unicorn can't start a second emulation from
     inside a hook, so a real nested call isn't run.
3. **List and input hooks. Done.** `mt_bt_list_data` (`0x600F7CAC`), the encoder, +1
   and −1 callback words (`0x600F5178`, `0x600F5144`, `0x600F5140`) and the two button
   steps (`0x600F5E80`, `0x600F5E8A`), all chained.
   - **Version and size:** `1.2.0-bt2`, 849 bytes of code, the same 16 bytes of RAM.
     The nine names are a const table in flash (eight stock string addresses and the
     patch's own).
   - **Rig:** the screen rig from phase 1 is now part of
     `tools/emu/test_slicethru.py`.
   - **Results:** checks 3.1–3.3 and the list-to-voice check pass, on the patch alone
     and on the local patch/all + slicethru bundle. The whole test runs in 10.9 s (11.2
     s on the bundle).
   - **Rig lesson:** the off-the-list comparison runs on a fresh pair of rigs. A rig
     the input checks had used carries screen state (the selected slice's position)
     that stock's rig never had.
   - **Left for phase 4:** the second half of 3.4, clearing a stale flag at the file
     tick.
4. **Settings file. Done.** `mt_bt_playback_update` (Sample Playback update, word
   `0x60042698`) and `mt_bt_pattern_update` (Pattern update, word `0x600403DC`, now
   shared with nine patches), both chained.
   - **Version and size:** `1.2.0-bt3`, 1,813 bytes of code, 32 bytes of RAM.
   - **File handling:**
     - The file is read and written only while the file manager is idle (the stock
       test at `0x600bc990`, as copypattern uses).
     - On a project change the flags are cleared at once, then read from the file once
       the file manager is idle.
     - A load while the Sample Playback screen shows a sample instrument redraws the
       list (`0x600f7c54`).
     - A flag whose instrument isn't an active Beat Slice instrument is cleared, but
       only while the file manager is idle, so never in the middle of a project load.
   - **Results:** checks 4.1–4.4 and the stale-flag checks pass. The whole test (65
     checks) runs in 12.0 s, and in 12.5 s on the local patch/all + slicethru bundle.
     On that bundle the colorsong, copypattern, humanize, visualizer, swing,
     interpolate and condtrig tests also pass. legato, masterfx and synths wait for
     `run_tests.py` in phase 5.
5. **On its own. Done.**
   - **Docs:** `patch/slicethru/README.md`, `prebuilt/` (the patcher's output from it
     is byte-identical to the local build's, SHA-256 `76e0a004…2cb2e`), and the root
     README and PATCHING entries.
   - **Layout test:** `tools/test_layout.py` builds slicethru alone and with patch/all's
     patches in both orders. It passes (49.8 s), and `test_slicethru.py` passes on both
     bundles (15 s each).
   - **Quick tier:** `run_tests.py --tier quick` passes: 21 jobs, 0 failures, 5.8 min,
     nothing from the cache.
   - **What the runner doesn't cover yet:** it tests the mods in the web patcher's
     manifest, so slicethru's own test isn't in it until phase 6 adds it there. Until
     then it runs on its own.
   - **Next:** the user's hardware check (section 10).
6. **Bundle, after the hardware check and the user's go-ahead.** `patch/all`, the web
   patcher manifest, `run_tests.py --tier release` (ask first, section 7 #8), the
   hardware bundle check.

## 9. Emulator test plan (`tools/emu/test_slicethru.py <patched.ptf> <stock.ptf>`)

Times: "measured" means the scratch scripts this plan is based on (the voice
experiment ran in 1.7 s and the phase-1 screen scripts in 1.8–1.9 s, setup included). The others are estimates from comparable
existing tests, to be replaced by the runner's measured times once the test exists.

| # | Check | Time |
|---|---|---|
| 2.1 | Flag off: for modes 0–7 and notes 0–127, every note-on leaves the voice object byte-identical to stock | the whole phase-2 test runs in 5.3 s (measured) |
| 2.2 | Flag on: notes 24..24+n−1 start at slice k and end at `0xffff` (frames = length − start); notes below 24 and past the last slice as stock; 1 and 48 slices; no slices | about 1 s (measured, part of the 1.7 s run) |
| 2.3 | Render to the end: frames played = length − start; per-block instructions equal to stock | about 1 s (measured: 4,916 instructions per block in both) |
| 2.4 | `S` FX, reverse (start position and direction), tune and finetune unchanged | under 1 s (measured for `S` and reverse) |
| 2.5 | Stereo instrument: both voices get the same extended end | about 1 s (estimate) |
| 2.6 | Re-entry: an interrupt note-on (a different instrument) injected at stock's `audio_irq_disable` inside an outer note-on; both voices end where they should | about 1 s (estimate) |
| 3.1 | The list shows nine names; selection = V for each (mode, flag); rows drawn as the phase-1 rig found them | about 2 s (measured: the phase-1 list script, setup included, 1.8 s) |
| 3.2 | Every input path (encoder ±1..±4, +1, −1, buttons 7/8): mode and flag after each step, clamping at 0 and 8, Beat Slice ↔ Thru without Wavetable's setup running | about 2 s (measured: the phase-1 input script, 13 steps, 1.9 s) |
| 3.3 | Off the list (other focus, pop-up open): the four callbacks behave as stock (same calls, same state) | part of the 10.9 s run (measured) |
| 3.4 | Changing the mode away from Thru clears the flag; mode set by loading another instrument clears it at the next tick | about 2 s (estimate) |
| 4.1 | `slicethru.bin` written a moment after a change, 16 bytes, right checksum; nothing written without a change | part of the 12.0 s run (measured) |
| 4.2 | Loaded on a project change; a bad magic, checksum or high bits, or no file: no Thru instruments | part of the 12.0 s run (measured) |
| 4.3 | Unnamed project: flags kept until the first save, then written | part of the 12.0 s run (measured) |
| 4.4 | Stock firmware with the same project: the instruments play Beat Slice (note-on as stock) | part of the 12.0 s run (measured) |
| 5.1 | Version text `1.2.0-bt1` (`check_version.py`) | 1 s (comparable) |
| 6.1 | In patch/all: the Play Mode list renders the same with synths' list hook in; every check above passes | the patch's own run, about 30 s (estimate) |

Through `tools/run_tests.py`:
- **`--tier quick`** while working: layout, each patch alone, patch/all. About 2
  minutes today (documented), plus this test's ~30 s per firmware.
- **`--tier release`** before the bundle is committed: adds the web harness and the
  cover set. About 55 minutes estimated for eleven patches (35.7 minutes measured for
  ten). The runner prints its own estimate; over 45 minutes, I ask before running.
- **`--tier deep`:** not needed. This patch doesn't touch masterfx's or synths' audio
  code.

## 10. Hardware check (the user, after phase 5; about 15 minutes)

1. Install `TrackerPlus_bt3.ptf` (`1.2.0-bt3`, SHA-256 `76e0a004…2cb2e`, the
   PATCHING table) through Config → Firmware. Boot; Config shows `1.2.0-bt3`.
2. A drum loop instrument: Beat Slice, Auto slice. Pads play slices as before.
3. The Play Mode list shows nine entries, Beat Slice Thru between Beat Slice and
   Wavetable, all readable, no scroll bar. Choose Beat Slice Thru: the pads now play
   from each slice to the end of the loop.
4. A pattern with notes on steps 1 and 9 only: each plays on until the next note.
   Then an OFF step, a `q` gate, `S` and `r` on a step.
5. Switch Beat Slice ↔ Beat Slice Thru with the encoder, the arrows and buttons 7/8.
   Switch to Wavetable and back.
6. Save the project, open another, reopen it: still Beat Slice Thru. Power off and on,
   open it again: still Beat Slice Thru.
7. Save As under a new name and open the copy: note whether it is Beat Slice Thru or
   Beat Slice (the one point the emulator can't answer).
8. Revert: reinstall stock 1.2.0. The project opens and the instrument plays Beat
   Slice.
