"""Independent processed Sheet/Heatmap groups and portable ownership."""
from tests.quick_helpers import set_processing_window
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import DatasetSession, ProcessingState, Workspace, detached_graph_session, graph_cube
from spectra_app.project_io import read_project, write_project
from spectra_app.workbook_state import (add_curve, add_processing_result, create_comparison, ensure_dataset_sheet,
    remove_sheet, resolve_curve, retain_current_processing_result, save_sheet_snapshot,
    select_curve_additions, sheet_values, validate_workbook_state)
from tests import quick_migration_case as migration
from tests.quick_application_helpers import pump
from spectra_app.common_prompts import PromptAction
from tests import dat_properties_quick_helpers as dat
from tests.background_helpers import wait_jobs


class ProcessingResultGroupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'sample.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{x} {x*x} {x**3} {2*x*x}' for x in np.linspace(1, 4, 31)), encoding='utf-8')
        cube = load_dat_cube(self.path)
        self.workspace = Workspace()
        self.session = self.workspace.register(DatasetSession(source_path=self.path,
            base_cube=cube, current_cube=cube))
        self.original = ensure_dataset_sheet(self.workspace, self.session.dataset_id)

    def add(self, scale, window):
        return add_processing_result(self.workspace, self.session.dataset_id,
            replace(self.session.base_cube, z=self.session.base_cube.z * scale),
            ProcessingState('energy_second_derivative', True, window))

    def test_results_keep_values_source_identity_recipe_and_visuals(self):
        self.session.active_graph.gate_indices = [0, 2]
        self.session.view.colormap = 'plasma'
        first, first_graph = self.add(2, 7)
        first_values = sheet_values(self.workspace, first).copy()
        first_graph.view.colormap = 'magma'
        second, second_graph = self.add(3, 11)
        self.assertNotEqual(first.result_id, second.result_id)
        self.assertIn('7', first.name)
        self.assertIn('11', second.name)
        self.assertEqual(second_graph.gate_indices, [0, 2])
        np.testing.assert_array_equal(sheet_values(self.workspace, first), first_values)
        np.testing.assert_array_equal(graph_cube(self.session, first_graph).z,
                                      self.session.base_cube.z[[0, 2]] * 2)
        self.session.fit_result = object()
        self.session.batch_peak_map = {0: np.array([2])}
        shared_peaks = self.session.batch_peak_map
        detached = detached_graph_session(replace(self.session, active_graph_id=first_graph.graph_id))
        self.assertFalse(detached.batch_peak_map)
        self.assertIs(self.session.batch_peak_map, shared_peaks)
        self.assertIn(0, shared_peaks)
        self.session.activate_graph(first_graph.graph_id)
        self.assertIsNone(self.session.fit_result)
        self.assertFalse(self.session.batch_peak_map)
        self.assertEqual(self.session.applied.window_length, 7)
        self.assertIs(self.session.current_cube, self.session.processing_results[first.result_id].cube)
        self.assertEqual(first_graph.view.colormap, 'magma')
        duplicate = self.session.create_graph('Copy')
        self.assertEqual(duplicate.result_id, first.result_id)
        comparison = create_comparison(self.workspace, 'Chosen results')
        curve = add_curve(self.workspace, comparison.comparison_id, first.sheet_id, 0, 1)
        self.session.activate_graph(second_graph.graph_id)
        np.testing.assert_array_equal(resolve_curve(self.workspace, curve)[1], self.session.base_cube.z[0] * 2)
        additions, skipped = select_curve_additions(self.workspace, comparison.comparison_id,
            [(second.sheet_id, 0, 1, 'processed')])
        self.assertEqual((len(additions), skipped), (1, 0))
        validate_workbook_state(self.workspace)

    def test_remove_active_graph_switches_alias_and_inactive_analysis_binds_exact_result(self):
        from spectra_app.analysis_document import ensure_analysis_graph, analysis_input_changed
        first, first_graph = self.add(2, 7)
        second, second_graph = self.add(3, 11)
        analysis = ensure_analysis_graph(self.session, first_graph.graph_id)
        self.assertEqual(analysis.analysis.science.applied.window_length, 7)
        np.testing.assert_array_equal(analysis.analysis.science.current_cube.z, self.session.base_cube.z * 2)
        self.assertFalse(analysis_input_changed(self.session, analysis))
        self.session.activate_graph(second_graph.graph_id)
        self.session.remove_graph(second_graph.graph_id)
        if self.session.active_graph.result_id is not None:
            self.assertIs(self.session.current_cube,
                self.session.processing_results[self.session.active_graph.result_id].cube)
        bound = analysis.analysis.science.current_cube.z.copy()
        remove_sheet(self.workspace, first.sheet_id)
        self.assertIsNone(analysis.analysis.source_graph_id)
        np.testing.assert_array_equal(analysis.analysis.science.current_cube.z, bound)
        write_project(Path(self.directory.name) / 'removed-graph.spectra', self.workspace)

    def test_source_replacement_preserves_groups_and_rejects_changed_source_values(self):
        first, graph = self.add(2, 7)
        base = self.session.base_cube
        changed = replace(base, z=base.z + 1)
        candidate = DatasetSession(source_path=self.path, base_cube=changed, current_cube=changed)
        with self.assertRaisesRegex(ValueError, 'new copy'):
            self.workspace.register(candidate, replace_existing=True)
        self.assertIs(self.workspace.datasets[self.session.dataset_id], self.session)
        replacement = DatasetSession(source_path=self.path, base_cube=base, current_cube=base)
        self.workspace.register(replacement, replace_existing=True)
        self.assertIn(first.result_id, replacement.processing_results)
        self.assertIn(graph.graph_id, replacement.graphs)
        validate_workbook_state(self.workspace)

    def test_delete_last_legacy_result_keeps_a_raw_fallback_graph(self):
        self.session.current_cube = replace(self.session.base_cube, z=self.session.base_cube.z * 4)
        self.session.applied = ProcessingState('energy_second_derivative', False, 9)
        result = retain_current_processing_result(self.workspace, self.session.dataset_id)
        sheet = next(sheet for sheet in self.workspace.sheets.values() if sheet.result_id == result.result_id)
        remove_sheet(self.workspace, sheet.sheet_id)
        self.assertFalse(self.session.processing_results)
        self.assertIs(self.session.current_cube, self.session.base_cube)
        self.assertEqual(self.session.active_graph.source_mode, 'raw')
        validate_workbook_state(self.workspace)

    def test_legacy_processed_is_promoted_once_before_new_result(self):
        self.session.current_cube = replace(self.session.base_cube, z=self.session.base_cube.z * 4)
        self.session.applied = ProcessingState('energy_second_derivative', False, 9)
        legacy = ensure_dataset_sheet(self.workspace, self.session.dataset_id, 'processed')
        legacy_graph = self.session.active_graph
        before = sheet_values(self.workspace, legacy).copy()
        self.add(2, 7)
        self.assertIsNotNone(legacy.result_id)
        self.assertEqual(legacy_graph.result_id, legacy.result_id)
        self.assertEqual(len(self.session.processing_results), 2)
        self.add(3, 11)
        self.assertEqual(len(self.session.processing_results), 3)
        np.testing.assert_array_equal(sheet_values(self.workspace, legacy), before)

    def test_save_load_and_delete_active_result_leave_raw_and_other_group(self):
        first, first_graph = self.add(2, 7)
        second, second_graph = self.add(3, 11)
        snapshot = save_sheet_snapshot(self.workspace, second.sheet_id, 'Chosen frozen copy')
        comparison = create_comparison(self.workspace, 'Chosen result comparison')
        retained_curve = add_curve(self.workspace, comparison.comparison_id, first.sheet_id, 0, 1)
        removed_curve = add_curve(self.workspace, comparison.comparison_id, second.sheet_id, 0, 1)
        target = Path(self.directory.name) / 'groups.spectra'
        write_project(target, self.workspace)
        loaded = read_project(target)
        owner = loaded.datasets[self.session.dataset_id]
        self.assertEqual(owner.active_graph.result_id, second.result_id)
        np.testing.assert_array_equal(sheet_values(loaded, first.sheet_id)[:, 1:],
                                      (owner.base_cube.z * 2).T)
        remove_sheet(loaded, second.sheet_id)
        self.assertNotIn(second_graph.graph_id, owner.graphs)
        self.assertNotIn(second.result_id, owner.processing_results)
        self.assertIn(first_graph.graph_id, owner.graphs)
        self.assertIn(self.original.sheet_id, loaded.sheets)
        self.assertIn(snapshot.sheet_id, loaded.sheets)
        self.assertIn(comparison.comparison_id, loaded.comparisons)
        self.assertIn(retained_curve.curve_id, loaded.comparisons[comparison.comparison_id].curves)
        self.assertNotIn(removed_curve.curve_id, loaded.comparisons[comparison.comparison_id].curves)
        self.assertIn(owner.active_graph_id, owner.graphs)
        validate_workbook_state(loaded)
        write_project(target, loaded)
        read_project(target)


class ProcessingResultGroupUiTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.path = self.folder / 'sample.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{x} {x*x + np.sin(12*x)} {x**3} {2*x*x}' for x in np.linspace(1, 4, 61)), encoding='utf-8')
        self.raw_bytes = self.path.read_bytes()
        self.window = self.controller
        self.errors = self.quick_errors
        self.window._show_error = self.errors.append
        self.window._load_dat_file_direct(self.path)
        self.window.open_graph_document()
        pump()

    def tearDown(self):
        wait_jobs(self.window)
        self.assertEqual(self.quick_errors, [], 'Unexpected production Quick error')

    def apply(self, window_length):
        w = self.window
        w.processing.setMode('energy_second_derivative')
        set_processing_window(w.processing, window_length)
        w._on_generate_heatmap()
        wait_jobs(w)
        self.assertFalse(self.errors)
        session = w.workspace.active
        result_id = session.active_graph.result_id
        sheet = next(sheet for sheet in w.workspace.sheets.values() if sheet.result_id == result_id)
        return sheet, session.active_graph

    def test_apply_preserves_groups_pending_controls_original_and_repeated_run(self):
        from spectra_app.processing import energy_second_derivative
        w, session = self.window, self.window.workspace.active
        first, first_graph = self.apply(7)
        first_values = sheet_values(w.workspace, first).copy()
        dat.set_choice(w, 'colormap', 'plasma')
        set_processing_window(w.processing, 11)
        np.testing.assert_array_equal(sheet_values(w.workspace, first), first_values)
        self.assertEqual(len(session.processing_results), 1)
        second, second_graph = self.apply(11)
        np.testing.assert_allclose(session.current_cube.z, energy_second_derivative(
            session.base_cube.photon_energy, session.base_cube.z, smooth=True, window_length=11, polyorder=3))
        w.activate_graph(first_graph.graph_id)
        self.assertEqual(w.processing.windowLength, 7)
        self.assertEqual(session.view.colormap, 'plasma')
        self.assertIs(w._quick_sheet_plot(first.sheet_id, 'heatmap'), first_graph)
        w._restore_original_data()
        self.assertEqual(len(session.processing_results), 2)
        self.assertIs(session.current_cube, session.base_cube)
        self.assertEqual(session.active_graph.source_mode, 'raw')
        w.activate_graph(second_graph.graph_id)
        third, _ = self.apply(11)
        self.assertNotEqual(third.result_id, second.result_id)
        self.assertEqual(len(session.processing_results), 3)
        np.testing.assert_array_equal(sheet_values(w.workspace, first), first_values)
        self.assertEqual(self.path.read_bytes(), self.raw_bytes)
        w.save_project(self.folder / 'ui-groups.spectra')
        self.assertFalse(self.errors)

    def test_remove_active_result_sheet_prunes_linked_pages_and_preserves_other_group(self):
        from tests.scientific_fixtures import workflow_fit_result as _fit_result
        w, session = self.window, self.window.workspace.active
        first, first_graph = self.apply(7)
        second, second_graph = self.apply(11)
        w.fit_result, w._fit_gate_idx = _fit_result(4), 0
        session.batch_peak_map = {0: np.array([2])}
        w._quick_sheet_plot(first.sheet_id, 'heatmap')
        self.assertIsNone(session.fit_result)
        self.assertFalse(session.batch_peak_map)
        w._quick_sheet_plot(second.sheet_id, 'heatmap')
        w.fit_result, w._fit_gate_idx = _fit_result(4), 0
        session.batch_peak_map = {0: np.array([2])}
        w._refresh_data_navigator()
        project = w.project
        rows = [row for row in project.all_rows() if row.label == second.name]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].key, ('sheet', second.sheet_id))
        project.select_key(rows[0].key)
        self.assertEqual(w._navigator_remove_reason(('sheet', second.sheet_id)), '')
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.YES):
            w._remove_navigator_item()
        self.assertNotIn(second.result_id, session.processing_results)
        self.assertNotIn(second_graph.graph_id, session.graphs)
        self.assertIsNone(w.graph_documents.document_for((session.dataset_id, second_graph.graph_id)))
        self.assertIn(first.sheet_id, w.workspace.sheets)
        self.assertIn(first_graph.graph_id, session.graphs)
        self.assertIsNone(session.fit_result)
        self.assertFalse(session.batch_peak_map)
        self.assertEqual(self.path.read_bytes(), self.raw_bytes)
        w.save_project(self.folder / 'after-delete.spectra')
        self.assertFalse(self.errors)

