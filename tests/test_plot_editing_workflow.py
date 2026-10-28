from tests import dat_properties_quick_helpers as dat
from tests import source_navigation_helpers as sn
from tests import quick_migration_case as migration
from tests.quick_application_helpers import application, wait_until
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from spectra_app.common_prompts import PromptAction
from spectra_app.dataset_state import graph_export_stem
from spectra_app.plot_state import active_plot_recipe
from spectra_app.plot_presentation import ColorbarTicks, NormSpec, PlotAnnotation
from spectra_app.plot_templates import FORMAT_TEMPLATES_KEY
from tests.background_helpers import wait_jobs
from tests.formal_quick_helpers import click_item, item as quick_item, pump

class PlotEditingWorkflowTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        self.window_errors = [self.quick_errors]
        self.errors = self.quick_errors
        self.paths = []
        for name in ('a', 'b'):
            path = self.folder / (name + '.dat')
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
            self.paths.append(path)
            self.w._load_dat_file_direct(path)
            self.w.open_graph_document()
        self.a, self.b = self.w.workspace.datasets.values()
        self.w.root_window.resize(1500, 950)
        self.w.request_outer_view('workspace')
        self.w.dat_details_action.setChecked(True)
        self.w.request_task(2)
        self.w.root_window.show()
        pump()

    def tearDown(self):
        for errors in self.window_errors:
            self.assertEqual(errors, [], 'Unexpected production Quick error')

    def window(self):
        host, folder, errors = self.enterContext(application())
        self.window_errors.append(errors)
        return host.controller

    def close_window(self, window):
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            window.close()
            wait_until(lambda: window._closing)
        self.assertFalse(window.root_window.isVisible())

    def canvas(self):
        return self.w.graph_documents.content_item(self.w._document_key())

    def canvas_point(self, annotation, endpoint=False):
        point = self.w.plot_page.preview.annotation_point(annotation, endpoint=endpoint)
        return quick_item(self.canvas(), 'formalPaper').mapToScene(QPointF(point)).toPoint()

    def focus_canvas(self):
        quick_item(self.canvas(), 'formalViewport').forceActiveFocus()

    def formal_value(self, field, value, *, draft=False):
        # Use the real current Properties surface and its captured owner token.
        source = quick_item(self.w, 'formalProperties')
        bridge = self.w.plot_page.formal
        token = bridge.capturePropertiesInput(source)
        self.assertTrue(token)
        method = bridge.setDraft if draft else bridge.setValue
        method(field, value, source, token)

    def apply_annotation(self):
        source = quick_item(self.w, 'formalProperties')
        bridge = self.w.plot_page.formal
        token = bridge.capturePropertiesInput(source)
        self.assertTrue(token)
        return bridge.applyAnnotation(source, token)

    def render(self):
        self.w._render_plot_page()
        self.assertIsNotNone(self.w.plot_page.rendered_page, self.w.plot_page.status_text)
        pump()
        return self.w.plot_page.rendered_page

    def export_path(self, session, index=1, extension='svg'):
        stem = graph_export_stem(session.source_path.stem, session.active_graph_id, session.active_graph.name)
        return self.folder / f'{stem}_export{index:02d}.{extension}'

    def test_named_format_recall_applies_selected_owners_without_losing_ranges_or_results(self):
        w = self.w
        self.assertTrue(hasattr(w, 'save_plot_format'), 'Named plot formats need UI integration')
        w.set_plot_presentation(replace(self.b.presentation, width_in=4.2, aspect_locked=False))
        dat.set_choice(w, 'colormap', 'plasma')
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
        self.assertEqual(dat.state(w)['colormap'], 'plasma')
        self.assertEqual(tuple(w._heat_ax.get_xlim()), self.b.view.x_limits)
        self.assertEqual(tuple(w._heat_mesh.get_clim()), (-51.,151.))
        self.render()
        w.delete_plot_format('Paper')
        self.assertEqual(w.plot_page.formal.state['formats'], [])

    def test_target_checkboxes_preview_and_partial_errors_are_independent_of_navigation(self):
        w = self.w
        self.assertTrue(hasattr(w, 'create_format_dialog'), 'Target checkbox format dialog is missing')
        w.save_plot_format('Paper')
        self.a.view.norm = NormSpec('log')
        self.a.view.color_limits = (-1., 5.)
        # Explicit limits copied to a regional target must fail, independently.
        self.a.view.color_scale_mode = '2'
        sn.reveal(w)
        dialog = w.create_format_dialog('Paper')
        self.assertFalse(quick_item(dialog, 'formatOption_mapping').property('checked'))
        self.assertFalse(quick_item(dialog, 'formatOption_color_limits').property('checked'))
        dialog.show()
        pump()
        click_item(dialog, 'plotTargetsAll')
        self.assertEqual(set(dialog.selected_ids()), {self.a.dataset_id,self.b.dataset_id})
        click_item(dialog, 'formatOption_color_limits')
        self.assertIn('same global or regional', quick_item(dialog, 'formatChanges').property('text'))
        self.assertEqual(w.source_navigation.activeDatasetId, self.b.dataset_id)
        self.assertEqual(quick_item(w, 'sourceDatasets').property('currentIndex'), 1)
        self.assertEqual(w.workspace.active_dataset_id, self.b.dataset_id)
        results = w.apply_plot_format('Paper', dialog.selected_ids(), copy_color_limits=True)
        self.assertFalse(results[0]['success'])
        self.assertTrue(results[1]['success'])
        click_item(dialog, 'plotTargetsClear')
        dialog.close()
        self.assertEqual(dialog.selected_ids(), [])

    def test_malformed_format_preferences_are_reported_and_preserved(self):
        w = self.w
        self.assertTrue(hasattr(w, 'save_plot_format'), 'Format store UI is missing')
        w._annotation_settings.setValue(FORMAT_TEMPLATES_KEY, '{bad')
        with self.assertRaises(ValueError):
            w.save_plot_format('Paper')
        w._refresh_plot_formats()
        self.assertEqual(w._annotation_settings.value(FORMAT_TEMPLATES_KEY), '{bad')
        self.assertIn('malformed', w.plot_page.status_text)
        self.assertTrue(quick_item(w, 'formal_status').isVisible())

    def test_qtimer_batch_freezes_jobs_and_preserves_active_owner_and_png_metadata(self):
        w = self.w
        self.assertTrue(hasattr(w, 'start_plot_batch'), 'Serial GUI batch binding is missing')
        self.render()
        before = (w.workspace.active_dataset_id, w._data_generation, w.plot_page.preview.svg_bytes,
                  dat.state(w)['colormap'], self.b.current_cube.z.copy())
        w.start_plot_batch([self.a.dataset_id,self.b.dataset_id], formats=('svg','png'))
        self.assertTrue(w._plot_batch_timer.isActive())
        # The queued inactive job must retain its captured settings.
        self.a.view.annotations['plot_title'] = 'Later edit'
        QTest.qWait(1400)
        self.assertTrue(w.plot_batch_summary.success, w.plot_batch_report)
        self.assertEqual(w.workspace.active_dataset_id, before[0])
        self.assertEqual(w._data_generation, before[1])
        self.assertEqual(w.plot_page.preview.svg_bytes, before[2])
        self.assertEqual(dat.state(w)['colormap'], before[3])
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
        start=self.canvas_point(annotation)
        end=start+QPoint(24,18)
        QTest.mousePress(w.root_window,Qt.LeftButton,pos=start)
        QTest.mouseMove(w.root_window,end,delay=10)
        self.assertIsNotNone(p.rendered_page, 'Dragging must retain the paper geometry')
        QTest.mouseRelease(w.root_window,Qt.LeftButton,pos=end)
        self.assertNotEqual(self.b.presentation.annotations[0].position,annotation.position)
        self.render()
        self.formal_value('annotation_text', '10T σ⁺', draft=True)
        click_item(w, 'annotation_apply')
        page=self.render()
        self.assertIn('10T',page.svg_bytes.decode())
        self.assertNotIn('editing-handle',page.svg_bytes.decode())
        w._save_current_plot_state()
        other_page = w.open_graph_document(self.a.dataset_id)
        self.assertEqual(len(other_page.formal.state['annotations']),0)
        self.assertIs(w.open_graph_document(self.b.dataset_id), p)
        self.assertEqual(len(p.formal.state['annotations']),1)
        self.focus_canvas()
        QTest.keyClick(w.root_window,Qt.Key_Delete)
        self.assertEqual(self.b.presentation.annotations,())
        self.assertEqual(self.a.presentation.annotations,())

    def test_batch_common_folder_blank_is_rejected_and_results_are_selectable(self):
        from spectra_app.plot_dialogs import BatchExportDialog
        dialog=BatchExportDialog((self.a,self.b),self.b.dataset_id,self.w)
        self.addCleanup(dialog.deleteLater)
        dialog.bridge.setCommonFolder(True)
        with self.assertRaises(ValueError):
            self.w.start_plot_batch(dialog.selected_ids(),common_folder=dialog.destination())
        self.w.start_plot_batch([self.b.dataset_id],formats=('svg',))
        QTest.qWait(700)
        self.assertTrue(hasattr(self.w,'create_plot_batch_results_dialog'), 'Batch details need a selectable report')
        result=self.w.create_plot_batch_results_dialog()
        self.assertIn(self.export_path(self.b).name, result.property('reportText'))
        report = next(control for control in result.findChildren(QQuickItem)
                      if control.inherits('QQuickTextArea'))
        self.assertTrue(report.property('readOnly'))
        self.assertTrue(report.property('selectByMouse'))

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
        p.set_zoom('200%'); pump()
        paper_point=p.preview.annotation_point(old,endpoint=True)
        viewport=quick_item(self.canvas(), 'formalViewport')
        viewport.setProperty('contentX', max(0., paper_point.x()-viewport.width()/2))
        viewport.setProperty('contentY', max(0., paper_point.y()-viewport.height()/2))
        pump()
        start=quick_item(self.canvas(), 'formalPaper').mapToScene(QPointF(paper_point)).toPoint()
        QTest.mousePress(w.root_window,Qt.LeftButton,pos=start)
        QTest.mouseMove(w.root_window,start+QPoint(20,0),delay=10)
        QTest.mouseRelease(w.root_window,Qt.LeftButton,pos=start+QPoint(20,0))
        moved=self.b.presentation.annotations[0]
        self.assertEqual(moved.position,old.position)
        self.assertGreater(moved.target[0],old.target[0])
        self.render(); self.focus_canvas()
        QTest.keyClick(w.root_window,Qt.Key_Right)
        nudged=self.b.presentation.annotations[0]
        self.assertGreater(nudged.position[0],old.position[0])
        self.assertEqual(nudged.target,moved.target)
        w._save_current_plot_state()
        self.close_window(w)
        pump()
        w2=self.window()
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
        self.close_window(self.w)
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
        self.formal_value('annotation_coordinate_space', 'data')
        self.assertTrue(self.apply_annotation())
        self.render()
        item=self.b.presentation.annotations[0]
        self.assertEqual(item.coordinate_space,'data')
        after=p.preview.annotation_point(item)
        self.assertLessEqual((before-after).manhattanLength(),1)
        position=item.position
        p.set_zoom('200%'); pump()
        self.assertEqual(self.b.presentation.annotations[0].position,position)
        self.formal_value('annotation_visible', False)
        self.assertTrue(self.apply_annotation())
        page=self.render()
        self.assertNotIn('annotation-'+item.id,page.svg_bytes.decode())
        self.assertFalse(self.b.presentation.annotations[0].visible)

    def test_invalid_active_tick_edit_cannot_be_saved_or_exported_through_batch(self):
        w=self.w; p=w.plot_page
        self.render()
        self.formal_value('tick_values', 'not three numbers', draft=True)
        self.formal_value('custom_ticks', True)
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
        self.formal_value('annotation_coordinate_space', 'data')
        self.formal_value('annotation_x', '-1', draft=True)
        before=self.b.presentation.annotations[0]
        self.assertFalse(self.apply_annotation())
        self.assertEqual(self.b.presentation.annotations[0],before)
        self.assertIn('positive',p.formal.state['error'])
        pump(60)
        error_editor = quick_item(self.w, p.formal.state['errorField'])
        self.assertTrue(error_editor.isVisible())
        self.assertTrue(error_editor.parentItem().property('invalid'))
        self.assertFalse(self.w.root_window.grabWindow().isNull())  # Paint the actual mounted overlay.

    def test_annotation_add_buttons_require_a_valid_page_and_block_empty_window(self):
        empty=self.window()
        self.assertEqual(empty.contextKind, 'empty')
        # The production empty context does not mount publication editors.
        # No annotation add control may be available before a graph exists.
        self.assertFalse(any(control.isVisible() and control.isEnabled()
                             for control in empty.root_window.findChildren(QQuickItem)
                             if control.objectName() in ('add_text', 'add_arrow')))
        with self.assertRaises(ValueError):
            empty.plot_page.add_annotation('text','No data')
        self.render()
        self.w.plot_page.formal.focusRequested.emit('annotations', 'annotation_text')
        pump(60)
        self.assertTrue(quick_item(self.w, 'add_text').isEnabled())
        self.w._invalidate_plot_page(schedule=False)
        self.assertFalse(quick_item(self.w, 'add_arrow').isEnabled())


if __name__=='__main__':
    unittest.main()
