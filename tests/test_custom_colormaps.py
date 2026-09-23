import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from matplotlib.colors import ListedColormap
from PySide6.QtWidgets import QApplication

from spectra_app.custom_colormaps import CUSTOM_COLORMAP_NAMES, resolve_colormap
from spectra_app.dat_io import DataCube
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings


_APP = QApplication.instance() or QApplication([])
_PALETTE_DIR = Path(__file__).resolve().parents[1] / "spectra_app" / "palettes"


def _cube(z: np.ndarray) -> DataCube:
    z = np.asarray(z, dtype=float)
    energy = np.arange(z.shape[1], dtype=float)
    gate = np.arange(z.shape[0], dtype=float)
    return DataCube(
        energy=energy,
        gate=gate,
        z=z,
        gate_label="Gate",
        title="custom palette test",
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


def _read_jasc_palette(path: Path) -> np.ndarray:
    lines = path.read_text(encoding="ascii").splitlines()
    assert lines[:3] == ["JASC-PAL", "0100", "256"]
    colors = np.asarray([[int(value) for value in line.split()] for line in lines[3:]], dtype=float)
    assert colors.shape == (256, 3)
    return colors / 255.0


class CustomColormapTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_settings(self)
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window.close()

    def make_window(self) -> MainWindow:
        window = MainWindow()
        window._show_error = lambda message: self.fail(f"unexpected Qt error: {message}")
        window.current_cube = _cube(np.arange(12, dtype=float).reshape(3, 4))
        window.current_gate = 0.0
        window._set_view_limits_to_auto()
        window._plot_cube()
        self.windows.append(window)
        return window

    def test_bundled_palettes_have_exact_256_colors_in_source_order(self) -> None:
        for name in CUSTOM_COLORMAP_NAMES:
            expected = _read_jasc_palette(_PALETTE_DIR / f"{name}.pal")
            cmap = resolve_colormap(name)

            self.assertIsInstance(cmap, ListedColormap)
            actual = np.asarray(cmap.colors, dtype=float)
            self.assertEqual(actual.shape, (256, 3))
            np.testing.assert_array_equal(actual, expected)

    def test_selector_resolves_custom_and_builtin_colormaps_on_real_heatmap(self) -> None:
        window = self.make_window()
        self.assertEqual(window.cmap_combo.currentText(), "RdBu_r")
        for name in (*CUSTOM_COLORMAP_NAMES, "viridis"):
            window.cmap_combo.setCurrentText(name)
            cmap = window._heat_mesh.get_cmap()
            if name in CUSTOM_COLORMAP_NAMES:
                expected = _read_jasc_palette(_PALETTE_DIR / f"{name}.pal")
                self.assertIsInstance(cmap, ListedColormap)
                np.testing.assert_array_equal(np.asarray(cmap.colors), expected)
            else:
                self.assertEqual(cmap.name, name)

    def test_palette_only_selection_preserves_color_limits(self) -> None:
        window = self.make_window()
        window.vmin_spin.setValue(2.25)
        window.vmax_spin.setValue(8.75)
        before = (window.vmin_spin.value(), window.vmax_spin.value())

        window.cmap_combo.setCurrentText("RdBu_r_p0p45")

        self.assertEqual((window.vmin_spin.value(), window.vmax_spin.value()), before)
        self.assertEqual(
            (window._heat_mesh.norm.vmin, window._heat_mesh.norm.vmax),
            before,
        )


if __name__ == "__main__":
    unittest.main()
