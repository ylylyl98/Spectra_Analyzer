from tests import ordinary_analysis_quick_helpers as oa
from tests import analysis_header_quick_helpers as ah
import csv
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

import numpy as np
from spectra_app.dataset_state import DatasetSession
from spectra_app.fitting import fit_multi_peak
from tests import dat_properties_quick_helpers as dp
from tests.background_helpers import wait_jobs
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import pump
from tests.scientific_fixtures import workflow_cube as _cube, workflow_fit_result as _fit_result


class WorkflowTests(QuickMigrationCase):
    def make_window(self, z: np.ndarray, gates: np.ndarray | None = None):
        """Register real scientific state in this case's production Quick host."""
        controller = self.controller
        cube = _cube(z, gates)
        source = self.folder / 'workflow.dat'
        source.write_text('X ' + ' '.join(f'{gate:.17g}' for gate in cube.gate) + '\n' +
                          '\n'.join(' '.join(f'{value:.17g}' for value in row)
                                    for row in np.column_stack((cube.energy, cube.z.T))),
                          encoding='utf-8')
        cube = replace(cube, source_path=str(source))
        session = DatasetSession(source_path=source,
                                 base_cube=cube, current_cube=cube,
                                 sidecar_writes_enabled=False)
        controller.register_dataset(session)
        controller.activate_dataset(session.dataset_id, capture_outgoing=False)
        controller.current_gate = float(cube.gate[0])
        controller._set_view_limits_to_auto()
        controller._set_fit_range_to_full()
        controller._plot_cube()
        assert controller.request_outer_view('analysis')
        controller.dat_details_action.setChecked(True)
        assert controller.request_task(1)
        pump(100)
        return controller

    def test_plot_gate_navigation_is_the_single_gate_state(self) -> None:
        window = self.make_window(np.ones((3, 21)), gates=np.asarray([1.5, 7.25, 19.0]))

        self.assertFalse(hasattr(window, "gate_spin"))
        self.assertFalse(hasattr(window, "apply_gate_btn"))
        ah.edit(window, 'gate', '7.25', commit=True)

        self.assertEqual(window.current_gate, 7.25)
        self.assertEqual(int(ah.text(window, 'index')), 2)
        gate_used, _ = window._nearest_spectrum(window.current_gate)
        self.assertEqual(gate_used, 7.25)
        self.assertIn("7.25", window._spec_ax.get_title())

    def test_use_current_x_range_copies_bounds_without_following_zoom(self) -> None:
        window = self.make_window(np.ones((2, 21)))
        dp.setup_number(window, 'xmin', 16.0)
        dp.setup_number(window, 'xmax', 4.0)
        oa.set_value(window, 'fit_xmin_fixed', True)
        oa.set_value(window, 'fit_xmax_fixed', True)

        oa.click(window, 'currentRange')
        self.assertEqual(window._fit_range_bounds(), (4.0, 16.0))
        self.assertTrue(oa.value(window, 'fit_xmin_fixed'))
        self.assertTrue(oa.value(window, 'fit_xmax_fixed'))

        window._heat_ax.set_xlim(1.0, 3.0)
        self.assertEqual(window._fit_range_bounds(), (4.0, 16.0))
        self.assertIn("Fit range", oa.ui(window, 'rangeText'))

    def test_axes_zoom_refreshes_visible_range_summary_and_readiness(self) -> None:
        window = self.make_window(np.ones((2, 21)))
        window._refresh_fit_readiness()
        self.assertIn("0", oa.ui(window, 'readinessText'))
        window._heat_ax.set_xlim(3.0, 12.0)

        self.assertEqual((window.dat_properties.number('xmin'), window.dat_properties.number('xmax')), (3.0, 12.0))
        self.assertIn("3", oa.ui(window, 'rangeText'))
        self.assertIn("12", oa.ui(window, 'rangeText'))
        self.assertIn("3", oa.ui(window, 'readinessText'))
        self.assertIn("12", oa.ui(window, 'readinessText'))

    def test_find_peaks_source_exposes_explicit_detect_then_fit_action(self) -> None:
        x = np.linspace(0.0, 20.0, 81)
        z = np.exp(-((x - 10.0) ** 2) / 1.2)[None, :]
        window = self.make_window(z)
        oa.set_number(window, 'fit_xmin', 0.0)
        oa.set_number(window, 'fit_xmax', 20.0)
        oa.set_number(window, 'linewidth_min', 0)
        messages: list[str] = []
        window._show_error = messages.append
        window._refresh_fit_readiness()

        self.assertTrue(oa.command_enabled(window, 'fit'))
        oa.click(window, 'fit')
        wait_jobs(window)
        self.assertGreater(window._get_seeds_for_current_gate().size, 0)
        self.assertIsNotNone(window._active_fit_result())
        self.assertFalse(messages)

    def test_existing_manual_seeds_are_not_redetected_and_outside_range_is_explained(self) -> None:
        window = self.make_window(np.ones((1, 21)))
        window.peak_indices = np.asarray([2], dtype=int)
        window._peak_gate_idx = 0
        oa.set_number(window, 'fit_xmin', 10.0)
        oa.set_number(window, 'fit_xmax', 20.0)
        messages: list[str] = []
        window._show_error = messages.append

        window._refresh_fit_readiness()
        self.assertFalse(oa.command_enabled(window, 'fit'))
        self.assertIn("outside", oa.ui(window, 'readinessText').lower())
        window._on_fit()
        wait_jobs(window)
        self.assertTrue(messages)
        self.assertIn("outside", messages[-1].lower())

    def test_in_range_manual_seed_fits_directly_and_is_preserved(self) -> None:
        x = np.linspace(0.0, 20.0, 81)
        window = self.make_window(np.exp(-((x - 10.0) ** 2) / 1.2)[None, :])
        window.peak_indices = np.asarray([40], dtype=int)
        window._peak_gate_idx = 0
        oa.set_number(window, 'linewidth_min', 0)
        oa.set_number(window, 'fit_xmin', 0.0)
        oa.set_number(window, 'fit_xmax', 20.0)
        messages: list[str] = []
        window._show_error = messages.append
        window._on_fit()
        wait_jobs(window)

        self.assertEqual(window.peak_indices.tolist(), [40])
        self.assertIsNotNone(window._active_fit_result())
        self.assertFalse(messages)

    def test_loaded_csv_seeds_are_used_without_detection_fallback(self) -> None:
        x = np.linspace(0.0, 20.0, 81)
        window = self.make_window(np.exp(-((x - 10.0) ** 2) / 1.2)[None, :])
        oa.set_number(window, 'linewidth_min', 0)
        messages: list[str] = []
        window._show_error = messages.append
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seeds.csv"
            path.write_text("gate,peak_energy\n0,10\n", encoding="utf-8")
            window._load_seeds_csv_file(path)
            self.assertTrue(oa.value(window, 'csv_source'))
            self.assertEqual(window._get_seeds_for_current_gate().tolist(), [10.0])
            window._on_fit()
            wait_jobs(window)
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
        oa.set_value(window, 'csv_source', True)
        window._csv_seeds_map = None
        messages: list[str] = []
        window._show_error = messages.append

        window._on_fit()
        wait_jobs(window)
        self.assertTrue(messages)
        self.assertIn("CSV", messages[-1])
        self.assertEqual(window._get_seeds_for_current_gate().size, 0)

    def test_batch_fit_rejects_missing_selected_csv_without_mutating_results(self) -> None:
        window = self.make_window(np.ones((2, 21)))
        oa.set_value(window, 'csv_source', True)
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
        wait_jobs(window)

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
        oa.set_number(window, 'linewidth_min', 0)
        oa.set_number(window, 'fit_xmin', 0.0)
        oa.set_number(window, 'fit_xmax', 20.0)
        window.peak_indices = np.asarray([5], dtype=int)
        window._peak_gate_idx = 0
        window._on_fit()
        wait_jobs(window)
        window._set_current_gate_from_index(1)
        window.peak_indices = np.asarray([15], dtype=int)
        window._peak_gate_idx = 1
        window._on_fit()
        wait_jobs(window)
        window._batch_fit_export_rows.append(
            window._fit_result_to_export_row(13.0, "Lorentzian", "X", None, status="fit_failed:test")
        )

        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "current.csv", Path(tmp) / "all.csv"]
            with patch("spectra_app.main_window.FilePicker.getSaveFileName", side_effect=[
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
