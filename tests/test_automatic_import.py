"""Automatic import boundaries: actual entry points, full tables and optional review."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from threading import Event
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt, QUrl
from PySide6.QtTest import QTest

from spectra_app.background_jobs import JobContext, LoadRequest
from spectra_app.data_import import prepare_data_batch
from spectra_app.dataset_state import graph_cube
from spectra_app.workbook_state import (create_comparison, ensure_dataset_sheet,
    set_sheet_mapping, sheet_values)
from spectra_app.xy_import_dialog import XYImportDialog
from tests.background_helpers import wait_jobs
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as quick
from tests import source_navigation_helpers as sn


class AutomaticImportTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.errors = self.quick_errors

    def tearDown(self):
        wait_jobs(self.window)

    def file(self, name='table.csv', text='x,a,b\n3,10,20\n1,30,40\n'):
        path = self.folder / name
        path.write_text(text)
        return path

    def prepared(self, paths):
        entries, errors = prepare_data_batch(
            [LoadRequest(path, None, self.window._available_colormaps()) for path in paths],
            self.window.snapshot_dataset_defaults(), JobContext(Event(), lambda _: None))
        self.assertFalse(errors)
        return entries

    def test_ordinary_file_project_source_inventory_and_drop_publish_without_review(self):
        w = self.window
        for route in ('file', 'project', 'source', 'inventory', 'drop'):
            with self.subTest(route=route):
                path = self.file(route + '.csv')
                with patch('spectra_app.main_window.FilePicker.getOpenFileNames', return_value=([str(path)], '')), \
                        patch.object(XYImportDialog, 'exec', side_effect=AssertionError('automatic import opened review')):
                    if route == 'file':
                        w._graph_command_actions['import'].trigger()
                    elif route == 'project':
                        quick.click_item(w, 'projectImport')
                    elif route == 'source':
                        self.assertTrue(w.request_outer_view('analysis'))
                        sn.reveal(w)
                        quick.click_item(w, 'sourceImport')
                    elif route == 'inventory':
                        self.assertTrue(w.request_outer_view('analysis'))
                        ticket = w.source_navigation.begin_folder(path.parent)
                        w.source_navigation.apply_files(ticket, [path.name])
                        sn.reveal(w, inventory=True)
                        quick.click_item(w, 'sourceLoad')
                    else:
                        self.assertTrue(w.dropUrls([QUrl.fromLocalFile(str(path))]))
                    wait_jobs(w)
                sheet = next(s for s in w.workspace.sheets.values() if s.source_path == path)
                self.assertEqual((sheet.x_column, sheet.y_columns), (0, [1, 2]))
                np.testing.assert_equal(sheet.data, [[3, 10, 20], [1, 30, 40]])
                self.assertEqual(w.outerView, 'workspace')
                self.assertIsNotNone(w.graph_documents.content_item(w.graph_documents.registry.active_key))
                self.assertIsNone(w.workspace.active_dataset_id)
                self.assertFalse(w.workspace.comparisons)
        self.assertEqual(len(w.workspace.workbooks), 5)
        self.assertFalse(self.errors)

    def test_all_y_defaults_follow_each_table_width_and_legacy_import(self):
        paths = [self.file('two.csv', 'x,a\n1,2\n'), self.file('four.csv', 'x,a,b,c\n1,2,3,4\n')]
        entries = self.prepared(paths)
        self.assertEqual([(e.mapping[0], list(e.mapping[1])) for e in entries], [(0, [1]), (0, [1, 2, 3])])
        tables, mappings, _ = self.window._prepare_xy_import(paths)
        self.assertEqual(mappings, [(0, [1]), (0, [1, 2, 3])])
        dialog = XYImportDialog(tables, {})
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.mappings, [(0, [1]), (0, [1, 2, 3])])
        self.window.import_xy_files(paths)
        self.assertEqual([s.y_columns for s in self.window.workspace.sheets.values()], [[1], [1, 2, 3]])

    def test_large_matrix_imports_every_gate_and_roundtrips_without_source(self):
        gates = list(range(100, -1, -1))
        path = self.file('large.csv', 'Energy(eV),' + ','.join(map(str, gates)) + '\n'
                         + '\n'.join(str(x) + ',' + ','.join(str(x * 1000 + g) for g in gates) for x in (1, 2)))
        w = self.window
        with patch.object(XYImportDialog, 'exec', side_effect=AssertionError('unexpected matrix review')):
            w._import_data_files([path])
            wait_jobs(w)
        sheet = next(iter(w.workspace.sheets.values()))
        self.assertEqual(sheet.y_columns, list(range(1, 102)))
        values = sheet_values(w.workspace, sheet).copy()
        self.assertEqual(values.shape, (2, 102))
        np.testing.assert_equal(values[:, [0, 1, 101]], [[1, 1000, 1100], [2, 2000, 2100]])
        processed = ensure_dataset_sheet(w.workspace, sheet.dataset_id, 'processed')
        self.assertEqual(processed.y_columns, list(range(1, 102)))
        identity, owner = sheet.sheet_id, w._sheet_owner(sheet.sheet_id).workbook_id
        saved = w.save_project(self.folder / 'automatic.spectra')
        path.unlink()
        with patch.object(w, '_confirm_project_discard', return_value=True):
            self.assertTrue(w.open_project(saved))
        self.assertEqual(w.workspace.sheets[identity].y_columns, list(range(1, 102)))
        self.assertEqual(w._sheet_owner(identity).workbook_id, owner)
        np.testing.assert_equal(sheet_values(w.workspace, identity), values)
        self.assertFalse(w.workspace.comparisons)

    def test_spaced_dat_headers_auto_import_and_select_all_heatmaps_keep_every_gate(self):
        gates = np.linspace(6, -6, 301)
        energy = np.array([1.8, 1.7, 1.6])
        paths, expected = [], {}
        for number in range(2):
            values = np.arange(903).reshape(3, 301) + number * 1000
            text = 'Photon energy(eV)\t' + '\t'.join(map(str, gates)) + '\n'
            text += '\n'.join('\t'.join(map(str, row)) for row in np.column_stack((energy, values)))
            path = self.file(f'spaced-{number}.dat', text)
            paths.append(path)
            expected[path] = values[::-1, ::-1].T
        ordinary = self.file('ordinary.dat', 'x signal temperature\n3 10 20\n1 30 40\n')
        w = self.window
        with patch.object(XYImportDialog, 'exec', side_effect=AssertionError('unexpected import review')):
            w._import_data_files([*paths, ordinary])
            wait_jobs(w)
        self.host.show()
        for path in paths:
            with self.subTest(path=path.name):
                sheet = next(s for s in w.workspace.sheets.values() if s.source_path == path)
                self.assertEqual(sheet.kind, 'dat')
                session = w.workspace.datasets[sheet.dataset_id]
                self.assertEqual(session.base_cube.x_detection_source, 'header label')
                self.assertEqual(session.base_cube.x_detection_note,
                                 "Detected from x-axis header 'Photon energy(eV)'.")
                book = w.open_workbook(w._sheet_owner(sheet.sheet_id).workbook_id, sheet.sheet_id)
                page = book.current_sheet
                set_sheet_mapping(w.workspace, sheet.sheet_id, 0, [1])
                page.refresh()
                quick.pump()
                scene = w.graph_documents.content_item(('workbook', book.workbook_id, ''))
                quick.item(scene, 'sheetCells').forceActiveFocus()
                QTest.keyClick(self.host.window, Qt.Key_A, Qt.ControlModifier)
                quick.pump()
                self.assertEqual(sheet.y_columns, list(range(1, 302)))
                self.assertTrue(w.workbook_commands.heatmapReady)
                quick.click_item(w, 'workbookHeatmap')
                self.assertEqual(session.active_graph.gate_indices, list(range(301)))
                cube = graph_cube(session)
                np.testing.assert_allclose(cube.gate, gates[::-1])
                np.testing.assert_array_equal(cube.energy, energy[::-1])
                np.testing.assert_array_equal(cube.z, expected[path])
        sheet = next(s for s in w.workspace.sheets.values() if s.source_path == ordinary)
        self.assertEqual(sheet.kind, 'xy')
        np.testing.assert_array_equal(sheet.data, [[3, 10, 20], [1, 30, 40]])
        self.assertFalse(self.errors)

    def test_optional_shared_action_reviews_type_mapping_and_cancel_without_publication(self):
        w = self.window
        path = self.file('matrix.csv', 'Energy(eV),2,-1\n1,20,10\n2,40,30\n')
        action = w._graph_command_actions['import_options']
        self.assertIsNotNone(action)
        project_action = next(a for a in w.app_bridge.project.actions if a.key == 'import_options')
        self.assertIs(project_action._action, action)
        self.host.show()
        quick.pump()
        seen = []
        def review(dialog):
            self.assertFalse(w._background_jobs.busy)
            self.assertEqual(dialog.mappings, [(0, [1, 2])])
            self.assertIn('comparison', dialog.bridge.typeReason.lower())
            dialog.bridge.setKind('xy')
            self.assertEqual(dialog.mappings, [(0, [1, 2])])
            dialog.mappings = [(2, [0])]
            seen.append(True)
            return XYImportDialog.Accepted
        with patch('spectra_app.main_window.FilePicker.getOpenFileNames', return_value=([str(path)], '')), \
                patch.object(XYImportDialog, 'exec', review):
            quick.click_item(w, 'projectActions')
            self.assertEqual(quick.item(w, 'projectCommand_import_options').property('text'), action.text())
            quick.click_item(w, 'projectCommand_import_options')
            wait_jobs(w)
        sheet = next(iter(w.workspace.sheets.values()))
        self.assertEqual((sheet.kind, sheet.x_column, sheet.y_columns), ('xy', 2, [0]))
        self.assertEqual(seen, [True])
        other = self.file('cancel.csv')
        before = (list(w.workspace.sheets), list(w.workspace.workbooks), w.graph_documents.keys())
        with patch('spectra_app.main_window.FilePicker.getOpenFileNames', return_value=([str(other)], '')), \
                patch.object(XYImportDialog, 'exec', return_value=XYImportDialog.Rejected):
            action.trigger()
            wait_jobs(w)
        self.assertEqual((list(w.workspace.sheets), list(w.workspace.workbooks), w.graph_documents.keys()), before)

    def test_existing_graph_file_route_keeps_review_and_explicit_curve_selection(self):
        w = self.window
        path = self.file()
        target = create_comparison(w.workspace, 'Target')
        page = w.open_xy_comparison(target.comparison_id)
        def review(dialog):
            self.assertEqual(dialog.comparison_id, target.comparison_id)
            self.assertEqual(dialog.mappings, [(0, [1, 2])])
            dialog.mappings = [(0, [2])]
            dialog.compare = True
            return XYImportDialog.Accepted
        with patch('spectra_app.main_window.FilePicker.getOpenFileNames', return_value=([str(path)], '')), \
                patch.object(XYImportDialog, 'exec', review):
            page.importDataRequested.emit()
            wait_jobs(w)
        self.assertEqual([c.y_column for c in target.curves.values()], [2])
        self.assertEqual(w.graph_documents.registry.active_key, ('xy', target.comparison_id, ''))

    def test_automatic_reimport_preserves_custom_mapping_identity_and_owner(self):
        path = self.file()
        w = self.window
        w.import_data_files(self.prepared([path]))
        sheet = next(iter(w.workspace.sheets.values()))
        set_sheet_mapping(w.workspace, sheet.sheet_id, 2, [0])
        owner = w._sheet_owner(sheet.sheet_id).workbook_id
        path.unlink()
        with patch.object(XYImportDialog, 'exec', side_effect=AssertionError('retained review')):
            w._import_data_files([path])
            wait_jobs(w)
        self.assertIs(w.workspace.sheets[sheet.sheet_id], sheet)
        self.assertEqual((sheet.x_column, sheet.y_columns), (2, [0]))
        self.assertEqual(w._sheet_owner(sheet.sheet_id).workbook_id, owner)

    def test_partial_failures_publish_at_idle_and_remain_visible_in_workspace(self):
        good, bad = self.file(), self.file('bad.csv', 'no numeric data')
        w = self.window
        original = w.import_data_files
        def publish(*args, **kwargs):
            self.assertFalse(w._background_jobs.busy)
            self.assertFalse(w._dat_import_pending)
            return original(*args, **kwargs)
        with patch.object(XYImportDialog, 'exec', side_effect=AssertionError('partial review')), \
                patch.object(w, 'import_data_files', publish):
            w._import_data_files([good, bad])
            wait_jobs(w)
        self.assertEqual(len(w.workspace.sheets), 1)
        self.assertIn('1', w.statusText)
        self.assertIn('failed', w.statusText.lower())
        self.assertIn(str(bad), w.source_navigation.importStatus)
        self.assertIn(str(bad), self.errors[-1])

    def test_stale_workspace_close_and_cancel_delivery_do_not_publish(self):
        w = self.window
        entries = self.prepared([self.file()])
        for reason in ('workspace', 'close', 'cancel'):
            with self.subTest(reason=reason):
                w._dat_import_token += 1
                token, workspace = w._dat_import_token, w.workspace
                w._set_dat_import_pending(True)
                w._pending_data_review = (token, workspace, entries, [], None, False)
                if reason == 'workspace':
                    w.workspace = type(workspace)()
                elif reason == 'close':
                    w._close_pending = True
                else:
                    w._cancel_background_job()
                with patch.object(XYImportDialog, 'exec', side_effect=AssertionError('stale dialog')):
                    w._on_background_idle()
                self.assertFalse(w.workspace.sheets)
                self.assertFalse(w.graph_documents.keys())
                w._close_pending = False
                w._set_dat_import_pending(False)
