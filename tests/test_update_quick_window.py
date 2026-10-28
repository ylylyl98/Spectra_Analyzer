"""Update actions travel through real QML controls without external updates."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import hashlib
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QEventLoop, QPointF, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from spectra_app.update_ui import UpdateController
from spectra_app.updates import VerifiedInstaller


APP = QApplication.instance() or QApplication([])


def wait_events(milliseconds):
    """Pump the application loop while the native Quick renderer runs."""
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


class LocalUpdates:
    """A synthetic release; all installer bytes are disposable local data."""
    def __init__(self):
        self.checks = 0
        self.downloads = 0
        self.release = SimpleNamespace(version='9.9.9', notes='<b>Plain notes</b>\nRelease details')

    def check(self, version, context):
        self.checks += 1
        context.report('Reading synthetic release…')
        return self.release

    def download(self, release, directory, context):
        self.downloads += 1
        context.report('Verifying local fixture…')
        payload = b'local fixture, never executable'
        path = Path(directory) / 'fixture.exe'
        path.write_bytes(payload)
        return VerifiedInstaller(path, hashlib.sha256(payload).hexdigest(), len(payload))


class UpdateQuickWindowTests(unittest.TestCase):
    def setUp(self):
        self.owner = QWidget()
        self.owner.show()
        self.client = LocalUpdates()
        self.controller = UpdateController(self.owner, client=self.client)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.controller.cancel()
        self.wait(lambda: not self.controller.busy)
        if self.controller.dialog:
            self.controller.dialog.hide()
        self.owner.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            wait_events(10)
        self.assertTrue(predicate(), 'update window operation timed out')

    def show(self):
        self.controller.open()
        self.wait(lambda: not self.controller.busy)
        wait_events(40)
        view = self.controller.dialog
        self.assertIsInstance(view, QQuickView)
        self.assertEqual(view.status(), QQuickView.Ready)
        self.assertIsNotNone(view.rootObject())
        return view

    def item(self, name):
        def find(root):
            if root.objectName() == name:
                return root
            for child in root.childItems():
                found = find(child)
                if found is not None:
                    return found
            return None
        item = find(self.controller.dialog.rootObject())
        self.assertIsNotNone(item, name)
        return item

    def click(self, name):
        item = self.item(name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.controller.dialog, Qt.LeftButton, pos=point)
        APP.processEvents()
        return item

    def test_real_pointer_keyboard_notes_and_source_install_gate(self):
        with patch('spectra_app.update_ui.sys.frozen', False, create=True):
            view = self.show()
        self.assertEqual(view.modality(), Qt.NonModal)
        self.assertIs(view.transientParent(), self.owner.windowHandle())
        notes = self.item('updateReleaseNotes')
        self.assertTrue(notes.property('readOnly'))
        self.assertTrue(notes.property('selectByMouse'))
        self.assertEqual(notes.property('text'), self.client.release.notes)
        notes.forceActiveFocus()
        QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(notes.property('selectedText'), self.client.release.notes)
        QTest.keyClick(view, Qt.Key_Backspace)
        self.assertEqual(notes.property('text'), self.client.release.notes)
        check = self.item('updateCheck')
        check.forceActiveFocus(Qt.TabFocusReason)
        self.assertTrue(check.property('activeFocus'))
        self.assertTrue(check.property('visualFocus'))
        QTest.keyClick(view, Qt.Key_Space)
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.client.checks, 2)
        self.click('updateDownload')
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.client.downloads, 1)
        self.assertIn('Download verified', view.bridge.statusText)
        self.assertFalse(self.item('updateDownload').property('enabled'))
        self.assertFalse(self.item('updateInstall').property('enabled'))
        requested = []
        self.controller.install_requested.connect(lambda: requested.append(True))
        self.click('updateInstall')
        self.assertFalse(requested)
        installer = self.controller.installer
        self.click('updateClose')
        self.assertFalse(view.isVisible())
        self.assertFalse(installer.path.exists())

    def test_packaged_qml_install_request_native_close_escape_and_reuse(self):
        with patch('spectra_app.update_ui.sys.frozen', True, create=True):
            view = self.show()
        requested = []
        self.controller.install_requested.connect(lambda: requested.append(True))
        self.click('updateDownload')
        self.wait(lambda: not self.controller.busy)
        self.assertTrue(self.item('updateInstall').property('enabled'))
        with patch('spectra_app.update_ui.launch_installer') as launch:
            install = self.item('updateInstall')
            install.forceActiveFocus(Qt.TabFocusReason)
            QTest.keyClick(view, Qt.Key_Space)
            self.assertEqual(requested, [True])
            request = self.controller.take_install_request()
            self.assertTrue(self.controller.request_is_current(request))
            self.assertTrue(self.controller.arm_install(request))
            view.close()
            self.assertFalse(view.isVisible())
            self.assertFalse(self.controller.request_is_current(request))
            self.assertIsNone(self.controller._armed)
            self.controller.accept_close()
            launch.assert_not_called()
        self.controller.open()
        self.wait(lambda: not self.controller.busy)
        self.assertIs(self.controller.dialog, view)
        self.assertTrue(view.isVisible())
        QTest.keyClick(view, Qt.Key_Escape)
        self.assertFalse(view.isVisible())
        self.controller.open()
        self.wait(lambda: not self.controller.busy)
        self.assertIs(self.controller.dialog, view)
        self.assertTrue(view.isVisible())
        self.assertEqual(self.client.checks, 3)

    def test_cancel_hides_reusable_window_but_retains_worker_until_idle(self):
        started, cancelled, release = threading.Event(), threading.Event(), threading.Event()
        def slow_check(version, context):
            started.set()
            context.report('Waiting for synthetic cancellation…')
            while not release.wait(.005):
                try:
                    context.check_cancelled()
                except Exception:
                    cancelled.set()
            context.check_cancelled()
        self.client.check = slow_check
        self.addCleanup(release.set)
        self.controller.open()
        self.wait(started.is_set)
        view = self.controller.dialog
        wait_events(40)
        self.assertFalse(self.item('updateCheck').property('enabled'))
        self.assertFalse(self.item('updateDownload').property('enabled'))
        self.assertEqual(self.item('updateClose').property('text'), 'Cancel')
        self.assertEqual(self.item('updateStatus').property('text'), 'Waiting for synthetic cancellation…')
        self.assertTrue(self.item('updateProgress').property('visible'))
        self.click('updateClose')
        self.wait(cancelled.is_set)
        self.assertFalse(view.isVisible())
        self.assertTrue(self.controller.busy)
        self.controller.open()
        self.assertIs(self.controller.dialog, view)
        self.assertTrue(view.isVisible())
        self.assertTrue(view.bridge.busy)
        release.set()
        self.wait(lambda: not self.controller.busy)
        self.assertTrue(self.item('updateCheck').property('enabled'))

    def test_reflow_at_default_and_narrow_enlarged_font_keeps_actions_visible(self):
        original_font = APP.font()
        self.addCleanup(APP.setFont, original_font)
        view = self.show()
        for size, font in (((560, 390), original_font), ((380, 390), QFont('Segoe UI', 15))):
            APP.setFont(font)
            view.resize(*size)
            wait_events(60)
            items = [self.item(name) for name in ('updateCheck', 'updateDownload', 'updateInstall', 'updateClose')]
            for item in items:
                top = item.mapToScene(QPointF(0, 0))
                self.assertGreaterEqual(top.x(), 0)
                self.assertGreaterEqual(top.y(), 0)
                self.assertLessEqual(top.x() + item.width(), view.width())
                self.assertLessEqual(top.y() + item.height(), view.height())
                self.assertEqual(item.property('font').pointSize(), font.pointSize())
            if size[0] == 380:
                self.assertGreater(items[-1].mapToScene(QPointF()).y(), items[0].mapToScene(QPointF()).y())

    def test_owner_disposal_deletes_quick_window_and_bridge(self):
        owner = QWidget()
        controller = UpdateController(owner, client=LocalUpdates())
        try:
            controller.open()
            self.wait(lambda: not controller.busy)
            view, bridge = controller.dialog, controller.dialog.bridge
            owner.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertFalse(isValid(controller))
            self.assertFalse(isValid(view))
            self.assertFalse(isValid(bridge))
        finally:
            if isValid(owner):
                controller.cancel()
                self.wait(lambda: not controller.busy)
                owner.deleteLater()
                APP.sendPostedEvents(None, QEvent.DeferredDelete)
                APP.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
