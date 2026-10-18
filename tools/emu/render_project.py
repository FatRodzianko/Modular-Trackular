"""Render a project's Song grid the way the device builds it, stock vs. patched.

  python tools/emu/render_project.py <stock.ptf> <patched.ptf> <project dir> <out.png>

For each firmware: power-on (random fingerprint table), project loaded as the
firmware leaves it (playlist and track masks from project.mt, current pattern = first
slot, FS serving the project folder as Workspace/), then the Song screen entry
sequence (usage update for the current pattern, the BL at 0x600fe402, then Song
screen update ticks until any rescan is done) and the stock song_grid_draw. Every scroll page (12 rows each) is drawn. The output image has
one column per firmware (stock left, patched right) and one row per page, cropped to
the grid.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eve import Renderer  # noqa: E402
from song_grid import SONG_GRID_DRAW, build_song_control  # noqa: E402
from test_rescan import Rig  # noqa: E402

GRID = (0, 86, 492, 420)          # x0, y0, x1, y1 of the grid area at 1x
GAP = 12


def pages(ptf: Path, project: Path):
    r = Rig(ptf)
    r.power_on()
    r.load_project(project, project.name)
    loads = r.song_enter()                         # entry + update ticks until the rescan is done
    slots = len(r.playlist)
    imgs = []
    for scroll in range(0, max(1, slots - 12 + 1), 12) if slots > 12 else [0]:
        imgs.append(draw(r, slots, scroll))
    if slots > 12 and (slots - 12) % 12:
        imgs.append(draw(r, slots, slots - 12))        # last page, like scrolling to the end
    return imgs, loads, r.playlist


def draw(r: Rig, slots: int, scroll: int) -> np.ndarray:
    ctrl = build_song_control(r.m, slots, scroll)
    r.m.display_list.clear()
    r.m.call(SONG_GRID_DRAW, ctrl)
    ren = Renderer()
    ren.run(r.m.display_list)
    x0, y0, x1, y1 = GRID
    return ren.img[y0:y1, x0:x1]


def main() -> int:
    stock, patched, project, out = (Path(a) for a in sys.argv[1:5])
    left, _, playlist = pages(stock, project)
    right, loads, _ = pages(patched, project)
    h, w, _ = left[0].shape
    rows = len(left)
    canvas = np.full((rows * h + (rows - 1) * GAP, 2 * w + GAP, 3), 40, dtype=np.uint8)
    for i in range(rows):
        y = i * (h + GAP)
        canvas[y:y + h, 0:w] = left[i]
        canvas[y:y + h, w + GAP:] = right[i]
    ren = Renderer()
    ren.img = canvas
    ren.save_png(out, scale=2)
    print(f"slots {len(playlist)}: " + " ".join(f"{p:X}" for p in playlist) + "  (hex, as the device lists them)")
    print(f"patched: {loads} pattern files read while the Song screen opened")
    print(f"wrote {out} ({rows} page(s); left = stock, right = patched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
