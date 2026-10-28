"""Analysis documents are edited in one shared workbench and export only the result."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
import unittest
import sys
import threading
import numpy as np
from PySide6.QtCore import QCoreApplication
from tests.quick_migration_case import QuickMigrationCase
from tests import dat_properties_quick_helpers as dp
from tests import analysis_quick_helpers as aq
from tests.settings_helpers import wait_for_background_jobs

APP = QCoreApplication


class AnalysisWorkbenchTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.callback_errors = []
        trap = patch.object(sys, 'excepthook', lambda *args: self.callback_errors.append(args))
        trap.start()
        self.addCleanup(trap.stop)
        x = np.linspace(1., 2., 201)
        y = 1 + 3*np.exp(-((x-1.35)/.045)**2) - 1.5*np.exp(-((x-1.68)/.06)**2)
        path = self.folder / 'workbench.dat'
        path.write_text('Energy(eV) 0 2 5\n' + '\n'.join(
            f'{a} {b} {b*1.1} {b*.9}' for a, b in zip(x, y)))
        self.w = self.controller
        self.w._show_error = self.fail
        self.w._load_dat_file_direct(path)
        self.session = self.w.workspace.active
        self.source_id = self.session.active_graph_id
        self.w.open_graph_document()
        self.addCleanup(self.dispose)

    def dispose(self):
        wait_for_background_jobs(self.w, timeout=10)
        APP.instance().clipboard().clear()
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.quick_errors, [])

    def enter(self):
        self.w._open_analysis(('heatmap', (self.session.dataset_id, self.source_id)))
        graph = self.session.active_graph
        self.assertNotEqual(graph.graph_id, self.source_id, 'Analyze must create an independent result graph')
        self.assertIsNotNone(graph.analysis)
        return graph

    def test_analyze_creates_reuses_and_project_graph_opens_shared_editor(self):
        source = self.session.active_graph
        before = deepcopy(source.view)
        graph = self.enter()
        self.assertEqual(len(self.session.graphs), 2)
        self.assertEqual(source.view, before)
        self.assertNotIn((self.session.dataset_id, graph.graph_id),
                         self.w.graph_documents.registry.open_keys())
        self.assertIs(self.w.graph_documents.document_for(
            (self.session.dataset_id, graph.graph_id)), self.w.plot_page)
        self.w._back_to_workspace()
        self.assertEqual(self.session.active_graph_id, self.source_id)
        self.w.open_graph_document(self.session.dataset_id, graph.graph_id)
        self.assertEqual(self.w.outerView, 'analysis')
        self.assertEqual(self.session.active_graph_id, graph.graph_id)
        self.w._open_analysis(('heatmap', (self.session.dataset_id, self.source_id)))
        self.assertEqual(self.session.active_graph_id, graph.graph_id)
        self.assertEqual(len(self.session.graphs), 2)

    def assert_result_export_identity(self):
        page = self.w.plot_page.rendered_page
        self.assertIsNotNone(page)
        self.assertIs(self.w.workbench_preview.page, page)
        self.assertEqual(self.w.workbench_preview.svg_bytes, page.svg_bytes)
        self.assertEqual(self.w.copy_graph_svg().svg_bytes, page.svg_bytes)

    def find(self, *, all_gates=True):
        aq.set_choice(self.w, 'scope', 'all' if all_gates else 'current')
        aq.set_choice(self.w, 'mode', 'Both')
        aq.command(self.w, 'find')
        wait_for_background_jobs(self.w, timeout=15)
        APP.processEvents()

    def test_find_filter_fit_uses_bound_input_and_formal_result(self):
        graph = self.enter()
        doc = graph.analysis
        original = self.session.current_cube.z.copy()
        self.find()
        self.assertEqual(set(doc.candidate_pools), {0, 1, 2})
        self.assertTrue(all(len(doc.accepted_seeds(g)) == 2 for g in range(3)))
        self.assertEqual({s.kind for s in doc.accepted_seeds(0)}, {'peak', 'dip'})
        self.assert_result_export_identity()
        retained = deepcopy(doc.candidate_pools)
        aq.setup_filter(self.w, 'min_prominence_fraction', 1.)
        aq.command(self.w, 'filter')
        self.assertEqual(len(doc.accepted_seeds(0)), 0)
        aq.set_choice(self.w, 'filterPreset', 'Loose')
        aq.command(self.w, 'filter')
        self.assertEqual(doc.candidate_pools, retained)
        self.assertEqual(len(doc.accepted_seeds(0)), 2)
        APP.processEvents()
        self.assert_result_export_identity()
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        APP.processEvents()
        self.assertEqual(doc.gate_status, {0: 'fitted', 1: 'fitted', 2: 'fitted'})
        for fit in doc.science.batch_fit_result_map.values():
            self.assertGreater(fit.r_squared, .999)
            self.assertEqual(len(fit.x), original.shape[1])
        np.testing.assert_array_equal(self.session.current_cube.z, original)
        self.assertEqual(self.session.batch_peak_map, {})
        self.assertEqual(self.session.batch_fit_result_map, {})
        self.assertEqual(len(self.session.graphs), 2)
        self.w._workbench_render()
        page = self.w.plot_page.rendered_page
        self.assertIsNotNone(page)
        self.assertEqual(page.svg_bytes, self.w.workbench_preview.svg_bytes)
        self.assertNotIn(b'Auxiliary linecut', page.svg_bytes)
        self.assertNotIn(b'Total fit', page.svg_bytes)
        self.assertTrue(self.w.plot_page.preview.snapshot.scatter_layers)
        self.assertEqual(self.w.copy_graph_svg().svg_bytes, self.w.workbench_preview.svg_bytes)
        self.assertIn(b'analysis-scatter-fitted-peak', self.w.workbench_preview.svg_bytes)
        aq.setup_number(self.w, 'xmin', 1.01)
        APP.processEvents()
        self.assertEqual(doc.science.batch_fit_result_map, {})
        self.assertNotIn(b'analysis-scatter-fitted-peak', self.w.workbench_preview.svg_bytes,
                         'Retiring a fit must refresh the visible formal result immediately')

    def test_manual_seed_edit_retires_affected_fit_and_voigt_preserves_fixed_center(self):
        graph = self.enter()
        doc = graph.analysis
        self.find(all_gates=False)
        gate = self.w._current_gate_index()
        seeds = doc.accepted_seeds(gate)
        fixed = seeds[0].position + .00071
        doc.set_seeds(gate, [replace(seeds[0], position=fixed, fixed_center=True, width=.025, gamma=.01), seeds[1]])
        self.w._workbench_refresh()
        aq.set_choice(self.w, 'model', 'Voigt')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        self.assertEqual(doc.gate_status[gate], 'fitted', doc.gate_messages)
        result = doc.science.batch_fit_result_map[gate]
        self.assertTrue(np.any(result.centers == fixed))
        self.assertIsNotNone(result.gammas)
        self.assertIsNotNone(result.sigmas)
        aq.edit_seed(self.w, doc.seeds_by_gate[gate][0].seed_id, 'position', fixed+.002)
        self.assertNotIn(gate, doc.science.batch_fit_result_map)
        self.assertAlmostEqual(doc.seeds_by_gate[gate][0].position, fixed+.002)
        self.assertEqual(self.session.fit_result, None)

    def test_seed_selection_survives_refresh_by_identity_and_rejects_stale_owner(self):
        from spectra_app.analysis_document import AnalysisSeed
        doc = self.enter().analysis
        bridge = self.w.analysis_workbench
        gate = self.w._current_gate_index()
        first, second = AnalysisSeed(1.35, manual=True), AnalysisSeed(1.68, kind='dip', manual=True)
        doc.set_seeds(gate, [first, second])
        self.w._workbench_refresh()
        owner = bridge.ownerToken
        bridge.selectSeed(second.seed_id, gate, owner)
        self.assertEqual(aq.state(self.w)['selectedSeedId'], second.seed_id)
        doc.set_seeds(gate, [second, first])
        for _ in range(5):
            self.w._workbench_refresh()
            self.assertEqual(aq.state(self.w)['selectedSeedId'], second.seed_id)
            self.assertEqual([row['seedId'] for row in aq.state(self.w)['seeds']],
                             [second.seed_id, first.seed_id])
        aq.command(self.w, 'remove')
        self.assertEqual([seed.seed_id for seed in doc.seeds_by_gate[gate]], [first.seed_id])
        self.w._workbench_duplicate()
        duplicate = self.session.active_graph.analysis
        before = deepcopy(duplicate.seeds_by_gate)
        bridge.draftSeed(first.seed_id, gate, 'position', '1.44', owner)
        bridge.command('remove', owner)
        self.assertTrue(bridge.commitEditors(bridge.ownerToken))
        self.assertEqual(duplicate.seeds_by_gate, before)
        self.assertEqual(doc.seeds_by_gate[gate], [first])

    def test_results_table_pairs_only_participating_seeds_with_narrow_interval_fit(self):
        from spectra_app.quick_dialog import QuickDialog, _transient_window
        from PySide6.QtGui import QGuiApplication, QWindow
        graph = self.enter()
        self.find(all_gates=False)
        gate = self.w._current_gate_index()
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.setup_number(self.w, 'xmin', 1.55)
        aq.setup_number(self.w, 'xmax', 1.9)
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        fit = graph.analysis.science.batch_fit_result_map[gate]
        self.assertEqual(fit.centers.size, 1)
        unrelated = QWindow()
        self.addCleanup(unrelated.deleteLater)
        self.addCleanup(unrelated.close)
        unrelated.show()
        unrelated.requestActivate()
        aq.pump()
        self.assertIs(QGuiApplication.focusWindow(), unrelated)
        self.assertIs(_transient_window(self.host), self.w.root_window)
        dialog = self.w._workbench_results_table()
        self.assertIs(dialog.window().transientParent(), self.w.root_window)
        self.addCleanup(dialog.reject)
        self.assertIsInstance(dialog, QuickDialog)
        bridge = dialog.bridge
        rows = [['' if value is None else str(value) for value in row]
                for row in bridge.snapshot_rows if float(row[0]) == self.w.current_cube.gate[gate]]
        self.assertEqual(len(rows), 2, 'Out-of-interval accepted seeds remain visible')
        peak, dip = next(row for row in rows if row[2] == 'peak'), next(row for row in rows if row[2] == 'dip')
        self.assertEqual(peak[4:12], [''] * 8)
        self.assertAlmostEqual(float(dip[4]), fit.centers[0])
        self.assertAlmostEqual(float(dip[6]), fit.widths[0])

    def test_source_format_follows_continuously_without_rebinding_science(self):
        graph = self.enter()
        self.find(all_gates=False)
        version = graph.analysis.input_version
        pools = deepcopy(graph.analysis.candidate_pools)
        self.w._workbench_source_graph()
        self.assertEqual(self.session.active_graph_id, self.source_id)
        self.w.dat_properties.setLabel('plot_title', 'Linked source format', self.w.dat_properties.ownerToken)
        source = self.session.active_graph
        self.w.set_plot_presentation(replace(source.presentation, numeric_pt=9., text_pt=12.,
            frame_width_in=3.1, frame_height_in=2.0, axis=replace(source.presentation.axis, exchange_xy=True)))
        dp.set_choice(self.w, 'colormap', 'plasma')
        self.w._finish_graph_editors()
        self.w.open_graph_document(self.session.dataset_id, graph.graph_id)
        snapshot = self.w.plot_page.preview.snapshot
        self.assertEqual(snapshot.title, 'Linked source format')
        self.assertEqual(self.w.plot_page.presentation.numeric_pt, 9.)
        self.assertTrue(self.w.plot_page.presentation.axis.exchange_xy)
        self.assertEqual(graph.analysis.input_version, version)
        self.assertEqual(graph.analysis.candidate_pools, pools)
        self.w.request_task(2)
        aq.pump()
        self.assertFalse(dp.dat_item(self.w, 'sharedDatProperties').isEnabled())
        self.w.request_task(1)
        self.assert_result_export_identity()

    def test_project_reopen_restores_progress_and_duplicate_is_independent(self):
        graph = self.enter()
        self.find(all_gates=False)
        gate = self.w._current_gate_index()
        self.w._workbench_add_seed(position=1.55, kind='dip')
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.set_choice(self.w, 'filterPreset', 'Strict')
        aq.command(self.w, 'filter')
        self.w._set_current_gate_from_index(2, update_plot=True)
        before = deepcopy(graph.analysis.seeds_by_gate)
        path = self.folder / 'analysis.spectra'
        self.w.save_project(path)
        with patch('spectra_app.peak_candidates.collect_peak_candidates', side_effect=AssertionError('Unexpected recomputation')):
            self.assertTrue(self.w.open_project(path))
            self.session = self.w.workspace.active
            self.w.open_graph_document(self.session.dataset_id, graph.graph_id)
        restored = self.session.active_graph.analysis
        self.assertEqual(restored.seeds_by_gate, before)
        self.assertEqual(self.w._current_gate_index(), 2)
        self.assertEqual(aq.state(self.w)['model'], 'Gaussian')
        self.assertEqual(aq.state(self.w)['scope'], 'current')
        self.assert_result_export_identity()
        self.w._workbench_duplicate()
        duplicate = self.session.active_graph.analysis
        self.assertIsNot(duplicate, restored)
        self.assertEqual(duplicate.seeds_by_gate, before)
        self.assert_result_export_identity()
        self.w._workbench_add_seed(position=1.44)
        self.assertNotEqual(duplicate.seeds_by_gate, restored.seeds_by_gate)

    def test_raw_sparse_graph_binds_full_raw_input_and_rebind_retires_results(self):
        source = self.session.active_graph
        source.source_mode = 'raw'
        source.gate_indices = [0, 2]
        self.session.current_cube = replace(self.session.current_cube, z=self.session.current_cube.z*0)
        graph = self.enter()
        self.assertEqual(graph.gate_indices, [0, 2])
        self.find()
        self.assertEqual(set(graph.analysis.candidate_pools), {0, 1, 2})
        self.assertTrue(graph.analysis.accepted_seeds(1))
        self.assertEqual(self.w.plot_page.preview.snapshot.z.shape[0], 2)
        self.w._set_current_gate_from_index(1, update_plot=True)
        self.assertEqual(self.w._current_gate_index(), 1, 'Auxiliary inspection includes omitted display Gates')
        version = graph.analysis.input_version
        aq.set_choice(self.w, 'inputMode', 'processed')
        with patch.object(self.w, '_workbench_confirm_rebind', return_value='replace'):
            aq.command(self.w, 'rebind')
        self.assertNotEqual(graph.analysis.input_version, version)
        self.assertEqual(graph.analysis.candidate_pools, {})
        self.assert_result_export_identity()
        self.find()
        self.assertTrue(all(status == 'empty' for status in graph.analysis.gate_status.values()))
        self.assertEqual(source.gate_indices, [0, 2])

    def test_save_commits_pending_interval_window_and_seed_editor(self):
        from spectra_app.project_io import read_project
        graph = self.enter()
        self.find(all_gates=False)
        self.w.root_window.show()
        APP.processEvents()
        gate = self.w._current_gate_index()
        # The interval edit is finished by the next real focus change. The
        # final seed text remains active when Save captures the document.
        aq.set_choice(self.w, 'detector', 'D2')
        self.w.save_project(self.folder / 'drafts.spectra')
        self.assertFalse(self.w.projectModified)
        self.assertTrue(self.w.request_task(2))
        aq.pump()
        view_xmin = dp.dat_item(self.w, 'xmin')
        view_text = view_xmin.property('text')
        original_xmin = graph.analysis.science.analysis_controls.fit_xmin
        editor = aq.edit_number(self.w, 'xmin', '-')
        self.assertIsNot(editor, view_xmin)
        self.assertTrue(aq.item(self.w, 'analysisWorkbench').isAncestorOf(editor))
        self.assertEqual(view_xmin.property('text'), view_text)
        self.w._refresh_project_modified()
        self.assertEqual(graph.analysis.science.analysis_controls.fit_xmin, original_xmin)
        self.assertTrue(self.w.projectModified, 'An invalid active Quick draft is unsaved work')
        aq.edit_number(self.w, 'xmin', '1.11')
        aq.edit_number(self.w, 'd2Window', '17')
        seed_id = graph.analysis.seeds_by_gate[gate][0].seed_id
        aq.select_seed(self.w, seed_id)
        aq.command(self.w, 'seeds')
        aq.pump()
        editor = aq.type_text(self.w.workbench_seed_dialog, seed_id + ':position', '1.35123')
        self.assertEqual(editor.property('text'), '1.35123')
        self.assertNotEqual(graph.analysis.seeds_by_gate[gate][0].position, 1.35123,
                            'Seed draft must still be pending at Save')
        self.w._refresh_project_modified()
        self.assertTrue(self.w.projectModified)
        self.w.save_project(self.folder / 'drafts.spectra')
        self.assertFalse(self.w.projectModified)
        restored = read_project(self.folder / 'drafts.spectra').active.active_graph.analysis
        self.assertEqual(restored.science.analysis_controls.fit_xmin, 1.11)
        self.assertEqual(restored.science.analysis_controls.d2_window, 17)
        self.assertAlmostEqual(restored.seeds_by_gate[gate][0].position, 1.35123)

    def test_filter_preserves_manual_move_kind_and_exclusion(self):
        graph = self.enter()
        self.find(all_gates=False)
        gate = self.w._current_gate_index()
        aq.edit_seed(self.w, graph.analysis.seeds_by_gate[gate][0].seed_id, 'position', '1.3571')
        aq.edit_seed(self.w, graph.analysis.seeds_by_gate[gate][0].seed_id, 'kind', 'dip')
        aq.set_choice(self.w, 'filterPreset', 'Strict')
        aq.command(self.w, 'filter')
        aq.set_choice(self.w, 'filterPreset', 'Loose')
        aq.command(self.w, 'filter')
        seeds = graph.analysis.accepted_seeds(gate)
        self.assertTrue(any(s.position == 1.3571 and s.kind == 'dip' for s in seeds))
        self.assertFalse(any(s.kind == 'peak' for s in seeds))

    def test_original_and_processed_sheet_entry_preserves_separate_input_progress(self):
        from spectra_app.workbook_state import ensure_dataset_sheet
        self.session.current_cube = replace(self.session.current_cube, z=self.session.current_cube.z*0)
        ensure_dataset_sheet(self.w.workspace, self.session.dataset_id, 'processed')
        original = next(s for s in self.w.workspace.sheets.values() if s.dataset_id == self.session.dataset_id and s.role == 'original')
        processed = next(s for s in self.w.workspace.sheets.values() if s.dataset_id == self.session.dataset_id and s.role == 'processed')
        self.w._open_analysis(('sheet', original.sheet_id))
        original_graph = self.session.active_graph
        self.assertEqual(original_graph.analysis.source_mode, 'raw')
        self.find(all_gates=False)
        original_pools = deepcopy(original_graph.analysis.candidate_pools)
        self.assertTrue(original_graph.analysis.accepted_seeds(self.w._current_gate_index()))
        self.w._open_analysis(('sheet', processed.sheet_id))
        processed_graph = self.session.active_graph
        self.assertNotEqual(processed_graph.graph_id, original_graph.graph_id)
        self.assertEqual(processed_graph.analysis.source_mode, 'processed')
        self.find(all_gates=False)
        self.assertTrue(all(s == 'empty' for s in processed_graph.analysis.gate_status.values()))
        self.w._open_analysis(('sheet', original.sheet_id))
        self.assertIs(self.session.active_graph, original_graph)
        self.assertEqual(original_graph.analysis.candidate_pools, original_pools)

    def test_sheet_entry_after_source_deletion_retains_format_and_existing_result(self):
        from spectra_app.workbook_state import ensure_dataset_sheet
        self.w.dat_properties.setLabel('plot_title', 'Retained analysis format', self.w.dat_properties.ownerToken)
        dp.set_choice(self.w, 'colormap', 'plasma')
        self.w.set_plot_presentation(replace(self.session.presentation, numeric_pt=9., text_pt=12.))
        retained = self.enter()
        self.find(all_gates=False)
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        doc = retained.analysis
        gate = self.w._current_gate_index()
        self.assertEqual(doc.gate_status[gate], 'fitted')
        fit = doc.science.batch_fit_result_map[gate]
        input_version = doc.input_version
        input_z = doc.science.current_cube.z.copy()
        pools = deepcopy(doc.candidate_pools)
        seeds = deepcopy(doc.seeds_by_gate)
        statuses = dict(doc.gate_status)
        self.session.current_cube = replace(self.session.current_cube, z=self.session.current_cube.z*0)
        ensure_dataset_sheet(self.w.workspace, self.session.dataset_id, 'processed')

        for role, mode in (('original', 'raw'), ('processed', 'processed')):
            with self.subTest(role=role):
                self.w.open_graph_document(self.session.dataset_id, retained.graph_id)
                for source_id in [g.graph_id for g in self.session.graphs.values() if g.analysis is None]:
                    self.w.remove_graph(source_id)
                self.assertTrue(all(g.analysis is not None for g in self.session.graphs.values()))
                self.assertIsNone(doc.source_graph_id)
                retained_view = deepcopy(retained.view)
                retained_presentation = deepcopy(retained.presentation)
                sheet = next(s for s in self.w.workspace.sheets.values()
                             if s.dataset_id == self.session.dataset_id and s.role == role)

                self.w._open_analysis(('sheet', sheet.sheet_id))

                result = self.session.active_graph
                self.assertIsNot(result, retained)
                self.assertEqual(result.analysis.source_mode, mode)
                source = self.session.graphs[result.analysis.source_graph_id]
                self.assertIsNone(source.analysis)
                self.assertEqual(source.source_mode, mode)
                self.assertEqual(source.view, retained_view)
                self.assertEqual(source.presentation, retained_presentation)
                self.assertIsNot(source.view, retained.view)
                self.assertIsNot(source.view.annotations, retained.view.annotations)
                expected_cube = self.session.base_cube if role == 'original' else self.session.current_cube
                np.testing.assert_array_equal(result.analysis.science.current_cube.z, expected_cube.z)
                self.assertEqual(doc.input_version, input_version)
                np.testing.assert_array_equal(doc.science.current_cube.z, input_z)
                self.assertEqual(doc.candidate_pools, pools)
                self.assertEqual(doc.seeds_by_gate, seeds)
                self.assertEqual(doc.gate_status, statuses)
                self.assertIs(doc.science.batch_fit_result_map[gate], fit)

    def test_cancelled_find_keeps_existing_pool_and_idle_controls_recover(self):
        from spectra_app.peak_candidates import collect_peak_candidates
        graph = self.enter()
        self.find()
        self.w.root_window.show()
        APP.processEvents()
        before = deepcopy(graph.analysis.candidate_pools)
        release, started = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            started.set()
            release.wait(5)
            return collect_peak_candidates(*args, **kwargs)
        try:
            with patch('spectra_app.peak_candidates.collect_peak_candidates', side_effect=blocked):
                aq.command(self.w, 'find')
                self.assertTrue(started.wait(2))
                self.assertFalse(aq.control(self.w, 'analysis_find').isEnabled())
                self.assertTrue(aq.control(self.w, 'analysis_stop').isVisible())
                self.assertTrue(aq.control(self.w, 'analysis_stop').isEnabled())
                aq.click(self.w, 'analysis_stop')
                release.set()
                wait_for_background_jobs(self.w, timeout=10)
        finally:
            release.set()
        self.assertEqual(graph.analysis.candidate_pools, before)
        self.assertTrue(aq.control(self.w, 'analysis_find').isEnabled())

    def test_failed_and_no_seed_gate_outcomes_are_distinct(self):
        graph = self.enter()
        aq.set_choice(self.w, 'scope', 'all')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=10)
        self.assertEqual(set(graph.analysis.gate_status.values()), {'no-seed'})
        self.find()
        with patch('spectra_app.fitting.fit_multi_peak', side_effect=ValueError('controlled invalid input')):
            aq.command(self.w, 'fit')
            wait_for_background_jobs(self.w, timeout=10)
        self.assertEqual(set(graph.analysis.gate_status.values()), {'failed'})
        self.assertTrue(all('controlled' in message for message in graph.analysis.gate_messages.values()))


if __name__ == '__main__':
    unittest.main()
