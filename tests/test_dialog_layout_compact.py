"""Small dialogs reserve real reading space for targets and previews."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QEvent, QPoint
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from run_qt import APP_QSS
from spectra_app.dataset_state import Workspace
from spectra_app.workbook_state import create_workbook, register_xy_sheet
from spectra_app.sheet_comparison_dialog import SheetComparisonDialog
from spectra_app.plot_dialogs import ApplyFormatDialog
from spectra_app.plot_templates import capture_format
from tests.test_plot_templates import dataset

APP = QApplication.instance() or QApplication([])


class CompactDialogTests(unittest.TestCase):
    def setUp(self):
        self.old_font, self.old_qss = APP.font(), APP.styleSheet()
        APP.setStyle('Fusion')
        APP.setFont(QFont('Segoe UI', 10))
        APP.setStyleSheet(APP_QSS)
        self.dialog = None
        self.addCleanup(self.dispose)

    def dispose(self):
        if self.dialog:
            self.dialog.close()
            self.dialog.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        APP.setFont(self.old_font)
        APP.setStyleSheet(self.old_qss)

    def show_dialog(self, dialog):
        self.dialog = dialog
        dialog.resize(760, 560)
        dialog.show()
        APP.processEvents()
        self.assertLessEqual(dialog.width(), 760)
        self.assertLessEqual(dialog.height(), 560)

    def assert_on_screen(self, widget):
        self.assertTrue(widget.isVisible())
        self.assertTrue(self.dialog.rect().contains(widget.mapTo(self.dialog, QPoint(0, 0))))
        self.assertTrue(self.dialog.rect().contains(widget.mapTo(self.dialog, widget.rect().bottomRight())))

    def test_sheet_mapping_and_preview_show_multiple_rows_at_small_size(self):
        workspace = Workspace()
        book = create_workbook(workspace, 'Experiment')
        sheet = register_xy_sheet(workspace, book.workbook_id, 'Original',
            np.arange(180, dtype=float).reshape(30, 6), ['X', 'A', 'B', 'C', 'D', 'E'])
        dialog = SheetComparisonDialog(workspace, selected_sheet_ids=[sheet.sheet_id])
        self.show_dialog(dialog)
        self.assertGreaterEqual(dialog.y_list.viewport().height(), 3 * dialog.y_list.sizeHintForRow(0))
        self.assertGreaterEqual(dialog.preview.viewport().height(), 3 * dialog.preview.rowHeight(0))
        self.assert_on_screen(dialog.commit_btn)
        dialog.y_list.item(2).setCheckState(Qt.Checked)
        self.assertIn(2, dialog.selected_mappings()[sheet.sheet_id][1])
        self.assertEqual(dialog.preview.model().data(dialog.preview.model().index(1, 2)), '8')

    def test_apply_targets_and_preview_remain_readable_while_options_update(self):
        sessions = [dataset(f'target-{i}') for i in range(6)]
        for index, session in enumerate(sessions):
            session.source_path = Path(f'target-{index}.dat')
        dialog = ApplyFormatDialog(sessions, sessions[0].dataset_id,
                                   capture_format(sessions[0], 'Format'))
        self.show_dialog(dialog)
        self.assertGreaterEqual(dialog.targets.viewport().height(), 4 * dialog.targets.sizeHintForRow(0))
        self.assertGreaterEqual(dialog.preview_text.viewport().height(), 5 * dialog.preview_text.fontMetrics().lineSpacing())
        self.assert_on_screen(dialog.buttons.button(QDialogButtonBox.Ok))
        dialog.targets.item(1).setCheckState(Qt.Checked)
        dialog.format_ranges_btn.click()
        self.assertIn('target-1', dialog.preview_text.toPlainText())
        self.assertTrue(dialog.options().x_limits)
        self.assertTrue(dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
        dialog.clear_btn.click()
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())


if __name__ == '__main__':
    unittest.main()
