from tests import dat_properties_quick_helpers as dat
from tests.quick_helpers import set_processing_window
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

from spectra_app.dat_io import DataCube
from spectra_app.dataset_state import DatasetSession
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import pump
from spectra_app.plot_state import active_plot_recipe
from spectra_app.processing import energy_second_derivative
from spectra_app.signal_titles import RAW_LABEL
from tests.background_helpers import wait_jobs
from tests.settings_helpers import wait_for_background_jobs


def _cube(z: np.ndarray, *, photon_energy: bool = True) -> DataCube:
    z = np.asarray(z, dtype=float)
    energy = np.linspace(1.0, 4.0, z.shape[1])
    return DataCube(
        energy=energy,
        gate=np.arange(z.shape[0], dtype=float),
        z=z,
        gate_label="Gate",
        title="quadratic",
        source_path="quadratic.dat",
        x_original=energy.copy(),
        x_original_type="ev" if photon_energy else "pixel",
        x_original_label="Photon Energy (eV)" if photon_energy else "Pixel / Channel / Index",
        x_detection_source="test",
        x_detection_note="",
        x_is_ambiguous=False,
        x_override=None,
        photon_energy=energy.copy() if photon_energy else None,
        photon_energy_note="",
        energy_label="Photon Energy (eV)" if photon_energy else "Pixel / Channel / Index",
    )


class HeatmapProcessingTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.windows = []

    def window(self):
        if not self.windows:
            window = self.controller
        else:
            # Keep the first settings context alive while a distinct host reopens its profile.
            from contextlib import nullcontext
            from tests.quick_application_helpers import application
            with patch('tests.quick_application_helpers.isolated_settings',
                       lambda: nullcontext(self.folder)):
                host, _folder, errors = self.enterContext(application())
            self.addCleanup(lambda: self.assertFalse(errors))
            window = host.controller
            self.assertTrue(all(window is not previous for previous in self.windows))
        self.windows.append(window)
        return window

    def close_window(self, window):
        from tests.quick_application_helpers import wait_until
        with patch.object(window, '_confirm_project_discard_async', lambda callback: callback()):
            window.close()
            wait_until(lambda: window._closing)

    def tearDown(self):
        self.assertFalse(self.quick_errors)
        super().tearDown()

    def make_window(self, *, photon_energy=True):
        energy = np.linspace(1.0, 4.0, 13)
        z = np.vstack([(energy - 2.0) ** 2, 2.0 * (energy - 2.0) ** 2 + 1.0])
        window = self.window()
        cube = _cube(z, photon_energy=photon_energy)
        source = self.folder / 'quadratic.dat'
        np.savetxt(source, np.column_stack((cube.energy, cube.z.T)),
                   header='Energy(eV) 0 1', comments='')
        session = DatasetSession(source_path=source, base_cube=cube, current_cube=cube,
                                 sidecar_writes_enabled=False)
        window.register_dataset(session)
        window.activate_dataset(session.dataset_id, capture_outgoing=False)
        window.current_gate = 0.0
        window._set_view_limits_to_auto()
        window._plot_cube()
        window.open_graph_document()
        return window

    def test_processing_defaults_and_reset_use_21_points(self):
        window = self.make_window()
        self.assertEqual(window.processing.windowLength, 21)
        self.assertEqual(window._processing_window_length, 21)
        set_processing_window(window.processing, 11)
        window._set_processing_controls()
        self.assertEqual(window.processing.windowLength, 21)
        self.assertEqual(window._processing_window_length, 21)

    def test_generate_uses_energy_second_derivative_and_repeated_generate_is_not_cumulative(self):
        window = self.make_window()
        original = window.current_cube.z.copy()
        expected = energy_second_derivative(window.current_cube.photon_energy, original)

        window.processing.setMode("energy_second_derivative")
        window._on_generate_heatmap()
        wait_jobs(window)
        np.testing.assert_allclose(window.current_cube.z, expected)
        np.testing.assert_allclose(window._base_cube.z, original)
        self.assertIn("d²", dat.state(window)['signal_title'])

        window._on_generate_heatmap()
        wait_jobs(window)
        np.testing.assert_allclose(window.current_cube.z, expected)

    def test_original_restores_exact_source_after_processing(self):
        window = self.make_window()
        original = window.current_cube.z.copy()
        window.processing.setMode("energy_second_derivative")
        window._on_generate_heatmap()
        wait_jobs(window)
        window.processing.setMode("original")
        window._on_generate_heatmap()
        np.testing.assert_array_equal(window.current_cube.z, original)
        self.assertEqual(window._processing_mode, "original")

    def test_pixel_axis_is_rejected_without_destroying_current_plot(self):
        window = self.make_window(photon_energy=False)
        original = window.current_cube.z.copy()
        with patch.object(window, "_show_error") as show_error:
            window.processing.setMode("energy_second_derivative")
            window._on_generate_heatmap()
            wait_jobs(window)
        np.testing.assert_array_equal(window.current_cube.z, original)
        show_error.assert_called_once()
        self.assertIn("photon energy", show_error.call_args.args[0].lower())

    def test_smoothing_toggle_and_window_are_applied(self):
        window = self.make_window()
        source = window.current_cube
        noisy = source.z + 0.08 * np.sin(np.arange(source.z.shape[1]))
        window._base_cube = replace(source, z=noisy)
        window.current_cube = window._base_cube
        window._plot_cube()
        window.processing.setMode("energy_second_derivative")
        window.processing.setSmoothing(False)
        set_processing_window(window.processing, 9)
        window._on_generate_heatmap()
        wait_jobs(window)
        unsmoothed = window.current_cube.z.copy()
        self.assertFalse(window._processing_smooth)
        self.assertEqual(window._processing_window_length, 9)

        window.processing.setSmoothing(True)
        window._on_generate_heatmap()
        wait_jobs(window)
        smoothed = window.current_cube.z.copy()
        expected = energy_second_derivative(
            window._base_cube.photon_energy, window._base_cube.z,
            smooth=True, window_length=9,
        )
        np.testing.assert_allclose(smoothed, expected)
        self.assertFalse(np.allclose(smoothed, unsmoothed))

    def test_processing_recipe_round_trip_and_legacy_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quadratic.dat"
            energy = np.linspace(1.0, 4.0, 13)
            lines = ["Energy(eV) 0 1"]
            lines.extend(f"{x:.12g} {(x - 2) ** 2:.12g} {2 * (x - 2) ** 2 + 1:.12g}" for x in energy)
            path.write_text("\n".join(lines), encoding="utf-8")
            window = self.window()
            window._load_dat_file_direct(path)
            self.assertEqual(window.processing.windowLength, 21)
            window.processing.setMode("energy_second_derivative")
            window.processing.setSmoothing(False)
            set_processing_window(window.processing, 11)
            window._on_generate_heatmap()
            wait_jobs(window)
            expected = energy_second_derivative(window._base_cube.photon_energy, window._base_cube.z,
                                                smooth=False, window_length=11)
            self.close_window(window)
            pump()
            payload = json.loads(window._plot_state_store.path_for(path).read_text(encoding="utf-8"))
            self.assertEqual(active_plot_recipe(payload["plot"])["processing"], {
                "mode": "energy_second_derivative", "smooth": False, "window_length": 11,
                "original_signal_title": RAW_LABEL,
            })

            restored = self.window()
            restored._load_dat_file_direct(path)
            np.testing.assert_allclose(restored.current_cube.z, expected)
            self.assertEqual(restored._processing_mode, "energy_second_derivative")
            self.assertFalse(restored.processing.smoothing)
            self.assertEqual(restored.processing.windowLength, 11)

            payload["plot"] = active_plot_recipe(payload["plot"])
            payload["version"] = 3
            payload["plot"].pop("processing")
            window._plot_state_store.path_for(path).write_text(json.dumps(payload), encoding="utf-8")
            legacy = self.window()
            legacy._load_dat_file_direct(path)
            np.testing.assert_array_equal(legacy.current_cube.z, legacy._base_cube.z)
            self.assertEqual(legacy._processing_mode, "original")

            self.assertEqual(legacy.processing.windowLength, 21)

    def test_export_metadata_records_transformed_current_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quadratic.dat"
            energy = np.linspace(1.0, 4.0, 13)
            path.write_text("Energy(eV) 0 1\n" + "\n".join(
                f"{x:.12g} {(x - 2) ** 2:.12g} {2 * (x - 2) ** 2 + 1:.12g}" for x in energy
            ), encoding="utf-8")
            window = self.window()
            window._load_dat_file_direct(path)
            window.processing.setMode("energy_second_derivative")
            window._on_generate_heatmap()
            wait_jobs(window)
            window.open_graph_document()
            window._render_plot_page()
            exported = window.export_plot_page('png')
            with Image.open(exported) as image:
                metadata = json.loads(image.info["SpectraAnalyzer"])
            self.assertEqual(metadata["processing"], "energy_second_derivative")
            self.assertTrue(metadata["processing_smooth"])

    def test_file_switch_and_x_axis_override_reset_to_a_fresh_original_cube(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.dat"
            second = Path(tmp) / "second.dat"
            body = "Energy(eV) 0 1\n" + "\n".join(
                f"{x:.12g} {(x - 2) ** 2:.12g} {2 * (x - 2) ** 2 + 1:.12g}"
                for x in np.linspace(1.0, 4.0, 13)
            )
            first.write_text(body, encoding="utf-8")
            second.write_text(body.replace("Energy(eV)", "Energy(eV)"), encoding="utf-8")
            window = self.window()
            window._load_dat_file_direct(first)
            original = window.current_cube.z.copy()
            window.processing.setMode("energy_second_derivative")
            window._on_generate_heatmap()
            wait_jobs(window)
            window._load_dat_file_direct(second)
            self.assertEqual(window._processing_mode, "original")
            np.testing.assert_array_equal(window.current_cube.z, original)

            dat.set_choice(window, 'axis_override', "pixel")
            wait_for_background_jobs(window)
            self.assertEqual(window._processing_mode, "original")
            np.testing.assert_array_equal(window.current_cube.z, original)
            with patch.object(window, "_show_error") as show_error:
                window.processing.setMode("energy_second_derivative")
                window._on_generate_heatmap()
                wait_jobs(window)
            show_error.assert_called_once()

    def test_original_annotation_survives_processed_recipe_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quadratic.dat"
            energy = np.linspace(1.0, 4.0, 13)
            path.write_text("Energy(eV) 0 1\n" + "\n".join(
                f"{x:.12g} {(x - 2) ** 2:.12g} {2 * (x - 2) ** 2 + 1:.12g}" for x in energy
            ), encoding="utf-8")
            window = self.window()
            window._load_dat_file_direct(path)
            window.dat_properties.setLabel('signal_title', "Measured signal", window.dat_properties.ownerToken)
            window.processing.setMode("energy_second_derivative")
            window._on_generate_heatmap()
            wait_jobs(window)
            self.close_window(window)
            pump()
            restored = self.window()
            restored._load_dat_file_direct(path)
            self.assertEqual(restored._processing_mode, "energy_second_derivative")
            restored.processing.setMode("original")
            restored._on_generate_heatmap()
            self.assertEqual(dat.state(restored)['signal_title'], "Measured signal")
