import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt, QEvent, QTimer, QSignalBlocker
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

from spectra_app.main_window import MainWindow
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])


class SheetBatchComparisonTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.window = MainWindow()
        self.errors = []
        self.window._show_error = self.errors.append
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.close()
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def populate(self):
        # Each row/grid is deliberate: batch plotting must never align by index.
        books = [create_workbook(self.window.workspace, name) for name in ('Alpha', 'Beta')]
        arrays = ([[10, 2, 30], [20, 1, 40]], [[4, 50], [8, 60], [12, 70]],
                  [[9, 8, .5]], [[11, 3], [12, 2]], [[7, 90], [6, 80]])
        names = (['a', 'time', 'b'], ['time', 'a'], ['a', 'b', 'time'], ['a', 'time'], ['time', 'a'])
        sheets = []
        for i, (values, columns) in enumerate(zip(arrays, names)):
            path = self.folder / f'source-{i}.csv'
            path.write_text('external source', encoding='utf-8')
            sheet = register_xy_sheet(self.window.workspace, books[0 if i < 3 else 1].workbook_id,
                f'Sample {i+1}', np.array(values, dtype=float), columns, source_path=path)
            sheets.append(sheet)
        self.window._auxiliary_changed()
        return books, sheets

    def tree_items(self, tree):
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            yield parent
            for j in range(parent.childCount()):
                yield parent.child(j)

    def navigator_item(self, key):
        return next(item for item in self.tree_items(self.window.data_navigator) if item.data(0, Qt.UserRole) == key)

    def source_item(self, dialog, sheet):
        return next(item for item in self.tree_items(dialog.source_tree) if item.data(0, Qt.UserRole) == sheet.sheet_id)

    def configure(self, dialog, sheet, x, ys, mode='raw', include=True):
        item = self.source_item(dialog, sheet)
        dialog.source_tree.setCurrentItem(item)
        item.setCheckState(0, Qt.Checked if include else Qt.Unchecked)
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData(mode))
        dialog.x_combo.setCurrentIndex(x)
        for i in range(dialog.y_list.count()):
            dialog.y_list.item(i).setCheckState(Qt.Checked if i in ys else Qt.Unchecked)

    def modal(self, trigger, action):
        failures = []
        def interact():
            dialog = APP.activeModalWidget()
            try:
                self.assertIsInstance(dialog, QDialog)
                action(dialog)
            except BaseException as exc:
                failures.append(exc)
                if dialog:
                    dialog.reject()
        QTimer.singleShot(0, interact)
        trigger()
        if failures:
            raise failures[0]

    def show_navigator(self):
        self.window.show()
        self.window.project_panel_action.setChecked(True)
        APP.processEvents()

    def test_ctrl_shift_batch_dialog_creates_one_graph_with_independent_arrays(self):
        books, sheets = self.populate()
        tree = self.window.data_navigator
        self.show_navigator()
        def click(sheet, modifiers=Qt.NoModifier):
            item = self.navigator_item(('sheet', sheet.sheet_id))
            tree.scrollToItem(item)
            QTest.mouseClick(tree.viewport(), Qt.LeftButton, modifiers, tree.visualItemRect(item).center())
            if modifiers & Qt.ControlModifier:
                QTest.keyRelease(tree, Qt.Key_Control)
            if modifiers & Qt.ShiftModifier:
                QTest.keyRelease(tree, Qt.Key_Shift)
        click(sheets[0])
        click(sheets[2], Qt.ShiftModifier)
        click(sheets[3], Qt.ControlModifier)
        click(sheets[4], Qt.ControlModifier)
        self.assertEqual(len(tree.selectedItems()), 5, 'Navigator must support Ctrl/Shift across Workbooks')
        self.assertFalse(self.window.graph_documents.keys(), 'Sources need not be opened')
        def configure(dialog):
            self.assertEqual(list(dialog.selected_mappings()), [sheet.sheet_id for sheet in sheets])
            for sheet, x, ys in zip(sheets, [1, 0, 2, 1, 0], [[0,2], [1], [0], [0], [1]]):
                self.configure(dialog, sheet, x, ys)
            self.assertIn('6', dialog.summary.text())
            dialog.commit_btn.click()
        self.modal(self.window.compare_sheets_btn.trigger, configure)
        self.assertFalse(self.errors)
        self.assertEqual(len(self.window.workspace.comparisons), 1)
        graph = self.window.graph_documents.page_for(self.window.graph_documents._active_key)
        expected = [([2,1], [10,20]), ([2,1], [30,40]), ([4,8,12], [50,60,70]),
                    ([.5], [9]), ([3,2], [11,12]), ([7,6], [90,80])]
        self.assertEqual(len(graph.axes.lines), len(expected))
        for line, (x,y) in zip(graph.axes.lines, expected):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)
        saved = self.window.save_project(self.folder / 'batch.spectra')
        for sheet in sheets:
            sheet.source_path.unlink()
        self.assertTrue(self.window.open_project(saved))
        graph = self.window.graph_documents.page_for(self.window.graph_documents._active_key)
        self.assertEqual(len(graph.axes.lines), 6)
        self.assertEqual(self.window.workspace.sheets[sheets[0].sheet_id].y_columns, [0,2])
        for line, (x,y) in zip(graph.axes.lines, expected):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)

    def test_graph_add_has_fixed_owner_even_when_navigator_and_last_target_are_other(self):
        books, sheets = self.populate()
        a = create_comparison(self.window.workspace, 'A')
        b = create_comparison(self.window.workspace, 'B')
        page = self.window.open_xy_comparison(a.comparison_id)
        self.assertTrue(hasattr(page, 'add_data_btn'), 'Empty graphs need a discoverable Add data action')
        self.window._refresh_data_navigator()
        self.window.data_navigator.setCurrentItem(self.navigator_item(('xy', b.comparison_id)))
        self.assertEqual(self.window._last_comparison_id, b.comparison_id)
        def choose(dialog):
            self.window._last_comparison_id = b.comparison_id
            self.assertEqual(dialog.destination_combo.currentData(), a.comparison_id)
            self.assertFalse(dialog.destination_combo.isEnabled())
            self.assertFalse(dialog.selected_mappings())
            self.configure(dialog, sheets[0], 1, [0,2])
            self.configure(dialog, sheets[1], 0, [1])
            dialog.commit_btn.click()
        self.modal(page.add_data_btn.click, choose)
        self.assertFalse(self.errors)
        self.assertEqual(len(a.curves), 3)
        self.assertFalse(b.curves)
        self.assertEqual(len(page.axes.lines), 3)
        self.assertEqual(self.window.graph_documents._active_key, ('xy', a.comparison_id, ''))
        self.modal(page.add_data_btn.click, lambda dialog: (self.assertFalse(dialog.selected_mappings()), dialog.reject()))
        self.assertEqual(len(a.curves), 3)

    def test_cancel_stages_mapping_without_dirtying_or_opening_documents(self):
        books, sheets = self.populate()
        self.assertTrue(hasattr(self.window, 'compare_sheets_btn'))
        self.window.save_project(self.folder / 'clean.spectra')
        before = copy.deepcopy([(s.x_column, s.y_columns, s.mode) for s in sheets])
        self.window.data_navigator.setCurrentItem(self.navigator_item(('sheet', sheets[0].sheet_id)))
        def cancel(dialog):
            self.configure(dialog, sheets[0], 1, [0,2])
            QTest.keyClick(dialog, Qt.Key_Escape)
        self.modal(self.window.compare_sheets_btn.trigger, cancel)
        self.window._refresh_project_modified()
        self.assertFalse(self.window.isWindowModified())
        self.assertFalse(self.window.workspace.comparisons)
        self.assertFalse(self.window.graph_documents.keys())
        self.assertEqual(before, [(s.x_column, s.y_columns, s.mode) for s in sheets])

    def test_invalid_and_stale_sources_targets_and_busy_reject_whole_batch(self):
        _, sheets = self.populate()
        self.assertTrue(hasattr(self.window, 'compare_sheets'))
        before = copy.deepcopy([(s.x_column, s.y_columns, s.mode) for s in sheets])
        self.window.save_project(self.folder / 'validation-clean.spectra')
        first = {sheets[0].sheet_id: (1, [0,2], 'raw')}
        for invalid in ((0, [], 'raw'), (0, [0], 'raw'), (0, [99], 'raw'), (0, [1], 'processed')):
            with self.assertRaisesRegex(ValueError, 'Sample 2'):
                self.window.compare_sheets({**first, sheets[1].sheet_id: invalid})
        with self.assertRaisesRegex(ValueError, 'missing|exist|available'):
            self.window.compare_sheets({**first, 'removed-sheet': (0,[1],'raw')})
        with self.assertRaisesRegex(ValueError, 'comparison|target'):
            self.window.compare_sheets(first, 'removed-target')
        with patch.object(self.window, '_project_busy', return_value=True):
            with self.assertRaisesRegex(ValueError, 'Wait'):
                self.window.compare_sheets(first)
        self.assertFalse(self.window.workspace.comparisons)
        self.assertFalse(self.window.graph_documents.keys())
        self.assertEqual(before, [(s.x_column, s.y_columns, s.mode) for s in sheets])
        self.window._refresh_project_modified()
        self.assertFalse(self.window.isWindowModified())

    def test_invalid_target_draft_retained_and_existing_curves_styles_unchanged(self):
        books, sheets = self.populate()
        self.assertTrue(hasattr(self.window, 'compare_sheets'))
        target = create_comparison(self.window.workspace, 'Target')
        old = add_curve(self.window.workspace, target.comparison_id, sheets[0].sheet_id, 0, 1,
                        color='#123456', width=2.25, visible=False, label='Retain me')
        page = self.window.open_xy_comparison(target.comparison_id)
        self.window.open_workbook(books[0].workbook_id)
        page.color_edit.setText('invalid')
        mapping = {sheets[0].sheet_id: (1, [0,2], 'raw')}
        with self.assertRaisesRegex(ValueError, 'six-digit'):
            self.window.compare_sheets(mapping, target.comparison_id)
        self.assertEqual(page.color_edit.text(), 'invalid')
        self.assertEqual(sheets[0].x_column, 0)
        self.assertEqual(len(target.curves), 1)
        page.color_edit.setText('#123456')
        retained = copy.deepcopy(old)
        self.window.compare_sheets(mapping, target.comparison_id)
        self.assertEqual(old, retained)
        self.assertEqual(len(target.curves), 3)
        self.assertEqual(self.window.graph_documents._active_key, ('workbook', books[0].workbook_id, ''))

    def test_navigator_refresh_and_context_keep_selection_and_guard_destructive_actions(self):
        books, sheets = self.populate()
        tree = self.window.data_navigator
        self.show_navigator()
        first = self.navigator_item(('sheet', sheets[0].sheet_id))
        second = self.navigator_item(('sheet', sheets[1].sheet_id))
        tree.setCurrentItem(first)
        second.setSelected(True)
        self.navigator_item(('workbook', books[1].workbook_id)).setExpanded(False)
        keys = {item.data(0,Qt.UserRole) for item in tree.selectedItems()}
        self.window._refresh_data_navigator()
        self.assertEqual({item.data(0,Qt.UserRole) for item in tree.selectedItems()}, keys)
        self.assertFalse(self.navigator_item(('workbook', books[1].workbook_id)).isExpanded())
        self.assertEqual(tree.currentItem().data(0,Qt.UserRole), ('sheet', sheets[0].sheet_id))
        second = self.navigator_item(('sheet', sheets[1].sheet_id))
        point = tree.visualItemRect(second).center()
        checked = []
        def inspect_menu():
            menu = APP.activePopupWidget()
            if isinstance(menu, QMenu):
                checked.append({a.text(): a.isEnabled() for a in menu.actions()})
                menu.close()
        QTimer.singleShot(0, inspect_menu)
        APP.sendEvent(tree.viewport(), QContextMenuEvent(QContextMenuEvent.Mouse, point, tree.viewport().mapToGlobal(point)))
        self.assertTrue(checked)
        self.assertFalse(checked[0]['Remove 2 selected items…'])
        self.assertFalse(checked[0]['Rename…'])
        self.assertTrue(checked[0]['Compare selected data…'])
        self.assertEqual({item.data(0,Qt.UserRole) for item in tree.selectedItems()}, keys)
        with patch('spectra_app.main_window.QMessageBox.warning') as warning:
            self.window._remove_navigator_item()
            warning.assert_not_called()
        with patch('spectra_app.main_window.QInputDialog.getText') as rename:
            self.window._rename_navigator_item()
            rename.assert_not_called()
        self.assertEqual(len(self.window.workspace.sheets), 5)
        first = self.navigator_item(('sheet', sheets[0].sheet_id))
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, tree.visualItemRect(first).center())
        with patch('spectra_app.main_window.QInputDialog.getText', return_value=('Renamed', True)):
            self.window._rename_navigator_item()
        self.assertEqual(sheets[0].name, 'Renamed')
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Yes):
            self.window._remove_navigator_item()
        self.assertNotIn(sheets[0].sheet_id, self.window.workspace.sheets)

    def test_selected_workbook_deduplicates_children_in_project_order(self):
        books, sheets = self.populate()
        self.assertTrue(hasattr(self.window, '_selected_sheet_ids'))
        tree = self.window.data_navigator
        tree.setCurrentItem(self.navigator_item(('sheet', sheets[4].sheet_id)))
        self.navigator_item(('workbook', books[0].workbook_id)).setSelected(True)
        self.navigator_item(('sheet', sheets[0].sheet_id)).setSelected(True)
        comparison = create_comparison(self.window.workspace, 'Ignored graph')
        self.window._refresh_data_navigator()
        self.navigator_item(('xy', comparison.comparison_id)).setSelected(True)
        self.assertEqual(self.window._selected_sheet_ids(), [s.sheet_id for s in sheets[:3]] + [sheets[4].sheet_id])

    def test_metadata_refresh_preserves_scrolled_navigator(self):
        books, sheets = self.populate()
        # The persistent Project pane has more tree height than the old
        # command-heavy sidebar. Populate enough rows to require scrolling.
        for i in range(95):
            register_xy_sheet(self.window.workspace, books[0].workbook_id, f'Extra source {i}',
                              np.array([[1.,2.]]), ['x','y'])
        self.window._refresh_data_navigator()
        self.show_navigator()
        tree = self.window.data_navigator
        item = self.navigator_item(('sheet', books[0].sheet_ids[95]))
        tree.setCurrentItem(item)
        tree.scrollToItem(item)
        self.navigator_item(('workbook', books[1].workbook_id)).setExpanded(False)
        APP.processEvents()
        scroll = tree.verticalScrollBar().value()
        self.assertGreater(scroll, 0)
        self.window._auxiliary_changed()
        APP.processEvents()
        self.assertEqual(tree.verticalScrollBar().value(), scroll)
        self.assertEqual(tree.currentItem().data(0, Qt.UserRole), ('sheet', books[0].sheet_ids[95]))
        self.assertFalse(self.navigator_item(('workbook', books[1].workbook_id)).isExpanded())

    def test_append_dialog_preserves_source_selection_closed_target_and_unrelated_draft(self):
        books, sheets = self.populate()
        target = create_comparison(self.window.workspace, 'Append here')
        other = create_comparison(self.window.workspace, 'Keep draft')
        graph = self.window.open_xy_comparison(target.comparison_id)
        unrelated = self.window.open_xy_comparison(other.comparison_id)
        workbook = self.window.open_workbook(books[0].workbook_id)
        page = workbook.sheet_pages[sheets[0].sheet_id]
        page.table.selectColumn(1)
        page.table.setCurrentIndex(page.model.index(1, 1))
        selection = [(i.row(), i.column()) for i in page.table.selectedIndexes()]
        unrelated.name_edit.setText('')
        self.window.graph_documents.close_document(('xy', target.comparison_id, ''))
        self.window.data_navigator.setCurrentItem(self.navigator_item(('sheet', sheets[0].sheet_id)))
        def append(dialog):
            self.configure(dialog, sheets[0], 1, [0,2])
            # An edited unchecked Sheet must retain its old defaults.
            self.configure(dialog, sheets[2], 2, [0], include=False)
            dialog.destination_combo.setCurrentIndex(dialog.destination_combo.findData(target.comparison_id))
            dialog.commit_btn.click()
        self.modal(self.window.compare_sheets_btn.trigger, append)
        self.assertFalse(self.errors)
        self.assertEqual(len(target.curves), 2)
        self.assertEqual(len(graph.axes.lines), 2)
        self.assertIsNone(self.window.graph_documents.window_for(('xy', target.comparison_id, '')))
        self.assertEqual(self.window.graph_documents._active_key, ('workbook', books[0].workbook_id, ''))
        self.assertEqual([(i.row(), i.column()) for i in page.table.selectedIndexes()], selection)
        self.assertEqual(page.table.currentIndex().column(), 1)
        self.assertEqual(page.x_combo.currentData(), 1)
        self.assertIn('(X)', page.model.headerData(1, Qt.Horizontal))
        self.assertEqual(sheets[2].x_column, 0)
        self.assertEqual(unrelated.name_edit.text(), '')
        unrelated.name_edit.setText('Keep draft')

    def test_dialog_validation_no_y_stale_source_and_stale_target_cannot_accept(self):
        _, sheets = self.populate()
        from spectra_app.sheet_comparison_dialog import SheetComparisonDialog
        target = create_comparison(self.window.workspace, 'Target')
        dialog = SheetComparisonDialog(self.window.workspace, selected_sheet_ids=[sheets[0].sheet_id])
        self.addCleanup(dialog.deleteLater)
        self.configure(dialog, sheets[0], 1, [])
        self.assertFalse(dialog.commit_btn.isEnabled())
        self.assertIn('Sample 1', dialog.feedback.text())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.configure(dialog, sheets[0], 1, [0,2])
        self.assertTrue(dialog.commit_btn.isEnabled())
        dialog.destination_combo.setCurrentIndex(dialog.destination_combo.findData(target.comparison_id))
        del self.window.workspace.comparisons[target.comparison_id]
        dialog.accept()
        self.assertIn('comparison', dialog.feedback.text())
        self.assertEqual(dialog.result(), QDialog.Rejected)
        dialog.destination_combo.setCurrentIndex(0)
        saved_sheet = self.window.workspace.sheets.pop(sheets[0].sheet_id)
        try:
            dialog.accept()
            self.assertIn('Sample 1', dialog.feedback.text())
            self.assertIn('no longer exists', dialog.feedback.text())
            self.assertEqual(dialog.result(), QDialog.Rejected)
        finally:
            self.window.workspace.sheets[saved_sheet.sheet_id] = saved_sheet
        self.assertFalse(self.window.workspace.comparisons)
        self.assertEqual(sheets[0].x_column, 0)

    def test_preflight_invalid_target_does_not_commit_current_graph_draft(self):
        _, sheets = self.populate()
        a = create_comparison(self.window.workspace, 'Current')
        b = create_comparison(self.window.workspace, 'Invalid target')
        target = self.window.open_xy_comparison(b.comparison_id)
        current = self.window.open_xy_comparison(a.comparison_id)
        target.name_edit.setText('')
        current.name_edit.setText('Uncommitted current name')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.window.compare_sheets({sheets[0].sheet_id: (1,[0],'raw')}, b.comparison_id)
        self.assertEqual(a.name, 'Current')
        self.assertEqual(current.name_edit.text(), 'Uncommitted current name')
        self.assertEqual(target.name_edit.text(), '')
        self.assertFalse(a.curves)
        self.assertFalse(b.curves)
        self.assertEqual(sheets[0].x_column, 0)
        target.name_edit.setText('Invalid target')

    def test_mixed_dat_modes_use_own_gate_labels_preview_and_keep_scientific_context(self):
        _, sheets = self.populate()
        dat_sheets = []
        sessions = []
        for i, header in enumerate(('Energy(eV) -2 4', 'Energy(eV) 7 11')):
            path = self.folder / f'gates-{i}.dat'
            path.write_text(header + '\n' + '\n'.join(f'{1+j/10} {j+1} {2*j+2}' for j in range(13)), encoding='utf-8')
            self.window._load_dat_file_direct(path)
            session = self.window.workspace.active
            sessions.append(session)
            dat_sheets.append(next(s for s in self.window.workspace.sheets.values() if s.dataset_id == session.dataset_id))
        # An inactive cached DAT table has a selected Gate cell. Refresh must not
        # emit a current-column request that activates this dataset or its Gate.
        first_book = self.window._sheet_owner(dat_sheets[0].sheet_id)
        cached = self.window.open_workbook(first_book.workbook_id).sheet_pages[dat_sheets[0].sheet_id]
        self.window._replace_current_data(replace(sessions[1].current_cube, z=sessions[1].current_cube.z * 3),
                                         mode='energy_second_derivative', smooth=False, window_length=5)
        dat_sheets[1] = next(s for s in self.window.workspace.sheets.values()
                             if s.dataset_id == sessions[1].dataset_id and s.role == 'processed')
        with QSignalBlocker(cached.table.selectionModel()):
            cached.table.setCurrentIndex(cached.model.index(3, 2))
            cached.table.selectColumn(2)
        self.assertEqual(cached.table.currentIndex().column(), 2)
        target = create_comparison(self.window.workspace, 'Mixed sources')
        graph = self.window.open_xy_comparison(target.comparison_id)
        active = self.window.workspace.active_dataset_id
        gate = self.window._current_gate_index()
        current_cube = self.window.current_cube
        expected_raw = sessions[0].base_cube.z[1].copy()
        expected_applied = sessions[1].current_cube.z[0].copy()
        def choose(dialog):
            self.configure(dialog, sheets[0], 1, [0])
            self.assertEqual(dialog.mode_combo.count(), 1)
            self.configure(dialog, dat_sheets[0], 0, [2], 'raw')
            self.assertFalse(dialog.x_combo.isEnabled())
            self.assertIn('4', dialog.y_list.item(2).text())
            model = dialog.preview.model()
            self.assertLessEqual(model.rowCount(), 100)
            np.testing.assert_array_equal(model.values[2], expected_raw[:100])
            self.assertTrue(np.shares_memory(model.values[2], sessions[0].base_cube.z))
            self.configure(dialog, dat_sheets[1], 0, [1], 'processed')
            self.assertIn('7', dialog.y_list.item(1).text())
            np.testing.assert_array_equal(dialog.preview.model().values[1], expected_applied[:100])
            self.assertEqual(self.window.workspace.active_dataset_id, active)
            self.assertEqual(self.window._current_gate_index(), gate)
            dialog.commit_btn.click()
        self.modal(graph.add_data_btn.click, choose)
        self.assertFalse(self.errors)
        self.assertEqual(len(graph.axes.lines), 3)
        np.testing.assert_array_equal(graph.axes.lines[0].get_xdata(), [2,1])
        np.testing.assert_array_equal(graph.axes.lines[1].get_ydata(), expected_raw)
        np.testing.assert_array_equal(graph.axes.lines[2].get_ydata(), expected_applied)
        self.assertEqual(self.window.workspace.active_dataset_id, active)
        self.assertEqual(self.window._current_gate_index(), gate)
        self.assertIs(self.window.current_cube, current_cube)
        self.assertEqual(cached.table.currentIndex().column(), 2)
        self.assertEqual(self.window.graph_documents._active_key, ('xy', target.comparison_id, ''))


if __name__ == '__main__':
    unittest.main()
