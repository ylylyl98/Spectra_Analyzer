"""Colorbar display precision preserves the scientific values and shared page contract."""
from tests import dat_properties_quick_helpers as dat
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from dataclasses import replace
import unittest
import xml.etree.ElementTree as ET

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from tests.quick_migration_case import QuickMigrationCase
from spectra_app.plot_presentation import (ColorbarPresentation, ColorbarTicks, ColorRegion,
                                           HeatmapSnapshot, NormSpec, PlotPresentation)
from spectra_app.plot_templates import FormatStyle, FormatTemplate, capture_format, apply_format
from spectra_app.svg_export import (_bar_ticks, apply_heatmap_colorbar_style, build_color_norm,
                                    prepare_heatmap_page, render_hybrid_svg)
from tests.formal_quick_helpers import edit_field, item, pump
from tests.quick_application_helpers import application
from contextlib import contextmanager
from unittest.mock import patch
from tests.test_plot_templates import dataset

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


class NumberFormatPageWarningTests(QuickMigrationCase):
    def test_regional_page_warning_is_visible_with_unavailable_global_sample_and_clears_on_invalidate(self):
        snapshot = HeatmapSnapshot(x=np.arange(6), y=[0., 1.],
            z=np.linspace(-.0563, .1938, 12).reshape(2, 6), color_limits=(-.0563, .1938),
            regions=tuple(ColorRegion(f'region-{i+1}', 2*i, 2*i+2, (-.0563, .1938)) for i in range(3)))
        p = PlotPresentation(width_in=7., height_in=5.)
        rendered = render_hybrid_svg(snapshot, p)
        self.assertTrue(rendered.warnings)
        w = self.controller
        path = self.folder / 'warning.dat'
        path.write_text('Energy(eV) 0 1\n1 1 2\n2 3 4\n')
        w._load_dat_file_direct(path)
        w.open_graph_document()
        w.request_task(2)
        w.root_window.resize(1100, 900)
        page = w.plot_page
        w._plot_render_timer.stop()
        owner_fields = ('dataset_id', 'activation_epoch', 'science_revision',
                        'view_revision', 'presentation_revision', 'graph_id')
        snapshot = replace(snapshot, **dict(zip(owner_fields, w._plot_owner_token())))
        rendered = render_hybrid_svg(snapshot, p)
        page.set_presentation(p)
        page.set_colorbar_norms({region.id: build_color_norm(snapshot.norm, region.color_limits)
                                 for region in snapshot.regions})
        page.invalidate(snapshot.owner_token)
        page.formal.revealRequested.emit('colorbar', 'bar_label_format')
        pump(65)
        self.assertTrue(page.commit_rendered_page(rendered))
        pump()
        self.assertEqual(page.formal.state['selectedBar'], 'global')
        self.assertEqual(page.formal.state['sample'], 'Selected bar unavailable')
        self.assertIn(rendered.warnings[0], page.formal.state['warning'])
        self.assertTrue(item(self.controller, 'bar_warning').isVisible())
        self.assertTrue(page.command_enabled('export_svg'))
        page.formal.selectBar('region-1')
        self.assertEqual(page.formal.state['sample'], '-0.056 | 0.069 | 0.19')
        self.assertIn(rendered.warnings[0], page.formal.state['warning'])
        page.formal.selectBar('global')
        self.assertIn(rendered.warnings[0], page.formal.state['warning'])
        page.invalidate((*snapshot.owner_token[:4], snapshot.presentation_revision+1, *snapshot.owner_token[5:]))
        self.assertEqual(page.formal.state['sample'], 'Selected bar unavailable')
        self.assertEqual(page.formal.state['warning'], '')
        self.assertFalse(item(self.controller, 'bar_warning').isVisible())
        self.assertEqual(page.status_text, '')
        self.assertFalse(page.command_enabled('export_svg'))


class NumberFormatUiTests(QuickMigrationCase):
    def setUp(self):
        if os.name == 'nt' and self._testMethodName in (
                'test_native_precision_blur_commits_valid_draft',
                'test_native_precision_blur_recovers_decimal_by_mouse',
                'test_native_precision_blur_recovers_decimal_by_keyboard'):
            self.enterContext(patch.dict(os.environ, QT_QPA_PLATFORM='windows'))
        super().setUp()
        self.w = self.controller
        self.w._show_error = self.fail
        self.path = self.folder / 'scan_PL730nm.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {1+i} {465+i} {926+i}' for i in range(12)), encoding='utf-8')
        self.w._load_dat_file_direct(self.path)
        self.w.open_graph_document()
        self.w.request_task(2)

    def tearDown(self):
        self.assertFalse(self.quick_errors)
        super().tearDown()

    def controls(self):
        self.w.root_window.resize(1180, 860)
        self.w.root_window.show()
        page = self.w.plot_page
        self.w.request_task(2)
        pump(60)
        self.assertIsNotNone(item(self.w, 'bar_label_format'))
        return page

    def _native_engineering_precision(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtTest import QTest
        from tests.formal_quick_helpers import ensure_visible, input_receiver

        page = self.controls()
        if os.name == 'nt':
            self.assertEqual(QGuiApplication.platformName(), 'windows')
        page.formal.focusRequested.emit('colorbar', 'bar_label_format')
        pump(65)
        choice = ensure_visible(item(self.w, 'bar_label_format'))
        self.assertTrue(choice.hasActiveFocus())
        QTest.keyClick(input_receiver(self.w, choice), Qt.Key_End)
        pump()
        self.assertEqual(page.presentation.colorbar.label_format, 'engineering')
        return page

    def _native_format_popup(self):
        from PySide6.QtCore import QObject, QPointF, Qt
        from PySide6.QtTest import QTest
        from tests.formal_quick_helpers import ensure_visible, input_receiver

        choice = ensure_visible(item(self.w, 'bar_label_format'))
        receiver = input_receiver(self.w, choice)
        point = choice.mapToScene(QPointF(choice.width()/2, choice.height()/2)).toPoint()
        QTest.mouseClick(receiver, Qt.LeftButton, pos=point)
        pump()
        popup = next(child for child in choice.findChildren(QObject)
                     if child.inherits('QQuickPopup'))
        self.assertTrue(popup.property('visible'))
        self.assertTrue(popup.property('opened'))
        return choice, popup, receiver

    def _native_decimal_selection(self, choice, popup, receiver, *, mouse):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from tests.formal_quick_helpers import input_receiver
        import shiboken6

        activations, chosen, clicks = [], [], []
        choice.activated.connect(activations.append)
        choice.parentItem().chosen.connect(chosen.append)
        retained_receivers = [receiver]
        if mouse:
            view = popup.property('contentItem')
            rows = [child for container in view.childItems() for child in container.childItems()
                    if child.inherits('QQuickItemDelegate')]
            row = next(child for child in rows if child.property('index') == 1)
            native_receiver = input_receiver(self.w, row)
            retained_receivers.append(native_receiver)
            self.assertTrue(shiboken6.isValid(row) and shiboken6.isValid(native_receiver)
                            and row.isVisible() and row.isEnabled() and native_receiver.isVisible())
            self.assertEqual(row.property('contentItem').property('text'), 'Decimal')
            row.clicked.connect(lambda: clicks.append(True))
            visible_rect = row.boundingRect()
            ancestor = row.parentItem()
            while ancestor is not None:
                if ancestor.clip():
                    visible_rect = visible_rect.intersected(
                        row.mapRectFromItem(ancestor, ancestor.boundingRect()))
                ancestor = ancestor.parentItem()
            self.assertFalse(visible_rect.isEmpty(), 'Decimal must have a visible native hit area')
            point = row.mapToScene(visible_rect.center()).toPoint()
            QTest.mouseMove(native_receiver, point, delay=20)
            pump(65)
            QTest.mouseClick(native_receiver, Qt.LeftButton, pos=point)
        else:
            QTest.keyClick(receiver, Qt.Key_Home)
            QTest.keyClick(receiver, Qt.Key_Down)
            QTest.keyClick(receiver, Qt.Key_Return)
        pump(65)
        self.assertEqual(activations, [1], 'Native Decimal activation must be delivered')
        self.assertEqual(chosen, ['decimal'], 'The real QML chosen callback must run')
        if mouse:
            self.assertEqual(clicks, [True], 'The visible Decimal delegate must receive the click')
        self.assertFalse(popup.property('visible'))
        return retained_receivers

    def test_native_precision_blur_commits_valid_draft(self):
        page = self._native_engineering_precision()
        before = page.presentation
        self.assertNotEqual(before.colorbar.significant_digits, 5)
        edit_field(self.w, 'bar_precision', '5', commit=False)
        self.assertEqual(page.presentation, before)
        self.assertEqual(self.w.workspace.active.presentation, before)
        self.assertEqual(page.formal._drafts.get('bar_precision'), '5')

        choice, popup, receiver = self._native_format_popup()

        self.assertEqual(page.presentation.colorbar.significant_digits, 5)
        self.assertEqual(self.w.workspace.active.presentation.colorbar.significant_digits, 5)
        self.assertFalse(page.has_pending_project_edits())
        self.assertIsNone(page.presentation_error)
        retained_receivers = self._native_decimal_selection(choice, popup, receiver, mouse=True)
        self.assertTrue(retained_receivers)
        self.assertEqual(page.presentation.colorbar.label_format, 'decimal')
        self.assertEqual(self.w.workspace.active.presentation.colorbar.label_format, 'decimal')

    def _assert_native_precision_blur_recovery(self, *, mouse):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from tests.formal_quick_helpers import input_receiver

        page = self._native_engineering_precision()
        before = page.presentation
        science = self.w.current_cube.z.copy()
        precision = edit_field(self.w, 'bar_precision', '0', commit=False)
        error_focus = []
        page.formal.focusRequested.connect(lambda section, field: error_focus.append((section, field)))
        QTest.keyClick(input_receiver(self.w, precision), Qt.Key_Return)
        pump(65)
        self.assertIn(('colorbar', 'bar_precision'), error_focus)
        self.assertTrue(precision.hasActiveFocus())
        self.assertEqual(page.formal._drafts.get('bar_precision'), '0')
        self.assertTrue(page.presentation_error)
        self.assertIn('from 1 to 12', page.presentation_error)
        self.assertEqual(page.presentation, before)
        self.assertEqual(self.w.workspace.active.presentation, before)
        self.assertFalse(page.command_enabled('export_svg'))
        error_focus.clear()
        with self.assertRaises(ValueError):
            page.finish_editor()
        pump(65)
        self.assertIn(('colorbar', 'bar_precision'), error_focus)
        self.assertTrue(precision.hasActiveFocus())
        self.assertEqual(page.formal._drafts.get('bar_precision'), '0')

        choice, popup, receiver = self._native_format_popup()
        retained_receivers = self._native_decimal_selection(choice, popup, receiver, mouse=mouse)

        self.assertTrue(retained_receivers)
        self.assertEqual(page.presentation.colorbar.label_format, 'decimal')
        self.assertEqual(self.w.workspace.active.presentation.colorbar.label_format, 'decimal')
        self.assertIsNone(page.presentation_error)
        self.assertFalse(page.has_pending_project_edits())
        np.testing.assert_array_equal(self.w.current_cube.z, science)
        edit_field(self.w, 'bar_precision', '0', commit=True)
        self.assertEqual(page.presentation.colorbar.decimal_places, 0)
        self.assertEqual(self.w.workspace.active.presentation.colorbar.decimal_places, 0)
        self.assertIsNone(page.presentation_error)

    def test_native_precision_blur_recovers_decimal_by_mouse(self):
        self._assert_native_precision_blur_recovery(mouse=True)

    def test_native_precision_blur_recovers_decimal_by_keyboard(self):
        self._assert_native_precision_blur_recovery(mouse=False)

    def test_precision_blur_rejects_captured_owner_after_context_changes(self):
        page = self.controls()
        w = self.w

        def capture():
            current = w.plot_page
            source = item(w, 'formalProperties')
            token = current.formal.capturePropertiesInput(source)
            self.assertTrue(token)
            return current, source, token

        def reject(captured):
            outgoing, source, token = captured
            before = {(dataset_id, graph_id): graph.presentation
                      for dataset_id, session in w.workspace.datasets.items()
                      for graph_id, graph in session.graphs.items()}
            drafts = dict(outgoing.formal._drafts)
            self.assertFalse(outgoing.formal.commitFieldOnBlur('bar_precision', source, token))
            self.assertEqual(outgoing.formal._drafts, drafts)
            self.assertEqual({(dataset_id, graph_id): graph.presentation
                              for dataset_id, session in w.workspace.datasets.items()
                              for graph_id, graph in session.graphs.items()}, before)

        captured = capture()
        graph = w.create_graph('Blur owner')
        w.open_graph_document(graph_id=graph.graph_id)
        w.request_task(2)
        pump(70)
        self.assertIsNot(w.plot_page, page)
        with self.subTest(context='graph'):
            reject(captured)
        captured = capture()
        second_path = self.folder / 'second-blur.dat'
        second_path.write_text(self.path.read_text(encoding='utf-8'), encoding='utf-8')
        w._load_dat_file_direct(second_path)
        w.open_graph_document()
        w.request_task(2)
        pump(70)
        with self.subTest(context='dataset'):
            reject(captured)
        captured = capture()
        self.host.moveProperties(True)
        pump(70)
        self.assertTrue(self.host._floating_properties.isVisible())
        self.assertNotEqual(w.plot_page.formal.capturePropertiesInput(captured[1]), captured[2])
        with self.subTest(context='placement'):
            reject(captured)

    def test_controls_update_precision_meaning_live_sample_and_duplicate_warning(self):
        page = self.controls()
        self.w._heat_mesh.set_clim(920., 930.)
        self.w._apply_live_heatmap_style()
        page.formal.setValue('bar_label_format', 'scientific')
        precision = edit_field(self.w, 'bar_precision', '0', commit=True)
        self.assertIn('Mantissa decimal places', precision.parentItem().property('label'))
        self.assertEqual(page.formal.state['sample'], '9e2 | 9e2 | 9e2')
        self.assertIn('duplicate', page.formal.state['warning'].lower())
        self.assertTrue(item(self.controller, 'bar_warning').isVisible())
        page.formal.setValue('bar_label_format', 'engineering')
        self.assertIn('Significant digits', precision.parentItem().property('label'))
        edit_field(self.w, 'bar_precision', '0', commit=True)
        self.assertTrue(page.presentation_error)
        self.assertGreaterEqual(page.presentation.colorbar.significant_digits, 1)
        page.formal.setValue('bar_label_format', 'decimal')
        edit_field(self.w, 'bar_precision', '0', commit=True)
        self.assertIsNone(page.presentation_error)
        self.assertEqual(page.presentation.colorbar.decimal_places, 0)
        self.assertIn('Decimal places', precision.parentItem().property('label'))

    def test_native_precision_focus_survives_automatic_paper_growth(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from tests.formal_quick_helpers import ensure_visible, input_receiver, replace_text

        page = self.controls()
        w = self.w
        w._plot_render_timer.stop()
        w._heat_mesh.set_clim(920., 930.)
        w._apply_live_heatmap_style()
        page.formal.focusRequested.emit('colorbar', 'bar_label_format')
        pump(65)
        choice = ensure_visible(item(w, 'bar_label_format'))
        receiver = input_receiver(w, choice)
        self.assertTrue(choice.hasActiveFocus())
        QTest.keyClick(receiver, Qt.Key_Home)
        QTest.keyClick(receiver, Qt.Key_Down)
        QTest.keyClick(receiver, Qt.Key_Down)
        self.assertEqual(page.presentation.colorbar.label_format, 'scientific')
        w._plot_render_timer.stop()
        precision = edit_field(w, 'bar_precision', '2', commit=False)
        self.assertFalse(page.has_pending_project_edits())
        token = precision.property('inputOwner')
        generation = page._input_generation
        gesture = page.preview._generation
        before = page.presentation

        w._render_plot_page()

        self.assertGreater(page.presentation.width_in, before.width_in)
        self.assertTrue(precision.hasActiveFocus())
        self.assertEqual(precision.property('inputOwner'), token)
        self.assertEqual(page._input_generation, generation)
        self.assertGreater(page.preview._generation, gesture)
        self.assertEqual(page.rendered_page.owner_token, page.expected_owner)
        self.assertTrue(page.command_enabled('export_svg'))
        receiver = input_receiver(w, precision)
        replace_text(receiver, '0')
        QTest.keyClick(receiver, Qt.Key_Return)
        pump()
        self.assertEqual(page.presentation.colorbar.decimal_places, 0)
        self.assertEqual(page.formal.state['sample'], '9e2 | 9e2 | 9e2')
        self.assertIn('duplicate', page.formal.state['warning'].lower())
        w._render_plot_page()
        self.assertEqual(page.rendered_page.owner_token, w._plot_owner_token())

    def test_native_precision_draft_survives_pending_render_until_return(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from tests.formal_quick_helpers import input_receiver

        page = self.controls()
        w = self.w
        w._heat_mesh.set_clim(920., 930.)
        w._apply_live_heatmap_style()
        page.formal.setValue('bar_label_format', 'scientific')
        w._render_plot_page()
        precision = edit_field(w, 'bar_precision', '0', commit=False)
        self.assertEqual(page.formal._drafts, {'bar_precision': '0'})
        token = precision.property('inputOwner')
        generation = page._input_generation
        gesture = page.preview._generation
        owner = page.expected_owner

        w._render_plot_page()

        self.assertEqual(page.formal._drafts, {'bar_precision': '0'})
        self.assertEqual(page.expected_owner, owner)
        self.assertEqual(page._input_generation, generation)
        self.assertEqual(precision.property('inputOwner'), token)
        self.assertTrue(precision.hasActiveFocus())
        self.assertGreater(page.preview._generation, gesture)
        self.assertIsNone(page.rendered_page)
        self.assertFalse(page.command_enabled('export_svg'))
        QTest.keyClick(input_receiver(w, precision), Qt.Key_Return)
        pump()
        self.assertFalse(page.has_pending_project_edits())
        self.assertIsNone(page.presentation_error)
        self.assertEqual(page.presentation.colorbar.decimal_places, 0)
        self.assertEqual(page.formal.state['sample'], '9e2 | 9e2 | 9e2')
        self.assertIn('duplicate', page.formal.state['warning'].lower())
        w._render_plot_page()
        self.assertEqual(page.rendered_page.owner_token, w._plot_owner_token())

        # The opt-in cannot preserve a changed document/scientific context.
        current = page.expected_owner
        for index in (0, 1, 2, 3, 5):
            changed = list(current)
            changed[index] = (changed[index] + 1 if isinstance(changed[index], int)
                              else changed[index] + '-other')
            generation = page._input_generation
            with self.subTest(owner_component=index):
                page.invalidate(tuple(changed), preserve_input=True)
                self.assertEqual(page._input_generation, generation + 1)
            page.invalidate(current)
        generation = page._input_generation
        previous_revision = (*current[:4], current[4] - 1, *current[5:])
        page.invalidate(previous_revision, preserve_input=True)
        self.assertEqual(page._input_generation, generation + 1)
        page.invalidate(current)
        generation = page._input_generation
        skipped_revision = (*current[:4], current[4] + 2, *current[5:])
        page.invalidate(skipped_revision, preserve_input=True)
        self.assertEqual(page._input_generation, generation + 1)
        page.invalidate(current)
        generation = page._input_generation
        page.invalidate(current)
        self.assertEqual(page._input_generation, generation + 1)

    def test_live_and_formal_colorbar_geometry_stays_fixed_and_commit_shows_warning(self):
        w = self.w
        w.open_graph_document()
        self.assertTrue(w.request_outer_view('analysis'))
        w.request_task(2)
        w.plot_page.formal.revealRequested.emit('colorbar', 'bar_label_format')
        pump(65)
        for count in (1, 2, 3):
            dat.set_choice(w, 'color_scale', 'global' if count == 1 else str(count))
            w.plot_page.formal.selectBar('global' if count == 1 else 'region-1')
            p = replace(w.workspace.active.presentation, width_in=7., height_in=5.,
                        colorbar=replace(w.workspace.active.presentation.colorbar, width_in=.7))
            previous = None
            for digits in (2, 6):
                w.set_plot_presentation(replace(p, colorbar=replace(p.colorbar, significant_digits=digits)))
                w._render_plot_page()
                page = w.plot_page.rendered_page
                self.assertIsNotNone(page, w.plot_page.status_text)
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
                    self.assertIn(page.warnings[0], w.plot_page.formal.state['warning'])
                    self.assertTrue(item(w, 'bar_warning').isVisible())
                    self.assertTrue(w.plot_page.command_enabled('export_svg'))
                previous = bars
        warning = page.warnings[0]
        p = w.workspace.active.presentation
        w.set_plot_presentation(replace(p, colorbar=replace(p.colorbar, width_in=1.4, gap_in=.6)))
        self.assertNotIn(warning, w.plot_page.formal.state['warning'])
        w._render_plot_page()
        self.assertIsNotNone(w.plot_page.rendered_page, w.plot_page.status_text)
        self.assertEqual(w.plot_page.rendered_page.warnings, ())
        self.assertNotIn(warning, w.plot_page.formal.state['warning'])

    def test_sample_uses_current_live_norm_and_missing_selected_bar_is_unavailable(self):
        page = self.controls()
        self.w._heat_mesh.set_clim(0., 926.)
        self.w._apply_live_heatmap_style()
        self.assertEqual(page.formal.state['sample'], '0 | 460 | 930')
        self.w._heat_mesh.set_clim(0., 930.)
        self.w._apply_live_heatmap_style()
        self.assertEqual(page.formal.state['sample'], '0 | 460 | 930')
        page.formal.selectBar('region-3')
        self.assertIn('unavailable', page.formal.state['sample'].lower())
        dat.set_choice(self.w, 'color_scale', '3')
        self.assertNotIn('unavailable', page.formal.state['sample'].lower())
        self.assertEqual(page.formal.state['sample'].split(' | '),
                         [text.get_text() for text in self.w._heat_colorbars[2].ax.get_xticklabels()])

    def test_ui_changes_retire_exports_preserve_science_dimensions_and_sidecar_restore(self):
        import time
        from PySide6.QtTest import QTest

        page = self.controls(); w = self.w
        # Reserve sufficient paper for the intermediate two-decimal scientific
        # labels; automatic paper growth is covered separately above.
        p = replace(w.workspace.active.presentation, width_in=114.5/25.4, frame_width_in=2.9,
                    colorbar=replace(w.workspace.active.presentation.colorbar, width_in=.7000000001,
                                     height_in=.0650000001, gap_in=.1200000001, frame_gap_in=.0250000001,
                                     ticks={'global': ColorbarTicks((20., 465., 926.))}))
        w.set_plot_presentation(p); w._render_plot_page()
        self.assertIsNotNone(page.rendered_page)
        dimensions = (114.5/25.4, p.height_in, 2.9, p.frame_height_in)
        self.assertEqual(tuple(getattr(page.presentation, field) for field in
                               ('width_in', 'height_in', 'frame_width_in', 'frame_height_in')), dimensions)
        cube, mesh, norm = w.current_cube, w._heat_mesh, w._heat_mesh.norm
        arrays = cube.z.copy(); limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), mesh.get_clim()
        page.formal.setValue('bar_label_format', 'scientific')
        w._render_plot_page()
        self.assertIsNotNone(page.rendered_page, page.status_text)
        self.assertEqual(tuple(getattr(page.presentation, field) for field in
                               ('width_in', 'height_in', 'frame_width_in', 'frame_height_in')), dimensions)
        edit_field(self.w, 'bar_precision', '0', commit=True)
        self.assertIsNone(page.rendered_page)
        self.assertFalse(page.command_enabled('export_svg'))
        self.assertIs(w.current_cube, cube); self.assertIs(w._heat_mesh, mesh); self.assertIs(mesh.norm, norm)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), mesh.get_clim()), limits)
        np.testing.assert_array_equal(cube.z, arrays)
        self.assertEqual(tuple(getattr(page.presentation, field) for field in
                               ('width_in', 'height_in', 'frame_width_in', 'frame_height_in')), dimensions)
        self.assertEqual(tuple(getattr(page.presentation.colorbar, name) for name in
                               ('width_in', 'height_in', 'gap_in', 'frame_gap_in')),
                         (.7000000001, .0650000001, .1200000001, .0250000001))
        w._render_plot_page()
        self.assertIsNotNone(page.rendered_page, page.status_text)
        self.assertEqual([text.get_text() for text in w._heat_colorbar.ax.get_xticklabels()], ['2e1', '5e2', '9e2'])
        saved = page.presentation
        sidecar = self.path.with_name(self.path.name + '.plot.json')
        # Opening the real graph saves its initial recipe. Closing must write
        # this later accepted edit rather than merely leave that file in place.
        self.assertTrue(sidecar.exists())
        before_close = sidecar.read_bytes()
        stored, notice = w._plot_state_store.load(self.path, w._loaded_source_fingerprint)
        self.assertEqual(notice, '')
        self.assertNotEqual(PlotPresentation.from_dict(stored['presentation']).colorbar, saved.colorbar)
        with patch.object(w, '_confirm_project_discard_async', lambda callback: callback()):
            w.close()
        pump()
        self.assertTrue(w._closing)
        deadline = time.monotonic() + 5
        while not sidecar.exists() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(sidecar.exists(), 'Accepted window close did not save the plot sidecar')
        self.assertNotEqual(sidecar.read_bytes(), before_close)
        @contextmanager
        def same_profile():
            yield self.folder
        with patch('tests.quick_application_helpers.isolated_settings', same_profile):
            host, folder, errors = self.enterContext(application())
        self.assertEqual(folder, self.folder)
        self.addCleanup(lambda: self.assertFalse(errors))
        self.assertIsNot(host.controller, w)
        self.w = host.controller
        self.w._show_error = self.fail
        self.w._load_dat_file_direct(self.path)
        self.assertEqual(self.w.workspace.active.presentation.colorbar, saved.colorbar)
        self.assertEqual(tuple(getattr(self.w.workspace.active.presentation, field) for field in
                               ('width_in', 'height_in', 'frame_width_in', 'frame_height_in')), dimensions)

