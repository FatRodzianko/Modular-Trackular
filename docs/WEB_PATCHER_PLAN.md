# Web patcher: plan

A page on GitHub Pages that does what `tools/patch_ptf.py` does, in the browser, for
people who can't run Python. The user picks their own stock `TrackerPlus_1.2.0.ptf`,
ticks the mods they want, and downloads the patched `.ptf`. Nothing is uploaded: the
firmware is read, patched and saved on the user's own computer.

The page must produce exactly the bytes `tools/patch_ptf.py` produces from the same
specs. Everything below serves that.

## What the page does

1. **Stock firmware.** The user drops or chooses a file. The page checks:
   - the file's SHA-256 is the official `TrackerPlus_1.2.0.ptf`
     (`d4616304…8ccd`, 4,586,889 bytes, from `web/manifest.json`), and
   - the decoded image's SHA-256 is the spec's `stock_image_sha256` (`0d924ba9…1f3b`),
     as `patch_ptf.py` checks.

   Anything else is refused with a message that says what the file is not and where
   to get the right one. The file check is stricter than `patch_ptf.py`, on purpose:
   with the exact file as the only input, every output the page can make is known in
   advance (step 3).
2. **Mods.** A grid of cards, one per mod in the manifest: checkbox, name, a short
   description, the hardware status and a link to its README. A "Select all" box ticks
   exactly the mods `patch/all/patch.json` includes and builds that bundle.
3. **Build.** The page works out the spec (below), patches in memory, reads the result
   back (as `patch_ptf.py` does), hashes it, and compares the hash with
   `web/expected.json`: the SHA-256 that `tools/patch_ptf.py` produced for the same
   selection, made by the test harness. Only a match gives a download button. A
   mismatch says so and offers nothing to download. When the hash is also a
   hardware-tested build (the table in `docs/PATCHING.md`), the page says so.
4. **Install.** Steps on the page: back up the card, copy to `/Firmware` next to the
   stock file, Config → Firmware, back to stock the same way, and the pinhole reflash
   if it doesn't boot (the same text as `docs/PATCHING.md` sections 3 to 5).

Not offered: `versiontest`, `ramdiag` (a standalone diagnostic) and anything not in
the manifest. Mods still in progress (`patch/synths`, conditional trigs) are added to
the manifest when they are finished and tested on hardware.

## Which spec a selection uses

| Selection | Spec | Version text |
|---|---|---|
| one mod | its own `patch/<name>/patch.json` | the mod's own (`1.2.0-sw1`, …) |
| all of them (exactly the mods in `patch/all`) | `patch/all/patch.json` | `patch/all`'s (`1.2.0-MTE`) |
| any other mix | a bundle made in the page (below) | `M` + 2 hex digits |

A mix is a bundle spec built from `patch/all/patch.json` (same `firmware`,
`stock_image_sha256`, `version_word`), with:

- **`include` in the manifest's order**, the canonical order: colorsong, humanize,
  interpolate, masterfx, swing, visualizer. It is the order `tools/test_layout.py`
  tests ("every"), and `patch/all`'s order is a part of it. New mods go at the end.
- **`version`**: `M` followed by the selection's bit mask as two uppercase hex
  digits. Each mod has a fixed `bit` in the manifest (colorsong 0, humanize 1,
  interpolate 2, masterfx 3, swing 4, visualizer 5), so the text says which mods are
  in a firmware: colorsong + swing is `1.2.0-M11`. Always 3
  characters, so Config shows it. It can't collide with `MTE` or `MT0` (`T` isn't a
  hex digit). Bits 6 and 7 are left for the next two mods; a ninth needs a new
  scheme (for example two base-32 digits).

The page offers this spec for download too ("for Python users"), so anyone can
rebuild the same file with `tools/patch_ptf.py` and compare.

The output is named `TrackerPlus_<version>.ptf` (`TrackerPlus_sw1.ptf`,
`TrackerPlus_MTE.ptf`, `TrackerPlus_M11.ptf`): it starts with `TrackerPlus` and ends
with `.ptf`, as Config's firmware list needs, and is short. The page reads the
version text from the output itself (the version word's pointer), as
`tools/emu/check_version.py` does, and shows it.

## The port

Plain JavaScript, no build step, no libraries, no CDN:

- `web/patcher.js`: an ES module with no DOM code, so the same file runs in the
  browser and under Node for the tests. A line-for-line port of `tools/ptf.py`
  (reading Intel HEX the way `read_ptf` does, re-encoding with the stock record
  layout as `encode_ptf` does) and of `tools/patch_ptf.py` (`load_patch`,
  `assign_ram` with the same `RAM_REGIONS` and order, `place`, `set_next`,
  `thumb_bl`, `bl_target`, the branch pass, then the word pass with the version word,
  the tail, the 16-byte records with type 04 records, the read-back checks, the
  `.map.json`). It refuses in the same cases with the same reasons.
  - All 32-bit arithmetic goes through `>>> 0` and `DataView` `getUint32`/`setUint32`:
    RAM addresses such as `0x83FF9BF0` are above `2^31`, where JavaScript's bitwise
    operators turn negative.
  - It refuses a spec with a key it doesn't know. `patch_ptf.py` is gaining ITCM
    hooks (`itcm_branches`) for the conditional-trigs work; a mod using them is
    refused by the page until the port has them, and the harness then checks it like
    the rest.
  - `.sym` and `.rel` files are read with either line ending (a checkout on Windows
    may have CRLF).
- `web/app.js`: the page (file input, grid, build, download). SHA-256 from Web Crypto
  (`crypto.subtle.digest`), which GitHub Pages' HTTPS allows. The download is a
  `Blob` URL.
- `web/index.html`, `web/style.css`: one page in a ProTracker-style look (CSS only,
  system fonts), light theme by default and a dark one by toggle (remembered), works
  at phone width.
- `web/manifest.json`: the data (below).
- `web/expected.json`: the expected output SHA-256 for every selection (keyed by the
  mods' ids in the manifest's order, joined with `+`), written by the test harness from
  `tools/patch_ptf.py`'s outputs, and `inputs_sha256`, a digest of the patch files they
  were made from (text files counted without CR, so either line ending gives the same
  digest).

`RAM_REGIONS`, `RAM_DEFAULT_ORDER`, `ALIGN` and `VERSION_MAX` are copied into
`patcher.js`. The harness checks they equal the Python values, so a change to one
without the other fails the tests.

## The manifest

`web/manifest.json`:

```json
{
  "stock": {"file": "TrackerPlus_1.2.0.ptf", "size": 4586889,
            "file_sha256": "d4616304…", "image_sha256": "0d924ba9…"},
  "repo": "https://github.com/FatRodzianko/Modular-Trackular",
  "all": "patch/all/patch.json",
  "patches": [
    {"id": "colorsong", "bit": 0, "spec": "patch/colorsong/patch.json",
     "title": "Color Song", "summary": "…", "status": "Tested on hardware"},
    …
  ],
  "tested_builds": {"<sha256>": "colorsong alone (1.2.0-c11)", …}
}
```

Each card's "Details" link is the mod's README on GitHub (`repo` + the spec's folder).

Adding a mod: build it, test it, add an entry with the next free `bit` at the end of
`patches`, run the harness with `--update` (it regenerates `web/expected.json` from
`tools/patch_ptf.py`), and add its hardware-tested hash to `tested_builds` once it has
been tested on hardware.

## How the patch files reach the page

The page fetches each spec and its `prebuilt_bin`, `prebuilt_sym` and `prebuilt_rel`
from the site, at the same paths as in the repository (`patch/<name>/patch.json`,
`patch/<name>/prebuilt/…`), and resolves `include` paths against the bundle's URL as
`patch_ptf.py` resolves them against its folder. The page never uses `bin`/`sym`/`rel`
(the local `build/` output, which isn't in the repository).

`web/stage.py <out dir>` assembles the site: the files in `web/` the page needs, plus
`patch.json` and the three prebuilt files of every mod in the manifest and of
`patch/all`, and nothing else. No copies are committed: `.gitignore` keeps all `*.bin`
out except `patch/*/prebuilt/`, so the repository has one copy of each binary, and
the site can't serve a stale one. The staged folder has no Polyend file in it; the
stage script refuses any `.ptf` or `.hex`. It also refuses when `web/expected.json`
doesn't list every selection, or its `inputs_sha256` doesn't match the patch files
being staged: a changed prebuilt can't be published until the harness has run again.
That check needs no firmware, so the Pages workflow runs it.

## Testing

`tools/test_web_patcher.py <stock .ptf> [out dir] [--update]` (needs Node, which runs
`patcher.js` through `tools/web_patcher_node.mjs`; nothing to install):

1. Stages the site into the out dir, as the deployment does.
2. Checks that `patcher.js`'s constants equal `patch_ptf.py`'s.
3. For **every selection** the page offers (each mod alone, `patch/all`, and every
   other mix: 63 with six mods), runs the page's own selection logic and patcher
   under Node on the staged files, writes the `.ptf`, the `.map.json` and the spec it
   used, then runs `tools/patch_ptf.py` on the same staged spec and stock file, and
   checks the two `.ptf` files are byte-identical and the `.map.json` files equal.
4. Runs the same comparison for the bundles `tools/test_layout.py` builds that the
   page doesn't offer (the reversed orders, masterfx first, the chained humanize
   copy), so the port is checked in both orders.
5. Runs `tools/test_layout.py`'s `verify` (placement, RAM, relocation, chains, version
   text) on every mix.
6. Checks the refusals match: a stock file of another version, a bundle with ramdiag,
   an unchained shared site.
7. Checks every single-mod and `patch/all` output against the hardware-tested SHA-256
   in `docs/PATCHING.md` and the manifest's `tested_builds`.
8. Checks `web/expected.json` holds exactly these hashes and the current
   `inputs_sha256` (`--update` rewrites it, only when everything else passed).

Then, by hand or from the harness's output folder: the emulator tests
(`tools/emu/test_*.py`) on a few web-produced files (all six, a two-mod mix, one
alone), and the page itself in a browser at phone and desktop width, in both themes,
with its output hash compared to Python's.

## Deployment

Recommended: a GitHub Actions Pages workflow (`.github/workflows/pages.yml`) that runs
on pushes to `master` touching `web/` or `patch/`, runs `python web/stage.py _site`,
and deploys `_site` with `actions/upload-pages-artifact` and `actions/deploy-pages`.
Only the staged files are served. Serving `/docs` instead would publish the RE notes
as a Jekyll site and can't serve the binaries without committing copies of them.

Steps for the repository owner:

1. Run the harness locally; all checks pass and `web/expected.json` is current.
2. Commit `web/`, `.github/workflows/pages.yml`, this plan and the doc changes; push.
3. On GitHub: Settings → Pages → Build and deployment → Source: **GitHub Actions**.
4. The workflow publishes to `https://fatrodzianko.github.io/Modular-Trackular/`.
   Open it, patch the stock file, and compare the downloaded file's SHA-256 with the
   table in `docs/PATCHING.md`.

Local preview: `python web/stage.py build/site` then
`python -m http.server -d build/site 8000` and open `http://localhost:8000/` (Web
Crypto works on `localhost`).

## Risks and how they're covered

- **A port bug.** Every output the page can make is compared byte for byte with
  Python's in the harness, and the page itself refuses any output whose hash isn't
  the Python one.
- **A prebuilt changes without the page's data.** The Pages workflow refuses to
  publish (`inputs_sha256`); were it published anyway, the page's hash check fails and
  offers no download. The harness fails until `web/expected.json` is regenerated.
- **A browser caches old files across a deploy.** The page fetches with
  `cache: "no-cache"` (revalidate); a mix of old and new files fails the hash check.
- **A wrong stock file.** Refused by the file hash and the image hash.
- **A mix that works alone but not together.** As with Python, the page can't detect
  this. Mixes that aren't a hardware-tested build say so on the page, and the install
  steps keep the stock file in `/Firmware`.
