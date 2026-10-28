"""Actual pointer and keyboard interaction with the Stage19 Quick prompts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from spectra_app.common_prompts import CommonPromptBridge
from spectra_app.curve_prompts import CurveColorPromptBridge
from spectra_app.quick_dialog import QuickDialog


APP = QApplication.instance() or QApplication([])


class CurvePromptQuickTests(unittest.TestCase):
    def setUp(self):
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if shiboken6.isValid(dialog):
                dialog.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def dialog(self, initial='#336699', *, parent=None, show=True):
        bridge = CurveColorPromptBridge(initial)
        dialog = QuickDialog(bridge, 'CurveColorPrompt.qml', parent, title='Curve color',
                             size=(500, 570), minimum_size=(380, 470))
        bridge.acceptRequested.connect(dialog.accept)
        bridge.rejectRequested.connect(dialog.reject)
        self.dialogs.append(dialog)
        if show:
            dialog.show()
            QTest.qWait(40)
        self.assertEqual(dialog.window().status(), QQuickView.Ready)
        self.assertFalse(dialog.findChildren(QWidget))
        return dialog

    def item(self, dialog, name):
        def find(root):
            if root.objectName() == name:
                return root
            for child in root.childItems():
                result = find(child)
                if result is not None:
                    return result
        found = find(dialog.rootObject())
        self.assertIsNotNone(found, name)
        return found

    def click(self, dialog, name, x=.5, y=.5):
        item = self.item(dialog, name)
        point = item.mapToScene(QPointF(item.width() * x, item.height() * y)).toPoint()
        QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
        APP.processEvents()
        return item

    def replace(self, dialog, text):
        QTest.keyClick(dialog.window(), Qt.Key_A, Qt.ControlModifier)
        for char in text:
            QTest.keyClick(dialog.window(), char)
        APP.processEvents()

    def test_exact_hex_and_name_entry_enter_completes_once(self):
        for draft, expected in (('#a1B2c3', '#a1b2c3'), ('tomato', '#ff6347')):
            dialog = self.dialog()
            editor = self.item(dialog, 'curveColorEditor')
            self.assertTrue(editor.hasActiveFocus())
            self.assertEqual(editor.property('selectedText'), '#336699')
            self.replace(dialog, draft)
            self.assertEqual(dialog.bridge.normalizedColor, expected)
            self.assertEqual(editor.property('text'), draft)
            results = []
            dialog.finished.connect(results.append)
            QTest.keyClick(dialog.window(), Qt.Key_Return)
            dialog.bridge.accept()
            self.assertEqual(results, [dialog.Accepted])
            self.assertEqual(dialog.bridge.input_value(), expected)

    def test_invalid_raw_entry_blocks_accept_retains_preview_then_correction(self):
        dialog = self.dialog()
        self.replace(dialog, 'not a color')
        self.assertEqual(dialog.bridge.draft, 'not a color')
        self.assertEqual(dialog.bridge.normalizedColor, '#336699')
        self.assertFalse(self.item(dialog, 'curveColorAction_ok').isEnabled())
        self.assertTrue(self.item(dialog, 'curveColorValidation').isVisible())
        QTest.keyClick(dialog.window(), Qt.Key_Return)
        self.assertTrue(dialog.window().isVisible())
        self.replace(dialog, '#010203')
        self.assertFalse(self.item(dialog, 'curveColorValidation').isVisible())
        self.click(dialog, 'curveColorAction_ok')
        self.assertEqual(dialog.result(), dialog.Accepted)
        self.assertEqual(dialog.bridge.input_value(), '#010203')

    def test_visual_hue_sv_rgb_pointer_and_keyboard_change_only_draft(self):
        dialog = self.dialog('#ff0000')
        finished = []
        dialog.finished.connect(finished.append)
        self.click(dialog, 'curveColorHue', x=.35)
        self.click(dialog, 'curveColorSaturationValue', x=.75, y=.25)
        self.assertAlmostEqual(dialog.bridge.saturation, .75, delta=.03)
        self.assertAlmostEqual(dialog.bridge.value, .75, delta=.03)
        self.assertGreater(dialog.bridge.green, dialog.bridge.red)
        before = dialog.bridge.saturation
        QTest.keyClick(dialog.window(), Qt.Key_Left)
        self.assertLess(dialog.bridge.saturation, before)
        self.click(dialog, 'curveColorSaturationValue', x=.95, y=.03)
        self.assertGreater(dialog.bridge.saturation, .94)
        for _ in range(7):
            QTest.keyClick(dialog.window(), Qt.Key_Right)
            QTest.keyClick(dialog.window(), Qt.Key_Up)
        self.assertEqual(dialog.bridge.saturation, 1.)
        self.assertEqual(dialog.bridge.value, 1.)
        self.click(dialog, 'curveColorBlue', x=.8)
        self.assertGreater(dialog.bridge.blue, 170)
        previous = dialog.bridge.blue
        QTest.keyClick(dialog.window(), Qt.Key_Left)
        self.assertLess(dialog.bridge.blue, previous)
        self.assertEqual(self.item(dialog, 'curveColorEditor').property('text'), dialog.bridge.normalizedColor)
        self.assertEqual(finished, [])
        self.assertTrue(dialog.window().isVisible())
        self.click(dialog, 'curveColorAction_cancel')
        self.assertEqual(finished, [dialog.Rejected])

    def test_escape_close_and_owner_close_reject_once(self):
        for method in ('escape', 'window', 'owner'):
            owner = QWidget()
            dialog = self.dialog(parent=owner)
            results = []
            dialog.finished.connect(results.append)
            if method == 'escape':
                QTest.keyClick(dialog.window(), Qt.Key_Escape)
            elif method == 'window':
                dialog.window().close()
            else:
                owner.deleteLater()
                APP.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()
            if shiboken6.isValid(dialog):
                dialog.close()
                self.assertFalse(dialog.window().isVisible())
            self.assertEqual(results, [dialog.Rejected])
            if shiboken6.isValid(owner):
                owner.deleteLater()

    def test_closed_before_deferred_focus_and_minimum_size_actions(self):
        dialog = self.dialog(show=False)
        dialog.show()
        dialog.window().close()
        QTest.qWait(30)
        self.assertFalse(dialog.window().isVisible())
        narrow = self.dialog()
        narrow.resize(380, 470)
        QTest.qWait(30)
        for name in ('curveColorAction_ok', 'curveColorAction_cancel', 'curveColorEditor',
                     'curveColorSaturationValue'):
            item = self.item(narrow, name)
            origin = item.mapToScene(QPointF(0, 0))
            self.assertGreaterEqual(origin.x(), 0)
            self.assertGreaterEqual(origin.y(), 0)
            self.assertLessEqual(origin.x() + item.width(), narrow.width() + 1)
            self.assertLessEqual(origin.y() + item.height(), narrow.height() + 1)
        self.click(narrow, 'curveColorAction_cancel')
        self.assertEqual(narrow.result(), narrow.Rejected)

    def test_float_finite_range_draft_and_keyboard_correction(self):
        bridge = CommonPromptBridge('float', label='Width:', value=1.25,
                                    minimum=.000001, maximum=50., decimals=6)
        dialog = QuickDialog(bridge, 'CommonPrompt.qml', size=(460, 250), minimum_size=(300, 180))
        self.dialogs.append(dialog)
        bridge.acceptRequested.connect(dialog.accept)
        bridge.rejectRequested.connect(dialog.reject)
        dialog.show()
        QTest.qWait(40)
        editor = self.item(dialog, 'commonPromptEditor')
        self.assertTrue(editor.hasActiveFocus())
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.property('selectedText'), '1.250000')
        for raw in ('-', 'nan', 'inf', '50.000001', '0'):
            self.replace(dialog, raw)
            QTest.keyClick(dialog.window(), Qt.Key_Return)
            self.assertEqual(bridge.draft, raw)
            self.assertTrue(dialog.window().isVisible())
            self.assertTrue(self.item(dialog, 'commonPromptValidation').isVisible())
        self.replace(dialog, '0.000001')
        self.assertFalse(self.item(dialog, 'commonPromptValidation').isVisible())
        self.click(dialog, 'commonPromptAction_ok')
        self.assertEqual(dialog.result(), dialog.Accepted)
        self.assertEqual(bridge.input_value(), .000001)


if __name__ == '__main__':
    unittest.main()
