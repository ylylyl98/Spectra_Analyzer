from tests import source_navigation_helpers as sn
"""Daily project entry, preferences and modification status through real Qt routes."""
from tests import analysis_header_quick_helpers as ah
from tests import ordinary_analysis_quick_helpers as oa
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PySide6.QtTest import QTest
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem

from spectra_app.common_prompts import PromptAction
from spectra_app.quick_root import QuickApplication
from spectra_app.dataset_state import detached_graph_session
from spectra_app.plot_dialogs import PlotTargetsDialog
from spectra_app.disclosure import reveal_editor
from tests import dat_properties_quick_helpers as dp
from tests.quick_migration_case import QuickMigrationCase
from tests.formal_quick_helpers import item, pump, input_receiver, replace_text
from tests.background_helpers import wait_jobs
from tests.scientific_fixtures import workflow_fit_result as _fit_result

class ProjectDailyWorkflowTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.errors = self.quick_errors
        self.hosts = [self.host]
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
            self.wait_idle(window)
        self.assertEqual(self.errors, [])

    def dispose_extra(self, host):
        owner = host.controller
        self.wait_idle(owner)
        if not owner._closing:
            with patch.object(owner, '_confirm_project_discard_async', lambda callback: callback()):
                host.window.close()
                deadline = time.monotonic() + 8
                while not owner._closing and time.monotonic() < deadline:
                    pump(10)
                self.assertTrue(owner._closing)
        host.dispose()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()

    def wait_idle(self, window):
        deadline = time.monotonic() + 8
        while (window._project_io_jobs.busy or window._background_jobs.busy) and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(window._project_io_jobs.busy or window._background_jobs.busy,
                         'asynchronous project operation timed out')
        pump()

    def window(self):
        if not self.windows:
            window = self.controller
        else:
            # Every restart shares this case's isolated profile, as production
            # windows do; each remains a registered real Quick application.
            host = QuickApplication()
            self.hosts.append(host)
            host.show()
            window = host.controller
            window._show_error = self.errors.append
            self.addCleanup(self.dispose_extra, host)
        self.windows.append(window)
        window.root_window.show()
        pump()
        return window

    def saved(self, suffix='.spectra'):
        window = self.window()
        window._load_dat_file_direct(self.paths[0])
        window.open_graph_document()
        path = window.save_project(self.folder / ('daily' + suffix))
        return window, path

    def settle(self):
        QTest.qWait(240)
        for window in self.windows:
            self.wait_idle(window)
            # Paper growth during rendering can restart the dirty debounce.
            # Observe its completion instead of assuming the first wait covered it.
            deadline = time.monotonic() + 3
            def pending():
                return (window._plot_render_timer.isActive()
                        or window._project_modified_timer.isActive()
                        or window._project_io_jobs.busy or window._background_jobs.busy)
            while pending() and time.monotonic() < deadline:
                pump(10)
            self.assertFalse(pending(), 'render and project marker did not settle')
        pump()

    def drop(self, window, paths):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        point = QPointF(window.root_window.width()/2, window.root_window.height()/2)
        enter = QDragEnterEvent(point.toPoint(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(window.root_window, enter)
        event = QDropEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QCoreApplication.sendEvent(window.root_window, event)
        self.wait_idle(window)
        return enter, event

    def recent_menu(self, window, title):
        bar = window.root_window.property('menuBar')
        entry = next(control for control in bar.property('contentItem').childItems()
                     if control.property('text') == 'Recent projects')
        def click(control):
            receiver = input_receiver(window, control)
            point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
            QTest.mouseClick(receiver, Qt.LeftButton, pos=point)
            pump()
        click(entry)
        deadline = time.monotonic() + 3
        matches = []
        while not matches and time.monotonic() < deadline:
            matches = [control for control in window.root_window.findChildren(QQuickItem)
                       if control.inherits('QQuickMenuItem') and control.isVisible()
                       and control.property('text') == title]
            if not matches:
                pump(10)
        self.assertEqual(len(matches), 1, title)
        click(matches[0])

    def test_recent_success_persistence_dedup_bounds_and_clear(self):
        w, first = self.saved()
        for index in range(11):
            w.save_project(self.folder / f'{index}.spectra')
        w.save_project(first)
        restarted = self.window()
        actions = restarted.recentProjects
        self.assertEqual(len(actions), 10)
        self.assertEqual(Path(actions[0]['path']), first)
        self.assertEqual(len({action['path'] for action in actions}), 10)
        self.assertIn(first.name, actions[0]['title'])
        self.assertIn(str(first.parent), actions[0]['title'])
        self.recent_menu(restarted, actions[0]['title'])
        self.wait_idle(restarted)
        self.assertEqual(restarted._project_path, first)
        self.recent_menu(restarted, 'Clear recent projects')
        again = self.window()
        self.assertFalse(again.recentProjects)

    def test_failed_and_canceled_open_save_preserve_recents_path_and_marker(self):
        w, path = self.saved()
        w.dat_properties.setLabel('plot_title', 'unsaved', w.dat_properties.ownerToken)
        self.settle()
        title = w.title
        recent = w._annotation_settings.value('projects/recent', [], type=list)
        with patch('spectra_app.project_io.write_project', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                w.save_project(self.folder / 'failed.spectra')
        with patch('spectra_app.main_window.FilePicker.getSaveFileName', return_value=('', '')):
            w._on_save_project(save_as=True)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL):
            self.assertFalse(w.open_project(path))
        bad = self.folder / 'bad.spectra'
        bad.write_text('broken project', encoding='utf-8')
        w._open_project_path(bad)
        self.settle()
        self.assertEqual(w._project_path, path)
        self.assertEqual(w.title, title)
        self.assertEqual(w._annotation_settings.value('projects/recent', [], type=list), recent)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("project", self.errors[0].lower())
        self.errors.clear()

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
        w.dat_properties.setLabel('plot_title', 'keep pending changes', w.dat_properties.ownerToken)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as prompt:
            self.drop(w, [path])
            self.assertEqual(prompt.call_count, 1)
        self.assertIs(w.workspace, current)
        self.assertEqual(dp.state(w)['plot_title'], 'keep pending changes')
        w._dat_import_pending = True
        enter, event = self.drop(w, [canonical])
        self.assertFalse(enter.isAccepted())
        self.assertFalse(event.isAccepted())
        w._dat_import_pending = False
        # Drag feedback shares drop-time validation and has no publication or
        # status side effects for empty, remote, mixed and valid local sources.
        before = (w.workspace, w.statusText, list(w.workspace.datasets))
        for urls, expected in (([], False), ([QUrl('https://example.invalid/data.csv')], False),
                               ([QUrl.fromLocalFile(str(canonical)), QUrl.fromLocalFile(str(self.paths[0]))], False),
                               ([QUrl.fromLocalFile(str(self.paths[0])), QUrl.fromLocalFile(str(self.paths[1]))], True),
                               ([QUrl.fromLocalFile(str(canonical))], True)):
            self.assertEqual(w.canAcceptDropUrls(urls), expected)
        self.assertEqual((w.workspace, w.statusText, list(w.workspace.datasets)), before)
        receiver = self.window()
        enter, event = self.drop(receiver, [self.paths[1]])
        self.assertTrue(enter.isAccepted())
        self.assertTrue(event.isAccepted())
        self.assertIsNotNone(receiver.workspace.dataset_for_path(self.paths[1]))

    def test_startup_dispatch_reports_bad_arguments_and_keeps_window_operable(self):
        from run_qt import schedule_startup_project
        owner, path = self.saved()
        w = self.window()
        w.root_window.show()
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
        self.errors.clear()
        self.assertTrue(w.root_window.isVisible())

    def test_real_main_preserves_unicode_filename_after_qt_option_consumption(self):
        owner, path = self.saved()
        path = owner.save_project(self.folder / '项目 daily.spectra')
        script = r'''
import json
import sys
from pathlib import Path
from PySide6.QtCore import QTimer, QSettings
from PySide6.QtGui import QGuiApplication
import run_qt
import spectra_app.main_window
windows, errors, observed = [], [], {}
class ProbeSettings(QSettings):
    def __init__(self, *args):
        super().__init__(str(Path(sys.argv[-1]).parent / 'launch-preferences.ini'), QSettings.IniFormat)
spectra_app.main_window.QSettings = ProbeSettings
class ProbeHost(run_qt.QuickApplication):
    def __init__(self):
        super().__init__()
        windows.append(self.controller)
        self.controller._show_error = errors.append
        attempts = [0]
        def finish():
            attempts[0] += 1
            owner = self.controller
            if (owner._project_io_jobs.busy or owner._project_path is None) and attempts[0] < 100:
                QTimer.singleShot(50, finish)
                return
            observed.update(path=str(owner._project_path), errors=list(errors),
                            exact_qgui=type(QGuiApplication.instance()) is QGuiApplication)
            QGuiApplication.quit()
        QTimer.singleShot(50, finish)
run_qt.QuickApplication = ProbeHost
result = run_qt.main()
print(json.dumps(dict(observed, exit=result), ensure_ascii=True))
'''
        completed = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script, '-platform', 'offscreen', str(path)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, '')
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result['exit'], 0)
        self.assertEqual(result['path'], str(path), result['errors'])
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['exact_qgui'])

    def test_marker_pending_scientific_and_formal_edits_do_not_commit(self):
        w, path = self.saved()
        self.settle()
        self.assertFalse(w.projectModified)
        original = oa.value(w, 'prominence')
        oa.draft(w, 'prominence', '0.127')
        with patch.object(w, '_capture_project_workspace', side_effect=AssertionError('full capture')), \
             patch.object(w.plot_page, 'finish_editor', side_effect=AssertionError('implicit commit')):
            self.settle()
        self.assertTrue(w.projectModified)
        self.assertIn('*', w.title)
        self.assertEqual(oa.value(w, 'prominence'), original)
        oa.draft(w, 'prominence', str(original))
        self.settle()
        self.assertFalse(w.projectModified)
        presentation = w.plot_page.presentation
        displayed = w.plot_page.formal.state['fields']['text_pt']
        w.plot_page.formal.setDraft('text_pt', '15')
        self.settle()
        self.assertTrue(w.projectModified)
        self.assertEqual(w.plot_page.presentation, presentation)
        w.plot_page.formal.setDraft('text_pt', displayed)
        self.settle()
        self.assertFalse(w.projectModified)

    def test_marker_undo_revert_inactive_format_summary_and_new(self):
        w, path = self.saved()
        baseline = w.workspace.active.presentation
        w.set_plot_presentation(replace(baseline, text_pt=baseline.text_pt + 1))
        self.settle()
        grown = w.workspace.active.presentation
        self.assertGreater(grown.height_in, baseline.height_in)
        self.assertTrue(w.projectModified)
        self.assertTrue(w.undo_edit())
        self.settle()
        self.assertEqual(w.workspace.active.presentation, baseline)
        self.assertFalse(w.projectModified)
        self.assertTrue(w.redo_edit())
        self.settle()
        self.assertEqual(w.workspace.active.presentation, grown)
        self.assertTrue(w.projectModified)
        self.assertTrue(w.undo_edit())
        self.settle()
        self.assertFalse(w.projectModified)
        oa.set_summary(w, 'new summary')
        self.settle()
        self.assertTrue(w.projectModified)
        w.save_project()
        self.assertFalse(w.projectModified)
        w._load_dat_file_direct(self.paths[1])
        self.settle()
        self.assertTrue(w.projectModified)
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
        self.assertTrue(w.projectModified)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            self.assertTrue(w.new_project())
        self.settle()
        self.assertFalse(w.projectModified)

    def test_delayed_inactive_page_growth_stays_in_its_own_undo_command(self):
        w, path = self.saved()
        first = w.workspace.active
        baseline = first.presentation
        history = w._history_for(first)
        count = len(history.undo_stack)
        with patch.object(w, '_schedule_plot_page_render'):
            w.set_plot_presentation(replace(baseline, text_pt=baseline.text_pt + 1))
            w._plot_render_timer.stop()
            w._load_dat_file_direct(self.paths[1])
            w.open_graph_document()
            active = w.workspace.active
            active_presentation = active.presentation
            w._render_plot_page((first.dataset_id, first.active_graph_id))
        grown = first.presentation
        self.assertGreater(grown.height_in, baseline.height_in)
        self.assertIs(w.workspace.active, active)
        self.assertEqual(active.presentation, active_presentation)
        self.assertEqual(len(history.undo_stack), count + 1,
                         'Preparing an inactive page must not create a render operation')
        w.open_graph_document(first.dataset_id)
        self.assertTrue(w.undo_edit())
        self.assertEqual(first.presentation, baseline)
        self.assertTrue(w.redo_edit())
        w._render_plot_page()
        self.assertEqual(first.presentation, grown)

    def test_delayed_growth_and_newer_palette_undo_restore_saved_presentation(self):
        w, path = self.saved()
        baseline = w.workspace.active.presentation
        history = w._history_for(w.workspace.active)
        count = len(history.undo_stack)
        with patch.object(w, '_schedule_plot_page_render'):
            w.set_plot_presentation(replace(baseline, text_pt=baseline.text_pt + 1))
            w._plot_render_timer.stop()
            dp.set_choice(w, 'colormap', 'plasma')
            w._plot_render_timer.stop()
            w._render_plot_page()
            self.assertGreater(w.workspace.active.presentation.height_in, baseline.height_in)
            self.assertEqual(len(history.undo_stack), count + 2)
            self.assertTrue(w.undo_edit())
            w._render_plot_page()
            self.assertEqual(w.workspace.active.presentation.text_pt, baseline.text_pt + 1)
            self.assertTrue(w.undo_edit())
            w._render_plot_page()
        self.settle()
        self.assertEqual(w.workspace.active.presentation, baseline)
        self.assertFalse(w.projectModified)

    def test_presentation_redo_before_first_render_keeps_paper_reversible(self):
        w, path = self.saved()
        baseline = w.workspace.active.presentation
        with patch.object(w, '_schedule_plot_page_render'):
            w.set_plot_presentation(replace(baseline, text_pt=baseline.text_pt + 1))
            w._plot_render_timer.stop()
            self.assertTrue(w.undo_edit())
            self.assertTrue(w.redo_edit())
            w._render_plot_page()
            self.assertGreater(w.workspace.active.presentation.height_in, baseline.height_in)
            self.assertTrue(w.undo_edit())
            w._render_plot_page()
        self.settle()
        self.assertEqual(w.workspace.active.presentation, baseline)
        self.assertFalse(w.projectModified)

    def test_rows_search_navigation_and_annotation_selection_stay_clean(self):
        w, path = self.saved()
        sn.reveal(w)
        w.source_navigation.setVisibleRows(12)
        sn.pump()
        taller = item(w, 'sourceDatasets').height()
        w.source_navigation.setVisibleRows(4)
        sn.pump()
        self.assertLess(item(w, 'sourceDatasets').height(), taller)
        w.source_navigation.setSearchText('no match')
        self.assertTrue(w.request_task(1))
        self.assertTrue(w.request_task(0))
        spin = item(w, 'sourceVisibleRows').property('contentItem')
        spin.forceActiveFocus()
        QTest.keyClick(w.root_window, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(w.root_window, Qt.Key_Backspace)
        self.settle()
        self.assertFalse(w.projectModified)
        w.save_project()
        self.assertFalse(w.projectModified)
        restarted = self.window()
        self.assertEqual(restarted.source_navigation.visibleRows, 4)

    def test_rows_header_fits_normal_and_compact_sidebar(self):
        old_font = QGuiApplication.font()
        try:
            QGuiApplication.setFont(QFont('Segoe UI', 10))
            w, path = self.saved()
            self.assertTrue(w.request_outer_view('analysis'))
            sn.reveal(w)
            for width in (1320, 1000):
                w.root_window.resize(width, 850 if width == 1320 else 680)
                self.settle()
                spin = item(w, 'sourceVisibleRows')
                viewport = item(w, 'propertiesScroll')
                self.assertTrue(spin.isVisible())
                point = spin.mapToItem(viewport, QPointF())
                self.assertGreaterEqual(point.x(), 0)
                self.assertLessEqual(point.x() + spin.width(), viewport.width())
                self.assertGreaterEqual(spin.width(), spin.property('implicitWidth'))
        finally:
            QGuiApplication.setFont(old_font)

    def test_target_labels_preserve_identity_and_distinguish_folders(self):
        w = self.window()
        for path in self.paths:
            w._load_dat_file_direct(path)
        dialog = PlotTargetsDialog(w.workspace.datasets.values(), w.workspace.active_dataset_id, w.root_window)
        self.addCleanup(dialog.deleteLater)
        items = dialog.bridge.targets
        self.assertIn('first', items[0].label)
        self.assertIn('second', items[1].label)
        for item in items:
            self.assertNotIn(item.datasetId, item.label)
            self.assertIn('sample.dat', item.label)
            self.assertTrue(Path(item.tooltip).is_absolute())
        self.assertEqual({item.datasetId for item in items}, set(w.workspace.datasets))
        from spectra_app.plot_dialogs import ApplyFormatDialog
        from spectra_app.plot_templates import capture_format
        template = capture_format(w.workspace.active, 'daily labels')
        formatted = ApplyFormatDialog(w.workspace.datasets.values(), w.workspace.active_dataset_id, template, w.root_window)
        self.addCleanup(formatted.deleteLater)
        for dataset_id in w.workspace.datasets:
            self.assertNotIn(dataset_id, formatted.bridge.previewText)
            for target in formatted.bridge.targets:
                self.assertNotIn(dataset_id, target.label)

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
        original_gate = int(ah.text(w, 'index'))
        ah.edit(w, 'index', '1', commit=True)
        self.settle()
        self.assertTrue(w.projectModified)
        ah.edit(w, 'index', str(original_gate), commit=True)
        self.settle()
        self.assertFalse(w.projectModified)
        w._load_dat_file_direct(self.paths[1])
        w.save_project()
        saved_owner = w.workspace.active_dataset_id
        other_owner = next(key for key in w.workspace.datasets if key != saved_owner)
        w.activate_dataset(other_owner)
        self.settle()
        self.assertTrue(w.projectModified)
        w.activate_dataset(saved_owner)
        self.settle()
        self.assertFalse(w.projectModified)

    def test_scientific_completion_and_result_clear_notify_title(self):
        w, path = self.saved()
        w._start_background_job(lambda context: _fit_result(2),
            lambda result: setattr(w, 'fit_result', result), 'test result', owner='analysis')
        wait_jobs(w)
        self.settle()
        self.assertIsNotNone(w.fit_result)
        self.assertTrue(w.projectModified)
        w.save_project()
        w._clear_processing_analysis_state()
        self.settle()
        self.assertTrue(w.projectModified)

    def test_heatmap_gate_and_seeds_reload_are_durable_notifications(self):
        w, path = self.saved()
        w._on_canvas_click(SimpleNamespace(inaxes=w._heat_ax, ydata=0.0, button=1))
        self.settle()
        self.assertEqual(w.current_gate, 0.0)
        self.assertTrue(w.projectModified)
        seeds = self.folder / 'seeds.csv'
        seeds.write_text('gate,peak_energy\n0,2.1\n', encoding='utf-8')
        w._load_seeds_csv_file(seeds)
        w.save_project()
        seeds.write_text('gate,peak_energy\n0,2.4\n', encoding='utf-8')
        w._load_seeds_csv_file(seeds)
        self.settle()
        self.assertEqual(w._csv_seeds_map, {0: [2.4]})
        self.assertTrue(w.projectModified)

    def test_failed_install_restores_saved_stamp_and_modified_title(self):
        w, path = self.saved()
        source = w.workspace.active.source_path
        with patch('spectra_app.main_window.FilePicker.getExistingDirectory', return_value=str(source.parent)):
            w._on_browse_root()
        wait_jobs(w)
        w.source_navigation.setSearchText('no matching source')
        source_presentation = (w.source_navigation.searchText, w.source_navigation.rootPath,
                               w.source_navigation.selectedFolder, w.source_navigation.selectedFile,
                               list(w.source_navigation.fileRows))
        saved_stamp = w._project_saved_stamp
        recent = list(w._recent_project_paths)
        current = w.workspace
        oa.set_summary(w, 'keep this summary')
        self.settle()
        before = w.title
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD), \
             patch.object(w, '_project_installed_workspace', side_effect=RuntimeError('failed projection')):
            with self.assertRaises(RuntimeError):
                w.open_project(path)
        self.settle()
        self.assertIs(w.workspace, current)
        self.assertEqual(w._project_saved_stamp, saved_stamp)
        self.assertEqual(w.title, before)
        self.assertEqual(w._recent_project_paths, recent)
        self.assertEqual((w.source_navigation.searchText, w.source_navigation.rootPath,
                          w.source_navigation.selectedFolder, w.source_navigation.selectedFile,
                          w.source_navigation.fileRows), source_presentation)

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
        p.formal.selectBar('region-1')
        self.settle()
        self.assertFalse(w.projectModified)
        p.formal.selectBar('global')
        p.formal.setDraft('tick_labels', 'low | modified | high')
        self.settle()
        self.assertTrue(w.projectModified)
        self.assertEqual(p.presentation.colorbar.ticks['global'].labels, ('low', 'middle', 'high'))
        p.formal.setDraft('tick_labels', 'low | middle | high')
        p.formal.setDraft('annotation_text', 'pending text')
        self.settle()
        self.assertTrue(w.projectModified)
        self.assertEqual(p.presentation.annotations[0].text, 'stored text')
        p.formal.setDraft('annotation_text', 'stored text')
        self.settle()
        self.assertFalse(w.projectModified)

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
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            self.assertTrue(w.new_project())
        runtime_workspace_stamp(w.workspace, cache)
        self.assertEqual(cache, {})


if __name__ == '__main__':
    unittest.main()
