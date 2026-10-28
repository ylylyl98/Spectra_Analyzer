from tests import ordinary_analysis_quick_helpers as oa
from tests import quick_migration_case as migration
from tests import analysis_canvas_quick_helpers as ac
from tests.quick_helpers import set_processing_window
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from types import SimpleNamespace
import threading
import sys
from unittest.mock import patch
import unittest

import numpy as np
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QInputMethodEvent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from tests.dat_properties_quick_helpers import (
    edit_number, edit_label, reveal, choose,
    click, state, set_choice, set_flag, setup_number)
from spectra_app.plot_templates import FormatApplicationOptions
from tests.settings_helpers import wait_for_background_jobs
from tests.formal_quick_helpers import item as quick_item, pump, input_receiver
from tests.test_workflow import _fit_result

class EditHistoryTests(migration.QuickMigrationCase):
    def setUp(self):
        if sys.platform == 'win32' and self._testMethodName == 'test_template_manual_override_restores_owner_template_snapshot':
            # The real ComboBox popup requires the native Windows presentation.
            self.enterContext(patch.dict(os.environ, QT_QPA_PLATFORM='windows'))
        super().setUp()
        self.w = self.controller
        self.path = self.folder / 'history.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)))
        self.w._load_dat_file_direct(self.path)
        self.w.open_graph_document()
        self.s = self.w.workspace.active
        self.w.root_window.resize(1250, 900)
        self.w.request_outer_view('workspace')
        self.w.dat_details_action.setChecked(True)
        self.w.request_task(2)
        self.w.root_window.requestActivate()
        pump()
        self.render()

    def tearDown(self):
        wait_for_background_jobs(self.w)
        self.assertEqual(self.quick_errors, [], 'Unexpected production Quick error')
        super().tearDown()

    def render(self):
        self.w._render_plot_page()
        pump()
        self.assertIsNotNone(self.w.plot_page.rendered_page)
        return self.w.plot_page.rendered_page

    def canvas(self):
        return self.w.graph_documents.content_item(self.w._document_key())

    def focus_canvas(self):
        viewport = quick_item(self.canvas(), 'formalViewport')
        viewport.forceActiveFocus()
        pump()
        self.assertTrue(viewport.hasActiveFocus())

    def menu_item(self, group, action):
        bar = self.w.root_window.property('menuBar')
        entry = next(item for item in bar.property('contentItem').childItems()
                     if item.property('text') == group)
        point = entry.mapToScene(QPointF(entry.width()/2, entry.height()/2)).toPoint()
        QTest.mouseClick(self.w.root_window, Qt.LeftButton, pos=point)
        pump()
        control = next(item for item in self.w.root_window.findChildren(QQuickItem)
                       if item.inherits('QQuickMenuItem') and item.isVisible()
                       and item.property('text') == action.text())
        self.assertTrue(control.isEnabled())
        return control

    def click_menu_item(self, control):
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        QTest.mouseClick(input_receiver(self.w, control), Qt.LeftButton, pos=point)
        pump()

    def undo(self):
        self.assertTrue(hasattr(self.w, 'undo_edit'), 'Dataset Undo is missing')
        self.assertTrue(self.w.undo_edit())

    def redo(self):
        self.assertTrue(self.w.redo_edit())

    def test_annotation_add_edit_move_delete_are_separate_reversible_commands(self):
        p = self.w.plot_page
        item = p.add_annotation('text', 'one')
        self.undo()
        self.assertEqual(self.s.presentation.annotations, ())
        self.redo()
        self.assertEqual(self.s.presentation.annotations[0].text, 'one')
        self.render()
        p.select_annotation(item.id)
        p.formal.setDraft('annotation_text', 'two')
        p.formal.applyAnnotation()
        self.undo()
        self.assertEqual(self.s.presentation.annotations[0].text, 'one')
        self.redo()
        self.render()
        start = quick_item(self.canvas(), 'formalPaper').mapToScene(
            p.preview.annotation_point(self.s.presentation.annotations[0])).toPoint()
        end = start + QPoint(22, 15)
        QTest.mousePress(self.w.root_window, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.w.root_window, end)
        QTest.mouseRelease(self.w.root_window, Qt.LeftButton, pos=end)
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
        old = state(w)['plot_title']
        for text in ('N', 'Ne', 'New title'):
            edit_label(w, 'plot_title', text)
        QTest.keyClick(w.root_window, Qt.Key_Return)
        self.undo()
        self.assertEqual(state(w)['plot_title'], old)
        self.assertFalse(w.undo_edit(), 'Typing must produce one committed command')
        self.redo()
        self.assertIn('New title', self.render().svg_bytes.decode())
        recalled, notice = w._plot_state_store.load(self.path, self.s.loaded_fingerprint)
        self.assertEqual(recalled['annotations']['plot_title'], 'New title', notice)

    def test_visual_undo_preserves_later_fit_controls_recipe_gate_and_cube(self):
        w = self.w
        cube = self.s.current_cube
        old_name = state(w)['y_axis_name']
        edit_label(w, 'y_axis_name', 'Magnetic field')
        QTest.keyClick(w.root_window, Qt.Key_Return)
        fit = _fit_result(1.5)
        self.s.fit_result = fit
        self.s.fit_gate_idx = 0
        self.s.batch_fit_result_map[0] = fit
        oa.set_number(w, 'prominence', .37)
        set_processing_window(w.processing, 9)
        w._set_current_gate_from_index(2)
        pending = replace(self.s.pending)
        self.undo()
        self.assertEqual(state(w)['y_axis_name'], old_name)
        self.assertIs(self.s.fit_result, fit)
        self.assertIs(self.s.batch_fit_result_map[0], fit)
        self.assertEqual(self.s.analysis_controls.prominence, .37)
        self.assertEqual(self.s.pending, pending)
        self.assertEqual(w.current_gate, 2.)
        self.assertIs(self.s.current_cube, cube)

    def test_signal_manual_edit_undo_restores_auto_mode_and_label(self):
        w = self.w
        old = state(w)['signal_title']
        self.assertTrue(state(w)['signal_title_auto'])
        edit_label(w, 'signal_title', 'Manual signal')
        QTest.keyClick(w.root_window, Qt.Key_Return)
        self.undo()
        self.assertTrue(state(w)['signal_title_auto'])
        self.assertEqual(state(w)['signal_title'], old)
        self.redo()
        self.assertFalse(state(w)['signal_title_auto'])
        self.assertEqual(state(w)['signal_title'], 'Manual signal')

    def test_presentation_and_range_changes_restore_only_changed_domains(self):
        w, p = self.w, self.w.plot_page
        old_text = self.s.presentation.text_pt
        p.formal.setValue('text_pt', old_text + 2)
        edit_number(w, 'xmin', '1.2', commit=True)
        xlim = self.s.view.x_limits
        self.undo()
        self.assertEqual(float(state(w)['xmin']), 1.)
        self.assertEqual(self.s.presentation.text_pt, old_text + 2)
        self.undo()
        self.assertEqual(float(p.formal.state['fields']['text_pt']), old_text)
        self.redo()
        self.redo()
        self.assertEqual(self.s.view.x_limits, xlim)
        set_flag(w, 'xmin_lock', True)
        self.undo()
        self.assertFalse(state(w)['xmin_lock'])
        self.assertEqual(self.s.view.x_limits, xlim)

    def test_color_mapping_palette_regions_and_auto_colors_are_reversible(self):
        w = self.w
        old_palette = self.s.view.colormap
        set_choice(w, 'colormap', 'plasma')
        self.undo()
        self.assertEqual(self.s.view.colormap, old_palette)
        self.redo()
        set_choice(w, 'mapping', 'log')
        self.undo()
        self.assertEqual(self.s.view.norm.kind, 'linear')
        self.redo()
        set_choice(w, 'color_scale', '2')
        self.undo()
        self.assertEqual(self.s.view.color_scale_mode, 'global')
        self.redo()
        self.assertEqual(self.s.view.color_scale_mode, '2')
        old_edges = list(self.s.view.color_region_boundaries)
        setup_number(w, 'edge0', 1.35)
        self.undo()
        self.assertEqual(self.s.view.color_region_boundaries, old_edges)

    def test_noop_capture_selection_render_zoom_and_gate_do_not_record_edits(self):
        self.addCleanup(QGuiApplication.clipboard().clear)
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
        edit_label(w, 'plot_title', 'First owner')
        other_path = self.folder / 'other.dat'
        other_path.write_bytes(self.path.read_bytes())
        w._load_dat_file_direct(other_path)
        other = w.workspace.active
        self.assertTrue(hasattr(w, 'undo_edit'), 'Dataset Undo is missing')
        self.assertFalse(w.undo_edit())
        edit_label(w, 'y_axis_name', 'Other field')
        QTest.keyClick(w.root_window, Qt.Key_Return)
        w.activate_dataset(self.s.dataset_id)
        self.undo()
        self.assertEqual(self.s.view.annotations['plot_title'], '')
        self.assertEqual(other.view.annotations['y_axis_name'], 'Other field')
        edit_label(w, 'plot_title', 'Replacement')
        QTest.keyClick(w.root_window, Qt.Key_Return)
        self.assertFalse(w.redo_edit())
        w.activate_dataset(other.dataset_id)
        self.undo()
        self.assertNotEqual(other.view.annotations['y_axis_name'], 'Other field')

    def test_selective_format_paste_records_each_target_and_preserves_unselected_values(self):
        w = self.w
        w.plot_page.formal.setValue('text_pt', 11.)
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
        reveal(w, 'y_axis_name')
        old = state(w)['y_axis_name']
        QTest.keyClick(w.root_window, Qt.Key_End)
        for character in ' xyz':
            event = QInputMethodEvent()
            event.setCommitString(character)
            QCoreApplication.sendEvent(w.root_window, event)
        QTest.keyClick(w.root_window, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(state(w)['y_axis_name'], old)
        self.assertEqual(len(self.s.presentation.annotations), 1)
        self.assertIn('undo', w._graph_command_actions)
        command = self.menu_item('Edit', w._graph_command_actions['undo'])
        self.assertIn('Add annotation', command.property('text'))
        self.click_menu_item(command)
        self.assertEqual(self.s.presentation.annotations, ())
        self.render()
        self.focus_canvas()
        QTest.keyClick(w.root_window, Qt.Key_Y, Qt.ControlModifier)
        self.assertEqual(len(self.s.presentation.annotations), 1)

    def test_reread_and_transformation_retire_incompatible_history(self):
        w = self.w
        w.plot_page.formal.setValue('text_pt', 16.)
        owner = self.s.dataset_id
        w._load_dat_file_direct(self.path)
        self.assertEqual(w.workspace.active.dataset_id, owner)
        self.assertTrue(hasattr(w, 'undo_edit'), 'Dataset Undo is missing')
        self.assertFalse(w.undo_edit())
        w.plot_page.formal.setValue('text_pt', 18.)
        w._replace_current_data(replace(w.current_cube, z=w.current_cube.z * 2),
            mode='energy_second_derivative', smooth=False, window_length=5)
        self.assertFalse(w.undo_edit())

    def test_busy_work_rejects_restore_without_altering_committed_state(self):
        w = self.w
        w.plot_page.formal.setValue('text_pt', 16.)
        release = threading.Event()
        self.addCleanup(release.set)
        def work(context):
            release.wait(3)
            return 'finished'
        w._start_background_job(work, w.publish_status, 'Testing work')
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
        old = state(w)['y_axis_unit']
        edit_label(w, 'y_axis_unit', 'Tesla')
        QTest.keyClick(w.root_window, Qt.Key_Return)
        w._annotation_settings.setValue('plot_titles/presets', '{"unrelated":"keep"}')
        self.undo()
        self.assertEqual(w._annotation_settings.value('plot_annotations/y_axis_unit'), old)
        self.assertEqual(w._annotation_settings.value('plot_titles/presets'), '{"unrelated":"keep"}')
        self.redo()
        self.assertEqual(w._annotation_settings.value('plot_annotations/y_axis_unit'), 'Tesla')

    def test_template_manual_override_restores_owner_template_snapshot(self):
        w = self.w
        choose(w, 'title_preset', 'Current group')
        edit_label(w, 'title_pattern', 'Template title')
        click(w, 'shared_template_apply')
        template = w._copy_title_settings(self.s.view.title_settings)
        edit_label(w, 'plot_title', 'Manual override')
        QTest.keyClick(w.root_window, Qt.Key_Return)
        self.undo()
        self.assertEqual(state(w)['plot_title'], 'Template title')
        self.assertEqual(self.s.view.title_settings, template)
        self.redo()
        self.assertEqual(self.s.view.title_settings['manual_override'], 'Manual override')

    def test_numeric_color_commit_includes_implicit_lock_in_one_command(self):
        w = self.w
        original = self.s.view.color_limits
        edit_number(w, 'vmax', '30', commit=True)
        self.assertTrue(state(w)['vmax_lock'])
        self.undo()
        self.assertFalse(state(w)['vmax_lock'])
        self.assertEqual(self.s.view.color_limits, original)
        self.assertFalse(w.undo_edit(), 'Value plus implicit Fix must be one command')
        self.redo()
        self.assertTrue(state(w)['vmax_lock'])
        self.assertEqual(float(state(w)['vmax']), 30.)

    def test_toolbar_range_gesture_is_one_command_and_display_zoom_is_not(self):
        w = self.w
        original = self.s.view.x_limits
        ac.reveal(w)
        w.toolbar.pan()
        before = tuple(w._heat_ax.get_xlim())
        ac.drag_data(w, w._heat_ax, (1.4, 1.), (1.6, 1.))
        self.assertNotEqual(tuple(w._heat_ax.get_xlim()), before)
        w.toolbar.pan()
        w.request_outer_view('workspace')
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
        p.formal.setDraft('tick_values', 'bad ticks')
        p.formal.setValue('custom_ticks', True)
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
        w.plot_page.formal.setValue('numeric_pt', 9.)
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
        from tests import analysis_canvas_quick_helpers as ac
        w = self.w
        self.seed_peaks()
        fit = _fit_result(1.5)
        w.fit_result, w._fit_gate_idx = fit, 0
        w.batch_fit_result_map = {0: fit}
        ac.reveal(w)
        w._update_spectrum_only(); w.canvas.draw()
        def open_menu():
            ac.click_data(w, w._spec_ax, (1.5, float(np.mean(w._spec_ax.get_ylim()))),
                          button=Qt.RightButton)
            self.assertTrue(ac.peak_menu(w).property('visible'))
        history = w._history_for(self.s)
        before = len(history.undo_stack)
        open_menu()
        self.assertEqual(ac.choose_peak_label(w, 12), ('int', '8', 1, 99))
        self.assertEqual(len(history.undo_stack), before + 1)
        self.assertEqual(w.peak_label_lookup[(0, 5)], 12)
        self.assertIs(w.fit_result, fit)
        for result in ((99, False), (12, True)):
            open_menu()
            ac.choose_peak_label(w, result[0], accept=result[1])
            self.assertEqual(w.peak_label_lookup[(0, 5)], 12)
            self.assertEqual(len(history.undo_stack), before + 1)
            self.assertIs(w.fit_result, fit)
        self.undo()
        self.assertEqual(w.peak_label_lookup[(0, 5)], 8)
        self.assertIs(w.fit_result, fit)
        self.redo()
        open_menu()
        ac.click_peak_menu(w, 'delete')
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
        oa.set_mode(w, 'circle', True)
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
        self.assertIn('1.5', oa.summary(w))
        self.assertIn('8', [artist.get_text() for artist in w._heat_peak_texts])
        self.redo()
        self.assertNotIn('1.5', oa.summary(w))
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
        p.formal.setValue('frame_auto', True)
        self.undo()
        self.assertFalse(p.formal.state['fields']['frame_auto'])
        self.redo()
        self.assertIsNone(self.s.presentation.frame_width_in)
        self.assertIsNone(self.s.presentation.frame_height_in)
        p.formal.setValue('axis_x_mode', 'By increment')
        p.formal.setValue('axis_x_mode', 'By count')
        self.undo()
        self.assertEqual(p.formal.state['fields']['axis_x_mode'], 'By increment')
        self.assertIsNone(self.s.presentation.axis.x_major_count)
