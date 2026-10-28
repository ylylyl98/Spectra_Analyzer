"""Selection is a reversible preview, independent of exports and analysis input."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import re
import unittest

import matplotlib as mpl
import numpy as np
from matplotlib.backend_bases import MouseEvent
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as formal
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from tests import test_xy_analysis_workflow as analysis_fixture
from tests.settings_helpers import wait_for_background_jobs
from spectra_app.project_io import workspace_digest

from tests.xy_helpers import set_xy_draft, xy_value, select_xy_rows, xy_field
from tests.xy_canvas_quick_helpers import key as canvas_key
from tests.xy_analysis_quick_helpers import analysis_command

def quick_curve_list(page):
    from tests.xy_helpers import inspector_host
    return formal.ensure_visible(formal.item(inspector_host(page), 'xyCurveList'))


def inspector_key(page, code):
    QTest.keyClick(page.parent().root_window, code)
    formal.pump()


def click_curve_row(page, row, modifier=Qt.NoModifier):
    from PySide6.QtCore import QPointF
    view = quick_curve_list(page)
    view.setProperty('contentY', min(row * 42., max(0., view.property('contentHeight') - view.height())))
    formal.pump()
    pending = list(view.childItems())
    while pending:
        control = pending.pop()
        if control.property('rowId') == list(page.comparison.curves)[row]:
            formal.ensure_visible(control)
            point = control.mapToScene(QPointF(control.width() * .6, control.height() / 2)).toPoint()
            window = page.parent().root_window
            QTest.mouseClick(window, Qt.LeftButton, modifier, point)
            if modifier & Qt.ControlModifier:
                QTest.keyRelease(window, Qt.Key_Control)
            if modifier & Qt.ShiftModifier:
                QTest.keyRelease(window, Qt.Key_Shift)
            formal.pump()
            return
        pending.extend(control.childItems())
    raise AssertionError(f'No rendered QML curve row {row}')


class SelectionHighlightTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.workspace = self.controller.workspace
        self.book = create_workbook(self.workspace, 'Experiment A')
        self.sheet = register_xy_sheet(self.workspace, self.book.workbook_id, 'Original',
            np.array([[0., 1.], [1., 2.], [2., 1.]]), ['Time (s)', 'Signal'],
            source_path=Path('C:/measurements/sample.csv'))
        self.graph = create_comparison(self.workspace, 'Comparison')
        self.curves = [add_curve(self.workspace, self.graph.comparison_id, self.sheet.sheet_id,
                       0, 1, label=f'Measurement {index + 1}') for index in range(8)]
        for index, curve in enumerate(self.curves):
            curve.y_offset = index * 3.
        self.page = self.controller.open_xy_comparison(self.graph.comparison_id)
        select_xy_rows(self.page, [0])
        self.host.window.resize(1000, 750)
        self.addCleanup(self.dispose)
        formal.pump()

    def dispose(self):
        self.assertEqual(self.quick_errors, [])

    def selected_ids(self):
        return self.page._selected_ids()

    def select(self, rows, current=None):
        return select_xy_rows(self.page, rows, current=current)

    def click_row(self, row, modifier=Qt.NoModifier):
        click_curve_row(self.page, row, modifier)

    def lines(self):
        return {line.get_gid(): line for line in self.page.axes.lines}

    def assert_highlight(self, rows):
        selected = {self.curves[row].curve_id for row in rows if self.curves[row].visible}
        lines = self.lines()
        for curve in self.curves:
            if not curve.visible:
                self.assertNotIn(curve.curve_id, lines)
                continue
            line = lines[curve.curve_id]
            active = curve.curve_id in selected
            self.assertEqual(line.get_color(), curve.color)
            self.assertAlmostEqual(line.get_alpha() or 1., .25 if selected and not active else 1.)
            self.assertAlmostEqual(line.get_linewidth(), curve.width * (1.5 if active else 1.))
            if active and curve.style != 'line':
                self.assertGreater(line.get_markersize(), 4)
        if selected:
            active_z = [line.get_zorder() for cid, line in lines.items() if cid in selected]
            faded_z = [line.get_zorder() for cid, line in lines.items() if cid not in selected]
            if faded_z:
                self.assertGreater(min(active_z), max(faded_z))

    def test_list_keyboard_multi_selection_and_escape_restore_preview(self):
        self.click_row(2)
        self.assert_highlight([2])
        quick_curve_list(self.page).forceActiveFocus(); inspector_key(self.page, Qt.Key_Down)
        self.assert_highlight([3])
        self.click_row(5, Qt.ControlModifier)
        self.assert_highlight([3, 5])
        self.click_row(7, Qt.ShiftModifier)
        self.assert_highlight([3, 5, 6, 7])
        quick_curve_list(self.page).forceActiveFocus(); inspector_key(self.page, Qt.Key_Escape)
        self.assertEqual(self.selected_ids(), set())
        self.assert_highlight([])

    def test_highlight_is_preview_only_and_leaves_navigation_model_and_exports_unchanged(self):
        self.page.inspector_bridge.selectAll(False)
        self.page.canvas.draw()
        original_digest = workspace_digest(self.workspace)
        self.page.toolbar.push_current()
        self.page.axes.set_xlim(.4, 1.4)
        self.page.axes.set_ylim(4, 15)
        self.page.toolbar.push_current()
        axes = self.page.axes
        history = len(self.page.toolbar._nav_stack)
        with TemporaryDirectory() as folder, mpl.rc_context({'svg.hashsalt': 'selection-proof'}):
            before_svg = re.sub(rb'<dc:date>.*?</dc:date>', b'', self.page.svg_bytes())
            before = Path(folder) / 'before.png'
            after = Path(folder) / 'after.png'
            self.page.export(before)
            self.click_row(2)
            self.assert_highlight([2])
            self.assertIs(self.page.axes, axes)
            self.assertEqual(axes.get_xlim(), (.4, 1.4))
            self.assertEqual(axes.get_ylim(), (4, 15))
            self.assertEqual(len(self.page.toolbar._nav_stack), history)
            self.assertEqual(re.sub(rb'<dc:date>.*?</dc:date>', b'', self.page.svg_bytes()), before_svg)
            self.page.export(after)
            self.assertEqual(before.read_bytes(), after.read_bytes())
        self.page.inspector_bridge.setHighlight(not self.page.inspector_bridge.highlight)
        self.assert_highlight([])
        self.assertEqual(self.selected_ids(), {self.curves[2].curve_id})
        self.page.inspector_bridge.setHighlight(not self.page.inspector_bridge.highlight)
        self.assert_highlight([2])
        canvas_key(self.page, Qt.Key_Escape)
        self.assertEqual(self.selected_ids(), set())
        self.assert_highlight([])
        self.assertIs(self.page.axes, axes)
        self.assertEqual(axes.get_xlim(), (.4, 1.4))
        self.assertEqual(len(self.page.toolbar._nav_stack), history)
        self.assertEqual(workspace_digest(self.workspace), original_digest)

    def test_styles_legend_and_hidden_selection_restore_after_redraw(self):
        for index, curve in enumerate(self.curves):
            curve.style = ('line', 'scatter', 'line_symbol')[index % 3]
            curve.label = 'Same label'
        self.page.refresh()
        self.select([1, 2])
        self.page.render()
        self.assert_highlight([1, 2])
        legend = self.page.axes.get_legend()
        for index, (handle, text) in enumerate(zip(legend.legend_handles, legend.get_texts())):
            active = index in (1, 2)
            self.assertAlmostEqual(handle.get_alpha() or 1., 1. if active else .25)
            self.assertAlmostEqual(text.get_alpha() or 1., 1. if active else .25)
            self.assertEqual(text.get_fontweight(), 'bold' if active else 'normal')
        self.page._move_selected(-1)
        self.assert_highlight([1, 2])
        self.page._batch_visibility('hide')
        self.assert_highlight([1, 2])  # A hidden-only selection fades no visible curve.
        self.page._batch_visibility('show')
        self.assert_highlight([1, 2])
        self.page.inspector_bridge.selectAll(False)
        self.assert_highlight([])
        for line in self.page.axes.lines:
            self.assertEqual(line.get_markersize(), self.page.comparison.curves[line.get_gid()].marker_size)
            self.assertEqual(line.get_zorder(), 2)

    def test_pick_blank_and_pan_zoom_keep_selection_boundary(self):
        self.graph.normalization = 'maxabs'
        self.graph.stack_step = .5
        self.page.refresh()
        self.page.canvas.draw()
        line = self.lines()[self.curves[4].curve_id]
        x, y = line.get_transform().transform(line.get_xydata()[1])
        event = MouseEvent('button_press_event', self.page.canvas, x, y, button=1)
        self.page._plot_clicked(event)
        self.assert_highlight([4])
        for mode in (self.page.toolbar.pan, self.page.toolbar.zoom):
            mode()
            self.page._plot_clicked(MouseEvent('button_press_event', self.page.canvas,
                *self.page.axes.transAxes.transform((.05, .98)), button=1))
            self.assertEqual(self.selected_ids(), {self.curves[4].curve_id})
            mode()
        self.page._plot_clicked(MouseEvent('button_press_event', self.page.canvas,
            *self.page.axes.transAxes.transform((.05, .98)), button=1))
        self.assertEqual(self.selected_ids(), set())
        self.assert_highlight([])

    def test_auxiliary_overlay_never_takes_pick_priority(self):
        self.page.canvas.draw()
        self.page.axes.plot([.5, .5], [0, 25], gid='xy-analysis-fit')
        self.page._plot_clicked(MouseEvent('button_press_event', self.page.canvas,
            *self.page.axes.transData.transform((.5, 10.5)), button=1))
        self.assertEqual(self.selected_ids(), {self.curves[3].curve_id})

    def test_nan_gaps_and_scatter_do_not_pick_imaginary_segments(self):
        self.sheet.data[1, 1] = np.nan
        self.page.render()
        self.page.canvas.draw()
        self.page._plot_clicked(MouseEvent('button_press_event', self.page.canvas,
            *self.page.axes.transData.transform((1, 1)), button=1))
        self.assertEqual(self.selected_ids(), set())
        self.sheet.data[1, 1] = 2
        self.curves[0].style = 'scatter'
        self.page.render()
        self.page.canvas.draw()
        self.select([1])
        self.page._plot_clicked(MouseEvent('button_press_event', self.page.canvas,
            *self.page.axes.transData.transform((.5, 1.5)), button=1))
        self.assertEqual(self.selected_ids(), set())

    def test_editor_escape_reverts_draft_and_list_escape_keeps_validation_guard(self):
        self.click_row(2)
        set_xy_draft(self.page, 'color', 'invalid')
        quick_curve_list(self.page).forceActiveFocus(); inspector_key(self.page, Qt.Key_Escape)
        self.assertEqual(self.selected_ids(), {self.curves[2].curve_id})
        self.assertEqual(xy_value(self.page, 'color'), 'invalid')
        self.assert_highlight([2])
        xy_field(self.page, 'color')[1].forceActiveFocus()
        inspector_key(self.page, Qt.Key_Escape)
        self.assertEqual(xy_value(self.page, 'color'), self.curves[2].color)
        self.assertEqual(self.selected_ids(), {self.curves[2].curve_id})
        self.assert_highlight([2])
        quick_curve_list(self.page).forceActiveFocus(); inspector_key(self.page, Qt.Key_Escape)
        self.assertEqual(self.selected_ids(), set())
        self.assert_highlight([])


class AnalysisSelectionIsolationTests(QuickMigrationCase):
    setUp = analysis_fixture.XYAnalysisWorkflowTests.setUp
    dispose = analysis_fixture.XYAnalysisWorkflowTests.dispose
    analyze = analysis_fixture.XYAnalysisWorkflowTests.analyze
    fit = analysis_fixture.XYAnalysisWorkflowTests.fit

    def test_completed_analysis_survives_browsing_multiple_and_empty_selection(self):
        self.fit()
        original = self.panel.progress
        fit = self.panel.fit_result
        payload = deepcopy(self.page.comparison.analysis_state)
        self.w.save_project(self.folder / 'selection.spectra')
        digest = workspace_digest(self.w.workspace)
        self.page.axes.set_xlim(-1, 2)
        axes = self.page.axes
        for rows in ([1], [0, 1], [], [0]):
            select_xy_rows(self.page, rows)
            self.assertIs(self.panel.progress, original)
            self.assertIs(self.panel.fit_result, fit)
            self.assertEqual(self.page.comparison.analysis_state, payload)
            self.assertEqual(workspace_digest(self.w.workspace), digest)
            self.assertEqual(self.panel.bridge.state['fitEnabled'], rows == [0])
            self.assertFalse(self.w._background_jobs.busy)
            self.assertIs(self.page.axes, axes)
            self.assertEqual(axes.get_xlim(), (-1, 2))
            self.assertTrue(any(line.get_gid() == 'xy-analysis-fit' for line in axes.lines))
            self.w._refresh_project_modified()
            self.assertFalse(self.w.projectModified)
            overlay = next(line for line in axes.lines if line.get_gid() == 'xy-analysis-fit')
            self.assertEqual(overlay.get_alpha() or 1., .25 if rows == [1] else 1.)
        self.assertIn('RMSE', self.panel.bridge.state['status'])
        self.assertNotIn('to use its results', self.panel.bridge.state['status'])

    def test_mismatched_fit_is_rejected_and_explicit_find_binds_new_source(self):
        self.fit()
        original = self.panel.progress
        select_xy_rows(self.page, [1])
        self.panel.start('fit')
        self.assertFalse(self.w._background_jobs.busy)
        self.assertIs(self.panel.progress, original)
        self.assertIn(original.captured.provenance['curve'], self.panel.bridge.state['context'])
        analysis_command(self.panel, 'find')
        wait_for_background_jobs(self.w)
        self.assertNotEqual(self.panel.captured.curve_id, original.captured.curve_id)
        analysis_command(self.panel, 'fit')
        wait_for_background_jobs(self.w)
        np.testing.assert_allclose(self.panel.fit_result.centers, [-1], atol=1e-5)

    def test_browsing_does_not_hide_real_source_invalidation(self):
        self.fit()
        captured = self.panel.captured
        select_xy_rows(self.page, [1])
        self.w.workspace.sheets[captured.provenance['sheet_id']].data[:, 1] += 1
        self.page.render()
        self.assertIsNone(self.panel.fit_result)
        self.assertEqual(self.panel.captured.curve_id, captured.curve_id)
        self.assertFalse(self.panel.bridge.state['fitEnabled'])


if __name__ == '__main__':
    unittest.main()
