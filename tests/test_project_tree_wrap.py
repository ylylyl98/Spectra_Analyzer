"""Production Project rows wrap while preserving pointer and keyboard routes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

import numpy as np

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFont, QFontMetricsF
from PySide6.QtQml import QQmlExpression, qmlContext
from PySide6.QtTest import QTest

from spectra_app.workbook_state import Workbook, Sheet, Comparison
from tests.project_helpers import (click_project_row, project_control, project_label,
                                   project_row, settle_project)
from tests.quick_migration_case import QuickMigrationCase


LONG_NAME = 'WSe2_PC2_20261012_ScanEnergyPolarizationTemperatureGateVoltage_NoSpaces.dat'


class ProjectTreeWrapTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        self.original_font = QFont(project_control(self.w, 'projectNavigator').property('controlFont'))
        self.host.window.resize(1500, 920)
        self.host.show()
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
    def tearDown(self):
        QTest.keyRelease(self.w.root_window, Qt.Key_Control)
        QTest.keyRelease(self.w.root_window, Qt.Key_Shift)
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def settle(self):
        settle_project(self.w)

    def set_width(self, width):
        panel = project_control(self.w, 'projectNavigator')
        point = panel.mapToScene(QPointF(panel.width() + 2, panel.height() / 2))
        end = point + QPointF(width - panel.width(), 0)
        QTest.mousePress(self.w.root_window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        QTest.mouseMove(self.w.root_window, end.toPoint(), 30)
        QTest.mouseRelease(self.w.root_window, Qt.LeftButton, Qt.NoModifier, end.toPoint())
        self.settle()
        self.assertAlmostEqual(panel.width(), width, delta=1)

    def keys(self):
        return [('workbook', self.book.workbook_id), ('sheet', self.sheet.sheet_id),
                ('xy', self.comparison.comparison_id)]

    def lower_point(self, key):
        item = project_row(self.w, key)
        return item.mapToScene(QPointF(item.width() - 18, item.height() - 4)).toPoint()

    def test_rows_grow_and_shrink_with_width_and_font_without_changing_names(self):
        project = self.w.project
        originals = [project.row_for_key(key) for key in self.keys()]
        font_heights = []
        for point_size in (10, 13, 15, 10):
            font = QFont(self.original_font)
            font.setPointSize(point_size)
            project_control(self.w, 'projectNavigator').setProperty('controlFont', font)
            self.settle()
            sizes = []
            for width in (220, 490, 220):
                self.set_width(width)
                rows = [project_row(self.w, key) for key in self.keys()]
                labels = [project_label(self.w, key) for key in self.keys()]
                project_list = project_control(self.w, 'projectList')
                self.assertTrue(all(row.height() > QFontMetricsF(label.property('font')).height()
                                    for row, label in zip(rows, labels)))
                self.assertTrue(all(row.width() <= project_list.width() for row in rows))
                self.assertTrue(all(a.y() + a.height() <= b.y() for a, b in zip(rows, rows[1:])))
                self.assertTrue(all(not label.property('truncated') for label in labels))
                self.assertTrue(all(label.property('paintedWidth') <= label.width() + 1 for label in labels))
                self.assertEqual(project_list.property('contentX'), 0)
                vertical_only = QQmlExpression(qmlContext(project_list), project_list,
                                                'flickableDirection === Flickable.VerticalFlick')
                self.assertEqual(vertical_only.evaluate(), (True, False))
                self.assertEqual([label.property('text') for label in labels], [row.label for row in originals])
                sizes.append([row.height() for row in rows])
            self.assertTrue(all(a > b for a, b in zip(sizes[0], sizes[1])))
            self.assertEqual(sizes[0], sizes[2])
            font_heights.append(sizes[0])
        self.assertEqual(font_heights[0], font_heights[-1])
        self.assertGreater(font_heights[2][0], font_heights[0][0])
        self.assertEqual(originals, [project.row_for_key(key) for key in self.keys()])

    def test_wrapped_hit_selection_refresh_and_open_routes_stay_native(self):
        parent, child, comparison = self.keys()
        project = self.w.project
        click_project_row(self.w, parent, lower=True)
        click_project_row(self.w, child, Qt.ControlModifier, lower=True)
        selected = set(project.selected_keys())
        self.assertEqual(selected, {parent, child})
        self.w._refresh_data_navigator()
        self.settle()
        self.assertEqual(selected, set(project.selected_keys()))
        self.assertTrue(project.is_expanded(parent))

        click_project_row(self.w, parent, lower=True)
        click_project_row(self.w, comparison, Qt.ShiftModifier, lower=True)
        expected = {parent, child, comparison}
        self.assertEqual({key for key in project.selected_keys() if key[0] != 'group'}, expected)
        click_project_row(self.w, child, button=Qt.RightButton, lower=True)
        self.assertEqual(project.current_key(), child)
        self.assertEqual({key for key in project.selected_keys() if key[0] != 'group'}, expected)
        QTest.keyClick(self.w.root_window, Qt.Key_Escape)
        self.settle()
        for key, method, args in (
            (parent, 'open_workbook', (self.book.workbook_id,)),
            (child, 'open_workbook', (self.book.workbook_id, self.sheet.sheet_id)),
            (comparison, 'open_xy_comparison', (self.comparison.comparison_id,)),
        ):
            with self.subTest(kind=key[0]), patch.object(self.w, method) as opened:
                point = self.lower_point(key)
                QTest.mouseClick(self.w.root_window, Qt.LeftButton, Qt.NoModifier, point)
                QTest.keyClick(self.w.root_window, Qt.Key_Return)
                self.settle()
                opened.assert_called_once_with(*args)
                opened.reset_mock()
                QTest.mouseDClick(self.w.root_window, Qt.LeftButton, Qt.NoModifier, point)
                self.settle()
                opened.assert_called_once_with(*args)
                project.set_expanded(parent, True)
                self.settle()
        click_project_row(self.w, parent, lower=True)
        QTest.keyClick(self.w.root_window, Qt.Key_Left)
        self.assertFalse(project.is_expanded(parent))
        self.w._refresh_data_navigator()
        self.settle()
        self.assertFalse(project.is_expanded(parent))
        QTest.keyClick(self.w.root_window, Qt.Key_Right)
        self.assertTrue(project.is_expanded(parent))

    def test_scroll_and_heatmap_row_survive_wrapping(self):
        for number in range(25):
            book = Workbook(name=f'{number}_' + LONG_NAME)
            self.w.workspace.workbooks[book.workbook_id] = book
        self.w._refresh_data_navigator()
        self.set_width(220)
        project_list = project_control(self.w, 'projectList')
        self.assertGreater(project_list.property('contentHeight'), project_list.height())
        project_list.setProperty('contentY', 12.0)
        self.settle()
        scroll = project_list.property('contentY')
        self.assertGreater(scroll, 0)
        self.w._refresh_data_navigator()
        self.settle()
        self.assertEqual(project_list.property('contentY'), scroll)

        path = self.folder / LONG_NAME
        path.write_text('Energy(eV) 0 1\n1 2 3\n2 4 6\n3 6 9\n')
        self.w._load_dat_file_direct(path)
        session = self.w.workspace.active
        self.w.open_graph_document(session.dataset_id, session.active_graph_id)
        graph = session.active_graph
        graph.name = 'Heatmap_能量_e\u0301_🔬_' + LONG_NAME
        self.w._refresh_data_navigator()
        key = ('heatmap', (session.dataset_id, graph.graph_id))
        row = self.w.project.row_for_key(key)
        with patch.object(self.w, 'open_graph_document') as opened:
            label = project_label(self.w, key)
            self.assertGreater(project_row(self.w, key).height(), QFontMetricsF(label.property('font')).height())
            self.assertFalse(label.property('truncated'))
            click_project_row(self.w, key, lower=True)
            self.assertEqual(self.w.project.current_key(), key)
            QTest.keyClick(self.w.root_window, Qt.Key_Return)
            self.settle()
            opened.assert_called_once_with(session.dataset_id, graph.graph_id)
        self.assertEqual(label.property('text'), 'Heatmap_能量_e\u0301_🔬_' + LONG_NAME)
        self.assertIn(str(path), row.tooltip)


if __name__ == '__main__':
    unittest.main()
