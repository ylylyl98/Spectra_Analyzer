"""Ordinary peak actions through real Quick input and the canonical DAT state."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from tests.quick_migration_case import QuickMigrationCase
from tests import analysis_canvas_quick_helpers as ac
from tests.formal_quick_helpers import item
from tests.settings_helpers import wait_for_background_jobs

APP = QCoreApplication


class PeakMenuQuickTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.path = self.folder/'quick-peaks.dat'
        self.path.write_text('Energy(eV) 0 3 10\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)))
        self.w = self.controller
        self.w._show_error = self.fail
        self.w._load_dat_file_direct(self.path)
        self.w.root_window.resize(1300, 950)
        self.w.plot_page.set_zoom('100%')
        ac.reveal(self.w)
        self.seed_peaks()

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

    @property
    def bridge(self):
        return self.w.dat_canvas

    def point(self, energy=1.5):
        return ac.data_point(self.w, self.w._spec_ax,
                             (energy, float(np.mean(self.w._spec_ax.get_ylim()))))

    def open_menu(self):
        QTest.mouseClick(self.w.root_window, Qt.RightButton, pos=self.point())
        ac.pump(70)
        self.assertTrue(ac.peak_menu(self.w).property('visible'))
        self.assertTrue(self.bridge.peakMenu)
        return dict(self.bridge.peakMenu)

    def assert_rejected(self, request):
        before = len(self.w._history_for(self.w.workspace.active).undo_stack)
        with patch('spectra_app.main_window.prompt_int') as prompt:
            self.assertFalse(self.bridge.choosePeakMenu(request['requestId'], 'set_label', request['owner']))
        prompt.assert_not_called()
        self.assertEqual(len(self.w._history_for(self.w.workspace.active).undo_stack), before)

    def test_actual_right_release_menu_dismiss_and_canvas_focus(self):
        w = self.w; host = item(w, 'applicationDatCanvas'); point = self.point()
        owner = self.bridge.ownerToken
        ac.press(w, point, button=Qt.RightButton)
        self.assertFalse(ac.peak_menu(w).property('visible'))
        self.assertFalse(self.bridge.peakMenu)
        self.assertIsNotNone(self.bridge._peak_menu_request)
        ac.release(w, point, button=Qt.RightButton); ac.pump(70)
        menu = ac.peak_menu(w)
        self.assertTrue(menu.property('visible'))
        self.assertEqual(menu.property('count'), 3)
        self.assertIn('8', item(host, 'analysisPeakSetLabel').property('text'))
        self.assertEqual(item(host, 'analysisPeakDelete').property('text'), 'Delete Peak')
        self.assertTrue(ac.peak_menu(w).property('visible'))
        self.assertIsNotNone(QGuiApplication.focusWindow())
        self.assertEqual(w.findChildren(QWidget), [])
        self.assertEqual(self.bridge.ownerToken, owner)
        self.assertFalse(host.property('gestureActive'))
        self.assertIsNone(self.bridge._gesture_owner)
        self.assertEqual(self.bridge._held_buttons, set())
        self.assertIsNone(w.canvas._button)
        popup_keys = []
        ids = [w.canvas.mpl_connect(name, lambda event: popup_keys.append((event.name, event.key)))
               for name in ('key_press_event', 'key_release_event')]
        try:
            for dismiss in ('escape', 'outside'):
                if dismiss == 'outside':
                    self.open_menu()
                    QTest.mouseClick(w.root_window, Qt.LeftButton, pos=host.mapToScene(QPointF(5, 5)).toPoint())
                else:
                    QTest.keyClick(self.w.root_window, Qt.Key_Escape)
                ac.pump(70)
                self.assertFalse(menu.property('visible'))
                self.assertFalse(self.bridge.peakMenu)
                self.assertTrue(host.hasActiveFocus())
                self.assertIs(QGuiApplication.focusWindow(), w.root_window)
        finally:
            for cid in ids:
                w.canvas.mpl_disconnect(cid)
        self.assertEqual(popup_keys, [], 'Neither Escape half may reach Matplotlib')
        seen = []
        cid = w.canvas.mpl_connect('key_press_event', lambda event: seen.append(event.key))
        try:
            QTest.keyClick(self.w.root_window, Qt.Key_A); ac.pump(30)
        finally:
            w.canvas.mpl_disconnect(cid)
        self.assertIn('a', seen)
        self.assertFalse(w.undo_edit(), 'Dismissal must not enter scientific history')

    def test_logical_anchor_with_scroll_dpr_and_edge_clamp(self):
        w = self.w; host = item(w, 'applicationDatCanvas')
        w.plot_page.set_zoom('200%'); ac.pump(120); w._layout_live_plots(); w.canvas.draw()
        viewport = item(host, 'analysisCanvasViewport')
        image = item(host, 'analysisCanvasImage')
        for dpr in (1., 2.):
            with self.subTest(dpr=dpr):
                self.bridge.set_device_pixel_ratio(dpr); w._layout_live_plots(); w.canvas.draw()
                point = self.point()
                # Select nonzero offsets while keeping the peak inside the real viewport.
                logical = image.mapFromScene(QPointF(point))
                viewport.setProperty('contentX', max(20., logical.x()-viewport.width()/2))
                viewport.setProperty('contentY', max(20., logical.y()-viewport.height()/2))
                ac.pump(30)
                point = self.point()
                self.assertGreater(viewport.property('contentX'), 0)
                self.assertGreater(viewport.property('contentY'), 0)
                seen = []
                cid = w.canvas.mpl_connect('button_press_event', lambda event: seen.append(event))
                try:
                    QTest.mouseClick(w.root_window, Qt.RightButton, pos=point); ac.pump(70)
                finally:
                    w.canvas.mpl_disconnect(cid)
                self.assertTrue(ac.peak_menu(w).property('visible'))
                request = self.bridge.peakMenu
                anchor = host.mapFromScene(QPointF(point))
                self.assertAlmostEqual(request['anchorX'], anchor.x(), delta=1.)
                self.assertAlmostEqual(request['anchorY'], anchor.y(), delta=1.)
                self.assertIs(seen[-1].inaxes, w._spec_ax)
                self.assertAlmostEqual(seen[-1].xdata, 1.5, delta=.008)
                content = ac.peak_menu(w).property('contentItem')
                origin = host.mapFromScene(content.mapToScene(QPointF(0, 0)))
                self.assertAlmostEqual(origin.x(), anchor.x()+ac.peak_menu(w).property('leftPadding'), delta=2.)
                QTest.keyClick(self.w.root_window, Qt.Key_Escape); ac.pump(40)
        # Place the same scientific target near the lower-right viewport edge.
        logical = image.mapFromScene(QPointF(self.point()))
        viewport.setProperty('contentX', logical.x()-viewport.width()+12)
        viewport.setProperty('contentY', logical.y()-viewport.height()+12)
        ac.pump(30)
        point = self.point()
        QTest.mouseClick(w.root_window, Qt.RightButton, pos=point); ac.pump(70)
        menu = ac.peak_menu(w); self.assertTrue(menu.property('visible'))
        content = menu.property('contentItem'); origin = host.mapFromScene(content.mapToScene(QPointF(0, 0)))
        self.assertGreaterEqual(origin.x(), 0); self.assertGreaterEqual(origin.y(), 0)
        self.assertLessEqual(origin.x()+content.width(), host.width()+1)
        self.assertLessEqual(origin.y()+content.height(), host.height()+1)
        self.assertLess(origin.x(), host.mapFromScene(QPointF(point)).x(), 'A right-edge popup must clamp left')

    def test_miss_empty_mode_import_and_heatmap_keep_their_routes(self):
        w = self.w; host = item(w, 'applicationDatCanvas')
        QTest.mouseClick(w.root_window, Qt.RightButton, pos=self.point(1.95)); ac.pump(40)
        self.assertFalse(self.bridge.peakMenu)
        w.toolbar.pan()
        QTest.mouseClick(w.root_window, Qt.RightButton, pos=self.point()); ac.pump(40)
        self.assertFalse(self.bridge.peakMenu)
        w.toolbar.pan()
        w._set_dat_import_pending(True)
        try:
            QTest.mouseClick(w.root_window, Qt.RightButton, pos=self.point()); ac.pump(40)
            self.assertFalse(self.bridge.peakMenu)
        finally:
            w._set_dat_import_pending(False)
        ac.click_data(w, w._heat_ax, (1.5, .05), button=Qt.RightButton)
        np.testing.assert_array_equal(w.peak_indices, [2])
        self.assertFalse(self.bridge.peakMenu)
        self.assertTrue(w.undo_edit())
        w.peak_indices = np.array([], dtype=int); w.batch_peak_map.clear()
        QTest.mouseClick(w.root_window, Qt.RightButton, pos=self.point()); ac.pump(40)
        self.assertFalse(self.bridge.peakMenu)
        self.assertFalse(ac.peak_menu(w).property('visible'))

    def test_same_gate_noop_new_request_invalid_choice_and_replay(self):
        first = self.open_menu()
        self.w._set_current_gate_from_index(0)
        self.assertEqual(self.bridge.peakMenu['requestId'], first['requestId'])
        self.assertFalse(self.bridge.choosePeakMenu(first['requestId'], '__getattribute__', first['owner']))
        self.assertTrue(self.bridge.peakMenu)
        QTest.keyClick(self.w.root_window, Qt.Key_Escape); ac.pump(40)
        second = self.open_menu()
        self.assertNotEqual(first['requestId'], second['requestId'])
        self.assert_rejected(first)
        keys = []
        ids = [self.w.canvas.mpl_connect(name, lambda event: keys.append((event.name, event.key)))
               for name in ('key_press_event', 'key_release_event')]
        try:
            host = item(self.w, 'applicationDatCanvas')
            QTest.keyClick(self.w.root_window, Qt.Key_Down)
            QTest.keyClick(self.w.root_window, Qt.Key_Down)
            self.assertEqual(ac.peak_menu(self.w).property('currentIndex'), 2)
            QTest.keyClick(self.w.root_window, Qt.Key_Return); ac.pump(70)
        finally:
            for cid in ids:
                self.w.canvas.mpl_disconnect(cid)
        self.assertEqual(keys, [], 'Menu keys must not reach Matplotlib')
        np.testing.assert_array_equal(self.w.peak_indices, [2])
        self.assert_rejected(second)
        self.assertTrue(self.w.undo_edit())
        np.testing.assert_array_equal(self.w.peak_indices, [2, 5])

    def test_owner_gate_target_and_hidden_roundtrips_retire_requests(self):
        w = self.w; session = w.workspace.active
        other = self.folder/'other.dat'; other.write_bytes(self.path.read_bytes())
        for change in ('gate', 'target', 'source', 'graph', 'hide'):
            with self.subTest(change=change):
                request = self.open_menu()
                if change == 'gate':
                    w._set_current_gate_from_index(1); w._set_current_gate_from_index(0)
                elif change == 'target':
                    w.peak_indices = np.array([2, 5]); w._refresh_analysis_context()
                elif change == 'source':
                    w._load_dat_file_direct(other); w.activate_dataset(session.dataset_id); ac.reveal(w)
                elif change == 'graph':
                    graph_id = session.active_graph_id
                    w.create_graph('Peak request retirement')
                    w.activate_graph(graph_id)
                    ac.reveal(w)
                else:
                    item(w, 'applicationDatCanvas').setVisible(False); ac.pump(20); item(w, 'applicationDatCanvas').setVisible(True)
                ac.pump(80)
                self.assert_rejected(request)
                self.assertFalse(ac.peak_menu(w).property('visible'))
                np.testing.assert_array_equal(w.peak_indices, [2, 5])
                self.assertEqual(w.peak_label_lookup[(0, 5)], 8)
                self.assertFalse(w.undo_edit())
        # Owner comparison must reject a replacement immediately, before refresh.
        request = self.open_menu()
        w.current_cube = replace(w.current_cube, z=w.current_cube.z.copy())
        self.assert_rejected(request)

    def test_label_modal_gate_and_target_retirement_prevents_late_edit(self):
        w = self.w
        for change in ('gate', 'target', 'hide'):
            with self.subTest(change=change):
                self.open_menu()
                def retire(dialog):
                    if change == 'gate':
                        w._set_current_gate_from_index(1); w._set_current_gate_from_index(0)
                    elif change == 'target':
                        w.peak_indices = np.array([2, 5]); w._refresh_analysis_context()
                    else:
                        item(w, 'applicationDatCanvas').setVisible(False); ac.pump(20); item(w, 'applicationDatCanvas').setVisible(True)
                ac.choose_peak_label(w, 33, before_accept=retire)
                self.assertEqual(w.peak_label_lookup[(0, 5)], 8)
                self.assertFalse(w.undo_edit(), 'Retired modal continuation must not record a label')

    def test_named_workbench_and_xy_do_not_accept_ordinary_peak_requests(self):
        from spectra_app.workbook_state import create_comparison
        w = self.w; session = w.workspace.active
        graph_id = session.active_graph_id
        request = self.open_menu()
        w._workbench_open(('heatmap', (session.dataset_id, graph_id)))
        ac.pump(80)
        self.assertIsNotNone(w._workbench_document())
        self.assertFalse(self.bridge.enabled)
        self.assertFalse(ac.peak_menu(w).property('visible'))
        self.assert_rejected(request)
        w.open_graph_document(session.dataset_id, graph_id)
        ac.reveal(w)
        request = self.open_menu()
        graph = create_comparison(w.workspace, 'Peak routing XY')
        page = w.open_xy_comparison(graph.comparison_id)
        ac.pump(80)
        self.assertFalse(self.bridge.enabled)
        self.assertFalse(ac.peak_menu(w).property('visible'))
        self.assert_rejected(request)
        self.assertIsNot(page.canvas, w.canvas)
        self.assertFalse(w.undo_edit())


if __name__ == '__main__':
    unittest.main()
