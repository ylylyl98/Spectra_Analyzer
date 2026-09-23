import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from matplotlib import rc_context
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog, QSlider, QSpinBox, QScrollArea
from tests.test_image_export import _cube
from tests.test_workflow import _fit_result
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings
from spectra_app.image_export import _build_heatmap_export_figure, export_figure_png

_APP = QApplication.instance() or QApplication([])

def wheel(widget):
    point = widget.rect().center()
    event = QWheelEvent(QPointF(point), QPointF(widget.mapToGlobal(point)), QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    QApplication.sendEvent(widget, event)
    _APP.processEvents()

class PresentationContractTests(unittest.TestCase):
    def setUp(self):
        self.settings_path = isolate_settings(self)
        self.window = MainWindow()
        self.window.current_cube = replace(_cube(np.arange(24.).reshape(3,8)), gate_label='Doping (V)', source_path='sample.dat')
        self.window.current_folder = str(self.settings_path.parent)
        self.window._set_view_limits_to_auto()
        self.window._plot_cube()
    def tearDown(self):
        self.window.close()

    def test_wheel_preserves_focused_controls_and_scrolls_parent_page(self):
        w = self.window
        w.show()
        _APP.processEvents()
        scroll = w.findChild(QScrollArea)
        scroll.verticalScrollBar().setValue(0)
        for control in (w.cmap_combo, w.peak_smooth_win_spin, w.xmin_spin):
            control.setFocus()
            before = control.currentIndex() if hasattr(control, 'currentIndex') else control.value()
            wheel(control)
            after = control.currentIndex() if hasattr(control, 'currentIndex') else control.value()
            self.assertEqual(after, before)
        self.assertGreater(scroll.verticalScrollBar().value(), 0)
        dialog = QDialog(w)
        for control in (QSpinBox(dialog), QSlider(Qt.Horizontal, dialog)):
            control.setValue(50)
            control.setFocus()
            wheel(control)
            self.assertEqual(control.value(), 50)
        calls = []
        w.canvas.mpl_connect('scroll_event', calls.append)
        limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim()
        wheel(w.canvas)
        self.assertEqual(calls, [])
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim()), limits)
        w.peak_smooth_win_spin.stepUp()
        self.assertEqual(w.peak_smooth_win_spin.value(), 12)

    def test_annotations_are_independent_exact_and_synchronized(self):
        w = self.window
        self.assertEqual(w._heat_ax.get_title(), '')
        self.assertEqual(w.y_axis_name_edit.text(), 'Doping')
        self.assertEqual(w.y_axis_unit_edit.text(), 'V')
        title = '  Sample_A~B  $literal$  '
        w.plot_title_edit.setText(title)
        w.y_axis_name_edit.setText('Bias')
        w.y_axis_unit_edit.setText('mV')
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
        w.y_axis_unit_edit.clear()
        self.assertEqual(w._heat_ax.get_ylabel(), 'Bias')

    def test_fixed_export_plot_area_all_fonts_and_landscape_preview(self):
        w = self.window
        for size in ((8.,6.), (12.,8.)):
            w.figure.set_size_inches(*size)
            w._layout_live_plots()
            w.canvas.draw()
            box = w._heat_ax.get_window_extent()
            self.assertAlmostEqual(box.width / box.height, 4 / 3, places=6)
            detached = _build_heatmap_export_figure(w.figure, selected_axes=(w._heat_ax, w._heat_colorbar.ax))
            detached.canvas.draw()
            box = detached.axes[0].get_window_extent()
            self.assertAlmostEqual(box.width / detached.dpi, 4.0, places=6)
            self.assertAlmostEqual(box.height / detached.dpi, 3.0, places=6)
            for figure in (w.figure, detached):
                for axis in figure.axes:
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
        w.current_cube = replace(w.current_cube, z=w.current_cube.z * 1e5)
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
                        self.assertGreaterEqual(gap, .03)
                        self.assertLessEqual(gap, .05)
                        quantity = bar.xaxis.label.get_window_extent(renderer)
                        self.assertGreater(quantity.y0, heat_bounds.y1)
                        offset = heat.yaxis.get_offset_text()
                        if offset.get_text():
                            self.assertFalse(offset.get_window_extent(renderer).overlaps(quantity))

    def test_compact_export_labels_fit_without_overlap_for_signed_scientific_axes(self):
        w = self.window
        for title in ('', 'Sample_A: gate scan'):
            w.plot_title_edit.setText(title)
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

    def test_manual_title_is_close_to_plot_upper_left_without_annotation_overlap(self):
        w = self.window
        for size in ((4., 5.), (8., 6.), (12., 8.)):
            w.figure.set_size_inches(*size)
            for title in ('', 'E=18V 9T Kp', 'E=18V 9T Kp — reflection gate scan'):
                w.plot_title_edit.setText(title)
                for limits in ((1., 3.), (10000., 30000.)):
                    w._heat_ax.set_ylim(limits)
                    detached = _build_heatmap_export_figure(w.figure, selected_axes=(w._heat_ax, w._heat_colorbar.ax))
                    for figure, heat, bar, artist in ((w.figure, w._heat_ax, w._heat_colorbar.ax, w._heat_ax.title),
                                                      (detached, *detached.axes, detached.texts[0])):
                        with self.subTest(size=size, title=title, limits=limits, exported=figure is detached):
                            figure.canvas.draw()
                            renderer = figure.canvas.get_renderer()
                            self.assertEqual(artist.get_text(), title)
                            self.assertEqual(bar.get_xlabel(), 'ΔR/R (a. u.)')
                            if not title:
                                continue
                            bounds = artist.get_window_extent(renderer)
                            plot = heat.get_window_extent(renderer)
                            self.assertAlmostEqual(bounds.x0, plot.x0, delta=.1)
                            gap = (bounds.y0 - plot.y1) / figure.dpi
                            self.assertGreaterEqual(gap, .03)
                            self.assertLess(gap, .40)
                            if title == 'E=18V 9T Kp' and limits == (1., 3.) and size != (4., 5.):
                                self.assertLessEqual(gap, .06)
                            for text in (bar.xaxis.label, *bar.get_xticklabels(), heat.yaxis.get_offset_text()):
                                if text.get_text():
                                    self.assertFalse(bounds.overlaps(text.get_window_extent(renderer)))

    def test_short_filename_is_readable_and_independent_of_title_and_view(self):
        w = self.window
        original = w._heatmap_export_filename()
        self.assertEqual(original, 'sample_export01.png')
        self.assertEqual(w._heatmap_export_filename(), original)
        w.plot_title_edit.setText('not_a_filename')
        w.fit_smooth_chk.setChecked(True)
        self.assertEqual(w._heatmap_export_filename(), original)
        w.y_axis_unit_edit.setText('mV')
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
        w.current_cube = replace(w.current_cube, source_path=source)
        filename = w._heatmap_export_filename()
        self.assertEqual(filename, stem + '_export01.png')
        w.current_cube = replace(w.current_cube, source_path=source.replace('rep01', 'rep02'))
        self.assertNotEqual(w._heatmap_export_filename(), filename)
        w.current_cube = replace(w.current_cube, source_path=source)
        w.plot_title_edit.setText('Independent display title')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / filename
            w.current_folder = tmp
            with patch('spectra_app.main_window.QFileDialog.getSaveFileName', side_effect=AssertionError('PNG must save without a dialog')):
                w._on_export_png()
            with Image.open(path) as image:
                metadata = json.loads(image.info['SpectraAnalyzer'])
            self.assertEqual(metadata['source_path'], source)
            self.assertEqual(metadata['source_stem'], stem)
            self.assertEqual(metadata['x_limits'], [0., 10.])
            self.assertEqual(metadata['y_limits'], [1., 3.])
            self.assertEqual(metadata['color_limits'], list(w._heat_mesh.get_clim()))
            self.assertEqual(metadata['x_label'], 'X')
            self.assertEqual(metadata['y_label'], 'Doping (V)')
            self.assertEqual(metadata['signal_label'], 'ΔR/R (a. u.)')
            self.assertEqual(metadata['colormap'], 'RdBu_r')
            self.assertEqual(metadata['axis_original_type'], 'pixel')
            self.assertIsNone(metadata['axis_override'])
            self.assertEqual(metadata['processing'], 'raw')
            self.assertEqual(metadata['plot_title'], 'Independent display title')
        w.current_cube = replace(w.current_cube, source_path='$' + 's' * 240 + '.dat')
        self.assertEqual(w._heatmap_export_filename(), '$' + 's' * 240 + '_export01.png')
        w.current_cube = replace(w.current_cube, source_path='s' * 250 + '.dat')
        with self.assertRaisesRegex(ValueError, 'too long'):
            w._heatmap_export_filename()

    def test_png_export_uses_next_unused_sequence_in_current_dat_folder(self):
        w = self.window
        with tempfile.TemporaryDirectory() as tmp:
            w.current_folder = tmp
            existing = Path(tmp) / 'sample_export01.png'
            existing.write_bytes(b'preserve original export')
            third = Path(tmp) / 'sample_export03.png'
            third.write_bytes(b'preserve third export')
            with patch('spectra_app.main_window.QFileDialog.getSaveFileName', side_effect=AssertionError('PNG must save without a dialog')):
                w._on_export_png()
                self.assertTrue((Path(tmp) / 'sample_export02.png').is_file())
                w._on_export_png()
                self.assertTrue((Path(tmp) / 'sample_export04.png').is_file())
            self.assertEqual(existing.read_bytes(), b'preserve original export')
            self.assertEqual(third.read_bytes(), b'preserve third export')
            self.assertEqual(w._heatmap_export_filename(), 'sample_export05.png')
            next_folder = Path(tmp) / 'next'
            next_folder.mkdir()
            w.current_folder = str(next_folder)
            w._on_export_png()
            self.assertTrue((next_folder / 'sample_export01.png').is_file())

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
