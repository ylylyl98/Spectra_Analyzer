import csv
import os
import threading
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl, QThread
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from spectra_app import background_jobs
from spectra_app.main_window import MainWindow
from spectra_app.xy_import_dialog import XYImportDialog
from tests.settings_helpers import isolate_settings
from tests.background_helpers import wait_jobs
from tests.test_workflow import _fit_result

APP = QApplication.instance() or QApplication([])


class DatasetImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = isolate_settings(self).parent
        self.window = MainWindow()
        self.errors = []
        self.window._show_error = self.errors.append
        review = patch.object(XYImportDialog, 'exec', return_value=QDialog.Accepted)
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

    def tearDown(self):
        self.release.set()
        wait_jobs(self.window)
        self.window.close()

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
        self.assertEqual(w.imported_dataset_list.count(), 2)
        self.assertEqual(len(self.errors), 1)
        self.assertIn(str(self.invalid), self.errors[0])
        self.assertIn(str(missing), self.errors[0])
        self.assertFalse(w._dat_import_pending)
        self.assertTrue(w.import_dats_btn.isEnabled())

    def test_all_fail_and_infrastructure_failure_preserve_existing_cube_and_results(self):
        w = self.window
        w._load_dat_file_direct(self.paths[0])
        owner, cube = w.workspace.active_dataset_id, w.current_cube
        result = _fit_result(2)
        w.fit_result = result
        w.analysis_text.setPlainText('committed results')
        self.import_paths([self.invalid, self.directory / 'absent.dat'])
        self.assertEqual(w.workspace.active_dataset_id, owner)
        self.assertIs(w.current_cube, cube)
        self.assertIs(w.fit_result, result)
        self.assertEqual(w.analysis_text.toPlainText(), 'committed results')
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
        self.assertIn('infrastructure failure', self.errors[-1])
        self.assertFalse(w._dat_import_pending)
        self.assertTrue(w.import_dats_btn.isEnabled())

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
        self.assertEqual(w.imported_dataset_list.count(), 2)
        labels = [w.imported_dataset_list.item(i).text() for i in range(2)]
        self.assertNotEqual(*labels)
        self.assertIn('first', labels[0])
        self.assertIn('second', labels[1])
        for i, path in enumerate(self.paths[:2]):
            item = w.imported_dataset_list.item(i)
            self.assertEqual(item.toolTip(), str(path))
            self.assertEqual(w.workspace.datasets[item.data(Qt.UserRole)].source_path, path)
        w.imported_dataset_list.setFocus()
        QTest.keyClick(w.imported_dataset_list, Qt.Key_Down)
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

        labels = [w.imported_dataset_list.item(i).text() for i in range(4)]
        self.assertEqual(labels, [f'Sample.DAT — {Path("Group A") / "data"}',
                                  f'sample.dat — {Path("Group B") / "DATA"}',
                                  'sample.dat — third', 'reference.dat'])
        for i, path in enumerate([*paths, self.paths[2], unique]):
            item = w.imported_dataset_list.item(i)
            self.assertEqual(item.toolTip(), str(path.resolve()))
            self.assertEqual(w.workspace.datasets[item.data(Qt.UserRole)].source_path, path)

    def test_search_keeps_active_results_and_clear_restores_import_order(self):
        w = self.window
        self.import_paths(self.paths)
        self.assertTrue(hasattr(w, 'imported_dataset_search'), 'Imported datasets need a search control')
        owner, cube = w.workspace.active_dataset_id, w.current_cube
        result = _fit_result(2)
        w.fit_result = result
        w.analysis_text.setPlainText('active results remain available')
        notice = 'One invalid DAT could not be imported.'
        w.dat_import_status_label.setText(notice)

        with patch.object(w, 'activate_dataset', side_effect=AssertionError('search activated a dataset')), \
                patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('search reread a DAT')):
            w.imported_dataset_search.setText('  SECoND  ')
            self.assertEqual([w.imported_dataset_list.item(i).isHidden() for i in range(3)],
                             [True, False, True])
            self.assertIn('1 / 3', w.imported_dataset_label.text())
            self.assertEqual(w.workspace.active_dataset_id, owner)
            self.assertEqual(w.imported_dataset_list.currentItem().data(Qt.UserRole), owner)
            self.assertIs(w.current_cube, cube)
            self.assertIs(w.fit_result, result)
            self.assertEqual(w.analysis_text.toPlainText(), 'active results remain available')
            self.assertEqual(w.dat_import_status_label.text(), notice)
            w.imported_dataset_search.setFocus()
            QTest.keyClick(w.imported_dataset_search, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClick(w.imported_dataset_search, Qt.Key_Backspace)
            self.assertEqual([w.imported_dataset_list.item(i).isHidden() for i in range(3)],
                             [False, False, False])
            self.assertEqual([w.workspace.datasets[w.imported_dataset_list.item(i).data(Qt.UserRole)].source_path
                              for i in range(3)], self.paths)
            self.assertEqual(w.imported_dataset_list.currentItem().data(Qt.UserRole), owner)
            self.assertEqual(w.dat_import_status_label.text(), notice)

    def test_search_keyboard_navigation_skips_hidden_files_and_preserves_no_match_owner(self):
        w = self.window
        self.import_paths(self.paths)
        self.assertTrue(hasattr(w, 'imported_dataset_search'), 'Imported datasets need a search control')
        w.imported_dataset_search.setText('sAMple.DAT')
        self.assertTrue(all(not w.imported_dataset_list.item(i).isHidden() for i in range(3)))
        w.imported_dataset_search.setText(f'  {str(self.paths[2].parent).upper()}  ')
        w.imported_dataset_list.setFocus()
        QTest.keyClick(w.imported_dataset_list, Qt.Key_Down)
        self.assertEqual(w.current_dat_path, self.paths[2])
        self.assertEqual(w.imported_dataset_search.text(), f'  {str(self.paths[2].parent).upper()}  ')
        self.assertEqual([w.imported_dataset_list.item(i).isHidden() for i in range(3)],
                         [True, True, False])
        owner, cube = w.workspace.active_dataset_id, w.current_cube
        w.imported_dataset_search.setText('no matching experiment')
        self.assertTrue(all(w.imported_dataset_list.item(i).isHidden() for i in range(3)))
        self.assertIn('0 / 3', w.imported_dataset_label.text())
        self.assertFalse(w.imported_dataset_filter_status_label.isHidden())
        self.assertIn('No matches', w.imported_dataset_filter_status_label.text())
        QTest.keyClick(w.imported_dataset_list, Qt.Key_Down)
        QTest.keyClick(w.imported_dataset_list, Qt.Key_Up)
        self.assertEqual(w.workspace.active_dataset_id, owner)
        self.assertIs(w.current_cube, cube)
        self.assertEqual(w.imported_dataset_list.count(), 3)

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
        with patch.object(background_jobs, 'prepare_dat', side_effect=blocked):
            w._import_dat_files(self.paths[1:])
            self.assertTrue(started.wait(2))
            token = w._dat_import_token
            self.assertTrue(w._dat_import_pending)
            self.assertEqual(len(w.workspace.datasets), 1)
            self.assertFalse(w.import_dats_btn.isEnabled())
            self.assertFalse(w.imported_dataset_list.isEnabled())
            self.assertFalse(w.imported_dataset_search.isEnabled())
            self.assertFalse(w.generate_heatmap_btn.isEnabled())
            self.assertFalse(w.x_axis_override_combo.isEnabled())
            self.assertFalse(w.load_seeds_csv_btn.isEnabled())
            self.assertTrue(w._job_cancel_button.isEnabled())
            w.plot_title_edit.setText('edited during cancelled import')
            w._set_current_gate_from_index(1, update_plot=True)
            self.assertFalse(w.fit_btn.isEnabled())
            committed = _fit_result(2)
            w.fit_result, w._fit_gate_idx = committed, 1
            w.peak_indices = np.array([4, 17])
            w._peak_gate_idx = 1
            w.batch_peak_map = {1: w.peak_indices.copy()}
            with patch.object(w._background_jobs, 'start', side_effect=AssertionError('superseded batch')), \
                    patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('locked read')), \
                    patch('spectra_app.main_window.QFileDialog.getExistingDirectory', side_effect=AssertionError('locked dialog')):
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
            self.assertTrue(w.import_dats_btn.isEnabled())
            self.assertTrue(w.imported_dataset_search.isEnabled())
            message = w.statusBar().currentMessage()
            w._dat_import_progress(token, 'late progress')
            w._dat_import_failed(token, RuntimeError('late error'))
            w._accept_dat_import(token, [], [], [])
            self.assertEqual(w.statusBar().currentMessage(), message)
            self.release.set()
            wait_jobs(w)
        self.assertEqual(len(w.workspace.datasets), 1)
        self.assertEqual(w.workspace.active_dataset_id, owner)
        self.assertEqual(w.plot_title_edit.text(), 'edited during cancelled import')
        self.assertEqual(w.current_gate, 1)
        self.assertFalse(self.errors)
        # A retained cancelled worker does not own the next import lock.
        self.import_paths([self.paths[1]])
        self.assertEqual(len(w.workspace.datasets), 2)

    def test_defaults_are_frozen_candidates_are_off_thread_and_latest_outgoing_edits_are_saved(self):
        w = self.window
        w._load_dat_file_direct(self.paths[0])
        owner = w.workspace.active_dataset_id
        w.plot_title_edit.setText('request defaults')
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
            threads.append(QThread.currentThread() == APP.thread())
            return initializer(*args, **kwargs)
        with patch.object(background_jobs, 'prepare_dat', side_effect=blocked), \
                patch.object(w, 'initialize_dataset', side_effect=initialize):
            w._import_dat_files(self.paths[1:])
            self.assertTrue(started.wait(2))
            w.plot_title_edit.setText('latest outgoing edit')
            w._save_plot_annotation('plot_title', 'latest outgoing edit')
            w._heat_ax.set_xlim(1.35, 3.65)
            self.release.set()
            wait_jobs(w)
        self.assertEqual(threads, [False, False])
        for session in list(w.workspace.datasets.values())[1:]:
            self.assertEqual(session.view.annotations['plot_title'], 'request defaults')
        w.activate_dataset(owner)
        self.assertEqual(w.plot_title_edit.text(), 'latest outgoing edit')
        np.testing.assert_allclose(w._heat_ax.get_xlim(), [1.35, 3.65])

    def test_multiselect_folder_and_real_qt_multiple_dat_drop_share_registry(self):
        w = self.window
        with patch('spectra_app.main_window.QFileDialog.getOpenFileNames',
                   return_value=([str(self.paths[0]), str(self.paths[1])], '')):
            w.import_dats_btn.click()
            wait_jobs(w)
        w.current_folder = str(self.paths[2].parent)
        w._apply_dat_files([self.paths[2].name])
        w._on_load_selected_file()
        wait_jobs(w)
        self.assertEqual(len(w.workspace.datasets), 3)
        dropped_paths = [self.directory / 'drop-second.dat', self.directory / 'drop-first.dat']
        for path in dropped_paths:
            path.write_text(self.paths[0].read_text(encoding='utf-8'), encoding='utf-8')
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in dropped_paths])
        enter = QDragEnterEvent(QPointF(20, 20).toPoint(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        w.dragEnterEvent(enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        w.dropEvent(drop)
        self.assertTrue(drop.isAccepted())
        wait_jobs(w)
        self.assertEqual(len(w.workspace.datasets), 5)
        self.assertEqual([session.source_path for session in list(w.workspace.datasets.values())[-2:]], dropped_paths)
        sheet = next(s for s in w.workspace.sheets.values() if s.source_path == dropped_paths[0] and s.role == 'original')
        self.assertEqual(w.graph_documents._active_key, ('workbook', w._sheet_owner(sheet.sheet_id).workbook_id, ''))
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)

    def test_csv_drop_is_data_mixed_numeric_drop_imports_and_explicit_seeds_still_works(self):
        w = self.window
        w._load_dat_file_direct(self.paths[0])
        csv_path = self.directory / 'seeds.csv'
        csv_path.write_text('gate,peak_energy\n0,2\n', encoding='utf-8')
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(csv_path))])
        drop = QDropEvent(QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        w.dropEvent(drop)
        self.assertTrue(drop.isAccepted())
        wait_jobs(w)
        self.assertNotEqual(w._csv_seeds_path, str(csv_path))
        self.assertTrue(any(s.source_path == csv_path and s.kind == 'xy' for s in w.workspace.sheets.values()))
        with patch('spectra_app.main_window.QFileDialog.getOpenFileName', return_value=(str(csv_path), '')):
            w._on_load_seeds_csv()
        self.assertEqual(w._csv_seeds_path, str(csv_path))
        mime.setUrls([QUrl.fromLocalFile(str(self.paths[1])), QUrl.fromLocalFile(str(csv_path))])
        mixed = QDropEvent(QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        w.dropEvent(mixed)
        self.assertTrue(mixed.isAccepted())
        wait_jobs(w)
        self.assertEqual(len(w.workspace.datasets), 2)
        self.assertEqual(sum(s.kind == 'xy' for s in w.workspace.sheets.values()), 1)

    def test_folder_inventory_keeps_its_source_after_same_basename_import_from_other_folder(self):
        w = self.window
        with patch('spectra_app.main_window.QFileDialog.getExistingDirectory',
                   return_value=str(self.paths[0].parent)):
            w._on_browse_root()
            wait_jobs(w)
        self.assertEqual(w.file_list.currentItem().text(), self.paths[0].name)
        with patch('spectra_app.main_window.QFileDialog.getOpenFileNames',
                   return_value=([str(self.paths[1])], '')):
            w.import_dats_btn.click()
            wait_jobs(w)
        b_id = w.workspace.dataset_for_path(self.paths[1]).dataset_id
        w.activate_dataset(b_id)
        b_cube = w.current_cube
        b_result = _fit_result(2)
        w.fit_result = b_result
        self.assertEqual(w.current_dat_path, self.paths[1])
        self.assertEqual(w.file_list.currentItem().text(), self.paths[0].name)
        w.load_btn.click()
        wait_jobs(w)
        source = next(s for s in w.workspace.sheets.values() if s.source_path == self.paths[0] and s.role == 'original')
        self.assertEqual(w.graph_documents._active_key, ('workbook', w._sheet_owner(source.sheet_id).workbook_id, ''))
        self.assertEqual(len(w.workspace.datasets), 2)
        self.assertIs(w.workspace.datasets[b_id].current_cube, b_cube)
        self.assertIs(w.workspace.datasets[b_id].fit_result, b_result)
        self.assertFalse(self.errors)


class DatasetActivationTests(unittest.TestCase):
    def setUp(self):
        self.directory = isolate_settings(self).parent
        self.window = MainWindow()
        self.errors = []
        self.window._show_error = self.errors.append
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
        self.window.close()

    def load(self, index):
        self.window._load_dat_file_direct(self.paths[index])
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
        getattr(w, f'{bound}_lock').setChecked(True)
        getattr(w, f'{bound}_spin').setValue(fixed)
        # The new file's entire intensity range crosses the fixed bound.
        self.paths[1].write_text(
            'Energy(eV) 0 1\n1 1 2\n2 3 4\n3 5 6\n', encoding='utf-8')
        self.load(1)
        self.assertTrue(getattr(w, f'{bound}_lock').isChecked())
        self.assertEqual(getattr(w, f'{bound}_spin').value(), fixed)
        np.testing.assert_allclose(
            (w.vmin_spin.value(), w.vmax_spin.value()), expected_limits, atol=1e-10)
        np.testing.assert_allclose(w._heat_mesh.get_clim(), expected_limits, atol=1e-10)

    def test_new_file_keeps_fixed_minimum_below_auto_maximum(self):
        self.assert_new_file_single_color_fix('vmin', 100.0, (100.0, 100.001))

    def test_new_file_keeps_fixed_maximum_above_auto_minimum(self):
        self.assert_new_file_single_color_fix('vmax', -100.0, (-100.001, -100.0))

    def test_new_file_with_fixed_empty_view_keeps_previous_color_bounds(self):
        w = self.window
        self.load(0)
        w.xmin_lock.setChecked(True)
        w.xmax_lock.setChecked(True)
        previous = (w.vmin_spin.value(), w.vmax_spin.value())
        self.paths[1].write_text(
            'Energy(eV) 0 1\n20 1 2\n21 3 4\n22 5 6\n', encoding='utf-8')
        self.load(1)
        np.testing.assert_allclose((w.vmin_spin.value(), w.vmax_spin.value()), previous)
        np.testing.assert_allclose(w._heat_mesh.get_clim(), previous)

    def test_cached_round_trip_restores_owner_controls_results_and_csv_without_reads(self):
        w = self.window
        a = self.load(0)
        cube = w.current_cube
        w._set_current_gate_from_index(2, update_plot=True)
        w.fit_xmin_spin.setValue(1.2)
        w.fit_xmax_spin.setValue(3.8)
        w.fit_xmin_lock.setChecked(True)
        w.maxfev_spin.setValue(4321)
        w._d2_radio.setChecked(True)
        w.d2_mode_combo.setCurrentText('Both')
        w.d2_sg_win_spin.setValue(9)
        w.d2_prom_spin.setValue(0.12)
        w.fit_model_combo.setCurrentText('Gaussian')
        w.fit_smooth_chk.setChecked(True)
        w.fit_smooth_win_spin.setValue(7)
        w.lw_min_spin.setValue(2)
        w.lw_max_spin.setValue(9)
        w.peak_move_limit_spin.setValue(4)
        w.peak_indices = np.array([4, 17])
        w._peak_gate_idx = 2
        w.peak_label_lookup = {(2, 4): 8}
        w.batch_peak_map = {2: np.array([4, 17])}
        seeds = self.directory / 'a.csv'
        seeds.write_text('gate,peak_energy\n2,1.4\n2,2.7\n', encoding='utf-8')
        w._load_seeds_csv_file(seeds)
        w._seeds_from_csv_radio.setChecked(True)
        result = self.commit(2.7)
        w.analysis_text.setPlainText('A committed summary')
        w.plot_title_edit.setText('A title')
        w._save_plot_annotation('plot_title', 'A title')
        w.processing_mode_combo.setCurrentIndex(1)  # pending, no Generate
        w.processing_smooth_chk.setChecked(False)
        w.processing_window_spin.setValue(9)
        w._heat_ax.set_xlim(1.35, 3.65)
        a_limits = w._heat_ax.get_xlim()
        a_generation = w._data_generation
        b = self.load(1)
        w.batch_peak_map[0] = np.array([9])
        w._csv_seeds_map = {0: [3.2]}
        self.commit(3.2)
        w.analysis_text.setPlainText('B summary')
        w.maxfev_spin.setValue(9876)
        w._orig_radio.setChecked(True)
        w.fit_model_combo.setCurrentText('Lorentzian')
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
        self.assertTrue(w._seeds_from_csv_radio.isChecked())
        self.assertEqual(w.analysis_text.toPlainText(), 'A committed summary')
        self.assertEqual(w.plot_title_edit.text(), 'A title')
        self.assertEqual(w.maxfev_spin.value(), 4321)
        self.assertTrue(w._d2_radio.isChecked())
        self.assertEqual(w.d2_mode_combo.currentText(), 'Both')
        self.assertEqual(w.d2_sg_win_spin.value(), 9)
        self.assertAlmostEqual(w.d2_prom_spin.value(), 0.12)
        self.assertEqual(w.fit_model_combo.currentText(), 'Gaussian')
        self.assertTrue(w.fit_smooth_chk.isChecked())
        self.assertEqual(w.fit_smooth_win_spin.value(), 7)
        self.assertEqual(w.lw_min_spin.value(), 2)
        self.assertEqual(w.lw_max_spin.value(), 9)
        self.assertEqual(w.peak_move_limit_spin.value(), 4)
        self.assertTrue(w.fit_xmin_lock.isChecked())
        self.assertEqual(w.processing_mode_combo.currentData(), 'energy_second_derivative')
        self.assertEqual(w._processing_mode, 'original')
        self.assertFalse(w.processing_smooth_chk.isChecked())
        self.assertEqual(w.processing_window_spin.value(), 9)
        np.testing.assert_allclose(w._heat_ax.get_xlim(), a_limits)
        self.assertGreater(w._data_generation, a_generation)
        self.assertTrue(w.export_all_fit_btn.isEnabled())
        output = self.directory / 'owner.csv'
        with patch('spectra_app.main_window.QFileDialog.getSaveFileName', return_value=(str(output), '')):
            w._on_export_all_fit_csv()
        with output.open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['gate'], '2')
        self.assertEqual(rows[0]['peak_1_center'], '2.7')
        w.activate_dataset(b)
        self.assertEqual(w.maxfev_spin.value(), 9876)
        self.assertEqual(w.analysis_text.toPlainText(), 'B summary')

    def test_cached_fixed_recipes_survive_other_owner_defaults_and_later_replot(self):
        w = self.window
        a = self.load(0)
        w.color_scale_combo.setCurrentIndex(1)
        w.region_boundary_spins[0].setValue(1.65)
        w.fix_region_edges_chk.setChecked(True)
        edge = w._color_region_boundaries[0]
        w.plot_title_edit.setText('A fixed title')
        w._save_plot_annotation('plot_title', 'A fixed title')
        self.load(1)
        w.region_boundary_spins[0].setValue(3.15)
        w.fix_region_edges_chk.setChecked(True)
        w.plot_title_edit.setText('B default title')
        w._save_plot_annotation('plot_title', 'B default title')
        settings_before = w._annotation_settings.value('fixed_region_edges/recipe')
        w.activate_dataset(a)
        self.assertEqual(w._annotation_settings.value('fixed_region_edges/recipe'), settings_before)
        w._plot_cube()
        w.color_scale_combo.setCurrentIndex(2)
        w.color_scale_combo.setCurrentIndex(1)
        self.assertAlmostEqual(w._color_region_boundaries[0], edge)
        self.assertEqual(w.plot_title_edit.text(), 'A fixed title')

    def test_processing_and_explicit_reread_invalidate_only_owner_and_preserve_id(self):
        w = self.window
        a = self.load(0)
        self.commit(1.8)
        b = self.load(1)
        b_cube, b_fit = w.current_cube, self.commit(3.1)
        w.activate_dataset(a)
        w.processing_mode_combo.setCurrentIndex(1)
        w._on_generate_heatmap()
        wait_jobs(w)
        self.assertEqual(w.batch_fit_result_map, {})
        w.activate_dataset(b)
        self.assertIs(w.current_cube, b_cube)
        self.assertIs(w.fit_result, b_fit)
        w.activate_dataset(a)
        old_fingerprint = w._loaded_source_fingerprint
        self.paths[0].write_text(self.paths[0].read_text().replace('1 1 1 1', '1 20 30 40'), encoding='utf-8')
        w._load_dat_file_direct(self.paths[0])
        self.assertEqual(w.workspace.active_dataset_id, a)
        self.assertNotEqual(w._loaded_source_fingerprint, old_fingerprint)
        self.assertEqual(w._base_cube.z[0, 0], 20)
        self.assertEqual(len(w.workspace.datasets), 2)
        w.activate_dataset(b)
        self.assertIs(w.fit_result, b_fit)

    def test_applied_derivative_and_axis_change_preserve_other_owner(self):
        w = self.window
        a = self.load(0)
        w.y_axis_name_edit.setText('A Gate')
        w.processing_mode_combo.setCurrentIndex(1)
        w.processing_smooth_chk.setChecked(False)
        w.processing_window_spin.setValue(9)
        w._on_generate_heatmap()
        wait_jobs(w)
        a_processed = w.current_cube
        a_fit = self.commit(2.1)
        b = self.load(1)
        w.y_axis_name_edit.setText('B default Gate')
        b_cube, b_fit = w.current_cube, self.commit(3.1)
        w.activate_dataset(a)
        self.assertIs(w.current_cube, a_processed)
        self.assertIs(w.fit_result, a_fit)
        self.assertEqual(w._processing_mode, 'energy_second_derivative')
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')):
            w.activate_dataset(b)
            w.activate_dataset(a)
        # Exercise the explicit synchronous axis interpretation helper.
        w.x_axis_override_combo.blockSignals(True)
        w.x_axis_override_combo.setCurrentIndex(w.x_axis_override_combo.findData('pixel'))
        w.x_axis_override_combo.blockSignals(False)
        with patch.object(w._annotation_settings, 'value', side_effect=AssertionError('runtime defaults read')):
            w._on_x_axis_override_changed()
        self.assertEqual(w.current_cube.x_override, 'pixel')
        self.assertEqual(w.y_axis_name_edit.text(), 'A Gate')
        self.assertIsNone(w.fit_result)
        w.activate_dataset(b)
        self.assertIs(w.current_cube, b_cube)
        self.assertIs(w.fit_result, b_fit)

    def test_staged_initialization_uses_detached_defaults_and_sidecar_failure_does_not_block_switch(self):
        w = self.window
        a = self.load(0)
        w.plot_title_edit.setText('request title')
        fingerprint = w._loaded_source_fingerprint
        defaults = w.snapshot_dataset_defaults()
        # A batch can construct candidates without changing the visible owner.
        from spectra_app.dat_io import load_dat_cube
        from spectra_app.plot_state import source_fingerprint
        prepared = (load_dat_cube(self.paths[1]), source_fingerprint(self.paths[1]), None, '')
        w.plot_title_edit.setText('later outgoing edit')
        candidate = w.initialize_dataset(self.paths[1], prepared, defaults)
        self.assertEqual(w.workspace.active_dataset_id, a)
        self.assertEqual(w.plot_title_edit.text(), 'later outgoing edit')
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
        self.assertIn('sidecar is read-only', w.statusBar().currentMessage())
        w.activate_dataset(a)
        self.assertEqual(w.plot_title_edit.text(), 'later outgoing edit')

    def test_switch_flushes_annotations_and_discards_transient_interactions(self):
        w = self.window
        a = self.load(0)
        b = self.load(1)
        w.activate_dataset(a)
        w.plot_title_edit.setText('last keystroke')
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
        self.assertEqual(w.plot_title_edit.text(), 'last keystroke')

    def test_returning_to_same_owner_rejects_blocked_old_result_and_error(self):
        w = self.window
        a = self.load(0)
        b = self.load(1)
        for fails in (False, True):
            w.activate_dataset(a)
            committed = self.commit(1.8)
            w._csv_seeds_map = {w._current_gate_index(): [2.0]}
            w._seeds_from_csv_radio.setChecked(True)
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
