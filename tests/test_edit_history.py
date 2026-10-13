import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QInputDialog

from spectra_app.main_window import MainWindow
from spectra_app.plot_templates import FormatApplicationOptions
from tests.settings_helpers import isolate_settings, wait_for_background_jobs
from tests.test_workflow import _fit_result

APP = QApplication.instance() or QApplication([])


class EditHistoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.w._show_error = self.fail
        self.path = self.folder / 'history.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)))
        self.w._load_dat_file_direct(self.path)
        self.s = self.w.workspace.active
        self.w.resize(1250, 900)
        self.w.plot_tabs.setCurrentWidget(self.w.graph_documents)
        self.w.show()
        self.w.activateWindow()
        APP.processEvents()
        self.render()

    def tearDown(self):
        wait_for_background_jobs(self.w)
        APP.clipboard().clear()
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def render(self):
        self.w._render_plot_page()
        APP.processEvents()
        self.assertIsNotNone(self.w.plot_page.rendered_page)
        return self.w.plot_page.rendered_page

    def undo(self):
        self.assertTrue(hasattr(self.w, 'undo_edit'), 'Dataset Undo is missing')
        self.assertTrue(self.w.undo_edit())

    def redo(self):
        self.assertTrue(self.w.redo_edit())

    def finish_label(self, control):
        (control.lineEdit() if hasattr(control, 'lineEdit') else control).editingFinished.emit()

    def test_annotation_add_edit_move_delete_are_separate_reversible_commands(self):
        p = self.w.plot_page
        item = p.add_annotation('text', 'one')
        self.undo()
        self.assertEqual(self.s.presentation.annotations, ())
        self.redo()
        self.assertEqual(self.s.presentation.annotations[0].text, 'one')
        self.render()
        p.select_annotation(item.id)
        p.annotation_text.setText('two')
        p.annotation_apply_btn.click()
        self.undo()
        self.assertEqual(self.s.presentation.annotations[0].text, 'one')
        self.redo()
        self.render()
        start = p.preview.annotation_point(self.s.presentation.annotations[0])
        end = start + QPoint(22, 15)
        QTest.mousePress(p.preview, Qt.LeftButton, pos=start)
        QTest.mouseMove(p.preview, end)
        QTest.mouseRelease(p.preview, Qt.LeftButton, pos=end)
        moved = self.s.presentation.annotations[0].position
        self.assertNotEqual(moved, (.15, .85))
        self.undo()
        self.assertEqual(self.s.presentation.annotations[0].position, (.15, .85))
        self.redo()
        self.assertEqual(self.s.presentation.annotations[0].position, moved)
        p.delete_annotation()
        self.assertEqual(self.s.presentation.annotations, ())
        self.undo()
        self.assertEqual(self.s.presentation.annotations[0].text, 'two')
        self.assertEqual(self.s.presentation.annotations[0].position, moved)

    def test_committed_title_is_one_command_and_restores_sidecar_and_export(self):
        w = self.w
        old = w.plot_title_edit.text()
        for text in ('N', 'Ne', 'New title'):
            w.plot_title_edit.setText(text)
        self.finish_label(w.plot_title_edit)
        self.undo()
        self.assertEqual(w.plot_title_edit.text(), old)
        self.assertFalse(w.undo_edit(), 'Typing must produce one committed command')
        self.redo()
        self.assertIn('New title', self.render().svg_bytes.decode())
        recalled, notice = w._plot_state_store.load(self.path, self.s.loaded_fingerprint)
        self.assertEqual(recalled['annotations']['plot_title'], 'New title', notice)

    def test_visual_undo_preserves_later_fit_controls_recipe_gate_and_cube(self):
        w = self.w
        cube = self.s.current_cube
        old_name = w.y_axis_name_edit.text()
        w.y_axis_name_edit.setText('Magnetic field')
        self.finish_label(w.y_axis_name_edit)
        fit = _fit_result(1.5)
        self.s.fit_result = fit
        self.s.fit_gate_idx = 0
        self.s.batch_fit_result_map[0] = fit
        w.prom_spin.setValue(.37)
        w.processing_window_spin.setValue(9)
        w._set_current_gate_from_index(2)
        pending = replace(self.s.pending)
        self.undo()
        self.assertEqual(w.y_axis_name_edit.text(), old_name)
        self.assertIs(self.s.fit_result, fit)
        self.assertIs(self.s.batch_fit_result_map[0], fit)
        self.assertEqual(self.s.analysis_controls.prominence, .37)
        self.assertEqual(self.s.pending, pending)
        self.assertEqual(w.current_gate, 2.)
        self.assertIs(self.s.current_cube, cube)

    def test_signal_manual_edit_undo_restores_auto_mode_and_label(self):
        w = self.w
        old = w.signal_title_combo.currentText()
        self.assertTrue(w.signal_title_auto_chk.isChecked())
        w.signal_title_combo.setCurrentText('Manual signal')
        self.finish_label(w.signal_title_combo)
        self.undo()
        self.assertTrue(w.signal_title_auto_chk.isChecked())
        self.assertEqual(w.signal_title_combo.currentText(), old)
        self.redo()
        self.assertFalse(w.signal_title_auto_chk.isChecked())
        self.assertEqual(w.signal_title_combo.currentText(), 'Manual signal')

    def test_presentation_and_range_changes_restore_only_changed_domains(self):
        w, p = self.w, self.w.plot_page
        old_text = self.s.presentation.text_pt
        p.text_spin.setValue(old_text + 2)
        w.xmin_spin.setValue(1.2)
        xlim = self.s.view.x_limits
        self.undo()
        self.assertEqual(w.xmin_spin.value(), 1.)
        self.assertEqual(self.s.presentation.text_pt, old_text + 2)
        self.undo()
        self.assertEqual(p.text_spin.value(), old_text)
        self.redo()
        self.redo()
        self.assertEqual(self.s.view.x_limits, xlim)
        w.xmin_lock.setChecked(True)
        self.undo()
        self.assertFalse(w.xmin_lock.isChecked())
        self.assertEqual(self.s.view.x_limits, xlim)

    def test_color_mapping_palette_regions_and_auto_colors_are_reversible(self):
        w = self.w
        old_palette = self.s.view.colormap
        w.cmap_combo.setCurrentIndex(w.cmap_combo.findData('plasma'))
        self.undo()
        self.assertEqual(self.s.view.colormap, old_palette)
        self.redo()
        w.mapping_combo.setCurrentIndex(w.mapping_combo.findData('log'))
        self.undo()
        self.assertEqual(self.s.view.norm.kind, 'linear')
        self.redo()
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        self.undo()
        self.assertEqual(self.s.view.color_scale_mode, 'global')
        self.redo()
        self.assertEqual(self.s.view.color_scale_mode, '2')
        old_edges = list(self.s.view.color_region_boundaries)
        w.region_boundary_spins[0].setValue(1.35)
        self.undo()
        self.assertEqual(self.s.view.color_region_boundaries, old_edges)

    def test_noop_capture_selection_render_zoom_and_gate_do_not_record_edits(self):
        w, p = self.w, self.w.plot_page
        self.assertTrue(hasattr(w, 'undo_edit'), 'Dataset Undo is missing')
        w.copy_plot_settings()
        w.copy_graph_svg()
        p.finish_editor()
        p.set_zoom('200%')
        w._set_current_gate_from_index(0)
        self.render()
        self.assertFalse(w.undo_edit())
        self.assertFalse(w._graph_command_actions['undo'].isEnabled())
        item = p.add_annotation('text', 'note')
        self.render()
        p.select_annotation(item.id)
        p._replace_annotation(item)
        self.undo()
        self.assertEqual(self.s.presentation.annotations, ())
        self.assertFalse(w.undo_edit())

    def test_pending_label_flushes_to_original_owner_and_new_edit_clears_redo(self):
        w = self.w
        w.plot_title_edit.setText('First owner')
        other_path = self.folder / 'other.dat'
        other_path.write_bytes(self.path.read_bytes())
        w._load_dat_file_direct(other_path)
        other = w.workspace.active
        self.assertTrue(hasattr(w, 'undo_edit'), 'Dataset Undo is missing')
        self.assertFalse(w.undo_edit())
        w.y_axis_name_edit.setText('Other field')
        self.finish_label(w.y_axis_name_edit)
        w.activate_dataset(self.s.dataset_id)
        self.undo()
        self.assertEqual(self.s.view.annotations['plot_title'], '')
        self.assertEqual(other.view.annotations['y_axis_name'], 'Other field')
        w.plot_title_edit.setText('Replacement')
        self.finish_label(w.plot_title_edit)
        self.assertFalse(w.redo_edit())
        w.activate_dataset(other.dataset_id)
        self.undo()
        self.assertNotEqual(other.view.annotations['y_axis_name'], 'Other field')

    def test_selective_format_paste_records_each_target_and_preserves_unselected_values(self):
        w = self.w
        w.plot_page.text_spin.setValue(11.)
        w.copy_plot_settings()
        source = self.s
        other_path = self.folder / 'target.dat'
        other_path.write_bytes(self.path.read_bytes())
        w._load_dat_file_direct(other_path)
        target = w.workspace.active
        original = target.presentation.text_pt
        options = FormatApplicationOptions(layout=False, axis=False, colormap=False,
            colorbar=False, typography=True)
        outcomes = w.paste_plot_settings([target.dataset_id, source.dataset_id], options=options)
        self.assertTrue(all(item['success'] for item in outcomes))
        self.assertEqual(target.presentation.text_pt, 11.)
        fit = _fit_result(1.5)
        target.fit_result = fit
        target.pending.window_length = 9
        w._set_current_gate_from_index(0)
        self.undo()
        self.assertEqual(target.presentation.text_pt, original)
        self.assertIs(target.fit_result, fit)
        self.assertEqual(target.pending.window_length, 9)
        self.assertEqual(w.current_gate, 0.)
        w.activate_dataset(source.dataset_id)
        self.undo()
        self.assertEqual(source.presentation.text_pt, 12., 'No-op paste must not shadow source edit')

    def seed_peaks(self):
        w = self.w
        w._set_current_gate_from_index(0)
        w.peak_indices = np.array([2, 5])
        w._peak_gate_idx = 0
        w.peak_kind_lookup = {2: True, 5: False}
        w.peak_label_lookup = {(0, 2): 7, (0, 5): 8, (1, 3): 9}
        w.batch_peak_map = {0: np.array([2, 5]), 1: np.array([3])}
        w.batch_peak_kind_lookup = {(0, 2): True, (0, 5): False, (1, 3): True}
        w._sync_batch_peak_coords()

    def test_manual_peak_remove_restores_maps_and_invalidates_only_affected_fits(self):
        w = self.w
        self.seed_peaks()
        affected, unrelated = _fit_result(1.5), _fit_result(1.3)
        w.batch_fit_result_map = {0: affected, 1: unrelated}
        w.batch_fit_map = {0: affected.centers, 1: unrelated.centers}
        w.fit_result, w._fit_gate_idx = affected, 0
        self.assertTrue(w._remove_manual_peak_near(1.5))
        self.assertNotIn(0, w.batch_fit_result_map)
        self.assertIs(w.batch_fit_result_map[1], unrelated)
        self.assertNotIn(5, w.peak_kind_lookup)
        self.assertNotIn((0, 5), w.batch_peak_kind_lookup)
        self.undo()
        np.testing.assert_array_equal(w.peak_indices, [2, 5])
        np.testing.assert_array_equal(w.batch_peak_map[0], [2, 5])
        self.assertFalse(w.peak_kind_lookup[5])
        self.assertEqual(w.peak_label_lookup[(0, 5)], 8)
        self.assertIsNone(w.fit_result)
        self.assertIs(w.batch_fit_result_map[1], unrelated)

    def test_peak_add_drag_clear_and_redo_preserve_cube_and_gate_navigation(self):
        w = self.w
        cube = w.current_cube
        self.seed_peaks()
        self.assertTrue(w._add_manual_peak(1.8))
        self.undo()
        np.testing.assert_array_equal(w.peak_indices, [2, 5])
        self.redo()
        w._drag_peak_arr_idx = 1
        w._on_canvas_release(SimpleNamespace(inaxes=w._spec_ax, xdata=1.6))
        self.assertNotIn(5, w.peak_kind_lookup)
        self.assertFalse(w.peak_kind_lookup[6])
        self.assertEqual(w.peak_label_lookup[(0, 6)], 8)
        self.undo()
        self.assertEqual(w.peak_label_lookup[(0, 5)], 8)
        w._on_clear_peaks()
        self.undo()
        np.testing.assert_array_equal(w.peak_indices, [2, 5, 8])
        w._set_current_gate_from_index(1)
        self.redo()
        self.assertEqual(w.current_gate, 1.)
        np.testing.assert_array_equal(w.peak_indices, [3])
        self.assertIs(w.current_cube, cube)

    def test_lasso_circle_and_all_gate_clear_are_reversible(self):
        w = self.w
        self.seed_peaks()
        w._lasso_selected_peak_ids = [(0, 5)]
        w._on_delete_lasso_selection()
        self.undo()
        self.assertEqual(w.peak_label_lookup[(0, 5)], 8)
        deleted = w._delete_batch_peaks_in_circle(1.3, 1., 1.33, 1.2)
        self.assertEqual(deleted, 1)
        self.undo()
        np.testing.assert_array_equal(w.batch_peak_map[1], [3])
        w._on_clear_batch_peaks()
        self.undo()
        self.assertEqual(set(w.batch_peak_map), {0, 1})

    def test_native_text_undo_and_explicit_menu_undo_do_not_conflict(self):
        w, p = self.w, self.w.plot_page
        p.add_annotation('text', 'note')
        p.control_scroll.ensureWidgetVisible(w.y_axis_name_edit)
        w.y_axis_name_edit.setFocus()
        old = w.y_axis_name_edit.text()
        QTest.keyClick(w.y_axis_name_edit, Qt.Key_End)
        QTest.keyClicks(w.y_axis_name_edit, ' xyz')
        QTest.keyClick(w.y_axis_name_edit, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(w.y_axis_name_edit.text(), old)
        self.assertEqual(len(self.s.presentation.annotations), 1)
        self.assertIn('undo', w._graph_command_actions)
        w._edit_menu.aboutToShow.emit()
        self.assertIn('Add annotation', w._graph_command_actions['undo'].text())
        w._graph_command_actions['undo'].trigger()
        self.assertEqual(self.s.presentation.annotations, ())
        p.preview.setFocus()
        QTest.keyClick(p.preview, Qt.Key_Y, Qt.ControlModifier)
        self.assertEqual(len(self.s.presentation.annotations), 1)

    def test_reread_and_transformation_retire_incompatible_history(self):
        w = self.w
        w.plot_page.text_spin.setValue(16.)
        owner = self.s.dataset_id
        w._load_dat_file_direct(self.path)
        self.assertEqual(w.workspace.active.dataset_id, owner)
        self.assertTrue(hasattr(w, 'undo_edit'), 'Dataset Undo is missing')
        self.assertFalse(w.undo_edit())
        w.plot_page.text_spin.setValue(18.)
        w._replace_current_data(replace(w.current_cube, z=w.current_cube.z * 2),
            mode='energy_second_derivative', smooth=False, window_length=5)
        self.assertFalse(w.undo_edit())

    def test_busy_work_rejects_restore_without_altering_committed_state(self):
        w = self.w
        w.plot_page.text_spin.setValue(16.)
        release = threading.Event()
        self.addCleanup(release.set)
        def work(context):
            release.wait(3)
            return 'finished'
        w._start_background_job(work, lambda result: w.statusBar().showMessage(result), 'Testing work')
        self.assertTrue(w._background_jobs.busy)
        self.assertIn('undo', w._graph_command_actions)
        self.assertFalse(w._graph_command_actions['undo'].isEnabled())
        self.assertFalse(w.undo_edit())
        self.assertEqual(self.s.presentation.text_pt, 16.)
        release.set()
        wait_for_background_jobs(w)
        self.undo()
        self.assertEqual(self.s.presentation.text_pt, 12.)

    def test_history_is_bounded_and_duplicate_peak_operations_are_noops(self):
        w = self.w
        self.assertTrue(hasattr(w, 'undo_edit'), 'Dataset Undo is missing')
        for index in range(110):
            w.set_plot_presentation(replace(self.s.presentation, title_position=(index / 110., .9)))
        count = 0
        while w.undo_edit():
            count += 1
        self.assertGreater(count, 0)
        self.assertLessEqual(count, 100)
        self.seed_peaks()
        self.assertFalse(w._add_manual_peak(1.2))
        self.assertFalse(w._remove_manual_peak_near(2.1))
        self.assertFalse(w.undo_edit())

    def test_fixed_label_defaults_follow_undo_without_changing_named_presets(self):
        w = self.w
        old = w.y_axis_unit_edit.text()
        w.y_axis_unit_edit.setText('Tesla')
        self.finish_label(w.y_axis_unit_edit)
        w._annotation_settings.setValue('plot_titles/presets', '{"unrelated":"keep"}')
        self.undo()
        self.assertEqual(w._annotation_settings.value('plot_annotations/y_axis_unit'), old)
        self.assertEqual(w._annotation_settings.value('plot_titles/presets'), '{"unrelated":"keep"}')
        self.redo()
        self.assertEqual(w._annotation_settings.value('plot_annotations/y_axis_unit'), 'Tesla')

    def test_template_manual_override_restores_owner_template_snapshot(self):
        w = self.w
        w.title_preset_combo.setCurrentText('Current group')
        w.title_pattern_edit.setText('Template title')
        self.finish_label(w.title_pattern_edit)
        template = w._copy_title_settings(self.s.view.title_settings)
        w.plot_title_edit.setText('Manual override')
        self.finish_label(w.plot_title_edit)
        self.undo()
        self.assertEqual(w.plot_title_edit.text(), 'Template title')
        self.assertEqual(self.s.view.title_settings, template)
        self.redo()
        self.assertEqual(self.s.view.title_settings['manual_override'], 'Manual override')

    def test_numeric_color_commit_includes_implicit_lock_in_one_command(self):
        w = self.w
        original = self.s.view.color_limits
        w.vmax_spin.setFocus()
        w.vmax_spin.lineEdit().selectAll()
        QTest.keyClicks(w.vmax_spin.lineEdit(), '30')
        QTest.keyClick(w.vmax_spin, Qt.Key_Return)
        self.assertTrue(w.vmax_lock.isChecked())
        self.undo()
        self.assertFalse(w.vmax_lock.isChecked())
        self.assertEqual(self.s.view.color_limits, original)
        self.assertFalse(w.undo_edit(), 'Value plus implicit Fix must be one command')
        self.redo()
        self.assertTrue(w.vmax_lock.isChecked())
        self.assertEqual(w.vmax_spin.value(), 30.)

    def test_toolbar_range_gesture_is_one_command_and_display_zoom_is_not(self):
        w = self.w
        original = self.s.view.x_limits
        w.toolbar.pan()
        w._heat_ax.set_xlim(1.1, 1.9)
        w._heat_ax.set_xlim(1.2, 1.8)
        w._on_canvas_release(SimpleNamespace())
        w.toolbar.pan()
        w.plot_page.set_zoom('200%')
        self.undo()
        self.assertEqual(self.s.view.x_limits, original)
        self.assertFalse(w.undo_edit())

    def test_deleting_other_gate_seeds_preserves_current_fit_and_export_rows(self):
        w = self.w
        self.seed_peaks()
        fit = _fit_result(1.5)
        w.fit_result, w._fit_gate_idx = fit, 0
        w.batch_fit_result_map = {0: fit}
        w.batch_fit_map = {0: fit.centers}
        w._batch_fit_export_rows = [{'gate': '0', 'status': 'ok'}, {'gate': '1', 'status': 'ok'}]
        w._lasso_selected_peak_ids = [(1, 3)]
        w._on_delete_lasso_selection()
        self.assertIs(w.fit_result, fit)
        self.assertEqual(w._batch_fit_export_rows, [{'gate': '0', 'status': 'ok'}])
        self.undo()
        self.assertIs(w.fit_result, fit)
        self.assertIs(w.batch_fit_result_map[0], fit)

    def test_rejected_ticks_and_noop_peak_drag_do_not_create_entries(self):
        w, p = self.w, self.w.plot_page
        p.tick_values_edit.setText('bad ticks')
        p.custom_ticks.setChecked(True)
        self.assertIsNotNone(p.presentation_error)
        self.assertFalse(w.undo_edit())
        p.set_presentation(self.s.presentation)
        self.seed_peaks()
        fit = _fit_result(1.5)
        w.fit_result, w._fit_gate_idx = fit, 0
        w._drag_peak_arr_idx = 1
        w._on_canvas_release(SimpleNamespace(inaxes=w._spec_ax, xdata=1.5))
        self.assertIs(w.fit_result, fit)
        self.assertFalse(w.undo_edit())

    def test_format_paste_into_inactive_target_has_its_own_undo(self):
        w = self.w
        source = self.s
        other_path = self.folder / 'inactive.dat'
        other_path.write_bytes(self.path.read_bytes())
        w._load_dat_file_direct(other_path)
        target = w.workspace.active
        w.activate_dataset(source.dataset_id)
        w.plot_page.numeric_spin.setValue(9.)
        w.copy_plot_settings()
        options = FormatApplicationOptions(layout=False, typography=True, axis=False,
            colormap=False, colorbar=False)
        outcomes = w.paste_plot_settings([target.dataset_id], options=options)
        self.assertTrue(outcomes[0]['success'])
        self.assertIs(w.workspace.active, source)
        self.undo()
        self.assertEqual(target.presentation.numeric_pt, 9.)
        w.activate_dataset(target.dataset_id)
        self.undo()
        self.assertEqual(target.presentation.numeric_pt, 10.)

    def test_peak_context_menu_delete_and_label_use_history_and_keep_label_fits(self):
        w = self.w
        self.seed_peaks()
        fit = _fit_result(1.5)
        w.fit_result, w._fit_gate_idx = fit, 0
        w.batch_fit_result_map = {0: fit}
        def choose(data):
            def select():
                menu = APP.activePopupWidget()
                action = next(action for action in menu.actions() if action.data() == data)
                menu.setActiveAction(action)
                QTest.keyClick(menu, Qt.Key_Return)
            QTimer.singleShot(0, select)
        choose('set_label')
        with patch.object(QInputDialog, 'getInt', return_value=(12, True)):
            w._show_peak_context_menu(1.5)
        self.assertEqual(w.peak_label_lookup[(0, 5)], 12)
        self.assertIs(w.fit_result, fit)
        self.undo()
        self.assertEqual(w.peak_label_lookup[(0, 5)], 8)
        self.assertIs(w.fit_result, fit)
        self.redo()
        choose('delete')
        w._show_peak_context_menu(1.5)
        np.testing.assert_array_equal(w.peak_indices, [2])
        self.assertNotIn(0, w.batch_fit_result_map)
        self.undo()
        np.testing.assert_array_equal(w.peak_indices, [2, 5])
        self.assertEqual(w.peak_label_lookup[(0, 5)], 12)

    def test_circle_release_preserves_fit_for_gate_outside_deleted_circle(self):
        w = self.w
        self.seed_peaks()
        fit = _fit_result(1.5)
        w.fit_result, w._fit_gate_idx = fit, 0
        w.batch_fit_result_map = {0: fit}
        w.circle_delete_btn.setChecked(True)
        w._batch_delete_circle_start = (1.3, 1.)
        w._on_canvas_release(SimpleNamespace(inaxes=w._heat_ax, button=1, xdata=1.33, ydata=1.2))
        self.assertNotIn(1, w.batch_peak_map)
        self.assertIs(w.fit_result, fit)
        self.undo()
        self.assertIs(w.fit_result, fit)

    def test_lasso_current_trace_is_redrawn_from_cleaned_peak_maps(self):
        w = self.w
        self.seed_peaks()
        w._lasso_selected_peak_ids = [(0, 5)]
        w._on_delete_lasso_selection()
        np.testing.assert_array_equal(w._current_trace_peak_indices(), [2])
        self.assertNotIn('8', [artist.get_text() for artist in w._heat_peak_texts])
        self.undo()
        self.assertIn('1.5', w.analysis_text.toPlainText())
        self.assertIn('8', [artist.get_text() for artist in w._heat_peak_texts])
        self.redo()
        self.assertNotIn('1.5', w.analysis_text.toPlainText())
        self.assertNotIn('8', [artist.get_text() for artist in w._heat_peak_texts])

    def test_empty_peak_clear_is_a_noop_and_keeps_existing_csv_fit(self):
        w = self.w
        fit = _fit_result(1.5)
        w.fit_result, w._fit_gate_idx = fit, w._current_gate_index()
        w._on_clear_peaks()
        self.assertIs(w.fit_result, fit)
        self.assertFalse(w.undo_edit())

    def test_coupled_frame_and_tick_fields_restore_atomically(self):
        p = self.w.plot_page
        p.frame_auto.setChecked(True)
        self.undo()
        self.assertFalse(p.frame_auto.isChecked())
        self.redo()
        self.assertIsNone(self.s.presentation.frame_width_in)
        self.assertIsNone(self.s.presentation.frame_height_in)
        p.x_mode_combo.setCurrentText('By increment')
        p.x_mode_combo.setCurrentText('By count')
        self.undo()
        self.assertEqual(p.x_mode_combo.currentText(), 'By increment')
        self.assertIsNone(self.s.presentation.axis.x_major_count)
