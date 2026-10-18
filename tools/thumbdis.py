"""Quick Thumb disassembly with literal-pool values and string previews.

Usage: python tools/thumbdis.py <image.bin> <base> <start> <end>
"""
import re
import struct
import sys

from capstone import CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB, Cs


def main():
    path, base, start, end = sys.argv[1], *(int(x, 0) for x in sys.argv[2:5])
    d = open(path, "rb").read()

    def w(a):
        return struct.unpack_from("<I", d, a - base)[0] if base <= a < base + len(d) - 3 else None

    def s(a):
        if not (base <= a < base + len(d)):
            return ""
        m = re.match(rb"[\x20-\x7e]{3,60}", d[a - base:])
        return f' "{m.group().decode()}"' if m else ""

    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    a = start
    while a < end:
        got = False
        for i in md.disasm(d[a - base:end - base], a):
            extra = ""
            m = re.search(r"\[pc, #(0x[0-9a-f]+|\d+)\]", i.op_str)
            if i.mnemonic.startswith("ldr") and m:
                lit = ((i.address + 4) & ~3) + int(m.group(1), 0)
                v = w(lit)
                if v is not None:
                    extra = f"   ; ={v:#x}{s(v)}"
            print(f"{i.address:#x}: {i.mnemonic:8} {i.op_str}{extra}")
            a = i.address + i.size
            got = True
        if not got or a < end:
            print(f"{a:#x}: .short {struct.unpack_from('<H', d, a - base)[0]:#06x}")
            a += 2


if __name__ == "__main__":
    main()
