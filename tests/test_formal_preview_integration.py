"""Formal preview through retained production Quick documents and scenes."""
from tests import dat_properties_quick_helpers as dat
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from dataclasses import replace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtGui import QGuiApplication
import shiboken6
from PySide6.QtTest import QTest


from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import application
from spectra_app.formal_preview_controller import FormalPreviewController
from spectra_app.formal_plot_controller import FormalPlotController

from tests.formal_quick_helpers import item, pump, input_receiver, replace_text



class FormalPreviewIntegrationTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller

    def tearDown(self):
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def canvas(self, page=None, owner=None):
        owner = owner or self.window
        page = page or owner.plot_page
        key = next(key for key, controller in owner.graph_documents.cached_items() if controller is page)
        scene = owner.graph_documents.content_item(key)
        self.assertIsInstance(scene, QQuickItem)
        return scene

    def focus_canvas(self, page=None):
        item(self.canvas(page), 'formalViewport').forceActiveFocus()

    def canvas_focused(self, page=None):
        return item(self.canvas(page), 'formalViewport').hasActiveFocus()

    def test_official_entry_uses_quick_preview_without_native_scroll_wrapper(self):
        page = self.open_graph()
        preview = self.canvas(page)
        self.assertIsInstance(preview, QQuickItem)
        self.assertFalse(hasattr(page, 'preview_scroll'))
        self.assertIsInstance(page, FormalPlotController)
        self.assertIs(page.formal.parent(), page)
        self.assertIs(preview.property('bridge'), page.preview)
        self.assertIs(item(self.window, 'formalCommands').property('bridge'), page.formal)
        self.assertIs(item(self.window, 'formalProperties').property('bridge'), page.formal)
        self.assertIsInstance(page.preview, FormalPreviewController)
        self.assertFalse(hasattr(page.preview, 'host'))
        readonly = item(self.window, 'applicationWorkbenchPreview')
        self.assertIs(readonly.property('bridge'), self.window.workbench_preview)
        self.assertIsInstance(self.window.workbench_preview, FormalPreviewController)
        self.assertTrue(self.window.workbench_preview.readOnly)

    def test_adapter_deferred_deletion_detaches_external_controller_without_retiring_document(self):
        p = self.open_graph()
        note = p.add_annotation('text', 'Remount note')
        self.render()
        p.select_annotation(note.id)
        p.set_zoom('150%')
        source = item(self.window, 'formalProperties')
        token = p.formal.capturePropertiesInput(source)
        self.assertTrue(token)
        p.formal.setDraft('width_in', '-', source, token)
        accepted = p.preview.svg_bytes
        documents, key = self.window.graph_documents, self.window._document_key()
        presentation = documents._presentations[key]
        old_mount, old = presentation.mount, presentation.mount.item
        old_attachment = old_mount.scene
        old_route = old_attachment.route
        frame, content = presentation.frame, old.parentItem()
        documents.attachFrame(list(key), frame, content)
        replacement = documents.content_item(key)
        replacement_attachment = presentation.mount.scene
        QGuiApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.assertFalse(shiboken6.isValid(old))
        self.assertTrue(old_attachment._retired)
        self.assertFalse(p.retired)
        self.assertFalse(p.preview._disposed)
        self.assertIs(replacement.property('bridge'), p.preview)
        self.assertNotEqual(replacement_attachment.route, old_route)
        self.assertFalse(replacement_attachment._retired)
        self.assertEqual(p.preview.svg_bytes, accepted)
        self.assertEqual(p.preview.selected_id, note.id)
        self.assertEqual(p.zoom_text, '150%')
        self.assertEqual(p.formal.state['fields']['width_in'], '-')
        with self.assertRaises(ValueError):
            p.finish_editor()
        p.formal._drafts.clear()
        p.presentation_error = None
        documents.discard(key)
        self.assertTrue(p.retired)
        self.assertTrue(replacement_attachment._retired)
        self.assertFalse(p.command_enabled('export_svg'))
        self.assertFalse(p.commit_rendered_page(p.preview.page))

    def test_disabled_parent_initial_state_and_actual_parent_deletion_with_prompt(self):
        page = self.open_graph()
        documents, key = self.window.graph_documents, self.window._document_key()
        frame = documents._presentations[key].frame
        frame.setEnabled(False)
        pump()
        self.assertFalse(page.formal.allowed())
        self.assertFalse(page.preview.canEdit)
        frame.setEnabled(True)
        pump()
        self.assertTrue(page.formal.allowed())
        self.assertTrue(page.preview.canEdit)
        page.formal.openPrompt('text')
        dialog = page.formal._annotation_dialog
        self.assertIsNotNone(dialog)
        rejected = []
        dialog.rejected.connect(lambda: rejected.append(True))
        frame.deleteLater()
        QGuiApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.assertEqual(rejected, [True])
        self.assertFalse(page.retired)
        self.assertFalse(page.formal.allowed())

    def open_graph(self):
        path = self.folder / 'quick-formal.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)), encoding='utf-8')
        w = self.window
        w._load_dat_file_direct(path)
        w.open_graph_document()
        self.host.window.resize(1500, 920)
        self.host.window.requestActivate()
        w.dat_details_action.setChecked(True)
        w.request_task(2)
        pump(50)
        self.render()
        return w.plot_page

    def render(self):
        self.window._render_plot_page()
        pump(30)
        self.assertIsNotNone(self.window.plot_page.rendered_page, self.window.plot_page.status_text)
        return self.window.plot_page.rendered_page

    def reveal(self, preview, point):
        viewport = item(self.canvas(), 'formalViewport')
        for axis, value, extent in (('X', point.x(), viewport.width()), ('Y', point.y(), viewport.height())):
            maximum = max(0., viewport.property('contentWidth' if axis == 'X' else 'contentHeight') - extent)
            viewport.setProperty('content' + axis, min(maximum, max(0., value - extent / 2)))
        pump()
        return item(self.canvas(), 'formalPaper').mapToScene(QPointF(point)).toPoint()

    def capture_preview(self, destination):
        canvas = self.canvas()
        self.assertTrue(canvas.isVisible())
        rectangle = canvas.mapRectToScene(canvas.boundingRect())
        image = self.host.window.grabWindow()
        ratio = image.width() / self.host.window.width()
        cropped = image.copy(round(rectangle.x()*ratio), round(rectangle.y()*ratio),
                             round(rectangle.width()*ratio), round(rectangle.height()*ratio))
        self.assertFalse(cropped.isNull())
        self.assertTrue(cropped.save(str(destination)))

    def note(self, page):
        note = page.add_annotation('text', 'Default note')
        self.render()
        return note

    def test_default_selection_drag_nudge_delete_escape_copy_and_graph_shortcuts(self):
        self.addCleanup(QGuiApplication.clipboard().clear)
        p = self.open_graph()
        note = self.note(p)
        preview = p.preview
        start = self.reveal(preview, preview.annotation_point(note))
        accepted = p.rendered_page
        cube = self.window.current_cube
        with patch('spectra_app.main_window.render_hybrid_svg', side_effect=AssertionError('selection rendered')):
            QTest.mouseClick(self.host.window, Qt.LeftButton, pos=start)
            pump()
            self.assertEqual(preview.selected_id, note.id)
            self.assertEqual(p.presentation.annotations[0].position, (.15, .85))
            self.assertIs(p.rendered_page, accepted)
            self.assertTrue(self.canvas_focused())
        QTest.keyClick(self.host.window, Qt.Key_C, Qt.ControlModifier)
        pump()
        self.assertEqual(bytes(QGuiApplication.clipboard().mimeData().data('image/svg+xml')), p.rendered_page.svg_bytes)
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.host.window, start + QPoint(23, 13))
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=start + QPoint(23, 13))
        moved = p.presentation.annotations[0].position
        self.assertNotEqual(moved, note.position)
        self.assertTrue(self.canvas_focused(), 'A committed gesture must retain canvas keyboard ownership')
        self.render()
        self.assertTrue(self.canvas_focused())
        self.assertEqual(preview.selected_id, note.id)
        QTest.keyClick(self.host.window, Qt.Key_Right)
        self.assertGreater(p.presentation.annotations[0].position[0], moved[0])
        self.render()
        self.assertTrue(self.canvas_focused())
        QTest.keyClick(self.host.window, Qt.Key_C, Qt.ControlModifier)
        pump()
        self.assertEqual(bytes(QGuiApplication.clipboard().mimeData().data('image/svg+xml')), p.rendered_page.svg_bytes)
        self.assertTrue(self.canvas_focused())
        moved = p.presentation.annotations[0].position
        start = self.reveal(preview, preview.annotation_point(p.presentation.annotations[0]))
        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=start)
        QTest.keyClick(self.host.window, Qt.Key_Right, Qt.ShiftModifier)
        nudged = p.presentation.annotations[0].position
        self.assertGreater(nudged[0], moved[0])
        self.render()
        start = self.reveal(preview, preview.annotation_point(p.presentation.annotations[0]))
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.host.window, start + QPoint(25, 10))
        QTest.keyClick(self.host.window, Qt.Key_Escape)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=start + QPoint(25, 10))
        self.assertEqual(p.presentation.annotations[0].position, nudged)
        self.assertIsNone(preview.selected_id)
        self.render()
        self.assertIsNone(preview.selected_id, 'A fresh render must not undo canvas Escape')
        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=start)
        QTest.keyClick(self.host.window, Qt.Key_Delete)
        self.assertEqual(p.presentation.annotations, ())
        self.render()
        self.focus_canvas()
        QTest.keyClick(self.host.window, Qt.Key_Z, Qt.ControlModifier)
        pump()
        self.assertEqual(p.presentation.annotations[0].position, nudged)
        self.render()
        self.focus_canvas()
        QTest.keyClick(self.host.window, Qt.Key_Y, Qt.ControlModifier)
        pump()
        self.assertEqual(p.presentation.annotations, ())
        self.assertIs(self.window.current_cube, cube)

    def test_native_drag_does_not_republish_document_frames_and_commit_is_queued(self):
        from spectra_app import main_window
        w = self.window
        p = self.open_graph()
        note = self.note(p)
        start = self.reveal(p.preview, p.preview.annotation_point(note))
        before = p.presentation
        with patch.object(w.graph_documents.frames, 'refresh', wraps=w.graph_documents.frames.refresh) as refresh, \
             patch.object(main_window, 'prepare_heatmap_page', wraps=main_window.prepare_heatmap_page) as prepare, \
             patch.object(w, '_apply_live_heatmap_style', wraps=w._apply_live_heatmap_style) as style:
            QTest.mousePress(self.host.window, Qt.LeftButton, pos=start)
            for step in range(1, 11):
                QTest.mouseMove(self.host.window, start + QPoint(step * 2, step))
                pump(1)
            self.assertEqual(refresh.call_count, 0)
            self.assertEqual(prepare.call_count, 0)
            self.assertEqual(p.presentation, before)
            QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=start + QPoint(20, 10))
            self.assertNotEqual(p.presentation, before)
            # Native release may already process the queued zero-delay timer.
            self.assertEqual(w._plot_render_timer.interval(), 0)
            pump(30)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(style.call_count, 0)
            self.assertEqual(p.rendered_page.owner_token, w._plot_owner_token())
        self.assertTrue(w.undo_edit())
        self.assertEqual(p.presentation, before)

    def test_committed_properties_coalesce_without_hidden_live_style(self):
        from spectra_app import main_window
        w = self.window
        p = self.open_graph()
        with patch.object(main_window, 'prepare_heatmap_page', wraps=main_window.prepare_heatmap_page) as prepare, \
             patch.object(w, '_apply_live_heatmap_style', wraps=w._apply_live_heatmap_style) as style:
            p.formal.setDraft('text_pt', str(p.presentation.text_pt + 1))
            self.assertTrue(p.formal.commitField('text_pt'))
            p.formal.setDraft('numeric_pt', str(p.presentation.numeric_pt + 1))
            self.assertTrue(p.formal.commitField('numeric_pt'))
            self.assertEqual(prepare.call_count, 0)
            self.assertEqual(style.call_count, 0)
            self.assertEqual(w._plot_render_timer.interval(), 0)
            pump(30)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(style.call_count, 0)
            self.assertEqual(p.rendered_page.owner_token, w._plot_owner_token())
        self.assertTrue(w.request_outer_view('analysis'))
        pump(70)
        self.assertFalse(w._annotation_layout_pending)
        self.assertEqual(w._heat_ax.title.get_fontsize(), w._live_display_presentation()[0].text_pt)

    def test_same_active_document_still_publishes_when_stacking_changes(self):
        w = self.window
        self.open_graph()
        first = w._document_key()
        w.create_graph('Second frame')
        w.open_graph_document()
        self.render()
        documents, active = w.graph_documents, w._document_key()
        documents.setMode('free')
        pump()
        documents.registry.report_stacking((active, first))
        with patch.object(documents.frames, 'refresh', wraps=documents.frames.refresh) as refresh:
            self.assertTrue(documents.requestActivate(active))
            self.assertEqual(documents._stacking()[-1], active)
            self.assertEqual(refresh.call_count, 1)
            self.assertTrue(documents.requestActivate(active))
            self.assertEqual(refresh.call_count, 1)

    def test_graph_shortcuts_stay_local_to_canvas_and_leave_text_editor_undo_local(self):
        p = self.open_graph()
        self.note(p)
        triggered = []
        for name in ('undo', 'redo', 'rescale'):
            self.window._graph_command_actions[name].triggered.connect(
                lambda _checked=False, key=name: triggered.append(key))
        title = dat.reveal(self.window, 'plot_title')
        host = input_receiver(self.window, title)
        QTest.keyClick(host, Qt.Key_A, Qt.ControlModifier)
        original = title.property('text')
        replace_text(host, 'Local text draft')
        QTest.keyClick(host, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(title.property('text'), original)
        self.assertEqual(triggered, [])
        self.assertEqual(len(p.presentation.annotations), 1)
        self.render()
        preview = p.preview
        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=self.reveal(preview, preview.annotation_point(p.presentation.annotations[0])))
        QTest.keyClick(self.host.window, Qt.Key_Z, Qt.ControlModifier)
        pump()
        self.assertEqual(triggered, ['undo'])
        self.render()
        QTest.keyClick(self.host.window, Qt.Key_Y, Qt.ControlModifier)
        pump()
        self.assertEqual(triggered, ['undo', 'redo'])
        self.render()
        QTest.keyClick(self.host.window, Qt.Key_R, Qt.ControlModifier)
        pump()
        self.assertEqual(triggered, ['undo', 'redo', 'rescale'])

    def test_measured_doubleclick_at_200_percent_scroll_reveals_and_focuses_fields(self):
        p = self.open_graph()
        w = self.window
        w.dat_properties.setLabel('plot_title', 'Measured title', w.dat_properties.ownerToken)
        w._finish_graph_editors()
        w.set_plot_presentation(replace(p.presentation, width_in=12., height_in=9., aspect_locked=False))
        self.render()
        p.set_zoom('200%')
        pump()
        observed = []
        p.propertyEditRequested.connect(lambda kind, identifier: observed.append((kind, identifier)))
        for key, expected in (('title', ('title', '')), ('horizontal-axis', ('horizontal-axis', '')),
                              ('colorbar:global', ('colorbar', 'global'))):
            with self.subTest(key=key):
                left, bottom, width, height = p.rendered_page.object_bounds[key][0]
                pw, ph = p.paper_display_size()
                point = self.reveal(p.preview, QPointF((left+width/2)*pw, (1-bottom-height/2)*ph))
                viewport = item(self.canvas(p), 'formalViewport')
                self.assertGreater(viewport.property('contentX'), 0)
                self.assertGreater(viewport.property('contentY'), 0)
                presentation = p.presentation
                history = w._history_for(w.workspace.active)
                saved_history = tuple(history.undo_stack), tuple(history.redo_stack)
                QTest.mouseDClick(self.host.window, Qt.LeftButton, pos=point)
                QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=point)
                pump(50)
                self.assertEqual(p.presentation, presentation)
                self.assertEqual((tuple(history.undo_stack), tuple(history.redo_stack)), saved_history)
                self.assertEqual(observed[-1], expected)
                self.assertFalse(self.canvas_focused())
                if key == 'title':
                    self.assertTrue(dat.dat_item(w, 'plot_title').hasActiveFocus())
                else:
                    field = 'axis_x_mode' if key == 'horizontal-axis' else 'custom_ticks'
                    self.assertTrue(item(w, field).property('activeFocus'))
        capture = os.environ.get('SPECTRA_FORMAL_NATIVE_CAPTURE')
        if capture:
            from pathlib import Path
            output = Path(capture)
            output.mkdir(parents=True, exist_ok=True)
            w.graph_documents.setMode('tabbed')
            w.set_plot_presentation(replace(p.presentation, width_in=6.6, height_in=5.,
                                           frame_width_in=None, frame_height_in=None))
            self.render()
            p.set_zoom('200%')
            pump(100)
            pw, ph = p.paper_display_size()
            self.reveal(p.preview, QPointF(pw*.55, ph*.5))
            self.assertTrue(self.host.window.grabWindow().save(str(output / 'formal-app-200-percent.png')))
            self.capture_preview(output / 'formal-preview-200-percent.png')
            p.set_zoom('Fit')
            pump(100)
            self.assertTrue(self.host.window.grabWindow().save(str(output / 'formal-app-fit.png')))
            self.capture_preview(output / 'formal-preview-fit.png')

    def test_resize_zoom_scroll_invalidation_and_accepted_bytes_keep_host_and_export_authority(self):
        p = self.open_graph()
        note = self.note(p)
        preview, scene = p.preview, self.canvas(p)
        attachment = self.window.graph_documents._presentations[self.window._document_key()].mount.scene
        provider = attachment.provider
        accepted = p.rendered_page
        with patch('spectra_app.main_window.render_hybrid_svg', side_effect=AssertionError('display rendered')):
            p.set_zoom('200%')
            self.host.window.resize(1100, 700)
            pump()
            viewport = item(self.canvas(), 'formalViewport')
            viewport.setProperty('contentX', 25.)
            viewport.setProperty('contentY', 19.)
            pump()
            self.assertIs(p.rendered_page, accepted)
            self.assertEqual(preview.svg_bytes, accepted.svg_bytes)
        exported = self.window.export_plot_page('svg')
        self.assertEqual(exported.read_bytes(), accepted.svg_bytes)
        scroll = viewport.property('contentX'), viewport.property('contentY')
        owner = p.expected_owner
        start = self.reveal(preview, preview.annotation_point(note))
        scroll = viewport.property('contentX'), viewport.property('contentY')
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.host.window, start + QPoint(20, 10))
        canonical = p.presentation
        p.invalidate(owner)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=start + QPoint(20, 10))
        self.assertEqual(p.presentation, canonical)
        self.assertEqual(preview.svg_bytes, accepted.svg_bytes)
        self.assertEqual((viewport.property('contentX'), viewport.property('contentY')), scroll)
        self.assertFalse(p.command_enabled('export_svg'))
        self.assertFalse(preview.canEdit)
        self.assertFalse(p.commit_rendered_page(replace(accepted, owner_token=('stale',))))
        self.assertTrue(p.commit_rendered_page(accepted))
        self.assertIs(p.preview, preview)
        self.assertIs(attachment.provider, provider)
        self.assertIs(self.canvas(p), scene)
        self.assertEqual((viewport.property('contentX'), viewport.property('contentY')), scroll)
        self.assertEqual(preview.svg_bytes, accepted.svg_bytes)
        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=start)
        self.assertEqual(preview.selected_id, note.id)
        other_lifetime = (owner[0], owner[1] + 1, *owner[2:])
        p.invalidate(other_lifetime)
        self.assertIsNone(preview.selected_id)
        self.assertFalse(p.commit_rendered_page(accepted))
        preview.set_geometry(replace(preview.snapshot, activation_epoch=other_lifetime[1]), p.presentation)
        self.assertTrue(p.commit_rendered_page(replace(accepted, owner_token=other_lifetime)))
        self.assertIsNone(preview.selected_id, 'A cloned annotation ID in a new owner is not selected')

    def test_successful_discard_permanently_retires_preview_before_deferred_deletion(self):
        first = self.open_graph()
        note = self.note(first)
        w = self.window
        second_graph = w.create_graph('Second')
        second = w.open_graph_document()
        self.render()
        controller = first.preview
        attachment = w.graph_documents._presentations[(w.workspace.active_dataset_id, 'default')].mount.scene
        accepted = first.rendered_page
        # Retain the discarded QObject until Qt's deferred-delete queue runs.
        w.open_graph_document(w.workspace.active_dataset_id, 'default')
        pump()
        point = controller.annotation_point(note)
        generation = controller.scenePress(attachment.generation, point.x(), point.y())
        self.assertGreaterEqual(generation, 0)
        controller.sceneMove(attachment.generation, generation, point.x()+15, point.y()+10)
        edits = []
        controller.annotationMoved.connect(edits.append)
        key = (w.workspace.active_dataset_id, 'default')
        w.graph_documents.discard(key)
        self.assertTrue(controller._disposed)
        self.assertIsNone(first.rendered_page)
        self.assertFalse(first.command_enabled('export_svg'))
        self.assertFalse(controller.set_page(accepted))
        controller.sceneRelease(attachment.generation, generation, point.x()+30, point.y()+20)
        self.assertEqual(edits, [])
        self.assertIsNone(self.host.engine.imageProvider(attachment.route))
        self.assertFalse(second.preview._disposed)
        self.assertEqual(second.preview.svg_bytes, second.rendered_page.svg_bytes)
        self.assertIs(w.plot_page, second)

    def test_project_owner_and_other_window_with_identical_keys_remain_isolated(self):
        p = self.open_graph()
        self.note(p)
        project = self.window.save_project(self.folder / 'formal.spectra')
        with application() as (other_host, _, errors):
            other = other_host.controller
            other.open_project(project)
            op = other.open_graph_document()
            other_host.window.resize(1500, 920)
            other_host.window.requestActivate()
            other._render_plot_page()
            pump(50)
            self.assertEqual((op.expected_owner[0], op.expected_owner[-1]),
                             (p.expected_owner[0], p.expected_owner[-1]))
            point = op.preview.annotation_point(op.presentation.annotations[0])
            paper = item(self.canvas(op, other), 'formalPaper')
            start = paper.mapToScene(QPointF(point)).toPoint()
            QTest.mouseClick(other_host.window, Qt.LeftButton, pos=start)
            QTest.keyClick(other_host.window, Qt.Key_Right)
            self.assertNotEqual(op.presentation.annotations[0].position, p.presentation.annotations[0].position)
            self.assertEqual(p.presentation.annotations[0].position, (.15, .85))
            self.assertEqual(self.window.plot_page.preview.svg_bytes, p.rendered_page.svg_bytes)
            other.save_project(project)
            self.assertEqual(errors, [])



if __name__ == '__main__':
    unittest.main()
