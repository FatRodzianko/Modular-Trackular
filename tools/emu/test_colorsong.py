"""Check the colorsong rules on synthetic patterns, running the patched firmware.

  python tools/emu/test_colorsong.py <patched.ptf> [out.png]

Patterns (track 1 = kick, track 2 = snare unless noted):
  1  kick on every 4th step incl. step 0, snare every 8th from step 4, 64 steps
  2  same as 1 plus a kick fill on steps 58, 60, 61, 62, 63
  3  exact copy of 1
  4  same as 1 but 128 steps long (content unchanged)
  5  same as 1 but the kick uses another instrument (sample 2)
  6  same as 1 plus FX1 type 5 value 10 on kick step 16
  7  same as 1 plus FX1 type 5 value 11 on kick step 16 (FX value only)
  8  mask set directly, never fingerprinted (like a pattern not opened since boot)
  9  same as 1, "humanized": every kick and snare note gets FX1 m (Micro-move, type 2)
     and FX2 V (Volume/Velocity, type 18) with varying values
 10  same as 1 plus a step holding only an m FX (no note) on the kick track
 11  same as 9 plus FX1 type 5 value 10 replacing m on kick step 16 (a counted FX)
 12  pattern 1's kick track copied to track 6 (track 1 empty), snare as in 1
 13  pattern 1's kick track copied to track 6 of a 128-step pattern
Expected: same color for 1 and 3; same hue but different shade for 2, 4, 6, 7
(vs 1); different hue for 5; snare hue differs from kick hue; untouched snare tracks
match; pattern 8 keeps the stock theme color; 9 and 10 look exactly like 1 (m and V
are ignored); 11 looks exactly like 6; the kick copied to track 6 (12) looks exactly
like the kick in 1; in a 128-step pattern (13) it keeps the hue with another shade.
Ranking: colors follow order of first appearance in the song, so the first
family (the kick in 1) is red with the main shade, families are at least 30 degrees
apart in hue, and the distinct kick variants (1, 2, 4, 6, 7) are clearly different.
"""
from __future__ import annotations

import colorsys
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eve import Renderer  # noqa: E402
from machine import Machine  # noqa: E402
from song_grid import (MASK_HI, MASK_LO, PATTERN_DATA_SIZE, PLAYLIST, SAVE_PATH_USAGE_CALL, SONG_GRID_DRAW,  # noqa: E402
                       bl_target, build_song_control)

TRACK0, TRACK_SIZE = 0x1C, 0x301
KICK, SNARE, OTHER = 0, 1, 2
MICRO_MOVE, VOLUME = 2, 18          # FX types ignored by colorsong


def empty_pattern(length: int) -> bytearray:
    d = bytearray(PATTERN_DATA_SIZE)
    for t in range(16):
        base = TRACK0 + t * TRACK_SIZE
        d[base] = length - 1
        for s in range(128):
            d[base + 1 + s * 6] = 0xFF                 # note empty
    return d


def put(d: bytearray, track: int, step: int, note: int, ins: int, fx1=(0, 0), fx2=(0, 0)):
    st = TRACK0 + track * TRACK_SIZE + 1 + step * 6
    d[st:st + 6] = bytes([note, ins, fx1[0], fx1[1], fx2[0], fx2[1]])


def base_pattern(length=64, kick_ins=KICK) -> bytearray:
    d = empty_pattern(length)
    for s in range(0, 64, 4):
        put(d, 0, s, 36, kick_ins)
    for s in range(4, 64, 8):
        put(d, 1, s, 38, SNARE)
    return d


def patterns() -> dict[int, bytearray]:
    p = {1: base_pattern(), 3: base_pattern(), 4: base_pattern(128), 5: base_pattern(kick_ins=OTHER)}
    p[2] = base_pattern()
    for s in (58, 60, 61, 62, 63):
        put(p[2], 0, s, 36, KICK)
    p[6] = base_pattern()
    put(p[6], 0, 16, 36, KICK, fx1=(5, 10))
    p[7] = base_pattern()
    put(p[7], 0, 16, 36, KICK, fx1=(5, 11))
    p[9] = base_pattern()
    for i, s in enumerate(range(0, 64, 4)):
        put(p[9], 0, s, 36, KICK, fx1=(MICRO_MOVE, 3 + i * 7 % 40), fx2=(VOLUME, 60 + i * 5 % 35))
    for i, s in enumerate(range(4, 64, 8)):
        put(p[9], 1, s, 38, SNARE, fx1=(MICRO_MOVE, 10 + i * 3), fx2=(VOLUME, 70 + i))
    p[10] = base_pattern()
    put(p[10], 0, 2, 0xFF, 0, fx1=(MICRO_MOVE, 20))
    p[12] = empty_pattern(64)
    p[13] = empty_pattern(128)
    for q in (12, 13):
        for s in range(0, 64, 4):
            put(p[q], 5, s, 36, KICK)
        for s in range(4, 64, 8):
            put(p[q], 1, s, 38, SNARE)
    p[11] = bytearray(p[9])
    put(p[11], 0, 16, 36, KICK, fx1=(5, 10), fx2=(VOLUME, 90))
    return p


def block_colors(words: list[int]) -> list[int]:
    """Colors of filled blocks, in draw order (row by row, track by track)."""
    out, color = [], None
    for w in words:
        if w >> 24 == 0x04:
            color = w & 0xFFFFFF
            out.append(color)
    return out


def hls(rgb: int):
    r, g, b = ((rgb >> 16) & 255) / 255, ((rgb >> 8) & 255) / 255, (rgb & 255) / 255
    return colorsys.rgb_to_hls(r, g, b)


def main() -> int:
    m = Machine(Path(sys.argv[1]))
    usage = bl_target(m, SAVE_PATH_USAGE_CALL)
    buf = m.alloc(PATTERN_DATA_SIZE)
    pats = patterns()
    for n, d in pats.items():
        m.write(buf, d)
        m.call(usage, 0, n, buf)
    m.w8(MASK_LO + 8, 0x03)                       # tracks 1-2 used, no fingerprint
    m.w8(MASK_HI + 8, 0x00)
    order = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    m.write(PLAYLIST, bytes(order + [0] * (255 - len(order))))
    # The grid shows 12 rows: draw the first page, then scroll to the end for the rest.
    c = {}
    for scroll in sorted({0, max(0, len(order) - 12)}):
        ctrl = build_song_control(m, len(order), scroll)
        m.display_list.clear()
        m.call(SONG_GRID_DRAW, ctrl)
        if scroll == 0 and len(sys.argv) > 2:
            r = Renderer()
            r.run(m.display_list)
            r.save_png(Path(sys.argv[2]), scale=2)
        colors = block_colors(m.display_list)
        rows = min(12, len(order) - scroll)
        assert len(colors) == 16 * rows, len(colors)
        for i in range(rows):
            for t in range(16):
                c[(order[scroll + i], t)] = colors[i * 16 + t]
    kick = {p: c[(p, 0)] for p in order if p not in (8, 12, 13)}
    kick_moved = {p: c[(p, 5)] for p in (12, 13)}
    snare = {p: c[(p, 1)] for p in order if p != 8}
    for p in kick:
        h, l_, s = hls(kick[p])
        print(f"pattern {p}: kick #{kick[p]:06x} (hue {h * 360:5.1f}, light {l_:.2f}, sat {s:.2f})"
              f"  snare #{snare[p]:06x}")

    def hue_gap(a, b):
        d = abs(hls(a)[0] - hls(b)[0]) * 360
        return min(d, 360 - d)

    def clearly_different(a, b):
        (_, la, sa), (_, lb, sb) = hls(a), hls(b)
        return abs(la - lb) * 100 >= 8 or abs(sa - sb) * 100 >= 8

    def same_hue(a, b, tol=2.0):
        d = abs(hls(a)[0] - hls(b)[0]) * 360
        return min(d, 360 - d) <= tol

    checks = [
        ("copy has identical color", kick[1] == kick[3] and snare[1] == snare[3]),
        ("fill: same hue as base", same_hue(kick[1], kick[2])),
        ("fill: different shade", kick[1] != kick[2]),
        ("128 steps: same hue", same_hue(kick[1], kick[4])),
        ("128 steps: different shade", kick[1] != kick[4]),
        ("other kick sample: different hue", not same_hue(kick[1], kick[5], tol=10)),
        ("FX added: same hue, different shade", same_hue(kick[1], kick[6]) and kick[1] != kick[6]),
        ("FX value only: different shade", kick[6] != kick[7]),
        ("snare hue differs from kick hue", not same_hue(kick[1], snare[1], tol=10)),
        ("snare unchanged by kick fill / FX", snare[1] == snare[2] == snare[6] == snare[7]),
        ("empty tracks keep the theme color",
         all(c[(p, t)] == 0x121212 for p in order for t in range(2, 16) if not (p in (12, 13) and t == 5))
         and all(c[(p, 0)] == 0x121212 for p in (12, 13))),
        ("track copied to another column: identical color", kick_moved[12] == kick[1]),
        ("copied into a 128-step pattern: same hue, other shade",
         same_hue(kick_moved[13], kick[1]) and kick_moved[13] != kick[1]),
        ("no fingerprint: stock white", c[(8, 0)] == 0xFFFFFF and c[(8, 1)] == 0xFFFFFF),
        ("first family: red, main shade", kick[1] == 0xE31C1C),
        ("families at least 30 degrees apart", hue_gap(kick[1], snare[1]) >= 30 and hue_gap(kick[1], kick[5]) >= 30
         and hue_gap(snare[1], kick[5]) >= 30),
        ("kick variants clearly different (>= 8 points in lightness or saturation)",
         all(clearly_different(kick[a], kick[b]) for a in (1, 2, 4, 6, 7) for b in (1, 2, 4, 6, 7) if a < b)),
        ("humanized (m + V on every note): same colors as base", kick[9] == kick[1] and snare[9] == snare[1]),
        ("step with only an m FX: same color as base", kick[10] == kick[1]),
        ("counted FX still counts next to ignored ones", kick[11] == kick[6] and kick[11] != kick[1]),
    ]
    ok = True
    for name, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok &= passed

    # Flagged tracks (the firmware's alternate block color, e.g. muted): same hue, darker.
    m.display_list.clear()
    ctrl = build_song_control(m, len(order), 0, track_flags=bytes([1] + [0] * 15))
    m.call(SONG_GRID_DRAW, ctrl)
    flagged = block_colors(m.display_list)[0]          # pattern 1, track 1
    passed = same_hue(kick[1], flagged, tol=4) and hls(flagged)[1] < hls(kick[1])[1] / 2
    print(f"  {'PASS' if passed else 'FAIL'}  flagged track: same hue, darker (#{flagged:06x})")
    ok &= passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
