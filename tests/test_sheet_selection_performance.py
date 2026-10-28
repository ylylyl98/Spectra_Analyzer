"""Whole-column mapping reads selection rectangles without visiting every cell."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest

from spectra_app.workbook_page import SheetTableModel
from tests.workbook_quick_helpers import pump, item, point_in, table_key
from spectra_app.workbook_state import create_workbook, register_xy_sheet

from tests.quick_migration_case import QuickMigrationCase


class SheetSelectionPerformanceTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertFalse(self.quick_errors, self.quick_errors))

    def make_page(self, rows=6, columns=6):
        workspace = self.controller.workspace
        book = create_workbook(workspace)
        sheet = register_xy_sheet(workspace, book.workbook_id, 'Selection',
            np.zeros((rows, columns)), [f'Column {column}' for column in range(columns)])
        sheet.y_columns = [2, 4]
        workbook = self.controller.open_workbook(book.workbook_id)
        self.host.window.resize(1600, 800)
        pump()
        return workbook.current_sheet, sheet

    def select_rectangles(self, page, rectangles):
        selection = QItemSelection()
        for top, left, bottom, right in rectangles:
            selection.select(page.model.index(top, left), page.model.index(bottom, right))
        page.selection_model.select(selection, QItemSelectionModel.ClearAndSelect)

    def test_tall_select_all_maps_every_y_without_per_cell_flags_queries(self):
        flags_calls = 0
        original_flags = SheetTableModel.flags

        def counted_flags(model, index):
            nonlocal flags_calls
            flags_calls += 1
            return original_flags(model, index)

        # Qt caches virtual overrides when the model is constructed.
        with patch.object(SheetTableModel, 'flags', counted_flags):
            page, sheet = self.make_page(1340, 302)
            selection = page.selection_model
            flags_calls = 0
            page.model.index(0, 0).flags()
            self.assertGreater(flags_calls, 0, 'The Qt virtual-call counter must be active')
            flags_calls = 0
            with patch.object(selection, 'selectedColumns', wraps=selection.selectedColumns) as selected_columns:
                self.assertIs(selection.selectedColumns, selected_columns)
                page.selectAll()
                self.assertEqual(sheet.y_columns, list(range(1, 302)))
                # Count only the action; deferred paints can inspect visible cells.
                self.assertLess(flags_calls, 20 * page.model.columnCount(),
                                'Select All must not query flags for the entire matrix')
                selected_columns.assert_not_called()

    def test_accessible_corner_and_ctrl_a_do_not_enumerate_selected_cells(self):
        flags_calls = 0
        original_flags = SheetTableModel.flags

        def counted_flags(model, index):
            nonlocal flags_calls
            flags_calls += 1
            return original_flags(model, index)

        was_active = QAccessible.isActive()
        self.addCleanup(QAccessible.setActive, was_active)
        with patch.object(SheetTableModel, 'flags', counted_flags):
            page, sheet = self.make_page(1340, 302)
            QAccessible.setActive(True)
            self.assertTrue(QAccessible.isActive())
            for gesture in ('corner', 'ctrl_a'):
                with self.subTest(gesture=gesture):
                    flags_calls = 0
                    if gesture == 'corner':
                        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=point_in(self.host, 'sheetHeaders', -30, 12))
                    else:
                        table_key(page, Qt.Key_A, Qt.ControlModifier)
                    self.assertEqual(sheet.y_columns, list(range(1, 302)))
                    self.assertEqual(sum(span.width() * span.height() for span in
                                         page.selection_model.selection()), 1340 * 302)
                    self.assertLess(flags_calls, 20 * 302,
                                    'Accessible selection must use ranges instead of every cell')
                    flags_calls = 0
                    page.clearSelection()
                    self.assertFalse(page.selection_model.hasSelection())
                    self.assertLess(flags_calls, 20 * 302,
                                    'Accessible deselection must use ranges instead of every cell')

    def test_accessibility_exposes_visible_cell_semantics_without_matrix_children(self):
        page, _sheet = self.make_page(1340, 302)
        was_active = QAccessible.isActive()
        self.addCleanup(QAccessible.setActive, was_active)
        QAccessible.setActive(True)
        body = item(self.host, 'sheetCells')
        before = len(body.childItems())
        self.select_rectangles(page, [(2, 3, 5, 4)])
        page.clearSelection()
        pump()
        self.assertLess(len(body.childItems()), 500)
        self.assertLessEqual(abs(len(body.childItems()) - before), 4)
        # Qt Quick exposes only materialized cell delegates through accessibility.
        # No native child-grid formula or 404,680-cell enumeration is required.
        delegates = [c for c in body.childItems()[0].childItems() if c.property('display') is not None]
        self.assertTrue(delegates)
        self.assertTrue(all(c.property('row') < page.rowCount for c in delegates))
        cell = next(c for c in delegates if c.property('row') == 0 and c.property('column') == 0)
        accessible = QAccessible.queryAccessibleInterface(cell)
        self.assertIsNotNone(accessible)
        self.assertEqual(accessible.role(), QAccessible.Cell)
        self.assertEqual(accessible.text(QAccessible.Name), 'Row 1, column 1: 0')
        self.assertFalse(accessible.state().selected)
        self.select_rectangles(page, [(0, 0, 5, 0)])
        pump()
        self.assertTrue(accessible.state().selected)
        page.clearSelection()
        pump()
        self.assertFalse(accessible.state().selected)

    def test_whole_column_mapping_requires_complete_row_coverage(self):
        cases = [
            ('partial cells', [(0, 1, 4, 3)], [2, 4]),
            ('X only', [(0, 0, 5, 0)], [2, 4]),
            ('cleared', [], [2, 4]),
            ('discontiguous columns', [(0, 1, 5, 1), (0, 5, 5, 5)], [1, 5]),
            ('adjacent row intervals', [(0, 1, 1, 1), (2, 1, 3, 2), (4, 1, 5, 1)], [1]),
            ('out of order intervals', [(4, 1, 5, 1), (0, 1, 1, 1), (2, 1, 3, 1)], [1]),
            ('row gap', [(0, 1, 1, 1), (3, 1, 5, 1)], [2, 4]),
            ('mixed full and partial', [(0, 1, 5, 1), (1, 3, 5, 3)], [1]),
        ]
        for label, rectangles, expected in cases:
            with self.subTest(selection=label):
                page, sheet = self.make_page()
                self.select_rectangles(page, rectangles)
                page._columns_chosen()
                self.assertEqual(sheet.y_columns, expected)

    def test_empty_sheet_retains_intended_y_columns(self):
        page, sheet = self.make_page()
        sheet.data = sheet.data[:0]
        page.refresh()
        page.selectAll()
        page._columns_chosen()
        self.assertEqual(sheet.y_columns, [2, 4])

    def test_explicit_header_gesture_preserves_disjoint_ranges_without_selected_columns_scan(self):
        page, sheet = self.make_page()
        self.select_rectangles(page, [(0, 1, 1, 1), (2, 1, 5, 1), (0, 4, 5, 4)])
        page._columns_chosen()
        self.assertEqual(sheet.y_columns, [1, 4])
        with patch.object(page.selection_model, 'selectedColumns', wraps=page.selection_model.selectedColumns) as selected_columns:
            page.beginHeader(3, Qt.ControlModifier.value)
            page.endHeader()
            self.assertEqual(sheet.y_columns, [1, 3, 4])
            selected_columns.assert_not_called()

    def test_header_context_preserves_complete_columns_and_metadata_rows_never_choose_y(self):
        page, sheet = self.make_page()
        self.select_rectangles(page, [(0, 1, 1, 1), (2, 1, 5, 1), (0, 4, 5, 4)])
        page._columns_chosen()
        before = page.capture_view_state()['ranges']
        with patch.object(page.selection_model, 'selectedColumns', side_effect=AssertionError('matrix scan')):
            self.assertTrue(page.openColumnContext(4, 0))
            self.assertEqual(page.capture_view_state()['ranges'], before)
            self.assertEqual(sheet.y_columns, [1, 4])
            self.assertTrue(page.openColumnContext(3, 1))
            self.assertEqual(sheet.y_columns, [1, 4])
            self.assertTrue(page.openColumnContext(3, 0))
            self.assertEqual(sheet.y_columns, [3])
