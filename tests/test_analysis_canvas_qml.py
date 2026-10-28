"""Real Quick rendering and input at the ordinary canvas consumer boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import unittest

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Property, Qt, QUrl, Signal, Slot, qInstallMessageHandler
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QWheelEvent
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tests.formal_quick_helpers import item, pump


APP = QApplication.instance() or QApplication([])


class FrameProvider(QQuickImageProvider):
    def __init__(self, bridge):
        super().__init__(QQuickImageProvider.Image)
        self.bridge = bridge

    def requestImage(self, identifier, size, requested_size):
        frame = self.bridge.frame
        size.setWidth(frame.width())
        size.setHeight(frame.height())
        return frame.copy()


class CanvasFixture(QObject):
    """Exact typed v1 consumer API; no QWidget geometry or input adapters."""
    imageChanged = Signal()
    geometryChanged = Signal()
    stateChanged = Signal()
    cursorChanged = Signal()
    rubberbandChanged = Signal()
    peakMenuChanged = Signal()
    peakMenuFocusRequested = Signal(str)

    def __init__(self, dpr=1):
        super().__init__()
        self.logical_width, self.logical_height = 900., 600.
        self.viewport = (0., 0.)
        self.dpr = dpr
        self.token, self.live = 'source:graph:epoch1', True
        self.band = dict(visible=False, x=0., y=0., width=0., height=0.)
        self.calls, self.accepted = [], []
        self.pointer_anchors = []
        self.cancel_count = 0
        self.frame = QImage(round(self.logical_width * dpr), round(self.logical_height * dpr), QImage.Format_RGBA8888)
        self.frame.fill(QColor('#ecf3fa'))
        # A measured solid patch proves visible projection after scrolling.
        from PySide6.QtGui import QPainter
        painter = QPainter(self.frame)
        painter.fillRect(round(180 * dpr), round(120 * dpr), round(120 * dpr), round(90 * dpr), QColor('#2468a0'))
        painter.end()
        self.frame.setDevicePixelRatio(dpr)

    sceneGeneration = Property(int, lambda self: 1, notify=stateChanged)
    imageUrl = Property(str, lambda self: 'image://analysis-canvas/1', notify=imageChanged)
    contentWidth = Property(float, lambda self: self.logical_width, notify=geometryChanged)
    contentHeight = Property(float, lambda self: self.logical_height, notify=geometryChanged)
    viewportWidth = Property(float, lambda self: self.viewport[0], notify=geometryChanged)
    viewportHeight = Property(float, lambda self: self.viewport[1], notify=geometryChanged)
    devicePixelRatio = Property(float, lambda self: float(self.dpr), notify=geometryChanged)
    ownerToken = Property(str, lambda self: self.token, notify=stateChanged)
    enabled = Property(bool, lambda self: self.live, notify=stateChanged)
    cursorShape = Property(int, lambda self: Qt.CrossCursor.value, notify=cursorChanged)
    rubberband = Property('QVariantMap', lambda self: self.band, notify=rubberbandChanged)
    peakMenu = Property('QVariantMap', lambda self: {}, notify=peakMenuChanged)
    formatCommands = Property('QVariantMap', lambda self: {
        key: False for key in ('copy_svg', 'copy_png', 'settings_copy', 'settings_paste')
    }, notify=stateChanged)

    @Slot(int, float, float, result=bool)
    def reportViewport(self, generation, width, height):
        self.viewport = (width, height)
        return generation == 1

    @Slot(str, float, float, int, int, int, bool, str, result=bool)
    @Slot(str, float, float, int, int, int, bool, str, float, float, result=bool)
    def pointerEvent(self, kind, x, y, button, buttons, modifiers, double_click, owner,
                     anchor_x=None, anchor_y=None):
        call = (kind, x, y, button, buttons, modifiers, double_click, owner)
        self.calls.append(call)
        self.pointer_anchors.append((kind, anchor_x, anchor_y, owner))
        accepted = self.live and owner == self.token
        if accepted:
            self.accepted.append(call)
        return accepted

    @Slot(str, int, str, int, str, result=bool)
    def keyEvent(self, kind, key, text, modifiers, owner):
        call = ('key', kind, key, text, modifiers, owner)
        self.calls.append(call)
        accepted = self.live and owner == self.token
        if accepted:
            self.accepted.append(call)
        return accepted

    @Slot(float, float, float, int, str, result=bool)
    def scrollEvent(self, x, y, step, modifiers, owner):
        self.calls.append(('wheel', x, y, step, modifiers, owner))
        return self.live and owner == self.token

    @Slot()
    def cancelGesture(self):
        self.cancel_count += 1

    @Slot(str, str, result=bool)
    def dismissPeakMenu(self, request_id, owner):
        return False

    @Slot(str, result=bool)
    def restorePeakMenuFocus(self, owner):
        return self.live and owner == self.token

    def replace_owner(self):
        self.token += ':next'
        self.stateChanged.emit()
        pump()


class AnalysisCanvasQmlTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.old_handler = qInstallMessageHandler(lambda kind, context, text: self.messages.append(text))
        self.host = None

    def tearDown(self):
        if self.host is not None:
            self.host.close()
            self.host.deleteLater()
            pump()
        qInstallMessageHandler(self.old_handler)
        self.assertEqual([text for text in self.messages if '.qml:' in text], [])

    def create_scene(self, dpr=1):
        self.bridge = CanvasFixture(dpr)
        self.host = QQuickWidget()
        self.host.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.host.setFont(QFont('Arial', 10))
        self.host.engine().addImageProvider('analysis-canvas', FrameProvider(self.bridge))
        self.host.setInitialProperties({'bridge': self.bridge, 'attachmentGeneration': 1})
        self.host.setSource(QUrl.fromLocalFile(str(Path(__file__).parents[1] / 'spectra_app/qml/AnalysisCanvas.qml')))
        self.host.resize(340, 220)
        self.host.show()
        self.host.activateWindow()
        pump(60)
        self.assertEqual(self.host.status(), QQuickWidget.Ready, [error.toString() for error in self.host.errors()])
        self.viewport = item(self.host, 'analysisCanvasViewport')
        self.image = item(self.host, 'analysisCanvasImage')
        self.input = item(self.host, 'analysisCanvasInput')

    def scroll(self, x=140, y=90):
        self.viewport.setProperty('contentX', x)
        self.viewport.setProperty('contentY', y)
        pump()

    def move(self, x, y, buttons=Qt.LeftButton, modifiers=Qt.NoModifier):
        point = QPointF(x, y)
        event = QMouseEvent(QEvent.MouseMove, point, self.host.mapToGlobal(point.toPoint()), Qt.NoButton, buttons, modifiers)
        QApplication.sendEvent(self.host, event)
        pump()

    def gesture_calls(self, calls=None):
        # Native window exposure and QtTest cursor positioning can add genuine
        # unheld hover moves; only captured motion belongs to a drag sequence.
        return [call for call in (self.bridge.calls if calls is None else calls)
                if call[0] in ('press', 'release') or call[0] == 'move' and call[4]]

    def test_visible_frame_logical_size_scroll_projection_and_dpr(self):
        for dpr in (1, 2):
            with self.subTest(dpr=dpr):
                self.create_scene(dpr)
                self.assertEqual((self.image.width(), self.image.height()), (900., 600.))
                self.assertEqual(self.bridge.viewport, (340., 220.))
                self.scroll()
                frame = self.host.grabFramebuffer()
                ratio = frame.width() / self.host.width()
                self.assertEqual(frame.pixelColor(round(70 * ratio), round(60 * ratio)).name(), '#2468a0')
                self.assertEqual(frame.pixelColor(round(20 * ratio), round(20 * ratio)).name(), '#ecf3fa')
                QTest.mouseClick(self.host, Qt.LeftButton, pos=QPoint(75, 65))
                pump()
                press = next(call for call in self.bridge.calls if call[0] == 'press')
                self.assertEqual(press[1:3], (215., 155.))
                anchor = next(call for call in self.bridge.pointer_anchors if call[0] == 'press')
                self.assertEqual(anchor[1:3], (75., 65.))
                self.host.close()
                self.host.deleteLater()
                pump()
                self.host = None

    def test_grabbed_drag_preserves_buttons_modifiers_and_release_outside(self):
        self.create_scene()
        self.scroll()
        QTest.mousePress(self.host, Qt.LeftButton, Qt.ShiftModifier, QPoint(75, 65))
        self.move(360, 240, Qt.LeftButton, Qt.ShiftModifier)
        QTest.mouseRelease(self.host, Qt.LeftButton, Qt.ShiftModifier, QPoint(360, 240))
        pump()
        gesture = self.gesture_calls()
        self.assertEqual([call[0] for call in gesture], ['press', 'move', 'release'])
        self.assertEqual(gesture[0][1:3], (215., 155.))
        self.assertEqual(gesture[-1][1:3], (500., 330.))
        self.assertEqual(gesture[1][4], Qt.LeftButton.value)
        self.assertTrue(all(call[5] & Qt.ShiftModifier.value for call in gesture))
        self.assertEqual((self.viewport.property('contentX'), self.viewport.property('contentY')), (140., 90.))

    def test_stable_owner_refresh_preserves_drag_and_negative_release_coordinates(self):
        self.create_scene()
        self.scroll()
        QTest.mousePress(self.host, Qt.LeftButton, pos=QPoint(75, 65))
        self.bridge.stateChanged.emit()
        pump()
        self.move(-190, -140)
        QTest.mouseRelease(self.host, Qt.LeftButton, pos=QPoint(-190, -140))
        pump()
        self.assertEqual(self.bridge.cancel_count, 0)
        gesture = self.gesture_calls(self.bridge.accepted)
        self.assertEqual([call[0] for call in gesture], ['press', 'move', 'release'])
        self.assertEqual(gesture[-1][1:3], (-50., -50.))

    def test_additional_held_button_stays_with_one_captured_owner(self):
        self.create_scene()
        QTest.mousePress(self.host, Qt.LeftButton, Qt.ControlModifier, QPoint(75, 65))
        QTest.mousePress(self.host, Qt.RightButton, Qt.ControlModifier, QPoint(75, 65))
        self.move(100, 100, Qt.LeftButton | Qt.RightButton, Qt.ControlModifier)
        QTest.mouseRelease(self.host, Qt.RightButton, Qt.ControlModifier, QPoint(100, 100))
        QTest.mouseRelease(self.host, Qt.LeftButton, Qt.ControlModifier, QPoint(100, 100))
        pump()
        gesture = self.gesture_calls(self.bridge.accepted)
        self.assertEqual([call[0] for call in gesture], ['press', 'press', 'move', 'release', 'release'])
        self.assertEqual(gesture[2][4], (Qt.LeftButton | Qt.RightButton).value)
        self.assertEqual({call[-1] for call in gesture}, {self.bridge.token})

    def test_owner_roundtrip_cancels_held_pointer_and_stale_keyboard_focus(self):
        self.create_scene()
        QTest.mousePress(self.host, Qt.LeftButton, pos=QPoint(75, 65))
        old_owner = self.bridge.token
        self.bridge.replace_owner()
        self.bridge.token = old_owner
        self.bridge.stateChanged.emit()
        pump()
        self.move(100, 100)
        QTest.mouseRelease(self.host, Qt.LeftButton, pos=QPoint(100, 100))
        pump()
        self.assertEqual(self.bridge.cancel_count, 1)
        self.assertEqual([call[0] for call in self.gesture_calls(self.bridge.accepted)], ['press'])
        self.bridge.replace_owner()
        before = len(self.bridge.accepted)
        QTest.keyClick(self.host, Qt.Key_A, Qt.ControlModifier)
        pump()
        self.assertEqual(len(self.bridge.accepted), before)
        self.assertFalse(self.host.rootObject().property('activeFocus'))
        QTest.mouseClick(self.host, Qt.LeftButton, pos=QPoint(80, 70))
        QTest.keyClick(self.host, Qt.Key_A, Qt.ControlModifier)
        pump()
        keys = [call for call in self.bridge.accepted if call[0] == 'key']
        self.assertTrue(any(call[1:3] == ('press', Qt.Key_A.value) for call in keys))
        self.assertTrue(any(call[1:3] == ('release', Qt.Key_A.value) for call in keys))

    def test_wheel_forwards_callback_and_scrollbar_scrolls_without_science_press(self):
        self.create_scene()
        self.scroll()
        point = QPointF(75, 65)
        event = QWheelEvent(point, self.host.mapToGlobal(point.toPoint()), QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(self.host, event)
        pump(100)
        wheel = next(call for call in self.bridge.calls if call[0] == 'wheel')
        self.assertEqual(wheel[1:4], (215., 155., -1.))
        self.assertGreater(self.viewport.property('contentY'), 90)
        scrollbar = item(self.host, 'analysisCanvasVerticalScrollBar')
        self.assertTrue(scrollbar.isVisible())
        self.assertTrue(scrollbar.property('interactive'))
        old_y = self.viewport.property('contentY')
        self.bridge.calls.clear()
        point = scrollbar.mapToScene(QPointF(scrollbar.width() / 2, scrollbar.height() - 15)).toPoint()
        QTest.mouseClick(self.host, Qt.LeftButton, pos=point)
        pump(100)
        self.assertNotEqual(self.viewport.property('contentY'), old_y)
        self.assertFalse(any(call[0] == 'press' for call in self.bridge.calls))

    def test_horizontal_and_shift_wheel_preserve_viewport_scrolling(self):
        self.create_scene()
        self.scroll()
        point = QPointF(75, 65)
        for delta, modifiers in ((QPoint(-120, 0), Qt.NoModifier), (QPoint(0, -120), Qt.ShiftModifier)):
            with self.subTest(delta=delta, modifiers=modifiers):
                old_x = self.viewport.property('contentX')
                old_y = self.viewport.property('contentY')
                event = QWheelEvent(point, self.host.mapToGlobal(point.toPoint()), QPoint(), delta, Qt.NoButton, modifiers, Qt.NoScrollPhase, False)
                QApplication.sendEvent(self.host, event)
                pump(100)
                self.assertGreater(self.viewport.property('contentX'), old_x)
                self.assertEqual(self.viewport.property('contentY'), old_y)
                wheel = [call for call in self.bridge.calls if call[0] == 'wheel'][-1]
                self.assertEqual(wheel[3:5], (-1., modifiers.value))

    def test_rubberband_projects_image_coordinates_and_hide_cancels(self):
        self.create_scene()
        self.scroll()
        self.bridge.band = dict(visible=True, x=180., y=120., width=100., height=70.)
        self.bridge.rubberbandChanged.emit()
        pump()
        band = item(self.host, 'analysisCanvasRubberband')
        self.assertTrue(band.isVisible())
        self.assertEqual(band.mapToScene(QPointF(0, 0)), QPointF(40, 30))
        self.assertEqual((band.width(), band.height()), (100., 70.))
        for x, y in ((180., 120.), (160., 100.)):
            # The two thin outlines remain distinguishable against both the
            # dark image patch and the light frame background.
            self.bridge.band.update(x=x, y=y)
            self.bridge.rubberbandChanged.emit()
            pump()
            frame = self.host.grabFramebuffer()
            ratio = frame.width() / self.host.width()
            edge_x, edge_y = x-140, y-90+10
            self.assertEqual(frame.pixelColor(round(edge_x*ratio), round(edge_y*ratio)).name(), '#ffffff')
            self.assertEqual(frame.pixelColor(round((edge_x+1)*ratio), round(edge_y*ratio)).name(), '#202020')
        QTest.mousePress(self.host, Qt.LeftButton, pos=QPoint(75, 65))
        # QQuickWidget host hide is retired by the production bridge. Exercise
        # the consumer's own visibility boundary without fabricating host APIs.
        self.host.rootObject().setVisible(False)
        pump()
        self.assertGreaterEqual(self.bridge.cancel_count, 1)
        self.host.rootObject().setVisible(True)
        QTest.mouseRelease(self.host, Qt.LeftButton, pos=QPoint(75, 65))
        pump()
        self.assertFalse(any(call[0] == 'release' for call in self.bridge.accepted))

    def test_doubleclick_and_hover_enter_leave_use_actual_events(self):
        self.create_scene()
        QTest.mouseMove(self.host, QPoint(40, 40))
        QTest.mouseDClick(self.host, Qt.LeftButton, Qt.AltModifier, QPoint(80, 60))
        QTest.mouseRelease(self.host, Qt.LeftButton, Qt.AltModifier, QPoint(80, 60))
        self.move(360, 240, Qt.NoButton)
        # Native QtTest moves beyond a widget can be delivered without the
        # platform window's separate leave notification. Send that actual Qt
        # boundary event explicitly rather than call any QML handler.
        QApplication.sendEvent(self.host, QEvent(QEvent.Leave))
        pump()
        presses = [call for call in self.bridge.calls if call[0] == 'press']
        self.assertTrue(any(call[6] for call in presses), presses)
        self.assertTrue(any(call[5] & Qt.AltModifier.value for call in presses))
        self.assertTrue(any(call[0] == 'enter' for call in self.bridge.calls))
        self.assertTrue(any(call[0] == 'leave' for call in self.bridge.calls))


if __name__ == '__main__':
    unittest.main()
