import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView
from spectra_app.main_window import MainWindow
from spectra_app.dataset_state import graph_export_stem
from spectra_app.plot_state import active_plot_recipe
from spectra_app.plot_presentation import ColorbarTicks, NormSpec, PlotAnnotation
from spectra_app.plot_templates import FORMAT_TEMPLATES_KEY
from tests.settings_helpers import isolate_settings
from tests.background_helpers import wait_jobs

APP = QApplication.instance() or QApplication([])


class PlotEditingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.windows = [self.w]
        self.errors = []
        self.w._show_error = self.errors.append
        self.paths = []
        for name in ('a', 'b'):
            path = self.folder / (name + '.dat')
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
            self.paths.append(path)
            self.w._load_dat_file_direct(path)
            self.w.open_graph_document()
        self.a, self.b = self.w.workspace.datasets.values()
        self.w.resize(1500, 950)
        self.w.plot_tabs.setCurrentIndex(1)
        self.w.show()
        APP.processEvents()

    def tearDown(self):
        for window in self.windows:
            wait_jobs(window)
            window.close()
            APP.processEvents()
            window.deleteLater()
        APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def render(self):
        self.w._render_plot_page()
        self.assertIsNotNone(self.w.plot_page.rendered_page, self.w.plot_page.status_label.text())
        APP.processEvents()
        return self.w.plot_page.rendered_page

    def export_path(self, session, index=1, extension='svg'):
        stem = graph_export_stem(session.source_path.stem, session.active_graph_id, session.active_graph.name)
        return self.folder / f'{stem}_export{index:02d}.{extension}'

    def test_named_format_recall_applies_selected_owners_without_losing_ranges_or_results(self):
        w = self.w
        self.assertTrue(hasattr(w, 'save_plot_format'), 'Named plot formats need UI integration')
        w.set_plot_presentation(replace(self.b.presentation, width_in=4.2, aspect_locked=False))
        w.cmap_combo.setCurrentText('plasma')
        w.save_plot_format('Paper')
        w.set_plot_presentation(replace(self.b.presentation, width_in=3.3))
        for i, owner in enumerate((self.a, self.b)):
            owner.view.x_limits = (1.12+i*.01, 1.91+i*.01)
            owner.view.y_limits = (.1+i*.1, 1.8)
            owner.view.color_limits = (-50.-i, 150.+i)
            owner.view.annotations['plot_title'] = 'Owner ' + str(i)
            owner.presentation = replace(owner.presentation, colorbar=replace(owner.presentation.colorbar,
                ticks={'global': ColorbarTicks((-20., 40., 100.),('L','M','H'))}),
                annotations=(PlotAnnotation('note'+str(i), text='9T σ⁺'),))
            owner.fit_result = object()
        before = [(deepcopy(o.view), o.presentation, o.current_cube, o.fit_result, o.current_cube.z.copy())
                  for o in (self.a, self.b)]
        active, epoch = w.workspace.active_dataset_id, w._data_generation
        report = w.apply_plot_format('Paper', [self.a.dataset_id, self.b.dataset_id])
        self.assertTrue(all(item['success'] for item in report), report)
        self.assertEqual(w.workspace.active_dataset_id, active)
        self.assertEqual(w._data_generation, epoch)
        for owner, (view, page, cube, fit, z) in zip((self.a,self.b), before):
            self.assertEqual(owner.presentation.width_in, 4.2)
            self.assertEqual(owner.view.colormap, 'plasma')
            self.assertEqual(owner.view.x_limits, view.x_limits)
            self.assertEqual(owner.view.y_limits, view.y_limits)
            self.assertEqual(owner.view.color_limits, view.color_limits)
            self.assertEqual(owner.view.annotations, view.annotations)
            self.assertEqual(owner.presentation.colorbar.ticks, page.colorbar.ticks)
            self.assertEqual(owner.presentation.annotations, page.annotations)
            self.assertIs(owner.current_cube, cube)
            self.assertIs(owner.fit_result, fit)
            np.testing.assert_array_equal(cube.z, z)
            payload = json.loads(w._plot_state_store.path_for(owner.source_path).read_text(encoding='utf-8'))
            self.assertEqual(active_plot_recipe(payload['plot'])['presentation']['width_in'], 4.2)
        self.assertEqual(w.cmap_combo.currentText(), 'plasma')
        self.assertEqual(tuple(w._heat_ax.get_xlim()), self.b.view.x_limits)
        self.assertEqual(tuple(w._heat_mesh.get_clim()), (-51.,151.))
        self.render()
        w.delete_plot_format('Paper')
        self.assertEqual(w.plot_page.format_combo.count(), 0)

    def test_target_checkboxes_preview_and_partial_errors_are_independent_of_navigation(self):
        w = self.w
        self.assertTrue(hasattr(w, 'create_format_dialog'), 'Target checkbox format dialog is missing')
        w.save_plot_format('Paper')
        self.a.view.norm = NormSpec('log')
        self.a.view.color_limits = (-1., 5.)
        # Explicit limits copied to a regional target must fail, independently.
        self.a.view.color_scale_mode = '2'
        dialog = w.create_format_dialog('Paper')
        self.assertFalse(dialog.copy_mapping.isChecked())
        self.assertFalse(dialog.copy_limits.isChecked())
        dialog.select_all_btn.click()
        self.assertEqual(set(dialog.selected_ids()), {self.a.dataset_id,self.b.dataset_id})
        dialog.copy_limits.setChecked(True)
        self.assertIn('same global or regional', dialog.preview_text.toPlainText())
        self.assertEqual(w.imported_dataset_list.selectionMode(), QAbstractItemView.SingleSelection)
        self.assertEqual(w.workspace.active_dataset_id, self.b.dataset_id)
        results = w.apply_plot_format('Paper', dialog.selected_ids(), copy_color_limits=True)
        self.assertFalse(results[0]['success'])
        self.assertTrue(results[1]['success'])
        dialog.clear_btn.click()
        self.assertEqual(dialog.selected_ids(), [])

    def test_malformed_format_preferences_are_reported_and_preserved(self):
        w = self.w
        self.assertTrue(hasattr(w, 'save_plot_format'), 'Format store UI is missing')
        w._annotation_settings.setValue(FORMAT_TEMPLATES_KEY, '{bad')
        with self.assertRaises(ValueError):
            w.save_plot_format('Paper')
        w._refresh_plot_formats()
        self.assertEqual(w._annotation_settings.value(FORMAT_TEMPLATES_KEY), '{bad')
        self.assertIn('malformed', w.plot_page.status_label.text())

    def test_qtimer_batch_freezes_jobs_and_preserves_active_owner_and_png_metadata(self):
        w = self.w
        self.assertTrue(hasattr(w, 'start_plot_batch'), 'Serial GUI batch binding is missing')
        self.render()
        before = (w.workspace.active_dataset_id, w._data_generation, w.plot_page.preview.svg_bytes,
                  w.cmap_combo.currentText(), self.b.current_cube.z.copy())
        w.start_plot_batch([self.a.dataset_id,self.b.dataset_id], formats=('svg','png'))
        self.assertTrue(w._plot_batch_timer.isActive())
        # The queued inactive job must retain its captured settings.
        self.a.view.annotations['plot_title'] = 'Later edit'
        QTest.qWait(1400)
        self.assertTrue(w.plot_batch_summary.success, w.plot_batch_report)
        self.assertEqual(w.workspace.active_dataset_id, before[0])
        self.assertEqual(w._data_generation, before[1])
        self.assertEqual(w.plot_page.preview.svg_bytes, before[2])
        self.assertEqual(w.cmap_combo.currentText(), before[3])
        np.testing.assert_array_equal(self.b.current_cube.z, before[4])
        with Image.open(self.export_path(self.a, extension='png')) as image:
            meta=json.loads(image.info['SpectraAnalyzer'])
            self.assertEqual(meta['source_path'], str(self.paths[0]))
            self.assertNotEqual(meta['plot_title'], 'Later edit')
            self.assertIn('presentation',meta)
        old = self.export_path(self.a).read_bytes()
        w.start_plot_batch([self.a.dataset_id], formats=('svg','png'))
        QTest.qWait(700)
        self.assertTrue(self.export_path(self.a, 2).exists())
        self.assertEqual(self.export_path(self.a).read_bytes(),old)

    def test_batch_mixed_preparation_write_failure_and_cancel_keep_finished_files(self):
        w = self.w
        self.assertTrue(hasattr(w, 'start_plot_batch'), 'Batch cancel binding is missing')
        self.a.current_cube = None
        w.start_plot_batch([self.a.dataset_id,self.b.dataset_id], formats=('svg','png'))
        QTest.qWait(900)
        self.assertEqual(w.plot_batch_summary.failed,1)
        self.assertIn('prepare',w.plot_batch_report)
        self.assertTrue(self.export_path(self.b, extension='png').exists())
        w.start_plot_batch([self.b.dataset_id,self.b.dataset_id], formats=('svg',))
        # One GUI step, then cancel between files.
        w._plot_batch_timer.stop()
        w._step_plot_batch()
        w.cancel_plot_batch()
        self.assertEqual(w.plot_batch_summary.canceled,1)
        self.assertTrue(self.export_path(self.b, 2).exists())
        self.assertFalse(self.export_path(self.b, 3).exists())

    def test_actual_preview_text_drag_edit_delete_and_annotation_sidecar_isolation(self):
        w=self.w; p=w.plot_page
        self.assertTrue(hasattr(p,'add_annotation'), 'Formal page annotation controls are missing')
        self.render()
        p.add_annotation('text','9T σ⁺')
        self.render()
        annotation=self.b.presentation.annotations[0]
        self.assertEqual(annotation.position,(.15,.85))
        start=p.preview.annotation_point(annotation)
        end=start+QPoint(24,18)
        QTest.mousePress(p.preview,Qt.LeftButton,pos=start)
        QTest.mouseMove(p.preview,end,delay=10)
        self.assertIsNotNone(p.rendered_page, 'Dragging must retain the paper geometry')
        QTest.mouseRelease(p.preview,Qt.LeftButton,pos=end)
        self.assertNotEqual(self.b.presentation.annotations[0].position,annotation.position)
        self.render()
        p.annotation_text.setText('10T σ⁺')
        p.annotation_apply_btn.click()
        page=self.render()
        self.assertIn('10T',page.svg_bytes.decode())
        self.assertNotIn('editing-handle',page.svg_bytes.decode())
        w._save_current_plot_state()
        other_page = w.open_graph_document(self.a.dataset_id)
        self.assertEqual(other_page.annotation_list.count(),0)
        self.assertIs(w.open_graph_document(self.b.dataset_id), p)
        self.assertEqual(p.annotation_list.count(),1)
        p.preview.setFocus()
        QTest.keyClick(p.preview,Qt.Key_Delete)
        self.assertEqual(self.b.presentation.annotations,())
        self.assertEqual(self.a.presentation.annotations,())

    def test_batch_common_folder_blank_is_rejected_and_results_are_selectable(self):
        from spectra_app.plot_dialogs import BatchExportDialog
        dialog=BatchExportDialog((self.a,self.b),self.b.dataset_id,self.w)
        dialog.common_folder.setChecked(True)
        with self.assertRaises(ValueError):
            self.w.start_plot_batch(dialog.selected_ids(),common_folder=dialog.destination())
        self.w.start_plot_batch([self.b.dataset_id],formats=('svg',))
        QTest.qWait(700)
        self.assertTrue(hasattr(self.w,'create_plot_batch_results_dialog'), 'Batch details need a selectable report')
        result=self.w.create_plot_batch_results_dialog()
        self.assertIn(self.export_path(self.b).name,result.report_text.toPlainText())

    def test_format_renderer_rejects_invalid_target_without_mutating_it(self):
        w=self.w
        w.save_plot_format('Paper')
        # Out-of-range custom ticks are invalid. Crowded valid labels now
        # produce an export warning while retaining the requested colorbar.
        self.a.presentation=replace(self.a.presentation,colorbar=replace(self.a.presentation.colorbar,
            ticks={'global':ColorbarTicks((-20.,0.,100.))}))
        before=self.a.presentation
        results=w.apply_plot_format('Paper',[self.a.dataset_id,self.b.dataset_id])
        self.assertFalse(results[0]['success'],'Invalid retained ticks must be a per-target format error')
        self.assertIn('inside the valid color limits', results[0]['error'])
        self.assertTrue(results[1]['success'])
        self.assertEqual(self.a.presentation,before)

    def test_numbered_arrow_endpoint_drag_and_keyboard_nudge_are_zoom_independent(self):
        w=self.w; p=w.plot_page
        self.assertTrue(hasattr(p,'add_annotation'), 'Arrow annotation controls are missing')
        self.render()
        p.add_annotation('arrow','1')
        self.render(); old=self.b.presentation.annotations[0]
        self.assertEqual(old.font_role,'numeric')
        p.set_zoom('200%'); APP.processEvents()
        start=p.preview.annotation_point(old,endpoint=True)
        QTest.mousePress(p.preview,Qt.LeftButton,pos=start)
        QTest.mouseMove(p.preview,start+QPoint(20,0),delay=10)
        QTest.mouseRelease(p.preview,Qt.LeftButton,pos=start+QPoint(20,0))
        moved=self.b.presentation.annotations[0]
        self.assertEqual(moved.position,old.position)
        self.assertGreater(moved.target[0],old.target[0])
        self.render(); p.preview.setFocus()
        QTest.keyClick(p.preview,Qt.Key_Right)
        nudged=self.b.presentation.annotations[0]
        self.assertGreater(nudged.position[0],old.position[0])
        self.assertEqual(nudged.target,moved.target)
        w._save_current_plot_state()
        w.close()
        APP.processEvents()
        w2=MainWindow(); self.windows.append(w2)
        w2._load_dat_file_direct(self.paths[1])
        self.assertEqual(w2.workspace.active.presentation.annotations,(nudged,))

    def test_batch_one_png_write_failure_preserves_svg_and_continues_then_close_cancels_pending(self):
        from spectra_app.plot_batch import SerialPlotBatch
        from spectra_app.svg_export import write_svg_png
        def write_png(page,path,**kwargs):
            if path.name.startswith('a_'):
                raise OSError('Simulated PNG destination failure')
            return write_svg_png(page,path,**kwargs)
        def runner(jobs,**kwargs):
            return SerialPlotBatch(jobs,png_writer=write_png,**kwargs)
        with patch('spectra_app.main_window.SerialPlotBatch',side_effect=runner):
            self.w.start_plot_batch([self.a.dataset_id,self.b.dataset_id],formats=('svg','png'))
        QTest.qWait(900)
        self.assertEqual(self.w.plot_batch_summary.failed,1)
        self.assertTrue(self.export_path(self.a).exists())
        self.assertFalse(self.export_path(self.a, extension='png').exists())
        self.assertTrue(self.export_path(self.b, extension='png').exists())
        self.assertIn('PNG failed (write)',self.w.plot_batch_report)
        self.w.start_plot_batch([self.a.dataset_id,self.b.dataset_id],formats=('svg',))
        self.w._plot_batch_timer.stop()
        self.w._step_plot_batch()
        self.w.close()
        self.assertEqual(self.w.plot_batch_summary.canceled,1)
        self.assertFalse(self.w._plot_batch_timer.isActive())
        self.assertTrue(self.export_path(self.a, 2).exists())
        self.assertFalse(self.export_path(self.b, 2).exists())

    def test_data_annotation_space_editor_keeps_location_after_exchange_flip_and_zoom(self):
        w=self.w; p=w.plot_page
        w.set_plot_presentation(replace(self.b.presentation,axis=replace(self.b.presentation.axis,
            exchange_xy=True,flip_x=True,flip_y=True)))
        self.render()
        p.add_annotation('text','9T σ⁺')
        self.render()
        before=p.preview.annotation_point(self.b.presentation.annotations[0])
        p.annotation_space.setCurrentIndex(p.annotation_space.findData('data'))
        p.annotation_apply_btn.click()
        self.render()
        item=self.b.presentation.annotations[0]
        self.assertEqual(item.coordinate_space,'data')
        after=p.preview.annotation_point(item)
        self.assertLessEqual((before-after).manhattanLength(),1)
        position=item.position
        p.set_zoom('200%'); APP.processEvents()
        self.assertEqual(self.b.presentation.annotations[0].position,position)
        p.annotation_visible.setChecked(False)
        p.annotation_apply_btn.click()
        page=self.render()
        self.assertNotIn('annotation-'+item.id,page.svg_bytes.decode())
        self.assertFalse(self.b.presentation.annotations[0].visible)

    def test_invalid_active_tick_edit_cannot_be_saved_or_exported_through_batch(self):
        w=self.w; p=w.plot_page
        self.render()
        p.tick_values_edit.setText('not three numbers')
        p.custom_ticks.setChecked(True)
        self.assertIsNotNone(p.presentation_error)
        with self.assertRaises(ValueError):
            w.save_plot_format('Invalid editor')
        w.start_plot_batch([self.b.dataset_id,self.a.dataset_id],formats=('svg',))
        QTest.qWait(700)
        self.assertEqual(w.plot_batch_summary.failed,1)
        self.assertFalse(self.export_path(self.b).exists())
        self.assertTrue(self.export_path(self.a).exists())

    def test_negative_data_annotation_on_log_coordinate_axis_preserves_valid_note(self):
        p=self.w.plot_page
        self.render()
        p.add_annotation('text','9T σ⁺')
        self.render()
        p.preview.set_geometry(replace(p.preview.snapshot,x_scale='log'),p.presentation)
        p.annotation_space.setCurrentIndex(p.annotation_space.findData('data'))
        p.annotation_x.setValue(-1.)
        before=self.b.presentation.annotations[0]
        p.annotation_apply_btn.click()
        self.assertEqual(self.b.presentation.annotations[0],before)
        self.assertIn('positive',p.status_label.text())
        p.preview.grab()  # A rejected coordinate must never raise during overlay painting.

    def test_annotation_add_buttons_require_a_valid_page_and_block_empty_window(self):
        empty=MainWindow()
        self.windows.append(empty)
        self.assertFalse(empty.plot_page.add_text_btn.isEnabled())
        self.assertFalse(empty.plot_page.add_arrow_btn.isEnabled())
        with self.assertRaises(ValueError):
            empty.plot_page.add_annotation('text','No data')
        self.render()
        self.assertTrue(self.w.plot_page.add_text_btn.isEnabled())
        self.w._invalidate_plot_page(schedule=False)
        self.assertFalse(self.w.plot_page.add_arrow_btn.isEnabled())


if __name__=='__main__':
    unittest.main()
