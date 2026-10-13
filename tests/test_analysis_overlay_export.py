"""Formal analysis heatmaps retain source geometry and independent vector markers."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np

from spectra_app.analysis_document import AnalysisSeed, ensure_analysis_graph, graph_presentation
from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import AnalysisControls, DatasetDefaults, FixedRecipe, ViewState, initialize_dataset
from spectra_app.fitting import FitResult
from spectra_app.plot_presentation import AxisPresentation, ScatterLayer, ScatterStyle
from spectra_app.plot_snapshot import snapshot_dataset
from spectra_app import svg_export


NS = {'s': 'http://www.w3.org/2000/svg'}
HREF = '{http://www.w3.org/1999/xlink}href'


class AnalysisOverlayExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / 'source.dat'
        path.write_text('Energy(eV) -2 0 3\n1 1 2 3\n2 3 4 5\n3 2 3 4\n')
        defaults = DatasetDefaults(ViewState(), AnalysisControls(),
            {name: FixedRecipe() for name in ('processing', 'regions', 'region_edges')}, {})
        self.session = initialize_dataset(path, (load_dat_cube(path), None, None, ''), defaults)
        self.source = self.session.active_graph
        self.source.view.annotations['plot_title'] = 'Scientific result'
        self.source.presentation = replace(self.source.presentation,
            frame_width_in=None, frame_height_in=None, width_in=5, height_in=4)

    def populate(self):
        graph = ensure_analysis_graph(self.session)
        document = graph.analysis
        document.set_seeds(0, [AnalysisSeed(1.333, 'peak', manual=True), AnalysisSeed(2.111, 'dip', manual=True)])
        document.set_seeds(2, [AnalysisSeed(1.777, 'peak', manual=True)])
        x = document.science.current_cube.energy
        fit = FitResult('Gaussian', x.copy(), np.ones(3), np.ones((2, 3)),
            np.array([1.381, 2.219]), np.array([3., -2.]), np.array([.1, .2]), 0., .1, .9)
        document.record_fit(0, fit)
        return graph

    def test_snapshot_keeps_continuous_positions_physical_gates_and_types(self):
        graph = self.populate()
        snap = snapshot_dataset(self.session)
        layers = {layer.id: layer for layer in snap.scatter_layers}
        np.testing.assert_array_equal(layers['detected-peak'].x, [1.333, 1.777])
        np.testing.assert_array_equal(layers['detected-peak'].y, [-2., 3.])
        np.testing.assert_array_equal(layers['detected-dip'].x, [2.111])
        np.testing.assert_array_equal(layers['fitted-peak'].x, [1.381])
        np.testing.assert_array_equal(layers['fitted-dip'].x, [2.219])
        self.assertEqual(layers['fitted-dip'].kind, 'dip')
        self.assertEqual(layers['fitted-dip'].role, 'fitted')
        self.assertEqual(snap.peaks, ())
        for layer in layers.values():
            with self.assertRaises(ValueError):
                layer.x.setflags(write=True)
        graph.gate_indices = [2]
        selected = {layer.id: layer for layer in snapshot_dataset(self.session).scatter_layers}
        np.testing.assert_array_equal(selected['detected-peak'].x, [1.777])
        self.assertEqual(selected['fitted-peak'].x.size, 0)

    def test_formal_svg_preserves_source_raster_and_geometry_without_aux_linecut(self):
        source_snap = snapshot_dataset(self.session)
        source_page = svg_export.render_hybrid_svg(source_snap, self.session.presentation)
        graph = self.populate()
        document = graph.analysis
        document.overlay_styles['fitted-peak'] = replace(document.overlay_styles['fitted-peak'], visible=False)
        page = svg_export.render_hybrid_svg(snapshot_dataset(self.session), graph_presentation(self.session))
        self.assertEqual(source_page.axes_bounds, page.axes_bounds)
        self.assertEqual(source_page.figure_size_in, page.figure_size_in)
        source_root, result_root = ET.fromstring(source_page.svg_bytes), ET.fromstring(page.svg_bytes)
        self.assertEqual(source_root.find('.//s:image', NS).get(HREF), result_root.find('.//s:image', NS).get(HREF))
        self.assertEqual(len(result_root.findall('.//s:image', NS)), 1)
        for kind in ('detected-peak', 'detected-dip', 'fitted-dip'):
            self.assertIsNotNone(result_root.find(f".//s:g[@id='analysis-scatter-{kind}']", NS))
        self.assertIsNone(result_root.find(".//s:g[@id='analysis-scatter-fitted-peak']", NS))
        self.assertNotIn(b'linecut', page.svg_bytes.lower())
        self.assertNotIn(b'analysis-scatter', source_page.svg_bytes)
        self.session.activate_graph(self.source.graph_id)
        self.assertEqual(snapshot_dataset(self.session).scatter_layers, ())

    def test_axis_exchange_and_flips_transform_markers_once(self):
        self.populate()
        self.source.presentation = replace(self.source.presentation,
            axis=AxisPresentation(exchange_xy=True, flip_x=True, flip_y=True))
        captured = {}
        original = svg_export._add_annotations
        def capture(ax, snapshot, presentation):
            original(ax, snapshot, presentation)
            captured.update({line.get_gid(): (line.get_xdata().copy(), line.get_ydata().copy())
                for line in ax.lines if str(line.get_gid()).startswith('analysis-scatter-')})
        with patch.object(svg_export, '_add_annotations', capture):
            page = svg_export.render_hybrid_svg(snapshot_dataset(self.session), self.session.presentation)
        np.testing.assert_array_equal(captured['analysis-scatter-fitted-dip'][0], [-2.])
        np.testing.assert_array_equal(captured['analysis-scatter-fitted-dip'][1], [2.219])
        self.assertGreater(page.display_x_limits[0], page.display_x_limits[1])
        self.assertGreater(page.display_y_limits[0], page.display_y_limits[1])

    def test_styles_and_layers_reject_invalid_values(self):
        for values in ({'color': 'invalid-color'}, {'marker': '$evil$'}, {'size': 0},
                       {'alpha': 2}, {'visible': 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ScatterStyle(**values)
        with self.assertRaises(ValueError):
            ScatterLayer('bad', [1], [float('nan')])
        with self.assertRaises(ValueError):
            ScatterLayer('bad', [1, 2], [3])
