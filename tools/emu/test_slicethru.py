"""Check the slicethru patch: Beat Slice notes that play from their slice to the sample end.

  python tools/emu/test_slicethru.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the slicethru-only build and with bundles that include it. The voice checks
call the firmware's play-mode dispatcher (0x60032abc) and the sample voice's render
(0x600327e4) directly, on a voice object the test builds, in the patched and the stock
firmware side by side. The patch's flags are set in its RAM (phase 2: no list entry or
settings file yet).

1. Sites: the dispatcher's Beat Slice call (0x60032b3a) and the Beat Slice note-on's end
   point (0x60032c62) branch into the patch; its version text.
2. Flag off: for every play mode, notes 0..127 and several instruments, the voice object
   after a note-on is byte-identical to stock. The same with the patch's RAM full of
   garbage (it initializes itself), and with other instruments flagged.
3. Flag on: each note starts where stock starts it and ends at the sample end; the
   frames to play, the sample pointer and the reverse start follow. 1, 4 and 48 slices,
   no slices, notes below 24 and past the last slice.
4. Render: a play-through voice plays to the sample end, and every block costs what
   stock's does.
5. The Slice FX (S), reverse, tune and finetune: as stock, with the end at the sample end.
6. Stereo: the right voice (+0x118 set) copies the left one's extended end.
7. Re-entry: a note-on made while another is in progress (as when the audio interrupt
   cuts into a pad preview) uses its own flag and leaves the other's.
8. The patch writes nothing outside its RAM and the stack; cost of a note-on.
"""
from __future__ import annotations

import random
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patch_ptf import bl_target  # noqa: E402
from layout import patch_code, patch_ram  # noqa: E402
import seq_timing as st  # noqa: E402

INS = 0x20200000                    # 48 instruments, 0x174 bytes each
INS_SIZE = 0x174
SAMPLE_TABLE = INS + 0x4DC4         # per instrument: left and right sample addresses
DISPATCH = 0x60032ABC               # (voice, instrument, note): note-on setup by play mode
BS_SITE, END_SITE = 0x60032B3A, 0x60032C62
BS_NOTE_ON, TO_FRAMES = 0x60032B50, 0x60100694
UPDATE = 0x600327E4                 # the sample voice's render (one 128-frame block)
AUDIO_MEM_INIT = 0x600FF704         # (pool, blocks)
SPRINTF, FMT_LITERAL = 0x60167194, 0x600CBF9C
STACK = (0x20050000, 0x20058000)
BT_MAGIC = 0x5442544D
VOICE_SIZE = 0x140
BEAT_SLICE = 5
FRAMES = 44100
QUARTERS = (0, 0x4000, 0x8000, 0xC000)
# voice fields the note-on sets (FINDINGS section 27)
V_START, V_END, V_FRAMES, V_PTR, V_LEFT, V_REV = 0x34, 0x38, 0x50, 0x18, 0x1C, 0x7C
V_PLAYING, V_ADDR, V_CHANNEL, V_LEN = 0x20, 0x24, 0x28, 0x68
V_PITCH, V_SLICE_FX, V_REVERSE, V_FX_SLICE, V_PARTNER = 0x74, 0xAE, 0xB0, 0xC4, 0x118
V_TUNE, V_FINETUNE = 0x90, 0x91

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Rig:
    """One firmware (patched or stock): instruments, sample buffers and voices."""

    def __init__(self, ptf: Path, patched: bool):
        self.m = m = st.make(ptf)
        self.patched = patched
        self.ram = patch_ram(ptf, "slicethru") if patched else None
        self.code = patch_code(ptf, "slicethru") if patched else None
        self.samples: dict[tuple[int, int], int] = {}
        m.call(AUDIO_MEM_INIT, 0x20004378, 80)

    def instrument(self, i: int, mode: int = BEAT_SLICE, frames: int = FRAMES, slices=QUARTERS,
                   tune: int = 0, finetune: int = 0) -> None:
        m = self.m
        a = INS + INS_SIZE * i
        m.write(a, bytes(INS_SIZE))
        m.w8(a, 1)
        m.w32(a + 0x2C, frames)
        m.w8(a + 0x3C, mode)
        m.w16(a + 0x40, 1)
        m.w16(a + 0x42, 0xFFFE)
        m.w16(a + 0x44, 0xFFFF)
        m.w8(a + 0xFE, tune & 0xFF)
        m.w8(a + 0xFF, finetune & 0xFF)
        m.w8(a + 0x100, 50)
        for k, s in enumerate(slices):
            m.w16(a + 0x108 + 2 * k, s)
        m.w8(a + 0x168, len(slices))
        key = (i, frames)
        if key not in self.samples:
            buf = m.alloc(frames * 2 + 64)
            m.write(buf, b"".join(struct.pack("<h", (k % 2000) + 1) for k in range(frames)))
            self.samples[key] = buf
        m.w32(SAMPLE_TABLE + 8 * i, self.samples[key])
        m.w32(SAMPLE_TABLE + 8 * i + 4, self.samples[key])

    def voice(self, channel: int = 0, partner: int = 0) -> int:
        v = self.m.alloc(VOICE_SIZE)
        self.m.w8(v + V_CHANNEL, channel)
        self.m.w32(v + V_PARTNER, partner)
        return v

    # -- the patch's RAM ----------------------------------------------------------------
    def flags(self, *instruments: int) -> None:
        if not self.patched:
            return
        lo = hi = 0
        for i in instruments:
            if i < 32:
                lo |= 1 << i
            else:
                hi |= 1 << (i - 32)
        m = self.m
        m.w32(self.ram, BT_MAGIC)
        m.w32(self.ram + 4, lo)
        m.w32(self.ram + 8, hi)
        m.w8(self.ram + 12, 0)

    def cur(self) -> int:
        return self.m.uc.mem_read(self.ram + 12, 1)[0]

    def note_on(self, v: int, i: int, note: int) -> int:
        return self.m.call(DISPATCH, v, i, note)

    def fields(self, v: int) -> dict[str, int]:
        r = self.m.r32
        return {"start": r(v + V_START), "end": r(v + V_END), "frames": r(v + V_FRAMES),
                "ptr": r(v + V_PTR) - r(v + V_ADDR), "left": r(v + V_LEFT), "rev": r(v + V_REV),
                "playing": self.m.uc.mem_read(v + V_PLAYING, 1)[0]}

    def blob(self, v: int) -> bytes:
        """The voice object, with its two sample pointers made relative to the sample
        (the two firmwares' buffers are at different scratch addresses)."""
        b = bytearray(self.m.uc.mem_read(v, VOICE_SIZE))
        addr = self.m.r32(v + V_ADDR)
        if addr:
            struct.pack_into("<I", b, V_PTR, (self.m.r32(v + V_PTR) - addr) & 0xFFFFFFFF)
            struct.pack_into("<I", b, V_ADDR, 0)
        return bytes(b)


def frames_of(point: int, frames: int = FRAMES) -> int:
    return int(frames / 65535.0 * point)       # as 0x60100694 (float32, truncated); exact for these


# ---- 1. sites and version -----------------------------------------------------------

def site_checks(p: Rig) -> None:
    print("sites and version")
    img = p.m.image
    lo, hi = p.code
    for at, name in ((BS_SITE, "the dispatcher's Beat Slice call"), (END_SITE, "the Beat Slice end point")):
        t = bl_target(img, 0x60030000, at)
        check(lo <= t < hi, f"{name} ({at:#x}) branches into the patch ({t:#x})")
    m = p.m
    buf = m.alloc(64)
    m.call(SPRINTF, buf, m.r32(FMT_LITERAL), 1, 2, 0)
    text = bytes(m.uc.mem_read(buf, 64)).split(b"\0")[0].decode()
    check(text.startswith("1.2.0-") and len(text) <= 9, f"version text {text!r}")


# ---- 2. flag off --------------------------------------------------------------------

def off_checks(p: Rig, s: Rig) -> None:
    print("flag off: the voice as stock")
    notes = list(range(0, 128, 3)) + [24, 25, 26, 27, 71, 127]
    for setup in ("garbage RAM", "zero flags", "other instruments flagged"):
        bad = []
        for mode in range(8):
            for i in (0, 7, 47):
                for r in (p, s):
                    r.instrument(i, mode=mode)
                if setup == "garbage RAM":
                    p.m.write(p.ram, bytes([0xA5]) * 16)
                elif setup == "zero flags":
                    p.flags()
                else:
                    p.flags(*[k for k in range(48) if k != i])
                for note in notes:
                    vp, vs = p.voice(), s.voice()
                    rp, rs = p.note_on(vp, i, note), s.note_on(vs, i, note)
                    if rp != rs or p.blob(vp) != s.blob(vs):
                        bad.append((mode, i, note))
        check(not bad, f"{setup}: modes 0-7, instruments 0, 7, 47, {len(notes)} notes each: "
                       f"every voice byte-identical to stock{'' if not bad else f' (differ: {bad[:4]})'}")
    check(p.m.r32(p.ram) == BT_MAGIC and p.cur() == 0, "the patch initialized its RAM, cur is 0 after the calls")


# ---- 3. flag on ---------------------------------------------------------------------

def on_checks(p: Rig, s: Rig) -> None:
    print("flag on: from the slice to the sample end")
    for slices, label in ((QUARTERS, "4 slices"), ((0,), "1 slice"),
                          (tuple(k * 1365 for k in range(48)), "48 slices"), ((), "no slices")):
        for r in (p, s):
            r.instrument(3, slices=slices)
        p.flags(3)
        bad = []
        n = max(len(slices), 1)
        for note in [0, 12, 23] + list(range(24, 24 + n + 2)) + [100, 127]:
            vp, vs = p.voice(), s.voice()
            p.note_on(vp, 3, note)
            s.note_on(vs, 3, note)
            a, b = p.fields(vp), s.fields(vs)
            want = dict(b, end=FRAMES, frames=FRAMES - b["start"], rev=0)
            if a != want:
                bad.append((note, a, want))
        check(not bad, f"{label}: every note starts where stock does, ends at frame {FRAMES}, "
                       f"frames to play = {FRAMES} - start{'' if not bad else f' (first: {bad[0]})'}")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
    p.flags(3)
    vp = p.voice()
    p.note_on(vp, 3, 25)
    f = p.fields(vp)
    check((f["start"], f["end"], f["frames"]) == (frames_of(0x4000), FRAMES, FRAMES - frames_of(0x4000)),
          f"note 25 (slice 2 of 4): frames {f['start']}..{f['end']} ({f['frames']} to play)")


# ---- 4. render ----------------------------------------------------------------------

def render(r: Rig, v: int, limit: int = 1000) -> tuple[int, int]:
    """Render blocks until the voice stops: (blocks, instructions per block)."""
    m = r.m
    m.write(v + V_PITCH, struct.pack("<f", 1.0))     # pitch ratio 1 (the rig builds no note tables)
    count = {"n": 0}

    def counter(uc, a, sz, u):
        count["n"] += 1
    h = m.uc.hook_add(UC_HOOK_CODE, counter)
    n = 0
    while m.uc.mem_read(v + V_PLAYING, 1)[0] and n < limit:
        m.call(UPDATE, v)
        n += 1
    m.uc.hook_del(h)
    return n, count["n"] // max(n, 1)


def render_checks(p: Rig, s: Rig) -> None:
    print("render to the end")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
    p.flags(3)
    for note in (24, 25, 27):
        vp, vs = p.voice(), s.voice()
        p.note_on(vp, 3, note)
        s.note_on(vs, 3, note)
        want = p.fields(vp)["frames"]
        n, cost = render(p, vp)
        ns, cost_s = render(s, vs)
        check(n == -(-want // 128) and ns == -(-s.fields(vs)["frames"] // 128),
              f"note {note}: plays {n} blocks for {want} frames (stock {ns} blocks, its slice)")
        vq, vt = p.voice(), s.voice()                    # cost: the same number of blocks, both playing
        p.note_on(vq, 3, note)
        s.note_on(vt, 3, 24 if note != 27 else 27)
        p.m.write(vq + V_PITCH, struct.pack("<f", 1.0))
        s.m.write(vt + V_PITCH, struct.pack("<f", 1.0))
        cp = cs = 0
        for _ in range(10):
            cp += steps(p, vq)
            cs += steps(s, vt)
        check(cp == cs, f"note {note}: {cp // 10} instructions per block, stock {cs // 10}")


def steps(r: Rig, v: int) -> int:
    count = {"n": 0}

    def counter(uc, a, sz, u):
        count["n"] += 1
    h = r.m.uc.hook_add(UC_HOOK_CODE, counter)
    r.m.call(UPDATE, v)
    r.m.uc.hook_del(h)
    return count["n"]


# ---- 5. S FX, reverse, tune ---------------------------------------------------------

def fx_checks(p: Rig, s: Rig) -> None:
    print("the Slice FX, reverse, tune and finetune")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS, tune=-7, finetune=20)
    p.flags(3)
    for label, setup in (("S FX slice 3", {V_SLICE_FX: 1, V_FX_SLICE: 2}), ("reverse", {V_REVERSE: 1}),
                         ("S FX slice 4 and reverse", {V_SLICE_FX: 1, V_FX_SLICE: 3, V_REVERSE: 1})):
        vp, vs = p.voice(), s.voice()
        for off, val in setup.items():
            p.m.w8(vp + off, val)
            s.m.w8(vs + off, val)
        p.note_on(vp, 3, 24)
        s.note_on(vs, 3, 24)
        a, b = p.fields(vp), s.fields(vs)
        rev = FRAMES - b["start"] - 1 if V_REVERSE in setup else 0
        want = dict(b, end=FRAMES, frames=FRAMES - b["start"], rev=rev)
        same = (p.m.uc.mem_read(vp + V_TUNE, 2) == s.m.uc.mem_read(vs + V_TUNE, 2)
                and p.m.r32(vp + 0x23) & 0xFF == s.m.r32(vs + 0x23) & 0xFF)
        check(a == want and same, f"{label}: start {a['start']} as stock, end {a['end']}, reverse start "
                                  f"{a['rev']}; tune, finetune and the fixed pitch as stock")


# ---- 6. stereo ----------------------------------------------------------------------

def stereo_checks(p: Rig, s: Rig) -> None:
    print("stereo: the right voice copies the left one")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
    p.flags(3)
    left = p.voice(0)
    right = p.voice(1, partner=left)
    p.note_on(left, 3, 26)
    p.note_on(right, 3, 26)
    a, b = p.fields(left), p.fields(right)
    check(a["end"] == b["end"] == FRAMES and a["start"] == b["start"] == frames_of(0x8000),
          f"both voices: frames {b['start']}..{b['end']}")


# ---- 7. re-entry --------------------------------------------------------------------

def reentry_checks(p: Rig, s: Rig) -> None:
    print("re-entry: a note-on inside another")
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
        r.instrument(4, slices=QUARTERS)
    p.flags(3)                          # 3 plays through, 4 doesn't
    m = p.m
    for outer, inner in ((1, 4), (0, 3)):
        m.w8(p.ram + 12, outer)         # cur as an outer note-on in progress leaves it
        v, vs = p.voice(), s.voice()
        p.note_on(v, inner, 25)
        s.note_on(vs, inner, 25)
        end = p.fields(v)["end"]
        want = FRAMES if inner == 3 else s.fields(vs)["end"]
        check(end == want and p.cur() == outer,
              f"cur {outer} in progress, instrument {inner}'s note-on: end {end} (its own flag), "
              f"cur back to {p.cur()}")
    m.w8(p.ram + 12, 0)


# ---- 8. writes and cost -------------------------------------------------------------

def write_checks(p: Rig, s: Rig) -> None:
    print("writes and cost")
    m = p.m
    lo, hi = p.code
    stray = []

    def on_write(uc, access, a, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        if lo <= pc < hi and not (p.ram <= a < p.ram + 16 or STACK[0] <= a < STACK[1]):
            stray.append((hex(pc), hex(a)))
    h = m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    rnd = random.Random(7)
    for r in (p, s):
        r.instrument(3, slices=QUARTERS)
        r.instrument(9, mode=0)
    for k in range(300):
        p.flags(*rnd.sample(range(48), rnd.randint(0, 48)))
        p.note_on(p.voice(), rnd.choice((3, 9)), rnd.randint(0, 127))
    m.uc.hook_del(h)
    check(not stray, f"300 random note-ons: no write by the patch outside its RAM and the stack {stray[:3]}")
    p.flags(3)
    costs = []
    for r in (p, s):
        c = {"n": 0}

        def counter(uc, a, sz, u, c=c):
            c["n"] += 1
        hk = r.m.uc.hook_add(UC_HOOK_CODE, counter)
        r.note_on(r.voice(), 3, 25)
        r.m.uc.hook_del(hk)
        costs.append(c["n"])
    check(costs[0] - costs[1] < 60, f"a Beat Slice note-on: {costs[0]} instructions, stock {costs[1]} "
                                    f"(+{costs[0] - costs[1]})")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    p, s = Rig(ptf, True), Rig(stock, False)
    site_checks(p)
    off_checks(p, s)
    on_checks(p, s)
    render_checks(p, s)
    fx_checks(p, s)
    stereo_checks(p, s)
    reentry_checks(p, s)
    write_checks(p, s)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
