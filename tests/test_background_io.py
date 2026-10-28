import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QCoreApplication, QThread, QTimer

from spectra_app import background_jobs
from tests import dat_properties_quick_helpers as dp
from tests import analysis_quick_helpers as aq
from tests.quick_migration_case import QuickMigrationCase


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert predicate(), "Background job did not finish"


class BackgroundJobsTests(QuickMigrationCase):
    def test_work_is_off_thread_but_result_and_progress_are_on_gui_thread(self):
        jobs = background_jobs.BackgroundJobs()
        seen = []
        def work(context):
            context.report("Working")
            return QThread.currentThread() == QCoreApplication.instance().thread()
        jobs.start(work, lambda value: seen.append((value, QThread.currentThread() == QCoreApplication.instance().thread())),
                   on_progress=lambda value: seen.append((value, QThread.currentThread() == QCoreApplication.instance().thread())))
        wait_until(lambda: not jobs.busy)
        self.assertEqual(seen, [("Working", True), (False, True)])

    def test_superseded_result_and_error_do_not_apply(self):
        jobs = background_jobs.BackgroundJobs()
        release = threading.Event()
        started = threading.Event()
        seen = []
        def old(context):
            started.set()
            release.wait(2)
            raise ValueError("stale error")
        jobs.start(old, seen.append, on_error=lambda exc: seen.append(str(exc)))
        self.assertTrue(started.wait(1))
        jobs.start(lambda context: "new", seen.append)
        release.set()
        wait_until(lambda: not jobs.busy)
        self.assertEqual(seen, ["new"])

    def test_cancel_keeps_event_loop_responsive_and_discards_result(self):
        jobs = background_jobs.BackgroundJobs()
        seen = []
        started = threading.Event()
        def work(context):
            started.set()
            while True:
                context.check_cancelled()
                time.sleep(0.005)
        jobs.start(work, seen.append)
        self.assertTrue(started.wait(1))
        QTimer.singleShot(0, lambda: seen.append("timer"))
        jobs.cancel()
        wait_until(lambda: not jobs.busy)
        self.assertEqual(seen, ["timer"])


class BackgroundIOTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.directory = self.folder
        self.window = self.controller
        self.errors = self.quick_errors
        self.path = self.directory / "sample.dat"
        self.path.write_text("Energy(eV) 0 1\n1.0 1 2\n1.1 2 3\n1.2 3 4\n", encoding="utf-8")

    def tearDown(self):
        if hasattr(self.window, "_background_jobs"):
            self.window._background_jobs.cancel()
            wait_until(lambda: not self.window._background_jobs.busy)

    def test_async_load_keeps_timer_running_and_matches_direct_data(self):
        self.assertTrue(hasattr(self.window, "_load_dat_file_async"))
        original = background_jobs.load_dat_cube
        threads = []
        def slow_load(*args, **kwargs):
            threads.append(QThread.currentThread() == QCoreApplication.instance().thread())
            time.sleep(0.05)
            return original(*args, **kwargs)
        ticks = []
        with patch.object(background_jobs, "load_dat_cube", slow_load):
            self.window._load_dat_file_async(self.path)
            QTimer.singleShot(0, lambda: ticks.append(self.window.current_cube is None))
            wait_until(lambda: not self.window._background_jobs.busy)
        self.assertEqual(threads, [False])
        self.assertEqual(ticks, [True])
        self.assertEqual(self.window.current_dat_path, self.path)
        self.assertEqual(self.window.current_cube.z.tolist(), original(self.path).z.tolist())
        self.assertFalse(self.errors)

    def test_close_waits_for_worker_without_delivering_result(self):
        self.assertTrue(hasattr(self.window, "_start_background_job"))
        release = threading.Event()
        started = threading.Event()
        seen = []
        def work(context):
            started.set()
            release.wait(2)
            return "result"
        self.host.show()
        self.window._start_background_job(work, seen.append, "Working")
        self.assertTrue(started.wait(1))
        self.window._start_background_job(lambda context: seen.append('pending executed'),
                                          seen.append, "Replacement")
        with patch.object(self.window, '_confirm_project_discard_async', lambda callback: callback()):
            self.assertFalse(self.host.window.close())
            self.assertTrue(self.host.window.isVisible())
            release.set()
            wait_until(lambda: not self.host.window.isVisible())
        self.assertFalse(seen)

    def test_axis_override_is_background_and_preserves_selected_gate(self):
        self.window._load_dat_file_direct(self.path)
        self.window.current_gate = 1.0
        dp.set_choice(self.window, 'axis_override', "pixel")
        wait_until(lambda: not self.window._background_jobs.busy)
        self.assertEqual(self.window.current_cube.x_override, "pixel")
        self.assertEqual(self.window.current_gate, 1.0)
        self.assertFalse(self.errors)

    def test_new_direct_load_discards_pending_async_load(self):
        release = threading.Event()
        started = threading.Event()
        original = background_jobs.load_dat_cube
        other = self.directory / "new.dat"
        other.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        def blocked(*args, **kwargs):
            started.set()
            release.wait(2)
            return original(*args, **kwargs)
        with patch.object(background_jobs, "load_dat_cube", blocked):
            self.window._load_dat_file_async(self.path)
            self.assertTrue(started.wait(1))
            self.window._load_dat_file_direct(other)
            release.set()
            wait_until(lambda: not self.window._background_jobs.busy)
        self.assertEqual(self.window.current_dat_path, other)

    def test_async_discovery_lists_nested_dat_folder(self):
        nested = self.directory / "nested"
        nested.mkdir()
        (nested / "second.dat").write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        with patch("spectra_app.main_window.FilePicker.getExistingDirectory", return_value=str(self.directory)):
            self.window._on_browse_root()
        wait_until(lambda: not self.window._background_jobs.busy)
        self.assertEqual(len(self.window.source_navigation.folderRows), 2)
        self.assertEqual(self.window.source_navigation.fileRows[0]['name'], "sample.dat")

    def test_changed_source_during_load_reports_error_without_replacing_cube(self):
        self.window._load_dat_file_direct(self.path)
        old_cube = self.window.current_cube
        original = background_jobs.load_dat_cube
        def changed(*args, **kwargs):
            cube = original(*args, **kwargs)
            self.path.write_text(self.path.read_text(encoding="utf-8") + "1.3 4 5\n", encoding="utf-8")
            return cube
        with patch.object(background_jobs, "load_dat_cube", changed):
            self.window._load_dat_file_async(self.path)
            wait_until(lambda: not self.window._background_jobs.busy)
        self.assertIs(self.window.current_cube, old_cube)
        self.assertIn("changed while loading", self.errors[0])

    def test_async_load_recalls_regional_recipe_and_fixed_count(self):
        self.window._load_dat_file_direct(self.path)
        dp.set_choice(self.window, 'color_scale', "3")
        dp.setup_number(self.window, 'edge0', 1.05)
        dp.setup_number(self.window, 'edge1', 1.15)
        self.window._save_current_plot_state()
        self.window._load_dat_file_async(self.path)
        wait_until(lambda: not self.window._background_jobs.busy)
        self.assertEqual(self.window._color_scale_mode, "3")
        self.assertEqual(self.window._color_region_boundaries, [1.05, 1.15])
        self.assertEqual(len(self.window._heat_meshes), 3)
        self.assertFalse(self.errors)

    def test_gui_apply_error_is_reported(self):
        def apply(result):
            raise ValueError("apply failed")
        self.window._start_background_job(lambda context: 1, apply, "Working")
        wait_until(lambda: not self.window._background_jobs.busy)
        self.assertEqual(self.errors, ["apply failed"])

    def test_cancel_axis_override_restores_combo_and_discards_pending_cube(self):
        self.window._load_dat_file_direct(self.path)
        old_cube = self.window.current_cube
        started = threading.Event()
        release = threading.Event()
        original = background_jobs.load_dat_cube
        def blocked(*args, **kwargs):
            started.set()
            release.wait(2)
            return original(*args, **kwargs)
        with patch.object(background_jobs, "load_dat_cube", blocked):
            dp.set_choice(self.window, 'axis_override', "pixel")
            self.assertTrue(started.wait(1))
            self.window._cancel_background_job()
            self.assertIn("Cancelling", self.window.statusText)
            self.assertEqual(self.window.dat_properties._axis_override, old_cube.x_override)
            release.set()
            wait_until(lambda: not self.window._background_jobs.busy)
        self.assertIs(self.window.current_cube, old_cube)
        self.assertEqual(self.window.statusText, "Operation cancelled.")

    def test_edits_during_import_are_saved_before_switching_to_new_source(self):
        self.path.write_text("Energy(eV) 0 1\n" + "\n".join(
            f"{1 + i / 10} {i * i} {2 * i * i}" for i in range(13)), encoding="utf-8")
        other = self.directory / "other.dat"
        other.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        self.window._load_dat_file_direct(self.path)
        dp.set_flag(self.window, 'fix_annotations', False)
        self.window.processing.setMode("energy_second_derivative")
        self.window._on_generate_heatmap()
        wait_until(lambda: not self.window._background_jobs.busy)
        outgoing = self.window.current_cube
        original = background_jobs._cube_from_dat_layout
        started, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            started.set()
            release.wait(5)
            return original(*args, **kwargs)
        with patch.object(background_jobs, "_cube_from_dat_layout", blocked):
            self.window._import_data_files([other])
            self.assertTrue(started.wait(1))
            self.assertTrue(self.window._dat_import_pending)
            self.assertFalse(self.window.processing.applyEnabled)
            # Production import pauses processing; even a queued command cannot
            # supersede the import and lose the source being prepared.
            self.window._on_generate_heatmap()
            self.assertIs(self.window.current_cube, outgoing)
            self.window.dat_properties.setLabel('plot_title', "Edited while loading", self.window.dat_properties.ownerToken)
            self.window._heat_ax.set_xlim(1.15, 1.95)
            release.set()
            wait_until(lambda: not self.window._background_jobs.busy)
        imported = self.window.workspace.dataset_for_path(other)
        self.assertIsNotNone(imported)
        self.assertFalse(self.window._dat_import_pending)
        self.assertFalse(self.errors)
        self.window.activate_dataset(imported.dataset_id)
        self.assertEqual(self.window.current_dat_path, other)
        self.window._load_dat_file_direct(self.path)
        self.assertEqual(dp.state(self.window)['plot_title'], "Edited while loading")
        self.assertEqual(self.window._processing_mode, "energy_second_derivative")
        self.assertEqual(self.window._heat_ax.get_xlim(), (1.15, 1.95))

    def test_same_file_reload_keeps_edits_made_after_recipe_was_read(self):
        self.window._load_dat_file_direct(self.path)
        self.window.dat_properties.setLabel('plot_title', "Before loading", self.window.dat_properties.ownerToken)
        original = background_jobs.load_dat_cube
        started, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            started.set()
            release.wait(5)
            return original(*args, **kwargs)
        with patch.object(background_jobs, "load_dat_cube", blocked):
            self.window._load_dat_file_async(self.path)
            self.assertTrue(started.wait(1))
            self.window.dat_properties.setLabel('plot_title', "Latest title", self.window.dat_properties.ownerToken)
            release.set()
            wait_until(lambda: not self.window._background_jobs.busy)
        self.assertEqual(dp.state(self.window)['plot_title'], "Latest title")
        self.assertEqual(self.window._heat_ax.get_title(), "Latest title")

    def _seed_workbench(self, scale=1.):
        from spectra_app.analysis_document import AnalysisSeed
        x = np.linspace(1., 2., 121)
        y = scale * (.4 + 2*np.exp(-.5*((x-1.35)/.04)**2))
        self.path.write_text('Energy(eV) 0 1\n' + '\n'.join(
            f'{a} {b} {b}' for a, b in zip(x, y)), encoding='utf-8')
        self.window._load_dat_file_direct(self.path)
        session = self.window.workspace.active
        self.window.open_graph_document()
        self.window._open_analysis(('heatmap', (session.dataset_id, session.active_graph_id)))
        doc = self.window._workbench_document()
        gate = self.window._current_gate_index()
        aq.set_choice(self.window, 'model', 'Gaussian')
        doc.set_seeds(gate, [AnalysisSeed(1.35, kind='peak', manual=True)])
        self.window._workbench_refresh()
        return doc, gate

    def test_workbench_fits_small_signal_units_with_relative_amplitude_seed(self):
        from spectra_app.fitting import fit_multi_peak
        doc, gate = self._seed_workbench(scale=1e-18)
        amplitudes = []

        def fit(*args, **kwargs):
            amplitudes.extend(kwargs['initial_amplitudes'])
            return fit_multi_peak(*args, **kwargs)

        with patch('spectra_app.fitting.fit_multi_peak', side_effect=fit):
            self.window._workbench_fit()
            wait_until(lambda: not self.window._background_jobs.busy)
        self.assertTrue(amplitudes)
        self.assertLess(max(amplitudes), 1e-16)
        self.assertEqual(doc.gate_status[gate], 'fitted', doc.gate_messages)
        result = doc.science.batch_fit_result_map[gate]
        np.testing.assert_allclose(result.amplitudes, [2e-18], rtol=1e-5, atol=0)
        self.assertGreater(result.r_squared, .999999)
        self.assertFalse(self.errors)

    def test_workbench_fits_manual_shoulder_seed_across_signal_units(self):
        from spectra_app.analysis_document import AnalysisSeed
        x = np.linspace(1., 2., 401)
        shape = np.exp(-.5*((x-1.5)/.2)**2)
        for kind, sign in (('peak', 1), ('dip', -1)):
            for scale in (1., 1e-18, 1e18):
                with self.subTest(kind=kind, scale=scale):
                    y = scale*(.4 + sign*2*shape)
                    path = self.directory / f'shoulder-{kind}-{scale}.dat'
                    path.write_text('Energy(eV) 0 1\n' + '\n'.join(
                        f'{a} {b} {b}' for a, b in zip(x, y)), encoding='utf-8')
                    self.window._load_dat_file_direct(path)
                    session = self.window.workspace.active
                    self.window.open_graph_document()
                    self.window._open_analysis(('heatmap', (session.dataset_id, session.active_graph_id)))
                    doc = self.window._workbench_document()
                    gate = self.window._current_gate_index()
                    aq.set_choice(self.window, 'model', 'Gaussian')
                    # This shoulder lies at the median signal height, so its
                    # local height alone is an unusably small amplitude seed.
                    doc.set_seeds(gate, [AnalysisSeed(1.75, kind=kind, manual=True)])
                    self.window._workbench_refresh()
                    self.window._workbench_fit()
                    wait_until(lambda: not self.window._background_jobs.busy)
                    self.assertEqual(doc.gate_status[gate], 'fitted', doc.gate_messages)
                    result = doc.science.batch_fit_result_map[gate]
                    np.testing.assert_allclose(result.centers, [1.5], atol=2e-6)
                    np.testing.assert_allclose(result.widths, [.2], atol=2e-6)
                    np.testing.assert_allclose(result.amplitudes/scale, [sign*2], atol=2e-6)
                    np.testing.assert_allclose(result.y_fit/scale, y/scale, atol=2e-6)
                    self.assertGreater(result.r_squared, .999999)
                    self.assertFalse(self.errors)

    def test_workbench_fit_cancels_inside_solver_without_applying_result(self):
        doc, gate = self._seed_workbench()
        started, cancelled = threading.Event(), threading.Event()

        def fit(*args, check_cancelled=None, **kwargs):
            self.assertIsNotNone(check_cancelled)
            started.set()
            try:
                while True:
                    check_cancelled()
                    time.sleep(.001)
            except background_jobs.CancelledError:
                cancelled.set()
                raise

        with patch('spectra_app.fitting.fit_multi_peak', side_effect=fit):
            self.window._workbench_fit()
            self.assertTrue(started.wait(1))
            self.window._cancel_background_job()
            wait_until(lambda: not self.window._background_jobs.busy)
        self.assertTrue(cancelled.is_set())
        self.assertNotIn(gate, doc.science.batch_fit_result_map)
        self.assertFalse(self.errors)

    def test_workbench_linecut_serializes_undefined_r_squared_as_null(self):
        from dataclasses import replace
        import json
        from spectra_app.fitting import fit_multi_peak
        doc, gate = self._seed_workbench()
        cube = doc.science.current_cube
        fit = fit_multi_peak(cube.energy, cube.z[gate], centers_hint=np.array([1.35]),
                             model='Gaussian', mode='Peaks')
        doc.record_fit(gate, replace(fit, r_squared=float('nan')), status='fitted', message='')
        with patch.object(self.window, '_publish_analysis_results') as publish:
            self.window._workbench_create_linecut_graph()
        publish.assert_called_once()
        sheet = publish.call_args.args[1][0]
        def reject_nonfinite(value):
            self.fail(f'Nonstandard JSON metric: {value}')
        provenance = json.loads(sheet.snapshot_note, parse_constant=reject_nonfinite)
        self.assertIsNone(provenance['r_squared'])
        self.assertEqual(provenance['rmse'], fit.rmse)
        self.assertFalse(self.errors)
