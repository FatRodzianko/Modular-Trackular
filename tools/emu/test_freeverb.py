"""Check the freeverb patch (Freeverb as the master reverb) and render it next to the stock reverb.

  python tools/emu/test_freeverb.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf> [<wav folder>]

Works with the freeverb-only build and with bundles that include it.

The reverb object (0x20019014) and its stock buffers come from the firmware's static
constructors. The test calls the object's update() through its vtable, as the audio
graph does, with the block helpers stubbed: receive-writable hands it two prepared
blocks (left, right send), transmit and release are recorded.

1. Hook and block handling: the vtable slot points into the patch; with the bypass flag
   set nothing is received or sent; with no left block nothing is sent, and with no
   right block the left one is released; otherwise both blocks are transmitted (left to
   output 0, right to output 1) and both released, as the stock update() does.
2. Freeverb: the stock buffers, filled with noise first, are cleared on first use
   (silence in, silence out); amount 0 returns the send unchanged; an impulse gives a
   decaying tail with no clipping, longer with a larger Size; more Damping takes high
   frequencies out of the tail; Predelay moves the reverb later by its number of
   samples; Diffusion changes the tail; the level field (+0x70) follows the output.
3. Level and cost: a burst of noise through the stock reverb and Freeverb at the same
   settings, tail loudness within 6 dB of each other; instructions per block for both
   (Freeverb under 90,000).
4. WAV files (if a folder is given): the same test sounds through the stock reverb and
   Freeverb, default settings and a large room, 44.1 kHz stereo, return only.
"""
from __future__ import annotations

import json
import math
import random
import struct
import sys
import wave
from pathlib import Path

from unicorn import UC_HOOK_CODE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seq_timing import make  # noqa: E402

REVERB = 0x20019014
RECEIVE_WRITABLE, TRANSMIT, RELEASE = 0x600305D4, 0x60030574, 0x60030524
BUF_L, BUF_R = 0x24, 0x28
AMOUNT, INPUT_GAIN, SIZE, DIFFUSION, LP, PREDELAY, LEVEL, BYPASS = 0x4C, 0x50, 0x54, 0x58, 0x5C, 0x6C, 0x70, 0x80
BLOCK = 128
RATE = 44100

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Rig:
    """The reverb object of one firmware, driven block by block."""

    def __init__(self, ptf: Path):
        self.m = m = make(ptf)
        self.blocks = [m.alloc(4 + 2 * BLOCK, 4), m.alloc(4 + 2 * BLOCK, 4)]
        self.give = [True, True]
        self.sent: list[tuple[int, int]] = []
        self.released: list[int] = []
        self.received = 0

        def receive(mm):
            i = mm.arg(1)
            self.received += 1
            return self.blocks[i] if i < 2 and self.give[i] else 0
        m.stub_function(RECEIVE_WRITABLE, receive)
        m.stub_function(TRANSMIT, lambda mm: self.sent.append((mm.arg(1), mm.arg(2))) or 0)
        m.stub_function(RELEASE, lambda mm: self.released.append(mm.arg(0)) or 0)
        self.update = m.r32(m.r32(REVERB))
        self.counting = False
        self.count = 0
        self.settings(bypass=0)

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


def main() -> int:
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    wav_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    code = next(q for q in json.loads(ptf.with_suffix(".map.json").read_text())["patches"] if q["name"] == "freeverb")
    lo, hi = (int(x, 16) for x in code["code"])

    print("hook and block handling")
    r = Rig(ptf)
    check(lo <= r.update < hi and r.update & 1, f"the reverb's update() is the patch's ({r.update:#x})")
    r.settings(bypass=1)
    r.received = 0
    r.run([0] * BLOCK, [0] * BLOCK)
    check(r.received == 0 and not r.sent, "bypass set: nothing received or sent")
    r.settings()
    r.give = [False, True]
    r.run([0] * BLOCK, [0] * BLOCK)
    check(not r.sent, "no left block: nothing sent")
    r.give = [True, False]
    r.released.clear()
    r.run([0] * BLOCK, [0] * BLOCK)
    check(not r.sent and r.released == [r.blocks[0]], "no right block: nothing sent, the left block released")
    r.give = [True, True]
    r.released.clear()
    r.run([0] * BLOCK, [0] * BLOCK)
    check([o for _, o in r.sent] == [0, 1] and [b for b, _ in r.sent] == r.blocks and sorted(r.released) == sorted(r.blocks),
          "both blocks: left to output 0, right to output 1, both released")

    print("Freeverb")
    r = Rig(ptf)
    rnd = random.Random(7)
    for off in (BUF_L, BUF_R):                       # garbage in the stock buffers
        buf = r.m.r32(REVERB + off)
        r.m.write(buf, bytes(rnd.randrange(256) for _ in range(32768)))
    out_l, out_r = r.run([0] * (BLOCK * 20), [0] * (BLOCK * 20))
    check(not any(out_l) and not any(out_r), "the stock buffers are cleared before first use: silence in, silence out")
    r.settings(amount=0.0)
    sig = noise_burst(BLOCK * 8, BLOCK * 8)
    out_l, out_r = r.run(sig, sig)
    check(out_l == sig and out_r == sig, "amount 0: the send comes back unchanged")

    def tail(size=0.5, damping=0.5, predelay=0.0, diffusion=0.625, seconds=4.0):
        rr = Rig(ptf)
        rr.settings(size=size, damping=damping, predelay=predelay, diffusion=diffusion)
        n = int(seconds * RATE) // BLOCK * BLOCK
        left, right = rr.run(impulse(n), impulse(n))
        return rr, left, right
    r1, l_small, r_small = tail(size=0.2)
    r2, l_big, r_big = tail(size=0.9)
    t_small, t_big = decay_time(l_small, 0), decay_time(l_big, 0)
    check(max(map(abs, l_big + r_big)) < 32767 and rms(l_big[RATE:2 * RATE]) > 0,
          f"impulse: a tail without clipping (peak {max(map(abs, l_big + r_big))})")
    check(t_big > t_small * 1.5, f"a larger Size decays more slowly (40 dB in {t_small:.2f} s at 0.2, {t_big:.2f} s at 0.9)")
    check(l_big != r_big, "the two channels differ (stereo spread)")
    _, l_bright, _ = tail(damping=0.0, seconds=2.0)
    _, l_dark, _ = tail(damping=1.0, seconds=2.0)
    hb, hd = hf_ratio(l_bright[RATE // 4:]), hf_ratio(l_dark[RATE // 4:])
    check(hd < hb * 0.8, f"more Damping, fewer high frequencies in the tail ({hb:.3f} -> {hd:.3f})")
    _, l_pre0, _ = tail(predelay=0.0, seconds=1.0)
    _, l_pre, _ = tail(predelay=0.5, seconds=1.0)
    wet0 = [v for v in l_pre0[1:]]
    wet1 = [v for v in l_pre[1:]]
    on0 = next(i for i, v in enumerate(wet0) if abs(v) > 20)
    on1 = next(i for i, v in enumerate(wet1) if abs(v) > 20)
    check(abs((on1 - on0) - 2500) <= 2, f"Predelay 0.5 moves the reverb {on1 - on0} samples later (2500 expected)")
    _, l_d0, _ = tail(diffusion=0.0, seconds=1.0)
    _, l_d1, _ = tail(diffusion=0.85, seconds=1.0)
    check(l_d0 != l_d1, "Diffusion changes the tail")
    check(r2.level() >= 0.0 and r1.level() >= 0.0, f"the level field is written ({r2.level():.1f})")
    rr = Rig(ptf)
    rr.run(noise_burst(BLOCK * 16, BLOCK * 16), noise_burst(BLOCK * 16, BLOCK * 16, 2))   # past the first comb
    check(rr.level() > 100, f"the level field follows the output ({rr.level():.0f})")

    print("level against the stock reverb, and cost")
    n = 2 * RATE // BLOCK * BLOCK
    burst_l, burst_r = noise_burst(n, RATE // 10, 3), noise_burst(n, RATE // 10, 4)
    results = {}
    for label, fw in (("stock", stock), ("freeverb", ptf)):
        rr = Rig(fw)
        rr.settings()
        count = [0]
        rr.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: count.__setitem__(0, count[0] + 1))
        left, right = rr.run(burst_l, burst_r)
        results[label] = (left, right, count[0] / (n // BLOCK))
    tail_s = rms(results["stock"][0][RATE // 5:RATE] + results["stock"][1][RATE // 5:RATE])
    tail_f = rms(results["freeverb"][0][RATE // 5:RATE] + results["freeverb"][1][RATE // 5:RATE])
    check(abs(db(tail_f, tail_s)) <= 6.0, f"tail loudness, Freeverb vs stock: {db(tail_f, tail_s):+.1f} dB")
    check(results["freeverb"][2] < 90_000, f"instructions per block: Freeverb {results['freeverb'][2]:.0f}, "
          f"stock {results['stock'][2]:.0f} (both including the stubbed block helpers)")

    if wav_dir:
        wav_dir.mkdir(parents=True, exist_ok=True)
        n = int(5.0 * RATE) // BLOCK * BLOCK
        dry_l, dry_r = plucks(n)
        write_wav(wav_dir / "reverb_send_dry.wav", dry_l, dry_r)
        for label, fw in (("stock", stock), ("freeverb", ptf)):
            for setting, kw in (("default", {}), ("large", {"size": 0.9, "damping": 0.3, "predelay": 0.2})):
                rr = Rig(fw)
                rr.settings(**kw)
                left, right = rr.run(dry_l, dry_r)
                write_wav(wav_dir / f"reverb_{label}_{setting}.wav", left, right)
        print(f"  wrote WAV files to {wav_dir}")
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
