"""Document-local visual undo and explicit cancellation of invalid drafts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from copy import deepcopy

import numpy as np
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtTest import QTest


from spectra_app.dataset_state import Workspace
from spectra_app.project_io import workspace_digest, write_project, read_project
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve, remove_sheet
from spectra_app.xy_plot_controller import XYPlotController
from tests.quick_migration_case import QuickMigrationCase
from tests import test_xy_analysis_workflow as workflow_fixture
from tests import formal_quick_helpers as formal

from tests.xy_helpers import set_xy_draft, xy_value, select_xy_rows, xy_field
from tests.xy_canvas_quick_helpers import command as canvas_command, key as canvas_key
from tests.xy_analysis_quick_helpers import analysis_command

APP = QCoreApplication

def quick_curve_list(page):
    from tests.xy_helpers import inspector_host
    return formal.ensure_visible(formal.item(inspector_host(page), 'xyCurveList'))


def inspector_key(page, key):
    QTest.keyClick(page.parent().root_window, key)
    formal.pump()


class XYEditRecoveryTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.workspace = self.controller.workspace
        book = create_workbook(self.workspace)
        self.sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Source',
            np.column_stack((np.arange(10.), np.arange(10.) ** 2)), ['X', 'Y'])
        self.graph = create_comparison(self.workspace)
        self.curves = [add_curve(self.workspace, self.graph.comparison_id, self.sheet.sheet_id, 0, 1,
                                 label=f'Curve {i}') for i in range(3)]
        self.page = self.controller.open_xy_comparison(self.graph.comparison_id)
        select_xy_rows(self.page, [0])
        self.addCleanup(self.dispose)
        self.host.window.resize(1000, 720)
        self.host.show()
        APP.processEvents()

    def dispose(self):
        self.assertEqual(self.quick_errors, [])

    def test_bulk_style_reorder_remove_undo_redo_keep_identity_and_source(self):
        page, graph = self.page, self.graph
        data = self.sheet.data
        initial = workspace_digest(self.workspace)
        notifications = []
        page.historyChanged.connect(lambda: notifications.append(True))
        self.assertFalse(page.can_undo())
        page._apply_batch_style({c.curve_id for c in self.curves}, 'color', '#abcdef')
        page._set_curve_order(list(graph.curves)[::-1])
        page._remove_selected()
        removed = self.curves[0]
        self.assertNotIn(removed.curve_id, graph.curves)
        self.assertTrue(page.undo())
        self.assertIs(graph.curves[removed.curve_id], removed)
        self.assertTrue(page.undo())
        self.assertEqual(list(graph.curves), [c.curve_id for c in self.curves])
        self.assertTrue(page.undo())
        self.assertEqual(workspace_digest(self.workspace), initial)
        self.assertIs(self.sheet.data, data)
        self.assertFalse(page.can_undo())
        self.assertTrue(page.can_redo())
        self.assertTrue(page.redo())
        self.assertEqual([c.color for c in self.curves], ['#abcdef'] * 3)
        self.assertGreaterEqual(len(notifications), 7)
        page._apply_batch_style({removed.curve_id}, 'width', 4.)
        self.assertFalse(page.can_redo())

    def test_graph_curve_display_reset_and_reopen_history(self):
        page = self.page
        set_xy_draft(page, 'plot_title', 'A title')
        page.inspector_bridge.commitField('plot_title')
        set_xy_draft(page, 'color', '#123456')
        page.inspector_bridge.commitField('color')
        set_xy_draft(page, 'stack_step', '3')
        page.inspector_bridge.commitField('stack_step')
        page._reset_display()
        self.assertEqual(self.graph.stack_step, 0.)
        self.assertTrue(page.undo())
        self.assertEqual(self.graph.stack_step, 3.)
        self.assertTrue(page.undo())
        self.assertEqual(self.graph.stack_step, 0.)
        self.assertTrue(page.undo())
        self.assertEqual(self.curves[0].color, '#1f77b4')
        other = XYPlotController(self.workspace, self.graph.comparison_id)
        try:
            self.assertTrue(other.can_undo())
            self.assertTrue(other.undo())
            self.assertEqual(self.graph.plot_title, '')
        finally:
            other.dispose(); other.deleteLater()
        # Runtime history is deliberately absent from the project format.
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'history.spectra'
            write_project(path, self.workspace)
            restored = read_project(path)
            self.assertFalse(hasattr(restored.comparisons[self.graph.comparison_id], '_edit_history'))

    def test_invalid_color_escape_and_visible_revert_restore_committed_values(self):
        page = self.page
        self.host.window.resize(1000, 700); self.host.show(); APP.processEvents()
        before = workspace_digest(self.workspace)
        xy_field(page, 'color')[1].forceActiveFocus()
        set_xy_draft(page, 'color', 'invalid color')
        set_xy_draft(page, 'name', 'unfinished name')
        page.inspector_bridge.commitField('color')
        self.assertIn('color', page.inspector_bridge.message.lower())
        with self.assertRaises(ValueError):
            page.finish_editor()
        select_xy_rows(page, [1])
        self.assertEqual(list(page.comparison.curves).index(page._active_curve_id), 0)
        inspector_key(page, Qt.Key_Escape)
        self.assertEqual(xy_value(page, 'color'), self.curves[0].color)
        self.assertEqual(xy_value(page, 'name'), self.graph.name)
        self.assertFalse(page.has_pending_project_edits())
        self.assertEqual(workspace_digest(self.workspace), before)
        self.assertFalse(page.can_undo())
        select_xy_rows(page, [1])
        self.assertEqual(list(page.comparison.curves).index(page._active_curve_id), 1)
        set_xy_draft(page, 'y_offset', 'nan')
        page.inspector_bridge.revert()
        self.assertFalse(page.has_pending_project_edits())
        self.assertEqual(workspace_digest(self.workspace), before)

    def test_undo_cannot_resurrect_curve_after_source_sheet_deletion(self):
        self.page._remove_selected()
        remove_sheet(self.workspace, self.sheet.sheet_id)
        self.page.refresh()
        self.assertFalse(self.page.can_undo())
        self.assertFalse(self.page.undo())
        self.assertEqual(self.graph.curves, {})

    def test_detached_save_snapshot_omits_history_without_changing_live_document(self):
        self.page._apply_batch_style({self.curves[0].curve_id}, 'width', 3.)
        snapshot = deepcopy(self.workspace)
        self.assertTrue(self.page.can_undo())
        history = snapshot.comparisons[self.graph.comparison_id]._edit_history
        self.assertEqual(history.undo_stack, [])
        self.assertEqual(history.redo_stack, [])

    def test_visible_revert_cancels_valid_focus_draft_without_committing_it(self):
        page = self.page
        self.host.window.resize(1000, 700); self.host.show(); APP.processEvents()
        before = workspace_digest(self.workspace)
        xy_field(page, 'label')[1].forceActiveFocus()
        set_xy_draft(page, 'label', 'Pending valid label')
        formal.click_item(self.controller, 'xyRevertEdits')
        self.assertEqual(xy_value(page, 'label'), self.curves[0].label)
        self.assertEqual(workspace_digest(self.workspace), before)
        self.assertFalse(page.can_undo())

    def test_delete_keys_remove_only_with_valid_drafts_and_editor_delete_is_text(self):
        page = self.page
        self.host.window.resize(1000, 700); self.host.show(); APP.processEvents()
        curve_id = self.curves[0].curve_id
        set_xy_draft(page, 'color', 'invalid')
        quick_curve_list(page).forceActiveFocus(); inspector_key(page, Qt.Key_Delete)
        canvas_key(page, Qt.Key_Delete)
        self.assertIn(curve_id, self.graph.curves)
        xy_field(page, 'color')[1].forceActiveFocus()
        inspector_key(page, Qt.Key_Escape)
        xy_field(page, 'label')[1].forceActiveFocus(); xy_field(page, 'label')[1].selectAll()
        inspector_key(page, Qt.Key_Delete)
        self.assertEqual(xy_value(page, 'label'), '')
        self.assertIn(curve_id, self.graph.curves)
        page.revert_editor()
        canvas_key(page, Qt.Key_Delete)
        self.assertNotIn(curve_id, self.graph.curves)
        self.assertTrue(page.undo())
        quick_curve_list(page).forceActiveFocus(); inspector_key(page, Qt.Key_Delete)
        self.assertNotIn(curve_id, self.graph.curves)


class XYAnalysisRecoveryTests(QuickMigrationCase):
    setUp = workflow_fixture.XYAnalysisWorkflowTests.setUp
    dispose = workflow_fixture.XYAnalysisWorkflowTests.dispose
    fit = workflow_fixture.XYAnalysisWorkflowTests.fit
    analyze = workflow_fixture.XYAnalysisWorkflowTests.analyze

    def test_remove_undo_retains_fit_by_reference_and_rechecks_source_before_restore(self):
        self.fit()
        curve_id = self.panel.captured.curve_id
        curve = self.page.comparison.curves[curve_id]
        fit = self.panel.fit_result
        captured = self.panel.captured
        self.page._remove_selected()
        self.assertNotIn(curve_id, self.page.comparison.curves)
        self.assertIsNone(self.panel.fit_result)
        self.assertTrue(self.page.undo())
        self.assertIs(self.page.comparison.curves[curve_id], curve)
        self.assertIs(self.panel.fit_result, fit)
        self.assertIs(self.panel.captured.data, captured.data)
        self.assertIn('xy-analysis-fit', [line.get_gid() for line in self.page.axes.lines])
        self.assertTrue(self.page.redo())
        sheet = self.w.workspace.sheets[curve.sheet_id]
        sheet.data[:, 1] += .1
        self.assertTrue(self.page.undo())
        self.assertIsNone(self.panel.fit_result)

    def test_style_edits_keep_fit_and_overlay_artists_without_repreparing_scientific_data(self):
        from unittest.mock import patch
        import traceback
        from spectra_app.comparison_display import prepare_comparison
        self.fit()
        page = self.page
        curve_id = self.panel.captured.curve_id
        fit = self.panel.fit_result
        overlay = next(line for line in page.axes.lines if line.get_gid() == 'xy-analysis-fit')
        prepared = []
        def record(*args):
            prepared.append(''.join(traceback.format_stack(limit=20)))
            return prepare_comparison(*args)
        with patch('spectra_app.xy_analysis.prepare_comparison', side_effect=record):
            page._apply_batch_style({curve_id}, 'color', '#abcdef')
            self.assertTrue(page.undo())
            set_xy_draft(page, 'width', 4., commit=True)
        self.assertEqual(len(prepared), 0, '\n'.join(prepared))
        self.assertIs(self.panel.fit_result, fit)
        self.assertIs(next(line for line in page.axes.lines if line.get_gid() == 'xy-analysis-fit'), overlay)

    def test_undo_removal_does_not_replace_newer_explicit_analysis(self):
        from tests.settings_helpers import wait_for_background_jobs
        self.fit()
        removed_id = self.panel.captured.curve_id
        self.page._remove_selected()
        canvas_command(self.page, 'analyze')
        analysis_command(self.panel, 'find'); wait_for_background_jobs(self.w)
        analysis_command(self.panel, 'fit'); wait_for_background_jobs(self.w)
        newer_fit = self.panel.fit_result
        newer_input = self.panel.captured
        newer_payload = self.page.comparison.analysis_state
        self.assertIsNotNone(newer_fit)
        self.assertNotEqual(newer_input.curve_id, removed_id)
        np.testing.assert_allclose(newer_fit.centers, [-1.], atol=1.e-5)
        self.assertTrue(self.page.undo())
        self.assertIn(removed_id, self.page.comparison.curves)
        self.assertIs(self.panel.fit_result, newer_fit)
        self.assertIs(self.panel.captured, newer_input)
        self.assertIs(self.page.comparison.analysis_state, newer_payload)
        self.assertTrue(self.page.redo())
        self.assertTrue(self.page.undo())
        self.assertIs(self.panel.fit_result, newer_fit)
        self.assertIs(self.page.comparison.analysis_state, newer_payload)


if __name__ == '__main__':
    unittest.main()
