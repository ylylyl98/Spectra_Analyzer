"""Real GUI event paths must not repeat expensive rendering for one edit."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.colors import Normalize, to_hex
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app import main_window, svg_export
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings
from tests.background_helpers import wait_jobs

APP = QApplication.instance() or QApplication([])


class RenderEventPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        path = self.folder / 'events.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/100} {i+1} {2*i+1} {3*i+1}' for i in range(100)))
        self.w = MainWindow()
        self.w._show_error = self.fail
        self.w._load_dat_file_direct(path)
        QTest.qWait(300)

    def tearDown(self):
        self.w.close()
        APP.processEvents()

    def settle(self):
        for _ in range(8):
            APP.processEvents()

    def test_analysis_edit_retires_bytes_and_defers_formal_render_until_plot_entry(self):
        w = self.w
        w._render_plot_page()
        old = w.plot_page.rendered_page
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render:
            w.xmin_spin.setValue(1.25)
            self.assertIsNone(w.plot_page.rendered_page)
            self.assertFalse(w.export_png_btn.isEnabled())
            QTest.qWait(350)
            self.assertEqual(render.call_count, 0)
            w.plot_tabs.setCurrentWidget(w.graph_documents)
            QTest.qWait(400)
            self.assertEqual(render.call_count, 1)
        page = w.plot_page.rendered_page
        self.assertIsNotNone(page, w.plot_page.status_label.text())
        self.assertNotEqual(page.owner_token, old.owner_token)
        self.assertEqual(page.owner_token, w._plot_owner_token())
        self.assertEqual(w.plot_page.preview.snapshot.x_limits[0], 1.25)

    def test_leaving_plot_cancels_pending_automatic_render(self):
        w = self.w
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        w._render_plot_page()
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render:
            w.xmin_spin.setValue(1.2)
            w.plot_tabs.setCurrentIndex(0)
            QTest.qWait(350)
            self.assertEqual(render.call_count, 0)
        self.assertIsNone(w.plot_page.rendered_page)
        w._render_plot_page()  # Explicit preparation remains available while hidden.
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())

    def test_visible_title_burst_produces_one_final_svg_and_layout(self):
        w = self.w
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        w._render_plot_page()
        self.settle()
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render, \
             patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout, \
             patch.object(w._annotation_settings, 'sync', wraps=w._annotation_settings.sync) as sync:
            QTest.keyClicks(w.plot_title_edit, 'One final title')
            self.assertEqual(w._heat_ax.get_title(), 'One final title')
            self.assertEqual(w.workspace.active.view.annotations['plot_title'], 'One final title')
            self.assertIsNone(w.plot_page.rendered_page)
            self.assertEqual(render.call_count, 0)
            QTest.qWait(700)
            self.assertEqual(render.call_count, 1)
            self.assertEqual(layout.call_count, 1)
            self.assertEqual(sync.call_count, 1)
        self.assertIn('One final title', w.plot_page.rendered_page.svg_bytes.decode())

    def test_gate_enter_and_focus_out_rebuild_changed_trace_once(self):
        w = self.w
        w._set_current_gate_from_index(0)
        w.plot_gate_spin.lineEdit().selectAll()
        with patch.object(w, '_update_spectrum_only_impl', wraps=w._update_spectrum_only_impl) as rebuild:
            QTest.keyClicks(w.plot_gate_spin.lineEdit(), '1')
            QTest.keyClick(w.plot_gate_spin, Qt.Key_Return)
            w._on_plot_gate_spin_edit_finished()
            self.assertEqual(rebuild.call_count, 1)
        self.assertEqual(w.current_gate, 1.)
        self.assertEqual(w.plot_gate_index_spin.value(), 2)
        np.testing.assert_equal(w._spec_ax.lines[0].get_ydata(), np.arange(100)*2+1)
        with patch.object(w, '_update_spectrum_only_impl', wraps=w._update_spectrum_only_impl) as rebuild:
            w._on_plot_gate_spin_edit_finished()
            w._set_current_gate_from_value(1.01)
            self.assertEqual(rebuild.call_count, 0)
            w._update_spectrum_only()  # Explicit peak/fit refresh is never suppressed.
            self.assertEqual(rebuild.call_count, 1)

    def test_cancel_paste_dialog_after_pending_title_restores_current_export_page(self):
        w = self.w
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        w.copy_plot_settings()
        self.settle()
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render:
            QTest.keyClicks(w.plot_title_edit, 'Captured title')
            dialog = w.create_paste_settings_dialog(current_only=True)
            dialog.reject()
            QTest.qWait(700)
            self.assertEqual(render.call_count, 1)
        self.assertTrue(w.export_png_btn.isEnabled())
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())
        self.assertIn('Captured title', w.plot_page.rendered_page.svg_bytes.decode())

    def test_locked_regional_bounds_skip_scans_and_keep_independent_limits(self):
        w = self.w
        for count in (2, 3):
            w.color_scale_combo.setCurrentIndex(count - 1)
            w.vmin_lock.setChecked(True)
            w.vmax_lock.setChecked(True)
            ranges = [(1. + 10*i, 5. + 10*i) for i in range(count)]
            w._last_valid_region_color_limits = ranges.copy()
            with patch.object(np, 'nanpercentile', wraps=np.nanpercentile) as percentile:
                actual = w._regional_color_ranges([np.array([100., 200.])] * count)
                self.assertEqual(percentile.call_count, 0)
            self.assertEqual(actual, ranges)
            self.assertEqual(w._regional_color_ranges([np.array([])] * count), ranges)

    def test_leaving_plot_finishes_title_layout_while_background_render_waits(self):
        w = self.w
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        w._render_plot_page()
        release = threading.Event()
        self.addCleanup(release.set)
        def work(context):
            release.wait(2)
            return 1
        w._start_background_job(work, lambda value: None, 'Waiting')
        with patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout:
            QTest.keyClicks(w.plot_title_edit, 'Deferred title')
            QTest.qWait(350)
            self.assertEqual(layout.call_count, 0)
            w.plot_tabs.setCurrentIndex(0)
            self.assertEqual(layout.call_count, 1)
        release.set()
        wait_jobs(w)
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        QTest.qWait(350)
        self.assertIn('Deferred title', w.plot_page.rendered_page.svg_bytes.decode())

    def test_gate_transfer_keeps_batch_peaks_and_unchanged_formal_owner(self):
        w = self.w
        w.set_plot_presentation(replace(w.workspace.active.presentation, include_peak_labels=True))
        w.batch_peak_map = {0: np.array([5]), 1: np.array([12])}
        w._set_current_gate_from_index(0)
        w._render_plot_page()
        before = w._plot_owner_token()
        w._set_current_gate_from_index(1)
        np.testing.assert_equal(w.peak_indices, [12])
        self.assertEqual(w._peak_gate_idx, 1)
        self.assertEqual(w._plot_owner_token(), before)
        w.peak_indices = np.array([15])
        w._update_spectrum_only()
        self.assertNotEqual(w._plot_owner_token(), before)
        w._render_plot_page()
        self.assertIn(1.15, [peak.x for peak in w.plot_page.preview.snapshot.peaks])

    def test_no_peak_spectrum_refresh_does_not_retire_formal_page(self):
        w = self.w
        w.set_plot_presentation(replace(w.workspace.active.presentation, include_peak_labels=True))
        w._render_plot_page()
        page = w.plot_page.rendered_page
        w._set_current_gate_from_index(1)
        w._update_spectrum_only()
        self.assertTrue(w.plot_page.rendered_page is page, 'Unchanged formal peak labels must retain current bytes')

    def test_shared_x_limit_updates_auto_ranges_and_stats_once_from_either_axes(self):
        w = self.w
        for ax, limits in ((w._heat_ax, (1.2, 1.8)), (w._spec_ax, (1.3, 1.7))):
            with patch.object(np, 'nanpercentile', wraps=np.nanpercentile) as percentile, \
                 patch.object(w, '_visible_color_values', wraps=w._visible_color_values) as visible:
                ax.set_xlim(*limits)
                self.assertEqual(percentile.call_count, 1)
                self.assertEqual(visible.call_count, 1)
            self.assertEqual(w.xmin_spin.value(), limits[0])
            np.testing.assert_allclose(w._heat_mesh.get_clim(), w._last_valid_color_limits)
            self.assertIn('visible valid cells', w.color_clipping_status.text())

    def test_numeric_x_range_clipping_counts_the_new_view_once(self):
        w = self.w
        with patch.object(w, '_visible_color_values', wraps=w._visible_color_values) as visible:
            w.xmin_spin.setValue(1.5)
            self.assertIn('of 150 visible valid cells', w.color_clipping_status.text())
            self.assertEqual(visible.call_count, 1)
        self.assertEqual(w._heat_ax.get_xlim()[0], 1.5)

    def test_locked_color_bounds_skip_percentiles_and_refresh_visible_statistics(self):
        w = self.w
        w.vmin_spin.setValue(50)
        w.vmax_spin.setValue(100)
        w.vmin_lock.setChecked(True)
        w.vmax_lock.setChecked(True)
        with patch.object(np, 'nanpercentile', wraps=np.nanpercentile) as percentile:
            w._heat_ax.set_xlim(1., 1.09)
            self.assertEqual(percentile.call_count, 0)
        np.testing.assert_equal(w._heat_mesh.get_clim(), (50., 100.))
        self.assertEqual(w.color_clipping_status.text(),
            'Below: 30 (100.00%) · Above: 0 (0.00%) of 30 visible valid cells')

    def test_marker_only_draws_reuse_contrast_and_layout_reuses_it(self):
        w = self.w
        w._drag_peak_arr_idx, w._drag_current_x = 0, 1.2
        w._update_spectrum_only()
        w.canvas.draw()
        self.settle()
        mesh = w._heat_mesh
        with patch.object(mesh, 'draw', wraps=mesh.draw) as draw:
            for value in (1.3, 1.4, 1.5):
                w._on_canvas_motion(SimpleNamespace(inaxes=w._spec_ax, xdata=value))
                w.canvas.draw()
            self.assertEqual(draw.call_count, 3, 'Each figure draw must rasterize the heatmap only once')
        with patch.object(svg_export, 'RendererAgg', wraps=svg_export.RendererAgg) as underlay:
            w._layout_live_plots()
            self.assertEqual(underlay.call_count, 0)
        self.assertAlmostEqual(w._drag_scatter.get_offsets()[0, 0], 1.5)

    def test_cached_contrast_refreshes_for_inplace_data_mask_geometry_and_tick_edits(self):
        # The shared helper's real colored marks must remain correct after cache hits.
        from tests.test_heatmap_tick_contrast import HeatmapTickContrastTests
        helper = HeatmapTickContrastTests()
        figure, canvas, ax = helper.axes()
        mesh = ax.pcolormesh([0., 1., 2.], [0., 1., 2.], np.zeros((2, 2)), cmap='jet', norm=Normalize(0, 1))
        ax.set_xlim(0., 2.); ax.set_ylim(.1, 1.9); ax.set_xticks([.5, 1.5])
        helper.update(ax, canvas)
        with patch.object(mesh, 'draw', wraps=mesh.draw) as draw:
            svg_export._update_heatmap_tick_contrast(ax, canvas.get_renderer())
            self.assertEqual(draw.call_count, 0)
        mesh.get_array()[...] = .65
        helper.update(ax, canvas)
        self.assertEqual(to_hex(ax.xaxis.get_major_ticks()[0].tick1line.get_color()), '#000000')

        mesh.get_array().mask = np.ones((2, 2), dtype=bool)
        helper.update(ax, canvas)
        self.assertEqual(to_hex(ax.xaxis.get_major_ticks()[0].tick1line.get_color()), '#000000')
        mesh.set_array(np.zeros((2, 2))); helper.update(ax, canvas)
        self.assertEqual(to_hex(ax.xaxis.get_major_ticks()[0].tick1line.get_color()), '#ffffff')
        for action in (lambda: figure.set_size_inches(5, 4),
                       lambda: ax.set_position([.1, .1, .8, .8]),
                       lambda: ax.set_xticks([.25, 1.25]),
                       lambda: mesh.get_coordinates().__setitem__((slice(None), slice(None), 0), 10.)):
            action()
            with patch.object(svg_export, 'RendererAgg', wraps=svg_export.RendererAgg) as underlay:
                svg_export._update_heatmap_tick_contrast(ax, canvas.get_renderer())
                self.assertEqual(underlay.call_count, 1)
        self.assertEqual(to_hex(ax.xaxis.get_major_ticks()[0].tick1line.get_color()), '#000000')

    def test_marker_contrast_does_not_copy_contiguous_fortran_data(self):
        w = self.w
        w._heat_mesh.set_array(np.asfortranarray(w._heat_mesh.get_array()))
        w.canvas.draw()
        self.settle()
        original = np.ascontiguousarray
        copies = []
        def observe(values, *args, **kwargs):
            result = original(values, *args, **kwargs)
            if np.asarray(values).nbytes >= 100 and not np.shares_memory(result, values):
                copies.append(result.shape)
            return result
        with patch.object(np, 'ascontiguousarray', side_effect=observe):
            svg_export._update_heatmap_tick_contrast(w._heat_ax, w.canvas.get_renderer())
        self.assertEqual(copies, [], 'Marker-only contrast must hash contiguous buffers without copying them')

    def test_memory_order_change_with_identical_physical_bytes_refreshes_contrast(self):
        from tests.test_heatmap_tick_contrast import HeatmapTickContrastTests
        helper = HeatmapTickContrastTests()
        _figure, canvas, ax = helper.axes()
        mesh = ax.pcolormesh([0., 1., 2.], [0., 1., 2.], [[0., .65], [0., .65]],
                             cmap='jet', norm=Normalize(0, 1))
        ax.set_xlim(0., 2.); ax.set_ylim(.1, 1.9); ax.set_xticks([.5, 1.5])
        helper.update(ax, canvas)
        self.assertEqual([to_hex(t.tick1line.get_color()) for t in ax.xaxis.get_major_ticks()],
                         ['#ffffff', '#000000'])
        # Physical bytes are unchanged, but the logical bright half is now above.
        mesh.set_array(np.array([[0., 0.], [.65, .65]], order='F'))
        helper.update(ax, canvas)
        self.assertEqual([to_hex(t.tick1line.get_color()) for t in ax.xaxis.get_major_ticks()],
                         ['#ffffff', '#ffffff'])
