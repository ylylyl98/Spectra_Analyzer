"""Project persistence observed through the real Qt workspace boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
from dataclasses import replace
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from spectra_app.main_window import MainWindow
from spectra_app.edit_history import EditCommand, FieldChange
from tests.settings_helpers import isolate_settings
from tests.background_helpers import wait_jobs
from tests.test_workflow import _fit_result

APP = QApplication.instance() or QApplication([])


class ProjectWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self, project_dialogs=True).parent
        self.windows = []
        self.paths = []
        for name in ('first', 'second'):
            folder = self.folder / name
            folder.mkdir()
            path = folder / 'sample.dat'
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{x:.12g} {x*x:.12g} {x**3:.12g} {x:.12g}'
                for x in np.linspace(1, 4, 31)), encoding='utf-8')
            self.paths.append(path)

    def tearDown(self):
        for window in self.windows:
            wait_jobs(window)
            with patch.object(window, '_confirm_project_discard', return_value=True, create=True):
                window.close()

    def window(self):
        window = MainWindow()
        window._show_error = lambda text: None
        self.windows.append(window)
        return window

    def test_graph_workspace_save_load_restores_only_open_set_and_closed_geometry(self):
        w = self.window()
        w.show()
        w._load_dat_file_direct(self.paths[0])
        owner = w.workspace.active_dataset_id
        w.open_graph_document()
        detail = w.create_graph('Closed detail').graph_id
        w.open_graph_document()
        last = w.create_graph('Overview').graph_id
        w.open_graph_document()
        rectangles = [QRect(20 + i * 45, 25 + i * 35, 380, 270) for i in range(3)]
        keys = [(owner, graph) for graph in ('default', detail, last)]
        for key, rect in zip(keys, rectangles):
            w.graph_documents.window_for(key).setGeometry(rect)
        w.graph_documents.close_document(keys[1])
        saved = w.save_project(self.folder / 'layout.spectra')
        self.assertFalse(w.isWindowModified())
        w.graph_documents.window_for(keys[2]).move(155, 180)
        w._refresh_project_modified()
        self.assertTrue(w.isWindowModified())
        w.graph_documents.window_for(keys[2]).setGeometry(rectangles[2])
        w._refresh_project_modified()
        self.assertFalse(w.isWindowModified())
        self.paths[0].unlink()
        restored = self.window()
        self.assertTrue(restored.open_project(saved))
        restored.show()
        APP.processEvents()
        self.assertEqual({item.key for item in restored.graph_documents.subWindowList()}, {keys[0], keys[2]})
        self.assertEqual(restored.graph_documents.window_for(keys[2]).geometry(), rectangles[2])
        self.assertEqual(restored.workspace.active.active_graph_id, last)
        restored._refresh_project_modified()
        self.assertFalse(restored.isWindowModified())
        restored.open_graph_document(*keys[1])
        self.assertEqual(restored.graph_documents.window_for(keys[1]).geometry(), rectangles[1])
        restored._refresh_project_modified()
        self.assertTrue(restored.isWindowModified())

    def test_all_closed_layout_stays_closed_on_load_and_plot_entry(self):
        w = self.window()
        w.show()
        w._load_dat_file_direct(self.paths[0])
        w.open_graph_document()
        key = w._document_key()
        rect = QRect(42, 55, 390, 280)
        w.graph_documents.window_for(key).setGeometry(rect)
        w.graph_documents.close_document(key)
        saved = w.save_project(self.folder / 'closed.spectra')
        restored = self.window()
        restored.open_project(saved)
        restored.show()
        restored.plot_tabs.setCurrentWidget(restored.graph_documents)
        APP.processEvents()
        self.assertEqual(restored.graph_documents.subWindowList(), [])
        self.assertFalse(restored.graph_command_host.isVisible())
        restored._refresh_project_modified()
        self.assertFalse(restored.isWindowModified())
        restored.open_graph_document()
        self.assertEqual(restored.graph_documents.window_for(key).geometry(), rect)

    def test_offscreen_saved_rectangle_is_reachable_without_shrinking_or_dirtying_on_show(self):
        from spectra_app.project_io import write_project
        w = self.window()
        w._load_dat_file_direct(self.paths[0])
        w.open_graph_document()
        workspace = w._capture_project_workspace()
        workspace.graph_workspace['documents'][0]['rect'] = [99999, -500, 560, 390]
        destination = self.folder / 'offscreen.spectra'
        write_project(destination, workspace)
        restored = self.window()
        restored.resize(1100, 720)
        restored.open_project(destination)
        restored.show()
        restored.plot_tabs.setCurrentWidget(restored.graph_documents)
        APP.processEvents()
        window = restored.graph_documents.activeSubWindow()
        self.assertEqual(window.size().toTuple(), (560, 390))
        self.assertTrue(restored.graph_documents.viewport().rect().contains(window.pos()))
        restored._refresh_project_modified()
        self.assertFalse(restored.isWindowModified())

    def test_analysis_save_keeps_each_dataset_selected_graph_and_open_document_owner(self):
        w = self.window()
        w._load_dat_file_direct(self.paths[0])
        first_id = w.workspace.active_dataset_id
        w.open_graph_document()
        w.create_graph('Selected first detail')
        selected = w.workspace.active.active_graph_id
        w.plot_tabs.setCurrentIndex(0)
        w._load_dat_file_direct(self.paths[1])
        second_id = w.workspace.active_dataset_id
        saved = w.save_project(self.folder / 'analysis-layout.spectra')
        restored = self.window()
        restored.open_project(saved)
        self.assertEqual(restored.workspace.active_dataset_id, second_id)
        self.assertEqual(restored.workspace.datasets[first_id].active_graph_id, selected)
        self.assertEqual(restored.workspace.datasets[second_id].active_graph_id, 'default')
        self.assertEqual({item.key for item in restored.graph_documents.subWindowList()},
                         {(first_id, 'default'), (first_id, selected)})
        self.assertFalse(restored.graph_command_host.isVisible())
        # Reopening the same file while currently in Plot must not show graph
        # A alongside dataset B's property editors or change B's selection.
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        with patch.object(w, '_confirm_project_discard', return_value=True):
            w.open_project(saved)
        self.assertIsNot(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertEqual(w.workspace.active_dataset_id, second_id)
        self.assertEqual(w.workspace.datasets[first_id].active_graph_id, selected)
        self.assertEqual(w.workspace.datasets[second_id].active_graph_id, 'default')
        self.assertFalse(w.graph_command_host.isVisible())

    def test_save_restart_embeds_two_owners_without_source_reads(self):
        w = self.window()
        for path in self.paths:
            w._load_dat_file_direct(path)
            w.peak_indices = np.array([4, 9])
            w._peak_gate_idx = 1
            w.fit_result = _fit_result(2)
            w._fit_gate_idx = 1
            w.batch_fit_result_map = {1: w.fit_result}
            w._csv_seeds_map = {1: [2.1]}
            w._csv_seeds_path = str(self.folder / 'missing.csv')
            w.analysis_text.setPlainText('stored result ' + path.parent.name)
        w.prom_spin.lineEdit().setText('0.127')
        w.processing_window_spin.lineEdit().setText('25')
        w.plot_title_edit.setText('Portable typed title')
        expected = {s.dataset_id: s.current_cube.z.copy() for s in w.workspace.datasets.values()}
        saved = w.save_project(self.folder / 'portable')
        self.assertEqual(saved.suffix, '.spectra')
        for path in self.paths:
            path.unlink()
        restored = self.window()
        with patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('source read')), \
             patch.object(restored._plot_state_store, 'load', side_effect=AssertionError('sidecar read')), \
             patch.object(restored, 'snapshot_dataset_defaults', side_effect=AssertionError('defaults')), \
             patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')), \
             patch('spectra_app.main_window.fit_multi_peak', side_effect=AssertionError('recompute')):
            self.assertTrue(restored.open_project(saved))
        self.assertEqual(restored.workspace.active_dataset_id, w.workspace.active_dataset_id)
        self.assertEqual(restored.prom_spin.value(), .127)
        self.assertEqual(restored.processing_window_spin.value(), 25)
        self.assertEqual(restored.plot_title_edit.text(), 'Portable typed title')
        for owner, session in restored.workspace.datasets.items():
            np.testing.assert_array_equal(session.current_cube.z, expected[owner])
            self.assertIs(session.base_cube, session.current_cube)
            self.assertFalse(session.sidecar_writes_enabled)
            self.assertIsNotNone(session.fit_result)
            self.assertEqual(session.csv_seeds_map, {1: [2.1]})
        event = QCloseEvent()
        with patch('spectra_app.main_window.QMessageBox.warning', side_effect=AssertionError('clean close prompt')):
            restored.closeEvent(event)
        self.assertTrue(event.isAccepted())

    def test_menu_shortcuts_preserve_dat_import_and_copy(self):
        w = self.window()
        expected = {'new_project': 'Ctrl+N', 'open_project': 'Ctrl+Shift+O',
                    'save_project': 'Ctrl+S', 'save_project_as': 'Ctrl+Shift+S',
                    'import': 'Ctrl+O', 'copy_graph': 'Ctrl+Alt+J'}
        for name, key in expected.items():
            self.assertEqual(w._graph_command_actions[name].shortcut().toString(), key)

    def test_native_save_names_preserve_unicode_and_spaces(self):
        from spectra_app.project_io import read_project
        w = self.loaded_window()
        folder = self.folder / '中文 项目'
        folder.mkdir()
        for entered, expected in (('无后缀 项目', '无后缀 项目.spectra'),
                                  ('指定 项目.spectra', '指定 项目.spectra')):
            with self.subTest(entered=entered):
                saved = w.save_project(folder / entered)
                self.assertEqual(saved, folder / expected)
                self.assertTrue(saved.exists())
                self.assertEqual(w._project_path, saved)
                np.testing.assert_array_equal(
                    read_project(saved).active.current_cube.z, w.current_cube.z)

    def test_new_project_save_dialog_defaults_to_spectra(self):
        w = self.loaded_window()
        entered = self.folder / 'chosen without extension'
        with patch('spectra_app.main_window.QFileDialog.getSaveFileName',
                   return_value=(str(entered), '')) as dialog:
            saved = w.save_project()
        self.assertEqual(Path(dialog.call_args.args[2]).name, 'Untitled.spectra')
        self.assertEqual(dialog.call_args.args[3], 'Spectra projects (*.spectra)')
        self.assertEqual(saved, self.folder / 'chosen without extension.spectra')
        self.assertTrue(saved.exists())

    def test_new_project_save_as_dialog_defaults_to_spectra(self):
        w = self.loaded_window()
        entered = self.folder / 'chosen.spectra'
        with patch('spectra_app.main_window.QFileDialog.getSaveFileName',
                   return_value=(str(entered), '')) as dialog:
            w._on_save_project(save_as=True)
        self.assertEqual(Path(dialog.call_args.args[2]).name, 'Untitled.spectra')
        self.assertEqual(dialog.call_args.args[3], 'Spectra projects (*.spectra)')
        self.assertEqual(w._project_path, entered)
        self.assertTrue(entered.exists())

    def test_open_dialog_accepts_native_and_legacy_projects(self):
        w = self.loaded_window()
        paths = [w.save_project(self.folder / name)
                 for name in ('native.spectra', 'legacy.spextra')]
        other = self.window()
        for path in paths:
            with self.subTest(path=path), patch(
                    'spectra_app.main_window.QFileDialog.getOpenFileName',
                    return_value=(str(path), '')) as dialog:
                other._on_open_project()
                self.assertEqual(dialog.call_args.args[3],
                                 'Spectra projects (*.spectra *.spextra)')
                self.assertEqual(other._project_path, path)
                np.testing.assert_array_equal(other.current_cube.z, w.current_cube.z)

    def test_legacy_project_keeps_saved_path_on_ctrl_s(self):
        from spectra_app.project_io import read_project
        w = self.loaded_window()
        entered = self.folder / '旧 项目.spextra'
        self.assertEqual(w.save_project(entered), entered)
        other = self.window()
        self.assertTrue(other.open_project(entered))
        other.plot_title_edit.setText('changed after legacy open')
        with patch('spectra_app.main_window.QFileDialog.getSaveFileName',
                   side_effect=AssertionError('existing project does not need a path')):
            other._on_save_project()
        self.assertEqual(other._project_path, entered)
        self.assertTrue(entered.exists())
        self.assertFalse(entered.with_name(entered.name + '.spectra').exists())
        self.assertEqual(read_project(entered).active.view.annotations['plot_title'],
                         'changed after legacy open')

    def test_legacy_project_save_as_suggests_spectra_without_renaming(self):
        w = self.loaded_window()
        legacy = w.save_project(self.folder / 'legacy.spextra')
        previous_bytes = legacy.read_bytes()
        with patch('spectra_app.main_window.QFileDialog.getSaveFileName',
                   return_value=('', '')) as dialog:
            w._on_save_project(save_as=True)
        self.assertEqual(Path(dialog.call_args.args[2]), self.folder / 'legacy.spectra')
        self.assertEqual(dialog.call_args.args[3], 'Spectra projects (*.spectra)')
        self.assertEqual(w._project_path, legacy)
        self.assertEqual(legacy.read_bytes(), previous_bytes)
        self.assertFalse((self.folder / 'legacy.spectra').exists())

    def test_pending_gate_xy_colors_and_formal_numbers_restore_effective_values(self):
        w = self.loaded_window()
        pending = {'plot_gate_spin': '2', 'xmin_spin': '1.4', 'xmax_spin': '3.6',
                   'ymin_spin': '0.25', 'ymax_spin': '1.75',
                   'vmin_spin': '1.2', 'vmax_spin': '14.2'}
        for name, text in pending.items():
            getattr(w, name).lineEdit().setText(text)
        w.plot_page.width_spin.lineEdit().setText('4.101')
        w.plot_page.text_spin.lineEdit().setText('11')
        w.plot_page.dpi_spin.lineEdit().setText('220')
        path = w.save_project(self.folder / 'typed.spextra')
        other = self.window()
        self.assertTrue(other.open_project(path))
        self.assertEqual(other.current_gate, 2)
        np.testing.assert_allclose(other._heat_ax.get_xlim(), (1.4, 3.6))
        np.testing.assert_allclose(other._heat_ax.get_ylim(), (.25, 1.75))
        np.testing.assert_allclose(other._heat_mesh.get_clim(), (1.2, 14.2))
        self.assertTrue(other.vmin_lock.isChecked())
        self.assertTrue(other.vmax_lock.isChecked())
        self.assertAlmostEqual(other.workspace.active.presentation.width_in, 4.101)
        self.assertEqual(other.workspace.active.presentation.text_pt, 11)
        self.assertEqual(other.workspace.active.presentation.heatmap_dpi, 220)

    def test_pending_gate_index_also_commits_before_save(self):
        w = self.loaded_window()
        w.plot_gate_index_spin.lineEdit().setText('3')
        path = w.save_project(self.folder / 'gate-index.spextra')
        other = self.window()
        self.assertTrue(other.open_project(path))
        self.assertEqual(other.current_gate, 2)
        self.assertEqual(other.plot_gate_index_spin.value(), 3)

    def loaded_window(self):
        w = self.window()
        w._load_dat_file_direct(self.paths[0])
        return w

    def test_guard_cancel_discard_and_save_canceled_or_failed_preserve_workspace(self):
        w = self.loaded_window()
        previous = w.workspace
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Cancel) as prompt:
            self.assertFalse(w.new_project())
        self.assertEqual(prompt.call_args.args[-1], QMessageBox.Cancel)
        self.assertIs(w.workspace, previous)
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Save), \
             patch('spectra_app.main_window.QFileDialog.getSaveFileName', return_value=('', '')):
            self.assertFalse(w.new_project())
        self.assertIs(w.workspace, previous)
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Save), \
             patch.object(w, 'save_project', side_effect=OSError('disk full')):
            self.assertFalse(w.new_project())
        self.assertIs(w.workspace, previous)
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Discard):
            self.assertTrue(w.new_project())
        self.assertFalse(w.workspace.datasets)

    def test_successful_guard_save_persists_latest_edits_before_new(self):
        from spectra_app.project_io import read_project
        w = self.loaded_window()
        path = w.save_project(self.folder / 'guard.spextra')
        owner = w.workspace.active_dataset_id
        w.plot_title_edit.setText('latest before New')
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Save):
            self.assertTrue(w.new_project())
        self.assertEqual(read_project(path).datasets[owner].view.annotations['plot_title'], 'latest before New')
        self.assertIsNone(w._project_path)

    def test_write_failure_preserves_baseline_path_file_and_source_ownership(self):
        w = self.loaded_window()
        source = w.workspace.active
        with patch('spectra_app.project_io.write_project', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                w.save_project(self.folder / 'failed.spextra')
        self.assertTrue(source.sidecar_writes_enabled)
        self.assertIsNone(w._project_path)
        path = w.save_project(self.folder / 'saved.spextra')
        old_bytes, digest = path.read_bytes(), w._project_saved_digest
        w.prom_spin.setValue(.2)
        with patch('spectra_app.project_io.write_project', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                w.save_project(self.folder / 'different.spextra')
        self.assertEqual((w._project_path, w._project_saved_digest), (path, digest))
        self.assertEqual(path.read_bytes(), old_bytes)

    def test_invalid_edit_blocks_save_but_explicit_discard_can_close(self):
        w = self.loaded_window()
        w.prom_spin.lineEdit().setText('not a number')
        with self.assertRaisesRegex(ValueError, 'numeric'):
            w.save_project(self.folder / 'invalid.spextra')
        event = QCloseEvent()
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Cancel) as prompt:
            w.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertIn('cannot be saved', prompt.call_args.args[2])
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Discard):
            w.closeEvent(event)
        self.assertTrue(event.isAccepted())

    def test_invalid_committed_mapping_save_preserves_previous_file_and_baseline(self):
        w = self.loaded_window()
        path = w.save_project(self.folder / 'valid-mapping.spextra')
        previous_bytes, baseline = path.read_bytes(), w._project_saved_digest
        w.mapping_combo.setCurrentIndex(w.mapping_combo.findData('log'))
        w.vmin_spin.lineEdit().setText('-1')
        with self.assertRaisesRegex(ValueError, 'sample.dat.*Log color limits'):
            w.save_project()
        self.assertEqual(path.read_bytes(), previous_bytes)
        self.assertEqual((w._project_path, w._project_saved_digest), (path, baseline))
        self.assertLess(w.vmin_spin.value(), 0)

    def test_malformed_and_failed_install_keep_previous_model_path_baseline(self):
        w = self.loaded_window()
        path = w.save_project(self.folder / 'saved.spextra')
        previous, baseline = w.workspace, w._project_saved_digest
        bad = self.folder / 'malformed.spextra'
        bad.write_bytes(b'not an archive')
        with patch.object(w, '_confirm_project_discard', side_effect=AssertionError('guard before decode')):
            with self.assertRaises(ValueError):
                w.open_project(bad)
        self.assertIs(w.workspace, previous)
        with patch.object(w, 'activate_dataset', side_effect=RuntimeError('projection failure')):
            with self.assertRaisesRegex(RuntimeError, 'projection failure'):
                w.open_project(path)
        self.assertIs(w.workspace, previous)
        self.assertEqual((w._project_path, w._project_saved_digest), (path, baseline))
        self.assertIsNotNone(w._heat_mesh)
        self.assertEqual(w.imported_dataset_list.count(), 1)

    def test_failed_projection_preserves_processed_owner_history_and_noop_generate(self):
        w = self.loaded_window()
        session = w.workspace.active
        session.current_cube = replace(session.base_cube, z=-session.base_cube.z)
        session.applied.mode = 'energy_second_derivative'
        session.applied.original_signal_title = 'Intensity'
        session.pending = replace(session.applied)
        w.restore_dataset_controls(session)
        w._plot_cube()
        result = _fit_result(2)
        session.fit_result, session.fit_gate_idx = result, 1
        w._applied_derivative_sources[session.dataset_id] = (id(session.base_cube), id(session.current_cube))
        path = w.save_project(self.folder / 'old-processed.spextra')
        history = w._history_for(session)
        command = EditCommand('prior visual edit', (
            FieldChange(('view', 'annotations', 'plot_title'), 'before', 'after'),))
        history.record(command)
        with patch.object(w, 'activate_dataset', side_effect=RuntimeError('projection failure')):
            with self.assertRaises(RuntimeError):
                w.open_project(path)
        self.assertIs(w._history_for(session), history)
        self.assertEqual(history.undo_stack, [command])
        self.assertEqual(w._applied_derivative_sources[session.dataset_id],
                         (id(session.base_cube), id(session.current_cube)))
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')):
            w._on_generate_heatmap()
        self.assertIs(session.fit_result, result)
        self.assertFalse(w._background_jobs.busy)

    def test_open_cancel_keeps_current_work_after_validating_candidate(self):
        w = self.loaded_window()
        path = w.save_project(self.folder / 'saved.spextra')
        previous = w.workspace
        w.prom_spin.setValue(.4)
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Cancel):
            self.assertFalse(w.open_project(path))
        self.assertIs(w.workspace, previous)
        self.assertEqual(w.prom_spin.value(), .4)

    def test_internal_plot_value_error_rolls_back_instead_of_installing_partial_view(self):
        w = self.loaded_window()
        w.fit_result = _fit_result(2)
        w._fit_gate_idx = 1
        path = w.save_project(self.folder / 'saved.spextra')
        previous, cube, result = w.workspace, w.current_cube, w.fit_result
        baseline = w._project_saved_digest
        original = w._plot_cube_impl
        calls = []
        def fail_once():
            calls.append(True)
            if len(calls) == 1:
                raise ValueError('injected plot failure')
            original()
        with patch.object(w, '_plot_cube_impl', side_effect=fail_once):
            with self.assertRaisesRegex(ValueError, 'injected plot failure'):
                w.open_project(path)
        self.assertIs(w.workspace, previous)
        self.assertIs(w.current_cube, cube)
        self.assertIs(w.fit_result, result)
        self.assertEqual(w._project_saved_digest, baseline)
        self.assertFalse(w._project_installing)
        self.assertIsNotNone(w._heat_mesh)

    def test_project_owner_suppresses_both_sidecars_and_source_axis_routes(self):
        w = self.loaded_window()
        w._load_dat_file_direct(self.paths[1])
        w.save_project(self.folder / 'saved.spextra')
        with patch.object(w._plot_state_store, 'save', side_effect=AssertionError('sidecar write')), \
             patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('axis source read')), \
             patch('spectra_app.main_window.prepare_dat', side_effect=AssertionError('async source read')):
            self.assertEqual(w._save_current_plot_state(), '')
            for session in w.workspace.datasets.values():
                w._save_plot_dataset(session)
            w._on_x_axis_override_changed()
            w._on_x_axis_override_async()
            w.activate_dataset(next(iter(w.workspace.datasets)))
        self.assertFalse(w._background_jobs.busy)
        w._set_dat_import_pending(True)
        w._set_dat_import_pending(False)
        w._refresh_processing_controls()
        self.assertFalse(w.x_axis_override_combo.isEnabled())
        self.assertIn('new project', w.x_axis_override_combo.toolTip())

    def test_close_busy_cancel_consumes_retry_and_later_job_does_not_close(self):
        w = self.loaded_window()
        w.show()
        release = threading.Event()
        self.addCleanup(release.set)
        w._start_background_job(lambda context: release.wait(5), lambda result: None, 'test')
        with self.assertRaises(ValueError):
            w.save_project(self.folder / 'busy.spextra')
        with self.assertRaises(ValueError):
            w.new_project()
        with patch('spectra_app.project_io.read_project', side_effect=AssertionError('busy read')):
            with self.assertRaises(ValueError):
                w.open_project(self.folder / 'busy.spextra')
        event = QCloseEvent()
        w.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(w._close_pending)
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Cancel), \
             patch.object(w, 'close', wraps=w.close) as close:
            release.set()
            wait_jobs(w)
            QTest.qWait(15)
            self.assertFalse(w._close_pending)
            self.assertEqual(close.call_count, 1)
            self.assertTrue(w.isVisible())
            w._start_background_job(lambda context: None, lambda result: None, 'later')
            wait_jobs(w)
            QTest.qWait(15)
            self.assertEqual(close.call_count, 1)

    def test_new_clears_visible_science_preview_histories_and_imports_again(self):
        w = self.loaded_window()
        w.save_project(self.folder / 'saved.spextra')
        w.imported_dataset_search.setText('sample')
        w._render_plot_page()
        self.assertTrue(w.figure.axes)
        self.assertIsNotNone(w.plot_page.rendered_page)
        w._cached_d2 = (np.arange(2), np.arange(2))
        old_generation = w._data_generation
        self.assertTrue(w.new_project())
        self.assertEqual(w.figure.axes, [])
        self.assertIsNone(w._heat_mesh)
        self.assertIsNone(w._spec_ax)
        self.assertEqual(w.plot_page.preview.svg_bytes, b'')
        self.assertIsNone(w.plot_page.preview.snapshot)
        self.assertEqual(w.analysis_text.toPlainText(), '')
        self.assertEqual(w.plot_gate_spin.value(), 0)
        self.assertEqual(w._csv_path_label.text(), '(none loaded)')
        self.assertFalse(w._csv_seeds_row.isVisible())
        self.assertEqual(w.imported_dataset_list.count(), 0)
        self.assertEqual(w.imported_dataset_search.text(), '')
        self.assertEqual(w._edit_histories, {})
        self.assertEqual(w._applied_derivative_sources, {})
        self.assertIsNone(w._cached_d2)
        self.assertGreater(w._data_generation, old_generation)
        w._import_dat_files([self.paths[1]])
        wait_jobs(w)
        self.assertIsNotNone(w._heat_mesh)
        self.assertEqual(w.current_dat_path, self.paths[1])
        self.assertTrue(w.x_axis_override_combo.isEnabled())

    def test_restored_processing_ownership_pairs_and_pending_controls_are_distinct(self):
        w = self.loaded_window()
        session = w.workspace.active
        session.current_cube = replace(session.base_cube, z=-session.base_cube.z)
        session.applied.mode = 'energy_second_derivative'
        session.applied.original_signal_title = 'Intensity'
        w._plot_cube()
        w.processing_mode_combo.setCurrentIndex(w.processing_mode_combo.findData('original'))
        path = w.save_project(self.folder / 'processed.spextra')
        other = self.window()
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')):
            self.assertTrue(other.open_project(path))
        restored = other.workspace.active
        self.assertEqual(restored.applied.mode, 'energy_second_derivative')
        self.assertEqual(restored.pending.mode, 'original')
        self.assertEqual(other._applied_derivative_sources[restored.dataset_id],
                         (id(restored.base_cube), id(restored.current_cube)))
        self.assertIsNot(restored.base_cube, restored.current_cube)

    def test_all_nan_processed_array_and_existing_fit_restore_without_recompute(self):
        w = self.loaded_window()
        session = w.workspace.active
        session.current_cube = replace(session.base_cube, z=np.full_like(session.base_cube.z, np.nan))
        session.applied.mode = 'energy_second_derivative'
        session.applied.original_signal_title = 'Intensity'
        session.pending = replace(session.applied)
        w.restore_dataset_controls(session)
        w._plot_cube()
        session.fit_result, session.fit_gate_idx = _fit_result(2), 1
        path = w.save_project(self.folder / 'missing-values.spextra')
        other = self.window()
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')):
            self.assertTrue(other.open_project(path))
        self.assertTrue(np.isnan(other.current_cube.z).all())
        self.assertIsNotNone(other.fit_result)
        self.assertEqual(other._processing_mode, 'energy_second_derivative')

    def test_project_export_folder_numbering_batch_csv_and_comparison_provenance(self):
        w = self.loaded_window()
        w.peak_indices = np.array([4])
        w._peak_gate_idx = w._current_gate_index()
        w.fit_result = _fit_result(2)
        w._fit_gate_idx = w._current_gate_index()
        w.batch_fit_result_map = {w._fit_gate_idx: w.fit_result}
        project_folder = self.folder / 'projects'
        project_folder.mkdir()
        w.save_project(project_folder / 'saved.spextra')
        (project_folder / 'sample_Graph 1_export01.svg').write_text('reserved')
        self.assertEqual(w._heatmap_export_filename('svg'), 'sample_Graph 1_export02.svg')
        w._render_plot_page()
        saved = w.export_plot_page('svg')
        self.assertEqual(saved.parent, project_folder)
        defaults = []
        def choose(_window, _title, default, _filter):
            defaults.append(Path(default))
            return default, ''
        with patch('spectra_app.main_window.QFileDialog.getSaveFileName', side_effect=choose):
            w._on_save_peaks_csv()
            w._on_export_current_fit_csv()
            w._on_export_all_fit_csv()
            w._on_export_spectrum_csv()
        self.assertEqual(len(defaults), 4, defaults)
        self.assertTrue(all(path.parent == project_folder and path.exists() for path in defaults))
        comparison = w.open_plot_comparison()
        with patch('spectra_app.plot_comparison.QFileDialog.getSaveFileName', return_value=('', '')) as dialog:
            comparison._choose_export('svg')
        self.assertEqual(Path(dialog.call_args.args[2]).parent, project_folder)
        self.assertIn(str(self.paths[0]), comparison.source_label.text())
        w.start_plot_batch([w.workspace.active_dataset_id], formats=('svg',))
        self.assertEqual(w._plot_batch_basenames[0].parent, project_folder)
        while not w._plot_batch.done:
            w._step_plot_batch()
        self.assertTrue(all(output.path.parent == project_folder
                            for result in w.plot_batch_summary.results for output in result.outputs))


if __name__ == '__main__':
    unittest.main()
