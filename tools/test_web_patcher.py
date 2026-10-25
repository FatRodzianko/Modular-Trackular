"""Check that the web patcher (web/patcher.js) makes exactly what tools/patch_ptf.py makes.

  python tools/test_web_patcher.py <stock TrackerPlus_1.2.0.ptf> [out dir] [--update]

Needs Node (runs web/patcher.js through tools/web_patcher_node.mjs). It:
  - stages the site with web/stage.py, as the deployment does, and runs both patchers
    on the staged files (so both use the prebuilt patches);
  - checks patcher.js's constants (RAM regions, alignment, version text) equal
    patch_ptf.py's;
  - for every selection the page offers (each mod alone, patch/all, every other mix),
    plans it as the page does, patches it in JavaScript, patches the same spec with
    patch_ptf.py, and checks the .ptf files are byte-identical and the .map.json files
    equal;
  - does the same for the bundles tools/test_layout.py builds in other orders
    (reversed, masterfx first, every mod reversed, humanize with a chained copy);
  - does the same for ITCM hooks: condtrig placed first, and condtrig with a copy of
    itself on its ITCM sites;
  - checks both refuse the same things: another firmware version, a bundle with
    ramdiag, a shared site not chained by both, two patches passing different
    registers at one ITCM site; and that the port refuses a spec using a key it
    doesn't know (a patch_ptf.py feature it doesn't have yet);
  - runs tools/test_layout.py's checks (placement, RAM, relocation, chains, version
    text) on every selection's output;
  - checks the single-mod and patch/all outputs against the hardware-tested SHA-256s
    in docs/PATCHING.md and web/manifest.json;
  - checks web/expected.json (the hashes the page accepts) holds exactly the
    selections' hashes; --update rewrites it.
Outputs: <out dir>/py, the .ptf and .map.json files the emulator tests need, and
<out dir>/js, the web patcher's .map.json files (its .ptf files only where they differ).

Both patchers run in parallel: the web patcher in one Node process per core, patch_ptf.py
in a pool of worker processes that parse the stock firmware once (patch_ptf.py's own
code, called in the worker instead of in a new interpreter per selection).
"""
from __future__ import annotations

import contextlib
import dataclasses
import functools
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "web"))
import patch_ptf  # noqa: E402
import stage  # noqa: E402
import test_layout  # noqa: E402
from ptf import read_ptf  # noqa: E402

failures: list[str] = []
PROCS = os.cpu_count() or 8


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


def _init_worker(stock: str) -> None:
    """Parse the stock firmware once per worker, and load each patch's files once for the
    layout checks. patch_ptf.py gets a fresh copy of the parsed stock on every call (it
    replaces the copy's image and record list, as it would a newly read file's)."""
    global _stock_img
    stock_path = Path(stock)
    parsed = read_ptf(stock_path)
    _stock_img = bytes(parsed.image)

    def read_cached(path: Path):
        if Path(path) == stock_path:
            return dataclasses.replace(parsed, records=list(parsed.records), image=bytearray(parsed.image))
        return read_ptf(path)

    patch_ptf.read_ptf = read_cached
    test_layout.patch_files = functools.lru_cache(None)(test_layout.patch_files)


def _py_job(site: Path, name: str, spec: str | None, stock: Path, outdir: Path, compare: bool,
            verify: tuple | None) -> SimpleNamespace | None:
    """patch_ptf.py on one spec; then, if asked, the comparison with the web patcher's
    output (whose .ptf is deleted when it's identical) and test_layout.py's checks."""
    if not spec:
        return None
    out = outdir / "py" / f"{name}.ptf"
    so, se = io.StringIO(), io.StringIO()
    argv, sys.argv = sys.argv, ["patch_ptf.py", str(stock), str(site / spec), str(out)]
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
    r = SimpleNamespace(returncode=rc, stdout=so.getvalue(), stderr=se.getvalue(), same=None, same_map=None,
                        layout_checked=False, layout_failed=0, layout_log="")
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
            test_layout.verify(key, out, specs, version, _stock_img)
        r.layout_checked = True
        r.layout_failed = len(test_layout.failures) - n0
        r.layout_log = buf.getvalue() if r.layout_failed else ""
    return r


def tested_hashes() -> dict[str, tuple[str, bool]]:
    """version text -> (SHA-256, tested on hardware), from the table in docs/PATCHING.md."""
    rows = re.findall(r"^\|[^|]*\| `TrackerPlus_(\w+)\.ptf` \|[^|]*\| ([^|]*) \| `([0-9a-f]{64})` \|$",
                      (ROOT / "docs" / "PATCHING.md").read_text(), re.M)
    return {code: (sha, hw.startswith("tested")) for code, hw, sha in rows}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    update = "--update" in sys.argv
    stock = Path(args[0]).resolve()
    outdir = Path(args[1]).resolve() if len(args) > 1 else Path(tempfile.mkdtemp())
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
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
    for name in ("ramdiag",):
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

    other_orders = {
        "reversed": bundle("reversed", ["interpolate", "humanize", "colorsong"], "MTR"),
        "with-masterfx-reversed": bundle("with-masterfx-reversed", ["masterfx", "interpolate", "humanize", "colorsong"], "MTY"),
        "every-reversed": bundle("every-reversed", list(reversed(ids)), "MTT"),
        "chain": bundle("chain", ["humanize", "humanize-copy"], "CHN"),
        "condtrig-first": bundle("condtrig-first", ["condtrig", "swing", "visualizer", "interpolate"], "MCF"),
        "itcm-chain": bundle("itcm-chain", ["condtrig", "condtrig-copy"], "CHI"),
    }
    refusals = {
        "with-ramdiag": (bundle("with-ramdiag", ["colorsong", "ramdiag"], "BAD"), stock, "standalone"),
        "unchained": (bundle("unchained", ["humanize", "humanize-unchained"], "BAD"), stock, "not chained by both"),
        "itcm-args": (bundle("itcm-args", ["condtrig", "condtrig-args"], "BAD"), stock, 'different "args"'),
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
          and consts["VERSION_PREFIX"].encode() == patch_ptf.VERSION_PREFIX, "ALIGN, VERSION_MAX, VERSION_PREFIX")

    selections = [list(c) for n in range(1, len(ids) + 1) for c in itertools.combinations(ids, n)]
    sel_jobs = [{"name": "sel-" + "+".join(c), "ids": c} for c in selections]
    _, js_sel = run_js(site, stock, sel_jobs, outdir / "js", PROCS)
    extra_jobs = [{"name": n, "spec": s} for n, s in other_orders.items()]
    extra_jobs += [{"name": n, "spec": s} for n, (s, st, _) in refusals.items() if st == stock]
    extra_jobs += [{"name": "unported", "spec": "patch/swing-future/patch.json"}]
    _, js_extra = run_js(extra, stock, extra_jobs, outdir / "js")
    if "other-firmware" in refusals:
        _, js_other = run_js(extra, refusals["other-firmware"][1], [{"name": "other-firmware", "spec": refusals["other-firmware"][0]}],
                             outdir / "js")
        js_extra.update(js_other)

    def layout_args(sel: dict) -> tuple | None:
        js = js_sel[sel["name"]]
        if not js["ok"]:
            return None
        specs = [site / p["spec"] for p in manifest["patches"] if p["id"] in sel["ids"]]
        version = None if js["kind"] == "single" else json.loads((site / js["spec"]).read_text())["version"]
        return (js["key"], specs, version)

    (outdir / "py").mkdir()
    py_jobs = [(site, sel["name"], js_sel[sel["name"]].get("spec"), stock, outdir, js_sel[sel["name"]]["ok"],
                layout_args(sel)) for sel in sel_jobs]
    py_jobs += [(extra, n, s, stock, outdir, js_extra[n]["ok"], None) for n, s in other_orders.items()]
    py_jobs += [(extra, n, s, st, outdir, False, None) for n, (s, st, _) in refusals.items()]
    with ProcessPoolExecutor(PROCS, initializer=_init_worker, initargs=(str(stock),)) as pool:
        py = dict(zip([j[1] for j in py_jobs], pool.map(_py_job, *zip(*py_jobs), chunksize=4)))

    def same(name: str, label: str) -> bool:
        js, pr = js_extra.get(name) or js_sel[name], py[name]
        if not js["ok"] or pr is None or pr.returncode:
            check(False, f"{label}: both patched (js: {js.get('error')}; py: {pr and (pr.stdout + pr.stderr)[-300:]})")
            return False
        check(pr.same and pr.same_map, f"{label}: .ptf byte-identical to patch_ptf.py's, .map.json equal "
              f"({js['sha256'][:12]}…)")
        return pr.same

    print(f"every selection the page offers ({len(selections)})")
    expected = {}
    for sel in sel_jobs:
        js = js_sel[sel["name"]]
        if same(sel["name"], f"{js.get('kind', '?'):6} {js.get('version_text')}  {'+'.join(sel['ids'])}"):
            expected[js["key"]] = js["sha256"]
    print("the bundles test_layout.py builds in other orders")
    for n in other_orders:
        same(n, n)

    print("refusals")
    for n, (_, _, why) in refusals.items():
        js, pr = js_extra[n], py[n]
        check(not js["ok"] and why in js["error"] and pr.returncode != 0 and why in pr.stderr,
              f"{n}: both refuse ({js.get('error', '')[:70]})")
    js = js_extra["unported"]
    check(not js["ok"] and "doesn't support future_hooks" in js["error"],
          "a spec using a key the port doesn't know (future_hooks) is refused")

    print("test_layout.py checks on every selection")
    bad = 0
    for sel in sel_jobs:
        pr = py[sel["name"]]
        if pr is not None and pr.layout_failed:
            print(pr.layout_log)
            bad += 1
    checked = sum(1 for sel in sel_jobs if py[sel["name"]] is not None and py[sel["name"]].layout_checked)
    check(bad == 0 and checked == len(sel_jobs),
          f"placement, RAM, relocation, chain words, hooked sites and version text for all {len(sel_jobs)}")

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
    check(list(expected) == stage.selection_keys(), "the selections are the ones web/stage.py expects")
    exp_path = ROOT / "web" / "expected.json"
    want = {"inputs_sha256": stage.inputs_digest(), "selections": expected}
    if update and not failures:
        exp_path.write_text(json.dumps(want, indent=1) + "\n", newline="\n")
        print(f"  wrote {exp_path.relative_to(ROOT)} ({len(expected)} selections)")
    elif update:
        print("  not updated: there are failures")
    check(json.loads(exp_path.read_text()) == want,
          f"holds exactly the {len(selections)} selections' hashes and the patch files' digest "
          "(run with --update after changing a patch)")

    print(f"outputs in {outdir}")
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
