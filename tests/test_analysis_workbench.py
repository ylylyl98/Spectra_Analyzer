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
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings, wait_for_background_jobs

APP = QApplication.instance() or QApplication([])


class AnalysisWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.callback_errors = []
        trap = patch.object(sys, 'excepthook', lambda *args: self.callback_errors.append(args))
        trap.start()
        self.addCleanup(trap.stop)
        x = np.linspace(1., 2., 201)
        y = 1 + 3*np.exp(-((x-1.35)/.045)**2) - 1.5*np.exp(-((x-1.68)/.06)**2)
        path = self.folder / 'workbench.dat'
        path.write_text('Energy(eV) 0 2 5\n' + '\n'.join(
            f'{a} {b} {b*1.1} {b*.9}' for a, b in zip(x, y)))
        self.w = MainWindow()
        self.w._show_error = self.fail
        self.w._load_dat_file_direct(path)
        self.session = self.w.workspace.active
        self.source_id = self.session.active_graph_id
        self.w.open_graph_document()
        self.addCleanup(self.dispose)

    def dispose(self):
        wait_for_background_jobs(self.w, timeout=10)
        self.w.close()
        self.w.deleteLater()
        APP.clipboard().clear()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertEqual(self.callback_errors, [])

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
        self.assertIsNone(self.w.graph_documents.window_for((self.session.dataset_id, graph.graph_id)))
        self.w._back_to_workspace()
        self.assertEqual(self.session.active_graph_id, self.source_id)
        self.w.open_graph_document(self.session.dataset_id, graph.graph_id)
        self.assertEqual(self.w.plot_tabs.currentIndex(), 0)
        self.assertEqual(self.session.active_graph_id, graph.graph_id)
        self.w._open_analysis(('heatmap', (self.session.dataset_id, self.source_id)))
        self.assertEqual(self.session.active_graph_id, graph.graph_id)
        self.assertEqual(len(self.session.graphs), 2)

    def find(self, *, all_gates=True):
        self.w.workbench_scope.setCurrentIndex(int(all_gates))
        self.w.workbench_mode.setCurrentText('Both')
        self.w.workbench_find_btn.click()
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
        retained = deepcopy(doc.candidate_pools)
        self.w.workbench_filter_controls['min_prominence_fraction'].setValue(1.)
        self.w.workbench_filter_btn.click()
        self.assertEqual(len(doc.accepted_seeds(0)), 0)
        self.w.workbench_filter_preset.setCurrentText('Loose')
        self.w.workbench_filter_btn.click()
        self.assertEqual(doc.candidate_pools, retained)
        self.assertEqual(len(doc.accepted_seeds(0)), 2)
        self.w.workbench_model.setCurrentText('Gaussian')
        self.w.workbench_fit_btn.click()
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
        self.w.workbench_xmin.setValue(1.01)
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
        self.w.workbench_model.setCurrentText('Voigt')
        self.w.workbench_fit_btn.click()
        wait_for_background_jobs(self.w, timeout=15)
        self.assertEqual(doc.gate_status[gate], 'fitted', doc.gate_messages)
        result = doc.science.batch_fit_result_map[gate]
        self.assertTrue(np.any(result.centers == fixed))
        self.assertIsNotNone(result.gammas)
        self.assertIsNotNone(result.sigmas)
        self.w.workbench_seeds.item(0, 1).setText(str(fixed+.002))
        self.assertNotIn(gate, doc.science.batch_fit_result_map)
        self.assertAlmostEqual(doc.seeds_by_gate[gate][0].position, fixed+.002)
        self.assertEqual(self.session.fit_result, None)

    def test_seed_summary_refresh_releases_replaced_native_items(self):
        import shiboken6
        from PySide6.QtCore import QByteArray
        from PySide6.QtSvg import QSvgRenderer
        from spectra_app.analysis_document import AnalysisSeed

        doc = self.enter().analysis
        gate = self.w._current_gate_index()
        doc.set_seeds(gate, [AnalysisSeed(1.35), AnalysisSeed(1.68, kind='dip', enabled=False)])
        self.w._workbench_refresh()
        for _ in range(5):
            old_items = [self.w.workbench_seed_summary.item(row, column)
                         for row in range(2) for column in range(3)]
            self.w._workbench_refresh()
            self.assertTrue(all(not shiboken6.isValid(item) for item in old_items),
                            'Replaced summary items must leave no stale native pointer registrations')
            for row in range(2):
                for column in range(3):
                    summary = self.w.workbench_seed_summary.item(row, column)
                    detail = self.w.workbench_seeds.item(row, column)
                    self.assertEqual(summary.text(), detail.text())
                    self.assertEqual(summary.flags(), detail.flags())
                    self.assertEqual(summary.checkState(), detail.checkState())
            # New QObjects may reuse the released table-item addresses.
            renderer = QSvgRenderer(QByteArray(b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'))
            self.assertTrue(renderer.isValid())

    def test_results_table_pairs_only_participating_seeds_with_narrow_interval_fit(self):
        from PySide6.QtWidgets import QDialog, QTableWidget
        graph = self.enter()
        self.find(all_gates=False)
        gate = self.w._current_gate_index()
        self.w.workbench_model.setCurrentText('Gaussian')
        self.w.workbench_xmin.setValue(1.55)
        self.w.workbench_xmax.setValue(1.9)
        self.w.workbench_fit_btn.click()
        wait_for_background_jobs(self.w, timeout=15)
        fit = graph.analysis.science.batch_fit_result_map[gate]
        self.assertEqual(fit.centers.size, 1)
        with patch.object(QDialog, 'exec', return_value=0):
            self.w._workbench_results_table()
        dialog = next(d for d in self.w.findChildren(QDialog) if d.windowTitle() == 'Analysis results')
        table = dialog.findChild(QTableWidget)
        rows = [[table.item(row, col).text() for col in range(table.columnCount())]
                for row in range(table.rowCount()) if float(table.item(row, 0).text()) == self.w.current_cube.gate[gate]]
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
        self.w.plot_title_edit.setText('Linked source format')
        source = self.session.active_graph
        self.w.set_plot_presentation(replace(source.presentation, numeric_pt=9., text_pt=12.,
            frame_width_in=3.1, frame_height_in=2.0, axis=replace(source.presentation.axis, exchange_xy=True)))
        self.w.cmap_combo.setCurrentText('plasma')
        self.w._finish_graph_editors()
        self.w.open_graph_document(self.session.dataset_id, graph.graph_id)
        snapshot = self.w.plot_page.preview.snapshot
        self.assertEqual(snapshot.title, 'Linked source format')
        self.assertEqual(self.w.plot_page.presentation.numeric_pt, 9.)
        self.assertTrue(self.w.plot_page.presentation.axis.exchange_xy)
        self.assertEqual(graph.analysis.input_version, version)
        self.assertEqual(graph.analysis.candidate_pools, pools)
        self.assertFalse(self.w._shared_color_scale_box.isEnabled())

    def test_project_reopen_restores_progress_and_duplicate_is_independent(self):
        graph = self.enter()
        self.find(all_gates=False)
        gate = self.w._current_gate_index()
        self.w._workbench_add_seed(position=1.55, kind='dip')
        self.w.workbench_model.setCurrentText('Gaussian')
        self.w.workbench_filter_preset.setCurrentText('Strict')
        self.w.workbench_filter_btn.click()
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
        self.assertEqual(self.w.workbench_model.currentText(), 'Gaussian')
        self.assertEqual(self.w.workbench_scope.currentText(), 'Current Gate')
        self.w._workbench_duplicate()
        duplicate = self.session.active_graph.analysis
        self.assertIsNot(duplicate, restored)
        self.assertEqual(duplicate.seeds_by_gate, before)
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
        self.w.workbench_input.setCurrentText('Processed')
        with patch.object(self.w, '_workbench_confirm_rebind', return_value='replace'):
            self.w.workbench_bind_btn.click()
        self.assertNotEqual(graph.analysis.input_version, version)
        self.assertEqual(graph.analysis.candidate_pools, {})
        self.find()
        self.assertTrue(all(status == 'empty' for status in graph.analysis.gate_status.values()))
        self.assertEqual(source.gate_indices, [0, 2])

    def test_save_commits_pending_interval_window_and_seed_editor(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLineEdit
        from spectra_app.project_io import read_project
        graph = self.enter()
        self.find(all_gates=False)
        self.w.show()
        APP.processEvents()
        gate = self.w._current_gate_index()
        self.w.workbench_xmin.lineEdit().setText('1.11')
        self.w.workbench_sg_window.lineEdit().setText('17')
        self.w.workbench_seed_dialog.show()
        APP.processEvents()
        self.w.workbench_seeds.setCurrentCell(0, 1)
        self.w.workbench_seeds.editItem(self.w.workbench_seeds.item(0, 1))
        editor = self.w.workbench_seeds.findChild(QLineEdit)
        self.assertIsNotNone(editor)
        editor.setText('1.35123')
        self.w.save_project(self.folder / 'drafts.spectra')
        restored = read_project(self.folder / 'drafts.spectra').active.active_graph.analysis
        self.assertEqual(restored.science.analysis_controls.fit_xmin, 1.11)
        self.assertEqual(restored.science.analysis_controls.d2_window, 17)
        self.assertAlmostEqual(restored.seeds_by_gate[gate][0].position, 1.35123)

    def test_filter_preserves_manual_move_kind_and_exclusion(self):
        graph = self.enter()
        self.find(all_gates=False)
        gate = self.w._current_gate_index()
        self.w.workbench_seeds.item(0, 1).setText('1.3571')
        self.w.workbench_seeds.item(0, 2).setText('dip')
        self.w.workbench_filter_preset.setCurrentText('Strict')
        self.w.workbench_filter_btn.click()
        self.w.workbench_filter_preset.setCurrentText('Loose')
        self.w.workbench_filter_btn.click()
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
        self.w.plot_title_edit.setText('Retained analysis format')
        self.w.cmap_combo.setCurrentText('plasma')
        self.w.set_plot_presentation(replace(self.session.presentation, numeric_pt=9., text_pt=12.))
        retained = self.enter()
        self.find(all_gates=False)
        self.w.workbench_model.setCurrentText('Gaussian')
        self.w.workbench_fit_btn.click()
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
        before = deepcopy(graph.analysis.candidate_pools)
        release, started = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            started.set()
            release.wait(5)
            return collect_peak_candidates(*args, **kwargs)
        try:
            with patch('spectra_app.peak_candidates.collect_peak_candidates', side_effect=blocked):
                self.w.workbench_find_btn.click()
                self.assertTrue(started.wait(2))
                self.assertFalse(self.w.workbench_find_btn.isEnabled())
                self.w._cancel_background_job()
                release.set()
                wait_for_background_jobs(self.w, timeout=10)
        finally:
            release.set()
        self.assertEqual(graph.analysis.candidate_pools, before)
        self.assertTrue(self.w.workbench_find_btn.isEnabled())

    def test_failed_and_no_seed_gate_outcomes_are_distinct(self):
        graph = self.enter()
        self.w.workbench_scope.setCurrentText('All Gates')
        self.w.workbench_fit_btn.click()
        wait_for_background_jobs(self.w, timeout=10)
        self.assertEqual(set(graph.analysis.gate_status.values()), {'no-seed'})
        self.find()
        with patch('spectra_app.fitting.fit_multi_peak', side_effect=ValueError('controlled invalid input')):
            self.w.workbench_fit_btn.click()
            wait_for_background_jobs(self.w, timeout=10)
        self.assertEqual(set(graph.analysis.gate_status.values()), {'failed'})
        self.assertTrue(all('controlled' in message for message in graph.analysis.gate_messages.values()))


if __name__ == '__main__':
    unittest.main()
