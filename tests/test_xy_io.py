from pathlib import Path
import tempfile
import unittest

import numpy as np

from spectra_app.xy_io import XYParseError, _strip_comment, load_xy, parse_xy


class XYIOTests(unittest.TestCase):
    def test_comment_free_lines_do_not_scan_characters(self):
        class CountedLine(str):
            indexed_reads = 0

            def __getitem__(self, key):
                self.indexed_reads += 1
                return super().__getitem__(key)

        for source in ('', '1.25\t' * 1024, '  波长\u2003强度  ',
                       '"Count ""adjusted"", signal",X'):
            with self.subTest(source=source[:40]):
                line = CountedLine(source)
                self.assertIs(_strip_comment(line), line)
                self.assertEqual(line.indexed_reads, 0)

    def test_comment_free_quoted_and_unicode_tables_keep_values_and_labels(self):
        for source, names in (
                ('\ufeff"计数 ""校准"", 信号", λ / nm \n"10","2"\n20,NA\n',
                 ['计数 "校准", 信号', 'λ / nm']),
                ('"Count ""adjusted"""\t波长\n10\t2\n20\t\n',
                 ['Count "adjusted"', '波长']),
                ('  强度\u2003波长  \n 10\u20032 \n20\u2003NA\n', ['强度', '波长'])):
            with self.subTest(source=source):
                result = parse_xy(source)
                self.assertEqual(result.column_names, names)
                self.assertTrue(result.has_header)
                np.testing.assert_equal(result.data, [[10, 2], [20, np.nan]])

    def test_headers_comments_missing_values_and_order(self):
        result = parse_xy('# measurement\nSignal,X,Other\n10,2,\n20,1,3e-2 # note\n')
        self.assertEqual(result.column_names, ['Signal', 'X', 'Other'])
        np.testing.assert_equal(result.data, [[10, 2, np.nan], [20, 1, .03]])

    def test_whitespace_tabs_numeric_headerless_and_missing_tokens(self):
        for source in ('3 4e2\n1 NA\n', '3\t4e2\n1\t\n'):
            with self.subTest(source=source):
                result = parse_xy(source)
                self.assertEqual(result.column_names, ['Column 1', 'Column 2'])
                np.testing.assert_equal(result.data, [[3, 400], [1, np.nan]])

    def test_delimiter_only_rows_preserve_missing_measurement_gaps(self):
        for delimiter in (',', '\t'):
            with self.subTest(delimiter=delimiter):
                source = '\n'.join((f'X{delimiter}Y', f'1{delimiter}2', delimiter,
                                    '', '   ', '# comment', '; comment', '// comment',
                                    f' {delimiter} ', f'3{delimiter}4'))
                result = parse_xy(source)
                np.testing.assert_equal(result.data, [[1, 2], [np.nan, np.nan],
                                                     [np.nan, np.nan], [3, 4]])
                with self.assertRaisesRegex(XYParseError, 'line 11, column 2'):
                    parse_xy(source + f'\n5{delimiter}bad\n')

    def test_hash_comments_preserve_quoted_fields_and_escaped_quotes(self):
        for header, names in (('"Count #1",X', ['Count #1', 'X']),
                              ('"Count ""#1"", adjusted",X', ['Count "#1", adjusted', 'X'])):
            with self.subTest(header=header):
                result = parse_xy(header + '\n"10","2"# comment, ignored\n')
                self.assertEqual(result.column_names, names)
                np.testing.assert_equal(result.data, [[10, 2]])

    def test_blank_tab_lines_remain_blank_outside_tsv(self):
        for source in ('X,Y\n\t\n1,2\n', 'X Y\n\t\n1 2\n'):
            with self.subTest(source=source):
                np.testing.assert_equal(parse_xy(source).data, [[1, 2]])

    def test_comment_handling_keeps_unquoted_inference_and_strict_rows(self):
        result = parse_xy('X Y # comment, with tabs\t\n1 2# note\n')
        self.assertEqual(result.column_names, ['X', 'Y'])
        np.testing.assert_equal(result.data, [[1, 2]])
        for source in ('X,Y\n"1,2# unclosed\n', 'X,Y\n"1"x,2# trailing\n'):
            with self.subTest(source=source):
                with self.assertRaisesRegex(XYParseError, 'Invalid delimited row at line 2'):
                    parse_xy(source)

    def test_errors_report_physical_line_number(self):
        for source, line in (('# note\nX Y\n1 2\n3 junk\n', 4),
                             ('X,Y\n1,2\n3,4,5\n', 3), ('1,broken\n2,3\n', 1)):
            with self.subTest(source=source):
                with self.assertRaisesRegex(XYParseError, f'line {line}'):
                    parse_xy(source)
        for source in ('', '# comment\n', 'X,Y\n'):
            with self.assertRaises(XYParseError):
                parse_xy(source)

    def test_explicit_dat_load_does_not_infer_or_sort_axes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'numeric.dat'
            path.write_text('X Y\n700 2\n400 9\n', encoding='utf-8')
            result = load_xy(path)
            self.assertEqual(result.source_path, path)
            np.testing.assert_array_equal(result.data, [[700, 2], [400, 9]])

    def test_dat_style_header_can_include_numeric_gate_labels(self):
        result = parse_xy('Energy(eV) 0 1\n2 10 20\n1 30 40\n')
        self.assertEqual(result.column_names, ['Energy(eV)', '0', '1'])
        np.testing.assert_array_equal(result.data[:, 0], [2, 1])
