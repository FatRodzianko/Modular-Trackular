"""Check the legato patch: MIDI notes with a Glide FX (G) play legato.

  python tools/emu/test_legato.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the legato-only build and with bundles that include it. The sequencer runs
tick by tick (seq_tick) with MIDI instruments on the tracks and fake MIDI ports (USB and
DIN both on) that record each message and the tick it went out; the patched seq_advance
call of app_audio_block runs, with no time to add, every 13 ticks, as often as audio
blocks come at 120 BPM, so only the patch's own end-of-block work runs there.

1. Sites: the old note's note-off before a new note (ITCM 0x24da) and the step's note
   (ITCM 0x2fd8) go through ITCM stubs to the patch's hooks, which chain to the stock
   functions.
2. Without G, and on patterns of random notes, OFF steps and FX (every type, random
   values; Chance only 0..100, as condtrig gives values above it a meaning) on tracks 9-16: the same messages at the same ticks as stock firmware. With
   G in those patterns: the same messages at each tick as stock, in another order only
   on G steps, and no more notes left on after stop.
3. Legato: with G (any value, FX1 or FX2) the new note-on goes out before the old note's
   note-off, at the same tick: adjacent steps, after empty steps, in a looping pattern,
   across a song-mode pattern change, with a channel change.
4. Stock order kept: the same pitch and channel, the old note gated (q) or ended by an
   OFF step, Chord or Arp on either note, a sample or synth instrument on either side,
   G on a step without a note, G on the first note of a take.
5. With other FX: Micro-move and the instrument's MIDI delay move both messages, Volume
   keeps its velocity, Random note is compared after it picks the note, CC in the other
   column still goes out first, a Chance that fails sends nothing.
6. No stuck notes: stop after a legato note ends it as stock does; a kept note planted
   in the patch's RAM is ended by the end of the audio block and by the next note.
7. Two tracks on one channel: as stock firmware.
8. Cost: instructions per tick with G on every step of 16 MIDI tracks, against stock.
"""
from __future__ import annotations

import json
import random
import struct
import sys
from collections import Counter
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R3, UC_ARM_REG_S0

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patch_ptf import bl_target  # noqa: E402
from layout import patch_ram  # noqa: E402
import seq_timing as st  # noqa: E402

SEQ, PROJECT, SETTINGS = st.SEQ, st.PROJECT, st.SETTINGS
MICRO_MOVE, ROLL, CHANCE, RANDOM_NOTE, CC_A = 2, 3, 4, 5, 8
CHORD, VOLUME, GLIDE, GATE, ARP = 14, 18, 19, 20, 21
AUDIO_CALL = 0x600AA7F8
NOTE_OFF_SITE, TRIGGER_SITE = 0x24DA, 0x2FD8
SEQ_NOTE_OFF_Q, SEQ_TRIGGER_NOTE = 0x11A4, 0x149C
SEQ_STOP = 0x600ACD90
NOTE_ON_OUT, NOTE_OFF_OUT = 0x600ABB44, 0x600ABBE8   # seq_note_on_out_q / seq_note_off_out_q
FM_REQUEST_PATTERN_LOAD = 0x600BC938
ENGINE_CLICK = 0x42E4
STEP = 576
BLOCK = 13                          # ticks per audio block at 120 BPM
PER_TRACK_LIMIT = 150               # instructions the patch may add per track and step
G = (GLIDE, 0)
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Rig:
    """The sequencer, MIDI out on USB and DIN, the MIDI octave at 2 (notes unshifted)."""

    def __init__(self, ptf: Path, last_step: int = 15, patched: bool = True):
        self.m = m = st.make(ptf)
        m.stub_function(ENGINE_CLICK, lambda mm: 0)
        m.w8(SETTINGS + 0x2C, 3)
        m.w8(SETTINGS + 0x2F, 2)
        m.w8(PROJECT + 0xAA0 + 0x10 * 1 + 3, 74)    # channel 1: CC A = 74
        self.cur, self.nxt = st.pattern(m, last_step), st.pattern(m, last_step)
        m.w32(SEQ + 0x14, self.cur)
        m.w32(SEQ + 0x18, self.nxt)
        image = bytes(m.uc.mem_read(0x60000000, 0x200000))
        self.audio = bl_target(image, 0x60000000, AUDIO_CALL) | 1 if patched else None
        self.tick0 = 0

    def steps(self, steps, buf=None) -> None:
        for s in steps:
            st.set_step(self.m, buf or self.cur, *s)

    def block(self) -> None:
        if self.audio is not None:
            self.m.uc.reg_write(UC_ARM_REG_S0, 0)
            self.m.call(self.audio, SEQ)

    def start(self) -> None:
        self.m.call(st.SEQ_PLAY_FROM, SEQ, 0)
        self.block()
        self.tick0 = self.m.ticks
        self.m.events.clear()

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.m.ticks += 1
            self.m.call(st.SEQ_TICK, SEQ, max_insns=2_000_000)
            if self.m.ticks % BLOCK == 0:
                self.block()

    def stop(self) -> None:
        self.m.call(SEQ_STOP, SEQ, max_insns=5_000_000)

    def msgs(self, port: str = "DIN") -> list[tuple[int, str, int, int]]:
        """(tick since start, "on"/"off", channel, note) of the port's note messages."""
        out = []
        for tick, what in self.m.events:
            w = what.split()
            if w[0] == port and w[1] == "note":
                out.append((tick - self.tick0, w[2], int(w[5][2:]), int(w[3])))
        return out

    def left_on(self) -> int:
        on = Counter()
        for _, kind, ch, note in self.msgs("USB"):
            on[ch, note] += 1 if kind == "on" else -1
        return sum(v for v in on.values() if v > 0)


def skip_synth_notes(rig: Rig) -> None:
    """Synth notes (instruments 64-66) return at once: the rig has no synth engine."""
    def hook(uc, a, size, u):
        if uc.reg_read(UC_ARM_REG_R3):
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    for a in (NOTE_ON_OUT, NOTE_OFF_OUT):
        rig.m.uc.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)


def play(ptf: Path, steps, n_steps: int, last_step: int = 15, patched: bool = True, extra=None) -> Rig:
    rig = Rig(ptf, last_step, patched)
    rig.steps(steps)
    if extra:
        extra(rig)
    rig.start()
    rig.run(STEP * n_steps)
    return rig


def at(rig: Rig, step: int, port: str = "DIN") -> list[tuple[str, int, int]]:
    """The note messages of a step's first micro-step, in order."""
    t = 12 + STEP * step
    return [(k, c, n) for tick, k, c, n in rig.msgs(port) if tick == t]


def site_checks(ptf: Path) -> None:
    print("Sites")
    stubs = json.loads(ptf.with_suffix(".map.json").read_text()).get("itcm_stubs", {"sites": []})
    sites = {int(x["at"], 16): x for x in stubs["sites"] if x["patch"] == "legato"}
    rig = Rig(ptf)
    itcm = bytes(rig.m.uc.mem_read(0, 0x20000))
    for site, what in ((NOTE_OFF_SITE, "bl seq_note_off_q"), (TRIGGER_SITE, "bl seq_trigger_note")):
        x = sites.get(site)
        ok = x is not None and bl_target(itcm, 0, site) == int(x["stub"], 16)
        if ok:
            ok = struct.unpack_from("<I", itcm, int(x["stub"], 16) + 4)[0] == int(x["hook"], 16) | 1
        check(ok, f"ITCM {site:#x} ({what}) -> stub -> the legato hook")


def fuzz_steps(rng: random.Random, last: int, glide: bool):
    types = [0] * 8 + list(range(1, 43))
    if glide:
        types += [GLIDE] * 6
    else:
        types.remove(GLIDE)
    steps = []
    for t in rng.sample(range(8, 16), 4):
        for s in range(last + 1):
            r = rng.random()
            if r < 0.15:
                continue
            note = rng.choice([-2, -3, -4]) if r < 0.22 else rng.randrange(36, 84)
            instr = rng.choice([48, 48, 48, 49, 50]) if note >= 0 else rng.choice([48, 0])
            fx = []
            for _ in range(2):
                ty = rng.choice(types)
                v = rng.randrange(256) if rng.random() < 0.2 else rng.randrange(101)
                fx.append((ty, min(v, 100) if ty == CHANCE else v))   # above 100: condtrig's conditions
            steps.append((t, s, note, instr, fx[0], fx[1]))
    return steps


def stock_checks(ptf: Path, stock: Path) -> None:
    print("Against stock firmware: random patterns, 30 without G and 30 with G")
    rng = random.Random(7)
    same, legato_ok, swapped, not_more_on = 0, 0, 0, 0
    for glide in (False, True):
        for _ in range(30):
            last = rng.choice([3, 7, 15])
            steps = fuzz_steps(rng, last, glide)
            n = rng.choice([6, 10, 18])
            runs = []
            for path, patched in ((ptf, True), (stock, False)):
                rig = play(path, steps, n, last, patched)
                rig.stop()
                runs.append(rig)
            ev = [[(tick, w) for tick, w in r.m.events] for r in runs]
            if not glide:
                same += ev[0] == ev[1]
                continue
            per_tick = [Counter(), Counter()]
            for i in (0, 1):
                for tick, w in ev[i]:
                    per_tick[i][tick, w] += 1
            legato_ok += per_tick[0] == per_tick[1]
            swapped += ev[0] != ev[1]
            not_more_on += runs[0].left_on() <= runs[1].left_on()
    check(same == 30, f"without G: every message at the same tick as stock ({same} of 30 patterns)")
    check(legato_ok == 30, f"with G: the same messages at each tick as stock ({legato_ok} of 30; "
                           f"{swapped} patterns with a changed order)")
    check(swapped > 0, "with G: some patterns play legato")
    check(not_more_on == 30, f"with G: no more notes left on after stop than stock ({not_more_on} of 30)")


def legato_checks(ptf: Path) -> None:
    print("Legato order")
    for column in (0, 1):
        for v in (0, 50, 100, 255):
            fx = ((GLIDE, v), (0, 0)) if column == 0 else ((0, 0), (GLIDE, v))
            rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, *fx)], 2)
            ok = at(rig, 1) == [("on", 1, 62), ("off", 1, 60)] and at(rig, 1, "USB") == at(rig, 1)
            check(ok, f"G {v} in FX{column + 1}: on 62, then off 60, same tick, DIN and USB")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G), (8, 2, 64, 48, G), (8, 3, 65, 48)], 4)
    check(at(rig, 1) == [("on", 1, 62), ("off", 1, 60)] and at(rig, 2) == [("on", 1, 64), ("off", 1, 62)]
          and at(rig, 3) == [("off", 1, 64), ("on", 1, 65)], "a phrase: two legato notes, then a plain one")
    rig = play(ptf, [(8, 0, 60, 48), (8, 3, 62, 48, G)], 4)
    check(at(rig, 3) == [("on", 1, 62), ("off", 1, 60)], "after empty steps")
    rig = play(ptf, [(8, 0, 60, 48, G), (8, 1, 62, 48, G)], 4, last_step=1)
    check(at(rig, 2) == [("on", 1, 60), ("off", 1, 62)] and at(rig, 3) == [("on", 1, 62), ("off", 1, 60)],
          "a looping 2-step pattern")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 49, G)], 2)
    check(at(rig, 1) == [("on", 2, 62), ("off", 1, 60)], "channel 1 to channel 2")

    def song(rig: Rig) -> None:
        rig.steps([(8, 0, 64, 48, G)], rig.nxt)
        rig.m.stub_function(FM_REQUEST_PATTERN_LOAD, lambda mm: 1)
        rig.m.write(PROJECT + 0x5C2, bytes([1, 2, 0]))
        rig.m.w8(PROJECT + 0x6C1, 0)
        rig.m.w8(SEQ + 0x1C, 1)
    rig = Rig(ptf, 3)
    rig.steps([(8, 0, 60, 48), (8, 3, 62, 48)])
    song(rig)
    rig.m.call(st.SEQ_PLAY_FROM, SEQ, 0)
    rig.m.w8(SEQ + 0x1C, 1)
    rig.tick0 = rig.m.ticks
    rig.m.events.clear()
    rig.run(STEP * 5)
    check(at(rig, 4) == [("on", 1, 64), ("off", 1, 62)], "song mode: G on the next pattern's first note")


def stock_order_checks(ptf: Path) -> None:
    print("Stock order kept")
    cases = [
        ("the same pitch and channel", [(8, 0, 60, 48), (8, 1, 60, 48, G)], [("off", 1, 60), ("on", 1, 60)]),
        ("the old note gated (q 50)", [(8, 0, 60, 48, (GATE, 50)), (8, 1, 62, 48, G)], [("off", 1, 60), ("on", 1, 62)]),
        ("the old note gated (q 100)", [(8, 0, 60, 48, (GATE, 100)), (8, 1, 62, 48, G)], [("off", 1, 60), ("on", 1, 62)]),
        ("Chord on the new note", [(8, 0, 60, 48), (8, 1, 62, 48, G, (CHORD, 1))], None),
        ("Chord on the old note", [(8, 0, 60, 48, (CHORD, 1)), (8, 1, 62, 48, G)], None),
        ("Arp on the new note", [(8, 0, 60, 48), (8, 1, 62, 48, G, (ARP, 1))], None),
        ("a synth instrument (64) before", [(8, 0, 60, 64), (8, 1, 62, 48, G)], None),
        ("a synth instrument (64) after", [(8, 0, 60, 48), (8, 1, 62, 64, G)], None),
        ("G on a step without a note", [(8, 0, 60, 48), (8, 1, -1, 48, G), (8, 2, 62, 48)], None),
        ("G on the first note of a take", [(8, 0, 60, 48, G), (8, 1, 62, 48)], None),
    ]
    for what, steps, want in cases:
        rigs = []
        for patched in (True, False):
            rig = Rig(PTF if patched else STOCK, 15, patched)
            skip_synth_notes(rig)
            rig.steps(steps)
            rig.start()
            rig.run(STEP * 3)
            rig.stop()
            rigs.append(rig)
        ok = rigs[0].m.events == rigs[1].m.events
        if want is not None:
            ok = ok and at(rigs[0], 1) == want
        check(ok, f"{what}: as stock firmware")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, -2, 48), (8, 2, 62, 48, G)], 3)
    check(at(rig, 1) == [("off", 1, 60)] and at(rig, 2) == [("on", 1, 62)], "after an OFF step: just the note-on")


def fx_checks(ptf: Path) -> None:
    print("With other FX")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G, (MICRO_MOVE, 50))], 2)
    check([(t, k, n) for t, k, _, n in rig.msgs()] == [(12, "on", 60), (12 + STEP + 24 * 12, "on", 62),
                                                       (12 + STEP + 24 * 12, "off", 60)],
          "Micro-move 50: on 62, then off 60, both at micro-step 25")

    def delay(rig: Rig) -> None:
        rig.m.w8(PROJECT + 0xAA0 + 0x10 * 1 + 9, 50)
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G)], 2, extra=delay)
    m = rig.msgs()
    check(len(m) == 3 and m[1][1:] == ("on", 1, 62) and m[2][1:] == ("off", 1, 60) and m[1][0] == m[2][0] > STEP,
          "instrument MIDI delay: both messages delayed together, legato order")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G, (VOLUME, 50))], 2)
    vel = [w for _, w in rig.m.events if w.startswith("DIN note on 62")]
    check(at(rig, 1) == [("on", 1, 62), ("off", 1, 60)] and vel == ["DIN note on 62 63 ch1"],
          "Volume 50: velocity 63, legato order")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G, (RANDOM_NOTE, 12)), (8, 2, 64, 48, G, (RANDOM_NOTE, 12))], 3)
    s1, s2 = at(rig, 1), at(rig, 2)
    check(len(s1) == 2 and s1[0][0] == "on" and s1[1] == ("off", 1, 60) and len(s2) == 2 and s2[0][0] == "on"
          and s2[1] == ("off", 1, s1[0][2]), f"Random note: legato from the picked note ({s1}, {s2})")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G, (CC_A, 10))], 2)
    din = [w for tick, w in rig.m.events if tick - rig.tick0 == 12 + STEP and w.startswith("DIN")]
    check(din == ["DIN cc 74 10 ch1", "DIN note on 62 127 ch1", "DIN note off 60 0 ch1"],
          f"CC A in the other column: CC, then on, then off ({din})")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G, (CHANCE, 0)), (8, 2, 64, 48)], 3)
    check(at(rig, 1) == [] and at(rig, 2) == [("off", 1, 60), ("on", 1, 64)], "Chance 0: nothing sent on the step")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G, (CHANCE, 100))], 2)
    check(at(rig, 1) == [("on", 1, 62), ("off", 1, 60)], "Chance 100: legato")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G, (ROLL, 1))], 2)
    check(at(rig, 1) == [("on", 1, 62), ("off", 1, 60)], "Roll on the new note: legato into its first note")


def stuck_checks(ptf: Path) -> None:
    print("No stuck notes")
    rig = play(ptf, [(8, 0, 60, 48), (8, 1, 62, 48, G)], 1)
    rig.run(STEP // 2)
    rig.stop()
    on = Counter()
    for _, k, c, n in rig.msgs():
        on[c, n] += 1 if k == "on" else -1
    check(all(v == 0 for v in on.values()), "stop after a legato note: every note ended")

    ram = patch_ram(ptf, "legato")
    for how in ("the end of the audio block", "the next note"):
        rig = Rig(ptf)
        rig.steps([(9, 2, 70, 50)])
        rig.start()
        rig.run(STEP)
        rig.m.write(ram, struct.pack("<II", 0x474C544D, SEQ) + bytes([10]) + bytes([57, 49, 0, 0, 0, 0, 0xFF]))
        rig.m.events.clear()
        if how == "the end of the audio block":
            rig.block()
        else:
            rig.audio = None                # no audio-block hook: only the next note can end it
            rig.run(STEP * 2)
        offs = [w for _, w in rig.m.events if "note off 57" in w]
        check(offs == ["USB note off 57 0 ch2", "DIN note off 57 0 ch2"] and rig.m.uc.mem_read(ram + 8, 1)[0] == 0xFF,
              f"a planted kept note is ended by {how}")


def two_track_checks(ptf: Path, stock: Path) -> None:
    print("Two tracks on one channel")
    steps = [(8, 0, 60, 48), (9, 1, 62, 48, G), (8, 2, 64, 48, G), (9, 3, 65, 48)]
    runs = []
    for path, patched in ((ptf, True), (stock, False)):
        rig = play(path, steps, 4, patched=patched)
        rig.stop()
        runs.append(rig)
    a, b = runs[0].msgs(), runs[1].msgs()
    check(sorted(a) == sorted(b) and at(runs[0], 2) == [("on", 1, 64), ("off", 1, 60)],
          "the same messages as stock; on track 9 the legato note starts before its old note ends")


def cost_checks(ptf: Path, stock: Path) -> None:
    print("Cost (instructions per tick over three steps, 16 MIDI tracks with G on every note)")
    counts = {}
    for label, path, patched in (("stock", stock, False), ("patched", ptf, True)):
        rig = Rig(path, patched=patched)
        for t in range(16):
            for s in range(3):
                rig.steps([(t, s, 60 + 2 * s, 48 + t, G)])
        rig.start()
        n = [0]
        h = rig.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: n.__setitem__(0, n[0] + 1))
        worst = 0
        for _ in range(STEP * 3):
            before = n[0]
            rig.m.ticks += 1
            rig.m.call(st.SEQ_TICK, SEQ, max_insns=2_000_000)
            worst = max(worst, n[0] - before)
        rig.m.uc.hook_del(h)
        counts[label] = (n[0], worst)
    extra = counts["patched"][1] - counts["stock"][1]
    text = "; ".join(f"{k}: worst {w}, total {t}" for k, (t, w) in counts.items())
    check(extra <= 16 * PER_TRACK_LIMIT, f"at most {PER_TRACK_LIMIT} more per track at the worst tick ({extra} in all: {text})")


PTF = STOCK = None


def main() -> int:
    global PTF, STOCK
    PTF, STOCK = Path(sys.argv[1]), Path(sys.argv[2])
    site_checks(PTF)
    stock_checks(PTF, STOCK)
    legato_checks(PTF)
    stock_order_checks(PTF)
    fx_checks(PTF)
    stuck_checks(PTF)
    two_track_checks(PTF, STOCK)
    cost_checks(PTF, STOCK)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
