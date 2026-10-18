"""Check how tools/patch_ptf.py places and relocates patches, without the emulator.

  python tools/test_layout.py <stock TrackerPlus_1.2.0.ptf> [out dir]

Builds, from the repository's patch specs (local builds if present, else prebuilt):
  - colorsong alone (must be byte-identical to the hardware-tested c10 build),
  - versiontest alone (pinned base; byte-identical to MT0),
  - humanize alone,
  - patch/all (colorsong, then humanize),
  - the same two patches the other way round (humanize, then colorsong).
For each output it reads the image back and checks:
  - the patches sit in the listed order, each right after the one before (16-byte
    aligned), or at its fixed base; the gaps are 0xFF;
  - each patch's bytes equal its .bin, except the words its relocation table lists,
    which equal the .bin's value plus the move, exactly;
  - every hook (BL site or word) points at its symbol plus the move;
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
from patch_ptf import load_rel, load_symbols  # noqa: E402
from ptf import read_ptf  # noqa: E402

C10_SHA = "5d82977eadf595b6a491b90c793dcd1bfc644cec74920a98897d86444e28cb4a"
MT0_SHA = "f45510a17f8b9b120c0e699f239a737a145c3d57bbaf596046fb7a8bc643ffff"
STOCK_END = 0x601BAB78
ALIGN = 16
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def bl_target(image: bytes, base: int, at: int) -> int:
    hw1, hw2 = struct.unpack_from("<HH", image, at - base)
    s = (hw1 >> 10) & 1
    j1, j2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
    i1, i2 = ~(j1 ^ s) & 1, ~(j2 ^ s) & 1
    off = (s << 24) | (i1 << 23) | (i2 << 22) | ((hw1 & 0x3FF) << 12) | ((hw2 & 0x7FF) << 1)
    if s:
        off -= 1 << 25
    return at + 4 + off


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


def verify(label: str, out: Path, specs: list[Path], version: str | None) -> None:
    print(label)
    ptf = read_ptf(out)
    img, base0 = bytes(ptf.image), ptf.base
    end = STOCK_END
    for sp in specs:
        p = patch_files(sp)
        spec, blob = p["spec"], p["blob"]
        if "base" in spec:
            base, link, offsets = int(spec["base"], 16), int(spec["base"], 16), []
        else:
            link, _, offsets = p["rel"]
            base = (end + ALIGN - 1) & ~(ALIGN - 1)
        delta = base - link
        name = spec["name"]
        check(all(b == 0xFF for b in img[end - base0:base - base0]), f"{name}: gap before it is 0xFF")
        got = img[base - base0:base - base0 + len(blob)]
        want = bytearray(blob)
        for off in offsets:
            v = struct.unpack_from("<I", blob, off)[0]
            struct.pack_into("<I", want, off, (v + delta) & 0xFFFFFFFF)
        check(got == bytes(want), f"{name}: at {base:#x} (moved {delta:+#x}), {len(offsets)} words relocated, all other bytes as built")
        hooks_ok = all(bl_target(img, base0, int(br["at"], 16)) == p["syms"][br["to"]] + delta
                       for br in spec["branches"])
        words_ok = all(struct.unpack_from("<I", img, int(w["at"], 16) - base0)[0]
                       == (p["syms"][w["to"]] + delta) | (1 if w.get("thumb") else 0)
                       for w in spec.get("words", []) if not (version and w.get("version")))
        check(hooks_ok and words_ok, f"{name}: {len(spec['branches'])} calls and its words point into it")
        end = base + len(blob)
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
    cs, hz, vt = (ROOT / "patch" / n / "patch.json" for n in ("colorsong", "humanize", "versiontest"))
    all_spec = ROOT / "patch" / "all" / "patch.json"
    rev = json.loads(all_spec.read_text())
    rev.update(name="all-reversed", include=[str(hz), str(cs)], version="MTR")
    rev_spec = outdir / "reversed.json"
    rev_spec.write_text(json.dumps(rev, indent=2))

    for label, spec, specs, version, sha in (
            ("colorsong alone", cs, [cs], None, C10_SHA),
            ("versiontest alone (fixed base)", vt, [vt], None, MT0_SHA),
            ("humanize alone", hz, [hz], None, None),
            ("patch/all: colorsong, then humanize", all_spec, [cs, hz], json.loads(all_spec.read_text())["version"], None),
            ("reversed: humanize, then colorsong", rev_spec, [hz, cs], "MTR", None)):
        out = outdir / f"{spec.parent.name if spec != rev_spec else 'reversed'}.ptf"
        if not build(stock, spec, out):
            check(False, f"{label}: patch_ptf failed")
            continue
        verify(label, out, specs, version)
        if sha:
            got = hashlib.sha256(out.read_bytes()).hexdigest()
            check(got == sha, f"{label}: byte-identical to the hardware-tested build")
    print(f"outputs in {outdir}")
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
