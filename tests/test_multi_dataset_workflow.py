from tests import source_navigation_helpers as sn
from tests import ordinary_analysis_quick_helpers as oa
from tests.quick_helpers import set_processing_window
import csv
import os
import threading
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PySide6.QtCore import QObject, QCoreApplication, QMimeData, QPointF, Qt, QUrl, QThread
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QGuiApplication
from PySide6.QtTest import QTest
from spectra_app import background_jobs
from tests.dat_properties_quick_helpers import (
    edit_label, state,
    set_choice, set_flag, setup_number)
from spectra_app.xy_import_dialog import XYImportDialog
from tests.background_helpers import wait_jobs
from tests.scientific_fixtures import workflow_fit_result as _fit_result
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import pump
from tests.formal_quick_helpers import item


class DatasetImportTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.directory = self.folder
        self.window = self.controller
        self.errors = self.quick_errors
        review = patch.object(XYImportDialog, 'exec', return_value=XYImportDialog.Accepted)
        review.start()
        self.addCleanup(review.stop)
        self.release = threading.Event()
        self.paths = []
        for folder in ('first', 'second', 'third'):
            parent = self.directory / folder
            parent.mkdir()
            path = parent / 'sample.dat'
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{x:.12g} {x*x:.12g} {x**3:.12g} {x:.12g}'
                for x in np.linspace(1, 4, 31)), encoding='utf-8')
            self.paths.append(path)
        self.invalid = self.directory / 'invalid.dat'
        self.invalid.write_text('not a spectrum matrix', encoding='utf-8')
        self.assertTrue(self.window.request_outer_view('analysis'))
        self.window.dat_details_action.setChecked(True)
        self.window.request_task(0)
        pump()

    def tearDown(self):
        self.release.set()
        wait_jobs(self.window)
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def import_paths(self, paths):
        self.window._import_dat_files(paths)
        wait_jobs(self.window)

    def test_mixed_batch_commits_in_request_order_once_and_reports_every_failure(self):
        w = self.window
        missing = self.directory / 'missing.dat'
        activations = []
        original = w.activate_dataset
        def activate(dataset_id, **kwargs):
            activations.append(dataset_id)
            return original(dataset_id, **kwargs)
        with patch.object(w, 'activate_dataset', side_effect=activate):
            self.import_paths([self.invalid, self.paths[1], missing, self.paths[0]])
        self.assertEqual([s.source_path for s in w.workspace.datasets.values()],
                         [self.paths[1], self.paths[0]])
        self.assertEqual(activations, [w.workspace.active_dataset_id])
        self.assertEqual(w.current_dat_path, self.paths[1])
        self.assertEqual(len(w.source_navigation.datasetRows), 2)
        self.assertEqual(len(self.errors), 1)
        self.assertIn(str(self.invalid), self.errors[0])
        self.assertIn(str(missing), self.errors[0])
        self.errors.clear()
        self.assertFalse(w._dat_import_pending)
        self.assertTrue(w.source_navigation.importEnabled)

    def test_all_fail_and_infrastructure_failure_preserve_existing_cube_and_results(self):
        w = self.window
        w._load_dat_file_direct(self.paths[0])
        owner, cube = w.workspace.active_dataset_id, w.current_cube
        result = _fit_result(2)
        w.fit_result = result
        oa.set_summary(w, 'committed results')
        self.import_paths([self.invalid, self.directory / 'absent.dat'])
        self.assertEqual(w.workspace.active_dataset_id, owner)
        self.assertIs(w.current_cube, cube)
        self.assertIs(w.fit_result, result)
        self.assertEqual(oa.summary(w), 'committed results')
        self.assertEqual(len(self.errors), 1)
        self.assertIn(str(self.invalid), self.errors[0])
        self.assertIn('absent.dat', self.errors[0])
        self.errors.clear()
        original = background_jobs.prepare_dat
        def infrastructure(request, context):
            if request.path == self.paths[2]:
                raise RuntimeError('infrastructure failure')
            return original(request, context)
        with patch.object(background_jobs, 'prepare_dat', side_effect=infrastructure):
            self.import_paths(self.paths[1:])
        self.assertEqual(len(w.workspace.datasets), 1)
        self.assertEqual(w.workspace.active_dataset_id, owner)
        self.assertIs(w.fit_result, result)
        self.assertEqual(len(self.errors), 1)
        self.assertIn('infrastructure failure', self.errors[-1])
        self.errors.clear()
        self.assertFalse(w._dat_import_pending)
        self.assertTrue(w.source_navigation.importEnabled)

    def test_normalized_duplicates_reuse_results_and_same_basename_labels_distinguish(self):
        w = self.window
        self.import_paths(self.paths[:2])
        owner = w.workspace.active_dataset_id
        result = _fit_result(2)
        w.fit_result = result
        equivalent = self.paths[0].parent / '..' / 'first' / 'sample.dat'
        with patch.object(background_jobs, 'prepare_dat', side_effect=AssertionError('duplicate read')):
            self.import_paths([equivalent, self.paths[0], self.paths[1]])
        self.assertEqual(w.workspace.active_dataset_id, owner)
        self.assertIs(w.fit_result, result)
        self.assertEqual(len(w.workspace.datasets), 2)
        self.assertEqual(len(w.source_navigation.datasetRows), 2)
        labels = [row['label'] for row in w.source_navigation.datasetRows]
        self.assertNotEqual(*labels)
        self.assertIn('first', labels[0])
        self.assertIn('second', labels[1])
        for i, path in enumerate(self.paths[:2]):
            row = w.source_navigation.datasetRows[i]
            self.assertEqual(row['path'], str(path))
            self.assertEqual(w.workspace.datasets[row['datasetId']].source_path, path)
        self.assertTrue(w.request_outer_view('analysis'))
        pump()
        sn.key(w, 'sourceDatasets', Qt.Key_Down)
        self.assertEqual(w.current_dat_path, self.paths[1])

    def test_duplicate_names_use_the_shortest_distinguishing_folder_suffix(self):
        w = self.window
        paths = []
        for folder, name in (('Group A/data', 'Sample.DAT'), ('Group B/DATA', 'sample.dat')):
            parent = self.directory / folder
            parent.mkdir(parents=True)
            path = parent / name
            path.write_bytes(self.paths[0].read_bytes())
            paths.append(path)
        unique = self.directory / 'reference.dat'
        unique.write_bytes(self.paths[0].read_bytes())
        self.import_paths([*paths, self.paths[2], unique])

        labels = [row['label'] for row in w.source_navigation.datasetRows]
        self.assertEqual(labels, [f'Sample.DAT — {Path("Group A") / "data"}',
                                  f'sample.dat — {Path("Group B") / "DATA"}',
                                  'sample.dat — third', 'reference.dat'])
        for i, path in enumerate([*paths, self.paths[2], unique]):
            row = w.source_navigation.datasetRows[i]
            self.assertEqual(row['path'], str(path.resolve()))
            self.assertEqual(w.workspace.datasets[row['datasetId']].source_path, path)

    def test_search_keeps_active_results_and_clear_restores_import_order(self):
        w = self.window
        self.import_paths(self.paths)
        self.assertTrue(w.request_outer_view('analysis'))
        pump()
        self.assertIsNotNone(sn.control(w, 'sourceSearch'), 'Imported datasets need a search control')
        owner, cube = w.workspace.active_dataset_id, w.current_cube
        result = _fit_result(2)
        w.fit_result = result
        oa.set_summary(w, 'active results remain available')
        notice = 'One invalid DAT could not be imported.'
        w.source_navigation.set_import_status(notice)

        with patch.object(w, 'activate_dataset', side_effect=AssertionError('search activated a dataset')), \
                patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('search reread a DAT')):
            w.source_navigation.setSearchText('  SECoND  ')
            self.assertEqual([row['datasetId'] for row in w.source_navigation.datasetRows],
                             [w.workspace.dataset_for_path(self.paths[1]).dataset_id])
            self.assertIn('1 / 3', w.source_navigation.countText)
            self.assertEqual(w.workspace.active_dataset_id, owner)
            self.assertEqual(w.source_navigation.activeDatasetId, owner)
            self.assertIs(w.current_cube, cube)
            self.assertIs(w.fit_result, result)
            self.assertEqual(oa.summary(w), 'active results remain available')
            self.assertEqual(w.source_navigation.importStatus, notice)
            sn.key(w, 'sourceSearch', Qt.Key_A, Qt.ControlModifier)
            sn.key(w, 'sourceSearch', Qt.Key_Backspace)
            self.assertEqual(len(w.source_navigation.datasetRows), 3)
            self.assertEqual([w.workspace.datasets[row['datasetId']].source_path
                              for row in w.source_navigation.datasetRows], self.paths)
            self.assertEqual(w.source_navigation.activeDatasetId, owner)
            self.assertEqual(w.source_navigation.importStatus, notice)

    def test_search_keyboard_navigation_skips_hidden_files_and_preserves_no_match_owner(self):
        w = self.window
        self.import_paths(self.paths)
        self.assertTrue(w.request_outer_view('analysis'))
        pump()
        self.assertIsNotNone(sn.control(w, 'sourceSearch'), 'Imported datasets need a search control')
        w.source_navigation.setSearchText('sAMple.DAT')
        self.assertEqual(len(w.source_navigation.datasetRows), 3)
        w.source_navigation.setSearchText(f'  {str(self.paths[2].parent).upper()}  ')
        sn.key(w, 'sourceDatasets', Qt.Key_Down)
        self.assertEqual(w.current_dat_path, self.paths[2])
        self.assertEqual(w.source_navigation.searchText, f'  {str(self.paths[2].parent).upper()}  ')
        self.assertEqual([row['datasetId'] for row in w.source_navigation.datasetRows],
                         [w.workspace.dataset_for_path(self.paths[2]).dataset_id])
        owner, cube = w.workspace.active_dataset_id, w.current_cube
        w.source_navigation.setSearchText('no matching experiment')
        self.assertEqual(w.source_navigation.datasetRows, [])
        self.assertIn('0 / 3', w.source_navigation.countText)
        self.assertTrue(w.source_navigation.noMatches)
        self.assertIn('No matches', sn.control(w, 'sourceNoMatches').property('text'))
        sn.key(w, 'sourceDatasets', Qt.Key_Down)
        sn.key(w, 'sourceDatasets', Qt.Key_Up)
        self.assertEqual(w.workspace.active_dataset_id, owner)
        self.assertIs(w.current_cube, cube)
        self.assertEqual(len(w.workspace.datasets), 3)

    def test_cancel_discards_whole_batch_unlocks_before_thread_ends_and_rejects_late_callbacks(self):
        w = self.window
        w._load_dat_file_direct(self.paths[0])
        owner, cube = w.workspace.active_dataset_id, w.current_cube
        started = threading.Event()
        original = background_jobs.prepare_dat
        def blocked(request, context):
            if request.path == self.paths[2]:
                started.set()
                self.release.wait(5)
            return original(request, context)
        w.request_task(1)
        pump()
        with patch.object(background_jobs, 'prepare_dat', side_effect=blocked):
            w._import_dat_files(self.paths[1:])
            self.assertTrue(started.wait(2))
            token = w._dat_import_token
            self.assertTrue(w._dat_import_pending)
            self.assertEqual(len(w.workspace.datasets), 1)
            self.assertFalse(w.source_navigation.importEnabled)
            self.assertFalse(sn.control(w, 'sourceDatasets').isEnabled())
            self.assertFalse(sn.control(w, 'sourceSearch').isEnabled())
            self.assertFalse(w.processing.applyEnabled)
            self.assertFalse(state(w)['source_axis_enabled'])
            self.assertFalse(oa.command_enabled(w, 'loadCsv'))
            self.assertTrue(w.cancelVisible)
            cancel = next(control for control in self.host.window.findChildren(QObject)
                          if control.property('text') == 'Cancel' and control.property('visible'))
            self.assertTrue(cancel.isEnabled())
            edit_label(w, 'plot_title', 'edited during cancelled import')
            w._set_current_gate_from_index(1, update_plot=True)
            self.assertFalse(oa.command_enabled(w, 'fit'))
            committed = _fit_result(2)
            w.fit_result, w._fit_gate_idx = committed, 1
            w.peak_indices = np.array([4, 17])
            w._peak_gate_idx = 1
            w.batch_peak_map = {1: w.peak_indices.copy()}
            with patch.object(w._background_jobs, 'start', side_effect=AssertionError('superseded batch')), \
                    patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('locked read')), \
                    patch('spectra_app.main_window.FilePicker.getExistingDirectory', side_effect=AssertionError('locked dialog')):
                w._on_browse_root()
                w._on_folder_changed()
                w._import_dat_files([self.paths[0]])
                w._load_dat_file_direct(self.paths[0])
                w._load_dat_file_async(self.paths[0])
                w._on_x_axis_override_changed()
                w._on_x_axis_override_async()
                w._on_generate_heatmap()
                w._restore_original_data()
                w._on_find_peaks()
                w._on_batch_find_peaks_all_gates()
                w._on_fit()
                w._on_batch_fit_all_gates()
                w._on_clear_fit()
                w._on_clear_peaks()
                w._on_clear_batch_peaks()
                w._load_seeds_csv_file(self.directory / 'missing.csv')
                w.activate_dataset(owner)
            self.assertIs(w.current_cube, cube)
            self.assertIs(w.fit_result, committed)
            self.assertEqual(w.batch_peak_map[1].tolist(), [4, 17])
            self.assertFalse(w._add_manual_peak(3))
            self.assertFalse(w._remove_manual_peak_near(1.4))
            w._on_canvas_click(SimpleNamespace(inaxes=w._spec_ax, xdata=1.4, button=1))
            self.assertIsNone(w._drag_peak_arr_idx)
            w._on_canvas_click(SimpleNamespace(inaxes=w._heat_ax, xdata=2, ydata=1, button=1))
            self.assertEqual(w.current_gate, 1)
            self.assertIs(w.fit_result, committed)
            w._cancel_background_job()
            self.assertTrue(w._background_jobs.busy)
            self.assertFalse(w._dat_import_pending)
            self.assertTrue(w.source_navigation.importEnabled)
            self.assertTrue(w.source_navigation.navigationEnabled)
            message = w.statusText
            w._dat_import_progress(token, 'late progress')
            w._dat_import_failed(token, RuntimeError('late error'))
            w._accept_dat_import(token, [], [], [])
            self.assertEqual(w.statusText, message)
            self.release.set()
            wait_jobs(w)
        self.assertEqual(len(w.workspace.datasets), 1)
        self.assertEqual(w.workspace.active_dataset_id, owner)
        self.assertEqual(state(w)['plot_title'], 'edited during cancelled import')
        self.assertEqual(w.current_gate, 1)
        self.assertFalse(self.errors)
        # A retained cancelled worker does not own the next import lock.
        self.import_paths([self.paths[1]])
        self.assertEqual(len(w.workspace.datasets), 2)

    def test_defaults_are_frozen_candidates_are_off_thread_and_latest_outgoing_edits_are_saved(self):
        w = self.window
        w._load_dat_file_direct(self.paths[0])
        owner = w.workspace.active_dataset_id
        self.host.window.resize(1500, 920)
        self.host.show()
        w.open_graph_document()
        edit_label(w, 'plot_title', 'request defaults')
        w._save_plot_annotation('plot_title', 'request defaults')
        started = threading.Event()
        original = background_jobs.prepare_dat
        initializer = w.initialize_dataset
        threads = []
        def blocked(request, context):
            started.set()
            self.release.wait(5)
            return original(request, context)
        def initialize(*args, **kwargs):
            threads.append(QThread.currentThread() == QGuiApplication.instance().thread())
            return initializer(*args, **kwargs)
        with patch.object(background_jobs, 'prepare_dat', side_effect=blocked), \
                patch.object(w, 'initialize_dataset', side_effect=initialize):
            w._import_dat_files(self.paths[1:])
            self.assertTrue(started.wait(2))
            self.assertTrue(w.dat_properties.enabled)
            self.assertFalse(state(w)['source_axis_enabled'])
            edit_label(w, 'plot_title', 'latest outgoing edit')
            w._save_plot_annotation('plot_title', 'latest outgoing edit')
            w._heat_ax.set_xlim(1.35, 3.65)
            self.release.set()
            wait_jobs(w)
        self.assertEqual(threads, [False, False])
        for session in list(w.workspace.datasets.values())[1:]:
            self.assertEqual(session.view.annotations['plot_title'], 'request defaults')
        w.activate_dataset(owner)
        self.assertEqual(state(w)['plot_title'], 'latest outgoing edit')
        np.testing.assert_allclose(w._heat_ax.get_xlim(), [1.35, 3.65])

    def test_multiselect_folder_and_real_qt_multiple_dat_drop_share_registry(self):
        w = self.window
        with patch('spectra_app.main_window.FilePicker.getOpenFileNames',
                   return_value=([str(self.paths[0]), str(self.paths[1])], '')):
            sn.click(w, 'sourceImport')
            wait_jobs(w)
        w.current_folder = str(self.paths[2].parent)
        w._apply_dat_files(w.source_navigation.begin_folder(self.paths[2].parent), [self.paths[2].name])
        w._on_load_selected_file()
        wait_jobs(w)
        self.assertEqual(len(w.workspace.datasets), 3)
        dropped_paths = [self.directory / 'drop-second.dat', self.directory / 'drop-first.dat']
        for path in dropped_paths:
            path.write_text(self.paths[0].read_text(encoding='utf-8'), encoding='utf-8')
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in dropped_paths])
        enter = QDragEnterEvent(QPointF(self.host.window.width() / 2, self.host.window.height() / 2).toPoint(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(self.host.window, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(self.host.window.width() / 2, self.host.window.height() / 2), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(self.host.window, drop)
        self.assertTrue(drop.isAccepted())
        wait_jobs(w)
        self.assertEqual(len(w.workspace.datasets), 5)
        self.assertEqual([session.source_path for session in list(w.workspace.datasets.values())[-2:]], dropped_paths)
        sheet = next(s for s in w.workspace.sheets.values() if s.source_path == dropped_paths[0] and s.role == 'original')
        self.assertEqual(w.graph_documents.registry.active_key, ('workbook', w._sheet_owner(sheet.sheet_id).workbook_id, ''))
        self.assertEqual(w.outerView, 'workspace')
        self.assertIsNotNone(w.graph_documents.content_item(w.graph_documents.registry.active_key))

    def test_csv_drop_is_data_mixed_numeric_drop_imports_and_explicit_seeds_still_works(self):
        w = self.window
        w._load_dat_file_direct(self.paths[0])
        csv_path = self.directory / 'seeds.csv'
        csv_path.write_text('gate,peak_energy\n0,2\n', encoding='utf-8')
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(csv_path))])
        enter = QDragEnterEvent(QPointF(self.host.window.width() / 2, self.host.window.height() / 2).toPoint(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(self.host.window, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(self.host.window.width() / 2, self.host.window.height() / 2), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(self.host.window, drop)
        self.assertTrue(drop.isAccepted())
        wait_jobs(w)
        self.assertNotEqual(w._csv_seeds_path, str(csv_path))
        self.assertTrue(any(s.source_path == csv_path and s.kind == 'xy' for s in w.workspace.sheets.values()))
        with patch('spectra_app.main_window.FilePicker.getOpenFileName', return_value=(str(csv_path), '')):
            w._on_load_seeds_csv()
        self.assertEqual(w._csv_seeds_path, str(csv_path))
        mime.setUrls([QUrl.fromLocalFile(str(self.paths[1])), QUrl.fromLocalFile(str(csv_path))])
        enter = QDragEnterEvent(QPointF(self.host.window.width() / 2, self.host.window.height() / 2).toPoint(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(self.host.window, enter)
        self.assertTrue(enter.isAccepted())
        mixed = QDropEvent(QPointF(self.host.window.width() / 2, self.host.window.height() / 2), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(self.host.window, mixed)
        self.assertTrue(mixed.isAccepted())
        wait_jobs(w)
        self.assertEqual(len(w.workspace.datasets), 2)
        self.assertEqual(sum(s.kind == 'xy' for s in w.workspace.sheets.values()), 1)

    def test_folder_inventory_keeps_its_source_after_same_basename_import_from_other_folder(self):
        w = self.window
        with patch('spectra_app.main_window.FilePicker.getExistingDirectory',
                   return_value=str(self.paths[0].parent)):
            w._on_browse_root()
            wait_jobs(w)
        self.assertEqual(Path(w.source_navigation.selectedFile).name, self.paths[0].name)
        self.assertTrue(w.request_outer_view('analysis'))
        pump()
        with patch('spectra_app.main_window.FilePicker.getOpenFileNames',
                   return_value=([str(self.paths[1])], '')):
            sn.click(w, 'sourceImport')
            wait_jobs(w)
        b_id = w.workspace.dataset_for_path(self.paths[1]).dataset_id
        w.activate_dataset(b_id)
        b_cube = w.current_cube
        b_result = _fit_result(2)
        w.fit_result = b_result
        self.assertEqual(w.current_dat_path, self.paths[1])
        self.assertEqual(Path(w.source_navigation.selectedFile).name, self.paths[0].name)
        self.assertTrue(w.request_outer_view('analysis'))
        pump()
        sn.click(w, 'sourceLoad')
        wait_jobs(w)
        source = next(s for s in w.workspace.sheets.values() if s.source_path == self.paths[0] and s.role == 'original')
        self.assertEqual(w.graph_documents.registry.active_key, ('workbook', w._sheet_owner(source.sheet_id).workbook_id, ''))
        self.assertEqual(len(w.workspace.datasets), 2)
        self.assertIs(w.workspace.datasets[b_id].current_cube, b_cube)
        self.assertIs(w.workspace.datasets[b_id].fit_result, b_result)
        self.assertFalse(self.errors)


class DatasetActivationTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.directory = self.folder
        self.window = self.controller
        self.errors = self.quick_errors
        self.release = threading.Event()
        self.paths = []
        for name, scale in [('a', 1), ('b', 3)]:
            path = self.directory / (name + '.dat')
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{x:.12g} {scale*x*x:.12g} {scale*x**3:.12g} {scale*x:.12g}'
                for x in np.linspace(1, 4, 31)), encoding='utf-8')
            self.paths.append(path)

    def tearDown(self):
        self.release.set()
        wait_jobs(self.window)
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def load(self, index):
        self.window._load_dat_file_direct(self.paths[index])
        self.window.open_graph_document()
        self.assertTrue(self.window.request_outer_view('analysis'))
        pump()
        self.assertTrue(hasattr(self.window, 'workspace'), 'Loads must register dataset sessions')
        return self.window.workspace.active_dataset_id

    def commit(self, center):
        w = self.window
        gate = w._current_gate_index()
        result = _fit_result(center)
        w.fit_result, w._fit_gate_idx = result, gate
        w.batch_fit_result_map = {gate: result}
        w.batch_fit_map = {gate: result.centers.copy()}
        w._batch_fit_export_rows = [w._fit_result_to_export_row(
            w.current_gate, result.model, w.current_cube.energy_label, result, status='ok')]
        w._has_batch_fit_results = True
        w._refresh_export_actions()
        return result

    def assert_new_file_single_color_fix(self, bound, fixed, expected_limits):
        w = self.window
        self.load(0)
        set_flag(w, f'{bound}_lock', True)
        setup_number(w, bound, fixed)
        # The new file's entire intensity range crosses the fixed bound.
        self.paths[1].write_text(
            'Energy(eV) 0 1\n1 1 2\n2 3 4\n3 5 6\n', encoding='utf-8')
        self.load(1)
        self.assertTrue(state(w)[f'{bound}_lock'])
        self.assertEqual(float(state(w)[bound]), fixed)
        np.testing.assert_allclose(
            (float(state(w)['vmin']), float(state(w)['vmax'])), expected_limits, atol=1e-10)
        np.testing.assert_allclose(w._heat_mesh.get_clim(), expected_limits, atol=1e-10)

    def test_new_file_keeps_fixed_minimum_below_auto_maximum(self):
        self.assert_new_file_single_color_fix('vmin', 100.0, (100.0, 100.001))

    def test_new_file_keeps_fixed_maximum_above_auto_minimum(self):
        self.assert_new_file_single_color_fix('vmax', -100.0, (-100.001, -100.0))

    def test_new_file_with_fixed_empty_view_keeps_previous_color_bounds(self):
        w = self.window
        self.load(0)
        set_flag(w, 'xmin_lock', True)
        set_flag(w, 'xmax_lock', True)
        previous = (float(state(w)['vmin']), float(state(w)['vmax']))
        previous_mesh = w._heat_mesh.get_clim()
        previous_bounds = tuple(w._dat_view().bounds[key] for key in ('vmin', 'vmax'))
        self.paths[1].write_text(
            'Energy(eV) 0 1\n20 1 2\n21 3 4\n22 5 6\n', encoding='utf-8')
        self.load(1)
        np.testing.assert_allclose((float(state(w)['vmin']), float(state(w)['vmax'])), previous)
        np.testing.assert_allclose(w._heat_mesh.get_clim(), previous_mesh)
        np.testing.assert_allclose(
            tuple(w._dat_view().bounds[key] for key in ('vmin', 'vmax')), previous_bounds)

    def test_cached_round_trip_restores_owner_controls_results_and_csv_without_reads(self):
        w = self.window
        a = self.load(0)
        cube = w.current_cube
        w._set_current_gate_from_index(2, update_plot=True)
        oa.set_number(w, 'fit_xmin', 1.2)
        oa.set_number(w, 'fit_xmax', 3.8)
        oa.set_value(w, 'fit_xmin_fixed', True)
        oa.set_number(w, 'maxfev', 4321)
        oa.set_value(w, 'derivative_detection', True)
        oa.set_value(w, 'd2_mode', 'Both')
        oa.set_number(w, 'd2_window', 9)
        oa.set_number(w, 'd2_prominence', 0.12)
        oa.set_value(w, 'fit_model', 'Gaussian')
        oa.set_value(w, 'fit_smooth', True)
        oa.set_number(w, 'fit_smooth_window', 7)
        oa.set_number(w, 'linewidth_min', 2)
        oa.set_number(w, 'linewidth_max', 9)
        oa.set_number(w, 'peak_move_limit', 4)
        w.peak_indices = np.array([4, 17])
        w._peak_gate_idx = 2
        w.peak_label_lookup = {(2, 4): 8}
        w.batch_peak_map = {2: np.array([4, 17])}
        seeds = self.directory / 'a.csv'
        seeds.write_text('gate,peak_energy\n2,1.4\n2,2.7\n', encoding='utf-8')
        w._load_seeds_csv_file(seeds)
        oa.set_value(w, 'csv_source', True)
        result = self.commit(2.7)
        oa.set_summary(w, 'A committed summary')
        edit_label(w, 'plot_title', 'A title')
        w._save_plot_annotation('plot_title', 'A title')
        w.processing.setMode('energy_second_derivative')  # pending, no Generate
        w.processing.setSmoothing(False)
        set_processing_window(w.processing, 9)
        w._heat_ax.set_xlim(1.35, 3.65)
        a_limits = w._heat_ax.get_xlim()
        a_generation = w._data_generation
        b = self.load(1)
        w.batch_peak_map[0] = np.array([9])
        w._csv_seeds_map = {0: [3.2]}
        self.commit(3.2)
        oa.set_summary(w, 'B summary')
        oa.set_number(w, 'maxfev', 9876)
        oa.set_value(w, 'derivative_detection', False)
        oa.set_value(w, 'fit_model', 'Lorentzian')
        self.paths[0].rename(self.paths[0].with_suffix('.hidden'))
        settings_value = w._annotation_settings.value
        def cached_settings_value(key, *args, **kwargs):
            # Format names are live UI preferences, not dataset defaults.
            self.assertEqual(key, 'plot/format_templates_v1', 'cached owner read defaults')
            return settings_value(key, *args, **kwargs)
        with patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('cached load')), \
                patch.object(w._plot_state_store, 'load', side_effect=AssertionError('sidecar read')), \
                patch.object(w._annotation_settings, 'value', side_effect=cached_settings_value):
            w.activate_dataset(a)
        self.assertIs(w.current_cube, cube)
        self.assertEqual(w.current_gate, 2)
        self.assertEqual(w.peak_indices.tolist(), [4, 17])
        self.assertEqual(w.peak_label_lookup, {(2, 4): 8})
        self.assertEqual(set(w.batch_peak_map), {2})
        self.assertIs(w.fit_result, result)
        self.assertEqual(w._csv_seeds_map, {2: [1.4, 2.7]})
        self.assertEqual(w._csv_seeds_path, str(seeds))
        self.assertTrue(oa.value(w, 'csv_source'))
        self.assertEqual(oa.summary(w), 'A committed summary')
        self.assertEqual(state(w)['plot_title'], 'A title')
        self.assertEqual(oa.value(w, 'maxfev'), 4321)
        self.assertTrue(oa.value(w, 'derivative_detection'))
        self.assertEqual(oa.value(w, 'd2_mode'), 'Both')
        self.assertEqual(oa.value(w, 'd2_window'), 9)
        self.assertAlmostEqual(oa.value(w, 'd2_prominence'), 0.12)
        self.assertEqual(oa.value(w, 'fit_model'), 'Gaussian')
        self.assertTrue(oa.value(w, 'fit_smooth'))
        self.assertEqual(oa.value(w, 'fit_smooth_window'), 7)
        self.assertEqual(oa.value(w, 'linewidth_min'), 2)
        self.assertEqual(oa.value(w, 'linewidth_max'), 9)
        self.assertEqual(oa.value(w, 'peak_move_limit'), 4)
        self.assertTrue(oa.value(w, 'fit_xmin_fixed'))
        self.assertEqual(w.processing.mode, 'energy_second_derivative')
        self.assertEqual(w._processing_mode, 'original')
        self.assertFalse(w.processing.smoothing)
        self.assertEqual(w.processing.windowLength, 9)
        np.testing.assert_allclose(w._heat_ax.get_xlim(), a_limits)
        self.assertGreater(w._data_generation, a_generation)
        oa.reveal(w, tab=1)
        self.assertTrue(oa.command_enabled(w, 'exportAllFit'))
        output = self.directory / 'owner.csv'
        with patch('spectra_app.main_window.FilePicker.getSaveFileName', return_value=(str(output), '')):
            w._on_export_all_fit_csv()
        with output.open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['gate'], '2')
        self.assertEqual(rows[0]['peak_1_center'], '2.7')
        w.activate_dataset(b)
        self.assertEqual(oa.value(w, 'maxfev'), 9876)
        self.assertEqual(oa.summary(w), 'B summary')

    def test_cached_fixed_recipes_survive_other_owner_defaults_and_later_replot(self):
        w = self.window
        a = self.load(0)
        set_choice(w, 'color_scale', '2')
        setup_number(w, 'edge0', 1.65)
        set_flag(w, 'fix_region_edges', True)
        edge = w._color_region_boundaries[0]
        edit_label(w, 'plot_title', 'A fixed title')
        w._save_plot_annotation('plot_title', 'A fixed title')
        self.load(1)
        setup_number(w, 'edge0', 3.15)
        set_flag(w, 'fix_region_edges', True)
        edit_label(w, 'plot_title', 'B default title')
        w._save_plot_annotation('plot_title', 'B default title')
        settings_before = w._annotation_settings.value('fixed_region_edges/recipe')
        w.activate_dataset(a)
        self.assertEqual(w._annotation_settings.value('fixed_region_edges/recipe'), settings_before)
        w._plot_cube()
        set_choice(w, 'color_scale', '3')
        set_choice(w, 'color_scale', '2')
        self.assertAlmostEqual(w._color_region_boundaries[0], edge)
        self.assertEqual(state(w)['plot_title'], 'A fixed title')

    def test_processing_and_explicit_reread_invalidate_only_owner_and_preserve_id(self):
        w = self.window
        a = self.load(0)
        self.commit(1.8)
        b = self.load(1)
        b_cube, b_fit = w.current_cube, self.commit(3.1)
        w.activate_dataset(a)
        old_fingerprint = w._loaded_source_fingerprint
        self.paths[0].write_text(self.paths[0].read_text().replace('1 1 1 1', '1 20 30 40'), encoding='utf-8')
        w._load_dat_file_direct(self.paths[0])
        self.assertEqual(w.workspace.active_dataset_id, a)
        self.assertNotEqual(w._loaded_source_fingerprint, old_fingerprint)
        self.assertEqual(w._base_cube.z[0, 0], 20)
        self.assertEqual(len(w.workspace.datasets), 2)
        w.activate_dataset(b)
        self.assertIs(w.current_cube, b_cube)
        self.assertIs(w.fit_result, b_fit)
        w.activate_dataset(a)
        w.processing.setMode('energy_second_derivative')
        w._on_generate_heatmap()
        wait_jobs(w)
        self.assertEqual(w.batch_fit_result_map, {})
        retained_session, retained_cube = w.workspace.active, w.current_cube
        retained_results = dict(retained_session.processing_results)
        self.assertTrue(retained_results)
        retained_fit = self.commit(2.1)
        retained_fingerprint = w._loaded_source_fingerprint
        self.paths[0].write_text(self.paths[0].read_text().replace('1 20 30 40', '1 21 31 41'), encoding='utf-8')
        # Retained processing runs bind the original source values and axes.
        # A changed reread must retain that owner; explicit new-copy import is
        # the supported route for the changed source.
        w._load_dat_file_direct(self.paths[0])
        self.assertEqual(len(self.errors), 1)
        self.assertIn('Import this changed DAT as a new copy', self.errors.pop())
        self.assertIs(w.workspace.active, retained_session)
        self.assertIs(w.current_cube, retained_cube)
        self.assertIs(w.fit_result, retained_fit)
        self.assertEqual(w._loaded_source_fingerprint, retained_fingerprint)
        self.assertEqual(w._base_cube.z[0, 0], 20)
        self.assertEqual(retained_session.processing_results, retained_results)
        w._import_data_files([self.paths[0]], new_copy=True)
        wait_jobs(w)
        copied = next(session for session in w.workspace.datasets.values()
                      if session.source_path == self.paths[0] and session.dataset_id != a)
        self.assertNotEqual(copied.dataset_id, a)
        self.assertEqual(copied.base_cube.z[0, 0], 21)
        self.assertIs(w.workspace.datasets[a], retained_session)
        self.assertIs(retained_session.current_cube, retained_cube)
        self.assertIs(retained_session.fit_result, retained_fit)
        self.assertEqual(len(w.workspace.datasets), 3)
        w.activate_dataset(b)
        self.assertIs(w.current_cube, b_cube)
        self.assertIs(w.fit_result, b_fit)

    def test_applied_derivative_and_axis_change_preserve_other_owner(self):
        w = self.window
        a = self.load(0)
        edit_label(w, 'y_axis_name', 'A Gate')
        self.commit(2.1)
        b = self.load(1)
        edit_label(w, 'y_axis_name', 'B default Gate')
        b_cube, b_fit = w.current_cube, self.commit(3.1)
        w.activate_dataset(a)
        # Axis interpretation remains editable before a processing run is retained.
        w.dat_properties._axis_override = 'pixel'
        with patch.object(w._annotation_settings, 'value', side_effect=AssertionError('runtime defaults read')):
            w._on_x_axis_override_changed()
        self.assertEqual(w.current_cube.x_override, 'pixel')
        self.assertEqual(state(w)['y_axis_name'], 'A Gate')
        self.assertIsNone(w.fit_result)
        w.activate_dataset(b)
        self.assertIs(w.current_cube, b_cube)
        self.assertIs(w.fit_result, b_fit)
        w.activate_dataset(a)
        # Return to photon energy before requesting an energy derivative.
        w.dat_properties._axis_override = 'ev'
        with patch.object(w._annotation_settings, 'value', side_effect=AssertionError('runtime defaults read')):
            w._on_x_axis_override_changed()
        self.assertEqual(w.current_cube.x_override, 'ev')
        w.processing.setMode('energy_second_derivative')
        w.processing.setSmoothing(False)
        set_processing_window(w.processing, 9)
        w._on_generate_heatmap()
        wait_jobs(w)
        a_session, a_processed = w.workspace.active, w.current_cube
        a_source = w._base_cube
        a_results = dict(a_session.processing_results)
        self.assertTrue(a_results)
        a_fit = self.commit(2.1)
        w.activate_dataset(b)
        w.activate_dataset(a)
        self.assertIs(w.current_cube, a_processed)
        self.assertIs(w.fit_result, a_fit)
        self.assertEqual(w._processing_mode, 'energy_second_derivative')
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')):
            w.activate_dataset(b)
            w.activate_dataset(a)
        # A retained run protects its source interpretation and keeps its own
        # scientific and fit identities while restoring the rejected choice.
        w.dat_properties._axis_override = 'pixel'
        with patch.object(w._annotation_settings, 'value', side_effect=AssertionError('runtime defaults read')):
            w._on_x_axis_override_changed()
        self.assertEqual(w.dat_properties._axis_override, 'ev')
        self.assertEqual(w.current_cube.x_override, 'ev')
        self.assertIs(w.current_cube, a_processed)
        self.assertIs(w._base_cube, a_source)
        self.assertIs(w.fit_result, a_fit)
        self.assertEqual(a_session.processing_results, a_results)
        self.assertEqual(state(w)['y_axis_name'], 'A Gate')
        self.assertIn('new project', w.statusText)
        w.activate_dataset(b)
        self.assertIs(w.current_cube, b_cube)
        self.assertIs(w.fit_result, b_fit)

    def test_staged_initialization_uses_detached_defaults_and_sidecar_failure_does_not_block_switch(self):
        w = self.window
        a = self.load(0)
        edit_label(w, 'plot_title', 'request title')
        fingerprint = w._loaded_source_fingerprint
        defaults = w.snapshot_dataset_defaults()
        # A batch can construct candidates without changing the visible owner.
        from spectra_app.dat_io import load_dat_cube
        from spectra_app.plot_state import source_fingerprint
        prepared = (load_dat_cube(self.paths[1]), source_fingerprint(self.paths[1]), None, '')
        edit_label(w, 'plot_title', 'later outgoing edit')
        candidate = w.initialize_dataset(self.paths[1], prepared, defaults)
        self.assertEqual(w.workspace.active_dataset_id, a)
        self.assertEqual(state(w)['plot_title'], 'later outgoing edit')
        self.assertEqual(candidate.view.annotations['plot_title'], 'request title')
        w.register_dataset(candidate)
        self.paths[0].write_text(self.paths[0].read_text() + '\n', encoding='utf-8')
        calls = []
        def failed_save(path, source_hash, plot):
            calls.append((path, source_hash))
            raise OSError('sidecar is read-only')
        with patch.object(w._plot_state_store, 'save', side_effect=failed_save):
            w.activate_dataset(candidate.dataset_id)
        self.assertEqual(calls, [(self.paths[0], fingerprint)])
        self.assertEqual(w.workspace.active_dataset_id, candidate.dataset_id)
        self.assertIn('sidecar is read-only', w.statusText)
        w.activate_dataset(a)
        self.assertEqual(state(w)['plot_title'], 'later outgoing edit')

    def test_switch_flushes_annotations_and_discards_transient_interactions(self):
        w = self.window
        a = self.load(0)
        b = self.load(1)
        w.activate_dataset(a)
        edit_label(w, 'plot_title', 'last keystroke')
        w._save_plot_annotation('plot_title', 'last keystroke')
        self.assertTrue(w._annotation_edit_pending)
        w._schedule_view_layout()
        w._drag_peak_arr_idx, w._drag_current_x = 2, 1.5
        w._batch_delete_circle_start = (1.4, 1)
        w._lasso_selected_peak_ids = [(0, 4)]
        w._cached_d2 = (np.array([1]), np.array([2]))
        w.toolbar.push_current()
        w.activate_dataset(b)
        self.assertFalse(w._annotation_edit_pending)
        self.assertFalse(w._annotation_edit_timer.isActive())
        self.assertFalse(w._view_layout_timer.isActive())
        self.assertIsNone(w._drag_peak_arr_idx)
        self.assertIsNone(w._drag_current_x)
        self.assertIsNone(w._batch_delete_circle_start)
        self.assertEqual(w._lasso_selected_peak_ids, [])
        self.assertIsNone(w._cached_d2)
        self.assertFalse(w.toolbar._nav_stack())
        w.activate_dataset(a)
        self.assertEqual(state(w)['plot_title'], 'last keystroke')

    def test_returning_to_same_owner_rejects_blocked_old_result_and_error(self):
        w = self.window
        a = self.load(0)
        b = self.load(1)
        for fails in (False, True):
            w.activate_dataset(a)
            committed = self.commit(1.8)
            w._csv_seeds_map = {w._current_gate_index(): [2.0]}
            oa.set_value(w, 'csv_source', True)
            started = threading.Event()
            self.release.clear()
            def solver(*args, **kwargs):
                started.set()
                self.release.wait(5)
                if fails:
                    raise ValueError('stale owner error')
                return _fit_result(3.3)
            with patch('spectra_app.main_window.fit_multi_peak', side_effect=solver):
                old_key = w._analysis_job_key()
                w._on_fit()
                self.assertTrue(started.wait(2))
                callbacks = next(iter(w._background_jobs._jobs.values()))
                with self.assertRaisesRegex(ValueError, 'Wait'):
                    w.activate_dataset(b)
                self.assertEqual(w.workspace.active_dataset_id, a)
                w._cancel_background_job()
                self.release.set()
                wait_jobs(w)
                w.activate_dataset(b)
                w.activate_dataset(a)
                self.assertNotEqual(old_key, w._analysis_job_key())
                if fails:
                    callbacks[1](ValueError('stale owner error'))
                else:
                    callbacks[0](_fit_result(3.3))
            self.assertIs(w.fit_result, committed)
        self.assertFalse(self.errors)
