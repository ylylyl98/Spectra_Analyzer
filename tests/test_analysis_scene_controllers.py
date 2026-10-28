"""Import-safe QGui-only subprocess proof for DAT and linecut attachments."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from matplotlib.figure import Figure
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QPoint, QPointF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QWidget
from PySide6.QtTest import QTest
from shiboken6 import isValid

from spectra_app.analysis_canvas import AnalysisFigureCanvas, CanvasPointerContext, PeakMenuRequest
from spectra_app.dat_canvas_controller import CanvasServices, DatCanvasController
from spectra_app.analysis_canvas_scene import AnalysisCanvasScene
from spectra_app.analysis_linecut_controller import AnalysisLinecutController
from spectra_app.analysis_linecut_scene import AnalysisLinecutScene

CHILD = os.environ.get('SPECTRA_DAT_SCENE_CHILD') == '1'
APP = (QGuiApplication.instance() or QGuiApplication([])) if CHILD else None


def pump(ms=35):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@unittest.skipUnless(CHILD, 'QGuiApplication-only child')
class SceneControllerTests(unittest.TestCase):
    def setUp(self):
        self.trap = patch.object(QWidget, '__init__', side_effect=AssertionError('QWidget constructed'))
        self.trap.start()
        self.engine = QQmlEngine()
        self.window = QQuickWindow()
        self.window.resize(680, 500)
        self.window.show()
        self.controllers, self.scenes, self.items = [], [], []
        qml = Path(__file__).resolve().parents[1] / 'spectra_app/qml'
        self.components = {linecut: QQmlComponent(self.engine,
            QUrl.fromLocalFile(str(qml / ('AnalysisLinecut.qml' if linecut else 'AnalysisCanvas.qml'))))
            for linecut in (False, True)}
        for component in self.components.values():
            self.assertEqual(component.status(), QQmlComponent.Ready,
                             '\n'.join(e.toString() for e in component.errors()))
        pump()

    def tearDown(self):
        for controller in self.controllers:
            if isValid(controller):
                controller.dispose()
        for item in self.items:
            if isValid(item):
                item.setParentItem(None)
                item.deleteLater()
        if isValid(self.window):
            self.window.close()
            self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        if isValid(self.engine):
            for component in self.components.values():
                component.deleteLater()
            self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.trap.stop()

    def controller(self, color, linecut=False):
        figure = Figure(figsize=(3., 2.), facecolor=color)
        controller = (AnalysisLinecutController(figure, QObject(self.engine)) if linecut else
                      DatCanvasController(AnalysisFigureCanvas(figure), parent=QObject(self.engine)))
        self.controllers.append(controller)
        return controller

    def mount(self, controller, linecut=False):
        scene = (AnalysisLinecutScene if linecut else AnalysisCanvasScene)(controller, self.engine)
        component = self.components[linecut]
        item = component.createWithInitialProperties({
            'preview' if linecut else 'bridge': controller,
            'attachmentGeneration': scene.generation})
        self.assertIsNotNone(item, '\n'.join(e.toString() for e in component.errors()))
        item.setParentItem(self.window.contentItem())
        item.setX(330 * (len(self.scenes) % 2))
        item.setY(230 if linecut else 0)
        item.setWidth(300)
        item.setHeight(200)
        scene.bind_item(item)
        self.scenes.append(scene)
        self.items.append(item)
        pump()
        return scene, item

    def color_at(self, root):
        capture = self.window.grabWindow()
        self.assertFalse(capture.isNull())
        point = root.mapToScene(QPointF(root.width() / 2, root.height() / 2))
        ratio = self.window.devicePixelRatio()
        return capture.pixelColor(round(point.x() * ratio), round(point.y() * ratio)).name()

    def test_two_dat_two_linecut_distinct_immutable_routes_and_retained_remount(self):
        self.assertIs(type(APP), QGuiApplication)
        routes = []
        for linecut in (False, True):
            pairs = [self.mount(self.controller(color, linecut), linecut) for color in ('red', 'blue')]
            a, b = pairs[0][0], pairs[1][0]
            ca, cb = a.controller, b.controller
            routes.extend((a.route, b.route))
            self.assertTrue(ca.imageUrl)
            self.assertNotEqual(ca.imageUrl, cb.imageUrl)
            self.assertEqual(a.provider._revision if linecut else a.provider.revision,
                             b.provider._revision if linecut else b.provider.revision)
            self.assertEqual(self.color_at(pairs[0][1]), '#ff0000')
            self.assertEqual(self.color_at(pairs[1][1]), '#0000ff')
            self.assertIsNone(self.engine.rootContext().contextProperty('appBridge'))
            images = a.provider._images if linecut else a.provider.images
            revision = str(a.provider._revision if linecut else a.provider.revision)
            first = a.provider.requestImage(revision, QSize(), QSize())
            self.assertEqual(first.pixelColor(0, 0).name(), '#ff0000')
            first_bytes = bytes(first.constBits())
            ca.figure.set_facecolor('green') if linecut else ca.canvas.figure.set_facecolor('green')
            ca.schedule_render() if linecut else ca.canvas.draw_idle()
            pump()
            self.assertEqual(bytes(first.constBits()), first_bytes)
            self.assertEqual(bytes(a.provider.requestImage(revision, QSize(), QSize()).constBits()), first_bytes)
            old_generation = a.generation
            a.detach()
            self.assertFalse(ca.reportViewport(old_generation, 999, 999))
            replacement, root = self.mount(ca, linecut)
            self.assertNotEqual(replacement.route, a.route)
            self.assertTrue(ca.imageUrl.startswith('image://' + replacement.route))
            self.assertIsNotNone(self.engine.imageProvider(b.route))
            self.assertEqual(root.width(), 300)
        self.assertEqual(len(set(routes)), 4)

    def test_real_effective_visibility_enabled_window_and_stale_reports(self):
        c = self.controller('red')
        scene, item = self.mount(c)
        parent = QQuickItem(self.window.contentItem())
        self.items.append(parent)
        item.setParentItem(parent)
        pump()
        owner = c.ownerToken
        self.assertTrue(c.pointerEvent('press', 10, 10, 1, 1, 0, False, owner))
        item.setParentItem(self.window.contentItem())
        pump()
        self.assertFalse(c.pointerEvent('release', 10, 10, 1, 0, 0, False, owner))
        item.setParentItem(parent)
        owner = c.ownerToken
        self.assertTrue(c.pointerEvent('press', 10, 10, 1, 1, 0, False, owner))
        parent.setEnabled(False)
        pump()
        self.assertFalse(c.allowed())
        self.assertFalse(c.pointerEvent('release', 10, 10, 1, 0, 0, False, owner))
        parent.setEnabled(True)
        parent.setVisible(False)
        pump()
        self.assertFalse(c.visible)
        parent.setVisible(True)
        item.setWidth(345)
        pump()
        self.assertEqual(c.viewportWidth, 345)
        item.setParentItem(None)
        pump()
        self.assertFalse(c.allowed())
        item.setParentItem(self.window.contentItem())
        pump()
        self.assertTrue(c.allowed())
        old_generation = scene.generation
        replacement, _ = self.mount(c)
        self.assertFalse(c.reportViewport(old_generation, 900, 900))
        self.assertEqual(c.viewportWidth, 300)
        replacement.detach()

    def test_linecut_dpr_hidden_coalescing_and_disposal(self):
        c = self.controller('red', True)
        scene, item = self.mount(c, True)
        with patch.object(c._canvas, 'draw', wraps=c._canvas.draw) as draw:
            for _ in range(20):
                c.schedule_render()
            pump()
            self.assertEqual(draw.call_count, 1)
            item.setVisible(False)
            for _ in range(20):
                c.schedule_render()
            pump()
            self.assertEqual(draw.call_count, 1)
            c.update_scene(scene.generation, attached=True, visible=True, enabled=True,
                           viewport_size=(321, 123), device_pixel_ratio=2.)
            pump()
            self.assertEqual(c._last_frame.size(), QSize(642, 246))
            self.assertEqual(c._last_frame.devicePixelRatio(), 2.)
            c.update_scene(scene.generation, attached=True, visible=True, enabled=True,
                           viewport_size=(321, 123), device_pixel_ratio=1.5)
            pump()
            self.assertEqual(c._last_frame.size(), QSize(482, 184))
            c.schedule_render()
            c.dispose()
            c.dispose()
            pump()
            self.assertEqual(draw.call_count, 3)
            self.assertFalse(c._render_timer.isActive())

    def test_controller_and_window_deletion_in_both_orders(self):
        for linecut in (False, True):
            c = self.controller('red', linecut)
            scene, item = self.mount(c, linecut)
            c.schedule_render() if linecut else c.canvas.draw_idle()
            c.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertTrue(scene._retired)
            item.setWidth(420)
            pump()
        c = self.controller('red')
        scene, _ = self.mount(c)
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.assertFalse(c.visible)
        self.assertIsNone(c._gesture_owner)

    def test_engine_deletion_retires_provider_before_future_publication(self):
        # A presentation engine can die before an externally owned renderer.
        controllers = []
        for linecut in (False, True):
            c = self.controller('red', linecut)
            c.setParent(None)
            scene, _ = self.mount(c, linecut)
            controllers.append((c, scene, linecut))
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        for c, scene, linecut in controllers:
            self.assertTrue(scene._retired)
            self.assertIsNone(c._scene)
            c.schedule_render() if linecut else c.canvas.draw()
            self.assertEqual(c.imageUrl, '')
            c.dispose()

    def test_explicit_peak_services_preserve_typed_validation_and_one_shot_choice(self):
        calls = []
        accepted = [True]
        services = CanvasServices(validate_peak_menu=lambda request: accepted[0],
            continue_peak_menu=lambda request, key: calls.append((request, key)))
        c = DatCanvasController(AnalysisFigureCanvas(Figure()), services=services,
                                parent=QObject(self.engine))
        self.controllers.append(c)
        scene, root = self.mount(c)
        request = PeakMenuRequest(request_id='same-id',
            pointer=CanvasPointerContext(c.ownerToken, 10., 10.), session=object(), graph=object(),
            cube=object(), axes=object(), gate=0, energy_index=0, energy=1., label=0,
            override=None, epoch=0, targets=object(), target_indices=(0,))
        def right_menu(event):
            c.request_peak_menu(request)
        c.canvas.mpl_connect('button_press_event', right_menu)
        self.assertTrue(c.pointerEvent('press', 10, 10, 2, 2, 0, False, c.ownerToken))
        self.assertEqual(c.peakMenu, {})
        self.assertTrue(c.pointerEvent('release', 10, 10, 2, 0, 0, False, c.ownerToken))
        self.assertEqual(c.peakMenu['requestId'], request.request_id)
        self.assertFalse(c.choosePeakMenu('wrong-id', 'delete', c.ownerToken))
        self.assertTrue(c.choosePeakMenu(request.request_id, 'delete', c.ownerToken))
        self.assertEqual(calls, [(request, 'delete')])
        self.assertFalse(c.choosePeakMenu(request.request_id, 'delete', c.ownerToken))
        c.request_peak_menu(request)
        c._peak_menu_ready = True
        accepted[0] = False
        self.assertFalse(c.choosePeakMenu(request.request_id, 'delete', c.ownerToken))
        self.assertEqual(c.peakMenu, {})
        self.assertEqual(len(calls), 1)

    def test_image_requests_never_draw_and_retained_revisions_are_bounded(self):
        for linecut in (False, True):
            c = self.controller('red', linecut)
            scene, _ = self.mount(c, linecut)
            figure = c.figure if linecut else c.canvas.figure
            canvas = c._canvas if linecut else c.canvas
            provider = scene.provider
            first = provider.requestImage('1', QSize(), QSize())
            pixels = bytes(first.constBits())
            for color in ('green', 'blue', 'orange', 'purple'):
                figure.set_facecolor(color)
                c.schedule_render() if linecut else canvas.draw_idle()
                pump()
            revisions = provider._images if linecut else provider.images
            self.assertEqual(len(revisions), 2 if linecut else 3)
            self.assertTrue(provider.requestImage('1', QSize(), QSize()).isNull())
            self.assertEqual(bytes(first.constBits()), pixels)
            revision = str(provider._revision if linecut else provider.revision)
            with patch.object(canvas, 'draw', side_effect=AssertionError('Image request drew Figure')):
                stored = provider.requestImage(revision, QSize(), QSize(10, 10))
                self.assertEqual(stored.pixelColor(0, 0).name(), '#800080')
            c.dispose()
            self.assertEqual(provider._images if linecut else provider.images, {})

    def test_real_pointer_peak_menu_paired_key_release_focus_and_stale_root(self):
        c = self.controller('white')
        continued, keys, releases = [], [], []
        c.services = CanvasServices(validate_peak_menu=lambda request: True,
            continue_peak_menu=lambda request, key: continued.append(key))
        scene, root = self.mount(c)
        def capture_menu(event):
            if event.button.value != 3:
                return
            c.request_peak_menu(PeakMenuRequest(request_id=c.next_peak_menu_id(),
                pointer=event.guiEvent, session=object(), graph=object(), cube=object(),
                axes=event.inaxes, gate=0, energy_index=0, energy=1., label=7,
                override=None, epoch=0, targets=object(), target_indices=(0,)))
        c.canvas.mpl_connect('button_press_event', capture_menu)
        c.canvas.mpl_connect('button_release_event', releases.append)
        for name in ('key_press_event', 'key_release_event'):
            c.canvas.mpl_connect(name, lambda event: keys.append((event.name, event.key)))
        point = root.mapToScene(QPointF(70, 60)).toPoint()
        QTest.mouseClick(self.window, Qt.RightButton, pos=point)
        pump()
        menu = root.findChild(QObject, 'analysisPeakMenu')
        self.assertTrue(menu.property('visible'))
        QTest.keyClick(self.window, Qt.Key_Escape)
        pump()
        self.assertFalse(menu.property('visible'))
        self.assertEqual(keys, [])
        self.assertTrue(root.hasActiveFocus())
        QTest.keyClick(self.window, Qt.Key_A)
        self.assertEqual(keys, [('key_press_event', 'a'), ('key_release_event', 'a')])
        QTest.mouseClick(self.window, Qt.RightButton, pos=point)
        pump()
        delete = root.findChild(QQuickItem, 'analysisPeakDelete')
        QTest.mouseClick(self.window, Qt.LeftButton,
                         pos=delete.mapToScene(QPointF(delete.width()/2, delete.height()/2)).toPoint())
        pump()
        self.assertEqual(continued, ['delete'])
        self.assertEqual(c.peakMenu, {})
        QTest.mousePress(self.window, Qt.LeftButton, pos=point)
        before = len(releases)
        self.window.hide()
        pump()
        self.window.show()
        QTest.mouseRelease(self.window, Qt.LeftButton, pos=point)
        pump()
        self.assertEqual(len(releases), before)
        old_generation = scene.generation
        replacement, fresh = self.mount(c)
        self.assertFalse(root.isEnabled())
        root.setWidth(901)
        scene.refresh()
        self.assertFalse(c.reportViewport(old_generation, 902, 903))
        self.assertEqual(c.viewportWidth, fresh.width())
        with patch.object(self.window, 'devicePixelRatio', return_value=2.):
            QCoreApplication.sendEvent(self.window, QEvent(QEvent.DevicePixelRatioChange))
            pump()
            self.assertEqual(c.devicePixelRatio, 2.)
        replacement.refresh()


@unittest.skipIf(CHILD, 'parent wrapper')
class QGuiSubprocessTests(unittest.TestCase):
    def test_host_free_scene_process_exits_cleanly(self):
        env = dict(os.environ, SPECTRA_DAT_SCENE_CHILD='1', QT_QPA_PLATFORM='offscreen',
                   QSG_RHI_BACKEND='software')
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'unittest',
                                 'tests.test_analysis_scene_controllers', '-v'],
                                cwd=Path(__file__).resolve().parents[1], env=env,
                                text=True, capture_output=True, timeout=60)
        print(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('test_real_pointer_peak_menu_paired_key_release_focus_and_stale_root',
                      result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
