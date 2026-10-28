from tests import dat_properties_quick_helpers as dat
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from PySide6.QtCore import QObject
from PySide6.QtQuick import QQuickItem
from spectra_app.dataset_state import DatasetSession
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import application, wait_until
from spectra_app.plot_presentation import NormSpec, PlotPresentation
from spectra_app.plot_state import validate_plot_state, active_plot_recipe

from tests.formal_quick_helpers import item, pump


class FormalPlotWorkflowTests(QuickMigrationCase):
    def test_only_plot_owns_image_export_buttons_and_analysis_keeps_navigation(self):
        from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
        w = self.window
        analysis = item(w, 'applicationDatCanvas')
        self.assertIsInstance(analysis, QQuickItem)
        command_bar = item(w, 'formalCommands')
        self.assertIsInstance(item(command_bar, 'formal_export_png'), QQuickItem)
        self.assertFalse(analysis.isAncestorOf(command_bar))
        self.assertNotIn('save_figure', {action['key'] for action in w.analysis_navigation.actions})
        self.assertTrue(w.request_outer_view('analysis'))
        pump()
        navigation = item(w, 'AnalysisNavigation')
        visible_controls = [control for control in w.root_window.findChildren(QQuickItem)
                            if control.isVisible()]
        self.assertFalse(any(control.objectName() in ('formal_export_png', 'formal_export_svg')
                             for control in visible_controls))
        self.assertFalse(any(control.objectName() == 'formalCommands'
                             for control in visible_controls))
        self.assertFalse(any(control.property('command') == 'save_figure'
                             for control in navigation.findChildren(QObject)))
        for command in ('home', 'back', 'forward', 'pan', 'zoom'):
            control = item(navigation, 'analysisNav' + command.title())
            self.assertEqual(control.property('command'), command)
        self.assertTrue(any(item[3] == 'save_figure' for item in NavigationToolbar2QT.toolitems))
        self.assertTrue(w.request_outer_view('workspace'))
        pump()
        self.render()
        self.assertTrue(w.plot_page.command_enabled("export_png"))
        w._invalidate_plot_page(schedule=False)
        self.assertFalse(w.plot_page.command_enabled("export_png"))

    def test_permanent_dimensions_follow_page_pixels_frame_and_valid_owner(self):
        w = self.window
        p = w.plot_page
        self.assertTrue(hasattr(p, 'dimensions_text'), 'Permanent Plot dimensions are missing')
        self.assertLessEqual(item(w, 'formal_dimensions').width(), item(w, 'formalProperties').width())
        properties = item(w, 'applicationProperties')
        self.assertTrue(properties.isAncestorOf(item(w, 'formalProperties')))
        self.assertFalse(item(w, 'formalCommands').isAncestorOf(item(w, 'formalProperties')))
        baseline = self.render()
        self.assertEqual(baseline.owner_token, w._plot_owner_token())
        self.assertIn('actual', p.dimensions_text)
        w._invalidate_plot_page(schedule=False)
        self.assertIsNone(p.rendered_page)
        self.assertFalse(p.command_enabled('export_png'))
        self.assertIn('Page including labels and colorbars: 3.602 × 3.45 in', p.dimensions_text)
        self.assertIn('91.5 × 87.6 mm', p.dimensions_text)
        self.assertIn('2.900 × 2.800 in (requested; preview pending)', p.dimensions_text)
        p.formal.setValue("frame_auto", True)
        self.assertIn('Data plot frame: Auto (preview pending)', p.dimensions_text)
        self.assertIn('PNG: 1081 × 1035 px @ 300 DPI', p.dimensions_text)
        old = self.render()
        self.assertIn('actual', p.dimensions_text)
        p.formal.setValue("aspect_locked", False)
        p.formal.setValue("width_in", 4.203)
        p.formal.setValue("height_in", 4.017)
        p.formal.setValue("heatmap_dpi", 301)
        pending = p.dimensions_text
        self.assertIn('Page including labels and colorbars: 4.203 × 4.017 in', pending)
        self.assertIn('Data plot frame: Auto (preview pending)', pending)
        self.assertIn('PNG: 1265 × 1209 px @ 301 DPI', pending)
        self.assertNotIn('actual', pending)
        self.assertFalse(p.commit_rendered_page(old))
        self.assertEqual(p.dimensions_text, pending)
        page = self.render()
        summary = p.dimensions_text
        expected_frame = ' × '.join(f'{value:.3f}' for value in page.plot_size_in)
        self.assertIn(f'Data plot frame: {expected_frame} in (actual)', summary)
        p.set_zoom('200%')
        self.assertEqual(p.dimensions_text, summary)
        self.assertEqual(p.preview.svg_bytes, page.svg_bytes)
        w.open_graph_document()
        png = w.export_plot_page('png')
        with Image.open(png) as image:
            self.assertEqual(image.size, tuple(round(value * 301) for value in page.figure_size_in))
        p.formal.setValue("frame_auto", False)
        p.formal.setValue("frame_width_in", 40)
        w._render_plot_page()
        self.assertIsNone(p.rendered_page)
        self.assertNotIn('actual', p.dimensions_text)
        self.assertIn('requested; preview pending', p.dimensions_text)
        w._load_dat_file_direct(self.paths[1])
        self.assertIsNot(w.plot_page, p)
        self.assertIn('40.000', p.dimensions_text)
        self.assertIn('2.900 × 2.800 in (requested; preview pending)', w.plot_page.dimensions_text)

    def test_unit_display_preserves_literal_text_and_keeps_successful_footer_quiet(self):
        w = self.window
        w.dat_properties.setLabel('signal_title', 'DR manual (a. u.)', w.dat_properties.ownerToken)
        p = w.plot_page
        self.assertIn('bar_show_unit', p.formal.state['fields'])
        p.formal.setValue("bar_show_unit", False)
        page = self.render()
        self.assertIn('DR manual', page.svg_bytes.decode())
        self.assertNotIn('DR manual (a. u.)', page.svg_bytes.decode())
        self.assertEqual(dat.state(w)['signal_title'], 'DR manual (a. u.)')
        self.assertEqual(p.status_text, '')
        self.assertTrue(not bool(p.status_text))
        p.formal.setValue("bar_show_unit", True)
        self.assertIn('DR manual (arb. units)', self.render().svg_bytes.decode())
        self.assertEqual(dat.state(w)['signal_title'], 'DR manual (a. u.)')
        self.assertTrue(w.request_outer_view('analysis'))
        pump(80)
        for mode in ('Global', '2', '3'):
            dat.set_choice(w, 'color_scale', 'global' if mode == 'Global' else mode)
            pump()
            label = (w._heat_signal_label.get_text() if mode != 'Global'
                     else w._heat_colorbar.ax.get_xlabel())
            self.assertEqual(label, 'DR manual (arb. units)')
        self.assertEqual(w._spec_ax.get_ylabel(), 'Intensity (arb. units)')

    def test_friendly_palette_and_optional_unit_roundtrip_through_real_sidecar(self):
        w = self.window
        dat.set_choice(w, 'colormap', 'RdBu_r_p0p6')
        w.dat_properties.setLabel('signal_title', 'ΔR/R₀ (a. u.)', w.dat_properties.ownerToken)
        w.plot_page.formal.setValue("bar_show_unit", False)
        w._save_current_plot_state()
        sidecar = w._plot_state_store.path_for(self.paths[0])
        payload = json.loads(sidecar.read_text(encoding='utf-8'))
        self.assertEqual(active_plot_recipe(payload['plot'])['colormap'], 'RdBu_r_p0p6')
        w._load_dat_file_direct(self.paths[1])
        w._load_dat_file_async(self.paths[0])
        from tests.settings_helpers import wait_for_background_jobs
        wait_for_background_jobs(w)
        self.assertEqual(next(entry['label'] for entry in dat.state(w)['colormaps'] if entry['value'] == dat.state(w)['colormap']), 'RdBu_r_0.6')
        self.assertEqual(w._heat_mesh.get_cmap().name, 'RdBu_r_p0p6')
        self.assertFalse(w.plot_page.formal.state["fields"]["bar_show_unit"])
        self.assertEqual(dat.state(w)['signal_title'], 'ΔR/R₀ (a. u.)')
        self.assertIn('ΔR/R₀', self.render().svg_bytes.decode())
        # Actual old-v3 restore supplies the additive default and keeps text/palette.
        w.current_dat_path = None
        payload['plot'] = active_plot_recipe(payload['plot'])
        payload['version'] = 3
        payload['plot']['presentation']['colorbar'].pop('show_unit')
        sidecar.write_text(json.dumps(payload), encoding='utf-8')
        w._load_dat_file_direct(self.paths[0])
        self.assertTrue(w.plot_page.formal.state["fields"]["bar_show_unit"])
        self.assertEqual(dat.state(w)['signal_title'], 'ΔR/R₀ (a. u.)')
        self.assertEqual(next(entry['label'] for entry in dat.state(w)['colormaps'] if entry['value'] == dat.state(w)['colormap']), 'RdBu_r_0.6')
        dat.set_choice(w, 'colormap', 'jet'); w._save_current_plot_state()
        w._load_dat_file_direct(self.paths[1]); w._load_dat_file_direct(self.paths[0])
        self.assertEqual(next(entry['label'] for entry in dat.state(w)['colormaps'] if entry['value'] == dat.state(w)['colormap']), 'jet')

    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.errors = self.quick_errors
        self.paths = []
        for name in ('a', 'b'):
            path = self.folder / (name + '.dat')
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
            self.paths.append(path)
        self.window._load_dat_file_direct(self.paths[0])
        # Interactive Quick actions require the real registry pane to be open.
        self.window.open_graph_document()
        self.window.dat_details_action.setChecked(True)
        self.window.request_task(2)
        pump(90)

    def tearDown(self):
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def render(self):
        self.window._render_plot_page()
        page = self.window.plot_page.rendered_page
        self.assertIsNotNone(page, self.window.plot_page.status_text)
        return page

    def test_current_roundtrip_and_legacy_defaults_preserve_old_view(self):
        w = self.window
        self.assertTrue(hasattr(w.workspace.active, 'presentation'), 'DatasetSession needs formal presentation state')
        w.set_plot_presentation(replace(w.workspace.active.presentation, width_in=4.2, aspect_locked=False))
        w._save_current_plot_state()
        payload = json.loads(w._plot_state_store.path_for(self.paths[0]).read_text(encoding='utf-8'))
        self.assertEqual(payload['version'], 6)
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
        self.assertEqual(active_plot_recipe(loaded)['presentation']['width_in'], 4.2)
        self.assertFalse({'svg_bytes', 'activation_epoch', 'z', 'science_revision'} & set(payload['plot']))
        for graph in payload['plot']['graphs']:
            self.assertFalse({'svg_bytes', 'activation_epoch', 'z', 'science_revision'} & set(graph['plot']))

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

    def test_quick_frame_controls_remain_independent_of_locked_page_dimensions(self):
        p = self.window.plot_page
        self.assertIn('frame_auto', p.formal.state['fields'])
        before = self.window.current_cube.z.copy()
        self.assertTrue(not p.formal.state['fields']['frame_auto'])
        np.testing.assert_allclose(self.render().plot_size_in, [2.9, 2.8])
        p.formal.setValue("frame_auto", True)
        automatic = self.render()
        self.assertFalse(not p.formal.state['fields']['frame_auto'])
        self.assertGreater(float(p.formal.state["fields"]["frame_width_in"]), 2.)
        p.formal.setValue("frame_auto", False)
        custom = self.render()
        np.testing.assert_allclose(custom.plot_size_in, automatic.plot_size_in, atol=.001)
        p.formal.setValue("frame_width_in", 2.1)
        p.formal.setValue("frame_height_in", 1.5)
        p.formal.setValue("bar_frame_gap_in", .10)
        p.formal.setValue("bar_height_in", .08)
        page = self.render()
        np.testing.assert_allclose(page.plot_size_in, [2.1, 1.5])
        self.assertEqual(page.figure_size_in, (91.5/25.4, 3.45))
        p.formal.setValue("width_in", 6.6)
        page = self.render()
        self.assertEqual(page.figure_size_in, (6.6, round(6.6*3.45/(91.5/25.4), 3)))
        np.testing.assert_allclose(page.plot_size_in, [2.1, 1.5])
        p.formal.setValue("frame_width_in", 40.)
        self.window._render_plot_page()
        self.assertIsNone(p.rendered_page)
        self.assertFalse(p.command_enabled("export_svg"))
        self.assertIn('frame', p.status_text.lower())
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
        self.assertTrue(w.plot_page.command_enabled("export_svg"))
        self.assertEqual(w._plot_owner_token(), old_a.owner_token)

    def test_final_byte_export_and_png_share_page_zoom_never_renders(self):
        w = self.window
        self.assertTrue(hasattr(w, 'plot_page'), 'Formal page export is missing')
        page = self.render()
        with patch('spectra_app.main_window.render_hybrid_svg', side_effect=AssertionError('zoom rendered')):
            w.plot_page.set_zoom('200%')
            self.assertIs(w.plot_page.rendered_page, page)
            self.assertEqual(w.plot_page.rendered_page.svg_bytes, page.svg_bytes)
        w.open_graph_document()
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
        self.assertFalse(w.plot_page.command_enabled("export_svg"))
        with patch('spectra_app.main_window.render_hybrid_svg', side_effect=ValueError('layout too small')):
            w._render_plot_page()
            self.assertIsNone(w.plot_page.rendered_page)
            self.assertIn('layout too small', w.plot_page.status_text)
            # Export performs a fresh render when there is no accepted page;
            # keep the renderer failure active through that validation retry.
            with self.assertRaises(ValueError):
                w.export_plot_page('svg')

    def test_log_masks_analysis_and_auto_limits_positive_without_processing(self):
        w = self.window
        w.plot_page.formal.setValue("height_in", 4.5)  # Space for the required missing-color note.
        self.assertTrue('mappings' in dat.state(w), 'Shared display mapping is missing')
        session = w.workspace.active
        cube = session.current_cube; before = cube.z.copy()
        session.fit_result = object(); fit = session.fit_result
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('science recalculated')):
            dat.set_choice(w, 'mapping', 'log')
        self.assertEqual(session.view.norm.kind, 'log')
        self.assertIs(session.current_cube, cube)
        self.assertIs(session.fit_result, fit)
        np.testing.assert_array_equal(cube.z, before)
        self.assertEqual(int(np.ma.getmaskarray(w._heat_mesh.get_array()).sum()), 12)
        expected = np.percentile(np.concatenate((np.arange(1,13), 10*np.arange(1,13))), [.5,99.5])
        np.testing.assert_allclose(w._heat_mesh.get_clim(), expected, atol=1e-4)
        page = self.render()
        self.assertEqual(page.nonpositive_count, 12)
        self.assertEqual(w.plot_page.status_text, '')
        self.assertTrue(not bool(w.plot_page.status_text))
        self.assertTrue('mapping_status' in dat.state(w), 'Log masking count must also be visible in Analysis')
        self.assertIn('12', dat.state(w)['mapping_status'])

    def test_regional_limits_and_norm_roundtrip_without_recomputing_ranges(self):
        w = self.window
        dat.set_choice(w, 'color_scale', '2')
        session = w.workspace.active
        session.view.last_valid_region_color_limits = [(-20., 30.), (-40., 130.)]
        session.view.norm = NormSpec('symlog', 2.)
        w._apply_color_limits_to_mesh()
        w._save_current_plot_state()
        loaded, notice = w._plot_state_store.load(self.paths[0], w._loaded_source_fingerprint)
        self.assertIn('color_region_limits', active_plot_recipe(loaded), 'Regional effective ranges must survive sidecars')
        self.assertEqual(loaded['color_region_limits'], [[-20., 30.], [-40., 130.]])
        self.assertEqual(loaded['norm'], {'kind': 'symlog', 'linthresh': 2.})
        with patch.object(w, '_confirm_project_discard_async', lambda callback: callback()):
            self.host.window.close()
            wait_until(lambda: w._closing)
        with application() as (replacement_host, _, errors):
            replacement = replacement_host.controller
            replacement._load_dat_file_direct(self.paths[0])
            self.assertEqual(replacement.workspace.active.view.last_valid_region_color_limits,
                             [(-20., 30.), (-40., 130.)])
            self.assertEqual([mesh.get_clim() for mesh in replacement._heat_meshes],
                             [(-20., 30.), (-40., 130.)])
            self.assertEqual(errors, [])

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
                    w.plot_page.formal.setValue("height_in", 4.5)  # Separate regional header rows.
                    dat.set_choice(w, 'color_scale', '2')
                dat.set_choice(w, 'mapping', 'zero-centered')
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
        w.plot_page.formal.setValue("include_peak_labels", True)
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

    def test_quick_page_controls_couple_dimensions_and_exchange_display_only(self):
        w = self.window
        page_ui = w.plot_page
        before = w.current_cube.z.copy()
        page_ui.formal.setValue("width_in", 6.6)
        self.assertEqual(w.workspace.active.presentation.height_in, round(6.6*3.45/(91.5/25.4), 3))
        page_ui.formal.setValue("aspect_locked", False)
        page_ui.formal.setValue("height_in", 4.5)
        page_ui.formal.setValue('axis_exchange_xy', True)
        page_ui.formal.setValue('axis_flip_x', True)
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
        w.plot_page.formal.setValue("custom_ticks", True)
        self.assertIsNone(w.plot_page.rendered_page)
        w.dat_properties.setLabel('plot_title', 'A new title while ticks remain invalid', w.dat_properties.ownerToken)
        w._render_plot_page()
        self.assertTrue(w.plot_page.rendered_page is None,
                        'Unaccepted presentation editor input must not silently export the old format')
        w.plot_page.formal.setDraft('tick_values', '-10, 50, 100')
        w.plot_page.formal.commitField('tick_values')
        w.plot_page.formal.setValue("height_in", 4.5)  # The deliberately long edited title needs a row.
        self.render()
        self.assertTrue(w.plot_page.command_enabled("export_svg"))

    def test_mapping_no_positive_values_shows_error_and_cannot_export(self):
        w = self.window
        self.assertTrue('mappings' in dat.state(w), 'Shared display mapping is missing')
        self.render()
        w.workspace.active.current_cube = replace(w.current_cube, z=-np.ones_like(w.current_cube.z))
        dat.set_choice(w, 'mapping', 'log')
        w._render_plot_page()
        self.assertIsNone(w.plot_page.rendered_page)
        self.assertIn('positive', w.plot_page.status_text.lower())
        self.assertFalse(w.plot_page.command_enabled("export_svg"))

if __name__ == '__main__':
    unittest.main()

