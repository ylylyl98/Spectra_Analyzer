"""Transaction contracts through fresh, actual production QObject/Quick roots.

Each case exits its process after QML/scene teardown.  The parent requires the
real exit status and empty stderr, so a PASS before a teardown crash is failure.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest

CASES = ('mixed_roundtrip', 'repeat_install_rollback', 'hidden_invalid_editor',
         'save_before_new_rechecks', 'committed_save_cancel', 'close_job_drain',
         'analysis_workspace_owners', 'normal_geometry_roundtrip', 'new_project_resets_layout',
         'hidden_analysis_editor', 'hidden_formal_editor', 'xy_panel_release_lifetime',
         'named_install_rollback')


class QuickApplicationTransactionTests(unittest.TestCase):
    def run_case(self, name):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--case', name],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'), timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn(name + ': PASS after Quick teardown', result.stdout)


def _wrapper(name):
    def test(self):
        self.run_case(name)
    return test


for _name in CASES:
    setattr(QuickApplicationTransactionTests, 'test_' + _name, _wrapper(_name))


class ActualTransactions(unittest.TestCase):
    def setUp(self):
        from tests.quick_application_helpers import application
        self.fixture = application()
        self.host, self.folder, self.errors = self.fixture.__enter__()
        self.w = self.host.controller
        self.addCleanup(self.fixture.__exit__, None, None, None)
        self.source = self.folder / '测量.csv'
        self.source.write_text('X,Y\n0,10\n1,20\n2,30\n', encoding='utf-8')

    def idle(self):
        from tests.quick_application_helpers import wait_until
        wait_until(lambda: not self.w._project_io_jobs.busy and not self.w._background_jobs.busy)

    def dat(self, name='光谱'):
        from tests.quick_application_helpers import pump
        path = self.folder / (name + '.dat')
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
        self.w._load_dat_file_direct(path)
        pump()
        return path, self.w.workspace.active

    def book(self):
        from tests.quick_application_helpers import pump
        book = self.w.import_xy_files([self.source])
        pump()
        key = ('workbook', book.workbook_id, '')
        self.assertIsNotNone(self.w.graph_documents.content_item(key))
        return book

    def type_text(self, editor, text, window=None):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from tests.quick_application_helpers import pump
        target = self.host.window if window is None else window
        editor.forceActiveFocus()
        pump()
        if window is not None:
            point = editor.mapToScene(QPointF(editor.width()/2, editor.height()/2)).toPoint()
            QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, point)
        QTest.keyClick(target, Qt.Key_A, Qt.ControlModifier)
        for character in text:
            QTest.keyClick(target, Qt.Key(ord(character.upper())),
                Qt.ShiftModifier if character.isupper() else Qt.NoModifier)
        pump()
        self.assertEqual(editor.property('text'), text)

    def case_hidden_analysis_editor(self):
        from tests.quick_application_helpers import item, pump
        w = self.w
        _, session = self.dat()
        w.open_graph_document()
        key = w._document_key()
        before = session.analysis_controls.prominence
        w.request_task(1)
        pump()
        self.type_text(item(self.host, 'ordinaryField_prominence'), '-')
        w.request_task(0)
        pump()
        self.assertFalse(w.graph_documents.close_document(key))
        pump(120)
        self.assertEqual(w.taskIndex, 1)
        self.assertEqual(self.host.window.activeFocusItem().objectName(), 'ordinaryField_prominence')
        self.assertEqual(w.ordinary_analysis.state['texts']['prominence'], '-')
        self.assertEqual(session.analysis_controls.prominence, before)
        self.assertFalse(self.errors)

    def case_hidden_formal_editor(self):
        from tests.quick_application_helpers import item, pump
        w = self.w
        _, session = self.dat()
        page = w.open_graph_document()
        key = w._document_key()
        w.request_task(2)
        pump()
        page.formal.focusRequested.emit('page', 'width_in')
        pump(100)
        self.type_text(item(self.host, 'width_in'), '-')
        before = session.presentation.width_in
        w.request_task(0)
        pump()
        self.assertFalse(w.graph_documents.close_document(key))
        pump(120)
        self.assertEqual(w.taskIndex, 2)
        self.assertEqual(self.host.window.activeFocusItem().objectName(), 'width_in')
        self.assertEqual(page.formal.state['fields']['width_in'], '-')
        self.assertEqual(session.presentation.width_in, before)
        self.assertFalse(self.errors)

    def case_xy_panel_release_lifetime(self):
        from unittest.mock import patch
        from PySide6.QtQuick import QQuickWindow
        import shiboken6
        from spectra_app.workbook_state import create_comparison, add_curve
        from tests.quick_application_helpers import item, pump
        w = self.w
        book = self.book()
        graph = create_comparison(w.workspace, 'Inspector lifetime')
        add_curve(w.workspace, graph.comparison_id, book.sheet_ids[0], 0, 1)
        xy = w.open_xy_comparison(graph.comparison_id)
        pump()
        floating = self.host.window.findChild(QQuickWindow, 'floatingPropertiesWindow')
        for floated in (False, True):
            self.assertTrue(shiboken6.isValid(floating), f'before float={floated}')
            w.floatProperties(floated)
            w.reveal_xy_inspector(xy, 2, 'plot_title')
            pump(100)
            window = floating if floated else self.host.window
            title = window.activeFocusItem()
            self.assertEqual(title.objectName(), 'xyField_plot_title')
            expected = 'measured title ' + str(floated).lower()
            self.type_text(title, expected, window)
            with patch.object(xy.inspector_bridge, '_panel_focused', wraps=xy.inspector_bridge._panel_focused) as focused:
                xy.inspector_bridge.focusFieldRequested.emit('x_label')
                pump(100)
                self.assertTrue(focused.called)
            self.assertEqual(graph.plot_title, expected,
                (xy.inspector_bridge.inputOwner, xy.inspector_bridge._drafts,
                 xy.inspector_bridge.error, window.activeFocusItem().objectName()))
            w.selectInspectorSection(0)
            pump(100)
            self.assertTrue(shiboken6.isValid(self.host.window), f'section switch float={floated}')
            self.assertTrue(shiboken6.isValid(floating), f'section switch float={floated}')
            w.request_task(1)
            pump()
            self.assertTrue(shiboken6.isValid(self.host.window), f'task switch float={floated}')
            self.assertTrue(shiboken6.isValid(floating), f'task switch float={floated}')
        self.assertFalse(self.errors)

    def case_mixed_roundtrip(self):
        from dataclasses import replace
        from unittest.mock import patch
        import numpy as np
        from spectra_app.workbook_state import add_curve, create_comparison, resolve_curve
        from tests.quick_application_helpers import pump
        w = self.w
        source, session = self.dat()
        session.current_cube = replace(session.base_cube, z=-session.base_cube.z)
        session.applied.mode = 'energy_second_derivative'
        session.applied.original_signal_title = 'Intensity'
        session.pending = replace(session.applied, mode='original')
        session.fixed_recipes['processing'].enabled = True
        session.fixed_recipes['processing'].recipe = session.applied.recipe()
        w.restore_dataset_controls(session)
        w._plot_cube()
        formal = w.open_graph_document()
        w._render_plot_page()
        book = self.book()
        graph = create_comparison(w.workspace, 'Cross-file')
        curve = add_curve(w.workspace, graph.comparison_id, book.sheet_ids[0], 0, 1, label='Sample A', color='#aabbcc', width=2)
        xy = w.open_xy_comparison(graph.comparison_id)
        pump()
        self.assertIsNotNone(w.graph_documents.content_item(('xy', graph.comparison_id, '')))
        original = session.current_cube.z.copy()
        path = w.save_project(self.folder / 'mixed.spectra')
        self.assertFalse(w.projectModified)
        self.source.unlink()
        source.unlink()
        with patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('source reread')), patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('science recompute')):
            self.assertTrue(w.open_project(path))
        pump()
        restored = w.workspace.datasets[session.dataset_id]
        np.testing.assert_array_equal(restored.current_cube.z, original)
        self.assertEqual(restored.applied.mode, 'energy_second_derivative')
        self.assertEqual(restored.pending.mode, 'original')
        self.assertEqual(restored.fixed_recipes, session.fixed_recipes)
        self.assertIsNot(restored.current_cube, restored.base_cube)
        actual = w.workspace.comparisons[graph.comparison_id].curves[curve.curve_id]
        x, y = resolve_curve(w.workspace, actual)
        np.testing.assert_array_equal(x, [0, 1, 2])
        np.testing.assert_array_equal(y, [10, 20, 30])
        self.assertEqual((actual.label, actual.color, actual.width), ('Sample A', '#aabbcc', 2))
        self.assertIsNot(w.graph_documents.document_for(('xy', graph.comparison_id, '')), xy)
        w._refresh_project_modified()
        self.assertFalse(w.projectModified)
        self.assertFalse(self.errors)

    def case_repeat_install_rollback(self):
        from unittest.mock import patch
        from copy import deepcopy
        from spectra_app.edit_history import EditCommand, FieldChange
        from spectra_app.project_io import read_project
        from tests.quick_application_helpers import pump
        w = self.w
        _, session = self.dat()
        first = w.open_graph_document()
        first_key = w._document_key()
        graph = w.create_graph('Closed detail')
        second = w.open_graph_document()
        second_key = w._document_key()
        w.graph_documents.close_document(second_key)
        w.open_graph_document(*first_key)
        path = w.save_project(self.folder / 'rollback.spectra')
        history = w._history_for(session)
        command = EditCommand('prior visual edit', (FieldChange(('view', 'annotations', 'plot_title'), 'before', 'after'),))
        history.record(command)
        # Runtime-only invalid drafts must survive a failed candidate install.
        w.dat_properties.draftNumber('xmin', '-', w.dat_properties.ownerToken)
        self.assertEqual(w.dat_properties.state['xmin'], '-')
        w.dat_properties.setTemplate('title_pattern', 'Unfinished {', w.dat_properties.ownerToken)
        w.dat_properties.setDisclosure('colors', True, w.dat_properties.ownerToken)
        with self.assertRaisesRegex(ValueError, 'pending xmin editor'):
            w.dat_properties.validate_editor()
        w.request_task(0)
        w.processing.setWindowDraft('invalid')
        before_drafts = deepcopy(w.processing.snapshot_editor_drafts())
        before_dat_editors = w.dat_properties.snapshot_editor_state()
        self.assertTrue(w.analysis_header.setDraft('gate', '-', w.analysis_header.ownerToken))
        self.assertTrue(w.ordinary_analysis.setDraft('prominence', '-', w.ordinary_analysis.ownerToken))
        with self.assertRaises(ValueError):
            w.analysis_header.validate_editor()
        with self.assertRaises(ValueError):
            w.ordinary_analysis.validate_editor()
        header_error = (w.analysis_header.errorField, w.analysis_header.errorText)
        ordinary_error = (w.ordinary_analysis.errorField, w.ordinary_analysis.errorText)
        w.request_task(0)
        before_task = w.taskIndex
        previous, baseline = w.workspace, w._project_saved_digest
        layout = deepcopy(w._graph_workspace_snapshot())
        for _ in range(2):
            with patch.object(w, 'activate_dataset', side_effect=RuntimeError('projection failure')):
                with self.assertRaisesRegex(RuntimeError, 'projection failure'):
                    w._install_project(read_project(path), path)
            pump()
            self.assertIs(w.workspace, previous)
            self.assertIs(w._history_for(session), history)
            self.assertEqual(history.undo_stack, [command])
            self.assertEqual(w.processing.snapshot_editor_drafts(), before_drafts)
            self.assertIs(w.graph_documents.document_for(first_key), first)
            self.assertIs(w.graph_documents.document_for(second_key), second)
            self.assertEqual(w._graph_workspace_snapshot(), layout)
            self.assertEqual(w.dat_properties.state['xmin'], '-')
            self.assertEqual(w.dat_properties.snapshot_editor_state(), before_dat_editors)
            self.assertEqual(w.taskIndex, before_task)
            self.assertEqual(w.analysis_header.state['gateText'], '-')
            self.assertEqual(w.ordinary_analysis.state['texts']['prominence'], '-')
            self.assertEqual((w.analysis_header.errorField, w.analysis_header.errorText), header_error)
            self.assertEqual((w.ordinary_analysis.errorField, w.ordinary_analysis.errorText), ordinary_error)
            self.assertEqual((w._project_path, w._project_saved_digest), (path, baseline))
            self.assertFalse(w._project_installing)
        self.assertFalse(self.errors)

    def case_named_install_rollback(self):
        from unittest.mock import patch
        from copy import deepcopy
        from spectra_app.project_io import read_project
        from tests.quick_application_helpers import pump
        w = self.w
        self.dat()
        w.open_graph_document()
        source_key = w._document_key()
        w._workbench_open(('heatmap', source_key))
        pump()
        self.assertTrue(w.workbenchActive)
        workspace, document = w.workspace, w._workbench_document()
        path = w.save_project(self.folder / 'named.spectra')
        w.analysis_workbench.draftNumber('xmin', '-', w.analysis_workbench.ownerToken)
        with self.assertRaisesRegex(ValueError, 'pending Analysis numeric editor'):
            w.analysis_workbench.validate_editor()
        owner = w.analysis_workbench.ownerToken
        error = (w.analysis_workbench.errorField, w.analysis_workbench.errorText)
        filter_draft = deepcopy(w.analysis_workbench.state['filter'])
        with patch.object(w, 'activate_dataset', side_effect=RuntimeError('named projection failure')):
            with self.assertRaisesRegex(RuntimeError, 'named projection failure'):
                w._install_project(read_project(path), path)
        pump()
        self.assertIs(w.workspace, workspace)
        self.assertIs(w._workbench_document(), document)
        self.assertEqual(w.analysis_workbench.ownerToken, owner)
        self.assertEqual(w.analysis_workbench.state['xmin'], '-')
        self.assertEqual(w.analysis_workbench.state['filter'], filter_draft)
        self.assertEqual((w.analysis_workbench.errorField, w.analysis_workbench.errorText), error)
        self.assertEqual((w.outerView, w.taskIndex), ('analysis', 1))
        self.assertFalse(self.errors)

    def case_hidden_invalid_editor(self):
        from unittest.mock import patch
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from spectra_app.common_prompts import PromptAction
        from tests.quick_application_helpers import item, pump, wait_until
        w = self.w
        _, session = self.dat()
        w.open_graph_document()
        key = w._document_key()
        path = w.save_project(self.folder / 'valid.spectra')
        prior = path.read_bytes()
        w.request_task(2)
        pump()
        w.dat_properties.focusRequested.emit('ranges', 'xmin')
        pump(100)
        editor = self.host.window.activeFocusItem()
        self.assertEqual(editor.objectName(), 'xmin')
        QTest.keyClick(self.host.window, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(self.host.window, Qt.Key_Minus)
        pump()
        self.assertEqual(editor.property('text'), '-')
        w.request_task(0)  # Destroy the view Loader while its invalid draft remains.
        pump()
        self.assertFalse(w.graph_documents.close_document(key))
        pump(120)
        self.assertEqual(w.taskIndex, 2)
        self.assertEqual(self.host.window.activeFocusItem().objectName(), 'xmin')
        with self.assertRaisesRegex(ValueError, 'pending xmin editor'):
            w.save_project(self.folder / 'invalid.spectra')
        self.assertEqual(path.read_bytes(), prior)
        self.assertFalse((self.folder / 'invalid.spectra').exists())
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL):
            self.host.window.close()
            self.idle()
            self.assertTrue(self.host.window.isVisible())
            self.assertIn(key, w.graph_documents.registry.open_keys())
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            self.host.window.close()
            wait_until(lambda: w._closing)
        self.assertFalse(self.host.window.isVisible())

    def case_save_before_new_rechecks(self):
        from unittest.mock import patch
        import threading
        from spectra_app.common_prompts import PromptAction
        from spectra_app.project_io import read_project, write_project
        from tests.quick_application_helpers import wait_until
        w = self.w
        book = self.book()
        sheet = w.workspace.sheets[book.sheet_ids[0]]
        previous = w.workspace
        path = self.folder / 'before-new.spectra'
        started, release = threading.Event(), threading.Event()
        def held_write(*args, **kwargs):
            started.set()
            assert release.wait(8)
            return write_project(*args, **kwargs)
        try:
            with patch('spectra_app.project_io.write_project', side_effect=held_write), patch('spectra_app.main_window.FilePicker.getSaveFileName', return_value=(str(path), '')), patch('spectra_app.main_window.prompt_message', side_effect=[PromptAction.SAVE, PromptAction.CANCEL]) as prompt:
                w._on_new_project()
                wait_until(started.is_set)
                sheet.name = 'Edited while saving'
                w._schedule_project_modified()
                release.set()
                self.idle()
                self.assertEqual(prompt.call_count, 2)
        finally:
            release.set()
        self.assertIs(w.workspace, previous)
        self.assertTrue(w.projectModified)
        self.assertNotEqual(read_project(path).sheets[sheet.sheet_id].name, sheet.name)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            w._on_new_project()
            self.idle()
        self.assertIsNot(w.workspace, previous)
        self.assertFalse(w.workspace.workbooks)
        import shiboken6
        self.assertTrue(shiboken6.isValid(self.host.window), 'Actual New Project deleted the production root QQuickWindow')
        self.assertTrue(shiboken6.isValid(w._project_modified_timer))
        self.assertTrue(self.host.window.isVisible())
        self.assertFalse(self.errors)

    def case_committed_save_cancel(self):
        from unittest.mock import patch
        import threading
        from spectra_app.project_io import read_project
        from tests.quick_application_helpers import wait_until
        w = self.w
        book = self.book()
        path = self.folder / 'committed.spectra'
        committed, release = threading.Event(), threading.Event()
        continued = []
        real_replace = os.replace
        def held_replace(*args):
            real_replace(*args)
            committed.set()
            assert release.wait(8)
        try:
            with patch('spectra_app.project_io.os.replace', side_effect=held_replace):
                w._save_project_async(path, on_saved=lambda: continued.append(True))
                wait_until(committed.is_set)
                w._cancel_background_job()
                release.set()
                self.idle()
        finally:
            release.set()
        self.assertIn(book.workbook_id, read_project(path).workbooks)
        self.assertEqual(w._project_path, path)
        self.assertIsNotNone(w._project_saved_digest)
        self.assertFalse(w.projectModified)
        self.assertIn('after the file was saved', w.statusText)
        self.assertFalse(continued)
        self.assertFalse(self.errors)

    def case_close_job_drain(self):
        from unittest.mock import patch
        import threading
        from spectra_app.common_prompts import PromptAction
        from tests.quick_application_helpers import wait_until, pump
        w = self.w
        self.book()
        release = threading.Event()
        started = threading.Event()
        def held(context):
            started.set()
            release.wait(8)
            context.check_cancelled()
        try:
            w._start_background_job(held, lambda result: None, 'held actual job')
            wait_until(started.is_set)
            with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as prompt:
                self.host.window.close()
                self.assertTrue(w._close_pending)
                release.set()
                self.idle()
                self.assertEqual(prompt.call_count, 1)
                self.assertFalse(w._close_pending)
                self.assertTrue(self.host.window.isVisible())
                w._start_background_job(lambda context: 7, lambda result: None, 'later job')
                self.idle()
                self.assertEqual(prompt.call_count, 1)
                self.assertTrue(self.host.window.isVisible())
            # A close approval belongs to exactly one workspace revision.
            w._project_close_approved = (w.workspace, w._project_revision)
            w._schedule_project_modified()
            with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as prompt:
                self.host.window.close()
                self.idle()
                self.assertEqual(prompt.call_count, 1)
                self.assertTrue(self.host.window.isVisible())
                self.assertFalse(w._project_close_approved)
        finally:
            release.set()
        self.assertFalse(self.errors)

    def case_analysis_workspace_owners(self):
        from tests.quick_application_helpers import pump
        w = self.w
        _, first = self.dat('first')
        formal = w.open_graph_document()
        source_key = w._document_key()
        _, second = self.dat('second')
        self.assertEqual(w.workspace.active_dataset_id, second.dataset_id)
        w.open_graph_document(*source_key)
        self.assertEqual(w.workspace.active_dataset_id, first.dataset_id)
        book = self.book()
        book_key = ('workbook', book.workbook_id, '')
        self.assertEqual(w.graph_documents.registry.active_key, book_key)
        self.assertTrue(w.request_outer_view('analysis'))
        w.activate_dataset(second.dataset_id)
        pump()
        self.assertEqual(w.workspace.active_dataset_id, second.dataset_id)
        self.assertEqual(w.graph_documents.registry.active_key, book_key)
        self.assertIn(w.ordinary_analysis.ownerToken, w.dat_canvas.ownerToken)
        self.assertTrue(w.dat_canvas.allowed())
        w._back_to_workspace()
        pump()
        self.assertEqual(w.outerView, 'workspace')
        self.assertEqual(w.graph_documents.registry.active_key, book_key)
        self.assertIs(w.graph_documents.document_for(source_key), formal)
        w.open_graph_document(*source_key)
        self.assertTrue(w.request_outer_view('analysis'))
        self.assertTrue(w.graph_documents.close_document(source_key))
        w._back_to_workspace()
        pump()
        self.assertEqual(w.outerView, 'workspace')
        self.assertNotIn(source_key, w.graph_documents.registry.open_keys())
        self.assertEqual(w.graph_documents.registry.active_key, book_key)
        self.assertFalse(self.errors)

    def case_normal_geometry_roundtrip(self):
        from PySide6.QtCore import QRect
        from spectra_app.project_io import read_project
        from tests.quick_application_helpers import pump
        w = self.w
        self.dat()
        w.open_graph_document()
        first = w._document_key()
        w.create_graph('Closed detail')
        w.open_graph_document()
        second = w._document_key()
        docs = w.graph_documents
        rects = [QRect(20, 25, 380, 270), QRect(65, 60, 420, 300)]
        docs.setMode('free')
        pump()
        for key, rectangle in zip((first, second), rects):
            frame = docs._presentations[key].frame
            token = docs.captureFrameInput(list(key), frame)
            self.assertTrue(docs.moveResize(list(key), frame, token, *rectangle.getRect()))
            pump()
        self.assertTrue(docs.close_document(second))
        docs.setMode('tabbed')
        pump()
        self.assertEqual(docs.registry.normal_rectangle(first), rects[0])
        path = w.save_project(self.folder / 'layout.spectra')
        saved = read_project(path).graph_workspace
        self.assertEqual(saved['mode'], 'tabbed')
        self.assertTrue(w.open_project(path))
        pump()
        self.assertEqual(set(docs.registry.open_keys()), {first})
        self.assertEqual(docs.mode, 'tabbed')
        self.assertEqual(docs.registry.normal_rectangle(first), rects[0])
        w._refresh_project_modified()
        self.assertFalse(w.projectModified)
        docs.setMode('free')
        pump()
        self.assertEqual(docs.display_rectangle(first), rects[0])
        w.open_graph_document(*second)
        pump()
        self.assertEqual(docs.registry.normal_rectangle(second), rects[1])
        self.assertIsNotNone(docs.content_item(second))
        self.assertTrue(w.projectModified)
        self.assertFalse(self.errors)

    def case_new_project_resets_layout(self):
        from unittest.mock import patch
        from PySide6.QtCore import QRect
        from spectra_app.common_prompts import PromptAction
        from tests.quick_application_helpers import item, pump
        w = self.w
        self.dat()
        w.open_graph_document()
        docs = w.graph_documents
        key = w._document_key()
        docs.setMode('free')
        pump()
        viewport = item(self.host, 'workspaceDocumentViewport')
        docs.registry.remember_rectangle(key, QRect(500, 600, 1800, 1600))
        docs._presentations[key].rectangle = QRect(500, 600, 1800, 1600)
        docs._publish()
        pump()
        viewport.setProperty('contentX', 250.)
        viewport.setProperty('contentY', 300.)
        pump()
        self.assertNotEqual(docs._scroll, (0, 0))
        self.assertTrue(docs.close_document(key))
        self.assertIsNotNone(docs.registry.normal_rectangle(key))
        self.assertFalse(docs.registry.initial_view)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            self.assertTrue(w.new_project())
        pump()
        self.assertEqual(docs.mode, 'tabbed')
        self.assertEqual(docs.keys(), ())
        self.assertIsNone(docs.registry.normal_rectangle(key))
        self.assertTrue(docs.registry.initial_view)
        self.assertEqual(docs._scroll, (0, 0))
        self.assertEqual((viewport.property('contentX'), viewport.property('contentY')), (0., 0.))
        self.assertFalse(self.errors)


if __name__ == '__main__':
    if '--case' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        name = sys.argv[sys.argv.index('--case') + 1]
        suite = unittest.TestSuite([ActualTransactions('case_' + name)])
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
        if result.wasSuccessful():
            print(name + ': PASS after Quick teardown', flush=True)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main()
