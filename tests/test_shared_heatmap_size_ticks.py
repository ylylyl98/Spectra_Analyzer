"""Shared physical frame and Origin-style linear tick contracts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties, findfont
from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from spectra_app.main_window import MainWindow
from spectra_app.plot_presentation import AxisPresentation, ColorbarTicks, HeatmapSnapshot, PlotAnnotation, PlotPresentation
from spectra_app.plot_templates import FormatStyle
from spectra_app.svg_export import apply_heatmap_axis_style, render_hybrid_svg
from spectra_app import svg_export
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])
if os.name == 'nt' and os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
    # Windows offscreen needs native font files for stable widget metrics.
    for family in ('Consolas', 'Arial', 'Segoe UI'):
        QFontDatabase.addApplicationFont(findfont(FontProperties(family=family)))


def assert_compact_axis_boxes(test, ax, renderer, points_to_pixels, *, inside):
    """Catch whole-row corner padding and stale endpoint alignment in real text."""
    box = ax.get_window_extent(renderer)
    labels = [[tick.label1 for tick in axis._update_ticks()
               if tick.label1.get_visible() and tick.label1.get_text()]
              for axis in (ax.xaxis, ax.yaxis)]
    boxes = [[label.get_window_extent(renderer) for label in row] for row in labels]
    gap = (1 if inside else 5) * points_to_pixels
    for item in boxes[0]:
        test.assertAlmostEqual(box.y0 - item.y1, gap, places=5)
    for item in boxes[1]:
        test.assertAlmostEqual(box.x0 - item.x1, gap, places=5)
    test.assertAlmostEqual(min(item.y0 for item in boxes[0])
                           - ax.xaxis.label.get_window_extent(renderer).y1,
                           points_to_pixels, places=5)
    test.assertAlmostEqual(min(item.x0 for item in boxes[1])
                           - ax.yaxis.label.get_window_extent(renderer).x1,
                           points_to_pixels, places=5)
    test.assertFalse(any(x.overlaps(y) for x in boxes[0] for y in boxes[1]))
    left_index = min(range(len(boxes[0])), key=lambda i: boxes[0][i].x0)
    for index, label in enumerate(labels[0]):
        if index != left_index:
            test.assertEqual(label.get_horizontalalignment(), 'center')


class TickRecordsTests(unittest.TestCase):
    def test_qtsvg_clips_long_literal_multiline_title_inside_frame(self):
        snapshot = HeatmapSnapshot(x=[1., 2.], y=[0., 1.], z=np.ones((2, 2)),
                                   x_label='Energy (eV)', y_label='Gate (V)', signal_label='PL (arb. units)')
        title = '\n'.join(['Literal $text$ <&> ' + 'W'*70] + ['W'*70]*24)
        for frame_size in ((2.9, 2.8), (None, None)):
            p = PlotPresentation(width_in=5., height_in=4.5,
                                 frame_width_in=frame_size[0], frame_height_in=frame_size[1])
            blank = render_hybrid_svg(snapshot, p)
            decorated = render_hybrid_svg(replace(snapshot, title=title), p)
            self.assertEqual(decorated.axes_bounds, blank.axes_bounds)
            self.assertEqual(decorated.figure_size_in, blank.figure_size_in)
            group = ET.fromstring(decorated.svg_bytes).find(
                ".//s:g[@id='page-title-overlay']", {'s': svg_export.SVG_NS})
            self.assertEqual('\n'.join(node.text or '' for node in group.findall(
                './/s:text', {'s': svg_export.SVG_NS})), title)
            for dpi in (144, 300):
                with self.subTest(frame_size=frame_size, dpi=dpi):
                    images = [svg_export.render_svg_image(page, dpi=dpi).convertToFormat(
                        QImage.Format.Format_RGBA8888) for page in (blank, decorated)]
                    pixels = [np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
                        image.height(), image.width(), 4) for image in images]
                    changed = np.any(pixels[0] != pixels[1], axis=2)
                    left, bottom, width, height = decorated.axes_bounds
                    image_height, image_width = changed.shape
                    # Include only pixels intersecting the exact frame bounds.
                    x0, x1 = int(np.floor(left*image_width)), int(np.ceil((left+width)*image_width))
                    y0 = int(np.floor((1-bottom-height)*image_height))
                    y1 = int(np.ceil((1-bottom)*image_height))
                    inside = changed[y0:y1, x0:x1]
                    self.assertGreater(np.count_nonzero(inside), 0, 'Title was not rendered.')
                    outside = changed.copy(); outside[y0:y1, x0:x1] = False
                    self.assertEqual(np.count_nonzero(outside), 0,
                                     'QtSvg painted title text outside the heatmap frame.')

    def test_compact_rendered_gaps_keep_endpoint_counts_under_flip_and_exchange(self):
        snapshot = HeatmapSnapshot(x=[0., 5., 10.], y=[1., 2., 3.], z=np.ones((3, 3)),
                                   x_limits=(0., 10.), y_limits=(1., 3.),
                                   x_label='Energy (eV)', y_label='Gate (V)',
                                   title='Condition A', signal_label='PL (arb. units)')
        audit = svg_export._audit_layout
        for inside, exchange, reverse in ((True, False, False), (True, False, True),
                                         (True, True, True), (False, False, False)):
            p = PlotPresentation(width_in=5., height_in=5., frame_width_in=2.98, frame_height_in=2.68,
                                 axis=AxisPresentation(ticks_inside=inside, exchange_xy=exchange,
                                                       flip_x=reverse, flip_y=reverse,
                                                       x_major_count=5, y_major_count=5))
            def check(figure):
                audit(figure)
                ax = figure.axes[0]
                assert_compact_axis_boxes(self, ax, figure.canvas.get_renderer(), 1., inside=inside)
                self.assertEqual(len(ax.get_xticks()), 5)
                self.assertEqual(len(ax.get_yticks()), 5)
            with self.subTest(inside=inside, exchange=exchange, reverse=reverse), \
                    patch.object(svg_export, '_audit_layout', side_effect=check):
                page = render_hybrid_svg(snapshot, p)
                np.testing.assert_allclose(page.plot_size_in, [2.98, 2.68])

    def test_new_defaults_and_legacy_missing_geometry_are_distinct(self):
        self.assertEqual(PlotPresentation().width_in, 91.5/25.4)
        self.assertEqual(FormatStyle().width_in, 91.5/25.4)
        self.assertEqual((PlotPresentation().frame_width_in, PlotPresentation().frame_height_in), (2.9, 2.8))
        self.assertEqual((FormatStyle().frame_width_in, FormatStyle().frame_height_in), (2.9, 2.8))
        self.assertEqual(PlotPresentation.from_dict({}).width_in, 3.3)
        self.assertEqual(PlotPresentation.from_dict({'width_in': 4.1}).width_in, 4.1)
        old = PlotPresentation().to_dict()
        old.pop('frame_width_in'); old.pop('frame_height_in')
        self.assertIsNone(PlotPresentation.from_dict(old).frame_width_in)
        style = FormatStyle().to_dict()
        style.pop('frame_width_in'); style.pop('frame_height_in')
        for field in ('x_major_count', 'y_major_count', 'x_minor_count', 'y_minor_count'):
            style['axis'].pop(field, None)
        restored = FormatStyle.from_dict(style)
        self.assertIsNone(restored.frame_width_in)
        self.assertEqual(restored.axis.x_minor_count, 1)
        with self.assertRaises(ValueError):
            AxisPresentation(x_major_step=.2, x_major_count=5)
        for payload in ({'x_major_count': 1}, {'x_major_count': 101}, {'x_minor_count': 21}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                AxisPresentation.from_dict(payload)

    def test_exact_visible_major_and_minor_counts_preserve_endpoints_and_flip(self):
        figure = Figure(); FigureCanvasAgg(figure)
        ax = figure.add_subplot(); ax.set_xlim(1.13, 2.07); ax.set_ylim(3., -2.)
        p = PlotPresentation(axis=AxisPresentation(x_major_count=7, y_major_count=6,
                                                   x_minor_count=3, y_minor_count=2))
        apply_heatmap_axis_style(ax, p); figure.canvas.draw()
        np.testing.assert_allclose(ax.get_xticks(), np.linspace(1.13, 2.07, 7))
        np.testing.assert_allclose(ax.get_yticks(), np.linspace(-2., 3., 6))
        self.assertEqual(len(ax.xaxis.get_minorticklocs()), 18)
        self.assertEqual(len(ax.yaxis.get_minorticklocs()), 10)
        self.assertEqual(PlotPresentation.from_dict(p.to_dict()), p)
        apply_heatmap_axis_style(ax, replace(p, axis=replace(p.axis, x_minor_count=0)))
        self.assertEqual(len(ax.xaxis.get_minorticklocs()), 0)
        ax.set_xscale('log')
        with self.assertRaisesRegex(ValueError, 'linear'):
            apply_heatmap_axis_style(ax, p)

    def test_crowded_manual_count_is_rejected_without_reducing_choice(self):
        snapshot = HeatmapSnapshot(x=np.linspace(1.13, 2.07, 10), y=[0., 1.],
                                   z=np.ones((2, 10)), x_label='Energy (eV)', y_label='Gate (V)')
        p = PlotPresentation(width_in=5., height_in=5., axis=AxisPresentation(x_major_count=50))
        with self.assertRaisesRegex(ValueError, 'overlap'):
            render_hybrid_svg(snapshot, p)
        self.assertEqual(p.axis.x_major_count, 50)

    def test_nonlinear_minor_counts_reject_manual_values_and_restore_scale_defaults(self):
        figure = Figure(); FigureCanvasAgg(figure)
        ax = figure.add_subplot(); ax.set_xscale('log', base=2, subs=[1.5]); ax.set_xlim(1, 64)
        p = PlotPresentation()
        apply_heatmap_axis_style(ax, p)
        original = ax.xaxis.get_minorticklocs().copy()
        self.assertGreater(len(original), 0)
        major = ax.xaxis.get_major_locator()
        with self.assertRaisesRegex(ValueError, 'linear'):
            apply_heatmap_axis_style(ax, replace(p, axis=replace(p.axis, x_minor_count=3)))
        apply_heatmap_axis_style(ax, replace(p, axis=replace(p.axis, x_minor_count=0)))
        self.assertEqual(len(ax.xaxis.get_minorticklocs()), 0)
        apply_heatmap_axis_style(ax, p)
        np.testing.assert_allclose(ax.xaxis.get_minorticklocs(), original)
        self.assertIs(ax.xaxis.get_major_locator(), major)

    def test_formal_count_uses_exact_visible_endpoints_after_exchange(self):
        snapshot = HeatmapSnapshot(x=np.linspace(1., 2., 10), y=[0., 1., 2.],
                                   z=np.ones((3, 10)), x_limits=(1., 2.), y_limits=(0., 2.))
        p = PlotPresentation(width_in=5., height_in=5.,
                             axis=AxisPresentation(exchange_xy=True, x_major_count=5,
                                                   y_major_count=6, x_minor_count=2, y_minor_count=0))
        page = render_hybrid_svg(snapshot, p)
        root = ET.fromstring(page.svg_bytes)
        ns = {'s': 'http://www.w3.org/2000/svg'}
        for prefix, expected in (('x', np.linspace(0., 2., 5)), ('y', np.linspace(1., 2., 6))):
            main_axis = root.find(f".//s:g[@id='matplotlib.axis_{1 if prefix == 'x' else 2}']", ns)
            labels = [float(text.text) for group in main_axis.findall('.//s:g', ns)
                      if group.get('id', '').startswith(f'{prefix}tick_')
                      for text in group.findall('.//s:text', ns)]
            np.testing.assert_allclose(labels, expected)

    def test_title_overlay_and_annotations_leave_actual_page_geometry_unchanged(self):
        snapshot = HeatmapSnapshot(x=[1., 2.], y=[0., 1.], z=np.ones((2, 2)),
                                   x_label='Energy (eV)', y_label='Gate (V)', signal_label='PL (arb. units)')
        ns = {'s': 'http://www.w3.org/2000/svg'}
        annotation = PlotAnnotation(id='note', kind='text', text='Existing note', position=(.4, .5),
                                    coordinate_space='axes-fraction')
        for frame_size in ((2.9, 2.8), (None, None)):
            for alignment in ('left', 'center', 'right'):
                p = PlotPresentation(height_in=3.45 if alignment == 'right' else 4.2,
                                     frame_width_in=frame_size[0], frame_height_in=frame_size[1])
                p = replace(p, colorbar=replace(p.colorbar, alignment=alignment))
                baseline = render_hybrid_svg(snapshot, p)
                for title in ('Condition A', 'Long manual title with literal $text$\nSecond line ' * 5):
                    with self.subTest(frame_size=frame_size, alignment=alignment, title=title):
                        page = render_hybrid_svg(replace(snapshot, title=title), replace(p, annotations=(annotation,)))
                        self.assertEqual(page.axes_bounds, baseline.axes_bounds)
                        self.assertEqual(page.plot_size_in, baseline.plot_size_in)
                        self.assertEqual(page.figure_size_in, baseline.figure_size_in)
                        self.assertEqual(page.colorbar_bounds, baseline.colorbar_bounds)
                        self.assertEqual(page.header_line_count, baseline.header_line_count)
                        root = ET.fromstring(page.svg_bytes)
                        group = root.find(".//s:g[@id='page-title-overlay']", ns)
                        self.assertIsNotNone(group)
                        parents = {child: parent for parent in root.iter() for child in parent}
                        node = group.find('.//s:text', ns)
                        self.assertIn('12px', node.get('style'))
                        clip = None
                        while node is not None and clip is None:
                            clip = node.get('clip-path'); node = parents.get(node)
                        rectangle = root.find(f".//s:clipPath[@id='{clip[5:-1]}']/s:rect", ns)
                        left, bottom, width, height = page.axes_bounds
                        page_width, page_height = np.array(page.figure_size_in) * 72
                        np.testing.assert_allclose([float(rectangle.get(name)) for name in ('x', 'y', 'width', 'height')],
                            [left*page_width, (1-bottom-height)*page_height, width*page_width, height*page_height], atol=1e-5)


class SharedFrameQtTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        path = self.folder / 'scan.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
        self.window = MainWindow(); self.window._show_error = self.fail
        self.window._load_dat_file_direct(path); self.window.show(); APP.processEvents()

    def tearDown(self):
        self.window.close(); APP.processEvents()

    def logical_frame(self):
        w = self.window; w.canvas.draw()
        box = w._heat_ax.get_window_extent()
        return np.array([box.width, box.height]) / w.canvas.device_pixel_ratio

    def test_title_edits_keep_live_and_formal_frames_with_hidden_plot_and_regional_bars(self):
        w = self.window
        w.plot_tabs.setCurrentIndex(0)
        for auto, exchange, mode in ((False, False, 'global'), (True, True, 'global'), (False, True, '3')):
            p = replace(w.workspace.active.presentation,
                        height_in=5. if mode == '3' else 3.45,
                        frame_width_in=None if auto else 2.9, frame_height_in=None if auto else 2.8,
                        axis=AxisPresentation(exchange_xy=exchange))
            w.set_plot_presentation(p)
            w.color_scale_combo.setCurrentIndex(0 if mode == 'global' else w.color_scale_combo.findData(mode))
            w.plot_title_edit.clear(); w._finish_annotation_edit(); w._render_plot_page()
            baseline = w.plot_page.rendered_page
            self.assertIsNotNone(baseline, w.plot_page.status_label.text())
            frame = self.logical_frame().copy()
            bar_bounds = [bar.ax.get_position().bounds for bar in w._heat_colorbars]
            for title in ('Condition A', 'A long manual title with literal $text$\nSecond line ' * 4):
                with self.subTest(auto=auto, exchange=exchange, mode=mode, title=title):
                    w.plot_title_edit.setText(title)
                    np.testing.assert_allclose(self.logical_frame(), frame, atol=1e-8)
                    w._finish_annotation_edit()
                    np.testing.assert_allclose(self.logical_frame(), frame, atol=1e-8)
                    w._render_plot_page()
                    page = w.plot_page.rendered_page
                    self.assertIsNotNone(page, w.plot_page.status_label.text())
                    self.assertEqual(page.axes_bounds, baseline.axes_bounds)
                    self.assertEqual(page.colorbar_bounds, baseline.colorbar_bounds)
                    np.testing.assert_allclose(self.logical_frame(), frame, atol=1e-8)
                    np.testing.assert_allclose([bar.ax.get_position().bounds for bar in w._heat_colorbars], bar_bounds)
                    artist = w._heat_ax.title
                    self.assertEqual(artist.get_text(), title)
                    self.assertIs(artist.get_transform(), w._heat_ax.transAxes)
                    self.assertEqual(artist.get_position(), (.02, .98))
                    self.assertTrue(artist.get_clip_on())
                    self.assertFalse(artist.get_in_layout())
                    self.assertFalse(artist.get_parse_math())
                    self.assertAlmostEqual(artist.get_fontsize(), p.text_pt*96*w._analysis_display_scale()*w.canvas.device_pixel_ratio/w.figure.dpi)

    def test_regional_overlay_supports_empty_custom_colorbar_labels(self):
        w = self.window
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('3'))
        p = replace(w.workspace.active.presentation, height_in=5.)
        ticks = {f'region-{index+1}': ColorbarTicks(tuple(bar.get_ticks()), ('', '', ''))
                 for index, bar in enumerate(w._heat_colorbars)}
        w.set_plot_presentation(replace(p, colorbar=replace(p.colorbar, ticks=ticks)))
        before = self.logical_frame()
        w.plot_title_edit.setText('Condition A'); w._finish_annotation_edit()
        w._render_plot_page()
        self.assertIsNotNone(w.plot_page.rendered_page, w.plot_page.status_label.text())
        np.testing.assert_allclose(self.logical_frame(), before)
        w.canvas.draw()
        self.assertGreater(w._heat_signal_label.get_window_extent(w.canvas.get_renderer()).y0,
                           max(bar.ax.get_window_extent().y1 for bar in w._heat_colorbars))

    def test_live_compact_gaps_recompute_corner_alignment_after_flip_and_pan(self):
        w = self.window
        p = replace(w.workspace.active.presentation, frame_width_in=2.9, frame_height_in=2.55,
                    axis=AxisPresentation(x_major_count=5, y_major_count=5))
        w.set_plot_presentation(p)
        for inside, count, xlim, ylim in ((True, 7, (1., 2.), (0., 2.)),
                                         (True, 7, (2., 1.), (2., 0.)),
                                         (True, 5, (1.13, 1.83), (.2, 1.8)),
                                         (False, 5, (1.13, 1.83), (.2, 1.8))):
            w.set_plot_presentation(replace(p, axis=replace(p.axis, ticks_inside=inside, x_major_count=count)))
            w._heat_ax.set_xlim(xlim); w._heat_ax.set_ylim(ylim)
            w._layout_live_plots(); w.canvas.draw()
            points = 96 * w._analysis_display_scale() * w.canvas.device_pixel_ratio / 72
            with self.subTest(inside=inside, xlim=xlim):
                assert_compact_axis_boxes(self, w._heat_ax, w.canvas.get_renderer(), points, inside=inside)
                self.assertEqual(w._heat_ax.get_xlim(), xlim)
                self.assertEqual(w._heat_ax.get_ylim(), ylim)
                self.assertEqual(len(w._heat_ax.get_xticks()), count)
                self.assertEqual(len(w._heat_ax.get_yticks()), 5)
                labels = [tick.label1 for tick in w._heat_ax.yaxis._update_ticks()
                          if tick.label1.get_visible() and tick.label1.get_text()]
                bottom = min(labels, key=lambda label: label.get_window_extent(w.canvas.get_renderer()).y0)
                self.assertTrue(all(label.get_verticalalignment() == 'center_baseline'
                                    for label in labels if label is not bottom))
                if not inside:
                    self.assertEqual(bottom.get_verticalalignment(), 'center_baseline')

    def test_fixed_frame_shared_across_zoom_resize_and_svg_bytes(self):
        w = self.window
        w.set_plot_presentation(replace(w.workspace.active.presentation,
                                        frame_width_in=2.2, frame_height_in=1.3))
        w._render_plot_page(); page = w.plot_page.rendered_page
        self.assertIsNotNone(page)
        self.assertIsInstance(w.analysis_scroll, QScrollArea)
        mesh, cube, norm = w._heat_mesh, w.current_cube, w._heat_mesh.norm
        limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim()
        for zoom, factor in (('100%', 1), ('150%', 1.5), ('200%', 2)):
            w.plot_page.set_zoom(zoom); APP.processEvents()
            expected = np.array([2.2, 1.3]) * 96 * factor
            np.testing.assert_allclose(self.logical_frame(), expected, atol=1)
            preview = w.plot_page.preview.size()
            visible = np.array([preview.width()*page.axes_bounds[2], preview.height()*page.axes_bounds[3]])
            np.testing.assert_allclose(self.logical_frame(), visible, atol=1)
            w.resize(1000 if factor == 1 else 1250, 720); APP.processEvents()
            np.testing.assert_allclose(self.logical_frame(), expected, atol=1)
            self.assertEqual(w.plot_page.preview.svg_bytes, page.svg_bytes)
            self.assertIs(w.plot_page.rendered_page, page)
        self.assertIs(w._heat_mesh, mesh); self.assertIs(w.current_cube, cube); self.assertIs(w._heat_mesh.norm, norm)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim()), limits)
        self.assertEqual(w.workspace.active.presentation.numeric_pt, 10)

    def test_native_tick_modes_apply_count_increment_and_minor_count(self):
        w = self.window; page = w.plot_page
        page.x_mode_combo.setCurrentText('By count'); page.x_count_spin.setValue(7)
        page.x_minor_count_spin.setValue(3)
        w.canvas.draw()
        self.assertEqual(len(w._heat_ax.get_xticks()), 7)
        self.assertEqual(len(w._heat_ax.xaxis.get_minorticklocs()), 18)
        self.assertEqual(w.workspace.active.presentation.axis.x_major_count, 7)
        self.assertEqual(w.workspace.active.presentation.width_in, 91.5/25.4)
        page.x_mode_combo.setCurrentText('By increment'); page.x_step_spin.setValue(.2)
        self.assertIsNone(w.workspace.active.presentation.axis.x_major_count)
        np.testing.assert_allclose(np.diff(w._heat_ax.get_xticks()), .2)

    def test_unedited_dimensions_preserve_exact_saved_values_at_qt_rounding_ties(self):
        w = self.window
        p = replace(w.workspace.active.presentation, height_in=3.5625,
                    frame_width_in=2.5625, frame_height_in=2.5625)
        w.set_plot_presentation(p)
        self.assertEqual(w.plot_page.height_spin.value(), 3.563)
        self.assertEqual(w.plot_page.frame_width_spin.value(), 2.563)
        w.plot_page.numeric_spin.setValue(11)
        actual = w.workspace.active.presentation
        self.assertEqual(actual.width_in, p.width_in)
        self.assertEqual(actual.height_in, p.height_in)
        self.assertEqual((actual.frame_width_in, actual.frame_height_in), (2.5625, 2.5625))
        w._render_plot_page(); page = w.plot_page.rendered_page
        self.assertIsNotNone(page, w.plot_page.status_label.text())
        self.assertEqual(page.figure_size_in, (p.width_in, 3.5625))
        np.testing.assert_allclose(page.plot_size_in, [2.5625, 2.5625], atol=1e-12)

    def test_analysis_fit_uses_its_viewport_at_startup_resize_and_tab_switch(self):
        self.window.close()
        self.window = MainWindow(); self.window._show_error = self.fail
        w = self.window
        w._load_dat_file_direct(self.folder / 'scan.dat')
        self.assertFalse(w.isVisible())
        np.testing.assert_allclose(self.logical_frame(), np.array([2.9, 2.8])*96, atol=1)
        w.resize(1500, 960); w.show(); w.plot_tabs.setCurrentIndex(0); QTest.qWait(350)
        self.assertEqual(w.plot_tabs.currentIndex(), 0)
        self.assertEqual(w.plot_page.display_scale, 1.)
        initial = self.logical_frame()
        self.assertTrue(np.all(initial >= np.array([2.9, 2.8])*96*1.35), initial)
        self.assertAlmostEqual(initial[0]/initial[1], 2.9/2.8)
        self.assertAlmostEqual(w._spec_ax.get_window_extent().height/w.canvas.device_pixel_ratio, 95.)
        self.assertEqual(w.analysis_scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(w.analysis_scroll.verticalScrollBar().maximum(), 0)
        w._render_plot_page(); page = w.plot_page.rendered_page
        self.assertIsNotNone(page, w.plot_page.status_label.text())
        owner, saved, cube, norm = w._plot_owner_token(), w.workspace.active.presentation, w.current_cube, w._heat_mesh.norm
        science = cube.z.copy()
        view = w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim(), w.current_gate
        w.plot_tabs.setCurrentIndex(1); QTest.qWait(150)
        plot_scale = w.plot_page.display_scale
        self.assertGreater(plot_scale, .1)
        np.testing.assert_allclose(self.logical_frame(), initial, atol=1)
        w.plot_tabs.setCurrentIndex(0); QTest.qWait(150)
        np.testing.assert_allclose(self.logical_frame(), initial, atol=1)
        w.resize(1100, 740); QTest.qWait(150)
        smaller = self.logical_frame()
        self.assertTrue(np.all(smaller < initial*.9), (smaller, initial))
        self.assertEqual(w.plot_page.display_scale, plot_scale)
        w.resize(1500, 960); QTest.qWait(150)
        np.testing.assert_allclose(self.logical_frame(), initial, atol=1)
        QTest.qWait(200)
        np.testing.assert_allclose(self.logical_frame(), initial, atol=1)
        self.assertFalse(w._view_layout_timer.isActive())
        self.assertIs(w.plot_page.rendered_page, page)
        self.assertEqual(w.plot_page.preview.svg_bytes, page.svg_bytes)
        self.assertEqual(w.workspace.active.presentation, saved)
        self.assertEqual(w._plot_owner_token(), owner)
        self.assertIs(w.current_cube, cube); self.assertIs(w._heat_mesh.norm, norm)
        np.testing.assert_array_equal(cube.z, science)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim(), w.current_gate), view)
        w.analysis_zoom_combo.setCurrentText('200%'); APP.processEvents()
        np.testing.assert_allclose(self.logical_frame(), np.array([2.9, 2.8])*192, atol=1)
        self.assertEqual(w.plot_page.zoom_combo.currentText(), '200%')
        original = w.canvas.device_pixel_ratio
        try:
            w.canvas._set_device_pixel_ratio(2.)
            w._layout_live_plots()
            np.testing.assert_allclose(self.logical_frame(), np.array([2.9, 2.8])*192, atol=1)
        finally:
            w.canvas._set_device_pixel_ratio(original)
        w.analysis_zoom_combo.setCurrentText('Fit'); QTest.qWait(250)
        np.testing.assert_allclose(self.logical_frame(), initial, atol=1)
        self.assertEqual(w.analysis_scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(w.analysis_scroll.verticalScrollBar().maximum(), 0)

    def test_invalid_manual_ticks_after_toolbar_coordinate_log_retire_exports(self):
        w = self.window
        w._render_plot_page(); self.assertIsNotNone(w.plot_page.rendered_page)
        # Matplotlib's retained axes editor uses this real coordinate setter.
        w._heat_ax.set_xscale('log')
        for fields in ({'x_major_step': .2}, {'x_major_count': 7}):
            p = replace(w.workspace.active.presentation,
                        axis=AxisPresentation(**fields))
            w.set_plot_presentation(p)
            self.assertIsNone(w.plot_page.rendered_page)
            self.assertFalse(w.plot_page.export_svg_btn.isEnabled())
            self.assertFalse(w.plot_page.export_png_btn.isEnabled())
            self.assertIn('linear', w.plot_page.status_label.text())
            w._render_plot_page(); self.assertIsNone(w.plot_page.rendered_page)
            for extension in ('svg', 'png'):
                with self.assertRaises(ValueError):
                    w.export_plot_page(extension)
            self.assertEqual(w.workspace.active.presentation, p)
            w._set_current_gate_from_value(1., update_plot=True)
            self.assertGreater(len(w._spec_ax.lines), 0)
        w._heat_ax.set_xscale('linear')
        w._render_plot_page()
        self.assertIsNotNone(w.plot_page.rendered_page, w.plot_page.status_label.text())
        self.assertEqual(w.workspace.active.presentation.axis.x_major_count, 7)
        self.assertTrue(w.plot_page.export_svg_btn.isEnabled())

    def test_crowded_manual_count_disables_exports_and_keeps_editor_choice(self):
        w = self.window
        w._render_plot_page(); self.assertIsNotNone(w.plot_page.rendered_page)
        w.plot_page.x_mode_combo.setCurrentText('By count')
        w.plot_page.x_count_spin.setValue(100)
        w._render_plot_page()
        self.assertIsNone(w.plot_page.rendered_page)
        self.assertFalse(w.plot_page.export_svg_btn.isEnabled())
        self.assertFalse(w.plot_page.export_png_btn.isEnabled())
        self.assertEqual(w.workspace.active.presentation.axis.x_major_count, 100)
        self.assertEqual(w.plot_page.x_count_spin.value(), 100)
        w._set_current_gate_from_value(1., update_plot=True)
        self.assertGreater(len(w._spec_ax.lines), 0)

    def test_nonlinear_minor_editor_retires_exports_and_recovers_automatic_minors(self):
        w = self.window; p = w.plot_page
        w._render_plot_page(); self.assertIsNotNone(p.rendered_page)
        w._heat_ax.set_xscale('log'); w._layout_live_plots()
        original = w._heat_ax.xaxis.get_minorticklocs().copy()
        self.assertGreater(len(original), 0)
        p.x_minor_count_spin.setValue(3)
        self.assertEqual(w.workspace.active.presentation.axis.x_minor_count, 3)
        self.assertIsNone(p.rendered_page)
        self.assertFalse(p.export_svg_btn.isEnabled()); self.assertFalse(p.export_png_btn.isEnabled())
        self.assertIn('linear', p.status_label.text())
        w._render_plot_page(); self.assertIsNone(p.rendered_page)
        self.assertGreater(len(w._heat_ax.xaxis.get_minorticklocs()), 0)
        p.x_minor_count_spin.setValue(1); w._render_plot_page()
        self.assertIsNotNone(p.rendered_page, p.status_label.text())
        self.assertEqual(w._heat_ax.get_xscale(), 'log')
        np.testing.assert_allclose(w._heat_ax.xaxis.get_minorticklocs(), original)
        p.x_minor_count_spin.setValue(0)
        self.assertEqual(len(w._heat_ax.xaxis.get_minorticklocs()), 0)
        p.x_minor_count_spin.setValue(1)
        np.testing.assert_allclose(w._heat_ax.xaxis.get_minorticklocs(), original)

    def test_sidecar_counts_restore_and_legacy_auto_never_uses_other_owner(self):
        w = self.window
        w.open_graph_document()
        w.plot_page.set_zoom('100%')
        a = w.workspace.active
        p = replace(a.presentation, axis=AxisPresentation(x_major_count=7, y_major_count=5,
                                                          x_minor_count=3, y_minor_count=2))
        w.set_plot_presentation(p); w._save_current_plot_state()
        saved, notice = w._plot_state_store.load(w.current_dat_path, w._loaded_source_fingerprint)
        self.assertEqual(notice, '')
        self.assertEqual(PlotPresentation.from_dict(saved['presentation']), p)
        w.set_plot_presentation(replace(p, frame_width_in=None, frame_height_in=None))
        w._render_plot_page(); old = w.plot_page.rendered_page
        self.assertIsNotNone(old)
        w.plot_tabs.setCurrentIndex(0)
        np.testing.assert_allclose(self.logical_frame(), np.array(old.plot_size_in)*96, atol=1)
        bpath = self.folder / 'other.dat'
        bpath.write_text('Energy(eV) 0 1\n1 1 2\n2 3 4', encoding='utf-8')
        w._load_dat_file_direct(bpath)
        w.plot_page.set_zoom('100%')
        w.set_plot_presentation(replace(w.workspace.active.presentation,
                                        frame_width_in=None, frame_height_in=None))
        w.plot_page.rendered_page = old
        w._layout_live_plots()
        np.testing.assert_allclose(self.logical_frame(), np.array([2.9, 2.8])*96, atol=1)
        self.assertFalse(w.plot_page.commit_rendered_page(old))


if __name__ == '__main__':
    unittest.main()
