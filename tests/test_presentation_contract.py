from tests import ordinary_analysis_quick_helpers as oa
from tests import dat_properties_quick_helpers as dat
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from pathlib import Path
import json
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from matplotlib import rc_context
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from tests import quick_migration_case as migration
from tests import source_navigation_helpers as sn
from tests.quick_application_helpers import pump
from tests.formal_quick_helpers import item, ensure_visible
from tests.test_image_export import _cube
from tests.scientific_fixtures import workflow_fit_result as _fit_result
from spectra_app.dataset_state import canonical_path
from spectra_app.image_export import _build_heatmap_export_figure, export_figure_png

def wheel(receiver, control):
    point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
    QTest.mouseMove(receiver, point)
    pump()
    event = QWheelEvent(QPointF(point), QPointF(receiver.mapToGlobal(point)),
                        QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
    # Flickable uses input timestamps to measure wheel velocity; independent
    # native events must not all appear to occur at timestamp zero.
    event.setTimestamp(time.monotonic_ns() // 1_000_000)
    QCoreApplication.sendEvent(receiver, event)
    pump()


class PresentationContractTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.errors = self.quick_errors
        source = self.folder / 'sample.dat'
        cube = replace(_cube(np.arange(24.).reshape(3, 8)),
                       gate_label='Doping (V)', source_path=str(source))
        source.write_text('X ' + ' '.join(f'{gate:.17g}' for gate in cube.gate) + '\n' +
                          '\n'.join(' '.join(f'{value:.17g}' for value in row)
                                    for row in np.column_stack((cube.energy, cube.z.T))),
                          encoding='utf-8')
        session = self.window.initialize_dataset(
            source, (cube, None, None, ''), self.window.snapshot_dataset_defaults())
        session.sidecar_writes_enabled = False
        self.window.register_dataset(session)
        self.window.activate_dataset(session.dataset_id, capture_outgoing=False)
        self.window.current_folder = str(self.folder)
        self.window._set_view_limits_to_auto()
        self.window._plot_cube()
        self.window.open_graph_document()
        self.assertTrue(self.window.request_outer_view('analysis'))
        self.window.dat_details_action.setChecked(True)
        self.window.request_task(2)
        pump(60)

    def tearDown(self):
        self.assertEqual(self.quick_errors, [], 'Unexpected production Quick error')

    def set_cube(self, cube):
        # A raw graph renders Original data, independently of applied data.
        self.window._base_cube = self.window.current_cube = cube
        self.bind_source_path(cube.source_path)

    def bind_source_path(self, path):
        workspace, session = self.window.workspace, self.window.workspace.active
        workspace.path_index = {key: owner for key, owner in workspace.path_index.items()
                                if owner != session.dataset_id}
        session.source_path = Path(path)
        workspace.path_index[canonical_path(path)] = session.dataset_id
        self.assertIs(workspace.dataset_for_path(path), session)

    def move_source_to(self, folder):
        """Copy the real DAT to the canonical output folder under test."""
        session = self.window.workspace.active
        source = Path(folder) / session.source_path.name
        # A cube can retain its original measurement provenance while the
        # imported DAT copy supplies the actual source folder for exports.
        original = self.folder / 'sample.dat'
        if source != original:
            shutil.copy2(original, source)
        self.bind_source_path(source)
        self.window.current_folder = str(folder)

    def test_wheel_preserves_focused_controls_and_scrolls_parent_page(self):
        from spectra_app.curve_prompts import CurveColorPromptBridge
        from spectra_app.quick_dialog import QuickDialog
        w = self.window
        w.root_window.resize(1100, 680)
        w.root_window.show()
        pump()
        oa.select_tab(w, 0)
        for field in ('colormap', 'xmin'):
            control = dat.reveal(w, field)
            control.forceActiveFocus()
            scroll = item(w, 'propertiesScroll').property('contentItem')
            before = dat.state(w)[field]
            before_scroll = scroll.property('contentY')
            wheel(w.root_window, control)
            self.assertEqual(dat.state(w)[field], before)
            self.assertGreater(scroll.property('contentY'), before_scroll,
                               (field, scroll.property('contentHeight'), control.isVisible()))
        oa.set_value(w, 'peak_smooth', True)
        control = oa.focus(w, 'peak_smooth_window')
        scroll = item(w, 'propertiesScroll').property('contentItem')
        # Keep the revealed editor inside the actual viewport; resetting this
        # taller Quick form to its top would send the wheel below the window.
        before_scroll = scroll.property('contentY')
        center = control.mapToItem(scroll, QPointF(control.width()/2, control.height()/2))
        self.assertTrue(0 <= center.y() <= scroll.height())
        self.assertLess(before_scroll, scroll.property('contentHeight') - scroll.height())
        before = oa.value(w, 'peak_smooth_window')
        wheel(w.root_window, control)
        self.assertEqual(oa.value(w, 'peak_smooth_window'), before)
        self.assertGreater(scroll.property('contentY'), before_scroll,
                           (type(control).__name__, scroll.property('contentHeight'), control.isVisible()))

        # Cover the real production SpinBox and modal Slider, replacing the
        # retired generic QWidget controls with actual available consumers.
        sn.reveal(w)
        spin = ensure_visible(item(w, 'sourceVisibleRows'))
        spin.forceActiveFocus()
        before = spin.property('value')
        wheel(w.root_window, spin)
        self.assertEqual(spin.property('value'), before)
        bridge = CurveColorPromptBridge('#336699')
        dialog = QuickDialog(bridge, 'CurveColorPrompt.qml', w,
                             title='Curve color', size=(500, 570), minimum_size=(380, 470))
        self.addCleanup(dialog.deleteLater)
        bridge.acceptRequested.connect(dialog.accept)
        bridge.rejectRequested.connect(dialog.reject)
        dialog.show()
        pump()
        slider = ensure_visible(item(dialog, 'curveColorHue'))
        slider.forceActiveFocus()
        before = slider.property('value')
        wheel(dialog.window(), slider)
        self.assertEqual(slider.property('value'), before)
        dialog.close()
        pump()

        calls = []
        w.canvas.mpl_connect('scroll_event', calls.append)
        limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim()
        wheel(w.root_window, item(w, 'analysisCanvasInput'))
        # Quick preserves the documented Matplotlib registry callback while
        # scrolling its viewport; the canvas bridge contract requires no zoom.
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0].canvas, w.canvas)
        self.assertEqual(calls[0].step, -1)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim()), limits)
        oa.key(w, 'peak_smooth_window', Qt.Key_Up)
        self.assertEqual(oa.value(w, 'peak_smooth_window'), 13)

    def test_annotations_are_independent_exact_and_synchronized(self):
        w = self.window
        self.assertEqual(w._heat_ax.get_title(), '')
        self.assertEqual(dat.state(w)['y_axis_name'], 'Doping')
        self.assertEqual(dat.state(w)['y_axis_unit'], 'V')
        title = '  Sample_A~B  $literal$  '
        w.dat_properties.setLabel('plot_title', title, w.dat_properties.ownerToken)
        w.dat_properties.setLabel('y_axis_name', 'Bias', w.dat_properties.ownerToken)
        w.dat_properties.setLabel('y_axis_unit', 'mV', w.dat_properties.ownerToken)
        self.assertEqual(w._heat_ax.get_title(), title)
        self.assertEqual(w._heat_ax.get_ylabel(), 'Bias (mV)')
        w.canvas.draw()
        renderer = w.canvas.get_renderer()
        title_box = w._heat_ax.title.get_window_extent(renderer)
        colorbar_ticks = [text.get_window_extent(renderer) for text in w._heat_colorbar.ax.get_xticklabels()]
        self.assertTrue(all(not title_box.overlaps(box) for box in colorbar_ticks))
        exported = _build_heatmap_export_figure(w.figure, selected_axes=(w._heat_ax, w._heat_colorbar.ax))
        self.assertEqual(exported.texts[0].get_text(), title)
        self.assertEqual(exported.axes[0].get_ylabel(), 'Bias (mV)')
        np.testing.assert_array_equal(w.current_cube.gate, [1., 2., 3.])
        w.dat_properties.setLabel('y_axis_unit', '', w.dat_properties.ownerToken)
        self.assertEqual(w._heat_ax.get_ylabel(), 'Bias')

    def test_live_presentation_fonts_and_legacy_fixed_export_contract(self):
        w = self.window
        for size in ((8.,6.), (12.,8.)):
            w.figure.set_size_inches(*size)
            w._layout_live_plots()
            w.canvas.draw()
            box = w._heat_ax.get_window_extent()
            self.assertAlmostEqual(box.width / box.height, 2.9/2.8, places=6)
            p = (w.workspace.active or w._unregistered_session).presentation
            factor = 96*w._analysis_display_scale()*w.canvas.device_pixel_ratio/w.figure.dpi
            for axis in (w._heat_ax, w._heat_colorbar.ax):
                self.assertTrue(all(text.get_fontsize() == p.text_pt*factor
                                    for text in (axis.title, axis.xaxis.label, axis.yaxis.label)))
                self.assertTrue(all(text.get_fontsize() == p.numeric_pt*factor
                                    for text in (*axis.get_xticklabels(), *axis.get_yticklabels())))
            self.assertTrue(all(text.get_fontsize() == p.numeric_pt*factor
                                for text in (w._heat_ax.xaxis.get_offset_text(), w._heat_ax.yaxis.get_offset_text())))
            detached = _build_heatmap_export_figure(w.figure, selected_axes=(w._heat_ax, w._heat_colorbar.ax))
            detached.canvas.draw()
            box = detached.axes[0].get_window_extent()
            self.assertAlmostEqual(box.width / detached.dpi, 4.0, places=6)
            self.assertAlmostEqual(box.height / detached.dpi, 3.0, places=6)
            for figure, axes in ((w.figure, (w._spec_ax,)), (detached, detached.axes)):
                for axis in axes:
                    texts = [axis.title, axis.xaxis.label, axis.yaxis.label, axis.xaxis.get_offset_text(), axis.yaxis.get_offset_text(), *axis.get_xticklabels(), *axis.get_yticklabels()]
                    self.assertTrue(all(text.get_fontsize() == 12 for text in texts))
            with tempfile.TemporaryDirectory() as tmp:
                with rc_context({'savefig.bbox': 'tight'}):
                    path = export_figure_png(w.figure, Path(tmp)/'sample.png', selected_axes=(w._heat_ax,w._heat_colorbar.ax))
                with Image.open(path) as image:
                    self.assertEqual(image.size, (1545,1242))
                    self.assertAlmostEqual(image.info['dpi'][0], 300, delta=.01)

    def test_large_intensity_spectrum_label_stays_inside_canvas(self):
        w = self.window
        self.set_cube(replace(w.current_cube, z=w.current_cube.z * 1e5))
        w._plot_cube()
        w.canvas.draw()
        box = w._spec_ax.yaxis.label.get_window_extent(w.canvas.get_renderer())
        self.assertGreaterEqual(box.x0, 0)

    def test_colorbar_gap_is_narrow_without_scientific_offset_overlap(self):
        w = self.window
        for size in ((4., 5.), (8., 6.), (12., 8.)):
            w.figure.set_size_inches(*size)
            w._layout_live_plots()
            for limits in ((1., 3.), (10000., 30000.), (1e9, 1e9 + 2)):
                w._heat_ax.set_ylim(limits)
                detached = _build_heatmap_export_figure(
                    w.figure, selected_axes=(w._heat_ax, w._heat_colorbar.ax))
                for figure, heat, bar in ((w.figure, w._heat_ax, w._heat_colorbar.ax),
                                           (detached, *detached.axes)):
                    with self.subTest(size=size, limits=limits, exported=figure is detached):
                        figure.canvas.draw()
                        renderer = figure.canvas.get_renderer()
                        heat_bounds = heat.get_window_extent(renderer)
                        gap = (bar.get_window_extent(renderer).y0 - heat_bounds.y1) / figure.dpi
                        if figure is w.figure:
                            factor = 96*w._analysis_display_scale()*w.canvas.device_pixel_ratio/w.figure.dpi
                            self.assertAlmostEqual(gap, w.workspace.active.presentation.colorbar.frame_gap_in*factor)
                        else:
                            self.assertGreaterEqual(gap, .03)
                            self.assertLessEqual(gap, .05)
                        quantity = bar.xaxis.label.get_window_extent(renderer)
                        self.assertGreater(quantity.y0, heat_bounds.y1)
                        offset = heat.yaxis.get_offset_text()
                        if offset.get_text():
                            self.assertFalse(offset.get_window_extent(renderer).overlaps(quantity))

    def test_regional_colorbars_are_compact_and_share_one_signal_label(self):
        w = self.window
        w.dat_properties.setLabel('signal_title', "d²(ΔR/R)/dE² (a. u.)", w.dat_properties.ownerToken)
        w.dat_properties.setLabel('plot_title', "Regional title", w.dat_properties.ownerToken)
        for mode, names in (("2", ("Left", "Right")), ("3", ("Left", "Middle", "Right"))):
            with self.subTest(mode=mode):
                dat.set_choice(w, 'color_scale', mode)
                w.canvas.draw()
                p = w.workspace.active.presentation
                factor = 96*w._analysis_display_scale()*w.canvas.device_pixel_ratio/w.figure.dpi
                self.assertEqual([bar.ax.get_xlabel() for bar in w._heat_colorbars], [""] * len(names))
                self.assertTrue(all(
                    abs(bar.ax.get_position().width * w.figure.get_size_inches()[0] - p.colorbar.width_in*factor) < 1e-6
                    and abs(bar.ax.get_position().height * w.figure.get_size_inches()[1] - p.colorbar.height_in*factor) < 1e-6
                    for bar in w._heat_colorbars
                ))
                self.assertTrue(all(
                    text.get_fontsize() == p.numeric_pt*factor
                    for bar in w._heat_colorbars
                    for text in bar.ax.get_xticklabels()
                ))
                self.assertEqual(w._heat_signal_label.get_fontsize(), p.text_pt*factor)
                self.assertTrue(all(len(bar.get_ticks()) == 3 for bar in w._heat_colorbars))
                self.assertEqual(
                    [text.get_text() for text in w.figure.texts if text.get_gid() == "heatmap_signal_label"],
                    ["d²(ΔR/R)/dE² (arb. units)"],
                )
                renderer = w.canvas.get_renderer()
                for previous, current in zip(w._heat_colorbars, w._heat_colorbars[1:]):
                    gap = (current.ax.get_position().x0 - previous.ax.get_position().x1) * w.figure.get_size_inches()[0]
                    self.assertAlmostEqual(gap, p.colorbar.gap_in*factor)
                title = w._heat_ax.title.get_window_extent(renderer)
                self.assertTrue(all(
                    not title.overlaps(tick.get_window_extent(renderer))
                    for bar in w._heat_colorbars
                    for tick in bar.ax.get_xticklabels()
                ))

    def test_regional_title_has_priority_over_compact_colorbar_row(self):
        w = self.window
        w.dat_properties.setLabel('signal_title', "d²(ΔR/R)/dE² (a. u.)", w.dat_properties.ownerToken)
        w.dat_properties.setLabel('plot_title', "A long regional heatmap title", w.dat_properties.ownerToken)
        dat.set_choice(w, 'color_scale', "3")
        w.canvas.draw()
        renderer = w.canvas.get_renderer()
        title = w._heat_ax.title.get_window_extent(renderer)
        page = w.figure.bbox
        self.assertGreaterEqual(title.x0, 0)
        self.assertLessEqual(title.x1, page.x1)
        self.assertLessEqual(title.y1, page.y1)
        self.assertTrue(all(
            not title.overlaps(tick.get_window_extent(renderer))
            for bar in w._heat_colorbars
            for tick in bar.ax.get_xticklabels()
            if tick.get_text()
        ))
        label = next(text for text in w.figure.texts if text.get_gid() == "heatmap_signal_label")
        label_box = label.get_window_extent(renderer)
        self.assertGreaterEqual(label_box.x0, 0)
        self.assertLessEqual(label_box.x1, page.x1)

    def test_view_settings_group_follows_control_workflows(self):
        w = self.window
        colors = dat.dat_item(w, 'shared_colors_section')
        ranges = dat.dat_item(w, 'shared_ranges_section')
        labels = dat.dat_item(w, 'shared_labels_section')
        self.assertLess(colors.y(), ranges.y())
        self.assertLess(ranges.y(), labels.y())
        self.assertFalse(dat.dat_item(w, 'edge0').isVisible())

    def test_compact_export_labels_fit_without_overlap_for_signed_scientific_axes(self):
        w = self.window
        for title in ('', 'Sample_A: gate scan'):
            w.dat_properties.setLabel('plot_title', title, w.dat_properties.ownerToken)
            for limits in ((1., 3.), (-3., -1.), (10000., 30000.), (-10000., -3000.)):
                with self.subTest(title=title, limits=limits):
                    w._heat_ax.set_xlim(limits)
                    w._heat_ax.set_ylim(limits)
                    detached = _build_heatmap_export_figure(w.figure, selected_axes=(w._heat_ax,w._heat_colorbar.ax))
                    detached.canvas.draw()
                    renderer = detached.canvas.get_renderer()
                    heat, bar = detached.axes
                    page = detached.bbox
                    for axis in (heat, bar):
                        bounds = axis.get_tightbbox(renderer)
                        self.assertGreaterEqual(bounds.x0, 0)
                        self.assertGreaterEqual(bounds.y0, 0)
                        self.assertLessEqual(bounds.x1, page.x1)
                        self.assertLessEqual(bounds.y1, page.y1)
                    quantity = bar.xaxis.label.get_window_extent(renderer)
                    offset = heat.yaxis.get_offset_text().get_window_extent(renderer)
                    self.assertFalse(offset.overlaps(quantity))
                    bar_bounds = bar.get_tightbbox(renderer)
                    self.assertGreater(bar_bounds.y0, heat.get_window_extent().y1)
                    self.assertLess((bar_bounds.y0 - heat.get_window_extent().y1) / detached.dpi, .15)
                    self.assertLess((page.y1 - bar_bounds.y1) / detached.dpi, .30)
                    if title:
                        title_bounds = detached.texts[0].get_window_extent(renderer)
                        self.assertFalse(title_bounds.overlaps(quantity))
                        self.assertTrue(all(not title_bounds.overlaps(text.get_window_extent(renderer)) for text in bar.get_xticklabels()))
                        self.assertLessEqual(title_bounds.y1, page.y1)

    def test_live_title_is_inside_frame_and_legacy_export_retains_header_contract(self):
        w = self.window
        for size in ((4., 5.), (8., 6.), (12., 8.)):
            w.figure.set_size_inches(*size)
            for title in ('', 'E=18V 9T Kp', 'E=18V 9T Kp — reflection gate scan'):
                w.dat_properties.setLabel('plot_title', title, w.dat_properties.ownerToken)
                for limits in ((1., 3.), (10000., 30000.)):
                    w._heat_ax.set_ylim(limits)
                    detached = _build_heatmap_export_figure(w.figure, selected_axes=(w._heat_ax, w._heat_colorbar.ax))
                    for figure, heat, bar, artist in ((w.figure, w._heat_ax, w._heat_colorbar.ax, w._heat_ax.title),
                                                      (detached, *detached.axes, detached.texts[0])):
                        with self.subTest(size=size, title=title, limits=limits, exported=figure is detached):
                            figure.canvas.draw()
                            renderer = figure.canvas.get_renderer()
                            self.assertEqual(artist.get_text(), title)
                            self.assertEqual(bar.get_xlabel(), 'Signal (arb. units)')
                            if not title:
                                continue
                            bounds = artist.get_window_extent(renderer)
                            plot = heat.get_window_extent(renderer)
                            if figure is w.figure:
                                self.assertAlmostEqual(bounds.x0, plot.x0+.02*plot.width, delta=.1)
                                self.assertAlmostEqual(bounds.y1, plot.y0+.98*plot.height, delta=.1)
                                self.assertTrue(artist.get_clip_on())
                                self.assertFalse(artist.get_in_layout())
                                continue
                            self.assertAlmostEqual(bounds.x0, plot.x0, delta=.1)
                            gap = (bounds.y0 - plot.y1) / figure.dpi
                            self.assertGreaterEqual(gap, .03)
                            self.assertLess(gap, .40)
                            if figure is detached and title == 'E=18V 9T Kp' and limits == (1., 3.) and size != (4., 5.):
                                self.assertLessEqual(gap, .06)
                            for text in (bar.xaxis.label, *bar.get_xticklabels(), heat.yaxis.get_offset_text()):
                                if text.get_text():
                                    self.assertFalse(bounds.overlaps(text.get_window_extent(renderer)))

    def test_short_filename_is_readable_and_independent_of_title_and_view(self):
        w = self.window
        original = w._heatmap_export_filename()
        self.assertEqual(original, 'sample_Graph 1_export01.png')
        self.assertEqual(w._heatmap_export_filename(), original)
        w.dat_properties.setLabel('plot_title', 'not_a_filename', w.dat_properties.ownerToken)
        oa.set_value(w, 'fit_smooth', True)
        self.assertEqual(w._heatmap_export_filename(), original)
        w.dat_properties.setLabel('y_axis_unit', 'mV', w.dat_properties.ownerToken)
        self.assertEqual(w._heatmap_export_filename(), original)
        w._heat_ax.set_xlim(2., 8.)
        self.assertEqual(w._heatmap_export_filename(), original)
        w._heat_mesh.set_clim(-3e4, 6e4)
        self.assertEqual(w._heatmap_export_filename(), original)
        metadata = w._heatmap_export_metadata()
        self.assertEqual(metadata['x_limits'], [2., 8.])
        self.assertEqual(metadata['color_limits'], [-3e4, 6e4])
        self.assertEqual(metadata['y_label'], 'Doping (mV)')

    def test_export_name_preserves_source_conditions_and_complete_embedded_metadata(self):
        w = self.window
        stem = 'YZD320~pa~9T3.6KRef0.2sx10~680nmc~R1_175deg~0.95TG-BG=18_rep01_001_avg13_DR_R_External_d2E_Regrid'
        source = 'C:/measurements/原始数据/' + stem + '.dat'
        self.set_cube(replace(w.current_cube, source_path=source))
        filename = w._heatmap_export_filename()
        self.assertEqual(filename, stem + '_Graph 1_export01.png')
        self.set_cube(replace(w.current_cube, source_path=source.replace('rep01', 'rep02')))
        self.assertNotEqual(w._heatmap_export_filename(), filename)
        self.set_cube(replace(w.current_cube, source_path=source))
        w.dat_properties.setLabel('plot_title', 'Independent display title', w.dat_properties.ownerToken)
        # Properties remains the current visible ordinary Analysis consumer.
        source_item = item(w, 'formalProperties')
        token = w.plot_page.formal.capturePropertiesInput(source_item)
        self.assertTrue(token)
        w.plot_page.formal.setValue('height_in', 4.5, source_item, token)  # Space for the long provenance title.
        w._render_plot_page()
        self.assertIsNotNone(w.plot_page.rendered_page, w.plot_page.status_text)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / filename
            self.move_source_to(tmp)
            with patch('spectra_app.main_window.FilePicker.getSaveFileName', side_effect=AssertionError('PNG must save without a dialog')):
                w._on_export_png()
            self.assertEqual(self.errors, [])
            with Image.open(path) as image:
                metadata = json.loads(image.info['SpectraAnalyzer'])
            self.assertEqual(metadata['source_path'], source)
            self.assertEqual(metadata['source_stem'], stem)
            self.assertEqual(metadata['x_limits'], [0., 10.])
            self.assertEqual(metadata['y_limits'], [1., 3.])
            self.assertEqual(metadata['color_limits'], list(w._heat_mesh.get_clim()))
            self.assertEqual(metadata['x_label'], 'X')
            self.assertEqual(metadata['y_label'], 'Doping (V)')
            self.assertEqual(metadata['signal_label'], 'Signal (arb. units)')
            self.assertEqual(metadata['colormap'], 'RdBu_r')
            self.assertEqual(metadata['axis_original_type'], 'pixel')
            self.assertIsNone(metadata['axis_override'])
            self.assertEqual(metadata['processing'], 'raw')
            self.assertEqual(metadata['plot_title'], 'Independent display title')
        self.set_cube(replace(w.current_cube, source_path='$' + 's' * 232 + '.dat'))
        self.assertEqual(w._heatmap_export_filename(), '$' + 's' * 232 + '_Graph 1_export01.png')
        self.set_cube(replace(w.current_cube, source_path='s' * 250 + '.dat'))
        with self.assertRaisesRegex(ValueError, 'too long'):
            w._heatmap_export_filename()

    def test_png_export_uses_next_unused_sequence_in_current_dat_folder(self):
        w = self.window
        w._render_plot_page()
        self.assertIsNotNone(w.plot_page.rendered_page, w.plot_page.status_text)
        with tempfile.TemporaryDirectory() as tmp:
            self.move_source_to(tmp)
            existing = Path(tmp) / 'sample_Graph 1_export01.png'
            existing.write_bytes(b'preserve original export')
            third = Path(tmp) / 'sample_Graph 1_export03.png'
            third.write_bytes(b'preserve third export')
            with patch('spectra_app.main_window.FilePicker.getSaveFileName', side_effect=AssertionError('PNG must save without a dialog')):
                w._on_export_png()
                self.assertEqual(self.errors, [])
                self.assertTrue((Path(tmp) / 'sample_Graph 1_export02.png').is_file())
                w._on_export_png()
                self.assertEqual(self.errors, [])
                self.assertTrue((Path(tmp) / 'sample_Graph 1_export04.png').is_file())
            self.assertEqual(existing.read_bytes(), b'preserve original export')
            self.assertEqual(third.read_bytes(), b'preserve third export')
            self.assertEqual(w._heatmap_export_filename(), 'sample_Graph 1_export05.png')
            next_folder = Path(tmp) / 'next'
            next_folder.mkdir()
            self.move_source_to(next_folder)
            w._on_export_png()
            self.assertEqual(self.errors, [])
            self.assertTrue((next_folder / 'sample_Graph 1_export01.png').is_file())

    def test_spectrum_legend_fit_stats_and_peak_annotations_use_twelve_point_text(self):
        w = self.window
        w.peak_indices = np.asarray([2], dtype=int)
        w._peak_gate_idx = w._current_gate_index()
        w.batch_fit_result_map[w._current_gate_index()] = _fit_result(3.0)
        w._update_spectrum_only()
        w.canvas.draw()
        legend = w._spec_ax.get_legend()
        self.assertIsNotNone(legend)
        self.assertTrue(legend.get_texts())
        self.assertTrue(w._heat_peak_texts)
        self.assertTrue(w._spec_ax.texts)
        for text in (*legend.get_texts(), *w._heat_peak_texts, *w._spec_ax.texts):
            self.assertEqual(text.get_fontsize(), 12)

    def test_twelve_point_preview_labels_do_not_overlap_spectrum_title(self):
        w = self.window
        for size in ((4., 5.), (8., 6.), (12., 8.)):
            with self.subTest(size=size):
                w.figure.set_size_inches(*size)
                w._layout_live_plots()
                w.canvas.draw()
                renderer = w.canvas.get_renderer()
                label = w._heat_ax.xaxis.label.get_window_extent(renderer)
                title = w._spec_ax.title.get_window_extent(renderer)
                self.assertGreater(label.y0, title.y1)
