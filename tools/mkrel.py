"""Make a patch's relocation table by comparing two links of the same code.

Usage: python tools/mkrel.py <linked.bin> <shifted.bin> <link base> <shift> <out.rel>

The patch code is linked twice: at its link base, and `shift` bytes higher. Every
32-bit word that differs between the two is an absolute address of something inside
the patch (a pointer to its own strings, data or functions, Thumb bit included). The
table lists those words, so tools/patch_ptf.py can place the patch at any address by
adding (new base - link base) to them. Calls and PC-relative loads inside the patch,
and pointers into the stock firmware, are the same in both links and need nothing.

Anything else that differs (a byte change outside an aligned word, a word that
changed by another amount, or one that doesn't point into the patch) means the code
can't be moved this way, and the table is refused.

Output (text):
  link 0x601BC000
  size 2348
  0x0040            one line per word to adjust (offset into the .bin)
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


def main() -> int:
    a_path, b_path, base_s, shift_s, out_path = sys.argv[1:6]
    a, b = Path(a_path).read_bytes(), Path(b_path).read_bytes()
    base, shift = int(base_s, 0), int(shift_s, 0)
    if len(a) != len(b):
        sys.exit(f"the two links differ in size ({len(a)} vs {len(b)} bytes)")
    if shift % 16:
        sys.exit("the shift must be a multiple of 16")
    words = sorted({i & ~3 for i in range(len(a)) if a[i] != b[i]})
    for off in words:
        if off + 4 > len(a):
            sys.exit(f"offset {off:#x}: a difference in the last, partial word")
        wa, wb = struct.unpack_from("<I", a, off)[0], struct.unpack_from("<I", b, off)[0]
        if (wb - wa) & 0xFFFFFFFF != shift:
            sys.exit(f"offset {off:#x}: {wa:#010x} -> {wb:#010x} is not a shift by {shift:#x}")
        if not base <= (wa & ~1) <= base + len(a):
            sys.exit(f"offset {off:#x}: {wa:#010x} doesn't point into the patch")
    lines = [f"link {base:#010x}", f"size {len(a)}"] + [f"{off:#06x}" for off in words]
    Path(out_path).write_text("\n".join(lines) + "\n", newline="\n")
    print(f"{out_path}: {len(words)} relocated words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
