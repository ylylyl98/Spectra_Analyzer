"""Read-only policy through real input to the shared loaded Quick scene."""
from dataclasses import replace
import math
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from spectra_app.formal_preview import FormalPreview
from spectra_app.plot_presentation import PlotAnnotation, PlotPresentation
from tests.test_formal_preview_quick import OWNER, SVG, page
from tests.workbook_quick_helpers import item, pump

APP = QApplication.instance() or QApplication([])


class FormalReadonlyPreviewTests(unittest.TestCase):
    def setUp(self):
        self.host = FormalPreview(display_policy='readonly')
        self.host.resize(640, 480)
        self.host.set_page(page())
        self.host.show()
        pump(35)
        self.assertEqual(self.host.status(), QQuickWidget.Ready)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def wheel(self, delta):
        host = self.host
        pos = QPointF(320, 240)
        event = QWheelEvent(pos, QPointF(host.mapToGlobal(pos.toPoint())), QPoint(), QPoint(0, delta),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(host, event)
        pump()
        self.assertTrue(event.isAccepted())

    def drag(self, start=QPoint(320, 240), delta=QPoint(31, -17)):
        QTest.mousePress(self.host, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.host, start + delta)
        QTest.mouseRelease(self.host, Qt.LeftButton, pos=start + delta)
        pump()

    def fit(self):
        QTest.mouseDClick(self.host, Qt.LeftButton, pos=QPoint(320, 240))
        QTest.mouseRelease(self.host, Qt.LeftButton, pos=QPoint(320, 240))
        pump()

    def test_wheel_relative_to_current_fit_clamps_and_doubleclick_resets_free_pan(self):
        host = self.host
        paper = item(host, 'formalPaper')
        self.assertAlmostEqual(paper.width(), 640.)
        self.wheel(-120)
        self.assertAlmostEqual(host.bridge.relativeZoom, 1 / 1.2)
        self.assertLess(paper.width(), host.width())
        origin = paper.position()
        self.drag()
        self.assertEqual(paper.position() - origin, QPointF(31, -17))
        self.wheel(60)
        self.assertAlmostEqual(host.bridge.relativeZoom, 1.2 ** -.5)
        host.resize(500, 360)
        pump()
        fit = min(500 / (6 * 96), 360 / (4 * 96))
        self.assertAlmostEqual(host.bridge._scale, fit * 1.2 ** -.5)
        self.wheel(24000)
        self.assertEqual(host.bridge.relativeZoom, 12.)
        self.assertAlmostEqual(paper.width(), 6 * 96 * fit * 12)
        request = host.image_provider._last_request
        self.assertLessEqual(max(request.width(), request.height()), 2048)
        self.assertLessEqual(request.width() * request.height(), 2048 ** 2)
        self.assertAlmostEqual(request.width() / request.height(), 1.5, delta=.01)
        source = paper.property('sourceSize')
        self.assertLess(source.width(), paper.width())
        self.assertEqual(request, QSize(round(source.width() * host.devicePixelRatioF()),
                                       round(source.height() * host.devicePixelRatioF())))
        self.wheel(-24000)
        self.assertEqual(host.bridge.relativeZoom, .25)
        self.fit()
        self.assertEqual(host.bridge.relativeZoom, 1.)
        self.assertEqual((host.bridge.panX, host.bridge.panY), (0., 0.))
        self.assertAlmostEqual(paper.x(), (host.width() - paper.width()) / 2)
        self.assertAlmostEqual(paper.y(), (host.height() - paper.height()) / 2)

    def test_exact_accepted_revision_physical_geometry_and_dpr_once_without_export_render(self):
        host = self.host
        self.assertEqual(host.findChildren(QWidget), [])
        provider, scene = host.image_provider, host.rootObject()
        changed = SVG.replace(b'#1c65bc', b'#bc651c')
        previous_url = item(host, 'formalPaper').property('source')
        self.assertTrue(host.set_page(replace(page(changed), figure_size_in=(8., 4.))))
        pump()
        self.assertNotEqual(item(host, 'formalPaper').property('source'), previous_url)
        self.assertEqual(host.svg_bytes, changed)
        with patch('spectra_app.formal_preview_controller.render_svg_image') as export_raster, \
                patch('spectra_app.svg_export.render_hybrid_svg') as scientific:
            host.resize(510, 320)
            self.wheel(120)
            self.drag()
            self.fit()
            pump()
        scientific.assert_not_called()
        export_raster.assert_not_called()
        self.assertIs(host.image_provider, provider)
        self.assertIs(host.rootObject(), scene)
        paper = item(host, 'formalPaper')
        self.assertAlmostEqual(paper.width() / paper.height(), 2.)
        source = paper.property('sourceSize')
        self.assertEqual(source, QSize(math.ceil(paper.width()), math.ceil(paper.height())))
        self.assertEqual(provider._last_request, QSize(round(source.width() * host.devicePixelRatioF()),
                                                      round(source.height() * host.devicePixelRatioF())))
        self.assertFalse(host.grabFramebuffer().isNull())
        self.assertEqual(host.svg_bytes, changed)

    def test_actual_and_direct_edit_entrypoints_never_emit_mutations_or_selection(self):
        host = self.host
        note = PlotAnnotation('note', text='Default note', position=(.15, .85))
        presentation = PlotPresentation(width_in=6., height_in=4., annotations=(note,))
        from types import SimpleNamespace
        host.set_geometry(SimpleNamespace(owner_token=OWNER, title='Editable measured title',
                                          x_scale='linear', y_scale='linear'), presentation)
        signals = []
        for name in ('annotationSelected', 'annotationMoved', 'annotationDeleteRequested', 'titleMoved',
                     'propertyEditRequested', 'graphCopyRequested'):
            getattr(host, name).connect(lambda *args, name=name: signals.append((name, args)))
        point = host.paper_to_viewport(host.annotation_point(note))
        self.drag(point, QPoint(20, 10))
        self.fit()
        for key in (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Right):
            QTest.keyClick(host, key)
        QTest.keyClick(host, Qt.Key_C, Qt.ControlModifier)
        host.selected_id = note.id
        host._title_selected = True
        self.assertFalse(host.bridge.press(100, 100))
        host.bridge.move(130, 130)
        host.bridge.release(130, 130)
        self.assertFalse(host.bridge.doubleClick(100, 100))
        for key, modifiers in ((Qt.Key_Delete, 0), (Qt.Key_Right, 0), (Qt.Key_C, Qt.ControlModifier.value)):
            self.assertFalse(host.bridge.key(key.value, modifiers))
        self.assertEqual(signals, [])
        self.assertIs(host.presentation, presentation)
        self.assertIsNone(host.selected_id)
        self.assertFalse(host._title_selected)
        self.assertEqual(host.bridge.overlay, {})
        self.assertEqual(host.svg_bytes, SVG)

    def test_owner_handoff_requires_explicit_invalidation_and_retires_old_drag(self):
        host = self.host
        new_owner = ('other-analysis', 2, 3, 5, 7)
        replacement = page(SVG.replace(b'#1c65bc', b'#bc651c'), new_owner)
        self.assertFalse(host.set_page(replacement))
        self.assertEqual(host.svg_bytes, SVG)
        start = QPoint(320, 240)
        QTest.mousePress(host, Qt.LeftButton, pos=start)
        QTest.mouseMove(host, start + QPoint(15, 10))
        pump()
        offset = host.bridge.panX, host.bridge.panY
        generation = host.bridge.gestureGeneration
        host.invalidate(new_owner)
        self.assertEqual(host.svg_bytes, SVG)
        self.assertFalse(host.set_page(page()))
        self.assertTrue(host.set_page(replacement))
        QTest.mouseMove(host, start + QPoint(55, 40))
        QTest.mouseRelease(host, Qt.LeftButton, pos=start + QPoint(55, 40))
        host.bridge.viewMove(generation, 600, 450)
        self.assertFalse(host.bridge.viewFit(generation))
        pump()
        self.assertEqual((host.bridge.panX, host.bridge.panY), offset)
        self.assertEqual(host.svg_bytes, replacement.svg_bytes)
        self.drag()
        self.assertEqual((host.bridge.panX, host.bridge.panY), (offset[0] + 31, offset[1] - 17))

    def test_same_owner_acceptance_hide_and_disable_retire_drag_without_stale_input(self):
        host = self.host
        for condition in ('accept', 'hide', 'disable', 'invalidate'):
            with self.subTest(condition=condition):
                start = QPoint(320, 240)
                QTest.mousePress(host, Qt.LeftButton, pos=start)
                QTest.mouseMove(host, start + QPoint(10, 10))
                pump()
                offset = host.bridge.panX, host.bridge.panY
                if condition == 'accept':
                    host.set_page(page())
                elif condition == 'hide':
                    host.hide()
                    host.show()
                elif condition == 'disable':
                    host.setEnabled(False)
                    host.setEnabled(True)
                else:
                    host.invalidate(OWNER)
                    self.wheel(120)
                    self.fit()
                    self.assertEqual(host.bridge.relativeZoom, 1.)
                    host.set_page(page())
                QTest.mouseMove(host, start + QPoint(45, 35))
                QTest.mouseRelease(host, Qt.LeftButton, pos=start + QPoint(45, 35))
                pump()
                self.assertEqual((host.bridge.panX, host.bridge.panY), offset)


if __name__ == '__main__':
    unittest.main()
