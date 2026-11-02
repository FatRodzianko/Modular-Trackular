"""Check that the web patcher (web/patcher.js) makes exactly what tools/patch_ptf.py makes.

  python tools/test_web_patcher.py <stock TrackerPlus_1.2.0.ptf> [out dir] [--update] [--no-cache]
                                   [--jobs N]

Needs Node (runs web/patcher.js through tools/web_patcher_node.mjs). It:
  - stages the site with web/stage.py, as the deployment does, and runs both patchers
    on the staged files (so both use the prebuilt patches);
  - checks patcher.js's constants (RAM regions, alignment, version text) equal
    patch_ptf.py's, and so do web/verify.js's (its own copy of the layout rules);
  - before patching anything, checks every selection's spec path, and every bundle's
    version text, is unique ignoring case (Windows and FAT ignore it, so two would
    overwrite each other or read the same in Config), and that the version texts are
    letters and digits; stops if not;
  - for every selection the page offers (each mod alone, patch/all, every other mix),
    plans it as the page does, patches it in JavaScript, patches the same spec with
    patch_ptf.py, and checks the .ptf files are byte-identical and the .map.json files
    equal; past MAX_COMPARE selections (more than 15 mods), patch_ptf.py builds only
    those compared() picks (every selection of 1 to 3 mods and of all but up to 2, then
    an even spread);
  - runs web/verify.js, the check the page runs before every download, on every
    selection and on the bundles in other orders: it must accept them all; and on
    builds broken on purpose (tools/verify_corrupt.mjs: a stock byte, a hook, a
    relocation, RAM out of its region or overlapping, a chain word, the version text,
    padding, the copy table, an ITCM stub, the .ptf text) made from each mod alone,
    patch/all and two mixes: it must reject every one;
  - does the same for the bundles tools/test_layout.py builds in other orders
    (reversed, masterfx first, every mod reversed, humanize with a chained copy);
  - does the same for ITCM hooks: condtrig placed first, and condtrig with a copy of
    itself on its ITCM sites;
  - does the same for a non-BL site ("replaces": "insns"): test_layout.py's slicethru
    copy, alone and in slicethru's place in patch/all;
  - does the same for tail calls ("tail": b.w sites): tracklen with a copy of itself on
    its two b.w sites;
  - checks both refuse the same things: another firmware version, a bundle with
    ramdiag, a shared site not chained by both, two patches passing different
    registers at one ITCM site, a non-BL site with "next" or a bad "replaces", a
    second patch on a non-BL site (plain or chained), a site overlapping another, a
    tail call with "replaces" or with "tail" other than true, "tail" on a BL site, a
    b.w site without "tail"; and
    that the port refuses a spec using a key it doesn't know (a patch_ptf.py feature
    it doesn't have yet);
  - runs tools/test_layout.py's checks (placement, RAM, relocation, chains, version
    text) on every selection's output;
  - checks the single-mod and patch/all outputs against the hardware-tested SHA-256s
    in docs/PATCHING.md and web/manifest.json;
  - checks web/expected.json (the hashes the page accepts) holds exactly the hashes
    of the selections both patchers built (all of them, up to MAX_COMPARE); --update
    rewrites it.
Outputs: <out dir>/py, each selection's .map.json, and index.json: for each selection that
passed, its files' name, its .ptf's SHA-256, where its image ends (the last non-0xFF byte,
for the test runner's cover set) and the spec it was patched from in <out dir>/site. A
selection's files are named sel-<its spec's folder> (sel-swing, sel-all, sel-mix-1AU), not
by its mods, so no name grows with the number of mods; a check fails if any file written
under <out dir> is more than MAX_PATH characters below it. A selection's
.ptf (4.6 MB; 88 GB for fourteen mods' 16,383) is deleted once it passed: the test runner
rebuilds the few mixes its emulator tests run on, and checks each against its SHA-256.
A selection that failed keeps its .ptf, for a look. The bundles in other orders keep
theirs. <out dir>/js holds the web patcher's .map.json files (its .ptf files only where
they differ).

Both patchers run in parallel, --jobs N processes at once (default one per core): the web
patcher in Node processes, patch_ptf.py in a pool of worker processes that parse the
stock firmware once (patch_ptf.py's own code, called in the worker instead of in a new
interpreter per selection).

In memory: neither patcher reads back the 6 MB .ptf it just wrote (most of their time), and
patch_ptf.py's text comes from fast_encode (the stock records' lines encoded once per
worker), its parsed output kept for its read-back and the layout checks. Every FULL_EVERY-th
selection, each mod alone and patch/all are built in full instead, as the patchers run on
their own (their encoders, their read-backs, the layout checks reading the file), and
fast_encode must give the same text on each; the test runner also rebuilds the cover mixes
with patch_ptf.py itself and checks their SHA-256. Profiled 2026-11-02: 1.5 s and 0.27 s
per mix in full, 0.16 s and 0.11 s in memory.

The selections' cache (<out dir>/selections.json): a selection's key is a digest of the
patcher code (both patchers, the page's check, test_layout.py and this file), the stock firmware, the spec
the page plans for it and every patch file that spec uses. A selection whose key passed
before (byte-identical or past MAX_COMPARE, layout checks passed, web/verify.js
accepted it), and whose .map.json in <out dir>/py is
still as written, isn't patched or checked again: a change to one patch reruns only the
selections that contain it. --no-cache runs them all. The bundles in other orders, the
refusals and the broken builds always run.
"""
from __future__ import annotations

import contextlib
import dataclasses
import functools
import hashlib
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "web"))
import patch_ptf  # noqa: E402
import stage  # noqa: E402
import test_layout  # noqa: E402
from ptf import _line as ptf_line, read_ptf  # noqa: E402

failures: list[str] = []
PROCS = os.cpu_count() or 8
CODE = [ROOT / "tools" / f for f in ("patch_ptf.py", "ptf.py", "test_layout.py", "test_web_patcher.py",
                                     "web_patcher_node.mjs", "verify_corrupt.mjs")] \
    + [ROOT / "web" / f for f in ("patcher.js", "verify.js")]
KEEP = ("py", "selections.json")              # what a run keeps from the one before
MAX_PATH = 80                                 # characters below <out dir>, for any file written
FULL_EVERY = 64                               # every 64th selection built as patch_ptf.py runs alone
MAX_COMPARE = 32767                           # selections patch_ptf.py also builds (all, to 15 mods)


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def run_js(site: Path, stock: Path, jobs: list[dict], out: Path, procs: int = 1) -> tuple[dict, dict[str, dict]]:
    """The jobs in `procs` Node processes at once, each taking every procs-th job. Each
    job writes only its own files (its outputs, and its own mix's spec)."""
    chunks = [jobs[i::procs] for i in range(min(procs, len(jobs)))] or [[]]

    def node(i: int) -> subprocess.CompletedProcess:
        jobs_path = out.parent / f"jobs-{out.name}-{i}.json"
        jobs_path.write_text(json.dumps(chunks[i], indent=1))
        return subprocess.run(["node", str(ROOT / "tools" / "web_patcher_node.mjs"), str(site), str(stock),
                               str(jobs_path), str(out)], capture_output=True, text=True)

    with ThreadPoolExecutor(len(chunks)) as pool:
        runs = list(pool.map(node, range(len(chunks))))
    results = {}
    for r in runs:
        if r.returncode:
            sys.exit("node failed:\n" + r.stdout + r.stderr)
        lines = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
        consts = lines[0]["constants"]
        results.update((d["name"], d) for d in lines[1:])
    return consts, {j["name"]: results[j["name"]] for j in jobs}


# ---- the Python side, in worker processes ----------------------------------------------

_stock_img = b""
_stock_lines: dict[tuple[int, bytes], str] = {}  # a stock data record (address, bytes) -> its line
_read_cached = None
_written: dict[Path, object] = {}                # .ptf just written -> its parsed form (in memory)


def _init_worker(stock: str) -> None:
    """Parse the stock firmware once per worker, encode its data records' lines once, and
    load each patch's files once for the layout checks. patch_ptf.py gets a fresh copy of
    the parsed stock on every call (it replaces the copy's image and record list, as it
    would a newly read file's)."""
    global _stock_img, _read_cached
    stock_path = Path(stock)
    parsed = read_ptf(stock_path)
    _stock_img = bytes(parsed.image)
    for r in parsed.records:
        if r.kind == 0:
            _stock_lines[(r.addr, r.payload)] = ptf_line(len(r.payload), r.addr & 0xFFFF, 0, r.payload)

    def read_cached(path: Path):
        if Path(path) == stock_path:
            return dataclasses.replace(parsed, records=list(parsed.records), image=bytearray(parsed.image))
        return read_ptf(path)

    _read_cached = read_cached
    patch_ptf.read_ptf = read_cached
    test_layout.patch_files = functools.lru_cache(None)(test_layout.patch_files)


def fast_encode(p) -> str:
    """ptf.encode_ptf's text, faster: a data record whose address and bytes are a stock
    record's reuses that record's line, and the 16-byte records after the stock image (the
    patches) are encoded together with numpy. The harness checks it equals encode_ptf on
    the selections it builds in full (FULL_EVERY)."""
    lines: list[str | None] = []
    tail = []
    for r in p.records:
        if r.kind == 0:
            off = r.addr - p.base
            payload = bytes(p.image[off:off + len(r.payload)])
            line = _stock_lines.get((r.addr, payload))
            if line is None:
                if len(payload) == 16:
                    tail.append((len(lines), r.addr & 0xFFFF, payload))
                    lines.append(None)
                    continue
                line = ptf_line(len(payload), r.addr & 0xFFFF, 0, payload)
            lines.append(line)
        else:
            lines.append(ptf_line(len(r.payload), r.addr, r.kind, r.payload))
    if tail:
        raw = np.zeros((len(tail), 21), np.uint8)
        raw[:, 0] = 16
        addrs = np.array([t[1] for t in tail], np.uint32)
        raw[:, 1], raw[:, 2] = addrs >> 8, addrs & 0xFF
        raw[:, 4:20] = np.frombuffer(b"".join(t[2] for t in tail), np.uint8).reshape(len(tail), 16)
        raw[:, 20] = (-raw[:, :20].sum(axis=1, dtype=np.int64)) & 0xFF
        text = raw.tobytes().hex().upper()
        for i, (pos, _, _) in enumerate(tail):
            lines[pos] = ":" + text[42 * i:42 * i + 42]
    return "\r\n".join(lines) + "\r\n"


def _encode_kept(p) -> str:
    """patch_ptf.py's encoder in memory: fast_encode, keeping the parsed output for its
    read-back and the layout checks instead of reading the 6 MB file back."""
    text = fast_encode(p)
    _written[Path(_argv_out[0])] = dataclasses.replace(p, records=list(p.records), image=bytearray(p.image))
    return text


_argv_out = [""]


def _read_kept(path: Path):
    return _written[Path(path)] if Path(path) in _written else _read_cached(path)


def _py_job(site: Path, name: str, spec: str | None, stock: Path, outdir: Path, compare: bool,
            verify: tuple | None, full: bool = True, python: bool = True) -> SimpleNamespace | None:
    """patch_ptf.py on one spec; then, if asked, the comparison with the web patcher's
    output (whose .ptf is deleted when it's identical) and test_layout.py's checks.
    Not `python` (a selection past MAX_COMPARE): no patch_ptf.py, only the web patcher's
    output, which web/verify.js checked; its map is kept and its .ptf deleted, as for one
    that passed.
    Not `full`: in memory (fast_encode, and the output's parsed form kept for patch_ptf.py's
    read-back and the layout checks, not read from the file). `full`: as patch_ptf.py runs
    on its own, and fast_encode's text compared with its encoder's."""
    if not spec:
        return None
    out = outdir / "py" / f"{name}.ptf"
    if not python:
        a = outdir / "js" / f"{name}.ptf"
        shutil.copyfile(a.with_suffix(".map.json"), out.with_suffix(".map.json"))
        r = SimpleNamespace(returncode=0, stdout="", stderr="", same=None, same_map=None, layout_checked=False,
                            layout_failed=0, layout_log="", full=None, fast_same=None, compared=False,
                            end=data_end(a), map=hashlib.sha256(a.with_suffix(".map.json").read_bytes()).hexdigest())
        a.unlink()
        return r
    so, se = io.StringIO(), io.StringIO()
    argv, sys.argv = sys.argv, ["patch_ptf.py", str(stock), str(site / spec), str(out)]
    fast_same = None
    real_encode = patch_ptf.encode_ptf
    if full:
        def encode_both(p):
            nonlocal fast_same
            text = real_encode(p)
            fast_same = fast_encode(p) == text
            return text
        patch_ptf.encode_ptf = encode_both
    else:
        _argv_out[0] = str(out)
        patch_ptf.encode_ptf = _encode_kept
        patch_ptf.read_ptf = _read_kept
    try:
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            rc = patch_ptf.main()
    except SystemExit as e:                       # as the interpreter reports sys.exit()
        rc = e.code if isinstance(e.code, int) else 0 if e.code is None else 1
        if not isinstance(e.code, (int, type(None))):
            se.write(f"{e.code}\n")
    except Exception:
        rc = 1
        se.write(traceback.format_exc())
    finally:
        sys.argv = argv
        patch_ptf.encode_ptf = real_encode
        patch_ptf.read_ptf = _read_cached
    kept = _written.pop(out, None)
    r = SimpleNamespace(returncode=rc, stdout=so.getvalue(), stderr=se.getvalue(), same=None, same_map=None,
                        layout_checked=False, layout_failed=0, layout_log="", end=None, map=None, full=full,
                        fast_same=fast_same, compared=True)
    if rc:
        return r
    if compare:
        a = outdir / "js" / f"{name}.ptf"
        r.same = a.read_bytes() == out.read_bytes()
        r.same_map = a.with_suffix(".map.json").read_text() == out.with_suffix(".map.json").read_text()
        if r.same:
            a.unlink()
    if verify:
        key, specs, version = verify
        n0 = len(test_layout.failures)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            test_layout.verify(key, out, specs, version, _stock_img, parsed=kept)
        r.layout_checked = True
        r.layout_failed = len(test_layout.failures) - n0
        r.layout_log = buf.getvalue() if r.layout_failed else ""
    if name.startswith("sel-") and r.same and r.same_map and r.layout_checked and not r.layout_failed:
        r.end = data_end(out)
        r.map = hashlib.sha256(out.with_suffix(".map.json").read_bytes()).hexdigest()
        out.unlink()                              # passed: the index keeps what the test runner needs
    return r


def compared(sel_jobs: list[dict], budget: int = MAX_COMPARE) -> set[str]:
    """The selections patch_ptf.py builds too, byte for byte against the web patcher's:
    all of them while they fit in `budget`. Past it, every selection of one to three mods
    (so every pair and triple of mods that share a hook) and of all mods but up to two,
    then an even spread of the rest, the same ones each run. Every selection, compared or
    not, is checked by web/verify.js, as the page checks it."""
    names = [sel["name"] for sel in sel_jobs]
    if len(names) <= budget:
        return set(names)
    most = max(len(sel["ids"]) for sel in sel_jobs)
    must = [sel["name"] for sel in sel_jobs if len(sel["ids"]) <= 3 or len(sel["ids"]) >= most - 2]
    set_must = set(must)
    rest = [n for n in names if n not in set_must]
    room = max(0, budget - len(must))
    return set_must | {rest[i * len(rest) // room] for i in range(room)}


def data_end(ptf: Path) -> int:
    """Address after the image's last non-0xFF byte, read from the end of the Intel HEX:
    only its last 64 KB, more if the answer isn't in them."""
    size, chunk = ptf.stat().st_size, 1 << 16
    with ptf.open("rb") as f:
        while True:
            start = max(0, size - chunk)
            f.seek(start)
            lines = f.read().splitlines()[1 if start else 0:]     # the first line may be cut
            last = None
            for line in reversed(lines):
                n, addr, kind = int(line[1:3], 16), int(line[3:7], 16), int(line[7:9], 16)
                data = bytes.fromhex(line[9:9 + 2 * n].decode())
                if kind == 0 and last is None and data.rstrip(b"\xff"):
                    last = addr + len(data.rstrip(b"\xff"))
                elif kind == 4 and last is not None:
                    return (int.from_bytes(data, "big") << 16) + last
            if not start:
                raise ValueError(f"{ptf}: no data")
            chunk *= 4


@functools.lru_cache(None)
def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "-"


def selection_key(base: str, site: Path, plan: dict) -> str:
    """The selections' cache key: `base` (the code and the stock), the planned spec, and
    each spec it includes with the patch files it names."""
    h = hashlib.sha256(f"{base}|{plan['key']}|{plan['kind']}".encode())
    top = site / plan["spec"]
    specs = [top] + [top.parent / inc for inc in json.loads(top.read_text()).get("include", [])]
    for sp in specs:
        h.update(f"|{sp.relative_to(site).as_posix()}:{file_sha(sp)}".encode())
        spec = json.loads(sp.read_text())
        for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel"):
            if spec.get(k):
                h.update(f"|{k}:{file_sha(sp.parent / spec[k])}".encode())
    return h.hexdigest()


def outputs_as_written(py: Path, name: str, entry: dict) -> bool:
    """A cached selection's map (the cover set reads it) is the one it passed with."""
    return "map" in entry and file_sha(py / f"{name}.map.json") == entry["map"]


def tested_hashes() -> dict[str, tuple[str, bool]]:
    """version text -> (SHA-256, tested on hardware), from the table in docs/PATCHING.md."""
    rows = re.findall(r"^\|[^|]*\| `TrackerPlus_(\w+)\.ptf` \|[^|]*\| ([^|]*) \| `([0-9a-f]{64})` \|$",
                      (ROOT / "docs" / "PATCHING.md").read_text(), re.M)
    return {code: (sha, hw.startswith("tested")) for code, hw, sha in rows}


def name_problems(plans: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Selections whose spec paths or version texts match ignoring case (Windows and FAT
    ignore it), and version texts that aren't 1 to VERSION_MAX letters and digits, or
    exactly VERSION_LONG. Only
    bundles (mixes and patch/all) have a version text in their spec; a single mod's is in
    its code."""
    clashes = []
    for field in ("spec", "version"):
        seen = {}
        for n, pl in plans.items():
            v = pl.get(field)
            if pl["ok"] and v is not None:
                if v.lower() in seen:
                    clashes.append(f"{n} and {seen[v.lower()]}: {field} {v}")
                seen.setdefault(v.lower(), n)
    bad = sorted({str(pl["version"]) for pl in plans.values() if pl["ok"] and pl.get("version") is not None
                  and not re.fullmatch(r"[0-9A-Za-z]{1,%d}|[0-9A-Za-z]{%d}" % (patch_ptf.VERSION_MAX, patch_ptf.VERSION_LONG),
                                       str(pl["version"]))})
    return clashes, bad


def main() -> int:
    opts = sys.argv[1:]
    procs = int(opts[opts.index("--jobs") + 1]) if "--jobs" in opts else PROCS
    args = [a for i, a in enumerate(opts) if not a.startswith("--") and (i == 0 or opts[i - 1] != "--jobs")]
    update = "--update" in sys.argv
    use_cache = "--no-cache" not in sys.argv
    stock = Path(args[0]).resolve()
    outdir = Path(args[1]).resolve() if len(args) > 1 else Path(tempfile.mkdtemp())
    cache_path = outdir / "selections.json"
    cache = {}
    if use_cache and cache_path.is_file():
        with contextlib.suppress(ValueError):
            cache = json.loads(cache_path.read_text())
    if outdir.exists():
        for child in outdir.iterdir():
            if use_cache and child.name in KEEP:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    outdir.mkdir(parents=True, exist_ok=True)
    py_dir = outdir / "py"
    py_dir.mkdir(exist_ok=True)
    for f in py_dir.iterdir():                    # only selections' maps are kept
        if not f.name.startswith("sel-") or f.suffix == ".ptf":
            f.unlink()
    site = outdir / "site"
    r = subprocess.run([sys.executable, "-I", str(ROOT / "web" / "stage.py"), str(site), "--no-check"],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("stage failed:\n" + r.stdout + r.stderr)
    print(r.stdout.strip())
    manifest = json.loads((site / "manifest.json").read_text())
    all_spec = json.loads((site / manifest["all"]).read_text())
    ids = [p["id"] for p in manifest["patches"]]

    # A second tree with the extra specs only the tests use, so the staged site stays as
    # deployed.
    extra = outdir / "site-extra"
    shutil.copytree(site, extra)
    for name in ("ramdiag",):                    # not on the page
        src = ROOT / "patch" / name
        (extra / "patch" / name / "prebuilt").mkdir(parents=True)
        shutil.copyfile(src / "patch.json", extra / "patch" / name / "patch.json")
        for f in (src / "prebuilt").iterdir():
            shutil.copyfile(f, extra / "patch" / name / "prebuilt" / f.name)

    def bundle(name: str, inc: list[str], version: str) -> str:
        spec = dict(all_spec, name=name, include=[f"../{i}/patch.json" for i in inc], version=version)
        path = f"patch/{name}/patch.json"
        (extra / path).parent.mkdir(parents=True, exist_ok=True)
        (extra / path).write_text(json.dumps(spec, indent=2))
        return path

    hz_spec = json.loads((extra / "patch/humanize/patch.json").read_text())
    copy = dict(hz_spec, name="humanize-copy",
                branches=[b for b in hz_spec["branches"] if "next" in b],
                words=[w for w in hz_spec["words"] if "next" in w])
    for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel"):
        copy[k] = "../humanize/" + hz_spec[k]
    (extra / "patch/humanize-copy").mkdir()
    (extra / "patch/humanize-copy/patch.json").write_text(json.dumps(copy, indent=2))
    unchained = dict(copy, name="humanize-unchained",
                     words=[{k: v for k, v in w.items() if k != "next"} for w in copy["words"]])
    (extra / "patch/humanize-unchained").mkdir()
    (extra / "patch/humanize-unchained/patch.json").write_text(json.dumps(unchained, indent=2))
    ct_spec = json.loads((extra / "patch/condtrig/patch.json").read_text())
    ct_files = {k: "../condtrig/" + ct_spec[k] for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel")}
    ct_copy = dict(ct_spec, name="condtrig-copy", branches=[], words=[], **ct_files)
    (extra / "patch/condtrig-copy").mkdir()
    (extra / "patch/condtrig-copy/patch.json").write_text(json.dumps(ct_copy, indent=2))
    ct_args = dict(ct_copy, name="condtrig-args", itcm_branches=[dict(b, args=["r4"]) for b in ct_copy["itcm_branches"]])
    (extra / "patch/condtrig-args").mkdir()
    (extra / "patch/condtrig-args/patch.json").write_text(json.dumps(ct_args, indent=2))
    bt_spec = json.loads((extra / "patch/slicethru/patch.json").read_text())
    bt_files = {k: "../slicethru/" + bt_spec[k] for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel")}
    no_replaces = {k: v for k, v in test_layout.INSNS_SITE.items() if k != "replaces"}
    for name, sites, own in (("slicethru-insns", [test_layout.INSNS_SITE], True),
                             ("insns-next", [dict(test_layout.INSNS_SITE, next="mt_bt_bs_note_on")], True),
                             ("insns-bad", [dict(test_layout.INSNS_SITE, replaces="bl")], True),
                             ("insns-second", [test_layout.INSNS_SITE], False),
                             ("insns-chained", [dict(no_replaces, next="mt_bt_bs_note_on")], False),
                             ("insns-overlap", [test_layout.INSNS_OVERLAP], False)):
        ats = {int(br["at"], 16) for br in sites}       # slicethru's own site there gives way
        kept = [br for br in bt_spec["branches"] if int(br["at"], 16) not in ats] if own else []
        spec = dict(bt_spec, name=name, branches=kept + sites,
                    words=bt_spec["words"] if own else [], **bt_files)
        (extra / "patch" / name).mkdir()
        (extra / "patch" / name / "patch.json").write_text(json.dumps(spec, indent=2))

    pm_spec = json.loads((extra / "patch/tracklen/patch.json").read_text())
    pm_files = {k: "../tracklen/" + pm_spec[k] for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel")}
    pm_tails = [br for br in pm_spec["branches"] if br.get("tail")]
    pm_bl = next(br for br in pm_spec["branches"] if "next" in br and not br.get("tail"))
    tail = pm_tails[0]
    for name, branches in (("tracklen-tails", pm_tails),
                           ("tail-insns", [dict({k: v for k, v in tail.items() if k != "next"}, replaces="insns")]),
                           ("tail-yes", [dict(tail, tail="yes")]),
                           ("tail-on-bl", [dict(pm_bl, tail=True)]),
                           ("bw-plain", [{k: v for k, v in tail.items() if k != "tail"}])):
        (extra / "patch" / name).mkdir()
        (extra / "patch" / name / "patch.json").write_text(json.dumps(
            dict(pm_spec, name=name, branches=branches, words=[], itcm_branches=[], **pm_files), indent=2))

    other_orders = {
        "reversed": bundle("reversed", ["interpolate", "humanize", "colorsong"], "MTR"),
        "with-masterfx-reversed": bundle("with-masterfx-reversed", ["masterfx", "interpolate", "humanize", "colorsong"], "MTY"),
        "every-reversed": bundle("every-reversed", list(reversed(ids)), "MTT"),
        "chain": bundle("chain", ["humanize", "humanize-copy"], "CHN"),
        "condtrig-first": bundle("condtrig-first", ["condtrig", "swing", "visualizer", "interpolate"], "MCF"),
        "itcm-chain": bundle("itcm-chain", ["condtrig", "condtrig-copy"], "CHI"),
        "slicethru-insns": "patch/slicethru-insns/patch.json",
        "all-insns": bundle("all-insns", [i if i != "slicethru" else "slicethru-insns" for i in ids], "INS"),
        "tail-chain": bundle("tail-chain", ["tracklen", "tracklen-tails"], "PTC"),
        "long-version": bundle("long-version", ["colorsong", "humanize"], "K7Q2M"),
    }
    refusals = {
        "with-ramdiag": (bundle("with-ramdiag", ["colorsong", "ramdiag"], "BAD"), stock, "standalone"),
        "unchained": (bundle("unchained", ["humanize", "humanize-unchained"], "BAD"), stock, "not chained by both"),
        "itcm-args": (bundle("itcm-args", ["condtrig", "condtrig-args"], "BAD"), stock, 'different "args"'),
        "insns-next": ("patch/insns-next/patch.json", stock, "nothing to chain"),
        "insns-bad": ("patch/insns-bad/patch.json", stock, '"replaces" takes "insns"'),
        "insns-second": (bundle("insns-second-bundle", ["slicethru-insns", "insns-second"], "BAD"), stock,
                         "not chained by both"),
        "insns-chained": (bundle("insns-chained-bundle", ["slicethru-insns", "insns-chained"], "BAD"), stock,
                          "not chained by both"),
        "insns-overlap": (bundle("insns-overlap-bundle", ["slicethru-insns", "insns-overlap"], "BAD"), stock,
                          "overlaps 0x60035522"),
        "tail-insns": ("patch/tail-insns/patch.json", stock, "can't be a tail call"),
        "tail-yes": ("patch/tail-yes/patch.json", stock, '"tail" takes true'),
        "tail-on-bl": ("patch/tail-on-bl/patch.json", stock, "is not a b.w"),
        "bw-plain": ("patch/bw-plain/patch.json", stock, 'a b.w site takes "tail": true'),
    }
    sw_spec = json.loads((extra / "patch/swing/patch.json").read_text())
    unported = dict(sw_spec, name="swing-future", future_hooks=[],
                    **{k: "../swing/" + sw_spec[k] for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel")})
    (extra / "patch/swing-future").mkdir()
    (extra / "patch/swing-future/patch.json").write_text(json.dumps(unported, indent=2))
    older = sorted(p for p in stock.parent.glob("TrackerPlus_*.ptf") if p.name != stock.name)
    if older:
        refusals["other-firmware"] = ("patch/swing/patch.json", older[0], "does not match")

    print("constants")
    consts, _ = run_js(site, stock, [], outdir / "js")
    py_regions = {k: [s, e, sorted(f)] for k, (s, e, f) in patch_ptf.RAM_REGIONS.items()}
    js_regions = {k: [s, e, sorted(f)] for k, (s, e, f) in consts["RAM_REGIONS"].items()}
    check(js_regions == py_regions and list(consts["RAM_REGIONS"]) == list(patch_ptf.RAM_REGIONS),
          "RAM_REGIONS (addresses, flags, order) as in patch_ptf.py")
    check(consts["RAM_DEFAULT_ORDER"] == patch_ptf.RAM_DEFAULT_ORDER, "RAM_DEFAULT_ORDER as in patch_ptf.py")
    check(consts["ALIGN"] == patch_ptf.ALIGN and consts["VERSION_MAX"] == patch_ptf.VERSION_MAX
          and consts["VERSION_PREFIX"].encode() == patch_ptf.VERSION_PREFIX
          and consts["VERSION_LONG"] == patch_ptf.VERSION_LONG
          and consts["VERSION_LONG_PREFIX"].encode() == patch_ptf.VERSION_LONG_PREFIX,
          "ALIGN, VERSION_MAX, VERSION_PREFIX, VERSION_LONG, VERSION_LONG_PREFIX")
    v = consts["VERIFY"]                          # web/verify.js: its own copy of the rules
    v_regions = {k: [s, e, sorted(f)] for k, (s, e, f) in v["RAM_REGIONS"].items()}
    check(v_regions == py_regions and list(v["RAM_REGIONS"]) == list(patch_ptf.RAM_REGIONS)
          and v["RAM_DEFAULT_ORDER"] == patch_ptf.RAM_DEFAULT_ORDER and v["ALIGN"] == patch_ptf.ALIGN
          and v["FLASH_SECTOR"] == patch_ptf.FLASH_SECTOR and v["FLASH_PAGE"] == patch_ptf.FLASH_PAGE
          and v["COPY_TABLE"] == patch_ptf.COPY_TABLE and v["ITCM_ENTRY"] == patch_ptf.ITCM_ENTRY
          and v["STUB_ENTRY"] == patch_ptf.STUB_ENTRY and tuple(v["STUB_ENTRY_STOCK"]) == patch_ptf.STUB_ENTRY_STOCK
          and v["ITCM_STUBS"] == patch_ptf.ITCM_STUBS and v["REGS"] == patch_ptf.ITCM_ARG_REGS
          and v["PREFIX_SHORT"].encode() == patch_ptf.VERSION_PREFIX
          and v["PREFIX_LONG"].encode() == patch_ptf.VERSION_LONG_PREFIX,
          "web/verify.js's RAM regions, alignment, flash padding, copy table, ITCM stubs and version "
          "prefixes as in patch_ptf.py")
    print("version texts for a 15th mod on")
    long = consts["MIX_VERSIONS"]
    check(all(re.fullmatch(r"[0-9A-Z]{%d}" % patch_ptf.VERSION_LONG, v) and int(v, 36) == m for m, v in long)
          and len({v for _, v in long}) == len({m for m, _ in long}),
          f"{len(long)} mixes with bit 14 to 24: {patch_ptf.VERSION_LONG} capitals or digits each, the mix's bits "
          f"in base 36 (so all different), shown as 1.2-{long[0][1]} (9 characters)")

    selections = [list(c) for n in range(1, len(ids) + 1) for c in itertools.combinations(ids, n)]
    sel_jobs = [{"name": "sel-" + "+".join(c), "ids": c} for c in selections]

    # the selections that passed before with the same inputs, from the page's own plans
    _, plans = run_js(site, stock, [dict(j, plan=True) for j in sel_jobs], outdir / "js")
    print("names")
    clashes, bad = name_problems(plans)
    check(not clashes, f"the {len(plans)} selections' spec paths and version texts are unique ignoring case"
          + "".join(f"\n          {c}" for c in clashes[:10]))
    check(not bad, f"version texts are letters and digits, at most {patch_ptf.VERSION_MAX} or exactly "
          f"{patch_ptf.VERSION_LONG}"
          + (f": {', '.join(bad[:10])}" if bad else ""))
    print("compare budget")
    fake = [{"name": "+".join(map(str, c)), "ids": c} for n in range(1, 17) for c in itertools.combinations(range(16), n)]
    picked = compared(fake)
    check(compared(sel_jobs[:MAX_COMPARE]) == {s["name"] for s in sel_jobs[:MAX_COMPARE]}
          and len(picked) == MAX_COMPARE and picked == compared(fake)
          and all(sel["name"] in picked for sel in fake if len(sel["ids"]) <= 3 or len(sel["ids"]) >= 14),
          f"all selections while there are at most {MAX_COMPARE}; past that (16 mods: {len(fake)}), "
          f"{MAX_COMPARE}: every one of 1-3 mods and of all but 0-2, then the same spread each run")
    if failures:                                  # before the long part
        print(f"{len(failures)} failure(s), before patching anything")
        return 1
    # each selection's files: sel-<its spec's folder>, short whatever the number of mods
    files = {n: "sel-" + Path(pl["spec"]).parent.name if pl["ok"] and pl.get("spec")
             else "sel-x" + hashlib.sha256(n.encode()).hexdigest()[:12] for n, pl in plans.items()}
    check(len(set(files.values())) == len(files), "the selections' file names are unique")
    for f in py_dir.iterdir():                    # selections the page no longer offers, older names
        if f.name.split(".")[0] not in set(files.values()):
            f.unlink()
    base = hashlib.sha256("|".join([*(file_sha(f) for f in CODE), file_sha(stock)]).encode()).hexdigest()
    keys = {n: selection_key(base, site, pl) if pl["ok"] else None for n, pl in plans.items()}
    cached = {n for n, k in keys.items() if k in cache and outputs_as_written(py_dir, files[n], cache[k])}
    # In memory, but every FULL_EVERY-th selection (by its place among all of them, so the
    # same ones each run), each mod alone and patch/all as the patchers run on their own.
    full = {sel["name"] for i, sel in enumerate(sel_jobs)
            if i % FULL_EVERY == 0 or plans[sel["name"]].get("kind") in ("single", "all")}
    todo = [dict(j, file=files[j["name"]], full=j["name"] in full) for j in sel_jobs if j["name"] not in cached]
    by_python = compared(sel_jobs)
    _, js_new = run_js(site, stock, todo, outdir / "js", procs)
    js_sel = {j["name"]: cache[keys[j["name"]]]["js"] if j["name"] in cached else js_new[j["name"]]
              for j in sel_jobs}
    extra_jobs = [{"name": n, "spec": s} for n, s in other_orders.items()]
    extra_jobs += [{"name": n, "spec": s} for n, (s, st, _) in refusals.items() if st == stock]
    extra_jobs += [{"name": "unported", "spec": "patch/swing-future/patch.json"}]
    _, js_extra = run_js(extra, stock, extra_jobs, outdir / "js")
    if "other-firmware" in refusals:
        _, js_other = run_js(extra, refusals["other-firmware"][1], [{"name": "other-firmware", "spec": refusals["other-firmware"][0]}],
                             outdir / "js")
        js_extra.update(js_other)

    # the page's check on broken firmware: each mod alone, patch/all, the first mix and the
    # largest one, each broken in every way tools/verify_corrupt.mjs knows
    mixes = [n for n in plans if plans[n].get("kind") == "mix" and plans[n]["ok"]]
    broken_from = [n for n in plans if plans[n].get("kind") in ("single", "all") and plans[n]["ok"]]
    broken_from += [mixes[0], max(mixes, key=len)] if mixes else []
    _, js_broken = run_js(site, stock, [{"name": n, "spec": plans[n]["spec"], "corrupt": True,
                                         "file": "broken-" + files[n][4:], "full": False}
                                        for n in dict.fromkeys(broken_from)], outdir / "js", procs)

    def layout_args(sel: dict) -> tuple | None:
        js = js_sel[sel["name"]]
        if not js["ok"]:
            return None
        specs = [site / p["spec"] for p in manifest["patches"] if p["id"] in sel["ids"]]
        version = None if js["kind"] == "single" else json.loads((site / js["spec"]).read_text())["version"]
        return (js["key"], specs, version)

    py_jobs = [(site, sel["file"], js_sel[sel["name"]].get("spec"), stock, outdir, js_sel[sel["name"]]["ok"],
                layout_args(sel), sel["name"] in full, sel["name"] in by_python) for sel in todo]
    py_jobs += [(extra, n, s, stock, outdir, js_extra[n]["ok"], None, True, True) for n, s in other_orders.items()]
    py_jobs += [(extra, n, s, st, outdir, False, None, True, True) for n, (s, st, _) in refusals.items()]
    py_names = [sel["name"] for sel in todo] + list(other_orders) + list(refusals)
    with ProcessPoolExecutor(procs, initializer=_init_worker, initargs=(str(stock),)) as pool:
        py = dict(zip(py_names, pool.map(_py_job, *zip(*py_jobs), chunksize=4)))
    for n in cached:                              # as they were when they passed
        py[n] = SimpleNamespace(returncode=0, stdout="", stderr="", same=True, same_map=True,
                                layout_checked=True, layout_failed=0, layout_log="",
                                compared=cache[keys[n]].get("compared", True))

    def same(name: str, label: str) -> bool:
        js, pr = js_extra.get(name) or js_sel[name], py[name]
        if not js["ok"] or pr is None or pr.returncode:
            check(False, f"{label}: both patched (js: {js.get('error')}; py: {pr and (pr.stdout + pr.stderr)[-300:]})")
            return False
        if not pr.compared:                       # past MAX_COMPARE: web/verify.js's check alone
            check(bool(js.get("verified")), f"{label}: web patcher only, checked by web/verify.js "
                  f"({js['sha256'][:12]}…)")
            return bool(js.get("verified"))
        check(pr.same and pr.same_map, f"{label}: .ptf byte-identical to patch_ptf.py's, .map.json equal "
              f"({js['sha256'][:12]}…)")
        return pr.same

    print(f"every selection the page offers ({len(selections)}; {len(cached)} unchanged since they passed, "
          f"from the cache)")
    expected = {}
    for sel in sel_jobs:
        js = js_sel[sel["name"]]
        if same(sel["name"], f"{js.get('kind', '?'):6} {js.get('version_text')}  {'+'.join(sel['ids'])}"):
            if py[sel["name"]].compared:         # a hash patch_ptf.py made too
                expected[js["key"]] = js["sha256"]
    print("the bundles test_layout.py builds in other orders")
    for n in other_orders:
        same(n, n)
    check(js_extra["long-version"].get("version_text") == "1.2-K7Q2M",
          f"a 5-character version: the image's own text reads 1.2-K7Q2M ({js_extra['long-version'].get('version_text')})")

    print("refusals")
    for n, (_, _, why) in refusals.items():
        js, pr = js_extra[n], py[n]
        check(not js["ok"] and why in js["error"] and pr.returncode != 0 and why in pr.stderr,
              f"{n}: both refuse ({js.get('error', '')[:70]})")
    js = js_extra["unported"]
    check(not js["ok"] and "doesn't support future_hooks" in js["error"],
          "a spec using a key the port doesn't know (future_hooks) is refused")

    print("the page's own check (web/verify.js)")
    unverified = [n for n in [*(s["name"] for s in sel_jobs), *other_orders]
                  if (js_extra.get(n) or js_sel[n])["ok"] and not (js_extra.get(n) or js_sel[n]).get("verified")]
    for n in unverified[:5]:
        print(f"    {n}: {(js_extra.get(n) or js_sel[n]).get('verify_problems')}")
    check(not unverified, f"accepts every build: the {len(sel_jobs)} selections and the {len(other_orders)} "
          f"bundles in other orders" + (f" ({len(unverified)} rejected)" if unverified else ""))
    tried = sum(len(js.get("corrupt", {}).get("tried", [])) for js in js_broken.values())
    missed = [f"{n}: {m}" for n, js in js_broken.items() for m in js.get("corrupt", {}).get("missed", [])]
    for m in missed[:10]:
        print(f"    missed {m}")
    check(all(js["ok"] and js.get("verified") for js in js_broken.values()) and tried > 0 and not missed,
          f"rejects all {tried} deliberately broken builds made from {len(js_broken)} selections (each mod "
          f"alone, patch/all, two mixes)" + (f"; missed {len(missed)}" if missed else ""))

    print("in memory and in full")
    built = [pr for pr in py.values() if pr is not None and not pr.returncode and getattr(pr, "full", None) is not None]
    whole = [pr for pr in built if pr.full]
    check(bool(whole) and all(pr.fast_same for pr in whole),
          f"{len(whole)} of the {len(built)} builds made as patch_ptf.py runs on its own (its encoder, the "
          f"6 MB read-back); the in-memory encoder gives the same text on every one")

    print("test_layout.py checks on every selection")
    bad = 0
    for sel in sel_jobs:
        pr = py[sel["name"]]
        if pr is not None and pr.layout_failed:
            print(pr.layout_log)
            bad += 1
    checked = sum(1 for sel in sel_jobs if py[sel["name"]] is not None and py[sel["name"]].layout_checked)
    want = sum(1 for sel in sel_jobs if py[sel["name"]] is None or py[sel["name"]].compared)
    check(bad == 0 and checked == want,
          f"placement, RAM, relocation, chain words, hooked sites and version text for all {want} "
          f"built by patch_ptf.py too")

    # the selections' cache: every current selection that passed, the new ones as written
    passed = {}
    for sel in sel_jobs:
        n, js, pr = sel["name"], js_sel[sel["name"]], py[sel["name"]]
        if n in cached:
            passed[keys[n]] = cache[keys[n]]
        elif keys[n] and js["ok"] and js.get("verified") and pr is not None and not pr.returncode and (
                (pr.same and pr.same_map and pr.layout_checked and not pr.layout_failed) or not pr.compared):
            passed[keys[n]] = {"js": js, "map": pr.map, "end": pr.end, "compared": pr.compared}
    cache_path.write_text(json.dumps(passed, indent=0) + "\n")
    index = {n: {"file": files[n], "sha256": passed[k]["js"]["sha256"], "end": passed[k]["end"],
                 "spec": js_sel[n]["spec"]} for n, k in keys.items() if k in passed}
    (py_dir / "index.json").write_text(json.dumps(index, indent=0) + "\n")

    print("hardware-tested builds")
    table = tested_hashes()
    singles_all = [js for js in js_sel.values() if js.get("kind") in ("single", "all")]
    for js in singles_all:
        code = js["version_text"].split("-", 1)[1]
        sha, on_hw = table.get(code, (None, False))
        check(sha == js["sha256"], f"{js['key']}: {js['version_text']} is the build in docs/PATCHING.md")
        check((js["sha256"] in manifest["tested_builds"]) == on_hw,
              f"{js['key']}: {'listed' if on_hw else 'not listed'} in manifest tested_builds, as docs/PATCHING.md says")
    check(set(manifest["tested_builds"]) <= {sha for sha, on_hw in table.values() if on_hw},
          "manifest tested_builds are all tested builds in docs/PATCHING.md")

    print("web/expected.json")
    both = [js_sel[sel["name"]].get("key") for sel in sel_jobs if py[sel["name"]] is None or py[sel["name"]].compared]
    check(list(expected) == [k for k in stage.selection_keys() if k in set(both)] and stage.expected_keys_ok(expected),
          f"the selections are the ones web/stage.py expects: the {len(both)} both patchers built, each mod "
          "alone and all of them among them")
    exp_path = ROOT / "web" / "expected.json"
    want = {"inputs_sha256": stage.inputs_digest(), "selections": expected}
    if update and not failures:
        exp_path.write_text(json.dumps(want, indent=1) + "\n", newline="\n")
        print(f"  wrote {exp_path.relative_to(ROOT)} ({len(expected)} selections)")
    elif update:
        print("  not updated: there are failures")
    check(json.loads(exp_path.read_text()) == want,
          f"holds exactly the {len(expected)} selections' hashes and the patch files' digest "
          "(run with --update after changing a patch)")

    print("file names")
    longest = max((p.relative_to(outdir).as_posix() for p in outdir.rglob("*")), key=len)
    check(len(longest) <= MAX_PATH, f"every file written is at most {MAX_PATH} characters below the output "
          f"folder (longest {len(longest)}: {longest})")

    print(f"outputs in {outdir}")
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
