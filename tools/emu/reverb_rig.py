"""The master reverb and delay objects of one firmware in the emulator, driven block by block.

Used by tools/emu/test_masterfx.py, stress_masterfx.py and compare_masterfx.py. The
reverb object (0x20019014) and its stock buffers come from the firmware's static
constructors. Rig calls the object's update() through its vtable, as the audio graph
does, with the block helpers stubbed: receive-writable hands it two prepared blocks
(left, right send), transmit and release are recorded. With `reverb` given, a masterfx
build gets that choice (0 Stock, 1 Freeverb) before the Pattern screen's first update
and the 8 blocks a switch takes; without it, boot() only runs those. DelayRig does the
same for the delay object (0x20018fc0).
"""
from __future__ import annotations

import json
import math
import random
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seq_timing import make  # noqa: E402

REVERB = 0x20019014
RECEIVE_WRITABLE, TRANSMIT, RELEASE = 0x600305D4, 0x60030574, 0x60030524
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
MENU_CTOR, ATEXIT = 0x60118ACC, 0x600A3594
CLEAR_BLOCKS = 8
STOCK_UPDATE = 0x60031E5C
BUF_L, BUF_R = 0x24, 0x28
AMOUNT, INPUT_GAIN, SIZE, DIFFUSION, LP, PREDELAY, LEVEL, BYPASS = 0x4C, 0x50, 0x54, 0x58, 0x5C, 0x6C, 0x70, 0x80
BLOCK = 128
RATE = 44100
MX_CHOICE = 8                       # masterfx's state + this: the reverb choice
MAP_MARKER = 0x50414D52             # "RMAP": masterfx's table of where its state is
MAP_NAMES = ("mx", "fv", "tape", "plate", "spring", "bbd", "shimmer", "gated")  # the table's entries, in order


def ram_map(m, ptf: Path) -> dict[str, int]:
    """Where masterfx's state is in the firmware built at `ptf` (loaded in machine m): its
    code holds a marker followed by the address of each part (mt_mfx_ram_map), which the
    patcher relocated with the rest."""
    patches = json.loads(Path(ptf).with_suffix(".map.json").read_text())["patches"]
    lo, hi = (int(x, 16) for x in next(p for p in patches if p["name"] == "masterfx")["code"])
    code = bytes(m.uc.mem_read(lo, hi - lo))
    for i in range(0, len(code) - 4 * (len(MAP_NAMES) + 1), 4):
        if struct.unpack_from("<I", code, i)[0] == MAP_MARKER:
            return dict(zip(MAP_NAMES, struct.unpack_from(f"<{len(MAP_NAMES)}I", code, i + 4)))
    raise SystemExit(f"{ptf}: masterfx's RAM map not found")


class Rig:
    """The reverb object of one firmware, driven block by block."""

    def __init__(self, ptf: Path, booted: bool = True, reverb: int | None = None):
        self.m = m = make(ptf)
        self.mx = ram_map(m, ptf)["mx"] if reverb is not None else None
        self.blocks = [m.alloc(4 + 2 * BLOCK, 4), m.alloc(4 + 2 * BLOCK, 4)]
        self.give = [True, True]
        self.sent: list[tuple[int, int]] = []
        self.released: list[int] = []
        self.received = 0
        self.received_blocks: list[int] = []

        def receive(mm):
            i = mm.arg(1)
            self.received += 1
            if i < 2 and self.give[i]:
                self.received_blocks.append(self.blocks[i])
                return self.blocks[i]
            return 0
        m.stub_function(RECEIVE_WRITABLE, receive)
        m.stub_function(TRANSMIT, lambda mm: self.sent.append((mm.arg(1), mm.arg(2))) or 0)
        m.stub_function(RELEASE, lambda mm: self.released.append(mm.arg(0)) or 0)
        self.update = m.r32(m.r32(REVERB))
        m.stub_function(PATTERN_UPDATE, lambda mm: 0)
        m.stub_function(ATEXIT, lambda mm: 0)
        self.settings(bypass=0)
        if reverb is not None:
            self.choose(reverb)
        if booted:
            self.boot()

    def choose(self, reverb: int) -> None:
        """masterfx: the project's reverb choice (0 Stock, 1 Freeverb)."""
        self.m.w8(self.mx + MX_CHOICE, reverb)

    def boot(self) -> None:
        """The Pattern screen updates once (the unit has booted), then the 8 blocks of a switch."""
        self.m.call(self.m.r32(PATTERN_UPDATE_SLOT), 0x20021770)
        self.run([0] * (BLOCK * CLEAR_BLOCKS), [0] * (BLOCK * CLEAR_BLOCKS))

    def f32(self, off: int, v: float) -> None:
        self.m.write(REVERB + off, struct.pack("<f", v))

    def settings(self, size=0.5, damping=0.5, predelay=0.0, diffusion=0.625, amount=0.8, gain=0.1, bypass=0):
        """As the Master Reverb setters store them (Damping as 1 - damping, Predelay x 5000)."""
        self.f32(SIZE, size)
        self.f32(LP, 1.0 - damping)
        self.f32(DIFFUSION, diffusion)
        self.f32(AMOUNT, amount)
        self.f32(INPUT_GAIN, gain)
        self.m.w32(REVERB + PREDELAY, int(predelay * 5000))
        self.m.w8(REVERB + BYPASS, bypass)

    def run(self, left: list[int], right: list[int]) -> tuple[list[int], list[int]]:
        """Process a signal block by block; returns the transmitted left and right."""
        out_l, out_r = [], []
        for b in range(0, len(left), BLOCK):
            for blk, sig in zip(self.blocks, (left, right)):
                chunk = sig[b:b + BLOCK] + [0] * (BLOCK - len(sig[b:b + BLOCK]))
                self.m.write(blk + 4, struct.pack(f"<{BLOCK}h", *chunk))
            self.sent.clear()
            self.m.call(self.update, REVERB, max_insns=5_000_000)
            got = {o: blk for blk, o in self.sent}
            for o, dst in ((0, out_l), (1, out_r)):
                if o in got:
                    dst.extend(struct.unpack(f"<{BLOCK}h", bytes(self.m.uc.mem_read(got[o] + 4, 2 * BLOCK))))
                else:
                    dst.extend([0] * BLOCK)
        return out_l, out_r

    def level(self) -> float:
        return struct.unpack("<f", bytes(self.m.uc.mem_read(REVERB + LEVEL, 4)))[0]


def rms(x: list[int]) -> float:
    return math.sqrt(sum(v * v for v in x) / max(1, len(x)))


def db(a: float, b: float) -> float:
    return 20 * math.log10(max(a, 1e-9) / max(b, 1e-9))


def decay_time(x: list[int], start: int) -> float:
    """Seconds until the tail's 50 ms RMS falls 40 dB below its peak."""
    win = RATE // 20
    levels = [rms(x[i:i + win]) for i in range(start, len(x) - win, win)]
    peak = max(levels)
    for i, lv in enumerate(levels):
        if lv < peak / 100 and i > levels.index(peak):
            return i * win / RATE
    return len(levels) * win / RATE


def hf_ratio(x: list[int]) -> float:
    """High-frequency share of a signal: energy of its first difference over its energy."""
    d = [x[i + 1] - x[i] for i in range(len(x) - 1)]
    return rms(d) / max(rms(x), 1e-9)


def impulse(n: int) -> list[int]:
    return [12000] + [0] * (n - 1)


def noise_burst(n: int, burst: int, seed: int = 1) -> list[int]:
    rnd = random.Random(seed)
    return [int(rnd.uniform(-8000, 8000)) if i < burst else 0 for i in range(n)]


def plucks(n: int) -> tuple[list[int], list[int]]:
    """A few decaying plucked tones and a click, panned, for listening."""
    left, right = [0.0] * n, [0.0] * n
    for t0, f, pan in ((0.0, 220.0, 0.3), (0.5, 330.0, 0.7), (1.0, 440.0, 0.5), (1.5, 165.0, 0.5)):
        s = int(t0 * RATE)
        for i in range(min(RATE // 2, n - s)):
            v = 9000 * math.exp(-i / (RATE * 0.08)) * math.sin(2 * math.pi * f * i / RATE)
            left[s + i] += v * (1 - pan)
            right[s + i] += v * pan
    c = int(2.0 * RATE)
    if c < n:
        left[c] += 12000
        right[c] += 12000
    return [int(v) for v in left], [int(v) for v in right]


def write_wav(path: Path, left: list[int], right: list[int]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<hh", a, b) for a, b in zip(left, right)))


# ---- the master delay --------------------------------------------------------------

DELAY = 0x20018FC0
RECEIVE_READONLY, ALLOCATE = 0x600305BC, 0x60030478
DELAY_STOCK_UPDATE = 0x60032404
SET_FEEDBACK, SET_TIME_MS, SET_FILTER_HZ, SET_PINGPONG = 0x60031F20, 0x60031F80, 0x60032070, 0x60032060
DL_BUFS = (0x14, 0x18, 0x1C)
DL_TIME, DL_FEEDBACK, DL_BYPASS, DL_PINGPONG, DL_POLE = 0x28, 0x2C, 0x2D, 0x2E, 0x38
DL_BUF_BYTES = 2 << 18                  # 2^18 16-bit samples
DL_CLEAR_BLOCKS = 32                    # a switch to Tape clears two buffers, 32 KB per block
DL_STOCK_CLEAR_BLOCKS = 48              # ... a switch to Stock all three
MX_DELAY = 13                           # masterfx's state + this: the delay choice


def _f32_bits(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", v))[0]


class DelayRig:
    """The delay object of one firmware, driven block by block.

    Its three buffers (allocated at runtime on the unit) are given here, cleared. The
    block helpers are stubbed: receive-read-only hands it the mono send block (or none),
    allocate hands it two output blocks (or none), transmit and release are recorded.
    With `delay` given, a masterfx build gets that choice (0 Stock, 1 Tape) before the
    Pattern screen's first update and the blocks a switch to it takes.
    """

    def __init__(self, ptf: Path, booted: bool = True, delay: int | None = None):
        self.m = m = make(ptf)
        self.mx = ram_map(m, ptf)["mx"] if delay is not None else None
        self.bufs = [m.alloc(DL_BUF_BYTES, 4) for _ in DL_BUFS]
        for off, b in zip(DL_BUFS, self.bufs):
            m.w32(DELAY + off, b)
        self.inp = m.alloc(4 + 2 * BLOCK, 4)
        self.outs = [m.alloc(4 + 2 * BLOCK, 4), m.alloc(4 + 2 * BLOCK, 4)]
        self.give_input = True
        self.give_outputs = 2                   # how many allocations succeed per block
        self.sent: list[tuple[int, int]] = []
        self.released: list[int] = []
        self.handed: list[int] = []             # blocks handed out this block
        self._allocs = 0

        def receive(mm):
            if self.give_input:
                self.handed.append(self.inp)
                return self.inp
            return 0

        def allocate(mm):
            if self._allocs < self.give_outputs:
                b = self.outs[self._allocs]
                self._allocs += 1
                self.handed.append(b)
                return b
            return 0
        m.stub_function(RECEIVE_READONLY, receive)
        m.stub_function(ALLOCATE, allocate)
        m.stub_function(TRANSMIT, lambda mm: self.sent.append((mm.arg(1), mm.arg(2))) or 0)
        m.stub_function(RELEASE, lambda mm: self.released.append(mm.arg(0)) or 0)
        m.stub_function(PATTERN_UPDATE, lambda mm: 0)
        m.stub_function(ATEXIT, lambda mm: 0)
        self.update = m.r32(m.r32(DELAY))
        m.w8(DELAY + DL_BYPASS, 0)
        self.settings()
        if delay is not None:
            self.choose(delay)
        if booted:
            self.boot(DL_CLEAR_BLOCKS if delay else 0)

    def choose(self, delay: int) -> None:
        """masterfx: the project's delay choice (0 Stock, 1 Tape)."""
        self.m.w8(self.mx + MX_DELAY, delay)

    def boot(self, blocks: int = DL_CLEAR_BLOCKS) -> None:
        """The Pattern screen updates once (the unit has booted), then `blocks` silent blocks."""
        self.m.call(self.m.r32(PATTERN_UPDATE_SLOT), 0x20021770)
        self.run([0] * (BLOCK * blocks))

    def _setf(self, fn: int, v: float) -> None:
        from unicorn.arm_const import UC_ARM_REG_S0
        self.m.uc.reg_write(UC_ARM_REG_S0, _f32_bits(v))
        self.m.call(fn | 1, DELAY, max_insns=2_000_000)

    def settings(self, time_ms=250.0, feedback=0.5, filter_hz=20000.0, pingpong=0, bypass=None):
        """Through the stock delay's own setters, as the Master Delay page calls them."""
        self._setf(SET_TIME_MS, time_ms)
        self._setf(SET_FEEDBACK, feedback)
        self._setf(SET_FILTER_HZ, filter_hz)
        self.m.call(SET_PINGPONG | 1, DELAY, pingpong)
        if bypass is not None:
            self.m.w8(DELAY + DL_BYPASS, bypass)

    def run(self, sig: list[int]) -> tuple[list[int], list[int]]:
        """Process a mono send block by block; returns the transmitted left and right."""
        out_l, out_r = [], []
        for b in range(0, len(sig), BLOCK):
            chunk = sig[b:b + BLOCK] + [0] * (BLOCK - len(sig[b:b + BLOCK]))
            self.m.write(self.inp + 4, struct.pack(f"<{BLOCK}h", *chunk))
            self.sent.clear()
            self.released.clear()
            self.handed.clear()
            self._allocs = 0
            self.m.call(self.update, DELAY, max_insns=5_000_000)
            got = {o: blk for blk, o in self.sent}
            for o, dst in ((0, out_l), (1, out_r)):
                if o in got:
                    dst.extend(struct.unpack(f"<{BLOCK}h", bytes(self.m.uc.mem_read(got[o] + 4, 2 * BLOCK))))
                else:
                    dst.extend([0] * BLOCK)
        return out_l, out_r
