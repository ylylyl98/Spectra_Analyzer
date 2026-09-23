import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

from spectra_app.dat_io import DataCube
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings


_APP = QApplication.instance() or QApplication([])


def _cube(z: np.ndarray) -> DataCube:
    z = np.asarray(z, dtype=float)
    energy = np.arange(z.shape[1], dtype=float)
    gate = np.arange(z.shape[0], dtype=float)
    return DataCube(
        energy=energy,
        gate=gate,
        z=z,
        gate_label="Gate",
        title="test",
        source_path="",
        x_original=energy.copy(),
        x_original_type="pixel",
        x_original_label="X",
        x_detection_source="test",
        x_detection_note="",
        x_is_ambiguous=False,
        x_override=None,
        photon_energy=None,
        photon_energy_note="",
        energy_label="X",
    )


def _window(z: np.ndarray) -> MainWindow:
    window = MainWindow()
    window.current_cube = _cube(z)
    window.current_gate = 0.0
    window._set_view_limits_to_auto()
    window._plot_cube()
    return window


class ViewColorLimitsTests(unittest.TestCase):
    def tearDown(self) -> None:
        for window in getattr(self, "_windows", []):
            window.close()

    def setUp(self) -> None:
        isolate_settings(self)
        self._windows: list[MainWindow] = []

    def make_window(self, z: np.ndarray) -> MainWindow:
        window = _window(z)
        self._windows.append(window)
        return window

    def test_xy_spin_edit_recomputes_color_limits_from_visible_data(self) -> None:
        window = self.make_window(
            [[1, 2, 3, 4], [10, 20, 30, 40], [100, 200, 300, 400]]
        )
        window.xmin_spin.setValue(1.0)
        window.xmax_spin.setValue(2.0)
        window.ymin_spin.setValue(0.0)
        window.ymax_spin.setValue(1.0)

        expected = np.nanpercentile(np.asarray([[2, 3], [20, 30]], dtype=float), [0.5, 99.5])
        self.assertAlmostEqual(window.vmin_spin.value(), float(expected[0]), places=4)
        self.assertAlmostEqual(window.vmax_spin.value(), float(expected[1]), places=4)
        self.assertAlmostEqual(window._heat_ax.collections[0].norm.vmin, float(expected[0]), places=4)
        self.assertAlmostEqual(window._heat_ax.collections[0].norm.vmax, float(expected[1]), places=4)

    def test_axes_changes_sync_xy_controls_and_shared_spectrum_x(self) -> None:
        window = self.make_window(np.arange(20, dtype=float).reshape(4, 5))
        window._heat_ax.set_xlim(1.0, 3.0)
        window._heat_ax.set_ylim(1.0, 2.0)
        self.assertEqual((window.xmin_spin.value(), window.xmax_spin.value()), (1.0, 3.0))
        self.assertEqual((window.ymin_spin.value(), window.ymax_spin.value()), (1.0, 2.0))

        window._spec_ax.set_xlim(0.5, 2.5)
        self.assertEqual((window.xmin_spin.value(), window.xmax_spin.value()), (0.5, 2.5))
        expected = np.nanpercentile(np.asarray([[6, 7], [11, 12]], dtype=float), [0.5, 99.5])
        self.assertAlmostEqual(window._heat_ax.collections[0].norm.vmin, float(expected[0]), places=4)
        self.assertAlmostEqual(window._heat_ax.collections[0].norm.vmax, float(expected[1]), places=4)
        window._update_spectrum_only()
        self.assertEqual((window.xmin_spin.value(), window.xmax_spin.value()), (0.5, 2.5))

        window.toolbar.push_current()
        window._heat_ax.set_xlim(2.0, 3.0)
        window.toolbar.home()
        self.assertEqual((window.xmin_spin.value(), window.xmax_spin.value()), (0.5, 2.5))

    def test_auto_view_respects_each_fixed_color_bound(self) -> None:
        window = self.make_window(np.arange(12, dtype=float).reshape(3, 4))
        window.vmin_spin.setValue(8.0)
        window.vmin_lock.setChecked(True)
        window._set_view_limits_to_auto()
        self.assertEqual(window.vmin_spin.value(), 8.0)
        self.assertGreater(window.vmax_spin.value(), 8.0)
        window.vmin_lock.setChecked(False)
        self.assertNotEqual(window.vmin_spin.value(), 8.0)

    def test_spectrum_redraw_restores_sync_guard_when_plotting_fails(self) -> None:
        window = self.make_window(np.arange(12, dtype=float).reshape(3, 4))
        original = window._update_heatmap_peak_labels
        window._update_heatmap_peak_labels = lambda *_args: (_ for _ in ()).throw(RuntimeError("injected"))
        try:
            with self.assertRaises(RuntimeError):
                window._update_spectrum_only()
        finally:
            window._update_heatmap_peak_labels = original
        self.assertFalse(window._view_syncing)

    def test_empty_visible_data_keeps_last_valid_color_limits(self) -> None:
        window = self.make_window(np.arange(12, dtype=float).reshape(3, 4))
        previous = (window.vmin_spin.value(), window.vmax_spin.value())
        window._heat_ax.set_xlim(100.0, 101.0)
        window._heat_ax.set_ylim(100.0, 101.0)
        self.assertEqual((window.vmin_spin.value(), window.vmax_spin.value()), previous)
        self.assertEqual(
            (window._heat_ax.collections[0].norm.vmin, window._heat_ax.collections[0].norm.vmax),
            previous,
        )

    def test_nonfinite_visible_data_keeps_last_valid_color_limits_after_redraw(self) -> None:
        window = self.make_window(np.asarray([[1.0, 2.0], [np.nan, np.nan]]))
        previous = (window.vmin_spin.value(), window.vmax_spin.value())
        window._heat_ax.set_xlim(0.0, 1.0)
        window._heat_ax.set_ylim(1.0, 1.1)
        window._update_spectrum_only()
        self.assertEqual((window.vmin_spin.value(), window.vmax_spin.value()), previous)
        self.assertEqual(
            (window._heat_ax.collections[0].norm.vmin, window._heat_ax.collections[0].norm.vmax),
            previous,
        )

    def test_constant_data_has_a_non_degenerate_color_range(self) -> None:
        window = self.make_window(np.full((3, 4), 7.0))
        self.assertLess(window._heat_ax.collections[0].norm.vmin, window._heat_ax.collections[0].norm.vmax)

    def test_single_fixed_bound_stays_fixed_when_auto_range_crosses_it(self) -> None:
        window = self.make_window(np.ones((3, 4)))
        window.vmax_spin.setValue(0.0)
        window.vmax_lock.setChecked(True)
        window._set_view_limits_to_auto()
        self.assertEqual(window.vmax_spin.value(), 0.0)
        self.assertLess(window.vmin_spin.value(), 0.0)

    def test_both_fixed_color_bounds_are_preserved(self) -> None:
        window = self.make_window(np.arange(12, dtype=float).reshape(3, 4))
        window.vmin_spin.setValue(100.0)
        window.vmax_spin.setValue(-100.0)
        window.vmin_lock.setChecked(True)
        window.vmax_lock.setChecked(True)
        window._set_view_limits_to_auto()
        self.assertEqual(window.vmin_spin.value(), 100.0)
        self.assertEqual(window.vmax_spin.value(), -100.0)
        self.assertLess(window._heat_ax.collections[0].norm.vmin, window._heat_ax.collections[0].norm.vmax)


if __name__ == "__main__":
    unittest.main()
