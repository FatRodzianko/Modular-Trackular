"""Stress the masterfx patch: long randomized sessions through the reverb or delay object's update().

  python tools/emu/stress_masterfx.py <patched.ptf> [blocks] [seed] [seed ...] [--delay]

Works with masterfx alone or in a bundle. Each reverb session starts with Freeverb
chosen; with --delay, the sessions drive the delay object instead (below), starting with
Tape chosen. Both switch between all the effects at random.

Each seed runs a session of `blocks` audio blocks (default 20000, about 58 s) through
the reverb object's update() as tools/emu/test_masterfx.py does (tools/emu/reverb_rig.py),
with:
  - sends that change every so often: silence, noise at random levels, full-scale noise,
    full-scale DC, a loud sine, impulses;
  - at random blocks: the Master Reverb fields set to random values, extremes or
    garbage (NaN, infinities, negatives, huge numbers, Predelay out of range); the bypass
    flag toggled; a missing left or right block; the buffer pointers moved to other
    memory and back; the Pattern screen hook and the start-up reset (the patch's hook on
    the Config menu constructor, as at every start) fired; the project's reverb choice
    set to any of the reverbs or a value out of range.
After every block it checks:
  - no fault, and the update returns within 400,000 instructions;
  - every write by the patch's code went to its own RAM, the two reverb buffers in use,
    the audio blocks, the reverb's level field, the stock reverb's filter states (zeroed
    on a switch to it), or the stack (a write watch covers the rest of RAM);
  - its state stays consistent: delay positions inside their lines, the predelay
    position inside its ring, the clear progress within the buffers;
  - every block it received was released exactly once and only those were transmitted;
  - each reverb's state is consistent: positions in range, filter states finite;
  - after garbage settings are replaced by normal ones, a reverb produces sound again
    (the probe chooses Freeverb, Plate or Spring: the stock reverb itself stays silent after a NaN
    setting, in stock firmware too, as its filter states stay NaN until a restart).
It also reports the deepest stack the patch used and the most instructions in a block.

--delay: the same through the delay object's update() (tools/emu/reverb_rig.py's
DelayRig), with the Master Delay set through the stock setters to random values (Time
mostly up to 800 ms, sometimes up to 4 s, Feedback, Filter, PingPong) and its fields to
garbage (Time 0 or huge, Feedback 255, any Filter coefficient), the bypass flag toggled,
a missing send block or output blocks, the three buffers moved to other memory and back,
the Pattern-screen start, the start-up reset and the delay choice (Stock, Tape, BBD,
"Gated", or a
value out of range) at random. After every block: no fault and a bounded instruction
count; every write by the patch's code went to its own RAM, the three delay buffers in
use, the output blocks, the stock delay's filter states or the stack; every block handed
to it was released exactly once and only those were transmitted; the Tape's, the
BBD's and "Gated"'s states are consistent (record position on the line, delay within its range, finite
filter states). After garbage, once normal settings are back and the chosen delay has
glided to the new Time, an impulse gives an echo again.
"""
from __future__ import annotations

import json
import math
import random
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reverb_rig as tf  # noqa: E402
from machine import STACK_TOP  # noqa: E402
from patch_ptf import bl_target  # noqa: E402

REVERB = tf.REVERB
OBJ_SIZE = 0x94
BUF_BYTES = 32768
MAX_INSNS = 400_000
NAN, INF = float("nan"), float("inf")
GARBAGE = [NAN, INF, -INF, -1.0, -1e9, 2.0, 1e9, 1e-40]
FILTERS = (0x60, 0x6C)              # the stock reverb's filter states, zeroed on a switch to it


class Session:
    def __init__(self, ptf: Path, seed: int):
        self.rnd = random.Random(seed)
        self.seed = seed
        self.rig = tf.Rig(ptf, booted=False, reverb=1)
        m = self.m = self.rig.m
        self.where = tf.ram_map(m, ptf)
        code = next(q for q in json.loads(ptf.with_suffix(".map.json").read_text())["patches"]
                    if q["name"] == "masterfx")
        self.lo, self.hi = (int(x, 16) for x in code["code"])
        self.ram = int(code["ram"]["start"], 16), int(code["ram"]["end"], 16)
        self.bufs = [m.r32(REVERB + tf.BUF_L), m.r32(REVERB + tf.BUF_R)]
        self.alt_bufs = [m.alloc(BUF_BYTES, 4), m.alloc(BUF_BYTES, 4)]
        self.errors: list[str] = []
        self.deepest = STACK_TOP
        self.most_insns = 0
        self.block = 0
        self.events: dict[str, int] = {}
        # a new start: the patch's hook on the Config menu constructor's last item, which
        # chains to the stock cfg_menu_item (stubbed: the menu is built once per machine)
        m.stub_function(0x601183E8, lambda mm: 0)
        m.stub_function(0x60118394, lambda mm: 0)    # cfg_menu_folder (the patch adds one)
        image = bytes(m.uc.mem_read(0x60000000, 0x200000))
        self.restart_hook = bl_target(image, 0x60000000, 0x601193E6)
        self.received = self.rig.received_blocks      # kept up to date by the rig's stubs
        self.released = self.rig.released

        # a write watch on everything the patch shouldn't write
        def on_write(uc, access, address, size, value, user):
            pc = uc.reg_read(UC_ARM_REG_PC)
            if not (self.lo <= pc < self.hi):
                return                       # the stock code's own writes
            if STACK_TOP - 0x4000 <= address < STACK_TOP:
                self.deepest = min(self.deepest, address)
                return
            if self.ram[0] <= address < self.ram[1]:
                return
            if REVERB + tf.LEVEL <= address < REVERB + tf.LEVEL + 4:
                return
            if REVERB + FILTERS[0] <= address < REVERB + FILTERS[1]:
                return
            for b in self.current_bufs():
                if b <= address < b + BUF_BYTES:
                    return
            if any(blk + 4 <= address < blk + 4 + 2 * tf.BLOCK for blk in self.rig.blocks):
                return
            self.errors.append(f"block {self.block}: write of {size} bytes at {address:#x} from {pc:#x}")
        for lo, hi in ((0x20000000, 0x20058000), (0x20200000, 0x20208000), (0x80000000, 0x84000000),
                       (0x00000000, 0x00020000)):
            m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=lo, end=hi - 1)

    def current_bufs(self) -> list[int]:
        return [self.m.r32(REVERB + tf.BUF_L), self.m.r32(REVERB + tf.BUF_R)]

    # -- signals ------------------------------------------------------------------
    def signal(self, kind: str, level: int) -> tuple[list[int], list[int]]:
        r, n = self.rnd, tf.BLOCK
        if kind == "silence":
            return [0] * n, [0] * n
        if kind == "noise":
            return [int(r.uniform(-level, level)) for _ in range(n)], [int(r.uniform(-level, level)) for _ in range(n)]
        if kind == "dc":
            return [32767] * n, [-32768] * n
        if kind == "sine":
            ph = r.random() * 6.283
            s = [int(32000 * math.sin(ph + 0.07 * i)) for i in range(n)]
            return s, s[::-1]
        if kind == "impulse":
            s = [0] * n
            s[r.randrange(n)] = 32767
            return s, list(s)
        raise ValueError(kind)

    # -- events -------------------------------------------------------------------
    def set_f32(self, off: int, v: float) -> None:
        self.m.write(REVERB + off, struct.pack("<f", v))

    def random_settings(self, garbage: bool) -> None:
        r = self.rnd
        for off in (tf.SIZE, tf.LP, tf.DIFFUSION, tf.AMOUNT, tf.INPUT_GAIN):
            self.set_f32(off, r.choice(GARBAGE) if garbage and r.random() < 0.5 else r.random())
        pre = r.choice([-100000, -1, 5001, 2 ** 31 - 1]) if garbage and r.random() < 0.5 else r.randrange(5001)
        self.m.w32(REVERB + tf.PREDELAY, pre)

    def normal_settings(self) -> None:
        self.rig.settings(size=0.7, damping=0.3, predelay=0.0, diffusion=0.6, amount=0.8, gain=0.1)

    # -- checks -------------------------------------------------------------------
    def check_state(self) -> None:
        """Each reverb's state, if it is laid out: positions in range, filter states finite."""
        m = self.m
        raw = bytes(m.uc.mem_read(self.where["fv"], 288))
        if struct.unpack_from("<I", raw, 0)[0] == 0x56524654:
            lines = [struct.unpack_from("<HHHH", raw, 12 + 8 * i) for i in range(24)]
            for i, (start, length, pos, _) in enumerate(lines):
                if not (pos < length and start + length <= 16384):
                    self.errors.append(f"block {self.block}: Freeverb line {i} start {start} len {length} pos {pos}")
            stores = struct.unpack_from("<16f", raw, 12 + 8 * 24)
            pre_pos = struct.unpack_from("<H", raw, 12 + 8 * 24 + 64)[0]
            if pre_pos >= 5001 or not all(math.isfinite(v) for v in stores):
                self.errors.append(f"block {self.block}: Freeverb predelay position {pre_pos}, stores {stores}")
        raw = bytes(m.uc.mem_read(self.where["plate"], 36))
        if struct.unpack_from("<I", raw, 0)[0] == 0x54414C50:
            wp = struct.unpack_from("<I", raw, 12)[0]
            floats = struct.unpack_from("<5f", raw, 16)
            if wp >= 16384 or not all(math.isfinite(v) for v in floats):
                self.errors.append(f"block {self.block}: Plate position {wp}, states {floats}")
        raw = bytes(m.uc.mem_read(self.where["spring"], 108))
        if struct.unpack_from("<I", raw, 0)[0] == 0x47525053:
            pre_pos, phase, w0, w1 = struct.unpack_from("<4H", raw, 12)
            floats = struct.unpack_from("<22f", raw, 20)
            if pre_pos >= 5001 or phase >= 4 or w0 >= 512 or w1 >= 512 or not all(math.isfinite(v) for v in floats):
                self.errors.append(f"block {self.block}: Spring positions {pre_pos} {phase} {w0} {w1}, states {floats}")
        raw = bytes(m.uc.mem_read(self.where["shimmer"], 60))
        if struct.unpack_from("<I", raw, 0)[0] == 0x4D4D4853:
            wp, h0, h1 = struct.unpack_from("<IHH", raw, 12)
            floats = struct.unpack_from("<10f", raw, 20)
            if wp >= 16384 or not (1 <= h0 <= 2048 and 1 <= h1 <= 2048) or not all(math.isfinite(v) for v in floats):
                self.errors.append(f"block {self.block}: Shimmer position {wp}, heads {h0} {h1}, states {floats}")

    def note(self, what: str) -> None:
        self.events[what] = self.events.get(what, 0) + 1

    def run(self, blocks: int) -> None:
        r, m = self.rnd, self.m
        self.normal_settings()
        kind, level = "noise", 8000
        garbage_until = -1
        recovered_checks = 0
        for self.block in range(blocks):
            if r.random() < 0.02:
                kind = r.choice(["silence", "noise", "noise", "dc", "sine", "impulse"])
                level = r.choice([200, 3000, 16000, 32767])
            ev = r.random()
            if ev < 0.01:
                g = r.random() < 0.5
                self.random_settings(garbage=g)
                self.note("garbage settings" if g else "random settings")
                garbage_until = self.block + 1
            elif ev < 0.013:
                m.w8(REVERB + tf.BYPASS, r.choice([0, 1]))
                self.note("bypass")
            elif ev < 0.016:
                self.rig.give = [r.random() < 0.5, r.random() < 0.5]
                self.note("missing blocks")
            elif ev < 0.018:
                self.rig.give = [True, True]
            elif ev < 0.0185:                # buffers moved, or back
                pair = self.alt_bufs if self.current_bufs() == self.bufs else self.bufs
                m.w32(REVERB + tf.BUF_L, pair[0])
                m.w32(REVERB + tf.BUF_R, pair[1])
                self.note("buffers moved")
            elif ev < 0.0195:
                m.call(m.r32(tf.PATTERN_UPDATE_SLOT), 0x20021770)      # the Pattern screen runs
                self.note("pattern screen")
            elif ev < 0.0197:
                m.call(self.restart_hook | 1, 0, 0, 0, 0, 0, 0)          # a new start
                self.note("restart")
            elif ev < 0.0205:
                self.rig.choose(r.choice([0, 1, 2, 3, 4, 1, 2, 3, 4, 5, 0xFF]))
                self.note("reverb choice")
            elif ev < 0.0225 and garbage_until >= 0:
                self.normal_settings()                                  # back to normal knobs
                m.w8(REVERB + tf.BYPASS, 0)
                self.rig.give = [True, True]
                recovered_checks = 200
                garbage_until = -1
            left, right = self.signal(kind, level)
            for blk, sig in zip(self.rig.blocks, (left, right)):
                m.write(blk + 4, struct.pack(f"<{tf.BLOCK}h", *sig))
            self.received.clear()
            self.released.clear()
            self.rig.sent.clear()
            count = [0]
            sample = self.block % 50 == 0
            if sample:
                h = m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: count.__setitem__(0, count[0] + 1))
            try:
                m.call(self.rig.update, REVERB, max_insns=MAX_INSNS)
            except RuntimeError as e:
                self.errors.append(f"block {self.block}: {e}")
                break
            finally:
                if sample:
                    m.uc.hook_del(h)
                    self.most_insns = max(self.most_insns, count[0])
            if sorted(self.released) != sorted(self.received):
                self.errors.append(f"block {self.block}: received {self.received}, released {self.released}")
            if any(b not in self.received for b, _ in self.rig.sent):
                self.errors.append(f"block {self.block}: transmitted a block it didn't receive")
            self.check_state()
            if recovered_checks:
                recovered_checks -= 1
                if recovered_checks == 0:
                    probe = self.recovery_probe()
                    self.note("recovery probes")
                    if not probe:
                        self.errors.append(f"block {self.block}: no reverb after normal settings came back")
            if len(self.errors) > 5:
                break

    def recovery_probe(self) -> bool:
        """With normal settings and a booted state: does a burst produce a tail?"""
        m = self.m
        m.call(m.r32(tf.PATTERN_UPDATE_SLOT), 0x20021770)
        self.normal_settings()
        m.w8(REVERB + tf.BYPASS, 0)
        self.rig.give = [True, True]
        m.w32(REVERB + tf.BUF_L, self.bufs[0])
        m.w32(REVERB + tf.BUF_R, self.bufs[1])
        n = tf.BLOCK
        self.rig.choose(self.rnd.choice([1, 2, 3, 4]))              # Freeverb, Plate, Spring or Shimmer
        self.rig.run([0] * (n * 20), [0] * (n * 20))                 # any clear or switch finishes
        burst = tf.noise_burst(n * 8, n * 8, self.seed)
        self.rig.run(burst, burst)
        out_l, _ = self.rig.run([0] * (n * 40), [0] * (n * 40))
        return tf.rms(out_l) > 5


DL = tf.DELAY
DL_STATES = (0x3C, 0x54)            # the stock delay's filter states, zeroed on a switch to it
DL_MAX_INSNS = 600_000
TAPE_MAGIC = 0x45504154
BBD_MAGIC = 0x44444242
DL_ACTIVE = 0x24                    # masterfx's state + this: the delay running


class DelaySession:
    def __init__(self, ptf: Path, seed: int):
        self.rnd = random.Random(seed)
        self.seed = seed
        self.rig = tf.DelayRig(ptf, booted=False, delay=1)
        m = self.m = self.rig.m
        code = next(q for q in json.loads(ptf.with_suffix(".map.json").read_text())["patches"]
                    if q["name"] == "masterfx")
        self.lo, self.hi = (int(x, 16) for x in code["code"])
        self.ram = int(code["ram"]["start"], 16), int(code["ram"]["end"], 16)
        where = tf.ram_map(m, ptf)
        self.tape, self.bbd, self.gated = where["tape"], where["bbd"], where["gated"]
        self.bufs = list(self.rig.bufs)
        self.alt_bufs = [m.alloc(tf.DL_BUF_BYTES, 4) for _ in tf.DL_BUFS]
        self.errors: list[str] = []
        self.deepest = STACK_TOP
        self.most_insns = 0
        self.block = 0
        self.events: dict[str, int] = {}
        m.stub_function(0x601183E8, lambda mm: 0)
        m.stub_function(0x60118394, lambda mm: 0)
        image = bytes(m.uc.mem_read(0x60000000, 0x200000))
        self.restart_hook = bl_target(image, 0x60000000, 0x601193E6)

        def on_write(uc, access, address, size, value, user):
            pc = uc.reg_read(UC_ARM_REG_PC)
            if not (self.lo <= pc < self.hi):
                return
            if STACK_TOP - 0x4000 <= address < STACK_TOP:
                self.deepest = min(self.deepest, address)
                return
            if self.ram[0] <= address < self.ram[1]:
                return
            if DL + DL_STATES[0] <= address < DL + DL_STATES[1]:
                return
            for b in self.current_bufs():
                if b <= address < b + tf.DL_BUF_BYTES:
                    return
            if any(blk + 4 <= address < blk + 4 + 2 * tf.BLOCK for blk in self.rig.outs):
                return
            self.errors.append(f"block {self.block}: write of {size} bytes at {address:#x} from {pc:#x}")
        for lo, hi in ((0x20000000, 0x20058000), (0x20200000, 0x20208000), (0x80000000, 0x84000000),
                       (0x00000000, 0x00020000)):
            m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=lo, end=hi - 1)

    def current_bufs(self) -> list[int]:
        return [self.m.r32(DL + off) for off in tf.DL_BUFS]

    def note(self, what: str) -> None:
        self.events[what] = self.events.get(what, 0) + 1

    def signal(self, kind: str, level: int) -> list[int]:
        r, n = self.rnd, tf.BLOCK
        if kind == "silence":
            return [0] * n
        if kind == "noise":
            return [int(r.uniform(-level, level)) for _ in range(n)]
        if kind == "dc":
            return [32767] * n
        if kind == "sine":
            ph = r.random() * 6.283
            return [int(32000 * math.sin(ph + 0.07 * i)) for i in range(n)]
        s = [0] * n
        s[r.randrange(n)] = 32767
        return s

    def random_settings(self) -> None:
        r = self.rnd
        t_ms = r.uniform(1, 800) if r.random() < 0.9 else r.uniform(800, 4000)
        self.rig.settings(time_ms=t_ms, feedback=r.uniform(0, 1.2), filter_hz=r.uniform(10, 22000),
                          pingpong=r.choice([0, 1]))

    def garbage(self) -> None:
        r, m = self.rnd, self.m
        m.w32(DL + tf.DL_TIME, r.choice([0, 1, 0xFFFFFFFF, 0x7FFFFFFF, 1 << 18, r.getrandbits(32)]))
        m.w8(DL + tf.DL_FEEDBACK, r.choice([253, 254, 255, r.getrandbits(8)]))
        m.w32(DL + tf.DL_POLE, r.getrandbits(32))
        m.w8(DL + tf.DL_PINGPONG, r.getrandbits(8))

    def check_state(self) -> None:
        """The tape's or the BBD's state, if laid out: positions in range, filter states finite."""
        for label, addr, magic, nfloats in (("tape", self.tape, TAPE_MAGIC, 17), ("BBD", self.bbd, BBD_MAGIC, 25)):
            raw = bytes(self.m.uc.mem_read(addr, 20 + 4 * nfloats))
            magic_, b0, b1, wpos, dint = struct.unpack_from("<5I", raw, 0)
            if magic_ != magic:
                continue
            floats = list(struct.unpack_from(f"<{nfloats}f", raw, 20))
            if label == "BBD":
                del floats[1]                    # coef_for, a delay in samples
            dfrac = floats[0]
            if not (wpos < (1 << 18) and 4 <= dint <= (1 << 18) - 511 and 0.0 <= dfrac < 1.0
                    and all(math.isfinite(v) for v in floats)):
                self.errors.append(f"block {self.block}: {label} state wpos {wpos} dint {dint} dfrac {dfrac} {floats[1:]}")
        raw = bytes(self.m.uc.mem_read(self.gated, 36))
        if struct.unpack_from("<I", raw, 0)[0] == 0x45544147:
            wpos, wp = struct.unpack_from("<II", raw, 12)
            floats = struct.unpack_from("<4f", raw, 20)
            if wpos >= (1 << 18) or wp >= (1 << 18) or not all(math.isfinite(v) for v in floats):
                self.errors.append(f"block {self.block}: \"Gated\" positions {wpos} {wp}, states {floats}")

    def run_block(self, sig: list[int]) -> None:
        m = self.m
        m.write(self.rig.inp + 4, struct.pack(f"<{tf.BLOCK}h", *sig))
        self.rig.sent.clear()
        self.rig.released.clear()
        self.rig.handed.clear()
        self.rig._allocs = 0
        count = [0]
        sample = self.block % 50 == 0
        if sample:
            h = m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: count.__setitem__(0, count[0] + 1))
        try:
            m.call(self.rig.update, DL, max_insns=DL_MAX_INSNS)
        except RuntimeError as e:
            self.errors.append(f"block {self.block}: {e}")
        finally:
            if sample:
                m.uc.hook_del(h)
                self.most_insns = max(self.most_insns, count[0])
        if sorted(self.rig.released) != sorted(self.rig.handed):
            self.errors.append(f"block {self.block}: handed {self.rig.handed}, released {self.rig.released}")
        if any(b not in self.rig.handed for b, _ in self.rig.sent):
            self.errors.append(f"block {self.block}: transmitted a block it wasn't handed")
        self.check_state()

    def run(self, blocks: int) -> None:
        r, m = self.rnd, self.m
        self.rig.settings()
        kind, level = "noise", 8000
        garbage_until = -1
        probe_at = -1
        for self.block in range(blocks):
            if r.random() < 0.02:
                kind = r.choice(["silence", "noise", "noise", "dc", "sine", "impulse"])
                level = r.choice([200, 3000, 16000, 32767])
            ev = r.random()
            if ev < 0.006:
                self.random_settings()
                self.note("random settings")
            elif ev < 0.009:
                self.garbage()
                self.note("garbage settings")
                garbage_until = self.block
            elif ev < 0.011:
                m.w8(DL + tf.DL_BYPASS, r.choice([0, 0, 1]))
                self.note("bypass")
            elif ev < 0.014:
                self.rig.give_input = r.random() < 0.5
                self.rig.give_outputs = r.choice([0, 1, 2, 2])
                self.note("missing blocks")
            elif ev < 0.016:
                self.rig.give_input, self.rig.give_outputs = True, 2
            elif ev < 0.0165:
                pair = self.alt_bufs if self.current_bufs() == self.bufs else self.bufs
                for off, b in zip(tf.DL_BUFS, pair):
                    m.w32(DL + off, b)
                self.note("buffers moved")
            elif ev < 0.0175:
                m.call(m.r32(tf.PATTERN_UPDATE_SLOT), 0x20021770)
                self.note("pattern screen")
            elif ev < 0.0177:
                m.call(self.restart_hook | 1, 0, 0, 0, 0, 0, 0)
                self.note("restart")
            elif ev < 0.0187:
                self.rig.choose(r.choice([0, 1, 2, 3, 1, 2, 3, 4, 0xFF]))
                self.note("delay choice")
            elif ev < 0.0197 and garbage_until >= 0 and probe_at < 0:
                probe_at = self.block
            self.run_block(self.signal(kind, level))
            if probe_at == self.block:
                self.note("recovery probes")
                if not self.recovery_probe():
                    self.errors.append(f"block {self.block}: no echo after normal settings came back")
                garbage_until = probe_at = -1
            if len(self.errors) > 5:
                break

    def recovery_probe(self) -> bool:
        """Normal settings, Tape, everything handed out: does an impulse give an echo?"""
        m = self.m
        m.call(m.r32(tf.PATTERN_UPDATE_SLOT), 0x20021770)
        choice = self.rnd.choice([1, 2])                            # Tape or BBD
        self.rig.choose(choice)
        self.rig.give_input, self.rig.give_outputs = True, 2
        for off, b in zip(tf.DL_BUFS, self.bufs):
            m.w32(DL + off, b)
        self.rig.settings(time_ms=50.0, feedback=0.5, filter_hz=20000.0, pingpong=0, bypass=0)
        target = m.r32(DL + tf.DL_TIME)
        state, magic = (self.tape, TAPE_MAGIC) if choice == 1 else (self.bbd, BBD_MAGIC)
        for _ in range(3000):                       # any switches' clears, then the glide to 50 ms
            self.run_block([0] * tf.BLOCK)
            if (m.uc.mem_read(self.rig.mx + DL_ACTIVE, 1)[0] == choice and m.r32(state) == magic
                    and abs(m.r32(state + 16) - target) < 50):
                break
        self.run_block([20000] + [0] * (tf.BLOCK - 1))
        out = []
        for _ in range(30):
            self.run_block([0] * tf.BLOCK)
            got = {o: blk for blk, o in self.rig.sent}
            if 0 in got:
                out += struct.unpack(f"<{tf.BLOCK}h", bytes(m.uc.mem_read(got[0] + 4, 2 * tf.BLOCK)))
        return bool(out) and max(abs(v) for v in out) > 300


def main() -> int:
    args = sys.argv[1:]
    delay = "--delay" in args
    args = [a for a in args if a != "--delay"]
    ptf = Path(args[0])
    blocks = int(args[1]) if len(args) > 1 else 20000
    seeds = [int(s) for s in args[2:]] or [1]
    failed = 0
    for seed in seeds:
        s = DelaySession(ptf, seed) if delay else Session(ptf, seed)
        s.run(blocks)
        stack = STACK_TOP - 0x100 - s.deepest
        status = "PASS" if not s.errors else "FAIL"
        print(f"  {status}  seed {seed}: {s.block + 1} blocks, most instructions in a sampled block "
              f"{s.most_insns}, deepest stack {stack} bytes")
        print("        events: " + ", ".join(f"{k} {v}" for k, v in sorted(s.events.items())))
        for e in s.errors[:6]:
            print("        " + e)
        failed += bool(s.errors)
    print(f"{failed} failing session(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
