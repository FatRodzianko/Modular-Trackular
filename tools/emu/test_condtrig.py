"""Check the condtrig patch (conditional trigs): playback.

  python tools/emu/test_condtrig.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the condtrig-only build and with bundles that include it. The sequencer runs
tick by tick (seq_tick), MIDI instruments on the tracks, with fake MIDI ports that
record each note and the tick it went out; the patched seq_advance call of
app_audio_block (or the render loop's) runs before the ticks of each take, as the
audio block runs it, with no time to add, so only its check for a new take runs.

1. Sites: the Chance FX's random call (ITCM 0x30ce) and the count-in's playback start
   (ITCM 0x3752) go through ITCM stubs to the patch's hooks.
2. Chance 0..100 as stock: the same notes at the same ticks as stock firmware over 40
   passes (the random sequence is untouched).
3. Every condition, 101 (1st), 102 (!1s), 103..137 (1:2 ... 8:8), in FX1 and in FX2, on
   one-step patterns for 16 passes: each plays exactly on the passes its rule gives;
   138, 200 and 255 always play, as in stock firmware.
4. A step whose condition fails drops its note, the CC in its other column, a
   Micro-move's delayed note, and its sample-track and synth notes; a step whose
   condition holds sends them as without it. Two Chance FX: 1:2 with C 0 never plays,
   1:2 with C 100 plays as 1:2.
5. Counting per track: a 4-step pattern, a Break FX ending it early, performance mode
   with tracks of different lengths, backward, random and pattern-table play modes,
   playback started from a later step.
6. Counts back to 0: a new take after a stop (seen by the audio block's and the render
   loop's hooks), playback started again while playing through each of the four
   seq_play_from veneers, a count-in, Preview; in song mode a pattern repeated in the
   next slot keeps counting and another pattern starts from 0.
7. Entering them: the encoder edit (seq_change_selection_fx_value) takes a Chance cell
   from 100 into the conditions and stops at 137 (8:8), and back down to the
   percentages; every other FX type keeps the limits stock firmware gives it, also in a
   selection that mixes types; pad entry still gives 0..100.
8. Showing them: the pattern grid's FX1 and FX2 value texts show `1st`, `!1s`, `1:2` ...
   `8:8`, and stock firmware's text for everything else.
9. Stock firmware with such patterns: values 101..137 always play, show as numbers, and
   an encoder edit makes them 100.
9b. With Variable Track Length (tracklen) in the build: a short track's conditions count
   its own loops (each start of its loop, the last cut-short one too) against a model,
   for lengths 1, 5, 6 and the pattern's length - 1 together with a track without a
   length, lengths 48 and 100 in a 128-step pattern, playback from a later step, a Break FX ending the pattern early, and
   performance mode's backward play (which counts as without a length). Skipped on
   builds without tracklen.
10. Cost: the patch's own instructions per audio block at 800 BPM with a condition in
    FX1 and FX2 of every track (what it adds to stock's work: test_bundle_cost.py).
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_S0

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patch_ptf import bl_target  # noqa: E402
from layout import patch_code, patch_function  # noqa: E402
import seq_timing as st  # noqa: E402

SEQ = st.SEQ
PROJECT = st.PROJECT
CHANCE, CC_A, MICRO_MOVE, BREAK = 4, 8, 2, 13
AUDIO_CALL, RENDER_CALL = 0x600AA7F8, 0x600BC27A
CHANCE_SITE, COUNT_IN_SITE = 0x30CE, 0x3752
PLAY_FROM_VENEERS = (0x600AC600, 0x600AC690, 0x600ACD30, 0x600AD540)
SEQ_STOP, SEQ_PLAY_SELECTION = 0x600ACD90, 0x600AC3D0
FM_LOADED_PATTERN = 0x2001E488 + 0x130    # file manager: the preloaded pattern's number
VOICE_NOTE_ON, NOTE_ON_OUT = 0x5C04, 0x600ABB44     # sample voices, MIDI channels and synths
NOTE_OFF_OUT = 0x600ABBE8                           # synths (no synth engine in this rig)
ENGINE_CLICK = 0x42E4
SEL_FX_CHANGE, SEL_FX_SCALED = 0x600AF0F8, 0x600AF8E8  # encoder edit; pad entry (position 0..47)
FX_VALUE_TEXT_CELL = 0x600AFDC8
GRID_FX1_CALL, GRID_FX2_CALL = 0x600DF748, 0x600DF77C
MICRO_MOVE_T, VOLUME, SWING, TEMPO = 2, 18, 17, 15
STEP = 576                          # ticks per step
FIRST_MICRO = 12                    # the tick of a take's first micro-step
OWN_LIMIT = 3_800                   # own instructions per block: 1 % of a block at about 4 cycles each
TICKS_PER_BLOCK = 90                # at 800 BPM (30,720 ticks a second, 344.5 blocks)
NAMES = {101: "1st", 102: "!1s"}
for _b in range(2, 9):
    for _a in range(1, _b + 1):
        NAMES[103 + (_b - 1) * _b // 2 - 1 + _a - 1] = f"{_a}:{_b}"
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def holds(v: int, n: int) -> bool:
    """The rule a value stands for, on pass n (0 = the first)."""
    if v == 101:
        return n == 0
    if v == 102:
        return n >= 1
    if v > 137:
        return True
    a, b = (int(x) for x in NAMES[v].split(":"))
    return n % b == a - 1


class Rig:
    """The sequencer with MIDI instrument t + 1 (channel t + 1) on track t."""

    def __init__(self, ptf: Path, steps: int = 1, patched: bool = True):
        self.m = m = st.make(ptf)
        m.stub_function(ENGINE_CLICK, lambda mm: 0)
        m.w8(st.SETTINGS + 0x2C, 2)             # MIDI out: jack
        m.w8(PROJECT + 0xAA0 + 0x10 * 1 + 3, 74)    # channel 1: CC A = 74
        self.steps = steps
        self.cur, self.nxt = st.pattern(m, steps - 1), st.pattern(m, steps - 1)
        m.w32(SEQ + 0x14, self.cur)
        m.w32(SEQ + 0x18, self.nxt)
        image = bytes(m.uc.mem_read(0x60000000, 0x200000))
        self.audio = bl_target(image, 0x60000000, AUDIO_CALL) | 1 if patched else None
        self.render = bl_target(image, 0x60000000, RENDER_CALL) | 1 if patched else None
        self.tick0 = 0

    def step(self, track: int, step: int, fx1=(0, 0), fx2=(0, 0), note: int = 60, instr: int | None = None):
        for buf in (self.cur, self.nxt):
            st.set_step(self.m, buf, track, step, note, 48 + track if instr is None else instr, fx1, fx2)

    def block(self, render: bool = False) -> None:
        """The patched seq_advance call, with no time: only the patch's own check runs."""
        f = self.render if render else self.audio
        if f is not None:
            self.m.uc.reg_write(UC_ARM_REG_S0, 0)
            self.m.call(f, SEQ)

    def start(self, step: int = 0) -> None:
        self.m.call(st.SEQ_PLAY_FROM, SEQ, step)
        self.block()
        self.tick0 = self.m.ticks
        self.m.events.clear()

    def run(self, passes: int, extra: int = FIRST_MICRO) -> None:
        for _ in range(passes * self.steps * STEP + extra):
            self.m.ticks += 1
            self.m.call(st.SEQ_TICK, SEQ, max_insns=2_000_000)

    def notes(self) -> list[tuple[int, int, int]]:
        """(tick since start, channel, note) of each note sent."""
        out = []
        for tick, what in self.m.events:
            w = what.split()
            if w[1:3] == ["note", "on"]:
                out.append((tick - self.tick0, int(w[5][2:]), int(w[3])))
        return out

    def passes_played(self, channel: int) -> list[int]:
        """The passes (by tick) in which channel played."""
        return sorted({(t - FIRST_MICRO) // (self.steps * STEP) for t, c, _ in self.notes() if c == channel})


def site_checks(ptf: Path) -> None:
    print("Sites")
    stubs = json.loads(ptf.with_suffix(".map.json").read_text()).get("itcm_stubs", {"sites": []})
    sites = {int(x["at"], 16): x for x in stubs["sites"] if x["patch"] == "condtrig"}
    rig = Rig(ptf)
    itcm = bytes(rig.m.uc.mem_read(0, 0x20000))
    for at, what in ((CHANCE_SITE, "Chance FX: bl rand_range_q"), (COUNT_IN_SITE, "count-in: bl seq_play_from")):
        x = sites.get(at)
        ok = x is not None and bl_target(itcm, 0, at) == int(x["stub"], 16)
        if ok:
            stub = int(x["stub"], 16)
            lit = stub + (8 if at == CHANCE_SITE else 4)
            ok = struct.unpack_from("<I", itcm, lit)[0] == int(x["hook"], 16) | 1
        check(ok, f"ITCM {at:#x} ({what}) -> stub -> the condtrig hook")


def stock_chance_checks(ptf: Path, stock: Path) -> None:
    print("Chance 0..100: as stock firmware")
    runs = []
    for path, patched in ((ptf, True), (stock, False)):
        rig = Rig(path, steps=4, patched=patched)
        values = [0, 25, 50, 75, 100]
        for t in range(4):
            for s in range(4):
                rig.step(t, s, (CHANCE, values[(t + s) % 5]), (CHANCE, values[(t + 2 * s + 3) % 5]) if s % 2 else (0, 0))
        rig.start()
        rig.run(40)
        runs.append(rig.notes())
    check(runs[0] == runs[1] and runs[0], f"the same {len(runs[0])} notes at the same ticks as stock firmware")


def condition_checks(ptf: Path) -> None:
    print("Every condition, 16 passes of a one-step pattern")
    values = list(range(101, 138)) + [138, 200, 255]
    passes = 16
    for column in (0, 1):
        bad = []
        for i in range(0, len(values), 16):
            group = values[i:i + 16]
            rig = Rig(ptf)
            for t, v in enumerate(group):
                rig.step(t, 0, (CHANCE, v) if column == 0 else (0, 0), (CHANCE, v) if column == 1 else (0, 0))
            rig.start()
            rig.run(passes, extra=0)
            for t, v in enumerate(group):
                want = [n for n in range(passes) if holds(v, n)]
                if rig.passes_played(t + 1) != want:
                    bad.append(f"{NAMES.get(v, v)}: {rig.passes_played(t + 1)}")
        check(not bad, f"FX{column + 1}: {len(values)} values each play on exactly the passes of their rule"
              + (f" (wrong: {'; '.join(bad[:4])})" if bad else ""))


def drop_checks(ptf: Path) -> None:
    print("What a failed condition drops")
    rig = Rig(ptf)
    rig.step(0, 0, (CHANCE, 103), (CC_A, 77))           # 1:2 and a CC
    rig.step(1, 0, (MICRO_MOVE, 50), (CHANCE, 104))     # Micro-move 50 and 2:2
    rig.step(2, 0, (CHANCE, 103), (CHANCE, 0))          # 1:2 and C 0
    rig.step(3, 0, (CHANCE, 100), (CHANCE, 103))        # C 100 and 1:2
    rig.start()
    rig.run(6, extra=0)
    ccs = sorted({(t - FIRST_MICRO) // STEP for t, what in
                  [(t - rig.tick0, w) for t, w in rig.m.events] if " cc 74 77" in what})
    check(rig.passes_played(1) == [0, 2, 4] and ccs == [0, 2, 4],
          f"1:2 with a CC: note and CC on passes {rig.passes_played(1)} and {ccs} only")
    late = [(t - FIRST_MICRO) % STEP for t, c, _ in rig.notes() if c == 2]
    check(rig.passes_played(2) == [1, 3, 5] and late and all(x == late[0] and x > 0 for x in late),
          f"2:2 with Micro-move 50: delayed notes on passes {rig.passes_played(2)} only, all {late[0] if late else '?'} "
          f"ticks late")
    check(rig.passes_played(3) == [], "1:2 with C 0: never plays")
    check(rig.passes_played(4) == [0, 2, 4], f"C 100 with 1:2: plays as 1:2 ({rig.passes_played(4)})")

    for label, track, instr, func in (("sample track (instrument 1)", 0, 0, VOICE_NOTE_ON),
                                      ("synth (instrument 65)", 9, 64, NOTE_ON_OUT)):
        rig = Rig(ptf)
        calls: list[int] = []
        rig.m.stub_function(func, lambda mm, c=calls: c.append(mm.ticks) or 0)
        rig.m.stub_function(NOTE_OFF_OUT, lambda mm: 0)
        rig.step(track, 0, (CHANCE, 103), instr=instr)
        rig.start()
        rig.run(6, extra=0)
        got = sorted({(t - rig.tick0 - FIRST_MICRO) // STEP for t in calls})
        check(got == [0, 2, 4], f"{label} with 1:2: note-on on passes {got}")


def counting_checks(ptf: Path) -> None:
    print("Counting passes per track")
    rig = Rig(ptf, steps=4)
    rig.step(0, 1, (CHANCE, 103))                       # 1:2 on step 2
    rig.step(1, 3, (CHANCE, 108))                       # 1:4 on step 4
    rig.start()
    rig.run(8, extra=0)
    check(rig.passes_played(1) == [0, 2, 4, 6] and rig.passes_played(2) == [0, 4],
          f"4-step pattern: 1:2 on passes {rig.passes_played(1)}, 1:4 on {rig.passes_played(2)}")

    rig = Rig(ptf, steps=4)
    rig.step(0, 0, (CHANCE, 103))                       # 1:2 on step 1
    rig.step(5, 1, (BREAK, 1))                          # Break on step 2: the pattern is 2 steps long
    rig.start()
    rig.run(4, extra=0)                                 # 16 steps: 8 passes of 2 steps
    got = sorted({(t - FIRST_MICRO) // (2 * STEP) for t, c, _ in rig.notes() if c == 1})
    check(got == [0, 2, 4, 6], f"Break FX on step 2: 1:2 on the 2-step passes {got}")

    rig = Rig(ptf, steps=4)
    rig.step(2, 0, (CHANCE, 101))                       # 1st on step 1
    rig.step(2, 3, (CHANCE, 101))                       # and step 4
    rig.start(2)
    rig.run(3)
    got = sorted(t // STEP for t, c, _ in rig.notes() if c == 3)
    check(got == [1], f"playback from step 3: '1st' on step 4 of the first pass only (steps played {got})")

    rig = Rig(ptf, steps=4)
    m = rig.m
    m.w8(SEQ + 0x1D, 1)                                 # performance mode
    for t in range(16):
        m.w16(SEQ + 0x38 + 2 * t, 3)
    m.w16(SEQ + 0x38 + 2 * 1, 2)                        # track 2: 3 steps
    m.w8(SEQ + 0x68 * 2 + 0xB9, 1)                      # track 3: backward
    m.w8(SEQ + 0x68 * 3 + 0xB9, 2)                      # track 4: random
    m.w8(SEQ + 0x68 * 4 + 0xB9, 3)                      # track 5: the first pattern table
    for t in range(5):
        for s in range(4):
            rig.step(t, s, (CHANCE, 103))               # 1:2 on every step
    rig.start()
    rig.run(6, extra=0)                                 # 24 steps
    def by_pass(ch: int, length: int) -> list[int]:
        return sorted({(t - FIRST_MICRO) // STEP // length for t, c, _ in rig.notes() if c == ch})
    want = {1: (4, [0, 2, 4]), 2: (3, [0, 2, 4, 6]), 3: (4, [0, 2, 4]), 4: (4, [0, 2, 4]), 5: (4, [0, 2, 4])}
    got = {ch: by_pass(ch, n) for ch, (n, _) in want.items()}
    check(all(got[ch] == w for ch, (_, w) in want.items()),
          f"performance mode, 1:2 on every step: 4 steps {got[1]}, 3 steps {got[2]}, backward {got[3]}, "
          f"random {got[4]}, pattern table {got[5]}")


def reset_checks(ptf: Path) -> None:
    print("Counts back to 0")
    first = 101

    def fresh(render: bool = False) -> Rig:
        rig = Rig(ptf)
        rig.step(0, 0, (CHANCE, first))
        rig.start()
        rig.run(3, extra=0)
        return rig

    for render in (False, True):
        rig = fresh()
        rig.m.call(SEQ_STOP, SEQ)
        rig.block(render)                               # the hook sees playback stopped
        rig.m.call(st.SEQ_PLAY_FROM, SEQ, 0)           # stock call: not through a veneer
        rig.block(render)
        rig.tick0 = rig.m.ticks
        rig.m.events.clear()
        rig.run(2, extra=0)
        check(rig.passes_played(1) == [0], f"stop, then play ({'render loop' if render else 'audio block'} hook): "
              f"'1st' plays on the first pass again ({rig.passes_played(1)})")

    for v in PLAY_FROM_VENEERS:
        rig = fresh()
        rig.m.call(v, SEQ, 0)                           # while playing: no new take for the block hook
        rig.block()
        rig.tick0 = rig.m.ticks
        rig.m.events.clear()
        rig.run(2, extra=0)
        check(rig.passes_played(1) == [0], f"play again while playing, through veneer {v:#x}: "
              f"'1st' on the first pass ({rig.passes_played(1)})")

    rig = fresh()
    rig.m.call(SEQ_STOP, SEQ)
    for k, val in ((0xE6, 1), (0xE8, 0), (0xE9, 0)):    # count-in: 1 bar of 1
        rig.m.w8(st.SETTINGS + k, val)
    rig.m.w32(SEQ + 0x6F4, 0)
    rig.m.w8(SEQ + 0x6F8, 1)
    rig.m.events.clear()
    start = None
    for i in range(STEP * 4):                           # no block hook call: only the count-in's hook
        rig.m.ticks += 1
        rig.m.call(st.SEQ_TICK, SEQ, max_insns=2_000_000)
        if start is None and rig.m.uc.mem_read(SEQ + 0x2A, 1)[0]:
            start = rig.m.ticks
    played = sorted({(t - start) // STEP for t, c, _ in [(t, c, n) for t, c, n in
                     ((t, int(w.split()[5][2:]), 0) for t, w in rig.m.events if "note on" in w)] if c == 1}) \
        if start else None
    check(played == [0], f"count-in: '1st' on the first pass after it ({played})")

    rig = fresh()
    rig.m.call(SEQ_STOP, SEQ)
    rig.block()
    rig.m.write(SEQ, bytes([0, 0, 0, 0]))               # selection: step 1, track 1
    rig.m.call(SEQ_PLAY_SELECTION, SEQ, 1)              # Preview, looping
    rig.block()
    rig.tick0 = rig.m.ticks
    rig.m.events.clear()
    rig.run(3, extra=0)
    check(rig.passes_played(1) == [0], f"Preview (looping): '1st' on its first pass only ({rig.passes_played(1)})")

    song_checks(ptf)


def song_checks(ptf: Path) -> None:
    rig = Rig(ptf)
    m = rig.m
    data = {}
    for n in (1, 2):
        buf = st.pattern(m, 0)
        st.set_step(m, buf, 0, 0, 60 + n, 48, (CHANCE, 101))        # 1st
        st.set_step(m, buf, 1, 0, 60 + n, 49, (CHANCE, 104))        # 2:2
        data[n] = bytes(m.uc.mem_read(buf, 0x3030))
    m.write(rig.cur, data[1])
    m.write(PROJECT + 0x5C2, bytes([1, 1, 2, 0]))                   # playlist: A A B
    m.w8(PROJECT + 0x6C1, 0)
    m.w8(PROJECT + 0x6C8, 1)

    def request(mm):                                # the pattern "loads" at once, as the main loop would
        mm.write(struct.unpack("<I", mm.uc.mem_read(SEQ + 0x18, 4))[0], data[mm.arg(1)])
        mm.w8(FM_LOADED_PATTERN, mm.arg(1))         # the swap takes the playing pattern's number from it
        return 1
    m.stub_function(st.FM_REQUEST_PATTERN_LOAD, request)
    m.call(st.SEQ_PLAY_FROM, SEQ, 0)
    m.w8(SEQ + 0x1C, 1)                                             # song mode
    rig.block()
    rig.tick0 = m.ticks
    m.events.clear()
    rig.run(6, extra=0)
    patterns = [n - 84 for _, _, n in sorted({(t // STEP, c, n) for t, c, n in rig.notes()})]
    check(rig.passes_played(1) == [0, 2, 3, 5] and rig.passes_played(2) == [1, 4],
          f"song A A B A A B: '1st' on slots {rig.passes_played(1)}, 2:2 on {rig.passes_played(2)} "
          f"(patterns heard {''.join('AB'[n - 1] for n in patterns)})")


TL_MAGIC = 0x314C544D                # tracklen's state: "MTL1", then its lengths [256 patterns][16 tracks]


def tracklen_ram(ptf: Path) -> int | None:
    """tracklen's RAM in this build, or None when it isn't in it."""
    for p in json.loads(Path(ptf).with_suffix(".map.json").read_text())["patches"]:
        if p["name"] == "tracklen" and p["ram"]:
            return int(p["ram"]["start"], 16)
    return None


def loop_model(value: int, step: int, e: int, length: int, start: int, steps: int) -> list[int]:
    """The steps (counted from playback start at `start`) on which a condition on `step` of
    a track of length e plays: n is the number of loop starts after the first step played."""
    out, n = [], 0
    for q in range(start, start + steps):
        pos = q % length
        if q > start and pos % e == 0:
            n += 1
        if pos % e == step and holds(value, n):
            out.append(q - start)
    return out


def track_length_checks(ptf: Path) -> None:
    ram = tracklen_ram(ptf)
    if ram is None:
        print("Short tracks (Variable Track Length): not in this build, skipped")
        return
    print("Short tracks (Variable Track Length): conditions count the track's own loops")

    def rig_with(lengths: dict[int, int], steps: int = 16) -> Rig:
        rig = Rig(ptf, steps=steps)
        rig.m.w8(PROJECT + 0x6C8, 1)                    # the playing pattern: 1
        rig.m.write(ram, struct.pack("<I", TL_MAGIC) + bytes(256 * 16))
        for t, e in lengths.items():
            rig.m.w8(ram + 4 + 16 * 1 + t, e)
        return rig

    def played(rig: Rig, ch: int) -> list[int]:
        return sorted((t - FIRST_MICRO) // STEP for t, c, _ in rig.notes() if c == ch)

    cases = [(0, 6, 1, 103), (1, 1, 0, 108), (2, 15, 0, 104), (3, 5, 2, 102), (4, 16, 1, 103), (5, 6, 1, 101),
             (6, 6, 3, 105)]                            # (track, length, step, condition); 16: none
    rig = rig_with({t: e for t, e, _, _ in cases if e < 16})
    for t, _, s, v in cases:
        rig.step(t, s, (CHANCE, v))
    rig.start()
    rig.run(4, extra=0)
    for t, e, s, v in cases:
        want = loop_model(v, s, e, 16, 0, 64)
        got = played(rig, t + 1)
        check(got == want, f"length {e if e < 16 else 'none'}, {NAMES[v]} on step {s + 1}: "
                           f"steps {got[:8]}{' ...' if len(got) > 8 else ''}")

    rig = rig_with({0: 48, 1: 100}, steps=128)
    rig.step(0, 40, (CHANCE, 103))                      # 1:2 on step 41
    rig.step(1, 90, (CHANCE, 108))                      # 1:4 on step 91
    rig.start()
    rig.run(3, extra=0)
    for t, e, s, v in ((0, 48, 40, 103), (1, 100, 90, 108)):
        want = loop_model(v, s, e, 128, 0, 3 * 128)
        check(played(rig, t + 1) == want, f"128 steps, length {e}: {NAMES[v]} on step {s + 1} plays on steps "
                                          f"{played(rig, t + 1)}")

    rig = rig_with({0: 6})
    rig.step(0, 3, (CHANCE, 101))                       # 1st on step 4
    rig.step(0, 1, (CHANCE, 103))                       # 1:2 on step 2
    rig.start(9)
    rig.run(2, extra=-9 * STEP)
    want = sorted(set(loop_model(101, 3, 6, 16, 9, 23) + loop_model(103, 1, 6, 16, 9, 23)))
    check(played(rig, 1) == want, f"length 6, playback from step 10: 1st and 1:2 on {played(rig, 1)}")

    rig = rig_with({0: 3})
    rig.step(0, 1, (CHANCE, 103))                       # 1:2 on step 2
    rig.step(5, 7, (BREAK, 1))                          # Break on step 8: the pattern is 8 steps long
    rig.start()
    rig.run(2, extra=0)                                 # 32 steps: 4 passes of 8
    want = loop_model(103, 1, 3, 8, 0, 32)
    check(played(rig, 1) == want, f"length 3, Break FX on step 8: 1:2 on {played(rig, 1)}")

    got = []
    for lengths in ({}, {0: 2}):
        rig = rig_with(lengths, steps=4)
        rig.m.w8(SEQ + 0x1D, 1)                         # performance mode
        for t in range(16):
            rig.m.w16(SEQ + 0x38 + 2 * t, 3)
        rig.m.w8(SEQ + 0xB9, 1)                         # track 1: backward
        for s in range(4):
            rig.step(0, s, (CHANCE, 103))
        rig.start()
        rig.run(6, extra=0)
        got.append(played(rig, 1))
    check(got[0] == got[1], f"performance mode, backward, 1:2 on every step: length 2 counts as none ({got[1]})")


def cost_checks(ptf: Path) -> None:
    print("Cost (the patch's own instructions per block at 800 BPM, 16 tracks with a condition in FX1 and FX2)")
    ranges = [patch_code(ptf, "condtrig")]
    if tracklen_ram(ptf) is not None:                   # its call to tracklen's mt_tl_len counts here
        ranges.append(patch_function(ptf, "tracklen", "mt_tl_len"))
        print("  (with tracklen's mt_tl_len, which it calls on every track's every step)")
    for label, value in (("Chance 50", 50), ("1:2", 103), ("8:8", 137)):
        short = len(ranges) > 1                         # with tracklen: 2 steps, every track of length 1,
        rig = Rig(ptf, steps=2 if short else 1, patched=True)   # so each step is a loop start
        for t in range(16):
            rig.step(t, 0, (CHANCE, value), (CHANCE, value))
        if short:
            ram = tracklen_ram(ptf)
            rig.m.w8(PROJECT + 0x6C8, 1)
            rig.m.write(ram, struct.pack("<I", TL_MAGIC) + bytes(256 * 16))
            for t in range(16):
                rig.m.w8(ram + 4 + 16 + t, 1)
        rig.start()
        n = [0]
        hs = [rig.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: n.__setitem__(0, n[0] + 1), begin=lo, end=hi - 1)
              for lo, hi in ranges]
        blocks = []
        for tick in range(STEP * 2):
            if tick % TICKS_PER_BLOCK == 0:
                blocks.append(n[0])
            rig.m.ticks += 1
            rig.m.call(st.SEQ_TICK, SEQ, max_insns=2_000_000)
        blocks.append(n[0])
        for h in hs:
            rig.m.uc.hook_del(h)
        worst = max(b - a for a, b in zip(blocks, blocks[1:]))
        check(worst <= OWN_LIMIT, f"{label}: at most {OWN_LIMIT} of its own per block ({worst} at worst, "
                                  f"{n[0]} over two steps)")


def cell(m, buf, track: int, step: int, column: int) -> tuple[int, int]:
    a = buf + 0x1F + 0x301 * track + 6 * step + 2 * column
    return tuple(m.uc.mem_read(a, 2))


def encoder(m, buf, cells, column: int, delta: int) -> list[int]:
    """Set (track, step, type, value) cells, select their rectangle, turn the encoder."""
    for t, s_, typ, v in cells:
        st.set_step(m, buf, t, s_, 60, 48, (typ, v) if column == 0 else (0, 0), (typ, v) if column == 1 else (0, 0))
    steps, tracks = [c[1] for c in cells], [c[0] for c in cells]
    sel = m.alloc(0x20)
    m.write(sel, bytes([min(steps), min(tracks), max(steps), max(tracks)]) + bytes(0x10))
    m.w32(sel + 0x14, buf)
    m.call(SEL_FX_CHANGE, sel, column, delta & 0xFFFFFFFF, max_insns=2_000_000)
    return [cell(m, buf, t, s_, column)[1] for t, s_, _, _ in cells]


def ui_checks(ptf: Path, stock: Path) -> None:
    print("Entering conditions: the encoder")
    m, sm = Rig(ptf).m, Rig(stock, patched=False).m
    buf, sbuf = st.pattern(m, 15), st.pattern(sm, 15)
    for start, delta, want in ((100, 1, 101), (101, 1, 102), (102, 1, 103), (136, 5, 137), (137, 1, 137),
                               (101, -1, 100), (105, -10, 95), (95, 10, 105), (0, -1, 0), (50, 200, 137)):
        for column in (0, 1):
            got = encoder(m, buf, [(2, 3, CHANCE, start)], column, delta)[0]
            check(got == want, f"FX{column + 1} C {NAMES.get(start, start)} {delta:+d} -> "
                               f"{NAMES.get(got, got)} (want {NAMES.get(want, want)})")
    same = []
    for typ, start, delta in ((MICRO_MOVE_T, 100, 1), (VOLUME, 50, 60), (CC_A, 120, 20), (SWING, 75, 1),
                              (TEMPO, 200, 5), (MICRO_MOVE_T, 0, -1), (CHANCE + 1, 100, 5)):
        a = encoder(m, buf, [(4, 1, typ, start)], 0, delta)[0]
        b = encoder(sm, sbuf, [(4, 1, typ, start)], 0, delta)[0]
        same.append(a == b)
    check(all(same), "other FX types (m, V, a, I, T, n): the same limits as in stock firmware")
    got = encoder(m, buf, [(5, 0, CHANCE, 98), (6, 0, MICRO_MOVE_T, 98)], 0, 5)
    check(got == [103, 100], f"a selection with C 98 and m 98, +5: C {NAMES.get(got[0], got[0])}, m {got[1]}")
    sel = m.alloc(0x20)
    st.set_step(m, buf, 7, 2, 60, 48, (CHANCE, 105))
    m.write(sel, bytes([2, 7, 2, 7]) + bytes(0x10))
    m.w32(sel + 0x14, buf)
    m.call(SEL_FX_SCALED, sel, 0, 47, max_insns=2_000_000)
    check(cell(m, buf, 7, 2, 0)[1] == 100, f"pad entry (last pad) on a condition: C {cell(m, buf, 7, 2, 0)[1]}")

    print("Showing conditions: the pattern grid")
    image = bytes(m.uc.mem_read(0x60000000, 0x200000))
    calls = [bl_target(image, 0x60000000, GRID_FX1_CALL) | 1, bl_target(image, 0x60000000, GRID_FX2_CALL) | 1]
    out, sout = m.alloc(8), sm.alloc(8)
    m.w32(SEQ + 0x14, buf)
    sm.w32(SEQ + 0x14, sbuf)

    def text(mm, f, o, column, value, typ=CHANCE):
        b = buf if mm is m else sbuf
        st.set_step(mm, b, 3, 9, 60, 48, (typ, value) if column == 0 else (0, 0), (typ, value) if column == 1 else (0, 0))
        mm.write(o, b"\xaa" * 8)
        mm.call(f, SEQ, o, 4, column, 3, 9)
        return bytes(mm.uc.mem_read(o, 4))
    for column in (0, 1):
        names = {v: text(m, calls[column], out, column, v) for v in range(101, 138)}
        check(all(t == NAMES[v].encode() + b"\0" for v, t in names.items()),
              f"FX{column + 1}: 101..137 show {names[101][:3].decode()}, {names[102][:3].decode()}, "
              f"{names[103][:3].decode()} ... {names[137][:3].decode()}")
        cases = [(CHANCE, 0), (CHANCE, 50), (CHANCE, 100), (CHANCE, 138), (CHANCE, 255), (MICRO_MOVE_T, 105),
                 (VOLUME, 50), (0, 0)]
        ok = all(text(m, calls[column], out, column, v, typ) == text(sm, FX_VALUE_TEXT_CELL | 1, sout, column, v, typ)
                 for typ, v in cases)
        check(ok, f"FX{column + 1}: everything else as stock firmware shows it (C 0, 50, 100, 138, 255; m 105; "
                  f"V 50; no FX)")

    print("Stock firmware with conditions")
    rig = Rig(stock, patched=False)
    for t, v in enumerate((101, 102, 103, 120, 137)):
        rig.step(t, 0, (CHANCE, v))
    rig.start()
    rig.run(8, extra=0)
    check(all(rig.passes_played(t + 1) == list(range(8)) for t in range(5)),
          "values 101..137 play on every pass")
    check(text(sm, FX_VALUE_TEXT_CELL | 1, sout, 0, 103) == b"103\0", "and show as numbers (C 103: '103')")
    got = encoder(sm, sbuf, [(2, 3, CHANCE, 105)], 0, 1)[0]
    check(got == 100, f"an encoder turn on C 105 makes it C {got}")


def main() -> int:
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    site_checks(ptf)
    stock_chance_checks(ptf, stock)
    condition_checks(ptf)
    drop_checks(ptf)
    counting_checks(ptf)
    reset_checks(ptf)
    track_length_checks(ptf)
    ui_checks(ptf, stock)
    cost_checks(ptf)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
