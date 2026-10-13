import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import (AnalysisControls, DatasetDefaults, FixedRecipe,
                                      GraphState, ViewState, Workspace, detached_graph_session,
                                      initialize_dataset)
from spectra_app.plot_state import PlotStateStore, source_fingerprint
from spectra_app.project_io import (ProjectFormatError, read_project, runtime_workspace_stamp,
                                    workspace_digest, write_project)


class NamedGraphsStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'signal.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(12)), encoding='utf-8')
        self.defaults = DatasetDefaults(ViewState(), AnalysisControls(), {
            name: FixedRecipe() for name in ('processing', 'regions', 'region_edges')}, {})
        self.fingerprint = source_fingerprint(self.path)
        self.session = initialize_dataset(self.path, (load_dat_cube(self.path), self.fingerprint,
                                                     None, ''), self.defaults)
        self.workspace = Workspace()
        self.workspace.register(self.session)
        self.workspace.active_dataset_id = self.session.dataset_id

    def test_clone_switch_names_and_science_identity(self):
        session = self.session
        session.batch_peak_map[0] = np.array([1])
        session.view.bounds['xmin'] = 1.4
        original = session.active_graph
        graph = session.create_graph('  细节 α  ')
        self.assertEqual(graph.name, '细节 α')
        self.assertIsNot(graph.view, original.view)
        self.assertIsNot(graph.view.bounds, original.view.bounds)
        self.assertIsNot(graph.presentation, original.presentation)
        self.assertEqual(graph.view.bounds['xmin'], 1.4)
        graph.view.bounds['xmin'] = 1.5
        cube, peaks = session.current_cube, session.batch_peak_map
        session.activate_graph(original.graph_id)
        self.assertEqual(session.view.bounds['xmin'], 1.4)
        self.assertIs(session.current_cube, cube)
        self.assertIs(session.batch_peak_map, peaks)
        with self.assertRaises(ValueError):
            session.create_graph('细节 α')
        with self.assertRaises(ValueError):
            session.rename_graph(graph.graph_id, ' ')
        session.remove_graph(graph.graph_id)
        with self.assertRaises(ValueError):
            session.remove_graph(original.graph_id)

    def test_preview_detaches_active_visual_state_without_copying_science(self):
        self.session.create_graph('Detail')
        digest = workspace_digest(self.workspace)
        candidate = detached_graph_session(self.session)
        candidate.view = copy.deepcopy(candidate.view)
        candidate.view.annotations['plot_title'] = 'candidate'
        candidate.view.bounds['xmin'] = 1.5
        self.assertEqual(workspace_digest(self.workspace), digest)
        self.assertIs(candidate.current_cube, self.session.current_cube)
        self.assertIs(candidate.batch_fit_map, self.session.batch_fit_map)
        self.assertIsNot(candidate.graphs, self.session.graphs)

    def test_native_v8_missing_source_roundtrip_and_inactive_digest_validation(self):
        graph = self.session.create_graph('Detail')
        graph.view.x_limits = (1.2, 1.8)
        graph.view.locks['xmin'] = True
        self.session.activate_graph('default')
        exact, runtime = workspace_digest(self.workspace), runtime_workspace_stamp(self.workspace, {})
        graph.view.annotations['plot_title'] = 'inactive edit'
        self.assertNotEqual(workspace_digest(self.workspace), exact)
        self.assertNotEqual(runtime_workspace_stamp(self.workspace, {}), runtime)
        destination = self.path.with_suffix('.spectra')
        saved = write_project(destination, self.workspace)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(json.loads(archive.read('manifest.json'))['version'], 8)
        self.path.unlink()
        restored = read_project(destination)
        self.assertEqual(workspace_digest(restored), saved)
        self.assertEqual(list(restored.active.graphs), ['default', graph.graph_id])
        self.assertEqual(restored.active.active_graph_id, 'default')
        self.assertEqual(restored.active.graphs[graph.graph_id].view.x_limits, (1.2, 1.8))
        graph.view.gate = float('nan')
        with self.assertRaises(ProjectFormatError):
            write_project(destination, self.workspace)

    def test_strict_v1_migration_and_malformed_v2_graphs(self):
        destination = self.path.with_suffix('.spectra')
        write_project(destination, self.workspace)
        with zipfile.ZipFile(destination) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        for field in ('workbooks', 'sheets', 'comparisons'):
            manifest.pop(field)
        for graph in manifest['datasets'][0]['graphs']:
            graph.pop('published')
            graph.pop('source_mode')
            graph.pop('gate_indices')
        legacy = copy.deepcopy(manifest)
        legacy['version'] = 1
        legacy.pop('graph_workspace')
        record = legacy['datasets'][0]
        graph = record.pop('graphs')[0]
        record.pop('active_graph_id')
        record.update(view=graph['view'], presentation=graph['presentation'])
        def install(payload):
            with zipfile.ZipFile(destination, 'w') as archive:
                for name, value in members.items():
                    archive.writestr(name, json.dumps(payload).encode() if name == 'manifest.json' else value)
        install(legacy)
        migrated = read_project(destination)
        self.assertEqual(migrated.active.active_graph_id, 'default')
        self.assertEqual(migrated.active.active_graph.name, 'Graph 1')
        v2 = copy.deepcopy(manifest)
        v2['version'] = 2
        v2.pop('graph_workspace')
        install(v2)
        self.assertIsNone(read_project(destination).graph_workspace)
        del legacy['datasets'][0]['view']['bounds']
        install(legacy)
        with self.assertRaises(ProjectFormatError):
            read_project(destination)
        for mutate in (
                lambda r: r.update(graphs=[]),
                lambda r: r.update(active_graph_id='missing'),
                lambda r: r['graphs'].append(copy.deepcopy(r['graphs'][0])),
                lambda r: r['graphs'][0].update(name=' ')):
            bad = copy.deepcopy(v2)
            mutate(bad['datasets'][0])
            install(bad)
            with self.assertRaises(ProjectFormatError):
                read_project(destination)

    def test_sidecar_v6_collection_roundtrip_and_legacy_active_recipe_access(self):
        self.session.create_graph('细节')
        self.session.view.annotations['plot_title'] = 'second'
        self.session.view.gate = 2.
        store = PlotStateStore()
        store.save(self.path, self.fingerprint, self.session.plot_collection())
        payload = json.loads(store.path_for(self.path).read_text(encoding='utf-8'))
        self.assertEqual(payload['version'], 6)
        self.assertNotIn('annotations', payload['plot'])
        collection, notice = store.load(self.path, self.fingerprint)
        self.assertEqual(notice, '')
        self.assertEqual(collection['annotations']['plot_title'], 'second')
        recalled = initialize_dataset(self.path, (load_dat_cube(self.path), self.fingerprint,
                                                  collection, ''), self.defaults)
        self.assertEqual([g.name for g in recalled.graphs.values()], ['Graph 1', '细节'])
        self.assertEqual(recalled.active_graph_id, self.session.active_graph_id)
        self.assertEqual(recalled.view.gate, 2.)
        self.assertIsNone(store.load(self.path, 'changed fingerprint')[0])
        recipe = self.session.view.plot_recipe(self.session.current_cube, self.session.applied,
                                               self.session.presentation)
        for version in (1, 2, 3):
            old = copy.deepcopy(recipe)
            if version < 3:
                for key in ('norm', 'presentation', 'color_region_limits'):
                    old.pop(key)
            if version == 1:
                for key in ('title_settings', 'processing'):
                    old.pop(key)
            store.path_for(self.path).write_text(json.dumps({'version': version,
                'fingerprint': self.fingerprint, 'plot': old}), encoding='utf-8')
            migrated, notice = store.load(self.path, self.fingerprint)
            self.assertEqual(notice, '')
            self.assertEqual(migrated['active_graph_id'], 'default')
            self.assertEqual(migrated['annotations']['plot_title'], 'second')

    def test_fixed_processing_sidecar_recall_refreshes_inactive_color_locks_and_caches(self):
        session = self.session
        session.create_graph('Detail')
        session.view.locks['vmin'] = session.view.locks['vmax'] = True
        session.view.bounds.update(vmin=100., vmax=200.)
        session.view.color_limits = (100., 200.)
        session.view.last_valid_region_color_limits = []
        session.activate_graph('default')
        collection = session.plot_collection()
        self.defaults.fixed_recipes['processing'] = FixedRecipe(True, {
            'mode': 'energy_second_derivative', 'smooth': False, 'window_length': 21})
        recalled = initialize_dataset(self.path, (session.base_cube, self.fingerprint, collection, ''), self.defaults)
        self.assertEqual(recalled.applied.mode, 'energy_second_derivative')
        for graph in recalled.graphs.values():
            self.assertFalse(graph.view.locks['vmin'])
            self.assertFalse(graph.view.locks['vmax'])
            self.assertLess(abs(graph.view.color_limits[0]), 1.)
            self.assertEqual(graph.view.last_valid_region_color_limits, [])

    def test_native_and_sidecar_reject_duplicate_names_bad_ids_and_inactive_invalid_style(self):
        graph = self.session.create_graph('Detail')
        self.session.activate_graph('default')
        destination = self.path.with_suffix('.spectra')
        graph.name = 'Graph 1'
        with self.assertRaises(ProjectFormatError):
            write_project(destination, self.workspace)
        graph.name = 'Detail'
        graph.graph_id = 'wrong key'
        with self.assertRaises(ProjectFormatError):
            write_project(destination, self.workspace)
        graph.graph_id = 'default'
        with self.assertRaises(ProjectFormatError):
            write_project(destination, self.workspace)
        graph.graph_id = next(key for key in self.session.graphs if key != 'default')
        collection = self.session.plot_collection()
        collection['graphs'][1]['plot']['presentation']['width_in'] = float('nan')
        with self.assertRaises(ValueError):
            PlotStateStore().save(self.path, self.fingerprint, collection)
