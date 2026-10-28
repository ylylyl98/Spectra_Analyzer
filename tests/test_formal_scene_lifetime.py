"""Real formal scene teardown, isolated from QWidget application test runners."""
import gc
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from shiboken6 import isValid

from spectra_app.formal_preview_controller import FormalPreviewController
from spectra_app.formal_preview_scene import FormalPreviewScene
from spectra_app.plot_presentation import PlotAnnotation, PlotPresentation
from tests.test_formal_preview_controller import OWNER, page

_CHILD = os.environ.get('SPECTRA_FORMAL_LIFETIME_QGUI_CHILD') == '1'
QML = Path(__file__).resolve().parents[1] / 'spectra_app/qml/FormalPreview.qml'


def deferred_delete(*objects):
    for obj in objects:
        if isValid(obj):
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QGuiApplication.processEvents()


class FormalSceneLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        assert type(cls.app) is QGuiApplication

    def controller(self, color='red'):
        controller = FormalPreviewController()
        controller.set_geometry(SimpleNamespace(owner_token=OWNER, title='',
            x_scale='linear', y_scale='linear'), PlotPresentation(width_in=6., height_in=4.,
                annotations=(PlotAnnotation('note', text='Retained note', position=(.3, .5)),)))
        self.assertTrue(controller.set_page(page(color)))
        return controller

    def mount(self, controller, engine):
        window = QQuickWindow()
        window.resize(400, 300)
        window.show()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML)))
        self.assertEqual(component.status(), QQmlComponent.Ready,
                         '\n'.join(error.toString() for error in component.errors()))
        scene = FormalPreviewScene(controller, engine)
        root = component.createWithInitialProperties(
            {'bridge': controller, 'attachmentGeneration': scene.generation})
        self.assertIsNotNone(root)
        root.setParentItem(window.contentItem())
        root.setWidth(400)
        root.setHeight(300)
        scene.bind_item(root)
        self.app.processEvents()
        self.assertTrue(controller.canView)
        return scene, root, component, window

    def test_minimal_engine_first_gc_reproduction(self):
        # Preserve the production probe's ordering: engine, controller dispose,
        # item/window deferred deletion, then reference release and collection.
        engine = QQmlEngine()
        window = QQuickWindow()
        window.resize(300, 200)
        window.show()
        controller = FormalPreviewController()
        scene = FormalPreviewScene(controller, engine)
        item = QQuickItem(window.contentItem())
        item.setWidth(300)
        item.setHeight(200)
        scene.bind_item(item)
        self.app.processEvents()
        print('minimal: before engine', flush=True)
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        controller.dispose()
        item.deleteLater()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        print('minimal: before GC', flush=True)
        del controller, scene, item, window, engine
        gc.collect()
        print('minimal: after GC', flush=True)

    def test_engine_first_qml_retires_input_and_provider_and_can_remount(self):
        class RecordingEngine(QQmlEngine):
            def removeImageProvider(self, route):
                removed.append(route)
                super().removeImageProvider(route)

        removed = []
        engine = RecordingEngine()
        controller = self.controller()
        scene, root, component, window = self.mount(controller, engine)
        controller.select_annotation('note')
        controller.set_zoom('150%')
        accepted = controller.svg_bytes
        generation = scene.generation
        deferred_delete(engine)
        self.assertTrue(scene._retired)
        self.assertEqual(removed, [])  # Engine owns provider destruction.
        self.assertFalse(controller._disposed)
        self.assertFalse(controller.canView)
        self.assertFalse(controller.canEdit)
        self.assertEqual(controller.imageUrl, '')
        self.assertIsNone(controller._publish_image)
        self.assertIsNone(controller._retire_image)
        root.setVisible(False)
        root.setVisible(True)
        root.setWidth(390)
        scene.report_hybrid(True, True)
        scene.refresh()
        self.assertEqual(controller.scenePress(generation, 10, 10), -1)
        self.assertTrue(controller.set_page(page()))
        self.assertEqual(controller.imageUrl, '')
        self.assertEqual((controller.svg_bytes, controller.selected_id, controller.zoom),
                         (accepted, 'note', '150%'))
        replacement_engine = QQmlEngine()
        replacement, new_root, new_component, new_window = self.mount(controller, replacement_engine)
        self.assertNotEqual(scene.route, replacement.route)
        scene.detach()
        self.assertTrue(controller.canEdit)
        self.assertIs(replacement_engine.imageProvider(replacement.route), replacement.provider)
        self.assertEqual((controller.svg_bytes, controller.selected_id, controller.zoom),
                         (accepted, 'note', '150%'))
        controller.dispose()
        deferred_delete(root, component, window, new_root, new_component, new_window,
                        scene, replacement, controller, replacement_engine)
        del scene, root, component, window, replacement, new_root, new_component, new_window
        del controller, engine, replacement_engine
        gc.collect()
        print('production QML: engine first, remount and GC complete', flush=True)

    def test_scene_before_controller_and_other_scene_remains_live(self):
        engine = QQmlEngine()
        first, second = self.controller(), self.controller('blue')
        scene, root, component, window = self.mount(first, engine)
        other, other_root, other_component, other_window = self.mount(second, engine)
        deferred_delete(root)  # The real presentation dies before its owner.
        self.assertTrue(scene._retired)
        self.assertIsNone(engine.imageProvider(scene.route))
        self.assertFalse(first._disposed)
        self.assertEqual(first.svg_bytes, page().svg_bytes)
        deferred_delete(scene, first)
        self.assertTrue(second.canEdit)
        self.assertIs(engine.imageProvider(other.route), other.provider)
        second.dispose()
        deferred_delete(other_root, other, second, component, other_component, window, other_window, engine)
        del scene, root, first, second, other, other_root, component, other_component, window, other_window, engine
        gc.collect()
        print('production QML: scene first and GC complete', flush=True)

    def test_controller_before_retained_qml_scene(self):
        engine = QQmlEngine()
        controller = self.controller()
        scene, root, component, window = self.mount(controller, engine)
        deferred_delete(controller)
        self.assertTrue(scene._retired)
        self.assertIsNone(engine.imageProvider(scene.route))
        # Later item events and repeated detach cannot enter the deleted owner.
        root.setVisible(False)
        root.setWidth(380)
        scene.refresh()
        scene.detach()
        deferred_delete(root, scene, component, window, engine)
        del scene, root, component, window, engine, controller
        gc.collect()
        print('production QML: controller first and GC complete', flush=True)


class FormalSceneLifetimeSubprocessTests(unittest.TestCase):
    def test_real_qgui_teardown_exits_successfully(self):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-X', 'faulthandler', '-m', 'unittest',
             'tests.test_formal_scene_lifetime', '-v'],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', SPECTRA_FORMAL_LIFETIME_QGUI_CHILD='1'),
            capture_output=True, text=True, encoding='utf-8', timeout=90)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('minimal: after GC', result.stdout)
        self.assertIn('Ran 4 tests', result.stderr)
        self.assertIn('OK', result.stderr)


def load_tests(loader, tests, pattern):
    case = FormalSceneLifetimeTests if _CHILD else FormalSceneLifetimeSubprocessTests
    return loader.loadTestsFromTestCase(case)


if __name__ == '__main__':
    unittest.main()
