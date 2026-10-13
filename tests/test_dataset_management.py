"""Dataset management through workspace, Qt and saved-project boundaries."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from spectra_app.dataset_state import DatasetSession, Workspace
from spectra_app.main_window import MainWindow
from tests.background_helpers import wait_jobs
from tests.settings_helpers import isolate_settings
from tests.test_workflow import _fit_result

APP = QApplication.instance() or QApplication([])


class WorkspaceRemovalTests(unittest.TestCase):
    def test_remove_retires_path_and_selects_next_then_previous(self):
        workspace = Workspace()
        a, b, c = [workspace.register(DatasetSession(source_path=Path(f'{name}.dat')))
                   for name in ('a', 'b', 'c')]
        workspace.active_dataset_id = b.dataset_id
        self.assertIs(workspace.remove(b.dataset_id), b)
        self.assertIs(workspace.active, c)
        self.assertIsNone(workspace.dataset_for_path(b.source_path))
        replacement = workspace.register(DatasetSession(source_path=b.source_path))
        self.assertIsNot(replacement, b)
        workspace.remove(replacement.dataset_id)
        self.assertIs(workspace.active, c)
        workspace.remove(c.dataset_id)
        self.assertIs(workspace.active, a)
        workspace.remove(a.dataset_id)
        self.assertIsNone(workspace.active_dataset_id)
        self.assertEqual(workspace.path_index, {})


class DatasetManagementTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self, project_dialogs=True).parent
        self.windows, self.errors, self.paths = [], [], []
        for name in ('first', 'second', 'third'):
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
            with patch.object(window, '_confirm_project_discard', return_value=True):
                window.close()
            window.deleteLater()
        APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def window(self):
        window = MainWindow()
        window._show_error = self.errors.append
        self.windows.append(window)
        return window

    def loaded(self, count=3):
        window = self.window()
        for path in self.paths[:count]:
            window._load_dat_file_direct(path)
        return window, list(window.workspace.datasets.values())

    def remove(self, window, dataset_id):
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Yes):
            return window.remove_dataset(dataset_id)

    def menu_choice(self, callback):
        failures = []
        def choose():
            menu = APP.activePopupWidget()
            try:
                self.assertIsInstance(menu, QMenu)
                callback(menu)
            except BaseException as exc:
                failures.append(exc)
            finally:
                if menu is not None:
                    menu.close()
        QTimer.singleShot(0, choose)
        return failures

    def test_inactive_remove_preserves_drafts_results_view_and_history(self):
        w, (a, b, c) = self.loaded()
        w.set_plot_presentation(replace(c.presentation, text_pt=c.presentation.text_pt + 1))
        c.fit_result = _fit_result(2.0)
        c.fit_gate_idx = 1
        c.fit_export_rows = [{'gate': '1', 'center': '2'}]
        w.plot_gate_index_spin.setValue(1)
        w.plot_title_edit.setText('active pending title')
        w.prom_spin.lineEdit().setText('0.127')
        w.analysis_text.setPlainText('retained analysis')
        history = w._edit_histories[c.dataset_id]
        commands = tuple(history.undo_stack)
        pending = w._pending_history_edit
        limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim()
        rendered = w.plot_page.rendered_page
        self.assertTrue(self.remove(w, a.dataset_id))
        self.assertIs(w.workspace.active, c)
        self.assertEqual(w.plot_title_edit.text(), 'active pending title')
        self.assertEqual(w.prom_spin.lineEdit().text(), '0.127')
        self.assertEqual(w.analysis_text.toPlainText(), 'retained analysis')
        self.assertEqual(w.plot_gate_index_spin.value(), 1)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim()), limits)
        self.assertIs(w.plot_page.rendered_page, rendered)
        self.assertIs(w._pending_history_edit, pending)
        self.assertIs(w._edit_histories[c.dataset_id], history)
        self.assertEqual(tuple(history.undo_stack), commands)
        self.assertIsNotNone(c.fit_result)
        self.assertEqual(c.fit_export_rows, [{'gate': '1', 'center': '2'}])
        for cache in (w._edit_histories, w._plot_revisions, w._plot_cube_owners,
                      w._applied_derivative_sources):
            self.assertNotIn(a.dataset_id, cache)

    def test_active_remove_selects_next_then_previous_without_control_leakage(self):
        w, (a, b, c) = self.loaded()
        for index, session in enumerate((a, b, c)):
            w.activate_dataset(session.dataset_id)
            w.plot_title_edit.setText(f'owner {index}')
            w.prom_spin.setValue(.1 + index / 10)
            w.analysis_text.setPlainText(f'summary {index}')
            w.capture_active_dataset(render=False)
        w.activate_dataset(b.dataset_id)
        w.plot_title_edit.setText('removed draft')
        w.prom_spin.setValue(.9)
        w._lasso_mode = True
        self.assertTrue(self.remove(w, b.dataset_id))
        self.assertIs(w.workspace.active, c)
        self.assertEqual(w.plot_title_edit.text(), 'owner 2')
        self.assertAlmostEqual(w.prom_spin.value(), .3)
        self.assertEqual(w.analysis_text.toPlainText(), 'summary 2')
        self.assertFalse(w._lasso_mode)
        self.assertFalse(w._annotation_edit_timer.isActive())
        self.assertIsNone(w._pending_history_edit)
        QTest.qWait(300)
        self.assertEqual(c.view.annotations['plot_title'], 'owner 2')
        self.assertTrue(self.remove(w, c.dataset_id))
        self.assertIs(w.workspace.active, a)
        self.assertEqual(w.plot_title_edit.text(), 'owner 0')
        self.assertAlmostEqual(w.prom_spin.value(), .1)

    def test_cancel_has_no_mutation_and_names_the_source(self):
        w, (a, b, c) = self.loaded()
        w.plot_title_edit.setText('uncommitted')
        before = {path: path.read_bytes() for path in self.folder.rglob('*') if path.is_file()}
        pending = w._pending_history_edit
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Cancel) as prompt:
            self.assertFalse(w.remove_dataset(b.dataset_id))
        message = prompt.call_args.args[2]
        self.assertIn(str(b.source_path), message)
        self.assertIn('analysis', message.lower())
        self.assertIn('original DAT', message)
        self.assertEqual(list(w.workspace.datasets), [a.dataset_id, b.dataset_id, c.dataset_id])
        self.assertIs(w.workspace.active, c)
        self.assertIs(w._pending_history_edit, pending)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_last_removal_is_dirty_empty_and_exact_guard_save_reopens_without_source(self):
        w, (a,) = self.loaded(1)
        destination = self.folder / 'projects'
        destination.mkdir()
        project = w.save_project(destination / 'empty later.spectra')
        saved_digest, saved_stamp = w._project_saved_digest, w._project_saved_stamp
        w.fix_processing_chk.setChecked(True)
        w.fix_regions_chk.setChecked(True)
        w.fix_region_edges_chk.setChecked(True)
        w.cmap_combo.setCurrentIndex(w.cmap_combo.findData('magma'))
        w.prom_spin.lineEdit().setText('')
        w.plot_page.font_edit.setText('removed font draft')
        source_files = {path: path.read_bytes() for path in a.source_path.parent.iterdir() if path.is_file()}
        self.assertTrue(self.remove(w, a.dataset_id))
        self.assertEqual(w._project_path, project)
        self.assertEqual(w._project_saved_digest, saved_digest)
        self.assertEqual(w._project_saved_stamp, saved_stamp)
        self.assertTrue(w.isWindowModified())
        self.assertIn('*', w.windowTitle())
        self.assertEqual(w._output_folder(), destination)
        self.assertIsNone(w.current_cube)
        self.assertEqual(w.figure.axes, [])
        self.assertIsNone(w.plot_page.rendered_page)
        self.assertIsNone(w.plot_page.preview.snapshot)
        self.assertEqual(w.analysis_text.toPlainText(), '')
        self.assertEqual(w.plot_title_edit.text(), '')
        self.assertFalse(w.fix_processing_chk.isChecked())
        self.assertFalse(w.fix_regions_chk.isChecked())
        self.assertFalse(w.fix_region_edges_chk.isChecked())
        self.assertEqual(w._selected_colormap(), w._unregistered_session.view.colormap)
        self.assertTrue(w.prom_spin.hasAcceptableInput())
        self.assertFalse(w.plot_page.export_svg_btn.isEnabled())
        self.assertFalse(w._graph_command_actions['copy_graph'].isEnabled())
        self.assertEqual(w._edit_histories, {})
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Cancel) as guard:
            self.assertFalse(w.new_project())
        self.assertEqual(guard.call_count, 1)
        self.assertEqual(w._project_path, project)
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Save):
            self.assertTrue(w._confirm_project_discard())
        self.assertFalse(w.isWindowModified())
        self.assertEqual(source_files, {path: path.read_bytes() for path in source_files})
        a.source_path.unlink()
        restored = self.window()
        with patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('source read')):
            self.assertTrue(restored.open_project(project))
        self.assertEqual(restored.workspace.datasets, {})
        self.assertIsNone(restored.workspace.active)
        self.assertFalse(restored.isWindowModified())
        self.assertEqual(restored._output_folder(), destination)

    def test_busy_import_analysis_and_batch_reject_without_prompt(self):
        w, (a,) = self.loaded(1)
        with patch('spectra_app.main_window.QMessageBox.warning', side_effect=AssertionError('busy prompt')):
            w._dat_import_pending = True
            with self.assertRaisesRegex(ValueError, 'Wait'):
                w.remove_dataset(a.dataset_id)
            w._dat_import_pending = False
            with patch.object(type(w._background_jobs), 'busy', new=property(lambda self: True)):
                with self.assertRaisesRegex(ValueError, 'Wait'):
                    w.remove_dataset(a.dataset_id)
            from types import SimpleNamespace
            w._plot_batch = SimpleNamespace(done=False)
            with self.assertRaisesRegex(ValueError, 'Wait'):
                w.remove_dataset(a.dataset_id)
            w._plot_batch = None
        self.assertIs(w.workspace.active, a)

    def test_busy_recheck_after_confirmation_keeps_dataset(self):
        w, (a,) = self.loaded(1)
        def choice(*args):
            w._dat_import_pending = True
            return QMessageBox.Yes
        try:
            with patch('spectra_app.main_window.QMessageBox.warning', side_effect=choice):
                with self.assertRaisesRegex(ValueError, 'Wait'):
                    w.remove_dataset(a.dataset_id)
            self.assertIs(w.workspace.active, a)
        finally:
            w._dat_import_pending = False

    def test_remove_reimport_allows_same_path_and_keeps_original_bytes(self):
        w, (a,) = self.loaded(1)
        original = a.source_path.read_bytes()
        self.assertTrue(self.remove(w, a.dataset_id))
        w._import_dat_files([a.source_path])
        wait_jobs(w)
        replacement = w.workspace.dataset_for_path(a.source_path)
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement.dataset_id, a.dataset_id)
        self.assertEqual(a.source_path.read_bytes(), original)

    def test_async_import_into_saved_project_suppresses_sidecars_before_activation(self):
        w, (a,) = self.loaded(1)
        destination = self.folder / '项目 export'
        destination.mkdir()
        w.save_project(destination / 'test.spectra')
        observed = []
        activate = w.activate_dataset
        def project(dataset_id, **kwargs):
            session = w.workspace.datasets[dataset_id]
            observed.append((session.sidecar_writes_enabled, w._output_folder(session)))
            return activate(dataset_id, **kwargs)
        with patch.object(w, 'activate_dataset', side_effect=project), \
             patch.object(w._plot_state_store, 'save', side_effect=AssertionError('source sidecar write')):
            w._import_dat_files([self.paths[1]])
            wait_jobs(w)
            self.assertFalse(w._dat_import_pending)
            b = w.workspace.active
            self.assertEqual(b.source_path, self.paths[1])
            w.plot_title_edit.setText('project new import')
            w.capture_active_dataset(render=False)
            w._save_current_plot_state()
            w.activate_dataset(a.dataset_id)
            w.activate_dataset(b.dataset_id)
        self.assertEqual(observed, [(False, destination)] * 3)
        self.assertFalse(self.paths[1].with_name('sample.dat.plot.json').exists())
        self.assertFalse(b.sidecar_writes_enabled)
        self.assertFalse(w.x_axis_override_combo.isEnabled())
        w._refresh_project_modified()
        self.assertTrue(w.isWindowModified())

    def test_async_import_into_empty_saved_project_and_unsaved_compatibility(self):
        w = self.window()
        destination = self.folder / 'project'
        destination.mkdir()
        w.save_project(destination / 'blank.spectra')
        w._import_dat_files([self.paths[0]])
        wait_jobs(w)
        self.assertFalse(w.workspace.active.sidecar_writes_enabled)
        self.assertEqual(w._output_folder(), destination)
        ordinary = self.window()
        ordinary._import_dat_files([self.paths[1]])
        wait_jobs(ordinary)
        self.assertTrue(ordinary.workspace.active.sidecar_writes_enabled)
        self.assertEqual(ordinary._output_folder(), self.paths[1].parent)
        ordinary.plot_title_edit.setText('source owned')
        ordinary.capture_active_dataset(render=False)
        ordinary._save_current_plot_state()
        self.assertTrue(self.paths[1].with_name('sample.dat.plot.json').exists())

    def test_context_targets_filtered_inactive_item_and_right_click_preserves_owner(self):
        w, (a, b, c) = self.loaded()
        w.imported_dataset_search.setText('second')
        w.show()
        APP.processEvents()
        item = w.imported_dataset_list.item(1)
        position = w.imported_dataset_list.visualItemRect(item).center()
        menus = []
        def choose(menu):
            menus.append([action.text() for action in menu.actions() if not action.isSeparator()])
            next(action for action in menu.actions() if action.text() == 'Copy Source Path').trigger()
        QTest.mouseClick(w.imported_dataset_list.viewport(), Qt.RightButton, pos=position)
        self.assertIs(w.workspace.active, c)
        failures = self.menu_choice(choose)
        event = QContextMenuEvent(QContextMenuEvent.Mouse, position,
                                 w.imported_dataset_list.viewport().mapToGlobal(position))
        QApplication.sendEvent(w.imported_dataset_list.viewport(), event)
        self.assertEqual(failures, [])
        self.assertTrue(menus)
        self.assertEqual(menus[-1], ['Copy Source Path', 'Open Source Folder',
                                    'Open Export Folder', 'Remove from Project'])
        self.assertEqual(APP.clipboard().text(), str(b.source_path))
        self.assertIs(w.workspace.active, c)
        with patch('spectra_app.main_window.QMenu', side_effect=AssertionError('blank menu')):
            w._show_dataset_context_menu(QPoint(5, w.imported_dataset_list.viewport().height() - 2))
        failures = self.menu_choice(lambda menu: next(action for action in menu.actions()
                              if action.text() == 'Remove from Project').trigger())
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Yes):
            w._show_dataset_context_menu(position)
        self.assertEqual(failures, [])
        self.assertNotIn(b.dataset_id, w.workspace.datasets)
        self.assertIs(w.workspace.active, c)

    def test_context_busy_remove_disabled_and_unicode_urls_missing_paths(self):
        w = self.window()
        unicode_folder = self.folder / '数据 with spaces'
        unicode_folder.mkdir()
        source = unicode_folder / 'missing 数据.dat'
        source.write_bytes(self.paths[0].read_bytes())
        w._load_dat_file_direct(source)
        a = w.workspace.active
        output = self.folder / 'project output'
        output.mkdir()
        w.save_project(output / 'folder.spectra')
        source.unlink()
        w._copy_dataset_source_path(a.dataset_id)
        self.assertEqual(APP.clipboard().text(), str(a.source_path))
        with patch('spectra_app.main_window.QDesktopServices.openUrl', return_value=True) as opened:
            self.assertTrue(w._open_dataset_folder(a.dataset_id))
        self.assertTrue(opened.call_args.args[0].isLocalFile())
        self.assertEqual(opened.call_args.args[0].toLocalFile(), str(unicode_folder).replace('\\', '/'))
        with patch('spectra_app.main_window.QDesktopServices.openUrl', return_value=True) as opened:
            self.assertTrue(w._open_dataset_folder(a.dataset_id, export=True))
        self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), output)
        with patch('spectra_app.main_window.QDesktopServices.openUrl', return_value=False):
            self.assertFalse(w._open_dataset_folder(a.dataset_id, export=True))
        self.assertIn('Could not open', self.errors[-1])
        missing = self.folder / 'unavailable'
        a.source_path = missing / 'source.dat'
        with patch('spectra_app.main_window.QDesktopServices.openUrl', side_effect=AssertionError('missing folder')):
            self.assertFalse(w._open_dataset_folder(a.dataset_id))
        self.assertIn(str(missing), self.errors[-1])
        self.assertFalse(missing.exists())
        w._dat_import_pending = True
        def inspect(menu):
            remove = next(action for action in menu.actions() if action.text() == 'Remove from Project')
            self.assertFalse(remove.isEnabled())
        try:
            failures = self.menu_choice(inspect)
            w._show_dataset_context_menu(w.imported_dataset_list.visualItemRect(
                w.imported_dataset_list.item(0)).center())
            self.assertEqual(failures, [])
        finally:
            w._dat_import_pending = False

    def test_frozen_comparison_survives_removal_with_identical_hybrid_svg(self):
        w, (a,) = self.loaded(1)
        comparison = w.open_plot_comparison()
        svg = comparison.preview.svg_bytes
        self.assertIn(b'<image', svg)
        self.assertIn(b'<text', svg)
        self.assertTrue(self.remove(w, a.dataset_id))
        self.assertTrue(comparison.isVisible())
        self.assertEqual(comparison.preview.svg_bytes, svg)
        comparison.close()
        comparison.deleteLater()

    def test_inactive_removed_preview_owner_is_released_without_capturing_active_draft(self):
        w, (a, b, c) = self.loaded()
        w.activate_dataset(a.dataset_id)
        w._capture_formal_source()
        self.assertEqual(w.plot_page.preview.snapshot.owner_token[0], a.dataset_id)
        w.activate_dataset(c.dataset_id)
        w.plot_title_edit.setText('retained draft')
        pending = w._pending_history_edit
        self.assertTrue(self.remove(w, a.dataset_id))
        self.assertIsNone(w.plot_page.preview.snapshot)
        self.assertEqual(w.plot_title_edit.text(), 'retained draft')
        self.assertIs(w._pending_history_edit, pending)
        self.assertIs(w.workspace.active, c)

    def test_direct_import_and_replacement_in_saved_project_keep_project_ownership(self):
        w, (a,) = self.loaded(1)
        project = w.save_project(self.folder / 'direct.spectra')
        with patch.object(w._plot_state_store, 'save', side_effect=AssertionError('sidecar write')):
            w._load_dat_file_direct(self.paths[1])
            b = w.workspace.active
            owner = b.dataset_id
            self.assertFalse(b.sidecar_writes_enabled)
            w._load_dat_file_direct(self.paths[1])
            self.assertEqual(w.workspace.active_dataset_id, owner)
            self.assertIsNot(w.workspace.active, b)
            self.assertFalse(w.workspace.active.sidecar_writes_enabled)
            self.assertEqual(w._output_folder(), project.parent)

    def test_canceled_import_and_analysis_callbacks_cannot_resurrect_removed_owner(self):
        from spectra_app import background_jobs
        w, (a, b) = self.loaded(2)
        started, release = Event(), Event()
        prepare = background_jobs.prepare_dat
        def blocked_import(request, context):
            started.set()
            release.wait(3)
            return prepare(request, context)
        try:
            with patch.object(background_jobs, 'prepare_dat', side_effect=blocked_import):
                w._import_dat_files([self.paths[2]])
                self.assertTrue(started.wait(2))
                stale_import = next(iter(w._background_jobs._jobs.values()))[0]
                with self.assertRaisesRegex(ValueError, 'Wait'):
                    w.remove_dataset(b.dataset_id)
                w._cancel_background_job()
                with self.assertRaisesRegex(ValueError, 'Wait'):
                    w.remove_dataset(b.dataset_id)
                release.set()
                wait_jobs(w)
        finally:
            release.set()
            wait_jobs(w)
        self.assertTrue(self.remove(w, b.dataset_id))
        stale_import(([b], []))
        self.assertEqual(list(w.workspace.datasets), [a.dataset_id])
        self.assertIs(w.workspace.active, a)
        w._csv_seeds_map = {w._current_gate_index(): [2.0]}
        w._seeds_from_csv_radio.setChecked(True)
        started.clear()
        release.clear()
        def blocked_fit(*args, **kwargs):
            started.set()
            release.wait(3)
            return _fit_result(2.0)
        try:
            with patch('spectra_app.main_window.fit_multi_peak', side_effect=blocked_fit):
                w._on_fit()
                self.assertTrue(started.wait(2))
                callbacks = next(iter(w._background_jobs._jobs.values()))
                with self.assertRaisesRegex(ValueError, 'Wait'):
                    w.remove_dataset(a.dataset_id)
                w._cancel_background_job()
                release.set()
                wait_jobs(w)
        finally:
            release.set()
            wait_jobs(w)
        self.assertTrue(self.remove(w, a.dataset_id))
        callbacks[0](_fit_result(3.3))
        callbacks[1](ValueError('stale analysis error'))
        self.assertEqual(w.workspace.datasets, {})
        self.assertIsNone(w.fit_result)
        self.assertEqual(w._unregistered_session.fit_export_rows, [])
        self.assertEqual(w.analysis_text.toPlainText(), '')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
