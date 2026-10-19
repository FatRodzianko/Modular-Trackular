"""Make a patch's relocation table by comparing links of the same code.

Usage: python tools/mkrel.py <linked.bin> <flash-shifted.bin> <link base> <shift> <out.rel>
                             [--ram <ram-shifted.bin> <ram link> <ram size>]

The patch code is linked at its link base, then again with the code `shift` bytes
higher, and (with --ram) again with its RAM (the .mt_ram section) `shift` bytes
higher. Every 32-bit word that differs between the first two links is an absolute
address inside the patch's code (a pointer to its own strings, data or functions,
Thumb bit included); every word that differs between the first and the third is an
address inside its RAM. The table lists both kinds, so tools/patch_ptf.py can place
the code and the RAM anywhere by adding the move to those words. Calls and
PC-relative loads inside the patch, and pointers into the stock firmware, are the
same in all links and need nothing.

Anything else that differs (a byte change outside an aligned word, a word that
changed by another amount, a word that doesn't point into the code or RAM, or one
that changed in both) means the code can't be moved this way, and the table is
refused.

Output (text):
  link 0x601BC000
  size 2348
  ram_link 0x20207FC0      (with --ram)
  ram_size 48
  0x0040                   a word holding a code address (offset into the .bin)
  ram 0x0060               a word holding a RAM address
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


def changed_words(a: bytes, b: bytes, shift: int, lo: int, hi: int, what: str) -> list[int]:
    if len(a) != len(b):
        sys.exit(f"the {what} links differ in size ({len(a)} vs {len(b)} bytes)")
    words = sorted({i & ~3 for i in range(len(a)) if a[i] != b[i]})
    for off in words:
        if off + 4 > len(a):
            sys.exit(f"offset {off:#x}: a difference in the last, partial word")
        wa, wb = struct.unpack_from("<I", a, off)[0], struct.unpack_from("<I", b, off)[0]
        if (wb - wa) & 0xFFFFFFFF != shift:
            sys.exit(f"offset {off:#x}: {wa:#010x} -> {wb:#010x} is not a {what} shift by {shift:#x}")
        if not lo <= (wa & ~1) <= hi:
            sys.exit(f"offset {off:#x}: {wa:#010x} doesn't point into the patch's {what}")
    return words


def main() -> int:
    args = sys.argv[1:]
    ram = None
    if "--ram" in args:
        i = args.index("--ram")
        ram = args[i + 1:i + 4]
        args = args[:i]
    a_path, b_path, base_s, shift_s, out_path = args[:5]
    a, b = Path(a_path).read_bytes(), Path(b_path).read_bytes()
    base, shift = int(base_s, 0), int(shift_s, 0)
    if shift % 16:
        sys.exit("the shift must be a multiple of 16")
    code = changed_words(a, b, shift, base, base + len(a), "code")
    lines = [f"link {base:#010x}", f"size {len(a)}"]
    ram_words: list[int] = []
    if ram:
        r_bin, r_link, r_size = Path(ram[0]).read_bytes(), int(ram[1], 0), int(ram[2], 0)
        ram_words = changed_words(a, r_bin, shift, r_link, r_link + r_size, "RAM")
        both = set(code) & set(ram_words)
        if both:
            sys.exit(f"words changed by both shifts: {[hex(x) for x in sorted(both)]}")
        lines += [f"ram_link {r_link:#010x}", f"ram_size {r_size}"]
    lines += [f"{off:#06x}" for off in code] + [f"ram {off:#06x}" for off in ram_words]
    Path(out_path).write_text("\n".join(lines) + "\n", newline="\n")
    extra = f", {len(ram_words)} RAM words ({int(ram[2], 0)} bytes of RAM)" if ram else ""
    print(f"{out_path}: {len(code)} relocated words{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
