import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PySide6.QtWidgets import QApplication
from spectra_app.main_window import MainWindow
from spectra_app.processing import energy_second_derivative
from tests.background_helpers import wait_jobs
from tests.settings_helpers import isolate_settings

_APP = QApplication.instance() or QApplication([])


class FixedProcessingRegionsTests(unittest.TestCase):
    def setUp(self):
        isolate_settings(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.windows = []
        self.addCleanup(lambda: [w.close() for w in self.windows])

    def window(self):
        w = MainWindow()
        self.windows.append(w)
        return w

    def source(self, name, low=1, high=4, scale=1, label="Energy(eV)"):
        path = Path(self.tmp.name) / (name + ".dat")
        x = np.linspace(low, high, 31)
        path.write_text(label + " 0 1\n" + "\n".join(
            f"{v:.12g} {scale * (v-2)**3:.12g} {scale * (v-2)**2:.12g}" for v in x), encoding="utf-8")
        return path

    def derivative(self, w):
        w.processing_mode_combo.setCurrentIndex(1)
        w.processing_smooth_chk.setChecked(False)
        w.processing_window_spin.setValue(9)
        w._on_generate_heatmap()
        wait_jobs(w)

    def test_fixed_processing_overrides_recall_and_restart_without_cumulative_derivative(self):
        a, b = self.source("a"), self.source("b", scale=4)
        w = self.window()
        w._load_dat_file_direct(b)
        w.vmin_lock.setChecked(True)
        w.vmax_lock.setChecked(True)
        w.vmin_spin.setValue(-999)
        w.vmax_spin.setValue(999)
        w._load_dat_file_direct(a)
        self.derivative(w)
        w.fix_processing_chk.setChecked(True)
        for path in (b, a, b):
            w._load_dat_file_direct(path)
            expected = energy_second_derivative(w._base_cube.photon_energy, w._base_cube.z,
                                                smooth=False, window_length=9)
            np.testing.assert_allclose(w.current_cube.z, expected)
            self.assertEqual(w.processing_window_spin.value(), 9)
            self.assertFalse(w.vmin_lock.isChecked())
            self.assertFalse(w.vmax_lock.isChecked())
            self.assertNotEqual(w._heat_mesh.get_clim(), (-999, 999))
        w.close()
        restored = self.window()
        self.assertEqual(restored.processing_mode_combo.currentData(), "energy_second_derivative")
        restored._load_dat_file_direct(a)
        self.assertTrue(restored.fix_processing_chk.isChecked())
        self.assertEqual(restored._processing_mode, "energy_second_derivative")
        restored.fix_processing_chk.setChecked(False)
        restored._load_dat_file_direct(self.source("new"))
        self.assertEqual(restored._processing_mode, "original")

    def test_regions_carry_absolute_edges_with_clamping_and_auto_intensities(self):
        a, b = self.source("a"), self.source("b", 6, 9, 30)
        w = self.window()
        w._load_dat_file_direct(b)
        w.vmin_lock.setChecked(True)
        w.vmax_lock.setChecked(True)
        w.vmin_spin.setValue(-999)
        w.vmax_spin.setValue(999)
        w._load_dat_file_direct(a)
        w.color_scale_combo.setCurrentIndex(2)
        original_edges = list(w._color_region_boundaries)
        w.fix_regions_chk.setChecked(True)
        w.fix_region_edges_chk.setChecked(True)
        old_ranges = [mesh.get_clim() for mesh in w._heat_meshes]
        w._load_dat_file_direct(b)
        self.assertEqual(w._color_scale_mode, "3")
        self.assertFalse(w.vmin_lock.isChecked())
        self.assertTrue(all(end > start for start, end in w._region_layout()[1]))
        np.testing.assert_allclose(
            w._fixed_recipe("region_edges")["boundaries"], original_edges)
        self.assertNotEqual(old_ranges, [mesh.get_clim() for mesh in w._heat_meshes])
        self.assertEqual(w._processing_mode, "original")
        w._load_dat_file_direct(a)
        np.testing.assert_allclose(w._color_region_boundaries, original_edges)
        w.close()
        restored = self.window()
        restored._load_dat_file_direct(a)
        self.assertTrue(restored.fix_regions_chk.isChecked())
        self.assertTrue(restored.fix_region_edges_chk.isChecked())
        np.testing.assert_allclose(restored._color_region_boundaries, original_edges)
        restored.fix_regions_chk.setChecked(False)
        restored._load_dat_file_direct(self.source("new"))
        self.assertEqual(restored._color_scale_mode, "global")

    def test_fixed_live_choices_update_independently(self):
        w = self.window()
        w._load_dat_file_direct(self.source("a"))
        w.fix_processing_chk.setChecked(True)
        w.processing_mode_combo.setCurrentIndex(1)
        w.processing_smooth_chk.setChecked(False)
        w.processing_window_spin.setValue(11)
        w.color_scale_combo.setCurrentIndex(1)
        w.fix_regions_chk.setChecked(True)
        w.fix_region_edges_chk.setChecked(True)
        w.region_boundary_spins[0].setValue(2.15)
        edge = w._color_region_boundaries[0]
        w._load_dat_file_direct(self.source("b"))
        self.assertEqual(w._processing_mode, "energy_second_derivative")
        self.assertEqual(w._processing_window_length, 11)
        self.assertFalse(w._processing_smooth)
        self.assertAlmostEqual(w._color_region_boundaries[0], edge)
        w.fix_processing_chk.setChecked(False)
        w._load_dat_file_direct(self.source("c"))
        self.assertEqual(w._processing_mode, "original")
        self.assertEqual(w._color_scale_mode, "2")

    def test_count_fix_does_not_carry_edges(self):
        w = self.window()
        w._load_dat_file_direct(self.source("a"))
        w.color_scale_combo.setCurrentIndex(1)
        w.region_boundary_spins[0].setValue(1.55)
        w.fix_regions_chk.setChecked(True)
        self.assertFalse(w.fix_region_edges_chk.isChecked())
        w._load_dat_file_direct(self.source("b"))
        self.assertEqual(w._color_scale_mode, "2")
        self.assertGreater(w._color_region_boundaries[0], 2)

    def test_edge_fix_preserves_count_and_recovers_after_count_changes(self):
        w = self.window()
        w._load_dat_file_direct(self.source("a"))
        w.color_scale_combo.setCurrentIndex(1)
        w.region_boundary_spins[0].setValue(1.55)
        w.fix_region_edges_chk.setChecked(True)
        edge = w._color_region_boundaries[0]
        w.color_scale_combo.setCurrentIndex(2)
        self.assertEqual(len(w._color_region_boundaries), 2)
        w.color_scale_combo.setCurrentIndex(1)
        self.assertAlmostEqual(w._color_region_boundaries[0], edge)
        w._load_dat_file_direct(self.source("b"))
        self.assertEqual(w._color_scale_mode, "global")
        w.color_scale_combo.setCurrentIndex(1)
        self.assertAlmostEqual(w._color_region_boundaries[0], edge)
        w.close()
        restored = self.window()
        restored._load_dat_file_direct(self.source("c"))
        self.assertEqual(restored._color_scale_mode, "global")
        restored.color_scale_combo.setCurrentIndex(1)
        self.assertAlmostEqual(restored._color_region_boundaries[0], edge)
        restored.fix_region_edges_chk.setChecked(False)
        restored.region_boundary_spins[0].setValue(3.15)
        self.assertAlmostEqual(restored._fixed_recipe("region_edges")["boundaries"][0], edge)
        restored._load_dat_file_direct(self.source("d"))
        restored.color_scale_combo.setCurrentIndex(1)
        self.assertGreater(restored._color_region_boundaries[0], 2)

    def test_legacy_combined_fix_migrates_edges_once(self):
        w = self.window()
        settings = w._annotation_settings
        settings.setValue("fixed_regions/enabled", True)
        settings.setValue("fixed_regions/recipe", '{"mode":"2","boundaries":[1.55]}')
        settings.remove("fixed_region_edges")
        settings.sync()
        restored = self.window()
        restored._load_dat_file_direct(self.source("a"))
        self.assertTrue(restored.fix_region_edges_chk.isChecked())
        self.assertAlmostEqual(restored._color_region_boundaries[0], 1.55)
        restored.fix_region_edges_chk.setChecked(False)
        restarted = self.window()
        self.assertFalse(restarted.fix_region_edges_chk.isChecked())

    def test_incompatible_axis_keeps_preference_for_next_compatible_file(self):
        w = self.window()
        w._load_dat_file_direct(self.source("a"))
        self.derivative(w)
        w.fix_processing_chk.setChecked(True)
        w._load_dat_file_direct(self.source("pixels", 10, 40, label="Pixel"))
        self.assertEqual(w._processing_mode, "original")
        self.assertEqual(w.processing_mode_combo.currentData(), "original")
        self.assertIn("photon energy", w.statusBar().currentMessage())
        w._load_dat_file_direct(self.source("b"))
        self.assertEqual(w._processing_mode, "energy_second_derivative")
