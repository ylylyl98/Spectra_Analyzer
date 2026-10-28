"""Real Qt Quick Sheet mapping controls and staged workspace boundaries."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import unittest

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtQuick import QQuickView
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from spectra_app.dataset_state import Workspace
from spectra_app.fitting import FitResult
from spectra_app.sheet_comparison_dialog import SheetComparisonDialog, _SheetPreviewModel
from spectra_app.workbook_state import (create_workbook, register_xy_sheet, create_comparison,
                                        ensure_dataset_sheet, add_curve)
from tests.test_plot_templates import dataset

APP = QApplication.instance() or QApplication([])


def settle_sheet(dialog):
    for _ in range(4):
        APP.processEvents()
    dialog.window().update()
    QTest.qWait(10)


def sheet_control(dialog, name):
    result = dialog.rootObject().findChild(QObject, name)
    if result is not None:
        return result
    def visit(item):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = visit(child)
            if found is not None:
                return found
    result = visit(dialog.rootObject())
    if result is None:
        raise AssertionError(f'Missing Sheet QML control: {name}')
    return result


def click_sheet_control(dialog, name):
    settle_sheet(dialog)
    item = sheet_control(dialog, name)
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    QTest.mouseClick(dialog.window(), Qt.LeftButton, Qt.NoModifier, point)
    settle_sheet(dialog)
    return item


def set_sheet_destination(dialog, comparison_id):
    dialog.bridge.setDestination(next(i for i, row in enumerate(dialog.bridge.destinations)
                                      if row['value'] == (comparison_id or '')))


def set_sheet_mapping(dialog, sheet_id, x, ys, *, include=True):
    bridge = dialog.bridge
    bridge.selectSource(sheet_id)
    if (sheet_id in dialog.selected_mappings()) != include:
        bridge.toggleSource(sheet_id)
    bridge.setX(x)
    for row in list(bridge._column_model.rows):
        bridge.setY(row['column'], row['column'] in ys)


class SheetComparisonQuickTests(unittest.TestCase):
    def setUp(self):
        self.old_font = APP.font()
        APP.setFont(QFont('Segoe UI', 10))
        self.dialogs = []
        self.addCleanup(self.dispose)

    def dispose(self):
        for dialog in self.dialogs:
            dialog.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        APP.setFont(self.old_font)

    def xy(self):
        workspace = Workspace()
        book = create_workbook(workspace, 'Experiment')
        sheets = [register_xy_sheet(workspace, book.workbook_id, name,
            np.arange(900.).reshape(150, 6), list('XABCDE')) for name in ('Original', 'Results')]
        sheets[1].role = 'snapshot'
        return workspace, book, sheets

    def keep(self, workspace, **kwargs):
        dialog = SheetComparisonDialog(workspace, **kwargs)
        self.dialogs.append(dialog)
        return dialog

    def show(self, dialog, width=760, height=560):
        dialog.resize(width, height)
        dialog.show()
        settle_sheet(dialog)

    def test_real_pointer_and_keyboard_source_tristate_originals_xy_and_result_copy(self):
        workspace, book, (original, result) = self.xy()
        before = copy.deepcopy([(s.x_column, s.y_columns, s.mode) for s in workspace.sheets.values()])
        dialog = self.keep(workspace, selected_sheet_ids=[original.sheet_id])
        self.show(dialog)
        self.assertIsInstance(dialog.window(), QQuickView)
        self.assertNotIsInstance(dialog, QWidget)
        self.assertFalse(dialog.findChildren(QWidget))
        self.assertEqual(dialog.bridge._source_model.rows[0]['checkState'], 1)
        click_sheet_control(dialog, 'sheetSourceCheck_0')
        self.assertEqual(list(dialog.selected_mappings()), [original.sheet_id, result.sheet_id])
        self.assertEqual(dialog.bridge._source_model.rows[0]['checkState'], 2)
        click_sheet_control(dialog, 'sheetSource_2')
        source_list = sheet_control(dialog, 'sheetSources')
        self.assertEqual(dialog.bridge.currentId, result.sheet_id)
        source_list.forceActiveFocus()
        QTest.keyClick(dialog.window(), Qt.Key_Space)
        self.assertEqual(dialog.bridge._source_model.rows[0]['checkState'], 1)
        self.assertNotIn(result.sheet_id, dialog.selected_mappings())
        # Editing a source does not include it implicitly.
        x_combo = sheet_control(dialog, 'sheetXColumn')
        x_combo.forceActiveFocus()
        QTest.keyClick(dialog.window(), Qt.Key_Down)
        settle_sheet(dialog)
        self.assertEqual(dialog.bridge._choices[result.sheet_id][0], 1)
        self.assertNotIn(1, dialog.bridge._choices[result.sheet_id][1])
        click_sheet_control(dialog, 'sheetY_2')
        self.assertNotIn(2, dialog.bridge._choices[result.sheet_id][1])
        self.assertNotIn(result.sheet_id, dialog.selected_mappings())
        click_sheet_control(dialog, 'sheetOriginals')
        self.assertEqual(list(dialog.selected_mappings()), [original.sheet_id])
        copied = dialog.selected_mappings()
        copied[original.sheet_id][1].clear()
        self.assertTrue(dialog.selected_mappings()[original.sheet_id][1])
        click_sheet_control(dialog, 'sheetCancel')
        self.assertEqual(dialog.result(), dialog.Rejected)
        self.assertEqual(before, [(s.x_column, s.y_columns, s.mode) for s in workspace.sheets.values()])
        self.assertFalse(workspace.comparisons)

    def test_modal_pointer_accept_cancel_escape_and_window_close(self):
        workspace, _, sheets = self.xy()
        for action, expected in [('sheetAccept', 1), ('sheetCancel', 0), ('escape', 0), ('close', 0)]:
            dialog = self.keep(workspace, selected_sheet_ids=[sheets[0].sheet_id])
            errors = []
            def interact():
                try:
                    self.assertIs(APP.modalWindow(), dialog.window())
                    if action == 'escape':
                        QTest.keyClick(dialog.window(), Qt.Key_Escape)
                    elif action == 'close':
                        dialog.window().close()
                    else:
                        click_sheet_control(dialog, action)
                except BaseException as exc:
                    errors.append(exc)
                    dialog.reject()
            QTimer.singleShot(0, interact)
            self.assertEqual(dialog.exec(), expected)
            if errors:
                raise errors[0]
            self.assertFalse(dialog.window().isVisible())
        self.assertFalse(workspace.comparisons, 'Acceptance publishes choices only')

    def test_live_accept_rejects_removed_owner_target_changed_rows_and_duplicate_zero(self):
        workspace, book, sheets = self.xy()
        sheet = sheets[0]
        target = create_comparison(workspace, 'Target')
        dialog = self.keep(workspace, selected_sheet_ids=[sheet.sheet_id], comparison_id=target.comparison_id)
        self.assertTrue(dialog.bridge.canAccept)
        book.sheet_ids.remove(sheet.sheet_id)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Rejected)
        self.assertIn('no longer exists', dialog.bridge.feedback)
        book.sheet_ids.insert(0, sheet.sheet_id)
        dialog.bridge.validate()
        self.assertTrue(dialog.bridge.canAccept)
        workspace.comparisons.pop(target.comparison_id)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Rejected)
        self.assertIn('destination', dialog.bridge.feedback)
        workspace.comparisons[target.comparison_id] = target
        old_data = sheet.data
        sheet.data = old_data[:0]
        dialog.accept()
        self.assertIn('No current results', dialog.bridge.feedback)
        sheet.data = old_data
        for y in sheet.y_columns:
            add_curve(workspace, target.comparison_id, sheet.sheet_id, 0, y)
        dialog.bridge.validate()
        self.assertFalse(dialog.bridge.canAccept)
        self.assertIn('0 new', dialog.bridge.summary)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Rejected)
        dialog.bridge.setIncludeExisting(True)
        self.assertTrue(dialog.include_existing)
        self.assertIn('5 to add', dialog.bridge.summary)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Accepted)
        self.assertEqual(len(target.curves), 5)

    def test_preview_borrows_bounded_columns_and_only_current_model_survives(self):
        workspace, _, sheets = self.xy()
        session = dataset('gates')
        workspace.datasets[session.dataset_id] = session
        dat = ensure_dataset_sheet(workspace, session.dataset_id, 'original')
        dialog = self.keep(workspace, selected_sheet_ids=[sheets[0].sheet_id])
        model = dialog.bridge.previewModel
        self.assertEqual(model.rowCount(), 100)
        self.assertTrue(np.shares_memory(model.values[2], sheets[0].data))
        self.assertEqual(model.data(model.index(1, 2)), '8')
        self.assertEqual(model.headerData(2, Qt.Horizontal), 'B')
        dialog.bridge.selectSource(dat.sheet_id)
        dat_model = dialog.bridge.previewModel
        self.assertTrue(np.shares_memory(dat_model.values[2], session.base_cube.z))
        self.assertFalse(dialog.bridge.xEditable)
        dialog.bridge.setX(1)
        self.assertEqual(dialog.bridge._choices[dat.sheet_id][0], 0)
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(shiboken6.isValid(model))
        self.assertEqual(dialog.bridge.findChildren(_SheetPreviewModel), [dat_model])
        dialog.bridge.selectSource(sheets[1].sheet_id)
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(shiboken6.isValid(dat_model))

    def test_numeric_columns_and_fit_parameter_x_exception_keep_fixed_role(self):
        workspace, _, sheets = self.xy()
        sheet = sheets[0]
        sheet.text_columns = {2: ['label'] * 150}
        sheet.y_columns = [1, 3, 4, 5]
        session = dataset('fit-source')
        session.fit_result = FitResult('Gaussian', session.base_cube.energy,
            session.base_cube.z[0], np.array([session.base_cube.z[0]]),
            np.array([2.]), np.array([1.]), np.array([.3]), 0., 0., 1.)
        session.fit_gate_idx = 0
        workspace.datasets[session.dataset_id] = session
        fit = ensure_dataset_sheet(workspace, session.dataset_id, 'fit_parameters')
        dialog = self.keep(workspace, selected_sheet_ids=[sheet.sheet_id])
        self.assertNotIn(2, [row['column'] for row in dialog.bridge.xOptions])
        self.assertFalse(dialog.bridge._column_model.rows[2]['available'])
        before = copy.deepcopy(dialog.bridge._choices[sheet.sheet_id])
        dialog.bridge.setX(2)
        dialog.bridge.setY(2, True)
        self.assertEqual(dialog.bridge._choices[sheet.sheet_id], before)
        dialog.bridge.selectSource(fit.sheet_id)
        self.assertTrue(dialog.bridge.xEditable)
        self.assertEqual(dialog.bridge.valueLabel, 'Fit Parameters')
        self.assertNotIn(1, [row['column'] for row in dialog.bridge.xOptions])
        self.assertNotIn(2, [row['column'] for row in dialog.bridge.xOptions])
        dialog.bridge.setX(3)
        self.assertEqual(dialog.bridge._choices[fit.sheet_id][0], 3)
        self.assertEqual(dialog.bridge._choices[fit.sheet_id][2], fit.mode)
        self.assertEqual(fit.x_column, 0, 'The editor changes draft choices only')

    def test_markup_looking_source_column_and_cell_text_stay_literal(self):
        workspace, book, sheets = self.xy()
        book.name = '<b>Experiment</b>'
        sheet = sheets[0]
        sheet.name = '<b>Original</b>'
        sheet.column_names[2] = '<b>Label</b>'
        sheet.text_columns = {2: ['<b>value</b>'] * 150}
        sheet.y_columns = [1, 3, 4, 5]
        dialog = self.keep(workspace, selected_sheet_ids=[sheet.sheet_id])
        self.show(dialog)
        for name, expected in [('sheetSourceLabel_1', '<b>Original</b>'),
                               ('sheetPreviewHeaderText_2', '<b>Label</b>'),
                               ('sheetPreviewCellText_0_2', '<b>value</b>')]:
            item = sheet_control(dialog, name)
            self.assertEqual(item.property('text'), expected)
            expression = QQmlExpression(QQmlEngine.contextForObject(item), item, 'textFormat === Text.PlainText')
            self.assertEqual(expression.evaluate(), (True, False))
        click_sheet_control(dialog, 'sheetSource_1')
        self.assertEqual(dialog.bridge.currentId, sheet.sheet_id)
        self.assertIn('<b>Original</b>', dialog.bridge.provenance)

    def test_narrow_and_enlarged_font_keep_controls_and_three_preview_rows(self):
        workspace, _, sheets = self.xy()
        dialog = self.keep(workspace, selected_sheet_ids=[sheets[0].sheet_id])
        for width, font_size in [(760, 10), (760, 14), (520, 12)]:
            APP.setFont(QFont('Segoe UI', font_size))
            self.show(dialog, width)
            self.assertEqual(sheet_control(dialog, 'sheetXColumn').property('font').pointSizeF(), font_size)
            if width < 650:
                click_sheet_control(dialog, 'sheetSourcesTab')
                self.assertTrue(sheet_control(dialog, 'sheetSourcesTab').property('checked'))
                self.assertTrue(sheet_control(dialog, 'sheetSources').isVisible())
                self.assertFalse(sheet_control(dialog, 'sheetPreview').isVisible())
                click_sheet_control(dialog, 'sheetColumnsTab')
                self.assertEqual(sheet_control(dialog, 'sheetNarrowTabs').property('currentIndex'), 1)
                self.assertTrue(sheet_control(dialog, 'sheetColumnsTab').property('checked'))
                self.assertFalse(sheet_control(dialog, 'sheetSourcesTab').property('checked'))
                self.assertTrue(sheet_control(dialog, 'sheetPreview').isVisible())
                self.assertFalse(sheet_control(dialog, 'sheetSources').isVisible())
            preview = sheet_control(dialog, 'sheetPreview')
            y_columns = sheet_control(dialog, 'sheetYColumns')
            self.assertGreaterEqual(preview.height(), 3 * max(26, font_size * 1.4 + 8))
            self.assertGreaterEqual(y_columns.height(), 3 * 28)
            for name in ('sheetXColumn', 'sheetMatchGates', 'sheetAccept', 'sheetCancel', 'sheetIncludeExisting'):
                control = sheet_control(dialog, name)
                point = control.mapToScene(QPointF(0, 0))
                self.assertTrue(control.isVisible(), name)
                self.assertGreaterEqual(point.x(), 0, name)
                self.assertGreaterEqual(point.y(), 0, name)
                self.assertLessEqual(point.x() + control.width(), width + 1, name)
                self.assertLessEqual(point.y() + control.height(), dialog.height() + 1, name)


if __name__ == '__main__':
    unittest.main()
