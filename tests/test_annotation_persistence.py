import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from contextlib import contextmanager
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
from spectra_app.plot_state import source_fingerprint
from spectra_app.image_export import _build_heatmap_export_figure
from tests import dat_properties_quick_helpers as dp
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import application, pump
from tests.test_image_export import _cube


class AnnotationPersistenceTests(QuickMigrationCase):
    def setUp(self):
        if (sys.platform == 'win32' and self._testMethodName ==
                'test_signal_presets_and_custom_text_survive_refresh_switch_and_restart'):
            # The DAT popup requires a real native window to stay open through
            # its opening mouse release; preserve the actual preset input.
            self.enterContext(patch.dict(os.environ, QT_QPA_PLATFORM='windows'))
        super().setUp()
        self.settings_path = self.folder / 'profile.ini'
        self.windows = []
        self.host_errors = [self.quick_errors]

    def tearDown(self):
        for errors in self.host_errors:
            self.assertFalse(errors)
        super().tearDown()

    def make_window(self):
        if not self.windows:
            window = self.controller
        else:
            # The first application's profile owner stays alive until cleanup.
            # Each restart constructs a separate real host against that profile.
            @contextmanager
            def same_profile():
                yield self.folder
            with patch('tests.quick_application_helpers.isolated_settings', same_profile):
                host, folder, errors = self.enterContext(application())
            self.assertEqual(folder, self.folder)
            self.host_errors.append(errors)
            window = host.controller
            self.assertIsNot(window, self.windows[-1])
        self.windows.append(window)
        return window

    def close_window(self, window):
        with patch.object(window, '_confirm_project_discard_async', lambda callback: callback()):
            window.close()
        pump()
        self.assertTrue(window._closing)

    def load_source(self, window, source="first.dat", label="Doping (V)"):
        path = self.folder / source
        cube = replace(_cube(np.arange(24.).reshape(3, 8)),
                       source_path=str(path), gate_label=label)
        np.savetxt(path, np.column_stack((cube.energy, cube.z.T)),
                   header='Energy ' + ' '.join(map(str, cube.gate)), comments='')
        window._apply_loaded_dat(path, cube, source_fingerprint(path), None, '', '')
        window.open_graph_document()
        window.request_task(2)
        pump(60)
        return cube

    def test_manual_annotations_survive_source_switch_and_replot_without_changing_data(self):
        window = self.make_window()
        self.load_source(window)
        window.dat_properties.setLabel('plot_title', "  Sample $literal$  ", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('y_axis_name', "Bias", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('y_axis_unit', "mV", window.dat_properties.ownerToken)

        cube = self.load_source(window, "second.dat", "Temperature (K)")
        window._plot_cube()
        window.current_dat_path = self.folder / "second.dat"
        with patch("spectra_app.main_window.load_dat_cube", return_value=cube):
            window._on_x_axis_override_changed()

        self.assertEqual(window._heat_ax.get_title(), "  Sample $literal$  ")
        self.assertEqual(window._heat_ax.get_ylabel(), "Bias (mV)")
        self.assertEqual(cube.gate_label, "Temperature (K)")
        np.testing.assert_array_equal(cube.gate, [1., 2., 3.])
        np.testing.assert_array_equal(cube.z, np.arange(24.).reshape(3, 8))

    def test_manual_annotations_are_restored_in_a_new_window(self):
        first = self.make_window()
        self.load_source(first)
        first.dat_properties.setLabel('plot_title', "Remember this title", first.dat_properties.ownerToken)
        first.dat_properties.setLabel('y_axis_name', "Bias", first.dat_properties.ownerToken)
        first.dat_properties.setLabel('y_axis_unit', "mV", first.dat_properties.ownerToken)
        self.close_window(first)

        restored = self.make_window()
        self.assertEqual(dp.state(restored)['plot_title'], "Remember this title")
        self.assertEqual(dp.state(restored)['y_axis_name'], "Bias")
        self.assertEqual(dp.state(restored)['y_axis_unit'], "mV")
        self.load_source(restored, "next.dat", "Temperature (K)")
        self.assertEqual(restored._heat_ax.get_title(), "Remember this title")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Bias (mV)")

    def test_cleared_title_and_unit_remain_blank_after_switch_and_restart(self):
        first = self.make_window()
        self.load_source(first)
        first.dat_properties.setLabel('plot_title', "Temporary title", first.dat_properties.ownerToken)
        first.dat_properties.setLabel('plot_title', '', first.dat_properties.ownerToken)
        first.dat_properties.setLabel('y_axis_unit', '', first.dat_properties.ownerToken)
        self.load_source(first, "second.dat", "Temperature (K)")
        self.assertEqual(first._heat_ax.get_title(), "")
        self.assertEqual(first._heat_ax.get_ylabel(), "Temperature")
        self.close_window(first)

        restored = self.make_window()
        self.load_source(restored, "third.dat", "Field (T)")
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Field")
        self.assertEqual(dp.state(restored)['y_axis_unit'], "")

    def test_unedited_fields_keep_inheriting_source_labels_after_restart(self):
        first = self.make_window()
        self.load_source(first)
        self.assertEqual(first._heat_ax.get_ylabel(), "Doping (V)")
        self.load_source(first, "second.dat", "Temperature (K)")
        self.assertEqual(first._heat_ax.get_ylabel(), "Temperature (K)")
        self.close_window(first)

        restored = self.make_window()
        self.load_source(restored, "third.dat", "Field (T)")
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Field (T)")

    def test_editing_one_field_before_loading_data_does_not_pin_other_fields(self):
        first = self.make_window()
        first.dat_properties.setLabel('y_axis_name', "Custom bias", first.dat_properties.ownerToken)
        self.load_source(first)
        self.assertEqual(first._heat_ax.get_ylabel(), "Custom bias (V)")
        self.load_source(first, "second.dat", "Temperature (K)")
        self.assertEqual(first._heat_ax.get_ylabel(), "Custom bias (K)")
        self.close_window(first)

        restored = self.make_window()
        self.load_source(restored, "third.dat", "Field (T)")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Custom bias (T)")

    def test_signal_presets_and_custom_text_survive_refresh_switch_and_restart(self):
        window = self.make_window()
        self.load_source(window)
        self.assertTrue('signal_title' in dp.state(window))
        self.assertEqual(dp.state(window)['signal_title'], "Signal (arb. units)")
        for label in ("d²(ΔR/R₀)/dE² (arb. units)", "PL (arb. units)",
                      "d²(ΔR/R)/dE² (arb. units)", "Custom signal", ""):
            with self.subTest(label=label):
                if label in ("d²(ΔR/R₀)/dE² (arb. units)", "PL (arb. units)"):
                    window.root_window.resize(1200, 800)
                    window.root_window.show()
                    dp.choose(window, 'signal_preset', label)
                else:
                    window.dat_properties.setLabel('signal_title', label, window.dat_properties.ownerToken)
                dp.setup_number(window, 'vmin', 2.)
                dp.setup_number(window, 'vmax', 18.)
                self.assertEqual(window._heat_colorbar.ax.get_xlabel(), label)
                cube = self.load_source(window, "next.dat", "Temperature (K)")
                self.assertEqual(window._heat_colorbar.ax.get_xlabel(), label)
                np.testing.assert_array_equal(cube.z, np.arange(24.).reshape(3, 8))
                self.assertEqual(window._heatmap_export_metadata()["signal_label"], label)
                detached = _build_heatmap_export_figure(
                    window.figure, selected_axes=(window._heat_ax, window._heat_colorbar.ax))
                self.assertEqual(detached.axes[1].get_xlabel(), label)
                self.close_window(window)
                window = self.make_window()
                self.assertEqual(dp.state(window)['signal_title'], label)
                self.load_source(window)
                self.assertEqual(window._heat_colorbar.ax.get_xlabel(), label)

    def test_derivative_signal_label_fits_export_with_title_and_scientific_axis(self):
        window = self.make_window()
        self.load_source(window)
        self.assertTrue('signal_title' in dp.state(window))
        window.dat_properties.setLabel('signal_title', "d²(ΔR/R)/dE² (a. u.)", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('plot_title', "Reflection gate scan", window.dat_properties.ownerToken)
        window._heat_ax.set_ylim(1e9, 1e9 + 2.)
        detached = _build_heatmap_export_figure(
            window.figure, selected_axes=(window._heat_ax, window._heat_colorbar.ax))
        detached.canvas.draw()
        renderer = detached.canvas.get_renderer()
        heat, bar = detached.axes
        label = bar.xaxis.label.get_window_extent(renderer)
        self.assertGreaterEqual(label.x0, 0)
        self.assertLessEqual(label.x1, detached.bbox.x1)
        self.assertFalse(label.overlaps(detached.texts[0].get_window_extent(renderer)))
        self.assertFalse(label.overlaps(heat.yaxis.get_offset_text().get_window_extent(renderer)))
        self.assertAlmostEqual(heat.get_window_extent().width / detached.dpi, 4.0, places=6)
        self.assertAlmostEqual(heat.get_window_extent().height / detached.dpi, 3.0, places=6)
        self.assertEqual(bar.xaxis.label.get_fontsize(), 12)

    def test_fix_defaults_checked_and_unchecking_only_resets_on_next_source(self):
        window = self.make_window()
        self.load_source(window)
        self.assertTrue('fix_annotations' in dp.state(window))
        self.assertTrue(dp.state(window)['fix_annotations'])
        window.dat_properties.setLabel('plot_title', "Fixed title", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('y_axis_name', "Bias", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('y_axis_unit', "mV", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('signal_title', "PL (a. u.)", window.dat_properties.ownerToken)
        dp.set_flag(window, 'fix_annotations', False)
        self.assertEqual(window._heat_ax.get_title(), "Fixed title")
        window._plot_cube()
        self.assertEqual(window._heat_ax.get_ylabel(), "Bias (mV)")
        self.assertEqual(window._heat_colorbar.ax.get_xlabel(), "PL (arb. units)")
        self.load_source(window, "next.dat", "Temperature (K)")
        self.assertEqual(window._heat_ax.get_title(), "")
        self.assertEqual(window._heat_ax.get_ylabel(), "Temperature (K)")
        self.assertEqual(window._heat_colorbar.ax.get_xlabel(), "Signal (arb. units)")
        window.dat_properties.setLabel('plot_title', "Current image only", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('y_axis_unit', '', window.dat_properties.ownerToken)
        window.dat_properties.setLabel('signal_title', "Current signal only", window.dat_properties.ownerToken)
        self.assertEqual(window._heat_ax.get_title(), "Current image only")
        self.close_window(window)
        restored = self.make_window()
        self.assertFalse(dp.state(restored)['fix_annotations'])
        self.load_source(restored)
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Doping (V)")
        self.assertEqual(restored._heat_colorbar.ax.get_xlabel(), "Signal (arb. units)")

    def test_enabling_fix_captures_current_fields_including_blanks(self):
        window = self.make_window()
        self.load_source(window)
        self.assertTrue('fix_annotations' in dp.state(window))
        window.dat_properties.setLabel('plot_title', "Old saved title", window.dat_properties.ownerToken)
        dp.set_flag(window, 'fix_annotations', False)
        self.load_source(window, "second.dat", "Temperature (K)")
        window.dat_properties.setLabel('plot_title', '', window.dat_properties.ownerToken)
        window.dat_properties.setLabel('y_axis_unit', '', window.dat_properties.ownerToken)
        window.dat_properties.setLabel('signal_title', "", window.dat_properties.ownerToken)
        dp.set_flag(window, 'fix_annotations', True)
        self.load_source(window, "third.dat", "Field (T)")
        self.assertEqual(window._heat_ax.get_title(), "")
        self.assertEqual(window._heat_ax.get_ylabel(), "Temperature")
        self.assertEqual(window._heat_colorbar.ax.get_xlabel(), "")
        self.close_window(window)
        restored = self.make_window()
        self.assertTrue(dp.state(restored)['fix_annotations'])
        self.load_source(restored)
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Temperature")
        self.assertEqual(restored._heat_colorbar.ax.get_xlabel(), "")

    def test_unfixed_annotations_recalled_when_same_file_is_explicitly_reloaded(self):
        window = self.make_window()
        path = self.settings_path.parent / "first.dat"
        path.write_text("Energy(eV) 0 1\n1.0 1 2\n1.1 2 3\n1.2 3 4\n", encoding="utf-8")
        window._load_dat_file_direct(path)
        self.assertTrue('fix_annotations' in dp.state(window))
        dp.set_flag(window, 'fix_annotations', False)
        window.dat_properties.setLabel('plot_title', "Temporary", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('y_axis_name', "Temporary bias", window.dat_properties.ownerToken)
        window._load_dat_file_direct(path)
        self.assertEqual(window._heat_ax.get_title(), "Temporary")
        self.assertEqual(window._heat_ax.get_ylabel(), "Temporary bias")
