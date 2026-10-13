import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import Qt, QItemSelection, QItemSelectionModel, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
from dataclasses import replace
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])
REAL_DISCARD = MainWindow._confirm_project_discard


class WorkbookWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.window = MainWindow()
        self.window._show_error = lambda message: self.fail(message)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.close()
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def import_three(self):
        texts = ('signal,time\n10,1\n20,2\n30,3\n',
                 'unused,time,signal\n99,4,40\n98,8,80\n',
                 'signal,unused,time\n5,0,0.5\n15,0,1.5\n25,0,2.5\n35,0,3.5\n')
        paths = []
        for index, text in enumerate(texts):
            path = self.folder / f'{index}.csv'
            path.write_text(text, encoding='utf-8')
            paths.append(path)
        return paths, self.window.import_xy_files(paths, grouping='single')

    def test_three_file_controls_keep_independent_grids_and_copy_readonly_table(self):
        paths, book = self.import_three()
        page = self.window.open_workbook(book.workbook_id)
        for index, (x, y) in enumerate(((1, 0), (1, 2), (2, 0))):
            sheet_page = page.sheet_pages[book.sheet_ids[index]]
            page.tabs.setCurrentWidget(sheet_page)
            sheet_page.x_combo.setCurrentIndex(x)
            for row in range(sheet_page.y_list.count()):
                sheet_page.y_list.item(row).setCheckState(Qt.Checked if row == y else Qt.Unchecked)
            (sheet_page.new_comparison_btn if index == 0 else sheet_page.add_comparison_btn).click()
        comparison = next(iter(self.window.workspace.comparisons.values()))
        graph = self.window.open_xy_comparison(comparison.comparison_id)
        self.assertEqual(len(graph.axes.lines), 3)
        for line, x, y in zip(graph.axes.lines, ([1,2,3], [4,8], [.5,1.5,2.5,3.5]),
                              ([10,20,30], [40,80], [5,15,25,35])):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)
        sheet_page = page.sheet_pages[book.sheet_ids[0]]
        model = sheet_page.model
        self.assertFalse(model.flags(model.index(0,0)) & Qt.ItemIsEditable)
        selection = QItemSelection(model.index(0,0), model.index(1,1))
        sheet_page.table.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)
        sheet_page.copy_selection()
        self.assertEqual(APP.clipboard().text(), '10\t1\n20\t2')
        self.window._refresh_project_modified()
        self.assertTrue(self.window.isWindowModified())

    def test_xy_only_save_without_sources_and_failed_install_restores_same_pages(self):
        paths, book = self.import_three()
        workbook_page = self.window.open_workbook(book.workbook_id)
        sheet_page = workbook_page.sheet_pages[book.sheet_ids[0]]
        sheet_page.new_comparison_btn.click()
        comparison = next(iter(self.window.workspace.comparisons.values()))
        graph = self.window.open_xy_comparison(comparison.comparison_id)
        saved = self.window.save_project(self.folder / 'tables.spectra')
        for path in paths:
            path.unlink()
        with patch.object(self.window, '_project_installed_workspace', side_effect=ValueError('injected')):
            with self.assertRaisesRegex(ValueError, 'injected'):
                self.window.open_project(saved)
        self.assertIs(self.window.graph_documents.page_for(('workbook', book.workbook_id, '')), workbook_page)
        self.assertIs(self.window.graph_documents.page_for(('xy', comparison.comparison_id, '')), graph)
        self.assertTrue(self.window.open_project(saved))
        self.assertEqual(len(self.window.workspace.sheets), 3)
        self.assertEqual(self.window.graph_documents._active_key, ('xy', comparison.comparison_id, ''))
        self.window._refresh_project_modified()
        self.assertFalse(self.window.isWindowModified())

    def test_table_shortcut_is_scoped_and_copies_full_precision_rows_and_columns(self):
        paths, first = self.import_three()
        path = self.folder / 'precise.csv'
        path.write_text('x,y\n1.2345678901234567,8\n2.3456789012345678,9\n', encoding='utf-8')
        second = self.window.import_xy_files([path])
        page = self.window.open_workbook(second.workbook_id).sheet_pages[second.sheet_ids[0]]
        self.window.show()
        self.window.graph_documents.tile_graphs()
        APP.processEvents()
        page.table.selectRow(0)
        page.table.setFocus()
        QTest.keyClick(page.table, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(APP.clipboard().text(), '1.2345678901234567\t8')
        page.table.clearSelection()
        page.table.selectColumn(1)
        QTest.keyClick(page.table, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(APP.clipboard().text(), '8\n9')
        duplicate = self.window.import_xy_files([path])
        self.assertEqual(duplicate.workbook_id, second.workbook_id)
        self.assertEqual(len(self.window.workspace.workbooks), 2)

    def test_invalid_xy_drafts_block_save_switch_and_close_until_corrected(self):
        paths, book = self.import_three()
        workbook = self.window.open_workbook(book.workbook_id)
        workbook.sheet_pages[book.sheet_ids[0]].new_comparison_btn.click()
        comparison = next(iter(self.window.workspace.comparisons.values()))
        page = self.window.open_xy_comparison(comparison.comparison_id)
        page.name_edit.setText(' ')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.window.save_project(self.folder / 'invalid.spectra')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.window.open_workbook(book.workbook_id)
        self.assertFalse(self.window.graph_documents.close_document())
        self.assertEqual(comparison.name, 'Comparison 1')
        page.name_edit.setText('Measurements')
        page.color_edit.setText('red')
        with self.assertRaisesRegex(ValueError, 'six-digit'):
            self.window.save_project(self.folder / 'invalid.spectra')
        self.window._refresh_project_modified()
        self.assertTrue(self.window.isWindowModified())
        page.color_edit.setText('#123456')
        self.window.save_project(self.folder / 'valid.spectra')
        self.assertEqual(comparison.name, 'Measurements')
        self.assertEqual(next(iter(comparison.curves.values())).color, '#123456')
        self.window._refresh_comparison_targets()
        self.assertEqual(workbook.sheet_pages[book.sheet_ids[0]].target_combo.currentText(), 'Measurements')

    def test_outer_analysis_transition_rejects_invalid_xy_then_processing_preserves_edit(self):
        from tests.background_helpers import wait_jobs
        w = self.window
        path = self.folder / 'science.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i*i+1} {i*i+2}' for i in range(31)), encoding='utf-8')
        w._load_dat_file_direct(path)
        book = next(iter(w.workspace.workbooks.values()))
        workbook = w.open_workbook(book.workbook_id)
        workbook.sheet_pages[book.sheet_ids[0]].new_comparison_btn.click()
        comparison = next(iter(w.workspace.comparisons.values()))
        page = w.open_xy_comparison(comparison.comparison_id)
        page.color_edit.setText('red')
        page.color_edit.editingFinished.emit()
        w.plot_tabs.setCurrentIndex(0)
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertEqual(page.color_edit.text(), 'red')
        page.color_edit.setText('#567890')
        page.label_edit.setText('Committed before processing')
        w.plot_tabs.setCurrentIndex(0)
        self.assertEqual(w.plot_tabs.currentIndex(), 0)
        w.processing_mode_combo.setCurrentIndex(w.processing_mode_combo.findData('energy_second_derivative'))
        w._on_generate_heatmap()
        wait_jobs(w)
        self.assertEqual(w.workspace.active.applied.mode, 'energy_second_derivative')
        curve = next(iter(comparison.curves.values()))
        self.assertEqual((curve.label, curve.color), ('Committed before processing', '#567890'))
        # External source refresh also must not overwrite a pending editor.
        page.color_edit.setText('invalid')
        w._load_dat_file_direct(path)
        self.assertEqual(page.color_edit.text(), 'invalid')
        page.color_edit.setText('#567890')

    def test_dat_gate_link_live_processing_and_visible_document_routing(self):
        w = self.window
        paths = []
        for suffix in ('a', 'b'):
            path = self.folder / f'{suffix}.dat'
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
            paths.append(path)
            w._load_dat_file_direct(path)
        sessions = list(w.workspace.datasets.values())
        first = sessions[0]
        sheet = next(sheet for sheet in w.workspace.sheets.values() if sheet.dataset_id == first.dataset_id)
        # This live-update case compares one raw Gate with its processed counterpart.
        sheet.y_columns = [1]
        book = w._sheet_owner(sheet.sheet_id)
        workbook = w.open_workbook(book.workbook_id)
        page = workbook.sheet_pages[sheet.sheet_id]
        np.testing.assert_array_equal(page.model.values[0], first.base_cube.energy)
        np.testing.assert_array_equal(page.model.values[2], first.base_cube.z[1])
        page.table.setCurrentIndex(page.model.index(0, 2))
        self.assertEqual(w.workspace.active_dataset_id, first.dataset_id)
        self.assertEqual(w._current_gate_index(), 1)
        self.assertEqual(w.graph_documents._active_key, ('workbook', book.workbook_id, ''))
        self.assertFalse(w.graph_command_host.isVisible())
        w.plot_tabs.setCurrentIndex(0)
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        self.assertEqual(w.graph_documents._active_key, ('workbook', book.workbook_id, ''))
        page.new_comparison_btn.click()
        comparison = next(iter(w.workspace.comparisons.values()))
        w.open_workbook(book.workbook_id)
        from spectra_app.workbook_state import ensure_dataset_sheet
        processed = ensure_dataset_sheet(w.workspace, first.dataset_id, 'processed')
        processed.y_columns = [1]
        w._refresh_auxiliary_documents()
        page = w.open_workbook(book.workbook_id, processed.sheet_id).sheet_pages[processed.sheet_id]
        page.add_comparison_btn.click()
        xy = w.open_xy_comparison(comparison.comparison_id)
        raw = first.base_cube.z[0].copy()
        w._replace_current_data(replace(first.current_cube, z=first.current_cube.z * 3),
                                mode='energy_second_derivative', smooth=False, window_length=5)
        np.testing.assert_array_equal(xy.axes.lines[0].get_ydata(), raw)
        np.testing.assert_array_equal(xy.axes.lines[1].get_ydata(), raw * 3)
        w._set_current_gate_from_index(2)
        self.assertEqual(page.model.gate_column, 3)
        self.assertEqual(w.graph_documents._active_key, ('xy', comparison.comparison_id, ''))
        self.assertFalse(w._graph_command_actions['undo'].isEnabled())
        w.open_workbook(book.workbook_id)
        with self.assertRaisesRegex(ValueError, 'table cells'):
            w.copy_graph_svg()
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Yes):
            w.remove_dataset(first.dataset_id)
        self.assertEqual(len(comparison.curves), 0)
        self.assertNotIn(sheet.sheet_id, w.workspace.sheets)
        self.assertIsNone(w.graph_documents.page_for(('workbook', book.workbook_id, '')))

    def test_navigator_rename_remove_and_curve_selection_preserve_invalid_drafts(self):
        paths, book = self.import_three()
        workbook = self.window.open_workbook(book.workbook_id)
        workbook.sheet_pages[book.sheet_ids[0]].new_comparison_btn.click()
        comparison = next(iter(self.window.workspace.comparisons.values()))
        self.window._add_sheet_comparison(book.sheet_ids[1], comparison.comparison_id)
        page = self.window.open_xy_comparison(comparison.comparison_id)
        page.color_edit.setText('red')
        page.color_edit.editingFinished.emit()
        self.window.data_navigator.setCurrentItem(self.window.data_navigator.topLevelItem(0))
        errors = []
        self.window._show_error = errors.append
        with patch('spectra_app.main_window.QInputDialog.getText', return_value=('Renamed', True)):
            self.window._rename_navigator_item()
        self.assertEqual(page.color_edit.text(), 'red')
        self.assertNotEqual(book.name, 'Renamed')
        self.assertTrue(errors)
        page.curve_list.setCurrentRow(1)
        self.assertEqual(page.curve_list.currentRow(), 0)
        self.assertEqual(page.color_edit.text(), 'red')
        page.color_edit.setText('#123456')
        page.label_edit.setText('First only')
        page.curve_list.setCurrentRow(1)
        curves = list(comparison.curves.values())
        self.assertEqual((curves[0].label, curves[0].color), ('First only', '#123456'))
        self.assertNotEqual(curves[1].label, 'First only')

    def test_removing_active_comparison_selects_workbook_and_never_copies_hidden_dat(self):
        w = self.window
        path = self.folder / 'spectra.dat'
        path.write_text('Energy(eV) 0 1\n1 2 3\n2 4 6\n3 6 9\n', encoding='utf-8')
        w._load_dat_file_direct(path)
        book = next(iter(w.workspace.workbooks.values()))
        workbook = w.open_workbook(book.workbook_id)
        workbook.sheet_pages[book.sheet_ids[0]].new_comparison_btn.click()
        item = next(w.data_navigator.topLevelItem(i) for i in range(w.data_navigator.topLevelItemCount())
                    if w.data_navigator.topLevelItem(i).data(0, Qt.UserRole)[0] == 'xy')
        w.data_navigator.setCurrentItem(item)
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Yes):
            w._remove_navigator_item()
        self.assertEqual(w.graph_documents._active_key, ('workbook', book.workbook_id, ''))
        self.assertFalse(w._graph_command_actions['copy_graph'].isEnabled())
        with patch.object(w, '_capture_formal_source', side_effect=AssertionError('hidden DAT')):
            with self.assertRaises(ValueError):
                w.copy_graph_svg()

    def test_mixed_project_closed_documents_and_last_dat_removal_keep_xy(self):
        w = self.window
        dat = self.folder / 'matrix.dat'
        dat.write_text('Energy(eV) 0 1\n1 2 3\n2 4 6\n3 6 9\n', encoding='utf-8')
        w._load_dat_file_direct(dat)
        session = w.workspace.active
        dat_book = next(iter(w.workspace.workbooks.values()))
        w.open_workbook(dat_book.workbook_id)
        paths, xy_book = self.import_three()
        xy_book.name = 'Retained numeric tables'
        workbook = w.open_workbook(xy_book.workbook_id)
        workbook.sheet_pages[xy_book.sheet_ids[0]].new_comparison_btn.click()
        comparison = next(iter(w.workspace.comparisons.values()))
        dat_key = ('workbook', dat_book.workbook_id, '')
        self.assertTrue(w.graph_documents.close_document(dat_key))
        saved = w.save_project(self.folder / 'mixed.spectra')
        for path in [dat, *paths]:
            path.unlink()
        self.assertTrue(w.open_project(saved))
        self.assertIsNone(w.graph_documents.window_for(dat_key))
        self.assertEqual(w.graph_documents._active_key, ('xy', comparison.comparison_id, ''))
        self.assertEqual(w.workspace.workbooks[xy_book.workbook_id].name, 'Retained numeric tables')
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Yes):
            w.remove_dataset(session.dataset_id)
        self.assertEqual(len(w.workspace.datasets), 0)
        self.assertIsNotNone(w.graph_documents.page_for(('xy', comparison.comparison_id, '')))
        self.assertEqual(len(w.workspace.comparisons[comparison.comparison_id].curves), 1)
        for key in tuple(w.graph_documents.visible_keys()):
            w.graph_documents.close_document(key)
        closed = w.save_project(self.folder / 'all-closed.spectra')
        self.assertTrue(w.open_project(closed))
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        self.assertEqual(w.graph_documents.subWindowList(), [])
        self.assertFalse(w._graph_command_actions['copy_graph'].isEnabled())
        self.assertEqual(len(w.workspace.sheets), 3)

    def test_unsaved_xy_only_workspace_runs_real_discard_guard(self):
        self.import_three()
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Cancel) as warning:
            self.assertFalse(REAL_DISCARD(self.window))
        warning.assert_called_once()
