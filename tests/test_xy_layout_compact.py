"""Compact navigation must preserve drafts and keep overflow commands usable."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from copy import deepcopy
import numpy as np
from PySide6.QtCore import Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabWidget, QToolBar, QToolButton
from spectra_app.dataset_state import Workspace
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_plot_page import XYPlotPage
from spectra_app.xy_analysis import AnalysisSettings, XYAnalysisProgress, capture_input, collect_candidates
from tests.settings_helpers import isolate_settings, wait_for_background_jobs
import tests.test_xy_analysis_workflow as workflow_fixture

APP = QApplication.instance() or QApplication([])


class XYCompactLayoutTests(unittest.TestCase):
    def setUp(self):
        isolate_settings(self)
        self.workspace = Workspace()
        book = create_workbook(self.workspace, 'Experiment')
        sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Original',
            np.array([[0., 1.], [1., 2.], [2., 1.]]), ['Time', 'Signal'])
        self.graph = create_comparison(self.workspace, 'Comparison')
        self.curve = add_curve(self.workspace, self.graph.comparison_id, sheet.sheet_id, 0, 1)
        self.page = XYPlotPage(self.workspace, self.graph.comparison_id)
        self.page.resize(1000, 720)
        self.page.show()
        APP.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.page.command_bar.close()
        self.page.dispose()
        self.page.close()
        self.page.deleteLater()
        APP.clipboard().clear()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def test_first_analysis_tab_keeps_saved_progress_and_other_curve_draft(self):
        sheet = self.workspace.sheets[self.curve.sheet_id]
        x = np.linspace(-4, 4, 101)
        sheet.data = np.column_stack([x, 2 + np.exp(-.5 * (x / .5) ** 2)])
        other = add_curve(self.workspace, self.graph.comparison_id, sheet.sheet_id, 0, 1)
        other.label = 'Curve B'
        captured = capture_input(self.workspace, self.graph, self.curve.curve_id, AnalysisSettings())
        progress = XYAnalysisProgress(captured)
        progress.set_candidates(collect_candidates(captured.data, captured.settings))
        self.graph.analysis_state = progress.to_payload()
        saved = deepcopy(self.graph.analysis_state)
        self.page.refresh()
        self.page.curve_list.clearSelection()
        self.page.curve_list.setCurrentRow(1)
        self.page.label_edit.setFocus()
        self.page.label_edit.selectAll()
        QTest.keyClicks(self.page.label_edit, 'unfinished-B')
        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertEqual(other.label, 'Curve B')
        self.assertTrue(self.page.has_pending_project_edits())

        tabs = self.page.inspector_tabs
        QTest.mouseClick(tabs.tabBar(), Qt.LeftButton, pos=tabs.tabBar().tabRect(1).center())
        APP.processEvents()

        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertEqual(other.label, 'Curve B')
        self.assertEqual(self.page.label_edit.text(), 'unfinished-B')
        self.assertTrue(self.page.has_pending_project_edits())
        self.assertEqual(self.graph.analysis_state, saved)
        self.assertIn('Curve B', self.page.analysis_panel.context_label.text())
        self.assertIsNone(self.page.analysis_panel.peaks)
        self.assertFalse(self.page.analysis_panel.fit_btn.isEnabled())
        self.assertFalse(self.page.analysis_panel.save_btn.isEnabled())
        self.page.analysis_panel.model_combo.setCurrentText('Lorentzian')
        self.page.analysis_panel.preset_combo.setCurrentText('Loose')
        self.page.render()
        self.assertEqual(self.graph.analysis_state, saved)
        tabs.tabBar().setFocus()
        QTest.keyClick(tabs.tabBar(), Qt.Key_Right)
        APP.processEvents()
        self.assertEqual(tabs.currentIndex(), 2)
        QTest.keyClick(tabs.tabBar(), Qt.Key_Left)
        QTest.keyClick(tabs.tabBar(), Qt.Key_Left)
        APP.processEvents()
        self.assertEqual(tabs.currentIndex(), 0)
        self.assertEqual(self.page.label_edit.text(), 'unfinished-B')
        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertEqual(other.label, 'Curve B')
        self.assertTrue(self.page.has_pending_project_edits())
        self.assertEqual(self.graph.analysis_state, saved)
        QTest.mouseClick(tabs.tabBar(), Qt.LeftButton, pos=tabs.tabBar().tabRect(1).center())
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertEqual(self.page.label_edit.text(), 'unfinished-B')
        self.assertEqual(other.label, 'Curve B')
        self.assertTrue(self.page.has_pending_project_edits())
        self.assertEqual(self.graph.analysis_state, saved)

    def test_malformed_saved_source_is_deferred_safely_during_navigation(self):
        self.graph.analysis_state = {'version': 1, 'curve_id': []}
        try:
            self.page.analysis_panel.activate(restore_selection=False)
        except (ValueError, KeyError, TypeError) as exc:
            self.fail(f'Navigation must defer malformed saved progress safely: {exc}')
        self.assertEqual(self.graph.analysis_state, {'version': 1, 'curve_id': []})
        self.assertIsNone(self.page.analysis_panel.captured)
        self.page.analysis_panel.activate()
        self.assertIn('could not be restored', self.page.analysis_panel.status.text())

    def test_inspector_navigation_preserves_focused_drafts_and_selection(self):
        tabs = self.page.inspector.findChild(QTabWidget)
        self.assertIsNotNone(tabs, 'Curves and Analysis need direct navigation')
        indexes = {tabs.tabText(i): i for i in range(tabs.count())}
        original = self.curve.label
        self.page.label_edit.setFocus()
        self.page.label_edit.setText('unfinished legend')
        selected = self.page._selected_ids()
        QTest.mouseClick(tabs.tabBar(), Qt.LeftButton,
                         pos=tabs.tabBar().tabRect(indexes['Analysis']).center())
        APP.processEvents()
        self.assertTrue(self.page.analysis_panel.isVisible())
        self.assertEqual(self.curve.label, original)
        QTest.mouseClick(tabs.tabBar(), Qt.LeftButton,
                         pos=tabs.tabBar().tabRect(indexes['Curves']).center())
        APP.processEvents()
        self.assertEqual(self.page.label_edit.text(), 'unfinished legend')
        self.assertEqual(self.page._selected_ids(), selected)
        self.assertTrue(self.page.has_pending_project_edits())
        self.page.analysis_panel.activate()
        APP.processEvents()
        self.assertEqual(tabs.currentIndex(), indexes['Analysis'])
        self.assertTrue(self.page.analysis_panel.find_btn.isVisible())
        tabs.setCurrentIndex(indexes['Display'])
        self.page.x_range_combo.setCurrentIndex(self.page.x_range_combo.findData('manual'))
        self.assertFalse(self.page.x_min_edit.isHidden())
        self.page.x_range_combo.setCurrentIndex(self.page.x_range_combo.findData('all'))
        self.assertTrue(self.page.x_min_edit.isHidden())
        self.page.finish_editor()
        self.assertIn('unfinished legend', self.page.selection_label.text())

    def test_analysis_activation_and_keyboard_navigation_preserve_unfinished_width(self):
        tabs = self.page.inspector_tabs
        self.page.width_spin.setFocus()
        self.page.width_spin.lineEdit().setText('2.1234567')
        self.page.analysis_panel.activate()
        APP.processEvents()
        self.assertEqual(self.page.width_spin.lineEdit().text(), '2.1234567')
        self.assertEqual(self.curve.width, 1.5)
        tabs.tabBar().setFocus()
        QTest.keyClick(tabs.tabBar(), Qt.Key_Left)
        APP.processEvents()
        self.assertTrue(self.page.curve_list.isVisible())
        self.assertEqual(self.page.width_spin.lineEdit().text(), '2.1234567')

    def test_narrow_toolbar_overflow_applies_style_and_tracks_disabled_selection(self):
        bar = self.page.command_bar
        self.assertIsInstance(bar, QToolBar, 'Narrow graphs need native overflow commands')
        bar.resize(225, 45)
        bar.show()
        APP.processEvents()
        extension = bar.findChild(QToolButton, 'qt_toolbar_ext_button')
        self.assertTrue(extension.isVisible())
        action = self.page.style_buttons['line_symbol'].defaultAction()
        self.assertIn(action, extension.menu().actions())
        action.trigger()
        self.assertEqual(self.curve.style, 'line_symbol')
        self.page.curve_list.clearSelection()
        self.assertFalse(action.isEnabled())
        self.assertFalse(self.page.style_buttons['line_symbol'].isEnabled())
        self.page.curve_list.item(0).setSelected(True)
        self.assertTrue(action.isEnabled())
        self.page.style_buttons['scatter'].click()
        self.assertEqual(self.curve.style, 'scatter')


class XYCompactAnalysisNavigationTests(unittest.TestCase):
    setUp = workflow_fixture.XYAnalysisWorkflowTests.setUp
    analyze = workflow_fixture.XYAnalysisWorkflowTests.analyze
    fit = workflow_fixture.XYAnalysisWorkflowTests.fit

    def dispose(self):
        APP.clipboard().clear()
        workflow_fixture.XYAnalysisWorkflowTests.dispose(self)

    def reopen_saved_progress_with_other_selection(self):
        self.fit()
        self.saved = deepcopy(self.page.comparison.analysis_state)
        comparison_id = self.page.comparison_id
        target = self.folder / 'navigation-progress.spectra'
        self.w.save_project(target)
        self.assertTrue(self.w.open_project(target))
        self.w.open_xy_comparison(comparison_id)
        self.page = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        self.panel = self.page.analysis_panel
        self.page.curve_list.clearSelection()
        self.page.curve_list.setCurrentRow(1)
        self.other = list(self.page.comparison.curves.values())[1]
        self.page.label_edit.setFocus()
        self.page.label_edit.selectAll()
        QTest.keyClicks(self.page.label_edit, 'unfinished-B')
        tabs = self.page.inspector_tabs
        QTest.mouseClick(tabs.tabBar(), Qt.LeftButton, pos=tabs.tabBar().tabRect(1).center())
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {self.other.curve_id})
        self.assertEqual(self.page.comparison.analysis_state, self.saved)
        self.assertIsNone(self.panel.fit_result)
        self.assertTrue(self.panel.find_btn.isEnabled())
        self.assertFalse(self.panel.save_btn.isEnabled())

    def test_explicit_resume_restores_saved_curve_after_navigation(self):
        self.reopen_saved_progress_with_other_selection()
        self.page.resume_analysis_action.trigger()
        APP.processEvents()
        self.assertEqual(self.other.label, 'unfinished-B')
        self.assertFalse(self.page.has_pending_project_edits())
        self.assertEqual(self.page._selected_ids(), {self.saved['curve_id']})
        self.assertEqual(self.panel.captured.curve_id, self.saved['curve_id'])
        np.testing.assert_allclose(self.panel.fit_result.centers, [.7], atol=1e-5)
        self.assertIn('Signal', self.panel.context_label.text())
        self.assertIn('Restored', self.panel.status.text())

        tabs = self.page.inspector_tabs
        QTest.mouseClick(tabs.tabBar(), Qt.LeftButton, pos=tabs.tabBar().tabRect(0).center())
        APP.processEvents()
        QTest.mouseClick(self.page.curve_list.viewport(), Qt.LeftButton,
                         pos=self.page.curve_list.visualItemRect(self.page.curve_list.item(1)).center())
        QTest.mouseClick(tabs.tabBar(), Qt.LeftButton, pos=tabs.tabBar().tabRect(1).center())
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {self.other.curve_id})
        # Browsing another curve retains the restored analysis until Find is explicit.
        self.assertEqual(self.panel.captured.curve_id, self.saved['curve_id'])
        self.assertIn('Reference', self.panel.context_label.text())
        np.testing.assert_allclose(self.panel.fit_result.centers, [.7], atol=1e-5)
        self.assertEqual(self.page.comparison.analysis_state, self.saved)
        self.assertFalse(self.panel.fit_btn.isEnabled())
        self.assertFalse(self.panel.save_btn.isEnabled())

    def test_find_after_navigation_replaces_saved_progress_with_selected_curve(self):
        self.reopen_saved_progress_with_other_selection()
        self.panel.find_btn.click()
        wait_for_background_jobs(self.w)
        self.assertEqual(self.page._selected_ids(), {self.other.curve_id})
        self.assertEqual(self.other.label, 'unfinished-B')
        self.assertEqual(self.panel.captured.curve_id, self.other.curve_id)
        self.assertEqual(self.page.comparison.analysis_state['curve_id'], self.other.curve_id)
        self.assertIn('Reference', self.panel.context_label.text())
        self.assertEqual(len(self.panel.peaks.indices), 1)
        self.panel.fit_btn.click()
        wait_for_background_jobs(self.w)
        np.testing.assert_allclose(self.panel.fit_result.centers, [-1], atol=1e-5)

    def test_first_analysis_tab_without_saved_progress_can_find_current_curve(self):
        self.page.curve_list.clearSelection()
        self.page.curve_list.setCurrentRow(1)
        other = list(self.page.comparison.curves.values())[1]
        tabs = self.page.inspector_tabs
        QTest.mouseClick(tabs.tabBar(), Qt.LeftButton, pos=tabs.tabBar().tabRect(1).center())
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertTrue(self.panel.find_btn.isEnabled())
        self.panel.find_btn.click()
        wait_for_background_jobs(self.w)
        self.assertEqual(self.panel.captured.curve_id, other.curve_id)
        self.assertEqual(len(self.panel.peaks.indices), 1)
        self.assertIn('Reference', self.panel.context_label.text())


if __name__ == '__main__':
    unittest.main()
