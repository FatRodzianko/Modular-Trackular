"""Check the songloop patch: loop a selection of Song positions.

  python tools/emu/test_songloop.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the songloop-only build and with bundles that include it. Each check calls
the code a hooked site leads to (the BL target, the callback word or the ITCM stub), so
it runs whatever the bundle chained there, with the firmware functions around it
stubbed where they would need the whole UI.

1. Sites: every hooked BL, callback word and ITCM BL leads into the patch (or its stub).
2. Selecting: Shift + Down/Up in the pattern column with Rec on grows and shrinks the
   block from the cursor; without Shift, or with Rec off, the stock cursor move runs and
   the selection goes away. The patch's RAM full of garbage: it initializes itself.
3. Play Selection, stopped: Play Song starts from the block's start, the loop is on,
   the cursor goes back to the moved edge. Shift + Play does the same.
4. The preload (ITCM stubs): without a loop the same pattern as stock song_peek_next;
   at the block's end the block's first pattern, and the pattern end then goes to the
   block's start; not yet in the block: on as stock.
5. Continue Song ends the loop and the selection; Play Selection while playing arms it.
6. Stop: stock seq_stop ends the loop.
7. Locked with a block or during the loop: Undo, Add, Delete (softkeys and the Delete
   key), << Pattern >> (softkeys and Shift + jog) don't reach stock; without, they do.
   Jog without Shift during a loop of several positions doesn't move the cursor.
8. Labels: Play Selection / Continue Song; without a selection the stock labels (Loop
   Pattern, Loop Performance). Performance Play and the perform-flag reads are stock.
9. Status bar: the repeat signs (armed / looping) replace the play icon in the text.
10. Fonts: after a font load the glyphs and widths go to RAM_G, and the list icons.
11. The frame: the cursor frame covers the block (still and gliding).
12. The play icon with the perform flag and Rec on (a selection): the label hook takes
    the play position over before the Song update, which with Rec on wouldn't.
13. Performance stays on while the loop runs: a scheduled "Performance off" at the
    pattern end is cancelled (as Loop Performance does); Continue Song schedules it again.
14. Preload in Performance: with track 1 not on Fwd, the main loop requests the next
    pattern once, on the last two rows of track 16; not with Fwd, stopped or queued.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R5,
                               UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10)

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from patch_ptf import bl_target  # noqa: E402
from layout import patch_code, patch_ram  # noqa: E402
from machine import FLASH_BASE, STUB_RET, Machine  # noqa: E402

SONG = 0x20026D04
SONG_FOCUS, SONG_LABEL, SONG_SLOTS, SONG_CURSOR, SONG_REC, SONG_POPUP = 0xB1, 0xB2, 0xB4, 0xB6, 0x120, 0x11C
SONG_POS, PLAYLIST = 0x202046C1, 0x202045C2
SEQ = 0x20017274
FM = 0x2001E488
BUTTONS_PTR = 0x200209E8
SHIFT = 0x1D

PEEK_SITES = (0x23C4, 0x2ABE)
SONG_NEXT_SITE, LABELS_SITE, STOP_SITE, TICK_SITE = 0x600AD2D0, 0x600FDD82, 0x600ACD98, 0x600CC5EC
FRAME_SITES = (0x601086FA, 0x601088A4)
ICON_SITES = (0x60108520, 0x60108818, 0x601089F2, 0x60108B0C)
SPRINTF_SITE, COLORS_SITE = 0x600CF9F0, 0x600FE20E
FONT_SITES = (0x60101ADE, 0x600C5F82, 0x600CBEDE)
CHANGE_SITES = (0x600FE7CA, 0x600FE7E2, 0x600FE81C, 0x600FE836)
WORDS = {"jog": 0x600FD948, "play": 0x600FD94C, "up": 0x600FD95C, "down": 0x600FD960,
         "softkey0": 0x600FD96C, "undo": 0x600FD974, "add": 0x600FD978, "delete": 0x600FD97C,
         "delete_key": 0x600FD990}
PERFORM_PLAY_WORD = 0x600E6638      # stock: Performance's Play stays as it is

# stock functions the hooks lead to
SONG_PEEK_NEXT, SONG_NEXT_POSITION = 0x7030, 0x600C3970
SLOT_MOVE, SHOW_CURSOR, PLAY_SONG = 0x600FE078, 0x600FED80, 0x600FDA58
UNDO, ADD, DELETE, DELETE_KEY, CHANGE = 0x600EDDC8, 0x600FE100, 0x600FE520, 0x600D995C, 0x600FE738
STOCK_JOG, STOCK_SOFTKEY0 = 0x600C9590, 0x600FE600
BUTTON_IS_PRESSED = 0x600CF90C
DISP_FUNCS = {0x60101D5C: "text", 0x60101D74: "text2", 0x60101E84: "refresh", 0x60101DA4: "colors",
              0x60102014: "sync"}
SEQ_STOP, SEQ_PLAY_PATTERN = 0x600ACD90, 0x600AC5F8
POPUP_UPDATE, FM_LOAD, IRQ_OFF, IRQ_ON = 0x600CD0C8, 0x600BC938, 0x600303D4, 0x600303C8
SPRINTF = 0x60167194
LOAD_FONTS, FONT_SET = 0x601018E8, 0x601020EC
EVE_LINE_WIDTH = 0x6011F8B8
FONT4_ENTRY = 0x83FCD374 + 3 * 0xAC

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Rig:
    """A machine with the Song screen's surroundings stubbed; records the stub calls."""

    def __init__(self, ptf: Path, slots: int = 10):
        self.m = m = Machine(ptf)
        self.ram = patch_ram(ptf, "songloop") if ptf != STOCK else 0
        self.calls: list[tuple] = []
        self.held: set[int] = set()
        m.w32(BUTTONS_PTR, 0x20000100)
        m.stub_function(BUTTON_IS_PRESSED, lambda mm: int(mm.arg(1) in self.held))
        for addr, name in [(SLOT_MOVE, "slot_move"), (SHOW_CURSOR, "show_cursor"), (UNDO, "undo"),
                           (ADD, "add"), (DELETE, "delete"), (DELETE_KEY, "delete_key"), (CHANGE, "change"),
                           (STOCK_JOG, "jog"), (STOCK_SOFTKEY0, "softkey0"), (SEQ_PLAY_PATTERN, "play_pattern"),
                           (POPUP_UPDATE, "popup"), (FM_LOAD, "fm_load"), (IRQ_OFF, "irq_off"),
                           (IRQ_ON, "irq_on"), (EVE_LINE_WIDTH, "line_width"), (LOAD_FONTS, "load_fonts"),
                           (FONT_SET, "font_set")] + list(DISP_FUNCS.items()):
            m.stub_function(addr, self._recorder(name))
        m.stub_function(PLAY_SONG, self._play_song)
        m.write(PLAYLIST, bytes(range(1, slots + 1)) + b"\0" * (255 - slots))
        m.w16(SONG + SONG_SLOTS, slots)
        m.w8(SONG + SONG_FOCUS, 1)
        m.w8(SONG + SONG_REC, 1)

    def _recorder(self, name):
        def rec(mm):
            self.calls.append((name, mm.arg(0), mm.arg(1), mm.arg(2), mm.arg(3)))
            return 0
        return rec

    def _play_song(self, mm):
        self.calls.append(("play_song", mm.uc.mem_read(SONG + SONG_CURSOR, 1)[0]))
        return 0

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def state(self) -> dict:
        b = self.m.uc.mem_read(self.ram, 16)
        return dict(magic=struct.unpack_from("<I", b)[0], sel=b[4], anchor=b[5], first=b[6], last=b[7],
                    loop=b[8], wrap=b[9], snap=b[10], end=b[11], entered=b[12], preloaded=b[14])

    def set_state(self, sel=0, anchor=0, first=0, last=0, loop=0, end=0, entered=0) -> None:
        self.m.write(self.ram, struct.pack("<I", 0x504F4C53) +
                     bytes([sel, anchor, first, last, loop, 0, 0, end, entered, 0, 0, 0]))

    def playing(self, on: bool = True) -> None:
        self.m.w8(SEQ + 0x2A, int(on))
        self.m.w8(SEQ + 0x1C, int(on))

    def word(self, name: str) -> int:
        return self.m.r32(WORDS[name])

    def site(self, at: int) -> int:
        return bl_target(self.m.image, FLASH_BASE, at)

    def cb(self, name: str, r0: int = 0) -> None:
        self.calls.clear()
        self.m.call(self.word(name), r0)


def itcm_bl_target(m: Machine, at: int) -> int:
    h1, h2 = struct.unpack("<HH", bytes(m.uc.mem_read(at, 4)))
    s = (h1 >> 10) & 1
    j1, j2 = (h2 >> 13) & 1, (h2 >> 11) & 1
    i1, i2 = 1 - (j1 ^ s), 1 - (j2 ^ s)
    off = (s << 24) | (i1 << 23) | (i2 << 22) | ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1)
    if s:
        off -= 1 << 25
    return at + 4 + off


def test_sites(ptf: Path) -> None:
    r = Rig(ptf)
    lo, hi = patch_code(ptf, "songloop")
    inside = lambda a: lo <= (a & ~1) < hi  # noqa: E731
    sites = [SONG_NEXT_SITE, LABELS_SITE, STOP_SITE, TICK_SITE, SPRINTF_SITE, COLORS_SITE, *FRAME_SITES,
             *ICON_SITES, *FONT_SITES, *CHANGE_SITES]
    # a site another patch in a bundle hooks after songloop goes to that patch: its chain
    # then leads here, so only "into patch code" is checked
    check(all(r.site(a) >= 0x601BAB78 for a in sites), f"{len(sites)} BL sites lead into patch code")
    check(all(r.m.r32(a) & 1 and (r.m.r32(a) & ~1) >= 0x601BAB78 for a in WORDS.values()),
          f"{len(WORDS)} callback words lead into patch code")
    s = Machine(STOCK)
    check(r.m.r32(PERFORM_PLAY_WORD) == s.r32(PERFORM_PLAY_WORD), "Performance Play as stock")
    check(all(r.m.uc.mem_read(a, 2) == s.uc.mem_read(a, 2) for a in (0x600FDD92, 0x600FEDB8)),
          "the Song update and labels read the perform flag as stock")
    stubs = [itcm_bl_target(r.m, a) for a in PEEK_SITES]
    check(all(0x1FB40 <= s < 0x20000 for s in stubs), f"ITCM preload sites go to stubs {[hex(s) for s in stubs]}")


def test_select(ptf: Path) -> None:
    r = Rig(ptf)
    r.m.write(r.ram, b"\xAA" * 16)                 # garbage: initializes itself
    r.m.w8(SONG + SONG_CURSOR, 4)
    r.held = {SHIFT}
    for _ in range(3):
        r.cb("down")
    st = r.state()
    check(st["magic"] == 0x504F4C53 and (st["sel"], st["first"], st["last"], st["end"]) == (1, 4, 7, 7),
          f"Shift + Down x3 from 5: block {st['first'] + 1}-{st['last'] + 1}")
    r.cb("up")
    st = r.state()
    check((st["first"], st["last"]) == (4, 6) and r.m.uc.mem_read(SONG + SONG_CURSOR, 1)[0] == 6,
          "Shift + Up: block 5-7, cursor on the moved edge")
    for _ in range(4):
        r.cb("up")
    st = r.state()
    check((st["first"], st["last"], st["sel"]) == (4, 4, 0), "Shift + Up x4: never above the anchor (the top)")
    for _ in range(2):
        r.cb("down")
    r.held = set()
    r.cb("down")
    check(r.state()["sel"] == 0 and r.names() == ["slot_move"] and r.calls[0][2] == 1,
          "Down without Shift: the selection goes, stock moves the cursor (+1)")
    r.m.w8(SONG + SONG_REC, 0)
    r.held = {SHIFT}
    r.cb("down")
    check(r.state()["sel"] == 0 and "slot_move" in r.names(), "Rec off: Shift + Down doesn't select")
    r.m.w8(SONG + SONG_FOCUS, 0)
    r.held = set()
    r.cb("up")
    check(r.names() == [] or r.names()[0] != "slot_move", "grid focus: Up goes to the stock handler")


def test_play_selection(ptf: Path) -> None:
    for how in ("softkey0", "play"):
        r = Rig(ptf)
        r.set_state(sel=1, anchor=2, first=2, last=4, end=4)
        r.held = {SHIFT} if how == "play" else set()
        r.cb(how)
        st = r.state()
        ps = [c for c in r.calls if c[0] == "play_song"]
        check(ps and ps[0][1] == 2 and st["loop"] == 1 and st["entered"] == 1 and
              r.m.uc.mem_read(SONG + SONG_CURSOR, 1)[0] == 4,
              f"{'Shift + Play' if how == 'play' else 'softkey 0'}, stopped: plays from position 3, loop on")
    r = Rig(ptf)
    r.cb("softkey0")
    check(r.names() == ["softkey0"], "softkey 0 without a selection: stock")
    r = Rig(ptf)
    r.m.stub_function(0x600C863C, r._recorder("stock_play"))
    r.set_state(sel=1, anchor=2, first=2, last=4, end=4)
    r.cb("play")
    check(r.names() == ["stock_play"] and r.state()["loop"] == 0, "Play without Shift: stock Play")


def test_preload(ptf: Path, stock: Path) -> None:
    s = Rig(stock)
    s.m.w8(SONG_POS, 3)
    want = s.m.call(SONG_PEEK_NEXT, FM)
    r = Rig(ptf)
    stubs = [itcm_bl_target(r.m, a) for a in PEEK_SITES]
    r.m.w8(SONG_POS, 3)
    got = [r.m.call(st_, FM) for st_ in stubs]
    check(got == [want, want] == [5, 5], f"no loop: preload {got} as stock ({want})")
    r.set_state(sel=1, first=1, last=3, loop=1, entered=1)
    got = r.m.call(stubs[0], FM)
    check(got == 2 and r.state()["wrap"] == 1, f"loop 2-4 at its end: preload pattern {got}")
    r.m.call(r.site(SONG_NEXT_SITE), FM)
    check(r.m.uc.mem_read(SONG_POS, 1)[0] == 1 and r.state()["wrap"] == 0, "pattern end: to position 2")
    r.set_state(sel=1, first=5, last=7, loop=1, entered=0)
    r.m.w8(SONG_POS, 2)
    got = r.m.call(stubs[1], FM)
    check(got == 4 and r.state()["wrap"] == 0, f"armed, block not reached: on as stock ({got})")
    r.set_state(sel=1, first=5, last=7, loop=1, entered=1)
    got = r.m.call(stubs[1], FM)
    check(got == 6 and r.state()["wrap"] == 1, "outside the block after reaching it: back to its start")


def test_continue(ptf: Path) -> None:
    r = Rig(ptf)
    r.playing()
    r.set_state(sel=1, first=2, last=4, loop=1, entered=1, end=4)
    r.m.w8(SONG_POS, 3)
    r.cb("softkey0")
    st = r.state()
    check(st["loop"] == 0 and st["sel"] == 0 and r.m.uc.mem_read(SONG + SONG_CURSOR, 1)[0] == 3,
          "Continue Song: loop and selection off, the cursor on the position playing")
    r = Rig(ptf)
    r.playing()
    r.set_state(sel=1, first=2, last=4, end=4)
    r.m.w8(SONG_POS, 7)
    r.cb("softkey0")
    stubs = [itcm_bl_target(r.m, a) for a in PEEK_SITES]
    got = r.m.call(stubs[0], FM)
    r.m.call(r.site(SONG_NEXT_SITE), FM)
    check(r.state()["entered"] == 1 and got == 3 and r.m.uc.mem_read(SONG_POS, 1)[0] == 2,
          "Play Selection with the block behind the play position: |: :| at once, to its start at the pattern end")
    r.set_state(sel=1, first=5, last=7, end=7)
    r.m.w8(SONG_POS, 1)
    r.cb("softkey0")
    st = r.state()
    check(st["loop"] == 1 and st["entered"] == 0 and "play_song" not in r.names(),
          "Play Selection while playing: armed, the song plays on")


def test_stop(ptf: Path) -> None:
    r = Rig(ptf)
    r.set_state(sel=1, first=2, last=4, loop=1, entered=1)
    r.m.stub_function(0x600CFBC8, lambda mm: 0)    # the status bar reset, seq_stop's first call
    r.m.stub_function(0x600AB8F4, lambda mm: 0)    # midi_send_stop
    hit = []
    r.m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: hit.append(a), begin=STOP_SITE + 4, end=STOP_SITE + 4)
    try:
        r.m.call(SEQ_STOP, SEQ, max_insns=200_000)
    except RuntimeError:
        pass                                       # the rest of seq_stop needs the MIDI ports
    check(r.state()["loop"] == 0 and hit, "seq_stop ends the loop, then goes on")


def test_locks(ptf: Path) -> None:
    for name, stock in (("undo", "undo"), ("add", "add"), ("delete", "delete"), ("delete_key", "delete_key")):
        r = Rig(ptf)
        r.set_state(sel=1, first=2, last=4)
        r.cb(name)
        locked = stock not in r.names()
        r.set_state()
        r.cb(name)
        check(locked and stock in r.names(), f"{name}: locked with a block, stock without")
    for at in CHANGE_SITES:
        r = Rig(ptf)
        r.set_state(loop=1)
        r.calls.clear()
        r.m.call(r.site(at), SONG, 1)
        locked = "change" not in r.names()
        r.set_state()
        r.m.call(r.site(at), SONG, 1)
        check(locked and r.names() == ["change"], f"<< Pattern >> {at:#x}: locked during the loop, stock without")
    r = Rig(ptf)
    r.set_state(sel=1, first=2, last=4)
    r.held = {SHIFT}
    r.cb("jog", 1)
    locked = "jog" not in r.names()
    r.set_state()
    r.cb("jog", 1)
    check(locked and r.names() == ["jog"], "Shift + jog: locked with a block, stock without")
    r = Rig(ptf)
    r.set_state(sel=1, anchor=2, first=2, last=4, loop=1, end=4)
    r.m.w8(SONG + SONG_CURSOR, 4)
    moves = []
    for name, d in (("down", 0), ("up", 0), ("jog", 9), ("down", 0), ("jog", (-20) & 0xFFFFFFFF)):
        r.cb(name, d)
        st = r.state()
        moves.append((st["first"] + 1, st["last"] + 1, r.m.uc.mem_read(SONG + SONG_CURSOR, 1)[0] + 1))
    check("slot_move" not in r.names() and moves == [(4, 6, 6), (3, 5, 5), (8, 10, 10), (8, 10, 10), (1, 3, 3)],
          f"during a loop, Up/Down/jog move the whole block within the song: {moves}")
    r.set_state()
    r.cb("jog", 2)
    check([c[:3] for c in r.calls] == [("slot_move", SONG, 2)], "jog without a loop: stock cursor move")


def test_labels(ptf: Path) -> None:
    hook = Rig(ptf).site(LABELS_SITE)
    r = Rig(ptf)
    r.m.w8(SONG + SONG_LABEL, 0)
    r.set_state(sel=1, first=2, last=4)
    r.m.call(hook, SONG)
    texts = [r.m.cstr(c[3]) for c in r.calls if c[0] in ("text", "text2")]
    check(texts == ["Play", "Selection", "Play", "Song"], f"stopped with a block: {' / '.join(texts)}")
    r.calls.clear()
    r.playing()
    r.set_state(sel=1, first=2, last=4, loop=1, entered=1)
    r.m.call(hook, SONG)
    texts = [r.m.cstr(c[3]) for c in r.calls if c[0] in ("text", "text2")]
    check(texts == ["Continue", "Song", "Stop", ""], f"looping: {' / '.join(texts)}")
    for perform, want in ((0, ["Loop", "Pattern"]), (1, ["Loop", "Performance"])):
        r = Rig(ptf)
        r.playing()
        r.m.w8(SEQ + 0x1D, perform)
        r.m.w8(SONG + SONG_LABEL, 0)
        r.calls.clear()
        r.m.call(hook, SONG)
        texts = [r.m.cstr(c[3]) for c in r.calls if c[0] in ("text", "text2")][:2]
        check(texts == want, f"no selection, playing, Performance {perform}: stock labels {' / '.join(texts)}")


def test_playhead(ptf: Path) -> None:
    hook = Rig(ptf).site(LABELS_SITE)
    for perform, rec, want in ((1, 1, (1, 5, 5)), (0, 1, (0, 0xFF, 0xFFFF)), (1, 0, (0, 0xFF, 0xFFFF))):
        r = Rig(ptf)
        r.playing()
        r.m.w8(SEQ + 0x1D, perform)
        r.m.w8(SONG + SONG_REC, rec)
        r.m.w16(SONG + 0x122, 0xFFFF)              # as the Song screen leaves it when opened
        r.m.w8(SONG + 0x18, 0)
        r.m.w8(SONG + 0x19, 0xFF)
        r.m.w8(SONG + SONG_CURSOR, 2)
        r.m.w8(SONG_POS, 5)
        r.m.call(hook, SONG)
        got = (r.m.uc.mem_read(SONG + 0x18, 1)[0], r.m.uc.mem_read(SONG + 0x19, 1)[0],
               struct.unpack("<H", bytes(r.m.uc.mem_read(SONG + 0x122, 2)))[0])
        check(got == want, f"Performance {perform}, Rec {rec}: play icon (on, position, shown) {got}"
                           + (" (the Song update does it itself)" if want[0] == 0 else ""))


def test_hold(ptf: Path) -> None:
    for loop, perform, want in ((1, 1, 0x600ABF9D), (0, 1, 0x7091), (1, 0, 0x7091)):
        r = Rig(ptf)
        r.playing()
        r.set_state(sel=1, first=2, last=4, loop=loop, entered=1)
        r.m.w8(SEQ + 0x1D, perform)
        r.m.w32(SEQ + 0x6E8, 0x7091)
        r.m.w8(SONG_POS, 3)
        r.m.call(r.site(SONG_NEXT_SITE), FM)
        got = r.m.r32(SEQ + 0x6E8)
        check(got == want, f"pattern end, loop {loop}, Performance {perform}: scheduled action {got:#x}")
    r = Rig(ptf)
    r.playing()
    r.set_state(sel=1, first=2, last=4, loop=1, entered=1)
    r.m.w8(SEQ + 0x1D, 1)
    r.m.w32(SEQ + 0x6E8, 0x600ABF9D)
    r.m.w8(0x20022FA5, 0)
    r.cb("softkey0")
    check(r.state()["loop"] == 0 and r.m.r32(SEQ + 0x6E8) == 0x7091 and r.m.uc.mem_read(0x20022FA5, 1)[0] == 1,
          "Continue Song with Performance: stock Continue Song schedules Performance off")


def test_status(ptf: Path) -> None:
    r = Rig(ptf)
    seen = []
    r.m.stub_function(SPRINTF, lambda mm: seen.append(mm.cstr(mm.arg(4))) or 0)   # the icon argument
    play = r.m.alloc(4)
    r.m.write(play, b"\x1a\x1b\0")

    def icon() -> str:
        r.m.call(r.site(SPRINTF_SITE), 0, 0, 0, 0, play, 0, 0, 0)
        return seen[-1]
    plain = icon()
    r.playing()
    r.set_state(sel=1, first=5, last=7, loop=1, entered=0)
    armed = icon()
    r.set_state(sel=1, first=5, last=7, loop=1, entered=1)
    looping = icon()
    check((plain, armed, looping) == ("\x1a\x1b", "{}", "`^"),
          f"status icon: {plain!r} / armed {armed!r} / looping {looping!r}")


def test_fonts(ptf: Path) -> None:
    r = Rig(ptf)
    m = r.m
    uploads = []

    def upload(mm):
        uploads.append((mm.arg(3), bytes(mm.uc.mem_read(mm.arg(1), mm.arg(2)))))
    hal, vt = m.alloc(16), m.alloc(0x40)
    m.w32(hal, vt)
    m.w32(vt + 0x30, m.add_stub(upload))
    disp = m.alloc(16)
    m.w32(disp, hal)
    widths = bytes([0] * 0x18 + [16] * (128 - 0x18))
    widths = widths[:0x1A] + bytes([21, 22]) + widths[0x1C:]
    m.write(FONT4_ENTRY, widths + struct.pack("<IIII", 2, 10, 20, 24))
    m.w32(FONT4_ENTRY + 0x9C, 0x10000)
    m.w32(FONT4_ENTRY + 0xA0, 0x11000)
    for at in FONT_SITES:
        uploads.clear()
        m.call(r.site(at), disp, 0, 5)
        addrs = sorted(a for a, _ in uploads)
        w = bytes(m.uc.mem_read(FONT4_ENTRY, 128))
        glyph_len = 10 * 24
        check(0x1F000 in addrs and 0x1F100 in addrs and 0x10000 + 0x5C in addrs and
              0x11000 + (0x60 - 0x18) * glyph_len in addrs and w[0x60] == 21 and w[0x5E] == 22 and
              w[0x7B] == 21 and w[0x7D] == 22, f"font load {at:#x}: glyphs, widths and list icons uploaded")
    icon = dict(uploads)[0x1F000]
    check(len(icon) == 200 and any(icon), "the list icon has pixels")


def test_frame(ptf: Path) -> None:
    r = Rig(ptf)
    m = r.m
    ctrl = m.alloc(0x60)
    m.w16(ctrl + 0x1E, 29)                         # list top 29 + 0x3e = 91
    m.w16(ctrl + 0x32, 2)                          # first visible slot 2
    m.w16(SONG + 0xC, 12)
    r.set_state(sel=1, first=5, last=8)
    m.w16(ctrl + 0x56, 5 - 2)
    y0 = 91 + 3 * 27
    for reg, v in ((UC_ARM_REG_R4, ctrl), (UC_ARM_REG_R7, y0), (UC_ARM_REG_R8, 145), (UC_ARM_REG_R10, 100),
                   (UC_ARM_REG_R5, 8)):
        m.uc.reg_write(reg, v)
    m.call(r.site(FRAME_SITES[0]), 0x18)
    y0n, y1n = m.uc.reg_read(UC_ARM_REG_R7), m.uc.reg_read(UC_ARM_REG_R9)
    lw = [c for c in r.calls if c[0] == "line_width"]
    check((y0n, y1n) == (91 + 3 * 27, 91 + 6 * 27) and lw and lw[0][1] == 0x18,
          f"still frame over the block 6-9: y {y0n}..{y1n + 27}, then eve_line_width")


def test_perform_preload(ptf: Path) -> None:
    pat = 0x20000200

    def rig(pmode=1, perform=1, queued=0, pos=3):
        r = Rig(ptf)
        r.playing()
        r.m.w8(SEQ + 0x1D, perform)
        r.m.w8(SEQ + 0xB9, pmode)
        r.m.w8(SEQ + 0x28, queued)
        r.m.w32(SEQ + 0x14, pat)
        r.m.w8(pat + 0x1C, 63)
        r.m.w8(SONG_POS, pos)
        r.set_state()
        return r

    def tick(r, row):
        r.m.w16(SEQ + 0x68 * 16 + 0x20, row)
        r.calls.clear()
        r.m.call(r.site(TICK_SITE), 0x20020880)
        ok = [c[:2] for c in r.calls if c[0] == "popup"] == [("popup", 0x20020880)]
        return ok, [c[1:3] for c in r.calls if c[0] == "fm_load"]
    r = rig()
    res = [tick(r, row) for row in (10, 61, 62, 63, 0, 62)]
    check(all(ok for ok, _ in res) and [len(l) for _, l in res] == [0, 0, 1, 0, 0, 1] and res[2][1] == [(FM, 5)],
          f"track 1 Bwd: requests per row {[len(l) for _, l in res]}")
    for what, kw in (("Fwd", dict(pmode=0)), ("no Performance", dict(perform=0)), ("queued", dict(queued=1))):
        ok, loads = tick(rig(**kw), 62)
        check(ok and loads == [], f"{what}: no own request")
    r = rig(pos=4)
    r.set_state(sel=1, first=2, last=4, loop=1, entered=1)
    ok, loads = tick(r, 63)
    check(ok and loads == [(FM, 3)], "loop 3-5 at its end: requests the block's first pattern")


STOCK = Path()


def main() -> int:
    global STOCK
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    ptf, STOCK = Path(sys.argv[1]), Path(sys.argv[2])
    for name, fn in (("sites", test_sites), ("select", test_select), ("play selection", test_play_selection),
                     ("continue", test_continue), ("stop", test_stop), ("locks", test_locks),
                     ("labels", test_labels), ("status", test_status), ("fonts", test_fonts),
                     ("frame", test_frame), ("playhead", test_playhead), ("hold", test_hold),
                     ("perform preload", test_perform_preload)):
        print(name)
        fn(ptf)
    print("preload")
    test_preload(ptf, STOCK)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
