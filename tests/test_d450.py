import io
import os
import struct
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import d450tspl as tspl  # noqa: E402

FILTER = os.path.join(ROOT, "filter", "rastertod450")
PPD = os.path.join(ROOT, "ppd", "D450.ppd")


def raster(pages, sync=b"3SaR", bits=8, cspace=tspl.CSPACE_W, copies=1):
    """Build a CUPS raster stream from [(width, height, pixel bytes)]."""
    endian = "<" if sync[0:1] != b"R" else ">"
    out = bytearray(sync)
    for width, height, data in pages:
        bytes_per_line = (width * bits + 7) // 8
        header = bytearray(1796)
        struct.pack_into(endian + "2I", header, 276, 203, 203)
        struct.pack_into(endian + "I", header, 340, copies)
        struct.pack_into(endian + "2I", header, 372, width, height)
        struct.pack_into(endian + "3I", header, 384, bits, bits, bytes_per_line)
        struct.pack_into(endian + "I", header, 400, cspace)
        out += header
        if sync in (b"RaS2", b"2SaR"):
            for row in range(height):
                line = data[row * bytes_per_line:(row + 1) * bytes_per_line]
                out.append(0)                               # line repeated once
                for start in range(0, len(line), 128):      # literal runs
                    chunk = line[start:start + 128]
                    if len(chunk) == 1:
                        out += bytes([0]) + chunk
                    else:
                        out += bytes([257 - len(chunk)]) + chunk
        else:
            out += data
    return bytes(out)


class HeaderTest(unittest.TestCase):
    def test_default_header_matches_vendor_driver_sequence(self):
        self.assertEqual(
            tspl.build_header(812, 1218, tspl.JobOptions()),
            b"SIZE 102 mm,152 mm\r\nREFERENCE 0,0\r\nGAP 3.0 mm,0.0 mm\r\n"
            b"OFFSET 0 mm\r\nDIRECTION 0,0\r\nDENSITY 8\r\nSPEED 5\r\n"
            b"SET TEAR OFF\r\nCLS\r\n")

    def test_media_and_post_variants(self):
        cont = tspl.build_header(400, 200, tspl.JobOptions(media="continuous", post="tear"))
        self.assertIn(b"GAP 0 mm,0 mm\r\n", cont)
        self.assertIn(b"SET TEAR ON\r\n", cont)
        mark = tspl.build_header(400, 200, tspl.JobOptions(media="mark", gap_mm=2, post="peel"))
        self.assertIn(b"BLINE 2.0 mm,0.0 mm\r\n", mark)
        self.assertIn(b"SET PEEL ON\r\n", mark)

    def test_zero_gap_becomes_three_like_the_driver(self):
        self.assertIn(b"GAP 3.0 mm,", tspl.build_header(400, 200, tspl.JobOptions(gap_mm=0)))

    def test_printer_defaults_omit_density_and_speed(self):
        header = tspl.build_header(400, 200, tspl.JobOptions(density=None, speed=None))
        self.assertNotIn(b"DENSITY", header)
        self.assertNotIn(b"SPEED", header)

    def test_invalid_options_are_rejected(self):
        for bad in (dict(density=16), dict(speed=1), dict(media="x"), dict(copies=0)):
            with self.assertRaises(tspl.D450Error):
                tspl.build_header(400, 200, tspl.JobOptions(**bad))

    def test_size_rounding(self):
        self.assertEqual(tspl.dots_to_mm(812), 102)
        self.assertEqual(tspl.dots_to_mm(1218), 152)
        self.assertEqual(tspl.dots_to_mm(406), 51)


class BitmapTest(unittest.TestCase):
    def test_polarity_one_is_white_msb_first(self):
        gray = bytes([0, 255, 255, 255, 255, 255, 255, 255, 255, 0])
        bits, width_bytes = tspl.gray_to_bits(gray, 10, 1)
        self.assertEqual(width_bytes, 2)
        self.assertEqual(bits, bytes([0b01111111, 0b10111111]))   # padding is white

    def test_threshold_boundary(self):
        bits, _ = tspl.gray_to_bits(bytes([127, 128] + [255] * 6), 8, 1)
        self.assertEqual(bits, bytes([0b01111111]))

    def test_dithers_keep_extremes_and_halve_mid_grey(self):
        for mode in (tspl.DITHER_ORDERED, tspl.DITHER_DIFFUSION):
            white, _ = tspl.gray_to_bits(b"\xff" * 64 * 16, 64, 16, mode)
            black, _ = tspl.gray_to_bits(b"\x00" * 64 * 16, 64, 16, mode)
            self.assertEqual(white, b"\xff" * 8 * 16, mode)
            self.assertEqual(black, b"\x00" * 8 * 16, mode)
            mid, _ = tspl.gray_to_bits(b"\x80" * 64 * 64, 64, 64, mode)
            ones = sum(bin(b).count("1") for b in mid)
            self.assertAlmostEqual(ones / (64 * 64), 0.5, delta=0.05, msg=mode)

    def test_job_roundtrip_trims_blank_rows(self):
        width, height = 16, 10
        gray = bytearray(b"\xff" * width * height)
        gray[3 * width + 2] = 0
        gray[6 * width + 9] = 0
        job = tspl.job_from_gray(bytes(gray), width, height, tspl.JobOptions(copies=3))
        (label,) = tspl.parse_job(job)
        self.assertEqual((label.x, label.y, label.width_bytes, label.height, label.mode),
                         (0, 3, 2, 4, 1))
        self.assertEqual(label.copies, 3)
        self.assertTrue(job.endswith(b"\r\nPRINT 1,3\r\n"))
        restored = tspl.bits_to_gray(label.bits, 2, 4)
        self.assertEqual(restored, bytes(gray[3 * width:7 * width]))

    def test_blank_label_has_no_bitmap(self):
        job = tspl.job_from_gray(b"\xff" * 64, 8, 8)
        self.assertNotIn(b"BITMAP", job)
        (label,) = tspl.parse_job(job)
        self.assertEqual(label.height, 0)

    def test_rotate180(self):
        gray = bytes([0] + [255] * 15)                     # black dot top-left of 8x2
        job = tspl.job_from_gray(gray, 8, 2, rotate180=True)
        (label,) = tspl.parse_job(job)
        self.assertEqual((label.y, label.bits), (1, bytes([0b11111110])))

    def test_oversize_page_is_cropped(self):
        width = tspl.MAX_WIDTH_DOTS + 40
        job = tspl.job_from_gray(b"\x00" * width * 2, width, 2)
        (label,) = tspl.parse_job(job)
        self.assertEqual(label.width_bytes * 8, tspl.MAX_WIDTH_DOTS)

    def test_build_job_validates(self):
        with self.assertRaises(tspl.D450Error):
            tspl.build_job(b"\x00" * 3, 2, 2)
        with self.assertRaises(tspl.D450Error):
            tspl.build_job(b"\x00" * 109, 109, 1)


class CompressionTest(unittest.TestCase):
    def test_framing_is_4k_chunks_with_le_length_and_zero_terminator(self):
        data = bytes(range(256)) * 40                      # 10240 bytes -> 3 chunks
        packed = tspl.pack_bitmap(data)
        pos, chunks = 0, []
        while True:
            (length,) = struct.unpack_from("<I", packed, pos)
            pos += 4
            if length == 0:
                break
            chunks.append(packed[pos:pos + length])
            pos += length
        self.assertEqual(pos, len(packed))
        self.assertEqual(len(chunks), 3)
        sizes = [4096, 4096, 2048]
        self.assertEqual(b"".join(tspl.lzo1x_decompress(c, n) for c, n in zip(chunks, sizes)),
                         data)

    def test_compressed_job_roundtrip(self):
        width, height = 812, 600
        gray = bytearray(b"\xff" * width * height)
        for row in range(50, 550):
            gray[row * width + 100:row * width + 700] = b"\x00" * 600
        plain = tspl.job_from_gray(bytes(gray), width, height)
        packed = tspl.job_from_gray(bytes(gray), width, height, tspl.JobOptions(compress=True))
        self.assertLess(len(packed), len(plain) // 10)
        a, b = tspl.parse_job(plain)[0], tspl.parse_job(packed)[0]
        self.assertEqual((a.mode, b.mode), (1, 4))
        self.assertEqual(a.bits, b.bits)
        self.assertEqual((a.y, a.height), (b.y, b.height))


class RasterTest(unittest.TestCase):
    def setUp(self):
        self.gray = bytes((x * 7 + y * 13) % 256 for y in range(5) for x in range(12))

    def test_v3_both_byte_orders(self):
        for sync in (b"3SaR", b"RaS3"):
            (page,) = tspl.read_raster(io.BytesIO(raster([(12, 5, self.gray)], sync, copies=2)))
            self.assertEqual((page.width, page.height, page.copies), (12, 5, 2))
            self.assertEqual(page.gray, self.gray)

    def test_v2_compressed_and_multiple_pages(self):
        stream = raster([(12, 5, self.gray), (12, 5, self.gray[::-1])], b"RaS2")
        pages = list(tspl.read_raster(io.BytesIO(stream)))
        self.assertEqual([p.gray for p in pages], [self.gray, self.gray[::-1]])

    def test_v2_repeat_codes(self):
        header = b"2SaR" + raster([(4, 3, b"")], b"3SaR")[4:]
        body = bytes([2, 3, 0x55])                         # 3 lines of 4 x 0x55
        (page,) = tspl.read_raster(io.BytesIO(header + body))
        self.assertEqual(page.gray, b"\x55" * 12)

    def test_black_colourspace_and_one_bit(self):
        (page,) = tspl.read_raster(io.BytesIO(raster([(2, 1, b"\xff\x00")], cspace=tspl.CSPACE_K)))
        self.assertEqual(page.gray, b"\x00\xff")
        (page,) = tspl.read_raster(io.BytesIO(
            raster([(10, 1, bytes([0b10000000, 0b01000000]))], bits=1, cspace=tspl.CSPACE_K)))
        self.assertEqual(page.gray, bytes([0] + [255] * 8 + [0]))

    def test_bad_streams(self):
        with self.assertRaises(tspl.D450Error):
            list(tspl.read_raster(io.BytesIO(b"%PDF-1.4")))
        with self.assertRaises(tspl.D450Error):
            list(tspl.read_raster(io.BytesIO(raster([(12, 5, self.gray)])[:-10])))


class FilterTest(unittest.TestCase):
    def run_filter(self, options="", env=None, stream=None):
        environment = dict(os.environ, PPD=PPD)
        environment.pop("DEVICE_URI", None)
        environment.update(env or {})
        if stream is None:
            gray = bytearray(b"\xff" * 80 * 40)
            gray[10 * 80:11 * 80] = b"\x00" * 80
            stream = raster([(80, 40, bytes(gray))], copies=2)
        return subprocess.run([FILTER, "1", "user", "title", "1", options],
                              input=stream, capture_output=True, env=environment)

    def test_defaults_come_from_ppd(self):
        result = self.run_filter()
        self.assertEqual(result.returncode, 0, result.stderr)
        (label,) = tspl.parse_job(result.stdout)
        self.assertEqual(label.commands, [
            "SIZE 10 mm,5 mm", "REFERENCE 0,0", "GAP 3.0 mm,0.0 mm", "OFFSET 0 mm",
            "DIRECTION 0,0", "DENSITY 8", "SPEED 5", "SET TEAR OFF", "CLS"])
        self.assertEqual((label.y, label.height, label.mode, label.copies), (10, 1, 1, 2))
        self.assertIn(b"PAGE: 1 2", result.stderr)

    def test_job_options_override(self):
        result = self.run_filter("d450Density=12 d450Speed=Default d450Media=Continuous "
                                 "d450Post=TearOff d450Compress=On PageSize=w288h432")
        self.assertEqual(result.returncode, 0, result.stderr)
        (label,) = tspl.parse_job(result.stdout)
        self.assertIn("DENSITY 12", label.commands)
        self.assertIn("GAP 0 mm,0 mm", label.commands)
        self.assertIn("SET TEAR ON", label.commands)
        self.assertFalse([c for c in label.commands if c.startswith("SPEED")])
        self.assertEqual(label.mode, 4)

    def test_bluetooth_uri_turns_compression_on(self):
        result = self.run_filter(env={"DEVICE_URI": "bluetooth://AABBCCDDEEFF"})
        self.assertEqual(tspl.parse_job(result.stdout)[0].mode, 4)

    def test_bad_input_fails_cleanly(self):
        result = self.run_filter(stream=b"not a raster")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"ERROR:", result.stderr)
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
