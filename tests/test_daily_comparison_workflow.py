"""Observable daily comparison commands and staged physical Gate matching."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton, QMessageBox, QMenu

from spectra_app.main_window import MainWindow
from spectra_app.sheet_comparison_dialog import SheetComparisonDialog
from spectra_app.workbook_state import create_comparison, remove_comparison, create_workbook, register_xy_sheet, merge_workbooks, ensure_dataset_sheet
from spectra_app.processing import energy_second_derivative
from tests.settings_helpers import isolate_settings
from tests.background_helpers import wait_jobs

APP = QApplication.instance() or QApplication([])


class DailyComparisonTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.errors = []
        self.w._show_error = self.errors.append
        self.w.resize(1500, 920)
        self.w.show()
        APP.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def xy(self, name='source'):
        path = self.folder / (name + '.csv')
        path.write_text('x,a,b\n0.5,1,3\n1.5,2,4\n')
        self.w._import_data_files([path])
        wait_jobs(self.w)
        book = next(b for b in self.w.workspace.workbooks.values()
                    if any(self.w.workspace.sheets[s].source_path == path for s in b.sheet_ids))
        page = self.w.open_workbook(book.workbook_id).tabs.currentWidget()
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
        self.assertTrue(hasattr(commands, 'pin_action'), 'Shared commands need a visible Pin target action')
        commands.target_combo.setCurrentIndex(commands.target_combo.findData(target.comparison_id))
        if not commands.pin_action.isChecked():
            commands.pin_action.trigger()
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
        self.assertEqual(commands.target_combo.currentData(), a.comparison_id)
        QTest.mouseClick(commands.add_btn, Qt.LeftButton)
        self.assertEqual(len(a.curves), 2)
        self.assertFalse(b.curves)
        self.assertEqual(self.w.graph_documents._active_key, ('workbook', other.workbook_id, ''))
        np.testing.assert_array_equal(self.w.graph_documents.page_for(('xy', a.comparison_id, '')).axes.lines[0].get_xdata(), [.5, 1.5])
        commands.target_combo.setCurrentIndex(commands.target_combo.findData(b.comparison_id))
        self.w.open_xy_comparison(a.comparison_id)
        self.w.open_workbook(book.workbook_id)
        self.assertEqual(commands.target_combo.currentData(), b.comparison_id)
        commands.add_action.trigger()
        self.assertEqual(len(b.curves), 2)

    def test_pin_rename_close_show_and_delete_do_not_fall_back(self):
        book, page, a, b, commands = self.targets()
        a.name = 'Renamed target'
        self.w._auxiliary_changed()
        self.assertEqual(commands.target_combo.currentText(), a.name)
        self.w.graph_documents.close_document(('xy', a.comparison_id, ''))
        commands.add_action.trigger()
        self.assertEqual(len(a.curves), 2)
        self.assertIsNone(self.w.graph_documents.window_for(('xy', a.comparison_id, '')))
        commands.show_target_action.trigger()
        self.assertEqual(self.w.graph_documents._active_key, ('xy', a.comparison_id, ''))
        self.w.open_workbook(book.workbook_id)
        remove_comparison(self.w.workspace, a.comparison_id)
        self.w._refresh_auxiliary_documents()
        self.w._auxiliary_changed()
        self.w.open_xy_comparison(b.comparison_id)
        self.w.open_workbook(book.workbook_id)
        self.assertIsNone(commands.target_combo.currentData())
        self.assertFalse(commands.add_action.isEnabled())
        self.assertFalse(commands.show_target_action.isEnabled())

    def test_direct_duplicate_skip_and_explicit_repeat_keep_styles_and_source(self):
        book, page = self.xy()
        self.w.workbook_commands.style_actions['scatter'].trigger()
        graph = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        original = copy.deepcopy(graph.comparison.curves)
        self.w.open_workbook(book.workbook_id)
        with patch('spectra_app.main_window.QMessageBox.exec', side_effect=AssertionError('Routine modal')):
            self.w.workbook_commands.add_action.trigger()
        self.assertEqual(graph.comparison.curves, original)
        self.assertIn('skipped 2', self.w.statusBar().currentMessage())
        self.assertTrue(hasattr(self.w.workbook_commands, 'add_again_action'))
        self.w.workbook_commands.add_again_action.trigger()
        self.assertEqual(len(graph.comparison.curves), 4)
        self.assertEqual(list(graph.comparison.curves.values())[:2], list(original.values()))
        self.assertEqual(self.w.graph_documents._active_key, ('workbook', book.workbook_id, ''))

    def test_pin_new_open_and_failed_install_rollback(self):
        book, page, a, b, commands = self.targets()
        path = self.w.save_project(self.folder / 'targets.spectra')
        with patch.object(self.w, '_project_installed_workspace', side_effect=ValueError('install failure')):
            with self.assertRaisesRegex(ValueError, 'install failure'):
                self.w.open_project(path)
        self.assertEqual(commands.target_combo.currentData(), a.comparison_id)
        self.assertTrue(commands.pin_action.isChecked())
        self.w.open_xy_comparison(b.comparison_id)
        self.w.open_workbook(book.workbook_id)
        self.assertEqual(commands.target_combo.currentData(), a.comparison_id)
        self.w.open_project(path)
        self.assertFalse(commands.pin_action.isChecked())
        self.w.open_workbook(book.workbook_id)
        self.pin(self.w.workspace.comparisons[a.comparison_id])
        self.w.new_project()
        self.assertIsNone(self.w._last_comparison_id)
        self.assertFalse(commands.pin_action.isChecked())

    def test_workbook_defaults_original_and_explicit_result_survives_merge_selection(self):
        _, sheets = self.dat()
        book = self.w._sheet_owner(sheets[0].sheet_id)
        other, _ = self.xy()
        merge_workbooks(self.w.workspace, [book.workbook_id, other.workbook_id], book.workbook_id)
        self.w._refresh_data_navigator()
        tree = self.w.data_navigator
        def item(key):
            return next(i for i in self.w._navigator_items() if i.data(0, Qt.UserRole) == key)
        tree.setCurrentItem(item(('workbook', book.workbook_id)))
        originals = [s for s in book.sheet_ids if self.w.workspace.sheets[s].role == 'original']
        self.assertEqual(self.w._selected_sheet_ids(), originals)
        result = next(s for s in sheets if s.role == 'processed')
        item(('sheet', result.sheet_id)).setSelected(True)
        self.assertEqual(set(self.w._selected_sheet_ids()), set(originals + [result.sheet_id]))

    def test_dialog_workbook_check_includes_all_and_originals_command_is_explicit(self):
        _, sheets = self.dat()
        dialog = SheetComparisonDialog(self.w.workspace)
        self.addCleanup(dialog.deleteLater)
        parent = dialog.source_tree.topLevelItem(0)
        parent.setCheckState(0, Qt.Checked)
        self.assertEqual(list(dialog.selected_mappings()), [s.sheet_id for s in sheets])
        dialog.select_originals_btn.click()
        self.assertEqual(list(dialog.selected_mappings()), [s.sheet_id for s in sheets if s.role == 'original'])
        result = next(s for s in sheets if s.role == 'processed')
        dialog._items[result.sheet_id].setCheckState(0, Qt.Checked)
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
        self.assertTrue(hasattr(dialog, 'match_gates_btn'), 'Gate matching must be an explicit action')
        dialog._choices[source.sheet_id] = (0, [1, 3], 'raw')
        dialog.source_tree.setCurrentItem(dialog._items[source.sheet_id])
        dialog._load_editor()
        xy_before = copy.deepcopy(dialog._choices[xy.sheet_id])
        dialog.match_gates_btn.click()
        self.assertEqual(dialog._choices[raw.sheet_id], (0, [2, 1], 'raw'))
        self.assertEqual(dialog._choices[processed.sheet_id], (0, [3, 2], 'processed'))
        self.assertEqual(dialog._choices[xy.sheet_id], xy_before)
        target_session.current_cube = replace(target_session.current_cube, gate=np.array([4., 7., -2.000000001]))
        before = copy.deepcopy(dialog._choices)
        dialog.match_gates_btn.click()
        self.assertEqual(dialog._choices, before)
        self.assertIn('-2', dialog.feedback.text())
        self.assertIn(dialog._names[processed.sheet_id], dialog.feedback.text())
        self.assertFalse(self.w.workspace.comparisons)

    def test_processing_action_moves_to_analysis_and_really_applies_transform(self):
        session, sheets = self.dat()
        self.assertEqual(self.w.generate_heatmap_btn.text(), 'Apply processing')
        self.assertTrue(self.w._analysis_sidebar_scroll.isAncestorOf(self.w._processing_content))
        self.w.activate_dataset(session.dataset_id)
        self.w.plot_tabs.setCurrentIndex(0)
        self.w._sidebar_tabs.setCurrentWidget(self.w._analysis_sidebar_scroll)
        original = session.base_cube.z.copy()
        expected = energy_second_derivative(session.base_cube.photon_energy, original)
        self.w.processing_mode_combo.setCurrentIndex(self.w.processing_mode_combo.findData('energy_second_derivative'))
        self.w.processing_smooth_chk.setChecked(False)
        self.w.generate_heatmap_btn.click()
        wait_jobs(self.w)
        np.testing.assert_allclose(session.current_cube.z, expected)
        np.testing.assert_array_equal(session.base_cube.z, original)
        self.assertEqual(self.w.generate_heatmap_btn.text(), 'Apply processing')
        self.assertFalse(any(g.published for g in session.graphs.values()))

    def test_heatmap_view_default_later_choice_and_collapsed_navigation(self):
        session, sheets = self.dat()
        original = next(s for s in sheets if s.role == 'original')
        book = self.w._sheet_owner(original.sheet_id)
        self.assertTrue(hasattr(self.w, '_source_navigation_content'))
        self.assertTrue(self.w._source_navigation_content.isHidden())
        self.w.open_workbook(book.workbook_id, original.sheet_id)
        self.w.workbook_commands.heatmap_action.trigger()
        self.assertEqual(self.w._sidebar_tabs.tabText(self.w._sidebar_tabs.currentIndex()), 'View')
        self.w._sidebar_tabs.setCurrentWidget(self.w._analysis_sidebar_scroll)
        self.w.open_workbook(book.workbook_id)
        self.w.workbook_commands.heatmap_action.trigger()
        self.assertIs(self.w._sidebar_tabs.currentWidget(), self.w._analysis_sidebar_scroll)
        self.w._set_dat_details_requested(False)
        self.w.open_workbook(book.workbook_id)
        self.w.workbook_commands.heatmap_action.trigger()
        self.assertTrue(self.w.dat_details_dock.isHidden())

    def test_current_gate_appends_raw_then_processed_without_activating_existing_target(self):
        session, _ = self.dat()
        self.w.activate_dataset(session.dataset_id)
        self.w.plot_tabs.setCurrentIndex(0)
        # A new graph activates; a later addition returns to the source view.
        self.w._set_current_gate_from_index(0)
        self.w._add_current_gate_comparison()
        target = self.w.workspace.comparisons[self.w._last_comparison_id]
        graph = self.w.graph_documents.page_for(('xy', target.comparison_id, ''))
        self.assertEqual(self.w.graph_documents._active_key, ('xy', target.comparison_id, ''))
        np.testing.assert_array_equal(graph.axes.lines[0].get_ydata(), session.base_cube.z[0])
        self.w.plot_tabs.setCurrentIndex(0)
        with patch('spectra_app.main_window.QMessageBox.exec', side_effect=AssertionError('Routine modal')):
            self.w.add_gate_comparison_btn.trigger()
        self.assertEqual(len(target.curves), 1)
        self.assertIn('skipped 1', self.w.statusBar().currentMessage())
        self.w.processing_mode_combo.setCurrentIndex(self.w.processing_mode_combo.findData('energy_second_derivative'))
        self.w.processing_smooth_chk.setChecked(False)
        self.w.generate_heatmap_btn.click()
        wait_jobs(self.w)
        self.w.add_gate_comparison_btn.trigger()
        self.assertEqual(self.w.plot_tabs.currentIndex(), 0)
        self.assertEqual(len(target.curves), 2)
        np.testing.assert_array_equal(graph.axes.lines[1].get_ydata(), session.current_cube.z[0])
        curves = list(target.curves.values())
        self.assertEqual([self.w.workspace.sheets[c.sheet_id].role for c in curves], ['original', 'processed'])
        self.w.add_gate_again_action.trigger()
        self.assertEqual(len(target.curves), 3)
        self.assertEqual(self.w.plot_tabs.currentIndex(), 0)
        self.assertFalse(self.errors)

    def test_deleted_pin_can_choose_replacement_from_native_narrow_overflow(self):
        book, page, a, b, commands = self.targets()
        remove_comparison(self.w.workspace, a.comparison_id)
        self.w._refresh_auxiliary_documents()
        self.w._auxiliary_changed()
        commands.setMaximumWidth(320)
        APP.processEvents()
        self.assertFalse(commands.add_action.isEnabled())
        extension = commands.findChild(QToolButton, 'qt_toolbar_ext_button')
        self.assertIsNotNone(extension)
        self.assertTrue(extension.isVisible())
        failures = []
        def choose():
            try:
                menu = APP.activePopupWidget()
                self.assertIsInstance(menu, QMenu)
                target_action = next(action for action in menu.actions() if action.text() == 'Target…')
                self.assertTrue(target_action.isEnabled())
                target_menu = target_action.menu()
                self.assertIsNotNone(target_menu)
                replacement = next(action for action in target_menu.actions() if action.text() == b.name)
                replacement.trigger()
                menu.close()
            except BaseException as exc:
                failures.append(exc)
                if APP.activePopupWidget():
                    APP.activePopupWidget().close()
        QTimer.singleShot(0, choose)
        QTest.mouseClick(extension, Qt.LeftButton)
        if failures:
            raise failures[0]
        self.assertEqual(commands.target_combo.currentData(), b.comparison_id)
        self.assertTrue(commands.pin_action.isChecked())
        commands.add_action.trigger()
        self.assertEqual(len(b.curves), 2)

    def test_no_original_workbook_reports_feedback_and_preserves_result_choice(self):
        book, page = self.xy()
        sheet = self.w.workspace.sheets[page.sheet_id]
        sheet.role = 'snapshot'
        self.w._refresh_data_navigator()
        parent = next(self.w.data_navigator.topLevelItem(i) for i in range(self.w.data_navigator.topLevelItemCount())
                      if self.w.data_navigator.topLevelItem(i).data(0, Qt.UserRole) == ('workbook', book.workbook_id))
        self.w.data_navigator.setCurrentItem(parent)
        self.assertEqual(self.w._selected_sheet_ids(), [])
        self.assertTrue(self.w.compare_sheets_btn.isEnabled())
        self.w.compare_sheets_btn.trigger()
        self.assertIn('no Original', self.w.statusBar().currentMessage())
        dialog = SheetComparisonDialog(self.w.workspace)
        self.addCleanup(dialog.deleteLater)
        dialog.source_tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        self.assertIn(sheet.sheet_id, dialog.selected_mappings())
        self.assertTrue(dialog.commit_btn.isEnabled())
        dialog.select_originals_btn.click()
        self.assertFalse(dialog.selected_mappings())
        self.assertFalse(dialog.commit_btn.isEnabled())


if __name__ == '__main__':
    unittest.main()
