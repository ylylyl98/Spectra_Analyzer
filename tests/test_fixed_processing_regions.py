from tests.quick_helpers import set_processing_window
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from unittest.mock import patch
from tests.quick_migration_case import QuickMigrationCase
from spectra_app.processing import energy_second_derivative
from tests.background_helpers import wait_jobs
from tests.dat_properties_quick_helpers import (state, setup_number, set_choice, set_flag)

class FixedProcessingRegionsTests(QuickMigrationCase):
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

    def source(self, name, low=1, high=4, scale=1, label="Energy(eV)"):
        path = self.folder / (name + ".dat")
        x = np.linspace(low, high, 31)
        path.write_text(label + " 0 1\n" + "\n".join(
            f"{v:.12g} {scale * (v-2)**3:.12g} {scale * (v-2)**2:.12g}" for v in x), encoding="utf-8")
        return path

    def derivative(self, w):
        w.processing.setMode('energy_second_derivative')
        w.processing.setSmoothing(False)
        set_processing_window(w.processing, 9)
        w._on_generate_heatmap()
        wait_jobs(w)

    def test_fixed_processing_overrides_recall_and_restart_without_cumulative_derivative(self):
        a, b = self.source("a"), self.source("b", scale=4)
        w = self.window()
        w._load_dat_file_direct(b)
        set_flag(w, 'vmin_lock', True)
        set_flag(w, 'vmax_lock', True)
        setup_number(w, 'vmin', -999)
        setup_number(w, 'vmax', 999)
        w._load_dat_file_direct(a)
        self.derivative(w)
        w.processing.setFixed(True)
        for path in (b, a, b):
            w._load_dat_file_direct(path)
            expected = energy_second_derivative(w._base_cube.photon_energy, w._base_cube.z,
                                                smooth=False, window_length=9)
            np.testing.assert_allclose(w.current_cube.z, expected)
            self.assertEqual(w.processing.windowLength, 9)
            self.assertFalse(w._dat_view().locks['vmin'])
            self.assertFalse(w._dat_view().locks['vmax'])
            self.assertNotEqual(w._heat_mesh.get_clim(), (-999, 999))
        self.close_window(w)
        restored = self.window()
        self.assertEqual(restored.processing.mode, "energy_second_derivative")
        restored._load_dat_file_direct(a)
        self.assertTrue(restored.processing.fixed)
        self.assertEqual(restored._processing_mode, "energy_second_derivative")
        restored.processing.setFixed(False)
        restored._load_dat_file_direct(self.source("new"))
        self.assertEqual(restored._processing_mode, "original")

    def test_regions_carry_absolute_edges_with_clamping_and_auto_intensities(self):
        a, b = self.source("a"), self.source("b", 6, 9, 30)
        w = self.window()
        w._load_dat_file_direct(b)
        set_flag(w, 'vmin_lock', True)
        set_flag(w, 'vmax_lock', True)
        setup_number(w, 'vmin', -999)
        setup_number(w, 'vmax', 999)
        w._load_dat_file_direct(a)
        set_choice(w, 'color_scale', '3')
        original_edges = list(w._color_region_boundaries)
        set_flag(w, 'fix_regions', True)
        set_flag(w, 'fix_region_edges', True)
        old_ranges = [mesh.get_clim() for mesh in w._heat_meshes]
        w._load_dat_file_direct(b)
        self.assertEqual(w._color_scale_mode, "3")
        self.assertFalse(w._dat_view().locks['vmin'])
        self.assertTrue(all(end > start for start, end in w._region_layout()[1]))
        np.testing.assert_allclose(
            w._fixed_recipe("region_edges")["boundaries"], original_edges)
        self.assertNotEqual(old_ranges, [mesh.get_clim() for mesh in w._heat_meshes])
        self.assertEqual(w._processing_mode, "original")
        w._load_dat_file_direct(a)
        np.testing.assert_allclose(w._color_region_boundaries, original_edges)
        self.close_window(w)
        restored = self.window()
        restored._load_dat_file_direct(a)
        self.assertTrue(state(restored)['fix_regions'])
        self.assertTrue(state(restored)['fix_region_edges'])
        np.testing.assert_allclose(restored._color_region_boundaries, original_edges)
        set_flag(restored, 'fix_regions', False)
        restored._load_dat_file_direct(self.source("new"))
        self.assertEqual(restored._color_scale_mode, "global")

    def test_fixed_live_choices_update_independently(self):
        w = self.window()
        w._load_dat_file_direct(self.source("a"))
        w.processing.setFixed(True)
        w.processing.setMode('energy_second_derivative')
        w.processing.setSmoothing(False)
        set_processing_window(w.processing, 11)
        set_choice(w, 'color_scale', '2')
        set_flag(w, 'fix_regions', True)
        set_flag(w, 'fix_region_edges', True)
        setup_number(w, 'edge0', 2.15)
        edge = w._color_region_boundaries[0]
        w._load_dat_file_direct(self.source("b"))
        self.assertEqual(w._processing_mode, "energy_second_derivative")
        self.assertEqual(w._processing_window_length, 11)
        self.assertFalse(w._processing_smooth)
        self.assertAlmostEqual(w._color_region_boundaries[0], edge)
        w.processing.setFixed(False)
        w._load_dat_file_direct(self.source("c"))
        self.assertEqual(w._processing_mode, "original")
        self.assertEqual(w._color_scale_mode, "2")

    def test_unfixed_new_import_uses_one_bar_and_recall_preserves_explicit_regions(self):
        w = self.window()
        regional = self.source('regional')
        w._load_dat_file_direct(regional)
        w.open_graph_document()
        set_choice(w, 'color_scale', '3')
        set_flag(w, 'fix_regions', False)
        saved_edges = list(w._color_region_boundaries)
        saved_limits = [mesh.get_clim() for mesh in w._heat_meshes]
        self.assertEqual(w._save_current_plot_state(), '')
        w._load_dat_file_direct(self.source('fresh'))
        self.assertEqual(w._color_scale_mode, 'global')
        self.assertEqual(len(w._heat_colorbars), 1)
        self.assertEqual(w._color_region_boundaries, [])
        self.close_window(w)
        restored = self.window()
        restored._load_dat_file_direct(regional)
        self.assertFalse(state(restored)['fix_regions'])
        self.assertEqual(restored._color_scale_mode, '3')
        self.assertEqual(len(restored._heat_colorbars), 3)
        np.testing.assert_allclose(restored._color_region_boundaries, saved_edges)
        np.testing.assert_allclose([mesh.get_clim() for mesh in restored._heat_meshes], saved_limits)

    def test_count_fix_does_not_carry_edges(self):
        w = self.window()
        w._load_dat_file_direct(self.source("a"))
        set_choice(w, 'color_scale', '2')
        setup_number(w, 'edge0', 1.55)
        set_flag(w, 'fix_regions', True)
        self.assertFalse(state(w)['fix_region_edges'])
        w._load_dat_file_direct(self.source("b"))
        self.assertEqual(w._color_scale_mode, "2")
        self.assertGreater(w._color_region_boundaries[0], 2)

    def test_edge_fix_preserves_count_and_recovers_after_count_changes(self):
        w = self.window()
        w._load_dat_file_direct(self.source("a"))
        set_choice(w, 'color_scale', '2')
        setup_number(w, 'edge0', 1.55)
        set_flag(w, 'fix_region_edges', True)
        edge = w._color_region_boundaries[0]
        set_choice(w, 'color_scale', '3')
        self.assertEqual(len(w._color_region_boundaries), 2)
        set_choice(w, 'color_scale', '2')
        self.assertAlmostEqual(w._color_region_boundaries[0], edge)
        w._load_dat_file_direct(self.source("b"))
        self.assertEqual(w._color_scale_mode, "global")
        set_choice(w, 'color_scale', '2')
        self.assertAlmostEqual(w._color_region_boundaries[0], edge)
        self.close_window(w)
        restored = self.window()
        restored._load_dat_file_direct(self.source("c"))
        self.assertEqual(restored._color_scale_mode, "global")
        set_choice(restored, 'color_scale', '2')
        self.assertAlmostEqual(restored._color_region_boundaries[0], edge)
        set_flag(restored, 'fix_region_edges', False)
        setup_number(restored, 'edge0', 3.15)
        self.assertAlmostEqual(restored._fixed_recipe("region_edges")["boundaries"][0], edge)
        restored._load_dat_file_direct(self.source("d"))
        set_choice(restored, 'color_scale', '2')
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
        self.assertTrue(state(restored)['fix_region_edges'])
        self.assertAlmostEqual(restored._color_region_boundaries[0], 1.55)
        set_flag(restored, 'fix_region_edges', False)
        restarted = self.window()
        self.assertFalse(state(restarted)['fix_region_edges'])

    def test_incompatible_axis_keeps_preference_for_next_compatible_file(self):
        w = self.window()
        w._load_dat_file_direct(self.source("a"))
        self.derivative(w)
        w.processing.setFixed(True)
        w._load_dat_file_direct(self.source("pixels", 10, 40, label="Pixel"))
        self.assertEqual(w._processing_mode, "original")
        self.assertEqual(w.processing.mode, "original")
        self.assertIn("photon energy", w.statusText)
        w._load_dat_file_direct(self.source("b"))
        self.assertEqual(w._processing_mode, "energy_second_derivative")
