"""Check the resample patch (phase 3: the buffer above the loaded samples, and Play
during a recording with the engine on).

  python tools/emu/test_resample.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the resample-only build and with bundles that include it. The checks run the
firmware's record start (0x600f9074, its screen calls stubbed) and the audio engine
(tools/emu/synth_rig.py, with engine_block_update before every graph update, as the
audio interrupt runs them) in the patched and the stock firmware side by side. The
recorder's entry is replayed with its stock calls; Play is pressed through the pool
word the recorder binds (0x600fa4ac), and the stock sequencer ticks a pattern.
docs/LIVE_RESAMPLING_PLAN.md has the design.

1. Sites: the record start's buffer call (0x600f90d2), the Play handler's pool word,
   the recorder's update slot and the ITCM image's "recording?" word point into the
   patch (and the booted ITCM holds it); its version text.
2. Placement: for a range of bank fill levels (0x20204f48) and both channel counts, the
   recording's channel pointers and frames are where the plan puts them: the first
   32-byte boundary at or above base + used, the rest of the bank rounded down to 256
   bytes. With nothing loaded, and whenever less than 10 seconds would fit (or the
   bank's fields hold garbage), the recorder gets exactly stock's buffer.
3. The patch's RAM full of garbage: it initializes itself.
4. A recording: the loaded samples' bytes stay as they were and the recording holds
   the input. In stock firmware the same recording writes over them (the reason the
   recorder blocks the sample instruments).
5. The patch writes nothing outside its RAM and the stack; cost of the hook.
6. Play during a recording (Monitor off, the input silent): samples, synths and MIDI
   play. The output is bit-identical to stock firmware given the same steps by hand
   (the recorder's entry, the stock calls that open the gates, Play). Against normal
   playback with the recorder closed it differs only while the sends come back: the
   firmware recomputes them in the first engine block after Play, not before it.
   engine_block_update's "recording?" answers 0 only while the patch has the gates
   open, and only for the recording it moved.
7. The input: with Monitor off, an input of 1000 / -1000 changes nothing at the
   master (no line-in leak, so a cable from the output back to the input can't loop),
   and the recording holds exactly the input while the pattern plays. With Monitor
   on, the input reaches the master at the same level as in the stock recorder.
8. Stop: stopping the recording stops the pattern and closes the gates. The notes
   still sounding end as after a normal pattern stop (their release); once they have,
   nothing sounds and every byte the recorder's entry wrote is as in stock after the
   entry and the same number of audio blocks.
9. Stock where the engine isn't asked for: Play in the recorder with no recording, and
   Play during a recording that couldn't be moved, leave the gates shut; a recording
   without Play gives the stock output at the master.
10. Cost: a block of the engine-on recording costs about what normal playback does;
   the patch's own share per block, and its writes on Play and on the update.
11. Shift+Rec, through the real Rec handler: plain Rec arms nothing. Shift+Rec without a
   count-in (Pre Roll off, or Radio) starts the pattern with the recording; with Pre
   Roll on it arms, the count-in runs with the pattern stopped, and the pattern starts
   at the record start. Either way the output and MIDI are those of Rec then Play at
   once (section 6). Play during the count-in arms instead of starting the pattern.
   No pattern when the buffer can't be moved, after a count-in that ended without a
   recording, with garbage or a stale arm in RAM; a pattern already playing silently
   starts again from its start. Stock: Shift+Rec is plain Rec. Writes and cost.
"""
from __future__ import annotations

import random
import struct
import sys
from pathlib import Path

import numpy as np
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patch_ptf import bl_target  # noqa: E402
from layout import patch_code, patch_ram  # noqa: E402
import synth_rig as sr  # noqa: E402
from machine import STACK_TOP  # noqa: E402

ENGINE = sr.ENGINE
REC = 0x20027F28                    # the recorder object
SCR = 0x20026100                    # the Sample Recorder screen
SCR_SOURCE = 0x78                   # 0 Line In, 1 Radio, 2 Mic LG, 3 Mic HG, 4 USB
REC_START = 0x600F9074              # start recording (screen method)
REC_START_STUBS = (0x600FD210, 0x600FC998, 0x600F8E3C, 0x60119414)   # its screen/UI calls
BUF_SITE = 0x600F90D2
REC_STOP, REC_CHANNEL, REC_LENGTH = 0x6010C944, 0x6010CA68, 0x6010CA8C
REC_FRAMES = 0x20027F24             # frames per channel the recorder has room for
EBU = 0x41C8                        # engine_block_update (ITCM)
OUT = ENGINE + 0x24D4               # the output sink: DMA buffer pointers at +0x24/+0x28
CODEC_IN = ENGINE + 0x98            # the codec input object: interleaved block at +0x14
STEMS_SINK = 0x20026EC8             # set after boot; the stems mixer only runs with it
TIMERS = (0x20027D3C, 0x20027D18, 0x20027CFC)
BANK, BANK_SIZE = 0x8026A000, 0x03C00000
BANK_TOTAL, BANK_USED = 0x20204F44, 0x20204F48
SPRINTF, FMT_LITERAL = 0x60167194, 0x600CBF9C
STACK = (STACK_TOP - 0x10000, STACK_TOP)
RS_MAGIC = 0x5352544D
RS_OPENED = 5                       # the "gates open" byte in the patch's RAM
MIN_BYTES_PER_CH = 10 * 44100 * 2   # 10 seconds of 16-bit samples
PLAY_WORD = 0x600FA4AC              # pool word: the recorder's Play handler (0x600d07b5)
STOCK_PLAY = 0x600D07B4
REC_WORD = 0x600F9000               # pool word: the recorder's Rec handler (0x600d0621)
QUEUES_SITE = 0x600F90D8            # the record start's bl 0x6010c890 (the record queues' start)
RS_ARMED = 6                        # the "start the pattern with the recording" byte
PREROLL = 0x2002061C + 0xE7         # settings: Pre Roll (1 = on), 0x600ad400
COUNTIN_DONE_CB = 0x600F8605        # seq+0x6fc, set by the recorder's start
SCR_ACTIVE, SCR_COUNTIN_DONE = 0xB4D, 0xBF8
UPDATE_WORD = 0x60042C54            # the Sample Recorder's vtable: update (0x600fafe9)
STOCK_UPDATE = 0x600FAFE8
IS_REC_WORD = 0x601ACC28            # ITCM image: the literal of the thunk 0x1f540 (rec+0x2c)
IS_REC_ITCM, IS_REC_THUNK = 0x1F544, 0x1F540
SHIFT_HELD = 0x600CF90C             # (buttons, 0x1d): the Play handler's Shift test
SEQ = 0x20017274
SEQ_TICK = 0x35E8
FW_SEQ_STOP = 0x600ACD90            # (seq): the Play handler's stop
SDRAM = 0x80000000
SETTINGS = 0x2002061C               # +0x2c MIDI out
PROJECT = 0x20204000                # +0xa84..: the 11 mix levels
INS, INS_SIZE = 0x20200000, 0x174
SAMPLE_TABLE = INS + 0x4DC4
INS_DEFAULTS = 0x600C1B50           # (_, instrument): clears it and sets a new instrument's defaults
BLOCKED = 0x2001E3A8                # the 48 sample instruments' "blocked" flags
PLAYERS = [0x2001A138 + 0x848 * t for t in range(8)]
TICKS_PER_BLOCK = 48
ENTRY_CALLS = ((0x600B39CC,), (0x600AD600, SEQ, 1), (0x600B2DD8,), (0x600B4058,), (0x600B40C0,),
               (0x600B3A88,), (0x600B3440,), (0x600B36DC, ENGINE, 0))    # 0x600fa228's engine calls

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Rig:
    """One firmware (patched or stock): the audio engine, the recorder, a codec input."""

    def __init__(self, ptf: Path, patched: bool):
        self.s = s = sr.SynthRig(ptf)
        self.m = m = s.m
        self.patched = patched
        self.ram = patch_ram(ptf, "resample") if patched else None
        self.code = patch_code(ptf, "resample") if patched else None
        for ptr in TIMERS:                  # boot-time timer objects the engine's code reads
            if m.r32(ptr) == 0 or m.r32(m.r32(ptr)) == 0:
                obj, vt = m.alloc(0x10), m.alloc(0x20)
                m.w32(obj, vt)
                for k in range(8):
                    m.w32(vt + 4 * k, s.add_stub(lambda mm: 1000))
                m.w32(ptr, obj)
        sink, vt = m.alloc(0x10), m.alloc(0x20)
        m.w32(sink, vt)
        for k in range(8):
            m.w32(vt + 4 * k, s.add_stub(lambda mm: 0))
        m.w32(STEMS_SINK, sink)
        bl, br = m.alloc(256), m.alloc(256)
        m.w32(OUT + 0x24, bl)
        m.w32(OUT + 0x28, br)
        m.w32(OUT + 0x2C, 0)
        m.w32(OUT + 0x30, 0)
        self.inbuf = m.alloc(128 * 4)
        m.write(self.inbuf, b"".join(struct.pack("<hh", 1000, -1000) for _ in range(128)))
        for a in REC_START_STUBS:
            m.stub_function(a, lambda mm: 0)
        m.w32(BANK_TOTAL, BANK_SIZE)

    def start(self, used: int, source: int = 0, total: int = BANK_SIZE) -> tuple[int, int, int]:
        """Start a recording with the bank `used` bytes full; (ch0, ch1, frames)."""
        m = self.m
        m.w32(BANK_USED, used)
        m.w32(BANK_TOTAL, total)
        m.w8(SCR + SCR_SOURCE, source)
        m.call(REC_START, SCR, max_insns=5_000_000)
        return m.call(REC_CHANNEL, REC, 0), m.call(REC_CHANNEL, REC, 1), m.r32(REC_FRAMES)

    def stop(self) -> None:
        self.m.call(REC_STOP, REC, max_insns=5_000_000)
        self.m.w32(BANK_TOTAL, BANK_SIZE)

    def block(self) -> None:
        m = self.m
        m.w32(CODEC_IN + 0x14, self.inbuf)
        m.call(EBU, ENGINE, max_insns=50_000_000)
        m.call(sr.GRAPH_UPDATE, max_insns=50_000_000)

    def moved(self) -> int:
        return self.m.uc.mem_read(self.ram + 4, 1)[0]


def expected(used: int, ch: int, total: int = BANK_SIZE) -> tuple[int, int, int]:
    """(buffer, size, moved flag) the plan gives the recorder. Nothing loaded is a move
    by zero bytes: stock's buffer, with the flag set."""
    if total == BANK_SIZE and used <= BANK_SIZE - 32:
        off = (used + 31) & ~31
        free = (BANK_SIZE - off) & ~255
        if free >= MIN_BYTES_PER_CH * ch:
            return BANK + off, free, 1
    return BANK, BANK_SIZE, 0


# ---- 1. sites and version -----------------------------------------------------------

def site_checks(p: Rig) -> None:
    print("sites and version")
    lo, hi = p.code
    t = bl_target(p.m.image, 0x60030000, BUF_SITE)
    check(lo <= t < hi, f"the record start's buffer call ({BUF_SITE:#x}) branches into the patch ({t:#x})")
    for at, what in ((PLAY_WORD, "the Play handler's pool word"), (UPDATE_WORD, "the recorder's update slot"),
                     (IS_REC_WORD, "the ITCM image's 'recording?' word")):
        w = struct.unpack_from("<I", p.m.image, at - 0x60030000)[0]
        check(lo <= (w & ~1) < hi and w & 1, f"{what} ({at:#x}) points into the patch ({w:#x})")
    t = bl_target(p.m.image, 0x60030000, QUEUES_SITE)
    check(lo <= t < hi, f"the record start's queue start ({QUEUES_SITE:#x}) branches into the patch ({t:#x})")
    w = struct.unpack_from("<I", p.m.image, REC_WORD - 0x60030000)[0]
    check(lo <= (w & ~1) < hi and w & 1, f"the Rec handler's pool word ({REC_WORD:#x}) points into the patch ({w:#x})")
    w = p.m.r32(IS_REC_ITCM)
    check(lo <= (w & ~1) < hi, f"after boot, ITCM {IS_REC_ITCM:#x} (the thunk's literal) holds it ({w:#x})")
    m = p.m
    buf = m.alloc(64)
    m.call(SPRINTF, buf, m.r32(FMT_LITERAL), 1, 2, 0)
    text = bytes(m.uc.mem_read(buf, 64)).split(b"\0")[0].decode()
    check(text.startswith("1.2.0-") and len(text) <= 9, f"version text {text!r}")


# ---- 2. placement -------------------------------------------------------------------

STEREO_MIN = MIN_BYTES_PER_CH * 2
CASES = [  # (label, used, source, bank total)
    ("nothing loaded", 0, 0, BANK_SIZE),
    ("1 byte used", 1, 0, BANK_SIZE),
    ("32 bytes", 32, 0, BANK_SIZE),
    ("1,000,002 bytes", 1_000_002, 0, BANK_SIZE),
    ("30 MB", 30 << 20, 0, BANK_SIZE),
    ("30 MB, Mic LG (mono)", 30 << 20, 2, BANK_SIZE),
    ("30 MB, USB", 30 << 20, 4, BANK_SIZE),
    ("10 s stereo left before the 256-byte rounding", BANK_SIZE - STEREO_MIN, 0, BANK_SIZE),
    ("10 s stereo left after it", BANK_SIZE - STEREO_MIN - 256, 0, BANK_SIZE),
    ("32 bytes short of 10 s stereo", BANK_SIZE - STEREO_MIN + 32, 0, BANK_SIZE),
    ("room for 10 s mono, not stereo: Line In", BANK_SIZE - STEREO_MIN + 0x1000, 0, BANK_SIZE),
    ("room for 10 s mono, not stereo: Mic HG", BANK_SIZE - STEREO_MIN + 0x1000, 3, BANK_SIZE),
    ("bank full", BANK_SIZE, 0, BANK_SIZE),
    ("used past the bank (garbage)", 0xFFFFFFF0, 0, BANK_SIZE),
    ("the bank's size field not the bank's (garbage)", 1 << 20, 0, 0x01000000),
]


def placement_checks(p: Rig, s: Rig) -> None:
    print("placement")
    for label, used, source, total in CASES:
        ch = 1 if source in (2, 3) else 2
        got = p.start(used, source, total)
        moved = p.moved()
        p.stop()
        ref = s.start(used, source, total)
        s.stop()
        buf, size, want_moved = expected(used, ch, total)
        frames = size // (2 * ch)
        want = (buf, buf + 2 * frames if ch == 2 else buf, frames)   # mono: 0x6010ca68 gives channel 0 for both
        check(got == want and moved == want_moved,
              f"{label}: channels at {got[0]:#x} / {got[1]:#x}, {got[2]} frames each, moved {moved} "
              f"(want {want[0]:#x} / {want[1]:#x}, {want[2]}, moved {want_moved})")
        if (buf, size) == (BANK, BANK_SIZE):
            check(got == ref, f"{label}: the same buffer as stock ({ref[0]:#x}, {ref[2]} frames)")
        else:
            end = got[0] + 2 * ch * got[2]
            check(got[0] >= BANK + used and got[0] % 32 == 0 and end <= BANK + BANK_SIZE,
                  f"{label}: above the loaded samples, 32-byte aligned, ends inside the bank ({end:#x})")
        check(ref[0] == BANK and ref[2] == BANK_SIZE // (2 * ch), f"{label}: stock records at the bank's base")


# ---- 3. garbage RAM -----------------------------------------------------------------

def garbage_checks(p: Rig) -> None:
    print("the patch's RAM full of garbage")
    rnd = random.Random(3)
    for k in range(3):
        p.m.write(p.ram, bytes(rnd.randrange(256) for _ in range(16)))
        got = p.start(1 << 20)
        p.stop()
        want = expected(1 << 20, 2)
        check(got[0] == want[0] and p.m.r32(p.ram) == RS_MAGIC and p.moved() == 1,
              f"garbage {k + 1}: initialized, recording at {got[0]:#x}")


# ---- 4. a recording -----------------------------------------------------------------

USED = 0x40000                       # 256 KB of loaded samples
BLOCKS = 40


def record(r: Rig) -> tuple[bool, int, np.ndarray, np.ndarray]:
    m = r.m
    pattern = bytes((k * 7 + 3) & 0xFF for k in range(USED))
    m.write(BANK, pattern)
    ch0, ch1, _ = r.start(USED, 0)
    for _ in range(BLOCKS):
        r.block()
    r.stop()
    n = m.call(REC_LENGTH)
    left = np.frombuffer(bytes(m.uc.mem_read(ch0, 2 * n)), "<i2")
    right = np.frombuffer(bytes(m.uc.mem_read(ch1, 2 * n)), "<i2")
    kept = bytes(m.uc.mem_read(BANK, USED)) == pattern
    return kept, n, left, right


def recording_checks(p: Rig, s: Rig) -> None:
    print("a recording")
    kept, n, left, right = record(p)
    check(kept, f"the {USED // 1024} KB of loaded samples are byte-identical after {BLOCKS} blocks of recording")
    check(n >= (BLOCKS - 2) * 128, f"the recording has {n} frames")
    check(n > 0 and bool(np.all(left == 1000)) and bool(np.all(right == -1000)),
          "it holds the input (left 1000, right -1000)")
    kept_s, n_s, _, _ = record(s)
    check(not kept_s and n_s == n, f"stock: the same recording ({n_s} frames) writes over the loaded samples")


# ---- 5. writes and cost -------------------------------------------------------------

def write_checks(p: Rig) -> None:
    print("writes and cost")
    m = p.m
    lo, hi = p.code
    stray = []
    n = [0]

    def on_write(uc, access, a, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        if lo <= pc < hi and not (p.ram <= a < p.ram + 16 or STACK[0] <= a < STACK[1]):
            stray.append((hex(pc), hex(a)))

    def on_code(uc, a, size, user):
        n[0] += 1
    hw = m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    hc = m.uc.hook_add(UC_HOOK_CODE, on_code, begin=lo, end=hi - 1)
    rnd = random.Random(11)
    worst = 0
    for k in range(40):
        before = n[0]
        p.start(rnd.choice((0, rnd.randrange(BANK_SIZE), rnd.randrange(1 << 32))), rnd.randrange(5))
        worst = max(worst, n[0] - before)
        p.stop()
    m.uc.hook_del(hw)
    m.uc.hook_del(hc)
    check(not stray, f"40 record starts: no write by the patch outside its RAM and the stack {stray[:3]}")
    check(worst <= 80, f"the hook runs at most {worst} instructions of its own per record start")


# ---- 6..10: Play during a recording ---------------------------------------------------

PLAY_USED = 0x100000                 # 1 MB of loaded samples: the recording is moved
SOUND_BLOCKS = 120                   # 0.35 s
SAMPLE_FRAMES = 4410                 # 0.1 s samples: their tails end inside a run
TAIL_BLOCKS = 120                    # after the stop: the releases end well inside this
SETTLE_BLOCKS = 30                   # audio blocks before Play in the settled comparison
SETTLED_MAX_DIFF = 64                # -54 dB of full scale
OPEN_STEPS = ((0x600B39E4,), (0x600AD600, SEQ, 0), (0x600B2DE4,), (0x600B3AE4,), (0x600B4128,))


class Session(Rig):
    """A project in one firmware: two sample tracks, two synths and a MIDI track on step
    0 of a 16-step pattern, sends and mix levels away from their defaults; the
    sequencer ticks TICKS_PER_BLOCK times before every audio block."""

    def __init__(self, ptf: Path, patched: bool, monitor: bool | None = None, inp: tuple[int, int] = (0, 0)):
        super().__init__(ptf, patched)
        m = self.m
        m.w8(SETTINGS + 0x2C, 2)                        # MIDI out: jack
        self.shift = 0
        m.stub_function(SHIFT_HELD, lambda mm: self.shift)
        m.w32(SEQ + 0x6FC, COUNTIN_DONE_CB)             # as the recorder's start sets it
        m.stub_function(STOCK_UPDATE, lambda mm: 0)     # the recorder's own update (screen work)
        m.write(self.inbuf, b"".join(struct.pack("<hh", *inp) for _ in range(128)))
        for i in range(2):
            self.sample_instrument(i)
        for t in range(8):
            m.w8(PROJECT + 0xA84 + t, 70)               # track volumes
        m.w8(PROJECT + 0xA8C, 60)                       # reverb
        m.w8(PROJECT + 0xA8D, 75)                       # delay
        m.w8(PROJECT + 0xA91, 80)                       # dry mix
        for k in (0, 1):                                # the synths' delay and reverb sends
            for t in (1, 2):
                a = ENGINE + 0x1688 + (k * 10 + t) * 32
                m.write(a, struct.pack("<f", 60.0))
                m.w8(a + 4, 1)
        m.w32(ENGINE + 0x14B8 + 0x28, 4410)             # delay time 0.1 s: it returns inside the run
        self.cur, nxt = self.pattern(), self.pattern()
        for track, ins in enumerate((0, 1, 64, 65, 48)):
            m.write(self.cur + 0x1C + track * 0x301 + 1, bytes([60, ins, 0, 0, 0, 0]))
        m.w32(SEQ + 0x14, self.cur)
        m.w32(SEQ + 0x18, nxt)
        if monitor is None:                             # the recorder closed: sends applied as after a load
            self.mark_sends()
        else:
            self.entry(monitor)
        self.ticks = 0

    def pattern(self) -> int:
        data = bytearray(0x3030)
        for t in range(16):
            base = 0x1C + t * 0x301
            data[base] = 15
            for s in range(128):
                data[base + 1 + 6 * s] = 0xFF
        buf = self.m.alloc(len(data))
        self.m.write(buf, bytes(data))
        return buf

    def sample_instrument(self, i: int, frames: int = SAMPLE_FRAMES) -> None:
        m = self.m
        a = INS + INS_SIZE * i
        m.call(INS_DEFAULTS, 0, a)                      # a new instrument (bit depth 16, volume 50...)
        m.w32(a + 0x2C, frames)
        m.w8(a + 0x106, 60)                             # its reverb and delay sends
        m.w8(a + 0x170, 60)
        buf = BANK + 0x40000 * i                        # loaded samples: inside PLAY_USED
        m.write(buf, b"".join(struct.pack("<h", int(8000 * np.sin(k * 0.06 * (i + 1)))) for k in range(frames)))
        m.w32(SAMPLE_TABLE + 8 * i, buf)
        m.w32(SAMPLE_TABLE + 8 * i + 4, buf)
        m.w32(a + 0x28, buf)

    def mark_sends(self) -> None:
        m = self.m
        for pl in PLAYERS:
            m.w32(pl + 0x798, m.r32(pl + 0x798) | 0x100100)
        for k in range(3):
            m.w32(ENGINE + 0x1A48 + 4 * k, m.r32(ENGINE + 0x1A48 + 4 * k) | 0x6)

    def entry(self, monitor: bool) -> None:
        m = self.m
        m.call(0x600AD418, SEQ, 4)                      # seq+0x34 = 4
        for c in ENTRY_CALLS:
            m.call(c[0], *(c[1:] or (ENGINE,)), max_insns=20_000_000)
        m.call(0x600B30EC, ENGINE, 0x56 if monitor else 0)   # line-in level: Monitor

    def open_by_hand(self) -> None:
        """What the patch does on Play, done with the stock calls (stock firmware)."""
        for c in OPEN_STEPS:
            self.m.call(c[0], *(c[1:] or (ENGINE,)), max_insns=20_000_000)
        self.mark_sends()

    def idle(self, n: int) -> None:
        for _ in range(n):
            self.block()

    def play(self) -> None:
        self.m.call(self.m.r32(PLAY_WORD), 0, 0, 0, 0, max_insns=20_000_000)

    def rec_press(self, used: int = PLAY_USED, source: int = 0, shift: int = 0, preroll: int = 0) -> None:
        """Rec through the real handler, as the button dispatch calls it."""
        m = self.m
        m.w32(BANK_USED, used)
        m.w8(SCR + SCR_SOURCE, source)
        m.w8(PREROLL, preroll)
        self.shift = shift
        m.call(m.r32(REC_WORD), 0, 0, 0, 0, max_insns=20_000_000)
        self.shift = 0

    def count_in(self, limit: int = 100_000) -> int:
        """Sequencer ticks until the count-in's end callback; then the record start, as
        the recorder's update calls it then (bl at 0x600fb1fa). Returns the ticks."""
        m = self.m
        n = 0
        while not self.scr(SCR_COUNTIN_DONE) and n < limit:
            m.ticks += 1
            m.call(SEQ_TICK, SEQ, max_insns=5_000_000)
            n += 1
        if self.scr(SCR_COUNTIN_DONE):
            m.call(REC_START, SCR, max_insns=5_000_000)
        return n

    def scr(self, off: int) -> int:
        return self.m.uc.mem_read(SCR + off, 1)[0]

    def armed(self) -> int:
        return self.m.uc.mem_read(self.ram + RS_ARMED, 1)[0] if self.ram else 0

    def recording(self) -> int:
        return self.m.uc.mem_read(REC + 0x2C, 1)[0]

    def update(self) -> None:
        self.m.call(self.m.r32(UPDATE_WORD), SCR, max_insns=20_000_000)

    def run(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        m = self.m
        L, R = [], []
        for _ in range(n):
            for _ in range(TICKS_PER_BLOCK):
                m.ticks += 1
                m.call(SEQ_TICK, SEQ, max_insns=5_000_000)
            self.block()
            L.append(np.frombuffer(bytes(m.uc.mem_read(m.r32(OUT + 0x24), 256)), "<i2").copy())
            R.append(np.frombuffer(bytes(m.uc.mem_read(m.r32(OUT + 0x28), 256)), "<i2").copy())
        return np.concatenate(L), np.concatenate(R)

    def opened(self) -> int:
        return self.m.uc.mem_read(self.ram + RS_OPENED, 1)[0] if self.ram else 0

    def is_recording(self) -> int:
        return self.m.call(IS_REC_THUNK | 1, REC, max_insns=10_000)

    def recorded(self) -> tuple[np.ndarray, np.ndarray]:
        m = self.m
        n = m.call(REC_LENGTH)
        ch0, ch1 = m.call(REC_CHANNEL, REC, 0), m.call(REC_CHANNEL, REC, 1)
        return (np.frombuffer(bytes(m.uc.mem_read(ch0, 2 * n)), "<i2"),
                np.frombuffer(bytes(m.uc.mem_read(ch1, 2 * n)), "<i2"))

    def playing(self) -> bool:
        b = lambda o: self.m.uc.mem_read(SEQ + o, 1)[0]
        return not b(0x2D) and bool(b(0x1D) or b(0x2A))


def peak(*ws: np.ndarray) -> int:
    return max(int(np.abs(w.astype(int)).max()) if len(w) else 0 for w in ws)


def same(a: tuple[np.ndarray, np.ndarray], b: tuple[np.ndarray, np.ndarray]) -> tuple[bool, int]:
    d = max(int(np.abs(x.astype(int) - y.astype(int)).max()) for x, y in zip(a, b))
    return d == 0, d


def entry_writes(stock: Path, monitor: bool, blocks: int, played: int = 0) -> dict[int, int]:
    """Every byte the recorder's entry calls write (outside the stack), with its value
    `blocks` audio blocks later (some are gain ramps the audio objects move every block).
    With `played`, the pattern plays that many blocks and stops just before the entry,
    so the delay and reverb carry the same history as at the end of an engine-on take."""
    s = Session(stock, False)
    s.idle(1)                                       # the project's sends applied, as before any entry
    if played:
        s.play()
        s.run(played)
        s.m.call(FW_SEQ_STOP, SEQ, max_insns=20_000_000)
    m = s.m
    seen: set[int] = set()

    def on_write(uc, access, a, size, value, user):
        if not STACK[0] <= a < STACK[1]:
            seen.update(range(a, a + size))
    h = m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    s.entry(monitor)
    m.uc.hook_del(h)
    s.idle(blocks)
    return {a: m.uc.mem_read(a, 1)[0] for a in sorted(seen)}


def engine_on_checks(ptf: Path, stock: Path) -> None:
    print("Play during a recording")
    ref = Session(stock, False, monitor=False)      # stock: the recorder's entry, the gates opened by hand
    ref.open_by_hand()
    ref.play()
    want = ref.run(SOUND_BLOCKS)
    midi_ref = list(ref.m.events)

    p = Session(ptf, True, monitor=False)
    ch0, _, _ = p.start(PLAY_USED)
    check(ch0 >= BANK + PLAY_USED, f"the recording is moved above the loaded samples ({ch0:#x})")
    check(p.is_recording() == 1 and not p.opened(), "before Play: 'recording?' answers 1, the gates are shut")
    hits = []
    p.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: hits.append(
        (p.m.uc.mem_read(BLOCKED, 48) == bytes(48), p.m.uc.mem_read(SEQ + 0x7DD, 1)[0], p.opened())),
        begin=0x600AC5F8, end=0x600AC5F8)
    p.play()
    check(hits == [(True, 0, 1)], f"Play: the gates are open before the stock handler starts the pattern {hits}")
    check(p.playing(), "the pattern plays")
    check(p.is_recording() == 0, "while the gates are open, engine_block_update's 'recording?' answers 0")
    got = p.run(SOUND_BLOCKS)
    ok, d = same(got, want)
    check(ok and peak(*want) > 5000,
          f"the output is bit-identical to stock given the same steps by hand (peak {peak(*got)}, max diff {d})")
    check([e[1] for e in p.m.events] == [e[1] for e in midi_ref] and len(midi_ref) > 0,
          f"MIDI is sent as in stock ({len(p.m.events)} messages)")
    # against normal playback, both after SETTLE_BLOCKS audio blocks (the synths' oscillators run on)
    normal = Session(stock, False)
    normal.idle(SETTLE_BLOCKS)
    normal.play()
    n_out = normal.run(SOUND_BLOCKS)
    q = Session(ptf, True, monitor=False)
    q.start(PLAY_USED)
    q.idle(SETTLE_BLOCKS)
    q.play()
    q_out = q.run(SOUND_BLOCKS)
    _, d = same(q_out, n_out)
    tail = same(tuple(w[-128 * 40:] for w in q_out), tuple(w[-128 * 40:] for w in n_out))[0]
    check(d <= SETTLED_MAX_DIFF and tail and peak(*n_out) > 5000,
          f"against normal playback with the recorder closed: max diff {d} of peak {peak(*n_out)} while the "
          f"sends come back, then bit-identical")
    left, right = p.recorded()
    check(len(left) >= (SOUND_BLOCKS - 2) * 128 and not left.any() and not right.any(),
          f"the recording ({len(left)} frames) holds the silent input only")
    m = p.m
    buf = m.r32(REC)
    m.w32(REC, buf + 0x100)                            # another recording (the factory test's buffers)
    check(p.is_recording() == 1, "the gates open but the recorder's buffer not the moved one: 'recording?' is stock's")
    m.w32(REC, buf)

    # 8. stop
    print("stopping the recording")
    entry = entry_writes(stock, monitor=False, blocks=TAIL_BLOCKS, played=SOUND_BLOCKS)
    p.stop()
    check(p.opened() == 1 and p.playing(), "right after the stop (before the recorder's update) the engine still runs")
    n_midi = len(p.m.events)
    p.update()
    check(not p.playing(), "the recorder's update stops the pattern")
    check(not p.opened() and p.is_recording() == 0, "the gates are closed; 'recording?' is stock's (0)")
    off = [e[1] for e in p.m.events[n_midi:]]
    check(any("note off" in e for e in off), f"the stop sends the MIDI note-off {off[:2]}")
    n_midi = len(p.m.events)
    after = p.run(TAIL_BLOCKS)
    check(len(p.m.events) == n_midi, "no note starts after the stop")
    check(peak(after[0][-128 * 20:], after[1][-128 * 20:]) == 0,
          f"the releases end, then nothing sounds (peak of the first block after the stop "
          f"{peak(after[0][:128], after[1][:128])}, of the last 20 blocks 0)")
    # SDRAM: the delay and reverb buffers. Bypassed, the delay still writes what is left in
    # its state, in stock too, so those bytes are compared by level, the rest exactly.
    state = {a: v for a, v in entry.items() if a < SDRAM}
    diff = [(hex(a), v, m.uc.mem_read(a, 1)[0]) for a, v in state.items() if m.uc.mem_read(a, 1)[0] != v]
    check(not diff, f"then all {len(state)} bytes of state the recorder's entry wrote are as in stock {TAIL_BLOCKS} "
                    f"blocks after an entry right after the same pattern stopped {diff[:6]}")
    bufs = sorted(a for a in entry if a >= SDRAM and not a & 1)
    level = lambda read: max(abs(struct.unpack("<h", read(a))[0]) for a in bufs)
    ours = level(lambda a: bytes(m.uc.mem_read(a, 2)))
    theirs = level(lambda a: bytes([entry[a], entry.get(a + 1, 0)]))
    check(ours <= max(theirs, SETTLED_MAX_DIFF),
          f"the delay and reverb buffers hold leftovers no larger than stock's (max {ours}, stock {theirs})")
    p.update()
    check(not p.opened() and not p.playing(), "a second update changes nothing")
    p.play()                                            # the crop screen's Play: stock
    check(not p.opened() and p.m.uc.mem_read(BLOCKED, 48) != bytes(48), "Play after the recording: the gates stay shut")


def input_checks(ptf: Path, stock: Path) -> None:
    print("the input (a cable from the output back to the input)")
    base = Session(ptf, True, monitor=False)
    base.start(PLAY_USED)
    base.play()
    silent = base.run(SOUND_BLOCKS)
    p = Session(ptf, True, monitor=False, inp=(1000, -1000))
    p.start(PLAY_USED)
    p.play()
    got = p.run(SOUND_BLOCKS)
    ok, d = same(got, silent)
    check(ok, f"Monitor off: an input of 1000 / -1000 changes nothing at the master (max diff {d}): "
              "no line-in reaches the output, so a cable loop can't feed back")
    left, right = p.recorded()
    check(len(left) > 0 and bool(np.all(left == 1000)) and bool(np.all(right == -1000)),
          f"the recording ({len(left)} frames) holds exactly the input while the pattern plays")
    levels = []
    for ptf_, patched in ((ptf, True), (stock, False)):
        r = Session(ptf_, patched, monitor=True, inp=(1000, -1000))
        for t in range(5):                              # an empty pattern: only the input sounds
            r.m.write(r.cur + 0x1C + t * 0x301 + 1, bytes([0xFF, 0, 0, 0, 0, 0]))
        r.start(PLAY_USED)
        r.play()
        L, R = r.run(40)
        levels.append((int(L[-128:].mean().round()), int(R[-128:].mean().round()), r.opened()))
    (pl, pr, po), (sl, sr_, _) = levels
    check(po == 1 and (pl, pr) == (sl, sr_) and pl != 0,
          f"Monitor on: the input reaches the master as in the stock recorder (left {pl} / right {pr} for 1000 / -1000, "
          f"stock {sl} / {sr_}); a cable loop then feeds back at {abs(pl) / 1000:.2f} per pass")


def stock_cases(ptf: Path, stock: Path) -> None:
    print("stock where the engine isn't asked for")
    outs = []
    for ptf_, patched in ((ptf, True), (stock, False)):
        r = Session(ptf_, patched, monitor=False)
        r.play()                                        # the recorder open, no recording
        outs.append((r.run(30), r.opened(), r.m.uc.mem_read(BLOCKED, 48) != bytes(48), len(r.m.events)))
    (po, popen, pblk, pmidi), (so, _, sblk, smidi) = outs
    check(not popen and pblk and same(po, so)[0] and pmidi == smidi and pmidi > 0,
          f"Play with no recording: the gates stay shut, output and MIDI as stock (peak {peak(*po)}, MIDI {pmidi})")
    r = Session(ptf, True, monitor=False)
    r.start(BANK_SIZE - 0x1000)                         # less than 10 s free: stock's buffer
    r.play()
    o = r.run(30)
    check(not r.opened() and r.is_recording() == 1 and peak(*o) == 0,
          f"Play during a recording that couldn't be moved: the gates stay shut, the engine silent (peak {peak(*o)})")
    outs = []
    for ptf_, patched in ((ptf, True), (stock, False)):
        r = Session(ptf_, patched, monitor=True, inp=(1000, -1000))
        r.start(PLAY_USED)
        outs.append(r.run(40))
    ok, d = same(*outs)
    check(ok and peak(*outs[0]) > 0, f"a recording without Play (Monitor on): the master as stock's (max diff {d})")


def cost_checks(ptf: Path, stock: Path) -> None:
    print("cost and writes")
    counts = []
    for ptf_, patched, monitor in ((stock, False, None), (ptf, True, False)):
        r = Session(ptf_, patched, monitor)
        if patched:
            r.start(PLAY_USED)
        lo, hi = r.code if patched else (0, 0)
        own = [0]
        stray = []
        allowed = [(pl + 0x798, pl + 0x79C) for pl in PLAYERS] + [(ENGINE + 0x1A48, ENGINE + 0x1A54)]
        if patched:
            allowed += [(r.ram, r.ram + 16), STACK]

            def on_write(uc, access, a, size, value, user):
                pc = uc.reg_read(UC_ARM_REG_PC)
                if lo <= pc < hi and not any(x <= a < y for x, y in allowed):
                    stray.append((hex(pc), hex(a)))
            hw = r.m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
        r.play()
        r.run(8)
        n = [0]
        hc = r.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: n.__setitem__(0, n[0] + 1))
        if patched:
            ho = r.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: own.__setitem__(0, own[0] + 1), begin=lo, end=hi - 1)
        blocks = 16
        for _ in range(blocks):
            r.block()
        r.m.uc.hook_del(hc)
        counts.append(n[0] // blocks)
        if patched:
            r.m.uc.hook_del(ho)
            r.stop()
            r.update()
            r.m.uc.hook_del(hw)
            check(not stray, f"Play, 24 blocks and the closing update: no write by the patch outside its RAM, "
                             f"the stack and the send flags {stray[:3]}")
            check(own[0] // blocks <= 20, f"the patch's own instructions per block: {own[0] // blocks}")
    normal, rec = counts
    check(rec <= normal * 1.03, f"a block of the engine-on recording: {rec:,} instructions, normal playback {normal:,} "
                                f"({(rec / normal - 1) * 100:+.1f}%)")


# ---- 11. Shift+Rec ---------------------------------------------------------------------

def shift_rec_checks(ptf: Path, stock: Path) -> None:
    print("Shift+Rec")
    # the reference: Rec, then Play at once (section 6: as stock given the same steps by hand)
    ref = Session(ptf, True, monitor=False)
    ref.start(PLAY_USED)
    ref.play()
    want = ref.run(SOUND_BLOCKS)
    midi_ref = [e[1] for e in ref.m.events]

    p = Session(ptf, True, monitor=False)
    p.rec_press()
    check(p.recording() and not p.armed() and not p.opened() and not p.playing(),
          "plain Rec: it records, nothing armed, the gates shut, the pattern stopped")
    out = p.run(20)
    check(peak(*out) == 0 and not p.m.events, f"plain Rec: the master silent, no MIDI (peak {peak(*out)})")

    for label, source, preroll in (("Shift+Rec, Pre Roll off", 0, 0), ("Shift+Rec, Pre Roll on, Radio (no count-in)", 1, 1)):
        p = Session(ptf, True, monitor=False)
        p.rec_press(source=source, shift=1, preroll=preroll)
        check(p.recording() and p.opened() and p.playing() and not p.armed(),
              f"{label}: the recording starts with the gates open and the pattern playing")
        got = p.run(SOUND_BLOCKS)
        ok, d = same(got, want)
        check(ok and [e[1] for e in p.m.events] == midi_ref,
              f"{label}: output and MIDI as Rec then Play at once (max diff {d}, MIDI {len(p.m.events)})")

    p = Session(ptf, True, monitor=False)
    p.rec_press(shift=1, preroll=1)
    check(p.scr(SCR_ACTIVE) and not p.recording() and p.armed() and not p.opened() and not p.playing(),
          "Shift+Rec, Pre Roll on: the count-in runs, armed, the gates shut, the pattern stopped")
    p.update()
    check(p.armed(), "the recorder's update during the count-in keeps it armed")
    ticks = p.count_in()
    check(p.recording() and p.opened() and p.playing() and not p.armed() and not p.m.events,
          f"at the count-in's end ({ticks} ticks) the recording starts with the pattern, no note before it")
    t0 = p.m.ticks
    got = p.run(SOUND_BLOCKS)
    first = [e[0] - t0 for e in p.m.events if "note on" in e[1]][:1]
    check(bool(first) and 0 < first[0] <= TICKS_PER_BLOCK,
          f"the first note-on comes in the recording's first block ({first[0] if first else None} ticks after its start)")
    ok, d = same(got, want)
    check(ok and [e[1] for e in p.m.events] == midi_ref,
          f"after the count-in: output and MIDI as Rec then Play at once (max diff {d})")

    p = Session(ptf, True, monitor=False)
    calls = []
    p.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: calls.append(a), begin=STOCK_PLAY, end=STOCK_PLAY)
    p.rec_press(preroll=1)
    check(p.scr(SCR_ACTIVE) and not p.armed(), "plain Rec, Pre Roll on: the count-in runs, not armed")
    p.play()
    check(p.armed() and not p.playing() and not calls,
          "Play during the count-in: armed, the stock handler not called, the pattern stopped")
    ticks = p.count_in()
    check(p.recording() and p.opened() and p.playing(), f"at the count-in's end ({ticks} ticks) the pattern starts")
    got = p.run(SOUND_BLOCKS)
    ok, d = same(got, want)
    check(ok, f"Play during the count-in: output as Rec then Play at once (max diff {d})")

    # a second Rec during the count-in: stock lets the count-in run on (hardware too)
    s = Session(stock, False, monitor=False)
    s.rec_press(preroll=1)
    s.rec_press(preroll=1)
    ticks = s.count_in()
    check(s.recording() and not s.playing(), f"stock: a second Rec during the count-in, it still records at its end ({ticks} ticks)")
    for label, first, second, wanted in (("Shift+Rec, then plain Rec", 1, 0, True),
                                         ("plain Rec, then Shift+Rec", 0, 1, True),
                                         ("plain Rec, then plain Rec", 0, 0, False)):
        p = Session(ptf, True, monitor=False)
        p.rec_press(shift=first, preroll=1)
        p.rec_press(shift=second, preroll=1)
        check(p.scr(SCR_ACTIVE) and not p.recording() and bool(p.armed()) == wanted and not p.playing(),
              f"{label} during the count-in: the count-in runs on, {'armed' if wanted else 'not armed'}")
        p.update()
        ticks = p.count_in()
        out = p.run(SOUND_BLOCKS)
        if wanted:
            ok, d = same(out, want)
            check(p.recording() and p.opened() and p.playing() and ok,
                  f"... at its end ({ticks} ticks) the pattern starts with the recording (max diff {d})")
        else:
            check(p.recording() and not p.opened() and not p.playing() and peak(*out) == 0,
                  f"... at its end ({ticks} ticks) it records, no pattern, the master silent")

    p = Session(ptf, True, monitor=False)
    p.rec_press(used=BANK_SIZE, shift=1)
    out = p.run(20)
    check(p.recording() and not p.moved() and not p.armed() and not p.opened() and not p.playing() and peak(*out) == 0,
          "Shift+Rec, the bank full (stock's buffer): no pattern, the gates shut, the master silent")

    p = Session(ptf, True, monitor=False)
    p.rec_press(shift=1, preroll=1)
    p.m.w8(SCR + SCR_ACTIVE, 0)                     # the count-in ended without a recording
    p.update()
    check(not p.armed(), "a count-in that ends without a recording: the update disarms")
    p.rec_press()
    check(p.recording() and not p.playing() and not p.opened(), "then plain Rec: no pattern")

    rnd = random.Random(5)
    for k in range(3):
        p = Session(ptf, True, monitor=False)
        p.m.write(p.ram, bytes(rnd.randrange(256) for _ in range(16)))
        p.rec_press()
        check(p.recording() and not p.playing() and not p.opened(), f"garbage RAM {k + 1}, plain Rec: no pattern")
    p = Session(ptf, True, monitor=False)
    p.rec_press(used=BANK_SIZE, shift=1, preroll=1)  # armed, then left before the end
    p.m.w8(SCR + SCR_ACTIVE, 0)
    p.m.w8(SCR + SCR_COUNTIN_DONE, 0)
    p.m.w8(p.ram + RS_ARMED, 1)                     # still armed (no update ran)
    p.rec_press()
    check(p.recording() and not p.playing() and not p.opened(), "a stale arm, then plain Rec: no pattern")

    p = Session(ptf, True, monitor=False)
    p.play()                                        # stock: the pattern plays silently in the setup
    p.run(10)
    n = len(p.m.events)
    p.rec_press(shift=1)
    check(p.recording() and p.opened() and p.playing(), "Shift+Rec over a silently playing pattern: gates open, playing")
    t0 = p.m.ticks
    out = p.run(SOUND_BLOCKS)
    starts = [e[0] - t0 for e in p.m.events[n:] if "note on" in e[1]]
    check(peak(*out) > 5000 and bool(starts) and 0 < starts[0] <= TICKS_PER_BLOCK,
          f"... and the pattern starts again from its start with the recording (peak {peak(*out)}, first "
          f"note-on {starts[0] if starts else None} ticks after)")

    s = Session(stock, False, monitor=False)
    s.rec_press(shift=1)
    out = s.run(20)
    check(s.recording() and not s.playing() and peak(*out) == 0, "stock: Shift+Rec is plain Rec")

    # writes and cost of the two new hooks
    p = Session(ptf, True, monitor=False)
    m = p.m
    lo, hi = p.code
    stray, n_ins = [], [0]
    sends = [(pl + 0x798, pl + 0x79C) for pl in PLAYERS] + [(ENGINE + 0x1A48, ENGINE + 0x1A54)]

    def on_write(uc, access, a, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        if lo <= pc < hi and not (p.ram <= a < p.ram + 16 or STACK[0] <= a < STACK[1]
                                  or any(x <= a < y for x, y in sends)):
            stray.append((hex(pc), hex(a)))

    def on_code(uc, a, size, user):
        n_ins[0] += 1
    hw = m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    hc = m.uc.hook_add(UC_HOOK_CODE, on_code, begin=lo, end=hi - 1)
    p.rec_press(shift=1, preroll=1)
    p.update()
    p.count_in()
    m.uc.hook_del(hw)
    m.uc.hook_del(hc)
    check(not stray, f"Shift+Rec with a count-in: no write by the patch outside its RAM, the stack and the send flags {stray[:3]}")
    check(n_ins[0] <= 400, f"the patch's own instructions for it: {n_ins[0]}")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    p, s = Rig(ptf, True), Rig(stock, False)
    site_checks(p)
    placement_checks(p, s)
    garbage_checks(p)
    recording_checks(p, s)
    write_checks(p)
    del p, s
    engine_on_checks(ptf, stock)
    input_checks(ptf, stock)
    stock_cases(ptf, stock)
    cost_checks(ptf, stock)
    shift_rec_checks(ptf, stock)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
