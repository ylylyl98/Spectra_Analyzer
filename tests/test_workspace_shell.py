"""Shell integration: navigation and presentation must not change editor owners."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import sys
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])


class WorkspaceShellTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.callback_errors = []
        capture = patch.object(sys, 'excepthook', lambda *error: self.callback_errors.append(error))
        capture.start()
        self.addCleanup(capture.stop)
        self.w = MainWindow()
        self.errors = []
        self.w._show_error = self.errors.append
        self.w.resize(1500, 920)
        self.w.show()
        APP.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        QTest.keyRelease(self.w, Qt.Key_Control)
        QTest.keyRelease(self.w, Qt.Key_Shift)
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertEqual(self.callback_errors, [])

    def import_three(self):
        texts = ('signal,time\n10,1\n20,2\n30,3\n',
                 'unused,time,signal\n99,4,40\n98,8,80\n',
                 'signal,unused,time\n5,0,0.5\n15,0,1.5\n25,0,2.5\n')
        paths = []
        for i, text in enumerate(texts):
            path = self.folder / f'{i}.csv'
            path.write_text(text, encoding='utf-8')
            paths.append(path)
        self.w.import_xy_files(paths, mappings=[(1, [0]), (1, [2]), (2, [0])], compare=True)
        return paths, list(self.w.workspace.workbooks.values()), self.w.graph_documents.page_for(self.w.graph_documents._active_key)

    def load_dat(self):
        path = self.folder / 'spectrum.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        self.w._load_dat_file_direct(path)
        return self.w.workspace.active

    def test_fresh_window_starts_in_empty_workspace(self):
        w = self.w
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertFalse(w.plot_tabs.tabBar().isVisible())
        self.assertFalse(w.analyze_action.isEnabled())
        self.assertTrue(w.data_navigator.isVisible())
        self.assertTrue(w.graph_documents._empty.isVisible())
        self.assertEqual(w.graph_documents.subWindowList(), [])
        self.assertEqual(w.graph_documents.keys(), ())
        self.assertIsNone(w.graph_documents._active_key)
        self.assertEqual(w.workspace.datasets, {})
        self.assertEqual(w.workspace.workbooks, {})
        self.assertEqual(w.workspace.comparisons, {})
        self.assertTrue(w.dat_details_dock.isVisible())
        self.assertTrue(w.dat_details_action.isEnabled())
        self.assertTrue(w.graph_command_host.isVisible())
        self.assertIn('No active document', w._context_properties.text())
        self.assertFalse(w.isWindowModified())

    def test_startup_workspace_allows_deliberate_analysis_switch(self):
        w = self.w
        session = self.load_dat()
        page = w.open_graph_document(session.dataset_id)
        w.analyze_action.trigger()
        APP.processEvents()
        self.assertEqual(w.plot_tabs.currentIndex(), 0)
        self.assertTrue(w.dat_details_dock.isVisible())
        self.assertTrue(w.dat_details_action.isEnabled())
        self.assertGreaterEqual(w._sidebar_tabs.indexOf(w._analysis_sidebar_scroll), 0)
        self.assertGreaterEqual(w._sidebar_tabs.indexOf(w._view_sidebar_scroll), 0)
        self.assertEqual(w._sidebar_tabs.indexOf(w.plot_page.control_scroll), -1)
        w.hide()
        w.show()
        APP.processEvents()
        self.assertEqual(w.plot_tabs.currentIndex(), 0)
        w.analysis_back_btn.click()
        APP.processEvents()
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertTrue(w.dat_details_dock.isVisible())
        self.assertIs(w.plot_page, page)
        self.assertTrue(w.graph_documents.window_for((session.dataset_id, session.active_graph_id)).isVisible())

    def test_project_tree_stays_visible_across_document_kinds_and_restores_width(self):
        w = self.w
        self.assertTrue(w.data_navigator.isVisible(), 'Project navigation must be available immediately')
        _, books, xy = self.import_three()
        for book in books[:2]:
            w.open_workbook(book.workbook_id)
            APP.processEvents()
            self.assertTrue(w.data_navigator.isVisible())
        w.open_xy_comparison(xy.comparison_id)
        self.assertTrue(w.data_navigator.isVisible())
        session = self.load_dat()
        w.open_graph_document(session.dataset_id)
        self.assertTrue(w.data_navigator.isVisible())
        w.plot_tabs.setCurrentIndex(0)
        self.assertTrue(w.data_navigator.isVisible())
        self.assertEqual(w.plot_tabs.tabText(1), 'Workspace')
        w._main_splitter.setSizes([270, 1000])
        width = w._main_splitter.sizes()[0]
        w.project_panel_action.trigger()
        self.assertFalse(w.data_navigator.isVisible())
        w.project_panel_action.trigger()
        APP.processEvents()
        self.assertTrue(w.data_navigator.isVisible())
        self.assertAlmostEqual(w._main_splitter.sizes()[0], width, delta=3)

    def test_document_activation_keeps_workspace_and_properties_geometry(self):
        w = self.w
        _, books, xy = self.import_three()
        session = self.load_dat()
        heatmap = w.open_graph_document(session.dataset_id)
        workbook = w.open_workbook(books[0].workbook_id)
        w.open_xy_comparison(xy.comparison_id)
        APP.processEvents()
        viewport = w.graph_documents.viewport().geometry()
        project_width = w._main_splitter.sizes()[0]
        rectangles = {key: w.graph_documents._document_rectangle(w.graph_documents.window_for(key))
                      for key in w.graph_documents.keys() if w.graph_documents.window_for(key)}
        for activate, content in ((lambda: w.open_graph_document(session.dataset_id), w._sidebar_tabs.parentWidget()),
                                  (lambda: w.open_workbook(books[0].workbook_id), None),
                                  (lambda: w.open_xy_comparison(xy.comparison_id), xy.inspector)):
            activate()
            APP.processEvents()
            self.assertTrue(w.dat_details_dock.isVisible())
            self.assertTrue(w.graph_command_host.isVisible())
            self.assertEqual(w.graph_documents.viewport().geometry(), viewport)
            self.assertEqual(w._main_splitter.sizes()[0], project_width)
            for key, rect in rectangles.items():
                self.assertEqual(w.graph_documents._document_rectangle(w.graph_documents.window_for(key)), rect)
            if content is not None:
                self.assertTrue(w.dat_details_dock.isAncestorOf(content))
        self.assertFalse(w.signal_title_combo.isVisible())
        self.assertTrue(xy.inspector.isVisible())
        w.dat_details_action.setChecked(False)
        APP.processEvents()
        hidden_viewport = w.graph_documents.viewport().geometry()
        for activate in (lambda: w.open_workbook(books[0].workbook_id),
                         lambda: w.open_graph_document(session.dataset_id),
                         lambda: w.open_xy_comparison(xy.comparison_id)):
            activate()
            APP.processEvents()
            self.assertFalse(w.dat_details_dock.isVisible())
            self.assertTrue(w.dat_details_action.isEnabled())
            self.assertEqual(w.graph_documents.viewport().geometry(), hidden_viewport)

    def test_properties_preserve_user_intent_and_heatmap_tree_route(self):
        w = self.w
        self.assertTrue(hasattr(w, 'dat_details_dock'), 'DAT controls need an independent contextual dock')
        session = self.load_dat()
        page = w.open_graph_document()
        self.assertTrue(w.dat_details_dock.isVisible())
        w.dat_details_action.trigger()
        self.assertFalse(w.dat_details_dock.isVisible())
        _, books, xy = self.import_three()
        self.assertFalse(w.dat_details_dock.isVisible())
        self.assertTrue(w.dat_details_action.isEnabled())
        self.assertTrue(w.graph_command_host.isVisible())
        self.assertFalse(w.add_gate_comparison_btn.isEnabled())
        popup = w.createPopupMenu()
        try:
            details = next(action for action in popup.actions() if action.text() == 'Properties')
            self.assertTrue(details.isEnabled())
            details.trigger()
            self.assertTrue(w.dat_details_dock.isVisible())
            details.trigger()
            self.assertFalse(w.dat_details_dock.isVisible())
        finally:
            popup.deleteLater()
        heatmap = next(w.data_navigator.topLevelItem(i) for i in range(w.data_navigator.topLevelItemCount())
                       if w.data_navigator.topLevelItem(i).data(0, Qt.UserRole) == ('heatmap', (session.dataset_id, 'default')))
        w.data_navigator.setCurrentItem(heatmap)
        w._open_navigator_item()
        self.assertIs(w.plot_page, page)
        self.assertFalse(w.dat_details_dock.isVisible())
        w.dat_details_action.trigger()
        self.assertTrue(w.dat_details_dock.isVisible())
        w.open_workbook(books[0].workbook_id)
        self.assertTrue(w.dat_details_dock.isVisible())
        w.open_graph_document(session.dataset_id)
        self.assertTrue(w.dat_details_dock.isVisible())
        w.dat_details_dock.close()
        w.open_xy_comparison(xy.comparison_id)
        w.open_graph_document(session.dataset_id)
        self.assertFalse(w.dat_details_dock.isVisible(), 'Closing the dock records explicit DAT visibility intent')
        self.assertEqual(self.errors, [])

    def test_project_restore_reuses_properties_host_and_releases_old_inspectors(self):
        from shiboken6 import isValid
        w = self.w
        _, books, xy = self.import_three()
        session = self.load_dat()
        w.open_graph_document(session.dataset_id)
        w.open_xy_comparison(xy.comparison_id)
        old_inspector = xy.inspector
        saved = w.save_project(self.folder / 'stable-shell.spectra')
        self.assertTrue(w.open_project(saved))
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertFalse(isValid(old_inspector))
        viewport = w.graph_documents.viewport().geometry()
        w.open_workbook(books[0].workbook_id)
        APP.processEvents()
        self.assertEqual(w.graph_documents.viewport().geometry(), viewport)
        w.open_graph_document(session.dataset_id)
        APP.processEvents()
        self.assertEqual(w.graph_documents.viewport().geometry(), viewport)
        self.assertEqual(self.errors, [])

    def test_inspector_toggle_keeps_selection_drafts_and_guarded_owner(self):
        w = self.w
        _, books, page = self.import_three()
        self.assertTrue(hasattr(page, 'inspector_toggle_btn'), 'Comparison inspector needs a page-owned toggle')
        page.curve_list.setCurrentRow(1)
        page.curve_list.item(2).setSelected(True)
        selected = page._selected_ids()
        color = page._selected().color
        page.color_edit.setText('invalid')
        page.label_edit.setText('Unfinished legend')
        page.color_edit.setFocus()
        QTest.mouseClick(page.inspector_toggle_btn, Qt.LeftButton)
        self.assertFalse(page.inspector.isVisible())
        self.assertEqual(page._selected().color, color)
        self.assertEqual(page._selected_ids(), selected)
        self.assertEqual(page.color_edit.text(), 'invalid')
        self.assertEqual(page.label_edit.text(), 'Unfinished legend')
        active = w.graph_documents._active_key
        with self.assertRaisesRegex(ValueError, 'six-digit'):
            w.open_workbook(books[0].workbook_id)
        w.plot_tabs.setCurrentIndex(0)
        self.assertEqual(w.plot_tabs.currentIndex(), 1)
        self.assertEqual(w.graph_documents._active_key, active)
        self.assertFalse(w.graph_documents.close_document())
        page.inspector_toggle_btn.click()
        self.assertTrue(page.inspector.isVisible())
        self.assertEqual(page.color_edit.text(), 'invalid')
        self.assertEqual(page._selected_ids(), selected)
        page.color_edit.setText(color)
        page.finish_editor()

    def test_hidden_heatmap_draft_rejects_analysis_and_document_changes(self):
        w = self.w
        self.load_dat()
        page = w.open_graph_document()
        width = page.width_spin.value()
        def click_view_details():
            QTest.mouseClick(w.menuBar(), Qt.LeftButton, Qt.NoModifier,
                            w.menuBar().actionGeometry(w._view_menu.menuAction()).center())
            APP.processEvents()
            self.assertTrue(w._view_menu.isVisible())
            self.assertEqual(page.width_spin.lineEdit().text(), '-')
            QTest.mouseClick(w._view_menu, Qt.LeftButton, Qt.NoModifier,
                            w._view_menu.actionGeometry(w.dat_details_action).center())
        def click_dock_menu():
            menu = w.createPopupMenu()
            try:
                menu.popup(w.mapToGlobal(w.rect().center()))
                APP.processEvents()
                self.assertEqual(page.width_spin.lineEdit().text(), '-')
                QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier,
                                menu.actionGeometry(w.dat_details_action).center())
            finally:
                menu.hide()
                menu.deleteLater()
        for hide in (w.dat_details_action.trigger, w.dat_details_dock.close, click_view_details, click_dock_menu):
            w.dat_details_action.setChecked(True)
            w._sidebar_tabs.setCurrentWidget(page.control_scroll)
            page.control_scroll.ensureWidgetVisible(page.width_spin)
            page.width_spin.setFocus()
            APP.processEvents()
            page._loading = True
            try:
                page.width_spin.lineEdit().setText('-')
            finally:
                page._loading = False
            hide()
            APP.processEvents()
            self.assertEqual(page.width_spin.lineEdit().text(), '-')
            self.assertEqual(page.width_spin.value(), width)
            self.assertTrue(page.has_pending_project_edits())
            self.assertFalse(w.dat_details_action.isChecked())
            w.dat_details_action.setChecked(True)
            APP.processEvents()
            self.assertEqual(page.width_spin.lineEdit().text(), '-')
            self.assertEqual(page.width_spin.value(), width)
            w.dat_details_action.setChecked(False)
        w.plot_tabs.setCurrentIndex(0)
        self.assertEqual(w.plot_tabs.currentIndex(), 1, 'Analysis must not strand a hidden invalid heatmap editor')
        self.assertFalse(w.dat_details_dock.isVisible())
        self.assertEqual(page.width_spin.lineEdit().text(), '-')
        with self.assertRaises(ValueError):
            w.open_workbook(next(iter(w.workspace.workbooks)))
        self.assertFalse(w.graph_documents.close_document())
        page.width_spin.setValue(4.)
        w.plot_tabs.setCurrentIndex(0)
        self.assertEqual(w.plot_tabs.currentIndex(), 0)

    def test_inspector_mouse_toggle_does_not_commit_valid_focused_drafts(self):
        _, _, page = self.import_three()
        original_label = page._selected().label
        page.inspector.ensureWidgetVisible(page.label_edit)
        page.label_edit.setFocus()
        page.label_edit.setText('Unfinished valid label')
        QTest.mouseClick(page.inspector_toggle_btn, Qt.LeftButton)
        self.assertEqual(page._selected().label, original_label)
        self.assertEqual(page.label_edit.text(), 'Unfinished valid label')
        page.inspector_toggle_btn.click()
        self.assertEqual(page._selected().label, original_label)
        self.assertTrue(page.has_pending_project_edits())
        page.finish_editor()

    def test_heatmap_property_edit_reveals_hidden_dat_details(self):
        w = self.w
        self.load_dat()
        w.open_graph_document()
        w._render_plot_page()
        for kind, editor in (('title', w.plot_title_edit), ('horizontal-range', w.xmin_spin)):
            with self.subTest(kind=kind):
                w.dat_details_action.setChecked(False)
                w._show_plot_property(kind)
                APP.processEvents()
                self.assertTrue(w.dat_details_dock.isVisible())
                self.assertTrue(w.dat_details_action.isChecked())
                self.assertTrue(editor.isVisible())
                self.assertTrue(editor.hasFocus() or editor.isAncestorOf(APP.focusWidget()))

    def test_three_files_keep_source_navigation_and_shell_changes_do_not_dirty(self):
        w = self.w
        self.assertTrue(w.data_navigator.isVisible(), 'Project tree must remain reachable throughout import')
        paths, books, page = self.import_three()
        self.assertEqual(len(books), 3)
        self.assertEqual(len(page.comparison.curves), 3)
        for line, x, y in zip(page.axes.lines, ([1,2,3], [4,8], [.5,1.5,2.5]),
                              ([10,20,30], [40,80], [5,15,25])):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)
        page.curve_list.setCurrentRow(1)
        page.source_btn.click()
        book_page = w.graph_documents.page_for(w.graph_documents._active_key)
        self.assertEqual(book_page.tabs.currentWidget().sheet_id, books[1].sheet_ids[0])
        w.open_xy_comparison(page.comparison_id)
        w.graph_documents.tabbed_view()
        saved = w.save_project(self.folder / 'comparison.spectra')
        page.inspector_toggle_btn.click()
        w.project_panel_action.trigger()
        w.project_panel_action.trigger()
        w._refresh_project_modified()
        self.assertFalse(w.isWindowModified())
        for path in paths:
            path.unlink()
        self.assertTrue(w.open_project(saved))
        restored = w.graph_documents.page_for(w.graph_documents._active_key)
        self.assertEqual(restored.comparison_id, page.comparison_id)
        self.assertTrue(w.data_navigator.isVisible())
        self.assertFalse(w.dat_details_dock.isVisible())
        self.assertEqual(len(restored.axes.lines), 3)

    def test_unknown_tree_kinds_are_inert_and_heatmap_entries_follow_renames(self):
        w = self.w
        session = self.load_dat()
        w.open_graph_document(session.dataset_id)
        w.rename_graph('default', 'Overview')
        keys = [w.data_navigator.topLevelItem(i) for i in range(w.data_navigator.topLevelItemCount())]
        heatmaps = [item for item in keys if item.data(0, Qt.UserRole)[0] == 'heatmap']
        self.assertEqual(len(heatmaps), 1, 'Formal graphs must be reachable with DAT details closed')
        self.assertIn('Overview', heatmaps[0].text(0))
        unknown = QTreeWidgetItem(['Unknown'])
        unknown.setData(0, Qt.UserRole, ('unknown', 'not-a-comparison'))
        w.data_navigator.addTopLevelItem(unknown)
        w.data_navigator.setCurrentItem(unknown)
        active = w.graph_documents._active_key
        w._open_navigator_item()
        self.assertEqual(w.graph_documents._active_key, active)
        self.assertFalse(w._navigator_buttons['Open'].isEnabled())
        self.assertFalse(w._navigator_buttons['Rename'].isEnabled())
        self.assertFalse(w._navigator_buttons['Remove'].isEnabled())
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
