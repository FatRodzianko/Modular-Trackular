"""Find C++ vtables in a Tracker+ image and list their method addresses.

Usage:
  python tools/vtables.py <image.bin> [--methods out.txt]

A vtable candidate is a run of at least 8 Thumb code pointers (odd values inside
the flash code area or ITCM), with at least 4 distinct values, allowing up to 3 zero slots (offset-to-top, RTTI,
pure virtuals). With --methods, every pointer is written one per line, for
ghidra/scripts/CreateFunctions.java.
"""
import struct
import sys

BASE = 0x60030000
CODE_END = 0x6018D6EC          # start of the ITCM load image; code lives below it
ITCM_END = 0x20000


def is_code(v: int) -> bool:
    # ITCM pointers below 0x1000 are almost always small integers (e.g. a prime table)
    return bool(v & 1) and (BASE <= v < CODE_END or 0x1000 <= v < ITCM_END)


def find_vtables(d: bytes):
    runs, o = [], 0x400        # skip the vector table
    while o < len(d) - 4:
        n = zeros = 0
        while o + 4 * n < len(d) - 4:
            v = struct.unpack_from("<I", d, o + 4 * n)[0]
            if is_code(v):
                n += 1
            elif v == 0 and zeros < 3:
                n += 1
                zeros += 1
            else:
                break
        distinct = {struct.unpack_from("<I", d, o + 4 * i)[0] for i in range(n)}
        if n - zeros >= 8 and len(distinct) >= 4:
            runs.append((BASE + o, [struct.unpack_from("<I", d, o + 4 * i)[0] for i in range(n)]))
            o += 4 * n
        else:
            o += 4
    return runs


def main():
    d = open(sys.argv[1], "rb").read()
    runs = find_vtables(d)
    for a, m in runs:
        print(f"{a:#x} {len(m):3d} " + " ".join(f"{x:x}" for x in m))
    if "--methods" in sys.argv:
        out = sys.argv[sys.argv.index("--methods") + 1]
        ptrs = sorted({x for _, m in runs for x in m if is_code(x)})
        with open(out, "w") as f:
            f.write("\n".join(f"{x:#x}" for x in ptrs) + "\n")
        print(f"# {len(runs)} vtables, {len(ptrs)} method pointers -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
