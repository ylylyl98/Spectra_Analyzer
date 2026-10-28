"""Typed Stage17 XY consumer fixtures exercised through actual Qt input."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import unittest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Property, Qt, QUrl, Signal, Slot, qInstallMessageHandler
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtQuick import QQuickImageProvider, QQuickItem
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from tests.formal_quick_helpers import item, pump, click_item

APP = QApplication.instance() or QApplication([])
QML = Path(__file__).parents[1] / 'spectra_app/qml'


class XYFixture(QObject):
    imageChanged = Signal()
    geometryChanged = Signal()
    stateChanged = Signal()
    cursorChanged = Signal()
    rubberbandChanged = Signal()
    messageChanged = Signal()

    def __init__(self, dpr=1):
        super().__init__()
        self.dpr = dpr
        self.viewport = (0., 0.)
        self.token, self.live = 'comparison:1:epoch:1', True
        self.calls, self.accepted = [], []
        self.cancel_count = 0
        self.band = dict(visible=False, x=0., y=0., width=0., height=0.)
        self.mode_value = ''
        self.rows = [dict(key=key, text=text, tooltip=text+' action', enabled=True, checked=False, checkable=False)
                     for key, text in [('style:line', 'Line'), ('style:scatter', 'Scatter'), ('style:line_symbol', 'Line + Symbol'), ('analyze', 'Analyze'), ('resume', 'Resume analysis'), ('add', 'Add data…'), ('addSheets', 'Existing Sheets…'), ('addFiles', 'From files…'), ('inspector', 'Inspector')]]
        self.frame = QImage(round(240*dpr), round(140*dpr), QImage.Format_RGBA8888)
        self.frame.fill(QColor('#ecf3fa'))
        painter = QPainter(self.frame)
        painter.fillRect(round(30*dpr), round(30*dpr), round(80*dpr), round(60*dpr), QColor('#2468a0'))
        painter.end()
        self.frame.setDevicePixelRatio(dpr)

    imageUrl = Property(str, lambda s: 'image://xy-fixture/1', notify=imageChanged)
    contentX = Property(float, lambda s: 50., notify=geometryChanged)
    contentY = Property(float, lambda s: 40., notify=geometryChanged)
    contentWidth = Property(float, lambda s: 240., notify=geometryChanged)
    contentHeight = Property(float, lambda s: 140., notify=geometryChanged)
    viewportWidth = Property(float, lambda s: s.viewport[0], notify=geometryChanged)
    viewportHeight = Property(float, lambda s: s.viewport[1], notify=geometryChanged)
    devicePixelRatio = Property(float, lambda s: float(s.dpr), notify=geometryChanged)
    cursorShape = Property(int, lambda s: Qt.CrossCursor.value, notify=cursorChanged)
    rubberband = Property('QVariantMap', lambda s: s.band, notify=rubberbandChanged)
    ownerToken = Property(str, lambda s: s.token, notify=stateChanged)
    enabled = Property(bool, lambda s: s.live, notify=stateChanged)
    backEnabled = Property(bool, lambda s: True, notify=stateChanged)
    forwardEnabled = Property(bool, lambda s: False, notify=stateChanged)
    mode = Property(str, lambda s: s.mode_value, notify=stateChanged)
    message = Property(str, lambda s: 'x=1.0 y=2.0', notify=messageChanged)
    selectionText = Property(str, lambda s: '1 selected', notify=stateChanged)
    inspectorChecked = Property(bool, lambda s: True, notify=stateChanged)
    actions = Property('QVariantList', lambda s: s.rows, notify=stateChanged)

    def record(self, call, owner):
        self.calls.append(call)
        accepted = self.live and owner == self.token
        if accepted: self.accepted.append(call)
        return accepted

    @Slot(float, float, int, result=bool)
    def setSceneViewport(self, width, height, generation):
        self.viewport = (width, height)
        return True

    @Slot(str, float, float, int, int, int, bool, str, result=bool)
    def pointerEvent(self, kind, x, y, button, buttons, modifiers, double_click, owner):
        return self.record((kind, x, y, button, buttons, modifiers, double_click, owner), owner)

    @Slot(str, int, str, int, str, result=bool)
    def keyEvent(self, kind, key, text, modifiers, owner):
        return self.record(('key', kind, key, text, modifiers, owner), owner)

    @Slot(str, str, result=bool)
    def invoke(self, key, owner): return self.record(('invoke', key, owner), owner)

    @Slot()
    def cancelGesture(self): self.cancel_count += 1

    def replace_owner(self):
        self.token += ':next'
        self.stateChanged.emit()
        pump()


class FrameProvider(QQuickImageProvider):
    def __init__(self, fixture):
        super().__init__(QQuickImageProvider.Image)
        self.fixture = fixture
    def requestImage(self, identifier, size, requested_size):
        frame = self.fixture.frame
        size.setWidth(frame.width()); size.setHeight(frame.height())
        return frame.copy()


class XYCanvasQmlTests(unittest.TestCase):
    def setUp(self):
        self.messages, self.hosts = [], []
        self.old_handler = qInstallMessageHandler(lambda kind, context, text: self.messages.append(text))
    def tearDown(self):
        for host in self.hosts:
            host.close(); host.deleteLater()
        pump()
        qInstallMessageHandler(self.old_handler)
        self.assertEqual([text for text in self.messages if '.qml:' in text], [])

    def scene(self, name='XYCanvas', width=340, dpr=1):
        self.bridge = XYFixture(dpr)
        host = QQuickWidget()
        host.setResizeMode(QQuickWidget.SizeRootObjectToView)
        host.setFont(QFont('Arial', 10))
        host.engine().addImageProvider('xy-fixture', FrameProvider(self.bridge))
        host.setInitialProperties({'bridge': self.bridge})
        host.setSource(QUrl.fromLocalFile(str(QML / (name+'.qml'))))
        host.resize(width, 220 if name == 'XYCanvas' else 76)
        host.show(); host.activateWindow(); pump(70)
        self.hosts.append(host)
        self.assertEqual(host.status(), QQuickWidget.Ready, [e.toString() for e in host.errors()])
        if name != 'XYCanvas':
            host.rootObject().setProperty('controlFont', QFont('Arial', 10))
            host.resize(width, round(host.rootObject().implicitHeight()))
            pump()
        return host

    def move(self, host, x, y, buttons=Qt.LeftButton, modifiers=Qt.NoModifier):
        point = QPointF(x, y)
        QApplication.sendEvent(host, QMouseEvent(QEvent.MouseMove, point, host.mapToGlobal(point.toPoint()), Qt.NoButton, buttons, modifiers))
        pump()

    def gestures(self, calls=None):
        return [c for c in (self.bridge.accepted if calls is None else calls) if c[0] in ('press', 'release') or c[0]=='move' and c[4]]

    def test_frame_letterbox_logical_projection_and_dpr_fixture(self):
        for dpr in (1, 2):
            host = self.scene(dpr=dpr)
            image = item(host, 'XYCanvasImage')
            self.assertEqual((image.x(), image.y(), image.width(), image.height()), (50., 40., 240., 140.))
            self.assertEqual(self.bridge.viewport, (340., 220.))
            frame = host.grabFramebuffer(); ratio=frame.width()/host.width()
            self.assertEqual(frame.pixelColor(round(90*ratio), round(80*ratio)).name(), '#2468a0')
            self.assertEqual(frame.pixelColor(round(60*ratio), round(50*ratio)).name(), '#ecf3fa')
            self.assertEqual(frame.pixelColor(round(10*ratio), round(10*ratio)).name(), '#e8ecf1')
            QTest.mouseClick(host, Qt.LeftButton, pos=QPoint(90, 80)); pump()
            self.assertEqual(next(c for c in self.bridge.calls if c[0]=='press')[1:3], (90., 80.))

    def test_grab_outside_and_true_flags_and_double_click(self):
        host = self.scene()
        QTest.mousePress(host, Qt.LeftButton, Qt.ControlModifier, QPoint(90, 80))
        QTest.mousePress(host, Qt.RightButton, Qt.ControlModifier, QPoint(90, 80))
        self.move(host, -30, 260, Qt.LeftButton|Qt.RightButton, Qt.ControlModifier)
        QTest.mouseRelease(host, Qt.RightButton, Qt.ControlModifier, QPoint(-30,260))
        QTest.mouseRelease(host, Qt.LeftButton, Qt.ControlModifier, QPoint(-30,260)); pump()
        calls=self.gestures()
        self.assertEqual([c[0] for c in calls], ['press','press','move','release','release'])
        self.assertEqual(calls[2][1:3], (-30.,260.))
        self.assertEqual(calls[2][4], (Qt.LeftButton|Qt.RightButton).value)
        self.assertTrue(all(c[5]&Qt.ControlModifier.value for c in calls))
        QTest.mouseDClick(host, Qt.LeftButton, Qt.AltModifier, QPoint(80,60))
        QTest.mouseRelease(host, Qt.LeftButton, Qt.AltModifier, QPoint(80,60)); pump()
        self.assertTrue(any(c[0]=='press' and c[6] for c in self.bridge.accepted))

    def test_owner_roundtrip_cancels_pointer_and_focused_keys(self):
        host=self.scene()
        QTest.mousePress(host,Qt.LeftButton,pos=QPoint(90,80))
        old=self.bridge.token
        self.bridge.replace_owner(); self.bridge.token=old; self.bridge.stateChanged.emit(); pump()
        self.move(host,120,90); QTest.mouseRelease(host,Qt.LeftButton,pos=QPoint(120,90)); pump()
        self.assertEqual([c[0] for c in self.gestures()],['press'])
        self.assertEqual(self.bridge.cancel_count,1)
        before=len(self.bridge.accepted)
        QTest.keyClick(host,Qt.Key_Delete); pump()
        self.assertEqual(len(self.bridge.accepted),before)
        QTest.mouseClick(host,Qt.LeftButton,pos=QPoint(95,85))
        QTest.keyClick(host,Qt.Key_Escape); pump()
        self.assertTrue(any(c[0]=='key' and c[2]==Qt.Key_Escape.value for c in self.bridge.accepted))

    def test_same_owner_refresh_preserves_drag_and_hide_cancels(self):
        host=self.scene()
        QTest.mousePress(host,Qt.LeftButton,pos=QPoint(90,80))
        self.bridge.stateChanged.emit(); pump()
        self.move(host,110,90); QTest.mouseRelease(host,Qt.LeftButton,pos=QPoint(110,90)); pump()
        self.assertEqual([c[0] for c in self.gestures()],['press','move','release'])
        QTest.mousePress(host,Qt.LeftButton,pos=QPoint(90,80))
        host.rootObject().setVisible(False); pump()
        self.assertEqual(self.bridge.cancel_count,1)

    def test_paired_rubberband_and_cursor_and_no_wheel_command(self):
        host=self.scene()
        self.bridge.band.update(visible=True,x=30.,y=30.,width=100.,height=60.)
        self.bridge.rubberbandChanged.emit(); pump()
        band=item(host,'XYCanvasRubberband')
        self.assertEqual(band.mapToScene(QPointF()),QPointF(80,70))
        frame=host.grabFramebuffer(); ratio=frame.width()/host.width()
        self.assertEqual(frame.pixelColor(round(80*ratio),round(80*ratio)).name(),'#ffffff')
        self.assertEqual(frame.pixelColor(round(81*ratio),round(80*ratio)).name(),'#202020')
        self.assertEqual(item(host,'XYCanvasPointer').property('cursorShape'),Qt.CrossCursor)
        before=list(self.bridge.accepted)
        point=QPointF(90,80)
        QApplication.sendEvent(host,QWheelEvent(point,host.mapToGlobal(point.toPoint()),QPoint(),QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)); pump()
        self.assertEqual(self.bridge.accepted,before)

    def test_navigation_controls_real_clicks_disabled_and_checked_projection(self):
        host=self.scene('XYNavigation',900)
        for key in ('home','back','pan','zoom'):
            click_item(host,'XYNavigation_'+key)
        click_item(host,'XYNavigation_forward')
        click_item(host,'XYAddDataButton'); click_item(host,'XYInspectorToggle')
        self.assertEqual([c[1] for c in self.bridge.accepted if c[0]=='invoke'],['home','back','pan','zoom','add','inspector'])
        self.bridge.mode_value='pan/zoom'; self.bridge.stateChanged.emit(); pump()
        self.assertTrue(item(host,'XYNavigation_pan').property('checked'))
        self.assertTrue(item(host,'XYInspectorToggle').property('checked'))
        host.resize(320,host.height()); pump()
        self.assertGreater(host.rootObject().implicitHeight(),40)
        self.assertTrue(all(child.x()+child.width()<=320 for child in item(host,'XYNavigationRoot').childItems() if child.isVisible()))

    def test_commands_responsive_overflow_and_canonical_disabled_state(self):
        host=self.scene('XYCommands',900)
        for key in ('style_line','style_scatter','style_line_symbol','analyze','resume'):
            click_item(host,'XYCommand_'+key)
        self.assertEqual([c[1] for c in self.bridge.accepted],['style:line','style:scatter','style:line_symbol','analyze','resume'])
        self.assertFalse(item(host,'XYCommandsOverflow').isVisible())
        host.resize(230,40); pump()
        self.assertTrue(item(host,'XYCommandsOverflow').isVisible())
        self.assertFalse(item(host,'XYCommand_style_line').isVisible())
        self.assertTrue(item(host,'XYCommand_analyze').isVisible())
        self.assertGreater(item(host,'XYSelectionCount').width(),20)
        self.bridge.rows[3]['enabled']=False; self.bridge.stateChanged.emit(); pump()
        before=len(self.bridge.accepted); click_item(host,'XYCommand_analyze')
        self.assertEqual(len(self.bridge.accepted),before)

    def test_stale_button_focus_keyboard_does_not_recapture_owner(self):
        host=self.scene('XYCommands',900)
        click_item(host,'XYCommand_analyze')
        before=len(self.bridge.accepted)
        self.bridge.replace_owner()
        QTest.keyClick(host,Qt.Key_Space); pump()
        self.assertEqual(len(self.bridge.accepted),before)
        click_item(host,'XYCommand_analyze')
        self.assertEqual(len(self.bridge.accepted),before+1)

    def popup_click(self, host, name):
        control=item(host,name)
        window=control.window()
        self.assertIsNotNone(window)
        point=control.mapToScene(QPointF(control.width()/2,control.height()/2)).toPoint()
        QTest.mouseClick(window,Qt.LeftButton,pos=point); pump()

    def test_add_popup_actual_items_and_stale_owner_close(self):
        host=self.scene('XYNavigation',900)
        click_item(host,'XYAddDataOptions')
        self.popup_click(host,'XYAddFiles')
        self.assertEqual(self.bridge.accepted[-1][1],'addFiles')
        click_item(host,'XYAddDataOptions')
        menu=host.rootObject().findChild(QObject,'XYAddDataMenu')
        self.assertTrue(menu.property('visible'))
        self.bridge.replace_owner()
        self.assertFalse(menu.property('visible'))
        self.assertEqual([c[1] for c in self.bridge.accepted],['addFiles'])

    def test_overflow_actual_items_and_owner_cancellation(self):
        host=self.scene('XYCommands',230)
        click_item(host,'XYCommandsOverflow')
        self.popup_click(host,'XYOverflow_style_line_symbol')
        self.assertEqual(self.bridge.accepted[-1][1],'style:line_symbol')
        click_item(host,'XYCommandsOverflow')
        self.popup_click(host,'XYOverflow_resume')
        self.assertEqual(self.bridge.accepted[-1][1],'resume')
        click_item(host,'XYCommandsOverflow')
        menu=host.rootObject().findChild(QObject,'XYCommandsOverflowMenu')
        self.bridge.replace_owner()
        self.assertFalse(menu.property('visible'))


if __name__=='__main__': unittest.main()
