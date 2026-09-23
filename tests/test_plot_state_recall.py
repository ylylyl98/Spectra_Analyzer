import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from spectra_app.main_window import MainWindow
from spectra_app.image_export import _build_heatmap_export_figure
from tests.settings_helpers import isolate_settings


_APP = QApplication.instance() or QApplication([])


class PlotStateRecallTests(unittest.TestCase):
    def setUp(self):
        self.directory = isolate_settings(self).parent
        self.windows = []
        self.a = self.make_dat("first/sample.dat")
        self.b = self.make_dat("second/sample.dat")

    def tearDown(self):
        for window in self.windows:
            window.close()

    def make_dat(self, name):
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Energy(eV) 0 1 2\n" + "\n".join(
            f"{1 + i / 10} {i + 1} {2 * i + 1} {3 * i + 1}" for i in range(12)), encoding="utf-8")
        return path

    def window(self):
        window = MainWindow()
        self.windows.append(window)
        return window

    def configure(self, window):
        window.fix_annotations_chk.setChecked(False)
        window.plot_title_edit.setText("Sample A")
        window.y_axis_name_edit.setText("Bias")
        window.y_axis_unit_edit.setText("mV")
        window.signal_title_combo.setEditText("Custom signal")
        window.cmap_combo.setCurrentText("viridis")
        window.xmin_lock.setChecked(True)
        window.vmin_lock.setChecked(True)
        window.vmax_lock.setChecked(True)
        window.current_gate = 2.0
        window._sync_gate_controls()
        window._heat_ax.set_xlim(1.123456789, 1.987654321)
        window._heat_ax.set_ylim(0.123456789, 1.876543219)
        window._heat_mesh.set_clim(2.123456789, 15.987654321)

    def assert_recalled(self, window):
        self.assertEqual(window._heat_ax.get_title(), "Sample A")
        self.assertEqual(window._heat_ax.get_ylabel(), "Bias (mV)")
        self.assertEqual(window._heat_colorbar.ax.get_xlabel(), "Custom signal")
        self.assertEqual(window._heat_mesh.get_cmap().name, "viridis")
        self.assertEqual(window.current_gate, 2.)
        self.assertTrue(window.xmin_lock.isChecked())
        self.assertTrue(window.vmin_lock.isChecked())
        self.assertTrue(window.vmax_lock.isChecked())
        self.assertFalse(window.ymin_lock.isChecked())
        self.assertFalse(window.fix_annotations_chk.isChecked())
        np.testing.assert_array_equal(window._heat_ax.get_xlim(), [1.123456789, 1.987654321])
        np.testing.assert_array_equal(window._heat_ax.get_ylim(), [0.123456789, 1.876543219])
        np.testing.assert_array_equal(window._heat_mesh.get_clim(), [2.123456789, 15.987654321])

    def test_same_file_reload_recalls_plot_even_when_annotations_unfixed(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        window._load_dat_file_direct(self.a)
        self.assert_recalled(window)

    def test_switch_same_basename_paths_and_restart_recall_independent_plots(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        window._load_dat_file_direct(self.b)
        self.assertEqual(window._heat_ax.get_title(), "")
        window.plot_title_edit.setText("Sample B")
        window._load_dat_file_direct(self.a)
        self.assert_recalled(window)
        window.close()
        restored = self.window()
        restored._load_dat_file_direct(self.a)
        self.assert_recalled(restored)
        restored._load_dat_file_direct(self.b)
        self.assertEqual(restored._heat_ax.get_title(), "Sample B")

    def test_saved_override_is_used_before_parsing_and_latest_style_applies(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        window.x_axis_override_combo.setCurrentIndex(window.x_axis_override_combo.findData("pixel"))
        self.configure(window)
        window.close()
        restored = self.window()
        with patch("spectra_app.image_export.PLOT_FONT_SIZE", 14):
            restored._load_dat_file_direct(self.a)
            self.assertEqual(restored.current_cube.x_override, "pixel")
            self.assertEqual(restored._heat_ax.xaxis.label.get_fontsize(), 14)
            detached = _build_heatmap_export_figure(restored.figure,
                selected_axes=(restored._heat_ax, restored._heat_colorbar.ax))
            self.assertEqual(detached.axes[0].xaxis.label.get_fontsize(), 14)
        self.assert_recalled(restored)
        payload = json.loads(restored._plot_state_store.path_for(self.a).read_text(encoding="utf-8"))
        self.assertEqual(set(payload["plot"]), {
            "axis_override", "colormap", "annotations", "fix_annotations", "bounds", "locks",
            "x_limits", "y_limits", "color_limits", "gate", "title_settings"})

    def test_changed_source_does_not_restore_old_plot(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        self.a.write_text(self.a.read_text(encoding="utf-8").replace("Energy(eV)", "Pixel"), encoding="utf-8")
        window._load_dat_file_direct(self.a)
        self.assertEqual(window._heat_ax.get_title(), "")
        self.assertEqual(window.current_cube.x_original_type, "pixel")
        self.assertIn("changed", window.statusBar().currentMessage().lower())

    def test_corrupt_snapshot_is_ignored_with_visible_status(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        window.close()
        path = window._plot_state_store.path_for(self.a)
        path.write_text('{"version":', encoding="utf-8")
        restored = self.window()
        restored._load_dat_file_direct(self.a)
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertIn("saved plot", restored.statusBar().currentMessage().lower())
        self.assertIn("ignored", restored.statusBar().currentMessage().lower())

    def test_failed_load_preserves_current_plot_and_results(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        cube = window.current_cube
        window.peak_indices = np.asarray([3, 7])
        with patch.object(window, "_show_error") as error:
            window._load_dat_file_direct(self.directory / "missing.dat")
        error.assert_called_once()
        self.assertIs(window.current_cube, cube)
        np.testing.assert_array_equal(window.peak_indices, [3, 7])
        self.assert_recalled(window)

    def test_restoring_annotations_does_not_replace_global_new_file_defaults(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        window.plot_title_edit.setText("A saved title")
        window._load_dat_file_direct(self.b)
        window.plot_title_edit.setText("Global new file title")
        window._load_dat_file_direct(self.a)
        self.assertEqual(window._heat_ax.get_title(), "A saved title")
        window._load_dat_file_direct(self.make_dat("third.dat"))
        self.assertEqual(window._heat_ax.get_title(), "Global new file title")

    def test_sidecar_follows_dat_and_copying_pair_keeps_plot_portable(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        window.close()
        sidecar = self.a.with_name(self.a.name + ".plot.json")
        self.assertTrue(sidecar.is_file())
        destination = self.directory / "copied" / self.a.name
        destination.parent.mkdir()
        shutil.copy2(self.a, destination)
        shutil.copy2(sidecar, destination.with_name(destination.name + ".plot.json"))
        restored = self.window()
        restored._load_dat_file_direct(destination)
        self.assert_recalled(restored)
        self.assertNotIn(str(self.a.resolve()), sidecar.read_text(encoding="utf-8"))

    def test_read_only_destination_reports_save_failure_without_changing_dat(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        original = self.a.read_bytes()
        self.configure(window)
        with patch("spectra_app.plot_state.os.replace", side_effect=PermissionError("Folder is read-only")):
            window._load_dat_file_direct(self.b)
        self.assertIn("Could not save plot settings", window.statusBar().currentMessage())
        self.assertEqual(self.a.read_bytes(), original)
        self.assertEqual(window.current_dat_path, self.b)

    def test_unrestored_source_uses_default_signal_when_global_default_absent(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        window.signal_title_combo.blockSignals(True)
        window.signal_title_combo.setEditText("Saved custom Z")
        window.signal_title_combo.blockSignals(False)
        window._load_dat_file_direct(self.b)
        window._load_dat_file_direct(self.a)
        self.assertEqual(window.signal_title_combo.currentText(), "Saved custom Z")
        self.assertFalse(window._annotation_settings.contains("plot_annotations/signal_title"))
        window._load_dat_file_direct(self.make_dat("third.dat"))
        self.assertEqual(window.signal_title_combo.currentText(), "ΔR/R (a. u.)")

    def test_png_export_saves_sidecar_while_window_remains_open(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        output = self.a.with_name(self.a.stem + "_export01.png")
        with patch("spectra_app.main_window.QFileDialog.getSaveFileName", side_effect=AssertionError("PNG must save without a dialog")):
            window._on_export_png()
        self.assertTrue(output.is_file())
        sidecar = self.a.with_name(self.a.name + ".plot.json")
        self.assertTrue(sidecar.is_file())
        self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8"))["plot"]["annotations"]["plot_title"], "Sample A")

    def test_png_export_success_is_reported_even_when_sidecar_save_fails(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        output = self.a.with_name(self.a.stem + "_export01.png")
        with patch("spectra_app.main_window.QFileDialog.getSaveFileName", side_effect=AssertionError("PNG must save without a dialog")), \
                patch.object(window._plot_state_store, "save", side_effect=PermissionError("Folder is read-only")):
            window._on_export_png()
        self.assertTrue(output.is_file())
        self.assertIn("Exported PNG", window.statusBar().currentMessage())
        self.assertIn("Could not save plot settings", window.statusBar().currentMessage())

    def test_invalid_or_incompatible_recipes_do_not_partially_restore(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        window.close()
        path = window._plot_state_store.path_for(self.a)
        original = json.loads(path.read_text(encoding="utf-8"))
        for mutation in (lambda data: data.update(version=99),
                         lambda data: data["plot"].update(color_limits=[float("nan"), 9]),
                         lambda data: data["plot"].update(colormap="unavailable-map"),
                         lambda data: data["plot"]["locks"].update(xmin="false")):
            with self.subTest(mutation=mutation):
                recipe = json.loads(json.dumps(original))
                mutation(recipe)
                path.write_text(json.dumps(recipe), encoding="utf-8")
                restored = self.window()
                restored._load_dat_file_direct(self.a)
                self.assertEqual(restored._heat_ax.get_title(), "")
                self.assertFalse(restored.xmin_lock.isChecked())
                self.assertIn("ignored", restored.statusBar().currentMessage())
                restored.close()
