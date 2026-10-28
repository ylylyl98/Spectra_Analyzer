"""Production import review through its actual Qt Quick controls and results."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import unittest

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtQuick import QQuickView
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from spectra_app.data_import import PreparedDataFile
from spectra_app.dataset_state import Workspace
from spectra_app.workbook_state import create_workbook, create_comparison
from spectra_app.xy_import_dialog import XYImportDialog, _PreviewModel
from spectra_app.xy_io import XYTable, parse_xy

APP = QApplication.instance() or QApplication([])


class XYImportQuickTests(unittest.TestCase):
    def setUp(self):
        self.dialogs = []
        self.workspace = Workspace()

    def tearDown(self):
        for dialog in self.dialogs:
            if shiboken6.isValid(dialog):
                dialog.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def table(self, text='x,a,b\n1,2,3\n2,4,6\n3,6,9\n4,8,12\n', name='source.csv'):
        return parse_xy(text, source_path=Path(name))

    def entry(self, name='source.csv', *, retained=False):
        xy = self.table(name=name)
        dat = XYTable(np.array([[1., 30., 20.], [2., 60., 40.], [3., 90., 60.], [4., 120., 80.]]),
            ['Energy', 'Gate = -1', 'Gate = 2'], Path(name))
        return PreparedDataFile(Path(name), 'dat', 'Detected complete Gate layout.', {'xy': xy, 'dat': dat},
            sheet_id='retained' if retained else None)

    def dialog(self, tables=None, **kwargs):
        dialog = XYImportDialog(tables or [self.table()], self.workspace.workbooks,
            comparisons=self.workspace.comparisons, **kwargs)
        self.dialogs.append(dialog)
        dialog.resize(760, 560)
        dialog.show()
        QTest.qWait(60)
        self.assertIsInstance(dialog.window(), QQuickView)
        self.assertEqual(dialog.window().status(), QQuickView.Ready)
        return dialog

    def item(self, dialog, name):
        def find(root):
            if root.objectName() == name:
                return root
            for child in root.childItems():
                result = find(child)
                if result is not None:
                    return result
        item = find(dialog.rootObject())
        self.assertIsNotNone(item, name)
        return item

    def click(self, dialog, name):
        item = self.item(dialog, name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
        APP.processEvents()
        return item

    def choose(self, dialog, name, key):
        item = self.item(dialog, name)
        item.forceActiveFocus()
        QTest.keyClick(dialog.window(), key)
        APP.processEvents()

    def test_real_source_x_y_keyboard_mouse_and_empty_y_action_gates(self):
        dialog = self.dialog([self.table(), self.table(name='second.csv')])
        self.assertFalse(dialog.findChildren(QWidget))
        self.assertEqual(dialog.mappings, [(0, [1, 2]), (0, [1, 2])])
        self.click(dialog, 'xyImportSource_1')
        self.assertEqual(dialog.bridge.sourceIndex, 1)
        self.choose(dialog, 'xyImportSources', Qt.Key_Up)
        self.assertEqual(dialog.bridge.sourceIndex, 0)
        self.choose(dialog, 'xyImportX', Qt.Key_End)
        self.assertEqual(dialog.mappings[0], (2, [1]))
        self.assertFalse(self.item(dialog, 'xyImportY_2').property('enabled'))
        y = self.item(dialog, 'xyImportY_1')
        y.forceActiveFocus()
        QTest.keyClick(dialog.window(), Qt.Key_Space)
        self.assertEqual(dialog.mappings[0], (2, []))
        self.assertTrue(self.item(dialog, 'xyImportOnly').property('enabled'))
        self.assertFalse(self.item(dialog, 'xyImportAndCompare').property('enabled'))
        self.click(dialog, 'xyImportOnly')
        self.assertEqual(dialog.result(), dialog.Accepted)
        self.assertFalse(dialog.compare)

    def test_actual_destination_name_edit_comparison_and_duplicate_option(self):
        target = create_comparison(self.workspace, 'Target')
        create_workbook(self.workspace, 'External selection')
        dialog = self.dialog(workbook_id=next(iter(self.workspace.workbooks)))
        self.assertEqual(dialog.destination, 'per_file')
        self.choose(dialog, 'xyImportDestination', Qt.Key_Down)
        self.assertIsNone(dialog.destination)
        editor = self.item(dialog, 'xyImportWorkbookName')
        editor.forceActiveFocus()
        QTest.keyClick(dialog.window(), Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(dialog.window(), Qt.Key_Backspace)
        self.assertFalse(dialog.bridge.canImport)
        self.assertFalse(dialog.bridge.canCompare)
        for character in 'Reviewed import':
            QTest.keyClick(dialog.window(), character)
        self.assertEqual(dialog.workbook_name, 'Reviewed import')
        self.choose(dialog, 'xyImportComparison', Qt.Key_End)
        self.assertEqual(dialog.comparison_id, target.comparison_id)
        self.click(dialog, 'xyImportIncludeExisting')
        self.assertTrue(dialog.include_existing)
        self.assertEqual(self.item(dialog, 'xyImportAndCompare').property('text'), 'Add to comparison')
        self.click(dialog, 'xyImportAndCompare')
        self.assertEqual(dialog.result(), dialog.Accepted)
        self.assertTrue(dialog.compare)
        self.assertFalse(self.workspace.sheets)
        self.assertFalse(target.curves)

    def test_type_override_cache_dat_lock_unavailable_and_cancel_metadata_isolation(self):
        entry = self.entry()
        dialog = self.dialog([entry.table], entries=[entry])
        self.assertFalse(self.item(dialog, 'xyImportX').property('enabled'))
        self.choose(dialog, 'xyImportType', Qt.Key_Home)
        self.assertEqual(dialog.bridge.kind, 'xy')
        self.choose(dialog, 'xyImportX', Qt.Key_End)
        self.click(dialog, 'xyImportY_0')
        self.assertEqual(dialog.mappings[0], (2, [0, 1]))
        self.choose(dialog, 'xyImportType', Qt.Key_End)
        self.assertEqual(dialog.mappings[0], (0, [1, 2]))
        self.choose(dialog, 'xyImportType', Qt.Key_Home)
        self.assertEqual(dialog.mappings[0], (2, [0, 1]))
        self.assertIn('Manual type correction', dialog.bridge.typeReason)
        self.assertEqual(entry.kind, 'dat')
        self.assertIs(dialog.entries[0].tables['dat'].data, entry.tables['dat'].data)
        self.click(dialog, 'xyImportCancel')
        self.assertEqual(entry.kind, 'dat')
        locked = self.entry('retained.csv', retained=True)
        unavailable = PreparedDataFile(Path('only.csv'), 'xy', 'XY only', {'xy': self.table(name='only.csv')},
            option_errors={'dat': 'No complete Gate layout'})
        second = self.dialog([locked.table, unavailable.table], entries=[locked, unavailable])
        self.assertFalse(self.item(second, 'xyImportType').property('enabled'))
        second.bridge.setKind('xy')
        self.assertEqual(second.entries[0].kind, 'dat')
        self.click(second, 'xyImportSource_1')
        self.assertFalse(second.bridge.typeChoices[1]['available'])
        second.bridge.setKind('dat')
        self.assertEqual(second.bridge.feedback, 'No complete Gate layout')
        self.assertEqual(second.entries[1].kind, 'xy')

    def test_matching_exact_unique_names_headerless_and_kind_feedback(self):
        tables = [self.table('t,a,b\n1,2,3\n'), self.table('b,t,a\n3,1,2\n', 'moved.csv'),
            self.table('t,a,a\n1,2,3\n', 'ambiguous.csv')]
        dialog = self.dialog(tables)
        self.click(dialog, 'xyImportApplyMatching')
        self.assertEqual(dialog.mappings, [(0, [1, 2]), (1, [2, 0]), (0, [1, 2])])
        self.assertIn('Applied to 1', dialog.bridge.feedback)
        self.assertIn('ambiguous.csv', dialog.bridge.feedback)
        headerless = self.dialog([self.table('1,2,3\n'), self.table('4,5,6\n', 'same.csv'), self.table('7,8\n', 'short.csv')])
        headerless.bridge.setX(2)
        headerless.bridge.setY(0, True)
        headerless.bridge.setY(1, False)
        self.click(headerless, 'xyImportApplyMatching')
        self.assertEqual(headerless.mappings, [(2, [0]), (2, [0]), (0, [1])])
        a, b = self.entry('dat.csv'), self.entry('xy.csv')
        b.kind = 'xy'
        mixed = self.dialog([a.table, b.table], entries=[a, b])
        self.click(mixed, 'xyImportApplyMatching')
        self.assertIn('skipped 1', mixed.bridge.feedback)

    def test_preview_borrowed_bound_precision_and_retired_on_source_switch(self):
        data = np.arange(200 * 400, dtype=float).reshape(200, 400)
        data[0, 0] = 1.234567890123456
        table = XYTable(data, [f'Column {i}' for i in range(400)], Path('wide.csv'))
        dialog = self.dialog([table, self.table(name='next.csv')])
        model = dialog.bridge.preview
        self.assertIsInstance(model, _PreviewModel)
        self.assertEqual((model.rowCount(), model.columnCount()), (100, 400))
        self.assertTrue(np.shares_memory(model.values, data))
        self.assertEqual(model.data(model.index(0, 0)), '1.23456789012')
        self.assertEqual(model.headerData(399, Qt.Horizontal), 'Column 399')
        preview = self.item(dialog, 'xyImportPreview')
        def delegates(item):
            return sum(1 for child in item.childItems() if child.property('display') is not None) + sum(delegates(child) for child in item.childItems())
        self.assertGreater(delegates(preview), 0)
        self.assertLess(delegates(preview), 200)
        self.click(dialog, 'xyImportSource_1')
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(shiboken6.isValid(model))
        self.assertIs(dialog.bridge.preview.table, dialog.tables[1])

    def test_accept_revalidates_targets_sources_and_mapping_without_stale_permission(self):
        book = create_workbook(self.workspace, 'Destination')
        target = create_comparison(self.workspace, 'Target')
        dialog = self.dialog()
        dialog.bridge.setDestinationIndex(2)
        self.assertTrue(dialog.bridge.canImport)
        del self.workspace.workbooks[book.workbook_id]
        dialog.bridge.commit(False)
        self.assertEqual(dialog.result(), dialog.Rejected)
        self.assertTrue(dialog.window().isVisible())
        dialog.bridge.setDestinationIndex(0)
        dialog.bridge.setComparisonIndex(1)
        self.assertTrue(dialog.bridge.canCompare)
        del self.workspace.comparisons[target.comparison_id]
        dialog.bridge.commit(True)
        self.assertFalse(dialog.compare)
        self.assertEqual(dialog.result(), dialog.Rejected)
        dialog.mappings[0] = (99, [1])
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Rejected)
        entry = self.entry()
        source = self.dialog([entry.table], entries=[entry])
        source.entries[0].tables = {'xy': entry.tables['xy']}
        source.bridge.commit(False)
        self.assertEqual(source.result(), source.Rejected)
        self.assertIn('interpretation', source.bridge.validation)

    def test_fixed_comparison_modal_escape_close_and_parent_lifetime(self):
        target = create_comparison(self.workspace, 'Fixed target')
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        dialog = self.dialog(parent=owner, comparison_id=target.comparison_id)
        self.assertIs(dialog.window().transientParent(), owner.windowHandle())
        self.assertFalse(self.item(dialog, 'xyImportOnly').property('visible'))
        self.assertFalse(self.item(dialog, 'xyImportComparison').property('visible'))
        self.assertEqual(self.item(dialog, 'xyImportFixedComparison').property('text'), 'Fixed target')
        self.assertEqual(self.item(dialog, 'xyImportAndCompare').property('text'), 'Add to comparison')
        results = []
        dialog.finished.connect(results.append)
        QTimer.singleShot(0, lambda: self.click(dialog, 'xyImportAndCompare'))
        self.assertEqual(dialog.exec(), dialog.Accepted)
        dialog.reject()
        self.assertEqual(results, [1])
        escape = self.dialog()
        QTest.keyClick(escape.window(), Qt.Key_Escape)
        self.assertEqual(escape.result(), escape.Rejected)
        closed = self.dialog()
        closed.window().close()
        APP.processEvents()
        self.assertFalse(closed.window().isVisible())
        self.assertEqual(closed.result(), closed.Rejected)
        child_owner = QWidget()
        child = self.dialog(parent=child_owner)
        window = child.window()
        child_owner.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(shiboken6.isValid(child))
        self.assertFalse(shiboken6.isValid(window))

    def test_compact_narrow_and_large_font_preview_actions_are_reachable(self):
        old_font = QFont(APP.font())
        self.addCleanup(APP.setFont, old_font)
        artifact_dir = os.environ.get('XY_IMPORT_SCREENSHOT_DIR')
        for width, height, point_size in ((760, 560, old_font.pointSize()), (600, 560, 13)):
            font = QFont(old_font)
            font.setPointSize(point_size)
            APP.setFont(font)
            entry = self.entry()
            dialog = self.dialog([entry.table], entries=[entry])
            dialog.resize(width, height)
            QTest.qWait(100)
            preview = self.item(dialog, 'xyImportPreview')
            self.assertGreaterEqual(preview.height(), 75)
            for name in ('xyImportOnly', 'xyImportAndCompare', 'xyImportCancel'):
                button = self.item(dialog, name)
                point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
                self.assertTrue(0 <= point.x() < width and 0 <= point.y() < height, name)
            if artifact_dir:
                folder = Path(artifact_dir)
                folder.mkdir(parents=True, exist_ok=True)
                self.assertTrue(dialog.window().grabWindow().save(str(folder / f'xy-import-{width}x{height}-{point_size}pt.png')))
            self.click(dialog, 'xyImportCancel')

    def test_popup_return_selects_only_and_open_dialog_tracks_application_font(self):
        old_font = QFont(APP.font())
        self.addCleanup(APP.setFont, old_font)
        table = self.table(name='sample <b>literal.csv')
        table.column_names[1] = '<i>literal signal</i>'
        dialog = self.dialog([table])
        self.click(dialog, 'xyImportDestination')
        QTest.keyClick(dialog.window(), Qt.Key_Down)
        QTest.keyClick(dialog.window(), Qt.Key_Return)
        APP.processEvents()
        self.assertIsNone(dialog.destination)
        self.assertEqual(dialog.result(), dialog.Rejected)
        self.assertTrue(dialog.window().isVisible())
        font = QFont(old_font)
        font.setPointSize(13)
        APP.setFont(font)
        APP.processEvents()
        info = self.item(dialog, 'xyImportPreviewInfo')
        self.assertEqual(info.property('font').pointSize(), 13)
        expression = QQmlExpression(QQmlEngine.contextForObject(info), info, 'textFormat === 0')
        self.assertTrue(expression.evaluate()[0])
        self.assertIn('literal.csv', info.property('text'))
        self.assertEqual(self.item(dialog, 'xyImportOnly').property('font').pointSize(), 13)
        self.assertEqual(self.item(dialog, 'xyImportPreviewHeaders').height(), 25)


if __name__ == '__main__':
    unittest.main()
