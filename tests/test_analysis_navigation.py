"""Observable on-demand Analysis entry, source ownership and return navigation."""
from tests import analysis_header_quick_helpers as ah
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PySide6.QtTest import QTest

from tests.quick_migration_case import QuickMigrationCase
from spectra_app.workbook_state import Sheet, ensure_dataset_sheet, save_sheet_snapshot
from tests import analysis_quick_helpers as aq
from tests.settings_helpers import wait_for_background_jobs
from spectra_app.project_model import row_key
from tests.formal_quick_helpers import item, pump, click_item


APP = QCoreApplication


class AnalysisNavigationTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.callback_errors = []
        trap = patch.object(sys, 'excepthook', lambda *args: self.callback_errors.append(args))
        trap.start()
        self.addCleanup(trap.stop)
        self.errors = []
        self.w = self.controller
        self.w._show_error = self.errors.append
        self.addCleanup(self.dispose)
        self.w.root_window.resize(1500, 920)
        self.w.root_window.show()
        APP.processEvents()

    def dispose(self):
        QTest.keyRelease(self.w.root_window, Qt.Key_Control)
        QTest.keyRelease(self.w.root_window, Qt.Key_Shift)
        wait_for_background_jobs(self.w)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.quick_errors, [])

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
        self.assertIsNotNone(self.w.project.row_for_key(key))
        self.w.project.select_key(key)
        APP.processEvents()
        return self.w.project.row_for_key(key)

    def sheet(self, session, role='original'):
        return next(s for s in self.w.workspace.sheets.values()
                    if s.dataset_id == session.dataset_id and s.role == role)

    def test_startup_has_workspace_without_peer_tabs_and_disabled_empty_analysis(self):
        w = self.w
        self.assertFalse(hasattr(w, 'plot_tabs'), 'Outer views must use the production Quick shell')
        self.require_actions()
        self.assertEqual(w.outerView, 'workspace')
        self.assertEqual(w.graph_documents.registry.open_keys(), ())
        self.assertTrue(item(w, 'workspaceDocuments').isVisible())
        self.assertFalse(w.analyze_action.isEnabled())
        self.assertFalse(w.project_analyze_action.isEnabled())
        w.analyze_action.trigger()
        self.assertEqual(w.outerView, 'workspace')
        for key in (Qt.Key_Tab, Qt.Key_Backtab):
            QTest.keyClick(w.root_window, key, Qt.ControlModifier)
            self.assertEqual(w.outerView, 'workspace',
                          'Hidden peer tabs must not remain a keyboard path into empty Analysis')

    def test_graph_button_routes_its_owner_and_menu_reuses_single_live_canvas(self):
        w = self.w
        a = self.load('alpha')
        page_a = w.open_graph_document(a.dataset_id)
        b = self.load('beta')
        w.open_graph_document(b.dataset_id)
        self.assertIn('analyze', page_a.formal.state['commands'], 'Each graph command bar needs Analyze')
        canvas = w.workbench_preview
        w.dat_details_action.setChecked(False)
        pump(220)
        click_item(w, 'documentTab_' + '_'.join((a.dataset_id, 'default')))
        self.assertIs(w.workspace.active, a)
        self.assertIs(w.plot_page, page_a)
        click_item(w, 'formal_analyze')
        self.assertIs(w.workspace.active, a)
        self.assertEqual(w.outerView, 'analysis')
        self.assertTrue(item(w, 'applicationWorkbenchPreview').isVisible())
        self.assertTrue(w.propertiesVisible)
        self.assertTrue(item(w, 'applicationProperties').isVisible())
        self.assertEqual(w.taskIndex, 1)
        self.assertTrue(item(w, 'analysisWorkbench').isVisible())
        self.assertTrue(item(w, 'analysisCommands').isVisible(), 'Analysis commands must own the context band')
        self.assertIn('alpha.dat', ah.context(w))
        self.assertTrue(ah.header(w, 'analysisBack').isVisible())
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'workspace')
        self.assertIs(w.plot_page, page_a)
        self.assertTrue(w.request_task(2))
        pump()
        self.assertTrue(item(w, 'sharedDatProperties').isVisible())
        self.require_actions()
        w.analyze_action.trigger()
        self.assertIs(w.workbench_preview, canvas)
        self.assertTrue(item(w, 'applicationProperties').isVisible())
        self.assertEqual(w.taskIndex, 1)
        self.assertTrue(item(w, 'analysisWorkbench').isVisible())

    def test_project_context_action_uses_selected_graph_instead_of_active_dataset(self):
        w = self.w
        a = self.load('alpha')
        w.open_graph_document(a.dataset_id)
        b = self.load('beta')
        w.open_graph_document(b.dataset_id)
        self.require_actions()
        item = self.select(('heatmap', (a.dataset_id, 'default')))
        self.assertIs(w.workspace.active, b)
        w.project.prepareContext(row_key(item.key))
        action = next(action for action in w.project.contextActions if action.key == 'analyze')
        self.assertTrue(action.enabled)
        action.trigger()
        self.assertEqual(w.outerView, 'analysis')
        self.assertIs(w.workspace.active, a)
        self.assertIn('alpha.dat', ah.context(w))

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
            self.assertIn('alpha.dat', ah.context(w))
            ah.button(w, 'analysisBack')
            self.assertEqual(w.graph_documents.registry.active_key, ('workbook', book.workbook_id, ''))
            page = w.graph_documents.document_for(w.graph_documents.registry.active_key)
            self.assertEqual(page.current_sheet.sheet_id, sheet.sheet_id)
            w.analyze_action.trigger()
            self.assertEqual(w.outerView, 'analysis')
            ah.button(w, 'analysisBack')
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
        self.assertEqual(w.outerView, 'workspace')
        self.assertIs(w.workspace.active, b)
        w.open_workbook(book_a.workbook_id, sheet_a.sheet_id)
        self.select(('workbook', book_a.workbook_id))
        self.assertTrue(w.project_analyze_action.isEnabled())
        w.project_analyze_action.trigger()
        self.assertIs(w.workspace.active, a)
        self.assertIn('alpha.dat', ah.context(w))

    def test_unsupported_and_ambiguous_contexts_never_fall_back_to_stale_dat(self):
        w = self.w
        session = self.load()
        self.require_actions()
        path = self.folder / 'ordinary.csv'
        path.write_text('x,y\n1,2\n2,4\n3,6\n')
        w.import_xy_files([path], mappings=[(0, [1])], compare=True)
        xy = w.graph_documents.registry.active_key
        csv_sheet = next(s for s in w.workspace.sheets.values() if s.kind == 'xy')
        csv_book = w._sheet_owner(csv_sheet.sheet_id)
        for key in (('sheet', csv_sheet.sheet_id), ('workbook', csv_book.workbook_id)):
            self.select(key)
            self.assertFalse(w.project_analyze_action.isEnabled())
            w.project_analyze_action.trigger()
            self.assertEqual(w.outerView, 'workspace')
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
        w.project.select_key(first.key, Qt.ControlModifier)
        self.assertIn(csv_item.key, w.project.selected_keys())
        self.assertFalse(w.project_analyze_action.isEnabled())
        w.project.clearSelection()
        self.assertFalse(w.project_analyze_action.isEnabled())

    def test_header_reports_applied_input_and_full_batch_scope_for_raw_sparse_graph(self):
        w = self.w
        session = self.load('source &plain')
        page = w.open_graph_document()
        self.assertTrue(page.command_enabled('analyze'), 'Graph needs Analysis entry')
        graph = session.active_graph
        graph.source_mode = 'raw'
        graph.gate_indices = [0, 2]
        w._project_selected_graph(session)
        raw = session.base_cube.z.copy()
        page.formal.command('analyze')
        header = ah.header(w, 'contextSummary')
        self.assertEqual(ah.enum_value(header, 'textFormat'), Qt.PlainText.value)
        self.assertLessEqual(header.property('contentWidth'), header.width())
        self.assertLessEqual(header.property('contentHeight'), header.height())
        self.assertIn('Frozen Original', ah.context(w))
        self.assertIn('/ Original / bound input', ah.details(w))
        self.assertIn('2/4 Gates · analyzed 4', ah.context(w))
        self.assertIn('All Gates analyzes all 4 bound Gates', ah.details(w))
        self.assertIn('2 of 4 Gates', ah.details(w))
        self.assertIsNotNone(w._workbench_document())
        self.assertFalse(w.app_bridge.processing.enabled)
        version = session.active_graph.analysis.input_version
        aq.set_choice(w, 'inputMode', 'processed')
        self.assertIn('Frozen Original', ah.context(w), 'Choosing a pending input does not bind it')
        self.assertEqual(session.active_graph.analysis.input_version, version)
        with patch.object(w, '_workbench_confirm_rebind', return_value='replace'):
            aq.command(w, 'rebind')
        self.assertIn('Frozen Processed', ah.context(w))
        self.assertIn('/ Processed / bound input', ah.details(w))
        self.assertIn('2 of 4 Gates', ah.details(w))
        ah.edit(w, 'index', '3', commit=True)
        self.assertIn('Gate: 2', ah.details(w))
        self.assertIn('3/4', ah.details(w))
        self.assertIn('display', ah.details(w).lower())
        np.testing.assert_array_equal(session.base_cube.z, raw)
        self.assertEqual(graph.source_mode, 'raw')
        self.assertEqual(graph.gate_indices, [0, 2])

    def test_invalid_drafts_and_busy_operations_keep_navigation_owner(self):
        w = self.w
        session = self.load()
        page = w.open_graph_document()
        self.assertTrue(page.command_enabled('analyze'), 'Graph needs Analysis entry')
        page.formal.setDraft('width_in', '-')
        page.formal.command('analyze')
        self.assertEqual(w.outerView, 'workspace')
        self.assertEqual(page.formal.state['fields']['width_in'], '-')
        self.assertIs(w.workspace.active, session)
        page.formal.setValue('width_in', 4.)
        with patch.object(w, '_project_busy', return_value=True):
            page.formal.command('analyze')
            self.assertEqual(w.outerView, 'workspace')
        page.formal.command('analyze')
        w.root_window.show()
        aq.edit_number(w, 'xmin', '-')
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'analysis')
        self.assertEqual(aq.control(w, 'xmin').property('text'), '-')
        aq.edit_number(w, 'xmin', '1.', commit=True)
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'workspace')

    def test_long_source_header_remains_readable_in_narrow_analysis_view(self):
        w = self.w
        name = 'WSe2_PL730nm_gate_sweep_reference_8T_3.6K_2026_10_12_extended_acquisition'
        self.load(name)
        w.open_graph_document()
        w.analyze_action.trigger()
        w.root_window.resize(1200, 820)
        APP.processEvents()
        header = ah.header(w, 'contextSummary')
        self.assertIn(name[:21], ah.context(w))
        self.assertIn(name + '.dat', ah.details(w))
        title = ah.header(w, 'contextTitle')
        self.assertLessEqual(title.property('contentHeight'), title.height())
        self.assertEqual(ah.enum_value(title, 'textFormat'), Qt.PlainText.value)
        self.assertEqual(ah.enum_value(title, 'elide'), Qt.ElideMiddle.value)
        self.assertTrue(ah.header(w, 'analysisBack').isVisible())

    def test_closed_return_view_stays_closed_and_save_reopen_retains_entry_and_graph_state(self):
        w = self.w
        session = self.load()
        page = w.open_graph_document()
        self.assertTrue(page.command_enabled('analyze'), 'Graph needs Analysis entry')
        session.active_graph.source_mode = 'raw'
        session.active_graph.gate_indices = [0, 2]
        w._project_selected_graph(session)
        ah.edit(w, 'index', '3', commit=True)
        key = (session.dataset_id, session.active_graph_id)
        w.analyze_action.trigger()
        analysis_key = (session.dataset_id, session.active_graph_id)
        self.assertNotIn(analysis_key, w.graph_documents.registry.open_keys())
        self.assertTrue(w.graph_documents.close_document(key))
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'workspace')
        self.assertNotIn(key, w.graph_documents.registry.open_keys())
        self.assertEqual(w.graph_documents.registry.open_keys(), ())
        self.assertTrue(item(w, 'workspaceDocuments').isVisible())
        w.open_graph_document(*key)
        w.analyze_action.trigger()
        saved = w.save_project(self.folder / 'navigation.spectra')
        expected = deepcopy(session.active_graph)
        self.assertTrue(w.open_project(saved))
        restored = w.workspace.datasets[session.dataset_id].active_graph
        self.assertEqual(restored.graph_id, expected.graph_id)
        self.assertEqual(restored.analysis.input_version, expected.analysis.input_version)
        self.assertEqual(restored.analysis.science.view.gate, expected.analysis.science.view.gate)
        if w.outerView == 'workspace':
            w.analyze_action.trigger()
        self.assertEqual(w.outerView, 'analysis')
        self.assertIn('sample.dat', ah.context(w))
        self.assertIn('2/4 Gates · analyzed 4', ah.context(w))
        self.assertIn('All Gates analyzes all 4 bound Gates', ah.details(w))
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'workspace')
        self.assertEqual(w.graph_documents.registry.active_key, key)


if __name__ == '__main__':
    unittest.main()
