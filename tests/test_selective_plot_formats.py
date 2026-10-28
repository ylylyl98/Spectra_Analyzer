"""Selected presentation groups cannot change target scientific or text owners."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QSettings
from spectra_app import plot_templates as formats
from spectra_app.plot_presentation import (
    AxisPresentation, ColorbarPresentation, ColorbarTicks, HeatmapSnapshot,
    NormSpec, PlotAnnotation, PlotPresentation)
from spectra_app.svg_export import render_hybrid_svg
from tests.settings_helpers import isolate_settings
from tests.test_plot_templates import dataset


class SelectiveFormatTests(unittest.TestCase):
    def options(self, **selected):
        self.assertTrue(hasattr(formats, 'FormatApplicationOptions'),
                        'Selective application needs an immutable options record')
        flags = dict(layout=False, typography=False, axis=False, colormap=False,
                     colorbar=False)
        flags.update(selected)
        return formats.FormatApplicationOptions(**flags)

    def source(self):
        source = dataset('source')
        source.view.x_limits, source.view.y_limits = (4., 1.), (-20., 20.)
        source.view.color_limits = (-3., 15.)
        source.view.norm = NormSpec('symlog', .5)
        source.view.colormap = 'plasma'
        source.presentation = replace(
            source.presentation, width_in=5., height_in=4., heatmap_dpi=240,
            frame_width_in=3.3, frame_height_in=2.4, aspect_locked=False,
            font_family='DejaVu Sans', numeric_pt=9., text_pt=13.,
            axis=AxisPresentation(exchange_xy=True, flip_x=True, border=False),
            colorbar=ColorbarPresentation(alignment='left', gap_in=.25))
        return source

    def test_each_basic_group_changes_only_its_selected_fields(self):
        source = self.source()
        template = formats.capture_format(source, 'Source')
        expected = {
            'layout': {'width_in', 'height_in', 'heatmap_dpi', 'frame_width_in',
                       'frame_height_in', 'aspect_locked'},
            'typography': {'numeric_pt', 'text_pt'},
            'axis': {'axis.exchange_xy', 'axis.flip_x', 'axis.border'},
            'colormap': {'colormap'},
            'colorbar': {'colorbar.alignment', 'colorbar.gap_in'},
        }
        for group, fields in expected.items():
            with self.subTest(group=group):
                target = dataset()
                before = deepcopy(target.view)
                result = formats.apply_format(target, template, options=self.options(**{group: True}))
                self.assertEqual(set(result.changed_fields), fields)
                self.assertEqual(target.view.x_limits, before.x_limits)
                self.assertEqual(target.view.y_limits, before.y_limits)
                self.assertEqual(target.view.color_limits, before.color_limits)
                self.assertEqual(target.view.annotations, before.annotations)

    def test_default_options_copy_basic_groups_and_remain_immutable(self):
        self.options()
        options = formats.FormatApplicationOptions()
        with self.assertRaises(FrozenInstanceError):
            options.mapping = True
        target = dataset()
        formats.apply_format(target, formats.capture_format(self.source(), 'Source'), options=options)
        self.assertEqual(target.presentation.width_in, 5.)
        self.assertEqual(target.view.colormap, 'plasma')
        self.assertEqual(target.view.x_limits, (1., 4.))
        self.assertEqual(target.view.y_limits, (0., 2.))
        self.assertEqual(target.view.color_limits, (1., 12.))
        self.assertEqual(target.view.norm.kind, 'linear')

    def test_data_axis_ranges_keep_original_axes_despite_exchange_and_flips(self):
        source, target = self.source(), dataset()
        template = formats.capture_format(source, 'Source')
        options = self.options(x_limits=True, y_limits=True)
        result = formats.apply_format(target, template, options=options)
        self.assertEqual(target.view.x_limits, (4., 1.))
        self.assertEqual(target.view.y_limits, (-20., 20.))
        self.assertEqual(target.presentation.axis, AxisPresentation())
        self.assertEqual({k: target.view.bounds[k] for k in ('xmin', 'xmax', 'ymin', 'ymax')},
                         {'xmin': 1., 'xmax': 4., 'ymin': -20., 'ymax': 20.})
        self.assertTrue(all(target.view.locks[k] for k in ('xmin', 'xmax', 'ymin', 'ymax')))
        self.assertFalse(target.view.locks['vmin'])
        self.assertIn('x_limits', result.changed_fields)

    def test_disabled_lock_preserves_target_endpoint_flags_and_unselected_ranges(self):
        target = dataset()
        target.view.locks.update(xmin=True, xmax=False, ymin=False, ymax=True)
        before = deepcopy(target.view)
        formats.apply_format(target, formats.capture_format(self.source(), 'Source'),
                             options=self.options(x_limits=True, lock_copied_ranges=False))
        self.assertEqual(target.view.locks, before.locks)
        self.assertEqual(target.view.y_limits, before.y_limits)
        self.assertEqual(target.view.bounds['ymin'], before.bounds['ymin'])

    def test_new_v_limits_lock_endpoints_but_legacy_flags_preserve_flags(self):
        template = formats.capture_format(self.source(), 'Source')
        new, legacy = dataset('new'), dataset('legacy')
        formats.apply_format(new, template, options=self.options(color_limits=True))
        formats.apply_format(legacy, template, copy_color_limits=True)
        self.assertEqual(new.view.color_limits, (-3., 15.))
        self.assertEqual(new.view.last_valid_color_limits, (-3., 15.))
        self.assertEqual((new.view.bounds['vmin'], new.view.bounds['vmax']), (-3., 15.))
        self.assertTrue(new.view.locks['vmin'] and new.view.locks['vmax'])
        self.assertFalse(legacy.view.locks['vmin'] or legacy.view.locks['vmax'])

    def test_regional_limits_require_same_mode_and_keep_target_custom_ticks(self):
        source, target = self.source(), dataset()
        source.view.color_scale_mode = '2'
        source.view.last_valid_region_color_limits = [(-2., 8.), (-3., 15.)]
        template = formats.capture_format(source, 'Regional')
        original = target.view, target.presentation
        with self.assertRaisesRegex(ValueError, 'mode'):
            formats.apply_format(target, template, options=self.options(color_limits=True))
        self.assertIs(target.view, original[0])
        self.assertIs(target.presentation, original[1])
        target.view.color_scale_mode = '2'
        target.view.color_region_boundaries = [3.5]
        target.presentation = replace(target.presentation, colorbar=ColorbarPresentation(ticks={
            'region-1': ColorbarTicks((1., 3., 5.)),
            'region-2': ColorbarTicks((6., 8., 12.)),
            'global': ColorbarTicks((1., 5., 12.))}))
        formats.apply_format(target, template, options=self.options(color_limits=True))
        self.assertEqual(target.view.last_valid_region_color_limits, [(-2., 8.), (-3., 15.)])
        self.assertEqual(target.view.color_region_boundaries, [3.5])
        self.assertEqual(target.presentation.colorbar.ticks['region-1'].values, (1., 3., 5.))
        self.assertIn('global', target.presentation.colorbar.ticks)
        self.assertTrue(target.view.locks['vmin'] and target.view.locks['vmax'])

    def test_incompatible_mapping_or_ticks_leave_all_target_records_unchanged(self):
        source, target = self.source(), dataset()
        source.view.norm, source.view.color_limits = NormSpec('log'), (2., 9.)
        target.view.color_limits = (-1., 12.)
        template = formats.capture_format(source, 'Narrow log')
        for option in ('mapping', 'color_limits'):
            target.presentation = replace(target.presentation, colorbar=ColorbarPresentation(
                ticks={'global': ColorbarTicks((1., 5., 12.))}))
            original = target.view, target.presentation
            with self.subTest(option=option), self.assertRaises(ValueError):
                formats.apply_format(target, template, options=self.options(**{option: True}))
            self.assertIs(target.view, original[0])
            self.assertIs(target.presentation, original[1])

    def test_data_text_anchor_becomes_frame_fraction_and_explicit_pair_preserves_target(self):
        self.options()
        source, target = self.source(), dataset()
        source.presentation = replace(source.presentation, annotations=(
            PlotAnnotation('source-text', text='Source wording', position=(3.25, -10.), coordinate_space='data'),
            PlotAnnotation('source-arrow', 'arrow', 'Source arrow', (.1, .8), target=(2., 0.)),))
        target.presentation = replace(target.presentation, annotations=(
            PlotAnnotation('different-uuid', text='Target wording', position=(2., 1.), coordinate_space='data',
                           font_role='numeric', visible=False, color='red'),
            PlotAnnotation('target-arrow', 'arrow', 'Target arrow', (.8, .2), target=(3.5, 1.5)),
            PlotAnnotation('untouched', text='Third', position=(.6, .6)),))
        template = formats.capture_format(source, 'Text')
        # exchange: display X is Gate, reversed by flip_x; display Y is descending Energy.
        self.assertEqual(template.annotation_layout[0].position, (.75, .25))
        self.assertEqual(template.annotation_layout[0].coordinate_space, 'axes-fraction')
        target.view.x_limits, target.view.y_limits = (100., 200.), (500., 1000.)
        original_annotations = target.presentation.annotations
        pairs = {'source-text': 'different-uuid', 'source-arrow': 'target-arrow'}
        options = self.options(annotation_positions=True)
        preview = formats.preview_format(target, template, options=options, annotation_pairs=pairs)
        self.assertIs(target.presentation.annotations, original_annotations)
        result = formats.apply_format(target, template, options=options, annotation_pairs=pairs)
        self.assertEqual(result, preview)
        self.assertEqual(target.presentation.annotations[0], replace(original_annotations[0],
                         position=(.75, .25), coordinate_space='axes-fraction'))
        self.assertEqual(target.presentation.annotations[1], replace(original_annotations[1], position=(.1, .8)))
        self.assertIs(target.presentation.annotations[2], original_annotations[2])
        self.assertEqual(target.presentation.annotations[1].target, (3.5, 1.5))
        self.assertEqual(target.view.annotations['plot_title'], 'target')

    def test_text_position_requires_valid_explicit_unambiguous_pairs(self):
        source, target = self.source(), dataset()
        source.presentation = replace(source.presentation, annotations=(
            PlotAnnotation('s1', text='same'), PlotAnnotation('s2', text='same')))
        target.presentation = replace(target.presentation, annotations=(
            PlotAnnotation('t1', text='same'), PlotAnnotation('t2', 'arrow', text='same', target=(1., 1.))))
        template = formats.capture_format(source, 'Text')
        for pairs in (None, {}, {'missing': 't1'}, {'s1': 'missing'},
                      {'s1': 't1', 's2': 't1'}, {'s1': 't2'}):
            original = target.view, target.presentation
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                formats.apply_format(target, template, options=self.options(annotation_positions=True),
                                     annotation_pairs=pairs)
            self.assertIs(target.view, original[0])
            self.assertIs(target.presentation, original[1])

    def test_capture_uses_actual_log_source_coordinates_and_rejects_invalid_capture(self):
        self.options()
        source = dataset('source')
        source.presentation = replace(source.presentation, annotations=(
            PlotAnnotation('s1', text='Log', position=(10., 1.), coordinate_space='data'),))
        snapshot = HeatmapSnapshot([1., 100.], [0., 2.], [[1., 2.], [3., 4.]],
                                  x_limits=(1., 100.), y_limits=(0., 2.), x_scale='log')
        page = SimpleNamespace(axes_bounds=(0., 0., 1., 1.),
                               display_x_limits=(1., 100.), display_y_limits=(0., 2.))
        template = formats.capture_format(source, 'Log', source_snapshot=snapshot, source_page=page)
        self.assertEqual(template.annotation_layout[0].position, (.5, .5))
        with self.assertRaises(ValueError):
            formats.capture_format(source, 'Incomplete', source_page=page)

    def test_title_position_is_separate_and_old_sidecars_keep_default(self):
        self.assertIn('title_position', PlotPresentation().to_dict())
        source, target = self.source(), dataset()
        source.presentation = replace(source.presentation, title_position=(.2, .7))
        target.presentation = replace(target.presentation, title_position=(.8, .4))
        template = formats.capture_format(source, 'Title')
        formats.apply_format(target, template)
        self.assertEqual(target.presentation.title_position, (.8, .4))
        formats.apply_format(target, template, options=self.options(title_position=True))
        self.assertEqual(target.presentation.title_position, (.2, .7))
        self.assertEqual(target.view.annotations['plot_title'], 'target')
        old = target.presentation.to_dict()
        old.pop('title_position')
        self.assertEqual(PlotPresentation.from_dict(old).title_position, (.02, .98))
        for position in ((-1., .5), (.5, 1.01), (float('nan'), .5), (True, .5), (.5,)):
            with self.subTest(position=position), self.assertRaises(ValueError):
                replace(target.presentation, title_position=position)

    def test_capture_rejects_other_owner_and_stale_rendered_pages(self):
        source = dataset('source')
        source.presentation = replace(source.presentation, annotations=(
            PlotAnnotation('s1', text='Log anchor', position=(10., 1.), coordinate_space='data'),))
        snapshot = HeatmapSnapshot([1., 100.], [0., 2.], [[1., 2.], [3., 4.]],
                                  x_limits=(1., 100.), y_limits=(0., 2.), x_scale='log',
                                  dataset_id='source', science_revision=3)
        page = render_hybrid_svg(snapshot, source.presentation)
        for token in (('other-dataset', 0, 3, 0, 0), ('source', 0, 2, 0, 0),
                      ('source', 1, 3, 0, 0), ('source', 0, 3, 1, 0)):
            with self.subTest(token=token), self.assertRaises(ValueError):
                formats.capture_format(source, 'Wrong page', source_snapshot=snapshot,
                                       source_page=replace(page, owner_token=token))
        empty_snapshot = replace(snapshot, dataset_id='')
        valid = formats.capture_format(source, 'Detached compatible', source_snapshot=empty_snapshot,
                                       source_page=replace(page, owner_token=empty_snapshot.owner_token))
        self.assertAlmostEqual(valid.annotation_layout[0].position[0], .5)
        self.assertAlmostEqual(valid.annotation_layout[0].position[1], .5)

    def test_old_template_optional_fields_remain_unavailable_and_store_reads_do_not_write(self):
        self.options()
        old = formats.capture_format(self.source(), 'Old').to_dict()
        for name in ('x_limits', 'y_limits', 'title_position', 'annotation_layout'):
            old.pop(name)
        raw = json.dumps({'version': 1, 'templates': [old]})
        settings = QSettings(str(isolate_settings(self)), QSettings.IniFormat)
        settings.setValue(formats.FORMAT_TEMPLATES_KEY, raw)
        template = formats.FormatTemplateStore(settings).load('Old')
        self.assertEqual(settings.value(formats.FORMAT_TEMPLATES_KEY), raw)
        self.assertIsNone(template.x_limits)
        self.assertIsNone(template.y_limits)
        self.assertIsNone(template.title_position)
        self.assertEqual(template.annotation_layout, ())
        target = dataset()
        for group in ('x_limits', 'y_limits', 'title_position', 'annotation_positions'):
            with self.subTest(group=group), self.assertRaises(ValueError):
                formats.apply_format(target, template, options=self.options(**{group: True}))

    def test_malformed_new_payload_and_options_are_rejected_without_mutation(self):
        self.options()
        good = formats.capture_format(self.source(), 'Source').to_dict()
        for changes in ({'x_limits': [1., 1.]}, {'y_limits': [0., float('inf')]},
                        {'title_position': [0., 2.]}, {'annotation_layout': [{'id': 's1'}]},
                        {'unknown': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                formats.FormatTemplate.from_dict(dict(good, **changes))
        for bad in (1, 'yes', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                formats.FormatApplicationOptions(x_limits=bad)
        target = dataset()
        original = target.view, target.presentation
        with self.assertRaises(ValueError):
            formats.apply_format(target, formats.capture_format(self.source(), 'Source'), options={})
        self.assertIs(target.view, original[0])
        self.assertIs(target.presentation, original[1])

    def test_selected_ranges_mapping_and_positions_do_not_replace_scientific_owners(self):
        source, target = self.source(), dataset()
        source.presentation = replace(source.presentation, annotations=(PlotAnnotation('s1', position=(.2, .3)),))
        target.presentation = replace(target.presentation, annotations=(PlotAnnotation('t1', text='Target'),))
        science = (target.base_cube, target.current_cube, target.applied, target.pending,
                   target.batch_fit_result_map, target.fit_export_rows, target.peak_indices)
        before = target.current_cube.z.copy()
        formats.apply_format(target, formats.capture_format(source, 'All'), options=self.options(
            x_limits=True, y_limits=True, color_limits=True, mapping=True, annotation_positions=True),
            annotation_pairs={'s1': 't1'})
        for old, current in zip(science, (target.base_cube, target.current_cube, target.applied,
                                         target.pending, target.batch_fit_result_map,
                                         target.fit_export_rows, target.peak_indices)):
            self.assertIs(current, old)
        self.assertTrue((target.current_cube.z == before).all())


if __name__ == '__main__':
    unittest.main()
