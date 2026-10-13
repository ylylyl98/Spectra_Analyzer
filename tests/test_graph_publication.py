"""An analysis owner becomes a graph document only through an explicit plot action."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from spectra_app.background_jobs import JobContext, LoadRequest
from spectra_app.data_import import prepare_data_batch
from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import (AnalysisControls, DatasetDefaults, DatasetSession, FixedRecipe,
                                      GraphState, ViewState, Workspace, initialize_dataset)
from spectra_app.main_window import MainWindow
from spectra_app.plot_dialogs import BatchExportDialog
from spectra_app.plot_state import PlotStateStore, source_fingerprint, validate_plot_collection
from spectra_app.project_io import ProjectFormatError, read_project, write_project
from spectra_app.workbook_state import create_comparison
from tests.background_helpers import wait_jobs
from tests.settings_helpers import isolate_settings
from tests.test_project_io import archive_members, overwrite_archive

APP = QApplication.instance() or QApplication([])


def matrix(path):
    path.write_text('Energy(eV) -1 1\n' + '\n'.join(
        f'{1+i/10} {i*i+1} {2*i*i+3}' for i in range(13)), encoding='utf-8')


class GraphPublicationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'matrix.dat'
        matrix(self.source)
        self.defaults = DatasetDefaults(ViewState(), AnalysisControls(), {
            name: FixedRecipe() for name in ('processing', 'regions', 'region_edges')}, {})
        self.fingerprint = source_fingerprint(self.source)
        self.cube = load_dat_cube(self.source)
        self.session = self.initialize()
        self.workspace = Workspace()
        self.workspace.register(self.session)
        self.workspace.active_dataset_id = self.session.dataset_id
        self.project = self.source.with_suffix('.spectra')

    def initialize(self, saved=None):
        return initialize_dataset(self.source, (self.cube, self.fingerprint, saved, ''), self.defaults)

    def test_fresh_placeholder_and_explicit_clone_have_independent_publication(self):
        self.assertFalse(getattr(self.session.active_graph, 'published', True))
        self.assertFalse(DatasetSession().active_graph.published)
        self.assertTrue(GraphState().published)
        graph = self.session.create_graph('Detail')
        self.assertTrue(graph.published)
        self.assertFalse(self.session.graphs['default'].published)
        self.session.activate_graph('default')
        self.assertFalse(self.session.active_graph.published)

    def test_native_and_sidecar_roundtrip_preserve_hidden_state_and_styles(self):
        self.assertFalse(getattr(self.session.active_graph, 'published', True))
        self.session.view.color_scale_mode = '2'
        self.session.view.color_region_boundaries = [0.]
        self.session.view.colormap = 'magma'
        self.session.create_graph('Explicit')
        write_project(self.project, self.workspace)
        manifest = json.loads(archive_members(self.project)['manifest.json'])
        self.assertEqual(manifest['version'], 8)
        restored = read_project(self.project).active
        self.assertEqual([g.published for g in restored.graphs.values()], [False, True])
        self.assertEqual(restored.graphs['default'].view.color_region_boundaries, [0.])
        self.assertEqual(restored.graphs['default'].view.colormap, 'magma')
        store = PlotStateStore()
        store.save(self.source, self.fingerprint, self.session.plot_collection())
        self.assertEqual(json.loads(store.path_for(self.source).read_text())['version'], 6)
        saved, notice = store.load(self.source, self.fingerprint)
        self.assertEqual(notice, '')
        recalled = self.initialize(saved)
        self.assertEqual([g.published for g in recalled.graphs.values()], [False, True])

    def test_every_legacy_native_version_keeps_saved_graphs_published(self):
        self.session.create_graph('Retained')
        write_project(self.project, self.workspace)
        members = archive_members(self.project)
        current = json.loads(members['manifest.json'])
        for version in range(1, 8):
            with self.subTest(version=version):
                manifest = copy.deepcopy(current)
                manifest['version'] = version
                for graph in manifest['datasets'][0]['graphs']:
                    graph.pop('published', None)
                    if version < 7:
                        graph.pop('source_mode')
                        graph.pop('gate_indices')
                if version < 4:
                    for field in ('workbooks', 'sheets', 'comparisons'):
                        manifest.pop(field)
                elif version < 6:
                    for sheet in manifest['sheets']:
                        for field in ('role', 'fit_gate_idx', 'text_columns', 'snapshot_note'):
                            sheet.pop(field)
                if version < 3:
                    manifest.pop('graph_workspace')
                if version == 1:
                    record = manifest['datasets'][0]
                    graph = record.pop('graphs')[0]
                    record.pop('active_graph_id')
                    record.update(view=graph['view'], presentation=graph['presentation'])
                members['manifest.json'] = json.dumps(manifest).encode()
                overwrite_archive(self.project, members)
                restored = read_project(self.project).active
                self.assertTrue(all(getattr(g, 'published', False) for g in restored.graphs.values()))
                self.assertEqual(len(restored.graphs), 1 if version == 1 else 2)

    def test_every_legacy_sidecar_version_publishes_saved_graphs(self):
        recipe = self.session.view.plot_recipe(self.cube, self.session.applied, self.session.presentation)
        for version in range(1, 6):
            with self.subTest(version=version):
                if version < 4:
                    payload = copy.deepcopy(recipe)
                    if version < 3:
                        for field in ('norm', 'presentation', 'color_region_limits'):
                            payload.pop(field)
                    if version == 1:
                        for field in ('title_settings', 'processing'):
                            payload.pop(field)
                else:
                    payload = self.session.plot_collection()
                    for graph in payload['graphs']:
                        graph.pop('published', None)
                        if version == 4:
                            graph.pop('source_mode')
                            graph.pop('gate_indices')
                saved = validate_plot_collection(payload, version=version)
                self.assertTrue(all(g.get('published', False) for g in saved['graphs']))
                self.assertTrue(self.initialize(saved).active_graph.published)
        self.assertTrue(getattr(self.initialize(recipe).active_graph, 'published', False))

    def test_publication_is_strict_boolean_and_old_schemas_reject_new_fields(self):
        write_project(self.project, self.workspace)
        members = archive_members(self.project)
        current = json.loads(members['manifest.json'])
        for value in (None, 0, 1, 'false', []):
            with self.subTest(value=value):
                manifest = copy.deepcopy(current)
                manifest['datasets'][0]['graphs'][0]['published'] = value
                members['manifest.json'] = json.dumps(manifest).encode()
                overwrite_archive(self.project, members)
                with self.assertRaises(ProjectFormatError):
                    read_project(self.project)
                collection = self.session.plot_collection()
                collection['graphs'][0]['published'] = value
                with self.assertRaises(ValueError):
                    validate_plot_collection(collection)
        current['version'] = 7
        current['datasets'][0]['graphs'][0]['published'] = True
        members['manifest.json'] = json.dumps(current).encode()
        overwrite_archive(self.project, members)
        with self.assertRaises(ProjectFormatError):
            read_project(self.project)
        collection = self.session.plot_collection()
        collection['graphs'][0]['published'] = True
        with self.assertRaises(ValueError):
            validate_plot_collection(collection, version=5)

    def test_hidden_document_layout_is_rejected_even_for_closed_geometry(self):
        self.assertFalse(getattr(self.session.active_graph, 'published', True))
        key = [self.session.dataset_id, 'default']
        for opened, rect in ((True, None), (False, [0, 0, 500, 400]), (False, None)):
            with self.subTest(opened=opened, rect=rect):
                self.workspace.graph_workspace = dict(mode='free', documents=[
                    dict(key=key, open=opened, rect=rect)], active=key if opened else None,
                    stacking=[key] if opened else [])
                with self.assertRaisesRegex(ProjectFormatError, 'unpublished'):
                    write_project(self.project, self.workspace)


class GraphPublicationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.errors = []
        self.w._show_error = self.errors.append
        self.addCleanup(self.dispose)
        self.paths = [self.folder / 'matrix.dat', self.folder / 'curves.csv']
        matrix(self.paths[0])
        self.paths[1].write_text('x,a,b\n0,10,20\n1,20,40\n2,30,60\n')

    def dispose(self):
        wait_jobs(self.w)
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def entries(self, paths=None):
        entries, errors = prepare_data_batch([LoadRequest(path, None, self.w._available_colormaps())
            for path in (paths or self.paths)], self.w.snapshot_dataset_defaults(),
            JobContext(Event(), lambda _: None))
        self.assertFalse(errors)
        return entries

    def heatmap_items(self):
        tree = self.w.data_navigator
        return [tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(tree.topLevelItemCount())
                if tree.topLevelItem(i).data(0, Qt.UserRole)[0] == 'heatmap']

    def test_ordinary_mixed_import_opens_first_owner_and_preserves_prior_windows(self):
        previous = create_comparison(self.w.workspace, 'Already open')
        page = self.w.open_xy_comparison(previous.comparison_id)
        prior = ('xy', previous.comparison_id, '')
        prior_window = self.w.graph_documents._windows[prior]
        book = self.w.import_data_files(self.entries())
        self.assertEqual(len(self.w.workspace.workbooks), 2)
        self.assertEqual(len(self.w.workspace.datasets), 1)
        self.assertEqual(len(self.w.workspace.sheets), 2)
        self.assertEqual(set(self.w.graph_documents._windows), {prior, ('workbook', book.workbook_id, '')})
        self.assertIs(self.w.graph_documents._windows[prior], prior_window)
        self.assertIs(self.w.graph_documents.page_for(prior), page)
        self.assertEqual(self.heatmap_items(), [])
        self.assertEqual([len(s.y_columns) for s in self.w.workspace.sheets.values()], [2, 2])

    def test_single_and_repeated_import_reuse_workbook_and_keep_implicit_graph_out_of_inventories(self):
        book = self.w.import_data_files(self.entries(self.paths[:1]))
        self.assertEqual(set(self.w.graph_documents._windows), {('workbook', book.workbook_id, '')})
        self.assertEqual(self.w.import_data_files(self.entries(self.paths[:1])).workbook_id, book.workbook_id)
        self.assertEqual(len(self.w.workspace.workbooks), 1)
        session = next(iter(self.w.workspace.datasets.values()))
        self.w.activate_dataset(session.dataset_id)
        self.w._select_graph_page(session, show=True)
        self.w.plot_tabs.setCurrentWidget(self.w.graph_documents)
        self.assertEqual(self.heatmap_items(), [])
        self.assertEqual(self.w.graph_combo.count(), 0)
        self.assertNotIn((session.dataset_id, 'default'), self.w.graph_documents._windows)
        self.assertTrue(all(len(r['key']) == 3 for r in self.w._graph_workspace_snapshot()['documents']))
        dialog = BatchExportDialog([session], session.dataset_id)
        self.addCleanup(dialog.deleteLater)
        dialog.select_all_btn.click()
        self.assertEqual(dialog.selected_graph_targets(), [])
        self.w.start_plot_batch(graph_targets=[(session.dataset_id, 'default')], formats=('svg',))
        self.assertEqual(self.w._plot_batch_basenames, ())
        self.assertIn('unpublished', self.w._plot_batch_entries[0].error)

    def test_processing_save_reload_never_publishes_analysis_graph(self):
        book = self.w.import_data_files(self.entries(self.paths[:1]))
        session = next(iter(self.w.workspace.datasets.values()))
        original = session.base_cube.z.copy()
        self.w.activate_dataset(session.dataset_id)
        self.w.processing_mode_combo.setCurrentIndex(self.w.processing_mode_combo.findData('energy_second_derivative'))
        self.w.processing_smooth_chk.setChecked(False)
        self.w._on_generate_heatmap()
        wait_jobs(self.w)
        self.assertEqual(session.applied.mode, 'energy_second_derivative')
        np.testing.assert_array_equal(session.base_cube.z, original)
        self.assertFalse(np.array_equal(session.current_cube.z, original))
        self.w.open_workbook(book.workbook_id)
        saved = self.w.save_project(self.folder / 'processed.spectra')
        self.w.open_project(saved)
        restored = self.w.workspace.datasets[session.dataset_id]
        self.assertFalse(getattr(restored.active_graph, 'published', True))
        self.assertEqual(set(self.w.graph_documents._windows), {('workbook', book.workbook_id, '')})
        self.assertEqual(self.heatmap_items(), [])
        self.assertEqual(self.errors, [])

    def test_explicit_open_and_quick_heatmap_publish_then_close_reload_retains_document(self):
        book = self.w.import_data_files(self.entries(self.paths[:1]))
        session = next(iter(self.w.workspace.datasets.values()))
        self.w.open_graph_document(session.dataset_id, 'default')
        self.assertTrue(getattr(session.active_graph, 'published', False))
        key = (session.dataset_id, 'default')
        self.assertIn(key, self.w.graph_documents._windows)
        self.assertIn(('heatmap', key), self.heatmap_items())
        sidecar, notice = PlotStateStore().load(self.paths[0], session.loaded_fingerprint)
        self.assertEqual(notice, '')
        self.assertTrue(sidecar['graphs'][0]['published'])
        self.w.graph_documents._windows[key].close()
        self.w.open_workbook(book.workbook_id)
        saved = self.w.save_project(self.folder / 'closed.spectra')
        self.w.open_project(saved)
        self.assertNotIn(key, self.w.graph_documents._windows)
        self.assertIn(('heatmap', key), self.heatmap_items())
        self.w.open_graph_document(*key)
        self.assertIn(key, self.w.graph_documents._windows)
        session = self.w.workspace.datasets[session.dataset_id]
        sheet = self.w.workspace.sheets[book.sheet_ids[0]]
        self.w.open_workbook(book.workbook_id)
        graph = self.w._quick_sheet_plot(sheet.sheet_id, 'heatmap')
        self.assertTrue(graph.published)
        self.assertEqual((graph.source_mode, graph.gate_indices), ('raw', [0, 1]))

    def test_quick_matched_placeholder_is_published_before_sidecar_save(self):
        book = self.w.import_data_files(self.entries(self.paths[:1]))
        session = next(iter(self.w.workspace.datasets.values()))
        session.active_graph.source_mode, session.active_graph.gate_indices = 'raw', [0, 1]
        graph = self.w._quick_sheet_plot(book.sheet_ids[0], 'heatmap')
        self.assertEqual(graph.graph_id, 'default')
        self.assertTrue(getattr(graph, 'published', False))
        self.assertEqual(self.heatmap_items(), [('heatmap', (session.dataset_id, 'default'))])
        saved, notice = PlotStateStore().load(self.paths[0], session.loaded_fingerprint)
        self.assertEqual(notice, '')
        self.assertTrue(saved['graphs'][0]['published'])

    def test_first_quick_heatmap_after_reload_appears_in_project_immediately(self):
        book = self.w.import_data_files(self.entries(self.paths[:1]))
        saved = self.w.save_project(self.folder / 'unplotted.spectra')
        self.w.open_project(saved)
        session = next(iter(self.w.workspace.datasets.values()))
        graph = self.w._quick_sheet_plot(book.sheet_ids[0], 'heatmap')
        self.assertTrue(graph.published)
        self.assertFalse(session.graphs['default'].published)
        self.assertEqual(self.heatmap_items(), [('heatmap', (session.dataset_id, graph.graph_id))])

    def test_invalid_outgoing_editor_does_not_publish(self):
        book = self.w.import_data_files(self.entries(self.paths[:1]))
        session = next(iter(self.w.workspace.datasets.values()))
        target = create_comparison(self.w.workspace, 'Draft')
        page = self.w.open_xy_comparison(target.comparison_id)
        page.name_edit.setText(' ')
        with self.assertRaises(ValueError):
            self.w.open_graph_document(session.dataset_id, 'default')
        self.assertFalse(getattr(session.active_graph, 'published', True))
        self.assertIsNone(self.w._quick_sheet_plot(book.sheet_ids[0], 'heatmap'))
        self.assertFalse(session.active_graph.published)
        self.assertEqual(len(session.graphs), 1)
        page.name_edit.setText('Draft')

    def test_invalid_analysis_editor_blocks_first_explicit_open(self):
        self.w.import_data_files(self.entries(self.paths[:1]))
        session = next(iter(self.w.workspace.datasets.values()))
        self.w.activate_dataset(session.dataset_id)
        self.w.xmin_spin.lineEdit().setText('1e')
        with self.assertRaisesRegex(ValueError, 'range editor'):
            self.w.open_graph_document(session.dataset_id, 'default')
        self.assertFalse(session.active_graph.published)
        self.w.xmin_spin.lineEdit().setText('1')


if __name__ == '__main__':
    unittest.main()
