"""Check the humanize patch on the patched firmware: the Pattern screen button and the edit.

  python tools/emu/test_humanize.py <patched.ptf>

Works with the humanize-only build and with bundles that include it.

1. Button: runs the stock page-3 button binding (0x600e29a4) with a fake button
   manager. In record mode button 2 must be bound to a handler in the patch; outside
   record mode it must stay unbound. The other buttons keep their stock bindings.
2. Label: runs the stock page-3 label code (0x600e584c) with the display calls
   stubbed and recorded. Button 2 must read "Humanize" and get Track Name's data, and
   the active color set in record mode or the greyed-out one outside it. The other
   labels are unchanged.
3. Edit: presses the button (calls the bound handler) with a pattern in RAM and the
   editor state set up, through the stock selection, undo and changed-flag code.
   Outside record mode a press must change nothing (no edit, no undo entry).
   Every step is checked against the rules, for timing range T and volume range R:
     - steps without a note (empty, note off, cut, fade) are unchanged;
     - note and instrument never change, nor do other FX;
     - an existing V gets a value in the window of R around it within 1..100 (V 0
       stays 0), an existing m one in the window of T around it within 0..100. A
       window is v - R/2 .. v - R/2 + R, slid to stay inside the limits;
     - a missing m goes into the first free slot as 0..T, then a missing V into a
       free slot, in the window of R around the default for the step's instrument
       (samples 0..47, MIDI and synths from 48);
     - with Volume/velocity off, V FX are neither changed nor added;
     - nothing outside the selection (or, without a selection, outside the cursor's
       track up to the pattern length) changes.
   Also: the undo ring holds the pattern as it was, the pattern is marked changed,
   and the grid rows are rebuilt once.
4. Ranges: 300 presses on a fresh pattern each time; every value of each window must
   show up, with the default settings (new m 0..10, V and m 45..55 from 50, 90..100
   from 98, 0..10 from 1) and with others (both ranges 20, timing range 20 with
   volume range 4 and the other way round, Volume/velocity off, MIDI velocity 100 for
   MIDI and synth instruments, sample volume 30).
5. Config menu: runs the stock menu constructor (0x60118acc). Project Settings must
   hold its 10 stock items and then (after any other patch's entries listed before
   it) a "Humanize" folder with Timing range,
   Volume/velocity, Volume range, MIDI velocity and Sample volume, set up as stock
   number and option items on the patch's settings, which start at their defaults
   (10, On, 10, 50, 50).
6. Settings file: drives the Pattern and Config screen update hooks with a fake SD
   card. A project without humanize.bin gets the defaults and no file is written; a
   changed value is written once, a while later; another project gets its own values;
   coming back loads the saved ones; a damaged file gives the defaults; a file from
   an earlier version (HHZ1, one range) gives both ranges its range; a project
   without a name writes nothing. Save As and new projects through the firmware's
   save request (project_save.save_as_checks): a new project writes nothing until its
   first save, and Save As keeps the values and writes them to the new folder. The
   stock update methods run on every call.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakefs import PROJECT_NAME, FakeFS  # noqa: E402
from layout import patch_code, patch_ram  # noqa: E402
from machine import Machine  # noqa: E402
from project_save import save_as_checks  # noqa: E402
from song_grid import PATTERN_DATA_SIZE  # noqa: E402

SEQ = 0x20017274                    # bytes 0..3: selection rectangle; +0x14: pattern data
SEQ_PATTERN = SEQ + 0x14
PATVIEW = 0x20021770                # Pattern screen
ED = PATVIEW + 0x1000               # its editor state
ED_RECORD = 0x254                   # record mode flag
DISPLAY = 0x20027D64                # *(DISPLAY + 8) -> label color sets
STYLE_ACTIVE, STYLE_GREY = 0x44, 0x54
FM = 0x2001E488                     # file manager
BUTTON_MANAGER_PTR = 0x200209E0
PAGE3_BIND = 0x600E29A4
PAGE3_LABELS = 0x600E584C
FILL_ROWS = 0x600DF3A0
UNDO_STATE = 0x2001F610             # [0] next slot, [1] count
UNDO_RING = 0x83F811CC
UNDO_SLOT = 0x3030
CUR_PATTERN = 0x202046C8
CHANGED = FM + 0x4CA                # + pattern number
STOCK_HANDLERS = {0: 0x600DFE65, 1: 0x600DFDD1, 7: 0x600E2A45}
HZ = 0                              # humanize state (magic, session, value[5], ...): from the .map.json
HZ_MAGIC = 0x395A484D
HZ_VALUE = 0                        # HZ + 8: timing-1, volume (0 Off / 1 On), MIDI-1, sample-1, volume range-1
MENU_CTOR = 0x60118ACC
ATEXIT = 0x600A3594
PROJECT_SETTINGS = 0x20028714
MENU_CURRENT = 0x20028180           # -> the folder the Config screen shows
MENU_SELECT, MENU_BACK = 0x60118928, 0x60118904
MENU_FORMAT, MENU_REDRAW, MENU_EDITOR = 0x6011870C, 0x60118828, 0x601185D0
MENU_APPLY, MENU_EDITOR_CLOSE = 0x60118658, 0x600CAB60   # (screen, value): store, callback, close
OFF_ON = 0x60060B88
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
CONFIG_UPDATE_SLOT, CONFIG_UPDATE = 0x6003B5F8, 0x600C6474
FILE_MAGIC, OLD_MAGIC = b"HHZ2", b"HHZ1"
DEFAULT_VALUES = bytes([9, 1, 49, 49, 9])
SAVE_DELAY = 25
DISPLAY_CALLS = {0x60101D5C: "text", 0x60101D74: "text2", 0x60101DA4: "colors",
                 0x60101DF8: "data", 0x60101D8C: "value", 0x60101CAC: "style",
                 0x60101CD0: "style_fields", 0x60101E84: "refresh", 0x60102014: "sync"}

TRACK = 0x301
STEPS = 0x1D
EMPTY, OFF, CUT, FADE = 0xFF, 0xFC, 0xFD, 0xFE
FX_M, FX_V, FX_CUTOFF, FX_PAN = 2, 18, 27, 31

CODE = (0, 0)                       # set in main(): the patch's code, start and end
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def fnv1a(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h


def new_pattern(last_step: int) -> bytearray:
    d = bytearray(PATTERN_DATA_SIZE)
    for t in range(16):
        base = 0x1C + TRACK * t
        d[base] = last_step
        for s in range(128):
            d[base + 1 + 6 * s:base + 7 + 6 * s] = bytes([EMPTY, 0, 0, 0, 0, 0])
    return d


def put(d: bytearray, t: int, s: int, note: int, fx1=(0, 0), fx2=(0, 0), ins: int = 3) -> None:
    o = STEPS + TRACK * t + 6 * s
    d[o:o + 6] = bytes([note, ins, fx1[0], fx1[1], fx2[0], fx2[1]])


def step(d: bytes, t: int, s: int) -> bytes:
    o = STEPS + TRACK * t + 6 * s
    return bytes(d[o:o + 6])


class Params:
    def __init__(self, timing=10, volume_range=10, volume=True, midi=50, sample=50):
        self.timing, self.volume_range = timing, volume_range
        self.volume, self.midi, self.sample = volume, midi, sample


DEFAULTS = Params()


def window(c: int, r: int, lo: int, hi: int) -> tuple[int, int]:
    a = c - r // 2
    b = a + r
    if b - a >= hi - lo:
        return lo, hi
    if a < lo:
        return lo, b + lo - a
    if b > hi:
        return a - (b - hi), hi
    return a, b


def rule_error(before: bytes, after: bytes, p: Params = DEFAULTS) -> str | None:
    """Why `after` breaks the humanize rules for a step that was `before` (None = ok)."""
    if after[:2] != before[:2]:
        return "note or instrument changed"
    if before[0] >= 0x80:                        # empty, note off, cut, fade
        return None if after == before else "changed a step without a note"
    slots_b = [(before[2], before[3]), (before[4], before[5])]
    slots_a = [(after[2], after[3]), (after[4], after[5])]
    adds = []
    if not any(t == FX_M for t, _ in slots_b):
        adds.append(FX_M)
    if p.volume and not any(t == FX_V for t, _ in slots_b):
        adds.append(FX_V)
    v_base = p.sample if before[1] <= 47 else p.midi
    for (tb, vb), (ta, va) in zip(slots_b, slots_a):
        if tb == FX_V:
            if ta != FX_V:
                return "V changed type"
            if (vb == 0 or not p.volume) and va != vb:
                return f"V {vb} changed to {va}"
            lo, hi = window(vb, p.volume_range, 1, 100)
            if vb and p.volume and not lo <= va <= hi:
                return f"V {vb} -> {va}, window {lo}..{hi}"
        elif tb == FX_M:
            lo, hi = window(vb, p.timing, 0, 100)
            if ta != FX_M or not lo <= va <= hi:
                return f"m {vb} -> {ta}:{va}, window {lo}..{hi}"
        elif tb != 0:
            if (ta, va) != (tb, vb):
                return f"other FX {tb}:{vb} changed"
        elif adds:
            kind = adds.pop(0)
            lo, hi = (0, p.timing) if kind == FX_M else window(v_base, p.volume_range, 1, 100)
            if ta != kind or not lo <= va <= hi:
                return f"free slot got {ta}:{va}, expected {'m' if kind == FX_M else 'V'} {lo}..{hi}"
        elif (ta, va) != (tb, vb):
            return "free slot filled with nothing to add"
    return None


class Rig:
    def __init__(self, ptf: Path):
        self.m = Machine(ptf)
        m = self.m
        self.mgr = m.alloc(0x40)
        self.entries = m.alloc(8 * 0x14)
        m.w8(self.mgr + 5, 8)
        m.w32(self.mgr + 0x1C, self.entries)
        m.w32(BUTTON_MANAGER_PTR, self.mgr)
        self.buf = m.alloc(UNDO_SLOT + 16)
        m.w32(SEQ_PATTERN, self.buf)
        m.w8(CUR_PATTERN, 5)
        self.fill_rows = 0
        self.display: list[tuple[str, int, int]] = []

        def on_fill_rows(mm):
            self.fill_rows += 1
        m.stub_function(FILL_ROWS, on_fill_rows)
        for addr, name in DISPLAY_CALLS.items():
            m.stub_function(addr, lambda mm, n=name: self.display.append((n, mm.arg(1), mm.arg(2))))

    def entry(self, i: int) -> tuple[int, int]:
        e = self.entries + 0x14 * i
        return self.m.r32(e) & 0xFF, self.m.r32(e + 4)

    def load(self, d: bytes) -> None:
        self.m.write(self.buf, bytes(d))

    def pattern(self) -> bytes:
        return bytes(self.m.uc.mem_read(self.buf, PATTERN_DATA_SIZE))

    def settings(self, p: Params) -> None:
        self.m.w32(HZ, HZ_MAGIC)
        self.m.write(HZ_VALUE, bytes([p.timing - 1, int(p.volume), p.midi - 1, p.sample - 1,
                                      p.volume_range - 1]))

    def record(self, on: bool) -> None:
        self.m.w8(ED + ED_RECORD, 1 if on else 0)

    def editor(self, cursor_track: int, selection=None) -> None:
        m = self.m
        m.w8(ED + 0x1E4, cursor_track)
        m.w16(ED + 0x1E6, 0)
        m.w8(ED + 0x1FE, 0)
        if selection is None:
            m.w8(ED + 0x1F4, 0)
        else:
            (t0, s0), (t1, s1) = selection
            m.w8(ED + 0x1F4, 2)
            m.w8(ED + 0x1F7, t0)
            m.w16(ED + 0x1F8, s0)
            m.w8(ED + 0x1FA, t1)
            m.w16(ED + 0x1FC, s1)


def scenario_pattern() -> bytearray:
    d = new_pattern(31)                          # 32 steps
    put(d, 0, 0, 48)                                             # no FX
    put(d, 0, 1, EMPTY, (FX_CUTOFF, 40))                         # FX without a note
    put(d, 0, 2, OFF)
    put(d, 0, 3, CUT)
    put(d, 0, 4, FADE, (FX_V, 50))
    put(d, 0, 5, 50, (FX_CUTOFF, 40))                            # one free slot
    put(d, 0, 6, 50, (FX_CUTOFF, 40), (FX_PAN, 60))              # no free slot
    put(d, 0, 7, 50, (0, 0), (FX_V, 50))                         # V in FX2
    put(d, 0, 8, 50, (FX_V, 3))                                  # V near the bottom
    put(d, 0, 9, 50, (FX_V, 100), (FX_M, 98))                    # both, near the top
    put(d, 0, 10, 50, (FX_V, 0))                                 # V 0 stays
    put(d, 0, 11, 50, (FX_M, 2))                                 # m near the bottom
    put(d, 0, 12, 50, (FX_M, 0), (FX_V, 50))
    put(d, 0, 13, 50, (FX_CUTOFF, 40), (FX_M, 50))               # m, no room for V
    put(d, 0, 31, 60)                                            # last step
    put(d, 0, 32, 60)                                            # past the pattern length
    for t in (1, 2):
        for s in range(40):
            put(d, t, s, 36 + s % 12)
    return d


def check_rules(before: bytes, after: bytes, inside, p: Params = DEFAULTS) -> tuple[int, list[str]]:
    changed, errors = 0, []
    for t in range(16):
        for s in range(128):
            b, a = step(before, t, s), step(after, t, s)
            if a != b:
                changed += 1
            if inside(t, s):
                e = rule_error(b, a, p)
                if e:
                    errors.append(f"track {t} step {s}: {e}")
            elif a != b:
                errors.append(f"track {t} step {s}: changed outside the selection")
    return changed, errors


def main() -> int:
    global HZ, HZ_VALUE, CODE
    ptf = Path(sys.argv[1])
    HZ = patch_ram(ptf, "humanize")
    CODE = patch_code(ptf, "humanize")
    HZ_VALUE = HZ + 8
    try:                                        # copypattern puts its button on button 3
        patch_code(ptf, "copypattern")
        free = range(4, 7)
    except SystemExit:
        free = range(3, 7)
    try:                                        # polymeter's Trk Len takes the next free one
        patch_code(ptf, "polymeter")
        free = range(free.start + 1, 7)
    except SystemExit:
        pass
    r = Rig(ptf)
    m = r.m

    print("button binding (Pattern screen, page 3)")
    r.record(False)
    m.call(PAGE3_BIND, 0)
    check(r.entry(2)[0] == 0, "outside record mode: button 2 stays unbound")
    r.record(True)
    m.call(PAGE3_BIND, 0)
    kind, handler = r.entry(2)
    in_patch = CODE[0] <= handler < CODE[1] and handler & 1
    check(kind == 1 and bool(in_patch), f"record mode: button 2 bound to a patch handler ({kind}, {handler:#x})")
    for i, h in STOCK_HANDLERS.items():
        check(r.entry(i) == (1, h), f"button {i} keeps its stock handler {h:#x}")
    check(all(r.entry(i)[0] == 0 for i in free), f"buttons {free.start}..{free.stop - 1} stay unbound")
    if not (kind == 1 and in_patch):
        print("no Humanize button: is this a humanize build?")
        return 1

    print("button labels")
    ctrls = list(range(20, 28))
    for i, c in enumerate(ctrls):
        m.w8(ED + 0x292 + i, c)
    styles = m.r32(DISPLAY + 8)
    for rec in (True, False):
        r.record(rec)
        r.display.clear()
        m.call(PAGE3_LABELS, PATVIEW)
        texts = {c: m.cstr(p) for n, c, p in r.display if n == "text"}
        colors = {c: p for n, c, p in r.display if n == "colors"}
        data = {c: p for n, c, p in r.display if n == "data"}
        mode = "record mode" if rec else "outside record mode"
        check(texts.get(22) == "Humanize", f"{mode}: button 2 text {texts.get(22)!r}")
        check(texts.get(20) == "Pattern" and texts.get(21) == "Track" and texts.get(27) == "More"
              and all(texts.get(20 + c) == "" for c in free), f"{mode}: other button texts unchanged")
        want = styles + (STYLE_ACTIVE if rec else STYLE_GREY)
        check(colors.get(22) == want and colors.get(21) == styles + STYLE_ACTIVE,
              f"{mode}: button 2 colors {'active' if rec else 'greyed out'}, Track Name active")
        check(22 in data and data[22] == data.get(21), f"{mode}: button 2 gets Track Name's data")

    print("press outside record mode: nothing happens")
    before = scenario_pattern()
    r.load(before)
    r.editor(cursor_track=0)
    r.record(False)
    m.w8(UNDO_STATE, 0)
    m.w8(UNDO_STATE + 1, 0)
    m.w8(CHANGED + 5, 0)
    r.fill_rows = 0
    m.call(handler)
    check(r.pattern() == bytes(before), "pattern unchanged")
    check(m.r32(UNDO_STATE) & 0xFFFF == 0 and m.r32(CHANGED + 5) & 0xFF == 0 and r.fill_rows == 0,
          "no undo entry, not marked changed, grid not rebuilt")
    r.record(True)

    print("humanize, no selection: the cursor's whole track")
    before = scenario_pattern()
    r.load(before)
    r.editor(cursor_track=0)
    m.w8(UNDO_STATE, 0)
    m.w8(UNDO_STATE + 1, 0)
    m.w8(CHANGED + 5, 0)
    r.fill_rows = 0
    m.call(handler)
    after = r.pattern()
    changed, errors = check_rules(before, after, lambda t, s: t == 0 and s <= 31)
    check(not errors, "all steps follow the rules" + ("" if not errors else ": " + "; ".join(errors[:4])))
    added = {s: (step(after, 0, s)[2], step(after, 0, s)[4]) for s in (0, 5, 7, 8, 10, 11, 31)}
    want = {0: (FX_M, FX_V), 5: (FX_CUTOFF, FX_M), 7: (FX_M, FX_V), 8: (FX_V, FX_M),
            10: (FX_V, FX_M), 11: (FX_M, FX_V), 31: (FX_M, FX_V)}
    check(added == want, "missing m / V added in the free slots (m first)")
    check(step(after, 0, 6) == step(before, 0, 6), "a step with two other FX is unchanged")
    check(step(after, 0, 32) == step(before, 0, 32), "the step past the pattern length is unchanged")
    check(step(after, 0, 10)[2:4] == bytes([FX_V, 0]), "V 0 stays 0")
    ring = bytes(m.uc.mem_read(UNDO_RING, PATTERN_DATA_SIZE))
    check(ring == bytes(before), "undo ring holds the pattern as it was")
    check(m.r32(UNDO_STATE) & 0xFFFF == 0x0101, "undo ring advanced by one")
    check(m.r32(CHANGED + 5) & 0xFF == 1, "pattern marked changed")
    check(r.fill_rows == 1, f"grid rows rebuilt once ({r.fill_rows})")

    print("humanize, selection: tracks 1..2, steps 4..8")
    before = scenario_pattern()
    r.load(before)
    r.editor(cursor_track=1, selection=((2, 8), (1, 4)))      # corners in either order
    m.call(handler)
    after = r.pattern()
    changed, errors = check_rules(before, after, lambda t, s: t in (1, 2) and 4 <= s <= 8)
    check(not errors, "only the selection changes, by the rules"
          + ("" if not errors else ": " + "; ".join(errors[:4])))
    check(changed == 10, f"10 steps changed ({changed})")

    print("ranges over 300 presses")
    seen = {"new m": set(), "new V": set(), "m from 50": set(), "V from 50": set()}
    base = new_pattern(1)
    put(base, 0, 0, 48)
    put(base, 0, 1, 48, (FX_M, 50), (FX_V, 50))
    r.editor(cursor_track=0)
    for _ in range(300):
        r.load(base)
        m.call(handler)
        p = r.pattern()
        a0, a1 = step(p, 0, 0), step(p, 0, 1)
        seen["new m"].add(a0[3])
        seen["new V"].add(a0[5])
        seen["m from 50"].add(a1[3])
        seen["V from 50"].add(a1[5])
    for name, want in (("new m", range(0, 11)), ("new V", range(45, 56)),
                       ("m from 50", range(45, 56)), ("V from 50", range(45, 56))):
        got = seen[name]
        check(got == set(want), f"{name}: {min(got)}..{max(got)}, {len(got)} values")

    print("ranges near the limits (defaults)")
    base = new_pattern(1)
    put(base, 0, 0, 48, (FX_V, 98), (FX_M, 1))
    seen = {"V from 98": set(), "m from 1": set()}
    for _ in range(300):
        r.load(base)
        m.call(handler)
        a0 = step(r.pattern(), 0, 0)
        seen["V from 98"].add(a0[3])
        seen["m from 1"].add(a0[5])
    for name, want in (("V from 98", range(90, 101)), ("m from 1", range(0, 11))):
        got = seen[name]
        check(got == set(want), f"{name}: {min(got)}..{max(got)}, {len(got)} values (window slides)")

    print("settings: ranges, Volume/velocity off, MIDI velocity 100, sample volume 30")
    for p, label in ((Params(timing=20, volume_range=20), "both ranges 20"),
                     (Params(timing=20, volume_range=4), "timing 20, volume 4"),
                     (Params(timing=4, volume_range=20), "timing 4, volume 20"),
                     (Params(volume=False), "Volume/velocity off"),
                     (Params(midi=100, sample=30), "MIDI 100, sample 30")):
        r.settings(p)
        before = scenario_pattern()
        r.load(before)
        r.editor(cursor_track=0)
        m.call(handler)
        changed, errors = check_rules(before, r.pattern(), lambda t, s: t == 0 and s <= 31, p)
        check(not errors, f"{label}: all steps follow the rules"
              + ("" if not errors else ": " + "; ".join(errors[:4])))
    base = new_pattern(1)
    put(base, 0, 0, 48)                                   # sample instrument 3
    put(base, 0, 1, 48, (FX_M, 50), (FX_V, 50))
    put(base, 1, 0, 48, ins=48)                           # MIDI channel 1
    put(base, 2, 0, 48, ins=64)                           # synth 1
    seen = {k: set() for k in ("new m", "V from 50", "sample V", "MIDI V", "synth V")}
    r.settings(Params(timing=20, volume_range=20, midi=100, sample=30))
    r.editor(cursor_track=0, selection=((0, 0), (2, 1)))
    for _ in range(300):
        r.load(base)
        m.call(handler)
        pt = r.pattern()
        seen["new m"].add(step(pt, 0, 0)[3])
        seen["sample V"].add(step(pt, 0, 0)[5])
        seen["V from 50"].add(step(pt, 0, 1)[5])
        seen["MIDI V"].add(step(pt, 1, 0)[5])
        seen["synth V"].add(step(pt, 2, 0)[5])
    for name, want in (("new m", range(0, 21)), ("V from 50", range(40, 61)), ("sample V", range(20, 41)),
                       ("MIDI V", range(80, 101)), ("synth V", range(80, 101))):
        got = seen[name]
        check(got == set(want), f"range 20: {name}: {min(got)}..{max(got)}, {len(got)} values")
    for timing, vrange, want in ((20, 4, {"new m": range(0, 21), "m from 50": range(40, 61),
                                          "V from 50": range(48, 53), "new V": range(28, 33)}),
                                 (4, 20, {"new m": range(0, 5), "m from 50": range(48, 53),
                                          "V from 50": range(40, 61), "new V": range(20, 41)})):
        r.settings(Params(timing=timing, volume_range=vrange, sample=30))
        r.editor(cursor_track=0)
        two = new_pattern(1)
        put(two, 0, 0, 48)
        put(two, 0, 1, 48, (FX_M, 50), (FX_V, 50))
        seen = {k: set() for k in want}
        for _ in range(300):
            r.load(two)
            m.call(handler)
            pt = r.pattern()
            seen["new m"].add(step(pt, 0, 0)[3])
            seen["new V"].add(step(pt, 0, 0)[5])
            seen["m from 50"].add(step(pt, 0, 1)[3])
            seen["V from 50"].add(step(pt, 0, 1)[5])
        for name, w in want.items():
            got = seen[name]
            check(got == set(w), f"timing range {timing}, volume range {vrange}: {name}: "
                  f"{min(got)}..{max(got)}, {len(got)} values")
    r.settings(Params(volume=False))
    r.load(base)
    m.call(handler)
    pt = r.pattern()
    check(step(pt, 0, 0)[2:] == bytes([FX_M, step(pt, 0, 0)[3], 0, 0]) and step(pt, 0, 1)[4:] == bytes([FX_V, 50]),
          "Volume/velocity off: only m added, existing V kept")

    menu_checks(ptf)
    file_checks(ptf)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


def menu_checks(ptf: Path) -> None:
    print("Config menu: Project Settings > Humanize")
    m = Machine(ptf)
    m.stub_function(ATEXIT, lambda mm: 0)
    m.call(MENU_CTOR, max_insns=50_000_000)
    ps = PROJECT_SETTINGS
    cap, count = m.r32(ps + 8) & 0xFF, m.r32(ps + 0x1C) >> 8 & 0xFF
    kids = [m.r32(m.r32(ps + 0xC) + 4 * i) for i in range(min(cap, count))]
    labels = [m.cstr(m.r32(k + (0x18 if m.r32(k) & 0xFF == 1 else 4))) for k in kids]
    stock = ["Performance presets", "Pattern divider", "Notes scale", "Root note", "Notes layout",
             "Anti-aliasing", "Limiter mode", "Sends mode", "Extra headroom", "Snap to zero"]
    check(cap == count and count >= 11, f"Project Settings: room for {cap}, holds {count}")
    check(labels[:10] == stock, "the 10 stock items are unchanged, in order")
    if "Humanize" not in labels[10:]:
        check(False, f"a Humanize folder after the stock items ({labels[10:]})")
        return
    at = labels.index("Humanize", 10)
    folder = kids[at]
    check(m.r32(folder) & 0xFF == 1, f"item {at + 1} is the folder 'Humanize' (after {labels[10:at]})")
    fcap, fcount = m.r32(folder + 8) & 0xFF, m.r32(folder + 0x1C) >> 8 & 0xFF
    items = [m.r32(m.r32(folder + 0xC) + 4 * i) for i in range(min(fcap, fcount))]
    want = [("Timing range", 2, HZ_VALUE), ("Volume/velocity", 4, HZ_VALUE + 1),
            ("Volume range", 2, HZ_VALUE + 4), ("MIDI velocity", 2, HZ_VALUE + 2),
            ("Sample volume", 2, HZ_VALUE + 3)]
    got = [(m.cstr(m.r32(i + 4)), m.r32(i + 0xC) & 0xFF, m.r32(m.r32(i + 0x10))) for i in items]
    check(got == want, f"its items: {[g[0] for g in got]}, on the patch's settings")
    numbers_ok = all(bytes(m.uc.mem_read(m.r32(i + 0x10) + 4, 4)) == bytes([0, 99, 1, 1])
                     for i, w in zip(items, want) if w[1] == 2)
    check(numbers_ok, "number items: index 0..99, shown + 1 (1..100)")
    opt = m.r32(items[1] + 0x10) if len(items) > 1 else 0
    check(opt and m.r32(opt + 4) & 0xFF == 2 and m.r32(opt + 8) == OFF_ON, "Volume/velocity: the stock Off/On list")
    check(all(m.cstr(m.r32(i + 8)) for i in items), "every item has help text")
    check(bytes(m.uc.mem_read(HZ_VALUE, 5)) == DEFAULT_VALUES and m.r32(HZ) == HZ_MAGIC,
          "settings start at the defaults (10, On, 50, 50, volume range 10)")

    print("Config menu navigation, with the stock code")
    redraws, editors = [], []
    m.stub_function(MENU_REDRAW, lambda mm: redraws.append(mm.r32(MENU_CURRENT)))
    m.stub_function(MENU_EDITOR, lambda mm: editors.append((mm.arg(1), mm.cstr(mm.arg(2)), mm.arg(4))))
    m.w32(MENU_CURRENT, ps)
    m.w8(ps + 0x1C, at)                                  # Project Settings: the Humanize entry
    m.call(MENU_SELECT)
    check(m.r32(MENU_CURRENT) == folder and redraws[-1:] == [folder], "select opens the Humanize folder")
    m.call(MENU_FORMAT, folder)
    texts = [m.cstr(m.r32(m.r32(folder + 0x14) + 4 * i)) for i in range(len(items))]
    check(texts == ["10", "On", "10", "50", "50"], f"values shown: {texts}")
    m.call(MENU_FORMAT, ps)
    ps_texts = [m.cstr(m.r32(m.r32(ps + 0x14) + 4 * i)) for i in range(count)]
    check(ps_texts[at] == "" and all(ps_texts[:10]), "Project Settings list: stock values, no value for the folder")
    m.w8(folder + 0x1C, 0)                               # Timing range
    m.call(MENU_SELECT)
    check(editors[-1:] == [(2, "Timing range", m.r32(items[0] + 0x10))],
          "select on Timing range opens the stock number editor")
    m.w8(folder + 0x1C, 2)                               # Volume range
    m.call(MENU_SELECT)
    check(editors[-1:] == [(2, "Volume range", m.r32(items[2] + 0x10))],
          "select on Volume range opens the stock number editor")
    m.w8(folder + 0x1C, 1)                               # Volume/velocity
    m.call(MENU_SELECT)
    check(editors[-1:] == [(4, "Volume/velocity", m.r32(items[1] + 0x10))], "select on Volume/velocity opens the option editor")
    closed = []
    m.stub_function(MENU_EDITOR_CLOSE, lambda mm: closed.append(1))
    for name, value in (("Timing range", 19), ("Volume/velocity", 0), ("Volume/velocity", 1),
                        ("Volume range", 3), ("MIDI velocity", 99), ("Sample volume", 29)):
        idx = [w[0] for w in want].index(name)
        m.w8(folder + 0x1C, idx)
        n = len(closed)
        m.call(MENU_APPLY, 0, value, max_insns=2_000_000)  # the editor's Apply, stock code
        check(m.uc.mem_read(want[idx][2], 1)[0] == value and len(closed) == n + 1,
              f"Apply on {name} ({value}): stored, editor closed")
    opt = m.r32(items[1] + 0x10)
    cb = m.r32(opt + 12)
    check(CODE[0] <= cb < CODE[1] and cb & 1, f"Volume/velocity has an apply callback in the patch ({cb:#x})")
    back = m.call(MENU_BACK)
    check(back == 1 and m.r32(MENU_CURRENT) == ps, "back returns to Project Settings")
    back = m.call(MENU_BACK)
    check(back == 0 and m.r32(MENU_CURRENT) == ps, "back from Project Settings leaves the menu, as stock")


def file_checks(ptf: Path) -> None:
    print("settings file (humanize.bin), through the screens' update hooks")
    m = Machine(ptf)
    fs = FakeFS(m, Path("."))
    calls = {"pattern": 0, "config": 0}

    def count(name):
        def f(mm):
            calls[name] += 1
        return f
    m.stub_function(PATTERN_UPDATE, count("pattern"))
    m.stub_function(CONFIG_UPDATE, count("config"))
    pat_hook, cfg_hook = m.r32(PATTERN_UPDATE_SLOT), m.r32(CONFIG_UPDATE_SLOT)
    check(pat_hook != PATTERN_UPDATE | 1 and cfg_hook != CONFIG_UPDATE | 1, "both update methods are hooked")

    def tick(n=1, hook=None):
        for _ in range(n):
            m.call(hook or pat_hook, PATVIEW)

    def values():
        return bytes(m.uc.mem_read(HZ_VALUE, 5))

    def path(name):
        return f"Projects/User/{name}/humanize.bin"

    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(values() == DEFAULT_VALUES, "project without humanize.bin: defaults")
    tick(SAVE_DELAY + 5)
    check(fs.writes_opened == 0, "nothing written while nothing changed")
    m.write(HZ_VALUE, bytes([19, 0, 99, 29, 3]))          # as the Config menu would
    tick(SAVE_DELAY - 2, cfg_hook)
    check(fs.writes_opened == 0, "no write right after a change")
    tick(5, cfg_hook)
    data = fs.overlay.get(path("A"))
    check(fs.writes_opened == 1 and data is not None and data[:4] == FILE_MAGIC
          and data[4:12] == bytes([19, 0, 99, 29, 3, 0, 0, 0]) and len(data) == 16,
          "the change is written once, a while later (HHZ2, 16 bytes)")
    check(max(fs.write_sizes or [0]) <= 256, "writes of at most 256 bytes")
    tick(SAVE_DELAY * 2)
    check(fs.writes_opened == 1, "no rewrite while nothing changes")
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    check(values() == DEFAULT_VALUES, "another project without the file: defaults")
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(values() == bytes([19, 0, 99, 29, 3]), "back to the first project: its saved values")
    good = fs.overlay[path("A")]
    fs.overlay[path("A")] = good[:15] + bytes([good[15] ^ 1])
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(values() == DEFAULT_VALUES, "damaged file: defaults")
    fs.overlay[path("A")] = good
    old = bytes([24, 1, 79, 39])                          # HHZ1: range 25, On, 80, 40
    fs.overlay[path("D")] = OLD_MAGIC + old + struct.pack("<I", fnv1a(old))
    fs.set_project(Path("."), "D", "Projects/User")
    opened = fs.writes_opened
    tick()
    check(values() == bytes([24, 1, 79, 39, 24]), "earlier version's file (HHZ1): both ranges get its range")
    tick(SAVE_DELAY + 5)
    check(fs.writes_opened == opened, "and it is not rewritten while nothing changes")
    m.w8(HZ_VALUE + 4, 4)
    tick(SAVE_DELAY + 5)
    data = fs.overlay.get(path("D"))
    check(data[:4] == FILE_MAGIC and data[4:9] == bytes([24, 1, 79, 39, 4]), "a change rewrites it as HHZ2")
    fs.overlay[path("D")] = OLD_MAGIC + old + struct.pack("<I", fnv1a(old) ^ 1)
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    fs.set_project(Path("."), "D", "Projects/User")
    tick()
    check(values() == DEFAULT_VALUES, "damaged HHZ1 file: defaults")
    m.write(PROJECT_NAME, b"\0")                           # the name cleared while Workspace loads
    tick()
    check(values() == DEFAULT_VALUES, "project without a name: defaults")
    opened = fs.writes_opened
    m.write(HZ_VALUE, bytes([4, 1, 69, 69, 7]))
    tick(SAVE_DELAY * 2)
    check(fs.writes_opened == opened, "project without a name: nothing written")

    def hhz2(v):
        data = bytes(v) + bytes(3)
        return FILE_MAGIC + data + struct.pack("<I", fnv1a(data))

    save_as_checks(check, m, fs, tick, "humanize.bin", values, lambda v: m.write(HZ_VALUE, bytes(v)),
                   DEFAULT_VALUES, bytes([4, 1, 69, 69, 7]), bytes([14, 0, 29, 9, 2]), hhz2, SAVE_DELAY)
    total = sum(calls.values())
    check(calls["pattern"] > 0 and calls["config"] > 0 and total == calls["pattern"] + calls["config"],
          f"stock update methods ran on every call ({calls['pattern']} + {calls['config']})")


if __name__ == "__main__":
    sys.exit(main())
