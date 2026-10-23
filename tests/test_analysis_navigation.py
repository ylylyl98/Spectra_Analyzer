"""Observable on-demand Analysis entry, source ownership and return navigation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from spectra_app.main_window import MainWindow
from spectra_app.workbook_state import Sheet, ensure_dataset_sheet, save_sheet_snapshot
from tests.settings_helpers import isolate_settings, wait_for_background_jobs


APP = QApplication.instance() or QApplication([])


class AnalysisNavigationTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.callback_errors = []
        trap = patch.object(sys, 'excepthook', lambda *args: self.callback_errors.append(args))
        trap.start()
        self.addCleanup(trap.stop)
        self.errors = []
        for method in ('critical', 'warning'):
            dialog = patch.object(QMessageBox, method, lambda _parent, title, message, *_args:
                (self.errors.append(f'{title}: {message}'), QMessageBox.Cancel)[1])
            dialog.start()
            self.addCleanup(dialog.stop)
        errors = patch.object(MainWindow, '_show_error', lambda _window, message: self.errors.append(message))
        errors.start()
        self.addCleanup(errors.stop)
        self.w = MainWindow()
        self.addCleanup(self.dispose)
        self.w.resize(1500, 920)
        self.w.show()
        APP.processEvents()

    def dispose(self):
        QTest.keyRelease(self.w, Qt.Key_Control)
        QTest.keyRelease(self.w, Qt.Key_Shift)
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.callback_errors, [])

    def load(self, name='sample'):
        path = self.folder / (name + '.dat')
        path.write_text('Energy(eV) 0 1 2 3\n' + '\n'.join(
            f'{1+i/20} {i*i+1} {2*i*i+3} {3*i*i+2} {4*i*i+4}' for i in range(31)))
        self.w._load_dat_file_direct(path)
        return self.w.workspace.active

    def require_actions(self):
        self.assertTrue(hasattr(self.w, 'analyze_action'), 'Analysis needs an explicit menu entry')
        self.assertTrue(hasattr(self.w, 'project_analyze_action'), 'Project needs targeted Analyze')

    def select(self, key):
        tree = self.w.data_navigator
        tree.clearSelection()
        for item in self.w._navigator_items():
            if item.data(0, Qt.UserRole) == key:
                tree.setCurrentItem(item)
                item.setSelected(True)
                tree.scrollToItem(item)
                APP.processEvents()
                return item
        self.fail(f'Missing Project item: {key}')

    def sheet(self, session, role='original'):
        return next(s for s in self.w.workspace.sheets.values()
                    if s.dataset_id == session.dataset_id and s.role == role)

    def test_startup_has_workspace_without_peer_tabs_and_disabled_empty_analysis(self):
        w = self.w
        self.assertFalse(w.plot_tabs.tabBar().isVisible(), 'Permanent Analysis/Workspace peer tabs must be hidden')
        self.require_actions()
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertTrue(w.graph_documents._empty.isVisible())
        self.assertFalse(w.analyze_action.isEnabled())
        self.assertFalse(w.project_analyze_action.isEnabled())
        w.analyze_action.trigger()
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        for key in (Qt.Key_Tab, Qt.Key_Backtab):
            QTest.keyClick(w.plot_tabs, key, Qt.ControlModifier)
            self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents,
                          'Hidden peer tabs must not remain a keyboard path into empty Analysis')

    def test_graph_button_routes_its_owner_and_menu_reuses_single_live_canvas(self):
        w = self.w
        a = self.load('alpha')
        page_a = w.open_graph_document(a.dataset_id)
        b = self.load('beta')
        w.open_graph_document(b.dataset_id)
        self.assertTrue(hasattr(page_a, 'analyze_btn'), 'Each graph command bar needs Analyze')
        canvas = w.workbench_preview
        w.dat_details_action.setChecked(False)
        page_a.analyze_btn.click()
        self.assertIs(w.workspace.active, a)
        self.assertEqual(w.plot_tabs.currentIndex(), 0)
        self.assertTrue(w.workbench_preview.isVisible())
        self.assertTrue(w.dat_details_dock.isVisible())
        self.assertIs(w._sidebar_tabs.currentWidget(), w._analysis_sidebar_scroll)
        self.assertFalse(w.graph_command_host.isVisible(), 'Analysis must not display a stale MDI title')
        self.assertIn('alpha.dat', w.analysis_context_label.text())
        self.assertTrue(w.analysis_back_btn.isVisible())
        w.analysis_back_btn.click()
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertIs(w.plot_page, page_a)
        self.assertTrue(page_a.control_scroll.isAncestorOf(w.color_scale_combo))
        self.require_actions()
        w.analyze_action.trigger()
        self.assertIs(w.workbench_preview, canvas)
        self.assertTrue(w._view_sidebar_scroll.isAncestorOf(w.color_scale_combo))
        self.assertGreaterEqual(w._sidebar_tabs.indexOf(w._analysis_sidebar_scroll), 0)

    def test_project_context_action_uses_selected_graph_instead_of_active_dataset(self):
        w = self.w
        a = self.load('alpha')
        w.open_graph_document(a.dataset_id)
        b = self.load('beta')
        w.open_graph_document(b.dataset_id)
        self.require_actions()
        item = self.select(('heatmap', (a.dataset_id, 'default')))
        self.assertIs(w.workspace.active, b)
        test = self
        class ContextMenu(QMenu):
            def exec(menu, *_args):
                action = next(action for action in menu.actions() if action.text() == 'Analyze')
                test.assertTrue(action.isEnabled())
                action.trigger()
        with patch('spectra_app.main_window.QMenu', ContextMenu):
            w._navigator_menu(w.data_navigator.visualItemRect(item).center())
        self.assertEqual(w.plot_tabs.currentIndex(), 0)
        self.assertIs(w.workspace.active, a)
        self.assertIn('alpha.dat', w.analysis_context_label.text())

    def test_project_and_active_sheet_entries_resolve_dat_and_return_to_source_workbook(self):
        w = self.w
        a = self.load('alpha')
        b = self.load('beta')
        ensure_dataset_sheet(w.workspace, a.dataset_id, 'processed')
        w._refresh_data_navigator()
        self.require_actions()
        for role in ('original', 'processed'):
            sheet = self.sheet(a, role)
            book = w._sheet_owner(sheet.sheet_id)
            self.select(('sheet', sheet.sheet_id))
            self.assertTrue(w.project_analyze_action.isEnabled())
            w.project_analyze_action.trigger()
            self.assertIs(w.workspace.active, a)
            self.assertIn('alpha.dat', w.analysis_context_label.text())
            w.analysis_back_btn.click()
            self.assertEqual(w.graph_documents._active_key, ('workbook', book.workbook_id, ''))
            page = w.graph_documents.page_for(w.graph_documents._active_key)
            self.assertEqual(page.tabs.currentWidget().sheet_id, sheet.sheet_id)
            w.analyze_action.trigger()
            self.assertEqual(w.plot_tabs.currentIndex(), 0)
            w.analysis_back_btn.click()
        self.assertIsNot(w.workspace.active, b)

    def test_workbook_requires_unambiguous_dat_or_its_explicit_current_data_sheet(self):
        w = self.w
        a = self.load('alpha')
        b = self.load('beta')
        self.require_actions()
        sheet_a, sheet_b = self.sheet(a), self.sheet(b)
        book_a, book_b = w._sheet_owner(sheet_a.sheet_id), w._sheet_owner(sheet_b.sheet_id)
        w.merge_workbooks([book_a.workbook_id, book_b.workbook_id], book_a.workbook_id)
        self.select(('workbook', book_a.workbook_id))
        self.assertFalse(w.project_analyze_action.isEnabled())
        w.project_analyze_action.trigger()
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertIs(w.workspace.active, b)
        w.open_workbook(book_a.workbook_id, sheet_a.sheet_id)
        self.select(('workbook', book_a.workbook_id))
        self.assertTrue(w.project_analyze_action.isEnabled())
        w.project_analyze_action.trigger()
        self.assertIs(w.workspace.active, a)
        self.assertIn('alpha.dat', w.analysis_context_label.text())

    def test_unsupported_and_ambiguous_contexts_never_fall_back_to_stale_dat(self):
        w = self.w
        session = self.load()
        self.require_actions()
        path = self.folder / 'ordinary.csv'
        path.write_text('x,y\n1,2\n2,4\n3,6\n')
        w.import_xy_files([path], mappings=[(0, [1])], compare=True)
        xy = w.graph_documents._active_key
        csv_sheet = next(s for s in w.workspace.sheets.values() if s.kind == 'xy')
        csv_book = w._sheet_owner(csv_sheet.sheet_id)
        for key in (('sheet', csv_sheet.sheet_id), ('workbook', csv_book.workbook_id)):
            self.select(key)
            self.assertFalse(w.project_analyze_action.isEnabled())
            w.project_analyze_action.trigger()
            self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
            self.assertIs(w.workspace.active, session)
        self.assertTrue(w.analyze_action.isEnabled(), 'Active XY graph supports curve analysis')
        w.open_workbook(csv_book.workbook_id)
        self.assertFalse(w.analyze_action.isEnabled())
        result = Sheet(name='Fit parameters', kind='dat', dataset_id=session.dataset_id, role='fit_parameters', y_columns=[4])
        w.workspace.sheets[result.sheet_id] = result
        csv_book.sheet_ids.append(result.sheet_id)
        w._refresh_data_navigator()
        self.select(('sheet', result.sheet_id))
        self.assertFalse(w.project_analyze_action.isEnabled())
        snapshot = save_sheet_snapshot(w.workspace, csv_sheet.sheet_id, 'Frozen CSV')
        w._refresh_data_navigator()
        self.select(('sheet', snapshot.sheet_id))
        self.assertFalse(w.project_analyze_action.isEnabled())
        first = self.select(('sheet', self.sheet(session).sheet_id))
        csv_item = self.select(('sheet', csv_sheet.sheet_id))
        first.setSelected(True)
        self.assertTrue(csv_item.isSelected())
        self.assertFalse(w.project_analyze_action.isEnabled())
        w.data_navigator.clearSelection()
        self.assertFalse(w.project_analyze_action.isEnabled())

    def test_header_reports_applied_input_and_full_batch_scope_for_raw_sparse_graph(self):
        w = self.w
        session = self.load('source &plain')
        page = w.open_graph_document()
        self.assertTrue(hasattr(page, 'analyze_btn'), 'Graph needs Analysis entry')
        graph = session.active_graph
        graph.source_mode = 'raw'
        graph.gate_indices = [0, 2]
        w._project_selected_graph(session)
        raw = session.base_cube.z.copy()
        page.analyze_btn.click()
        header = w.analysis_context_label
        self.assertEqual(header.textFormat(), Qt.PlainText)
        self.assertTrue(header.wordWrap())
        self.assertIn('Frozen Original', header.text())
        self.assertIn('/ Original / bound input', header.toolTip())
        self.assertIn('visible 2/4 Gates · analyzed 4', header.text())
        self.assertIn('All Gates analyzes all 4 bound Gates', header.toolTip())
        self.assertIn('2 of 4 Gates', header.toolTip())
        self.assertFalse(w._processing_content.isEnabled())
        version = session.active_graph.analysis.input_version
        w.workbench_input.setCurrentText('Processed')
        self.assertIn('Frozen Original', header.text(), 'Choosing a pending input does not bind it')
        self.assertEqual(session.active_graph.analysis.input_version, version)
        with patch.object(w, '_workbench_confirm_rebind', return_value='replace'):
            w.workbench_bind_btn.click()
        self.assertIn('Frozen Processed', header.text())
        self.assertIn('/ Processed / bound input', header.toolTip())
        self.assertIn('2 of 4 Gates', header.toolTip())
        w.plot_gate_index_spin.setValue(3)
        self.assertIn('Gate: 2', header.toolTip())
        self.assertIn('3/4', header.toolTip())
        self.assertIn('display', header.toolTip().lower())
        np.testing.assert_array_equal(session.base_cube.z, raw)
        self.assertEqual(graph.source_mode, 'raw')
        self.assertEqual(graph.gate_indices, [0, 2])

    def test_invalid_drafts_and_busy_operations_keep_navigation_owner(self):
        w = self.w
        session = self.load()
        page = w.open_graph_document()
        self.assertTrue(hasattr(page, 'analyze_btn'), 'Graph needs Analysis entry')
        page.width_spin.lineEdit().setText('-')
        page.analyze_btn.click()
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertEqual(page.width_spin.lineEdit().text(), '-')
        self.assertIs(w.workspace.active, session)
        page.width_spin.setValue(4.)
        with patch.object(w, '_project_busy', return_value=True):
            page.analyze_btn.click()
            self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        page.analyze_btn.click()
        w.workbench_xmin.lineEdit().setText('-')
        w.analysis_back_btn.click()
        self.assertEqual(w.plot_tabs.currentIndex(), 0)
        self.assertEqual(w.workbench_xmin.lineEdit().text(), '-')
        w.workbench_xmin.setValue(1.)
        w.analysis_back_btn.click()
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)

    def test_long_source_header_remains_readable_in_narrow_analysis_view(self):
        w = self.w
        name = 'WSe2_PL730nm_gate_sweep_reference_8T_3.6K_2026_10_12_extended_acquisition'
        self.load(name)
        w.open_graph_document()
        w.analyze_action.trigger()
        w.resize(1200, 820)
        APP.processEvents()
        header = w.analysis_context_label
        self.assertIn(name[:21], header.text())
        self.assertIn(name + '.dat', header.toolTip())
        bounds = header.fontMetrics().boundingRect(
            QRect(0, 0, header.width(), 10000), Qt.TextWordWrap, header.text())
        self.assertLessEqual(bounds.width(), header.width(), 'Every header line must fit without clipping')
        self.assertLessEqual(bounds.height(), header.height())
        self.assertTrue(w.analysis_back_btn.isVisible())

    def test_closed_return_view_stays_closed_and_save_reopen_retains_entry_and_graph_state(self):
        w = self.w
        session = self.load()
        page = w.open_graph_document()
        self.assertTrue(hasattr(page, 'analyze_btn'), 'Graph needs Analysis entry')
        session.active_graph.source_mode = 'raw'
        session.active_graph.gate_indices = [0, 2]
        w._project_selected_graph(session)
        w.plot_gate_index_spin.setValue(3)
        key = (session.dataset_id, session.active_graph_id)
        page.analyze_btn.click()
        analysis_key = (session.dataset_id, session.active_graph_id)
        self.assertIsNone(w.graph_documents.window_for(analysis_key))
        self.assertTrue(w.graph_documents.close_document(key))
        w.analysis_back_btn.click()
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertIsNone(w.graph_documents.window_for(key))
        self.assertTrue(w.graph_documents._empty.isVisible())
        w.open_graph_document(*key)
        w.analyze_action.trigger()
        saved = w.save_project(self.folder / 'navigation.spectra')
        expected = deepcopy(session.active_graph)
        self.assertTrue(w.open_project(saved))
        restored = w.workspace.datasets[session.dataset_id].active_graph
        self.assertEqual(restored.graph_id, expected.graph_id)
        self.assertEqual(restored.analysis.input_version, expected.analysis.input_version)
        self.assertEqual(restored.analysis.science.view.gate, expected.analysis.science.view.gate)
        if w.plot_tabs.currentWidget() is w.graph_documents:
            w.analyze_action.trigger()
        self.assertEqual(w.plot_tabs.currentIndex(), 0)
        self.assertIn('sample.dat', w.analysis_context_label.text())
        self.assertIn('visible 2/4 Gates · analyzed 4', w.analysis_context_label.text())
        self.assertIn('All Gates analyzes all 4 bound Gates', w.analysis_context_label.toolTip())
        w.analysis_back_btn.click()
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertEqual(w.graph_documents._active_key, key)


if __name__ == '__main__':
    unittest.main()
