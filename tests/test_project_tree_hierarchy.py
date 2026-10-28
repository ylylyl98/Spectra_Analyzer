"""Source hierarchy preserves full names, ownership and native tree commands."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import DatasetSession
from spectra_app.workbook_state import Workbook, Sheet, Comparison, Curve, merge_workbooks
from tests.quick_migration_case import QuickMigrationCase
from tests.project_helpers import click_project_row, project_control, project_row, settle_project

LONG_NAME = 'WSe2_PC2_能量_20261013_ScanEnergyPolarizationTemperatureGateVoltage_01.dat'


class ProjectTreeHierarchyTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        self.host.window.resize(1500, 1000)
        self.host.show()
        self.project = self.w.project

    def tearDown(self):
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def settle(self):
        settle_project(self.w)

    def rows(self):
        return self.project.all_rows()

    def item(self, key):
        return self.project.row_for_key(key)

    def parent(self, row):
        return self.item(row.parent)

    def labels(self, key):
        return [row.label for row in self.project.children(key)]

    def source(self, filename=LONG_NAME, directory='run_a', alias=None):
        path = self.folder / directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Energy(eV) 0 1\n1 2 3\n2 4 6\n3 6 9\n', encoding='utf-8')
        cube = load_dat_cube(path)
        session = DatasetSession(source_path=path, base_cube=cube, current_cube=cube)
        self.w.workspace.register(session, as_new_copy=True)
        book = next(book for book in self.w.workspace.workbooks.values()
                    if any(self.w.workspace.sheets[sid].dataset_id == session.dataset_id
                           for sid in book.sheet_ids))
        book.name = alias or filename
        for role, name in [('processed', 'Processed')]:
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
            self.assertEqual(root.label, name)
            self.assertEqual(self.labels(root.key),
                             ['Worksheets (2)', 'Heatmaps (1)'])
            data = self.project.children(root.key)[0]
            self.assertEqual(self.labels(data.key), ['Original', 'Processed'])
            heatmap = self.item(('heatmap', (session.dataset_id, graph.graph_id)))
            self.assertEqual(self.parent(self.parent(heatmap)), root)
            self.assertEqual(heatmap.label, graph.name)
            self.assertEqual(sum(name in row.label for row in self.rows()), 1)
            self.assertEqual(root.key[0], 'workbook')
            self.assertEqual(heatmap.key[0], 'heatmap')
            self.assertNotIn(('heatmap', (session.dataset_id, 'default')),
                             [row.key for row in self.rows()])
        xy = self.item(('xy', comparison.comparison_id))
        self.assertEqual(xy.label, 'Cross-source result · 2 sources')
        self.assertEqual(self.parent(xy).label, 'Comparisons (1)')
        self.assertIsNone(self.parent(xy).parent)
        comparison.curves.clear()
        self.w._refresh_data_navigator()
        self.assertIsNone(self.parent(self.item(('xy', comparison.comparison_id))).parent)

    def test_merged_sources_same_basename_alias_and_embedded_values(self):
        first, one, graph_one = self.source(alias='Merged measurements')
        second, two, graph_two = self.source(directory='run_b')
        merge_workbooks(self.w.workspace, [first.workbook_id, second.workbook_id], first.workbook_id)
        embedded = Sheet(name='Calibration', data=np.array([[1., 2.]]), column_names=['X', 'Y'])
        first.sheet_ids.append(embedded.sheet_id)
        self.w.workspace.sheets[embedded.sheet_id] = embedded
        self.w._refresh_data_navigator()
        root = self.item(('workbook', first.workbook_id))
        self.assertEqual(root.label, 'Merged measurements · 2 sources + embedded values')
        self.assertEqual(self.labels(root.key),
                         [LONG_NAME + ' — run_a', LONG_NAME + ' — run_b', 'Embedded values'])
        for session, graph, directory in [(one, graph_one, 'run_a'), (two, graph_two, 'run_b')]:
            heatmap = self.item(('heatmap', (session.dataset_id, graph.graph_id)))
            source = self.parent(self.parent(heatmap))
            self.assertEqual(source.label, LONG_NAME + ' — ' + directory)
            self.assertEqual(source.parent, root.key)
            self.assertEqual(self.project.children(source.key)[0].label, 'Worksheets (2)')
        self.assertEqual(self.parent(self.parent(self.item(('sheet', embedded.sheet_id)))).label, 'Embedded values')
        self.project.select_key(root.key)
        self.assertEqual(set(self.w._selected_reload_paths()), {one.source_path, two.source_path})
        self.assertEqual(self.w._selected_sheet_ids(), [first.sheet_ids[0], first.sheet_ids[2], embedded.sheet_id])
        self.assertEqual(self.w._selected_workbook_ids(), [first.workbook_id])
        # Original sheet identity governs ownership even if the session path is stale.
        two.source_path = self.folder / 'stale' / 'different.dat'
        self.w._refresh_data_navigator()
        self.assertIn('run_b', self.parent(self.parent(self.item(('heatmap', (two.dataset_id, graph_two.graph_id))))).label)

    def test_alias_and_detached_dataset_keep_full_source_names(self):
        book, session, graph = self.source(alias='User title')
        orphan = DatasetSession(source_path=self.folder / 'detached.dat')
        orphan_graph = orphan.create_graph('Detached graph')
        self.w.workspace.datasets[orphan.dataset_id] = orphan
        self.w._refresh_data_navigator()
        self.assertEqual(self.item(('workbook', book.workbook_id)).label, 'User title\u2028' + LONG_NAME)
        item = self.item(('heatmap', (orphan.dataset_id, orphan_graph.graph_id)))
        self.assertEqual(self.parent(self.parent(item)).label, 'detached.dat')
        self.assertIsNone(self.parent(self.parent(item)).parent)
        key = self.parent(self.parent(item)).key
        self.w._refresh_data_navigator()
        self.assertEqual(self.item(key).label, 'detached.dat')

    def test_reloaded_copies_and_duplicate_embedded_books_keep_distinct_full_labels(self):
        first, _, _ = self.source()
        second, _, _ = self.source()
        embedded = [Workbook(name='Calibration') for _ in range(2)]
        for book in embedded:
            self.w.workspace.workbooks[book.workbook_id] = book
        self.w._refresh_data_navigator()
        labels = [self.item(('workbook', book.workbook_id)).label
                  for book in (first, second, *embedded)]
        self.assertEqual(len(set(labels)), 4)
        self.assertEqual(labels[:2], [LONG_NAME + ' (1)', LONG_NAME + ' (2)'])
        self.assertTrue(all('Calibration' in label and 'embedded' in label for label in labels[2:]))
        self.w._refresh_data_navigator()
        self.assertEqual(labels, [self.item(('workbook', book.workbook_id)).label
                                 for book in (first, second, *embedded)])

    def test_group_keyboard_mouse_and_actions_never_route_business_objects(self):
        book, session, graph = self.source()
        self.w._refresh_data_navigator()
        self.settle()
        group = self.parent(self.item(('sheet', book.sheet_ids[0])))
        click_project_row(self.w, group.key)
        with patch.object(self.w, 'open_workbook') as workbook, patch.object(self.w, 'open_graph_document') as heatmap:
            QTest.keyClick(self.w.root_window, Qt.Key_Return)
            self.assertFalse(self.project.is_expanded(group.key))
            QTest.keyClick(self.w.root_window, Qt.Key_Return)
            self.assertTrue(self.project.is_expanded(group.key))
            item = project_row(self.w, group.key)
            from PySide6.QtCore import QPointF
            point = item.mapToScene(QPointF(item.width()-20, item.height()/2)).toPoint()
            QTest.mouseDClick(self.w.root_window, Qt.LeftButton, Qt.NoModifier, point)
            self.assertFalse(self.project.is_expanded(group.key))
            workbook.assert_not_called()
            heatmap.assert_not_called()
        self.w._refresh_navigator_actions()
        for action in [*self.w._navigator_buttons.values(), self.w.compare_sheets_btn,
                       self.w.merge_workbooks_btn, self.w.reload_source_action, self.w.project_analyze_action]:
            self.assertFalse(action.isEnabled())
        self.assertEqual(self.w._selected_sheet_ids(), [])
        self.assertEqual(self.w._selected_workbook_ids(), [])
        self.assertEqual(self.w._selected_reload_paths(), [])
        with patch('spectra_app.main_window.prompt_text') as rename, \
                patch('spectra_app.main_window.prompt_message') as remove:
            self.w._rename_navigator_item()
            self.w._remove_navigator_item()
            rename.assert_not_called()
            remove.assert_not_called()
        click_project_row(self.w, group.key, button=Qt.RightButton)
        self.assertTrue(project_control(self.w, 'projectContextMenu').property('visible'))
        self.assertTrue(self.project.contextIsGroup)
        self.assertFalse(self.project.contextExpanded)
        self.project.toggleContextGroup()
        self.assertTrue(self.project.is_expanded(group.key))
        QTest.keyClick(self.w.root_window, Qt.Key_Escape)

    def test_recursive_folding_current_selection_scroll_and_active_style_survive_refresh(self):
        first, one, graph_one = self.source(alias='Merged')
        second, two, graph_two = self.source(directory='run_b')
        merge_workbooks(self.w.workspace, [first.workbook_id, second.workbook_id], first.workbook_id)
        for index in range(20):
            book = Workbook(name=f'Extra {index} ' + LONG_NAME)
            self.w.workspace.workbooks[book.workbook_id] = book
        self.w._refresh_data_navigator()
        self.w.open_graph_document(one.dataset_id, graph_one.graph_id)
        self.settle()
        heatmap_key = ('heatmap', (one.dataset_id, graph_one.graph_id))
        self.project.select_key(heatmap_key)
        data = self.parent(self.item(('sheet', first.sheet_ids[0])))
        heatmap_group = self.parent(self.item(heatmap_key))
        other_source = self.parent(self.parent(self.item(('sheet', second.sheet_ids[0]))))
        folded = {row.key for row in (data, heatmap_group, other_source)}
        for key in folded:
            self.project.set_expanded(key, False)
        self.project.select_key(('workbook', first.workbook_id), Qt.ControlModifier)
        self.project.select_key(heatmap_key, context=True)
        selected = set(self.project.selected_keys())
        settle_project(self.w)
        view = project_control(self.w, 'projectList')
        view.setProperty('contentY', 80)
        settle_project(self.w)
        scroll = view.property('contentY')
        self.w._refresh_data_navigator()
        self.settle()
        self.assertEqual(self.project.current_key(), heatmap_key)
        self.assertEqual(set(self.project.selected_keys()), selected)
        self.assertEqual(view.property('contentY'), scroll)
        self.assertTrue(all(not self.project.is_expanded(key) for key in folded))
        self.project.set_expanded(heatmap_group.key, True)
        active = project_row(self.w, heatmap_key)
        self.assertTrue(active.property('active'))
        self.assertIn('Active document', active.property('description'))
        self.assertTrue(self.w.graph_documents.close_document(heatmap_key[1]))
        self.w._refresh_navigator_active()
        self.assertFalse(active.property('active'))


if __name__ == '__main__':
    unittest.main()
