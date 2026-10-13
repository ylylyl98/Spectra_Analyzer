from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import DatasetSession, Workspace
from spectra_app.workbook_state import (add_curve, create_comparison, create_workbook,
    decode_document_key, document_key, document_record_key, ensure_dataset_sheet,
    register_xy_sheet, remove_sheet, remove_workbook, resolve_curve, set_sheet_mapping,
    sheet_column_values, sheet_columns, sheet_shape, sheet_values, validate_workbook_state)


class WorkbookStateTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        self.book = create_workbook(self.workspace, 'Measurements')

    def add(self, name, data, columns):
        return register_xy_sheet(self.workspace, self.book.workbook_id, name, np.array(data, dtype=float),
                                 columns, source_path=Path(name + '.csv'))

    def test_independent_grids_columns_and_live_data(self):
        a = self.add('A', [[10, 1], [20, 2]], ['Signal', 'X'])
        b = self.add('B', [[99, 30, 4], [98, 50, 8], [97, 70, 12]], ['Other', 'Signal', 'X'])
        graph = create_comparison(self.workspace, 'Cross-file')
        ca = add_curve(self.workspace, graph.comparison_id, a.sheet_id, 1, 0)
        cb = add_curve(self.workspace, graph.comparison_id, b.sheet_id, 2, 1)
        for curve, x, y in ((ca, [1, 2], [10, 20]), (cb, [4, 8, 12], [30, 50, 70])):
            actual_x, actual_y = resolve_curve(self.workspace, curve)
            np.testing.assert_array_equal(actual_x, x)
            np.testing.assert_array_equal(actual_y, y)
        a.data[0, 0] = 42
        self.assertEqual(resolve_curve(self.workspace, ca)[1][0], 42)
        set_sheet_mapping(self.workspace, b.sheet_id, 2, [0, 1])
        self.assertEqual(b.y_columns, [0, 1])
        with self.assertRaises(ValueError):
            add_curve(self.workspace, graph.comparison_id, a.sheet_id, 0, 2)
        with self.assertRaises(ValueError):
            set_sheet_mapping(self.workspace, a.sheet_id, 0, [1], mode='processed')

    def test_duplicate_import_reuses_without_replacing_and_explicit_duplicate(self):
        a = self.add('A', [[1, 2]], ['X', 'Y'])
        b = self.add('A', [[4, 5]], ['X', 'Y'])
        self.assertIs(a, b)
        self.assertEqual(a.data[0, 0], 1)
        duplicate = register_xy_sheet(self.workspace, self.book.workbook_id, 'Again',
            np.array([[4., 5.]]), ['X', 'Y'], source_path=Path('A.csv'), allow_duplicate=True)
        self.assertNotEqual(a.sheet_id, duplicate.sheet_id)

    def test_membership_and_source_removal_prune_curves(self):
        a = self.add('A', [[1, 2]], ['X', 'Y'])
        graph = create_comparison(self.workspace, 'Graph')
        add_curve(self.workspace, graph.comparison_id, a.sheet_id, 0, 1)
        other = create_workbook(self.workspace, 'Other')
        other.sheet_ids.append(a.sheet_id)
        with self.assertRaisesRegex(ValueError, 'owner'):
            validate_workbook_state(self.workspace)
        other.sheet_ids.clear()
        remove_sheet(self.workspace, a.sheet_id)
        self.assertFalse(graph.curves)
        self.assertEqual(self.book.sheet_ids, [])
        remove_workbook(self.workspace, self.book.workbook_id)
        self.assertNotIn(self.book.workbook_id, self.workspace.workbooks)

    def test_dat_orientation_live_processing_replacement_and_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'matrix.dat'
            path.write_text('Energy(eV) 0 1\n1 10 20\n2 30 40\n', encoding='utf-8')
            cube = load_dat_cube(path)
            session = self.workspace.register(DatasetSession(source_path=path, base_cube=cube, current_cube=cube))
            sheet = ensure_dataset_sheet(self.workspace, session.dataset_id)
            self.assertIsNone(sheet.data)
            self.assertEqual(sheet_shape(self.workspace, sheet), (2, 3))
            self.assertEqual(len(sheet_columns(self.workspace, sheet)), 3)
            np.testing.assert_array_equal(sheet_values(self.workspace, sheet), [[1, 10, 20], [2, 30, 40]])
            self.assertTrue(np.shares_memory(sheet_column_values(self.workspace, sheet, 1), cube.z))
            graph = create_comparison(self.workspace, 'DAT comparison')
            raw = add_curve(self.workspace, graph.comparison_id, sheet.sheet_id, 0, 1)
            processed = add_curve(self.workspace, graph.comparison_id, sheet.sheet_id, 0, 2, mode='processed')
            session.current_cube = replace(cube, z=cube.z * 3)
            np.testing.assert_array_equal(resolve_curve(self.workspace, raw)[1], [10, 30])
            np.testing.assert_array_equal(resolve_curve(self.workspace, processed)[1], [60, 120])
            smaller = replace(cube, gate=cube.gate[:1], z=cube.z[:1])
            candidate = DatasetSession(source_path=path, base_cube=smaller, current_cube=smaller)
            self.workspace.register(candidate, replace_existing=True)
            self.assertIn(raw.curve_id, graph.curves)
            self.assertNotIn(processed.curve_id, graph.curves)
            self.workspace.remove(candidate.dataset_id)
            self.assertNotIn(sheet.sheet_id, self.workspace.sheets)
            self.assertFalse(graph.curves)

    def test_typed_document_keys_and_legacy_conversion(self):
        self.assertEqual(document_key('heatmap', 'owner', 'default'), ('owner', 'default'))
        self.assertEqual(document_key('workbook', 'book'), ('workbook', 'book', ''))
        self.assertEqual(document_key('xy', 'comparison'), ('xy', 'comparison', ''))
        self.assertEqual(document_record_key({'dataset_id': 'xy', 'graph_id': 'book'}), ('xy', 'book'))
        for invalid in (['xy', 'book', 'bad'], ['unknown', 'id', ''], ['id'], [1, 'default']):
            with self.assertRaises(ValueError):
                decode_document_key(invalid)
