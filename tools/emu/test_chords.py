"""Check the chords patch: Chord FX values 30..236 play the chord library.

  python tools/emu/test_chords.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the chords-only build and with bundles that include it. The sequencer runs
tick by tick (seq_tick) with MIDI instruments on the tracks and fake MIDI ports (USB and
DIN both on) that record each message and the tick it went out (test_legato's rig).

The oracle: a library id whose table entry holds stock chord k's mask must play exactly
as stock firmware plays chord k. The test writes stock masks into the patched image's
library (ids 201..229 = stock 1..29) and compares the two firmwares message for message.

1. The library: every id 30..236 on a MIDI channel plays the step's note and its
   intervals, lowest first, and the next note ends exactly those notes, in FX1 and in
   FX2. Ids 30..69 are the list in docs/CHORDS_PLAN.md 4.1; 70..236 are every other set
   of one to three intervals within 1..11, by size, then in digit order. Notes above 127
   are skipped. Ids 237..255 play as in stock firmware (the note alone).
2. Against stock firmware, through the oracle: random patterns (notes, OFF steps, every
   FX type but Arp and Glide, Chance only 0..100, as condtrig and legato give those a
   meaning, and Random FX value, which randomizes around the chord's own value) on
   tracks 9-16, the same messages at the same ticks, also after stop; ids 0..29 and
   237..255 unmapped. Every hook (the three seq_trigger_note calls and the note-off
   word) plays a library chord at least once.
3. The same, case by case: Micro-move, the instrument's MIDI delay, Roll, gate, OFF, CUT
   and FADE steps, Chance, Random note, Volume, roots 0, 120 and 127, FX2, Chord in
   both columns, Chord with G (legato keeps the stock order), mute and stop while a
   chord sounds, a song-mode pattern change, a sample instrument, and performance mode's
   step repeat (ITCM 0x22f8) with and without its transpose. Random FX value on a
   library chord (the same values on both firmwares): the stock chords stock firmware
   picks for that value.
4. Synth slots (tools/emu/synth_rig.py, S1 at Polyphony 4): the host gets the same note
   calls as for the stock chord with the same mask; " 59" on 60 starts 60, 65 and 69.
5. The Arp (ITCM 0x2428 and 0x32ce, arp_next_note_q): an id carrying stock chord k's mask
   plays exactly as stock chord k under every Arp value 1..33, with the Arp in FX1 and in
   FX2 (all messages, so the arp's notes and offs too). Every library id under two Arp
   values plays only the step's note and its intervals. After every call the table
   literal at ITCM 0xcc0 is stock's again and the arp state's chord value is restored.
   Unused values 237..255 stay chromatic, as in stock firmware.
6. Entering them: the encoder takes a Chord cell from 29 on into the library and stops
   at 236; turning left goes back; other FX types keep their limits, also in a mixed
   selection; pad entry still gives 0..29.
7. Showing them: the pattern grid's FX1 and FX2 texts show each id's intervals (" 38",
   "368", "  1"); everything else as stock firmware shows it.
8. Stock firmware with such patterns: ids 30..255 play the note alone with its note-off,
   their grid text stays within the 4-byte cell, an encoder turn makes them 29, and a
   pattern file holding every value 30..255 is written and read back unchanged.
9. Cost: the patch's own instructions per audio block at 800 BPM with a 4-note library
   chord on every step of 16 MIDI tracks.
"""
from __future__ import annotations

import itertools
import json
import random
import struct
import sys
import tempfile
import zlib
from collections import Counter
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R6

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patch_ptf import bl_target  # noqa: E402
from layout import patch_code, patch_function  # noqa: E402
import seq_timing as st  # noqa: E402
import synth_rig as sr  # noqa: E402
import test_condtrig as tc  # noqa: E402
import test_legato as tl  # noqa: E402

SEQ, PROJECT = st.SEQ, st.PROJECT
MICRO_MOVE, ROLL, CHANCE, RANDOM_NOTE = 2, 3, 4, 5
CHORD, RANDOM_FX, VOLUME, GLIDE, GATE, ARP = 14, 16, 18, 19, 20, 21
STOCK_TABLE = 0x60039BFC
ARP_LITERAL = 0xCC0                 # ITCM: arp_next_note_q's pointer to the chord table
ARP_CHORD = 0x44                    # arp state: the step's chord value
MUTE = 0x202046E0                   # + track: muted
TRK_REPEAT = 0xB8                   # track: performance step repeat (ITCM 0x22f8 re-triggers the note)
TRK_TRANSPOSE = 0xB0                # track: performance transpose on, amount
STEP = tl.STEP
FIRST_MICRO = 12
OWN_LIMIT = tl.OWN_LIMIT
TICKS_PER_BLOCK = tl.TICKS_PER_BLOCK
CH_FIRST, CH_LAST = 30, 236
MAPPED = 200                        # oracle: id MAPPED + k holds stock chord k's mask
HEX = "0123456789ABCDEF"

# docs/CHORDS_PLAN.md 4.1: the inversions asked for, then the other inversions of stock chords
FIRST = [" 38", " 59", " 49", " 58", "378", "459", "158", "479", "358", "259", "368", "359", "269",
         " 39", " 69", " 6A", " 46", " 16", " 5B", "469", "258", "458", "148", "468", "248", "26A",
         "478", "348", "159", "257", "35A", "58A", "357", "249", "59A", "457", "138", "267", "45A", "168"]
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def intervals(label: str) -> tuple[int, ...]:
    return tuple(HEX.index(c) for c in label.strip())


def label(iv: tuple[int, ...]) -> str:
    return "".join(HEX[i] for i in iv).rjust(3)


def stock_masks(stock: Path) -> list[int]:
    m = st.make(stock)
    return [struct.unpack("<H", bytes(m.uc.mem_read(STOCK_TABLE + 2 * i, 2)))[0] for i in range(30)]


def library(masks: list[int]) -> dict[int, tuple[int, ...]]:
    """id -> intervals, as the plan lays the library out."""
    stock = {tuple(i for i in range(1, 16) if m >> i & 1) for m in masks}
    first = [intervals(x) for x in FIRST]
    rest = [c for n in (1, 2, 3) for c in itertools.combinations(range(1, 12), n)
            if c not in stock and c not in first]
    return {CH_FIRST + i: iv for i, iv in enumerate(first + rest)}


def map_stock(rig_m, ptf: Path, masks: list[int]) -> None:
    """The oracle: ids MAPPED + k get stock chord k's mask in the patched image."""
    lib = patch_function(ptf, "chords", "ch_library")[0]
    for k in range(1, 30):
        rig_m.write(lib + 2 * (MAPPED + k - CH_FIRST), struct.pack("<H", masks[k]))


def mapped(steps):
    """The patched pattern for a stock one: Chord values 1..29 -> MAPPED + value."""
    out = []
    for s in steps:
        s = list(s)
        for i in range(4, len(s)):
            ty, v = s[i]
            if ty == CHORD and 1 <= v <= 29:
                s[i] = (ty, MAPPED + v)
        out.append(tuple(s))
    return out


def rig_pair(ptf: Path, stock: Path, masks: list[int], last_step: int = 15):
    a = tl.Rig(ptf, last_step, True)
    map_stock(a.m, ptf, masks)
    b = tl.Rig(stock, last_step, False)
    for r in (a, b):
        tl.skip_synth_notes(r)
    return a, b


def balanced(rig: tl.Rig) -> bool:
    on = Counter()
    for port in ("USB", "DIN"):
        for _, k, c, n in rig.msgs(port):
            on[port, c, n] += 1 if k == "on" else -1
    return all(v == 0 for v in on.values())


# ---- 1. the library -----------------------------------------------------------------

def library_checks(ptf: Path, lib: dict[int, tuple[int, ...]]) -> None:
    print("The library: what each id plays")
    ivs = list(lib.values())
    stock_ivs = {tuple(i for i in range(1, 16) if m >> i & 1) for m in MASKS}
    check(len(lib) == CH_LAST - CH_FIRST + 1, f"ids {CH_FIRST}..{CH_LAST}: {len(lib)} chords")
    check(len(set(ivs)) == len(ivs) and set(ivs) & stock_ivs == {intervals("378"), intervals("479")},
          "all different, and none a stock chord but ids 34 (378) and 37 (479), which repeat them on purpose")
    every = {c for n in (1, 2, 3) for c in itertools.combinations(range(1, 12), n)}
    check(every <= set(ivs) | stock_ivs, "with the stock chords: every set of 1 to 3 intervals within 1..11")
    rig = tl.Rig(ptf)
    empty = bytes(rig.m.uc.mem_read(st.pattern(rig.m, 15), 0x3030))
    ids = list(range(CH_FIRST, 256))
    bad = []
    for column in (0, 1):
        for i in range(0, len(ids), 16):
            group = ids[i:i + 16]
            rig.m.write(rig.cur, empty)
            for t, v in enumerate(group):
                fx = [(CHORD, v), (0, 0)] if column == 0 else [(0, 0), (CHORD, v)]
                rig.steps([(t, 0, 48, 48 + t, *fx), (t, 1, 40, 48 + t)])
            rig.start()
            rig.run(STEP + FIRST_MICRO + 1)
            rig.stop()
            msgs = rig.msgs()
            for t, v in enumerate(group):
                want = [48 + x for x in (0,) + lib.get(v, ())]
                ons = [n for tick, k, c, n in msgs if c == t + 1 and k == "on" and tick == FIRST_MICRO]
                offs = [n for tick, k, c, n in msgs if c == t + 1 and k == "off" and tick == FIRST_MICRO + STEP]
                if ons != want or offs != want:
                    bad.append((column + 1, v, ons, offs, want))
    check(not bad, f"ids {CH_FIRST}..255 in FX1 and FX2 on note 48: the library's notes, lowest first, and the "
                   f"next note ends them (237..255: the note alone){'; ' + str(bad[:3]) if bad else ''}")
    rig.m.write(rig.cur, empty)
    rig.steps([(0, 0, 120, 48, (CHORD, 31)), (1, 0, 127, 49, (CHORD, 236)), (2, 0, 125, 50, (CHORD, 35))])
    rig.start()
    rig.run(FIRST_MICRO + 1)
    rig.stop()
    got = [[n for _, k, c, n in rig.msgs() if c == ch and k == "on"] for ch in (1, 2, 3)]
    check(got == [[120, 125], [127], [125]], f"notes above 127 skipped: 120 ' 59', 127 '9AB', 125 '459' ({got})")
    check(balanced(rig), "and stop ends exactly the notes sent")


# ---- 2. and 3. against stock firmware --------------------------------------------------

def fuzz_steps(rng: random.Random, last: int):
    types = [0] * 8 + [t for t in range(1, 43) if t not in (GLIDE, ARP, RANDOM_FX)] + [CHORD] * 10
    steps = []
    for t in rng.sample(range(8, 16), 4):
        for s in range(last + 1):
            r = rng.random()
            if r < 0.15:
                continue
            note = rng.choice([-2, -3, -4]) if r < 0.22 else rng.randrange(30, 128)
            instr = rng.choice([48, 48, 49, 50]) if note >= 0 else rng.choice([48, 0])
            fx = []
            for _ in range(2):
                ty = rng.choice(types)
                if ty == CHORD:
                    v = rng.randrange(237, 256) if rng.random() < 0.1 else rng.randrange(30)
                else:
                    v = rng.randrange(256) if rng.random() < 0.2 else rng.randrange(101)
                fx.append((ty, min(v, 100) if ty == CHANCE else v))
            steps.append((t, s, note, instr, fx[0], fx[1]))
    return steps


class Entries:
    """Counts library chords reaching each hook's entry point."""

    NAMES = ("mt_ch_trigger_a", "mt_ch_trigger_b", "mt_ch_trigger_c", "mt_ch_note_off")

    def __init__(self, ptf: Path):
        self.ptf = ptf
        self.n = Counter()

    def attach(self, m) -> None:
        for name in self.NAMES:
            a = patch_function(self.ptf, "chords", name)[0]

            def hook(uc, a_, size, u, name=name):
                rec = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R2), 7))
                v = rec[3] if rec[2] == CHORD else rec[5] if rec[4] == CHORD else 0
                arp = rec[4] == ARP if rec[2] == CHORD else rec[2] == ARP
                if 48 <= rec[1] <= 66 and CH_FIRST <= v <= CH_LAST and not arp:
                    self.n[name] += 1
            m.uc.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)


def run_pair(ptf, stock, steps, n_steps, last=15, extra=None, between=None, entries=None):
    """Play the mapped pattern on the patched firmware and the stock one on stock."""
    runs = []
    for r, s in zip(rig_pair(ptf, stock, MASKS, last), (mapped(steps), steps)):
        if entries is not None and r.audio is not None:
            entries.attach(r.m)
        r.steps(s)
        if extra:
            extra(r)
        r.start()
        if between:
            between(r)
        else:
            r.run(STEP * n_steps)
        r.stop()
        runs.append(r)
    return runs


def stock_checks(ptf: Path, stock: Path, entries: Entries) -> None:
    print("Against stock firmware (the oracle): 40 random patterns")
    rng = random.Random(11)
    same = chords = 0
    for _ in range(40):
        last = rng.choice([3, 7, 15])
        steps = fuzz_steps(rng, last)
        chords += sum(1 for s in steps for ty, v in s[4:] if ty == CHORD and v)
        a, b = run_pair(ptf, stock, steps, rng.choice([6, 10, 18]), last, entries=entries)
        same += a.m.events == b.m.events
    check(same == 40, f"every message at the same tick as stock, also after stop ({same} of 40; "
                      f"{chords} chord steps)")


def case_checks(ptf: Path, stock: Path, entries: Entries) -> None:
    print("Against stock firmware: case by case")
    C5, C24, C13 = (CHORD, 5), (CHORD, 24), (CHORD, 13)

    def delay(r):
        r.m.w8(PROJECT + 0xAA0 + 0x10 * 1 + 9, 50)

    def mute(r):
        r.run(STEP // 2)
        r.m.w8(MUTE + 8, 1)
        r.run(STEP * 2)

    def stop_mid(r):
        r.run(STEP // 2)

    def repeat(rate, transpose=0):
        def between(r):
            r.m.w8(SEQ + 0x68 * 8 + TRK_REPEAT, rate & 0xFF)
            if transpose:
                r.m.write(SEQ + 0x68 * 8 + TRK_TRANSPOSE, bytes([1, transpose & 0xFF]))
            r.run(STEP * 2)
        return between

    def song(r):
        r.m.stub_function(tl.FM_REQUEST_PATTERN_LOAD, lambda mm: 1)
        r.m.write(PROJECT + 0x5C2, bytes([1, 2, 0]))
        r.m.w8(PROJECT + 0x6C1, 0)
        r.m.w8(SEQ + 0x1C, 1)

    cases = [
        ("plain", [(8, 0, 60, 48, C5), (8, 1, 62, 48)], 2, None, None),
        ("Micro-move 50", [(8, 0, 60, 48, C24, (MICRO_MOVE, 50)), (8, 1, 62, 48)], 2, None, None),
        ("Micro-move 100 into the next step", [(8, 0, 60, 48, (MICRO_MOVE, 100), C5), (8, 1, 62, 48, C24)], 3,
         None, None),
        ("the instrument's MIDI delay 50", [(8, 0, 60, 48, C5), (8, 1, 62, 48, C24)], 3, delay, None),
        ("Roll 1", [(8, 0, 60, 48, C24, (ROLL, 1)), (8, 1, 62, 48)], 2, None, None),
        ("Roll 8", [(8, 0, 60, 48, (ROLL, 8), C13), (8, 2, 62, 48)], 3, None, None),
        ("gate q 50", [(8, 0, 60, 48, C24, (GATE, 50)), (8, 2, 62, 48)], 3, None, None),
        ("an OFF step", [(8, 0, 60, 48, C5), (8, 1, -2, 48)], 2, None, None),
        ("a CUT step", [(8, 0, 60, 48, C5), (8, 1, -3, 48)], 2, None, None),
        ("a FADE step", [(8, 0, 60, 48, C5), (8, 1, -4, 48)], 2, None, None),
        ("Chance 0", [(8, 0, 60, 48, C5, (CHANCE, 0)), (8, 1, 62, 48)], 2, None, None),
        ("Chance 100", [(8, 0, 60, 48, (CHANCE, 100), C5), (8, 1, 62, 48)], 2, None, None),
        ("Random note 12", [(8, 0, 60, 48, C24, (RANDOM_NOTE, 12)), (8, 1, 62, 48, C5, (RANDOM_NOTE, 12))], 3,
         None, None),
        ("Volume 50", [(8, 0, 60, 48, C24, (VOLUME, 50)), (8, 1, 62, 48)], 2, None, None),
        ("root 0", [(8, 0, 0, 48, C24), (8, 1, 62, 48)], 2, None, None),
        ("root 120", [(8, 0, 120, 48, C24), (8, 1, 62, 48)], 2, None, None),
        ("root 127", [(8, 0, 127, 48, C24), (8, 1, 62, 48)], 2, None, None),
        ("FX2", [(8, 0, 60, 48, (0, 0), C24), (8, 1, 62, 48)], 2, None, None),
        ("Chord in both columns", [(8, 0, 60, 48, C5, C24), (8, 1, 62, 48, (CHORD, 0), C13)], 3, None, None),
        ("Chord with G (legato order stays stock)", [(8, 0, 60, 48, C5), (8, 1, 62, 48, (GLIDE, 0), C24),
                                                     (8, 2, 64, 48, (GLIDE, 0))], 3, None, None),
        ("a sample instrument", [(0, 0, 60, 0, C24), (0, 1, 62, 0)], 2, None, None),
        ("channel 16", [(9, 0, 60, 63, C24), (9, 1, 62, 63)], 2, None, None),
        ("mute while the chord sounds", [(8, 0, 60, 48, C24)], 0, None, mute),
        ("stop while the chord sounds", [(8, 0, 60, 48, C24)], 0, None, stop_mid),
        ("performance step repeat 2", [(8, 0, 60, 48, C24), (8, 1, 62, 48, C5)], 0, None, repeat(2)),
        ("performance step repeat 9, gate q 50", [(8, 0, 60, 48, C24, (GATE, 50))], 0, None, repeat(9)),
        ("performance step repeat 4, transpose +5", [(8, 0, 60, 48, C13)], 0, None, repeat(4, 5)),
        ("performance step repeat 4, transpose -7", [(8, 0, 60, 48, C24)], 0, None, repeat(4, -7)),
    ]
    for what, steps, n, extra, between in cases:
        a, b = run_pair(ptf, stock, steps, n, extra=extra, between=between, entries=entries)
        check(a.m.events == b.m.events and balanced(a) == balanced(b),
              f"{what}: as stock firmware ({len(a.msgs())} messages)")
    runs = []
    first, second = [(8, 0, 60, 48, C5), (8, 3, 62, 48, C24)], [(8, 0, 64, 48, C13)]
    for r in rig_pair(ptf, stock, MASKS, 3):
        patched = r.audio is not None
        if patched:
            entries.attach(r.m)
        r.steps(mapped(first) if patched else first)
        song(r)
        r.steps(mapped(second) if patched else second, r.nxt)
        r.m.call(st.SEQ_PLAY_FROM, SEQ, 0)
        r.m.w8(SEQ + 0x1C, 1)
        r.tick0 = r.m.ticks
        r.m.events.clear()
        r.run(STEP * 6)
        r.stop()
        runs.append(r)
    check(runs[0].m.events == runs[1].m.events and len(runs[0].msgs()) > 6,
          "song mode: a chord on the last step, then the next pattern's: as stock firmware")
    print("Random FX value (x) on a library chord: a random stock chord, as stock firmware picks it")
    for v, rfx in ((31, 5), (100, 40), (236, 255), (40, 0)):
        runs = []
        for path, patched in ((ptf, True), (stock, False)):
            r = tl.Rig(path, 0, patched)
            r.steps([(8, 0, 60, 48, (CHORD, v), (RANDOM_FX, rfx))])
            r.start()
            r.run(STEP * 12)
            r.stop()
            runs.append(r)
        sets = {tuple(n for t_, k, c, n in runs[0].msgs() if k == "on" and t_ == t) for t, *_ in runs[0].msgs()}
        check(runs[0].m.events == runs[1].m.events, f"Chord {v}, x {rfx}: as stock firmware over 12 passes "
                                                    f"({len(sets)} different chords)")
    print("Every hook plays library chords")
    for name in Entries.NAMES:
        check(entries.n[name] > 0, f"{name}: {entries.n[name]} library chords")


# ---- 4. and 5. synths, the Arp -----------------------------------------------------------

def synth_checks(ptf: Path, stock: Path) -> None:
    print("Synth slots (S1, Polyphony 4): the host's note calls")
    for k in (5, 13, 24):
        runs = []
        for path, patched in ((ptf, True), (stock, False)):
            rig = tl.synth_rig(path, sr.VAP, 4, {})
            if patched:
                map_stock(rig.m, ptf, MASKS)
            v = MAPPED + k if patched else k
            tl.synth_play(rig, [(0, 0, 60, 64, (CHORD, v)), (0, 1, 62, 64)], 2, render=False)
            runs.append(list(rig.m.events))
        check(runs[0] == runs[1] and len(runs[0]) > 4, f"stock chord {k}'s mask: as stock firmware ({runs[0][:5]} ...)")
    rig = tl.synth_rig(ptf, sr.VAP, 4, {})
    tl.synth_play(rig, [(0, 0, 60, 64, (CHORD, 31)), (0, 1, 62, 64)], 2, render=False)
    ons = [n for t, k, c, n in rig.m.events if k == "on" and t == FIRST_MICRO]
    check(ons == [60, 65, 69], f"' 59' on 60: note-ons 60, 65, 69 ({ons})")


class ArpWatch:
    """Checks every arp_next_note_q call through the hooks: when it returns (ITCM 0x242c,
    0x32d2), the table literal is stock's again and the arp state's chord value is the one
    the hook was entered with. Counts the library chords each hook got."""

    RETURNS = (0x242C, 0x32D2)                # r6 = the track; its arp state is r6 + 0x68

    def __init__(self, ptf: Path, m):
        self.n, self.calls, self.bad = Counter(), 0, []
        self.entered: dict[int, int] = {}
        for name in ("mt_ch_arp_a", "mt_ch_arp_b"):
            a = patch_function(ptf, "chords", name)[0]

            def enter(uc, a_, size, u, name=name):
                state = uc.reg_read(UC_ARM_REG_R1)
                v = uc.mem_read(state + ARP_CHORD, 1)[0]
                self.entered[state] = v
                if CH_FIRST <= v <= CH_LAST:
                    self.n[name] += 1
            m.uc.hook_add(UC_HOOK_CODE, enter, begin=a, end=a)
        for at in self.RETURNS:
            def back(uc, a_, size, u):
                state = uc.reg_read(UC_ARM_REG_R6) + 0x68
                self.calls += 1
                lit = struct.unpack("<I", bytes(uc.mem_read(ARP_LITERAL, 4)))[0]
                v = uc.mem_read(state + ARP_CHORD, 1)[0]
                if lit != STOCK_TABLE or v != self.entered.pop(state, None):
                    self.bad.append((a_, hex(lit), v))
            m.uc.hook_add(UC_HOOK_CODE, back, begin=at, end=at)


def arp_run(rig: tl.Rig, cases, n_steps: int = 4):
    """One case per track (MIDI channel t + 1): (chord value, Arp value, column of the
    Chord, root)."""
    rig.steps([(t, 0, root, 48 + t, *[(CHORD, v), (ARP, arp)][::1 if col == 0 else -1])
               for t, (v, arp, col, root) in enumerate(cases)]
              + [(t, 0, -1, 0) for t in range(len(cases), 16)])
    rig.start()
    rig.run(STEP * n_steps)
    rig.stop()
    return list(rig.m.events)


def chunks(items, n=16):
    return [items[i:i + n] for i in range(0, len(items), n)]


def arp_checks(ptf: Path, stock: Path, lib: dict[int, tuple[int, ...]]) -> None:
    print("The Arp over a library chord: the chord's notes, as stock arpeggiates its own")
    a, b = rig_pair(ptf, stock, MASKS, 3)
    lib_rig = tl.Rig(ptf, 3, True)                      # the library as built, nothing mapped
    watch = ArpWatch(ptf, a.m)
    lib_watch = ArpWatch(ptf, lib_rig.m)
    cases = [(k, arp, col, 48 + 3 * (k % 8)) for k in range(1, 30) for arp in range(1, 34) for col in (0, 1)]
    same = 0
    first = None
    for part in chunks(cases):
        got = arp_run(a, [(MAPPED + k, arp, col, root) for k, arp, col, root in part])
        want = arp_run(b, part)
        if got == want:
            same += 1
        elif first is None:
            first = part[0]
    n = len(chunks(cases))
    check(same == n, f"ids carrying stock chords 1..29 under every Arp value 1..33, in FX1 and FX2: every "
                     f"message at the same tick as stock ({len(cases)} cases on {n} runs of 16 tracks, "
                     f"{n - same} differ; first {first})")
    part = [(v, 9, col, 60) for v in (237, 255) for col in (0, 1)]
    got, want = arp_run(a, part), arp_run(b, part)
    ons = [nn for _, kk, c, nn in a.msgs() if kk == "on" and c == 1]
    check(got == want and len(set(ons)) == 12, f"Chord 237 and 255 (unused) with Arp: chromatic, as stock firmware "
                                               f"({ons[:8]} ...)")
    # the library's own ids: the arpeggio plays exactly the chord's notes
    wrong = []
    cases = [(v, arp, col, 60 - 5 * col) for v in lib for arp in (9, 20) for col in (0, 1)]
    for part in chunks(cases):
        arp_run(lib_rig, part)
        for t, (v, arp, col, root) in enumerate(part):
            notes = {nn for _, kk, c, nn in lib_rig.msgs() if kk == "on" and c == t + 1}
            if notes != {root, *(root + i for i in lib[v])}:
                wrong.append((v, arp, col, sorted(notes)))
    check(not wrong, f"ids {CH_FIRST}..{CH_LAST} under Arp 9 and 20, in FX1 and FX2: the step's note and the "
                     f"chord's intervals, no other notes ({len(cases)} cases, {len(wrong)} wrong {wrong[:3]})")
    calls, bad = watch.calls + lib_watch.calls, watch.bad + lib_watch.bad
    check(not bad and calls > 1000, f"after every arp_next_note_q call: the table literal is stock's and the chord "
                                    f"value restored ({calls} calls, {len(bad)} wrong {bad[:3]})")
    for name in ("mt_ch_arp_a", "mt_ch_arp_b"):
        n = watch.n[name] + lib_watch.n[name]
        check(n > 0, f"{name}: {n} library chords")


# ---- 6. and 7. entering and showing ----------------------------------------------------

def ui_checks(ptf: Path, stock: Path, lib: dict[int, tuple[int, ...]]) -> None:
    print("Entering chords: the encoder")
    m, sm = tl.Rig(ptf).m, tl.Rig(stock, patched=False).m
    buf, sbuf = st.pattern(m, 15), st.pattern(sm, 15)
    for start, delta, want in ((29, 1, 30), (30, -1, 29), (30, 1, 31), (20, 20, 40), (235, 1, 236), (236, 1, 236),
                               (230, 10, 236), (0, -1, 0), (100, -50, 50), (5, 1, 6)):
        for column in (0, 1):
            got = tc.encoder(m, buf, [(2, 3, CHORD, start)], column, delta)[0]
            check(got == want, f"FX{column + 1} Chord {start} {delta:+d} -> {got} (want {want})")
    same = []
    for typ, start, delta in ((MICRO_MOVE, 100, 1), (VOLUME, 50, 60), (8, 120, 20), (17, 75, 1), (15, 200, 5),
                              (MICRO_MOVE, 0, -1), (RANDOM_NOTE, 100, 5), (ARP, 30, 10)):
        a = tc.encoder(m, buf, [(4, 1, typ, start)], 0, delta)[0]
        b = tc.encoder(sm, sbuf, [(4, 1, typ, start)], 0, delta)[0]
        same.append(a == b)
    check(all(same), "other FX types (m, V, a, I, T, n, A): the same limits as in stock firmware")
    got = tc.encoder(m, buf, [(5, 0, CHORD, 27), (6, 0, VOLUME, 98)], 0, 5)
    check(got == [32, 100], f"a selection with Chord 27 and V 98, +5: Chord {got[0]}, V {got[1]}")
    sel = m.alloc(0x20)
    st.set_step(m, buf, 7, 2, 60, 48, (CHORD, 40))
    m.write(sel, bytes([2, 7, 2, 7]) + bytes(0x10))
    m.w32(sel + 0x14, buf)
    m.call(tc.SEL_FX_SCALED, sel, 0, 47, max_insns=2_000_000)
    check(tc.cell(m, buf, 7, 2, 0)[1] == 29, f"pad entry (last pad) on Chord 40: {tc.cell(m, buf, 7, 2, 0)[1]}")

    print("Showing chords: the pattern grid")
    image = bytes(m.uc.mem_read(0x60000000, 0x200000))
    calls = [bl_target(image, 0x60000000, tc.GRID_FX1_CALL) | 1, bl_target(image, 0x60000000, tc.GRID_FX2_CALL) | 1]
    out, sout = m.alloc(8), sm.alloc(8)
    m.w32(SEQ + 0x14, buf)
    sm.w32(SEQ + 0x14, sbuf)

    def text(mm, f, o, column, value, typ=CHORD):
        b = buf if mm is m else sbuf
        st.set_step(mm, b, 3, 9, 60, 48, (typ, value) if column == 0 else (0, 0), (typ, value) if column == 1 else (0, 0))
        mm.write(o, b"\xaa" * 8)
        mm.call(f, SEQ, o, 4, column, 3, 9)
        return bytes(mm.uc.mem_read(o, 8))
    for column in (0, 1):
        texts = {v: text(m, calls[column], out, column, v) for v in lib}
        bad = [(v, t) for v, t in texts.items() if t != label(lib[v]).encode() + b"\0" + b"\xaa" * 4]
        check(not bad, f"FX{column + 1}: ids 30..236 show their intervals ({texts[30][:3].decode()!r}, "
                       f"{texts[40][:3].decode()!r}, {texts[70][:3].decode()!r} ... {texts[236][:3].decode()!r})"
                       f"{'; ' + str(bad[:3]) if bad else ''}")
        cases = [(CHORD, 0), (CHORD, 5), (CHORD, 29), (CHORD, 237), (CHORD, 255), (CHANCE, 50), (MICRO_MOVE, 105),
                 (VOLUME, 50), (0, 0)]
        ok = all(text(m, calls[column], out, column, v, typ) == text(sm, tc.FX_VALUE_TEXT_CELL | 1, sout, column, v, typ)
                 for typ, v in cases)
        check(ok, f"FX{column + 1}: everything else as stock firmware shows it (Chord 0, 5, 29, 237, 255; C 50; "
                  f"m 105; V 50; no FX)")


# ---- 8. stock firmware with library chords ----------------------------------------------

def stock_firmware_checks(stock: Path) -> None:
    print("Stock firmware with library chords")
    rig = tl.Rig(stock, patched=False)
    for t, v in enumerate((30, 31, 69, 236, 255)):
        rig.steps([(t, 0, 60, 48 + t, (CHORD, v)), (t, 1, 62, 48 + t)])
    rig.start()
    rig.run(STEP + FIRST_MICRO + 1)
    got = [[(k, n) for _, k, c, n in rig.msgs() if c == t + 1] for t in range(5)]
    check(all(g == [("on", 60), ("off", 60), ("on", 62)] for g in got), "ids 30, 31, 69, 236, 255: the note alone, "
                                                                         "and its note-off")
    m = rig.m
    buf, out = st.pattern(m, 15), m.alloc(16)
    m.w32(SEQ + 0x14, buf)
    ok = True
    for v in range(30, 256):
        for column in (0, 1):
            st.set_step(m, buf, 3, 9, 60, 48, (CHORD, v) if column == 0 else (0, 0), (CHORD, v) if column == 1 else (0, 0))
            m.write(out, b"\xaa" * 16)
            m.call(tc.FX_VALUE_TEXT_CELL | 1, SEQ, out, 4, column, 3, 9)
            ok = ok and bytes(m.uc.mem_read(out + 4, 12)) == b"\xaa" * 12
    check(ok, "the grid text of 30..255 stays within the 4-byte cell")
    got = tc.encoder(m, buf, [(2, 3, CHORD, 40)], 0, 1)[0]
    check(got == 29, f"an encoder turn on Chord 40 makes it Chord {got}")

    import test_copypattern as tcp
    with tempfile.TemporaryDirectory() as work:
        m = st.make(stock)
        fs = tcp.CardFS(m, Path(work))
        crc_obj, crc_vt = m.alloc(8), m.alloc(8)
        m.w32(crc_obj, crc_vt)
        m.w32(crc_vt, m.add_stub(lambda mm: zlib.crc32(bytes(mm.uc.mem_read(mm.arg(1), mm.arg(2))))))
        m.w32(tcp.FM_CRC, crc_obj)
        data = tcp.new_pattern(128)
        for i, v in enumerate(range(30, 256)):
            t, s = divmod(i, 16)
            o = 0x1C + tcp.TRACK * t + 1 + 6 * s
            data[o:o + 6] = bytes([60, 48, CHORD, v, CHORD, 255 - (v - 30)])
        src, dst, path = m.alloc(tcp.SIZE), m.alloc(tcp.SIZE), m.alloc(64)
        m.write(src, bytes(data))
        m.write(path, b"Workspace/chords.mtp\0")
        m.call(tcp.WRITE_PATTERN, tcp.FM, path, src)
        m.call(tcp.READ_PATTERN, tcp.FM, path, dst)
        back = bytes(m.uc.mem_read(dst, tcp.SIZE))
        check("Workspace/chords.mtp" in fs.overlay and back[0x1C:0x302C] == bytes(data[0x1C:0x302C]),
              "a pattern file with Chord values 30..255 in both columns: written and read back unchanged")


# ---- 9. cost -----------------------------------------------------------------------------

def cost_checks(ptf: Path) -> None:
    print("Cost (the patch's own instructions per block at 800 BPM, 16 MIDI tracks, '459' on every step)")
    lo, hi = patch_code(ptf, "chords")
    rig = tl.Rig(ptf)
    for t in range(16):
        for s in range(4):
            rig.steps([(t, s, 50 + 2 * s, 48 + t, (CHORD, 35))])
    rig.start()
    n = [0]
    h = rig.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: n.__setitem__(0, n[0] + 1), begin=lo, end=hi - 1)
    blocks = []
    for tick in range(STEP * 4):
        if tick % TICKS_PER_BLOCK == 0:
            blocks.append(n[0])
        rig.m.ticks += 1
        rig.m.call(st.SEQ_TICK, SEQ, max_insns=2_000_000)
    blocks.append(n[0])
    rig.m.uc.hook_del(h)
    worst = max(b - a for a, b in zip(blocks, blocks[1:]))
    notes = sum(1 for _, k, _, _ in rig.msgs() if k == "on")
    check(0 < worst <= OWN_LIMIT, f"at most {OWN_LIMIT} of its own per block ({worst} at worst, {n[0]} over four "
                                  f"steps, {notes} note-ons)")


MASKS: list[int] = []


def main() -> int:
    global MASKS
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    if not any(p["name"] == "chords" for p in json.loads(ptf.with_suffix(".map.json").read_text())["patches"]):
        raise SystemExit(f"{ptf}: no chords patch in it")
    MASKS = stock_masks(stock)
    lib = library(MASKS)
    library_checks(ptf, lib)
    entries = Entries(ptf)
    case_checks(ptf, stock, entries)
    stock_checks(ptf, stock, entries)
    synth_checks(ptf, stock)
    arp_checks(ptf, stock, lib)
    ui_checks(ptf, stock, lib)
    stock_firmware_checks(stock)
    cost_checks(ptf)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
