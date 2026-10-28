import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

from spectra_app.dataset_state import (AnalysisControls, DatasetDefaults, FixedRecipe,
    SessionField, ViewState, Workspace, initialize_dataset)
from spectra_app.dat_io import load_dat_cube
from spectra_app.plot_snapshot import snapshot_dataset
from spectra_app.plot_presentation import PlotPresentation
from spectra_app.project_io import ProjectFormatError, read_project, write_project, workspace_digest, runtime_workspace_stamp


class AnalysisDocumentStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'source.dat'
        self.path.write_text('Energy(eV) -2 0 3\n1 1 2 3\n2 3 4 5\n3 2 3 4\n')
        defaults = DatasetDefaults(ViewState(), AnalysisControls(),
            {name: FixedRecipe() for name in ('processing', 'regions', 'region_edges')}, {})
        self.session = initialize_dataset(self.path, (load_dat_cube(self.path), None, None, ''), defaults)
        self.workspace = Workspace()
        self.workspace.register(self.session)
        self.workspace.active_dataset_id = self.session.dataset_id

    def test_science_owner_reuse_and_duplicate_are_isolated(self):
        from spectra_app.analysis_document import ensure_analysis_graph, analysis_owner, duplicate_analysis_graph
        session = self.session
        source = session.active_graph
        graph = ensure_analysis_graph(session)
        owner = analysis_owner(session)
        self.assertIsNot(owner, session)
        self.assertFalse(np.shares_memory(owner.current_cube.z, session.current_cube.z))
        owner.peak_indices = np.array([1])
        owner.batch_peak_map[0] = np.array([1])
        owner.analysis_controls.fit_model = 'Gaussian'
        self.assertEqual(session.peak_indices.size, 0)
        self.assertEqual(session.batch_peak_map, {})
        self.assertEqual(session.analysis_controls.fit_model, 'Lorentzian')
        session.activate_graph(source.graph_id)
        self.assertIs(ensure_analysis_graph(session), graph)
        clone = duplicate_analysis_graph(session)
        clone.analysis.science.batch_peak_map[0][0] = 2
        self.assertEqual(owner.batch_peak_map[0][0], 1)
        self.assertNotEqual(clone.analysis.input_version, graph.analysis.input_version)

    def test_descriptor_routes_only_science_and_gate_without_swapping_active(self):
        from spectra_app.analysis_document import ensure_analysis_graph
        class Window:
            peaks = SessionField('peak_indices')
            cube = SessionField('current_cube')
            gate = SessionField('view', 'gate')
        window = Window()
        window.workspace, window._unregistered_session = self.workspace, self.session
        graph = ensure_analysis_graph(self.session)
        window.peaks = np.array([2])
        window.gate = 3
        self.assertIs(self.workspace.active, self.session)
        self.assertIs(window.cube, graph.analysis.science.current_cube)
        self.assertEqual(graph.analysis.science.view.gate, 3)
        self.assertEqual(self.session.peak_indices.size, 0)

    def test_snapshot_follows_source_format_not_source_science_and_detaches_on_delete(self):
        from spectra_app.analysis_document import ensure_analysis_graph, graph_presentation
        session = self.session
        source = session.active_graph
        graph = ensure_analysis_graph(session)
        frozen_z = graph.analysis.science.current_cube.z.copy()
        source.view.annotations['plot_title'] = 'Updated source title'
        source.view.colormap = 'magma'
        source.presentation = replace(source.presentation, numeric_pt=13)
        session.current_cube = replace(session.current_cube, z=session.current_cube.z * 10)
        snap = snapshot_dataset(session)
        self.assertEqual(snap.title, 'Updated source title')
        self.assertEqual(snap.cmap, 'magma')
        self.assertEqual(graph_presentation(session).numeric_pt, 13)
        np.testing.assert_array_equal(snap.z, frozen_z)
        session.remove_graph(source.graph_id)
        self.assertIsNone(graph.analysis.source_graph_id)
        self.assertEqual(snapshot_dataset(session).title, 'Updated source title')

    def test_roundtrip_preserves_analysis_without_recomputation(self):
        from spectra_app.analysis_document import ensure_analysis_graph, AnalysisSeed
        graph = ensure_analysis_graph(self.session)
        graph.analysis.set_seeds(1, [AnalysisSeed(position=2.125, kind='dip', width=.2,
            fixed_center=True, manual=True)])
        graph.analysis.science.view.gate = 0
        graph.analysis.gate_scope = 'current'
        graph.analysis.gate_messages[1] = 'Manual seeds ready'
        target = Path(self.tmp.name) / 'analysis.spextra'
        before = write_project(target, self.workspace)
        loaded = read_project(target)
        self.assertEqual(before, workspace_digest(loaded))
        restored = loaded.active.active_graph.analysis
        self.assertEqual(restored.source_graph_id, 'default')
        self.assertEqual(restored.seeds_by_gate[1][0].position, 2.125)
        self.assertTrue(restored.seeds_by_gate[1][0].fixed_center)
        self.assertEqual(restored.gate_messages[1], 'Manual seeds ready')
        self.assertEqual(restored.gate_scope, 'current')
        self.assertFalse(np.shares_memory(restored.science.current_cube.z, loaded.active.current_cube.z))

    def _pool(self, gate=0):
        from spectra_app.peak_candidates import PeakCandidate
        cube = self.session.current_cube
        return [PeakCandidate(i, float(cube.energy[i]), float(cube.z[gate, i]), kind,
            prominence, prominence, .1, noise, 'Original') for i, kind, prominence, noise in
            ((0, 'peak', .01, 1), (1, 'dip', .2, 6), (2, 'peak', .3, 8))]

    def _fit(self):
        from spectra_app.fitting import FitResult
        x = self.session.current_cube.energy.copy()
        return FitResult('Gaussian', x, np.ones(x.size), np.ones((1, x.size)),
            np.array([2.123]), np.array([-1.]), np.array([.15]), 0., .1, .9)

    def test_filter_recovers_candidates_preserving_manual_exclusions_moves_and_additions(self):
        from spectra_app.analysis_document import ensure_analysis_graph, AnalysisSeed
        from spectra_app.peak_candidates import candidate_filter_preset
        doc = ensure_analysis_graph(self.session).analysis
        doc.set_candidates(0, self._pool(), candidate_filter_preset('Loose'))
        old_ids = [seed.seed_id for seed in doc.seeds_by_gate[0]]
        seeds = list(doc.seeds_by_gate[0])
        seeds[1] = replace(seeds[1], enabled=False, manual=True)
        seeds[2] = replace(seeds[2], position=2.91, manual=True, width=.2)
        seeds.append(AnalysisSeed(1.414, manual=True, width=.13))
        doc.set_seeds(0, seeds)
        doc.apply_filter(candidate_filter_preset('Strict'), 0)
        self.assertFalse(doc.seeds_by_gate[0][0].enabled)
        doc.apply_filter(candidate_filter_preset('Loose'), 0)
        self.assertEqual(len(doc.candidate_pools[0]), 3)
        self.assertEqual(len(doc.seeds_by_gate[0]), 4)
        self.assertEqual([seed.seed_id for seed in doc.seeds_by_gate[0]][:3], old_ids)
        self.assertTrue(doc.seeds_by_gate[0][0].enabled)
        self.assertFalse(doc.seeds_by_gate[0][1].enabled)
        self.assertEqual(doc.seeds_by_gate[0][2].position, 2.91)
        self.assertEqual(doc.seeds_by_gate[0][2].width, .2)
        self.assertEqual(doc.seeds_by_gate[0][3].position, 1.414)

    def test_seed_edits_and_find_retire_fits_but_noop_filter_keeps_status(self):
        from spectra_app.analysis_document import ensure_analysis_graph
        from spectra_app.peak_candidates import candidate_filter_preset
        doc = ensure_analysis_graph(self.session).analysis
        settings = candidate_filter_preset('Loose')
        doc.set_candidates(0, self._pool(), settings)
        fit = self._fit()
        doc.record_fit(0, fit)
        token = doc.job_token
        doc.apply_filter(settings, 0)
        self.assertEqual(doc.gate_status[0], 'fitted')
        self.assertIs(doc.science.batch_fit_result_map[0], fit)
        self.assertEqual(doc.job_token, token)
        doc.set_candidates(0, self._pool(), settings)
        self.assertNotEqual(doc.job_token, token)
        self.assertNotIn(0, doc.science.batch_fit_result_map)
        self.assertIsNone(doc.science.fit_result)
        self.assertEqual(doc.gate_status[0], 'detected')
        doc.record_fit(0, fit)
        seeds = list(doc.seeds_by_gate[0])
        seeds[0] = replace(seeds[0], position=1.1, manual=True)
        doc.set_seeds(0, seeds)
        self.assertNotIn(0, doc.science.batch_fit_result_map)

    def test_input_binding_selected_display_all_gates_and_rebind(self):
        from spectra_app.analysis_document import ensure_analysis_graph, rebind_analysis_input
        self.session.active_graph.gate_indices = [1]
        graph = ensure_analysis_graph(self.session)
        old = graph.analysis
        self.assertEqual(old.science.current_cube.gate.size, 3)
        self.assertEqual(snapshot_dataset(self.session).y.tolist(), [0.])
        old.set_candidates(2, self._pool(2))
        self.session.current_cube = replace(self.session.current_cube, z=self.session.current_cube.z*3)
        rebound = rebind_analysis_input(self.session, source_mode='processed', gate_indices=[1])
        self.assertNotEqual(rebound.input_version, old.input_version)
        self.assertEqual(rebound.candidate_pools, {})
        self.assertEqual(rebound.seeds_by_gate, {})
        np.testing.assert_array_equal(rebound.science.current_cube.z, self.session.current_cube.z)
        self.assertFalse(np.shares_memory(rebound.science.current_cube.z, self.session.current_cube.z))

    def test_progress_and_styles_change_save_stamp_and_survive_deleted_source(self):
        from spectra_app.analysis_document import ensure_analysis_graph
        graph = ensure_analysis_graph(self.session)
        cache = {}
        before = runtime_workspace_stamp(self.workspace, cache)
        graph.analysis.set_candidates(0, self._pool())
        self.assertNotEqual(before, runtime_workspace_stamp(self.workspace, cache))
        graph.analysis.record_fit(0, self._fit())
        graph.analysis.overlay_styles['fitted-dip'] = replace(graph.analysis.overlay_styles['fitted-dip'], visible=False)
        self.session.remove_graph('default')
        target = Path(self.tmp.name) / 'detached.spextra'
        digest = write_project(target, self.workspace)
        loaded = read_project(target)
        self.assertEqual(workspace_digest(loaded), digest)
        restored = loaded.active.active_graph.analysis
        self.assertIsNone(restored.source_graph_id)
        self.assertEqual(len(restored.candidate_pools[0]), 3)
        self.assertEqual(restored.gate_status[0], 'fitted')
        self.assertFalse(restored.overlay_styles['fitted-dip'].visible)

    def test_malformed_analysis_links_indices_seeds_and_cycles_rejected(self):
        from spectra_app.analysis_document import ensure_analysis_graph
        graph = ensure_analysis_graph(self.session)
        graph.analysis.set_candidates(0, self._pool())
        target = Path(self.tmp.name) / 'invalid.spextra'
        write_project(target, self.workspace)
        with zipfile.ZipFile(target) as archive:
            original = {name: archive.read(name) for name in archive.namelist()}
        def mutate_table(a, members, family, field, mutate):
            reference = a[family][0][field]
            table = np.load(io.BytesIO(members[reference]), allow_pickle=False)
            changed = mutate(table)
            buffer = io.BytesIO()
            np.save(buffer, table if changed is None else changed, allow_pickle=False)
            members[reference] = buffer.getvalue()
        candidates = lambda a, m, change: mutate_table(a, m, 'candidate_pools', 'candidates', change)
        seeds = lambda a, m, change: mutate_table(a, m, 'seeds_by_gate', 'seeds', change)
        cases = {
            'missing source': lambda a, m: a.update(source_graph_id='missing'),
            'self source': lambda a, m: a.update(source_graph_id=graph.graph_id),
            'bad Gate': lambda a, m: a['candidate_pools'][0].update(gate=999),
            'bad sample': lambda a, m: candidates(a, m, lambda t: t.__setitem__((0, 0), 999)),
            'fractional sample': lambda a, m: candidates(a, m, lambda t: t.__setitem__((0, 0), 1.5)),
            'mismatched input': lambda a, m: candidates(a, m, lambda t: t.__setitem__((0, 1), 99)),
            'invalid candidate code': lambda a, m: candidates(a, m, lambda t: t.__setitem__((0, 3), 2)),
            'missing candidate value': lambda a, m: candidates(a, m, lambda t: t.__setitem__((0, 4), np.nan)),
            'duplicate candidates': lambda a, m: candidates(a, m, lambda t: np.concatenate((t, t[:1]))),
            'duplicate seeds': lambda a, m: a['seeds_by_gate'][0]['ids'].__setitem__(1, a['seeds_by_gate'][0]['ids'][0]),
            'invalid seed code': lambda a, m: seeds(a, m, lambda t: t.__setitem__((0, 2), 2)),
            'missing seed position': lambda a, m: seeds(a, m, lambda t: t.__setitem__((0, 0), np.nan)),
            'invalid seed optional': lambda a, m: seeds(a, m, lambda t: t.__setitem__((0, 5), np.inf)),
            'invalid bounds': lambda a, m: seeds(a, m, lambda t: t.__setitem__((0, slice(9, 11)), [3, 2])),
            'invalid status': lambda a, m: a['gate_status'][0].update(status='fitted'),
            'nested graph': lambda a, m: a['science']['graphs'][0].update(analysis=copy.deepcopy(a)),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                manifest = json.loads(original['manifest.json'])
                members = dict(original)
                mutate(manifest['datasets'][0]['graphs'][1]['analysis'], members)
                members['manifest.json'] = json.dumps(manifest).encode()
                with zipfile.ZipFile(target, 'w') as archive:
                    for key, value in members.items():
                        archive.writestr(key, value)
                with self.assertRaises(ProjectFormatError):
                    read_project(target)
        # In-memory object cycles are rejected before recursive serialization.
        graph.analysis.science.active_graph.analysis = graph.analysis
        with self.assertRaisesRegex(ProjectFormatError, 'Nested analysis'):
            write_project(target, self.workspace)

    def test_source_sidecar_never_contains_analysis(self):
        from spectra_app.analysis_document import ensure_analysis_graph
        graph = ensure_analysis_graph(self.session)
        collection = self.session.plot_collection()
        self.assertEqual(collection['active_graph_id'], 'default')
        self.assertEqual([item['graph_id'] for item in collection['graphs']], ['default'])
        self.assertNotIn(graph.graph_id, [item['graph_id'] for item in collection['graphs']])

    def test_voigt_components_roundtrip_and_invalid_gamma_rejected(self):
        from spectra_app.analysis_document import ensure_analysis_graph
        graph = ensure_analysis_graph(self.session)
        fit = replace(self._fit(), model='Voigt', sigmas=np.array([.15]), gammas=np.array([.07]))
        graph.analysis.record_fit(2, fit)
        target = Path(self.tmp.name) / 'voigt.spextra'
        write_project(target, self.workspace)
        restored = read_project(target).active.active_graph.analysis.science.batch_fit_result_map[2]
        self.assertEqual(restored.model, 'Voigt')
        np.testing.assert_array_equal(restored.centers, [2.123])
        np.testing.assert_array_equal(restored.sigmas, [.15])
        np.testing.assert_array_equal(restored.gammas, [.07])
        for invalid in (replace(fit, gammas=None), replace(fit, gammas=np.array([-1.]))):
            with self.subTest(invalid=invalid.gammas):
                graph.analysis.science.fit_result = invalid
                with self.assertRaises(ProjectFormatError):
                    write_project(target, self.workspace)
        with self.assertRaises(ValueError):
            replace(fit, sigmas=np.array([.18]))

    def test_xy_progress_json_codec_and_conditional_legacy_schema(self):
        from spectra_app.workbook_state import create_comparison
        target = Path(self.tmp.name) / 'xy.spextra'
        write_project(target, self.workspace)
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(json.loads(archive.read('manifest.json'))['version'], 8)
        comparison = create_comparison(self.workspace, 'XY progress')
        comparison.analysis_state = {'version': 1, 'current_gate': 2, 'curves': [], 'note': None}
        write_project(target, self.workspace)
        restored = read_project(target).comparisons[comparison.comparison_id]
        self.assertEqual(restored.analysis_state, comparison.analysis_state)
        with zipfile.ZipFile(target) as archive:
            # Newly created comparisons retain their presentation in schema 12.
            self.assertEqual(json.loads(archive.read('manifest.json'))['version'], 12)
        comparison.analysis_state['invalid'] = float('nan')
        with self.assertRaises(ProjectFormatError):
            write_project(target, self.workspace)

    def test_render_candidate_borrows_analysis_science_but_detaches_visuals(self):
        from spectra_app.analysis_document import ensure_analysis_graph
        from spectra_app.dataset_state import detached_graph_session
        graph = ensure_analysis_graph(self.session)
        graph.analysis.set_candidates(0, self._pool())
        candidate = detached_graph_session(self.session)
        self.assertIs(candidate.active_graph.analysis, graph.analysis)
        self.assertIs(candidate.active_graph.analysis.science.current_cube, graph.analysis.science.current_cube)
        candidate.view.annotations['plot_title'] = 'detached edit'
        self.assertNotEqual(graph.view.annotations['plot_title'], 'detached edit')

    def test_large_complete_pool_uses_numeric_tables_and_roundtrips_without_truncation(self):
        from spectra_app.analysis_document import ensure_analysis_graph
        from spectra_app.peak_candidates import PeakCandidate
        energy = np.linspace(1., 3., 1000)
        cube = replace(self.session.base_cube, energy=energy, x_original=energy.copy(),
            photon_energy=energy.copy(), gate=np.arange(100, dtype=float), z=np.ones((100, 1000)))
        self.session.base_cube = self.session.current_cube = cube
        doc = ensure_analysis_graph(self.session).analysis
        pool = [PeakCandidate(i, float(x), 1., 'peak' if i % 2 else 'dip', .2, .2, .01, 6., 'Original')
                for i, x in enumerate(energy)]
        for gate in range(100):
            doc.set_candidates(gate, pool)
        original_ids = [seed.seed_id for seed in doc.seeds_by_gate[99]]
        target = Path(self.tmp.name) / 'complete-pool.spextra'
        digest = write_project(target, self.workspace)
        with zipfile.ZipFile(target) as archive:
            # The previous repeated-key layout required >40 MiB for this pool.
            self.assertLess(archive.getinfo('manifest.json').file_size, 8 * 1024**2)
            manifest = json.loads(archive.read('manifest.json'))
            document = manifest['datasets'][0]['graphs'][1]['analysis']
            self.assertIsInstance(document['candidate_pools'][0]['candidates'], str)
            self.assertIsInstance(document['seeds_by_gate'][0]['seeds'], str)
        restored_workspace = read_project(target)
        restored = restored_workspace.active.active_graph.analysis
        self.assertEqual(sum(map(len, restored.candidate_pools.values())), 100000)
        self.assertEqual(sum(map(len, restored.seeds_by_gate.values())), 100000)
        self.assertEqual(restored.candidate_pools[99], doc.candidate_pools[99])
        self.assertEqual([seed.seed_id for seed in restored.seeds_by_gate[99]], original_ids)
        self.assertEqual(workspace_digest(restored_workspace), digest)
