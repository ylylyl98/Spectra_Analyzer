import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtTest import QTest


from tests.quick_migration_case import QuickMigrationCase
from tests.dat_properties_quick_helpers import (edit_number, click, state)

from tests.formal_quick_helpers import click_item, edit_field, item, pump, replace_text, input_receiver



class FormalPropertiesQuickTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.host.window.resize(1500, 920)
        self.path = self.folder / 'formal.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
        self.window._load_dat_file_direct(self.path)
        self.window.open_graph_document()
        self.page = self.window.plot_page
        self.window.dat_details_action.setChecked(True)
        self.window.request_task(2)
        self.window._render_plot_page()
        pump(70)

    def tearDown(self):
        self.page.formal._drafts.clear()
        self.page.formal._values.clear()
        self.page.formal._annotation_drafts.clear()
        self.page.formal._annotation_pending.clear()
        self.page.presentation_error = None
        if self.page.formal._annotation_dialog is not None:
            self.page.formal._annotation_dialog.close()
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def draft(self, field, text):
        source = item(self.window, 'formalProperties')
        token = self.page.formal.capturePropertiesInput(source)
        self.assertTrue(token)
        self.page.formal.setDraft(field, text, source, token)
        self.assertEqual(self.page.formal.state['fields'][field], text)

    def canvas(self):
        return self.window.graph_documents.content_item(self.window._document_key())

    def canvas_point(self, annotation):
        point = self.page.preview.annotation_point(annotation)
        paper = item(self.canvas(), 'formalPaper')
        return paper.mapToScene(QPointF(point)).toPoint()

    def focus_canvas(self):
        item(self.canvas(), 'formalViewport').forceActiveFocus()


    def render(self):
        self.window._render_plot_page()
        self.assertIsNotNone(self.page.rendered_page, self.page.status_text)
        return self.page.rendered_page

    def test_real_quick_controls_and_pointer_numeric_error_retain_focus(self):
        p = self.page
        properties = item(self.window, 'formalProperties')
        commands = item(self.window, 'formalCommands')
        self.assertIsInstance(properties, QQuickItem)
        self.assertIsInstance(commands, QQuickItem)
        self.assertIs(properties.property('bridge'), p.formal)
        self.assertIs(commands.property('bridge'), p.formal)
        original = p.presentation.width_in
        control = edit_field(self.window, 'width_in', '-', commit=True)
        self.assertEqual(p.formal.state['fields']['width_in'], '-')
        self.assertTrue(control.hasActiveFocus())
        self.assertIn('finite', p.presentation_error)
        self.assertEqual(p.presentation.width_in, original)
        self.assertFalse(p.command_enabled('export_svg'))
        with self.assertRaises(ValueError):
            p.finish_editor()
        edit_field(self.window, 'width_in', '4.2', commit=True)
        self.assertEqual(p.presentation.width_in, 4.2)
        self.assertFalse(p.has_pending_project_edits())

    def test_simultaneous_drafts_and_reverting_display_preserve_exact_saved_values(self):
        p = self.page
        exact = 4.2034567890123
        self.window.set_plot_presentation(replace(p.presentation, width_in=exact,
            height_in=4.017654321, aspect_locked=False, title_position=(.020000000012, .980000000023)))
        saved = p.presentation
        baseline = p.formal.state['fields']['width_in']
        self.draft('width_in', '5')
        self.draft('width_in', baseline)
        self.assertFalse(p.has_pending_project_edits())
        self.assertEqual(p.presentation, saved)
        self.draft('numeric_pt', '11')
        self.draft('text_pt', '13')
        self.assertEqual(tuple(p.formal.state['fields'][name] for name in ('numeric_pt', 'text_pt')), ('11', '13'))
        p.formal.commitField('numeric_pt')
        self.assertEqual((p.presentation.numeric_pt, p.presentation.text_pt), (11, 13))
        self.assertEqual(p.presentation.width_in, exact)
        self.assertEqual(p.presentation.title_position, saved.title_position)
        p.formal.setValue('bar_show_unit', False)
        self.assertEqual(p.presentation.width_in, exact)

    def test_formal_capture_preserves_simultaneous_native_range_text(self):
        p, w = self.page, self.window
        edit_number(w, 'xmin', '1.15')
        click(w, 'shared_ranges_toggle')  # Collapsing keeps unfinished shared bytes.
        control = edit_field(w, 'width_in', '4.5')
        self.assertEqual(control.property('text'), '4.5')
        self.assertTrue(control.hasActiveFocus())
        self.assertIs(input_receiver(w, control), self.host.window)
        self.assertNotEqual(w.workspace.active.view.bounds['xmin'], 1.15)
        w._finish_graph_editors(capture_science=False)
        self.assertEqual(p.presentation.width_in, 4.5)
        self.assertEqual(w.workspace.active.view.x_limits[0], 1.15)
        self.assertEqual(float(state(w)['xmin']), 1.15)

    def test_canvas_reveal_transfers_actual_keyboard_focus_to_quick_field(self):
        p = self.page
        annotation = p.add_annotation('text', 'before')
        self.render()
        self.focus_canvas()
        self.window._show_plot_property('annotation', annotation.id)
        pump(150)
        control = item(self.window, 'annotation_text')
        self.assertTrue(control.hasActiveFocus())
        self.assertIs(input_receiver(self.window, control), self.host.window)
        replace_text(self.host.window, 'after')
        pump()
        self.assertEqual(p.formal.state['fields']['annotation_text'], 'after')

    def test_dynamic_tick_precision_and_arrow_fields_follow_semantic_choices(self):
        p = self.page
        p.formal.focusRequested.emit('axes', 'axis_x_mode')
        pump(60)
        self.assertFalse(item(self.window, 'axis_x_step').isVisible())
        self.assertFalse(item(self.window, 'axis_x_count').isVisible())
        p.formal.setValue('axis_x_mode', 'By increment')
        self.assertTrue(item(self.window, 'axis_x_step').isVisible())
        self.assertFalse(item(self.window, 'axis_x_count').isVisible())
        p.formal.focusRequested.emit('colorbar', 'bar_label_format')
        pump(60)
        p.formal.setValue('bar_label_format', 'scientific')
        p.formal.setValue('bar_precision', 0)
        self.assertEqual(p.presentation.colorbar.decimal_places, 0)
        self.assertTrue(item(self.window, 'bar_scientific_style').isVisible())
        p.formal.setValue('bar_label_format', 'engineering')
        self.assertFalse(item(self.window, 'bar_scientific_style').isVisible())
        self.render()
        text = p.add_annotation('text', '<b>literal</b>')
        self.render()
        p.reveal_property('annotation', text.id)
        pump(60)
        self.assertFalse(item(self.window, 'annotation_target_x').isVisible())
        arrow = p.add_annotation('arrow', '1')
        self.render()
        p.reveal_property('annotation', arrow.id)
        pump(60)
        self.assertTrue(item(self.window, 'annotation_target_x').isVisible())

    def test_annotation_drafts_apply_together_and_coordinate_failure_rolls_back(self):
        p = self.page
        annotation = p.add_annotation('arrow', 'old')
        self.render()
        self.draft('annotation_text', 'new')
        self.draft('annotation_x', '0.123456789123')
        self.draft('annotation_target_y', '0.62')
        self.assertEqual(tuple(p.formal.state['fields'][name] for name in ('annotation_text', 'annotation_x', 'annotation_target_y')), ('new', '0.123456789123', '0.62'))
        self.assertEqual(p.presentation.annotations[0], annotation)
        self.assertTrue(p.formal.applyAnnotation())
        edited = p.presentation.annotations[0]
        self.assertEqual((edited.text, edited.position[0], edited.target[1]), ('new', .123456789123, .62))
        self.render()
        old_fields = dict(p.formal.state['fields'])
        with patch('spectra_app.formal_plot_bridge.page_to_coordinates', side_effect=ValueError('Cannot convert')):
            p.formal.setValue('annotation_coordinate_space', 'data')
        fields = p.formal.state['fields']
        self.assertEqual(fields['annotation_coordinate_space'], old_fields['annotation_coordinate_space'])
        self.assertEqual(fields['annotation_x'], old_fields['annotation_x'])
        self.assertEqual(p.presentation.annotations[0], edited)
        self.assertIn('Cannot convert', p.status_text)

    def test_custom_three_tick_errors_retain_all_drafts_and_block_capture(self):
        p = self.page
        p.formal.setValue('custom_ticks', True)
        self.assertIn('exactly three', p.presentation_error)
        self.draft('tick_values', '1, 2, 2')
        self.draft('tick_labels', 'one | two | three')
        self.assertFalse(p.formal.commitField('tick_values'))
        self.assertIn('increase', p.presentation_error)
        self.assertEqual(p.formal.state['fields']['tick_labels'], 'one | two | three')
        self.draft('tick_values', '1, 2, 3')
        self.assertTrue(p.formal.commitField('tick_values'))
        ticks = p.presentation.colorbar.ticks['global']
        self.assertEqual((ticks.values, ticks.labels), ((1., 2., 3.), ('one', 'two', 'three')))

    def test_annotation_switch_add_retains_drafts_and_capture_validates_all_atomically(self):
        p = self.page
        first = p.add_annotation('text', 'first')
        self.render()
        self.draft('annotation_text', 'first draft')
        second = p.add_annotation('text', 'second')
        self.assertEqual(p.presentation.annotations[0], first)
        self.draft('annotation_text', 'second draft')
        self.draft('annotation_x', '-')
        p.formal.selectAnnotation(first.id)
        self.assertEqual(p.formal.state['fields']['annotation_text'], 'first draft')
        self.assertEqual(p.presentation.annotations[1], second)
        saved = p.presentation
        with self.assertRaises(ValueError):
            p.finish_editor()
        self.assertEqual(p.presentation, saved)
        self.assertEqual(p.formal.state['selectedAnnotation'], second.id)
        self.assertEqual(p.preview.selected_id, second.id)
        self.assertEqual(p.formal.state['fields']['annotation_x'], '-')
        self.draft('annotation_x', '0.2')
        self.assertTrue(p.formal.applyAnnotation())
        self.assertEqual(p.presentation.annotations[0], first)
        self.assertEqual(p.presentation.annotations[1].text, 'second draft')
        p.finish_editor()
        self.assertEqual(p.presentation.annotations[0].text, 'first draft')
        self.assertFalse(p.has_pending_project_edits())

    def test_direct_annotation_selection_reveals_without_applying_and_delete_prunes_only_its_draft(self):
        p = self.page
        first = p.add_annotation('text', 'first')
        self.render()
        second = p.add_annotation('text', 'second')
        self.render()
        self.window._set_dat_details_requested(True)
        self.window.request_task(2)
        self.draft('annotation_text', 'unfinished second')
        self.focus_canvas()
        p.select_annotation(first.id)
        pump(150)
        self.assertTrue(item(self.window, 'annotation_text').isVisible())
        self.assertTrue(item(self.canvas(), 'formalViewport').hasActiveFocus())
        self.assertEqual(p.presentation.annotations, (first, second))
        p.preview.cancel_interaction()
        p.select_annotation(first.id)
        self.assertEqual(p.preview.selected_id, first.id)
        self.draft('annotation_text', 'unfinished first')
        p.formal.deleteAnnotation()
        self.assertEqual([item.id for item in p.presentation.annotations], [second.id])
        self.assertNotIn(first.id, p.formal._annotation_pending)
        self.assertIn(second.id, p.formal._annotation_pending)
        p.select_annotation(second.id)
        self.assertEqual(p.formal.state['fields']['annotation_text'], 'unfinished second')
        p.finish_editor()
        self.assertEqual(p.presentation.annotations[0].text, 'unfinished second')

    def test_decimal_tie_display_preserves_exact_canonical_values(self):
        p = self.page
        saved = replace(p.presentation, height_in=3.5625, frame_width_in=2.5625,
                        frame_height_in=2.5625)
        self.window.set_plot_presentation(saved)
        self.assertEqual(p.formal.state['fields']['height_in'], '3.563')
        self.assertEqual(p.formal.state['fields']['frame_width_in'], '2.563')
        p.formal.setValue('numeric_pt', 11)
        self.assertEqual(p.presentation.height_in, 3.5625)
        self.assertEqual((p.presentation.frame_width_in, p.presentation.frame_height_in), (2.5625, 2.5625))

    def test_single_canvas_click_keeps_keyboard_nudge_copy_and_delete_target(self):
        p = self.page
        annotation = p.add_annotation('text', 'canvas keys')
        annotation = replace(annotation, position=(.25, .75))
        self.window.set_plot_presentation(replace(p.presentation, annotations=(annotation,)))
        self.render()
        self.window._set_dat_details_requested(True)
        self.window.request_task(2)
        QTest.mouseClick(self.host.window, Qt.LeftButton,
                         pos=self.canvas_point(annotation))
        pump(150)
        self.assertTrue(item(self.canvas(), 'formalViewport').hasActiveFocus())
        before = p.presentation.annotations[0].position
        self.render()
        QTest.keyClick(self.host.window, Qt.Key_Right)
        self.assertNotEqual(p.presentation.annotations[0].position, before)
        # Exercise Copy/Delete after their own single selection, independently
        # of the preceding nudge and render invalidation.
        self.window.set_plot_presentation(replace(p.presentation, annotations=(annotation,)))
        self.render()
        QTest.mouseClick(self.host.window, Qt.LeftButton,
                         pos=self.canvas_point(annotation))
        pump(150)
        self.assertTrue(item(self.canvas(), 'formalViewport').hasActiveFocus())
        copied = []
        p.graphCopyRequested.disconnect()
        p.graphCopyRequested.connect(lambda: copied.append(True))
        QTest.keyClick(self.host.window, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(copied, [True])
        QTest.keyClick(self.host.window, Qt.Key_Delete)
        self.assertEqual(p.presentation.annotations, ())

    def test_real_numeric_keys_step_and_invalid_or_busy_drafts_do_not_change(self):
        p = self.page
        editor = edit_field(self.window, 'title_x', '0.5', commit=True)
        QTest.keyClick(self.host.window, Qt.Key_Up)
        pump()
        self.assertEqual(p.presentation.title_position[0], .51)
        editor = edit_field(self.window, 'width_in', '-', commit=False)
        QTest.keyClick(self.host.window, Qt.Key_Up)
        pump()
        self.assertEqual(p.formal.state['fields']['width_in'], '-')
        self.assertTrue(editor.hasActiveFocus())
        old = p.presentation
        with patch.object(self.window, '_project_busy', return_value=True):
            self.assertFalse(p.formal.stepField('width_in', 1))
        self.assertEqual(p.presentation, old)

    def test_busy_deleted_and_stale_guards_reject_direct_mutation_commands(self):
        p = self.page
        original = p.presentation
        signals = []
        p.exportRequested.connect(signals.append)
        with patch.object(self.window, '_project_busy', return_value=True):
            p.formal.setValue('width_in', 5)
            p.formal.setDraft('height_in', '5')
            p.formal.command('export_svg')
            p.formal.openPrompt('text')
        self.assertEqual(p.presentation, original)
        self.assertFalse(p.has_pending_project_edits())
        self.assertEqual(signals, [])
        old = p.rendered_page
        p.invalidate((*p.expected_owner[:4], p.expected_owner[4]+1, p.expected_owner[5]))
        self.assertFalse(p.commit_rendered_page(old))
        p.formal.command('export_svg')
        self.assertEqual(signals, [])
        self.assertEqual(p.preview.svg_bytes, old.svg_bytes)
        self.assertFalse(p.preview.canEdit)
        self.assertTrue(item(self.canvas(), 'formalViewport').isEnabled())
        self.assertTrue(item(self.canvas(), 'formalPaper').property('source').toString())
        edits = []
        p.preview.propertyEditRequested.connect(lambda *args: edits.append(args))
        left, bottom, width, height = old.object_bounds['horizontal-axis'][0]
        paper_width, paper_height = p.preview.paper_display_size()
        paper = item(self.canvas(), 'formalPaper')
        point = paper.mapToScene(QPointF((left+width/2)*paper_width,
                                        (1-bottom-height/2)*paper_height)).toPoint()
        viewport = item(self.canvas(), 'formalViewport')
        self.assertTrue(viewport.isVisible())
        self.assertTrue(viewport.contains(viewport.mapFromScene(QPointF(point))))
        self.assertTrue(self.canvas().contains(self.canvas().mapFromScene(QPointF(point))))
        QTest.mouseDClick(self.host.window, Qt.LeftButton, pos=point)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=point)
        pump()
        self.assertEqual(edits, [])
        self.assertEqual(p.presentation, original)

    def test_command_pointer_uses_current_exact_bytes_and_annotation_cancel_is_noop(self):
        p = self.page
        page = self.render()
        with patch.object(self.window, '_on_export_plot_page') as unused:
            # Dispatch callbacks were captured at page construction; observe the
            # actual public signal while avoiding a file chooser.
            exports = []
            p.exportRequested.disconnect()
            p.exportRequested.connect(exports.append)
            click_item(self.window, 'formal_export_svg')
        self.assertEqual(exports, ['svg'])
        self.assertEqual(p.rendered_page.svg_bytes, page.svg_bytes)
        p.formal.openPrompt('text')
        dialog = p.formal._annotation_dialog
        pump()
        self.assertIs(p.formal.parent(), p)
        self.assertIs(dialog.parent(), self.host.window)
        self.assertTrue(self.host.window.isAncestorOf(dialog.window(), QWindow.IncludeTransients))
        QTest.keyClick(dialog.window(), Qt.Key_Escape)
        pump()
        self.assertEqual(p.presentation.annotations, ())
        self.assertFalse(p.formal.state['prompt']['open'])


if __name__ == '__main__':
    unittest.main()
