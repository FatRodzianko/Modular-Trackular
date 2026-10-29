"""Run the repository's tests at a chosen depth, in parallel, skipping what already passed.

  python tools/run_tests.py <stock TrackerPlus_1.2.0.ptf> [--tier quick|release|deep]
                            [--jobs N] [--no-cache] [--out DIR] [--dry-run] [--update]
                            [--mixes-of a,b,...]

Tiers (times with an empty cache on an 8-core PC; with the cache, only what changed runs):
  quick    (about 2 min) tools/test_layout.py, plus every patch's emulator test on each patch alone
           and on patch/all (masterfx and synths in their --quick mode), and on the
           diagnostic builds (DIAGNOSTICS: headroom alone and listed last in patch/all
           and in patch/all + tracklen); test_bundle_cost.py's sequencer side on each of
           them. For a change to one patch, while working on it.
  release  (about 40 min) quick, plus tools/test_web_patcher.py (every web patcher selection
           byte-identical to patch_ptf.py's, and its layout checks), plus every patch's
           emulator test on a cover set of web patcher mixes and on the bundles
           test_layout.py builds in other orders; masterfx, synths and test_bundle_cost.py
           (its whole block) in full mode on patch/all. Before a commit that changes what
           users get. (synths' tests are
           test_synths.py and test_phase.py, PHASE's: EXTRA_TESTS.)
  deep     (hours) release with masterfx and synths in full mode on every mix. Overnight, and
           only when their audio code changes.

The cover set: the web patcher offers every mix of the mods (16383 for fourteen). Byte
identity and the layout checks run on all of them (the harness does that in minutes);
the emulator tests run on the fewest mixes that between them contain every case that
differs from mix to mix:
  - each patch's RAM block (and late RAM block, "late_ram") in each region it can land
    in, at the region's start, in its middle and in its last 64 bytes (where a short
    read past the end faults);
  - each pair of patches that sit next to each other in a hook chain on a shared site;
  - an image that the sector padding pads, and one it doesn't.
Each mix runs the emulator test of every patch it contains.

The cache (build/test_cache.json, local only): a job's key is the SHA-256 of the
firmware file it tests, the test and its mode, and a digest of every file under
tools/emu/ (and tools/make_headroom_project.py, whose project test_bundle_cost.py plays)
and of the stock firmware. A key that passed before is not run again, so a
rerun after a change only runs what the change touched. --no-cache runs everything.
The cache also keeps each test's measured duration, for the time estimate.

Full masterfx and synths on patch/all (release): their full mode adds checks that depend
on the patch's own code alone, so a full pass is also recorded against that patch's own
files (patch.json, prebuilt/, build/) and the test code. While those are unchanged,
patch/all gets the --quick run (the checks that depend on the mix) instead; the deep tier
always runs full mode. The harness keeps its own cache, per selection (see
test_web_patcher.py), so after a change to one patch it redoes only the selections that
contain it.

--mixes-of a,b,... (release and deep): also every patch's test on every web mix of two
or more of these mods and no others (26 for five), for a change to a hook they all
share, beyond the cover set's neighbour pairs.

--update (release and deep): the harness rewrites web/expected.json (the hashes the
page accepts) when everything else in it passes, as test_web_patcher.py --update does.
Use it after changing a patch or web/manifest.json, instead of a separate harness run.
The pass is recorded for the rewritten file, so the next run doesn't repeat it.

Before anything long, checks that take seconds (preflight), and stops on a failure:
  - every patch.json and web/*.json parses, and web/*.js and the Node tool pass node --check
    (release and deep; quick needs no Node);
  - each patch's build/ (which patch_ptf.py prefers to prebuilt/) holds what prebuilt/
    does: otherwise the tests test a build users don't get. Release and deep stop on it;
    quick only names them (a patch being worked on has a newer build/);
  - then tools/test_layout.py, alone, before the emulator tests.
If the web harness fails (its name check fails in about a minute, before it patches
anything), the jobs not yet started are dropped. --jobs N also caps the harness's own
processes; while it runs beside other jobs, up to 2N - 1 processes can be busy, so for a
hard CPU cap set the run's processor affinity (it passes to the processes it starts).

Writes a log per job and a summary (summary.txt) to --out (default build/testrun).
Exit status 0 when everything passed.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
EMU = TOOLS / "emu"
sys.path.insert(0, str(TOOLS))
from patch_ptf import RAM_REGIONS  # noqa: E402

PY = sys.executable
CACHE = ROOT / "build" / "test_cache.json"
EDGE = 64                                  # bytes from a region's start or end that count as "at the edge"
FLASH_SECTOR, FLASH_PAGE = 0x1000, 0x100
STOCK_ARG = {"masterfx", "swing", "condtrig", "synths", "phase", "legato", "slicethru", "resample", "tracklen", "songloop",
             "bundle_cost"}                # tests that take the stock .ptf too
QUICK_MODE = {"masterfx", "synths", "phase", "bundle_cost"}   # tests with a --quick mode
EXTRA_TESTS = {"synths": ["phase"]}        # a patch's tests besides test_<patch>.py (PHASE: test_phase.py)
DIAGNOSTICS = [                            # specs outside the web patcher, tested in every tier
    ("headroom", "patch/headroom/patch.json"),
    ("all-headroom", "patch/headroom/all_headroom.json"),
    ("all-tracklen-headroom", "patch/headroom/all_tracklen_headroom.json"),
]
DEFAULT_SECONDS = 60.0                     # estimate for a test not timed yet, unless below
MEASURED_SECONDS = {                       # alone, on an 8-core PC (the cache keeps its own, measured under load)
    "layout": 45, "web harness": 800, "colorsong": 1, "copypattern": 3, "humanize": 6, "visualizer": 8,
    "swing": 16, "headroom": 7, "interpolate": 25, "condtrig": 36, "legato": 130, "slicethru": 12, "resample": 245, "masterfx (quick)": 15,
    "masterfx (full)": 385, "synths (quick)": 127, "synths (full)": 1400, "phase (quick)": 150,
    "phase (full)": 660, "bundle_cost (quick)": 25, "bundle_cost (full)": 95,
}


# ---- helpers ------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_files(paths) -> str:
    h = hashlib.sha256()
    for p in sorted(p for p in paths if p.is_file() and "__pycache__" not in p.parts):
        h.update(str(p.relative_to(ROOT)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def data_end(ptf: Path) -> int:
    """Address after the image's last non-0xFF byte, read from the end of the Intel HEX."""
    last = None
    for line in reversed(ptf.read_text().splitlines()):
        n, addr, kind = int(line[1:3], 16), int(line[3:7], 16), int(line[7:9], 16)
        data = bytes.fromhex(line[9:9 + 2 * n])
        if kind == 0 and last is None and data.rstrip(b"\xff"):
            last = addr + len(data.rstrip(b"\xff"))
        elif kind == 4 and last is not None:
            return (int.from_bytes(data, "big") << 16) + last
    raise ValueError(f"{ptf}: no data")


def hook_sites(spec_path: Path) -> set[int]:
    spec = json.loads(spec_path.read_text())
    sites = set()
    for key in ("branches", "words", "itcm_branches"):
        for e in spec.get(key, []):
            if not e.get("version"):
                sites.add(int(e["at"], 16))
    return sites


# ---- what to test -------------------------------------------------------------------

def mods() -> list[tuple[str, Path]]:
    """The mods the web patcher offers (id, spec), in its order: the ones with tests."""
    manifest = json.loads((ROOT / "web" / "manifest.json").read_text())
    return [(m["id"], ROOT / m["spec"]) for m in manifest["patches"]]


def mix_features(ptf: Path, sites: dict[str, set[int]]) -> tuple[list[str], set]:
    """The patches in a built mix, and the cases it contains (see the cover set above)."""
    m = json.loads(ptf.with_suffix(".map.json").read_text())
    names = [p["name"] for p in m["patches"]]
    feats = set()
    for p in m["patches"]:
        for kind in ("ram", "late_ram"):
            ram = p.get(kind)
            if not ram:
                continue
            start, end = int(ram["start"], 16), int(ram["end"], 16)
            region = ram["region"]
            lo, hi, _ = RAM_REGIONS[region]
            edge = "start" if start - lo < EDGE else "end" if hi - end < EDGE else "middle"
            feats.add((kind, p["name"], region, edge))
    every_site = set().union(*(sites.get(n, set()) for n in names)) if names else set()
    for site in every_site:
        chain = [n for n in names if site in sites.get(n, set())]
        for a, b in zip(chain, chain[1:]):
            feats.add(("chain", a, b))
    end = data_end(ptf)                     # without the padding's 0xFF bytes
    feats.add(("padded", 0 < end % FLASH_SECTOR <= FLASH_PAGE))
    return names, feats


def cover_set(outputs: list[Path], sites: dict[str, set[int]], tested: list[Path] = ()) -> list[Path]:
    """Greedy set cover: the fewest mixes that between them have every case that the
    mixes in `tested` (tested anyway) don't."""
    feats = {}
    for f in outputs:
        feats[f] = mix_features(f, sites)[1]
    want = set().union(*feats.values())
    for f in tested:
        want -= mix_features(f, sites)[1]
    chosen = []
    while want:
        best = max(feats, key=lambda f: (len(feats[f] & want), -len(json.loads(
            f.with_suffix(".map.json").read_text())["patches"])))
        got = feats[best] & want
        if not got:
            break
        chosen.append(best)
        want -= got
    return chosen


# ---- preflight ----------------------------------------------------------------------

def stale_builds() -> list[str]:
    """Patches whose build/ files differ from prebuilt/ (patch_ptf.py uses build/ when present)."""
    stale = []
    for spec_path in sorted((ROOT / "patch").glob("*/patch.json")):
        try:
            spec, d = json.loads(spec_path.read_text()), spec_path.parent
        except ValueError:                     # preflight reports it
            continue
        for k in ("bin", "sym", "rel"):
            built, shipped = spec.get(k), spec.get("prebuilt_" + k)
            if not (built and shipped and (d / built).is_file() and (d / shipped).is_file()):
                continue
            a, b = (d / built).read_bytes(), (d / shipped).read_bytes()
            if k != "bin":                     # text: line endings don't count
                a, b = a.replace(b"\r\n", b"\n"), b.replace(b"\r\n", b"\n")
            if a != b:
                stale.append(f"patch/{d.name}/{built}")
    return stale


def preflight(tier: str) -> list[str]:
    """Problems found in seconds, which otherwise surface late in a long run."""
    problems = []
    for f in sorted([*(ROOT / "patch").rglob("*.json"), *(ROOT / "web").glob("*.json")]):
        if "build" in f.relative_to(ROOT).parts or "prebuilt" in f.relative_to(ROOT).parts:
            continue
        try:
            json.loads(f.read_text())
        except ValueError as e:
            problems.append(f"{f.relative_to(ROOT)}: not valid JSON ({e})")
    if tier != "quick":
        for f in [*sorted((ROOT / "web").glob("*.js")), TOOLS / "web_patcher_node.mjs"]:
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
            if r.returncode:
                problems.append(f"{f.relative_to(ROOT)}: node --check failed\n{r.stderr.strip()}")
    stale = stale_builds()
    if stale:
        what = ("build/ differs from prebuilt/ (patch_ptf.py uses build/), so the tests would not test what "
                "users get: copy it to prebuilt/ when shipping it, or move build/ aside:\n  " + "\n  ".join(stale))
        if tier == "quick":
            print("note: " + what, flush=True)
        else:
            problems.append(what)
    return problems


# ---- running ------------------------------------------------------------------------

def own_files(patch: str) -> list[Path] | None:
    """The files a patch's code comes from (None for a test without its own patch folder)."""
    d = ROOT / "patch" / patch
    if not (d / "patch.json").is_file():
        return None
    return [d / "patch.json", *d.glob("prebuilt/*"), *d.glob("build/*.*")]


class Runner:
    def __init__(self, stock: Path, out: Path, workers: int, use_cache: bool, full_by_own: bool = False):
        self.stock, self.out, self.workers, self.use_cache = stock, out, workers, use_cache
        self.full_by_own = full_by_own       # release: full mode only when the patch's own files changed
        self.full_skipped: list[str] = []
        self.cache = load_cache()
        self.cache.setdefault("passed", {})
        self.cache.setdefault("seconds", {})
        self.emu_digest = digest_files([*EMU.rglob("*.py"), TOOLS / "make_headroom_project.py"])
        self.stock_sha = sha256_file(stock)
        self.lock = threading.Lock()
        self.results: list[tuple[str, str, bool, str]] = []   # (what, test, ok, note)
        self.stopped = ""                    # why the jobs not yet started were dropped
        self.dropped = 0
        (out / "logs").mkdir(parents=True, exist_ok=True)

    def save(self):
        with self.lock:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(self.cache, indent=1, sort_keys=True))

    def estimate(self, kind: str) -> float:
        return self.cache["seconds"].get(kind, MEASURED_SECONDS.get(kind, DEFAULT_SECONDS))

    def key(self, *parts: str) -> str:
        return hashlib.sha256("|".join((*parts, self.emu_digest, self.stock_sha)).encode()).hexdigest()

    def run_command(self, label: str, kind: str, key: str, cmd: list[str], log: Path, also: tuple = ()) -> bool:
        if self.use_cache and key in self.cache["passed"]:
            with self.lock:
                self.results.append((label, kind, True, "cached"))
            return True
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        dt = time.time() - t0
        text = r.stdout + r.stderr
        log.write_text(text)
        npass, nfail = len(re.findall(r"^\s*PASS\b", text, re.M)), len(re.findall(r"^\s*FAIL\b", text, re.M))
        ok = r.returncode == 0 and nfail == 0 and npass > 0
        note = f"pass {npass} fail {nfail}" + ("" if r.returncode == 0 else f" exit {r.returncode}")
        with self.lock:
            self.results.append((label, kind, ok, note))
            old = self.cache["seconds"].get(kind)
            self.cache["seconds"][kind] = dt if old is None else 0.7 * old + 0.3 * dt
            if ok:
                for k in (key, *also):
                    self.cache["passed"][k] = {"what": label, "test": kind, "when": time.strftime("%Y-%m-%d %H:%M")}
        self.save()
        return ok

    def emu_job(self, ptf: Path, patch: str, full: bool) -> tuple:
        also = ()
        if full and patch in QUICK_MODE and self.full_by_own and (own := own_files(patch)):
            own_key = self.key("own", digest_files(own), patch, "full")
            if not (self.use_cache and own_key in self.cache["passed"]):
                also = (own_key,)                    # a full pass holds for these files
            elif not (self.use_cache and self.key(sha256_file(ptf), patch, "full") in self.cache["passed"]):
                full = False
                self.full_skipped.append(f"{ptf.stem}: {patch}")
        mode = "full" if (full or patch not in QUICK_MODE) else "quick"
        kind = f"{patch}" + (f" ({mode})" if patch in QUICK_MODE else "")
        cmd = [PY, "-I", str(EMU / f"test_{patch}.py"), str(ptf)]
        if patch in STOCK_ARG:
            cmd.append(str(self.stock))
        if mode == "quick":
            cmd.append("--quick")
        key = self.key(sha256_file(ptf), patch, mode)
        log = self.out / "logs" / f"{ptf.stem}.{patch}.{mode}.txt"
        return (f"{ptf.stem}", kind, key, cmd, log, also)

    def run_all(self, jobs: list[tuple], stop_kinds: set[str] = frozenset()) -> int:
        """Run jobs (label, kind, key, cmd, log) in parallel, longest first; print progress.
        A failed job of a kind in stop_kinds drops the jobs not yet started."""
        todo = [j for j in jobs if not (self.use_cache and j[2] in self.cache["passed"])]
        cached = len(jobs) - len(todo)
        for j in jobs:
            if j not in todo:
                self.results.append((j[0], j[1], True, "cached"))
                for k in j[5] if len(j) > 5 else ():
                    self.cache["passed"].setdefault(k, self.cache["passed"][j[2]])
        self.save()
        todo.sort(key=lambda j: (j[1] not in stop_kinds, -self.estimate(j[1])))
        total = sum(self.estimate(j[1]) for j in todo)
        longest = max((self.estimate(j[1]) for j in todo), default=0)
        # jobs run 1.5x slower with every core busy (measured); one long job can't be shared out
        wall = 1.5 * max(total / self.workers, longest)
        print(f"{len(jobs)} jobs: {cached} already passed (cache), {len(todo)} to run, "
              f"about {wall / 60:.0f} min with {self.workers} at a time", flush=True)
        left = [total]
        done = [0]
        failed = [0]

        def one(j):
            if self.stopped:
                with self.lock:
                    self.dropped += 1
                return False
            ok = self.run_command(*j)
            with self.lock:
                if not ok and j[1] in stop_kinds:
                    self.stopped = f"{j[1]} failed"
                done[0] += 1
                failed[0] += not ok
                left[0] -= self.estimate(j[1])
                print(f"  [{done[0]}/{len(todo)}] {'ok  ' if ok else 'FAIL'} {j[0]}: {j[1]}  "
                      f"(about {max(left[0], 0) / self.workers / 60:.0f} min left)", flush=True)
            return ok

        with ThreadPoolExecutor(self.workers) as ex:
            list(ex.map(one, todo))
        if self.dropped:
            print(f"  {self.stopped}: {self.dropped} jobs not started", flush=True)
        return failed[0]


def build(stock: Path, spec: Path, out: Path) -> Path:
    r = subprocess.run([PY, "-I", str(TOOLS / "patch_ptf.py"), str(stock), str(spec), str(out)],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"building {spec} failed:\n{r.stdout}{r.stderr}")
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = sys.argv[1:]

    def opt(name, default):
        return opts[opts.index(name) + 1] if name in opts else default

    if not args:
        print(__doc__)
        return 2
    stock = Path(args[0]).resolve()
    tier = opt("--tier", "quick")
    if tier not in ("quick", "release", "deep"):
        sys.exit("--tier is quick, release or deep")
    update = "--update" in opts
    if update and tier == "quick":
        sys.exit("--update needs --tier release or deep (the quick tier doesn't run the web harness)")
    mixes_of = [n for n in opt("--mixes-of", "").split(",") if n]
    if mixes_of and tier == "quick":
        sys.exit("--mixes-of needs --tier release or deep (the mixes are the web harness's outputs)")
    workers = int(opt("--jobs", max(1, (os.cpu_count() or 2) - 1)))
    out = Path(opt("--out", ROOT / "build" / "testrun")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    problems = preflight(tier)
    if problems:
        print("preflight failed (nothing run):\n" + "\n".join(f"  {p}" for p in problems))
        return 1
    r = Runner(stock, out, workers, "--no-cache" not in opts, full_by_own=tier == "release")
    t0 = time.time()
    patches = mods()
    sites = {name: hook_sites(spec) for name, spec in patches}
    names = [n for n, _ in patches]

    # 1. tools that check placement and bytes (no emulator)
    # web/expected.json only matters to the harness, which checks it (and --update rewrites it)
    expected = ROOT / "web" / "expected.json"
    tool_digest = digest_files([TOOLS / "patch_ptf.py", TOOLS / "ptf.py", TOOLS / "test_layout.py",
                                TOOLS / "test_web_patcher.py", TOOLS / "web_patcher_node.mjs",
                                *(f for f in (ROOT / "web").glob("*") if f != expected),
                                *(ROOT / "patch").rglob("prebuilt/*"),
                                *(ROOT / "patch").rglob("patch.json"), *(ROOT / "patch").rglob("build/*.*")])
    pre = [("test_layout", "layout", r.key("layout", tool_digest),
            [PY, "-I", str(TOOLS / "test_layout.py"), str(stock), str(out / "layout")], out / "logs" / "layout.txt")]
    harness = out / "harness"
    if tier != "quick":
        hkey = r.key("harness", tool_digest, sha256_file(expected))
        if not (harness / "py").is_dir():           # the cover set needs its outputs even when cached
            r.cache["passed"].pop(hkey, None)
        pre.append(("test_web_patcher", "web harness", hkey,
                    [PY, "-I", str(TOOLS / "test_web_patcher.py"), str(stock), str(harness)] + (["--update"] if update else [])
                    + (["--no-cache"] if "--no-cache" in opts else []) + ["--jobs", str(workers)],
                    out / "logs" / "harness.txt"))
    def emu_jobs(targets: list[tuple[Path, bool]]) -> list[tuple]:
        """Every contained patch's emulator test on each (file, masterfx/synths in full mode)."""
        jobs = []
        for ptf, full in targets:
            contained = [p["name"] for p in json.loads(ptf.with_suffix(".map.json").read_text())["patches"]]
            tests = [t for p in contained for t in [p] + EXTRA_TESTS.get(p, [])]
            jobs += [r.emu_job(ptf, t, full) for t in tests if (EMU / f"test_{t}.py").exists()]
            jobs.append(r.emu_job(ptf, "bundle_cost", full))     # the whole firmware against stock
        return jobs

    # 2. each patch alone and patch/all, built here, so their emulator tests (the full
    # synths run on patch/all is the longest job) run alongside the checks above
    builds = out / "builds"
    builds.mkdir(exist_ok=True)
    first = [(build(stock, spec, builds / f"{name}.ptf"), tier == "deep") for name, spec in patches]
    first.append((build(stock, ROOT / "patch" / "all" / "patch.json", builds / "all.ptf"), tier != "quick"))
    first += [(build(stock, ROOT / spec, builds / f"{name}.ptf"), False) for name, spec in DIAGNOSTICS]
    jobs = pre + emu_jobs(first)
    if "--dry-run" in opts:
        for j in jobs:
            print(" ", j[0], j[1])
        return 0
    print(f"tier {tier}: layout{' and web patcher' if tier != 'quick' else ''} checks, and the emulator tests "
          f"on each patch alone and patch/all", flush=True)
    if r.full_skipped:
        print(f"  --quick instead of full mode (the patch's own files unchanged since a full pass): "
              f"{', '.join(r.full_skipped)}", flush=True)
    failed = r.run_all(pre[:1])                # the layout test alone first (under a minute)
    if failed:
        r.stopped = "layout failed"
    else:
        failed = r.run_all(jobs[1:], stop_kinds={"web harness"})
    if update and hkey in r.cache["passed"]:
        # a pass with --update leaves expected.json holding what the harness checked: the
        # pass holds for the rewritten file too
        r.cache["passed"][r.key("harness", tool_digest, sha256_file(expected))] = r.cache["passed"][hkey]
        r.save()

    # 3. release and deep: the cover set of web mixes and the bundles in other orders,
    # from the harness's outputs (byte-identical to the web patcher's, which it checks)
    if tier != "quick" and not r.stopped:
        py = harness / "py"
        singles = [py / f"sel-{n}.ptf" for n in names]
        # the bundles test_layout.py builds in other orders (not the chained copies of a
        # patch, which only test the patcher)
        extra = [f for f in py.glob("*.ptf") if not f.name.startswith("sel-") and
                 all(p["name"] in names for p in json.loads(f.with_suffix(".map.json").read_text())["patches"])]
        every = py / ("sel-" + "+".join(names) + ".ptf")
        mixes = sorted(f for f in py.glob("sel-*.ptf") if f not in singles and f != every)
        cover = cover_set(mixes, sites, [every])
        print(f"cover set: {len(cover)} of {len(mixes) + 1} mixes, plus {len(extra)} bundles in other orders",
              flush=True)
        (out / "cover_set.txt").write_text("".join(f"{f.stem}\n" for f in cover))
        if mixes_of:
            unknown = set(mixes_of) - set(names)
            if unknown:
                sys.exit(f"--mixes-of: no mod {', '.join(sorted(unknown))}")
            pick = [n for n in names if n in mixes_of]          # the web patcher's order
            chosen = [py / ("sel-" + "+".join(c) + ".ptf")
                      for k in range(2, len(pick) + 1) for c in itertools.combinations(pick, k)]
            extra += [f for f in chosen if f not in cover and f != every]
            print(f"--mixes-of: {len(chosen)} mixes of {', '.join(pick)}", flush=True)
        failed += r.run_all(emu_jobs([(f, tier == "deep") for f in cover + extra]))

    # 4. summary
    lines = [f"tier {tier}: {len(r.results)} jobs, {failed} failed, "
             f"{sum(1 for x in r.results if x[3] == 'cached')} from the cache, {(time.time() - t0) / 60:.1f} min"
             + (f"; stopped early ({r.stopped}), the rest not run" if r.stopped else "")]
    lines += [f"FAIL  {w}: {k}  {n}  (log in {out / 'logs'})" for w, k, ok, n in r.results if not ok]
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
