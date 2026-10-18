"""Print the version text a firmware image would show, using its own sprintf.

  python tools/emu/check_version.py <file.ptf>

Mirrors 0x600cbe88 (non-beta path): sprintf(buf, *(char **)0x600cbf9c, 1, 2, 0).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from machine import Machine  # noqa: E402

SPRINTF = 0x60167194
FMT_LITERAL = 0x600CBF9C

m = Machine(Path(sys.argv[1]))
m.uc.mem_map(0x40000000, 0x4000000)
m.uc.mem_map(0xE0000000, 0x100000)
m.call(0x60166294, max_insns=50_000_000)      # static constructors (newlib state, etc.)
fmt = m.r32(FMT_LITERAL)
buf = m.alloc(64)
m.call(SPRINTF, buf, fmt, 1, 2, 0)
text = bytes(m.uc.mem_read(buf, 64)).split(b"\0")[0].decode()
print(f"format at {fmt:#010x} -> version text {text!r}")
