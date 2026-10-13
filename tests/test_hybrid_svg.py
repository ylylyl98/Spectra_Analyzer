"""Observable contracts of the detached formal page and its final SVG bytes."""
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication

try:
    records = importlib.import_module('spectra_app.plot_presentation')
    export = importlib.import_module('spectra_app.svg_export')
except ModuleNotFoundError:
    records = export = None

_APP = QApplication.instance() or QApplication([])
NS = {'s': 'http://www.w3.org/2000/svg'}
XLINK = '{http://www.w3.org/1999/xlink}href'


class HybridSvgTests(unittest.TestCase):
    def test_default_midpoint_removes_only_relative_floating_roundoff(self):
        norm = export.build_color_norm(records.NormSpec(), (-676.9555186135741, 676.9555186135744))
        values, labels, _ = export._bar_ticks(norm, None)
        self.assertEqual(values[1], 0.)
        self.assertEqual(labels[1], '0')
        custom = records.ColorbarTicks((-600., 1.3e-13, 600.))
        self.assertEqual(export._bar_ticks(norm, custom)[0][1], 1.3e-13)
        tiny = export.build_color_norm(records.NormSpec(), (-1e-13, 3e-13))
        self.assertAlmostEqual(export._bar_ticks(tiny, None)[0][1], 1e-13, delta=1e-28)

    def setUp(self):
        self.assertIsNotNone(records, 'Formal-page ordinary records are not implemented')
        self.assertIsNotNone(export, 'Hybrid SVG renderer is not implemented')

    def snapshot(self, **kwargs):
        options = dict(x=[0., 1., 2., 3.], y=[0., 1., 2.],
                       z=np.array([[-2., 0., 1., 2.], [-1., 1., 2., 3.], [0., 2., 3., 4.]]),
                       x_label='Energy (eV)', y_label='Gate (V)', title='Sample 9T σ⁺',
                       signal_label='d²(ΔR/R)/dE² (a. u.)', color_limits=(-2., 4.), cmap='RdBu_r')
        options.update(kwargs)
        return records.HeatmapSnapshot(**options)

    def test_snapshot_copies_arrays_and_preserves_source_mask(self):
        source = np.ma.array([[1., 2.], [3., 4.]], mask=[[False, True], [False, False]])
        x = np.array([1., 2.])
        snapshot = self.snapshot(x=x, y=[0., 1.], z=source, color_limits=(1., 4.))
        source[0, 0] = 99
        x[0] = 99
        self.assertEqual(snapshot.z[0, 0], 1.)
        self.assertTrue(np.isnan(snapshot.z[0, 1]))
        self.assertEqual(snapshot.x[0], 1.)
        for array in (snapshot.x, snapshot.y, snapshot.z):
            self.assertFalse(array.flags.writeable)
            with self.assertRaises(ValueError):
                array.setflags(write=True)

    def test_mapping_is_shared_and_log_masks_only_display_values(self):
        source = np.array([-2., 0., 1., 10., 100., np.nan])
        masked, count = export.prepare_display_values(source, records.NormSpec(kind='log'))
        self.assertEqual(count, 2)
        self.assertEqual(masked.mask.tolist(), [True, True, False, False, False, True])
        np.testing.assert_equal(source, [-2., 0., 1., 10., 100., np.nan])
        np.testing.assert_equal(export.valid_color_values(source, records.NormSpec(kind='log')), [1., 10., 100.])
        norm = export.build_color_norm(records.NormSpec(kind='log'), (1., 100.))
        self.assertAlmostEqual(float(norm(10.)), .5)
        norm = export.build_color_norm(records.NormSpec(kind='zero-centered'), (-2., 4.))
        self.assertAlmostEqual(float(norm(0.)), .5)

    def test_illegal_mapping_limits_and_no_positive_data_fail_explicitly(self):
        for kind, limits in [('log', (0., 4.)), ('zero-centered', (1., 4.)), ('linear', (4., 1.))]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                export.build_color_norm(records.NormSpec(kind=kind), limits)
        with self.assertRaisesRegex(ValueError, 'threshold|linthresh'):
            records.NormSpec(kind='symlog', linthresh=0)
        with self.assertRaisesRegex(ValueError, 'positive'):
            export.render_hybrid_svg(self.snapshot(z=np.zeros((3, 4)), color_limits=(1., 4.),
                                                   norm=records.NormSpec(kind='log')), records.PlotPresentation())

    def test_json_roundtrip_rejects_nonfinite_unknown_and_wrong_typed_fields(self):
        annotation = records.PlotAnnotation(id='text-1', text='9T σ⁺', position=(.8, .9))
        presentation = records.PlotPresentation(annotations=(annotation,), axis=records.AxisPresentation(exchange_xy=True))
        saved = json.loads(json.dumps(presentation.to_dict(), allow_nan=False))
        restored = records.PlotPresentation.from_dict(saved)
        self.assertEqual(restored, presentation)
        self.assertEqual(records.NormSpec.from_dict({'kind': 'symlog', 'linthresh': .2}).to_dict(),
                         {'kind': 'symlog', 'linthresh': .2})
        for payload in ({'width_in': float('nan')}, {'width_in': True}, {'heatmap_dpi': 0},
                        {'axis': {'flip_x': 1}}, {'unknown': 3}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                records.PlotPresentation.from_dict(payload)

    def test_page_has_physical_units_one_raster_editable_text_and_gradient(self):
        page = export.render_hybrid_svg(self.snapshot(), records.PlotPresentation())
        root = ET.fromstring(page.svg_bytes)
        self.assertAlmostEqual(float(root.attrib['width'][:-2]), 91.5/25.4, places=12)
        self.assertEqual(root.attrib['height'], '3.45in')
        self.assertEqual(page.figure_size_in, (91.5/25.4, 3.45))
        np.testing.assert_allclose(page.plot_size_in, [2.9, 2.8])
        images = root.findall('.//s:image', NS)
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].attrib[XLINK].startswith('data:image/png;base64,'))
        semantic = ''.join(root.itertext())
        self.assertIn('σ⁺', semantic)
        self.assertIn('d²(ΔR/R)/dE² (arb. units)', semantic)
        self.assertGreater(len(root.findall('.//s:text', NS)), 5)
        self.assertEqual(len(root.findall('.//s:linearGradient', NS)), 1)
        self.assertIn('10px', page.svg_bytes.decode())
        self.assertIn('12px', page.svg_bytes.decode())

    def test_origin_header_is_inline_right_anchored_and_preserves_unequal_ticks(self):
        literal = '∂²(DR/R₀)/∂E² (arb. units)'
        ticks = records.ColorbarTicks((-2000., 130., 2000.), ('-2000', '130', '2000'))
        presentation = records.PlotPresentation(width_in=4., colorbar=records.ColorbarPresentation(ticks={'global': ticks}))
        page = export.render_hybrid_svg(self.snapshot(title='', signal_label=literal,
            color_limits=(-2000., 2000.)), presentation)
        left, bottom, width, height = page.axes_bounds
        bar_left, bar_bottom, bar_width, bar_height = page.colorbar_bounds['global']
        self.assertAlmostEqual(bar_left + bar_width, left + width)
        self.assertAlmostEqual(bar_width * 4., .70)
        self.assertAlmostEqual((bar_bottom - bottom - height) * 3.45, .025)
        np.testing.assert_allclose(page.colorbar_tick_positions['global'], [0., .5325, 1.])
        root = ET.fromstring(page.svg_bytes)
        signal = next(node for node in root.findall('.//s:text', NS) if node.text == literal)
        bar_top_pt = (1 - bar_bottom - bar_height) * 3.45 * 72
        self.assertLess(abs(float(signal.get('y')) - bar_top_pt), 12.)
        self.assertEqual(signal.get('style').split('text-anchor: ')[-1].split(';')[0], 'end')
        self.assertIn('10px', page.svg_bytes.decode())
        self.assertIn('12px', page.svg_bytes.decode())

    def test_custom_rectangular_frame_and_vertical_gap_are_physical_settings(self):
        fields = records.PlotPresentation().to_dict()
        self.assertIn('frame_width_in', fields, 'Explicit frame geometry is missing')
        snap = self.snapshot(title='', signal_label='Signal')
        for lock in (False, True):
            presentation = records.PlotPresentation(frame_width_in=2.1, frame_height_in=1.5,
                aspect_locked=lock, colorbar=records.ColorbarPresentation(frame_gap_in=.10))
            page = export.render_hybrid_svg(snap, presentation)
            np.testing.assert_allclose(page.plot_size_in, [2.1, 1.5])
            left, bottom, width, height = page.axes_bounds
            bar_left, bar_bottom, bar_width, _ = page.colorbar_bounds['global']
            self.assertAlmostEqual(bar_left + bar_width, left + width)
            self.assertAlmostEqual((bar_bottom - bottom - height) * 3.45, .10)
        for payload in ({'frame_width_in': 2.}, {'frame_width_in': 0., 'frame_height_in': 2.},
                        {'frame_width_in': float('nan'), 'frame_height_in': 2.}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                records.PlotPresentation.from_dict(payload)
        with self.assertRaisesRegex(ValueError, 'frame.*fit|frame.*large'):
            export.render_hybrid_svg(snap, records.PlotPresentation(frame_width_in=4., frame_height_in=2.))

    def test_rectangular_colorbar_ends_retain_outside_scale_heatmap_colors(self):
        z = np.tile([-3., -2., 4., 5.], (3, 1))
        z[0, 0] = np.nan
        snap = self.snapshot(z=z)
        page = export.render_hybrid_svg(snap, records.PlotPresentation())
        root = ET.fromstring(page.svg_bytes)
        self.assertIsNone(root.find(".//s:g[@id='colorbar-under-global']", NS))
        self.assertIsNone(root.find(".//s:g[@id='colorbar-over-global']", NS))
        self.assertEqual(page.under_count, 2)
        self.assertEqual(page.over_count, 3)
        self.assertEqual(page.missing_count, 1)

    def test_long_inline_signal_uses_measured_stacked_fallback_without_font_shrink(self):
        snap = self.snapshot(signal_label='An intentionally long measured signal formula ' * 4)
        page = export.render_hybrid_svg(snap, records.PlotPresentation(frame_width_in=None, frame_height_in=None))
        self.assertGreater(page.header_line_count, 2)
        root = ET.fromstring(page.svg_bytes)
        texts = [node for node in root.findall('.//s:text', NS) if 'signal' in (node.text or '')]
        self.assertTrue(texts)
        self.assertTrue(all('12px' in node.get('style', '') for node in texts))
        self.assertEqual(len(root.findall('.//s:image', NS)), 1)

    def test_view_limit_endpoint_ticks_fit_the_lower_left_corner(self):
        # The existing image-export cube has endpoints on both displayed axes;
        # inward ticks must retain them without colliding at the lower left.
        snapshot = self.snapshot(x=np.linspace(0., 10., 8), y=[1., 2., 3.],
                                 z=np.arange(24).reshape(3, 8), x_label='X', y_label='Doping (V)',
                                 title='', signal_label='ΔR/R (a. u.)', x_limits=(0., 10.),
                                 y_limits=(1., 3.), color_limits=(.115, 22.885))
        for exchange, reverse, want_x, want_y in (
                (False, False, (0., 10.), (1., 3.)),
                (False, True, (10., 0.), (3., 1.)),
                (True, False, (1., 3.), (0., 10.)),
                (True, True, (3., 1.), (10., 0.))):
            with self.subTest(exchange=exchange, reverse=reverse):
                presentation = records.PlotPresentation(axis=records.AxisPresentation(
                    exchange_xy=exchange, flip_x=reverse, flip_y=reverse,
                    x_major_count=5, y_major_count=5))
                page = export.render_hybrid_svg(snapshot, presentation)
                root = ET.fromstring(page.svg_bytes)
                for axis, endpoints in (('x', want_x), ('y', want_y)):
                    labels = [node.text for group in root.findall('.//s:g', NS)
                              if group.attrib.get('id', '').startswith(f'{axis}tick_')
                              for node in group.findall('.//s:text', NS)]
                    for endpoint in endpoints:
                        self.assertIn(endpoint, [float(value.replace('−', '-')) for value in labels])
                self.assertEqual(page.display_x_limits, want_x)
                self.assertEqual(page.display_y_limits, want_y)
                self.assertEqual(page.figure_size_in, (91.5/25.4, 3.45))
                self.assertGreater(page.plot_size_in[0], 2.)
                self.assertIn('10px', page.svg_bytes.decode())
                self.assertIn('12px', page.svg_bytes.decode())

    def test_all_norms_render_consistent_qt_heatmap_and_gradient_colors(self):
        # Hand-chosen data value lies at half of each norm's color domain.
        for kind, limits, value in [('linear', (-2., 4.), 1.), ('log', (1., 100.), 10.),
                                    ('symlog', (-10., 10.), 0.), ('zero-centered', (-2., 4.), 0.)]:
            with self.subTest(kind=kind):
                snap = self.snapshot(z=np.full((3, 4), value), color_limits=limits, norm=records.NormSpec(kind=kind))
                page = export.render_hybrid_svg(snap, records.PlotPresentation(width_in=4.2, height_in=4.5))
                qt_image = export.render_svg_image(page, dpi=150)
                left, bottom, width, height = page.axes_bounds
                heat_color = qt_image.pixelColor(round((left + width / 2) * qt_image.width()),
                                                 round((1 - bottom - height / 2) * qt_image.height()))
                bar_left, bar_bottom, bar_width, bar_height = page.colorbar_bounds['global']
                bar_color = qt_image.pixelColor(round((bar_left + bar_width / 2) * qt_image.width()),
                                                round((1 - bar_bottom - bar_height / 2) * qt_image.height()))
                for channel in ('red', 'green', 'blue'):
                    self.assertLessEqual(abs(getattr(heat_color, channel)() - getattr(bar_color, channel)()), 5)

    def test_alpha_and_under_over_colors_survive_the_gradient_replacement(self):
        from matplotlib import colormaps
        cmap = colormaps['RdBu_r'].with_extremes(under='#123456', over='#ABCDEF')
        colormaps.register(cmap, name='hybrid-test-extremes', force=True)
        try:
            snap = self.snapshot(z=np.tile([-3., -2., 4., 5.], (3, 1)), cmap='hybrid-test-extremes', alpha=.5)
            page = export.render_hybrid_svg(snap, records.PlotPresentation(
                colorbar=records.ColorbarPresentation(show_extensions=True)))
            root = ET.fromstring(page.svg_bytes)
            self.assertIsNotNone(root.find(".//s:g[@id='colorbar-under-global']", NS))
            self.assertIsNotNone(root.find(".//s:g[@id='colorbar-over-global']", NS))
            stops = root.findall('.//s:stop', NS)
            self.assertTrue(all(float(stop.attrib['stop-opacity']) == .5 for stop in stops))
            image = export.render_svg_image(page, dpi=300)
            left, bottom, width, height = page.axes_bounds
            under = image.pixelColor(round((left+width/8)*image.width()), round((1-bottom-height/2)*image.height()))
            over = image.pixelColor(round((left+width*7/8)*image.width()), round((1-bottom-height/2)*image.height()))
            for actual, expected in ((under, (136, 153, 170)), (over, (213, 230, 247))):
                for channel, want in zip(('red', 'green', 'blue'), expected):
                    self.assertLessEqual(abs(getattr(actual, channel)()-want), 2)
        finally:
            colormaps.unregister('hybrid-test-extremes')

    def test_log_count_is_visible_and_missing_cells_are_gray(self):
        z = np.array([[-1., 0., 1., 10.], [-2., 0., 2., 20.], [-3., 0., 3., 30.]])
        page = export.render_hybrid_svg(self.snapshot(z=z, color_limits=(1., 30.),
            norm=records.NormSpec(kind='log'), x_limits=(.5, 3.5)), records.PlotPresentation(height_in=4.5))
        self.assertEqual(page.nonpositive_count, 3)
        self.assertEqual(page.header_line_count, 2)
        image = export.render_svg_image(page, dpi=150)
        left, bottom, width, height = page.axes_bounds
        pixel = image.pixelColor(round((left+width/6)*image.width()), round((1-bottom-height/2)*image.height()))
        self.assertEqual((pixel.red(), pixel.green(), pixel.blue()), (217, 217, 217))

    def test_nonuniform_x_centers_keep_positive_cells_in_a_narrow_view(self):
        snapshot = self.snapshot(x=[1., 2., 10.], y=[1., 2., 3.],
            z=np.tile([-1., 10., 100.], (3, 1)), x_limits=(1.5, 2.5), y_limits=(1., 3.),
            color_limits=(1., 100.), norm=records.NormSpec(kind='log'))
        before = tuple(array.copy() for array in (snapshot.x, snapshot.y, snapshot.z))
        page = export.render_hybrid_svg(snapshot, records.PlotPresentation(height_in=4.5))
        self.assertEqual(page.nonpositive_count, 0)
        self.assertEqual(page.display_x_limits, (1.5, 2.5))
        for array, original in zip((snapshot.x, snapshot.y, snapshot.z), before):
            np.testing.assert_equal(array, original)
            self.assertFalse(array.flags.writeable)

    def test_nonuniform_y_centers_count_all_visible_nonpositive_cells(self):
        source = np.ma.array([[-1., 10., 100.], [-2., 0., 20.], [0., -200., 300.]],
                             mask=[[False, False, False], [False, False, False], [True, False, False]])
        original_data, original_mask = source.data.copy(), source.mask.copy()
        snapshot = self.snapshot(x=[1., 2., 3.], y=[1., 2., 10.], z=source,
            x_limits=(1., 3.), y_limits=(1., 2.5), color_limits=(1., 300.),
            norm=records.NormSpec(kind='log'))
        page = export.render_hybrid_svg(snapshot, records.PlotPresentation(width_in=4.2, height_in=4.5))
        self.assertEqual(page.nonpositive_count, 3)
        np.testing.assert_equal(source.data, original_data)
        np.testing.assert_equal(source.mask, original_mask)

    def test_explicit_edges_use_midpoints_for_visible_log_cells(self):
        z = np.array([[100., -1., 200.], [100., 10., 200.], [100., 0., 200.]])
        for axis in ('x', 'y'):
            with self.subTest(axis=axis):
                coordinates = {'x': [1., 2., 3.], 'y': [1., 2., 3.]}
                coordinates[axis] = [.5, 1.5, 6., 14.]
                snapshot = self.snapshot(**coordinates, z=z if axis == 'x' else z.T,
                    **{f'{axis}_limits': (3.5, 4.)}, color_limits=(1., 200.),
                    norm=records.NormSpec(kind='log'))
                page = export.render_hybrid_svg(snapshot, records.PlotPresentation(height_in=4.5))
                self.assertEqual(page.nonpositive_count, 2)

    def test_unchanged_snapshot_and_presentation_produce_identical_final_bytes(self):
        snapshot = self.snapshot()
        presentation = records.PlotPresentation()
        before = snapshot.z.copy()
        first = export.render_hybrid_svg(snapshot, presentation)
        second = export.render_hybrid_svg(snapshot, presentation)
        self.assertEqual(first.svg_bytes, second.svg_bytes)
        self.assertEqual(first.digest, second.digest)
        np.testing.assert_equal(snapshot.z, before)

    def test_qt_renders_real_font_glyphs_instead_of_offscreen_missing_font_boxes(self):
        page = export.render_hybrid_svg(self.snapshot(title='I', signal_label=''), records.PlotPresentation(height_in=4.))
        root = ET.fromstring(page.svg_bytes)
        title = next(node for node in root.findall('.//s:text', NS) if node.text == 'I')
        image = export.render_svg_image(page, dpi=300)
        # Arial's uppercase I has a dark central stem. A missing-font square
        # has a white interior at this point, although SVG text still exists.
        x = round((float(title.attrib['x']) + 1.65) * 300 / 72)
        y = round((float(title.attrib['y']) - 5.) * 300 / 72)
        pixel = image.pixelColor(x, y)
        self.assertLess(pixel.red(), 80)

    def test_regional_two_and_three_are_one_raster_and_keep_separate_gradients(self):
        for count in (2, 3):
            with self.subTest(count=count):
                z = np.tile(np.arange(count, dtype=float), (3, 1))
                regions = tuple(records.ColorRegion(id=f'region-{i}', start=i, stop=i+1,
                                                     color_limits=(i-1., i+1.), label=f'Region {i+1}') for i in range(count))
                snap = self.snapshot(x=np.arange(count), z=z, regions=regions)
                page = export.render_hybrid_svg(snap, records.PlotPresentation(height_in=4.5))
                root = ET.fromstring(page.svg_bytes)
                self.assertEqual(len(root.findall('.//s:image', NS)), 1)
                self.assertEqual(len(root.findall('.//s:linearGradient', NS)), count)
                image = export.render_svg_image(page, dpi=150)
                left, bottom, width, height = page.axes_bounds
                pixels = [image.pixelColor(round((left + width*(i+.5)/count)*image.width()),
                                          round((1-bottom-height/2)*image.height())) for i in range(count)]
                self.assertTrue(all(abs(p.red()-pixels[0].red()) <= 2 for p in pixels))

    def test_custom_log_ticks_use_actual_norm_positions(self):
        ticks = records.ColorbarTicks(values=(1., 10., 1000.), labels=('one', 'ten', 'thousand'))
        presentation = records.PlotPresentation(width_in=4.2, height_in=4.5, colorbar=records.ColorbarPresentation(ticks={'global': ticks}))
        page = export.render_hybrid_svg(self.snapshot(z=np.full((3, 4), 10.), color_limits=(1., 1000.),
                                                     norm=records.NormSpec(kind='log')), presentation)
        np.testing.assert_allclose(page.colorbar_tick_positions['global'], [0., 1/3, 1.])
        with self.assertRaisesRegex(ValueError, 'tick'):
            export.render_hybrid_svg(self.snapshot(color_limits=(1., 4.)), presentation)

    def test_exchange_flip_and_vector_annotations_keep_data_coordinates(self):
        annotation = records.PlotAnnotation(id='arrow-1', kind='arrow', text='7', font_role='numeric',
                                            position=(.8, .8), target=(1., 2.), target_space='data')
        presentation = records.PlotPresentation(axis=records.AxisPresentation(exchange_xy=True, flip_x=True),
                                                annotations=(annotation,), include_peak_labels=True)
        page = export.render_hybrid_svg(self.snapshot(peaks=(records.PeakLabel(x=1., y=2., text='P1'),)), presentation)
        root = ET.fromstring(page.svg_bytes)
        group = root.find(".//s:g[@id='annotation-arrow-1']", NS)
        self.assertIsNotNone(group)
        self.assertGreater(len(group.findall('.//s:path', NS)), 0)
        self.assertIn('P1', ''.join(root.itertext()))
        self.assertEqual(page.display_x_limits, (2.5, -.5))
        self.assertEqual(page.display_y_limits, (-.5, 3.5))

    def test_arrow_text_and_geometry_crossing_plot_bounds_are_clipped_in_svg(self):
        annotation = records.PlotAnnotation(id='cross-boundary', kind='arrow', text='Cross boundary text',
            position=(.95, .5), target=(-.2, .5), target_space='axes-fraction')
        page = export.render_hybrid_svg(self.snapshot(), records.PlotPresentation(annotations=(annotation,)))
        root = ET.fromstring(page.svg_bytes)
        group = root.find(".//s:g[@id='annotation-cross-boundary']", NS)
        self.assertIsNotNone(group)
        text = group.find('.//s:text', NS)
        self.assertIsNotNone(text)
        self.assertEqual(text.text, 'Cross boundary text')
        arrow = group.find(".//s:g[@id='annotation-cross-boundary-arrow']", NS)
        self.assertIsNotNone(arrow)
        paths = arrow.findall('.//s:path', NS)
        self.assertTrue(paths)
        parents = {child: parent for parent in root.iter() for child in parent}
        left, bottom, width, height = page.axes_bounds
        page_width, page_height = (size * 72 for size in page.figure_size_in)
        axes_rectangle = (left * page_width, (1 - bottom - height) * page_height,
                          width * page_width, height * page_height)
        for label, element in [('text', text), *[(f'arrow-{i}', path) for i, path in enumerate(paths)]]:
            with self.subTest(element=label):
                node, clip = element, None
                while node is not None and clip is None:
                    clip = node.get('clip-path')
                    node = parents.get(node)
                self.assertIsNotNone(clip, f'{label} must have an effective SVG clip path')
                rectangle = root.find(f".//s:clipPath[@id='{clip[5:-1]}']/s:rect", NS)
                self.assertIsNotNone(rectangle)
                np.testing.assert_allclose([float(rectangle.get(name)) for name in ('x', 'y', 'width', 'height')],
                                           axes_rectangle, atol=1e-5)

    def test_too_small_page_and_invalid_regional_coverage_fail(self):
        with self.assertRaisesRegex(ValueError, 'layout|page|small'):
            export.render_hybrid_svg(self.snapshot(), records.PlotPresentation(width_in=.5, height_in=.5))
        with self.assertRaisesRegex(ValueError, 'region'):
            self.snapshot(regions=(records.ColorRegion(id='a', start=0, stop=1, color_limits=(-2., 4.)),
                                   records.ColorRegion(id='b', start=2, stop=4, color_limits=(-2., 4.))))

    def test_long_signal_and_regional_ticks_keep_requested_font_size(self):
        regions = tuple(records.ColorRegion(id=f'r{i}', start=i, stop=i+1,
                                            color_limits=(-.07239, 1.053), label=('Left', 'Middle', 'Right')[i])
                        for i in range(3))
        page = export.render_hybrid_svg(self.snapshot(x=[0., 1., 2.], z=np.zeros((3, 3)), regions=regions,
            signal_label='Second derivative d²(ΔR/R)/dE² (a. u.) long signal formula'),
            records.PlotPresentation(frame_width_in=None, frame_height_in=None))
        self.assertIn('12px', page.svg_bytes.decode())
        self.assertGreaterEqual(page.header_line_count, 2)
        # Auto geometry measures the final four-significant-digit labels.
        # Its derived size may be below one inch; the usable square and
        # requested type sizes are the contracts for this legacy Auto page.
        self.assertGreater(page.plot_size_in[0], .65)
        self.assertAlmostEqual(*page.plot_size_in)
        root = ET.fromstring(page.svg_bytes)
        for index in range(2, 5):
            bar = root.find(f".//s:g[@id='axes_{index}']", NS)
            labels = [text for group in bar.findall('.//s:g', NS)
                      if group.get('id', '').startswith('xtick_') for text in group.findall('.//s:text', NS)]
            self.assertEqual(len(labels), 3)
            self.assertTrue(all('font-size: 10px;' in text.get('style') for text in labels))

    def test_regional_layout_adapts_default_axis_tick_density_to_decimal_coordinates(self):
        regions = tuple(records.ColorRegion(id=f'r{i}', start=i*2, stop=(i+1)*2,
                                            color_limits=(-6e6, 8e6), label=('Left', 'Middle', 'Right')[i])
                        for i in range(3))
        page = export.render_hybrid_svg(self.snapshot(x=np.linspace(1.284, 1.562, 60), y=[-9., 0., 9.],
                                                   x_limits=(1.284, 1.562),
                                                   z=np.zeros((3, 60)), regions=tuple(
                    records.ColorRegion(id=f'r{i}', start=i*20, stop=(i+1)*20, color_limits=region.color_limits,
                                        label=region.label) for i, region in enumerate(regions))),
                                        records.PlotPresentation(height_in=2.9, frame_width_in=None, frame_height_in=None))
        self.assertGreater(page.plot_size_in[0], .65)

    def test_writers_use_final_bytes_exclusively_and_png_has_size_dpi_metadata(self):
        page = export.render_hybrid_svg(self.snapshot(), records.PlotPresentation())
        with tempfile.TemporaryDirectory() as temporary:
            svg = export.write_svg(page, Path(temporary) / 'proof')
            self.assertEqual(svg.read_bytes(), page.svg_bytes)
            with self.assertRaises(FileExistsError):
                export.write_svg(page, svg)
            png = export.write_svg_png(page, Path(temporary) / 'proof.png', dpi=300, metadata={'Dataset': 'test'})
            with Image.open(png) as image:
                self.assertEqual(image.size, (1081, 1035))
                self.assertAlmostEqual(image.info['dpi'][0], 300., places=1)
                self.assertEqual(image.info['Dataset'], 'test')
            with self.assertRaises(FileExistsError):
                export.write_svg_png(page, png)


if __name__ == '__main__':
    unittest.main()
