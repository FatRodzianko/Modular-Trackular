"""Apply a patch (patch/<name>/patch.json) to a stock .ptf and write a new .ptf.

Usage: python tools/patch_ptf.py <stock.ptf> <patch.json> <out.ptf>

A spec lists "bin"/"sym"/"rel" (the patch build output: code, symbols and relocation
table from tools/mkrel.py; "prebuilt_bin"/"prebuilt_sym"/"prebuilt_rel" are used when
there is no local build), "branches" (Thumb-2 BL sites to retarget) and optional
"words" (aligned 32-bit literals to replace with a symbol address; "thumb": true sets
bit 0, for function pointers such as vtable entries).

Placement: a patch goes right after the stock image, or right after the patch before
it, 16-byte aligned. Its relocation table lists the words that hold its own
addresses; they and its symbols are shifted to where it lands, so it can sit anywhere.
A spec with a fixed "base" (and no relocation table) sits exactly there instead.

RAM: a patch's state variables are its .mt_ram section; the relocation table gives
its size and the words that hold RAM addresses. The patcher hands out RAM from the
free regions in RAM_REGIONS (FINDINGS section 15), patch by patch in the order
listed: each patch's whole block goes into the first region in its order (the
default, or the spec's "ram_regions") that still has room, 16-byte aligned, and its
RAM words are moved to match. A spec with "ram_startup": true (state used before
main, e.g. from a static constructor) never gets SDRAM, which may not be ready that
early. The ITCM tail is only used when a spec names it (aligned accesses only). A
spec with "standalone": true (ramdiag) can't go into a bundle. The patcher writes
<out>.map.json next to the output: where each patch's code and RAM landed.

A bundle spec combines several patches into one firmware: "include" lists their
patch.json files (relative to the bundle's folder), in the order they are placed, and
"version" gives the version text (Config shows 1.2.0-<version>, at most 3 characters).
The included patches' own version-text words ("version": true) are dropped, and the
bundle's text is appended after the last patch. Hook sites must not overlap.

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
ALIGN = 16                          # where a patch may start (keeps 4-byte literal alignment)

# Free RAM for patches (FINDINGS section 15), all checked on hardware with ramdiag.
# name: (start, end, flags). "startup": usable before main; "opt_in": only when a spec
# names it.
RAM_REGIONS = {
    "ocram_tail":       (0x20205000, 0x20208000, {"startup"}),     # 12,288 B
    "dtcm_above_heap":  (0x20051C70, 0x20053000, {"startup"}),     # 5,008 B, below the stack
    "sdram_top":        (0x83FF9BF0, 0x84000000, set()),           # 25,616 B, cached
    "dtcm_after_audio": (0x200094C0, 0x20009800, {"startup"}),     # 832 B
    "itcm_tail":        (0x0001FB40, 0x00020000, {"startup", "opt_in"}),  # 1,216 B, aligned only
}
RAM_DEFAULT_ORDER = ["ocram_tail", "dtcm_above_heap", "sdram_top", "dtcm_after_audio"]


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


def load_rel(path: Path) -> dict:
    """A relocation table from tools/mkrel.py: code link base and size, the words that
    hold code addresses, and the RAM link base, size and RAM words (if any)."""
    rel = {"link": None, "size": None, "offsets": [], "ram_link": None, "ram_size": 0, "ram_offsets": []}
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] in ("link", "size", "ram_link", "ram_size"):
            rel[parts[0]] = int(parts[1], 0)
        elif parts[0] == "ram":
            rel["ram_offsets"].append(int(parts[1], 0))
        else:
            rel["offsets"].append(int(parts[0], 0))
    if rel["link"] is None or rel["size"] is None:
        sys.exit(f"{path}: not a relocation table")
    return rel


def load_patch(spec: dict, pdir: Path, in_bundle: bool) -> dict:
    """One patch: its code blob, symbols, relocation table and hook sites."""
    files = {k: spec.get(k) for k in ("bin", "sym", "rel")}
    if not (pdir / spec["bin"]).exists() and "prebuilt_bin" in spec:
        files = {k: spec.get("prebuilt_" + k) for k in ("bin", "sym", "rel")}
        print(f"no local build, using the prebuilt patch: {(pdir / files['bin']).relative_to(pdir.parent.parent)}")
    blob = (pdir / files["bin"]).read_bytes()
    syms = load_symbols(pdir / files["sym"])
    pinned = int(spec["base"], 16) if "base" in spec else None
    rel = load_rel(pdir / files["rel"]) if files["rel"] else None
    if rel is None and pinned is None:
        sys.exit(f"{spec['name']}: needs a relocation table (\"rel\") or a fixed \"base\"")
    if rel and rel["size"] != len(blob):
        sys.exit(f"{spec['name']}: relocation table is for {rel['size']} bytes, the code is {len(blob)}")
    if in_bundle and spec.get("standalone"):
        sys.exit(f"{spec['name']} is a standalone patch and can't go into a bundle")
    order = spec.get("ram_regions", RAM_DEFAULT_ORDER)
    for r in order:
        if r not in RAM_REGIONS:
            sys.exit(f"{spec['name']}: unknown RAM region {r!r} (known: {', '.join(RAM_REGIONS)})")
    words = [wd for wd in spec.get("words", []) if not (in_bundle and wd.get("version"))]
    return {"name": spec["name"], "pinned": pinned, "link": rel["link"] if rel else pinned,
            "rel": rel["offsets"] if rel else None, "blob": blob, "syms": syms,
            "ram_link": rel["ram_link"] if rel else None, "ram_size": rel["ram_size"] if rel else 0,
            "ram_rel": rel["ram_offsets"] if rel else [], "ram_order": order,
            "ram_startup": bool(spec.get("ram_startup")),
            "branches": spec["branches"], "words": words}


def assign_ram(parts: list[dict]) -> None:
    """Give each patch with RAM a block from the free regions, in the order listed."""
    free = {name: start for name, (start, _, _) in RAM_REGIONS.items()}
    for p in parts:
        p["ram"] = None
        if not p["ram_size"]:
            continue
        for name in p["ram_order"]:
            start, end, flags = RAM_REGIONS[name]
            if p["ram_startup"] and "startup" not in flags:
                continue
            base = (free[name] + ALIGN - 1) & ~(ALIGN - 1)
            if base + p["ram_size"] <= end:
                p["ram"] = (base, name)
                free[name] = base + p["ram_size"]
                break
        if p["ram"] is None:
            left = ", ".join(f"{n} {RAM_REGIONS[n][1] - free[n]} B" for n in p["ram_order"])
            sys.exit(f"{p['name']}: no free RAM region has {p['ram_size']} bytes left ({left})")


def place(p: dict, base: int) -> None:
    """Put patch p at `base`: shift its relocated words and its symbols."""
    delta = base - p["link"]
    if delta and p["rel"] is None:
        sys.exit(f"{p['name']} is linked at {p['link']:#x} and has no relocation table to move it")
    ram_delta = p["ram"][0] - p["ram_link"] if p.get("ram") else 0
    blob = bytearray(p["blob"])
    for offs, d in ((p["rel"] or [], delta), (p["ram_rel"], ram_delta)):
        for off in offs:
            v = struct.unpack_from("<I", blob, off)[0]
            struct.pack_into("<I", blob, off, (v + d) & 0xFFFFFFFF)
    p["base"], p["blob"] = base, bytes(blob)
    p["syms"] = {k: v + delta for k, v in p["syms"].items()}


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

    # RAM: each patch's block from the free regions, in the order listed.
    assign_ram(parts)

    # Code layout: the patches in the order listed, each right after the one before
    # (16-byte aligned), or at its fixed "base" if it has one.
    stock_end = ptf.base + len(ptf.image)
    end = stock_end
    tail = bytearray()
    for p in parts:
        base = p["pinned"] if p["pinned"] is not None else (end + ALIGN - 1) & ~(ALIGN - 1)
        if base < end:
            sys.exit(f"{p['name']} at {base:#x} overlaps what comes before it (ends {end:#x})")
        place(p, base)
        tail += b"\xff" * (base - end) + p["blob"]
        end = base + len(p["blob"])

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

    words = [(wd, p["syms"][wd["to"]] | (1 if wd.get("thumb") else 0), p["name"])
             for p in parts for wd in p["words"]]
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
    for p in parts:
        if p["ram"]:
            print(f"  RAM: {p['name']} {p['ram_size']} bytes at {p['ram'][0]:#x} ({p['ram'][1]})")
    layout = {
        "output": out_path.name,
        "version_text": "1.2.0-" + (version.decode() if version is not None else "(the patch's own)"),
        "patches": [{
            "name": p["name"],
            "code": [f"{p['base']:#010x}", f"{p['base'] + len(p['blob']):#010x}"],
            "ram": ({"region": p["ram"][1], "start": f"{p['ram'][0]:#010x}",
                     "end": f"{p['ram'][0] + p['ram_size']:#010x}"} if p["ram"] else None),
        } for p in parts],
    }
    map_path = out_path.with_suffix(".map.json")
    map_path.write_text(json.dumps(layout, indent=2) + "\n", newline="\n")
    print(f"file sha256 {hashlib.sha256(out_path.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
