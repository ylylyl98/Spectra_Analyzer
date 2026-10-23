"""Content detection and real unified import routes, including retained projects."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from threading import Event
from unittest.mock import patch

import numpy as np

from spectra_app import dat_io
from spectra_app.background_jobs import JobContext, LoadRequest
from spectra_app.data_import import prepare_data_batch
from spectra_app.main_window import MainWindow
from spectra_app.workbook_state import create_comparison
from spectra_app.xy_import_dialog import XYImportDialog
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from tests.background_helpers import wait_jobs
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])


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


class UnifiedImportGuards(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.window = MainWindow()
        self.errors = []
        self.window._show_error = self.errors.append
        self.paths = [self.folder / 'matrix.csv', self.folder / 'ordinary.dat']
        self.paths[0].write_text('Energy(eV),2,-1\n1,20,10\n2,40,30\n')
        self.paths[1].write_text('3 10 20\n1 30 40\n2 50 60\n')
        self.addCleanup(self.dispose)

    def dispose(self):
        wait_jobs(self.window)
        self.window.close()
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

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
        self.assertEqual(dialog.type_combo.currentData(), 'dat')
        self.assertFalse(dialog.x_combo.isEnabled())
        self.assertIn('Gate = -1', dialog.tables[0].column_names)
        np.testing.assert_equal(dialog.tables[0].data, [[1, 10, 20], [2, 30, 40]])
        dialog.type_combo.setCurrentIndex(dialog.type_combo.findData('xy'))
        self.assertTrue(dialog.x_combo.isEnabled())
        self.assertIn('Manual type correction', dialog.type_reason.text())
        dialog.file_list.setCurrentRow(1)
        dialog.type_combo.setCurrentIndex(dialog.type_combo.findData('dat'))
        self.window.import_data_files(entries, mappings=dialog.mappings)
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
        page.name_edit.setText('Pending name')
        target_page.name_edit.setText(' ')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.window.import_data_files(self.prepared(), comparison_id=target.comparison_id)
        self.assertEqual(current.name, 'Current')
        self.assertEqual(page.name_edit.text(), 'Pending name')
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
            self.assertIn(str(invalid), dialog.feedback.text())
            seen.extend(entry.kind for entry in dialog.entries)
            return QDialog.Accepted
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
            self.assertFalse(dialog.type_combo.isEnabled())
            self.assertEqual([entry.kind for entry in dialog.entries], ['dat', 'xy'])
            return QDialog.Accepted
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
        w.show()
        APP.processEvents()
        header = page.table.horizontalHeader()
        point = QPoint(header.sectionViewportPosition(2) + header.sectionSize(2) // 2, header.height() // 2)
        with patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('reread')):
            QTest.mouseClick(header.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        self.assertEqual(w.workspace.active_dataset_id, sheet.dataset_id)
        self.assertEqual(w.current_gate, 2)
        self.assertIs(w.current_cube, entries[0].session.current_cube)
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)
        w.plot_tabs.setCurrentIndex(next(index for index in range(w.plot_tabs.count())
                                        if w.plot_tabs.tabText(index) == 'Analysis'))
        self.assertIsNot(w.plot_tabs.currentWidget(), w.graph_documents)
        self.assertTrue(w.generate_heatmap_btn.isEnabled())
        self.assertIsNotNone(w._heat_mesh)
        self.assertFalse(self.errors)
