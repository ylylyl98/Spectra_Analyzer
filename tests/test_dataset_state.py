import tempfile
import unittest
from pathlib import Path

import numpy as np

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import (AnalysisControls, DatasetDefaults, DatasetSession,
                                      FixedRecipe, ViewState, Workspace, initialize_dataset)
from spectra_app.plot_state import PlotStateStore, source_fingerprint
from spectra_app.project_io import read_project, write_project


class DatasetStateTests(unittest.TestCase):
    def test_gate_condition_defaults_initialize_y_axis_name_and_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            defaults = DatasetDefaults(ViewState(), AnalysisControls(),
                {group: FixedRecipe() for group in ('processing', 'regions', 'region_edges')}, {})
            for condition, expected_name in (
                ('BG=-1.5V', 'Tg'), ('TG=2', 'Bg'),
                ('BG-TG=1', 'Tg+Bg'), ('TG+BG=1', 'Tg\u2212Bg'),
                ('0.95TG-BG=18', '0.95Tg+Bg'), ('TG-2BG=0', 'Tg+2Bg'),
                ('0.95TG+BG=18', '0.95Tg\u2212Bg'),
            ):
                with self.subTest(condition=condition):
                    path = Path(directory) / f'scan_{condition}.dat'
                    path.write_text('Energy(eV) -1 2\n1.6 1 2\n1.7 3 4\n', encoding='utf-8')
                    session = initialize_dataset(path, (load_dat_cube(path), '', None, ''), defaults)
                    self.assertEqual(session.view.annotations['y_axis_name'], expected_name)
                    self.assertEqual(session.view.annotations['y_axis_unit'], 'V')

    def test_filename_gate_label_initializes_name_and_unit_below_fixed_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scan_0.95TG-BG=18.dat'
            path.write_text('Energy(eV) -1 2\n1.6 1 2\n1.7 3 4\n', encoding='utf-8')
            prepared = (load_dat_cube(path), source_fingerprint(path), None, '')
            for fixed, annotations, expected in (
                (True, {}, ('0.95Tg+Bg', 'V')),
                (True, {'y_axis_name': 'Manual gate', 'y_axis_unit': 'mV'}, ('Manual gate', 'mV')),
                (True, {'y_axis_name': '', 'y_axis_unit': ''}, ('', '')),
                (False, {'y_axis_name': 'Manual gate', 'y_axis_unit': 'mV'}, ('0.95Tg+Bg', 'V')),
            ):
                with self.subTest(fixed=fixed, annotations=annotations):
                    defaults = DatasetDefaults(ViewState(fix_annotations=fixed), AnalysisControls(),
                        {group: FixedRecipe() for group in ('processing', 'regions', 'region_edges')}, annotations)
                    session = initialize_dataset(path, prepared, defaults)
                    self.assertEqual((session.view.annotations['y_axis_name'],
                                      session.view.annotations['y_axis_unit']), expected)

    def test_filename_gate_label_preserves_saved_source_and_project_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scan_0.95TG\u2212BG=18_d2E_W21_O2_Regrid.dat'
            path.write_text('Energy(eV) -1 2\n1.6 1 2\n1.7 3 4\n', encoding='utf-8')
            cube, fingerprint = load_dat_cube(path), source_fingerprint(path)
            defaults = DatasetDefaults(ViewState(), AnalysisControls(),
                {group: FixedRecipe() for group in ('processing', 'regions', 'region_edges')},
                {'y_axis_name': 'Global manual', 'y_axis_unit': 'mV'})
            store = PlotStateStore()
            for name, unit in (('Source manual', 'mV'), ('', '')):
                with self.subTest(name=name, unit=unit):
                    session = initialize_dataset(path, (cube, fingerprint, None, ''), defaults)
                    session.view.annotations.update(y_axis_name=name, y_axis_unit=unit)
                    store.save(path, fingerprint, session.view.plot_recipe(cube, session.applied))
                    saved, notice = store.load(path, fingerprint)
                    self.assertEqual(notice, '')
                    restored = initialize_dataset(path, (cube, fingerprint, saved, notice), defaults)
                    self.assertEqual((restored.view.annotations['y_axis_name'],
                                      restored.view.annotations['y_axis_unit']), (name, unit))
                    workspace = Workspace()
                    workspace.register(restored)
                    project = Path(directory) / 'annotations.spectra'
                    write_project(project, workspace)
                    reopened = read_project(project).datasets[restored.dataset_id]
                    self.assertEqual(reopened.base_cube.gate_label, '0.95Tg+Bg (V)')
                    self.assertEqual((reopened.view.annotations['y_axis_name'],
                                      reopened.view.annotations['y_axis_unit']), (name, unit))

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
