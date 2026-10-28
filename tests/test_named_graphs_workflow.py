from tests import analysis_header_quick_helpers as ah
from tests import dat_properties_quick_helpers as dat
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem

from spectra_app.common_prompts import PromptAction
from tests import quick_migration_case as migration
from tests.quick_application_helpers import application, wait_until
from spectra_app.plot_presentation import PlotAnnotation
from spectra_app.project_io import workspace_digest
from spectra_app.dat_io import load_dat_cube
from spectra_app.plot_presentation import NormSpec
from tests.formal_quick_helpers import edit_field, item, input_receiver, pump


class NamedGraphsWorkflowTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.directory = self.folder
        self.path = self.directory / 'sample.dat'
        self.path.write_text('# Signal: PL\nEnergy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
        self.window = self.controller
        self.window_errors = [self.quick_errors]
        self.window._load_dat_file_direct(self.path)
        self.window.open_graph_document()
        pump()

    def tearDown(self):
        for errors in self.window_errors:
            self.assertEqual(errors, [], 'Unexpected production Quick error')

    def restarted_window(self):
        host, folder, errors = self.enterContext(application())
        self.window_errors.append(errors)
        return host.controller

    def menu_undo(self):
        """Open the actual menu with pointer input and click its Undo item."""
        w = self.window
        menus = [obj for obj in w.root_window.findChildren(QObject)
                 if obj.inherits('QQuickMenu') and obj.property('title') == 'Edit']
        self.assertEqual(len(menus), 1)
        menu = menus[0]
        bar_item = next(obj for obj in w.root_window.findChildren(QQuickItem)
                        if obj.inherits('QQuickMenuBarItem') and obj.property('text') == 'Edit')
        point = bar_item.mapToScene(QPointF(bar_item.width()/2, bar_item.height()/2)).toPoint()
        QTest.mouseClick(w.root_window, Qt.LeftButton, pos=point)
        pump()
        self.assertTrue(menu.property('visible'))
        undo = next(obj for obj in menu.findChildren(QQuickItem)
                    if obj.inherits('QQuickMenuItem') and str(obj.property('text')).startswith('Undo'))
        point = undo.mapToScene(QPointF(undo.width()/2, undo.height()/2)).toPoint()
        QTest.mouseClick(input_receiver(w, undo), Qt.LeftButton, pos=point)
        pump()

    def test_combo_switch_projects_independent_ranges_styles_gate_and_shared_science(self):
        w, session = self.window, self.window.workspace.active
        session.batch_peak_map[0] = np.array([2])
        session.csv_seeds_map = {0: [1.2]}
        cube, peaks, seeds = session.current_cube, session.batch_peak_map, session.csv_seeds_map
        dat.setup_number(w, 'xmin', 1.2)
        dat.set_flag(w, 'xmin_lock', True)
        dat.set_choice(w, 'colormap', 'magma')
        w.set_plot_presentation(replace(session.presentation, annotations=(PlotAnnotation('a', text='note'),)))
        detail = w.create_graph('细节 α')
        dat.setup_number(w, 'xmin', 1.5)
        dat.set_choice(w, 'colormap', 'viridis')
        w.current_gate = 2.
        w._sync_gate_controls()
        w.capture_active_dataset()
        w.source_navigation.selectGraph('default')
        self.assertEqual(session.active_graph_id, 'default')
        self.assertAlmostEqual(w.dat_properties.number('xmin'), 1.2)
        self.assertEqual(w._heat_mesh.get_cmap().name, 'magma')
        self.assertTrue(dat.state(w)['xmin_lock'])
        self.assertEqual(session.presentation.annotations[0].text, 'note')
        self.assertIs(session.current_cube, cube)
        self.assertIs(session.batch_peak_map, peaks)
        self.assertIs(session.csv_seeds_map, seeds)
        w.activate_graph(detail.graph_id)
        self.assertAlmostEqual(w.dat_properties.number('xmin'), 1.5)
        self.assertEqual(w.current_gate, 2.)

    def test_chronological_visual_undo_selects_graph_and_removal_prunes_both_stacks(self):
        w = self.window
        dat.set_choice(w, 'colormap', 'magma')
        graph = w.create_graph('Detail')
        dat.set_choice(w, 'colormap', 'viridis')
        w.activate_graph('default')
        self.assertTrue(w.undo_edit())
        self.assertEqual(w.workspace.active.active_graph_id, graph.graph_id)
        self.assertEqual(w._heat_mesh.get_cmap().name, 'magma')
        self.assertTrue(w.undo_edit())
        self.assertEqual(w.workspace.active.active_graph_id, 'default')
        history = w._history_for(w.workspace.active)
        w.remove_graph(graph.graph_id)
        self.assertTrue(all(c.graph_id != graph.graph_id for c in history.undo_stack + history.redo_stack))
        self.assertEqual(len(w.source_navigation.graphRows), 1)
        self.assertFalse(w.source_navigation.graphActionEnabled(w.source_navigation.graphContext(), 'remove'))

    def test_title_draft_finishes_on_switch_and_stale_graph_render_is_rejected(self):
        w = self.window
        graph = w.create_graph('Detail')
        w.dat_properties.setLabel('plot_title', 'pending detail title', w.dat_properties.ownerToken)
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
        dialog.bridge.quickOptions(True)
        self.assertIn('Detail', dialog.bridge.previewText)
        self.assertEqual(workspace_digest(w.workspace), digest)
        self.assertEqual(session.active_graph.name, 'Detail')

    def test_busy_and_modal_owner_guards_and_names(self):
        w = self.window
        session = w.workspace.active
        errors = []
        w._show_error = errors.append
        with patch.object(w, '_project_busy', return_value=True):
            w._refresh_graph_commands()
            self.assertFalse(w.source_navigation.graphsEnabled)
            with self.assertRaises(ValueError):
                w.create_graph('busy')
        with patch('spectra_app.main_window.prompt_text', return_value=('  细节 α  ', True)) as prompt:
            w._on_new_graph()
        detail = session.active_graph
        self.assertEqual(detail.name, '细节 α')
        self.assertEqual(prompt.call_args.kwargs, {'text': 'Graph 2'})
        names = {key: graph.name for key, graph in session.graphs.items()}
        for result in (('', False), ('', True)):
            with patch('spectra_app.main_window.prompt_text', return_value=result):
                w._on_new_graph()
            self.assertEqual({key: graph.name for key, graph in session.graphs.items()}, names)
        self.assertEqual(len(errors), 1)
        self.assertIn('nonempty graph name', errors.pop())
        with patch('spectra_app.main_window.prompt_text', return_value=('', True)) as prompt:
            w._on_rename_graph()
        self.assertEqual(prompt.call_args.kwargs, {'text': '细节 α'})
        self.assertEqual(detail.name, '细节 α')
        self.assertIn('nonempty graph name', errors.pop())
        with patch('spectra_app.main_window.prompt_text', side_effect=lambda *a, **k:
                   (w.activate_graph('default') or 'stale rename', True)):
            w._on_rename_graph()
        self.assertEqual(detail.name, '细节 α')
        self.assertEqual(session.active_graph_id, 'default')
        self.assertFalse(errors)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD), \
             patch('spectra_app.main_window.prompt_text', side_effect=lambda *a, **k:
                   (w.new_project() and 'orphan', True)):
            w._on_new_graph()
        self.assertIsNone(w.workspace.active)
        self.assertEqual(len(w.source_navigation.graphRows), 0)

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
        self.assertIn(graph.name, comparison.bridge.sourceText)
        self.assertEqual(page.owner_token[-1], graph.graph_id)
        self.assertEqual(json.loads(comparison._metadata_json)['graph_name'], graph.name)
        w.activate_graph('default')
        self.assertEqual(comparison._page.svg_bytes, page.svg_bytes)
        self.addCleanup(QGuiApplication.clipboard().clear)
        w._on_copy_graph_svg()
        mime = QGuiApplication.clipboard().mimeData()
        self.assertTrue(mime.hasFormat('image/svg+xml'))
        self.assertFalse(mime.hasImage())

    def test_native_restart_and_sidecar_restart_preserve_graphs(self):
        w = self.window
        graph = w.create_graph('Detail')
        w.dat_properties.setLabel('plot_title', 'detail title', w.dat_properties.ownerToken)
        w._finish_annotation_edit()
        w.activate_graph('default')
        self.assertEqual(w._save_current_plot_state(), '')
        restarted = self.restarted_window()
        restarted._load_dat_file_direct(self.path)
        self.assertEqual(len(restarted.source_navigation.graphRows), 2)
        restarted.activate_graph(graph.graph_id)
        self.assertEqual(dat.state(restarted)['plot_title'], 'detail title')
        destination = self.directory / 'all.spectra'
        w.save_project(destination)
        self.path.unlink()
        restored = self.restarted_window()
        self.assertTrue(restored.open_project(destination))
        self.assertEqual(len(restored.source_navigation.graphRows), 2)
        self.assertEqual(restored.workspace.active.active_graph_id, 'default')

    def test_processing_and_axis_replacement_normalize_inactive_graphs(self):
        w, session = self.window, self.window.workspace.active
        graph = w.create_graph('Detail')
        dat.setup_number(w, 'xmin', 1.4)
        dat.set_flag(w, 'xmin_lock', True)
        dat.set_choice(w, 'color_scale', '2')
        w.capture_active_dataset()
        w.activate_graph('default')
        # Source-axis replacement normalizes every eligible raw graph before
        # retained processed results make that source-axis route unavailable.
        axis_cube = load_dat_cube(self.path, x_axis_override='pixel')
        w._apply_axis_override(axis_cube, session.loaded_fingerprint, 1.)
        self.assertIs(session.current_cube, axis_cube)
        for owner in session.graphs.values():
            self.assertEqual(owner.view.x_limits, (1., 2.2))
            self.assertFalse(owner.view.locks['xmin'])
            self.assertEqual(owner.view.color_region_boundaries, [])
            self.assertEqual(owner.view.annotations['signal_title'], 'PL (arb. units)')
        graph.view.last_valid_color_limits = (50., 60.)
        raw_views = {key: deepcopy(owner.view) for key, owner in session.graphs.items()}
        old_token = w.snapshot_plot_dataset(session).owner_token
        cube = replace(session.base_cube, z=session.base_cube.z * -3)
        w._replace_current_data(cube, mode='energy_second_derivative', smooth=False, window_length=21)
        self.assertIs(session.current_cube, cube)
        processed = session.active_graph
        self.assertNotIn(processed.graph_id, raw_views)
        self.assertIsNotNone(processed.result_id)
        self.assertIs(session.processing_results[processed.result_id].cube, cube)
        self.assertNotEqual(processed.view.last_valid_color_limits, (50., 60.))
        self.assertEqual(processed.view.last_valid_region_color_limits, [])
        self.assertIn('d²PL', processed.view.annotations['signal_title'])
        self.assertLess(processed.view.color_limits[0], 0.)
        for key, view in raw_views.items():
            self.assertEqual(session.graphs[key].view, view)
        self.assertNotEqual(old_token, w.snapshot_plot_dataset(session).owner_token)
        retained = dict(session.processing_results)
        w._apply_axis_override(load_dat_cube(self.path), session.loaded_fingerprint, 1.)
        self.assertEqual(self.quick_errors, [
            'Retained processing results use this saved axis. Import the DAT as a new copy to change its axis interpretation.'])
        self.quick_errors.clear()
        self.assertIs(session.current_cube, cube)
        self.assertIs(session.base_cube, axis_cube)
        self.assertEqual(session.processing_results, retained)
        for key, view in raw_views.items():
            self.assertEqual(session.graphs[key].view, view)

    def test_batch_active_graph_and_sanitized_graph_names_do_not_collide(self):
        w, session = self.window, self.window.workspace.active
        first = w.create_graph('detail/A')
        w.dat_properties.setLabel('plot_title', 'first detail', w.dat_properties.ownerToken)
        w._finish_annotation_edit()
        first_name = w._heatmap_export_filename('svg')
        second = w.create_graph('detail:A')
        second_name = w._heatmap_export_filename('svg')
        self.assertNotEqual(first_name.casefold(), second_name.casefold())
        w.start_plot_batch([session.dataset_id], formats=('svg',), common_folder=self.directory)
        self.assertFalse(w.source_navigation.graphsEnabled)
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
        dat.set_choice(w, 'colormap', 'magma')
        other = w.create_graph('Other')
        width = other.presentation.width_in
        w.root_window.show()
        pump()
        edit_field(w, 'width_in', f'{width + 1:.8f}')
        self.assertTrue(w.plot_page.has_pending_project_edits())
        self.assertEqual(other.presentation.width_in, width)
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
        dat.set_flag(w, 'xmin_lock', True)
        w.dat_properties.draftNumber('xmin', '1.5', w.dat_properties.ownerToken)
        w.dat_properties.draftNumber('vmin', '10', w.dat_properties.ownerToken)
        ah.edit(w, 'index', '3')
        w.activate_graph('default')
        self.assertEqual(other.view.bounds['xmin'], 1.5)
        self.assertTrue(other.view.locks['xmin'])
        self.assertEqual(other.view.bounds['vmin'], 10.)
        self.assertTrue(other.view.locks['vmin'])
        self.assertEqual(other.view.gate, 2.)
        w.activate_graph(other.graph_id)
        dat.set_choice(w, 'color_scale', '2')
        w.dat_properties.draftNumber('edge0', '1.8', w.dat_properties.ownerToken)
        w.activate_graph('default')
        self.assertEqual(other.view.color_region_boundaries, [1.75])  # Nearest complete-cell edge.

    def test_menu_undo_rejects_invalid_numeric_drafts_without_changing_owner_or_history(self):
        w, session = self.window, self.window.workspace.active
        dat.set_choice(w, 'colormap', 'magma')
        owner = session.active_graph_id
        commands = tuple(w._history_for(session).undo_stack)
        w.root_window.show()
        pump()
        for field in ('width_in', 'xmin'):
            with self.subTest(field=field):
                if field == 'width_in':
                    original = w.plot_page.formal.state['fields'][field]
                    control = edit_field(w, field, 'invalid')
                else:
                    original = dat.state(w)['xmin']
                    w.dat_properties.draftNumber('xmin', 'invalid', w.dat_properties.ownerToken)
                w._graph_command_actions['undo'].trigger()
                if field == 'width_in':
                    self.assertEqual(control.property('text'), 'invalid')
                    self.assertEqual(w.plot_page.formal.state['fields'][field], 'invalid')
                else:
                    self.assertEqual(dat.state(w)['xmin'], 'invalid')
                self.assertEqual(session.active_graph_id, owner)
                self.assertEqual(tuple(w._history_for(session).undo_stack), commands)
                self.assertIn('Correct', w.statusText)
                if field == 'width_in':
                    edit_field(w, field, original)
                else:
                    w.dat_properties.draftNumber('xmin', original, w.dat_properties.ownerToken)

        # Validation of sibling DAT text precedes any otherwise-valid formal
        # commit, so a failed Undo cannot partially publish a presentation.
        width = session.presentation.width_in
        control = edit_field(w, 'width_in', str(width + 1))
        w.dat_properties.draftNumber('xmin', 'invalid', w.dat_properties.ownerToken)
        self.assertFalse(w.undo_edit())
        self.assertEqual(session.presentation.width_in, width)
        self.assertEqual(control.property('text'), str(width + 1))
        self.assertEqual(dat.state(w)['xmin'], 'invalid')
        self.assertEqual(session.active_graph_id, owner)
        self.assertEqual(tuple(w._history_for(session).undo_stack), commands)

    def test_menu_undo_commits_manual_annotation_draft_without_science_capture(self):
        w, session = self.window, self.window.workspace.active
        other = w.create_graph('Other')
        w.set_plot_presentation(replace(session.presentation, annotations=(PlotAnnotation('note', text='before'),)))
        w._clear_edit_history(session)
        w.activate_graph('default')
        dat.set_choice(w, 'colormap', 'magma')
        w.activate_graph(other.graph_id)
        pending = session.pending
        w.request_outer_view('workspace')
        w.root_window.show()
        pump()
        w._render_plot_page()
        w.plot_page.select_annotation('note')
        control = edit_field(w, 'annotation_text', 'draft annotation')
        self.assertTrue(control.hasActiveFocus())
        self.assertEqual(other.presentation.annotations[0].text, 'before')
        self.assertTrue(w.plot_page.has_pending_project_edits())
        self.menu_undo()
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
        dat.setup_number(w, 'xmin', 1.5)
        dat.set_flag(w, 'xmin_lock', True)
        dat.set_choice(w, 'color_scale', '2')
        w._apply_axis_override(load_dat_cube(self.path, x_axis_override='pixel'), session.loaded_fingerprint, 1.)
        self.assertFalse(dat.state(w)['xmin_lock'])
        self.assertEqual(w.dat_properties.number('xmin'), 1.)
        w.capture_active_dataset()
        for graph in session.graphs.values():
            self.assertFalse(graph.view.locks['xmin'])
            self.assertEqual(graph.view.bounds['xmin'], 1.)
        w.activate_graph(other.graph_id)
        self.assertEqual(w.dat_properties.number('xmin'), 1.)
