import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings, wait_for_background_jobs

APP = QApplication.instance() or QApplication([])


class OriginInteractionTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.errors = []
        self.w._show_error = self.errors.append
        self.path = self.folder / 'graph.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)))
        self.w._load_dat_file_direct(self.path)
        self.w.resize(1250, 900)
        self.w.plot_tabs.setCurrentWidget(self.w.graph_documents)
        self.w.show()
        self.w.activateWindow()
        APP.processEvents()
        self.render()

    def tearDown(self):
        wait_for_background_jobs(self.w)
        APP.clipboard().clear()
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def render(self):
        self.w._render_plot_page()
        APP.processEvents()
        page = self.w.plot_page.rendered_page
        self.assertIsNotNone(page, self.w.plot_page.status_label.text())
        return page

    def object_point(self, key):
        preview = self.w.plot_page.preview
        self.assertTrue(hasattr(preview.page, 'object_bounds'), 'Formal renderer must expose visible object geometry')
        left, bottom, width, height = preview.page.object_bounds[key][0]
        return QPoint(round((left+width/2)*preview.width()),
                      round((1-bottom-height/2)*preview.height()))

    def test_shared_labels_remain_editable_in_plot_across_modes_and_dataset_switches(self):
        w, p = self.w, self.w.plot_page
        editors = (w.plot_title_edit, w.y_axis_name_edit, w.y_axis_unit_edit, w.signal_title_combo)
        for _ in range(3):
            for editor in editors:
                p.control_scroll.ensureWidgetVisible(editor)
                APP.processEvents()
                self.assertTrue(editor.isVisible(), 'Existing label editor is hidden while Plot is active')
                self.assertTrue(p.control_scroll.isAncestorOf(editor))
            w.plot_tabs.setCurrentIndex(0)
            self.assertTrue(w._view_sidebar_scroll.isAncestorOf(w.plot_title_edit))
            w.plot_tabs.setCurrentWidget(w.graph_documents)
        w.plot_title_edit.setText('Shared title')
        w.y_axis_name_edit.setText('Field')
        w.y_axis_unit_edit.setText('T')
        w.signal_title_combo.setCurrentText('Custom PL (arb. units)')
        self.assertFalse(w.signal_title_auto_chk.isChecked())
        page = self.render()
        self.assertIn('Shared title', page.svg_bytes.decode())
        self.assertIn('Field (T)', page.svg_bytes.decode())
        owner = w.workspace.active_dataset_id
        other = self.folder / 'other.dat'
        other.write_bytes(self.path.read_bytes())
        w._load_dat_file_direct(other)
        w.plot_title_edit.setText('Other')
        w.activate_dataset(owner)
        self.assertEqual(w.plot_title_edit.text(), 'Shared title')
        self.assertIs(editors[0], w.plot_title_edit)
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)

    def test_doubleclick_title_and_annotation_reveals_and_focuses_existing_editors(self):
        w, p = self.w, self.w.plot_page
        w.plot_title_edit.setText('Editable title')
        self.render()
        w._sidebar_tabs.setCurrentIndex(0)
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=self.object_point('title'))
        APP.processEvents()
        self.assertIs(w._sidebar_tabs.currentWidget(), p.control_scroll)
        self.assertTrue(w.plot_title_edit.hasFocus())
        self.assertTrue(w.plot_title_edit.isVisible())
        p.add_annotation('text', 'Manual note')
        self.render()
        item = w.workspace.active.presentation.annotations[0]
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=self.object_point('annotation:'+item.id))
        APP.processEvents()
        self.assertEqual(p.annotation_text.text(), 'Manual note')
        self.assertTrue(p.annotation_text.hasFocus())
        point = p.annotation_text.mapTo(p.control_scroll.viewport(), p.annotation_text.rect().center())
        self.assertTrue(p.control_scroll.viewport().rect().contains(point))

    def test_doubleclick_displayed_axes_and_regional_bar_uses_zoomed_geometry(self):
        w, p = self.w, self.w.plot_page
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        w.set_plot_presentation(replace(w.workspace.active.presentation, height_in=5.,
            axis=replace(w.workspace.active.presentation.axis, exchange_xy=True, flip_x=True, flip_y=True)))
        self.render()
        p.set_zoom('200%')
        APP.processEvents()
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=self.object_point('horizontal-label'))
        self.assertTrue(w.y_axis_name_edit.hasFocus(), 'Exchanged horizontal label represents the original Y label')
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=self.object_point('vertical-axis'))
        self.assertTrue(p.y_mode_combo.hasFocus())
        left, bottom, width, height = p.rendered_page.colorbar_bounds['region-2']
        point = QPoint(round((left+width/2)*p.preview.width()), round((1-bottom-height/2)*p.preview.height()))
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=point)
        self.assertEqual(p.bar_id_combo.currentText(), 'region-2')
        self.assertTrue(p.tick_values_edit.hasFocus() or p.custom_ticks.hasFocus())
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=self.object_point('signal-label'))
        self.assertTrue(w.signal_title_combo.hasFocus() or w.signal_title_combo.lineEdit().hasFocus())

    def test_plot_axis_range_editors_reuse_source_bounds_and_exchange_mapping(self):
        w, p = self.w, self.w.plot_page
        self.assertTrue(p.control_scroll.isAncestorOf(w.xmin_spin), 'Source bounds must remain accessible in Plot')
        w.set_plot_presentation(replace(w.workspace.active.presentation, axis=replace(
            w.workspace.active.presentation.axis, exchange_xy=True)))
        self.render()
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=self.object_point('horizontal-range'))
        self.assertTrue(w.ymin_spin.hasFocus())
        self.assertTrue(w.ymin_spin.isVisible())
        left, bottom, width, height = p.rendered_page.axes_bounds
        upper = QPoint(round((left+width/2)*p.preview.width()),
                       round((1-bottom-height)*p.preview.height()))
        right = QPoint(round((left+width)*p.preview.width()),
                       round((1-bottom-height/2)*p.preview.height()))
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=right)
        self.assertTrue(w.xmin_spin.hasFocus(), 'Right frame must edit original X after exchange')
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=upper)
        self.assertTrue(w.ymin_spin.hasFocus(), 'Upper frame must edit original Y after exchange')
        w.ymin_spin.setValue(.4)
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertEqual(w.workspace.active.view.y_limits[0], .4)

    def test_stale_hidden_and_clipped_objects_cannot_open_property_editors(self):
        w, p = self.w, self.w.plot_page
        w.plot_title_edit.setText('Editable title')
        self.render()
        self.assertTrue(hasattr(p, 'propertyEditRequested'), 'Doubleclick editing signal is missing')
        events = []
        p.propertyEditRequested.connect(lambda *args: events.append(args))
        title_point = self.object_point('title')
        w._invalidate_plot_page(schedule=False)
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=title_point)
        self.assertEqual(events, [])
        self.render()
        w.plot_tabs.setCurrentIndex(0)
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=title_point)
        self.assertEqual(events, [])
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        w.set_plot_presentation(replace(w.workspace.active.presentation, axis=replace(
            w.workspace.active.presentation.axis, show_x=False, show_y=False),
            colorbar=replace(w.workspace.active.presentation.colorbar, visible=False, show_label=False)))
        page = self.render()
        self.assertNotIn('horizontal-axis', page.object_bounds)
        self.assertNotIn('vertical-axis', page.object_bounds)
        self.assertFalse(any(key.startswith('colorbar:') for key in page.object_bounds))
        w.plot_title_edit.setText('Long clipped title ' * 8)
        w.set_plot_presentation(replace(w.workspace.active.presentation, title_position=(.9,.85)))
        page = self.render()
        left, bottom, width, height = page.axes_bounds
        outside = QPoint(round((left+width)*p.preview.width())+8, p.preview.title_point().y()+5)
        QTest.mouseDClick(p.preview, Qt.LeftButton, pos=outside)
        self.assertEqual(events, [])

    def test_copy_graph_commits_fresh_hybrid_svg_identical_to_export(self):
        w, p = self.w, self.w.plot_page
        self.assertTrue(hasattr(w, 'copy_graph_svg'), 'Formal Copy SVG command is missing')
        before = w.workspace.active.current_cube.z.copy()
        p.aspect_lock.setChecked(False)
        p.width_spin.setValue(4.)
        p.height_spin.setValue(4.)
        p.dpi_spin.lineEdit().setText('150')
        w.plot_title_edit.setText('Fresh clipboard title')
        page = w.copy_graph_svg()
        mime = APP.clipboard().mimeData()
        self.assertEqual(mime.formats(), ['image/svg+xml'])
        self.assertFalse(mime.hasImage())
        self.assertEqual(page.heatmap_dpi, 150)
        self.assertEqual(bytes(mime.data('image/svg+xml')), page.svg_bytes)
        self.assertIn('Fresh clipboard title', bytes(mime.data('image/svg+xml')).decode())
        self.assertEqual(page.svg_bytes.count(b'data:image/png;base64,'), 1)
        self.assertNotIn('editing-handle', page.svg_bytes.decode())
        exported = w.export_plot_page('svg')
        self.assertEqual(bytes(mime.data('image/svg+xml')), exported.read_bytes())
        np.testing.assert_array_equal(w.workspace.active.current_cube.z, before)

    def test_copy_graph_invalid_editor_and_empty_window_preserve_clipboard(self):
        w, p = self.w, self.w.plot_page
        self.assertTrue(hasattr(w, 'copy_graph_svg'), 'Formal Copy SVG command is missing')
        APP.clipboard().setText('Keep this clipboard')
        p.tick_values_edit.setText('bad ticks')
        p.custom_ticks.setChecked(True)
        with self.assertRaises(ValueError):
            w.copy_graph_svg()
        self.assertEqual(APP.clipboard().text(), 'Keep this clipboard')
        empty = MainWindow()
        self.addCleanup(empty.deleteLater)
        self.addCleanup(empty.close)
        with self.assertRaises(ValueError):
            empty.copy_graph_svg()
        self.assertEqual(APP.clipboard().text(), 'Keep this clipboard')

    def test_shared_menus_import_shortcut_and_native_text_copy(self):
        w, p = self.w, self.w.plot_page
        self.assertTrue(hasattr(w, '_graph_command_actions'), 'Shared File/Edit/View commands are missing')
        self.assertEqual([action.text().replace('&','') for action in w.menuBar().actions()], ['File','Edit','View'])
        self.assertEqual(p.copy_graph_btn.text(), 'Copy SVG')
        self.assertEqual(w._graph_command_actions['copy_graph'].text(), 'Copy Graph as SVG')
        extra = self.folder / 'shortcut.dat'
        extra.write_bytes(self.path.read_bytes())
        p.preview.setFocus()
        with patch('spectra_app.main_window.QFileDialog.getOpenFileNames', return_value=([str(extra)], 'DAT Files (*.dat)')):
            QTest.keyClick(p.preview, Qt.Key_O, Qt.ControlModifier)
            wait_for_background_jobs(w)
        self.assertIsNotNone(w.workspace.dataset_for_path(extra))
        p = w.plot_page
        w.plot_title_edit.setText('Native copy text')
        p.control_scroll.ensureWidgetVisible(w.plot_title_edit)
        w.plot_title_edit.setFocus()
        w.plot_title_edit.selectAll()
        QTest.keyClick(w.plot_title_edit, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(APP.clipboard().text(), 'Native copy text')
        self.assertFalse(APP.clipboard().mimeData().hasImage())
        p.preview.setFocus()
        QTest.keyClick(p.preview, Qt.Key_J, Qt.ControlModifier | Qt.AltModifier)
        self.assertEqual(APP.clipboard().mimeData().formats(), ['image/svg+xml'], self.errors)
        self.assertFalse(APP.clipboard().mimeData().hasImage())
        APP.clipboard().setText('Graph shortcut replaces this')
        p.preview.setFocus()
        QTest.keyClick(p.preview, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(APP.clipboard().mimeData().formats(), ['image/svg+xml'], self.errors)
        self.assertFalse(APP.clipboard().mimeData().hasImage())

    def test_rescale_shortcut_is_text_safe_and_escape_cancels_drag_without_edit(self):
        w, p = self.w, self.w.plot_page
        self.assertTrue(hasattr(w, '_graph_command_actions'), 'Graph shortcuts are missing')
        w._heat_ax.set_xlim(1.2,1.5)
        w._sync_formal_plot_view()
        w.plot_title_edit.setFocus()
        QTest.keyClick(w.plot_title_edit, Qt.Key_R, Qt.ControlModifier)
        self.assertEqual(tuple(w._heat_ax.get_xlim()), (1.2,1.5))
        p.preview.setFocus()
        QTest.keyClick(p.preview, Qt.Key_R, Qt.ControlModifier)
        self.assertLess(w._heat_ax.get_xlim()[0], 1.2)
        self.assertGreater(w._heat_ax.get_xlim()[1], 1.5)
        self.render()
        p.add_annotation('text','Canceled move')
        self.render()
        item = w.workspace.active.presentation.annotations[0]
        start = p.preview.annotation_point(item)
        QTest.mousePress(p.preview, Qt.LeftButton, pos=start)
        QTest.mouseMove(p.preview, start+QPoint(25,15), delay=10)
        QTest.keyClick(p.preview, Qt.Key_Escape)
        QTest.mouseRelease(p.preview, Qt.LeftButton, pos=start+QPoint(25,15))
        self.assertEqual(w.workspace.active.presentation.annotations[0], item)
        self.assertIsNone(p.preview.selected_id)

    def test_explicit_rescale_menu_command_runs_while_a_text_editor_keeps_focus(self):
        w = self.w
        w._heat_ax.set_xlim(1.2,1.5)
        w.plot_title_edit.setFocus()
        w._graph_command_actions['rescale'].trigger()
        self.assertLess(w._heat_ax.get_xlim()[0], 1.2)
        self.assertGreater(w._heat_ax.get_xlim()[1], 1.5)

    def test_escape_cancels_analysis_peak_drag_before_mouse_release(self):
        w = self.w
        w.plot_tabs.setCurrentIndex(0)
        w._add_manual_peak(1.3)
        before = w.peak_indices.copy()
        w._on_canvas_click(SimpleNamespace(inaxes=w._spec_ax, xdata=1.3, ydata=1., button=1))
        self.assertIsNotNone(w._drag_peak_arr_idx)
        w._on_canvas_motion(SimpleNamespace(inaxes=w._spec_ax, xdata=1.8))
        w.canvas.setFocus()
        QTest.keyClick(w.canvas, Qt.Key_Escape)
        w._on_canvas_release(SimpleNamespace(inaxes=w._spec_ax, xdata=1.8, ydata=1., button=1))
        np.testing.assert_array_equal(w.peak_indices, before)
        self.assertIsNone(w._motion_cid)
        self.assertIsNone(w._drag_scatter)


if __name__ == '__main__':
    unittest.main()
