"""Check how tools/patch_ptf.py places and relocates patches, without the emulator.

  python tools/test_layout.py <stock TrackerPlus_1.2.0.ptf> [out dir]

Builds, from the repository's patch specs (local builds if present, else prebuilt):
  - colorsong alone, humanize alone, interpolate alone, masterfx alone, ramdiag alone,
  - versiontest alone (pinned base; byte-identical to MT0),
  - patch/all (colorsong, humanize, interpolate),
  - the same patches the other way round (interpolate, humanize, colorsong),
  - colorsong, humanize, interpolate and masterfx, both ways round,
  - humanize and a copy of it that hooks only its chained sites (two patches on the
    same four sites),
and checks that a bundle with ramdiag (standalone) is refused, and so is one where a
shared site isn't chained by both patches.
For each output it reads the image back and its .map.json, and checks:
  - the patches sit in the listed order, each right after the one before (16-byte
    aligned), or at its fixed base; the gaps are 0xFF;
  - each patch's RAM block lies in a RAM region it may use (no SDRAM for patches that
    use RAM at startup; the ITCM tail only when named), 16-byte aligned, and no two
    blocks overlap;
  - each patch's bytes equal its .bin, except the words its relocation table lists,
    which equal the .bin's value plus the code move or the RAM move, exactly;
  - each chain word ("next") holds what its site pointed to before that patch: the
    stock target or the hook of the patch before it;
  - every hooked site (BL or word) points at the hook of the last patch that hooks it;
  - the version text is the bundle's (or the patch's own when alone).
The emulator tests (tools/emu/test_*.py) then check that the firmware works.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from patch_ptf import RAM_DEFAULT_ORDER, RAM_REGIONS, bl_target, load_rel, load_symbols  # noqa: E402
from ptf import read_ptf  # noqa: E402

MT0_SHA = "f45510a17f8b9b120c0e699f239a737a145c3d57bbaf596046fb7a8bc643ffff"
STOCK_END = 0x601BAB78
ALIGN = 16
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def patch_files(spec_path: Path) -> dict:
    spec = json.loads(spec_path.read_text())
    d = spec_path.parent
    keys = ("bin", "sym", "rel")
    if not (d / spec["bin"]).exists():
        keys = ("prebuilt_bin", "prebuilt_sym", "prebuilt_rel")
    blob = (d / spec[keys[0]]).read_bytes()
    syms = load_symbols(d / spec[keys[1]])
    rel = load_rel(d / spec[keys[2]]) if spec.get(keys[2]) else None
    return {"spec": spec, "blob": blob, "syms": syms, "rel": rel}


def build(stock: Path, spec: Path, out: Path) -> bool:
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(spec), str(out)],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stdout + r.stderr)
    return r.returncode == 0


def verify(label: str, out: Path, specs: list[Path], version: str | None, stock: bytes) -> None:
    print(label)
    ptf = read_ptf(out)
    img, base0 = bytes(ptf.image), ptf.base
    layout = {q["name"]: q for q in json.loads(out.with_suffix(".map.json").read_text())["patches"]}
    end = STOCK_END
    ram_blocks = []
    hooked: dict[int, tuple[str, int, str]] = {}    # site -> (kind, what it points to, patch)
    for sp in specs:
        p = patch_files(sp)
        spec, blob, rel = p["spec"], p["blob"], p["rel"]
        name = spec["name"]
        if "base" in spec:
            base, link, offsets = int(spec["base"], 16), int(spec["base"], 16), []
        else:
            link, offsets = rel["link"], rel["offsets"]
            base = (end + ALIGN - 1) & ~(ALIGN - 1)
        delta = base - link
        ram_delta, ram_offsets = 0, []
        if rel and rel["ram_size"]:
            ram = layout[name]["ram"]
            start, rend, region = int(ram["start"], 16), int(ram["end"], 16), ram["region"]
            lo, hi, flags = RAM_REGIONS[region]
            allowed = region in spec.get("ram_regions", RAM_DEFAULT_ORDER) and \
                (not spec.get("ram_startup") or "startup" in flags)
            check(allowed and lo <= start and rend <= hi and start % ALIGN == 0
                  and rend - start == rel["ram_size"],
                  f"{name}: {rel['ram_size']} B of RAM at {start:#x} in {region}, a region it may use")
            ram_blocks.append((start, rend, name))
            ram_delta, ram_offsets = start - rel["ram_link"], rel["ram_offsets"]
        check(all(b == 0xFF for b in img[end - base0:base - base0]), f"{name}: gap before it is 0xFF")
        got = img[base - base0:base - base0 + len(blob)]
        want = bytearray(blob)
        for offs, d in ((offsets, delta), (ram_offsets, ram_delta)):
            for off in offs:
                v = struct.unpack_from("<I", blob, off)[0]
                struct.pack_into("<I", want, off, (v + d) & 0xFFFFFFFF)
        syms = {k: v + delta for k, v in p["syms"].items()}
        chained = 0
        for kind, hook in [("bl", br) for br in spec["branches"]] + \
                [("word", w) for w in spec.get("words", []) if not (version and w.get("version"))]:
            at = int(hook["at"], 16)
            if at in hooked:
                before = hooked[at][1]
            elif kind == "bl":
                before = bl_target(stock, base0, at)
            else:
                before = struct.unpack_from("<I", stock, at - base0)[0]
            if "next" in hook:
                struct.pack_into("<I", want, syms[hook["next"]] - base, before | 1 if kind == "bl" else before)
                chained += 1
            target = syms[hook["to"]] | (1 if kind == "word" and hook.get("thumb") else 0)
            hooked[at] = (kind, target, name)
        check(got == bytes(want), f"{name}: code at {base:#x} (moved {delta:+#x}), {len(offsets)} code words "
              f"and {len(ram_offsets)} RAM words (moved {ram_delta:+#x}) relocated, {chained} chain words "
              f"filled, all other bytes as built")
        end = base + len(blob)
    sites_ok = all((bl_target(img, base0, at) if kind == "bl" else struct.unpack_from("<I", img, at - base0)[0])
                   == target for at, (kind, target, _) in hooked.items())
    shared = sum(1 for at in hooked if sum(1 for sp in specs for h in
                                           patch_files(sp)["spec"]["branches"] + patch_files(sp)["spec"].get("words", [])
                                           if int(h["at"], 16) == at) > 1)
    check(sites_ok, f"all {len(hooked)} hooked sites point at the last patch's hook ({shared} shared)")
    ram_blocks.sort()
    check(all(a[1] <= b[0] for a, b in zip(ram_blocks, ram_blocks[1:])), "no two patches' RAM overlaps")
    vptr = struct.unpack_from("<I", img, 0x600CBF9C - base0)[0]
    text = img[vptr - base0:].split(b"\0")[0].decode()
    if version:
        check(text == "%d.%d.%d-" + version and vptr >= end, f"version text {text!r} after the last patch")
    else:
        check(text.startswith("%d.%d.%d-"), f"version text {text!r} (the patch's own)")


def main() -> int:
    stock = Path(sys.argv[1])
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp())
    outdir.mkdir(parents=True, exist_ok=True)
    cs, hz, vt, rd, ip, mx = (ROOT / "patch" / n / "patch.json"
                              for n in ("colorsong", "humanize", "versiontest", "ramdiag", "interpolate", "masterfx"))
    all_spec = ROOT / "patch" / "all" / "patch.json"
    rev = json.loads(all_spec.read_text())
    rev.update(name="all-reversed", include=[str(ip), str(hz), str(cs)], version="MTR")
    rev_spec = outdir / "reversed.json"
    rev_spec.write_text(json.dumps(rev, indent=2))
    with_mx = dict(rev, name="with-masterfx", include=[str(cs), str(hz), str(ip), str(mx)], version="MTX")
    with_mx_spec = outdir / "with-masterfx.json"
    with_mx_spec.write_text(json.dumps(with_mx, indent=2))
    with_mx_rev = dict(rev, name="with-masterfx-reversed", include=[str(mx), str(ip), str(hz), str(cs)], version="MTY")
    with_mx_rev_spec = outdir / "with-masterfx-reversed.json"
    with_mx_rev_spec.write_text(json.dumps(with_mx_rev, indent=2))
    hz_spec = json.loads(hz.read_text())
    copy = dict(hz_spec, name="humanize-copy",
                branches=[b for b in hz_spec["branches"] if "next" in b],
                words=[w for w in hz_spec["words"] if "next" in w])
    for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel"):
        copy[k] = str((hz.parent / hz_spec[k]).resolve())
    copy_spec = outdir / "humanize-copy.json"
    copy_spec.write_text(json.dumps(copy, indent=2))
    chain = dict(rev, name="chain", include=[str(hz), str(copy_spec.resolve())], version="CHN")
    chain_spec = outdir / "chain.json"
    chain_spec.write_text(json.dumps(chain, indent=2))
    stock_img = bytes(read_ptf(stock).image)

    for label, spec, specs, version, sha in (
            ("colorsong alone", cs, [cs], None, None),
            ("versiontest alone (fixed base)", vt, [vt], None, MT0_SHA),
            ("humanize alone", hz, [hz], None, None),
            ("interpolate alone", ip, [ip], None, None),
            ("masterfx alone", mx, [mx], None, None),
            ("ramdiag alone", rd, [rd], None, None),
            ("patch/all: colorsong, humanize, interpolate", all_spec, [cs, hz, ip],
             json.loads(all_spec.read_text())["version"], None),
            ("reversed: interpolate, humanize, colorsong", rev_spec, [ip, hz, cs], "MTR", None),
            ("colorsong, humanize, interpolate, masterfx", with_mx_spec, [cs, hz, ip, mx], "MTX", None),
            ("masterfx, interpolate, humanize, colorsong", with_mx_rev_spec, [mx, ip, hz, cs], "MTY", None),
            ("chained: humanize, then a copy on its four shared sites", chain_spec, [hz, copy_spec], "CHN", None)):
        out = outdir / f"{spec.stem if spec.parent == outdir else spec.parent.name}.ptf"
        if not build(stock, spec, out):
            check(False, f"{label}: patch_ptf failed")
            continue
        verify(label, out, specs, version, stock_img)
        if sha:
            got = hashlib.sha256(out.read_bytes()).hexdigest()
            check(got == sha, f"{label}: byte-identical to the hardware-tested build")
    print("a bundle with a standalone patch")
    bad = dict(rev, name="with-ramdiag", include=[str(cs), str(rd)], version="BAD")
    bad_spec = outdir / "with-ramdiag.json"
    bad_spec.write_text(json.dumps(bad, indent=2))
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(bad_spec),
                        str(outdir / "with-ramdiag.ptf")], capture_output=True, text=True)
    check(r.returncode != 0 and "standalone" in (r.stdout + r.stderr), "refused: ramdiag is standalone")
    print("a shared site not chained by both patches")
    unchained = dict(copy, name="humanize-unchained",
                     words=[{k: v for k, v in w.items() if k != "next"} for w in copy["words"]])
    un_spec = outdir / "humanize-unchained.json"
    un_spec.write_text(json.dumps(unchained, indent=2))
    bad = dict(rev, name="unchained", include=[str(hz), str(un_spec.resolve())], version="BAD")
    bad_spec = outdir / "unchained.json"
    bad_spec.write_text(json.dumps(bad, indent=2))
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(bad_spec),
                        str(outdir / "unchained.ptf")], capture_output=True, text=True)
    check(r.returncode != 0 and "not chained by both" in (r.stdout + r.stderr), "refused: a shared site isn't chained")
    print(f"outputs in {outdir}")
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
