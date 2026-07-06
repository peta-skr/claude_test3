"""A from-scratch PNG decoder.

Just as :mod:`pybrowser.raster` *encodes* PNGs by hand, this module *decodes*
them by hand: it walks the chunk stream, inflates the ``IDAT`` data with
``zlib``, reverses the per-scanline filters, and expands the result into a
flat RGB or RGBA pixel buffer.  Grayscale, truecolor, palette and their
alpha variants are supported at bit depth 8 (palette also at 1/2/4).

Adam7-interlaced images are not supported and return ``None`` so the caller
can show a placeholder.
"""

from __future__ import annotations

import struct
import zlib
from typing import List, Optional

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# channels per pixel for each PNG colour type
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class Bitmap:
    """Decoded image pixels: RGB with an optional per-pixel alpha channel."""

    __slots__ = ("width", "height", "rgb", "alpha")

    def __init__(self, width: int, height: int, rgb: bytes,
                 alpha: Optional[bytes] = None) -> None:
        self.width = width
        self.height = height
        self.rgb = rgb                # width*height*3 bytes
        self.alpha = alpha            # width*height bytes, or None (opaque)

    def pixel(self, x: int, y: int):
        i = (y * self.width + x) * 3
        a = 255 if self.alpha is None else self.alpha[y * self.width + x]
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2], a


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytearray:
    """Reverse PNG scanline filters, returning the raw pixel bytes."""
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for row in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ftype == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        # ftype 0 == None -> nothing to do
        out[row * stride:(row + 1) * stride] = line
        prev = line
    return out


def _iter_samples(line_bytes: bytes, width: int, bit_depth: int):
    """Yield ``width`` sample values from a scanline at 1/2/4/8-bit depth."""
    if bit_depth == 8:
        for i in range(width):
            yield line_bytes[i]
    else:
        per_byte = 8 // bit_depth
        mask = (1 << bit_depth) - 1
        count = 0
        for byte in line_bytes:
            for shift in range(8 - bit_depth, -1, -bit_depth):
                if count >= width:
                    return
                yield (byte >> shift) & mask
                count += 1


def decode_png(data: bytes) -> Optional[Bitmap]:
    if data[:8] != PNG_SIGNATURE:
        return None
    pos = 8
    width = height = bit_depth = color_type = interlace = 0
    idat = bytearray()
    palette: List[tuple] = []
    trns: Optional[bytes] = None
    try:
        while pos + 8 <= len(data):
            (length,) = struct.unpack(">I", data[pos:pos + 4])
            ctype = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + length]
            pos += 12 + length  # length + type + data + crc
            if ctype == b"IHDR":
                (width, height, bit_depth, color_type, _comp, _filt,
                 interlace) = struct.unpack(">IIBBBBB", body)
            elif ctype == b"PLTE":
                palette = [(body[i], body[i + 1], body[i + 2])
                           for i in range(0, len(body), 3)]
            elif ctype == b"tRNS":
                trns = body
            elif ctype == b"IDAT":
                idat.extend(body)
            elif ctype == b"IEND":
                break
    except struct.error:
        return None

    if interlace != 0 or color_type not in _CHANNELS:
        return None
    if bit_depth not in (1, 2, 4, 8):
        return None
    if bit_depth != 8 and color_type != 3:
        return None  # only palette uses sub-byte depths here

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return None

    channels = _CHANNELS[color_type]
    bits_per_pixel = channels * bit_depth
    bpp = max(1, bits_per_pixel // 8)
    stride = (width * bits_per_pixel + 7) // 8
    if len(raw) < height * (stride + 1):
        return None
    pixels = _unfilter(raw, height, stride, bpp)

    rgb = bytearray(width * height * 3)
    alpha: Optional[bytearray] = None

    if color_type == 2:                       # truecolour RGB
        for p in range(width * height):
            rgb[p * 3:p * 3 + 3] = pixels[p * 3:p * 3 + 3]
    elif color_type == 6:                     # truecolour + alpha
        alpha = bytearray(width * height)
        for p in range(width * height):
            s = p * 4
            rgb[p * 3] = pixels[s]
            rgb[p * 3 + 1] = pixels[s + 1]
            rgb[p * 3 + 2] = pixels[s + 2]
            alpha[p] = pixels[s + 3]
    elif color_type == 0:                     # grayscale
        for p in range(width * height):
            v = pixels[p]
            rgb[p * 3] = rgb[p * 3 + 1] = rgb[p * 3 + 2] = v
    elif color_type == 4:                     # grayscale + alpha
        alpha = bytearray(width * height)
        for p in range(width * height):
            v = pixels[p * 2]
            rgb[p * 3] = rgb[p * 3 + 1] = rgb[p * 3 + 2] = v
            alpha[p] = pixels[p * 2 + 1]
    elif color_type == 3:                     # palette
        if not palette:
            return None
        alpha = bytearray(b"\xff" * (width * height)) if trns else None
        p = 0
        for row in range(height):
            line = pixels[row * stride:(row + 1) * stride]
            for idx in _iter_samples(line, width, bit_depth):
                if idx >= len(palette):
                    idx = 0
                r, g, b = palette[idx]
                rgb[p * 3] = r
                rgb[p * 3 + 1] = g
                rgb[p * 3 + 2] = b
                if alpha is not None:
                    alpha[p] = trns[idx] if idx < len(trns) else 255
                p += 1

    return Bitmap(width, height, bytes(rgb), bytes(alpha) if alpha else None)


def decode_image(data: bytes, content_type: str = "") -> Optional[Bitmap]:
    """Dispatch by magic bytes / content type to the PNG or JPEG decoder."""
    ctype = content_type.lower()
    if data[:8] == PNG_SIGNATURE or "png" in ctype:
        return decode_png(data)
    if data[:2] == b"\xff\xd8" or "jpeg" in ctype or "jpg" in ctype:
        from .jpeg import decode_jpeg
        return decode_jpeg(data)
    return None
