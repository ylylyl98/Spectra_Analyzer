import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import time
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt, QTimer, QSignalBlocker, QItemSelection, QItemSelectionModel
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickView
from spectra_app.sheet_comparison_dialog import SheetComparisonDialog

from spectra_app.common_prompts import PromptAction
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as quick
from tests.project_helpers import click_project_row, project_control, project_row, settle_project

from tests.xy_helpers import set_xy_draft, xy_value

from tests.xy_canvas_quick_helpers import click_named as click_xy_command, item as xy_command_item

def set_sheet_destination(dialog, comparison_id):
    dialog.bridge.setDestination(next(i for i, row in enumerate(dialog.bridge.destinations)
                                      if row['value'] == (comparison_id or '')))


def set_sheet_mapping(dialog, sheet_id, x, ys, *, include=True):
    bridge = dialog.bridge
    bridge.selectSource(sheet_id)
    if (sheet_id in dialog.selected_mappings()) != include:
        bridge.toggleSource(sheet_id)
    bridge.setX(x)
    for row in list(bridge._column_model.rows):
        bridge.setY(row['column'], row['column'] in ys)


def set_inactive_xy_fixture_draft(page, field, value):
    # Canonical fixture setup for the original programmatic inactive editor.
    # Native input remains guarded and never changes the selected document.
    owner = page.parent()
    key = ('xy', page.comparison_id, '')
    assert owner.graph_documents.registry.active_key != key
    assert owner.workspace.comparisons[page.comparison_id] is page.comparison
    bridge = page.inspector_bridge
    if value == bridge.canonical_values()[field]:
        bridge._drafts.pop(field, None)
    else:
        bridge._drafts[field] = value
    bridge.changed.emit()
    page._draft_changed()
    assert bridge.values_map()[field] == value


class SheetBatchComparisonTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.errors = self.quick_errors
        self.addCleanup(lambda: self.assertFalse(self.errors))

    def populate(self):
        # Each row/grid is deliberate: batch plotting must never align by index.
        books = [create_workbook(self.window.workspace, name) for name in ('Alpha', 'Beta')]
        arrays = ([[10, 2, 30], [20, 1, 40]], [[4, 50], [8, 60], [12, 70]],
                  [[9, 8, .5]], [[11, 3], [12, 2]], [[7, 90], [6, 80]])
        names = (['a', 'time', 'b'], ['time', 'a'], ['a', 'b', 'time'], ['a', 'time'], ['time', 'a'])
        sheets = []
        for i, (values, columns) in enumerate(zip(arrays, names)):
            path = self.folder / f'source-{i}.csv'
            path.write_text('external source', encoding='utf-8')
            sheet = register_xy_sheet(self.window.workspace, books[0 if i < 3 else 1].workbook_id,
                f'Sample {i+1}', np.array(values, dtype=float), columns, source_path=path)
            sheets.append(sheet)
        self.window._auxiliary_changed()
        return books, sheets

    def configure(self, dialog, sheet, x, ys, mode='raw', include=True):
        self.assertEqual(sheet.mode, mode)
        set_sheet_mapping(dialog, sheet.sheet_id, x, ys, include=include)

    def modal(self, trigger, action):
        failures = []
        deadline = time.monotonic() + 8
        completed = []
        def interact():
            dialog = next((owner for owner in self.host.window.findChildren(SheetComparisonDialog)
                           if owner.window().isVisible()), None)
            if dialog is None and time.monotonic() < deadline:
                QTimer.singleShot(10, interact)
                return
            try:
                self.assertIsInstance(dialog, SheetComparisonDialog)
                self.assertIsInstance(dialog.window(), QQuickView)
                self.assertIs(dialog.parent(), self.host.window)
                self.assertIs(QGuiApplication.modalWindow(), dialog.window())
                completed.append(dialog)
                action(dialog)
            except BaseException as exc:
                failures.append(exc)
                if dialog:
                    dialog.reject()
        timer = QTimer(self.window)
        timer.setSingleShot(True)
        timer.timeout.connect(interact)
        timer.start(0)
        try:
            trigger()
        finally:
            timer.stop()
            timer.deleteLater()
        if failures:
            raise failures[0]
        self.assertTrue(completed, 'Expected a real Sheet comparison dialog')

    def show_navigator(self):
        self.host.show()
        self.window.project_panel_action.setChecked(True)
        quick.pump()

    def test_ctrl_shift_batch_dialog_creates_one_graph_with_independent_arrays(self):
        books, sheets = self.populate()
        self.show_navigator()
        def click(sheet, modifiers=Qt.NoModifier):
            click_project_row(self.window, ('sheet', sheet.sheet_id), modifiers)
        click(sheets[0])
        click(sheets[2], Qt.ShiftModifier)
        click(sheets[3], Qt.ControlModifier)
        click(sheets[4], Qt.ControlModifier)
        self.assertEqual({key for key in self.window.project.selected_keys() if key[0] != 'group'},
                         {('sheet', sheet.sheet_id) for sheet in sheets},
                         'Navigator must support Ctrl/Shift across Workbooks')
        self.assertFalse(self.window.graph_documents.keys(), 'Sources need not be opened')
        def configure(dialog):
            self.assertEqual(list(dialog.selected_mappings()), [sheet.sheet_id for sheet in sheets])
            for sheet, x, ys in zip(sheets, [1, 0, 2, 1, 0], [[0,2], [1], [0], [0], [1]]):
                self.configure(dialog, sheet, x, ys)
            self.assertIn('6', dialog.bridge.summary)
            dialog.accept()
        self.modal(self.window.compare_sheets_btn.trigger, configure)
        self.assertFalse(self.errors)
        self.assertEqual(len(self.window.workspace.comparisons), 1)
        graph = self.window.graph_documents.document_for(self.window.graph_documents.registry.active_key)
        expected = [([2,1], [10,20]), ([2,1], [30,40]), ([4,8,12], [50,60,70]),
                    ([.5], [9]), ([3,2], [11,12]), ([7,6], [90,80])]
        self.assertEqual(len(graph.axes.lines), len(expected))
        for line, (x,y) in zip(graph.axes.lines, expected):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)
        saved = self.window.save_project(self.folder / 'batch.spectra')
        for sheet in sheets:
            sheet.source_path.unlink()
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Clean saved project unexpectedly requested discard')):
            self.assertTrue(self.window.open_project(saved))
        graph = self.window.graph_documents.document_for(self.window.graph_documents.registry.active_key)
        self.assertEqual(len(graph.axes.lines), 6)
        self.assertEqual(self.window.workspace.sheets[sheets[0].sheet_id].y_columns, [0,2])
        for line, (x,y) in zip(graph.axes.lines, expected):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)

    def test_graph_add_has_fixed_owner_even_when_navigator_and_last_target_are_other(self):
        self.host.window.resize(1500, 920)
        self.host.show()
        quick.pump()
        books, sheets = self.populate()
        a = create_comparison(self.window.workspace, 'A')
        b = create_comparison(self.window.workspace, 'B')
        page = self.window.open_xy_comparison(a.comparison_id)
        self.assertTrue(xy_command_item(self.window, 'XYAddDataButton').isVisible())
        self.window._refresh_data_navigator()
        self.window.project.select_key(('xy', b.comparison_id))
        self.assertEqual(self.window._last_comparison_id, b.comparison_id)
        def choose(dialog):
            self.window._last_comparison_id = b.comparison_id
            self.assertEqual(dialog.comparison_id, a.comparison_id)
            self.assertFalse(not dialog.bridge.fixedDestination)
            self.assertFalse(dialog.selected_mappings())
            self.configure(dialog, sheets[0], 1, [0,2])
            self.configure(dialog, sheets[1], 0, [1])
            dialog.accept()
        self.modal(lambda: click_xy_command(self.window, 'XYAddDataButton'), choose)
        self.assertFalse(self.errors)
        self.assertEqual(len(a.curves), 3)
        self.assertFalse(b.curves)
        self.assertEqual(len(page.axes.lines), 3)
        self.assertEqual(self.window.graph_documents.registry.active_key, ('xy', a.comparison_id, ''))
        self.modal(lambda: click_xy_command(self.window, 'XYAddDataButton'), lambda dialog: (self.assertFalse(dialog.selected_mappings()), dialog.reject()))
        self.assertEqual(len(a.curves), 3)

    def test_cancel_stages_mapping_without_dirtying_or_opening_documents(self):
        books, sheets = self.populate()
        self.assertTrue(hasattr(self.window, 'compare_sheets_btn'))
        self.window.save_project(self.folder / 'clean.spectra')
        before = copy.deepcopy([(s.x_column, s.y_columns, s.mode) for s in sheets])
        self.window.project.select_key(('sheet', sheets[0].sheet_id))
        def cancel(dialog):
            self.configure(dialog, sheets[0], 1, [0,2])
            QTest.keyClick(dialog.window(), Qt.Key_Escape)
        self.modal(self.window.compare_sheets_btn.trigger, cancel)
        self.window._refresh_project_modified()
        self.assertFalse(self.window.projectModified)
        self.assertFalse(self.window.workspace.comparisons)
        self.assertFalse(self.window.graph_documents.keys())
        self.assertEqual(before, [(s.x_column, s.y_columns, s.mode) for s in sheets])

    def test_invalid_and_stale_sources_targets_and_busy_reject_whole_batch(self):
        _, sheets = self.populate()
        self.assertTrue(hasattr(self.window, 'compare_sheets'))
        before = copy.deepcopy([(s.x_column, s.y_columns, s.mode) for s in sheets])
        self.window.save_project(self.folder / 'validation-clean.spectra')
        first = {sheets[0].sheet_id: (1, [0,2], 'raw')}
        for invalid in ((0, [], 'raw'), (0, [0], 'raw'), (0, [99], 'raw'), (0, [1], 'processed')):
            with self.assertRaisesRegex(ValueError, 'Sample 2'):
                self.window.compare_sheets({**first, sheets[1].sheet_id: invalid})
        with self.assertRaisesRegex(ValueError, 'missing|exist|available'):
            self.window.compare_sheets({**first, 'removed-sheet': (0,[1],'raw')})
        with self.assertRaisesRegex(ValueError, 'comparison|target'):
            self.window.compare_sheets(first, 'removed-target')
        with patch.object(self.window, '_dat_import_pending', True):
            with self.assertRaisesRegex(ValueError, 'Wait'):
                self.window.compare_sheets(first)
        self.assertFalse(self.window.workspace.comparisons)
        self.assertFalse(self.window.graph_documents.keys())
        self.assertEqual(before, [(s.x_column, s.y_columns, s.mode) for s in sheets])
        self.window._refresh_project_modified()
        self.assertFalse(self.window.projectModified)

    def test_invalid_target_draft_retained_and_existing_curves_styles_unchanged(self):
        books, sheets = self.populate()
        self.assertTrue(hasattr(self.window, 'compare_sheets'))
        target = create_comparison(self.window.workspace, 'Target')
        old = add_curve(self.window.workspace, target.comparison_id, sheets[0].sheet_id, 0, 1,
                        color='#123456', width=2.25, visible=False, label='Retain me')
        page = self.window.open_xy_comparison(target.comparison_id)
        self.window.open_workbook(books[0].workbook_id)
        set_inactive_xy_fixture_draft(page, 'color', 'invalid')
        mapping = {sheets[0].sheet_id: (1, [0,2], 'raw')}
        with self.assertRaisesRegex(ValueError, 'six-digit'):
            self.window.compare_sheets(mapping, target.comparison_id)
        self.assertEqual(xy_value(page, 'color'), 'invalid')
        self.assertEqual(sheets[0].x_column, 0)
        self.assertEqual(len(target.curves), 1)
        set_inactive_xy_fixture_draft(page, 'color', '#123456')
        retained = copy.deepcopy(old)
        self.window.compare_sheets(mapping, target.comparison_id)
        self.assertEqual(old, retained)
        self.assertEqual(len(target.curves), 3)
        self.assertEqual(self.window.graph_documents.registry.active_key, ('workbook', books[0].workbook_id, ''))

    def test_navigator_refresh_and_context_keep_selection_and_guard_destructive_actions(self):
        books, sheets = self.populate()
        project = self.window.project
        self.show_navigator()
        first = ('sheet', sheets[0].sheet_id)
        second = ('sheet', sheets[1].sheet_id)
        project.select_key(first)
        project.select_key(second, Qt.ControlModifier)
        project.select_key(first, context=True)
        project.set_expanded(('workbook', books[1].workbook_id), False)
        keys = set(project.selected_keys())
        self.window._refresh_data_navigator()
        self.assertEqual(set(project.selected_keys()), keys)
        self.assertFalse(project.is_expanded(('workbook', books[1].workbook_id)))
        self.assertEqual(project.current_key(), first)
        click_project_row(self.window, second, button=Qt.RightButton)
        context = project_control(self.window, 'projectContextMenu')
        self.assertTrue(context.property('visible'))
        checked = {action.key: action.enabled for action in project.contextActions}
        self.assertFalse(checked['remove'])
        self.assertFalse(checked['rename'])
        self.assertTrue(checked['compare'])
        self.assertEqual(set(project.selected_keys()), keys)
        QTest.keyClick(self.host.window, Qt.Key_Escape)
        with patch('spectra_app.main_window.prompt_message') as warning:
            self.window._remove_navigator_item()
            warning.assert_not_called()
        with patch('spectra_app.main_window.prompt_text') as rename:
            self.window._rename_navigator_item()
            rename.assert_not_called()
        self.assertEqual(len(self.window.workspace.sheets), 5)
        click_project_row(self.window, ('sheet', sheets[0].sheet_id))
        with patch('spectra_app.main_window.prompt_text', return_value=('Renamed', True)):
            self.window._rename_navigator_item()
        self.assertEqual(sheets[0].name, 'Renamed')
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.YES):
            self.window._remove_navigator_item()
        self.assertNotIn(sheets[0].sheet_id, self.window.workspace.sheets)

    def test_selected_workbook_deduplicates_children_in_project_order(self):
        books, sheets = self.populate()
        self.assertTrue(hasattr(self.window, '_selected_sheet_ids'))
        project = self.window.project
        project.select_key(('sheet', sheets[4].sheet_id))
        project.select_key(('workbook', books[0].workbook_id), Qt.ControlModifier)
        project.select_key(('sheet', sheets[0].sheet_id), Qt.ControlModifier)
        comparison = create_comparison(self.window.workspace, 'Ignored graph')
        self.window._refresh_data_navigator()
        project.select_key(('xy', comparison.comparison_id), Qt.ControlModifier)
        self.assertEqual(self.window._selected_sheet_ids(), [s.sheet_id for s in sheets[:3]] + [sheets[4].sheet_id])

    def test_metadata_refresh_preserves_scrolled_navigator(self):
        books, sheets = self.populate()
        # The persistent Project pane has more tree height than the old
        # command-heavy sidebar. Populate enough rows to require scrolling.
        for i in range(95):
            register_xy_sheet(self.window.workspace, books[0].workbook_id, f'Extra source {i}',
                              np.array([[1.,2.]]), ['x','y'])
        self.window._refresh_data_navigator()
        self.show_navigator()
        project = self.window.project
        key = ('sheet', books[0].sheet_ids[95])
        project.select_key(key)
        project_row(self.window, key)
        project.set_expanded(('workbook', books[1].workbook_id), False)
        settle_project(self.window)
        view = project_control(self.window, 'projectList')
        scroll = view.property('contentY')
        self.assertGreater(scroll, 0)
        self.window._auxiliary_changed()
        settle_project(self.window)
        self.assertEqual(view.property('contentY'), scroll)
        self.assertEqual(project.current_key(), key)
        self.assertFalse(project.is_expanded(('workbook', books[1].workbook_id)))

    def test_append_dialog_preserves_source_selection_closed_target_and_unrelated_draft(self):
        books, sheets = self.populate()
        target = create_comparison(self.window.workspace, 'Append here')
        other = create_comparison(self.window.workspace, 'Keep draft')
        graph = self.window.open_xy_comparison(target.comparison_id)
        unrelated = self.window.open_xy_comparison(other.comparison_id)
        workbook = self.window.open_workbook(books[0].workbook_id)
        page = workbook.sheet_pages[sheets[0].sheet_id]
        page.selectColumn(1)
        page.setCurrentCell(1, 1)
        selection = [(i.row(), i.column()) for i in page.selection_model.selectedIndexes()]
        set_inactive_xy_fixture_draft(unrelated, 'name', '')
        self.window.graph_documents.close_document(('xy', target.comparison_id, ''))
        self.window.project.select_key(('sheet', sheets[0].sheet_id))
        def append(dialog):
            self.configure(dialog, sheets[0], 1, [0,2])
            # An edited unchecked Sheet must retain its old defaults.
            self.configure(dialog, sheets[2], 2, [0], include=False)
            set_sheet_destination(dialog, target.comparison_id)
            dialog.accept()
        self.modal(self.window.compare_sheets_btn.trigger, append)
        self.assertFalse(self.errors)
        self.assertEqual(len(target.curves), 2)
        self.assertEqual(len(graph.axes.lines), 2)
        self.assertNotIn(('xy', target.comparison_id, ''), self.window.graph_documents.registry.open_keys())
        self.assertIsNone(self.window.graph_documents.content_item(('xy', target.comparison_id, '')))
        self.assertEqual(self.window.graph_documents.registry.active_key, ('workbook', books[0].workbook_id, ''))
        self.assertEqual([(i.row(), i.column()) for i in page.selection_model.selectedIndexes()], selection)
        self.assertEqual(page.currentColumn, 1)
        self.assertEqual(page.xColumn, 1)
        self.assertIn('(X)', page.model.headerData(1, Qt.Horizontal))
        self.assertEqual(sheets[2].x_column, 0)
        self.assertEqual(xy_value(unrelated, 'name'), '')
        set_inactive_xy_fixture_draft(unrelated, 'name', 'Keep draft')

    def test_dialog_validation_no_y_stale_source_and_stale_target_cannot_accept(self):
        _, sheets = self.populate()
        from spectra_app.sheet_comparison_dialog import SheetComparisonDialog
        target = create_comparison(self.window.workspace, 'Target')
        dialog = SheetComparisonDialog(self.window.workspace, selected_sheet_ids=[sheets[0].sheet_id])
        self.addCleanup(dialog.deleteLater)
        self.configure(dialog, sheets[0], 1, [])
        self.assertFalse(dialog.bridge.canAccept)
        self.assertIn('Sample 1', dialog.bridge.feedback)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Rejected)
        self.configure(dialog, sheets[0], 1, [0,2])
        self.assertTrue(dialog.bridge.canAccept)
        set_sheet_destination(dialog, target.comparison_id)
        del self.window.workspace.comparisons[target.comparison_id]
        dialog.accept()
        self.assertIn('comparison', dialog.bridge.feedback)
        self.assertEqual(dialog.result(), dialog.Rejected)
        dialog.bridge.setDestination(0)
        saved_sheet = self.window.workspace.sheets.pop(sheets[0].sheet_id)
        try:
            dialog.accept()
            self.assertIn('Sample 1', dialog.bridge.feedback)
            self.assertIn('no longer exists', dialog.bridge.feedback)
            self.assertEqual(dialog.result(), dialog.Rejected)
        finally:
            self.window.workspace.sheets[saved_sheet.sheet_id] = saved_sheet
        self.assertFalse(self.window.workspace.comparisons)
        self.assertEqual(sheets[0].x_column, 0)

    def test_preflight_invalid_target_does_not_commit_current_graph_draft(self):
        _, sheets = self.populate()
        a = create_comparison(self.window.workspace, 'Current')
        b = create_comparison(self.window.workspace, 'Invalid target')
        target = self.window.open_xy_comparison(b.comparison_id)
        current = self.window.open_xy_comparison(a.comparison_id)
        set_inactive_xy_fixture_draft(target, 'name', '')
        set_xy_draft(current, 'name', 'Uncommitted current name')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.window.compare_sheets({sheets[0].sheet_id: (1,[0],'raw')}, b.comparison_id)
        self.assertEqual(a.name, 'Current')
        self.assertEqual(xy_value(current, 'name'), 'Uncommitted current name')
        self.assertEqual(xy_value(target, 'name'), '')
        self.assertFalse(a.curves)
        self.assertFalse(b.curves)
        self.assertEqual(sheets[0].x_column, 0)
        set_inactive_xy_fixture_draft(target, 'name', 'Invalid target')

    def test_mixed_dat_modes_use_own_gate_labels_preview_and_keep_scientific_context(self):
        self.host.window.resize(1500, 920)
        self.host.show()
        quick.pump()
        _, sheets = self.populate()
        dat_sheets = []
        sessions = []
        for i, header in enumerate(('Energy(eV) -2 4', 'Energy(eV) 7 11')):
            path = self.folder / f'gates-{i}.dat'
            path.write_text(header + '\n' + '\n'.join(f'{1+j/10} {j+1} {2*j+2}' for j in range(13)), encoding='utf-8')
            self.window._load_dat_file_direct(path)
            session = self.window.workspace.active
            sessions.append(session)
            dat_sheets.append(next(s for s in self.window.workspace.sheets.values() if s.dataset_id == session.dataset_id))
        # An inactive cached DAT table has a selected Gate cell. Refresh must not
        # emit a current-column request that activates this dataset or its Gate.
        first_book = self.window._sheet_owner(dat_sheets[0].sheet_id)
        cached = self.window.open_workbook(first_book.workbook_id).sheet_pages[dat_sheets[0].sheet_id]
        self.window._replace_current_data(replace(sessions[1].current_cube, z=sessions[1].current_cube.z * 3),
                                         mode='energy_second_derivative', smooth=False, window_length=5)
        dat_sheets[1] = next(s for s in self.window.workspace.sheets.values()
                             if s.dataset_id == sessions[1].dataset_id and s.role == 'processed')
        with QSignalBlocker(cached.selection_model):
            # Arrange the original inactive-table selection directly in its
            # canonical model; user input may only target the active document.
            cached.selection_model.setCurrentIndex(cached.model.index(3, 2), QItemSelectionModel.NoUpdate)
            column = QItemSelection(cached.model.index(0, 2),
                                    cached.model.index(cached.model.rowCount()-1, 2))
            cached.selection_model.select(column, QItemSelectionModel.ClearAndSelect)
        self.assertEqual(cached.currentColumn, 2)
        target = create_comparison(self.window.workspace, 'Mixed sources')
        graph = self.window.open_xy_comparison(target.comparison_id)
        active = self.window.workspace.active_dataset_id
        gate = self.window._current_gate_index()
        current_cube = self.window.current_cube
        expected_raw = sessions[0].base_cube.z[1].copy()
        expected_applied = sessions[1].current_cube.z[0].copy()
        def choose(dialog):
            self.configure(dialog, sheets[0], 1, [0])
            self.assertEqual(dialog.bridge.valueLabel, "Original")
            self.configure(dialog, dat_sheets[0], 0, [2], 'raw')
            self.assertFalse(dialog.bridge.xEditable)
            self.assertIn('4', dialog.bridge._column_model.rows[2]['label'])
            model = dialog.bridge.previewModel
            self.assertLessEqual(model.rowCount(), 100)
            np.testing.assert_array_equal(model.values[2], expected_raw[:100])
            self.assertTrue(np.shares_memory(model.values[2], sessions[0].base_cube.z))
            self.configure(dialog, dat_sheets[1], 0, [1], 'processed')
            self.assertIn('7', dialog.bridge._column_model.rows[1]['label'])
            np.testing.assert_array_equal(dialog.bridge.previewModel.values[1], expected_applied[:100])
            self.assertEqual(self.window.workspace.active_dataset_id, active)
            self.assertEqual(self.window._current_gate_index(), gate)
            dialog.accept()
        self.modal(lambda: click_xy_command(self.window, 'XYAddDataButton'), choose)
        self.assertFalse(self.errors)
        self.assertEqual(len(graph.axes.lines), 3)
        np.testing.assert_array_equal(graph.axes.lines[0].get_xdata(), [2,1])
        np.testing.assert_array_equal(graph.axes.lines[1].get_ydata(), expected_raw)
        np.testing.assert_array_equal(graph.axes.lines[2].get_ydata(), expected_applied)
        self.assertEqual(self.window.workspace.active_dataset_id, active)
        self.assertEqual(self.window._current_gate_index(), gate)
        self.assertIs(self.window.current_cube, current_cube)
        self.assertEqual(cached.currentColumn, 2)
        self.assertEqual(self.window.graph_documents.registry.active_key, ('xy', target.comparison_id, ''))


if __name__ == '__main__':
    unittest.main()
