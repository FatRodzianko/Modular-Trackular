"""Check how tools/patch_ptf.py places and relocates patches, without the emulator.

  python tools/test_layout.py <stock TrackerPlus_1.2.0.ptf> [out dir]

Builds, from the repository's patch specs (local builds if present, else prebuilt):
  - colorsong alone, humanize alone, interpolate alone, masterfx alone, swing alone,
    visualizer alone, ramdiag alone,
  - versiontest alone (pinned base; byte-identical to MT0),
  - patch/all (colorsong, humanize, interpolate, masterfx, swing, visualizer, condtrig,
    synths, legato, copypattern, slicethru, resample, tracklen, songloop),
  - colorsong, humanize and interpolate the other way round (interpolate, humanize,
    colorsong),
  - colorsong, humanize, interpolate and masterfx, both ways round, and with swing and
    visualizer too,
  - synths alone, and after and before all of those,
  - humanize and a copy of it that hooks only its chained sites (two patches on the
    same four sites),
  - itcmtest (ITCM hooks) alone, after and before all the others but synths, and with
    a copy of it on the same two ITCM sites,
  - condtrig alone, after and before all the others but itcmtest and synths, and after
    itcmtest (both on the same two ITCM sites),
  - copypattern alone, after and before humanize (both add a button to the same page),
    and after and before patch/all's patches,
  - legato alone, and patch/all in reverse order (legato first; with condtrig,
    both with ITCM hooks),
  - slicethru alone, and first with patch/all's other patches after it in reverse order
    (it chains the Pattern screen's update slot with most of them; patch/all has it
    last),
  - resample alone, and first with patch/all's other patches after it in reverse order
    (its RAM moves to another region with all of them; an ITCM-image word, as swing's;
    patch/all has it last),
  - songloop alone, after patch/all's patches, and first with them after it in reverse
    order (ITCM hooks on the two preload calls; no site shared with patch/all's patches),
  - tracklen (ITCM hooks, 4 KB of RAM) alone, after patch/all's patches, and first with
    them after it in reverse order; and with page 3's other buttons (humanize,
    copypattern, both) in both orders, and with a copy of it that hooks only its two
    tail calls (b.w sites, chained by both),
  - headroom (a diagnostic) alone, listed last after patch/all's patches, and after
    them and tracklen (its own bundle specs, patch/headroom/all_*.json),
  - a copy of slicethru with a non-BL site ("replaces": "insns", the two instructions
    at 0x60035522), alone and in patch/all's place for slicethru; the site's 4 bytes
    are a BL to the hook and the bytes around it are stock,
and checks that a bundle with ramdiag (standalone) is refused, and so is one where a
shared site isn't chained by both patches, one where two patches pass different
registers at one ITCM site, a "late_ram" word that holds no size, a non-BL site with
"next" or with a "replaces" other than "insns", a second patch on a non-BL site (with
"replaces", or chaining it with "next"), a site overlapping another, a tail call with
"replaces" or with "tail" other than true, "tail" on a BL site, and a b.w site without
"tail".
For each output it reads the image back and its .map.json, and checks:
  - the patches sit in the listed order, each right after the one before (16-byte
    aligned), or at its fixed base; the gaps are 0xFF;
  - each patch's RAM block lies in a RAM region it may use (no SDRAM for patches that
    use RAM at startup; the ITCM tail only when named), 16-byte aligned, and no two
    blocks overlap; a late RAM block ("late_ram") too, in any region of the default
    order, its size the one its word held, and the word now its address;
  - each patch's bytes equal its .bin, except the words its relocation table lists,
    which equal the .bin's value plus the code move or the RAM move, exactly;
  - each chain word ("next") holds what its site pointed to before that patch: the
    stock target or the hook of the patch before it;
  - each link word ("links") holds the other patch's symbol where it landed, or 0 when
    that patch isn't in the bundle (condtrig's link to tracklen, in both orders);
  - every hooked site (BL or word) points at the hook of the last patch that hooks it;
    branch sites are BLs, but tail calls ("tail"), which stay b.w;
  - ITCM hooks: each hooked ITCM BL (in the ITCM image in flash) goes to its stub at
    the start of the ITCM tail, the stub moves the named registers and jumps to the
    last patch's hook, the stubs' bytes follow the last patch (16-byte aligned, 0xFF
    gap), startup copy table entry 3 copies them there (and is stock without ITCM
    hooks), and no RAM block overlaps them;
  - the image doesn't end in the first 256-byte page of a flash sector (the patcher
    pads it: the bootloader leaves such a sector unerased);
  - the version text is the bundle's (or the patch's own when alone).
The emulator tests (tools/emu/test_*.py) then check that the firmware works.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from patch_ptf import (RAM_DEFAULT_ORDER, RAM_REGIONS, bl_target, branch_form, load_rel, load_symbols,  # noqa: E402
                       thumb_bl)
from ptf import read_ptf  # noqa: E402

MT0_SHA = "f45510a17f8b9b120c0e699f239a737a145c3d57bbaf596046fb7a8bc643ffff"
# A non-BL site: the 1-shot render's two instructions at the end of a voice (FINDINGS
# section 27), and the next two, which overlap it by 2 bytes.
INSNS_SITE = {"at": "0x60035522", "old": "e369e367", "to": "mt_bt_bs_note_on", "replaces": "insns",
              "note": "layout test: a non-BL site"}
INSNS_OVERLAP = dict(INSNS_SITE, at="0x60035524", old="e3670232")
STOCK_END = 0x601BAB78
ALIGN = 16
ITCM_SRC = 0x6018D6E4               # flash address of ITCM address 0 in the ITCM image
STUB_ENTRY = 0x600302C4             # startup copy table entry 3: {load, exec, length}
STUB_ENTRY_STOCK = (0x601AD21C, 0x80000000, 0)
ITCM_STUBS = 0x1FB40                # start of the ITCM tail
REGS = {f"r{n}": n for n in range(4, 13)} | {"sp": 13}
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def patch_files(spec_path: Path) -> dict:
    spec = json.loads(spec_path.read_text())
    d = spec_path.parent
    keys = ("bin", "sym", "rel")
    if not (d / spec["bin"]).exists():
        keys = ("prebuilt_bin", "prebuilt_sym", "prebuilt_rel")
    blob = (d / spec[keys[0]]).read_bytes()
    syms = load_symbols(d / spec[keys[1]])
    rel = load_rel(d / spec[keys[2]]) if spec.get(keys[2]) else None
    return {"spec": spec, "blob": blob, "syms": syms, "rel": rel}


def build(stock: Path, spec: Path, out: Path) -> bool:
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(spec), str(out)],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stdout + r.stderr)
    return r.returncode == 0


def verify(label: str, out: Path, specs: list[Path], version: str | None, stock: bytes) -> None:
    print(label)
    ptf = read_ptf(out)
    img, base0 = bytes(ptf.image), ptf.base
    layout = {q["name"]: q for q in json.loads(out.with_suffix(".map.json").read_text())["patches"]}
    end = STOCK_END
    ram_blocks = []
    hooked: dict[int, tuple[str, int, str]] = {}    # site -> (kind, what it points to, patch)
    forms: dict[int, str] = {}                      # branch site -> "bl", or "b.w" for a tail call
    itcm: dict[int, tuple[int, tuple]] = {}         # ITCM site -> (last hook, registers passed)
    placed = {}                                     # patch -> its symbols where it landed
    for sp in specs:
        q = patch_files(sp)
        n = q["spec"]["name"]
        link = q["rel"]["link"] if q["rel"] else int(q["spec"]["base"], 16)
        placed[n] = {k: v + int(layout[n]["code"][0], 16) - link for k, v in q["syms"].items()}
    for sp in specs:
        p = patch_files(sp)
        spec, blob, rel = p["spec"], p["blob"], p["rel"]
        name = spec["name"]
        if "base" in spec:
            base, link, offsets = int(spec["base"], 16), int(spec["base"], 16), []
        else:
            link, offsets = rel["link"], rel["offsets"]
            base = (end + ALIGN - 1) & ~(ALIGN - 1)
        delta = base - link
        ram_delta, ram_offsets = 0, []
        if rel and rel["ram_size"]:
            ram = layout[name]["ram"]
            start, rend, region = int(ram["start"], 16), int(ram["end"], 16), ram["region"]
            lo, hi, flags = RAM_REGIONS[region]
            allowed = region in spec.get("ram_regions", RAM_DEFAULT_ORDER) and \
                (not spec.get("ram_startup") or "startup" in flags)
            check(allowed and lo <= start and rend <= hi and start % ALIGN == 0
                  and rend - start == rel["ram_size"],
                  f"{name}: {rel['ram_size']} B of RAM at {start:#x} in {region}, a region it may use")
            ram_blocks.append((start, rend, name))
            ram_delta, ram_offsets = start - rel["ram_link"], rel["ram_offsets"]
        check(all(b == 0xFF for b in img[end - base0:base - base0]), f"{name}: gap before it is 0xFF")
        got = img[base - base0:base - base0 + len(blob)]
        want = bytearray(blob)
        for offs, d in ((offsets, delta), (ram_offsets, ram_delta)):
            for off in offs:
                v = struct.unpack_from("<I", blob, off)[0]
                struct.pack_into("<I", want, off, (v + d) & 0xFFFFFFFF)
        syms = {k: v + delta for k, v in p["syms"].items()}
        if "late_ram" in spec:                      # a second block, for state used after boot
            late = layout[name].get("late_ram")
            woff = syms[spec["late_ram"]] - base
            size = struct.unpack_from("<I", blob, woff)[0]
            start, rend, region = (int(late["start"], 16), int(late["end"], 16), late["region"]) if late else (0, 0, "")
            lo, hi, _ = RAM_REGIONS.get(region, (0, 0, set()))
            check(late is not None and region in RAM_DEFAULT_ORDER and lo <= start and rend <= hi
                  and start % ALIGN == 0 and rend - start == size,
                  f"{name}: {size} B of late RAM at {start:#x} in {region}, its word's size")
            ram_blocks.append((start, rend, name + " (late)"))
            struct.pack_into("<I", want, woff, start)
        for ln in spec.get("links", []):            # another patch's address, 0 without it
            other = placed.get(ln["patch"])
            value = other[ln["symbol"]] | (1 if ln.get("thumb") else 0) if other else 0
            struct.pack_into("<I", want, syms[ln["word"]] - base, value)
        chained = 0
        for kind, hook in [("bl", br) for br in spec["branches"]] + \
                [("word", w) for w in spec.get("words", []) if not (version and w.get("version"))]:
            at = int(hook["at"], 16)
            if kind == "bl":
                forms[at] = "b.w" if hook.get("tail") else "bl"
            if at in hooked:
                before = hooked[at][1]
            elif kind == "bl":
                before = bl_target(stock, base0, at)
            else:
                before = struct.unpack_from("<I", stock, at - base0)[0]
            if "next" in hook:
                struct.pack_into("<I", want, syms[hook["next"]] - base, before | 1 if kind == "bl" else before)
                chained += 1
            target = syms[hook["to"]] | (1 if kind == "word" and hook.get("thumb") else 0)
            hooked[at] = (kind, target, name)
        for hook in spec.get("itcm_branches", []):
            at = int(hook["at"], 16)
            flash = ITCM_SRC + at
            before = itcm[at][0] if at in itcm else bl_target(stock, base0, flash) - flash + at
            if "next" in hook:
                struct.pack_into("<I", want, syms[hook["next"]] - base, before | 1)
                chained += 1
            itcm[at] = (syms[hook["to"]], tuple(hook.get("args", [])))
        check(got == bytes(want), f"{name}: code at {base:#x} (moved {delta:+#x}), {len(offsets)} code words "
              f"and {len(ram_offsets)} RAM words (moved {ram_delta:+#x}) relocated, {chained} chain words "
              f"filled" + "".join(f", link {ln['word']} -> {ln['patch'] if ln['patch'] in placed else 0}"
                                   for ln in spec.get("links", [])) + ", all other bytes as built")
        end = base + len(blob)
    sites_ok = all((bl_target(img, base0, at) if kind == "bl" else struct.unpack_from("<I", img, at - base0)[0])
                   == target for at, (kind, target, _) in hooked.items())
    shared = sum(1 for at in hooked if sum(1 for sp in specs for h in
                                           patch_files(sp)["spec"]["branches"] + patch_files(sp)["spec"].get("words", [])
                                           if int(h["at"], 16) == at) > 1)
    check(sites_ok, f"all {len(hooked)} hooked sites point at the last patch's hook ({shared} shared)")
    tails = sum(1 for f in forms.values() if f == "b.w")
    check(all(branch_form(img[at - base0:at - base0 + 4]) == f for at, f in forms.items()),
          f"the {len(forms)} branch sites keep their form: BL, or b.w for the {tails} tail call(s)")
    ram_blocks.sort()
    check(all(a[1] <= b[0] for a, b in zip(ram_blocks, ram_blocks[1:])), "no two patches' RAM overlaps")
    entry = struct.unpack_from("<3I", img, STUB_ENTRY - base0)
    if itcm:
        stubs = bytearray()
        sites_ok = True
        for at, (hook, args) in itcm.items():         # in the order first hooked
            stub = ITCM_STUBS + len(stubs)
            jump = struct.pack("<HHI", 0xF8DF, 0xF000, hook | 1)           # ldr.w pc, [pc, #0]; .word
            moves = [0x4600 | (REGS[r] << 3) | (2 + i) for i, r in enumerate(args)]  # mov r2/r3, reg
            stubs += (struct.pack("<HH", *(moves + [0xBF00] * (2 - len(moves)))) if args else b"") + jump
            flash = ITCM_SRC + at
            sites_ok &= bl_target(img, base0, flash) - flash + at == stub
        flash_at = (end + ALIGN - 1) & ~(ALIGN - 1)
        check(sites_ok, f"all {len(itcm)} hooked ITCM sites BL to their stubs at {ITCM_STUBS:#x}")
        check(img[flash_at - base0:flash_at - base0 + len(stubs)] == bytes(stubs)
              and all(b == 0xFF for b in img[end - base0:flash_at - base0]),
              f"the stubs ({len(stubs)} bytes) follow the last patch at {flash_at:#x}, moving the named "
              f"registers and jumping to the last hook")
        check(entry == (flash_at, ITCM_STUBS, len(stubs)),
              f"copy table entry 3 copies them to ITCM {ITCM_STUBS:#x} at boot")
        check(all(rend <= ITCM_STUBS or start >= ITCM_STUBS + len(stubs) for start, rend, _ in ram_blocks),
              "no patch RAM overlaps the stubs")
        end = flash_at + len(stubs)
    else:
        check(entry == STUB_ENTRY_STOCK, "no ITCM hooks: copy table entry 3 is stock")
    img_end = base0 + len(img)
    check(not 0 < img_end % 0x1000 <= 0x100,
          f"the image ends at {img_end:#x}, not in the first 256-byte page of a flash sector "
          f"(the bootloader leaves such a sector unerased)")
    vptr =struct.unpack_from("<I", img, 0x600CBF9C - base0)[0]
    text = img[vptr - base0:].split(b"\0")[0].decode()
    if version:
        check(text == "%d.%d.%d-" + version and vptr >= end, f"version text {text!r} after the last patch")
    else:
        check(text.startswith("%d.%d.%d-"), f"version text {text!r} (the patch's own)")


def main() -> int:
    stock = Path(sys.argv[1])
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp())
    outdir.mkdir(parents=True, exist_ok=True)
    cs, hz, vt, rd, ip, mx, sw, vz = (ROOT / "patch" / n / "patch.json"
                                      for n in ("colorsong", "humanize", "versiontest", "ramdiag", "interpolate",
                                                "masterfx", "swing", "visualizer"))
    all_spec = ROOT / "patch" / "all" / "patch.json"
    rev = json.loads(all_spec.read_text())
    rev.update(name="all-reversed", include=[str(ip), str(hz), str(cs)], version="MTR")
    rev_spec = outdir / "reversed.json"
    rev_spec.write_text(json.dumps(rev, indent=2))
    with_mx = dict(rev, name="with-masterfx", include=[str(cs), str(hz), str(ip), str(mx)], version="MTX")
    with_mx_spec = outdir / "with-masterfx.json"
    with_mx_spec.write_text(json.dumps(with_mx, indent=2))
    with_mx_rev = dict(rev, name="with-masterfx-reversed", include=[str(mx), str(ip), str(hz), str(cs)], version="MTY")
    with_mx_rev_spec = outdir / "with-masterfx-reversed.json"
    with_mx_rev_spec.write_text(json.dumps(with_mx_rev, indent=2))
    every = dict(rev, name="every", include=[str(cs), str(hz), str(ip), str(mx), str(sw), str(vz)], version="MTS")
    every_spec = outdir / "every.json"
    every_spec.write_text(json.dumps(every, indent=2))
    every_rev = dict(rev, name="every-reversed", include=[str(vz), str(sw), str(mx), str(ip), str(hz), str(cs)], version="MTT")
    every_rev_spec = outdir / "every-reversed.json"
    every_rev_spec.write_text(json.dumps(every_rev, indent=2))
    sy = ROOT / "patch" / "synths" / "patch.json"
    with_sy = dict(rev, name="every-synths", include=[str(cs), str(hz), str(ip), str(mx), str(sw), str(vz), str(sy)],
                   version="MTU")
    with_sy_spec = outdir / "every-synths.json"
    with_sy_spec.write_text(json.dumps(with_sy, indent=2))
    with_sy_rev = dict(rev, name="every-synths-reversed",
                       include=[str(sy), str(vz), str(sw), str(mx), str(ip), str(hz), str(cs)], version="MTV")
    with_sy_rev_spec = outdir / "every-synths-reversed.json"
    with_sy_rev_spec.write_text(json.dumps(with_sy_rev, indent=2))
    hz_spec = json.loads(hz.read_text())
    copy = dict(hz_spec, name="humanize-copy",
                branches=[b for b in hz_spec["branches"] if "next" in b],
                words=[w for w in hz_spec["words"] if "next" in w])
    for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel"):
        copy[k] = str((hz.parent / hz_spec[k]).resolve())
    copy_spec = outdir / "humanize-copy.json"
    copy_spec.write_text(json.dumps(copy, indent=2))
    chain = dict(rev, name="chain", include=[str(hz), str(copy_spec.resolve())], version="CHN")
    chain_spec = outdir / "chain.json"
    chain_spec.write_text(json.dumps(chain, indent=2))
    it = ROOT / "patch" / "itcmtest" / "patch.json"
    with_it = dict(rev, name="every-itcm", include=[str(cs), str(hz), str(ip), str(mx), str(sw), str(vz), str(it)],
                   version="MTI")
    with_it_spec = outdir / "every-itcm.json"
    with_it_spec.write_text(json.dumps(with_it, indent=2))
    with_it_rev = dict(rev, name="every-itcm-reversed",
                       include=[str(it), str(vz), str(sw), str(mx), str(ip), str(hz), str(cs)], version="MTJ")
    with_it_rev_spec = outdir / "every-itcm-reversed.json"
    with_it_rev_spec.write_text(json.dumps(with_it_rev, indent=2))
    it_spec = json.loads(it.read_text())
    it_copy = dict(it_spec, name="itcmtest-copy", words=[])
    for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel"):
        it_copy[k] = str((it.parent / it_spec[k]).resolve())
    it_copy_spec = outdir / "itcmtest-copy.json"
    it_copy_spec.write_text(json.dumps(it_copy, indent=2))
    it_chain = dict(rev, name="itcm-chain", include=[str(it), str(it_copy_spec.resolve())], version="CHI")
    it_chain_spec = outdir / "itcm-chain.json"
    it_chain_spec.write_text(json.dumps(it_chain, indent=2))
    ct = ROOT / "patch" / "condtrig" / "patch.json"
    with_ct = dict(rev, name="every-condtrig", include=[str(cs), str(hz), str(ip), str(mx), str(sw), str(vz), str(ct)],
                   version="MTK")
    with_ct_spec = outdir / "every-condtrig.json"
    with_ct_spec.write_text(json.dumps(with_ct, indent=2))
    with_ct_rev = dict(rev, name="every-condtrig-reversed",
                       include=[str(ct), str(vz), str(sw), str(mx), str(ip), str(hz), str(cs)], version="MTL")
    with_ct_rev_spec = outdir / "every-condtrig-reversed.json"
    with_ct_rev_spec.write_text(json.dumps(with_ct_rev, indent=2))
    it_ct = dict(rev, name="itcm-condtrig", include=[str(it), str(ct)], version="ICT")
    it_ct_spec = outdir / "itcm-condtrig.json"
    it_ct_spec.write_text(json.dumps(it_ct, indent=2))
    lg = ROOT / "patch" / "legato" / "patch.json"
    cp = ROOT / "patch" / "copypattern" / "patch.json"
    hz_cp = dict(rev, name="humanize-copypattern", include=[str(hz), str(cp)], version="HCP")
    hz_cp_spec = outdir / "humanize-copypattern.json"
    hz_cp_spec.write_text(json.dumps(hz_cp, indent=2))
    cp_hz = dict(rev, name="copypattern-humanize", include=[str(cp), str(hz)], version="CPH")
    cp_hz_spec = outdir / "copypattern-humanize.json"
    cp_hz_spec.write_text(json.dumps(cp_hz, indent=2))
    all_cp_list = [cs, hz, ip, mx, sw, vz, ct, sy, lg, cp]
    all_cp = dict(rev, name="all-copypattern", include=[str(x) for x in all_cp_list], version="CPA")
    all_cp_spec = outdir / "all-copypattern.json"
    all_cp_spec.write_text(json.dumps(all_cp, indent=2))
    all_cp_rev = dict(rev, name="all-copypattern-reversed", include=[str(x) for x in all_cp_list[::-1]],
                      version="CPR")
    all_cp_rev_spec = outdir / "all-copypattern-reversed.json"
    all_cp_rev_spec.write_text(json.dumps(all_cp_rev, indent=2))
    all_lg_list = [cs, hz, ip, mx, sw, vz, ct, sy, lg]
    all_lg_rev = dict(rev, name="all-legato-reversed", include=[str(x) for x in all_lg_list[::-1]], version="LGR")
    all_lg_rev_spec = outdir / "all-legato-reversed.json"
    all_lg_rev_spec.write_text(json.dumps(all_lg_rev, indent=2))
    bt = ROOT / "patch" / "slicethru" / "patch.json"
    all_bt_list = all_cp_list + [bt]
    all_bt_rev = dict(rev, name="all-slicethru-reversed", include=[str(x) for x in all_bt_list[::-1]],
                      version="BTR")
    all_bt_rev_spec = outdir / "all-slicethru-reversed.json"
    all_bt_rev_spec.write_text(json.dumps(all_bt_rev, indent=2))
    rs = ROOT / "patch" / "resample" / "patch.json"
    all_rs_list = all_bt_list + [rs]
    all_rs_rev = dict(rev, name="all-resample-reversed", include=[str(x) for x in all_rs_list[::-1]],
                      version="RSR")
    all_rs_rev_spec = outdir / "all-resample-reversed.json"
    all_rs_rev_spec.write_text(json.dumps(all_rs_rev, indent=2))
    pm = ROOT / "patch" / "tracklen" / "patch.json"
    sl = ROOT / "patch" / "songloop" / "patch.json"
    all_sl_list = all_rs_list + [pm, sl]
    all_sl = dict(rev, name="all-songloop", include=[str(x) for x in all_sl_list], version="SLA")
    all_sl_spec = outdir / "all-songloop.json"
    all_sl_spec.write_text(json.dumps(all_sl, indent=2))
    all_sl_rev = dict(rev, name="all-songloop-reversed", include=[str(x) for x in all_sl_list[::-1]],
                      version="SLR")
    all_sl_rev_spec = outdir / "all-songloop-reversed.json"
    all_sl_rev_spec.write_text(json.dumps(all_sl_rev, indent=2))
    all_pm_list = all_rs_list + [pm]
    all_pm = dict(rev, name="all-tracklen", include=[str(x) for x in all_pm_list], version="PMA")
    all_pm_spec = outdir / "all-tracklen.json"
    all_pm_spec.write_text(json.dumps(all_pm, indent=2))
    all_pm_rev = dict(rev, name="all-tracklen-reversed", include=[str(x) for x in all_pm_list[::-1]],
                      version="PMR")
    all_pm_rev_spec = outdir / "all-tracklen-reversed.json"
    all_pm_rev_spec.write_text(json.dumps(all_pm_rev, indent=2))
    hr = ROOT / "patch" / "headroom" / "patch.json"
    hr_all, hr_all_pm = (ROOT / "patch" / "headroom" / f"{n}.json" for n in ("all_headroom", "all_tracklen_headroom"))
    pm_page3 = {}                               # tracklen with page 3's other buttons
    for name, version, specs in (("humanize-tracklen", "HPM", [hz, pm]), ("tracklen-humanize", "PMH", [pm, hz]),
                                 ("copypattern-tracklen", "CPM", [cp, pm]),
                                 ("tracklen-copypattern", "PCM", [pm, cp]),
                                 ("humanize-copypattern-tracklen", "HCM", [hz, cp, pm]),
                                 ("tracklen-copypattern-humanize", "MCH", [pm, cp, hz])):
        spec_path = outdir / f"{name}.json"
        spec_path.write_text(json.dumps(dict(rev, name=name, include=[str(x) for x in specs], version=version),
                                        indent=2))
        pm_page3[version] = (name, spec_path, specs)
    pm_spec = json.loads(pm.read_text())
    pm_files = {k: str((pm.parent / pm_spec[k]).resolve())
                for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel")}
    pm_tails = [br for br in pm_spec["branches"] if br.get("tail")]
    pm_bl = next(br for br in pm_spec["branches"] if "next" in br and not br.get("tail"))

    def pm_copy(name: str, branches: list[dict]) -> Path:
        """tracklen hooking only `branches`."""
        path = outdir / f"{name}.json"
        path.write_text(json.dumps(dict(pm_spec, name=name, branches=branches, words=[], itcm_branches=[],
                                        **pm_files), indent=2))
        return path.resolve()

    pm_tail_chain = outdir / "tracklen-tail-chain.json"
    pm_tail_chain.write_text(json.dumps(dict(rev, name="tracklen-tail-chain",
                                             include=[str(pm), str(pm_copy("tracklen-tails", pm_tails))],
                                             version="PTC"), indent=2))
    bt_spec = json.loads(bt.read_text())
    bt_files = {k: str((bt.parent / bt_spec[k]).resolve())
                for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel")}

    def bt_copy(name: str, branches: list[dict], own: bool = True) -> Path:
        """slicethru with `branches` added (own: its own hooks too, less any at the same
        address; else only those)."""
        ats = {int(br["at"], 16) for br in branches}
        kept = [br for br in bt_spec["branches"] if int(br["at"], 16) not in ats] if own else []
        spec = dict(bt_spec, name=name, branches=kept + branches,
                    words=bt_spec["words"] if own else [], **bt_files)
        path = outdir / f"{name}.json"
        path.write_text(json.dumps(spec, indent=2))
        return path.resolve()

    bt_ins = bt_copy("slicethru-insns", [INSNS_SITE])
    all_ins_list = [bt_ins if x == bt else x for x in all_rs_list]
    all_ins = dict(rev, name="all-insns", include=[str(x) for x in all_ins_list], version="INS")
    all_ins_spec = outdir / "all-insns.json"
    all_ins_spec.write_text(json.dumps(all_ins, indent=2))
    stock_img = bytes(read_ptf(stock).image)

    for label, spec, specs, version, sha in (
            ("colorsong alone", cs, [cs], None, None),
            ("versiontest alone (fixed base)", vt, [vt], None, MT0_SHA),
            ("humanize alone", hz, [hz], None, None),
            ("interpolate alone", ip, [ip], None, None),
            ("masterfx alone", mx, [mx], None, None),
            ("swing alone", sw, [sw], None, None),
            ("visualizer alone", vz, [vz], None, None),
            ("synths alone", sy, [sy], None, None),
            ("ramdiag alone", rd, [rd], None, None),
            ("patch/all: colorsong, humanize, interpolate, masterfx, swing, visualizer, condtrig, synths, legato, "
             "copypattern, slicethru, resample, tracklen, songloop", all_spec,
             [cs, hz, ip, mx, sw, vz, ct, sy, lg, cp, bt, rs, pm, sl],
             json.loads(all_spec.read_text())["version"], None),
            ("reversed: interpolate, humanize, colorsong", rev_spec, [ip, hz, cs], "MTR", None),
            ("colorsong, humanize, interpolate, masterfx", with_mx_spec, [cs, hz, ip, mx], "MTX", None),
            ("masterfx, interpolate, humanize, colorsong", with_mx_rev_spec, [mx, ip, hz, cs], "MTY", None),
            ("colorsong, humanize, interpolate, masterfx, swing, visualizer", every_spec,
             [cs, hz, ip, mx, sw, vz], "MTS", None),
            ("visualizer, swing, masterfx, interpolate, humanize, colorsong", every_rev_spec,
             [vz, sw, mx, ip, hz, cs], "MTT", None),
            ("colorsong, humanize, interpolate, masterfx, swing, visualizer, synths", with_sy_spec,
             [cs, hz, ip, mx, sw, vz, sy], "MTU", None),
            ("synths, visualizer, swing, masterfx, interpolate, humanize, colorsong", with_sy_rev_spec,
             [sy, vz, sw, mx, ip, hz, cs], "MTV", None),
            ("chained: humanize, then a copy on its four shared sites", chain_spec, [hz, copy_spec], "CHN", None),
            ("itcmtest alone (ITCM hooks)", it, [it], None, None),
            ("colorsong, humanize, interpolate, masterfx, swing, visualizer, itcmtest", with_it_spec,
             [cs, hz, ip, mx, sw, vz, it], "MTI", None),
            ("itcmtest, visualizer, swing, masterfx, interpolate, humanize, colorsong", with_it_rev_spec,
             [it, vz, sw, mx, ip, hz, cs], "MTJ", None),
            ("chained: itcmtest, then a copy on its two ITCM sites", it_chain_spec, [it, it_copy_spec], "CHI",
             None),
            ("condtrig alone", ct, [ct], None, None),
            ("colorsong, humanize, interpolate, masterfx, swing, visualizer, condtrig", with_ct_spec,
             [cs, hz, ip, mx, sw, vz, ct], "MTK", None),
            ("condtrig, visualizer, swing, masterfx, interpolate, humanize, colorsong", with_ct_rev_spec,
             [ct, vz, sw, mx, ip, hz, cs], "MTL", None),
            ("chained on ITCM sites: itcmtest, then condtrig", it_ct_spec, [it, ct], "ICT", None),
            ("copypattern alone", cp, [cp], None, None),
            ("humanize, copypattern", hz_cp_spec, [hz, cp], "HCP", None),
            ("copypattern, humanize", cp_hz_spec, [cp, hz], "CPH", None),
            ("patch/all's patches, then copypattern", all_cp_spec, all_cp_list, "CPA", None),
            ("copypattern, then patch/all's patches reversed", all_cp_rev_spec, all_cp_list[::-1], "CPR", None),
            ("legato alone", lg, [lg], None, None),
            ("patch/all reversed (legato first)", all_lg_rev_spec, all_lg_list[::-1], "LGR", None),
            ("slicethru alone", bt, [bt], None, None),
            ("slicethru, then patch/all's patches reversed", all_bt_rev_spec, all_bt_list[::-1], "BTR", None),
            ("resample alone", rs, [rs], None, None),
            ("resample, then patch/all's patches reversed", all_rs_rev_spec, all_rs_list[::-1], "RSR", None),
            ("tracklen alone (ITCM hooks)", pm, [pm], None, None),
            ("patch/all's patches, then tracklen", all_pm_spec, all_pm_list, "PMA", None),
            ("tracklen, then patch/all's patches reversed", all_pm_rev_spec, all_pm_list[::-1], "PMR", None),
            ("songloop alone (ITCM hooks)", sl, [sl], None, None),
            ("patch/all's patches, then songloop", all_sl_spec, all_sl_list, "SLA", None),
            ("songloop, then patch/all's patches reversed", all_sl_rev_spec, all_sl_list[::-1], "SLR", None),
            *[(f"page 3's buttons: {name}", spec_path, specs, version, None)
              for version, (name, spec_path, specs) in pm_page3.items()],
            ("chained tail calls: tracklen, then a copy on its two b.w sites", pm_tail_chain,
             [pm, outdir / "tracklen-tails.json"], "PTC", None),
            ("headroom alone", hr, [hr], None, None),
            ("patch/all's patches, then headroom", hr_all, all_rs_list + [hr], "MH1", None),
            ("patch/all's patches, tracklen, then headroom", hr_all_pm, all_pm_list + [hr], "MH2", None),
            ("a non-BL site: slicethru with the two instructions at 0x60035522", bt_ins, [bt_ins], None, None),
            ("a non-BL site in patch/all: the slicethru copy in slicethru's place", all_ins_spec, all_ins_list,
             "INS", None)):
        out = outdir / f"{spec.stem if spec.parent == outdir or spec.stem != "patch" else spec.parent.name}.ptf"
        if not build(stock, spec, out):
            check(False, f"{label}: patch_ptf failed")
            continue
        verify(label, out, specs, version, stock_img)
        if sha:
            got = hashlib.sha256(out.read_bytes()).hexdigest()
            check(got == sha, f"{label}: byte-identical to the hardware-tested build")
        if bt_ins in specs:
            ptf = read_ptf(out)
            at = int(INSNS_SITE["at"], 16)
            placed = next(q for q in json.loads(out.with_suffix(".map.json").read_text())["patches"]
                          if q["name"] == "slicethru-insns")
            p = patch_files(bt_ins)
            hook = int(placed["code"][0], 16) + p["syms"][INSNS_SITE["to"]] - p["rel"]["link"]
            lo, hi = at - 0x20 - ptf.base, at + 0x20 - ptf.base
            want = bytearray(stock_img[lo:hi])
            want[at - ptf.base - lo:at - ptf.base - lo + 4] = thumb_bl(at, hook)
            check(bytes(ptf.image[lo:hi]) == bytes(want),
                  f"{at:#x}: its 4 bytes are a BL to {INSNS_SITE['to']}, the 32 bytes either side are stock")
    print("a bundle with a standalone patch")
    bad = dict(rev, name="with-ramdiag", include=[str(cs), str(rd)], version="BAD")
    bad_spec = outdir / "with-ramdiag.json"
    bad_spec.write_text(json.dumps(bad, indent=2))
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(bad_spec),
                        str(outdir / "with-ramdiag.ptf")], capture_output=True, text=True)
    check(r.returncode != 0 and "standalone" in (r.stdout + r.stderr), "refused: ramdiag is standalone")
    print("a shared site not chained by both patches")
    unchained = dict(copy, name="humanize-unchained",
                     words=[{k: v for k, v in w.items() if k != "next"} for w in copy["words"]])
    un_spec = outdir / "humanize-unchained.json"
    un_spec.write_text(json.dumps(unchained, indent=2))
    bad = dict(rev, name="unchained", include=[str(hz), str(un_spec.resolve())], version="BAD")
    bad_spec = outdir / "unchained.json"
    bad_spec.write_text(json.dumps(bad, indent=2))
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(bad_spec),
                        str(outdir / "unchained.ptf")], capture_output=True, text=True)
    check(r.returncode != 0 and "not chained by both" in (r.stdout + r.stderr), "refused: a shared site isn't chained")
    for what, change, refusal in (
            ("an ITCM site not chained by both patches", lambda b: {k: v for k, v in b.items() if k != "next"},
             "not chained by both"),
            ("two patches passing different registers at one ITCM site", lambda b: dict(b, args=["r4"]),
             "different \"args\"")):
        print(what)
        bad_it = dict(it_copy, name="itcmtest-bad", itcm_branches=[change(b) for b in it_copy["itcm_branches"]])
        bad_it_spec = outdir / "itcmtest-bad.json"
        bad_it_spec.write_text(json.dumps(bad_it, indent=2))
        bad = dict(rev, name="itcm-bad", include=[str(it), str(bad_it_spec.resolve())], version="BAD")
        bad_spec = outdir / "itcm-bad.json"
        bad_spec.write_text(json.dumps(bad, indent=2))
        r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(bad_spec),
                            str(outdir / "itcm-bad.ptf")], capture_output=True, text=True)
        check(r.returncode != 0 and refusal in (r.stdout + r.stderr), f"refused: {what}")
    print("a late RAM word that holds no size")
    sy_spec = json.loads(sy.read_text())
    no_size = dict(sy_spec, name="synths-no-size", late_ram="mt_syn_next_post")    # a chain word: 0
    for k in ("bin", "sym", "rel", "prebuilt_bin", "prebuilt_sym", "prebuilt_rel"):
        no_size[k] = str((sy.parent / sy_spec[k]).resolve())
    bad_spec = outdir / "synths-no-size.json"
    bad_spec.write_text(json.dumps(no_size, indent=2))
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(bad_spec),
                        str(outdir / "synths-no-size.ptf")], capture_output=True, text=True)
    check(r.returncode != 0 and "not a late RAM size" in (r.stdout + r.stderr), "refused: a late RAM word holding 0")
    no_replaces = {k: v for k, v in INSNS_SITE.items() if k != "replaces"}
    for what, specs, refusal in (
            ("a non-BL site with \"next\"", [bt_copy("insns-next", [dict(INSNS_SITE, next="mt_bt_bs_note_on")])],
             "nothing to chain"),
            ("a \"replaces\" other than \"insns\"", [bt_copy("insns-bad", [dict(INSNS_SITE, replaces="bl")])],
             "\"replaces\" takes \"insns\""),
            ("a second patch on a non-BL site", [bt_ins, bt_copy("insns-second", [INSNS_SITE], own=False)],
             "not chained by both"),
            ("a second patch chaining a non-BL site (\"next\")",
             [bt_ins, bt_copy("insns-chained", [dict(no_replaces, next="mt_bt_bs_note_on")], own=False)],
             "not chained by both"),
            ("a site overlapping a non-BL site by 2 bytes", [bt_ins, bt_copy("insns-overlap", [INSNS_OVERLAP], own=False)],
             "overlaps 0x60035522")):
        print(what)
        bad_spec = specs[0]
        if len(specs) > 1:
            bad_spec = outdir / "insns-bundle.json"
            bad_spec.write_text(json.dumps(dict(rev, name="insns-bundle", include=[str(x) for x in specs], version="BAD"),
                                           indent=2))
        r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock), str(bad_spec),
                            str(outdir / "insns-bad.ptf")], capture_output=True, text=True)
        check(r.returncode != 0 and refusal in (r.stdout + r.stderr), f"refused: {what}")
    tail = pm_tails[0]
    for what, branch, refusal in (
            ("a tail call that replaces instructions",
             dict({k: v for k, v in tail.items() if k != "next"}, replaces="insns"), "can't be a tail call"),
            ("\"tail\" other than true", dict(tail, tail="yes"), "\"tail\" takes true"),
            ("\"tail\" on a BL site", dict(pm_bl, tail=True), "is not a b.w"),
            ("a b.w site without \"tail\"", {k: v for k, v in tail.items() if k != "tail"},
             "a b.w site takes \"tail\": true")):
        print(what)
        r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "patch_ptf.py"), str(stock),
                            str(pm_copy("tracklen-bad", [branch])), str(outdir / "tracklen-bad.ptf")],
                           capture_output=True, text=True)
        check(r.returncode != 0 and refusal in (r.stdout + r.stderr), f"refused: {what}")
    print(f"outputs in {outdir}")
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
