# Copy to New with track lengths: build plan

Status: built. Step 1, tracklen tl8, is tested on hardware (alone and in patch/all
MLN). Step 2, copypattern cp5, is tested on hardware (alone, with tl8 as CTL, and in
patch/all MPL).

## The gap

Copy to New (patch/copypattern) writes the current pattern into the next empty one,
pattern file and all. The pattern's own length (the last-step byte of each track)
comes with it. The lengths set with Trk Len (patch/tracklen) don't: they live in
tracklen's RAM table (`len[pattern][track]`, 0 = "follows the pattern") and in
`tracklen.bin` in the project folder, which copypattern can't see. So today:

- the new pattern plays every track at the pattern's length;
- a target pattern that was given lengths before and later emptied keeps those old
  lengths, since copypattern's empty check only looks at steps.

## The way in: "links"

The patcher already joins two patches that work together: condtrig's word
`mt_ct_tl_len` gets tracklen's `mt_tl_len` (`"links"` in `patch.json`, filled by
`tools/patch_ptf.py` and `web/patcher.js` alike, 0 when tracklen isn't in the build,
in either order). Copy to New uses the same: tracklen gains one function, copypattern
gains a link word to it and calls it after a copy. No new patcher feature, no new
hook site, no run-time search.

## Step 1: tracklen tl8 (copypattern unchanged)

### Code (`patch/tracklen/tracklen.c`)

A new exported function, kept by the link script like the hooks (`.text.mt_*`):

```c
/* For copypattern ("links"): pattern `to` gets pattern `from`'s lengths. */
void mt_tl_copy_lengths(uint32_t from, uint32_t to);
```

- `from` and `to` must be 1..255 and differ; otherwise it does nothing.
- It copies the 16 lengths of `from` over those of `to`, zeros ("follows the
  pattern") included, so stale lengths in the target go. If any byte changed it sets
  `changed`, and the existing save logic writes `tracklen.bin` a moment later (or
  removes it when no length is left), as a Trk Len change does. A new project with no
  folder keeps them in RAM until its first save, as now.
- **Not loaded yet.** If the project's lengths aren't loaded yet (`load` pending, or
  `session` isn't the current project's), copying now would be overwritten by the load.
  Then the pair is kept as pending (from, to and the project's session) and applied
  right after `load_lengths` in `lengths_tick`, if the session still matches; a
  session change drops it. This is the case of pressing Copy to New right after
  opening a project. About 6 bytes more state.
- Safe against the audio interrupt: it writes only the target's row, which isn't the
  playing pattern (copypattern never picks the current, queued or playlist patterns),
  and single bytes.

Nothing calls the function in a tracklen-only build, so tl8 alone plays and behaves
exactly as tl7.

### Files

- `patch/tracklen/tracklen.c`: the function, the pending pair, the header comment.
- `patch/tracklen/patch.json`: version text `tl8` (unused today; checked again at build).
- `patch/tracklen/prebuilt/`: rebuilt.
- `patch/tracklen/README.md`: one line in "How it works" on the function; the
  "Copy to New copies a pattern without its lengths" line stays until step 2.
- `docs/PATCHING.md`: tracklen's row (tl8).
- `patch/all/patch.json`: new version text (fresh, checked unused at build).
- `web/manifest.json`: tested builds updated after the hardware test.

### Tests

- `tools/emu/test_tracklen.py`, a new section "the copy function", calling
  `mt_tl_copy_lengths` directly (found with `patch_function`, the table with
  `patch_ram`):
  - all 16 lengths copied; the source unchanged; the target's old lengths replaced,
    zeros included;
  - from = to, 0, and over 255: nothing changes;
  - `tracklen.bin` written once after the save delay, with the target's entry; a copy
    of a pattern with no lengths over one that had the only lengths: the file removed;
  - a project with no folder: nothing written, lengths in RAM;
  - called while the load is pending: applied after the load, from the loaded
    lengths; a project change first: dropped.
- Everything else in `test_tracklen` unchanged: without the call, tl8 is tl7.
- `tools/test_layout.py`: nothing new; its tracklen builds cover the new code.
- Runs: the quick tier while working, then the release tier with `--update` (tracklen's
  prebuilt changes, so every web mix with tracklen changes). Commands and estimates as
  `run_tests.py` gives them once the test changes are final.

### Hardware (user)

- `tl8` alone and the new `patch/all`: Trk Len works as before (set, play, save, reopen).
  Nothing new to see; this step is a regression check.

Then commit (on the user's word), one commit.

## Step 2: copypattern cp5

### Code (`patch/copypattern/copypattern.c`)

- A link word: `CHAIN_WORD(mt_cp_tl_copy);  /* tracklen's mt_tl_copy_lengths ("links"), 0 without it */`.
- In `write_copy`, after the pattern file is written, the usage mask set and the
  project marked unsaved: `if (mt_cp_tl_copy) ((tl_copy_fn)mt_cp_tl_copy)(CP->source, target);`.
  Only on a successful write: "Pattern changed - try again" and "Copy failed - SD card"
  copy no lengths. Not in the pass-through build.
- The message stays "Copied to pattern N". No new RAM.

### Files

- `patch/copypattern/copypattern.c`: the link word, the call, the header comment.
- `patch/copypattern/patch.json`: `"links": [{"word": "mt_cp_tl_copy", "patch":
  "tracklen", "symbol": "mt_tl_copy_lengths", "thumb": true, ...}]`, version `cp5`,
  the description ("with Variable Track Length, also its track lengths").
- `patch/copypattern/prebuilt/`: rebuilt.
- `patch/copypattern/README.md`, `patch/tracklen/README.md` (the "without its lengths"
  line becomes "Copy to New copies them"), `docs/TRACK_LENGTH_PLAN.md` (limits: only
  the Song screen's copy still leaves lengths behind), `docs/COPY_PATTERN_PLAN.md`,
  `docs/PATCHING.md` (copypattern's row), root `README.md` if its copypattern line
  mentions lengths.
- `patch/all/patch.json`: new version text. `web/manifest.json`: tested builds.

Needs step 1 in the repo: the patcher refuses a link to a symbol tracklen's prebuilt
doesn't have, so cp5 with tl7 can't be built.

### Tests

- `tools/emu/test_copypattern.py`:
  - with tracklen in the build (`TL_CODE`): lengths set in tracklen's table for the
    source; a copy, stopped and playing: the target gets them, its old lengths
    replaced; `tracklen.bin` written after tracklen's save delay; "Pattern changed"
    and a failed write: the target's lengths untouched;
  - without tracklen: the link word is 0 and the copy is as before.
- `tools/test_layout.py`: its link-word check already reads every `"links"` entry, so
  copypattern's word is checked in every build: tracklen's function where it landed,
  or 0. The existing builds cover copypattern alone, with tracklen in both orders, and
  `patch/all`.
- Runs: quick tier while working, release tier with `--update` before the commit.

### Hardware (user)

`cp5` alone, a `cp5` + `tl8` build, and the new `patch/all`:
- cp5 alone: Copy to New as with cp4.
- With tracklen: give a few tracks of a pattern Trk Len lengths; Copy to New while
  stopped and while playing; open the new pattern: same lengths, same playback.
- A target that had lengths before: they're replaced by the source's.
- Save, reopen the project: the new pattern still has its lengths.

Then commit (on the user's word), one commit. The user pushes.

## Not included

- The Song screen's own pattern copy still copies no lengths (stock code; a separate
  change if wanted).
- Expand, Shrink and Undo still leave lengths alone, as now.
