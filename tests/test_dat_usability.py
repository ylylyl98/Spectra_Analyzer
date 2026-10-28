"""DAT workbench acceptance checks at the document, UI, and saved-output boundaries."""
from tests import analysis_header_quick_helpers as ah
import csv
import json
from dataclasses import replace
from pathlib import Path
import threading
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtQuick import QQuickItem
from spectra_app.quick_dialog import QuickDialog

from tests import test_analysis_workbench as base
from tests import analysis_quick_helpers as aq
from tests.settings_helpers import wait_for_background_jobs
from tests.quick_migration_case import QuickMigrationCase
from spectra_app.analysis_document import AnalysisSeed, rebind_analysis_input
from spectra_app.project_io import read_project, write_project


class DatUsabilityTests(QuickMigrationCase):
    setUp = base.AnalysisWorkbenchTests.setUp

    def dispose(self):
        wait_for_background_jobs(self.w, timeout=10)
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.quick_errors, [])

    enter = base.AnalysisWorkbenchTests.enter
    find = base.AnalysisWorkbenchTests.find

    def test_precise_seed_graph_retains_identity_kinds_source_and_project_precision(self):
        graph = self.enter()
        doc = graph.analysis
        gate = self.w._current_gate_index()
        seeds = [AnalysisSeed(1.35123, manual=True), AnalysisSeed(1.35124, kind='dip', manual=True)]
        doc.set_seeds(gate, seeds)
        self.w._workbench_refresh()
        aq.command(self.w, 'linecut')
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
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        doc, token = graph.analysis, graph.analysis.job_token
        fitted = dict(doc.science.batch_fit_result_map)
        self.assertFalse(aq.control(self.w, 'analysis_rebind').isEnabled())
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
        self.assertTrue(aq.control(self.w, 'analysis_rebind').isEnabled())
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
        aq.set_choice(self.w, 'scope', 'current')
        self.w._set_current_gate_from_index(0, update_plot=True)
        aq.set_choice(self.w, 'filterPreset', 'Loose')
        aq.command(self.w, 'filter')
        self.w._set_current_gate_from_index(1, update_plot=True)
        aq.set_choice(self.w, 'filterPreset', 'Strict')
        self.assertIn('Pending: Strict', aq.state(self.w)['filterState'])
        self.assertEqual(graph.analysis.filters_by_gate[1].preset, 'Balanced')
        aq.command(self.w, 'filter')
        self.w._set_current_gate_from_index(0, update_plot=True)
        self.assertEqual(aq.state(self.w)['filterPreset'], 'Loose')
        self.assertIn('Applied: Loose', aq.state(self.w)['filterState'])
        aq.set_choice(self.w, 'scope', 'all')
        self.assertIn('mixed', aq.state(self.w)['filterState'].lower())
        self.assertIn('All 3 Gates', aq.state(self.w)['filterState'])

    def test_mixed_fit_summary_messages_and_locate_gate(self):
        from spectra_app.fitting import fit_multi_peak
        graph = self.enter()
        self.find()
        graph.analysis.set_seeds(2, [])
        aq.set_choice(self.w, 'model', 'Gaussian')
        count = 0
        def controlled(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError('Gate trace has invalid data')
            return fit_multi_peak(*args, **kwargs)
        with patch('spectra_app.fitting.fit_multi_peak', side_effect=controlled):
            aq.command(self.w, 'fit')
            wait_for_background_jobs(self.w, timeout=15)
        summary = aq.state(self.w)['status']
        for expected in ('1 fitted', '1 failed', '1 no-seed', '0 cancelled', '3/3 processed'):
            self.assertIn(expected, summary)
        dialog = self.w._workbench_results_table()
        self.addCleanup(dialog.reject)
        self.assertIsInstance(dialog, QuickDialog)
        bridge = dialog.bridge
        table = bridge.tableModel
        self.assertEqual(table.headerData(2, Qt.Horizontal, Qt.DisplayRole), 'Message',
                         'Failure reasons must be visible beside Gate and Status without horizontal scrolling')
        failed_row = next(r for r in range(table.rowCount())
                          if 'invalid data' in table.data(table.index(r, 2), Qt.DisplayRole))
        self.assertTrue(bridge.selectCell(failed_row, 0, bridge.ownerToken))
        self.assertTrue(bridge.locate(bridge.ownerToken))
        self.assertEqual(self.w._current_gate_index(), 1)

    def test_cancel_retains_completed_fit_and_reports_remaining_gates(self):
        from spectra_app.fitting import fit_multi_peak
        graph = self.enter()
        self.find()
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        previous = dict(graph.analysis.science.batch_fit_result_map)
        self.assertEqual(set(previous), {0, 1, 2})
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
                aq.command(self.w, 'fit')
                self.assertTrue(started.wait(5))
                self.w._cancel_background_job()
                release.set()
                wait_for_background_jobs(self.w, timeout=15)
        finally:
            release.set()
        fits = graph.analysis.science.batch_fit_result_map
        self.assertEqual(set(fits), {0, 1, 2})
        self.assertIsNot(fits[0], previous[0], 'Completed Gate must retain its newly computed fit')
        self.assertIs(fits[1], previous[1], 'Cancelled Gate must retain its previous fit')
        self.assertIs(fits[2], previous[2], 'Unstarted Gate must retain its previous fit')
        self.assertIn('1 fitted', aq.state(self.w)['status'])
        self.assertIn('2 cancelled', aq.state(self.w)['status'])
        self.assertIn('Cancelled', graph.analysis.gate_messages[1])

    def test_all_gate_parameters_save_to_workbook_and_csv_with_precision_and_provenance(self):
        graph = self.enter()
        self.find()
        graph.analysis.set_seeds(2, [AnalysisSeed(1.351234567891, kind='dip', enabled=False, manual=True)])
        aq.set_choice(self.w, 'scope', 'current')
        self.w._set_current_gate_from_index(0, update_plot=True)
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        graph.analysis.gate_status[1] = 'failed'
        graph.analysis.gate_messages[1] = 'Test failure detail'
        target = self.folder / 'parameters.csv'
        with patch('spectra_app.analysis_workbench.FilePicker.getSaveFileName', return_value=(str(target), 'CSV')):
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
        self.host.show()
        seed_id = graph.analysis.seeds_by_gate[gate][0].seed_id
        aq.select_seed(self.w, seed_id)
        aq.command(self.w, 'seeds')
        aq.pump()
        self.assertIn('HWHM', aq.state(self.w)['widthLabel'])
        self.assertIn('eV', aq.state(self.w)['xLabel'])
        self.assertFalse(aq.item(self.w.workbench_seed_dialog, seed_id + ':gamma').isEnabled())
        aq.set_choice(self.w, 'model', 'Voigt')
        self.assertIn('sigma', aq.state(self.w)['widthLabel'].lower())
        self.assertTrue(aq.item(self.w.workbench_seed_dialog, seed_id + ':gamma').isEnabled())
        self.assertEqual(graph.analysis.seeds_by_gate[gate][0].gamma, .01)

    def test_effective_d2_windows_show_segment_adjustments_and_input_context_is_frozen(self):
        graph = self.enter()
        doc = graph.analysis
        cube = doc.science.current_cube
        z = cube.z.copy()
        z[0, :7] += np.arange(7) * .01
        z[0, 7:10] = np.nan
        z[0, 25:29] = np.nan
        doc.science.current_cube = replace(cube, z=z)
        aq.set_choice(self.w, 'scope', 'current')
        self.w._set_current_gate_from_index(0, update_plot=True)
        aq.set_choice(self.w, 'detector', 'D2')
        aq.setup_number(self.w, 'd2Window', 12)
        self.assertIn('7, 13', aq.state(self.w)['d2Note'])
        self.session.graphs[self.source_id].gate_indices = [0]
        self.w._workbench_refresh()
        text = ah.context(self.w)
        self.assertIn('Frozen', text)
        self.assertIn(doc.input_version[:8], ah.details(self.w))
        self.assertIn('3/3 Gates', text)
        self.assertIn('analyzed 1', text)
        self.assertIn('Source changed', text)

    def test_fit_linecut_uses_full_frozen_source_with_interval_fit(self):
        graph = self.enter()
        self.find(all_gates=False)
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.setup_number(self.w, 'xmin', 1.2)
        aq.setup_number(self.w, 'xmax', 1.5)
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        cube = graph.analysis.science.current_cube
        gate = self.w._current_gate_index()
        fit = graph.analysis.science.batch_fit_result_map[gate]
        aq.command(self.w, 'linecut')
        comparison = list(self.w.workspace.comparisons.values())[-1]
        sheet = self.w.workspace.sheets[next(iter(comparison.curves.values())).sheet_id]
        np.testing.assert_array_equal(sheet.data[:, :2], np.column_stack([cube.energy, cube.z[gate]]))
        valid = np.isin(cube.energy, fit.x)
        np.testing.assert_array_equal(sheet.data[valid, 2], fit.y_fit)
        self.assertTrue(np.isnan(sheet.data[~valid, 2:]).all())
        np.testing.assert_allclose(sheet.data[valid, 3:], (fit.components + fit.baseline).T)

    def test_replace_confirmation_defaults_to_cancel_and_gate_scope_change_is_detected(self):
        from spectra_app.quick_dialog import QuickDialog
        graph = self.enter()
        self.find()
        self.session.graphs[self.source_id].gate_indices = [0, 2]
        self.w._workbench_refresh()
        self.assertTrue(aq.control(self.w, 'analysis_rebind').isEnabled())
        captured = []
        def inspect(host):
            captured.append(host)
            self.assertEqual(host.bridge.state['rebindCount'], 3)
            cancel = aq.item(host, 'analysis_rebind_cancel')
            self.assertIsNotNone(aq.item(host, 'analysis_rebind_duplicate'))
            host.show()
            aq.pump()
            self.assertTrue(cancel.hasActiveFocus(), 'Cancel must receive default keyboard focus')
            texts = [str(child.property('text')) for child in host.rootObject().findChildren(QQuickItem)
                     if child.property('text') is not None]
            self.assertTrue(any('3 Gates' in text for text in texts), texts)
            from PySide6.QtTest import QTest
            QTest.keyClick(host.window(), Qt.Key_Escape)
            self.assertEqual(host.result(), QuickDialog.Rejected)
            return host.result()
        old = graph.analysis
        with patch.object(QuickDialog, 'exec', inspect):
            self.w._workbench_rebind_input()
        self.assertEqual(len(captured), 1)
        self.assertIs(graph.analysis, old)
        self.session.remove_graph(self.source_id)
        with self.assertRaisesRegex(ValueError, 'source graph was removed'):
            rebind_analysis_input(self.session)

