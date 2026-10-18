"""Read, convert and re-encode Polyend Tracker+ firmware (.ptf, Intel HEX).

Usage:
  python tools/ptf.py info  <file.ptf>
  python tools/ptf.py tobin <file.ptf> <out.bin>
  python tools/ptf.py check <file.ptf>     # re-encode and compare byte for byte

A .ptf is Intel HEX with CRLF line endings: type 04 (extended linear address),
00 (data, variable lengths that follow the linker sections), one 05 (start
address) and 01 (end). Tracker+ images load at 0x60030000 (FlexSPI flash); the
bootloader below that is not part of the file.

Re-encoding keeps the stock record layout and only rewrites the data and
checksum of records whose bytes changed, so an unmodified image reproduces the
stock file exactly.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Record:
    kind: int
    addr: int          # absolute address for data records, raw 16-bit field otherwise
    payload: bytes


@dataclass
class Ptf:
    records: list[Record]
    base: int          # lowest data address
    image: bytearray   # 0xFF-filled from base to the highest data address
    start: int | None  # entry point from the type 05 record

    def data_ranges(self) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        for r in self.records:
            if r.kind != 0:
                continue
            s, e = r.addr, r.addr + len(r.payload)
            if ranges and ranges[-1][1] == s:
                ranges[-1] = (ranges[-1][0], e)
            else:
                ranges.append((s, e))
        return ranges


def read_ptf(path: Path) -> Ptf:
    records: list[Record] = []
    ext = 0
    start = None
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line:
            continue
        raw = bytes.fromhex(line[1:])
        if line[0] != ":" or sum(raw) & 0xFF or raw[0] != len(raw) - 5:
            raise ValueError(f"{path.name}:{n}: bad record")
        kind, addr16, payload = raw[3], int.from_bytes(raw[1:3], "big"), raw[4:-1]
        if kind == 0:
            records.append(Record(0, ext + addr16, payload))
        elif kind == 4:
            ext = int.from_bytes(payload, "big") << 16
            records.append(Record(4, addr16, payload))
        elif kind == 5:
            start = int.from_bytes(payload, "big")
            records.append(Record(5, addr16, payload))
        elif kind == 1:
            records.append(Record(1, addr16, payload))
            break
        else:
            raise ValueError(f"{path.name}:{n}: unsupported record type {kind}")
    data = [r for r in records if r.kind == 0]
    base = min(r.addr for r in data)
    top = max(r.addr + len(r.payload) for r in data)
    image = bytearray(b"\xff" * (top - base))
    for r in data:
        image[r.addr - base:r.addr - base + len(r.payload)] = r.payload
    return Ptf(records, base, image, start)


def _line(count: int, addr16: int, kind: int, payload: bytes) -> str:
    raw = bytes([count, addr16 >> 8, addr16 & 0xFF, kind]) + payload
    return ":" + (raw + bytes([(-sum(raw)) & 0xFF])).hex().upper()


def encode_ptf(ptf: Ptf) -> str:
    """Re-encode using the original record layout and the (possibly patched) image."""
    out = []
    for r in ptf.records:
        if r.kind == 0:
            off = r.addr - ptf.base
            payload = bytes(ptf.image[off:off + len(r.payload)])
            out.append(_line(len(payload), r.addr & 0xFFFF, 0, payload))
        else:
            out.append(_line(len(r.payload), r.addr, r.kind, r.payload))
    return "\r\n".join(out) + "\r\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, src = argv[0], Path(argv[1])
    ptf = read_ptf(src)
    if cmd == "info":
        print(f"file      {src.name}")
        print(f"records   {len(ptf.records)}")
        print(f"base      {ptf.base:#010x}")
        print(f"size      {len(ptf.image):#x} ({len(ptf.image)} bytes)")
        print(f"entry     {ptf.start:#010x}" if ptf.start is not None else "entry     none")
        for s, e in ptf.data_ranges():
            print(f"range     {s:#010x}-{e:#010x}")
    elif cmd == "tobin":
        Path(argv[2]).write_bytes(ptf.image)
        print(f"wrote {argv[2]} ({len(ptf.image)} bytes, load at {ptf.base:#010x})")
    elif cmd == "check":
        same = encode_ptf(ptf).encode() == src.read_bytes()
        print("round trip OK" if same else "round trip MISMATCH")
        return 0 if same else 2
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
