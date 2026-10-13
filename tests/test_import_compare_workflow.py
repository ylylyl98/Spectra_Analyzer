import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import Qt, QPoint, QTimer, QEvent, QItemSelection, QItemSelectionModel
from PySide6.QtWidgets import QApplication, QDialog, QMenu
from PySide6.QtTest import QTest
from PySide6.QtGui import QContextMenuEvent
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings
from tests.background_helpers import wait_jobs

APP = QApplication.instance() or QApplication([])


class ImportCompareTests(unittest.TestCase):
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

    def files(self, texts):
        paths = []
        for index, text in enumerate(texts):
            path = self.folder / f'source-{index}.csv'
            path.write_text(text, encoding='utf-8')
            paths.append(path)
        return paths

    def dialog(self, paths):
        from spectra_app.xy_import_dialog import XYImportDialog
        tables, mappings, reused = self.window._prepare_xy_import(paths)
        dialog = XYImportDialog(tables, self.window.workspace.workbooks, mappings=mappings, reused=reused)
        # These cases exercise intentional batch grouping; per-file default has
        # its own actual-dialog acceptance test in test_workbook_organization.
        dialog.destination_combo.setCurrentIndex(dialog.destination_combo.findData(None))
        self.addCleanup(dialog.deleteLater)
        return dialog

    def configure(self, dialog, row, x, ys):
        dialog.file_list.setCurrentRow(row)
        dialog.x_combo.setCurrentIndex(x)
        for index in range(dialog.y_list.count()):
            dialog.y_list.item(index).setCheckState(Qt.Checked if index in ys else Qt.Unchecked)

    def commit(self, dialog, compare=True):
        return self.window.import_xy_files([], tables=dialog.tables, mappings=dialog.mappings,
            compare=compare, workbook_id=dialog.destination_combo.currentData(),
            workbook_name=dialog.name_edit.text(), grouping='single')

    def test_ten_matching_files_configure_once_and_plot_original_arrays(self):
        paths = self.files(['time,signal\n2,20\n1,10\n'] * 10)
        dialog = self.dialog(paths)
        self.configure(dialog, 0, 1, [0])
        dialog.apply_btn.click()
        self.assertIn('9', dialog.feedback.text())
        book = self.commit(dialog)
        graph = self.window.graph_documents.page_for(self.window.graph_documents._active_key)
        self.assertEqual(len(book.sheet_ids), 10)
        self.assertEqual(len(graph.axes.lines), 10)
        for line in graph.axes.lines:
            np.testing.assert_array_equal(line.get_xdata(), [20, 10])
            np.testing.assert_array_equal(line.get_ydata(), [2, 1])

    def test_three_independent_mappings_multiy_and_save_reopen(self):
        paths = self.files(['a,t,b\n10,2,30\n20,1,40\n', 't,a\n4,50\n8,60\n12,70\n', 'a,b,t\n9,8,.5\n'])
        dialog = self.dialog(paths)
        for row, x, ys in ((0, 1, [0, 2]), (1, 0, [1]), (2, 2, [0])):
            self.configure(dialog, row, x, ys)
        book = self.commit(dialog)
        graph = self.window.graph_documents.page_for(self.window.graph_documents._active_key)
        for line, x, y in zip(graph.axes.lines, ([2,1], [2,1], [4,8,12], [.5]), ([10,20], [30,40], [50,60,70], [9])):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)
        self.assertEqual(len(graph.axes.lines), 4)
        saved = self.window.save_project(self.folder / 'mapped.spectra')
        self.assertTrue(self.window.open_project(saved))
        self.assertEqual([(self.window.workspace.sheets[s].x_column, self.window.workspace.sheets[s].y_columns)
                          for s in book.sheet_ids], [(1,[0,2]), (0,[1]), (2,[0])])
        graph = self.window.graph_documents.page_for(self.window.graph_documents._active_key)
        self.assertEqual(len(graph.axes.lines), 4)

    def test_mapping_reuse_matches_names_and_skips_ambiguous_and_headerless_mismatch(self):
        paths = self.files(['t,a,b\n1,2,3\n', 'b,t,a\n3,1,2\n', 't,a,a\n1,2,3\n', 't,c,b\n1,2,3\n'])
        dialog = self.dialog(paths)
        self.configure(dialog, 0, 0, [1,2])
        dialog.apply_btn.click()
        self.assertEqual(dialog.mappings, [(0,[1,2]), (1,[2,0]), (0,[1,2]), (0,[1,2])])
        self.assertIn('skipped', dialog.feedback.text().lower())
        paths = self.files(['1,2,3\n', '4,5,6\n', '7,8\n', 'Column 1,Column 2,Column 3\n1,2,3\n'])
        dialog = self.dialog(paths)
        self.configure(dialog, 0, 2, [0])
        dialog.apply_btn.click()
        self.assertEqual(dialog.mappings, [(2,[0]), (2,[0]), (0,[1]), (0,[1,2])])

    def test_invalid_batch_and_mapping_are_atomic_and_name_source(self):
        paths = self.files(['x,y\n1,2\n', 'x,y\n2,bad\n'])
        with self.assertRaisesRegex(ValueError, 'source-1.csv'):
            self.window.import_xy_files(paths)
        self.assertFalse(self.window.workspace.workbooks)
        paths[1].write_text('x,y\n2,3\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.window.import_xy_files(paths, mappings=[(0,[1]), (1,[1])], compare=True)
        self.assertFalse(self.window.workspace.sheets)
        self.assertFalse(self.window.graph_documents.keys())

    def test_user_cancel_does_not_change_workspace_or_dirty_state(self):
        paths = self.files(['x,y\n1,2\n'])
        from spectra_app.xy_import_dialog import XYImportDialog
        self.window._refresh_project_modified()
        dirty = self.window.isWindowModified()
        def cancel(dialog):
            dialog.reject()
            return QDialog.Rejected
        with patch('spectra_app.main_window.QFileDialog.getOpenFileNames', return_value=(paths, '')), patch.object(XYImportDialog, 'exec', cancel):
            self.window._on_import_xy()
            wait_jobs(self.window)
        self.window._refresh_project_modified()
        self.assertEqual(self.window.isWindowModified(), dirty)
        self.assertFalse(self.window.workspace.sheets)
        self.assertFalse(self.window.graph_documents.keys())

    def test_existing_source_previews_retained_values_and_validates_reused_schema(self):
        paths = self.files(['x,y\n1,2\n'])
        book = self.window.import_xy_files(paths)
        paths[0].write_text('x,y,z\n9,8,7\n', encoding='utf-8')
        dialog = self.dialog(paths)
        self.assertEqual(dialog.tables[0].column_names, ['x','y'])
        np.testing.assert_array_equal(dialog.tables[0].data, [[1,2]])
        self.assertIn('retained', dialog.preview_info.text().lower())
        with self.assertRaises(ValueError):
            self.window.import_xy_files(paths, mappings=[(0,[2])], compare=True)
        self.assertEqual(len(self.window.workspace.workbooks), 1)
        self.assertFalse(self.window.workspace.comparisons)
        self.assertEqual(self.commit(dialog).workbook_id, book.workbook_id)

    def test_header_events_mapping_and_cell_copy_do_not_reset_selection(self):
        book = self.window.import_xy_files(self.files(['x,a,b,c\n1,2,3,4\n2,5,6,7\n']))
        page = self.window.open_workbook(book.workbook_id).sheet_pages[book.sheet_ids[0]]
        self.window.show()
        self.window.graph_documents.tabbed_view()
        APP.processEvents()
        header = page.table.horizontalHeader()
        def click(column, modifiers=Qt.NoModifier):
            QTest.mouseClick(header.viewport(), Qt.LeftButton, modifiers, QPoint(header.sectionViewportPosition(column)+20, 10))
        click(1)
        click(3, Qt.ControlModifier)
        self.assertEqual(self.window.workspace.sheets[page.sheet_id].y_columns, [1,3])
        self.assertEqual([i.column() for i in page.table.selectionModel().selectedColumns()], [1,3])
        click(1)
        click(3, Qt.ShiftModifier)
        self.assertEqual(self.window.workspace.sheets[page.sheet_id].y_columns, [1,2,3])
        selection = QItemSelection(page.model.index(0,0), page.model.index(0,2))
        page.table.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)
        page.copy_selection()
        self.assertEqual(APP.clipboard().text(), '1\t2\t3')
        self.assertEqual(self.window.workspace.sheets[page.sheet_id].y_columns, [1,2,3])
        self.assertIn('A(X)', page.model.headerData(0, Qt.Horizontal))
        self.assertIn('B(Y)', page.model.headerData(1, Qt.Horizontal))
        page.table.setCurrentIndex(page.model.index(0,2))
        page.table.setFocus()
        QTest.keyClick(page.table, Qt.Key_Space, Qt.ControlModifier)
        self.assertEqual(self.window.workspace.sheets[page.sheet_id].y_columns, [2])
        point = QPoint(header.sectionViewportPosition(2)+20, 10)
        def choose_x():
            menu = APP.activePopupWidget()
            self.assertIsInstance(menu, QMenu)
            QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(menu.actions()[0]).center())
        QTimer.singleShot(0, choose_x)
        APP.sendEvent(header.viewport(), QContextMenuEvent(QContextMenuEvent.Mouse, point, header.viewport().mapToGlobal(point)))
        self.assertEqual(self.window.workspace.sheets[page.sheet_id].x_column, 2)
        self.assertEqual(self.window.workspace.sheets[page.sheet_id].y_columns, [])
        self.assertFalse(page.new_comparison_btn.isEnabled())
        self.assertIn('C(X)', page.model.headerData(2, Qt.Horizontal))

    def test_add_preserves_workbook_selection_focus_and_closed_target(self):
        book = self.window.import_xy_files(self.files(['x,y,z\n' + '\n'.join(f'{i},{i*2},{i*3}' for i in range(150))]), mappings=[(0, [1])])
        workbook = self.window.open_workbook(book.workbook_id)
        page = workbook.sheet_pages[book.sheet_ids[0]]
        page.new_comparison_btn.click()
        comparison = next(iter(self.window.workspace.comparisons.values()))
        graph = self.window.open_xy_comparison(comparison.comparison_id)
        self.window.open_workbook(book.workbook_id)
        self.window.show()
        self.window.graph_documents.tabbed_view()
        APP.processEvents()
        page.table.selectColumn(1)
        page.table.verticalScrollBar().setValue(50)
        page.table.setFocus()
        APP.processEvents()
        graph.name_edit.setText('Pending name')
        graph.color_edit.setText('#123456')
        before = [(i.row(),i.column()) for i in page.table.selectionModel().selectedIndexes()]
        scroll = page.table.verticalScrollBar().value()
        # This focus/closed-target case deliberately creates duplicate copies.
        # Default additions now skip them; exercise the explicit repeat choice.
        self.window.workbook_commands.add_again_action.trigger()
        self.assertEqual(self.window.graph_documents._active_key, ('workbook',book.workbook_id,''))
        self.assertIs(APP.focusWidget(), page.table)
        self.assertEqual(page.table.verticalScrollBar().value(), scroll)
        self.assertEqual([(i.row(),i.column()) for i in page.table.selectionModel().selectedIndexes()], before)
        self.assertEqual(comparison.name, 'Pending name')
        self.assertEqual(next(iter(comparison.curves.values())).color, '#123456')
        self.assertEqual(len(graph.axes.lines), 2)
        self.window.graph_documents.close_document(('xy', comparison.comparison_id, ''))
        self.window.workbook_commands.add_again_action.trigger()
        self.assertIsNone(self.window.graph_documents.window_for(('xy', comparison.comparison_id, '')))
        self.assertEqual(len(comparison.curves), 3)
        self.assertFalse(self.errors)

    def test_invalid_pending_target_editor_blocks_append_before_mutation(self):
        book = self.window.import_xy_files(self.files(['x,y\n1,2\n']))
        page = self.window.open_workbook(book.workbook_id).sheet_pages[book.sheet_ids[0]]
        page.new_comparison_btn.click()
        comparison = next(iter(self.window.workspace.comparisons.values()))
        graph = self.window.open_xy_comparison(comparison.comparison_id)
        self.window.open_workbook(book.workbook_id)
        graph.color_edit.setText('invalid')
        page.add_comparison_btn.click()
        self.assertEqual(len(comparison.curves), 1)
        self.assertEqual(graph.color_edit.text(), 'invalid')
        self.assertTrue(self.errors)
        self.assertEqual(self.window.graph_documents._active_key, ('workbook', book.workbook_id, ''))
        graph.color_edit.setText('#123456')
