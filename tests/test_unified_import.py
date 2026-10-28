"""Content detection and real unified import routes, including retained projects."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from dataclasses import fields
import tempfile
import unittest
from threading import Event
from unittest.mock import patch

import numpy as np

from spectra_app import dat_io
from spectra_app.background_jobs import CancelledError, JobContext, LoadRequest
from spectra_app.data_import import prepare_data_batch
from spectra_app.dataset_state import (AnalysisControls, DatasetDefaults, FixedRecipe,
                                      ProcessingState, ViewState)
from spectra_app.plot_state import PlotStateStore, source_fingerprint
from spectra_app.workbook_state import create_comparison
from spectra_app.xy_import_dialog import XYImportDialog
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from tests.background_helpers import wait_jobs
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as quick

from tests.xy_helpers import set_xy_draft, xy_value

class ContentDetectionTests(unittest.TestCase):
    def test_matrix_header_is_evidence_independent_of_suffix_and_separator(self):
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ('.dat', '.txt', '.csv', '.tsv', '.xy'):
                for separator in (' ', ',', '\t'):
                    path = Path(directory) / ('matrix' + suffix)
                    path.write_text('\n'.join(separator.join(row) for row in
                        [('Energy(eV)', '2', '-1'), ('1', '20', '10'), ('2', '40', '30')]))
                    self.assertTrue(dat_io.matrix_layout_reason(path))

    def test_generic_headerless_and_incomplete_header_are_not_matrix_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'table.dat'
            for text in ('3 10 20\n1 30 40\n2 50 60\n',
                         'x signal temperature\n1 2 3\n2 4 6\n',
                         'x 0 missing\n1 2 3\n2 4 6\n'):
                path.write_text(text)
                self.assertFalse(dat_io.matrix_layout_reason(path))

    def test_existing_embedded_and_numeric_header_layouts_are_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'matrix.txt'
            for prefix in ('', 'Title 0 0\n0 0 0\n'):
                path.write_text(prefix + '0 -1 1\n500 10 20\n501 30 40\n502 50 60\n')
                self.assertTrue(dat_io.matrix_layout_reason(path))


class UnifiedParseReuseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.path = self.folder / 'matrix.csv'
        self.path.write_text('Wavelength(nm),2,-1\n600,20,10\n500,40,30\n')
        self.defaults = DatasetDefaults(ViewState(), AnalysisControls(),
            {group: FixedRecipe() for group in ('processing', 'regions', 'region_edges')}, {})
        self.context = JobContext(Event(), lambda _: None)

    def prepare(self, request=None, **kwargs):
        return prepare_data_batch([request or LoadRequest(self.path, None, ('viridis',))],
                                  self.defaults, self.context, **kwargs)

    def assert_cube_equal(self, actual, expected):
        for field in fields(expected):
            with self.subTest(cube_field=field.name):
                np.testing.assert_equal(getattr(actual, field.name), getattr(expected, field.name))

    def test_batch_reads_dat_layout_once_and_retains_both_complete_interpretations(self):
        expected = dat_io.load_dat_cube(self.path)
        with patch.object(dat_io, '_read_dat_layout', wraps=dat_io._read_dat_layout) as reader:
            entries, errors = self.prepare()
        self.assertFalse(errors)
        entry, = entries
        self.assertEqual(entry.kind, 'dat')
        self.assertEqual(set(entry.tables), {'xy', 'dat'})
        self.assertEqual(entry.tables['xy'].column_names, ['Wavelength(nm)', '2', '-1'])
        np.testing.assert_equal(entry.tables['xy'].data, [[600, 20, 10], [500, 40, 30]])
        np.testing.assert_equal(entry.tables['dat'].data,
                                [[1240 / 600, 10, 20], [1240 / 500, 30, 40]])
        self.assert_cube_equal(entry.session.base_cube, expected)
        self.assertEqual(entry.session.loaded_fingerprint, source_fingerprint(self.path))
        self.assertEqual(reader.call_count, 1)

    def test_valid_xy_tables_keep_original_order_and_do_not_retry_failed_dat_parse(self):
        for source, expected, names in (
                ('3 10 20\n1 30 40\n', [[3, 10, 20], [1, 30, 40]],
                 ['Column 1', 'Column 2', 'Column 3']),
                ('"Signal","X"\n"10","2"\n"20","1"\n', [[10, 2], [20, 1]], ['Signal', 'X']),
                ('X,Y\n3,10\n1,NA\n', [[3, 10], [1, np.nan]], ['X', 'Y'])):
            with self.subTest(source=source):
                self.path.write_text(source)
                with patch.object(dat_io, '_read_dat_layout', wraps=dat_io._read_dat_layout) as reader:
                    entries, errors = self.prepare()
                self.assertFalse(errors)
                entry, = entries
                self.assertEqual(entry.kind, 'xy')
                np.testing.assert_equal(entry.table.data, expected)
                self.assertEqual(entry.table.column_names, names)
                if source.startswith('"'):
                    self.assertEqual(set(entry.tables), {'xy'})
                    self.assertIn('No numeric matrix rows', entry.option_errors['dat'])
                self.assertEqual(reader.call_count, 1)

    def test_source_change_after_first_layout_read_rejects_reused_layout(self):
        original = dat_io._read_dat_layout
        def changed(path):
            layout = original(path)
            with path.open('a') as stream:
                stream.write('400,50,60\n')
            return layout
        with patch.object(dat_io, '_read_dat_layout', side_effect=changed) as reader:
            entries, errors = self.prepare()
        self.assertFalse(entries)
        self.assertEqual([error.path for error in errors], [self.path])
        self.assertIn('Source content changed while preparing', errors[0].reason)
        self.assertEqual(reader.call_count, 1)

    def test_saved_axis_is_applied_to_raw_layout_and_recall_controls_remain_effective(self):
        saved_cube = dat_io.load_dat_cube(self.path, x_axis_override='pixel')
        recipe = ViewState().plot_recipe(saved_cube, ProcessingState())
        PlotStateStore().save(self.path, source_fingerprint(self.path), recipe)
        for recall, colormaps, expected_override in (
                (True, ('viridis',), 'pixel'), (False, ('viridis',), 'mev'),
                (True, ('magma',), 'mev')):
            with self.subTest(recall=recall, colormaps=colormaps):
                request = LoadRequest(self.path, 'mev', colormaps, recall=recall)
                expected = dat_io.load_dat_cube(self.path, x_axis_override=expected_override)
                with patch.object(dat_io, '_read_dat_layout', wraps=dat_io._read_dat_layout) as reader:
                    entries, errors = self.prepare(request)
                self.assertFalse(errors)
                entry, = entries
                self.assertEqual(entry.kind, 'dat')
                self.assert_cube_equal(entry.session.base_cube, expected)
                np.testing.assert_equal(entry.tables['xy'].data, [[600, 20, 10], [500, 40, 30]])
                if colormaps == ('magma',):
                    self.assertIn('colormap is unavailable', entry.session.initialization_notice)
                self.assertEqual(reader.call_count, 1)

    def test_cancelled_preparation_propagates_and_missing_invalid_errors_stay_ordered(self):
        self.context._cancelled.set()
        with self.assertRaises(CancelledError):
            self.prepare()
        self.context._cancelled.clear()
        original = dat_io._read_dat_layout
        def cancelled(path):
            layout = original(path)
            self.context._cancelled.set()
            return layout
        with patch.object(dat_io, '_read_dat_layout', side_effect=cancelled):
            with self.assertRaises(CancelledError):
                self.prepare()
        self.context._cancelled.clear()
        invalid, missing = self.folder / 'invalid.csv', self.folder / 'missing.dat'
        invalid.write_text('no numeric rows')
        entries, errors = prepare_data_batch(
            [LoadRequest(path, None, ('viridis',)) for path in (missing, self.path, invalid)],
            self.defaults, self.context)
        self.assertEqual([entry.path for entry in entries], [self.path])
        self.assertEqual([error.path for error in errors], [missing, invalid])


class UnifiedImportGuards(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.errors = self.quick_errors
        self.paths = [self.folder / 'matrix.csv', self.folder / 'ordinary.dat']
        self.paths[0].write_text('Energy(eV),2,-1\n1,20,10\n2,40,30\n')
        self.paths[1].write_text('3 10 20\n1 30 40\n2 50 60\n')

    def tearDown(self):
        wait_jobs(self.window)

    def prepared(self):
        entries, errors = prepare_data_batch(
            [LoadRequest(path, None, self.window._available_colormaps()) for path in self.paths],
            self.window.snapshot_dataset_defaults(), JobContext(Event(), lambda _: None))
        self.assertFalse(errors)
        return entries

    def test_manual_type_override_changes_committed_type_and_matrix_preview_is_actual_cube(self):
        entries = self.prepared()
        dialog = XYImportDialog([entry.table for entry in entries], {}, entries=entries)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.bridge.kind, 'dat')
        self.assertFalse(dialog.bridge.xEditable)
        self.assertIn('Gate = -1', dialog.tables[0].column_names)
        np.testing.assert_equal(dialog.tables[0].data, [[1, 10, 20], [2, 30, 40]])
        dialog.bridge.setKind('xy')
        self.assertTrue(dialog.bridge.xEditable)
        self.assertIn('Manual type correction', dialog.bridge.typeReason)
        dialog.bridge.selectSource(1)
        dialog.bridge.setKind('dat')
        self.window.import_data_files(dialog.entries, mappings=dialog.mappings)
        original = [sheet for sheet in self.window.workspace.sheets.values() if sheet.role == 'original']
        self.assertEqual([sheet.kind for sheet in original], ['xy', 'dat'])
        np.testing.assert_equal(original[0].data, [[1, 20, 10], [2, 40, 30]])
        self.assertEqual(len(self.window.workspace.datasets), 1)

    def test_invalid_second_mapping_or_stale_target_cannot_register_first_matrix(self):
        entries = self.prepared()
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.window.import_data_files(entries, mappings=[(0, [1]), (0, [9])])
        self.assertFalse(self.window.workspace.datasets)
        self.assertFalse(self.window.workspace.sheets)
        target = create_comparison(self.window.workspace, 'Target')
        del self.window.workspace.comparisons[target.comparison_id]
        with self.assertRaisesRegex(ValueError, 'no longer exists'):
            self.window.import_data_files(entries, comparison_id=target.comparison_id)
        self.assertFalse(self.window.workspace.workbooks)

    def test_invalid_target_draft_does_not_mutate_mixed_batch_or_current_draft(self):
        target = create_comparison(self.window.workspace, 'Target')
        target_page = self.window.open_xy_comparison(target.comparison_id)
        current = create_comparison(self.window.workspace, 'Current')
        page = self.window.open_xy_comparison(current.comparison_id)
        quick.pump()
        set_xy_draft(page, 'name', 'Pending name')
        self.assertEqual(xy_value(page, 'name'), 'Pending name')
        self.assertEqual(self.window.graph_documents.registry.active_key,
                         ('xy', current.comparison_id, ''))
        self.assertFalse(target_page.inspector_bridge.enabled)
        set_xy_draft(target_page, 'name', ' ')
        self.assertEqual(xy_value(target_page, 'name'), 'Target')
        # An inactive scene rejects new input. Seed the retained hidden draft
        # at its real state owner to exercise append's two-owner preflight.
        target_page.inspector_bridge._drafts['name'] = ' '
        self.assertEqual(xy_value(target_page, 'name'), ' ')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.window.import_data_files(self.prepared(), comparison_id=target.comparison_id)
        self.assertEqual(current.name, 'Current')
        self.assertEqual(xy_value(page, 'name'), 'Pending name')
        self.assertFalse(self.window.workspace.sheets)
        self.assertFalse(self.window.workspace.datasets)

    def test_retained_missing_owner_rejects_mixed_batch_before_new_matrix_registration(self):
        self.window.import_xy_files(self.paths[1:])
        retained = self.window._retained_data_import(self.paths[1])
        self.window.workspace.workbooks.clear()
        entries = self.prepared()[:1] + [retained]
        with self.assertRaisesRegex(ValueError, 'Workbook'):
            self.window.import_data_files(entries)
        self.assertFalse(self.window.workspace.datasets)
        self.assertEqual(len(self.window.workspace.sheets), 1)

    def test_all_error_empty_and_partial_batches_preserve_state_and_review_only_after_idle(self):
        invalid, missing = self.folder / 'invalid.txt', self.folder / 'missing.dat'
        invalid.write_text('no numeric rows')
        w = self.window
        w._import_data_files([])
        self.assertFalse(w._background_jobs.busy)
        w._import_data_files([invalid, missing])
        wait_jobs(w)
        self.assertFalse(w.workspace.sheets)
        self.assertFalse(w.graph_documents.keys())
        self.assertIn(str(invalid), self.errors[-1])
        self.assertLess(self.errors[-1].index(str(invalid)), self.errors[-1].index(str(missing)))
        seen = []
        def review(dialog):
            self.assertFalse(w._background_jobs.busy)
            self.assertFalse(w._dat_import_pending)
            self.assertEqual(len(dialog.entries), 2)
            self.assertIn(str(invalid), dialog.bridge.feedback)
            seen.extend(entry.kind for entry in dialog.entries)
            return XYImportDialog.Accepted
        with patch.object(XYImportDialog, 'exec', review):
            w._import_data_files([self.paths[0], invalid, self.paths[1]], review=True)
            wait_jobs(w)
        self.assertEqual(seen, ['dat', 'xy'])
        self.assertEqual(len(w.workspace.workbooks), 2)

    def test_preparation_cancel_and_stale_review_leave_no_sources_or_documents(self):
        started, release = Event(), Event()
        def blocked(requests, defaults, context, *, preferred_kinds=None):
            started.set()
            release.wait(3)
            context.check_cancelled()
            return [], []
        w = self.window
        with patch('spectra_app.main_window.prepare_data_batch', blocked):
            w._import_data_files(self.paths)
            self.assertTrue(started.wait(2))
            token = w._dat_import_token
            w._cancel_background_job()
            release.set()
            wait_jobs(w)
        with patch.object(XYImportDialog, 'exec', side_effect=AssertionError('stale review')):
            w._review_data_import(token, w.workspace, self.prepared(), [], None)
        self.assertFalse(w.workspace.sheets)
        self.assertFalse(w.graph_documents.keys())
        self.assertFalse(self.errors)

    def test_retained_matrix_and_xy_never_read_missing_sources_and_keep_disabled_type(self):
        entries = self.prepared()
        w = self.window
        w.import_data_files(entries)
        ids = list(w.workspace.sheets)
        for path in self.paths:
            path.unlink()
        def review(dialog):
            self.assertTrue(all(dialog.reused))
            self.assertFalse(dialog.bridge.typeEditable)
            self.assertEqual([entry.kind for entry in dialog.entries], ['dat', 'xy'])
            return XYImportDialog.Accepted
        with patch('spectra_app.data_import.source_fingerprint', side_effect=AssertionError('retained source read')), \
                patch.object(XYImportDialog, 'exec', review):
            w._import_data_files(self.paths, review=True)
            wait_jobs(w)
        self.assertEqual(list(w.workspace.sheets), ids)
        self.assertFalse(self.errors)

    def test_new_matrix_workbook_gate_selection_enters_existing_analysis_without_source_read(self):
        w = self.window
        entries = self.prepared()[:1]
        book = w.import_data_files(entries)
        self.assertIsNone(w.workspace.active_dataset_id)
        self.assertEqual(w.graph_documents.keys(), (('workbook', book.workbook_id, ''),))
        sheet = w.workspace.sheets[book.sheet_ids[0]]
        page = w.open_workbook(book.workbook_id).sheet_pages[sheet.sheet_id]
        self.host.show()
        quick.pump()
        scene = w.graph_documents.content_item(('workbook', book.workbook_id, ''))
        header = quick.item(scene, 'sheetHeaders')
        point = header.mapToScene(QPointF(page.columnPosition(2) - float(header.property('contentX'))
                                         + page.columnWidth(2) / 2, header.height() / 6)).toPoint()
        with patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('reread')):
            QTest.mouseClick(self.host.window, Qt.LeftButton, Qt.NoModifier, point)
            quick.pump()
        self.assertEqual(w.workspace.active_dataset_id, sheet.dataset_id)
        self.assertEqual(w.current_gate, 2)
        self.assertIs(w.current_cube, entries[0].session.current_cube)
        self.assertEqual(w.outerView, 'workspace')
        self.assertTrue(w.request_outer_view('analysis'))
        self.assertEqual(w.outerView, 'analysis')
        self.assertTrue(w.processing.applyEnabled)
        self.assertIsNotNone(w._heat_mesh)
        self.assertFalse(self.errors)
