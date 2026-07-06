"""A baseline (sequential DCT, Huffman) JPEG encoder and decoder.

Like the PNG pair in :mod:`pybrowser.raster` (encode) and
:mod:`pybrowser.image` (decode), this module implements *both* directions
from scratch so they verify each other: :func:`encode_canvas` writes a
standards-compliant baseline JPEG, and :func:`decode_jpeg` reads one back.

Scope: baseline SOF0, 8-bit, Huffman coding, YCbCr, with chroma subsampling
supported on decode.  The encoder emits 4:4:4 with the standard Annex-K
tables.  Progressive and arithmetic JPEGs are not supported.
"""

from __future__ import annotations

import math
import struct
from typing import Dict, List, Optional, Tuple

from .image import Bitmap

# Zig-zag ordering of the 64 DCT coefficients.
ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
]

# Standard (Annex K) quantisation tables at "quality 50".
STD_LUMA_Q = [
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99,
]
STD_CHROMA_Q = [
    17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99,
]

# Standard Huffman table specifications: (counts-per-length[16], values).
DC_LUMA_BITS = [0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
DC_LUMA_VALS = list(range(12))
DC_CHROMA_BITS = [0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
DC_CHROMA_VALS = list(range(12))
AC_LUMA_BITS = [0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d]
AC_LUMA_VALS = [
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
    0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08,
    0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0, 0x24, 0x33, 0x62, 0x72,
    0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45,
    0x46, 0x47, 0x48, 0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74, 0x75,
    0x76, 0x77, 0x78, 0x79, 0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3,
    0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6,
    0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9,
    0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
    0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4,
    0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa,
]
AC_CHROMA_BITS = [0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77]
AC_CHROMA_VALS = [
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41,
    0x51, 0x07, 0x61, 0x71, 0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91,
    0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0, 0x15, 0x62, 0x72, 0xd1,
    0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
    0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44,
    0x45, 0x46, 0x47, 0x48, 0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58,
    0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74,
    0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a,
    0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4,
    0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7,
    0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
    0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4,
    0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa,
]

# Pre-computed cosine basis for the 8x8 (I)DCT.
_COS = [[math.cos((2 * x + 1) * u * math.pi / 16) for u in range(8)]
        for x in range(8)]
_C = [1 / math.sqrt(2)] + [1.0] * 7


def _scaled_quant(base: List[int], quality: int) -> List[int]:
    quality = max(1, min(100, quality))
    scale = 5000 // quality if quality < 50 else 200 - quality * 2
    out = []
    for q in base:
        v = (q * scale + 50) // 100
        out.append(max(1, min(255, v)))
    return out


# ---------------------------------------------------------------------------
# DCT
# ---------------------------------------------------------------------------


def _fdct(block: List[float]) -> List[float]:
    out = [0.0] * 64
    for v in range(8):
        for u in range(8):
            s = 0.0
            for y in range(8):
                cy = _COS[y][v]
                row = y * 8
                for x in range(8):
                    s += block[row + x] * _COS[x][u] * cy
            out[v * 8 + u] = 0.25 * _C[u] * _C[v] * s
    return out


def _idct(block: List[float]) -> List[float]:
    out = [0.0] * 64
    for y in range(8):
        for x in range(8):
            s = 0.0
            for v in range(8):
                cyv = _COS[y][v] * _C[v]
                row = v * 8
                for u in range(8):
                    s += _C[u] * cyv * block[row + u] * _COS[x][u]
            out[y * 8 + x] = 0.25 * s
    return out


# ---------------------------------------------------------------------------
# Huffman helpers
# ---------------------------------------------------------------------------


def _build_encode_table(bits: List[int], vals: List[int]) -> Dict[int, Tuple[int, int]]:
    table: Dict[int, Tuple[int, int]] = {}
    code = 0
    k = 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            table[vals[k]] = (code, length)
            code += 1
            k += 1
        code <<= 1
    return table


def _build_decode_table(bits: List[int], vals: List[int]) -> Dict[Tuple[int, int], int]:
    table: Dict[Tuple[int, int], int] = {}
    code = 0
    k = 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            table[(length, code)] = vals[k]
            code += 1
            k += 1
        code <<= 1
    return table


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------


class _BitWriter:
    def __init__(self) -> None:
        self.out = bytearray()
        self.acc = 0
        self.nbits = 0

    def write(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.acc = (self.acc << 1) | ((value >> i) & 1)
            self.nbits += 1
            if self.nbits == 8:
                self._emit(self.acc)
                self.acc = 0
                self.nbits = 0

    def _emit(self, byte: int) -> None:
        self.out.append(byte)
        if byte == 0xFF:
            self.out.append(0x00)  # byte stuffing

    def flush(self) -> None:
        if self.nbits > 0:
            self.acc = (self.acc << (8 - self.nbits)) | ((1 << (8 - self.nbits)) - 1)
            self._emit(self.acc)
            self.acc = 0
            self.nbits = 0


def _category(value: int) -> int:
    return value.bit_length()


def _mantissa(value: int, size: int) -> int:
    return value if value >= 0 else value + (1 << size) - 1


def encode_canvas(canvas, quality: int = 80) -> bytes:
    """Encode a :class:`pybrowser.raster.Canvas` as a baseline JPEG (4:4:4)."""
    return encode_rgb(canvas.pixels, canvas.width, canvas.height, quality)


def encode_rgb(pixels, width: int, height: int, quality: int = 80) -> bytes:
    luma_q = _scaled_quant(STD_LUMA_Q, quality)
    chroma_q = _scaled_quant(STD_CHROMA_Q, quality)

    # RGB -> YCbCr planes (full resolution; 4:4:4).
    y_plane = [0.0] * (width * height)
    cb_plane = [0.0] * (width * height)
    cr_plane = [0.0] * (width * height)
    for i in range(width * height):
        r, g, b = pixels[i * 3], pixels[i * 3 + 1], pixels[i * 3 + 2]
        y_plane[i] = 0.299 * r + 0.587 * g + 0.114 * b
        cb_plane[i] = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
        cr_plane[i] = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b

    dc_luma = _build_encode_table(DC_LUMA_BITS, DC_LUMA_VALS)
    ac_luma = _build_encode_table(AC_LUMA_BITS, AC_LUMA_VALS)
    dc_chroma = _build_encode_table(DC_CHROMA_BITS, DC_CHROMA_VALS)
    ac_chroma = _build_encode_table(AC_CHROMA_BITS, AC_CHROMA_VALS)

    writer = _BitWriter()
    preds = [0, 0, 0]
    planes = [y_plane, cb_plane, cr_plane]
    quants = [luma_q, chroma_q, chroma_q]
    dc_tabs = [dc_luma, dc_chroma, dc_chroma]
    ac_tabs = [ac_luma, ac_chroma, ac_chroma]

    for by in range(0, height, 8):
        for bx in range(0, width, 8):
            for comp in range(3):
                preds[comp] = _encode_block(
                    writer, planes[comp], width, height, bx, by,
                    quants[comp], dc_tabs[comp], ac_tabs[comp], preds[comp])
    writer.flush()

    return _assemble(width, height, luma_q, chroma_q, bytes(writer.out))


def _encode_block(writer, plane, width, height, bx, by, quant,
                  dc_table, ac_table, pred) -> int:
    block = [0.0] * 64
    for y in range(8):
        sy = min(by + y, height - 1)
        for x in range(8):
            sx = min(bx + x, width - 1)
            block[y * 8 + x] = plane[sy * width + sx] - 128.0
    coeffs = _fdct(block)
    q = [int(round(coeffs[i] / quant[i])) for i in range(64)]

    # DC (differential).
    diff = q[0] - pred
    size = _category(abs(diff))
    code, length = dc_table[size]
    writer.write(code, length)
    if size:
        writer.write(_mantissa(diff, size), size)

    # AC (run-length + Huffman) in zig-zag order.
    run = 0
    for k in range(1, 64):
        coef = q[ZIGZAG[k]]
        if coef == 0:
            run += 1
            continue
        while run > 15:
            zrl = ac_table[0xF0]
            writer.write(zrl[0], zrl[1])
            run -= 16
        size = _category(abs(coef))
        code, length = ac_table[(run << 4) | size]
        writer.write(code, length)
        writer.write(_mantissa(coef, size), size)
        run = 0
    if run > 0:  # trailing zeros -> end-of-block
        eob = ac_table[0x00]
        writer.write(eob[0], eob[1])
    return q[0]


def _dqt(table_id: int, quant: List[int]) -> bytes:
    # DQT stores the 64 values in zig-zag order.
    zz = bytes(quant[ZIGZAG[i]] for i in range(64))
    return _marker(0xDB, bytes([table_id]) + zz)


def _dht(cls_id: int, bits: List[int], vals: List[int]) -> bytes:
    return _marker(0xC4, bytes([cls_id]) + bytes(bits) + bytes(vals))


def _marker(code: int, payload: bytes) -> bytes:
    return bytes([0xFF, code]) + struct.pack(">H", len(payload) + 2) + payload


def _assemble(width, height, luma_q, chroma_q, scan: bytes) -> bytes:
    out = bytearray(b"\xff\xd8")  # SOI
    out += _marker(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")  # APP0
    out += _dqt(0, luma_q)
    out += _dqt(1, chroma_q)
    # SOF0: precision, height, width, 3 components (all 1x1 sampling, 4:4:4).
    sof = struct.pack(">BHHB", 8, height, width, 3)
    sof += bytes([1, 0x11, 0, 2, 0x11, 1, 3, 0x11, 1])
    out += _marker(0xC0, sof)
    out += _dht(0x00, DC_LUMA_BITS, DC_LUMA_VALS)
    out += _dht(0x10, AC_LUMA_BITS, AC_LUMA_VALS)
    out += _dht(0x01, DC_CHROMA_BITS, DC_CHROMA_VALS)
    out += _dht(0x11, AC_CHROMA_BITS, AC_CHROMA_VALS)
    # SOS: 3 components, DC/AC table selectors.
    sos = bytes([3, 1, 0x00, 2, 0x11, 3, 0x11, 0, 63, 0])
    out += _marker(0xDA, sos)
    out += scan
    out += b"\xff\xd9"  # EOI
    return bytes(out)


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------


class _BitReader:
    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos
        self.acc = 0
        self.nbits = 0
        self.marker = 0  # set when a non-stuffed 0xFF marker is hit

    def bit(self) -> int:
        if self.nbits == 0:
            if self.pos >= len(self.data):
                return 0
            byte = self.data[self.pos]
            self.pos += 1
            if byte == 0xFF:
                nxt = self.data[self.pos] if self.pos < len(self.data) else 0
                if nxt == 0x00:
                    self.pos += 1  # stuffed byte
                else:
                    self.marker = nxt
                    return 0
            self.acc = byte
            self.nbits = 8
        self.nbits -= 1
        return (self.acc >> self.nbits) & 1

    def receive(self, size: int) -> int:
        v = 0
        for _ in range(size):
            v = (v << 1) | self.bit()
        return v

    def extend(self, size: int) -> int:
        if size == 0:
            return 0
        v = self.receive(size)
        if v < (1 << (size - 1)):
            v += (-1 << size) + 1
        return v

    def reset(self) -> None:
        self.acc = 0
        self.nbits = 0
        self.marker = 0


def decode_jpeg(data: bytes) -> Optional[Bitmap]:
    if data[:2] != b"\xff\xd8":
        return None
    try:
        return _decode(data)
    except (IndexError, KeyError, ValueError, struct.error):
        return None


def _huff_decode(reader: _BitReader, table: Dict[Tuple[int, int], int]) -> int:
    code = 0
    for length in range(1, 17):
        code = (code << 1) | reader.bit()
        val = table.get((length, code))
        if val is not None:
            return val
    raise ValueError("bad Huffman code")


def _decode(data: bytes) -> Optional[Bitmap]:
    pos = 2
    n = len(data)
    quant: Dict[int, List[int]] = {}
    huff_dc: Dict[int, Dict[Tuple[int, int], int]] = {}
    huff_ac: Dict[int, Dict[Tuple[int, int], int]] = {}
    width = height = 0
    components: List[dict] = []
    restart_interval = 0

    while pos < n:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        length = struct.unpack(">H", data[pos:pos + 2])[0]
        seg = data[pos + 2:pos + length]
        pos += length

        if marker == 0xDB:  # DQT
            i = 0
            while i < len(seg):
                pq_tq = seg[i]
                i += 1
                table_id = pq_tq & 0x0F
                precision = pq_tq >> 4
                if precision == 0:
                    tbl = list(seg[i:i + 64])
                    i += 64
                else:
                    tbl = [struct.unpack(">H", seg[i + 2 * j:i + 2 * j + 2])[0]
                           for j in range(64)]
                    i += 128
                # De-zig-zag into natural order.
                natural = [0] * 64
                for k in range(64):
                    natural[ZIGZAG[k]] = tbl[k]
                quant[table_id] = natural
        elif marker == 0xC0:  # SOF0 (baseline)
            _prec, height, width, ncomp = struct.unpack(">BHHB", seg[:6])
            off = 6
            for _ in range(ncomp):
                cid = seg[off]
                hv = seg[off + 1]
                tq = seg[off + 2]
                components.append({"id": cid, "h": hv >> 4, "v": hv & 0x0F,
                                   "quant": tq})
                off += 3
        elif marker in (0xC1, 0xC2, 0xC3):
            return None  # extended/progressive/lossless not supported
        elif marker == 0xC4:  # DHT
            i = 0
            while i < len(seg):
                tc_th = seg[i]
                i += 1
                counts = list(seg[i:i + 16])
                i += 16
                total = sum(counts)
                vals = list(seg[i:i + total])
                i += total
                table = _build_decode_table(counts, vals)
                if tc_th >> 4 == 0:
                    huff_dc[tc_th & 0x0F] = table
                else:
                    huff_ac[tc_th & 0x0F] = table
        elif marker == 0xDD:  # DRI
            restart_interval = struct.unpack(">H", seg[:2])[0]
        elif marker == 0xDA:  # SOS
            ns = seg[0]
            off = 1
            scan = []
            for _ in range(ns):
                cs = seg[off]
                td_ta = seg[off + 1]
                off += 2
                comp = next(c for c in components if c["id"] == cs)
                comp["dc"] = td_ta >> 4
                comp["ac"] = td_ta & 0x0F
                scan.append(comp)
            return _decode_scan(data, pos, width, height, components, scan,
                                quant, huff_dc, huff_ac, restart_interval)
    return None


def _decode_scan(data, pos, width, height, components, scan, quant,
                 huff_dc, huff_ac, restart_interval) -> Bitmap:
    hmax = max(c["h"] for c in components)
    vmax = max(c["v"] for c in components)
    mcu_w = 8 * hmax
    mcu_h = 8 * vmax
    mcus_x = (width + mcu_w - 1) // mcu_w
    mcus_y = (height + mcu_h - 1) // mcu_h

    for c in components:
        c["px_w"] = mcus_x * c["h"] * 8
        c["px_h"] = mcus_y * c["v"] * 8
        c["data"] = [0] * (c["px_w"] * c["px_h"])
        c["pred"] = 0

    reader = _BitReader(data, pos)
    mcu_count = 0
    for my in range(mcus_y):
        for mx in range(mcus_x):
            if restart_interval and mcu_count and mcu_count % restart_interval == 0:
                reader.reset()
                for c in components:
                    c["pred"] = 0
            for comp in scan:
                for by in range(comp["v"]):
                    for bx in range(comp["h"]):
                        _decode_block(reader, comp, quant[comp["quant"]],
                                      huff_dc[comp["dc"]], huff_ac[comp["ac"]],
                                      (mx * comp["h"] + bx) * 8,
                                      (my * comp["v"] + by) * 8)
            mcu_count += 1

    return _to_rgb(width, height, components, hmax, vmax)


def _decode_block(reader, comp, quant, dc_table, ac_table, ox, oy) -> None:
    coeffs = [0.0] * 64
    t = _huff_decode(reader, dc_table)
    diff = reader.extend(t)
    comp["pred"] += diff
    coeffs[0] = comp["pred"] * quant[0]

    k = 1
    while k < 64:
        rs = _huff_decode(reader, ac_table)
        r = rs >> 4
        s = rs & 0x0F
        if s == 0:
            if r == 15:
                k += 16
                continue
            break  # end of block
        k += r
        if k >= 64:
            break
        coeffs[ZIGZAG[k]] = reader.extend(s) * quant[ZIGZAG[k]]
        k += 1

    block = _idct(coeffs)
    stride = comp["px_w"]
    for y in range(8):
        row = (oy + y) * stride + ox
        for x in range(8):
            comp["data"][row + x] = int(round(block[y * 8 + x])) + 128


def _to_rgb(width, height, components, hmax, vmax) -> Bitmap:
    rgb = bytearray(width * height * 3)
    single = len(components) == 1
    y_c = components[0]
    cb_c = components[1] if not single else None
    cr_c = components[2] if not single and len(components) > 2 else None

    for py in range(height):
        for px in range(width):
            yv = _sample(y_c, px, py, hmax, vmax)
            if single:
                r = g = b = yv
            else:
                cb = _sample(cb_c, px, py, hmax, vmax) - 128
                cr = _sample(cr_c, px, py, hmax, vmax) - 128
                r = yv + 1.402 * cr
                g = yv - 0.344136 * cb - 0.714136 * cr
                b = yv + 1.772 * cb
            i = (py * width + px) * 3
            rgb[i] = _clamp(r)
            rgb[i + 1] = _clamp(g)
            rgb[i + 2] = _clamp(b)
    return Bitmap(width, height, bytes(rgb), None)


def _sample(comp, px, py, hmax, vmax) -> float:
    cx = px * comp["h"] // hmax
    cy = py * comp["v"] // vmax
    return comp["data"][cy * comp["px_w"] + cx]


def _clamp(v: float) -> int:
    return 0 if v < 0 else (255 if v > 255 else int(round(v)))
