"""One-click fit graphs are ordinary, detached, editable workspace documents."""
import json
from tests import ordinary_analysis_quick_helpers as oa
from dataclasses import replace
from tests.quick_migration_case import QuickMigrationCase
from PySide6.QtGui import QGuiApplication
from tests.xy_analysis_quick_helpers import analysis_command, choose_analysis
from unittest.mock import patch
import numpy as np
from PySide6.QtGui import QImage
from tests import test_xy_analysis_workflow as xy_workflow
APP = xy_workflow.APP
from tests.settings_helpers import wait_for_background_jobs
from tests.xy_helpers import set_xy_draft, xy_value
from tests.formal_quick_helpers import item, pump, click_item
from spectra_app.fitting import fit_multi_peak
from spectra_app.workbook_state import remove_comparison, remove_workbook, remove_sheet, resolve_curve


class FitGraphCreationTests(QuickMigrationCase):
    setUp = xy_workflow.XYAnalysisWorkflowTests.setUp
    dispose = xy_workflow.XYAnalysisWorkflowTests.dispose
    analyze = xy_workflow.XYAnalysisWorkflowTests.analyze
    fit = xy_workflow.XYAnalysisWorkflowTests.fit

    def multi_fit(self):
        curve = next(iter(self.page.comparison.curves.values()))
        source = self.w.workspace.sheets[curve.sheet_id]
        x = source.data[:, 0]
        self.component1 = 4*np.exp(-.5*((x+1.5)/.35)**2)
        self.component2 = 3*np.exp(-.5*((x-1.5)/.45)**2)
        source.data[:, 1] = 2 + self.component1 + self.component2
        source.data[len(x)//2, 1] = np.nan
        self.page.comparison.normalization = 'maxabs'
        self.page.comparison.stack_step = 9
        curve.y_offset = 4
        self.page.refresh()
        self.analyze()
        analysis_command(self.panel, 'find'); wait_for_background_jobs(self.w)
        analysis_command(self.panel, 'fit'); wait_for_background_jobs(self.w)
        self.assertIsNotNone(self.panel.fit_result)
        self.assertEqual(len(self.panel.fit_result.centers), 2)
        self.source_values = source.data[:, :2].copy()

    def create_xy(self):
        self.w.open_xy_comparison(self.page.comparison_id)
        self.w.dat_details_action.setChecked(True)
        self.w.request_task(1)
        pump()
        self.panel.sync()
        self.assertIsNotNone(item(self.w, 'xy-analysis-graph'), 'Missing Create fit graph action')
        self.assertTrue(item(self.w, 'xy-analysis-graph').isEnabled())
        click_item(self.w, 'xy-analysis-graph'); APP.processEvents()
        page = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.assertIsNot(page, self.page)
        self.assertTrue(hasattr(page, 'comparison'))
        return page

    def test_xy_button_creates_real_numeric_components_and_standard_exports(self):
        self.multi_fit()
        self.page.axes.set_xlim(-1, 1)  # Toolbar-only zoom must not crop the snapshot.
        page = self.create_xy()
        comparison = page.comparison
        curves = list(comparison.curves.values())
        self.assertEqual(len(curves), 4)
        self.assertEqual(comparison.normalization, 'none')
        self.assertEqual(comparison.stack_step, 0)
        self.assertTrue(all(curve.y_offset == 0 for curve in curves))
        self.assertEqual(comparison.x_label, 'Position')
        self.assertEqual(comparison.y_label, 'Signal')
        self.assertEqual([c.label for c in curves], ['Source data', 'Total fit', 'Component 1 + baseline', 'Component 2 + baseline'])
        sheet = self.w.workspace.sheets[curves[0].sheet_id]
        self.assertEqual(sheet.kind, 'snapshot'); self.assertIsNone(sheet.dataset_id)
        np.testing.assert_equal(sheet.data[:, :2], self.source_values)
        valid = np.isfinite(self.source_values[:, 1])
        np.testing.assert_allclose(sheet.data[valid, 2], 2+self.component1[valid]+self.component2[valid], atol=1e-6)
        np.testing.assert_allclose(sheet.data[valid, 3], 2+self.component1[valid], atol=1e-6)
        np.testing.assert_allclose(sheet.data[valid, 4], 2+self.component2[valid], atol=1e-6)
        self.assertTrue(np.isnan(sheet.data[~valid, 2:]).all())
        self.assertEqual(len(page.axes.lines), 4)
        svg = self.folder / 'fit.svg'; png = self.folder / 'fit.png'
        page.export(svg); page.export(png)
        svg_text = svg.read_text(encoding='utf-8')
        for curve in curves:
            self.assertIn(curve.curve_id, svg_text)
            self.assertIn(curve.label, svg_text)
        raster = QImage(str(png)); self.assertFalse(raster.isNull())
        # PNG must contain each ordinary curve color, not just a blank plot.
        from PIL import Image
        pixels = np.array(Image.open(png).convert('RGB'))
        for curve in curves:
            color = np.array([int(curve.color[i:i+2], 16) for i in (1, 3, 5)])
            self.assertGreater(np.count_nonzero(np.all(np.abs(pixels.astype(int)-color) < 3, axis=2)), 2)
        # Release the offscreen platform's QMimeData before QGuiApplication teardown.
        self.addCleanup(QGuiApplication.clipboard().clear)
        self.w.copy_graph_svg()
        clipboard = bytes(QGuiApplication.clipboard().mimeData().data('image/svg+xml'))
        for curve in curves: self.assertIn(curve.curve_id.encode(), clipboard)

    def test_save_create_order_reopen_styles_and_recreate_removed_records(self):
        self.multi_fit()
        initial_books = len(self.w.workspace.workbooks)
        analysis_command(self.panel, 'save')
        graph = self.create_xy()
        self.assertEqual(len(self.w.workspace.workbooks), initial_books+1)
        curve = next(iter(graph.comparison.curves.values()))
        set_xy_draft(graph, 'color', '#123456'); set_xy_draft(graph, 'width', 3.25, commit=True)
        graph.finish_editor()
        identity = graph.comparison_id
        same = self.create_xy()
        self.assertEqual(same.comparison_id, identity)
        self.assertEqual(next(iter(same.comparison.curves.values())).color, '#123456')
        self.panel.save_results()
        self.assertEqual(len(self.w.workspace.workbooks), initial_books+1)
        remove_comparison(self.w.workspace, identity)
        self.w._refresh_auxiliary_documents()
        replacement = self.create_xy()
        self.assertNotEqual(replacement.comparison_id, identity)
        self.assertEqual(len(self.w.workspace.workbooks), initial_books+1)
        sheet_id = next(iter(replacement.comparison.curves.values())).sheet_id
        remove_sheet(self.w.workspace, sheet_id)
        self.w._refresh_auxiliary_documents()
        repaired = self.create_xy()
        self.assertEqual(len(repaired.comparison.curves), 4)
        self.assertEqual(len(self.w.workspace.workbooks), initial_books+2)

    def test_generate_then_save_and_refit_make_independent_results(self):
        self.fit()
        initial_books = len(self.w.workspace.workbooks)
        first = self.create_xy()
        self.panel.save_results()
        self.assertEqual(len(self.w.workspace.workbooks), initial_books+1)
        data = resolve_curve(self.w.workspace, list(first.comparison.curves.values())[1])[1].copy()
        self.w.open_xy_comparison(self.page.comparison_id)
        analysis_command(self.panel, 'fit'); wait_for_background_jobs(self.w)
        second = self.create_xy()
        self.assertNotEqual(first.comparison_id, second.comparison_id)
        self.assertEqual(len(self.w.workspace.workbooks), initial_books+2)
        np.testing.assert_equal(resolve_curve(self.w.workspace, list(first.comparison.curves.values())[1])[1], data)

    def test_create_guards_no_fit_busy_invalid_draft_and_stale_source(self):
        self.analyze()
        self.assertIsNotNone(item(self.w, 'xy-analysis-graph'), 'Missing Create fit graph action')
        self.assertFalse(self.panel.bridge.state['graphEnabled'])
        self.fit()
        counts = (len(self.w.workspace.workbooks), len(self.w.workspace.comparisons))
        set_xy_draft(self.page, 'stack_step', 'invalid')
        analysis_command(self.panel, 'graph')
        self.assertEqual((len(self.w.workspace.workbooks), len(self.w.workspace.comparisons)), counts)
        self.assertEqual(xy_value(self.page, 'stack_step'), 'invalid')
        set_xy_draft(self.page, 'stack_step', '0')
        # Services retain the real bound busy predicate; arrange its import state.
        with patch.object(self.w, '_dat_import_pending', True):
            self.panel.sync()
            self.assertFalse(self.panel.bridge.state['graphEnabled'])
            self.panel.create_analysis_graph()
        self.assertEqual((len(self.w.workspace.workbooks), len(self.w.workspace.comparisons)), counts)
        source = self.w.workspace.sheets[next(iter(self.page.comparison.curves.values())).sheet_id]
        source.data[:, 1] += 3
        self.panel.create_analysis_graph()
        self.assertIsNone(self.panel.fit_result)
        self.assertFalse(self.panel.bridge.state['graphEnabled'])
        self.assertEqual((len(self.w.workspace.workbooks), len(self.w.workspace.comparisons)), counts)

    def test_project_reopen_retains_graph_after_all_source_records_removed(self):
        self.multi_fit()
        graph = self.create_xy()
        identity = graph.comparison_id
        sheet_id = next(iter(graph.comparison.curves.values())).sheet_id
        expected = self.w.workspace.sheets[sheet_id].data.copy()
        remove_comparison(self.w.workspace, self.page.comparison_id)
        for book in list(self.w.workspace.workbooks.values()):
            if sheet_id not in book.sheet_ids: remove_workbook(self.w.workspace, book.workbook_id)
        self.w._refresh_auxiliary_documents(); self.w._refresh_data_navigator()
        target = self.folder / 'fit.spectra'
        self.w.save_project(target); self.path.unlink()
        self.assertTrue(self.w.open_project(target))
        graph = self.w.open_xy_comparison(identity)
        self.assertEqual(len(graph.comparison.curves), 4)
        np.testing.assert_equal(self.w.workspace.sheets[sheet_id].data, expected)
        self.assertEqual(len(graph.axes.lines), 4)
        graph.export(self.folder / 'reopened.svg')

    def dat_fixture(self):
        path = self.folder / 'gates.dat'
        x = np.linspace(1, 5, 201)
        first = 2+4*np.exp(-.5*((x-2)/.2)**2)+3*np.exp(-.5*((x-4)/.3)**2)
        second = 7+8*np.exp(-.5*((x-2)/.2)**2)+6*np.exp(-.5*((x-4)/.3)**2)
        path.write_text('Energy(eV) 0 1\n'+'\n'.join(f'{a} {b} {c}' for a,b,c in zip(x, first, second)), encoding='utf-8')
        self.w._load_dat_file_direct(path)
        session = self.w.workspace.active
        self.w.open_graph_document(session.dataset_id)
        oa.reveal(self.w)
        result = fit_multi_peak(x, first, centers_hint=np.array([2., 4.]), model='Gaussian', mode='Peaks')
        session.fit_result = result; session.fit_gate_idx = 0
        self.w._set_current_gate_from_index(0)
        self.w._refresh_export_actions()
        return session, x, first, second, result

    def test_dat_uses_current_gate_fit_and_actual_applied_source_not_current_controls(self):
        session, x, first, second, result = self.dat_fixture()
        self.assertIsNotNone(item(self.w, 'ordinaryCommand_createAnalysis'))
        oa.set_value(self.w, 'fit_model', 'Lorentzian')
        oa.set_number(self.w, 'fit_xmin', 2.5); oa.set_number(self.w, 'fit_xmax', 3.5)
        oa.set_value(self.w, 'fit_smooth', True)
        oa.click(self.w, 'createAnalysis'); APP.processEvents()
        graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.assertEqual(len(graph.comparison.curves), 4)
        sheet = self.w.workspace.sheets[next(iter(graph.comparison.curves.values())).sheet_id]
        np.testing.assert_allclose(sheet.data[:, 1], first)
        np.testing.assert_allclose(sheet.data[:, 3], 2+4*np.exp(-.5*((x-2)/.2)**2), atol=1e-6)
        note = json.loads(sheet.snapshot_note)
        self.assertEqual(note['model'], 'Gaussian')
        self.assertEqual(note['actual_interval'], [1., 5.])
        self.assertEqual(note['gate_index'], 0)
        self.assertIn('before fit-only smoothing', note['source_values'])
        self.assertEqual(next(iter(graph.comparison.curves.values())).label, 'Source data (applied)')
        self.w.open_graph_document(session.dataset_id)
        oa.reveal(self.w)
        self.w._set_current_gate_from_index(1)
        self.w._refresh_export_actions()
        self.assertFalse(oa.command_enabled(self.w, 'createAnalysis'))
        # Retained batch fit for Gate 1 is used even when the single-fit field is Gate 0.
        session.batch_fit_result_map[1] = fit_multi_peak(x, second, centers_hint=np.array([2., 4.]), model='Gaussian', mode='Peaks')
        self.w._refresh_export_actions(); self.assertTrue(oa.command_enabled(self.w, 'createAnalysis'))
        oa.click(self.w, 'createAnalysis'); APP.processEvents()
        current = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.assertNotEqual(current.comparison_id, graph.comparison_id)
        selected_sheet = self.w.workspace.sheets[next(iter(current.comparison.curves.values())).sheet_id]
        np.testing.assert_allclose(selected_sheet.data[:, 1], second)
        self.assertEqual(json.loads(selected_sheet.snapshot_note)['gate_index'], 1)
        set_xy_draft(current, 'color', '#abcdef'); current.finish_editor()
        self.w.open_graph_document(session.dataset_id)
        oa.click(self.w, 'createAnalysis')
        self.assertEqual(len(self.w.workspace.comparisons), 3)
        self.assertEqual(next(iter(current.comparison.curves.values())).color, '#abcdef')

    def test_dat_busy_invalid_draft_mismatched_coordinates_and_no_fit_do_not_create(self):
        session, x, first, second, result = self.dat_fixture()
        self.assertIsNotNone(item(self.w, 'ordinaryCommand_createAnalysis'))
        before = (len(self.w.workspace.workbooks), len(self.w.workspace.comparisons))
        with patch.object(self.w, '_project_busy', return_value=True):
            self.w._refresh_export_actions()
            self.assertFalse(oa.command_enabled(self.w, 'createAnalysis'))
        self.w._refresh_export_actions()
        self.assertTrue(oa.command_enabled(self.w, 'createAnalysis'))
        oa.draft(self.w, 'fit_xmin', 'invalid')
        oa.click(self.w, 'createAnalysis')
        self.assertIn('numeric editor', self.w.ordinary_analysis.errorText)
        pump(80)
        self.assertTrue(oa.control(self.w, 'fit_xmin').hasActiveFocus())
        self.assertEqual((len(self.w.workspace.workbooks), len(self.w.workspace.comparisons)), before)
        oa.draft(self.w, 'fit_xmin', '1.0')
        oa.commit(self.w)
        session.fit_result = replace(result, x=result.x+.001)
        with patch.object(self.w, '_show_error') as error:
            self.w._on_create_analysis_graph()
            self.assertIn('align', error.call_args.args[0])
        self.assertEqual((len(self.w.workspace.workbooks), len(self.w.workspace.comparisons)), before)
        session.fit_result = None
        self.w._refresh_export_actions()
        self.assertFalse(oa.command_enabled(self.w, 'createAnalysis'))

    def test_open_failure_rolls_back_new_entities_and_preserves_saved_results(self):
        self.fit()
        for saved_first in (False, True):
            if saved_first: analysis_command(self.panel, 'save')
            self.w.open_xy_comparison(self.page.comparison_id)
            before = (set(self.w.workspace.workbooks), set(self.w.workspace.sheets),
                      set(self.w.workspace.comparisons))
            with patch.object(self.w, 'open_xy_comparison', side_effect=ValueError('Cannot open test graph')):
                analysis_command(self.panel, 'graph')
            self.assertEqual((set(self.w.workspace.workbooks), set(self.w.workspace.sheets),
                              set(self.w.workspace.comparisons)), before)
            self.assertIn('Cannot open test graph', self.panel.bridge.state['status'])
            self.assertTrue(self.panel.bridge.state['graphEnabled'])
        graph = self.create_xy()
        self.assertEqual(len(graph.comparison.curves), 3)

    def test_saved_sheet_rename_and_default_roles_do_not_change_fit_graph_mapping(self):
        self.multi_fit()
        analysis_command(self.panel, 'save')
        book_id, sheet_ids = self.panel._result_ids
        sheet = self.w.workspace.sheets[sheet_ids[-1]]
        sheet.name = 'Renamed fit table'
        sheet.x_column = 1
        sheet.y_columns = [2]
        self.w.workspace.workbooks[book_id].sheet_ids.reverse()
        self.w._refresh_auxiliary_documents()
        graph = self.create_xy()
        self.assertEqual(len(graph.comparison.curves), 4)
        self.assertEqual([curve.x_column for curve in graph.comparison.curves.values()], [0, 0, 0, 0])
        self.assertEqual([curve.y_column for curve in graph.comparison.curves.values()], [1, 2, 3, 4])
        self.assertTrue(all(curve.sheet_id == sheet.sheet_id for curve in graph.comparison.curves.values()))

    def test_xy_find_only_creates_peak_and_dip_markers_without_fit(self):
        curve = next(iter(self.page.comparison.curves.values()))
        source = self.w.workspace.sheets[curve.sheet_id]
        x = source.data[:, 0]
        source.data[:, 1] = 2+4*np.exp(-.5*((x+1.52)/.35)**2)-3*np.exp(-.5*((x-1.52)/.45)**2)
        expected = source.data[:, :2].copy()
        self.analyze();choose_analysis(self.panel, 'mode', 'Both')
        analysis_command(self.panel, 'find');wait_for_background_jobs(self.w)
        self.assertIsNone(self.panel.fit_result)
        np.testing.assert_equal(self.panel.peaks.indices, [112, 188])
        self.assertTrue(self.panel.bridge.state['graphEnabled'], 'Find-only results must be graphable')
        analysis_command(self.panel, 'save')
        graph = self.create_xy()
        self.assertEqual([c.label for c in graph.comparison.curves.values()], ['Source data', 'Peaks', 'Dips'])
        curves = list(graph.comparison.curves.values())
        self.assertEqual([c.style for c in curves], ['line', 'scatter', 'scatter'])
        sheet = self.w.workspace.sheets[curves[0].sheet_id]
        np.testing.assert_equal(sheet.data[:, :2], expected)
        np.testing.assert_equal(np.flatnonzero(np.isfinite(sheet.data[:, 2])), [112])
        np.testing.assert_equal(np.flatnonzero(np.isfinite(sheet.data[:, 3])), [188])
        np.testing.assert_allclose(sheet.data[112, 2], 6., atol=1e-8)
        np.testing.assert_allclose(sheet.data[188, 3], -1., atol=1e-8)
        self.assertEqual(len(self.w.workspace.workbooks), 2)
        graph.export(self.folder/'markers.svg');graph.export(self.folder/'markers.png')
        for curve in curves:self.assertIn(curve.curve_id.encode(), (self.folder/'markers.svg').read_bytes())
        same=self.create_xy();self.assertEqual(same.comparison_id, graph.comparison_id)
        remove_comparison(self.w.workspace, self.page.comparison_id)
        source_book=next(book for book in self.w.workspace.workbooks.values() if source.sheet_id in book.sheet_ids)
        remove_workbook(self.w.workspace, source_book.workbook_id)
        self.w._refresh_auxiliary_documents();self.w._refresh_data_navigator()
        target=self.folder/'markers.spectra';self.w.save_project(target);self.path.unlink()
        self.assertTrue(self.w.open_project(target))
        restored=self.w.open_xy_comparison(graph.comparison_id)
        self.assertEqual(len(restored.axes.lines),3)
        np.testing.assert_equal(self.w.workspace.sheets[sheet.sheet_id].data, sheet.data)

    def test_dat_find_only_uses_current_gate_and_new_cube_source(self):
        path=self.folder/'peak-dip.dat'
        x=np.linspace(-6,6,301)
        y=2+4*np.exp(-.5*((x+1.52)/.35)**2)-3*np.exp(-.5*((x-1.52)/.45)**2)
        path.write_text('Energy(eV) 0 1\n'+'\n'.join(f'{a} {b} 2' for a,b in zip(x,y)),encoding='utf-8')
        self.w._load_dat_file_direct(path)
        session=self.w.workspace.active
        self.w.open_graph_document(session.dataset_id)
        oa.reveal(self.w)
        self.w._set_current_gate_from_index(0)
        oa.set_value(self.w, 'peak_mode', 'Both')
        oa.click(self.w, 'find')
        self.assertIsNone(self.w._active_fit_result())
        np.testing.assert_equal(self.w._current_trace_peak_indices(),[112,188])
        self.assertTrue(oa.command_enabled(self.w, 'createAnalysis'), 'DAT Find-only results must be graphable')
        oa.click(self.w, 'createAnalysis');APP.processEvents()
        graph=self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        curves=list(graph.comparison.curves.values())
        self.assertEqual([c.label for c in curves], ['Source data (applied)', 'Peaks', 'Dips'])
        sheet=self.w.workspace.sheets[curves[0].sheet_id]
        np.testing.assert_allclose(sheet.data[:,1],y)
        np.testing.assert_equal(np.flatnonzero(np.isfinite(sheet.data[:,2])),[112])
        np.testing.assert_equal(np.flatnonzero(np.isfinite(sheet.data[:,3])),[188])
        self.w.open_graph_document(session.dataset_id)
        oa.reveal(self.w)
        self.w._set_current_gate_from_index(1)
        self.assertFalse(oa.command_enabled(self.w, 'createAnalysis'))
        oa.click(self.w, 'find');self.assertFalse(oa.command_enabled(self.w, 'createAnalysis'))
        self.w._set_current_gate_from_index(0)
        session.current_cube=replace(session.current_cube,z=session.current_cube.z+10)
        self.w._refresh_export_actions();oa.click(self.w, 'createAnalysis')
        changed=self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.assertNotEqual(changed.comparison_id,graph.comparison_id)
        changed_sheet=self.w.workspace.sheets[next(iter(changed.comparison.curves.values())).sheet_id]
        np.testing.assert_allclose(changed_sheet.data[:,1],y+10)
        np.testing.assert_allclose(sheet.data[:,1],y)

    def test_dat_find_retires_fit_for_replaced_seeds(self):
        session, x, first, second, result = self.dat_fixture()
        session.batch_fit_result_map[0]=result
        session.batch_fit_result_map[1]=result
        oa.click(self.w, 'find')
        self.assertTrue(self.w._active_fit_result() is None, 'New detected seeds must not publish an older retained fit')
        self.assertIs(session.batch_fit_result_map[1],result)

    def test_batch_find_retires_prior_fits_even_with_identical_detected_seeds(self):
        session, x, first, second, result = self.dat_fixture()
        session.batch_peak_map={0:np.array([50,150]),1:np.array([50,150])}
        session.batch_fit_result_map={0:result,1:result}
        session.peak_indices=np.array([50,150]);session.peak_gate_idx=0
        oa.click(self.w, 'batchFind');wait_for_background_jobs(self.w)
        np.testing.assert_equal(session.batch_peak_map[0],[50,150])
        self.assertTrue(self.w._active_fit_result() is None)
        self.assertEqual(session.batch_fit_result_map,{})
