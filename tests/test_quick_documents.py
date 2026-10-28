"""Import-safe subprocess proof of the actual Quick workspace document scene."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class QuickDocumentsTests(unittest.TestCase):
    def test_real_qgui_documents(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--probe'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Real Quick document presenter: PASS', result.stdout)


def probe():
    import numpy as np
    from types import SimpleNamespace
    from unittest.mock import patch
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QPoint, QPointF, QRect, Qt, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget
    from spectra_app.dataset_state import Workspace
    from spectra_app.formal_plot_controller import FormalPlotController
    from spectra_app.quick_documents import QuickDocuments
    from spectra_app.workbook_page import WorkbookController
    from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
    from spectra_app.xy_plot_controller import XYPlotController
    from spectra_app.plot_presentation import PlotPresentation
    from tests.test_formal_preview_controller import page, OWNER
    import shiboken6

    global PROBE_APP
    PROBE_APP = QGuiApplication([])
    errors = []
    sys.excepthook = lambda *error: (errors.append(str(error[1])), sys.__excepthook__(*error))

    def pump(ms=40):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def flush():
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()

    def find(root, name):
        if root.objectName() == name:
            return root
        for child in root.childItems():
            result = find(child, name)
            if result is not None:
                return result
        return None

    class SceneTests(unittest.TestCase):
        def setUp(self):
            self.owner = QObject()
            self.engine = QQmlEngine()
            self.window = QQuickWindow()
            self.window.resize(1200, 820)
            self.window.show()
            self.events, self.denied, self.close_denied = [], set(), set()
            self.available = True

            def activate(key):
                self.events.append(('activate', key))
                return key not in self.denied

            def finish(key):
                self.events.append(('finish', key))
                return key not in self.close_denied

            self.documents = QuickDocuments(activate, finish, self.owner, input_eligible=lambda: self.available)
            # This scene fixture exercises free-frame geometry and gestures.
            self.documents.setMode('free')
            self.mount_errors = []
            self.documents.mountFailed.connect(self.mount_errors.append)
            self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(
                Path(__file__).resolve().parents[1] / 'spectra_app/qml/WorkspaceDocuments.qml')))
            self.assertTrue(self.component.isReady(), [error.toString() for error in self.component.errors()])
            self.root = self.component.createWithInitialProperties({'documents': self.documents})
            self.assertIsNotNone(self.root, [error.toString() for error in self.component.errors()])
            self.root.setParentItem(self.window.contentItem())
            self.root.setWidth(1200)
            self.root.setHeight(820)
            self.controllers = []
            pump()

        def tearDown(self):
            self.documents.detach()
            for controller in self.controllers:
                if shiboken6.isValid(controller):
                    controller.dispose()
            if shiboken6.isValid(self.root):
                self.root.setParentItem(None)
                self.root.deleteLater()
            if shiboken6.isValid(self.window):
                self.window.close()
                self.window.deleteLater()
            if shiboken6.isValid(self.component):
                self.component.deleteLater()
            flush()
            if shiboken6.isValid(self.engine):
                self.engine.deleteLater()
            flush()
            self.owner.deleteLater()
            flush()
            self.assertEqual(self.mount_errors, [])
            self.assertEqual(errors, [])

        def formal(self, identity, color='red'):
            controller = FormalPlotController(self.owner)
            controller.set_presentation(PlotPresentation(width_in=6., height_in=4.))
            controller.invalidate(OWNER)
            controller.preview.set_geometry(SimpleNamespace(owner_token=OWNER, title='',
                x_scale='linear', y_scale='linear', x_label='X', y_label='Y'), controller.presentation)
            self.assertTrue(controller.commit_rendered_page(page(color)))
            return self.cache(('dataset', identity), controller, identity)

        def cache(self, key, controller, title):
            self.controllers.append(controller)
            self.documents.cache_document(key, controller, title)
            self.assertIs(self.documents.show_document(key, title, user=False), controller)
            pump()
            self.assertIs(controller.parent(), self.owner)
            self.assertIsNotNone(self.documents._presentations[key].mount)
            return key, controller

        def frame(self, key):
            result = find(self.root, 'documentFrame_' + '_'.join(key))
            self.assertIsNotNone(result)
            return result

        def click(self, item):
            position = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            QTest.mouseClick(self.window, Qt.LeftButton, pos=position)
            pump()

        def button(self, name):
            item = find(self.root, name)
            self.assertIsNotNone(item, name)
            self.click(item)

        def resize(self, key, x, y, width, height):
            frame = self.frame(key)
            token = self.documents.captureFrameInput(list(key), frame)
            return self.documents.moveResize(list(key), frame, token, x, y, width, height)

        def maximize(self, key):
            frame = self.frame(key)
            token = self.documents.captureFrameInput(list(key), frame)
            return self.documents.toggleMaximize(list(key), frame, token)

        def test_geometry_real_frame_inputs_and_transactional_close(self):
            a, ca = self.formal('first')
            b, cb = self.formal('second', 'blue')
            d = self.documents
            self.assertTrue(self.resize(a, 20, 20, 480, 320))
            self.assertTrue(self.resize(b, 600, 100, 480, 320))
            # Every displayed free frame can activate before it is the owner.
            self.assertTrue(d.registry.authorize(d.registry.capture_input(a)))
            self.assertEqual(d.registry.active_key, a)
            title = find(self.frame(a), 'documentTitleBar')
            start = title.mapToScene(QPointF(70, 15)).toPoint()
            original = d.registry.normal_rectangle(a)
            QTest.mousePress(self.window, Qt.LeftButton, pos=start)
            QTest.mouseMove(self.window, start + QPoint(45, 38), delay=10)
            QTest.mouseRelease(self.window, Qt.LeftButton, pos=start + QPoint(45, 38))
            pump()
            self.assertEqual(d.registry.normal_rectangle(a).topLeft(), original.topLeft() + QPoint(45, 38))
            # A held gesture cannot acquire a fresh lease after presentation
            # reparenting or an application eligibility cycle in the same window.
            def reparent():
                temporary = QQuickItem(self.window.contentItem())
                self.root.setParentItem(temporary)
                self.root.setParentItem(self.window.contentItem())
                temporary.deleteLater()

            def retire_owner():
                self.available = False
                d.refresh_eligibility()
                self.available = True
                d.refresh_eligibility()

            for retire in (reparent, retire_owner):
                title = find(self.frame(a), 'documentTitleBar')
                frame, token = self.frame(a), d.captureFrameInput(list(a), self.frame(a))
                original = d.registry.normal_rectangle(a)
                start = title.mapToScene(QPointF(70, 15)).toPoint()
                QTest.mousePress(self.window, Qt.LeftButton, pos=start)
                retire()
                QTest.mouseMove(self.window, start + QPoint(22, 18), delay=10)
                QTest.mouseRelease(self.window, Qt.LeftButton, pos=start + QPoint(22, 18))
                pump()
                self.assertEqual(d.registry.normal_rectangle(a), original)
                self.assertFalse(d.moveResize(list(a), frame, token, 333, 222, 480, 320))
                self.assertFalse(d.toggleMaximize(list(a), frame, token))
                self.assertFalse(d.closeDocument(list(a), frame, token))
                start = title.mapToScene(QPointF(70, 15)).toPoint()
                QTest.mousePress(self.window, Qt.LeftButton, pos=start)
                QTest.mouseMove(self.window, start + QPoint(10, 8), delay=10)
                QTest.mouseRelease(self.window, Qt.LeftButton, pos=start + QPoint(10, 8))
                pump()
                self.assertEqual(d.registry.normal_rectangle(a).topLeft(), original.topLeft() + QPoint(10, 8))
            handle = find(self.frame(a), 'documentResizeHandle')
            start = handle.mapToScene(QPointF(7, 7)).toPoint()
            original = d.registry.normal_rectangle(a)
            QTest.mousePress(self.window, Qt.LeftButton, pos=start)
            QTest.mouseMove(self.window, start + QPoint(35, 25), delay=10)
            QTest.mouseRelease(self.window, Qt.LeftButton, pos=start + QPoint(35, 25))
            pump()
            normal = d.registry.normal_rectangle(a)
            self.assertEqual((normal.width(), normal.height()), (original.width() + 35, original.height() + 25))
            self.click(find(self.frame(a), 'documentMaximize'))
            self.assertTrue(d._presentations[a].maximized)
            self.assertEqual(d.registry.normal_rectangle(a), normal)
            self.button('documentsTabbed')
            self.assertEqual(d.mode, 'tabbed')
            self.assertEqual(d.registry.normal_rectangle(a), normal)
            tab = find(self.root, 'documentTab_' + '_'.join(b))
            old_tab_token = d.captureTabInput(list(b), tab)
            start = tab.mapToScene(QPointF(tab.width()/2, tab.height()/2)).toPoint()
            QTest.mousePress(self.window, Qt.LeftButton, pos=start)
            reparent()
            QTest.mouseRelease(self.window, Qt.LeftButton, pos=start)
            pump()
            self.assertEqual(d.registry.active_key, a)
            self.assertFalse(d.activateTab(list(b), tab, old_tab_token))
            self.button('documentTab_' + '_'.join(b))
            self.assertEqual(d.registry.active_key, b)
            self.assertFalse(d.registry.authorize(d.registry.capture_input(a)))
            self.button('documentsFree')
            self.assertEqual(d.display_rectangle(a), normal)
            self.assertTrue(d.requestActivate(list(a)))
            before = d.workspace_snapshot(d.keys())
            self.close_denied.add(a)
            self.assertFalse(d.close_document(a))
            self.assertEqual(d.workspace_snapshot(d.keys()), before)
            self.assertEqual(self.events[-1], ('finish', a))
            self.close_denied.clear()
            self.denied.add(b)
            self.assertFalse(d.close_document(a))
            self.assertEqual(d.workspace_snapshot(d.keys()), before)
            self.assertIs(d.document_for(a), ca)
            self.assertEqual(self.events[-2:], [('finish', a), ('activate', b)])
            self.denied.clear()
            old = d.registry.capture_input(a)
            self.assertTrue(d.close_document(a))
            self.assertEqual(d.registry.active_key, b)
            self.assertIs(d.document_for(a), ca)
            self.assertFalse(d.registry.authorize(old))
            self.assertIs(d.show_document(a, 'first'), ca)
            pump()
            self.assertEqual(d.registry.normal_rectangle(a), normal)
            self.button('documentsTile')
            self.assertEqual(d.mode, 'free')
            self.assertNotEqual(d.registry.normal_rectangle(a), d.registry.normal_rectangle(b))
            self.button('documentsCascade')
            self.assertEqual(d.registry.normal_rectangle(b).topLeft() - d.registry.normal_rectangle(a).topLeft(), QPoint(24, 24))

        def test_automatic_restore_scroll_and_normal_rectangles_stay_clean(self):
            key, controller = self.formal('saved')
            d = self.documents
            metadata = d.workspace_snapshot(d.keys())
            metadata['documents'][0]['rect'] = [4000, 3000, 500, 350]
            changes = []
            d.workspaceChanged.connect(lambda: changes.append(True))
            d.restore_workspace(metadata, lambda key: 'saved')
            pump(100)
            self.assertEqual(changes, [])
            self.assertEqual(d.registry.normal_rectangle(key), QRect(4000, 3000, 500, 350))
            adapted = d.display_rectangle(key)
            self.assertLess(adapted.x(), 1200)
            self.assertLess(adapted.y(), 820)
            self.root.setWidth(700)
            self.root.setHeight(500)
            pump()
            self.assertEqual(d.display_rectangle(key), adapted)
            self.assertEqual(changes, [])
            # Frame writeback is already in document coordinates when scrolled.
            viewport = find(self.root, 'workspaceDocumentViewport')
            self.resize(key, 1600, 900, 500, 350)
            viewport.setProperty('contentX', 900.)
            viewport.setProperty('contentY', 600.)
            pump()
            self.assertEqual(d._scroll, (900, 600))
            self.assertEqual(d.registry.normal_rectangle(key), QRect(1600, 900, 500, 350))
            self.assertTrue(self.maximize(key))
            self.assertEqual(d.display_rectangle(key).topLeft(), QPoint(900, 600))
            self.assertEqual(d.registry.normal_rectangle(key), QRect(1600, 900, 500, 350))

        def test_restore_open_document_with_null_rectangle_has_visible_clean_initial_layout(self):
            key, controller = self.formal('missing-geometry')
            d = self.documents
            metadata = d.workspace_snapshot(d.keys())
            metadata['documents'][0]['rect'] = None
            changes = []
            d.workspaceChanged.connect(lambda: changes.append(True))
            d.restore_workspace(metadata, lambda key: 'missing-geometry')
            pump(100)
            frame = self.frame(key)
            self.assertGreaterEqual(frame.width(), 300)
            self.assertGreaterEqual(frame.height(), 220)
            self.assertTrue(frame.isVisible())
            self.assertIsNotNone(d.content_item(key))
            self.assertTrue(d.registry.normal_rectangle(key).isValid())
            self.assertLess(d.display_rectangle(key).x(), self.root.width())
            self.assertLess(d.display_rectangle(key).y(), self.root.height())
            self.assertEqual(changes, [])

        def test_park_repeat_restore_revoked_input_and_disposal(self):
            a, ca = self.formal('retained')
            b, cb = self.formal('closed', 'blue')
            d = self.documents
            self.assertTrue(d.close_document(b))
            scientific = ca.rendered_page
            ca.set_zoom('150%')
            snapshot = d.workspace_snapshot(d.keys())
            lease = d.registry.capture_input(a)
            old_frame = self.frame(a)
            old_frame_token = d.captureFrameInput(list(a), old_frame)
            parked = d.park()
            self.assertEqual(d.keys(), ())
            self.assertFalse(d.registry.authorize(lease))
            candidate, candidate_controller = self.formal('candidate')
            self.assertIs(d.document_for(candidate), candidate_controller)
            d.discard(candidate)
            self.assertTrue(candidate_controller.retired)
            self.assertIsNone(d.document_for(candidate))
            for _ in range(2):
                d.restore(parked)
                pump()
                self.assertIs(d.document_for(a), ca)
                self.assertIs(d.document_for(b), cb)
                self.assertIs(ca.rendered_page, scientific)
                self.assertEqual(ca.zoom_text, '150%')
                self.assertEqual(d.workspace_snapshot(d.keys()), snapshot)
                self.assertFalse(d.registry.authorize(lease))
                self.assertFalse(d.closeDocument(list(a), old_frame, old_frame_token))
                self.assertFalse(d.toggleMaximize(list(a), old_frame, old_frame_token))
                self.assertIsNotNone(d._presentations[a].mount)
                lease = d.registry.capture_input(a)
            lease = d.registry.capture_input(a)
            d.registry.revoke_input(('missing', 'document'))
            self.assertTrue(d.registry.authorize(lease))
            d.registry.revoke_input()
            self.assertFalse(d.registry.authorize(lease))
            self.assertTrue(d.registry.authorize(d.registry.capture_input(a)))
            self.assertEqual(d.workspace_snapshot(d.keys()), snapshot)
            # Visibility and application view changes retire authority without
            # changing authoritative open/active/geometry metadata.
            for hide, show in ((lambda: self.window.hide(), lambda: self.window.show()),
                               (lambda: self.root.setEnabled(False), lambda: self.root.setEnabled(True)),
                               (lambda: setattr(self, 'available', False), lambda: setattr(self, 'available', True))):
                lease = d.registry.capture_input(a)
                hide(); d.refresh_eligibility(); pump()
                self.assertFalse(d.registry.authorize(lease))
                show(); d.refresh_eligibility(); pump()
                self.assertFalse(d.registry.authorize(lease))
                self.assertTrue(d.registry.authorize(d.registry.capture_input(a)))
                self.assertEqual(d.workspace_snapshot(d.keys()), snapshot)
            lease = d.registry.capture_input(a)
            temporary = QQuickItem(self.window.contentItem())
            self.root.setParentItem(temporary)
            pump()
            self.assertFalse(d.registry.authorize(lease))
            self.root.setParentItem(self.window.contentItem())
            temporary.deleteLater()
            pump()
            self.assertTrue(d.registry.authorize(d.registry.capture_input(a)))
            state = d.park()
            calls = []
            original = ca.dispose
            ca.dispose = lambda: (calls.append('dispose'), original())
            d.discard_parked(state)
            d.discard_parked(state)
            self.assertEqual(calls, ['dispose'])
            self.assertTrue(state.disposed)
            with self.assertRaises(ValueError):
                d.restore(state)

        def test_real_same_engine_formal_workbook_xy_and_scene_retirement(self):
            a, ca = self.formal('red')
            b, cb = self.formal('blue', 'blue')
            workspace = Workspace()
            book = create_workbook(workspace)
            sheet = register_xy_sheet(workspace, book.workbook_id, 'Source',
                np.column_stack((np.linspace(0, 10, 101), np.sin(np.linspace(0, 10, 101)))), ['X', 'Y'])
            comparison = create_comparison(workspace)
            comparison.presentation = None
            add_curve(workspace, comparison.comparison_id, sheet.sheet_id, 0, 1)
            c, cc = self.cache(('workbook', book.workbook_id, ''), WorkbookController(workspace, book.workbook_id, self.owner), 'Workbook')
            e, ce = self.cache(('xy', comparison.comparison_id, ''), XYPlotController(workspace, comparison.comparison_id, self.owner), 'XY')
            d = self.documents
            self.button('documentsTile')
            pump(200)
            self.assertIsNone(self.engine.rootContext().contextProperty('appBridge'))
            self.assertNotEqual(ca.preview.imageUrl, cb.preview.imageUrl)
            self.assertTrue(ca.preview.imageUrl.startswith('image://formal-page-'))
            self.assertTrue(ce.canvas_bridge.imageUrl.startswith('image://xy-canvas-'))
            for key, controller, bridge in ((a, ca, ca.preview), (b, cb, cb.preview), (c, cc, cc), (e, ce, ce.canvas_bridge)):
                self.assertIs(d._presentations[key].mount.item.property('bridge'), bridge)
                self.assertIs(controller.parent(), self.owner)
            xy_item = d.content_item(e)
            self.assertEqual((ce.canvas_bridge.viewportWidth, ce.canvas_bridge.viewportHeight),
                             (xy_item.width(), xy_item.height()))
            self.assertLessEqual(ce.canvas_bridge.contentHeight, xy_item.height())
            self.assertLessEqual(ce.canvas_bridge.contentWidth, xy_item.width())
            image = self.window.grabWindow()
            self.assertFalse(image.isNull())
            # Actual independently routed Formal raster colors reach the scene.
            for key, color in ((a, 'red'), (b, 'blue')):
                item = find(d._presentations[key].mount.item, 'formalViewport')
                point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
                pixel = image.pixelColor(point)
                self.assertGreater(pixel.red() if color == 'red' else pixel.blue(), 200)
                self.assertLess(pixel.blue() if color == 'red' else pixel.red(), 80)
            capture = os.environ.get('SPECTRA_QUICK_DOCUMENTS_CAPTURE')
            if capture:
                self.assertTrue(image.save(capture))
            sheet_page, data = cc.current_sheet, sheet.data
            self.assertTrue(sheet_page.editMetadata(0, 'long_name'))
            sheet_page.setEditorText('invalid\nname')
            progress, history = ce.analysis_controller.progress, ce._edit_history
            old_owner = ce.command_bridge.ownerToken
            state = d.park()
            for _ in range(2):
                d.restore(state)
                pump()
                self.assertIs(d.document_for(c), cc)
                self.assertIs(cc.current_sheet, sheet_page)
                self.assertEqual(sheet_page.editorText, 'invalid\nname')
                self.assertIs(sheet.data, data)
                self.assertIs(ce.analysis_controller.progress, progress)
                self.assertIs(ce._edit_history, history)
                self.assertFalse(ce.command_bridge.invoke('home', old_owner))
            with self.assertRaises(ValueError):
                cc.finish_editor()
            lease = d.registry.capture_input(a)
            old_route = ca.preview.imageUrl
            frame = self.frame(a)
            content = find(frame, 'documentContent')
            d.attachFrame(list(a), frame, content)
            pump()
            self.assertFalse(d.registry.authorize(lease))
            self.assertNotEqual(ca.preview.imageUrl, old_route)
            self.assertIs(ca.rendered_page, d.document_for(a).rendered_page)
            # Engine-first teardown retires presentation, preserving controllers.
            self.engine.deleteLater()
            flush()
            self.assertFalse(ca.retired)
            self.assertFalse(ce._disposed)
            self.assertFalse(d.registry.authorize(d.registry.capture_input(a)))

    with patch.object(QWidget, '__init__', side_effect=AssertionError('QWidget constructed')):
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SceneTests))
    if not result.wasSuccessful() or errors:
        raise SystemExit(1)
    print('Real Quick document presenter: PASS', flush=True)


if __name__ == '__main__':
    if '--probe' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        probe()
    else:
        unittest.main()
