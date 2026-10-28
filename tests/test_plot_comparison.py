"""Comparison windows retain the captured page at the UI/file boundary."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from spectra_app.svg_export import RenderedPage

APP = QApplication.instance() or QApplication([])
SOURCE = 'C:/measurements/sample 9T σ⁺.dat'
SVG_A = b'''<svg xmlns="http://www.w3.org/2000/svg" width="2in" height="1in" viewBox="0 0 144 72">
<rect width="144" height="72" fill="white"/>
<path d="M 18 45 L 110 20" stroke="blue"/>
<text x="20" y="35" font-family="DejaVu Sans" font-size="10">Captured annotation A</text>
</svg>'''
SVG_B = SVG_A.replace(b'annotation A', b'annotation B').replace(b'blue', b'red')


def page(svg_bytes=SVG_A, *, owner=('data-a', 3, 5, 7, 11)):
    return RenderedPage(
        svg_bytes=svg_bytes, digest=hashlib.sha256(svg_bytes).hexdigest(),
        owner_token=owner, figure_size_in=(2., 1.), axes_bounds=(.1, .1, .8, .8),
        plot_size_in=(1.6, .8), colorbar_bounds={'global': (.1, .9, .8, .05)},
        colorbar_tick_positions={'global': (0., .5, 1.)},
        display_x_limits=(0., 1.), display_y_limits=(0., 1.),
        nonpositive_count=0, under_count=0, over_count=0, missing_count=0,
        valid_count=4, header_line_count=1, heatmap_dpi=150, font_family='DejaVu Sans')


class PlotComparisonTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('spectra_app.plot_comparison'),
                             'Read-only comparison snapshot window is missing')
        from spectra_app.plot_comparison import PlotComparisonWindow
        self.window_class = PlotComparisonWindow
        self.windows = []
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def tearDown(self):
        for window in self.windows:
            if isValid(window):
                window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def window(self, captured_page=None, **kwargs):
        options = dict(source_label=SOURCE, metadata={'source_path': SOURCE, 'plot_title': 'Capture A'})
        options.update(kwargs)
        window = self.window_class(captured_page or page(), **options)
        self.windows.append(window)
        return window

    def item(self, window, name):
        item = window.rootObject().findChild(QObject, name)
        self.assertIsNotNone(item, name)
        return item

    def click(self, window, name):
        window.show()
        APP.processEvents()
        item = self.item(window, name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, pos=point)
        APP.processEvents()

    def test_exports_keep_creation_bytes_and_nested_metadata_after_caller_mutation(self):
        # Re-reading the caller's page/metadata at export would change these files.
        borrowed_bytes = bytearray(SVG_A)
        captured_page = page(borrowed_bytes)
        dimensions = [2., 1.]
        object.__setattr__(captured_page, 'figure_size_in', dimensions)
        metadata = {'source_path': SOURCE, 'plot_title': 'Capture A',
                    'presentation': {'annotations': [{'text': 'Captured annotation A'}]}}
        window = self.window(captured_page, metadata=metadata, png_dpi=200)
        folder = Path(self.folder.name)
        before_svg = window.export_svg(folder / 'unrelated-before.svg')
        before_png = window.export_png(folder / 'unrelated-before.png')

        borrowed_bytes[:] = SVG_B
        dimensions[:] = [8., 9.]
        captured_page.colorbar_bounds.clear()
        metadata['source_path'] = 'C:/other/active.dat'
        metadata['presentation']['annotations'][0]['text'] = 'Edited later'
        metadata['plot_title'] = 'Active title'
        after_svg = window.export_svg(folder / 'chosen-name.svg')
        after_png = window.export_png(folder / 'chosen-name.png')

        self.assertEqual(before_svg.read_bytes(), SVG_A)
        self.assertEqual(after_svg.read_bytes(), SVG_A)
        self.assertEqual(window.svg_bytes, SVG_A)
        for path in (before_png, after_png):
            with Image.open(path) as image:
                self.assertEqual(image.size, (400, 200))
                self.assertAlmostEqual(image.info['dpi'][0], 200, delta=.02)
                self.assertAlmostEqual(image.info['dpi'][1], 200, delta=.02)
                saved = json.loads(image.info['SpectraAnalyzer'])
                self.assertEqual(saved['source_path'], SOURCE)
                self.assertEqual(saved['plot_title'], 'Capture A')
                self.assertEqual(saved['presentation']['annotations'][0]['text'], 'Captured annotation A')
                self.assertEqual(image.info['Source'], SOURCE)
        self.assertIn('Capture A', self.item(window, 'comparisonSource').property('text'))
        self.assertNotIn('Active title', self.item(window, 'comparisonSource').property('text'))

    def test_each_window_zoom_changes_only_preview_size(self):
        # A global zoom or a rescaled export page would corrupt comparison/export.
        first = self.window()
        second = self.window()
        first.set_zoom('100%')
        second.set_zoom('150%')
        first_preview = self.item(first, 'comparisonPreview')
        second_preview = self.item(second, 'comparisonPreview')
        self.assertEqual((first_preview.width(), first_preview.height()), (192, 96))
        self.assertEqual((second_preview.width(), second_preview.height()), (288, 144))
        first.show()
        first.requestActivate()
        QTest.qWait(30)
        self.item(first, 'comparisonZoom').forceActiveFocus()
        QTest.keyClick(first, Qt.Key_End)
        APP.processEvents()
        self.assertEqual((first_preview.width(), first_preview.height()), (384, 192))
        self.assertEqual(second.bridge.zoom, '150%')
        self.assertEqual((second_preview.width(), second_preview.height()), (288, 144))
        first.resize(760, 520)
        first.show()
        first.set_zoom('Fit')
        APP.processEvents()
        viewport = self.item(first, 'comparisonViewport')
        self.assertLessEqual(first_preview.width(), viewport.width())
        self.assertLessEqual(first_preview.height(), viewport.height())
        self.assertAlmostEqual(first_preview.width() / first_preview.height(), 2., delta=.02)
        saved = first.export_svg(Path(self.folder.name) / 'fit.svg')
        self.assertEqual(saved.read_bytes(), SVG_A)
        with self.assertRaisesRegex(ValueError, 'zoom'):
            first.set_zoom('25%')

    def test_mouse_and_delete_keys_cannot_edit_captured_annotations(self):
        # Accidentally reusing editable SvgPagePreview would change the snapshot.
        metadata = {'source_path': SOURCE, 'presentation': {'annotations': [{'text': 'Captured annotation A'}]}}
        window = self.window(metadata=metadata)
        window.set_zoom('200%')
        window.show()
        APP.processEvents()
        canvas = self.item(window, 'comparisonPreview')
        start = canvas.mapToScene(QPointF(55, 90)).toPoint()
        end = canvas.mapToScene(QPointF(90, 110)).toPoint()
        QTest.mousePress(window, Qt.LeftButton, pos=start)
        QTest.mouseMove(window, end)
        QTest.mouseRelease(window, Qt.LeftButton, pos=end)
        self.item(window, 'comparisonViewport').forceActiveFocus()
        for key in (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            QTest.keyClick(window, key)
        output = window.export_svg(Path(self.folder.name) / 'untouched.svg')
        self.assertEqual(output.read_bytes(), SVG_A)
        self.assertEqual(window.svg_bytes, SVG_A)
        self.assertEqual(metadata['presentation']['annotations'], [{'text': 'Captured annotation A'}])

    def test_same_source_different_pages_survive_other_window_close(self):
        # Closing a comparison must not destroy its owner or another snapshot.
        owner = QWidget()
        owner.imported_data = {'data-a': object()}
        imported_data = owner.imported_data.copy()
        self.addCleanup(owner.close)
        first = self.window(page(SVG_A), parent=owner)
        second = self.window(page(SVG_B, owner=('data-a', 4, 5, 8, 12)), parent=owner)
        first.show()
        second.show()
        self.assertTrue(first.isTopLevel())
        self.assertEqual(first.modality(), Qt.NonModal)
        self.assertEqual(first.transientParent(), owner.windowHandle())
        first.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertFalse(isValid(first))
        self.assertTrue(isValid(owner))
        self.assertEqual(owner.imported_data, imported_data)
        self.assertTrue(second.isVisible())
        self.assertEqual(second.svg_bytes, SVG_B)
        saved = second.export_svg(Path(self.folder.name) / 'second.svg')
        self.assertEqual(saved.read_bytes(), SVG_B)

    def test_native_export_buttons_use_snapshot_even_with_arbitrary_selected_names(self):
        # Resolving a source from the output filename would lose original identity.
        window = self.window(png_dpi=144)
        folder = Path(self.folder.name)
        with patch.object(window, '_select_export_path',
                          return_value=str(folder / 'different-svg-name.svg')):
            self.click(window, 'comparisonExportSvg')
        with patch.object(window, '_select_export_path',
                          return_value=str(folder / 'different-png-name.png')):
            self.click(window, 'comparisonExportPng')
        self.assertEqual((folder / 'different-svg-name.svg').read_bytes(), SVG_A)
        with Image.open(folder / 'different-png-name.png') as image:
            self.assertEqual(image.size, (288, 144))
            self.assertEqual(image.info['Source'], SOURCE)
            self.assertEqual(json.loads(image.info['SpectraAnalyzer'])['source_path'], SOURCE)
        with patch.object(window, '_select_export_path', return_value=''):
            self.click(window, 'comparisonExportSvg')
        self.assertEqual(len(list(folder.iterdir())), 2)

    def test_existing_export_is_preserved_and_dialog_reports_failure(self):
        # A save-dialog choice must not bypass the writer's exclusive creation.
        window = self.window()
        output = Path(self.folder.name) / 'existing.svg'
        output.write_bytes(b'existing file')
        with self.assertRaises(FileExistsError):
            window.export_svg(output)
        with patch.object(window, '_select_export_path', return_value=str(output)):
            self.click(window, 'comparisonExportSvg')
        self.assertEqual(output.read_bytes(), b'existing file')
        self.assertIn('Failed', self.item(window, 'comparisonStatus').property('text'))

    def test_capture_label_identifies_source_title_and_versions_and_rejects_invalid_inputs(self):
        # A missing identity or invalid DPI could disguise/export the wrong capture.
        window = self.window()
        self.assertIn(SOURCE, self.item(window, 'comparisonSource').property('text'))
        self.assertIn('Capture A', self.item(window, 'comparisonSource').property('text'))
        for version in ('3', '5', '7', '11'):
            self.assertIn(version, self.item(window, 'comparisonSource').property('text'))
        self.assertIn('comparison', window.title().lower())
        self.assertIn('300', self.item(window, 'comparisonDimensions').property('text'))
        source = self.item(window, 'comparisonSource')
        plain = QQmlExpression(QQmlEngine.contextForObject(source), source, 'textFormat === TextEdit.PlainText')
        self.assertEqual(plain.evaluate(), (True, False))
        for source_label in ('', '   ', None):
            with self.subTest(source_label=source_label), self.assertRaisesRegex(ValueError, 'source'):
                self.window_class(page(), source_label=source_label, metadata={})
        for dpi in (0, -10, True, 1.5):
            with self.subTest(dpi=dpi), self.assertRaisesRegex(ValueError, 'DPI'):
                self.window_class(page(), source_label=SOURCE, metadata={}, png_dpi=dpi)
        with self.assertRaisesRegex(ValueError, 'page'):
            self.window_class(None, source_label=SOURCE, metadata={})


if __name__ == '__main__':
    unittest.main()
