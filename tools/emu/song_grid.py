"""Render the Song screen's track grid from a real project, using firmware code.

  python tools/emu/song_grid.py <stock.ptf> <project dir> <out.png> [--scroll N] [--dl out.txt]

Steps, all with the stock 1.2.0 functions running in Unicorn:
1. Load the project's playlist (project.mt offset 0x10, 255 bytes) into the RAM
   project struct at 0x20204000 (+0x5c2), like the firmware does.
2. For every pattern the playlist uses, put the .mtp data in RAM and call the
   function the firmware's save path calls (BL at 0x600c3b3e): the stock
   pattern_update_track_usage, or a patch's wrapper around it.
3. Build the song control and its grid data the way scr_song_set_grid_data
   (0x600fe25c) does, and call song_grid_draw (0x60108eac).
4. Render the captured EVE display list to a PNG.

Colors come from the firmware's default style table (flash 0x6005a4c0, the value
static constructors put in display+8); the song control uses the set at +0x31c.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eve import Renderer, disasm  # noqa: E402
from machine import Machine  # noqa: E402

PATTERN_UPDATE_TRACK_USAGE = 0x600C3998
SAVE_PATH_USAGE_CALL = 0x600C3B3E      # bl pattern_update_track_usage in the save path
SONG_GRID_DRAW = 0x60108EAC

PROJECT = 0x20204000
PLAYLIST = PROJECT + 0x5C2          # 255 x u8, 1-based pattern numbers, 0 = end
MASK_LO = PROJECT + 0x8CC           # lo[p] at MASK_LO + p (p = 1..255)
MASK_HI = PROJECT + 0xBF6           # hi[p] at MASK_HI + p
PATTERN_DATA_SIZE = 0x302C

# Song control geometry from scr_song_create_controls (0x600ff4dc).
CTRL_X, CTRL_Y, CTRL_W, CTRL_H = 1, 29, 697, 394

# Default style table (set as display+8 by the static constructors) and the song
# control's color set inside it: +0x04 filled, +0x1c filled (flagged track),
# +0x20 empty, +0x14 selection box.
STYLE_DEFAULT = 0x6005A4C0
SONG_COLORS = STYLE_DEFAULT + 0x31C


def bl_target(m: Machine, at: int) -> int:
    """Decode the Thumb-2 BL at `at` (so patched firmware is exercised through its hook)."""
    hw1, hw2 = struct.unpack("<HH", m.uc.mem_read(at, 4))
    s = (hw1 >> 10) & 1
    j1, j2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
    i1, i2 = ~(j1 ^ s) & 1, ~(j2 ^ s) & 1
    off = (s << 24) | (i1 << 23) | (i2 << 22) | ((hw1 & 0x3FF) << 12) | ((hw2 & 0x7FF) << 1)
    if s:
        off -= 1 << 25
    return at + 4 + off


def load_project(m: Machine, project: Path) -> list[int]:
    mt = (project / "project.mt").read_bytes()
    playlist = list(mt[0x10:0x10 + 255])
    m.write(PLAYLIST, bytes(playlist))
    buf = m.alloc(PATTERN_DATA_SIZE)
    usage = bl_target(m, SAVE_PATH_USAGE_CALL)
    print(f"save path calls {usage:#x}" + ("" if usage == PATTERN_UPDATE_TRACK_USAGE else " (patched)"))
    for p in sorted({p for p in playlist if p}):
        f = project / "patterns" / f"pattern_{p:02d}.mtp"
        if not f.exists():
            print(f"pattern {p}: {f.name} missing, treated as empty", file=sys.stderr)
            continue
        m.write(buf, f.read_bytes()[:PATTERN_DATA_SIZE])
        m.call(usage, 0, p, buf)
    return playlist


def build_song_control(m: Machine, slots: int, scroll: int, track_flags=b"\0" * 16) -> int:
    flags = m.alloc(16)
    m.write(flags, track_flags)

    g = m.alloc(0x40)                  # grid data (screen+0xcc in the firmware)
    m.w8(g + 0x00, slots)
    m.w32(g + 0x04, PLAYLIST)
    m.w8(g + 0x0D, 0)                  # no selection
    m.w8(g + 0x12, 0)                  # no overlay
    m.w8(g + 0x1C, 8)                  # sample tracks
    m.w8(g + 0x1D, 8)                  # MIDI / synth tracks
    m.w32(g + 0x20, MASK_LO + 1)       # indexed by pattern - 1
    m.w32(g + 0x24, MASK_HI + 1)
    m.w32(g + 0x28, flags)
    m.w32(g + 0x2C, flags + 8)

    ctrl = m.alloc(0x5C)
    m.w16(ctrl + 0x1C, CTRL_X)
    m.w16(ctrl + 0x1E, CTRL_Y)
    m.w16(ctrl + 0x20, CTRL_W)
    m.w16(ctrl + 0x22, CTRL_H)
    m.w32(ctrl + 0x28, SONG_COLORS)
    m.w16(ctrl + 0x32, scroll)
    m.w32(ctrl + 0x34, g)
    return ctrl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ptf", type=Path)
    ap.add_argument("project", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--scroll", type=int, default=0)
    ap.add_argument("--dl", type=Path, help="also write the decoded display list here")
    a = ap.parse_args()

    m = Machine(a.ptf)
    playlist = load_project(m, a.project)
    slots = next((i for i, p in enumerate(playlist) if p == 0), 255)
    for p in playlist[:slots]:
        lo, hi = m.uc.mem_read(MASK_LO + p, 1)[0], m.uc.mem_read(MASK_HI + p, 1)[0]
        print(f"pattern {p:3d}: tracks 16..1 {hi << 8 | lo:016b}")

    ctrl = build_song_control(m, slots, a.scroll)
    m.display_list.clear()
    m.call(SONG_GRID_DRAW, ctrl)
    print(f"{len(m.display_list)} display-list words")

    r = Renderer(background=(0, 0, 0))
    r.run(m.display_list)
    r.save_png(a.out, scale=2)
    if a.dl:
        a.dl.write_text("\n".join(disasm(m.display_list)) + "\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
