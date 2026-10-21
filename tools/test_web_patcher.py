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
  - checks both refuse the same things: another firmware version, a bundle with
    ramdiag, a shared site not chained by both; and that the port refuses a spec
    using a patch_ptf.py feature it doesn't have (ITCM hooks);
  - runs tools/test_layout.py's checks (placement, RAM, relocation, chains, version
    text) on every selection's output;
  - checks the single-mod and patch/all outputs against the hardware-tested SHA-256s
    in docs/PATCHING.md and web/manifest.json;
  - checks web/expected.json (the hashes the page accepts) holds exactly the
    selections' hashes; --update rewrites it.
Outputs: <out dir>/js and <out dir>/py, with the .map.json files the emulator tests
need.
"""
from __future__ import annotations

import contextlib
import io
import itertools
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "web"))
import patch_ptf  # noqa: E402
import stage  # noqa: E402
import test_layout  # noqa: E402
from ptf import read_ptf  # noqa: E402

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def run_js(site: Path, stock: Path, jobs: list[dict], out: Path) -> tuple[dict, dict[str, dict]]:
    jobs_path = out.parent / f"jobs-{out.name}.json"
    jobs_path.write_text(json.dumps(jobs, indent=1))
    r = subprocess.run(["node", str(ROOT / "tools" / "web_patcher_node.mjs"), str(site), str(stock), str(jobs_path), str(out)],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("node failed:\n" + r.stdout + r.stderr)
    lines = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    return lines[0]["constants"], {d["name"]: d for d in lines[1:]}


def run_py(site: Path, stock: Path, spec: str, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(site / spec), str(out)],
                          capture_output=True, text=True)


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

    other_orders = {
        "reversed": bundle("reversed", ["interpolate", "humanize", "colorsong"], "MTR"),
        "with-masterfx-reversed": bundle("with-masterfx-reversed", ["masterfx", "interpolate", "humanize", "colorsong"], "MTY"),
        "every-reversed": bundle("every-reversed", list(reversed(ids)), "MTT"),
        "chain": bundle("chain", ["humanize", "humanize-copy"], "CHN"),
    }
    refusals = {
        "with-ramdiag": (bundle("with-ramdiag", ["colorsong", "ramdiag"], "BAD"), stock, "standalone"),
        "unchained": (bundle("unchained", ["humanize", "humanize-unchained"], "BAD"), stock, "not chained by both"),
    }
    sw_spec = json.loads((extra / "patch/swing/patch.json").read_text())
    unported = dict(sw_spec, name="swing-itcm", itcm_branches=[],
                    **{k: "../swing/" + sw_spec[k] for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel")})
    (extra / "patch/swing-itcm").mkdir()
    (extra / "patch/swing-itcm/patch.json").write_text(json.dumps(unported, indent=2))
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
    _, js_sel = run_js(site, stock, sel_jobs, outdir / "js")
    extra_jobs = [{"name": n, "spec": s} for n, s in other_orders.items()]
    extra_jobs += [{"name": n, "spec": s} for n, (s, st, _) in refusals.items() if st == stock]
    extra_jobs += [{"name": "unported", "spec": "patch/swing-itcm/patch.json"}]
    _, js_extra = run_js(extra, stock, extra_jobs, outdir / "js")
    if "other-firmware" in refusals:
        _, js_other = run_js(extra, refusals["other-firmware"][1], [{"name": "other-firmware", "spec": refusals["other-firmware"][0]}],
                             outdir / "js")
        js_extra.update(js_other)

    (outdir / "py").mkdir()
    py_jobs = [(site, sel["name"], js_sel[sel["name"]].get("spec"), stock) for sel in sel_jobs]
    py_jobs += [(extra, n, s, stock) for n, s in other_orders.items()]
    py_jobs += [(extra, n, s, st) for n, (s, st, _) in refusals.items()]
    with ThreadPoolExecutor(8) as pool:
        py = dict(zip([j[1] for j in py_jobs],
                      pool.map(lambda j: run_py(j[0], j[3], j[2], outdir / "py" / f"{j[1]}.ptf") if j[2] else None, py_jobs)))

    def same(name: str, label: str) -> bool:
        js, pr = js_extra.get(name) or js_sel[name], py[name]
        if not js["ok"] or pr is None or pr.returncode:
            check(False, f"{label}: both patched (js: {js.get('error')}; py: {pr and (pr.stdout + pr.stderr)[-300:]})")
            return False
        a, b = outdir / "js" / f"{name}.ptf", outdir / "py" / f"{name}.ptf"
        ok = a.read_bytes() == b.read_bytes()
        ok_map = (outdir / "js" / f"{name}.map.json").read_text() == (outdir / "py" / f"{name}.map.json").read_text()
        check(ok and ok_map, f"{label}: .ptf byte-identical to patch_ptf.py's, .map.json equal ({js['sha256'][:12]}…)")
        return ok

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
    check(not js["ok"] and "doesn't support itcm_branches" in js["error"],
          "a spec using a patch_ptf.py feature the port lacks (itcm_branches) is refused")

    print("test_layout.py checks on every selection")
    stock_img = bytes(read_ptf(stock).image)
    bad_before = len(test_layout.failures)
    for sel in sel_jobs:
        js = js_sel[sel["name"]]
        if not js["ok"]:
            continue
        specs = [site / p["spec"] for p in manifest["patches"] if p["id"] in sel["ids"]]
        version = None if js["kind"] == "single" else json.loads((site / js["spec"]).read_text())["version"]
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            n0 = len(test_layout.failures)
            test_layout.verify(js["key"], outdir / "py" / f"{sel['name']}.ptf", specs, version, stock_img)
        if len(test_layout.failures) > n0:
            print(buf.getvalue())
    check(len(test_layout.failures) == bad_before,
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
