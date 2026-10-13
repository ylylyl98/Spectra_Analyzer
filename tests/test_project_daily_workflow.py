"""Daily project entry, preferences and modification status through real Qt routes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from spectra_app.main_window import MainWindow
from spectra_app.dataset_state import detached_graph_session
from spectra_app.plot_dialogs import PlotTargetsDialog
from tests.settings_helpers import isolate_settings
from tests.background_helpers import wait_jobs
from tests.test_workflow import _fit_result

APP = QApplication.instance() or QApplication([])


class ProjectDailyWorkflowTests(unittest.TestCase):
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
            with patch.object(window, '_confirm_project_discard', return_value=True):
                window.close()
            window.deleteLater()
        APP.processEvents()

    def window(self):
        window = MainWindow()
        window._show_error = lambda text: self.errors.append(text)
        self.errors = []
        self.windows.append(window)
        return window

    def saved(self, suffix='.spectra'):
        window = self.window()
        window._load_dat_file_direct(self.paths[0])
        path = window.save_project(self.folder / ('daily' + suffix))
        return window, path

    def settle(self):
        QTest.qWait(240)
        APP.processEvents()

    def drop(self, window, paths):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        enter = QDragEnterEvent(QPoint(4, 4), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        window.dragEnterEvent(enter)
        event = QDropEvent(QPointF(4, 4), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        window.dropEvent(event)
        return enter, event

    def test_recent_success_persistence_dedup_bounds_and_clear(self):
        w, first = self.saved()
        for index in range(11):
            w.save_project(self.folder / f'{index}.spectra')
        w.save_project(first)
        restarted = self.window()
        actions = [action for action in restarted._recent_projects_menu.actions() if action.data()]
        self.assertEqual(len(actions), 10)
        self.assertEqual(Path(actions[0].data()), first)
        self.assertEqual(len({action.data() for action in actions}), 10)
        self.assertIn(first.name, actions[0].text())
        self.assertEqual(actions[0].toolTip(), str(first))
        actions[0].trigger()
        self.assertEqual(restarted._project_path, first)
        restarted._recent_projects_menu.actions()[-1].trigger()
        again = self.window()
        self.assertFalse(any(action.data() for action in again._recent_projects_menu.actions()))

    def test_failed_and_canceled_open_save_preserve_recents_path_and_marker(self):
        w, path = self.saved()
        w.plot_title_edit.setText('unsaved')
        self.settle()
        title = w.windowTitle()
        recent = w._annotation_settings.value('projects/recent', [], type=list)
        with patch('spectra_app.project_io.write_project', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                w.save_project(self.folder / 'failed.spectra')
        with patch('spectra_app.main_window.QFileDialog.getSaveFileName', return_value=('', '')):
            w._on_save_project(save_as=True)
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Cancel):
            self.assertFalse(w.open_project(path))
        bad = self.folder / 'bad.spectra'
        bad.write_text('broken project', encoding='utf-8')
        w._open_project_path(bad)
        self.settle()
        self.assertEqual(w._project_path, path)
        self.assertEqual(w.windowTitle(), title)
        self.assertEqual(w._annotation_settings.value('projects/recent', [], type=list), recent)
        self.assertTrue(self.errors)

    def test_native_and_legacy_drop_and_ambiguous_rejection(self):
        owner, path = self.saved('.spextra')
        w = self.window()
        enter, event = self.drop(w, [path])
        self.assertTrue(enter.isAccepted())
        self.assertTrue(event.isAccepted())
        self.assertEqual(w._project_path, path)
        current = w.workspace
        for paths in ([path, path], [path, self.paths[0]]):
            enter, event = self.drop(w, paths)
            self.assertFalse(enter.isAccepted())
            self.assertFalse(event.isAccepted())
            self.assertIs(w.workspace, current)
        canonical = owner.save_project(self.folder / 'canonical.spectra')
        enter, event = self.drop(w, [canonical])
        self.assertTrue(enter.isAccepted())
        self.assertTrue(event.isAccepted())
        self.assertEqual(w._project_path, canonical)
        current = w.workspace
        w.plot_title_edit.setText('keep pending changes')
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Cancel) as prompt:
            self.drop(w, [path])
            self.assertEqual(prompt.call_count, 1)
        self.assertIs(w.workspace, current)
        self.assertEqual(w.plot_title_edit.text(), 'keep pending changes')
        w._dat_import_pending = True
        enter, event = self.drop(w, [canonical])
        self.assertFalse(event.isAccepted())
        w._dat_import_pending = False

    def test_startup_dispatch_reports_bad_arguments_and_keeps_window_operable(self):
        from run_qt import schedule_startup_project
        owner, path = self.saved()
        w = self.window()
        w.show()
        schedule_startup_project(w, ['run_qt.py', str(path)])
        self.settle()
        self.assertEqual(w._project_path, path)
        current = w.workspace
        for arguments in (['run_qt.py', str(path), 'extra'], ['run_qt.py', '--unknown'],
                          ['run_qt.py', str(self.folder / 'missing.spectra')]):
            schedule_startup_project(w, arguments)
            self.settle()
            self.assertIs(w.workspace, current)
        self.assertEqual(len(self.errors), 3)
        self.assertTrue(w.isVisible())

    def test_real_main_preserves_unicode_filename_after_qt_option_consumption(self):
        owner, path = self.saved()
        path = owner.save_project(self.folder / '项目 daily.spectra')
        script = '''
import json
import sys
from pathlib import Path
from PySide6.QtCore import QTimer, QSettings
from PySide6.QtWidgets import QApplication
import run_qt
import spectra_app.main_window
windows, errors = [], []
class ProbeSettings(QSettings):
    def __init__(self, *args):
        super().__init__(str(Path(sys.argv[-1]).parent / 'launch-preferences.ini'), QSettings.IniFormat)
spectra_app.main_window.QSettings = ProbeSettings
class ProbeWindow(run_qt.MainWindow):
    def __init__(self):
        super().__init__()
        windows.append(self)
    def _show_error(self, text):
        errors.append(text)
class ProbeApplication(QApplication):
    def exec(self):
        QTimer.singleShot(800, self.quit)
        return super().exec()
run_qt.MainWindow = ProbeWindow
run_qt.QApplication = ProbeApplication
result = run_qt.main()
print(json.dumps({'exit': result, 'path': str(windows[0]._project_path), 'errors': errors}, ensure_ascii=True))
'''
        completed = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, '-platform', 'offscreen', str(path)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result['path'], str(path), result['errors'])
        self.assertEqual(result['errors'], [])

    def test_marker_pending_scientific_and_formal_edits_do_not_commit(self):
        w, path = self.saved()
        self.settle()
        self.assertFalse(w.isWindowModified())
        original = w.prom_spin.value()
        w.prom_spin.lineEdit().setText('0.127')
        with patch.object(w, '_capture_project_workspace', side_effect=AssertionError('full capture')), \
             patch.object(w.plot_page, 'finish_editor', side_effect=AssertionError('implicit commit')):
            self.settle()
        self.assertTrue(w.isWindowModified())
        self.assertIn('*', w.windowTitle())
        self.assertEqual(w.prom_spin.value(), original)
        w.prom_spin.lineEdit().setText(w.prom_spin.textFromValue(original))
        self.settle()
        self.assertFalse(w.isWindowModified())
        presentation = w.plot_page.presentation
        w.plot_page.font_edit.setText('DejaVu Sans')
        self.settle()
        self.assertTrue(w.isWindowModified())
        self.assertEqual(w.plot_page.presentation, presentation)
        w.plot_page.font_edit.setText(presentation.font_family)
        self.settle()
        self.assertFalse(w.isWindowModified())

    def test_marker_undo_revert_inactive_format_summary_and_new(self):
        w, path = self.saved()
        baseline = w.workspace.active.presentation
        w.set_plot_presentation(replace(baseline, text_pt=baseline.text_pt + 1))
        self.settle()
        self.assertTrue(w.isWindowModified())
        self.assertTrue(w.undo_edit())
        self.settle()
        self.assertFalse(w.isWindowModified())
        w.analysis_text.setPlainText('new summary')
        self.settle()
        self.assertTrue(w.isWindowModified())
        w.save_project()
        self.assertFalse(w.isWindowModified())
        w._load_dat_file_direct(self.paths[1])
        self.settle()
        self.assertTrue(w.isWindowModified())
        w.save_project()
        inactive = next(session for session in w.workspace.datasets.values() if session is not w.workspace.active)
        from spectra_app.plot_templates import capture_format
        template_source = detached_graph_session(w.workspace.active)
        template_source.presentation = replace(w.workspace.active.presentation,
            axis=replace(w.workspace.active.presentation.axis, flip_x=True))
        w._plot_format_store.save(capture_format(template_source, 'daily format'))
        outcomes = w.apply_plot_format('daily format', [inactive.dataset_id])
        self.assertTrue(outcomes[0]['success'])
        self.settle()
        self.assertTrue(w.isWindowModified())
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Discard):
            self.assertTrue(w.new_project())
        self.settle()
        self.assertFalse(w.isWindowModified())

    def test_rows_search_navigation_and_annotation_selection_stay_clean(self):
        w, path = self.saved()
        w.imported_dataset_rows_spin.setValue(12)
        taller = w.imported_dataset_list.height()
        w.imported_dataset_rows_spin.setValue(4)
        self.assertLess(w.imported_dataset_list.height(), taller)
        w.imported_dataset_search.setText('no match')
        w._sidebar_tabs.setCurrentIndex(1)
        w.imported_dataset_rows_spin.lineEdit().setText('')
        self.settle()
        self.assertFalse(w.isWindowModified())
        w.save_project()
        self.assertFalse(w.isWindowModified())
        restarted = self.window()
        self.assertEqual(restarted.imported_dataset_rows_spin.value(), 4)

    def test_rows_header_fits_normal_and_compact_sidebar(self):
        from run_qt import APP_QSS
        old_font, old_style = APP.font(), APP.styleSheet()
        try:
            APP.setFont(QFont('Segoe UI', 10))
            APP.setStyleSheet(APP_QSS)
            w, path = self.saved()
            w.show()
            for width in (1320, 1000):
                w.resize(width, 850 if width == 1320 else 680)
                self.settle()
                spin = w.imported_dataset_rows_spin
                viewport = w._sidebar_tabs.widget(0).viewport()
                point = spin.mapTo(viewport, QPoint(0, 0))
                self.assertGreaterEqual(point.x(), 0)
                self.assertLessEqual(point.x() + spin.width(), viewport.width())
                self.assertGreaterEqual(spin.width(), spin.minimumSizeHint().width())
        finally:
            APP.setFont(old_font)
            APP.setStyleSheet(old_style)

    def test_target_labels_preserve_identity_and_distinguish_folders(self):
        w = self.window()
        for path in self.paths:
            w._load_dat_file_direct(path)
        dialog = PlotTargetsDialog(w.workspace.datasets.values(), w.workspace.active_dataset_id, w)
        self.addCleanup(dialog.deleteLater)
        items = [dialog.targets.item(index) for index in range(2)]
        self.assertIn('first', items[0].text())
        self.assertIn('second', items[1].text())
        for item in items:
            self.assertNotIn(item.data(Qt.UserRole), item.text())
            self.assertIn('sample.dat', item.text())
            self.assertTrue(Path(item.toolTip()).is_absolute())
        self.assertEqual({item.data(Qt.UserRole) for item in items}, set(w.workspace.datasets))
        from spectra_app.plot_dialogs import ApplyFormatDialog
        from spectra_app.plot_templates import capture_format
        template = capture_format(w.workspace.active, 'daily labels')
        formatted = ApplyFormatDialog(w.workspace.datasets.values(), w.workspace.active_dataset_id, template, w)
        self.addCleanup(formatted.deleteLater)
        for dataset_id in w.workspace.datasets:
            self.assertNotIn(dataset_id, formatted.preview_text.toPlainText())
            for label in formatted.findChildren(type(w.imported_dataset_label)):
                self.assertNotIn(dataset_id, label.text())

    def test_runtime_stamp_hashes_mutable_peaks_but_reuses_large_arrays(self):
        from spectra_app.project_io import runtime_workspace_stamp, _runtime_array_hash
        w, path = self.saved()
        session = w.workspace.active
        session.fit_result = _fit_result(2)
        cache = {}
        first = runtime_workspace_stamp(w.workspace, cache)
        large_ids = {id(session.current_cube.z), id(session.fit_result.components)}
        seen = []
        def record(array):
            seen.append(id(array))
            return _runtime_array_hash(array)
        with patch('spectra_app.project_io._runtime_array_hash', side_effect=record):
            self.assertEqual(runtime_workspace_stamp(w.workspace, cache), first)
            session.peak_indices = np.array([4, 9])
            second = runtime_workspace_stamp(w.workspace, cache)
            session.peak_indices[0] = 5
            self.assertNotEqual(runtime_workspace_stamp(w.workspace, cache), second)
        self.assertFalse(large_ids.intersection(seen))
        self.assertIn(id(session.fit_result.centers), seen)
        self.assertNotEqual(first, second)
        self.assertEqual(_runtime_array_hash(np.empty((0, 3))),
                         'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')

    def test_gate_and_active_dataset_switch_mark_and_restore_saved_state(self):
        w, path = self.saved()
        original_gate = w.plot_gate_index_spin.value()
        w.plot_gate_index_spin.setValue(1)
        self.settle()
        self.assertTrue(w.isWindowModified())
        w.plot_gate_index_spin.setValue(original_gate)
        self.settle()
        self.assertFalse(w.isWindowModified())
        w._load_dat_file_direct(self.paths[1])
        w.save_project()
        saved_owner = w.workspace.active_dataset_id
        other_owner = next(key for key in w.workspace.datasets if key != saved_owner)
        w.activate_dataset(other_owner)
        self.settle()
        self.assertTrue(w.isWindowModified())
        w.activate_dataset(saved_owner)
        self.settle()
        self.assertFalse(w.isWindowModified())

    def test_scientific_completion_and_result_clear_notify_title(self):
        w, path = self.saved()
        w._start_background_job(lambda context: _fit_result(2),
            lambda result: setattr(w, 'fit_result', result), 'test result', owner='analysis')
        wait_jobs(w)
        self.settle()
        self.assertIsNotNone(w.fit_result)
        self.assertTrue(w.isWindowModified())
        w.save_project()
        w._clear_processing_analysis_state()
        self.settle()
        self.assertTrue(w.isWindowModified())

    def test_heatmap_gate_and_seeds_reload_are_durable_notifications(self):
        w, path = self.saved()
        w._on_canvas_click(SimpleNamespace(inaxes=w._heat_ax, ydata=0.0, button=1))
        self.settle()
        self.assertEqual(w.current_gate, 0.0)
        self.assertTrue(w.isWindowModified())
        seeds = self.folder / 'seeds.csv'
        seeds.write_text('gate,peak_energy\n0,2.1\n', encoding='utf-8')
        w._load_seeds_csv_file(seeds)
        w.save_project()
        seeds.write_text('gate,peak_energy\n0,2.4\n', encoding='utf-8')
        w._load_seeds_csv_file(seeds)
        self.settle()
        self.assertEqual(w._csv_seeds_map, {0: [2.4]})
        self.assertTrue(w.isWindowModified())

    def test_failed_install_restores_saved_stamp_and_modified_title(self):
        w, path = self.saved()
        saved_stamp = w._project_saved_stamp
        recent = list(w._recent_project_paths)
        current = w.workspace
        w.analysis_text.setPlainText('keep this summary')
        self.settle()
        before = w.windowTitle()
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Discard), \
             patch.object(w, '_project_installed_workspace', side_effect=RuntimeError('failed projection')):
            with self.assertRaises(RuntimeError):
                w.open_project(path)
        self.settle()
        self.assertIs(w.workspace, current)
        self.assertEqual(w._project_saved_stamp, saved_stamp)
        self.assertEqual(w.windowTitle(), before)
        self.assertEqual(w._recent_project_paths, recent)

    def test_pending_ticks_annotation_and_ui_selection_do_not_commit(self):
        from spectra_app.plot_presentation import ColorbarTicks, PlotAnnotation
        w, path = self.saved()
        presentation = w.workspace.active.presentation
        presentation = replace(presentation, colorbar=replace(presentation.colorbar,
            ticks={'global': ColorbarTicks((1, 4, 8), ('low', 'middle', 'high'))}),
            annotations=(PlotAnnotation('daily-note', text='stored text'),))
        w.set_plot_presentation(presentation)
        w.save_project()
        p = w.plot_page
        p.select_annotation('daily-note')
        p.bar_id_combo.setCurrentText('region-1')
        self.settle()
        self.assertFalse(w.isWindowModified())
        p.bar_id_combo.setCurrentText('global')
        p.tick_labels_edit.setText('low | modified | high')
        self.settle()
        self.assertTrue(w.isWindowModified())
        self.assertEqual(p.presentation.colorbar.ticks['global'].labels, ('low', 'middle', 'high'))
        p.tick_labels_edit.setText('low | middle | high')
        p.annotation_text.setText('pending text')
        self.settle()
        self.assertTrue(w.isWindowModified())
        self.assertEqual(p.presentation.annotations[0].text, 'stored text')
        p.annotation_text.setText('stored text')
        self.settle()
        self.assertFalse(w.isWindowModified())

    def test_runtime_cache_prunes_retired_arrays_and_normalizes_numeric_metadata(self):
        from spectra_app.project_io import runtime_workspace_stamp, read_project
        w, path = self.saved()
        cache = {}
        before = runtime_workspace_stamp(w.workspace, cache)
        restored = read_project(path)
        self.assertEqual(runtime_workspace_stamp(restored, {}), before)
        for session in restored.datasets.values():
            session.presentation = replace(session.presentation, text_pt=int(session.presentation.text_pt))
        self.assertEqual(runtime_workspace_stamp(restored, {}), before)
        old_z = w.workspace.active.current_cube.z
        self.assertIn(id(old_z), cache)
        w.workspace.datasets.clear()
        w.workspace.active_dataset_id = None
        runtime_workspace_stamp(w.workspace, cache)
        self.assertEqual(cache, {})


if __name__ == '__main__':
    unittest.main()
