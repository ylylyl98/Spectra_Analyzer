import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.backend_bases import MouseEvent
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent, QObject
from PySide6.QtGui import QColor, QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.dataset_state import Workspace
from spectra_app.project_io import read_project, write_project, workspace_digest
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_plot_page import XYPlotPage, draw_comparison
from matplotlib.figure import Figure

from tests.xy_helpers import set_xy_draft, xy_value, select_xy_rows, xy_field, click_xy_item
from tests.test_curve_prompt_integration import run_quick_prompt

APP = QApplication.instance() or QApplication([])


def quick_curve_list(page):
    return page.curves_panel.rootObject().findChild(QObject, 'xyCurveList')


def quick_curve_row(page, row):
    view = quick_curve_list(page)
    view.setProperty('contentY', min(row * 42., max(0., view.property('contentHeight') - view.height())))
    APP.processEvents()
    pending = list(view.childItems())
    while pending:
        item = pending.pop()
        if item.property('rowId') == list(page.comparison.curves)[row]:
            return item
        pending.extend(item.childItems())
    raise AssertionError(f'No rendered QML curve row {row}')


def click_curve_row(page, row, modifier=Qt.NoModifier):
    item = quick_curve_row(page, row)
    point = item.mapToScene(QPointF(item.width() * .6, item.height() / 2))
    QTest.mouseClick(page.curves_panel, Qt.LeftButton, modifier, QPoint(round(point.x()), round(point.y())))
    if modifier & Qt.ControlModifier:
        QTest.keyRelease(page.curves_panel, Qt.Key_Control)
    if modifier & Qt.ShiftModifier:
        QTest.keyRelease(page.curves_panel, Qt.Key_Shift)
    APP.processEvents()


def curve_role(page, row, name):
    model = page.inspector_bridge.curves
    role = next(key for key, value in model.roleNames().items() if bytes(value).decode() == name)
    return model.data(model.index(row), role)


def click_quick_control(panel, object_name):
    item = panel.rootObject().findChild(QObject, object_name)
    assert item is not None, object_name
    parent = item.parentItem()
    while parent is not None:
        if parent.metaObject().indexOfProperty('contentY') >= 0:
            point = item.mapToItem(parent, QPointF(0, 0))
            parent.setProperty('contentY', max(0., min(parent.property('contentY') + point.y(),
                               parent.property('contentHeight') - parent.height())))
        parent = parent.parentItem()
    APP.processEvents()
    click_xy_item(panel, item)


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
        return self.page._selected_ids()

    def select(self, rows, current=None):
        return select_xy_rows(self.page, rows, current=current)

    def click_row(self, row, modifier=Qt.NoModifier):
        click_curve_row(self.page, row, modifier)

    def drop(self, rows, destination):
        # QML row gestures pass selected identities to the document's move command.
        return self.page.controller._move_curves({list(self.page.comparison.curves)[row] for row in rows}, destination)

    def plot_click(self, curve_index, *, button=1, key=None):
        self.page.canvas.draw()
        curve = self.curves[curve_index]
        line = next(line for line in self.page.axes.lines if line.get_gid() == curve.curve_id)
        x, y = line.get_transform().transform(line.get_xydata()[1])
        event = MouseEvent('button_press_event', self.page.canvas, x, y, button=button, key=key)
        self.page.canvas.callbacks.process('button_press_event', event)

    def test_rows_show_provenance_and_quick_ctrl_shift_selection_survives_refresh(self):
        self.assertEqual(curve_role(self.page, 0, 'caption'), 'Measurement 1')
        accessible = QAccessible.queryAccessibleInterface(quick_curve_row(self.page, 0))
        self.assertIn('Measurement 1', accessible.text(QAccessible.Name))
        for text in ('Experiment A', 'Original', 'Time (s)', 'Signal', 'raw'):
            self.assertIn(text, curve_role(self.page, 0, 'sourceDescription'))
            self.assertIn(text, accessible.text(QAccessible.Description))
        self.assertIn(str(self.sheet.source_path), curve_role(self.page, 0, 'sourceDescription'))
        self.assertTrue(QColor(curve_role(self.page, 0, 'curveColor')).isValid())
        self.click_row(0)
        self.click_row(2, Qt.ControlModifier)
        self.assertEqual(self.selected_ids(), {self.curves[0].curve_id, self.curves[2].curve_id})
        self.click_row(4, Qt.ShiftModifier)
        expected = self.selected_ids()
        self.assertTrue({self.curves[i].curve_id for i in (2, 3, 4)} <= expected)
        view = quick_curve_list(self.page)
        scroll = view.property('contentY')
        self.page.refresh()
        self.assertEqual(self.selected_ids(), expected)
        self.assertEqual(self.page._active_curve_id, self.curves[4].curve_id)
        self.assertEqual(view.property('contentY'), scroll)

    def test_batch_color_width_and_visibility_preserve_unrelated_fields_and_cancel(self):
        self.select([0, 2])
        before = [(c.label, c.y_offset, c.sheet_id, c.mode) for c in self.curves]
        colors = [c.color for c in self.curves]
        data = self.sheet.data.tobytes()
        history = self.page._edit_history.undo_stack
        run_quick_prompt(self.page, lambda: self.page.inspector_bridge.action('batchColor'),
                         text='#9944aa', accept=False, expected_initial=colors[0])
        self.assertEqual([c.color for c in self.curves], colors)
        self.assertEqual(len(history), 0)
        run_quick_prompt(self.page, lambda: self.page.inspector_bridge.action('batchColor'), text='#9944aa')
        self.assertEqual(len(history), 1)
        self.assertTrue(history[-1].appearance_only)
        run_quick_prompt(self.page, lambda: self.page.inspector_bridge.action('batchWidth'),
                         mode='float', text='4.25', accept=False)
        self.assertTrue(all(c.width == 1.5 for c in self.curves))
        self.assertEqual(len(history), 1)
        run_quick_prompt(self.page, lambda: self.page.inspector_bridge.action('batchWidth'),
                         mode='float', text='4.25')
        self.assertEqual(len(history), 2)
        self.assertTrue(history[-1].appearance_only)
        run_quick_prompt(self.page, lambda: self.page.inspector_bridge.action('batchWidth'),
                         mode='float', text='4.25')
        self.assertEqual(len(history), 2)
        self.assertEqual(self.selected_ids(), {self.curves[i].curve_id for i in (0, 2)})
        for i, curve in enumerate(self.curves):
            self.assertEqual(curve.color, '#9944aa' if i in (0, 2) else colors[i])
            self.assertEqual(curve.width, 4.25 if i in (0, 2) else 1.5)
        self.page.inspector_bridge.visibilityCommand('hide')
        self.assertEqual([c.visible for c in self.curves], [False, True, False, True, True, True, True, True])
        self.page.inspector_bridge.visibilityCommand('show')
        self.assertTrue(all(c.visible for c in self.curves))
        self.page.inspector_bridge.visibilityCommand('only')
        self.assertEqual([c.visible for c in self.curves], [True, False, True, False, False, False, False, False])
        self.page.inspector_bridge.visibilityCommand('all')
        self.assertTrue(all(c.visible for c in self.curves))
        self.assertEqual(before, [(c.label, c.y_offset, c.sheet_id, c.mode) for c in self.curves])
        self.assertEqual(data, self.sheet.data.tobytes())

    def test_quick_move_command_moves_block_and_roundtrips_plot_stack_order(self):
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
        self.assertEqual(self.page._active_curve_id, self.curves[2].curve_id)
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

    def test_other_graph_curves_are_not_mutated_by_local_reorder(self):
        graph = create_comparison(self.workspace, 'Other')
        add_curve(self.workspace, graph.comparison_id, self.sheet.sheet_id, 0, 1)
        other = XYPlotPage(self.workspace, graph.comparison_id)
        self.addCleanup(other.dispose)
        self.addCleanup(other.deleteLater)
        foreign_order = list(graph.curves)
        foreign_values = [(c.label, c.color, c.width, c.visible) for c in graph.curves.values()]
        self.select([0, 2], current=2)
        self.page.inspector_bridge.moveTo(5)
        self.assertEqual(list(graph.curves), foreign_order)
        self.assertEqual([(c.label, c.color, c.width, c.visible) for c in graph.curves.values()], foreign_values)
        self.assertEqual(list(self.graph.curves), [self.curves[i].curve_id for i in (1, 3, 4, 0, 2, 5, 6, 7)])

    def test_quick_row_drag_moves_selection_without_source_deletion(self):
        self.select([0, 2], current=2)
        row = quick_curve_row(self.page, 2)
        start = row.mapToScene(QPointF(row.width() * .6, 12))
        finish = start + QPointF(0, 21)
        # Shift retains the selected block when the row takes pointer focus.
        QTest.mousePress(self.page.curves_panel, Qt.LeftButton, Qt.ShiftModifier,
                         QPoint(round(start.x()), round(start.y())))
        QTest.mouseMove(self.page.curves_panel, QPoint(round(finish.x()), round(finish.y())))
        QTest.mouseRelease(self.page.curves_panel, Qt.LeftButton, Qt.ShiftModifier,
                           QPoint(round(finish.x()), round(finish.y())))
        QTest.keyRelease(self.page.curves_panel, Qt.Key_Shift)
        APP.processEvents()
        self.assertEqual(list(self.graph.curves), [self.curves[i].curve_id for i in (1, 0, 2, 3, 4, 5, 6, 7)])
        self.assertEqual(self.page.inspector_bridge.curves.rowCount(), 8)
        self.assertEqual(self.selected_ids(), {self.curves[i].curve_id for i in (0, 2)})

    def test_move_buttons_preserve_multiselection_and_removal_has_coherent_default(self):
        self.select([1, 2])
        self.page.inspector_bridge.moveSelected(-1)
        self.assertEqual(list(self.graph.curves), [self.curves[i].curve_id for i in (1, 2, 0, 3, 4, 5, 6, 7)])
        self.page.inspector_bridge.moveSelected(1)
        self.assertEqual(list(self.graph.curves), [c.curve_id for c in self.curves])
        self.assertEqual(self.selected_ids(), {self.curves[i].curve_id for i in (1, 2)})
        self.page.inspector_bridge.action('remove')
        self.assertNotIn(self.curves[1].curve_id, self.graph.curves)
        self.assertEqual(xy_value(self.page, 'label'), self.curves[2].label)
        self.assertTrue(self.selected_ids())
        while self.graph.curves:
            self.page.inspector_bridge.action('remove')
        self.assertEqual(self.page.inspector_bridge.curves.rowCount(), 0)
        self.assertEqual(self.page.inspector_bridge.selectedCount, 0)
        self.assertFalse(self.page.inspector_bridge.hasCurrent)
        self.assertFalse(xy_field(self.page, 'label')[1].isEnabled())

    def test_pending_edit_is_owned_by_previous_curve_on_switch(self):
        first, second = self.curves[:2]
        set_xy_draft(self.page, 'label', 'Pending label')
        set_xy_draft(self.page, 'y_offset', '7.25')
        set_xy_draft(self.page, 'width', '3.125')
        select_xy_rows(self.page, [1])
        self.assertEqual((first.label, first.y_offset, first.width), ('Pending label', 7.25, 3.125))
        self.assertEqual((second.label, second.y_offset, second.width), ('Measurement 2', 3., 1.5))
        self.assertEqual(xy_value(self.page, 'label'), second.label)

    def test_refresh_retains_valid_and_invalid_drafts_and_single_edits_are_not_batch(self):
        self.select([0, 2])
        set_xy_draft(self.page, 'label', 'Pending current label')
        self.page.refresh()
        self.assertEqual(xy_value(self.page, 'label'), 'Pending current label')
        self.assertEqual(self.curves[0].label, 'Measurement 1')
        set_xy_draft(self.page, 'color', 'bad')
        self.page.refresh()
        self.assertEqual(xy_value(self.page, 'color'), 'bad')
        self.assertEqual(xy_value(self.page, 'label'), 'Pending current label')
        set_xy_draft(self.page, 'color', '#112233')
        self.page.finish_editor()
        self.assertEqual((self.curves[0].label, self.curves[0].color), ('Pending current label', '#112233'))
        self.assertEqual(self.curves[2].label, 'Measurement 3')
        self.assertNotEqual(self.curves[2].color, '#112233')

    def test_invalid_draft_blocks_switch_batch_drop_visibility_and_canvas(self):
        self.select([0, 2])
        ids = self.selected_ids()
        before = workspace_digest(self.workspace)
        set_xy_draft(self.page, 'color', 'invalid')
        select_xy_rows(self.page, [1])
        self.assertEqual(list(self.page.comparison.curves).index(self.page._active_curve_id), 0)
        self.page.inspector_bridge.visibilityCommand('hide')
        with patch('spectra_app.xy_plot_controller.prompt_color') as dialog, \
                patch('spectra_app.xy_plot_controller.prompt_float') as width_dialog:
            self.page.inspector_bridge.action('batchColor')
            self.page.inspector_bridge.action('color')
            self.page.inspector_bridge.action('batchWidth')
            dialog.assert_not_called()
            width_dialog.assert_not_called()
        self.assertFalse(self.drop([0, 2], 5))
        self.page.inspector_bridge.setVisibility(self.curves[1].curve_id, False)
        self.assertTrue(curve_role(self.page, 1, 'shown'))
        self.plot_click(3)
        self.assertEqual(list(self.page.comparison.curves).index(self.page._active_curve_id), 0)
        self.assertEqual(self.selected_ids(), ids)
        self.assertEqual(xy_value(self.page, 'color'), 'invalid')
        self.assertEqual(workspace_digest(self.workspace), before)
        with self.assertRaises(ValueError):
            self.page.finish_editor()

    def test_canvas_overlap_tie_is_deterministic_and_background_does_not_select(self):
        self.curves[1].y_offset = self.curves[0].y_offset
        self.graph.normalization = 'minmax'
        self.page.refresh()
        select_xy_rows(self.page, [7])
        self.plot_click(1)
        self.assertEqual(list(self.page.comparison.curves).index(self.page._active_curve_id), 0)
        x, y = self.page.axes.transData.transform((.5, 20.))
        event = MouseEvent('button_press_event', self.page.canvas, x, y, button=1)
        self.page.canvas.callbacks.process('button_press_event', event)
        self.assertEqual(list(self.page.comparison.curves).index(self.page._active_curve_id), 0)

    def test_width_draft_survives_refresh_then_external_precise_width_can_reload(self):
        set_xy_draft(self.page, 'width', 'invalid')
        self.page.refresh()
        self.assertEqual(xy_value(self.page, 'width'), 'invalid')
        with self.assertRaises(ValueError):
            self.page.finish_editor()
        set_xy_draft(self.page, 'width', '2.5')
        self.page.finish_editor()
        self.curves[0].width = .123456789012345
        self.page.refresh()
        self.page.finish_editor()
        self.assertEqual(self.curves[0].width, .123456789012345)

    def test_canvas_selects_and_scrolls_without_style_mutation_and_ignores_navigation(self):
        before = workspace_digest(self.workspace)
        view = quick_curve_list(self.page)
        outer = view.parentItem()
        while outer is not None and outer.metaObject().indexOfProperty('contentY') < 0:
            outer = outer.parentItem()
        self.assertIsNotNone(outer)
        outer.setProperty('contentY', max(0., outer.property('contentHeight') - outer.height()))
        view.setProperty('contentY', 0.)
        self.plot_click(7)
        APP.processEvents()
        self.assertEqual(outer.property('contentY'), 0.)
        self.assertGreater(view.property('contentY'), 0.)
        self.assertEqual(list(self.page.comparison.curves).index(self.page._active_curve_id), 7)
        row = next(item for item in view.property('contentItem').childItems() if item.property('rowId') == self.curves[7].curve_id)
        self.assertLessEqual(row.y() - view.property('contentY'), view.height())
        self.assertGreaterEqual(row.y() - view.property('contentY') + row.height(), 0)
        self.plot_click(2, key='control')
        self.assertEqual(self.selected_ids(), {self.curves[i].curve_id for i in (2, 7)})
        self.plot_click(0, button=3)
        self.assertEqual(list(self.page.comparison.curves).index(self.page._active_curve_id), 2)
        self.page.toolbar.pan()
        self.plot_click(1)
        self.assertEqual(list(self.page.comparison.curves).index(self.page._active_curve_id), 2)
        self.page.toolbar.pan()
        self.page.toolbar.zoom()
        self.plot_click(1)
        self.assertEqual(list(self.page.comparison.curves).index(self.page._active_curve_id), 2)
        self.page.toolbar.zoom()
        self.assertEqual(workspace_digest(self.workspace), before)


if __name__ == '__main__':
    unittest.main()
