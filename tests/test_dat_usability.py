"""DAT workbench acceptance checks at the document, UI, and saved-output boundaries."""
import csv
import json
from dataclasses import replace
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QTableWidget

from tests import test_analysis_workbench as base
from tests.settings_helpers import wait_for_background_jobs
from spectra_app.analysis_document import AnalysisSeed, rebind_analysis_input
from spectra_app.project_io import read_project, write_project


class DatUsabilityTests(unittest.TestCase):
    setUp = base.AnalysisWorkbenchTests.setUp
    dispose = base.AnalysisWorkbenchTests.dispose
    enter = base.AnalysisWorkbenchTests.enter
    find = base.AnalysisWorkbenchTests.find

    def test_precise_seed_graph_retains_identity_kinds_source_and_project_precision(self):
        graph = self.enter()
        doc = graph.analysis
        gate = self.w._current_gate_index()
        seeds = [AnalysisSeed(1.35123, manual=True), AnalysisSeed(1.35124, kind='dip', manual=True)]
        doc.set_seeds(gate, seeds)
        self.w._workbench_refresh()
        self.w.workbench_linecut_btn.click()
        comparison = list(self.w.workspace.comparisons.values())[-1]
        self.assertEqual(len(comparison.curves), 3)
        sheet = self.w.workspace.sheets[next(iter(comparison.curves.values())).sheet_id]
        self.assertEqual(sheet.data[np.isfinite(sheet.data[:, 2]), 0].tolist(), [1.35123])
        self.assertEqual(sheet.data[np.isfinite(sheet.data[:, 3]), 0].tolist(), [1.35124])
        np.testing.assert_array_equal(sheet.data[:201, :2], np.column_stack((doc.science.current_cube.energy, doc.science.current_cube.z[gate])))
        provenance = json.loads(sheet.snapshot_note)
        self.assertEqual(provenance['input_version'], doc.input_version)
        self.assertEqual([s['seed_id'] for s in provenance['seeds']], [s.seed_id for s in seeds])
        target = self.folder / 'precise.spextra'
        write_project(target, self.w.workspace)
        restored = read_project(target).sheets[sheet.sheet_id]
        np.testing.assert_array_equal(restored.data, sheet.data)
        self.assertEqual(restored.snapshot_note, sheet.snapshot_note)

    def test_unchanged_input_is_disabled_and_model_rebind_is_a_noop(self):
        graph = self.enter()
        self.find()
        self.w.workbench_model.setCurrentText('Gaussian')
        self.w.workbench_fit_btn.click()
        wait_for_background_jobs(self.w, timeout=15)
        doc, token = graph.analysis, graph.analysis.job_token
        fitted = dict(doc.science.batch_fit_result_map)
        self.assertFalse(self.w.workbench_bind_btn.isEnabled())
        self.assertIs(rebind_analysis_input(self.session), doc)
        self.w._workbench_rebind_input()
        self.assertIs(graph.analysis, doc)
        self.assertEqual(doc.job_token, token)
        self.assertEqual(set(doc.candidate_pools), {0, 1, 2})
        for gate, fit in fitted.items():
            self.assertIs(doc.science.batch_fit_result_map[gate], fit)

    def test_changed_recipe_and_content_need_confirmation_and_duplicate_preserves_results(self):
        graph = self.enter()
        self.find()
        doc = graph.analysis
        self.session.applied.window_length += 2
        self.w._workbench_refresh()
        self.assertTrue(self.w.workbench_bind_btn.isEnabled())
        with patch.object(self.w, '_workbench_confirm_rebind', return_value='cancel') as confirm:
            self.w._workbench_rebind_input()
        self.assertEqual(confirm.call_args.args[0], 3)
        self.assertIs(graph.analysis, doc)
        self.session.current_cube = replace(self.session.current_cube, z=self.session.current_cube.z * 2)
        with patch.object(self.w, '_workbench_confirm_rebind', return_value='duplicate'):
            self.w._workbench_rebind_input()
        self.assertIs(graph.analysis, doc)
        self.assertEqual(set(doc.candidate_pools), {0, 1, 2})
        replacement = self.session.active_graph.analysis
        self.assertIsNot(replacement, doc)
        self.assertEqual(replacement.candidate_pools, {})
        np.testing.assert_array_equal(replacement.science.current_cube.z, self.session.current_cube.z)

    def test_gate_filter_restore_and_pending_scope_do_not_mutate_applied_filter(self):
        graph = self.enter()
        self.find()
        self.w.workbench_scope.setCurrentIndex(0)
        self.w._set_current_gate_from_index(0, update_plot=True)
        self.w.workbench_filter_preset.setCurrentText('Loose')
        self.w.workbench_filter_btn.click()
        self.w._set_current_gate_from_index(1, update_plot=True)
        self.w.workbench_filter_preset.setCurrentText('Strict')
        self.assertIn('Pending: Strict', self.w.workbench_filter_state.text())
        self.assertEqual(graph.analysis.filters_by_gate[1].preset, 'Balanced')
        self.w.workbench_filter_btn.click()
        self.w._set_current_gate_from_index(0, update_plot=True)
        self.assertEqual(self.w.workbench_filter_preset.currentText(), 'Loose')
        self.assertIn('Applied: Loose', self.w.workbench_filter_state.text())
        self.w.workbench_scope.setCurrentIndex(1)
        self.assertIn('mixed', self.w.workbench_filter_state.text().lower())
        self.assertIn('All 3 Gates', self.w.workbench_filter_state.text())

    def test_mixed_fit_summary_messages_and_locate_gate(self):
        from spectra_app.fitting import fit_multi_peak
        graph = self.enter()
        self.find()
        graph.analysis.set_seeds(2, [])
        self.w.workbench_model.setCurrentText('Gaussian')
        count = 0
        def controlled(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError('Gate trace has invalid data')
            return fit_multi_peak(*args, **kwargs)
        with patch('spectra_app.fitting.fit_multi_peak', side_effect=controlled):
            self.w.workbench_fit_btn.click()
            wait_for_background_jobs(self.w, timeout=15)
        summary = self.w.workbench_status.text()
        for expected in ('1 fitted', '1 failed', '1 no-seed', '0 cancelled', '3/3 processed'):
            self.assertIn(expected, summary)
        with patch.object(QDialog, 'exec', return_value=0):
            self.w._workbench_results_table()
        dialog = self.w.findChildren(QDialog)[-1]
        table = dialog.findChild(QTableWidget)
        message_col = next(c for c in range(table.columnCount()) if table.horizontalHeaderItem(c).text() == 'Message')
        self.assertEqual(table.horizontalHeader().visualIndex(message_col), 2,
                         'Failure reasons must be visible beside Gate and Status without horizontal scrolling')
        failed_row = next(r for r in range(table.rowCount()) if 'invalid data' in table.item(r, message_col).text())
        table.setCurrentCell(failed_row, 0)
        dialog.findChild(type(self.w.workbench_fit_btn), 'analysisLocateGate').click()
        self.assertEqual(self.w._current_gate_index(), 1)

    def test_cancel_retains_completed_fit_and_reports_remaining_gates(self):
        from spectra_app.fitting import fit_multi_peak
        graph = self.enter()
        self.find()
        self.w.workbench_model.setCurrentText('Gaussian')
        started, release = threading.Event(), threading.Event()
        count = 0
        def controlled(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                started.set()
                release.wait(5)
            return fit_multi_peak(*args, **kwargs)
        try:
            with patch('spectra_app.fitting.fit_multi_peak', side_effect=controlled):
                self.w.workbench_fit_btn.click()
                self.assertTrue(started.wait(5))
                self.w._cancel_background_job()
                release.set()
                wait_for_background_jobs(self.w, timeout=15)
        finally:
            release.set()
        self.assertEqual(set(graph.analysis.science.batch_fit_result_map), {0})
        self.assertIn('1 fitted', self.w.workbench_status.text())
        self.assertIn('2 cancelled', self.w.workbench_status.text())
        self.assertIn('Cancelled', graph.analysis.gate_messages[1])

    def test_all_gate_parameters_save_to_workbook_and_csv_with_precision_and_provenance(self):
        graph = self.enter()
        self.find()
        graph.analysis.set_seeds(2, [AnalysisSeed(1.351234567891, kind='dip', enabled=False, manual=True)])
        self.w.workbench_scope.setCurrentIndex(0)
        self.w._set_current_gate_from_index(0, update_plot=True)
        self.w.workbench_model.setCurrentText('Gaussian')
        self.w.workbench_fit_btn.click()
        wait_for_background_jobs(self.w, timeout=15)
        graph.analysis.gate_status[1] = 'failed'
        graph.analysis.gate_messages[1] = 'Test failure detail'
        target = self.folder / 'parameters.csv'
        with patch('spectra_app.analysis_workbench.QFileDialog.getSaveFileName', return_value=(str(target), 'CSV')):
            self.w._workbench_export_parameters()
        with target.open(newline='', encoding='utf-8-sig') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual({float(row['Gate']) for row in rows}, {0., 2., 5.})
        self.assertTrue(any(row['Message'] == 'Test failure detail' for row in rows))
        self.assertTrue(any(float(row['Seed X'] or 'nan') == 1.351234567891 for row in rows))
        self.assertTrue(all(row['Input version'] == graph.analysis.input_version for row in rows))
        self.w._workbench_save_parameters()
        sheet = next(s for s in self.w.workspace.sheets.values() if s.name == 'Analysis parameters')
        self.assertEqual(sheet.data.shape[0], len(rows))
        self.assertIn('Baseline', sheet.column_names)
        self.assertIn('RMSE', sheet.column_names)
        self.assertIn('Sigma', sheet.column_names)
        self.assertEqual(json.loads(sheet.snapshot_note)['input_version'], graph.analysis.input_version)

    def test_model_width_units_and_gamma_editability(self):
        graph = self.enter()
        gate = self.w._current_gate_index()
        graph.analysis.set_seeds(gate, [AnalysisSeed(1.35123, manual=True, gamma=.01)])
        self.w._workbench_refresh()
        self.assertIn('HWHM', self.w.workbench_seeds.horizontalHeaderItem(4).text())
        self.assertIn('eV', self.w.workbench_seeds.horizontalHeaderItem(4).text())
        self.assertFalse(self.w.workbench_seeds.item(0, 5).flags() & Qt.ItemIsEditable)
        self.w.workbench_model.setCurrentText('Voigt')
        self.assertIn('sigma', self.w.workbench_seeds.horizontalHeaderItem(4).text().lower())
        self.assertTrue(self.w.workbench_seeds.item(0, 5).flags() & Qt.ItemIsEditable)

    def test_effective_d2_windows_show_segment_adjustments_and_input_context_is_frozen(self):
        graph = self.enter()
        doc = graph.analysis
        cube = doc.science.current_cube
        z = cube.z.copy()
        z[0, :7] += np.arange(7) * .01
        z[0, 7:10] = np.nan
        z[0, 25:29] = np.nan
        doc.science.current_cube = replace(cube, z=z)
        self.w.workbench_scope.setCurrentIndex(0)
        self.w._set_current_gate_from_index(0, update_plot=True)
        self.w.workbench_detector.setCurrentIndex(1)
        self.w.workbench_sg_window.setValue(12)
        self.assertIn('7, 13', self.w.workbench_d2_note.text())
        self.session.graphs[self.source_id].gate_indices = [0]
        self.w._workbench_refresh()
        text = self.w.analysis_context_label.text()
        self.assertIn('Frozen', text)
        self.assertIn(doc.input_version[:8], text)
        self.assertIn('visible 3/3', text)
        self.assertIn('analyzed 1', text)
        self.assertIn('Source changed', text)

    def test_fit_linecut_uses_full_frozen_source_with_interval_fit(self):
        graph = self.enter()
        self.find(all_gates=False)
        self.w.workbench_model.setCurrentText('Gaussian')
        self.w.workbench_xmin.setValue(1.2)
        self.w.workbench_xmax.setValue(1.5)
        self.w.workbench_fit_btn.click()
        wait_for_background_jobs(self.w, timeout=15)
        cube = graph.analysis.science.current_cube
        gate = self.w._current_gate_index()
        fit = graph.analysis.science.batch_fit_result_map[gate]
        self.w.workbench_linecut_btn.click()
        comparison = list(self.w.workspace.comparisons.values())[-1]
        sheet = self.w.workspace.sheets[next(iter(comparison.curves.values())).sheet_id]
        np.testing.assert_array_equal(sheet.data[:, :2], np.column_stack([cube.energy, cube.z[gate]]))
        valid = np.isin(cube.energy, fit.x)
        np.testing.assert_array_equal(sheet.data[valid, 2], fit.y_fit)
        self.assertTrue(np.isnan(sheet.data[~valid, 2:]).all())
        np.testing.assert_allclose(sheet.data[valid, 3:], (fit.components + fit.baseline).T)

    def test_replace_confirmation_defaults_to_cancel_and_gate_scope_change_is_detected(self):
        from PySide6.QtWidgets import QMessageBox
        graph = self.enter()
        self.find()
        self.session.graphs[self.source_id].gate_indices = [0, 2]
        self.w._workbench_refresh()
        self.assertTrue(self.w.workbench_bind_btn.isEnabled())
        captured = []
        def inspect(box):
            captured.append(box)
            self.assertIs(box.defaultButton(), box.button(QMessageBox.Cancel))
            self.assertIn('3 Gates', box.text())
            self.assertTrue(any('Duplicate' in button.text() for button in box.buttons()))
        old = graph.analysis
        with patch.object(QMessageBox, 'exec', inspect):
            self.w._workbench_rebind_input()
        self.assertEqual(len(captured), 1)
        self.assertIs(graph.analysis, old)
        self.session.remove_graph(self.source_id)
        with self.assertRaisesRegex(ValueError, 'source graph was removed'):
            rebind_analysis_input(self.session)

