"""Owner-safe analysis commands and scientific drafts through real Qt controls."""
from copy import deepcopy
import unittest

from PySide6.QtCore import Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import tests.test_xy_analysis_workflow as workflow_fixture
from tests.settings_helpers import wait_for_background_jobs

APP = QApplication.instance() or QApplication([])


class XYUsabilityTests(unittest.TestCase):
    setUp = workflow_fixture.XYAnalysisWorkflowTests.setUp
    analyze = workflow_fixture.XYAnalysisWorkflowTests.analyze
    fit = workflow_fixture.XYAnalysisWorkflowTests.fit

    def dispose(self):
        APP.clipboard().clear()
        workflow_fixture.XYAnalysisWorkflowTests.dispose(self)

    def select_other(self):
        self.page.inspector_tabs.setCurrentIndex(0)
        APP.processEvents()
        QTest.mouseClick(self.page.curve_list.viewport(), Qt.LeftButton,
                        pos=self.page.curve_list.visualItemRect(self.page.curve_list.item(1)).center())
        self.page.inspector_tabs.setCurrentIndex(1)
        APP.processEvents()
        return self.page.curve_list.item(1).data(Qt.UserRole)

    def test_browsing_other_curve_cannot_mutate_retained_analysis_settings(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        fit = self.panel.fit_result
        self.select_other()
        self.assertFalse(self.panel.model_combo.isEnabled())
        self.assertFalse(self.panel.preset_combo.isEnabled())
        # Slots are owner checked even when a non-mouse caller changes a control.
        self.panel.preset_combo.setCurrentText('Strict')
        self.panel.model_combo.setCurrentText('Lorentzian')
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertIs(self.panel.fit_result, fit)
        self.assertIn('Resume', self.panel.resume_btn.text())
        self.assertIn('Start analysis', self.panel.start_selected_btn.text())
        self.panel.resume_btn.click()
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {saved['curve_id']})
        self.assertEqual(self.panel.model_combo.currentText(), 'Gaussian')
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertIs(self.panel.fit_result, fit)

    def test_all_numeric_intermediate_drafts_preserve_fit_and_payload(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        fit = self.panel.fit_result
        self.panel.details_btn.click()
        APP.processEvents()
        spins = (*self.panel._filter_spins(), self.panel.width_min_spin,
                 self.panel.width_max_spin, self.panel.sg_window_spin, self.panel.sg_order_spin)
        for spin in spins:
            with self.subTest(editor=spin.accessibleName()):
                edit = spin.lineEdit()
                before = edit.text()
                edit.setFocus()
                QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
                QTest.keyClick(edit, Qt.Key_Backspace)
                APP.processEvents()
                self.assertIs(self.panel.fit_result, fit)
                self.assertEqual(self.page.comparison.analysis_state, saved)
                self.assertFalse(self.panel.find_btn.isEnabled())
                self.assertFalse(self.panel.fit_btn.isEnabled())
                self.assertFalse(self.panel.save_btn.isEnabled())
                self.assertTrue(self.panel.settings_error.text())
                edit.setText(before)
                QTest.keyClick(edit, Qt.Key_Return)
                APP.processEvents()
                self.assertIs(self.panel.fit_result, fit)
                self.assertEqual(self.page.comparison.analysis_state, saved)

    def test_valid_width_draft_commits_once_and_status_drops_previous_rmse(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        fit = self.panel.fit_result
        self.panel.details_btn.click()
        edit = self.panel.width_min_spin.lineEdit()
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(edit, '0.1')
        APP.processEvents()
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertIs(self.panel.fit_result, fit)
        QTest.keyClick(edit, Qt.Key_Return)
        APP.processEvents()
        self.assertIsNone(self.panel.fit_result)
        self.assertEqual(self.panel.captured.settings.width_min, .1)
        self.assertEqual(self.panel.progress.candidate_pool[0].position, saved['candidate_pool'][0]['position'])
        self.assertNotIn('RMSE', self.panel.status.text())

    def test_invalid_source_still_retires_fit_instead_of_becoming_an_editor_draft(self):
        self.fit()
        source = self.w.workspace.sheets[self.panel.captured.provenance['sheet_id']]
        source.data[0, 0] = source.data[1, 0]
        self.page.render()
        self.assertIsNone(self.panel.fit_result)
        self.assertFalse(self.panel.save_btn.isEnabled())
        self.assertNotIn('RMSE', self.panel.status.text())

    def test_numeric_draft_survives_navigation_and_resume_rejects_invalid_curve_draft(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        self.panel.details_btn.click()
        edit = self.panel.width_min_spin.lineEdit()
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(edit, Qt.Key_Backspace)
        self.panel.details_dialog.hide()
        self.page.inspector_tabs.tabBar().setFocus()
        QTest.keyClick(self.page.inspector_tabs.tabBar(), Qt.Key_Right)
        APP.processEvents()
        self.assertEqual(edit.text(), '')
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.page.inspector_tabs.setCurrentIndex(1)
        self.panel.details_btn.click()
        APP.processEvents()
        self.assertEqual(edit.text(), '')
        edit.setText('0.000000001')
        QTest.keyClick(edit, Qt.Key_Return)
        self.panel.details_dialog.hide()
        other = self.select_other()
        self.page.color_edit.setText('invalid color')
        self.page.resume_analysis_action.trigger()
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {other})
        self.assertEqual(self.page.color_edit.text(), 'invalid color')
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
        self.panel.edit_interval_btn.click()
        self.page.x_range_combo.setCurrentIndex(self.page.x_range_combo.findData('common'))
        APP.processEvents()
        self.assertEqual(self.page.axes._comparison_display.x_limits, (-2, 2))
        self.assertIn('Common overlap of visible curves: -2 to 2', self.page.display_status.text())
        self.page.inspector_tabs.setCurrentIndex(1)
        self.page.axes.set_xlim(.6, .8)
        self.panel.find_btn.click()
        wait_for_background_jobs(self.w)
        self.assertEqual(self.panel.captured.provenance['interval'], (-2, 2))
        self.assertIn('-2 to 2', self.panel.context_label.text())
        narrow.visible = False
        self.page.render()
        self.assertEqual(self.page.axes._comparison_display.x_limits, (-6, 6))
        self.assertIn('-6 to 6', self.panel.context_label.text())
        self.assertEqual(self.panel.captured.provenance['interval'], (-6, 6))

    def test_analyze_selected_starts_other_curve_and_resume_restores_saved_curve(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        comparison_id = self.page.comparison_id
        target = self.folder / 'resume-usability.spectra'
        self.w.save_project(target)
        self.assertTrue(self.w.open_project(target))
        self.w.open_xy_comparison(comparison_id)
        self.page = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        self.panel = self.page.analysis_panel
        self.assertTrue(self.page.resume_analysis_action.isEnabled())
        other = self.select_other()
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.panel.resume_btn.click()
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {saved['curve_id']})
        self.assertIsNotNone(self.panel.fit_result)
        self.select_other()
        self.page.analyze_action.trigger()
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {other})
        self.assertEqual(self.panel.captured.curve_id, other)
        self.assertIsNone(self.panel.fit_result)
        self.panel.find_btn.click()
        wait_for_background_jobs(self.w)
        self.assertEqual(self.panel.captured.curve_id, other)
        self.assertEqual(len(self.panel.peaks.indices), 1)

    def test_edit_interval_navigation_preserves_unrelated_drafts(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        self.page.stack_step_edit.setText('unfinished offset')
        self.panel.edit_interval_btn.click()
        APP.processEvents()
        self.assertEqual(self.page.inspector_tabs.currentIndex(), 2)
        self.assertTrue(self.page.x_range_combo.hasFocus())
        self.assertEqual(self.page.stack_step_edit.text(), 'unfinished offset')
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertIn('visible curves', self.page.x_range_combo.itemText(self.page.x_range_combo.findData('common')))


if __name__ == '__main__':
    unittest.main()
