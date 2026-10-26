# Save As fix for the per-project mods: plan

Status: done for swing, humanize, interpolate and masterfx (sw2, hz9, ip6, mf5; all: MTS):
release tier passed 2026-10-25, hardware-tested 2026-10-26. colorsong left out (section
3.4).

## Verdict

- **Affected, confirmed in the emulator:** swing, humanize, interpolate and masterfx
  lose their setting on Save As **and** on a new project's first save. That holds alone
  and in `patch/all` (`1.2.0-MT5`), with the firmware's real save request.
- **colorsong is affected too, but only in looks.** After Save As the colours are
  rebuilt. They come out the same unless the song was edited after the colours were
  handed out; after such edits, 18 of 176 and 30 of 192 blocks changed colour in two
  stock demo projects. Nothing you chose is lost. Fixing it is optional (section 3.4).
- **Not affected:** synths (keeps its choice and writes `synths.bin` itself), slicethru
  (bt5, already fixed), and condtrig, legato, copypattern, visualizer and resample (no
  per-project file).
- **The fix:** the slicethru bt4 recipe, copied into each of the four: a chained hook
  on `bl strncpy` at `0x60114eaa`, a "New Project" check, and a few smaller changes
  (section 3). No patch can lend code to another, so each gets its own copy and its own
  link in the chain. The added state field fits in each patch's current RAM block, so
  no RAM block grows.
- **Batch the four.** Every fix also changes its test under `tools/emu/`, and any such
  change empties the whole test cache. So each separate round costs a full release run:
  **~105 min measured**, dominated by the forced full synths run. Four rounds come to
  **~7 h of runs and four hardware sessions**. One batch comes to **~1 h 55 min and one
  hardware session**. Section 5 has the numbers. The release run is over 45 minutes, so
  it needs your OK before it starts.

## 1. Which patches keep per-project state

| Patch | File | What happens on Save As / new project's first save (emulator) | Affected |
|---|---|---|---|
| swing | `swing.bin` | Setting back to Off/50, no file in the new folder | **yes** |
| humanize | `humanize.bin` | Values back to the defaults, no file | **yes** |
| interpolate | `interpolate.bin` | MIDI CC / Synths back to Off, no file | **yes** |
| masterfx | `masterfx.bin` | Reverb and delay back to stock, no file | **yes** |
| colorsong | `colorsong.bin` | Colours rebuilt; differ after edits (see above); file rewritten for the new name | cosmetic |
| synths | `synths.bin` | Kept, file written to the new folder | no |
| slicethru | `slicethru.bin` | Kept (bt4 fix) | no |
| condtrig, legato | none (the FX live in the pattern files, which the firmware copies) | n/a | no |
| copypattern | none (reads Workspace pattern files only) | n/a | no |
| visualizer, resample | none | n/a | no |
| ramdiag, itcmtest, versiontest | diagnostics, not offered in the web patcher | n/a | no |

### How it was confirmed

The emulator scripts are scratch files, kept out of the repo. They drive the **real
firmware**: the per-project patches through the Pattern screen's update slot
`0x600403DC` (every patch's chained hook in the firmware under test) and colorsong
through `test_rescan.py`'s rig. Save As is the firmware's own save request
`0x60114E34` with a path and a name, as `test_slicethru.py` does it.

- **Save As:** project "A" in `Projects/User`; change the setting; wait until the file
  is written; Save As "B". Result: the request returned 1 with state `0x0d`, and the name
  became "B".
  - swing, humanize, interpolate and masterfx were back to their defaults, with no
    file in B.
  - synths and slicethru kept their values and wrote B's file.
- **New project:** "New Project" with an empty path, as `0x60117c8c` makes it; change
  the setting; Save As "B". Same results.
  - The four also try to read and write `/New Project/<mod>.bin` at the card's root
    while the project has no folder. synths does too.
  - This is harmless on the device while no root folder of that name exists, because
    the open fails. But it is the same mistake FINDINGS section 25 describes: they test
    the name instead of the path.
- **Builds:** each of the four alone (`patch_ptf.py` on its own `patch.json`) and
  `patch/all` gave the same result. masterfx's settings block isn't the start of its RAM
  (`reverb_rig.ram_map` finds it).
- **colorsong:** fresh session, Song visit, colours saved for A. Then, optionally, the
  first used track gets another instrument (`test_rescan` part 8's edit). Then Save As B
  and visit the Song screen again.
  - Without the edit: 0 blocks changed.
  - With the edit: 18 of 176 and 30 of 192 changed. `colorsong.bin` was written for B
    with the new colours, which then stay.

Why the current tests pass anyway: `test_swing.py` (and the other three) model a new
project with an **empty name**, and none of them runs the firmware's save request. The
firmware's new project is "New Project" with an empty path, and Save As changes name
and path in one go.

### Found in passing (not in scope)

- **synths: Save As over an existing project loads the old folder's `synths.bin`.**
  Emulator: B had WAVE choice 2, and A, with choice 1, was saved as B; afterwards the
  choice was 2. The firmware doesn't delete the folder's other files (FINDINGS 25), and
  synths' rule "a file there wins" then applies the overwritten project's choice to the
  new content. slicethru, with the same setup, keeps A's flags. A follow-up for synths,
  not part of this fix.
- The four don't wait for the file manager to be idle before they read or write;
  slicethru does. That matters right after Save As (section 3.1, point 5).

## 2. The two cases the fix has to cover

1. **A new project's first save.** The new project is "New Project" with an empty path
   (`0x60117c8c`, at boot too when Workspace can't load). Its first save is a Save As
   through `0x60114e34`, from the project screen's Save As `0x600e9a78`.
2. **Save As of a saved project**, including over an existing project's folder
   (overwrite `0x600eaa58`). That folder may already hold an old `<mod>.bin`, and the
   saved project's settings must win.

Both pass through `bl strncpy(fm + 0x340, name, 0x21)` at **`0x60114eaa`**. Only Save As
runs it (a plain Save has no path and no name), after the new path has been copied. The
project screen's own update method isn't hooked, so the hook only marks the Save As.
The next Pattern or Config update does the file work.

## 3. The fix

### 3.1 Per patch (swing, humanize, interpolate, masterfx): slicethru bt4's recipe

1. **`project_unsaved()`**, as in `patch/slicethru/slicethru.c`: no name, or "New
   Project" with an empty path.
   - Use it for `unnamed` in `settings_tick` and as the guard in `settings_path`.
   - This alone fixes the new-project case: the existing `keep = X->unnamed` path then
     works as intended. It also stops the stray `/New Project/<mod>.bin` attempts.
2. **Session hash limits: 32 name characters and 254 path characters**, as slicethru
   uses. The four now hash up to 64 and 255.
   - When the hook runs, the strncpy (n = 0x21) has copied the name, but the
     firmware hasn't written its terminating 0 yet. Hashing past 33 bytes would read
     leftover bytes, so a long name's hash at the hook wouldn't match the next tick's
     hash.
   - With 255 path characters, a path with no 0 runs into the name field.
3. **Chained hook `mt_XX_save_name(dst, src, n)` on `0x60114eaa`**: call through the chain
   word, then `X->save_as = project_session()`.
   - The `patch.json` branch entry is the same as slicethru's: same `old` bytes, plus
     `"next"`.
   - Five patches then chain one site; the patcher and `test_layout.py` already handle
     shared chained sites.
4. **`settings_tick` on a session change:** `save_as = session == X->save_as`; clear
   `X->save_as`.
   - On a Save As, don't load the folder's file. Keep the values and set
     `saved_valid = 0`, so they are written to the new folder after `SAVE_DELAY`.
   - Today the four try `settings_load()` first even when they keep. Over an existing
     folder that would apply the old project's file, which is the synths bug above.
5. **Wait for the file manager to be idle** (`file_manager_idle()`, slicethru's: state 0
   or `0x0c`, and `(+0x13f & 0xfd) == 0`) before any read or write.
   - The session bookkeeping can run at once.
   - After the request the save is still running (state `0x0d` in the emulator). A
     write into the new folder could come before the firmware has made the folder, or
     land between the firmware's own file steps.
6. **A new state magic** (the RAM layout changes). It works as slicethru's "MTB5" did:
   RAM survives a firmware update, so a stale `save_as` from a warm restart can't match.

**RAM: no block grows.** The `.mt_ram` blocks are rounded to 16 bytes (`link.ld`), and
`ocram_tail` and `dtcm_above_heap` are both 100 % full in `patch/all`, so this mattered.
Adding a 4-byte `save_as` field:

| Patch | Now (nm) | +4 bytes | RAM block (unchanged) |
|---|---|---|---|
| swing | 20 | 24 | 32 |
| humanize | 24 | 28 | 32 |
| interpolate | 1492 | 1496 | 1504 |
| masterfx (all its parts) | 804 | 808 | 816 |

The build's `.rel` `ram_size` must stay as it is; the plan checks this after each build.

### 3.2 What can be shared, and what can't

- **No shared code at run time.** Any mix of patches can be selected in the web patcher,
  so no patch may depend on another's code or RAM. A "Save As notifier" patch that the
  others read would break every mix without it. Each patch carries its own copy (about
  25 lines: `project_unsaved`, the hook, the tick change, `file_manager_idle`), as they
  already each carry their own `settings_load/save`.
- **One site, one chain.** All four (and slicethru) hook the same `bl` with `"next"`.
  Each calls the next hook first and only stores its own mark, so the order in the chain
  doesn't matter. In `patch/all` the order is the include order: colorsong (if fixed),
  humanize, interpolate, masterfx, swing, ..., slicethru.
- **Shared in source:** the four edits are the same text with a different prefix.
  Writing one and repeating it is quick, and reviewing one diff covers all four.
- **Shared in the tests:** one Save As helper in `tools/emu/` (the firmware's save
  request with a path and name, then busy and idle ticks), lifted from
  `test_slicethru.py`'s `save()`. Each of the four tests and `test_slicethru.py` can use
  it. Changing `tools/emu/` empties the cache anyway, so in a batch this costs nothing
  extra.

### 3.3 Per-mod differences

| | swing | humanize | interpolate | masterfx |
|---|---|---|---|---|
| File, size | `HSW1`, 12 B | `HHZ2`, 16 B (reads `HHZ1`) | `HIP1`, 12 B | `HMF1`, 12 B |
| Values | on, amount | 5 values | MIDI CC, Synths | reverb, delay |
| Written | 25 updates after a change | same | same | same |
| I/O from | Pattern + Config update | same | same | same |
| Notes | — | — | — | settings aren't at the start of its RAM (tests use `ram_map`); reverb and delay buffers aren't touched by this |

All four read and write one file of 16 bytes or less in a single call, so they are well
inside "at most 256 bytes per piece" and need no changes there. The file formats don't
change, so the files of older versions still load.

### 3.4 colorsong (optional, cosmetic)

The same idea, with no new RAM. `ocram_tail` is full (colorsong 12208 + humanize 32 +
swing 32 + legato 16 = 12288), and colorsong's block is exactly 12208, so even 4 bytes
would push a patch into another region.

- Instead the hook sets `FP->session` to the new session at once, so `mt_song_enter`
  doesn't reset the colours, and marks them to be saved, so the next Song visit writes
  `colorsong.bin` to the new folder.
- `colors_path` gets the `project_unsaved` guard.
- To check while building it: the save mark (`saved_sum`, `PERSIST_DIRTY`).

Recommendation: **leave colorsong out of this batch** unless you want it. Its symptom
is cosmetic, and its fix is a different shape from the other four. Its tests (`test_rescan.py`)
need two real project folders and aren't in `run_tests.py`. If you want it in, it adds
one more patch to the same release run (section 5) and step 8 to the checklist.

## 4. Version texts (at most 3 characters after `1.2.0-`)

| Build | Now | New | Checked unused |
|---|---|---|---|
| swing | sw1 | **sw2** | not in git log, docs or `build/` |
| humanize | hz8 | **hz9** | same |
| interpolate | ip5 | **ip6** | same |
| masterfx | mf4 | **mf5** | same |
| colorsong (optional) | c11 | **c13** | `c12` exists as an old local build file in `build/`, so skip it |
| patch/all | MT5 | **MTS** ("S" for Save As) | `MT6` and `MT7` were hardware-tested bundles on 2026-10-18/19; `MT8`–`MTY` (several) exist as old local builds. `MTS` is in none of them. Its "T" isn't a hex digit, so it can't clash with the web patcher's mix texts (a letter + two hex digits) |

## 5. Building and testing: batched versus one at a time

### Measured numbers (no guesses)

From `build/test_cache.json` (each test's time, smoothed, measured with every core busy)
and the runner's own logs in `build/`:

| What | Measured |
|---|---|
| **release run after a `tools/emu/` change** (whole cache invalid; 141 jobs, 0 cached) | **105.4 min** (`release_bt5.log`, 2026-10-25; the runner estimated 56 + 60 min) |
| release run after a patch-only change, quick tier already passed | 68.5 min (`release_bt4.log`) |
| release run after a docs / tested-hashes change only | 3.0–3.1 min (`release_bt4_hw.log`, `release_bt5_tested.log`) |
| quick tier, empty cache (27 jobs) | 7.6 min (`quick_est.log`; estimated 10) |
| synths full on `patch/all` (forced whenever `tools/emu/` changes: its "own files" key includes the emulator digest) | 2740 s ≈ 46 min, the longest job |
| web harness, all selections | 800 s alone (`MEASURED_SECONDS`), 1122 s under load (cache) |
| single tests (cache) | swing 21 s, humanize 7 s, interpolate 29 s, masterfx quick 17 s, slicethru 71 s |

### Why each separate round costs a full release run

Each fix changes its patch's test in `tools/emu/` (the Save As checks above). The cache
key of every job includes a digest of all of `tools/emu/`, so each change reruns
**everything**, the full synths and masterfx runs on `patch/all` included. That's
`release_bt5`'s case: 105 min.

### The web harness's per-selection cache

A selection is redone when a patch it contains changed. Its key doesn't depend on
`tools/emu/`.

| | Selections redone (of 4095) |
|---|---|
| one patch changed (each separate round) | 2048 |
| four rounds in all | 8192 (2.0 × a full harness) |
| the four at once | 3840 (0.94 ×) |
| the four + colorsong at once | 3968 (0.97 ×) |

The harness runs alongside the 46-min synths job, so it's off the critical path either
way. Batching mostly saves CPU here, not wall time.

### The cover set and the bundles in other orders

With `tools/emu/` changed, all 113 cover-set jobs rerun in either plan, because their
keys change. In a batch they run once instead of four times.

### Totals

| Plan | Test runs | Hardware sessions | Commits |
|---|---|---|---|
| **A. one at a time** (4 rounds: build, quick, release, hardware, commit) | 4 × ~105 min ≈ **7 h** (+ 4 × 3 min after each hardware pass) | 4, each with that patch alone and a new `patch/all` | 4, and four `patch/all` versions on the way |
| **B. batched** (your proposal) | single tests while coding (seconds), one quick tier 7.6 min (its results are reused by the release run), one release `--update` ≈ **105 min**, one 3-min run after the hardware pass: **≈ 1 h 55 min** | 1, with 4 single firmwares + `MTS` | your choice: one, or one per patch plus one for `patch/all` (the testing is shared, so splitting commits costs nothing) |

**Saving: about 5 hours of test runs and three hardware sessions.**

**Batching risk, honestly:** if one patch fails in the release run, the batch waits for
that patch. A code-only fix (tests unchanged) reruns roughly the `release_bt4` case
(≤ 68.5 min). A fix that also changes a test reruns the full 105 min. The four edits are
one recipe that hardware has already proven on slicethru, so the chance of that is low.
And the cheap stages (single tests, quick tier) catch most problems before the long run.

Recommendation: **plan B**, with colorsong left out unless you ask for it.

## 6. Order of work (plan B)

1. **swing first**, the smallest. Make the recipe change (section 3.1). Add the shared
   Save As helper in `tools/emu/` and the new checks to `test_swing.py`:
   - a new project as the firmware makes it: nothing read or written;
   - its first save keeps the values and writes them to the new folder;
   - Save As from a saved project keeps them, writes the new folder and leaves the old
     file;
   - Save As over a folder that has its own file: the saved project wins;
   - a plain Save writes nothing;
   - reopening the old project gives its own values;
   - a Save As to a 32-character name;
   - the file manager busy: no I/O until idle.

   Then build it alone and in `patch/all` and run `test_swing.py` on both (seconds).
2. **humanize, interpolate and masterfx**: the same edit and the same checks; single
   tests on each alone and on `patch/all`. Check that each `.rel` `ram_size` is
   unchanged.
3. `test_slicethru.py` uses the shared helper; no slicethru code change.
4. **Quick tier** (7.6 min measured).
5. **Release tier with `--update`**: about 105 min measured, so **I ask you first** (over
   45 min). It rewrites `web/expected.json` in the same run.
6. Firmwares to you: `TrackerPlus_sw2.ptf`, `_hz9`, `_ip6`, `_mf5` and `TrackerPlus_MTS.ptf`,
   with SHA-256s, and the checklist below.
7. After your hardware pass:
   - READMEs: the Save As line and the new hook in each hooks table;
   - `docs/PATCHING.md` table: new rows, old rows become "the previous ...";
   - FINDINGS section 25: all mods handle Save As;
   - `web/manifest.json` tested builds;
   - then the 3-min release rerun;
   - commit only when you say.
8. Follow-ups to offer: synths' Save As over an existing project, and colorsong if
   left out.

## 7. Combined hardware checklist

**`TrackerPlus_MTS.ptf` (everything, about 15 min).** Settings to use:
- swing On, 66;
- a humanize value changed;
- interpolate MIDI CC On;
- a masterfx reverb and delay;
- a Beat Slice Thru instrument (control);
- a WAVE synth choice (control).

1. Config shows `1.2.0-MTS`.
2. Open a saved project, change all of the settings above, go to the Pattern screen and
   wait about 5 s (the files are written a moment after a change).
3. **Save As** under a new name, then back to Pattern, wait 5 s. Config → Project
   Settings: every value as set; the instrument still plays through.
4. Open the original project: its own values (they weren't changed by the copy).
5. Open the copy again: the values are still there (they came from its own files). If
   you want to look on the card, the copy's folder should now have `swing.bin`,
   `humanize.bin`, `interpolate.bin`, `masterfx.bin`, `slicethru.bin` and `synths.bin`.
6. **New project:** it starts at the defaults (nothing carried over from the previous
   project). Change the settings, first save under a name, then open another project and
   reopen this one: the values are kept.
7. **Save As over an existing project:** the values are the ones of the project you
   saved, not the overwritten one's. Exception, known and not fixed here: the synth
   choice comes from the overwritten project.
8. (Only if colorsong is in the batch.) Edit a pattern after visiting the Song screen,
   Save As: the Song screen's colours stay as they were.
9. Quick regression: swing is audible, the reverb works, humanize and interpolate act
   while playing.

**Each single firmware (`sw2`, `hz9`, `ip6`, `mf5`), about 3 min each.** Config shows
its version, then steps 2–6 for that mod's own setting only. If time is short, `MTS`
covers every hook in the chained case. The singles are the case where the next link in
the chain is stock `strncpy`, so test at least one of them; testing all four is what
gives each its "tested" row in `PATCHING.md`.
