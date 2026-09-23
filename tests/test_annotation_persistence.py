import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from spectra_app.main_window import MainWindow
from spectra_app.image_export import _build_heatmap_export_figure
from tests.settings_helpers import isolate_settings
from tests.test_image_export import _cube


_APP = QApplication.instance() or QApplication([])


class AnnotationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.settings_path = isolate_settings(self)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()

    def make_window(self):
        window = MainWindow()
        self.windows.append(window)
        return window

    def load_source(self, window, source="first.dat", label="Doping (V)"):
        cube = replace(_cube(np.arange(24.).reshape(3, 8)),
                       source_path=source, gate_label=label)
        window.current_cube = cube
        window._set_view_limits_to_auto()
        window._plot_cube()
        return cube

    def test_manual_annotations_survive_source_switch_and_replot_without_changing_data(self):
        window = self.make_window()
        self.load_source(window)
        window.plot_title_edit.setText("  Sample $literal$  ")
        window.y_axis_name_edit.setText("Bias")
        window.y_axis_unit_edit.setText("mV")

        cube = self.load_source(window, "second.dat", "Temperature (K)")
        window._plot_cube()
        window.current_dat_path = Path("second.dat")
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
        first.plot_title_edit.setText("Remember this title")
        first.y_axis_name_edit.setText("Bias")
        first.y_axis_unit_edit.setText("mV")
        first.close()

        restored = self.make_window()
        self.assertEqual(restored.plot_title_edit.text(), "Remember this title")
        self.assertEqual(restored.y_axis_name_edit.text(), "Bias")
        self.assertEqual(restored.y_axis_unit_edit.text(), "mV")
        self.load_source(restored, "next.dat", "Temperature (K)")
        self.assertEqual(restored._heat_ax.get_title(), "Remember this title")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Bias (mV)")

    def test_cleared_title_and_unit_remain_blank_after_switch_and_restart(self):
        first = self.make_window()
        self.load_source(first)
        first.plot_title_edit.setText("Temporary title")
        first.plot_title_edit.clear()
        first.y_axis_unit_edit.clear()
        self.load_source(first, "second.dat", "Temperature (K)")
        self.assertEqual(first._heat_ax.get_title(), "")
        self.assertEqual(first._heat_ax.get_ylabel(), "Temperature")
        first.close()

        restored = self.make_window()
        self.load_source(restored, "third.dat", "Field (T)")
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Field")
        self.assertEqual(restored.y_axis_unit_edit.text(), "")

    def test_unedited_fields_keep_inheriting_source_labels_after_restart(self):
        first = self.make_window()
        self.load_source(first)
        self.assertEqual(first._heat_ax.get_ylabel(), "Doping (V)")
        self.load_source(first, "second.dat", "Temperature (K)")
        self.assertEqual(first._heat_ax.get_ylabel(), "Temperature (K)")
        first.close()

        restored = self.make_window()
        self.load_source(restored, "third.dat", "Field (T)")
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Field (T)")

    def test_editing_one_field_before_loading_data_does_not_pin_other_fields(self):
        first = self.make_window()
        first.y_axis_name_edit.setText("Custom bias")
        self.load_source(first)
        self.assertEqual(first._heat_ax.get_ylabel(), "Custom bias (V)")
        self.load_source(first, "second.dat", "Temperature (K)")
        self.assertEqual(first._heat_ax.get_ylabel(), "Custom bias (K)")
        first.close()

        restored = self.make_window()
        self.load_source(restored, "third.dat", "Field (T)")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Custom bias (T)")

    def test_signal_presets_and_custom_text_survive_refresh_switch_and_restart(self):
        window = self.make_window()
        self.load_source(window)
        self.assertTrue(hasattr(window, "signal_title_combo"))
        self.assertEqual(window.signal_title_combo.currentText(), "ΔR/R (a. u.)")
        for label in ("d²(ΔR/R)/dE² (a. u.)", "PL (a. u.)", "Custom signal", ""):
            with self.subTest(label=label):
                if label in ("d²(ΔR/R)/dE² (a. u.)", "PL (a. u.)"):
                    index = window.signal_title_combo.findText(label)
                    self.assertGreaterEqual(index, 0)
                    window.signal_title_combo.setCurrentIndex(index)
                else:
                    window.signal_title_combo.setEditText(label)
                window.vmin_spin.setValue(2.)
                window.vmax_spin.setValue(18.)
                self.assertEqual(window._heat_colorbar.ax.get_xlabel(), label)
                cube = self.load_source(window, "next.dat", "Temperature (K)")
                self.assertEqual(window._heat_colorbar.ax.get_xlabel(), label)
                np.testing.assert_array_equal(cube.z, np.arange(24.).reshape(3, 8))
                self.assertEqual(window._heatmap_export_metadata()["signal_label"], label)
                detached = _build_heatmap_export_figure(
                    window.figure, selected_axes=(window._heat_ax, window._heat_colorbar.ax))
                self.assertEqual(detached.axes[1].get_xlabel(), label)
                window.close()
                window = self.make_window()
                self.assertEqual(window.signal_title_combo.currentText(), label)
                self.load_source(window)
                self.assertEqual(window._heat_colorbar.ax.get_xlabel(), label)

    def test_derivative_signal_label_fits_export_with_title_and_scientific_axis(self):
        window = self.make_window()
        self.load_source(window)
        self.assertTrue(hasattr(window, "signal_title_combo"))
        window.signal_title_combo.setCurrentText("d²(ΔR/R)/dE² (a. u.)")
        window.plot_title_edit.setText("Reflection gate scan")
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
        self.assertTrue(hasattr(window, "fix_annotations_chk"))
        self.assertTrue(window.fix_annotations_chk.isChecked())
        window.plot_title_edit.setText("Fixed title")
        window.y_axis_name_edit.setText("Bias")
        window.y_axis_unit_edit.setText("mV")
        window.signal_title_combo.setCurrentText("PL (a. u.)")
        window.fix_annotations_chk.setChecked(False)
        self.assertEqual(window._heat_ax.get_title(), "Fixed title")
        window._plot_cube()
        self.assertEqual(window._heat_ax.get_ylabel(), "Bias (mV)")
        self.assertEqual(window._heat_colorbar.ax.get_xlabel(), "PL (a. u.)")
        self.load_source(window, "next.dat", "Temperature (K)")
        self.assertEqual(window._heat_ax.get_title(), "")
        self.assertEqual(window._heat_ax.get_ylabel(), "Temperature (K)")
        self.assertEqual(window._heat_colorbar.ax.get_xlabel(), "ΔR/R (a. u.)")
        window.plot_title_edit.setText("Current image only")
        window.y_axis_unit_edit.clear()
        window.signal_title_combo.setEditText("Current signal only")
        self.assertEqual(window._heat_ax.get_title(), "Current image only")
        window.close()
        restored = self.make_window()
        self.assertFalse(restored.fix_annotations_chk.isChecked())
        self.load_source(restored)
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Doping (V)")
        self.assertEqual(restored._heat_colorbar.ax.get_xlabel(), "ΔR/R (a. u.)")

    def test_enabling_fix_captures_current_fields_including_blanks(self):
        window = self.make_window()
        self.load_source(window)
        self.assertTrue(hasattr(window, "fix_annotations_chk"))
        window.plot_title_edit.setText("Old saved title")
        window.fix_annotations_chk.setChecked(False)
        self.load_source(window, "second.dat", "Temperature (K)")
        window.plot_title_edit.clear()
        window.y_axis_unit_edit.clear()
        window.signal_title_combo.setEditText("")
        window.fix_annotations_chk.setChecked(True)
        self.load_source(window, "third.dat", "Field (T)")
        self.assertEqual(window._heat_ax.get_title(), "")
        self.assertEqual(window._heat_ax.get_ylabel(), "Temperature")
        self.assertEqual(window._heat_colorbar.ax.get_xlabel(), "")
        window.close()
        restored = self.make_window()
        self.assertTrue(restored.fix_annotations_chk.isChecked())
        self.load_source(restored)
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertEqual(restored._heat_ax.get_ylabel(), "Temperature")
        self.assertEqual(restored._heat_colorbar.ax.get_xlabel(), "")

    def test_unfixed_annotations_recalled_when_same_file_is_explicitly_reloaded(self):
        window = self.make_window()
        path = self.settings_path.parent / "first.dat"
        path.write_text("Energy(eV) 0 1\n1.0 1 2\n1.1 2 3\n1.2 3 4\n", encoding="utf-8")
        window._load_dat_file_direct(path)
        self.assertTrue(hasattr(window, "fix_annotations_chk"))
        window.fix_annotations_chk.setChecked(False)
        window.plot_title_edit.setText("Temporary")
        window.y_axis_name_edit.setText("Temporary bias")
        window._load_dat_file_direct(path)
        self.assertEqual(window._heat_ax.get_title(), "Temporary")
        self.assertEqual(window._heat_ax.get_ylabel(), "Temporary bias")
