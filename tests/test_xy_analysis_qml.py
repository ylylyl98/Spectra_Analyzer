"""Real XY Quick consumers with typed boundary fixtures.

These prove QML input/lifecycle/geometry, not production scientific validation.
Production bridge and route assertions live in the adjacent XY test modules.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from pathlib import Path
import unittest

from PySide6.QtCore import QObject, Property, Signal, Slot, Qt, QPoint, QPointF, QUrl, qInstallMessageHandler
from PySide6.QtQml import QQmlExpression
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.quick_ui import QuickPanel
from tests.formal_quick_helpers import item, pump

APP = QApplication.instance() or QApplication([])
QML = Path(__file__).parents[1] / 'spectra_app' / 'qml'


class SettingsFixture(QObject):
    changed = Signal()
    revealRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.owner = 'source-1'
        self.calls, self.sessions = [], {}
        self.n = 0
        self._values = dict(mode='Peaks', detector='D2', model='Gaussian', preset='Balanced')
        self._drafts = {key: '0' for key in ('prominence', 'noise', 'distance', 'candidateWidthMin', 'candidateWidthMax', 'maxCount', 'sgWindow', 'sgOrder', 'widthMin', 'widthMax')}
        self._drafts.update(prominence='0.030', sgWindow='11', sgOrder='3', widthMin='0.000000001')
        self._errors = {}
        self._state = dict(context='<b>source</b>', status='Ready', error='', editable=True, busy=False,
                           widthLabel='Gaussian sigma (X units)',
                           findEnabled=True, fitEnabled=True, seedsEnabled=True, saveEnabled=True,
                           graphEnabled=True, resumeVisible=True, resumeEnabled=True,
                           startVisible=True, startEnabled=True)

    state = Property('QVariantMap', lambda self: deepcopy(self._state), notify=changed)
    values = Property('QVariantMap', lambda self: deepcopy(self._values), notify=changed)
    drafts = Property('QVariantMap', lambda self: deepcopy(self._drafts), notify=changed)
    errors = Property('QVariantMap', lambda self: deepcopy(self._errors), notify=changed)
    ownerToken = Property(str, lambda self: self.owner, notify=changed)
    canAccept = Property(bool, lambda self: True, notify=changed)

    @Slot(str, result=str)
    def beginEdit(self, key):
        self.n += 1
        token = f'{self.owner}:{self.n}'
        self.sessions[token] = self.owner
        self.calls.append(('begin', key, token))
        return token

    def valid(self, token):
        return self.sessions.get(token) == self.owner

    @Slot(str, str, str, result=bool)
    def setDraft(self, key, text, token):
        self.calls.append(('draft', key, text, token))
        if not self.valid(token):
            return False
        self._drafts[key] = text
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def commit(self, token):
        self.calls.append(('commit', token))
        if not self.valid(token):
            return False
        self._errors = {k: 'Enter a number.' for k, v in self._drafts.items() if v in ('-', 'bad')}
        self.changed.emit()
        if self._errors:
            self.revealRequested.emit(next(iter(self._errors)))
            return False
        return True

    @Slot(str, str, str, result=bool)
    def choose(self, key, value, token):
        self.calls.append(('choose', key, value, token))
        if not self.valid(token):
            return False
        self._values[key] = value
        self.changed.emit()
        return True

    @Slot(str)
    def endEdit(self, token):
        self.calls.append(('end', token))
        self.sessions.pop(token, None)

    @Slot(str, int, str, result=bool)
    def step(self, key, direction, token):
        self.calls.append(('step', key, direction, token))
        if not self.valid(token):
            return False
        self._drafts[key] = str(float(self._drafts[key]) + direction)
        self.changed.emit()
        return True

    @Slot(str, str, result=bool)
    def command(self, name, owner):
        self.calls.append(('command', name, owner))
        return owner == self.owner

    def rotate(self):
        self.owner = 'source-2'
        self.sessions.clear()
        self.changed.emit()


class SeedFixture(QObject):
    changed = Signal()
    revealRequested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._rows = [self.row('seed-a', '1'), self.row('seed-b', '2')]
        self._selected, self.calls, self._errors = [], [], {}
        self._error = ''

    @staticmethod
    def row(seed_id, x):
        return dict(seedId=seed_id, enabled=True, fixedCenter=False, position=x, kind='peak',
                    amplitude='', width='', gamma='', centerMin='', centerMax='')

    rows = Property('QVariantList', lambda self: deepcopy(self._rows), notify=changed)
    results = Property('QVariantList', lambda self: [dict(center='1.2', height='-3', width='0.1', gamma='0.2')], notify=changed)
    resultModel = Property(str, lambda self: 'Voigt', notify=changed)
    selectedIds = Property('QStringList', lambda self: self._selected[:], notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    errors = Property('QVariantMap', lambda self: self._errors.copy(), notify=changed)
    canAccept = Property(bool, lambda self: True, notify=changed)

    @Slot('QStringList')
    def setSelected(self, ids):
        self._selected = ids
        self.changed.emit()

    @Slot(str, str, 'QVariant', result=bool)
    def edit(self, seed_id, field, value):
        self.calls.append(('edit', seed_id, field, value))
        next(row for row in self._rows if row['seedId'] == seed_id)[field] = value
        self.changed.emit()
        return True

    @Slot()
    def add(self):
        self._rows.append(self.row('manual-3', '1.5'))
        self.changed.emit()

    @Slot()
    def excludeSelected(self):
        self.calls.append(('exclude', self._selected[:]))
        for row in self._rows:
            if row['seedId'] in self._selected:
                row['enabled'] = False
        self.changed.emit()

    @Slot()
    def useFilterSelected(self):
        self.calls.append(('filter', self._selected[:]))

    @Slot(result=bool)
    def accept(self):
        self.calls.append(('accept',))
        for row in self._rows:
            if row['centerMax'] == 'bad':
                self._error = 'Invalid upper bound'
                self._errors = {row['seedId'] + '.centerMax': self._error}
                self.changed.emit()
                self.revealRequested.emit(row['seedId'], 'centerMax')
                return False
        return True

    @Slot()
    def cancel(self):
        self.calls.append(('cancel',))


class DialogFixture(QObject):
    def __init__(self):
        super().__init__()
        self.accepted = self.rejected = 0
        self.view = None

    @Slot()
    def accept(self):
        self.accepted += 1
        self.view.hide()

    @Slot()
    def reject(self):
        self.rejected += 1
        self.view.hide()


class XYAnalysisQmlTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.previous_handler = qInstallMessageHandler(lambda kind, ctx, message: self.messages.append(message))
        self.hosts, self.refs = [], []

    def tearDown(self):
        for host in self.hosts:
            host.close()
            host.setSource(QUrl())
            host.deleteLater()
        pump()
        qInstallMessageHandler(self.previous_handler)
        qml_messages = [m for m in self.messages if not (m.startswith('QFontDatabase: Cannot find font directory') or m == 'This plugin does not support propagateSizeHints()')]
        self.assertEqual(qml_messages, [], '\n'.join(qml_messages))

    def panel(self, width=300):
        bridge = SettingsFixture()
        self.refs.append(bridge)
        host = QuickPanel('XYAnalysisPanel.qml', bridge, bridge_property='bridge')
        host.resize(width, host.height())
        host.show(); host.activateWindow(); host.setFocus(); pump()
        self.hosts.append(host)
        return host, bridge

    def dialog(self, name, bridge, size=(580, 700)):
        dialog = DialogFixture()
        self.refs.extend((bridge, dialog))
        host = QQuickView()
        dialog.view = host
        host.setResizeMode(QQuickView.SizeRootObjectToView)
        host.setInitialProperties(dict(bridge=bridge, dialogHost=dialog))
        host.setSource(QUrl.fromLocalFile(str(QML / name)))
        self.assertEqual(host.status(), QQuickView.Ready, [e.toString() for e in host.errors()])
        host.resize(*size); host.show(); host.requestActivate(); pump()
        self.hosts.append(host)
        return host, dialog

    def evaluate(self, host, source):
        expr = QQmlExpression(host.rootContext(), host.rootObject(), source)
        result = expr.evaluate()
        self.assertFalse(expr.hasError(), expr.error().toString())
        return result[0] if isinstance(result, tuple) else result

    def click(self, host, control):
        if isinstance(control, str):
            control = item(host, control)
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2))
        QTest.mouseClick(host, Qt.LeftButton, pos=point.toPoint()); pump()

    def type(self, host, control, text):
        control.forceActiveFocus(); pump()
        QTest.keyClick(host, Qt.Key_A, Qt.ControlModifier)
        for character in text:
            QTest.keyClick(host, character)
        pump()

    def test_panel_widths_literal_text_and_captured_command_owner(self):
        host, bridge = self.panel(210)
        for width in (210, 300, 420):
            host.resize(width, host.height()); pump()
            self.assertEqual(host.rootObject().width(), width)
            for key in ('mode', 'detector', 'model', 'find', 'settings', 'graph'):
                control = item(host, 'xy-analysis-' + key)
                self.assertGreater(control.width(), 0)
                self.assertLessEqual(control.x() + control.width(), width)
        context = item(host, 'xy-analysis-context')
        self.assertEqual(context.property('text'), '<b>source</b>')
        host.rootContext().setContextProperty('probe', context)
        self.assertEqual(self.evaluate(host, 'probe.textFormat'), 0)
        control = item(host, 'xy-analysis-find')
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        QTest.mousePress(host, Qt.LeftButton, pos=point)
        bridge.rotate()
        QTest.mouseRelease(host, Qt.LeftButton, pos=point); pump()
        self.assertIn(('command', 'find', 'source-1'), bridge.calls)

    def test_focused_draft_refresh_cursor_return_and_normal_blur(self):
        bridge = SettingsFixture()
        host, _ = self.dialog('XYAnalysisSettings.qml', bridge)
        field = item(host, 'xy-analysis-prominence')
        bridge.revealRequested.emit('prominence'); pump()
        self.type(host, field, '0.00450')
        token = next(call[2] for call in reversed(bridge.calls) if call[0] == 'begin')
        field.setProperty('cursorPosition', 3)
        bridge.changed.emit(); pump()
        self.assertEqual(field.property('text'), '0.00450')
        self.assertEqual(field.property('cursorPosition'), 3)
        self.assertEqual(item(host, 'xy-analysis-width-label').property('text'), 'Gaussian sigma (X units)')
        QTest.keyClick(host, Qt.Key_Return); pump()
        self.assertTrue(field.hasActiveFocus())
        self.assertIn(token, bridge.sessions)
        self.assertIn(('commit', token), bridge.calls)
        self.type(host, field, '-')
        item(host, 'xy-analysis-noise').forceActiveFocus(); pump()
        self.assertEqual(bridge._drafts['prominence'], '-')
        self.assertIn(('end', token), bridge.calls)

    def test_hide_and_owner_roundtrip_retire_without_committing(self):
        bridge = SettingsFixture()
        host, dialog = self.dialog('XYAnalysisSettings.qml', bridge)
        field = item(host, 'xy-analysis-prominence')
        bridge.revealRequested.emit('prominence'); pump()
        self.type(host, field, '-')
        before = sum(c[0] == 'commit' for c in bridge.calls)
        host.hide(); pump()
        self.assertEqual(sum(c[0] == 'commit' for c in bridge.calls), before)
        self.assertEqual(bridge._drafts['prominence'], '-')
        self.assertEqual(bridge.sessions, {})
        host.show(); host.requestActivate(); pump()
        self.assertEqual(field.property('text'), '-')
        self.type(host, field, '0.01')
        bridge.rotate(); pump()
        self.assertFalse(field.hasActiveFocus())
        self.assertEqual(dialog.accepted, 0)

    def test_choice_popup_is_window_complete_and_stale_session_retires(self):
        host, bridge = self.panel(210)
        choice = item(host, 'xy-analysis-model')
        self.click(host, choice)
        self.assertTrue(self.evaluate(host, 'editors.model.editorItem.popup.visible'))
        self.assertTrue(self.evaluate(host, 'editors.model.editorItem.popup.popupType === 1'))
        self.assertGreaterEqual(self.evaluate(host, 'editors.model.editorItem.popup.height'), 100)
        self.assertEqual(choice.property('count'), 3)
        bridge.rotate(); pump()
        self.assertFalse(self.evaluate(host, 'editors.model.editorItem.popup.visible'))
        self.assertEqual(bridge.sessions, {})

    def test_keyboard_choice_step_and_settings_close_reopen(self):
        bridge = SettingsFixture()
        host, dialog = self.dialog('XYAnalysisSettings.qml', bridge)
        field = item(host, 'xy-analysis-prominence')
        bridge.revealRequested.emit('prominence'); pump()
        self.type(host, field, '2.5')
        QTest.keyClick(host, Qt.Key_Up); pump()
        self.assertEqual(field.property('text'), '3.5')
        QTest.keyClick(host, Qt.Key_Down); pump()
        self.assertEqual(field.property('text'), '2.5')
        self.assertEqual([c[2] for c in bridge.calls if c[0] == 'step'], [1, -1])
        self.type(host, field, '-')
        commits = sum(c[0] == 'commit' for c in bridge.calls)
        self.click(host, 'xy-analysis-settings-close')
        self.assertFalse(host.isVisible())
        self.assertEqual(sum(c[0] == 'commit' for c in bridge.calls), commits)
        self.assertEqual(dialog.rejected, 1)
        host.show(); host.requestActivate(); pump()
        self.assertEqual(field.property('text'), '-')
        bridge._drafts['prominence'] = '0.03'
        bridge.changed.emit()
        bridge.revealRequested.emit('mode'); pump()
        choice = item(host, 'xy-analysis-mode')
        choice.forceActiveFocus(); pump()
        QTest.keyClick(host, Qt.Key_Down); pump()
        self.assertEqual(bridge._values['mode'], 'Dips')
        host, bridge = self.panel()
        choice = item(host, 'xy-analysis-mode')
        choice.forceActiveFocus(); pump()
        QTest.keyClick(host, Qt.Key_Space); pump()
        self.assertTrue(self.evaluate(host, 'editors.mode.editorItem.popup.visible'))
        QTest.keyClick(APP.focusWindow(), Qt.Key_Escape); pump()
        self.assertFalse(self.evaluate(host, 'editors.mode.editorItem.popup.visible'))
        choice.forceActiveFocus(); pump()
        QTest.keyClick(host, Qt.Key_Down); pump()
        self.assertEqual(bridge._values['mode'], 'Dips')

    def test_focused_choice_and_command_reject_late_keyboard_owner(self):
        host, bridge = self.panel()
        choice = item(host, 'xy-analysis-mode')
        choice.forceActiveFocus(); pump()
        bridge.rotate(); pump()
        QTest.keyClick(host, Qt.Key_Down)
        QTest.keyClick(host, Qt.Key_Space); pump()
        self.assertFalse(any(c[0] == 'choose' for c in bridge.calls))
        self.assertEqual(bridge._values['mode'], 'Peaks')
        self.click(host, choice)
        self.assertTrue(self.evaluate(host, 'editors.mode.editorItem.popup.visible'))
        bridge.rotate(); pump()
        action = item(host, 'xy-analysis-find')
        action.forceActiveFocus(); pump()
        bridge.owner = 'source-3'; bridge.changed.emit(); pump()
        QTest.keyClick(host, Qt.Key_Space); pump()
        self.assertIn(('command', 'find', 'source-2'), bridge.calls)
        self.click(host, action)
        self.assertEqual(bridge.calls[-1], ('command', 'find', 'source-3'))

    def test_seed_stable_identity_raw_text_selection_actions_accept_and_cancel(self):
        bridge = SeedFixture()
        host, dialog = self.dialog('XYAnalysisSeeds.qml', bridge, (760, 500))
        fields = self.evaluate(host, 'Object.keys(editors).length')
        self.assertEqual(fields, 14)
        field = self.evaluate(host, 'editors["seed-b.position"]')
        self.type(host, field, '2.4500')
        field.setProperty('cursorPosition', 2)
        bridge.changed.emit(); pump()
        self.assertTrue(field.hasActiveFocus())
        self.assertEqual(field.property('text'), '2.4500')
        self.assertEqual(field.property('cursorPosition'), 2)
        self.assertIn(('edit', 'seed-b', 'position', '2.4500'), bridge.calls)
        bridge.setSelected(['seed-b'])
        self.click(host, 'xy-seed-exclude')
        self.assertFalse(bridge._rows[1]['enabled'])
        self.click(host, 'xy-seed-reset')
        self.assertIn(('filter', ['seed-b']), bridge.calls)
        self.click(host, 'xy-seed-add')
        self.assertEqual(len(bridge.rows), 3)
        self.click(host, 'xy-seed-accept')
        self.assertEqual(dialog.accepted, 1)
        self.assertEqual(bridge.calls.count(('accept',)), 1)
        host.show(); host.requestActivate(); pump()
        self.click(host, 'xy-seed-cancel')
        self.assertIn(('cancel',), bridge.calls)
        self.assertEqual(dialog.rejected, 1)

    def test_seed_invalid_accept_reveals_offscreen_bound_and_results_are_readonly(self):
        bridge = SeedFixture()
        bridge._rows += [bridge.row('seed-' + str(n), str(n)) for n in range(3, 30)]
        bridge._rows[-1]['centerMax'] = 'bad'
        host, dialog = self.dialog('XYAnalysisSeeds.qml', bridge, (600, 420))
        self.click(host, 'xy-seed-accept')
        self.assertTrue(host.isVisible())
        self.assertEqual(dialog.accepted, 0)
        field = self.evaluate(host, 'editors["seed-29.centerMax"]')
        self.assertTrue(field.hasActiveFocus())
        point = field.mapToScene(QPointF(5, 5))
        self.assertGreaterEqual(point.x(), 0)
        self.assertLess(point.x(), host.width())
        self.assertGreaterEqual(point.y(), 0)
        self.assertLess(point.y(), host.height())
        self.click(host, 'xy-seed-results-tab')
        result = item(host, 'xy-seed-result')
        self.assertIn('Height: -3', result.property('text'))
        self.assertIn('Gamma: 0.2', result.property('text'))
        self.assertFalse(hasattr(result, 'setText'))


if __name__ == '__main__':
    unittest.main()
