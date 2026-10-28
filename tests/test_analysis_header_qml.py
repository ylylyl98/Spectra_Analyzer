"""Actual Quick input at the shared Analysis header/Gate consumer boundary.

This fixture proves presentation and captured-owner routing; production numeric
parity, lifecycle and scientific ownership have separate bridge tests.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from pathlib import Path
import unittest

from PySide6.QtCore import QObject, QPointF, Property, Qt, Signal, Slot, qInstallMessageHandler
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtQml import QQmlExpression, QQmlEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.quick_ui import QuickPanel
from tests.formal_quick_helpers import item, pump

APP = QApplication.instance() or QApplication([])
# Windows' offscreen plugin has no system font database. Load the font used by
# run_qt.main explicitly so geometry measures normal text rather than tofu boxes.
if not QFontDatabase.families():
    QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / 'arial.ttf'))
FULL_NAME = '<b>sample</b>_' + 'long_source_name_' * 16 + '.dat'
DETAILS = ('Source: ' + FULL_NAME + '\nPath: C:/measurements/' + FULL_NAME
           + '\nGraph: Reference <em>literal</em>\nVersion: frozen input 7'
           + '\nRecipe: Original\nScope: All Gates\nDetection: Direct; original finite samples.')


class HeaderFixture(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.token = 'workspace:dataset:graph:input'
        self.closed = self.busy = False
        self.error_field = self.error_text = ''
        self.calls = []
        self.values = [0.0, 0.1000002, 0.9000007]
        self.position = self.changes = 0
        self.data = dict(title=FULL_NAME, summary='Frozen Original · 3/3 Gates · 2 analyzed',
                         warning='Source changed', details=DETAILS)
        self.reset_fields()

    def reset_fields(self):
        self.data.update(gateText=f'{self.values[self.position]:.6f}',
                         indexText=str(self.position + 1), count=len(self.values),
                         index=self.position + 1, canPrevious=self.position > 0,
                         canNext=self.position < len(self.values) - 1)

    state = Property('QVariantMap', lambda self: deepcopy(self.data), notify=stateChanged)
    ownerToken = Property(str, lambda self: self.token, notify=stateChanged)
    enabled = Property(bool, lambda self: not self.closed, notify=stateChanged)
    backEnabled = Property(bool, lambda self: not self.closed and not self.busy, notify=stateChanged)
    errorField = Property(str, lambda self: self.error_field, notify=stateChanged)
    errorText = Property(str, lambda self: self.error_text, notify=stateChanged)

    def valid(self, owner):
        return not self.closed and owner == self.token

    def publish(self):
        self.stateChanged.emit()
        self.draftChanged.emit()

    @Slot(str, str, str, result=bool)
    def setDraft(self, field, text, owner):
        self.calls.append(('draft', field, text, owner))
        if not self.valid(owner):
            return False
        self.data[field + 'Text'] = text
        self.publish()
        return True

    @Slot(str, result=bool)
    def commit(self, owner):
        self.calls.append(('commit', owner))
        if not self.valid(owner):
            return False
        try:
            gate = float(self.data['gateText'])
            index = int(self.data['indexText'])
            if not 0 <= gate <= float(f'{self.values[-1]:.6f}') or not 1 <= index <= len(self.values):
                raise ValueError
        except ValueError:
            self.error_field = 'gate' if self.data['gateText'] in ('-', '', 'nan') else 'index'
            self.error_text = 'Enter a valid Gate value or index.'
            self.publish()
            self.focusRequested.emit(self.error_field, owner)
            return False
        position = (index - 1 if self.data['indexText'] != str(self.position + 1)
                    else min(range(len(self.values)), key=lambda i: abs(self.values[i] - gate)))
        if position != self.position:
            self.changes += 1
            self.position = position
        self.error_field = self.error_text = ''
        self.reset_fields()
        self.publish()
        return True

    @Slot(int, str, result=bool)
    def step(self, delta, owner):
        self.calls.append(('step', delta, owner))
        if not self.commit(owner):
            return False
        position = max(0, min(len(self.values) - 1, self.position + delta))
        self.changes += position != self.position
        self.position = position
        self.reset_fields()
        self.publish()
        return True

    @Slot(str, result=bool)
    def back(self, owner):
        self.calls.append(('back', owner))
        return self.valid(owner) and not self.busy

    def replace_owner(self):
        self.token = 'workspace:replacement:graph:input'
        self.position = 0
        self.error_field = self.error_text = ''
        self.reset_fields()
        self.publish()


class AnalysisHeaderQmlTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.old_handler = qInstallMessageHandler(lambda kind, context, text: self.messages.append(text))
        self.bridge = HeaderFixture()
        self.hosts = []

    def tearDown(self):
        for host in self.hosts:
            host.close()
            host.deleteLater()
        pump()
        qInstallMessageHandler(self.old_handler)
        self.assertEqual([text for text in self.messages if '.qml:' in text], [])

    def panel(self, filename):
        host = QuickPanel(filename, self.bridge, bridge_property='bridge')
        host.setFont(QFont('Arial', 10))
        self.hosts.append(host)
        host.resize(442, host.height())
        host.show()
        host.activateWindow()
        pump(60)
        self.assertEqual(host.status(), QQuickWidget.Ready)
        return host

    def click(self, host, name):
        control = item(host, name)
        point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2)).toPoint()
        receiver = host if control.window() is host.quickWindow() else control.window()
        QTest.mouseClick(receiver, Qt.LeftButton, pos=point)
        pump()
        return control

    def edit(self, host, name, text):
        control = self.click(host, name)
        QTest.keyClick(host, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(host, text)
        pump()
        return control

    def test_compact_plain_context_full_selectable_details_and_owner_close(self):
        host = self.panel('AnalysisHeader.qml')
        self.assertLessEqual(host.height(), 56)
        title = item(host, 'contextTitle')
        self.assertEqual(title.property('text'), FULL_NAME)
        self.assertTrue(QQmlExpression(QQmlEngine.contextForObject(title), title,
                                       'textFormat === Text.PlainText').evaluate()[0])
        self.assertLess(title.width(), title.property('implicitWidth'))
        self.assertEqual(host.rootObject().property('accessibleDetails'), DETAILS)
        self.assertEqual(item(host, 'contextWarning').property('text'), 'Source changed')
        self.click(host, 'contextDetails')
        popup = host.rootObject().findChild(QObject, 'analysisContextPopup')
        self.assertTrue(popup.property('visible'))
        content = item(host, 'contextDetailsText')
        self.assertIsNot(content.window(), host.quickWindow())
        self.assertGreater(content.window().height(), host.height())
        self.assertEqual(content.property('text'), DETAILS)
        self.assertTrue(QQmlExpression(QQmlEngine.contextForObject(content), content,
                                       'textFormat === TextEdit.PlainText').evaluate()[0])
        self.assertTrue(content.property('readOnly'))
        self.assertTrue(content.property('selectByMouse'))
        content.forceActiveFocus()
        QTest.keyClick(content.window(), Qt.Key_A, Qt.ControlModifier)
        pump()
        self.assertEqual(content.property('selectedText'), DETAILS)
        self.bridge.replace_owner()
        pump()
        self.assertFalse(popup.property('visible'))

    def test_details_keyboard_access_and_source_removed_warning(self):
        host = self.panel('AnalysisHeader.qml')
        self.bridge.data['warning'] = 'Source removed'
        self.bridge.publish()
        details = item(host, 'contextDetails')
        details.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(host, Qt.Key_Space)
        pump()
        popup = host.rootObject().findChild(QObject, 'analysisContextPopup')
        self.assertTrue(popup.property('visible'))
        QTest.keyClick(item(host, 'contextDetailsText').window(), Qt.Key_Escape)
        pump()
        self.assertFalse(popup.property('visible'))
        warning = item(host, 'contextWarning')
        self.assertEqual(warning.property('text'), 'Source removed')
        self.assertLessEqual(warning.property('contentHeight'), warning.height())

    def test_source_tooltip_preserves_full_literal_filename(self):
        host = self.panel('AnalysisHeader.qml')
        title = item(host, 'contextTitle')
        point = title.mapToScene(QPointF(title.width() / 2, title.height() / 2)).toPoint()
        QTest.mouseMove(host, point)
        pump(700)
        tooltip = host.rootObject().findChild(QObject, 'contextSourceTooltip')
        self.assertTrue(tooltip.property('visible'))
        text = item(host, 'contextSourceTooltipText')
        self.assertEqual(text.property('text'), FULL_NAME)
        self.assertTrue(QQmlExpression(QQmlEngine.contextForObject(text), text,
                                       'textFormat === Text.PlainText').evaluate()[0])
        self.assertLessEqual(text.property('contentHeight'), text.height())
        self.assertIsNot(text.window(), host.quickWindow())

    def test_typed_gate_index_arrows_and_buttons_follow_array(self):
        host = self.panel('AnalysisGate.qml')
        self.assertLessEqual(host.height(), 34)
        self.edit(host, 'gateValue', '0.11')
        self.assertEqual(self.bridge.position, 0)
        QTest.keyClick(host, Qt.Key_Return)
        pump()
        self.assertEqual(self.bridge.position, 1)
        self.assertEqual(self.bridge.changes, 1)
        self.click(host, 'gateIndex')
        self.assertEqual(self.bridge.changes, 1)
        self.edit(host, 'gateIndex', '3')
        QTest.keyClick(host, Qt.Key_Return)
        pump()
        self.assertEqual(self.bridge.values[self.bridge.position], 0.9000007)
        QTest.keyClick(host, Qt.Key_Down)
        pump()
        self.assertEqual(self.bridge.position, 1)
        self.click(host, 'gatePrevious')
        self.assertEqual(self.bridge.position, 0)
        self.click(host, 'gateNext')
        self.assertEqual(self.bridge.values[self.bridge.position], 0.1000002)
        self.assertTrue(any(call[:2] == ('step', -1) for call in self.bridge.calls))
        for name in ('gateValue', 'gateIndex', 'gatePrevious', 'gateNext'):
            control = item(host, name)
            self.assertLessEqual(control.x() + control.width(), host.width())
            self.assertLessEqual(control.y() + control.height(), host.height())

    def test_invalid_raw_text_refocus_and_same_owner_cursor_survive_refresh(self):
        host = self.panel('AnalysisGate.qml')
        field = self.edit(host, 'gateValue', '-')
        self.assertEqual(field.property('text'), '-')
        QTest.keyClick(host, Qt.Key_Return)
        pump()
        self.assertTrue(field.hasActiveFocus())
        self.assertEqual(field.property('text'), '-')
        error = item(host, 'gateError')
        self.assertTrue(error.isVisible())
        self.assertGreater(error.height(), 0)
        self.assertLessEqual(error.property('contentHeight'), error.height())
        self.assertEqual(self.bridge.position, 0)
        field = self.edit(host, 'gateValue', '0.100000')
        QTest.keyClick(host, Qt.Key_Left)
        QTest.keyClick(host, Qt.Key_Left)
        cursor = field.property('cursorPosition')
        for _ in range(4):
            self.bridge.publish()
            pump()
        self.assertEqual(field.property('text'), '0.100000')
        self.assertEqual(field.property('cursorPosition'), cursor)
        self.assertTrue(field.hasActiveFocus())
        self.click(host, 'gateNext')
        self.assertEqual(self.bridge.position, 2)

    def test_busy_keeps_gate_enabled_back_disabled_and_stale_press_rejected(self):
        host = self.panel('AnalysisGate.qml')
        header = self.panel('AnalysisHeader.qml')
        self.bridge.busy = True
        self.bridge.publish()
        pump()
        self.assertFalse(item(header, 'analysisBack').isEnabled())
        self.assertTrue(item(host, 'gateValue').isEnabled())
        self.click(host, 'gateNext')
        self.assertEqual(self.bridge.position, 1)
        button = item(host, 'gateNext')
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        old = self.bridge.token
        QTest.mousePress(host, Qt.LeftButton, pos=point)
        self.bridge.replace_owner()
        pump()
        QTest.mouseRelease(host, Qt.LeftButton, pos=point)
        pump()
        self.assertEqual(self.bridge.position, 0)
        self.assertIn(('step', 1, old), self.bridge.calls)
        self.assertEqual(item(host, 'gateValue').property('text'), '0.000000')

    def test_owner_replacement_retires_focused_field_without_new_owner_commit(self):
        host = self.panel('AnalysisGate.qml')
        field = self.edit(host, 'gateIndex', '2')
        before = len(self.bridge.calls)
        old = self.bridge.token
        self.bridge.replace_owner()
        pump()
        self.assertEqual(self.bridge.position, 0)
        self.assertFalse(field.hasActiveFocus())
        self.assertEqual(field.property('text'), '1')
        self.assertFalse(any(call[0] == 'commit' and call[1] != old
                             for call in self.bridge.calls[before:]))

    def test_details_scroll_full_plain_text_and_explicit_close(self):
        self.bridge.data['details'] = DETAILS + '\n' + '\n'.join(
            'Detection provenance line ' + str(number) for number in range(80))
        host = self.panel('AnalysisHeader.qml')
        self.click(host, 'contextDetails')
        content = item(host, 'contextDetailsText')
        content.forceActiveFocus()
        QTest.keyClick(content.window(), Qt.Key_End, Qt.ControlModifier)
        pump()
        self.assertEqual(content.property('cursorPosition'), len(self.bridge.data['details']))
        self.assertGreater(content.property('contentHeight'), content.window().height())
        # The active text area follows its final line inside the scroll view.
        flickable = content.parentItem()
        while flickable is not None and flickable.metaObject().indexOfProperty('contentY') < 0:
            flickable = flickable.parentItem()
        self.assertIsNotNone(flickable)
        self.assertGreater(flickable.property('contentY'), 0)
        self.click(host, 'contextDetailsClose')
        self.assertFalse(host.rootObject().findChild(QObject, 'analysisContextPopup').property('visible'))

    def test_invalid_blur_restores_focus_and_stale_keyboard_release_cannot_step(self):
        host = self.panel('AnalysisGate.qml')
        field = self.edit(host, 'gateValue', '-')
        self.click(host, 'gateIndex')
        self.assertTrue(field.hasActiveFocus())
        self.assertEqual(field.property('text'), '-')
        self.assertEqual(self.bridge.position, 0)
        self.edit(host, 'gateValue', '0.1')
        QTest.keyClick(host, Qt.Key_Return)
        pump()
        button = item(host, 'gateNext')
        button.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyPress(host, Qt.Key_Space)
        self.bridge.replace_owner()
        pump()
        QTest.keyRelease(host, Qt.Key_Space)
        pump()
        self.assertEqual(self.bridge.position, 0)


if __name__ == '__main__':
    unittest.main()
