"""Responsive project I/O through the actual UI entry points and archive boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest

from spectra_app.background_jobs import CancelledError
from spectra_app.dataset_state import Workspace
from spectra_app.common_prompts import PromptAction
from spectra_app.project_io import ProjectFormatError, read_project, workspace_digest, write_project
from spectra_app.workbook_state import create_comparison
from tests import quick_migration_case as migration
from tests.formal_quick_helpers import item, pump

class ProjectAsyncTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.errors = self.quick_errors
        self.source = self.folder / 'table.csv'
        self.source.write_text('X,Y\n0,10\n1,20\n2,30\n', encoding='utf-8')
        self.book = self.window.import_xy_files([self.source])

    def tearDown(self):
        self.wait_idle()
        self.assertEqual(self.errors, [], 'Unexpected production Quick error')

    def wait_until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), 'asynchronous project operation timed out')

    def wait_idle(self):
        self.wait_until(lambda: not self.window._project_io_jobs.busy and not self.window._background_jobs.busy)
        QTest.qWait(20)

    def assert_manifest_discard_prompt(self, prompt):
        prompt.assert_called_once()
        args = prompt.call_args.args
        self.assertIn('current edits cannot be saved yet', args[2])
        self.assertIn('manifest exceeds the supported size', args[2])
        self.assertEqual(args[3], (PromptAction.SAVE, PromptAction.DISCARD, PromptAction.CANCEL))
        self.assertEqual(args[4], PromptAction.CANCEL)

    def test_manifest_limit_discard_allows_new(self):
        workspace = self.window.workspace
        with patch('spectra_app.project_io.MAX_MANIFEST_BYTES', 512), patch(
                'spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD) as prompt:
            with self.assertRaises(ProjectFormatError):
                workspace_digest(self.window._capture_project_workspace())
            self.window._on_new_project()
            self.wait_idle()
            self.assert_manifest_discard_prompt(prompt)
        self.assertIsNot(self.window.workspace, workspace)
        self.assertFalse(self.window.workspace.workbooks)
        self.assertFalse(self.errors)

    def test_manifest_limit_discard_allows_close(self):
        self.window.root_window.show()
        with patch('spectra_app.project_io.MAX_MANIFEST_BYTES', 512), patch(
                'spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD) as prompt:
            self.window.close()
            self.wait_idle()
            self.assert_manifest_discard_prompt(prompt)
            self.wait_until(lambda: not self.window.root_window.isVisible())
        self.assertFalse(self.errors)

    def test_manifest_limit_discard_allows_opening_valid_smaller_archive(self):
        path = self.folder / 'smaller.spectra'
        write_project(path, Workspace())
        workspace = self.window.workspace
        with patch('spectra_app.project_io.MAX_MANIFEST_BYTES', 512), patch(
                'spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD) as prompt:
            self.assertFalse(read_project(path).workbooks)
            self.window._open_project_path(path)
            self.wait_idle()
            self.assert_manifest_discard_prompt(prompt)
        self.assertIsNot(self.window.workspace, workspace)
        self.assertFalse(self.window.workspace.workbooks)
        self.assertEqual(self.window._project_path, path)
        self.assertFalse(self.window.projectModified)
        self.assertFalse(self.errors)

    def test_manifest_limit_cancel_retains_workspace_for_close_new_and_open(self):
        self.window.root_window.show()
        path = self.folder / 'cancel-open.spectra'
        write_project(path, Workspace())
        workspace = self.window.workspace
        for name, action in (('Close', self.window.close), ('New', self.window._on_new_project),
                             ('Open', lambda: self.window._open_project_path(path))):
            with self.subTest(action=name), patch('spectra_app.project_io.MAX_MANIFEST_BYTES', 512), patch(
                    'spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as prompt:
                action()
                self.wait_idle()
                self.assert_manifest_discard_prompt(prompt)
                self.assertIs(self.window.workspace, workspace)
                self.assertTrue(self.window.root_window.isVisible())
                self.assertIsNone(self.window._project_path)
        self.assertFalse(self.errors)

    def test_manifest_limit_failed_save_retains_workspace_and_previous_archive(self):
        path = self.window.save_project(self.folder / 'unchanged.spectra')
        before = path.read_bytes()
        workspace = self.window.workspace
        with patch('spectra_app.project_io.MAX_MANIFEST_BYTES', 512), patch(
                'spectra_app.main_window.prompt_message', return_value=PromptAction.SAVE) as prompt:
            self.window._on_new_project()
            self.wait_idle()
            self.assert_manifest_discard_prompt(prompt)
        self.assertIs(self.window.workspace, workspace)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.errors), 1)
        self.assertTrue(self.errors[0].startswith('Cannot save project: Project manifest exceeds'))
        self.errors.clear()

    def test_cancel_during_failing_digest_retains_workspace_without_prompt(self):
        workspace = self.window.workspace
        started, release = threading.Event(), threading.Event()
        def held_digest(snapshot):
            started.set()
            self.assertTrue(release.wait(5))
            return workspace_digest(snapshot)
        try:
            with patch('spectra_app.project_io.MAX_MANIFEST_BYTES', 512), patch(
                    'spectra_app.project_io.workspace_digest', side_effect=held_digest), patch(
                    'spectra_app.main_window.prompt_message') as prompt:
                self.window._on_new_project()
                self.wait_until(started.is_set)
                self.window._cancel_background_job()
                release.set()
                self.wait_idle()
                prompt.assert_not_called()
        finally:
            release.set()
        self.assertIs(self.window.workspace, workspace)
        self.assertFalse(self.errors)

    def test_failing_digest_rechecks_edited_workspace_before_prompting(self):
        sheet = self.window.workspace.sheets[self.book.sheet_ids[0]]
        started = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        snapshot_names, prompt_threads = [], []
        def held_digest(snapshot):
            index = len(snapshot_names)
            snapshot_names.append(snapshot.sheets[sheet.sheet_id].name)
            started[index].set()
            self.assertTrue(release[index].wait(5))
            return workspace_digest(snapshot)
        def cancel_prompt(*args, **kwargs):
            self.assertFalse(self.window._project_io_jobs.busy)
            prompt_threads.append(threading.get_ident())
            return PromptAction.CANCEL
        workspace = self.window.workspace
        try:
            with patch('spectra_app.project_io.MAX_MANIFEST_BYTES', 512), patch(
                    'spectra_app.project_io.workspace_digest', side_effect=held_digest), patch(
                    'spectra_app.main_window.prompt_message', side_effect=cancel_prompt) as prompt:
                self.window._on_new_project()
                self.wait_until(started[0].is_set)
                sheet.name = 'Edited during failed digest'
                self.window._schedule_project_modified()
                release[0].set()
                self.wait_until(started[1].is_set)
                prompt.assert_not_called()
                self.assertEqual(snapshot_names[1], sheet.name)
                release[1].set()
                self.wait_idle()
                self.assert_manifest_discard_prompt(prompt)
        finally:
            for event in release:
                event.set()
        self.assertIs(self.window.workspace, workspace)
        self.assertEqual(prompt_threads, [threading.get_ident()])
        self.assertFalse(self.errors)

    def test_failing_digest_does_not_prompt_for_replaced_workspace(self):
        started, release = threading.Event(), threading.Event()
        def held_digest(snapshot):
            started.set()
            self.assertTrue(release.wait(5))
            return workspace_digest(snapshot)
        try:
            with patch('spectra_app.project_io.MAX_MANIFEST_BYTES', 512), patch(
                    'spectra_app.project_io.workspace_digest', side_effect=held_digest), patch(
                    'spectra_app.main_window.prompt_message') as prompt:
                self.window._on_new_project()
                self.wait_until(started.is_set)
                self.window._new_project_unchecked()
                replacement = self.window.workspace
                release.set()
                self.wait_idle()
                prompt.assert_not_called()
        finally:
            release.set()
        self.assertIs(self.window.workspace, replacement)
        self.assertFalse(self.errors)

    def test_ui_save_compresses_detached_snapshot_while_events_and_edits_continue(self):
        path = self.folder / 'responsive.spectra'
        started, release = threading.Event(), threading.Event()
        threads = []
        def held_write(*args, **kwargs):
            threads.append(threading.get_ident())
            started.set()
            self.assertTrue(release.wait(5))
            return write_project(*args, **kwargs)
        sheet = self.window.workspace.sheets[self.book.sheet_ids[0]]
        original = float(sheet.data[0, 1])
        try:
            with patch('spectra_app.project_io.write_project', side_effect=held_write), patch(
                    'spectra_app.main_window.FilePicker.getSaveFileName', return_value=(str(path), '')):
                self.window._on_save_project()
                self.wait_until(started.is_set)
                heartbeat = []
                QTimer.singleShot(0, lambda: heartbeat.append(True))
                QTest.qWait(30)
                self.assertTrue(heartbeat)
                with patch('spectra_app.project_io.runtime_workspace_stamp',
                           side_effect=AssertionError('live arrays hashed on GUI during project I/O')):
                    self.window._refresh_project_modified()
                sheet.data[0, 1] = 99
                self.window._schedule_project_modified()
                release.set()
                self.wait_idle()
        finally:
            release.set()
        restored = read_project(path)
        self.assertEqual(restored.sheets[sheet.sheet_id].data[0, 1], original)
        self.assertTrue(self.window.projectModified)
        self.assertNotEqual(threads, [threading.get_ident()])
        self.assertFalse(self.errors)

    def test_cancelled_open_and_failed_save_retain_workspace_and_prior_file(self):
        path = self.window.save_project(self.folder / 'prior.spectra')
        prior = path.read_bytes()
        workspace = self.window.workspace
        started, release = threading.Event(), threading.Event()
        def held_read(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(5))
            return read_project(*args, **kwargs)
        try:
            with patch('spectra_app.project_io.read_project', side_effect=held_read):
                self.assertTrue(self.window._open_project_path(path))
                self.wait_until(started.is_set)
                self.window._cancel_background_job()
                release.set()
                self.wait_idle()
        finally:
            release.set()
        self.assertIs(self.window.workspace, workspace)
        with patch('spectra_app.project_io.write_project', side_effect=OSError('disk unavailable')):
            self.window._on_save_project()
            self.wait_idle()
        self.assertIs(self.window.workspace, workspace)
        self.assertEqual(path.read_bytes(), prior)
        self.assertEqual(self.errors, ['Cannot save project: disk unavailable'])
        self.errors.clear()

    def test_save_before_new_rechecks_changes_made_during_save(self):
        workspace = self.window.workspace
        sheet = workspace.sheets[self.book.sheet_ids[0]]
        path = self.folder / 'before-new.spectra'
        started, release = threading.Event(), threading.Event()
        def held_write(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(5))
            return write_project(*args, **kwargs)
        try:
            with patch('spectra_app.project_io.write_project', side_effect=held_write), patch(
                    'spectra_app.main_window.FilePicker.getSaveFileName', return_value=(str(path), '')), patch(
                    'spectra_app.main_window.prompt_message', side_effect=[PromptAction.SAVE, PromptAction.CANCEL]) as prompt:
                self.window._on_new_project()
                self.wait_until(started.is_set)
                sheet.name = 'Edited while saving'
                self.window._schedule_project_modified()
                release.set()
                self.wait_idle()
                self.assertEqual(prompt.call_count, 2)
        finally:
            release.set()
        self.assertIs(self.window.workspace, workspace)
        self.assertTrue(self.window.projectModified)
        self.assertNotEqual(read_project(path).sheets[sheet.sheet_id].name, sheet.name)
        self.assertFalse(self.errors)

    def test_cancel_before_atomic_replace_keeps_previous_archive(self):
        path = self.window.save_project(self.folder / 'atomic.spectra')
        before = path.read_bytes()
        cancelled = threading.Event()
        def check():
            if cancelled.is_set():
                raise CancelledError()
        with patch('spectra_app.project_io.os.fsync', side_effect=lambda fd: cancelled.set()):
            with self.assertRaises(CancelledError):
                write_project(path, self.window.workspace, check_cancelled=check)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.folder.glob('.atomic.spectra.*.tmp')), [])

    def test_late_cancel_acknowledges_committed_save_without_continuing_new_or_close(self):
        path = self.folder / 'committed.spectra'
        committed, release = threading.Event(), threading.Event()
        continued = []
        real_replace = os.replace
        def held_replace(*args):
            real_replace(*args)
            committed.set()
            self.assertTrue(release.wait(5))
        try:
            with patch('spectra_app.project_io.os.replace', side_effect=held_replace):
                self.window._save_project_async(path, on_saved=lambda: continued.append(True))
                self.wait_until(committed.is_set)
                self.window._cancel_background_job()
                release.set()
                self.wait_idle()
        finally:
            release.set()
        self.assertEqual(self.window._project_path, path)
        self.assertIsNotNone(self.window._project_saved_digest)
        self.assertFalse(self.window.projectModified)
        self.assertIn('after the file was saved', self.window.statusText)
        self.assertFalse(continued)
        self.assertFalse(self.errors)

    def test_persistence_snapshot_excludes_transient_comparison_undo_buffers(self):
        class TransientHistory:
            def __deepcopy__(self, memo):
                raise AssertionError('transient undo buffers must not be copied')
        comparison = create_comparison(self.window.workspace)
        history = comparison._edit_history = TransientHistory()
        snapshot = self.window._detached_project_snapshot(self.window.workspace)
        self.assertIs(comparison._edit_history, history)
        self.assertFalse(hasattr(snapshot.comparisons[comparison.comparison_id], '_edit_history'))

    def test_successful_ui_open_uses_installed_layout_baseline_and_closes_without_prompt(self):
        self.window.root_window.show()
        self.window.open_workbook(self.book.workbook_id)
        key = ('workbook', self.book.workbook_id, '')
        documents = self.window.graph_documents
        documents.setMode('free')
        pump()
        frame = item(self.window, 'documentFrame_' + '_'.join(key))
        token = documents.captureFrameInput(key, frame)
        self.assertTrue(documents.moveResize(key, frame, token, 5000, 5000, 420, 280))
        pump()
        path = self.window.save_project(self.folder / 'layout.spectra')
        before = self.window.workspace
        self.assertTrue(self.window._open_project_path(path))
        self.wait_idle()
        self.assertIsNot(self.window.workspace, before)
        self.assertEqual(self.window._project_path, path)
        self.assertFalse(self.window.projectModified)
        approved = []
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('clean installed project prompted')):
            self.window._confirm_project_discard_async(lambda: approved.append(True))
            self.wait_idle()
            self.assertEqual(approved, [True])
            self.window.close()
            self.wait_until(lambda: not self.window.root_window.isVisible())
        self.assertFalse(self.errors)

    def test_close_during_save_cancels_drains_and_cancel_guard_resumes_rendering(self):
        self.window.root_window.show()
        path = self.window.save_project(self.folder / 'closing.spectra')
        self.window.workspace.sheets[self.book.sheet_ids[0]].name = 'Dirty Sheet'
        self.window._schedule_project_modified()
        started, release = threading.Event(), threading.Event()
        def held_write(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(5))
            return write_project(*args, **kwargs)
        try:
            with patch('spectra_app.project_io.write_project', side_effect=held_write), patch(
                    'spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as prompt, patch.object(
                    self.window, '_schedule_plot_page_render') as render:
                self.window._on_save_project()
                self.wait_until(started.is_set)
                self.window.close()
                self.assertTrue(self.window.root_window.isVisible())
                release.set()
                self.wait_idle()
                self.wait_until(lambda: prompt.call_count == 1)
                self.assertTrue(self.window.root_window.isVisible())
                self.assertFalse(self.window._project_io_close_pending)
                self.assertTrue(render.called)
        finally:
            release.set()
        self.assertNotEqual(read_project(path).sheets[self.book.sheet_ids[0]].name, 'Dirty Sheet')
