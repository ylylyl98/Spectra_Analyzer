"""Quick-only Workbook contracts at the scene, input, and retained-owner seams."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import subprocess
import sys
import unittest
import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickItem
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, remove_sheet
from spectra_app.workbook_page import WorkbookController, WorkbookPlotCommandsController
from tests.workbook_quick_helpers import pump, item, click_item, click_header, table_key, set_metadata_draft, point_in, selection_ranges

from tests.quick_migration_case import QuickMigrationCase


class WorkbookQuickTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.workspace = self.controller.workspace
        self.book = create_workbook(self.workspace)
        self.sheets = [register_xy_sheet(self.workspace, self.book.workbook_id, f'Sheet {i}',
            np.arange(6000., dtype=float).reshape(300, 20), [f'Column {j}' for j in range(20)]) for i in range(2)]
        self.workbook = self.controller.open_workbook(self.book.workbook_id)
        self.host.window.resize(1440, 800)
        self.commands = self.controller.workbookCommands
        pump()
        self.root = self.controller.graph_documents.content_item(('workbook', self.book.workbook_id, ''))
        self.commands_root = item(self.host, 'workbookPlot_line').parentItem().parentItem()
        self.addCleanup(lambda: self.assertFalse(self.quick_errors, self.quick_errors))

    def test_production_hosts_have_real_quick_scene_and_no_native_table_controls(self):
        self.assertTrue(self.host.window.isVisible())
        self.assertIsInstance(self.root, QQuickItem)
        self.assertIsInstance(self.workbook, WorkbookController)
        self.assertIs(self.root.property('bridge'), self.workbook)
        self.assertIs(self.root.property('sheet'), self.workbook.current_sheet)
        self.assertIs(self.commands.sheet_page, self.workbook.current_sheet)
        self.assertIsInstance(self.commands, WorkbookPlotCommandsController)
        self.assertIsInstance(self.commands_root, QQuickItem)
        self.assertIs(self.commands_root.property('bridge'), self.commands)
        self.assertIs(self.commands.boundSheet, self.workbook.current_sheet)
        self.assertIsNone(self.host.engine.rootContext().contextProperty('appBridge'))
        self.assertIs(self.workbook.current_sheet.parent(), self.workbook)
        # The shared production fixture forbids every QWidget constructor.
        from PySide6.QtWidgets import QWidget
        self.assertFalse(self.host.findChildren(QWidget))
        self.assertEqual(item(self.host, 'sheetHeaders').property('rows'), 3)
        self.assertEqual(item(self.host, 'sheetCells').property('columns'), 20)

    def test_production_command_adapter_forwards_controller_signals_once(self):
        target = create_comparison(self.workspace)
        identity = target.comparison_id
        calls = dict(selected=[], pin=[], show=[], repeat=[])
        self.commands.targetSelected.connect(calls['selected'].append)
        self.commands.targetPinChanged.connect(calls['pin'].append)
        self.commands.targetShowRequested.connect(lambda: calls['show'].append(True))
        self.commands.repeatRequested.connect(lambda *args: calls['repeat'].append(args))
        self.commands.chooseTarget(identity)
        self.commands.pinTarget(True)
        self.commands.showTarget()
        self.assertEqual(self.controller.contextKind, 'xy')
        self.controller.open_workbook(self.book.workbook_id)
        self.commands.repeat()
        self.controller.open_workbook(self.book.workbook_id)
        self.assertEqual(calls, dict(selected=[identity], pin=[True], show=[True],
            repeat=[(self.workbook.current_sheet.sheet_id, identity)]))
        self.assertIs(self.commands.boundSheet, self.workbook.current_sheet)
        self.assertTrue(self.commands.targetPinned)
        self.assertIs(self.controller.workbookCommands, self.commands)
        self.assertIs(self.commands.boundSheet, self.workbook.current_sheet)
        self.commands.bind(self.workbook.current_sheet)
        self.commands.bind(self.workbook.current_sheet)
        states = []
        self.commands.stateChanged.connect(lambda: states.append(True))
        self.workbook.current_sheet.stateChanged.emit()
        self.assertEqual(states, [True])

    def test_actual_sheet_navigation_retains_ranges_current_scroll_and_manual_widths(self):
        page = self.workbook.current_sheet
        click_header(page, 2)
        page.resizeColumn(2, 87)
        page.setCurrentCell(8, 2)
        body = item(self.host, 'sheetCells')
        body.setProperty('contentX', 300.)
        body.setProperty('contentY', 640.)
        pump()
        state = page.capture_view_state()
        self.assertEqual(state['scroll'], (300., 640.))
        click_item(self.host, 'workbookSheet_' + self.sheets[1].sheet_id)
        self.assertIs(self.workbook.current_sheet, self.workbook.sheet_pages[self.sheets[1].sheet_id])
        self.assertEqual(self.workbook.current_sheet.scroll_x, 0)
        body.setProperty('contentX', 100.)
        body.setProperty('contentY', 260.)
        pump()
        click_item(self.host, 'workbookSheet_' + self.sheets[0].sheet_id)
        self.assertIs(self.workbook.current_sheet, page)
        self.assertEqual(page.capture_view_state(), state)
        self.assertEqual((body.property('contentX'), body.property('contentY')), state['scroll'])
        self.assertEqual(item(self.host, 'sheetHeaders').property('contentX'), body.property('contentX'))

    def test_invalid_draft_blocks_sheet_switch_save_and_guarded_commands(self):
        page = self.workbook.current_sheet
        page.editMetadata(1, 'long_name')
        set_metadata_draft(page, 'two\nlines')
        self.assertFalse(self.workbook.select_sheet(self.sheets[1].sheet_id))
        self.assertIs(self.workbook.current_sheet, page)
        with self.assertRaisesRegex(ValueError, 'Correct the Sheet'):
            self.workbook.finish_editor()
        self.assertEqual(page.editorColumn, 1)
        self.assertTrue(item(self.host, 'sheetMetadataEditor').hasActiveFocus())
        plots = []
        page.quickPlotRequested.connect(lambda *args: plots.append(args))
        self.commands.plot('line')
        self.assertFalse(plots)
        page.cancelEditor()
        self.assertTrue(self.workbook.select_sheet(self.sheets[1].sheet_id))

    def test_owner_guard_blocks_popup_selection_mapping_resize_and_plot_mutations(self):
        page = self.workbook.current_sheet
        target = create_comparison(self.workspace)
        page.refresh_targets(target.comparison_id)
        called = []
        self.workbook.set_input_guard(lambda: called.append(True) or False)
        before = (selection_ranges(page), page.xColumn, page.columnWidth(1), page.yColumns)
        page.beginHeader(2, 0); page.endHeader()
        page.setX(2); page.resizeColumn(1, 45)
        page.appendTo(target.comparison_id)
        self.commands.pinTarget(True)
        self.commands.chooseTarget(target.comparison_id)
        self.assertEqual((selection_ranges(page), page.xColumn, page.columnWidth(1), page.yColumns), before)
        self.assertFalse(self.commands.targetPinned)
        self.assertGreater(len(called), 4)

    def test_retired_sheet_rejects_late_popup_and_model_keeps_header_reference(self):
        page = self.workbook.current_sheet
        model, label = page.model, page.model.header_rows(1)
        remove_sheet(self.workspace, page.sheet_id)
        self.workbook.refresh()
        page.setX(3); page.setY(2, False); page.plot('line')
        self.assertEqual(model.header_rows(1), label)
        self.assertNotEqual(self.workbook.current_sheet.sheet_id, page.sheet_id)

    def test_body_rectangle_and_row_drag_copy_without_changing_intended_y(self):
        page = self.workbook.current_sheet
        click_header(page, 2)
        intended = list(page.yColumns)
        body = item(self.host, 'sheetCells')
        row_height = item(self.host, 'sheetHeaders').height() / 3
        start = point_in(self.host, 'sheetCells', 12, 12)
        end = point_in(self.host, 'sheetCells', page.columnWidth(0) + 12, row_height * 2 + 12)
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.host.window, end)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=end)
        pump()
        self.assertEqual(page.yColumns, intended)
        self.assertEqual(page.copy_selection(), '0\t1\n20\t21\n40\t41')
        start = point_in(self.host, 'sheetCells', -25, 12)
        end = point_in(self.host, 'sheetCells', -25, row_height * 2 + 12)
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.host.window, end)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=end)
        pump()
        self.assertEqual(page.yColumns, intended)
        self.assertEqual(selection_ranges(page), [(0, 0, 2, 19)])

    def test_normal_refresh_resets_view_while_explicit_restore_clips_without_gate_callbacks(self):
        page = self.workbook.current_sheet
        page.beginBody(5, 2, 0); page.dragBody(299, 3)
        page.rememberScroll(100, 500)
        state = page.capture_view_state()
        self.sheets[0].data = self.sheets[0].data[:20]
        page.refresh()
        self.assertFalse(page.selection_model.hasSelection())
        gates = []
        page.gateRequested.connect(lambda *args: gates.append(args))
        page.restore_view_state(state)
        self.assertEqual(selection_ranges(page), [(5, 2, 19, 3)])
        self.assertEqual(gates, [])

    def test_scrolled_header_and_body_hit_test_use_view_coordinates_once(self):
        page = self.workbook.current_sheet
        body = item(self.host, 'sheetCells')
        row_height = item(self.host, 'sheetHeaders').height() / 3
        body.setProperty('contentX', page.columnPosition(3))
        body.setProperty('contentY', row_height * 10)
        pump()
        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=point_in(self.host, 'sheetCells', 12, 12))
        pump()
        self.assertEqual((page.currentRow, page.currentColumn), (10, 3))
        self.assertEqual(page.copy_selection(), '203')
        click_header(page, 4)
        self.assertEqual(page.yColumns, [4])
        self.assertEqual(page.currentColumn, 4)

    def test_keyboard_crosses_both_viewport_edges_extends_body_selection_and_reveals_f2(self):
        page = self.workbook.current_sheet
        page.beginBody(0, 0, 0)
        intended = list(page.yColumns)
        for _ in range(24):
            table_key(page, Qt.Key_Down)
        for _ in range(12):
            table_key(page, Qt.Key_Right)
        body = item(self.host, 'sheetCells')
        row_height = item(self.host, 'sheetHeaders').height() / 3
        self.assertEqual((page.currentRow, page.currentColumn), (24, 12))
        self.assertGreater(body.property('contentX'), 0)
        self.assertGreater(body.property('contentY'), 0)
        self.assertLessEqual(page.columnPosition(12) + page.columnWidth(12), body.property('contentX') + body.width() + 1)
        self.assertLessEqual(25 * row_height, body.property('contentY') + body.height() + 1)
        table_key(page, Qt.Key_Right, Qt.ShiftModifier)
        table_key(page, Qt.Key_Down, Qt.ShiftModifier)
        self.assertEqual(selection_ranges(page), [(24, 12, 25, 13)])
        self.assertEqual(page.yColumns, intended)
        body.setProperty('contentX', 0.)
        table_key(page, Qt.Key_F2)
        editor = item(self.host, 'sheetMetadataEditor')
        self.assertTrue(editor.hasActiveFocus())
        self.assertGreaterEqual(editor.x(), 0)
        viewport = item(self.root, 'workbookContentViewport')
        corner = editor.mapToItem(viewport, QPointF())
        self.assertGreaterEqual(corner.x(), 0)
        self.assertGreaterEqual(corner.y(), 0)
        self.assertLessEqual(corner.x() + editor.width(), viewport.width())
        self.assertLessEqual(corner.y() + editor.height(), viewport.height())
        page.cancelEditor()


class WorkbookCommandSizingTests(unittest.TestCase):
    def run_quick_case(self, name):
        # Each sizing case owns a separate production QGuiApplication lifecycle.
        result = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8',
             str(Path(__file__).with_name('workbook_command_sizing_probe.py')), name],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
            timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn(name + ': PASS after Quick teardown', result.stdout)

    def test_current_formal_quick_root_controls_height_without_native_remeasurement(self):
        self.run_quick_case('formal_height')

    def test_hidden_wrapping_commands_do_not_resize_other_documents_or_remeasure_native_toolbar(self):
        self.run_quick_case('hidden_wrapping')
