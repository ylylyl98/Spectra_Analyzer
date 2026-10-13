import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.main_window import MainWindow
from spectra_app.plot_presentation import PlotAnnotation
from spectra_app.project_io import workspace_digest
from spectra_app.dat_io import load_dat_cube
from spectra_app.plot_presentation import NormSpec
from tests.settings_helpers import isolate_settings


_APP = QApplication.instance() or QApplication([])


def close_window(window):
    _APP.clipboard().clear()
    window.close()
    window.deleteLater()
    _APP.sendPostedEvents(None, QEvent.DeferredDelete)
    _APP.processEvents()


class NamedGraphsWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = isolate_settings(self).parent
        self.path = self.directory / 'sample.dat'
        self.path.write_text('# Signal: PL\nEnergy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
        self.window = MainWindow()
        self.addCleanup(close_window, self.window)
        self.window._load_dat_file_direct(self.path)
        self.window.open_graph_document()

    def test_combo_switch_projects_independent_ranges_styles_gate_and_shared_science(self):
        w, session = self.window, self.window.workspace.active
        session.batch_peak_map[0] = np.array([2])
        session.csv_seeds_map = {0: [1.2]}
        cube, peaks, seeds = session.current_cube, session.batch_peak_map, session.csv_seeds_map
        w.xmin_spin.setValue(1.2)
        w.xmin_lock.setChecked(True)
        w.cmap_combo.setCurrentText('magma')
        w.set_plot_presentation(replace(session.presentation, annotations=(PlotAnnotation('a', text='note'),)))
        detail = w.create_graph('细节 α')
        w.xmin_spin.setValue(1.5)
        w.cmap_combo.setCurrentText('viridis')
        w.current_gate = 2.
        w._sync_gate_controls()
        w.capture_active_dataset()
        w.graph_combo.setCurrentIndex(w.graph_combo.findData('default'))
        self.assertEqual(session.active_graph_id, 'default')
        self.assertAlmostEqual(w.xmin_spin.value(), 1.2)
        self.assertEqual(w._heat_mesh.get_cmap().name, 'magma')
        self.assertTrue(w.xmin_lock.isChecked())
        self.assertEqual(session.presentation.annotations[0].text, 'note')
        self.assertIs(session.current_cube, cube)
        self.assertIs(session.batch_peak_map, peaks)
        self.assertIs(session.csv_seeds_map, seeds)
        w.activate_graph(detail.graph_id)
        self.assertAlmostEqual(w.xmin_spin.value(), 1.5)
        self.assertEqual(w.current_gate, 2.)

    def test_chronological_visual_undo_selects_graph_and_removal_prunes_both_stacks(self):
        w = self.window
        w.cmap_combo.setCurrentText('magma')
        graph = w.create_graph('Detail')
        w.cmap_combo.setCurrentText('viridis')
        w.activate_graph('default')
        self.assertTrue(w.undo_edit())
        self.assertEqual(w.workspace.active.active_graph_id, graph.graph_id)
        self.assertEqual(w._heat_mesh.get_cmap().name, 'magma')
        self.assertTrue(w.undo_edit())
        self.assertEqual(w.workspace.active.active_graph_id, 'default')
        history = w._history_for(w.workspace.active)
        w.remove_graph(graph.graph_id)
        self.assertTrue(all(c.graph_id != graph.graph_id for c in history.undo_stack + history.redo_stack))
        self.assertEqual(w.graph_combo.count(), 1)
        self.assertFalse(w.remove_graph_action.isEnabled())

    def test_title_draft_finishes_on_switch_and_stale_graph_render_is_rejected(self):
        w = self.window
        graph = w.create_graph('Detail')
        w.plot_title_edit.setText('pending detail title')
        w.activate_graph('default')
        self.assertEqual(w.workspace.active.graphs[graph.graph_id].view.annotations['plot_title'], 'pending detail title')
        self.assertTrue(w.undo_edit())
        self.assertEqual(w.workspace.active.active_graph_id, graph.graph_id)
        w._render_plot_page()
        old = w.plot_page.rendered_page
        w.activate_graph('default')
        self.assertNotEqual(old.owner_token, w._plot_owner_token())
        self.assertFalse(w.plot_page.commit_rendered_page(old))

    def test_removal_keeps_shared_peak_history_and_undo_does_not_navigate(self):
        w, session = self.window, self.window.workspace.active
        other = w.create_graph('Other')
        w._clear_edit_history(session)
        with w._peak_edit('Add shared peak', (0,)):
            session.batch_peak_map[0] = np.array([2])
        history = w._history_for(session)
        self.assertEqual(len(history.undo_stack), 1)
        self.assertIsNone(history.undo_stack[-1].graph_id)
        w.remove_graph(other.graph_id)
        self.assertEqual(len(history.undo_stack), 1)
        self.assertEqual(session.active_graph_id, 'default')
        self.assertTrue(w.undo_edit())
        self.assertNotIn(0, session.batch_peak_map)
        self.assertEqual(session.active_graph_id, 'default')
        self.assertTrue(w.redo_edit())
        np.testing.assert_array_equal(session.batch_peak_map[0], [2])

    def test_preview_and_dirty_marker_do_not_mutate_any_graph(self):
        w = self.window
        w.create_graph('Detail')
        session = w.workspace.active
        digest = workspace_digest(w.workspace)
        w._project_marker_workspace()
        self.assertEqual(workspace_digest(w.workspace), digest)
        w.save_plot_format('sample style')
        digest = workspace_digest(w.workspace)
        dialog = w.create_format_dialog('sample style')
        self.addCleanup(dialog.deleteLater)
        dialog._quick_options(True)
        self.assertIn('Detail', dialog.preview_text.toPlainText())
        self.assertEqual(workspace_digest(w.workspace), digest)
        self.assertEqual(session.active_graph.name, 'Detail')

    def test_busy_and_modal_owner_guards_and_names(self):
        w = self.window
        with patch.object(w, '_project_busy', return_value=True):
            w._refresh_graph_commands()
            self.assertFalse(w.graph_combo.isEnabled())
            with self.assertRaises(ValueError):
                w.create_graph('busy')
        with patch('spectra_app.main_window.QInputDialog.getText', side_effect=lambda *a, **k:
                   (w.new_project() and 'orphan', True)):
            w._on_new_graph()
        self.assertIsNone(w.workspace.active)
        self.assertEqual(w.graph_combo.count(), 0)

    def test_export_comparison_and_svg_copy_identify_graph_and_freeze_owner(self):
        w = self.window
        graph = w.create_graph('../细节:*')
        name = w._heatmap_export_filename('svg')
        self.assertEqual(Path(name).name, name)
        self.assertIn('细节', name)
        self.assertNotIn(':', name)
        comparison = w.open_plot_comparison()
        self.addCleanup(comparison.close)
        page = comparison._page
        self.assertIn(graph.name, comparison.source_label.text())
        self.assertEqual(page.owner_token[-1], graph.graph_id)
        self.assertEqual(json.loads(comparison._metadata_json)['graph_name'], graph.name)
        w.activate_graph('default')
        self.assertEqual(comparison._page.svg_bytes, page.svg_bytes)
        w._on_copy_graph_svg()
        mime = QApplication.clipboard().mimeData()
        self.assertTrue(mime.hasFormat('image/svg+xml'))
        self.assertFalse(mime.hasImage())

    def test_native_restart_and_sidecar_restart_preserve_graphs(self):
        w = self.window
        graph = w.create_graph('Detail')
        w.plot_title_edit.setText('detail title')
        w._finish_annotation_edit()
        w.activate_graph('default')
        self.assertEqual(w._save_current_plot_state(), '')
        restarted = MainWindow()
        self.addCleanup(close_window, restarted)
        restarted._load_dat_file_direct(self.path)
        self.assertEqual(restarted.graph_combo.count(), 2)
        restarted.activate_graph(graph.graph_id)
        self.assertEqual(restarted.plot_title_edit.text(), 'detail title')
        destination = self.directory / 'all.spectra'
        w.save_project(destination)
        self.path.unlink()
        restored = MainWindow()
        self.addCleanup(close_window, restored)
        self.assertTrue(restored.open_project(destination))
        self.assertEqual(restored.graph_combo.count(), 2)
        self.assertEqual(restored.workspace.active.active_graph_id, 'default')

    def test_processing_and_axis_replacement_normalize_inactive_graphs(self):
        w, session = self.window, self.window.workspace.active
        graph = w.create_graph('Detail')
        w.xmin_spin.setValue(1.4)
        w.xmin_lock.setChecked(True)
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        w.capture_active_dataset()
        graph.view.last_valid_color_limits = (50., 60.)
        w.activate_graph('default')
        old_token = w.snapshot_plot_dataset(session).owner_token
        cube = replace(session.base_cube, z=session.base_cube.z * -3)
        w._replace_current_data(cube, mode='energy_second_derivative', smooth=False, window_length=21)
        self.assertIs(session.current_cube, cube)
        self.assertIsNone(graph.view.last_valid_color_limits)
        self.assertEqual(graph.view.last_valid_region_color_limits, [])
        self.assertIn('d²PL', graph.view.annotations['signal_title'])
        self.assertLess(graph.view.color_limits[0], 0.)
        self.assertNotEqual(old_token, w.snapshot_plot_dataset(session).owner_token)
        axis_cube = load_dat_cube(self.path, x_axis_override='pixel')
        w._apply_axis_override(axis_cube, session.loaded_fingerprint, 1.)
        self.assertIs(session.current_cube, axis_cube)
        for graph in session.graphs.values():
            self.assertEqual(graph.view.x_limits, (1., 2.2))
            self.assertFalse(graph.view.locks['xmin'])
            self.assertEqual(graph.view.color_region_boundaries, [])
            self.assertEqual(graph.view.annotations['signal_title'], 'PL (arb. units)')

    def test_batch_active_graph_and_sanitized_graph_names_do_not_collide(self):
        w, session = self.window, self.window.workspace.active
        first = w.create_graph('detail/A')
        w.plot_title_edit.setText('first detail')
        w._finish_annotation_edit()
        first_name = w._heatmap_export_filename('svg')
        second = w.create_graph('detail:A')
        second_name = w._heatmap_export_filename('svg')
        self.assertNotEqual(first_name.casefold(), second_name.casefold())
        w.start_plot_batch([session.dataset_id], formats=('svg',), common_folder=self.directory)
        self.assertFalse(w.graph_combo.isEnabled())
        w._plot_batch_timer.stop()
        w._step_plot_batch()
        output = w.plot_batch_summary.results[0].outputs[0].path
        self.assertEqual(output.name, second_name)
        self.assertEqual(session.active_graph_id, second.graph_id)
        self.assertIn('detail-A', w.plot_batch_report)
        self.assertIn('first detail', output.read_text(encoding='utf-8'))
        self.assertEqual(session.graphs[first.graph_id].view.annotations['plot_title'], 'first detail')

    def test_inactive_invalid_color_mapping_blocks_project_save_without_overwrite(self):
        w, session = self.window, self.window.workspace.active
        graph = w.create_graph('Detail')
        w.activate_graph('default')
        path = self.directory / 'saved.spectra'
        w.save_project(path)
        previous = path.read_bytes()
        graph.view.norm = NormSpec('log')
        graph.view.color_limits = (-1., 2.)
        graph.view.bounds.update(vmin=-1., vmax=2.)
        with self.assertRaisesRegex(ValueError, 'Detail'):
            w.save_project(path)
        self.assertEqual(path.read_bytes(), previous)

    def test_undo_commits_formal_draft_in_owning_graph_and_can_redo_it(self):
        w = self.window
        w.cmap_combo.setCurrentText('magma')
        other = w.create_graph('Other')
        width = other.presentation.width_in
        w.plot_page.width_spin.lineEdit().setText(f'{width + 1:.8f}')
        self.assertTrue(w.plot_page.has_pending_project_edits())
        self.assertTrue(w.undo_edit())
        history = w._history_for(w.workspace.active)
        self.assertEqual(history.redo_stack[-1].graph_id, other.graph_id)
        self.assertEqual(w.workspace.active.active_graph_id, other.graph_id)
        self.assertEqual(other.presentation.width_in, width)
        self.assertTrue(w.redo_edit())
        self.assertAlmostEqual(other.presentation.width_in, width + 1)

    def test_switch_commits_gate_range_and_regional_numeric_drafts(self):
        w = self.window
        other = w.create_graph('Other')
        w.xmin_lock.setChecked(True)
        w.xmin_spin.lineEdit().setText('1.5')
        w.vmin_spin.lineEdit().setText('10')
        w.plot_gate_index_spin.lineEdit().setText('3')
        w.activate_graph('default')
        self.assertEqual(other.view.bounds['xmin'], 1.5)
        self.assertTrue(other.view.locks['xmin'])
        self.assertEqual(other.view.bounds['vmin'], 10.)
        self.assertTrue(other.view.locks['vmin'])
        self.assertEqual(other.view.gate, 2.)
        w.activate_graph(other.graph_id)
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        w.region_boundary_spins[0].lineEdit().setText('1.8')
        w.activate_graph('default')
        self.assertEqual(other.view.color_region_boundaries, [1.75])  # Nearest complete-cell edge.

    def test_menu_undo_rejects_invalid_numeric_drafts_without_changing_owner_or_history(self):
        w, session = self.window, self.window.workspace.active
        w.cmap_combo.setCurrentText('magma')
        owner = session.active_graph_id
        commands = tuple(w._history_for(session).undo_stack)
        for control in (w.plot_page.width_spin, w.xmin_spin):
            with self.subTest(control=control):
                original = control.text()
                control.lineEdit().setText('invalid')
                w._graph_command_actions['undo'].trigger()
                self.assertEqual(control.lineEdit().text(), 'invalid')
                self.assertEqual(session.active_graph_id, owner)
                self.assertEqual(tuple(w._history_for(session).undo_stack), commands)
                self.assertIn('Correct', w.statusBar().currentMessage())
                control.lineEdit().setText(original)

    def test_menu_undo_commits_manual_annotation_draft_without_science_capture(self):
        w, session = self.window, self.window.workspace.active
        other = w.create_graph('Other')
        w.set_plot_presentation(replace(session.presentation, annotations=(PlotAnnotation('note', text='before'),)))
        w._clear_edit_history(session)
        w.activate_graph('default')
        w.cmap_combo.setCurrentText('magma')
        w.activate_graph(other.graph_id)
        pending = session.pending
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        w.show()
        _APP.processEvents()
        w._render_plot_page()
        w.plot_page.select_annotation('note')
        w.plot_page.annotation_text.setFocus()
        QTest.keyClick(w.plot_page.annotation_text, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(w.plot_page.annotation_text, 'draft annotation')
        w._edit_menu.popup(w.mapToGlobal(QPoint(30, 50)))
        _APP.processEvents()
        action = w._graph_command_actions['undo']
        QTest.mouseClick(w._edit_menu, Qt.LeftButton, pos=w._edit_menu.actionGeometry(action).center())
        _APP.processEvents()
        self.assertEqual(session.active_graph_id, other.graph_id)
        self.assertEqual(other.presentation.annotations[0].text, 'before')
        self.assertEqual(w._history_for(session).redo_stack[-1].graph_id, other.graph_id)
        self.assertEqual(session.pending, pending)
        self.assertTrue(w.redo_edit())
        self.assertEqual(other.presentation.annotations[0].text, 'draft annotation')

    def test_axis_replacement_projects_normalized_active_widgets_before_capture(self):
        w, session = self.window, self.window.workspace.active
        other = w.create_graph('Other')
        w.activate_graph('default')
        w.xmin_spin.setValue(1.5)
        w.xmin_lock.setChecked(True)
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        w._apply_axis_override(load_dat_cube(self.path, x_axis_override='pixel'), session.loaded_fingerprint, 1.)
        self.assertFalse(w.xmin_lock.isChecked())
        self.assertEqual(w.xmin_spin.value(), 1.)
        w.capture_active_dataset()
        for graph in session.graphs.values():
            self.assertFalse(graph.view.locks['xmin'])
            self.assertEqual(graph.view.bounds['xmin'], 1.)
        w.activate_graph(other.graph_id)
        self.assertEqual(w.xmin_spin.value(), 1.)
