"""Qt proof of the presentation boundary shared by Analysis and Plot."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import unittest

import numpy as np

from spectra_app.plot_presentation import ColorbarTicks
from tests import dat_properties_quick_helpers as dp
from tests.formal_quick_helpers import item
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import pump


class AnalysisPlotStyleTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.paths = []
        for name in ('a', 'b'):
            path = self.folder / f'{name}.dat'
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
            self.paths.append(path)
        self.window = self.controller
        self.window._load_dat_file_direct(self.paths[0])
        self.window.request_outer_view('analysis')
        self.window.dat_details_action.setChecked(True)
        self.window.request_task(2)
        pump()

    def tearDown(self):
        self.assertEqual(self.quick_errors, [])

    def formal_value(self, field, value):
        bridge = self.window.plot_page.formal
        source = item(self.window, 'formalProperties')
        self.assertTrue(source.isVisible())
        token = bridge.capturePropertiesInput(source)
        self.assertTrue(token, 'Visible Analysis Properties must own this edit')
        bridge.setValue(field, value, source, token)
        self.assertEqual(bridge.state['fields'][field], value)

    def assert_frame_ratio(self, expected):
        w = self.window
        w.canvas.draw()
        bounds = w._heat_ax.get_window_extent()
        self.assertGreater(bounds.width, 0)
        self.assertGreater(bounds.height, 0)
        self.assertAlmostEqual(bounds.width / bounds.height, expected, places=6)
        self.assertGreater(w._spec_ax.get_window_extent().height, 0)

    def assert_bar_ticks(self, bar, values, labels=None):
        np.testing.assert_allclose(bar.get_ticks(), values, atol=1e-12)
        if labels is not None:
            self.assertEqual([text.get_text() for text in bar.ax.get_xticklabels()], list(labels))

    def test_explicit_default_and_custom_frame_ratio_survive_live_canvas_resize(self):
        w = self.window
        for size in ((8., 6.), (4., 5.), (1.8, 2.)):
            w.figure.set_size_inches(*size)
            w._layout_live_plots()
            self.assert_frame_ratio(2.9/2.8)
        for dimensions in ((2.4, 1.2), (1.2, 2.4)):
            w.set_plot_presentation(replace(w.workspace.active.presentation,
                                           frame_width_in=dimensions[0], frame_height_in=dimensions[1]))
            # Source graph editing defers the hidden live canvas layout.
            w._layout_live_plots()
            self.assert_frame_ratio(dimensions[0] / dimensions[1])

    def test_valid_presentation_updates_artists_without_changing_science_or_view(self):
        w = self.window
        session = w.workspace.active
        session.peak_indices = np.array([1, 4])
        w._update_spectrum_only()
        cube, mesh, norm = w.current_cube, w._heat_mesh, w._heat_mesh.norm
        before = cube.z.copy(), w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w.current_gate
        p = session.presentation
        changed = replace(p, numeric_pt=9., text_pt=13., font_family='DejaVu Serif',
                          axis=replace(p.axis, border=False, minor_ticks=False,
                                       ticks_inside=False, x_major_step=.2))
        w.set_plot_presentation(changed)
        w.canvas.draw()
        self.assertIs(w.current_cube, cube)
        self.assertIs(w._heat_mesh, mesh)
        self.assertIs(w._heat_mesh.norm, norm)
        np.testing.assert_array_equal(cube.z, before[0])
        np.testing.assert_array_equal(session.peak_indices, [1, 4])
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w.current_gate), before[1:])
        self.assertFalse(any(spine.get_visible() for spine in w._heat_ax.spines.values()))
        self.assertFalse(any(line.get_visible() for line in w._heat_ax.get_xgridlines() + w._heat_ax.get_ygridlines()))
        numeric = [*w._heat_ax.get_xticklabels(), *w._heat_ax.get_yticklabels(),
                   w._heat_ax.xaxis.get_offset_text(), w._heat_ax.yaxis.get_offset_text(),
                   *w._heat_colorbar.ax.get_xticklabels()]
        text = [w._heat_ax.title, w._heat_ax.xaxis.label, w._heat_ax.yaxis.label,
                w._heat_colorbar.ax.xaxis.label]
        factor = 96*w._analysis_display_scale()*w.canvas.device_pixel_ratio/w.figure.dpi
        self.assertTrue(all(artist.get_fontsize() == 9*factor for artist in numeric))
        self.assertTrue(all(artist.get_fontsize() == 13*factor for artist in text))
        self.assertEqual(session.presentation.numeric_pt, 9)
        self.assertEqual(session.presentation.text_pt, 13)
        self.assertEqual(session.presentation.font_family, 'Arial')
        self.assertTrue(all(artist.get_fontfamily()[0] == 'Arial' for artist in numeric + text))
        self.assertEqual(w._heat_ax.xaxis.get_major_ticks()[0]._tickdir, 'out')
        self.assertEqual(len(w._heat_ax.xaxis.get_minorticklocs()), 0)
        # Spectrum clearing/gate navigation must retain the heatmap's interval.
        w._set_current_gate_from_value(1., update_plot=True)
        np.testing.assert_allclose(np.diff(w._heat_ax.get_xticks()), .2)
        self.assertEqual(w._spec_ax.xaxis.label.get_fontsize(), 12)
        w.set_plot_presentation(replace(changed, axis=replace(changed.axis, border=True,
                                                            border_width=2.1, major_ticks=False)))
        self.assertTrue(all(spine.get_visible() and spine.get_linewidth() == 2.1*factor
                            for spine in w._heat_ax.spines.values()))
        self.assertEqual(w._heat_ax.xaxis.get_major_ticks()[0].tick1line.get_markersize(), 0)
        literal = 'PL manual (a. u.)'
        w.dat_properties.setLabel('signal_title', literal, w.dat_properties.ownerToken)
        self.formal_value('bar_show_unit', False)
        self.assertEqual(w._heat_colorbar.ax.get_xlabel(), 'PL manual')
        self.assertEqual(dp.state(w)['signal_title'], literal)

    def test_dataset_switch_and_rebuild_restore_each_presentation(self):
        w = self.window
        a = w.workspace.active
        cube = a.current_cube
        a.peak_indices = np.array([2, 5])
        p = replace(a.presentation, frame_width_in=2.4, frame_height_in=1.2,
                    numeric_pt=8., font_family='DejaVu Serif')
        w.set_plot_presentation(p)
        limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim()
        w._load_dat_file_direct(self.paths[1])
        self.assert_frame_ratio(2.9/2.8)
        w.set_plot_presentation(replace(w.workspace.active.presentation, numeric_pt=11.))
        w.activate_dataset(a.dataset_id)
        self.assert_frame_ratio(2.)
        self.assertIs(w.current_cube, cube)
        self.assertEqual(w.workspace.active.presentation, p)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim()), limits)
        np.testing.assert_array_equal(a.peak_indices, [2, 5])
        factor = 96*w._analysis_display_scale()*w.canvas.device_pixel_ratio/w.figure.dpi
        self.assertTrue(all(text.get_fontsize() == 8*factor for text in w._heat_ax.get_xticklabels()))
        w._plot_cube()
        self.assert_frame_ratio(2.)
        self.assertTrue(all(text.get_fontfamily()[0] == 'Arial' for text in w._heat_ax.get_yticklabels()))

    def test_three_ticks_follow_mapping_and_custom_global_and_regional_settings(self):
        w = self.window
        for mapping in ('linear', 'log', 'symlog', 'zero-centered'):
            dp.set_choice(w, 'mapping', mapping)
            w.canvas.draw()
            norm = w._heat_mesh.norm
            expected = (norm.vmin, float(norm.inverse(.5)), norm.vmax)
            self.assert_bar_ticks(w._heat_colorbar, expected)
            p = w.workspace.active.presentation
            ticks = ColorbarTicks(expected, ('low', 'mid', 'high'))
            w.set_plot_presentation(replace(p, colorbar=replace(p.colorbar, ticks={'global': ticks})))
            self.assert_bar_ticks(w._heat_colorbar, expected, ticks.labels)
            w.set_plot_presentation(replace(p, colorbar=replace(p.colorbar, ticks={})))
        dp.set_choice(w, 'mapping', 'linear')
        for mode in ('2', '3'):
            dp.set_choice(w, 'color_scale', mode)
            custom = {}
            for index, bar in enumerate(w._heat_colorbars):
                norm = bar.mappable.norm
                values = (norm.vmin, float(norm.inverse(.5)), norm.vmax)
                self.assert_bar_ticks(bar, values)
                custom[f'region-{index+1}'] = ColorbarTicks(values, (f'{index}L', f'{index}M', f'{index}H'))
            p = w.workspace.active.presentation
            w.set_plot_presentation(replace(p, colorbar=replace(p.colorbar, ticks=custom)))
            for index, bar in enumerate(w._heat_colorbars):
                ticks = custom[f'region-{index+1}']
                self.assert_bar_ticks(bar, ticks.values, ticks.labels)
                factor = 96*w._analysis_display_scale()*w.canvas.device_pixel_ratio/w.figure.dpi
                self.assertTrue(all(text.get_fontsize() == p.numeric_pt*factor for text in bar.ax.get_xticklabels()))
            self.formal_value('bar_show_unit', False)
            self.assertNotIn('arb. units', w._heat_signal_label.get_text())
            w.set_plot_presentation(replace(w.workspace.active.presentation,
                                           colorbar=replace(w.workspace.active.presentation.colorbar, show_label=False)))
            self.assertEqual(w._heat_signal_label.get_text(), '')

    def test_out_of_range_custom_ticks_block_formal_export_but_live_bar_stays_usable(self):
        w = self.window
        norm = w._heat_mesh.norm
        custom = ColorbarTicks((norm.vmin, float(norm.inverse(.5)), norm.vmax), ('L', 'M', 'H'))
        p = w.workspace.active.presentation
        w.set_plot_presentation(replace(p, colorbar=replace(p.colorbar, ticks={'global': custom})))
        self.assert_bar_ticks(w._heat_colorbar, custom.values, custom.labels)
        dp.setup_number(w, 'vmax', float(norm.inverse(.75)))
        w.canvas.draw()
        current = w._heat_mesh.norm
        self.assert_bar_ticks(w._heat_colorbar, (current.vmin, float(current.inverse(.5)), current.vmax))
        self.assertEqual(w.workspace.active.presentation.colorbar.ticks['global'], custom)
        w._render_plot_page()
        self.assertIsNone(w.plot_page.rendered_page)
        self.assertIn('tick values', w.plot_page.status_text)
        self.assertTrue(item(w, 'formal_status').property('visible'))
        self.assertFalse(w.plot_page.command_enabled('export_svg'))
        gate = w.current_gate
        w._set_current_gate_from_value(0., update_plot=True)
        self.assertNotEqual(w.current_gate, gate)
        self.assertGreater(len(w._spec_ax.lines), 0)


if __name__ == '__main__':
    unittest.main()
