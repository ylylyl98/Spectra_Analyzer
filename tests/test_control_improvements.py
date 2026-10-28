from tests import ordinary_analysis_quick_helpers as oa
from tests import analysis_header_quick_helpers as ah
from tests import dat_properties_quick_helpers as dp
from tests.quick_helpers import set_processing_window
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import threading
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from spectra_app.plot_dialogs import BatchExportDialog
from spectra_app.dataset_state import DatasetSession
from spectra_app.plot_presentation import PlotAnnotation
from tests.background_helpers import wait_jobs
from tests.formal_quick_helpers import edit_field, pump, item, input_receiver
from tests import test_workflow as workflow
from tests.scientific_fixtures import workflow_fit_result as _fit_result
from tests.quick_migration_case import QuickMigrationCase

class ControlImprovementTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = workflow.WorkflowTests.make_window(self, np.ones((121, 101)), gates=np.arange(121) * 1.25)
        self.w._base_cube = self.w.current_cube
        self.w.peak_indices = np.array([50])
        self.w._peak_gate_idx = 0
        self.w.batch_peak_map = {0: np.array([50]), 1: np.array([50])}
        self.errors = []
        self.w._show_error = self.errors.append
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        wait_jobs(self.w)
        pump()

    def show_plot_properties(self):
        page = self.w.open_graph_document()
        self.w.dat_details_action.setChecked(True)
        self.assertTrue(self.w.request_task(2))
        pump(60)
        self.assertTrue(item(self.w, 'formalProperties').isVisible())
        return page

    def step_plot_field(self, page, field):
        control = edit_field(self.w, field, page.formal.state['fields'][field])
        self.assertTrue(control.hasActiveFocus())
        QTest.keyClick(input_receiver(self.w, control), Qt.Key_Up)
        pump()
        return float(page.formal.state['fields'][field])

    def test_linewidth_invalid_requests_preserve_results_and_seeds_on_every_path(self):
        w = self.w
        old_fit = _fit_result(10)
        w.fit_result = old_fit
        w._fit_gate_idx = 0
        oa.set_number(w, 'linewidth_min', 50)
        oa.set_number(w, 'linewidth_max', 10)
        w._refresh_fit_readiness()
        self.assertFalse(oa.command_enabled(w, 'fit'))
        self.assertFalse(oa.command_enabled(w, 'batchFit'))
        self.assertIn('linewidth', oa.ui(w, 'readinessText').lower())
        with patch('spectra_app.main_window.fit_multi_peak', return_value=_fit_result(11)):
            for action in (w._on_fit, w._on_batch_fit_all_gates, w._on_detect_then_fit_current_gate):
                action()
                self.assertFalse(w._background_jobs.busy)
                self.assertIs(w.fit_result, old_fit)
                np.testing.assert_array_equal(w.peak_indices, [50])
        self.assertEqual((oa.value(w, 'linewidth_min'), oa.value(w, 'linewidth_max')), (50, 10))
        self.assertTrue(all('linewidth' in error.lower() for error in self.errors))
        oa.set_number(w, 'linewidth_max', 0)
        w._refresh_fit_readiness()
        self.assertTrue(oa.command_enabled(w, 'fit'))

    def test_invalid_detect_then_fit_does_not_detect_or_replace_seeds(self):
        w = self.w
        w.peak_indices = np.array([], dtype=int)
        w.batch_peak_map = {}
        oa.set_number(w, 'linewidth_min', 50)
        oa.set_number(w, 'linewidth_max', 10)
        w._on_detect_then_fit_current_gate()
        self.assertEqual(w.peak_indices.size, 0)
        self.assertTrue(any('linewidth' in error.lower() for error in self.errors))

    def test_gate_index_typing_commits_one_actual_gate_and_arrow_steps_immediately(self):
        w = self.w
        pump()
        with patch.object(w, '_update_spectrum_only_impl', wraps=w._update_spectrum_only_impl) as rebuild:
            ah.edit(w, 'index', '120')
            self.assertEqual(w.current_gate, 0)
            self.assertEqual(rebuild.call_count, 0)
            ah.key(w, Qt.Key_Return)
            self.assertEqual(rebuild.call_count, 1)
            self.assertEqual(w.current_gate, 148.75)
            self.assertEqual(int(ah.text(w, 'index')), 120)
            ah.key(w, Qt.Key_Up)
            self.assertEqual(w.current_gate, 150)
            self.assertEqual(int(ah.text(w, 'index')), 121)
            self.assertEqual(rebuild.call_count, 2)

    def test_fit_bound_typing_rebuilds_once_on_focus_out(self):
        w = self.w
        oa.select_tab(w, 1)
        pump()
        with patch.object(w, '_update_spectrum_only', wraps=w._update_spectrum_only) as rebuild:
            oa.edit(w, 'fit_xmin', '1.23', commit=False)
            self.assertEqual(rebuild.call_count, 0)
            oa.focus(w, 'fit_xmax')
            pump()
            self.assertEqual(rebuild.call_count, 1)
        self.assertAlmostEqual(oa.value(w, 'fit_xmin'), 1.23)

    def test_busy_fit_deduplicates_same_target_but_allows_new_gate(self):
        w = self.w
        started = threading.Event()
        def solver(*args, **kwargs):
            started.set()
            self.release.wait(5)
            return _fit_result(10)
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            w._on_fit()
            self.assertTrue(started.wait(2))
            self.assertIn('Fitting', oa.command_text(w, 'fit'))
            self.assertFalse(oa.command_enabled(w, 'fit'))
            with patch.object(w, '_analysis_job_key', wraps=w._analysis_job_key) as snapshot_key:
                w._on_fit()
                self.assertEqual(snapshot_key.call_count, 0)
            self.assertEqual(len(w._background_jobs._jobs), 1)
            self.assertIsNone(w._background_jobs._pending)
            ah.edit(w, 'index', '2', commit=True)
            self.assertTrue(oa.command_enabled(w, 'fit'))
            w._on_fit()
            self.assertEqual(len(w._background_jobs._jobs), 1)
            self.assertIsNotNone(w._background_jobs._pending)
            self.release.set()
            wait_jobs(w)
        self.assertIn(1, w.batch_fit_result_map)
        self.assertNotIn(0, w.batch_fit_result_map)
        self.assertEqual(oa.command_text(w, 'fit'), 'Fit Current Gate')
        self.assertTrue(oa.command_enabled(w, 'fit'))

    def test_fit_error_cancel_and_setting_changes_restore_readiness(self):
        w = self.w
        def solver(*args, **kwargs):
            self.release.wait(5)
            raise ValueError('solver failure')
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            w._on_fit()
            self.assertFalse(oa.command_enabled(w, 'fit'))
            oa.set_number(w, 'linewidth_min', 12)
            self.assertTrue(oa.command_enabled(w, 'fit'))
            w._on_fit()
            w._cancel_background_job()
            self.assertTrue(oa.command_enabled(w, 'fit'))
            self.release.set()
            wait_jobs(w)
            w._on_fit()
            wait_jobs(w)
        self.assertTrue(oa.command_enabled(w, 'fit'))
        self.assertNotIn('Fitting', oa.command_text(w, 'fit'))
        self.assertTrue(any('solver failure' in error for error in self.errors))

    def test_single_detection_shows_local_activity_and_restores_find_action(self):
        w = self.w
        feedback = []
        def detect(*args, **kwargs):
            feedback.append((oa.command_text(w, 'find'), oa.ui(w, 'detectionText'), oa.command_enabled(w, 'find')))
            return np.array([30]), np.array([True])
        with patch('spectra_app.main_window.find_peak_indices', side_effect=detect):
            w._on_find_peaks()
        self.assertIn('Detecting', feedback[0][0])
        self.assertIn('Detecting', feedback[0][1])
        self.assertFalse(feedback[0][2])
        self.assertEqual(oa.command_text(w, 'find'), 'Find Peaks (This Gate)')
        self.assertTrue(oa.command_enabled(w, 'find'))
        np.testing.assert_array_equal(w.peak_indices, [30])

    def test_batch_activity_deduplicates_and_restores_after_cancel(self):
        w = self.w
        for action, command, path, result, word in (
            (w._on_batch_fit_all_gates, 'batchFit', 'spectra_app.main_window.fit_multi_peak',
             _fit_result(10), 'Fitting'),
            (w._on_batch_find_peaks_all_gates, 'batchFind', 'spectra_app.main_window.find_peak_indices',
             (np.array([30]), np.array([True])), 'Detecting')):
            self.release.clear()
            started = threading.Event()
            def compute(*args, **kwargs):
                started.set()
                self.release.wait(5)
                return result
            with patch(path, side_effect=compute):
                action()
                self.assertTrue(started.wait(2))
                self.assertIn(word, oa.command_text(w, command))
                self.assertFalse(oa.command_enabled(w, command))
                with patch.object(w, '_analysis_job_key', wraps=w._analysis_job_key) as snapshot_key:
                    action()
                    self.assertEqual(snapshot_key.call_count, 0)
                self.assertEqual(len(w._background_jobs._jobs), 1)
                w._cancel_background_job()
                self.assertTrue(oa.command_enabled(w, command))
                self.release.set()
                wait_jobs(w)
            np.testing.assert_array_equal(w.batch_peak_map[0], [50])

    def test_processing_applied_pending_and_short_window_are_local(self):
        w = self.w
        self.assertTrue(hasattr(w, 'processing'))
        self.assertIn('Original', w.processing.status)
        self.assertFalse(w.processing.smoothingEnabled)
        self.assertFalse(w.processing.windowEnabled)
        w.current_cube = replace(w.current_cube, photon_energy=w.current_cube.energy.copy())
        w._base_cube = w.current_cube
        w.processing.setMode('energy_second_derivative')
        self.assertIn('choose Apply processing', w.processing.status)
        self.assertTrue(w.processing.windowEnabled)
        set_processing_window(w.processing, 201)
        self.assertIn('101', w.processing.status)
        w._on_generate_heatmap()
        self.assertIn('Applying processing', w.processing.status)
        wait_jobs(w)
        self.assertIn('Applied: D²', w.processing.status)
        self.assertNotIn('Pending:', w.processing.status)
        set_processing_window(w.processing, 21)
        self.assertIn('choose Apply processing', w.processing.status)
        w.processing.setSmoothing(False)
        self.assertFalse(w.processing.windowEnabled)
        w.processing.setMode('original')
        self.assertIn('Pending: Original', w.processing.status)
        w.processing.apply()
        wait_jobs(w)
        self.assertIn('Applied: Original', w.processing.status)
        self.assertIs(w.current_cube, w._base_cube)

    def test_typed_even_sg_is_rejected_without_changing_trace_or_peaks(self):
        w = self.w
        w.current_cube = replace(w.current_cube, photon_energy=w.current_cube.energy.copy())
        w._base_cube = w.current_cube
        before = w.current_cube
        w.processing.setMode('energy_second_derivative')
        w.processing.setWindowDraft('20')
        w.processing.commitWindow()
        w._on_generate_heatmap()
        self.assertFalse(w._background_jobs.busy)
        self.assertIs(w.current_cube, before)
        self.assertTrue(hasattr(w, 'processing'))
        self.assertIn('odd', w.processing.error.lower())
        for smooth, field, actions, label in (
            ('peak_smooth', 'peak_smooth_window',
             (w._on_find_peaks, w._on_batch_find_peaks_all_gates), 'detectionText'),
            ('fit_smooth', 'fit_smooth_window',
             (w._on_fit, w._on_batch_fit_all_gates), 'readinessText')):
            oa.set_value(w, smooth, True)
            oa.edit(w, field, '10', commit=True)
            for action in actions:
                action()
                self.assertFalse(w._background_jobs.busy)
                np.testing.assert_array_equal(w.peak_indices, [50])
            self.assertIn('odd', oa.ui(w, label).lower())
            oa.set_number(w, field, 11)
            oa.key(w, field, Qt.Key_Up)
            self.assertEqual(oa.value(w, field), 13)

    def test_disabled_presmooth_and_d2_polyorder_effective_window(self):
        w = self.w
        oa.select_tab(w, 0)
        self.assertFalse(oa.control(w, 'peak_smooth_window').isVisible() and oa.enabled(w, 'peak_smooth_window'))
        oa.select_tab(w, 1)
        self.assertFalse(oa.control(w, 'fit_smooth_window').isVisible() and oa.enabled(w, 'fit_smooth_window'))
        oa.select_tab(w, 0)
        oa.set_value(w, 'peak_smooth', True)
        self.assertTrue(oa.control(w, 'peak_smooth_window').isVisible())
        self.assertTrue(oa.enabled(w, 'peak_smooth_window'))
        oa.set_value(w, 'derivative_detection', True)
        oa.set_number(w, 'd2_window', 3)
        oa.set_number(w, 'd2_polyorder', 3)
        w._on_find_peaks()
        self.assertIn('poly', oa.ui(w, 'detectionText').lower())
        np.testing.assert_array_equal(w.peak_indices, [50])
        oa.set_number(w, 'd2_window', 10)
        w._on_batch_find_peaks_all_gates()
        self.assertFalse(w._background_jobs.busy)

    def test_plot_steps_preserve_precise_positions_and_scientific_coordinates(self):
        page = self.show_plot_properties()
        p = replace(page.presentation, title_position=(.5, .8), annotations=(
            PlotAnnotation('a', position=(.5, .5)),
            PlotAnnotation('b', position=(10, 20), coordinate_space='data')))
        page.set_presentation(p)
        self.step_plot_field(page, 'title_x')
        self.assertAlmostEqual(page.presentation.title_position[0], .51)
        page.select_annotation('a')
        self.step_plot_field(page, 'annotation_x')
        self.assertTrue(page.formal.applyAnnotation())
        self.assertAlmostEqual(page.presentation.annotations[0].position[0], .51)
        page.select_annotation('b')
        stepped = self.step_plot_field(page, 'annotation_x')
        self.assertGreater(stepped, 10)
        self.assertLess(stepped, 11)
        old_width = page.presentation.width_in
        self.step_plot_field(page, 'width_in')
        self.assertAlmostEqual(page.presentation.width_in, old_width + .05)

    def test_d2_short_trace_keeps_valid_requested_poly_and_shows_effective_clamp(self):
        w = self.w
        oa.set_value(w, 'derivative_detection', True)
        dp.setup_number(w, 'xmax', .8)
        oa.set_number(w, 'd2_window', 11)
        oa.set_number(w, 'd2_polyorder', 6)
        w._on_find_peaks()
        self.assertEqual(self.errors, [])
        self.assertIn('5 points', oa.ui(w, 'detectionText'))
        self.assertIn('poly 4', oa.ui(w, 'detectionText'))
        self.assertEqual(oa.value(w, 'd2_polyorder'), 6)

    def test_fit_short_trace_shows_effective_window_without_changing_request(self):
        w = self.w
        oa.set_number(w, 'fit_xmax', 1.8)
        oa.set_value(w, 'fit_smooth', True)
        oa.set_number(w, 'fit_smooth_window', 11)
        self.assertIn('9 points', oa.ui(w, 'readinessText'))
        self.assertEqual(oa.value(w, 'fit_smooth_window'), 11)

    def test_annotation_data_step_uses_original_axis_spacing_after_exchange(self):
        w = self.w
        w.current_cube = replace(w.current_cube, energy=np.array([1., 1.2, 1.4]),
                                 x_original=np.array([1., 1.2, 1.4]), gate=np.array([10., 14.]),
                                 z=np.array([[1., 2., 3.], [4., 5., 6.]]))
        w._base_cube = w.current_cube
        w._set_view_limits_to_auto()
        w._plot_cube()
        page = self.show_plot_properties()
        p = replace(page.presentation, annotations=(PlotAnnotation(
            'a', position=(1.2, 14), coordinate_space='data'),))
        p = replace(p, axis=replace(p.axis, exchange_xy=True))
        w.set_plot_presentation(p)
        w._render_plot_page()
        self.assertIsNotNone(page.rendered_page, page.status_text)
        np.testing.assert_allclose(page.preview.snapshot.x, [1, 1.2, 1.4])
        np.testing.assert_allclose(page.preview.snapshot.y, [10, 14])
        page.select_annotation('a')
        self.assertAlmostEqual(self.step_plot_field(page, 'annotation_x'), 1.4)
        self.assertAlmostEqual(self.step_plot_field(page, 'annotation_y'), 18)

    def test_annotation_data_steps_refresh_when_the_formal_page_is_rendered(self):
        w = self.w
        page = self.show_plot_properties()
        w.set_plot_presentation(replace(page.presentation, annotations=(PlotAnnotation(
            'a', position=(10, 20), coordinate_space='data'),)))
        w._render_plot_page()
        self.assertIsNotNone(page.rendered_page, page.status_text)
        page.select_annotation('a')
        self.assertAlmostEqual(self.step_plot_field(page, 'annotation_x'), 10.2)
        self.assertAlmostEqual(self.step_plot_field(page, 'annotation_y'), 21.25)

    def test_dataset_switch_restores_sg_and_local_processing_feedback(self):
        w = self.w
        paths = []
        for name in ('first', 'second'):
            path = self.folder / (name + '.dat')
            path.write_text('Energy(eV) 0 1\n' + '\n'.join(
                f'{1+i/10} {i+1} {2*i+1}' for i in range(12)), encoding='utf-8')
            paths.append(path)
        w._load_dat_file_direct(paths[0])
        first = w.workspace.active_dataset_id
        w.processing.setMode('energy_second_derivative')
        set_processing_window(w.processing, 20)
        oa.set_value(w, 'fit_smooth', True)
        oa.set_number(w, 'fit_smooth_window', 10)
        w._load_dat_file_direct(paths[1])
        oa.set_value(w, 'fit_smooth', False)
        oa.select_tab(w, 1)
        self.assertIn('Applied: Original', w.processing.status)
        self.assertFalse(w.processing.smoothingEnabled)
        self.assertFalse(oa.control(w, 'fit_smooth_window').isVisible() and oa.enabled(w, 'fit_smooth_window'))
        w.activate_dataset(first)
        self.assertIn('odd', w.processing.error)
        self.assertIn('odd', oa.ui(w, 'readinessText'))
        self.assertTrue(oa.control(w, 'fit_smooth_window').isVisible())
        self.assertTrue(oa.enabled(w, 'fit_smooth_window'))
        self.assertFalse(oa.command_enabled(w, 'fit'))

    def test_batch_export_rejects_empty_and_unusable_common_folder_in_dialog(self):
        session = DatasetSession(dataset_id='a', source_path=self.folder / 'data.dat')
        session.active_graph.published = True
        dialog = BatchExportDialog([session], 'a')
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.bridge.canAccept)
        dialog.bridge.setCommonFolder(True)
        self.assertFalse(dialog.bridge.canAccept)
        self.assertTrue(dialog.bridge.folderError)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Rejected)
        dialog.set_destination_folder(self.folder / 'missing')
        self.assertFalse(dialog.bridge.canAccept)
        self.assertFalse((self.folder / 'missing').exists())
        dialog.set_destination_folder(self.folder)
        self.assertTrue(dialog.bridge.canAccept)
        self.assertEqual(dialog.selected_ids(), ['a'])
        self.assertEqual(dialog.formats(), ('svg', 'png'))
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Accepted)
