import unittest
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from spectra_app.image_export import _build_heatmap_export_figure, _cb_fmt_regional


class RegionalHeaderTests(unittest.TestCase):
    def make_figure(self, count):
        figure = Figure()
        FigureCanvasAgg(figure)
        heat = figure.add_subplot()
        heat.set_title("p3 8T 0.5TG-BG=0")
        figure.text(0, 0, "d²(ΔR/R)/dE² (a. u.)", gid="heatmap_signal_label")
        for index, limits in enumerate(((-8.9e4, 6e4), (-2.5e3, 2.4e3), (-6e-3, 8e-3))[:count]):
            x = np.linspace(1.64 + 0.35 * index / count, 1.64 + 0.35 * (index + 1) / count, 31)
            y = np.linspace(-10.5, 10.5, 51)
            values = np.sin(np.linspace(0, 6, 50))[:, None] * np.cos(np.linspace(0, 3, 30))[None, :]
            heat.pcolormesh(x, y, values * max(abs(v) for v in limits), vmin=limits[0], vmax=limits[1], cmap="RdBu_r")
        heat.set_xlabel("Energy (eV)")
        heat.set_ylabel("Gate (V)")
        return _build_heatmap_export_figure(figure, selected_axes=(heat,))

    def test_title_is_close_and_scientific_endpoints_do_not_overlap(self):
        for count in (2, 3):
            figure = self.make_figure(count)
            figure.canvas.draw()
            renderer = figure.canvas.get_renderer()
            title = figure.texts[0]
            gap = (title.get_window_extent(renderer).y0 - figure.axes[0].get_window_extent(renderer).y1) / figure.dpi
            self.assertAlmostEqual(gap, 0.04)
            self.assertEqual(title.get_fontsize(), 12)
            for bar in figure.axes[1:]:
                bar_gap = (bar.get_window_extent(renderer).y0 - figure.axes[0].get_window_extent(renderer).y1) / figure.dpi
                self.assertAlmostEqual(bar_gap, 0.04)
            labels = [*figure.texts, figure.axes[0].yaxis.get_offset_text()]
            labels += [tick for axis in figure.axes[1:] for tick in axis.get_xticklabels()]
            boxes = [label.get_window_extent(renderer) for label in labels if label.get_text()]
            for index, box in enumerate(boxes):
                self.assertTrue(all(not box.overlaps(other) for other in boxes[index + 1:]))
            self.assertEqual(_cb_fmt_regional(-2500), "-2.5e3")
            self.assertEqual(_cb_fmt_regional(2400), "2.4e3")

    def test_live_title_and_endpoints_remain_separate_at_canvas_sizes(self):
        from tests.test_image_export import ImageExportTests

        fixture = ImageExportTests()
        fixture.setUp()
        try:
            window = fixture.make_window(title="p3 8T 0.5TG-BG=0")
            window.signal_title_combo.setCurrentText("d²(ΔR/R)/dE² (a. u.)")
            presentation = (window.workspace.active or window._unregistered_session).presentation
            window.set_plot_presentation(replace(presentation, colorbar=replace(
                presentation.colorbar, width_in=1.2, gap_in=.5)))
            for count in (2, 3):
                window.color_scale_combo.setCurrentIndex(window.color_scale_combo.findData(str(count)))
                for mesh, limits in zip(window._heat_meshes, ((-89000, 60000), (-2500, 2400), (-0.006, 0.008))):
                    mesh.set_clim(*limits)
                for size in ((6.4, 4.8), (8, 6)):
                    window.figure.set_size_inches(*size)
                    window._layout_live_plots()
                    window.canvas.draw()
                    renderer = window.canvas.get_renderer()
                    title = window._heat_ax.title
                    title_box = title.get_window_extent(renderer)
                    frame_box = window._heat_ax.get_window_extent(renderer)
                    self.assertTrue(frame_box.contains(title_box.x0, title_box.y0))
                    self.assertTrue(frame_box.contains(title_box.x1, title_box.y1))
                    saved = (window.workspace.active or window._unregistered_session).presentation
                    scale = 96 * window._analysis_display_scale() * window.canvas.device_pixel_ratio / window.figure.dpi
                    for bar in window._heat_colorbars:
                        bar_gap = (bar.ax.get_window_extent(renderer).y0 - window._heat_ax.get_window_extent(renderer).y1) / window.figure.dpi
                        self.assertAlmostEqual(bar_gap, saved.colorbar.frame_gap_in * scale)
                    labels = [title, window._heat_signal_label, window._heat_ax.yaxis.get_offset_text()]
                    labels += [tick for bar in window._heat_colorbars for tick in bar.ax.get_xticklabels()]
                    boxes = [label.get_window_extent(renderer) for label in labels if label.get_text()]
                    for index, box in enumerate(boxes):
                        self.assertTrue(all(not box.overlaps(other) for other in boxes[index + 1:]))
        finally:
            fixture.tearDown()
