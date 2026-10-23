"""Sidebar navigation preserves drafts and reveals the editor a user requests."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
from copy import deepcopy
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from spectra_app.plot_page import PlotPage
from spectra_app.plot_presentation import PlotAnnotation
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings


APP = QApplication.instance() or QApplication([])


class SidebarDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.page = PlotPage()
        self.page.resize(1000, 850)
        self.page.show()
        self.page.activateWindow()
        APP.processEvents()
        self.changes = []
        self.page.presentationChanged.connect(self.changes.append)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def header(self, title):
        buttons = [button for button in self.page.control_scroll.findChildren(QToolButton)
                   if button.text() == title]
        self.assertEqual(len(buttons), 1, f'{title} must be an accessible disclosure')
        return buttons[0]

    def reach(self, editor):
        self.page.control_scroll.ensureWidgetVisible(editor)
        APP.processEvents()
        editor.setFocus()

    def test_common_controls_visible_and_advanced_controls_collapsed(self):
        p = self.page
        for editor in (p.width_spin, p.height_spin, p.numeric_spin, p.text_spin,
                       p.colorbar_visible, p.bar_format_combo, p.bar_precision_spin):
            self.assertTrue(editor.isVisible())
        for title, editor in (('Font and export', p.dpi_spin),
                              ('Frame and title layout', p.title_x_spin),
                              ('Axes and ticks', p.x_mode_combo),
                              ('Colorbar layout', p.bar_width_spin),
                              ('Custom ticks', p.tick_values_edit),
                              ('Text and arrows', p.annotation_text)):
            self.assertFalse(self.header(title).isChecked())
            self.assertFalse(editor.isVisible())

    def test_mouse_collapse_preserves_valid_and_invalid_numeric_drafts(self):
        p = self.page
        header = self.header('Frame and title layout')
        for draft in ('0.12345678', '-'):
            with self.subTest(draft=draft):
                header.setChecked(True)
                self.reach(p.title_x_spin)
                p.title_x_spin.lineEdit().selectAll()
                QTest.keyClicks(p.title_x_spin, draft)
                old = p.title_x_spin.value(), p.presentation
                self.page.control_scroll.ensureWidgetVisible(header)
                QTest.mouseClick(header, Qt.LeftButton)
                APP.processEvents()
                self.assertEqual((p.title_x_spin.value(), p.presentation), old)
                self.assertEqual(p.title_x_spin.lineEdit().text(), draft)
                self.assertTrue(header.hasFocus())
                QTest.keyClick(header, Qt.Key_Space)
                APP.processEvents()
                self.assertTrue(p.title_x_spin.isVisible())
                self.assertEqual(p.title_x_spin.lineEdit().text(), draft)
                self.assertEqual(self.changes, [])

    def test_header_keyboard_navigation_preserves_other_section_draft(self):
        p = self.page
        self.reach(p.width_spin)
        p.width_spin.lineEdit().selectAll()
        QTest.keyClicks(p.width_spin, '7.12345678')
        before = p.presentation
        header = self.header('Font and export')
        header.setFocus(Qt.TabFocusReason)
        QTest.keyClick(header, Qt.Key_Return)
        self.assertTrue(header.isChecked())
        QTest.keyClick(header, Qt.Key_Left)
        self.assertFalse(header.isChecked())
        QTest.keyClick(header, Qt.Key_Right)
        self.assertTrue(header.isChecked())
        self.assertEqual(p.width_spin.lineEdit().text(), '7.12345678')
        self.assertEqual(p.presentation, before)
        self.assertEqual(self.changes, [])

    def test_hidden_drafts_are_committed_or_revealed_on_validation_error(self):
        p = self.page
        header = self.header('Frame and title layout')
        header.setChecked(True)
        self.reach(p.title_x_spin)
        p.title_x_spin.lineEdit().setText('0.12345678')
        header.setChecked(False)
        p.finish_editor()
        self.assertEqual(p.presentation.title_position[0], .12345678)
        header.setChecked(True)
        self.reach(p.title_x_spin)
        p.title_x_spin.lineEdit().setText('-')
        header.setChecked(False)
        with self.assertRaisesRegex(ValueError, 'invalid numeric'):
            p.finish_editor()
        self.assertTrue(header.isChecked())
        self.assertTrue(p.title_x_spin.isVisible())
        self.assertEqual(p.title_x_spin.lineEdit().text(), '-')
        self.assertTrue(p.title_x_spin.hasFocus())

    def test_property_and_annotation_selection_reveal_existing_editor(self):
        p = self.page
        editor = p.property_editor('horizontal-axis')
        self.assertIs(editor, p.x_mode_combo)
        self.assertTrue(editor.isVisible())
        editor = p.property_editor('colorbar', 'global')
        self.assertIs(editor, p.custom_ticks)
        self.assertTrue(editor.isVisible())
        p.set_presentation(replace(p.presentation, annotations=(PlotAnnotation('note', text='Sample'),)))
        self.assertFalse(p.annotation_text.isVisible())
        p.select_annotation('note')
        self.assertTrue(p.annotation_text.isVisible())
        self.assertFalse(p.annotation_target_x.isVisible(), 'A text note has no arrow head')
        self.assertIs(p.property_editor('annotation', 'note'), p.annotation_text)

    def test_invalid_hidden_custom_ticks_are_revealed_without_losing_text(self):
        p = self.page
        header = self.header('Custom ticks')
        header.setChecked(True)
        p.custom_ticks.setChecked(True)
        self.reach(p.tick_values_edit)
        p.tick_values_edit.setText('0, invalid, 10')
        QTest.mouseClick(header, Qt.LeftButton)
        self.assertFalse(p.tick_values_edit.isVisible())
        with self.assertRaises(ValueError):
            p.finish_editor()
        self.assertTrue(p.tick_values_edit.isVisible())
        self.assertTrue(p.tick_values_edit.hasFocus())
        self.assertEqual(p.tick_values_edit.text(), '0, invalid, 10')


class WorkspaceDisclosureTests(unittest.TestCase):
    def setUp(self):
        folder = isolate_settings(self).parent
        path = folder / 'spectrum.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(15)))
        self.w = MainWindow()
        self.w._load_dat_file_direct(path)
        self.w.open_graph_document()
        self.w.resize(1500, 920)
        self.w.show()
        self.w.activateWindow()
        APP.processEvents()
        self.w._render_plot_page()
        self.w._plot_render_timer.stop()
        QTest.qWait(100)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def test_section_navigation_preserves_graph_and_does_not_render(self):
        w, p = self.w, self.w.plot_page
        before = (deepcopy(w.workspace.active.active_graph.view), p.presentation,
                  deepcopy(w._graph_revisions), w.workspace.active.current_cube.z.copy())
        headers = {b.text(): b for b in p.control_scroll.findChildren(QToolButton)}
        with patch.object(w, '_render_plot_page') as render, \
                patch.object(w.canvas, 'draw') as draw, \
                patch.object(w, '_layout_live_plots') as layout:
            for name in ('Label templates', 'Frame and title layout', 'Axes and ticks', 'Colorbar layout'):
                header = headers[name]
                header.setChecked(True)
                p.control_scroll.ensureWidgetVisible(header)
                QTest.mouseClick(header, Qt.LeftButton)
                QTest.keyClick(header, Qt.Key_Space)
                QTest.keyClick(header, Qt.Key_Left)
                APP.processEvents()
            QTest.qWait(250)
            self.assertFalse(w._plot_render_timer.isActive())
            render.assert_not_called()
            draw.assert_not_called()
            layout.assert_not_called()
        self.assertEqual(w.workspace.active.active_graph.view, before[0])
        self.assertEqual(p.presentation, before[1])
        self.assertEqual(w._graph_revisions, before[2])
        self.assertTrue((w.workspace.active.current_cube.z == before[3]).all())

    def test_property_navigation_reveals_shared_and_page_owned_fields(self):
        w, p = self.w, self.w.plot_page
        for kind, editor in (('title', w.plot_title_edit), ('signal-label', w.signal_title_combo),
                             ('horizontal-label', w.x_axis_override_combo),
                             ('horizontal-range', w.xmin_spin), ('horizontal-axis', p.x_mode_combo),
                             ('colorbar', p.custom_ticks)):
            with self.subTest(kind=kind):
                w._show_plot_property(kind, 'global' if kind == 'colorbar' else '')
                APP.processEvents()
                self.assertTrue(editor.isVisible())
                self.assertTrue(editor.hasFocus())
                point = editor.mapTo(p.control_scroll.viewport(), editor.rect().center())
                self.assertTrue(p.control_scroll.viewport().rect().contains(point))

    def test_preview_doubleclick_reveals_collapsed_axes_and_annotation(self):
        w, p = self.w, self.w.plot_page
        annotation = p.add_annotation('text', 'Manual note')
        w._render_plot_page()
        APP.processEvents()
        for kind, editor in (('vertical-axis', p.y_mode_combo),
                             ('annotation:' + annotation.id, p.annotation_text)):
            self.assertFalse(editor.isVisible())
            left, bottom, width, height = p.rendered_page.object_bounds[kind][0]
            point = QPoint(round((left + width / 2) * p.preview.width()),
                           round((1 - bottom - height / 2) * p.preview.height()))
            QTest.mouseDClick(p.preview, Qt.LeftButton, pos=point)
            APP.processEvents()
            self.assertTrue(editor.isVisible())
            self.assertTrue(editor.hasFocus())
            point = editor.mapTo(p.control_scroll.viewport(), editor.rect().center())
            self.assertTrue(p.control_scroll.viewport().rect().contains(point))


if __name__ == '__main__':
    unittest.main()
