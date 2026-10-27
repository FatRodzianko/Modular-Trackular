"""Emulator test of patch/headroom (docs/TIMING_HEADROOM_PLAN.md).

  python tools/emu/test_headroom.py <TrackerPlus.ptf with headroom>

The firmware may be headroom alone or a bundle with headroom listed last. Whole audio
blocks run through the app vtable's +8 word (0x60038724), as audio_out_cb calls them,
with the stock CPU meter, MIDI input and the engine stubbed: 16 MIDI tracks with a
note on every step. The cycle counter is faked: a read hook on the DWT page writes
CYCCNT = T + (instructions since the block began), and the test sets T per block, so a
block's period is exactly the T step and its work is its instruction count.

Checks:
1. Order: the app vtable word and the bl at 0x600aa7f8 go to headroom's hooks first
   (headroom listed last), and the Config debug lines' two bl snprintf too.
2. Transparent: 160 blocks give the same MIDI messages and the same sequencer state as
   the same firmware with headroom's two audio hooks put back to what they chain to.
3. Measurement: headroom's block and sequencer figures equal the instructions the
   chained calls take, plus its own between the counter reads (at most 48 and 8); the
   period is the T step.
4. Statistics: a 30 % longer period counts one late block and moves the smoothed period
   by under 1 %; a playback start resets the figures; 345 blocks roll the one-second
   window; an export (0x20026e44 set) counts nothing and restarts the period.
5. Garbage at power-on: random state is cleared by the first block.
6. Cost: headroom's own instructions per block (both hooks), at most COST_LIMIT.
7. No writes to the DWT page (or any debug register) from headroom's code.
8. Config lines: the text for known state, at most 21 characters (stock's longest line,
   "avg: 100%, last: 100%"); "no cycle counter" with DWT_CTRL bit 0 clear; "headroom:
   waiting" before a period is known.
"""
from __future__ import annotations

import random
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patch_ptf import bl_target, thumb_bl  # noqa: E402
from layout import patch_code, patch_ram  # noqa: E402
import seq_timing as st  # noqa: E402

SEQ, SETTINGS = st.SEQ, st.SETTINGS
APP = 0x200170E0
APP_VT_BLOCK = 0x60038724           # app vtable +8: app_audio_block
APP_AUDIO_BLOCK = 0x600AA7C9
SEQ_CALL = 0x600AA7F8               # app_audio_block: bl seq_advance
SEQ_VENEER = 0x600AA859
LINE_CALLS = (0x600CB570, 0x600CB590)   # cfg_debug_info: bl snprintf, the two CPU lines
AUDIO_STUBS = (0x6010C2C0, 0x6010C2EC, 0x600AB89C, 0x600AA848, 0x600AA850)  # meter, MIDI in, engine
SEQ_STOP = 0x600ACD90
EXPORTING = 0x20026E44
DWT_CTRL, DWT_CYCCNT = 0xE0001000, 0xE0001004
PERIOD = 1_531_700                  # cycles per block at 528 MHz and 44,122.65 Hz
WINDOW = 345
COST_LIMIT = 100                    # headroom's own instructions per block, both hooks (80 measured)
LINE_MAX = 21
HR_MAGIC = 0x5248544D
# state (headroom.c struct hr_state)
MAGIC, T_PREV, NOMINAL, BLK_MAX, SEQ_MAX, WIN_MAX, LAST_WIN, WIN_N, LATE, SEQ_CUR = range(0, 40, 4)
HAVE_PREV, WAS_PLAYING = 40, 41
STATE_SIZE = 48
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Rig:
    """The sequencer playing 16 MIDI tracks; whole blocks through the vtable word."""

    def __init__(self, ptf: Path, bypass: bool = False):
        self.m = m = st.make(ptf)
        for a in AUDIO_STUBS:
            m.stub_function(a, lambda mm: 0)
        self.lo, self.hi = patch_code(ptf, "headroom")
        self.hr = patch_ram(ptf, "headroom")
        image = bytes(m.uc.mem_read(0x60000000, 0x400000))
        self.block_hook = m.r32(APP_VT_BLOCK)
        self.seq_hook = bl_target(image, 0x60000000, SEQ_CALL) | 1
        self.next_block = self.chain_of(self.block_hook, "mt_hr_next_block", APP_AUDIO_BLOCK)
        self.next_seq = self.chain_of(self.seq_hook, "mt_hr_next_seq_advance", SEQ_VENEER)
        if bypass:                          # headroom's audio hooks out: what they chain to
            m.w32(APP_VT_BLOCK, self.next_block)
            m.write(SEQ_CALL, thumb_bl(SEQ_CALL, self.next_seq))
        # the fake cycle counter
        self.n = 0                          # instructions so far
        self.mark = 0                       # self.n when the block began
        self.t = 0                          # the test's clock at the block's start
        self.spans: dict[int, list[int]] = {}   # entry address -> instruction counts of its calls
        self._open: list[tuple[int, int, int]] = []
        self.own = 0                        # instructions in headroom's code
        self.dwt_writes: list[str] = []
        m.uc.hook_add(UC_HOOK_CODE, self._on_code)
        m.uc.hook_add(UC_HOOK_MEM_READ, self._on_dwt_read, begin=DWT_CTRL, end=DWT_CTRL + 0xFFF)
        m.uc.hook_add(UC_HOOK_MEM_WRITE, self._on_debug_write, begin=0xE0000000, end=0xE00FFFFF)
        m.w32(DWT_CTRL, 1)
        buf = st.pattern(m, 15)
        for t in range(16):
            for s in range(16):
                st.set_step(m, buf, t, s, 36 + (s * 5 + t) % 48, 48 + t)
        m.w32(SEQ + 0x14, buf)
        m.w32(SEQ + 0x18, buf)
        m.w8(SETTINGS + 0x2C, 2)            # MIDI out: DIN
        self.events = m.events

    def chain_of(self, hook: int, word: str, stock: int) -> int:
        """What a headroom hook chains to: its chain word's value (the stock target when
        headroom isn't the hook there)."""
        if not self.lo <= (hook & ~1) < self.hi:
            return stock
        return self.m.r32(self.chain_words[word])

    chain_words: dict[str, int] = {}

    def _on_code(self, uc, address, size, user):
        self.n += 1
        if self.lo <= address < self.hi:
            self.own += 1
        if address in self.watch:
            lr = uc.reg_read(UC_ARM_REG_LR) & ~1
            self._open.append((address, lr, self.n))
        while self._open and address == self._open[-1][1]:
            entry, _, n0 = self._open.pop()
            self.spans.setdefault(entry, []).append(self.n - n0)

    watch: set[int] = set()

    def _on_dwt_read(self, uc, access, address, size, value, user):
        if address == DWT_CYCCNT:
            uc.mem_write(DWT_CYCCNT, struct.pack("<I", (self.t + self.n - self.mark) & 0xFFFFFFFF))

    def _on_debug_write(self, uc, access, address, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        if self.lo <= pc < self.hi:
            self.dwt_writes.append(f"{pc:#x} -> {address:#x}")

    def play(self) -> None:
        self.m.call(st.SEQ_PLAY_FROM, SEQ, 0)

    def stop(self) -> None:
        self.m.call(SEQ_STOP, SEQ, max_insns=5_000_000)

    def block(self, period: int = PERIOD) -> None:
        self.t = (self.t + period) & 0xFFFFFFFF
        self.mark = self.n
        self.m.call(self.block_hook, APP, 0, 0, 128, max_insns=5_000_000)

    def st(self, off: int) -> int:
        return self.m.r32(self.hr + off) if off < HAVE_PREV else self.m.uc.mem_read(self.hr + off, 1)[0]

    def line(self, k: int) -> str:
        image = bytes(self.m.uc.mem_read(0x60000000, 0x400000))
        hook = bl_target(image, 0x60000000, LINE_CALLS[k]) | 1
        buf = self.m.alloc(64)
        self.m.call(hook, buf, 30, 0, 0)
        return self.m.cstr(buf)


def chain_words(ptf: Path) -> dict[str, int]:
    """Addresses of headroom's four chain words, from its symbol file and its place."""
    root = Path(__file__).resolve().parent.parent.parent / "patch" / "headroom"
    sym = root / "build" / "headroom.sym"
    if not sym.exists():
        sym = root / "prebuilt" / "headroom.sym"
    lo, _ = patch_code(ptf, "headroom")
    base = 0x601D0000                       # link.ld
    out = {}
    for line in sym.read_text().splitlines():
        a, _, name = line.split()
        if name.startswith("mt_hr_next_"):
            out[name] = lo + int(a, 16) - base
    return out


def order_checks(r: Rig) -> bool:
    print("Order")
    lo, hi = r.lo, r.hi
    image = bytes(r.m.uc.mem_read(0x60000000, 0x400000))
    first = lo <= (r.block_hook & ~1) < hi and lo <= (r.seq_hook & ~1) < hi
    check(first, "the app vtable +8 word and the bl at 0x600aa7f8 go to headroom first (listed last)")
    lines = all(lo <= bl_target(image, 0x60000000, a) < hi for a in LINE_CALLS)
    check(lines, "the two bl snprintf of the Config debug lines go to headroom")
    check(r.next_block != r.block_hook and r.next_seq != r.seq_hook,
          f"chained: block -> {r.next_block:#x}, sequencer -> {r.next_seq:#x}")
    return first


def transparent_checks(ptf: Path) -> None:
    print("Transparent (160 blocks, 16 MIDI tracks)")
    runs = []
    for bypass in (False, True):
        r = Rig(ptf, bypass)
        r.play()
        for _ in range(160):
            r.block()
        state = bytes(r.m.uc.mem_read(SEQ, 0x800))
        runs.append((list(r.events), state))
    (ev_a, s_a), (ev_b, s_b) = runs
    check(len(ev_a) > 100, f"the run sends MIDI ({len(ev_a)} messages)")
    check(ev_a == ev_b, "the same MIDI messages, in the same order, as without headroom's hooks")
    check(s_a == s_b, "the same sequencer state afterwards")


def measure_checks(r: Rig) -> None:
    print("Measurement")
    r.watch = {r.next_block & ~1, r.next_seq & ~1}
    r.play()
    r.block()                               # the first period starts here
    r.spans.clear()
    for _ in range(40):
        r.block()
    blocks, seqs = r.spans.get(r.next_block & ~1, []), r.spans.get(r.next_seq & ~1, [])
    check(len(blocks) == 40 and len(seqs) == 40, f"40 chained calls of each ({len(blocks)}, {len(seqs)})")
    blk, seq = r.st(BLK_MAX), r.st(SEQ_MAX)
    d_blk, d_seq = blk - max(blocks), seq - max(seqs)
    check(0 <= d_blk <= 48, f"block: {blk:,} = the chained call's worst {max(blocks):,} + {d_blk} of headroom's own")
    check(0 <= d_seq <= 8, f"sequencer: {seq:,} = the chained call's worst {max(seqs):,} + {d_seq} of headroom's own")
    check(r.st(NOMINAL) == PERIOD, f"the period: {r.st(NOMINAL):,} cycles (set {PERIOD:,})")
    check(r.st(LATE) == 0, "no late blocks at an even period")
    r.watch = set()


def statistics_checks(r: Rig) -> None:
    print("Statistics")
    nom = r.st(NOMINAL)
    r.block(PERIOD * 13 // 10)
    late, nom2 = r.st(LATE), r.st(NOMINAL)
    check(late == 1, f"a 30 % longer period: 1 late block ({late})")
    check(abs(nom2 - nom) < nom // 100, f"... the smoothed period moves under 1 % ({nom:,} -> {nom2:,})")
    r.block(PERIOD * 11 // 10)
    check(r.st(LATE) == 1, "a 10 % longer period isn't late (the line is 12.5 %)")
    for _ in range(WINDOW):
        r.block()
    check(r.st(LAST_WIN) > 0 and r.st(WIN_N) < WINDOW, f"{WINDOW} blocks roll the one-second window "
                                                         f"(last {r.st(LAST_WIN):,}, now {r.st(WIN_N)} blocks in)")
    # export
    before = [r.st(o) for o in (NOMINAL, BLK_MAX, SEQ_MAX, LATE, WIN_N)]
    r.m.w8(EXPORTING, 1)
    for _ in range(3):
        r.block(PERIOD * 5)
    during = [r.st(o) for o in (NOMINAL, BLK_MAX, SEQ_MAX, LATE, WIN_N)]
    r.m.w8(EXPORTING, 0)
    r.block(PERIOD * 40)                    # the first block after: no period yet
    after = r.st(LATE), r.st(NOMINAL)
    check(during == before and r.st(HAVE_PREV) == 1 and after == (before[3], before[0]),
          "an export (0x20026e44): nothing counted, and the period restarts after it")
    # a playback start resets the figures
    r.stop()
    r.block()
    check(r.st(WAS_PLAYING) == 0 and r.st(BLK_MAX) > 0, "stopped: the figures stay")
    r.play()
    r.block(PERIOD * 2)
    check(r.st(LATE) == 1 and r.st(WIN_N) == 1, "playback starts: the figures reset (the block that starts it counts)")
    check(r.st(NOMINAL) > 0, "... the smoothed period stays")


def garbage_checks(ptf: Path) -> None:
    print("Garbage at power-on")
    r = Rig(ptf)
    rnd = random.Random(7)
    r.m.write(r.hr, bytes(rnd.randrange(256) for _ in range(STATE_SIZE)))
    r.play()
    r.block()
    check(r.st(MAGIC) == HR_MAGIC and r.st(LATE) == 0 and r.st(NOMINAL) == 0 and r.st(HAVE_PREV) == 1,
          "random state: cleared by the first block")
    r.block()
    check(r.st(NOMINAL) == PERIOD, "... and the second block sets the period")


def cost_checks(r: Rig) -> None:
    print("Cost")
    worst = 0
    for _ in range(64):
        own = r.own
        r.block()
        worst = max(worst, r.own - own)
    check(worst <= COST_LIMIT, f"headroom's own instructions per block: {worst} (at most {COST_LIMIT})")
    check(not r.dwt_writes, f"no writes to the debug registers {r.dwt_writes[:3]}")


def line_checks(ptf: Path) -> None:
    print("Config lines")
    r = Rig(ptf)
    r.m.w32(r.hr + MAGIC, 0)
    r.play()
    r.block()
    check(r.line(0) == "headroom: waiting" and r.line(1) == "", f"before a period: {r.line(0)!r}, {r.line(1)!r}")
    for _ in range(3):
        r.block()
    r.m.w32(r.hr + BLK_MAX, PERIOD * 613 // 1000 + 10)
    r.m.w32(r.hr + SEQ_MAX, PERIOD * 41 // 1000 + 10)
    r.m.w32(r.hr + LAST_WIN, PERIOD * 402 // 1000 + 10)
    r.m.w32(r.hr + WIN_MAX, 0)
    r.m.w32(r.hr + LATE, 3)
    a, b = r.line(0), r.line(1)
    check(a == "blk 61.3% seq 4.1%", f"line 1: {a!r}")
    check(b == "1s 40.2% late 3", f"line 2: {b!r}")
    for off in (BLK_MAX, SEQ_MAX, LAST_WIN):
        r.m.w32(r.hr + off, 0xFFFFFFFF)
    r.m.w32(r.hr + LATE, 0xFFFFFFFF)
    a, b = r.line(0), r.line(1)
    check(len(a) <= LINE_MAX and len(b) <= LINE_MAX, f"at their longest: {a!r} ({len(a)}), {b!r} ({len(b)})")
    r.m.w32(DWT_CTRL, 0)
    check(r.line(0) == "no cycle counter", f"DWT_CTRL bit 0 clear: {r.line(0)!r}")


def main() -> int:
    ptf = Path(sys.argv[1])
    Rig.chain_words = chain_words(ptf)
    r = Rig(ptf)
    if not order_checks(r):
        print(f"{len(failures)} failure(s): headroom must be listed last")
        return 1
    transparent_checks(ptf)
    measure_checks(r)
    statistics_checks(r)
    cost_checks(r)
    garbage_checks(ptf)
    line_checks(ptf)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
