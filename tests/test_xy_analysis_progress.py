"""Resumable XY state and real inspector actions without detached exports."""
import json
import unittest
from dataclasses import replace
import numpy as np

from spectra_app.analysis_document import AnalysisSeed
from spectra_app.dataset_state import Workspace
from spectra_app.peak_candidates import candidate_filter_preset
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_analysis import AnalysisSettings, capture_input, XYAnalysisProgress, collect_candidates, fit_seeds


class XYProgressStateTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        book = create_workbook(self.workspace)
        self.x = np.linspace(-6, 6, 601)
        self.y = 2 + 5*np.exp(-.5*((self.x+1.5)/.4)**2) + .2*np.exp(-.5*((self.x-2)/.3)**2)
        self.sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Source',
            np.column_stack([self.x, self.y]), ['Position', 'Signal'])
        self.comparison = create_comparison(self.workspace)
        self.curve = add_curve(self.workspace, self.comparison.comparison_id, self.sheet.sheet_id, 0, 1)
        self.captured = capture_input(self.workspace, self.comparison, self.curve.curve_id, AnalysisSettings())

    def progress(self):
        progress = XYAnalysisProgress(self.captured)
        progress.set_candidates(collect_candidates(self.captured.data, self.captured.settings))
        return progress

    def test_relaxation_and_manual_exclude_move_add_survive_filter(self):
        progress = self.progress()
        self.assertEqual(len(progress.candidate_pool), 2)
        progress.apply_filter(candidate_filter_preset('Strict'))
        self.assertEqual(len(progress.accepted_seeds()), 1)
        progress.apply_filter(candidate_filter_preset('Loose'))
        self.assertEqual(len(progress.accepted_seeds()), 2)
        first, second = progress.seeds
        progress.set_seeds([replace(first, manual=True, enabled=False),
            replace(second, manual=True, position=2.015), AnalysisSeed(position=.5, manual=True)])
        progress.apply_filter(candidate_filter_preset('Strict'))
        progress.apply_filter(candidate_filter_preset('Loose'))
        self.assertFalse(next(seed for seed in progress.seeds if seed.seed_id == first.seed_id).enabled)
        self.assertEqual([seed.position for seed in progress.accepted_seeds()], [.5, 2.015])

    def test_json_roundtrip_rejects_stale_or_malformed_records(self):
        progress = self.progress()
        progress.fit_result = fit_seeds(progress.captured.data, progress.captured.settings, progress.seeds)
        progress.status = 'fitted'
        payload = json.loads(json.dumps(progress.to_payload(), allow_nan=False))
        restored = XYAnalysisProgress.from_payload(self.workspace, self.comparison, payload)
        np.testing.assert_allclose(restored.fit_result.y_fit, progress.fit_result.y_fit)
        self.assertEqual(restored.candidate_pool, progress.candidate_pool)
        self.assertEqual(restored.seeds, progress.seeds)
        broken = json.loads(json.dumps(payload)); broken['fit']['components'] = [[1]]
        with self.assertRaises(ValueError):
            XYAnalysisProgress.from_payload(self.workspace, self.comparison, broken)
        for key, value in (('gammas', [1., 2.]), ('centers', [200., 300.]), ('amplitudes', [-1., -2.])):
            broken = json.loads(json.dumps(payload)); broken['fit'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                XYAnalysisProgress.from_payload(self.workspace, self.comparison, broken)
        broken = json.loads(json.dumps(payload)); del broken['provenance']['x_label']
        with self.assertRaises(ValueError):
            XYAnalysisProgress.from_payload(self.workspace, self.comparison, broken)
        self.sheet.data[:, 1] += .1
        with self.assertRaisesRegex(ValueError, 'changed'):
            XYAnalysisProgress.from_payload(self.workspace, self.comparison, payload)

    def test_gap_and_voigt_fit_roundtrip_preserves_sigma_gamma_and_source(self):
        from scipy.special import voigt_profile
        y = 2 + 5*voigt_profile(self.x-.7, .35, .2)/voigt_profile(0, .35, .2)
        y[40] = np.nan
        self.sheet.data[:, 1] = y
        original = self.sheet.data.copy()
        captured = capture_input(self.workspace, self.comparison, self.curve.curve_id,
                                 AnalysisSettings(model='Voigt'))
        progress = XYAnalysisProgress(captured)
        progress.set_seeds([AnalysisSeed(.7, manual=True, width=.35, gamma=.2, fixed_center=True)])
        progress.fit_result = fit_seeds(captured.data, captured.settings, progress.seeds)
        progress.status = 'fitted'
        restored = XYAnalysisProgress.from_payload(self.workspace, self.comparison,
            json.loads(json.dumps(progress.to_payload(), allow_nan=False)))
        np.testing.assert_allclose(restored.fit_result.centers, [.7], atol=1e-10)
        np.testing.assert_allclose(restored.fit_result.sigmas, [.35], atol=1e-5)
        np.testing.assert_allclose(restored.fit_result.gammas, [.2], atol=1e-5)
        np.testing.assert_array_equal(self.sheet.data, original)


import tests.test_xy_analysis_workflow as workflow_fixture
from tests.settings_helpers import wait_for_background_jobs


class XYProgressWorkflowTests(unittest.TestCase):
    setUp = workflow_fixture.XYAnalysisWorkflowTests.setUp
    dispose = workflow_fixture.XYAnalysisWorkflowTests.dispose
    analyze = workflow_fixture.XYAnalysisWorkflowTests.analyze
    fit = workflow_fixture.XYAnalysisWorkflowTests.fit

    def test_project_reopen_restores_progress_without_export(self):
        self.page.curve_list.clearSelection()
        self.page.curve_list.item(1).setSelected(True)
        self.analyze()
        self.panel.find_btn.click(); wait_for_background_jobs(self.w)
        self.panel.fit_btn.click(); wait_for_background_jobs(self.w)
        original = self.panel.fit_result.y_fit.copy()
        selected = self.panel.captured.curve_id
        ids = set(self.w.workspace.workbooks)
        comparison_id = self.page.comparison_id
        self.assertIsNotNone(self.page.comparison.analysis_state)
        target = self.folder / 'progress.spectra'
        self.w.save_project(target)
        self.assertTrue(self.w.open_project(target))
        self.w.open_xy_comparison(comparison_id)
        page = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        page.analysis_panel.resume_analysis()
        self.assertIsNotNone(page.analysis_panel.fit_result)
        self.assertEqual(page._selected_ids(), {selected})
        np.testing.assert_allclose(page.analysis_panel.fit_result.y_fit, original)
        self.assertEqual(set(self.w.workspace.workbooks), ids)
        self.assertIn('Restored', page.analysis_panel.status.text())

    def test_filter_refits_only_retire_fit_and_seed_edits_persist(self):
        self.fit()
        candidates = list(self.panel.progress.candidate_pool)
        self.panel.model_combo.setCurrentText('Voigt')
        self.assertIsNone(self.panel.fit_result)
        self.assertEqual(self.panel.progress.candidate_pool, candidates)
        self.assertTrue(self.panel.save_btn.isEnabled())
        seed = self.panel.progress.seeds[0]
        self.panel.set_seeds([replace(seed, position=.713, manual=True, fixed_center=True)])
        self.panel.preset_combo.setCurrentText('Strict')
        self.panel.preset_combo.setCurrentText('Loose')
        self.assertEqual(self.panel.progress.accepted_seeds()[0].position, .713)
        self.panel.fit_btn.click(); wait_for_background_jobs(self.w)
        self.assertAlmostEqual(self.panel.fit_result.centers[0], .713, places=10)
        payload = self.page.comparison.analysis_state
        self.assertEqual(payload['seeds'][0]['position'], .713)
        self.assertEqual(payload['status'], 'fitted')

    def test_malformed_progress_is_reported_without_publishing_fit(self):
        self.page.comparison.analysis_state = {'version': 1, 'fit': 'invalid'}
        self.panel.resume_analysis()
        self.assertIsNone(self.panel.fit_result)
        self.assertIn('could not be restored', self.panel.status.text())

    def test_seed_dialog_kind_edit_survives_refilter_and_preserves_exact_markers(self):
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtWidgets import QApplication, QTableWidget, QDialogButtonBox
        self.fit()
        failures = []
        original_seeds = list(self.panel.progress.seeds)
        original_fit = self.panel.fit_result
        def confirm():
            dialog = QApplication.activeModalWidget()
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        QTimer.singleShot(0, confirm)
        self.panel.seeds_btn.click()
        self.assertEqual(self.panel.progress.seeds, original_seeds)
        self.assertIs(self.panel.fit_result, original_fit)
        def edit():
            dialog = QApplication.activeModalWidget()
            try:
                table = dialog.findChild(QTableWidget, 'xy-seed-table')
                table.item(0, 1).setText('.713')
                table.item(0, 2).setText('dip')
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
            except Exception as exc:
                failures.append(str(exc))
                dialog.reject()
            if dialog.isVisible():
                failures.append('Seed dialog rejected valid manual edit')
                dialog.reject()
        QTimer.singleShot(0, edit)
        self.panel.seeds_btn.click()
        self.assertEqual(failures, [])
        self.panel.preset_combo.setCurrentText('Strict')
        self.panel.preset_combo.setCurrentText('Loose')
        accepted = self.panel.progress.accepted_seeds()
        self.assertEqual([(seed.position, seed.kind) for seed in accepted], [(.713, 'dip')])
        line = next(line for line in self.page.axes.lines if line.get_gid() == 'xy-analysis-peaks')
        np.testing.assert_array_equal(line.get_xdata(), [.713])
        self.panel.create_graph_btn.click()
        markers = next(sheet for sheet in self.w.workspace.sheets.values() if sheet.name == 'Peak curves')
        rows = np.isfinite(markers.data[:, 3])
        np.testing.assert_array_equal(markers.data[rows, 0], [.713])

    def test_invalid_filter_draft_cannot_drop_pool_or_start_job(self):
        self.fit()
        pool = list(self.panel.progress.candidate_pool)
        self.panel.candidate_width_min_spin.setValue(2)
        self.panel.candidate_width_max_spin.setValue(1)
        self.assertFalse(self.panel.find_btn.isEnabled())
        self.assertFalse(self.panel.fit_btn.isEnabled())
        self.panel.start('find')
        self.assertFalse(self.w._background_jobs.busy)
        self.panel.candidate_width_max_spin.setValue(0)
        self.assertEqual(self.panel.progress.candidate_pool, pool)


if __name__ == '__main__':
    unittest.main()
