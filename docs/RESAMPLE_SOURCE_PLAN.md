# Plan: hearing and recording the tracker in the Sample Recorder

**Status:** design agreed with the user (2026-10-29); built as `rs5` to `rs8`. `rs8` is
hardware-tested on its own (section 5.1) and shipped in `patch/all` as `1.2.0-MR8` and
in the web patcher (MR8 hardware-tested 2026-10-30). This is phase 7 of [LIVE_RESAMPLING_PLAN.md](LIVE_RESAMPLING_PLAN.md), which
built `patch/resample` (`rs4`, in `patch/all` as `1.2.0-MR4`).

Today, to record the tracker's own sound you cable the output back to Line In. This
records it inside the unit instead.

## 1. What the user gets

Two new buttons in the Sample Recorder's setup, in the two empty slots after Source:

| Slot | Button | Off | On |
|---|---|---|---|
| 2 | **Trk Audio** (Tracker Audio) | the tracker is silent in the recorder, as stock | you hear the tracker (Play, or with the recording as below) |
| 3 | **Trk Record** (Record Tracker) | the recording holds Line In only, as stock | the recording holds Line In **plus** the tracker |

The user's cases, with Line In selected:

- **Both off:** record Line In; the tracker isn't heard. (Stock.)
- **Tracker Audio on, Record Tracker off:** record Line In; the tracker is heard but not
  recorded.
- **Both on:** record Line In and the tracker together; the tracker is heard.
- **Monitor** only decides whether you hear Line In, as in stock; it never changes the
  recording.
- **Nice to have, not required:** record the tracker without hearing it (Record
  Tracker on, Tracker Audio off). See section 4.

Pressing a button toggles it between Off and On (Monitor opens a list instead; the
toggle is simpler and needs no new list). The names are abbreviated to fit the slot
("Metronome" is about the widest text that fits).

**Starting and stopping, with Tracker Audio on** (rs4's rules, now behind the button):

- **Play in the setup** plays the pattern and you hear it, so you can set levels before
  recording. Rec then records with the pattern running.
- **Rec then Play:** the pattern starts and is heard during the recording.
- **Shift+Rec**, or **Play during the count-in:** the pattern starts with the recording.
- **Stopping the recording stops the pattern.**

With Tracker Audio off, Shift+Rec is a plain Rec and Play stays silent, as in stock.

## 2. How it works

- **Hearing the tracker** is rs4's "open the gates": undo the recorder entry's changes
  with the stock leave path's calls, and let `engine_block_update` run in full. rs4
  opened them during a recording only; rs5 also opens them for Play in the setup.
  The samples are safe there: only a recording writes into the sample bank, and rs4
  already moves every recording after the loaded samples (if it can't, the gates close
  before the recording starts, as stock).
- **Recording the tracker:** the master mixer (`E+0x2508`/`E+0x2574`) feeds, besides
  the output jacks, a pair of record queues (`E+0x1e58`/`E+0x2248`) that only Export
  uses. At the record start the patch begins them together with the input queues
  (audio interrupt masked, so both start on the same block). In the copy of each input
  block (`bl` at `0x6010c6f6` in the record copy loop) it takes the matching master
  block and:
  - **Monitor off:** adds it to the input block (clipped to 16 bits);
  - **Monitor on:** records the master block alone, which already holds Line In
    (Monitor routes the input through the master at 0 dB), so Line In isn't doubled.

  The emulator check (scratch, on rs4): a recording taken from the master queues is
  bit-identical to the output jacks, both channels, every block; with Monitor on it
  holds the input too. No cable, so no analog round trip and no feedback loop.
  On rs5 (`test_resample.py` section 12): the recording is the input plus the jacks'
  signal frame for frame from its first frame (Monitor on: the jacks' signal), a loud
  sum clips instead of wrapping, and what you hear is unchanged by Record Tracker. The
  mix costs about 1000 instructions per audio block (the M7's saturating 16-bit add).
  If a recording ever stops without the recorder's update (the screen left), the next
  audio block hands the master queues' blocks back to the pool.
- **The buttons:** slots 2 and 3 are label controls `scr+0x5f`/`scr+0x60`. The
  recorder's button binder `0x600f8e3c` binds buttons 0-7 with state handlers
  (`0x600f8fe0` → `0x600f8b1d`, `0x600f8fe4` → `0x600f8aa9` for slots 2 and 3), but in
  the setup it then calls the setup's binder `0x600f8db4`, which rebinds them
  press-only: slot 2 pool word `0x600f8e24` (`0x600f86ad`), slot 3 `0x600f8e28`
  (`0x600f8a4d`). Those select the slot (`scr+0x75`) or act only for Radio (frequency,
  seek). The patch hooks the setup's two words (rs5's first build hooked the state
  handlers, which the setup never reaches: the buttons did nothing on hardware).
  A press toggles the setting; it doesn't move the selection frame to the slot.
  The labels are emptied by `0x600fbc40` for every source but Radio (`bl` at
  `0x600f99e2`, `0x600f9a3a`, `0x600f9ab6`, `0x600fcbd4`); the patch draws its two
  labels after it.
- **Entering the recorder** (vtable start, word `0x60042c58`): the patch clears its
  per-visit state (gates, arm, recording), so nothing from a previous visit lingers.

## 3. The test builds (rs5, rs6) and what they leave for later

- **Line In first.** Tracker Audio shows for Line In, Mic LG, Mic HG and USB; Record
  Tracker only for Line In (the stereo copy path). Radio shows its own controls in
  those slots, so neither button is there and the recorder is stock with Radio (rs4
  let Shift+Rec start the pattern with Radio; rs5 doesn't).
- **Remembered per project (rs6).** rs5 kept both until power-off. rs6 keeps them with
  the project, as Monitor is kept, in `<project folder>/resample.bin` (12 bytes,
  checksummed), the way swing keeps `swing.bin`: Off on a new project and on a project
  without the file; read and written from the Pattern screen's update and the
  recorder's (never during a recording or its count-in), only while the file manager
  is idle; Save As takes them to the new folder (the save request's name copy,
  `0x60114eaa`). Stock saves Monitor in `project.mt` (`0x20204000+0x6ed`), but the
  struct's free bytes can't be identified safely (FINDINGS section 14), so the patch
  doesn't use it.
- **Record Tracker needs Tracker Audio on**, so the two buttons keep each other honest:
  turning Record Tracker on also turns Tracker Audio on, and turning Tracker Audio off
  also turns Record Tracker off (and stops a pattern Play started in the setup).
  Recording without hearing is section 4's later mode.

## 4. Later

1. **Per-project memory**: done in rs6 (section 3).
2. **Record without hearing** (Record Tracker on, Tracker Audio off): the engine runs
   and is recorded, but the jacks get only what Monitor says (the monitored Line In).
   The master feeds the jacks and the tap alike, so the patch would have to feed the
   output sink (`E+0x24d4`) something else; the metronome and Monitor go through the
   master too. Possible, more work.
3. **Mic and USB plus the tracker** (Mic is mono: the master's two channels averaged).
4. **The level meter** shows the input; with Record Tracker on it could show the mix.

## 5. The hardware check (rs5, 2026-10-29)

The user's check of the second rs5 build passed every step: the buttons show and
toggle; both Off is stock (Line In only, Play silent, Shift+Rec plain); Tracker Audio
On is heard in the setup and during the recording, and the stop stops the pattern;
Record Tracker records the pattern with no cable, Line In plus the pattern with a
source plugged in, and Line In isn't doubled with Monitor on; leaving and coming back
keeps both settings and nothing plays on; loaded samples are intact. (The first rs5
build drew the buttons but they did nothing: section 2, "The buttons".)

The open questions, answered on hardware:

- **The Volume knob doesn't reach the recording:** a take at -10 dB sounds the same as
  one at 0 dB, so the knob comes after the tap.
- **The Gain setting** changes Line In in the recording, not the tracker, as expected
  (it is the input's gain).
- **The metronome isn't in the recording** (rs5). On rs6 the count-in's last click
  was: see section 5.1.
- **Clipping:** nothing unusual heard; the recorded pattern sounds like its playback,
  including a sample driven hard with the Volume FX. The emulator check covers a loud
  input plus the pattern (the sum clips to 16 bits instead of wrapping).

### 5.1 The rs6 and rs7 checks (2026-10-29)

- **rs6:** per-project memory worked for power cycles, Save As and older projects. Two
  bugs:
  - New Project from a project with both On showed both On in the recorder until the
    Pattern screen was visited, and New Project then an immediate save saved them On.
    The patch looked for a project change only from the Pattern screen's update and
    the recorder's; New Project and Save As both happen on the project screen. rs7 also
    ticks from the project screen's update (vtable word `0x6004193c`, `0x600ead59`), and
    the recorder redraws the slots when a project change resets them.
  - With Record Tracker, the take began with the count-in's last click.
- **The click:** the sequencer's click (ITCM `0x42e4`) sets the preview player's master
  gain (input 5, `+0x2c`/`+0x48` on both master mixers) to the metronome volume
  (settings `+0xea`) and starts the click sample (404 frames) on the preview player
  `E+0xdb4`. The last click starts in the tick that ends the count-in, just before the
  end callback `seq+0x6fc` (`0x600f8605`, installed by the recorder's start from the
  pool word `0x600fa490`). The record queues and the output sink update before the
  master mixers, so a take's first master block is the one mixed before the record
  start.
  - rs7 muted the preview's input at the record start: the user's take still held the
    click's first 128 frames (one block).
  - rs8 mutes it in the count-in's end callback, in the same tick as the click, before
    any of it is mixed. It is given back when the mix ends, at the next record start if
    no mix starts, and in the update if the count-in ends without a recording. With
    Record Tracker on, the last click is therefore not heard either.
  - Hardware (rs8): no noticeable click in a count-in take with Record Tracker on.
- **Hardware (rs8), New Project:** from a project with both On, New Project, then
  straight to the recorder: both Off; New Project, then Save As at once (no Pattern
  screen or recorder in between): the saved project has both Off.

## 6. Build steps

1. rs5 in `patch/resample` (this build): the buttons, the gates in the setup, the
   tracker tap; `test_resample.py` extended (the rs4 checks with Tracker Audio on, the
   stock behaviour with it off, the labels and buttons, the recording = Line In +
   tracker with Monitor on and off, stock's other sources).
2. The user's hardware check of rs5: passed (section 5).
3. Per-project memory (rs6): built; `test_resample.py` section 13 (resample.bin through
   the Pattern screen's and the recorder's updates, Save As and new projects through
   the firmware's save request). Then the user's hardware check of rs6.
4. `patch/all` and the web patcher, release tier.
