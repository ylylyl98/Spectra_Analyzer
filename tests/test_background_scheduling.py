import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication

from spectra_app.background_jobs import BackgroundJobs, CancelledError, JobContext, _Job
from spectra_app.processing import energy_second_derivative


APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.005)
    assert predicate(), 'Background operation timed out'


class BackgroundSchedulingTests(unittest.TestCase):
    def test_replacements_run_one_at_a_time_and_only_latest_pending_runs(self):
        jobs = BackgroundJobs()
        started, release = threading.Event(), threading.Event()
        executions, results, idle = [], [], []
        launched = []
        original_start = QThread.start
        jobs.idle.connect(lambda: idle.append(jobs.busy))

        def launch(job):
            launched.append(job)
            original_start(job)

        def first(context):
            executions.append('first')
            started.set()
            release.wait(5)
            return 'first'

        try:
            with patch.object(_Job, 'start', launch):
                jobs.start(first, results.append)
                self.assertTrue(started.wait(1))
                for index in range(6):
                    def replacement(context, index=index):
                        executions.append(index)
                        return index
                    jobs.start(replacement, results.append)
                self.assertEqual(len(launched), 1)
                APP.processEvents()
                self.assertEqual(executions, ['first'])
                self.assertTrue(jobs.busy)
                self.assertEqual(idle, [])
                release.set()
                wait_until(lambda: not jobs.busy)
                self.assertEqual(len(launched), 2)
            self.assertEqual(executions, ['first', 5])
            self.assertEqual(results, [5])
            self.assertEqual(idle, [False])
        finally:
            jobs.cancel()
            release.set()
            wait_until(lambda: not jobs.busy)

    def test_cancel_discards_pending_and_waits_responsively_for_running_work(self):
        jobs = BackgroundJobs()
        started, release = threading.Event(), threading.Event()
        seen = []
        jobs.idle.connect(lambda: seen.append('idle'))

        def blocked(context):
            started.set()
            release.wait(5)
            return 'stale result'

        try:
            jobs.start(blocked, seen.append)
            self.assertTrue(started.wait(1))
            jobs.start(lambda context: seen.append('pending executed'), seen.append)
            jobs.cancel()
            QTimer.singleShot(0, lambda: seen.append('timer'))
            wait_until(lambda: 'timer' in seen)
            self.assertTrue(jobs.busy)
            self.assertEqual(seen, ['timer'])
            release.set()
            wait_until(lambda: not jobs.busy)
            self.assertEqual(seen, ['timer', 'idle'])
        finally:
            jobs.cancel()
            release.set()
            wait_until(lambda: not jobs.busy)

    def test_queued_stale_progress_and_error_do_not_reach_callbacks(self):
        jobs = BackgroundJobs()
        started, release = threading.Event(), threading.Event()
        seen = []

        def stale(context):
            context.report('stale progress')
            started.set()
            release.wait(5)
            raise ValueError('stale error')

        try:
            jobs.start(stale, seen.append, on_error=lambda exc: seen.append(str(exc)),
                       on_progress=seen.append)
            self.assertTrue(started.wait(1))
            jobs.start(lambda context: 'latest', seen.append)
            release.set()
            wait_until(lambda: not jobs.busy)
            self.assertEqual(seen, ['latest'])
        finally:
            jobs.cancel()
            release.set()
            wait_until(lambda: not jobs.busy)

    def test_error_callback_is_delivered_on_gui_thread(self):
        jobs = BackgroundJobs()
        seen = []

        def fail(context):
            raise ValueError('failed')

        jobs.start(fail, seen.append,
                   on_error=lambda exc: seen.append((str(exc), QThread.currentThread() == APP.thread())))
        wait_until(lambda: not jobs.busy)
        self.assertEqual(seen, [('failed', True)])

    def test_superseded_result_already_queued_for_gui_is_discarded(self):
        jobs = BackgroundJobs()
        seen = []
        jobs.start(lambda context: 'stale', seen.append)
        # The worker has emitted its result, but the GUI has not processed it.
        self.assertTrue(jobs._current.wait(1000))
        jobs.start(lambda context: 'latest', seen.append)
        wait_until(lambda: not jobs.busy)
        self.assertEqual(seen, ['latest'])


class ProcessingCancellationTests(unittest.TestCase):
    def test_smoothed_derivative_stops_between_coordinate_fits(self):
        cancelled = threading.Event()
        context = JobContext(cancelled, lambda message: None)
        original = np.linalg.lstsq
        calls = []

        def fit(*args, **kwargs):
            calls.append(1)
            if len(calls) == 4:
                cancelled.set()
            return original(*args, **kwargs)

        energy = np.linspace(1, 2, 100)
        with patch('spectra_app.processing.np.linalg.lstsq', side_effect=fit):
            with self.assertRaises(CancelledError):
                energy_second_derivative(energy, np.array([energy**2]),
                                         check_cancelled=context.check_cancelled)
        self.assertEqual(len(calls), 4)

    def test_derivative_checks_cancel_before_starting_numerical_work(self):
        cancelled = threading.Event()
        cancelled.set()
        context = JobContext(cancelled, lambda message: None)
        energy = np.linspace(1, 2, 100)
        for smooth in (False, True):
            with self.subTest(smooth=smooth), patch('spectra_app.processing.np.linalg.lstsq') as fit:
                with self.assertRaises(CancelledError):
                    energy_second_derivative(energy, np.array([energy**2]), smooth=smooth,
                                             check_cancelled=context.check_cancelled)
                fit.assert_not_called()
