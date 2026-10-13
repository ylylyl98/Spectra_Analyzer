"""Complete heatmap color controls follow the active Properties owner."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGroupBox

from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings


APP = QApplication.instance() or QApplication([])


class WorkspaceColorPropertiesTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.callback_errors = []
        capture = patch.object(sys, 'excepthook', lambda *error: self.callback_errors.append(error))
        capture.start()
        self.addCleanup(capture.stop)
        self.w = MainWindow()
        self.errors = []
        self.w._show_error = self.errors.append
        self.addCleanup(self.dispose)
        path = self.folder / 'spectrum.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(15)))
        self.w._load_dat_file_direct(path)
        self.w.open_graph_document()
        self.w.resize(1500, 920)
        self.w.show()
        self.w.activateWindow()
        APP.processEvents()

    def dispose(self):
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.callback_errors, [])

    def assert_reachable(self, scroll, control):
        self.w._sidebar_tabs.setCurrentWidget(scroll)
        self.assertTrue(scroll.isAncestorOf(control),
                        'Color Scale controls must belong to the active View properties')
        scroll.ensureWidgetVisible(control)
        APP.processEvents()
        self.assertTrue(control.isVisible())
        point = control.mapTo(scroll.viewport(), control.rect().center())
        self.assertTrue(scroll.viewport().rect().contains(point),
                        'The existing View scroll must expose the color control')

    def select(self, combo, data):
        self.assert_reachable(self.w.plot_page.control_scroll, combo)
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key_Home)
        for _ in range(combo.findData(data)):
            QTest.keyClick(combo, Qt.Key_Down)
        APP.processEvents()
        self.assertEqual(combo.currentData(), data)

    def render(self):
        self.w._render_plot_page()
        APP.processEvents()
        page = self.w.plot_page.rendered_page
        self.assertIsNotNone(page, self.w.plot_page.status_label.text())
        return page

    def test_workspace_global_control_replaces_two_rendered_bars_without_changing_data(self):
        w = self.w
        session = w.workspace.active
        base, current = session.base_cube.z.copy(), session.current_cube.z.copy()
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        self.assertEqual(len(self.render().colorbar_bounds), 2)
        self.select(w.color_scale_combo, 'global')
        self.assertEqual(len(self.render().colorbar_bounds), 1)
        self.assertEqual(len(w._heat_colorbars), 1)
        self.assertEqual(session.view.color_scale_mode, 'global')
        np.testing.assert_array_equal(session.base_cube.z, base)
        np.testing.assert_array_equal(session.current_cube.z, current)
        for control in (w.cmap_combo, w.mapping_combo, w.linthresh_spin,
                        w.vmin_spin, w.vmin_lock, w.vmax_spin, w.vmax_lock,
                        w.auto_color_btn, w.fix_regions_chk):
            self.assert_reachable(w.plot_page.control_scroll, control)
        self.select(w.color_scale_combo, '3')
        for control in w.region_boundary_spins:
            self.assert_reachable(w.plot_page.control_scroll, control)

    def test_workspace_color_edits_retain_each_graph_owner(self):
        w = self.w
        session = w.workspace.active
        original = session.current_cube.z.copy()
        self.select(w.color_scale_combo, '2')
        graph_a = session.active_graph
        graph_b = w.create_graph('Graph B')
        w.open_graph_document(session.dataset_id, graph_b.graph_id)
        saved_a = deepcopy(graph_a.view)
        self.select(w.color_scale_combo, 'global')
        self.select(w.mapping_combo, 'log')
        self.assert_reachable(w.plot_page.control_scroll, w.cmap_combo)
        w.cmap_combo.setCurrentText('magma')
        self.assert_reachable(w.plot_page.control_scroll, w.vmin_spin)
        w.vmin_spin.lineEdit().selectAll()
        QTest.keyClicks(w.vmin_spin, '2')
        QTest.keyClick(w.vmin_spin, Qt.Key_Return)
        self.assertTrue(w.vmin_lock.isChecked())
        self.assertEqual(len(self.render().colorbar_bounds), 1)
        self.assertEqual(graph_a.view, saved_a)
        w.open_graph_document(session.dataset_id, graph_a.graph_id)
        self.assertEqual(w.color_scale_combo.currentData(), '2')
        self.assertEqual(len(self.render().colorbar_bounds), 2)
        w.open_graph_document(session.dataset_id, graph_b.graph_id)
        self.assertEqual(w.color_scale_combo.currentData(), 'global')
        self.assertEqual(w.mapping_combo.currentData(), 'log')
        self.assertEqual(w.cmap_combo.currentText(), 'magma')
        self.assertEqual(w.vmin_spin.value(), 2.)
        self.assertTrue(w.vmin_lock.isChecked())
        np.testing.assert_array_equal(session.current_cube.z, original)

    def test_same_color_controls_move_between_graph_pages_and_analysis(self):
        w = self.w
        session = w.workspace.active
        graph_a = session.active_graph
        page_a = w.plot_page
        controls = (w.color_scale_combo, w.cmap_combo, w.mapping_combo, w.auto_color_btn)
        graph_b = w.create_graph('Graph B')
        page_b = w.open_graph_document(session.dataset_id, graph_b.graph_id)
        self.assertIsNot(page_a, page_b)
        for _ in range(2):
            for graph, page, inactive in ((graph_a, page_a, page_b), (graph_b, page_b, page_a)):
                w.open_graph_document(session.dataset_id, graph.graph_id)
                for control in controls:
                    self.assert_reachable(page.control_scroll, control)
                    self.assertFalse(inactive.control_scroll.isAncestorOf(control))
                w.plot_tabs.setCurrentIndex(0)
                for control in controls:
                    self.assert_reachable(w._view_sidebar_scroll, control)
                    self.assertFalse(page.control_scroll.isAncestorOf(control))
                w.plot_tabs.setCurrentWidget(w.graph_documents)
                self.assert_reachable(page.control_scroll, w.color_scale_combo)
                groups = [group for group in w.findChildren(QGroupBox) if group.title() == 'Color Scale']
                self.assertEqual(len(groups), 1, 'Switches must retain a single Color Scale group')
        self.assertEqual(controls, (w.color_scale_combo, w.cmap_combo, w.mapping_combo, w.auto_color_btn))

    def test_colorbar_doubleclick_reveals_complete_properties_and_keeps_tick_focus(self):
        w = self.w
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        rendered = self.render()
        page = w.plot_page
        left, bottom, width, height = rendered.colorbar_bounds['region-2']
        w.dat_details_action.setChecked(False)
        w._sidebar_tabs.setCurrentIndex(0)
        APP.processEvents()
        # Hiding Properties can resize the preview; use its final dimensions.
        point = QPoint(round((left+width/2)*page.preview.width()),
                       round((1-bottom-height/2)*page.preview.height()))
        QTest.mouseDClick(page.preview, Qt.LeftButton, pos=point)
        APP.processEvents()
        self.assertTrue(w.dat_details_dock.isVisible())
        self.assertIs(w._sidebar_tabs.currentWidget(), page.control_scroll)
        self.assertEqual(page.bar_id_combo.currentText(), 'region-2')
        self.assertTrue(page.tick_values_edit.hasFocus() or page.custom_ticks.hasFocus())
        self.assert_reachable(page.control_scroll, w.color_scale_combo)
        self.assert_reachable(page.control_scroll, w.mapping_combo)
        self.assert_reachable(page.control_scroll, page.bar_format_combo)
        self.assert_reachable(page.control_scroll, page.custom_ticks)


if __name__ == '__main__':
    unittest.main()
