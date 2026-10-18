"""Apply a patch (patch/<name>/patch.json) to a stock .ptf and write a new .ptf.

Usage: python tools/patch_ptf.py <stock.ptf> <patch.json> <out.ptf>

A spec lists "bin"/"sym" (the patch build output; "prebuilt_bin"/"prebuilt_sym" are
used when there is no local build), "branches" (Thumb-2 BL sites to retarget) and optional "words"
(aligned 32-bit literals to replace with a symbol address; "thumb": true sets bit 0,
for function pointers such as vtable entries).

A bundle spec combines several patches into one firmware: "include" lists their
patch.json files (relative to the bundle's folder) and "version" gives the version
text (Config shows 1.2.0-<version>, at most 3 characters). The included patches' own
version-text words ("version": true) are dropped, and the bundle's text is appended
after the last patch. Each patch keeps its own base address; their code ranges and
hook sites must not overlap.

- Refuses unless the stock image matches the SHA-256 in patch.json.
- Every branch or word site is checked against its original bytes before it is rewritten
  (as a Thumb-2 BL, or as the symbol address for words).
- The patch binaries are appended after the stock image. Gaps up to each base address
  are filled with 0xFF and written as data, so the output has no hole.
- The stock record layout is kept (changed records get new data and checksums), the
  new data goes in 16-byte records before the start/end records, and the output is
  read back and compared with the intended image.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ptf import Record, encode_ptf, read_ptf  # noqa: E402

VERSION_PREFIX = b"%d.%d.%d-"
VERSION_MAX = 3                     # "1.2.0-" + 3 characters: longer text doesn't show


def thumb_bl(at: int, target: int) -> bytes:
    """Encode a Thumb-2 BL at `at` to `target` (T1 encoding, +-16 MB)."""
    off = (target & ~1) - (at + 4)
    if not -(1 << 24) <= off < (1 << 24):
        raise ValueError(f"BL from {at:#x} to {target:#x} out of range")
    s = (off >> 24) & 1
    i1, i2 = (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = (~i1 ^ s) & 1, (~i2 ^ s) & 1
    imm10, imm11 = (off >> 12) & 0x3FF, (off >> 1) & 0x7FF
    hw1 = 0xF000 | (s << 10) | imm10
    hw2 = 0xD000 | (j1 << 13) | (j2 << 11) | imm11
    return struct.pack("<HH", hw1, hw2)


def load_symbols(path: Path) -> dict[str, int]:
    syms = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = int(parts[0], 16)
    return syms


def load_patch(spec: dict, pdir: Path, in_bundle: bool) -> dict:
    """One patch: its code blob, symbols and hook sites (words resolved to values)."""
    bin_path, sym_path = pdir / spec["bin"], pdir / spec["sym"]
    if not bin_path.exists() and "prebuilt_bin" in spec:
        bin_path, sym_path = pdir / spec["prebuilt_bin"], pdir / spec["prebuilt_sym"]
        print(f"no local build, using the prebuilt patch: {bin_path.relative_to(pdir.parent.parent)}")
    syms = load_symbols(sym_path)
    words = []
    for wd in spec.get("words", []):
        if in_bundle and wd.get("version"):
            continue
        value = syms[wd["to"]] | (1 if wd.get("thumb") else 0)
        words.append((wd, value))
    return {"name": spec["name"], "base": int(spec["base"], 16), "blob": bin_path.read_bytes(),
            "syms": syms, "branches": spec["branches"], "words": words}


def main() -> int:
    stock_path, spec_path, out_path = (Path(a) for a in sys.argv[1:4])
    spec = json.loads(spec_path.read_text())
    pdir = spec_path.parent
    ptf = read_ptf(stock_path)

    digest = hashlib.sha256(ptf.image).hexdigest()
    if digest != spec["stock_image_sha256"]:
        sys.exit(f"stock image hash {digest} does not match {spec['firmware']}")

    if "include" in spec:
        parts = []
        for inc in spec["include"]:
            ipath = (pdir / inc).resolve()
            ispec = json.loads(ipath.read_text())
            if ispec["stock_image_sha256"] != spec["stock_image_sha256"]:
                sys.exit(f"{inc} is for another firmware")
            parts.append(load_patch(ispec, ipath.parent, in_bundle=True))
        version = spec["version"].encode()
        if not 0 < len(version) <= VERSION_MAX:
            sys.exit(f"version text {spec['version']!r}: 1 to {VERSION_MAX} characters")
    else:
        parts = [load_patch(spec, pdir, in_bundle=False)]
        version = None

    # Code layout: each patch at its own base, in address order, after the stock image.
    stock_end = ptf.base + len(ptf.image)
    parts.sort(key=lambda p: p["base"])
    end = stock_end
    tail = bytearray()
    for p in parts:
        if p["base"] < end:
            sys.exit(f"{p['name']} at {p['base']:#x} overlaps what comes before it (ends {end:#x})")
        tail += b"\xff" * (p["base"] - end) + p["blob"]
        end = p["base"] + len(p["blob"])

    image = bytearray(ptf.image)
    sites: dict[int, str] = {}

    def claim(at: int, who: str) -> int:
        if at in sites:
            sys.exit(f"{at:#x}: patched by both {sites[at]} and {who}")
        sites[at] = who
        return at - ptf.base

    for p in parts:
        for br in p["branches"]:
            at = int(br["at"], 16)
            off = claim(at, p["name"])
            old = bytes.fromhex(br["old"])
            if image[off:off + 4] != old:
                sys.exit(f"{at:#x}: expected {old.hex()}, found {image[off:off + 4].hex()}")
            new = thumb_bl(at, p["syms"][br["to"]])
            image[off:off + 4] = new
            print(f"{at:#x}: bl -> {br['to']} @ {p['syms'][br['to']]:#x}  ({old.hex()} -> {new.hex()})  {br.get('note', '')}")

    words = [(wd, value, p["name"]) for p in parts for wd, value in p["words"]]
    if version is not None:
        end = (end + 3) & ~3
        vaddr = end
        text = VERSION_PREFIX + version + b"\0"
        tail += b"\xff" * (vaddr - stock_end - len(tail)) + text
        end = vaddr + len(text)
        vw = spec["version_word"]
        words.append(({"at": vw["at"], "old": vw["old"], "to": "version text",
                       "note": f"1.2.0-{version.decode()}"}, vaddr, spec["name"]))

    for wd, value, who in words:
        at = int(wd["at"], 16)
        off = claim(at, who)
        old = bytes.fromhex(wd["old"])
        if at & 3 or image[off:off + 4] != old:
            sys.exit(f"{at:#x}: expected aligned word {old.hex()}, found {image[off:off + 4].hex()}")
        new = struct.pack("<I", value)
        image[off:off + 4] = new
        shown = value & ~1 if wd.get("thumb") else value
        print(f"{at:#x}: word -> {wd['to']} @ {shown:#x}  ({old.hex()} -> {new.hex()})  {wd.get('note', '')}")

    patched = ptf
    patched.image = image                         # same length as stock; records re-encoded

    # Append the tail as 16-byte data records (+ type 04 records when the upper half changes).
    end_records = [r for r in patched.records if r.kind in (5, 1)]
    records = [r for r in patched.records if r.kind not in (5, 1)]
    upper = None
    for r in records:
        if r.kind == 4:
            upper = int.from_bytes(r.payload, "big")
    added = []
    for i in range(0, len(tail), 16):
        a = stock_end + i
        if a >> 16 != upper:
            upper = a >> 16
            added.append(Record(4, 0, upper.to_bytes(2, "big")))
        added.append(Record(0, a, bytes(tail[i:i + 16])))
    full_image = bytes(image) + bytes(tail)
    patched.records = records + added + end_records
    patched.image = bytearray(full_image)

    text = encode_ptf(patched)
    out_path.write_bytes(text.encode())

    check = read_ptf(out_path)
    if check.base != ptf.base or bytes(check.image) != full_image or check.start != ptf.start:
        sys.exit("read-back mismatch")
    if len(check.data_ranges()) != 1:
        sys.exit(f"output has gaps: {check.data_ranges()}")
    blobs = ", ".join(f"{p['name']} {len(p['blob'])} bytes at {p['base']:#x}" for p in parts)
    print(f"wrote {out_path}: {len(full_image)} bytes ({blobs})")
    print(f"file sha256 {hashlib.sha256(out_path.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
