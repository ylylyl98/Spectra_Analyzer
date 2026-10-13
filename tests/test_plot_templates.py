"""Format persistence and application preserve each dataset's scientific owner."""
from copy import deepcopy
from dataclasses import replace
import importlib
import json
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PySide6.QtCore import QSettings
from spectra_app.dat_io import DataCube
from spectra_app.dataset_state import DatasetSession, GraphState
from spectra_app.fitting import FitResult
from spectra_app.plot_presentation import (AxisPresentation, ColorbarPresentation,
                                           ColorbarTicks, NormSpec, PlotAnnotation,
                                           PlotPresentation)
from tests.settings_helpers import isolate_settings

try:
    templates = importlib.import_module('spectra_app.plot_templates')
except ModuleNotFoundError:
    templates = None


def dataset(identifier='target'):
    energy, gate = np.array([1., 2., 3., 4.]), np.array([0., 1., 2.])
    cube = DataCube(energy=energy, gate=gate, z=np.arange(12.).reshape(3, 4) + 1,
                    gate_label='Gate (V)', title='', source_path='', x_original=energy,
                    x_original_type='ev', x_original_label='Energy (eV)',
                    x_detection_source='test', x_detection_note='', x_is_ambiguous=False,
                    x_override=None, photon_energy=energy, photon_energy_note='',
                    energy_label='Energy (eV)')
    session = DatasetSession(dataset_id=identifier, base_cube=cube, current_cube=cube,
                             graphs={'default': GraphState()})
    session.view.x_limits = (1., 4.)
    session.view.y_limits = (0., 2.)
    session.view.color_limits = (1., 12.)
    session.view.bounds.update(vmin=1., vmax=12.)
    session.view.annotations.update(plot_title=identifier, y_axis_name='Gate', y_axis_unit='V')
    return session


class PlotTemplateTests(unittest.TestCase):
    def test_common_palettes_roundtrip_canonically_in_named_formats(self):
        store = templates.FormatTemplateStore(self.settings)
        for label, canonical in (('jet', 'jet'), ('RdBu_r_0.6', 'RdBu_r_p0p6')):
            source = self.source(); source.view.colormap = label
            store.save(templates.capture_format(source, label))
            restored = templates.FormatTemplateStore(QSettings(self.settings.fileName(), QSettings.IniFormat)).load(label)
            self.assertEqual(restored.colormap, canonical)
            target = dataset()
            target.presentation = replace(target.presentation, annotations=(PlotAnnotation('optional-note', text='9T σ⁺'),))
            ranges = target.view.color_limits
            templates.apply_format(target, restored)
            self.assertEqual(target.view.colormap, canonical)
            self.assertEqual(target.view.color_limits, ranges)
            self.assertEqual(target.presentation.annotations[0].text, '9T σ⁺')

    def setUp(self):
        self.assertIsNotNone(templates, 'Named format core is not implemented')
        self.settings = QSettings(str(isolate_settings(self)), QSettings.IniFormat)

    def source(self):
        source = dataset('source')
        source.view.colormap = 'RdBu_r_p0p45'
        source.view.norm = NormSpec(kind='symlog', linthresh=.5)
        source.view.color_limits = (-3., 15.)
        source.presentation = PlotPresentation(
            width_in=4.2, height_in=4.5, numeric_pt=11, text_pt=14, heatmap_dpi=240,
            axis=AxisPresentation(exchange_xy=True, border_width=2),
            colorbar=ColorbarPresentation(gap_in=.2, ticks={'global': ColorbarTicks((-3., 0., 15.))}),
            annotations=(PlotAnnotation('source-note', text='Source only'),), include_peak_labels=True)
        return source

    def test_named_persistence_is_versioned_json_and_isolated_from_sidecar_data(self):
        store = templates.FormatTemplateStore(self.settings)
        store.save(templates.capture_format(self.source(), 'Paper'))
        store.save(templates.capture_format(dataset(), 'Compact'))
        reloaded = templates.FormatTemplateStore(QSettings(self.settings.fileName(), QSettings.IniFormat))
        self.assertEqual(reloaded.list_names(), ('Compact', 'Paper'))
        saved = reloaded.load('Paper')
        self.assertEqual(saved.style.width_in, 4.2)
        self.assertEqual(saved.colormap, 'RdBu_r_p0p45')
        raw = json.loads(self.settings.value(templates.FORMAT_TEMPLATES_KEY))
        self.assertEqual(raw['version'], 1)
        self.assertEqual(len(raw['templates']), 2)
        text = json.dumps(raw)
        for forbidden in ('source_path', 'dataset_id', 'z', 'annotations', 'ticks'):
            self.assertNotIn('"' + forbidden + '"', text)
        self.assertTrue(reloaded.delete('Compact'))
        self.assertFalse(reloaded.delete('missing'))
        self.assertEqual(reloaded.list_names(), ('Paper',))
        with self.assertRaises(KeyError):
            reloaded.load('missing')

    def test_old_v1_formats_migrate_only_new_optional_geometry_fields(self):
        old = templates.capture_format(self.source(), 'Old Paper').to_dict()
        for name in ('frame_width_in', 'frame_height_in'):
            old['style'].pop(name, None)
        for name in ('alignment', 'signal_position', 'frame_gap_in', 'show_extensions', 'show_unit'):
            old['style']['colorbar'].pop(name, None)
        self.settings.setValue(templates.FORMAT_TEMPLATES_KEY, json.dumps({'version': 1, 'templates': [old]}))
        store = templates.FormatTemplateStore(self.settings)
        restored = store.load('Old Paper')
        self.assertIn('frame_width_in', restored.style.to_dict(), 'Old named formats need the optional geometry defaults')
        self.assertIsNone(restored.style.frame_width_in)
        self.assertEqual(restored.style.colorbar.alignment, 'right')
        self.assertFalse(restored.style.colorbar.show_extensions)
        self.assertTrue(restored.style.colorbar.show_unit)
        # Migration does not relax the established required fields or unknown-field checks.
        for mutate in (lambda p: p['style'].pop('numeric_pt'),
                       lambda p: p['style'].update(frame_width_in=2.),
                       lambda p: p['style']['colorbar'].update(unknown=True)):
            malformed = deepcopy(old)
            mutate(malformed)
            with self.subTest(payload=malformed), self.assertRaises(ValueError):
                templates.FormatTemplate.from_dict(malformed)

    def test_saved_frame_and_header_format_apply_without_copying_dataset_ticks(self):
        self.assertIn('frame_width_in', PlotPresentation().to_dict(), 'Explicit frame geometry is missing')
        source, target = self.source(), dataset()
        source.presentation = replace(source.presentation, frame_width_in=2.5, frame_height_in=1.6,
            colorbar=replace(source.presentation.colorbar, width_in=.7, height_in=.08,
                             alignment='left', signal_position='above', frame_gap_in=.04, show_extensions=True))
        target.presentation = replace(target.presentation,
            colorbar=ColorbarPresentation(ticks={'global': ColorbarTicks((1., 5., 12.))}))
        store = templates.FormatTemplateStore(self.settings)
        store.save(templates.capture_format(source, 'Rectangle'))
        templates.apply_format(target, store.load('Rectangle'))
        self.assertEqual((target.presentation.frame_width_in, target.presentation.frame_height_in), (2.5, 1.6))
        self.assertEqual(target.presentation.colorbar.alignment, 'left')
        self.assertEqual(target.presentation.colorbar.height_in, .08)
        self.assertEqual(target.presentation.colorbar.signal_position, 'above')
        self.assertEqual(target.presentation.colorbar.frame_gap_in, .04)
        self.assertTrue(target.presentation.colorbar.show_extensions)
        self.assertEqual(target.presentation.colorbar.ticks['global'].values, (1., 5., 12.))

    def test_malformed_store_never_replaces_previous_preferences(self):
        template = templates.capture_format(self.source(), 'Paper')
        good = {'version': 1, 'templates': [template.to_dict()]}
        invalid = []
        for mutate in (
            lambda p: p.update(version=True),
            lambda p: p.update(version=2),
            lambda p: p.update(unknown='ignored'),
            lambda p: p['templates'].append(deepcopy(p['templates'][0])),
            lambda p: p['templates'][0].update(colormap='unknown-palette'),
            lambda p: p['templates'][0]['style'].update(width_in=True),
            lambda p: p['templates'][0]['style'].update(annotations=[]),
            lambda p: p['templates'][0]['norm'].update(kind='unknown-mapping'),
        ):
            payload = deepcopy(good)
            mutate(payload)
            invalid.append(json.dumps(payload))
        invalid.extend(['not JSON', '[]', '{"version":1,"templates":null}'])
        for raw in invalid:
            with self.subTest(raw=raw):
                self.settings.setValue(templates.FORMAT_TEMPLATES_KEY, raw)
                store = templates.FormatTemplateStore(self.settings)
                with self.assertRaises(ValueError):
                    store.list_names()
                with self.assertRaises(ValueError):
                    store.save(template)
                self.assertEqual(self.settings.value(templates.FORMAT_TEMPLATES_KEY), raw)

    def test_default_application_changes_style_but_preserves_target_semantics_and_science(self):
        source, target = self.source(), dataset()
        target.presentation = PlotPresentation(
            include_peak_labels=True,
            colorbar=ColorbarPresentation(ticks={'global': ColorbarTicks((1., 5., 12.), ('lo', 'mid', 'hi'))}),
            annotations=(PlotAnnotation('target-note', text='9T σ⁺'),))
        target.fit_result = FitResult('lorentzian', np.array([1., 2.]), np.array([4., 5.]),
                                      np.array([[4., 5.]]), np.array([1.5]), np.array([5.]),
                                      np.array([.1]), 0., .01, .98)
        target.batch_fit_result_map[0] = target.fit_result
        target.fit_export_rows.append({'center': '1.5'})
        fit_map, export_rows = target.batch_fit_result_map, target.fit_export_rows
        before_view, before_annotations = deepcopy(target.view), target.presentation.annotations
        science = (target.current_cube, target.base_cube, target.fit_result, target.applied, target.pending)
        values = target.current_cube.z.copy()
        template = templates.capture_format(source, 'Paper')
        preview = templates.preview_format(target, template)
        self.assertIn('width_in', preview.changed_fields)
        self.assertIn('axis.exchange_xy', preview.changed_fields)
        self.assertIn('colormap', preview.changed_fields)
        self.assertEqual(target.presentation.width_in, 91.5/25.4)
        applied = templates.apply_format(target, template)
        self.assertEqual(applied.changed_fields, preview.changed_fields)
        self.assertEqual(target.presentation.width_in, 4.2)
        self.assertEqual(target.presentation.colorbar.ticks['global'].values, (1., 5., 12.))
        self.assertEqual(target.presentation.annotations, before_annotations)
        self.assertEqual(target.presentation.include_peak_labels, True)
        for field in ('x_limits', 'y_limits', 'color_limits', 'norm', 'color_scale_mode',
                      'color_region_boundaries', 'annotations', 'title_settings'):
            self.assertEqual(getattr(target.view, field), getattr(before_view, field))
        self.assertEqual((target.current_cube, target.base_cube, target.fit_result, target.applied, target.pending), science)
        np.testing.assert_array_equal(target.current_cube.z, values)
        self.assertIs(target.batch_fit_result_map, fit_map)
        self.assertIs(target.fit_export_rows, export_rows)
        self.assertEqual(target.fit_export_rows, [{'center': '1.5'}])
        np.testing.assert_array_equal(target.fit_result.centers, [1.5])
        self.assertEqual(templates.apply_format(target, template).changed_fields, ())

    def test_application_copies_nested_owners_instead_of_aliasing_them(self):
        template = templates.capture_format(self.source(), 'Paper')
        first, second = dataset('first'), dataset('second')
        for target in (first, second):
            target.presentation = replace(target.presentation, colorbar=ColorbarPresentation(
                ticks={'global': ColorbarTicks((1., 5., 12.))}))
            templates.apply_format(target, template)
        first.view.annotations['plot_title'] = 'changed'
        first.view.title_settings['manual_override'] = 'changed'
        first.presentation.colorbar.ticks.clear()
        self.assertEqual(second.view.annotations['plot_title'], 'second')
        self.assertEqual(second.view.title_settings['manual_override'], '')
        self.assertIn('global', second.presentation.colorbar.ticks)
        loaded = templates.FormatTemplate.from_dict(template.to_dict())
        encoded = loaded.to_dict()
        encoded['style']['axis']['border_width'] = 99
        self.assertEqual(template.style.axis.border_width, 2.)

    def test_explicit_global_mapping_and_limits_copy_updates_only_color_state(self):
        target = dataset()
        template = templates.capture_format(self.source(), 'Paper')
        templates.apply_format(target, template, copy_mapping=True)
        self.assertEqual(target.view.norm, NormSpec('symlog', .5))
        self.assertEqual(target.view.color_limits, (1., 12.))
        templates.apply_format(target, template, copy_color_limits=True)
        self.assertEqual(target.view.color_limits, (-3., 15.))
        self.assertEqual(target.view.last_valid_color_limits, (-3., 15.))
        self.assertEqual((target.view.bounds['vmin'], target.view.bounds['vmax']), (-3., 15.))
        self.assertEqual(target.view.norm, NormSpec('symlog', .5))

    def test_requested_invalid_mapping_or_tick_range_is_atomic(self):
        source = self.source()
        source.view.norm, source.view.color_limits = NormSpec('log'), (1., 12.)
        target = dataset()
        target.view.color_limits = (-1., 12.)
        template = templates.capture_format(source, 'Log')
        before_view, before_presentation = target.view, target.presentation
        with self.assertRaisesRegex(ValueError, 'positive'):
            templates.apply_format(target, template, copy_mapping=True)
        self.assertIs(target.view, before_view)
        self.assertIs(target.presentation, before_presentation)
        source.view.color_limits = (2., 9.)
        template = templates.capture_format(source, 'Narrow')
        target.view.color_limits = (1., 12.)
        target.presentation = replace(target.presentation, colorbar=ColorbarPresentation(
            ticks={'global': ColorbarTicks((1., 5., 12.))}))
        before_view, before_presentation = target.view, target.presentation
        with self.assertRaisesRegex(ValueError, 'global.*tick|tick.*global'):
            templates.apply_format(target, template, copy_color_limits=True)
        self.assertIs(target.view, before_view)
        self.assertIs(target.presentation, before_presentation)

    def test_regional_copy_preserves_boundaries_and_requires_matching_bar_count(self):
        source, target = self.source(), dataset()
        for session in (source, target):
            session.view.color_scale_mode = '2'
            session.view.color_region_boundaries = [2.5]
        source.view.last_valid_region_color_limits = [(-2., 8.), (-3., 9.)]
        target.view.last_valid_region_color_limits = [(1., 5.), (6., 12.)]
        target.view.color_region_boundaries = [3.5]
        target.presentation = replace(target.presentation, colorbar=ColorbarPresentation(ticks={
            'region-1': ColorbarTicks((1., 3., 5.)), 'region-2': ColorbarTicks((6., 8., 12.))}))
        template = templates.capture_format(source, 'Regions')
        templates.apply_format(target, template, copy_mapping=True)
        self.assertEqual(target.view.last_valid_region_color_limits, [(1., 5.), (6., 12.)])
        target.presentation = replace(target.presentation, colorbar=ColorbarPresentation())
        templates.apply_format(target, template, copy_color_limits=True)
        self.assertEqual(target.view.last_valid_region_color_limits, [(-2., 8.), (-3., 9.)])
        self.assertEqual(target.view.color_region_boundaries, [3.5])
        self.assertEqual(target.view.color_scale_mode, '2')
        global_target = dataset('global')
        before = global_target.presentation
        with self.assertRaisesRegex(ValueError, 'mode|regional'):
            templates.apply_format(global_target, template, copy_color_limits=True)
        self.assertIs(global_target.presentation, before)

    def test_regional_mapping_validates_every_bar_and_retains_inactive_custom_ticks(self):
        source, target = self.source(), dataset()
        source.view.norm, source.view.color_limits = NormSpec('log'), (1., 20.)
        template = templates.capture_format(source, 'Log')
        target.view.color_scale_mode = '3'
        target.view.color_region_boundaries = [1.5, 2.5]
        target.view.last_valid_region_color_limits = [(1., 3.), (-1., 5.), (5., 12.)]
        before = target.presentation
        with self.assertRaisesRegex(ValueError, 'region-2.*positive|positive.*region-2'):
            templates.apply_format(target, template, copy_mapping=True)
        self.assertIs(target.presentation, before)
        target.view.last_valid_region_color_limits[1] = (3., 5.)
        target.presentation = replace(target.presentation, colorbar=ColorbarPresentation(ticks={
            'global': ColorbarTicks((-2., 0., 12.)), 'region-2': ColorbarTicks((3., 4., 5.))}))
        templates.apply_format(target, template, copy_mapping=True)
        self.assertIn('global', target.presentation.colorbar.ticks)
        self.assertEqual(target.view.norm.kind, 'log')

    def test_empty_store_reads_do_not_write_defaults_and_duplicate_json_keys_are_rejected(self):
        store = templates.FormatTemplateStore(self.settings)
        self.assertEqual(store.list_names(), ())
        self.assertFalse(self.settings.contains(templates.FORMAT_TEMPLATES_KEY))
        raw = '{"version":2,"version":1,"templates":[]}'
        self.settings.setValue(templates.FORMAT_TEMPLATES_KEY, raw)
        with self.assertRaises(ValueError):
            store.list_names()
        self.assertEqual(self.settings.value(templates.FORMAT_TEMPLATES_KEY), raw)

    def test_missing_regional_ranges_and_wrong_typed_copy_options_leave_target_unchanged(self):
        target = dataset()
        target.view.color_scale_mode = '2'
        target.view.last_valid_region_color_limits = []
        template = templates.capture_format(self.source(), 'Paper')
        before_view, before_presentation = target.view, target.presentation
        for options in ({'copy_mapping': True}, {'copy_mapping': 1}, {'copy_color_limits': 'yes'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                templates.apply_format(target, template, **options)
            self.assertIs(target.view, before_view)
            self.assertIs(target.presentation, before_presentation)


if __name__ == '__main__':
    unittest.main()
