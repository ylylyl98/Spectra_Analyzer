"""Named graph targets cross the real Qt selector, snapshot and export boundaries."""
from tests import dat_properties_quick_helpers as dat
from tests import ordinary_analysis_quick_helpers as oa
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtQuick import QQuickView

from spectra_app.dataset_state import graph_export_stem
from spectra_app.plot_dialogs import ApplyFormatDialog, BatchExportDialog
from spectra_app.plot_presentation import NormSpec, PlotAnnotation
from spectra_app.plot_templates import capture_format
from tests import quick_migration_case as migration
from tests.quick_application_helpers import pump


class GraphBatchDialogTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        sessions = []
        for side in ('left', 'right'):
            folder = self.folder / side
            folder.mkdir()
            source = folder / 'same.dat'
            source.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
            self.w._load_dat_file_direct(source)
            self.w.open_graph_document()
            pump()
            sessions.append(self.w.workspace.active)
        self.a, self.b = sessions
        self.w.open_graph_document(self.a.dataset_id)
        self.detail = self.w.create_graph('Detail <α & β>')
        self.w.open_graph_document(self.a.dataset_id, 'default')
        self.w.open_graph_document(self.b.dataset_id)
        self.other = self.w.create_graph('Second view')
        self.dialog = BatchExportDialog((self.a, self.b), self.a.dataset_id, self.w)
        self.addCleanup(self.dialog.deleteLater)

    def tearDown(self):
        self.assertEqual(self.quick_errors, [], 'Unexpected production Quick error')

    def test_tree_current_only_default_parent_partial_and_graph_all_clear(self):
        dialog = self.dialog
        self.assertIsInstance(dialog.window(), QQuickView)
        self.assertEqual(dialog.selected_graph_targets(), [(self.a.dataset_id, 'default')])
        a, b = dialog.bridge.groups
        self.assertNotEqual(a.label, b.label)
        self.assertIn('left', a.label)
        self.assertIn('right', b.label)
        self.assertEqual(a.tooltip, str(self.a.source_path.resolve()))
        self.assertEqual(a.children[1].label, self.detail.name)
        self.assertEqual(a.checkState, Qt.PartiallyChecked.value)
        self.assertEqual(b.checkState, Qt.Unchecked.value)
        a.setChecked(True)
        self.assertEqual(dialog.selected_graph_targets(), [(self.a.dataset_id, 'default'), (self.a.dataset_id, self.detail.graph_id)])
        a.children[0].setChecked(False)
        self.assertEqual(a.checkState, Qt.PartiallyChecked.value)
        dialog.bridge.selectAll(True)
        self.assertEqual(dialog.selected_graph_targets(), [
            (self.a.dataset_id, 'default'), (self.a.dataset_id, self.detail.graph_id), (self.b.dataset_id, 'default'), (self.b.dataset_id, self.other.graph_id)])
        self.assertEqual(b.checkState, Qt.Checked.value)
        dialog.bridge.selectAll(False)
        self.assertEqual(dialog.selected_graph_targets(), [])
        self.assertFalse(dialog.bridge.canAccept)
        self.assertEqual(a.checkState, Qt.Unchecked.value)

    def test_folder_and_empty_selection_block_acceptance_without_creating_folders(self):
        dialog = self.dialog
        dialog.bridge.setCommonFolder(True)
        self.assertFalse(dialog.bridge.canAccept)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Rejected)
        missing = self.folder / 'missing'
        dialog.set_destination_folder(missing)
        self.assertFalse(dialog.bridge.canAccept)
        self.assertFalse(missing.exists())
        dialog.set_destination_folder(self.folder)
        self.assertTrue(dialog.bridge.canAccept)
        dialog.bridge.selectAll(False)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Rejected)
        dialog.bridge.groups[1].children[1].setChecked(True)
        self.assertEqual(dialog.selected_graph_targets(), [(self.b.dataset_id, self.other.graph_id)])
        self.assertEqual(dialog.destination(), str(self.folder))
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Accepted)

    def test_format_dialog_keeps_dataset_based_selection(self):
        template = capture_format(self.a, 'Paper')
        dialog = ApplyFormatDialog((self.a, self.b), self.b.dataset_id, template, self.w)
        self.addCleanup(dialog.deleteLater)
        self.assertIsInstance(dialog.window(), QQuickView)
        self.assertEqual(dialog.selected_ids(), [self.b.dataset_id])
        self.assertEqual(len(dialog.bridge.targets), 2)
        dialog.bridge.selectAll(True)
        self.assertEqual(dialog.selected_ids(), [self.a.dataset_id, self.b.dataset_id])


class GraphBatchExportTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.path = self.folder / 'sample.dat'
        self.path.write_text('# Signal: PL\nEnergy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
        self.w = self.controller
        self.w._load_dat_file_direct(self.path)
        self.w.open_graph_document()
        pump()
        self.session = self.w.workspace.active
        self.w.set_plot_presentation(replace(self.session.presentation, heatmap_dpi=80,
            width_in=5., height_in=5., aspect_locked=False, frame_width_in=None, frame_height_in=None))
        self.w.dat_properties.setLabel('plot_title', 'Overview title', self.w.dat_properties.ownerToken)
        self.w._finish_graph_editors(capture_science=False)
        self.detail = self.w.create_graph('Detail <α & β>')
        self.w.open_graph_document()
        pump()
        dat.setup_number(self.w, 'xmin', 1.5)
        dat.set_flag(self.w, 'xmin_lock', True)
        dat.set_choice(self.w, 'colormap', 'magma')
        self.w.dat_properties.setLabel('plot_title', 'Detail title', self.w.dat_properties.ownerToken)
        self.w.set_plot_presentation(replace(self.session.presentation, text_pt=15,
            annotations=(PlotAnnotation('detail-note', text='Detail note'),)))
        self.w._finish_graph_editors(capture_science=False)
        self.w.open_graph_document(graph_id='default')
        pump()

    def tearDown(self):
        self.assertEqual(self.quick_errors, [], 'Unexpected production Quick error')

    def finish_batch(self):
        self.w._plot_batch_timer.stop()
        while not self.w._plot_batch.done:
            self.w._step_plot_batch()
            self.w._plot_batch_timer.stop()

    def targets(self):
        return [(self.session.dataset_id, 'default'), (self.session.dataset_id, self.detail.graph_id)]

    def test_two_graph_outputs_freeze_ranges_styles_metadata_names_and_preserve_owner_science(self):
        w, session = self.w, self.session
        session.batch_peak_map[0] = np.array([2])
        session.csv_seeds_map = {0: [1.2]}
        session.fit_result = object()
        science = (session.current_cube, session.pending, session.applied, session.analysis_controls,
                   session.batch_peak_map, session.csv_seeds_map, session.fit_result)
        values = session.current_cube.z.copy()
        w.processing.setWindowDraft('25')
        oa.draft(w, 'fit_xmin', '1.4')
        controls = (dat.state(w)['xmin'], next(entry['label'] for entry in dat.state(w)['colormaps'] if entry['value'] == dat.state(w)['colormap']), w.source_navigation.activeGraphId,
                    w.processing.windowDraft, oa.text(w, 'fit_xmin'), dat.state(w)['plot_title'])
        active_view, active_page = deepcopy(session.view), session.presentation
        expected_detail = deepcopy(self.detail.view)
        detail_page = self.detail.presentation
        epoch = w._data_generation
        with patch.object(w, '_save_plot_dataset', wraps=w._save_plot_dataset) as save:
            w.start_plot_batch(graph_targets=self.targets(), formats=('svg', 'png'), common_folder=self.folder)
        self.assertEqual(save.call_count, 1)
        self.assertIs(save.call_args.args[0], session)
        self.assertEqual([job.snapshot.graph_id for job in w._plot_batch._jobs], ['default', self.detail.graph_id])
        self.detail.name = 'Later graph name'
        self.detail.view.annotations['plot_title'] = 'Later title'
        self.detail.view.x_limits = (1., 1.2)
        self.detail.presentation = replace(self.detail.presentation, text_pt=21)
        self.finish_batch()
        self.assertTrue(w.plot_batch_summary.success, w.plot_batch_report)
        self.assertEqual(w.workspace.active_dataset_id, session.dataset_id)
        self.assertEqual(session.active_graph_id, 'default')
        self.assertEqual(w._data_generation, epoch)
        self.assertEqual(session.view, active_view)
        self.assertEqual(session.presentation, active_page)
        self.assertEqual((dat.state(w)['xmin'], next(entry['label'] for entry in dat.state(w)['colormaps'] if entry['value'] == dat.state(w)['colormap']), w.source_navigation.activeGraphId,
                          w.processing.windowDraft, oa.text(w, 'fit_xmin'), dat.state(w)['plot_title']), controls)
        for expected, actual in zip(science, (session.current_cube, session.pending, session.applied,
                session.analysis_controls, session.batch_peak_map, session.csv_seeds_map, session.fit_result)):
            self.assertIs(actual, expected)
        np.testing.assert_array_equal(session.current_cube.z, values)
        self.assertEqual(w.plot_page.formal.state['batch']['maximum'], 2)
        for result, graph_id, name, view, presentation in zip(w.plot_batch_summary.results,
                ['default', self.detail.graph_id], ['Graph 1', 'Detail <α & β>'],
                [active_view, expected_detail], [active_page, detail_page]):
            self.assertEqual(result.graph_id, graph_id)
            self.assertIn(name, result.target_label)
            outputs = {item.format: item.path for item in result.outputs}
            self.assertIn(graph_export_stem('sample', graph_id, name), outputs['svg'].stem)
            self.assertEqual(outputs['svg'].parent, self.folder)
            svg = outputs['svg'].read_text(encoding='utf-8')
            self.assertIn(view.annotations['plot_title'], svg)
            self.assertNotIn('Later title', svg)
            with Image.open(outputs['png']) as image:
                metadata = json.loads(image.info['SpectraAnalyzer'])
                self.assertEqual(metadata['graph_id'], graph_id)
                self.assertEqual(metadata['graph_name'], name)
                self.assertEqual(metadata['x_limits'], list(view.x_limits))
                self.assertEqual(metadata['colormap'], view.colormap)
                self.assertEqual(metadata['presentation'], presentation.to_dict())
                self.assertAlmostEqual(image.info['dpi'][0], 80, delta=.05)
        saved = json.loads(w._plot_state_store.path_for(self.path).read_text(encoding='utf-8'))['plot']
        self.assertEqual(saved['active_graph_id'], 'default')
        self.assertEqual([g['name'] for g in saved['graphs']], ['Graph 1', 'Detail <α & β>'])
        self.assertIn('Detail <α & β>', w.plot_batch_report)
        self.assertNotIn('Later graph name', w.plot_batch_report)

    def test_valid_current_visual_drafts_finalize_without_capturing_scientific_drafts(self):
        w, session = self.w, self.session
        pending, analysis = deepcopy(session.pending), deepcopy(session.analysis_controls)
        width = session.presentation.width_in
        w.plot_page.formal.setDraft('width_in', f'{width + .5:.8f}')
        dat.set_flag(w, 'xmin_lock', True)
        w.dat_properties.draftNumber('xmin', '1.3', w.dat_properties.ownerToken)
        w.dat_properties.setLabel('plot_title', 'Pending title', w.dat_properties.ownerToken)
        w.processing.setWindowDraft('25')
        oa.draft(w, 'fit_xmin', '1.4')
        w.start_plot_batch(graph_targets=[(session.dataset_id, 'default')], formats=('svg',))
        job = w._plot_batch._jobs[0]
        self.assertAlmostEqual(job.presentation.width_in, width + .5)
        self.assertAlmostEqual(job.snapshot.x_limits[0], 1.3)
        self.assertEqual(job.snapshot.title, 'Pending title')
        self.assertEqual(session.pending, pending)
        self.assertEqual(session.analysis_controls, analysis)
        self.assertEqual(w.processing.windowDraft, '25')
        self.assertEqual(oa.text(w, 'fit_xmin'), '1.4')
        self.finish_batch()
        self.assertTrue(w.plot_batch_summary.success, w.plot_batch_report)

    def test_invalid_current_draft_fails_only_owning_pair_and_other_graph_remains_valid(self):
        w, session = self.w, self.session
        before = deepcopy(self.detail.view)
        w.plot_page.formal.setDraft('width_in', 'invalid')
        w.start_plot_batch(graph_targets=self.targets(), formats=('svg',))
        self.finish_batch()
        results = w.plot_batch_summary.results
        self.assertEqual([(r.graph_id, r.success) for r in results], [('default', False), (self.detail.graph_id, True)])
        self.assertEqual(results[0].error_stage, 'prepare')
        self.assertIn('editor', results[0].error)
        self.assertEqual(self.detail.view, before)
        self.assertEqual(w.plot_page.formal.state['fields']['width_in'], 'invalid')
        self.assertEqual(session.active_graph_id, 'default')
        # Choosing only another graph of this DAT must not inherit that editor failure.
        w.start_plot_batch(graph_targets=[(session.dataset_id, self.detail.graph_id)], formats=('svg',))
        self.finish_batch()
        self.assertTrue(w.plot_batch_summary.success, w.plot_batch_report)

    def test_invalid_graph_and_missing_target_are_identified_while_valid_graph_exports(self):
        w, session = self.w, self.session
        sidecar = w._plot_state_store.path_for(self.path)
        before_sidecar = sidecar.read_bytes()
        self.detail.view.norm = NormSpec('log')
        self.detail.view.color_limits = (-1., 2.)
        self.detail.view.bounds.update(vmin=-1., vmax=2.)
        w.start_plot_batch(graph_targets=[(session.dataset_id, self.detail.graph_id),
            (session.dataset_id, 'missing-graph'), (session.dataset_id, 'default')], formats=('svg',))
        self.finish_batch()
        results = w.plot_batch_summary.results
        self.assertEqual([r.graph_id for r in results], [self.detail.graph_id, 'missing-graph', 'default'])
        self.assertFalse(results[0].success)
        self.assertIn(self.detail.name, results[0].target_label)
        self.assertEqual(results[1].error_stage, 'prepare')
        self.assertIn('unavailable', results[1].error.lower())
        self.assertTrue(results[2].success, w.plot_batch_report)
        self.assertEqual(w.plot_batch_summary.failed, 2)
        self.assertIn('sidecar', w.plot_batch_report.lower())
        self.assertIn('1 settings warning', w.plot_page.formal.state['batch']['status'])
        self.assertEqual(sidecar.read_bytes(), before_sidecar)
        self.assertEqual(session.active_graph_id, 'default')

    def test_pending_and_cancel_keep_graph_identity_and_completed_no_overwrite_outputs(self):
        w, session = self.w, self.session
        existing = self.folder / (graph_export_stem('sample', 'default', 'Graph 1') + '_export01.png')
        existing.write_bytes(b'keep this prior file')
        w.start_plot_batch(graph_targets=self.targets(), formats=('svg', 'png'), common_folder=self.folder)
        self.assertIn('Detail <α & β>', w.plot_batch_report)
        self.assertIn('Pending export', w.plot_batch_report)
        w._plot_batch_timer.stop()
        w._step_plot_batch()
        w.cancel_plot_batch()
        first, second = w.plot_batch_summary.results
        self.assertTrue(first.success)
        self.assertTrue(second.canceled)
        self.assertEqual(second.graph_id, self.detail.graph_id)
        self.assertIn(self.detail.name, second.target_label)
        self.assertTrue(all(item.path.name.endswith('_export02.' + item.format) for item in first.outputs))
        self.assertEqual(existing.read_bytes(), b'keep this prior file')
        self.assertTrue(all(item.path.exists() for item in first.outputs))
        self.assertFalse(list(self.folder.glob('*Detail*_export*')))
        self.assertIn('Canceled before export', w.plot_batch_report)

    def test_inactive_dataset_export_does_not_change_live_selection_or_inherit_editor_failure(self):
        w, session = self.w, self.session
        other_path = self.folder / 'other.dat'
        other_path.write_bytes(self.path.read_bytes())
        w._load_dat_file_direct(other_path)
        w.open_graph_document()
        pump()
        other = w.workspace.active
        w.plot_page.formal.setDraft('width_in', 'invalid')
        active_controls = (dat.state(w)['xmin'], dat.state(w)['plot_title'], next(entry['label'] for entry in dat.state(w)['colormaps'] if entry['value'] == dat.state(w)['colormap']))
        w.start_plot_batch(graph_targets=self.targets(), formats=('svg',))
        self.finish_batch()
        self.assertTrue(w.plot_batch_summary.success, w.plot_batch_report)
        self.assertIs(w.workspace.active, other)
        self.assertEqual(session.active_graph_id, 'default')
        self.assertEqual(w.plot_page.formal.state['fields']['width_in'], 'invalid')
        self.assertEqual((dat.state(w)['xmin'], dat.state(w)['plot_title'], next(entry['label'] for entry in dat.state(w)['colormaps'] if entry['value'] == dat.state(w)['colormap'])), active_controls)

    def test_project_owned_graphs_use_project_folder_and_never_write_sidecars(self):
        w, session = self.w, self.session
        project_folder = self.folder / 'project'
        project_folder.mkdir()
        w.save_project(project_folder / 'saved.spectra')
        self.assertFalse(session.sidecar_writes_enabled)
        with patch.object(w._plot_state_store, 'save', wraps=w._plot_state_store.save) as save:
            w.start_plot_batch(graph_targets=self.targets(), formats=('svg',))
            self.finish_batch()
        self.assertEqual(save.call_count, 0)
        self.assertTrue(w.plot_batch_summary.success, w.plot_batch_report)
        self.assertTrue(all(output.path.parent == project_folder for result in w.plot_batch_summary.results
                            for output in result.outputs))
        self.assertEqual(session.active_graph_id, 'default')

    def test_dataset_api_retains_active_graph_and_repeated_ids_choose_next_numbers(self):
        w, session = self.w, self.session
        w.activate_graph(self.detail.graph_id)
        w.start_plot_batch([session.dataset_id, session.dataset_id], formats=('svg',))
        self.finish_batch()
        results = w.plot_batch_summary.results
        self.assertTrue(w.plot_batch_summary.success, w.plot_batch_report)
        self.assertEqual([r.graph_id for r in results], [self.detail.graph_id] * 2)
        self.assertEqual([r.outputs[0].path.suffix for r in results], ['.svg', '.svg'])
        self.assertTrue(results[0].outputs[0].path.name.endswith('_export01.svg'))
        self.assertTrue(results[1].outputs[0].path.name.endswith('_export02.svg'))
        self.assertEqual(session.active_graph_id, self.detail.graph_id)

    def test_modal_batch_action_dispatches_checked_graphs_to_real_exports(self):
        dialogs = []
        def create_dialog(sessions, active_id, parent):
            dialog = BatchExportDialog(sessions, active_id, parent)
            dialogs.append(dialog)
            def accept_selected():
                dialog.bridge.selectAll(True)
                dialog.bridge.setFormatIndex(1)
                dialog.accept()
            QTimer.singleShot(0, accept_selected)
            return dialog
        with patch('spectra_app.main_window.BatchExportDialog', side_effect=create_dialog):
            self.w._on_batch_export_plot()
        self.assertEqual(len(dialogs), 1)
        self.finish_batch()
        self.assertTrue(self.w.plot_batch_summary.success, self.w.plot_batch_report)
        self.assertEqual([r.graph_id for r in self.w.plot_batch_summary.results], ['default', self.detail.graph_id])
        self.assertTrue(all(r.outputs[0].path.exists() for r in self.w.plot_batch_summary.results))


if __name__ == '__main__':
    unittest.main()
