"""Worksheet selection produces a physical, editable comparison figure."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace
import zipfile

import numpy as np
from PIL import Image
from matplotlib.figure import Figure
from PySide6.QtCore import Qt

from spectra_app.dataset_state import ProcessingState
from spectra_app.plot_presentation import PlotPresentation
from spectra_app.project_io import read_project, write_project, workspace_digest, ProjectFormatError
from spectra_app.workbook_state import (create_workbook, register_xy_sheet,
    create_comparison, add_curve, ensure_dataset_sheet, set_sheet_column_metadata,
    add_processing_result, save_sheet_snapshot)
from spectra_app.xy_plot_controller import draw_comparison
from tests import quick_migration_case as migration
from tests.quick_application_helpers import pump
from tests.formal_quick_helpers import click_item
from tests.workbook_quick_helpers import click_header

from tests.xy_helpers import set_xy_draft

class ComparisonSelectionFormatTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        self.host.window.resize(1500, 920)
        pump()

    def tearDown(self):
        self.assertEqual(self.quick_errors, [], 'Unexpected production Quick error')

    def select(self, page, columns):
        for index, column in enumerate(columns):
            click_header(page, column, Qt.ControlModifier if index else Qt.NoModifier)

    def dat(self, name='sample_PL.dat'):
        path = self.folder / name
        path.write_text('Energy(eV) -1 0 1\n' + '\n'.join(
            f'{1+i/10} {i+1} {i+2} {i+3}' for i in range(12)))
        self.w._load_dat_file_direct(path)
        session = self.w.workspace.active
        return session, ensure_dataset_sheet(self.w.workspace, session.dataset_id)

    def test_selected_columns_keep_explicit_style_and_get_scientific_labels(self):
        session, sheet = self.dat()
        book = self.w._sheet_owner(sheet.sheet_id)
        page = self.w.open_workbook(book.workbook_id, sheet.sheet_id).current_sheet
        pump()
        self.select(page, [1, 3])
        pump()
        click_item(self.w, 'workbookPlot_line_symbol')
        graph_page = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        graph = graph_page.comparison
        self.assertEqual((graph.x_label, graph.y_label), ('Photon Energy (eV)', 'PL (arb. units)'))
        self.assertIsInstance(graph.presentation, PlotPresentation)
        self.assertEqual([c.style for c in graph.curves.values()], ['line_symbol', 'line_symbol'])
        self.assertEqual([c.y_column for c in graph.curves.values()], [1, 3])
        set_xy_draft(graph_page, 'x_label', 'Manual X')
        set_xy_draft(graph_page, 'y_label', 'Manual Y')
        graph_page.finish_editor()
        self.w.compare_sheets({sheet.sheet_id: (0, [2], 'raw')}, graph.comparison_id, style='scatter')
        self.assertEqual((graph.x_label, graph.y_label), ('Manual X', 'Manual Y'))
        self.assertEqual(list(graph.curves.values())[-1].style, 'scatter')

    def test_exact_raw_retained_and_legacy_processing_bind_signal_quantity(self):
        session, raw = self.dat('sample_DR_R.dat')
        processed = replace(session.base_cube, z=session.base_cube.z * 2)
        retained, _heatmap = add_processing_result(self.w.workspace, session.dataset_id, processed,
                                                 ProcessingState('energy_second_derivative'))
        self.addCleanup(session.activate_graph, _heatmap.graph_id)
        session.applied = ProcessingState('original')
        graph = self.w.compare_sheets({retained.sheet_id: (0, [1, 2], 'processed')})
        self.assertEqual(graph.y_label, 'd²(ΔR/R₀)/dE² (arb. units)')
        session.applied = ProcessingState('energy_second_derivative')
        graph = self.w.compare_sheets({raw.sheet_id: (0, [1], 'raw')})
        self.assertEqual(graph.y_label, 'ΔR/R₀ (arb. units)')
        session.current_cube = replace(processed, z=processed.z.copy())
        legacy = ensure_dataset_sheet(self.w.workspace, session.dataset_id, 'processed')
        self.assertIsNone(legacy.result_id)
        graph = self.w.compare_sheets({legacy.sheet_id: (0, [1], 'processed')})
        self.assertEqual(graph.y_label, 'd²(ΔR/R₀)/dE² (arb. units)')

    def test_metadata_blanks_mixed_quantities_and_numeric_snapshot_names(self):
        _session, sheet = self.dat()
        snapshot = save_sheet_snapshot(self.w.workspace, sheet.sheet_id, 'Frozen DAT')
        for columns in ([1], [1, 2]):
            graph = self.w.compare_sheets({snapshot.sheet_id: (0, columns, 'raw')})
            self.assertEqual(graph.y_label, 'Signal')
        set_sheet_column_metadata(self.w.workspace, sheet.sheet_id, 1, long_name='-6')
        graph = self.w.compare_sheets({sheet.sheet_id: (0, [1], 'raw')})
        self.assertEqual(graph.y_label, 'PL (arb. units)')
        set_sheet_column_metadata(self.w.workspace, sheet.sheet_id, 0, long_name='', units='')
        set_sheet_column_metadata(self.w.workspace, sheet.sheet_id, 1, long_name='Intensity', units='counts')
        set_sheet_column_metadata(self.w.workspace, sheet.sheet_id, 2, long_name='Intensity', units='')
        first = self.w.compare_sheets({sheet.sheet_id: (0, [1], 'raw')})
        self.assertEqual((first.x_label, first.y_label), ('', 'Intensity (counts)'))
        mixed = self.w.compare_sheets({sheet.sheet_id: (0, [1, 2], 'raw')})
        self.assertEqual(mixed.y_label, 'Intensity')
        set_sheet_column_metadata(self.w.workspace, sheet.sheet_id, 2, long_name='Voltage', units='V')
        mixed = self.w.compare_sheets({sheet.sheet_id: (0, [1, 2], 'raw')})
        self.assertEqual(mixed.y_label, 'Signal')
        book = create_workbook(self.w.workspace)
        snapshot = register_xy_sheet(self.w.workspace, book.workbook_id, 'Snapshot',
            np.array([[1., 2., 3.], [2., 3., 4.]]), ['Energy (eV)', '-1', '1'])
        graph = self.w.compare_sheets({snapshot.sheet_id: (0, [1, 2], 'raw')})
        self.assertEqual((graph.x_label, graph.y_label), ('Energy (eV)', 'Signal'))


class ComparisonPhysicalFormatTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.workspace = self.controller.workspace
        book = create_workbook(self.workspace)
        x = np.linspace(1.4, 1.8, 18001)
        sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Original',
            np.column_stack((x, np.sin(x * 90), np.cos(x * 90))), ['Energy (eV)', 'PL', 'PL'])
        self.graph = create_comparison(self.workspace)
        self.graph.x_label, self.graph.y_label = 'Energy (eV)', 'PL (arb. units)'
        for column in (1, 2):
            add_curve(self.workspace, self.graph.comparison_id, sheet.sheet_id, 0, column,
                      label=f'Gate = {column-1} V')
        for curve in self.graph.curves.values():
            curve.marker_shape, curve.marker_size = 'o', 4.  # Isolate the v12 physical-format contract.
        self.page = self.controller.open_xy_comparison(self.graph.comparison_id)
        pump()

    def tearDown(self):
        self.assertEqual(self.quick_errors, [], 'Unexpected production Quick error')

    def resize_preview(self, width, height):
        documents = self.controller.graph_documents
        documents.setMode('free')
        self.host.window.resize(width + 600, height + 160)
        pump()
        key = ('xy', self.graph.comparison_id, '')
        frame = documents._presentations[key].frame
        token = documents.captureFrameInput(list(key), frame)
        self.assertTrue(token)
        self.assertTrue(documents.moveResize(list(key), frame, token, 0, 0, width, height))
        pump()
        self.assertEqual((frame.width(), frame.height()), (width, height))

    def test_small_large_preview_and_exports_share_geometry_fonts_grid_and_full_samples(self):
        presentation = self.graph.presentation
        self.assertIsInstance(presentation, PlotPresentation)
        geometry = []
        for width, height in ((620, 500), (1400, 950)):
            self.resize_preview(width, height)
            self.page.canvas.draw()
            figure, axes = self.page.figure, self.page.axes
            np.testing.assert_allclose(figure.get_size_inches(), [presentation.width_in, presentation.height_in])
            canvas_width, canvas_height = self.page.canvas.get_width_height()
            self.assertAlmostEqual(canvas_width/canvas_height,
                                   presentation.width_in/presentation.height_in, delta=.015)
            geometry.append(axes.get_position().bounds)
            renderer = self.page.canvas.get_renderer()
            bbox = axes.get_tightbbox(renderer)
            self.assertGreaterEqual(bbox.x0, -1)
            self.assertGreaterEqual(bbox.y0, -1)
            self.assertLessEqual(bbox.x1, figure.bbox.width+1)
            self.assertLessEqual(bbox.y1, figure.bbox.height+1)
        np.testing.assert_allclose(*geometry, atol=.003)
        axes = draw_comparison(Figure(), self.workspace, self.graph)
        np.testing.assert_allclose(axes.get_position().bounds, geometry[0], atol=.003)
        self.assertEqual(len(axes.lines[0].get_xdata()), 18001)
        self.assertEqual(axes.xaxis.label.get_fontsize(), presentation.text_pt)
        self.assertEqual(axes.get_xticklabels()[0].get_fontsize(), presentation.numeric_pt)
        self.assertIn('Arial', axes.xaxis.label.get_fontfamily())
        self.assertFalse(any(line.get_visible() for line in axes.get_xgridlines()+axes.get_ygridlines()))
        self.assertEqual(axes.spines['left'].get_linewidth(), presentation.axis.border_width)
        self.assertFalse(axes.get_legend().get_frame_on())
        with TemporaryDirectory() as folder:
            png = self.page.export(Path(folder)/'figure.png')
            svg = self.page.export(Path(folder)/'figure.svg')
            with Image.open(png) as image:
                self.assertEqual(image.size, (int(presentation.width_in*presentation.heatmap_dpi),
                                              int(presentation.height_in*presentation.heatmap_dpi)))
            text = svg.read_text(encoding='utf8')
            self.assertIn('<text', text)
            self.assertIn('Arial', text)
            self.assertAlmostEqual(float(re.search(r'<svg[^>]*width="([\d.]+)pt"', text)[1]),
                                   presentation.width_in*72, places=5)

    def test_inspector_draft_dirty_undo_and_save_reload_without_source(self):
        original = workspace_digest(self.workspace)
        original_frame_width = self.page.axes.get_position().width * self.graph.presentation.width_in
        set_xy_draft(self.page, 'width_mm', '120')
        self.assertTrue(self.page.has_pending_project_edits())
        self.assertEqual(workspace_digest(self.workspace), original)
        self.page.inspector_bridge.commitField('width_mm')
        self.assertAlmostEqual(self.graph.presentation.width_in, 120/25.4)
        self.assertGreater(self.page.axes.get_position().width * self.graph.presentation.width_in,
                           original_frame_width)
        self.assertNotEqual(workspace_digest(self.workspace), original)
        self.assertTrue(self.page.undo())
        self.assertEqual(workspace_digest(self.workspace), original)
        self.assertTrue(self.page.redo())
        set_xy_draft(self.page, 'width_mm', '91.5')
        self.page.inspector_bridge.commitField('width_mm')
        self.assertAlmostEqual(self.graph.presentation.width_in, 91.5/25.4)
        set_xy_draft(self.page, 'width_mm', '120')
        self.page.inspector_bridge.commitField('width_mm')
        set_xy_draft(self.page, 'numeric_pt', '11')
        set_xy_draft(self.page, 'text_pt', '13')
        set_xy_draft(self.page, 'raster_dpi', '450')
        self.page.finish_editor()
        self.assertEqual(self.page.axes.xaxis.label.get_fontsize(), 13)
        self.assertEqual(self.graph.presentation.heatmap_dpi, 450)
        set_xy_draft(self.page, 'height_mm', '0')
        with self.assertRaises(ValueError):
            self.page.finish_editor()
        self.page.revert_editor()
        self.assertFalse(self.page.has_pending_project_edits())
        with TemporaryDirectory() as folder:
            path = Path(folder)/'format.spectra'
            write_project(path, self.workspace)
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['version'], 12)
            restored = read_project(path)
            self.assertEqual(restored.comparisons[self.graph.comparison_id].presentation, self.graph.presentation)
            self.assertEqual(workspace_digest(restored), workspace_digest(self.workspace))

    def test_minimum_page_fits_and_oversized_fonts_remain_an_uncommitted_draft(self):
        set_xy_draft(self.page, 'width_mm', '50.8')
        set_xy_draft(self.page, 'height_mm', '50.8')
        self.page.finish_editor()
        self.page.canvas.draw()
        bbox = self.page.axes.get_tightbbox(self.page.canvas.get_renderer())
        self.assertGreaterEqual(bbox.x0, -1)
        self.assertLessEqual(bbox.x1, self.page.figure.bbox.width+1)
        before = workspace_digest(self.workspace)
        set_xy_draft(self.page, 'numeric_pt', '24')
        set_xy_draft(self.page, 'text_pt', '30')
        self.page.inspector_bridge.commitField('text_pt')
        with self.assertRaisesRegex(ValueError, 'Figure is too small'):
            self.page.finish_editor()
        self.assertEqual(workspace_digest(self.workspace), before)
        self.assertTrue(self.page.has_pending_project_edits())
        self.page.revert_editor()
        self.assertFalse(self.page.has_pending_project_edits())

    def test_long_legend_stays_editable_and_export_refuses_cropped_output(self):
        label = 'Very long measured signal description ' * 12
        set_xy_draft(self.page, 'label', label)
        self.page.inspector_bridge.commitField('label')
        self.assertEqual(self.page._selected().label, label)
        self.assertIn('Figure is too small', self.page.inspector_bridge.displayStatus)
        self.page.refresh()
        self.assertIn('Figure is too small', self.page.inspector_bridge.displayStatus)
        with TemporaryDirectory() as folder:
            for extension in ('png', 'svg'):
                with self.assertRaisesRegex(ValueError, 'Figure is too small'):
                    self.page.export(Path(folder)/f'cropped.{extension}')
        set_xy_draft(self.page, 'label', 'Gate = 0 V')
        self.page.inspector_bridge.commitField('label')
        self.assertNotIn('Figure is too small', self.page.inspector_bridge.displayStatus)

    def test_legacy_comparison_stays_legacy_and_format_validation_is_strict(self):
        self.graph.presentation = None
        self.page.refresh()
        axes = draw_comparison(Figure(figsize=(7, 5)), self.workspace, self.graph)
        self.assertTrue(any(line.get_visible() for line in axes.get_xgridlines()))
        with TemporaryDirectory() as folder:
            path = Path(folder)/'legacy.spectra'
            write_project(path, self.workspace)
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['version'], 8)
            self.assertNotIn('presentation', manifest['comparisons'][0])
            self.assertIsNone(read_project(path).comparisons[self.graph.comparison_id].presentation)
            set_xy_draft(self.page, 'physical', True, commit=True)
            self.assertIsInstance(self.graph.presentation, PlotPresentation)
            self.assertEqual((self.graph.x_label, self.graph.y_label), ('Energy (eV)', 'PL (arb. units)'))
            self.assertTrue(self.page.undo())
            self.assertIsNone(self.graph.presentation)
            previous = self.graph.presentation
            try:
                self.graph.presentation = {'width_in': 4}
                with self.assertRaises(ProjectFormatError):
                    write_project(Path(folder)/'invalid.spectra', self.workspace)
            finally:
                self.graph.presentation = previous


if __name__ == '__main__':
    unittest.main()
