"""Check the itcmtest patch (no-op ITCM hooks) and the patcher's ITCM stubs.

  python tools/emu/test_itcmtest.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the itcmtest-only build and with bundles that include it. The emulator runs
the startup copy table from the image, as the reset handler does, so the stubs land in
ITCM the way they would on the device.

1. Boot: the stubs are in ITCM at the addresses the .map.json gives; the hooked ITCM
   BLs go to them; each stub moves the named registers into r2/r3 and jumps to its hook;
   every other byte of ITCM is as in stock firmware, apart from words that other
   patches in a bundle point at their own code (such as swing's at 0x1f81c).
2. Chance FX: a pattern with Chance 0, 25, 50, 75 and 100 (values above 100 are
   condtrig's conditions in a bundle with it) in FX1 and FX2 on four
   MIDI tracks, played for 40 passes, sends the same notes at the same ticks as stock
   firmware (the random sequence is untouched); the hook ran once per Chance FX
   evaluation, and gets the FX column's record (seq + 0x68 * track, minus 2 for FX1)
   and the track.
3. Count-in: a count-in ending in playback starts at the same tick and plays the same
   notes as stock firmware, through the hook (called once).
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unicorn import UC_HOOK_CODE  # noqa: E402

from layout import patch_ram  # noqa: E402
from patch_ptf import bl_target  # noqa: E402
import seq_timing as st  # noqa: E402

SEQ = st.SEQ
CHANCE = 4                          # C
CHANCE_SITE, PLAY_FROM_SITE = 0x30CE, 0x3752
RAND_RANGE, PLAY_FROM = 0x3AF4, 0x1250
COUNT_IN_ON, COUNT_IN_STEP = 0x6F8, 0x6F4
ENGINE_CLICK = 0x42E4               # metronome click in the count-in (no audio engine here)
STEP_TICKS = 576
STOCK_END = 0x601BAB78              # patch code starts after the stock image
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def machine(ptf: Path):
    m = st.make(ptf)
    m.stub_function(ENGINE_CLICK, lambda mm: 0)
    m.w8(st.SETTINGS + 0x2C, 2)                 # MIDI out: jack
    return m


def ticks(m, n: int) -> None:
    for _ in range(n):
        m.ticks += 1
        m.call(st.SEQ_TICK, SEQ, max_insns=2_000_000)


def boot_checks(ptf: Path, stock: Path) -> None:
    print("Boot: the stubs in ITCM")
    stubs = json.loads(ptf.with_suffix(".map.json").read_text())["itcm_stubs"]
    m, s = machine(ptf), machine(stock)
    itcm = bytes(m.uc.mem_read(0, 0x20000))
    stock_itcm = bytes(s.uc.mem_read(0, 0x20000))
    base, size = int(stubs["itcm"], 16), stubs["size"]
    flash = int(stubs["flash"], 16)
    check(itcm[base:base + size] == bytes(m.uc.mem_read(flash, size)) and size > 0,
          f"{size} bytes of stubs copied from flash {flash:#x} to ITCM {base:#x} at boot")
    sites = {int(x["at"], 16): x for x in stubs["sites"]}
    for at, want_args in ((CHANCE_SITE, (7, 8)), (PLAY_FROM_SITE, ())):
        x = sites.get(at)
        if x is None:
            check(False, f"ITCM {at:#x} is hooked")
            continue
        stub, hook = int(x["stub"], 16), int(x["hook"], 16)
        check(bl_target(itcm, 0, at) == stub, f"ITCM {at:#x}: bl -> stub {stub:#x}")
        code = itcm[stub:stub + (12 if want_args else 8)]
        moves = [0x4600 | (r << 3) | (2 + i) for i, r in enumerate(want_args)]
        want = (struct.pack("<HH", *moves) if want_args else b"") + struct.pack("<HHI", 0xF8DF, 0xF000, hook | 1)
        regs = ", ".join(f"r{2 + i} = r{r}" for i, r in enumerate(want_args)) or "no moves"
        check(code == want, f"stub {stub:#x}: {regs}, ldr pc -> hook {hook:#x}")
    def other_patch_word(a: int) -> bool:          # a bundled patch's hook word, e.g. at 0x1f81c
        v = struct.unpack_from("<I", itcm, a & ~3)[0] & ~1
        return STOCK_END <= v < flash
    changed = [a for a in range(0x20000) if itcm[a] != stock_itcm[a]
               and not (base <= a < base + size) and not any(at <= a < at + 4 for at in sites)
               and not other_patch_word(a)]
    check(not changed, "every other byte of ITCM as in stock firmware (apart from other patches' hook words)"
          + (f" (first difference at {changed[0]:#x})" if changed else ""))


def chance_pattern(m):
    cur, nxt = st.pattern(m, 3), st.pattern(m, 3)
    values = [0, 25, 50, 75, 100]
    for buf in (cur, nxt):
        for t in range(4):
            for s in range(4):
                v1, v2 = values[(t + s) % 5], values[(t + 2 * s + 3) % 5]
                st.set_step(m, buf, t, s, 48 + 2 * s, 48 + t, (CHANCE, v1), (CHANCE, v2) if s % 2 else (0, 0))
    m.w32(SEQ + 0x14, cur)
    m.w32(SEQ + 0x18, nxt)
    return cur


def chance_checks(ptf: Path, stock: Path) -> None:
    print("Chance FX: as stock firmware, through the hook")
    runs = {}
    for name, path in (("patched", ptf), ("stock", stock)):
        m = machine(path)
        chance_pattern(m)
        m.call(st.SEQ_PLAY_FROM, SEQ, 0)
        m.events.clear()
        evaluations = [0]
        if name == "stock":                     # count stock's own Chance evaluations
            m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: evaluations.__setitem__(0, evaluations[0] + 1),
                          begin=CHANCE_SITE, end=CHANCE_SITE)
        ticks(m, STEP_TICKS * 4 * 40 + 12)
        runs[name] = (m, list(m.events), evaluations[0])
    m, events, _ = runs["patched"]
    _, stock_events, evaluations = runs["stock"]
    notes = sum(1 for _, what in events if "note on" in what)
    check(events == stock_events and notes > 0,
          f"the same {notes} notes at the same ticks as stock firmware (Chance 0..100, 40 passes)")
    it = patch_ram(ptf, "itcmtest")
    calls = struct.unpack("<I", m.uc.mem_read(it + 4, 4))[0]
    check(calls == evaluations and calls > 0, f"the hook ran {calls} times, once per Chance FX evaluation "
                                               f"({evaluations} in stock firmware)")

    for track, column in ((0, 0), (2, 1), (15, 0), (9, 1)):
        m = machine(ptf)
        cur, nxt = st.pattern(m, 0), st.pattern(m, 0)
        for buf in (cur, nxt):
            fx = (CHANCE, 50)
            st.set_step(m, buf, track, 0, 60, 48, fx if column == 0 else (0, 0), fx if column == 1 else (0, 0))
        m.w32(SEQ + 0x14, cur)
        m.w32(SEQ + 0x18, nxt)
        m.call(st.SEQ_PLAY_FROM, SEQ, 0)
        ticks(m, 24)
        calls, col, trk = struct.unpack("<III", m.uc.mem_read(it + 4, 4) + m.uc.mem_read(it + 12, 8))
        want = SEQ + 0x68 * track - (2 if column == 0 else 0)
        check(calls == 1 and col == want and trk == track,
              f"Chance in FX{column + 1} on track {track + 1}: the hook gets column {want:#x} and track {track}")


def count_in_checks(ptf: Path, stock: Path) -> None:
    print("Count-in: as stock firmware, through the hook")
    runs = {}
    for name, path in (("patched", ptf), ("stock", stock)):
        m = machine(path)
        cur = chance_pattern(m)
        st.set_step(m, cur, 5, 0, 60, 48)
        st.set_step(m, cur, 5, 2, 62, 48)
        for k, v in ((0xE6, 1), (0xE8, 0), (0xE9, 1)):  # count-in on, 1 bar of 2
            m.w8(st.SETTINGS + k, v)
        m.w32(SEQ + COUNT_IN_STEP, 0)
        m.w8(SEQ + COUNT_IN_ON, 1)
        m.events.clear()
        playing_at = None
        for i in range(STEP_TICKS * 8):
            ticks(m, 1)
            if playing_at is None and m.uc.mem_read(SEQ + 0x2A, 1)[0]:
                playing_at = i
        runs[name] = (m, list(m.events), playing_at)
    m, events, at = runs["patched"]
    _, stock_events, stock_at = runs["stock"]
    check(at is not None and at == stock_at, f"playback starts at tick {at} (stock: {stock_at})")
    notes = sum(1 for _, what in events if "note on" in what)
    check(events == stock_events and notes > 0, f"then the same {notes} notes at the same ticks as stock")
    calls = struct.unpack("<I", m.uc.mem_read(patch_ram(ptf, "itcmtest") + 8, 4))[0]
    check(calls == 1, f"the count-in's seq_play_from went through the hook ({calls} call)")


def main() -> int:
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    boot_checks(ptf, stock)
    chance_checks(ptf, stock)
    count_in_checks(ptf, stock)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
