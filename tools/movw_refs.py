"""Find Thumb-2 MOVW/MOVT pairs that build a given 32-bit address.

Usage: python tools/movw_refs.py <image.bin> <base> <addr> [<addr> ...]
Literal-pool loads are found by searching for the 4-byte value; this covers the
other common way GCC materialises addresses.
"""
import struct
import sys


def imm16(hw1: int, hw2: int) -> int:
    i = (hw1 >> 10) & 1
    imm4 = hw1 & 0xF
    imm3 = (hw2 >> 12) & 7
    imm8 = hw2 & 0xFF
    return (imm4 << 12) | (i << 11) | (imm3 << 8) | imm8


def scan(d: bytes, base: int):
    movw, movt = {}, {}
    for off in range(0, len(d) - 4, 2):
        hw1, hw2 = struct.unpack_from("<HH", d, off)
        if hw1 & 0xFBF0 == 0xF240 and hw2 & 0x8000 == 0:
            movw.setdefault(imm16(hw1, hw2), []).append((base + off, (hw2 >> 8) & 0xF))
        elif hw1 & 0xFBF0 == 0xF2C0 and hw2 & 0x8000 == 0:
            movt.setdefault(imm16(hw1, hw2), []).append((base + off, (hw2 >> 8) & 0xF))
    return movw, movt


def main():
    path, base = sys.argv[1], int(sys.argv[2], 0)
    d = open(path, "rb").read()
    movw, movt = scan(d, base)
    for a in (int(x, 0) for x in sys.argv[3:]):
        hits = []
        for wa, rd in movw.get(a & 0xFFFF, []):
            for ta, rt in movt.get(a >> 16, []):
                if rt == rd and 0 < ta - wa <= 32:
                    hits.append(wa)
        lit = []
        v = struct.pack("<I", a)
        i = d.find(v)
        while i >= 0:
            lit.append(base + i)
            i = d.find(v, i + 1)
        print(f"{a:#010x}: movw/movt {[hex(h) for h in hits]}  literal {[hex(h) for h in lit]}")


if __name__ == "__main__":
    main()
