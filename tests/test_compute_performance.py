"""Observable numerical reuse and cancellable GUI work contracts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from spectra_app import main_window, fitting
from spectra_app.processing import energy_second_derivative
from tests.background_helpers import wait_jobs
from tests.settings_helpers import isolate_settings
from tests.test_workflow import _window, _fit_result


class ComputePerformanceTests(unittest.TestCase):
    def setUp(self):
        isolate_settings(self)
        x = np.linspace(0., 20., 101)
        self.w = _window(np.vstack([np.sin(x), x*x, x*x*x]))
        self.w.current_cube = replace(self.w.current_cube, photon_energy=x.copy())
        self.w._base_cube = self.w.current_cube
        self.w._show_error = lambda message: self.errors.append(message)
        self.errors = []
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        wait_jobs(self.w)
        self.w.close()

    def request(self):
        self.w.processing_mode_combo.setCurrentIndex(1)
        self.w._on_generate_heatmap()

    def test_identical_applied_recipe_keeps_cube_results_and_skips_compute_plot(self):
        w = self.w
        self.request()
        wait_jobs(w)
        cube = w.current_cube
        old = _fit_result(4)
        w.fit_result, w._fit_gate_idx = old, 0
        w.batch_fit_result_map = {0: old}
        w.peak_indices, w._peak_gate_idx = np.array([20]), 0
        with patch.object(main_window, 'energy_second_derivative', wraps=energy_second_derivative) as compute, \
             patch.object(w, '_plot_cube', wraps=w._plot_cube) as plot:
            w._on_generate_heatmap()
            wait_jobs(w)
            self.assertEqual(compute.call_count, 0)
            self.assertEqual(plot.call_count, 0)
        self.assertIs(w.current_cube, cube)
        self.assertIs(w.fit_result, old)
        self.assertIs(w.batch_fit_result_map[0], old)
        np.testing.assert_equal(w.peak_indices, [20])

    def test_processing_runs_off_thread_deduplicates_pending_and_keeps_latest_view(self):
        w = self.w
        started, threads = threading.Event(), []
        original = w.current_cube
        def compute(*args, **kwargs):
            threads.append(threading.get_ident())
            started.set()
            self.release.wait(2)
            return energy_second_derivative(*args, **kwargs)
        ticks = []
        timer = QTimer(w)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(5)
        with patch.object(main_window, 'energy_second_derivative', side_effect=compute) as derivative:
            self.request()
            self.assertTrue(started.wait(1))
            self.assertTrue(w._background_jobs.busy)
            self.assertIs(w.current_cube, original)
            w._on_generate_heatmap()
            QTest.qWait(30)
            self.assertTrue(ticks)
            self.assertNotEqual(threads[0], threading.get_ident())
            w._set_current_gate_from_index(2, update_plot=True)
            w.xmin_spin.setValue(3.)
            w.maxfev_spin.setValue(w.maxfev_spin.value()+1)
            w.plot_title_edit.setText('Latest title')
            self.release.set()
            wait_jobs(w)
            self.assertEqual(derivative.call_count, 1)
        timer.stop()
        self.assertEqual(w._processing_mode, 'energy_second_derivative')
        self.assertEqual(w.current_gate, 2.)
        self.assertEqual(w._heat_ax.get_xlim()[0], 3.)
        self.assertEqual(w._heat_ax.get_title(), 'Latest title')
        np.testing.assert_allclose(w.current_cube.z, energy_second_derivative(original.photon_energy, original.z))

    def test_original_or_recipe_edit_cancels_pending_and_keeps_previous_results(self):
        w = self.w
        for change in ('original', 'recipe', 'cancel', 'data'):
            with self.subTest(change=change):
                self.release.clear()
                started = threading.Event()
                original = w.current_cube
                old = _fit_result(4)
                w.fit_result, w._fit_gate_idx = old, 0
                def compute(*args, **kwargs):
                    started.set()
                    self.release.wait(2)
                    return energy_second_derivative(*args, **kwargs)
                with patch.object(main_window, 'energy_second_derivative', side_effect=compute):
                    self.request()
                    self.assertTrue(started.wait(1))
                    self.assertTrue(w._background_jobs.busy)
                    if change == 'original':
                        w.processing_mode_combo.setCurrentIndex(0)
                    elif change == 'recipe':
                        w.processing_window_spin.setValue(w.processing_window_spin.value()+2)
                    elif change == 'cancel':
                        w._cancel_background_job()
                    else:
                        w._data_generation += 1
                    self.release.set()
                    wait_jobs(w)
                self.assertIs(w.current_cube, original)
                self.assertIs(w.fit_result, old)
        self.assertEqual(self.errors, [])

    def test_processing_failure_keeps_cube_and_results(self):
        w = self.w
        original = w.current_cube
        old = _fit_result(4)
        w.fit_result = old
        with patch.object(main_window, 'energy_second_derivative', side_effect=ValueError('numeric failure')):
            self.request()
            wait_jobs(w)
        self.assertIs(w.current_cube, original)
        self.assertIs(w.fit_result, old)
        self.assertEqual(self.errors, ['numeric failure'])

    def test_dataset_switch_is_guarded_and_cancel_rejects_processing_result_and_error(self):
        w = self.w
        folder = isolate_settings(self).parent
        ids = []
        for name in ('a', 'b'):
            path = folder / (name + '.dat')
            path.write_text('Energy(eV) 0 1\n' + '\n'.join(
                f'{x} {x*x} {x*x*x}' for x in np.linspace(1., 4., 21)))
            w._load_dat_file_direct(path)
            ids.append(w.workspace.active_dataset_id)
        for fails in (False, True):
            with self.subTest(fails=fails):
                w.activate_dataset(ids[0])
                original = w.current_cube
                old = _fit_result(2.)
                w.fit_result, w._fit_gate_idx = old, 0
                self.release.clear()
                started = threading.Event()
                def compute(*args, **kwargs):
                    started.set()
                    self.release.wait(2)
                    if fails:
                        raise ValueError('stale derivative failure')
                    return energy_second_derivative(*args, **kwargs)
                with patch.object(main_window, 'energy_second_derivative', side_effect=compute):
                    self.request()
                    self.assertTrue(started.wait(1))
                    with self.assertRaisesRegex(ValueError, 'current operation'):
                        w.activate_dataset(ids[1])
                    self.assertEqual(w.workspace.active_dataset_id, ids[0])
                    w._cancel_background_job()
                    self.release.set()
                    wait_jobs(w)
                    w.activate_dataset(ids[1])
                    w.activate_dataset(ids[0])
                self.assertIs(w.current_cube, original)
                self.assertIs(w.fit_result, old)
        self.assertEqual(self.errors, [])

    def test_changed_recipe_supersedes_pending_derivative(self):
        w = self.w
        started, calls = threading.Event(), []
        def compute(*args, **kwargs):
            calls.append(kwargs['window_length'])
            if len(calls) == 1:
                started.set()
                self.release.wait(2)
            return energy_second_derivative(*args, **kwargs)
        with patch.object(main_window, 'energy_second_derivative', side_effect=compute):
            self.request()
            self.assertTrue(started.wait(1))
            w.processing_window_spin.setValue(9)
            w._on_generate_heatmap()
            self.release.set()
            wait_jobs(w)
        self.assertEqual(calls, [21, 9])
        self.assertEqual(w._processing_window_length, 9)
        np.testing.assert_allclose(w.current_cube.z, energy_second_derivative(
            w._base_cube.photon_energy, w._base_cube.z, window_length=9))

    def test_cancelled_axis_attempt_can_restart_identical_derivative(self):
        w = self.w
        started, calls = threading.Event(), []
        def compute(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                started.set()
                self.release.wait(2)
            return energy_second_derivative(*args, **kwargs)
        with patch.object(main_window, 'energy_second_derivative', side_effect=compute):
            self.request()
            self.assertTrue(started.wait(1))
            w._on_x_axis_override_changed()  # No source path: the attempt cancels and returns.
            w._on_generate_heatmap()
            self.release.set()
            wait_jobs(w)
        self.assertEqual(len(calls), 2)
        self.assertEqual(w._processing_mode, 'energy_second_derivative')

    def test_changed_base_requires_recompute_after_applied_recipe(self):
        w = self.w
        self.request()
        wait_jobs(w)
        w._base_cube = replace(w._base_cube, z=w._base_cube.z * 2)
        with patch.object(main_window, 'energy_second_derivative', wraps=energy_second_derivative) as compute:
            w._on_generate_heatmap()
            wait_jobs(w)
        self.assertEqual(compute.call_count, 1)
        np.testing.assert_allclose(w.current_cube.z, energy_second_derivative(
            w._base_cube.photon_energy, w._base_cube.z))

    def test_reloaded_processed_session_keeps_provenance_for_identical_generate(self):
        w = self.w
        folder = isolate_settings(self).parent
        path = folder / 'reload.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(
            f'{x} {x*x} {x*x*x}' for x in np.linspace(1., 4., 21)))
        w._load_dat_file_direct(path)
        owner = w.workspace.active_dataset_id
        self.request()
        wait_jobs(w)
        w._load_dat_file_direct(path)
        self.assertEqual(w.workspace.active_dataset_id, owner)
        self.assertEqual(w._processing_mode, 'energy_second_derivative')
        cube = w.current_cube
        old = _fit_result(2.)
        w.fit_result, w._fit_gate_idx = old, 0
        w.peak_indices, w._peak_gate_idx = np.array([4]), 0
        with patch.object(main_window, 'energy_second_derivative', wraps=energy_second_derivative) as compute, \
             patch.object(w, '_plot_cube', wraps=w._plot_cube) as plot:
            w._on_generate_heatmap()
            wait_jobs(w)
        self.assertEqual(compute.call_count, 0)
        self.assertEqual(plot.call_count, 0)
        self.assertIs(w.current_cube, cube)
        self.assertIs(w.fit_result, old)
        np.testing.assert_equal(w.peak_indices, [4])

    def test_restored_processed_source_replacement_recomputes_before_first_generate(self):
        w = self.w
        folder = isolate_settings(self).parent
        path = folder / 'restored.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(
            f'{x} {x*x} {x*x*x}' for x in np.linspace(1., 4., 21)))
        w._load_dat_file_direct(path)
        self.request()
        wait_jobs(w)
        w._save_current_plot_state()
        restored = main_window.MainWindow()
        restored._show_error = self.fail
        try:
            restored._load_dat_file_direct(path)
            self.assertEqual(restored._processing_mode, 'energy_second_derivative')
            restored._base_cube = replace(restored._base_cube, z=restored._base_cube.z * 3)
            with patch.object(main_window, 'energy_second_derivative', wraps=energy_second_derivative) as compute:
                restored._on_generate_heatmap()
                wait_jobs(restored)
            self.assertEqual(compute.call_count, 1)
            np.testing.assert_allclose(restored.current_cube.z, energy_second_derivative(
                restored._base_cube.photon_energy, restored._base_cube.z))
        finally:
            wait_jobs(restored)
            restored.close()

    def test_detection_derivative_reused_by_overlay_and_refresh_until_inputs_change(self):
        w = self.w
        w._d2_radio.setChecked(True)
        w.d2_sg_win_spin.setValue(11)
        x, y = w.current_cube.energy, w.current_cube.z[0]
        with patch.object(main_window, 'savgol_filter', wraps=main_window.savgol_filter) as overlay, \
             patch.object(fitting, 'savgol_filter', wraps=fitting.savgol_filter) as detect:
            w._on_find_peaks()
            self.assertEqual(detect.call_count, 1)
            self.assertEqual(overlay.call_count, 0)
            for _ in range(3):
                w._update_spectrum_only()
            self.assertEqual(overlay.call_count, 0)
            xr, actual = w._current_second_derivative_trace(x, y)
            expected = fitting.find_peaks_second_derivative(
                xr, y[w._peak_search_mask(x)], sg_window=w.d2_sg_win_spin.value(),
                sg_polyorder=w.d2_sg_poly_spin.value())[2]
            np.testing.assert_allclose(actual, expected)
            w.d2_sg_win_spin.setValue(13)
            w._current_second_derivative_trace(x, y)
            self.assertEqual(overlay.call_count, 1)
            w.xmin_spin.setValue(2.)
            w._current_second_derivative_trace(x, y)
            self.assertEqual(overlay.call_count, 2)
            w._set_current_gate_from_index(1, update_plot=True)
            self.assertEqual(overlay.call_count, 3)
            w._data_generation += 1
            w._current_second_derivative_trace(x, w.current_cube.z[1])
            self.assertEqual(overlay.call_count, 4)

