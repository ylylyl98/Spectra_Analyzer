import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings

_APP = QApplication.instance() or QApplication([])

class ViewResponsivenessTests(unittest.TestCase):
    def setUp(self):
        directory = isolate_settings(self).parent
        path = directory / 'synthetic.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/100} {i+1} {2*i+1} {3*i+1}' for i in range(100)))
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.window._load_dat_file_direct(path)

    def test_numeric_typing_commits_once(self):
        w = self.window
        for spin in (w.xmin_spin, w.xmax_spin, w.ymin_spin, w.ymax_spin,
                     w.vmin_spin, w.vmax_spin, *w.region_boundary_spins):
            self.assertFalse(spin.keyboardTracking())
        w.xmin_spin.lineEdit().selectAll()
        with patch.object(w, '_on_view_limit_changed', wraps=w._on_view_limit_changed) as changed:
            QTest.keyClicks(w.xmin_spin.lineEdit(), '1.25')
            self.assertEqual(changed.call_count, 0)
            QTest.keyClick(w.xmin_spin, Qt.Key_Return)
            self.assertEqual(changed.call_count, 1)
        self.assertEqual(w._heat_ax.get_xlim()[0], 1.25)

    def test_limits_preserve_heatmap_artists(self):
        w = self.window
        ax, mesh = w._heat_ax, w._heat_mesh
        w.xmin_spin.setValue(1.2)
        self.assertIs(w._heat_ax, ax)
        self.assertIs(w._heat_mesh, mesh)
        self.assertEqual(ax.get_xlim()[0], 1.2)
        w.vmin_spin.setValue(10)
        self.assertIs(w._heat_mesh, mesh)
        self.assertEqual(mesh.get_clim()[0], 10)

    def test_regional_rebuild_and_boundary_layout_once(self):
        w = self.window
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('3'))
        with patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout:
            w._plot_cube()
            self.assertEqual(layout.call_count, 1)
        with patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout:
            w.region_boundary_spins[0].setValue(1.25)
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
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('3'))
        with patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout:
            w._heat_ax.set_xlim(1.1, 1.8)
            w._heat_ax.set_xlim(1.2, 1.7)
            self.assertEqual(layout.call_count, 0)
            self.assertEqual(w.xmin_spin.value(), 1.2)
            for mesh, limits in zip(w._heat_meshes, w._last_valid_region_color_limits):
                np.testing.assert_allclose(mesh.get_clim(), limits)
            QTest.qWait(80)
            self.assertEqual(layout.call_count, 1)

    def test_regional_layout_does_not_rasterize_data_synchronously(self):
        w = self.window
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('3'))
        with patch.object(w.canvas, 'draw', wraps=w.canvas.draw) as draw:
            w._layout_live_plots()
            self.assertEqual(draw.call_count, 0)
