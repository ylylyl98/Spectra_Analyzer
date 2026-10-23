"""Worksheet header gestures, readable labels and user-controlled widths."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

import numpy as np
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle

from spectra_app.dataset_state import Workspace
from spectra_app.workbook_page import SheetPage
from spectra_app.workbook_state import create_workbook, register_xy_sheet

APP = QApplication.instance() or QApplication([])


class SheetHeaderTests(unittest.TestCase):
    def setUp(self):
        workspace = Workspace()
        book = create_workbook(workspace)
        self.sheet = register_xy_sheet(workspace, book.workbook_id, 'Spectrum',
            np.array([[1., 2., 3., 4.], [2., 3., 4., 5.], [3., 4., 5., 6.]]),
            ['Photon Energy (eV)', 'Photoluminescence intensity (counts/s)',
             'Reference intensity (counts/s)', 'Gate voltage (V)'])
        self.page = SheetPage(workspace, self.sheet.sheet_id)
        self.page.resize(1600, 450)
        self.page.show()
        APP.processEvents()
        self.table = self.page.table
        self.header = self.table.horizontalHeader()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.page.close()
        self.page.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def point(self, column):
        return QPoint(self.header.sectionViewportPosition(column) + 12, self.header.height() // 2)

    def click(self, column, modifiers=Qt.NoModifier):
        QTest.mouseClick(self.header.viewport(), Qt.LeftButton, modifiers, self.point(column))
        APP.processEvents()

    def assert_labels_fit(self):
        font = QFont(self.header.font())
        font.setBold(True)  # Native selected headers and the active Gate use bold.
        metrics = QFontMetrics(font)
        margin = self.header.style().pixelMetric(QStyle.PM_HeaderMargin, None, self.header)
        for column in range(self.page.model.columnCount()):
            text = self.page.model.headerData(column, Qt.Horizontal)
            self.assertGreaterEqual(self.header.sectionSize(column),
                                    metrics.horizontalAdvance(text) + 2 * margin, text)

    def test_labels_fit_bold_headers_after_gate_mapping_and_font_changes(self):
        self.assert_labels_fit()
        self.page.model.indicate_gate(1)
        self.click(1)
        self.assert_labels_fit()
        self.page.x_combo.setCurrentIndex(2)
        self.assert_labels_fit()
        font = QFont(self.header.font())
        font.setPointSizeF(font.pointSizeF() * 1.5)
        self.header.setFont(font)
        APP.processEvents()
        self.assert_labels_fit()

    def test_full_tooltip_survives_manual_resize_and_mapping_changes(self):
        label = 'B(Y): Photoluminescence intensity (counts/s)'
        self.assertEqual(self.page.model.headerData(1, Qt.Horizontal, Qt.ToolTipRole), label)
        self.header.resizeSection(1, 90)
        self.click(1)
        self.page.model.indicate_gate(1)
        self.click(3, Qt.ControlModifier)
        self.assertEqual(self.header.sectionSize(1), 90)
        self.assertEqual(self.page.model.headerData(1, Qt.Horizontal, Qt.ToolTipRole), '▶ ' + label)

    def test_copying_whole_column_then_resizing_header_keeps_mapping(self):
        self.click(1)
        self.assertEqual(self.sheet.y_columns, [1])
        start = self.table.visualRect(self.page.model.index(0, 2)).center()
        end = self.table.visualRect(self.page.model.index(2, 2)).center()
        QTest.mousePress(self.table.viewport(), Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(self.table.viewport(), end)
        QTest.mouseRelease(self.table.viewport(), Qt.LeftButton, Qt.NoModifier, end)
        self.assertEqual(self.page.copy_selection(), '3\n4\n5')
        self.assertEqual(self.sheet.y_columns, [1])
        edge = QPoint(self.header.sectionViewportPosition(2) + self.header.sectionSize(2) - 1,
                      self.header.height() // 2)
        before = self.header.sectionSize(2)
        QTest.mousePress(self.header.viewport(), Qt.LeftButton, Qt.NoModifier, edge)
        QTest.mouseMove(self.header.viewport(), edge + QPoint(35, 0))
        QTest.mouseRelease(self.header.viewport(), Qt.LeftButton, Qt.NoModifier, edge + QPoint(35, 0))
        APP.processEvents()
        self.assertGreater(self.header.sectionSize(2), before)
        self.assertEqual(self.sheet.y_columns, [1])

    def test_select_all_excludes_current_x_and_text_columns(self):
        self.sheet.kind = self.sheet.role = 'snapshot'
        self.sheet.text_columns = {2: ['first', 'second', 'third']}
        self.sheet.data[:, 2] = np.nan
        self.sheet.x_column = 3
        self.sheet.y_columns = [1]
        self.page.refresh()
        self.click(1)
        QTest.keyClick(self.table, Qt.Key_A, Qt.ControlModifier)
        APP.processEvents()
        self.assertEqual(self.sheet.x_column, 3)
        self.assertEqual(self.sheet.y_columns, [0, 1])

    def test_ctrl_shift_keyboard_x_only_and_clear_preserve_selection_contract(self):
        self.click(1)
        self.click(3, Qt.ControlModifier)
        self.assertEqual(self.sheet.y_columns, [1, 3])
        self.click(1)
        self.click(2, Qt.ShiftModifier)
        self.assertEqual(self.sheet.y_columns, [1, 2])
        self.click(0)
        self.table.clearSelection()
        self.assertEqual(self.sheet.y_columns, [1, 2])
        self.table.setCurrentIndex(self.page.model.index(0, 3))
        QTest.keyClick(self.table, Qt.Key_Space, Qt.ControlModifier)
        self.assertEqual(self.sheet.y_columns, [3])
        menu = self.page.column_menu(2)
        menu.actions()[0].trigger()
        self.assertEqual((self.sheet.x_column, self.sheet.y_columns), (2, [3]))
        menu.deleteLater()
