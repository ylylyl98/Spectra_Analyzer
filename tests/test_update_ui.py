"""Update dialog and accepted-close installer boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEventLoop, QTimer

from spectra_app.app_version import APP_VERSION
from spectra_app.common_prompts import PromptAction
from spectra_app.update_ui import UpdateController
from spectra_app.updates import ReleaseClient, VerifiedInstaller
from tests.test_app_updates import ReleaseFixture
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import pump


def wait_events(milliseconds):
    """Deliver native Quick and worker events through the application loop."""
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


class UpdateUiTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.addCleanup(self.dispose)

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            wait_events(10)
        self.assertTrue(predicate(), 'update operation timed out')

    def dispose(self):
        controller = getattr(self.window, '_update_controller', None)
        if controller:
            controller.cancel()
            self.wait(lambda: not controller.busy)
        self.wait(lambda: not self.window._project_io_jobs.busy)
        with patch.object(self.window, '_confirm_project_discard_async', side_effect=lambda continuation: continuation()):
            self.window.close()
        pump()
        self.assertEqual(self.quick_errors, [])

    def offer_installer(self):
        self.window._show_updates()
        controller = self.window._update_controller
        self.wait(lambda: not controller.busy)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'SpectraAnalyzer-0.2.0-Setup.exe'
        payload = b'MZ installer'
        path.write_bytes(payload)
        installer = VerifiedInstaller(path, hashlib.sha256(payload).hexdigest(), len(payload))
        controller._downloaded(installer)
        return controller, installer

    def import_dirty_project(self):
        source = self.folder / 'source.csv'
        source.write_text('X,Y\n0,2\n1,4\n2,6\n', encoding='utf-8')
        self.window.import_xy_files([source])
        self.assertTrue(self.window.workspace.workbooks)

    @patch('spectra_app.update_ui.ReleaseClient.check', return_value=None)
    def test_help_menu_opens_current_version_and_responsive_check(self, check):
        action = self.window.check_updates_action
        self.assertIn('Check for Updates', action.text())
        action.trigger()
        controller = self.window._update_controller
        self.wait(lambda: not controller.busy)
        self.assertEqual(APP_VERSION, controller.dialog.bridge.currentVersion)
        self.assertIn('up to date', controller.dialog.bridge.statusText)
        self.assertTrue(controller.dialog.isVisible())

    @patch('spectra_app.update_ui.ReleaseClient.check', return_value=None)
    def test_cancel_in_unsaved_guard_does_not_launch_on_later_normal_close(self, check):
        controller, installer = self.offer_installer()
        # This guard observes real asynchronous close delivery, choosing Cancel
        # by withholding its continuation just as the existing project UI does.
        with patch.object(self.window, '_confirm_project_discard_async', return_value=None), patch(
                'spectra_app.update_ui.launch_installer') as launch:
            controller.request_install()
            wait_events(30)
            self.assertTrue(self.host.window.isVisible())
            self.assertIsNone(controller.install_request)
            launch.assert_not_called()
        with patch.object(self.window, '_confirm_project_discard_async', side_effect=lambda continuation: continuation()), patch(
                'spectra_app.update_ui.launch_installer') as launch:
            self.window.close()
            self.wait(lambda: not self.host.window.isVisible())
            launch.assert_not_called()

    @patch('spectra_app.update_ui.ReleaseClient.check', return_value=None)
    def test_installer_launches_only_after_close_approval(self, check):
        controller, installer = self.offer_installer()
        pending = []
        with patch.object(self.window, '_confirm_project_discard_async', side_effect=pending.append), patch(
                'spectra_app.update_ui.launch_installer') as launch:
            controller.request_install()
            self.assertTrue(self.host.window.isVisible())
            launch.assert_not_called()
            pending.pop()()
            self.wait(lambda: not self.host.window.isVisible())
            launch.assert_called_once_with(installer)

    @patch('spectra_app.update_ui.ReleaseClient.check', return_value=None)
    def test_dismissing_dialog_revokes_delayed_install_approval(self, check):
        controller, installer = self.offer_installer()
        pending = []
        with patch.object(self.window, '_confirm_project_discard_async', side_effect=pending.append), patch(
                'spectra_app.update_ui.launch_installer') as launch:
            controller.request_install()
            controller.dialog.reject()
            pending.pop()()
            wait_events(30)
            self.assertTrue(self.host.window.isVisible())
            launch.assert_not_called()

    @patch('spectra_app.update_ui.ReleaseClient.check', return_value=None)
    def test_real_unsaved_cancel_keeps_project_and_revokes_install(self, check):
        self.import_dirty_project()
        controller, installer = self.offer_installer()
        workspace = self.window.workspace
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as prompt, patch(
                'spectra_app.update_ui.launch_installer') as launch:
            controller.request_install()
            self.wait(lambda: not self.window._project_io_jobs.busy)
            wait_events(30)
            prompt.assert_called_once()
            self.assertTrue(self.host.window.isVisible())
            self.assertIs(self.window.workspace, workspace)
            self.assertIsNone(controller.install_request)
            launch.assert_not_called()

    @patch('spectra_app.update_ui.ReleaseClient.check', return_value=None)
    def test_real_async_save_writes_project_before_installer_launch(self, check):
        from spectra_app.project_io import read_project
        self.import_dirty_project()
        controller, installer = self.offer_installer()
        project = self.folder / 'before-update.spectra'
        saved = []
        def launch_verified(value):
            saved.append(read_project(project))
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.SAVE), patch(
                'spectra_app.main_window.FilePicker.getSaveFileName', return_value=(str(project), '')), patch(
                'spectra_app.update_ui.launch_installer', side_effect=launch_verified) as launch:
            controller.request_install()
            self.wait(lambda: not self.host.window.isVisible())
            launch.assert_called_once_with(installer)
        self.assertEqual(len(saved[0].workbooks), 1)

    @patch('spectra_app.update_ui.ReleaseClient.check', return_value=None)
    def test_launch_failure_keeps_window_open_without_automatic_retry(self, check):
        controller, installer = self.offer_installer()
        with patch.object(self.window, '_confirm_project_discard_async', side_effect=lambda continuation: continuation()), patch(
                'spectra_app.update_ui.launch_installer', side_effect=OSError('launch refused')) as launch:
            controller.request_install()
            self.wait(lambda: 'launch refused' in controller.dialog.bridge.statusText)
            self.assertTrue(self.host.window.isVisible())
            self.assertTrue(controller.dialog.isVisible())
            wait_events(30)
            launch.assert_called_once()

    @patch('spectra_app.update_ui.ReleaseClient.check', return_value=None)
    def test_cancelled_save_path_retains_project_without_pending_install(self, check):
        self.import_dirty_project()
        controller, installer = self.offer_installer()
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.SAVE), patch(
                'spectra_app.main_window.FilePicker.getSaveFileName', return_value=('', '')), patch(
                'spectra_app.update_ui.launch_installer') as launch:
            controller.request_install()
            self.wait(lambda: not self.window._project_io_jobs.busy)
            wait_events(30)
            self.assertTrue(self.host.window.isVisible())
            self.assertIsNone(self.window._project_path)
            self.assertIsNone(controller.install_request)
            launch.assert_not_called()

    @patch('spectra_app.update_ui.ReleaseClient.check', return_value=None)
    def test_changed_revision_rechecks_guard_and_consumes_previous_approval(self, check):
        controller, installer = self.offer_installer()
        pending = []
        with patch.object(self.window, '_confirm_project_discard_async', side_effect=pending.append), patch(
                'spectra_app.update_ui.launch_installer') as launch:
            controller.request_install()
            pending.pop()()
            self.window._project_revision += 1
            self.wait(lambda: len(pending) == 1)
            # Declining the renewed guard consumes the previously armed update.
            self.assertTrue(self.host.window.isVisible())
            launch.assert_not_called()
        with patch.object(self.window, '_confirm_project_discard_async', side_effect=lambda continuation: continuation()), patch(
                'spectra_app.update_ui.launch_installer') as launch:
            self.window.close()
            self.wait(lambda: not self.host.window.isVisible())
            launch.assert_not_called()

    def test_closing_during_check_cancels_and_waits_for_owned_thread(self):
        started = threading.Event()
        def check(client, version, context):
            started.set()
            while True:
                context.check_cancelled()
                time.sleep(0.005)
        with patch('spectra_app.update_ui.ReleaseClient.check', new=check), patch.object(
                self.window, '_confirm_project_discard_async', side_effect=lambda continuation: continuation()):
            self.window._show_updates()
            self.wait(started.is_set)
            controller = self.window._update_controller
            self.window.close()
            self.wait(lambda: not self.host.window.isVisible())
            self.assertFalse(controller.busy)

    def test_real_controller_check_download_plain_notes_and_cancel_cleanup(self):
        fixture = ReleaseFixture()
        controller = UpdateController(self.host.window, client=ReleaseClient(fixture.repo, opener=fixture))
        self.window._update_controller = controller
        controller.install_requested.connect(self.window.close)
        controller.open()
        self.wait(lambda: not controller.busy)
        self.assertIn('0.2.0', controller.dialog.bridge.statusText)
        self.assertEqual(controller.dialog.bridge.releaseNotes, fixture.metadata['body'])
        controller.dialog.bridge.downloadUpdate()
        self.wait(lambda: not controller.busy)
        installer = controller.installer
        self.assertIsNotNone(installer)
        self.assertEqual(installer.path.read_bytes(), fixture.payload)
        self.assertIn('Download verified', controller.dialog.bridge.statusText)
        # This is a source-mode run, so installation is offered by the packaged app.
        self.assertFalse(controller.dialog.bridge.canInstall)
        controller.dialog.reject()
        self.assertFalse(installer.path.exists())


if __name__ == '__main__':
    unittest.main()
