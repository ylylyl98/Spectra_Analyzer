"""Observable daily comparison commands and staged physical Gate matching."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtQml import QQmlEngine, QQmlExpression

from spectra_app.sheet_comparison_dialog import SheetComparisonDialog
from spectra_app.workbook_state import create_comparison, remove_comparison, create_workbook, register_xy_sheet, merge_workbooks, ensure_dataset_sheet
from spectra_app.processing import energy_second_derivative
from tests.quick_migration_case import QuickMigrationCase
from tests.background_helpers import wait_jobs
from tests.formal_quick_helpers import click_item, item, pump

def set_sheet_mapping(dialog, sheet_id, x, ys):
    bridge = dialog.bridge
    bridge.selectSource(sheet_id)
    if sheet_id not in dialog.selected_mappings():
        bridge.toggleSource(sheet_id)
    bridge.setX(x)
    for row in list(bridge._column_model.rows):
        bridge.setY(row['column'], row['column'] in ys)


class DailyComparisonTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        self.errors = self.quick_errors
        self.host.window.resize(1500, 920)
        pump()
        self.addCleanup(lambda: self.assertFalse(self.errors))

    def xy(self, name='source'):
        path = self.folder / (name + '.csv')
        path.write_text('x,a,b\n0.5,1,3\n1.5,2,4\n')
        self.w._import_data_files([path])
        wait_jobs(self.w)
        book = next(b for b in self.w.workspace.workbooks.values()
                    if any(self.w.workspace.sheets[s].source_path == path for s in b.sheet_ids))
        page = self.w.open_workbook(book.workbook_id).current_sheet
        return book, page

    def dat(self, name='gates', gates=(-2, 4, 7)):
        path = self.folder / (name + '.dat')
        path.write_text('Energy(eV) ' + ' '.join(map(str, gates)) + '\n' + '\n'.join(
            f'{1+j/10} ' + ' '.join(str((i+1)*(j/10)**2) for i in range(len(gates))) for j in range(13)))
        self.w._import_data_files([path])
        wait_jobs(self.w)
        session = self.w.workspace.dataset_for_path(path)
        ensure_dataset_sheet(self.w.workspace, session.dataset_id, role='processed')
        sheets = [s for s in self.w.workspace.sheets.values() if s.dataset_id == session.dataset_id]
        return session, sheets

    def pin(self, target):
        commands = self.w.workbook_commands
        self.assertTrue(item(self.w, 'workbookPinTarget').isVisible(), 'Shared commands need a visible Pin target control')
        commands.chooseTarget(target.comparison_id)
        if not commands.targetPinned:
            commands.pinTarget(True)
        return commands

    def targets(self):
        book, page = self.xy()
        a, b = [create_comparison(self.w.workspace, n) for n in ('Target A', 'Target B')]
        self.w.open_xy_comparison(a.comparison_id)
        self.w.open_workbook(book.workbook_id)
        commands = self.pin(a)
        return book, page, a, b, commands

    def test_pin_survives_focus_import_sheet_switch_and_explicit_choice_updates_it(self):
        book, page, a, b, commands = self.targets()
        self.w.open_xy_comparison(b.comparison_id)
        other, source = self.xy('other')
        self.w.open_workbook(book.workbook_id)
        self.w.open_workbook(other.workbook_id)
        self.assertEqual(commands.targetId, a.comparison_id)
        click_item(self.w, 'workbookAppend')
        self.assertEqual(len(a.curves), 2)
        self.assertFalse(b.curves)
        self.assertEqual(self.w.graph_documents.registry.active_key, ('workbook', other.workbook_id, ''))
        np.testing.assert_array_equal(self.w.graph_documents.document_for(('xy', a.comparison_id, '')).axes.lines[0].get_xdata(), [.5, 1.5])
        commands.chooseTarget(b.comparison_id)
        self.w.open_xy_comparison(a.comparison_id)
        self.w.open_workbook(book.workbook_id)
        self.assertEqual(commands.targetId, b.comparison_id)
        commands.append()
        self.assertEqual(len(b.curves), 2)

    def test_pin_rename_close_show_and_delete_do_not_fall_back(self):
        book, page, a, b, commands = self.targets()
        a.name = 'Renamed target'
        self.w._auxiliary_changed()
        self.assertEqual(commands.targetLabel, a.name)
        self.w.graph_documents.close_document(('xy', a.comparison_id, ''))
        commands.append()
        self.assertEqual(len(a.curves), 2)
        self.assertNotIn(('xy', a.comparison_id, ''), self.w.graph_documents.registry.open_keys())
        self.assertIsNone(self.w.graph_documents.content_item(('xy', a.comparison_id, '')))
        commands.showTarget()
        self.assertEqual(self.w.graph_documents.registry.active_key, ('xy', a.comparison_id, ''))
        self.w.open_workbook(book.workbook_id)
        remove_comparison(self.w.workspace, a.comparison_id)
        self.w._refresh_auxiliary_documents()
        self.w._auxiliary_changed()
        self.w.open_xy_comparison(b.comparison_id)
        self.w.open_workbook(book.workbook_id)
        self.assertIsNone(commands.targetId)
        self.assertFalse(bool(commands.ready and commands.hasTarget))
        self.assertFalse(commands.hasTarget)

    def test_direct_duplicate_skip_and_explicit_repeat_keep_styles_and_source(self):
        book, page = self.xy()
        self.w.workbook_commands.plot('scatter')
        graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        original = copy.deepcopy(graph.comparison.curves)
        self.w.open_workbook(book.workbook_id)
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Routine modal')):
            self.w.workbook_commands.append()
        self.assertEqual(graph.comparison.curves, original)
        self.assertIn('skipped 2', self.w.statusText)
        self.assertIsNotNone(item(self.w, 'workbookAddAgain'))
        self.w.workbook_commands.repeat()
        self.assertEqual(len(graph.comparison.curves), 4)
        self.assertEqual(list(graph.comparison.curves.values())[:2], list(original.values()))
        self.assertEqual(self.w.graph_documents.registry.active_key, ('workbook', book.workbook_id, ''))

    def test_pin_new_open_and_failed_install_rollback(self):
        book, page, a, b, commands = self.targets()
        path = self.w.save_project(self.folder / 'targets.spectra')
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Clean saved project unexpectedly requested discard')), patch.object(self.w, '_project_installed_workspace', side_effect=ValueError('install failure')):
            with self.assertRaisesRegex(ValueError, 'install failure'):
                self.w.open_project(path)
        self.assertEqual(commands.targetId, a.comparison_id)
        self.assertTrue(commands.targetPinned)
        self.w.open_xy_comparison(b.comparison_id)
        self.w.open_workbook(book.workbook_id)
        self.assertEqual(commands.targetId, a.comparison_id)
        # Visiting B intentionally persists a new open document and stacking
        # entry. Save that real layout before exercising a clean reopen.
        path = self.w.save_project(path)
        self.assertEqual(commands.targetId, a.comparison_id)
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Clean saved project unexpectedly requested discard')):
            self.assertTrue(self.w.open_project(path))
        self.assertFalse(commands.targetPinned)
        self.w.open_workbook(book.workbook_id)
        self.pin(self.w.workspace.comparisons[a.comparison_id])
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Clean saved project unexpectedly requested discard')):
            self.assertTrue(self.w.new_project())
        self.assertIsNone(self.w._last_comparison_id)
        self.assertFalse(commands.targetPinned)

    def test_workbook_defaults_original_and_explicit_result_survives_merge_selection(self):
        _, sheets = self.dat()
        book = self.w._sheet_owner(sheets[0].sheet_id)
        other, _ = self.xy()
        merge_workbooks(self.w.workspace, [book.workbook_id, other.workbook_id], book.workbook_id)
        self.w._refresh_data_navigator()
        project = self.w.app_bridge.project
        project.select_key(('workbook', book.workbook_id))
        originals = [s for s in book.sheet_ids if self.w.workspace.sheets[s].role == 'original']
        self.assertEqual(self.w._selected_sheet_ids(), originals)
        result = next(s for s in sheets if s.role == 'processed')
        project.select_key(('sheet', result.sheet_id), Qt.ControlModifier)
        self.assertEqual(set(self.w._selected_sheet_ids()), set(originals + [result.sheet_id]))

    def test_dialog_workbook_check_includes_all_and_originals_command_is_explicit(self):
        _, sheets = self.dat()
        dialog = SheetComparisonDialog(self.w.workspace)
        self.addCleanup(dialog.deleteLater)
        dialog.bridge.toggleSource(dialog.bridge.sourceIdAt(0))
        self.assertEqual(list(dialog.selected_mappings()), [s.sheet_id for s in sheets])
        dialog.bridge.selectOriginals()
        self.assertEqual(list(dialog.selected_mappings()), [s.sheet_id for s in sheets if s.role == 'original'])
        result = next(s for s in sheets if s.role == 'processed')
        dialog.bridge.toggleSource(result.sheet_id)
        self.assertIn(result.sheet_id, dialog.selected_mappings())
        dialog.reject()
        self.assertFalse(self.w.workspace.comparisons)

    def test_exact_gate_match_uses_each_target_order_and_failure_is_atomic(self):
        source_session, first = self.dat('source-gates')
        target_session, second = self.dat('target-gates')
        # Real physical cubes may have a distinct order after source operations.
        target_session.base_cube = replace(target_session.base_cube, gate=np.array([7., -2., 4.]))
        target_session.current_cube = replace(target_session.current_cube, gate=np.array([4., 7., -2.]))
        source = next(s for s in first if s.role == 'original')
        raw = next(s for s in second if s.role == 'original')
        processed = next(s for s in second if s.role == 'processed')
        book, xy = self.xy()
        selected = [source.sheet_id, raw.sheet_id, processed.sheet_id, xy.sheet_id]
        dialog = SheetComparisonDialog(self.w.workspace, selected_sheet_ids=selected)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(hasattr(dialog.bridge, 'matchSelectedGates'), 'Gate matching must be an explicit action')
        set_sheet_mapping(dialog, source.sheet_id, 0, [1, 3])
        xy_before = copy.deepcopy(dialog.bridge._choices[xy.sheet_id])
        dialog.bridge.matchSelectedGates()
        self.assertEqual(dialog.bridge._choices[raw.sheet_id], (0, [2, 1], 'raw'))
        self.assertEqual(dialog.bridge._choices[processed.sheet_id], (0, [3, 2], 'processed'))
        self.assertEqual(dialog.bridge._choices[xy.sheet_id], xy_before)
        target_session.current_cube = replace(target_session.current_cube, gate=np.array([4., 7., -2.000000001]))
        before = copy.deepcopy(dialog.bridge._choices)
        dialog.bridge.matchSelectedGates()
        self.assertEqual(dialog.bridge._choices, before)
        self.assertIn('-2', dialog.bridge.feedback)
        self.assertIn(dialog.bridge._names[processed.sheet_id], dialog.bridge.feedback)
        self.assertFalse(self.w.workspace.comparisons)

    def test_processing_action_moves_to_analysis_and_really_applies_transform(self):
        session, sheets = self.dat()
        self.w.activate_dataset(session.dataset_id)
        self.assertTrue(self.w.request_outer_view('analysis'))
        self.assertTrue(self.w.request_task(0))
        pump()
        self.assertEqual(item(self.w, 'processingApply').property('text'), 'Apply processing')
        self.assertTrue(item(self.w, 'applicationProperties').isAncestorOf(item(self.w, 'processingPanel')))
        self.assertTrue(item(self.w, 'processingPanel').isVisible())
        original = session.base_cube.z.copy()
        source_graph = session.active_graph
        expected = energy_second_derivative(session.base_cube.photon_energy, original)
        self.w.processing.setMode('energy_second_derivative')
        self.w.processing.setSmoothing(False)
        click_item(self.w, 'processingApply')
        wait_jobs(self.w)
        np.testing.assert_allclose(session.current_cube.z, expected)
        np.testing.assert_array_equal(session.base_cube.z, original)
        self.assertEqual(item(self.w, 'processingApply').property('text'), 'Apply processing')
        result_graph = session.active_graph
        self.assertIsNot(result_graph, source_graph)
        self.assertFalse(source_graph.published)
        self.assertIsNotNone(result_graph.result_id)
        self.assertEqual({g.graph_id for g in session.graphs.values() if g.published}, {result_graph.graph_id})
        np.testing.assert_allclose(session.processing_results[result_graph.result_id].cube.z, expected)

    def test_heatmap_view_default_later_choice_and_collapsed_navigation(self):
        session, sheets = self.dat()
        original = next(s for s in sheets if s.role == 'original')
        book = self.w._sheet_owner(original.sheet_id)
        self.assertTrue(self.w.request_outer_view('analysis'))
        self.assertTrue(self.w.request_task(0))
        pump()
        self.assertIsNotNone(item(self.w, 'sourceNavigation'))
        self.assertFalse(item(self.w, 'sourceNavigation').property('advancedExpanded'))
        self.w.open_workbook(book.workbook_id, original.sheet_id)
        self.w.workbook_commands.plot('heatmap')
        self.assertEqual(self.w.taskIndex, 2)
        self.assertTrue(self.w.request_task(1))
        self.w.open_workbook(book.workbook_id)
        self.w.workbook_commands.plot('heatmap')
        self.assertEqual(self.w.taskIndex, 1)
        self.w._set_dat_details_requested(False)
        self.w.open_workbook(book.workbook_id)
        self.w.workbook_commands.plot('heatmap')
        self.assertFalse(self.w.propertiesVisible)

    def test_current_gate_appends_raw_then_processed_without_activating_existing_target(self):
        session, _ = self.dat()
        self.w.activate_dataset(session.dataset_id)
        self.assertTrue(self.w.request_outer_view('analysis'))
        self.assertEqual(self.w.workspace.active_dataset_id, session.dataset_id)
        # A new graph activates; a later addition returns to the source view.
        self.w._set_current_gate_from_index(0)
        self.w._add_current_gate_comparison()
        target = self.w.workspace.comparisons[self.w._last_comparison_id]
        graph = self.w.graph_documents.document_for(('xy', target.comparison_id, ''))
        self.assertEqual(self.w.graph_documents.registry.active_key, ('xy', target.comparison_id, ''))
        np.testing.assert_array_equal(graph.axes.lines[0].get_ydata(), session.base_cube.z[0])
        self.assertTrue(self.w.request_outer_view('analysis'))
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Routine modal')):
            self.w.add_gate_comparison_btn.trigger()
        self.assertEqual(len(target.curves), 1)
        self.assertIn('skipped 1', self.w.statusText)
        self.assertEqual(self.w.outerView, 'analysis')
        self.assertEqual(self.w.workspace.active_dataset_id, session.dataset_id)
        self.w.processing.setMode('energy_second_derivative')
        self.w.processing.setSmoothing(False)
        self.w.processing.apply()
        wait_jobs(self.w)
        self.w.add_gate_comparison_btn.trigger()
        self.assertEqual(self.w.graph_documents.registry.active_key, (session.dataset_id, session.active_graph_id))
        self.assertEqual(len(target.curves), 2)
        np.testing.assert_array_equal(graph.axes.lines[1].get_ydata(), session.current_cube.z[0])
        curves = list(target.curves.values())
        self.assertEqual([self.w.workspace.sheets[c.sheet_id].role for c in curves], ['original', 'processed'])
        self.w.add_gate_again_action.trigger()
        self.assertEqual(len(target.curves), 3)
        self.assertEqual(self.w.graph_documents.registry.active_key, (session.dataset_id, session.active_graph_id))
        self.assertFalse(self.errors)

    def test_deleted_pin_can_choose_replacement_from_quick_narrow_commands(self):
        book, page, a, b, commands = self.targets()
        remove_comparison(self.w.workspace, a.comparison_id)
        self.w._refresh_auxiliary_documents()
        self.w._auxiliary_changed()
        command_root = item(self.w, 'workbookChooseTarget').parentItem().parentItem()
        self.assertIs(command_root.property('bridge'), commands)
        # Assign through the actual QML component so the Loader's width binding
        # cannot immediately undo the original narrow-command fixture.
        loader = command_root.parentItem()
        self.assertTrue(loader.inherits('QQuickLoader'))
        expression = QQmlExpression(QQmlEngine.contextForObject(loader), loader, 'width = 320')
        expression.evaluate()
        self.assertFalse(expression.hasError(), expression.error().toString())
        pump()
        self.assertEqual(command_root.width(), 320)
        self.assertFalse(bool(commands.ready and commands.hasTarget))
        chooser = item(self.w, 'workbookChooseTarget')
        self.assertTrue(chooser.isVisible())
        self.assertTrue(chooser.isEnabled())
        self.assertLessEqual(chooser.x() + chooser.width(), command_root.width())
        click_item(self.w, 'workbookChooseTarget')
        click_item(self.w, 'workbookTarget_' + b.comparison_id)
        self.assertEqual(commands.targetId, b.comparison_id)
        self.assertTrue(commands.targetPinned)
        click_item(self.w, 'workbookAppend')
        self.assertEqual(len(b.curves), 2)

    def test_no_original_workbook_reports_feedback_and_preserves_result_choice(self):
        book, page = self.xy()
        sheet = self.w.workspace.sheets[page.sheet_id]
        sheet.role = 'snapshot'
        self.w._refresh_data_navigator()
        self.w.app_bridge.project.select_key(('workbook', book.workbook_id))
        self.assertEqual(self.w._selected_sheet_ids(), [])
        self.assertTrue(self.w.compare_sheets_btn.isEnabled())
        self.w.compare_sheets_btn.trigger()
        self.assertIn('no Original', self.w.statusText)
        dialog = SheetComparisonDialog(self.w.workspace)
        self.addCleanup(dialog.deleteLater)
        dialog.bridge.toggleSource(dialog.bridge.sourceIdAt(0))
        self.assertIn(sheet.sheet_id, dialog.selected_mappings())
        self.assertTrue(dialog.bridge.canAccept)
        dialog.bridge.selectOriginals()
        self.assertFalse(dialog.selected_mappings())
        self.assertFalse(dialog.bridge.canAccept)


if __name__ == '__main__':
    unittest.main()
