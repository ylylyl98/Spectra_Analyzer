"""Automatic XY legend names stay readable without rewriting retained provenance."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from collections import Counter
from tempfile import TemporaryDirectory
import unittest
import warnings
import xml.etree.ElementTree as ET

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import DatasetSession, Workspace
from spectra_app.project_io import read_project, workspace_digest, write_project
from spectra_app.workbook_state import (add_curve, create_comparison, create_workbook,
                                       ensure_dataset_sheet, register_xy_sheet)
from spectra_app.xy_plot_page import XYPlotPage, draw_comparison

APP = QApplication.instance() or QApplication([])
LONG_NAME = 'WSe2_device_2026_10_13_temperature_4K_power_10uW_polarization_scan_repeat_001.dat'


def dat_comparison(folder):
    workspace = Workspace()
    source = Path(folder) / LONG_NAME
    source.write_text('Energy(eV) -5 -4 -3\n1 10 12 14\n2 15 13 11\n3 11 14 16\n')
    cube = load_dat_cube(source)
    session = workspace.register(DatasetSession(source_path=source, base_cube=cube, current_cube=cube))
    sheet = ensure_dataset_sheet(workspace, session.dataset_id)
    graph = create_comparison(workspace)
    curves = [add_curve(workspace, graph.comparison_id, sheet.sheet_id, 0, column)
              for column in (1, 2, 3)]
    return workspace, graph, sheet, curves


def xy_sheet(workspace, source=None, columns=('X', 'Signal'), name='Original'):
    book = create_workbook(workspace, 'Measurements')
    return register_xy_sheet(workspace, book.workbook_id, name,
        np.arange(3 * len(columns), dtype=float).reshape(3, len(columns)),
        list(columns), source_path=source, allow_duplicate=True)


def render(workspace, graph):
    figure = Figure(figsize=(7, 5), dpi=150)
    FigureCanvasAgg(figure)
    axes = draw_comparison(figure, workspace, graph)
    figure.canvas.draw()
    return axes


def labels(axes):
    legend = axes.get_legend()
    return [text.get_text() for text in legend.get_texts()] if legend else []


def dispose_page(page):
    page.close()
    page.deleteLater()
    APP.sendPostedEvents(None, QEvent.DeferredDelete)
    APP.processEvents()


class CompactLegendTests(unittest.TestCase):
    def test_long_dat_names_fit_new_and_reopened_graph_without_changing_project(self):
        with TemporaryDirectory() as folder:
            workspace, graph, sheet, curves = dat_comparison(folder)
            original = workspace_digest(workspace)
            stored = [curve.label for curve in curves]
            path = Path(folder) / 'legend.spectra'
            write_project(path, workspace)
            sheet.source_path.unlink()
            for candidate in (workspace, read_project(path)):
                reopened = candidate.comparisons[graph.comparison_id]
                axes = render(candidate, reopened)
                self.assertEqual(labels(axes), ['Gate = -5', 'Gate = -4', 'Gate = -3'])
                self.assertEqual([curve.label for curve in reopened.curves.values()], stored)
                self.assertEqual(candidate.sheets[sheet.sheet_id].source_path, sheet.source_path)
                self.assertEqual(workspace_digest(candidate), original)
                bounds = axes.get_legend().get_window_extent(axes.figure.canvas.get_renderer())
                self.assertTrue(axes.bbox.contains(bounds.x0, bounds.y0))
                self.assertTrue(axes.bbox.contains(bounds.x1, bounds.y1))
                self.assertGreaterEqual(axes.get_legend().get_texts()[0].get_fontsize(), 10)

    def test_sheet_context_is_added_only_for_participating_sheets(self):
        with TemporaryDirectory() as folder:
            workspace, graph, sheet, curves = dat_comparison(folder)
            xy_sheet(workspace, Path(folder) / 'unrelated.csv')
            self.assertEqual(labels(render(workspace, graph))[0], 'Gate = -5')
            processed = add_curve(workspace, graph.comparison_id, sheet.sheet_id, 0, 1, mode='processed')
            self.assertEqual(labels(render(workspace, graph)),
                ['Original: Gate = -5', 'Original: Gate = -4', 'Original: Gate = -3', 'Processed: Gate = -5'])
            processed.visible = False
            self.assertEqual(labels(render(workspace, graph))[0], 'Gate = -5')

    def test_auto_legend_svg_ids_are_unique_and_exports_stay_identical(self):
        with TemporaryDirectory() as folder:
            workspace, graph, _sheet, _curves = dat_comparison(folder)
            page = XYPlotPage(workspace, graph.comparison_id)
            self.addCleanup(dispose_page, page)
            svg = page.svg_bytes()
            ids = Counter(node.attrib['id'] for node in ET.fromstring(svg).iter() if 'id' in node.attrib)
            self.assertFalse({identity: count for identity, count in ids.items() if count > 1})
            self.assertIn(b'Gate = -5', svg)
            self.assertNotIn(LONG_NAME.encode(), svg)
            self.assertEqual(page.export(Path(folder) / 'legend.svg').read_bytes(), svg)

    def test_long_source_collisions_and_earlier_unsuffixed_defaults_stay_distinct(self):
        workspace = Workspace()
        graph = create_comparison(workspace)
        stem = 'measurement_' + 'A' * 50 + '_scan'
        sources = [Path('folder-a') / (stem + '.csv'), Path('folder-b') / (stem + '.csv'),
                   Path('folder-c') / (stem.replace('A', 'B') + '.csv')]
        curves = []
        for source in sources:
            sheet = xy_sheet(workspace, source)
            curves.append(add_curve(workspace, graph.comparison_id, sheet.sheet_id, 0, 1))
        self.assertNotIn('[', curves[0].label)  # Created before the same-basename import.
        stored = [curve.label for curve in curves]
        axes = render(workspace, graph)
        displayed = labels(axes)
        self.assertEqual(len(set(displayed)), 3)
        for label in displayed:
            self.assertIn('…', label)
            self.assertTrue(label.endswith(': Signal'))
            self.assertLessEqual(len(label), 42)
        graph.curves = dict(reversed(list(graph.curves.items())))
        self.assertEqual(labels(render(workspace, graph)), displayed[::-1])
        self.assertEqual([curve.label for curve in curves], stored)

    def test_duplicate_columns_and_x_mappings_are_disambiguated(self):
        workspace = Workspace()
        sheet = xy_sheet(workspace, columns=('X', 'X', 'Signal', 'Signal'))
        graph = create_comparison(workspace)
        for x, y in ((0, 2), (1, 2), (0, 3)):
            add_curve(workspace, graph.comparison_id, sheet.sheet_id, x, y)
        self.assertEqual(labels(render(workspace, graph)),
            ['Signal [col 3] vs X [col 1]', 'Signal [col 3] vs X [col 2]', 'Signal [col 4]'])

    def test_source_qualifiers_avoid_literal_names_casefold_and_elision_collisions(self):
        prefix, suffix = 'measurement_', '_scan_repeat'
        cases = (
            ('sample', 'sample', 'sample [1]'),
            ('sample', 'SAMPLE', 'SaMpLe [1]', 'sample [2]'),
            ('sample', 'sample', 'sample [1]', 'SAMPLE [1]', 'sample [1] [1]'),
            (prefix + 'A' * 50 + suffix, prefix + 'B' * 50 + suffix,
             prefix.upper() + 'C' * 50 + suffix.upper()),
        )
        for stems in cases:
            with self.subTest(stems=stems):
                workspace = Workspace()
                graph = create_comparison(workspace)
                sheets = [xy_sheet(workspace, Path(f'folder-{index}') / f'{stem}.csv')
                          for index, stem in enumerate(stems)]
                curves = [add_curve(workspace, graph.comparison_id, sheet.sheet_id, 0, 1)
                          for sheet in sheets]
                custom = 'sample [1]: Signal'
                curves.append(add_curve(workspace, graph.comparison_id, sheets[0].sheet_id,
                                        0, 1, label=custom))
                stored = workspace_digest(workspace)
                displayed = labels(render(workspace, graph))
                self.assertEqual(len({label.casefold() for label in displayed[:-1]}), len(stems))
                self.assertEqual(displayed[-1], custom)
                self.assertTrue(all(label.endswith(': Signal') for label in displayed))
                self.assertEqual(workspace_digest(workspace), stored)
                graph.curves = dict(reversed(list(graph.curves.items())))
                self.assertEqual(labels(render(workspace, graph)), displayed[::-1])

    def test_custom_names_are_verbatim_and_generated_math_characters_are_literal(self):
        workspace = Workspace()
        sheet = xy_sheet(workspace, Path('_source.csv'), columns=('X', '_gain $x$'))
        graph = create_comparison(workspace)
        auto = add_curve(workspace, graph.comparison_id, sheet.sheet_id, 0, 1)
        custom_labels = ['Sample ' + 'long name ' * 15, '$I_{PL}$', 'Renamed.csv: Signal', '', '_private']
        for label in custom_labels:
            add_curve(workspace, graph.comparison_id, sheet.sheet_id, 0, 1, label=label)
        # Deliberately long custom text is preserved, even if the user makes it overflow.
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='Tight layout not applied.*')
            axes = render(workspace, graph)
        self.assertEqual(labels(axes), ['_gain $x$', *custom_labels[:3]])
        texts = axes.get_legend().get_texts()
        self.assertFalse(texts[0].get_parse_math())
        self.assertTrue(texts[2].get_parse_math())
        self.assertEqual(auto.label, '_source.csv: _gain $x$')

    def test_preview_selection_uses_curve_ids_and_style_edits_keep_raw_names(self):
        workspace = Workspace()
        sheet = xy_sheet(workspace, columns=('X', '_Signal'))
        graph = create_comparison(workspace)
        curves = [add_curve(workspace, graph.comparison_id, sheet.sheet_id, 0, 1, **kwargs)
                  for kwargs in ({'label': 'hidden', 'visible': False}, {'label': ''},
                                 {'label': '_private'}, {}, {'label': 'same'}, {'label': 'same'})]
        page = XYPlotPage(workspace, graph.comparison_id)
        self.addCleanup(dispose_page, page)
        page.resize(1100, 760)
        page.show()
        APP.processEvents()
        page.curve_list.setCurrentRow(3)
        raw = curves[3].label
        self.assertEqual(page.label_edit.text(), raw)
        page.width_spin.setValue(2.5)
        self.assertEqual(curves[3].label, raw)
        page.canvas.draw()
        self.assertEqual(labels(page.axes), ['_Signal', 'same', 'same'])
        for selected in (3, 5):
            page.curve_list.setCurrentRow(selected)
            legend = page.axes.get_legend()
            for index, (handle, text) in enumerate(zip(legend.legend_handles, legend.get_texts())):
                expected_id = curves[index + 3].curve_id
                active = expected_id == curves[selected].curve_id
                self.assertEqual(text.get_fontweight(), 'bold' if active else 'normal')
                self.assertAlmostEqual(text.get_alpha() or 1., 1. if active else .25)
                self.assertAlmostEqual(handle.get_alpha() or 1., 1. if active else .25)
        bounds = page.axes.get_legend().get_window_extent(page.canvas.get_renderer())
        self.assertTrue(page.axes.bbox.contains(bounds.x0, bounds.y0))
        self.assertTrue(page.axes.bbox.contains(bounds.x1, bounds.y1))


if __name__ == '__main__':
    unittest.main()
