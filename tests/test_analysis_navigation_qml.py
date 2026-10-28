"""Actual Quick input for the ordinary plot navigation presentation boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import unittest

from PySide6.QtCore import QObject, QPointF, Property, Qt, Signal, Slot, qInstallMessageHandler
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.quick_ui import QuickPanel
from tests.formal_quick_helpers import item, pump


APP = QApplication.instance() or QApplication([])
if not QFontDatabase.families():
    QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / 'arial.ttf'))


class NavigationFixture(QObject):
    stateChanged = Signal()
    messageChanged = Signal()

    def __init__(self):
        super().__init__()
        self.token = 'dataset:graph:generation1'
        self.live = True
        self.back = self.forward = False
        self.current_mode = self.coordinate = ''
        self.zoom = 'Fit'
        self.calls = []
        self.accepted = []
        self.rows = [dict(key=key, text=text, tooltip='Full ' + text, enabled=True)
                     for key, text in (
                         ('settings_copy', 'Copy settings'),
                         ('settings_paste', 'Paste settings'),
                         ('settings_copy_to', 'Copy to active graphs'),
                         ('comparison', 'Open comparison window'))]

    enabled = Property(bool, lambda self: self.live, notify=stateChanged)
    ownerToken = Property(str, lambda self: self.token, notify=stateChanged)
    backEnabled = Property(bool, lambda self: self.live and self.back, notify=stateChanged)
    forwardEnabled = Property(bool, lambda self: self.live and self.forward, notify=stateChanged)
    mode = Property(str, lambda self: self.current_mode, notify=stateChanged)
    message = Property(str, lambda self: self.coordinate, notify=messageChanged)
    zoomText = Property(str, lambda self: self.zoom, notify=stateChanged)
    actions = Property('QVariantList', lambda self: self.rows, notify=stateChanged)

    @Slot(str, str, result=bool)
    def invoke(self, command, owner):
        self.calls.append((command, owner))
        action = next((row for row in self.rows if row['key'] == command), None)
        if not self.live or owner != self.token or action and not action['enabled']:
            return False
        self.accepted.append((command, owner))
        if command in ('pan', 'zoom'):
            requested = 'pan/zoom' if command == 'pan' else 'zoom rect'
            self.current_mode = '' if self.current_mode == requested else requested
        self.stateChanged.emit()
        return True

    @Slot(str, str, result=bool)
    def setZoom(self, text, owner):
        self.calls.append(('display_zoom', text, owner))
        if not self.live or owner != self.token:
            return False
        self.accepted.append(('display_zoom', text, owner))
        self.zoom = text
        self.stateChanged.emit()
        return True

    def replace_owner(self):
        self.token += ':replacement'
        self.stateChanged.emit()
        pump()


class AnalysisNavigationQmlTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.old_handler = qInstallMessageHandler(lambda kind, context, text: self.messages.append(text))
        self.bridge = NavigationFixture()
        self.host = QuickPanel('AnalysisNavigation.qml', self.bridge, bridge_property='bridge')
        self.host.setFont(QFont('Arial', 10))
        self.host.resize(480, 40)
        self.host.show()
        self.host.activateWindow()
        pump(60)
        self.assertEqual(self.host.status(), QQuickWidget.Ready)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        pump()
        qInstallMessageHandler(self.old_handler)
        self.assertEqual([text for text in self.messages if '.qml:' in text], [])

    def point(self, name):
        try:
            control = item(self.host, name)
        except AssertionError:
            # Combo delegates belong to the actual detached popup window.
            def find(root):
                if root.objectName() == name:
                    return root
                for child in root.childItems():
                    found = find(child)
                    if found is not None:
                        return found
            control = next((found for window in APP.allWindows()
                            if hasattr(window, 'contentItem')
                            and (found := find(window.contentItem())) is not None), None)
            self.assertIsNotNone(control, name)
        return control, control.mapToScene(QPointF(control.width() / 2, control.height() / 2)).toPoint()

    def click(self, name):
        control, point = self.point(name)
        receiver = self.host if control.window() is self.host.quickWindow() else control.window()
        QTest.mouseClick(receiver, Qt.LeftButton, pos=point)
        pump()
        return control

    def expression(self, control, expression):
        return QQmlExpression(QQmlEngine.contextForObject(control), control, expression).evaluate()[0]

    def menu(self):
        self.click('analysisNavMore')
        popup = self.host.rootObject().findChild(QObject, 'analysisNavMenu')
        self.assertTrue(popup.property('visible'))
        return popup

    def test_pointer_commands_and_authoritative_mode_history_and_enabled_projection(self):
        self.assertFalse(item(self.host, 'analysisNavBack').isEnabled())
        self.assertFalse(item(self.host, 'analysisNavForward').isEnabled())
        self.click('analysisNavHome')
        self.click('analysisNavPan')
        self.assertEqual(self.bridge.current_mode, 'pan/zoom')
        self.assertTrue(item(self.host, 'analysisNavPan').property('checked'))
        self.click('analysisNavZoom')
        self.assertFalse(item(self.host, 'analysisNavPan').property('checked'))
        self.assertTrue(item(self.host, 'analysisNavZoom').property('checked'))
        self.bridge.current_mode = ''
        self.bridge.back = self.bridge.forward = True
        self.bridge.stateChanged.emit()
        pump()
        self.assertFalse(item(self.host, 'analysisNavZoom').property('checked'))
        self.click('analysisNavBack')
        self.click('analysisNavForward')
        self.assertEqual([call[0] for call in self.bridge.accepted], ['home', 'pan', 'zoom', 'back', 'forward'])
        self.bridge.live = False
        self.bridge.stateChanged.emit()
        pump()
        for name in ('Home', 'Back', 'Forward', 'Pan', 'Zoom', 'DisplayZoom', 'More'):
            self.assertFalse(item(self.host, 'analysisNav' + name).isEnabled())

    def test_stale_pointer_press_and_keyboard_focus_cannot_target_replacement(self):
        button, point = self.point('analysisNavPan')
        old = self.bridge.token
        QTest.mousePress(self.host, Qt.LeftButton, pos=point)
        self.bridge.replace_owner()
        QTest.mouseRelease(self.host, Qt.LeftButton, pos=point)
        pump()
        self.assertIn(('pan', old), self.bridge.calls)
        self.assertEqual(self.bridge.accepted, [])
        other = item(self.host, 'analysisNavHome')
        other.forceActiveFocus(Qt.TabFocusReason)
        pump()
        old = self.bridge.token
        self.bridge.replace_owner()
        QTest.keyClick(self.host, Qt.Key_Space)
        pump()
        self.assertIn(('home', old), self.bridge.calls)
        self.assertEqual(self.bridge.accepted, [])
        # A fresh pointer gesture deliberately selects the new owner.
        self.click('analysisNavHome')
        self.assertEqual(self.bridge.accepted, [('home', self.bridge.token)])

    def test_keyboard_commands_use_focus_capture(self):
        button = item(self.host, 'analysisNavPan')
        button.forceActiveFocus(Qt.TabFocusReason)
        pump()
        QTest.keyClick(self.host, Qt.Key_Space)
        pump()
        self.assertEqual(self.bridge.accepted, [('pan', self.bridge.token)])
        self.assertTrue(button.property('checked'))

    def test_zoom_pointer_popup_all_choices_and_model_projection(self):
        combo = item(self.host, 'analysisNavDisplayZoom')
        for index, text in enumerate(('Fit', '100%', '150%', '200%')):
            self.click('analysisNavDisplayZoom')
            option, _ = self.point('analysisNavDisplayZoomOption' + str(index))
            self.assertIsNot(option.window(), self.host.quickWindow())
            self.click('analysisNavDisplayZoomOption' + str(index))
            self.assertEqual(self.bridge.zoom, text)
            self.assertEqual(combo.property('currentIndex'), index)
        self.bridge.zoom = '100%'
        self.bridge.stateChanged.emit()
        pump()
        self.assertEqual(combo.property('currentIndex'), 1)

    def test_stale_keyboard_zoom_never_recaptures_at_activation(self):
        combo = item(self.host, 'analysisNavDisplayZoom')
        combo.forceActiveFocus(Qt.TabFocusReason)
        pump()
        old = self.bridge.token
        self.bridge.replace_owner()
        QTest.keyClick(self.host, Qt.Key_Down)
        pump()
        self.assertIn(('display_zoom', '100%', old), self.bridge.calls)
        self.assertEqual(self.bridge.accepted, [])
        self.assertEqual(combo.property('currentIndex'), 0)
        # Refocusing for a new session permits ordinary keyboard choice.
        item(self.host, 'analysisNavHome').forceActiveFocus()
        combo.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(self.host, Qt.Key_Down)
        pump()
        self.assertEqual(self.bridge.zoom, '100%')

    def test_menu_qaction_projection_native_popup_and_owner_session(self):
        self.bridge.rows[1]['enabled'] = False
        self.bridge.rows[0]['text'] = 'Copy <b>literal</b>'
        self.bridge.stateChanged.emit()
        popup = self.menu()
        copy = item(self.host, 'analysisNavSettingsCopy')
        self.assertIsNot(copy.window(), self.host.quickWindow())
        self.assertFalse(item(self.host, 'analysisNavSettingsPaste').isEnabled())
        self.assertEqual(copy.property('text'), 'Copy <b>literal</b>')
        self.assertTrue(self.expression(copy, 'contentItem.textFormat === Text.PlainText'))
        self.click('analysisNavSettingsCopy')
        self.assertEqual(self.bridge.accepted[-1], ('settings_copy', self.bridge.token))
        for name, key in (('SettingsCopyTo', 'settings_copy_to'), ('Comparison', 'comparison'),
                          ('Subplots', 'subplots'), ('Customize', 'customize')):
            self.menu()
            self.click('analysisNav' + name)
            self.assertEqual(self.bridge.accepted[-1], (key, self.bridge.token))
        self.menu()
        old = self.bridge.token
        control, point = self.point('analysisNavSettingsCopy')
        popup_window = control.window()
        QTest.mousePress(popup_window, Qt.LeftButton, pos=point)
        before = len(self.bridge.accepted)
        self.bridge.replace_owner()
        QTest.mouseRelease(popup_window, Qt.LeftButton, pos=point)
        pump()
        self.assertFalse(popup.property('visible'))
        self.assertEqual(len(self.bridge.accepted), before)
        self.assertEqual(popup.property('actionOwner'), old)

    def test_keyboard_menu_session_dispatch_and_stale_opener_rejection(self):
        opener = item(self.host, 'analysisNavMore')
        opener.forceActiveFocus(Qt.TabFocusReason)
        pump()
        QTest.keyClick(self.host, Qt.Key_Space)
        pump()
        popup = self.host.rootObject().findChild(QObject, 'analysisNavMenu')
        self.assertTrue(popup.property('visible'))
        entry = item(self.host, 'analysisNavSettingsCopy')
        for _ in range(6):
            QTest.keyClick(entry.window(), Qt.Key_Down)
        pump()
        QTest.keyClick(entry.window(), Qt.Key_Return)
        pump()
        self.assertEqual(self.bridge.accepted, [('customize', self.bridge.token)])
        self.assertFalse(popup.property('visible'))
        # Preserve keyboard focus ownership even when a dataset changes before
        # a subsequent activation; a new pointer press is a new session.
        item(self.host, 'analysisNavHome').forceActiveFocus()
        opener.forceActiveFocus(Qt.TabFocusReason)
        pump()
        before = len(self.bridge.accepted)
        self.bridge.replace_owner()
        QTest.keyClick(self.host, Qt.Key_Space)
        pump()
        self.assertFalse(popup.property('visible'))
        self.assertEqual(len(self.bridge.accepted), before)
        self.menu()
        self.assertEqual(popup.property('actionOwner'), self.bridge.token)

    def test_owner_replacement_closes_actual_zoom_popup_without_a_selection(self):
        self.click('analysisNavDisplayZoom')
        option, _ = self.point('analysisNavDisplayZoomOption3')
        popup_window = option.window()
        self.assertTrue(popup_window.isVisible())
        before = len(self.bridge.accepted)
        self.bridge.replace_owner()
        self.assertFalse(popup_window.isVisible())
        self.assertEqual(self.bridge.zoom, 'Fit')
        self.assertEqual(len(self.bridge.accepted), before)

    def test_coordinate_message_fixed_geometry_plain_text_and_full_tooltip(self):
        for width in (400, 480, 610):
            self.host.resize(width, 40)
            pump()
            names = ('Home', 'Back', 'Forward', 'Pan', 'Zoom', 'DisplayZoom', 'Message', 'More')
            controls = [item(self.host, 'analysisNav' + name) for name in names]
            original = [(control.x(), control.y(), control.width(), control.height()) for control in controls]
            self.bridge.coordinate = '<b>x=0.123456</b>, y=8.123456 ' * 18
            self.bridge.messageChanged.emit()
            pump()
            self.assertEqual(self.host.height(), 40)
            self.assertEqual(original, [(control.x(), control.y(), control.width(), control.height()) for control in controls])
            for control in controls:
                self.assertGreaterEqual(control.x(), 0)
                self.assertLessEqual(control.x() + control.width(), width)
                self.assertLessEqual(control.y() + control.height(), 40)
            for left, right in zip(controls, controls[1:]):
                self.assertLessEqual(left.x() + left.width(), right.x())
            message = item(self.host, 'analysisNavMessage')
            self.assertTrue(self.expression(message, 'textFormat === Text.PlainText && elide === Text.ElideRight'))
            self.assertGreater(message.property('implicitWidth'), message.width())
            tooltip = self.host.rootObject().findChild(QObject, 'analysisNavMessageTooltip')
            self.assertEqual(tooltip.property('text'), self.bridge.coordinate)


if __name__ == '__main__':
    unittest.main()
