"""Check colorsong's Song-screen rescan: power-on, re-entry, project switch, unsaved
edits, and reopening a project after discarding saved pattern edits.

  python tools/emu/test_rescan.py <patched.ptf> <project A dir> <project B dir>

Each scenario sets up RAM the way the firmware has it after loading a project
(playlist and track masks read from project.mt, current pattern in its RAM buffer,
the FS object serving the project folder as Workspace/), then runs the Song screen
the way the firmware does:
  1. entry, as 0x600fe3d8 does: the BL at 0x600c3b3e (pattern_update_usage_current
     -> usage hook) for the current pattern, then the BL at 0x600fe402 (patched:
     session check, then scr_song_set_grid_data, stubbed here),
  2. update ticks through the Song screen vtable slot 0x60042dd8 (patched: read at
     most one pattern file, redraw the grid, then the stock update, stubbed here),
and draws the grid with the stock song_grid_draw. Expected colors come from
fingerprinting every pattern from RAM through the usage hook (the path already
covered by test_colorsong.py).
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakefs import FILEMAN, FakeFS  # noqa: E402
from layout import patch_ram  # noqa: E402
from machine import Machine  # noqa: E402
from song_grid import (MASK_HI, MASK_LO, PATTERN_DATA_SIZE, PLAYLIST, SAVE_PATH_USAGE_CALL,  # noqa: E402
                       SONG_GRID_DRAW, bl_target, build_song_control)

SONG_ENTER_CALL = 0x600FE402        # bl scr_song_set_grid_data in the Song screen's start
SET_GRID_DATA = 0x600FE25C
SONG_UPDATE = 0x600FDD38
SONG_UPDATE_SLOT = 0x60042DD8          # Song screen vtable: update method
DISP_CTRL_REFRESH = 0x60101E84
DISPLAY = 0x20027D64
GRID_CTRL_INDEX = 7                    # value we put at screen+0xad
PROJECT_PATTERN_COPY_CALL = 0x601145CE  # bl file_copy_step in the project-open pattern copy
FILE_COPY_STEP = 0x600C4578
CUR_PATTERN = 0x202046C8
CUR_PATTERN_BUF_HOLDER = 0x20017274 + 0x14
FP_TABLE = 0x20205000                 # colorsong table: set from the firmware's .map.json in main
FP_TABLE_SIZE = 12208
FP_PATTERNS = FP_TABLE + 0x220         # table layout: pat[256] after header + family registry
FP_PATTERN_SIZE = 36                   # mask u16, valid u8, pad u8, key u16[16]
FILE_PLAYLIST, FILE_LO, FILE_HI = 0x10, 0x318, 0x642   # project.mt offsets (lo[p] at +p)


def project_files(project: Path):
    mt = (project / "project.mt").read_bytes()
    playlist = list(mt[FILE_PLAYLIST:FILE_PLAYLIST + 255])
    masks = {p: mt[FILE_LO + p] | mt[FILE_HI + p] << 8 for p in range(1, 256)}
    return playlist, masks


def pattern_bytes(project: Path, p: int) -> bytes:
    f = project / "patterns" / f"pattern_{p:02d}.mtp"
    return f.read_bytes()[:PATTERN_DATA_SIZE] if f.exists() else b""


class Rig:
    def __init__(self, ptf: Path):
        self.m = Machine(ptf)
        self.fs = FakeFS(self.m, Path("."))
        self.grid_data_calls = 0
        self.m.stub_function(SET_GRID_DATA, self._grid_data)
        self.update_calls = 0
        self.refreshes: list[tuple[int, int]] = []
        self.m.stub_function(SONG_UPDATE, self._update)
        self.m.stub_function(DISP_CTRL_REFRESH, self._refresh)
        self.screen = self.m.alloc(0x200)
        self.m.w8(self.screen + 0xAD, GRID_CTRL_INDEX)
        self.update = self.m.r32(SONG_UPDATE_SLOT) & ~1
        self.m.stub_function(FILE_COPY_STEP, self._copy_step)   # copy within the fake card
        self.copy_call = bl_target(self.m, PROJECT_PATTERN_COPY_CALL)
        self.cur_buf = self.m.alloc(PATTERN_DATA_SIZE)
        self.m.w32(CUR_PATTERN_BUF_HOLDER, self.cur_buf)
        self.usage = bl_target(self.m, SAVE_PATH_USAGE_CALL)
        self.enter = bl_target(self.m, SONG_ENTER_CALL)

    def _grid_data(self, m):
        self.grid_data_calls += 1

    def _update(self, m):
        self.update_calls += 1

    def _refresh(self, m):
        self.refreshes.append((m.arg(0), m.arg(1)))

    def _copy_step(self, m):
        src, dst = m.cstr(m.arg(1)), m.cstr(m.arg(2))
        data = self.fs.read_any(src)
        if data is not None:
            self.fs.put(dst, data)
        return 4                                    # "done with this file"

    def save_pattern(self, p: int, data: bytes):
        """Pattern save: file written to Workspace, then the usage update (hooked)."""
        self.fs.put(f"Workspace/patterns/pattern_{p:02d}.mtp", data)
        buf = self.m.alloc(PATTERN_DATA_SIZE)
        self.m.write(buf, data[:PATTERN_DATA_SIZE])
        self.m.call(self.usage, FILEMAN, p, buf)

    def reopen_project(self, project: Path, name: str):
        """Open `project` again: copy its pattern files over Workspace (through the
        firmware's copy call site), then reload playlist/masks/current pattern."""
        src_fmt = str(project / "patterns" / "pattern_{:02d}.mtp")
        for p in range(1, 256):
            src, dst = self.m.alloc(512), self.m.alloc(64)
            self.m.write(src, src_fmt.format(p).encode() + b"\0")
            self.m.write(dst, f"Workspace/patterns/pattern_{p:02d}.mtp".encode() + b"\0")
            self.m.call(self.copy_call, 0x2001F730, src, dst)
        return self.load_project(project, name)

    def power_on(self, seed=1):
        rnd = random.Random(seed)
        self.m.write(FP_TABLE, bytes(rnd.randrange(256) for _ in range(FP_TABLE_SIZE)))

    def load_project(self, project: Path, name: str, current: int | None = None, edit=None):
        """State right after the firmware opened `project` (masks from project.mt)."""
        playlist, masks = project_files(project)
        self.m.write(PLAYLIST, bytes(playlist))
        for p, mask in masks.items():
            self.m.w8(MASK_LO + p, mask & 0xFF)
            self.m.w8(MASK_HI + p, mask >> 8)
        self.fs.set_project(project, name, "Projects/User")      # parent folder, project name
        cur = current or playlist[0]
        data = bytearray(pattern_bytes(project, cur))
        if edit:
            edit(data)
        self.m.w8(CUR_PATTERN, cur)
        self.m.write(self.cur_buf, data)
        self.playlist = [p for p in playlist[:playlist.index(0) if 0 in playlist else 255]]
        return cur

    def song_enter(self, max_ticks=300):
        """Open the Song screen and run update ticks until the rescan is done.
        Returns file loads; per-step stats are kept on the rig."""
        cur = self.m.uc.mem_read(CUR_PATTERN, 1)[0]
        self.m.call(self.usage, FILEMAN, cur, self.cur_buf)    # pattern_update_usage_current
        loads0 = self.loads()
        self.m.call(self.enter, self.screen)                   # patched bl at 0x600fe402
        self.entry_loads = self.loads() - loads0
        self.loads_per_tick = []
        self.ticks = 0
        updates0, refreshes0 = self.update_calls, len(self.refreshes)
        while self.ticks < max_ticks:
            before = self.loads()
            self.m.call(self.update, self.screen)              # Song screen update method
            self.ticks += 1
            self.loads_per_tick.append(self.loads() - before)
            if self.m.r32(FP_TABLE + 0x10) == 0xFF or self.update == SONG_UPDATE:
                break
        self.tick_updates = self.update_calls - updates0
        self.tick_refreshes = self.refreshes[refreshes0:]
        return self.loads() - loads0

    def loads(self) -> int:
        return self.m.r32(FP_TABLE + 0xC)

    def ticks_n(self, n: int):
        """More Song screen updates (lets a pending color save happen)."""
        for _ in range(n):
            self.m.call(self.update, self.screen)

    def keys(self, pattern: int) -> bytes:
        """The stored fingerprint keys of a pattern (table layout)."""
        return bytes(self.m.uc.mem_read(FP_PATTERNS + pattern * FP_PATTERN_SIZE + 4, 32))

    def draw(self) -> list[int]:
        ctrl = build_song_control(self.m, len(self.playlist), 0)
        self.m.display_list.clear()
        self.m.call(SONG_GRID_DRAW, ctrl)
        return [w & 0xFFFFFF for w in self.m.display_list if w >> 24 == 0x04]


def expected_colors(ptf: Path, project: Path, name: str, current=None, edit=None) -> list[int]:
    """Reference: fingerprint every playlist pattern from RAM via the usage hook."""
    r = Rig(ptf)
    cur = r.load_project(project, name, current, edit)
    buf = r.m.alloc(PATTERN_DATA_SIZE)
    for p in sorted(set(r.playlist)):
        data = bytearray(pattern_bytes(project, p))
        if p == cur and edit:
            edit(data)
        r.m.write(buf, data)
        r.m.call(r.usage, FILEMAN, p, buf)
    return r.draw()


def main() -> int:
    global FP_TABLE, FP_PATTERNS
    FP_TABLE = patch_ram(Path(sys.argv[1]), "colorsong")
    FP_PATTERNS = FP_TABLE + 0x220
    ptf, proj_a, proj_b = (Path(a) for a in sys.argv[1:4])
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
        ok &= bool(cond)

    # project.mt really holds the masks the firmware computes from the pattern files.
    r0 = Rig(Path(sys.argv[1]))
    r0.load_project(proj_a, "A")
    playlist, masks = project_files(proj_a)
    buf = r0.m.alloc(PATTERN_DATA_SIZE)
    mismatches = 0
    for p in sorted({q for q in playlist if q}):
        r0.m.write(buf, pattern_bytes(proj_a, p))
        r0.m.w8(MASK_LO + p, 0); r0.m.w8(MASK_HI + p, 0)
        r0.m.call(0x600C3998, 0, p, buf)                       # stock function
        got = r0.m.uc.mem_read(MASK_LO + p, 1)[0] | r0.m.uc.mem_read(MASK_HI + p, 1)[0] << 8
        mismatches += got != masks[p]
    check("project.mt masks == masks computed from pattern files", mismatches == 0, f"{mismatches} differ")

    exp_a = expected_colors(ptf, proj_a, "A")
    exp_b = expected_colors(ptf, proj_b, "B")
    white = 0xFFFFFF

    # 1. Power-on: random table, open project A, enter the Song screen.
    r = Rig(ptf)
    r.power_on()
    r.load_project(proj_a, "A")
    loads = r.song_enter()
    got = r.draw()
    n_unique = len(set(r.playlist))
    check("power-on: colors match reference", got == exp_a)
    check("power-on: no stock-white filled blocks", white not in got)
    check("power-on: pattern files read = unique patterns - current", loads == n_unique - 1,
          f"{loads} reads, {n_unique} unique")
    check("original scr_song_set_grid_data still called", r.grid_data_calls == 1)
    check("Song screen entry does no file I/O", r.entry_loads == 0, f"{r.entry_loads} reads on entry")
    check("at most one pattern file per update", max(r.loads_per_tick) <= 1,
          f"{r.ticks} updates for {loads} files")
    check("stock update runs on every update", r.tick_updates == r.ticks)
    check("grid redrawn after each file read and when the rescan finishes",
          len(r.tick_refreshes) == loads + 1 and all(x == (DISPLAY, GRID_CTRL_INDEX) for x in r.tick_refreshes),
          f"{len(r.tick_refreshes)} redraws for {loads} files")
    check("file reads are at most 256 bytes", max(r.fs.read_sizes) <= 256, f"largest {max(r.fs.read_sizes)}")

    # 2. Re-entering the Song screen reads nothing.
    loads = r.song_enter()
    check("re-entry: no file reads", loads == 0, f"{loads} reads")
    check("re-entry: same colors", r.draw() == exp_a)

    # 3. Switch to project B (same machine, table still holds A).
    r.load_project(proj_b, "B")
    loads = r.song_enter()
    got = r.draw()
    check("project switch: colors match project B", got == exp_b)
    check("project switch: B's patterns were read", loads == len(set(r.playlist)) - 1, f"{loads} reads")

    # 4. Back to A: rebuilt again (session changed), still correct.
    r.load_project(proj_a, "A")
    r.song_enter()
    check("switch back: colors match project A", r.draw() == exp_a)

    # 5. Unsaved edit in the current pattern's RAM buffer wins over the file.
    def edit(d):                                   # track 1: every note uses instrument 5
        for s in range(128):
            st = 0x1C + 1 + s * 6
            if d[st] != 0xFF:
                d[st + 1] = 5
    exp_edit = expected_colors(ptf, proj_a, "A", edit=edit)
    r = Rig(ptf)
    r.power_on(2)
    r.load_project(proj_a, "A", edit=edit)
    r.song_enter()
    got = r.draw()
    check("unsaved edit: colors follow RAM, not the file", got == exp_edit and exp_edit != exp_a)
    # 6. Edit + save a pattern, then reopen the same project without saving it.
    #    The edited pattern is not the current one after reopening, and the edit
    #    keeps the track mask (notes up a semitone), so only the copy hook can tell
    #    that its fingerprint describes discarded content.
    r = Rig(ptf)
    r.power_on(3)
    first = r.load_project(proj_a, "A")
    r.song_enter()
    target = next(p for p in r.playlist if p != first)
    edited = bytearray(pattern_bytes(proj_a, target))
    for t in range(16):
        for s_ in range(128):
            st = 0x1C + t * 0x301 + 1 + s_ * 6
            if edited[st] != 0xFF and edited[st] < 0x7F:
                edited[st] += 1
    mask_before = r.m.uc.mem_read(MASK_LO + target, 1)[0] | r.m.uc.mem_read(MASK_HI + target, 1)[0] << 8
    keys_project = r.keys(target)
    r.m.w8(CUR_PATTERN, target)                 # edit it as the current pattern
    r.m.write(r.cur_buf, bytes(edited))
    r.save_pattern(target, bytes(edited))
    mask_after = r.m.uc.mem_read(MASK_LO + target, 1)[0] | r.m.uc.mem_read(MASK_HI + target, 1)[0] << 8
    r.song_enter()
    keys_edited = r.keys(target)
    # With order-of-appearance colors an edit of a one-of-a-kind block may keep its
    # shade (it is still the same rank), so check the fingerprint itself.
    check("saved edit: fingerprint updated, mask unchanged",
          keys_edited != keys_project and mask_before == mask_after,
          f"pattern {target}, mask {mask_before:#06x}->{mask_after:#06x}")
    r.reopen_project(proj_a, "A")               # discard: same name and path; current = first
    r.song_enter()
    check("reopen without saving: fingerprint back to the project's",
          r.keys(target) == keys_project and r.draw() == exp_a)
    # 7. Sticky colors: edits recolor only the edited blocks.
    r = Rig(ptf)
    r.power_on(4)
    first = r.load_project(proj_a, "A")
    r.song_enter()
    before = r.draw()
    rows = r.playlist[:12]
    data = bytearray(pattern_bytes(proj_a, first))
    mask = r.m.uc.mem_read(MASK_LO + first, 1)[0] | r.m.uc.mem_read(MASK_HI + first, 1)[0] << 8
    used = [t for t in range(16) if mask & (1 << t)]

    def edit_track(d, t, fn):
        for s_ in range(128):
            st = 0x1C + t * 0x301 + 1 + s_ * 6
            if d[st] != 0xFF and d[st] < 0x80:
                fn(d, st)

    def changed_blocks(a, b):
        return {(rows[i // 16], i % 16) for i in range(len(a)) if a[i] != b[i]}

    # 7a. New family: every note of the first slot's first used track switches to
    #     sample 46 (used nowhere else), i.e. an instrument set new to the song.
    t_fam = used[0]
    edit_track(data, t_fam, lambda d, st: d.__setitem__(st + 1, 46))
    r.m.write(r.cur_buf, bytes(data))
    r.save_pattern(first, bytes(data))
    after_fam = r.draw()
    ch = changed_blocks(before, after_fam)
    check("new family: only the edited track's blocks change color",
          ch and all(p == first and t == t_fam for p, t in ch), f"changed {sorted(ch)}")
    edited = [after_fam[i] for i in range(len(after_fam)) if (rows[i // 16], i % 16) == (first, t_fam)]
    others = {after_fam[i] for i in range(len(after_fam)) if (rows[i // 16], i % 16) != (first, t_fam)}
    check("new family gets a color no other block has", edited and edited[0] not in others)

    # 7b. New variant: notes up a semitone on another used track of the same pattern.
    t_var = used[1]
    edit_track(data, t_var, lambda d, st: d.__setitem__(st, d[st] + 1))
    r.m.write(r.cur_buf, bytes(data))
    r.save_pattern(first, bytes(data))
    after_var = r.draw()
    ch = changed_blocks(after_fam, after_var)
    check("new variant: only the edited track's blocks change color",
          ch and all(p == first and t == t_var for p, t in ch), f"changed {sorted(ch)}")
    # 8. Saved colors survive a power cycle.
    def first_track_new_family(d):
        used = 0
        for t in range(16):
            for s_ in range(128):
                st = 0x1C + t * 0x301 + 1 + s_ * 6
                if d[st] != 0xFF and d[st] < 0x80:
                    used |= 1 << t
        t0 = min(t for t in range(16) if used & (1 << t))
        for s_ in range(128):
            st = 0x1C + t0 * 0x301 + 1 + s_ * 6
            if d[st] != 0xFF and d[st] < 0x80:
                d[st + 1] = 46

    color_file = "Projects/User/A/colorsong.bin"
    r1 = Rig(ptf)
    r1.power_on(5)
    first = r1.load_project(proj_a, "A")
    r1.song_enter()
    r1.draw()
    r1.ticks_n(40)
    saved_first = r1.fs.overlay.get(color_file)
    check("first Song visit saves the colors", saved_first is not None and len(saved_first) > 12,
          f"{len(saved_first or b'')} bytes")
    check("color file written in pieces of at most 256 bytes",
          bool(r1.fs.write_sizes) and max(r1.fs.write_sizes) <= 256,
          f"largest {max(r1.fs.write_sizes or [0])}")
    edited = bytearray(pattern_bytes(proj_a, first))
    first_track_new_family(edited)
    r1.m.write(r1.cur_buf, bytes(edited))
    r1.save_pattern(first, bytes(edited))
    session1 = r1.draw()
    r1.ticks_n(40)
    check("an edit updates the saved colors", r1.fs.overlay.get(color_file) not in (None, saved_first))

    def power_cycle(overlay, name="A", seed=6):
        r_ = Rig(ptf)
        r_.power_on(seed)
        r_.fs.overlay = dict(overlay)
        r_.load_project(proj_a, name, edit=first_track_new_family)
        r_.song_enter()
        return r_

    r2 = power_cycle(r1.fs.overlay)
    session2 = r2.draw()
    fresh_overlay = {k: v for k, v in r1.fs.overlay.items() if k != color_file}
    fresh = power_cycle(fresh_overlay).draw()
    check("after a power cycle the colors are restored", session2 == session1)
    check("(control) without the file the colors would differ", fresh != session1)
    opened = r2.fs.writes_opened
    r2.draw()
    r2.ticks_n(40)
    check("no rewrite when nothing changed", r2.fs.writes_opened == opened,
          f"{r2.fs.writes_opened - opened} writes")

    damaged = dict(r1.fs.overlay)
    if color_file in damaged:
        data = bytearray(damaged[color_file])
        data[20] ^= 0xFF
        damaged[color_file] = bytes(data)
        check("damaged color file is ignored", power_cycle(damaged, seed=8).draw() == fresh)
    else:
        check("damaged color file is ignored", False, "no color file to damage")

    r4 = Rig(ptf)
    r4.power_on(7)
    r4.load_project(proj_a, "")
    r4.song_enter()
    r4.draw()
    r4.ticks_n(40)
    check("unsaved project (no name): nothing written", r4.fs.writes_opened == 0)
    # 9. Every edit of a one-of-a-kind block shows: a color released by the edit
    #    isn't handed straight back. Copy a track into an empty one, then change it
    #    step by step, in a fresh session and after a power cycle with saved colors.
    def edit_chain(rig, label):
        rows_ = rig.playlist[:12]
        target = None
        for p_ in dict.fromkeys(rows_):
            m_ = rig.m.uc.mem_read(MASK_LO + p_, 1)[0] | rig.m.uc.mem_read(MASK_HI + p_, 1)[0] << 8
            empty = [t for t in range(16) if not m_ & (1 << t)]
            used_ = [t for t in range(16) if m_ & (1 << t)]
            if empty and used_:
                target = (p_, used_[0], empty[0])
                break
        p_, src, dst = target
        # start from the pattern as it is in Workspace/ now (it may hold earlier edits)
        d = bytearray(rig.fs.read_any(f"Workspace/patterns/pattern_{p_:02d}.mtp")[:PATTERN_DATA_SIZE])

        def notes(fn):
            def f(d_):
                for s_ in range(128):
                    st = 0x1C + dst * 0x301 + 1 + s_ * 6
                    if d_[st] != 0xFF and d_[st] < 0x80:
                        fn(d_, st, s_)
            return f

        def copy_track(d_):
            a, b = 0x1C + src * 0x301, 0x1C + dst * 0x301
            d_[b:b + 0x301] = d_[a:a + 0x301]

        steps = [("copy", copy_track),
                 ("new sample 40", notes(lambda d_, st, s_: d_.__setitem__(st + 1, 40))),
                 ("new sample 41", notes(lambda d_, st, s_: d_.__setitem__(st + 1, 41))),
                 ("FX2 CC A", notes(lambda d_, st, s_: d_.__setitem__(slice(st + 4, st + 6), bytes([8, 64])))),
                 ("FX1 gate", notes(lambda d_, st, s_: d_.__setitem__(slice(st + 2, st + 4), bytes([20, s_ % 90])))),
                 ("all notes C0", notes(lambda d_, st, s_: d_.__setitem__(st, 0)))]
        prev = rig.draw()
        idx = [i for i in range(len(prev)) if (rows_[i // 16], i % 16) == (p_, dst)]
        ok_all, others_ok = True, True
        for name, fn in steps:
            fn(d)
            rig.m.w8(CUR_PATTERN, p_)
            rig.m.write(rig.cur_buf, bytes(d))
            rig.save_pattern(p_, bytes(d))
            rig.song_enter()
            cur = rig.draw()
            if name != "copy" and all(cur[i] == prev[i] for i in idx):
                ok_all = False
                print(f"     {label}: '{name}' left the block's color unchanged")
            others_ok &= all(cur[i] == prev[i] for i in range(len(cur)) if i not in idx)
            prev = cur
        check(f"{label}: every edit of a one-of-a-kind block changes its color", ok_all)
        check(f"{label}: no other block changes", others_ok)

    rf = Rig(ptf)
    rf.power_on(31)
    rf.load_project(proj_a, "A")
    rf.song_enter()
    edit_chain(rf, "fresh session")
    rr = Rig(ptf)
    rr.power_on(32)
    rr.fs.overlay = dict(r1.fs.overlay)
    rr.load_project(proj_a, "A", edit=first_track_new_family)
    rr.song_enter()
    edit_chain(rr, "after power cycle")
    print(f"  fs log sample: {r.fs.log[:2]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
