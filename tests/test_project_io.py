import copy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch
import warnings
import zipfile

import numpy as np

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import (AnalysisControls, DatasetSession, FixedRecipe, GraphState,
                                      ProcessingState, Workspace)
from spectra_app.fitting import FitResult
from spectra_app.plot_presentation import (AxisPresentation, ColorbarPresentation,
                                          ColorbarTicks, NormSpec, PlotAnnotation,
                                          PlotPresentation)
from spectra_app.project_io import (ProjectFormatError, read_project,
                                    workspace_digest, write_project)


def rich_workspace(directory):
    """Two independent owners, with every current durable state family exercised."""
    workspace = Workspace()
    for index in range(2):
        source = Path(directory) / f'source-{index}.dat'
        source.write_text('# Signal: PL\nEnergy(eV) -1 0 1\n' + '\n'.join(
            f'{1 + i / 10} {i+1} {i+2} {i+3}' for i in range(8)), encoding='utf-8')
        cube = load_dat_cube(source)
        session = DatasetSession(dataset_id=f'owner-{index}', source_path=source,
                                 loaded_fingerprint=f'fingerprint-{index}',
                                 graphs={'default': GraphState()},
                                 base_cube=cube, current_cube=cube)
        workspace.register(session)
    first, second = workspace.datasets.values()
    z = first.base_cube.z.copy()
    z[1, 3] = np.nan
    first.current_cube = replace(first.base_cube, z=z)
    first.applied = ProcessingState('energy_second_derivative', False, 21, None)
    first.pending = ProcessingState('energy_second_derivative', True, 8, 'literal original')
    first.fixed_recipes = {
        'processing': FixedRecipe(True, {'mode': 'energy_second_derivative',
                                        'smooth': True, 'window_length': 8}),
        'regions': FixedRecipe(True, {'mode': '3', 'boundaries': [-.4, .4]}),
        'region_edges': FixedRecipe(False, {'mode': 'global', 'boundaries': [.1]}),
    }
    first.view.gate = 0.
    first.view.colormap = 'RdBu_r'
    first.view.norm = NormSpec('symlog', .02)
    first.view.annotations = {'plot_title': 'literal α', 'y_axis_name': 'Gate',
                              'y_axis_unit': 'V', 'signal_title': 'my ΔR/R'}
    first.view.fix_annotations = False
    first.view.signal_title_auto = False
    first.view.title_settings = {'mode': 'template', 'preset': {
        'name': 'local', 'pattern': '{sample} {valley}', 'angle_map': {'85': 'K'}},
        'manual_override': 'manual annotation'}
    first.view.bounds = dict(xmin=1.1, xmax=1.6, ymin=-1., ymax=1., vmin=-2., vmax=4.)
    first.view.locks = dict(xmin=True, xmax=False, ymin=False, ymax=True, vmin=True, vmax=False)
    first.view.x_limits = (1.6, 1.1)
    first.view.y_limits = (-1., 1.)
    first.view.color_limits = (-2., 4.)
    first.view.color_scale_mode = '3'
    first.view.color_region_boundaries = [-.4, .4]
    first.view.last_valid_color_limits = (-1., 3.)
    first.view.last_valid_region_color_limits = [(-1., 1.), (-2., 2.), (-3., 3.)]
    first.presentation = PlotPresentation(
        width_in=4.2, frame_width_in=None, frame_height_in=None,
        font_family='Arial', include_peak_labels=True,
        axis=AxisPresentation(exchange_xy=True, flip_y=True, x_major_count=7),
        colorbar=ColorbarPresentation(label_format='engineering', ticks={
            'region-1': ColorbarTicks((-2., 0., 4.), ('low', 'zero', 'high'))}),
        annotations=(PlotAnnotation('note-a', text='portable note', position=(.2, .7)),
                     PlotAnnotation('arrow-a', kind='arrow', text='target',
                                    position=(.4, .5), target=(1.4, 0.))))
    first.analysis_controls = AnalysisControls(
        derivative_detection=True, peak_mode='Both', peak_smooth=True,
        peak_smooth_window=4, d2_mode='Dips', d2_window=4, d2_polyorder=6,
        fit_xmin=1.8, fit_xmax=1.0, fit_xmin_fixed=True, fit_smooth=True,
        fit_smooth_window=4, fit_model='Gaussian', linewidth_min=200,
        linewidth_max=100, csv_source=True)
    first.peak_indices = np.array([1, 5], dtype=np.int32)
    first.peak_gate_idx = 1
    first.peak_kind_lookup = {1: True, 5: False}
    first.peak_label_lookup = {(1, 1): 14, (2, 4): 7}
    first.batch_peak_map = {0: np.array([2], dtype=np.int64), 2: np.array([4], dtype=np.int16)}
    first.batch_peak_kind_lookup = {(0, 2): False, (2, 4): True}
    seed = Path(directory) / 'seed.csv'
    seed.write_text('seed source', encoding='utf-8')
    first.csv_seeds_path = str(seed)
    first.csv_seeds_map = {0: [1.225, 1.456], 2: []}
    x = np.linspace(1.13, 1.66, 5)
    result = FitResult('Gaussian', x, np.arange(5.), np.ones((2, 5)),
                       np.array([1.225, 1.456]), np.array([-2., 3.]),
                       np.array([.02, .08]), -.7, .2, -2.5)
    first.fit_result = result
    first.fit_gate_idx = 1
    first.batch_fit_map = {1: result.centers.copy(), 2: np.array([1.32])}
    first.batch_fit_result_map = {1: result, 2: replace(result, r_squared=float('nan'))}
    first.fit_export_rows = [{'gate': '0', 'status': 'ok', 'centers': '1.225;1.456'},
                             {'gate': '1', 'status': 'failed', 'rmse': 'nan'}]
    first.has_batch_fit_results = True
    first.summary = 'Persisted results\nNo computation during restore.'
    first.initialization_notice = 'Saved interpretation notice.'
    second.view.x_limits = (1., 1.)  # One-coordinate limits are a known model state.
    second.csv_seeds_map = {}
    workspace.active_dataset_id = second.dataset_id
    return workspace


def archive_members(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def overwrite_archive(path, members):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)


def npy_bytes(array):
    stream = io.BytesIO()
    np.save(stream, array, allow_pickle=True)  # Corruption fixtures deliberately include objects.
    return stream.getvalue()


class ProjectIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.workspace = rich_workspace(self.directory)
        self.path = self.directory / 'portable.spextra'

    def save(self):
        return write_project(self.path, self.workspace)

    def corrupt_manifest(self, mutate):
        self.save()
        members = archive_members(self.path)
        manifest = json.loads(members['manifest.json'])
        mutate(manifest)
        members['manifest.json'] = json.dumps(manifest).encode('utf-8')
        overwrite_archive(self.path, members)

    def test_legacy_graph_workspace_upgrades_to_v8_and_layout_digests(self):
        from spectra_app.project_io import runtime_workspace_stamp
        self.assertTrue(hasattr(self.workspace, 'graph_workspace'), 'Workspace must retain graph layout')
        layout = {'mode': 'free', 'documents': [
            {'dataset_id': 'owner-0', 'graph_id': 'default', 'open': True, 'rect': [15, 24, 420, 320]},
            {'dataset_id': 'owner-1', 'graph_id': 'default', 'open': False, 'rect': [80, 65, 390, 280]}],
            'active': ['owner-0', 'default'], 'stacking': [['owner-0', 'default']]}
        self.workspace.graph_workspace = layout
        exact, runtime = self.save(), runtime_workspace_stamp(self.workspace, {})
        restored = read_project(self.path)
        expected = dict(layout, documents=[
            {'key': [record['dataset_id'], record['graph_id']], 'open': record['open'], 'rect': record['rect']}
            for record in layout['documents']])
        self.assertEqual(restored.graph_workspace, expected)
        self.assertIsNot(restored.graph_workspace, layout)
        self.assertEqual(workspace_digest(restored), exact)
        self.assertEqual(runtime_workspace_stamp(restored, {}), runtime)
        self.assertEqual(json.loads(archive_members(self.path)['manifest.json'])['version'], 8)
        layout['documents'][0]['rect'][0] = 44
        self.assertNotEqual(workspace_digest(self.workspace), exact)
        self.assertNotEqual(runtime_workspace_stamp(self.workspace, {}), runtime)
        self.assertEqual(restored.graph_workspace['documents'][0]['rect'][0], 15)

    def test_malformed_graph_workspace_is_rejected_on_save_and_load(self):
        self.assertTrue(hasattr(self.workspace, 'graph_workspace'), 'Layout metadata needs strict validation')
        valid = {'mode': 'free', 'documents': [
            {'dataset_id': 'owner-0', 'graph_id': 'default', 'open': True, 'rect': [15, 24, 420, 320]}],
            'active': ['owner-0', 'default'], 'stacking': [['owner-0', 'default']]}
        for mutate in (
                lambda x: x.update(mode='floating'),
                lambda x: x['documents'].append(copy.deepcopy(x['documents'][0])),
                lambda x: x['documents'][0].update(dataset_id='missing'),
                lambda x: x['documents'][0].update(graph_id='missing'),
                lambda x: x['documents'][0].update(open=1),
                lambda x: x['documents'][0].update(rect=[0, 0, -1, 200]),
                lambda x: x['documents'][0].update(rect=[0, 0, 1000000, 200]),
                lambda x: x['documents'][0].update(rect=[True, 0, 300, 200]),
                lambda x: x.update(active=['owner-1', 'default']),
                lambda x: x.update(stacking=[])):
            bad = copy.deepcopy(valid)
            mutate(bad)
            with self.subTest(layout=bad):
                self.workspace.graph_workspace = None
                self.corrupt_manifest(lambda m: m.update(graph_workspace=bad))
                with self.assertRaises(ProjectFormatError):
                    read_project(self.path)
                self.workspace.graph_workspace = bad
                with self.assertRaises(ProjectFormatError):
                    self.save()

    def test_legacy_v2_v3_defaults_and_explicit_empty_layout(self):
        self.save()
        members = archive_members(self.path)
        manifest = json.loads(members['manifest.json'])
        for field in ('workbooks', 'sheets', 'comparisons'):
            manifest.pop(field)
        for session in manifest['datasets']:
            for graph in session['graphs']:
                graph.pop('published')
                graph.pop('source_mode')
                graph.pop('gate_indices')
        for version in (2, 3):
            manifest['version'] = version
            if version == 2:
                manifest.pop('graph_workspace', None)
            else:
                manifest['graph_workspace'] = {'mode': 'free', 'documents': [
                    {'dataset_id': 'owner-0', 'graph_id': 'default', 'open': True, 'rect': None}],
                    'active': ['owner-0', 'default'], 'stacking': [['owner-0', 'default']]}
            members['manifest.json'] = json.dumps(manifest).encode()
            overwrite_archive(self.path, members)
            restored = read_project(self.path)
            self.assertEqual(len([s for s in restored.sheets.values() if s.role == 'original']), 2)
            self.assertEqual({(s.dataset_id, s.role, s.fit_gate_idx) for s in restored.sheets.values()},
                             {('owner-0', 'original', None), ('owner-1', 'original', None),
                              ('owner-0', 'processed', None), ('owner-0', 'fit_parameters', None),
                              ('owner-0', 'fit_curves', 1), ('owner-0', 'fit_curves', 2)})
            if version == 2:
                self.assertIsNone(restored.graph_workspace)
            else:
                self.assertEqual(restored.graph_workspace['documents'][0],
                                 {'key': ['owner-0', 'default'], 'open': True, 'rect': None})
        restored.graph_workspace = {'mode': 'free', 'documents': [], 'active': None, 'stacking': []}
        write_project(self.path, restored)
        self.assertEqual(read_project(self.path).graph_workspace, restored.graph_workspace)

    def test_rich_disk_round_trip_needs_no_sources_and_preserves_alias(self):
        saved = self.save()
        for name in ('source-0.dat', 'source-1.dat', 'seed.csv'):
            (self.directory / name).unlink()
        with patch('spectra_app.dat_io.load_dat_cube', side_effect=AssertionError('source reload')):
            restored = read_project(self.path)
        self.assertEqual(list(restored.datasets), ['owner-0', 'owner-1'])
        self.assertEqual(restored.active_dataset_id, 'owner-1')
        self.assertEqual(saved, workspace_digest(self.workspace))
        self.assertEqual(saved, workspace_digest(restored))
        first, second = restored.datasets.values()
        self.assertIs(second.base_cube, second.current_cube)
        self.assertIsNot(first.base_cube, first.current_cube)
        self.assertFalse(first.sidecar_writes_enabled)
        self.assertFalse(second.sidecar_writes_enabled)
        self.assertEqual(restored.path_index, self.workspace.path_index)
        self.assertIs(restored.dataset_for_path(first.source_path), first)
        original = self.workspace.datasets['owner-0']
        for name in ('energy', 'gate', 'z', 'x_original', 'photon_energy'):
            np.testing.assert_array_equal(getattr(first.current_cube, name),
                                          getattr(original.current_cube, name))
        self.assertEqual(first.current_cube.z.dtype, original.current_cube.z.dtype)
        self.assertEqual(first.pending, original.pending)
        self.assertEqual(first.applied, original.applied)
        self.assertIsNone(first.applied.original_signal_title)
        self.assertEqual(first.view, original.view)
        self.assertEqual(first.presentation, original.presentation)
        self.assertEqual(first.fixed_recipes, original.fixed_recipes)
        self.assertEqual(first.analysis_controls, original.analysis_controls)
        self.assertEqual(first.peak_label_lookup, original.peak_label_lookup)
        self.assertEqual(first.peak_kind_lookup, original.peak_kind_lookup)
        self.assertEqual(first.batch_peak_kind_lookup, original.batch_peak_kind_lookup)
        self.assertEqual(first.csv_seeds_map, original.csv_seeds_map)
        self.assertEqual(first.csv_seeds_path, original.csv_seeds_path)
        self.assertEqual(first.fit_export_rows, original.fit_export_rows)
        self.assertEqual(first.summary, original.summary)
        self.assertEqual(first.initialization_notice, original.initialization_notice)
        self.assertEqual(first.fit_result.r_squared, -2.5)
        self.assertTrue(np.isnan(first.batch_fit_result_map[2].r_squared))
        for name in ('x', 'y_fit', 'components', 'centers', 'amplitudes', 'widths'):
            np.testing.assert_array_equal(getattr(first.fit_result, name), getattr(original.fit_result, name))
        for key, array in original.batch_peak_map.items():
            np.testing.assert_array_equal(first.batch_peak_map[key], array)
            self.assertEqual(first.batch_peak_map[key].dtype, array.dtype)
        for key, array in original.batch_fit_map.items():
            np.testing.assert_array_equal(first.batch_fit_map[key], array)

    def test_empty_workspace_and_default_runtime_policy(self):
        self.assertTrue(DatasetSession().sidecar_writes_enabled)
        empty = Workspace()
        digest = write_project(self.path, empty)
        restored = read_project(self.path)
        self.assertEqual(restored, empty)
        self.assertEqual(workspace_digest(restored), digest)

    def test_missing_and_nonfinite_z_and_duplicate_coordinates_are_preserved(self):
        owner = self.workspace.datasets['owner-0']
        energy = owner.base_cube.energy.copy()
        energy[1] = energy[0]
        z = owner.base_cube.z.copy()
        z[0, 0], z[0, 1] = np.inf, -np.inf
        owner.base_cube = replace(owner.base_cube, energy=energy, z=z)
        owner.current_cube = replace(owner.base_cube, z=np.full(z.shape, np.nan))
        saved = self.save()
        restored = read_project(self.path).datasets['owner-0']
        np.testing.assert_array_equal(restored.base_cube.z, z)
        self.assertTrue(np.isnan(restored.current_cube.z).all())
        np.testing.assert_array_equal(restored.current_cube.energy, energy)
        self.assertEqual(saved, workspace_digest(read_project(self.path)))

    def test_restore_never_probes_provenance_files(self):
        self.save()
        with patch.object(Path, 'resolve', side_effect=AssertionError('source resolve')):
            with patch.object(Path, 'exists', side_effect=AssertionError('source exists')):
                with patch.object(Path, 'stat', side_effect=AssertionError('source stat')):
                    restored = read_project(self.path)
        self.assertEqual(restored.path_index, self.workspace.path_index)

    def test_registered_relative_provenance_is_supported_without_source_reads(self):
        owner = self.workspace.datasets['owner-1']
        owner.source_path = Path(os.path.relpath(owner.source_path))
        owner.base_cube = replace(owner.base_cube, source_path=str(owner.source_path))
        owner.current_cube = owner.base_cube
        saved = self.save()
        restored = read_project(self.path)
        self.assertEqual(restored.datasets['owner-1'].source_path, owner.source_path)
        self.assertEqual(saved, workspace_digest(restored))

    def test_integer_literals_in_float_fields_keep_digest_after_restore(self):
        owner = self.workspace.datasets['owner-0']
        owner.view.bounds['xmin'] = 1
        owner.view.x_limits = (1, 2)
        owner.analysis_controls.fit_xmin = 2
        owner.fixed_recipes['region_edges'].recipe['boundaries'] = [0, 1]
        owner.fit_result = replace(owner.fit_result, rmse=0, r_squared=-2)
        self.assertEqual(self.save(), workspace_digest(read_project(self.path)))

    def test_digest_tracks_durable_edits_and_ignores_runtime_or_derived_fields(self):
        baseline = workspace_digest(self.workspace)
        changed = copy.deepcopy(self.workspace)
        changed.path_index = {}
        for session in changed.datasets.values():
            session.sidecar_writes_enabled = False
        self.assertEqual(workspace_digest(changed), baseline)
        mutations = (
            lambda w: setattr(w, 'active_dataset_id', 'owner-0'),
            lambda w: setattr(w, 'datasets', dict(reversed(list(w.datasets.items())))),
            lambda w: setattr(w.datasets['owner-0'].pending, 'window_length', 10),
            lambda w: w.datasets['owner-0'].current_cube.z.__setitem__((0, 0), 400.),
            lambda w: w.datasets['owner-0'].peak_label_lookup.__setitem__((1, 1), 9),
            lambda w: w.datasets['owner-0'].fit_export_rows[0].__setitem__('status', 'edited'),
            lambda w: w.datasets['owner-0'].csv_seeds_map[0].append(1.6),
            lambda w: setattr(w.datasets['owner-0'], 'presentation',
                              replace(w.datasets['owner-0'].presentation, numeric_pt=14.)),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(self.workspace)
                mutate(changed)
                self.assertNotEqual(workspace_digest(changed), baseline)
        self.save()
        with zipfile.ZipFile(self.path) as archive:
            manifest = archive.read('manifest.json')
        self.assertNotIn(b'sidecar_writes_enabled', manifest)
        self.assertNotIn(b'path_index', manifest)

    def test_failed_atomic_replace_preserves_previous_file_and_cleans_sibling(self):
        self.save()
        before = self.path.read_bytes()
        self.workspace.datasets['owner-0'].summary = 'unsaved edit'
        with patch('spectra_app.project_io.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaisesRegex(OSError, 'replace failed'):
                self.save()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()),
                         ['portable.spextra', 'seed.csv', 'source-0.dat', 'source-1.dat'])

    def test_invalid_manifest_fields_indices_enums_and_identity_are_rejected(self):
        mutations = {
            'version': lambda m: m.__setitem__('version', 99),
            'schema': lambda m: m.__setitem__('unexpected', True),
            'active': lambda m: m.__setitem__('active_dataset_id', 'missing'),
            'owner': lambda m: m['datasets'][1].__setitem__('dataset_id', 'owner-0'),
            'source': lambda m: m['datasets'][1].__setitem__('source_path', m['datasets'][0]['source_path']),
            'kind': lambda m: m['datasets'][0].__setitem__('kind', 'worksheet'),
            'gate': lambda m: m['datasets'][0].__setitem__('peak_gate_idx', 99),
            'tuple-index': lambda m: m['datasets'][0]['peak_label_lookup'][0].__setitem__('energy', 99),
            'tuple-duplicate': lambda m: m['datasets'][0]['peak_label_lookup'].append(m['datasets'][0]['peak_label_lookup'][0]),
            'gate-duplicate': lambda m: m['datasets'][0]['batch_fit_map'].append(m['datasets'][0]['batch_fit_map'][0]),
            'enum': lambda m: m['datasets'][0]['analysis_controls'].__setitem__('fit_model', 'unknown'),
            'fit-shape': lambda m: m['datasets'][0]['fit_result'].__setitem__('components', m['datasets'][0]['fit_result']['x']),
            'array-reference': lambda m: m['datasets'][1]['base_cube'].__setitem__('z', m['datasets'][0]['base_cube']['z']),
            'base-current': lambda m: m['datasets'][0]['current_cube'].__setitem__('gate_label', 'other'),
            'presentation-missing': lambda m: m['datasets'][0]['graphs'][0]['presentation']['axis'].pop('border'),
            'presentation-duplicate': lambda m: m['datasets'][0]['graphs'][0]['presentation']['annotations'].append(
                m['datasets'][0]['graphs'][0]['presentation']['annotations'][0]),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                self.corrupt_manifest(mutate)
                with self.assertRaises(ProjectFormatError):
                    read_project(self.path)

    def test_missing_unexpected_duplicate_and_traversal_members_are_rejected(self):
        for label in ('missing', 'unexpected', 'traversal', 'duplicate'):
            with self.subTest(label=label):
                self.save()
                members = archive_members(self.path)
                if label == 'missing':
                    del members[next(name for name in members if name.endswith('.npy'))]
                elif label == 'unexpected':
                    members['extra.txt'] = b'bad'
                elif label == 'traversal':
                    members['../escape.npy'] = npy_bytes(np.arange(3.))
                overwrite_archive(self.path, members)
                if label == 'duplicate':
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', UserWarning)
                        with zipfile.ZipFile(self.path, 'a') as archive:
                            archive.writestr('manifest.json', members['manifest.json'])
                with self.assertRaises(ProjectFormatError):
                    read_project(self.path)

    def test_object_bad_shape_nonfinite_coordinate_and_huge_header_rejected_before_load(self):
        for label in ('object', 'shape', 'coordinate', 'huge-header', 'index', 'trailing-data'):
            with self.subTest(label=label):
                self.save()
                members = archive_members(self.path)
                record = json.loads(members['manifest.json'])['datasets'][0]
                name = record['base_cube']['energy']
                if label == 'object':
                    bad = npy_bytes(np.array(['object'], dtype=object))
                elif label == 'shape':
                    bad = npy_bytes(np.ones((2, 4)))
                elif label == 'coordinate':
                    bad = npy_bytes(np.array([1., np.nan, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7]))
                elif label == 'index':
                    name = record['peak_indices']
                    bad = npy_bytes(np.array([99], dtype=np.int64))
                elif label == 'trailing-data':
                    bad = members[name] + b'unexpected trailing bytes'
                else:
                    stream = io.BytesIO()
                    np.lib.format.write_array_header_1_0(stream, {
                        'descr': '<f8', 'fortran_order': False, 'shape': (2**60,)})
                    bad = stream.getvalue()
                members[name] = bad
                overwrite_archive(self.path, members)
                if label in ('object', 'huge-header', 'trailing-data'):
                    with patch('spectra_app.project_io.np.load', side_effect=AssertionError('unsafe allocation')):
                        with self.assertRaises(ProjectFormatError):
                            read_project(self.path)
                else:
                    with self.assertRaises(ProjectFormatError):
                        read_project(self.path)

    def test_duplicate_json_keys_and_nonstandard_json_numbers_rejected(self):
        self.save()
        members = archive_members(self.path)
        for manifest in (b'{"version":1,"version":1}', b'{"version":NaN}'):
            with self.subTest(manifest=manifest):
                members['manifest.json'] = manifest
                overwrite_archive(self.path, members)
                with self.assertRaises(ProjectFormatError):
                    read_project(self.path)

    def test_archive_resource_limits_and_unsupported_compression(self):
        self.save()
        for name, limit in (('MAX_MEMBERS', 1), ('MAX_MANIFEST_BYTES', 10),
                            ('MAX_MEMBER_BYTES', 10), ('MAX_TOTAL_BYTES', 10)):
            with self.subTest(limit=name):
                with patch(f'spectra_app.project_io.{name}', limit):
                    with patch('spectra_app.project_io.np.load', side_effect=AssertionError('allocation')):
                        with self.assertRaises(ProjectFormatError):
                            read_project(self.path)
        members = archive_members(self.path)
        with zipfile.ZipFile(self.path, 'w', compression=zipfile.ZIP_BZIP2) as archive:
            for name, value in members.items():
                archive.writestr(name, value)
        with self.assertRaises(ProjectFormatError):
            read_project(self.path)

    def test_writer_counts_npy_header_in_member_limit(self):
        self.save()
        previous = self.path.read_bytes()
        owner = copy.deepcopy(self.workspace.datasets['owner-1'])
        axis = np.arange(1024, dtype=np.float64)
        owner.base_cube = replace(owner.base_cube, energy=axis, gate=np.array([0.]),
                                  z=np.ones((1, 1024)), x_original=axis,
                                  photon_energy=axis)
        owner.current_cube = owner.base_cube
        workspace = Workspace()
        workspace.register(owner)
        workspace.active_dataset_id = owner.dataset_id
        with patch('spectra_app.project_io.MAX_MEMBER_BYTES', axis.nbytes):
            with self.assertRaisesRegex(ProjectFormatError, 'including its header'):
                write_project(self.path, workspace)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_malformed_deflate_is_reported_as_project_format_error(self):
        self.save()
        with zipfile.ZipFile(self.path) as archive:
            info = archive.getinfo('manifest.json')
        payload = bytearray(self.path.read_bytes())
        name_length, extra_length = struct.unpack_from('<HH', payload, info.header_offset + 26)
        offset = info.header_offset + 30 + name_length + extra_length
        payload[offset] = 0x06  # Reserved DEFLATE block type.
        self.path.write_bytes(payload)
        with self.assertRaises(ProjectFormatError):
            read_project(self.path)

    def test_declared_oversized_npy_header_rejected_before_header_read(self):
        self.save()
        members = archive_members(self.path)
        record = json.loads(members['manifest.json'])['datasets'][0]
        members[record['base_cube']['energy']] = b'\x93NUMPY\x02\x00' + struct.pack('<I', 2**30)
        overwrite_archive(self.path, members)
        with patch('spectra_app.project_io.np.lib.format.read_array_header_2_0',
                   side_effect=AssertionError('unbounded header read')):
            with patch('spectra_app.project_io.np.load', side_effect=AssertionError('allocation')):
                with self.assertRaises(ProjectFormatError):
                    read_project(self.path)

    def test_structured_dtype_and_invalid_header_rejected_before_numpy_load(self):
        self.save()
        original_members = archive_members(self.path)
        name = json.loads(original_members['manifest.json'])['datasets'][0]['base_cube']['energy']
        for content in (npy_bytes(np.array([(1.,)], dtype=[('x', 'f8')])),
                        b'\x93NUMPY\x07\x00bad', b'not-an-npy'):
            with self.subTest(content=content[:16]):
                members = dict(original_members)
                members[name] = content
                overwrite_archive(self.path, members)
                with patch('spectra_app.project_io.np.load', side_effect=AssertionError('allocation')):
                    with self.assertRaises(ProjectFormatError):
                        read_project(self.path)

    def test_invalid_in_memory_state_does_not_replace_file(self):
        self.save()
        before = self.path.read_bytes()
        self.workspace.datasets['owner-0'].peak_indices = np.array([999], dtype=int)
        with self.assertRaises(ProjectFormatError):
            self.save()
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
