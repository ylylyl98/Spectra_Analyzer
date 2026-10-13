import tempfile
import unittest
from pathlib import Path

import numpy as np

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import (AnalysisControls, DatasetDefaults, DatasetSession,
                                      FixedRecipe, ViewState, Workspace, initialize_dataset)
from spectra_app.plot_state import source_fingerprint


class DatasetStateTests(unittest.TestCase):
    def test_initial_processing_keeps_declared_source_kind_and_derivative_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'declared.dat'
            path.write_text('# Signal: d²PL/dE²\nEnergy(eV) 0 1\n' + '\n'.join(
                f'{1+i/10} {i*i} {2*i*i}' for i in range(13)), encoding='utf-8')
            cube = load_dat_cube(path)
            defaults = DatasetDefaults(ViewState(), AnalysisControls(),
                                       {group: FixedRecipe() for group in ('processing', 'regions', 'region_edges')}, {})
            defaults.fixed_recipes['processing'] = FixedRecipe(True, {'mode': 'energy_second_derivative'})
            session = initialize_dataset(path, (cube, source_fingerprint(path), None, ''), defaults)
            self.assertEqual(session.applied.mode, 'energy_second_derivative')
            self.assertEqual(session.base_cube.signal_source_kind, 'pl')
            self.assertEqual(session.current_cube.signal_source_kind, 'pl')
            self.assertEqual(session.view.annotations['signal_title'], 'd⁴PL/dE⁴ (arb. units)')
            defaults.fixed_recipes['processing'].enabled = False
            original = initialize_dataset(path, (cube, source_fingerprint(path), None, ''), defaults)
            self.assertEqual(original.view.annotations['signal_title'], 'd²PL/dE² (arb. units)')

    def test_fresh_pl_chooses_jet_and_manual_default_stays_authoritative(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scan_PL730nm.dat'
            path.write_text('Energy(eV) 0 1\n1 1 2\n2 3 4\n3 5 6\n', encoding='utf-8')
            defaults = DatasetDefaults(ViewState(colormap='RdBu_r'), AnalysisControls(),
                                       {group: FixedRecipe() for group in ('processing', 'regions', 'region_edges')}, {})
            prepared = (load_dat_cube(path), source_fingerprint(path), None, '')
            session = initialize_dataset(path, prepared, defaults)
            self.assertEqual(session.view.colormap, 'jet')
            self.assertEqual(session.view.annotations['signal_title'], 'PL (arb. units)')
            self.assertEqual(session.presentation.annotations, ())
            defaults.colormap_manual = True
            for choice in ('magma', 'RdBu_r'):
                defaults.view.colormap = choice
                self.assertEqual(initialize_dataset(path, prepared, defaults).view.colormap, choice)
    def test_path_dedup_preserves_owner_and_explicit_replacement_preserves_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sub' / 'sample.dat'
            workspace = Workspace()
            original = workspace.register(DatasetSession(source_path=path))
            original.csv_seeds_map = {0: [1.5]}
            duplicate = workspace.register(DatasetSession(source_path=path.parent / '..' / 'sub' / path.name))
            self.assertIs(duplicate, original)
            other = workspace.register(DatasetSession(source_path=Path(directory) / 'elsewhere' / path.name))
            self.assertNotEqual(other.dataset_id, original.dataset_id)
            replacement = workspace.register(DatasetSession(source_path=path), replace_existing=True)
            self.assertEqual(replacement.dataset_id, original.dataset_id)
            self.assertIsNone(replacement.csv_seeds_map)
            self.assertEqual(list(workspace.datasets), [original.dataset_id, other.dataset_id])

    def test_candidates_share_defaults_but_never_mutable_scientific_containers(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / (name + '.dat') for name in ('a', 'b')]
            for path in paths:
                path.write_text('Energy(eV) 0 1\n1 1 2\n2 3 4\n3 5 6\n', encoding='utf-8')
            defaults = DatasetDefaults(ViewState(), AnalysisControls(),
                                       {group: FixedRecipe() for group in ('processing', 'regions', 'region_edges')},
                                       {'plot_title': 'request title'})
            sessions = [initialize_dataset(path, (load_dat_cube(path), source_fingerprint(path), None, ''), defaults)
                        for path in paths]
            a, b = sessions
            a.view.bounds['xmin'] = 1.5
            a.view.annotations['plot_title'] = 'edited A'
            a.fixed_recipes['regions'].recipe['mode'] = '3'
            a.batch_peak_map[0] = np.array([1])
            a.csv_seeds_map = {0: [2.0]}
            a.fit_export_rows.append({'gate': '0'})
            self.assertEqual(b.view.bounds['xmin'], 1)
            self.assertEqual(b.view.annotations['plot_title'], 'request title')
            self.assertEqual(defaults.annotation_defaults['plot_title'], 'request title')
            self.assertEqual(b.fixed_recipes['regions'].recipe, {})
            self.assertEqual(b.batch_peak_map, {})
            self.assertIsNone(b.csv_seeds_map)
            self.assertEqual(b.fit_export_rows, [])
            self.assertEqual(a.analysis_controls.fit_xmin, 1)
            self.assertEqual(a.analysis_controls.fit_xmax, 3)
            self.assertIs(a.base_cube, a.current_cube)
