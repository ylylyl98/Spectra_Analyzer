"""Selective transfer and comparison proof at the actual MainWindow boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from dataclasses import replace
import json
import unittest

import numpy as np
from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from spectra_app.main_window import MainWindow
from spectra_app.plot_presentation import PlotAnnotation
from spectra_app.plot_templates import FORMAT_TEMPLATES_KEY, FormatApplicationOptions, FormatTemplate
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])


def ranges_only(**kwargs):
    return FormatApplicationOptions(layout=False, typography=False, axis=False,
                                    colormap=False, colorbar=False, **kwargs)


class PlotCopyCompareWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.errors = []
        self.w._show_error = self.errors.append
        self.paths = []
        for name in ('condition-a', 'condition-b', 'condition-c'):
            path = self.folder / (name + '.dat')
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
            self.paths.append(path)
            self.w._load_dat_file_direct(path)
        self.a, self.b, self.c = self.w.workspace.datasets.values()
        self.w.resize(1500, 960)
        self.w.show()
        # Let startup layout callbacks finish before assertions about frozen bytes.
        QTest.qWait(250)

    def tearDown(self):
        self.w.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def copy_source(self):
        self.assertTrue(hasattr(self.w, 'copy_plot_settings'), 'Direct settings capture is missing')
        self.w.activate_dataset(self.a.dataset_id)
        self.w.plot_title_edit.setText('Condition A')
        self.w.set_plot_presentation(replace(self.a.presentation, width_in=4.2, aspect_locked=False,
                                            title_position=(.23, .77)))
        self.w.cmap_combo.setCurrentText('plasma')
        for name, value in (('xmin', 1.2), ('xmax', 1.9), ('ymin', .2), ('ymax', 1.8),
                            ('vmin', -22.), ('vmax', 122.)):
            getattr(self.w, name + '_spin').setValue(value)
        return self.w.copy_plot_settings()

    def test_direct_copy_is_frozen_without_named_store_and_selective_inactive_paste_locks_ranges(self):
        # A live/session-backed clipboard or named-store write would break this boundary.
        w = self.w
        copied = self.copy_source()
        self.assertIsNone(w._annotation_settings.value(FORMAT_TEMPLATES_KEY))
        self.assertEqual(copied.x_limits, (1.2, 1.9))
        self.assertIn(str(self.paths[0]), w.plot_page.clipboard_label.text())
        w.xmin_spin.setValue(1.4)
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
        other = MainWindow()
        self.addCleanup(other.close)
        other._load_dat_file_direct(self.paths[1])
        self.assertEqual(other.workspace.active.view.x_limits, (1.2, 1.9))
        self.assertEqual(other.workspace.active.presentation.title_position, (.23, .77))
        self.assertTrue(other.xmin_lock.isChecked())

    def test_invalid_source_editor_retains_previous_clipboard_and_named_save_uses_fresh_positions(self):
        copied = self.copy_source()
        self.w.plot_page.font_edit.setText('')
        self.w.plot_page._emit_presentation()
        with self.assertRaisesRegex(ValueError, 'editor'):
            self.w.copy_plot_settings()
        self.assertIs(self.w._plot_settings_clipboard, copied)
        self.w.plot_page.font_edit.setText('Arial')
        self.w.plot_page._emit_presentation()
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
        p.annotation_text.setText('Pending σ⁺')
        p.annotation_x.setValue(.33)
        p.width_spin.lineEdit().setText('4.6')
        captured = w.copy_plot_settings()
        self.assertEqual(captured.style.width_in, 4.6)
        self.assertEqual(captured.annotation_layout[0].text, 'Pending σ⁺')
        self.assertEqual(captured.annotation_layout[0].position, (.33, .3))

    def test_named_load_for_current_uses_selective_dialog_and_preserves_other_owners(self):
        self.copy_source()
        w = self.w
        w.save_plot_format('Selective')
        w.activate_dataset(self.b.dataset_id)
        old_width = self.b.presentation.width_in
        untouched = deepcopy(self.c.view)
        selected = []
        def accept_options():
            from spectra_app.plot_dialogs import ApplyFormatDialog
            dialogs = w.findChildren(ApplyFormatDialog)
            if not dialogs:
                return
            dialog = dialogs[-1]
            selected.extend(dialog.selected_ids())
            for check in dialog.option_checks.values():
                check.setChecked(False)
            dialog.option_checks['x_limits'].setChecked(True)
            dialog.accept()
        QTimer.singleShot(0, accept_options)
        w.plot_page.format_load_btn.click()
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
        dialog.only_format_btn.click()
        dialog.option_checks['layout'].setChecked(False)
        dialog.option_checks['typography'].setChecked(False)
        dialog.option_checks['axis'].setChecked(False)
        dialog.option_checks['colormap'].setChecked(False)
        dialog.option_checks['colorbar'].setChecked(False)
        dialog.option_checks['x_limits'].setChecked(True)
        preview = dialog.preview_text.toPlainText()
        self.assertIn('X:', preview)
        self.assertIn('Fixed', preview)
        self.assertIn('1.2', preview)
        self.assertEqual(dialog.targets.item(0).data(Qt.UserRole), self.a.dataset_id)
        self.assertIn(self.a.active_graph.name, dialog.targets.item(0).text())
        legacy = self.w._plot_settings_clipboard.to_dict()
        for key in ('x_limits', 'y_limits', 'title_position', 'annotation_layout'):
            legacy.pop(key)
        old = FormatTemplate.from_dict(legacy)
        from spectra_app.plot_dialogs import ApplyFormatDialog
        old_dialog = ApplyFormatDialog((self.a,), self.a.dataset_id, old, self.w)
        self.addCleanup(old_dialog.close)
        for name in ('x_limits', 'y_limits', 'title_position', 'annotation_positions'):
            self.assertFalse(old_dialog.option_checks[name].isEnabled())
            self.assertIn('unavailable', old_dialog.option_checks[name].text().lower())

    def test_title_editor_and_actual_drag_preserve_auto_geometry_text_and_annotation_delete_safety(self):
        self.assertTrue(hasattr(self.w.plot_page, 'title_x_spin'), 'Title position editor is missing')
        w, p = self.w, self.w.plot_page
        w.plot_title_edit.setText('Condition C σ⁺')
        w.set_plot_presentation(replace(self.c.presentation, frame_width_in=None, frame_height_in=None,
            annotations=(PlotAnnotation('keep', text='keep', position=(.5, .5)),)))
        w.plot_tabs.setCurrentIndex(1)
        p.title_x_spin.setValue(.2)
        p.title_y_spin.setValue(.8)
        QTest.qWait(250)
        w._render_plot_page()
        APP.processEvents()
        before = p.rendered_page
        self.assertIsNotNone(before, p.status_label.text())
        w._layout_live_plots()
        self.assertEqual(w._heat_ax.title.get_position(), (.2, .8))
        p.select_annotation('keep')
        start = p.preview.title_point()
        QTest.mousePress(p.preview, Qt.LeftButton, pos=start)
        QTest.mouseMove(p.preview, start + QPoint(20, 12), delay=10)
        QTest.mouseRelease(p.preview, Qt.LeftButton, pos=start + QPoint(20, 12))
        self.assertNotEqual(self.c.presentation.title_position, (.2, .8))
        p.preview.setFocus()
        QTest.keyClick(p.preview, Qt.Key_Delete)
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
        dialog.select_all_btn.click()
        dialog.option_checks['annotation_positions'].setChecked(True)
        self.assertEqual(dialog.annotation_pairs(), {})  # Duplicate text must not infer matches.
        self.assertIn('skipped', dialog.preview_text.toPlainText().lower())
        dialog.pair_combos[self.b.dataset_id]['s1'].setCurrentIndex(
            dialog.pair_combos[self.b.dataset_id]['s1'].findData('t2'))
        dialog.pair_combos[self.b.dataset_id]['sa'].setCurrentIndex(
            dialog.pair_combos[self.b.dataset_id]['sa'].findData('ta'))
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
        w.plot_title_edit.setText('Capture A')
        self.assertIn(str(self.paths[0]), w.plot_page.comparison_btn.toolTip())
        first = w.open_plot_comparison()
        w.plot_title_edit.setText('Capture B')
        w.cmap_combo.setCurrentText('plasma')
        second = w.open_plot_comparison()
        originals = [win.preview.svg_bytes for win in (first, second)]
        self.assertNotEqual(*originals)
        w.activate_dataset(self.c.dataset_id)
        w.plot_title_edit.setText('Third active')
        self.assertIn(str(self.paths[2]), w._comparison_action.toolTip())
        w.cmap_combo.setCurrentText('inferno')
        w._render_plot_page()
        owner, generation, arrays = w.workspace.active_dataset_id, w._data_generation, self.c.current_cube.z.copy()
        main_zoom = w.plot_page.zoom_combo.currentText()
        first.set_zoom('200%')
        self.assertEqual(w.plot_page.zoom_combo.currentText(), main_zoom)
        self.assertEqual(second.zoom_combo.currentText(), 'Fit')
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
        APP.processEvents()
        self.assertEqual(w._plot_comparison_windows, [second])
        w.close()
        APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(second))

    def test_locked_two_and_three_region_limits_survive_capture_activation_and_live_formal_projection(self):
        self.assertTrue(hasattr(self.w, 'copy_plot_settings'), 'Regional direct copy is missing')
        w = self.w
        for mode, limits in (('2', [(-20., 20.), (-50., 130.)]),
                             ('3', [(-21., 21.), (-31., 31.), (-61., 161.)])):
            w.activate_dataset(self.a.dataset_id)
            w.set_plot_presentation(replace(self.a.presentation, height_in=5.))
            w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData(mode))
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
        w, p = self.w, self.w.plot_page
        w.plot_tabs.setCurrentIndex(1)
        w.plot_title_edit.setText('Long literal clipped title ' * 6)
        w.set_plot_presentation(replace(self.c.presentation, title_position=(.85, .85)))
        QTest.qWait(250)
        w._render_plot_page()
        page = p.rendered_page
        self.assertIsNotNone(page, p.status_label.text())
        left, bottom, width, height = page.axes_bounds
        outside = QPoint(round((left+width)*p.preview.width())+4, p.preview.title_point().y()+4)
        QTest.mousePress(p.preview, Qt.LeftButton, pos=outside)
        self.assertIsNone(p.preview._drag_title,
                          'Invisible title text outside the clipped axes must not start a drag.')
        QTest.mouseRelease(p.preview, Qt.LeftButton, pos=outside)

    def test_regional_fixed_controls_can_unlock_and_return_to_visible_percentiles(self):
        # A copied regional lock must be observable and reversible in the actual controls.
        w = self.w
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        self.c.view.last_valid_region_color_limits = [(-20., 20.), (-50., 130.)]
        self.c.view.color_limits = (-20., 20.)
        self.c.view.bounds.update(vmin=-20., vmax=20.)
        self.c.view.locks.update(vmin=True, vmax=True)
        w._project_plot_dataset(self.c)
        self.assertTrue(w.vmin_lock.isEnabled())
        self.assertTrue(w.vmax_lock.isEnabled())
        self.assertTrue(w.auto_color_btn.isEnabled())
        self.assertIn('Fixed', w.color_range_status.text())
        self.assertFalse(w.vmin_spin.isEnabled())
        w.vmax_lock.click()
        self.assertIn('Partial', w.color_range_status.text())
        self.assertEqual([m.get_clim()[0] for m in w._heat_meshes], [-20., -50.])
        w.auto_color_btn.click()
        self.assertFalse(w.vmin_lock.isChecked())
        self.assertFalse(w.vmax_lock.isChecked())
        w.ymin_spin.setValue(1.)
        np.testing.assert_allclose([m.get_clim() for m in w._heat_meshes],
                                   [(1.055, 59.45), (7.055, 119.45)], atol=1e-8)
        self.assertIn('independent Auto', w.color_range_status.text())

    def test_switching_global_fixed_to_regional_starts_independent_auto_ranges(self):
        w = self.w
        w.vmin_spin.setValue(-20.)
        w.vmax_spin.setValue(130.)
        w.vmin_lock.setChecked(True)
        w.vmax_lock.setChecked(True)
        w.color_scale_combo.setCurrentIndex(w.color_scale_combo.findData('2'))
        self.assertFalse(w.vmin_lock.isChecked())
        self.assertFalse(w.vmax_lock.isChecked())
        w.ymin_spin.setValue(1.)
        np.testing.assert_allclose([m.get_clim() for m in w._heat_meshes],
                                   [(1.055, 59.45), (7.055, 119.45)], atol=1e-8)

    def test_copy_and_named_actions_fit_narrower_mainwindow_without_expanding_it(self):
        w = self.w
        w.plot_tabs.setCurrentIndex(1)
        w.resize(1250, 960)
        APP.processEvents()
        self.assertLessEqual(w.width(), 1250, 'Plot actions must not impose an oversized minimum window width.')
        for button in (w.plot_page.copy_settings_btn, w.plot_page.paste_settings_btn,
                       w.plot_page.copy_settings_to_btn, w.plot_page.comparison_btn,
                       w.plot_page.format_save_btn, w.plot_page.format_load_btn):
            self.assertTrue(w.plot_page.rect().contains(button.geometry().topLeft()))
            self.assertTrue(w.plot_page.rect().contains(button.geometry().bottomRight()))
