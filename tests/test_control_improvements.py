import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from spectra_app.plot_dialogs import BatchExportDialog
from spectra_app.dataset_state import DatasetSession
from spectra_app.plot_presentation import HeatmapSnapshot, PlotAnnotation
from tests.background_helpers import wait_jobs
from tests.settings_helpers import isolate_settings
from tests.test_workflow import _window, _fit_result

APP = QApplication.instance() or QApplication([])


class ControlImprovementTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = _window(np.ones((121, 101)), gates=np.arange(121) * 1.25)
        self.w.peak_indices = np.array([50])
        self.w._peak_gate_idx = 0
        self.w.batch_peak_map = {0: np.array([50]), 1: np.array([50])}
        self.errors = []
        self.w._show_error = self.errors.append
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        wait_jobs(self.w)
        self.w.close()
        APP.processEvents()

    def type_number(self, spin, text, *, commit=True):
        spin.setFocus()
        spin.lineEdit().selectAll()
        QTest.keyClicks(spin.lineEdit(), text)
        if commit:
            QTest.keyClick(spin.lineEdit(), Qt.Key_Return)
        APP.processEvents()

    def test_linewidth_invalid_requests_preserve_results_and_seeds_on_every_path(self):
        w = self.w
        old_fit = _fit_result(10)
        w.fit_result = old_fit
        w._fit_gate_idx = 0
        w.lw_min_spin.setValue(50)
        w.lw_max_spin.setValue(10)
        w._refresh_fit_readiness()
        self.assertFalse(w.fit_btn.isEnabled())
        self.assertFalse(w.batch_fit_btn.isEnabled())
        self.assertIn('linewidth', w.fit_readiness_label.text().lower())
        with patch('spectra_app.main_window.fit_multi_peak', return_value=_fit_result(11)):
            for action in (w._on_fit, w._on_batch_fit_all_gates, w._on_detect_then_fit_current_gate):
                action()
                self.assertFalse(w._background_jobs.busy)
                self.assertIs(w.fit_result, old_fit)
                np.testing.assert_array_equal(w.peak_indices, [50])
        self.assertEqual((w.lw_min_spin.value(), w.lw_max_spin.value()), (50, 10))
        self.assertTrue(all('linewidth' in error.lower() for error in self.errors))
        w.lw_max_spin.setValue(0)
        w._refresh_fit_readiness()
        self.assertTrue(w.fit_btn.isEnabled())

    def test_invalid_detect_then_fit_does_not_detect_or_replace_seeds(self):
        w = self.w
        w.peak_indices = np.array([], dtype=int)
        w.batch_peak_map = {}
        w.lw_min_spin.setValue(50)
        w.lw_max_spin.setValue(10)
        w._on_detect_then_fit_current_gate()
        self.assertEqual(w.peak_indices.size, 0)
        self.assertTrue(any('linewidth' in error.lower() for error in self.errors))

    def test_gate_index_typing_commits_one_actual_gate_and_arrow_steps_immediately(self):
        w = self.w
        w.show()
        APP.processEvents()
        changes = []
        w.plot_gate_index_spin.valueChanged.connect(changes.append)
        self.type_number(w.plot_gate_index_spin, '120', commit=False)
        self.assertEqual(w.current_gate, 0)
        self.assertEqual(changes, [])
        QTest.keyClick(w.plot_gate_index_spin.lineEdit(), Qt.Key_Return)
        APP.processEvents()
        self.assertEqual(changes, [120])
        self.assertEqual(w.current_gate, 148.75)
        QTest.keyClick(w.plot_gate_index_spin, Qt.Key_Up)
        self.assertEqual(w.current_gate, 150)
        self.assertEqual(changes, [120, 121])

    def test_fit_bound_typing_rebuilds_once_on_focus_out(self):
        w = self.w
        w._sidebar_tabs.setCurrentWidget(w._analysis_sidebar_scroll)
        w._analysis_tabs.setCurrentIndex(1)
        w.show()
        APP.processEvents()
        with patch.object(w, '_update_spectrum_only', wraps=w._update_spectrum_only) as rebuild:
            self.type_number(w.fit_xmin_spin, '1.23', commit=False)
            self.assertEqual(rebuild.call_count, 0)
            w.fit_xmax_spin.setFocus()
            APP.processEvents()
            self.assertEqual(rebuild.call_count, 1)
        self.assertAlmostEqual(w.fit_xmin_spin.value(), 1.23)

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
            self.assertIn('Fitting', w.fit_btn.text())
            self.assertFalse(w.fit_btn.isEnabled())
            with patch.object(w, '_analysis_job_key', wraps=w._analysis_job_key) as snapshot_key:
                w._on_fit()
                self.assertEqual(snapshot_key.call_count, 0)
            self.assertEqual(len(w._background_jobs._jobs), 1)
            w._set_current_gate_from_index(1, update_plot=True)
            self.assertTrue(w.fit_btn.isEnabled())
            w._on_fit()
            self.assertEqual(len(w._background_jobs._jobs), 2)
            self.release.set()
            wait_jobs(w)
        self.assertIn(1, w.batch_fit_result_map)
        self.assertNotIn(0, w.batch_fit_result_map)
        self.assertEqual(w.fit_btn.text(), 'Fit Current Gate')
        self.assertTrue(w.fit_btn.isEnabled())

    def test_fit_error_cancel_and_setting_changes_restore_readiness(self):
        w = self.w
        def solver(*args, **kwargs):
            self.release.wait(5)
            raise ValueError('solver failure')
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            w._on_fit()
            self.assertFalse(w.fit_btn.isEnabled())
            w.lw_min_spin.setValue(12)
            self.assertTrue(w.fit_btn.isEnabled())
            w._on_fit()
            w._cancel_background_job()
            self.assertTrue(w.fit_btn.isEnabled())
            self.release.set()
            wait_jobs(w)
            w._on_fit()
            wait_jobs(w)
        self.assertTrue(w.fit_btn.isEnabled())
        self.assertNotIn('Fitting', w.fit_btn.text())
        self.assertTrue(any('solver failure' in error for error in self.errors))

    def test_single_detection_shows_local_activity_and_restores_find_action(self):
        w = self.w
        feedback = []
        def detect(*args, **kwargs):
            feedback.append((w.find_btn.text(), w.peak_validation_label.text(), w.find_btn.isEnabled()))
            return np.array([30]), np.array([True])
        with patch('spectra_app.main_window.find_peak_indices', side_effect=detect):
            w._on_find_peaks()
        self.assertIn('Detecting', feedback[0][0])
        self.assertIn('Detecting', feedback[0][1])
        self.assertFalse(feedback[0][2])
        self.assertEqual(w.find_btn.text(), 'Find Peaks (This Gate)')
        self.assertTrue(w.find_btn.isEnabled())
        np.testing.assert_array_equal(w.peak_indices, [30])

    def test_batch_activity_deduplicates_and_restores_after_cancel(self):
        w = self.w
        for action, button, path, result, word in (
            (w._on_batch_fit_all_gates, w.batch_fit_btn, 'spectra_app.main_window.fit_multi_peak',
             _fit_result(10), 'Fitting'),
            (w._on_batch_find_peaks_all_gates, w.batch_find_btn, 'spectra_app.main_window.find_peak_indices',
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
                self.assertIn(word, button.text())
                self.assertFalse(button.isEnabled())
                with patch.object(w, '_analysis_job_key', wraps=w._analysis_job_key) as snapshot_key:
                    action()
                    self.assertEqual(snapshot_key.call_count, 0)
                self.assertEqual(len(w._background_jobs._jobs), 1)
                w._cancel_background_job()
                self.assertTrue(button.isEnabled())
                self.release.set()
                wait_jobs(w)
            np.testing.assert_array_equal(w.batch_peak_map[0], [50])

    def test_processing_applied_pending_and_short_window_are_local(self):
        w = self.w
        self.assertTrue(hasattr(w, 'processing_status_label'))
        self.assertIn('Original', w.processing_status_label.text())
        self.assertFalse(w.processing_smooth_chk.isEnabled())
        self.assertFalse(w.processing_window_spin.isEnabled())
        w.current_cube = replace(w.current_cube, photon_energy=w.current_cube.energy.copy())
        w.processing_mode_combo.setCurrentIndex(1)
        self.assertIn('Generate', w.processing_status_label.text())
        self.assertTrue(w.processing_window_spin.isEnabled())
        w.processing_window_spin.setValue(201)
        self.assertIn('101', w.processing_status_label.text())
        w._on_generate_heatmap()
        self.assertIn('Generating', w.processing_status_label.text())
        wait_jobs(w)
        self.assertIn('Applied: D²', w.processing_status_label.text())
        self.assertNotIn('needs Generate', w.processing_status_label.text())
        w.processing_window_spin.setValue(21)
        self.assertIn('needs Generate', w.processing_status_label.text())
        w.processing_smooth_chk.setChecked(False)
        self.assertFalse(w.processing_window_spin.isEnabled())
        w.processing_mode_combo.setCurrentIndex(0)
        self.assertIn('Applied: Original', w.processing_status_label.text())

    def test_typed_even_sg_is_rejected_without_changing_trace_or_peaks(self):
        w = self.w
        w.current_cube = replace(w.current_cube, photon_energy=w.current_cube.energy.copy())
        before = w.current_cube
        w.processing_mode_combo.setCurrentIndex(1)
        self.type_number(w.processing_window_spin, '20')
        w._on_generate_heatmap()
        self.assertFalse(w._background_jobs.busy)
        self.assertIs(w.current_cube, before)
        self.assertTrue(hasattr(w, 'processing_status_label'))
        self.assertIn('odd', w.processing_status_label.text().lower())
        for smooth, window, actions, label in (
            (w.peak_smooth_chk, w.peak_smooth_win_spin,
             (w._on_find_peaks, w._on_batch_find_peaks_all_gates), 'peak_validation_label'),
            (w.fit_smooth_chk, w.fit_smooth_win_spin,
             (w._on_fit, w._on_batch_fit_all_gates), 'fit_readiness_label')):
            smooth.setChecked(True)
            self.type_number(window, '10')
            for action in actions:
                action()
                self.assertFalse(w._background_jobs.busy)
                np.testing.assert_array_equal(w.peak_indices, [50])
            self.assertIn('odd', getattr(w, label).text().lower())
            window.setValue(11)
            QTest.keyClick(window, Qt.Key_Up)
            self.assertEqual(window.value(), 13)

    def test_disabled_presmooth_and_d2_polyorder_effective_window(self):
        w = self.w
        self.assertFalse(w.peak_smooth_win_spin.isEnabled())
        self.assertFalse(w.fit_smooth_win_spin.isEnabled())
        w.peak_smooth_chk.setChecked(True)
        self.assertTrue(w.peak_smooth_win_spin.isEnabled())
        w._d2_radio.setChecked(True)
        w.d2_sg_win_spin.setValue(3)
        w.d2_sg_poly_spin.setValue(3)
        w._on_find_peaks()
        self.assertIn('poly', w.peak_validation_label.text().lower())
        np.testing.assert_array_equal(w.peak_indices, [50])
        w.d2_sg_win_spin.setValue(10)
        w._on_batch_find_peaks_all_gates()
        self.assertFalse(w._background_jobs.busy)

    def test_plot_steps_preserve_precise_positions_and_scientific_coordinates(self):
        page = self.w.plot_page
        p = replace(page.presentation, title_position=(.5, .8), annotations=(
            PlotAnnotation('a', position=(.5, .5)),
            PlotAnnotation('b', position=(10, 20), coordinate_space='data')))
        page.set_presentation(p)
        QTest.keyClick(page.title_x_spin, Qt.Key_Up)
        self.assertAlmostEqual(page.presentation.title_position[0], .51)
        page.select_annotation('a')
        QTest.keyClick(page.annotation_x, Qt.Key_Up)
        page._apply_annotation_edit()
        self.assertAlmostEqual(page.presentation.annotations[0].position[0], .51)
        page.select_annotation('b')
        QTest.keyClick(page.annotation_x, Qt.Key_Up)
        self.assertGreater(page.annotation_x.value(), 10)
        self.assertLess(page.annotation_x.value(), 11)
        old_width = page.width_spin.value()
        QTest.keyClick(page.width_spin, Qt.Key_Up)
        self.assertAlmostEqual(page.width_spin.value(), old_width + .05)

    def test_d2_short_trace_keeps_valid_requested_poly_and_shows_effective_clamp(self):
        w = self.w
        w._d2_radio.setChecked(True)
        w.xmax_spin.setValue(.8)
        w.d2_sg_win_spin.setValue(11)
        w.d2_sg_poly_spin.setValue(6)
        w._on_find_peaks()
        self.assertEqual(self.errors, [])
        self.assertIn('5 points', w.peak_validation_label.text())
        self.assertIn('poly 4', w.peak_validation_label.text())
        self.assertEqual(w.d2_sg_poly_spin.value(), 6)

    def test_fit_short_trace_shows_effective_window_without_changing_request(self):
        w = self.w
        w.fit_xmax_spin.setValue(1.8)
        w.fit_smooth_chk.setChecked(True)
        w.fit_smooth_win_spin.setValue(11)
        self.assertIn('9 points', w.fit_readiness_label.text())
        self.assertEqual(w.fit_smooth_win_spin.value(), 11)

    def test_annotation_data_step_uses_original_axis_spacing_after_exchange(self):
        page = self.w.plot_page
        p = replace(page.presentation, annotations=(PlotAnnotation(
            'a', position=(1.2, 14), coordinate_space='data'),))
        p = replace(p, axis=replace(p.axis, exchange_xy=True))
        snapshot = HeatmapSnapshot([1, 1.2, 1.4], [10, 14], [[1, 2, 3], [4, 5, 6]])
        page.preview.set_geometry(snapshot, p)
        page.set_presentation(p)
        QTest.keyClick(page.annotation_x, Qt.Key_Up)
        QTest.keyClick(page.annotation_y, Qt.Key_Up)
        self.assertAlmostEqual(page.annotation_x.value(), 1.4)
        self.assertAlmostEqual(page.annotation_y.value(), 18)

    def test_annotation_data_steps_refresh_when_the_formal_page_is_rendered(self):
        w = self.w
        page = w.plot_page
        w.set_plot_presentation(replace(page.presentation, annotations=(PlotAnnotation(
            'a', position=(10, 20), coordinate_space='data'),)))
        w._render_plot_page()
        self.assertIsNotNone(page.rendered_page, page.status_label.text())
        QTest.keyClick(page.annotation_x, Qt.Key_Up)
        QTest.keyClick(page.annotation_y, Qt.Key_Up)
        self.assertAlmostEqual(page.annotation_x.value(), 10.2)
        self.assertAlmostEqual(page.annotation_y.value(), 21.25)

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
        w.processing_mode_combo.setCurrentIndex(1)
        w.processing_window_spin.setValue(20)
        w.fit_smooth_chk.setChecked(True)
        w.fit_smooth_win_spin.setValue(10)
        w._load_dat_file_direct(paths[1])
        w.fit_smooth_chk.setChecked(False)
        self.assertIn('Applied: Original', w.processing_status_label.text())
        self.assertFalse(w.processing_smooth_chk.isEnabled())
        self.assertFalse(w.fit_smooth_win_spin.isEnabled())
        w.activate_dataset(first)
        self.assertIn('odd', w.processing_status_label.text())
        self.assertIn('odd', w.fit_readiness_label.text())
        self.assertTrue(w.fit_smooth_win_spin.isEnabled())
        self.assertFalse(w.fit_btn.isEnabled())

    def test_batch_export_rejects_empty_and_unusable_common_folder_in_dialog(self):
        session = DatasetSession(dataset_id='a', source_path=self.folder / 'data.dat')
        dialog = BatchExportDialog([session], 'a')
        self.addCleanup(dialog.close)
        button = dialog.buttons.button(QDialogButtonBox.Ok)
        self.assertTrue(button.isEnabled())
        dialog.common_folder.setChecked(True)
        self.assertFalse(button.isEnabled())
        self.assertTrue(dialog.folder_validation_label.text())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Rejected)
        dialog.folder_edit.setText(str(self.folder / 'missing'))
        self.assertFalse(button.isEnabled())
        self.assertFalse((self.folder / 'missing').exists())
        dialog.folder_edit.setText(str(self.folder))
        self.assertTrue(button.isEnabled())
        self.assertEqual(dialog.selected_ids(), ['a'])
        self.assertEqual(dialog.formats(), ('svg', 'png'))
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
