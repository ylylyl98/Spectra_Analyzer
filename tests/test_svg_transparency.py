"""Final SVG payloads retain data colors over a transparent page and axes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import ListedColormap
from PySide6.QtCore import QByteArray, QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from spectra_app.dataset_state import Workspace
from spectra_app.plot_presentation import HeatmapSnapshot, PlotPresentation
from spectra_app.svg_export import render_hybrid_svg, render_svg_image
from spectra_app.workbook_state import add_curve, create_comparison, create_workbook, register_xy_sheet
from spectra_app.xy_plot_page import XYPlotPage

APP = QApplication.instance() or QApplication([])


def render_svg(svg, background=Qt.transparent, size=(700, 500)):
    renderer = QSvgRenderer(QByteArray(svg))
    assert renderer.isValid()
    image = QImage(*size, QImage.Format_ARGB32)
    image.fill(background)
    painter = QPainter(image)
    try:
        renderer.render(painter, QRectF(0, 0, *size))
    finally:
        painter.end()
    return image


def heatmap_snapshot(alpha=1.):
    return HeatmapSnapshot(x=[0., 1., 2., 3.], y=[0., 1., 2.],
        z=np.tile([0., 0., 1., 1.], (3, 1)), x_label='X', y_label='Y', title='',
        signal_label='Signal', color_limits=(0., 1.), cmap='svg-transparency-test',
        alpha=alpha, x_limits=(-1.5, 4.5), y_limits=(-.5, 2.5))


def xy_comparison(workspace):
    book = create_workbook(workspace)
    sheet = register_xy_sheet(workspace, book.workbook_id, 'Source',
        np.array([[0., .25, .75], [1., .25, .75]]), ['X', 'White', 'Red'])
    comparison = create_comparison(workspace)
    comparison.legend_visible = False
    for column, color in ((1, '#ffffff'), (2, '#cc3300')):
        curve = add_curve(workspace, comparison.comparison_id, sheet.sheet_id, 0, column)
        curve.color = color
        curve.width = 5.
        curve.style = 'line_symbol'
    return comparison


class SvgTransparencyTests(unittest.TestCase):
    def setUp(self):
        colormaps.register(ListedColormap(['#ffffff', '#cc3300']), name='svg-transparency-test')
        self.addCleanup(colormaps.unregister, 'svg-transparency-test')

    def test_heatmap_background_is_transparent_and_white_data_retains_its_alpha(self):
        for alpha in (1., .5):
            with self.subTest(alpha=alpha):
                snapshot = heatmap_snapshot(alpha)
                original = snapshot.z.copy()
                page = render_hybrid_svg(snapshot, PlotPresentation())
                size = tuple(round(value * 150) for value in page.figure_size_in)
                image = render_svg(page.svg_bytes, size=size)
                colored = render_svg(page.svg_bytes, QColor('#204060'), size=size)
                left, bottom, width, height = page.axes_bounds
                def point(x):
                    return (round((left + (x + 1.5) / 6. * width) * size[0]),
                            round((1. - bottom - height / 2.) * size[1]))
                for location in ((0, 0), point(-1.)):
                    self.assertEqual(image.pixelColor(*location).alpha(), 0)
                    self.assertEqual(colored.pixelColor(*location), QColor('#204060'))
                for x, rgb in ((.5, (255, 255, 255)), (2.5, (204, 51, 0))):
                    data = image.pixelColor(*point(x))
                    self.assertLessEqual(abs(data.alpha() - 255 * alpha), 1)
                    for actual, expected in zip(data.getRgb()[:3], rgb):
                        self.assertLessEqual(abs(actual - expected), 1)
                    for actual, value, background in zip(colored.pixelColor(*point(x)).getRgb()[:3], rgb, (32, 64, 96)):
                        self.assertLessEqual(abs(actual - (value * alpha + background * (1 - alpha))), 2)
                self.assertEqual(render_svg_image(page, dpi=150).pixelColor(0, 0), QColor('white'))
                np.testing.assert_array_equal(snapshot.z, original)

    def test_xy_background_is_transparent_but_white_curves_and_png_stay_visible(self):
        workspace = Workspace()
        comparison = xy_comparison(workspace)
        page = XYPlotPage(workspace, comparison.comparison_id)
        self.addCleanup(page.deleteLater)
        svg = page.svg_bytes()
        image = render_svg(svg)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        interior = [image.pixelColor(x, y) for y in range(20, 480) for x in range(100, 600)]
        self.assertGreater(sum(color.alpha() == 0 for color in interior), len(interior) * .7)
        self.assertGreater(sum(color == QColor('white') for color in interior), 100)
        self.assertGreater(sum(color == QColor('#cc3300') for color in interior), 100)
        colored = render_svg(svg, QColor('#204060'))
        self.assertEqual(colored.pixelColor(0, 0), QColor('#204060'))
        with TemporaryDirectory() as directory:
            png = page.export(Path(directory) / 'xy.png')
            self.assertEqual(QImage(str(png)).pixelColor(0, 0), QColor('white'))


@unittest.skipUnless(QApplication.platformName() == 'offscreen', 'Clipboard test must not touch the system clipboard')
class SvgClipboardIdentityTests(unittest.TestCase):
    def setUp(self):
        from spectra_app.main_window import MainWindow
        from tests.settings_helpers import isolate_settings
        self.folder = isolate_settings(self).parent
        self.window = MainWindow()
        self.window._show_error = self.fail
        self.addCleanup(self.dispose)

    def dispose(self):
        from tests.settings_helpers import wait_for_background_jobs
        wait_for_background_jobs(self.window)
        APP.clipboard().clear()
        self.window.close()
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def clipboard_svg(self):
        mime = APP.clipboard().mimeData()
        self.assertEqual(mime.formats(), ['image/svg+xml'])
        self.assertFalse(mime.hasImage())
        return bytes(mime.data('image/svg+xml'))

    def test_heatmap_copy_and_export_share_transparent_bytes(self):
        path = self.folder / 'heatmap.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)))
        self.window._load_dat_file_direct(path)
        self.window.open_graph_document()
        copied = self.window.copy_graph_svg()
        svg = self.clipboard_svg()
        self.assertEqual(svg, copied.svg_bytes)
        exported = self.window.export_plot_page('svg')
        self.assertEqual(svg, exported.read_bytes())
        self.assertEqual(render_svg(svg).pixelColor(0, 0).alpha(), 0)

    def test_xy_copy_and_export_share_transparent_bytes(self):
        comparison = xy_comparison(self.window.workspace)
        page = self.window.open_xy_comparison(comparison.comparison_id)
        self.window.copy_graph_svg()
        svg = self.clipboard_svg()
        exported = page.export(self.folder / 'xy.svg')
        self.assertEqual(svg, exported.read_bytes())
        self.assertEqual(render_svg(svg).pixelColor(0, 0).alpha(), 0)
        self.assertEqual(svg, page.svg_bytes())
