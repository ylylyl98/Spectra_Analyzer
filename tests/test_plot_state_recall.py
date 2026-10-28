import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from spectra_app.common_prompts import PromptAction
from tests import quick_migration_case as migration
from tests.quick_application_helpers import application, pump, wait_until
from spectra_app.plot_state import active_plot_recipe
from spectra_app.image_export import _build_heatmap_export_figure
from tests import dat_properties_quick_helpers as dp
from tests.background_helpers import wait_jobs as wait_for_background_jobs


class PlotStateRecallTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.directory = self.folder
        self.window_errors = []
        self.windows = []
        self.a = self.make_dat("first/sample.dat")
        self.b = self.make_dat("second/sample.dat")

    def tearDown(self):
        for errors in self.window_errors:
            self.assertEqual(errors, [], 'Unexpected production Quick error')

    def close_window(self, window):
        # Exercise the production close guard with the user's explicit Discard choice.
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            window.close()
            wait_until(lambda: window._closing)
        self.assertFalse(window.root_window.isVisible())

    def make_dat(self, name):
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Energy(eV) 0 1 2\n" + "\n".join(
            f"{1 + i / 10} {i + 1} {2 * i + 1} {3 * i + 1}" for i in range(12)), encoding="utf-8")
        return path

    def window(self):
        if not self.windows:
            window, errors = self.controller, self.quick_errors
        else:
            host, folder, errors = self.enterContext(application())
            window = host.controller
        self.windows.append(window)
        self.window_errors.append(errors)
        return window

    def configure(self, window):
        dp.set_flag(window, 'fix_annotations', False)
        window.dat_properties.setLabel('plot_title', "Sample A", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('y_axis_name', "Bias", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('y_axis_unit', "mV", window.dat_properties.ownerToken)
        window.dat_properties.setLabel('signal_title', "Custom signal", window.dat_properties.ownerToken)
        dp.set_choice(window, 'colormap', "viridis")
        dp.set_flag(window, 'xmin_lock', True)
        dp.set_flag(window, 'vmin_lock', True)
        dp.set_flag(window, 'vmax_lock', True)
        window.current_gate = 2.0
        window._sync_gate_controls()
        window._heat_ax.set_xlim(1.123456789, 1.987654321)
        window._heat_ax.set_ylim(0.123456789, 1.876543219)
        window._heat_mesh.set_clim(2.123456789, 15.987654321)

    def configure_regional(self, window):
        dp.set_choice(window, 'color_scale', "3")
        dp.setup_number(window, 'edge0', 1.4)
        dp.setup_number(window, 'edge1', 1.8)

    def assert_recalled(self, window):
        self.assertEqual(window._heat_ax.get_title(), "Sample A")
        self.assertEqual(window._heat_ax.get_ylabel(), "Bias (mV)")
        self.assertEqual(window._heat_colorbar.ax.get_xlabel(), "Custom signal")
        self.assertEqual(window._heat_mesh.get_cmap().name, "viridis")
        self.assertEqual(window.current_gate, 2.)
        self.assertTrue(dp.state(window)['xmin_lock'])
        self.assertTrue(dp.state(window)['vmin_lock'])
        self.assertTrue(dp.state(window)['vmax_lock'])
        self.assertFalse(dp.state(window)['ymin_lock'])
        self.assertFalse(dp.state(window)['fix_annotations'])
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
        window.dat_properties.setLabel('plot_title', "Sample B", window.dat_properties.ownerToken)
        window._load_dat_file_direct(self.a)
        self.assert_recalled(window)
        self.close_window(window)
        pump()
        restored = self.window()
        restored._load_dat_file_direct(self.a)
        self.assert_recalled(restored)
        restored._load_dat_file_direct(self.b)
        self.assertEqual(restored._heat_ax.get_title(), "Sample B")

    def test_saved_override_is_used_before_parsing_and_latest_style_applies(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        dp.set_choice(window, 'axis_override', "pixel")
        wait_for_background_jobs(window)
        self.configure(window)
        self.close_window(window)
        pump()
        restored = self.window()
        with patch("spectra_app.image_export.PLOT_FONT_SIZE", 14):
            restored._load_dat_file_direct(self.a)
            self.assertEqual(restored.current_cube.x_override, "pixel")
            p = restored.workspace.active.presentation
            # The live Analysis canvas fits independently of the formal paper.
            factor = 96*restored._analysis_display_scale()*restored.canvas.device_pixel_ratio/restored.figure.dpi
            self.assertEqual(restored._heat_ax.xaxis.label.get_fontsize(), p.text_pt*factor)
            detached = _build_heatmap_export_figure(restored.figure,
                selected_axes=(restored._heat_ax, restored._heat_colorbar.ax))
            self.assertEqual(detached.axes[0].xaxis.label.get_fontsize(), 14)
        self.assert_recalled(restored)
        payload = json.loads(restored._plot_state_store.path_for(self.a).read_text(encoding="utf-8"))
        self.assertEqual(set(payload['plot']), {'graphs', 'active_graph_id', 'processing', 'axis_override'})
        self.assertEqual(set(active_plot_recipe(payload["plot"])), {
            "axis_override", "colormap", "annotations", "fix_annotations", "bounds", "locks",
            "x_limits", "y_limits", "color_limits", "gate", "title_settings",
            "color_scale_mode", "color_region_boundaries", "processing", "signal_title_auto",
            'norm', 'presentation', 'color_region_limits'})

    def test_changed_source_does_not_restore_old_plot(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        self.a.write_text(self.a.read_text(encoding="utf-8").replace("Energy(eV)", "Pixel"), encoding="utf-8")
        window._load_dat_file_direct(self.a)
        self.assertEqual(window._heat_ax.get_title(), "")
        self.assertEqual(window.current_cube.x_original_type, "pixel")
        self.assertIn("changed", window.statusText.lower())

    def test_corrupt_snapshot_is_ignored_with_visible_status(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        self.close_window(window)
        path = window._plot_state_store.path_for(self.a)
        path.write_text('{"version":', encoding="utf-8")
        restored = self.window()
        restored._load_dat_file_direct(self.a)
        self.assertEqual(restored._heat_ax.get_title(), "")
        self.assertIn("saved plot", restored.statusText.lower())
        self.assertIn("ignored", restored.statusText.lower())

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
        window.dat_properties.setLabel('plot_title', "A saved title", window.dat_properties.ownerToken)
        window._load_dat_file_direct(self.b)
        window.dat_properties.setLabel('plot_title', "Global new file title", window.dat_properties.ownerToken)
        window._load_dat_file_direct(self.a)
        self.assertEqual(window._heat_ax.get_title(), "A saved title")
        window._load_dat_file_direct(self.make_dat("third.dat"))
        self.assertEqual(window._heat_ax.get_title(), "Global new file title")

    def test_sidecar_follows_dat_and_copying_pair_keeps_plot_portable(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        self.close_window(window)
        pump()
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
        self.assertIn("Could not save plot settings", window.statusText)
        self.assertEqual(self.a.read_bytes(), original)
        self.assertEqual(window.current_dat_path, self.b)

    def test_unrestored_source_uses_default_signal_when_global_default_absent(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        # Model a per-file manual recipe without changing global defaults.
        window._dat_view().signal_title_auto = False
        window._dat_view().annotations['signal_title'] = 'Saved custom Z'
        window.dat_properties.syncFromOwner()
        window._load_dat_file_direct(self.b)
        window._load_dat_file_direct(self.a)
        self.assertEqual(dp.state(window)['signal_title'], "Saved custom Z")
        self.assertFalse(window._annotation_settings.contains("plot_annotations/signal_title"))
        window._load_dat_file_direct(self.make_dat("third.dat"))
        self.assertEqual(dp.state(window)['signal_title'], "Signal (arb. units)")

    def test_png_export_saves_sidecar_while_window_remains_open(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        window.open_graph_document()
        self.configure(window)
        window._render_plot_page()
        self.assertIsNotNone(window.plot_page.rendered_page, window.plot_page.status_text)
        output = self.a.with_name(self.a.stem + "_Graph 1_export01.png")
        with patch("spectra_app.main_window.FilePicker.getSaveFileName", side_effect=AssertionError("PNG must save without a dialog")):
            window._on_export_png()
        self.assertTrue(output.is_file())
        sidecar = self.a.with_name(self.a.name + ".plot.json")
        self.assertTrue(sidecar.is_file())
        self.assertEqual(active_plot_recipe(json.loads(sidecar.read_text(encoding="utf-8"))["plot"])["annotations"]["plot_title"], "Sample A")

    def test_png_export_keeps_sidecar_failure_visible_after_success(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        window.open_graph_document()
        window._render_plot_page()
        self.assertIsNotNone(window.plot_page.rendered_page, window.plot_page.status_text)
        output = self.a.with_name(self.a.stem + "_Graph 1_export01.png")
        with patch("spectra_app.main_window.FilePicker.getSaveFileName", side_effect=AssertionError("PNG must save without a dialog")), \
                patch.object(window._plot_state_store, "save", side_effect=PermissionError("Folder is read-only")):
            window._on_export_png()
        self.assertTrue(output.is_file())
        self.assertNotIn("Exported PNG", window.statusText)
        self.assertIn("Could not save plot settings", window.statusText)

    def test_invalid_or_incompatible_recipes_do_not_partially_restore(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure(window)
        self.close_window(window)
        pump()
        path = window._plot_state_store.path_for(self.a)
        original = json.loads(path.read_text(encoding="utf-8"))
        for mutation in (lambda data: data.update(version=99),
                         lambda data: data["plot"]["graphs"][0]["plot"].update(color_limits=[float("nan"), 9]),
                         lambda data: data["plot"]["graphs"][0]["plot"].update(colormap="unavailable-map"),
                         lambda data: data["plot"]["graphs"][0]["plot"]["locks"].update(xmin="false")):
            with self.subTest(mutation=mutation):
                recipe = json.loads(json.dumps(original))
                mutation(recipe)
                path.write_text(json.dumps(recipe), encoding="utf-8")
                restored = self.window()
                restored._load_dat_file_direct(self.a)
                self.assertEqual(restored._heat_ax.get_title(), "")
                self.assertFalse(dp.state(restored)['xmin_lock'])
                self.assertIn("ignored", restored.statusText)
                self.close_window(restored)
                pump()

    def test_regional_color_scale_round_trips_and_old_sidecar_defaults_to_global(self):
        window = self.window()
        window._load_dat_file_direct(self.a)
        self.configure_regional(window)
        saved_boundaries = [window.dat_properties.number(f'edge{index}') for index in range(2)]
        self.close_window(window)
        pump()
        restored = self.window()
        restored._load_dat_file_direct(self.a)
        self.assertEqual(dp.state(restored)['color_scale'], "3")
        np.testing.assert_allclose(
            [restored.dat_properties.number(f'edge{index}') for index in range(2)], saved_boundaries)
        self.assertEqual(len(restored._heat_meshes), 3)

        path = restored._plot_state_store.path_for(self.a)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload['plot'] = active_plot_recipe(payload['plot'])
        payload['version'] = 3
        payload["plot"].pop("color_scale_mode")
        payload["plot"].pop("color_region_boundaries")
        path.write_text(json.dumps(payload), encoding="utf-8")
        old = self.window()
        old._load_dat_file_direct(self.a)
        self.assertEqual(dp.state(old)['color_scale'], "global")
        self.assertEqual(len(old._heat_meshes), 1)
