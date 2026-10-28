"""Owner-safe analysis commands and scientific drafts through real Qt controls."""
from copy import deepcopy
import unittest

from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtTest import QTest
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as formal
from unittest.mock import patch
import tests.test_xy_analysis_workflow as workflow_fixture
from tests.settings_helpers import wait_for_background_jobs

from tests.xy_analysis_quick_helpers import analysis_command, choose_analysis, type_analysis_field as _type_analysis_field
from tests.xy_helpers import set_xy_draft, xy_value

from tests.test_xy_layout_compact import click_curve_row, task, keyboard_task

APP = QCoreApplication


def type_analysis_field(panel, field, text):
    window, control = _type_analysis_field(panel, field, text)
    assert control.isVisible(), field
    ancestor, viewport_found = control.parentItem(), False
    while ancestor is not None:
        if ancestor.metaObject().indexOfProperty('contentY') >= 0:
            top = control.mapToItem(ancestor, QPointF())
            assert top.x() >= -.5 and top.y() >= -.5, field
            assert top.x() + control.width() <= ancestor.width() + .5, field
            assert top.y() + control.height() <= ancestor.height() + .5, field
            viewport_found = True
        ancestor = ancestor.parentItem()
    assert viewport_found, 'Analysis editor must be inside its real scroll viewport: ' + field
    if text == '':
        # An empty input-method commit leaves the selection intact in Qt Quick.
        # Delete through the same actual editor/window input route as a user.
        QTest.keyClick(window, Qt.Key_Backspace)
        formal.pump()
    return window, control


class XYUsabilityTests(QuickMigrationCase):
    setUp = workflow_fixture.XYAnalysisWorkflowTests.setUp
    analyze = workflow_fixture.XYAnalysisWorkflowTests.analyze
    fit = workflow_fixture.XYAnalysisWorkflowTests.fit

    def dispose(self):
        workflow_fixture.XYAnalysisWorkflowTests.dispose(self)

    def select_other(self):
        task(self, 0)
        APP.processEvents()
        click_curve_row(self.page, 1)
        task(self, 1)
        APP.processEvents()
        return list(self.page.comparison.curves)[1]

    def test_browsing_other_curve_cannot_mutate_retained_analysis_settings(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        fit = self.panel.fit_result
        self.select_other()
        self.assertFalse(self.panel.bridge.state['editable'])
        # Slots are owner checked even when a non-mouse caller changes a control.
        self.assertFalse(choose_analysis(self.panel, 'preset', 'Strict'))
        self.assertFalse(choose_analysis(self.panel, 'model', 'Lorentzian'))
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertIs(self.panel.fit_result, fit)
        self.assertIn('Resume', self.panel.bridge.state['resumeText'])
        self.assertIn('Start analysis', self.panel.bridge.state['startText'])
        analysis_command(self.panel, 'resume')
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {saved['curve_id']})
        self.assertEqual(self.panel.bridge.values['model'], 'Gaussian')
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertIs(self.panel.fit_result, fit)

    def test_all_numeric_intermediate_drafts_preserve_fit_and_payload(self):
        self.analyze()
        self.assertTrue(choose_analysis(self.panel, 'detector', 'D2'))
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        fit = self.panel.fit_result
        analysis_command(self.panel, 'settings')
        APP.processEvents()
        fields = ('prominence', 'noise', 'distance', 'candidateWidthMin', 'candidateWidthMax',
                  'maxCount', 'widthMin', 'widthMax', 'sgWindow', 'sgOrder')
        for field in fields:
            with self.subTest(editor=field):
                before = self.panel.bridge.drafts[field]
                window, edit = type_analysis_field(self.panel, field, '')
                self.assertEqual(edit.property('text'), '')
                self.assertIs(self.panel.fit_result, fit)
                self.assertEqual(self.page.comparison.analysis_state, saved)
                self.assertFalse(self.panel.bridge.state['findEnabled'])
                self.assertFalse(self.panel.bridge.state['fitEnabled'])
                self.assertFalse(self.panel.bridge.state['saveEnabled'])
                self.assertTrue(self.panel.bridge.state['error'])
                type_analysis_field(self.panel, field, before)
                QTest.keyClick(window, Qt.Key_Return)
                APP.processEvents()
                self.assertIs(self.panel.fit_result, fit)
                self.assertEqual(self.page.comparison.analysis_state, saved)

    def test_valid_width_draft_commits_once_and_status_drops_previous_rmse(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        fit = self.panel.fit_result
        analysis_command(self.panel, 'settings')
        window, edit = type_analysis_field(self.panel, 'widthMin', '0.1')
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertIs(self.panel.fit_result, fit)
        QTest.keyClick(window, Qt.Key_Return)
        APP.processEvents()
        self.assertIsNone(self.panel.fit_result)
        self.assertEqual(self.panel.captured.settings.width_min, .1)
        self.assertEqual(self.panel.progress.candidate_pool[0].position, saved['candidate_pool'][0]['position'])
        self.assertNotIn('RMSE', self.panel.bridge.state['status'])

    def test_invalid_source_still_retires_fit_instead_of_becoming_an_editor_draft(self):
        self.fit()
        original_fit = self.panel.fit_result
        type_analysis_field(self.panel, 'widthMin', '-')
        self.assertIs(self.panel.fit_result, original_fit)
        source = self.w.workspace.sheets[self.panel.captured.provenance['sheet_id']]
        source.data[0, 0] = source.data[1, 0]
        self.page.render()
        self.assertEqual(self.panel.bridge.drafts['widthMin'], '-')
        self.assertIsNone(self.panel.fit_result)
        self.assertFalse(self.panel.bridge.state['saveEnabled'])
        self.assertNotIn('RMSE', self.panel.bridge.state['status'])

    def test_numeric_draft_survives_navigation_and_resume_rejects_invalid_curve_draft(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        analysis_command(self.panel, 'settings')
        window, edit = type_analysis_field(self.panel, 'widthMin', '')
        self.panel.details_dialog.window().hide()
        keyboard_task(self, 2)
        APP.processEvents()
        self.assertEqual(edit.property('text'), '')
        self.assertEqual(self.page.comparison.analysis_state, saved)
        task(self, 1)
        analysis_command(self.panel, 'settings')
        APP.processEvents()
        self.assertEqual(edit.property('text'), '')
        window, edit = type_analysis_field(self.panel, 'widthMin', '0.000000001')
        QTest.keyClick(window, Qt.Key_Return)
        self.panel.details_dialog.window().hide()
        other = self.select_other()
        set_xy_draft(self.page, 'color', 'invalid color')
        self.page.resume_analysis_action.trigger()
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {other})
        self.assertEqual(xy_value(self.page, 'color'), 'invalid color')
        self.assertEqual(self.page.comparison.analysis_state, saved)

    def test_common_interval_tracks_visible_curves_and_navigation_uses_real_data_bounds(self):
        from spectra_app.workbook_state import register_xy_sheet, add_curve, create_workbook
        import numpy as np
        book = create_workbook(self.w.workspace)
        sheet = register_xy_sheet(self.w.workspace, book.workbook_id, 'Narrow reference',
            np.array([[-2., 0], [-1, 1], [0, 2], [1, 1], [2, 0]]), ['X', 'Y'])
        narrow = add_curve(self.w.workspace, self.page.comparison_id, sheet.sheet_id, 0, 1)
        self.page.refresh()
        self.analyze()
        analysis_command(self.panel, 'interval')
        self.page.inspector_bridge.setRangeMode('common')
        APP.processEvents()
        self.assertEqual(self.page.axes._comparison_display.x_limits, (-2, 2))
        self.assertIn('Common overlap of visible curves: -2 to 2', self.page.inspector_bridge.displayStatus)
        task(self, 1)
        self.page.axes.set_xlim(.6, .8)
        analysis_command(self.panel, 'find')
        wait_for_background_jobs(self.w)
        self.assertEqual(self.panel.captured.provenance['interval'], (-2, 2))
        self.assertIn('-2 to 2', self.panel.bridge.state['context'])
        narrow.visible = False
        self.page.render()
        self.assertEqual(self.page.axes._comparison_display.x_limits, (-6, 6))
        self.assertIn('-6 to 6', self.panel.bridge.state['context'])
        self.assertEqual(self.panel.captured.provenance['interval'], (-6, 6))

    def test_analyze_selected_starts_other_curve_and_resume_restores_saved_curve(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        comparison_id = self.page.comparison_id
        target = self.folder / 'resume-usability.spectra'
        self.w.save_project(target)
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Clean saved project unexpectedly requested discard')):
            self.assertTrue(self.w.open_project(target))
        self.w.open_xy_comparison(comparison_id)
        self.page = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.panel = self.page.analysis_controller
        self.assertTrue(self.page.resume_analysis_action.isEnabled())
        other = self.select_other()
        self.assertEqual(self.page.comparison.analysis_state, saved)
        analysis_command(self.panel, 'resume')
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {saved['curve_id']})
        self.assertIsNotNone(self.panel.fit_result)
        self.select_other()
        self.page.analyze_action.trigger()
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {other})
        self.assertEqual(self.panel.captured.curve_id, other)
        self.assertIsNone(self.panel.fit_result)
        analysis_command(self.panel, 'find')
        wait_for_background_jobs(self.w)
        self.assertEqual(self.panel.captured.curve_id, other)
        self.assertEqual(len(self.panel.peaks.indices), 1)

    def test_edit_interval_navigation_preserves_unrelated_drafts(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        set_xy_draft(self.page, 'stack_step', 'unfinished offset')
        analysis_command(self.panel, 'interval')
        APP.processEvents()
        self.assertEqual(self.controller.taskIndex, 2)
        self.assertTrue(formal.item(self.controller, 'xyChoice_x_range_mode').hasActiveFocus())
        self.assertEqual(xy_value(self.page, 'stack_step'), 'unfinished offset')
        self.assertEqual(self.page.comparison.analysis_state, saved)
        choices = formal.item(self.controller, 'xyChoice_x_range_mode').property('model')
        if hasattr(choices, 'toVariant'):
            choices = choices.toVariant()
        self.assertIn('Common overlap', [choice['label'] for choice in choices])


if __name__ == '__main__':
    unittest.main()
