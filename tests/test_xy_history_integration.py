"""Global Edit commands follow the active XY document through real Delete events."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])


class XYHistoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.window = MainWindow()
        self.window._show_error = self.fail
        self.addCleanup(self.window.close)

    def test_delete_undo_redo_restore_curve_identity_and_selection_via_global_edit(self):
        source = self.folder / 'curves.csv'
        source.write_text('X,A,B\n0,1,2\n1,3,4\n2,5,6\n', encoding='utf-8')
        book = self.window.import_xy_files([source])
        page = self.window._add_sheet_comparison(book.sheet_ids[0])
        self.window.show()
        QTest.qWait(20)
        page.curve_list.setCurrentRow(0)
        curve_id = page.curve_list.currentItem().data(Qt.UserRole)
        page.curve_list.setFocus()
        QTest.keyClick(page.curve_list, Qt.Key_Delete)
        self.assertNotIn(curve_id, page.comparison.curves)
        undo = self.window._graph_command_actions['undo']
        redo = self.window._graph_command_actions['redo']
        self.assertTrue(undo.isEnabled())
        undo.trigger()
        self.assertIn(curve_id, page.comparison.curves)
        self.assertEqual(page._selected_ids(), {curve_id})
        self.assertTrue(redo.isEnabled())
        self.window.open_workbook(book.workbook_id)
        self.assertFalse(undo.isEnabled())
        self.window.open_xy_comparison(page.comparison_id)
        self.assertTrue(redo.isEnabled())
        redo.trigger()
        self.assertNotIn(curve_id, page.comparison.curves)
