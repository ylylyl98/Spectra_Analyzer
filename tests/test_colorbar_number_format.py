"""Colorbar display precision preserves the scientific values and shared page contract."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from dataclasses import replace
import unittest
import xml.etree.ElementTree as ET

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QApplication

from spectra_app.main_window import MainWindow
from spectra_app.plot_page import PlotPage
from spectra_app.plot_presentation import (ColorbarPresentation, ColorbarTicks, ColorRegion,
                                           HeatmapSnapshot, NormSpec, PlotPresentation)
from spectra_app.plot_templates import FormatStyle, FormatTemplate, capture_format, apply_format
from spectra_app.svg_export import (_bar_ticks, apply_heatmap_colorbar_style, build_color_norm,
                                    prepare_heatmap_page, render_hybrid_svg)
from tests.settings_helpers import isolate_settings
from tests.test_plot_templates import dataset

APP = QApplication.instance() or QApplication([])
FORMAT_FIELDS = ('label_format', 'significant_digits', 'decimal_places',
                 'scientific_style', 'trim_trailing_zeros')


class NumberFormatTests(unittest.TestCase):
    def style(self, **changes):
        self.assertIn('label_format', ColorbarPresentation().to_dict(),
                      'Colorbar records need persisted number formatting')
        return replace(ColorbarPresentation(), **changes)

    def labels(self, style, values):
        return _bar_ticks(build_color_norm(NormSpec(), (-1e30, 1e30)), ColorbarTicks(values), style)[1]

    def test_default_auto_rounds_display_without_changing_actual_926_values(self):
        for upper in (930., 926.):
            with self.subTest(upper=upper):
                values, labels, positions = _bar_ticks(build_color_norm(NormSpec(), (0., upper)), None)
                self.assertEqual(labels, ('0', '460', '930'))
                self.assertEqual(values, (0., upper/2, upper))
                self.assertEqual(positions, (0., .5, 1.))

    def test_defaults_are_two_digits_and_two_decimal_places_without_rewriting_saved_precision(self):
        defaults = ColorbarPresentation()
        self.assertEqual((defaults.significant_digits, defaults.decimal_places), (2, 2))
        self.assertTrue(defaults.trim_trailing_zeros)
        saved = ColorbarPresentation.from_dict({'significant_digits': 6, 'decimal_places': 4})
        self.assertEqual((saved.significant_digits, saved.decimal_places), (6, 4))
        legacy = ColorbarPresentation.from_dict({'significant_digits': 4, 'decimal_places': 0})
        self.assertEqual(ColorbarPresentation.from_dict(legacy.to_dict()), legacy)
        self.assertEqual((legacy.significant_digits, legacy.decimal_places), (4, 0))

    def test_precision_modes_produce_hand_checked_labels(self):
        cases = [
            ({'label_format': 'auto'}, (1., 930., 2000.), ('1', '930', '2000')),
            ({'label_format': 'auto', 'significant_digits': 2}, (1., 926., 2000.), ('1', '930', '2000')),
            ({'label_format': 'decimal', 'decimal_places': 2, 'trim_trailing_zeros': False},
             (-.004, 930., 2000.), ('0.00', '930.00', '2000.00')),
            ({'label_format': 'decimal', 'decimal_places': 2}, (-.004, 930., 2000.), ('0', '930', '2000')),
            ({'label_format': 'scientific', 'decimal_places': 0}, (1., 930., 2000.), ('1e0', '9e2', '2e3')),
            ({'label_format': 'scientific', 'decimal_places': 1, 'trim_trailing_zeros': False},
             (1., 930., 2000.), ('1.0e0', '9.3e2', '2.0e3')),
            ({'label_format': 'scientific', 'decimal_places': 0, 'scientific_style': 'times-ten'},
             (-.001, 0., 2000.), ('-1×10⁻³', '0', '2×10³')),
            ({'label_format': 'engineering'}, (930., 2000., 1e6), ('930', '2k', '1M')),
            ({'label_format': 'engineering'}, (-1e-6, 0., 2e-3), ('-1µ', '0', '2m')),
            ({'label_format': 'engineering', 'significant_digits': 4, 'trim_trailing_zeros': False},
             (930., 2000., 1e6), ('930.0', '2.000k', '1.000M')),
            ({'label_format': 'engineering', 'significant_digits': 3},
             (999999., 1e6, 1e27), ('1M', '1M', '1e27')),
            ({'label_format': 'engineering', 'significant_digits': 1},
             (130., 930., 999999.), ('100', '900', '1M')),
            ({'label_format': 'engineering', 'significant_digits': 2, 'trim_trailing_zeros': False},
             (130., 930., 999.), ('130', '930', '1.0k')),
        ]
        for settings, values, wanted in cases:
            with self.subTest(settings=settings, values=values):
                self.assertEqual(self.labels(self.style(**settings), values), wanted)

    def test_new_settings_roundtrip_and_old_manual_labels_migrate_to_auto(self):
        style = self.style(label_format='scientific', decimal_places=0, scientific_style='times-ten',
                           trim_trailing_zeros=False, ticks={'global': ColorbarTicks((0., 463., 926.), ('L', 'M', 'H'))})
        page = PlotPresentation(colorbar=style)
        self.assertEqual(PlotPresentation.from_dict(page.to_dict()), page)
        old = page.to_dict()
        for field in FORMAT_FIELDS:
            old['colorbar'].pop(field)
        restored = PlotPresentation.from_dict(old)
        self.assertEqual(restored.colorbar.label_format, 'auto')
        self.assertEqual(restored.colorbar.significant_digits, 2)
        self.assertEqual(restored.colorbar.ticks['global'].labels, ('L', 'M', 'H'))

    def test_number_format_boundary_rejects_unknown_and_nonbounded_settings(self):
        self.style()
        for payload in ({'label_format': 'custom'}, {'scientific_style': 'python'},
                        {'significant_digits': 0}, {'significant_digits': 13},
                        {'significant_digits': True}, {'significant_digits': 4.0},
                        {'decimal_places': -1}, {'decimal_places': 13},
                        {'decimal_places': False}, {'decimal_places': '0'},
                        {'trim_trailing_zeros': 1}, {'trim_trailing_zeros': 'yes'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ColorbarPresentation.from_dict(payload)

    def test_templates_copy_number_format_preserving_target_ticks_labels_and_ranges(self):
        source, target = dataset('source'), dataset('target')
        source.presentation = replace(source.presentation, colorbar=self.style(label_format='engineering', significant_digits=5))
        target.presentation = replace(target.presentation, colorbar=ColorbarPresentation(
            ticks={'global': ColorbarTicks((1., 5., 12.), ('low', 'middle', 'high'))}))
        original_view, ticks = deepcopy(target.view), target.presentation.colorbar.ticks
        template = capture_format(source, 'Engineering')
        restored = FormatTemplate.from_dict(template.to_dict())
        apply_format(target, restored)
        self.assertEqual(target.presentation.colorbar.label_format, 'engineering')
        self.assertEqual(target.presentation.colorbar.significant_digits, 5)
        self.assertEqual(target.presentation.colorbar.ticks, ticks)
        self.assertEqual(target.view.color_limits, original_view.color_limits)
        self.assertEqual(target.view.norm, original_view.norm)
        old = restored.style.to_dict()
        for field in FORMAT_FIELDS:
            old['colorbar'].pop(field)
        self.assertEqual(FormatStyle.from_dict(old).colorbar.label_format, 'auto')
        bad = deepcopy(old); bad['colorbar']['arbitrary_expression'] = '{x}'
        with self.assertRaises(ValueError):
            FormatStyle.from_dict(bad)

    def test_all_norms_keep_tick_values_positions_and_manual_label_priority(self):
        style = self.style(label_format='scientific', decimal_places=0)
        cases = [('linear', (0., 926.)), ('log', (1., 926.)),
                 ('symlog', (-926., 926.)), ('zero-centered', (-20., 926.))]
        for kind, limits in cases:
            norm = build_color_norm(NormSpec(kind), limits)
            original = _bar_ticks(norm, None)
            formatted = _bar_ticks(norm, None, style)
            with self.subTest(kind=kind):
                self.assertEqual(formatted[0], original[0])
                self.assertEqual(formatted[2], original[2])
                manual = ColorbarTicks(formatted[0], ('exact low', 'exact mid', 'exact high'))
                self.assertEqual(_bar_ticks(norm, manual, style)[1], manual.labels)

    def test_live_and_svg_share_editable_scientific_labels_and_fixed_dimensions(self):
        style = self.style(label_format='scientific', decimal_places=0, scientific_style='times-ten')
        p = PlotPresentation(width_in=5., height_in=4., frame_width_in=2.9, frame_height_in=2.55, colorbar=style)
        snapshot = HeatmapSnapshot(x=[1., 2., 3.], y=[0., 1.], z=[[1., 930., 2000.], [1., 930., 2000.]],
                                   color_limits=(1., 2000.), regions=(ColorRegion('region-1', 0, 1, (1., 2000.)),
                                   ColorRegion('region-2', 1, 2, (1., 2000.)), ColorRegion('region-3', 2, 3, (1., 2000.))))
        page = render_hybrid_svg(snapshot, p)
        root = ET.fromstring(page.svg_bytes)
        text = [node.text for node in root.iter('{http://www.w3.org/2000/svg}text')]
        figure = Figure(); FigureCanvasAgg(figure)
        ax = figure.add_subplot()
        mesh = ax.imshow([[1., 2000.]], norm=build_color_norm(NormSpec(), (1., 2000.)))
        bar = figure.colorbar(mesh, orientation='horizontal')
        for bar_id in ('region-1', 'region-2', 'region-3'):
            apply_heatmap_colorbar_style(bar, p, bar_id)
            self.assertEqual([node.get_text() for node in bar.ax.get_xticklabels()], ['1×10⁰', '1×10³', '2×10³'])
        for label in ('1×10⁰', '1×10³', '2×10³'):
            self.assertEqual(text.count(label), 3)
        self.assertEqual(page.figure_size_in, (5., 4.))
        np.testing.assert_allclose(page.plot_size_in, (2.9, 2.55))
        self.assertEqual(p.numeric_pt, 10.)
        np.testing.assert_array_equal(snapshot.z, [[1., 930., 2000.], [1., 930., 2000.]])

    def test_fitting_number_labels_do_not_move_or_resample_the_heatmap(self):
        snapshot = HeatmapSnapshot(x=[1., 1.5, 2.], y=[0., 1., 2.], z=np.arange(9).reshape(3, 3),
                                   color_limits=(1.19, 937.89), x_label='Energy (eV)', y_label='Gate (V)')
        original = snapshot.z.copy()
        presentation = PlotPresentation(width_in=4.4, height_in=4.2)
        pages = []
        for digits, labels in ((4, ('1.19', '469.5', '937.9')),
                               (2, ('1.2', '470', '940')),
                               (6, ('1.19', '469.54', '937.89'))):
            page = render_hybrid_svg(snapshot, replace(presentation,
                colorbar=replace(presentation.colorbar, significant_digits=digits)))
            root = ET.fromstring(page.svg_bytes)
            texts = [node.text for node in root.iter('{http://www.w3.org/2000/svg}text')]
            for label in labels:
                self.assertIn(label, texts)
            image = root.find('.//{http://www.w3.org/2000/svg}image')
            pages.append((page, image.attrib))
        first, first_image = pages[0]
        for page, image in pages[1:]:
            self.assertNotEqual(page.svg_bytes, first.svg_bytes)
            self.assertEqual(page.figure_size_in, first.figure_size_in)
            self.assertEqual(page.axes_bounds, first.axes_bounds)
            self.assertEqual(image, first_image, 'Fitting label edits must preserve heatmap PNG bytes and placement')
            self.assertEqual(page.display_x_limits, first.display_x_limits)
            self.assertEqual(page.display_y_limits, first.display_y_limits)
            self.assertEqual(page.colorbar_tick_positions, first.colorbar_tick_positions)
            self.assertEqual(page.colorbar_bounds, first.colorbar_bounds)
        np.testing.assert_array_equal(snapshot.z, original)
        self.assertEqual(snapshot.color_limits, (1.19, 937.89))

    def test_colorbar_strip_keeps_exact_geometry_when_labels_overlap(self):
        for count, alignment in ((1, 'right'), (2, 'right'), (3, 'left'), (3, 'center'), (3, 'right')):
            snapshot = HeatmapSnapshot(x=np.linspace(1., 2., 2*count), y=[0., 1.],
                z=np.linspace(-.0563, .1938, 4*count).reshape(2, 2*count), color_limits=(-.0563, .1938),
                regions=tuple(ColorRegion(f'region-{i+1}', 2*i, 2*i+2, (-.0563, .1938), 'ABC'[i])
                              for i in range(count)) if count > 1 else ())
            p = PlotPresentation(width_in=7., height_in=5.,
                                 colorbar=ColorbarPresentation(alignment=alignment))
            first = None
            for digits in (2, 6):
                with self.subTest(count=count, alignment=alignment, digits=digits):
                    page = render_hybrid_svg(snapshot, replace(p, colorbar=replace(p.colorbar, significant_digits=digits)))
                    bars = np.array(list(page.colorbar_bounds.values())) * (7., 5., 7., 5.)
                    frame = np.array(page.axes_bounds) * (7., 5., 7., 5.)
                    strip_width = count*.7 + (count-1)*.12
                    offset = {'left': 0., 'center': (2.9-strip_width)/2, 'right': 2.9-strip_width}[alignment]
                    np.testing.assert_allclose(bars[:, 0], frame[0]+offset+np.arange(count)*.82, atol=1e-12)
                    np.testing.assert_allclose(bars[:, 1], frame[1]+frame[3]+.025, atol=1e-12)
                    np.testing.assert_allclose(bars[:, 2], .7, atol=1e-12)
                    np.testing.assert_allclose(bars[:, 3], .065, atol=1e-12)
                    self.assertTrue(page.warnings, 'Overlapping colorbar text must remain visible with an actionable warning')
                    self.assertIn('precision', page.warnings[0].lower())
                    if first is not None:
                        self.assertEqual(page.colorbar_bounds, first.colorbar_bounds)
                        self.assertEqual(page.axes_bounds, first.axes_bounds)
                    first = page

    def test_paper_growth_measures_middle_tick_labels_at_each_bar_position(self):
        cases = ((1, 'right', .5, 'Reference intensity'),
                 (1, 'left', .05, 'Reference intensity'),
                 (3, 'right', .05, 'Reference intensity measured at the central detector'),
                 (3, 'left', .95, 'Reference intensity measured at the central detector'))
        for count, alignment, middle, label in cases:
            with self.subTest(count=count, alignment=alignment, middle=middle):
                snapshot = HeatmapSnapshot(x=np.arange(2*count if count > 1 else 3), y=[0., 1.],
                    z=np.arange(4*count if count > 1 else 6).reshape(2, -1),
                    color_limits=(0., 1.), signal_label='',
                    regions=tuple(ColorRegion(f'region-{i+1}', 2*i, 2*i+2, (0., 1.))
                                  for i in range(count)) if count > 1 else ())
                bar_id = 'region-2' if count > 1 else 'global'
                p = PlotPresentation(colorbar=ColorbarPresentation(alignment=alignment,
                    ticks={bar_id: ColorbarTicks((0., middle, 1.), ('0', label, '1'))}))
                original = snapshot.z.copy()
                resolved, page = prepare_heatmap_page(snapshot, p)
                self.assertGreater(resolved.width_in, p.width_in)
                self.assertEqual(replace(resolved, width_in=p.width_in, height_in=p.height_in), p)
                np.testing.assert_allclose(page.plot_size_in, (p.frame_width_in, p.frame_height_in), atol=1e-12)
                size = page.figure_size_in
                bars = np.array(list(page.colorbar_bounds.values())) * (*size, *size)
                frame = np.array(page.axes_bounds) * (*size, *size)
                strip_width = count*p.colorbar.width_in + (count-1)*p.colorbar.gap_in
                offset = 0. if alignment == 'left' else p.frame_width_in-strip_width
                np.testing.assert_allclose(bars[:, 0], frame[0]+offset+
                    np.arange(count)*(p.colorbar.width_in+p.colorbar.gap_in), atol=1e-12)
                np.testing.assert_allclose(bars[:, 1], frame[1]+frame[3]+p.colorbar.frame_gap_in, atol=1e-12)
                np.testing.assert_allclose(bars[:, 2:], [[p.colorbar.width_in, p.colorbar.height_in]]*count, atol=1e-12)
                self.assertEqual(page.colorbar_tick_positions[bar_id], (0., middle, 1.))
                self.assertTrue(page.warnings, 'Fixed strips retain an actionable warning for overlapping text')
                self.assertIn(label, [node.text for node in ET.fromstring(page.svg_bytes).iter(
                    '{http://www.w3.org/2000/svg}text')])
                self.assertEqual(render_hybrid_svg(snapshot, resolved).svg_bytes, page.svg_bytes)
                np.testing.assert_array_equal(snapshot.z, original)


class NumberFormatPageWarningTests(unittest.TestCase):
    def test_regional_page_warning_is_visible_with_unavailable_global_sample_and_clears_on_invalidate(self):
        snapshot = HeatmapSnapshot(x=np.arange(6), y=[0., 1.],
            z=np.linspace(-.0563, .1938, 12).reshape(2, 6), color_limits=(-.0563, .1938),
            regions=tuple(ColorRegion(f'region-{i+1}', 2*i, 2*i+2, (-.0563, .1938)) for i in range(3)))
        p = PlotPresentation(width_in=7., height_in=5.)
        rendered = render_hybrid_svg(snapshot, p)
        self.assertTrue(rendered.warnings)
        page = PlotPage()
        self.addCleanup(page.deleteLater)
        page.set_presentation(p)
        page.set_colorbar_norms({region.id: build_color_norm(snapshot.norm, region.color_limits)
                                 for region in snapshot.regions})
        page.invalidate(snapshot.owner_token)
        self.assertTrue(page.commit_rendered_page(rendered))
        self.assertEqual(page.bar_id_combo.currentText(), 'global')
        self.assertEqual(page.bar_sample_label.text(), 'Selected bar unavailable')
        self.assertIn(rendered.warnings[0], page.bar_format_warning.text())
        self.assertFalse(page.bar_format_warning.isHidden())
        self.assertTrue(page.export_svg_btn.isEnabled())
        page.bar_id_combo.setCurrentText('region-1')
        self.assertEqual(page.bar_sample_label.text(), '-0.056 | 0.069 | 0.19')
        self.assertIn(rendered.warnings[0], page.bar_format_warning.text())
        page.bar_id_combo.setCurrentText('global')
        self.assertIn(rendered.warnings[0], page.bar_format_warning.text())
        page.invalidate((*snapshot.owner_token[:-1], snapshot.owner_token[-1]+1))
        self.assertEqual(page.bar_sample_label.text(), 'Selected bar unavailable')
        self.assertEqual(page.bar_format_warning.text(), '')
        self.assertTrue(page.bar_format_warning.isHidden())
        self.assertEqual(page.status_label.toolTip(), '')
        self.assertFalse(page.export_svg_btn.isEnabled())


class NumberFormatUiTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.w._show_error = self.fail
        self.path = self.folder / 'scan_PL730nm.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {1+i} {465+i} {926+i}' for i in range(12)), encoding='utf-8')
        self.w._load_dat_file_direct(self.path)

    def tearDown(self):
        self.w.close()
        APP.processEvents()

    def controls(self):
        page = self.w.plot_page
        self.assertTrue(hasattr(page, 'bar_format_combo'), 'Colorbar precision controls and live samples are missing')
        return page

    def test_controls_update_precision_meaning_live_sample_and_duplicate_warning(self):
        page = self.controls()
        self.w._heat_mesh.set_clim(920., 930.)
        self.w._apply_live_heatmap_style()
        page.bar_format_combo.setCurrentText('Scientific')
        page.bar_precision_spin.setValue(0)
        self.assertIn('Mantissa decimal places', page.bar_precision_label.text())
        self.assertEqual(page.bar_sample_label.text(), '9e2 | 9e2 | 9e2')
        self.assertIn('duplicate', page.bar_format_warning.text().lower())
        self.assertFalse(page.bar_format_warning.isHidden())
        page.bar_format_combo.setCurrentText('Engineering')
        self.assertIn('Significant digits', page.bar_precision_label.text())
        self.assertGreaterEqual(page.bar_precision_spin.minimum(), 1)
        page.bar_format_combo.setCurrentText('Decimal')
        self.assertEqual(page.bar_precision_spin.minimum(), 0)
        self.assertIn('Decimal places', page.bar_precision_label.text())

    def test_live_and_formal_colorbar_geometry_stays_fixed_and_commit_shows_warning(self):
        w = self.w
        w.open_graph_document()
        w.plot_tabs.setCurrentIndex(0)
        for count in (1, 2, 3):
            w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData(str(count)))
            w.plot_page.bar_id_combo.setCurrentText('global' if count == 1 else 'region-1')
            p = replace(w.workspace.active.presentation, width_in=7., height_in=5.,
                        colorbar=replace(w.workspace.active.presentation.colorbar, width_in=.7))
            previous = None
            for digits in (2, 6):
                w.set_plot_presentation(replace(p, colorbar=replace(p.colorbar, significant_digits=digits)))
                w._render_plot_page()
                page = w.plot_page.rendered_page
                self.assertIsNotNone(page, w.plot_page.status_label.text())
                factor = 96*w._analysis_display_scale()*w.canvas.device_pixel_ratio/w.figure.dpi
                size = w.figure.get_size_inches()
                frame = np.array(w._heat_ax.get_position().bounds) * (*size, *size) / factor
                bars = np.array([bar.ax.get_position().bounds for bar in w._heat_colorbars]) * (*size, *size) / factor
                np.testing.assert_allclose(bars[:, 2:], [[.7, .065]]*count, atol=1e-12)
                np.testing.assert_allclose(bars[:, 1]-frame[1]-frame[3], .025, atol=1e-12)
                if count > 1:
                    np.testing.assert_allclose(np.diff(bars[:, 0]), .82, atol=1e-12)
                formal = np.array(list(page.colorbar_bounds.values())) * (7., 5., 7., 5.)
                formal_frame = np.array(page.axes_bounds) * (7., 5., 7., 5.)
                np.testing.assert_allclose(bars[:, 0]-frame[0], formal[:, 0]-formal_frame[0], atol=1e-12)
                if previous is not None:
                    np.testing.assert_allclose(bars, previous, atol=1e-12)
                    self.assertTrue(page.warnings)
                    self.assertIn(page.warnings[0], w.plot_page.bar_format_warning.text())
                    self.assertFalse(w.plot_page.bar_format_warning.isHidden())
                    self.assertTrue(w.plot_page.export_svg_btn.isEnabled())
                previous = bars
        warning = page.warnings[0]
        p = w.workspace.active.presentation
        w.set_plot_presentation(replace(p, colorbar=replace(p.colorbar, width_in=1.4, gap_in=.6)))
        self.assertNotIn(warning, w.plot_page.bar_format_warning.text())
        w._render_plot_page()
        self.assertIsNotNone(w.plot_page.rendered_page, w.plot_page.status_label.text())
        self.assertEqual(w.plot_page.rendered_page.warnings, ())
        self.assertNotIn(warning, w.plot_page.bar_format_warning.text())

    def test_sample_uses_current_live_norm_and_missing_selected_bar_is_unavailable(self):
        page = self.controls()
        self.w._heat_mesh.set_clim(0., 926.)
        self.w._apply_live_heatmap_style()
        self.assertEqual(page.bar_sample_label.text(), '0 | 460 | 930')
        self.w._heat_mesh.set_clim(0., 930.)
        self.w._apply_live_heatmap_style()
        self.assertEqual(page.bar_sample_label.text(), '0 | 460 | 930')
        page.bar_id_combo.setCurrentText('region-3')
        self.assertIn('unavailable', page.bar_sample_label.text().lower())
        self.w.color_scale_combo.setCurrentIndex(self.w.color_scale_combo.findData('3'))
        self.assertNotIn('unavailable', page.bar_sample_label.text().lower())
        self.assertEqual(page.bar_sample_label.text().split(' | '),
                         [text.get_text() for text in self.w._heat_colorbars[2].ax.get_xticklabels()])

    def test_ui_changes_retire_exports_preserve_science_dimensions_and_sidecar_restore(self):
        import time
        from PySide6.QtTest import QTest

        page = self.controls(); w = self.w
        p = replace(w.workspace.active.presentation, width_in=91.5/25.4, frame_width_in=2.9,
                    colorbar=replace(w.workspace.active.presentation.colorbar, width_in=.7000000001,
                                     height_in=.0650000001, gap_in=.1200000001, frame_gap_in=.0250000001,
                                     ticks={'global': ColorbarTicks((20., 465., 926.))}))
        w.set_plot_presentation(p); w._render_plot_page()
        self.assertIsNotNone(page.rendered_page)
        cube, mesh, norm = w.current_cube, w._heat_mesh, w._heat_mesh.norm
        arrays = cube.z.copy(); limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), mesh.get_clim()
        page.bar_format_combo.setCurrentText('Scientific'); page.bar_precision_spin.setValue(0)
        self.assertIsNone(page.rendered_page)
        self.assertFalse(page.export_svg_btn.isEnabled())
        self.assertIs(w.current_cube, cube); self.assertIs(w._heat_mesh, mesh); self.assertIs(mesh.norm, norm)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), mesh.get_clim()), limits)
        np.testing.assert_array_equal(cube.z, arrays)
        self.assertEqual((page.presentation.width_in, page.presentation.frame_width_in), (91.5/25.4, 2.9))
        self.assertEqual(tuple(getattr(page.presentation.colorbar, name) for name in
                               ('width_in', 'height_in', 'gap_in', 'frame_gap_in')),
                         (.7000000001, .0650000001, .1200000001, .0250000001))
        w._render_plot_page()
        self.assertIsNotNone(page.rendered_page, page.status_label.text())
        self.assertEqual([text.get_text() for text in w._heat_colorbar.ax.get_xticklabels()], ['2e1', '5e2', '9e2'])
        saved = page.presentation
        sidecar = self.path.with_name(self.path.name + '.plot.json')
        self.assertFalse(sidecar.exists())
        w.close()
        deadline = time.monotonic() + 5
        while not sidecar.exists() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(sidecar.exists(), 'Accepted window close did not save the plot sidecar')
        self.w = MainWindow()
        self.w._show_error = self.fail
        self.w._load_dat_file_direct(self.path)
        self.assertEqual(self.w.workspace.active.presentation.colorbar, saved.colorbar)

