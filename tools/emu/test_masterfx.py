"""Check the masterfx patch (a choice of master reverb and delay per project).

  python tools/emu/test_masterfx.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf> [<wav folder>] [--quick]

--quick runs only what can differ between bundles (start-up, Stock identical to stock
firmware, switching, the Config menu and the settings file) and skips the sound,
delay and cost checks, which depend only on masterfx's own code (tools/run_tests.py).

Works with the masterfx-only build and with bundles that include it. The reverb object
(0x20019014) and the delay object (0x20018fc0) are driven through their vtables as the
audio graph does, with the block helpers stubbed (tools/emu/reverb_rig.py).

1. Start-up and Stock: until the Pattern screen's update has run, the hook gives the stock
   reverb's output, sample for sample, even with Freeverb chosen; with Stock chosen it
   stays identical to stock firmware after that too (300 blocks of changing signals).
2. Switching: choosing Freeverb gives 8 silent blocks (nothing sent) that clear both
   buffers, then Freeverb; choosing Stock again gives 8 silent blocks, zeroes the stock reverb's filter
   states, and then the stock reverb is silent with silence in and its impulse response
   matches a stock-only unit's (within 0.5 dB); a value out of range counts as Stock; the
   start-up reset (the Config menu constructor) brings back the stock reverb until the
   Pattern screen runs again. Switching through all the reverbs, each switch gives 8
   silent blocks that clear the buffers, then the chosen reverb, and the four differ.
3. Freeverb, Plate and Spring, each: with the bypass flag set nothing is received or sent; with no left block
   nothing is sent, and with no right block the left one is released; otherwise both
   blocks are transmitted (left to output 0, right to output 1) and both released, as the
   stock update() does. Amount 0 returns the send unchanged; an impulse gives a decaying
   tail with no clipping, longer with a larger Size; more Damping takes high frequencies
   out of the tail; Predelay moves the reverb later by its number of samples (within
   8 for the Spring, whose springs run at a quarter of the sample rate); Diffusion
   changes the tail; the level field (+0x70) follows the output; a burst of noise gives a
   tail within 6 dB of the stock reverb's at the same settings.
   The delay, as for the reverb: the stock delay before the Pattern screen and with
   Stock chosen (several settings); for Tape and BBD each: the switch's silent blocks
   that clear the lines, echoes at Time and twice Time, each repeat about half the one
   before at Feedback 0.5, the first echo of a tone at the stock delay's level (within
   3 dB), one echo at Feedback 0, sustained echoes and no NaN at Feedback 0.99, PingPong's
   right-then-left echoes, duller echoes with the Filter down, the pitch bend while Time
   changes and the return to pitch, silence after the tail, echoes carrying on without a
   send block, the block handling (bypass, a missing output block); the Tape's wow and
   flutter, the BBD's steady pitch and its duller echoes at longer Times. Back to Stock:
   the buffers cleared, the stock filter states zeroed, a fresh stock delay's impulse
   response; switching between all the delays; the start-up reset.
4. Config menu: the stock menu constructor; Project Settings holds its 10 stock items and
   a "Master FX" folder with a "Reverb" option item (Stock, Freeverb, Plate, Spring) and
   a "Delay" option item (Stock, Tape, BBD) on the patch's choices, starting at Stock;
   the stock Apply stores every value.
5. Settings file: masterfx.bin per project, through the screens' update hooks with a fake
   SD card: default Stock and no file, one write a while after a change, per-project
   values, a damaged file and a file naming an unknown reverb (Stock, and the file is
   kept), a file naming an unknown delay, a project without a name; Save As and new
   projects through the firmware's save request (project_save.save_as_checks).
6. Cost: with Stock chosen, the patch's own instructions per block (what it adds to stock's
   work: test_bundle_cost.py); for each reverb and delay, and for a switch's clearing
   blocks, the whole block.
7. WAV files (if a folder is given): the same test sounds through the stock reverb,
   Freeverb, Plate and Spring, default settings and a large room, 44.1 kHz stereo, return
   only; and through the stock delay, Tape and BBD (a few settings, a Time sweep and
   Feedback at the top), the dry sound with the delay return added.
"""
from __future__ import annotations

import math
import random

import numpy as np
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import reverb_rig as tf  # noqa: E402
from fakefs import PROJECT_NAME, FakeFS  # noqa: E402
from layout import patch_code  # noqa: E402
from machine import Machine  # noqa: E402
from patch_ptf import bl_target  # noqa: E402
from project_save import save_as_checks  # noqa: E402

B = tf.BLOCK
REVERB = tf.REVERB
MX_MAGIC = 0x3558464D
REVERB_STOCK, REVERB_FREEVERB, REVERB_PLATE, REVERB_SPRING, REVERB_SHIMMER = 0, 1, 2, 3, 4
REVERB_NAMES = ["Stock", "Freeverb", "Plate", "Spring", "Shimmer"]
DELAY_STOCK, DELAY_TAPE, DELAY_BBD, DELAY_GATED = 0, 1, 2, 3
DELAY_NAMES = ["Stock", "Tape", "BBD", '"Gated"']
FILTERS = (0x60, 0x64, 0x68)        # the stock reverb's filter states
CFG_MENU_ITEM, CFG_MENU_FOLDER = 0x601183E8, 0x60118394
MENU_CTOR, ATEXIT = 0x60118ACC, 0x600A3594
PROJECT_SETTINGS = 0x20028714
MENU_CURRENT = 0x20028180
MENU_SELECT = 0x60118928
MENU_APPLY, MENU_EDITOR_CLOSE = 0x60118658, 0x600CAB60
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
CONFIG_UPDATE_SLOT, CONFIG_UPDATE = 0x6003B5F8, 0x600C6474
FILE_MAGIC = b"HMF1"
SAVE_DELAY = 25
STOCK_ITEMS = ["Performance presets", "Pattern divider", "Notes scale", "Root note", "Notes layout",
               "Anti-aliasing", "Limiter mode", "Sends mode", "Extra headroom", "Snap to zero"]

MX = 0                              # set in main(): the patch's own state
TAPE = 0                            # ... and the tape's
BBD = 0                             # ... and the BBD's
GATED = 0                           # ... and the gated reverb's
SHIMMER = 0                         # ... and the shimmer's
RV_ACTIVE, DL_ACTIVE = 0x14, 0x24     # masterfx's state + these: the reverb and delay running
CODE = (0, 0)                       # set in main(): the patch's code, start and end
OWN_LIMIT = 3_800                   # own instructions per block: 1 % of a block at about 4 cycles each
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Signals:
    """The same changing test signal for several rigs: noise, silence, impulses, a sine."""

    def __init__(self, seed: int):
        self.rnd = random.Random(seed)

    def blocks(self, n: int) -> tuple[list[int], list[int]]:
        r = self.rnd
        left, right = [], []
        for _ in range(n):
            kind = r.choice(["noise", "noise", "silence", "impulse", "sine"])
            if kind == "noise":
                lv = r.choice([300, 4000, 16000])
                left += [int(r.uniform(-lv, lv)) for _ in range(B)]
                right += [int(r.uniform(-lv, lv)) for _ in range(B)]
            elif kind == "silence":
                left += [0] * B
                right += [0] * B
            elif kind == "impulse":
                s = [0] * B
                s[r.randrange(B)] = 20000
                left += s
                right += s
            else:
                ph = r.random() * 6.283
                s = [int(20000 * math.sin(ph + 0.03 * i)) for i in range(B)]
                left += s
                right += s[::-1]
        return left, right


class Rig(tf.Rig):
    def __init__(self, ptf: Path):
        super().__init__(ptf, booted=False)
        self.mx = MX
        m = self.m
        m.stub_function(CFG_MENU_ITEM, lambda mm: 0)     # the restart hook chains to it
        m.stub_function(CFG_MENU_FOLDER, lambda mm: 0)   # ... and adds its folder
        image = bytes(m.uc.mem_read(0x60000000, 0x200000))
        self.restart_hook = bl_target(image, 0x60000000, 0x601193E6)

    def pattern(self) -> None:
        self.m.call(self.m.r32(tf.PATTERN_UPDATE_SLOT), 0x20021770)

    def restart(self) -> None:
        self.m.call(self.restart_hook | 1, 0, 0, 0, 0, 0, 0)

    def run_counted(self, left, right):
        """run(), also returning how many blocks sent something."""
        sent_blocks = 0
        out_l, out_r = [], []
        for b in range(0, len(left), B):
            ol, orr = self.run(left[b:b + B], right[b:b + B])
            sent_blocks += bool(self.sent)
            out_l += ol
            out_r += orr
        return out_l, out_r, sent_blocks


def same(a, b) -> int:
    """Largest sample difference between two (left, right) outputs."""
    return max([abs(x - y) for x, y in zip(a[0] + a[1], b[0] + b[1])] + [0])


def peak(sig) -> int:
    return max([abs(v) for v in sig[0] + sig[1]] + [0])


def startup_and_stock(ptf: Path, stock: Path) -> None:
    print("start-up and Stock")
    r, s = Rig(ptf), tf.Rig(stock, booted=False)
    check(r.update != tf.STOCK_UPDATE | 1 and r.update & 1, f"reverb update() slot points into the patch ({r.update:#x})")
    sig = Signals(1)
    r.choose(REVERB_FREEVERB)
    x = sig.blocks(40)
    d = same(r.run(*x), s.run(*x))
    check(d == 0, f"before the Pattern screen, Freeverb chosen: the stock reverb's output (largest difference {d})")
    r.choose(REVERB_STOCK)
    r.pattern()
    check(r.m.r32(MX) == MX_MAGIC, "the patch's state is set up")
    x = sig.blocks(300)
    d = same(r.run(*x), s.run(*x))
    check(d == 0, f"booted, Stock chosen: identical to stock firmware over 300 blocks (largest difference {d})")


def switching(ptf: Path, stock: Path) -> None:
    print("switching")
    r = Rig(ptf)
    r.settings(size=0.8)
    r.pattern()
    sig = Signals(2)
    r.run(*sig.blocks(50))
    r.choose(REVERB_FREEVERB)
    x = sig.blocks(8)
    out_l, out_r, sent = r.run_counted(*x)
    check(sent == 0, f"to Freeverb: 8 blocks send nothing while the buffers are cleared ({sent} sent)")
    bl, br = r.m.r32(REVERB + tf.BUF_L), r.m.r32(REVERB + tf.BUF_R)
    clear = bytes(r.m.uc.mem_read(bl, 0x8000)) == bytes(0x8000) and bytes(r.m.uc.mem_read(br, 0x8000)) == bytes(0x8000)
    check(clear, "both buffers are clear")
    s = tf.Rig(stock, booted=False)
    s.settings(size=0.8)
    burst = tf.noise_burst(B * 40, B * 20, 8)
    a, b = r.run(burst, burst), s.run(burst, burst)
    tail = tf.rms(a[0][B * 20:])
    check(a != b and tail > 5, f"then Freeverb: not the stock reverb's output, and a tail (rms {tail:.0f})")
    r.m.write(REVERB + 0x60, struct.pack("<3f", 0.5, -0.25, 0.125))  # as the stock reverb leaves them
    r.choose(REVERB_STOCK)
    out_l, out_r, sent = r.run_counted(*sig.blocks(8))
    filt = [r.m.r32(REVERB + o) for o in FILTERS]
    check(sent == 0 and filt == [0, 0, 0], f"to Stock: 8 silent blocks ({sent} sent), filter states zeroed ({filt})")
    q = r.run([0] * (B * 40), [0] * (B * 40))
    check(peak(q) == 0, f"then the stock reverb: silence in, silence out (peak {peak(q)})")
    r.settings()
    imp = [12000] + [0] * (B * 200 - 1)
    after = r.run(imp, list(imp))
    s = tf.Rig(stock, booted=False)
    s.run(*Signals(2).blocks(50))
    for o in FILTERS:
        s.m.w32(REVERB + o, 0)
    for off in (tf.BUF_L, tf.BUF_R):
        s.m.write(s.m.r32(REVERB + off), bytes(0x8000))
    ref = s.run(imp, list(imp))
    lv = tf.db(tf.rms(after[0] + after[1]), tf.rms(ref[0] + ref[1]))
    check(abs(lv) < 0.5, f"its impulse response matches a stock-only unit's ({lv:+.2f} dB)")

    r.choose(7)
    r.run(*sig.blocks(20))
    out = r.run_counted(*sig.blocks(10))
    check(out[2] == 10 and r.m.uc.mem_read(MX + RV_ACTIVE, 1)[0] == REVERB_STOCK,
          "a value out of range counts as Stock (no switch, the stock reverb runs)")

    r.choose(REVERB_FREEVERB)
    r.run(*sig.blocks(20))
    r.restart()
    x = sig.blocks(30)
    a = r.run(*x)
    check(r.m.uc.mem_read(MX + RV_ACTIVE, 1)[0] == REVERB_STOCK and peak(a) > 0,
          "after the start-up reset: the stock reverb again, Freeverb still chosen")
    r.pattern()
    out = r.run_counted(*sig.blocks(8))
    out2 = r.run_counted(*sig.blocks(4))
    check(out[2] == 0 and out2[2] == 4 and r.m.uc.mem_read(MX + RV_ACTIVE, 1)[0] == REVERB_FREEVERB,
          "and Freeverb once the Pattern screen runs (8 silent blocks first)")


def reverb_checks(ptf: Path, stock: Path, which: int, name: str, pre_tol: int) -> None:
    print(f"{name}: block handling")
    r = tf.Rig(ptf, reverb=which)
    r.settings(bypass=1)
    r.received = 0
    r.run([0] * B, [0] * B)
    check(r.received == 0 and not r.sent, "bypass set: nothing received or sent")
    r.settings()
    r.give = [False, True]
    r.run([0] * B, [0] * B)
    check(not r.sent, "no left block: nothing sent")
    r.give = [True, False]
    r.released.clear()
    r.run([0] * B, [0] * B)
    check(not r.sent and r.released == [r.blocks[0]], "no right block: nothing sent, the left block released")
    r.give = [True, True]
    r.released.clear()
    r.run([0] * B, [0] * B)
    check([o for _, o in r.sent] == [0, 1] and [b for b, _ in r.sent] == r.blocks
          and sorted(r.released) == sorted(r.blocks), "both blocks: left to output 0, right to output 1, both released")

    print(f"{name}: sound")
    r = tf.Rig(ptf, reverb=which)
    r.settings(amount=0.0)
    sig = tf.noise_burst(B * 8, B * 8)
    out_l, out_r = r.run(sig, sig)
    check(out_l == sig and out_r == sig, "amount 0: the send comes back unchanged")

    def tail(size=0.5, damping=0.5, predelay=0.0, diffusion=0.625, seconds=4.0):
        rr = tf.Rig(ptf, reverb=which)
        rr.settings(size=size, damping=damping, predelay=predelay, diffusion=diffusion)
        n = int(seconds * tf.RATE) // B * B
        left, right = rr.run(tf.impulse(n), tf.impulse(n))
        return rr, left, right
    rate = tf.RATE
    r1, l_small, r_small = tail(size=0.2)
    r2, l_big, r_big = tail(size=0.9)
    t_small, t_big = tf.decay_time(l_small, 0), tf.decay_time(l_big, 0)
    check(max(map(abs, l_big + r_big)) < 32767 and tf.rms(l_big[rate:2 * rate]) > 0,
          f"impulse: a tail without clipping (peak {max(map(abs, l_big + r_big))})")
    check(t_big > t_small * 1.5, f"a larger Size decays more slowly (40 dB in {t_small:.2f} s at 0.2, {t_big:.2f} s at 0.9)")
    check(l_big != r_big, "the two channels differ (stereo spread)")
    _, l_bright, _ = tail(damping=0.0, seconds=2.0)
    _, l_dark, _ = tail(damping=1.0, seconds=2.0)
    hb, hd = tf.hf_ratio(l_bright[rate // 4:]), tf.hf_ratio(l_dark[rate // 4:])
    check(hd < hb * 0.8, f"more Damping, fewer high frequencies in the tail ({hb:.3f} -> {hd:.3f})")
    _, l_pre0, _ = tail(predelay=0.0, seconds=1.0)
    _, l_pre, _ = tail(predelay=0.5, seconds=1.0)
    on0 = next(i for i, v in enumerate(l_pre0[1:]) if abs(v) > 20)
    on1 = next(i for i, v in enumerate(l_pre[1:]) if abs(v) > 20)
    check(abs((on1 - on0) - 2500) <= pre_tol,
          f"Predelay 0.5 moves the reverb {on1 - on0} samples later (2500 expected, within {pre_tol})")
    _, l_d0, _ = tail(diffusion=0.0, seconds=1.0)
    _, l_d1, _ = tail(diffusion=0.85, seconds=1.0)
    check(l_d0 != l_d1, "Diffusion changes the tail")
    check(r2.level() >= 0.0 and r1.level() >= 0.0, f"the level field is written ({r2.level():.1f})")
    rr = tf.Rig(ptf, reverb=which)
    rr.run(tf.noise_burst(B * 32, B * 32), tf.noise_burst(B * 32, B * 32, 2))   # past the first echoes
    check(rr.level() > 100, f"the level field follows the output ({rr.level():.0f})")

    n = 2 * rate // B * B
    burst_l, burst_r = tf.noise_burst(n, rate // 10, 3), tf.noise_burst(n, rate // 10, 4)
    outs = {}
    for label, rig in (("stock", tf.Rig(stock)), ("fx", tf.Rig(ptf, reverb=which))):
        rig.settings()
        outs[label] = rig.run(burst_l, burst_r)
    tail_s = tf.rms(outs["stock"][0][rate // 5:rate] + outs["stock"][1][rate // 5:rate])
    tail_f = tf.rms(outs["fx"][0][rate // 5:rate] + outs["fx"][1][rate // 5:rate])
    check(abs(tf.db(tail_f, tail_s)) <= 6.0, f"tail loudness, {name} vs stock: {tf.db(tail_f, tail_s):+.1f} dB")


def reverb_cycle(ptf: Path) -> None:
    print("reverb: switching between all of them")
    r = Rig(ptf)
    r.settings(size=0.7)
    r.pattern()
    sig = Signals(9)
    r.run(*sig.blocks(20))
    seen = {}
    burst = tf.noise_burst(B * 30, B * 10, 12)
    for target in (REVERB_PLATE, REVERB_SPRING, REVERB_SHIMMER, REVERB_FREEVERB, REVERB_SPRING, REVERB_SHIMMER,
                   REVERB_PLATE, REVERB_STOCK):
        r.run(*sig.blocks(10))
        r.choose(target)
        _, _, sent = r.run_counted(*sig.blocks(8))
        bl, br = r.m.r32(REVERB + tf.BUF_L), r.m.r32(REVERB + tf.BUF_R)
        clear = bytes(r.m.uc.mem_read(bl, 0x8000)) == bytes(0x8000) and bytes(r.m.uc.mem_read(br, 0x8000)) == bytes(0x8000)
        out = r.run(burst, burst)
        active = r.m.uc.mem_read(MX + RV_ACTIVE, 1)[0]
        tail = tf.rms(out[0][B * 10:])
        ok = sent == 0 and clear and active == target and tail > 5
        seen.setdefault(target, out)
        check(ok, f"to {REVERB_NAMES[target]}: 8 silent blocks clear the buffers ({sent} sent), then it runs (tail rms {tail:.0f})")
    outs = [seen[k] for k in range(len(REVERB_NAMES))]
    check(all(outs[i] != outs[j] for i in range(len(outs)) for j in range(i + 1, len(outs))),
          f"the {len(outs)} reverbs sound different")


def shimmer_checks(ptf: Path) -> None:
    print("Shimmer: the octaves, and the loop")
    rate = tf.RATE

    def octaves(diffusion):
        r = tf.Rig(ptf, reverb=REVERB_SHIMMER)
        r.settings(size=0.8, diffusion=diffusion)
        n = 3 * rate // B * B
        tone = [int(8000 * math.sin(2 * math.pi * 440 * i / rate)) if i < rate // 2 else 0 for i in range(n)]
        left, _ = r.run(tone, tone)
        tail = left[rate:3 * rate]
        x = np.array(tail, float) * np.hanning(len(tail))
        power = np.abs(np.fft.rfft(x)) ** 2
        freq = np.fft.rfftfreq(len(tail), 1 / rate)
        total = power.sum() or 1.0                          # the share within 5 % of each octave
        return [power[(freq > f * 0.95) & (freq < f * 1.05)].sum() / total for f in (440.0, 880.0, 1760.0)]
    none, full = octaves(0.0), octaves(0.85)
    check(none[1] < 0.02 and full[1] + full[2] > 0.4,
          f"Diffusion 0: a plain plate (octaves up {100 * (none[1] + none[2]):.1f} %); at the top the octaves up "
          f"carry {100 * (full[1] + full[2]):.0f} % of the tail")
    r = tf.Rig(ptf, reverb=REVERB_SHIMMER)
    r.settings(size=1.0, diffusion=0.85, amount=1.0)
    noise = tf.noise_burst(B * 800, B * 800, 3)
    loud, _ = r.run(noise, noise)
    later = [r.run([0] * (B * 344), [0] * (B * 344))[0] for _ in range(12)]
    levels = [tf.rms(x) for x in later]
    floats = struct.unpack("<10f", bytes(r.m.uc.mem_read(SHIMMER + 20, 40)))
    check(max(map(abs, loud)) < 32767 and levels[-1] < levels[0] * 0.5 and all(math.isfinite(v) for v in floats),
          f"Size and shimmer at the top, loud noise: bounded (peak {max(map(abs, loud))}), and the tail dies away "
          f"(rms {levels[0]:.0f} after 1 s, {levels[-1]:.0f} after 12 s)")


def write_wavs(ptf: Path, stock: Path, wav_dir: Path) -> None:
    wav_dir.mkdir(parents=True, exist_ok=True)
    n = int(5.0 * tf.RATE) // B * B
    dry_l, dry_r = tf.plucks(n)
    tf.write_wav(wav_dir / "reverb_send_dry.wav", dry_l, dry_r)
    for label, make_rig in (("stock", lambda: tf.Rig(stock)),
                            ("freeverb", lambda: tf.Rig(ptf, reverb=REVERB_FREEVERB)),
                            ("plate", lambda: tf.Rig(ptf, reverb=REVERB_PLATE)),
                            ("spring", lambda: tf.Rig(ptf, reverb=REVERB_SPRING)),
                            ("shimmer", lambda: tf.Rig(ptf, reverb=REVERB_SHIMMER))):
        for setting, kw in (("default", {}), ("large", {"size": 0.9, "damping": 0.3, "predelay": 0.2})):
            rr = make_rig()
            rr.settings(**kw)
            left, right = rr.run(dry_l, dry_r)
            tf.write_wav(wav_dir / f"reverb_{label}_{setting}.wav", left, right)
    n = int(8.0 * tf.RATE) // B * B
    dry_l, dry_r = tf.plucks(n)
    mono = [(a + b) // 2 for a, b in zip(dry_l, dry_r)]
    # (name, [(seconds, settings), ...]): settings change at those points
    takes = (
        ("default", [(0, {"time_ms": 375.0, "feedback": 0.5})]),
        ("dub", [(0, {"time_ms": 375.0, "feedback": 0.85, "filter_hz": 2500.0})]),
        ("pingpong", [(0, {"time_ms": 250.0, "feedback": 0.6, "pingpong": 1})]),
        ("sweep", [(0, {"time_ms": 300.0, "feedback": 0.8, "filter_hz": 6000.0}),
                   (2.5, {"time_ms": 150.0, "feedback": 0.8, "filter_hz": 6000.0}),
                   (4.5, {"time_ms": 500.0, "feedback": 0.8, "filter_hz": 6000.0})]),
        ("max_feedback", [(0, {"time_ms": 300.0, "feedback": 0.99, "filter_hz": 4000.0})]),
    )
    for label, make_rig in (("stock", lambda: tf.DelayRig(stock)),
                            ("tape", lambda: DRig(ptf, delay=DELAY_TAPE)),
                            ("bbd", lambda: DRig(ptf, delay=DELAY_BBD))):
        for name, changes in takes:
            rr = make_rig()
            rr.settings(**changes[0][1])
            rr.run([0] * (B * 300))                 # settled
            out_l, out_r = [], []
            marks = [int(sec * tf.RATE) // B * B for sec, _ in changes] + [n]
            for (sec, kw), start, stop in zip(changes, marks, marks[1:]):
                rr.settings(**kw)
                ol, orr = rr.run(mono[start:stop])
                out_l += ol
                out_r += orr
            mix_l = [max(-32768, min(32767, d + w)) for d, w in zip(dry_l, out_l)]
            mix_r = [max(-32768, min(32767, d + w)) for d, w in zip(dry_r, out_r)]
            tf.write_wav(wav_dir / f"delay_{label}_{name}.wav", mix_l, mix_r)
    # the gated reverb: on drums, as it is usually heard
    n = int(4.0 * tf.RATE) // B * B
    drums = [0] * n
    beat = int(0.5 * tf.RATE)
    for k, start in enumerate(range(0, n - 4000, beat)):
        if k % 2:                                           # snare: noise and a tone
            r_ = random.Random(k)
            for i in range(3500):
                drums[start + i] += int(math.exp(-i / 700) * (9000 * r_.uniform(-1, 1) + 5000 * math.sin(i * 0.11)))
        else:                                               # kick
            for i in range(4000):
                drums[start + i] += int(16000 * math.exp(-i / 1500) * math.sin(2 * math.pi * (55 + 90 * math.exp(-i / 300)) * i / tf.RATE))
    drums = [max(-32767, min(32767, v)) for v in drums]
    tf.write_wav(wav_dir / "gated_send_dry.wav", drums, drums)
    for name, kw in (("300ms", {"time_ms": 300.0, "feedback": 0.0}),
                     ("300ms_release", {"time_ms": 300.0, "feedback": 0.6}),
                     ("150ms", {"time_ms": 150.0, "feedback": 0.0}),
                     ("reverse", {"time_ms": 400.0, "feedback": 0.0, "pingpong": 1})):
        rr = DRig(ptf, delay=DELAY_GATED)
        rr.settings(**kw)
        rr.run([0] * (B * 20))
        out_l, out_r = rr.run(drums)
        mix_l = [max(-32768, min(32767, d + w)) for d, w in zip(drums, out_l)]
        mix_r = [max(-32768, min(32767, d + w)) for d, w in zip(drums, out_r)]
        tf.write_wav(wav_dir / f"gated_{name}.wav", mix_l, mix_r)
    print(f"  wrote WAV files to {wav_dir}")


# ---- the delay -----------------------------------------------------------------------

DELAY = tf.DELAY
TAPE_MAGIC = 0x45504154
BBD_MAGIC = 0x44444242
GATED_MAGIC = 0x45544147


class DRig(tf.DelayRig):
    def __init__(self, ptf: Path, delay: int | None = None, booted: bool = True):
        super().__init__(ptf, booted=False, delay=delay)
        self.mx = MX
        m = self.m
        m.stub_function(CFG_MENU_ITEM, lambda mm: 0)
        m.stub_function(CFG_MENU_FOLDER, lambda mm: 0)
        image = bytes(m.uc.mem_read(0x60000000, 0x200000))
        self.restart_hook = bl_target(image, 0x60000000, 0x601193E6)
        if booted:
            self.boot(tf.DL_CLEAR_BLOCKS if delay else 0)

    def restart(self) -> None:
        self.m.call(self.restart_hook | 1, 0, 0, 0, 0, 0, 0)

    def active(self) -> int:
        return self.m.uc.mem_read(MX + DL_ACTIVE, 1)[0]

    def run_counted(self, sig):
        sent = 0
        out_l, out_r = [], []
        for b in range(0, len(sig), B):
            ol, orr = self.run(sig[b:b + B])
            sent += bool(self.sent)
            out_l += ol
            out_r += orr
        return out_l, out_r, sent


def peaks(x, thresh=300, gap=400):
    """(index, value) of the separate peaks above thresh."""
    res, last = [], -gap
    for i in range(1, len(x) - 1):
        if abs(x[i]) > thresh and abs(x[i]) >= abs(x[i - 1]) and abs(x[i]) >= abs(x[i + 1]):
            if i - last > gap:
                res.append((i, x[i]))
                last = i
            elif abs(x[i]) > abs(res[-1][1]):
                res[-1] = (i, x[i])
                last = i
    return res


def energy(x, at, half=300):
    return sum(v * v for v in x[max(0, at - half):at + half])


def freq_track(x, start, stop):
    """Frequencies (Hz) of the periods of a sine between start and stop, from rising zero crossings."""
    cross = []
    for i in range(start + 1, stop):
        if x[i - 1] < 0 <= x[i]:
            cross.append(i - 1 + x[i - 1] / (x[i - 1] - x[i]))
    return [tf.RATE / (b - a) for a, b in zip(cross, cross[1:])]


def sine(n, hz=1000.0, amp=12000):
    return [int(amp * math.sin(2 * math.pi * hz * i / tf.RATE)) for i in range(n)]


def delay_effect_checks(ptf: Path, stock: Path, which: int, name: str, state: int, magic: int) -> None:
    print(f"delay: switching to {name}")
    r = DRig(ptf)
    r.m.write(r.bufs[0], bytes(range(256)) * 64)          # something left on the tapes
    r.settings(time_ms=100.0, feedback=0.5)
    r.choose(which)
    out_l, out_r, sent = r.run_counted([3000] * (B * tf.DL_CLEAR_BLOCKS))
    clear = all(bytes(r.m.uc.mem_read(b, tf.DL_BUF_BYTES)) == bytes(tf.DL_BUF_BYTES) for b in r.bufs[:2])
    check(sent == 0 and clear and r.active() == which and r.m.r32(state) == magic,
          f"{tf.DL_CLEAR_BLOCKS} silent blocks ({sent} sent) clear the two lines, then {name}")
    r.run([0] * (B * 200))                                  # past any glide and echoes
    T = 4410
    imp = [20000] + [0] * (B * 80 - 1)
    l, rr_ = r.run(imp)
    pk = peaks(l)
    check(len(pk) >= 2 and abs(pk[0][0] - T) <= 100 and abs(pk[1][0] - 2 * T) <= 200 and l == rr_,
          f"an impulse: echoes at Time and twice Time (within the wow: {[i for i, _ in pk[:3]]}, {T} expected), "
          "the same on both sides")
    ratio = abs(pk[1][1]) / max(1, abs(pk[0][1])) if len(pk) >= 2 else 0
    check(0.3 < ratio < 0.65, f"Feedback 0.5: each repeat about half the one before ({ratio:.2f})")
    burst = [int(8000 * math.sin(2 * math.pi * 440 * i / tf.RATE)) if i < 2205 else 0 for i in range(B * 60)]
    s = tf.DelayRig(stock)
    s.settings(time_ms=100.0, feedback=0.5)
    sl, _ = s.run(burst)
    r.run([0] * (B * 100))
    tl, _ = r.run(burst)
    e_t, e_s = tf.rms(tl[T:T + 2205]), tf.rms(sl[T:T + 2205])
    check(abs(tf.db(e_t, e_s)) < 3.0,
          f"first echo of a 440 Hz tone, level against the stock delay's: {tf.db(e_t, e_s):+.1f} dB")
    r.settings(time_ms=100.0, feedback=0.0)
    r.run([0] * (B * 100))
    l, _ = r.run(imp)
    pk = peaks(l)
    check(len(pk) == 1, f"Feedback 0: one echo ({len(pk)})")
    r.settings(time_ms=100.0, feedback=0.99)
    r.run([0] * (B * 100))
    l, _ = r.run(imp + [0] * (B * 1700))
    late = max(abs(v) for v in l[4 * tf.RATE:5 * tf.RATE])
    floats = struct.unpack("<5f", bytes(r.m.uc.mem_read(state + 20, 20)))
    check(late > 1000 and all(math.isfinite(v) for v in floats),
          f"Feedback 0.99: the echoes sustain (peak {late} after 4 s), held by the saturation, no NaN")
    r.settings(time_ms=100.0, feedback=0.5, pingpong=1)
    r.run([0] * (B * 400))
    l, rt = r.run(imp)
    pl, pr = peaks(l), peaks(rt)
    check(pr and abs(pr[0][0] - T) <= 100 and (not pl or pl[0][0] > T + 2000) and pl and abs(pl[0][0] - 2 * T) <= 200,
          f"PingPong: the first echo on the right ({[i for i, _ in pr[:1]]}), the second on the left ({[i for i, _ in pl[:1]]})")

    def echo_hf(filter_hz):
        q = DRig(ptf, delay=which)
        q.settings(time_ms=100.0, feedback=0.0, filter_hz=filter_hz)
        q.run([0] * (B * 200))                              # past the glide from the start-up Time
        burst = tf.noise_burst(B * 40, B * 20, 4)
        ol, _ = q.run(burst)
        return tf.hf_ratio(ol[T:T + B * 20])
    bright, dark = echo_hf(20000.0), echo_hf(1000.0)
    check(dark < bright * 0.6, f"Filter 1 kHz: duller echoes than at 20 kHz ({bright:.3f} -> {dark:.3f})")
    if which == DELAY_BBD:
        def echo_hf_at(ms):
            q = DRig(ptf, delay=which)
            q.settings(time_ms=ms, feedback=0.0)
            q.run([0] * (B * 400))
            burst = tf.noise_burst(B * 200, B * 20, 4)
            ol, _ = q.run(burst)
            at = int(ms * 44.1)
            return tf.hf_ratio(ol[at:at + B * 20])
        short, long_ = echo_hf_at(100.0), echo_hf_at(400.0)
        check(long_ < short * 0.6, f"a longer Time, a slower clock: duller echoes at 400 ms than at 100 ms "
              f"({short:.3f} -> {long_:.3f})")

    q = DRig(ptf, delay=which)
    q.settings(time_ms=100.0, feedback=0.0)
    q.run([0] * (B * 200))
    n = tf.RATE * 2 // B * B
    tone = sine(n * 3)
    l1, _ = q.run(tone[:n])
    steady = freq_track(l1, tf.RATE // 2, n)
    q.settings(time_ms=300.0, feedback=0.0)
    l2, _ = q.run(tone[n:2 * n])
    glide = freq_track(l2, 0, tf.RATE // 10)
    l3, _ = q.run(tone[2 * n:])
    after = freq_track(l3, tf.RATE // 2, n)
    lo, hi = min(steady), max(steady)
    wobble = 100 * (hi - lo) / 2000
    if which == DELAY_TAPE:
        check(0.1 < wobble < 2.0, f"wow and flutter: a steady 1 kHz tone wobbles by {wobble:.2f} % ({lo:.1f}..{hi:.1f} Hz)")
    else:
        # the chip's sample and hold moves single zero crossings by a sample; the pitch, over 20 cycles
        avg = [sum(steady[i:i + 20]) / 20 for i in range(0, len(steady) - 20, 20)]
        drift = 100 * (max(avg) - min(avg)) / 2000
        check(drift < 0.1, f"a steady 1 kHz tone keeps its pitch (over 20 cycles within {drift:.3f} %)")
    check(glide and min(glide) < 950, f"Time 100 -> 300 ms: the echo bends down in pitch while the delay grows "
          f"(lowest {min(glide) if glide else 0:.0f} Hz)")
    check(after and abs(sum(after) / len(after) - 1000) < 10, f"and comes back to pitch ({sum(after) / len(after):.1f} Hz)")

    q = DRig(ptf, delay=which)
    q.settings(time_ms=150.0, feedback=0.5)
    q.run(tf.noise_burst(B * 40, B * 40, 6))
    tail, _ = q.run([0] * (B * 2100))
    check(max(abs(v) for v in tail[-B * 20:]) <= 2, f"after 6 s of silence, silence (peak {max(abs(v) for v in tail[-B * 20:])})")
    q.run(tf.noise_burst(B * 10, B * 10, 7))
    q.give_input = False
    l, _ = q.run([0] * (B * 60))
    check(max(abs(v) for v in l) > 300, "no send block: the echoes carry on")
    q.give_input = True
    q.settings(bypass=1)
    l, _ = q.run([1000] * B)
    check(not q.sent and sorted(q.released) == sorted(q.handed), "bypass set: nothing sent, every block released")
    q.settings(bypass=0)
    q.give_outputs = 1
    q.run([1000] * B)
    check(not q.sent and sorted(q.released) == sorted(q.handed), "an output block missing: nothing sent, every block released")
    q.give_outputs = 2
    q.run([1000] * B)
    check([o for _, o in q.sent] == [0, 1] and sorted(q.released) == sorted(q.handed),
          "otherwise: left to output 0, right to output 1, every block released")


def gated_checks(ptf: Path, stock: Path) -> None:
    print('delay: switching to "Gated"')
    r = DRig(ptf)
    r.m.write(r.bufs[0], bytes(range(256)) * 64)
    r.settings(time_ms=300.0, feedback=0.0)
    r.choose(DELAY_GATED)
    _, _, sent = r.run_counted([3000] * (B * tf.DL_CLEAR_BLOCKS))
    clear = all(bytes(r.m.uc.mem_read(b, tf.DL_BUF_BYTES)) == bytes(tf.DL_BUF_BYTES) for b in r.bufs[:2])
    check(sent == 0 and clear and r.active() == DELAY_GATED and r.m.r32(GATED) == GATED_MAGIC,
          f"{tf.DL_CLEAR_BLOCKS} silent blocks ({sent} sent) clear the two lines, then \"Gated\"")
    rate = tf.RATE
    hit = [int(12000 * math.exp(-i / 600) * math.sin(i * 0.9)) for i in range(2000)]

    def burst(time_ms=300.0, feedback=0.0, pingpong=0, filter_hz=20000.0, seconds=1.0):
        q = DRig(ptf, delay=DELAY_GATED)
        q.settings(time_ms=time_ms, feedback=feedback, pingpong=pingpong, filter_hz=filter_hz)
        q.run([0] * (B * 20))
        n = int(seconds * rate) // B * B
        return q.run(hit + [0] * (n - len(hit)))

    def thirds(x, length):
        return [tf.rms(x[int(length * k / 3):int(length * (k + 1) / 3)]) for k in range(3)]

    def end(x):
        return max((i for i, v in enumerate(x) if abs(v) > 30), default=0) / rate
    for ms in (150.0, 600.0):
        left, right = burst(time_ms=ms, seconds=ms / 1000 + 0.5)
        e = end(left + [0])
        check(ms / 1000 <= e <= ms / 1000 + 0.12 and left != right,
              f"Time {ms:.0f} ms: the burst lasts {e * 1000:.0f} ms, then nothing; the two sides differ")
    length = int(0.3 * rate)
    left, _ = burst()
    after = max(abs(v) for v in left[length + int(0.12 * rate):])
    check(after <= 2, f"silence after the burst (peak {after})")
    hard, fade = thirds(burst(feedback=0.0)[0], length), thirds(burst(feedback=0.99)[0], length)
    check(fade[2] < hard[2] * 0.6 and fade[0] > hard[0] * 0.8,
          f"Feedback at the top: the end fades out ({hard[2]:.0f} -> {fade[2]:.0f} in the last third)")
    fwd, rev = thirds(burst()[0], length), thirds(burst(pingpong=1)[0], length)
    check(rev[0] < rev[2] * 0.5 and fwd[0] > rev[0] * 2,
          f"PingPong: reversed, it swells up ({rev[0]:.0f} -> {rev[2]:.0f} from the first third to the last)")
    def noise_burst(filter_hz):
        q = DRig(ptf, delay=DELAY_GATED)
        q.settings(time_ms=300.0, feedback=0.0, filter_hz=filter_hz)
        q.run([0] * (B * 20))
        left, _ = q.run(tf.noise_burst(B * 120, 2000, 5))
        return tf.hf_ratio(left[:length])
    bright, dark = noise_burst(20000.0), noise_burst(1000.0)
    check(dark < bright * 0.6, f"Filter 1 kHz: a duller burst ({bright:.3f} -> {dark:.3f})")
    tone = [int(8000 * math.sin(2 * math.pi * 440 * i / rate)) if i < length else 0 for i in range(B * 200)]
    s = tf.DelayRig(stock)
    s.settings(time_ms=300.0, feedback=0.5)
    sl, _ = s.run(tone)
    q = DRig(ptf, delay=DELAY_GATED)
    q.settings(time_ms=300.0, feedback=0.0)
    q.run([0] * (B * 20))
    gl, _ = q.run(tone)
    lv = tf.db(tf.rms(gl[:length]), tf.rms(sl[length:2 * length]))
    check(abs(lv) < 6.0, f"a 440 Hz tone: the burst against the stock delay's first echo, {lv:+.1f} dB")
    q.run(hit)
    q.give_input = False
    left, _ = q.run([0] * (B * 40))
    check(max(abs(v) for v in left[:B * 20]) > 300, "no send block: the burst still plays out")
    q.give_input = True
    q.settings(bypass=1)
    q.run([1000] * B)
    check(not q.sent and sorted(q.released) == sorted(q.handed), "bypass set: nothing sent, every block released")
    q.settings(bypass=0)
    q.give_outputs = 1
    q.run([1000] * B)
    check(not q.sent and sorted(q.released) == sorted(q.handed), "an output block missing: nothing sent, every block released")
    q.give_outputs = 2
    q.run([1000] * B)
    check([o for _, o in q.sent] == [0, 1] and sorted(q.released) == sorted(q.handed),
          "otherwise: left to output 0, right to output 1, every block released")


def delay_checks(ptf: Path, stock: Path) -> None:
    print("delay: start-up and Stock")
    r, s = DRig(ptf, booted=False), tf.DelayRig(stock, booted=False)
    check(r.update != tf.DELAY_STOCK_UPDATE | 1 and r.update & 1, f"delay update() slot points into the patch ({r.update:#x})")
    sig = Signals(5)
    r.choose(1)
    x, _ = sig.blocks(60)
    d = same(r.run(x), s.run(x))
    check(d == 0, f"before the Pattern screen, Tape chosen: the stock delay's output (largest difference {d})")
    r.choose(0)
    r.m.call(r.m.r32(PATTERN_UPDATE_SLOT), 0x20021770)
    worst = 0
    for k, kw in enumerate(({}, {"time_ms": 80.0, "feedback": 0.8}, {"pingpong": 1, "filter_hz": 2000.0},
                            {"time_ms": 3000.0, "feedback": 0.3})):
        r.settings(**kw)
        s.settings(**kw)
        x, _ = sig.blocks(80)
        worst = max(worst, same(r.run(x), s.run(x)))
    check(worst == 0, f"booted, Stock chosen: identical to stock firmware over 320 blocks and 4 settings (largest difference {worst})")

    delay_effect_checks(ptf, stock, DELAY_TAPE, "Tape", TAPE, TAPE_MAGIC)
    delay_effect_checks(ptf, stock, DELAY_BBD, "BBD", BBD, BBD_MAGIC)
    gated_checks(ptf, stock)

    print("delay: back to Stock, and the start-up reset")
    r = DRig(ptf, delay=1)
    r.settings(time_ms=120.0, feedback=0.7)
    r.run(tf.noise_burst(B * 100, B * 100, 8))
    r.m.write(DELAY + 0x3C, struct.pack("<6i", 5, -7, 9, 11, -13, 15))
    r.choose(0)
    _, _, sent = r.run_counted([2000] * (B * tf.DL_STOCK_CLEAR_BLOCKS))
    clear = all(bytes(r.m.uc.mem_read(b, tf.DL_BUF_BYTES)) == bytes(tf.DL_BUF_BYTES) for b in r.bufs)
    states = struct.unpack("<6i", bytes(r.m.uc.mem_read(DELAY + 0x3C, 24)))
    check(sent == 0 and clear and states == (0,) * 6 and r.active() == 0,
          f"{tf.DL_STOCK_CLEAR_BLOCKS} silent blocks ({sent} sent) clear all three buffers and zero the stock filter states")
    s = tf.DelayRig(stock)
    s.settings(time_ms=120.0, feedback=0.7)
    imp = [20000] + [0] * (B * 100 - 1)
    d = same(r.run(imp), s.run(imp))
    check(d == 0, f"then the stock delay: its impulse response is a fresh stock delay's (largest difference {d})")
    r.choose(1)
    r.run([0] * (B * tf.DL_CLEAR_BLOCKS))
    r.restart()
    _, _, sent = r.run_counted([2000] * (B * 4))
    check(r.active() == 0 and sent == 4, "after the start-up reset: the stock delay, Tape still chosen")
    r.m.call(r.m.r32(PATTERN_UPDATE_SLOT), 0x20021770)
    _, _, sent = r.run_counted([0] * (B * tf.DL_CLEAR_BLOCKS))
    _, _, sent2 = r.run_counted([0] * (B * 4))
    check(sent == 0 and sent2 == 4 and r.active() == 1, "and the tape once the Pattern screen runs (after its clear)")
    r.choose(9)
    _, _, sent = r.run_counted([0] * (B * (tf.DL_STOCK_CLEAR_BLOCKS + 2)))
    check(r.active() == 0, "a value out of range counts as Stock")
    for target, blocks in ((DELAY_BBD, tf.DL_CLEAR_BLOCKS), (DELAY_GATED, tf.DL_CLEAR_BLOCKS),
                           (DELAY_TAPE, tf.DL_CLEAR_BLOCKS), (DELAY_GATED, tf.DL_CLEAR_BLOCKS),
                           (DELAY_BBD, tf.DL_CLEAR_BLOCKS), (DELAY_STOCK, tf.DL_STOCK_CLEAR_BLOCKS)):
        r.run(tf.noise_burst(B * 20, B * 20, target))
        r.choose(target)
        _, _, sent = r.run_counted([1500] * (B * blocks))
        _, _, sent2 = r.run_counted([1500] * (B * 4))
        check(sent == 0 and sent2 == 4 and r.active() == target,
              f"to {DELAY_NAMES[target]}: {blocks} silent blocks, then it runs")

    print("delay: cost")

    def per_block(rig, blocks=30, sig=1000):
        worst = 0
        for _ in range(blocks):
            n = [0]
            h = rig.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s_, u: n.__setitem__(0, n[0] + 1))
            rig.run([sig] * B)
            rig.m.uc.hook_del(h)
            worst = max(worst, n[0])
        return worst
    s = tf.DelayRig(stock)
    c_stock = per_block(s)
    for which in (DELAY_TAPE, DELAY_BBD, DELAY_GATED):
        q = DRig(ptf, delay=which)
        q2 = DRig(ptf, delay=which)
        q2.settings(pingpong=1)
        c_mono, c_pp = per_block(q), per_block(q2)
        check(c_mono < 60_000 and c_pp < 60_000, f"{DELAY_NAMES[which]}: {c_mono} instructions per block, {c_pp} "
              f"with PingPong (stock delay {c_stock}; under 60,000)")
    q.choose(0)
    c_clear = per_block(q, blocks=tf.DL_STOCK_CLEAR_BLOCKS)
    check(c_clear < 100_000, f"a switch's clearing blocks: at most {c_clear} instructions per block (under 100,000)")


def menu_checks(ptf: Path) -> None:
    print("Config menu: Project Settings > Master FX > Reverb, Delay")
    m = Machine(ptf)
    m.stub_function(ATEXIT, lambda mm: 0)
    m.call(MENU_CTOR, max_insns=50_000_000)
    ps = PROJECT_SETTINGS
    cap, count = m.r32(ps + 8) & 0xFF, m.r32(ps + 0x1C) >> 8 & 0xFF
    kids = [m.r32(m.r32(ps + 0xC) + 4 * i) for i in range(min(cap, count))]
    labels = [m.cstr(m.r32(k + (0x18 if m.r32(k) & 0xFF == 1 else 4))) for k in kids]
    check(cap == count and count >= 11, f"Project Settings: room for {cap}, holds {count}")
    check(labels[:10] == STOCK_ITEMS, "the 10 stock items are unchanged, in order")
    if "Master FX" not in labels[10:]:
        check(False, f"a 'Master FX' folder after the stock items ({labels[10:]})")
        return
    at = labels.index("Master FX", 10)
    folder = kids[at]
    fcap, fcount = m.r32(folder + 8) & 0xFF, m.r32(folder + 0x1C) >> 8 & 0xFF
    items = [m.r32(m.r32(folder + 0xC) + 4 * i) for i in range(min(fcap, fcount))]
    names = [m.cstr(m.r32(i + 4)) for i in items]
    check(m.r32(folder) & 0xFF == 1 and fcap == fcount == 2 and names == ["Reverb", "Delay"],
          f"item {at + 1} is the folder 'Master FX' with {names} (patch entries: {labels[10:]})")
    m.stub_function(0x60118828, lambda mm: 0)               # menu redraw
    m.w32(MENU_CURRENT, ps)
    m.w8(ps + 0x1C, at)
    m.call(MENU_SELECT)
    check(m.r32(MENU_CURRENT) == folder, "the stock select opens the folder")
    closed = []
    m.stub_function(MENU_EDITOR_CLOSE, lambda mm: closed.append(1))
    for idx, (label, addr, want) in enumerate((("Reverb", MX + 8, REVERB_NAMES),
                                               ("Delay", MX + 13, DELAY_NAMES))):
        item = items[idx]
        desc = m.r32(item + 0x10)
        cb = m.r32(desc + 12)
        opts = [m.cstr(m.r32(m.r32(desc + 8) + 4 * i)) for i in range(m.r32(desc + 4))]
        check(m.r32(item + 0xC) & 0xFF == 4 and m.r32(desc) == addr and opts == want
              and CODE[0] <= cb < CODE[1] and cb & 1 and m.cstr(m.r32(item + 8)) != ""
              and m.uc.mem_read(addr, 1)[0] == 0,
              f"'{label}': an option item {opts} on the patch's choice, with help text and an apply "
              "callback in the patch, starting at Stock")
        for value in list(range(1, len(want))) + [0]:
            m.w8(folder + 0x1C, idx)
            n = len(closed)
            m.call(MENU_APPLY, 0, value, max_insns=2_000_000)
            check(m.uc.mem_read(addr, 1)[0] == value and len(closed) == n + 1,
                  f"{label}: Apply {want[value]} stored, editor closed")


def fnv1a(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h


def file_checks(ptf: Path) -> None:
    print("settings file (masterfx.bin), through the screens' update hooks")
    m = Machine(ptf)
    fs = FakeFS(m, Path("."))
    calls = {"pattern": 0, "config": 0}
    m.stub_function(PATTERN_UPDATE, lambda mm: calls.__setitem__("pattern", calls["pattern"] + 1))
    m.stub_function(CONFIG_UPDATE, lambda mm: calls.__setitem__("config", calls["config"] + 1))
    pat_hook, cfg_hook = m.r32(PATTERN_UPDATE_SLOT), m.r32(CONFIG_UPDATE_SLOT)

    def tick(n=1, hook=None):
        for _ in range(n):
            m.call(hook or pat_hook, 0x20021770)

    def value():
        return m.uc.mem_read(MX + 8, 1)[0]

    def delay():
        return m.uc.mem_read(MX + 13, 1)[0]

    def path(name):
        return f"Projects/User/{name}/masterfx.bin"

    def writes():
        return len([x for x in fs.log if "masterfx.bin mode 3" in x])

    def hmf1(reverb, delay=0):
        data = bytes([reverb, delay, 0, 0])
        return FILE_MAGIC + data + struct.pack("<I", fnv1a(data))

    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(value() == 0 and delay() == 0, "project without masterfx.bin: Stock reverb and delay")
    tick(SAVE_DELAY + 5)
    check(writes() == 0, "nothing written while nothing changed")
    m.w8(MX + 8, 1)
    tick(SAVE_DELAY - 2, cfg_hook)
    check(writes() == 0, "no write right after a change")
    tick(5, cfg_hook)
    check(writes() == 1 and fs.overlay.get(path("A")) == hmf1(1), "the change is written once, a while later (HMF1, 12 bytes)")
    tick(SAVE_DELAY * 2)
    check(writes() == 1, "no rewrite while nothing changes")
    m.w8(MX + 13, 1)
    tick(SAVE_DELAY + 5)
    check(writes() == 2 and fs.overlay.get(path("A")) == hmf1(1, 1), "choosing Tape is saved too (byte 5)")
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    check(value() == 0, "another project without the file: Stock")
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(value() == 1 and delay() == 1, "back to the first project: Freeverb and Tape")
    good = fs.overlay[path("A")]
    fs.overlay[path("A")] = good[:11] + bytes([good[11] ^ 1])
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(value() == 0 and delay() == 0, "damaged file: Stock")
    fs.overlay[path("A")] = good
    fs.overlay[path("E")] = hmf1(5)
    fs.set_project(Path("."), "E", "Projects/User")
    n = writes()
    tick(SAVE_DELAY * 2)
    check(value() == 0 and writes() == n and fs.overlay[path("E")] == hmf1(5),
          "a file naming an unknown reverb: Stock, and the file is kept")
    fs.overlay[path("F")] = hmf1(1, 7)
    fs.set_project(Path("."), "F", "Projects/User")
    n = writes()
    tick(SAVE_DELAY * 2)
    check(value() == 0 and delay() == 0 and writes() == n and fs.overlay[path("F")] == hmf1(1, 7),
          "a file naming an unknown delay: Stock, and the file is kept")
    fs.overlay[path("G")] = hmf1(0, 1)
    fs.set_project(Path("."), "G", "Projects/User")
    tick()
    check(value() == 0 and delay() == 1, "a file with Tape only: Stock reverb, Tape")
    m.write(PROJECT_NAME, b"\0")
    tick()
    m.w8(MX + 8, 1)
    n = writes()
    tick(SAVE_DELAY * 2)
    check(writes() == n, "project without a name: nothing written")

    def put(v):
        m.w8(MX + 8, v[0])
        m.w8(MX + 13, v[1])

    save_as_checks(check, m, fs, tick, "masterfx.bin", lambda: (value(), delay()), put, (0, 0), (1, 1), (3, 2),
                   lambda v: hmf1(*v), SAVE_DELAY)
    check(calls["pattern"] > 0 and calls["config"] > 0, f"stock update methods ran ({calls})")


def cost_checks(ptf: Path) -> None:
    print("cost")

    def per_block(rig, blocks=60, own=False):
        counts = []
        for _ in range(blocks):
            n = [0]
            where = {"begin": CODE[0], "end": CODE[1] - 1} if own else {}
            h = rig.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: n.__setitem__(0, n[0] + 1), **where)
            rig.run(tf.noise_burst(B, B, 2 * len(counts)), tf.noise_burst(B, B, 2 * len(counts) + 1))
            rig.m.uc.hook_del(h)
            counts.append(n[0])
        return max(counts)

    r = Rig(ptf)
    r.pattern()
    mine = per_block(r, own=True)
    check(0 < mine <= OWN_LIMIT, f"Stock chosen: at most {OWN_LIMIT} of its own instructions per block ({mine})")
    for which in (REVERB_FREEVERB, REVERB_PLATE, REVERB_SPRING, REVERB_SHIMMER):
        r.choose(which)
        r.run([0] * (B * 8), [0] * (B * 8))
        c = per_block(r)
        check(c < 90_000, f"{REVERB_NAMES[which]} chosen: {c} instructions per block (under 90,000)")


def main() -> int:
    global MX, TAPE, BBD, GATED, SHIMMER, CODE
    args = [a for a in sys.argv[1:] if a != "--quick"]
    quick = "--quick" in sys.argv[1:]
    ptf, stock = Path(args[0]), Path(args[1])
    wav_dir = Path(args[2]) if len(args) > 2 else None
    where = tf.ram_map(Machine(ptf), ptf)
    MX, TAPE, BBD, GATED, SHIMMER = where["mx"], where["tape"], where["bbd"], where["gated"], where["shimmer"]
    CODE = patch_code(ptf, "masterfx")
    startup_and_stock(ptf, stock)
    switching(ptf, stock)
    reverb_cycle(ptf)
    if quick:                               # the mix-dependent parts only: hooks, switching, menu, file
        menu_checks(ptf)
        file_checks(ptf)
        print(f"{len(failures)} failure(s) (--quick: sound, delay and cost checks skipped)")
        return 1 if failures else 0
    for which, name, tol in ((REVERB_FREEVERB, "Freeverb", 2), (REVERB_PLATE, "Plate", 2), (REVERB_SPRING, "Spring", 8),
                             (REVERB_SHIMMER, "Shimmer", 2)):
        reverb_checks(ptf, stock, which, name, tol)
    shimmer_checks(ptf)
    delay_checks(ptf, stock)
    menu_checks(ptf)
    file_checks(ptf)
    cost_checks(ptf)
    if wav_dir:
        write_wavs(ptf, stock, wav_dir)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
