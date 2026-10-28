"""Actual Quick header gestures, metadata editor, and stable column widths."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QTest
from spectra_app.workbook_state import create_workbook, register_xy_sheet
from tests.workbook_quick_helpers import (pump, item, click_header, header_point,
    table_key, set_metadata_draft, selection_ranges, point_in)

from tests.quick_migration_case import QuickMigrationCase


class SheetHeaderTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        workspace = self.controller.workspace
        book = create_workbook(workspace)
        self.sheet = register_xy_sheet(workspace, book.workbook_id, 'Spectrum',
            np.array([[1., 2., 3., 4.], [2., 3., 4., 5.], [3., 4., 5., 6.]]),
            ['Photon Energy (eV)', 'Photoluminescence intensity (counts/s)',
             'Reference intensity (counts/s)', 'Gate voltage (V)'])
        self.workbook = self.controller.open_workbook(book.workbook_id)
        self.host.window.resize(1920, 800)
        pump()
        self.page = self.workbook.current_sheet
        self.root = self.controller.graph_documents.content_item(('workbook', book.workbook_id, ''))
        self.addCleanup(lambda: self.assertFalse(self.quick_errors, self.quick_errors))

    def assert_labels_fit(self):
        font = QFont(self.root.property('controlFont'))
        font.setBold(True)
        metrics = QFontMetrics(font)
        for column in range(self.page.columnCount):
            self.assertGreaterEqual(self.page.columnWidth(column),
                max(metrics.horizontalAdvance(text) for text in self.page.model.header_rows(column)) + 24)

    def test_labels_fit_bold_headers_after_gate_mapping_and_font_changes(self):
        self.assert_labels_fit()
        self.page.model.indicate_gate(1)
        click_header(self.page, 1)
        self.assert_labels_fit()
        self.page.setX(2)
        self.assert_labels_fit()
        font = QFont(self.root.property('controlFont'))
        font.setPointSizeF(font.pointSizeF() * 1.5)
        self.root.setProperty('controlFont', font)
        self.workbook.set_font(font)
        pump()
        self.assert_labels_fit()

    def test_full_tooltip_survives_manual_resize_and_mapping_changes(self):
        label = 'B(Y): Photoluminescence intensity (counts/s)'
        self.assertEqual(self.page.model.headerData(1, Qt.Horizontal, Qt.ToolTipRole), label)
        self.page.resizeColumn(1, 90)
        click_header(self.page, 1)
        self.page.model.indicate_gate(1)
        click_header(self.page, 3, Qt.ControlModifier)
        self.assertEqual(self.page.columnWidth(1), 90)
        self.assertEqual(self.page.model.headerData(1, Qt.Horizontal, Qt.ToolTipRole), label)

    def test_gate_and_selection_never_change_default_or_manual_widths(self):
        before = list(self.page._widths)
        self.page.model.indicate_gate(1)
        click_header(self.page, 1)
        click_header(self.page, 3, Qt.ControlModifier)
        self.page.setCurrentCell(0, 2)
        table_key(self.page, Qt.Key_Space, Qt.ControlModifier)
        self.page.model.indicate_gate(2)
        self.assertEqual(self.page._widths, before)
        self.page.resizeColumn(1, 83)
        self.page.setX(3)
        self.assertEqual(self.page.columnWidth(1), 83)
        self.assertNotIn('▶', self.page.model.headerData(1, Qt.Horizontal))

    def test_invalid_metadata_keeps_editor_and_corner_labels_do_not_select_all(self):
        click_header(self.page, 1)
        selection = selection_ranges(self.page)
        headers = item(self.host, 'sheetHeaders')
        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=point_in(self.host, 'sheetHeaders', -30, headers.height() * 5 / 6))
        self.assertEqual(selection_ranges(self.page), selection)
        self.page.editMetadata(1, 'long_name')
        set_metadata_draft(self.page, 'invalid\x00name')
        QTest.keyClick(self.host.window, Qt.Key_Return)
        pump()
        self.assertEqual(self.page.editorColumn, 1)
        self.assertEqual(self.sheet.column_metadata, {})
        self.assertIn('valid', self.page.editorError)
        self.assertTrue(item(self.host, 'sheetMetadataEditor').hasActiveFocus())
        set_metadata_draft(self.page, 'Corrected')
        QTest.keyClick(self.host.window, Qt.Key_Return)
        pump()
        self.assertEqual(self.page.editorColumn, -1)
        self.assertTrue(item(self.host, 'sheetCells').hasActiveFocus())
        self.assertEqual(selection_ranges(self.page), selection)
        table_key(self.page, Qt.Key_F2, Qt.ShiftModifier)
        self.assertEqual(self.page.editorLabel, 'Column B Units')
        QTest.keyClick(self.host.window, Qt.Key_Escape)
        pump()
        self.assertTrue(item(self.host, 'sheetCells').hasActiveFocus())

    def test_metadata_edit_commit_cancel_focus_loss_and_keyboard_leave_values_selection(self):
        click_header(self.page, 1)
        before, selection = self.sheet.data.copy(), selection_ranges(self.page)
        changes, gates = [], []
        self.page.changed.connect(lambda: changes.append(True))
        self.page.gateRequested.connect(lambda *args: gates.append(args))
        self.assertEqual(self.page.model.header_rows(1), ('B(Y)', 'Photoluminescence intensity', 'counts/s'))
        QTest.mouseDClick(self.host.window, Qt.LeftButton, pos=header_point(self.page, 1, 1))
        pump()
        self.assertEqual(self.page.editorColumn, 1)
        self.assertFalse(self.workbook.has_pending_project_edits(), 'Opening an editor is not dirty')
        set_metadata_draft(self.page, 'PL intensity')
        QTest.keyClick(self.host.window, Qt.Key_Return)
        pump()
        self.assertEqual(self.page.model.header_rows(1)[1], 'PL intensity')
        self.assertEqual(len(changes), 1)
        self.assertEqual(selection_ranges(self.page), selection)
        self.page.editMetadata(1, 'units')
        set_metadata_draft(self.page, 'cancelled')
        QTest.keyClick(self.host.window, Qt.Key_Escape)
        pump()
        self.assertEqual(self.page.model.header_rows(1)[2], 'counts/s')
        self.assertEqual(selection_ranges(self.page), selection, 'Escape preserves selection')
        self.page.editMetadata(1, 'units')
        set_metadata_draft(self.page, 'a.u.')
        item(self.host, 'workbookSnapshot').forceActiveFocus()
        pump()
        self.assertEqual(self.page.model.header_rows(1)[2], 'a.u.')
        self.assertEqual(selection_ranges(self.page), selection, 'Blur preserves selection')
        table_key(self.page, Qt.Key_F2)
        set_metadata_draft(self.page, '')
        QTest.keyClick(self.host.window, Qt.Key_Return)
        pump()
        self.assertEqual(self.page.model.header_rows(1)[1], '')
        self.assertEqual(selection_ranges(self.page), selection)
        np.testing.assert_equal(self.sheet.data, before)
        self.assertEqual(self.sheet.column_names[1], 'Photoluminescence intensity (counts/s)')
        self.assertEqual(self.sheet.y_columns, [1])
        self.assertEqual(gates, [])

    def test_copying_whole_column_then_resizing_header_keeps_mapping(self):
        click_header(self.page, 1)
        self.page.beginBody(0, 2, 0)
        self.page.dragBody(2, 2)
        self.assertEqual(self.page.copy_selection(), '3\n4\n5')
        self.assertEqual(self.sheet.y_columns, [1])
        before = self.page.columnWidth(2)
        edge = header_point(self.page, 2, 1, before - 2)
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=edge)
        QTest.mouseMove(self.host.window, edge + QPoint(35, 0))
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=edge + QPoint(35, 0))
        pump()
        self.assertGreater(self.page.columnWidth(2), before)
        self.assertEqual(self.sheet.y_columns, [1])

    def test_select_all_excludes_current_x_and_text_columns(self):
        self.sheet.kind = self.sheet.role = 'snapshot'
        self.sheet.text_columns = {2: ['first', 'second', 'third']}
        self.sheet.data[:, 2] = np.nan
        self.sheet.x_column = 3
        self.sheet.y_columns = [1]
        self.page.refresh()
        click_header(self.page, 1)
        table_key(self.page, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(self.sheet.x_column, 3)
        self.assertEqual(self.sheet.y_columns, [0, 1])

    def test_ctrl_shift_keyboard_x_only_and_clear_preserve_selection_contract(self):
        click_header(self.page, 1)
        click_header(self.page, 3, Qt.ControlModifier)
        self.assertEqual(self.sheet.y_columns, [1, 3])
        click_header(self.page, 1)
        click_header(self.page, 2, Qt.ShiftModifier)
        self.assertEqual(self.sheet.y_columns, [1, 2])
        click_header(self.page, 0)
        self.page.clearSelection()
        self.assertEqual(self.sheet.y_columns, [1, 2])
        self.page.setCurrentCell(0, 3)
        table_key(self.page, Qt.Key_Space, Qt.ControlModifier)
        self.assertEqual(self.sheet.y_columns, [3])
        self.page.setX(2)
        self.assertEqual((self.sheet.x_column, self.sheet.y_columns), (2, [3]))

    def test_gate_only_notifies_changed_columns_without_fitting(self):
        changes = []
        self.page.model.headerDataChanged.connect(lambda *args: changes.append(args))
        self.page.model.indicate_gate(1)
        self.assertEqual(changes, [(Qt.Horizontal, 1, 1)])
        changes.clear()
        with patch.object(self.page.model, 'headerData', wraps=self.page.model.headerData) as header_data:
            self.page.model.indicate_gate(1)
            self.assertEqual(changes, [])
            self.page.model.indicate_gate(3)
            self.assertEqual(changes, [(Qt.Horizontal, 1, 1), (Qt.Horizontal, 3, 3)])
            header_data.assert_not_called()
        changes.clear()
        self.page.model.indicate_gate(None)
        self.assertEqual(changes, [(Qt.Horizontal, 3, 3)])

    def test_repeated_mapping_and_current_x_normalization_are_noops(self):
        click_header(self.page, 1)
        changes = []
        self.page.changed.connect(lambda: changes.append(True))
        click_header(self.page, 1)
        widths = []
        self.page.widthsChanged.connect(lambda: widths.append(True))
        with patch.object(self.page, '_fit_widths', wraps=self.page._fit_widths) as fit, \
                patch.object(self.page.model, 'header_rows', wraps=self.page.model.header_rows) as read_header:
            self.page._columns_chosen()
            self.page.set_mapping(0, [0, 1])
            self.page.setY(0, True)
            fit.assert_not_called()
            read_header.assert_not_called()
        self.assertEqual(widths, [])
        self.assertEqual(changes, [])
        self.assertEqual(self.sheet.y_columns, [1])
