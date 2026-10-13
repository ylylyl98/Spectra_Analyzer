import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from spectra_app.main_window import MainWindow
from spectra_app.workbook_state import (create_comparison, add_curve, save_sheet_snapshot,
    create_workbook, register_xy_sheet, select_curve_additions, ensure_dataset_sheet)
from tests.settings_helpers import isolate_settings
from tests.background_helpers import wait_jobs

APP = QApplication.instance() or QApplication([])


class ImportExistingComparisonTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.window = MainWindow()
        self.errors = []
        self.callback_errors = []
        self.window._show_error = self.errors.append
        hook = patch.object(sys, 'excepthook', lambda *args: self.callback_errors.append(args[1]))
        hook.start()
        self.addCleanup(hook.stop)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.close()
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertFalse(self.callback_errors)

    def files(self):
        paths = [self.folder / name for name in ('first.csv', 'second.csv')]
        for path, text in zip(paths, ('signal,time\n10,2\n20,1\n', 'time,signal\n4,50\n8,60\n12,70\n')):
            path.write_text(text, encoding='utf-8')
        return paths

    def target(self):
        comparison = create_comparison(self.window.workspace, 'Named target')
        return comparison, self.window.open_xy_comparison(comparison.comparison_id)

    def modal(self, trigger, action):
        failures = []
        def interact():
            dialog = APP.activeModalWidget()
            if dialog is None and self.window._background_jobs.busy:
                QTimer.singleShot(10, interact)
                return
            try:
                self.assertIsInstance(dialog, QDialog)
                action(dialog)
            except BaseException as exc:
                failures.append(exc)
                if dialog:
                    dialog.reject()
        QTimer.singleShot(0, interact)
        trigger()
        wait_jobs(self.window)
        if failures:
            raise failures[0]
        self.assertFalse(self.callback_errors)

    def test_graph_file_action_maps_each_source_and_returns_to_target_without_workbook_view(self):
        paths = self.files()
        target, graph = self.target()
        self.assertTrue(hasattr(graph, 'add_files_action'), 'Add data must offer From files')
        def configure(dialog):
            self.assertEqual(dialog.comparison_combo.currentData(), target.comparison_id)
            self.assertFalse(dialog.comparison_combo.isEnabled())
            self.assertFalse(dialog.include_existing_chk.isChecked())
            dialog.x_combo.setCurrentIndex(1)
            dialog.y_list.item(0).setCheckState(Qt.Checked)
            dialog.file_list.setCurrentRow(1)
            dialog.compare_btn.click()
        with patch('spectra_app.main_window.QFileDialog.getOpenFileNames', return_value=(paths, '')):
            self.modal(graph.add_files_action.trigger, configure)
        self.assertFalse(self.errors)
        self.assertEqual(len(self.window.workspace.workbooks), 2)
        self.assertEqual(self.window.graph_documents.keys(), (('xy', target.comparison_id, ''),))
        self.assertEqual(len(graph.axes.lines), 2)
        for line, x, y in zip(graph.axes.lines, ([2, 1], [4, 8, 12]), ([10, 20], [50, 60, 70])):
            np.testing.assert_array_equal(line.get_xdata(), x)
            np.testing.assert_array_equal(line.get_ydata(), y)
        self.assertEqual([curve.label for curve in target.curves.values()], ['first.csv: signal', 'second.csv: signal'])

    def test_import_existing_defaults_skip_and_explicit_repeat_preserves_old_curves(self):
        paths = self.files()
        target, graph = self.target()
        book = self.window.import_xy_files(paths, comparison_id=target.comparison_id, mappings=[(1, [0]), (0, [1])])
        first = next(iter(target.curves.values()))
        first.label, first.color, first.visible = 'Custom', '#123456', False
        graph.refresh()
        saved = self.window.save_project(self.folder / 'retained.spectra')
        before = copy.deepcopy(target)
        paths[0].write_text('signal,time\n900,800\n700,600\n', encoding='utf-8')
        result = self.window.import_xy_files(paths, comparison_id=target.comparison_id, mappings=[(1, [0]), (0, [1])])
        self.assertIs(result, book)
        self.assertEqual(target, before)
        np.testing.assert_array_equal(self.window.workspace.sheets[first.sheet_id].data, [[10, 2], [20, 1]])
        self.window._refresh_project_modified()
        self.assertFalse(self.window.isWindowModified())
        self.assertIn('Added 0', self.window.statusBar().currentMessage())
        self.assertIn('skipped 2', self.window.statusBar().currentMessage())
        self.window.import_xy_files(paths, comparison_id=target.comparison_id, mappings=[(1, [0]), (0, [1])], include_existing=True)
        self.assertEqual(len(target.curves), 4)
        self.assertEqual(target.curves[first.curve_id], before.curves[first.curve_id])
        saved = self.window.save_project(saved)
        for path in paths:
            path.unlink()
        self.assertTrue(self.window.open_project(saved))
        self.assertEqual(len(self.window.workspace.comparisons[target.comparison_id].curves), 4)

    def test_invalid_target_draft_does_not_import_or_commit_current_draft(self):
        paths = self.files()
        target, graph = self.target()
        current = create_comparison(self.window.workspace, 'Other')
        active = self.window.open_xy_comparison(current.comparison_id)
        active.name_edit.setText('Uncommitted valid name')
        graph.name_edit.setText(' ')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.window.import_xy_files(paths, comparison_id=target.comparison_id)
        self.assertFalse(self.window.workspace.workbooks)
        self.assertFalse(self.window.workspace.sheets)
        self.assertEqual(current.name, 'Other')
        self.assertEqual(graph.name_edit.text(), ' ')

    def test_batch_skip_does_not_commit_drafts_or_change_mapping(self):
        book = self.window.import_xy_files(self.files()[:1])
        sheet = self.window.workspace.sheets[book.sheet_ids[0]]
        target = self.window.compare_sheets({sheet.sheet_id: (0, [1], 'raw')})
        graph = self.window.open_xy_comparison(target.comparison_id)
        graph.name_edit.setText('Draft')
        before = copy.deepcopy(target)
        self.window.compare_sheets({sheet.sheet_id: (0, [1], 'raw')}, target.comparison_id)
        self.assertEqual(target, before)
        self.assertEqual(graph.name_edit.text(), 'Draft')

    def test_global_named_destination_and_dialog_repeat_checkbox(self):
        paths = self.files()[:1]
        target, graph = self.target()
        other = create_comparison(self.window.workspace, 'Other target')
        def configure(dialog, repeat=False):
            dialog.comparison_combo.setCurrentIndex(dialog.comparison_combo.findData(target.comparison_id))
            self.window._last_comparison_id = other.comparison_id
            dialog.include_existing_chk.setChecked(repeat)
            dialog.compare_btn.click()
        with patch('spectra_app.main_window.QFileDialog.getOpenFileNames', return_value=(paths, '')):
            for repeat, expected in ((False, 1), (False, 1), (True, 2)):
                self.modal(self.window._on_import_xy, lambda dialog: configure(dialog, repeat))
                self.assertEqual(len(target.curves), expected)
                self.assertFalse(other.curves)
        self.assertFalse(self.errors)

    def test_batch_dialog_checkbox_skip_and_repeat(self):
        book = self.window.import_xy_files(self.files()[:1])
        sheet_id = book.sheet_ids[0]
        target = self.window.compare_sheets({sheet_id: (0, [1], 'raw')})
        graph = self.window.open_xy_comparison(target.comparison_id)
        def configure(dialog, repeat):
            item = dialog.source_tree.topLevelItem(0).child(0)
            item.setCheckState(0, Qt.Checked)
            dialog.include_existing_chk.setChecked(repeat)
            dialog.commit_btn.click()
        for repeat, expected in ((False, 1), (True, 2)):
            self.modal(graph.add_data_btn.click, lambda dialog: configure(dialog, repeat))
            self.assertEqual(len(target.curves), expected)
        self.assertFalse(self.errors)

    def test_direct_sheet_duplicate_choice_skip_repeat_cancel_and_closed_target(self):
        book = self.window.import_xy_files(self.files()[:1])
        sheet_id = book.sheet_ids[0]
        target = self.window.compare_sheets({sheet_id: (0, [1], 'raw')})
        graph = self.window.open_xy_comparison(target.comparison_id)
        page = self.window.open_workbook(book.workbook_id).sheet_pages[sheet_id]
        self.window.graph_documents.close_document(('xy', target.comparison_id, ''))
        def choose(dialog, text):
            self.assertIsInstance(dialog, QMessageBox)
            self.assertEqual(dialog.defaultButton().text(), 'Skip existing')
            next(button for button in dialog.buttons() if button.text() == text).click()
        for choice, expected in (('Skip existing', 1), ('Cancel', 1), ('Add again', 2)):
            self.modal(page.add_comparison_btn.click, lambda dialog: choose(dialog, choice))
            self.assertEqual(len(target.curves), expected)
            self.assertEqual(self.window.graph_documents._active_key, ('workbook', book.workbook_id, ''))
            self.assertIsNone(self.window.graph_documents.window_for(('xy', target.comparison_id, '')))
        self.assertEqual(len(graph.axes.lines), 2)
        self.assertFalse(self.errors)

    def test_same_basename_snapshot_embedded_and_explicit_labels(self):
        paths = []
        for folder in ('run-A', 'run-B'):
            path = self.folder / folder / 'signal.csv'
            path.parent.mkdir()
            path.write_text('x,y\n1,2\n', encoding='utf-8')
            paths.append(path)
        book = self.window.import_xy_files(paths, compare=True)
        target = next(iter(self.window.workspace.comparisons.values()))
        labels = [curve.label for curve in target.curves.values()]
        self.assertEqual(labels, ['signal.csv [run-A]: y', 'signal.csv [run-B]: y'])
        snapshot = save_sheet_snapshot(self.window.workspace, book.sheet_ids[0], 'Frozen run')
        curve = add_curve(self.window.workspace, target.comparison_id, snapshot.sheet_id, 0, 1)
        self.assertIn('Frozen run', curve.label)
        owner = create_workbook(self.window.workspace, 'Calibration')
        embedded = register_xy_sheet(self.window.workspace, owner.workbook_id, 'Original', np.array([[1., 2.]]), ['x', 'response'])
        curve = add_curve(self.window.workspace, target.comparison_id, embedded.sheet_id, 0, 1)
        self.assertEqual(curve.label, 'Calibration: response')
        explicit = add_curve(self.window.workspace, target.comparison_id, embedded.sheet_id, 0, 1, label='')
        self.assertEqual(explicit.label, '')
        self.assertNotEqual(curve.curve_id, explicit.curve_id)

    def test_relative_same_basename_labels_do_not_probe_or_change_source_paths(self):
        workspace = self.window.workspace
        book = create_workbook(workspace, 'Relative sources')
        paths = [Path('same.csv'), Path('other/same.csv')]
        sheets = [register_xy_sheet(workspace, book.workbook_id, 'Original',
                  np.array([[1., 2.]]), ['x', 'y'], source_path=path) for path in paths]
        target = create_comparison(workspace, 'Relative comparison')
        with patch.object(Path, 'exists', side_effect=AssertionError('Do not probe provenance')):
            curves = [add_curve(workspace, target.comparison_id, sheet.sheet_id, 0, 1) for sheet in sheets]
        self.assertNotEqual(curves[0].label, curves[1].label)
        self.assertIn('same.csv [', curves[0].label)
        self.assertIn('[other]', curves[1].label)
        self.assertEqual([sheet.source_path for sheet in sheets], paths)

    def test_identity_keeps_alternate_columns_snapshots_and_repeated_batch_references(self):
        path = self.folder / 'many.csv'
        path.write_text('x,a,b\n1,2,3\n', encoding='utf-8')
        target, _ = self.target()
        book = self.window.import_xy_files([path, path], comparison_id=target.comparison_id,
                                           mappings=[(0, [1]), (0, [1])])
        self.assertEqual(len(target.curves), 1)
        self.assertIn('skipped 1', self.window.statusBar().currentMessage())
        sheet = self.window.workspace.sheets[book.sheet_ids[0]]
        snapshot = save_sheet_snapshot(self.window.workspace, sheet.sheet_id, 'Snapshot')
        old = next(iter(target.curves.values()))
        old.visible = False
        candidates = [(sheet.sheet_id, 0, 1, 'raw'), (sheet.sheet_id, 2, 1, 'raw'),
                      (sheet.sheet_id, 0, 2, 'raw'), (snapshot.sheet_id, 0, 1, 'raw')]
        additions, skipped = select_curve_additions(self.window.workspace, target.comparison_id, candidates * 2)
        self.assertEqual(additions, candidates[1:])
        self.assertEqual(skipped, 5)

    def test_dat_gate_normalizes_source_role_and_duplicate_choice(self):
        path = self.folder / 'gates.dat'
        path.write_text('Energy(eV) -2.5 7\n1 10 20\n2 11 21\n3 12 22\n', encoding='utf-8')
        self.window._load_dat_file_direct(path)
        original = ensure_dataset_sheet(self.window.workspace, self.window.workspace.active_dataset_id)
        target, graph = self.target()
        self.window._set_current_gate_from_index(1)
        graph.name_edit.setText(' ')
        before_sheets = list(self.window.workspace.sheets)
        self.window._add_current_gate_comparison()
        self.assertFalse(target.curves)
        self.assertEqual(list(self.window.workspace.sheets), before_sheets)
        self.assertEqual(len(self.errors), 1)
        self.errors.clear()
        graph.name_edit.setText('Named target')
        self.window._add_current_gate_comparison()
        self.assertEqual(len(target.curves), 1)
        curve = next(iter(target.curves.values()))
        processed = self.window.workspace.sheets[curve.sheet_id]
        self.assertEqual(processed.role, 'processed')
        self.assertIn('gates.dat', curve.label)
        self.assertIn('Processed', curve.label)
        self.assertIn('7', curve.label)
        additions, skipped = select_curve_additions(self.window.workspace, target.comparison_id,
            [(original.sheet_id, 0, 2, 'processed'), (original.sheet_id, 0, 2, 'raw')])
        self.assertEqual(additions, [(original.sheet_id, 0, 2, 'raw')])
        self.assertEqual(skipped, 1)
        self.modal(self.window._add_current_gate_comparison, lambda dialog: dialog.defaultButton().click())
        self.assertEqual(len(target.curves), 1)
        self.modal(self.window._add_current_gate_comparison,
            lambda dialog: next(button for button in dialog.buttons() if button.text() == 'Add again').click())
        self.assertEqual(len(target.curves), 2)
        self.assertFalse(self.errors)

    def test_fit_curve_label_uses_actual_gate_and_retains_result_context(self):
        from tests.test_project_io import rich_workspace
        from spectra_app.workbook_state import reconcile_dataset_sheets
        workspace = rich_workspace(self.folder)
        workspace.active_dataset_id = 'owner-0'
        reconcile_dataset_sheets(workspace, 'owner-0')
        target = create_comparison(workspace, 'Results')
        fit = next(sheet for sheet in workspace.sheets.values() if sheet.role == 'fit_curves')
        curve = add_curve(workspace, target.comparison_id, fit.sheet_id, 0, 1)
        cube = workspace.datasets[fit.dataset_id].current_cube
        self.assertIn(fit.source_path.name, curve.label)
        self.assertIn(f'Fit curves · {cube.gate_label} = {cube.gate[fit.fit_gate_idx]:g}', curve.label)
        self.assertIn('Total fit', curve.label)

    def test_cancel_invalid_columns_stale_target_parse_failure_and_busy_are_atomic(self):
        paths = self.files()
        target, graph = self.target()
        graph.name_edit.setText('Draft name')
        with patch('spectra_app.main_window.QFileDialog.getOpenFileNames', return_value=(paths, '')):
            self.modal(graph.add_files_action.trigger, lambda dialog: dialog.reject())
        for kwargs in ({'comparison_id': 'removed'},
                       {'comparison_id': target.comparison_id, 'mappings': [(0, [1]), (0, [99])]}):
            with self.assertRaises(ValueError):
                self.window.import_xy_files(paths, **kwargs)
        with patch.object(self.window, '_project_busy', return_value=True):
            with self.assertRaisesRegex(ValueError, 'Wait'):
                self.window.import_xy_files(paths, comparison_id=target.comparison_id)
        paths[1].write_text('x,y\n2,bad\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.window.import_xy_files(paths, comparison_id=target.comparison_id)
        self.assertFalse(self.window.workspace.workbooks)
        self.assertFalse(target.curves)
        self.assertEqual(target.name, 'Named target')
        self.assertEqual(graph.name_edit.text(), 'Draft name')


if __name__ == '__main__':
    unittest.main()
