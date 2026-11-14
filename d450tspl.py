"""TSPL job generation for the D450 thermal label printer (Zhuhai Quin / Labelife).

The byte stream reproduces what the vendor's Windows driver plugin (QYUNI.dll)
sends: a text header (SIZE / REFERENCE / GAP / OFFSET / DIRECTION / DENSITY /
SPEED / SET TEAR|PEEL / CLS), one BITMAP command carrying a 1-bit image, and
PRINT. In the bitmap a 1 bit is white (no dot) and a 0 bit is black, MSB first.

Pure standard library; liblzo2 is loaded through ctypes only when compression
is requested.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator, List, Optional, Tuple

DPI = 203
MAX_WIDTH_DOTS = 864          # GPD: MaxPrintableWidth
MAX_HEIGHT_DOTS = 3045        # GPD: CUSTOMSIZE MaxSize
CRLF = b"\r\n"

MEDIA_GAP = "gap"
MEDIA_CONTINUOUS = "continuous"
MEDIA_MARK = "mark"

POST_NONE = "none"
POST_TEAR = "tear"
POST_PEEL = "peel"

DITHER_THRESHOLD = "threshold"
DITHER_ORDERED = "ordered"
DITHER_DIFFUSION = "diffusion"


class D450Error(Exception):
    pass


@dataclass
class JobOptions:
    density: Optional[int] = 8      # 0..15; None = leave the printer's setting
    speed: Optional[int] = 5        # 2..7;  None = leave the printer's setting
    media: str = MEDIA_GAP
    gap_mm: float = 3.0             # gap (or black-mark) height
    gap_offset_mm: float = 0.0
    post: str = POST_NONE
    offset_mm: float = 0.0          # extra feed after print (TSPL OFFSET)
    x: int = 0                      # bitmap origin, dots
    y: int = 0
    copies: int = 1
    compress: bool = False          # BITMAP mode 4 (used by the vendor over Bluetooth)

    def validate(self) -> None:
        if self.density is not None and not 0 <= self.density <= 15:
            raise D450Error("density must be 0..15")
        if self.speed is not None and not 2 <= self.speed <= 7:
            raise D450Error("speed must be 2..7")
        if self.media not in (MEDIA_GAP, MEDIA_CONTINUOUS, MEDIA_MARK):
            raise D450Error("unknown media type %r" % self.media)
        if self.post not in (POST_NONE, POST_TEAR, POST_PEEL):
            raise D450Error("unknown post action %r" % self.post)
        if self.copies < 1:
            raise D450Error("copies must be >= 1")


def dots_to_mm(dots: int) -> int:
    """Whole millimetres, rounded to nearest as the vendor driver does for SIZE."""
    return int(dots * 25.4 / DPI + 0.5)


def mm_to_dots(mm: float) -> int:
    return int(round(mm * DPI / 25.4))


# --------------------------------------------------------------------------
# 1-bit conversion
# --------------------------------------------------------------------------

_BAYER8 = [
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
]


def _threshold_table(threshold: int) -> bytes:
    # grey value -> ASCII '1' (white) or '0' (black)
    return bytes(0x31 if v >= threshold else 0x30 for v in range(256))


_ORDERED_TABLES = [
    [_threshold_table(int((_BAYER8[y][x] + 0.5) * 4)) for x in range(8)]
    for y in range(8)
]


def _pack_row(ascii_bits: bytes, width_bytes: int) -> bytes:
    """ASCII '0'/'1' string of width_bytes*8 characters -> packed bytes."""
    return int(ascii_bits, 2).to_bytes(width_bytes, "big")


def gray_to_bits(gray: bytes, width: int, height: int,
                 dither: str = DITHER_THRESHOLD,
                 threshold: int = 128) -> Tuple[bytes, int]:
    """Convert 8-bit grey (0 = black, 255 = white) to the printer's 1-bit rows.

    Returns (data, width_bytes). Rows are padded to a byte with white.
    """
    if len(gray) != width * height:
        raise D450Error("grey buffer is %d bytes, expected %d"
                        % (len(gray), width * height))
    width_bytes = (width + 7) // 8
    pad = b"1" * (width_bytes * 8 - width)
    out = bytearray()

    if dither == DITHER_THRESHOLD:
        table = _threshold_table(threshold)
        for row in range(height):
            line = gray[row * width:(row + 1) * width].translate(table)
            out += _pack_row(line + pad, width_bytes)
    elif dither == DITHER_ORDERED:
        for row in range(height):
            src = gray[row * width:(row + 1) * width]
            line = bytearray(width)
            tables = _ORDERED_TABLES[row & 7]
            for phase in range(8):
                line[phase::8] = src[phase::8].translate(tables[phase])
            out += _pack_row(bytes(line) + pad, width_bytes)
    elif dither == DITHER_DIFFUSION:
        # Floyd-Steinberg, serpentine-free; errors kept in integer sixteenths.
        nxt = [0] * (width + 2)
        for row in range(height):
            cur = nxt
            nxt = [0] * (width + 2)
            src = gray[row * width:(row + 1) * width]
            line = bytearray(width)
            carry = 0
            for col in range(width):
                value = src[col] + ((cur[col + 1] + carry) >> 4)
                if value >= 128:
                    line[col] = 0x31
                    err = value - 255
                else:
                    line[col] = 0x30
                    err = value
                carry = err * 7
                nxt[col] += err * 3
                nxt[col + 1] += err * 5
                nxt[col + 2] += err
            out += _pack_row(bytes(line) + pad, width_bytes)
    else:
        raise D450Error("unknown dither mode %r" % dither)
    return bytes(out), width_bytes


def rotate180_gray(gray: bytes) -> bytes:
    return gray[::-1]


def crop_gray(gray: bytes, width: int, height: int,
              new_width: int, new_height: int) -> bytes:
    """Crop (never pad) a grey buffer to at most new_width x new_height."""
    new_width = min(width, new_width)
    new_height = min(height, new_height)
    if new_width == width:
        return gray[:width * new_height]
    return b"".join(gray[r * width:r * width + new_width]
                    for r in range(new_height))


# --------------------------------------------------------------------------
# LZO (BITMAP mode 4)
# --------------------------------------------------------------------------

_lzo = None


def _load_lzo():
    global _lzo
    if _lzo is None:
        try:
            lib = ctypes.CDLL("liblzo2.so.2")
        except OSError:
            try:
                lib = ctypes.CDLL(ctypes.util.find_library("lzo2") or "liblzo2.so")
            except OSError as exc:
                raise D450Error("compression needs liblzo2 (%s)" % exc)
        lib.lzo1x_1_compress.restype = ctypes.c_int
        lib.lzo1x_decompress_safe.restype = ctypes.c_int
        _lzo = lib
    return _lzo


LZO_CHUNK = 4096


def pack_bitmap(data: bytes) -> bytes:
    """BITMAP mode 4 payload, as framed by the vendor driver.

    The bitmap is cut into 4096-byte chunks; each is LZO1X-compressed on its
    own and written as a 4-byte little-endian length plus the compressed
    bytes. Four zero bytes end the payload.
    """
    out = bytearray()
    for start in range(0, len(data), LZO_CHUNK):
        packed = lzo1x_compress(data[start:start + LZO_CHUNK])
        out += struct.pack("<I", len(packed)) + packed
    out += b"\0\0\0\0"
    return bytes(out)


def unpack_bitmap(data: bytes, pos: int, expected_len: int) -> Tuple[bytes, int]:
    """Inverse of pack_bitmap. Returns (bitmap, position after terminator)."""
    out = bytearray()
    while True:
        if pos + 4 > len(data):
            raise D450Error("truncated compressed BITMAP")
        (length,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if length == 0:
            break
        remaining = expected_len - len(out)
        if remaining <= 0 or pos + length > len(data):
            raise D450Error("corrupt compressed BITMAP")
        out += lzo1x_decompress(data[pos:pos + length], min(LZO_CHUNK, remaining))
        pos += length
    return bytes(out), pos


def lzo1x_compress(data: bytes) -> bytes:
    lib = _load_lzo()
    out = ctypes.create_string_buffer(len(data) + len(data) // 16 + 64 + 3)
    out_len = ctypes.c_ulong(len(out))
    work = ctypes.create_string_buffer(8 * 16384 * 2)
    rc = lib.lzo1x_1_compress(data, ctypes.c_ulong(len(data)), out,
                              ctypes.byref(out_len), work)
    if rc != 0:
        raise D450Error("lzo1x_1_compress failed (%d)" % rc)
    return out.raw[:out_len.value]


def lzo1x_decompress(data: bytes, expected_len: int) -> bytes:
    lib = _load_lzo()
    out = ctypes.create_string_buffer(expected_len)
    out_len = ctypes.c_ulong(expected_len)
    rc = lib.lzo1x_decompress_safe(data, ctypes.c_ulong(len(data)), out,
                                   ctypes.byref(out_len), None)
    if rc != 0:
        raise D450Error("lzo1x_decompress_safe failed (%d)" % rc)
    return out.raw[:out_len.value]


# --------------------------------------------------------------------------
# Job building
# --------------------------------------------------------------------------

def _mm1(value: float) -> bytes:
    return ("%.1f" % value).encode("ascii")


def build_header(width_dots: int, height_dots: int, opts: JobOptions) -> bytes:
    opts.validate()
    out = bytearray()
    out += b"SIZE %d mm,%d mm" % (dots_to_mm(width_dots), dots_to_mm(height_dots)) + CRLF
    out += b"REFERENCE 0,0" + CRLF
    if opts.media == MEDIA_CONTINUOUS:
        out += b"GAP 0 mm,0 mm" + CRLF
    elif opts.media == MEDIA_MARK:
        out += b"BLINE " + _mm1(opts.gap_mm) + b" mm," + _mm1(opts.gap_offset_mm) + b" mm" + CRLF
    else:
        gap = opts.gap_mm if opts.gap_mm != 0 else 3.0   # driver substitutes 3.0 for 0
        out += b"GAP " + _mm1(gap) + b" mm," + _mm1(opts.gap_offset_mm) + b" mm" + CRLF
    out += b"OFFSET %d mm" % int(round(opts.offset_mm)) + CRLF
    out += b"DIRECTION 0,0" + CRLF
    if opts.density is not None:
        out += b"DENSITY %d" % opts.density + CRLF
    if opts.speed is not None:
        out += b"SPEED %d" % opts.speed + CRLF
    if opts.post == POST_TEAR:
        out += b"SET TEAR ON" + CRLF
    elif opts.post == POST_PEEL:
        out += b"SET PEEL ON" + CRLF
    else:
        out += b"SET TEAR OFF" + CRLF
    out += b"CLS" + CRLF
    return bytes(out)


def build_job(bits: bytes, width_bytes: int, height: int,
              opts: Optional[JobOptions] = None,
              width_dots: Optional[int] = None) -> bytes:
    """One label: header + BITMAP + PRINT.

    width_dots is the label width used for SIZE when it is not a multiple of 8.
    """
    opts = opts or JobOptions()
    if width_dots is None:
        width_dots = width_bytes * 8
    if width_bytes * height != len(bits):
        raise D450Error("bitmap is %d bytes, expected %d"
                        % (len(bits), width_bytes * height))
    if width_bytes * 8 > MAX_WIDTH_DOTS:
        raise D450Error("bitmap is wider than the %d-dot print head" % MAX_WIDTH_DOTS)
    if height < 1:
        raise D450Error("bitmap has no rows")

    out = bytearray(build_header(width_dots, height, opts))
    # Like the vendor driver, send only the rows between the first and last
    # non-blank row and move the bitmap down by the rows skipped at the top.
    blank = b"\xff" * width_bytes
    top = 0
    while top < height and bits[top * width_bytes:(top + 1) * width_bytes] == blank:
        top += 1
    bottom = height
    while bottom > top and bits[(bottom - 1) * width_bytes:bottom * width_bytes] == blank:
        bottom -= 1
    if bottom > top:                      # a blank label is just fed, not imaged
        body = bits[top * width_bytes:bottom * width_bytes]
        out += b"BITMAP %d,%d,%d,%d,%d," % (opts.x, opts.y + top, width_bytes,
                                           bottom - top, 4 if opts.compress else 1)
        out += pack_bitmap(body) if opts.compress else body
        out += CRLF
    out += b"PRINT 1,%d" % opts.copies + CRLF
    return bytes(out)


def job_from_gray(gray: bytes, width: int, height: int,
                  opts: Optional[JobOptions] = None,
                  dither: str = DITHER_THRESHOLD,
                  rotate180: bool = False) -> bytes:
    """Grey page (0 = black) -> complete job, cropping to the print head."""
    if width > MAX_WIDTH_DOTS or height > MAX_HEIGHT_DOTS:
        new_w, new_h = min(width, MAX_WIDTH_DOTS), min(height, MAX_HEIGHT_DOTS)
        gray = crop_gray(gray, width, height, new_w, new_h)
        width, height = new_w, new_h
    if rotate180:
        gray = rotate180_gray(gray)
    bits, width_bytes = gray_to_bits(gray, width, height, dither)
    return build_job(bits, width_bytes, height, opts, width)


# --------------------------------------------------------------------------
# Job parsing (used by the tests and by `d450print --decode`)
# --------------------------------------------------------------------------

@dataclass
class ParsedLabel:
    commands: List[str]
    x: int
    y: int
    width_bytes: int
    height: int
    mode: int
    bits: bytes          # always uncompressed, 1 = white; rows actually sent
    copies: int

    @property
    def size_mm(self) -> Tuple[int, int]:
        for command in self.commands:
            if command.startswith("SIZE "):
                width, height = command[5:].split(",")
                return int(width.split()[0]), int(height.split()[0])
        raise D450Error("label has no SIZE command")

    def full_gray(self) -> Tuple[bytes, int, int]:
        """Whole label as grey (0 black), using SIZE for the height."""
        width = self.width_bytes * 8
        height = max(mm_to_dots(self.size_mm[1]), self.y + self.height)
        white = b"\xff" * width
        return (white * self.y + bits_to_gray(self.bits, self.width_bytes, self.height)
                + white * (height - self.y - self.height)), width, height


def parse_job(data: bytes) -> List[ParsedLabel]:
    labels: List[ParsedLabel] = []
    pos = 0
    commands: List[str] = []
    image = None
    while pos < len(data):
        if data.startswith(b"BITMAP ", pos):
            cursor = pos + 7
            fields = []
            for _ in range(5):
                comma = data.index(b",", cursor)
                fields.append(int(data[cursor:comma]))
                cursor = comma + 1
            x, y, width_bytes, height, mode = fields
            raw_len = width_bytes * height
            if mode == 4:
                bits, cursor = unpack_bitmap(data, cursor, raw_len)
            else:
                bits = data[cursor:cursor + raw_len]
                cursor += raw_len
            if len(bits) != raw_len:
                raise D450Error("truncated BITMAP data")
            if data[cursor:cursor + 2] != CRLF:
                raise D450Error("BITMAP data not followed by CRLF")
            image = (x, y, width_bytes, height, mode, bits)
            pos = cursor + 2
            continue
        end = data.index(CRLF, pos)
        line = data[pos:end].decode("ascii")
        pos = end + 2
        if line.startswith("PRINT "):
            x, y, width_bytes, height, mode, bits = image or (0, 0, 0, 0, 1, b"")
            labels.append(ParsedLabel(commands, x, y, width_bytes, height, mode,
                                      bits, int(line.split(",")[1])))
            commands, image = [], None
        else:
            commands.append(line)
    if commands or image:
        raise D450Error("trailing data without PRINT")
    return labels


def bits_to_gray(bits: bytes, width_bytes: int, height: int) -> bytes:
    """Packed rows -> 8-bit grey (0 black / 255 white), width_bytes*8 wide."""
    table = {ord("0"): 0, ord("1"): 255}
    out = bytearray()
    for row in range(height):
        chunk = bits[row * width_bytes:(row + 1) * width_bytes]
        text = bin(int.from_bytes(chunk, "big"))[2:].zfill(width_bytes * 8)
        out += text.encode("ascii").translate(
            bytes(table.get(i, 0) for i in range(256)))
    return bytes(out)


# --------------------------------------------------------------------------
# CUPS / PWG raster reading
# --------------------------------------------------------------------------

@dataclass
class RasterPage:
    width: int
    height: int
    copies: int
    gray: bytes          # 8-bit, 0 = black


_SYNC = {
    b"RaSt": (">", 1, False), b"tSaR": ("<", 1, False),
    b"RaS2": (">", 2, True), b"2SaR": ("<", 2, True),
    b"RaS3": (">", 3, False), b"3SaR": ("<", 3, False),
}

CSPACE_W, CSPACE_RGB, CSPACE_K, CSPACE_SW, CSPACE_SRGB = 0, 1, 3, 18, 19


def _read_exact(stream: BinaryIO, count: int) -> bytes:
    chunks = []
    remaining = count
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise D450Error("raster stream ended early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_compressed_page(stream: BinaryIO, height: int, bytes_per_line: int,
                          bytes_per_pixel: int) -> bytes:
    out = bytearray()
    rows = 0
    while rows < height:
        repeat = _read_exact(stream, 1)[0] + 1
        line = bytearray()
        while len(line) < bytes_per_line:
            code = _read_exact(stream, 1)[0]
            if code & 0x80:
                line += _read_exact(stream, (257 - code) * bytes_per_pixel)
            else:
                line += _read_exact(stream, bytes_per_pixel) * (code + 1)
        if len(line) != bytes_per_line:
            raise D450Error("corrupt raster line")
        repeat = min(repeat, height - rows)
        out += bytes(line) * repeat
        rows += repeat
    return bytes(out)


_BIT_TO_GRAY_K = None


def _page_to_gray(data: bytes, width: int, height: int, bits_per_pixel: int,
                  bytes_per_line: int, cspace: int) -> bytes:
    if bits_per_pixel == 8:
        rows = [data[r * bytes_per_line:r * bytes_per_line + width] for r in range(height)]
        gray = b"".join(rows)
        if cspace == CSPACE_K:
            gray = gray.translate(bytes(255 - v for v in range(256)))
        elif cspace not in (CSPACE_W, CSPACE_SW):
            raise D450Error("unsupported 8-bit colour space %d" % cspace)
        return gray
    if bits_per_pixel == 1:
        black_is_one = cspace == CSPACE_K
        table = bytes((0 if black_is_one else 255) if c == 0x31 else
                      (255 if black_is_one else 0) for c in range(256))
        out = bytearray()
        for r in range(height):
            chunk = data[r * bytes_per_line:(r + 1) * bytes_per_line]
            text = bin(int.from_bytes(chunk, "big"))[2:].zfill(bytes_per_line * 8)[:width]
            out += text.encode("ascii").translate(table)
        return bytes(out)
    if bits_per_pixel == 24 and cspace in (CSPACE_RGB, CSPACE_SRGB):
        out = bytearray(width * height)
        for r in range(height):
            row = data[r * bytes_per_line:r * bytes_per_line + width * 3]
            red, green, blue = row[0::3], row[1::3], row[2::3]
            out[r * width:(r + 1) * width] = bytes(
                (299 * a + 587 * b + 114 * c) // 1000
                for a, b, c in zip(red, green, blue))
        return bytes(out)
    raise D450Error("unsupported raster format: %d bits per pixel, colour space %d"
                    % (bits_per_pixel, cspace))


def read_raster(stream: BinaryIO) -> Iterator[RasterPage]:
    """Yield pages from a CUPS raster (v1/v2/v3) or PWG raster stream."""
    sync = _read_exact(stream, 4)
    if sync not in _SYNC:
        raise D450Error("not a CUPS raster stream (sync %r)" % sync)
    endian, version, compressed = _SYNC[sync]
    header_len = 420 if version == 1 else 1796
    while True:
        first = stream.read(1)
        if not first:
            return
        header = first + _read_exact(stream, header_len - 1)
        copies = struct.unpack_from(endian + "I", header, 340)[0]
        width, height = struct.unpack_from(endian + "2I", header, 372)
        bits_per_pixel, bytes_per_line = struct.unpack_from(endian + "2I", header, 388)
        cspace = struct.unpack_from(endian + "I", header, 400)[0]
        if not (0 < width <= 65536 and 0 < height <= 1 << 20
                and bytes_per_line >= (width * bits_per_pixel + 7) // 8):
            raise D450Error("implausible raster header (%dx%d, %d bytes/line)"
                            % (width, height, bytes_per_line))
        if compressed:
            data = _read_compressed_page(stream, height, bytes_per_line,
                                         max(1, bits_per_pixel // 8))
        else:
            data = _read_exact(stream, bytes_per_line * height)
        yield RasterPage(width, height, max(1, copies),
                         _page_to_gray(data, width, height, bits_per_pixel,
                                       bytes_per_line, cspace))
