"""Check the masterfx patch (a choice of master reverb per project).

  python tools/emu/test_masterfx.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf> [<wav folder>]

Works with the masterfx-only build and with bundles that include it. The reverb object
(0x20019014) is driven through its vtable as the audio graph does, with the block
helpers stubbed (tools/emu/reverb_rig.py).

1. Start-up and Stock: until the Pattern screen's update has run, the hook gives the stock
   reverb's output, sample for sample, even with Freeverb chosen; with Stock chosen it
   stays identical to stock firmware after that too (300 blocks of changing signals).
2. Switching: choosing Freeverb gives 8 silent blocks (nothing sent) that clear both
   buffers, then Freeverb; choosing Stock again gives 8 silent blocks, zeroes the stock reverb's filter
   states, and then the stock reverb is silent with silence in and its impulse response
   matches a stock-only unit's (within 0.5 dB); a value out of range counts as Stock; the
   start-up reset (the Config menu constructor) brings back the stock reverb until the
   Pattern screen runs again.
3. Freeverb: with the bypass flag set nothing is received or sent; with no left block
   nothing is sent, and with no right block the left one is released; otherwise both
   blocks are transmitted (left to output 0, right to output 1) and both released, as the
   stock update() does. Amount 0 returns the send unchanged; an impulse gives a decaying
   tail with no clipping, longer with a larger Size; more Damping takes high frequencies
   out of the tail; Predelay moves the reverb later by its number of samples; Diffusion
   changes the tail; the level field (+0x70) follows the output; a burst of noise gives a
   tail within 6 dB of the stock reverb's at the same settings.
4. Config menu: the stock menu constructor; Project Settings holds its 10 stock items and
   a "Master FX" folder with a "Reverb" option item (Stock, Freeverb) on the patch's
   choice, starting at Stock; the stock Apply stores both values.
5. Settings file: masterfx.bin per project, through the screens' update hooks with a fake
   SD card: default Stock and no file, one write a while after a change, per-project
   values, a damaged file and a file naming an unknown reverb (Stock, and the file is
   kept), the delay byte kept as read, a project without a name.
6. Cost: instructions per block with Stock (against stock firmware) and Freeverb.
7. WAV files (if a folder is given): the same test sounds through the stock reverb and
   Freeverb, default settings and a large room, 44.1 kHz stereo, return only.
"""
from __future__ import annotations

import math
import random
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import reverb_rig as tf  # noqa: E402
from fakefs import PROJECT_NAME, FakeFS  # noqa: E402
from layout import patch_ram  # noqa: E402
from machine import Machine  # noqa: E402
from patch_ptf import bl_target  # noqa: E402

B = tf.BLOCK
REVERB = tf.REVERB
MX_OFFSET = 0x120                   # the patch's RAM: Freeverb's state (288 bytes), then its own
MX_MAGIC = 0x5846464D
REVERB_STOCK, REVERB_FREEVERB = 0, 1
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
    check(out[2] == 10 and r.m.uc.mem_read(MX + 0x14, 1)[0] == REVERB_STOCK,
          "a value out of range counts as Stock (no switch, the stock reverb runs)")

    r.choose(REVERB_FREEVERB)
    r.run(*sig.blocks(20))
    r.restart()
    x = sig.blocks(30)
    a = r.run(*x)
    check(r.m.uc.mem_read(MX + 0x14, 1)[0] == REVERB_STOCK and peak(a) > 0,
          "after the start-up reset: the stock reverb again, Freeverb still chosen")
    r.pattern()
    out = r.run_counted(*sig.blocks(8))
    out2 = r.run_counted(*sig.blocks(4))
    check(out[2] == 0 and out2[2] == 4 and r.m.uc.mem_read(MX + 0x14, 1)[0] == REVERB_FREEVERB,
          "and Freeverb once the Pattern screen runs (8 silent blocks first)")


def freeverb_checks(ptf: Path, stock: Path) -> None:
    print("Freeverb: block handling")
    r = tf.Rig(ptf, reverb=REVERB_FREEVERB)
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

    print("Freeverb: sound")
    r = tf.Rig(ptf, reverb=REVERB_FREEVERB)
    r.settings(amount=0.0)
    sig = tf.noise_burst(B * 8, B * 8)
    out_l, out_r = r.run(sig, sig)
    check(out_l == sig and out_r == sig, "amount 0: the send comes back unchanged")

    def tail(size=0.5, damping=0.5, predelay=0.0, diffusion=0.625, seconds=4.0):
        rr = tf.Rig(ptf, reverb=REVERB_FREEVERB)
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
    check(abs((on1 - on0) - 2500) <= 2, f"Predelay 0.5 moves the reverb {on1 - on0} samples later (2500 expected)")
    _, l_d0, _ = tail(diffusion=0.0, seconds=1.0)
    _, l_d1, _ = tail(diffusion=0.85, seconds=1.0)
    check(l_d0 != l_d1, "Diffusion changes the tail")
    check(r2.level() >= 0.0 and r1.level() >= 0.0, f"the level field is written ({r2.level():.1f})")
    rr = tf.Rig(ptf, reverb=REVERB_FREEVERB)
    rr.run(tf.noise_burst(B * 16, B * 16), tf.noise_burst(B * 16, B * 16, 2))   # past the first comb
    check(rr.level() > 100, f"the level field follows the output ({rr.level():.0f})")

    n = 2 * rate // B * B
    burst_l, burst_r = tf.noise_burst(n, rate // 10, 3), tf.noise_burst(n, rate // 10, 4)
    outs = {}
    for label, rig in (("stock", tf.Rig(stock)), ("freeverb", tf.Rig(ptf, reverb=REVERB_FREEVERB))):
        rig.settings()
        outs[label] = rig.run(burst_l, burst_r)
    tail_s = tf.rms(outs["stock"][0][rate // 5:rate] + outs["stock"][1][rate // 5:rate])
    tail_f = tf.rms(outs["freeverb"][0][rate // 5:rate] + outs["freeverb"][1][rate // 5:rate])
    check(abs(tf.db(tail_f, tail_s)) <= 6.0, f"tail loudness, Freeverb vs stock: {tf.db(tail_f, tail_s):+.1f} dB")


def write_wavs(ptf: Path, stock: Path, wav_dir: Path) -> None:
    wav_dir.mkdir(parents=True, exist_ok=True)
    n = int(5.0 * tf.RATE) // B * B
    dry_l, dry_r = tf.plucks(n)
    tf.write_wav(wav_dir / "reverb_send_dry.wav", dry_l, dry_r)
    for label, make_rig in (("stock", lambda: tf.Rig(stock)),
                            ("freeverb", lambda: tf.Rig(ptf, reverb=REVERB_FREEVERB))):
        for setting, kw in (("default", {}), ("large", {"size": 0.9, "damping": 0.3, "predelay": 0.2})):
            rr = make_rig()
            rr.settings(**kw)
            left, right = rr.run(dry_l, dry_r)
            tf.write_wav(wav_dir / f"reverb_{label}_{setting}.wav", left, right)
    print(f"  wrote WAV files to {wav_dir}")


def menu_checks(ptf: Path) -> None:
    print("Config menu: Project Settings > Master FX > Reverb")
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
    check(m.r32(folder) & 0xFF == 1 and fcap == fcount == 1 and names == ["Reverb"],
          f"item {at + 1} is the folder 'Master FX' with {names} (patch entries: {labels[10:]})")
    m.stub_function(0x60118828, lambda mm: 0)               # menu redraw
    m.w32(MENU_CURRENT, ps)
    m.w8(ps + 0x1C, at)
    m.call(MENU_SELECT)
    check(m.r32(MENU_CURRENT) == folder, "the stock select opens the folder")
    item = items[0]
    desc = m.r32(item + 0x10)
    cb = m.r32(desc + 12)
    opts = [m.cstr(m.r32(m.r32(desc + 8) + 4 * i)) for i in range(m.r32(desc + 4))]
    check(m.r32(item + 0xC) & 0xFF == 4 and m.r32(desc) == MX + 8 and opts == ["Stock", "Freeverb"]
          and 0x601BAB78 <= cb < 0x601D0000 and cb & 1 and m.cstr(m.r32(item + 8)) != ""
          and m.uc.mem_read(MX + 8, 1)[0] == 0,
          f"'Reverb': an option item {opts} on the patch's choice, with help text and an apply "
          "callback in the patch, starting at Stock")
    closed = []
    m.stub_function(MENU_EDITOR_CLOSE, lambda mm: closed.append(1))
    for value, name in ((1, "Freeverb"), (0, "Stock")):
        m.w8(folder + 0x1C, 0)
        n = len(closed)
        m.call(MENU_APPLY, 0, value, max_insns=2_000_000)
        check(m.uc.mem_read(MX + 8, 1)[0] == value and len(closed) == n + 1, f"Apply {name} stored, editor closed")


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

    def path(name):
        return f"Projects/User/{name}/masterfx.bin"

    def writes():
        return len([x for x in fs.log if "masterfx.bin mode 3" in x])

    def hmf1(reverb, delay=0):
        data = bytes([reverb, delay, 0, 0])
        return FILE_MAGIC + data + struct.pack("<I", fnv1a(data))

    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(value() == 0, "project without masterfx.bin: Stock")
    tick(SAVE_DELAY + 5)
    check(writes() == 0, "nothing written while nothing changed")
    m.w8(MX + 8, 1)
    tick(SAVE_DELAY - 2, cfg_hook)
    check(writes() == 0, "no write right after a change")
    tick(5, cfg_hook)
    check(writes() == 1 and fs.overlay.get(path("A")) == hmf1(1), "the change is written once, a while later (HMF1, 12 bytes)")
    tick(SAVE_DELAY * 2)
    check(writes() == 1, "no rewrite while nothing changes")
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    check(value() == 0, "another project without the file: Stock")
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(value() == 1, "back to the first project: Freeverb")
    good = fs.overlay[path("A")]
    fs.overlay[path("A")] = good[:11] + bytes([good[11] ^ 1])
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(value() == 0, "damaged file: Stock")
    fs.overlay[path("A")] = good
    fs.overlay[path("E")] = hmf1(5)
    fs.set_project(Path("."), "E", "Projects/User")
    n = writes()
    tick(SAVE_DELAY * 2)
    check(value() == 0 and writes() == n and fs.overlay[path("E")] == hmf1(5),
          "a file naming an unknown reverb: Stock, and the file is kept")
    fs.overlay[path("F")] = hmf1(0, 3)
    fs.set_project(Path("."), "F", "Projects/User")
    tick()
    m.w8(MX + 8, 1)
    tick(SAVE_DELAY + 5)
    check(fs.overlay.get(path("F")) == hmf1(1, 3), "the delay byte is kept as read when the file is rewritten")
    m.write(PROJECT_NAME, b"\0")
    tick()
    m.w8(MX + 8, 1)
    n = writes()
    tick(SAVE_DELAY * 2)
    check(writes() == n, "project without a name: nothing written")
    fs.set_project(Path("."), "C", "Projects/User")
    tick()
    check(value() == 1, "first save of a new project keeps its choice")
    tick(SAVE_DELAY + 5)
    check(fs.overlay.get(path("C"), b"") == hmf1(1), "and writes it to its folder")
    check(calls["pattern"] > 0 and calls["config"] > 0, f"stock update methods ran ({calls})")


def cost_checks(ptf: Path, stock: Path) -> None:
    print("cost")

    def per_block(rig, blocks=60):
        counts = []
        for _ in range(blocks):
            n = [0]
            h = rig.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: n.__setitem__(0, n[0] + 1))
            rig.run(tf.noise_burst(B, B, 2 * len(counts)), tf.noise_burst(B, B, 2 * len(counts) + 1))
            rig.m.uc.hook_del(h)
            counts.append(n[0])
        return max(counts)

    r, s = Rig(ptf), tf.Rig(stock, booted=False)
    r.pattern()
    stock_cost, mine = per_block(s), per_block(r)
    check(mine - stock_cost < 200, f"Stock chosen: {mine} instructions per block, stock firmware {stock_cost}")
    r.choose(REVERB_FREEVERB)
    r.run([0] * (B * 8), [0] * (B * 8))
    fv = per_block(r)
    check(fv < 90_000, f"Freeverb chosen: {fv} instructions per block (under 90,000)")


def main() -> int:
    global MX
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    wav_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    MX = patch_ram(ptf, "masterfx") + MX_OFFSET
    startup_and_stock(ptf, stock)
    switching(ptf, stock)
    freeverb_checks(ptf, stock)
    menu_checks(ptf)
    file_checks(ptf)
    cost_checks(ptf, stock)
    if wav_dir:
        write_wavs(ptf, stock, wav_dir)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
