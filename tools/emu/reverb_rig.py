"""The master reverb object of one firmware in the emulator, driven block by block.

Used by tools/emu/test_masterfx.py, stress_masterfx.py and compare_masterfx.py. The
reverb object (0x20019014) and its stock buffers come from the firmware's static
constructors. Rig calls the object's update() through its vtable, as the audio graph
does, with the block helpers stubbed: receive-writable hands it two prepared blocks
(left, right send), transmit and release are recorded. With `reverb` given, a masterfx
build gets that choice (0 Stock, 1 Freeverb) before the Pattern screen's first update
and the 8 blocks a switch takes; without it, boot() only runs those.
"""
from __future__ import annotations

import math
import random
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout import patch_ram  # noqa: E402
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
MX_OFFSET = 0x120                   # masterfx's RAM: Freeverb's state (288 bytes), then its own
MX_CHOICE = 8                       # + MX_OFFSET: the reverb choice


class Rig:
    """The reverb object of one firmware, driven block by block."""

    def __init__(self, ptf: Path, booted: bool = True, reverb: int | None = None):
        self.m = m = make(ptf)
        self.mx = patch_ram(ptf, "masterfx") + MX_OFFSET if reverb is not None else None
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
