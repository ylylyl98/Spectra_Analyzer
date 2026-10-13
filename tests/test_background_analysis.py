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
from tests.settings_helpers import isolate_settings
from tests.test_workflow import _window, _fit_result


from tests.background_helpers import wait_jobs


class BackgroundAnalysisTests(unittest.TestCase):
    def setUp(self):
        isolate_settings(self)
        self.window = _window(np.ones((3, 101)))
        w = self.window
        w.peak_indices = np.array([50])
        w._peak_gate_idx = 0
        w.batch_peak_map = {0: np.array([50]), 1: np.array([50])}
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        wait_jobs(self.window)
        self.window.close()

    def test_single_fit_runs_off_thread_and_keeps_navigation(self):
        w = self.window
        threads = []
        applied = []
        def solver(*args, **kwargs):
            threads.append(threading.get_ident())
            self.release.wait(5)
            return _fit_result(10)
        original = w._upsert_fit_export_row
        def apply(row):
            applied.append(threading.get_ident())
            original(row)
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver), patch.object(w, '_upsert_fit_export_row', side_effect=apply):
            ticks = []
            timer = QTimer(w)
            timer.timeout.connect(lambda: ticks.append(True))
            timer.start(5)
            w._on_fit()
            deadline = time.monotonic() + 2
            while not ticks and time.monotonic() < deadline:
                QTest.qWait(10)
            timer.stop()
            self.assertTrue(w._background_jobs.busy)
            self.assertTrue(ticks)
            self.assertNotEqual(threads[0], threading.get_ident())
            w._set_current_gate_from_index(1, update_plot=True)
            self.release.set()
            wait_jobs(w)
        self.assertEqual(applied, [threading.get_ident()])
        self.assertEqual(w.current_gate, 1)
        self.assertIn(0, w.batch_fit_result_map)
        self.assertIsNone(w.fit_result)

    def test_clear_fit_discards_blocked_solver_result(self):
        w = self.window
        def solver(*args, **kwargs):
            self.release.wait(5)
            return _fit_result(10)
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            w._on_fit()
            self.assertTrue(w._background_jobs.busy)
            w._on_clear_fit()
            self.release.set()
            wait_jobs(w)
        self.assertEqual(w.batch_fit_result_map, {})
        self.assertIsNone(w.fit_result)

    def test_batch_fit_is_atomic_and_preserves_no_seed_rows(self):
        w = self.window
        with patch('spectra_app.main_window.fit_multi_peak', return_value=_fit_result(10)):
            w._on_batch_fit_all_gates()
            self.assertTrue(w._background_jobs.busy)
            wait_jobs(w)
        self.assertEqual(set(w.batch_fit_result_map), {0, 1})
        self.assertEqual([r['status'] for r in w._batch_fit_export_rows], ['ok', 'ok', 'no_seeds'])

    def test_cancel_batch_peak_detection_preserves_existing_peaks(self):
        w = self.window
        started = threading.Event()
        def detect(*args, **kwargs):
            started.set()
            self.release.wait(5)
            return np.array([20]), np.array([True])
        with patch('spectra_app.main_window.find_peak_indices', side_effect=detect):
            w._on_batch_find_peaks_all_gates()
            self.assertTrue(started.wait(2))
            self.assertTrue(w._background_jobs.busy)
            w._background_jobs.cancel()
            self.release.set()
            wait_jobs(w)
        np.testing.assert_array_equal(w.batch_peak_map[0], [50])
        np.testing.assert_array_equal(w.current_cube.z, np.ones((3, 101)))

    def test_replaced_data_discards_blocked_fit_result(self):
        w = self.window
        started = threading.Event()
        def solver(*args, **kwargs):
            started.set()
            self.release.wait(5)
            return _fit_result(10)
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            w._on_fit()
            self.assertTrue(started.wait(2))
            w.current_cube = replace(w.current_cube, z=np.full((3, 101), 7.0))
            w._data_generation += 1
            self.release.set()
            wait_jobs(w)
        self.assertEqual(w.batch_fit_result_map, {})
        np.testing.assert_array_equal(w.current_cube.z, np.full((3, 101), 7.0))

    def test_deleting_seeds_discards_blocked_fit_result(self):
        w = self.window
        started = threading.Event()
        def solver(*args, **kwargs):
            started.set()
            self.release.wait(5)
            return _fit_result(10)
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            w._on_fit()
            self.assertTrue(started.wait(2))
            w._on_clear_peaks()
            self.release.set()
            wait_jobs(w)
        self.assertEqual(w.batch_fit_result_map, {})
        self.assertEqual(w.peak_indices.size, 0)

    def test_batch_detection_reports_failed_and_empty_gates_and_applies_atomically(self):
        w = self.window
        threads = []
        progress = []
        def detect(*args, **kwargs):
            threads.append(threading.get_ident())
            if len(threads) == 1:
                raise ValueError('bad trace')
            if len(threads) == 2:
                return np.array([], dtype=int), np.array([], dtype=bool)
            return np.array([20]), np.array([False])
        with patch('spectra_app.main_window.find_peak_indices', side_effect=detect), patch.object(w.statusBar(), 'showMessage', side_effect=lambda message: progress.append(message)):
            w._on_batch_find_peaks_all_gates()
            np.testing.assert_array_equal(w.batch_peak_map[0], [50])
            wait_jobs(w)
        self.assertTrue(all(thread != threading.get_ident() for thread in threads))
        self.assertEqual(set(w.batch_peak_map), {2})
        np.testing.assert_array_equal(w.batch_peak_map[2], [20])
        self.assertFalse(w.batch_peak_kind_lookup[(2, 20)])
        for gate in range(1, 4):
            self.assertTrue(any(f'gate {gate}/3' in message for message in progress))

    def test_clear_suppresses_late_solver_error(self):
        w = self.window
        started = threading.Event()
        errors = []
        def solver(*args, **kwargs):
            started.set()
            self.release.wait(5)
            raise ValueError('late failure')
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver), patch.object(w, '_show_error', side_effect=errors.append):
            w._on_fit()
            self.assertTrue(started.wait(2))
            w._on_clear_fit()
            self.release.set()
            wait_jobs(w)
        self.assertEqual(errors, [])

    def test_seed_sync_without_changes_does_not_discard_fit(self):
        w = self.window
        started = threading.Event()
        def solver(*args, **kwargs):
            started.set()
            self.release.wait(5)
            return _fit_result(10)
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            w._on_fit()
            self.assertTrue(started.wait(2))
            w._sync_current_trace_peaks_to_batch_map()
            self.release.set()
            wait_jobs(w)
        self.assertIn(0, w.batch_fit_result_map)

    def test_settings_change_discards_snapshot_but_worker_uses_original_settings(self):
        w = self.window
        started = threading.Event()
        captured = []
        def solver(*args, **kwargs):
            captured.append(kwargs['maxfev'])
            started.set()
            self.release.wait(5)
            return _fit_result(10)
        original_maxfev = w.maxfev_spin.value()
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            w._on_batch_fit_all_gates()
            self.assertTrue(started.wait(2))
            w.maxfev_spin.setValue(original_maxfev + 1)
            self.release.set()
            wait_jobs(w)
        self.assertEqual(captured, [original_maxfev])
        self.assertEqual(w.batch_fit_result_map, {})

    def test_invalidated_batch_stops_after_current_gate(self):
        w = self.window
        for change in ('clear', 'fit_range', 'search_range', 'seeds', 'fit_full', 'use_view', 'toolbar_range'):
            with self.subTest(change=change):
                w.batch_peak_map = {0: np.array([50]), 1: np.array([50])}
                w.peak_indices = np.array([50])
                w._peak_gate_idx = 0
                self.release.clear()
                started, calls = threading.Event(), []
                def solver(*args, **kwargs):
                    calls.append(True)
                    started.set()
                    self.release.wait(2)
                    return _fit_result(10)
                with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
                    w._on_batch_fit_all_gates()
                    self.assertTrue(started.wait(1))
                    if change == 'clear':
                        w._on_clear_fit()
                    elif change == 'fit_range':
                        w.fit_xmin_spin.setValue(w.fit_xmin_spin.value()+.1)
                    elif change == 'search_range':
                        w.xmin_spin.setValue(w.xmin_spin.value()+.1)
                    elif change == 'fit_full':
                        w._on_set_fit_full_range()
                    elif change == 'use_view':
                        w._on_use_current_x_range()
                    elif change == 'toolbar_range':
                        w._heat_ax.set_xlim(w.xmin_spin.value()+.1, w.xmax_spin.value())
                    else:
                        w._on_clear_peaks()
                    self.release.set()
                    wait_jobs(w)
                self.assertEqual(len(calls), 1)
                self.assertEqual(w.batch_fit_result_map, {})

    def test_analysis_input_edits_do_not_cancel_unrelated_background_work(self):
        w = self.window
        started, applied = threading.Event(), []
        def work(context):
            started.set()
            self.release.wait(2)
            context.check_cancelled()
            return 7
        w._start_background_job(work, applied.append, 'Folder discovery')
        self.assertTrue(started.wait(1))
        with patch.object(w, '_analysis_job_key', side_effect=AssertionError('unnecessary seed hashing')):
            w.maxfev_spin.setValue(w.maxfev_spin.value()+1)
            w.plot_title_edit.setText('new title')
            w._set_current_gate_from_index(1, update_plot=True)
            w._on_clear_fit()
        self.release.set()
        wait_jobs(w)
        self.assertEqual(applied, [7])

    def test_cancel_running_batch_fit_keeps_previous_results(self):
        w = self.window
        old = _fit_result(5)
        w.batch_fit_result_map = {2: old}
        w.batch_fit_map = {2: old.centers.copy()}
        w._batch_fit_export_rows = [{'gate': '2', 'status': 'ok'}]
        started = threading.Event()
        def solver(*args, **kwargs):
            started.set()
            self.release.wait(5)
            return _fit_result(10)
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver) as solve:
            w._on_batch_fit_all_gates()
            self.assertTrue(started.wait(2))
            w._cancel_background_job()
            self.release.set()
            wait_jobs(w)
            self.assertEqual(solve.call_count, 1)
        self.assertEqual(w.batch_fit_result_map, {2: old})
        self.assertEqual(w._batch_fit_export_rows, [{'gate': '2', 'status': 'ok'}])

    def test_new_fit_request_supersedes_running_result(self):
        w = self.window
        started = threading.Event()
        calls = []
        def solver(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                started.set()
                self.release.wait(5)
                return _fit_result(5)
            return _fit_result(10)
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
            w._on_fit()
            self.assertTrue(started.wait(2))
            # An explicitly changed request still supersedes the running fit;
            # repeated clicks with identical inputs are now ignored by the UI.
            w.lw_min_spin.setValue(12)
            w._on_fit()
            self.release.set()
            wait_jobs(w)
        np.testing.assert_array_equal(w.batch_fit_result_map[0].centers, [10])
