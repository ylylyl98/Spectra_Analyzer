"""Real Quick input for the named DAT panel and its four Quick editors.

The small bridge fixture proves rendering, focus, owner capture and text input;
production science/ownership is covered by test_analysis_workbench.py.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
import math
import unittest

from PySide6.QtCore import QObject, Property, Signal, Slot, Qt, qInstallMessageHandler
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.quick_ui import QuickPanel
from spectra_app.quick_dialog import QuickDialog
from tests.formal_quick_helpers import item, click_item, pump

APP = QApplication.instance() or QApplication([])


class AnalysisFixture(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)
    revealRequested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.token = 'workspace:dataset:graph:input'
        self.calls = []
        self.error_field = self.error_text = ''
        self.data = dict(owner=self.token, gate=0, gateLabel='Gate 0 V', scope='current',
                         mode='Both', detector='Original', xmin='1', xmax='9', d2Window='11',
                         model='Voigt', inputMode='raw', busy=False, d2Note='',
                         status='Ready', counts='2 candidates · 2 accepted · 0 excluded',
                         filterPreset='Balanced', filterState='Pending criteria · Current Gate',
                         filterCanApply=True, filter={key: '0' for key in
                         ('min_prominence_fraction', 'min_noise_strength', 'min_width',
                          'max_width', 'min_distance', 'max_count')},
                         selectedSeedId='seed-a', detectedVisible=True, fittedVisible=True,
                         canBind=True, bindText='Use input', bindTooltip='Capture input',
                         canLinecut=True, addPosition='5', addKind='peak', rebindCount=3,
                         disclosures={}, widthLabel='Sigma', seeds=[
                             dict(seedId=identity, enabled=True, position=position, kind='peak',
                                  amplitude='', width='', gamma='0.5', fixedCenter=False,
                                  bound='', gammaEnabled=True)
                             for identity, position in [('seed-a', '3'), ('seed-b', '6')]])

    state = Property('QVariantMap', lambda self: deepcopy(self.data), notify=stateChanged)
    ownerToken = Property(str, lambda self: self.token, notify=stateChanged)
    enabled = Property(bool, lambda self: True, notify=stateChanged)
    canAccept = Property(bool, lambda self: True, notify=stateChanged)
    errorField = Property(str, lambda self: self.error_field, notify=draftChanged)
    errorText = Property(str, lambda self: self.error_text, notify=draftChanged)

    def publish(self):
        self.stateChanged.emit()
        self.draftChanged.emit()

    def valid(self, owner):
        return owner == self.token

    @Slot(str, str, str, result=bool)
    def draftNumber(self, field, text, owner):
        self.calls.append(('draftNumber', field, text, owner))
        if not self.valid(owner):
            return False
        self.data[field] = text
        self.publish()
        return True

    @Slot(str, str, str, result=bool)
    def draftFilter(self, field, text, owner):
        self.calls.append(('draftFilter', field, text, owner))
        if not self.valid(owner):
            return False
        self.data['filter'][field] = text
        self.publish()
        return True

    @Slot(str, int, str, str, str, result=bool)
    def draftSeed(self, identity, gate, field, text, owner):
        self.calls.append(('draftSeed', identity, gate, field, text, owner))
        if not self.valid(owner) or gate != self.data['gate']:
            return False
        for row in self.data['seeds']:
            if row['seedId'] == identity:
                row[field] = text == 'true' if field in ('enabled', 'fixedCenter') else text
                self.publish()
                return True
        return False

    @Slot(str, result=bool)
    def commitEditors(self, owner):
        self.calls.append(('commitEditors', owner))
        if not self.valid(owner):
            return False
        values = [(key, self.data[key], 'detection') for key in ('xmin', 'xmax', 'd2Window')]
        values += [(key, value, 'filter') for key, value in self.data['filter'].items()]
        for row in self.data['seeds']:
            values += [(row['seedId'] + ':' + key, row[key], 'seeds') for key in
                       ('position', 'amplitude', 'width', 'gamma', 'bound') if row[key] != '']
        for field, text, section in values:
            try:
                if not math.isfinite(float(text)):
                    raise ValueError
            except ValueError:
                self.error_field, self.error_text = field, 'Enter a finite number.'
                self.publish()
                self.focusRequested.emit(section, field)
                return False
        self.error_field = self.error_text = ''
        self.publish()
        return True

    @Slot(str, str, str, result=bool)
    def setChoice(self, field, value, owner):
        self.calls.append(('setChoice', field, value, owner))
        if not self.valid(owner):
            return False
        self.data[field] = value
        self.publish()
        return True

    @Slot(str, bool, str, result=bool)
    def setDisclosure(self, section, expanded, owner):
        if not self.valid(owner):
            return False
        self.data['disclosures'][section] = expanded
        self.publish()
        return True

    @Slot(str, int, str, result=bool)
    def selectSeed(self, identity, gate, owner):
        self.calls.append(('selectSeed', identity, gate, owner))
        if not self.valid(owner) or gate != self.data['gate']:
            return False
        self.data['selectedSeedId'] = identity
        self.publish()
        return True

    @Slot(str, bool, str, result=bool)
    def setOverlay(self, layer, visible, owner):
        self.calls.append(('setOverlay', layer, visible, owner))
        return self.valid(owner)

    @Slot(str, str, result=bool)
    def command(self, action, owner):
        self.calls.append(('command', action, owner))
        return self.valid(owner)

    @Slot(str, result=bool)
    def acceptAdd(self, owner):
        self.calls.append(('acceptAdd', owner))
        return self.valid(owner)

    @Slot(str, str, result=bool)
    def chooseRebind(self, choice, owner):
        self.calls.append(('chooseRebind', choice, owner))
        return self.valid(owner)


class AnalysisWorkbenchQmlTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.old_handler = qInstallMessageHandler(lambda kind, context, text: self.messages.append(text))
        self.bridge = AnalysisFixture()
        self.hosts = []

    def tearDown(self):
        for host in self.hosts:
            host.close()
            host.deleteLater()
        pump()
        qInstallMessageHandler(self.old_handler)
        self.assertEqual([message for message in self.messages if '.qml:' in message], [])

    def panel(self, width=320):
        host = QuickPanel('AnalysisWorkbench.qml', self.bridge, bridge_property='bridge')
        host.resize(width, host.height())
        self.hosts.append(host)
        host.show()
        host.activateWindow()
        pump(60)
        return host

    def dialog(self, filename, size=(740, 710)):
        host = QuickDialog(self.bridge, filename, title='Analysis UI test', size=size)
        self.hosts.append(host)
        host.show()
        pump(60)
        return host

    def type(self, host, name, text, commit=False):
        control = item(host, name)
        control.forceActiveFocus(Qt.OtherFocusReason)
        pump()
        target = host if isinstance(host, QQuickWidget) else host.window()
        QTest.keyClick(target, Qt.Key_A, Qt.ControlModifier)
        if text:
            if isinstance(host, QQuickWidget):
                QTest.keyClicks(target, text)
            else:
                for character in text:
                    QTest.keyClick(target, Qt.Key(ord(character)))
        else:
            QTest.keyClick(target, Qt.Key_Backspace)
        if commit:
            QTest.keyClick(target, Qt.Key_Return)
        pump()
        return control

    def test_narrow_panel_real_text_error_reveal_and_owner_retirement(self):
        host = self.panel(300)
        self.assertEqual(host.status(), QQuickWidget.Ready)
        control = self.type(host, 'xmin', '-1.23456')
        self.assertEqual(control.property('text'), '-1.23456')
        self.assertTrue(control.hasActiveFocus())
        self.assertEqual(control.property('cursorPosition'), 8)
        self.assertLessEqual(control.width(), 140)
        self.assertGreaterEqual(item(host, 'model').width(), 110)
        self.assertGreaterEqual(item(host, 'filterPreset').width(), 110)
        self.type(host, 'xmin', '-', commit=True)
        self.assertEqual(self.bridge.error_field, 'xmin')
        self.assertEqual(control.property('text'), '-')
        self.assertTrue(control.hasActiveFocus())
        previous = self.bridge.token
        self.bridge.token = 'workspace:dataset:other:input'
        self.bridge.data.update(xmin='10', owner=self.bridge.token)
        self.bridge.publish()
        pump()
        self.assertFalse(control.hasActiveFocus())
        self.assertEqual(control.property('text'), '10')
        self.assertNotIn(('commitEditors', self.bridge.token), self.bridge.calls)
        self.assertIn(('draftNumber', 'xmin', '-', previous), self.bridge.calls)

    def test_popup_keyboard_capture_closes_on_owner_change_and_seed_selection_is_id(self):
        host = self.panel()
        choice = item(host, 'scope')
        choice.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(host, Qt.Key_Down)
        pump()
        self.assertIn(('setChoice', 'scope', 'all', self.bridge.token), self.bridge.calls)
        QTest.keyClick(host, Qt.Key_Space)
        pump()
        popup = next(child for child in choice.findChildren(QObject) if child.inherits('QQuickPopup'))
        self.assertTrue(popup.property('visible'))
        self.bridge.token = 'retired-owner'
        self.bridge.publish()
        pump()
        self.assertFalse(popup.property('visible'))
        self.bridge.data['seeds'].reverse()
        self.bridge.publish()
        pump()
        click_item(host, 'seed_seed-b')
        self.assertEqual(self.bridge.data['selectedSeedId'], 'seed-b')
        self.assertEqual(self.bridge.calls[-1], ('selectSeed', 'seed-b', 0, self.bridge.token))

    def test_disclosure_preserves_invalid_bytes_and_keyboard_focus(self):
        host = self.panel()
        self.type(host, 'xmin', '-')
        click_item(host, 'analysis_detection_toggle')
        self.assertFalse(item(host, 'xmin').isVisible())
        self.assertEqual(self.bridge.data['xmin'], '-')
        self.assertFalse(any(call[0] == 'commitEditors' for call in self.bridge.calls))
        header = item(host, 'analysis_detection_toggle')
        self.assertTrue(header.hasActiveFocus())
        QTest.keyClick(host, Qt.Key_Right)
        pump()
        self.assertTrue(item(host, 'xmin').isVisible())
        self.assertEqual(item(host, 'xmin').property('text'), '-')

    def test_choice_popup_has_its_own_window_beyond_short_host(self):
        host = QuickPanel('AnalysisWorkbench.qml', self.bridge, expanding=True,
                          bridge_property='bridge')
        self.hosts.append(host)
        host.resize(300, 120)
        host.show()
        host.activateWindow()
        pump()
        click_item(host, 'scope')
        combo = item(host, 'scope')
        popup = next(child for child in combo.findChildren(QObject) if child.inherits('QQuickPopup'))
        view = popup.property('contentItem')
        rows = [child for child in view.childItems()[0].childItems() if child.inherits('QQuickItemDelegate')]
        self.assertEqual(len(rows), 2)
        window = rows[0].window()
        self.assertIsNot(window, host.quickWindow())
        for row in rows:
            self.assertLessEqual(row.y() + row.height(), window.height() + 1)

    def test_seed_detail_survives_multi_character_and_blank_typing_and_gamma_disabled(self):
        host = self.dialog('AnalysisSeedEditor.qml')
        editor = item(host, 'seed-a:width')
        control = self.type(host, 'seed-a:width', '1.234567')
        self.assertIs(control, editor)
        self.assertIs(item(host, 'seed-a:width'), editor)
        self.assertTrue(editor.hasActiveFocus())
        self.assertEqual(editor.property('text'), '1.234567')
        self.assertEqual(editor.property('cursorPosition'), 8)
        self.type(host, 'seed-a:width', '')
        self.assertEqual(self.bridge.data['seeds'][0]['width'], '')
        self.assertTrue(editor.hasActiveFocus())
        self.bridge.data['seeds'][0]['gammaEnabled'] = False
        self.bridge.publish()
        pump()
        self.assertFalse(item(host, 'seed-a:gamma').isEnabled())
        self.assertEqual(item(host, 'seed-a:gamma').property('text'), '0.5')
        self.type(host, 'seed-a:position', '-', commit=True)
        self.assertEqual(self.bridge.error_field, 'seed-a:position')
        self.assertTrue(item(host, 'seed-a:position').hasActiveFocus())

    def test_filter_uses_pending_text_and_all_dialogs_load(self):
        host = self.dialog('AnalysisFilterEditor.qml', (390, 640))
        editor = self.type(host, 'min_width', '2.71828')
        self.assertEqual(editor.property('text'), '2.71828')
        self.assertTrue(editor.hasActiveFocus())
        self.assertEqual(self.bridge.data['filter']['min_width'], '2.71828')
        self.assertFalse(any(call[:2] == ('command', 'filter') for call in self.bridge.calls))
        host.close()
        self.bridge = AnalysisFixture()
        add = self.dialog('AnalysisAddSeed.qml', (390, 380))
        self.type(add, 'addPosition', '4.25')
        self.assertEqual(self.bridge.data['addPosition'], '4.25')
        add.close()
        self.bridge = AnalysisFixture()
        rebind = self.dialog('AnalysisRebind.qml', (390, 430))
        self.assertTrue(item(rebind, 'analysis_rebind_cancel').hasActiveFocus())
        QTest.keyClick(rebind.window(), Qt.Key_Return)
        pump()
        self.assertEqual(rebind.result(), QuickDialog.Rejected)
        self.assertFalse(any(call[0] == 'chooseRebind' for call in self.bridge.calls))

    def test_busy_controls_and_keyboard_command_capture(self):
        host = self.panel()
        find = item(host, 'analysis_find')
        find.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(host, Qt.Key_Space)
        pump()
        self.assertIn(('command', 'find', self.bridge.token), self.bridge.calls)
        self.bridge.data['busy'] = True
        self.bridge.publish()
        pump()
        self.assertFalse(find.isEnabled())
        self.assertFalse(item(host, 'model').isEnabled())
        self.assertTrue(item(host, 'analysis_stop').isVisible())
        self.assertTrue(item(host, 'analysis_stop').isEnabled())


if __name__ == '__main__':
    unittest.main()
