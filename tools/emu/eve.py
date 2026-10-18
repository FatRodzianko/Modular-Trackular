"""Decode and roughly render FT81x/BT81x (EVE) display-list words.

Good enough to check layout and colors of what the firmware draws: RECTS, LINES,
LINE_STRIP and POINTS with COLOR_RGB, SCISSOR and SAVE/RESTORE_CONTEXT. Text,
bitmaps and co-processor widgets are listed by `disasm` but not rendered.
Vertex coordinates are taken as whole pixels (VERTEX_FORMAT 0), which is what the
Tracker+ firmware passes.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

W, H = 800, 480

PRIMS = {1: "BITMAPS", 2: "POINTS", 3: "LINES", 4: "LINE_STRIP", 5: "EDGE_STRIP_R",
         6: "EDGE_STRIP_L", 7: "EDGE_STRIP_A", 8: "EDGE_STRIP_B", 9: "RECTS"}
OPS = {0x00: "DISPLAY", 0x01: "BITMAP_SOURCE", 0x02: "CLEAR_COLOR_RGB", 0x03: "TAG",
       0x04: "COLOR_RGB", 0x05: "BITMAP_HANDLE", 0x06: "CELL", 0x07: "BITMAP_LAYOUT",
       0x08: "BITMAP_SIZE", 0x09: "ALPHA_FUNC", 0x0A: "STENCIL_FUNC", 0x0B: "BLEND_FUNC",
       0x0C: "STENCIL_OP", 0x0D: "POINT_SIZE", 0x0E: "LINE_WIDTH", 0x0F: "CLEAR_COLOR_A",
       0x10: "COLOR_A", 0x11: "CLEAR_STENCIL", 0x12: "CLEAR_TAG", 0x13: "STENCIL_MASK",
       0x14: "TAG_MASK", 0x15: "BITMAP_TRANSFORM_A", 0x1B: "SCISSOR_XY", 0x1C: "SCISSOR_SIZE",
       0x1F: "BEGIN", 0x20: "COLOR_MASK", 0x21: "END", 0x22: "SAVE_CONTEXT",
       0x23: "RESTORE_CONTEXT", 0x24: "RETURN", 0x25: "MACRO", 0x26: "CLEAR",
       0x27: "VERTEX_FORMAT", 0x28: "BITMAP_LAYOUT_H", 0x29: "BITMAP_SIZE_H"}


def _s15(v: int) -> int:
    return v - 0x8000 if v & 0x4000 else v


def disasm(words: list[int]) -> list[str]:
    out = []
    for w in words:
        top = w >> 30
        if top == 1:
            out.append(f"VERTEX2F({_s15((w >> 15) & 0x7FFF)}, {_s15(w & 0x7FFF)})")
        elif top == 2:
            out.append(f"VERTEX2II({(w >> 21) & 0x1FF}, {(w >> 12) & 0x1FF}, {(w >> 7) & 0x1F}, {w & 0x7F})")
        elif (w >> 24) == 0xFF:
            out.append(f"CMD_{w & 0xFF:#04x}")
        else:
            op = w >> 24
            name = OPS.get(op, f"OP_{op:#04x}")
            if op == 0x1F:
                out.append(f"BEGIN({PRIMS.get(w & 0xF, w & 0xF)})")
            elif op in (0x04, 0x02):
                out.append(f"{name}(#{w & 0xFFFFFF:06x})")
            else:
                out.append(f"{name}({w & 0xFFFFFF:#x})")
    return out


class Renderer:
    def __init__(self, background=(0, 0, 0)):
        self.img = np.zeros((H, W, 3), dtype=np.uint8)
        self.img[:, :] = background
        self.reset_state()

    def reset_state(self):
        self.color = (255, 255, 255)
        self.line_width = 16
        self.point_size = 16
        self.scissor = [0, 0, W, H]
        self.prim = None
        self.verts: list[tuple[int, int]] = []
        self.stack = []

    def _fill(self, x0, y0, x1, y1):
        sx, sy, sw, sh = self.scissor
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        x0, y0 = max(x0, sx, 0), max(y0, sy, 0)
        x1, y1 = min(x1, sx + sw - 1, W - 1), min(y1, sy + sh - 1, H - 1)
        if x0 <= x1 and y0 <= y1:
            self.img[y0:y1 + 1, x0:x1 + 1] = self.color

    def _line(self, a, b):
        half = max(0, (self.line_width // 16) // 2)
        (x0, y0), (x1, y1) = a, b
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            x = round(x0 + (x1 - x0) * i / n)
            y = round(y0 + (y1 - y0) * i / n)
            self._fill(x - half, y - half, x + half, y + half)

    def _vertex(self, x, y):
        self.verts.append((x, y))
        p = self.prim
        if p == 9 and len(self.verts) == 2:
            (x0, y0), (x1, y1) = self.verts
            self._fill(x0, y0, x1, y1)
            self.verts = []
        elif p == 3 and len(self.verts) == 2:
            self._line(*self.verts)
            self.verts = []
        elif p == 4 and len(self.verts) >= 2:
            self._line(self.verts[-2], self.verts[-1])
        elif p == 2:
            r = self.point_size // 16
            self._fill(x - r, y - r, x + r, y + r)
            self.verts = []
        elif p not in (2, 3, 4, 9):
            self.verts = []

    def run(self, words: list[int]):
        for w in words:
            top = w >> 30
            if top == 1:
                self._vertex(_s15((w >> 15) & 0x7FFF), _s15(w & 0x7FFF))
                continue
            if top == 2:
                self._vertex((w >> 21) & 0x1FF, (w >> 12) & 0x1FF)
                continue
            if (w >> 24) == 0xFF:
                continue  # co-processor command; parameters are not decoded
            op, arg = w >> 24, w & 0xFFFFFF
            if op == 0x04:
                self.color = ((arg >> 16) & 0xFF, (arg >> 8) & 0xFF, arg & 0xFF)
            elif op == 0x0E:
                self.line_width = arg & 0xFFF
            elif op == 0x0D:
                self.point_size = arg & 0x1FFF
            elif op == 0x1F:
                self.prim, self.verts = arg & 0xF, []
            elif op == 0x21:
                self.prim, self.verts = None, []
            elif op == 0x1B:
                self.scissor[0], self.scissor[1] = (arg >> 11) & 0x7FF, arg & 0x7FF
            elif op == 0x1C:
                self.scissor[2], self.scissor[3] = (arg >> 12) & 0xFFF, arg & 0xFFF
            elif op == 0x22:
                self.stack.append((self.color, self.line_width, self.point_size, list(self.scissor)))
            elif op == 0x23 and self.stack:
                self.color, self.line_width, self.point_size, self.scissor = self.stack.pop()

    def save_png(self, path: Path, scale: int = 1):
        img = self.img.repeat(scale, 0).repeat(scale, 1) if scale > 1 else self.img
        h, w, _ = img.shape
        raw = b"".join(b"\0" + img[y].tobytes() for y in range(h))

        def chunk(tag, data):
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
        Path(path).write_bytes(png)
