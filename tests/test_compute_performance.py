"""Observable numerical reuse and cancellable GUI work contracts."""
from tests import dat_properties_quick_helpers as dp
from tests import ordinary_analysis_quick_helpers as oa
from tests.quick_helpers import set_processing_window
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import threading
import time
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from spectra_app import main_window, fitting
from spectra_app.processing import energy_second_derivative
from tests.background_helpers import wait_jobs
from tests import test_workflow as workflow
from tests.scientific_fixtures import workflow_fit_result as _fit_result
from tests.quick_migration_case import QuickMigrationCase


class ComputePerformanceTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        x = np.linspace(0., 20., 101)
        self.w = workflow.WorkflowTests.make_window(self, np.vstack([np.sin(x), x*x, x*x*x]))
        self.w.current_cube = replace(self.w.current_cube, photon_energy=x.copy())
        self.w._base_cube = self.w.current_cube
        self.w._show_error = lambda message: self.errors.append(message)
        self.errors = []
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        wait_jobs(self.w)

    def request(self):
        self.w.processing.setMode('energy_second_derivative')
        self.w._on_generate_heatmap()

    def test_identical_applied_recipe_keeps_cube_results_and_skips_compute_plot(self):
        w = self.w
        self.request()
        wait_jobs(w)
        # Historical name retained: enabled explicit Apply now creates another
        # result graph while reusing cached immutable numerical buffers.
        session = w.workspace.active
        source = session.active_graph
        cube = w.current_cube
        self.assertIsNotNone(w._open_analysis(('heatmap', (session.dataset_id, source.graph_id))))
        analysis = session.active_graph
        science = analysis.analysis.science
        bound_cube = science.current_cube
        np.testing.assert_array_equal(bound_cube.z, cube.z)
        old = _fit_result(4)
        w.fit_result, w._fit_gate_idx = old, 0
        w.batch_fit_result_map = {0: old}
        w.peak_indices, w._peak_gate_idx = np.array([20]), 0
        w.open_graph_document(session.dataset_id, source.graph_id)
        self.assertTrue(w.processing.applyEnabled)
        previous_results = set(session.processing_results)
        with patch.object(main_window, 'energy_second_derivative', wraps=energy_second_derivative) as compute, \
             patch.object(w, '_plot_cube', wraps=w._plot_cube) as plot:
            w._on_generate_heatmap()
            wait_jobs(w)
            self.assertEqual(compute.call_count, 0)
            self.assertEqual(plot.call_count, 1)
        result_graph = session.active_graph
        self.assertIsNot(result_graph, source)
        self.assertNotEqual(result_graph.result_id, source.result_id)
        self.assertEqual(set(session.processing_results) - previous_results, {result_graph.result_id})
        self.assertIs(session.processing_results[source.result_id].cube, cube)
        self.assertIs(session.processing_results[result_graph.result_id].cube, w.current_cube)
        self.assertIsNot(w.current_cube, cube)
        self.assertIs(w.current_cube.z, cube.z)
        np.testing.assert_array_equal(w.current_cube.z, cube.z)
        self.assertEqual(w.current_cube.source_path, cube.source_path)
        self.assertIs(science.current_cube, bound_cube)
        np.testing.assert_array_equal(science.current_cube.z, cube.z)
        self.assertIs(science.fit_result, old)
        self.assertIs(science.batch_fit_result_map[0], old)
        np.testing.assert_equal(science.peak_indices, [20])

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
            dp.setup_number(w, 'xmin', 3.)
            oa.set_number(w, 'maxfev', oa.value(w, 'maxfev')+1)
            w.dat_properties.setLabel('plot_title', 'Latest title', w.dat_properties.ownerToken)
            self.release.set()
            wait_jobs(w)
            self.assertEqual(derivative.call_count, 1)
        timer.stop()
        self.assertEqual(w._processing_mode, 'energy_second_derivative')
        self.assertEqual(w.current_gate, 2.)
        self.assertEqual(w._heat_ax.get_xlim()[0], 3.)
        self.assertEqual(w._heat_ax.get_title(), 'Latest title')
        np.testing.assert_allclose(w.current_cube.z, energy_second_derivative(original.photon_energy, original.z))

    def test_backend_recipe_change_or_cancel_retires_pending_and_keeps_previous_results(self):
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
                    # The Quick editor is deliberately disabled during a job.
                    # Backend owner changes still invalidate stale completion.
                    if change == 'original':
                        owner = w.processing.owner()
                        owner.pending = replace(owner.pending, mode='original')
                        w.processing.syncFromOwner()
                    elif change == 'recipe':
                        owner = w.processing.owner()
                        owner.pending = replace(owner.pending, window_length=owner.pending.window_length + 2)
                        w.processing.syncFromOwner()
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
        folder = self.folder
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

    def test_cancel_then_changed_recipe_replaces_pending_derivative(self):
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
            w._cancel_background_job()
            self.release.set()
            wait_jobs(w)
            set_processing_window(w.processing, 9)
            w._on_generate_heatmap()
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
            w.current_dat_path = None  # Preserve the no-source cancellation boundary.
            w._on_x_axis_override_changed()
            self.release.set()
            wait_jobs(w)
            w._on_generate_heatmap()
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

    def test_reloaded_processed_session_retains_previous_run_on_identical_generate(self):
        w = self.w
        folder = self.folder
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
        self.assertEqual(plot.call_count, 1)
        self.assertIsNot(w.current_cube, cube)
        self.assertTrue(any(result.cube is cube for result in w.workspace.active.processing_results.values()))
        np.testing.assert_array_equal(w.current_cube.z, cube.z)
        self.assertIsNone(w.fit_result)
        self.assertEqual(w.peak_indices.size, 0)

    def test_restored_processed_source_replacement_recomputes_before_first_generate(self):
        w = self.w
        folder = self.folder
        path = folder / 'restored.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(
            f'{x} {x*x} {x*x*x}' for x in np.linspace(1., 4., 21)))
        w._load_dat_file_direct(path)
        self.request()
        wait_jobs(w)
        w._save_current_plot_state()
        from tests.quick_application_helpers import application
        restored_host, _, _ = self.enterContext(application())
        restored = restored_host.controller
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

    def test_detection_derivative_reused_by_overlay_and_refresh_until_inputs_change(self):
        w = self.w
        oa.set_value(w, 'derivative_detection', True)
        oa.set_number(w, 'd2_window', 11)
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
                xr, y[w._peak_search_mask(x)], sg_window=oa.value(w, 'd2_window'),
                sg_polyorder=oa.value(w, 'd2_polyorder'))[2]
            np.testing.assert_allclose(actual, expected)
            oa.set_number(w, 'd2_window', 13)
            w._current_second_derivative_trace(x, y)
            self.assertEqual(overlay.call_count, 1)
            dp.setup_number(w, 'xmin', 2.)
            w._current_second_derivative_trace(x, y)
            self.assertEqual(overlay.call_count, 2)
            w._set_current_gate_from_index(1, update_plot=True)
            self.assertEqual(overlay.call_count, 3)
            w._data_generation += 1
            w._current_second_derivative_trace(x, w.current_cube.z[1])
            self.assertEqual(overlay.call_count, 4)

