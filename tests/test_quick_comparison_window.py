"""Qt Quick snapshot proof at the QML, lifetime and captured-file boundaries."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QFont, QImage
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from spectra_app.dataset_state import graph_export_stem
from spectra_app.plot_comparison import PlotComparisonWindow, _SnapshotImageProvider
from spectra_app.svg_export import RenderedPage

APP = QApplication.instance() or QApplication([])
SOURCE = 'C:/synthetic measurements/sample 9T σ⁺.dat'
SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" width="6in" height="3in" viewBox="0 0 432 216">
<rect width="432" height="216" fill="white"/>
<path d="M 44 32 L 44 177 L 392 177" fill="none" stroke="#263447"/>
<path d="M 48 169 L 100 126 L 150 139 L 210 63 L 270 104 L 331 54 L 386 34"
fill="none" stroke="#1c65bc" stroke-width="2"/>
<text x="145" y="23" font-family="DejaVu Sans" font-size="12">Synthetic captured page A</text>
<text x="186" y="200" font-family="DejaVu Sans" font-size="10">Energy (eV)</text>
</svg>'''


def snapshot_page(svg=SVG, owner=('synthetic-a', 3, 5, 7, 11)):
    return RenderedPage(
        svg_bytes=svg, digest=hashlib.sha256(svg).hexdigest(), owner_token=owner,
        figure_size_in=(6., 3.), axes_bounds=(.1, .1, .8, .8), plot_size_in=(4.8, 2.4),
        colorbar_bounds={'global': (.1, .9, .8, .05)},
        colorbar_tick_positions={'global': (0., .5, 1.)},
        display_x_limits=(0., 1.), display_y_limits=(0., 1.),
        nonpositive_count=0, under_count=0, over_count=0, missing_count=0,
        valid_count=4, header_line_count=1, heatmap_dpi=150, font_family='DejaVu Sans',
        object_bounds={'annotation': ((.2, .3, .1, .1),)})


class QuickComparisonWindowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if isValid(window):
                window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def window(self, page=None, **kwargs):
        options = dict(source_label=SOURCE, metadata={'source_path': SOURCE, 'plot_title': 'Capture A'})
        options.update(kwargs)
        window = PlotComparisonWindow(page or snapshot_page(), **options)
        self.windows.append(window)
        window.show()
        QTest.qWait(80)
        return window

    def item(self, window, name):
        result = window.rootObject().findChild(QObject, name)
        self.assertIsNotNone(result, name)
        return result

    def click(self, window, name):
        item = self.item(window, name)
        center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, pos=center)
        APP.processEvents()

    def test_real_qml_window_loads_and_all_controls_fit_at_wide_and_narrow_sizes(self):
        window = self.window()
        self.assertIsInstance(window, QQuickView)
        self.assertNotIsInstance(window, QWidget)
        self.assertEqual(window.status(), QQuickView.Ready)
        self.assertTrue(window.isTopLevel())
        self.assertEqual(window.modality(), Qt.NonModal)
        self.assertIn('comparison', window.title().lower())
        self.assertEqual(window.findChildren(QWidget), [])
        source = self.item(window, 'comparisonSource')
        self.assertTrue(source.property('readOnly'))
        self.assertTrue(source.property('selectByMouse'))
        self.assertIn(SOURCE, source.property('text'))
        for width, height in ((900, 650), (420, 560)):
            window.resize(width, height)
            QTest.qWait(60)
            self.assertEqual(window.width(), width)
            for name in ('comparisonZoom', 'comparisonExportSvg', 'comparisonExportPng',
                         'comparisonDimensions', 'comparisonViewport', 'comparisonStatus'):
                item = self.item(window, name)
                origin = item.mapToScene(QPointF(0, 0))
                self.assertGreaterEqual(origin.x(), 0, name)
                self.assertGreaterEqual(origin.y(), 0, name)
                self.assertLessEqual(origin.x() + item.width(), width + 1, name)
                self.assertLessEqual(origin.y() + item.height(), height + 1, name)
            viewport, preview = (self.item(window, 'comparison' + name) for name in ('Viewport', 'Preview'))
            self.assertLessEqual(preview.width(), viewport.width())
            self.assertLessEqual(preview.height(), viewport.height())
            self.assertAlmostEqual(preview.width() / preview.height(), 2.)

    def test_exports_and_labels_remain_detached_from_borrowed_bytes_maps_and_metadata(self):
        borrowed = bytearray(SVG)
        captured = snapshot_page(borrowed)
        dimensions = [6., 3.]
        object.__setattr__(captured, 'figure_size_in', dimensions)
        metadata = {'source_path': SOURCE, 'plot_title': 'Capture A',
                    'presentation': {'annotations': [{'text': 'Captured annotation'}]}}
        window = self.window(captured, metadata=metadata, png_dpi=144)
        borrowed[:] = SVG.replace(b'page A', b'page B')
        dimensions[:] = [8., 9.]
        captured.colorbar_bounds.clear()
        captured.colorbar_tick_positions.clear()
        captured.object_bounds.clear()
        metadata['source_path'] = 'C:/synthetic/changed.dat'
        metadata['plot_title'] = 'Active title'
        metadata['presentation']['annotations'][0]['text'] = 'Edited'
        folder = Path(self.folder.name)
        self.assertEqual(window.export_svg(folder / 'arbitrary.svg').read_bytes(), SVG)
        self.assertEqual(window.svg_bytes, SVG)
        self.assertEqual(window._page.colorbar_bounds, {'global': (.1, .9, .8, .05)})
        self.assertEqual(window._page.colorbar_tick_positions, {'global': (0., .5, 1.)})
        self.assertEqual(window._page.object_bounds, {'annotation': ((.2, .3, .1, .1),)})
        png = window.export_png(folder / 'arbitrary.png')
        with Image.open(png) as image:
            self.assertEqual(image.size, (864, 432))
            self.assertAlmostEqual(image.info['dpi'][0], 144, delta=.02)
            self.assertAlmostEqual(image.info['dpi'][1], 144, delta=.02)
            saved = json.loads(image.info['SpectraAnalyzer'])
            self.assertEqual(saved['source_path'], SOURCE)
            self.assertEqual(saved['plot_title'], 'Capture A')
            self.assertEqual(saved['presentation']['annotations'][0]['text'], 'Captured annotation')
            self.assertEqual(image.info['Source'], SOURCE)
            self.assertEqual(image.info['PageDigest'], hashlib.sha256(SVG).hexdigest())
        self.assertIn('Capture A', self.item(window, 'comparisonSource').property('text'))
        self.assertNotIn('Active title', window.bridge.sourceText)

    def test_actual_zoom_keyboard_input_is_local_and_scrolls_at_fixed_scale(self):
        first, second = self.window(), self.window()
        first.requestActivate()
        QTest.qWait(30)
        zoom = self.item(first, 'comparisonZoom')
        zoom.forceActiveFocus()
        QTest.keyClick(first, Qt.Key_Down)
        APP.processEvents()
        self.assertEqual(first.bridge.zoom, '100%')
        self.assertEqual(second.bridge.zoom, 'Fit')
        for name, scale in (('100%', 1), ('150%', 1.5), ('200%', 2)):
            first.set_zoom(name)
            QTest.qWait(30)
            preview = self.item(first, 'comparisonPreview')
            self.assertEqual((preview.width(), preview.height()), (576 * scale, 288 * scale))
            self.assertEqual(zoom.property('currentText'), name)
        first.resize(420, 560)
        QTest.qWait(60)
        viewport = self.item(first, 'comparisonViewport')
        self.assertGreater(viewport.property('contentWidth'), viewport.width())
        viewport.forceActiveFocus()
        QTest.keyClick(first, Qt.Key_Right)
        self.assertGreater(viewport.property('contentX'), 0)
        first.set_zoom('Fit')
        QTest.qWait(60)
        preview = self.item(first, 'comparisonPreview')
        self.assertLessEqual(preview.width(), viewport.width())
        self.assertAlmostEqual(preview.width() / preview.height(), 2.)
        self.assertEqual(first._page.figure_size_in, (6., 3.))
        self.assertEqual(second.bridge.zoom, 'Fit')
        with self.assertRaisesRegex(ValueError, 'zoom'):
            first.set_zoom('25%')

    def test_qml_mouse_and_delete_inputs_cannot_edit_capture_or_provenance(self):
        metadata = {'source_path': SOURCE, 'presentation': {'annotations': [{'text': 'Captured'}]}}
        window = self.window(metadata=metadata)
        window.set_zoom('100%')
        preview = self.item(window, 'comparisonPreview')
        point = preview.mapToScene(QPointF(120, 80)).toPoint()
        QTest.mousePress(window, Qt.LeftButton, pos=point)
        QTest.mouseMove(window, point + QPoint(20, 12))
        QTest.mouseRelease(window, Qt.LeftButton, pos=point + QPoint(20, 12))
        self.item(window, 'comparisonViewport').forceActiveFocus()
        for key in (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            QTest.keyClick(window, key)
        saved = window.export_svg(Path(self.folder.name) / 'untouched.svg')
        self.assertEqual(saved.read_bytes(), SVG)
        self.assertEqual(window.svg_bytes, SVG)
        self.assertEqual(metadata['presentation']['annotations'], [{'text': 'Captured'}])

    def test_qml_export_buttons_use_source_defaults_and_arbitrary_names_without_overwrite(self):
        folder = Path(self.folder.name)
        metadata = {'source_path': SOURCE, 'graph_id': 'independent-graph', 'graph_name': 'Heat map',
                    'plot_title': 'Capture A'}
        window = self.window(metadata=metadata, png_dpi=144, output_folder=folder)
        for extension, name in (('svg', 'different-svg-name.svg'), ('png', 'different-png-name.png')):
            with patch.object(window, '_select_export_path', return_value=str(folder / name)) as picker:
                self.click(window, 'comparisonExport' + extension.title())
            picker.assert_called_once_with(extension, folder / (
                graph_export_stem(Path(SOURCE).stem, 'independent-graph', 'Heat map') + '_comparison.' + extension))
            self.assertIn('Exported ' + extension.upper(), window.bridge.statusText)
        self.assertEqual((folder / 'different-svg-name.svg').read_bytes(), SVG)
        with Image.open(folder / 'different-png-name.png') as image:
            self.assertEqual(image.size, (864, 432))
            self.assertEqual(image.info['Source'], SOURCE)
        existing = folder / 'existing.svg'
        existing.write_bytes(b'existing file')
        with self.assertRaises(FileExistsError):
            window.export_svg(existing)
        with patch.object(window, '_select_export_path', return_value=str(existing)):
            self.click(window, 'comparisonExportSvg')
        self.assertEqual(existing.read_bytes(), b'existing file')
        self.assertIn('Failed', self.item(window, 'comparisonStatus').property('text'))
        existing_png = folder / 'different-png-name.png'
        original_png = existing_png.read_bytes()
        with self.assertRaises(FileExistsError):
            window.export_png(existing_png)
        self.assertEqual(existing_png.read_bytes(), original_png)
        with patch.object(window, '_select_export_path', return_value=''):
            self.click(window, 'comparisonExportSvg')
        self.assertEqual(len(list(folder.iterdir())), 3)

    def test_save_picker_receives_actual_quick_window_and_suggested_export(self):
        window = self.window()
        default = Path(self.folder.name) / 'capture.svg'
        for selected in ('', str(default)):
            with self.subTest(selected=selected), patch(
                    'spectra_app.plot_comparison.FilePicker.getSaveFileName',
                    return_value=(selected, 'SVG files (*.svg)')) as picker:
                self.assertEqual(window._select_export_path('svg', default), selected)
                picker.assert_called_once_with(window, 'Export comparison SVG', str(default),
                                               'SVG files (*.svg)', default_suffix='svg')

    def test_close_disposes_only_that_window_and_owner_disposes_remaining_capture(self):
        owner = QWidget()
        owner.imported_data = {'synthetic': object()}
        original_data = owner.imported_data.copy()
        self.addCleanup(lambda: owner.close() if isValid(owner) else None)
        first = self.window(parent=owner)
        second = self.window(snapshot_page(SVG.replace(b'page A', b'page B')), parent=owner)
        self.assertEqual(first.transientParent(), owner.windowHandle())
        self.assertEqual(QObject.parent(first), owner)
        retained = [first, second]
        first.destroyed.connect(lambda: retained.remove(first))
        first.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertFalse(isValid(first))
        self.assertTrue(isValid(owner))
        self.assertEqual(retained, [second])
        self.assertTrue(second.isVisible())
        self.assertEqual(second.svg_bytes, SVG.replace(b'page A', b'page B'))
        self.assertEqual(owner.imported_data, original_data)
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(second))

    def test_actual_qml_provider_requests_one_device_pixel_per_display_pixel(self):
        requests = []
        original = _SnapshotImageProvider.requestImage

        def record_request(provider, image_id, size, requested_size):
            image = original(provider, image_id, size, requested_size)
            requests.append((QSize(requested_size), image.size()))
            return image

        # Observe requests made by QML itself. A manual request to the provider
        # cannot detect Qt applying the window DPR to Image.sourceSize again.
        with patch.object(_SnapshotImageProvider, 'requestImage', record_request):
            window = self.window()
            preview = self.item(window, 'comparisonPreview')
            for zoom in ('100%', '150%', '200%', 'Fit'):
                with self.subTest(zoom=zoom, dpr=window.devicePixelRatio()):
                    requests.clear()
                    window.set_zoom(zoom)
                    QTest.qWait(60)
                    self.assertTrue(requests, 'QML must reload the provider at each zoom')
                    expected = QSize(round(math.ceil(preview.width()) * window.devicePixelRatio()),
                                     round(math.ceil(preview.height()) * window.devicePixelRatio()))
                    self.assertEqual(requests[-1], (expected, expected))

    def test_native_validation_skips_raster_prepass_but_rejects_invalid_capture(self):
        with patch('spectra_app.plot_comparison.QGuiApplication.platformName', return_value='windows'), \
                patch('spectra_app.plot_comparison.render_svg_image') as raster:
            window = self.window()
            self.assertEqual(window.status(), QQuickView.Ready)
            raster.assert_not_called()
            invalid = snapshot_page(b'not an SVG')
            with self.assertRaisesRegex(ValueError, 'QtSvg'):
                PlotComparisonWindow(invalid, source_label=SOURCE, metadata={})
            invalid = snapshot_page()
            object.__setattr__(invalid, 'figure_size_in', (0., 3.))
            with self.assertRaisesRegex(ValueError, 'dimensions'):
                PlotComparisonWindow(invalid, source_label=SOURCE, metadata={})
            with patch('spectra_app.plot_comparison.QImage', return_value=QImage()) as allocation:
                allocation.Format_ARGB32 = QImage.Format_ARGB32
                with self.assertRaisesRegex(ValueError, 'allocate'):
                    PlotComparisonWindow(snapshot_page(), source_label=SOURCE, metadata={})

    def test_offscreen_validation_retains_export_font_registration(self):
        from spectra_app.svg_export import render_svg_image
        with patch('spectra_app.plot_comparison.QGuiApplication.platformName', return_value='offscreen'), \
                patch('spectra_app.plot_comparison.render_svg_image', wraps=render_svg_image) as raster:
            self.window()
            raster.assert_called_once()
            self.assertEqual(raster.call_args.kwargs, {'dpi': 72})

    def test_window_scene_renders_captured_svg_vectors_inside_the_qml_viewport(self):
        window = self.window()
        QTest.qWait(40)
        preview = self.item(window, 'comparisonPreview')
        screenshot = window.grabWindow()
        self.assertFalse(screenshot.isNull())
        self.assertEqual(screenshot.width(), round(window.width() * window.devicePixelRatio()))
        # This vertex belongs to the synthetic SVG. Checking scene pixels proves
        # the real QML Image displays captured vectors rather than an empty item.
        vertex = preview.mapToScene(QPointF(preview.width() * 270 / 432,
                                           preview.height() * 104 / 216))
        x, y = round(vertex.x() * window.devicePixelRatio()), round(vertex.y() * window.devicePixelRatio())
        pixels = [screenshot.pixelColor(x + dx, y + dy)
                  for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        self.assertTrue(any(abs(color.red() - 28) < 12 and abs(color.green() - 101) < 12
                            and abs(color.blue() - 188) < 12 for color in pixels))

    def test_current_application_font_updates_all_qml_controls_and_preserves_narrow_layout(self):
        original = APP.font()
        self.addCleanup(lambda: APP.setFont(original))
        APP.setFont(QFont('Arial', 11))
        window = self.window()
        for size in (11, 16):
            APP.setFont(QFont('Arial', size))
            window.resize(420, 560)
            QTest.qWait(60)
            for name in ('comparisonSource', 'comparisonZoom', 'comparisonExportSvg',
                         'comparisonExportPng', 'comparisonDimensions', 'comparisonStatus'):
                item = self.item(window, name)
                font = item.property('font')
                self.assertEqual(font.family(), 'Arial', name)
                self.assertEqual(font.pointSize(), size, name)
                if name != 'comparisonSource':  # Long provenance scrolls in its bounded viewer.
                    origin = item.mapToScene(QPointF(0, 0))
                    self.assertLessEqual(origin.x() + item.width(), window.width() + 1, name)
                    self.assertLessEqual(origin.y() + item.height(), window.height() + 1, name)
            self.assertGreater(self.item(window, 'comparisonViewport').height(), 80)

    def test_capture_rejects_invalid_inputs_and_retains_literal_source_and_versions(self):
        window = self.window()
        self.assertIn('Capture A', window.bridge.sourceText)
        for version in ('3', '5', '7', '11'):
            self.assertIn(version, window.bridge.sourceText)
        self.assertIn('300', window.bridge.dimensionsText)
        for source_label in ('', '   ', None):
            with self.subTest(source=source_label), self.assertRaisesRegex(ValueError, 'source'):
                PlotComparisonWindow(snapshot_page(), source_label=source_label, metadata={})
        for dpi in (0, -10, True, 1.5):
            with self.subTest(dpi=dpi), self.assertRaisesRegex(ValueError, 'DPI'):
                PlotComparisonWindow(snapshot_page(), source_label=SOURCE, metadata={}, png_dpi=dpi)
        for metadata in ([], {'invalid': float('nan')}, {'invalid': object()}):
            with self.subTest(metadata=metadata), self.assertRaisesRegex(ValueError, 'metadata'):
                PlotComparisonWindow(snapshot_page(), source_label=SOURCE, metadata=metadata)
        with self.assertRaisesRegex(ValueError, 'page'):
            PlotComparisonWindow(None, source_label=SOURCE, metadata={})


if __name__ == '__main__':
    unittest.main()
