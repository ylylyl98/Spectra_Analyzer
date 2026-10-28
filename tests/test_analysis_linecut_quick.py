"""Real QML scene and Agg display boundary for the auxiliary linecut."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import QCoreApplication, QEvent, QSize
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication, QWidget

from tests.workbook_quick_helpers import item, pump


APP = QApplication.instance() or QApplication([])


class AnalysisLinecutQuickTests(unittest.TestCase):
    def setUp(self):
        self.hosts = []

    def tearDown(self):
        for host in self.hosts:
            host.close()
            host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def preview(self, show=True):
        from spectra_app.analysis_linecut_preview import AnalysisLinecutPreview
        figure = Figure(figsize=(7., 1.8), layout='tight')
        ax = figure.add_subplot(111)
        x = np.linspace(1., 3., 101)
        baseline = np.full_like(x, .1)
        components = [np.exp(-((x - center) / .2) ** 2) for center in (1.6, 2.3)]
        y = sum(components) + baseline
        ax.plot(x, y + .02 * np.sin(x * 20), lw=1., label='Bound input')
        ax.plot(x, y, color='#a020f0', lw=1.3, label='Total fit')
        for component in components:
            ax.plot(x, component + baseline, lw=.7, alpha=.6)
        ax.scatter([1.6], [1.1], marker='^', color='#d62728', s=30, label='peak')
        ax.scatter([2.], [.2], marker='v', color='#2ca02c', s=30, label='dip')
        ax.set_title('Auxiliary linecut · Gate 4 · excluded from heatmap export', fontsize=9)
        ax.set_xlabel('Energy (eV)', fontsize=9)
        ax.tick_params(labelsize=8)
        ax.legend(fontsize=7, loc='best')
        host = AnalysisLinecutPreview(figure)
        self.hosts.append(host)
        host.resize(700, 190)
        if show:
            host.show()
            pump(60)
        return host, ax

    def test_real_quick_scene_displays_curves_labels_markers_and_legend(self):
        host, ax = self.preview()
        self.assertIsInstance(host, QQuickWidget)
        self.assertEqual(host.status(), QQuickWidget.Ready)
        self.assertTrue(host.rootObject().property('loadedReady'))
        self.assertEqual(host.minimumHeight(), 130)
        self.assertIn('excluded from heatmap export', host.accessibleName())
        self.assertEqual(host.findChildren(QWidget), [])
        self.assertEqual(item(host, 'analysisLinecutImage').property('source').toString(), host.imageUrl)
        self.assertEqual(len(ax.lines), 4)
        self.assertEqual(len(ax.collections), 2)
        self.assertEqual([text.get_text() for text in ax.legend_.texts],
                         ['Bound input', 'Total fit', 'peak', 'dip'])
        scene = host.grabFramebuffer()
        self.assertFalse(scene.isNull())
        colors = [scene.pixelColor(x, y) for y in range(0, scene.height(), 2)
                  for x in range(0, scene.width(), 2)]
        self.assertGreater(sum(c.red() < 180 and c.green() < 180 and c.blue() < 180
                               for c in colors), 150)
        self.assertGreater(sum(c.blue() > c.green() + 25 and c.red() > c.green() + 25
                               for c in colors), 30)
        # Locators also create labels beyond the displayed axis limits. Only
        # labels that Matplotlib actually paints must fit the actual raster.
        renderer = host.controller._canvas.get_renderer()
        xticks = [text for value, text in zip(ax.get_xticks(), ax.get_xticklabels())
                  if ax.get_xlim()[0] <= value <= ax.get_xlim()[1]]
        yticks = [text for value, text in zip(ax.get_yticks(), ax.get_yticklabels())
                  if ax.get_ylim()[0] <= value <= ax.get_ylim()[1]]
        for text in [ax.title, ax.xaxis.label, *xticks, *yticks, *ax.legend_.texts]:
            bounds = text.get_window_extent(renderer)
            self.assertGreaterEqual(bounds.x0, -1)
            self.assertGreaterEqual(bounds.y0, -1)
            self.assertLessEqual(bounds.x1, host.scene.provider._images[str(host.scene.provider._revision)].width() + 1)
            self.assertLessEqual(bounds.y1, host.scene.provider._images[str(host.scene.provider._revision)].height() + 1)

    def test_resize_uses_actual_dpr_once_and_preserves_canonical_artists(self):
        host, ax = self.preview()
        originals = [(line.get_xdata().copy(), line.get_ydata().copy()) for line in ax.lines]
        with patch.object(host.controller._canvas, 'draw', wraps=host.controller._canvas.draw) as draw:
            for width in range(710, 831, 10):
                host.resize(width, 220)
                host.schedule_render()
            pump(65)
            self.assertEqual(draw.call_count, 1)
        image = host.scene.provider._images[str(host.scene.provider._revision)]
        dpr = host.devicePixelRatioF()
        self.assertEqual(image.size(), QSize(round(host.width() * dpr), round(host.height() * dpr)))
        self.assertAlmostEqual(host.figure.dpi, host.controller._base_dpi * dpr)
        self.assertAlmostEqual(host.figure.get_figwidth(), host.width() / host.controller._base_dpi)
        for line, (x, y) in zip(ax.lines, originals):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)

    def test_coalesces_hidden_updates_flushes_latest_and_retires_callbacks(self):
        host, ax = self.preview(show=False)
        with patch.object(host.controller._canvas, 'draw', wraps=host.controller._canvas.draw) as draw:
            for value in range(20):
                ax.set_title(f'Latest {value}')
                host.schedule_render()
            pump(40)
            self.assertEqual(draw.call_count, 0)
            host.show()
            pump(60)
            self.assertEqual(draw.call_count, 1)
            self.assertEqual(ax.get_title(), 'Latest 19')
            for _ in range(20):
                host.schedule_render()
            host.hide()
            pump(40)
            self.assertEqual(draw.call_count, 1)
            host.show()
            pump(60)
            self.assertEqual(draw.call_count, 2)
            host.schedule_render()
            host.dispose()
            pump(40)
            self.assertEqual(draw.call_count, 2)
            self.assertEqual(host.scene.provider._images, {})

    def test_revision_pixels_immutable_bounded_and_display_does_not_export_or_fit(self):
        host, ax = self.preview()
        provider = host.scene.provider
        first_id = str(provider._revision)
        first = provider.requestImage(first_id, QSize(), QSize())
        first_bytes = bytes(first.constBits())
        arrays = [line.get_ydata().copy() for line in ax.lines]
        with (patch.object(host.figure, 'savefig', side_effect=AssertionError('Display exported')),
              patch('spectra_app.svg_export.render_hybrid_svg', side_effect=AssertionError('Display exported')),
              patch('spectra_app.fitting.fit_multi_peak', side_effect=AssertionError('Display refit')),
              patch('spectra_app.xy_analysis.detect_peaks', side_effect=AssertionError('Display detected'))):
            # The display only draws existing artists; no data/fit callback exists.
            ax.lines[1].set_color('orange')
            host.schedule_render()
            pump(45)
            self.assertEqual(bytes(first.constBits()), first_bytes)
            self.assertEqual(bytes(provider.requestImage(first_id, QSize(), QSize()).constBits()), first_bytes)
            for width in (720, 740, 760):
                host.resize(width, 190)
                pump(45)
        self.assertLessEqual(len(provider._images), 2)
        self.assertTrue(provider.requestImage(first_id, QSize(), QSize()).isNull())
        for line, y in zip(ax.lines, arrays):
            np.testing.assert_array_equal(line.get_ydata(), y)

    def test_external_controllers_detach_on_adapter_close_and_keep_canonical_renderer(self):
        from spectra_app.analysis_canvas import AnalysisCanvasView, AnalysisFigureCanvas
        from spectra_app.dat_canvas_controller import DatCanvasController
        from spectra_app.analysis_linecut_controller import AnalysisLinecutController
        from spectra_app.analysis_linecut_preview import AnalysisLinecutPreview
        for linecut in (False, True):
            figure = Figure(figsize=(3, 2))
            axis = figure.add_subplot(111)
            artist, = axis.plot([1, 2], [3, 4])
            controller = (AnalysisLinecutController(figure) if linecut else
                          DatCanvasController(AnalysisFigureCanvas(figure)))
            def host():
                view = (AnalysisLinecutPreview(figure, controller=controller) if linecut else
                        AnalysisCanvasView(controller.canvas, None, controller=controller))
                self.hosts.append(view)
                view.resize(400, 220)
                view.show()
                pump(45)
                return view
            first = host()
            first_route = first.scene.route
            latest = host()
            self.assertNotEqual(latest.scene.route, first_route)
            self.assertFalse(first.rootObject().isEnabled())
            first.resize(800, 450)
            first.close()
            pump()
            viewport = controller._viewport if linecut else (controller.viewportWidth, controller.viewportHeight)
            self.assertEqual(tuple(viewport), (400, 220))
            self.assertFalse(controller._disposed)
            self.assertIs(artist.axes, axis)
            latest.close()
            self.assertIsNone(controller._scene)
            self.assertFalse(controller._disposed)
            self.assertFalse(controller._last_frame.isNull())
            controller.dispose()
            controller.deleteLater()


if __name__ == '__main__':
    unittest.main()
