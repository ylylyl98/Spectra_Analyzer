import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from spectra_app.dat_io import DataCube
from spectra_app.dataset_state import DatasetSession
from spectra_app.main_window import ApplicationController
from tests.quick_migration_case import QuickMigrationCase
from tests.dat_properties_quick_helpers import (state, setup_number, set_choice, set_flag, command, edit_number, click, pump)


def _cube(z: np.ndarray) -> DataCube:
    z = np.asarray(z, dtype=float)
    energy = np.arange(z.shape[1], dtype=float)
    gate = np.arange(z.shape[0], dtype=float)
    return DataCube(
        energy=energy,
        gate=gate,
        z=z,
        gate_label="Gate",
        title="test",
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


class ViewColorLimitsTests(QuickMigrationCase):
    def tearDown(self):
        self.assertFalse(self.quick_errors)
        super().tearDown()

    def make_window(self, z: np.ndarray) -> ApplicationController:
        window = self.controller
        cube = _cube(z)
        source = self.folder / 'view-color-limits.dat'
        np.savetxt(source, np.column_stack((cube.energy, cube.z.T)),
                   header='Energy ' + ' '.join(map(str, cube.gate)), comments='')
        session = DatasetSession(source_path=source, base_cube=cube, current_cube=cube,
                                 sidecar_writes_enabled=False)
        window.register_dataset(session)
        window.activate_dataset(session.dataset_id, capture_outgoing=False)
        window.current_gate = 0.0
        window._set_view_limits_to_auto()
        window._plot_cube()
        window.open_graph_document()
        window.request_task(2)
        pump(60)
        self.assertEqual(window.contextKind, 'dat')
        self.assertFalse(self.quick_errors)
        return window

    def test_xy_spin_edit_recomputes_color_limits_from_visible_data(self) -> None:
        window = self.make_window(
            [[1, 2, 3, 4], [10, 20, 30, 40], [100, 200, 300, 400]]
        )
        setup_number(window, 'xmin', 1.0)
        setup_number(window, 'xmax', 2.0)
        setup_number(window, 'ymin', 0.0)
        setup_number(window, 'ymax', 1.0)

        expected = np.nanpercentile(np.asarray([[2, 3], [20, 30]], dtype=float), [0.5, 99.5])
        self.assertAlmostEqual(window._dat_view().bounds['vmin'], float(expected[0]), places=4)
        self.assertAlmostEqual(window._dat_view().bounds['vmax'], float(expected[1]), places=4)
        self.assertAlmostEqual(window._heat_ax.collections[0].norm.vmin, float(expected[0]), places=4)
        self.assertAlmostEqual(window._heat_ax.collections[0].norm.vmax, float(expected[1]), places=4)

    def test_native_edit_locks_only_changed_bound_and_auto_recalculates_visible_values(self):
        window = self.make_window(np.arange(20., dtype=float).reshape(4, 5))
        window.root_window.resize(1500, 920)
        window.root_window.show()
        pump(60)
        window._heat_ax.set_xlim(1., 3.); window._heat_ax.set_ylim(1., 2.)
        xy = (window._heat_ax.get_xlim(), window._heat_ax.get_ylim())
        setup_number(window, 'vmin', 3.)
        self.assertFalse(window._dat_view().locks['vmin'])
        edit_number(window, 'vmin', '7', commit=True)
        self.assertTrue(window._dat_view().locks['vmin'])
        self.assertFalse(window._dat_view().locks['vmax'])
        edit_number(window, 'vmax', '12', commit=True)
        self.assertTrue(window._dat_view().locks['vmax'])
        click(window, 'shared_auto_colors')
        self.assertFalse(window._dat_view().locks['vmin']); self.assertFalse(window._dat_view().locks['vmax'])
        expected = np.percentile(np.array([[6, 7, 8], [11, 12, 13]]), [.5, 99.5])
        np.testing.assert_allclose(window._heat_mesh.get_clim(), expected, atol=1e-4)
        self.assertEqual((window._heat_ax.get_xlim(), window._heat_ax.get_ylim()), xy)
        self.assertIn('0.5–99.5', state(window)['color_range_status'])
        self.assertIn('%', state(window)['color_clipping_status'])

    def test_axes_changes_sync_xy_controls_and_shared_spectrum_x(self) -> None:
        window = self.make_window(np.arange(20, dtype=float).reshape(4, 5))
        window._heat_ax.set_xlim(1.0, 3.0)
        window._heat_ax.set_ylim(1.0, 2.0)
        self.assertEqual((window._dat_view().bounds['xmin'], window._dat_view().bounds['xmax']), (1.0, 3.0))
        self.assertEqual((window._dat_view().bounds['ymin'], window._dat_view().bounds['ymax']), (1.0, 2.0))

        window._spec_ax.set_xlim(0.5, 2.5)
        self.assertEqual((window._dat_view().bounds['xmin'], window._dat_view().bounds['xmax']), (0.5, 2.5))
        expected = np.nanpercentile(np.asarray([[6, 7], [11, 12]], dtype=float), [0.5, 99.5])
        self.assertAlmostEqual(window._heat_ax.collections[0].norm.vmin, float(expected[0]), places=4)
        self.assertAlmostEqual(window._heat_ax.collections[0].norm.vmax, float(expected[1]), places=4)
        window._update_spectrum_only()
        self.assertEqual((window._dat_view().bounds['xmin'], window._dat_view().bounds['xmax']), (0.5, 2.5))

        navigation = window.analysisNavigation.toolbar
        self.assertIs(navigation, window.toolbar)
        navigation.push_current()
        window._heat_ax.set_xlim(2.0, 3.0)
        navigation.home()
        self.assertEqual((window._dat_view().bounds['xmin'], window._dat_view().bounds['xmax']), (0.5, 2.5))

    def test_auto_view_respects_each_fixed_color_bound(self) -> None:
        window = self.make_window(np.arange(12, dtype=float).reshape(3, 4))
        setup_number(window, 'vmin', 8.0)
        set_flag(window, 'vmin_lock', True)
        window._set_view_limits_to_auto()
        self.assertEqual(window._dat_view().bounds['vmin'], 8.0)
        self.assertGreater(window._dat_view().bounds['vmax'], 8.0)
        set_flag(window, 'vmin_lock', False)
        self.assertNotEqual(window._dat_view().bounds['vmin'], 8.0)

    def test_auto_mode_status_refreshes_when_visible_log_values_are_empty(self):
        window = self.make_window([[1., 2., 3.], [4., 5., 6.]])
        set_choice(window, 'mapping', 'log')
        set_flag(window, 'vmin_lock', True); set_flag(window, 'vmax_lock', True)
        window._heat_ax.set_xlim(100., 101.)
        previous = window._heat_mesh.get_clim()
        command(window, 'auto_colors')
        self.assertFalse(window._dat_view().locks['vmin']); self.assertFalse(window._dat_view().locks['vmax'])
        self.assertIn('Global: Auto', state(window)['color_range_status'])
        self.assertEqual(window._heat_mesh.get_clim(), previous)

    def test_spectrum_redraw_restores_sync_guard_when_plotting_fails(self) -> None:
        window = self.make_window(np.arange(12, dtype=float).reshape(3, 4))
        original = window._update_heatmap_peak_labels
        window._update_heatmap_peak_labels = lambda *_args: (_ for _ in ()).throw(RuntimeError("injected"))
        try:
            with self.assertRaises(RuntimeError):
                window._update_spectrum_only()
        finally:
            window._update_heatmap_peak_labels = original
        self.assertFalse(window._view_syncing)

    def test_empty_visible_data_keeps_last_valid_color_limits(self) -> None:
        window = self.make_window(np.arange(12, dtype=float).reshape(3, 4))
        previous = (window._dat_view().bounds['vmin'], window._dat_view().bounds['vmax'])
        window._heat_ax.set_xlim(100.0, 101.0)
        window._heat_ax.set_ylim(100.0, 101.0)
        self.assertEqual((window._dat_view().bounds['vmin'], window._dat_view().bounds['vmax']), previous)
        self.assertEqual(
            (window._heat_ax.collections[0].norm.vmin, window._heat_ax.collections[0].norm.vmax),
            previous,
        )

    def test_color_edits_preserve_axes_beyond_control_decimal_precision(self):
        window = self.make_window(np.arange(20., dtype=float).reshape(4, 5))
        window._heat_ax.set_xlim(.1234567, 3.7654321)
        window._heat_ax.set_ylim(.2345678, 2.8765432)
        xy = (window._heat_ax.get_xlim(), window._heat_ax.get_ylim())
        self.assertEqual(state(window)['xmin'], '0.1235')
        self.assertEqual(state(window)['ymax'], '2.8765')
        self.assertEqual(window._dat_view().x_limits, xy[0])
        self.assertEqual(window._dat_view().y_limits, xy[1])
        setup_number(window, 'vmin', 6.)
        setup_number(window, 'vmax', 12.)
        self.assertEqual((window._heat_ax.get_xlim(), window._heat_ax.get_ylim()), xy)
        command(window, 'auto_colors')
        self.assertEqual((window._heat_ax.get_xlim(), window._heat_ax.get_ylim()), xy)

    def test_restored_exact_limits_refresh_visible_clipping_scope(self):
        window = self.make_window(np.arange(12.).reshape(3, 4))
        window._restore_effective_plot_limits({'x_limits': [.1234567, 2.7654321],
                                               'y_limits': [-.1234567, 1.8765432],
                                               'color_limits': [2., 5.]})
        self.assertEqual(window._visible_color_values().size, 4)
        self.assertIn('of 4 visible valid cells', state(window)['color_clipping_status'])

    def test_nonfinite_visible_data_keeps_last_valid_color_limits_after_redraw(self) -> None:
        window = self.make_window(np.asarray([[1.0, 2.0], [np.nan, np.nan]]))
        previous = (window._dat_view().bounds['vmin'], window._dat_view().bounds['vmax'])
        window._heat_ax.set_xlim(0.0, 1.0)
        window._heat_ax.set_ylim(1.0, 1.1)
        window._update_spectrum_only()
        self.assertEqual((window._dat_view().bounds['vmin'], window._dat_view().bounds['vmax']), previous)
        self.assertEqual(
            (window._heat_ax.collections[0].norm.vmin, window._heat_ax.collections[0].norm.vmax),
            previous,
        )

    def test_constant_data_has_a_non_degenerate_color_range(self) -> None:
        window = self.make_window(np.full((3, 4), 7.0))
        self.assertLess(window._heat_ax.collections[0].norm.vmin, window._heat_ax.collections[0].norm.vmax)

    def test_single_fixed_bound_stays_fixed_when_auto_range_crosses_it(self) -> None:
        window = self.make_window(np.ones((3, 4)))
        setup_number(window, 'vmax', 0.0)
        set_flag(window, 'vmax_lock', True)
        window._set_view_limits_to_auto()
        self.assertEqual(window._dat_view().bounds['vmax'], 0.0)
        self.assertLess(window._dat_view().bounds['vmin'], 0.0)

    def test_both_fixed_color_bounds_are_preserved(self) -> None:
        window = self.make_window(np.arange(12, dtype=float).reshape(3, 4))
        setup_number(window, 'vmin', 100.0)
        setup_number(window, 'vmax', -100.0)
        set_flag(window, 'vmin_lock', True)
        set_flag(window, 'vmax_lock', True)
        window._set_view_limits_to_auto()
        self.assertEqual(window._dat_view().bounds['vmin'], 100.0)
        self.assertEqual(window._dat_view().bounds['vmax'], -100.0)
        self.assertLess(window._heat_ax.collections[0].norm.vmin, window._heat_ax.collections[0].norm.vmax)

    def test_regional_color_scale_uses_independent_percentiles_and_contiguous_meshes(self) -> None:
        window = self.make_window(np.asarray([
            [1, 2, 3, 4, 100, 200, 300, 400],
            [2, 3, 4, 5, 200, 300, 400, 500],
        ], dtype=float))
        set_choice(window, 'color_scale', "2")

        self.assertEqual(len(window._heat_meshes), 2)
        self.assertEqual(len(window._heat_colorbars), 2)
        self.assertTrue(window.dat_properties.enabled)
        command(window, 'auto_colors')
        first = np.nanpercentile(window.current_cube.z[:, :4], [0.5, 99.5])
        second = np.nanpercentile(window.current_cube.z[:, 4:], [0.5, 99.5])
        np.testing.assert_allclose(window._heat_meshes[0].get_clim(), first)
        np.testing.assert_allclose(window._heat_meshes[1].get_clim(), second)
        self.assertFalse(np.allclose(first, second))
        left = window._heat_meshes[0].get_coordinates()[-1, -1, 0]
        right = window._heat_meshes[1].get_coordinates()[0, 0, 0]
        self.assertEqual(left, right)

    def test_regional_boundary_edit_snaps_to_cell_edge_and_preserves_order(self) -> None:
        window = self.make_window(np.arange(24, dtype=float).reshape(3, 8))
        set_choice(window, 'color_scale', "3")
        setup_number(window, 'edge0', 4.71)
        boundaries = list(window._dat_view().color_region_boundaries)
        edges = window._axis_edges(window.current_cube.energy)
        self.assertIn(boundaries[0], edges[1:-1])
        self.assertLess(boundaries[0], boundaries[1])
        for left, right in zip(window._heat_meshes, window._heat_meshes[1:]):
            self.assertEqual(left.get_coordinates()[-1, -1, 0], right.get_coordinates()[0, 0, 0])

    def test_global_color_scale_remains_single_mesh_and_colorbar(self) -> None:
        window = self.make_window(np.arange(24, dtype=float).reshape(3, 8))
        self.assertEqual(len(window._heat_meshes), 1)
        self.assertEqual(len(window._heat_colorbars), 1)
        expected = np.nanpercentile(window.current_cube.z, [0.5, 99.5])
        np.testing.assert_allclose(window._heat_meshes[0].get_clim(), expected)

    def test_regional_auto_limits_keep_last_range_for_empty_or_nonfinite_region(self) -> None:
        window = self.make_window(np.asarray([
            [1.0, 2.0, np.nan, np.nan],
            [3.0, 4.0, np.nan, np.nan],
        ]))
        set_choice(window, 'color_scale', "2")
        previous = window._heat_meshes[1].get_clim()
        window._heat_ax.set_xlim(0.0, 1.0)
        window._heat_ax.set_ylim(1.0, 2.0)
        self.assertTrue(np.all(np.isfinite(window._heat_meshes[1].get_clim())))
        np.testing.assert_allclose(window._heat_meshes[1].get_clim(), previous)

    def test_three_region_mode_with_one_energy_column_degrades_safely(self) -> None:
        window = self.make_window(np.asarray([[7.0], [7.0]]))
        set_choice(window, 'color_scale', "3")
        self.assertEqual(len(window._heat_meshes), 1)
        self.assertEqual(len(window._heat_colorbars), 1)
        self.assertLess(window._heat_meshes[0].norm.vmin, window._heat_meshes[0].norm.vmax)


if __name__ == "__main__":
    unittest.main()
