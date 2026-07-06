"""A software rasterizer with a from-scratch PNG encoder.

``Canvas`` holds an RGB framebuffer (a flat ``bytearray``) and knows how to
fill rectangles, draw lines, and blit text using the built-in bitmap font.
``Canvas.to_png_bytes`` serialises the framebuffer to a valid PNG using only
``zlib`` from the standard library -- the chunks, CRCs and the zlib stream are
all assembled by hand.
"""

from __future__ import annotations

import struct
import zlib
from typing import List, Tuple

from .fonts import GLYPH_H, GLYPH_W, Font, glyph_rows

RGB = Tuple[int, int, int]

# A few named colours plus a hex/rgb() parser cover the common cases.
NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "silver": (192, 192, 192), "maroon": (128, 0, 0),
    "yellow": (255, 255, 0), "olive": (128, 128, 0), "lime": (0, 255, 0),
    "aqua": (0, 255, 255), "cyan": (0, 255, 255), "teal": (0, 128, 128),
    "navy": (0, 0, 128), "fuchsia": (255, 0, 255), "magenta": (255, 0, 255),
    "purple": (128, 0, 128), "orange": (255, 165, 0), "pink": (255, 192, 203),
    "brown": (165, 42, 42), "gold": (255, 215, 0), "transparent": None,
    "lightgray": (211, 211, 211), "lightgrey": (211, 211, 211),
    "darkgray": (169, 169, 169), "darkgrey": (169, 169, 169),
    "whitesmoke": (245, 245, 245), "lightblue": (173, 216, 230),
}


def parse_color(value: str, default: RGB = (0, 0, 0)) -> RGB:
    """Parse a CSS colour into an ``(r, g, b)`` triple."""
    if not value:
        return default
    value = value.strip().lower()
    if value in NAMED_COLORS:
        color = NAMED_COLORS[value]
        return default if color is None else color
    if value.startswith("#"):
        hexpart = value[1:]
        if len(hexpart) == 3:
            r, g, b = (int(c * 2, 16) for c in hexpart)
            return (r, g, b)
        if len(hexpart) == 6:
            try:
                return (int(hexpart[0:2], 16), int(hexpart[2:4], 16),
                        int(hexpart[4:6], 16))
            except ValueError:
                return default
    if value.startswith("rgb"):
        inside = value[value.find("(") + 1:value.find(")")]
        parts = [p.strip() for p in inside.split(",")]
        try:
            nums = [int(round(float(p[:-1]) * 255 / 100)) if p.endswith("%")
                    else int(float(p)) for p in parts[:3]]
            return (max(0, min(255, nums[0])), max(0, min(255, nums[1])),
                    max(0, min(255, nums[2])))
        except (ValueError, IndexError):
            return default
    return default


class Canvas:
    """An RGB framebuffer you can draw into and export as PNG."""

    def __init__(self, width: int, height: int, background: RGB = (255, 255, 255)):
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        r, g, b = background
        self.pixels = bytearray([r, g, b] * (self.width * self.height))

    # -- primitives ----------------------------------------------------

    def set_pixel(self, x: int, y: int, color: RGB) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.pixels[i] = color[0]
            self.pixels[i + 1] = color[1]
            self.pixels[i + 2] = color[2]

    def fill_rect(self, x: int, y: int, w: int, h: int, color: RGB) -> None:
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self.width, int(x + w))
        y1 = min(self.height, int(y + h))
        if x1 <= x0 or y1 <= y0:
            return
        r, g, b = color
        row = bytes([r, g, b]) * (x1 - x0)
        for yy in range(y0, y1):
            start = (yy * self.width + x0) * 3
            self.pixels[start:start + len(row)] = row

    def draw_hline(self, x0: int, x1: int, y: int, color: RGB) -> None:
        if x1 < x0:
            x0, x1 = x1, x0
        self.fill_rect(x0, y, x1 - x0 + 1, 1, color)

    def draw_vline(self, x: int, y0: int, y1: int, color: RGB) -> None:
        if y1 < y0:
            y0, y1 = y1, y0
        self.fill_rect(x, y0, 1, y1 - y0 + 1, color)

    def draw_rect_outline(self, x: int, y: int, w: int, h: int, color: RGB) -> None:
        self.draw_hline(x, x + w - 1, y, color)
        self.draw_hline(x, x + w - 1, y + h - 1, color)
        self.draw_vline(x, y, y + h - 1, color)
        self.draw_vline(x + w - 1, y, y + h - 1, color)

    # -- text ----------------------------------------------------------

    def draw_text(self, x: int, y: int, text: str, font: Font, color: RGB) -> None:
        """Blit ``text`` with its top-left corner at ``(x, y)``."""
        scale = font.scale
        bold = font.weight in ("bold", "bolder")
        italic = font.style in ("italic", "oblique")
        cursor = x
        for ch in text:
            self._blit_glyph(cursor, y, ch, scale, bold, italic, color)
            cursor += font.char_width

    def _blit_glyph(self, ox: int, oy: int, ch: str, scale: int,
                    bold: bool, italic: bool, color: RGB) -> None:
        rows = glyph_rows(ch)
        for ry in range(GLYPH_H):
            bits = rows[ry]
            # Italic: shear right by up to ~2px near the top of the glyph.
            shear = ((GLYPH_H - ry) * scale) // 6 if italic else 0
            for rx in range(GLYPH_W):
                if bits & (1 << (GLYPH_W - 1 - rx)):
                    px = ox + rx * scale + shear
                    py = oy + ry * scale
                    self.fill_rect(px, py, scale, scale, color)
                    if bold:
                        self.fill_rect(px + 1, py, scale, scale, color)

    def blit(self, src: "Canvas", dx: int, dy: int) -> None:
        """Copy the ``src`` framebuffer into this one at ``(dx, dy)``."""
        for sy in range(src.height):
            ty = dy + sy
            if ty < 0 or ty >= self.height:
                continue
            # Clip the source row to this canvas horizontally.
            x_start = max(0, -dx)
            x_end = min(src.width, self.width - dx)
            if x_end <= x_start:
                continue
            s = (sy * src.width + x_start) * 3
            e = (sy * src.width + x_end) * 3
            t = (ty * self.width + (dx + x_start)) * 3
            self.pixels[t:t + (e - s)] = src.pixels[s:e]

    # -- PNG export ----------------------------------------------------

    def to_png_bytes(self) -> bytes:
        """Encode the framebuffer as PNG (RGB, 8-bit) from scratch."""
        raw = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw.append(0)  # filter type 0 (None) for this scanline
            start = y * stride
            raw.extend(self.pixels[start:start + stride])
        compressed = zlib.compress(bytes(raw), 9)

        def chunk(tag: bytes, data: bytes) -> bytes:
            body = tag + data
            return (struct.pack(">I", len(data)) + body
                    + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

        signature = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (signature + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))

    def save_png(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.to_png_bytes())

    # -- terminal preview (headless verification) ----------------------

    def to_ascii(self, cols: int = 100) -> str:
        """Downsample to a monochrome ASCII-art preview for the terminal."""
        cols = min(cols, self.width)
        cell_w = max(1, self.width // cols)
        cell_h = cell_w * 2  # characters are ~twice as tall as wide
        ramp = " .:-=+*#%@"
        lines: List[str] = []
        for by in range(0, self.height, cell_h):
            row_chars: List[str] = []
            for bx in range(0, self.width, cell_w):
                total = 0
                count = 0
                for yy in range(by, min(by + cell_h, self.height)):
                    base = (yy * self.width + bx) * 3
                    for xx in range(bx, min(bx + cell_w, self.width)):
                        i = base + (xx - bx) * 3
                        total += (self.pixels[i] + self.pixels[i + 1]
                                  + self.pixels[i + 2]) // 3
                        count += 1
                if count == 0:
                    row_chars.append(" ")
                    continue
                lum = total / count
                # Darker pixels -> denser characters.
                idx = int((255 - lum) / 255 * (len(ramp) - 1))
                row_chars.append(ramp[idx])
            lines.append("".join(row_chars))
        return "\n".join(lines)
