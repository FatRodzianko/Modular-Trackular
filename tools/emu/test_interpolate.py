"""Check the interpolate patch (CC and Macro ramps) on the patched firmware.

  python tools/emu/test_interpolate.py <patched.ptf>

Works with the interpolate-only build and with bundles that include it.

1. Playback: starts the stock sequencer with seq_play_from and runs the real audio
   block (app_audio_block 0x600aa7c8, whose seq_advance call the patch hooks) block
   by block, with the MIDI ports replaced by fakes that log every CC with the track's
   step and micro-step at that moment. Every CC must be either the stock one (the CC
   step's own value, at its first micro-step) or the ramp value a separate model of
   the rules gives for that step and micro-step:
     - from the last step at or before the current one with that CC letter to the
       next one after it, on the same track, both on the same MIDI channel instrument,
       with a CC number for that letter and different values;
     - position = (steps since the start + (micro - 1) / 48) / steps between them,
       rounded to the nearest value;
     - in Pattern mode the search wraps around the pattern; in Song mode it doesn't.
   Checked: Off (stock CCs only), a rising ramp and the falling one back across the
   loop, Song mode (holds after the last CC), Preview (the stock selection playback,
   once and looping: ramps as in normal playback, only on the selected tracks), two
   letters on one track, a CC in FX2,
   sample instruments, a channel change, a program-change mapping, equal values, a
   muted track, stopping, the per-block cap (1 with the DIN jack, 4 with USB only),
   round-robin between ramps, and no ramp CCs while the jack's send ring is full.
   Synths (S1..S3): every call the patch makes to the stock synth_apply_fx_q (ITCM
   0x5070) must carry the model's value, the FX type, the step's FX column and the
   synth; the Macro is applied again at every new step of a ramp (even when the value
   hasn't changed), at most 8 calls per block shared between all ramps; no ramp across
   a synth change or on a muted synth; and the render loop's hooked seq_advance call
   (0x600bc27a) ramps too. (Synth note-on and note-off are stubbed: the emulator has no synth
   voices.)
   Cost: instructions the patch runs per audio block, with 16 MIDI tracks of 128 steps
   (sparse: one CC per track, so every look-up runs through the whole pattern; dense:
   ramps on every track and letter), must stay under 40,000 in every block.
   The two settings work on their own: with only MIDI CC on, synths get no ramp
   calls; with only Synths on, MIDI tracks get no ramp CCs.
2. Config menu: the stock menu constructor; Project Settings must hold its 10 stock
   items and an "FX Interpolation" folder (among any other patches' entries) with the
   "MIDI CC" and "Synths" Off/On items on the patch's two settings, which start Off;
   the stock select opens the folder, and the stock Apply stores On and Off for each.
3. Settings file: interpolate.bin per project, through the screens' update hooks with
   a fake SD card, as for humanize: default Off and no file, one write a while after a
   change (both settings in it), per-project values, a file from before the synth
   setting (synths Off), a damaged file, a project without a name; Save As and new
   projects through the firmware's save request (project_save.save_as_checks).
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_S0, UC_ARM_REG_SP

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patch_ptf import bl_target  # noqa: E402
from fakefs import PROJECT_NAME, FakeFS  # noqa: E402
from layout import patch_code, patch_ram  # noqa: E402
from machine import Machine  # noqa: E402
from project_save import new_project_checks, save_as_checks  # noqa: E402
from seq_timing import PROJECT, SEQ, SEQ_PLAY_FROM, SETTINGS, make, pattern, set_step  # noqa: E402

APP, APP_AUDIO_BLOCK = 0x200170E0, 0x600AA7C8
SEQ_PLAY_SELECTION = 0x600AC3D0     # (seq, loop): what the Pattern screen's Preview starts
SYNTH_APPLY_FX = 0x5070             # ITCM (engine, type, value, column, synth 0..2)
NOTE_ON_OUT = 0x600ABB44            # MIDI and synth note-on (stubbed for synth tests)
NOTE_OFF_OUT = 0x600ABBE8           # ... and note-off
RENDER_CALL = 0x600BC27A            # the render loop's bl seq_advance (hooked)
SYNTH = 64                          # S1; S2 = 65, S3 = 66
AUDIO_STUBS = (0x6010C2C0, 0x6010C2EC, 0x600AB89C, 0x600AA848, 0x600AA850)
IP = 0                              # the patch's state: from the .map.json
IP_MAGIC = 0x36504D4D
IP_VALUE = 0                        # IP + 8: Interpolate MIDI CC, 0 Off / 1 On
IP_SYNTH = 0                        # IP + 16: Interpolate synths
CC_NUMBERS = PROJECT + 0xAB3        # + 0x10 * (channel - 1) + letter
MUTE = (0x202046E0, 0x20204CF6)     # + track, + (track & 7)
JACK_RING_PTR = 0x2000C69C
FAKE_RING = 0x20009500
CC_TYPES = [8, 9, 10, 11, 12, 37]   # CC a..f
A, B = 0, 1
MENU_CTOR, ATEXIT = 0x60118ACC, 0x600A3594
PROJECT_SETTINGS = 0x20028714
MENU_CURRENT = 0x20028180
MENU_SELECT = 0x60118928
MENU_APPLY, MENU_EDITOR_CLOSE = 0x60118658, 0x600CAB60
OFF_ON = 0x60060B88
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
CONFIG_UPDATE_SLOT, CONFIG_UPDATE = 0x6003B5F8, 0x600C6474
FILE_MAGIC = b"HIP1"
SAVE_DELAY = 25
STOCK_ITEMS = ["Performance presets", "Pattern divider", "Notes scale", "Root note", "Notes layout",
               "Anti-aliasing", "Limiter mode", "Sends mode", "Extra headroom", "Snap to zero"]

CODE = (0, 0)                       # set in main(): the patch's code, start and end
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def cdiv(a: int, b: int) -> int:
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


class Song:
    """The steps of one test pattern, for the model: {track: {step: (instr, {letter: value})}}."""

    def __init__(self, last: int):
        self.last = last
        self.steps: dict[int, dict[int, tuple[int, dict[int, int]]]] = {}

    def cc(self, track: int, step: int, instr: int, values: dict[int, int]) -> None:
        self.steps.setdefault(track, {})[step] = (instr, values)

    def ramp(self, track: int, letter: int, cur: int, wrap: bool, cc_numbers: dict[int, int]):
        """(k, j, from, to, elapsed, length) for the ramp at step `cur`, or None."""
        steps, n = self.steps.get(track, {}), self.last + 1
        has = {s for s, st in steps.items() if letter in st[1]}
        back = next((d for d in range(n) if (cur - d) % n in has and (wrap or d <= cur)), None)
        fwd = next((d for d in range(1, n + 1) if (cur + d) % n in has and (wrap or cur + d <= self.last)), None)
        if back is None or fwd is None:
            return None
        k, j = (cur - back) % n, (cur + fwd) % n
        ik, vk, ij, vj = steps[k][0], steps[k][1], steps[j][0], steps[j][1]
        if not 48 <= ik <= 66 or ij != ik:
            return None
        cc = cc_numbers.get((ik - 47, letter), 0) if ik <= 63 else 0
        if cc > 127 or vk[letter] == vj[letter]:
            return None
        return k, j, vk[letter], vj[letter], back, back + fwd


def model_value(fr: int, to: int, elapsed: int, length: int, micro: int) -> int:
    total = length * 48
    pos = min(elapsed * 48 + (micro - 1 if 1 <= micro <= 48 else 0), total)
    diff = to - fr
    v = fr + cdiv(diff * pos + (total // 2 if diff >= 0 else -(total // 2)), total)
    return max(0, min(127, v))


class Player:
    def __init__(self, ptf: Path, on: bool, midi_out: int = 2, tracks_of_channel=None, synth: bool = False):
        self.m = m = make(ptf)
        for a in AUDIO_STUBS:
            m.stub_function(a, lambda mm: 0)
        m.w32(0x20027F1C, m.alloc(0x40))
        m.w8(0x20026E44, 0)
        m.w32(IP, IP_MAGIC)
        m.w8(IP_VALUE, 1 if on else 0)
        m.w8(IP_SYNTH, 1 if synth else 0)
        self.midi_out = midi_out
        self.track_of = tracks_of_channel or {}
        self.cc_numbers: dict[tuple[int, int], int] = {}
        self.block = 0
        self.ccs: list[tuple[int, int, int, int, int, int]] = []   # block, track, step, micro, cc, value
        self.other: list[tuple[int, str]] = []
        for ptr in (0x200171C0, 0x200171C4):
            vt = m.r32(m.r32(ptr))

            def on_cc(mm):
                ch = mm.arg(3)
                t = self.track_of.get(ch, 0)
                tr = SEQ + 0x68 * t
                step, micro = mm.r32(tr + 0x88) & 0xFFFF, mm.r32(tr + 0x86) & 0xFFFF
                self.ccs.append((self.block, t, step, micro, mm.arg(1), mm.arg(2)))
                return 0
            m.w32(vt + 0x0C, m.add_stub(on_cc))
            for off in (0x10, 0x14, 0x18):
                m.w32(vt + off, m.add_stub(lambda mm, o=off: self.other.append((self.block, f"vt+{o:#x}")) or 0))

    def set_cc_number(self, channel: int, letter: int, number: int) -> None:
        self.m.w8(CC_NUMBERS + 0x10 * (channel - 1) + letter, number)
        self.cc_numbers[(channel, letter)] = number

    def start(self, buf: int, song_mode: bool = False) -> None:
        m = self.m
        m.w32(SEQ + 0x14, buf)
        m.w32(SEQ + 0x18, buf)
        m.w8(SETTINGS + 0x2C, self.midi_out)
        m.call(SEQ_PLAY_FROM, SEQ, 0)
        if song_mode:
            m.w8(SEQ + 0x1C, 1)
        self.ccs.clear()

    def run(self, blocks: int, until_step=None, track: int = 0) -> None:
        for _ in range(blocks):
            self.block += 1
            self.m.call(APP_AUDIO_BLOCK, APP, 0, 0, 128, max_insns=3_000_000)
            if until_step is not None and self.m.r32(SEQ + 0x68 * track + 0x88) & 0xFFFF == until_step:
                break


def classify(p: Player, song: Song, letters_of_track: dict[int, list[int]], wrap: bool):
    """Split the CCs into stock and ramp ones; returns (stock, ramp, errors)."""
    stock, ramp, errors = [], [], []
    for block, t, step, micro, cc, value in p.ccs:
        st = song.steps.get(t, {}).get(step)
        letters = [lt for lt in letters_of_track.get(t, []) if p.cc_numbers.get((st[0] - 47 if st else 0, lt)) == cc]
        if st and micro == 1 and any(st[1].get(lt) == value for lt in st[1]):
            stock.append((block, t, step, value))
            continue
        ok = False
        for lt in letters_of_track.get(t, []):
            r = song.ramp(t, lt, step, wrap, p.cc_numbers)
            if r and p.cc_numbers.get((song.steps[t][r[0]][0] - 47, lt)) == cc \
                    and model_value(r[2], r[3], r[4], r[5], micro) == value:
                ok = True
                ramp.append((block, t, step, micro, lt, value))
                break
        if not ok:
            errors.append(f"block {block} track {t} step {step} micro {micro}: CC {cc} = {value} {letters}")
    return stock, ramp, errors


def per_block(ramp) -> dict[int, int]:
    counts: dict[int, int] = {}
    for b, *_ in ramp:
        counts[b] = counts.get(b, 0) + 1
    return counts


def playback_checks(ptf: Path) -> None:
    print("playback: a ramp on track 0 (MIDI channel 1, CC A = 74), 16 steps, Pattern mode")
    song = Song(15)

    def basic(on: bool, song_mode: bool = False, midi_out: int = 2, mute: bool = False) -> Player:
        p = Player(ptf, on, midi_out, {1: 0})
        p.set_cc_number(1, A, 74)
        buf = pattern(p.m, song.last)
        set_step(p.m, buf, 0, 0, 60, 48, (CC_TYPES[A], 0))
        set_step(p.m, buf, 0, 4, -1, 48, (CC_TYPES[A], 100))
        if mute:
            p.m.w8(MUTE[0], 1)
            p.m.w8(MUTE[1], 1)
        p.start(buf, song_mode)
        return p
    song.cc(0, 0, 48, {A: 0})
    song.cc(0, 4, 48, {A: 100})

    p = basic(False)
    p.run(16 * 45)
    stock, ramp, errors = classify(p, song, {0: [A]}, True)
    check(not errors and not ramp and [s[3] for s in stock][:2] == [0, 100],
          f"Off: only the stock CCs ({len(stock)}: {[s[3] for s in stock][:4]}), no ramp" + "; ".join(errors[:3]))

    p = basic(True)
    p.run(10 * 45, until_step=4)
    at_step4 = p.block
    p.run(16 * 45, until_step=0)                           # on to step 0 of the next loop
    at_wrap = p.block
    p.run(2)                                               # and its first micro-step
    stock, ramp, errors = classify(p, song, {0: [A]}, True)
    check(not errors, f"On: every CC is the stock one or the model's ramp value ({len(p.ccs)} CCs)"
          + ("" if not errors else ": " + "; ".join(errors[:3])))
    up = [v for (b, t, s, u, lt, v) in ramp if b <= at_step4]
    down = [v for (b, t, s, u, lt, v) in ramp if at_step4 < b <= at_wrap]
    check(len(up) >= 95 and up == sorted(up) and len(set(up)) == len(up) and up[0] <= 2 and up[-1] >= 98,
          f"rising ramp 0 -> 100 over steps 0..4: {len(up)} CCs, {up[0] if up else '-'}..{up[-1] if up else '-'}, "
          "each value once, in order")
    check(len(down) >= 95 and down == sorted(down, reverse=True) and down[-1] <= 2,
          f"falling ramp 100 -> 0 from step 4 to step 0 of the next loop: {len(down)} CCs, "
          f"{down[0] if down else '-'}..{down[-1] if down else '-'}")
    check(max(per_block(ramp).values()) == 1, "DIN jack: at most 1 ramp CC per block")
    check([s[3] for s in stock][:3] == [0, 100, 0], f"stock CCs still at the CC steps: {[s[3] for s in stock][:3]}")

    p = basic(True, song_mode=True)
    p.run(15 * 45, until_step=15)
    stock, ramp, errors = classify(p, song, {0: [A]}, False)
    check(not errors and ramp and all(s < 4 for (_, _, s, *_) in ramp),
          f"Song mode: ramps up to step 4, then holds ({len(ramp)} ramp CCs, last at step "
          f"{ramp[-1][2] if ramp else '-'})" + ("" if not errors else ": " + "; ".join(errors[:3])))

    p = basic(True, mute=True)
    p.run(8 * 45)
    check(not p.ccs, f"muted track: no CCs at all ({len(p.ccs)})")

    p = basic(True)
    p.run(60)
    p.m.w8(SEQ + 0x2A, 0)                                  # stopped
    n = len(p.ccs)
    p.run(100)
    check(n > 10 and len(p.ccs) == n, f"stopped: no more CCs ({n} before, {len(p.ccs) - n} after)")

    print("playback: what gets no ramp, and two letters, FX2")
    song2 = Song(15)
    others = []
    for on in (False, True):
        p = mixed(ptf, song2, on)
        others.append(p.other)
    letters = {0: [A, B], 1: [A], 2: [A], 3: [A], 4: [A], 5: [A]}
    stock, ramp, errors = classify(p, song2, letters, True)
    check(not errors, "every CC is a stock one or a model ramp value" + ("" if not errors else ": " + "; ".join(errors[:3])))
    by = {(t, lt) for (_, t, _, _, lt, _) in ramp}
    check({(0, A), (0, B), (5, A)} <= by, f"ramps on track 0 (CC A and CC B) and track 5 (CC in FX2): {sorted(by)}")
    check(not by & {(1, A), (2, A), (3, A), (4, A)},
          "no ramps for a sample instrument, a channel change, equal values or a program-change mapping")
    check(others[0] == others[1] and others[0], f"the same program changes as with Off ({len(others[1])})")
    preview_checks(ptf)
    synth_checks(ptf)
    cap_checks(ptf)
    ring_checks(ptf, basic, song)


def preview_checks(ptf: Path) -> None:
    print("playback: Preview of steps 2..5 on track 1 (tracks 1 and 2 both ramp 0 -> 100 over steps 0..8)")
    song = Song(15)
    for t, ins in ((0, 48), (1, 49)):
        song.cc(t, 0, ins, {A: 0})
        song.cc(t, 8, ins, {A: 100})
    for loop in (0, 1):
        p = Player(ptf, True, 2, {1: 0, 2: 1})
        m = p.m
        p.set_cc_number(1, A, 74)
        p.set_cc_number(2, A, 75)
        buf = pattern(m, song.last)
        for t, ins in ((0, 48), (1, 49)):
            set_step(m, buf, t, 0, 60, ins, (CC_TYPES[A], 0))
            set_step(m, buf, t, 8, -1, ins, (CC_TYPES[A], 100))
        m.w32(SEQ + 0x14, buf)
        m.w32(SEQ + 0x18, buf)
        m.w8(SETTINGS + 0x2C, 2)
        m.write(SEQ, bytes([2, 0, 5, 0]))                 # selection: steps 2..5, track 1 only
        m.call(SEQ_PLAY_SELECTION, SEQ, loop)
        started = m.uc.mem_read(SEQ + 0x1E, 1)[0] == 1     # selection playback on
        p.ccs.clear()
        p.run(45 * 10)
        stock, ramp, errors = classify(p, song, {0: [A], 1: [A]}, True)
        steps = [s for (_, _, s, *_) in ramp]
        label = "looping" if loop else "once"
        check(started and not errors and ramp and all(r[1] == 0 for r in ramp)
              and set(steps) <= {2, 3, 4, 5},
              f"Preview {label}: {len(ramp)} ramp CCs, on track 1 only, at steps {sorted(set(steps))}, each the "
              "value normal playback has there" + ("" if not errors else ": " + "; ".join(errors[:3])))
        if loop:
            restarts = sum(1 for a, b in zip(steps, steps[1:]) if b < a)
            check(restarts >= 1 and ramp[0][5] == model_value(0, 100, 2, 8, ramp[0][3]),
                  f"looping: the ramp picks up again at step 2 after each loop ({restarts} restarts)")


def mixed(ptf: Path, song2: Song, on: bool) -> Player:
    p = Player(ptf, on, 2, {1: 0, 2: 2, 6: 2, 3: 3, 4: 4, 5: 5})
    m = p.m
    for ch in range(1, 7):
        p.set_cc_number(ch, A, 20 + ch)
        p.set_cc_number(ch, B, 40 + ch)
    p.set_cc_number(4, A, 0x80)                            # channel 4: CC A sends program change
    buf = pattern(m, song2.last)
    rows = [  # track, step, instr, fx1, fx2
        (0, 0, 48, (CC_TYPES[A], 10), (CC_TYPES[B], 90)),  # two letters, FX1 and FX2
        (0, 8, 48, (CC_TYPES[B], 30), (CC_TYPES[A], 70)),
        (1, 0, 3, (CC_TYPES[A], 0), (0, 0)),               # sample instrument
        (1, 8, 3, (CC_TYPES[A], 100), (0, 0)),
        (2, 0, 49, (CC_TYPES[A], 0), (0, 0)),              # channel 2 -> channel 6: no ramp
        (2, 8, 53, (CC_TYPES[A], 100), (0, 0)),
        (3, 0, 50, (CC_TYPES[A], 50), (0, 0)),             # equal values
        (3, 8, 50, (CC_TYPES[A], 50), (0, 0)),
        (4, 0, 51, (CC_TYPES[A], 0), (0, 0)),              # program change mapping
        (4, 8, 51, (CC_TYPES[A], 100), (0, 0)),
        (5, 0, 52, (27, 40), (CC_TYPES[A], 0)),            # CC in FX2 next to another FX
        (5, 8, 52, (0, 0), (CC_TYPES[A], 120)),
    ]
    song2.steps.clear()
    for t, s, ins, fx1, fx2 in rows:
        set_step(m, buf, t, s, 60 if s == 0 else -1, ins, fx1, fx2)
        vals = {CC_TYPES.index(ty): v for ty, v in (fx1, fx2) if ty in CC_TYPES}
        song2.cc(t, s, ins, vals)
    p.start(buf)
    p.run(16 * 45, until_step=12)
    return p


class SynthPlayer(Player):
    """A Player that logs synth_apply_fx_q calls, split into stock and patch ones."""

    def __init__(self, ptf: Path, track_of_synth: dict[int, int], midi: bool = True, synth: bool = True):
        super().__init__(ptf, midi, 2, {}, synth)
        m = self.m
        m.stub_function(NOTE_ON_OUT, lambda mm: 0)
        m.stub_function(NOTE_OFF_OUT, lambda mm: 0)
        self.calls: list[tuple[int, int, int, int, int, int, int, bool]] = []
        code = next(q for q in json.loads(ptf.with_suffix(".map.json").read_text())["patches"]
                    if q["name"] == "interpolate")
        lo, hi = (int(x, 16) for x in code["code"])

        def on_apply(uc, a, s, u):
            synth = struct.unpack("<I", uc.mem_read(uc.reg_read(UC_ARM_REG_SP), 4))[0] & 0xFF
            t = track_of_synth.get(synth, 0)
            tr = SEQ + 0x68 * t
            step, micro = m.r32(tr + 0x88) & 0xFFFF, m.r32(tr + 0x86) & 0xFFFF
            by_patch = lo <= uc.reg_read(UC_ARM_REG_LR) < hi
            self.calls.append((self.block, t, step, micro, uc.reg_read(UC_ARM_REG_R1),
                               uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3) & 0xFF, synth, by_patch))
        m.uc.hook_add(UC_HOOK_CODE, on_apply, begin=SYNTH_APPLY_FX, end=SYNTH_APPLY_FX)

    def patch_calls(self):
        return [c for c in self.calls if c[8]]


def synth_errors(p: SynthPlayer, song: Song, letters: dict[int, list[int]]) -> list[str]:
    errors = []
    for block, t, step, micro, typ, value, column, synth, _ in p.patch_calls():
        lt = CC_TYPES.index(typ) if typ in CC_TYPES else -1
        r = song.ramp(t, lt, step, True, {}) if lt in letters.get(t, []) else None
        st = song.steps.get(t, {}).get(r[0]) if r else None
        want_col = st[2][lt] if st and len(st) > 2 else 0
        if not r or model_value(r[2], r[3], r[4], r[5], micro) != value or column != want_col \
                or synth != song.steps[t][r[0]][0] - SYNTH:
            errors.append(f"block {block} track {t} step {step} micro {micro}: type {typ} value {value} "
                          f"column {column} synth {synth}")
    return errors


def synth_checks(ptf: Path) -> None:
    print("synths: S1 on track 3, Macro a 0 -> 100 over steps 0..4 (FX only), S2 on track 4, Macro b in FX2")
    song = Song(15)
    p = SynthPlayer(ptf, {0: 2, 1: 3})
    m = p.m
    buf = pattern(m, song.last)
    set_step(m, buf, 2, 0, 60, SYNTH, (CC_TYPES[A], 0))
    set_step(m, buf, 2, 2, 62, SYNTH)                       # a note in between, no FX
    set_step(m, buf, 2, 4, -1, SYNTH, (CC_TYPES[A], 100))
    set_step(m, buf, 3, 0, -1, SYNTH + 1, (27, 50), (CC_TYPES[B], 10))
    set_step(m, buf, 3, 8, -1, SYNTH + 1, (27, 50), (CC_TYPES[B], 12))   # a slow ramp: 10 -> 12 over 8 steps
    song.cc(2, 0, SYNTH, {A: 0})
    song.cc(2, 4, SYNTH, {A: 100})
    song.cc(3, 0, SYNTH + 1, {B: 10})
    song.cc(3, 8, SYNTH + 1, {B: 12})
    for (t, s), cols in (((2, 0), {A: 0}), ((2, 4), {A: 0}), ((3, 0), {B: 1}), ((3, 8), {B: 1})):
        song.steps[t][s] = song.steps[t][s] + (cols,)
    p.start(buf)
    p.run(8 * 45, until_step=8, track=3)
    calls = p.patch_calls()
    errors = synth_errors(p, song, {2: [A], 3: [B]})
    up = [c[5] for c in calls if c[1] == 2 and c[2] < 4]
    check(not errors and len(up) >= 80 and up == sorted(up) and up[-1] >= 98,
          f"S1: {len(up)} Macro a updates rising to {up[-1] if up else '-'}, every one the model's value, "
          "type and synth" + ("" if not errors else ": " + "; ".join(errors[:3])))
    stock = [c for c in p.calls if not c[8] and c[1] == 2 and c[4] == CC_TYPES[A]]
    check([c[5] for c in stock][:2] == [0, 100], f"the stock call still sets each Macro step ({[c[5] for c in stock][:2]})")
    slow = [c for c in calls if c[1] == 3]
    steps = {c[2] for c in slow}
    check(all(c[6] == 1 for c in slow) and set(range(1, 8)) <= steps,
          f"S2: Macro b from FX2 goes to column 1, and is applied again at every step of a slow ramp "
          f"(steps {sorted(steps)}, {len(slow)} calls)")

    print("synths: no ramp across a synth change or on a muted synth")
    for label, change, mute in (("S1 -> S2", True, False), ("muted", False, True)):
        p = SynthPlayer(ptf, {0: 2, 1: 2})
        buf = pattern(p.m, 15)
        set_step(p.m, buf, 2, 0, -1, SYNTH, (CC_TYPES[A], 0))
        set_step(p.m, buf, 2, 4, -1, SYNTH + 1 if change else SYNTH, (CC_TYPES[A], 100))
        if mute:
            p.m.w8(MUTE[0] + 2, 1)
            p.m.w8(MUTE[1] + 2, 1)
        p.start(buf)
        p.run(4 * 45)
        check(not p.patch_calls(), f"{label}: no ramp calls ({len(p.patch_calls())})")

    print("synths: 18 ramps (S1..S3, Macros a..f), the per-block cap")
    p = SynthPlayer(ptf, {0: 0, 1: 1, 2: 2})
    song = Song(15)
    buf = pattern(p.m, song.last)
    for t in range(3):
        for i, (l1, l2) in enumerate(((0, 1), (2, 3), (4, 5))):
            set_step(p.m, buf, t, i, -1, SYNTH + t, (CC_TYPES[l1], 0), (CC_TYPES[l2], 0))
            set_step(p.m, buf, t, i + 3, -1, SYNTH + t, (CC_TYPES[l1], 127), (CC_TYPES[l2], 127))
            for s, v in ((i, 0), (i + 3, 127)):
                song.cc(t, s, SYNTH + t, {l1: v, l2: v})
                song.steps[t][s] = song.steps[t][s] + ({l1: 0, l2: 1},)
    p.start(buf)
    p.run(5 * 45, until_step=5)
    calls = p.patch_calls()
    errors = synth_errors(p, song, {t: list(range(6)) for t in range(3)})
    counts = per_block([(c[0],) for c in calls])
    ramps = {(c[1], c[4]) for c in calls}
    check(not errors and max(counts.values()) <= 8 and len(ramps) == 18,
          f"at most 8 synth updates per block (max {max(counts.values())}), all 18 ramps updated "
          f"({len(ramps)}), every one the model's value" + ("" if not errors else ": " + "; ".join(errors[:2])))

    print("synths: the render loop's seq_advance call")
    p = SynthPlayer(ptf, {0: 2})
    m = p.m
    buf = pattern(m, 15)
    set_step(m, buf, 2, 0, -1, SYNTH, (CC_TYPES[A], 0))
    set_step(m, buf, 2, 4, -1, SYNTH, (CC_TYPES[A], 100))
    p.start(buf)
    hook = bl_target(bytes(m.uc.mem_read(0x60000000, 0x200000)), 0x60000000, RENDER_CALL)
    for _ in range(45 * 3):
        p.block += 1
        m.uc.reg_write(UC_ARM_REG_S0, struct.unpack("<I", struct.pack("<f", 128 / 44122.65))[0])
        m.call(hook | 1, SEQ, max_insns=3_000_000)
    up = [c[5] for c in p.patch_calls()]
    check(len(up) >= 50 and up == sorted(up), f"calling the hooked render-loop site ramps too ({len(up)} updates, "
          f"{up[0] if up else '-'}..{up[-1] if up else '-'})")

    print("the two switches: a MIDI track (channel 1) and S1 ramping side by side")
    for midi, synth in ((True, False), (False, True), (False, False)):
        p = SynthPlayer(ptf, {0: 2}, midi=midi, synth=synth)
        p.track_of = {1: 0}
        p.set_cc_number(1, A, 74)
        buf = pattern(p.m, 15)
        set_step(p.m, buf, 0, 0, -1, 48, (CC_TYPES[A], 0))
        set_step(p.m, buf, 0, 4, -1, 48, (CC_TYPES[A], 100))
        set_step(p.m, buf, 2, 0, -1, SYNTH, (CC_TYPES[A], 0))
        set_step(p.m, buf, 2, 4, -1, SYNTH, (CC_TYPES[A], 100))
        p.start(buf)
        p.run(3 * 45)
        midi_ramps = len([c for c in p.ccs if c[3] != 1])      # stock CCs go out at micro-step 1
        synth_ramps = len(p.patch_calls())
        check((midi_ramps > 0) == midi and (synth_ramps > 0) == synth,
              f"MIDI CC {'On' if midi else 'Off'}, synths {'On' if synth else 'Off'}: "
              f"{midi_ramps} MIDI ramp CCs, {synth_ramps} synth ramp calls")


def cap_checks(ptf: Path) -> None:
    print("playback: the per-block cap and round robin (6 ramps)")
    for midi_out, cap, label in ((2, 1, "DIN jack"), (1, 4, "USB only")):
        p = Player(ptf, True, midi_out, {ch: ch - 1 for ch in range(1, 7)})
        song3 = Song(15)
        buf = pattern(p.m, song3.last)
        for t in range(6):
            p.set_cc_number(t + 1, A, 70 + t)
            set_step(p.m, buf, t, 0, 60, 48 + t, (CC_TYPES[A], 0))
            set_step(p.m, buf, t, 2, -1, 48 + t, (CC_TYPES[A], 127))
            song3.cc(t, 0, 48 + t, {A: 0})
            song3.cc(t, 2, 48 + t, {A: 127})
        p.start(buf)
        p.run(2 * 45, until_step=2)
        stock, ramp, errors = classify(p, song3, {t: [A] for t in range(6)}, True)
        counts = per_block(ramp)
        per_track = {t: sum(1 for r in ramp if r[1] == t) for t in range(6)}
        check(not errors and max(counts.values()) <= cap and min(per_track.values()) > 0
              and max(per_track.values()) - min(per_track.values()) <= max(2, sum(per_track.values()) // 10),
              f"{label}: at most {cap} ramp CCs per block (max {max(counts.values())}), "
              f"shared between the tracks {per_track}" + ("" if not errors else ": " + "; ".join(errors[:2])))


def ring_checks(ptf: Path, basic, song: Song) -> None:
    print("playback: the jack's send ring")
    for fill, want in ((200, False), (100, True)):
        p = basic(True)
        p.m.write(FAKE_RING, (fill).to_bytes(4, "little") + (0).to_bytes(4, "little"))
        p.m.w32(JACK_RING_PTR, FAKE_RING)
        p.run(3 * 45)
        _, ramp, _ = classify(p, song, {0: [A]}, True)
        check(bool(ramp) == want, f"ring holding {fill} bytes: {'ramps' if want else 'no ramp CCs'} ({len(ramp)})")


def cost_checks(ptf: Path) -> None:
    print("cost: instructions in the patch per audio block, 16 MIDI tracks of 128 steps")
    code = next(q for q in json.loads(ptf.with_suffix(".map.json").read_text())["patches"]
                if q["name"] == "interpolate")
    lo, hi = (int(x, 16) for x in code["code"])
    for label, dense in (("sparse (whole-pattern look-ups)", False), ("dense (ramps everywhere)", True)):
        p = Player(ptf, True, 1, {ch: ch - 1 for ch in range(1, 17)})
        buf = pattern(p.m, 127)
        for t in range(16):
            for letter in range(6):
                p.set_cc_number(t + 1, letter, 20 + letter)
            if not dense:
                set_step(p.m, buf, t, 0, 60, 48 + t, (CC_TYPES[A], 5))
            else:
                for s in range(0, 128, 8):
                    set_step(p.m, buf, t, s, 60, 48 + t, (CC_TYPES[(s // 8) % 6], (s * 7) % 128),
                             (CC_TYPES[(s // 8 + 3) % 6], (s * 5) % 128))
        count = [0]
        p.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: count.__setitem__(0, count[0] + 1), begin=lo, end=hi)
        p.start(buf)
        worst = total = 0
        blocks = 45 * 12
        for _ in range(blocks):
            count[0] = 0
            p.run(1)
            worst = max(worst, count[0])
            total += count[0]
        check(worst < 12_000, f"{label}: worst block {worst} instructions, average {total // blocks}")


def menu_checks(ptf: Path) -> None:
    print("Config menu: Project Settings > FX Interpolation > MIDI CC, Synths")
    m = Machine(ptf)
    m.stub_function(ATEXIT, lambda mm: 0)
    m.call(MENU_CTOR, max_insns=50_000_000)
    ps = PROJECT_SETTINGS
    cap, count = m.r32(ps + 8) & 0xFF, m.r32(ps + 0x1C) >> 8 & 0xFF
    kids = [m.r32(m.r32(ps + 0xC) + 4 * i) for i in range(min(cap, count))]
    labels = [m.cstr(m.r32(k + (0x18 if m.r32(k) & 0xFF == 1 else 4))) for k in kids]
    check(cap == count and count >= 11, f"Project Settings: room for {cap}, holds {count}")
    check(labels[:10] == STOCK_ITEMS, "the 10 stock items are unchanged, in order")
    if "FX Interpolation" not in labels[10:]:
        check(False, f"an 'FX Interpolation' folder after the stock items ({labels[10:]})")
        return
    at = labels.index("FX Interpolation", 10)
    folder = kids[at]
    fcap, fcount = m.r32(folder + 8) & 0xFF, m.r32(folder + 0x1C) >> 8 & 0xFF
    items = [m.r32(m.r32(folder + 0xC) + 4 * i) for i in range(min(fcap, fcount))]
    names = [m.cstr(m.r32(i + 4)) for i in items]
    check(m.r32(folder) & 0xFF == 1 and fcap == fcount == 2 and names == ["MIDI CC", "Synths"],
          f"item {at + 1} is the folder 'FX Interpolation' with {names} (patch entries: {labels[10:]})")
    m.stub_function(0x60118828, lambda mm: 0)               # menu redraw
    m.w32(MENU_CURRENT, ps)
    m.w8(ps + 0x1C, at)
    m.call(MENU_SELECT)
    check(m.r32(MENU_CURRENT) == folder, "the stock select opens the folder")
    closed = []
    m.stub_function(MENU_EDITOR_CLOSE, lambda mm: closed.append(1))
    for idx, (item, (label, addr)) in enumerate(zip(items, (("MIDI CC", IP_VALUE), ("Synths", IP_SYNTH)))):
        desc = m.r32(item + 0x10)
        cb = m.r32(desc + 12)
        check(m.r32(item + 0xC) & 0xFF == 4 and m.r32(desc) == addr and m.r32(desc + 4) & 0xFF == 2
              and m.r32(desc + 8) == OFF_ON and CODE[0] <= cb < CODE[1] and cb & 1
              and m.cstr(m.r32(item + 8)) != "" and m.uc.mem_read(addr, 1)[0] == 0,
              f"'{label}': a stock Off/On item on its own setting, with help text and an apply callback "
              "in the patch, starting Off")
        for value in (1, 0):
            m.w8(folder + 0x1C, idx)
            n = len(closed)
            m.call(MENU_APPLY, 0, value, max_insns=2_000_000)
            check(m.uc.mem_read(addr, 1)[0] == value and len(closed) == n + 1,
                  f"{label}: Apply {'On' if value else 'Off'} stored, editor closed")
    check(m.r32(IP) == IP_MAGIC, "the patch's state is set up")


def file_checks(ptf: Path) -> None:
    print("settings file (interpolate.bin), through the screens' update hooks")
    m = Machine(ptf)
    fs = FakeFS(m, Path("."))
    calls = {"pattern": 0, "config": 0}
    m.stub_function(PATTERN_UPDATE, lambda mm: calls.__setitem__("pattern", calls["pattern"] + 1))
    m.stub_function(CONFIG_UPDATE, lambda mm: calls.__setitem__("config", calls["config"] + 1))
    pat_hook, cfg_hook = m.r32(PATTERN_UPDATE_SLOT), m.r32(CONFIG_UPDATE_SLOT)

    def tick(n=1, hook=None):
        for _ in range(n):
            m.call(hook or pat_hook, 0x20021770)

    def values():
        return m.uc.mem_read(IP_VALUE, 1)[0], m.uc.mem_read(IP_SYNTH, 1)[0]

    def path(name):
        return f"Projects/User/{name}/interpolate.bin"

    def writes():
        return len([x for x in fs.log if "interpolate.bin mode 3" in x])

    def hip1(midi, synth):
        data = bytes([midi, synth, 0, 0])
        return FILE_MAGIC + data + struct.pack("<I", fnv1a(data))

    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(values() == (0, 0), "project without interpolate.bin: both Off")
    tick(SAVE_DELAY + 5)
    check(writes() == 0, "nothing written while nothing changed")
    m.w8(IP_VALUE, 1)
    tick(SAVE_DELAY - 2, cfg_hook)
    check(writes() == 0, "no write right after a change")
    tick(5, cfg_hook)
    data = fs.overlay.get(path("A"))
    check(writes() == 1 and data == hip1(1, 0), "the change is written once, a while later (HIP1, 12 bytes)")
    m.w8(IP_SYNTH, 1)
    tick(SAVE_DELAY + 5)
    check(writes() == 2 and fs.overlay.get(path("A")) == hip1(1, 1), "turning synths On is saved too (byte 5)")
    tick(SAVE_DELAY * 2)
    check(writes() == 2, "no rewrite while nothing changes")
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    check(values() == (0, 0), "another project without the file: both Off")
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(values() == (1, 1), "back to the first project: both On")
    fs.overlay[path("D")] = hip1(1, 0)                     # as written before the synth setting
    fs.set_project(Path("."), "D", "Projects/User")
    tick()
    check(values() == (1, 0), "a file from before the synth setting: MIDI On, synths Off")
    good = fs.overlay[path("A")]
    fs.overlay[path("A")] = good[:11] + bytes([good[11] ^ 1])
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(values() == (0, 0), "damaged file: both Off")
    fs.overlay[path("A")] = good
    m.write(PROJECT_NAME, b"\0")
    tick()
    m.w8(IP_VALUE, 1)
    m.w8(IP_SYNTH, 1)
    n = writes()
    tick(SAVE_DELAY * 2)
    check(writes() == n, "project without a name: nothing written")

    def put(v):
        m.w8(IP_VALUE, v[0])
        m.w8(IP_SYNTH, v[1])

    save_as_checks(check, m, fs, tick, "interpolate.bin", values, put, (0, 0), (1, 1), (0, 1),
                   lambda v: hip1(*v), SAVE_DELAY)
    new_project_checks(check, m, fs, tick, "interpolate.bin", values, (0, 0), (1, 1), lambda v: hip1(*v), SAVE_DELAY)
    check(calls["pattern"] > 0 and calls["config"] > 0, f"stock update methods ran ({calls})")


def fnv1a(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h


def main() -> int:
    global IP, IP_VALUE, IP_SYNTH, CODE
    ptf = Path(sys.argv[1])
    IP = patch_ram(ptf, "interpolate")
    CODE = patch_code(ptf, "interpolate")
    IP_VALUE = IP + 8
    IP_SYNTH = IP + 16
    playback_checks(ptf)
    cost_checks(ptf)
    menu_checks(ptf)
    file_checks(ptf)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
