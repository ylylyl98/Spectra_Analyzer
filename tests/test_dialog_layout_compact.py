"""Small production Quick dialogs reserve real reading space."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPointF
from PySide6.QtGui import QFont, QGuiApplication
from spectra_app.dataset_state import Workspace
from spectra_app.workbook_state import create_workbook, register_xy_sheet
from spectra_app.sheet_comparison_dialog import SheetComparisonDialog
from spectra_app.plot_dialogs import ApplyFormatDialog
from spectra_app.plot_templates import capture_format
from tests.test_plot_templates import dataset
from tests.quick_migration_case import QuickMigrationCase
from tests.formal_quick_helpers import item, pump


def sheet_control(dialog, name):
    return item(dialog, name)


def settle_sheet(dialog):
    dialog.window().update()
    pump()


class CompactDialogTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        self.old_font = QGuiApplication.font()
        QGuiApplication.setFont(QFont('Segoe UI', 10))
        self.dialog = None
        self.addCleanup(self.dispose)

    def dispose(self):
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        QGuiApplication.setFont(self.old_font)

    def show_dialog(self, dialog):
        self.dialog = dialog
        dialog.resize(760, 560)
        dialog.show()
        pump()
        self.assertLessEqual(dialog.width(), 760)
        self.assertLessEqual(dialog.height(), 560)

    def test_sheet_mapping_and_preview_show_multiple_rows_at_small_size(self):
        workspace = Workspace()
        book = create_workbook(workspace, 'Experiment')
        sheet = register_xy_sheet(workspace, book.workbook_id, 'Original',
            np.arange(180, dtype=float).reshape(30, 6), ['X', 'A', 'B', 'C', 'D', 'E'])
        dialog = SheetComparisonDialog(workspace, selected_sheet_ids=[sheet.sheet_id])
        self.show_dialog(dialog)
        settle_sheet(dialog)
        columns = sheet_control(dialog, 'sheetYColumns')
        preview = sheet_control(dialog, 'sheetPreview')
        button = sheet_control(dialog, 'sheetAccept')
        self.assertGreaterEqual(columns.property('height'), 3 * 28)
        self.assertGreaterEqual(preview.property('height'), 3 * 26)
        point = button.mapToScene(QPointF(0, 0))
        self.assertGreaterEqual(point.y(), 0)
        self.assertLessEqual(point.y() + button.property('height'), dialog.height())
        dialog.bridge.setY(2, True)
        self.assertIn(2, dialog.selected_mappings()[sheet.sheet_id][1])
        self.assertEqual(dialog.bridge.previewModel.data(dialog.bridge.previewModel.index(1, 2)), '8')

    def test_apply_targets_and_preview_remain_readable_while_options_update(self):
        sessions = [dataset(f'target-{i}') for i in range(6)]
        for index, session in enumerate(sessions):
            session.source_path = Path(f'target-{index}.dat')
        dialog = ApplyFormatDialog(sessions, sessions[0].dataset_id,
                                   capture_format(sessions[0], 'Format'))
        self.show_dialog(dialog)
        root = dialog.rootObject()
        targets = item(root, 'plotTargetsScroll')
        changes = item(root, 'formatChangesScroll')
        button = item(root, 'dialogAccept')
        self.assertGreaterEqual(targets.property('height'), 4 * 28)
        self.assertGreaterEqual(changes.property('height'), 5 * 14)
        point = button.mapToScene(QPointF(0, 0))
        self.assertGreaterEqual(point.y(), 0)
        self.assertLessEqual(point.y() + button.property('height'), dialog.height())
        dialog.bridge.targets[1].setChecked(True)
        dialog.bridge.quickOptions(True)
        self.assertIn('target-1', dialog.bridge.previewText)
        self.assertTrue(dialog.options().x_limits)
        self.assertTrue(button.property('enabled'))
        dialog.bridge.selectAll(False)
        self.assertFalse(button.property('enabled'))


if __name__ == '__main__':
    import unittest
    unittest.main()
