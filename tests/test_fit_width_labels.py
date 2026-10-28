from tests import ordinary_analysis_quick_helpers as oa
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from unittest.mock import patch

import numpy as np
from PySide6.QtTest import QTest
from tests.formal_quick_helpers import item, pump

from spectra_app.fitting import fit_multi_peak
from tests.background_helpers import wait_jobs
from tests import test_workflow as workflow
from tests.quick_migration_case import QuickMigrationCase

class FitWidthLabelTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        x = np.linspace(0, 20, 101)
        y = 0.7 + 4 * np.exp(-0.5 * ((x - 10) / 1.0) ** 2)
        self.w = workflow.WorkflowTests.make_window(self, np.vstack([y, y]))
        self.errors = []
        self.w._show_error = self.errors.append
        oa.select_tab(self.w, 1)
        pump()

    def tearDown(self):
        wait_jobs(self.w)

    def assert_explanation(self, model, parameter, factor):
        label = item(self.w, 'ordinaryWidthHelp')
        self.assertTrue(label.isVisible())
        for meaning in (model, parameter, factor):
            self.assertIn(meaning, label.property('text'))
        for field in ('linewidth_min', 'linewidth_max'):
            editor = oa.control(self.w, field)
            self.assertIn(parameter, editor.property('helpText'))
            self.assertIn('points', editor.property('helpText'))

    def test_model_switch_refreshes_visible_meaning_without_changing_bounds(self):
        w = self.w
        oa.set_number(w, 'linewidth_min', 2)
        oa.set_number(w, 'linewidth_max', 8)
        self.assert_explanation('Lorentzian', 'gamma', '2')
        oa.set_value(w, 'fit_model', 'Gaussian')
        self.assert_explanation('Gaussian', 'sigma', '2.35482')
        self.assertEqual((oa.value(w, 'linewidth_min'), oa.value(w, 'linewidth_max')), (2, 8))
        oa.set_value(w, 'fit_model', 'Lorentzian')
        self.assert_explanation('Lorentzian', 'gamma', '2')
        self.assertEqual((oa.value(w, 'linewidth_min'), oa.value(w, 'linewidth_max')), (2, 8))
        oa.set_number(w, 'linewidth_max', 0)
        self.assertIn('auto', oa.ui(w, 'maxWidthText').lower())
        oa.set_value(w, 'fit_model', 'Gaussian')
        self.assertIn('auto', oa.ui(w, 'maxWidthText').lower())
        self.assertEqual((oa.value(w, 'linewidth_min'), oa.value(w, 'linewidth_max')), (2, 0))

    def test_inline_fit_conversions_do_not_overlap_editors_at_default_sidebar_width(self):
        w = self.w
        w.root_window.resize(1320, 850)
        QTest.qWait(30)
        self.assertGreaterEqual(item(w, 'dockedProperties').width(), 350)
        self.assertLessEqual(item(w, 'dockedProperties').width(), 400)
        oa.set_number(w, 'linewidth_min', 2)
        for model in ('Lorentzian', 'Gaussian'):
            oa.set_value(w, 'fit_model', model)
            for maximum in (8, 0):
                oa.set_number(w, 'linewidth_max', maximum)
                QTest.qWait(30)
                for field in ('linewidth_min', 'linewidth_max', 'peak_move_limit'):
                    editor = oa.control(w, field)
                    label = item(w, 'ordinaryConversion_' + field)
                    row = item(w, 'ordinaryRow_' + field)
                    with self.subTest(model=model, maximum=maximum, conversion=label.property('text')):
                        self.assertTrue(editor.isVisible())
                        self.assertTrue(label.isVisible())
                        editor_rect, label_rect, row_rect = map(oa.scene_rect, (editor, label, row))
                        self.assertFalse(editor_rect.intersects(label_rect),
                                         (editor_rect, label_rect, row_rect))
                        self.assertTrue(row_rect.contains(editor_rect))
                        self.assertTrue(row_rect.contains(label_rect))

    def test_dataset_and_project_restore_refresh_meaning_and_keep_saved_values(self):
        w = self.w
        for name, model, minimum, maximum in (
            ('gaussian', 'Gaussian', 4, 8), ('lorentzian', 'Lorentzian', 6, 0)
        ):
            path = self.folder / f'{name}.dat'
            path.write_text('Energy(eV) 0 1\n' + '\n'.join(
                f'{x:.12g} {x*x:.12g} {x:.12g}' for x in np.linspace(1, 4, 31)), encoding='utf-8')
            w._load_dat_file_direct(path)
            oa.set_value(w, 'fit_model', model)
            oa.set_number(w, 'linewidth_min', minimum)
            oa.set_number(w, 'linewidth_max', maximum)
            oa.set_summary(w, 'Stored old summary: wid=0.5')
        gaussian, lorentzian = [session for session in w.workspace.datasets.values()
                                if session.source_path.name in ('gaussian.dat', 'lorentzian.dat')]
        for session, model, parameter, factor, bounds in (
            (gaussian, 'Gaussian', 'sigma', '2.35482', (4, 8)),
            (lorentzian, 'Lorentzian', 'gamma', '2', (6, 0)),
            (gaussian, 'Gaussian', 'sigma', '2.35482', (4, 8))
        ):
            w.activate_dataset(session.dataset_id)
            oa.reveal(w, tab=1)
            self.assert_explanation(model, parameter, factor)
            self.assertEqual((oa.value(w, 'linewidth_min'), oa.value(w, 'linewidth_max')), bounds)
            self.assertEqual(oa.summary(w), 'Stored old summary: wid=0.5')
        project = w.save_project(self.folder / 'widths.spectra')
        oa.set_value(w, 'fit_model', 'Lorentzian')
        with patch.object(w, '_confirm_project_discard', return_value=True):
            self.assertTrue(w.open_project(project))
        oa.reveal(w, tab=1)
        self.assert_explanation('Gaussian', 'sigma', '2.35482')
        self.assertEqual((oa.value(w, 'linewidth_min'), oa.value(w, 'linewidth_max')), (4, 8))
        self.assertEqual(oa.summary(w), 'Stored old summary: wid=0.5')
        with patch.object(w, '_confirm_project_discard', return_value=True):
            self.assertTrue(w.new_project())
        self.assertIsNone(w.workspace.active)
        self.assertIsNone(w.current_cube)
        oa.reveal(w, tab=1)
        self.assert_explanation('Lorentzian', 'gamma', '2')
        self.assertEqual((oa.value(w, 'linewidth_min'), oa.value(w, 'linewidth_max')), (0, 0))
        self.assertEqual(self.errors, [])

    def test_current_and_batch_fit_keep_raw_bounds_and_report_raw_sigma(self):
        w = self.w
        oa.set_value(w, 'fit_model', 'Gaussian')
        oa.set_number(w, 'linewidth_min', 2)
        oa.set_number(w, 'linewidth_max', 8)
        w.peak_indices = np.array([50])
        w._peak_gate_idx = 0
        w.batch_peak_map = {0: np.array([50]), 1: np.array([50])}
        with patch('spectra_app.main_window.fit_multi_peak', wraps=fit_multi_peak) as solver:
            w._on_fit()
            wait_jobs(w)
            self.assertIsNotNone(w.fit_result)
            self.assertIn('sigma=', oa.summary(w))
            self.assertNotIn('wid=', oa.summary(w))
            self.assertAlmostEqual(float(w.fit_result.widths[0]), 1.0, places=5)
            w._on_batch_fit_all_gates()
            wait_jobs(w)
        self.assertEqual(solver.call_count, 3)
        for call in solver.call_args_list:
            self.assertEqual(call.kwargs['model'], 'Gaussian')
            # Existing point conversion uses the 20-unit span / 101 points.
            self.assertAlmostEqual(call.kwargs['linewidth_min'], 40 / 101)
            self.assertAlmostEqual(call.kwargs['linewidth_max'], 160 / 101)
        self.assertEqual(len(w.batch_fit_result_map), 2)
        for row in w._batch_fit_export_rows:
            self.assertAlmostEqual(float(row['peak_1_width']), 1.0, places=5)
        self.assertEqual(self.errors, [])
