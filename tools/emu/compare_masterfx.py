"""Compare two masterfx builds block by block, and measure both.

  python tools/emu/compare_masterfx.py <a.ptf> <b.ptf> [blocks] [seed ...]

Both builds start with Freeverb chosen and get the same randomized session (as
tools/emu/stress_masterfx.py makes them: changing sends, Master Reverb fields set to
random and garbage values, bypass toggles, missing blocks, the Pattern-screen start, the
start-up reset, and now and then the reverb choice switched to Stock and back). After every block the
two outputs (both channels, every sample) and the level field must be equal, unless
--tolerance N allows a difference of up to N per sample (for builds that are meant to
differ slightly). Instructions per block are measured for both on a sample of blocks.
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
from patch_ptf import bl_target  # noqa: E402

REVERB = tf.REVERB
GARBAGE = [float("nan"), float("inf"), -float("inf"), -1.0, 2.0, 1e9]


def make_rig(ptf: Path) -> tf.Rig:
    r = tf.Rig(ptf, reverb=1)
    r.m.stub_function(0x601183E8, lambda mm: 0)          # cfg_menu_item, for the restart hook
    r.m.stub_function(0x60118394, lambda mm: 0)          # cfg_menu_folder, for the restart hook
    image = bytes(r.m.uc.mem_read(0x60000000, 0x200000))
    r.restart = bl_target(image, 0x60000000, 0x601193E6)
    return r


def session(a: tf.Rig, b: tf.Rig, blocks: int, seed: int, tolerance: int):
    rnd = random.Random(seed)
    rigs = (a, b)
    kind, level = "noise", 8000
    worst = 0
    costs = [[], []]
    for blk in range(blocks):
        if rnd.random() < 0.02:
            kind = rnd.choice(["silence", "noise", "noise", "sine", "impulse", "dc"])
            level = rnd.choice([200, 3000, 16000, 32767])
        ev = rnd.random()
        action = None
        if ev < 0.01:
            vals = {off: (rnd.choice(GARBAGE) if rnd.random() < 0.2 else rnd.random())
                    for off in (tf.SIZE, tf.LP, tf.DIFFUSION, tf.AMOUNT, tf.INPUT_GAIN)}
            pre = rnd.choice([-5, 0, rnd.randrange(5001), 5001])
            action = ("settings", vals, pre)
        elif ev < 0.012:
            action = ("bypass", rnd.choice([0, 1]))
        elif ev < 0.015:
            action = ("give", [rnd.random() < 0.5, rnd.random() < 0.5])
        elif ev < 0.017:
            action = ("give", [True, True])
        elif ev < 0.0175:
            action = ("pattern",)
        elif ev < 0.0177:
            action = ("restart",)
        elif ev < 0.0187:
            action = ("choose", rnd.choice([0, 1]))
        n = tf.BLOCK
        if kind == "silence":
            left = right = [0] * n
        elif kind == "noise":
            left = [int(rnd.uniform(-level, level)) for _ in range(n)]
            right = [int(rnd.uniform(-level, level)) for _ in range(n)]
        elif kind == "sine":
            ph = rnd.random() * 6.283
            left = [int(30000 * math.sin(ph + 0.05 * i)) for i in range(n)]
            right = left[::-1]
        elif kind == "dc":
            left, right = [32767] * n, [-32768] * n
        else:
            left = [0] * n
            left[rnd.randrange(n)] = 32767
            right = list(left)
        outs = []
        for i, r in enumerate(rigs):
            m = r.m
            if action:
                if action[0] == "settings":
                    for off, v in action[1].items():
                        m.write(REVERB + off, struct.pack("<f", v))
                    m.w32(REVERB + tf.PREDELAY, action[2])
                elif action[0] == "bypass":
                    m.w8(REVERB + tf.BYPASS, action[1])
                elif action[0] == "give":
                    r.give = list(action[1])
                elif action[0] == "pattern":
                    m.call(m.r32(tf.PATTERN_UPDATE_SLOT), 0x20021770)
                elif action[0] == "restart":
                    m.call(r.restart | 1, 0, 0, 0, 0, 0, 0)
                elif action[0] == "choose":
                    r.choose(action[1])
            count = [0]
            sample = blk % 25 == 0
            if sample:
                h = m.uc.hook_add(UC_HOOK_CODE, lambda uc, ad, s, u: count.__setitem__(0, count[0] + 1))
            out = r.run(left, right)
            if sample:
                m.uc.hook_del(h)
                costs[i].append(count[0])
            outs.append((out, r.level(), [o for _, o in r.sent]))
        (oa, la, sa), (ob, lb, sb) = outs
        diff = max([abs(x - y) for x, y in zip(oa[0] + oa[1], ob[0] + ob[1])] + [0])
        worst = max(worst, diff)
        level_ok = (la == lb) or (math.isnan(la) and math.isnan(lb)) or tolerance > 0
        if diff > tolerance or not level_ok or sa != sb:
            return f"block {blk}: differs (max {diff} per sample, level {la} vs {lb}, sent {sa} vs {sb})", worst, costs
    return None, worst, costs


def main() -> int:
    args = sys.argv[1:]
    tolerance = 0
    if "--tolerance" in args:
        k = args.index("--tolerance")
        tolerance = int(args[k + 1])
        del args[k:k + 2]
    a, b = Path(args[0]), Path(args[1])
    blocks = int(args[2]) if len(args) > 2 else 3000
    seeds = [int(s) for s in args[3:]] or [1]
    failed = 0
    for seed in seeds:
        err, worst, costs = session(make_rig(a), make_rig(b), blocks, seed, tolerance)
        ca = sum(costs[0]) / max(1, len(costs[0]))
        cb = sum(costs[1]) / max(1, len(costs[1]))
        status = "FAIL" if err else "PASS"
        print(f"  {status}  seed {seed}: {blocks} blocks, largest sample difference {worst}; "
              f"instructions per block {a.name} {ca:.0f} (max {max(costs[0])}), {b.name} {cb:.0f} (max {max(costs[1])})")
        if err:
            print("        " + err)
            failed += 1
    print(f"{failed} failing session(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
