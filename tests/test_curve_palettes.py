import tempfile
import unittest
from pathlib import Path

from matplotlib import colormaps
from matplotlib.colors import to_hex

from spectra_app.curve_palettes import CURVE_PALETTES, palette_colors, parse_palette_text, read_palette_file


class CurvePaletteTests(unittest.TestCase):
    def test_available_palette_names_are_the_requested_builtins_in_order(self):
        self.assertEqual(CURVE_PALETTES, (
            'tab10', 'tab20', 'tab20b', 'tab20c', 'Set1', 'Set2', 'Set3',
            'Dark2', 'Paired', 'Accent', 'Pastel1', 'Pastel2', 'viridis',
            'plasma', 'inferno', 'magma', 'cividis', 'turbo', 'coolwarm',
            'RdBu', 'Spectral',
        ))

    def test_qualitative_palettes_keep_native_order_and_cycle(self):
        for name in CURVE_PALETTES[:12]:
            with self.subTest(name=name):
                native = [to_hex(color) for color in colormaps[name].colors]
                self.assertEqual(palette_colors(name, len(native) + 2), native + native[:2])
                self.assertEqual(palette_colors(name, 1), native[:1])

    def test_continuous_palettes_sample_the_interior_and_single_midpoint(self):
        for name in CURVE_PALETTES[12:]:
            with self.subTest(name=name):
                cmap = colormaps[name]
                self.assertEqual(palette_colors(name, 3),
                                 [to_hex(cmap(position)) for position in (.1, .5, .9)])
                self.assertEqual(palette_colors(name, 1), [to_hex(cmap(.5))])
        self.assertEqual(palette_colors('viridis', 3), ['#482475', '#21918c', '#bddf26'])

    def test_zero_count_is_empty_and_results_do_not_share_mutable_state(self):
        self.assertEqual(palette_colors('tab10', 0), [])
        first = palette_colors('tab10', 2)
        first[0] = '#ffffff'
        self.assertEqual(palette_colors('tab10', 2), ['#1f77b4', '#ff7f0e'])

    def test_invalid_palette_name_or_count_is_rejected(self):
        for name in ('unknown', 'jet', 'Tab10', '', None, []):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'palette'):
                palette_colors(name, 2)
        for count in (-1, 1.5, True, False, '2', None):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'count'):
                palette_colors('tab10', count)


class PaletteTextTests(unittest.TestCase):
    def test_hex_text_csv_headers_bom_and_comments_are_normalized(self):
        text = '\ufeff# Exported colors\n\ncolor\n#F00\n#Aa00fF\n003399\n; note\n// note\n'
        self.assertEqual(parse_palette_text(text), ['#ff0000', '#aa00ff', '#003399'])
        self.assertEqual(parse_palette_text('"HEX"\n"#AABBCC"\n"#0f0"\n'),
                         ['#aabbcc', '#00ff00'])

    def test_byte_rgb_accepts_csv_or_whitespace_and_header(self):
        self.assertEqual(parse_palette_text('R,G,B\n255, 0, 128\n0 255 0\n0\t0\t255'),
                         ['#ff0080', '#00ff00', '#0000ff'])
        self.assertEqual(parse_palette_text('"255","0","128"'), ['#ff0080'])

    def test_decimal_notation_establishes_normalized_scale_for_the_whole_file(self):
        self.assertEqual(parse_palette_text('r g b\n1,0,0\n0.0, 0.5, 1\n0 1 0'),
                         ['#ff0000', '#0080ff', '#00ff00'])
        self.assertEqual(parse_palette_text('1e0 0e0 5e-1'), ['#ff0080'])

    def test_integer_zero_one_values_use_byte_rgb(self):
        self.assertEqual(parse_palette_text('1,0,0\n0 1 1'), ['#010000', '#000101'])

    def test_mixed_rgb_scales_report_the_offending_physical_line(self):
        with self.assertRaisesRegex(ValueError, r'Line 3:.*[Nn]ormalized.*integer'):
            parse_palette_text('# Comment\nR,G,B\n255,0,0\n0.5,0,1')
        with self.assertRaisesRegex(ValueError, r'Line 2:.*[Nn]ormalized'):
            parse_palette_text('0.5,0,1\n255,0,0')

    def test_rgb_ranges_nonfinite_values_and_malformed_rows_fail_with_line_numbers(self):
        invalid_rows = (
            '-1,0,0', '256,0,0', '0.1,1.1,0', 'nan,0,0', 'inf,0,0',
            '1e309,0,0', '0,0', '0,0,0,0', '255.0,0,0', '#GGGGGG',
            '#12345', '#abcd', '#12345678', '#ff0000,', 'red',
            '0.5,0.0,1.0 # inline comment', '__import__("os")',
            '"#ff0000', '0,,0', '0 0 0 extra',
        )
        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, r'Line 3:'):
                parse_palette_text('# Comment\n\n' + row)

    def test_headers_are_only_allowed_once_before_colors(self):
        for text in ('hex\nhex\n#123456', '#123456\ncolor', 'r,g,b\nr,g,b\n1,2,3'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, r'Line 2:'):
                parse_palette_text(text)

    def test_invalid_last_row_never_returns_a_partial_palette(self):
        with self.assertRaisesRegex(ValueError, r'Line 3:'):
            parse_palette_text('#ff0000\n#00ff00\ninvalid')
        self.assertEqual(parse_palette_text('#ff0000\n#00ff00'), ['#ff0000', '#00ff00'])

    def test_empty_comment_only_and_header_only_input_is_rejected(self):
        for text in ('', '\n \n', '# Comment\n; comment\n// comment', 'HEX\n'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'no colors'):
                parse_palette_text(text)

    def test_input_size_and_color_count_limits_are_enforced(self):
        self.assertEqual(len(parse_palette_text('#123456\n' * 4096)), 4096)
        with self.assertRaisesRegex(ValueError, r'Line 4097:.*4096'):
            parse_palette_text('#123456\n' * 4097)
        with self.assertRaisesRegex(ValueError, '1 MiB'):
            parse_palette_text('x' * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, '1 MiB'):
            parse_palette_text('# ' + '\u00e9' * (512 * 1024))


class PaletteFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'colors.csv'

    def test_reads_utf8_bom_and_plain_utf8(self):
        for encoding in ('utf-8', 'utf-8-sig'):
            with self.subTest(encoding=encoding):
                self.path.write_text('hex\n#1A2B3C\n#F00', encoding=encoding)
                self.assertEqual(read_palette_file(self.path), ['#1a2b3c', '#ff0000'])

    def test_invalid_content_encoding_missing_file_and_oversize_have_useful_errors(self):
        self.path.write_text('hex\n#123456\n#GGGGGG', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, r'colors.csv.*Line 3:'):
            read_palette_file(self.path)
        self.path.write_bytes(b'\xff\xfe\x00')
        with self.assertRaisesRegex(ValueError, r'colors.csv.*UTF-8'):
            read_palette_file(self.path)
        self.path.write_bytes(b' ' * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, r'colors.csv.*1 MiB'):
            read_palette_file(self.path)
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, r'colors.csv'):
            read_palette_file(self.path)


if __name__ == '__main__':
    unittest.main()
