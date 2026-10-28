"""Actual Quick consumer input for ordinary DAT analysis.

The QObject fixture implements the typed boundary; scientific parsing, ownership
and production integration are exercised separately against the real bridge.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from dataclasses import asdict
import unittest

from PySide6.QtCore import QObject, Property, Signal, Slot, Qt, QPoint, QPointF, qInstallMessageHandler
from PySide6.QtGui import QFont, QInputMethodEvent, QWheelEvent
from PySide6.QtQml import QQmlExpression
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.dataset_state import AnalysisControls
from spectra_app.ordinary_analysis_bridge import FIELD_METADATA
from spectra_app.quick_ui import QuickPanel
from tests.formal_quick_helpers import item, click_item, pump

APP = QApplication.instance() or QApplication([])


class OrdinaryFixture(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.token = 'workspace:dataset:graph:cube:1'
        self.tab = 0
        self.calls = []
        self.error_field = self.error_text = ''
        self.data = dict(values=asdict(AnalysisControls()), texts={}, ui={}, summary='<b>plain summary</b>',
                         csvPath='', peakNumber=1, peakCount=3, lassoActive=False, circleActive=False)
        self.data['texts'] = {entry['name']: str(self.data['values'][entry['name']])
                              for entry in FIELD_METADATA if entry['kind'] == 'number'}
        self.data['ui'] = {command + 'Enabled': True for command in (
            'find', 'batchFind', 'fit', 'batchFit', 'fullRange', 'currentRange', 'loadCsv',
            'clearFit', 'exportCurrentFit', 'exportAllFit', 'exportSpectrum', 'savePeaks',
            'lasso', 'circle', 'deleteLasso', 'clearLasso', 'clearBatch', 'deletePeak',
            'clearPeaks', 'createAnalysis')}
        self.data['ui'].update(axisText='Energy (eV)', rangeText='0–1 eV', readinessText='3 seeds ready',
                               widthHelp='Lorentzian HWHM in points, converted to eV.',
                               minWidthLabel='Minimum HWHM (pt)', maxWidthLabel='Maximum HWHM (pt)',
                               minWidthText='≈ 0.012 eV', maxWidthText='Automatic upper bound', moveText='Unconstrained')

    state = Property('QVariantMap', lambda self: deepcopy(self.data), notify=stateChanged)
    fields = Property('QVariantList', lambda self: list(FIELD_METADATA), constant=True)
    ownerToken = Property(str, lambda self: self.token, notify=stateChanged)
    selectedTab = Property(int, lambda self: self.tab, notify=stateChanged)
    enabled = Property(bool, lambda self: True, notify=stateChanged)
    errorText = Property(str, lambda self: self.error_text, notify=draftChanged)
    errorField = Property(str, lambda self: self.error_field, notify=draftChanged)

    def publish(self):
        self.stateChanged.emit()
        self.draftChanged.emit()

    @Slot(str, str, str, result=bool)
    def setDraft(self, field, text, owner):
        self.calls.append(('draft', field, text, owner))
        if owner != self.token:
            return False
        self.data['texts'][field] = text
        self.publish()
        return True

    @Slot(str, result=bool)
    def commit(self, owner):
        self.calls.append(('commit', owner))
        if owner != self.token:
            return False
        for field, text in self.data['texts'].items():
            try:
                float(text)
            except ValueError:
                self.error_field, self.error_text = field, 'Enter a finite number.'
                self.publish()
                self.focusRequested.emit(field, owner)
                return False
        self.error_field = self.error_text = ''
        self.publish()
        return True

    @Slot(str, 'QVariant', str, result=bool)
    def setValue(self, field, value, owner):
        self.calls.append(('value', field, value, owner))
        if owner != self.token:
            return False
        self.data['values'][field] = value
        self.publish()
        return True

    @Slot(str, int, str, result=bool)
    def step(self, field, direction, owner):
        self.calls.append(('step', field, direction, owner))
        if owner != self.token:
            return False
        metadata = next(entry for entry in FIELD_METADATA if entry['name'] == field)
        self.data['texts'][field] = str(float(self.data['texts'][field]) + metadata['step'] * direction)
        self.publish()
        return True

    @Slot(str, str, result=bool)
    def trigger(self, command, owner):
        self.calls.append(('trigger', command, owner))
        if owner != self.token:
            return False
        return self.commit(owner)

    @Slot(int)
    def selectTab(self, tab):
        self.tab = tab
        self.publish()

    @Slot(int, str, result=bool)
    def setPeakNumber(self, number, owner):
        self.calls.append(('peak', number, owner))
        if owner != self.token:
            return False
        self.data['peakNumber'] = number
        self.publish()
        return True


class OrdinaryAnalysisQmlTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.handler = qInstallMessageHandler(lambda kind, context, message: self.messages.append(message))
        self.bridge = OrdinaryFixture()
        self.host = QuickPanel('OrdinaryAnalysis.qml', self.bridge, bridge_property='bridge')
        self.host.setFont(QFont('Arial', 10))
        self.host.resize(300, self.host.height())
        self.host.show()
        self.host.activateWindow()
        pump(60)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        pump()
        qInstallMessageHandler(self.handler)
        self.assertEqual([message for message in self.messages if '.qml:' in message], [])

    def type(self, field, text, accept=False):
        self.bridge.focusRequested.emit(field, self.bridge.token)
        pump(60)
        editor = item(self.host, 'ordinaryField_' + field)
        QTest.keyClick(self.host, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(self.host, text)
        if accept:
            QTest.keyClick(self.host, Qt.Key_Return)
        pump()
        return editor

    def test_component_all_fields_actions_and_compact_geometry(self):
        self.assertEqual(self.host.status(), QQuickWidget.Ready)
        for entry in FIELD_METADATA:
            self.assertIsNotNone(item(self.host, 'ordinaryField_' + entry['name']))
        for command in [key[:-7] for key in self.bridge.data['ui'] if key.endswith('Enabled')]:
            self.assertIsNotNone(item(self.host, 'ordinaryCommand_' + command))
        for width in (300, 390):
            self.host.resize(width, self.host.height())
            self.bridge.selectTab(1)
            pump(60)
            for field in ('fit_xmin', 'fit_xmax', 'linewidth_min', 'linewidth_max', 'peak_move_limit'):
                editor = item(self.host, 'ordinaryField_' + field)
                self.assertGreater(editor.width(), 90)
                self.assertLessEqual(editor.mapToScene(editor.boundingRect().bottomRight()).x(), width)
            self.assertEqual(item(self.host, 'ordinaryField_linewidth_min').property('font').family(), 'Arial')
            self.assertEqual(item(self.host, 'ordinaryField_linewidth_min').property('font').pointSize(), 10)

    def test_multichar_raw_draft_cursor_refresh_and_enter_blur_once(self):
        editor = self.type('fit_xmin', '-1.23456')
        self.assertEqual(editor.property('text'), '-1.23456')
        self.assertEqual(editor.property('cursorPosition'), 8)
        QTest.keyClick(self.host, Qt.Key_Left)
        QTest.keyClick(self.host, Qt.Key_Left)
        position = editor.property('cursorPosition')
        self.bridge.data['ui']['rangeText'] = 'Refreshed range'
        self.bridge.publish()
        pump()
        self.assertEqual(editor.property('cursorPosition'), position)
        self.assertTrue(editor.hasActiveFocus())
        before = len([call for call in self.bridge.calls if call[0] == 'commit'])
        QTest.keyClick(self.host, Qt.Key_Return)
        item(self.host, 'ordinaryField_fit_xmax').forceActiveFocus(Qt.OtherFocusReason)
        pump()
        self.assertEqual(len([call for call in self.bridge.calls if call[0] == 'commit']) - before, 1)

    def test_invalid_draft_reveals_collapsed_other_tab_and_preserves_locale_text(self):
        self.type('fit_xmin', '-', accept=True)
        self.assertEqual(self.bridge.error_field, 'fit_xmin')
        self.assertEqual(len([call for call in self.bridge.calls if call[0] == 'commit']), 1)
        editor = item(self.host, 'ordinaryField_fit_xmin')
        self.assertEqual(editor.property('text'), '-')
        self.assertTrue(editor.hasActiveFocus())
        click_item(self.host, 'ordinaryGroupToggle_range')
        self.bridge.selectTab(0)
        self.bridge.focusRequested.emit('fit_xmin', self.bridge.token)
        pump(70)
        self.assertEqual(self.bridge.tab, 1)
        self.assertTrue(editor.isVisible())
        self.assertTrue(editor.hasActiveFocus())
        QTest.keyClick(self.host, Qt.Key_A, Qt.ControlModifier)
        event = QInputMethodEvent()
        event.setCommitString('−1,25')
        APP.sendEvent(self.host, event)
        pump()
        self.assertEqual(editor.property('text'), '−1,25')
        self.assertEqual(self.bridge.data['texts']['fit_xmin'], '−1,25')

    def test_dependent_controls_and_focus_reveal_do_not_change_scientific_values(self):
        self.assertFalse(item(self.host, 'ordinaryField_d2_window').isVisible())
        click_item(self.host, 'ordinaryField_derivative_detection')
        self.assertTrue(item(self.host, 'ordinaryField_d2_window').isVisible())
        self.assertFalse(item(self.host, 'ordinaryField_prominence').isVisible())
        self.bridge.focusRequested.emit('d2_max_dips', self.bridge.token)
        pump(60)
        self.assertTrue(item(self.host, 'ordinaryField_d2_max_dips').hasActiveFocus())
        self.assertEqual(self.bridge.data['values']['d2_mode'], 'Peaks')
        self.bridge.selectTab(1)
        pump()
        click_item(self.host, 'ordinaryField_csv_source')
        self.assertTrue(item(self.host, 'ordinaryCommand_loadCsv').isVisible())

    def test_stale_focus_and_pressed_command_are_retired(self):
        editor = self.type('fit_xmin', '7.125')
        old = self.bridge.token
        self.bridge.token = 'workspace:other:graph:cube:2'
        self.bridge.data['texts']['fit_xmin'] = '10'
        self.bridge.publish()
        pump()
        self.assertFalse(editor.hasActiveFocus())
        self.assertEqual(editor.property('text'), '10')
        self.assertFalse(any(call[0] == 'commit' and call[1] == old for call in self.bridge.calls))
        self.bridge.focusRequested.emit('fit_xmax', old)
        pump(60)
        self.assertFalse(item(self.host, 'ordinaryField_fit_xmax').hasActiveFocus())
        action = item(self.host, 'ordinaryCommand_fit')
        center = action.mapToScene(QPointF(action.width() / 2, action.height() / 2))
        point = QPoint(round(center.x()), round(center.y()))
        QTest.mousePress(self.host, Qt.LeftButton, pos=point)
        self.bridge.token = 'workspace:third:graph:cube:3'
        self.bridge.publish()
        pump()
        before = len([call for call in self.bridge.calls if call[0] == 'trigger'])
        QTest.mouseRelease(self.host, Qt.LeftButton, pos=point)
        pump()
        self.assertEqual(len([call for call in self.bridge.calls if call[0] == 'trigger']), before)

    def test_real_choice_keyboard_step_and_peak_highlight_input(self):
        editor = self.type('distance', '17')
        QTest.keyClick(self.host, Qt.Key_Up)
        pump()
        self.assertEqual(editor.property('text'), '18.0')
        self.assertIn(('step', 'distance', 1, self.bridge.token), self.bridge.calls)
        self.bridge.selectTab(1)
        pump()
        choice = item(self.host, 'ordinaryField_fit_model')
        choice.forceActiveFocus(Qt.OtherFocusReason)
        QTest.keyClick(self.host, Qt.Key_Down)
        pump()
        self.assertEqual(self.bridge.data['values']['fit_model'], 'Gaussian')
        self.assertIn(('value', 'fit_model', 'Gaussian', self.bridge.token), self.bridge.calls)
        peak = item(self.host, 'ordinaryPeakNumber')
        peak.forceActiveFocus(Qt.OtherFocusReason)
        QTest.keyClick(self.host, Qt.Key_Up)
        pump()
        self.assertEqual(self.bridge.data['peakNumber'], 2)
        self.assertIn(('peak', 2, self.bridge.token), self.bridge.calls)

    def test_readiness_commands_and_plain_selectable_summary(self):
        self.bridge.selectTab(1)
        pump()
        click_item(self.host, 'ordinaryCommand_fit')
        self.assertIn(('trigger', 'fit', self.bridge.token), self.bridge.calls)
        self.bridge.data['ui']['fitEnabled'] = False
        self.bridge.publish()
        pump()
        self.assertFalse(item(self.host, 'ordinaryCommand_fit').isEnabled())
        summary = item(self.host, 'ordinarySummary')
        self.assertTrue(summary.property('readOnly'))
        self.assertTrue(summary.property('selectByMouse'))
        self.assertEqual(summary.property('text'), '<b>plain summary</b>')
        expression = QQmlExpression(self.host.rootContext(), summary, 'textFormat === 0')
        self.assertEqual(expression.evaluate(), (True, False))
        self.assertEqual(item(self.host, 'ordinaryReadiness').property('text'), '3 seeds ready')

    def test_long_summary_has_bounded_viewport_with_keyboard_and_wheel_access(self):
        previous_height = self.host.height()
        self.bridge.data['summary'] = '\n'.join(f'Line {index}: fitted result' for index in range(300))
        self.bridge.publish()
        pump(60)
        self.assertLessEqual(self.host.height(), previous_height + 130)
        viewport = item(self.host, 'ordinarySummaryViewport')
        self.assertLessEqual(viewport.height(), 130)
        summary = item(self.host, 'ordinarySummary')
        summary.forceActiveFocus(Qt.OtherFocusReason)
        QTest.keyClick(self.host, Qt.Key_End, Qt.ControlModifier)
        pump()
        self.assertEqual(summary.property('cursorPosition'), len(self.bridge.data['summary']))
        flickable = viewport.property('contentItem')
        self.assertGreater(flickable.property('contentY'), 0)
        QTest.keyClick(self.host, Qt.Key_Home, Qt.ControlModifier)
        pump()
        position = viewport.mapToScene(QPointF(viewport.width() / 2, viewport.height() / 2))
        event = QWheelEvent(position, position, QPoint(), QPoint(0, -120), Qt.NoButton,
                            Qt.NoModifier, Qt.ScrollUpdate, False)
        APP.sendEvent(self.host, event)
        pump()
        self.assertGreater(flickable.property('contentY'), 0)


if __name__ == '__main__':
    unittest.main()

