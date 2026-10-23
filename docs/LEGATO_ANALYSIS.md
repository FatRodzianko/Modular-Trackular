# Legato for MIDI instruments: investigation

Status: analysis done; option B1 is chosen and planned in `docs/LEGATO_PLAN.md`.
Nothing is built. Firmware 1.2.0. FINDINGS section 24 has the
firmware facts this relies on. Instruments are numbered as the pattern stores them:
MIDI instruments are 48–63 (channels 1–16; the manual calls them 51–66).

## 1. Verdict

**Feasible, and small.** The stock sequencer sends the old note's note-off just before
the new note's note-on, in the same audio block, from two separate call sites in the
sequencer's ITCM code. A patch can skip the first call and make it after the second,
so the receiving synth gets "new note on, then old note off". A mono synth reads that
as legato. Two ITCM BL hooks do it; the patcher already supports them (`itcm_branches`,
as `patch/condtrig` uses). The deferred note-off is sent in the same
`seq_process_track` call, so no new stuck-note risk appears.

An emulator scratch run with hooks in Python (section 5) shows the reordering working in
every case tried, with no deferred note-off left over.

**Recommended:** a Glide FX (`G`) on a MIDI-instrument note means "play this note
legato". Stock firmware ignores `G` on MIDI notes, so such patterns play as before on
stock firmware, and no new FX, range or text changes are needed. A per-project
"all MIDI notes legato" setting can follow later as an option (section 7).

## 2. Stock behaviour (emulator, stock 1.2.0)

The real sequencer (`seq_tick`, ITCM `0x35e8`) was run with fake MIDI ports, as in
`tools/emu/seq_timing.py`, with the MIDI octave setting at 2 (no transpose). Bracketed
lines are the calls that produced each message.

**You are right.** On one track, a note followed by another note sends the old note's
note-off first, then the new note-on, at the same tick:

```
## adjacent steps, same track (60 then 62 then 64)
                                          [seq_trigger_note t=0 from 0x2fdc]
  tick    12  step 0 micro  1  DIN note on 60 127 ch1
                                          [seq_note_off_q t=0 from 0x24de]
  tick   588  step 1 micro  1  DIN note off 60 0 ch1
                                          [seq_trigger_note t=0 from 0x2fdc]
  tick   588  step 1 micro  1  DIN note on 62 127 ch1
```

All cases:

| Case | What stock sends |
|---|---|
| Adjacent steps, same track | Off old, then on new, same tick (above) |
| Same pitch repeated | Off 60, then on 60, same tick |
| Note, empty steps, note | The old note is **held through the empty steps**; off old + on new at the next note |
| Two tracks, same channel | **No note-off across tracks.** t0 60, t1 62: 62 goes on while 60 is held (your workaround). 60's off comes only when track 0 plays its next note |
| Two tracks, same pitch, same step | Two note-ons for 60, later only one note-off (stock quirk) |
| Channel changes on the track | Off to the old channel, on to the new one |
| Micro-move (m 50) on the new note | Off old + on new together, both at micro-step 25: the old note is held until the delayed note |
| Instrument MIDI delay | Same: both delayed together |
| Gate `q` 50 | Off at micro-step 24 (gate end); at the next note **another** off for the same note, then the on. `q` 100: off at micro-step 48, same duplicate |
| Gate `q` 101–255 | Exactly like `q` 100 (the gate is capped at 47 micro-steps) |
| Note OFF / CUT / FADE (note values −2/−3/−4) | Off at that step's micro-step 1; nothing more at the next note |
| Chance fails on the new note | No off and no on: the old note keeps sounding until the next note that plays |
| Roll | Retriggers send their own offs (gate path) |
| Chord FX | Offs for every chord note, then the next on |
| Pattern change (song mode) | Same as adjacent steps, at the new pattern's first note |
| Stop (`seq_stop`) | One off per track for its last note |
| Track muted while a note is held | Off at the track's next micro-step 1; its notes are then skipped |
| Glide `G` (any value 0–255) on a MIDI note | **Ignored**: identical to no FX |
| MIDI out USB / DIN / both | Identical order. With both: USB then DIN for each message |
| Volume `V` 50 | Velocity 63 (V × 127 / 100) |

Notes:
- MIDI notes have no length of their own: a note sounds until the track's next note,
  an OFF step, a gate end, a mute or stop. So a tie is already possible: leave the
  next step empty.
- The off and on go out in the same interrupt, back to back. On DIN that is 6 bytes,
  about 2 ms on the wire. Legato only needs the order reversed, not new timing.
- Not checked on hardware; the emulator runs the real sequencer code with fake ports.

## 3. What legato needs

A mono synth plays legato (no envelope retrigger, portamento in "legato" or "fingered"
mode) when a note-on arrives **while another note is held**. So the patch should send
**the new note-on, then the old note-off**, not drop the note-off. Dropping it would
leave the old note held on a polyphonic receiver, and a mono synth with last-note
priority would fall back to it when the new note ends.

The rule, for a step whose note will play on a MIDI-instrument track:
1. The new note has the legato marker (e.g. `G`), and the old note (the track's last
   triggered note) is a MIDI instrument too.
2. Skip the stock note-off of the old note; keep a copy of the old record (7 bytes).
3. Let the stock code trigger the new note.
4. Right after, send the old note's note-off through the stock `seq_note_off_q`.

Edge cases:

| Case | Behaviour | Why it's safe |
|---|---|---|
| Same pitch and channel | Stock order (off, on): a retrigger | "On 60, off 60" would end the new note. For a tie, leave the step empty |
| Different channel | On new (ch B), then off old (ch A) | Harmless; nothing to hold across channels |
| Old note had a gate (`q`) | Its note-off already went out; stock's duplicate off is sent after the new on | The note was already released, so no legato, as on a real keyboard |
| Chance or condtrig fails on the legato step | Nothing happens (stock skips this path) | No copy is made, so nothing can be left over |
| Micro-move or MIDI delay on the legato step | Both messages move together to the delayed time | Same path, same call |
| Stop | Stock sends off for each track's last note (the new one) | The old note's off already went out in the same audio block |
| Pattern change | The first note of the next pattern can carry the marker | Same path as adjacent steps |
| Mute, solo, OFF / CUT / FADE steps | Stock | Different call sites, not touched |
| Another track on the same channel | Stock: no cross-track offs | Not touched |
| Chord or Arp on either note | Stock order | A chord sends several offs; reordering could end shared pitches |
| Random note `n` on the legato step | Compare pitches after the random note is chosen, at the trigger hook | The pre-pass changes the note between the two sites |
| Synth instruments (64–66), samples | Stock | Synths have their own Glide mode with a Legato option |

**Leftover protection.** The copy lives only between the two hooks, inside one
`seq_process_track` call in the audio interrupt. In the scratch run nothing was ever left
over (section 5). As insurance, a build would also flush any copy at the start of the
first hook and after each `seq_advance` (the chained site `0x600aa7f8` that swing,
interpolate, visualizer and condtrig already share), so a copy can never outlive one
audio block (about 2.9 ms).

## 4. Where to hook

All ITCM addresses; the code is copied at boot from flash `0x6018d6e4` + address.

| Site | What | Hook |
|---|---|---|
| `0x24da` `bl seq_note_off_q` | Note-off of the track's last note before a new note plays. Runs when the track has triggered (`+0x7f`), the work note is ≥ 0 and the new or the old instrument is MIDI/synth (48–66). Args: r0 = seq, r1 = track, r2 = last record (`seq + 0x68·t + 0x68`), r3 = −4. Afterwards stock clears `+0x7f`, `+0x80` and the roll and arp state | `itcm_branches`, no `args` (the stub then keeps the caller's r0–r3). Legato: keep a copy of the record and return; else call through the chain word |
| `0x2fd8` `bl seq_trigger_note` | Plays the step's note (args r0 = seq, r1 = track, r2 = work record). Also the delayed-note (Micro-move) path. Afterwards stock sets `+0x7f` and `+0x80` and copies the work record to the last record | `itcm_branches`, no `args`. Call through the chain word, then send the deferred off |
| `0x11a4` `seq_note_off_q(seq, t, rec, mode)` | Stock note-off for a record: MIDI/synth through the veneer `0x1f608`, chords through `0x600ac508`, sample voices (t < 8) through `0x5678` | Called by the patch for the deferred off |

Why these and not the MIDI send functions:
- The MIDI senders are flash functions, `seq_note_on_out_q` (`0x600abb44`) and
  `seq_note_off_out_q` (`0x600abbe8`), `(note, velocity, channel, synth)`. ITCM reaches
  them through veneers whose target words are in flash: `0x601ace70` (veneer ITCM
  `0x1f788`, note-on) and `0x601accf0` (veneer `0x1f608`, note-off). A plain `words`
  hook could redirect them, but there they carry no track, and every note-off (gate,
  OFF step, roll, stop, mute) passes through the same word. The two ITCM sites above
  carry the track and run only for "old note before a new note".
- Other note-off callers that stay stock: the gate/roll/mute path `0x20ca`, OFF/CUT/FADE
  `0x296e`, `0x33f2` and `0x1242` (all tracks), and in flash `seq_stop` → 
  `seq_track_note_off_q` (`0x600ac5a8`) and the functions around `0x600ac960` and
  `0x600ad638`.

No other patch hooks `0x24da`, `0x2fd8` or the two veneer words. Each new ITCM site
costs one stub of 8 bytes in the ITCM tail (1,216 bytes; condtrig uses two sites).

## 5. Scratch proof (emulator, Python hooks)

Unicorn hooks stood in for the two BL hooks: at `0x24da`, for a new note with `G` in
FX1 or FX2, skip the call and keep a copy; at the return from `seq_trigger_note`
(`0x2fdc`, where r0–r3 are dead) call the real `seq_note_off_q` with the copy. A copy
still pending after a tick would print `ORPHAN`; none did.

```
## legato: 60, 62 G, 64 G, 65 plain
  tick    12  step 0 micro  1  DIN note on 60 127 ch1
  tick   588  step 1 micro  1  DIN note on 62 127 ch1
  tick   588  step 1 micro  1  DIN note off 60 0 ch1
  tick  1164  step 2 micro  1  DIN note on 64 127 ch1
  tick  1164  step 2 micro  1  DIN note off 62 0 ch1
  tick  1740  step 3 micro  1  DIN note off 64 0 ch1
  tick  1740  step 3 micro  1  DIN note on 65 127 ch1

## legato with Micro-move m 50
  tick    12  step 0 micro  1  DIN note on 60 127 ch1
  tick   876  step 1 micro 25  DIN note on 62 127 ch1
  tick   876  step 1 micro 25  DIN note off 60 0 ch1
  tick  1164  step 2 micro  1  DIN note off 62 0 ch1
  tick  1164  step 2 micro  1  DIN note on 64 127 ch1

## legato across a pattern change in song mode (4-step patterns)
  tick    12  step 0 micro  1  DIN note on 60 127 ch1
  tick  1740  step 3 micro  1  DIN note off 60 0 ch1
  tick  1740  step 3 micro  1  DIN note on 62 127 ch1
  tick  2316  step 4 micro  1  DIN note on 64 127 ch1
  tick  2316  step 4 micro  1  DIN note off 62 0 ch1

## legato then stop
  tick    12  step 0 micro  1  DIN note on 60 127 ch1
  tick   588  step 1 micro  1  DIN note on 62 127 ch1
  tick   588  step 1 micro  1  DIN note off 60 0 ch1
  tick  1152  step 1 micro 48  -- seq_stop --
  tick  1152  step 1 micro 48  DIN note off 62 0 ch1
```

Also run, all as expected: same pitch (stock order), Chance C 0 on the legato step
(nothing sent, nothing pending), Roll, Random note, after a gated note, into a chord
(stock order), after an empty step and an OFF step, channel change, and two tracks on
one channel. One finding: the same-pitch check must be made at the trigger hook,
because Random note changes the note after `0x24da`.

## 6. The three options

### A. A new FX type ("L Legato", id 43)

- **Free id:** yes, 43 and up (types 1–42 are used).
- **UI work:** the biggest part. The FX list (43-byte table `0x60039ca4`) and the name
  table must be copied and extended, with 17 + 3 references repointed and the loops
  bounded at 43 raised (FINDINGS 18). Range (0..100 by default) and text (plain number)
  work without changes.
- **Stock firmware with such a pattern:** plays as if the FX weren't there (no legato),
  keeps the bytes when saving, but the grid shows the cell as `----`, like an empty
  one, so it can be overwritten unknowingly.
- **Conflicts:** takes one of the two FX slots (see B1). No playback conflict with the
  other patches. Any other future new FX would share the list patch.
- **Risk:** medium. Many UI sites; the FX picker's behaviour with a list longer than 43
  isn't mapped.
- **Size:** large: the two playback hooks plus the list/name work (a few hundred lines,
  mostly table plumbing).

### B. An existing FX with a new meaning

**B1. Glide `G` (type 19) on a MIDI note = legato. Recommended.**
- **Stock:** `G` on a MIDI note does nothing; all values 0–255 send exactly what a note
  without FX sends (section 2). So the meaning is free, and the name fits: on synths
  and samples Glide already means "from the previous note to this one".
- **UI:** none. `G` is in the FX list, shows `G 50` etc., and the pads, Fill and random
  fill work as before. The value can be anything (it could later carry a portamento
  time, sent as a CC, but that's not needed for legato).
- **Stock firmware with such a pattern:** plays normally (off, then on) and shows the
  `G`, so the intent stays visible.
- **Conflicts:**
  - humanize: `G` uses an FX slot, so humanize can add only one of Micro-move and
    Volume on a legato step (it already skips a step with no free slot). Micro-move
    and Volume themselves work with legato.
  - condtrig, Chance: a failed step sends nothing, legato or not.
  - swing, interpolate, visualizer, synths, masterfx, colorsong: no shared sites except
    the optional insurance hook on the chained `0x600aa7f8`.
  - Projects that already have `G` on MIDI notes (it did nothing before) would start
    playing legato.
- **Risk:** low. Two ITCM BL hooks (the patcher feature is hardware-tested), no UI, no
  file format.
- **Size:** small: about 100 lines of C, a few hundred bytes of code, about 16 bytes of
  RAM. condtrig is 317 lines with its UI parts.
- **Confirmed on hardware:** stock firmware lets you put `G` on a MIDI step, and plays
  it with no legato and no glide.

**B2. Gate `q` above 100 = "hold into the next note".**
- Marks the old note instead of the new one ("tie into the next note").
- **Stock:** `q` 101–255 acts as `q` 100, so on stock the note is cut at the step end,
  the opposite of legato.
- **UI:** values above 100 need the encoder hook, like condtrig's `C`.
- More work and a worse stock fallback than B1.

**Others ruled out:** Chance above 100 belongs to condtrig. Micro-move above 100 delays
notes on stock (and drops the next one). Volume `V` above 100 and CC values above 127
change what stock sends.

### C. A setting (per project, per channel)

- **UX:** Config → Project Settings → "MIDI legato": Off / On (or one chosen channel,
  or all). Every MIDI note on those channels follows the rule in section 3. A detached
  note is made with a Gate `q` on the note before it, which releases it before the next
  note on (an existing FX, and it reads naturally).
- **Stock firmware:** ignores the settings file; the project plays as stock.
- **Conflicts:** none in playback, and no FX slot used. The Config menu sites are shared
  and chained by humanize, interpolate, masterfx, swing and synths (needs one more
  settings file, e.g. `legato.bin`, and a little RAM).
- **Limits:** no per-note control. A whole channel set to legato also slurs notes you
  meant to re-articulate, unless you add gates. Per channel needs a 16-way choice in
  a list that has room for one value; per instrument is the same thing (one MIDI
  instrument per channel), and the MIDI instrument screen isn't mapped.
- **Risk:** low to medium (settings UI and SD I/O, a pattern the other patches follow).
- **Size:** medium: the playback hooks plus the settings boilerplate (swing is 428
  lines).

### Summary

| | A. New FX | B1. `G` on MIDI | B2. `q` > 100 | C. Setting |
|---|---|---|---|---|
| Per-note control | yes | yes | yes | no |
| UI changes | FX list, names | none | encoder range | Config item, file |
| Stock plays it as | normal, cell looks empty | normal, `G` visible | cut at step end | normal |
| FX slot used | yes | yes | yes (on the old note) | no |
| Risk | medium | low | low–medium | low–medium |
| Size | large | small | small–medium | medium |

## 7. Recommendation

1. **Build B1 first: `G` on a MIDI note = legato.** It's the smallest change, needs no
   UI, degrades cleanly on stock firmware, and the hooks are proven in the emulator.
   Same pitch stays a retrigger (use an empty step for a tie); chords, arps, synths and
   samples stay stock.
2. **Optionally add C later:** a per-project "MIDI legato" setting (Off / one channel /
   all) that treats every MIDI note on those channels as if it had `G`. It costs the
   settings boilerplate but uses no FX slot, which matters with humanize. It shares the
   playback hooks with B1, so it can be added without touching them.
3. Skip A and B2: more work for no gain over B1.

Before planning: check on hardware (stock firmware) that `G` can be put on a MIDI
step, and that your synth does legato when a note-on arrives just before the old
note's note-off (the two-track workaround already shows this).
