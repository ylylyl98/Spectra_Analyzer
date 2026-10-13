"""Real Qt graph ownership, async publication, overlays and detached snapshots."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
from threading import Event
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from spectra_app.main_window import MainWindow
from spectra_app.project_io import read_project, write_project
from tests.settings_helpers import isolate_settings, wait_for_background_jobs

APP = QApplication.instance() or QApplication([])

class XYAnalysisWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.errors = []
        for method in ('warning', 'critical'):
            trap = patch.object(QMessageBox, method, lambda *args: (self.errors.append(args[2]), QMessageBox.Cancel)[1])
            trap.start(); self.addCleanup(trap.stop)
        trap = patch.object(sys, 'excepthook', lambda *args: self.errors.append(str(args)))
        trap.start(); self.addCleanup(trap.stop)
        self.w = MainWindow()
        self.addCleanup(self.dispose)
        self.w.resize(1500, 920); self.w.show()
        self.path = self.folder / 'curve.csv'
        x = np.linspace(-6, 6, 301)
        np.savetxt(self.path, np.column_stack([x, 2+5*np.exp(-.5*((x-.7)/.6)**2), 4+2*np.exp(-.5*((x+1)/.8)**2)]),
                   delimiter=',', header='Position,Signal,Reference', comments='')
        self.w.import_xy_files([self.path], mappings=[(0, [1, 2])], compare=True)
        self.page = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        self.assertTrue(hasattr(self.page, 'analysis_panel'), 'XY graph needs contextual analysis')
        self.panel = self.page.analysis_panel
        self.page.curve_list.clearSelection()
        self.page.curve_list.item(0).setSelected(True)
        APP.processEvents()

    def dispose(self):
        self.w._cancel_background_job()
        wait_for_background_jobs(self.w)
        QTest.keyRelease(self.w, Qt.Key_Control); QTest.keyRelease(self.w, Qt.Key_Shift)
        self.w.close(); self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete); APP.processEvents()
        self.assertEqual(self.errors, [])

    def analyze(self):
        self.page.analyze_btn.click(); APP.processEvents()
        self.assertTrue(self.panel.isVisible())
        self.assertIs(self.w.plot_tabs.currentWidget(), self.w.graph_documents)

    def fit(self):
        self.analyze()
        self.panel.find_btn.click(); wait_for_background_jobs(self.w)
        self.assertEqual(len(self.panel.peaks.indices), 1)
        self.panel.fit_btn.click(); wait_for_background_jobs(self.w)
        self.assertIsNotNone(self.panel.fit_result)
        np.testing.assert_allclose(self.panel.fit_result.centers, [.7], atol=1e-5)

    def test_toolbar_menu_and_project_route_xy_with_stale_dat(self):
        dat = self.folder / 'other.dat'
        dat.write_text('Energy(eV) 0 1\n1 2 3\n2 3 4\n3 4 5\n4 5 6\n5 6 7\n')
        self.w._load_dat_file_direct(dat)
        active = self.w.workspace.active
        self.page.analyze_btn.click()
        self.assertTrue(self.panel.isVisible())
        self.assertIs(self.w.workspace.active, active)
        self.assertIs(self.w.plot_tabs.currentWidget(), self.w.graph_documents)
        self.panel.hide()
        self.w.analyze_action.trigger()
        self.assertTrue(self.panel.isVisible())
        self.panel.hide()
        self.w.open_graph_document(active.dataset_id)
        tree = self.w.data_navigator
        tree.clearSelection()
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.data(0, Qt.UserRole) == ('xy', self.page.comparison_id):
                tree.setCurrentItem(item); item.setSelected(True); break
        self.w._refresh_navigator_actions()
        self.assertTrue(self.w.project_analyze_action.isEnabled())
        self.w.project_analyze_action.trigger()
        self.assertTrue(self.panel.isVisible())
        self.assertIs(self.w.plot_tabs.currentWidget(), self.w.graph_documents)
        self.assertIn('Position', self.panel.context_label.text())
        self.assertIn('Signal', self.panel.context_label.text())

    def test_exactly_one_selection_and_invalid_drafts(self):
        self.page.curve_list.clearSelection()
        self.analyze()
        self.assertFalse(self.panel.find_btn.isEnabled())
        self.assertIn('exactly one', self.panel.context_label.text())
        for i in range(2): self.page.curve_list.item(i).setSelected(True)
        self.assertFalse(self.panel.find_btn.isEnabled())
        self.page.curve_list.item(1).setSelected(False)
        self.assertTrue(self.panel.find_btn.isEnabled())
        self.page.stack_step_edit.setText('bad offset')
        self.panel.find_btn.click()
        self.assertFalse(self.w._background_jobs.busy)
        self.assertEqual(self.page.stack_step_edit.text(), 'bad offset')
        self.assertIn('finite', self.panel.status.text())
        self.page.stack_step_edit.setText('1.5')
        self.panel.find_btn.click(); wait_for_background_jobs(self.w)
        self.assertEqual(self.page.comparison.stack_step, 1.5)
        self.assertEqual(self.panel.peaks.indices.size, 1)

    def test_fit_overlay_reuses_reference_and_exports_exclude_transients(self):
        self.fit()
        captured_fit = self.panel.fit_result
        self.page.comparison.normalization = 'minmax'
        self.page.comparison.stack_step = 3
        curve = next(iter(self.page.comparison.curves.values()))
        curve.y_offset = .25
        self.page.render()
        line = next(line for line in self.page.axes.lines if line.get_gid() == 'xy-analysis-fit')
        y = self.panel.captured.data.y[self.panel.captured.data.valid]
        np.testing.assert_allclose(line.get_ydata()[np.isfinite(line.get_ydata())],
                                   (captured_fit.y_fit-y.min())/(y.max()-y.min())+.25)
        self.page.refresh()
        self.assertNotIn(b'xy-analysis-fit', self.page.svg_bytes())
        self.page.comparison.x_range_mode = 'manual'; self.page.comparison.x_range = (-2, 2)
        self.page.refresh()
        self.assertIsNone(self.panel.fit_result)
        self.assertFalse(self.panel.save_btn.isEnabled())

    def test_saved_results_survive_removing_sources_and_project_reopen(self):
        self.fit()
        count = len(self.w.workspace.workbooks)
        self.panel.save_btn.click()
        self.assertEqual(len(self.w.workspace.workbooks), count+1)
        self.assertFalse(self.panel.save_btn.isEnabled())
        self.panel.save_btn.click()
        self.assertEqual(len(self.w.workspace.workbooks), count+1)
        snapshots = [sheet for sheet in self.w.workspace.sheets.values() if sheet.kind == 'snapshot']
        self.assertEqual(len(snapshots), 3)
        for sheet in snapshots:
            self.assertTrue(sheet.snapshot_note); self.assertGreater(sheet.data.shape[0], 0)
        actual = self.folder / 'actual.spectra'
        self.w.save_project(actual)
        self.path.unlink()
        self.assertTrue(self.w.open_project(actual))
        restored_params = next(sheet for sheet in self.w.workspace.sheets.values() if sheet.name == 'Fit parameters')
        np.testing.assert_allclose(restored_params.data[0, :4], [.7, 5, .6, 2], atol=1e-5)
        self.assertIsNone(next(page.analysis_panel for key in self.w.graph_documents.keys()
                               if (page := self.w.graph_documents.page_for(key)) is not None
                               and hasattr(page, 'analysis_panel')).fit_result)
        from spectra_app.workbook_state import remove_workbook, remove_comparison
        for comparison_id in list(self.w.workspace.comparisons):
            remove_comparison(self.w.workspace, comparison_id)
        for workbook in list(self.w.workspace.workbooks.values()):
            if any(self.w.workspace.sheets[sid].kind != 'snapshot' for sid in workbook.sheet_ids):
                remove_workbook(self.w.workspace, workbook.workbook_id)
        self.w._refresh_auxiliary_documents()
        self.w._refresh_data_navigator()
        self.w.save_project(actual)
        self.assertTrue(self.w.open_project(actual))
        self.assertEqual(len(self.w.workspace.sheets), 3)
        self.assertTrue(all(sheet.kind == 'snapshot' for sheet in self.w.workspace.sheets.values()))
        # Serialize a detached workspace with only these saved records.
        from spectra_app.dataset_state import Workspace
        result = Workspace()
        result.sheets = {sheet.sheet_id: sheet for sheet in snapshots}
        book = next(book for book in self.w.workspace.workbooks.values() if set(book.sheet_ids) == set(result.sheets))
        result.workbooks = {book.workbook_id: book}
        target = self.folder / 'results.spectra'
        write_project(target, result)
        restored = read_project(target)
        params = next(sheet for sheet in restored.sheets.values() if sheet.name == 'Fit parameters')
        np.testing.assert_allclose(params.data[0, :4], [.7, 5, .6, 2], atol=1e-5)
        self.assertEqual(params.text_columns[6], ['Gaussian'])

    def test_selection_change_suppresses_late_result_and_error(self):
        self.analyze()
        for fail in (False, True):
            self.page.curve_list.clearSelection(); self.page.curve_list.item(0).setSelected(True)
            started, release = Event(), Event()
            import spectra_app.xy_analysis_panel as module
            real = module.detect_peaks
            def delayed(*args):
                started.set(); release.wait(5)
                if fail: raise ValueError('obsolete error')
                return real(*args)
            with patch.object(module, 'detect_peaks', delayed):
                self.panel.find_btn.click()
                self.assertTrue(started.wait(2))
                self.page.curve_list.clearSelection(); self.page.curve_list.item(1).setSelected(True)
                release.set(); wait_for_background_jobs(self.w)
            self.assertIsNone(self.panel.peaks)
            self.assertNotIn('obsolete error', self.panel.status.text())
            self.assertFalse(self.panel.busy)

    def test_cancel_clears_local_busy_when_callback_is_suppressed(self):
        self.analyze()
        started, release = Event(), Event()
        import spectra_app.xy_analysis_panel as module
        real = module.detect_peaks
        def delayed(*args):
            started.set(); release.wait(5); return real(*args)
        with patch.object(module, 'detect_peaks', delayed):
            self.panel.find_btn.click(); self.assertTrue(started.wait(2))
            self.w._cancel_background_job()
            self.assertFalse(self.panel.busy)
            release.set(); wait_for_background_jobs(self.w)
        self.assertIsNone(self.panel.peaks)
        self.assertTrue(self.panel.find_btn.isEnabled())

    def test_hidden_preceding_curve_keeps_nonzero_fit_stack_offset(self):
        self.page.curve_list.setCurrentRow(1)
        self.page.curve_list.clearSelection(); self.page.curve_list.item(1).setSelected(True)
        curves = list(self.page.comparison.curves.values())
        curves[0].visible = False
        self.page.comparison.normalization = 'maxabs'
        self.page.comparison.stack_step = 3
        curves[1].y_offset = -.5
        self.page.refresh()
        self.analyze()
        self.panel.find_btn.click(); wait_for_background_jobs(self.w)
        self.panel.fit_btn.click(); wait_for_background_jobs(self.w)
        np.testing.assert_allclose(self.panel.fit_result.centers, [-1], atol=1e-5)
        line = next(line for line in self.page.axes.lines if line.get_gid() == 'xy-analysis-fit')
        reference = self.panel.captured.data.y[self.panel.captured.data.valid]
        np.testing.assert_allclose(line.get_ydata(), self.panel.fit_result.y_fit / np.max(np.abs(reference)) + 2.5)

    def test_source_mutation_suppresses_late_result_and_error(self):
        self.analyze()
        source = self.w.workspace.sheets[next(iter(self.page.comparison.curves.values())).sheet_id]
        for fail in (False, True):
            started, release = Event(), Event()
            import spectra_app.xy_analysis_panel as module
            real = module.detect_peaks
            def delayed(*args):
                started.set(); release.wait(5)
                if fail: raise ValueError('obsolete source error')
                return real(*args)
            with patch.object(module, 'detect_peaks', delayed):
                self.panel.find_btn.click(); self.assertTrue(started.wait(2))
                source.data[:, 1] += 1
                self.page.render()
                release.set(); wait_for_background_jobs(self.w)
            self.assertIsNone(self.panel.peaks)
            self.assertNotIn('obsolete source error', self.panel.status.text())
            self.assertFalse(self.panel.busy)

    def test_invalid_entry_draft_is_retained_and_no_peak_save_is_disabled(self):
        self.page.stack_step_edit.setText('invalid')
        self.page.analyze_btn.click()
        self.assertTrue(self.panel.isHidden())
        self.assertEqual(self.page.stack_step_edit.text(), 'invalid')
        self.page.stack_step_edit.setText('0')
        self.analyze()
        source = self.w.workspace.sheets[next(iter(self.page.comparison.curves.values())).sheet_id]
        source.data[:, 1] = 2
        self.page.render()
        self.panel.find_btn.click(); wait_for_background_jobs(self.w)
        self.assertEqual(self.panel.peaks.indices.size, 0)
        self.assertFalse(self.panel.fit_btn.isEnabled())
        self.assertFalse(self.panel.save_btn.isEnabled())
        self.assertIn('No peaks', self.panel.status.text())

    def test_common_and_manual_interval_use_scientific_domain_not_toolbar_zoom(self):
        from spectra_app.workbook_state import register_xy_sheet, add_curve, create_workbook
        book = create_workbook(self.w.workspace)
        source = register_xy_sheet(self.w.workspace, book.workbook_id, 'Restricted reference',
            np.array([[-2., 0], [-1, 1], [0, 2], [1, 1], [2, 0]]), ['Coordinate', 'Reference'])
        add_curve(self.w.workspace, self.page.comparison_id, source.sheet_id, 0, 1)
        self.page.comparison.x_range_mode = 'common'
        self.page.refresh()
        self.analyze()
        self.page.axes.set_xlim(.6, .8)
        self.panel.find_btn.click(); wait_for_background_jobs(self.w)
        self.assertEqual(self.panel.captured.provenance['interval'], (-2, 2))
        np.testing.assert_allclose(self.panel.captured.data.x[[0, -1]], [-2, 2])
        self.panel.fit_btn.click(); wait_for_background_jobs(self.w)
        self.assertIsNotNone(self.panel.fit_result)
        self.panel.width_min_spin.setValue(.01)
        self.assertIsNone(self.panel.fit_result)
        self.assertEqual(self.panel.peaks.indices.size, 1)
        self.assertTrue(self.panel.save_btn.isEnabled())
        self.page.comparison.x_range_mode = 'manual'
        self.page.comparison.x_range = (-1, 1)
        self.page.refresh()
        self.panel.find_btn.click(); wait_for_background_jobs(self.w)
        self.assertEqual(self.panel.captured.provenance['interval'], (-1, 1))
        np.testing.assert_allclose(self.panel.captured.data.x[[0, -1]], [-1, 1])


    def test_unopened_analysis_does_not_capture_or_copy_numeric_sources(self):
        with patch('spectra_app.xy_analysis_panel.capture_input', side_effect=AssertionError('Dormant analysis copied data')):
            self.page.render()
            self.w._refresh_graph_commands()

    def test_raw_processed_dat_and_snapshot_curves_share_xy_analysis_without_dat_fit_state(self):
        from spectra_app.workbook_state import add_curve, save_sheet_snapshot, ensure_dataset_sheet
        from dataclasses import replace
        original = next(iter(self.page.comparison.curves.values()))
        snapshot = save_sheet_snapshot(self.w.workspace, original.sheet_id, 'Frozen source')
        snapshot_curve = add_curve(self.w.workspace, self.page.comparison_id, snapshot.sheet_id, 0, 1)
        dat = self.folder / 'spectrum.dat'
        x = np.linspace(-6, 6, 301)
        y = 2 + 5*np.exp(-.5*((x-.7)/.6)**2)
        dat.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{a} {b} {b*2}' for a,b in zip(x,y)))
        self.w._load_dat_file_direct(dat)
        session = self.w.workspace.active
        sheet = next(sheet for sheet in self.w.workspace.sheets.values() if sheet.dataset_id == session.dataset_id)
        dat_curve = add_curve(self.w.workspace, self.page.comparison_id, sheet.sheet_id, 0, 1)
        session.current_cube = replace(session.base_cube, z=session.base_cube.z + 10)
        processed = ensure_dataset_sheet(self.w.workspace, session.dataset_id, 'processed')
        processed_curve = add_curve(self.w.workspace, self.page.comparison_id, processed.sheet_id, 0, 1)
        self.page.refresh()
        self.w.open_xy_comparison(self.page.comparison_id)
        for target, expected_baseline, expected_mode in ((snapshot_curve, 2, 'raw'), (dat_curve, 2, 'raw'), (processed_curve, 12, 'processed')):
            self.page.curve_list.clearSelection()
            for row in range(self.page.curve_list.count()):
                item = self.page.curve_list.item(row)
                if item.data(Qt.UserRole) == target.curve_id:
                    self.page.curve_list.setCurrentItem(item); item.setSelected(True)
            self.analyze()
            self.panel.find_btn.click(); wait_for_background_jobs(self.w)
            self.panel.fit_btn.click(); wait_for_background_jobs(self.w)
            np.testing.assert_allclose(self.panel.fit_result.centers, [.7], atol=1e-5)
            self.assertAlmostEqual(self.panel.fit_result.baseline, expected_baseline, places=5)
            self.assertEqual(self.panel.captured.provenance['source_mode'], expected_mode)
            self.assertIsNone(session.fit_result)

    def test_all_xy_graph_styles_offer_owner_routed_curve_analysis(self):
        curve = next(iter(self.page.comparison.curves.values()))
        for style in ('line', 'scatter', 'line_symbol'):
            self.panel.hide()
            self.page.style_buttons[style].click()
            self.assertEqual(curve.style, style)
            self.analyze()
            self.assertEqual(self.panel.current_input().curve_id, curve.curve_id)
            self.panel.find_btn.click(); wait_for_background_jobs(self.w)
            self.panel.fit_btn.click(); wait_for_background_jobs(self.w)
            np.testing.assert_allclose(self.panel.fit_result.centers, [.7], atol=1e-5)
