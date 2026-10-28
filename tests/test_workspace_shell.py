"""Shell integration: navigation and presentation must not change editor owners."""
from tests import analysis_header_quick_helpers as ah
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import sys
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt, QEvent, QCoreApplication, QPointF
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from tests import dat_properties_quick_helpers as dp
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as quick

from tests.xy_helpers import set_xy_draft, xy_value, select_xy_rows, xy_field
from tests.formal_quick_helpers import edit_field, item

from tests.xy_canvas_quick_helpers import click_named as click_xy_command, item as xy_command_item

class WorkspaceShellTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.callback_errors = []
        capture = patch.object(sys, 'excepthook', lambda *error: self.callback_errors.append(error))
        capture.start()
        self.addCleanup(capture.stop)
        self.w = self.controller
        self.errors = self.quick_errors
        self.host.window.resize(1500, 920)
        quick.pump()
        self.addCleanup(self.dispose)

    def dispose(self):
        QTest.keyRelease(self.host.window, Qt.Key_Control)
        QTest.keyRelease(self.host.window, Qt.Key_Shift)
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.errors, [])

    def click_control(self, control):
        receiver = quick.input_receiver(self.w, control)
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        QTest.mouseClick(receiver, Qt.LeftButton, pos=point)
        quick.pump()

    def click_text(self, text, *, menu=False):
        kind = 'QQuickMenuBarItem' if menu else 'QQuickAbstractButton'
        matches = [control for control in self.host.window.findChildren(QQuickItem)
                   if control.inherits(kind) and control.isVisible()
                   and (menu or not control.inherits('QQuickMenuBarItem'))
                   and str(control.property('text')).replace('&', '') == text]
        self.assertEqual(len(matches), 1, text)
        self.click_control(matches[0])

    def view_properties(self):
        self.click_text('View', menu=True)
        matches = [control for control in self.host.window.findChildren(QQuickItem)
                   if control.inherits('QQuickMenuItem') and control.isVisible()
                   and control.property('text') == 'Properties']
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0].isEnabled())
        self.click_control(matches[0])

    def viewport_geometry(self):
        viewport = item(self.w, 'workspaceDocumentViewport')
        return (viewport.mapToScene(QPointF()), viewport.width(), viewport.height())

    def assert_context_viewport_geometry(self, before, band_height):
        after = self.viewport_geometry()
        # Each current command set keeps its natural height. Its exact height
        # delta accounts for the viewport's top edge; the shell edges stay fixed.
        delta = item(self.w, 'contextCommandBand').height() - band_height
        self.assertEqual(after[0].x(), before[0].x())
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[0].y() - before[0].y(), delta)
        self.assertEqual(after[0].y()+after[2], before[0].y()+before[2])

    def import_three(self):
        texts = ('signal,time\n10,1\n20,2\n30,3\n',
                 'unused,time,signal\n99,4,40\n98,8,80\n',
                 'signal,unused,time\n5,0,0.5\n15,0,1.5\n25,0,2.5\n')
        paths = []
        for i, text in enumerate(texts):
            path = self.folder / f'{i}.csv'
            path.write_text(text, encoding='utf-8')
            paths.append(path)
        self.w.import_xy_files(paths, mappings=[(1, [0]), (1, [2]), (2, [0])], compare=True)
        return paths, list(self.w.workspace.workbooks.values()), self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)

    def load_dat(self):
        path = self.folder / 'spectrum.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        self.w._load_dat_file_direct(path)
        return self.w.workspace.active

    def test_fresh_window_starts_in_empty_workspace(self):
        w = self.w
        self.assertEqual(w.outerView, 'workspace')
        self.assertFalse(item(w, 'applicationDatCanvas').isVisible())
        self.assertFalse(w.analyze_action.isEnabled())
        self.assertTrue(item(w, 'projectNavigator').isVisible())
        self.assertTrue(item(w, 'workspaceDocuments').isVisible())
        empty_hint = [control for control in item(w, 'workspaceDocuments').findChildren(QQuickItem)
                      if control.property('text') == 'Open a Workbook or graph from Project, or start with data files.']
        self.assertEqual(len(empty_hint), 1)
        self.assertTrue(empty_hint[0].isVisible())
        self.assertEqual(w.graph_documents.registry.open_keys(), ())
        self.assertEqual(w.graph_documents.keys(), ())
        self.assertIsNone(w.graph_documents.registry.active_key)
        self.assertEqual(w.workspace.datasets, {})
        self.assertEqual(w.workspace.workbooks, {})
        self.assertEqual(w.workspace.comparisons, {})
        self.assertTrue(w.propertiesVisible)
        self.assertTrue(w.dat_details_action.isEnabled())
        self.assertTrue(item(w, 'contextCommandBand').isVisible())
        self.assertEqual(w.contextKind, 'empty')
        self.assertEqual(w.contextSummary, 'Open a Workbook or graph from Project.')
        self.assertFalse(w.projectModified)
        self.click_text('Analysis')
        self.assertEqual(w.outerView, 'analysis')
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'workspace')

    def test_startup_workspace_allows_deliberate_analysis_switch(self):
        w = self.w
        session = self.load_dat()
        page = w.open_graph_document(session.dataset_id)
        w.analyze_action.trigger()
        quick.pump()
        self.assertEqual(w.outerView, 'analysis')
        self.assertTrue(w.propertiesVisible)
        self.assertTrue(w.dat_details_action.isEnabled())
        self.assertTrue(item(w, 'task_1').isVisible())
        self.assertTrue(item(w, 'task_2').isVisible())
        self.host.window.hide()
        self.host.show()
        quick.pump()
        self.assertEqual(w.outerView, 'analysis')
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'workspace')
        self.assertTrue(w.propertiesVisible)
        self.assertIs(w.plot_page, page)
        self.assertTrue(w.graph_documents.content_item((session.dataset_id, session.active_graph_id)).isVisible())

    def test_project_tree_stays_visible_across_document_kinds_and_restores_width(self):
        w = self.w
        project = item(w, 'projectNavigator')
        self.assertTrue(project.isVisible(), 'Project navigation must be available immediately')
        _, books, xy = self.import_three()
        for book in books[:2]:
            w.open_workbook(book.workbook_id)
            quick.pump()
            self.assertTrue(project.isVisible())
        w.open_xy_comparison(xy.comparison_id)
        self.assertTrue(project.isVisible())
        session = self.load_dat()
        w.open_graph_document(session.dataset_id)
        self.assertTrue(project.isVisible())
        self.assertTrue(w.request_outer_view('analysis'))
        self.assertTrue(project.isVisible())
        self.click_text('Workspace')
        self.assertEqual(w.outerView, 'workspace')
        # Resize the actual Quick SplitView divider, preserving the user's width.
        start = project.mapToScene(QPointF(project.width()+3, project.height()/2)).toPoint()
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.host.window, start + QPointF(30, 0).toPoint())
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=start + QPointF(30, 0).toPoint())
        quick.pump()
        width = project.width()
        self.assertGreater(width, 240)
        w.project_panel_action.trigger()
        self.assertFalse(project.isVisible())
        w.project_panel_action.trigger()
        quick.pump()
        self.assertTrue(project.isVisible())
        self.assertAlmostEqual(project.width(), width, delta=3)

    def test_document_activation_keeps_workspace_and_properties_geometry(self):
        w = self.w
        _, books, xy = self.import_three()
        session = self.load_dat()
        w.open_graph_document(session.dataset_id)
        w.open_workbook(books[0].workbook_id)
        w.open_xy_comparison(xy.comparison_id)
        w.graph_documents.setMode('free')
        quick.pump()
        viewport = self.viewport_geometry()
        band_height = item(w, 'contextCommandBand').height()
        project = item(w, 'projectNavigator')
        project_width = project.width()
        rectangles = {key: w.graph_documents.registry.normal_rectangle(key)
                      for key in w.graph_documents.registry.open_keys()}
        for activate, kind in ((lambda: w.open_graph_document(session.dataset_id), 'dat'),
                               (lambda: w.open_workbook(books[0].workbook_id), 'workbook'),
                               (lambda: w.open_xy_comparison(xy.comparison_id), 'xy')):
            activate()
            quick.pump()
            self.assertTrue(w.propertiesVisible)
            self.assertTrue(item(w, 'contextCommandBand').isVisible())
            self.assert_context_viewport_geometry(viewport, band_height)
            self.assertEqual(project.width(), project_width)
            for key, rect in rectangles.items():
                self.assertEqual(w.graph_documents.registry.normal_rectangle(key), rect)
            self.assertEqual(w.contextKind, kind)
            self.assertTrue(item(w, 'applicationProperties').isVisible())
        self.assertEqual(w.contextKind, 'xy')
        self.assertIs(w.propertiesController, xy)
        w.dat_details_action.setChecked(False)
        quick.pump(60)
        hidden_viewport = self.viewport_geometry()
        hidden_band_height = item(w, 'contextCommandBand').height()
        for activate in (lambda: w.open_workbook(books[0].workbook_id),
                         lambda: w.open_graph_document(session.dataset_id),
                         lambda: w.open_xy_comparison(xy.comparison_id)):
            activate()
            quick.pump()
            self.assertFalse(w.propertiesVisible)
            self.assertTrue(w.dat_details_action.isEnabled())
            self.assert_context_viewport_geometry(hidden_viewport, hidden_band_height)

    def test_properties_preserve_user_intent_and_heatmap_tree_route(self):
        w = self.w
        session = self.load_dat()
        page = w.open_graph_document()
        self.assertTrue(w.propertiesVisible)
        w.dat_details_action.trigger()
        self.assertFalse(w.propertiesVisible)
        _, books, xy = self.import_three()
        self.assertFalse(w.propertiesVisible)
        self.assertTrue(w.dat_details_action.isEnabled())
        self.assertTrue(item(w, 'contextCommandBand').isVisible())
        self.assertFalse(w.add_gate_comparison_btn.isEnabled())
        self.view_properties()
        self.assertTrue(w.propertiesVisible)
        self.view_properties()
        self.assertFalse(w.propertiesVisible)
        w.project.select_key(('heatmap', (session.dataset_id, 'default')))
        w._open_navigator_item()
        self.assertIs(w.plot_page, page)
        self.assertFalse(w.propertiesVisible)
        w.dat_details_action.trigger()
        self.assertTrue(w.propertiesVisible)
        w.open_workbook(books[0].workbook_id)
        self.assertTrue(w.propertiesVisible)
        w.open_graph_document(session.dataset_id)
        self.assertTrue(w.propertiesVisible)
        quick.click_item(w, 'floatPropertiesButton')
        self.host._floating_properties.close()
        quick.pump()
        self.assertFalse(w.propertiesVisible)
        w.open_xy_comparison(xy.comparison_id)
        w.open_graph_document(session.dataset_id)
        self.assertFalse(w.propertiesVisible, 'Closing Properties records explicit visibility intent')
        self.assertEqual(self.errors, [])

    def test_project_restore_reuses_properties_host_and_releases_old_inspectors(self):
        from shiboken6 import isValid
        w = self.w
        _, books, xy = self.import_three()
        session = self.load_dat()
        w.open_graph_document(session.dataset_id)
        w.open_xy_comparison(xy.comparison_id)
        self.assertTrue(w.request_task(0))
        quick.pump()
        properties = item(w, 'applicationProperties')
        old_inspector = item(w, 'xyCurveList')
        old_bridge = xy.inspector_bridge
        saved = w.save_project(self.folder / 'stable-shell.spectra')
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Clean saved project unexpectedly requested discard')):
            self.assertTrue(w.open_project(saved))
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        quick.pump()
        self.assertFalse(isValid(old_inspector))
        self.assertFalse(isValid(old_bridge))
        self.assertIs(item(w, 'applicationProperties'), properties)
        viewport = self.viewport_geometry()
        band_height = item(w, 'contextCommandBand').height()
        w.open_workbook(books[0].workbook_id)
        quick.pump()
        self.assert_context_viewport_geometry(viewport, band_height)
        w.open_graph_document(session.dataset_id)
        quick.pump()
        self.assert_context_viewport_geometry(viewport, band_height)
        self.assertEqual(self.errors, [])

    def test_inspector_toggle_keeps_selection_drafts_and_guarded_owner(self):
        w = self.w
        _, books, page = self.import_three()
        self.assertTrue(xy_command_item(w, 'XYInspectorToggle').isVisible())
        self.assertTrue(page.command_bridge.inspectorChecked)
        select_xy_rows(page, [1, 2], current=1)
        selected = page._selected_ids()
        color = page._selected().color
        set_xy_draft(page, 'color', 'invalid')
        set_xy_draft(page, 'label', 'Unfinished legend')
        xy_field(page, 'color')[1].forceActiveFocus()
        click_xy_command(w, 'XYInspectorToggle')
        self.assertFalse(w.propertiesVisible)
        self.assertEqual(page._selected().color, color)
        self.assertEqual(page._selected_ids(), selected)
        self.assertEqual(xy_value(page, 'color'), 'invalid')
        self.assertEqual(xy_value(page, 'label'), 'Unfinished legend')
        active = w.graph_documents.registry.active_key
        with self.assertRaisesRegex(ValueError, 'six-digit'):
            w.open_workbook(books[0].workbook_id)
        self.click_text('Analysis')
        self.assertEqual(w.outerView, 'workspace')
        self.assertEqual(w.graph_documents.registry.active_key, active)
        self.assertFalse(w.graph_documents.close_document())
        quick.pump()
        self.assertTrue(w.propertiesVisible)
        self.assertTrue(xy_field(page, 'color')[1].hasActiveFocus())
        click_xy_command(w, 'XYInspectorToggle')
        self.assertFalse(w.propertiesVisible)
        click_xy_command(w, 'XYInspectorToggle')
        self.assertTrue(w.propertiesVisible)
        self.assertEqual(xy_value(page, 'color'), 'invalid')
        self.assertEqual(page._selected_ids(), selected)
        set_xy_draft(page, 'color', color)
        page.finish_editor()

    def test_hidden_heatmap_draft_rejects_analysis_and_document_changes(self):
        w = self.w
        self.load_dat()
        page = w.open_graph_document()
        width = page.presentation.width_in
        def close_floating():
            quick.click_item(w, 'floatPropertiesButton')
            self.host._floating_properties.close()
            quick.pump()
        for hide in (w.dat_details_action.trigger,
                     lambda: quick.click_item(w, 'command_properties'),
                     self.view_properties, close_floating):
            if w.propertiesFloating:
                self.host.moveProperties(False)
            w.dat_details_action.setChecked(True)
            self.assertTrue(w.request_task(2))
            control = edit_field(w, 'width_in', '-')
            self.assertTrue(control.hasActiveFocus())
            hide()
            quick.pump()
            self.assertEqual(control.property('text'), '-')
            self.assertEqual(page.formal.state['fields']['width_in'], '-')
            self.assertEqual(page.presentation.width_in, width)
            self.assertTrue(page.has_pending_project_edits())
            self.assertFalse(w.dat_details_action.isChecked())
            w.dat_details_action.setChecked(True)
            quick.pump()
            self.assertEqual(control.property('text'), '-')
            self.assertEqual(page.presentation.width_in, width)
            w.dat_details_action.setChecked(False)
        active = w.graph_documents.registry.active_key
        self.click_text('Analysis')
        self.assertEqual(w.outerView, 'workspace', 'Analysis must not strand a hidden invalid heatmap editor')
        # Validation's focusRequested signal reveals this same invalid editor
        # through the production recovery route, including a floating panel.
        quick.pump(90)
        self.assertTrue(w.propertiesVisible)
        self.assertIs(w.propertiesController, page)
        self.assertEqual(w.graph_documents.registry.active_key, active)
        self.assertEqual(page.presentation.width_in, width)
        self.assertEqual(control.property('text'), '-')
        self.assertTrue(control.hasActiveFocus())
        self.assertIs(quick.input_receiver(w, control).activeFocusItem(), control)
        with self.assertRaises(ValueError):
            w.open_workbook(next(iter(w.workspace.workbooks)))
        self.assertFalse(w.graph_documents.close_document())
        # The visible real editor resolves the invalid draft before navigation.
        w.dat_details_action.setChecked(True)
        edit_field(w, 'width_in', '4', commit=True)
        self.click_text('Analysis')
        self.assertEqual(w.outerView, 'analysis')

    def test_inspector_mouse_toggle_does_not_commit_valid_focused_drafts(self):
        _, _, page = self.import_three()
        original_label = page._selected().label
        self.assertTrue(self.w.request_task(0))
        xy_field(page, 'label')[1].forceActiveFocus()
        set_xy_draft(page, 'label', 'Unfinished valid label')
        click_xy_command(self.w, 'XYInspectorToggle')
        self.assertEqual(page._selected().label, original_label)
        self.assertEqual(xy_value(page, 'label'), 'Unfinished valid label')
        click_xy_command(self.w, 'XYInspectorToggle')
        self.assertEqual(page._selected().label, original_label)
        self.assertTrue(page.has_pending_project_edits())
        page.finish_editor()

    def test_heatmap_property_edit_reveals_hidden_dat_details(self):
        w = self.w
        self.host.window.requestActivate()
        dp.pump()
        self.load_dat()
        w.open_graph_document()
        w._render_plot_page()
        for kind, field in (('title', 'plot_title'), ('horizontal-range', 'xmin')):
            with self.subTest(kind=kind):
                w.dat_details_action.setChecked(False)
                w._show_plot_property(kind)
                dp.pump()
                self.assertTrue(w.propertiesVisible)
                self.assertTrue(w.dat_details_action.isChecked())
                editor = dp.dat_item(w, field)
                self.assertTrue(editor.isVisible())
                for _ in range(20):
                    if editor.hasActiveFocus():
                        break
                    dp.pump(20)
                self.assertTrue(editor.hasActiveFocus())

    def test_three_files_keep_source_navigation_and_shell_changes_do_not_dirty(self):
        w = self.w
        self.assertTrue(item(w, 'projectNavigator').isVisible(), 'Project tree must remain reachable throughout import')
        paths, books, page = self.import_three()
        self.assertEqual(len(books), 3)
        self.assertEqual(len(page.comparison.curves), 3)
        for line, x, y in zip(page.axes.lines, ([1,2,3], [4,8], [.5,1.5,2.5]),
                              ([10,20,30], [40,80], [5,15,25])):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)
        select_xy_rows(page, [1])
        page.inspector_bridge.action('source')
        book_page = w.graph_documents.document_for(w.graph_documents.registry.active_key)
        self.assertEqual(book_page.current_sheet.sheet_id, books[1].sheet_ids[0])
        w.open_xy_comparison(page.comparison_id)
        w.graph_documents.setMode('tabbed')
        saved = w.save_project(self.folder / 'comparison.spectra')
        click_xy_command(w, 'XYInspectorToggle')
        w.project_panel_action.trigger()
        w.project_panel_action.trigger()
        w._refresh_project_modified()
        self.assertFalse(w.projectModified)
        for path in paths:
            path.unlink()
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Clean saved project unexpectedly requested discard')):
            self.assertTrue(w.open_project(saved))
        restored = w.graph_documents.document_for(w.graph_documents.registry.active_key)
        self.assertEqual(restored.comparison_id, page.comparison_id)
        self.assertTrue(item(w, 'projectNavigator').isVisible())
        self.assertFalse(w.propertiesVisible)
        self.assertEqual(len(restored.axes.lines), 3)

    def test_unknown_tree_kinds_are_inert_and_heatmap_entries_follow_renames(self):
        w = self.w
        session = self.load_dat()
        w.open_graph_document(session.dataset_id)
        w.rename_graph('default', 'Overview')
        heatmaps = [row for row in w.project.all_rows() if row.key[0] == 'heatmap']
        self.assertEqual(len(heatmaps), 1, 'Formal graphs must be reachable with DAT details closed')
        self.assertIn('Overview', heatmaps[0].label)
        from spectra_app.project_model import ProjectRow
        unknown = ProjectRow(('unknown', 'not-a-comparison'), 'Unknown')
        w.project.set_rows(w.project.all_rows() + [unknown])
        w.project.select_key(unknown.key)
        active = w.graph_documents.registry.active_key
        w._open_navigator_item()
        self.assertEqual(w.graph_documents.registry.active_key, active)
        self.assertFalse(w._navigator_buttons['Open'].isEnabled())
        self.assertFalse(w._navigator_buttons['Rename'].isEnabled())
        self.assertFalse(w._navigator_buttons['Remove'].isEnabled())
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
