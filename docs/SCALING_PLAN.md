# Plan: more mods with less testing

Status: Phase 1a, 1b and 1c are built and committed (test tooling only), and so are the names for mod 15 and beyond (Phase 3); 1d steps 1 to 3 are done (the deep tier timed at 3 h 55 min on 6 cores); 1d step 4 is planned; Phase 2 (2026-11-02: the page checks every download, the harness builds in memory) is built and passed a release run (43.2 min, was 118.6), not committed; the rest is plan only.

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
- Solved in Phase 3 below: mixes with a 15th mod on show `1.2-` plus 5 characters.

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

**1c. The harness disk change, already planned by the Beat Slice session.** Done (2026-11-01): `index.json` in `tools/test_web_patcher.py`, `rebuild_mixes` in `tools/run_tests.py`.
- The harness keeps each mix's `.map.json` and an index entry (the `.ptf`'s SHA-256, where its image ends, its spec) instead of each `.ptf`.
- The test runner picks the cover set from those and rebuilds only its 23 mixes, into `harness/cover/` as `mix-<version text>.ptf`. A rebuild that isn't byte-identical to what the harness checked is a failure, and its tests don't run.
- Nothing is checked less: byte identity and the layout checks still run on every mix before its `.ptf` is deleted.
- What you lose: browsing any mix's `.ptf` without building it first (`patch_ptf.py` with its spec from `harness/site/`).
- **File names no longer grow with the number of mods** (2026-11-01). The harness used to name each mix's files after its mods (`sel-humanize+interpolate+…`), which put its longest path at 247 of Windows' 260 characters with 14 mods, so one or two more mods would have broken it. Now a selection's files are named by its spec's folder (`sel-swing`, `sel-all`, `sel-mix-1AU`), the runner's rebuilt cover mixes likewise (`mix-1AU.ptf`), and the harness fails if any file it writes is more than 80 characters below its output folder.

Together, 1a and 1b should bring a typical one-mod release run back near your 60-minute goal. The web harness's doubling stays, and that's what Phase 2 is for.

**1d. A faster deep tier.** Plan written 2026-11-01; each step is timed before the next one starts.

The deep tier runs only when masterfx's or synths' audio code changes, not for every new patch. It runs every test in full mode on every build, cover mixes included, and takes nothing from the cache: it's the check on anything the cache keys might miss, so it keeps doing that (decided 2026-11-01).

Where its time goes, from the runner's own per-test times (not a timed deep run):

| Test, full mode | Share of the deep tier's CPU time |
|---|---|
| synths | 39 % |
| resample | 19 % |
| PHASE | 14 % |
| test_bundle_cost.py | 8 % |
| masterfx | 8 % |
| everything else | 12 % |

The runner's formula puts the whole tier at about 12.0 h with `--jobs 2`, and about 4.5 h with `--jobs 7`. Timed with step 3 in: 3 h 55 min with `--jobs 6` (below).

The steps, cheapest first:
1. **Run it overnight on every core.** No code change: the default `--jobs` is one less than the number of cores (7 here), and the daytime limit of 2 doesn't apply overnight. Done means a timed overnight run, which replaces the 4.5 h estimate above.
   - **Timed (2026-11-01):** 234.6 min with `--jobs 6`, step 3 in. See "Timed for 1d: the whole deep tier" below.
2. **Time each long test by section.** One full run each of synths, resample, PHASE, test_bundle_cost.py and masterfx on patch/all, with each section timed, so the next two steps go where the time is.
3. **Share the stock firmware's runs within one deep run.** Several sections run stock firmware with the same inputs for every build, for example synths' stock-output comparison (`run_pair`). Those results are identical from build to build, so a deep run could work them out once and reuse them for each build.
   - They'd be kept only for that one run, so the deep tier still takes nothing from before it started.
   - Before relying on it, a check that the shared results equal fresh ones, value for value, on two different builds.
   - Worth doing only if step 2 shows those runs are a real share of the time.
   - **Built (2026-11-01):** `tools/emu/stock_share.py`. Synths' `run_pair` stock half, synths' and PHASE's stock voice allocation. `run_tests.py` empties `<out>/stock_share` at the start of each run; the first job to need a figure works it out, jobs needing it at the same moment wait on an OS file lock (released if a job dies), later ones read it. Checked with `MT_STOCK_SHARE_VERIFY=1` on patch/all and synths alone, all four full runs at once: every shared figure (6 output comparisons, 5 allocations) equal to a fresh one, 0 failures. Timed below.
4. **Profile the emulator on the slowest section** (Python's own profiler, nothing to install). Take only changes to the test code or the emulator that leave every result exactly as it was. Checked by comparing each changed section's outputs before and after, value for value.

Not planned:
- **Splitting long tests into parallel parts.** The runner already starts the longest jobs first, and no single job (a full synths run is about 22–30 min) comes near a deep run's length, so the parts wouldn't shorten it.
- **Letting the deep tier use the cache.** That would make it a second release tier and remove the one run that checks what the cache keys might miss.

### Phase 2: check every download in the browser, and build the harness's mixes in memory

Decided 2026-11-02, replacing "fixed slots" (dropped, see below). Every mix keeps today's bytes, RAM layout and performance.

**Why the harness doubles.** The page builds the firmware in the browser and compares its hash with the one `patch_ptf.py` got for that exact mix (`web/expected.json`). To have those hashes, the harness builds every mix twice, in Python and in JavaScript: 16,383 mixes × 2 at 14 mods, and twice that for each new mod.

**Where its time goes** (profiled 2026-11-02, 40 mixes on one core, nothing else running):

| Step | Python (`patch_ptf.py` + `test_layout.verify`) | JavaScript (`patcher.js` under Node) |
|---|---|---|
| Re-reading the 6 MB output it just wrote | 958 ms | ~100 ms (36 %) |
| Encoding the 6 MB `.ptf` text | 378 ms | ~55 ms (19 %) |
| Building and checking | ~90 ms | ~45 ms |
| The rest (memory, hashing, files) | ~66 ms | ~73 ms |
| **Per mix** | **1,492 ms** | **273 ms** |

A prototype of the Python side in memory (stock record lines encoded once, the output's image kept instead of re-read) took 161 ms per mix, and all 40 mixes came out with the SHA-256 the harness had recorded.

**The three parts:**
1. **In-memory harness (option 2).** Both sides build each mix without re-reading their own output, and encode the unchanged stock records once. The full checks (the real encoder, the read-back) still run on a fixed sample of mixes in every run, and `run_tests.py` already rebuilds the cover mixes with the real `patch_ptf.py` and checks their SHA-256. Nothing users get changes.
2. **A byte-by-byte check on the page (option 1).** `web/verify.js`, separate from the builder, reads the finished `.ptf` and accounts for every byte: the stock bytes, each hook site, each chain and link word, each mod's code with its relocation removed, each RAM block (in a region the mod may use, aligned, not overlapping another), the ITCM stubs and copy table entry, the version text and the padding; any byte no rule explains is a failure. It decodes branches and hex itself rather than reusing the builder's encoder. The page runs it on every download and refuses one that fails. It is `test_layout.verify`'s checks, ported, plus the stock-byte and padding coverage those didn't have.
   - The harness runs it on every mix and every reordered bundle, which it must accept, and on deliberately broken firmware it must reject: `tools/verify_corrupt.mjs` breaks each mod alone, patch/all and two mixes in 13 to 17 ways each (a stock byte, a hook put back, a byte of a mod's code, a gap byte, a relocated word, RAM moved out of its region or onto another mod's, a chain word, the version text, padding added or cut, the copy table, an ITCM stub, a bad checksum, lowercase hex, a missing record, LF line endings), 242 builds in all. Its copy of the layout rules is checked equal to `patch_ptf.py`'s.
   - What it allows: a RAM block anywhere free (in a region the mod may use, aligned, not overlapping), not only where the builder would put it. Any such placement is safe; the builders' exact choice is what the hash comparison checks.
   - The page also keeps the stored-hash check for every mix the harness compared, and patch/all and the single-mod builds keep theirs against the hardware-tested SHA-256s.
3. **Compare the two builders as widely as a set number of mixes allows.** Every mix while there are at most `MAX_COMPARE` (32,767: up to 15 mods). Beyond that (`compared()` in `test_web_patcher.py`): every mix of one to three mods (so every pair and triple of mods sharing a hook site), every mix of all mods but up to two, then an even spread of the rest up to `MAX_COMPARE`, the same ones each run. Only compared mixes' hashes go into `expected.json`; for the others the page says the every-byte check passed, and not that they match the Python builder. `web/stage.py` publishes an `expected.json` holding fewer mixes only if each mod alone and patch/all are among them.

**Timed (2026-11-02, `--jobs 6`, 6 cores):** the release run that brought it in took 43.2 min, 256 jobs, 0 failed (the one before, with the old harness redoing every mix, 118.6 min). The harness redid all 16,383 mixes in 30.0 min, about 0.11 s a mix; 280 of them were built in full, and 241 broken builds were all rejected. `web/expected.json` came out unchanged. The cover stage took 11.6 min (42 quick-mode jobs whose last pass was in full mode, in the deep run).

**What it gives** (harness only, `--jobs 6`, from that run: 0.11 s a mix compared, ~0.064 s a mix the page's check alone covers; a one-patch change redoes the half of the mixes that contain it):

| Mods | Before | Phase 2, one-patch change | Phase 2, every mix |
|---|---|---|---|
| 14 | ~59 min | ~15 min | 30 min (measured) |
| 15 | ~2 h | ~30 min | ~60 min |
| 16 | ~4 h | ~45 min | ~1.6 h |
| 20 | ~2.5 days | ~9 h | ~19 h |
| 25 | months | ~12 days | ~25 days |

`MAX_COMPARE` caps only the Python side. Past 15 mods the harness still builds every mix in JavaScript and runs the page's check on it (the cover set is chosen from every mix's map), so that part still doubles with each mod. Capping it too needs the cover set chosen without building every mix (from each mod's size and RAM, which decide where everything lands) and the harness checking a sample past a second budget, with the page's check still on every download. Not built; it matters from about 17 mods.

Every mix, at every size, also passes the page's check before it can be downloaded. A bad firmware gets through only if the JavaScript builder has a bug in a mix the harness didn't compare and the every-byte check misses it; more likely such a bug shows as a refused download. The pinhole reflash with the stock `.ptf` recovers a unit either way (FINDINGS section 3).

**Fixed slots, dropped (2026-11-02).** Every mix would have been patch/all with the unticked mods switched off. Checked against all 16,369 multi-mod mixes, it would move RAM: for the audio blocks of synths and masterfx, 1,024 mixes faster, 3,444 slower, 5,118 some of each, 2,700 unchanged, by amounts the emulator can't measure (it doesn't model memory waits). It would also have changed the firmware writer itself, frozen the mod order, and made every mix patch/all's size. The checks above stop the doubling without any of that.

**A related idea, not planned:** RAM handed out by how often it's used (synths' and masterfx's per-sample state to DTCM first, colorsong's colors to SDRAM). Today it's first come, first served in the listed order, so a mix without colorsong puts masterfx's main block in OCRAM rather than DTCM. It would change patch/all's bytes, so it needs a hardware test.

### Phase 3: names for mod 15 and beyond

Done (2026-11-01), ahead of any new mod, so names never limit how many mods there are.
- **Existing names don't change.** Every mix of the current 14 mods keeps its 3-character name after `1.2.0-`, so every current build is byte-identical (`web/expected.json` unchanged).
- **A mix with a 15th mod or later on** (bit 14 and up) shows `1.2-` plus 5 characters: the mix's bits in base 36, capitals and digits only (`mixVersion` in `web/patcher.js`). Still 9 characters in Config, unique by construction up to 25 mods, and never the same length as a 3-character name.
- **Why it's safe:** the patch already writes the whole format string, so this is a text change. The firmware writes the version text once at boot (`0x600cbe88`) and only Config's label and its "Current version" item display it; nothing parses it.
- **Tests:** the harness checks the 5-character names for 516 masks with bit 14 to 24 (each decodes back to its mask, so all differ) and builds a reordered bundle with a 5-character version (`long-version`), whose image reads `1.2-K7Q2M`. `test_layout.py` checks the format matches the version's length.
- **The page** shows the same text: its version reading (`versionTextOf`), the selection and "all" labels and its already-patched check all handle both forms. The first run of the new check caught `versionTextOf` turning only `%d.%d.%d` into numbers.

### An alternative to Phase 2 I'm not recommending: switches instead of mixes

One firmware with every mod, each switched on or off in Config. The web patcher would offer single builds and that one build. That ends both the naming and the mix testing for good.

I'm not recommending it now:
- Every mod needs a new tested "off" path, which means hardware re-testing everything.
- Switches that survive restarts need a new settings file, with the Save As care that settings files have needed before.

That's far more risk than Phase 2, which changes only the test tools and the page's check.

## What I'd do, in order

1. **1a (per-test cache keys) and 1b (resample's quick mode first).** One session, timed before and after. A release run checks it. Nothing users get changes.
2. **1c, the harness disk change.** Done.
   Then 1d, the deep tier's steps, one at a time, with the timed overnight run last.
3. **Phase 2.** The in-memory harness, the page's every-byte check and the compare limit, in one change with one release run (`--update`).
4. **Chords joins** patch/all and the web patcher, using the bit-14 names.
5. **Names for mod 15 and beyond.** Done (Phase 3).
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

### Timed for 1c (2026-11-01)

- **Disk:** `build/testrun/harness` went from 84,475 MB to **394 MB**, and `build/testrun` from 84,931 MB to 850 MB (measured with `du`).
- **Release run that brought it in: 142.9 min, all 253 jobs passed.** Nearly all of that was the web harness redoing every one of its 16,383 selections, because its own code changed. That happens only when the harness's code changes; a change to one patch redoes only the selections that contain it.
  - The harness took 8,463 s (141 min) at `--jobs 2`, on 3 cores. The runner had estimated about 90 min, from its running average of earlier harness runs (3,606 s, then 1.5 times that for a busy machine). That average mixes full and partial reruns made at other `--jobs` settings, so it's a poor guide to a full rerun. The two full runs in the table above (3,130 s and 5,040 s) ran at settings I don't know.
  - All 171 cover-set jobs came from the cache: their keys are the files' SHA-256, so this shows the rebuilt mixes are byte-identical to the ones that passed before.
- **Choosing the cover set:** 5 s, down from 39 s, because each image's end now comes from the index. Same 23 mixes in the same order, checked against the committed code on the same files.
- **Fully cached release run, all 23 cover mixes rebuilt:** 1.0 min (0.9 min before).

### Timed for 1d step 2: where the long tests' time goes (2026-11-01)

One full run of each on `patch/all`, every section timed, with other runs on the machine (so read the shares, not the exact seconds):

| Test | Full run | Biggest sections |
|---|---|---|
| synths | 1,804 s | voice allocation 979 s (54 %), stress 194 s, stock output 193 s, swap 66 s |
| resample | 1,043 s | instrument 408 s, Record Tracker 242 s, Shift+Rec 154 s, engine on 99 s |
| PHASE | 801 s | engine 652 s, of which level 391 s; swap 58 s |
| masterfx | 344 s | reverb 173 s, delay 72 s, shimmer 47 s |
| test_bundle_cost.py | 118 s | (no sections; its stock side comes from its own stock cache file) |

**Stock firmware's share, the same for every build:**
- synths' voice allocation runs one note sequence on stock VAP and on the build, and compares which voice each note gets. Timed alone: stock 632 s, the build 244 s.
- synths' `script()` runs (stock output and the rig): stock 102 s, the build 84 s. The rig checks (stock only) are another 31 s.
- PHASE's voice allocation, inside its level checks: stock 263 s, the build 92 s.

So a full synths run spends about 765 s, and a full PHASE run about 263 s, on stock work that gives the same answer for every build. A deep run has 11 full runs of each (synths alone, patch/all, the 8 cover mixes that have synths, and one reordered bundle), so sharing that work within the run would save about 10 × (765 + 263) ≈ 10,300 s of CPU. That's roughly 18 % of the tier's CPU time by the runner's estimate, or about 35–40 min of a 4.5 h overnight run on 7 cores. Worked out from these timings, not timed as a deep run.

### Timed for 1d step 3: sharing stock results (2026-11-01)

Full synths and PHASE on patch/all, each run twice at the same moment (one computing everything, one reading a filled share folder), so the load was the same for both; `--jobs` doesn't apply, 4 processes on 6 cores:

| Test | Computing | Reading shared | Saved |
|---|---|---|---|
| synths | 1,493 s | 861 s | 632 s (42 %) |
| PHASE | 587 s | 341 s | 246 s (42 %) |

10 of a deep run's 11 full runs of each read instead of computing: about 10 × 878 ≈ 8,800 s of CPU saved per deep run (step 2's estimate was 10,300 s; the rig checks, 31 s, aren't shared). A job that waits for a figure being worked out is never slower than working it out itself. The deep run below timed the whole tier with sharing in.

### Timed for 1d: the whole deep tier (2026-11-01)

One deep run, `--jobs 6`, processor affinity 6 of the 8 cores, step 3 in, nothing from the cache: 256 jobs, 0 failed, **234.6 min** (3 h 55 min).

| Stage | Jobs | Runner's estimate | Took |
|---|---|---|---|
| Layout | 1 | 2 min | under 1 min |
| Each patch alone, patch/all, diagnostic builds, web harness | 81 | 142 min | 131 min |
| Cover set (23 mixes) and the 5 reordered bundles | 174 | 314 min | 103 min |

- The web harness (all 16,383 selections) was the last job of its stage to finish, so it set that stage's length.
- The runner's estimate for the cover-set stage was three times too long. Its per-job times predate step 3 and were taken under a different load.
- On 7 cores overnight it should take a little less. That hasn't been timed.

### Still estimates, not timed

- the 2.5–3 hour 15-mod harness run;
- the Phase 2 harness time (from the 2026-11-02 profile; timed when it lands).

Each step's session should time its own change and update this file.
