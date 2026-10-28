"""Check the copypattern patch on the patched firmware: the button and the copy.

  python tools/emu/test_copypattern.py <patched.ptf>

Works with the copypattern-only build and with bundles that include it (with humanize,
its Humanize button must still be there).

1. Button: runs the stock page-3 binding (0x600e29a4) with a fake button manager.
   Copy to New takes button 2, or button 3 when humanize is in the build (humanize
   keeps button 2). In record mode that button must be bound to a handler in the
   patch, outside record mode it stays unbound; buttons 0, 1 and 7 keep their stock
   handlers, and the other unused buttons stay unbound.
2. Labels: runs the stock page-3 label code (0x600e584c) with the display calls
   recorded. Its button must read "Copy to" / "New", get Pattern Name's data, and the
   active color set in record mode or the greyed-out one outside it. The other labels
   are unchanged (button 2 "Humanize" with humanize).
3. The copy, with the static constructors run, a fake SD card, the stock pattern
   writer and usage update, and a stand-in for the CRC object the firmware sets up at
   boot. Each scenario presses the button (the bound handler), then calls the Pattern
   screen's update slot until the message bar reports a result:
     - stopped, a 128-step pattern with other lengths on other tracks and unsaved
       edits: the file written equals what the stock writer writes for the play
       buffer (not the old file), the stock loader reads it back identical, the Song
       screen mask is the stock one, the unsaved-changes flags are set, the target's
       changed flag is not, and the current pattern, both buffers and the song
       position are unchanged;
     - lengths 16, 32, 64 and 128, one after the other into the next empty patterns;
     - patterns skipped: a mask, a note past the pattern length, an instrument alone,
       FX1 or FX2 type alone, an older file version, a short file, in the playlist,
       queued; an empty file counts as empty, and so does a missing one; wrap-around
       past 255;
     - no empty pattern left; the current pattern changing before the write (by hand,
       and by song mode's own buffer swap while playing); the file manager busy
       (waits, then gives up); a write error (the half-written file is removed);
     - playing in song mode, the sequencer ticking between updates: copied, nothing
       in the sequencer or the song changed, and playback goes on;
     - the file rules: nothing on the press, at most one pattern file per update,
       reads of at most 256 bytes, the stock update on every call, a second press
       during a copy ignored.
"""
from __future__ import annotations

import random
import struct
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seq_timing  # noqa: E402
from fakefs import FakeFS  # noqa: E402
from layout import patch_code  # noqa: E402
from machine import Machine  # noqa: E402

SEQ = 0x20017274
SEQ_PLAY, SEQ_PRELOAD = SEQ + 0x14, SEQ + 0x18
SEQ_SONG_MODE, SEQ_QUEUED, SEQ_QUEUE_ACTIVE, SEQ_PLAYING = SEQ + 0x1C, SEQ + 0x26, SEQ + 0x28, SEQ + 0x2A
SEQ_TICK, SEQ_PLAY_FROM = 0x35E8, 0x1250
FM_REQUEST_PATTERN_LOAD = 0x600BC938
SETTINGS = 0x2002061C
PROJECT = 0x20204000
PLAYLIST = PROJECT + 0x5C2
SONG_POS = PROJECT + 0x6C1
CUR_PATTERN = PROJECT + 0x6C8
PROJECT_UNSAVED = PROJECT + 0x6F4
MASK_LO, MASK_HI = PROJECT + 0x8CC, PROJECT + 0xBF6
FM = 0x2001E488
FM_STATE, FM_SUBSTATE = FM + 0x13D, FM + 0x13F
FM_UNSAVED = FM + 0x469
FM_CHANGED = FM + 0x4CA             # + pattern number
FM_CRC = FM + 0x1104
PATVIEW = 0x20021770
ED = PATVIEW + 0x1000
ED_RECORD = 0x254
DISPLAY = 0x20027D64
STYLE_ACTIVE, STYLE_GREY = 0x44, 0x54
BUTTON_MANAGER_PTR = 0x200209E0
PAGE3_BIND, PAGE3_LABELS = 0x600E29A4, 0x600E584C
STOCK_HANDLERS = {0: 0x600DFE65, 1: 0x600DFDD1, 7: 0x600E2A45}
PATTERN_UPDATE_SLOT, PATTERN_UPDATE = 0x600403DC, 0x600E1F98
WRITE_PATTERN = 0x600C1FB4
READ_PATTERN = 0x600C2350
SHOW_MESSAGE, MESSAGE_BAR = 0x600CDF28, 0x20020880
DISPLAY_CALLS = {0x60101D5C: "text", 0x60101D74: "text2", 0x60101DA4: "colors",
                 0x60101DF8: "data", 0x60101D8C: "value", 0x60101CAC: "style",
                 0x60101CD0: "style_fields", 0x60101E84: "refresh", 0x60102014: "sync"}

SIZE = 0x3030
DATA = 0x302C                       # then the CRC
TRACK = 0x301
HEADER = struct.pack("<IIIH", 0x0002534B, 0x01020901, 0x05050505, SIZE).ljust(0x1C, b"\0")
EMPTY = 0xFF
BUSY_LIMIT = 2000
PLAIN_FX = (2, 18)                  # Micro-move, Volume: nothing that ends or jumps a pattern

CODE = (0, 0)                       # set in main(): the patch's code, start and end
SLOT = 2                            # set in main(): its button, 2 alone or 3 with humanize
HZ_CODE = None                      # humanize's code, when it is in the build
TL_CODE = None                      # tracklen's (Trk Len on the button after this one), when it is in the build
failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def path_of(n: int) -> str:
    return f"Workspace/patterns/pattern_{n:02d}.mtp"


def new_pattern(lengths) -> bytearray:
    """An empty pattern; `lengths` is one length for every track or 16 of them."""
    if isinstance(lengths, int):
        lengths = [lengths] * 16
    d = bytearray(SIZE)
    d[:0x1C] = HEADER
    for t in range(16):
        base = 0x1C + TRACK * t
        d[base] = lengths[t] - 1
        for s in range(128):
            d[base + 1 + 6 * s] = EMPTY
    return d


def put(d: bytearray, t: int, s: int, note: int, ins: int = 0, fx1=(0, 0), fx2=(0, 0)) -> None:
    o = 0x1D + TRACK * t + 6 * s
    d[o:o + 6] = bytes([note & 0xFF, ins, fx1[0], fx1[1], fx2[0], fx2[1]])


def busy_pattern(lengths, seed: int, instruments=range(0, 64), fx_types=range(1, 40)) -> bytearray:
    """A pattern with content on every track, also past each track's length."""
    rnd = random.Random(seed)
    d = new_pattern(lengths)
    fx_types = list(fx_types)
    for t in range(16):
        for s in range(0, 128, rnd.choice((1, 2, 3, 5))):
            fx1 = (rnd.choice(fx_types), rnd.randrange(0, 101)) if rnd.random() < 0.3 else (0, rnd.randrange(0, 50))
            fx2 = (rnd.choice(fx_types), rnd.randrange(0, 101)) if rnd.random() < 0.2 else (0, 0)
            put(d, t, s, rnd.randrange(0, 120), rnd.choice(list(instruments)), fx1, fx2)
    return d


def stock_mask(d: bytes) -> int:
    """pattern_update_track_usage's rule: a track is used if a step up to the pattern
    length (track 0's) has a note, an FX type, or an instrument above 47."""
    last = d[0x1C]
    mask = 0
    for t in range(16):
        for s in range(last + 1):
            o = 0x1D + TRACK * t + 6 * s
            if d[o] != EMPTY or d[o + 2] or d[o + 4] or d[o + 1] > 47:
                mask |= 1 << t
                break
    return mask


class CardFS(FakeFS):
    """FakeFS with one shared stub for the methods nobody should call (the emulator has
    few stub slots, and the sequencer's fake MIDI ports take most of them), and a log
    of every exists/open/remove with the update it happened in."""

    def __init__(self, m, workspace: Path):
        self.m = m
        self.workspace = Path(workspace)
        self.files, self.next_handle, self.log = {}, 0x100, []
        self.overlay, self.read_sizes, self.write_sizes = {}, [], []
        self.writes_opened, self.removed, self.paths = 0, [], {}
        self.events: list[tuple[str, str]] = []
        self.fail_writes = False
        self.obj = m.alloc(0x10)
        vt = m.alloc(0x80)
        bad = m.add_stub(self._bad)
        for off in range(0, 0x80, 4):
            m.w32(vt + off, bad)
        for off, f in ((0x08, self._exists), (0x14, self._remove), (0x2C, self._open),
                       (0x30, self._read), (0x34, self._write), (0x40, self._close)):
            m.w32(vt + off, m.add_stub(f))
        m.w32(self.obj, vt)
        m.w32(0x2001E488, self.obj)

    def _bad(self, m):
        raise RuntimeError("unexpected FS method called")

    def _exists(self, m):
        self.events.append(("exists", m.cstr(m.arg(1))))
        return super()._exists(m)

    def _remove(self, m):
        self.events.append(("remove", m.cstr(m.arg(1))))
        return super()._remove(m)

    def _open(self, m):
        self.events.append(("open", m.cstr(m.arg(2))))
        return super()._open(m)

    def _write(self, m):
        if self.fail_writes:
            self.write_sizes.append(m.arg(2))
            return 0
        return super()._write(m)


class Rig:
    """The firmware with the sequencer's static constructors run, a fake card, a CRC
    stand-in, the message bar and the stock Pattern screen update recorded."""

    def __init__(self, ptf: Path, workspace: Path):
        self.m = m = seq_timing.make(ptf)
        self.fs = CardFS(m, workspace)
        crc_obj, crc_vt = m.alloc(8), m.alloc(8)
        m.w32(crc_obj, crc_vt)
        m.w32(crc_vt, m.add_stub(lambda mm: zlib.crc32(bytes(mm.uc.mem_read(mm.arg(1), mm.arg(2))))))
        m.w32(FM_CRC, crc_obj)
        self.messages: list[str] = []

        def message(mm):
            ok = mm.arg(0) == MESSAGE_BAR
            self.messages.append(mm.cstr(mm.arg(2)) if ok else f"<bar {mm.arg(0):#x}>")
        m.stub_function(SHOW_MESSAGE, message)
        self.stock_updates = self.hook_calls = 0

        def update(mm):
            self.stock_updates += 1
        m.stub_function(PATTERN_UPDATE, update)
        m.stub_function(FM_REQUEST_PATTERN_LOAD, self._request_load)
        self.hook = m.r32(PATTERN_UPDATE_SLOT)
        self.mgr = m.alloc(0x40)
        self.entries = m.alloc(8 * 0x14)
        m.w8(self.mgr + 5, 8)
        m.w32(self.mgr + 0x1C, self.entries)
        m.w32(BUTTON_MANAGER_PTR, self.mgr)
        m.write(FM + 0x340, b"\0")              # no project name: other patches' settings stay quiet
        m.w8(ED + ED_RECORD, 1)
        m.call(PAGE3_BIND, 0)
        self.button = m.r32(self.entries + 0x14 * SLOT + 4)
        self.play = m.alloc(SIZE)
        self.preload = m.alloc(SIZE)
        self.scratch = m.alloc(SIZE)
        self.path_buf = m.alloc(64)

    @staticmethod
    def _request_load(mm):
        """Song mode asks the main loop for the next pattern: stands in for the load
        having happened (fm+0x130 = the loaded pattern, which the buffer swap makes
        current). The preload buffer already holds the next pattern's data."""
        mm.w8(FM + 0x130, mm.arg(1))
        return 1

    def reset(self, current: int = 1) -> None:
        """A stopped project on pattern `current`, nothing used, an empty card."""
        m = self.m
        self.fs.overlay.clear()
        m.write(PROJECT + 0x5C2, bytes(255))
        m.write(MASK_LO, bytes(256))
        m.write(MASK_HI, bytes(256))
        m.write(FM_CHANGED, bytes(256))
        m.w8(FM_UNSAVED, 0)
        m.w8(PROJECT_UNSAVED, 0)
        m.w8(CUR_PATTERN, current)
        m.w8(SONG_POS, 0)
        m.w8(SEQ_SONG_MODE, 0)
        m.w8(SEQ_QUEUE_ACTIVE, 0)
        m.w16(SEQ_QUEUED, 0)
        m.w8(SEQ_PLAYING, 0)
        m.w8(FM_STATE, 0)
        m.w8(FM_SUBSTATE, 0)
        m.w32(SEQ_PLAY, self.play)
        m.w32(SEQ_PRELOAD, self.preload)
        m.write(self.play, new_pattern(32))
        m.write(self.preload, new_pattern(32))
        self.fs.fail_writes = False

    def used(self, n: int, mask: int = 1) -> None:
        self.m.w8(MASK_LO + n, mask & 0xFF)
        self.m.w8(MASK_HI + n, mask >> 8)

    def mask(self, n: int) -> int:
        return self.m.uc.mem_read(MASK_LO + n, 1)[0] | self.m.uc.mem_read(MASK_HI + n, 1)[0] << 8

    def buffer(self, a: int) -> bytes:
        return bytes(self.m.uc.mem_read(a, SIZE))

    def press(self) -> list:
        n = len(self.fs.events)
        self.m.call(self.button)
        return self.fs.events[n:]

    def update(self, between=None) -> list:
        n = len(self.fs.events)
        self.hook_calls += 1
        self.m.call(self.hook, PATVIEW, max_insns=20_000_000)
        if between:
            between()
        return self.fs.events[n:]

    def copy(self, limit: int = 300, between=None) -> tuple[str | None, int, list]:
        """Press, then update until a message; (message, updates, per-update events)."""
        self.messages.clear()
        self.fs.read_sizes.clear()
        pressed = self.press()
        per_update = []
        for i in range(limit):
            per_update.append(self.update(between))
            if self.messages:
                break
        return (self.messages[0] if self.messages else None), len(per_update), [pressed] + per_update

    def stock_write(self, data: bytes) -> bytes:
        """What the stock pattern writer puts on the card for `data`."""
        self.m.write(self.scratch, data)
        self.m.write(self.path_buf, b"Workspace/reference.mtp\0")
        self.m.call(WRITE_PATTERN, FM, self.path_buf, self.scratch)
        return self.fs.overlay.pop("Workspace/reference.mtp")

    def stock_read(self, n: int) -> bytes:
        self.m.write(self.path_buf, path_of(n).encode() + b"\0")
        self.m.call(READ_PATTERN, FM, self.path_buf, self.scratch)
        return self.buffer(self.scratch)


def file_rules(events: list, label: str) -> None:
    pressed, updates = events[0], events[1:]
    touched = [{p for _, p in ev if p.startswith("Workspace/patterns/")} for ev in updates]
    check(not pressed, f"{label}: no file I/O on the press")
    check(all(len(t) <= 1 for t in touched), f"{label}: at most one pattern file per update")


def button_checks(ptf: Path) -> None:
    others = [b for b in range(2, 7) if b != SLOT and not (HZ_CODE and b == 2) and not (TL_CODE and b == SLOT + 1)]
    print(f"button binding (Pattern screen, page 3): Copy to New expected on button {SLOT}")
    m = Machine(ptf)
    mgr, entries = m.alloc(0x40), m.alloc(8 * 0x14)
    m.w8(mgr + 5, 8)
    m.w32(mgr + 0x1C, entries)
    m.w32(BUTTON_MANAGER_PTR, mgr)

    def entry(i):
        return m.r32(entries + 0x14 * i) & 0xFF, m.r32(entries + 0x14 * i + 4)

    m.w8(ED + ED_RECORD, 0)
    m.call(PAGE3_BIND, 0)
    quiet = [b for b in (2, 3) if not (TL_CODE and b == SLOT + 1)]     # Trk Len is bound in both modes
    check(all(entry(b)[0] == 0 for b in quiet), f"outside record mode: buttons {quiet} stay unbound")
    m.w8(ED + ED_RECORD, 1)
    m.call(PAGE3_BIND, 0)
    kind, handler = entry(SLOT)
    check(kind == 1 and CODE[0] <= handler < CODE[1] and handler & 1,
          f"record mode: button {SLOT} bound to a patch handler ({kind}, {handler:#x})")
    for i, h in STOCK_HANDLERS.items():
        check(entry(i) == (1, h), f"button {i} keeps its stock handler {h:#x}")
    if HZ_CODE:
        kind2, h2 = entry(2)
        check(kind2 == 1 and HZ_CODE[0] <= h2 < HZ_CODE[1], f"button 2 is still humanize's ({h2:#x})")
    check(all(entry(i)[0] == 0 for i in others), f"buttons {others} stay unbound")

    print("button labels")
    calls: list[tuple[str, int, int]] = []
    for addr, name in DISPLAY_CALLS.items():
        m.stub_function(addr, lambda mm, n=name: calls.append((n, mm.arg(1), mm.arg(2))))
    for i, c in enumerate(range(20, 28)):
        m.w8(ED + 0x292 + i, c)
    styles = m.r32(DISPLAY + 8)
    ours = 20 + SLOT
    for rec in (True, False):
        m.w8(ED + ED_RECORD, 1 if rec else 0)
        calls.clear()
        m.call(PAGE3_LABELS, PATVIEW)
        text = {c: m.cstr(p) for n, c, p in calls if n == "text"}       # the last call per button
        text2 = {c: m.cstr(p) for n, c, p in calls if n == "text2"}
        colors: dict[int, list[int]] = {}
        for n, c, p in calls:
            if n == "colors":
                colors.setdefault(c, []).append(p)
        data = {c: p for n, c, p in calls if n == "data"}
        mode = "record mode" if rec else "outside record mode"
        check(text.get(ours) == "Copy to" and text2.get(ours) == "New",
              f"{mode}: button {SLOT} reads {text.get(ours)!r} / {text2.get(ours)!r}")
        check(text.get(20) == "Pattern" and text.get(21) == "Track" and text.get(27) == "More"
              and text2.get(20) == "Name" and text2.get(21) == "Name"
              and (not HZ_CODE or text.get(22) == "Humanize")
              and all(text.get(20 + b) == "" and text2.get(20 + b) == "" for b in others),
              f"{mode}: the other labels are unchanged" + (", Humanize on button 2" if HZ_CODE else ""))
        want = styles + (STYLE_ACTIVE if rec else STYLE_GREY)
        check(colors.get(ours) == [want], f"{mode}: button {SLOT} colors {'active' if rec else 'greyed out'}")
        check(colors.get(20) == [styles + STYLE_ACTIVE] and colors.get(27) == [styles + 0x64]
              and all(20 + b not in colors for b in others),
              f"{mode}: Pattern Name and More keep their colors, the unused buttons get none")
        check(ours in data and data[ours] == data.get(20), f"{mode}: button {SLOT} gets Pattern Name's data")


def copy_checks(ptf: Path) -> None:
    ws = Path(tempfile.mkdtemp(prefix="copypattern_ws_"))
    r = Rig(ptf, ws)
    m = r.m
    check(CODE[0] <= r.button < CODE[1], f"the rig found the patch's button handler ({r.button:#x})")

    print("copy while stopped: 128 steps, other lengths on other tracks, unsaved edits")
    r.reset(current=1)
    lengths = [128, 16, 32, 64, 128, 1, 100, 128, 7, 128, 64, 32, 16, 128, 48, 128]
    src = busy_pattern(lengths, seed=1)
    m.write(r.play, src)
    old = busy_pattern(lengths, seed=99)                    # what the card has: older
    r.fs.put(path_of(1), r.stock_write(old))
    r.used(1, 0xFFFF)
    r.used(2)                                               # used by its mask
    third = new_pattern(32)
    put(third, 0, 5, 60, 3)
    r.fs.put(path_of(3), r.stock_write(third))
    r.used(3)
    preload_before = r.buffer(r.preload)
    m.w8(SONG_POS, 0)
    msg, n, events = r.copy()
    reads = max(r.fs.read_sizes or [0])
    check(msg == "Copied to pattern 4", f"message {msg!r} after {n} updates")
    want = r.stock_write(src)
    got = r.fs.overlay.get(path_of(4))
    check(got == want, "pattern_04.mtp is what the stock writer writes for the play buffer")
    check(got is not None and got[:DATA] == bytes(src[:DATA]), "its data is the play buffer's, unsaved edits included")
    check(got is not None and got[:DATA] != r.fs.overlay[path_of(1)][:DATA], "not the source's older file")
    back = r.stock_read(4)
    check(back[:DATA] == bytes(src[:DATA]), "the stock loader reads it back identical")
    check(all(back[0x1C + TRACK * t] == lengths[t] - 1 for t in range(16)), "every track's length kept")
    check(r.mask(4) == stock_mask(src) and r.mask(4) != 0, f"Song screen mask {r.mask(4):#06x} (stock rule)")
    check(m.uc.mem_read(FM_UNSAVED, 1)[0] == 1 and m.uc.mem_read(PROJECT_UNSAVED, 1)[0] & 1,
          "project marked as having unsaved changes")
    check(m.uc.mem_read(FM_CHANGED + 4, 1)[0] == 0 and m.uc.mem_read(FM_CHANGED + 1, 1)[0] == 0,
          "no pattern changed flag set (pattern 4's, pattern 1's)")
    check(m.uc.mem_read(CUR_PATTERN, 1)[0] == 1 and m.r32(SEQ_PLAY) == r.play and m.r32(SEQ_PRELOAD) == r.preload,
          "still on pattern 1, buffers in place")
    check(r.buffer(r.play)[:DATA] == bytes(src[:DATA]) and r.buffer(r.preload) == preload_before,
          "play buffer data and preload buffer unchanged")
    check(sorted(p for p in r.fs.overlay if p.startswith("Workspace/patterns/")) ==
          [path_of(1), path_of(3), path_of(4)], "no other pattern file written")
    file_rules(events, "stopped")
    check(reads <= 256, f"reads of at most 256 bytes ({reads})")

    print("lengths 16, 32, 64, 128 into the next empty patterns")
    r.reset(current=5)
    r.used(5, 0xFFFF)
    for i, length in enumerate((16, 32, 64, 128)):
        src = busy_pattern(length, seed=10 + i)
        m.write(r.play, src)
        msg, n, events = r.copy()
        target = 6 + i
        got = r.fs.overlay.get(path_of(target))
        check(msg == f"Copied to pattern {target}" and got == r.stock_write(src)
              and all(got[0x1C + TRACK * t] == length - 1 for t in range(16)),
              f"{length} steps: {msg!r}, file and lengths right")
        file_rules(events, f"{length} steps")

    print("used patterns are skipped")
    r.reset(current=1)
    r.used(2)                                               # mask
    p = new_pattern(16)
    put(p, 4, 100, 50)                                      # a note past the length
    r.fs.put(path_of(3), r.stock_write(p))
    p = new_pattern(32)
    put(p, 15, 127, EMPTY, ins=5)                           # an instrument alone
    r.fs.put(path_of(4), r.stock_write(p))
    p = new_pattern(32)
    put(p, 0, 0, EMPTY, fx1=(18, 0))                        # FX1 type alone
    r.fs.put(path_of(5), r.stock_write(p))
    p = new_pattern(32)
    put(p, 7, 64, EMPTY, fx2=(2, 0))                        # FX2 type alone
    r.fs.put(path_of(6), r.stock_write(p))
    p = bytearray(r.stock_write(new_pattern(32)))
    p[8] = 4                                                # an older file version
    r.fs.put(path_of(7), bytes(p))
    r.fs.put(path_of(8), r.stock_write(new_pattern(32))[:0x2000])   # a short file
    m.write(PLAYLIST, bytes([1, 9, 1, 0]))                  # 9 is in the song
    m.w16(SEQ_QUEUED, 10)
    m.w8(SEQ_QUEUE_ACTIVE, 1)                               # 10 is queued
    empty = new_pattern(64)
    put(empty, 3, 3, EMPTY, ins=0, fx1=(0, 77))             # an FX value without a type
    r.fs.put(path_of(11), r.stock_write(empty))             # an empty file: a target
    src = busy_pattern(32, seed=3)
    m.write(r.play, src)
    msg, n, events = r.copy()
    check(msg == "Copied to pattern 11", f"mask, note past the length, instrument, FX1, FX2, old version, "
          f"short file, playlist and queue skipped: {msg!r} after {n} updates")
    check(r.fs.overlay.get(path_of(11)) == r.stock_write(src), "the empty file is replaced by the copy")
    check(all(r.fs.overlay.get(path_of(k)) is not None for k in range(3, 9)) and path_of(9) not in r.fs.overlay
          and path_of(10) not in r.fs.overlay, "the skipped patterns are untouched")
    file_rules(events, "skipping")
    check(max(r.fs.read_sizes or [0]) <= 256, "reads of at most 256 bytes")
    check(n == 7, f"one card lookup per update ({n} updates for 6 files and a target)")
    m.w8(SEQ_QUEUE_ACTIVE, 0)
    msg, n, events = r.copy()
    check(msg == "Copied to pattern 10", f"a pattern no longer queued is used next: {msg!r}")

    print("wrap-around past 255")
    r.reset(current=250)
    for k in list(range(250, 256)) + [1]:
        r.used(k)
    m.write(r.play, busy_pattern(64, seed=4))
    msg, n, events = r.copy()
    check(msg == "Copied to pattern 2" and path_of(2) in r.fs.overlay, f"from 250: {msg!r}")
    r.fs.overlay.pop(path_of(2))
    r.used(2, 0)
    r.reset(current=255)
    m.write(r.play, busy_pattern(64, seed=4))
    msg, n, events = r.copy()
    check(msg == "Copied to pattern 1" and path_of(1) in r.fs.overlay, f"from 255: {msg!r}")
    check(path_of(255) not in r.fs.overlay, "pattern_255.mtp untouched (the source)")

    print("no empty pattern left")
    r.reset(current=7)
    for k in range(1, 256):
        r.used(k)
    msg, n, events = r.copy()
    check(msg == "No empty pattern left" and not any(p.startswith("Workspace/patterns/") for p in r.fs.overlay),
          f"{msg!r}, nothing written ({n} updates)")
    r.reset(current=7)
    for k in range(1, 256):
        if k != 7:
            r.fs.put(path_of(k), r.stock_write(busy_pattern(16, seed=k)))
    before = dict(r.fs.overlay)
    msg, n, events = r.copy()
    check(msg == "No empty pattern left" and r.fs.overlay == before and n == 255,
          f"all 254 others hold notes in their files: {msg!r} after {n} updates, nothing written")
    file_rules(events, "no empty pattern")

    print("the current pattern changes during the search")
    r.reset(current=1)
    used = r.stock_write(busy_pattern(16, seed=20))
    r.fs.put(path_of(2), used)                              # one update to look at
    r.messages.clear()
    r.press()
    r.update()
    m.w8(CUR_PATTERN, 4)                                    # switched by hand
    r.update()
    check(r.messages == ["Pattern changed - try again"] and path_of(3) not in r.fs.overlay,
          f"dropped: {r.messages}")

    print("another project opened during the search")
    r.reset(current=1)
    r.fs.put(path_of(2), used)
    m.write(FM + 0x340, b"A\0")
    r.messages.clear()
    r.press()
    r.update()
    m.write(FM + 0x340, b"B\0")                            # another project, also on pattern 1
    ev = r.update() + r.update()
    ev = [e for e in ev if e[1].startswith("Workspace/patterns/")]
    check(not r.messages and not ev and path_of(3) not in r.fs.overlay,
          "dropped without a message, nothing read or written")
    msg, n, _ = r.copy()
    check(msg == "Copied to pattern 3", f"a new press works: {msg!r}")
    m.write(FM + 0x340, b"\0")

    print("file manager busy")
    r.reset(current=1)
    m.w8(FM_STATE, 0x0E)                                    # loading a pattern
    r.messages.clear()
    r.press()
    events = [r.update() for _ in range(20)]
    check(not any(events) and not r.messages, "nothing happens while the file manager is busy")
    m.w8(FM_STATE, 0x0C)
    m.w8(FM_SUBSTATE, 2)
    msg, n, _ = r.copy()                                    # the press is ignored: one copy
    check(msg == "Copied to pattern 2" and n == 1, f"goes on once it is idle again: {msg!r} after {n} updates")
    r.reset(current=1)
    m.w8(FM_SUBSTATE, 0x0E)
    msg, n, _ = r.copy(limit=BUSY_LIMIT + 5)
    check(msg == "Busy - try again" and n == BUSY_LIMIT and path_of(2) not in r.fs.overlay,
          f"gives up after {n} updates: {msg!r}")

    print("write error")
    r.reset(current=1)
    r.fs.fail_writes = True
    msg, n, _ = r.copy()
    check(msg == "Copy failed - SD card" and path_of(2) not in r.fs.overlay and r.mask(2) == 0
          and m.uc.mem_read(FM_UNSAVED, 1)[0] == 0, f"{msg!r}: no half-written file, nothing marked")

    print("a second press during a copy")
    r.reset(current=1)
    r.messages.clear()
    r.press()
    ev = r.press()
    r.update()
    r.update()
    r.update()
    check(not ev and r.messages == ["Copied to pattern 2"] and path_of(3) not in r.fs.overlay,
          f"ignored: one copy ({r.messages})")

    print("copy while playing in song mode")
    r.reset(current=1)
    m.w8(SETTINGS + 0x2C, 2)                                # MIDI out: jack
    a = busy_pattern(128, seed=5, instruments=range(48, 64), fx_types=PLAIN_FX)
    b = busy_pattern(128, seed=6, instruments=range(48, 64), fx_types=PLAIN_FX)
    m.write(r.play, a)
    m.write(r.preload, b)
    m.write(PLAYLIST, bytes([1, 2, 0]))
    r.used(1, 0xFFFF)
    r.used(2, 0xFFFF)
    r.fs.put(path_of(3), used)                              # one update to look at, then playback
    m.call(SEQ_PLAY_FROM, SEQ, 0)
    m.w8(SEQ_SONG_MODE, 1)
    m.events.clear()

    def ticks(k=24):
        def run():
            for _ in range(k):
                m.ticks += 1
                m.call(SEQ_TICK, SEQ, max_insns=2_000_000)
        return run
    ticks(576 * 3)()
    state = (m.r32(SEQ_PLAY), m.r32(SEQ_PRELOAD), m.uc.mem_read(SONG_POS, 1)[0], m.uc.mem_read(CUR_PATTERN, 1)[0],
             m.uc.mem_read(SEQ_SONG_MODE, 1)[0], m.uc.mem_read(SEQ_PLAYING, 1)[0])
    notes_before = len(m.events)
    msg, n, events = r.copy(between=ticks())
    check(msg == "Copied to pattern 4" and n == 2, f"{msg!r} after {n} updates, the sequencer running between")
    check(r.fs.overlay.get(path_of(4)) == r.stock_write(a), "pattern_04.mtp is the playing pattern")
    after = (m.r32(SEQ_PLAY), m.r32(SEQ_PRELOAD), m.uc.mem_read(SONG_POS, 1)[0], m.uc.mem_read(CUR_PATTERN, 1)[0],
             m.uc.mem_read(SEQ_SONG_MODE, 1)[0], m.uc.mem_read(SEQ_PLAYING, 1)[0])
    check(after == state and state[5], f"still playing pattern 1 in song mode, same buffers and position {after}")
    check(r.buffer(r.play)[:DATA] == bytes(a[:DATA]) and r.buffer(r.preload)[:DATA] == bytes(b[:DATA]),
          "play and preload buffers unchanged")
    ticks(576)()
    check(len(m.events) > notes_before, f"playback goes on ({len(m.events) - notes_before} MIDI messages)")
    file_rules(events, "playing")

    print("song mode moves to the next pattern during the search")
    r.reset(current=1)
    a = busy_pattern(4, seed=7, instruments=range(48, 64), fx_types=PLAIN_FX)
    b = busy_pattern(4, seed=8, instruments=range(48, 64), fx_types=PLAIN_FX)
    m.write(r.play, a)
    m.write(r.preload, b)
    m.write(PLAYLIST, bytes([1, 2, 0]))
    r.used(1, 0xFFFF)
    r.used(2, 0xFFFF)
    r.fs.put(path_of(3), used)                              # one update to look at
    m.call(SEQ_PLAY_FROM, SEQ, 0)
    m.w8(SEQ_SONG_MODE, 1)
    ticks(576 * 3 + 300)()
    r.messages.clear()
    r.press()
    r.update(ticks(576))                                   # pattern 3 looked at, then past the end
    swapped = m.r32(SEQ_PLAY) == r.preload
    r.update()
    check(swapped and r.messages == ["Pattern changed - try again"] and path_of(4) not in r.fs.overlay,
          f"buffers swapped ({swapped}): dropped, nothing written ({r.messages})")

    check(r.stock_updates == r.hook_calls, f"the stock update ran on every call ({r.stock_updates})")


def main() -> int:
    global CODE, HZ_CODE, TL_CODE, SLOT
    ptf = Path(sys.argv[1])
    CODE = patch_code(ptf, "copypattern")
    try:
        HZ_CODE = patch_code(ptf, "humanize")
    except SystemExit:
        HZ_CODE = None
    try:
        TL_CODE = patch_code(ptf, "tracklen")
    except SystemExit:
        TL_CODE = None
    SLOT = 3 if HZ_CODE else 2
    print(f"copypattern at {CODE[0]:#x}..{CODE[1]:#x}" + (", with humanize" if HZ_CODE else ""))
    button_checks(ptf)
    copy_checks(ptf)
    print(f"\n{len(failures)} failure(s)")
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
