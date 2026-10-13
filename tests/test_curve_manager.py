import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.backend_bases import MouseEvent
from PySide6.QtCore import Qt, QModelIndex, QItemSelectionModel, QPoint, QPointF, QEvent
from PySide6.QtGui import QColor, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.dataset_state import Workspace
from spectra_app.project_io import read_project, write_project, workspace_digest
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_plot_page import XYPlotPage, draw_comparison
from matplotlib.figure import Figure

APP = QApplication.instance() or QApplication([])


class CurveManagerTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        self.book = create_workbook(self.workspace, 'Experiment A')
        self.sheet = register_xy_sheet(self.workspace, self.book.workbook_id, 'Original',
            np.array([[0., 1.], [1., 2.], [2., 1.]]), ['Time (s)', 'Signal'],
            source_path=Path('C:/measurements/sample.csv'))
        self.graph = create_comparison(self.workspace, 'Comparison')
        self.curves = [add_curve(self.workspace, self.graph.comparison_id, self.sheet.sheet_id,
                       0, 1, label=f'Measurement {index + 1}') for index in range(8)]
        for index, curve in enumerate(self.curves):
            curve.y_offset = index * 3.
        self.page = XYPlotPage(self.workspace, self.graph.comparison_id)
        self.addCleanup(self.dispose)
        self.page.resize(1000, 750)
        self.page.show()
        APP.processEvents()

    def dispose(self):
        self.page.close()
        self.page.dispose()
        self.page.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def selected_ids(self):
        return {item.data(Qt.UserRole) for item in self.page.curve_list.selectedItems()}

    def select(self, rows, current=None):
        widget = self.page.curve_list
        widget.clearSelection()
        for row in rows:
            widget.item(row).setSelected(True)
        widget.setCurrentItem(widget.item(rows[0] if current is None else current), QItemSelectionModel.NoUpdate)

    def click_row(self, row, modifier=Qt.NoModifier):
        widget = self.page.curve_list
        widget.scrollToItem(widget.item(row))
        APP.processEvents()
        rect = widget.visualItemRect(widget.item(row))
        QTest.mouseClick(widget.viewport(), Qt.LeftButton, modifier, QPoint(rect.center().x(), rect.center().y()))
        # QTest's synthetic mouse modifiers remain in Qt's global input state
        # until released, unlike a completed physical Ctrl/Shift gesture.
        if modifier & Qt.ControlModifier:
            QTest.keyRelease(widget, Qt.Key_Control)
        if modifier & Qt.ShiftModifier:
            QTest.keyRelease(widget, Qt.Key_Shift)

    def drop(self, rows, destination):
        widget = self.page.curve_list
        mime = widget.model().mimeData([widget.model().index(row, 0) for row in rows])
        return widget.model().dropMimeData(mime, Qt.MoveAction, destination, 0, QModelIndex())

    def plot_click(self, curve_index, *, button=1, key=None):
        self.page.canvas.draw()
        curve = self.curves[curve_index]
        line = next(line for line in self.page.axes.lines if line.get_gid() == curve.curve_id)
        x, y = line.get_transform().transform(line.get_xydata()[1])
        event = MouseEvent('button_press_event', self.page.canvas, x, y, button=button, key=key)
        self.page.canvas.callbacks.process('button_press_event', event)

    def test_rows_show_provenance_and_native_ctrl_shift_selection_survives_refresh(self):
        item = self.page.curve_list.item(0)
        for text in ('Measurement 1', 'Experiment A', 'Original', 'Time (s)', 'Signal', 'raw'):
            self.assertIn(text, item.text())
        self.assertIn(str(self.sheet.source_path), item.toolTip())
        self.assertFalse(item.icon().isNull())
        self.click_row(0)
        self.click_row(2, Qt.ControlModifier)
        self.assertEqual(self.selected_ids(), {self.curves[0].curve_id, self.curves[2].curve_id})
        self.click_row(4, Qt.ShiftModifier)
        expected = self.selected_ids()
        self.assertTrue({self.curves[i].curve_id for i in (2, 3, 4)} <= expected)
        scrollbar = self.page.curve_list.verticalScrollBar()
        scroll = scrollbar.value()
        self.page.refresh()
        self.assertEqual(self.selected_ids(), expected)
        self.assertEqual(self.page.curve_list.currentItem().data(Qt.UserRole), self.curves[4].curve_id)
        self.assertEqual(scrollbar.value(), scroll)

    def test_batch_color_width_and_visibility_preserve_unrelated_fields_and_cancel(self):
        self.select([0, 2])
        before = [(c.label, c.y_offset, c.sheet_id, c.mode) for c in self.curves]
        colors = [c.color for c in self.curves]
        data = self.sheet.data.tobytes()
        with patch('spectra_app.xy_plot_page.QColorDialog.getColor', return_value=QColor()):
            self.page.batch_color_btn.click()
        self.assertEqual([c.color for c in self.curves], colors)
        with patch('spectra_app.xy_plot_page.QColorDialog.getColor', return_value=QColor('#9944aa')):
            self.page.batch_color_btn.click()
        with patch('spectra_app.xy_plot_page.QInputDialog.getDouble', return_value=(4.25, False)):
            self.page.batch_width_btn.click()
        self.assertTrue(all(c.width == 1.5 for c in self.curves))
        with patch('spectra_app.xy_plot_page.QInputDialog.getDouble', return_value=(4.25, True)):
            self.page.batch_width_btn.click()
        for i, curve in enumerate(self.curves):
            self.assertEqual(curve.color, '#9944aa' if i in (0, 2) else colors[i])
            self.assertEqual(curve.width, 4.25 if i in (0, 2) else 1.5)
        self.page.hide_selected_action.trigger()
        self.assertEqual([c.visible for c in self.curves], [False, True, False, True, True, True, True, True])
        self.page.show_selected_action.trigger()
        self.assertTrue(all(c.visible for c in self.curves))
        self.page.only_selected_action.trigger()
        self.assertEqual([c.visible for c in self.curves], [True, False, True, False, False, False, False, False])
        self.page.show_all_action.trigger()
        self.assertTrue(all(c.visible for c in self.curves))
        self.assertEqual(before, [(c.label, c.y_offset, c.sheet_id, c.mode) for c in self.curves])
        self.assertEqual(data, self.sheet.data.tobytes())

    def test_native_model_mime_drop_moves_block_and_roundtrips_plot_stack_order(self):
        self.graph.stack_step = 10.
        self.curves[1].visible = False
        self.page.refresh()
        self.select([0, 2], current=2)
        before = workspace_digest(self.workspace)
        self.assertTrue(self.drop([2, 0], 5))
        order = [1, 3, 4, 0, 2, 5, 6, 7]
        ids = [self.curves[i].curve_id for i in order]
        self.assertEqual(list(self.graph.curves), ids)
        self.assertNotEqual(before, workspace_digest(self.workspace))
        self.assertEqual(self.selected_ids(), {self.curves[i].curve_id for i in (0, 2)})
        self.assertEqual(self.page.curve_list.currentItem().data(Qt.UserRole), self.curves[2].curve_id)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'curves.spectra'
            write_project(path, self.workspace)
            restored = read_project(path)
        self.assertEqual(list(restored.comparisons[self.graph.comparison_id].curves), ids)
        reopened = draw_comparison(Figure(), restored, restored.comparisons[self.graph.comparison_id])
        self.assertEqual([line.get_gid() for line in reopened.lines], [line.get_gid() for line in self.page.axes.lines])
        for actual, expected in zip(reopened.lines, self.page.axes.lines):
            np.testing.assert_array_equal(actual.get_ydata(), expected.get_ydata())
        self.assertEqual(list(self.page.axes.get_legend_handles_labels()[1]), [self.curves[i].label for i in order if i != 1])
        np.testing.assert_array_equal(self.page.axes.lines[0].get_ydata(), self.sheet.data[:, 1] + 19.)

    def test_external_or_other_graph_mime_rejected(self):
        graph = create_comparison(self.workspace, 'Other')
        add_curve(self.workspace, graph.comparison_id, self.sheet.sheet_id, 0, 1)
        other = XYPlotPage(self.workspace, graph.comparison_id)
        self.addCleanup(other.deleteLater)
        mime = other.curve_list.model().mimeData([other.curve_list.model().index(0, 0)])
        before = list(self.graph.curves)
        self.assertFalse(self.page.curve_list.model().dropMimeData(mime, Qt.MoveAction, 0, 0, QModelIndex()))
        self.assertEqual(list(self.graph.curves), before)

    def test_drop_event_uses_same_list_mime_without_source_deletion(self):
        widget = self.page.curve_list
        self.select([0, 2], current=2)
        mime = widget.model().mimeData([widget.model().index(row, 0) for row in (0, 2)])

        class SameListDrop(QDropEvent):
            def source(self):
                return widget

        # Real Qt drop event and MIME; only the OS-owned source accessor is
        # supplied because offscreen Qt cannot conduct a Windows drag session.
        event = SameListDrop(QPointF(widget.visualItemRect(widget.item(3)).topLeft()),
                            Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        widget.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(list(self.graph.curves), [self.curves[i].curve_id for i in (1, 0, 2, 3, 4, 5, 6, 7)])
        self.assertEqual(widget.count(), 8)
        self.assertEqual(self.selected_ids(), {self.curves[i].curve_id for i in (0, 2)})

    def test_move_buttons_preserve_multiselection_and_removal_has_coherent_default(self):
        self.select([1, 2])
        self.page.move_up_btn.click()
        self.assertEqual(list(self.graph.curves), [self.curves[i].curve_id for i in (1, 2, 0, 3, 4, 5, 6, 7)])
        self.page.move_down_btn.click()
        self.assertEqual(list(self.graph.curves), [c.curve_id for c in self.curves])
        self.assertEqual(self.selected_ids(), {self.curves[i].curve_id for i in (1, 2)})
        self.page.remove_btn.click()
        self.assertNotIn(self.curves[1].curve_id, self.graph.curves)
        self.assertEqual(self.page.label_edit.text(), self.curves[2].label)
        self.assertTrue(self.selected_ids())
        while self.graph.curves:
            self.page.remove_btn.click()
        self.assertEqual(self.page.curve_list.count(), 0)
        self.assertFalse(self.page.batch_color_btn.isEnabled())
        self.assertFalse(self.page.batch_width_btn.isEnabled())
        self.assertFalse(self.page.label_edit.isEnabled())

    def test_pending_edit_is_owned_by_previous_curve_on_switch(self):
        first, second = self.curves[:2]
        self.page.label_edit.setText('Pending label')
        self.page.y_offset_edit.setText('7.25')
        self.page.width_spin.lineEdit().setText('3.125')
        self.page.curve_list.setCurrentRow(1)
        self.assertEqual((first.label, first.y_offset, first.width), ('Pending label', 7.25, 3.125))
        self.assertEqual((second.label, second.y_offset, second.width), ('Measurement 2', 3., 1.5))
        self.assertEqual(self.page.label_edit.text(), second.label)

    def test_refresh_retains_valid_and_invalid_drafts_and_single_edits_are_not_batch(self):
        self.select([0, 2])
        self.page.label_edit.setText('Pending current label')
        self.page.refresh()
        self.assertEqual(self.page.label_edit.text(), 'Pending current label')
        self.assertEqual(self.curves[0].label, 'Measurement 1')
        self.page.color_edit.setText('bad')
        self.page.refresh()
        self.assertEqual(self.page.color_edit.text(), 'bad')
        self.assertEqual(self.page.label_edit.text(), 'Pending current label')
        self.page.color_edit.setText('#112233')
        self.page.finish_editor()
        self.assertEqual((self.curves[0].label, self.curves[0].color), ('Pending current label', '#112233'))
        self.assertEqual(self.curves[2].label, 'Measurement 3')
        self.assertNotEqual(self.curves[2].color, '#112233')

    def test_invalid_draft_blocks_switch_batch_drop_visibility_and_canvas(self):
        self.select([0, 2])
        ids = self.selected_ids()
        before = workspace_digest(self.workspace)
        self.page.color_edit.setText('invalid')
        self.page.curve_list.setCurrentRow(1)
        self.assertEqual(self.page.curve_list.currentRow(), 0)
        self.page.hide_selected_action.trigger()
        with patch('spectra_app.xy_plot_page.QColorDialog.getColor') as dialog:
            self.page.batch_color_btn.click()
            dialog.assert_not_called()
        self.assertFalse(self.drop([0, 2], 5))
        self.page.curve_list.item(1).setCheckState(Qt.Unchecked)
        self.assertEqual(self.page.curve_list.item(1).checkState(), Qt.Checked)
        self.plot_click(3)
        self.assertEqual(self.page.curve_list.currentRow(), 0)
        self.assertEqual(self.selected_ids(), ids)
        self.assertEqual(self.page.color_edit.text(), 'invalid')
        self.assertEqual(workspace_digest(self.workspace), before)
        with self.assertRaises(ValueError):
            self.page.finish_editor()

    def test_canvas_overlap_tie_is_deterministic_and_background_does_not_select(self):
        self.curves[1].y_offset = self.curves[0].y_offset
        self.graph.normalization = 'minmax'
        self.page.refresh()
        self.page.curve_list.setCurrentRow(7)
        self.plot_click(1)
        self.assertEqual(self.page.curve_list.currentRow(), 0)
        x, y = self.page.axes.transData.transform((.5, 20.))
        event = MouseEvent('button_press_event', self.page.canvas, x, y, button=1)
        self.page.canvas.callbacks.process('button_press_event', event)
        self.assertEqual(self.page.curve_list.currentRow(), 0)

    def test_width_draft_survives_refresh_then_external_precise_width_can_reload(self):
        self.page.width_spin.lineEdit().setText('invalid')
        self.page.refresh()
        self.assertEqual(self.page.width_spin.lineEdit().text(), 'invalid')
        with self.assertRaises(ValueError):
            self.page.finish_editor()
        self.page.width_spin.lineEdit().setText('2.5')
        self.page.finish_editor()
        self.curves[0].width = .123456789012345
        self.page.refresh()
        self.page.finish_editor()
        self.assertEqual(self.curves[0].width, .123456789012345)

    def test_canvas_selects_and_scrolls_without_style_mutation_and_ignores_navigation(self):
        before = workspace_digest(self.workspace)
        outer = self.page.inspector.verticalScrollBar()
        outer.setValue(outer.maximum())
        self.plot_click(7)
        self.assertEqual(outer.value(), 0)
        self.assertEqual(self.page.curve_list.currentRow(), 7)
        self.assertTrue(self.page.curve_list.viewport().rect().intersects(
            self.page.curve_list.visualItemRect(self.page.curve_list.item(7))))
        self.plot_click(2, key='control')
        self.assertEqual(self.selected_ids(), {self.curves[i].curve_id for i in (2, 7)})
        self.plot_click(0, button=3)
        self.assertEqual(self.page.curve_list.currentRow(), 2)
        self.page.toolbar.pan()
        self.plot_click(1)
        self.assertEqual(self.page.curve_list.currentRow(), 2)
        self.page.toolbar.pan()
        self.page.toolbar.zoom()
        self.plot_click(1)
        self.assertEqual(self.page.curve_list.currentRow(), 2)
        self.page.toolbar.zoom()
        self.assertEqual(workspace_digest(self.workspace), before)


if __name__ == '__main__':
    unittest.main()
