import csv
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

from spectra_app.dat_io import DataCube
from spectra_app.fitting import FitResult, fit_multi_peak
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings


_APP = QApplication.instance() or QApplication([])


def _cube(z: np.ndarray, gates: np.ndarray | None = None) -> DataCube:
    z = np.asarray(z, dtype=float)
    energy = np.linspace(0.0, 20.0, z.shape[1])
    gate = np.asarray(gates if gates is not None else np.arange(z.shape[0]), dtype=float)
    return DataCube(
        energy=energy,
        gate=gate,
        z=z,
        gate_label="Gate",
        title="workflow",
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


def _window(z: np.ndarray, gates: np.ndarray | None = None) -> MainWindow:
    window = MainWindow()
    window.current_cube = _cube(z, gates)
    window.current_gate = float(window.current_cube.gate[0])
    window._set_view_limits_to_auto()
    window._set_fit_range_to_full()
    window._plot_cube()
    return window


def _fit_result(center: float) -> FitResult:
    x = np.linspace(0.0, 20.0, 21)
    y = np.zeros_like(x)
    return FitResult(
        model="Lorentzian",
        x=x,
        y_fit=y,
        components=np.zeros((1, x.size)),
        centers=np.asarray([center]),
        amplitudes=np.asarray([1.0]),
        widths=np.asarray([0.5]),
        baseline=0.0,
        rmse=0.0,
        r_squared=1.0,
    )


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_settings(self)
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window.close()

    def make_window(self, z: np.ndarray, gates: np.ndarray | None = None) -> MainWindow:
        window = _window(z, gates)
        self.windows.append(window)
        return window

    def test_plot_gate_navigation_is_the_single_gate_state(self) -> None:
        window = self.make_window(np.ones((3, 21)), gates=np.asarray([1.5, 7.25, 19.0]))

        self.assertFalse(hasattr(window, "gate_spin"))
        self.assertFalse(hasattr(window, "apply_gate_btn"))
        window.plot_gate_spin.setValue(7.25)
        window.plot_gate_spin.editingFinished.emit()

        self.assertEqual(window.current_gate, 7.25)
        self.assertEqual(window.plot_gate_index_spin.value(), 2)
        gate_used, _ = window._nearest_spectrum(window.current_gate)
        self.assertEqual(gate_used, 7.25)
        self.assertIn("7.25", window._spec_ax.get_title())

    def test_use_current_x_range_copies_bounds_without_following_zoom(self) -> None:
        window = self.make_window(np.ones((2, 21)))
        window.xmin_spin.setValue(16.0)
        window.xmax_spin.setValue(4.0)
        window.fit_xmin_lock.setChecked(True)
        window.fit_xmax_lock.setChecked(True)

        window.use_current_x_range_btn.click()
        self.assertEqual(window._fit_range_bounds(), (4.0, 16.0))
        self.assertTrue(window.fit_xmin_lock.isChecked())
        self.assertTrue(window.fit_xmax_lock.isChecked())

        window._heat_ax.set_xlim(1.0, 3.0)
        self.assertEqual(window._fit_range_bounds(), (4.0, 16.0))
        self.assertIn("Fit range", window.fit_range_summary_label.text())

    def test_axes_zoom_refreshes_visible_range_summary_and_readiness(self) -> None:
        window = self.make_window(np.ones((2, 21)))
        window._refresh_fit_readiness()
        self.assertIn("0", window.fit_readiness_label.text())
        window._heat_ax.set_xlim(3.0, 12.0)

        self.assertEqual((window.xmin_spin.value(), window.xmax_spin.value()), (3.0, 12.0))
        self.assertIn("3", window.fit_range_summary_label.text())
        self.assertIn("12", window.fit_range_summary_label.text())
        self.assertIn("3", window.fit_readiness_label.text())
        self.assertIn("12", window.fit_readiness_label.text())

    def test_find_peaks_source_exposes_explicit_detect_then_fit_action(self) -> None:
        x = np.linspace(0.0, 20.0, 81)
        z = np.exp(-((x - 10.0) ** 2) / 1.2)[None, :]
        window = self.make_window(z)
        window.fit_xmin_spin.setValue(0.0)
        window.fit_xmax_spin.setValue(20.0)
        window.lw_min_spin.setValue(0)
        messages: list[str] = []
        window._show_error = messages.append
        window._refresh_fit_readiness()

        self.assertTrue(window.detect_then_fit_btn.isEnabled())
        window.detect_then_fit_btn.click()
        self.assertGreater(window._get_seeds_for_current_gate().size, 0)
        self.assertIsNotNone(window._active_fit_result())
        self.assertFalse(messages)

    def test_existing_manual_seeds_are_not_redetected_and_outside_range_is_explained(self) -> None:
        window = self.make_window(np.ones((1, 21)))
        window.peak_indices = np.asarray([2], dtype=int)
        window._peak_gate_idx = 0
        window.fit_xmin_spin.setValue(10.0)
        window.fit_xmax_spin.setValue(20.0)
        messages: list[str] = []
        window._show_error = messages.append

        window._refresh_fit_readiness()
        self.assertFalse(window.detect_then_fit_btn.isEnabled())
        self.assertIn("outside", window.fit_readiness_label.text().lower())
        window._on_fit()
        self.assertTrue(messages)
        self.assertIn("outside", messages[-1].lower())

    def test_in_range_manual_seed_fits_directly_and_is_preserved(self) -> None:
        x = np.linspace(0.0, 20.0, 81)
        window = self.make_window(np.exp(-((x - 10.0) ** 2) / 1.2)[None, :])
        window.peak_indices = np.asarray([40], dtype=int)
        window._peak_gate_idx = 0
        window.lw_min_spin.setValue(0)
        window.fit_xmin_spin.setValue(0.0)
        window.fit_xmax_spin.setValue(20.0)
        messages: list[str] = []
        window._show_error = messages.append
        window._on_fit()

        self.assertEqual(window.peak_indices.tolist(), [40])
        self.assertIsNotNone(window._active_fit_result())
        self.assertFalse(messages)

    def test_loaded_csv_seeds_are_used_without_detection_fallback(self) -> None:
        x = np.linspace(0.0, 20.0, 81)
        window = self.make_window(np.exp(-((x - 10.0) ** 2) / 1.2)[None, :])
        window.lw_min_spin.setValue(0)
        messages: list[str] = []
        window._show_error = messages.append
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seeds.csv"
            path.write_text("gate,peak_energy\n0,10\n", encoding="utf-8")
            window._load_seeds_csv_file(path)
            self.assertTrue(window._seeds_from_csv_radio.isChecked())
            self.assertEqual(window._get_seeds_for_current_gate().tolist(), [10.0])
            window._on_fit()
        self.assertIsNotNone(window._active_fit_result())
        self.assertFalse(messages)

    def test_cropped_fit_accepts_default_minimum_linewidth_seed(self) -> None:
        x = np.linspace(1.5, 4.0, 126)
        y = 1.0 + 20.0 / (1.0 + ((x - 2.3) / 0.08) ** 2)
        result = fit_multi_peak(
            x, y, centers_hint=np.asarray([2.3]), model="Lorentzian", mode="Peaks",
            linewidth_min=0.2, linewidth_max=1.0, peak_move_limit=0.0,
        )
        self.assertAlmostEqual(float(result.centers[0]), 2.3, places=2)

    def test_csv_source_without_loaded_seeds_does_not_fallback_to_detection(self) -> None:
        window = self.make_window(np.ones((1, 21)))
        window._seeds_from_csv_radio.setChecked(True)
        window._csv_seeds_map = None
        messages: list[str] = []
        window._show_error = messages.append

        window._on_fit()
        self.assertTrue(messages)
        self.assertIn("CSV", messages[-1])
        self.assertEqual(window._get_seeds_for_current_gate().size, 0)

    def test_batch_fit_rejects_missing_selected_csv_without_mutating_results(self) -> None:
        window = self.make_window(np.ones((2, 21)))
        window._seeds_from_csv_radio.setChecked(True)
        window._csv_seeds_map = None
        window.batch_peak_map = {0: np.asarray([10], dtype=int)}
        existing = _fit_result(10.0)
        window.batch_fit_result_map = {0: existing}
        window.batch_fit_map = {0: np.asarray([10.0])}
        existing_row = window._fit_result_to_export_row(0.0, "Lorentzian", "X", existing)
        window._batch_fit_export_rows = [existing_row]
        messages: list[str] = []
        window._show_error = messages.append

        window._on_batch_fit_all_gates()

        self.assertTrue(messages)
        self.assertIn("CSV", messages[-1])
        self.assertEqual(window.batch_fit_result_map, {0: existing})
        self.assertEqual(window._batch_fit_export_rows, [existing_row])

    def test_current_and_all_fit_exports_have_explicit_scopes(self) -> None:
        x = np.linspace(0.0, 20.0, 21)
        z = np.vstack([
            np.exp(-((x - 5.0) ** 2) / 1.2),
            np.exp(-((x - 15.0) ** 2) / 1.2),
            np.zeros_like(x),
        ])
        window = self.make_window(z, gates=np.asarray([1.0, 9.0, 13.0]))
        messages: list[str] = []
        window._show_error = messages.append
        window.lw_min_spin.setValue(0)
        window.fit_xmin_spin.setValue(0.0)
        window.fit_xmax_spin.setValue(20.0)
        window.peak_indices = np.asarray([5], dtype=int)
        window._peak_gate_idx = 0
        window._on_fit()
        window._set_current_gate_from_index(1)
        window.peak_indices = np.asarray([15], dtype=int)
        window._peak_gate_idx = 1
        window._on_fit()
        window._batch_fit_export_rows.append(
            window._fit_result_to_export_row(13.0, "Lorentzian", "X", None, status="fit_failed:test")
        )

        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "current.csv", Path(tmp) / "all.csv"]
            with patch("spectra_app.main_window.QFileDialog.getSaveFileName", side_effect=[
                (str(paths[0]), "CSV Files (*.csv)"), (str(paths[1]), "CSV Files (*.csv)")
            ]):
                window._on_export_current_fit_csv()
                window._on_export_all_fit_csv()

            with paths[0].open(newline="", encoding="utf-8") as fh:
                current_rows = list(csv.DictReader(fh))
            with paths[1].open(newline="", encoding="utf-8") as fh:
                all_rows = list(csv.DictReader(fh))
            self.assertEqual([row["gate"] for row in current_rows], ["9"])
            self.assertEqual({row["gate"] for row in all_rows}, {"1", "9", "13"})
            self.assertEqual(next(row for row in all_rows if row["gate"] == "13")["status"], "fit_failed:test")
        self.assertFalse(messages)


if __name__ == "__main__":
    unittest.main()
