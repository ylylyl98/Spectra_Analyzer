"""Observable inward tick contrast on the shared heatmap background."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import unittest
import xml.etree.ElementTree as ET

import numpy as np
from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LogNorm, Normalize, to_hex
from matplotlib.figure import Figure
from matplotlib.lines import TICKUP, TICKDOWN, TICKLEFT, TICKRIGHT
from PySide6.QtWidgets import QApplication

from spectra_app import svg_export
from spectra_app.main_window import MainWindow
from spectra_app.plot_presentation import AxisPresentation, HeatmapSnapshot, PlotPresentation
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])
NS = {'s': svg_export.SVG_NS}


def stroke_point(line, renderer):
    direction = {TICKUP: (0, 1), TICKDOWN: (0, -1), TICKLEFT: (-1, 0), TICKRIGHT: (1, 0)}[line.get_marker()]
    return line.get_transform().transform(line.get_xydata()[0]) + np.array(direction) * renderer.points_to_pixels(line.get_markersize()) * .6


def rendered_stroke_is_white(canvas, line):
    pixels = np.asarray(canvas.buffer_rgba())
    x, y = stroke_point(line, canvas.get_renderer())
    row, col = int(pixels.shape[0] - y), int(x)
    # Antialiasing may split a subpixel stroke across two pixels.
    return np.max(np.min(pixels[row-1:row+2, col-1:col+2, :3], axis=2)) > 200


class HeatmapTickContrastTests(unittest.TestCase):
    def axes(self):
        figure = Figure(figsize=(4, 3), dpi=144)
        canvas = FigureCanvasAgg(figure)
        ax = figure.add_axes([.2, .2, .7, .7])
        svg_export.apply_heatmap_axis_style(ax, PlotPresentation())
        return figure, canvas, ax

    def update(self, ax, canvas):
        helper = getattr(svg_export, '_update_heatmap_tick_contrast', None)
        self.assertIsNotNone(helper, 'The shared local-background tick contrast is missing')
        canvas.draw()
        changed = helper(ax, canvas.get_renderer())
        canvas.draw()
        return changed

    def assert_color(self, line, expected):
        self.assertEqual(to_hex(line.get_color()), expected)
        self.assertEqual(to_hex(line.get_markeredgecolor()), expected)

    def test_formal_svg_and_png_render_white_dark_jet_ticks_with_black_numbers(self):
        snapshot = HeatmapSnapshot(x=[0., 1., 2.], y=[0., 1., 2.], z=np.zeros((3, 3)),
                                   cmap='jet', color_limits=(0., 1.), x_limits=(0., 2.), y_limits=(0., 2.))
        p = PlotPresentation(axis=AxisPresentation(x_major_count=3, y_major_count=3))
        page = svg_export.render_hybrid_svg(snapshot, p)
        root = ET.fromstring(page.svg_bytes)
        self.assertEqual(len(root.findall('.//s:image', NS)), 1)
        axes = [root.find(f".//s:g[@id='matplotlib.axis_{index}']", NS) for index in (1, 2)]
        marks = [node for axis in axes for group in axis.findall('.//s:g', NS)
                 if group.get('id', '').startswith(('xtick_', 'ytick_'))
                 for node in group.findall('.//s:use', NS)]
        self.assertGreater(len(marks), 6)
        self.assertTrue(all('stroke: #ffffff' in node.get('style', '') for node in marks),
                        'Dark heatmap ticks must export as native white SVG strokes')
        labels = [node for axis in axes for group in axis.findall('.//s:g', NS)
                  if group.get('id', '').startswith(('xtick_', 'ytick_'))
                  for node in group.findall('.//s:text', NS)]
        self.assertTrue(labels)
        self.assertTrue(all('fill: #ffffff' not in node.get('style', '') for node in labels))
        bar = root.find(".//s:g[@id='matplotlib.axis_3']", NS)
        self.assertTrue(all('stroke: #000000' in node.get('style', '')
                            for node in bar.findall('.//s:use', NS)))
        image = svg_export.render_svg_image(page, dpi=144)
        # The middle bottom tick extends 4 pt inward from the exact frame.
        left, bottom, width, height = page.axes_bounds
        x = round((left+width/2)*image.width())
        y = round((1-bottom)*image.height()-4*.6*2)
        values = [image.pixelColor(xx, yy).getRgb()[:3]
                  for xx in range(x-1, x+2) for yy in range(y-1, y+2)]
        self.assertGreater(max(min(value) for value in values), 200)

    def test_actual_major_and_minor_backgrounds_include_blank_mask_and_alpha(self):
        _figure, canvas, ax = self.axes()
        values = np.ma.array([[0., .65, 0., 0.], [0., .65, 0., 0.]], mask=[[0, 0, 1, 0]]*2)
        mesh = ax.pcolormesh(np.arange(5), [0., 1., 2.], values,
                             cmap=colormaps['jet'].with_extremes(bad=svg_export.MISSING_COLOR),
                             norm=Normalize(0, 1), edgecolors='none')
        ax.set_xlim(-1., 5.); ax.set_ylim(.1, 1.9)
        ax.set_xticks([-.5, .5, 1.5, 2.5, 3.5, 4.5])
        ax.set_xticks([-.75, .25, 1.25, 2.25, 3.25, 4.25], minor=True)
        before = values.copy(), ax.get_xlim(), ax.get_ylim(), mesh.norm
        self.update(ax, canvas)
        expected = ['#000000', '#ffffff', '#000000', '#000000', '#ffffff', '#000000']
        for ticks in (ax.xaxis.get_major_ticks(), ax.xaxis.get_minor_ticks()):
            self.assertEqual(len(ticks), 6)
            for tick, color in zip(ticks, expected):
                self.assert_color(tick.tick1line, color)
                self.assertEqual(to_hex(tick.label1.get_color()), '#000000')
        self.assertTrue(rendered_stroke_is_white(canvas, ax.xaxis.get_major_ticks()[1].tick1line))
        mesh.set_alpha(.25)
        self.update(ax, canvas)
        self.assertTrue(all(to_hex(t.tick1line.get_markeredgecolor()) == '#000000'
                            for t in ax.xaxis._update_ticks()))
        np.testing.assert_equal(values, before[0])
        self.assertEqual((ax.get_xlim(), ax.get_ylim()), before[1:3])
        self.assertIs(mesh.norm, before[3])

    def test_current_transforms_regions_and_palette_norm_changes_refresh_reused_ticks(self):
        _figure, canvas, ax = self.axes()
        # An irregular 2-D mesh and overlapping regional mesh use their real draw order.
        x = np.array([[0., 1., 2.], [0., .8, 2.], [0., 1.2, 2.]])
        y = np.array([[0., 0., 0.], [1., .8, 1.], [2., 2., 2.]])
        mesh = ax.pcolormesh(x, y, np.zeros((2, 2)), cmap='jet', norm=Normalize(0, 1), edgecolors='none')
        ax.pcolormesh([1., 2.], [0., 2.], [[.65]], cmap='jet', norm=Normalize(0, 1), edgecolors='none')
        ax.set_xlim(0., 2.); ax.set_ylim(.1, 1.9); ax.set_xticks([.5, 1.5])
        for limits in ((0., 2.), (2., 0.)):
            ax.set_xlim(*limits)
            self.update(ax, canvas)
            for tick, color in zip(ax.xaxis.get_major_ticks(), ['#ffffff', '#000000']):
                self.assert_color(tick.tick1line, color)
        ax.set_ylim(1.9, .1)
        self.update(ax, canvas)
        self.assert_color(ax.xaxis.get_major_ticks()[0].tick1line, '#ffffff')
        mesh.set_array(np.full((2, 2), .65)); self.update(ax, canvas)
        self.assert_color(ax.xaxis.get_major_ticks()[0].tick1line, '#000000')
        mesh.set_norm(Normalize(.65, 1.)); self.update(ax, canvas)
        self.assert_color(ax.xaxis.get_major_ticks()[0].tick1line, '#ffffff')
        mesh.set_cmap('gray_r'); self.update(ax, canvas)
        self.assert_color(ax.xaxis.get_major_ticks()[0].tick1line, '#000000')
        ax.tick_params(which='both', direction='out'); self.update(ax, canvas)
        self.assertTrue(all(to_hex(t.tick1line.get_markeredgecolor()) == '#000000'
                            for t in ax.xaxis._update_ticks()))
        # After panning, recycled marks on white uncovered space must reset.
        ax.tick_params(which='both', direction='in'); mesh.set_cmap('jet')
        ax.set_xlim(-2., 0.); ax.set_xticks([-1.5, -.5]); self.update(ax, canvas)
        self.assertTrue(all(to_hex(t.tick1line.get_markeredgecolor()) == '#000000'
                            for t in ax.xaxis.get_major_ticks()))
        # Unsupported custom markers use black rather than breaking redraw.
        custom = ax.xaxis.get_major_ticks()[0].tick1line
        custom.set_marker(np.array([[0., 0.], [0., 1.]]))
        custom.set_color('white'); custom.set_markeredgecolor('white')
        try:
            self.update(ax, canvas)
        except ValueError as exc:
            self.fail(f'Unsupported custom marks must remain usable during redraw: {exc}')
        self.assert_color(custom, '#000000')

    def test_exchanged_and_log_coordinates_use_the_actual_inward_stroke(self):
        _figure, canvas, ax = self.axes()
        # Exchange places the bright half above the dark half.
        mesh = ax.pcolormesh([1., 10., 100.], [0., 1., 2.], [[0., 0.], [.65, .65]],
                             cmap='jet', norm=Normalize(0, 1), edgecolors='none')
        ax.set_xscale('log'); ax.set_xlim(1., 100.); ax.set_ylim(0., 2.)
        ax.set_yticks([.5, 1.5]); ax.set_yticks([.25, 1.25], minor=True)
        self.update(ax, canvas)
        for ticks in (ax.yaxis.get_major_ticks(), ax.yaxis.get_minor_ticks()):
            for tick, color in zip(ticks, ['#ffffff', '#000000']):
                self.assert_color(tick.tick1line, color)
        mesh.set_array(np.ones((2, 2))); mesh.set_norm(LogNorm(1., 100.))
        self.update(ax, canvas)
        self.assertTrue(all(to_hex(t.tick1line.get_markeredgecolor()) == '#ffffff'
                            for t in ax.yaxis._update_ticks()))


class LiveTickContrastTests(unittest.TestCase):
    def setUp(self):
        folder = isolate_settings(self).parent
        path = folder/'contrast.dat'
        path.write_text('Energy(eV) 0 1 2\n1 0 0 0\n1.5 0 0 0\n2 0 0 0', encoding='utf-8')
        self.window = MainWindow()
        self.window._show_error = self.fail
        self.window._load_dat_file_direct(path)
        self.window.set_plot_presentation(replace(self.window.workspace.active.presentation,
            axis=AxisPresentation(x_major_count=3, y_major_count=3)))
        # Inspect pixels at a defined display size, independent of an unshown Fit viewport.
        self.window.plot_page.set_zoom('100%')

    def tearDown(self):
        self.window.close(); APP.processEvents()

    def settle(self):
        for _ in range(8):
            APP.processEvents()

    def test_palette_only_draw_repaints_live_pixels_and_stops_scheduling(self):
        w = self.window
        w.cmap_combo.setCurrentText('RdBu_r')
        w._heat_mesh.set_norm(Normalize(0., 1.))
        w._heat_mesh.set_cmap('gray_r')
        w._render_plot_page(); w._layout_live_plots(); w.canvas.draw(); self.settle()
        events = []
        cid = w.canvas.mpl_connect('draw_event', lambda event:
                                  events.append(getattr(w, '_laying_out_live_plots', False)))
        try:
            index = w.cmap_combo.findText('jet')
            self.assertGreaterEqual(index, 0)
            self.assertNotEqual(w.cmap_combo.currentIndex(), index)
            w.cmap_combo.setCurrentIndex(index)
            # The separate formal-preview debounce also measures live layout.
            # Isolate the retained canvas's palette-only repaint contract.
            w._plot_render_timer.stop()
            self.settle()
            tick = w._heat_ax.xaxis.get_major_ticks()[1]
            self.assertEqual(to_hex(tick.tick1line.get_markeredgecolor()), '#ffffff')
            self.assertTrue(rendered_stroke_is_white(w.canvas, tick.tick1line),
                            'Changing only the colormap must repaint the live tick pixels')
            count = len(events); self.settle()
            self.assertEqual(len(events), count, 'Tick contrast must settle without a queued draw loop')
            self.assertFalse(any(events), 'Palette-only contrast must not require live layout')
            self.assertLessEqual(count, 3)
            self.assertFalse(w.canvas._draw_pending)
            self.assertFalse(w._tick_contrast_timer.isActive())
            for zoom in ('100%', '150%', '200%'):
                w.plot_page.set_zoom(zoom); w.canvas.draw(); self.settle()
                self.assertTrue(rendered_stroke_is_white(w.canvas, w._heat_ax.xaxis.get_major_ticks()[1].tick1line))
            original = w.canvas.device_pixel_ratio
            try:
                w.canvas._set_device_pixel_ratio(2.)
                w._layout_live_plots(); w.canvas.draw(); self.settle()
                self.assertTrue(rendered_stroke_is_white(w.canvas, w._heat_ax.xaxis.get_major_ticks()[1].tick1line))
            finally:
                w.canvas._set_device_pixel_ratio(original)
        finally:
            w.canvas.mpl_disconnect(cid)


if __name__ == '__main__':
    unittest.main()
