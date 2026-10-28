"""Common prompts through real Quick controls and window keyboard events."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFont
from PySide6.QtQuick import QQuickView
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from spectra_app.common_prompts import CommonPromptBridge, PromptAction
from spectra_app.quick_dialog import QuickDialog

APP = QApplication.instance() or QApplication([])


class CommonPromptQuickTests(unittest.TestCase):
    def setUp(self):
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if shiboken6.isValid(dialog):
                dialog.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def dialog(self, mode, **kwargs):
        bridge = CommonPromptBridge(mode, **kwargs)
        dialog = QuickDialog(bridge, 'CommonPrompt.qml', title='Common prompt',
                             size=(460, 250), minimum_size=(300, 180))
        bridge.acceptRequested.connect(dialog.accept)
        bridge.rejectRequested.connect(dialog.reject)
        self.dialogs.append(dialog)
        dialog.show()
        QTest.qWait(50)
        self.assertIsInstance(dialog.window(), QQuickView)
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
        item = find(dialog.rootObject())
        self.assertIsNotNone(item, name)
        return item

    def click(self, dialog, name):
        item = self.item(dialog, name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
        APP.processEvents()
        return item

    def type_text(self, dialog, text):
        for char in text:
            QTest.keyClick(dialog.window(), char)
        APP.processEvents()

    def test_initial_rename_selection_raw_empty_and_enter_once(self):
        dialog = self.dialog('text', label='Name:', text='Original name')
        editor = self.item(dialog, 'commonPromptEditor')
        self.assertTrue(editor.hasActiveFocus())
        self.assertEqual(editor.property('selectedText'), 'Original name')
        self.type_text(dialog, '  renamed  ')
        self.assertEqual(dialog.bridge.draft, '  renamed  ')
        results = []
        dialog.finished.connect(results.append)
        QTest.keyClick(dialog.window(), Qt.Key_Return)
        self.assertEqual(dialog.bridge.input_value(), '  renamed  ')
        self.assertEqual(results, [dialog.Accepted])
        empty = self.dialog('text', text='Remove me')
        QTest.keyClick(empty.window(), Qt.Key_Backspace)
        QTest.keyClick(empty.window(), Qt.Key_Return)
        self.assertEqual(empty.result(), empty.Accepted)
        self.assertEqual(empty.bridge.input_value(), '')

    def test_integer_intermediate_invalid_enter_and_corrected_mouse_accept(self):
        dialog = self.dialog('int', label='Order:', value=2, minimum=-3, maximum=7)
        self.type_text(dialog, '-')
        self.assertEqual(dialog.bridge.draft, '-')
        QTest.keyClick(dialog.window(), Qt.Key_Return)
        self.assertTrue(dialog.window().isVisible())
        error = self.item(dialog, 'commonPromptValidation')
        self.assertTrue(error.property('visible'))
        self.assertIn('whole number', error.property('text'))
        self.type_text(dialog, '9')
        self.assertIn('-3', error.property('text'))
        QTest.keyClick(dialog.window(), Qt.Key_A, Qt.ControlModifier)
        self.type_text(dialog, '7')
        self.assertFalse(error.property('visible'))
        self.click(dialog, 'commonPromptAction_ok')
        self.assertEqual(dialog.result(), dialog.Accepted)
        self.assertEqual(dialog.bridge.input_value(), 7)

    def test_message_cancel_default_and_focused_button_enter_are_distinct(self):
        kwargs = dict(message='Save changes?', choices=(PromptAction.SAVE, PromptAction.DISCARD, PromptAction.CANCEL),
                      default=PromptAction.CANCEL)
        dialog = self.dialog('message', **kwargs)
        cancel = self.item(dialog, 'commonPromptAction_cancel')
        self.assertTrue(cancel.hasActiveFocus())
        self.assertTrue(cancel.property('primary'))
        QTest.keyClick(dialog.window(), Qt.Key_Return)
        self.assertEqual(dialog.result(), dialog.Rejected)
        self.assertEqual(dialog.bridge.action, PromptAction.CANCEL)
        save = self.dialog('message', **kwargs)
        # Tab traversal deliberately changes which focused action Enter activates.
        QTest.keyClick(save.window(), Qt.Key_Tab)
        self.assertTrue(self.item(save, 'commonPromptAction_save').hasActiveFocus())
        results = []
        save.finished.connect(results.append)
        QTest.keyClick(save.window(), Qt.Key_Return)
        self.assertEqual(save.bridge.action, PromptAction.SAVE)
        self.assertEqual(results, [save.Accepted])

    def test_fixed_item_popup_return_selects_without_accepting_and_empty_cannot_accept(self):
        dialog = self.dialog('item', label='Method:', items=['<b>Literal</b>', 'Second', 'Third'])
        self.assertTrue(self.item(dialog, 'commonPromptChoice').hasActiveFocus())
        self.click(dialog, 'commonPromptChoice')
        QTest.keyClick(dialog.window(), Qt.Key_Down)
        QTest.keyClick(dialog.window(), Qt.Key_Return)
        APP.processEvents()
        self.assertEqual(dialog.bridge.input_value(), 'Second')
        self.assertTrue(dialog.window().isVisible())
        QTest.keyClick(dialog.window(), Qt.Key_Return)
        self.assertEqual(dialog.result(), dialog.Accepted)
        empty = self.dialog('item', items=[])
        QTest.keyClick(empty.window(), Qt.Key_Return)
        self.assertTrue(empty.window().isVisible())
        self.assertTrue(self.item(empty, 'commonPromptValidation').property('visible'))
        self.click(empty, 'commonPromptAction_cancel')
        self.assertEqual(empty.result(), empty.Rejected)

    def test_escape_window_close_and_explicit_mouse_choice_complete_once(self):
        for dismiss in ('escape', 'close'):
            dialog = self.dialog('message', message='Only OK is shown')
            results = []
            dialog.finished.connect(results.append)
            if dismiss == 'escape':
                QTest.keyClick(dialog.window(), Qt.Key_Escape)
            else:
                dialog.window().close()
            APP.processEvents()
            dialog.close()
            self.assertEqual(results, [dialog.Rejected])
            self.assertEqual(dialog.bridge.action, PromptAction.CANCEL)
        dialog = self.dialog('message', choices=(PromptAction.YES, PromptAction.CANCEL), default=PromptAction.CANCEL)
        self.click(dialog, 'commonPromptAction_yes')
        self.assertEqual(dialog.bridge.action, PromptAction.YES)
        self.assertEqual(dialog.result(), dialog.Accepted)

    def test_close_before_deferred_initial_focus_keeps_window_closed(self):
        bridge = CommonPromptBridge('text', text='Never shown again')
        dialog = QuickDialog(bridge, 'CommonPrompt.qml', size=(460, 250), minimum_size=(300, 180))
        self.dialogs.append(dialog)
        bridge.acceptRequested.connect(dialog.accept)
        bridge.rejectRequested.connect(dialog.reject)
        results = []
        dialog.finished.connect(results.append)
        dialog.show()
        dialog.window().close()
        QTest.qWait(30)
        self.assertFalse(dialog.window().isVisible())
        self.assertEqual(results, [dialog.Rejected])

    def test_narrow_long_plain_message_scrolls_and_actions_remain_reachable(self):
        original = QFont(APP.font())
        self.addCleanup(APP.setFont, original)
        for point_size in (10, 16):
            font = QFont('Arial', point_size)
            APP.setFont(font)
            text = '<b>Literal message</b>\n' + ('Long wrapped message with selectable details. ' * 100)
            dialog = self.dialog('message', message=text, choices=tuple(PromptAction), default=PromptAction.CANCEL)
            dialog.resize(300, 180)
            QTest.qWait(50)
            body = self.item(dialog, 'commonPromptMessage')
            self.assertEqual(body.property('text'), text)
            expression = QQmlExpression(QQmlEngine.contextForObject(body), body, 'textFormat === TextEdit.PlainText')
            self.assertTrue(expression.evaluate()[0])
            self.assertGreater(body.property('contentHeight'), self.item(dialog, 'commonPromptContent').height())
            for action in PromptAction:
                button = self.item(dialog, 'commonPromptAction_' + action.value)
                origin = button.mapToScene(QPointF(0, 0))
                self.assertGreaterEqual(origin.x(), 0)
                self.assertGreaterEqual(origin.y(), 0)
                self.assertLessEqual(origin.x() + button.width(), dialog.width() + 1)
                self.assertLessEqual(origin.y() + button.height(), dialog.height() + 1)
                self.assertEqual(button.property('font').pointSize(), point_size)
            self.click(dialog, 'commonPromptAction_cancel')


if __name__ == '__main__':
    unittest.main()
