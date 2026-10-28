import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from spectra_app.dataset_state import Workspace
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from tests.xy_helpers import set_xy_draft, select_xy_rows

APP = QApplication.instance() or QApplication([])


class XYPlotWorkflowTests(unittest.TestCase):
    def test_curve_controls_export_and_nonfinite_gaps(self):
        from spectra_app.xy_plot_page import XYPlotPage
        workspace = Workspace()
        book = create_workbook(workspace)
        sheet = register_xy_sheet(workspace, book.workbook_id, 'Source',
            np.array([[1.,10.], [2.,np.nan], [3.,30.]]), ['Time','Signal'])
        comparison = create_comparison(workspace)
        first = add_curve(workspace, comparison.comparison_id, sheet.sheet_id, 0, 1)
        second = add_curve(workspace, comparison.comparison_id, sheet.sheet_id, 0, 1)
        page = XYPlotPage(workspace, comparison.comparison_id)
        self.addCleanup(page.deleteLater)
        page.resize(620, 500)
        page.show()
        APP.processEvents()
        select_xy_rows(page, [0])
        set_xy_draft(page, 'label', 'Measured', commit=True)
        set_xy_draft(page, 'color', '#dd3300', commit=True)
        set_xy_draft(page, 'width', '2.5', commit=True)
        page.set_curve_visibility(second.curve_id, False)
        self.assertEqual((first.label, first.color, first.width), ('Measured','#dd3300',2.5))
        self.assertFalse(second.visible)
        self.assertEqual(len(page.axes.lines), 1)
        self.assertTrue(np.isnan(page.axes.lines[0].get_ydata()[1]))
        page.resize(620, 500)
        page.show()
        APP.processEvents()
        page.canvas.draw()
        label_bounds = page.axes.yaxis.label.get_window_extent(page.canvas.get_renderer())
        self.assertGreaterEqual(label_bounds.x0, 0)
        with TemporaryDirectory() as directory:
            svg = page.export(Path(directory) / 'figure.svg')
            png = page.export(Path(directory) / 'figure.png')
            self.assertIn('<svg', svg.read_text(encoding='utf-8'))
            self.assertIn('Measured', svg.read_text(encoding='utf-8'))
            image = QImage(str(png))
            self.assertGreater(image.width(), 400)
            self.assertGreater(image.height(), 300)
            with self.assertRaises(FileExistsError):
                page.export(png)
        page.inspector_bridge.action('remove')
        self.assertEqual(len(comparison.curves), 1)
        second.width = .123456789012345
        page.refresh()
        page.finish_editor()
        self.assertEqual(second.width, .123456789012345)
        self.assertFalse(page.has_pending_project_edits())
