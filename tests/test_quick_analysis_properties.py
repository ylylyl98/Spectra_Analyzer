"""Publication Properties edits keep their real Analysis input authority."""
from copy import deepcopy
from dataclasses import replace
import os
import sys
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from tests.formal_quick_helpers import click_item, edit_field, ensure_visible, input_receiver, item, pump, replace_text
from tests.quick_migration_case import QuickMigrationCase


class AnalysisPropertiesInputTests(QuickMigrationCase):
    def setUp(self):
        if (sys.platform == 'win32' and self._testMethodName ==
                'test_native_choice_selection_cannot_edit_rebound_owner'):
            self.enterContext(patch.dict(os.environ, QT_QPA_PLATFORM='windows'))
        super().setUp()

    def open_mounted_graph(self, name='properties'):
        w = self.controller
        source = self.folder / (name + '.dat')
        source.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)), encoding='utf-8')
        w._load_dat_file_direct(source)
        page = w.open_graph_document()
        w.dat_details_action.setChecked(True)
        w.request_task(2)
        pump(120)
        w._render_plot_page()
        pump(60)
        key = w._document_key()
        self.assertIs(w.graph_documents.document_for(key), page)
        self.assertIsNotNone(w.graph_documents.content_item(key))
        self.assertTrue(w.graph_documents.content_item(key).isVisible())
        self.assertIsNotNone(page.rendered_page)
        return w, page

    def test_ordinary_analysis_visible_properties_preserve_drafts_and_commit_presentation(self):
        w, page = self.open_mounted_graph()
        session = w.workspace.active
        original_science = session.current_cube.z.copy()
        original_view = deepcopy(session.active_graph.view)
        original_width = page.presentation.width_in
        self.assertTrue(w.request_outer_view('analysis'))
        w.request_task(2)
        pump(60)
        self.assertFalse(w.workbenchActive)
        self.assertIs(w.plot_page, page)
        self.assertFalse(w.graph_documents.content_item(w._document_key()).isVisible())
        properties = item(w, 'formalProperties')
        self.assertTrue(properties.isVisible())
        self.assertTrue(properties.isEnabled())

        control = edit_field(w, 'width_in', '-', commit=True)
        self.assertEqual(page.formal._drafts.get('width_in'), '-')
        self.assertEqual(control.property('text'), '-')
        self.assertTrue(control.hasActiveFocus())
        self.assertIn('finite', page.presentation_error)
        self.assertEqual(page.presentation.width_in, original_width)
        self.assertEqual(session.presentation.width_in, original_width)
        with self.assertRaises(ValueError):
            page.finish_editor()
        self.assertTrue(control.hasActiveFocus())
        # The failed external flush retains this focused input owner. Recovery
        # must work by continued typing without another pointer or focus event.
        receiver = input_receiver(w, control)
        replace_text(receiver, '4.2')
        QTest.keyClick(receiver, Qt.Key_Return)
        pump()
        self.assertTrue(control.hasActiveFocus())
        self.assertEqual(page.presentation.width_in, 4.2)
        self.assertEqual(session.presentation.width_in, 4.2)
        self.assertFalse(page.has_pending_project_edits())
        self.assertIsNone(page.presentation_error)
        # A commit advances the existing input generation. Continued typing on
        # this exact focus owner must acquire the result of its own edit.
        receiver = input_receiver(w, control)
        replace_text(receiver, '4.4')
        QTest.keyClick(receiver, Qt.Key_Return)
        pump()
        self.assertTrue(control.hasActiveFocus())
        self.assertEqual(page.presentation.width_in, 4.4)
        self.assertEqual(session.presentation.width_in, 4.4)
        self.assertEqual(session.active_graph.view, original_view)
        np.testing.assert_array_equal(session.current_cube.z, original_science)
        self.assertEqual(self.quick_errors, [])

    def test_floating_properties_edit_does_not_authorize_hidden_preview_or_commands(self):
        w, page = self.open_mounted_graph()
        self.assertTrue(w.request_outer_view('analysis'))
        w.request_task(2)
        self.host.moveProperties(True)
        pump(90)
        self.assertTrue(self.host._floating_properties.isVisible())
        control = edit_field(w, 'numeric_pt', '12', commit=True)
        self.assertIs(input_receiver(w, control), self.host._floating_properties)
        self.assertEqual(w.workspace.active.presentation.numeric_pt, 12)
        self.assertFalse(page.formal.allowed())
        saved = page.presentation
        requests = []
        page.formatSaveRequested.connect(lambda: requests.append('save'))
        page.formal.command('format_save')
        page.formal.setDraft('width_in', '9')
        generation = page.preview._scene_generation
        self.assertEqual(page.preview.scenePress(generation, 30., 30.), -1)
        self.assertEqual(requests, [])
        self.assertEqual(page.presentation, saved)
        self.assertEqual(page.formal._drafts, {})
        self.assertEqual(self.quick_errors, [])

    def test_retained_properties_press_cannot_edit_rebound_owner(self):
        w, first = self.open_mounted_graph()
        self.assertTrue(w.request_outer_view('analysis'))
        w.request_task(2)
        first.formal.focusRequested.emit('page', 'aspect_locked')
        pump(70)
        toggle = ensure_visible(item(w, 'aspect_locked'))
        source = item(w, 'formalProperties')
        receiver = input_receiver(w, toggle)
        point = toggle.mapToScene(QPointF(toggle.width()/2, toggle.height()/2)).toPoint()
        before = first.presentation
        QTest.mousePress(receiver, Qt.LeftButton, pos=point)
        pump()
        queued = item(w, 'height_in')
        queued_owner = queued.property('inputOwner')
        first.formal.focusRequested.emit('page', 'height_in')
        _, second = self.open_mounted_graph('second')
        self.assertTrue(w.request_outer_view('analysis'))
        w.request_task(2)
        pump(70)
        self.assertIs(item(w, 'formalProperties'), source)
        self.assertIsNot(first, second)
        self.assertFalse(queued.hasActiveFocus())
        self.assertEqual(queued.property('inputOwner'), queued_owner)
        second_before = second.presentation
        QTest.mouseRelease(receiver, Qt.LeftButton, pos=point)
        pump()
        self.assertEqual(first.presentation, before)
        self.assertEqual(second.presentation, second_before)
        self.assertEqual(self.quick_errors, [])

    def test_properties_placement_change_retires_captured_press(self):
        w, page = self.open_mounted_graph()
        self.assertTrue(w.request_outer_view('analysis'))
        w.request_task(2)
        page.formal.focusRequested.emit('page', 'aspect_locked')
        pump(70)
        source = item(w, 'formalProperties')
        toggle = ensure_visible(item(w, 'aspect_locked'))
        receiver = input_receiver(w, toggle)
        point = toggle.mapToScene(QPointF(toggle.width()/2, toggle.height()/2)).toPoint()
        before = page.presentation
        old_token = page.formal.capturePropertiesInput(source)
        queued = item(w, 'height_in')
        queued_owner = queued.property('inputOwner')
        QTest.mousePress(receiver, Qt.LeftButton, pos=point)
        page.formal.focusRequested.emit('page', 'height_in')
        self.host.moveProperties(True)
        pump(70)
        self.assertFalse(queued.hasActiveFocus())
        self.assertEqual(queued.property('inputOwner'), queued_owner)
        new_token = page.formal.capturePropertiesInput(source)
        self.assertTrue(new_token)
        self.assertNotEqual(new_token, old_token)
        QTest.mouseRelease(receiver, Qt.LeftButton, pos=point)
        page.formal.setDraft('width_in', '8', source, old_token)
        self.assertEqual(page.presentation, before)
        self.assertEqual(page.formal._drafts, {})
        self.assertEqual(self.quick_errors, [])

    def test_analysis_choices_and_annotation_buttons_use_properties_authority(self):
        w, page = self.open_mounted_graph()
        self.assertTrue(w.request_outer_view('analysis'))
        w.request_task(2)
        page.formal.focusRequested.emit('colorbar', 'bar_label_format')
        pump(70)
        choice = ensure_visible(item(w, 'bar_label_format'))
        receiver = input_receiver(w, choice)
        self.assertTrue(choice.hasActiveFocus())
        QTest.keyClick(receiver, Qt.Key_End)
        pump()
        self.assertEqual(page.presentation.colorbar.label_format, 'engineering')
        self.assertEqual(w.workspace.active.presentation.colorbar.label_format, 'engineering')
        w._render_plot_page()
        self.assertIsNotNone(page.rendered_page)
        page.formal.focusRequested.emit('annotations', 'add_text')
        pump(70)
        click_item(w, 'add_text')
        dialog = page.formal._annotation_dialog
        self.assertIsNotNone(dialog)
        prompt = item(dialog, 'annotation_prompt_text')
        prompt.forceActiveFocus()
        replace_text(dialog.window(), 'Analysis annotation')
        click_item(dialog, 'annotation_prompt_accept')
        self.assertEqual([a.text for a in page.presentation.annotations], ['Analysis annotation'])
        self.assertEqual(w.workspace.active.presentation.annotations, page.presentation.annotations)
        click_item(w, 'annotation_delete')
        self.assertEqual(page.presentation.annotations, ())
        self.assertEqual(w.workspace.active.presentation.annotations, ())
        self.assertEqual(self.quick_errors, [])

    def test_native_choice_selection_cannot_edit_rebound_owner(self):
        w, first = self.open_mounted_graph()
        first_id = w.workspace.active_dataset_id
        _, second = self.open_mounted_graph('second-choice')
        second_id = w.workspace.active_dataset_id
        w.activate_dataset(first_id)
        self.assertTrue(w.request_outer_view('analysis'))
        w.request_task(2)
        first.formal.focusRequested.emit('colorbar', 'bar_label_format')
        pump(70)
        choice = ensure_visible(item(w, 'bar_label_format'))
        source = item(w, 'formalProperties')
        receiver = input_receiver(w, choice)
        point = choice.mapToScene(QPointF(choice.width()/2, choice.height()/2)).toPoint()
        QTest.mouseClick(receiver, Qt.LeftButton, pos=point)
        pump()
        popup = next(c for c in choice.findChildren(QObject) if c.inherits('QQuickPopup'))
        self.assertTrue(popup.property('visible'))
        self.assertTrue(popup.property('opened'))
        before = first.presentation
        activations = []
        events = []
        first.presentationChanged.connect(lambda value: events.append('source-published'))
        choice.activated.connect(activations.append)
        QTest.keyClick(receiver, Qt.Key_End)
        pump()
        self.assertTrue(popup.property('visible'))
        self.assertEqual(choice.property('highlightedIndex'), 3)
        def rebind_on_popup_close():
            events.append('closing')
            w.activate_dataset(second_id, capture_outgoing=False)
            events.append('rebound')

        popup.aboutToHide.connect(rebind_on_popup_close)
        second_before = second.presentation
        QTest.keyClick(receiver, Qt.Key_Return)
        pump(70)
        self.assertIs(item(w, 'formalProperties'), source)
        self.assertIsNot(first, second)
        self.assertIs(w.plot_page, second)
        self.assertFalse(popup.property('visible'))
        self.assertEqual(activations, [3], 'The native selection must exercise the real activated callback')
        # Qt delivers this activation before closing the popup. The selection
        # therefore belongs to the original owner; closing then rebinds it.
        self.assertLess(events.index('source-published'), events.index('closing'))
        self.assertLess(events.index('closing'), events.index('rebound'))
        self.assertEqual(first.presentation,
                         replace(before, colorbar=replace(before.colorbar, label_format='engineering')))
        self.assertEqual(second.presentation, second_before)
        self.assertEqual(self.quick_errors, [])

    def test_properties_tokens_reject_hidden_busy_retired_and_readonly_workbench(self):
        w, page = self.open_mounted_graph()
        self.assertTrue(w.request_outer_view('analysis'))
        w.request_task(2)
        pump(70)
        source = item(w, 'formalProperties')
        token = page.formal.capturePropertiesInput(source)
        self.assertTrue(token)
        page.formal.setDraft('width_in', '8', source, '')
        page.formal.setDraft('width_in', '8', None, token)
        page.formal.setDraft('width_in', '8', item(w, 'width_in'), token)
        self.assertEqual(page.formal._drafts, {})
        with patch.object(w, '_project_busy', return_value=True):
            page.formal.setDraft('width_in', '8', source, token)
            self.assertEqual(page.formal._drafts, {})
        w.dat_details_action.setChecked(False)
        pump()
        page.formal.setDraft('width_in', '8', source, token)
        self.assertEqual(page.formal._drafts, {})
        self.assertEqual(page.formal.capturePropertiesInput(source), '')
        w.dat_details_action.setChecked(True)
        pump()
        page.invalidate(page.expected_owner)
        page.formal.setDraft('width_in', '8', source, token)
        self.assertEqual(page.formal._drafts, {})
        source_key = w._document_key()
        w._open_analysis(('heatmap', source_key))
        w.request_task(2)
        pump(70)
        self.assertTrue(w.workbenchActive)
        self.assertFalse(source.isVisible())
        self.assertEqual(w.plot_page.formal.capturePropertiesInput(source), '')
        result_page = w.plot_page
        before = result_page.presentation
        result_page.formal.setValue('width_in', 8)
        self.assertEqual(result_page.presentation, before)
        self.assertEqual(self.quick_errors, [])
