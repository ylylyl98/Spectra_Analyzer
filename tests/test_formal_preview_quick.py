"""Production Quick paper proofs through scene pixels and real host input."""
from dataclasses import replace
import hashlib
import math
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from spectra_app.formal_preview import FormalPreview
from spectra_app.plot_annotations import page_to_coordinates
from spectra_app.plot_presentation import PlotAnnotation, PlotPresentation
from spectra_app.svg_export import RenderedPage
from tests.workbook_quick_helpers import item, pump

APP = QApplication.instance() or QApplication([])
OWNER = ('preview-graph', 2, 3, 5, 7)
SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" width="6in" height="4in" viewBox="0 0 576 384">
<rect width="576" height="384" fill="white"/>
<rect x="58" y="38" width="460" height="307" fill="#e6f0fc"/>
<path d="M 80 280 L 220 245 L 350 160 L 490 210" fill="none" stroke="#1c65bc" stroke-width="4"/>
<text x="150" y="70" font-family="Arial" font-size="12">Editable measured title</text>
<text x="126.72" y="84.48" font-family="Arial" font-size="12">Default note</text>
<path d="M 196 223 L 334 269" fill="none" stroke="black"/>
<rect x="202" y="11" width="172" height="15" fill="#aa271c"/>
</svg>'''


def page(svg=SVG, owner=OWNER):
    return RenderedPage(
        svg_bytes=svg, digest=hashlib.sha256(svg).hexdigest(), owner_token=owner,
        figure_size_in=(6., 4.), axes_bounds=(.1, .1, .8, .8), plot_size_in=(4.8, 3.2),
        colorbar_bounds={'region-2': (.35, .93, .3, .04)},
        colorbar_tick_positions={'region-2': (0., .5, 1.)},
        display_x_limits=(1., 100.), display_y_limits=(10., 1000.),
        nonpositive_count=0, under_count=0, over_count=0, missing_count=0,
        valid_count=4, header_line_count=1, heatmap_dpi=150, font_family='Arial',
        object_bounds={'title': ((.26, .81, .25, .05),),
                       'annotation:note': ((.22, .78, .18, .04),),
                       'horizontal-axis': ((.35, .025, .3, .04),),
                       'colorbar:region-2': ((.35, .93, .3, .04),)})


class FormalPreviewQuickTests(unittest.TestCase):
    def setUp(self):
        self.hosts = []
        self.note = PlotAnnotation('note', text='Default note', position=(.15, .85))
        self.arrow = PlotAnnotation('arrow', kind='arrow', text='Arrow', position=(.3, .4),
                                    target=(.6, .25), target_space='axes-fraction')
        self.presentation = PlotPresentation(width_in=6., height_in=4.,
            annotations=(self.note, self.arrow), title_position=(.2, .95))

    def tearDown(self):
        for host in self.hosts:
            host.close()
            host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def preview(self, size=(640, 480), presentation=None):
        host = FormalPreview()
        self.hosts.append(host)
        host.resize(*size)
        host.set_geometry(SimpleNamespace(owner_token=OWNER, title='Editable measured title',
                                         x_scale='linear', y_scale='linear'),
                          presentation or self.presentation)
        self.assertTrue(host.set_page(page()))
        host.show()
        host.activateWindow()
        pump(50)
        self.assertEqual(host.status(), QQuickWidget.Ready)
        return host

    def point(self, host, paper_point):
        return item(host, 'formalPaper').mapToScene(QPointF(paper_point)).toPoint()

    def reveal(self, host, paper_point):
        viewport = item(host, 'formalViewport')
        x, y = paper_point.x(), paper_point.y()
        viewport.setProperty('contentX', min(max(20., x - viewport.width() / 2),
                                            max(0., viewport.property('contentWidth') - viewport.width())))
        viewport.setProperty('contentY', min(max(12., y - viewport.height() / 2),
                                            max(0., viewport.property('contentHeight') - viewport.height())))
        pump()
        return self.point(host, paper_point)

    def measured_point(self, host, key):
        left, bottom, width, height = host.page.object_bounds[key][0]
        w, h = host.paper_display_size()
        return QPointF((left + width / 2) * w, (1 - bottom - height / 2) * h)

    def drag(self, host, point, delta):
        QTest.mousePress(host, Qt.LeftButton, pos=point)
        QTest.mouseMove(host, point + delta)
        QTest.mouseRelease(host, Qt.LeftButton, pos=point + delta)
        pump()

    def test_scene_owns_paper_scrolling_raster_resolution_and_actual_svg_pixels(self):
        host = self.preview()
        self.assertIsInstance(host, QQuickWidget)
        self.assertEqual(host.findChildren(QWidget), [])
        image_item = item(host, 'formalPaper')
        for zoom in ('100%', '150%', '200%', 'Fit'):
            host.set_zoom(zoom)
            pump(30)
            source = image_item.property('sourceSize')
            self.assertEqual(source, QSize(math.ceil(image_item.width()), math.ceil(image_item.height())))
            # The image-provider request, not the declarative logical size, is
            # the proof boundary for actual raster resolution on HiDPI screens.
            self.assertEqual(host.image_provider._last_request,
                             QSize(round(source.width() * host.devicePixelRatioF()),
                                   round(source.height() * host.devicePixelRatioF())))
            self.assertAlmostEqual(image_item.width() / image_item.height(), 1.5)
        captured = host.grabFramebuffer()
        self.assertFalse(captured.isNull())
        vertex = self.point(host, QPointF(image_item.width() * 350 / 576,
                                          image_item.height() * 160 / 384))
        x, y = round(vertex.x() * host.devicePixelRatioF()), round(vertex.y() * host.devicePixelRatioF())
        pixels = [captured.pixelColor(x + dx, y + dy) for dx in range(-2, 3) for dy in range(-2, 3)]
        self.assertTrue(any(abs(c.red() - 28) < 12 and abs(c.green() - 101) < 12
                            and abs(c.blue() - 188) < 12 for c in pixels))
        self.assertEqual(host.svg_bytes, SVG)

    def test_actual_default_position_click_selects_without_roundtrip_edit_or_focus_loss(self):
        host = self.preview()
        moved, selected = [], []
        host.annotationMoved.connect(moved.append)
        host.annotationSelected.connect(selected.append)
        before = host.presentation
        QTest.mouseClick(host, Qt.LeftButton, pos=self.point(host, host.annotation_point(self.note)))
        pump()
        self.assertEqual(selected, ['note'])
        self.assertEqual(moved, [])
        self.assertIs(host.presentation, before)
        self.assertEqual(host.presentation.annotations[0].position, (.15, .85))
        self.assertTrue(item(host, 'formalViewport').hasActiveFocus())
        self.assertTrue(host.hasFocus())
        self.assertTrue(item(host, 'formalSelectionOverlay').property('geometry')['visible'])
        capture = host.grabFramebuffer()
        anchor = self.point(host, host.annotation_point(self.note))
        ratio = host.devicePixelRatioF()
        colors = [capture.pixelColor(round((anchor.x() + dx) * ratio), round((anchor.y() + dy) * ratio))
                  for dx in range(-7, 8) for dy in range(-7, 8)]
        self.assertTrue(any(c.blue() > 160 and c.red() < 50 and c.green() < 140 for c in colors))

    def test_right_doubleclick_does_not_enter_left_button_property_editor(self):
        host = self.preview()
        edits = []
        host.propertyEditRequested.connect(lambda *args: edits.append(args))
        point = self.point(host, self.measured_point(host, 'title'))
        QTest.mouseDClick(host, Qt.RightButton, pos=point)
        QTest.mouseRelease(host, Qt.RightButton, pos=point)
        pump()
        self.assertEqual(edits, [])

    def test_actual_text_title_and_arrow_head_drags_commit_once_with_offset(self):
        host = self.preview()
        moved, title, selections = [], [], []
        host.annotationMoved.connect(moved.append)
        host.titleMoved.connect(title.append)
        host.annotationSelected.connect(selections.append)
        point = self.point(host, host.annotation_point(self.note))
        self.drag(host, point, QPoint(24, 16))
        self.assertEqual(len(moved), 1)
        expected = page_to_coordinates((host.annotation_point(self.note).x() + 24,
                                        host.annotation_point(self.note).y() + 16),
                                       host.paper_display_size(), host.page, host.snapshot,
                                       host.presentation, 'axes-fraction')
        for actual, value in zip(moved[0].position, expected):
            self.assertAlmostEqual(actual, value, delta=.002)
        self.assertEqual(host.presentation.annotations[0].position, (.15, .85))
        point = self.point(host, host.annotation_point(self.arrow, True))
        self.drag(host, point, QPoint(-18, 12))
        self.assertEqual(len(moved), 2)
        self.assertEqual(moved[-1].position, self.arrow.position)
        self.assertNotEqual(moved[-1].target, self.arrow.target)
        point = self.point(host, host.annotation_point(self.arrow))
        self.drag(host, point, QPoint(12, -8))
        self.assertEqual(len(moved), 3)
        self.assertNotEqual(moved[-1].position, self.arrow.position)
        self.assertEqual(moved[-1].target, self.arrow.target)
        point = self.point(host, self.measured_point(host, 'title'))
        self.drag(host, point, QPoint(22, 10))
        self.assertEqual(len(title), 1)
        self.assertEqual(selections[-1], '')
        self.assertIsNone(host.selected_id)
        self.assertTrue(all(0 <= value <= 1 for value in title[0]))
        deleted = []
        host.annotationDeleteRequested.connect(lambda: deleted.append(True))
        QTest.keyClick(host, Qt.Key_Delete)
        self.assertEqual(deleted, [])

    def test_actual_keys_nudge_copy_delete_and_escape_cancel_without_release_edit(self):
        host = self.preview()
        moved, deleted, copied = [], [], []
        host.annotationMoved.connect(moved.append)
        host.annotationDeleteRequested.connect(lambda: deleted.append(True))
        host.graphCopyRequested.connect(lambda: copied.append(True))
        point = self.point(host, host.annotation_point(self.note))
        QTest.mouseClick(host, Qt.LeftButton, pos=point)
        QTest.keyClick(host, Qt.Key_Right)
        QTest.keyClick(host, Qt.Key_Down, Qt.ShiftModifier)
        self.assertEqual(len(moved), 2)
        original = host.bridge._point(self.note)
        for annotation, dx, dy in ((moved[0], 1, 0), (moved[1], 0, 10)):
            result = host.bridge._point(annotation)
            self.assertAlmostEqual(result.x() - original.x(), dx)
            self.assertAlmostEqual(result.y() - original.y(), dy)
        QTest.keyClick(host, Qt.Key_C, Qt.ControlModifier)
        QTest.keyClick(host, Qt.Key_Delete)
        self.assertEqual(copied, [True])
        self.assertEqual(deleted, [True])
        QTest.mousePress(host, Qt.LeftButton, pos=point)
        QTest.mouseMove(host, point + QPoint(30, 15))
        pump()
        self.assertNotEqual(item(host, 'formalSelectionOverlay').property('geometry')['pointX'], original.x())
        QTest.keyClick(host, Qt.Key_Escape)
        QTest.mouseRelease(host, Qt.LeftButton, pos=point + QPoint(30, 15))
        self.assertEqual(len(moved), 2)
        self.assertIsNone(host.selected_id)
        self.assertFalse(item(host, 'formalSelectionOverlay').property('geometry'))

    def test_actual_doubleclick_uses_measured_geometry_at_200_percent_with_both_axis_scroll(self):
        host = self.preview(size=(300, 210))
        host.set_zoom('200%')
        pump(40)
        edits = []
        host.propertyEditRequested.connect(lambda *args: edits.append(args))
        viewport = item(host, 'formalViewport')
        for key, expected in (('title', ('title', '')), ('annotation:note', ('annotation', 'note')),
                              ('horizontal-axis', ('horizontal-axis', '')),
                              ('colorbar:region-2', ('colorbar', 'region-2'))):
            point = self.reveal(host, self.measured_point(host, key))
            self.assertGreater(viewport.property('contentX'), 0)
            self.assertGreater(viewport.property('contentY'), 0)
            self.assertTrue(host.rect().contains(point), key)
            QTest.mouseDClick(host, Qt.LeftButton, pos=point)
            QTest.mouseRelease(host, Qt.LeftButton, pos=point)
            pump()
            self.assertEqual(edits[-1], expected)
        point = self.reveal(host, host.annotation_point(self.arrow, True))
        QTest.mouseDClick(host, Qt.LeftButton, pos=point)
        QTest.mouseRelease(host, Qt.LeftButton, pos=point)
        pump()
        self.assertEqual(edits[-1], ('annotation', 'arrow'))

    def test_invalidation_retains_pixels_scroll_rejects_stale_gestures_and_updates_same_host(self):
        host = self.preview(size=(300, 210))
        host.set_zoom('200%')
        pump()
        point = self.reveal(host, host.annotation_point(self.note))
        viewport = item(host, 'formalViewport')
        scroll = viewport.property('contentX'), viewport.property('contentY')
        moved, edits = [], []
        host.annotationMoved.connect(moved.append)
        host.propertyEditRequested.connect(lambda *args: edits.append(args))
        provider, scene = host.image_provider, host.rootObject()
        source = item(host, 'formalPaper').property('source')
        QTest.mousePress(host, Qt.LeftButton, pos=point)
        QTest.mouseMove(host, point + QPoint(14, 8))
        new_owner = ('other-graph', 2, 3, 5, 7)
        host.invalidate(new_owner)
        QTest.mouseRelease(host, Qt.LeftButton, pos=point + QPoint(14, 8))
        QTest.mouseDClick(host, Qt.LeftButton, pos=point)
        QTest.keyClick(host, Qt.Key_Right)
        pump()
        self.assertEqual(moved, [])
        self.assertEqual(edits, [])
        self.assertEqual(host.svg_bytes, SVG)
        self.assertEqual(item(host, 'formalPaper').property('source'), source)
        self.assertEqual((viewport.property('contentX'), viewport.property('contentY')), (scroll[0] + 40, scroll[1]))
        self.assertFalse(host.set_page(page()))
        self.assertEqual(host.svg_bytes, SVG)
        host.set_geometry(SimpleNamespace(owner_token=new_owner, title='New owner', x_scale='linear', y_scale='linear'),
                          self.presentation)
        changed_svg = SVG.replace(b'#1c65bc', b'#bc651c')
        self.assertTrue(host.set_page(page(changed_svg, new_owner)))
        pump()
        self.assertIs(host.image_provider, provider)
        self.assertIs(host.rootObject(), scene)
        self.assertEqual(host.svg_bytes, changed_svg)
        self.assertNotEqual(item(host, 'formalPaper').property('source'), source)
        self.assertEqual((viewport.property('contentX'), viewport.property('contentY')), (scroll[0] + 40, scroll[1]))
        self.assertFalse(host.grabFramebuffer().isNull())

    def test_reverse_order_visibility_and_narrow_arrow_fallback_preserve_renderer_targets(self):
        upper = replace(self.note, id='upper')
        hidden = replace(self.note, id='hidden', visible=False)
        host = self.preview(presentation=replace(self.presentation, annotations=(self.note, upper, hidden, self.arrow)))
        selected, edits = [], []
        host.annotationSelected.connect(selected.append)
        host.propertyEditRequested.connect(lambda *args: edits.append(args))
        QTest.mouseClick(host, Qt.LeftButton, pos=self.point(host, host.annotation_point(self.note)))
        self.assertEqual(selected, ['upper'])
        # The center of an arrow's overall rectangle is empty. Only the actual
        # measured text target or narrow line/head may intercept a doubleclick.
        a, b = host.bridge._point(self.arrow), host.bridge._point(self.arrow, True)
        empty = QPointF(a.x() + 8, b.y() - 2)
        QTest.mouseDClick(host, Qt.LeftButton, pos=self.point(host, empty))
        QTest.mouseRelease(host, Qt.LeftButton, pos=self.point(host, empty))
        pump()
        self.assertEqual(edits, [])
        line = (a + b) / 2
        QTest.mouseDClick(host, Qt.LeftButton, pos=self.point(host, line))
        QTest.mouseRelease(host, Qt.LeftButton, pos=self.point(host, line))
        pump()
        self.assertEqual(edits, [('annotation', 'arrow')])

    def test_two_hosts_with_identical_owner_keys_keep_edits_and_copy_local(self):
        first, second = self.preview(), self.preview()
        edits_first, edits_second, copied_first, copied_second = [], [], [], []
        first.annotationMoved.connect(edits_first.append)
        second.annotationMoved.connect(edits_second.append)
        first.graphCopyRequested.connect(lambda: copied_first.append(True))
        second.graphCopyRequested.connect(lambda: copied_second.append(True))
        first.activateWindow()
        first.setFocus()
        pump()
        point = self.point(first, first.annotation_point(self.note))
        self.drag(first, point, QPoint(15, 10))
        QTest.keyClick(first, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(len(edits_first), 1)
        self.assertEqual(edits_second, [])
        self.assertEqual(copied_first, [True])
        self.assertEqual(copied_second, [])
        self.assertIsNone(second.selected_id)

    def test_hidden_disabled_guarded_or_changed_geometry_cannot_commit(self):
        for condition in ('hide', 'disable', 'guard', 'geometry'):
            with self.subTest(condition=condition):
                host = self.preview()
                moved = []
                host.annotationMoved.connect(moved.append)
                point = self.point(host, host.annotation_point(self.note))
                QTest.mousePress(host, Qt.LeftButton, pos=point)
                QTest.mouseMove(host, point + QPoint(20, 15))
                if condition == 'hide':
                    host.hide()
                elif condition == 'disable':
                    host.setEnabled(False)
                elif condition == 'guard':
                    host.set_mutation_guard(lambda: False)
                else:
                    host.set_geometry(SimpleNamespace(owner_token=OWNER, title='New geometry', x_scale='linear', y_scale='linear'),
                                      self.presentation)
                QTest.mouseRelease(host, Qt.LeftButton, pos=point + QPoint(20, 15))
                self.assertEqual(moved, [])

    def test_resize_and_scroll_do_not_run_scientific_or_export_renderers(self):
        host = self.preview()
        provider, scene = host.image_provider, host.rootObject()
        with patch('spectra_app.formal_preview_controller.render_svg_image') as raster_validation, \
                patch('spectra_app.svg_export.render_hybrid_svg') as scientific:
            host.resize(380, 320)
            host.set_zoom('200%')
            pump()
            viewport = item(host, 'formalViewport')
            viewport.setProperty('contentX', 35)
            viewport.setProperty('contentY', 30)
            pump()
        raster_validation.assert_not_called()
        scientific.assert_not_called()
        self.assertIs(host.image_provider, provider)
        self.assertIs(host.rootObject(), scene)
        self.assertEqual(host.svg_bytes, SVG)

    def test_native_platform_acceptance_skips_redundant_export_raster_prepass(self):
        host = self.preview()
        changed_svg = SVG.replace(b'#1c65bc', b'#bc651c')
        with patch('spectra_app.formal_preview_controller.QGuiApplication.platformName', return_value='windows'), \
                patch('spectra_app.formal_preview_controller.render_svg_image') as raster_validation:
            self.assertTrue(host.set_page(page(changed_svg)))
        raster_validation.assert_not_called()
        pump()
        self.assertEqual(host.svg_bytes, changed_svg)
        self.assertFalse(host.grabFramebuffer().isNull())

    def test_hidden_fit_preserves_scale_until_shown_including_new_page_acceptance(self):
        host = self.preview()
        scale, size = host.bridge._scale, host.paper_display_size()
        host.hide()
        pump()
        host.resize(380, 320)
        pump()
        self.assertEqual(host.bridge._scale, scale)
        self.assertEqual(host.paper_display_size(), size)
        visible_host = self.preview()
        visible_size = visible_host.paper_display_size()
        self.assertTrue(host.set_page(replace(page(), figure_size_in=(8., 4.))))
        pump()
        self.assertEqual(host.bridge._scale, scale)
        self.assertEqual(host.paper_display_size(), (8 * 96 * scale, 4 * 96 * scale))
        self.assertEqual(visible_host.paper_display_size(), visible_size)
        host.show()
        pump()
        expected_scale = min((host.width() - 12) / (8 * 96), (host.height() - 12) / (4 * 96))
        self.assertAlmostEqual(host.bridge._scale, expected_scale)
        self.assertEqual(host.paper_display_size(), (8 * 96 * expected_scale, 4 * 96 * expected_scale))

    def test_data_drag_and_nudge_preserve_canonical_exchange_log_and_flipped_limits(self):
        annotation = replace(self.note, coordinate_space='data', position=(10., 100.))
        presentation = replace(self.presentation, annotations=(annotation,),
                               axis=replace(self.presentation.axis, exchange_xy=True, flip_x=True, flip_y=True))
        host = self.preview(presentation=presentation)
        host.set_geometry(SimpleNamespace(owner_token=OWNER, title='', x_scale='log', y_scale='log'), presentation)
        host.set_page(replace(page(), display_x_limits=(1000., 10.), display_y_limits=(100., 1.), object_bounds={}))
        pump()
        moved = []
        host.annotationMoved.connect(moved.append)
        point = self.point(host, host.annotation_point(annotation))
        self.drag(host, point, QPoint(20, -10))
        self.assertEqual(len(moved), 1)
        original = host.bridge._point(annotation)
        changed = host.bridge._point(moved[0])
        self.assertAlmostEqual(changed.x() - original.x(), 20)
        self.assertAlmostEqual(changed.y() - original.y(), -10)
        QTest.keyClick(host, Qt.Key_Left, Qt.ShiftModifier)
        self.assertEqual(len(moved), 2)
        changed = host.bridge._point(moved[1])
        self.assertAlmostEqual(changed.x() - original.x(), -10)
        self.assertAlmostEqual(changed.y() - original.y(), 0)


if __name__ == '__main__':
    unittest.main()
