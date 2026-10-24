"""Whole-column mapping reads selection rectangles without visiting every cell."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QPoint
from PySide6.QtWidgets import QApplication

from spectra_app.dataset_state import Workspace
from spectra_app.workbook_page import SheetPage, SheetTableModel
from spectra_app.workbook_state import create_workbook, register_xy_sheet

APP = QApplication.instance() or QApplication([])


class SheetSelectionPerformanceTests(unittest.TestCase):
    def make_page(self, rows=6, columns=6):
        workspace = Workspace()
        book = create_workbook(workspace)
        sheet = register_xy_sheet(workspace, book.workbook_id, 'Selection',
            np.zeros((rows, columns)), [f'Column {column}' for column in range(columns)])
        sheet.y_columns = [2, 4]
        page = SheetPage(workspace, sheet.sheet_id)
        page.resize(1100, 400)
        page.show()
        APP.processEvents()
        self.addCleanup(self.dispose, page)
        return page, sheet

    def dispose(self, page):
        page.close()
        page.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def select_rectangles(self, page, rectangles):
        selection = QItemSelection()
        for top, left, bottom, right in rectangles:
            selection.select(page.model.index(top, left), page.model.index(bottom, right))
        page.table.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)

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
            selection = page.table.selectionModel()
            flags_calls = 0
            page.model.index(0, 0).flags()
            self.assertGreater(flags_calls, 0, 'The Qt virtual-call counter must be active')
            flags_calls = 0
            with patch.object(selection, 'selectedColumns', wraps=selection.selectedColumns) as selected_columns:
                self.assertIs(selection.selectedColumns, selected_columns)
                page.table.selectAll()
                self.assertEqual(sheet.y_columns, list(range(1, 302)))
                # Count only the action; deferred paints can inspect visible cells.
                self.assertLess(flags_calls, 20 * page.model.columnCount(),
                                'Select All must not query flags for the entire matrix')
                selected_columns.assert_not_called()

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
        page.table.selectAll()
        page._columns_chosen()
        self.assertEqual(sheet.y_columns, [2, 4])

    def test_header_menu_preserves_full_selection_without_selected_columns_scan(self):
        page, sheet = self.make_page()
        self.select_rectangles(page, [(0, 1, 1, 1), (2, 1, 5, 1), (0, 4, 5, 4)])
        page._columns_chosen()
        self.assertEqual(sheet.y_columns, [1, 4])
        selection = page.table.selectionModel()
        header = page.table.horizontalHeader()
        with patch.object(selection, 'selectedColumns', wraps=selection.selectedColumns) as selected_columns, \
                patch.object(page, 'column_menu'):
            self.assertIs(selection.selectedColumns, selected_columns)
            point = QPoint(header.sectionViewportPosition(4) + 12, header.height() // 2)
            page._header_menu(point)
            self.assertEqual(sheet.y_columns, [1, 4])
            self.assertEqual(sum(span.width() * span.height() for span in selection.selection()), 12)
            point = QPoint(header.sectionViewportPosition(3) + 12, header.height() // 2)
            page._header_menu(point)
            self.assertEqual(sheet.y_columns, [3])
            selected_columns.assert_not_called()
