import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from tests.quick_migration_case import QuickMigrationCase
from tests.dat_properties_quick_helpers import (state, setup_number, set_choice, edit_number, reveal, pump)

class ViewResponsivenessTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        directory = self.folder
        path = directory / 'synthetic.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/100} {i+1} {2*i+1} {3*i+1}' for i in range(100)))
        self.window = self.controller
        self.window._load_dat_file_direct(path)
        self.assertTrue(self.window.request_outer_view('analysis'))
        pump(60)

    def tearDown(self):
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def test_numeric_typing_commits_once(self):
        w = self.window
        w.root_window.resize(1500, 920)
        w.root_window.show()
        pump(60)
        for field in ('xmin', 'xmax', 'ymin', 'ymax', 'vmin', 'vmax'):
            self.assertIsInstance(state(w)[field], str)
        reveal(w, 'xmin')
        with patch.object(w, '_on_view_limit_changed', wraps=w._on_view_limit_changed) as changed, \
                patch.object(w, '_update_spectrum_only', wraps=w._update_spectrum_only) as redraw:
            edit_number(w, 'xmin', '1.25')
            self.assertEqual(changed.call_count, 0)
            self.assertEqual(redraw.call_count, 0)
            QTest.keyClick(w.root_window, Qt.Key_Return)
            pump()
            self.assertEqual(changed.call_count, 1)
            self.assertEqual(redraw.call_count, 1)
        self.assertEqual(w._heat_ax.get_xlim()[0], 1.25)

    def test_limits_preserve_heatmap_artists(self):
        w = self.window
        ax, mesh = w._heat_ax, w._heat_mesh
        setup_number(w, 'xmin', 1.2)
        self.assertIs(w._heat_ax, ax)
        self.assertIs(w._heat_mesh, mesh)
        self.assertEqual(ax.get_xlim()[0], 1.2)
        setup_number(w, 'vmin', 10)
        self.assertIs(w._heat_mesh, mesh)
        self.assertEqual(mesh.get_clim()[0], 10)

    def test_regional_rebuild_and_boundary_layout_once(self):
        w = self.window
        set_choice(w, 'color_scale', '3')
        with patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout:
            w._plot_cube()
            self.assertEqual(layout.call_count, 1)
        w.root_window.resize(1500, 920)
        w.root_window.show()
        pump(60)
        reveal(w, 'edge0')
        with patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout:
            edit_number(w, 'edge0', '1.25', commit=True)
            self.assertEqual(layout.call_count, 1)

    def test_drag_motion_reuses_marker(self):
        w = self.window
        w._drag_peak_arr_idx = 0
        w._drag_current_x = 1.2
        w._update_spectrum_only()
        marker = w._drag_scatter
        self.assertIsNotNone(marker)
        with patch.object(w, '_update_spectrum_only', wraps=w._update_spectrum_only) as update:
            w._on_canvas_motion(SimpleNamespace(inaxes=w._spec_ax, xdata=1.4))
            self.assertEqual(update.call_count, 0)
        self.assertIs(w._drag_scatter, marker)
        self.assertAlmostEqual(marker.get_offsets()[0, 0], 1.4)

    def test_pan_updates_colors_immediately_and_coalesces_layout(self):
        w = self.window
        set_choice(w, 'color_scale', '3')
        with patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout:
            w._heat_ax.set_xlim(1.1, 1.8)
            w._heat_ax.set_xlim(1.2, 1.7)
            self.assertEqual(layout.call_count, 0)
            self.assertEqual(w._dat_view().bounds['xmin'], 1.2)
            for mesh, limits in zip(w._heat_meshes, w._last_valid_region_color_limits):
                np.testing.assert_allclose(mesh.get_clim(), limits)
            pump(80)
            self.assertEqual(layout.call_count, 1)

    def test_regional_layout_does_not_rasterize_data_synchronously(self):
        w = self.window
        set_choice(w, 'color_scale', '3')
        with patch.object(w.canvas, 'draw', wraps=w.canvas.draw) as draw:
            w._layout_live_plots()
            self.assertEqual(draw.call_count, 0)
