"""Ordinary Quick editors at persistence, worker and owner boundaries."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtQuick import QQuickItem

from spectra_app.analysis_document import analysis_owner
from spectra_app.common_prompts import PromptAction
from tests.quick_migration_case import QuickMigrationCase
from spectra_app.project_io import read_project
from tests import analysis_header_quick_helpers as ah
from tests import ordinary_analysis_quick_helpers as oa
from tests.background_helpers import wait_jobs
from tests.formal_quick_helpers import item, pump
from tests.scientific_fixtures import workflow_fit_result as _fit_result

APP = QCoreApplication


class OrdinaryAnalysisIntegrationTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.w = self.controller
        self.w.root_window.resize(1320, 850)
        self.errors = []
        self.w._show_error = self.errors.append
        self.release = threading.Event()
        self.first = self.load('first')
        self.second = self.load('second')
        self.w.activate_dataset(self.first.dataset_id)
        self.assertTrue(self.w.request_outer_view('analysis'))
        self.addCleanup(self.dispose)

    def load(self, name):
        path = self.folder / (name + '.dat')
        x = np.linspace(1., 4., 81)
        y = 1. + 3. * np.exp(-((x-2.5)/.15)**2)
        path.write_text('Energy(eV) 0 10 20\n' + '\n'.join(
            f'{a:.12g} {b:.12g} {b:.12g} {b:.12g}' for a, b in zip(x, y)), encoding='utf-8')
        self.w._load_dat_file_direct(path)
        return self.w.workspace.active

    def dispose(self):
        self.release.set()
        wait_jobs(self.w)
        self.assertEqual(self.quick_errors, [])

    def test_invalid_quick_draft_blocks_save_and_switch_without_partial_sibling_commit(self):
        w = self.w
        saved = w.save_project(self.folder / 'valid.spectra')
        saved_bytes, digest = saved.read_bytes(), w._project_saved_digest
        original = deepcopy(self.first.analysis_controls)
        editor = oa.edit(w, 'fit_xmin', '-')
        # Multiple raw candidates coexist without committing one as focus moves.
        oa.draft(w, 'prominence', '0.127')
        with self.assertRaises(ValueError):
            w.save_project(self.folder / 'invalid.spectra')
        self.assertEqual(saved.read_bytes(), saved_bytes)
        self.assertEqual(w._project_saved_digest, digest)
        self.assertFalse((self.folder / 'invalid.spectra').exists())
        self.assertEqual(self.first.analysis_controls, original)
        self.assertEqual(oa.text(w, 'prominence'), '0.127')
        self.assertEqual(editor.property('text'), '-')
        with self.assertRaises(ValueError):
            w.activate_dataset(self.second.dataset_id)
        self.assertIs(w.workspace.active, self.first)
        self.assertEqual(self.first.analysis_controls, original)
        self.assertEqual(editor.property('text'), '-')
        self.assertTrue(editor.hasActiveFocus())
        oa.edit(w, 'fit_xmin', '1.25', commit=True)
        self.assertEqual(self.first.analysis_controls.prominence, .127)
        self.assertEqual(self.first.analysis_controls.fit_xmin, 1.25)
        w.save_project(saved)
        restored = read_project(saved)
        self.assertEqual(restored.datasets[self.first.dataset_id].analysis_controls.prominence, .127)
        self.assertEqual(restored.datasets[self.first.dataset_id].analysis_controls.fit_xmin, 1.25)
        stale = w.ordinary_analysis.ownerToken
        w.activate_dataset(self.second.dataset_id)
        self.assertFalse(w.ordinary_analysis.setDraft('prominence', '0.9', stale))
        self.assertFalse(w.ordinary_analysis.commit(stale))
        self.assertEqual(self.first.analysis_controls.prominence, .127)
        self.assertEqual(self.errors, [])

    def test_startup_effective_defaults_and_preimport_quick_settings_are_captured(self):
        from tests.quick_application_helpers import application
        initial_host, _, initial_errors = self.enterContext(application())
        initial = initial_host.controller
        self.addCleanup(lambda: self.assertEqual(initial_errors, []))
        self.assertEqual(tuple(oa.value(initial, field) for field in (
            'linewidth_min', 'linewidth_max', 'peak_move_limit', 'maxfev')),
            (10, 50, 10, 20000))
        self.assertTrue(initial.request_outer_view('analysis'))
        token = initial.ordinary_analysis.ownerToken
        editor = oa.edit(initial, 'maxfev', '54321')
        self.assertTrue(editor.isEnabled())
        self.assertEqual(oa.value(initial, 'maxfev'), 20000)
        initial._load_dat_file_direct(self.first.source_path)
        self.assertEqual(oa.value(initial, 'maxfev'), 54321)
        self.assertEqual(initial.workspace.active.analysis_controls.maxfev, 54321)
        self.assertFalse(initial.ordinary_analysis.setDraft('maxfev', '12345', token))

    def test_real_quick_edit_during_fit_preserves_captured_inputs_and_rejects_late_completion(self):
        w = self.w
        self.first.peak_indices = np.array([40])
        self.first.peak_gate_idx = 0
        oa.set_number(w, 'linewidth_min', 2)
        oa.set_number(w, 'linewidth_max', 8)
        oa.set_number(w, 'maxfev', 4321)
        started = threading.Event()
        captured = []
        def solver(*args, **kwargs):
            captured.append(dict(kwargs))
            started.set()
            self.release.wait(5)
            return _fit_result(2.5)
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            oa.click(w, 'fit')
            self.assertTrue(started.wait(2))
            self.assertTrue(w._background_jobs.busy)
            ah.edit(w, 'index', '2', commit=True)
            self.assertEqual(w.current_gate, 10.)
            before_key = w._analysis_job_key()
            editor = oa.edit(w, 'maxfev', '9876', commit=True)
            self.assertTrue(editor.isEnabled())
            self.assertEqual(oa.value(w, 'maxfev'), 9876)
            self.assertNotEqual(w._analysis_job_key(), before_key)
            self.assertEqual(captured[0]['maxfev'], 4321)
            self.assertAlmostEqual(captured[0]['linewidth_min'], 6/81)
            self.assertAlmostEqual(captured[0]['linewidth_max'], 24/81)
            self.release.set()
            wait_jobs(w)
        self.assertEqual(self.first.batch_fit_result_map, {})
        self.assertIsNone(self.first.fit_result)
        self.assertEqual(self.first.fit_export_rows, [])
        self.assertEqual(self.errors, [])

    def test_csv_quick_load_uses_nearest_gate_multiple_seeds_and_never_detects_as_fallback(self):
        w = self.w
        for header in ('gate,peak_energy\n', ''):
            path = self.folder / ('header.csv' if header else 'no-header.csv')
            path.write_text(header + '8,1.75\n8,2.75\nbad,ignored\nshort\n', encoding='utf-8')
            oa.set_value(w, 'csv_source', True)
            with patch('spectra_app.main_window.FilePicker.getOpenFileName', return_value=(str(path), 'CSV')):
                oa.click(w, 'loadCsv')
            self.assertTrue(oa.value(w, 'csv_source'))
            self.assertEqual(self.first.csv_seeds_map, {1: [1.75, 2.75]})
            self.assertEqual(self.first.csv_seeds_path, str(path))
            ah.edit(w, 'index', '2', commit=True)
            self.assertEqual(w._get_seeds_for_current_gate().tolist(), [1.75, 2.75])
            ah.edit(w, 'index', '1', commit=True)
            with patch('spectra_app.main_window.find_peak_indices', side_effect=AssertionError('CSV fallback')):
                w._on_fit()
            self.assertFalse(w._background_jobs.busy)
            self.assertTrue(self.errors)
            self.assertIn('CSV', self.errors.pop())
        for seeds in (None, {}, {0: [9.]}):
            self.first.csv_seeds_map = seeds
            w._refresh_fit_readiness()
            with patch('spectra_app.main_window.find_peak_indices', side_effect=AssertionError('CSV fallback')):
                w._on_fit()
                w._on_batch_fit_all_gates()
                wait_jobs(w)
            self.assertIsNone(self.first.fit_result)
            self.assertEqual(self.first.batch_fit_result_map, {})
        self.errors.clear()

    def test_inactive_removal_preserves_raw_cursor_summary_and_modes_switch_clears(self):
        w = self.w
        oa.set_summary(w, '<b>plain owner summary</b>')
        self.first.peak_indices = np.array([20, 40])
        self.first.peak_gate_idx = 0
        self.first.batch_peak_map = {0: np.array([20, 40])}
        w._sync_batch_peak_coords()
        w._refresh_peak_delete_controls()
        oa.click(w, 'lasso')
        self.assertTrue(w._lasso_mode)
        editor = oa.edit(w, 'prominence', '0.127')
        oa.key(w, 'prominence', Qt.Key_Left)
        cursor = editor.property('cursorPosition')
        token = w.ordinary_analysis.ownerToken
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.YES):
            self.assertTrue(w.remove_dataset(self.second.dataset_id))
        pump()
        self.assertEqual(w.ordinary_analysis.ownerToken, token)
        self.assertEqual(editor.property('text'), '0.127')
        self.assertEqual(editor.property('cursorPosition'), cursor)
        self.assertTrue(editor.hasActiveFocus())
        self.assertTrue(w._lasso_mode)
        summary = item(item(w, 'ordinaryAnalysisRoot'), 'ordinarySummary')
        self.assertEqual(summary.property('text'), '<b>plain owner summary</b>')
        self.assertEqual(ah.enum_value(summary, 'textFormat'), 0)
        self.assertEqual(analysis_owner(self.first).summary, '<b>plain owner summary</b>')
        third = self.load('third')
        self.assertFalse(w._lasso_mode)
        self.assertFalse(w._circle_delete_mode)
        self.assertEqual(oa.summary(w), third.summary)
        self.assertIn('Loaded: third.dat', oa.summary(w))
        self.assertNotEqual(oa.summary(w), '<b>plain owner summary</b>')
        w.activate_dataset(self.first.dataset_id)
        self.assertEqual(oa.value(w, 'prominence'), .127)
        self.assertEqual(oa.summary(w), '<b>plain owner summary</b>')
        self.assertFalse(w._lasso_mode)
        self.assertFalse(w.ordinary_analysis.setDraft('prominence', '.9', token))
        self.assertIsNot(w.workspace.active, third)
        self.assertEqual(self.errors, [])

    def test_shared_summary_writes_and_projects_the_named_scientific_owner(self):
        w = self.w
        oa.set_summary(w, 'ordinary owner')
        w.open_graph_document()
        w.analyze_action.trigger()
        pump()
        document = w._workbench_document()
        self.assertIsNotNone(document)
        self.assertIsNot(analysis_owner(self.first), self.first)
        w._set_analysis_summary('named owner')
        self.assertEqual(document.science.summary, 'named owner')
        self.assertEqual(self.first.summary, 'ordinary owner')
        self.assertEqual(oa.summary(w), 'named owner')
        self.assertTrue(item(w, 'analysisWorkbench').isVisible())
        self.assertIsNone(w.root_window.findChild(QQuickItem, 'ordinaryAnalysisRoot'))
        saved = w.save_project(self.folder / 'shared-summary.spectra')
        restored = read_project(saved).datasets[self.first.dataset_id]
        self.assertEqual(analysis_owner(restored).summary, 'named owner')
        self.assertEqual(restored.summary, 'ordinary owner')
        w.activate_graph('default')
        oa.reveal(w)
        self.assertEqual(oa.summary(w), 'ordinary owner')
        self.assertEqual(item(item(w, 'ordinaryAnalysisRoot'), 'ordinarySummary').property('text'), 'ordinary owner')
        self.assertEqual(self.errors, [])

    def test_live_workbook_and_xy_context_reject_old_ordinary_gestures(self):
        from spectra_app.workbook_state import create_comparison
        w = self.w
        original = deepcopy(self.first.analysis_controls)
        owner = w.ordinary_analysis.ownerToken
        sheet = next(sheet for sheet in w.workspace.sheets.values()
                     if sheet.dataset_id == self.first.dataset_id)
        book = w._sheet_owner(sheet.sheet_id)
        comparison = create_comparison(w.workspace)
        for activate in (
                lambda: w.open_workbook(book.workbook_id, sheet.sheet_id),
                lambda: w.open_xy_comparison(comparison.comparison_id)):
            self.assertTrue(w.request_outer_view('analysis'))
            activate()
            pump()
            self.assertTrue(w._auxiliary_active())
            self.assertFalse(w.ordinary_analysis.enabled)
            self.assertIsNone(w.root_window.findChild(QQuickItem, 'ordinaryAnalysisRoot'))
            self.assertFalse(w.ordinary_analysis.setDraft('prominence', '.9', owner))
            self.assertFalse(w.ordinary_analysis.setValue('peak_mode', 'Both', owner))
            self.assertFalse(w.ordinary_analysis.step('distance', 1, owner))
            self.assertFalse(w.ordinary_analysis.trigger('find', owner))
            self.assertFalse(w.ordinary_analysis.setPeakNumber(2, owner))
            self.assertEqual(self.first.analysis_controls, original)
            self.assertEqual(self.first.peak_indices.size, 0)
        self.assertEqual(self.errors, [])
