"""Source hierarchy preserves full names, ownership and native tree commands."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import DatasetSession
from spectra_app.main_window import MainWindow
from spectra_app.workbook_state import Workbook, Sheet, Comparison, Curve, merge_workbooks
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])
LONG_NAME = 'WSe2_PC2_能量_20261013_ScanEnergyPolarizationTemperatureGateVoltage_01.dat'


class ProjectTreeHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.w.resize(1500, 1000)
        self.w.show()
        self.tree = self.w.data_navigator
        self.w._main_splitter.setSizes([240, 1100])
        self.addCleanup(self.dispose)

    def dispose(self):
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def settle(self):
        for _ in range(4):
            APP.processEvents()

    def rows(self):
        def descendants(parent):
            for index in range(parent.childCount()):
                item = parent.child(index)
                yield item
                yield from descendants(item)
        return list(descendants(self.tree.invisibleRootItem()))

    def item(self, key):
        return next(item for item in self.rows() if item.data(0, Qt.UserRole) == key)

    def source(self, filename=LONG_NAME, directory='run_a', alias=None):
        path = self.folder / directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Energy(eV) 0 1\n1 2 3\n2 4 6\n3 6 9\n', encoding='utf-8')
        cube = load_dat_cube(path)
        session = DatasetSession(source_path=path, base_cube=cube, current_cube=cube)
        self.w.workspace.datasets[session.dataset_id] = session
        book = Workbook(name=alias or filename)
        self.w.workspace.workbooks[book.workbook_id] = book
        for role, name in [('original', 'Original'), ('processed', 'Processed')]:
            sheet = Sheet(name=name, kind='dat', source_path=path, dataset_id=session.dataset_id,
                          role=role, mode='raw' if role == 'original' else 'processed')
            book.sheet_ids.append(sheet.sheet_id)
            self.w.workspace.sheets[sheet.sheet_id] = sheet
        graph = session.create_graph('Custom heatmap')
        return book, session, graph

    def test_full_names_and_publication_under_dataset_owner_and_independent_comparisons(self):
        first, one, graph_one = self.source()
        second_name = LONG_NAME.replace('_01.dat', '_02.dat')
        second, two, graph_two = self.source(second_name)
        comparison = Comparison(name='Cross-source result')
        for book in (first, second):
            curve = Curve(sheet_id=book.sheet_ids[0])
            comparison.curves[curve.curve_id] = curve
        self.w.workspace.comparisons[comparison.comparison_id] = comparison
        self.w._refresh_data_navigator()
        for book, session, graph, name in ((first, one, graph_one, LONG_NAME),
                                           (second, two, graph_two, second_name)):
            root = self.item(('workbook', book.workbook_id))
            self.assertEqual(root.text(0), name)
            self.assertEqual([root.child(i).text(0) for i in range(root.childCount())],
                             ['Worksheets (2)', 'Heatmaps (1)'])
            data = root.child(0)
            self.assertEqual([data.child(i).text(0) for i in range(data.childCount())], ['Original', 'Processed'])
            heatmap = self.item(('heatmap', (session.dataset_id, graph.graph_id)))
            self.assertIs(heatmap.parent().parent(), root)
            self.assertEqual(heatmap.text(0), graph.name)
            self.assertEqual(sum(name in row.text(0) for row in self.rows()), 1)
            self.assertFalse(root.icon(0).isNull())
            self.assertFalse(heatmap.icon(0).isNull())
            self.assertNotIn(('heatmap', (session.dataset_id, 'default')),
                             [row.data(0, Qt.UserRole) for row in self.rows()])
        xy = self.item(('xy', comparison.comparison_id))
        self.assertEqual(xy.text(0), 'Cross-source result · 2 sources')
        self.assertEqual(xy.parent().text(0), 'Comparisons (1)')
        self.assertIsNone(xy.parent().parent())
        comparison.curves.clear()
        self.w._refresh_data_navigator()
        self.assertIsNone(self.item(('xy', comparison.comparison_id)).parent().parent())

    def test_merged_sources_same_basename_alias_and_embedded_values(self):
        first, one, graph_one = self.source(alias='Merged measurements')
        second, two, graph_two = self.source(directory='run_b')
        merge_workbooks(self.w.workspace, [first.workbook_id, second.workbook_id], first.workbook_id)
        embedded = Sheet(name='Calibration', data=np.array([[1., 2.]]), column_names=['X', 'Y'])
        first.sheet_ids.append(embedded.sheet_id)
        self.w.workspace.sheets[embedded.sheet_id] = embedded
        self.w._refresh_data_navigator()
        root = self.item(('workbook', first.workbook_id))
        self.assertEqual(root.text(0), 'Merged measurements · 2 sources + embedded values')
        self.assertEqual([root.child(i).text(0) for i in range(root.childCount())],
                         [LONG_NAME + ' — run_a', LONG_NAME + ' — run_b', 'Embedded values'])
        for session, graph, directory in [(one, graph_one, 'run_a'), (two, graph_two, 'run_b')]:
            heatmap = self.item(('heatmap', (session.dataset_id, graph.graph_id)))
            source = heatmap.parent().parent()
            self.assertEqual(source.text(0), LONG_NAME + ' — ' + directory)
            self.assertIs(source.parent(), root)
            self.assertEqual(source.child(0).text(0), 'Worksheets (2)')
        self.assertEqual(self.item(('sheet', embedded.sheet_id)).parent().parent().text(0), 'Embedded values')
        self.tree.setCurrentItem(root)
        self.assertEqual(set(self.w._selected_reload_paths()), {one.source_path, two.source_path})
        self.assertEqual(self.w._selected_sheet_ids(), [first.sheet_ids[0], first.sheet_ids[2], embedded.sheet_id])
        self.assertEqual(self.w._selected_workbook_ids(), [first.workbook_id])
        # Original sheet identity governs ownership even if the session path is stale.
        two.source_path = self.folder / 'stale' / 'different.dat'
        self.w._refresh_data_navigator()
        self.assertIn('run_b', self.item(('heatmap', (two.dataset_id, graph_two.graph_id))).parent().parent().text(0))

    def test_alias_and_detached_dataset_keep_full_source_names(self):
        book, session, graph = self.source(alias='User title')
        orphan = DatasetSession(source_path=self.folder / 'detached.dat')
        orphan_graph = orphan.create_graph('Detached graph')
        self.w.workspace.datasets[orphan.dataset_id] = orphan
        self.w._refresh_data_navigator()
        self.assertEqual(self.item(('workbook', book.workbook_id)).text(0), 'User title\u2028' + LONG_NAME)
        item = self.item(('heatmap', (orphan.dataset_id, orphan_graph.graph_id)))
        self.assertEqual(item.parent().parent().text(0), 'detached.dat')
        self.assertIsNone(item.parent().parent().parent())
        key = item.parent().parent().data(0, Qt.UserRole)
        self.w._refresh_data_navigator()
        self.assertEqual(self.item(key).text(0), 'detached.dat')

    def test_reloaded_copies_and_duplicate_embedded_books_keep_distinct_full_labels(self):
        first, _, _ = self.source()
        second, _, _ = self.source()
        embedded = [Workbook(name='Calibration') for _ in range(2)]
        for book in embedded:
            self.w.workspace.workbooks[book.workbook_id] = book
        self.w._refresh_data_navigator()
        labels = [self.item(('workbook', book.workbook_id)).text(0)
                  for book in (first, second, *embedded)]
        self.assertEqual(len(set(labels)), 4)
        self.assertEqual(labels[:2], [LONG_NAME + ' (1)', LONG_NAME + ' (2)'])
        self.assertTrue(all('Calibration' in label and 'embedded' in label for label in labels[2:]))
        self.w._refresh_data_navigator()
        self.assertEqual(labels, [self.item(('workbook', book.workbook_id)).text(0)
                                 for book in (first, second, *embedded)])

    def test_group_keyboard_mouse_and_actions_never_route_business_objects(self):
        book, session, graph = self.source()
        self.w._refresh_data_navigator()
        self.settle()
        group = self.item(('sheet', book.sheet_ids[0])).parent()
        self.tree.setCurrentItem(group)
        self.tree.setFocus()
        with patch.object(self.w, 'open_workbook') as workbook, patch.object(self.w, 'open_graph_document') as heatmap:
            QTest.keyClick(self.tree, Qt.Key_Return)
            self.assertFalse(group.isExpanded())
            QTest.keyClick(self.tree, Qt.Key_Return)
            self.assertTrue(group.isExpanded())
            self.settle()
            point = self.tree.visualItemRect(group).center()
            QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, point)
            QTest.mouseDClick(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, point)
            self.assertFalse(group.isExpanded())
            workbook.assert_not_called()
            heatmap.assert_not_called()
        self.w._refresh_navigator_actions()
        for action in [*self.w._navigator_buttons.values(), self.w.compare_sheets_btn,
                       self.w.merge_workbooks_btn, self.w.reload_source_action, self.w.project_analyze_action]:
            self.assertFalse(action.isEnabled())
        self.assertEqual(self.w._selected_sheet_ids(), [])
        self.assertEqual(self.w._selected_workbook_ids(), [])
        self.assertEqual(self.w._selected_reload_paths(), [])
        with patch('spectra_app.main_window.QInputDialog.getText') as rename, \
                patch('spectra_app.main_window.QMessageBox.warning') as remove:
            self.w._rename_navigator_item()
            self.w._remove_navigator_item()
            rename.assert_not_called()
            remove.assert_not_called()
        with patch('spectra_app.main_window.QMenu') as menu:
            self.w._navigator_menu(self.tree.visualItemRect(group).center())
            self.assertEqual(menu.return_value.addAction.call_count, 1)
            self.assertEqual(menu.return_value.addAction.call_args.args[0], 'Expand')

    def test_recursive_folding_current_selection_scroll_and_active_style_survive_refresh(self):
        first, one, graph_one = self.source(alias='Merged')
        second, two, graph_two = self.source(directory='run_b')
        merge_workbooks(self.w.workspace, [first.workbook_id, second.workbook_id], first.workbook_id)
        for index in range(20):
            book = Workbook(name=f'Extra {index} ' + LONG_NAME)
            self.w.workspace.workbooks[book.workbook_id] = book
        self.w._refresh_data_navigator()
        self.settle()
        heatmap_key = ('heatmap', (one.dataset_id, graph_one.graph_id))
        heatmap = self.item(heatmap_key)
        self.tree.setCurrentItem(heatmap)
        data = self.item(('sheet', first.sheet_ids[0])).parent()
        data.setExpanded(False)
        heatmap.parent().setExpanded(False)
        other_source = self.item(('sheet', second.sheet_ids[0])).parent().parent()
        other_source.setExpanded(False)
        self.item(('workbook', first.workbook_id)).setSelected(True)
        folded = {row.data(0, Qt.UserRole) for row in (data, heatmap.parent(), other_source)}
        selected = {row.data(0, Qt.UserRole) for row in self.tree.selectedItems()}
        self.tree.verticalScrollBar().setValue(8)
        scroll = self.tree.verticalScrollBar().value()
        with patch.object(self.w.plot_tabs, 'currentWidget', return_value=self.w.graph_documents), \
                patch.object(self.w.graph_documents, 'page_for', return_value=None):
            self.w.graph_documents._active_key = heatmap_key[1]
            self.w._refresh_data_navigator()
            self.settle()
            self.assertEqual(self.tree.currentItem().data(0, Qt.UserRole), heatmap_key)
            self.assertEqual({row.data(0, Qt.UserRole) for row in self.tree.selectedItems()}, selected)
            self.assertEqual(self.tree.verticalScrollBar().value(), scroll)
            self.assertTrue(all(not self.item(key).isExpanded() for key in folded))
            active = self.item(heatmap_key)
            self.assertTrue(active.data(0, Qt.UserRole + 1))
            self.assertTrue(active.font(0).underline())
            self.assertIn('Active document', active.data(0, Qt.AccessibleDescriptionRole))
            root = self.item(('workbook', first.workbook_id))
            self.assertTrue(root.font(0).bold())
            self.assertFalse(root.font(0).underline())
            self.w.graph_documents._active_key = None
            self.w._refresh_navigator_active()
            self.assertFalse(active.font(0).underline())
            self.assertTrue(root.font(0).bold())


if __name__ == '__main__':
    unittest.main()
