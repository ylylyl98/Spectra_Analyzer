import json
import os
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from spectra_app import workbook_state as state
from spectra_app.project_io import read_project, write_project, ProjectFormatError
from tests.test_project_io import rich_workspace, archive_members, overwrite_archive
from PySide6.QtCore import QCoreApplication, Qt
from spectra_app.common_prompts import PromptAction
from spectra_app.xy_import_dialog import XYImportDialog
from tests.quick_migration_case import QuickMigrationCase
from tests.background_helpers import wait_jobs
from tests.scientific_fixtures import workflow_fit_result as _fit_result

from tests.xy_helpers import set_xy_draft, xy_value
from tests.workbook_quick_helpers import item


class OrganizationUiTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.errors = self.quick_errors

    def tearDown(self):
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def test_actual_import_defaults_per_file_even_with_selected_owner(self):
        paths = []
        for name, data in [('first.csv', 'signal,time\n10,1\n20,2\n'), ('second.csv', 'time,signal\n3,30\n4,40\n5,50\n')]:
            path = self.folder / name
            path.write_text(data)
            paths.append(path)
        existing = state.create_workbook(self.window.workspace, 'Selected')
        self.window._refresh_data_navigator()
        self.window.project.select_key(('workbook', existing.workbook_id))
        def accept(dialog):
            self.assertEqual(dialog.destination, 'per_file')
            dialog.mappings = [(1, [0]), (0, [1])]
            dialog.compare = True
            return XYImportDialog.Accepted
        with patch('spectra_app.main_window.FilePicker.getOpenFileNames', return_value=([str(p) for p in paths], '')), patch.object(XYImportDialog, 'exec', accept):
            self.window._on_import_xy()
            wait_jobs(self.window)
        self.assertFalse(self.errors)
        self.assertEqual(existing.sheet_ids, [])
        self.assertEqual([b.name for b in self.window.workspace.workbooks.values()], ['Selected', 'first.csv', 'second.csv'])
        graph = next(iter(self.window.workspace.comparisons.values()))
        self.assertEqual(len(graph.curves), 2)
        for curve, values in zip(graph.curves.values(), ([10, 20], [30, 40, 50])):
            np.testing.assert_equal(state.resolve_curve(self.window.workspace, curve)[1], values)

    def test_snapshot_button_text_copy_merge_action_and_clear(self):
        workspace = rich_workspace(self.folder)
        self.window.workspace = workspace
        workspace.active_dataset_id = 'owner-0'
        self.window._sync_result_sheets('owner-0')
        source = next(s for s in workspace.sheets.values() if s.dataset_id == 'owner-0' and s.role == 'fit_parameters')
        owner = self.window._sheet_owner(source.sheet_id)
        page = self.window.open_workbook(owner.workbook_id, source.sheet_id).sheet_pages[source.sheet_id]
        self.assertEqual(page.model.data(page.model.index(0, 1)), 'Gaussian')
        self.assertFalse(page.columnChoices[1]['numeric'])
        page.selectAll()
        self.assertIn('\tGaussian\tok\t', page.copy_selection())
        with patch('spectra_app.main_window.prompt_text', return_value=('', False)):
            page.snapshot()
        self.assertFalse(any(s.role == 'snapshot' for s in workspace.sheets.values()))
        with patch('spectra_app.main_window.prompt_text', return_value=('Frozen parameters', True)):
            page.snapshot()
        snapshot = next(s for s in workspace.sheets.values() if s.role == 'snapshot')
        values, note = snapshot.data.copy(), snapshot.snapshot_note
        books = list(workspace.workbooks)
        self.window.merge_workbooks(books, books[1])
        self.assertEqual(list(workspace.workbooks), [books[1]])
        self.assertIn(snapshot.sheet_id, workspace.workbooks[books[1]].sheet_ids)
        page = self.window.open_workbook(books[1], source.sheet_id).sheet_pages[source.sheet_id]
        self.window._on_clear_fit()
        self.assertEqual(page.model.rowCount(), 0)
        self.assertFalse(item(page.parent(), 'workbookSnapshot').isEnabled())
        np.testing.assert_equal(snapshot.data, values)
        self.assertEqual(snapshot.snapshot_note, note)
        self.assertFalse(self.errors)

    def test_source_removal_refresh_has_no_qt_callback_error_and_retains_snapshot(self):
        w = self.window
        path = self.folder / 'source.dat'
        path.write_text('Energy(eV) 0 1\n1 2 3\n2 4 5\n3 6 7\n')
        w._load_dat_file_direct(path)
        ws = w.workspace
        session = ws.active
        session.batch_fit_result_map[0] = _fit_result(10)
        w._sync_result_sheets()
        original = state.ensure_dataset_sheet(ws, session.dataset_id)
        owner = w._sheet_owner(original.sheet_id)
        snapshot = state.save_sheet_snapshot(ws, original.sheet_id, 'Independent')
        unrelated = state.create_workbook(ws, 'Destination')
        other = state.register_xy_sheet(ws, unrelated.workbook_id, 'Other source', np.array([[1., 2.]]), ['X', 'Y'])
        w.open_workbook(owner.workbook_id)
        target_page = w.open_workbook(unrelated.workbook_id)
        w.merge_workbooks([owner.workbook_id, unrelated.workbook_id], unrelated.workbook_id)
        comparison = state.create_comparison(ws)
        state.add_curve(ws, comparison.comparison_id, original.sheet_id, 0, 1)
        retained = state.add_curve(ws, comparison.comparison_id, snapshot.sheet_id, 0, 1)
        graph = w.open_xy_comparison(comparison.comparison_id)
        set_xy_draft(graph, 'name', 'Pending graph name')
        # Invalid draft must survive source retirement without a stale curve row.
        set_xy_draft(graph, 'color', 'bad')
        errors = []
        with patch.object(sys, 'excepthook', side_effect=lambda *args: errors.append(args)), patch('spectra_app.main_window.prompt_message', return_value=PromptAction.YES):
            self.assertTrue(w.remove_dataset(session.dataset_id))
            QCoreApplication.processEvents()
        self.assertEqual(errors, [])
        self.assertEqual(set(target_page.sheet_pages), {snapshot.sheet_id, other.sheet_id})
        self.assertEqual(graph.inspector_bridge.curves.rowCount(), 1)
        self.assertEqual(xy_value(graph, 'name'), 'Pending graph name')
        self.assertEqual(list(comparison.curves), [retained.curve_id])
        self.assertEqual(state.resolve_curve(ws, retained)[1].tolist(), [2, 4, 6])
        self.assertFalse(self.errors)

    def test_fit_callbacks_processing_invalidation_and_refit_refresh_open_documents(self):
        w = self.window
        path = self.folder / 'analysis.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(f'{i / 5} 1 2 3' for i in range(101)))
        w._load_dat_file_direct(path)
        w._set_current_gate_from_index(0, update_plot=True)
        w.peak_indices = np.array([50])
        w._peak_gate_idx = 0
        w.batch_peak_map = {0: np.array([50]), 1: np.array([50])}
        first = replace(_fit_result(10.1234567890123), baseline=.234567890123)
        with patch('spectra_app.main_window.fit_multi_peak', return_value=first):
            w._on_fit()
            wait_jobs(w)
        ws = w.workspace
        source = next(s for s in ws.sheets.values() if s.role == 'fit_curves')
        owner = w._sheet_owner(source.sheet_id)
        page = w.open_workbook(owner.workbook_id, source.sheet_id).sheet_pages[source.sheet_id]
        self.assertEqual(page.model.rowCount(), 21)
        comparison = state.create_comparison(ws)
        curve = state.add_curve(ws, comparison.comparison_id, source.sheet_id, 0, 1)
        graph = w.open_xy_comparison(comparison.comparison_id)
        set_xy_draft(graph, 'name', 'Uncommitted graph name')
        set_xy_draft(graph, 'color', 'invalid draft')
        w._invalidate_peak_fits(ws.active, {0})
        self.assertEqual(page.model.rowCount(), 0)
        self.assertEqual(len(graph.axes.lines), 0)
        self.assertEqual(xy_value(graph, 'name'), 'Uncommitted graph name')
        self.assertEqual(xy_value(graph, 'color'), 'invalid draft')
        with patch('spectra_app.main_window.fit_multi_peak', return_value=first):
            w._on_fit()
            wait_jobs(w)
        self.assertEqual(page.model.rowCount(), 21)
        self.assertEqual(len(graph.axes.lines), 1)
        # Batch success includes a real no-seeds status; retained Gate 1 has a
        # different grid and remains independent when the selected Gate changes.
        short = replace(first, x=first.x[:7], y_fit=first.y_fit[:7], components=first.components[:, :7])
        with patch('spectra_app.main_window.fit_multi_peak', side_effect=[first, short]):
            w._on_batch_fit_all_gates()
            wait_jobs(w)
        other = next(s for s in ws.sheets.values() if s.role == 'fit_curves' and s.fit_gate_idx == 1)
        self.assertEqual(state.sheet_shape(ws, other)[0], 7)
        parameters = next(s for s in ws.sheets.values() if s.role == 'fit_parameters')
        self.assertIn('no_seeds', state.sheet_column_values(ws, parameters, 2))
        self.assertEqual(state.sheet_column_values(ws, parameters, 4)[0], first.centers[0])
        frozen = state.save_sheet_snapshot(ws, source.sheet_id, 'Before processing')
        values = frozen.data.copy()
        original = state.sheet_values(ws, state.ensure_dataset_sheet(ws, ws.active_dataset_id)).copy()
        w._replace_current_data(replace(w.current_cube, z=w.current_cube.z * 2), mode='energy_second_derivative', smooth=False, window_length=21)
        self.assertEqual(page.model.rowCount(), 0)
        self.assertEqual(state.resolve_curve(ws, curve)[1].size, 0)
        np.testing.assert_equal(frozen.data, values)
        np.testing.assert_equal(state.sheet_values(ws, state.ensure_dataset_sheet(ws, ws.active_dataset_id)), original)
        self.assertTrue(any(s.role == 'processed' for s in ws.sheets.values()))
        set_xy_draft(graph, 'color', '#123456')
        self.assertFalse(self.errors)

    def test_merge_cancel_busy_and_invalid_editor_are_atomic(self):
        ws = self.window.workspace
        books = [state.create_workbook(ws, name) for name in ('A', 'B')]
        sheets = [state.register_xy_sheet(ws, b.workbook_id, 'Original', np.array([[1., 2.]]), ['X', 'Y']) for b in books]
        comparison = state.create_comparison(ws)
        state.add_curve(ws, comparison.comparison_id, sheets[0].sheet_id, 0, 1)
        graph = self.window.open_xy_comparison(comparison.comparison_id)
        before = [list(b.sheet_ids) for b in books]
        set_xy_draft(graph, 'color', 'bad')
        with self.assertRaises(ValueError):
            self.window.merge_workbooks([b.workbook_id for b in books], books[1].workbook_id)
        self.assertEqual([b.sheet_ids for b in books], before)
        set_xy_draft(graph, 'color', '#123456')
        with patch.object(self.window, '_project_busy', return_value=True), self.assertRaises(ValueError):
            self.window.merge_workbooks([b.workbook_id for b in books], books[1].workbook_id)
        self.window._refresh_data_navigator()
        for book in books:
            self.window.project.select_key(('workbook', book.workbook_id), Qt.ControlModifier)
        with patch('spectra_app.main_window.prompt_item', return_value=('', False)):
            self.window.merge_workbooks_btn.trigger()
        self.assertEqual([b.sheet_ids for b in books], before)
        self.assertFalse(self.errors)


class OrganizationModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.workspace = rich_workspace(self.folder)
        self.session = self.workspace.datasets['owner-0']

    def role(self, role, gate=None):
        return next(s for s in self.workspace.sheets.values()
                    if s.dataset_id == 'owner-0' and s.role == role and s.fit_gate_idx == gate)

    def test_live_results_independent_grids_stable_clear_refit_and_text(self):
        result = self.session.batch_fit_result_map[1]
        self.session.batch_fit_result_map[2] = replace(result, x=result.x[:3], y_fit=result.y_fit[:3], components=result.components[:, :3])
        state.reconcile_dataset_sheets(self.workspace, 'owner-0')
        parameters = self.role('fit_parameters')
        curves = self.role('fit_curves', 2)
        self.assertEqual(state.sheet_shape(self.workspace, curves), (3, 4))
        self.assertEqual(state.sheet_column_values(self.workspace, parameters, 1)[0], 'Gaussian')
        self.assertEqual(state.sheet_column_values(self.workspace, parameters, 4)[0], result.centers[0])
        with self.assertRaises(ValueError):
            state.validate_mapping(self.workspace, parameters, 1, [4])
        graph = state.create_comparison(self.workspace)
        curve = state.add_curve(self.workspace, graph.comparison_id, curves.sheet_id, 0, 3)
        self.session.batch_fit_result_map.clear()
        self.session.fit_result = None
        self.session.fit_export_rows.clear()
        state.reconcile_dataset_sheets(self.workspace, 'owner-0')
        self.assertEqual(state.sheet_shape(self.workspace, curves), (0, 4))
        self.assertEqual(state.resolve_curve(self.workspace, curve)[1].size, 0)
        self.session.batch_fit_result_map[2] = replace(result, components=result.components[:1], centers=result.centers[:1], amplitudes=result.amplitudes[:1], widths=result.widths[:1])
        state.reconcile_dataset_sheets(self.workspace, 'owner-0')
        self.assertTrue(np.isnan(state.resolve_curve(self.workspace, curve)[1]).all())
        self.assertIs(self.role('fit_curves', 2), curves)

    def test_snapshot_merge_remove_and_source_free_column_metadata_schema11(self):
        state.reconcile_dataset_sheets(self.workspace, 'owner-0')
        source = self.role('fit_parameters')
        snapshot = state.save_sheet_snapshot(self.workspace, source.sheet_id, 'Before clear')
        before = [state.sheet_column_values(self.workspace, snapshot, c).copy() for c in range(len(snapshot.column_names))]
        note = snapshot.snapshot_note
        books = list(self.workspace.workbooks)
        state.merge_workbooks(self.workspace, books, books[1])
        self.workspace.remove('owner-0')
        self.assertIn(snapshot.sheet_id, self.workspace.sheets)
        for column, values in enumerate(before):
            np.testing.assert_equal(state.sheet_column_values(self.workspace, snapshot, column), values)
        self.assertEqual(snapshot.snapshot_note, note)
        path = self.folder / 'organized.spectra'
        write_project(path, self.workspace)
        self.assertTrue(snapshot.column_metadata)
        self.assertEqual(json.loads(archive_members(path)['manifest.json'])['version'], 11)
        for source in self.folder.glob('*.dat'):
            source.unlink()
        loaded = read_project(path)
        for column, values in enumerate(before):
            np.testing.assert_equal(state.sheet_column_values(loaded, snapshot.sheet_id, column), values)
        state.validate_workbook_state(loaded)

    def test_new_metadata_rejects_invalid_text_mapping_and_projection(self):
        state.reconcile_dataset_sheets(self.workspace, 'owner-0')
        snapshot = state.save_sheet_snapshot(self.workspace, self.role('fit_parameters').sheet_id, 'Parameters')
        path = self.folder / 'bad.spectra'
        write_project(path, self.workspace)
        original = archive_members(path)
        for mutation in ('text_length', 'text_x', 'gate', 'role'):
            manifest = json.loads(original['manifest.json'])
            record = next(s for s in manifest['sheets'] if s['sheet_id'] == snapshot.sheet_id)
            if mutation == 'text_length':
                record['text_columns']['1'] = []
            elif mutation == 'text_x':
                record['x_column'] = 1
            elif mutation == 'gate':
                record['fit_gate_idx'] = 0
            else:
                record['role'] = 'processed'
            overwrite_archive(path, {**original, 'manifest.json': json.dumps(manifest).encode()})
            with self.subTest(mutation=mutation), self.assertRaises(ProjectFormatError):
                read_project(path)

    def test_legacy_preflight_rejects_invalid_mappings_and_ownership_before_migration(self):
        # Construct schema5 fields from a plain current workspace; the independent
        # original-writer migration fixture is also exercised by parent acceptance.
        from spectra_app.dataset_state import Workspace
        workspace = Workspace()
        workspace.register(self.session)
        original = state.ensure_dataset_sheet(workspace, self.session.dataset_id)
        graph = state.create_comparison(workspace)
        state.add_curve(workspace, graph.comparison_id, original.sheet_id, 0, 1)
        path = self.folder / 'legacy.spectra'
        write_project(path, workspace)
        members = archive_members(path)
        manifest = json.loads(members['manifest.json'])
        keep = {s['sheet_id'] for s in manifest['sheets'] if s['role'] == 'original'}
        manifest['sheets'] = [s for s in manifest['sheets'] if s['sheet_id'] in keep]
        for book in manifest['workbooks']:
            book['sheet_ids'] = [key for key in book['sheet_ids'] if key in keep]
        for sheet in manifest['sheets']:
            for field in ('role', 'fit_gate_idx', 'text_columns', 'snapshot_note'):
                sheet.pop(field)
        manifest['version'] = 5
        baseline = json.dumps(manifest)
        for mutation in ('sheet_x', 'curve_y', 'owner', 'source', 'missing_dataset'):
            value = json.loads(baseline)
            if mutation == 'sheet_x':
                value['sheets'][0]['x_column'] = 999999
            elif mutation == 'curve_y':
                value['comparisons'][0]['curves'][0]['y_column'] = 999999
            elif mutation == 'owner':
                value['workbooks'][0]['sheet_ids'] = []
            elif mutation == 'source':
                value['sheets'][0]['source_path'] = 'changed.dat'
            else:
                value['sheets'][0]['dataset_id'] = 'missing'
            overwrite_archive(path, {**members, 'manifest.json': json.dumps(value).encode()})
            with self.subTest(mutation=mutation), self.assertRaises(ProjectFormatError):
                read_project(path)

    def test_single_fit_fallback_and_schema6_curve_mode_consistency(self):
        self.session.batch_fit_result_map.clear()
        self.session.fit_export_rows.clear()
        state.reconcile_dataset_sheets(self.workspace, self.session.dataset_id)
        source = self.role('fit_curves', self.session.fit_gate_idx)
        np.testing.assert_equal(state.sheet_column_values(self.workspace, source, 1), self.session.fit_result.y_fit)
        # A nonempty retained map is authoritative even with a stale transient pointer.
        self.session.batch_fit_result_map[2] = self.session.fit_result
        state.reconcile_dataset_sheets(self.workspace, self.session.dataset_id)
        self.assertEqual(state.sheet_shape(self.workspace, source)[0], 0)
        graph = state.create_comparison(self.workspace)
        with self.assertRaisesRegex(ValueError, 'mode'):
            state.add_curve(self.workspace, graph.comparison_id, source.sheet_id, 0, 1, mode='processed')
        self.assertFalse(graph.curves)
        curve = state.add_curve(self.workspace, graph.comparison_id, source.sheet_id, 0, 1)
        curve.mode = 'processed'
        with self.assertRaisesRegex(ValueError, 'mode'):
            state.validate_workbook_state(self.workspace)
