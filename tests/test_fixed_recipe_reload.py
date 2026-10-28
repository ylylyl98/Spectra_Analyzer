"""Reloaded fixed projections keep independent ownership from retained runs."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import (AnalysisControls, DatasetDefaults, FixedRecipe,
                                      ProcessingState, ViewState, Workspace,
                                      graph_cube, initialize_dataset)
from spectra_app.processing import energy_second_derivative
from spectra_app.project_io import read_project, write_project
from spectra_app.workbook_state import add_processing_result, sheet_values, validate_workbook_state


class FixedRecipeReloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'sample.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{x} {x*x + np.sin(12*x)} {x**3} {2*x*x}'
            for x in np.linspace(1, 4, 61)), encoding='utf-8')
        self.cube = load_dat_cube(self.path)
        self.defaults = DatasetDefaults(ViewState(), AnalysisControls(), {
            group: FixedRecipe() for group in ('processing', 'regions', 'region_edges')}, {})
        self.workspace = Workspace()
        self.session = self.workspace.register(self.initialize())
        self.workspace.active_dataset_id = self.session.dataset_id
        processing = ProcessingState('energy_second_derivative', True, 9)
        transformed = energy_second_derivative(self.cube.photon_energy, self.cube.z,
                                              smooth=True, window_length=9)
        self.sheet, self.graph = add_processing_result(self.workspace, self.session.dataset_id,
            replace(self.cube, z=transformed), processing)
        self.graph.gate_indices = [0, 2]
        self.graph.view.colormap = 'magma'
        self.graph.view.locks['vmin'] = self.graph.view.locks['vmax'] = True
        self.graph.view.bounds.update(vmin=-999., vmax=999.)
        self.graph.view.color_limits = (-999., 999.)

    def initialize(self, saved=None):
        return initialize_dataset(self.path, (self.cube, 'same-source', saved, ''), self.defaults)

    def reload(self):
        candidate = self.initialize(self.session.plot_collection())
        self.workspace.register(candidate, replace_existing=True)
        self.session = candidate
        return candidate

    def test_reload_uses_latest_fixed_processing_and_preserves_result_ownership(self):
        historical_view = deepcopy(self.graph.view)
        historical_name = self.graph.name
        historical_values = sheet_values(self.workspace, self.sheet).copy()
        self.defaults.fixed_recipes['processing'] = FixedRecipe(True, {
            'mode': 'energy_second_derivative', 'smooth': True, 'window_length': 11})
        expected = energy_second_derivative(self.cube.photon_energy, self.cube.z,
                                           smooth=True, window_length=11)
        reloaded = self.reload()
        self.assertEqual(reloaded.applied.window_length, 11)
        self.assertEqual(reloaded.pending.window_length, 11)
        np.testing.assert_allclose(reloaded.current_cube.z, expected)
        active = reloaded.active_graph
        self.assertNotEqual(active.graph_id, self.graph.graph_id)
        self.assertNotEqual(active.result_id, self.graph.result_id)
        self.assertIsNotNone(active.result_id)
        self.assertEqual(active.source_mode, 'processed')
        np.testing.assert_allclose(graph_cube(reloaded).z, expected[[0, 2]])
        current_sheet = next(sheet for sheet in self.workspace.sheets.values()
                             if sheet.result_id == active.result_id)
        self.assertEqual(active.name, f'{current_sheet.name} Heatmap')
        self.assertIn('SG 11, p3', active.name)
        self.assertEqual(self.graph.name, historical_name)
        np.testing.assert_allclose(sheet_values(self.workspace, current_sheet)[:, 1:], expected.T)
        self.assertEqual(len(reloaded.processing_results), 2)
        self.assertIs(reloaded.graphs[self.graph.graph_id], self.graph)
        self.assertEqual(self.graph.view, historical_view)
        np.testing.assert_array_equal(sheet_values(self.workspace, self.sheet), historical_values)
        self.assertFalse(active.view.locks['vmin'])
        self.assertFalse(active.view.locks['vmax'])
        self.assertNotEqual(active.view.color_limits, (-999., 999.))
        reloaded.activate_graph(self.graph.graph_id)
        self.assertEqual(reloaded.applied.window_length, 9)
        reloaded.activate_graph(active.graph_id)
        self.assertEqual(reloaded.applied.window_length, 11)
        np.testing.assert_allclose(graph_cube(reloaded).z, expected[[0, 2]])
        validate_workbook_state(self.workspace)
        project = self.path.with_suffix('.spectra')
        write_project(project, self.workspace)
        restored = read_project(project)
        owner = restored.datasets[reloaded.dataset_id]
        self.assertEqual(owner.active_graph_id, active.graph_id)
        self.assertEqual(owner.applied.window_length, 11)
        self.assertEqual(owner.active_graph.result_id, current_sheet.result_id)
        self.assertEqual(owner.active_graph.name, active.name)
        self.assertEqual(owner.graphs[self.graph.graph_id].name, historical_name)
        np.testing.assert_allclose(graph_cube(owner).z, expected[[0, 2]])
        np.testing.assert_allclose(sheet_values(restored, current_sheet.sheet_id)[:, 1:], expected.T)
        self.assertEqual(owner.graphs[self.graph.graph_id].view, historical_view)
        np.testing.assert_array_equal(sheet_values(restored, self.sheet.sheet_id), historical_values)

    def test_fixed_regions_refresh_reloaded_colors_without_rewriting_history(self):
        historical_view = deepcopy(self.graph.view)
        self.graph.name = 'Selected feature (SG 9)'
        self.defaults.fixed_recipes['regions'] = FixedRecipe(True, {'mode': '2'})
        reloaded = self.reload()
        self.assertEqual(reloaded.view.color_scale_mode, '2')
        self.assertFalse(reloaded.view.locks['vmin'])
        self.assertFalse(reloaded.view.locks['vmax'])
        self.assertNotEqual(reloaded.view.color_limits, (-999., 999.))
        self.assertEqual(self.graph.view, historical_view)
        self.assertIs(reloaded.graphs[self.graph.graph_id], self.graph)
        self.assertEqual(reloaded.processing_results[self.graph.result_id].processing.window_length, 9)
        self.assertNotEqual(reloaded.active_graph.result_id, self.graph.result_id)
        self.assertEqual(self.graph.name, 'Selected feature (SG 9)')
        self.assertEqual(reloaded.active_graph.name, 'Selected feature (SG 9) 2')
        validate_workbook_state(self.workspace)

    def test_fixed_original_replaces_generated_derivative_title_and_retains_history(self):
        historical_name = self.graph.name
        self.defaults.fixed_recipes['processing'] = FixedRecipe(True, {'mode': 'original'})
        reloaded = self.reload()
        self.assertEqual(reloaded.applied.mode, 'original')
        self.assertIs(reloaded.current_cube, reloaded.base_cube)
        self.assertIsNone(reloaded.active_graph.result_id)
        self.assertEqual(reloaded.active_graph.source_mode, 'raw')
        self.assertEqual(reloaded.active_graph.name, 'Original Heatmap')
        self.assertEqual(self.graph.name, historical_name)
        self.assertIs(reloaded.graphs[self.graph.graph_id], self.graph)
        self.assertEqual(len(reloaded.processing_results), 1)
        validate_workbook_state(self.workspace)

    def test_repeated_reload_retains_inactive_historical_graphs_without_cloning_them(self):
        detail = self.session.create_graph('Detail')
        detail.view.colormap = 'plasma'
        self.session.activate_graph(self.graph.graph_id)
        self.defaults.fixed_recipes['processing'] = FixedRecipe(True, {
            'mode': 'energy_second_derivative', 'smooth': True, 'window_length': 11})
        historical = {graph.graph_id: graph for graph in self.session.graphs.values()
                      if graph.result_id is not None}
        graph_count = len(self.session.graphs)
        result_count = len(self.session.processing_results)
        for _ in range(3):
            reloaded = self.reload()
            graph_count += 1
            result_count += 1
            self.assertEqual(len(reloaded.graphs), graph_count)
            self.assertEqual(len(reloaded.processing_results), result_count)
            self.assertEqual(reloaded.applied.window_length, 11)
            current_sheet = next(sheet for sheet in self.workspace.sheets.values()
                                 if sheet.result_id == reloaded.active_graph.result_id)
            self.assertEqual(reloaded.active_graph.name, f'{current_sheet.name} Heatmap')
            self.assertNotIn(reloaded.active_graph_id, historical)
            for identity, graph in historical.items():
                self.assertIs(reloaded.graphs[identity], graph)
            historical[reloaded.active_graph_id] = reloaded.active_graph
            validate_workbook_state(self.workspace)
        self.assertEqual(detail.view.colormap, 'plasma')
