"""Check the swing patch (Global Swing).

  python tools/emu/test_swing.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the swing-only build and with bundles that include it. The sequencer runs in
time, as the audio engine runs it: the patched seq_advance call of app_audio_block is
called with 0.5 ms at a time (or the stock seq_advance, for the stock firmware), and a
MIDI note on every step of track 1 marks when each step starts (the MIDI port objects
are fakes that record the time). Swing shows as steps alternating long and short: at
swing s a pair of steps lasts two plain steps, split (s + 50) : (150 - s).

1. Global swing Off: the stock sequencer's timing, sample for sample, with swing left
   over from before (as stock firmware keeps it).
2. Global swing On: a take starts on the long step at the Swing amount, whatever swing
   was left over (66 from 50 left on, 50 from 70 left on); a Swing FX changes it until
   the pattern starts again, and the next pass of the loop starts at the Swing amount
   again; a Swing FX on a muted track changes nothing; in song mode the next pattern
   starts at the Swing amount; changing the amount while playing changes the swing;
   turning Global swing off while playing leaves no swing; the render loop's call is
   hooked too.
3. Config menu: the stock menu constructor; Project Settings holds its 10 stock items and
   a "Global Swing" folder (among any other patches' entries) with "Global swing" (Off/On,
   Off) and "Swing amount" (25..75, showing 50); the stock Apply stores the values.
4. Settings file: swing.bin per project, through the screens' update hooks with a fake
   SD card: defaults and no file, one write a while after a change, per-project values,
   a damaged file, a project without a name; Save As and new projects through the
   firmware's save request (project_save.save_as_checks).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_S0

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fakefs import PROJECT_NAME, FakeFS  # noqa: E402
from layout import patch_code, patch_ram  # noqa: E402
from machine import Machine  # noqa: E402
from patch_ptf import bl_target  # noqa: E402
from project_save import new_project_checks, save_as_checks  # noqa: E402
import seq_timing as st  # noqa: E402

SEQ = st.SEQ
SEQ_ADVANCE = 0x3774                # ITCM: (seq, seconds)
AUDIO_CALL, RENDER_CALL = 0x600AA7F8, 0x600BC27A
TEMPO = 0x20204774                  # float BPM (130 by default)
MUTE = 0x202046E0                   # + track: muted
SWING_FX = 17                       # I
DT = 0.0005                         # seconds per call
MENU_CTOR, ATEXIT = 0x60118ACC, 0x600A3594
PROJECT_SETTINGS = 0x20028714
MENU_CURRENT = 0x20028180
MENU_SELECT = 0x60118928
MENU_APPLY, MENU_EDITOR_CLOSE = 0x60118658, 0x600CAB60
OFF_ON = 0x60060B88
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
CONFIG_UPDATE_SLOT, CONFIG_UPDATE = 0x6003B5F8, 0x600C6474
FILE_MAGIC = b"HSW1"
SAVE_DELAY = 25
STOCK_ITEMS = ["Performance presets", "Pattern divider", "Notes scale", "Root note", "Notes layout",
               "Anti-aliasing", "Limiter mode", "Sends mode", "Extra headroom", "Snap to zero"]

SW = 0                              # set in main(): the patch's state
CODE = (0, 0)
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Player:
    def __init__(self, ptf: Path, patched: bool = True, steps: int = 8):
        self.m = m = st.make(ptf)
        self.time = 0.0
        self.starts: list[float] = []          # when each step's note went out
        self.cur, self.nxt = st.pattern(m, steps - 1), st.pattern(m, steps - 1)
        for s in range(steps):
            st.set_step(m, self.cur, 0, s, 60, 48)
            st.set_step(m, self.nxt, 0, s, 60, 48)
        m.w8(st.SETTINGS + 0x2C, 2)            # MIDI out: jack
        image = bytes(m.uc.mem_read(0x60000000, 0x200000))
        self.advance = bl_target(image, 0x60000000, AUDIO_CALL) | 1 if patched else SEQ_ADVANCE | 1
        self.render = bl_target(image, 0x60000000, RENDER_CALL) | 1 if patched else SEQ_ADVANCE | 1
        m.events.clear()

    def setting(self, on: int, amount: int) -> None:
        self.m.w8(SW + 8, on)
        self.m.w8(SW + 9, amount - 25)

    def leftover(self, on: int, amount: float, phase: int) -> None:
        """Swing as stock firmware leaves it from before."""
        self.m.w8(SEQ + 0x60, on)
        self.m.w8(SEQ + 0x61, phase)
        self.m.write(SEQ + 0x64, struct.pack("<f", amount))

    def start(self) -> None:
        self.m.w32(SEQ + 0x14, self.cur)
        self.m.w32(SEQ + 0x18, self.nxt)
        self.m.call(st.SEQ_PLAY_FROM, SEQ, 0)

    def run(self, seconds: float, render: bool = False) -> None:
        m = self.m
        for _ in range(int(seconds / DT)):
            m.events.clear()
            m.uc.reg_write(UC_ARM_REG_S0, struct.unpack("<I", struct.pack("<f", DT))[0])
            m.call(self.render if render else self.advance, SEQ, max_insns=5_000_000)
            self.time += DT
            if any("note on" in what for _, what in m.events):
                self.starts.append(self.time)

    def steps(self) -> list[float]:
        """Step lengths, ms."""
        return [1000 * (b - a) for a, b in zip(self.starts, self.starts[1:])]


def ratio(lengths: list[float]) -> list[float]:
    """Each pair's first step as a share of the pair, as a swing amount (50 = even)."""
    return [round(100 * a / (a + b)) for a, b in zip(lengths[0::2], lengths[1::2])]


def timing_checks(ptf: Path, stock: Path) -> None:
    print("Global swing Off: as stock")
    a, b = Player(ptf), Player(stock, patched=False)
    for p in (a, b):
        p.leftover(1, 66.0, 0)
        p.start()
        p.run(2.0)
    check(a.starts == b.starts and a.starts,
          f"swing left over at 66: the same step times as stock firmware (pairs {ratio(a.steps())[:4]})")

    print("Global swing On")
    p = Player(ptf)
    p.setting(1, 66)
    p.leftover(1, 50.0, 1)
    p.start()
    p.run(2.0)
    r = ratio(p.steps())
    check(r and all(abs(x - 58) <= 1 for x in r[:6]),
          f"Swing amount 66, left over 50 from the short step: every pair long then short ({r[:6]}; "
          "116/200 = 58 % of the pair)")
    p = Player(ptf)
    p.setting(1, 50)
    p.leftover(1, 70.0, 0)
    p.start()
    p.run(2.0)
    r = ratio(p.steps())
    check(r and all(x == 50 for x in r[:6]), f"Swing amount 50, left over 70: even steps ({r[:6]})")

    p = Player(ptf, steps=8)
    st.set_step(p.m, p.cur, 0, 4, 60, 48, (SWING_FX, 50))  # a Swing FX 50 on step 5
    p.setting(1, 66)
    p.start()
    p.run(2.6)
    r = ratio(p.steps())
    check(len(r) >= 7 and all(abs(x - 58) <= 1 for x in r[0:2]) and all(x == 50 for x in r[2:4])
          and all(abs(x - 58) <= 1 for x in r[4:6]),
          f"a Swing FX 50 on step 5: even from there to the pattern's end, the next pass swings again ({r[:7]})")

    p = Player(ptf, steps=8)
    st.set_step(p.m, p.cur, 1, 0, 60, 48, (SWING_FX, 50))  # on track 2, muted
    p.m.w8(MUTE + 1, 1)
    p.setting(1, 66)
    p.start()
    p.run(1.5)
    r = ratio(p.steps())
    check(r and all(abs(x - 58) <= 1 for x in r[:4]), f"a Swing FX on a muted track: no change ({r[:4]})")

    p = Player(ptf, steps=4)
    st.set_step(p.m, p.cur, 0, 0, 60, 48, (SWING_FX, 75))  # pattern 1 swings 75 by FX
    p.m.write(st.PROJECT + 0x5C2, bytes([1, 2, 0]))        # playlist: 1, 2
    p.m.w8(st.PROJECT + 0x6C1, 0)
    p.m.stub_function(st.FM_REQUEST_PATTERN_LOAD, lambda mm: 1)
    p.setting(1, 60)
    p.start()
    p.m.w8(SEQ + 0x1C, 1)                                    # song mode
    p.run(1.2)
    r = ratio(p.steps())
    check(len(r) >= 4 and all(abs(x - 62) <= 1 for x in r[0:2]) and all(abs(x - 55) <= 1 for x in r[2:4]),
          f"song mode: pattern 1's Swing FX 75, then pattern 2 starts at the Swing amount 60 ({r[:4]})")

    p = Player(ptf)
    p.setting(1, 66)
    p.start()
    p.run(1.0)
    p.setting(1, 50)
    n = len(p.starts)
    p.run(1.0)
    later = ratio(p.steps()[n + 1:])
    check(later and all(x == 50 for x in later[:3]), f"the amount changed to 50 while playing: even from then on ({later[:3]})")
    p.setting(1, 70)
    p.run(0.5)
    p.setting(0, 70)
    n = len(p.starts)
    p.run(1.0)
    later = ratio(p.steps()[n + 1:])
    check(later and all(x == 50 for x in later[:3]) and p.m.uc.mem_read(SEQ + 0x60, 1)[0] == 0,
          f"Global swing turned Off while playing: no swing ({later[:3]})")

    p = Player(ptf)
    p.setting(1, 66)
    p.leftover(1, 50.0, 1)
    p.start()
    p.run(1.5, render=True)
    r = ratio(p.steps())
    check(r and all(abs(x - 58) <= 1 for x in r[:4]), f"through the render loop's call: the same ({r[:4]})")


def menu_checks(ptf: Path) -> None:
    print("Config menu: Project Settings > Global Swing")
    m = Machine(ptf)
    m.stub_function(ATEXIT, lambda mm: 0)
    m.call(MENU_CTOR, max_insns=50_000_000)
    ps = PROJECT_SETTINGS
    cap, count = m.r32(ps + 8) & 0xFF, m.r32(ps + 0x1C) >> 8 & 0xFF
    kids = [m.r32(m.r32(ps + 0xC) + 4 * i) for i in range(min(cap, count))]
    labels = [m.cstr(m.r32(k + (0x18 if m.r32(k) & 0xFF == 1 else 4))) for k in kids]
    check(cap == count and count >= 11, f"Project Settings: room for {cap}, holds {count}")
    check(labels[:10] == STOCK_ITEMS, "the 10 stock items are unchanged, in order")
    if "Global Swing" not in labels[10:]:
        check(False, f"a 'Global Swing' folder after the stock items ({labels[10:]})")
        return
    at = labels.index("Global Swing", 10)
    folder = kids[at]
    fcap, fcount = m.r32(folder + 8) & 0xFF, m.r32(folder + 0x1C) >> 8 & 0xFF
    items = [m.r32(m.r32(folder + 0xC) + 4 * i) for i in range(min(fcap, fcount))]
    names = [m.cstr(m.r32(i + 4)) for i in items]
    check(fcap == fcount == 2 and names == ["Global swing", "Swing amount"],
          f"item {at + 1} is the folder 'Global Swing' with {names}")
    d_on, d_amount = m.r32(items[0] + 0x10), m.r32(items[1] + 0x10)
    cb = m.r32(d_on + 12)
    check(m.r32(items[0] + 0xC) & 0xFF == 4 and m.r32(d_on) == SW + 8 and m.r32(d_on + 4) & 0xFF == 2
          and m.r32(d_on + 8) == OFF_ON and CODE[0] <= cb < CODE[1] and cb & 1 and m.uc.mem_read(SW + 8, 1)[0] == 0,
          "'Global swing': a stock Off/On item on the patch's setting, starting Off")
    lo, hi, step, offset = m.uc.mem_read(d_amount + 4, 4)
    check(m.r32(items[1] + 0xC) & 0xFF == 2 and m.r32(d_amount) == SW + 9 and (lo, hi, step, offset) == (0, 50, 1, 25)
          and m.uc.mem_read(SW + 9, 1)[0] + 25 == 50,
          f"'Swing amount': a number item showing {lo + offset}..{hi + offset}, starting at 50")
    m.stub_function(0x60118828, lambda mm: 0)
    m.w32(MENU_CURRENT, ps)
    m.w8(ps + 0x1C, at)
    m.call(MENU_SELECT)
    closed = []
    m.stub_function(MENU_EDITOR_CLOSE, lambda mm: closed.append(1))
    for idx, value, addr, what in ((0, 1, SW + 8, "Global swing On"), (1, 41, SW + 9, "Swing amount 66"),
                                   (0, 0, SW + 8, "Global swing Off")):
        m.w8(folder + 0x1C, idx)
        n = len(closed)
        m.call(MENU_APPLY, 0, value, max_insns=2_000_000)
        check(m.uc.mem_read(addr, 1)[0] == value and len(closed) == n + 1, f"Apply {what}: stored, editor closed")


def fnv1a(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h


def file_checks(ptf: Path) -> None:
    print("settings file (swing.bin), through the screens' update hooks")
    m = Machine(ptf)
    fs = FakeFS(m, Path("."))
    m.stub_function(PATTERN_UPDATE, lambda mm: 0)
    m.stub_function(CONFIG_UPDATE, lambda mm: 0)
    pat_hook, cfg_hook = m.r32(PATTERN_UPDATE_SLOT), m.r32(CONFIG_UPDATE_SLOT)

    def tick(n=1, hook=None):
        for _ in range(n):
            m.call(hook or pat_hook, 0x20021770)

    def values():
        return m.uc.mem_read(SW + 8, 1)[0], m.uc.mem_read(SW + 9, 1)[0] + 25

    def path(name):
        return f"Projects/User/{name}/swing.bin"

    def writes():
        return len([x for x in fs.log if "swing.bin mode 3" in x])

    def hsw1(on, amount):
        data = bytes([on, amount - 25, 0, 0])
        return FILE_MAGIC + data + struct.pack("<I", fnv1a(data))

    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(values() == (0, 50), "project without swing.bin: Off, 50")
    tick(SAVE_DELAY + 5)
    check(writes() == 0, "nothing written while nothing changed")
    m.w8(SW + 8, 1)
    m.w8(SW + 9, 66 - 25)
    tick(SAVE_DELAY - 2, cfg_hook)
    check(writes() == 0, "no write right after a change")
    tick(5, cfg_hook)
    check(writes() == 1 and fs.overlay.get(path("A")) == hsw1(1, 66), "the change is written once, a while later (HSW1, 12 bytes)")
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    check(values() == (0, 50), "another project without the file: Off, 50")
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(values() == (1, 66), "back to the first project: On, 66")
    good = fs.overlay[path("A")]
    fs.overlay[path("A")] = good[:11] + bytes([good[11] ^ 1])
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(values() == (0, 50), "damaged file: Off, 50")
    fs.overlay[path("A")] = good
    m.write(PROJECT_NAME, b"\0")
    tick()
    m.w8(SW + 8, 1)
    n = writes()
    tick(SAVE_DELAY * 2)
    check(writes() == n, "project without a name: nothing written")

    def put(v):
        m.w8(SW + 8, v[0])
        m.w8(SW + 9, v[1] - 25)

    save_as_checks(check, m, fs, tick, "swing.bin", values, put, (0, 50), (1, 70), (1, 30),
                   lambda v: hsw1(*v), SAVE_DELAY)
    new_project_checks(check, m, fs, tick, "swing.bin", values, (0, 50), (1, 70), lambda v: hsw1(*v), SAVE_DELAY)


def main() -> int:
    global SW, CODE
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    SW = patch_ram(ptf, "swing")
    CODE = patch_code(ptf, "swing")
    timing_checks(ptf, stock)
    menu_checks(ptf)
    file_checks(ptf)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
