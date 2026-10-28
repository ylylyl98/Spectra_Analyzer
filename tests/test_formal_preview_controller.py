"""Host-free controller and actual same-engine production QML scene proofs.

Run this module in its own process: it deliberately creates QGuiApplication,
without constructing or importing a QWidget host or QApplication.
"""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QPoint, QPointF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from spectra_app.formal_preview_controller import FormalPreviewController
from spectra_app.formal_preview_scene import FormalPreviewScene
from spectra_app.plot_presentation import PlotAnnotation, PlotPresentation
from spectra_app.svg_export import RenderedPage

_CHILD = os.environ.get('SPECTRA_FORMAL_PREVIEW_QGUI_CHILD') == '1'
APP = (QGuiApplication.instance() or QGuiApplication([])) if _CHILD else None
OWNER = ('identical-owner', 1, 2, 3, 4)
QML = Path(__file__).resolve().parents[1] / 'spectra_app/qml/FormalPreview.qml'
CAPTURE = Path(os.environ.get('SPECTRA_FORMAL_SCENE_CAPTURE', '')) if os.environ.get('SPECTRA_FORMAL_SCENE_CAPTURE') else None


def pump(ms=25):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def page(color='red', owner=OWNER):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="6in" height="4in" viewBox="0 0 576 384">'
           f'<rect width="576" height="384" fill="{color}"/></svg>').encode()
    return RenderedPage(svg_bytes=svg, digest=hashlib.sha256(svg).hexdigest(), owner_token=owner,
        figure_size_in=(6., 4.), axes_bounds=(.1, .1, .8, .8), plot_size_in=(4.8, 3.2),
        colorbar_bounds={}, colorbar_tick_positions={}, display_x_limits=(1., 100.),
        display_y_limits=(10., 1000.), nonpositive_count=0, under_count=0, over_count=0,
        missing_count=0, valid_count=4, header_line_count=1, heatmap_dpi=150, font_family='Arial')


class FormalPreviewControllerTests(unittest.TestCase):
    def setUp(self):
        self.engine = QQmlEngine()
        self.window = QQuickWindow()
        self.window.resize(660, 260)
        self.controllers = []
        self.attachments = []
        self.items = []
        self.note = PlotAnnotation('note', text='Note', position=(.3, .5))
        self.presentation = PlotPresentation(width_in=6., height_in=4., annotations=(self.note,))
        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(QML)))
        self.assertEqual(self.component.status(), QQmlComponent.Ready,
                         '\n'.join(e.toString() for e in self.component.errors()))
        self.window.show()
        pump()

    def tearDown(self):
        for controller in self.controllers:
            controller.dispose()
        for item in self.items:
            item.setParentItem(None)
            item.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.component.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def controller(self, color='red', **kwargs):
        controller = FormalPreviewController(parent=QObject(self.engine), **kwargs)
        self.controllers.append(controller)
        controller.set_geometry(SimpleNamespace(owner_token=OWNER, title='', x_scale='linear', y_scale='linear'),
                                self.presentation)
        self.assertTrue(controller.set_page(page(color)))
        return controller

    def mount(self, controller, x=0, parent=None):
        attachment = FormalPreviewScene(controller, self.engine)
        root = self.component.createWithInitialProperties(
            {'bridge': controller, 'attachmentGeneration': attachment.generation})
        self.assertIsNotNone(root, '\n'.join(e.toString() for e in self.component.errors()))
        root.setParentItem(parent or self.window.contentItem())
        root.setWidth(320)
        root.setHeight(240)
        root.setX(x)
        attachment.bind_item(root)
        self.attachments.append(attachment)
        self.items.append(root)
        pump()
        return attachment, root

    def item(self, root, name):
        return root.findChild(QQuickItem, name)

    def point(self, controller, root):
        return self.item(root, 'formalPaper').mapToScene(QPointF(controller.annotation_point(self.note))).toPoint()

    def color_at(self, root):
        capture = self.window.grabWindow()
        self.assertFalse(capture.isNull())
        point = root.mapToScene(QPointF(root.width()/2, root.height()/2))
        ratio = self.window.devicePixelRatio()
        return capture.pixelColor(round(point.x()*ratio), round(point.y()*ratio))

    def test_controller_has_no_widget_host_accepts_without_scene_and_permanent_disposal(self):
        controller = self.controller()
        self.assertIs(type(APP), QGuiApplication)
        self.assertIsInstance(controller.parent(), QObject)
        for name in ('host', 'isVisible', 'isEnabled', 'window', 'rootObject', 'setSource', 'addAction', 'update'):
            self.assertFalse(hasattr(controller, name), name)
        self.assertEqual(controller.svg_bytes, page().svg_bytes)
        self.assertFalse(controller.canView)
        self.assertFalse(controller.canEdit)
        self.assertFalse(controller.press(1, 1))
        self.assertFalse(controller.set_page(page('blue', ('other',))))
        controller.select_annotation('note')
        self.assertEqual(controller._selected_id, 'note')
        controller.set_zoom('200%')
        self.assertEqual(controller.zoom, '200%')
        self.assertEqual(controller.imageUrl, '')
        controller.dispose()
        self.assertFalse(controller.set_page(None))
        self.assertFalse(controller.key(Qt.Key_C.value, Qt.ControlModifier.value))
        with self.assertRaisesRegex(RuntimeError, 'disposed'):
            FormalPreviewScene(controller, self.engine)

    def test_two_production_items_share_engine_with_independent_pixels_routes_and_input(self):
        a, b = self.controller('red'), self.controller('blue')
        aa, ar = self.mount(a)
        ba, br = self.mount(b, 330)
        self.assertEqual(a.expected_owner, b.expected_owner)
        self.assertEqual(aa.provider._revision, ba.provider._revision)
        self.assertNotEqual(aa.route, ba.route)
        self.assertNotEqual(a.imageUrl, b.imageUrl)
        self.assertIs(ar.property('bridge'), a)
        self.assertIs(br.property('bridge'), b)
        self.assertIsNone(self.engine.rootContext().contextProperty('appBridge'))
        self.assertEqual(a._viewport_size, (ar.width(), ar.height()))
        self.assertEqual(a._device_pixel_ratio, self.window.devicePixelRatio())
        self.assertEqual(self.color_at(ar).name(), '#ff0000')
        self.assertEqual(self.color_at(br).name(), '#0000ff')
        if CAPTURE:
            CAPTURE.mkdir(parents=True, exist_ok=True)
            self.assertTrue(self.window.grabWindow().save(str(CAPTURE/'same-engine-red-blue.png')))
        moved_a, moved_b, copy_a, copy_b, selected_a, selected_b = [], [], [], [], [], []
        a.annotationMoved.connect(moved_a.append)
        b.annotationMoved.connect(moved_b.append)
        a.graphCopyRequested.connect(lambda: copy_a.append(True))
        b.graphCopyRequested.connect(lambda: copy_b.append(True))
        a.annotationSelected.connect(selected_a.append)
        b.annotationSelected.connect(selected_b.append)
        for c, root in ((a, ar), (b, br)):
            point = self.point(c, root)
            QTest.mouseClick(self.window, Qt.LeftButton, pos=point)
            QTest.keyClick(self.window, Qt.Key_Right)
            QTest.keyClick(self.window, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual((len(moved_a), len(moved_b)), (1, 1))
        self.assertEqual((copy_a, copy_b), ([True], [True]))
        self.assertEqual((selected_a, selected_b), (['note'], ['note']))
        b_url, b_revision = b.imageUrl, ba.provider._revision
        a.set_page(page('green'))
        pump()
        self.assertEqual(self.color_at(ar).name(), '#008000')
        self.assertEqual(self.color_at(br).name(), '#0000ff')
        self.assertEqual((b.imageUrl, ba.provider._revision), (b_url, b_revision))
        self.assertEqual(len(aa.provider._revisions), 2)
        a.set_page(page('yellow'))
        self.assertEqual(len(aa.provider._revisions), 2)
        self.assertTrue(aa.provider.requestImage('1', QSize(), QSize(20, 20)).isNull())
        a.dispose()
        pump()
        self.assertIsNone(self.engine.imageProvider(aa.route))
        self.assertEqual(aa.provider.accept(page('red').svg_bytes), '')
        self.assertTrue(aa.provider.requestImage('2', QSize(), QSize(20, 20)).isNull())
        self.assertFalse(a.sceneKey(aa.generation, Qt.Key_C.value, Qt.ControlModifier.value))
        self.assertEqual(self.color_at(br).name(), '#0000ff')
        QTest.mouseClick(self.window, Qt.LeftButton, pos=self.point(b, br))
        QTest.keyClick(self.window, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(copy_b, [True, True])
        self.assertIsNotNone(self.engine.imageProvider(ba.route))

    def test_detach_recreate_retains_bytes_selection_zoom_scroll_and_rejects_old_scene(self):
        controller = self.controller()
        controller.set_zoom('200%')
        aa, ar = self.mount(controller)
        viewport = self.item(ar, 'formalViewport')
        viewport.setProperty('contentX', 70.)
        viewport.setProperty('contentY', 55.)
        controller.select_annotation('note')
        point = controller.annotation_point(self.note)
        gesture = controller.scenePress(aa.generation, point.x(), point.y())
        self.assertGreaterEqual(gesture, 0)
        moved = []
        controller.annotationMoved.connect(moved.append)
        saved_bytes, saved_size = controller.svg_bytes, controller.paper_display_size()
        aa.detach()
        self.assertIsNone(self.engine.imageProvider(aa.route))
        self.assertFalse(controller.canView)
        self.assertEqual(controller.svg_bytes, saved_bytes)
        ar.setParentItem(None)
        # A detached old QML item stays alive while a fresh one is mounted.
        ab, br = self.mount(controller)
        self.assertNotEqual(aa.route, ab.route)
        self.assertNotEqual(aa.generation, ab.generation)
        self.assertEqual(controller.svg_bytes, saved_bytes)
        self.assertEqual(controller._selected_id, 'note')
        self.assertEqual(controller.zoom, '200%')
        vp = self.item(br, 'formalViewport')
        self.assertEqual((vp.property('contentX'), vp.property('contentY')), (70., 55.))
        self.assertEqual(self.color_at(br).name(), '#ff0000')
        controller.sceneRelease(aa.generation, gesture, point.x()+20, point.y()+20)
        controller.sceneDisplayGeometry(aa.generation, 1, 1, 1)
        controller.saveScroll(aa.generation, 0, 0)
        self.assertFalse(controller.sceneKey(aa.generation, Qt.Key_Delete.value, 0))
        self.assertEqual(moved, [])
        self.assertEqual(controller.paper_display_size(), saved_size)
        self.assertEqual(controller._scroll, (70., 55.))
        # The fresh scene's real input still works after the retired release.
        point = controller.annotation_point(self.note)
        vp.setProperty('contentX', max(0., point.x()-160))
        vp.setProperty('contentY', max(0., point.y()-120))
        pump()
        target = self.point(controller, br)
        QTest.mouseClick(self.window, Qt.LeftButton, pos=target)
        QTest.keyClick(self.window, Qt.Key_Right)
        self.assertEqual(len(moved), 1)

    def test_actual_item_ancestor_disabled_hidden_and_window_detach_retire_late_release(self):
        controller = self.controller()
        ancestor = QQuickItem(self.window.contentItem())
        ancestor.setWidth(320)
        ancestor.setHeight(240)
        aa, ar = self.mount(controller, parent=ancestor)
        moved = []
        controller.annotationMoved.connect(moved.append)
        for condition in ('item-hide', 'ancestor-hide', 'item-disable', 'ancestor-disable', 'window-detach', 'window-hide'):
            with self.subTest(condition=condition):
                point = controller.annotation_point(self.note)
                generation = controller.scenePress(aa.generation, point.x(), point.y())
                self.assertGreaterEqual(generation, 0)
                controller.sceneMove(aa.generation, generation, point.x()+15, point.y()+10)
                if condition == 'item-hide': ar.setVisible(False)
                elif condition == 'ancestor-hide': ancestor.setVisible(False)
                elif condition == 'item-disable': ar.setEnabled(False)
                elif condition == 'ancestor-disable': ancestor.setEnabled(False)
                elif condition == 'window-detach': ancestor.setParentItem(None)
                else: self.window.hide()
                self.assertFalse(controller.canEdit)
                self.assertFalse(controller.canView)
                # Reappearance cannot revive the original gesture.
                ar.setVisible(True); ancestor.setVisible(True)
                ar.setEnabled(True); ancestor.setEnabled(True)
                ancestor.setParentItem(self.window.contentItem())
                self.window.show()
                pump()
                self.assertTrue(controller.canEdit)
                controller.sceneRelease(aa.generation, generation, point.x()+30, point.y()+20)
                self.assertEqual(moved, [])
        ancestor.deleteLater()

    def test_destroyed_real_scene_retires_only_attachment_and_can_republish_retained_bytes(self):
        controller = self.controller()
        attachment, root = self.mount(controller)
        accepted_bytes = controller.svg_bytes
        self.items.remove(root)
        root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.assertTrue(attachment._retired)
        self.assertIsNone(self.engine.imageProvider(attachment.route))
        self.assertFalse(controller._disposed)
        self.assertFalse(controller.canView)
        self.assertEqual(controller.svg_bytes, accepted_bytes)
        replacement, item = self.mount(controller)
        self.assertNotEqual(replacement.route, attachment.route)
        self.assertTrue(controller.canView)
        self.assertEqual(self.color_at(item).name(), '#ff0000')

    def test_guard_replacement_and_authorization_reentrancy_retire_pending_input(self):
        controller = self.controller()
        aa, root = self.mount(controller)
        moved = []
        controller.annotationMoved.connect(moved.append)
        point = controller._point(self.note)
        generation = controller.scenePress(aa.generation, point.x(), point.y())
        controller.sceneRelease(aa.generation, generation, point.x()+.49, point.y()+.49)
        self.assertEqual(moved, [], 'Subpixel scene alignment must not begin a drag')
        self.assertEqual(controller.presentation, self.presentation)
        generation = controller.scenePress(aa.generation, point.x(), point.y())
        controller.sceneMove(aa.generation, generation, point.x()+2.25, point.y()+1.5)
        controller.sceneRelease(aa.generation, generation, point.x()+2.375, point.y()+1.625)
        self.assertEqual(len(moved), 1)
        delta = controller._point(moved[0]) - point
        self.assertAlmostEqual(delta.x(), 2.375)
        self.assertAlmostEqual(delta.y(), 1.625)
        moved.clear()
        generation = controller.scenePress(aa.generation, point.x(), point.y())
        controller.sceneMove(aa.generation, generation, point.x()+10, point.y()+10)
        controller.set_mutation_guard(lambda: True)
        controller.sceneRelease(aa.generation, generation, point.x()+20, point.y()+20)
        self.assertEqual(moved, [])
        def changes_owner():
            controller.invalidate(('replaced',))
            return True
        controller.set_mutation_guard(changes_owner)
        self.assertFalse(controller.sceneKey(aa.generation, Qt.Key_Right.value, 0))
        self.assertEqual(moved, [])


class FormalPreviewControllerTestsSubprocessTests(unittest.TestCase):
    def test_isolated_qgui_production_controller_and_scenes(self):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-m', 'unittest', 'tests.test_formal_preview_controller', '-v'],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', SPECTRA_FORMAL_PREVIEW_QGUI_CHILD='1'),
            capture_output=True, text=True, encoding='utf-8', timeout=90)
        # Preserve complete child evidence in the invoking test runner's log.
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Ran 6 tests', result.stderr)
        self.assertIn('OK', result.stderr)


def load_tests(loader, tests, pattern):
    case = FormalPreviewControllerTests if _CHILD else FormalPreviewControllerTestsSubprocessTests
    return loader.loadTestsFromTestCase(case)


if __name__ == '__main__':
    unittest.main()
