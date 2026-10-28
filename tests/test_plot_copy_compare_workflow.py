"""Selective transfer and comparison proof at the production Quick boundary."""
from tests import dat_properties_quick_helpers as dat
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from shiboken6 import isValid

from spectra_app.common_prompts import PromptAction
from spectra_app import main_window
from spectra_app.plot_presentation import PlotAnnotation
from spectra_app.plot_templates import FORMAT_TEMPLATES_KEY, FormatApplicationOptions, FormatTemplate
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import application, wait_until
from tests.formal_quick_helpers import click_item, item as quick_item, input_receiver, pump


def ranges_only(**kwargs):
    return FormatApplicationOptions(layout=False, typography=False, axis=False,
                                    colormap=False, colorbar=False, **kwargs)


class PlotCopyCompareWorkflowTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        self.errors = self.quick_errors
        self.paths = []
        for name in ('condition-a', 'condition-b', 'condition-c'):
            path = self.folder / (name + '.dat')
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
            self.paths.append(path)
            self.w._load_dat_file_direct(path)
        self.a, self.b, self.c = self.w.workspace.datasets.values()
        self.host.window.resize(1500, 960)
        self.w.open_graph_document()
        self.w.dat_details_action.setChecked(True)
        self.assertTrue(self.w.request_task(2))
        # Let startup layout callbacks finish before assertions about frozen bytes.
        QTest.qWait(250)

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def formal_call(self, page, method, *args):
        # Ordinary Analysis edits use their actual visible typed Properties
        # consumer; an input lease for a Workspace page cannot authorize them.
        self.w.dat_details_action.setChecked(True)
        self.assertTrue(self.w.request_task(2))
        pump()
        source = quick_item(self.w, 'formalProperties')
        self.assertTrue(source.isVisible())
        self.assertIs(self.w.plot_page, page)
        token = page.formal.capturePropertiesInput(source)
        self.assertTrue(token, dict(view=self.w.outerView, context=self.w.contextKind,
                                  active=self.w.graph_documents.registry.active_key,
                                  source_owner=self.w._document_key(),
                                  properties_current=self.w.propertiesController is page,
                                  source_current=source.property('bridge') is page.formal,
                                  system=page.formal.system_allowed(),
                                  source_enabled=source.isEnabled()))
        return getattr(page.formal, method)(*args, source, token)

    def copy_source(self):
        self.assertTrue(hasattr(self.w, 'copy_plot_settings'), 'Direct settings capture is missing')
        self.w.activate_dataset(self.a.dataset_id)
        # The source editor follows the active scientific DAT in Analysis;
        # opening C in Workspace does not publish A's document automatically.
        self.assertTrue(self.w.request_outer_view('analysis'))
        self.assertEqual(self.w.workspace.active_dataset_id, self.a.dataset_id)
        self.assertEqual(self.w._document_key()[0], self.a.dataset_id)
        self.assertEqual(self.w.outerView, 'analysis')
        self.w.dat_properties.setLabel('plot_title', 'Condition A', self.w.dat_properties.ownerToken)
        self.w.set_plot_presentation(replace(self.a.presentation, width_in=4.2, aspect_locked=False,
                                            title_position=(.23, .77)))
        dat.set_choice(self.w, 'colormap', 'plasma')
        for name, value in (('xmin', 1.2), ('xmax', 1.9), ('ymin', .2), ('ymax', 1.8),
                            ('vmin', -22.), ('vmax', 122.)):
            dat.setup_number(self.w, name, value)
        return self.w.copy_plot_settings()

    def test_direct_copy_is_frozen_without_named_store_and_selective_inactive_paste_locks_ranges(self):
        # A live/session-backed clipboard or named-store write would break this boundary.
        w = self.w
        copied = self.copy_source()
        self.assertIsNone(w._annotation_settings.value(FORMAT_TEMPLATES_KEY))
        self.assertEqual(copied.x_limits, (1.2, 1.9))
        self.assertIn(str(self.paths[0]), w.plot_page.formal.state['clipboard'])
        dat.setup_number(w, 'xmin', 1.4)
        w.set_plot_presentation(replace(self.a.presentation, width_in=3.7))
        self.b.view.annotations['plot_title'] = 'Condition B'
        old_page, old_fit, old_cube = self.b.presentation, self.b.fit_result, self.b.current_cube
        before = old_cube.z.copy()
        active, generation = w.workspace.active_dataset_id, w._data_generation
        report = w.paste_plot_settings([self.b.dataset_id], options=ranges_only(
            x_limits=True, y_limits=True, color_limits=True, title_position=True))
        self.assertTrue(report[0]['success'], report)
        self.assertEqual(self.b.view.x_limits, (1.2, 1.9))
        self.assertEqual(self.b.view.y_limits, (.2, 1.8))
        self.assertEqual(self.b.view.color_limits, (-22., 122.))
        self.assertTrue(all(self.b.view.locks.values()))
        self.assertEqual(self.b.presentation.width_in, old_page.width_in)
        self.assertEqual(self.b.presentation.title_position, (.23, .77))
        self.assertEqual(self.b.view.annotations['plot_title'], 'Condition B')
        self.assertIs(self.b.current_cube, old_cube)
        self.assertIs(self.b.fit_result, old_fit)
        np.testing.assert_array_equal(old_cube.z, before)
        self.assertEqual((w.workspace.active_dataset_id, w._data_generation), (active, generation))
        w.activate_dataset(self.b.dataset_id)
        self.assertEqual(tuple(w._heat_ax.get_xlim()), (1.2, 1.9))
        self.assertEqual(tuple(w._heat_mesh.get_clim()), (-22., 122.))
        w.activate_dataset(self.c.dataset_id)
        w.activate_dataset(self.b.dataset_id)
        self.assertEqual(self.b.view.x_limits, (1.2, 1.9))
        w._save_current_plot_state()
        other_host, _, other_errors = self.enterContext(application())
        other = other_host.controller
        self.addCleanup(lambda: self.assertFalse(other_errors))
        other._load_dat_file_direct(self.paths[1])
        self.assertEqual(other.workspace.active.view.x_limits, (1.2, 1.9))
        self.assertEqual(other.workspace.active.presentation.title_position, (.23, .77))
        self.assertTrue(dat.state(other)['xmin_lock'])

    def test_invalid_source_editor_retains_previous_clipboard_and_named_save_uses_fresh_positions(self):
        copied = self.copy_source()
        page = self.w.plot_page
        original_width = page.formal.state['fields']['width_in']
        self.formal_call(page, 'setDraft', 'width_in', '')
        self.assertFalse(self.formal_call(page, 'commitField', 'width_in'))
        with self.assertRaisesRegex(ValueError, 'editor'):
            self.w.copy_plot_settings()
        self.assertIs(self.w._plot_settings_clipboard, copied)
        self.formal_call(page, 'setDraft', 'width_in', original_width)
        self.assertTrue(self.formal_call(page, 'commitField', 'width_in'))
        self.w.set_plot_presentation(replace(self.a.presentation,
            annotations=(PlotAnnotation('data-note', text='A', position=(1.55, 1.), coordinate_space='data'),)))
        self.w.save_plot_format('Fresh')
        saved = self.w._plot_format_store.load('Fresh')
        self.assertEqual(saved.x_limits, (1.2, 1.9))
        self.assertAlmostEqual(saved.annotation_layout[0].position[0], .5)
        self.assertAlmostEqual(saved.annotation_layout[0].position[1], .5)

    def test_source_capture_finishes_pending_annotation_and_numeric_editors(self):
        self.copy_source()
        w, p = self.w, self.w.plot_page
        w.set_plot_presentation(replace(self.a.presentation, annotations=(
            PlotAnnotation('draft', text='Draft', position=(.2, .3)),)))
        self.formal_call(p, 'selectAnnotation', 'draft')
        self.formal_call(p, 'setDraft', 'annotation_text', 'Pending σ⁺')
        self.formal_call(p, 'setDraft', 'annotation_x', '.33')
        self.formal_call(p, 'setDraft', 'width_in', '4.6')
        captured = w.copy_plot_settings()
        self.assertEqual(captured.style.width_in, 4.6)
        self.assertEqual(captured.annotation_layout[0].text, 'Pending σ⁺')
        self.assertEqual(captured.annotation_layout[0].position, (.33, .3))

    def test_current_source_capture_reuses_page_and_detects_unsignaled_live_ranges(self):
        w = self.w
        w._render_plot_page()
        current = w.plot_page.rendered_page
        with patch.object(main_window, 'prepare_heatmap_page', wraps=main_window.prepare_heatmap_page) as prepare:
            w.copy_plot_settings()
            self.assertEqual(prepare.call_count, 0)
            self.assertIs(w.plot_page.rendered_page, current)
            # Capturing also imports toolbar/live limits, which need not have
            # delivered an ordinary invalidation callback yet.
            with w._heat_ax.callbacks.blocked():
                w._heat_ax.set_xlim(1.15, 1.85)
            copied = w.copy_plot_settings()
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(copied.x_limits, (1.15, 1.85))
            self.assertEqual(w.plot_page.preview.snapshot.x_limits, copied.x_limits)
            self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())

    def test_paste_publishes_validated_page_once_and_matching_paste_keeps_history(self):
        w = self.w
        w.copy_plot_settings()
        w.set_plot_presentation(replace(self.c.presentation, text_pt=self.c.presentation.text_pt + 1))
        w._render_plot_page()
        before = w._plot_owner_token()
        with patch.object(main_window, 'prepare_heatmap_page', wraps=main_window.prepare_heatmap_page) as prepare:
            report = w.paste_plot_settings()
            self.assertTrue(report[0]['success'], report)
            self.assertEqual(prepare.call_count, 1)
            page = w.plot_page.rendered_page
            self.assertIsNotNone(page)
            self.assertNotEqual(before, page.owner_token)
            self.assertEqual(page.owner_token, w._plot_owner_token())
            self.assertEqual(w.plot_page.preview.snapshot.owner_token, page.owner_token)
            self.assertFalse(w._plot_render_timer.isActive())
            pump(220)
            self.assertEqual(prepare.call_count, 1)
            owner = w._plot_owner_token()
            with patch.object(w, '_project_plot_dataset', wraps=w._project_plot_dataset) as project:
                report = w.paste_plot_settings()
                self.assertTrue(report[0]['success'], report)
                self.assertEqual(report[0]['changed_fields'], ())
                self.assertEqual(project.call_count, 0)
                self.assertEqual(prepare.call_count, 1)
            self.assertEqual(w._plot_owner_token(), owner)
            self.assertIs(w.plot_page.rendered_page, page)
        # A no-op must leave the preceding real paste as the next Undo item.
        fresh_presentation, fresh_page = main_window.prepare_heatmap_page(
            w.snapshot_plot_dataset(), self.c.presentation, renderer=main_window.render_hybrid_svg)
        self.assertEqual(fresh_presentation, self.c.presentation)
        self.assertEqual(page.svg_bytes, fresh_page.svg_bytes)
        self.assertEqual(page.digest, fresh_page.digest)
        self.assertEqual(page.object_bounds, fresh_page.object_bounds)
        self.assertTrue(w.undo_edit())
        self.assertEqual(self.c.presentation.text_pt, w._plot_settings_clipboard.style.text_pt + 1)

    def test_matching_paste_refreshes_ranges_captured_by_dialog_without_invalidation(self):
        w = self.w
        w.copy_plot_settings()
        with w._heat_ax.callbacks.blocked():
            w._heat_ax.set_xlim(1.15, 1.85)
        dialog = w.create_paste_settings_dialog(current_only=True)
        dialog.bridge.shutdown()
        dialog.deleteLater()
        self.assertEqual(self.c.view.x_limits, (1.15, 1.85))
        with patch.object(main_window, 'prepare_heatmap_page', wraps=main_window.prepare_heatmap_page) as prepare:
            report = w.paste_plot_settings()
            self.assertTrue(report[0]['success'], report)
            self.assertEqual(report[0]['changed_fields'], ())
            self.assertEqual(prepare.call_count, 1)
        self.assertEqual(w.plot_page.preview.snapshot.x_limits, (1.15, 1.85))
        self.assertEqual(w.plot_page.rendered_page.display_x_limits, (1.15, 1.85))
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())

    def test_workspace_colorbar_sample_follows_global_and_regional_rendered_limits(self):
        w = self.w
        for mode in ('global', '2'):
            with self.subTest(mode=mode):
                dat.set_choice(w, 'color_scale', mode)
                w._render_plot_page()
                dat.setup_number(w, 'vmin', -50.)
                dat.setup_number(w, 'vmax', 300.)
                w._render_plot_page()
                p, snapshot = w.plot_page, w.plot_page.preview.snapshot
                limits = ({region.id: region.color_limits for region in snapshot.regions}
                          if snapshot.regions else {'global': snapshot.color_limits})
                self.assertEqual(set(p._colorbar_norms), set(limits))
                for identifier, expected in limits.items():
                    self.assertEqual((p._colorbar_norms[identifier].vmin,
                                      p._colorbar_norms[identifier].vmax), expected)
                if mode == 'global':
                    self.assertIn('-50', p.formal.state['sample'])
                    self.assertIn('300', p.formal.state['sample'])

    def test_named_load_for_current_uses_selective_dialog_and_preserves_other_owners(self):
        self.copy_source()
        w = self.w
        w.save_plot_format('Selective')
        w.activate_dataset(self.b.dataset_id)
        self.assertIs(w.open_graph_document(self.b.dataset_id), w.plot_page)
        self.assertEqual(w.outerView, 'workspace')
        pump()
        self.assertEqual(w.workspace.active_dataset_id, self.b.dataset_id)
        self.assertTrue(w.plot_page.command_enabled('format_load'))
        old_width = self.b.presentation.width_in
        untouched = deepcopy(self.c.view)
        selected = []
        def accept_options():
            from spectra_app.plot_dialogs import ApplyFormatDialog
            dialogs = self.host.window.findChildren(ApplyFormatDialog)
            if not dialogs:
                return
            dialog = dialogs[-1]
            selected.extend(dialog.selected_ids())
            for check in dialog.bridge.option_items.values():
                check.setChecked(False)
            dialog.bridge.option_items['x_limits'].setChecked(True)
            dialog.accept()
        QTimer.singleShot(0, accept_options)
        w.plot_page.formal.command('format_load')
        self.assertEqual(selected, [self.b.dataset_id])
        self.assertEqual(self.b.view.x_limits, (1.2, 1.9))
        self.assertEqual(self.b.presentation.width_in, old_width)
        self.assertEqual(self.c.view, untouched)

    def test_dialog_selects_independent_groups_previews_ranges_and_disables_old_missing_fields(self):
        self.copy_source()
        dialog = self.w.create_paste_settings_dialog()
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.options().layout)
        self.assertFalse(dialog.options().x_limits)
        dialog.bridge.quickOptions(False)
        dialog.bridge.option_items['layout'].setChecked(False)
        dialog.bridge.option_items['typography'].setChecked(False)
        dialog.bridge.option_items['axis'].setChecked(False)
        dialog.bridge.option_items['colormap'].setChecked(False)
        dialog.bridge.option_items['colorbar'].setChecked(False)
        dialog.bridge.option_items['x_limits'].setChecked(True)
        preview = dialog.bridge.previewText
        self.assertIn('X:', preview)
        self.assertIn('Fixed', preview)
        self.assertIn('1.2', preview)
        self.assertEqual(dialog.bridge.targets[0].datasetId, self.a.dataset_id)
        self.assertIn(self.a.active_graph.name, dialog.bridge.targets[0].label)
        legacy = self.w._plot_settings_clipboard.to_dict()
        for key in ('x_limits', 'y_limits', 'title_position', 'annotation_layout'):
            legacy.pop(key)
        old = FormatTemplate.from_dict(legacy)
        from spectra_app.plot_dialogs import ApplyFormatDialog
        old_dialog = ApplyFormatDialog((self.a,), self.a.dataset_id, old, self.host.window)
        self.addCleanup(old_dialog.close)
        for name in ('x_limits', 'y_limits', 'title_position', 'annotation_positions'):
            self.assertFalse(old_dialog.bridge.option_items[name].available)
            self.assertIn('unavailable', old_dialog.bridge.option_items[name].label.lower())

    def test_title_editor_and_actual_drag_preserve_auto_geometry_text_and_annotation_delete_safety(self):
        self.assertIn('title_x', self.w.plot_page.formal.state['fields'])
        w = self.w
        p = w.open_graph_document()
        w.dat_properties.setLabel('plot_title', 'Condition C σ⁺', w.dat_properties.ownerToken)
        w.set_plot_presentation(replace(self.c.presentation, frame_width_in=None, frame_height_in=None,
            annotations=(PlotAnnotation('keep', text='keep', position=(.5, .5)),)))
        self.assertTrue(w.request_outer_view('workspace'))
        pump()
        self.formal_call(p, 'setValue', 'title_x', .2)
        self.formal_call(p, 'setValue', 'title_y', .8)
        self.assertEqual(self.c.presentation.title_position, (.2, .8))
        QTest.qWait(250)
        w._render_plot_page()
        pump()
        before = p.rendered_page
        self.assertIsNotNone(before, p.status_text)
        # Hidden Analysis geometry is deferred in the Quick shell. Inspect the
        # real live view, then return to this same formal paper for native input.
        self.assertTrue(w.request_outer_view('analysis'))
        self.assertEqual(w.workspace.active_dataset_id, self.c.dataset_id)
        w._layout_live_plots()
        self.assertEqual(w._heat_ax.title.get_position(), (.2, .8))
        self.assertTrue(w.request_outer_view('workspace'))
        self.assertIs(w.plot_page, p)
        p.select_annotation('keep')
        pump()
        left, bottom, width, height = before.object_bounds['title'][0]
        paper_width, paper_height = p.preview.paper_display_size()
        paper = quick_item(w, 'formalPaper')
        viewport = quick_item(w, 'formalViewport')
        start = paper.mapToScene(QPointF((left+width/2)*paper_width,
                                         (1-bottom-height/2)*paper_height)).toPoint()
        self.assertTrue(viewport.contains(viewport.mapFromScene(QPointF(start))))
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.host.window, start + QPoint(20, 12), delay=10)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=start + QPoint(20, 12))
        self.assertNotEqual(self.c.presentation.title_position, (.2, .8))
        viewport.forceActiveFocus()
        QTest.keyClick(self.host.window, Qt.Key_Delete)
        self.assertEqual(len(self.c.presentation.annotations), 1)
        w._render_plot_page()
        self.assertEqual(p.rendered_page.axes_bounds, before.axes_bounds)
        self.assertEqual(p.rendered_page.figure_size_in, before.figure_size_in)
        self.assertIn('Condition C', p.rendered_page.svg_bytes.decode())
        self.assertIn('page-title-overlay-mask', p.rendered_page.svg_bytes.decode())
        self.assertFalse(w._heat_ax.title.get_in_layout())

    def test_explicit_annotation_pairing_preserves_text_ids_arrowheads_and_reports_skip_and_duplicate(self):
        self.copy_source()
        w = self.w
        w.set_plot_presentation(replace(self.a.presentation, annotations=(
            PlotAnnotation('s1', text='same', position=(.2, .7)),
            PlotAnnotation('s2', text='same', position=(.3, .6)),
            PlotAnnotation('sa', kind='arrow', text='1', position=(.4, .5), target=(.9, .1)),)))
        w.copy_plot_settings()
        targets = (PlotAnnotation('t1', text='same', position=(.8, .1)),
                   PlotAnnotation('t2', text='same', position=(.7, .2)),
                   PlotAnnotation('ta', kind='arrow', text='2', position=(.6, .3), target=(.11, .22)))
        self.b.presentation = replace(self.b.presentation, annotations=targets)
        dialog = w.create_paste_settings_dialog()
        self.addCleanup(dialog.close)
        dialog.bridge.selectAll(True)
        dialog.bridge.option_items['annotation_positions'].setChecked(True)
        self.assertEqual(dialog.annotation_pairs(), {})  # Duplicate text must not infer matches.
        self.assertIn('skipped', dialog.bridge.previewText.lower())
        for source_id, target_id in (('s1', 't2'), ('sa', 'ta')):
            pair = next(p for p in dialog.bridge.pairs if p.dataset_id == self.b.dataset_id and p.source_id == source_id)
            pair.setCurrentIndex(pair.target_ids.index(target_id))
        pairs = dialog.annotation_pairs()
        report = w.paste_plot_settings([self.b.dataset_id, self.c.dataset_id],
            options=ranges_only(title_position=True, annotation_positions=True), annotation_pairs=pairs)
        self.assertTrue(all(entry['success'] for entry in report), report)
        self.assertIn('skipped', report[1]['notice'].lower())
        self.assertEqual(self.b.presentation.annotations[0], targets[0])
        self.assertEqual(self.b.presentation.annotations[1].position, (.2, .7))
        self.assertEqual(self.b.presentation.annotations[1].text, 'same')
        self.assertEqual(self.b.presentation.annotations[2].position, (.4, .5))
        self.assertEqual(self.b.presentation.annotations[2].target, (.11, .22))
        before = self.b.presentation
        bad = w.paste_plot_settings([self.b.dataset_id], options=ranges_only(annotation_positions=True),
            annotation_pairs={self.b.dataset_id: {'s1': 't1', 's2': 't1'}})
        self.assertFalse(bad[0]['success'])
        self.assertEqual(self.b.presentation, before)

    def test_two_mainwindow_comparisons_export_captured_bytes_and_metadata_after_third_dataset_changes(self):
        self.assertTrue(hasattr(self.w, 'open_plot_comparison'), 'MainWindow comparison capture is missing')
        w = self.w
        w.activate_dataset(self.a.dataset_id)
        w.dat_properties.setLabel('plot_title', 'Capture A', w.dat_properties.ownerToken)
        self.assertIn(str(self.paths[0]), w.plot_page.command_tooltip('comparison'))
        first = w.open_plot_comparison()
        w.dat_properties.setLabel('plot_title', 'Capture B', w.dat_properties.ownerToken)
        dat.set_choice(w, 'colormap', 'plasma')
        second = w.open_plot_comparison()
        originals = [win.svg_bytes for win in (first, second)]
        self.assertNotEqual(*originals)
        w.activate_dataset(self.c.dataset_id)
        w.dat_properties.setLabel('plot_title', 'Third active', w.dat_properties.ownerToken)
        self.assertIn(str(self.paths[2]), w._comparison_action.toolTip())
        dat.set_choice(w, 'colormap', 'inferno')
        w._render_plot_page()
        owner, generation, arrays = w.workspace.active_dataset_id, w._data_generation, self.c.current_cube.z.copy()
        main_zoom = w.plot_page.zoom_text
        first.set_zoom('200%')
        self.assertEqual(w.plot_page.zoom_text, main_zoom)
        self.assertEqual(second.bridge.zoom, 'Fit')
        for i, window in enumerate((first, second)):
            saved = window.export_svg(self.folder / f'capture-{i}.svg')
            self.assertEqual(saved.read_bytes(), originals[i])
            png = window.export_png(self.folder / f'capture-{i}.png')
            with Image.open(png) as image:
                meta = json.loads(image.info['SpectraAnalyzer'])
                self.assertEqual(meta['source_path'], str(self.paths[0]))
                self.assertEqual(meta['plot_title'], ('Capture A', 'Capture B')[i])
        self.assertEqual((w.workspace.active_dataset_id, w._data_generation), (owner, generation))
        np.testing.assert_array_equal(self.c.current_cube.z, arrays)
        first.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.assertEqual(w._plot_comparison_windows, [second])
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD) as prompt:
            self.host.window.close()
            wait_until(lambda: w._closing)
        prompt.assert_called_once()
        self.assertEqual(prompt.call_args.args[3],
                         (PromptAction.SAVE, PromptAction.DISCARD, PromptAction.CANCEL))
        pump()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(second))

    def test_locked_two_and_three_region_limits_survive_capture_activation_and_live_formal_projection(self):
        self.assertTrue(hasattr(self.w, 'copy_plot_settings'), 'Regional direct copy is missing')
        w = self.w
        for mode, limits in (('2', [(-20., 20.), (-50., 130.)]),
                             ('3', [(-21., 21.), (-31., 31.), (-61., 161.)])):
            w.activate_dataset(self.a.dataset_id)
            w.set_plot_presentation(replace(self.a.presentation, height_in=5.))
            dat.set_choice(w, 'color_scale', mode)
            self.a.view.color_limits = limits[0]
            self.a.view.last_valid_color_limits = limits[0]
            self.a.view.last_valid_region_color_limits = limits
            self.a.view.bounds.update(vmin=limits[0][0], vmax=limits[0][1])
            self.a.view.locks.update(vmin=True, vmax=True)
            w._project_plot_dataset(self.a)
            w.copy_plot_settings()
            self.b.view.color_scale_mode = mode
            self.b.presentation = replace(self.b.presentation, height_in=5.)
            self.b.view.last_valid_region_color_limits = [(0., 1.)] * len(limits)
            report = w.paste_plot_settings([self.b.dataset_id], options=ranges_only(color_limits=True))
            self.assertTrue(report[0]['success'], report)
            w.activate_dataset(self.b.dataset_id)
            self.assertEqual([tuple(m.get_clim()) for m in w._heat_meshes], limits)
            w._on_auto_view()
            self.assertEqual([tuple(m.get_clim()) for m in w._heat_meshes], limits,
                             'Locked regional endpoints must survive ordinary Auto View.')
            w._render_plot_page()
            self.assertEqual([r.color_limits for r in w.plot_page.preview.snapshot.regions], limits)
            w.activate_dataset(self.c.dataset_id)
            w.activate_dataset(self.b.dataset_id)
            self.assertEqual(self.b.view.last_valid_region_color_limits, limits)
            self.assertEqual([tuple(m.get_clim()) for m in w._heat_meshes], limits)

    def test_clipped_title_does_not_intercept_clicks_beyond_heatmap_frame(self):
        self.assertTrue(hasattr(self.w.plot_page.preview, 'title_point'), 'Title interaction is missing')
        w = self.w
        p = w.open_graph_document()
        self.assertTrue(w.request_outer_view('workspace'))
        pump()
        w.dat_properties.setLabel('plot_title', 'Long literal clipped title ' * 6, w.dat_properties.ownerToken)
        w.set_plot_presentation(replace(self.c.presentation, title_position=(.85, .85)))
        QTest.qWait(250)
        w._render_plot_page()
        pump()
        page = p.rendered_page
        self.assertIsNotNone(page, p.status_text)
        left, bottom, width, height = page.axes_bounds
        paper_width, _ = p.preview.paper_display_size()
        paper = quick_item(w, 'formalPaper')
        viewport = quick_item(w, 'formalViewport')
        outside = paper.mapToScene(QPointF((left+width)*paper_width+4,
                                           p.preview.title_point().y()+4)).toPoint()
        self.assertTrue(viewport.contains(viewport.mapFromScene(QPointF(outside))))
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=outside)
        self.assertIsNone(p.preview._gesture,
                          'Invisible title text outside the clipped axes must not start a drag.')
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=outside)

    def test_regional_fixed_controls_can_unlock_and_return_to_visible_percentiles(self):
        # A copied regional lock must be observable and reversible in the actual controls.
        w = self.w
        dat.set_choice(w, 'color_scale', '2')
        self.c.view.last_valid_region_color_limits = [(-20., 20.), (-50., 130.)]
        self.c.view.color_limits = (-20., 20.)
        self.c.view.bounds.update(vmin=-20., vmax=20.)
        self.c.view.locks.update(vmin=True, vmax=True)
        w._project_plot_dataset(self.c)
        self.assertTrue(dat.dat_item(w, 'vmin_lock').isEnabled())
        self.assertTrue(dat.dat_item(w, 'vmax_lock').isEnabled())
        self.assertTrue(dat.dat_item(w, 'shared_auto_colors').isEnabled())
        self.assertIn('Fixed', dat.state(w)['color_range_status'])
        self.assertFalse(dat.dat_item(w, 'vmin').isEnabled())
        dat.click(w, 'vmax_lock')
        self.assertIn('Partial', dat.state(w)['color_range_status'])
        self.assertEqual([m.get_clim()[0] for m in w._heat_meshes], [-20., -50.])
        dat.click(w, 'shared_auto_colors')
        self.assertFalse(dat.state(w)['vmin_lock'])
        self.assertFalse(dat.state(w)['vmax_lock'])
        dat.setup_number(w, 'ymin', 1.)
        np.testing.assert_allclose([m.get_clim() for m in w._heat_meshes],
                                   [(1.055, 59.45), (7.055, 119.45)], atol=1e-8)
        self.assertIn('independent Auto', dat.state(w)['color_range_status'])

    def test_switching_global_fixed_to_regional_starts_independent_auto_ranges(self):
        w = self.w
        dat.setup_number(w, 'vmin', -20.)
        dat.setup_number(w, 'vmax', 130.)
        dat.set_flag(w, 'vmin_lock', True)
        dat.set_flag(w, 'vmax_lock', True)
        dat.set_choice(w, 'color_scale', '2')
        self.assertFalse(dat.state(w)['vmin_lock'])
        self.assertFalse(dat.state(w)['vmax_lock'])
        dat.setup_number(w, 'ymin', 1.)
        np.testing.assert_allclose([m.get_clim() for m in w._heat_meshes],
                                   [(1.055, 59.45), (7.055, 119.45)], atol=1e-8)

    def test_copy_and_named_actions_fit_narrower_mainwindow_without_expanding_it(self):
        w = self.w
        w.open_graph_document()
        self.host.window.resize(1250, 960)
        pump(60)
        self.assertLessEqual(self.host.window.width(), 1250, 'Plot actions must not impose an oversized minimum window width.')
        host = w
        for name in ('formal_settings_menu', 'formal_formats_menu'):
            button = quick_item(host, name)
            top_left = button.mapToScene(QPointF()).toPoint()
            bottom_right = button.mapToScene(QPointF(button.width(), button.height())).toPoint()
            self.assertTrue(self.host.window.contentItem().contains(QPointF(top_left)), (name, top_left))
            self.assertTrue(self.host.window.contentItem().contains(QPointF(bottom_right)), (name, bottom_right))
        for menu, commands in (('formal_settings_menu', ('settings_copy', 'settings_paste',
                                'settings_copy_to', 'comparison')),
                               ('formal_formats_menu', ('format_save', 'format_load'))):
            click_item(host, menu)
            popup = None
            for command in commands:
                control = quick_item(host, 'formal_' + command)
                self.assertTrue(control.isVisible(), command)
                self.assertGreater(control.width(), 0, command)
                self.assertGreater(control.height(), 0, command)
                window = input_receiver(w, control)
                self.assertIsNotNone(window, command)
                self.assertIsNot(window, self.host.window, command)
                self.assertTrue(window.isVisible(), command)
                if popup is None:
                    popup = window
                self.assertIs(window, popup, command)
                top_left = window.mapToGlobal(control.mapToScene(QPointF()).toPoint())
                bottom_right = window.mapToGlobal(control.mapToScene(QPointF(
                    control.width()-1, control.height()-1)).toPoint())
                popup_bounds = window.geometry()
                screen = window.screen()
                self.assertIsNotNone(screen, command)
                screen_bounds = screen.availableGeometry()
                for point in (top_left, bottom_right):
                    self.assertTrue(popup_bounds.contains(point), (command, point, popup_bounds))
                    self.assertTrue(screen_bounds.contains(point), (command, point, screen_bounds))
            QTest.keyClick(popup, Qt.Key_Escape)
            pump()
            self.assertFalse(quick_item(host, 'formal_' + commands[0]).isVisible(), menu)
