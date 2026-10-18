"""Check the humanize patch on the patched firmware: the Pattern screen button and the edit.

  python tools/emu/test_humanize.py <patched.ptf>

Works with the humanize-only build and with bundles that include it.

1. Button: runs the stock page-3 button binding (0x600e29a4) with a fake button
   manager. Button 2 must be bound to a handler in the patch; the other buttons keep
   their stock bindings.
2. Label: runs the stock page-3 label code (0x600e584c) with the display calls
   stubbed and recorded. Button 2 must read "Humanize" and get Track Name's colors
   and data; the other labels are unchanged.
3. Edit: presses the button (calls the bound handler) with a pattern in RAM and the
   editor state set up, through the stock selection, undo and changed-flag code.
   Every step is checked against the rules:
     - steps without a note (empty, note off, cut, fade) are unchanged;
     - note and instrument never change, nor do other FX;
     - an existing V moves by -5..+5 within 1..100 (V 0 stays 0), an existing m by
       -5..+5 within 0..100;
     - a missing m goes into the first free slot as 0..10, then a missing V into a
       free slot as 45..55;
     - nothing outside the selection (or, without a selection, outside the cursor's
       track up to the pattern length) changes.
   Also: the undo ring holds the pattern as it was, the pattern is marked changed,
   and the grid rows are rebuilt once.
4. Ranges: 300 presses on a fresh pattern each time; every value of each range
   (new m 0..10, new V 45..55, adjusted m and V 45..55 from 50) must show up.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from machine import Machine  # noqa: E402
from song_grid import PATTERN_DATA_SIZE  # noqa: E402

SEQ = 0x20017274                    # bytes 0..3: selection rectangle; +0x14: pattern data
SEQ_PATTERN = SEQ + 0x14
PATVIEW = 0x20021770                # Pattern screen
ED = PATVIEW + 0x1000               # its editor state
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
DISPLAY_CALLS = {0x60101D5C: "text", 0x60101D74: "text2", 0x60101DA4: "colors",
                 0x60101DF8: "data", 0x60101D8C: "value", 0x60101CAC: "style",
                 0x60101CD0: "style_fields", 0x60101E84: "refresh", 0x60102014: "sync"}

TRACK = 0x301
STEPS = 0x1D
EMPTY, OFF, CUT, FADE = 0xFF, 0xFC, 0xFD, 0xFE
FX_M, FX_V, FX_CUTOFF, FX_PAN = 2, 18, 27, 31

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


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


def rule_error(before: bytes, after: bytes) -> str | None:
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
    if not any(t == FX_V for t, _ in slots_b):
        adds.append(FX_V)
    for (tb, vb), (ta, va) in zip(slots_b, slots_a):
        if tb == FX_V:
            if ta != FX_V:
                return "V changed type"
            if vb == 0 and va != 0:
                return "V 0 changed"
            if vb and not max(1, vb - 5) <= va <= min(100, vb + 5):
                return f"V {vb} -> {va}"
        elif tb == FX_M:
            if ta != FX_M or not max(0, vb - 5) <= va <= min(100, vb + 5):
                return f"m {vb} -> {ta}:{va}"
        elif tb != 0:
            if (ta, va) != (tb, vb):
                return f"other FX {tb}:{vb} changed"
        elif adds:
            kind = adds.pop(0)
            lo, hi = (0, 10) if kind == FX_M else (45, 55)
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


def check_rules(before: bytes, after: bytes, inside) -> tuple[int, list[str]]:
    changed, errors = 0, []
    for t in range(16):
        for s in range(128):
            b, a = step(before, t, s), step(after, t, s)
            if a != b:
                changed += 1
            if inside(t, s):
                e = rule_error(b, a)
                if e:
                    errors.append(f"track {t} step {s}: {e}")
            elif a != b:
                errors.append(f"track {t} step {s}: changed outside the selection")
    return changed, errors


def main() -> int:
    ptf = Path(sys.argv[1])
    r = Rig(ptf)
    m = r.m

    print("button binding (Pattern screen, page 3)")
    m.call(PAGE3_BIND, 0)
    kind, handler = r.entry(2)
    in_patch = 0x601BAB78 <= handler < 0x601C0000 and handler & 1
    check(kind == 1 and bool(in_patch), f"button 2 bound to a patch handler ({kind}, {handler:#x})")
    for i, h in STOCK_HANDLERS.items():
        check(r.entry(i) == (1, h), f"button {i} keeps its stock handler {h:#x}")
    check(all(r.entry(i)[0] == 0 for i in range(3, 7)), "buttons 3..6 stay unbound")
    if not (kind == 1 and in_patch):
        print("no Humanize button: is this a humanize build?")
        return 1

    print("button labels")
    ctrls = list(range(20, 28))
    for i, c in enumerate(ctrls):
        m.w8(ED + 0x292 + i, c)
    r.display.clear()
    m.call(PAGE3_LABELS, PATVIEW)
    texts = {c: m.cstr(p) for n, c, p in r.display if n == "text"}
    colors = {c: p for n, c, p in r.display if n == "colors"}
    data = {c: p for n, c, p in r.display if n == "data"}
    check(texts.get(22) == "Humanize", f"button 2 text: {texts.get(22)!r}")
    check(texts.get(20) == "Pattern" and texts.get(21) == "Track" and texts.get(27) == "More"
          and all(texts.get(c) == "" for c in range(23, 27)), "other button texts unchanged")
    check(22 in colors and colors[22] == colors.get(21), "button 2 gets Track Name's colors")
    check(22 in data and data[22] == data.get(21), "button 2 gets Track Name's data")

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

    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
