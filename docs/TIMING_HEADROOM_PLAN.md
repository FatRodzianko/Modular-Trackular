# Audio timing headroom: plan (firmware 1.2.0)

**Goal.** Measure on the Tracker+ how much of each audio block's time the firmware
uses, with and without the patches. That gives a real headroom number to replace the
emulator's instruction-count margins, such as legato's `PER_TRACK_LIMIT = 150` in
`tools/emu/test_legato.py`.

Status:
- Phase 1 (static and emulator checks) is done. Its results are in section 11 and
  FINDINGS section 31.
- Phase 2 is built and emulator-tested (`patch/headroom`, `hr1`; bundles `MH1` and
  `MH2`; `tools/emu/test_headroom.py`). What changed from this plan in the build is in
  section 12.
- Phase 3 (hardware) waits for the user. Nothing is committed.

**The user's decision (2026-10-26, relayed from the polymeter session):** build and run
this measurement before polymeter joins `patch/all`. Its hardware numbers set the limit
for a new whole-bundle cost check (section 8).

## 1. Short answer: stock already measures most of this

The stock firmware already times every audio block with the Cortex-M7 cycle counter
(FINDINGS section 31):

- `app_audio_block` (`0x600aa7c8`) starts a CPU meter before `midi_poll_input` and
  stops it after the engine's render. So the meter covers the sequencer, every
  sequencer-side patch hook, MIDI input, the synths and the effects.
- The meter keeps the time of the last block, the minimum, the maximum since boot and
  an average. The figures are microseconds, shown as a percentage of 2,902 µs.
- With an empty `.develop` file in the SD card's root, Config shows them once a second:
  `min: x%, max: y%` and `avg: x%, last: y%`, above "free heap" and "max stack"
  (section 15). The user has used this screen before. One stock session peaked at
  61 % with synths. Another reached 88 % in a session that included rendering; the
  meter doesn't run during an export, so that peak came from something else in that
  session.
- There is also a stock overload warning. Every 300 ms the firmware reads the worst
  block of that window. Above 84 % it shows a display element in one of three colours
  (85–92 %, 93–98 %, over 98 %). It hides it after four calmer readings. Which element
  this is on screen is not known yet.

So **phase 0 needs no patch at all**: put `.develop` on the card, load a worst-case
project on stock, `patch/all` and `patch/all` + polymeter in turn, and read
"max". That answers "how close to 100 % does the worst project get?" today, at no
risk.

What stock can't tell us, and what the diagnostic patch adds:

1. **The sequencer's share.** Stock times the whole block only. Legato's question is
   about the sequencer side (`seq_advance` and the six patch hooks chained on its
   call). Engine-side work (synths, masterfx) lands in the same number.
2. **Resolution.** Stock's steps are 1 %, which is 29 µs or about 15,300 cycles.
   Legato's whole limit (150 instructions × 16 tracks = 2,400 per micro-step) is
   about 0.2 % of a block. Stock's meter can't see a difference that small. The patch
   shows tenths of a percent, measured in cycles.
3. **No loading spikes.** Stock's max counts since power-on, including project
   loading. The patch resets its figures each time playback starts.
4. **Late blocks.** It counts blocks that started late, so a missed deadline shows as
   a count instead of a guess (section 4).
5. **No clock assumption.** Stock converts cycles to µs at 528 cycles per µs and
   compares with a fixed 2,902 µs. The patch measures the block period itself, in
   cycles, and gives percentages of that.

The user has decided to build the patch. Phase 0 stays as an optional cross-check: it
costs one extra boot per firmware, in the same hardware session. It also checks the
patch against a meter it doesn't touch. Once installed, headroom takes over stock's two
CPU lines.

## 2. The deadline

- **Block size:** 128 frames. `audio_out_cb` (`0x600a1e48`) passes 128 to the app's
  vtable `+8`.
- **Sample rate:** the Tracker+ codec runs at exactly 44,100 Hz. 44,122.65 Hz is only
  the constant `app_audio_block` uses to turn frames into seconds for `seq_advance` (a
  leftover from the original Tracker; it makes the tempo about 0.05 % slow). The CPU
  meter assumes 2,902 µs, which is 128 / 44,100 rounded.
- **Block period:** 128 / 44,100 = **2,902.5 µs** in real time. (128 / 44,122.65 =
  2,901.0 µs is the sequencer's idea of it, 0.05 % short.) Headroom doesn't depend on
  either: its percentages are of the period it measures, block start to block start.
- **Core clock:** `SystemCoreClock` is set to 528,000,000 (FINDINGS section 31). That
  makes one block **about 1,532,500 cycles**. The patch measures the real figure, so a
  core running at another speed (600 MHz would give about 1,741,500) shows up at once.
- **What happens at 100 %:** everything in a block runs inside the audio DMA interrupt.
  A block that runs past its period makes the next interrupt late. On the device a
  single block over 100 % didn't click (section 13, hr1 run B: `1s` 102.4 % in a clean
  recording); the output seems to have about one block of slack, so a dropout needs
  heavy blocks close enough together for the lateness to build up (run A, 800 BPM).
  Long before that, the main loop (screen, buttons, SD loading) starves, because it
  only gets the time the interrupt leaves. Stock's warning starts at 84 %.
- **What the meter leaves out:**
  - `audio_out_cb`'s bracket around the block. Phase 1 found it is USB audio
    ring-buffer bookkeeping, a few dozen instructions with the USB interrupt masked.
  - The eDMA handler's own overhead.

  Neither is inside `app_audio_block`. The late-block count covers them, because it
  times interrupt to interrupt. The USB interrupt (IRQ 113) runs at priority 0, so
  when it preempts the audio interrupt its time is counted inside the block. That is
  real cost for the deadline, so it should count.

## 3. What the patch times (`patch/headroom`, `hr1`)

Three readings of the cycle counter (`DWT_CYCCNT`, `0xE0001004`) per block:

| Reading | Where | Gives |
|---|---|---|
| `t0` | start of a wrapper around `app_audio_block` | the period since the previous block's `t0` |
| `s0`, `s1` | around the `bl seq_advance` at `0x600aa7f8` | the sequencer's share, with every patch hook chained there |
| `t1` | end of the wrapper | the whole block's work |

**Hooks:**

1. **The app vtable `+8` word**, `0x60038724`: a `words` hook (`thumb: true`,
   `next` chained) pointing to `mt_hr_block`. The wrapper reads `t0`, calls the chained
   word with the same r0–r3, then reads `t1`. No patch hooks this word today.
2. **The `bl` at `0x600aa7f8`**: a chained `branches` hook. Six patches already chain
   this site: swing, interpolate, visualizer, condtrig, legato and songloop. The last
   patch listed runs first, so **headroom must be listed last in any bundle**. Its hook
   then wraps all the others plus stock `seq_advance`. ITCM hooks inside the sequencer
   (polymeter, condtrig) run inside `seq_advance`, so they are counted too.
3. **The Config debug lines** (display only; section 5): the two `bl snprintf` at
   `0x600cb570` and `0x600cb590` in `cfg_debug_info`, both chained `branches`.

The engine side (midi poll + render: synths, masterfx, slicethru, resample) is the
block minus the sequencer. The two maxima are kept separately, because the worst block
and the worst sequencer share don't have to fall in the same block.

**The cycle counter is already running.** Stock's timer setup (`0x600a24a4`, called
from `main` before audio starts) sets `DEMCR.TRCENA` and `DWT_CTRL.CYCCNTENA`. The stock
meter then reads `CYCCNT` twice in every block, so reading it is nothing new for the
chip. The patch **never writes** to DWT, DEMCR or any other debug register. If
`DWT_CTRL` bit 0 is clear when Config draws, the lines say `no cycle counter`; there is
no attempt to enable it.

**Not counted: export.** During an export the byte `0x20026e44` is set:
- `app_audio_block` returns at once, while the main loop renders through its own
  `seq_advance` call at `0x600bc27a`;
- neither the stock meter nor the `0x600aa7f8` hook sees that work.

The wrapper still runs around the early return, so while the byte is set it counts
nothing: no work and no period. The interrupt keeps its pace, but blocks carry no work
then. Export speed is not a real-time matter: it only makes the export take longer.

## 4. State (`.mt_ram`, 48 bytes as built)

Written only by the audio interrupt (the main loop only reads it):

| Field | Use |
|---|---|
| `magic` | garbage at boot; any mismatch clears the state |
| `t_prev` | `t0` of the previous block |
| `nominal` | smoothed period: starts at the first period, then `nominal += (p − nominal) >> 6` |
| `blk_max`, `seq_max` | worst work and worst sequencer share since the last reset, in cycles |
| `win_max`, `win_n`, `last_win` | worst block in the current ~1 s window (345 blocks), and the last full window's |
| `late` | blocks whose period was over `nominal + nominal / 8` (112.5 %) |
| `was_playing` | previous `seq+0x2a`; a 0 → 1 change resets everything except `nominal` |

**Cost in the audio path:** 80 instructions per block for both wrappers together, as
built and measured in the emulator (estimated at 25–35 before the build). That covers
loads, subtracts, compares, the shift for `nominal`, and the call and return. There is
no division and no other call out. That is about 0.005 % of a block.
The emulator test checks the number (section 6). All the division (cycles → tenths of
a percent) happens in the Config line code, once a second, in the main loop.

**Placement:** set `"ram_startup": true`, used here only to keep the state out of
SDRAM. Cached SDRAM could add a miss to the measurement itself. The patcher then picks
DTCM or OCRAM. `patch/all` + polymeter's map will show which region it lands in, and
that the total still fits.

## 5. Showing it

**Recommended: replace stock's two CPU lines on the `.develop` Config screen.** The
hooks on the two `snprintf` calls write the patch's text into the same 30-byte buffers,
so each line can hold up to 29 characters:

```
blk 61.3%  seq 4.1%        worst block / worst sequencer share, since play started
1s 40.2%  late 0           worst block of the last second / late blocks since play started
```

- Percentages are tenths: cycles × 1000 / `nominal`, clamped.
- "free heap" and "max stack" stay as they are.
- Stock's own overload warning keeps working, because the stock meter is untouched.
- Nothing touches the SD card. The user creates `.develop` on the computer, as before.
- The Config screen redraws these lines once a second, so the values are live while
  playing (if Config can be open while playing; phase 1 checks).

Fallbacks, if the user doesn't want `.develop`:
- The version text holds 3 characters only, so it is too small.
- A read-only item under Config → Project Settings, as ramdiag did, means more hooks on
  the shared Config constructor sites. It is only worth it if `.develop` turns out to
  change something else.

Alternative content, if the user prefers it (open question 3): cycles instead of
percentages, or the measured core clock (`nominal` / 2,901 µs, e.g. `clk 528MHz`) in
place of the late count.

## 6. Emulator tests (`tools/emu/test_headroom.py`)

`tools/emu/test_interpolate.py` already calls the whole `app_audio_block` with the
stock meter, MIDI poll and render stubbed (`AUDIO_STUBS`). The test reuses that setup.

**A fake cycle counter.** `seq_timing.make()` already maps the PPB as plain RAM. A
memory-read hook on `0xE0001000`–`0xE0001FFF` writes the current fake count into
`CYCCNT` just before each read, and the test sets `DWT_CTRL` to 1. Phase 1 tried this:
two stock `cycles_q` calls 3 instructions apart read 3 apart. Two modes:
- **Instruction count:** `CYCCNT` = instructions executed, from a code hook. The
  patch's `blk` and `seq` must then equal independently counted instructions of the
  same calls, minus the wrapper's own fixed cost.
- **Scripted:** the test sets the count between calls to simulate periods.

Checks:

1. **Transparent:** with headroom on stock, and as the last patch on `patch/all`, the
   same blocks produce the same MIDI events, the same sequencer state and the same
   chained calls, in the same order, as without it.
2. **Chain position:** in `all + headroom` the `0x600aa7f8` site calls headroom's hook
   first, and its `next` word leads to songloop's or legato's hook (whichever is listed
   before it), then down to stock.
3. **Statistics:** scripted periods of 1,531,700 cycles. Then:
   - one block made 30 % longer: `late` = 1, `nominal` barely moves;
   - a playback start (`seq+0x2a` 0 → 1): the maxima reset;
   - 345 blocks: the window rolls over;
   - `0x20026e44` set (export): nothing counted, and the period is restarted when it
     clears.
4. **Garbage at boot:** random state → cleared on the first block.
5. **Audio-path cost:** headroom's own instructions per block ≤ 100 (80 measured;
   counted in its code range).
6. **Never writes DWT:** a write hook on the page fails the test on any write. With
   `DWT_CTRL` = 0 the lines read `no cycle counter`.
7. **Display:** call `cfg_debug_info` with the fake display. Render the text with
   `eve.py` and check:
   - the strings for known state;
   - that each line fits where stock's line was (stock's longest is
     `min: 100%, max: 100%`, 20 characters);
   - that the heap and stack lines are unchanged.
8. **Layout:** `tools/test_layout.py` scenarios for headroom alone, `all + headroom` and
   `all + polymeter + headroom`.

Not in `patch/all` or the web patcher: it is a diagnostic tool, like ramdiag. Its test
joins `run_tests.py`'s quick tier for its own specs only.

## 7. Hardware procedure

**The worst-case project** (the user builds it, or the emulator writes it; open question
5). Everything at once, on all 16 tracks:

- **Tempo:** the top of the tempo range, plus one run at 120 BPM for comparison.
- **Tracks 1–8:** sample instruments, a note on every step, Micro-move on every other
  step, some Roll and Arp. At least one long stereo sample and one Beat Slice
  instrument.
- **Synths:** Synth 1–3 on several tracks with chords, and their macros moved by FX on
  every step. This was stock's 61 % case.
- **MIDI tracks:** notes and a CC FX on every step, with interpolate ramps where the
  firmware has them.
- **Patch features:**
  - masterfx reverb and delay on, at long settings;
  - swing and humanize on;
  - condtrig chances on many steps;
  - legato G on the MIDI tracks;
  - in the polymeter run, different track lengths (e.g. 16, 15, 13, 7).
- **Order:** song mode with two patterns, so a pattern switch and its preload happen
  during the run.

**Phase 0 (optional cross-check: stock meter, no patch):**

1. Back up the card. Put an empty file named `.develop` in its root.
2. For each firmware, **stock**, **`patch/all` (MTW)** and **`patch/all` + polymeter**:
   1. Install it and power-cycle. Stock's max counts since boot, so each run needs a
      fresh boot.
   2. Open Config and note min/max (the boot baseline, about 42 % before).
   3. Open the worst-case project and play it in song mode for 3 minutes.
   4. Open Config and note min/max/avg/last, while still playing if possible.
   5. Note any clicks heard, and whether the overload warning appeared.
   6. Repeat at 120 BPM after another power-cycle.
3. Six readings in all. The difference between firmwares is meaningful only where it
   is more than 1–2 %.

**With headroom (the main run):** the same three firmwares with headroom listed last, e.g.
`hr1`, `MH1`, `MH2`. Read both lines after 3 minutes of play. Then stop, start again
(the figures reset) and play a single pattern without song mode, to separate the
pattern switch from steady play.

What to report back: the numbers; any clicks; whether the late count was ever above 0;
and, if Config showed the clock option, the measured MHz.

## 8. What the numbers mean for the emulator limits

**From instructions to cycles.**
- The emulator counts instructions; the device spends cycles. Patch code runs from
  flash through the cache, so a cycle per instruction is the best case. A cache miss
  costs more.
- The hardware `seq` share in cycles, divided by the emulator's instruction count for
  the same project, gives a real cycles-per-instruction figure for sequencer code.
  Call it `k`.

**A new whole-bundle cost check** (`tools/emu/test_bundle_cost.py`, the user's request).

What it runs:
- The worst-case scenario of section 7, built in the emulator from the same
  description: 16 busy tracks with the same FX mix, at the top tempo.
- It runs on stock and on each bundle: `patch/all`, `patch/all` + polymeter, and the
  reordered bundles.

What it counts, per audio block (shape set by phase 1's timed runs, section 11):
- **The sequencer side over the whole scenario:** `app_audio_block` with the engine
  stubbed, so the patched `0x600aa7f8` call runs with all its chained hooks. That is
  9–12 ms of wall time per block with every instruction counted. A 16-step pattern at
  the top tempo plus a pattern switch is a few hundred blocks: a few seconds per
  firmware. Every bundle, quick tier.
- **The whole block over a short window:** the real render on `SynthRig`, with Synth
  1–3 at full polyphony and masterfx's heaviest reverb chosen. That is 0.7–0.9 s per
  block, so only about 16 blocks, around the scenario's worst sequencer block: about
  15 s per firmware. `patch/all` and `patch/all` + polymeter only, release tier.

What it checks: worst block, bundle minus stock, ≤ `limit`. Here `limit` =
(allowed cycles) / `k`. Allowed cycles = the hardware headroom we choose to keep
spare. For example, the worst block on `patch/all` + polymeter must stay ≤ 80 % of a
block, safely under stock's 84 % warning. The 80 % is a placeholder; the user sets it
after the numbers (question 7).

The check records the hardware figures it rests on as named constants with a comment:
`k`, the measured stock and bundle maxima, and the date. A later reader can then see
where the limit came from.

**Legato's and condtrig's checks count only their own code.** Today both compare a
patched firmware's worst tick with stock's. On a bundle that measures every patch's
cost, which is why legato failed at 151 per track on `patch/all` + polymeter. They
become per-patch checks:
- count instructions only inside the patch's own code range (`layout.patch_code`),
  per track and step, under the same rig;
- keep a per-patch limit, now a share of the measured headroom instead of 150 by
  judgment. For example: one patch ≤ 1 % of a block at the worst tick, converted with
  `k`.

The total is then guarded once, by the bundle check, instead of by accident in two
patch tests.

Phase 1's grep of the other cost checks:
- Interpolate (≤ 40,000 per block), polymeter (≤ 50 per track and step) and resample
  (≤ 20 per block) already count only their own code.
- Copypattern's `BUSY_LIMIT` and synths' `BLOCK_LIMIT` are hang guards, not cost
  limits.
- Masterfx's "Stock chosen" check compares a whole block with stock firmware (under
  200 more instructions). On a bundle, other engine-side patches' work would count
  there too. It passes on `patch/all` today, but it gets the same split: count
  masterfx's own code.

Which shares to allow (per patch and for the bundle) is the user's call once the numbers
are in.

## 9. Phases

| Phase | What | Risk |
|---|---|---|
| 1 (done) | Static and emulator checks: who else calls the app vtable `+8` (render path?); whether rendering runs blocks at another pace; what `0x600a1cbc` brackets; that Config's debug lines refresh while playing; that the emulator page mapping and read hook work; a timed run of the full render in the emulator, for the bundle check's design; other tests that compare a bundle's total with stock | none |
| 2 (done) | Build `patch/headroom`, `tools/emu/test_headroom.py`, bundle specs (stock + headroom, all + headroom, all + polymeter + headroom), layout scenarios; quick tier | none (emulator) |
| 3 (done) | Hardware: section 7, optional phase 0 cross-check, then the three headroom firmwares (run sheet: section 13; results: section 14) | low: one vtable word, three chained BLs, reads of a register stock already reads |
| 4 (done) | `tools/emu/test_bundle_cost.py` with limits from the numbers; legato's, condtrig's and masterfx's cost checks reduced to their own code; results (section 14) | none |
| 5 | Polymeter joins `patch/all` (the polymeter session's phases), if the bundle check passes | per that plan |

Each phase waits for the user's go-ahead. Nothing is committed unless the user says so.

Polymeter (`patch/polymeter`) is uncommitted and still changing. Its measured build
uses that folder's prebuilt as it is on the day of the hardware run. The report notes
its version text and the prebuilt's SHA-256, so the numbers can be tied to that exact
build.

## 10. Open questions for the user

**Answered 2026-10-26: the plan is approved with the recommended answers.**
1. Phase 0 runs once, as a cross-check.
2. The figures replace the two CPU lines on the `.develop` Config screen.
3. The lines show percentages.
4. The figures reset at every playback start.
5. The emulator writes the test project, if `project_save.py` can; otherwise the user
   builds it.
6. The names are `patch/headroom` (`hr1`), `MH1` and `MH2`.
7. The limits are set after the numbers are in.

The original questions:

1. **Phase 0 as a cross-check?** The patch is decided. Do you also want the stock meter
   readings, which take one extra boot per firmware in the same session?
   (Recommended: yes, once.)
2. **Display:** replace the two CPU lines on the `.develop` Config screen (recommended),
   or a Project Settings item?
3. **Line content:** percentages (above), cycles, or the measured clock instead of the
   late count?
4. **Reset rule:** at every playback start (recommended), or only on power-up, as stock
   does?
5. **The test project:** build it on the device, or have the emulator write it to copy
   to the card (an extra, using `tools/emu/project_save.py` if it can do this)?
6. **Names:** `patch/headroom` with `hr1`, and bundle texts `MH1` (all + headroom) and
   `MH2` (all + polymeter + headroom)? The bundle specs would live in
   `patch/headroom/`, because the patcher can't include a bundle in a bundle.
7. **The limits afterwards:**
   - The whole bundle: the worst block may reach what share of a block (e.g. 80 %,
     under stock's 84 % warning)?
   - One patch: what share at the worst tick (e.g. 1 %)?

## 11. Phase 1 results (2026-10-26)

All facts and addresses are in FINDINGS section 31 ("Export, the block's other callers,
and Config").

| Question | Answer | What changes in the plan |
|---|---|---|
| Who else calls the app vtable `+8`? | Only `audio_out_cb`. The one reference to `app_audio_block` is the vtable word `0x60038724` | The word hook sees every real-time block and nothing else |
| Does rendering run blocks at another pace? | Export doesn't run audio blocks at all. It sets `0x20026e44`, `app_audio_block` returns at once, and the main loop renders through its own `seq_advance` call (`0x600bc27a`) | Pause on `0x20026e44` instead of `seq+0x7dc` (section 3). Section 15's 88 % didn't come from the export itself |
| What does `0x600a1cbc` bracket? | USB audio ring-buffer bookkeeping (the platform object's `+0x31c`), with USB_OTG1 (IRQ 113) masked; a few dozen instructions | Nothing; the late count covers it (section 2) |
| Do Config's debug lines refresh while playing? | Yes. `scr_config_update` refreshes them every ≥ 1,000 ms whenever the developer byte is set, with no playback check | The display plan stands. Whether the Config screen can be opened during playback is a UI question for the hardware run (the sequencer keeps playing across screens) |
| Do the emulator page and read hook work? | Yes. `seq_timing.make()` already maps the PPB; a read hook supplies `CYCCNT`, and the instruction-count mode reads exactly | Section 6 simplified |
| How slow is a full block in the emulator? | Whole block with Synth 1–3: 0.7–0.9 s each (about 744,000 instructions, the render about 96 %). Sequencer side alone: 9–12 ms per block | The bundle check's split (section 8) |
| Other bundle-vs-stock cost checks? | legato and condtrig (sequencer side), masterfx's "Stock chosen" (engine side) | All three get the own-code split (section 8) |
| Can the emulator write the test project? | In principle: `project_save.save()` runs the firmware's own Save As under `FakeFS`. Getting the files out of `FakeFS` onto a card is unchecked; the samples would be the user's own | Tried before the hardware run (not part of the phase 2 build); if it doesn't work, the user builds the project on the device |

A first emulator estimate: the stock firmware's whole block with three synth slots
sounding is about 744,000 instructions. At one cycle per instruction that is about
49 % of a block. The device read 61 % for a similar load, so `k` (cycles per
instruction) may be near 1.2–1.3 for this mix. Only the hardware run can say.

## 12. Phase 2: as built (2026-10-26)

- **Files.**
  - `patch/headroom/`: `headroom.c`, `link.ld`, `build.sh`, `patch.json`,
    `README.md`, and `prebuilt/`.
  - The bundle specs `all_headroom.json` (`MH1`) and `all_polymeter_headroom.json`
    (`MH2`).
  - `tools/emu/test_headroom.py`.
  - Three headroom scenarios in `tools/test_layout.py`, and the three builds as
    `DIAGNOSTICS` in `tools/run_tests.py`, which runs them in every tier.
- **Hooks.** As planned:
  - the app vtable `+8` word;
  - `bl seq_advance` at `0x600aa7f8`;
  - the two Config `bl snprintf`.

  All are chained. Headroom must be listed last; the test fails otherwise (checked
  with a headroom-then-legato bundle).
- **Code and RAM.** The code is 757 bytes and the state 48 bytes, `ram_startup`.
  Alone, the state lands in the OCRAM tail. In both bundles it lands in
  `dtcm_after_audio` (`0x200094c0`), the 840-byte gap after the audio block pool. That
  region was untouched in ramdiag's hardware session (FINDINGS section 15), but no
  hardware-tested patch has used it yet; headroom will be the first.
- **Changes from the plan:**
  - No on-demand reset flag. The figures reset only when playback starts.
  - Percentages are cycles ÷ (period ÷ 1000), so the code needs no 64-bit division;
    that is off by under 0.1 %. They are capped at 999.9 %.
  - The late count shows at most 99999.
  - `1s` is the worse of the current and the last full 345-block window.
  - The audio-path cost is 80 instructions per block, not 25–35.
- **The test** (0 failures on `hr1`, `MH1` and `MH2`):
  - the block figure is the chained call's instructions + 41 of headroom's own;
  - the sequencer figure is the chained call's + 4;
  - the period equals the test's clock step;
  - MIDI and sequencer state are identical to the same firmware with headroom's audio
    hooks put back (160 blocks, 16 MIDI tracks).

## 13. Phase 3 run sheet (hardware)

This is section 7 made concrete. One project serves every run: each firmware ignores the
files of patches it doesn't have, so `hr1` plays it as stock would, `MH1` adds patch/all's
features, and `MH2` adds polymeter's track lengths.

### The firmwares

| Order | File | Config shows | What it is |
|---|---|---|---|
| 0 (optional) | your stock `TrackerPlus_1.2.0.ptf` | `1.2.0` | the stock meter alone (phase 0, once) |
| 1 | `build/TrackerPlus_hr1.ptf` | `1.2.0-hr1` | stock + headroom |
| 2 | `build/TrackerPlus_MH1.ptf` | `1.2.0-MH1` | patch/all (MTW) + headroom |
| 3 | `build/TrackerPlus_MH2.ptf` | `1.2.0-MH2` | patch/all + polymeter + headroom |

Full hashes (the builds the quick tier passed on 2026-10-26):
- `hr1`: `3c5205aa0194dc627df1526f60833de44009eb529daa7d5d11102c5171088d1d`
- `MH1`: `45dc76626ace9a7b6847786115dff197e83eb4b0bf25db4ea260822e2689dd29`
- `MH2`: `3a6003152b20b7b69a5b7a9acaafbe37a4acd0eba59306f94d92de54099b1daa`, with
  polymeter `pm1` (prebuilt `polymeter.bin`
  `6243b655607117d5e1c67870c2fb774855e030896281473f8b7701c9c801156c`). If polymeter's
  prebuilt changes before the run, rebuild `MH2` and run the quick tier again first.

`hr1` goes first because its state sits in the OCRAM tail, which earlier patches already
use on hardware. `MH1` and `MH2` put it in `dtcm_after_audio` (`0x200094c0`), which no
hardware-tested patch has used yet (section 12). If `MH1` misbehaves in any way (no
boot, a freeze, noise), stop there and go back to your usual firmware.

### Before you start

1. Back up the SD card.
2. Put an empty file named `.develop` in the card's root.
3. Copy the three `.ptf` files above into `/Firmware` on the card.
4. Have a phone ready to photograph the Config screen: that's the easiest record.

### The test project (instruments on the device, the rest from the emulator)

`tools/make_headroom_project.py` writes the busy part into a copy of a project you make
on the device. It runs the firmware's own pattern writer and reader, Song screen mask
update and `project.mt` writer in the emulator, then plays the song on the given
firmware as a check. What it writes:

- **Patterns 1 and 2** (16 steps), a note and both FX on every step of all 16 tracks:
  - tracks 1–8, instruments 1–8: Micro-move on every other step, condtrig conditions
    (Chance 103–137) on tracks 1–4, Roll on 5–6 and Arp on 7–8 between them; reverb and
    delay sends;
  - tracks 9–14, synths S1, S1, S2, S2, S3, S3: a MIDI Chord on every step, and Macro 1
    or 2 swinging 0 ↔ 127 on every step, so FX Interpolation ramps all the time;
  - tracks 15–16, `M01` and `M02`: CC A swinging 0 ↔ 127, `G` (legato) on every other
    step and CC B between. Nothing needs to be plugged into MIDI out.
- **`project.mt`:** the song pattern 1, pattern 2 (a pattern switch with its preload on
  every pass); the tempo 800 BPM, the top of the firmware's range (its tempo edit,
  `0x600fd6b0`, clamps to 40–800); no muted track; both patterns' Song screen masks.
  Everything else stays as you saved it.
- **The mods' settings** (stock ignores them, so they count only on `MH1` and `MH2`):
  Master FX Reverb **Shimmer** and Delay **"Gated"** (the two heaviest); Global Swing
  on at 66; FX Interpolation MIDI CC and Synths **On**; polymeter lengths 15, 13, 11 and
  7 on tracks 3, 6, 11 and 14 (`MH2` only).
- Humanize has nothing to set: it changes notes when its button is pressed, so it costs
  nothing during playback.

Checked in the emulator on a copy of one of your Save As test projects (2026-10-26):
- the patterns come back identical through the firmware's reader;
- the base `project.mt` comes back identical through the firmware's writer before any
  change;
- the masterfx and interpolate files are byte-identical to the ones the patches wrote
  on the device for the same settings;
- six patterns of song mode on `MH2`: 1,302 sample note-ons, 2,253 synth notes, 192
  MIDI note-ons and 288 CCs, patterns 1 and 2 both requested. On stock: 1,440 sample
  note-ons, since there a Chance above 100 always plays.

The firmware's project open, from the card into Workspace, wasn't run in the emulator.
That's the first thing step 6 below checks on the device.

**On the device** (on your usual firmware is fine):

1. New project. Instruments 1–8: load samples, at least one of them a long stereo
   sample. Set instrument 8's play mode to Beat Slice.
2. Synths S1, S2 and S3: any models, Polyphony **3, 3 and 2** (the three slots share
   8 voices; the firmware allows no more), and their Reverb Send turned up.
3. Master: long Reverb and Delay settings.
4. Save As `HEADROOM`. Power off and take the card out.

**On the computer:**

5. Copy the project's folder (under `Projects` on the card) to the computer, then:

   ```bash
   .venv/Scripts/python tools/make_headroom_project.py build/TrackerPlus_MH2.ptf <copied folder> <new folder>
   ```

   Use the repo's Python (`.venv/bin/python` outside Windows): it has the emulator's
   packages, and a system Python usually doesn't.

   It prints what it wrote and the check's note counts. It warns if an instrument slot
   1–8 or a synth patch is missing. Copy the new folder's contents over the project's
   folder on the card (the same folder name).

**Back on the device:**

6. Open `HEADROOM` from the project screen (Open, even if it was the last project:
   opening copies the new files into Workspace). Don't Save before that. Check:
   - pattern 1 has a note on every step of every track, and the song is 1, 2;
   - the tempo reads 800 (if it shows another top value, note it);
   - on `MH1`/`MH2`: Config → Project Settings shows Shimmer / "Gated", Global Swing on
     at 66, and FX Interpolation On / On;
   - on `MH2`: Trk Len shows 15, 13, 11 and 7 on tracks 3, 6, 11 and 14.

   If any of that is wrong, stop and tell me what you see.

The same project is used on every firmware.

### Each firmware

1. Install it (Config → Firmware) and let it reboot. Check that Config shows the
   version in the table.
2. Open Config once before playing. Headroom's lines read `headroom: waiting`, or
   figures from the idle blocks since boot; both are fine. They reset when playback
   starts.
3. **Run A: song, 800 BPM.** Open `HEADROOM`, play the song for 3 minutes. Then, still
   playing, open Config and photograph the two lines (`blk … seq …` and `1s … late …`).
   If Config can't be opened while playing, or playback stops when you open it: stop,
   open Config at once, and photograph it then. `blk`, `seq` and `late` keep their
   values until the next play; only `1s` falls to idle within about a second, so note
   that it was read after stopping.
4. **Run B: song, 120 BPM.** Set 120 BPM, play again (the figures reset), 3 minutes,
   read as in step 3.
5. **Run C: one pattern, 800 BPM.** Back to 800 BPM, play pattern 1 alone (not
   song mode) for 3 minutes, read as in step 3. This separates the pattern switch from
   steady play.
6. During all three, note any click or dropout you hear, and whether stock's overload
   warning appears.

**Phase 0 (optional, once):** on the stock firmware, power-cycle, play run A, then
open Config and photograph stock's `min/max/avg/last` lines. Stock's max counts since
boot (about 42 % at power-on), so only a fresh boot gives a clean figure. It's a
cross-check of `hr1`'s `blk`; the two should agree within a few percent.

Afterwards, install your usual firmware again. If a unit doesn't boot: put only the
stock `TrackerPlus_1.2.0.ptf` in `/Firmware`, power on with the card in, and press the
pinhole button next to the USB-C port.

### What to send back

| Firmware | Run | blk % | seq % | 1s % | late | Read while playing? | Clicks / warning |
|---|---|---|---|---|---|---|---|
| hr1 | A | | | | | | |
| hr1 | B | | | | | | |
| hr1 | C | | | | | | |
| MH1 | A / B / C | | | | | | |
| MH2 | A / B / C | | | | | | |
| stock (optional) | A | min/max/avg/last: | | | | | |

Plus anything odd: a slow boot, the screen stuttering, a run where `late` was above 0.
Photos are enough; I'll read the figures off them.

### What the numbers then decide (phase 4)

- `blk` on `MH2` against the 84 % warning says how much headroom the full bundle leaves.
- `seq` and the emulator's instruction count for the same project give `k`, the cycles
  per instruction of sequencer code, which turns the emulator's limits into real time
  (section 8).
- `MH1` minus `hr1`, and `MH2` minus `MH1`, are what patch/all and polymeter cost on
  the device.
- Any `late` above 0, or a click, is a finding in itself, whatever the percentages say.

## 14. Phase 3 and 4 results (2026-10-27)

The test project of section 13, song mode at 800 BPM, about 3 minutes per run, read on
Config while playing, recorded from the outputs (glitches counted in the recordings).

### The runs

With Synth 1-3 at Polyphony 3/3/2:

| Firmware | Run | Master FX (reverb / delay) | blk | seq | 1s | late | Glitches |
|---|---|---|---|---|---|---|---|
| hr1 | A | stock | 110.6 % | 30.1 % | 104.9 % | 0 | 139 |
| hr1 | B (120 BPM) | stock | 107.5 % | 26.0 % | 102.4 % | 0 | 1 |
| hr1 | C (pattern 1) | stock | 109.0 % | 29.4 % | 105.6 % | 0 | 605 |
| MH1 | A | Shimmer / "Gated" | 130.3 % | 33.9 % | 130.3 % | 6,809 | 10,599 |
| MH1 | D (synth interpolation Off) | Shimmer / "Gated" | 132.1 % | 32.1 % | 130.0 % | 6,626 | 6,092 |
| MH1 | E (as D) | Stock / Stock | 118.0 % | 31.5 % | 115.7 % | 2,009 | 1,319 |
| MH1 | F (as D) | Shimmer / Stock | 125.4 % | 33.2 % | 121.5 % | about 5,100 | 2,173 |
| MH1 | G (as D) | Stock / "Gated" | 127.7 % | 32.3 % | 121.9 % | 5,395 | 2,916 |
| MH1 | H (as E, MIDI interpolation and swing Off) | Stock / Stock | 115.1 % | 30.5 % | 113.6 % | 282 | 1,473 |

With Synth 1-3 at Polyphony 0 and tracks 9-14 (the synth tracks) muted, everything else
on (Shimmer / "Gated", Global Swing 66, FX Interpolation MIDI CC and Synths On):

| Firmware | blk | seq | 1s | late | Glitches | Free heap | Max stack |
|---|---|---|---|---|---|---|---|
| hr1 | 46.0 % | 18.1 % | 43.4 % | 0 | 187 (spikes, below) | 85,768 B | 6,609 B |
| MH1 | 66.5 % | 20.7 % | 64.7 % | 0 | 3 (first 30 s) | 84,040 B | 8,169 B |
| MH2 (played in pattern-loop mode) | 68.1 % | 18.6 % | 65.3 % | 0 | 0 | 84,012 B | 8,121 B |

### What they show

- **A single block over 100 % doesn't drop out.** The output keeps about one block of
  slack. Dropouts come from overruns close together, so `late` (a block starting over
  12.5 % late) misses runs of mild overruns. hr1 run B read 107.5 % with 1 glitch.
- **The synth voices are the big load.** An allocated voice is rendered whether it
  sounds or not: idle, with nothing playing, MH2 read about 80 % with 8 voices and
  37 % with none. With 8 voices this project overloads stock firmware too.
- **The glitches come once per pattern pass** (0.300 s at 800 BPM) when the load is near
  the limit, from the heaviest step's blocks.
- **Without synths the full bundle leaves a third of a block.** patch/all with every
  feature on: 66.5 %, with polymeter 68.1 %, against stock's 46.0 % and its 84 % warning.
- **patch/all costs about 20 points over stock** (no synths): about 18 are masterfx's
  Shimmer and "Gated" over the stock reverb and delay, about 3 swing and MIDI
  interpolation (runs E and H), the rest the always-on hooks. Polymeter adds about
  0.1 % (emulator), below what one maximum can show.
- **The stock effects at Reverb Size 100 and Delay Time 3500 ms make one-sample spikes**
  (hr1's no-synth run: 187, steady, mostly 0.6 s apart, at 46 % with `late` 0). Glitch
  counts compare only between runs with the same Master FX.

### Cycles per instruction (`k`)

- The emulator plays the same project (`tools/emu/test_bundle_cost.py`'s scenario) and
  counts instructions per block.
- **Differences, not totals.** The no-synth pair, MH1 minus hr1 (same project, neither
  overloaded):
  - sequencer side: 2.6 points = about 39,800 cycles, against 10,900 more instructions:
    **3.7 cycles each**;
  - engine side: about 17.9 points = about 274,000 cycles for Shimmer and "Gated",
    against about 61,000 more instructions: **4.5 cycles each**.
- **Working figure: `k` = 4** for patch code. 1 % of a block is about 15,300 cycles,
  about 3,800 patch instructions.
- Stock's own sequencer work doesn't fit it: hr1's 18.1 % is about 277,000 cycles for
  27,700 instructions. The device's maximum also holds what the emulator doesn't run:
  interrupts inside the bracket, the song's pattern loads (the no-synth MH2 run, in
  pattern-loop mode, read 2 points less `seq` than MH1 in song mode), data stalls. So
  every limit is set on a bundle's difference from stock.

### The checks (phase 4)

- **`tools/emu/test_bundle_cost.py`** plays the project in RAM on stock and on a bundle,
  with every mod the bundle has switched on as in the runs:
  - quick: the sequencer side's worst block over four passes of the song, at most
    **20,000** instructions over stock (about 5 % of a block). patch/all + polymeter
    today: 13,355;
  - full: plus the engine side with the real render (Synth 1-3 sounding, Shimmer and
    "Gated"), the sum at most **120,000** over stock (about 31 %: with stock's 46 %, under
    80 %). patch/all + polymeter today: 75,079 (19.6 % at `k` = 4; the device read 22.1
    points);
  - `tools/run_tests.py` runs it on every firmware it tests, in full on patch/all in the
    release tier. Stock's figures are kept in `build/bundle_cost_stock.json`.
- **Each patch's own code: at most 3,800 instructions per block** (1 % of a block),
  instead of comparing a bundle's total with stock:
  - legato: 16 MIDI tracks with G on every note, 800 BPM: 2,160 at worst;
  - condtrig: a condition in FX1 and FX2 of every track: 2,003 at worst;
  - masterfx with Stock chosen: 32.
