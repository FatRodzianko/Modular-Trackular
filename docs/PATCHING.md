# Patching Tracker+ firmware 1.2.0

How to turn your own copy of the stock firmware into a modded one, install it, and get
back to stock. Nothing here contains or redistributes Polyend's firmware: you download
it from Polyend and the patcher modifies your copy locally.

**Use at your own risk.** The mods here were tested on one Tracker+, and
switching back to stock always worked. That is not a guarantee for your unit.

## 1. Get and check the stock firmware

1. Download **Tracker+ firmware 1.2.0** from Polyend's website (the Tracker+
   firmware downloads). The file is `TrackerPlus_1.2.0.ptf`.
2. Check it:

   | | |
   |---|---|
   | Size | 4,586,889 bytes |
   | SHA-256 | `d4616304f33d6d0b56516c658f34fa22517c1dd1630021c1ca89afca7b618ccd` |

   ```bash
   sha256sum TrackerPlus_1.2.0.ptf                      # Linux / Git Bash
   Get-FileHash TrackerPlus_1.2.0.ptf                   # Windows PowerShell
   shasum -a 256 TrackerPlus_1.2.0.ptf                  # macOS
   ```

   The patcher also checks the decoded image (SHA-256 `0d924ba9…1f3b`) and refuses
   anything else, so a different firmware version can't be patched by mistake.

## 2. Build the patched firmware

### In your browser

The [web patcher](https://fatrodzianko.github.io/Modular-Trackular/) does what the
Python command below does, without installing anything:

1. Choose your stock `TrackerPlus_1.2.0.ptf`. The page checks the file's SHA-256 (the
   one in step 1) and the image's, and refuses anything else, including a file that's
   already patched.
2. Tick the mods you want. One mod gives its own build, "Select all" gives the
   `patch/all` bundle (all twelve, `1.2.0-MTW`), and any other mix gets a version text
   that says which mods are in it: `M` plus two hex digits for colorsong 01, humanize 02,
   interpolate 04, masterfx 08, swing 10, visualizer 20, condtrig 40 and synths 80
   (colorsong + swing is `1.2.0-M11`); `N` instead of `M` when legato is in it
   (colorsong + legato is `1.2.0-N01`), `O` when copypattern is (colorsong + copypattern
   is `1.2.0-O01`), `P` when both are, and four letters further on (`Q` to `T`) when
   slicethru is in it too (colorsong + slicethru is `1.2.0-Q01`). With resample the
   letter is eight further on, `U` to `Z` and then `A` and `B` (colorsong + resample is
   `1.2.0-U01`).
3. Patch and download. The page only offers the file when its SHA-256 is the one
   `tools/patch_ptf.py` makes for that selection (`web/expected.json`), and says when
   it's also one of the hardware-tested builds in the table below. A mix that isn't in
   the table hasn't been tested on hardware as one firmware.

Everything runs in the browser: the firmware is not uploaded, and the site serves only
this repository's page and prebuilt patch files. For a mix, the page shows the bundle
spec it used, so the Python command gives the same file. How it's built and tested:
[WEB_PATCHER_PLAN.md](WEB_PATCHER_PLAN.md).

Then install it as in section 3.

### With Python

You only need **Python 3** (standard library only, no compiler; tested with 3.15). The repository ships
the compiled patch code in `patch/<name>/prebuilt/`, so this works out of the box:

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/colorsong/patch.json build/TrackerPlus_c11.ptf
```

Use `patch/humanize/patch.json` for Humanize alone, `patch/interpolate/patch.json` for
interpolate alone, or `patch/all/patch.json` for all twelve in one firmware (see "Choosing patches" below for your own
mix).

The patcher checks the stock image, checks the original bytes at every patched
address, writes the new `.ptf`, reads it back, and prints its SHA-256. The output
must be byte-identical to these builds:

| Patch | Output file | Version text in Config | On hardware | Output SHA-256 |
|---|---|---|---|---|
| colorsong | `TrackerPlus_c11.ptf` | `1.2.0-c11` | tested | `cc9119abe627d994ba6010bfe0db59129e3f494611dbc8c1e5c68fcf0653ebc6` |
| humanize | `TrackerPlus_hz9.ptf` | `1.2.0-hz9` | tested | `1b4585815fa0c66b5689135375f2da8e7bfec57cce6f5cf2e62592f4c4765626` |
| the previous humanize (Save As dropped the settings) | `TrackerPlus_hz8.ptf` | `1.2.0-hz8` | tested (with colorsong and interpolate) | `ec3c30fda0d186299e39cce2be48031303f3bc0583fd1e8803600b500bf4b46f` |
| interpolate | `TrackerPlus_ip6.ptf` | `1.2.0-ip6` | tested | `598dc01fb55ec0549c9809d2a14702e591354f29a07c0ad7d7401d405171391e` |
| the previous interpolate (Save As dropped the settings) | `TrackerPlus_ip5.ptf` | `1.2.0-ip5` | tested (with colorsong and humanize) | `f3d98eb18f5852dc718d41ba9311e813d056d1ab5b37b1252e87cf0341c0942a` |
| masterfx | `TrackerPlus_mf5.ptf` | `1.2.0-mf5` | tested | `f7ec780456867eac7a8cb469d6bb929949ef7a334ccaeb1c0c645863c8657a5a` |
| the previous masterfx (Save As dropped the settings) | `TrackerPlus_mf4.ptf` | `1.2.0-mf4` | tested | `0f3f9cd0c0c00ceed7651aacece96bd52dd084991e82f9e03d814630b898df1a` |
| swing | `TrackerPlus_sw2.ptf` | `1.2.0-sw2` | tested | `05a3f65fb21381224f95f0e41f1782909b86fbe0628cbaa9263da18e4ac1a275` |
| the previous swing (Save As dropped the settings) | `TrackerPlus_sw1.ptf` | `1.2.0-sw1` | tested | `510fe9ad078ed737f1cf3c63ea706a8a0e50f978c9d115bfbc773f4252a0e8d7` |
| visualizer | `TrackerPlus_vz5.ptf` | `1.2.0-vz5` | tested | `46970bb1689414fd711cadc6cfd149da458145aafcd9f6d274f10759dce9bdbd` |
| the previous visualizer (the same animation, every pad update a full flush) | `TrackerPlus_vz2.ptf` | `1.2.0-vz2` | tested | `8b3ddcb09de46829799f542cf9d7423d95d4f39aa76db44881f5c18eb0361736` |
| condtrig | `TrackerPlus_ct1.ptf` | `1.2.0-ct1` | tested | `539328948185c4a02577ce62181fc4143a5e86dc06a33e61b469d52b21da8197` |
| synths (WAVE and PHASE) | `TrackerPlus_sy9.ptf` | `1.2.0-sy9` | tested | `1c3dc6dad3f0abbe1fda5cc9837a55a28c63a088cd40f238fba1776e0d1e52fc` |
| the previous synths (Save As over an existing project kept that project's choices) | `TrackerPlus_sy8.ptf` | `1.2.0-sy8` | tested | `75087646e0902daa7867fe5fdf91de98874348a2140f1becbd5d0ba29bee06e2` |
| the previous synths (WAVE only) | `TrackerPlus_sy7.ptf` | `1.2.0-sy7` | tested | `dddf733b197e59d8a6dc7f3e1ee253ec450518238ca991d651f03ce4e0dc940f` |
| the previous synths (without the effect) | `TrackerPlus_sy6.ptf` | `1.2.0-sy6` | tested | `6f56aa00881797f55bec494b359d1248badd0fa11a66e4b3b15cd1adb878dc52` |
| the previous synths (without Drive) | `TrackerPlus_sy5.ptf` | `1.2.0-sy5` | tested | `4ccd24c538a814e7f930130d7cf69bfb9733515ae362569e1d41cf1aa6996e36` |
| the previous synths (filter envelope on the Env 2 tile) | `TrackerPlus_sy4.ptf` | `1.2.0-sy4` | tested | `2fca2e07bb19ef4e539328152af27c173067ec6088604c45d9f28b96aa876e97` |
| legato (MIDI and synth slots) | `TrackerPlus_lg2.ptf` | `1.2.0-lg2` | tested | `a7bab99688e6036e58b66bd8e60806170a045b10c57a218d6607ab9261f81a75` |
| the previous legato (MIDI only) | `TrackerPlus_lg1.ptf` | `1.2.0-lg1` | tested | `f266ed1f131fc15edf12b224e081f16d9109938b004d8410e2cb0cdb380a68e6` |
| copypattern | `TrackerPlus_cp4.ptf` | `1.2.0-cp4` | tested (also with humanize) | `7bfbc3eb09a7eb9d9dd4ccf3a8a52b1221743be7061397048f72bbc32908db01` |
| all (colorsong + humanize + interpolate + masterfx + swing + visualizer + condtrig + synths + legato + copypattern + slicethru + resample) | `TrackerPlus_MTW.ptf` | `1.2.0-MTW` | tested | `b927f87b5584f6576f5d5f09d2481bbeb9203328a690380843d68bca93aef933` |
| the previous all (the same with synths sy8) | `TrackerPlus_MTS.ptf` | `1.2.0-MTS` | tested | `ac0edf2f1f2d3077e3c9f95babd136f065ca40ec18080e23d8cab1f496f595c9` |
| the previous all (the same with swing sw1, humanize hz8, interpolate ip5, masterfx mf4) | `TrackerPlus_MT5.ptf` | `1.2.0-MT5` | tested | `61446c44bfd6669dd1044da4415ceb13f0df6b47c6f6a49ad37f2405195ae4f7` |
| the previous all (the same with slicethru bt4, without Loop) | `TrackerPlus_MT4.ptf` | `1.2.0-MT4` | tested | `eec53343f9fc18ed981863af0808761974ee23024158705df4c362a87442fee8` |
| the previous all (the same with slicethru bt3) | `TrackerPlus_MR4.ptf` | `1.2.0-MR4` | tested | `b2376c91b9382658c39018a78daf78c9b22a083bfb1b4c739e147bffea84748f` |
| the previous all (without resample) | `TrackerPlus_MS8.ptf` | `1.2.0-MS8` | tested | `ee0c901bd8cc7c7786e68c19d7c9e97fc93847fb10adbaa48a80b138638fbd4d` |
| the previous all (the same with synths sy7) | `TrackerPlus_MBT.ptf` | `1.2.0-MBT` | tested | `f610374f5bbf29a100272dc5fa4af545ecd778932d57b9a913d54f1a30a4c33c` |
| the previous all (without slicethru) | `TrackerPlus_MS7.ptf` | `1.2.0-MS7` | tested | `5ed2e44bed5432a84e4787d10b0222072f1a9d72776a1d3de357582679cafefd` |
| the previous all (the same with synths sy6) | `TrackerPlus_MS6.ptf` | `1.2.0-MS6` | tested | `764994f1711bddafa691d44567239442cc1729fd5da776ab1510ae1e0b416457` |
| the previous all (the same with synths sy5) | `TrackerPlus_MS5.ptf` | `1.2.0-MS5` | tested | `05d5d8153c41a81d2b40a50f9c300befd76a850608f4b8d0cb40f9b151b363d7` |
| the previous all (the same with synths sy4) | `TrackerPlus_ML2.ptf` | `1.2.0-ML2` | tested | `8fd81d702351f30b57b1da47c297248e184043a363b10e9a3b75027e6e84aa5e` |
| the previous all (the same with legato lg1) | `TrackerPlus_MLC.ptf` | `1.2.0-MLC` | tested | `4170d1cbf34c3a91073d6f276798844dcdd10faee99acb7fc079af484bc269b0` |
| the all before that (with legato lg1, without copypattern) | `TrackerPlus_MLG.ptf` | `1.2.0-MLG` | tested | `4afd36206ccbb73b6d2556b4de9228afc060360653152fa492d67139da2ad0a2` |
| the one before that (without legato and copypattern) | `TrackerPlus_MSY.ptf` | `1.2.0-MSY` | tested (made with the web patcher) | `f3fee2dd07e88d3b8810cd1d9b371e0636f7ad4920c777f021b28da2afd2fd99` |
| the one before that (without synths, legato and copypattern) | `TrackerPlus_MCT.ptf` | `1.2.0-MCT` | tested | `6d818234483106b3b6444c1f180bf3f2b5cb93060d75762f752f252fce6b8437` |
| the one before that (without condtrig, synths, legato and copypattern) | `TrackerPlus_MTE.ptf` | `1.2.0-MTE` | tested | `72f65355487ea13908a73cc891dacf3568e883ce2fc5388c3c169c888852d982` |
| slicethru (Beat Slice Thru, with Loop) | `TrackerPlus_bt5.ptf` | `1.2.0-bt5` | tested | `5f50698d8545833a6c6a7cbba495de4004da2cd9250b36e7ad548f50dc4981e8` |
| the previous slicethru (without Loop) | `TrackerPlus_bt4.ptf` | `1.2.0-bt4` | tested | `8aed6732660f5c8e8ab231b20fbfa58bfaf3b875896ce790b5b9ae2bd6552232` |
| the previous slicethru (Save As dropped the setting) | `TrackerPlus_bt3.ptf` | `1.2.0-bt3` | tested | `76e0a004302bb9e57d214cb04cf0053e906f1b6de3141a4ad4809f440de2cb2e` |
| resample (live resampling) | `TrackerPlus_rs4.ptf` | `1.2.0-rs4` | tested | `5cf21721e8c1f9339304b24adfcc355a07f37dcaff9697a5b97ab41d50352925` |
| ramdiag (diagnostic, on its own) | `TrackerPlus_RD2.ptf` | `1.2.0-RD2` | tested | `84a33f1858bdd3c908eeea62227d84f68fe450dc4e3e21fb21077292fef1b82e` |
| versiontest | `TrackerPlus_MT0.ptf` | `1.2.0-MT0` | tested | `f45510a17f8b9b120c0e699f239a737a145c3d57bbaf596046fb7a8bc643ffff` |
| itcmtest (ITCM hooks test, on its own) | `TrackerPlus_it1.ptf` | `1.2.0-it1` | tested | `701b35dfadb4626ebe19e94ccb3af47aff9e4d604684d59b2e8b9cbf5da18d7c` |

**Name the output `TrackerPlus<anything>.ptf`.** Config's firmware list only shows
files that start with `TrackerPlus` and end with `.ptf`. Keep names short: on the
original Tracker, names longer than 31 characters showed garbage.

### Choosing patches: one, several, or your own mix

Each patch can be installed on its own, or several can go into one firmware:

- **One patch:** use its own spec. `patch/colorsong/patch.json` gives colorsong only,
  `patch/humanize/patch.json` Humanize only, `patch/interpolate/patch.json`
  interpolate only, `patch/masterfx/patch.json` the master reverb and delay chosen per project
  (see its README for the effects), `patch/swing/patch.json` Global Swing,
  `patch/visualizer/patch.json` the pad visualizer, `patch/condtrig/patch.json`
  conditional trigs, `patch/synths/patch.json` the WAVE and PHASE synths, `patch/legato/patch.json`
  MIDI legato,
  `patch/copypattern/patch.json` the Copy to New button,
  `patch/slicethru/patch.json` the Beat Slice Thru play mode,
  `patch/resample/patch.json` live resampling.
- **Several patches:** use a bundle spec, which lists the patches to combine.
  `patch/all/patch.json` is the bundle that ships (all twelve: colorsong, Humanize,
  interpolate, masterfx, swing, the visualizer, condtrig, synths, legato, copypattern,
  slicethru and resample):

  ```json
  "include": ["../colorsong/patch.json", "../humanize/patch.json", "../interpolate/patch.json",
              "../masterfx/patch.json", "../swing/patch.json", "../visualizer/patch.json",
              "../condtrig/patch.json", "../synths/patch.json", "../legato/patch.json",
              "../copypattern/patch.json", "../slicethru/patch.json", "../resample/patch.json"],
  "version": "MTW",
  ```

To make your own mix, copy `patch/all/` to a new folder (for example `patch/mymix/`)
and edit its `patch.json`:

1. List the patches you want in `include`, as paths relative to the bundle's folder.
   They are placed in flash in that order. Your own patches can be listed too.
2. Change `name`, and set `version` to 1–3 characters that tell your build apart.
   Config shows `1.2.0-<version>`; longer text doesn't show.
3. Leave `stock_image_sha256` and `version_word` as they are.

```bash
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/mymix/patch.json build/TrackerPlus_mymix.ptf
```

The patches in one firmware have to fit together:

- **Flash: handled for you.** The patcher puts the patches after the stock image in
  the order listed, each right after the one before (16-byte aligned), and moves
  each one's code to where it lands, using the relocation table its build makes
  (`prebuilt/<name>.rel`). Listing `["../interpolate/patch.json",
  "../humanize/patch.json", "../colorsong/patch.json"]` puts them the other way round
  (`tools/test_layout.py` checks both orders, and both were tested on hardware). A patch whose spec has a fixed
  `base` and no relocation table (like versiontest) stays exactly there, and must not
  overlap what comes before it. An output that would end within the first 256 bytes of
  a 4 KB flash sector gets `0xFF` padding into the sector's second page: the bootloader
  doesn't erase a sector whose data stop in its first page, and a build that ended there
  boot-looped (FINDINGS section 26).
- **Hook sites: shared ones must be chained.** Two patches can hook the same call or
  data word only if both chain it: the hook names a `next` word in the patch, which
  the patcher fills with what the site pointed to before (the stock function, or the
  hook of the patch listed before it), and the patch calls through that word instead
  of calling the stock function. The last patch listed runs first. Humanize,
  interpolate, masterfx and swing chain the Project Settings menu and the Pattern and
  Config update methods this way, so they all appear in Config. Swing and the visualizer
  both chain the sequencer's audio-block and step-advance calls, and the visualizer the
  Pattern update method too. Sites only one patch needs can stay plain.
- **RAM: handled for you.** Each patch's state variables are one block (its
  `.mt_ram` section). The patcher hands the blocks out from the free RAM regions
  below, in the order the patches are listed: each block goes into the first region
  (in the patch's order) that still has room, 16-byte aligned, and the patch's code
  is moved to match. It prints where each block went and writes it, with the code
  ranges, to `<output>.map.json` next to the `.ptf`.

  | Region | Addresses | Size | Notes |
  |---|---|---|---|
  | `ocram_tail` | `0x20205000`–`0x20207fff` | 12,288 B | on-chip |
  | `dtcm_above_heap` | `0x20051c70`–`0x20052fff` | 5,008 B | on-chip, below the stack |
  | `sdram_top` | `0x83ff9bf0`–`0x83ffffff` | 25,616 B | external SDRAM; not before `main`. Fine for state used once the unit runs (colorsong); not for state written from the audio interrupt during boot (that hung the unit at boot) |
  | `dtcm_after_audio` | `0x200094c0`–`0x200097ff` | 832 B | on-chip |
  | `itcm_tail` | `0x0001fb40`–`0x0001ffff` | 1,216 B | only when a spec names it; aligned accesses only |

  The default order is the first four, top to bottom. colorsong (12,208 B) and
  Humanize (32 B) both fit in `ocram_tail`. If a patch's block fits nowhere, the
  patcher refuses to build.
- **Firmware:** every patch must be for the same stock firmware
  (`stock_image_sha256`).

The patcher refuses to build when code ranges overlap, two patches hook the same site
without both chaining it, a patch is for another firmware, an original byte doesn't match, the RAM runs out, or
a bundle includes a patch marked `standalone` (the ramdiag diagnostic). It **can't**
detect two patches that each work alone but disturb each other.

A new combination, or the same patches in another order, is a firmware nobody has
tested, and its SHA-256 isn't in the table above. Before installing it, run the
emulator tests of every patch it contains against the combined `.ptf` (below), and
keep the stock `.ptf` in `/Firmware`. `tools/test_layout.py` checks the placement
itself (below).

### Building the patch code yourself (optional)

With the Arm GNU Toolchain installed (`arm-none-eabi-gcc` on `PATH`, or `ARM_GCC_BIN`
set to its `bin` folder):

```bash
patch/colorsong/build.sh                                # writes patch/colorsong/build/
python tools/patch_ptf.py path/to/TrackerPlus_1.2.0.ptf patch/colorsong/patch.json build/TrackerPlus_c11.ptf
```

When `patch/<name>/build/` exists, the patcher uses it instead of `prebuilt/`. The
tested build used Arm GNU Toolchain 15.3.rel2. A different compiler may produce
different (but equivalent) code and therefore a different hash.

### Checking it in the emulator first (optional, recommended for your own changes)

The quickest way is `tools/run_tests.py`, which runs the checks below in parallel and
skips anything that already passed on the same files:

```bash
python tools/run_tests.py path/to/TrackerPlus_1.2.0.ptf --tier quick      # while working on a patch (~2 min)
python tools/run_tests.py path/to/TrackerPlus_1.2.0.ptf --tier release    # before a commit users get (~40 min with an empty cache)
```

`--mixes-of swing,humanize,...` (release or deep) also runs every patch's test on every
mix of two or more of the named mods, for a change to a hook they all share.

- **quick:** `test_layout.py`, then every patch's emulator test on each patch alone and
  on `patch/all`.
- **release:** quick, plus `test_web_patcher.py` (every web patcher selection
  byte-identical to `patch_ptf.py`'s), plus every patch's test on a *cover set* of web
  patcher mixes and on the bundles `test_layout.py` builds in other orders.
  - The cover set is the fewest mixes that between them contain every case that differs
    from mix to mix: each patch's RAM at the start, middle and last 64 bytes of each
    region it can land in, each pair of patches next to each other on a shared hook, and
    an image the sector padding pads and one it doesn't. For the twelve mods that's 19
    of the 4083 mixes, besides `patch/all`.
  - masterfx and synths run in `--quick` mode, which leaves out their sound, cost and
    long stress checks (these depend only on their own code), except on `patch/all`,
    where they run in full when their own files (`patch.json`, `prebuilt/`, `build/`)
    changed since their last full pass.
- **deep:** release with masterfx and synths in full everywhere; only when their audio
  code changes.

It prints an estimate before it starts and the time left as it goes, keeps a log per
test in `build/testrun/logs`, and writes `build/testrun/summary.txt`. Its cache
(`build/test_cache.json`) remembers what passed: a test isn't run again on a firmware
file it already passed with the same test and emulator code. The web patcher check
keeps its own cache per selection, so after a change to one mod it redoes only the
selections that contain it (all 4095 take about 21 minutes, none about 30 seconds).
`--no-cache` runs everything; `--jobs N` sets how many run at once.

Each check can also be run on its own:

```bash
python -m pip install unicorn capstone numpy
python tools/emu/check_version.py build/TrackerPlus_c11.ptf          # -> '1.2.0-c11'
python tools/emu/test_colorsong.py build/TrackerPlus_c11.ptf
python tools/emu/test_rescan.py build/TrackerPlus_c11.ptf "path/to/<project A>" "path/to/<project B>"
python tools/emu/test_humanize.py build/TrackerPlus_hz9.ptf
python tools/emu/test_interpolate.py build/TrackerPlus_ip6.ptf
python tools/emu/test_masterfx.py build/TrackerPlus_mf5.ptf path/to/TrackerPlus_1.2.0.ptf out/fx_wav
python tools/emu/test_swing.py build/TrackerPlus_sw2.ptf path/to/TrackerPlus_1.2.0.ptf
python tools/emu/test_visualizer.py build/TrackerPlus_vz5.ptf
python tools/emu/test_condtrig.py build/TrackerPlus_ct1.ptf path/to/TrackerPlus_1.2.0.ptf
python tools/emu/test_itcmtest.py build/TrackerPlus_it1.ptf path/to/TrackerPlus_1.2.0.ptf
python tools/emu/test_synths.py build/TrackerPlus_sy9.ptf path/to/TrackerPlus_1.2.0.ptf [wav folder]
python tools/emu/test_phase.py build/TrackerPlus_sy9.ptf path/to/TrackerPlus_1.2.0.ptf [--wav folder]
python tools/emu/test_legato.py build/TrackerPlus_lg2.ptf path/to/TrackerPlus_1.2.0.ptf
python tools/emu/test_copypattern.py build/TrackerPlus_cp4.ptf
python tools/emu/test_slicethru.py build/TrackerPlus_bt5.ptf path/to/TrackerPlus_1.2.0.ptf
python tools/emu/test_resample.py build/TrackerPlus_rs4.ptf path/to/TrackerPlus_1.2.0.ptf
```

`tools/run_tests.py` runs the tests of the mods the web patcher offers. resample isn't
offered yet, so run its test on its own, as above.


Each test works on any firmware that contains its patch, so for a bundle run every
contained patch's tests on the bundle's `.ptf`. The tests read where the patch's RAM
went from the `.map.json` the patcher wrote next to the `.ptf`, so keep the two
together.

`tools/test_layout.py` checks the patcher's placement without the emulator. It builds
each patch alone, `patch/all` (all twelve) and its reverse, colorsong, humanize and interpolate in the
other order, those three with masterfx in both orders, the six before condtrig in the other order, the six with synths in both orders,
copypattern alone, with humanize in both orders and with all nine in both orders,
slicethru alone and first with the others after it in reverse order,
resample alone and first with the others after it in reverse order, and two
patches on the same chained sites, and
checks that each patch's code and RAM sit where they
should (RAM in a region the patch may use, no two blocks overlapping), that exactly the
words in its relocation table moved, that each chain word holds what its site pointed
to before, and that every hooked site points at the last patch that hooks it. With
itcmtest (alone, after and before all the others, and chained with a copy of itself)
it also checks the ITCM stubs: each hooked ITCM call goes to its stub, the stub jumps
to the last hook, the copy table copies the stubs to ITCM, and no RAM overlaps them.
It also checks that a shared site that isn't chained by both patches is refused, and
two patches passing different registers at one ITCM site:

```bash
python tools/test_layout.py path/to/TrackerPlus_1.2.0.ptf build/layout
```

`tools/test_web_patcher.py` (needs Node) runs the web patcher's code on every selection
the page offers and on `test_layout.py`'s other bundle orders, checks each output is
byte-identical to `patch_ptf.py`'s, and runs `test_layout.py`'s checks on them. After
changing a patch, or the mods in `web/manifest.json`, run it with `--update` to refresh
`web/expected.json`; the Pages workflow refuses to publish until then. The release tier
does that in the same run when given `--update`, so the harness doesn't run twice:

```bash
python tools/run_tests.py path/to/TrackerPlus_1.2.0.ptf --tier release --update
python tools/test_web_patcher.py path/to/TrackerPlus_1.2.0.ptf build/webtest --update   # the harness alone
```

Each published site is a build named by a digest of its files (`web/stage.py`). The page
loads its code by that name and checks `build.json`, so a browser still holding an earlier
build's page or code (GitHub Pages lets it keep them for 10 minutes) reloads once onto the
new build, instead of running old code on new patch files.

The test projects are copies of project folders from the SD card (`Projects/...`).

## 3. Install

1. **Back up your SD card** (the whole card, including the `Workspace` folder).
2. In the card's `/Firmware` folder, keep the stock `TrackerPlus_1.2.0.ptf` and add the
   patched file. Remove other old `.ptf` versions, so an emergency reflash (below) can
   only pick one of these two.
3. On the Tracker+: **Config → Firmware**, choose the patched file, confirm. The unit
   updates and reboots.
4. Config should now show the patch's version text, e.g. `1.2.0-c11 Build 1504`.

## 4. Back to stock

Config → Firmware → `TrackerPlus_1.2.0.ptf` → confirm. Config shows `1.2.0 Build 1504`
again.

Some mods keep per-project settings in a small file inside the project folder:
colorsong its colors in `colorsong.bin`, Humanize its ranges and velocities in
`humanize.bin`, interpolate its switches in `interpolate.bin`, masterfx its effect
choices in `masterfx.bin`, swing its settings in `swing.bin`. Stock firmware ignores these files, and you can delete them
(deleting one resets that mod's settings for the project: colorsong's colors go back to
song order, the others to their defaults).

## 5. If it doesn't boot

The bootloader is separate from the firmware and isn't touched by patches.

1. On a computer, remove the patched `.ptf` from `/Firmware`, so only the stock
   `TrackerPlus_1.2.0.ptf` is left.
2. Put the card back, power on, and press the **pinhole button next to the USB-C
   port** with a pin. The bootloader shows "Updating..." and installs the `.ptf` from
   `/Firmware`.

This worked on the test unit (with the unit on and the card in). If even that fails,
contact Polyend support.

If the unit only freezes in one place (e.g. a mod's screen) but boots, pull the power,
boot, and switch back to stock through Config.

## 6. Writing your own patch

See `patch/colorsong/` for a complete example and `docs/FINDINGS.md` for the firmware
details. A patch is:

- C/assembly code, linked at any address after the stock image (`link.ld`).
- State variables in the `.mt_ram` section:
  `static struct my_state state __attribute__((section(".mt_ram")));`. It isn't
  initialised (no zeroing, no initial values): the patch has to set it up itself,
  for example with a magic word checked on first use. The patcher picks the address.
- A relocation table, so the patcher can move the code and the RAM: `build.sh` links
  the patch three times (as built, code moved 64 KB, RAM moved 64 KB), and
  `tools/mkrel.py` lists the words that differ by exactly that much (pointers to the
  patch's own code and data, and to its RAM). It refuses code it can't move this way.
  The colorsong and Humanize build scripts show how.
- `patch.json`: the stock image hash, the build outputs (`bin`, `sym`, `rel`, and
  their `prebuilt_` copies), Thumb-2 `BL` sites to retarget (with their original
  bytes), and data words to replace (literal pools, vtable slots with
  `"thumb": true`). Instead of a relocation table, a spec can give a fixed `base`.
  A hook other patches may share gets `"next": "<symbol>"`: a 4-byte word in the
  patch, linked as 0, that the patch calls through instead of the original
  (`const volatile uint32_t` in C, so the compiler reads it; see Humanize's
  `CHAIN_WORD`).
  A hook can also replace 4 bytes that aren't a `BL` (two 16-bit instructions, or one
  32-bit one): a `branches` entry with `"replaces": "insns"`. The patcher writes a `BL`
  to the hook there, and the hook does the replaced instructions' work itself and
  returns to the next one. With no original call to chain to, such a site takes no
  `next`, and no second patch may hook it. The patcher refuses any two sites that
  overlap.
  Calls inside the sequencer's ITCM code go in `itcm_branches` instead: `at` is the
  ITCM address of the `BL`, with its original bytes as in `branches`, and `args` names
  up to two registers (r4–r12, sp) that the call site holds and the hook should get
  as its third and fourth arguments. ITCM code can't call patch code in flash
  directly, so the patcher puts a stub per site at the start of the ITCM tail
  (`0x1fb40`, which is then not given out as RAM), and sets the startup copy table's
  unused entry 3 to copy the stubs there at boot. The stub moves `args` into r2 and r3
  and jumps to the hook, which returns straight to the ITCM caller. Chaining works as
  for `branches`, with the original arguments plus `args` passed on through `next`, so
  every patch on one ITCM site must name the same `args`. See
  [patch/itcmtest/](../patch/itcmtest/).
  Optional RAM settings:
  - `"ram_startup": true` when the state is used before `main` (for example from the
    Config menu constructor, as Humanize does). The patch then never gets
    `sdram_top`, which may not be set up that early.
  - `"ram_regions": [...]` to choose the regions and their order instead of the
    default. Name `itcm_tail` here to allow it (32-bit aligned accesses only).
  - `"late_ram": "<symbol>"` for a second block, for state used only once the unit has
    booted (it may land in `sdram_top`). The symbol names a word in the patch that holds
    the block's size, `const volatile uint32_t name = sizeof(...)`; the patcher gives the
    block out after every patch's own, from the default region order, and writes its
    address over the size. See synths' effect state.
  - `"standalone": true` for a patch that must not be combined with others.
- `tools/patch_ptf.py` checks everything, appends the code without gaps, and keeps the
  stock Intel HEX layout, so an unmodified image re-encodes to the exact stock file.
- To combine it with other patches, give it its own hook sites or chain the shared
  ones, and mark its version-text word with `"version": true` so a bundle can
  replace it (see "Choosing patches" in section 2).
