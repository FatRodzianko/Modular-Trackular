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

Late RAM: a spec with "late_ram": "<symbol>" gets a second block, for state used only
after the unit has booted (it may be in SDRAM). The symbol names an aligned word in the
patch code that holds the block's size as linked; the patcher gives the block out from
the default region order (startup or not), after every patch's own block, so the other
blocks land where they would without it, and writes its address over the size. The
patch reaches the block only through that word.

A bundle spec combines several patches into one firmware: "include" lists their
patch.json files (relative to the bundle's folder), in the order they are placed, and
"version" gives the version text (Config shows 1.2.0-<version>, at most 3 characters).
The included patches' own version-text words ("version": true) are dropped, and the
bundle's text is appended after the last patch.

Chained hooks: a branch or word with "next": "<symbol>" names a 4-byte word in the
patch (linked as 0) that the patcher fills with what the site pointed to before: the
stock target (Thumb bit set) or the hook of the patch listed before it. The patch
calls through that word instead of calling the stock function itself. Several patches
can hook the same site this way; the last one listed runs first. A site hooked by more
than one patch must be chained by all of them, otherwise the patcher refuses.

Non-BL sites: a branch with "replaces": "insns" overwrites 4 bytes that aren't a BL
(two 16-bit instructions, or one 32-bit one) with a BL to the hook, which must do their
work itself. There is no old target to chain to, so "next" is refused there, and so is
a second patch on the site. No two sites may overlap: one starting 1 to 3 bytes into
another is refused.

ITCM hooks: "itcm_branches" lists BL sites in the sequencer's ITCM code ("at" is the
ITCM address; the bytes live in the ITCM image in flash, which the startup copy table
copies to ITCM at boot). ITCM code can't BL to flash, so the patcher makes a stub per
site: "mov r2, <args[0]>; mov r3, <args[1]>" for the registers named in "args" (up to
two of r4-r12 and sp; none: no moves), then "ldr.w pc, [pc, #0]" and the hook's
address. The stubs go at the start of the ITCM tail (which is then not given out as
RAM), their bytes after the patches in flash, and entry 3 of the startup copy table
(stock: unused, length 0) is set to copy them there at boot. The site's BL is rewritten
to the stub; the stub jumps, so the hook returns straight to the ITCM caller with the
caller's arguments in r0, r1 and the named registers in r2, r3. A chain word on an ITCM
site gets the stock target or the hook (not the stub) of the patch before it, which
the hook calls with the same four arguments; so all patches on one ITCM site must name
the same "args".

- Refuses unless the stock image matches the SHA-256 in patch.json.
- Every branch or word site is checked against its original bytes before it is rewritten
  (as a Thumb-2 BL, or as the symbol address for words).
- The patch binaries are appended after the stock image. Gaps up to each base address
  are filled with 0xFF and written as data, so the output has no hole.
- An output that would end within the first 256 bytes of a 4 KB flash sector gets
  0xFF bytes up to 16 bytes into the sector's second page: the bootloader doesn't
  erase a sector whose data stop in its first page (FINDINGS section 26).
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
FLASH_SECTOR = 0x1000               # flash erase sector
FLASH_PAGE = 0x100                  # flash program page

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

# The startup copy table (FINDINGS sections 2 and 22): 4 x {load, exec, length}.
COPY_TABLE = 0x600302A0
ITCM_ENTRY = 1                      # the ITCM image: flash 0x6018d6ec -> ITCM 0x8
STUB_ENTRY = 3                      # unused in stock firmware; carries the ITCM stubs
STUB_ENTRY_STOCK = (0x601AD21C, 0x80000000, 0)
ITCM_STUBS = RAM_REGIONS["itcm_tail"][0]
ITCM_ARG_REGS = {f"r{n}": n for n in range(4, 13)} | {"sp": 13}


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


def bl_target(image: bytes, base: int, at: int) -> int:
    """Where the Thumb-2 BL at `at` goes (image starts at `base`)."""
    hw1, hw2 = struct.unpack_from("<HH", image, at - base)
    s = (hw1 >> 10) & 1
    j1, j2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
    i1, i2 = ~(j1 ^ s) & 1, ~(j2 ^ s) & 1
    off = (s << 24) | (i1 << 23) | (i2 << 22) | ((hw1 & 0x3FF) << 12) | ((hw2 & 0x7FF) << 1)
    if s:
        off -= 1 << 25
    return at + 4 + off


def copy_entry(image: bytes, base: int, n: int) -> tuple[int, int, int]:
    """Entry n of the startup copy table: (load, exec, length)."""
    return struct.unpack_from("<3I", image, COPY_TABLE + 12 * n - base)


def itcm_flash(image: bytes, base: int, at: int) -> int:
    """Where the ITCM code at `at` (4 bytes) comes from in flash."""
    load, exe, size = copy_entry(image, base, ITCM_ENTRY)
    if at & 1 or not exe <= at <= exe + size - 4:
        sys.exit(f"ITCM {at:#x} is not in the ITCM image ({exe:#x}..{exe + size:#x})")
    return load + at - exe


def itcm_stub(args: tuple[str, ...], hook: int) -> bytes:
    """mov r2/r3 from the named registers (nop for a missing one), ldr.w pc, [pc, #0],
    the hook's address. 8 bytes without args, 12 with; starts 4-byte aligned."""
    jump = struct.pack("<HHI", 0xF8DF, 0xF000, hook | 1)
    if not args:
        return jump
    moves = [0x4600 | (ITCM_ARG_REGS[r] << 3) | (2 + i) for i, r in enumerate(args)]
    moves += [0xBF00] * (2 - len(moves))
    return struct.pack("<HH", *moves) + jump


def itcm_stub_size(args: tuple[str, ...]) -> int:
    return 12 if args else 8


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


def late_size(spec: dict, blob: bytes, syms: dict[str, int], rel: dict | None, pinned: int | None) -> int:
    """The size of a patch's late RAM block: its "late_ram" word as linked (0: none)."""
    symbol = spec.get("late_ram")
    if symbol is None:
        return 0
    if symbol not in syms:
        sys.exit(f"{spec['name']}: no symbol {symbol!r} for \"late_ram\"")
    off = syms[symbol] - (rel["link"] if rel else pinned)
    if off & 3 or not 0 <= off <= len(blob) - 4:
        sys.exit(f"{spec['name']}: {symbol} is not an aligned word in the patch code")
    if rel and (off in rel["offsets"] or off in rel["ram_offsets"]):
        sys.exit(f"{spec['name']}: {symbol} is in the relocation table")
    size = struct.unpack_from("<I", blob, off)[0]
    if not 0 < size <= 0x10000:
        sys.exit(f"{spec['name']}: {symbol} holds {size}, not a late RAM size (1 to 65536 bytes)")
    return size


def load_patch(spec: dict, pdir: Path, in_bundle: bool) -> dict:
    """One patch: its code blob, symbols, relocation table and hook sites."""
    files = {k: spec.get(k) for k in ("bin", "sym", "rel")}
    if not (pdir / spec["bin"]).exists() and "prebuilt_bin" in spec:
        files = {k: spec.get("prebuilt_" + k) for k in ("bin", "sym", "rel")}
        shown, root = pdir / files["bin"], pdir.parent.parent
        print(f"no local build, using the prebuilt patch: {shown.relative_to(root) if shown.is_relative_to(root) else shown}")
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
    for br in spec["branches"]:
        if "replaces" not in br:
            continue
        if br["replaces"] != "insns":
            sys.exit(f"{spec['name']}: site {br['at']}: \"replaces\" takes \"insns\", not {br['replaces']!r}")
        if "next" in br:
            sys.exit(f"{spec['name']}: site {br['at']} replaces instructions, not a BL: nothing to chain (\"next\")")
    for br in spec.get("itcm_branches", []):
        args = br.get("args", [])
        if len(args) > 2 or any(r not in ITCM_ARG_REGS for r in args):
            sys.exit(f"{spec['name']}: ITCM site {br['at']}: \"args\" takes up to two of "
                     f"{', '.join(ITCM_ARG_REGS)}")
    return {"name": spec["name"], "pinned": pinned, "link": rel["link"] if rel else pinned,
            "rel": rel["offsets"] if rel else None, "blob": blob, "syms": syms,
            "ram_link": rel["ram_link"] if rel else None, "ram_size": rel["ram_size"] if rel else 0,
            "ram_rel": rel["ram_offsets"] if rel else [], "ram_order": order,
            "ram_startup": bool(spec.get("ram_startup")),
            "late_word": spec.get("late_ram"), "late_size": late_size(spec, blob, syms, rel, pinned),
            "branches": spec["branches"], "words": words,
            "itcm_branches": spec.get("itcm_branches", [])}


def itcm_sites(parts: list[dict]) -> dict[int, tuple[str, ...]]:
    """ITCM site -> the registers its stub passes, in the order the stubs are laid out."""
    sites: dict[int, tuple[str, ...]] = {}
    for p in parts:
        for br in p["itcm_branches"]:
            at, args = int(br["at"], 16), tuple(br.get("args", []))
            if sites.get(at, args) != args:
                sys.exit(f"ITCM {at:#x}: hooked with different \"args\" ({', '.join(sites[at]) or 'none'} "
                         f"and {', '.join(args) or 'none'})")
            sites[at] = args
    return sites


def assign_ram(parts: list[dict], itcm_reserved: int = 0) -> None:
    """Give each patch with RAM a block from the free regions, in the order listed.
    The first `itcm_reserved` bytes of the ITCM tail hold the ITCM stubs."""
    free = {name: start for name, (start, _, _) in RAM_REGIONS.items()}
    free["itcm_tail"] += (itcm_reserved + ALIGN - 1) & ~(ALIGN - 1)
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
    # Late blocks after all the others: they don't move them.
    for p in parts:
        p["late"] = None
        if not p["late_size"]:
            continue
        for name in RAM_DEFAULT_ORDER:
            base = (free[name] + ALIGN - 1) & ~(ALIGN - 1)
            if base + p["late_size"] <= RAM_REGIONS[name][1]:
                p["late"] = (base, name)
                free[name] = base + p["late_size"]
                break
        if p["late"] is None:
            left = ", ".join(f"{n} {RAM_REGIONS[n][1] - free[n]} B" for n in RAM_DEFAULT_ORDER)
            sys.exit(f"{p['name']}: no free RAM region has {p['late_size']} bytes left for its late RAM ({left})")


def set_next(p: dict, symbol: str, value: int, linked: int = 0) -> None:
    """Fill patch p's chain word `symbol` (placed, linked as `linked`: 0) with `value`."""
    if symbol not in p["syms"]:
        sys.exit(f"{p['name']}: no symbol {symbol!r} for \"next\"")
    off = p["syms"][symbol] - p["base"]
    blob = bytearray(p["blob"])
    if off & 3 or not 0 <= off <= len(blob) - 4 or struct.unpack_from("<I", blob, off)[0] != linked:
        sys.exit(f"{p['name']}: {symbol} is not an aligned word holding {linked} in the patch code")
    if off in (p["rel"] or []) or off in p["ram_rel"]:
        sys.exit(f"{p['name']}: {symbol} is in the relocation table")
    struct.pack_into("<I", blob, off, value)
    p["blob"] = bytes(blob)


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

    # The ITCM stubs (one per hooked ITCM site) take the start of the ITCM tail.
    stub_sites = itcm_sites(parts)
    stub_size = sum(itcm_stub_size(args) for args in stub_sites.values())
    if ITCM_STUBS + stub_size > RAM_REGIONS["itcm_tail"][1]:
        sys.exit(f"{len(stub_sites)} ITCM stubs ({stub_size} bytes) don't fit in the ITCM tail")

    # RAM: each patch's block from the free regions, in the order listed.
    assign_ram(parts, stub_size)

    # Code layout: the patches in the order listed, each right after the one before
    # (16-byte aligned), or at its fixed "base" if it has one; then the ITCM stubs'
    # bytes, which the startup copy table copies to ITCM.
    stock_end = ptf.base + len(ptf.image)
    end = stock_end
    for p in parts:
        base = p["pinned"] if p["pinned"] is not None else (end + ALIGN - 1) & ~(ALIGN - 1)
        if base < end:
            sys.exit(f"{p['name']} at {base:#x} overlaps what comes before it (ends {end:#x})")
        place(p, base)
        if p["late"]:
            set_next(p, p["late_word"], p["late"][0], linked=p["late_size"])
        end = base + len(p["blob"])
    stub_flash = None
    if stub_sites:
        stub_flash = (end + ALIGN - 1) & ~(ALIGN - 1)
        end = stub_flash + stub_size

    stock = bytes(ptf.image)
    image = bytearray(ptf.image)
    sites: dict[int, tuple[str, bool]] = {}

    def claim(at: int, who: str, chained: bool) -> int:
        for d in (-3, -2, -1, 1, 2, 3):
            if at + d in sites:
                sys.exit(f"{at:#x} ({who}) overlaps {at + d:#x} ({sites[at + d][0]})")
        if at in sites:
            first, first_chained = sites[at]
            if not (first_chained and chained):
                sys.exit(f"{at:#x}: patched by both {first} and {who}, and not chained by both (\"next\")")
        sites[at] = (who, chained)
        return at - ptf.base

    for p in parts:
        for br in p["branches"]:
            at = int(br["at"], 16)
            off = claim(at, p["name"], "next" in br)
            old = bytes.fromhex(br["old"])
            if stock[off:off + 4] != old:
                sys.exit(f"{at:#x}: expected {old.hex()}, found {stock[off:off + 4].hex()}")
            new = thumb_bl(at, p["syms"][br["to"]])
            chain = ", replaces instructions" if "replaces" in br else ""
            if "next" in br:
                before = bl_target(image, ptf.base, at)
                set_next(p, br["next"], before | 1)
                chain = f", {br['next']} -> {before:#x}"
            image[off:off + 4] = new
            print(f"{at:#x}: bl -> {br['to']} @ {p['syms'][br['to']]:#x}  ({old.hex()} -> {new.hex()}{chain})  {br.get('note', '')}")

    # ITCM sites: chain words get the stock target or the hook before; the BL goes to
    # the site's stub, which jumps to the last hook.
    hooks: dict[int, tuple[int, str]] = {}           # ITCM site -> (last hook, its patch)
    for p in parts:
        for br in p["itcm_branches"]:
            at = int(br["at"], 16)
            flash = itcm_flash(stock, ptf.base, at)
            off = claim(flash, p["name"], "next" in br)
            old = bytes.fromhex(br["old"])
            if stock[off:off + 4] != old:
                sys.exit(f"ITCM {at:#x} (flash {flash:#x}): expected {old.hex()}, found {stock[off:off + 4].hex()}")
            before = hooks[at][0] if at in hooks else bl_target(stock, ptf.base, flash) - flash + at
            chain = ""
            if "next" in br:
                set_next(p, br["next"], before | 1)
                chain = f", {br['next']} -> {before:#x}"
            hooks[at] = (p["syms"][br["to"]], p["name"])
            print(f"ITCM {at:#x}: bl -> stub -> {br['to']} @ {p['syms'][br['to']]:#x}  "
                  f"(r2, r3 = {', '.join(br.get('args', [])) or '-'}{chain})  {br.get('note', '')}")
    stubs = bytearray()
    stub_map = []
    for at, args in stub_sites.items():
        stub = ITCM_STUBS + len(stubs)
        stubs += itcm_stub(args, hooks[at][0])
        flash = itcm_flash(stock, ptf.base, at)
        image[flash - ptf.base:flash - ptf.base + 4] = thumb_bl(at, stub)
        stub_map.append({"at": f"{at:#x}", "stub": f"{stub:#x}", "hook": f"{hooks[at][0]:#010x}",
                         "patch": hooks[at][1]})
    if stub_sites:
        entry = COPY_TABLE + 12 * STUB_ENTRY
        for i, (old, new) in enumerate(zip(STUB_ENTRY_STOCK, (stub_flash, ITCM_STUBS, len(stubs)))):
            off = claim(entry + 4 * i, "ITCM stubs", False)
            if struct.unpack_from("<I", stock, off)[0] != old:
                sys.exit(f"copy table entry {STUB_ENTRY} isn't the unused stock entry")
            struct.pack_into("<I", image, off, new)
        print(f"copy table entry {STUB_ENTRY}: {len(stubs)} bytes of ITCM stubs, flash {stub_flash:#x} -> "
              f"ITCM {ITCM_STUBS:#x}")

    words = [(wd, p["syms"][wd["to"]] | (1 if wd.get("thumb") else 0), p)
             for p in parts for wd in p["words"]]
    if version is not None:
        end = (end + 3) & ~3
        vaddr = end
        text = VERSION_PREFIX + version + b"\0"
        version_text = (vaddr, text)
        end = vaddr + len(text)
        vw = spec["version_word"]
        words.append(({"at": vw["at"], "old": vw["old"], "to": "version text",
                       "note": f"1.2.0-{version.decode()}"}, vaddr, {"name": spec["name"]}))

    for wd, value, p in words:
        at = int(wd["at"], 16)
        off = claim(at, p["name"], "next" in wd)
        old = bytes.fromhex(wd["old"])
        if at & 3 or stock[off:off + 4] != old:
            sys.exit(f"{at:#x}: expected aligned word {old.hex()}, found {stock[off:off + 4].hex()}")
        before = struct.unpack_from("<I", image, off)[0]
        new = struct.pack("<I", value)
        image[off:off + 4] = new
        chain = ""
        if "next" in wd:
            set_next(p, wd["next"], before)
            chain = f", {wd['next']} -> {before:#x}"
        shown = value & ~1 if wd.get("thumb") else value
        print(f"{at:#x}: word -> {wd['to']} @ {shown:#x}  ({old.hex()} -> {new.hex()}{chain})  {wd.get('note', '')}")

    # The patches' code (with their chain words filled), after the stock image, then the
    # ITCM stubs.
    tail = bytearray()
    end = stock_end
    for p in parts:
        tail += b"\xff" * (p["base"] - end) + p["blob"]
        end = p["base"] + len(p["blob"])
    if stub_sites:
        tail += b"\xff" * (stub_flash - end) + stubs
    if version is not None:
        vaddr, text = version_text
        tail += b"\xff" * (vaddr - stock_end - len(tail)) + text
    # Never end inside the first 256-byte page of a 4 KB flash sector: the bootloader
    # leaves such a sector unerased, so that last page comes out ANDed with what an
    # earlier firmware left there (FINDINGS section 26). Pad with 0xFF into page 1.
    end = stock_end + len(tail)
    if 0 < end % FLASH_SECTOR <= FLASH_PAGE:
        tail += b"\xff" * (end - end % FLASH_SECTOR + FLASH_PAGE + ALIGN - end)

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
        if p["late"]:
            print(f"  late RAM: {p['name']} {p['late_size']} bytes at {p['late'][0]:#x} ({p['late'][1]})")
    layout = {
        "output": out_path.name,
        "version_text": "1.2.0-" + (version.decode() if version is not None else "(the patch's own)"),
        "patches": [{
            "name": p["name"],
            "code": [f"{p['base']:#010x}", f"{p['base'] + len(p['blob']):#010x}"],
            "ram": ({"region": p["ram"][1], "start": f"{p['ram'][0]:#010x}",
                     "end": f"{p['ram'][0] + p['ram_size']:#010x}"} if p["ram"] else None),
        } | ({"late_ram": {"region": p["late"][1], "start": f"{p['late'][0]:#010x}",
                           "end": f"{p['late'][0] + p['late_size']:#010x}"}} if p["late"] else {})
            for p in parts],
    }
    if stub_sites:
        layout["itcm_stubs"] = {"flash": f"{stub_flash:#010x}", "itcm": f"{ITCM_STUBS:#x}",
                                "size": len(stubs), "sites": stub_map}
    map_path = out_path.with_suffix(".map.json")
    map_path.write_text(json.dumps(layout, indent=2) + "\n", newline="\n")
    print(f"file sha256 {hashlib.sha256(out_path.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
