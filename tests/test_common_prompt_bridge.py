"""Prompt validation and synchronous modal lifetime at the Python boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from spectra_app.common_prompts import (CommonPromptBridge, PromptAction,
                                        prompt_int, prompt_item, prompt_message,
                                        prompt_text)
from spectra_app.quick_dialog import QuickDialog


APP = QApplication.instance() or QApplication([])


class CommonPromptBridgeTests(unittest.TestCase):
    def test_raw_text_empty_and_whitespace_are_accepted_without_domain_validation(self):
        bridge = CommonPromptBridge('text', text='original')
        accepted = QSignalSpy(bridge.acceptRequested)
        for text in ('', '  raw name\n'):
            with self.subTest(text=text):
                fresh = CommonPromptBridge('text', text=text)
                self.assertTrue(fresh.canAccept)
                self.assertEqual(fresh.input_value(), text)
        bridge.setDraft('')
        bridge.activateDefault()
        self.assertEqual(accepted.count(), 1)
        self.assertEqual(bridge.input_value(), '')

    def test_fixed_items_empty_cannot_accept_and_selection_returns_exact_item(self):
        empty = CommonPromptBridge('item')
        accepted = QSignalSpy(empty.acceptRequested)
        empty.activateDefault()
        self.assertEqual(accepted.count(), 0)
        self.assertFalse(empty.canAccept)
        self.assertIn('Choose', empty.validationError)
        self.assertEqual(empty.currentIndex, -1)
        choices = CommonPromptBridge('item', items=['first', '  second  '], current=1)
        self.assertEqual(choices.input_value(), '  second  ')
        choices.setCurrentIndex(0)
        self.assertEqual(choices.input_value(), 'first')
        choices.setCurrentIndex(10)
        self.assertFalse(choices.canAccept)

    def test_integer_intermediate_nonfinite_and_range_errors_then_inclusive_boundaries(self):
        for draft in ('', '-', '+', '1.2', 'nan', 'inf', '1e2', '-3', '4'):
            with self.subTest(draft=draft):
                bridge = CommonPromptBridge('int', value=0, minimum=-2, maximum=3)
                accepted = QSignalSpy(bridge.acceptRequested)
                bridge.setDraft(draft)
                self.assertEqual(bridge.draft, draft)
                self.assertFalse(bridge.canAccept)
                self.assertEqual(bridge.validationError, '')
                bridge.activateDefault()
                self.assertEqual(accepted.count(), 0)
                self.assertTrue(bridge.validationError)
                bridge.setDraft('-2')
                self.assertEqual(bridge.validationError, '')
                self.assertTrue(bridge.canAccept)
        for draft, expected in (('-2', -2), ('+3', 3), (' 0 ', 0)):
            bridge = CommonPromptBridge('int', minimum=-2, maximum=3)
            bridge.setDraft(draft)
            accepted = QSignalSpy(bridge.acceptRequested)
            bridge.activateDefault()
            self.assertEqual(accepted.count(), 1)
            self.assertEqual(bridge.input_value(), expected)
            self.assertIsInstance(bridge.input_value(), int)

    def test_message_cancel_default_and_explicit_actions_request_completion_once(self):
        choices = (PromptAction.SAVE, PromptAction.DISCARD, PromptAction.CANCEL)
        bridge = CommonPromptBridge('message', choices=choices, default=PromptAction.CANCEL)
        rejected = QSignalSpy(bridge.rejectRequested)
        accepted = QSignalSpy(bridge.acceptRequested)
        self.assertEqual([a['key'] for a in bridge.actions], ['save', 'discard', 'cancel'])
        bridge.activateDefault()
        bridge.cancel()
        bridge.chooseAction('save')
        self.assertEqual(rejected.count(), 1)
        self.assertEqual(accepted.count(), 0)
        self.assertEqual(bridge.action, PromptAction.CANCEL)
        bridge = CommonPromptBridge('message', choices=choices)
        accepted = QSignalSpy(bridge.acceptRequested)
        bridge.chooseAction('discard')
        bridge.chooseAction('save')
        self.assertEqual(accepted.count(), 1)
        self.assertEqual(bridge.action, PromptAction.DISCARD)

    def test_shutdown_freezes_result_and_draft(self):
        bridge = CommonPromptBridge('text', text='before')
        accepted = QSignalSpy(bridge.acceptRequested)
        rejected = QSignalSpy(bridge.rejectRequested)
        bridge.shutdown()
        bridge.setDraft('after')
        bridge.chooseAction('ok')
        bridge.cancel()
        self.assertEqual(bridge.input_value(), 'before')
        self.assertFalse(bridge.canAccept)
        self.assertEqual(accepted.count(), 0)
        self.assertEqual(rejected.count(), 0)


class CommonPromptModalTests(unittest.TestCase):
    def setUp(self):
        self._callback_errors = []
        self._watchdogs = []

    def tearDown(self):
        for watchdog in self._watchdogs:
            watchdog.stop()
            watchdog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertEqual(self._callback_errors, [])

    def _guard(self, reject):
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        def timeout():
            self._callback_errors.append('Modal callback did not complete within 3 seconds')
            reject()
        watchdog.timeout.connect(timeout)
        watchdog.start(3000)
        self._watchdogs.append(watchdog)
        return watchdog

    def _schedule(self, callback):
        def close_windows():
            for window in APP.allWindows():
                window.close()
        watchdog = self._guard(close_windows)
        def act():
            try:
                hosts = [w for w in APP.allWindows() if w.isVisible()]
                self.assertEqual(len(hosts), 1)
                callback(hosts[0].rootObject().property('bridge'))
            except Exception as error:
                self._callback_errors.append(repr(error))
                close_windows()
            finally:
                watchdog.stop()
        QTimer.singleShot(0, act)

    def test_public_helpers_return_actual_modal_results(self):
        self._schedule(lambda bridge: (bridge.setDraft(''), bridge.chooseAction('ok')))
        self.assertEqual(prompt_text(None, 'Rename', 'Name', text='old'), ('', True))
        self._schedule(lambda bridge: (bridge.setCurrentIndex(1), bridge.chooseAction('ok')))
        self.assertEqual(prompt_item(None, 'Item', 'Choose', ['a', 'b']), ('b', True))
        self._schedule(lambda bridge: (bridge.setDraft('-4'), bridge.chooseAction('ok')))
        self.assertEqual(prompt_int(None, 'Count', 'Count', value=2, min=-4, max=8), (-4, True))
        self._schedule(lambda bridge: bridge.activateDefault())
        self.assertEqual(prompt_message(None, 'Save', 'Unsaved',
                         (PromptAction.SAVE, PromptAction.CANCEL), PromptAction.CANCEL), PromptAction.CANCEL)
        self._schedule(lambda bridge: (bridge.setDraft('changed'), bridge.cancel()))
        self.assertEqual(prompt_text(None, 'Rename', 'Name', text='old'), ('old', False))

    def test_transient_ancestry_compact_minimum_and_legacy_default(self):
        owner = QWidget()
        child = QObject(owner)
        compact = QuickDialog(CommonPromptBridge('text'), 'CommonPrompt.qml', child,
                              minimum_size=(300, 180))
        legacy = QuickDialog(CommonPromptBridge('text'), 'CommonPrompt.qml')
        try:
            self.assertIs(compact.window().transientParent(), owner.windowHandle())
            self.assertEqual((compact.window().minimumWidth(), compact.window().minimumHeight()), (300, 180))
            self.assertEqual((legacy.window().minimumWidth(), legacy.window().minimumHeight()), (380, 360))
        finally:
            compact.deleteLater()
            legacy.deleteLater()
            owner.deleteLater()

    def test_parent_deletion_wakes_nested_modal_and_finishes_once(self):
        owner = QObject()
        bridge = CommonPromptBridge('text', text='original')
        host = QuickDialog(bridge, 'CommonPrompt.qml', owner)
        finished = QSignalSpy(host.finished)
        watchdog = self._guard(host.reject)
        QTimer.singleShot(0, owner.deleteLater)
        self.assertEqual(host.exec(), QuickDialog.Rejected)
        watchdog.stop()
        self.assertEqual(finished.count(), 1)
        self.assertFalse(isValid(owner))
        self.assertFalse(isValid(host))


if __name__ == '__main__':
    unittest.main()
