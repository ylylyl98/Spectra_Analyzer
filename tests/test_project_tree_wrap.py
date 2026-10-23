"""Project rows follow available width without changing native navigation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

import numpy as np

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from spectra_app.main_window import MainWindow
from spectra_app.workbook_state import Workbook, Sheet, Comparison
from tests.settings_helpers import isolate_settings


APP = QApplication.instance() or QApplication([])
LONG_NAME = 'WSe2_PC2_20261012_ScanEnergyPolarizationTemperatureGateVoltage_NoSpaces.dat'


class ProjectTreeWrapTests(unittest.TestCase):
    def setUp(self):
        isolate_settings(self)
        self.w = MainWindow()
        self.w.resize(1500, 920)
        self.w.show()
        self.tree = self.w.data_navigator
        book = Workbook(name=LONG_NAME)
        sheet = Sheet(name='能量光谱_' + LONG_NAME, source_path=None,
                      data=np.array([[1., 2.], [2., 3.]]), column_names=['X', 'Y'])
        book.sheet_ids.append(sheet.sheet_id)
        self.w.workspace.workbooks[book.workbook_id] = book
        self.w.workspace.sheets[sheet.sheet_id] = sheet
        comparison = Comparison(name='Long comparison graph with several words ' + LONG_NAME)
        self.w.workspace.comparisons[comparison.comparison_id] = comparison
        self.w._refresh_data_navigator()
        self.book, self.sheet, self.comparison = book, sheet, comparison
        self.set_width(220)
        self.addCleanup(self.dispose)

    def dispose(self):
        QTest.keyRelease(self.tree, Qt.Key_Control)
        QTest.keyRelease(self.tree, Qt.Key_Shift)
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def settle(self):
        for _ in range(4):
            APP.processEvents()

    def set_width(self, width):
        self.w._main_splitter.setSizes([width, 1000])
        self.settle()

    def rows(self):
        keys = [('workbook', self.book.workbook_id), ('sheet', self.sheet.sheet_id),
                ('xy', self.comparison.comparison_id)]
        items = {item.data(0, Qt.UserRole): item for item in self.w._navigator_items()}
        return tuple(items[key] for key in keys)

    def lower_point(self, item):
        self.tree.scrollToItem(item)
        self.settle()
        rect = self.tree.visualItemRect(item)
        return QPoint(rect.left() + 10, rect.bottom() - 3)

    def test_rows_grow_and_shrink_with_width_and_font_without_changing_names(self):
        rows = self.rows()
        originals = [(row.text(0), row.data(0, Qt.UserRole), row.toolTip(0)) for row in rows]
        font_heights = []
        for point_size in (10, 13, 15, 10):
            font = QFont(self.tree.font())
            font.setPointSize(point_size)
            self.tree.setFont(font)
            sizes = []
            for width in (220, 490, 220):
                self.set_width(width)
                rects = [self.tree.visualItemRect(row) for row in rows]
                self.assertTrue(all(rect.height() > self.tree.fontMetrics().height() for rect in rects))
                self.assertTrue(all(rect.right() < self.tree.viewport().width() for rect in rects))
                self.assertTrue(all(a.bottom() < b.top() for a, b in zip(rects, rects[1:])))
                self.assertEqual(self.tree.horizontalScrollBar().maximum(), 0)
                sizes.append([rect.height() for rect in rects])
            self.assertTrue(all(a > b for a, b in zip(sizes[0], sizes[1])))
            self.assertEqual(sizes[0], sizes[2])
            font_heights.append(sizes[0])
        self.assertEqual(font_heights[0], font_heights[-1])
        self.assertGreater(font_heights[2][0], font_heights[0][0])
        self.assertEqual(originals, [(row.text(0), row.data(0, Qt.UserRole), row.toolTip(0)) for row in rows])

    def test_wrapped_hit_selection_refresh_and_open_routes_stay_native(self):
        parent, child, comparison = self.rows()
        QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, self.lower_point(parent))
        QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, Qt.ControlModifier, self.lower_point(child))
        selected = {item.data(0, Qt.UserRole) for item in self.tree.selectedItems()}
        self.assertEqual(len(selected), 2)
        self.w._refresh_data_navigator()
        self.settle()
        self.assertEqual(selected, {item.data(0, Qt.UserRole) for item in self.tree.selectedItems()})
        parent, child, comparison = self.rows()
        self.assertTrue(parent.isExpanded())

        QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, self.lower_point(parent))
        QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, Qt.ShiftModifier, self.lower_point(comparison))
        self.assertEqual({item.data(0, Qt.UserRole) for item in self.tree.selectedItems()
                          if item.data(0, Qt.UserRole)[0] != 'group'},
                         {('workbook', self.book.workbook_id), ('sheet', self.sheet.sheet_id),
                          ('xy', self.comparison.comparison_id)})
        with patch('spectra_app.main_window.QMenu'):
            self.w._navigator_menu(self.lower_point(child))
        self.assertIs(self.tree.currentItem(), child)
        self.assertEqual({item.data(0, Qt.UserRole) for item in self.tree.selectedItems()
                          if item.data(0, Qt.UserRole)[0] != 'group'},
                         {('workbook', self.book.workbook_id), ('sheet', self.sheet.sheet_id),
                          ('xy', self.comparison.comparison_id)})
        for item, method, args in (
            (parent, 'open_workbook', (self.book.workbook_id,)),
            (child, 'open_workbook', (self.book.workbook_id, self.sheet.sheet_id)),
            (comparison, 'open_xy_comparison', (self.comparison.comparison_id,)),
        ):
            with self.subTest(kind=item.data(0, Qt.UserRole)[0]), patch.object(self.w, method) as opened:
                point = self.lower_point(item)
                self.assertIs(self.tree.itemAt(point), item)
                QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, point)
                QTest.keyClick(self.tree, Qt.Key_Return)
                opened.assert_called_once_with(*args)
                opened.reset_mock()
                QTest.mouseDClick(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, point)
                opened.assert_called_once_with(*args)
                parent.setExpanded(True)
                self.settle()
        self.tree.setCurrentItem(parent)
        QTest.keyClick(self.tree, Qt.Key_Left)
        self.assertFalse(parent.isExpanded())
        self.w._refresh_data_navigator()
        self.settle()
        parent = self.tree.topLevelItem(0)
        self.assertFalse(parent.isExpanded())
        QTest.keyClick(self.tree, Qt.Key_Right)
        self.assertTrue(parent.isExpanded())

    def test_scroll_and_heatmap_row_survive_wrapping(self):
        for number in range(25):
            book = Workbook(name=f'{number}_' + LONG_NAME)
            self.w.workspace.workbooks[book.workbook_id] = book
        self.w._refresh_data_navigator()
        self.set_width(220)
        tree = self.tree
        self.assertGreater(tree.verticalScrollBar().maximum(), 0)
        tree.verticalScrollBar().setValue(12)
        scroll = tree.verticalScrollBar().value()
        self.w._refresh_data_navigator()
        self.settle()
        self.assertEqual(tree.verticalScrollBar().value(), scroll)
        heatmap = QTreeWidgetItem(['Heatmap_能量_e\u0301_🔬_' + LONG_NAME])
        heatmap.setData(0, Qt.UserRole, ('heatmap', ('dataset-id', 'graph-id')))
        heatmap.setToolTip(0, 'Full source filename: ' + LONG_NAME)
        tree.addTopLevelItem(heatmap)
        with patch.object(self.w, 'open_graph_document') as opened:
            point = self.lower_point(heatmap)
            self.assertGreater(tree.visualItemRect(heatmap).height(), tree.fontMetrics().height())
            self.assertIs(tree.itemAt(point), heatmap)
            QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, point)
            QTest.keyClick(tree, Qt.Key_Return)
            opened.assert_called_once_with('dataset-id', 'graph-id')
        self.assertEqual(heatmap.text(0), 'Heatmap_能量_e\u0301_🔬_' + LONG_NAME)
        self.assertEqual(heatmap.toolTip(0), 'Full source filename: ' + LONG_NAME)


if __name__ == '__main__':
    unittest.main()
