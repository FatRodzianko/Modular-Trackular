"""Check the visualizer patch, running the patched firmware.

  python tools/emu/test_visualizer.py <patched.ptf>

The sequencer runs as the audio engine runs it (the patched seq_advance call in
app_audio_block, 2 ms at a time), the Pattern screen's update method is called every
10 ms (the stock method stubbed: the patch's hook around it is what's tested), a fake
timer object gives the milliseconds, and the four stock column button callbacks are
replaced by stand-ins that select the column and set the screen's "held" flag as the
stock ones do, and log their calls. What the pads show is read back from the real pad
LED driver's PWM frame, which the stock setPad writes. The driver's I2C bus is a fake that
logs every transaction and keeps what the chip would hold; the driver's flush (the
patched vtable word) is called after each screen update while a pad has changed, as the
main loop's scheduler does.

Checks:
- Stopped: the four buttons work as in stock firmware; the visualizer doesn't start.
- Playing: the four held start it, without the fourth press being passed on, with the
  long-press flags cleared, and with the column from before selected again; the pads
  animate within the pads brightness; the firmware's own pad writes are dropped, and
  come back (with what changed meanwhile) when it stops.
- A press of any of the four stops it and isn't passed on, nor are the other presses
  until all four are up; then the buttons work again.
- Record on, holding a bottom-row button (Pattern: the patterns on the pads), performance
  mode, stopping and leaving the screen hide it.
- I2C: while it shows, each flush after the first sends only the changed brightness
  bytes in one burst (no register reads), the chip ends up with exactly the driver's
  frame and every pad enabled, and the bus is busy a small share of the time; hidden,
  the stock flush runs.
- Deterministic: two fresh plays of the same pattern give the same frames (to a level,
  as a step may land one audio block apart); a later pass of the loop gives different ones.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_S0

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from layout import patch_ram  # noqa: E402
from patch_ptf import bl_target  # noqa: E402
import seq_timing as st  # noqa: E402

SEQ = st.SEQ
AUDIO_CALL = 0x600AA7F8
TIMER = 0x20027D18
PAD_MGR = 0x200171D0
LEDS = 0x2000C32C                   # board + 0x450: the pad LED object
DRIVER = LEDS + 4                   # IS31FL3731 driver object: PWM frame at +8
GAMMA = 0x60037390
SET_LAYER_B = 0x600ABDC8            # (mgr, on, level, pad)
FIELDS = 0x20022770                 # Pattern screen + 0x1000
REC, COLUMN, HELD = FIELDS + 0x254, FIELDS + 0x255, FIELDS + 0x4C7
PAD_MODE = FIELDS + 0x354           # int8: -1 normal, 0 patterns (Pattern held), 1, 2
SETTINGS = st.SETTINGS
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
FLUSH_SLOT = 0x600373B8             # pad LED driver vtable: flush
DRV_DIRTY = 0x14D
I2C_BIT_S = 1 / 400_000             # the pad bus runs at 400 kHz
I2C_TXN_S = 30e-6                   # start, address, stop and driver overhead per transaction (estimate)
BUTTON_SLOTS = [0x600DF2DC, 0x600DF2E0, 0x600DF2E4, 0x600DF2E8]
STOCK_BUTTONS = [0x600D20AC, 0x600D4494, 0x600E308C, 0x600E2FAC]
NAMES = ["Note", "Instrument", "FX1", "FX2"]
DT = 0.002                          # seconds per audio call
UPDATE_MS = 10
STEPS = 16

VZ = 0
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Rig:
    def __init__(self, ptf: Path):
        self.m = m = st.make(ptf)
        self.ms = 1000
        self.time = 0.0
        self.calls: list[tuple[str, int]] = []
        timer, vt = m.alloc(0x10), m.alloc(0x10)
        m.w32(timer, vt)
        m.w32(vt, m.add_stub(lambda mm: self.ms & 0xFFFFFFFF))
        m.w32(TIMER, timer)
        m.w32(PAD_MGR + 0x98, LEDS)
        m.w8(SETTINGS + 0x34, 12)
        m.w8(SETTINGS + 0x35, 31)
        m.stub_function(PATTERN_UPDATE, lambda mm: 0)
        for col, addr in enumerate(STOCK_BUTTONS):
            m.stub_function(addr, lambda mm, col=col: self._stock_button(col, mm.arg(0)))
        self.update = m.r32(PATTERN_UPDATE_SLOT)
        self.buttons = [m.r32(a) for a in BUTTON_SLOTS]
        image = bytes(m.uc.mem_read(0x60000000, 0x200000))
        self.advance = bl_target(image, 0x60000000, AUDIO_CALL) | 1
        self.gamma = {v: i for i, v in enumerate(m.uc.mem_read(GAMMA, 32))}
        self.cur, self.nxt = self.pattern(), self.pattern()
        # the fake I2C bus: write (vtable +0xc) and read (+0x10) of (bus, addr, reg, 1, buf, len)
        self.txns: list[tuple[str, int, int]] = []
        self.chip = bytearray(0xB4)                 # frame page 0: enables, blink, PWM
        self.chip_page = 0
        bus, bvt = m.alloc(0x10), m.alloc(0x40)
        m.w32(bus, bvt)
        nothing = m.add_stub(lambda mm: 0)
        for k in range(16):
            m.w32(bvt + 4 * k, nothing)
        m.w32(bvt + 0xC, m.add_stub(self._i2c_write))
        m.w32(bvt + 0x10, m.add_stub(self._i2c_read))
        m.w32(DRIVER + 4, bus)
        self.flush = m.r32(FLUSH_SLOT)
        self.flushes: list[tuple[bool, list]] = []  # (showing, its transactions)
        m.w8(COLUMN, 1)
        m.w8(PAD_MODE, 0xFF)                    # no bottom-row pad view

    def pattern(self) -> int:
        m = self.m
        p = st.pattern(m, STEPS - 1)
        for s in range(STEPS):
            if s % 4 == 0:
                st.set_step(m, p, 0, s, 36 + (s // 4) * 7, 1, (18, 100 - 10 * (s // 4)))
            if s % 4 == 2:
                st.set_step(m, p, 1, s, 50, 2)
            if s % 3 == 0:
                st.set_step(m, p, 2, s, 60 + (s * 5) % 24, 3, (5, s * 3), (18, 60))
            if s == 7:
                st.set_step(m, p, 3, s, -1, 4, (9, 40))   # FX only
        return p

    def _i2c_write(self, mm) -> int:
        reg, buf, n = mm.arg(2), mm.arg(4), mm.arg(5)
        data = bytes(mm.uc.mem_read(buf, n))
        self.txns.append(("w", reg, n))
        if reg == 0xFD:
            self.chip_page = data[0]
        elif self.chip_page == 0 and reg + n <= 0xB4:
            self.chip[reg:reg + n] = data
        return 0

    def _i2c_read(self, mm) -> int:
        self.txns.append(("r", mm.arg(2), mm.arg(5)))
        mm.write(mm.arg(4), bytes(mm.arg(5)))
        return 0

    def _stock_button(self, col: int, state: int) -> int:
        m = self.m
        self.calls.append((NAMES[col], state))
        if state == 1:
            if not any(m.uc.mem_read(HELD, 4)):
                m.w8(HELD + col, 1)
            m.w8(COLUMN, col)
        elif state == 0:
            m.w8(HELD + col, 0)
        return 0

    def press(self, col: int) -> None:
        self.m.call(self.buttons[col], 1)

    def release(self, col: int) -> None:
        self.m.call(self.buttons[col], 0)

    def start(self) -> None:
        self.m.w32(SEQ + 0x14, self.cur)
        self.m.w32(SEQ + 0x18, self.nxt)
        self.m.call(st.SEQ_PLAY_FROM, SEQ, 0)

    def stop(self) -> None:
        self.m.w8(SEQ + 0x2A, 0)

    def run(self, seconds: float, screen: bool = True, frames: list | None = None) -> None:
        m = self.m
        next_update = self.ms
        for _ in range(round(seconds / DT)):
            m.uc.reg_write(UC_ARM_REG_S0, struct.unpack("<I", struct.pack("<f", DT))[0])
            m.call(self.advance, SEQ, max_insns=5_000_000)
            self.time += DT
            self.ms += round(DT * 1000)
            if screen and self.ms >= next_update:
                next_update += UPDATE_MS
                m.call(self.update, 0x20021770, 0, 0, 0)
                if m.uc.mem_read(DRIVER + DRV_DIRTY, 1)[0]:
                    self.txns = []
                    m.call(self.flush, DRIVER)
                    self.flushes.append((self.showing(), self.txns))
                if frames is not None and self.m.r32(VZ + LAST_FRAME) == self.ms:
                    clock = struct.unpack("<f", bytes(m.uc.mem_read(VZ + 4, 4)))[0]
                    frames.append((round(clock, 3), self.pads()))   # a frame was just drawn

    def pads(self) -> tuple[int, ...]:
        pwm = self.m.uc.mem_read(DRIVER + 8, 144)
        return tuple(self.gamma.get(pwm[p + (p // 12) * 4], -1) for p in range(48))

    def app_write(self, pad: int, level: int) -> None:
        """The firmware lighting a pad through its pad manager (layer B)."""
        self.m.call(SET_LAYER_B, PAD_MGR, 1, level, pad)

    def showing(self) -> bool:
        return self.m.uc.mem_read(VZ + SHOWING, 1)[0] == 1


# struct vz_state: magic, clock, phase[3], target[3], wind, hash, last_ripple, 4 bytes, 12 ripples of
# 36 bytes, last_update, last_frame, last_button, armed, showing (offsets)
LAST_FRAME = 4 + 4 + 12 + 12 + 4 + 4 + 4 + 4 + 12 * 36 + 4
SHOWING = LAST_FRAME + 4 + 4 + 1


def enter(r: Rig) -> None:
    for c in range(4):
        r.press(c)
        r.run(0.03)


def release_all(r: Rig) -> None:
    for c in range(4):
        r.release(c)


def button_checks(r: Rig) -> None:
    m = r.m
    print("stopped: stock buttons")
    r.app_write(5, 20)
    check(r.pads()[5] == 20, "a firmware pad write shows (pad 6 at 20)")
    r.calls.clear()
    enter(r)
    check([c for c in r.calls] == [(n, 1) for n in NAMES], f"all four presses passed on ({r.calls})")
    release_all(r)
    r.run(0.1)
    check(not r.showing() and m.uc.mem_read(COLUMN, 1)[0] == 3, "no visualizer; column as stock leaves it (FX2)")
    m.w8(COLUMN, 1)

    print("playing: switching on")
    r.start()
    r.run(0.3)
    r.calls.clear()
    r.press(0)
    r.run(0.03)
    r.press(1)
    check(not any(m.uc.mem_read(HELD, 4)), "two held: long-press flags cleared")
    r.run(0.03)
    r.press(2)
    r.run(0.03)
    r.press(3)
    check(r.calls == [("Note", 1), ("Instrument", 1), ("FX1", 1), ("Instrument", 1)],
          f"fourth press not passed on, Instrument selected again ({r.calls})")
    check(m.uc.mem_read(COLUMN, 1)[0] == 1, "column: Instrument, as before")
    check(not any(m.uc.mem_read(HELD, 4)), "no long-press flag left")
    r.run(0.1)
    check(r.showing(), "showing")
    r.calls.clear()
    release_all(r)
    check(r.calls == [("Note", 0), ("Instrument", 0), ("FX1", 0)], f"releases: FX2's not passed on ({r.calls})")

    drawn: list = []
    r.run(2.0, frames=drawn)
    frames = [f for _, f in drawn]
    distinct = len(set(frames))
    top = max(max(f) for f in frames)
    check(distinct > 50, f"the pads animate ({distinct} different frames in 2 s)")
    check(0 < top <= 31 and min(min(f) for f in frames) >= 0, f"levels within 0..31 (peak {top})")
    lit = sum(1 for f in frames for v in f if v >= 8) / (48 * len(frames))
    check(0.1 < lit < 0.9, f"not all dark, not all bright ({lit:.0%} of pad-frames at a quarter or more)")
    jumps = [max(abs(a - b) for a, b in zip(f, g)) for f, g in zip(frames, frames[1:])]
    small = sum(1 for j in jumps if j <= 2) / len(jumps)
    check(small > 0.95 and max(jumps) <= 6,
          f"gradual: {small:.0%} of 20 ms frame steps change no pad by more than 2 levels (most {max(jumps)})")
    r.app_write(7, 25)
    r.run(0.05)
    check(m.uc.mem_read(PAD_MGR + 0x34 + 7, 1)[0] == 25, "firmware pad write while showing: kept in its layer")

    print("switching off")
    r.calls.clear()
    r.press(2)
    r.run(0.05)
    p = r.pads()
    check(not r.showing() and r.calls == [], "a press of FX1: off, not passed on")
    check(p[5] == 20 and p[7] == 25 and sum(p) == 45, f"stock pads back, with the write made meanwhile ({p[5]}, {p[7]}, sum {sum(p)})")
    check(m.uc.mem_read(COLUMN, 1)[0] == 1, "column unchanged")
    r.press(0)
    r.press(3)
    r.release(2)
    r.release(0)
    r.release(3)
    check(r.calls == [], f"the rest of that chord not passed on ({r.calls})")
    r.press(3)
    r.release(3)
    check(r.calls == [("FX2", 1), ("FX2", 0)], "then the buttons work again")
    m.w8(COLUMN, 1)


def i2c_checks(r: Rig) -> None:
    m = r.m
    print("I2C cost")
    r.flushes.clear()
    r.run(4.0)
    shown = [t for showing, t in r.flushes if showing][1:]
    reads = sum(1 for t in shown for kind, _, _ in t if kind == "r")
    most = max(len(t) for t in shown)
    check(reads == 0 and most <= 2,
          f"showing: {len(shown)} flushes in 4 s, each at most {most} writes (page select + brightness), {reads} reads")
    pwm = bytes(m.uc.mem_read(DRIVER + 8, 144))
    check(bytes(r.chip[0x24:0xB4]) == pwm, "the chip holds exactly the driver's frame")
    enabled = all(r.chip[(p + p // 12 * 4) >> 3] >> ((p + p // 12 * 4) & 7) & 1 for p in range(48))
    check(enabled, "every pad enabled (dark pads are brightness 0)")
    nbytes = sum(n + 2 for t in shown for _, _, n in t)
    ntx = sum(len(t) for t in shown)
    busy = (nbytes * 9 * I2C_BIT_S + ntx * I2C_TXN_S) / 4.0
    r.flushes.clear()
    m.w8(REC, 1)                                # hidden: the stock flush
    r.run(0.1)
    stock = [t for showing, t in r.flushes if not showing]
    stock_tx = max(len(t) for t in stock) if stock else 0
    stock_bytes = max(sum(n + 2 for _, _, n in t) for t in stock) if stock else 0
    check(stock_tx > 20 and bytes(r.chip[0x24:0xB4]) == bytes(m.uc.mem_read(DRIVER + 8, 144)),
          f"hidden: the stock flush ({stock_tx} transactions, {stock_bytes} bytes), the chip holds the stock pads")
    stock_busy = (stock_bytes * 9 * I2C_BIT_S + stock_tx * I2C_TXN_S) * 1000 / 30
    check(busy < 0.05, f"showing: the pad bus busy {busy:.1%} of the time"
          f" (the stock flush at the same frame rate would be {stock_busy:.0%})")
    m.w8(REC, 0)
    r.run(0.1)


def hide_checks(r: Rig) -> None:
    m = r.m
    print("hiding")
    enter(r)
    release_all(r)
    r.run(0.2)
    check(r.showing(), "on again")
    m.w8(REC, 1)
    r.run(0.05)
    check(not r.showing() and r.pads()[5] == 20, "record on: hidden, stock pads")
    m.w8(REC, 0)
    r.run(0.05)
    check(r.showing(), "record off: shows again")
    m.w8(PAD_MODE, 0)
    r.run(0.05)
    check(not r.showing() and r.pads()[5] == 20, "Pattern held (patterns on the pads): hidden, stock pads")
    m.w8(PAD_MODE, 0xFF)
    r.run(0.05)
    check(r.showing(), "Pattern released: shows again")
    m.w8(SEQ + 0x1D, 1)
    r.run(0.05)
    check(not r.showing(), "performance mode: hidden")
    m.w8(SEQ + 0x1D, 0)
    r.run(0.05)
    check(r.showing(), "back from performance mode: shows")
    r.run(0.3, screen=False)
    r.app_write(9, 15)
    p = r.pads()
    check(not r.showing() and p[9] == 15 and p[5] == 20, "Pattern screen gone: the next firmware pad write gives the pads back")
    r.run(0.05)
    check(r.showing(), "back in the Pattern screen: shows")
    r.stop()
    r.run(0.05)
    check(not r.showing() and r.pads()[5] == 20, "stopped: hidden")


def determinism_checks(r: Rig) -> None:
    print("deterministic per take")
    step_s = 60 / 130 / 4                       # 130 BPM, 16th notes
    loop = STEPS * step_s
    takes = []
    for _ in range(2):
        frames: list = []
        r.start()
        r.run(loop * 2, frames=frames)
        r.stop()
        r.run(0.1)
        takes.append(frames)
    first, second = dict(takes[0]), dict(takes[1])
    common = [c for c in first if c in second]
    # The sequencer keeps a remainder of a tick across takes, so a step can come one
    # audio block (2 ms) earlier or later in another take: a level or so on some pads.
    same = sum(1 for c in common if first[c] == second[c])
    worst = max(max(abs(x - y) for x, y in zip(first[c], second[c])) for c in common)
    check(len(common) > 100 and worst <= 2,
          f"two fresh plays: the same frames ({same} of {len(common)} identical, the rest within {worst} levels)")
    later = []
    for c, f in first.items():                  # the frame one loop later (to half a frame)
        if c < loop:
            near = [c2 for c2 in first if abs(c2 - c - loop) < 0.016]
            if near:
                later.append((f, first[near[0]]))
    differ = sum(1 for a, b in later if a != b)
    check(len(later) > 20 and differ > len(later) * 0.8,
          f"the second pass of the loop differs from the first ({differ} of {len(later)} frames)")


def main() -> int:
    global VZ
    ptf = Path(sys.argv[1])
    VZ = patch_ram(ptf, "visualizer")
    r = Rig(ptf)
    button_checks(r)
    hide_checks(r)
    r.start()
    r.run(0.1)
    if not r.showing():                         # still switched on from before: it shows at once
        enter(r)
        release_all(r)
    i2c_checks(r)
    r.stop()
    r.run(0.1)
    determinism_checks(r)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
