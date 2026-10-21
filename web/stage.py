"""Assemble the web patcher's site into a folder, for GitHub Pages or a local preview.

  python web/stage.py <out dir> [--no-check]

Copies the page (web/index.html, app.js, patcher.js, style.css, manifest.json,
expected.json) and, for every mod in web/manifest.json and the patch/all bundle,
its patch.json and the prebuilt files it names (prebuilt_bin, prebuilt_sym,
prebuilt_rel), at the same paths as in the repository. Nothing else: no build/
output, no other patches, and never a .ptf or .hex file.

It refuses when web/expected.json is out of date: when it doesn't list every
selection the page offers, or its "inputs_sha256" (a digest of the patch files
above) doesn't match them, as after a patch's prebuilt files change. Then run
tools/test_web_patcher.py with --update, which needs the stock firmware.
--no-check skips that (the test harness stages before it updates).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PAGE = ["index.html", "app.js", "patcher.js", "style.css", "manifest.json", "expected.json"]
NEVER = {".ptf", ".hex"}


def patch_files() -> list[str]:
    """Repository-relative paths of the patch files the site serves."""
    manifest = json.loads((WEB / "manifest.json").read_text())
    specs = [p["spec"] for p in manifest["patches"]]
    all_spec = json.loads((ROOT / manifest["all"]).read_text())
    for inc in all_spec["include"]:
        path = (ROOT / manifest["all"]).parent.joinpath(inc).resolve().relative_to(ROOT).as_posix()
        if path not in specs:
            sys.exit(f"{manifest['all']} includes {inc}, which isn't in web/manifest.json")
    files = [manifest["all"]]
    for spec_path in specs:
        spec = json.loads((ROOT / spec_path).read_text())
        if "prebuilt_bin" not in spec or "prebuilt_sym" not in spec:
            sys.exit(f"{spec_path}: no prebuilt files")
        files.append(spec_path)
        folder = Path(spec_path).parent
        for k in ("prebuilt_bin", "prebuilt_sym", "prebuilt_rel"):
            if spec.get(k):
                files.append((folder / spec[k]).as_posix())
    return files


def inputs_digest() -> str:
    """SHA-256 over the patch files the page uses. Text files count without CR, as a
    checkout may have either line ending and the patchers read both alike."""
    h = hashlib.sha256()
    for rel in sorted(patch_files()):
        data = (ROOT / rel).read_bytes()
        if not rel.endswith(".bin"):
            data = data.replace(b"\r\n", b"\n")
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def selection_keys() -> list[str]:
    """Every selection the page offers, as expected.json keys: ids in manifest order."""
    ids = [p["id"] for p in json.loads((WEB / "manifest.json").read_text())["patches"]]
    return ["+".join(c) for n in range(1, len(ids) + 1) for c in itertools.combinations(ids, n)]


def main() -> int:
    out = Path(next(a for a in sys.argv[1:] if not a.startswith("--")))
    if "--no-check" not in sys.argv:
        expected = json.loads((WEB / "expected.json").read_text())
        if expected.get("inputs_sha256") != inputs_digest() or \
                sorted(expected.get("selections", {})) != sorted(selection_keys()):
            sys.exit("web/expected.json is out of date: run tools/test_web_patcher.py <stock .ptf> --update")
    if out.exists():
        shutil.rmtree(out)
    for rel in ["web/" + f for f in PAGE] + patch_files():
        src = ROOT / rel
        if src.suffix.lower() in NEVER:
            sys.exit(f"refusing to publish {rel}")
        dst = out / (rel[len("web/"):] if rel.startswith("web/") else rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    (out / ".nojekyll").write_text("")
    n = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"staged {n} files in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
