"""Compact navigation must preserve drafts and keep overflow commands usable."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from copy import deepcopy
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QCoreApplication, Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickItem
from PySide6.QtQml import QQmlProperty, qmlContext
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as formal
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_plot_controller import XYPlotController
from spectra_app.xy_analysis import AnalysisSettings, XYAnalysisProgress, capture_input, collect_candidates
from tests.settings_helpers import wait_for_background_jobs
import tests.test_xy_analysis_workflow as workflow_fixture

from tests.xy_analysis_quick_helpers import analysis_command, choose_analysis
from tests.xy_helpers import set_xy_draft, xy_value, select_xy_rows, xy_field, type_xy_field
from tests import xy_canvas_quick_helpers as canvas_quick

APP = QCoreApplication


def mount_comparison(case):
    case.controller.open_xy_comparison(case.graph.comparison_id)
    documents = case.controller.graph_documents
    case.page = documents.document_for(documents.registry.active_key)
    case.assertIsInstance(case.page, XYPlotController)
    case.host.window.resize(1040, 850)
    formal.pump(100)
    case.assertTrue(case.page.presentation_eligible())

def click_curve_row(page, row):
    owner = page.parent()
    formal.click_item(owner, 'task_0')
    view = formal.item(owner, 'xyCurveList')
    view.setProperty('contentY', min(row * 42., max(0., view.property('contentHeight') - view.height())))
    formal.pump()
    identity = list(page.comparison.curves)[row]
    pending = list(view.childItems())
    while pending:
        control = pending.pop()
        if control.property('rowId') == identity:
            formal.ensure_visible(control)
            point = control.mapToScene(QPointF(control.width() * .6, control.height() / 2)).toPoint()
            QTest.mouseClick(formal.input_receiver(owner, control), Qt.LeftButton, pos=point)
            formal.pump()
            return
        pending.extend(control.childItems())
    raise AssertionError('Missing mounted curve row ' + str(row))


def task(case, index):
    formal.click_item(case.controller, 'task_' + str(index))
    case.assertEqual(case.controller.taskIndex, index)


def keyboard_task(case, index):
    control = formal.item(case.controller, 'task_' + str(index))
    receiver = formal.input_receiver(case.controller, control)
    receiver.requestActivate()
    formal.pump()
    control.forceActiveFocus()
    case.assertTrue(control.hasActiveFocus())
    QTest.keyClick(receiver, Qt.Key_Space)
    formal.pump()
    case.assertEqual(case.controller.taskIndex, index)


def assert_editor_visible(case, field):
    _, control = xy_field(case.page, field)
    case.assertTrue(control.isVisible())
    ancestor = control.parentItem()
    found = False
    while ancestor is not None:
        if ancestor.metaObject().indexOfProperty('contentY') >= 0:
            top = control.mapToItem(ancestor, QPointF())
            case.assertGreaterEqual(top.y(), -.5)
            case.assertGreaterEqual(top.x(), -.5)
            case.assertLessEqual(top.y() + control.height(), ancestor.height() + .5)
            case.assertLessEqual(top.x() + control.width(), ancestor.width() + .5)
            found = True
        ancestor = ancestor.parentItem()
    case.assertTrue(found, 'Editor must be visible in its actual inspector scroll viewport')
    return control


class XYCompactLayoutTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        self.workspace = self.controller.workspace
        book = create_workbook(self.workspace, 'Experiment')
        sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Original',
            np.array([[0., 1.], [1., 2.], [2., 1.]]), ['Time', 'Signal'])
        self.graph = create_comparison(self.workspace, 'Comparison')
        self.curve = add_curve(self.workspace, self.graph.comparison_id, sheet.sheet_id, 0, 1)
        mount_comparison(self)

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
        self.page.inspector_bridge.selectAll(False)
        select_xy_rows(self.page, [1])
        xy_field(self.page, 'label')[1].forceActiveFocus()
        xy_field(self.page, 'label')[1].selectAll()
        type_xy_field(self.page, 'label', 'unfinished-B')
        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertEqual(other.label, 'Curve B')
        self.assertTrue(self.page.has_pending_project_edits())

        task(self, 1)
        APP.processEvents()

        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertEqual(other.label, 'Curve B')
        self.assertEqual(xy_value(self.page, 'label'), 'unfinished-B')
        self.assertTrue(self.page.has_pending_project_edits())
        self.assertEqual(self.graph.analysis_state, saved)
        self.assertIn('Curve B', self.page.analysis_controller.bridge.state['context'])
        self.assertIsNone(self.page.analysis_controller.peaks)
        self.assertFalse(self.page.analysis_controller.bridge.state['fitEnabled'])
        self.assertFalse(self.page.analysis_controller.bridge.state['saveEnabled'])
        choose_analysis(self.page.analysis_controller, 'model', 'Lorentzian')
        choose_analysis(self.page.analysis_controller, 'preset', 'Loose')
        self.page.render()
        self.assertEqual(self.graph.analysis_state, saved)
        keyboard_task(self, 2)
        APP.processEvents()
        self.assertEqual(self.controller.taskIndex, 2)
        keyboard_task(self, 1)
        keyboard_task(self, 0)
        APP.processEvents()
        self.assertEqual(self.controller.taskIndex, 0)
        self.assertEqual(xy_value(self.page, 'label'), 'unfinished-B')
        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertEqual(other.label, 'Curve B')
        self.assertTrue(self.page.has_pending_project_edits())
        self.assertEqual(self.graph.analysis_state, saved)
        task(self, 1)
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertEqual(xy_value(self.page, 'label'), 'unfinished-B')
        self.assertEqual(other.label, 'Curve B')
        self.assertTrue(self.page.has_pending_project_edits())
        self.assertEqual(self.graph.analysis_state, saved)

    def test_malformed_saved_source_is_deferred_safely_during_navigation(self):
        self.graph.analysis_state = {'version': 1, 'curve_id': []}
        try:
            self.page.analysis_controller.activate(restore_selection=False)
        except (ValueError, KeyError, TypeError) as exc:
            self.fail(f'Navigation must defer malformed saved progress safely: {exc}')
        self.assertEqual(self.graph.analysis_state, {'version': 1, 'curve_id': []})
        self.assertIsNone(self.page.analysis_controller.captured)
        self.page.analysis_controller.activate()
        self.assertIn('could not be restored', self.page.analysis_controller.bridge.state['status'])

    def test_inspector_navigation_preserves_focused_drafts_and_selection(self):
        for index, label in enumerate(('Data', 'Analysis', 'View')):
            self.assertEqual(formal.item(self.controller, 'task_' + str(index)).property('text'), label)
        original = self.curve.label
        assert_editor_visible(self, 'label').forceActiveFocus()
        set_xy_draft(self.page, 'label', 'unfinished legend')
        selected = self.page._selected_ids()
        task(self, 1)
        self.assertTrue(formal.item(self.controller, 'xy-analysis-panel').isVisible())
        self.assertEqual(self.curve.label, original)
        task(self, 0)
        self.assertEqual(xy_value(self.page, 'label'), 'unfinished legend')
        self.assertEqual(self.page._selected_ids(), selected)
        self.assertTrue(self.page.has_pending_project_edits())
        self.page.analysis_controller.activate()
        formal.pump()
        self.assertEqual(self.controller.taskIndex, 1)
        self.assertTrue(formal.item(self.controller, 'xy-analysis-find').isVisible())
        task(self, 2)
        self.page.inspector_bridge.setRangeMode('manual')
        assert_editor_visible(self, 'x_min')
        self.page.inspector_bridge.setRangeMode('all')
        self.assertFalse(formal.item(self.controller, 'xyField_x_min').isVisible())
        self.page.finish_editor()
        self.assertIn('unfinished legend', self.page.inspector_bridge.currentLabel)

    def test_analysis_activation_and_keyboard_navigation_preserve_unfinished_width(self):
        assert_editor_visible(self, 'width').forceActiveFocus()
        set_xy_draft(self.page, 'width', '2.1234567')
        self.page.analysis_controller.activate()
        formal.pump()
        self.assertEqual(self.controller.taskIndex, 1)
        self.assertEqual(xy_value(self.page, 'width'), '2.1234567')
        self.assertEqual(self.curve.width, 1.5)
        keyboard_task(self, 0)
        self.assertTrue(self.page.inspector_bridge._panels[0][0].isVisible())
        self.assertEqual(xy_value(self.page, 'width'), '2.1234567')

    def test_narrow_toolbar_overflow_applies_style_and_tracks_disabled_selection(self):
        bar = formal.item(self.controller, 'XYCommandsRoot')
        self.assertIsInstance(bar, QQuickItem, 'Narrow graphs need actual Quick overflow commands')
        self.host.window.resize(760, 720)
        self.controller.dat_details_action.setChecked(True)
        self.assertTrue(QQmlProperty(bar, 'Layout.maximumWidth', qmlContext(bar.parentItem())).write(225))
        APP.processEvents()
        canvas_quick.pump(50)
        self.assertEqual(bar.width(), 225)
        extension = canvas_quick.item(self.controller, 'XYCommandsOverflow')
        self.assertTrue(extension.isVisible())
        action = self.page.style_actions['line_symbol']
        canvas_quick.click_item(self.controller, extension)
        entry = canvas_quick.item(self.controller, 'XYOverflow_style_line_symbol')
        self.assertTrue(entry.isVisible())
        canvas_quick.click_item(self.controller, entry)
        self.assertEqual(self.curve.style, 'line_symbol')
        self.page.inspector_bridge.selectAll(False)
        self.assertFalse(action.isEnabled())
        canvas_quick.click_item(self.controller, extension)
        self.assertFalse(canvas_quick.item(self.controller, 'XYOverflow_style_line_symbol').isEnabled())
        QTest.keyClick(formal.input_receiver(self.controller, formal.item(self.controller, 'XYOverflow_style_line_symbol')), Qt.Key_Escape)
        select_xy_rows(self.page, [0])
        self.assertTrue(action.isEnabled())
        canvas_quick.command(self.page, 'style:scatter')
        self.assertEqual(self.curve.style, 'scatter')


class XYCompactAnalysisNavigationTests(QuickMigrationCase):
    setUp = workflow_fixture.XYAnalysisWorkflowTests.setUp
    analyze = workflow_fixture.XYAnalysisWorkflowTests.analyze
    fit = workflow_fixture.XYAnalysisWorkflowTests.fit

    def dispose(self):
        workflow_fixture.XYAnalysisWorkflowTests.dispose(self)

    def reopen_saved_progress_with_other_selection(self):
        self.fit()
        self.saved = deepcopy(self.page.comparison.analysis_state)
        comparison_id = self.page.comparison_id
        target = self.folder / 'navigation-progress.spectra'
        # The shared native task selection survives document/project changes.
        # Establish a first Analysis visit after browsing the other source.
        task(self, 0)
        self.w.save_project(target)
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Clean saved project unexpectedly requested discard')):
            self.assertTrue(self.w.open_project(target))
        self.w.open_xy_comparison(comparison_id)
        self.page = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.panel = self.page.analysis_controller
        self.page.inspector_bridge.selectAll(False)
        select_xy_rows(self.page, [1])
        self.other = list(self.page.comparison.curves.values())[1]
        xy_field(self.page, 'label')[1].forceActiveFocus()
        xy_field(self.page, 'label')[1].selectAll()
        type_xy_field(self.page, 'label', 'unfinished-B')
        task(self, 1)
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {self.other.curve_id})
        self.assertEqual(self.page.comparison.analysis_state, self.saved)
        self.assertIsNone(self.panel.fit_result)
        self.assertTrue(self.panel.bridge.state['findEnabled'])
        self.assertFalse(self.panel.bridge.state['saveEnabled'])

    def test_explicit_resume_restores_saved_curve_after_navigation(self):
        self.reopen_saved_progress_with_other_selection()
        self.page.resume_analysis_action.trigger()
        APP.processEvents()
        self.assertEqual(self.other.label, 'unfinished-B')
        self.assertFalse(self.page.has_pending_project_edits())
        self.assertEqual(self.page._selected_ids(), {self.saved['curve_id']})
        self.assertEqual(self.panel.captured.curve_id, self.saved['curve_id'])
        np.testing.assert_allclose(self.panel.fit_result.centers, [.7], atol=1e-5)
        self.assertIn('Signal', self.panel.bridge.state['context'])
        self.assertIn('Restored', self.panel.bridge.state['status'])

        task(self, 0)
        APP.processEvents()
        click_curve_row(self.page, 1)
        task(self, 1)
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {self.other.curve_id})
        # Browsing another curve retains the restored analysis until Find is explicit.
        self.assertEqual(self.panel.captured.curve_id, self.saved['curve_id'])
        self.assertIn('Reference', self.panel.bridge.state['context'])
        np.testing.assert_allclose(self.panel.fit_result.centers, [.7], atol=1e-5)
        self.assertEqual(self.page.comparison.analysis_state, self.saved)
        self.assertFalse(self.panel.bridge.state['fitEnabled'])
        self.assertFalse(self.panel.bridge.state['saveEnabled'])

    def test_find_after_navigation_replaces_saved_progress_with_selected_curve(self):
        self.reopen_saved_progress_with_other_selection()
        analysis_command(self.panel, 'find')
        wait_for_background_jobs(self.w)
        self.assertEqual(self.page._selected_ids(), {self.other.curve_id})
        self.assertEqual(self.other.label, 'unfinished-B')
        self.assertEqual(self.panel.captured.curve_id, self.other.curve_id)
        self.assertEqual(self.page.comparison.analysis_state['curve_id'], self.other.curve_id)
        self.assertIn('Reference', self.panel.bridge.state['context'])
        self.assertEqual(len(self.panel.peaks.indices), 1)
        analysis_command(self.panel, 'fit')
        wait_for_background_jobs(self.w)
        np.testing.assert_allclose(self.panel.fit_result.centers, [-1], atol=1e-5)

    def test_first_analysis_tab_without_saved_progress_can_find_current_curve(self):
        self.page.inspector_bridge.selectAll(False)
        select_xy_rows(self.page, [1])
        other = list(self.page.comparison.curves.values())[1]
        task(self, 1)
        APP.processEvents()
        self.assertEqual(self.page._selected_ids(), {other.curve_id})
        self.assertTrue(self.panel.bridge.state['findEnabled'])
        analysis_command(self.panel, 'find')
        wait_for_background_jobs(self.w)
        self.assertEqual(self.panel.captured.curve_id, other.curve_id)
        self.assertEqual(len(self.panel.peaks.indices), 1)
        self.assertIn('Reference', self.panel.bridge.state['context'])

        saved = deepcopy(self.page.comparison.analysis_state)
        workspace = self.w.workspace
        graph = create_comparison(workspace, 'Switched analysis owner')
        curve = add_curve(workspace, graph.comparison_id, other.sheet_id, 0, 2, label='Switched reference')
        switched = self.w.open_xy_comparison(graph.comparison_id)
        formal.pump()
        self.assertEqual(self.controller.taskIndex, 1)
        self.assertIs(self.controller.propertiesController, switched)
        self.assertTrue(switched.presentation_eligible())
        self.assertTrue(switched.analysis_controller.bridge.state['findEnabled'])
        self.assertIn('Switched reference', switched.analysis_controller.bridge.state['context'])
        self.assertEqual(switched._selected_ids(), {curve.curve_id})
        self.assertEqual(self.page.comparison.analysis_state, saved)

        self.controller.dat_details_action.setChecked(False)
        hidden_graph = create_comparison(workspace, 'Hidden analysis owner')
        add_curve(workspace, hidden_graph.comparison_id, other.sheet_id, 0, 2, label='Hidden reference')
        with patch('spectra_app.xy_analysis_controller.capture_input', side_effect=AssertionError('Hidden analysis eagerly captured source data')):
            hidden = self.w.open_xy_comparison(hidden_graph.comparison_id)
            hidden.render()
            self.w._refresh_graph_commands()
            formal.pump()
        self.assertFalse(self.controller.propertiesVisible)
        self.assertFalse(hidden.analysis_controller._activated)
        self.assertEqual(hidden.analysis_controller.bridge.state['context'], '')
        self.controller.dat_details_action.setChecked(True)
        formal.pump()
        self.assertTrue(self.controller.propertiesVisible)
        self.assertTrue(hidden.analysis_controller._activated)
        self.assertTrue(hidden.analysis_controller.bridge.state['findEnabled'])
        self.assertIn('Hidden reference', hidden.analysis_controller.bridge.state['context'])


if __name__ == '__main__':
    unittest.main()
