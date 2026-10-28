"""Actual Quick input for Analysis commands, snapshot Results and local Style drafts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
import math
import unittest

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QPoint,
                            QPointF, Property, Qt, Signal, Slot, qInstallMessageHandler)
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
import shiboken6

from spectra_app.quick_dialog import QuickDialog
from spectra_app.quick_ui import QuickPanel
from tests.formal_quick_helpers import item, pump

APP = QApplication.instance() or QApplication([])
LAYERS = ['detected-peak', 'detected-dip', 'fitted-peak', 'fitted-dip']
PROVENANCE = 'Frozen source: original input\nFilter: pending criteria are not applied\nFit failed at this Gate; retained for reproducibility.'


class SnapshotModel(QAbstractTableModel):
    def rowCount(self, parent=QModelIndex()):
        return 2000 if not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()):
        return 28 if not parent.isValid() else 0

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == Qt.DisplayRole:
            return PROVENANCE if index.column() == 2 else f'{index.row()}:{index.column()}'

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return ['Gate', 'Status', 'Message'][section] if section < 3 else f'Parameter {section}'


class AnalysisPopupFixture(QObject):
    stateChanged = Signal()
    focusRequested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.token = 'dataset:graph:frozen-input'
        self.closed = False
        self.calls = []
        self.error_text = ''
        self.model = SnapshotModel(self)
        self.data = dict(busy=False, sourceAvailable=True, auxLinecutVisible=True,
                         rowCount=2000, columnCount=28, selectedRow=0, selectedColumn=0,
                         selectedHeader='Gate', selectedText='0:0',
                         styles={layer: dict(color='#336699', marker='o', size='5.0', filled=True)
                                 for layer in LAYERS})
        self.canonical = deepcopy(self.data['styles'])
        self.locate_result = False
        self.save_result = False
        self.export_result = False

    state = Property('QVariantMap', lambda self: deepcopy(self.data), notify=stateChanged)
    ownerToken = Property(str, lambda self: self.token, notify=stateChanged)
    enabled = Property(bool, lambda self: not self.closed and not self.data['busy'], notify=stateChanged)
    canAccept = Property(bool, lambda self: self.enabled, notify=stateChanged)
    errorText = Property(str, lambda self: self.error_text, notify=stateChanged)
    busy = Property(bool, lambda self: self.data['busy'], notify=stateChanged)
    sourceAvailable = Property(bool, lambda self: self.data['sourceAvailable'], notify=stateChanged)
    auxLinecutVisible = Property(bool, lambda self: self.data['auxLinecutVisible'], notify=stateChanged)
    layerIds = Property('QStringList', lambda self: LAYERS, constant=True)
    tableModel = Property(QObject, lambda self: self.model, constant=True)

    def valid(self, owner):
        return self.enabled and owner == self.token

    def shutdown(self):
        self.closed = True
        self.stateChanged.emit()

    @Slot(str, str, result=bool)
    def command(self, command, owner):
        self.calls.append(('command', command, owner))
        return self.valid(owner)

    @Slot(bool, str, result=bool)
    def setAuxLinecut(self, visible, owner):
        self.calls.append(('auxLinecut', visible, owner))
        if not self.valid(owner):
            return False
        self.data['auxLinecutVisible'] = visible
        self.stateChanged.emit()
        return True

    @Slot(int, int, str, result=bool)
    def selectCell(self, row, column, owner):
        self.calls.append(('selectCell', row, column, owner))
        if not self.valid(owner):
            return False
        self.data.update(selectedRow=row, selectedColumn=column,
                         selectedHeader=self.model.headerData(column, Qt.Horizontal),
                         selectedText=self.model.data(self.model.index(row, column)))
        self.stateChanged.emit()
        return True

    @Slot(str, result=bool)
    def locate(self, owner):
        self.calls.append(('locate', owner))
        return self.valid(owner) and self.locate_result

    @Slot(str, result=bool)
    def saveWorkbook(self, owner):
        self.calls.append(('saveWorkbook', owner))
        return self.valid(owner) and self.save_result

    @Slot(str, result=bool)
    def exportCsv(self, owner):
        self.calls.append(('exportCsv', owner))
        return self.valid(owner) and self.export_result

    @Slot(str, str, str, str, result=bool)
    def draftStyle(self, layer, field, text, owner):
        self.calls.append(('draftStyle', layer, field, text, owner))
        if not self.valid(owner):
            return False
        self.data['styles'][layer][field] = text == 'true' if field == 'filled' else text
        self.stateChanged.emit()
        return True

    @Slot(str, result=bool)
    def applyStyles(self, owner):
        self.calls.append(('applyStyles', owner))
        if not self.valid(owner):
            return False
        for layer in LAYERS:
            try:
                size = float(self.data['styles'][layer]['size'])
                if not math.isfinite(size) or not 0.1 <= size <= 100:
                    raise ValueError
            except ValueError:
                self.error_text = 'Size must be a finite number from 0.1 to 100.'
                self.stateChanged.emit()
                self.focusRequested.emit(layer, 'size')
                return False
        self.canonical = deepcopy(self.data['styles'])
        return True


class AnalysisResultsQmlTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.old_handler = qInstallMessageHandler(lambda kind, context, text: self.messages.append(text))
        self.bridge = AnalysisPopupFixture()
        self.hosts = []

    def tearDown(self):
        for host in self.hosts:
            host.close()
            host.deleteLater()
        pump()
        qInstallMessageHandler(self.old_handler)
        self.assertEqual([text for text in self.messages if '.qml:' in text], [])

    def dialog(self, filename, size=(390, 480)):
        host = QuickDialog(self.bridge, filename, title='Analysis Quick input test', size=size)
        self.hosts.append(host)
        host.show()
        pump(70)
        return host

    def click(self, host, name, double=False, *, receiver=None):
        control = item(host, name)
        point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2)).toPoint()
        window = control.window()
        self.assertIsNotNone(window, name)
        self.assertTrue(shiboken6.isValid(window), name)
        if receiver is None:
            host_window = host.quickWindow() if isinstance(host, QQuickWidget) else host.window()
            receiver = host if isinstance(host, QQuickWidget) and window is host_window else window
        else:
            self.assertIs(window, receiver, name)
        self.assertTrue(shiboken6.isValid(receiver) and receiver.isVisible(), name)
        # Crossing between the widget host and native popup needs a pointer move.
        QTest.mouseMove(receiver, point)
        (QTest.mouseDClick if double else QTest.mouseClick)(receiver, Qt.LeftButton, pos=point)
        pump()
        return control

    def type(self, host, name, text):
        control = item(host, name)
        control.forceActiveFocus(Qt.OtherFocusReason)
        pump()
        QTest.keyClick(host.window(), Qt.Key_A, Qt.ControlModifier)
        for character in text:
            QTest.keyClick(host.window(), Qt.Key(ord(character.upper())),
                           Qt.ShiftModifier if character.isupper() else Qt.NoModifier)
            pump(1)
        pump()
        return control

    def test_commands_wrap_routes_toggle_busy_and_stale_keyboard_release(self):
        host = QuickPanel('AnalysisCommands.qml', self.bridge, bridge_property='bridge')
        self.hosts.append(host)
        host.resize(280, host.height())
        host.show()
        host.activateWindow()
        pump(70)
        self.assertEqual(host.status(), QQuickWidget.Ready)
        commands = ['source', 'original', 'results', 'duplicate']
        for command in commands:
            control = self.click(host, 'command_' + command)
            self.assertLessEqual(control.x() + control.width(), host.width())
            self.assertEqual(self.bridge.calls[-1], ('command', command, self.bridge.token))
        menu = host.rootObject().findChild(QObject, 'analysisExportMenu')
        for command in ['exportSvg', 'exportPng', 'copySvg']:
            opened = QSignalSpy(menu.opened)
            self.click(host, 'command_exportMenu')
            if not menu.property('opened'):
                self.assertTrue(opened.wait(1000), command)
            self.assertTrue(menu.property('visible') and menu.property('opened'), command)
            control = item(host, 'command_' + command)
            receiver = control.window()
            self.assertIsNotNone(receiver, command)
            self.assertTrue(shiboken6.isValid(receiver) and receiver.isVisible(), command)
            self.assertIsNot(receiver, host.quickWindow())
            self.assertLessEqual(control.mapToScene(QPointF(0, control.height())).y(), receiver.height() + 1)
            self.click(host, 'command_' + command, receiver=receiver)
            self.assertEqual(self.bridge.calls[-1], ('command', command, self.bridge.token))
        self.click(host, 'command_auxLinecut')
        self.assertFalse(self.bridge.data['auxLinecutVisible'])
        self.click(host, 'command_exportMenu')
        self.assertTrue(menu.property('visible'))
        self.bridge.token = 'retired-menu-owner'
        self.bridge.stateChanged.emit()
        pump()
        self.assertFalse(menu.property('visible'))
        command = item(host, 'command_results')
        command.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyPress(host, Qt.Key_Space)
        old_count = len(self.bridge.calls)
        self.bridge.token = 'replacement-owner'
        self.bridge.stateChanged.emit()
        QTest.keyRelease(host, Qt.Key_Space)
        pump()
        self.assertEqual(len(self.bridge.calls), old_count)
        self.bridge.data['busy'] = True
        self.bridge.stateChanged.emit()
        pump()
        self.assertFalse(command.isEnabled())
        self.bridge.data.update(busy=False, sourceAvailable=False)
        self.bridge.stateChanged.emit()
        pump()
        self.assertFalse(item(host, 'command_source').isEnabled())

    def test_results_virtualized_full_message_selection_and_doubleclick(self):
        host = self.dialog('AnalysisResults.qml', (700, 520))
        root = host.rootObject()
        def descendants(obj):
            for child in obj.childItems():
                yield child
                yield from descendants(child)
        cells = [obj for obj in descendants(root) if obj.objectName().startswith('resultCell_')]
        self.assertGreater(len(cells), 0)
        self.assertLess(len(cells), 500)
        self.click(host, 'resultCell_1_2')
        self.assertEqual(self.bridge.data['selectedRow'], 1)
        self.assertEqual(self.bridge.data['selectedColumn'], 2)
        self.assertEqual(item(host, 'resultsSelectedText').property('text'), PROVENANCE)
        self.assertEqual(item(host, 'resultsSelectedHeader').property('text'), 'Message')
        QTest.keyClick(host.window(), Qt.Key_Left)
        pump()
        self.assertEqual(self.bridge.data['selectedColumn'], 1)
        QTest.keyClick(host.window(), Qt.Key_Right)
        pump()
        self.assertEqual(item(host, 'resultsSelectedText').property('text'), PROVENANCE)
        self.bridge.locate_result = True
        self.click(host, 'resultCell_1_2', double=True)
        self.assertIn(('locate', self.bridge.token), self.bridge.calls)
        self.assertTrue(self.bridge.closed)

    def test_results_cancelled_export_save_stays_open_success_closes_and_narrow_actions_fit(self):
        host = self.dialog('AnalysisResults.qml')
        for name in ['resultsLocate', 'resultsSave', 'resultsExport', 'resultsClose']:
            control = item(host, name)
            point = control.mapToScene(QPointF(control.width(), control.height()))
            self.assertLessEqual(point.x(), host.width())
            self.assertLessEqual(point.y(), host.height())
        self.click(host, 'resultsExport')
        self.click(host, 'resultsSave')
        self.click(host, 'resultsLocate')
        self.assertTrue(host.window().isVisible())
        self.bridge.save_result = True
        self.click(host, 'resultsSave')
        self.assertFalse(host.window().isVisible())

    def test_style_multicharacter_draft_keeps_item_focus_cursor_and_cancel_discards(self):
        host = self.dialog('AnalysisStyle.qml', (390, 600))
        name = 'style_detected-peak_color'
        original = item(host, name)
        self.assertGreaterEqual(original.width(), 120)
        control = self.type(host, name, '#ab12cd')
        self.assertIs(control, original)
        self.assertIs(item(host, name), original)
        self.assertTrue(control.hasActiveFocus())
        self.assertEqual(control.property('cursorPosition'), 7)
        self.assertEqual(control.property('text'), '#ab12cd')
        self.assertEqual(self.bridge.data['styles']['detected-peak']['color'], '#ab12cd')
        self.assertEqual(self.bridge.canonical['detected-peak']['color'], '#336699')
        self.click(host, 'styleCancel')
        self.assertTrue(self.bridge.closed)
        self.assertEqual(self.bridge.canonical['detected-peak']['color'], '#336699')

    def test_style_invalid_raw_text_revealed_and_popup_window_contained(self):
        host = self.dialog('AnalysisStyle.qml', (390, 400))
        last = 'style_fitted-dip_size'
        self.type(host, last, '-')
        self.click(host, 'styleApply')
        pump(60)  # Error layout and the validation reveal timer must both settle.
        editor = item(host, last)
        self.assertEqual(editor.property('text'), '-')
        self.assertTrue(editor.hasActiveFocus())
        position = editor.mapToScene(QPointF(0, 0))
        viewport = item(host, 'styleScroll')
        top = viewport.mapToScene(QPointF(0, 0)).y()
        self.assertGreaterEqual(position.y(), top)
        self.assertLessEqual(position.y() + editor.height(), top + viewport.property('availableHeight'))
        self.assertTrue(host.window().isVisible())
        # A real marker popup must escape the short dialog viewport.
        marker = item(host, 'style_fitted-dip_marker')
        self.bridge.focusRequested.emit('fitted-dip', 'marker')
        pump(60)
        self.click(host, 'style_fitted-dip_marker')
        popup = next(child for child in marker.findChildren(QObject) if child.inherits('QQuickPopup'))
        self.assertTrue(popup.property('visible'))
        content = popup.property('contentItem')
        rows = [obj for obj in content.childItems()[0].childItems() if obj.inherits('QQuickMenuItem')]
        self.assertGreater(len(rows), 0)
        self.assertIsNot(rows[0].window(), host.window())
        for row in rows:
            point = row.mapToScene(QPointF(0, row.height()))
            self.assertLessEqual(point.y(), row.window().height() + 1)
        chosen = rows[1]
        QTest.mouseClick(chosen.window(), Qt.LeftButton,
                         pos=chosen.mapToScene(QPointF(chosen.width()/2, chosen.height()/2)).toPoint())
        pump()
        self.assertEqual(self.bridge.data['styles']['fitted-dip']['marker'], 's')
        self.assertEqual(editor.property('text'), '-')
        self.click(host, 'style_fitted-dip_marker')
        self.assertTrue(popup.property('visible'))
        self.bridge.closed = True
        self.bridge.stateChanged.emit()
        pump()
        self.assertFalse(popup.property('visible'))

    def test_style_escape_and_window_close_never_apply(self):
        for close in ['escape', 'window']:
            self.bridge = AnalysisPopupFixture()
            host = self.dialog('AnalysisStyle.qml')
            self.type(host, 'style_detected-peak_size', '12.34')
            if close == 'escape':
                QTest.keyClick(host.window(), Qt.Key_Escape)
            else:
                host.window().close()
            pump()
            self.assertTrue(self.bridge.closed)
            self.assertEqual(self.bridge.canonical['detected-peak']['size'], '5.0')
            self.assertFalse(any(call[0] == 'applyStyles' for call in self.bridge.calls))


if __name__ == '__main__':
    unittest.main()
