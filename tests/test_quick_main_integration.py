"""The production Quick shell uses real Qt Quick views and domain owners."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

import unittest
from unittest.mock import patch
from dataclasses import replace
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from tests.background_helpers import wait_jobs
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import pump


from tests.formal_quick_helpers import (item as quick_item, click_item,
                                         replace_text, ensure_visible)


class QuickMainIntegrationTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.paths = [self.folder / f'data-{index}.dat' for index in range(2)]
        for path in self.paths:
            path.write_text('Energy(eV) 0 1\n' + '\n'.join(
                f'{1 + i / 10} {i * i + 1} {2 * i * i + 1}' for i in range(17)), encoding='utf-8')
        self.w = self.controller
        self.errors = self.quick_errors
        self.w._load_dat_file_direct(self.paths[0])
        session = self.w.workspace.active
        self.w.open_graph_document(session.dataset_id, session.active_graph_id)
        self.w._set_dat_details_requested(True)
        pump()
        click_item(self.w, 'task_0')
        QTest.qWait(30)
        self.assertTrue(quick_item(self.w, 'processingPanel').isVisible())

    def tearDown(self):
        wait_jobs(self.w)
        self.assertEqual(self.quick_errors, [])

    def derivative(self):
        host = self.w
        click_item(host, 'processingMode')
        QTest.keyClick(self.host.window, Qt.Key_End)
        QTest.keyClick(self.host.window, Qt.Key_Return)
        pump()
        self.assertEqual(self.w.processing.mode, 'energy_second_derivative')

    def type_window(self, text, *, enter=False):
        host = self.w
        click_item(host, 'processingWindow')
        replace_text(self.host.window, text)
        if enter:
            QTest.keyClick(self.host.window, Qt.Key_Return)
        pump()

    def test_real_shell_navigation_and_action_state_round_trip(self):
        w = self.w
        self.assertIs(type(QGuiApplication.instance()), QGuiApplication)
        for name in ('commandStrip', 'taskNavigation', 'processingPanel'):
            control = quick_item(w, name)
            self.assertIsInstance(control, QQuickItem)
            self.assertTrue(control.isVisible())
            self.assertTrue(self.host.window.contentItem().isAncestorOf(control))
        self.assertFalse(self.host.window.grabWindow().isNull())
        self.assertFalse(hasattr(w, 'processing_window_spin'))
        click_item(w, 'task_1')
        self.assertEqual(w.taskIndex, 1)
        self.assertTrue(quick_item(w, 'ordinaryAnalysisRoot').isVisible())
        w.request_task(2)
        pump()
        self.assertTrue(quick_item(w, 'task_2').property('checked'))
        checked = w.project_panel_action.isChecked()
        click_item(w, 'command_project')
        self.assertEqual(w.project_panel_action.isChecked(), not checked)
        w.project_panel_action.setChecked(checked)
        pump()
        self.assertEqual(quick_item(w, 'command_project').property('checked'), checked)
        w.project_panel_action.setChecked(False)
        self.assertFalse(w.projectPanelVisible)
        w._set_dat_details_requested(False)
        pump()
        self.assertFalse(quick_item(w, 'command_project').property('checked'))
        self.assertFalse(quick_item(w, 'command_properties').property('checked'))

    def test_qml_edits_and_apply_reach_canonical_pending_and_result(self):
        w = self.w
        owner = w.workspace.active
        original = owner.current_cube
        self.derivative()
        self.type_window('9', enter=True)
        click_item(w, 'processingFixed')
        self.assertEqual(owner.pending.window_length, 9)
        self.assertEqual(owner.pending.mode, 'energy_second_derivative')
        self.assertTrue(owner.fixed_recipes['processing'].enabled)
        self.assertEqual(owner.fixed_recipes['processing'].recipe['window_length'], 9)
        click_item(w, 'processingApply')
        wait_jobs(w)
        self.assertEqual(owner.applied.mode, 'energy_second_derivative')
        self.assertIsNot(owner.current_cube, original)
        self.assertEqual(len(owner.processing_results), 1)
        self.assertEqual(self.errors, [])
        # Restoration remains bound after real user edits.
        owner.pending = replace(owner.pending, window_length=11, smooth=False)
        w.processing.syncFromOwner()
        pump()
        self.assertEqual(quick_item(w, 'processingWindow').property('text'), '11')
        self.assertFalse(quick_item(w, 'processingSmoothing').property('checked'))

    def test_invalid_text_survives_owner_switch_and_blocks_save(self):
        w = self.w
        first = w.workspace.active_dataset_id
        self.derivative()
        self.type_window('20', enter=True)
        self.assertEqual(w.processing.windowDraft, '20')
        self.assertEqual(w.workspace.active.pending.window_length, 21)
        self.assertIn('odd', w.processing.error)
        self.assertFalse(quick_item(w, 'processingApply').isEnabled())
        w._load_dat_file_direct(self.paths[1])
        self.assertEqual(w.processing.windowDraft, '21')
        with self.assertRaisesRegex(ValueError, 'processing window'):
            w.save_project(self.folder / 'invalid.spectra')
        w.activate_dataset(first)
        self.assertEqual(w.workspace.active_dataset_id, first)
        self.assertEqual(w.processing.windowDraft, '20')
        self.assertEqual(quick_item(w, 'processingWindow').property('text'), '20')
        w._refresh_project_modified()
        self.assertTrue(w.projectModified)

    def test_save_commits_qml_text_and_reopen_restores_it(self):
        w = self.w
        self.derivative()
        self.type_window('25')
        self.assertTrue(w.processing.hasDraft)
        saved = w.save_project(self.folder / 'qml-draft.spectra')
        self.assertEqual(w.workspace.active.pending.window_length, 25)
        self.assertFalse(w.processing.hasDraft)
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Saved project unexpectedly dirty')):
            self.assertTrue(w.open_project(saved))
        self.assertEqual(w.processing.windowLength, 25)
        self.assertEqual(quick_item(w, 'processingWindow').property('text'), '25')
        self.assertFalse(w.projectModified)

    def test_analysis_document_processing_uses_its_scientific_owner(self):
        w = self.w
        session = w.workspace.active
        source_pending = replace(session.pending)
        source_id = session.active_graph_id
        w._open_analysis(('heatmap', (session.dataset_id, source_id)))
        document = w._workbench_document()
        owner = document.science
        self.assertIsNot(owner, session)
        self.assertIs(w.processing.owner(), owner)
        self.assertTrue(w.workbenchActive)
        self.assertTrue(w.analysis_workbench.allowed(w.analysis_workbench.ownerToken))
        frozen_pending = replace(owner.pending)
        self.assertFalse(w.processing.enabled)
        w.processing.setMode('energy_second_derivative')
        w.processing.setWindowDraft('9')
        self.assertFalse(w.processing.hasDraft)
        self.assertTrue(w.processing.commitWindow())
        self.assertFalse(w.processing.hasDraft)
        self.assertEqual(owner.pending, frozen_pending)
        self.assertEqual(session.pending, source_pending)
        self.assertTrue(quick_item(w, 'analysisWorkbench').isVisible())

    def test_failed_project_install_restores_invalid_qml_draft(self):
        w = self.w
        saved = w.save_project(self.folder / 'before-failed-open.spectra')
        workspace = w.workspace
        self.derivative()
        self.type_window('20', enter=True)
        from spectra_app.common_prompts import PromptAction
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD), \
                patch.object(w, 'activate_dataset', side_effect=RuntimeError('projection failure')):
            with self.assertRaisesRegex(RuntimeError, 'projection failure'):
                w.open_project(saved)
        self.assertIs(w.workspace, workspace)
        self.assertEqual(w.processing.windowDraft, '20')
        self.assertIn('odd', w.processing.error)
        self.assertEqual(quick_item(w, 'processingWindow').property('text'), '20')

    def test_long_pasted_numeric_text_remains_an_invalid_draft(self):
        w = self.w
        self.derivative()
        text = '9' * 5000
        w.processing.setWindowDraft(text)
        self.assertFalse(w.processing.commitWindow())
        self.assertEqual(w.processing.windowDraft, text)
        self.assertIn('odd', w.processing.error)
        self.assertFalse(w.processing.applyEnabled)
        with self.assertRaisesRegex(ValueError, 'processing window'):
            w.save_project(self.folder / 'long.spectra')

    def test_narrow_dock_keeps_quick_processing_inside_visible_viewport(self):
        w = self.w
        self.host.window.resize(1000, 720)
        pump(80)
        dock = quick_item(w, 'dockedProperties')
        # Drag the real SplitView handle immediately left of Properties. Its
        # native pointer route, rather than a preferred-width property, owns
        # the width that the visible panel must fit.
        start = dock.mapToScene(QPointF(-3, dock.height() / 2)).toPoint()
        end = start + QPoint(round(dock.width() - 300), 0)
        QTest.mousePress(self.host.window, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.host.window, end)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=end)
        pump(80)
        self.assertAlmostEqual(dock.width(), 300, delta=2)
        viewport = quick_item(w, 'propertiesScroll')
        panel = quick_item(w, 'processingPanel')
        self.assertLessEqual(panel.width(), viewport.width())
        self.assertLessEqual(viewport.property('contentWidth'), viewport.width())
        for name in ('processingFixed', 'processingMode', 'processingWindow', 'processingApply'):
            control = ensure_visible(quick_item(panel, name))
            rect = control.mapRectToItem(panel, control.boundingRect())
            self.assertGreaterEqual(rect.left(), 0)
            self.assertLessEqual(rect.right(), panel.width())
        self.assertFalse(self.host.window.grabWindow().isNull())

    def test_import_busy_disables_qml_and_rejects_ui_mutation(self):
        w = self.w
        self.derivative()
        before = replace(w.workspace.active.pending)
        w._set_dat_import_pending(True)
        pump()
        self.assertFalse(quick_item(w, 'processingMode').isEnabled())
        self.assertFalse(quick_item(w, 'processingWindow').isEnabled())
        self.assertFalse(quick_item(w, 'processingApply').isEnabled())
        click_item(w, 'processingSmoothing')
        click_item(w, 'processingApply')
        w.processing.setWindowDraft('9')
        w.processing.setMode('original')
        w._on_generate_heatmap()
        self.assertEqual(w.workspace.active.pending, before)
        self.assertFalse(w._background_jobs.busy)
        w._set_dat_import_pending(False)
        pump()
        self.assertTrue(quick_item(w, 'processingWindow').isEnabled())


if __name__ == '__main__':
    unittest.main()
