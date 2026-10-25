# Live Resampling: plan (firmware 1.2.0)

Keep the internal engine (sample and synth instruments) audible while the Sample
Recorder is recording, so the user can play the pattern and record over it. The engine
comes on during a recording only when:

- Play is pressed after the recording has started, or
- the recording is started with **Shift+Rec**. That runs the count-in (if Pre Roll is
  on) and starts the pattern as the recording starts.

Otherwise the recorder behaves exactly as stock.

All addresses are firmware 1.2.0. `E` = the audio engine object `0x20017b08`; `seq` =
the sequencer `0x20017274`; `scr` = the Sample Recorder screen object `0x20026100`.
The facts are in FINDINGS section 28.

Status: phases 1 (rig and measurements), 2 (`rs1`, the buffer move), 3 (`rs2`, Play
after Rec) and 4 (`rs3`, Shift+Rec) are done in the emulator, and phase 5 (on its own:
README, `prebuilt/`, docs, layout test) is done (section 8). The user's hardware check
of `1.2.0-rs3` passed except a plain Rec during a Shift+Rec count-in, fixed in `rs4`
(section 9.1); the user's recheck of `rs4` passed. Next: phase 6 on the user's go.
Nothing is committed. The user has
answered the questions (section 10).

## 1. Verdict

**The user is half right, and the patch is feasible: 6 small hooks, nothing touching
the codec, DMA or the audio graph.** Five hooks are in flash. The sixth is one jump word
in the ITCM image, loaded at boot (the kind swing already uses); it has no ITCM stub and
changes no ITCM code.

1. **It isn't a mute.** The output chain, the master mixer and the codec keep running
   all through a recording, and the line-in Monitor goes through the same master bus.
   What stock does is **drop the note-ons**:
   - Entering the recorder calls `0x600b39cc`. That sets 48 per-instrument "blocked"
     flags (`0x2001e3a8`..`d7`), and the sample note-on (ITCM `0x5c04`) plays nothing
     for a blocked instrument.
   - It also sets `seq+0x7dd` = 1 (`0x600ad600`). `seq_trigger_note` (ITCM `0x149c`)
     returns at once for the three synth instruments (64..66) while it is set (test at
     ITCM `0x14b0`).
   - **MIDI instruments (48..63) take another branch** of `seq_trigger_note`, which has
     no such test. That is why MIDI still plays.

   The manual documents this: the unit can record audio or play a pattern's audio, not
   both at once. Delay and reverb are also bypassed, and their sends zeroed, for as
   long as the recorder is open.
2. **The real reason is memory.** Stock records into `0x8026a000` (`0x600f9074`, buffer
   size `0x3c00000`). That is the sample bank's base, where the project's samples are
   loaded. **A recording writes over the loaded samples.** Playing a sample instrument
   during a recording would play the recording as it is written, so stock blocks them.
   Before the engine can be turned on, the recording has to go into the bank's free
   space instead, above the samples already loaded. One BL hook at the record start
   does that, because every reader of the recording finds it through the recorder
   object (`0x6010ca68`), not the fixed address.
3. **Turning the engine on is two stock calls** (emulator, section 6):
   - `0x600b39e4(E)` clears the blocked flags;
   - `0x600ad600(seq, 0)` clears `seq+0x7dd`.

   With only these, a sample and a synth note sound at the master during a recording,
   MIDI is still sent, and the recording's input queue still holds only the input.

   **Making it sound as in normal playback takes more** (phase 1):
   - Undo the entry's other changes with the stock leave path's own calls: delay
     bypass, the mix levels (the recorder sets all eleven to 0 dB) and the limiter.
   - Mark the sends to be re-applied.
   - Stop the engine's per-block update from returning early during a recording.
     While recording, `engine_block_update` (ITCM `0x41c8`, in the audio interrupt)
     copies the recording and then returns at once. That skips envelopes, LFO FX,
     automation and the re-applying of the sends. The "recording?" question goes
     through one ITCM jump word, so the sixth hook answers "no" there while the engine
     is on. The copy runs before that question, so the recording is unaffected.

   With all of it, the output in the emulator is bit-identical to normal playback.
4. **What goes into the recording is unchanged: the selected input only.** The engine
   is heard, not recorded. To record the engine you would need a cable from the output
   back to the input, or a new **Resample** source. Stock already has master-output
   record queues (`E+0x1e58`/`E+0x2248`, used by render/export), and in the emulator
   they carry the engine during a recording. That source is an extra, not part of the
   request (section 4.5, question 1).
5. **The rule for when the engine comes on fits hooks.** Rec and Play are button
   handler pointers stored in literal pools (one word each, no other references,
   called once per press).
   Both record-start paths (immediate, and after the count-in) go through one function,
   `0x600f9074`, whose queue-start call is a BL that can be hooked. The recorder's
   update is a vtable word, so a hook there can tell when the recording stops.

## 2. What stock firmware does

### 2.1 The recorder screen

- `scr` = `0x20026100` (pointer at `0x200006b0`), vtable `0x60042c4c`:
  - update `0x600fafe8` (vtable word `0x60042c54`);
  - start `0x600fa228`;
  - leave `0x600f8b74`.
- Fields:
  - `+0x76` mode: 0 record setup, 1 crop/edit, 2 name/save;
  - `+0x78` source: 0 Line In, 1 Radio, 2 Mic LG, 3 Mic HG, 4 USB (q);
  - `+0x7e` Monitor (0 = on);
  - `+0xb4d` recording or counting in (`seq_tick` reads it);
  - `+0xbf8`/`+0xbf9` count-in done/pending;
  - `+0xbba` the "stop the pattern?" pop-up.
- **Entering** (`0x600fa228`):
  - If the pattern is playing, it asks "This action will stop the pattern" and stops it.
  - Mode: it sets `seq+0x34` = 4 and the count-in callback `seq+0x6fc` = `0x600f8605`.
  - It mutes the codec volume for 500 ms, then restores the headphone level.
  - Engine calls:

    | Call | What it does |
    |---|---|
    | `0x600b39cc(E)` | block the 48 sample instruments |
    | `0x600ad600(seq, 1)` | `seq+0x7dd` = 1: drop synth note-ons |
    | `0x600b2dd8` | delay bypass on |
    | `0x600b4058`, `0x600b40c0` | delay/reverb sends to 0 |
    | `0x600b3a88` | save the 11 mix levels (8 track volumes, reverb, delay, dry mix) and set them to 0 dB (`0x56`) |
    | `0x600b3440` | mute the line-in passthrough (bus input 3, line-in delay/reverb sends) |
    | `0x600b36dc(E, 0)` | limiter side-chain mode and selectors to 0 |
    | `0x600b30ec(E, level)` | bus input-3 (line-in) gain: `0x56` with Monitor on, else 0 |

- **Leaving** (`0x600f8b74`) undoes all of it, stopping the pattern first if it plays.
  It clears the blocked flags (`0x600b39e4`) and `seq+0x7dd`.

### 2.2 Rec, the count-in and Play

- **Rec** (button `0x1c`): the handler `0x600d0620`, bound by `0x600f8e3c` from the
  pool word **`0x600f9000`**. In mode 0, or in mode 1 while recording, it calls
  `0x600fb3cc`. In mode 0:
  - **Pre Roll on and the source isn't Radio:** it sets `+0xbf8` = 0x100 and `+0xb4d` =
    1, and starts the count-in `0x600ac2c0` ("Counting in").
  - **Otherwise:** `0x600f9074`, which records at once.

  **There is no Shift test, so stock Shift+Rec does the same as Rec.**
- **The count-in** runs in `seq_tick` (ITCM `0x35e8`, in the audio interrupt) on the
  metronome. At its end it calls `seq+0x6fc` (`0x600f8605`, sets `+0xbf8`). It doesn't
  start the pattern; the Pattern screen's count-in (`+0x6f8`) is the one that calls
  `seq_play_from`. The recorder's update sees `+0xbf9` and `+0xbf8` and calls
  `0x600f9074` (BL at `0x600fb1fa`), from the main loop.
- **Play** (button `0x17`): the handler `0x600d07b4`, bound by the recorder's start
  from the pool word **`0x600fa4ac`**. It is the generic one:
  - playing: stop (`0x600acd90`);
  - stopped and Shift (`0x1d`) held: continue (`0x600acd38`);
  - otherwise: play the pattern (`0x600ac5f8`).

  So Play during a recording already starts the pattern. Only MIDI is heard.

### 2.3 Recording

`0x600f9074`:

1. sets `+0xb4d` = 1 and rebinds the buttons (`0x600f8e3c`);
2. calls `0x6010cb90(rec, buf, size, ch, ch, flag)` at **`0x600f90d2`**, where:
   - `rec` = the recorder `0x20027f28`;
   - buf = `0x8026a000` and size = `0x3c00000` (literal and `mov.w` at `0x600f909e`);
   - ch = 1 for the mics, 2 for the others;
   - flag = source ≠ USB;
3. starts the record queues with `0x6010c890(rec)` at **`0x600f90d8`**;
4. reads the two channel pointers back with `0x6010ca68(rec, ch)`.

- **Copying:** the copy loop `0x6010c5a8` moves blocks from the queues into SDRAM in
  the audio interrupt. `engine_block_update` (ITCM `0x41c8`) calls it first, then
  returns at once while `rec+0x2c` (recording) is set (section 1, point 3). Nothing goes
  to the SD card while recording.
- **Stop:** `0x6010c944` stops the queues, copies what they still hold, then clears
  `rec+0x2c`.
- **Sources:** `0x6010c890` fetches the queues:
  - sources 0..3: the input queues `E+0x108`/`E+0x150`, accessor `0x60111884`, BLs at
    `0x6010c916`/`0x6010c926`;
  - USB: `E+0x1e8`/`E+0x230`, accessor `0x60111890`.

  **No stock source records the internal output.** The master queues
  `E+0x1e58`/`E+0x2248` exist, for render/export.
- **The sample bank:**
  - base `0x8026a000` (`*0x200006ac`);
  - size `0x3c00000` at `0x20204f44`;
  - bytes used at `0x20204f48`.

  Instruments hold their sample address at `ins+0x28`. Of the recorder's code, only
  `0x600f9074` uses the base address; every other reader goes through the recorder
  object.

## 3. The options

| | What | Cost | Verdict |
|---|---|---|---|
| a | Only open the gates (no buffer move) | 2 calls | **No:** samples would play the recording as it overwrites them, and the recording would hold them |
| b | Move the recording above the loaded samples always, open the gates by the rule | 6 hooks | **Recommended** |
| c | Move the buffer only for Shift+Rec | 6 hooks | Play after a plain Rec couldn't turn the engine on, which the user asked for |
| d | b plus a Resample source (records the master) | b + 2 hooks + a list change | Extra; ask first |

The cost of (b): the longest recording becomes the free part of the bank, not the
whole bank. With no samples loaded nothing changes (about 713 s mono). With 30 MB
loaded it is about 370 s mono, or 185 s stereo. If less than a minimum is free (say 10
s), the patch leaves the buffer where stock puts it and the engine stays off for that
recording: the stock behaviour. This is a behaviour change for every recording; the
user has accepted it (question 2).

## 4. Design (option b)

### 4.1 Hooks

All are chained (`next`) even though no other patch uses these sites today.

| # | Site | Kind | Hook | Does |
|---|---|---|---|---|
| 1 | `0x600F9000` | word (Rec handler) | `mt_rs_rec_button` | On a Rec press in mode 0, not recording: arm "start the pattern" if Shift (`0x1d`) is held, else disarm. Then the stock handler |
| 2 | `0x600FA4AC` | word (Play handler) | `mt_rs_play_button` | Recording, buffer moved, pattern stopped: open the gates, then the stock handler (the pattern starts in the next tick, so the gates must be open first or the downbeat is lost). Otherwise just the stock handler |
| 3 | `0x600F90D2` | BL → `0x6010cb90` | `mt_rs_rec_buffer` | Replace buf/size with the bank's free space (from `0x20204f48`, rounded up to 32 bytes, size rounded down to whole frames). Fall back to stock below the minimum |
| 4 | `0x600F90D8` | BL → `0x6010c890` | `mt_rs_rec_started` | Chain (the queues start), then if armed and the buffer moved: open the gates and play the pattern (`0x600ac5f8(seq)`). One site covers both start paths |
| 5 | `0x60042C54` | vtable word (recorder update) | `mt_rs_update` | Chain, then: if the patch opened the gates and the recording has stopped (`rec+0x2c` == 0), stop the pattern and close the gates, so the crop/save screens see stock state. Disarm when nothing is counting in or recording in the setup (`scr+0xb4d` clear, or another mode) |
| 6 | `0x601ACC28` | word in the ITCM image (thunk `0x1f540`'s literal, loaded to ITCM `0x1f544` at boot) | `mt_rs_is_recording` | In the audio interrupt: return 0 while the patch has the gates open, else chain to the stock `0x6010c888` (`rec+0x2c`). `engine_block_update` then runs in full during the recording. Its only caller is ITCM `0x41d8` |

- **"Open the gates"** puts the master back to how it sounds in normal playback (the
  user's answer to question 4), with the stock leave path's own calls. In order:
  1. `0x600b39e4(E)` and `0x600ad600(seq, 0)`: sample and synth note-ons back;
  2. `0x600b2de4(E)`: delay bypass off;
  3. `0x600b3ae4(E)`: the eleven mix levels back (8 track volumes, reverb, delay,
     dry mix; the recorder sets them all to 0 dB);
  4. `0x600b4128(E)`: the limiter and its side-chain from the project;
  5. **mark the sends** for re-applying, with the audio interrupt masked
     (`0x600303d4`/`0x600303c8`), since the interrupt changes the same words. That is
     `player+0x798 |= 0x100100` for the 8 players and `E+0x1a48 + 4·k |= 0x6` for the
     3 synths. The stock per-block code then recomputes every send from the
     instrument, the step FX and the mutes;
  6. set "opened", so hook 6 lets the per-block update run.

  The line-in passthrough and the Monitor level stay as the recorder set them, so the
  input is heard only as Monitor says.
- **"Close the gates"** (the recording has stopped):
  1. stop the pattern (`seq_stop`, question 3), so no note is held;
  2. clear "opened";
  3. re-apply the recorder's entry state with the entry's own calls, in its order:
     `0x600b39cc(E)`, `0x600ad600(seq, 1)`, `0x600b2dd8(E)`, `0x600b4058(E)`,
     `0x600b40c0(E)`, `0x600b3a88(E)`, `0x600b36dc(E, 0)`. `0x600b4058`/`40c0` clear
     the delay and reverb, so their tails stop with the recording, as in the stock
     recorder. Notes still sounding at the stop play their release on the dry path,
     as after a normal pattern stop (phase 3 measured the same tail as stock's stop).
- **Leaving the recorder** needs nothing: stock's leave clears the same gates and stops
  the pattern. A stale "opened" flag is harmless. The next update in a later recorder
  visit sees it with no recording, closes gates that are already closed and clears it.
- **Play during the count-in** (recording not started yet) acts as Shift+Rec: it arms,
  and the pattern starts with the recording (question 5). The stock Play handler isn't
  called then. Stock would start the pattern silently over the count-in.
- **Not covered:** a pattern started by external MIDI start/clock during a recording.
  That doesn't pass the Play handler, so the engine stays off: the stock behaviour.

### 4.2 Timing of the Shift+Rec start

The recording starts in the main loop, when the update sees the count-in finished.
Hook 4 starts the pattern right after the queues start. The sequencer advances once per
audio block, in the same interrupt as the record copy (FINDINGS section 12,
`app_audio_block`). So the first step plays in the first block the recording gets,
and **the recording and the pattern are aligned**.

Only the gap between the count-in's last beat and the pattern's first step varies: one
main-loop pass (until the recorder's update runs) plus at most one block (2.9 ms). The
emulator has no real main loop, so phase 1 can't give the main-loop part. The hardware
check judges it by ear (section 9, step 4). If it is audible, the alternative is to
start the pattern from the count-in itself, in the interrupt (`seq+0x6f8`). The
recording would then start that main-loop pass after the downbeat, which is worse for
resampling. So this design stays unless the hardware says otherwise.

### 4.3 State

16 bytes in `.mt_ram`, default region order, no `ram_startup`:

| Field | Use |
|---|---|
| magic | `.mt_ram` is garbage at boot |
| armed | Shift+Rec (or Play in the count-in) seen |
| moved | this recording's buffer was moved |
| opened | the patch opened the gates |

No file, no setting and nothing on disk.

### 4.4 What the user gets

- **Plain Rec, then Play:** the pattern plays with all its instruments while the input
  records, as heard through the headphones/outputs.
- **Shift+Rec:** count-in (if Pre Roll), then the recording and the pattern start
  together.
- **The sound:** the master sounds as in normal playback, delay and reverb included,
  plus the input if Monitor is on.
- **Stop the recording:** the pattern stops and the crop screen works as stock.
- **Everything else is stock,** including Rec without Shift and without Play, Monitor,
  and the sources.

### 4.5 Extra (not requested): a Resample source

This records the master output (everything heard, the metronome and a monitored input
included) instead of an input.

- **Hook:** the two accessor BLs at `0x6010c916`/`0x6010c926`, returning the master
  queues when the source is Resample.
- **UI:** it needs a sixth entry in the source list, which hasn't been traced. That is
  its own RE step, like the Beat Slice Thru list.
- **With Monitor on and a line-in source,** the input is in the master too.
- **The user's answer:** last. Only once everything else works, and only if they
  then want it (phase 7).

## 5. Interactions with the other patches

- **No shared sites.** None of the current patches hooks `0x600f9000`, `0x600fa4ac`,
  `0x600f90d2`, `0x600f90d8`, `0x60042c54` or `0x601acc28` (checked against every
  `patch.json`). Chaining keeps it safe if one does later.
- **masterfx and the visualizer** read the master output and are unaffected. With the
  gates open they see the engine as in normal playback.
- **synths and legato** act inside the synth note-on, after the `seq+0x7dd` test, so
  they work as in normal playback once the gate is open.
- **Joining the bundle:** after its hardware check it joins `patch/all` (listed last)
  and the web patcher. It would be bit 11 in `web/manifest.json`, with a new
  `patch/all` version text. Hook 6 is an ordinary `words` entry in the ITCM image's
  flash copy, as swing's `0x601acf00` is. There are no `itcm_branches`, so
  `web/patcher.js` needs no change.

## 6. Evidence so far (emulator)

The rig is scratch work: the whole audio graph from `synth_rig`, a 1-shot sample
instrument, a synth (64) and a MIDI instrument (48), played through `seq_trigger_note`.
The recorder entry is replayed with the stock calls of section 2.1.

| Case | Synth peak at the master | Sample voice | MIDI note-ons |
|---|---|---|---|
| Stock, recorder not open | 21406 | 7998 | sent (USB and DIN) |
| Stock recorder entry, Monitor on or off | 0 | 0 (the note-on returns 0) | sent |
| Entry + the two gate calls | 17866 (delay/reverb still bypassed) | 7998 | sent |

- **Recording:** with a test input of 1000 fed to the codec input object, the input
  record queue holds 1000 in every case. With Monitor on and the gates open, the master
  carries the engine plus the input (32765, clipped), and the master queues carry it
  too.
- **Block pool (before phase 1):** the first rig had no stems sink, so the stems mixer
  held 28 blocks and its numbers weren't usable. Phase 1 below replaces them.

**Phase 1** (scratch scripts, stock firmware, the open/close steps of section 4.1 done
by hand). The rig now:
- runs `engine_block_update` before every graph update, as the device does;
- starts a real recording (`0x6010cb90`, `0x6010c890`);
- has a stems sink object at `0x20026ec8`, as after boot.

Results:

- **Sound.** Two sample tracks and two synths, sends on, 120 blocks (0.35 s). The
  input was silent and Monitor off. An engine-on recording with every step of "open
  the gates" gives output **bit-identical** to normal playback of the same notes with
  the recorder closed. The recording holds the input. (Phase 3 found that the rig's
  sample instruments had bit depth 0, so the sample tracks were silent at the master
  and this covered the synths only. Phase 3 repeats it with real instrument defaults.)
  - Without hook 6, the reverb is missing (differs by up to 291), because the synth
    sends are never re-applied.
  - Without the mix-level restore, the levels differ (up to 701).
  - Without the limiter restore, nothing changed in the rig; it is kept because the
    stock leave does it.
  - With the input not silent, the only difference is the line-in's own reverb send,
    which the recorder mutes (by 1 LSB here). That is Monitor's job, so it is correct.
- **Delay:** its return stayed silent in the rig, in normal playback too. So only the
  bypass flag and the send setter calls are checked, not the delayed sound. It is in
  the hardware check.
- **Cost and pool** (8 sample tracks and 3 synths, 24 blocks):

  | Case | Instructions per block | Pool peak (of 80) | Allocation failures |
  |---|---|---|---|
  | Normal playback | 539,000 | 74 | 30 |
  | Stock recording (engine silent) | 451,000 | 76 | 30 |
  | Engine-on recording, hook 6 simulated | 532,000 | 76 | 30 |

  The recording itself costs little: the copy runs every block and holds at most 2
  blocks. Engine-on recording is within 2% of normal playback, and peaks where stock
  recording does. The failures are the same in every case, normal playback included,
  so the rig's pool is still tighter than the device's. Only the comparison counts.
- **Buttons:** both handlers are bound "press only" (flag 1; the dispatch at
  `0x600cf7c0` calls them only on that state). `0x1d` is Shift.
- **Stop:** `0x6010c944` clears `rec+0x2c` once the last blocks are copied (callers:
  the recorder at `0x600faf74`, and `0x60110760`). Hook 5 watches that flag.

## 7. Risks and open points

| Risk | Touches | Plan |
|---|---|---|
| Block pool: engine plus record queues at once | audio blocks (no code change to the graph) | Phase 1: peak as stock recording, +2 over normal playback, no new failures. A failed allocation drops a block (a click), not a crash. The hardware check listens for clicks |
| CPU: engine plus recording copy, both in the audio interrupt | audio interrupt | Phase 1: within 2% of normal playback. Phase 3, on `rs2`: +0.7%; the patch's own share 14 instructions per block |
| Hook 6 runs in the audio interrupt | ITCM jump word | A few instructions: read the patch's flag (with the magic check), else chain. The flag is only read after boot, so any RAM region works. The emulator runs it through the real ITCM thunk |
| The full per-block update while recording | audio | It is what normal playback runs; the only new part is that it runs alongside the copy. Phase 3 checks the recording stays bit-exact to the input |
| Delay not heard in the rig | audio | Hardware check step 3 |
| Something assumes the recording is at `0x8026a000` | memory | Only `0x600f9074` uses it; the rest goes through `0x6010ca68`. Phase 2 also runs crop and Save & Load in the emulator as far as the rig allows, and the hardware check saves a recording |
| How stock gets the samples back after overwriting them | memory | Not traced (a reload job, `0x600bc894` → `0x60115298`, is the candidate). With the buffer moved, nothing is overwritten, so a reload is only wasted time. The hardware check plays the project's samples after a recording |
| Feedback: output cabled to the input with Monitor on | audio level | Phase 3: with Monitor off no input reaches the master (an input of ±1000 changes no output sample), so a cable can't loop. With Monitor on the input reaches the master at 1.0, as in the stock recorder, so the loop's gain is the cable path's own (output level × input gain): at or above 1 it builds up. Note it in the README |
| Count-in to pattern offset | timing | One main-loop pass + ≤ 2.9 ms; recording and pattern stay aligned (section 4.2). Judged in the hardware check |
| Held synth notes when the gates close | audio | Stop the pattern first (stock stop sends note-offs). Phase 3: the releases end as after a normal stop, then silence |

**Codec, DMA, ITCM:** none of the steps writes the codec or the DMA buffers. The ITCM
image changes in one word only (hook 6, the target of an existing jump), as swing's
does; no ITCM code changes and no ITCM stub is added. The patch calls stock engine
functions that the recorder's own entry and leave already call. The steps that change
what the engine does during a recording are the gate opening and hook 6 (phase 3). The
step that changes memory use is the buffer move (phase 2).

## 8. Build phases

Each phase ends with its emulator checks passing; the user decides when the next one
starts.

1. **Rig and measurements, stock only. Done** (section 6, FINDINGS section 28).
   - **Restores:** found the stock restore for each piece, and the eleven mix levels.
   - **New finding:** the per-block engine update returns early while recording, which
     needs hook 6.
   - **Checked:** the handlers are press-only, `0x1d` is Shift, and the stop flag is
     `rec+0x2c`.
   - **Sound:** with every step done by hand, the output was bit-identical to normal
     playback.
   - **Measured:** pool and CPU.
   - **Where the rig lives:** it stays in the session's scratch folder for now. Adding a
     file to `tools/emu/` invalidates every cached test result, and other sessions are
     working there. It moves into `tools/emu/test_resample.py` with phase 2, as
     slicethru's did.
   - **Not done in the emulator:** the delay's sound (silent in the rig) and the
     main-loop part of the count-in gap. Both are in the hardware check.
2. **Buffer move (hook 3).** `patch/resample`, `1.2.0-rs1`. **Done.**
   - **Size:** 181 bytes of code, 16 bytes of RAM (magic, a "moved" flag, the last
     buffer and size).
   - **What it does:** the buffer starts at base + used, rounded up to 32 bytes; its size
     is the rest of the bank, rounded down to 256 bytes. If less than 10 s at the
     recording's channel count would fit, or the call isn't stock's (other buffer, other
     bank size, not 1 or 2 channels), the call goes to stock unchanged.
   - **Tests** (`tools/emu/test_resample.py <patched.ptf> <stock.ptf>`, 0 failures,
     about 30 s alone):
     - the site and the version text;
     - placement in 15 cases (empty bank = stock's buffer exactly, mono and stereo,
       unaligned used, the 10 s limit on both sides of the 256-byte rounding, a full
       bank, an unexpected bank size), read back through the recorder's own channel and
       length accessors;
     - garbage RAM at boot;
     - a 40-block recording leaves 256 KB of loaded samples byte-identical and records
       the input exactly; stock, run the same way, writes over them;
     - no write outside its RAM and the stack, and the hook's own cost (a few dozen
       instructions per record start).
   - **Not run in the emulator:** crop and Save & Load of a moved recording. They read
     the recording through the same accessors (FINDINGS section 28), so they should
     follow the move; the hardware check saves one.
   - **Bundle** (local only: `patch/all`'s includes plus resample, placed last; RAM in
     the last 16 bytes of `dtcm_above_heap`): every patch's test passes except synths
     and phase (`--quick`), with one failure each: a stray write from synths' code to
     `0x20034100`. The same failure shows on `patch/all` as it is now, without
     resample, so it comes from the uncommitted synths/PHASE work, not from `rs1`.
3. **Play after Rec (hooks 2, 5 and 6).** `rs2`. **Done** (emulator).
   - **Size:** 705 bytes of code, 16 bytes of RAM (an "opened" byte joins "moved").
   - **What it does:**
     - Play during a moved recording, pattern stopped: open the gates (section 4.1),
       then the stock handler.
     - The recorder's update, once the recording has stopped: stop the pattern, close
       the gates.
     - Hook 6 answers 0 only while the gates are open and the recorder's buffer is the
       one the patch moved. The factory self-test's recordings (`0x601106f0`, own
       buffers, FINDINGS section 28) always get stock's answer.
     - "opened" is cleared at each record start.
   - **Tests** (`test_resample.py` sections 6–10, 0 failures, about 2.5 minutes):
     - **Sound:** two sample tracks, two synths and a MIDI track, sends and mix levels
       away from their defaults.
       - The output is bit-identical to stock firmware given the same steps by hand
         (the entry, the five open calls, Play). MIDI is the same.
       - Against normal playback with the recorder closed (30 audio blocks before Play
         in both): max difference 27 of a 32268 peak (−61 dB) for about 20 blocks,
         then bit-identical. The cause: the firmware recomputes the sends in the first
         engine block after Play, not before it. Stock's own leave does the same; it
         just never shows, because blocks run before the user presses Play.
       - The gates are open before the stock handler starts the pattern.
     - **Input** (the feedback question): with Monitor off, an input of ±1000 changes
       no output sample, and the recording holds exactly the input while the pattern
       plays. With Monitor on, the input reaches the master as in the stock recorder
       (1.0).
     - **Stop:** the update stops the pattern (MIDI note-off sent) and closes the gates.
       No note starts afterwards. The releases end as after a normal pattern stop, then
       silence. After that, every byte of state the recorder's entry writes is as in
       stock after an entry made right after the same pattern stopped. The delay
       buffer's leftovers (the bypassed delay keeps writing its state, in stock too)
       are no larger than stock's (30 vs 33).
     - **Stock where not asked for:** Play with no recording; Play during a recording
       that couldn't be moved; a recording without Play (Monitor on, master as stock's).
     - **Cost:** +0.7% instructions per block over normal playback. The patch's own
       share is 14 instructions per block. Nothing writes outside its RAM, the stack
       and the send flags.
   - **Rig fix:** the rig's sample instruments now come from the firmware's own
     new-instrument defaults (`0x600c1b50`). Before that, their bit depth was 0 and the
     sample tracks were silent at the master in every case, so phase 1's "bit-identical"
     covered the synths only.
   - **Bundle** (local only: the committed `patch/all` (MS8) includes plus resample,
     placed last, `1.2.0-MRS`; RAM in `dtcm_above_heap`): all 13 patch tests pass
     (masterfx, synths and phase `--quick`), about 5 minutes. Phase 2's synths/phase
     failure is gone now that the PHASE work is committed.
   - **Not done in the emulator:** crop and Save & Load after an engine-on recording;
     the delay's sound (hardware check).
4. **Shift+Rec (hooks 1 and 4).** `rs3`. **Done** (emulator).
   - **Size:** 1073 bytes of code, 16 bytes of RAM (an "armed" byte joins the others).
   - **What it does:**
     - **Rec** (record setup, not recording): arms if Shift is held, then the stock
       handler. If the press started nothing (no count-in, no recording: a pop-up, say),
       it disarms again. Without a count-in the stock handler records at once, and hook
       4 runs inside it.
     - **Record queues started** (`0x600f90d8`, reached from Rec and from the count-in's
       end alike): if armed and the buffer was moved, open the gates and play the
       pattern. Not moved: disarm and stay stock.
     - **Play during the count-in** arms, and the stock handler isn't called.
     - **The update** disarms when nothing is counting in or recording.
     - **Not in the plan (my choice, flagged):** if the pattern is already playing
       silently when Shift+Rec starts the recording (Play pressed in the setup, which
       stock allows), it is stopped and played again from its start, so the recording
       and the pattern still start together.
   - **Tests** (`test_resample.py` section 11; the whole test 0
     failures, about 3.5 minutes; driven through the real Rec handler):
     - plain Rec: records, nothing armed, no pattern, the master silent;
     - Shift+Rec with Pre Roll off, and with Radio (Pre Roll on but no count-in): the
       recording starts with the pattern. Output and MIDI are bit-identical to Rec then
       Play at once, which section 6 checks against stock;
     - Shift+Rec with Pre Roll on: the count-in runs (577 sequencer ticks in the rig)
       armed, with the pattern stopped, no note, and the update keeping the arm. At its
       end the recording and the pattern start together. The first note-on comes 12
       ticks (one micro-step) after the record start, as after any Play. Output as
       above;
     - Play during the count-in: armed, the stock handler not called, the pattern
       starts at the record start, output as above;
     - no pattern when the buffer can't be moved (bank full), after a count-in that
       ended without a recording, with garbage RAM, or with a stale arm;
     - a silently playing pattern starts again from its start (first note-on 12 ticks
       after the record start);
     - stock: Shift+Rec is plain Rec;
     - no write outside its RAM, the stack and the send flags; 277 of the patch's own
       instructions for a whole Shift+Rec with a count-in.
   - **Bundle** (local only, as in phase 3: `patch/all` MS8 plus resample, `1.2.0-MRS`):
     all 13 patch tests pass, about 5 minutes.
   - **Not done in the emulator:** the main-loop part of the gap between the count-in's
     last beat and the record start (section 4.2). The hardware check judges it.
5. **On its own.** README, `prebuilt/`, the PATCHING entry, `tools/test_layout.py`, and
   `run_tests.py --tier quick`. Then the user's hardware check (section 9).
   **Done** (uncommitted):
   - `patch/resample` is rs3, with `prebuilt/` and a README. `tools/emu/test_resample.py`
     is the test from phases 2–4.
   - The patcher's output is the same from the local build, from `prebuilt/` and from
     the phase 4 build: `TrackerPlus_rs3.ptf`, SHA-256 `71ad672f…`.
   - The root README and `docs/PATCHING.md` list resample as emulator-tested: not yet
     tested on hardware, and not yet in `patch/all` or the web patcher. `run_tests.py`
     covers the web patcher's mods only, so until phase 6 resample's test runs on its
     own.
   - `tools/test_layout.py` builds resample alone, and after and before patch/all's
     patches. It passes. Its RAM lands in `ocram_tail` alone or first, and in
     `dtcm_above_heap` last.
   - FINDINGS section 28 has the phase 3–4 facts; there are 4 new names.
   - `run_tests.py --tier quick`: 25 jobs, 0 failed (6.7 min). `test_resample.py` on
     rs3 and on the two layout bundles (patch/all's patches before and after it): 0
     failures each.
   - After the hardware check: `rs4` (section 9.1), 1089 B code, `TrackerPlus_rs4.ptf`
     SHA-256 `5cf21721…`. `test_resample.py` 0 failures (4.1 min; on rs3 it fails the
     two new "Shift+Rec, then plain Rec" checks only), layout 0 failures, `run_tests.py
     --tier quick` 25 jobs, 0 failed (5.9 min).
6. **Bundle.**
   - Join `patch/all`, with a new version text.
   - Add it to the web patcher (bit 11), with its tested builds.
   - Run the release tier with `--update`.
7. **Resample source (extra, only if the user asks once 1–6 work).** Trace the
   recorder's source list, add the entry, hook the two queue accessor BLs (section 4.5).

## 9. Hardware check (the user, after phase 5; about 15 minutes)

Use a project with a few sample instruments, a synth track and a MIDI track; Line In
with nothing plugged in; headphones at a low level.

1. Install `rs3` and check Config shows `1.2.0-rs3`.
2. **Plain Rec, no Play:** it records as before, the pattern is silent and the
   recording plays back.
3. **Plain Rec, then Play:**
   - you hear samples and synths at the same mix levels as in normal playback, with
     their delay and reverb (set a track's delay send high to hear the delay);
   - MIDI still goes out;
   - stopping the recording stops the pattern.
4. **Shift+Rec with Pre Roll on:**
   - count-in, then the pattern and the recording start together;
   - note how far the first beat is from the count-in.
5. Save & Load the recording. Then play the project's other sample instruments: they
   must sound right, not like the recording.
6. **Leave the recorder:** delay and reverb are back and the pattern plays normally.
7. Listen for clicks or dropouts during steps 3 and 4. If there are any, stop and
   report.

### 9.1 Results (`rs3`, 2026-10-25)

- Steps 1–7 pass: plain Rec is stock; Play after Rec plays samples, synths and MIDI at
  the mix levels with delay and reverb; stopping the recording stops the pattern;
  Shift+Rec with and without Pre Roll, and Play during the count-in, start the pattern
  with the recording; the loaded samples sound right after Save & Load; leaving the
  recorder plays normally. No clicks or dropouts.
- Also as planned: a second Play during a recording stops the pattern and the
  recording goes on; the recorder can't be left during a recording; Shift+Rec over a
  silently playing pattern restarts it from step 1 (the flagged extra, working as described).
- **Fixed in `rs4`:** a plain Rec during a Shift+Rec count-in disarmed it (the count-in
  ran on and the recording started, without the pattern). Stock has no way to cancel
  the count-in and lets a second Rec pass, so `rs4` keeps the arm there; Shift+Rec
  during a plain count-in arms, as Play does. Emulator: stock and the three
  combinations checked in `test_resample.py` section 11. Hardware recheck of `rs4`
  (Pre Roll on): Shift+Rec then Rec, and Rec then Shift+Rec, start the pattern with the
  recording; Rec twice records silently.
- **Start gap:** in a Shift+Rec take (Pre Roll on, about 65 BPM going by the kick
  spacing), the first sound is 12.15 ms into the recording, a sharp kick onset on step 1
  with no FX, at the same level as the later kicks (no ramp). The sequencer's first note
  comes one micro-step after Play (4.8 ms at 65 BPM, stock), plus up to one block
  (2.9 ms). What else is in the rest depends on how the master reached the recorder's
  input (an analog loop adds the output and input buffers and the converters; the
  recording's −84 dBFS noise floor, not digital zero, points to an analog path). Asked
  the user.

## 10. Decisions (the user's answers)

1. **What is recorded:** the selected input. The Resample source is saved for last
   (phase 7), to consider once everything else works.
2. **Shorter recordings:** accepted. The longest recording is the free sample memory.
3. **Stopping the recording stops the pattern:** yes.
4. **Effects:** anything that would normally play on the master plays, delay and
   reverb included (section 4.1).
5. **Play during the count-in acts like Shift+Rec:** yes.
