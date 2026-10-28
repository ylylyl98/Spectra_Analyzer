"""Production ordinary DAT science through the real Quick canvas scene."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QWidget
from PySide6.QtQuick import QQuickItem
from tests.formal_quick_helpers import item
from matplotlib.backends.backend_agg import FigureCanvasAgg

from tests.quick_migration_case import QuickMigrationCase
from tests import analysis_canvas_quick_helpers as ac
from tests import ordinary_analysis_quick_helpers as oa
from tests.settings_helpers import wait_for_background_jobs
from tests.scientific_fixtures import workflow_fit_result as _fit_result

APP = QCoreApplication


class AnalysisCanvasQuickIntegrationTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.path = self.folder / 'quick-science.dat'
        self.path.write_text('Energy(eV) 0 3 10\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)))
        self.w = self.controller
        self.w._show_error = self.fail
        self.w._load_dat_file_direct(self.path)
        self.w.root_window.resize(1300, 950)
        self.w.plot_page.set_zoom('100%')
        ac.reveal(self.w)
        self.assertIsInstance(self.w.canvas, FigureCanvasAgg)
        self.assertNotIsInstance(self.w.canvas, QWidget)
        self.assertIsInstance(item(self.w, 'applicationDatCanvas'), QQuickItem)
        self.assertTrue(self.w.dat_canvas.enabled)

    def tearDown(self):
        wait_for_background_jobs(self.w)
        self.assertEqual(self.quick_errors, [])

    def seed_peaks(self):
        w = self.w
        w._set_current_gate_from_index(0)
        w.peak_indices = np.array([2, 5])
        w._peak_gate_idx = 0
        w.peak_kind_lookup = {2: True, 5: False}
        w.peak_label_lookup = {(0, 2): 7, (0, 5): 8, (1, 3): 9}
        w.batch_peak_map = {0: np.array([2, 5]), 1: np.array([3])}
        w.batch_peak_kind_lookup = {(0, 2): True, (0, 5): False, (1, 3): True}
        w._sync_batch_peak_coords(); w._update_spectrum_only(); w.canvas.draw()

    def spectrum_y(self):
        return float(np.mean(self.w._spec_ax.get_ylim()))

    def test_gate_uses_nearest_physical_value_through_log_inverted_axes(self):
        w = self.w; cube = w.current_cube
        for ratio in (1., 2.):
            w.dat_canvas.set_device_pixel_ratio(ratio)
            w._heat_ax.set_xscale('log'); w._heat_ax.set_xlim(2.1, 1.)
            w._heat_ax.set_ylim(10., 0.); w._layout_live_plots(); w.canvas.draw()
            self.assertEqual(w._heat_ax.get_xscale(), 'log')
            self.assertTrue(w._heat_ax.xaxis_inverted()); self.assertTrue(w._heat_ax.yaxis_inverted())
            history = list(w._history_for(w.workspace.active).undo_stack)
            seen = []
            cid = w.canvas.mpl_connect('button_press_event', lambda event: seen.append(event))
            try:
                ac.click_data(w, w._heat_ax, (1.5, 6.))
            finally:
                w.canvas.mpl_disconnect(cid)
            self.assertEqual(w.current_gate, 3.)
            self.assertEqual(w._heat_ax.get_xscale(), 'log')
            # Existing spectrum refresh reapplies canonical ordered shared X.
            # The event above must still invert the pre-refresh image transform.
            self.assertFalse(w._heat_ax.xaxis_inverted())
            self.assertTrue(w._heat_ax.yaxis_inverted())
            np.testing.assert_allclose(w._heat_ax.get_xlim(), (1., 2.1))
            self.assertIs(w.current_cube, cube)
            self.assertIs(seen[-1].inaxes, w._heat_ax)
            self.assertAlmostEqual(seen[-1].xdata, 1.5, delta=.02)
            self.assertAlmostEqual(seen[-1].ydata, 6., delta=.12)
            self.assertEqual(w._history_for(w.workspace.active).undo_stack, history,
                             'Gate selection is not a scientific edit')

    def test_peak_add_snapped_drag_history_and_same_owner_refresh(self):
        w = self.w; cube = w.current_cube; original = cube.z.copy()
        self.seed_peaks()
        ac.click_data(w, w._spec_ax, (1.803, self.spectrum_y()))
        np.testing.assert_array_equal(w.peak_indices, [2, 5, 8])
        self.assertTrue(w.undo_edit()); np.testing.assert_array_equal(w.peak_indices, [2, 5])
        self.assertTrue(w.redo_edit())
        start = ac.data_point(w, w._spec_ax, (1.5, self.spectrum_y()))
        end = ac.data_point(w, w._spec_ax, (1.642, self.spectrum_y()), ensure_visible=False)
        ac.press(w, start)
        self.assertEqual(w._drag_peak_arr_idx, 1)
        owner = w.dat_canvas.ownerToken
        w.analysis_navigation.refresh(); w.ordinary_analysis.refresh()
        self.assertEqual(w.dat_canvas.ownerToken, owner)
        ac.move(w, end); ac.release(w, end); ac.pump(80)
        np.testing.assert_array_equal(w.peak_indices, [2, 6, 8])
        self.assertFalse(w.peak_kind_lookup[6]); self.assertEqual(w.peak_label_lookup[(0, 6)], 8)
        self.assertTrue(w.undo_edit()); self.assertEqual(w.peak_label_lookup[(0, 5)], 8)
        self.assertTrue(w.redo_edit()); self.assertEqual(w.peak_label_lookup[(0, 6)], 8)
        ac.drag_data(w, w._spec_ax, (1.6, self.spectrum_y()), (1.604, self.spectrum_y()))
        self.assertTrue(w.undo_edit()); np.testing.assert_array_equal(w.peak_indices, [2, 5, 8])
        self.assertTrue(w.redo_edit())
        # The canonical graph QActions now belong to the real Quick host.
        host = item(w, 'applicationDatCanvas')
        host.forceActiveFocus(Qt.OtherFocusReason)
        self.assertTrue(host.hasActiveFocus())
        from PySide6.QtTest import QTest
        QTest.keyClick(w.root_window, Qt.Key_Z, Qt.ControlModifier); ac.pump(80)
        np.testing.assert_array_equal(w.peak_indices, [2, 5, 8])
        self.assertTrue(host.hasActiveFocus(), 'Undo must retain the actual canvas keyboard owner')
        QTest.keyClick(w.root_window, Qt.Key_Y, Qt.ControlModifier); ac.pump(80)
        np.testing.assert_array_equal(w.peak_indices, [2, 6, 8])
        w._heat_ax.set_xlim(1.2, 1.8)
        QTest.keyClick(w.root_window, Qt.Key_R, Qt.ControlModifier); ac.pump(80)
        np.testing.assert_allclose(w._heat_ax.get_xlim(), (1., 2.1))
        np.testing.assert_array_equal(w.peak_indices, [2, 6, 8])
        self.assertIs(w.current_cube, cube); np.testing.assert_array_equal(cube.z, original)

    def test_lasso_and_normalized_circle_keep_unrelated_fit_and_undo(self):
        w = self.w; self.seed_peaks()
        fit = _fit_result(1.5); w.fit_result = fit; w._fit_gate_idx = 0
        w.batch_fit_result_map[0] = fit
        oa.set_mode(w, 'lasso', True)
        self.assertIsNotNone(w._lasso_selector)
        ac.path_data(w, w._heat_ax, [(1.24, 2.), (1.36, 2.), (1.36, 4.),
                                    (1.24, 4.), (1.24, 2.)])
        self.assertEqual(w._lasso_selected_peak_ids, [(1, 3)])
        w._on_delete_lasso_selection()
        self.assertNotIn(1, w.batch_peak_map)
        self.assertIs(w.fit_result, fit); self.assertIs(w.batch_fit_result_map[0], fit)
        self.assertTrue(w.undo_edit()); np.testing.assert_array_equal(w.batch_peak_map[1], [3])
        oa.set_mode(w, 'lasso', False); oa.set_mode(w, 'circle', True)
        ac.drag_data(w, w._heat_ax, (1.3, 3.), (1.34, 3.3))
        self.assertNotIn(1, w.batch_peak_map)
        self.assertIs(w.fit_result, fit); self.assertIs(w.batch_fit_result_map[0], fit)
        self.assertTrue(w.undo_edit()); np.testing.assert_array_equal(w.batch_peak_map[1], [3])
        self.assertIsNone(w._batch_delete_circle_start)

    def test_navigation_zoom_pan_guard_fit_gate_rubberband_and_history(self):
        w = self.w; self.seed_peaks()
        fit = _fit_result(1.5); w.fit_result = fit; w._fit_gate_idx = 0
        gate = w.current_gate; old = w._heat_ax.get_xlim()
        nav = w.analysis_navigation
        self.assertTrue(nav.invoke('zoom', nav.ownerToken))
        start = ac.data_point(w, w._heat_ax, (1.2, 2.))
        end = ac.data_point(w, w._heat_ax, (1.8, 8.), ensure_visible=False)
        ac.press(w, start); ac.move(w, end)
        self.assertTrue(w.dat_canvas.rubberband['visible'])
        ac.release(w, end); ac.pump(80)
        self.assertFalse(w.dat_canvas.rubberband['visible'])
        zoomed = w._heat_ax.get_xlim()
        np.testing.assert_allclose(zoomed, [1.2, 1.8], atol=.02)
        self.assertTrue(nav.backEnabled)
        self.assertEqual(w.current_gate, gate); self.assertIs(w.fit_result, fit)
        np.testing.assert_array_equal(w.peak_indices, [2, 5])
        self.assertTrue(nav.invoke('back', nav.ownerToken)); np.testing.assert_allclose(w._heat_ax.get_xlim(), old)
        self.assertTrue(nav.invoke('forward', nav.ownerToken)); np.testing.assert_allclose(w._heat_ax.get_xlim(), zoomed)
        self.assertTrue(nav.invoke('pan', nav.ownerToken))
        ac.drag_data(w, w._heat_ax, (1.4, 5.), (1.5, 5.5))
        self.assertNotEqual(w._heat_ax.get_xlim(), zoomed)
        self.assertEqual(w.current_gate, gate); self.assertIs(w.fit_result, fit)
        np.testing.assert_array_equal(w.peak_indices, [2, 5])
        self.assertTrue(nav.invoke('pan', nav.ownerToken))
        self.assertTrue(w.undo_edit(), 'A real navigation gesture must enter scientific range history')

    def test_peak_drag_is_cancelled_by_source_graph_roundtrip_and_hide(self):
        w = self.w; self.seed_peaks(); dataset = w.workspace.active.dataset_id
        other_path = self.folder/'replacement.dat'; other_path.write_bytes(self.path.read_bytes())
        for change in ('source', 'graph', 'hide'):
            with self.subTest(change=change):
                start = ac.data_point(w, w._spec_ax, (1.5, self.spectrum_y()))
                end = ac.data_point(w, w._spec_ax, (1.7, self.spectrum_y()), ensure_visible=False)
                ac.press(w, start); self.assertEqual(w._drag_peak_arr_idx, 1)
                ac.move(w, start+(end-start)/2)
                self.assertIsNotNone(w._drag_scatter)
                self.assertIn('Dragging', [artist.get_label() for artist in w._spec_ax.collections])
                old_owner = w.dat_canvas.ownerToken
                if change == 'source':
                    w._load_dat_file_direct(other_path); w.activate_dataset(dataset); ac.reveal(w)
                elif change == 'graph':
                    w.open_graph_document(); self.assertTrue(w.request_outer_view('analysis'))
                else:
                    item(w, 'applicationDatCanvas').setVisible(False); ac.pump(30); item(w, 'applicationDatCanvas').setVisible(True)
                ac.pump(80)
                self.assertNotEqual(w.dat_canvas.ownerToken, old_owner)
                self.assertIsNone(w._drag_peak_arr_idx)
                self.assertIsNone(w._drag_scatter)
                self.assertNotIn('Dragging', [artist.get_label() for artist in w._spec_ax.collections])
                legend = w._spec_ax.get_legend()
                if legend is not None:
                    self.assertNotIn('Dragging', [text.get_text() for text in legend.get_texts()])
                ac.move(w, end); ac.release(w, end); ac.pump(50)
                np.testing.assert_array_equal(w.peak_indices, [2, 5])
                self.assertFalse(w.undo_edit(), 'A retired drag must not commit an edit')
        nav = w.analysis_navigation
        self.assertTrue(nav.invoke('pan', nav.ownerToken))
        start = ac.data_point(w, w._heat_ax, (1.4, 5.))
        end = ac.data_point(w, w._heat_ax, (1.5, 5.5), ensure_visible=False)
        ac.press(w, start); ac.move(w, end)
        self.assertIsNotNone(w._pending_history_edit)
        moved_limits = w._heat_ax.get_xlim()
        item(w, 'applicationDatCanvas').setVisible(False); ac.pump(30); item(w, 'applicationDatCanvas').setVisible(True); ac.pump(80)
        self.assertIsNone(w.toolbar._pan_info)
        self.assertIsNone(w._pending_history_edit)
        ac.release(w, end); ac.pump(30)
        self.assertEqual(w._heat_ax.get_xlim(), moved_limits)
        self.assertTrue(nav.invoke('pan', nav.ownerToken))
        self.assertTrue(w.undo_edit(), 'Applied Pan range belongs to its original session')
        self.assertFalse(w.undo_edit(), 'Cancelled release must not add a second range command')

    def test_real_drag_burst_coalesces_agg_work_without_losing_sample(self):
        w = self.w; self.seed_peaks(); ac.pump(160)
        start = ac.data_point(w, w._spec_ax, (1.5, self.spectrum_y()))
        end = ac.data_point(w, w._spec_ax, (1.71, self.spectrum_y()), ensure_visible=False)
        ac.press(w, start)
        with patch.object(w.canvas, 'draw', wraps=w.canvas.draw) as draw:
            for index in range(40):
                ac.move(w, start+(end-start)*(index+1)/40)
            self.assertEqual(draw.call_count, 0, 'Mouse moves must schedule Agg work')
            self.assertTrue(w.canvas._render_timer.isActive())
            ac.release(w, end); ac.pump(180)
            self.assertGreater(draw.call_count, 0)
            self.assertLessEqual(draw.call_count, 3, 'Forty held moves must coalesce')
            print(f'Stage16 real input burst: 40 held moves; {draw.call_count} Agg draws after settle', flush=True)
        np.testing.assert_array_equal(w.peak_indices, [2, 7])
        self.assertTrue(w.undo_edit()); np.testing.assert_array_equal(w.peak_indices, [2, 5])


if __name__ == '__main__':
    unittest.main()
