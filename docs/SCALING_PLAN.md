# Plan: more mods with less testing

Status: Phase 1a and 1b are built (test tooling only, not committed yet); the rest is plan only.

## The problem today (14 mods in the web patcher)

### Measured costs

| What | Time | Source |
|---|---|---|
| Web harness, every mix | 52–84 min | last two full runs: 3,130 s and 5,040 s |
| Quick tier after a test-code edit | 47.7 min | push check, 2026-11-01; 81 jobs, none cached |
| Release tier for rs9 (resample) | 130.9 min | `build/release_mr9b.txt`; 253 jobs, 78 cached |

### Why it's slow

**1. Any test edit throws away the whole cache.**
- A job's cache key includes a digest of every file under `tools/emu/`.
- So adding `test_chords.py` or editing `test_resample.py` reruns every test on every build, including the full synths (30 min) and PHASE (11 min) runs.
- That is the whole reason the push check took 47.7 min.

**2. Heavy tests run in full on every cover-set mix.**

The cover set is 23 mixes. Per release run:

| Test | Seconds per run | Cover mixes | Total |
|---|---|---|---|
| resample | 687 | 8 | about 92 min of CPU |
| legato | 190 | 5 | 16 min |
| tracklen | 86 | 9 | 13 min |

- Synths, PHASE and masterfx already have a `--quick` mode for mixes.
- Resample, legato, tracklen and chords don't.
- At `--jobs 2`, resample alone is roughly 46 of the cover set's ~80 minutes. That's worked out from the per-run times, not timed separately.

**3. The web harness doubles with every mod.**
- It builds every mix twice, once with each patcher, which is 2^N builds.
- Each mod also adds a hash per mix to `web/expected.json`, which the page checks before download.
- At 16 mods that's 65,535 mixes; at 20 it's over a million. So the whole-image list can't go on growing, however fast the tools get.

**4. The names run out.**
- Config shows `1.2.0-` plus at most 3 characters.
- 3 characters from A–Z and 0–9 give 46,656 names, enough for 15 mods (32,767 mixes) but not 16.

### Another limit I found while checking: RAM

In `patch/all` (MR9), the two RAM regions that are ready at startup are completely full:
- `ocram_tail` ends at `0x20208000`.
- `dtcm_above_heap` ends at `0x20053000`.

What's left:

| Region | Free | Note |
|---|---|---|
| `dtcm_after_audio` | 832 B | ready at startup |
| `sdram_top` | about 14.7 KB | after boot only, cached |
| ITCM tail | about 1.1 KB | |

So a future mod that needs a lot of state at startup won't fit as things are, whatever the naming or testing. Chords needs no RAM, so it isn't affected. Also still unknown: the bootloader's largest accepted image (FINDINGS section 3). patch/all is about 2.3 MB of code past stock and boots fine.

## The plan

### Phase 1: cheap test fixes (no firmware changes, no hardware testing)

These help whatever happens later, and none of them changes a byte of any firmware.

**1a. Cache keys per test.** Done (2026-11-01): `imported_files` and `READS` in `tools/run_tests.py`.
- **Change:** a job's key would cover the files it actually uses: the firmware, its own test file, every `tools/emu` module it imports (worked out from its imports), `patch_ptf.py` and the stock firmware.
- **Effect:**
  - Editing `test_resample.py` reruns only resample's jobs.
  - A change to a shared module such as `machine.py` or `fakefs.py` still reruns everything that imports it.
  - The push check would have run only the chords and resample jobs, not all 81.
- **Risk:** a missed dependency would keep a stale pass. The guard against that:
  - The deep tier and `--no-cache` ignore the cache.
  - A test that loads a file without importing it would list that file by hand.
- **This reverses a written rule.** CLAUDE.md says "A change to `tools/emu/` invalidates everything, as it should." That rule would be reworded: a change to shared emulator code still reruns every test that uses it, while a change to one test reruns only that test.

**1b. A quick mode for each long test, used on cover-set mixes.** Done for resample, legato and tracklen (2026-11-01): `MIX_QUICK` in `tools/run_tests.py`. Chords not yet: it isn't in the web patcher, so it runs on no cover mix and a quick mode would save nothing until it joins.
- **The rule:** any test over about a minute gets a `--quick` mode, as synths, PHASE and masterfx have.
  - Quick mode runs the checks that depend on the mix: its RAM block where it landed, each hook reached through the chain, and one short play-through.
  - The full test still runs on the mod alone and on patch/all, and in the deep tier.
- **Applies to:** resample first (the big one), then legato, chords and tracklen.
- **Estimated saving:** 40–50 minutes off a release run that has to redo the cover set. That's an estimate from the per-run times above, so the first step is to time it.

**1c. The harness disk change, already planned by the Beat Slice session.**
- The harness keeps a fingerprint per mix instead of each `.ptf`.
- `build/testrun` drops from about 21 GB to about 190 MB.
- This is disk only, not time. The plan is written and waits on your go.

Together, 1a and 1b should bring a typical one-mod release run back near your 60-minute goal. The web harness's doubling stays, and that's what Phase 2 is for.

### Phase 2: stop the doubling with "fixed slots" (before chords joins the web patcher)

**The idea:** patch/all's layout becomes the layout for every mix. A mix is the All image with the unticked mods taken out:
- their code and RAM stay unused, at the same addresses;
- their hook words go back to stock;
- each shared hook chain skips them.

**Why it suits your risk tolerance:**
- **Nothing hardware-tested changes.**
  - patch/all is built exactly as now, so its bytes are identical.
  - Single-mod builds keep using each mod's own spec, so they're unchanged too.
  - Only multi-mod mixes change bytes, and no current mix has been hardware-tested as a whole. The tested-builds list's older All bundles were built from older mod versions, so they no longer match any mix the page makes.
  - The tools would prove this: a hard check that All and every single build are byte-identical before and after.
- **The page's check stays complete.** It would no longer look up one hash per mix. It would check every byte of what it built:
  - the stock regions, against stock;
  - each mod's slot, against one hash per mod;
  - each hook and chain word, against a small table per shared site.

  Every byte is covered, so this is as strict as today's check, not a sample. It works because slots can't overlap, which is checked once for the whole layout.
- **Testing becomes linear.**

  | Check | Today | With fixed slots |
  |---|---|---|
  | Web harness | 2^N builds | each mod's slot once, plus each shared site's chain combinations |
  | Cover set | as now | smaller |

  The busiest site has about 5 mods, so 32 chain variants.

  The cover set shrinks because a mod's RAM is always in one place:
  - The "RAM block at a region's start, middle and end" cases go away.
  - The "padded or not" case goes away, since every mix has the All image's size.
  - What remains is each pair of neighbours on a shared hook chain, plus mixes that leave mods out of a chain.
- **Adding a mod stays cheap:**
  - The new mod goes last in patch/all, so every existing mod keeps its address.
  - Testing a new mod means its own tests, its slot, the chains it joins, and the cover mixes it's in. It doesn't mean rebuilding everything.

**Costs and catches:**
- **It's a sizeable change:** `patch_ptf.py`, `patcher.js`, `test_web_patcher.py`, `test_layout.py`, `stage.py` and the format of `expected.json`.
- **Mixes download larger.** A mix is always the size of patch/all, so it flashes a little slower.
- **The mod order in patch/all is frozen.** Reordering would move every slot.

**Recommended order:**
1. A design session writes the detailed design, including the byte-check format, and times a prototype.
2. Then it gets built, with an overnight deep run.
3. Then chords joins as mod 15 under the new scheme.

Chords stays usable on its own meanwhile.

**Why not add chords first under today's scheme:**
- The harness would go to 32,767 mixes × 2 builds, about 2.5–3 hours once (an estimate).
- After that, every one-mod change at 15 mods would rebuild about 16,000 mixes (roughly 80 min).
- That's paid again and again until Phase 2 lands.

### Phase 3: names for mod 16 and beyond

**Up to 15 mods**, the current names all stay. Bit 14 (chords) uses the second character `G`–`V` instead of `0`–`F`, the way bit 13 already uses the last character. Colorsong + chords would be `1.2.0-MG1`.

**From mod 16, no 3-character name can be unique.** The options:

1. **A shorter prefix for new mixes (recommended, if a check passes).**
   - **How:** mixes with mod 15 or later show `1.2-` plus 5 characters, encoding the mix in base 36. That's room for 25 mods.
   - **What keeps its name:** existing names, single builds and patch/all keep `1.2.0-`.
   - **What it needs:** the patch already supplies the whole format string, so this is a text change. Before deciding, it needs an emulator check:
     - that nothing reads the version text besides Config's display (a memory watch on `0x200205d4` through boot, Config, save and load);
     - that 9 wide characters (like `1.2-WWWWW`) still show, rendered with `eve.py`.
2. **Mod packs.** Related small mods share one checkbox (for example a "sequencer extras" pack), so the web patcher stays at 15 or fewer. No firmware or naming change, but less choice for users.
3. **A short build code that isn't unique**, with the full mod list in the file name and on the page. I don't recommend it: two different firmwares could show the same text in Config, which makes bug reports ambiguous.

### An alternative to Phase 2 I'm not recommending: switches instead of mixes

One firmware with every mod, each switched on or off in Config. The web patcher would offer single builds and that one build. That ends both the naming and the mix testing for good.

I'm not recommending it now:
- Every mod needs a new tested "off" path, which means hardware re-testing everything.
- Switches that survive restarts need a new settings file, with the Save As care that settings files have needed before.

That's far more risk than Phase 2, which changes only the build tools.

## What I'd do, in order

1. **1a (per-test cache keys) and 1b (resample's quick mode first).** One session, timed before and after. A release run checks it. Nothing users get changes.
2. **1c, the harness disk change**, when you say go.
3. **Phase 2.** A design session first, plan only, with a timed prototype; then build, with an overnight deep run.
4. **Chords joins** patch/all and the web patcher, using the bit-14 names.
5. **Before mod 16:** the emulator check on the version text, then the name choice.
6. **Watch RAM.** Before planning a new mod, check what startup RAM it needs against the 832 B left.

## Numbers to confirm as each step lands

### Timed for 1a and 1b (2026-11-01, `--jobs 2`, processor affinity 3 cores)

**1a, per-test cache keys.**
- Edit only `test_resample.py`, then `--tier quick --dry-run`: 5 of 81 jobs to run (resample's 4, plus layout, which was due anyway after a `web/manifest.json` change). Before, the same edit reran all 81.
- A temporary one-line edit to `synth_rig.py`: 41 of 81 to run, every test that imports it (synths, PHASE, legato, chords, slicethru, resample, and test_bundle_cost.py through `make_headroom_project.py`) and nothing else.
- **Quick tier after test-only edits:**
  - Before: 47.7 min, 81 jobs, none cached (edits to chords' and resample's tests).
  - After: **22.7 min**, 81 jobs, 68 cached (edits to resample's, legato's and tracklen's tests).
  - Most of the 22.7 min is resample's full run on resample alone and on patch/all, about 12.5 min each.

**1b, quick modes on mixes.** Per run, timed by the runner under load:

| Test | Full | --quick | Cover-set jobs (23 mixes + 4 bundles) |
|---|---|---|---|
| resample | 687–757 s | 36 s | 9 |
| legato | 190–215 s | 50 s | 6 |
| tracklen | 86–104 s | 14 s | 10 |

- **The cover-set phase's jobs for those three tests:**
  - Before: about 8,200 s of CPU (9 × 687 + 6 × 190 + 10 × 86, the per-run times measured before the change), roughly 68 min at `--jobs 2`. That comes from the measured per-run times; the phase wasn't timed on its own.
  - After: **6.3 min**, timed: 25 jobs, 08:28–08:34.
- The diagnostic builds (all-headroom, all-tracklen-headroom) also take these tests' `--quick` mode now. In the quick tier that's 5 jobs, about 1,860 s of CPU less each time they rerun.

**Release tier after those edits: 22.1 min, all 253 jobs passed** (227 cached). Where the time went:
- the web harness: 4 min (its own cache redid only what changed);
- choosing the cover set: 11 min;
- the 25 cover-set jobs: 6.3 min.

The rs9 release run before this (a patch change, so not the same case) took 130.9 min. A second release run after a line-ending-only change to the legato and tracklen tests (their 8 quick-tier jobs and 16 cover-set jobs reran) took 19.4 min, all 253 passed.

**Choosing the cover set, fixed the same day.** It took about 11 min in those runs (508 s under a profiler):
- `data_end` read every one of the harness's 16,369 `.ptf` files in full (4.6 MB each) to find each image's end: 429 s;
- the greedy pick re-read every `map.json` in each of its 24 rounds: 62 s.

Now `data_end` reads only each file's last 64 KB (more when needed), and the patch counts are kept. That takes it to **39 s**, with the same 23 mixes in the same order. A release run with everything cached now takes 0.9 min.

### Still estimates, not timed

- the 2.5–3 hour 15-mod harness run;
- the Phase 2 harness time.

Each step's session should time its own change and update this file.
