"""Apply a patch (patch/<name>/patch.json) to a stock .ptf and write a new .ptf.

Usage: python tools/patch_ptf.py <stock.ptf> <patch.json> <out.ptf>

A spec lists "bin"/"sym" (the patch build output; "prebuilt_bin"/"prebuilt_sym" are
used when there is no local build), "branches" (Thumb-2 BL sites to retarget) and optional "words"
(aligned 32-bit literals to replace with a symbol address; "thumb": true sets bit 0,
for function pointers such as vtable entries).

- Refuses unless the stock image matches the SHA-256 in patch.json.
- Every branch or word site is checked against its original bytes before it is rewritten
  (as a Thumb-2 BL, or as the symbol address for words).
- The patch binary is appended right after the stock image. The gap up to its base
  address is filled with 0xFF and written as data, so the output has no hole.
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


def main() -> int:
    stock_path, spec_path, out_path = (Path(a) for a in sys.argv[1:4])
    spec = json.loads(spec_path.read_text())
    pdir = spec_path.parent
    ptf = read_ptf(stock_path)

    digest = hashlib.sha256(ptf.image).hexdigest()
    if digest != spec["stock_image_sha256"]:
        sys.exit(f"stock image hash {digest} does not match {spec['firmware']}")

    base = int(spec["base"], 16)
    bin_path, sym_path = pdir / spec["bin"], pdir / spec["sym"]
    if not bin_path.exists() and "prebuilt_bin" in spec:
        bin_path, sym_path = pdir / spec["prebuilt_bin"], pdir / spec["prebuilt_sym"]
        print(f"no local build, using the prebuilt patch: {bin_path.relative_to(pdir.parent.parent)}")
    blob = bin_path.read_bytes()
    syms = load_symbols(sym_path)
    stock_end = ptf.base + len(ptf.image)
    if base < stock_end:
        sys.exit(f"patch base {base:#x} overlaps the stock image (ends {stock_end:#x})")

    image = bytearray(ptf.image)
    for br in spec["branches"]:
        at = int(br["at"], 16)
        off = at - ptf.base
        old = bytes.fromhex(br["old"])
        if image[off:off + 4] != old:
            sys.exit(f"{at:#x}: expected {old.hex()}, found {image[off:off + 4].hex()}")
        new = thumb_bl(at, syms[br["to"]])
        image[off:off + 4] = new
        print(f"{at:#x}: bl -> {br['to']} @ {syms[br['to']]:#x}  ({old.hex()} -> {new.hex()})  {br.get('note', '')}")

    for wd in spec.get("words", []):
        at = int(wd["at"], 16)
        off = at - ptf.base
        old = bytes.fromhex(wd["old"])
        if at & 3 or image[off:off + 4] != old:
            sys.exit(f"{at:#x}: expected aligned word {old.hex()}, found {image[off:off + 4].hex()}")
        new = struct.pack("<I", syms[wd["to"]] | (1 if wd.get("thumb") else 0))
        image[off:off + 4] = new
        print(f"{at:#x}: word -> {wd['to']} @ {syms[wd['to']]:#x}  ({old.hex()} -> {new.hex()})  {wd.get('note', '')}")

    tail = b"\xff" * (base - stock_end) + blob
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
        added.append(Record(0, a, tail[i:i + 16]))
    full_image = bytes(image) + tail
    patched.records = records + added + end_records
    patched.image = bytearray(full_image)

    text = encode_ptf(patched)
    out_path.write_bytes(text.encode())

    check = read_ptf(out_path)
    if check.base != ptf.base or bytes(check.image) != full_image or check.start != ptf.start:
        sys.exit("read-back mismatch")
    if len(check.data_ranges()) != 1:
        sys.exit(f"output has gaps: {check.data_ranges()}")
    print(f"wrote {out_path}: {len(full_image)} bytes ({len(blob)} patch bytes at {base:#x})")
    print(f"file sha256 {hashlib.sha256(out_path.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
