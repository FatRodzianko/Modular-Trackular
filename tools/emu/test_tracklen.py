"""Check the tracklen patch (Variable Track Length: a length per track): playback, the Trk Len button, the grid,
live recording and the lengths' file.

  python tools/emu/test_tracklen.py <patched.ptf> <stock TrackerPlus_1.2.0.ptf>

Works with the tracklen-only build and with bundles that include it. The sequencer runs
tick by tick (seq_tick), MIDI instruments on the tracks, with fake MIDI ports that
record each note and the tick it went out. Track lengths are set by writing the patch's
table (its RAM, found through the .map.json): magic, then 256 patterns x 16 tracks.

The oracle: a track of length E in a pattern of length P must play exactly what stock
firmware plays for the same track with its first E steps written out to the end of the
pattern (step s holding step s mod E: 0-5, 0-5, 0-3 for E 6 in 16). The short track's own
steps E..P-1 hold other notes, which must never sound. That covers whatever a step
carries (Roll, Micro-move, Break ...) and every play mode the sequencer has.

1. Sites: memcpy_small at ITCM 0x2170 and seq_distance_to_next_note_q at 0x33a2 and
   0x3520 go through ITCM stubs to the patch's hooks.
2. No lengths (also: table not set up, a length 0, a length at or above the pattern's,
   and a length the pattern has been shortened below): the same MIDI events at the same
   ticks as stock firmware, with FX on every track.
3. Lengths 1, 5, 6 and 15 in a 16-step pattern; all 16 tracks with lengths 1..16 at once;
   a 128-step pattern with lengths 48 and 100: as the oracle over several passes. The
   tracks' positions (+0x88) still count the whole pattern, so the pattern ends after P
   steps whichever tracks are short, track 16 included.
4. What a step carries, on a short track, as the oracle: Roll at several values with
   empty steps after it (its look-ahead), Micro-move on the track's last step (the
   delayed note crosses its loop), Break (the first one ends the pattern), and a
   sample-track instrument (its voice note-on) and a synth (its note-on).
5. Play modes, as the oracle: Play from a later step, a looping selection, performance
   mode backward.
6. Roll's look-ahead itself: the hooks give, for every step of a short track, what the
   stock function gives on the written-out pattern; on a track without a length they
   give what it gives on the same pattern.
7. Song mode: the next pattern is requested on the last step as in stock firmware, a
   short track in pattern A plays its loop, and pattern B then plays its own lengths.
8. Cost: the instructions run in the patch's code and its ITCM stubs, per track and
   step, with all 16 tracks short (the worst case) and with none.

Rendering runs the same seq_process_track code (ITCM), so it is not checked separately.

The Trk Len button and the encoder (the Pattern screen's page 3), with a fake button
manager, the stock page-3 bind and label code, and the display calls recorded:
9. The bind: Trk Len's button is the first one after humanize's (2) and copypattern's
   (the next free one): 2 alone, 3 with one of them, 4 with both. It is bound as kind 3
   to the patch; buttons 0, 1 and 7 keep their stock handlers; humanize's and
   copypattern's buttons aren't the patch's, and the rest stay unbound.
10. The labels: Trk Len's button reads "Trk Len" with an empty second line, the active
   colors and Pattern Name's data; the other patches' buttons keep their texts, the
   free ones stay empty, Pattern Name, Track Name and More are unchanged (alone: every
   other display call is the same as stock's, in the same order).
11. Held: a press sets up the table (from garbage) and shows the cursor's track's length
   ("--" when it plays the whole pattern); the encoder changes that track's length in
   the playing pattern's row, clamped to 1..the pattern's length (the pattern's length
   is stored as 0), and stock's encoder isn't called. A label redraw while held shows
   the length. A length the pattern was shortened below shows "--" and is kept until
   the encoder changes it. A 128-step pattern shows 3 digits.
12. Not held: after the release, after the bind was cleared (as pages 1 and 2 do) and
   after page 3 is bound again, the encoder goes to stock with the same delta and the
   table doesn't change; also while held, when an overlay is open or stock's own guard
   is set.
12b. Through the firmware's button input (0x600cc610): a press, then the "still held"
   events (state 2) a held button sends, leave Trk Len held; the release ends it.
13. A length set with the button plays: track 1 set to 6 in a 16-step pattern plays
   0-5 0-5 0-3.
14. Without the hold the encoder is stock's: alone, cursor moves and edits (record off
   and on, each column) leave the same RAM as stock firmware, also after a press and
   release of Trk Len.
15. The Pattern grid, drawn by stock code (the row texts, the grid control's constructor
   with the Pattern screen's descriptor, its draw methods), each cell's text pointer and
   color logged at patgrid_draw_cell. The oracle: stock firmware with the short tracks'
   steps E.. left empty. Every cell must get the color stock gives that empty step (the
   column's empty color), and every other cell stock's own color, in the same order, with
   the same display list. Covers: the cursor and a muted track (their colors win), the
   three row settings (rows outside the pattern wrapped round, blank, or none), a pattern of 4 steps
   (rows wrap more than once), 128 steps, the grid scrolled to tracks 13-16, the
   layouts with an overlay (8 rows) or a side panel (3 or 2 tracks), the single-column
   views (8 tracks of one column), a 3-character instrument text (its own call site),
   and a table without lengths, not set up, or set for another pattern (all stock).
16. The encoder held on Trk Len also redraws the grid control.
16b. ... and refills its row texts at once: past the new length the rows show the loop for
   it (steps that come back into the length show their own contents), with no cursor move.
17. Live recording during play (settings+0xce 2, the Pattern screen current, seq+0x2b
   set), pads' note-on through seq_record_live_note's veneers and note-off through the
   hooked calls of seq_record_note_off_q. The oracle: stock firmware with each short
   track written out to the pattern's end (step s holding step s mod E), recording the
   same hits at the same ticks. Every row stock changes on a short track must be
   changed at its step mod E in the patched pattern, the dimmed rows must keep their
   own contents, every other track must match stock row for row, and the tracks'
   state and the MIDI sent must be the same. The dimmed rows hold notes where the real
   steps are empty and the other way round, so stock's choice of track (the first
   with no note sounding) and the note-off's (only onto an empty step) are checked
   against the real steps. Covers: early and late hits
   on dimmed rows, a late hit on E's last step (goes to step 0), the pattern's last
   step, a hit while a note sounds (the next track), note-offs (same step, steps later,
   and onto a step holding a note: kept), length 1, 128 steps, two short
   tracks, a track without a length (as stock), and no lengths at all (the whole
   pattern as stock). Every one of the 8 veneers and 18 note-off sites gives the same
   result as the first.
17b. Recording over several loops, a sample instrument (the one change from stock's
   rules: a short track's note-offs don't stop a note from landing on its step): with
   length 6, three hits in loop 1 fill the 6 steps with notes and note-offs; loop 2's
   hits on note-offs stay on track 1, a hit on a note goes to the next track; a hit
   that goes elsewhere leaves the note-offs; length 2; a track without a length keeps
   stock's rule (as stock firmware). The patch's own writes to the pattern happen with the audio
   interrupt (NVIC bit 0) masked, and stock records with the interrupt as the caller
   left it (the recorders mask and enable it themselves).
18. The lengths' file (tracklen.bin on the fake SD card of tools/emu/fakefs.py), through
   the Pattern and Config screens' update hooks: nothing written while nothing changed;
   a change written once, a moment later (TLN1, the entries, a checksum); every pattern
   with lengths (4,347 bytes) written and read back, every read and write at most 256
   bytes; another project clears them, coming back reads them; a damaged, short or
   wrong file (checksum, magic, count, pattern 0, truncated, empty) gives none, none of
   it used; every length cleared removes the file; nothing read while the file manager
   is busy (the old project's lengths cleared at once), then read and the grid redrawn
   once; a project without a name: nothing written; no pattern file written; Save As
   and new projects as the other patches' files (project_save.save_as_checks). The
   encoder marks a change for the file only when a length changes.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_SP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import machine as mach  # noqa: E402
from fakefs import PROJECT_NAME, FakeFS  # noqa: E402
from project_save import save_as_checks  # noqa: E402
import seq_timing as st  # noqa: E402
from layout import patch_code, patch_function, patch_ram  # noqa: E402

SEQ, PROJECT = st.SEQ, st.PROJECT
CUR_PATTERN = PROJECT + 0x6C8
SITES = {0x2170: "step copy", 0x33A2: "Roll look-ahead (a)", 0x3520: "Roll look-ahead (b)"}
DISTANCE = 0x3958                   # seq_distance_to_next_note_q (stock, ITCM)
MAGIC = 0x314C544D
ROLL, MICRO_MOVE, BREAK, VOLUME = 3, 2, 13, 18
VOICE_NOTE_ON = 0x5C04              # sample voices
NOTE_ON_OUT = 0x600ABB44            # synths (as test_condtrig)
NOTE_OFF_OUT = 0x600ABBE8           # synths (no synth engine in this rig)
ENGINE_CLICK = 0x42E4
STEP = 576                          # ticks per step
NOTE_OUT = 24                       # a MIDI instrument sends the step's note + 24
FIRST_MICRO = 12                    # the tick of a take's first micro-step
PER_TRACK_LIMIT = 50                # instructions the patch may add per track and step

# the Pattern screen (page 3) and the encoder
PATVIEW = 0x20021770
ED = PATVIEW + 0x1000
ED_CURSOR_TRACK, ED_COLUMN, ED_RECORD, ED_OVERLAY = 0x1E4, 0x255, 0x254, 0x398
ED_CURSOR_ROW = 0x1E6
BUTTON_MANAGER_PTR = 0x200209E0
PAGE3_BIND, PAGE3_LABELS = 0x600E29A4, 0x600E584C
BTN_CLEAR = 0x600CF53C                  # btn_clear(manager, first, last)
BTN_INPUT = 0x600CC610                  # the buttons' input callback (manager pointer, button, state): 1 press, 2 still held, 0 release
JOG_WORD, STOCK_JOG = 0x600DF2F0, 0x600C90A0
JOG_GUARD = 0x6010E0F8                  # stock pat_jog does nothing while this is nonzero
HELD_QUERY = 0x600CF90C                 # buttons_is_held_q: no buttons object in this rig
PAD_REFRESH = 0x600E1310                # no pads in this rig
DISPLAY = 0x20027D64
DISPLAY_CALLS = {0x60101D5C: "text", 0x60101D74: "text2", 0x60101DA4: "colors",
                 0x60101DF8: "data", 0x60101E84: "refresh", 0x60102014: "sync"}
STOCK_HANDLERS = {0: 0x600DFE65, 1: 0x600DFDD1, 7: 0x600E2A45}
STYLE_ACTIVE, STYLE_MORE = 0x44, 0x64
NAME_DATA, MORE_DATA = PATVIEW + 0x12CC, PATVIEW + 0x12EC
CTRL0 = 20                              # label control of button 0 in this rig; button b's is 20 + b
TRK_LEN = 2                             # Trk Len's button: set in main() from the build (page3_buttons)
FILL_ROWS = 0x600DF3A0                  # patview_fill_rows_q(patview): the grid's row texts
GRID_CTOR = 0x6010A3D4                  # patgrid_ctor(ctrl, desc)
DRAW_CELL = 0x6010B204                  # patgrid_draw_cell(ctrl, x, y, font, text, n, w)
CELL_SITES = {0x6010B3DE: "note", 0x6010B5AE: "instrument (3 characters)", 0x6010B62C: "instrument",
              0x6010B7BE: "FX1", 0x6010B9C4: "FX2"}
STYLE = 0x6005A4C0                      # *(display + 8): the styles; the grid's colors at +0x2b8
GRID_DATA = PATVIEW + 0x14              # row texts: 0x11d a track, 0x13 a row
ED_HIGHLIGHT, ED_LAYOUT, ED_GRID_CTRL, ED_FIRST_TRACK = 0x1F4, 0x1FF, 0x29B, 0x1EC
MUTES = PROJECT + 0x6E0

REC_VENEERS = [0x600AB698, 0x600C73C8, 0x600CD550, 0x600D4B30, 0x600F1920, 0x600F54E8, 0x600FAD80, 0x6011A0F8]
REC_OFF_SITES = [0x600AB514, 0x600C6AFC, 0x600C6B78, 0x600C6CDE, 0x600C6ECE, 0x600C6F96, 0x600C71EE, 0x600C7348,
                 0x600CD4A6, 0x600D4792, 0x600D491E, 0x600D4ADC, 0x600F0396, 0x600F1904, 0x600F57AA, 0x6011A0A8,
                 0x600F5500, 0x600F9418]
REC_LIVE, REC_OFF = 0x15C8, 0x600AC700  # the stock recorders
SCREEN_MANAGER = 0x200207C0             # +8: the current screen
NVIC_ISER, NVIC_ICER = 0xE000E100, 0xE000E180
PADS = 0x14                             # the recorders' source: the pads

# the lengths' file
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
CONFIG_UPDATE_SLOT, CONFIG_UPDATE = 0x6003B5F8, 0x600C6474
CTRL_REFRESH = 0x60101E84
FM_STATE = 0x2001E488 + 0x13D
SAVE_DELAY = 25
TL_CHANGED = 0x100F                     # the patch's RAM: a length changed since the file
TL_SIZE = 0x1018

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


class Rig:
    """The sequencer with MIDI instrument t + 1 (channel t + 1) on track t."""

    def __init__(self, ptf: Path, last: int, table: int | None = None, pattern: int = 1):
        self.m = m = st.make(ptf)
        m.stub_function(ENGINE_CLICK, lambda mm: 0)
        m.w8(st.SETTINGS + 0x2C, 2)             # MIDI out: jack
        self.last = last
        self.cur, self.nxt = st.pattern(m, last), st.pattern(m, last)
        m.w32(SEQ + 0x14, self.cur)
        m.w32(SEQ + 0x18, self.nxt)
        m.w8(CUR_PATTERN, pattern)
        self.table = table
        if table is not None:
            m.write(table, struct.pack("<I", MAGIC) + bytes(256 * 16))
        self.tick0 = 0

    def length(self, track: int, n: int, pattern: int = 1) -> None:
        self.m.w8(self.table + 4 + 16 * pattern + track, n)

    def put(self, track: int, step: int, note: int, fx1=(0, 0), fx2=(0, 0), instr: int | None = None,
            bufs=None) -> None:
        for buf in bufs or (self.cur, self.nxt):
            st.set_step(self.m, buf, track, step, note, 48 + track if instr is None else instr, fx1, fx2)

    def start(self, step: int = 0) -> None:
        self.m.call(st.SEQ_PLAY_FROM, SEQ, step)
        self.tick0 = self.m.ticks
        self.m.events.clear()

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.m.ticks += 1
            self.m.call(st.SEQ_TICK, SEQ, max_insns=2_000_000)

    def events(self) -> list[tuple[int, str]]:
        return [(t - self.tick0, w) for t, w in self.m.events]

    def notes(self, channel: int) -> list[int]:
        """The step notes sent on a channel, in order."""
        out = []
        for _, what in self.m.events:
            w = what.split()
            if w[1:3] == ["note", "on"] and int(w[5][2:]) == channel:
                out.append(int(w[3]) - NOTE_OUT)
        return out


def pair(ptf: Path, stock: Path, last: int, tracks: dict[int, tuple[int, list]], others=(),
         setup=None, start: int = 0, ticks: int | None = None, voice: bool = False,
         synth: bool = False):
    """The patched rig with track lengths, and stock with those tracks written out.

    tracks: {track: (E, steps)} where steps[s] = (note, fx1, fx2, instr) for s < E. The
    patched rig gets notes 100 + s on the short tracks' steps E..last (never heard).
    others: (track, step, note, fx1, fx2) put in both. setup(rig) runs after the pattern
    is written; ticks defaults to 3 passes. Returns the two event lists."""
    out = []
    for path, patched in ((ptf, True), (stock, False)):
        rig = Rig(path, last, patch_ram(ptf, "tracklen") if patched else None)
        for t, (e, steps) in tracks.items():
            for s in range(last + 1):
                if patched and s >= e:
                    rig.put(t, s, 100 + s % 20)
                elif steps[s % e] is not None:
                    note, fx1, fx2, instr = steps[s % e]
                    rig.put(t, s, note, fx1, fx2, instr)
            if patched:
                rig.length(t, e)
        for t, s, note, fx1, fx2 in others:
            rig.put(t, s, note, fx1, fx2)
        voices: list = []
        if voice:
            rig.m.stub_function(VOICE_NOTE_ON, lambda mm, v=voices: v.append((mm.ticks, mm.arg(1), mm.arg(2))) or 0)
        if synth:
            rig.m.stub_function(NOTE_ON_OUT, lambda mm, v=voices: v.append((mm.ticks, mm.arg(1), mm.arg(2))) or 0)
            rig.m.stub_function(NOTE_OFF_OUT, lambda mm: 0)
        if setup:
            setup(rig)
        rig.start(start)
        rig.run(ticks if ticks is not None else 3 * (last + 1) * STEP + FIRST_MICRO)
        out.append([(t - rig.tick0, w) for t, w in rig.m.events] + [(t - rig.tick0, f"voice {a} {b}") for t, a, b in voices])
    return out


def notes_steps(e: int, base: int = 40, fx=None) -> list:
    return [(base + s, *(fx(s) if fx else ((0, 0), (0, 0))), None) for s in range(e)]


class UiRig(Rig):
    """Rig plus the Pattern screen's page 3: a fake button manager, label controls
    20..27, the display calls recorded, stock's encoder recorded (stock_jog=True) or run."""

    def __init__(self, ptf: Path, last: int = 15, table: int | None = None, pattern: int = 1,
                 stock_jog: bool = True):
        super().__init__(ptf, last, table, pattern)
        m = self.m
        self.mgr, self.entries = m.alloc(0x40), m.alloc(8 * 0x14)
        m.w8(self.mgr + 5, 8)
        m.w32(self.mgr + 0x1C, self.entries)
        m.w32(BUTTON_MANAGER_PTR, self.mgr)
        for b in range(8):
            m.w8(ED + 0x292 + b, CTRL0 + b)
        self.guard = 0
        m.stub_function(JOG_GUARD, lambda mm: self.guard)
        m.stub_function(HELD_QUERY, lambda mm: 0)
        m.stub_function(PAD_REFRESH, lambda mm: 0)
        self.calls: list[tuple[str, int, int]] = []
        for addr, name in DISPLAY_CALLS.items():
            m.stub_function(addr, lambda mm, n=name: self.calls.append((n, mm.arg(1), mm.arg(2))))
        self.jogs: list[int] = []
        if stock_jog:
            m.stub_function(STOCK_JOG, lambda mm: self.jogs.append(struct.unpack("<i", struct.pack("<I", mm.arg(0)))[0]))

    def r8(self, a: int) -> int:
        return self.m.uc.mem_read(a, 1)[0]

    def entry(self, b: int) -> tuple[int, int, int]:
        """kind, kind 1's handler, kind 3's handler"""
        e = self.entries + 0x14 * b
        return self.r8(e), self.m.r32(e + 4), self.m.r32(e + 8)

    def bind(self) -> None:
        self.m.call(PAGE3_BIND, 0)

    def labels(self) -> list[tuple[str, int, int]]:
        self.calls.clear()
        self.m.call(PAGE3_LABELS, PATVIEW)
        return list(self.calls)

    def button(self, state: int) -> None:
        self.calls.clear()
        self.m.call(self.entry(TRK_LEN)[2], state)

    def jog(self, delta: int) -> None:
        self.calls.clear()
        self.jogs.clear()
        self.m.call(self.m.r32(JOG_WORD), delta)

    def text2(self) -> str | None:
        """The last second-line text set on Trk Len's control"""
        texts = [self.m.cstr(a) for n, c, a in self.calls if n == "text2" and c == CTRL0 + TRK_LEN]
        return texts[-1] if texts else None

    def stored(self, track: int, pattern: int = 1) -> int:
        return self.r8(self.table + 4 + 16 * pattern + track)


def page3_others(ptf: Path) -> list[str]:
    out = []
    for name in ("humanize", "copypattern"):
        try:
            patch_code(ptf, name)
            out.append(name)
        except SystemExit:
            pass
    return out


def page3_buttons(ptf: Path) -> tuple[dict[int, str], int]:
    """The other patches' page-3 buttons ({button: label}) and Trk Len's: humanize takes
    button 2, copypattern the first free one, Trk Len the next."""
    used: dict[int, str] = {}
    for name, label in (("humanize", "Humanize"), ("copypattern", "Copy to")):
        if name in page3_others(ptf):
            used[2 + len(used)] = label
    return used, 2 + len(used)


def button_checks(ptf: Path, stock: Path) -> None:
    code = patch_code(ptf, "tracklen")
    others = page3_others(ptf)
    used, ours = page3_buttons(ptf)
    free = [b for b in range(2, 7) if b != ours and b not in used]
    print(f"Trk Len: the bind and the labels (Pattern screen, page 3): button {ours} expected"
          + (f" (with {', '.join(others)})" if others else ""))
    r = UiRig(ptf, table=patch_ram(ptf, "tracklen"))
    for rec in (0, 1):
        r.m.w8(ED + ED_RECORD, rec)
        r.bind()
        mode = "record mode" if rec else "outside record mode"
        kind, _, handler = r.entry(ours)
        check(kind == 3 and code[0] <= handler < code[1] and handler & 1,
              f"{mode}: button {ours} bound as kind 3 to the patch ({kind}, {handler:#x})")
        check(all(r.entry(b)[:2] == (1, h) for b, h in STOCK_HANDLERS.items()),
              f"{mode}: buttons 0, 1 and 7 keep their stock handlers")
        check(all(not (code[0] <= x < code[1]) for b in used for x in r.entry(b)[1:]),
              f"{mode}: the other patches' buttons {sorted(used)} aren't the patch's")
        check(all(r.entry(b)[0] == 0 for b in free), f"{mode}: buttons {free} stay unbound")

    s = UiRig(stock)
    for rec in (0, 1):
        mode = "record mode" if rec else "outside record mode"
        r.m.w8(ED + ED_RECORD, rec)
        s.m.w8(ED + ED_RECORD, rec)
        calls = r.labels()
        ctrl = CTRL0 + ours
        styles = r.m.r32(DISPLAY + 8)
        text = {c: r.m.cstr(a) for n, c, a in calls if n == "text"}       # the last call per control
        text2 = {c: r.m.cstr(a) for n, c, a in calls if n == "text2"}
        colors = [a for n, c, a in calls if n == "colors" and c == ctrl]
        data = [a for n, c, a in calls if n == "data" and c == ctrl]
        check(text.get(ctrl) == "Trk Len" and text2.get(ctrl) == "--",
              f"{mode}: button {ours} reads {text.get(ctrl)!r} / {text2.get(ctrl)!r}")
        check(colors == [styles + STYLE_ACTIVE] and data == [NAME_DATA],
              f"{mode}: button {ours} gets the active colors and Pattern Name's data")
        check(all(text.get(CTRL0 + b) == label for b, label in used.items())
              and all(text.get(CTRL0 + b) == "" and text2.get(CTRL0 + b) == "" for b in free),
              f"{mode}: the other patches' labels kept ({used}), buttons {free} empty")
        more = CTRL0 + 7
        check(text.get(CTRL0) == "Pattern" and text.get(CTRL0 + 1) == "Track" and text.get(more) == "More"
              and [a for n, c, a in calls if n == "colors" and c == more] == [styles + STYLE_MORE]
              and [a for n, c, a in calls if n == "data" and c == more] == [MORE_DATA],
              f"{mode}: Pattern Name, Track Name and More unchanged")
        if not others:
            base = [(n, c, s.m.cstr(a) if n.startswith("text") else a) for n, c, a in s.labels()]
            got = [(n, c, r.m.cstr(a) if n.startswith("text") else a) for n, c, a in calls if c != ctrl]
            check(got == [x for x in base if x[1] != ctrl],
                  f"{mode}: every other display call as stock's ({len(got)} calls, same order)")


def hold_checks(ptf: Path) -> None:
    print("Trk Len held: the encoder sets the cursor's track's length")
    table = patch_ram(ptf, "tracklen")
    r = UiRig(ptf, table=None)
    r.table = table
    m = r.m
    m.write(table, bytes([0xA5]) * TL_SIZE)
    r.bind()
    r.button(1)
    check(m.r32(table) == MAGIC and m.uc.mem_read(table + 4, 256 * 16) == bytes(256 * 16)
          and r.r8(table + TL_CHANGED) == 0,
          "the first press sets up the table from garbage (magic, all lengths 0, nothing to save)")
    check(r.text2() == "--" and any(n == "refresh" and c == CTRL0 + TRK_LEN for n, c, _ in r.calls),
          f"press: the second line reads {r.text2()!r} and is redrawn")
    steps = [(-1, 15, "15"), (-20, 1, "1"), (1, 2, "2"), (100, 0, "--"), (-10, 6, "6"), (0, 6, "6")]
    for delta, want, label in steps:
        r.jog(delta)
        check(r.stored(0) == want and r.text2() == label and not r.jogs,
              f"encoder {delta:+d}: track 1 length {r.stored(0)} ({r.text2()!r}), stock's encoder not called")
    check(r.r8(table + TL_CHANGED) == 1, "a length changed: marked for the file")
    m.w8(table + TL_CHANGED, 0)
    for delta in (0, -10):                      # 6 stays 6; 1 - 10 clamps to 1
        if delta < 0:
            r.jog(-5)
            m.w8(table + TL_CHANGED, 0)
        r.jog(delta)
        check(r.stored(0) == (6 if delta == 0 else 1) and r.r8(table + TL_CHANGED) == 0,
              f"encoder {delta:+d} that leaves the length as it was ({r.stored(0)}): nothing marked")
    r.jog(5)
    m.w8(ED + ED_CURSOR_TRACK, 3)
    m.w8(CUR_PATTERN, 5)
    r.jog(-3)
    check(r.stored(3, 5) == 13 and r.stored(0, 1) == 6 and r.text2() == "13",
          f"cursor on track 4, pattern 5: track 4's length {r.stored(3, 5)} in pattern 5's row; pattern 1's untouched")
    calls = r.labels()
    t2 = [m.cstr(a) for n, c, a in calls if n == "text2" and c == CTRL0 + TRK_LEN]
    check(t2[-1:] == ["13"], f"a label redraw while held shows the length (second lines set: {t2}, the last wins)")

    print("Trk Len released, unbound or blocked: the encoder is stock's")
    before = bytes(m.uc.mem_read(table, 4 + 256 * 16))
    r.button(0)
    check(r.text2() == "13", f"release: the second line still shows the length ({r.text2()!r})")
    for delta in (1, -2):
        r.jog(delta)
        check(r.jogs == [delta] and bytes(m.uc.mem_read(table, 4 + 256 * 16)) == before,
              f"released, encoder {delta:+d}: stock's encoder gets {r.jogs}, the table is unchanged")
    for what, setup, undo in (
            ("the bind cleared (pages 1 and 2)", lambda: m.call(BTN_CLEAR, r.mgr, 0, 6), None),
            ("page 3 bound again", r.bind, None),
            ("an overlay open", lambda: m.w8(ED + ED_OVERLAY, 1), lambda: m.w8(ED + ED_OVERLAY, 0)),
            ("stock's guard set", lambda: setattr(r, "guard", 1), lambda: setattr(r, "guard", 0))):
        r.bind()
        r.button(1)
        setup()
        r.jog(2)
        check(r.jogs == [2] and bytes(m.uc.mem_read(table, 4 + 256 * 16)) == before,
              f"held, then {what}: stock's encoder gets {r.jogs}, the table is unchanged")
        if undo:
            undo()
            r.jog(-1)
            check(not r.jogs and r.stored(3, 5) == 12, f"... and once that's over, the encoder is Trk Len's again")
            m.w8(table + 4 + 16 * 5 + 3, 13)
        r.bind()

    print("Trk Len and the pattern's length")
    m.w8(CUR_PATTERN, 1)
    m.w8(ED + ED_CURSOR_TRACK, 0)
    m.w8(table + 4 + 16 + 0, 10)
    for buf in (r.cur, r.nxt):
        m.w8(buf + 0x1C, 7)
    r.bind()
    r.button(1)
    check(r.text2() == "--" and r.stored(0) == 10, f"length 10 in an 8-step pattern: {r.text2()!r}, kept ({r.stored(0)})")
    r.jog(-1)
    check(r.stored(0) == 7 and r.text2() == "7", f"encoder -1: {r.stored(0)} ({r.text2()!r})")
    for buf in (r.cur, r.nxt):
        m.w8(buf + 0x1C, 127)
    m.w8(table + 4 + 16 + 0, 0)
    r.button(1)
    for delta, want, label in ((-1, 127, "127"), (-27, 100, "100"), (-1, 99, "99"), (200, 0, "--")):
        r.jog(delta)
        check(r.stored(0) == want and r.text2() == label, f"128 steps, encoder {delta:+d}: {r.stored(0)} ({r.text2()!r})")
    r.button(0)

    print("Trk Len through the firmware's button input (press, still held, release)")
    r = UiRig(ptf, table=None)
    r.table = table
    m = r.m
    skip = m.alloc(0x40)                        # the manager's per-button "skip" bytes, all 0
    m.w32(r.mgr + 0xC, skip)
    m.w8(table + 4 + 16 + 0, 0)
    m.w8(CUR_PATTERN, 1)
    m.w8(ED + ED_CURSOR_TRACK, 0)
    for buf in (r.cur, r.nxt):
        m.w8(buf + 0x1C, 15)
    r.bind()
    for state in (1, 2, 2):                     # a press, then "still held" events
        m.call(BTN_INPUT, BUTTON_MANAGER_PTR, TRK_LEN, state)
    r.jog(-4)
    check(r.stored(0) == 12 and not r.jogs,
          f"press, then 'still held' (state 2) twice, encoder -4: length {r.stored(0)}, stock's encoder not called")
    m.call(BTN_INPUT, BUTTON_MANAGER_PTR, TRK_LEN, 0)
    r.jog(1)
    check(r.jogs == [1] and r.stored(0) == 12, f"release: stock's encoder gets {r.jogs}, the length stays 12")

    print("Trk Len's second line follows the cursor's track (the Pattern screen's update)")
    m.stub_function(PATTERN_UPDATE, lambda mm: 0)
    update = m.r32(PATTERN_UPDATE_SLOT)
    shown = []
    for what, setup in (("cursor to track 2 (no length)", lambda: m.w8(ED + ED_CURSOR_TRACK, 1)),
                        ("nothing changed", lambda: None),
                        ("cursor back to track 1", lambda: m.w8(ED + ED_CURSOR_TRACK, 0)),
                        ("track 1's length changed elsewhere (a file load)", lambda: m.w8(table + 4 + 16, 9)),
                        ("page 3 left (its binds cleared), cursor to track 2",
                         lambda: (m.call(BTN_CLEAR, r.mgr, 0, 6), m.w8(ED + ED_CURSOR_TRACK, 1)))):
        setup()
        r.calls.clear()
        m.call(update, PATVIEW, 0, 0, 0)
        shown.append((what, r.text2()))
    check([t for _, t in shown] == ["--", None, "12", "9", None],
          "second line after each update: " + "; ".join(f"{w}: {t!r}" for w, t in shown))

    print("A length set with Trk Len plays")
    r = UiRig(ptf, table=None)
    r.table = table
    for s in range(16):
        r.put(0, s, 40 + s)
    r.bind()
    r.button(1)
    r.jog(-10)
    r.button(0)
    r.start()
    r.run(2 * 16 * STEP + FIRST_MICRO)
    got = [n - 40 for n in r.notes(1)]
    want = ([0, 1, 2, 3, 4, 5] * 3)[:16] * 2
    check(r.stored(0) == 6 and got[:32] == want, f"track 1 set to 6 with the button: plays {got[:18]} ...")


def jog_ram(ptf: Path, press: bool, scenario, deltas=(1, 1, -1, 3, -2, 5, -7, 1)) -> tuple[list[bytes], list]:
    r = UiRig(ptf, stock_jog=False)
    m = r.m
    for t in range(4):
        for s in range(16):
            r.put(t, s, 30 + s, (ROLL, 6) if s % 3 == 0 else (0, 0), (VOLUME, 20 + s) if s % 2 else (0, 0),
                  instr=4 + t)                  # sample instruments: synth slots need the synth system
    if press:
        r.bind()
        r.button(1)
        r.button(0)
    scenario(r)
    calls = []
    for delta in deltas:
        r.jog(delta)
        calls.append([(n, c) for n, c, _ in r.calls])
    skip = []
    try:
        lo = patch_ram(ptf, "tracklen")
        skip = [(lo, lo + TL_SIZE)]
    except SystemExit:
        pass
    dumps = []
    for lo, hi in ((0x20000000, 0x20050000), (0x20200000, 0x20208000)):
        b = bytearray(m.uc.mem_read(lo, hi - lo))
        for a, z in skip:
            if lo <= a < hi:
                b[a - lo:z - lo] = bytes(z - a)
        dumps.append(bytes(b))
    dumps += [bytes(m.uc.mem_read(r.cur, 0x3030)), bytes(m.uc.mem_read(r.nxt, 0x3030))]
    return dumps, calls


def stock_jog_checks(ptf: Path, stock: Path) -> None:
    print("Without the hold the encoder is stock's")
    names = [p["name"] for p in json.loads(ptf.with_suffix(".map.json").read_text())["patches"]]
    if names != ["tracklen"]:
        print(f"  (skipped: compared with stock only on tracklen alone; this build has {len(names)} patches)")
        return

    def column(rec, col, row=0, track=0):
        def setup(r):
            r.m.w8(ED + ED_RECORD, rec)
            r.m.w8(ED + ED_COLUMN, col)
            r.m.w16(ED + ED_CURSOR_ROW, row)
            r.m.w8(ED + ED_CURSOR_TRACK, track)
            r.m.w16(ED + 0x1EA, 16)
        return setup

    scenarios = [("record off", column(0, 0)), ("record off, row 5 of track 3", column(0, 0, 5, 2))]
    scenarios += [(f"record on, column {c}", column(1, c, 3, 1)) for c in range(4)]
    for label, scenario in scenarios:
        want = jog_ram(stock, False, scenario)
        still = jog_ram(stock, False, scenario, deltas=())
        changed = [i for i, (a, b) in enumerate(zip(want[0], still[0])) if a != b]
        for press in (False, True):
            got = jog_ram(ptf, press, scenario)
            diff = [i for i, (a, b) in enumerate(zip(got[0], want[0])) if a != b]
            check(not diff and got[1] == want[1],
                  f"{label}{', after Trk Len pressed and released' if press else ''}: "
                  f"8 encoder turns leave the same RAM and display calls as stock" + (f" (differ: {diff})" if diff else ""))
        check(bool(changed), f"{label}: (the turns do change stock's RAM: regions {changed})")


def site_checks(ptf: Path) -> None:
    print("Sites")
    stubs = json.loads(ptf.with_suffix(".map.json").read_text())["itcm_stubs"]["sites"]
    ours = {int(s["at"], 16) for s in stubs if s["patch"] == "tracklen"}
    for at, what in SITES.items():
        check(at in ours, f"ITCM {at:#x} ({what}) goes through a stub to tracklen")


def stock_checks(ptf: Path, stock: Path) -> None:
    print("No lengths: as stock firmware")
    others = [(t, s, 40 + s, (MICRO_MOVE, 20 * (s % 3)) if t % 2 else (VOLUME, 50), (ROLL, 6) if s % 5 == 0 else (0, 0))
              for t in (0, 1, 7, 15) for s in range(16) if (s + t) % 3]
    ram = patch_ram(ptf, "tracklen")
    cases = (("table set up, no lengths", None),
             ("table not set up (no magic)", lambda r: r.m.w32(ram, 0)),
             ("lengths 0, 16 and 40 in a 16-step pattern", lambda r: (r.length(0, 16), r.length(1, 40), r.length(7, 0))),
             ("a length the pattern was shortened below (10 in 8 steps)", "short"))
    for label, setup in cases:
        out = []
        for path, patched in ((ptf, True), (stock, False)):
            last = 7 if setup == "short" else 15
            rig = Rig(path, last, ram if patched else None)
            for t, s, note, fx1, fx2 in others:
                rig.put(t, s, note, fx1, fx2)
            if patched and setup == "short":
                rig.length(0, 10)
            elif patched and setup:
                setup(rig)
            rig.start()
            rig.run(3 * (last + 1) * STEP + FIRST_MICRO)
            out.append(rig.events())
        check(out[0] == out[1] and len(out[0]) > 50, f"{label}: the same {len(out[1])} MIDI events as stock")


def length_checks(ptf: Path, stock: Path) -> None:
    print("Lengths: as the pattern written out (the oracle)")
    for e in (1, 5, 6, 15):
        a, b = pair(ptf, stock, 15, {0: (e, notes_steps(e))}, others=[(1, s, 70 + s, (0, 0), (0, 0)) for s in range(16)])
        check(a == b and len(a) > 40, f"track 1 length {e} in 16 steps: {len(a)} events as the oracle")
    rig = Rig(ptf, 15, patch_ram(ptf, "tracklen"))
    for s in range(16):
        rig.put(0, s, 40 + s)
    rig.length(0, 6)
    rig.start()
    rig.run(3 * 16 * STEP + FIRST_MICRO)
    heard = [n - 40 for n in rig.notes(1)]
    check(heard[:48] == ([0, 1, 2, 3, 4, 5] * 3)[:16] * 3, f"length 6: plays {heard[:18]} ... (0-5 0-5 0-3, then again)")

    tracks = {t: (t + 1, notes_steps(t + 1, 30 + 3 * t)) for t in range(16)}
    a, b = pair(ptf, stock, 15, tracks)
    check(a == b and len(a) > 300, f"all 16 tracks, lengths 1..16 at once: {len(a)} events as the oracle")

    # positions and the pattern's end: track 16 short, every position still runs 0..15
    rig = Rig(ptf, 15, patch_ram(ptf, "tracklen"))
    for s in range(16):
        rig.put(15, s, 40 + s)
        rig.put(1, s, 40 + s)
    rig.length(15, 5)
    rig.start()
    seen = set()
    for _ in range(2 * 16 * STEP + FIRST_MICRO):
        rig.m.ticks += 1
        rig.m.call(st.SEQ_TICK, SEQ, max_insns=2_000_000)
        seen |= {struct.unpack("<H", rig.m.uc.mem_read(SEQ + 0x68 * t + 0x88, 2))[0] for t in range(16)}
    heard15, heard1 = [n - 40 for n in rig.notes(16)], [n - 40 for n in rig.notes(2)]
    check(heard15[:32] == ([0, 1, 2, 3, 4] * 4)[:16] * 2 and heard1[:32] == list(range(16)) * 2,
          f"track 16 length 5: {heard15[:17]} ...; track 2 plays 0..15 (the pattern still ends after 16 steps)")
    check(seen == set(range(16)), "every track's position (+0x88) still counts 0..15")

    a, b = pair(ptf, stock, 127, {0: (48, notes_steps(48, 20)), 1: (100, notes_steps(100, 10))},
                ticks=128 * STEP + 40 * STEP)
    check(a == b and len(a) > 250, f"128-step pattern, lengths 48 and 100: {len(a)} events as the oracle")


def step_checks(ptf: Path, stock: Path) -> None:
    print("What a step carries, on a short track")
    for v in (1, 6, 20, 40):
        steps = [(40, (0, 0), (0, 0), None), None, None, (43, (ROLL, v), (0, 0), None), None, None]
        a, b = pair(ptf, stock, 15, {0: (6, steps)})
        check(a == b and len(a) > 6, f"Roll {v} on step 4 of a 6-step track, empty steps after it: {len(a)} events as the oracle")
    steps = notes_steps(6, fx=lambda s: ((MICRO_MOVE, 90), (0, 0)) if s == 5 else ((0, 0), (0, 0)))
    a, b = pair(ptf, stock, 15, {0: (6, steps)})
    check(a == b, f"Micro-move 90 on the track's last step: {len(a)} events as the oracle")
    steps = notes_steps(6, fx=lambda s: ((BREAK, 0), (0, 0)) if s == 2 else ((0, 0), (0, 0)))
    a, b = pair(ptf, stock, 15, {0: (6, steps)}, others=[(1, s, 70 + s, (0, 0), (0, 0)) for s in range(16)])
    check(a == b, f"Break on step 3 of a 6-step track (ends the pattern there): {len(a)} events as the oracle")
    steps = [(40 + s, (0, 0), (0, 0), 0) for s in range(6)]
    a, b = pair(ptf, stock, 15, {0: (6, steps)}, voice=True)
    voices = [w for _, w in a if w.startswith("voice")]
    check(a == b and len(voices) >= 48, f"a sample instrument on a 6-step track: {len(voices)} voice note-ons as the oracle")
    steps = [(40 + s, (0, 0), (0, 0), 64) for s in range(5)]
    a, b = pair(ptf, stock, 15, {9: (5, steps)}, synth=True)
    ons = [w for _, w in a if w.startswith("voice")]
    check(a == b and len(ons) >= 48, f"a synth (instrument 65) on a 5-step track 10: {len(ons)} note-ons as the oracle")


def mode_checks(ptf: Path, stock: Path) -> None:
    print("Play modes, on a 6-step track")
    tracks = {0: (6, notes_steps(6))}
    others = [(1, s, 70 + s, (0, 0), (0, 0)) for s in range(16)]
    a, b = pair(ptf, stock, 15, tracks, others, start=9)
    check(a == b and len(a) > 40, f"Play from step 10: {len(a)} events as the oracle")

    def selection(rig):
        rig.m.call(st.SEQ_PLAY_FROM, SEQ, 8)
        rig.m.w8(SEQ + 0x1F, 1)
        rig.m.w8(SEQ + 0x20, 8)
        rig.m.w8(SEQ + 0x22, 11)
    a, b = pair(ptf, stock, 15, tracks, others, setup=selection, start=8, ticks=12 * STEP)
    check(a == b and len(a) > 10, f"a looping selection, steps 9..12: {len(a)} events as the oracle")

    def performance(rig):
        rig.m.call(st.SEQ_PLAY_FROM, SEQ, 0)
        rig.m.w8(SEQ + 0x1D, 1)
        for t in range(8):
            rig.m.w16(SEQ + 0x38 + 2 * t, 15)
        rig.m.w16(SEQ + 0x36, 0xFFFF)
        rig.m.w8(SEQ + 0xB9, 1)                  # track 1 backward
    a, b = pair(ptf, stock, 15, tracks, others, setup=performance, ticks=40 * STEP)
    check(a == b and len(a) > 40, f"performance mode, track 1 backward: {len(a)} events as the oracle")


def roll_checks(ptf: Path, stock: Path) -> None:
    print("Roll's look-ahead")
    hooks = {int(s["at"], 16): int(s["hook"], 16) for s in
             json.loads(ptf.with_suffix(".map.json").read_text())["itcm_stubs"]["sites"] if s["patch"] == "tracklen"}
    ram = patch_ram(ptf, "tracklen")
    pm = Rig(ptf, 15, ram)
    sk = Rig(stock, 15)
    content = {0: None, 3: 50, 5: 52, 9: 60, 13: 64}            # notes on the 6-step track 1 and on track 2
    for s, n in content.items():
        if n is not None:
            pm.put(0, s, n)
            pm.put(1, s, n)
            sk.put(1, s, n)
    for s in range(16):
        if s % 6 in content and content[s % 6] is not None:
            sk.put(0, s, content[s % 6])
    pm.put(2, 9, 60)                                            # track 3, length 5: a note only past it
    pm.length(0, 6)
    pm.length(2, 5)
    bad = []
    for at in (0x33A2, 0x3520):
        for t in (0, 1, 2):
            for step in range(16):
                got = pm.m.call(hooks[at] | 1, SEQ, t, step)
                want = sk.m.call(DISTANCE | 1, SEQ, t, step)
                if got != want:
                    bad.append((hex(at), t, step, got, want))
    check(not bad, f"both hooks, every step of a 6-step track, a 5-step track with no note in its steps and a "
                   f"track without a length: as stock on the written-out pattern"
                   f"{'' if not bad else f' (differs: {bad[:4]})'}")


def song_checks(ptf: Path) -> None:
    print("Song mode, pattern A then B")
    rig = Rig(ptf, 15, patch_ram(ptf, "tracklen"))
    m = rig.m
    a_buf, b_buf = st.pattern(m, 15), st.pattern(m, 15)
    for s in range(16):
        st.set_step(m, a_buf, 0, s, 40 + s, 48)
        st.set_step(m, b_buf, 0, s, 70 + s, 48)
    data_b = bytes(m.uc.mem_read(b_buf, 0x3030))
    m.write(rig.cur, bytes(m.uc.mem_read(a_buf, 0x3030)))
    m.write(PROJECT + 0x5C2, bytes([1, 2, 0]))
    m.w8(PROJECT + 0x6C1, 0)
    rig.length(0, 6, pattern=1)
    rig.length(0, 4, pattern=2)
    requests = []

    def request(mm):
        requests.append((mm.ticks, mm.arg(1)))
        mm.write(struct.unpack("<I", mm.uc.mem_read(SEQ + 0x18, 4))[0], data_b)
        mm.w8(0x2001E488 + 0x130, mm.arg(1))
        return 1
    m.stub_function(st.FM_REQUEST_PATTERN_LOAD, request)
    m.call(st.SEQ_PLAY_FROM, SEQ, 0)
    m.w8(SEQ + 0x1C, 1)
    rig.tick0 = m.ticks
    m.events.clear()
    rig.run(2 * 16 * STEP + FIRST_MICRO)
    steps = [(t - rig.tick0 - FIRST_MICRO) // STEP for t, _ in requests]
    heard = rig.notes(1)
    check(steps[:1] == [15] and requests[0][1] == 2, f"B requested on step 16 ({steps[:1]}), as stock firmware")
    check([n - 40 for n in heard[:16]] == ([0, 1, 2, 3, 4, 5] * 3)[:16],
          f"A's track 1 (length 6): {[n - 40 for n in heard[:16]]}")
    check([n - 70 for n in heard[16:32]] == [0, 1, 2, 3] * 4,
          f"then B (pattern 2) plays its own length 4: {[n - 70 for n in heard[16:32]]}")


def cost_checks(ptf: Path) -> None:
    print("Cost (instructions in the patch's code and its ITCM stubs, 16 tracks with notes, 4 passes;"
          " mt_tl_len, which only condtrig calls, counts in condtrig's cost check)")
    lo, hi = patch_code(ptf, "tracklen")
    len_lo, len_hi = patch_function(ptf, "tracklen", "mt_tl_len")
    stubs = [int(x["stub"], 16) for x in json.loads(ptf.with_suffix(".map.json").read_text())["itcm_stubs"]["sites"]
             if x["patch"] == "tracklen"]
    for label, short in (("no lengths", False), ("16 tracks of length 5", True)):
        rig = Rig(ptf, 15, patch_ram(ptf, "tracklen"))
        for t in range(16):
            for s in range(16):
                rig.put(t, s, 40 + s)
            if short:
                rig.length(t, 5)
        rig.start()
        n = [0]
        count = lambda uc, a, size, u: n.__setitem__(0, n[0] + 1)
        hs = [rig.m.uc.hook_add(UC_HOOK_CODE, count, begin=a, end=b - 1) for a, b in ((lo, len_lo), (len_hi, hi))]
        hs += [rig.m.uc.hook_add(UC_HOOK_CODE, count, begin=a, end=a + 11) for a in stubs]
        rig.run(4 * 16 * STEP)
        for h in hs:
            rig.m.uc.hook_del(h)
        per = n[0] / (4 * 16 * 16)
        check(per <= PER_TRACK_LIMIT, f"{label}: {per:.1f} instructions per track and step (at most {PER_TRACK_LIMIT})")


class GridRig:
    """The Pattern grid of one pattern: every step of every track filled (note, sample
    instrument, FX1 and FX2), except the steps `empty` names, which stay empty. `loop`
    ({track: E}) writes a track's first E steps out to the pattern's end (step s holds step
    s mod E's contents); `holes` ({(track, step)}) leaves those steps (before the write-out)
    empty."""

    def __init__(self, ptf: Path, last: int, table: int | None = None, lengths=None, empty=None,
                 loop=None, holes=frozenset(),
                 cursor: int = 0, wrap: int = 0, highlight_track: int | None = None, first_track: int = 0,
                 mutes=(), layout: int = 0, mask: int = 0, three=(), pattern: int = 1):
        self.m = m = st.make(ptf)
        m.w32(mach.FAKE_DISPLAY + 8, STYLE)
        buf = st.pattern(m, last)
        m.w32(SEQ + 0x14, buf)
        m.w8(CUR_PATTERN, 1)
        for t in range(16):
            for s in range(last + 1):
                src = s % loop[t] if loop and t in loop and s >= loop[t] else s
                if not (empty and s >= empty.get(t, 999)) and (t, src) not in holes:
                    st.set_step(m, buf, t, s, 24 + src % 96, 4 + t, (VOLUME, 10 + src % 50), (ROLL, 1 + src % 5))
        if table is not None:
            m.write(table, struct.pack("<I", MAGIC) + bytes(256 * 16))
            for t, e in (lengths or {}).items():
                m.w8(table + 4 + 16 * pattern + t, e)
        m.w8(st.SETTINGS + 0xD2, wrap)
        m.w16(ED + ED_CURSOR_ROW, cursor)
        for t in mutes:
            m.w8(MUTES + t, 1)
        m.call(FILL_ROWS, PATVIEW)
        m.w8(ED + ED_FIRST_TRACK, first_track)
        m.w8(ED + ED_LAYOUT, layout)
        if highlight_track is not None:
            m.w8(ED + ED_CURSOR_TRACK, highlight_track)
            m.w8(ED + ED_HIGHLIGHT, 1)
        for t in three:                     # a 3-character instrument text on every filled row
            for row in range(15):
                at = GRID_DATA + 0x11D * t + 0x13 * row + 5
                if m.uc.mem_read(at, 1) not in (b"-", b"\0"):
                    m.write(at, b"100\0")
        desc = m.alloc(0x20)
        m.w32(desc + 4, 0)
        m.w32(desc + 8, GRID_DATA)
        m.w32(desc + 0xC, 0x1000400)
        m.w32(desc + 0x10, STYLE + 0x2B8)
        for i, v in enumerate((0, 0x1C, 0x320, 0x18B)):
            m.w16(desc + 0x14 + 2 * i, v)
        self.ctrl = m.alloc(0x50)
        m.call(GRID_CTOR, self.ctrl, desc)
        m.w8(self.ctrl + 0x18, mask)        # the single-column view's column bit (ed+0x4dc)

    def draw(self):
        """[(text pointer, color)] for each cell, the sites used, the display list"""
        m, cells, sites = self.m, [], []

        def hook(uc, addr, size, user):
            sp = uc.reg_read(UC_ARM_REG_SP)
            ctrl = uc.reg_read(UC_ARM_REG_R0)
            cells.append((m.r32(sp), m.r32(ctrl + 0x44)))
        h = m.uc.hook_add(UC_HOOK_CODE, hook, begin=DRAW_CELL, end=DRAW_CELL)
        lr: list[int] = []
        h2 = m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: lr.append(a), begin=min(CELL_SITES), end=max(CELL_SITES))
        m.display_list.clear()
        vt = m.r32(self.ctrl)
        for k in range(5):
            m.call(m.r32(vt + 4 * k), self.ctrl)
        m.uc.hook_del(h)
        m.uc.hook_del(h2)
        return cells, {a for a in lr if a in CELL_SITES}, list(m.display_list)

    def colors(self) -> tuple[int, ...]:
        """note, instrument, FX1, FX2: normal, then empty"""
        base = STYLE + 0x2B8
        return tuple(self.m.r32(base + o) for o in (8, 0xC, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24))


def past_end(rig: "GridRig", pointer: int, lengths: dict) -> int | None:
    """The column (0..3) of the cell with this text pointer when its step is at or past its
    track's length, else None (as the patch decodes it)"""
    m = rig.m
    off = pointer - GRID_DATA
    track, row, col = off // 0x11D, off % 0x11D // 0x13, {0: 0, 5: 1, 9: 2, 0xE: 3}.get(off % 0x11D % 0x13)
    length = struct.unpack("<H", m.uc.mem_read(ED + 0x1EA, 2))[0]
    e = lengths.get(track, 0)
    if col is None or not 1 <= e < length:
        return None
    step = struct.unpack("<H", m.uc.mem_read(ED + 0x1E8, 2))[0] - 7 + row
    if not 0 <= step < length:
        if m.uc.mem_read(st.SETTINGS + 0xD2, 1)[0]:
            return None
        step %= length
    return col if step >= e else None


def grid_checks(ptf: Path, stock: Path) -> None:
    print("The Pattern grid: past a track's length, its loop going on, grey (a muted track's color)")
    table = patch_ram(ptf, "tracklen")
    dim_sites: set[int] = set()
    base = STYLE + 0x2B8

    def case(what, last, lengths, holes=frozenset(), **kw):
        """the patched grid with lengths against stock's with each short track's loop
        written out to the pattern's end: past the end, a cell in its column's normal color
        grey, an empty one blank (as many spaces), everything else as stock"""
        got = GridRig(ptf, last, table, lengths, holes=holes, **kw)
        cells, sites, _ = got.draw()
        oracle = GridRig(stock, last, loop={t: e for t, e in lengths.items() if e <= last}, holes=holes, **kw)
        written, _, _ = oracle.draw()
        bad, grey, blank = [], 0, 0
        for (p, c), (wp, wc) in zip(cells, written):
            text, want_text = got.m.cstr(p), oracle.m.cstr(wp)
            col = past_end(oracle, wp, lengths)
            want_c, want_t = wc, want_text
            if col is not None and wc == oracle.m.r32(base + 8 + 4 * col):
                want_c, grey = oracle.m.r32(base + 0x30), grey + 1
            elif col is not None and wc == oracle.m.r32(base + 0x18 + 4 * col) and want_text.startswith("-"):
                want_t, blank = " " * len(want_text), blank + 1
            if not ((p == wp or want_t != want_text) and c == want_c and text == want_t):   # a blank is the patch's own text
                bad.append((hex(wp), want_text, text, hex(wc), hex(c)))
        check(len(cells) == len(written) and not bad and grey > 0,
              f"{what}: {len(cells)} cells, {grey} grey and {blank} blank past the end (the loop going on), "
              f"the rest as stock{'' if not bad else f' (differ: {bad[:3]})'}")
        if not bad:
            for site in sites:
                dim_sites.add(site)
        return got

    rig = case("16 steps, lengths 6, 1, 15 on tracks 1-3; cursor at step 10 of track 1, track 3 muted", 15,
               {0: 6, 1: 1, 2: 15}, cursor=10, highlight_track=0, mutes=(2,))
    for wrap, cursor in ((0, 0), (1, 2), (2, 1), (2, 14), (0, 15), (1, 15)):
        case(f"row setting {wrap}, cursor at step {cursor}", 15, {0: 6, 1: 3, 3: 9}, cursor=cursor, wrap=wrap)
    case("a 4-step pattern, lengths 1, 2, 3 (rows wrap round more than once)", 3, {0: 1, 1: 2, 2: 3}, cursor=1)
    case("128 steps, length 100, cursor at step 98", 127, {0: 100, 2: 1, 3: 127}, cursor=98)
    case("scrolled to tracks 13-16", 15, {12: 5, 13: 1, 15: 12}, cursor=8, first_track=12, highlight_track=15)
    for layout, what in ((1, "an overlay open (8 rows)"), (2, "a side panel (3 tracks)"), (4, "a side panel (2 tracks)")):
        case(f"layout: {what}", 15, {0: 6, 1: 3, 2: 2}, cursor=9, layout=layout)
    for mask, what in ((1, "notes"), (2, "instruments"), (4, "FX1"), (8, "FX2")):
        case(f"single-column view ({what}, 8 tracks)", 15, {0: 6, 3: 2, 6: 11, 7: 1}, cursor=4, mask=mask)
    case("3-character instrument texts", 15, {0: 6, 1: 2}, cursor=3, three=(0, 1, 2))
    check(dim_sites == set(CELL_SITES), "every call site dims a cell: "
          + ", ".join(CELL_SITES[s] for s in sorted(dim_sites)))
    n = rig.colors() + (rig.m.r32(base + 0x30),)
    check(len(set(n)) == 9, "the 8 column colors and the muted one are distinct: " + " ".join(f"{c:06x}" for c in n))

    print("Empty steps in a short track's loop: blank past the end")
    holes = frozenset({(0, 1), (0, 4), (1, 0), (2, 2), (2, 3)})
    case("cursor on track 1 at step 9, track 3 muted", 15, {0: 6, 1: 3, 2: 4}, holes,
         cursor=9, highlight_track=0, mutes=(2,))
    case("row setting 0 (rows wrap round), cursor at step 1", 15, {0: 6, 1: 3, 2: 4}, holes, cursor=1, wrap=0)
    case("3-character instrument texts", 15, {0: 6, 1: 3, 2: 4}, holes, cursor=3, three=(0, 1))

    for what, setup in (("no lengths", lambda m: None),
                        ("table not set up", lambda m: m.write(table, bytes([0xA5]) * (4 + 256 * 16))),
                        ("lengths for pattern 2 only", lambda m: [m.w8(table + 4 + 32 + t, 3) for t in range(16)]),
                        ("lengths at or above the pattern's", lambda m: [m.w8(table + 4 + 16 + t, 16 + t) for t in range(16)])):
        got = GridRig(ptf, 15, table, cursor=5, highlight_track=1, mutes=(3,))
        setup(got.m)
        cells, _, dl = got.draw()
        want, _, want_dl = GridRig(stock, 15, cursor=5, highlight_track=1, mutes=(3,)).draw()
        check(cells == want and dl == want_dl, f"{what}: every cell and the display list as stock ({len(cells)} cells)")

    print("Trk Len redraws the grid")
    r = UiRig(ptf, table=table)
    r.m.w8(ED + ED_GRID_CTRL, 40)
    r.bind()
    r.button(1)
    r.jog(-3)
    check(("refresh", 40) in [(n, c) for n, c, _ in r.calls] and r.stored(0) == 13,
          f"encoder -3 while held: track 1 length {r.stored(0)}, the grid (control 40) is refreshed")
    r.button(0)

    print("Trk Len refills the grid: the loop for the new length at once, with no cursor move")
    r = UiRig(ptf, table=table)
    m = r.m
    for s in range(16):
        r.put(0, s, 40 + s)
    m.w8(table + 4 + 16, 0)
    m.w8(CUR_PATTERN, 1)
    m.w8(ED + ED_CURSOR_TRACK, 0)
    m.w16(ED + ED_CURSOR_ROW, 7)
    m.w8(ED + ED_GRID_CTRL, 40)

    def note_texts() -> dict[int, str]:
        """track 1's note text per step shown, from the grid's row texts"""
        centre = struct.unpack("<H", m.uc.mem_read(ED + 0x1E8, 2))[0]
        return {centre - 7 + row: m.cstr(GRID_DATA + 0x13 * row) for row in range(15)}
    m.call(FILL_ROWS, PATVIEW)
    base = note_texts()
    r.bind()
    r.button(1)
    for delta, e in ((-12, 4), (2, 6), (-1, 5), (11, 0)):
        r.jog(delta)
        got = note_texts()
        want = {s: base[s % e] if e and s >= e else base[s] for s in got}
        check(got == want and r.stored(0) == e and len(set(base.values())) == 15,
              f"encoder {delta:+d} to length {e or '--'}: every row shows "
              + ("the loop past the end" if e else "its own step") + ("" if got == want else f" (got {got})"))
    r.button(0)


def branch_target(image: bytes, at: int) -> int:
    """Where a Thumb-2 BL or B.W goes"""
    hw1, hw2 = struct.unpack_from("<HH", image, at - 0x60030000)
    s, j1, j2 = hw1 >> 10 & 1, hw2 >> 13 & 1, hw2 >> 11 & 1
    off = s << 24 | (~(j1 ^ s) & 1) << 23 | (~(j2 ^ s) & 1) << 22 | (hw1 & 0x3FF) << 12 | (hw2 & 0x7FF) << 1
    return at + 4 + off - (1 << 25 if s else 0)


class RecRig(Rig):
    """Rig, recording during play: notes from the pads through the recorders' sites"""

    def __init__(self, ptf: Path, last: int, table: int | None = None):
        super().__init__(ptf, last, table)
        m = self.m
        m.w8(st.SETTINGS + 0xCE, 2)
        m.w32(SCREEN_MANAGER + 8, PATVIEW)
        m.w8(ED + ED_RECORD, 1)
        self.irq: list[str] = []

        def icer(uc, access, a, size, value, user):
            if value & 1:
                m.w32(NVIC_ISER, m.r32(NVIC_ISER) & ~1)
        m.uc.hook_add(UC_HOOK_MEM_WRITE, icer, begin=NVIC_ICER, end=NVIC_ICER + 3)
        for at in (REC_LIVE, REC_OFF):
            m.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: self.irq.append("off" if m.r32(NVIC_ISER) & 1 == 0 else "on"),
                          begin=at, end=at)
        m.w32(NVIC_ISER, 1)

    def record(self) -> None:
        self.m.w8(SEQ + 0x2B, 1)

    def press(self, note: int, veneer: int = REC_VENEERS[1]) -> None:
        self.m.call(veneer, SEQ, PADS, note, 0x7F, 0, max_insns=20_000_000)

    def release(self, note: int, site: int = REC_OFF_SITES[3]) -> None:
        self.m.call(branch_target(self.m.image, site), SEQ, PADS, note, 0, 0, max_insns=20_000_000)

    def rows(self, t: int) -> list[bytes]:
        b = bytes(self.m.uc.mem_read(self.cur + 0x1D + 0x301 * t, 6 * (self.last + 1)))
        return [b[6 * s:6 * s + 6] for s in range(self.last + 1)]

    def tracks(self) -> bytes:
        return bytes(self.m.uc.mem_read(SEQ + 0x68, 0x68 * 16))


def rec_case(ptf: Path, stock: Path, table: int, what: str, last: int, lengths: dict[int, int],
             real: dict[int, dict[int, int]], actions, lands=None) -> RecRig:
    """lengths {track: E}; real {track: {step: note}} (a short track's steps below E);
    actions [(tick, "press" or "release", note)], played from the step before the first;
    the oracle holds while no later action reads a row an earlier one changed through
    another row of the same step (so length 1 has no note-off case); lands (track, row, note): where the
    patched pattern must get the note. The dimmed rows hold notes 100 + s where the real
    step is empty and nothing where it holds one."""
    rigs = []
    for path, patched in ((ptf, True), (stock, False)):
        r = RecRig(path, last, table if patched else None)
        for t in range(16):
            e = lengths.get(t, last + 1)
            steps = real.get(t, {})
            for s in range(last + 1):
                if patched and s >= e:
                    note = None if (s % e) in steps else 100 + s % 20
                else:
                    note = steps.get(s % e)
                if note is not None:
                    r.put(t, s, note, bufs=(r.cur,))
            if patched and t in lengths:
                r.length(t, e)
        begin = max(0, min(tick for tick, _, _ in actions) // STEP - 1)
        r.start(begin)
        r.record()
        before = [r.rows(t) for t in range(16)]
        now = STEP * begin
        for tick, kind, note in actions:
            r.run(tick - now)
            now = tick
            r.press(note) if kind == "press" else r.release(note)
        rigs.append((r, before))
    (p, pb), (s, sb) = rigs
    ok, why = True, []
    wrote = 0
    for t in range(16):
        pa, sa = p.rows(t), s.rows(t)
        if t not in lengths:
            if pa != sa:
                ok = False
                why.append(f"track {t + 1} differs from stock")
            continue
        e = lengths[t]
        changed = {x: sa[x] for x in range(last + 1) if sa[x] != sb[t][x]}
        for r_ in range(e):
            hits = [x for x in changed if x % e == r_]
            if len(hits) > 1:
                why.append(f"track {t + 1}: stock changed rows {hits} (one step of the loop): the case is ambiguous")
                ok = False
            want = changed[hits[0]] if hits else pb[t][r_]
            wrote += bool(hits)
            if pa[r_] != want:
                ok = False
                why.append(f"track {t + 1} step {r_}: {pa[r_].hex()}, stock wrote {want.hex()}")
        if pa[e:] != pb[t][e:]:
            ok = False
            why.append(f"track {t + 1}: a dimmed row changed")
    same_state = p.tracks() == s.tracks() and p.events() == s.events()
    landed = lands is None or p.rows(lands[0])[lands[1]][0] == lands[2] & 0xFF
    check(ok and same_state and landed and (wrote > 0 or lands is None or lands[0] not in lengths),
          f"{what}: {wrote} row(s) recorded onto short tracks as stock records the written-out track"
          + ("" if same_state else " (track state or MIDI differs)")
          + ("" if landed else f" (expected note {lands[2]} at track {lands[0] + 1} step {lands[1]})")
          + ("" if not why else ": " + "; ".join(why[:3])))
    return p


def record_checks(ptf: Path, stock: Path) -> None:
    print("Live recording onto a short track's steps")
    table = patch_ram(ptf, "tracklen")
    real = {0: {2: 42}}                         # track 1, length 6: a note on step 2 only
    at = lambda step, micro=8: STEP * step + 12 * micro
    rec_case(ptf, stock, table, "early hit on step 11 (row 10 -> step 4)", 15, {0: 6}, real,
             [(at(10), "press", 60)], (0, 4, 60))
    rec_case(ptf, stock, table, "early hit on step 10 while step 3's note sounds (-> track 2, row 9)", 15, {0: 6}, real,
             [(at(9), "press", 61)], (1, 9, 61))
    rec_case(ptf, stock, table, "late hit on step 6, E's last (-> step 0)", 15, {0: 6}, real,
             [(at(5, 40), "press", 62)], (0, 0, 62))
    rec_case(ptf, stock, table, "late hit on the pattern's last step (-> step 0)", 15, {0: 6}, {0: {}},
             [(at(15, 40), "press", 63)], (0, 0, 63))
    rec_case(ptf, stock, table, "note-off in the same step (row 11 -> step 5)", 15, {0: 6}, real,
             [(at(10), "press", 60), (at(10, 30), "release", 60)], (0, 5, -4))
    rec_case(ptf, stock, table, "note-off two steps later (row 12 -> step 0)", 15, {0: 6}, real,
             [(at(10), "press", 60), (at(12, 10), "release", 60)], (0, 0, -4))
    rec_case(ptf, stock, table, "note-off onto a step holding a note (row 12 -> step 0, kept)", 15, {0: 6},
             {0: {0: 40, 2: 42}}, [(at(10), "press", 60), (at(12, 10), "release", 60)], (0, 0, 40))
    rec_case(ptf, stock, table, "length 1: a hit on step 8", 15, {0: 1}, {0: {}},
             [(at(7), "press", 64)], (0, 0, 64))
    rec_case(ptf, stock, table, "128 steps, length 100: a hit on step 111 (-> step 10)", 127, {0: 100}, real,
             [(at(110), "press", 65)], (0, 10, 65))
    rec_case(ptf, stock, table, "two short tracks (6, 5): track 1's note sounds, track 2 gets row 9 (-> step 4)",
             15, {0: 6, 1: 5}, {0: {2: 42}, 1: {}}, [(at(9), "press", 66)], (1, 4, 66))
    rec_case(ptf, stock, table, "a track without a length: as stock", 15, {1: 6}, {1: {}},
             [(at(9), "press", 67), (at(10, 5), "release", 67)], (0, 9, 67))
    p = rec_case(ptf, stock, table, "no lengths: as stock", 15, {}, {},
                 [(at(9), "press", 68), (at(10, 5), "release", 68)], (0, 9, 68))
    check(p.irq == ["on", "on"], f"no lengths: the audio interrupt isn't touched ({p.irq})")

    print("Recording over several loops: a short track's note-offs don't block a note")

    def loops(path: Path, lengths: dict[int, int], hits, last: int = 15, put=()):
        r = RecRig(path, last, table if lengths else None)
        r.m.stub_function(VOICE_NOTE_ON, lambda mm: 0)
        for t, s_, note in put:
            r.put(t, s_, note, bufs=(r.cur,))
        for t, e in lengths.items():
            r.length(t, e)
        r.start(0)
        r.record()
        now, got = 0, []
        for i, step in enumerate(hits):
            note = 60 + i
            r.run(at(step) - now)
            now = at(step)
            r.press(note)
            r.run(at(step, 14) - now)
            now = at(step, 14)
            r.release(note)
            got.append(next(((t, s_) for t in range(16) for s_, row in enumerate(r.rows(t)) if row[0] == note), None))
        return r, got

    # length 6: three notes and their note-offs fill steps 0-5 in loop 1; loop 2's hits on
    # steps 1 and 3 (note-offs) stay on track 1; then step 1 holds a note: the next track
    r, got = loops(ptf, {0: 6}, [0, 2, 4, 7, 9, 13])
    want = [(0, 0), (0, 2), (0, 4), (0, 1), (0, 3), (1, 13)]
    check(got == want, f"length 6, hits on steps 1, 3, 5, then 8 and 10 (loop 2's steps 2 and 4, note-offs), then "
                       f"14 (step 2, now a note): {got}")
    check([b[0] for b in r.rows(0)[:6]] == [60, 63, 61, 64, 62, 0xFC] and r.rows(0)[6:] == [bytes([0xFF] + [0] * 5)] * 10,
          f"track 1's steps: {[b[0] for b in r.rows(0)[:6]]}, the dimmed rows untouched")
    # a hit that goes to another track (its step holds a note) leaves the note-offs it
    # could reach in place
    r, got = loops(ptf, {0: 6}, [0, 2, 6], put=[(1, 6, 50)])
    check(got[2] == (2, 6) and [b[0] for b in r.rows(0)[:6]] == [60, 0xFC, 61, 0xFC, 0xFF, 0xFF],
          f"a hit on step 7 (loop step 1, a note) goes to track 3 (track 2 has a note there): {got[2]}; "
          f"track 1's note-offs stay: {[b[0] for b in r.rows(0)[:6]]}")
    # length 1 and length 2: the one or two steps
    r, got = loops(ptf, {0: 2}, [0, 3, 5])
    check(got == [(0, 0), (0, 1), (1, 5)], f"length 2: hits on steps 1, 4 (a note-off), 6 (a note): {got}")
    # a track without a length keeps stock's rule: its note-off blocks
    s_got = loops(stock, {}, [0, 1])[1]
    r, got = loops(ptf, {2: 6}, [0, 1])
    check(got == s_got == [(0, 0), (1, 1)], f"a track without a length: a hit onto its note-off goes to the next "
                                            f"track, as stock ({got}, stock {s_got})")

    print("Every recording site, and the audio interrupt")
    r = RecRig(ptf, 15, table)
    r.put(0, 2, 42, bufs=(r.cur,))
    r.length(0, 6)
    r.start(9)
    r.record()
    r.run(at(10) - at(9, 0))
    m = r.m
    lo, hi = patch_code(ptf, "tracklen")
    ours: list[int] = []                        # ISER bit 0 at each of the patch's writes to the pattern

    def write(uc, access, a, size, value, user):
        if lo <= uc.reg_read(UC_ARM_REG_PC) < hi:
            ours.append(m.r32(NVIC_ISER) & 1)
    m.uc.hook_add(UC_HOOK_MEM_WRITE, write, begin=r.cur, end=r.cur + 0x3030)
    regions = [(0x20000000, 0x58000), (0x20200000, 0x8000), (r.cur, 0x3030)]
    snap = [bytes(m.uc.mem_read(a, n)) for a, n in regions]

    def restore(state):
        for (a, n), b in zip(regions, state):
            m.write(a, b)
    results = []
    for v in REC_VENEERS:
        restore(snap)
        r.irq.clear()
        ours.clear()
        r.press(70, v)
        results.append((r.rows(0)[4], r.irq[:], ours[:], m.r32(NVIC_ISER) & 1))
    first = results[0]
    check(all(x == first for x in results) and first[0][0] == 70,
          "all 8 veneers record the hit on step 5 (row 10)")
    check(first[1] == ["on"] and first[2] and not any(first[2]) and first[3] == 1,
          f"the patch's {len(first[2])} writes to the pattern happen with the audio interrupt masked; stock records "
          f"with it as the caller left it (enabled), and it is enabled after")
    restore(snap)
    r.press(70)
    after_press = [bytes(m.uc.mem_read(a, n)) for a, n in regions]
    offs = []
    for site in REC_OFF_SITES:
        restore(after_press)
        r.irq.clear()
        ours.clear()
        r.release(70, site)
        offs.append((r.rows(0)[5], r.irq[:], ours[:], m.r32(NVIC_ISER) & 1))
    first = offs[0]
    check(all(x == first for x in offs) and first[0][0] == 0xFC and first[1] == ["on"] and first[2]
          and not any(first[2]) and first[3] == 1,
          "all 18 note-off sites (2 of them tail calls) put the note-off on step 6 (row 11), the patch's "
          "writes masked, stock entered with the interrupt enabled")
    restore(after_press)
    m.w32(NVIC_ISER, 0)
    r.irq.clear()
    ours.clear()
    r.release(70)
    check(r.irq == ["off"] and ours and not any(ours),
          f"with the audio interrupt masked by the caller, stock is entered with it still masked ({r.irq})")


def fnv1a(data: bytes) -> int:
    h = 2166136261
    for b in data:
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h


def tln1(lengths: dict[int, tuple]) -> bytes:
    """tracklen.bin for {pattern: 16 lengths}: TLN1, the count, the entries, a checksum"""
    entries = b"".join(bytes([p]) + bytes(v) for p, v in sorted(lengths.items()) if any(v))
    count = struct.pack("<I", len(entries) // 17)
    return b"TLN1" + count + entries + struct.pack("<I", fnv1a(count + entries))


def file_checks(ptf: Path) -> None:
    print("the lengths' file (tracklen.bin), through the screens' update hooks")
    table = patch_ram(ptf, "tracklen")
    m = mach.Machine(ptf)
    fs = FakeFS(m, Path("."))
    m.stub_function(PATTERN_UPDATE, lambda mm: 0)
    m.stub_function(CONFIG_UPDATE, lambda mm: 0)
    refreshed: list[int] = []
    m.stub_function(CTRL_REFRESH, lambda mm: refreshed.append(mm.arg(1)))
    m.w8(ED + ED_GRID_CTRL, 0xFF)               # no grid control: the Pattern screen not built
    pat_hook, cfg_hook = m.r32(PATTERN_UPDATE_SLOT), m.r32(CONFIG_UPDATE_SLOT)
    m.write(table, bytes([0xA5]) * TL_SIZE)     # RAM at power-on

    def tick(n=1, hook=None):
        for _ in range(n):
            m.call(hook or pat_hook, PATVIEW, max_insns=50_000_000)

    def get() -> dict[int, tuple]:
        raw = bytes(m.uc.mem_read(table + 4, 256 * 16))
        return {p: tuple(raw[16 * p:16 * p + 16]) for p in range(1, 256) if any(raw[16 * p:16 * p + 16])}

    def put(lengths: dict[int, tuple]) -> None:     # as the Trk Len encoder does
        raw = bytearray(256 * 16)
        for p, v in lengths.items():
            raw[16 * p:16 * p + 16] = bytes(v)
        m.write(table + 4, bytes(raw))
        m.w8(table + TL_CHANGED, 1)

    def path(name):
        return f"Projects/User/{name}/tracklen.bin"

    def writes():
        return len([x for x in fs.log if "tracklen.bin mode 3" in x])

    def reopen(name="A", hook=None):
        fs.set_project(Path("."), "B" if name != "B" else "C", "Projects/User")
        tick(hook=hook)
        fs.set_project(Path("."), name, "Projects/User")
        tick(hook=hook)

    def lens(**kw) -> tuple:
        v = [0] * 16
        for k, n in kw.items():
            v[int(k[1:]) - 1] = n
        return tuple(v)

    fs.set_project(Path("."), "A", "Projects/User")
    tick()
    check(m.r32(table) == MAGIC and get() == {}, "power-on garbage, a project without tracklen.bin: no lengths")
    tick(SAVE_DELAY + 5)
    check(writes() == 0 and not fs.removed, "nothing written while nothing changed")
    a = {1: lens(t1=6), 200: lens(t3=5, t16=100)}
    put(a)
    tick(SAVE_DELAY - 2)
    check(writes() == 0, "no write right after a change")
    tick(5)
    check(writes() == 1 and fs.overlay.get(path("A")) == tln1(a),
          f"the change is written once, a moment later (TLN1, 2 entries: {len(tln1(a))} bytes)")
    tick(SAVE_DELAY * 2)
    check(writes() == 1, "and not again while nothing changes")
    reopen("B")
    check(get() == {}, "another project without the file: no lengths")
    reopen()
    check(get() == a, "back to the first project: its lengths again")

    full = {p: tuple((p + t) % 120 + 1 for t in range(16)) for p in range(1, 256)}
    put(full)
    tick(SAVE_DELAY + 5)
    check(fs.overlay.get(path("A")) == tln1(full) and len(tln1(full)) == 8 + 255 * 17 + 4,
          f"every pattern with lengths: {len(tln1(full))} bytes written")
    reopen()
    check(get() == full, "and read back whole")
    check(max(fs.read_sizes + fs.write_sizes) <= 256, f"every read and write is at most 256 bytes "
          f"(largest {max(fs.read_sizes + fs.write_sizes)})")

    good = tln1(a)
    body = good[4:-4]
    count = struct.pack("<I", 3)
    zero = struct.pack("<I", 1) + bytes(17)
    for label, bad in (("a wrong checksum", good[:-1] + bytes([good[-1] ^ 1])),
                       ("a wrong magic", b"X" + good[1:]),
                       ("a count of 256", b"TLN1" + struct.pack("<I", 256) + good[8:]),
                       ("more entries counted than there are", b"TLN1" + count + body[4:]
                        + struct.pack("<I", fnv1a(count + body[4:]))),
                       ("an entry for pattern 0", b"TLN1" + zero + struct.pack("<I", fnv1a(zero))),
                       ("the checksum missing", good[:-4]),
                       ("the header only", good[:8]),
                       ("an empty file", b"")):
        fs.overlay[path("A")] = bad
        reopen()
        check(get() == {}, f"{label}: no lengths (none of it used)")
    fs.overlay[path("A")] = good
    reopen()
    check(get() == a, "the file put back: its lengths")

    put({})
    tick(SAVE_DELAY + 5)
    check(path("A") not in fs.overlay and path("A") in fs.removed, "every length cleared: the file is removed")
    n = writes()
    reopen()
    tick(SAVE_DELAY + 5)
    check(get() == {} and writes() == n, "reopened: no lengths, nothing written")
    put(a)
    tick(SAVE_DELAY + 5, cfg_hook)
    check(fs.overlay.get(path("A")) == good, "through the Config screen's update too")

    print("the file manager busy, and the grid")
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    m.w8(FM_STATE, 5)                           # the file manager busy (a project being opened)
    fs.set_project(Path("."), "A", "Projects/User")
    reads = len([x for x in fs.log if "tracklen.bin mode 0" in x])
    tick(3)
    check(len([x for x in fs.log if "tracklen.bin mode 0" in x]) == reads and get() == {},
          "while the file manager is busy: the file isn't read yet (no lengths)")
    m.w8(FM_STATE, 0)
    m.w8(ED + ED_GRID_CTRL, 9)
    refreshed.clear()
    tick()
    loaded = list(refreshed)
    tick(3)
    check(get() == a and loaded == [9] and refreshed == [9],
          f"once it is idle: read, and the grid redrawn once ({refreshed})")
    m.w8(ED + ED_GRID_CTRL, 0xFF)
    m.w8(FM_STATE, 5)
    fs.set_project(Path("."), "B", "Projects/User")
    tick()
    check(get() == {}, "another project opened while the file manager is busy: the lengths cleared at once")
    m.w8(FM_STATE, 0)
    m.write(PROJECT_NAME, b"\0")               # the name cleared while Workspace loads
    tick()
    put(a)
    n = writes()
    tick(SAVE_DELAY * 2)
    check(writes() == n, "a project without a name: nothing written")
    check(not any("mode 3" in x and ".mtp" in x for x in fs.log), "no pattern file is written")

    save_as_checks(check, m, fs, tick, "tracklen.bin", get, put, {}, a, {7: lens(t2=3)}, tln1, SAVE_DELAY)


def main() -> int:
    ptf, stock = Path(sys.argv[1]), Path(sys.argv[2])
    site_checks(ptf)
    stock_checks(ptf, stock)
    length_checks(ptf, stock)
    step_checks(ptf, stock)
    mode_checks(ptf, stock)
    roll_checks(ptf, stock)
    song_checks(ptf)
    cost_checks(ptf)
    global TRK_LEN
    TRK_LEN = page3_buttons(ptf)[1]
    button_checks(ptf, stock)
    hold_checks(ptf)
    stock_jog_checks(ptf, stock)
    grid_checks(ptf, stock)
    record_checks(ptf, stock)
    file_checks(ptf)
    print(f"{len(failures)} failure(s)")
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
