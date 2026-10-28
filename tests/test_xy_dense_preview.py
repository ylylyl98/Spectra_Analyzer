"""Bounded preview rendering never becomes an analysis/export input."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from spectra_app.dataset_state import Workspace
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_analysis import capture_input, AnalysisSettings
from spectra_app.xy_plot_page import XYPlotPage, draw_comparison
from tests.xy_helpers import set_xy_draft, select_xy_rows

APP = QApplication.instance() or QApplication([])


class XYDensePreviewTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        book = create_workbook(self.workspace)
        x = np.arange(100001., dtype=float)
        y = np.sin(x / 100)
        y[12345], y[87654] = 500., -400.
        y[45000:45040] = np.nan
        self.sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Dense',
            np.column_stack((x, y)), ['X', 'Y'])
        self.graph = create_comparison(self.workspace)
        self.curve = add_curve(self.workspace, self.graph.comparison_id, self.sheet.sheet_id, 0, 1)
        self.page = XYPlotPage(self.workspace, self.graph.comparison_id)
        self.addCleanup(self.page.dispose)
        self.addCleanup(self.page.deleteLater)

    def test_preview_bounded_extrema_and_gaps_full_resolution_analysis_and_exports(self):
        preview = self.page.axes.lines[0]
        self.assertLessEqual(len(preview.get_xdata()), 8000)
        self.assertEqual(np.nanmax(preview.get_ydata()), 500.)
        self.assertEqual(np.nanmin(preview.get_ydata()), -400.)
        xy = preview.get_xydata()
        finite = np.isfinite(xy).all(axis=1)
        self.assertTrue(np.isnan(xy).any())
        for left, right, both in zip(xy[:-1], xy[1:], finite[:-1] & finite[1:]):
            if both:
                self.assertFalse(left[0] < 45000 and right[0] >= 45040)
        full = draw_comparison(Figure(), self.workspace, self.graph)
        self.assertEqual(len(full.lines[0].get_xdata()), len(self.sheet.data))
        captured = capture_input(self.workspace, self.graph, self.curve.curve_id, AnalysisSettings())
        self.assertEqual(len(captured.data.x), len(self.sheet.data))
        self.assertEqual(int(captured.data.valid.sum()), len(self.sheet.data) - 40)
        rendered = []
        original = draw_comparison
        def record(*args, **kwargs):
            axes = original(*args, **kwargs)
            rendered.append(len(axes.lines[0].get_xdata()))
            return axes
        with TemporaryDirectory() as folder, patch('spectra_app.xy_plot_controller.draw_comparison', side_effect=record):
            self.page.export(Path(folder) / 'dense.png')
            self.page.export(Path(folder) / 'dense.svg')
            self.page.svg_bytes()
        self.assertEqual(rendered, [len(self.sheet.data)] * 3)
        self.assertTrue(np.isnan(self.sheet.data[45000:45040, 1]).all())

    def test_style_edit_reuses_artists_data_and_zoom_without_prepare(self):
        page = self.page
        page.resize(1100, 760)
        page.show()
        APP.processEvents()
        self.assertTrue(page.inspector_bridge.enabled)
        line, axes = page.axes.lines[0], page.axes
        xdata = line.get_xdata()
        select_xy_rows(page, [0])
        page.axes.set_xlim(1000., 2000.); page.axes.set_ylim(-1., 1.)
        page.toolbar.push_current()
        history_size = len(page.toolbar._nav_stack)
        with patch('spectra_app.xy_plot_controller.prepare_comparison', side_effect=AssertionError('full prepare')):
            page.controller._apply_batch_style({self.curve.curve_id}, 'color', '#abcdef')
            self.assertTrue(set_xy_draft(page, 'plot_title', 'Title', commit=True))
            self.assertTrue(set_xy_draft(page, 'width', '3', commit=True))
        self.assertIs(page.axes, axes)
        self.assertIs(page.axes.lines[0], line)
        self.assertIs(line.get_xdata(), xdata)
        self.assertEqual(line.get_color(), '#abcdef')
        self.assertEqual(line.get_linewidth(), 4.5)
        self.assertEqual(axes.get_xlim(), (1000., 2000.))
        self.assertEqual(axes.get_ylim(), (-1., 1.))
        self.assertEqual(len(page.toolbar._nav_stack), history_size)
        self.assertEqual(axes.get_title(), 'Title')

    def test_small_curves_keep_all_points_and_gap_explosion_stays_bounded(self):
        from spectra_app.xy_preview import sample_preview_curve
        x = np.arange(8.)
        y = x ** 2
        shown_x, shown_y = sample_preview_curve(x, y)
        self.assertIs(shown_x, x)
        self.assertIs(shown_y, y)
        x = np.arange(100001.)
        y = np.where(x % 2, np.nan, x)
        shown_x, shown_y = sample_preview_curve(x, y)
        self.assertLessEqual(len(shown_x), 8000)
        finite = np.isfinite(shown_y)
        self.assertFalse(np.any(finite[:-1] & finite[1:]))

    def test_manual_and_common_domains_keep_every_usable_point(self):
        x = self.sheet.data[:, 0]
        self.sheet.data[:, 1] = np.sin(2 * np.pi * x / 10)
        book = next(iter(self.workspace.workbooks.values()))
        overlap_x = np.arange(100.)
        overlap = register_xy_sheet(self.workspace, book.workbook_id, 'Overlap',
            np.column_stack((overlap_x, -overlap_x)), ['X', 'Y'])
        add_curve(self.workspace, self.graph.comparison_id, overlap.sheet_id, 0, 1)
        original = self.sheet.data.copy()
        for mode in ('manual', 'common'):
            with self.subTest(mode=mode):
                self.graph.x_range_mode = mode
                self.graph.x_range = (0., 99.) if mode == 'manual' else None
                self.page.render()
                line = self.page.axes.lines[0]
                finite = np.isfinite(line.get_ydata())
                np.testing.assert_array_equal(line.get_xdata()[finite], overlap_x)
                np.testing.assert_array_equal(line.get_ydata()[finite], original[:100, 1])
                full = draw_comparison(Figure(), self.workspace, self.graph).lines[0]
                self.assertEqual(len(full.get_xdata()), len(original))
                self.assertEqual(np.isfinite(full.get_ydata()).sum(), 100)
        np.testing.assert_array_equal(self.sheet.data, original)

    def test_sparse_usable_runs_keep_all_points_and_compress_only_gaps(self):
        from spectra_app.xy_preview import sample_preview_curve
        x = np.arange(100001.)
        y = np.full(x.shape, np.nan)
        usable = np.r_[5:105, 40000, 40002, 99901:100001]
        y[usable] = np.sin(usable)
        x[40002] = np.inf
        valid = np.isfinite(x) & np.isfinite(y)
        shown_x, shown_y = sample_preview_curve(x, y)
        finite = np.isfinite(shown_x) & np.isfinite(shown_y)
        np.testing.assert_array_equal(shown_x[finite], x[valid])
        np.testing.assert_array_equal(shown_y[finite], y[valid])
        self.assertEqual((~finite).sum(), 2)
        self.assertLessEqual(len(shown_x), 8000)

    def test_sampling_is_independent_of_masked_padding_and_gap_lengths(self):
        from spectra_app.xy_preview import sample_preview_curve
        x = np.sin(np.arange(20000.) / 100)
        y = np.cos(np.arange(20000.) / 20)
        x[111], x[15000], y[300], y[12000] = -20., 30., -40., 50.
        compact_x = np.r_[x[:8000], np.nan, x[8000:12000], np.nan, x[12000:]]
        compact_y = np.r_[y[:8000], np.nan, y[8000:12000], np.nan, y[12000:]]
        wide_x, wide_y = np.full((2, 100001), np.nan)
        for target, source in ((slice(9000, 17000), slice(0, 8000)),
                               (slice(50000, 54000), slice(8000, 12000)),
                               (slice(90000, 98000), slice(12000, 20000))):
            wide_x[target], wide_y[target] = x[source], y[source]
        expected = sample_preview_curve(compact_x, compact_y, max_points=240)
        shown_x, shown_y = sample_preview_curve(wide_x, wide_y, max_points=240)
        np.testing.assert_array_equal(shown_x, expected[0])
        np.testing.assert_array_equal(shown_y, expected[1])
        self.assertLessEqual(len(shown_x), 240)
        self.assertEqual((np.nanmin(shown_x), np.nanmax(shown_x)), (-20., 30.))
        self.assertEqual((np.nanmin(shown_y), np.nanmax(shown_y)), (-40., 50.))
        self.assertEqual(np.isnan(shown_y).sum(), 2)

    def test_nonmonotonic_x_extrema_survive_preview_and_keep_full_axes_bounds(self):
        self.sheet.data[11111, 0] = -1.e6
        self.sheet.data[22222, 0] = 2.e6
        self.page.render()
        line = self.page.axes.lines[0]
        self.assertEqual(np.nanmin(line.get_xdata()), -1.e6)
        self.assertEqual(np.nanmax(line.get_xdata()), 2.e6)
        full = draw_comparison(Figure(), self.workspace, self.graph)
        self.assertEqual(self.page.axes.get_xlim(), full.get_xlim())
        self.assertLessEqual(len(line.get_xdata()), 8000)


if __name__ == '__main__':
    unittest.main()
