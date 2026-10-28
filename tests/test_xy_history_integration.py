"""Global Edit commands follow the active XY document through real Delete events."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as formal
from tests.xy_helpers import inspector_host, select_xy_rows

class XYHistoryIntegrationTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller

    def test_delete_undo_redo_restore_curve_identity_and_selection_via_global_edit(self):
        source = self.folder / 'curves.csv'
        source.write_text('X,A,B\n0,1,2\n1,3,4\n2,5,6\n', encoding='utf-8')
        book = self.window.import_xy_files([source])
        page = self.window._add_sheet_comparison(book.sheet_ids[0])
        QTest.qWait(20)
        select_xy_rows(page, [0])
        curve_id = page._active_curve_id
        view = formal.item(inspector_host(page), 'xyCurveList')
        view.forceActiveFocus()
        QTest.keyClick(formal.input_receiver(self.controller, view), Qt.Key_Delete)
        formal.pump()
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
