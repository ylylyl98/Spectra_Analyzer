import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea
from spectra_app.dataset_state import DatasetSession
from spectra_app.main_window import MainWindow
from spectra_app.plot_presentation import NormSpec, PlotPresentation
from spectra_app.plot_state import validate_plot_state, active_plot_recipe
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])

class FormalPlotWorkflowTests(unittest.TestCase):
    def test_only_plot_owns_image_export_buttons_and_analysis_keeps_navigation(self):
        from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
        w = self.window
        analysis = w.plot_tabs.widget(0)
        self.assertFalse(any(button.text() in ('Export PNG', 'Export SVG')
                             for button in analysis.findChildren(QPushButton)))
        self.assertIs(w.export_png_btn, w.plot_page.export_png_btn)
        self.assertTrue(w.graph_command_host.isAncestorOf(w.export_png_btn))
        self.assertNotIn('save_figure', w.toolbar._actions)
        self.assertTrue({'home', 'back', 'forward', 'pan', 'zoom'} <= set(w.toolbar._actions))
        self.assertTrue(any(item[3] == 'save_figure' for item in NavigationToolbar2QT.toolitems))
        self.render()
        self.assertTrue(w.export_png_btn.isEnabled())
        w._invalidate_plot_page(schedule=False)
        self.assertFalse(w.export_png_btn.isEnabled())

    def test_permanent_dimensions_follow_page_pixels_frame_and_valid_owner(self):
        w = self.window
        p = w.plot_page
        self.assertTrue(hasattr(p, 'dimensions_label'), 'Permanent Plot dimensions are missing')
        self.assertTrue(p.dimensions_label.wordWrap())
        self.assertTrue(w.dat_details_dock.isAncestorOf(p.dimensions_label))
        self.assertFalse(w.graph_command_host.isAncestorOf(p.dimensions_label))
        self.assertIn('Page including labels and colorbars: 3.602 × 3.45 in', p.dimensions_label.text())
        self.assertIn('91.5 × 87.6 mm', p.dimensions_label.text())
        self.assertIn('2.900 × 2.800 in (requested; preview pending)', p.dimensions_label.text())
        p.frame_auto.setChecked(True)
        self.assertIn('Data plot frame: Auto (preview pending)', p.dimensions_label.text())
        self.assertIn('PNG: 1081 × 1035 px @ 300 DPI', p.dimensions_label.text())
        old = self.render()
        self.assertIn('actual', p.dimensions_label.text())
        p.aspect_lock.setChecked(False)
        p.width_spin.setValue(4.203)
        p.height_spin.setValue(4.017)
        p.dpi_spin.setValue(301)
        pending = p.dimensions_label.text()
        self.assertIn('Page including labels and colorbars: 4.203 × 4.017 in', pending)
        self.assertIn('Data plot frame: Auto (preview pending)', pending)
        self.assertIn('PNG: 1265 × 1209 px @ 301 DPI', pending)
        self.assertNotIn('actual', pending)
        self.assertFalse(p.commit_rendered_page(old))
        self.assertEqual(p.dimensions_label.text(), pending)
        page = self.render()
        summary = p.dimensions_label.text()
        expected_frame = ' × '.join(f'{value:.3f}' for value in page.plot_size_in)
        self.assertIn(f'Data plot frame: {expected_frame} in (actual)', summary)
        p.set_zoom('200%')
        self.assertEqual(p.dimensions_label.text(), summary)
        self.assertEqual(p.preview.svg_bytes, page.svg_bytes)
        png = w.export_plot_page('png')
        with Image.open(png) as image:
            self.assertEqual(image.size, tuple(round(value * 301) for value in page.figure_size_in))
        p.frame_auto.setChecked(False)
        p.frame_width_spin.setValue(40)
        w._render_plot_page()
        self.assertIsNone(p.rendered_page)
        self.assertNotIn('actual', p.dimensions_label.text())
        self.assertIn('requested; preview pending', p.dimensions_label.text())
        w._load_dat_file_direct(self.paths[1])
        self.assertIsNot(w.plot_page, p)
        self.assertIn('40.000', p.dimensions_label.text())
        self.assertIn('2.900 × 2.800 in (requested; preview pending)', w.plot_page.dimensions_label.text())

    def test_unit_display_preserves_literal_text_and_reports_clipping_percentages(self):
        w = self.window
        w.signal_title_combo.setCurrentText('DR manual (a. u.)')
        p = w.plot_page
        self.assertTrue(hasattr(p, 'colorbar_unit'), 'Optional unit display control is missing')
        p.colorbar_unit.setChecked(False)
        page = self.render()
        self.assertIn('DR manual', page.svg_bytes.decode())
        self.assertNotIn('DR manual (a. u.)', page.svg_bytes.decode())
        self.assertEqual(w.signal_title_combo.currentText(), 'DR manual (a. u.)')
        self.assertIn('%', p.status_label.text())
        p.colorbar_unit.setChecked(True)
        self.assertIn('DR manual (arb. units)', self.render().svg_bytes.decode())
        self.assertEqual(w.signal_title_combo.currentText(), 'DR manual (a. u.)')
        for mode in ('Global', '2', '3'):
            index = 0 if mode == 'Global' else w.color_scale_combo.findData(mode)
            w.color_scale_combo.setCurrentIndex(index)
            label = (w._heat_signal_label.get_text() if mode != 'Global'
                     else w._heat_colorbar.ax.get_xlabel())
            self.assertEqual(label, 'DR manual (arb. units)')
        self.assertEqual(w._spec_ax.get_ylabel(), 'Intensity (arb. units)')

    def test_friendly_palette_and_optional_unit_roundtrip_through_real_sidecar(self):
        w = self.window
        w.cmap_combo.setCurrentText('RdBu_r_0.6')
        w.signal_title_combo.setCurrentText('ΔR/R₀ (a. u.)')
        w.plot_page.colorbar_unit.setChecked(False)
        w._save_current_plot_state()
        sidecar = w._plot_state_store.path_for(self.paths[0])
        payload = json.loads(sidecar.read_text(encoding='utf-8'))
        self.assertEqual(active_plot_recipe(payload['plot'])['colormap'], 'RdBu_r_p0p6')
        w._load_dat_file_direct(self.paths[1])
        w._load_dat_file_async(self.paths[0])
        from tests.settings_helpers import wait_for_background_jobs
        wait_for_background_jobs(w)
        self.assertEqual(w.cmap_combo.currentText(), 'RdBu_r_0.6')
        self.assertEqual(w._heat_mesh.get_cmap().name, 'RdBu_r_p0p6')
        self.assertFalse(w.plot_page.colorbar_unit.isChecked())
        self.assertEqual(w.signal_title_combo.currentText(), 'ΔR/R₀ (a. u.)')
        self.assertIn('ΔR/R₀', self.render().svg_bytes.decode())
        # Actual old-v3 restore supplies the additive default and keeps text/palette.
        w.current_dat_path = None
        payload['plot'] = active_plot_recipe(payload['plot'])
        payload['version'] = 3
        payload['plot']['presentation']['colorbar'].pop('show_unit')
        sidecar.write_text(json.dumps(payload), encoding='utf-8')
        w._load_dat_file_direct(self.paths[0])
        self.assertTrue(w.plot_page.colorbar_unit.isChecked())
        self.assertEqual(w.signal_title_combo.currentText(), 'ΔR/R₀ (a. u.)')
        self.assertEqual(w.cmap_combo.currentText(), 'RdBu_r_0.6')
        w.cmap_combo.setCurrentText('jet'); w._save_current_plot_state()
        w._load_dat_file_direct(self.paths[1]); w._load_dat_file_direct(self.paths[0])
        self.assertEqual(w.cmap_combo.currentText(), 'jet')

    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.window = MainWindow()
        self.errors = []
        self.window._show_error = self.errors.append
        self.paths = []
        for name in ('a', 'b'):
            path = self.folder / (name + '.dat')
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
            self.paths.append(path)
        self.window._load_dat_file_direct(self.paths[0])

    def tearDown(self):
        self.window.close()
        APP.processEvents()

    def render(self):
        self.window._render_plot_page()
        page = self.window.plot_page.rendered_page
        self.assertIsNotNone(page, self.window.plot_page.status_label.text())
        return page

    def test_v4_roundtrip_and_legacy_defaults_preserve_old_view(self):
        w = self.window
        self.assertTrue(hasattr(w.workspace.active, 'presentation'), 'DatasetSession needs formal presentation state')
        w.set_plot_presentation(replace(w.workspace.active.presentation, width_in=4.2, aspect_locked=False))
        w._save_current_plot_state()
        payload = json.loads(w._plot_state_store.path_for(self.paths[0]).read_text(encoding='utf-8'))
        self.assertEqual(payload['version'], 4)
        self.assertEqual(active_plot_recipe(payload['plot'])['presentation']['width_in'], 4.2)
        for version in (1, 2):
            old = dict(active_plot_recipe(payload['plot']))
            old.pop('norm'); old.pop('presentation'); old.pop('color_region_limits', None)
            if version == 1:
                old.pop('title_settings'); old.pop('processing')
            migrated = validate_plot_state(old, version=version)
            self.assertEqual(migrated['x_limits'], old['x_limits'])
            self.assertEqual(migrated['norm'], {'kind': 'linear', 'linthresh': 1.0})
            self.assertEqual(migrated['presentation']['width_in'], 3.3)
        loaded, notice = w._plot_state_store.load(self.paths[0], w._loaded_source_fingerprint)
        self.assertEqual(notice, '')
        self.assertEqual(loaded['presentation']['width_in'], 4.2)
        self.assertFalse({'svg_bytes', 'activation_epoch', 'z', 'science_revision'} & set(payload['plot']))

    def test_v3_rejects_malformed_norm_and_presentation_before_restore(self):
        recipe = self.window._current_plot_state()
        self.assertIn('norm', recipe, 'v3 recipe needs shared NormSpec')
        for field, value in [('norm', {'kind': 'log', 'linthresh': False}),
                             ('presentation', {'width_in': float('nan')}),
                             ('presentation', {'width_in': True}),
                             ('presentation', {'canvas': 'runtime'}),
                             ('norm', {'kind': 'bogus'})]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_plot_state({**recipe, field: value}, version=3)

    def test_v3_sidecar_without_new_geometry_fields_restores_with_defaults(self):
        recipe = self.window._current_plot_state()
        presentation = recipe['presentation']
        for name in ('frame_width_in', 'frame_height_in'):
            presentation.pop(name, None)
        for name in ('alignment', 'signal_position', 'frame_gap_in', 'show_extensions', 'show_unit'):
            presentation['colorbar'].pop(name, None)
        migrated = validate_plot_state(recipe, version=3)
        self.assertIn('frame_width_in', migrated['presentation'], 'v3 sidecars need optional frame defaults')
        self.assertIsNone(migrated['presentation']['frame_width_in'])
        self.assertEqual(migrated['presentation']['colorbar']['alignment'], 'right')
        self.assertFalse(migrated['presentation']['colorbar']['show_extensions'])
        self.assertTrue(migrated['presentation']['colorbar']['show_unit'])

    def test_native_frame_controls_remain_independent_of_locked_page_dimensions(self):
        p = self.window.plot_page
        self.assertTrue(hasattr(p, 'frame_auto'), 'Independent heatmap frame controls are missing')
        before = self.window.current_cube.z.copy()
        self.assertTrue(p.frame_width_spin.isEnabled())
        np.testing.assert_allclose(self.render().plot_size_in, [2.9, 2.8])
        p.frame_auto.setChecked(True)
        automatic = self.render()
        self.assertFalse(p.frame_width_spin.isEnabled())
        self.assertGreater(p.frame_width_spin.value(), 2.)
        p.frame_auto.setChecked(False)
        custom = self.render()
        np.testing.assert_allclose(custom.plot_size_in, automatic.plot_size_in, atol=.001)
        p.frame_width_spin.setValue(2.1)
        p.frame_height_spin.setValue(1.5)
        p.bar_frame_gap_spin.setValue(.10)
        p.bar_height_spin.setValue(.08)
        page = self.render()
        np.testing.assert_allclose(page.plot_size_in, [2.1, 1.5])
        self.assertEqual(page.figure_size_in, (91.5/25.4, 3.45))
        p.width_spin.setValue(6.6)
        page = self.render()
        self.assertEqual(page.figure_size_in, (6.6, round(6.6*3.45/(91.5/25.4), 3)))
        np.testing.assert_allclose(page.plot_size_in, [2.1, 1.5])
        p.frame_width_spin.setValue(40.)
        self.window._render_plot_page()
        self.assertIsNone(p.rendered_page)
        self.assertFalse(p.export_svg_btn.isEnabled())
        self.assertIn('frame', p.status_label.text().lower())
        np.testing.assert_array_equal(self.window.current_cube.z, before)

    def test_dataset_switch_retains_page_and_rejects_other_owner_bytes(self):
        w = self.window
        self.assertTrue(hasattr(w, 'plot_page'), 'Analysis/Plot tabs are missing')
        a = w.workspace.active
        fit = object(); a.fit_result = fit
        w.set_plot_presentation(replace(a.presentation, width_in=4.1, aspect_locked=False))
        old_a = self.render()
        a_page = w.plot_page
        w._load_dat_file_direct(self.paths[1])
        b = w.workspace.active
        w.set_plot_presentation(replace(b.presentation, width_in=4.7, aspect_locked=False))
        old_b = self.render()
        w.activate_dataset(a.dataset_id)
        self.assertEqual(a.presentation.width_in, 4.1)
        self.assertEqual(b.presentation.width_in, 4.7)
        self.assertIs(a.fit_result, fit)
        self.assertIs(w.plot_page, a_page)
        self.assertIs(w.plot_page.rendered_page, old_a)
        self.assertFalse(w.plot_page.commit_rendered_page(old_b))
        self.assertTrue(w.plot_page.export_svg_btn.isEnabled())
        self.assertEqual(w._plot_owner_token(), old_a.owner_token)

    def test_final_byte_export_and_png_share_page_zoom_never_renders(self):
        w = self.window
        self.assertTrue(hasattr(w, 'plot_page'), 'Formal page export is missing')
        page = self.render()
        with patch('spectra_app.main_window.render_hybrid_svg', side_effect=AssertionError('zoom rendered')):
            w.plot_page.set_zoom('200%')
            self.assertIs(w.plot_page.rendered_page, page)
            self.assertEqual(w.plot_page.rendered_page.svg_bytes, page.svg_bytes)
        svg = w.export_plot_page('svg')
        self.assertEqual(svg.read_bytes(), page.svg_bytes)
        png = w.export_plot_page('png')
        with Image.open(png) as image:
            self.assertEqual(image.size, (1081, 1035))
            metadata = json.loads(image.info['SpectraAnalyzer'])
            self.assertEqual(metadata['source_path'], str(self.paths[0]))
            self.assertAlmostEqual(image.info['dpi'][0], 300, delta=.1)
        self.assertNotEqual(w.export_plot_page('svg'), svg)
        self.assertEqual(svg.read_bytes(), page.svg_bytes)

    def test_content_change_and_render_failure_immediately_disable_old_export(self):
        w = self.window
        self.assertTrue(hasattr(w, 'plot_page'), 'Formal page invalidation is missing')
        self.render()
        w._heat_ax.set_xlim(1.1, 1.8)
        self.assertIsNone(w.plot_page.rendered_page)
        self.assertFalse(w.plot_page.export_svg_btn.isEnabled())
        with patch('spectra_app.main_window.render_hybrid_svg', side_effect=ValueError('layout too small')):
            w._render_plot_page()
        self.assertIsNone(w.plot_page.rendered_page)
        self.assertIn('layout too small', w.plot_page.status_label.text())
        with self.assertRaises(ValueError):
            w.export_plot_page('svg')

    def test_log_masks_analysis_and_auto_limits_positive_without_processing(self):
        w = self.window
        w.plot_page.height_spin.setValue(4.5)  # Space for the required missing-color note.
        self.assertTrue(hasattr(w, 'mapping_combo'), 'Shared display mapping is missing')
        session = w.workspace.active
        cube = session.current_cube; before = cube.z.copy()
        session.fit_result = object(); fit = session.fit_result
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('science recalculated')):
            w.mapping_combo.setCurrentIndex(w.mapping_combo.findData('log'))
        self.assertEqual(session.view.norm.kind, 'log')
        self.assertIs(session.current_cube, cube)
        self.assertIs(session.fit_result, fit)
        np.testing.assert_array_equal(cube.z, before)
        self.assertEqual(int(np.ma.getmaskarray(w._heat_mesh.get_array()).sum()), 12)
        expected = np.percentile(np.concatenate((np.arange(1,13), 10*np.arange(1,13))), [.5,99.5])
        np.testing.assert_allclose(w._heat_mesh.get_clim(), expected, atol=1e-4)
        page = self.render()
        self.assertEqual(page.nonpositive_count, 12)
        self.assertIn('12', w.plot_page.status_label.text())
        self.assertTrue(hasattr(w, 'mapping_status_label'), 'Log masking count must also be visible in Analysis')
        self.assertIn('12', w.mapping_status_label.text())

    def test_regional_limits_and_norm_roundtrip_without_recomputing_ranges(self):
        w = self.window
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        session = w.workspace.active
        session.view.last_valid_region_color_limits = [(-20., 30.), (-40., 130.)]
        session.view.norm = NormSpec('symlog', 2.)
        w._apply_color_limits_to_mesh()
        w._save_current_plot_state()
        loaded, notice = w._plot_state_store.load(self.paths[0], w._loaded_source_fingerprint)
        self.assertIn('color_region_limits', active_plot_recipe(loaded), 'Regional effective ranges must survive sidecars')
        self.assertEqual(loaded['color_region_limits'], [[-20., 30.], [-40., 130.]])
        self.assertEqual(loaded['norm'], {'kind': 'symlog', 'linthresh': 2.})
        w.close()
        replacement = MainWindow()
        try:
            replacement._load_dat_file_direct(self.paths[0])
            self.assertEqual(replacement.workspace.active.view.last_valid_region_color_limits,
                             [(-20., 30.), (-40., 130.)])
            self.assertEqual([mesh.get_clim() for mesh in replacement._heat_meshes],
                             [(-20., 30.), (-40., 130.)])
        finally:
            replacement.close()

    def test_v3_mapping_specific_limit_validation_and_missing_new_defaults(self):
        recipe = self.window._current_plot_state()
        for kind in ('log', 'zero-centered'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_plot_state({**recipe, 'norm': {'kind': kind}, 'color_limits': [1., 10.] if kind == 'zero-centered' else [-1., 10.]}, version=3)
        for invalid in ([[1., 1.]], [[False, 5.]], [[1., float('inf')]], 'runtime'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_plot_state({**recipe, 'color_scale_mode': '2', 'color_region_limits': invalid}, version=3)
        missing = {key: value for key, value in recipe.items() if key not in ('norm', 'presentation', 'color_region_limits')}
        loaded = validate_plot_state(missing, version=3)
        self.assertIn('color_region_limits', loaded, 'Missing v3 ranges must default safely')
        self.assertEqual(loaded['color_region_limits'], [])
        self.assertEqual(loaded['norm']['kind'], 'linear')

    def test_zero_centered_auto_limits_make_signed_and_one_sided_data_usable(self):
        w = self.window
        for sign, regional in ((1, False), (-1, True)):
            with self.subTest(sign=sign, regional=regional):
                cube = replace(w.current_cube, z=sign * np.abs(w.current_cube.z))
                w.workspace.active.current_cube = cube
                before = cube.z.copy()
                if regional:
                    w.plot_page.height_spin.setValue(4.5)  # Separate regional header rows.
                    w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
                w.mapping_combo.setCurrentIndex(w.mapping_combo.findData('zero-centered'))
                w._on_color_mapping_changed()
                page = self.render()
                for mesh in w._heat_meshes:
                    low, high = mesh.get_clim()
                    self.assertLess(low, 0)
                    self.assertGreater(high, 0)
                    self.assertEqual(mesh.norm(0), .5)
                self.assertEqual(page.nonpositive_count, 0)
                np.testing.assert_array_equal(cube.z, before)

    def test_peak_changes_invalidate_an_opted_in_page_and_use_session_geometry(self):
        w = self.window
        session = w.workspace.active
        w.plot_page.include_peaks.setChecked(True)
        session.peak_gate_idx = 1
        session.peak_indices = np.array([2])
        w._update_spectrum_only()
        before = self.render()
        snapshot = w.snapshot_plot_dataset()
        self.assertEqual([(peak.x, peak.y, peak.text) for peak in snapshot.peaks], [(1.2, 1., '1')])
        session.peak_indices = np.array([3])
        w._update_spectrum_only()
        self.assertTrue(w.plot_page.rendered_page is None, 'Changed opted-in scientific markers must invalidate current output')
        after = self.render()
        self.assertNotEqual(after.svg_bytes, before.svg_bytes)

    def test_native_page_controls_couple_dimensions_and_exchange_display_only(self):
        w = self.window
        page_ui = w.plot_page
        before = w.current_cube.z.copy()
        page_ui.width_spin.setValue(6.6)
        self.assertEqual(w.workspace.active.presentation.height_in, round(6.6*3.45/(91.5/25.4), 3))
        page_ui.aspect_lock.setChecked(False)
        page_ui.height_spin.setValue(4.5)
        page_ui.axis_controls['exchange_xy'].setChecked(True)
        page_ui.axis_controls['flip_x'].setChecked(True)
        page = self.render()
        self.assertEqual(page.figure_size_in, (6.6, 4.5))
        self.assertEqual(page.display_x_limits, (2., 0.))
        np.testing.assert_allclose(page.display_y_limits, (1., 2.1))
        self.assertEqual(page_ui.preview.svg_bytes, page.svg_bytes)
        np.testing.assert_array_equal(w.current_cube.z, before)

    def test_inactive_snapshot_never_projects_widgets_or_reads_artists(self):
        w = self.window
        a = w.workspace.active
        original = a.current_cube.z.copy()
        w._load_dat_file_direct(self.paths[1])
        active = w.workspace.active
        with patch.object(w._heat_ax, 'get_xlim', side_effect=AssertionError('live artist read')):
            snapshot = w.snapshot_plot_dataset(a)
        self.assertIs(w.workspace.active, active)
        self.assertEqual(snapshot.dataset_id, a.dataset_id)
        np.testing.assert_array_equal(snapshot.z, original)
        self.assertFalse(snapshot.z.flags.writeable)

    def test_invalid_custom_tick_editor_blocks_export_through_other_view_edits(self):
        w = self.window
        self.render()
        w.plot_page.custom_ticks.setChecked(True)
        self.assertIsNone(w.plot_page.rendered_page)
        w.plot_title_edit.setText('A new title while ticks remain invalid')
        w._render_plot_page()
        self.assertTrue(w.plot_page.rendered_page is None,
                        'Unaccepted presentation editor input must not silently export the old format')
        w.plot_page.tick_values_edit.setText('-10, 50, 100')
        w.plot_page.tick_values_edit.editingFinished.emit()
        w.plot_page.height_spin.setValue(4.5)  # The deliberately long edited title needs a row.
        self.render()
        self.assertTrue(w.plot_page.export_svg_btn.isEnabled())

    def test_mapping_no_positive_values_shows_error_and_cannot_export(self):
        w = self.window
        self.assertTrue(hasattr(w, 'mapping_combo'), 'Shared display mapping is missing')
        self.render()
        w.workspace.active.current_cube = replace(w.current_cube, z=-np.ones_like(w.current_cube.z))
        w.mapping_combo.setCurrentIndex(w.mapping_combo.findData('log'))
        w._render_plot_page()
        self.assertIsNone(w.plot_page.rendered_page)
        self.assertIn('positive', w.plot_page.status_label.text().lower())
        self.assertFalse(w.plot_page.export_svg_btn.isEnabled())

if __name__ == '__main__':
    unittest.main()

