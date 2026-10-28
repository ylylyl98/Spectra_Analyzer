"""Project persistence observed through the real Qt workspace boundary."""
from tests import analysis_header_quick_helpers as ah
from tests import ordinary_analysis_quick_helpers as oa
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
from copy import deepcopy
import time
from dataclasses import replace
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtTest import QTest

from spectra_app.common_prompts import PromptAction
from tests.dat_properties_quick_helpers import (edit_number, edit_label, state, set_choice)
from spectra_app.edit_history import EditCommand, FieldChange
from tests import quick_migration_case as migration
from tests.quick_application_helpers import application, pump
from tests.background_helpers import wait_jobs
from tests.formal_quick_helpers import item
from tests.scientific_fixtures import workflow_fit_result as _fit_result

class ProjectWorkflowTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.windows = []
        self.window_errors = []
        self.paths = []
        for name in ('first', 'second'):
            folder = self.folder / name
            folder.mkdir()
            path = folder / 'sample.dat'
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{x:.12g} {x*x:.12g} {x**3:.12g} {x:.12g}'
                for x in np.linspace(1, 4, 31)), encoding='utf-8')
            self.paths.append(path)

    def tearDown(self):
        for window in self.windows:
            wait_jobs(window)
            self.wait_project(window)
        for errors in self.window_errors:
            self.assertEqual(errors, [], 'Unexpected production Quick error')

    def wait_project(self, window):
        deadline = time.monotonic() + 8
        while window._project_io_jobs.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(window._project_io_jobs.busy, 'project operation timed out')
        QTest.qWait(20)

    def window(self):
        if not self.windows:
            window = self.controller
            errors = self.quick_errors
        else:
            host, folder, errors = self.enterContext(application())
            window = host.controller
        self.windows.append(window)
        self.window_errors.append(errors)
        return window

    def set_rectangle(self, window, key, rectangle):
        """Arrange free-layout geometry through the actual mounted frame owner."""
        documents = window.graph_documents
        window.request_outer_view('workspace')
        documents.setMode('free')
        pump()
        frame = item(window, 'documentFrame_' + '_'.join(key))
        token = documents.captureFrameInput(key, frame)
        self.assertTrue(documents.moveResize(key, frame, token, *rectangle.getRect()))
        pump()

    def test_graph_workspace_save_load_restores_only_open_set_and_closed_geometry(self):
        w = self.window()
        w.root_window.show()
        w._load_dat_file_direct(self.paths[0])
        owner = w.workspace.active_dataset_id
        w.open_graph_document()
        detail = w.create_graph('Closed detail').graph_id
        w.open_graph_document()
        last = w.create_graph('Overview').graph_id
        w.open_graph_document()
        rectangles = [QRect(20 + i * 45, 25 + i * 35, 380, 270) for i in range(3)]
        keys = [(owner, graph) for graph in ('default', detail, last)]
        for key, rect in zip(keys, rectangles):
            self.set_rectangle(w, key, rect)
        w.graph_documents.close_document(keys[1])
        saved = w.save_project(self.folder / 'layout.spectra')
        self.assertFalse(w.projectModified)
        self.set_rectangle(w, keys[2], QRect(155, 180, 380, 270))
        w._refresh_project_modified()
        self.assertTrue(w.projectModified)
        self.set_rectangle(w, keys[2], rectangles[2])
        w._refresh_project_modified()
        self.assertFalse(w.projectModified)
        self.paths[0].unlink()
        restored = self.window()
        self.assertTrue(restored.open_project(saved))
        restored.root_window.show()
        pump()
        self.assertEqual(set(restored.graph_documents.registry.open_keys()), {keys[0], keys[2]})
        self.assertEqual(restored.graph_documents.registry.normal_rectangle(keys[2]), rectangles[2])
        self.assertEqual(restored.workspace.active.active_graph_id, last)
        restored._refresh_project_modified()
        self.assertFalse(restored.projectModified)
        restored.open_graph_document(*keys[1])
        self.assertEqual(restored.graph_documents.registry.normal_rectangle(keys[1]), rectangles[1])
        restored._refresh_project_modified()
        self.assertTrue(restored.projectModified)

    def test_all_closed_layout_stays_closed_on_load_and_plot_entry(self):
        w = self.window()
        w.root_window.show()
        w._load_dat_file_direct(self.paths[0])
        w.open_graph_document()
        key = w._document_key()
        rect = QRect(42, 55, 390, 280)
        self.set_rectangle(w, key, rect)
        w.graph_documents.close_document(key)
        saved = w.save_project(self.folder / 'closed.spectra')
        restored = self.window()
        restored.open_project(saved)
        restored.root_window.show()
        restored.request_outer_view('workspace')
        pump()
        self.assertEqual(restored.graph_documents.registry.open_keys(), ())
        self.assertTrue(item(restored, 'formalCommands').isVisible())
        viewport = item(restored, 'workspaceDocumentViewport')
        empty_message = next(child for child in viewport.property('contentItem').childItems()
                             if child.metaObject().indexOfProperty('text') >= 0
                             and str(child.property('text')).startswith('Open a Workbook'))
        self.assertTrue(empty_message.isVisible())
        self.assertEqual(empty_message.property('text'),
                         'Open a Workbook or graph from Project, or start with data files.')
        restored._refresh_project_modified()
        self.assertFalse(restored.projectModified)
        restored.open_graph_document()
        self.assertEqual(restored.graph_documents.registry.normal_rectangle(key), rect)

    def test_offscreen_saved_rectangle_is_reachable_without_shrinking_or_dirtying_on_show(self):
        from spectra_app.project_io import write_project
        w = self.window()
        w._load_dat_file_direct(self.paths[0])
        w.open_graph_document()
        workspace = w._capture_project_workspace()
        saved_rect = [99999, -500, 560, 390]
        workspace.graph_workspace['mode'] = 'free'
        workspace.graph_workspace['documents'][0]['rect'] = saved_rect
        destination = self.folder / 'offscreen.spectra'
        write_project(destination, workspace)
        restored = self.window()
        restored.root_window.resize(1100, 720)
        restored.open_project(destination)
        restored.root_window.show()
        restored.request_outer_view('workspace')
        pump()
        QTest.qWait(20)
        key = restored.graph_documents.registry.active_key
        frame = item(restored, 'documentFrame_' + '_'.join(key))
        self.assertEqual((frame.width(), frame.height()), (560, 390))
        viewport = item(restored, 'workspaceDocumentViewport')
        self.assertGreater(frame.width(), viewport.width())
        title = QRect(round(frame.x()), round(frame.y()), round(frame.width()), 34)
        visible = QRect(round(viewport.property('contentX')), round(viewport.property('contentY')),
                        round(viewport.width()), round(viewport.height()))
        reachable = visible.intersected(title)
        self.assertGreaterEqual(reachable.width(), 120)
        self.assertEqual(reachable.height(), title.height())
        self.assertEqual(restored._capture_project_workspace().graph_workspace['documents'][0]['rect'],
                         saved_rect)
        restored._refresh_project_modified()
        self.assertFalse(restored.projectModified)

    def test_analysis_save_keeps_each_dataset_selected_graph_and_open_document_owner(self):
        w = self.window()
        w._load_dat_file_direct(self.paths[0])
        first_id = w.workspace.active_dataset_id
        w.open_graph_document()
        w.create_graph('Selected first detail')
        selected = w.workspace.active.active_graph_id
        w.request_outer_view('analysis')
        w._load_dat_file_direct(self.paths[1])
        second_id = w.workspace.active_dataset_id
        saved = w.save_project(self.folder / 'analysis-layout.spectra')
        restored = self.window()
        restored.open_project(saved)
        self.assertEqual(restored.workspace.active_dataset_id, second_id)
        self.assertEqual(restored.workspace.datasets[first_id].active_graph_id, selected)
        self.assertEqual(restored.workspace.datasets[second_id].active_graph_id, 'default')
        self.assertEqual(set(restored.graph_documents.registry.open_keys()),
                         {(first_id, 'default'), (first_id, selected)})
        self.assertEqual(restored.outerView, 'analysis')
        self.assertTrue(item(restored, 'analysisHeader').isVisible())
        # Reopening the same file while currently in Plot must not show graph
        # A alongside dataset B's property editors or change B's selection.
        w.request_outer_view('workspace')
        with patch.object(w, '_confirm_project_discard', return_value=True):
            w.open_project(saved)
        self.assertEqual(w.workspace.active_dataset_id, second_id)
        self.assertEqual(w.workspace.datasets[first_id].active_graph_id, selected)
        self.assertEqual(w.workspace.datasets[second_id].active_graph_id, 'default')
        self.assertEqual(w.outerView, 'analysis')
        self.assertTrue(item(w, 'analysisHeader').isVisible())

    def test_save_restart_embeds_two_owners_without_source_reads(self):
        w = self.window()
        for path in self.paths:
            w._load_dat_file_direct(path)
            w.peak_indices = np.array([4, 9])
            w._peak_gate_idx = 1
            w.fit_result = _fit_result(2)
            w._fit_gate_idx = 1
            w.batch_fit_result_map = {1: w.fit_result}
            w._csv_seeds_map = {1: [2.1]}
            w._csv_seeds_path = str(self.folder / 'missing.csv')
            oa.set_summary(w, 'stored result ' + path.parent.name)
        oa.draft(w, 'prominence', '0.127')
        w.processing.setWindowDraft('25')
        self.assertTrue(w.request_outer_view('analysis'))
        edit_label(w, 'plot_title', 'Portable typed title')
        expected = {s.dataset_id: s.current_cube.z.copy() for s in w.workspace.datasets.values()}
        saved = w.save_project(self.folder / 'portable')
        self.assertEqual(saved.suffix, '.spectra')
        for path in self.paths:
            path.unlink()
        restored = self.window()
        with patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('source read')), \
             patch.object(restored._plot_state_store, 'load', side_effect=AssertionError('sidecar read')), \
             patch.object(restored, 'snapshot_dataset_defaults', side_effect=AssertionError('defaults')), \
             patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')), \
             patch('spectra_app.main_window.fit_multi_peak', side_effect=AssertionError('recompute')):
            self.assertTrue(restored.open_project(saved))
        self.assertEqual(restored.workspace.active_dataset_id, w.workspace.active_dataset_id)
        self.assertEqual(oa.value(restored, 'prominence'), .127)
        self.assertEqual(restored.processing.windowLength, 25)
        self.assertEqual(state(restored)['plot_title'], 'Portable typed title')
        for owner, session in restored.workspace.datasets.items():
            np.testing.assert_array_equal(session.current_cube.z, expected[owner])
            self.assertIs(session.base_cube, session.current_cube)
            self.assertFalse(session.sidecar_writes_enabled)
            self.assertIsNotNone(session.fit_result)
            self.assertEqual(session.csv_seeds_map, {1: [2.1]})
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('clean close prompt')):
            restored.root_window.close()
            self.wait_project(restored)
            self.assertFalse(restored.root_window.isVisible())
        self.assertFalse(restored._project_close_approved)

    def test_menu_shortcuts_preserve_dat_import_and_copy(self):
        w = self.window()
        expected = {'new_project': 'Ctrl+N', 'open_project': 'Ctrl+Shift+O',
                    'save_project': 'Ctrl+S', 'save_project_as': 'Ctrl+Shift+S',
                    'import': 'Ctrl+O', 'copy_graph': 'Ctrl+Alt+J'}
        for name, key in expected.items():
            self.assertEqual(w._graph_command_actions[name].shortcut().toString(), key)

    def test_native_save_names_preserve_unicode_and_spaces(self):
        from spectra_app.project_io import read_project
        w = self.loaded_window()
        folder = self.folder / '中文 项目'
        folder.mkdir()
        for entered, expected in (('无后缀 项目', '无后缀 项目.spectra'),
                                  ('指定 项目.spectra', '指定 项目.spectra')):
            with self.subTest(entered=entered):
                saved = w.save_project(folder / entered)
                self.assertEqual(saved, folder / expected)
                self.assertTrue(saved.exists())
                self.assertEqual(w._project_path, saved)
                np.testing.assert_array_equal(
                    read_project(saved).active.current_cube.z, w.current_cube.z)

    def test_new_project_save_dialog_defaults_to_spectra(self):
        w = self.loaded_window()
        entered = self.folder / 'chosen without extension'
        with patch('spectra_app.main_window.FilePicker.getSaveFileName',
                   return_value=(str(entered), '')) as dialog:
            saved = w.save_project()
        self.assertEqual(Path(dialog.call_args.args[2]).name, 'Untitled.spectra')
        self.assertEqual(dialog.call_args.args[3], 'Spectra projects (*.spectra)')
        self.assertEqual(saved, self.folder / 'chosen without extension.spectra')
        self.assertTrue(saved.exists())

    def test_new_project_save_as_dialog_defaults_to_spectra(self):
        w = self.loaded_window()
        entered = self.folder / 'chosen.spectra'
        with patch('spectra_app.main_window.FilePicker.getSaveFileName',
                   return_value=(str(entered), '')) as dialog:
            w._on_save_project(save_as=True)
            self.wait_project(w)
        self.assertEqual(Path(dialog.call_args.args[2]).name, 'Untitled.spectra')
        self.assertEqual(dialog.call_args.args[3], 'Spectra projects (*.spectra)')
        self.assertEqual(w._project_path, entered)
        self.assertTrue(entered.exists())

    def test_open_dialog_accepts_native_and_legacy_projects(self):
        w = self.loaded_window()
        paths = [w.save_project(self.folder / name)
                 for name in ('native.spectra', 'legacy.spextra')]
        other = self.window()
        for path in paths:
            with self.subTest(path=path), patch(
                    'spectra_app.main_window.FilePicker.getOpenFileName',
                    return_value=(str(path), '')) as dialog:
                other._on_open_project()
                self.wait_project(other)
                self.assertEqual(dialog.call_args.args[3],
                                 'Spectra projects (*.spectra *.spextra)')
                self.assertEqual(other._project_path, path)
                np.testing.assert_array_equal(other.current_cube.z, w.current_cube.z)

    def test_legacy_project_keeps_saved_path_on_ctrl_s(self):
        from spectra_app.project_io import read_project
        w = self.loaded_window()
        entered = self.folder / '旧 项目.spextra'
        self.assertEqual(w.save_project(entered), entered)
        other = self.window()
        self.assertTrue(other.open_project(entered))
        self.assertTrue(other.request_outer_view('analysis'))
        edit_label(other, 'plot_title', 'changed after legacy open')
        with patch('spectra_app.main_window.FilePicker.getSaveFileName',
                   side_effect=AssertionError('existing project does not need a path')):
            other._on_save_project()
            self.wait_project(other)
        self.assertEqual(other._project_path, entered)
        self.assertTrue(entered.exists())
        self.assertFalse(entered.with_name(entered.name + '.spectra').exists())
        self.assertEqual(read_project(entered).active.view.annotations['plot_title'],
                         'changed after legacy open')

    def test_legacy_project_save_as_suggests_spectra_without_renaming(self):
        w = self.loaded_window()
        legacy = w.save_project(self.folder / 'legacy.spextra')
        previous_bytes = legacy.read_bytes()
        with patch('spectra_app.main_window.FilePicker.getSaveFileName',
                   return_value=('', '')) as dialog:
            w._on_save_project(save_as=True)
        self.assertEqual(Path(dialog.call_args.args[2]), self.folder / 'legacy.spectra')
        self.assertEqual(dialog.call_args.args[3], 'Spectra projects (*.spectra)')
        self.assertEqual(w._project_path, legacy)
        self.assertEqual(legacy.read_bytes(), previous_bytes)
        self.assertFalse((self.folder / 'legacy.spectra').exists())

    def test_pending_gate_xy_colors_and_formal_numbers_restore_effective_values(self):
        w = self.loaded_window()
        w.root_window.resize(1500, 920)
        w.root_window.show()
        w.open_graph_document()
        self.assertTrue(w.request_outer_view('analysis'))
        pending = {'xmin': '1.4', 'xmax': '3.6', 'ymin': '0.25',
                   'ymax': '1.75', 'vmin': '1.2', 'vmax': '14.2'}
        edit_number(w, 'xmin', pending['xmin'])
        gate_before = w.current_gate
        ah.edit(w, 'gate', '2')
        self.assertTrue(w.request_task(2))
        pump()
        # Simultaneous unfinished bytes are runtime drafts, before any commit.
        for field, text in pending.items():
            w.dat_properties.draftNumber(field, text, w.dat_properties.ownerToken)
        formal = w.plot_page.formal
        source = item(w, 'formalProperties')
        token = formal.capturePropertiesInput(source)
        self.assertTrue(token)
        presentation_before = w.workspace.active.presentation
        formal_pending = {'width_in': '4.101', 'text_pt': '11', 'heatmap_dpi': '220'}
        for field, text in formal_pending.items():
            formal.setDraft(field, text, source, token)
        self.assertEqual(formal._drafts, formal_pending)
        self.assertEqual(w.plot_page.presentation, presentation_before)
        self.assertEqual(w.workspace.active.presentation, presentation_before)
        self.assertEqual(w.dat_properties._drafts[w.dat_properties.owner_key()], pending)
        self.assertEqual(w.analysis_header._drafts[w.analysis_header.owner_key()], {'gate': '2'})
        self.assertEqual(w.current_gate, gate_before)
        path = w.save_project(self.folder / 'typed.spextra')
        other = self.window()
        self.assertTrue(other.open_project(path))
        self.assertEqual(other.current_gate, 2)
        np.testing.assert_allclose(other._heat_ax.get_xlim(), (1.4, 3.6))
        np.testing.assert_allclose(other._heat_ax.get_ylim(), (.25, 1.75))
        np.testing.assert_allclose(other._heat_mesh.get_clim(), (1.2, 14.2))
        self.assertTrue(state(other)['vmin_lock'])
        self.assertTrue(state(other)['vmax_lock'])
        self.assertAlmostEqual(other.workspace.active.presentation.width_in, 4.101)
        self.assertEqual(other.workspace.active.presentation.text_pt, 11)
        self.assertEqual(other.workspace.active.presentation.heatmap_dpi, 220)

    def test_pending_gate_index_also_commits_before_save(self):
        w = self.loaded_window()
        ah.edit(w, 'index', '3')
        path = w.save_project(self.folder / 'gate-index.spextra')
        other = self.window()
        self.assertTrue(other.open_project(path))
        self.assertEqual(other.current_gate, 2)
        self.assertEqual(int(ah.text(other, 'index')), 3)

    def loaded_window(self):
        w = self.window()
        w._load_dat_file_direct(self.paths[0])
        self.assertTrue(w.request_outer_view('analysis'))
        return w

    def test_guard_cancel_discard_and_save_canceled_or_failed_preserve_workspace(self):
        w = self.loaded_window()
        previous = w.workspace
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as prompt:
            self.assertFalse(w.new_project())
        self.assertEqual(prompt.call_args.args[3], (PromptAction.SAVE, PromptAction.DISCARD, PromptAction.CANCEL))
        self.assertEqual(prompt.call_args.args[-1], PromptAction.CANCEL)
        self.assertEqual(prompt.call_args.kwargs, {'kind': 'warning'})
        self.assertIs(w.workspace, previous)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.SAVE), \
             patch('spectra_app.main_window.FilePicker.getSaveFileName', return_value=('', '')):
            self.assertFalse(w.new_project())
        self.assertIs(w.workspace, previous)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.SAVE), \
             patch.object(w, 'save_project', side_effect=OSError('disk full')):
            self.assertFalse(w.new_project())
        self.assertEqual(self.quick_errors, ['Could not save project: disk full'])
        self.quick_errors.clear()
        self.assertIs(w.workspace, previous)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            self.assertTrue(w.new_project())
        self.assertFalse(w.workspace.datasets)

    def test_successful_guard_save_persists_latest_edits_before_new(self):
        from spectra_app.project_io import read_project
        w = self.loaded_window()
        path = w.save_project(self.folder / 'guard.spextra')
        owner = w.workspace.active_dataset_id
        edit_label(w, 'plot_title', 'latest before New')
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.SAVE):
            self.assertTrue(w.new_project())
        self.assertEqual(read_project(path).datasets[owner].view.annotations['plot_title'], 'latest before New')
        self.assertIsNone(w._project_path)

    def test_write_failure_preserves_baseline_path_file_and_source_ownership(self):
        w = self.loaded_window()
        source = w.workspace.active
        with patch('spectra_app.project_io.write_project', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                w.save_project(self.folder / 'failed.spextra')
        self.assertTrue(source.sidecar_writes_enabled)
        self.assertIsNone(w._project_path)
        path = w.save_project(self.folder / 'saved.spextra')
        old_bytes, digest = path.read_bytes(), w._project_saved_digest
        oa.set_number(w, 'prominence', .2)
        with patch('spectra_app.project_io.write_project', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                w.save_project(self.folder / 'different.spextra')
        self.assertEqual((w._project_path, w._project_saved_digest), (path, digest))
        self.assertEqual(path.read_bytes(), old_bytes)

    def test_invalid_edit_blocks_save_but_explicit_discard_can_close(self):
        for source in ('processing', 'shared'):
            with self.subTest(source=source):
                w = self.loaded_window()
                w.root_window.resize(1500, 920)
                w.root_window.show()
                w.open_graph_document()
                saved = w.save_project(self.folder / f'{source}-valid.spextra')
                old_bytes, baseline = saved.read_bytes(), w._project_saved_digest
                old_view = deepcopy(w.workspace.active.view)
                if source == 'processing':
                    control = oa.edit(w, 'prominence', 'not a number')
                    draft = 'not a number'
                    pattern = 'numeric'
                else:
                    control = edit_number(w, 'xmin', '-')
                    draft = '-'
                    pattern = 'pending xmin editor'
                with self.assertRaisesRegex(ValueError, pattern):
                    w.save_project(self.folder / f'{source}-invalid.spextra')
                self.assertEqual(saved.read_bytes(), old_bytes)
                self.assertEqual((w._project_path, w._project_saved_digest), (saved, baseline))
                self.assertEqual(w.workspace.active.view, old_view)
                self.assertFalse((self.folder / f'{source}-invalid.spextra').exists())
                self.assertEqual(control.property('text'), draft)
                with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as prompt:
                    w.root_window.close()
                    self.wait_project(w)
                    self.assertTrue(w.root_window.isVisible())
                    self.assertIn('cannot be saved', prompt.call_args.args[2])
                with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
                    w.root_window.close()
                    self.wait_project(w)
                self.assertFalse(w.root_window.isVisible())

    def test_invalid_committed_mapping_save_preserves_previous_file_and_baseline(self):
        w = self.loaded_window()
        path = w.save_project(self.folder / 'valid-mapping.spextra')
        previous_bytes, baseline = path.read_bytes(), w._project_saved_digest
        set_choice(w, 'mapping', 'log')
        edit_number(w, 'vmin', '-1')
        with self.assertRaisesRegex(ValueError, 'sample.dat.*Log color limits'):
            w.save_project()
        self.assertEqual(path.read_bytes(), previous_bytes)
        self.assertEqual((w._project_path, w._project_saved_digest), (path, baseline))
        self.assertLess(float(state(w)['vmin']), 0)

    def test_malformed_and_failed_install_keep_previous_model_path_baseline(self):
        w = self.loaded_window()
        path = w.save_project(self.folder / 'saved.spextra')
        previous, baseline = w.workspace, w._project_saved_digest
        bad = self.folder / 'malformed.spextra'
        bad.write_bytes(b'not an archive')
        with patch.object(w, '_confirm_project_discard', side_effect=AssertionError('guard before decode')):
            with self.assertRaises(ValueError):
                w.open_project(bad)
        self.assertIs(w.workspace, previous)
        with patch.object(w, 'activate_dataset', side_effect=RuntimeError('projection failure')):
            with self.assertRaisesRegex(RuntimeError, 'projection failure'):
                w.open_project(path)
        self.assertIs(w.workspace, previous)
        self.assertEqual((w._project_path, w._project_saved_digest), (path, baseline))
        self.assertIsNotNone(w._heat_mesh)
        self.assertEqual(len(w.source_navigation.datasetRows), 1)

    def test_failed_projection_preserves_owner_then_cached_apply_creates_independent_result(self):
        w = self.loaded_window()
        session = w.workspace.active
        session.current_cube = replace(session.base_cube, z=-session.base_cube.z)
        session.applied.mode = 'energy_second_derivative'
        session.applied.original_signal_title = 'Intensity'
        session.pending = replace(session.applied)
        w.restore_dataset_controls(session)
        w._plot_cube()
        result = _fit_result(2)
        session.fit_result, session.fit_gate_idx = result, 1
        w._applied_derivative_sources[session.dataset_id] = (id(session.base_cube), id(session.current_cube))
        path = w.save_project(self.folder / 'old-processed.spextra')
        history = w._history_for(session)
        command = EditCommand('prior visual edit', (
            FieldChange(('view', 'annotations', 'plot_title'), 'before', 'after'),))
        history.record(command)
        with patch.object(w, 'activate_dataset', side_effect=RuntimeError('projection failure')):
            with self.assertRaises(RuntimeError):
                w.open_project(path)
        self.assertIs(w._history_for(session), history)
        self.assertEqual(history.undo_stack, [command])
        self.assertEqual(w._applied_derivative_sources[session.dataset_id],
                         (id(session.base_cube), id(session.current_cube)))
        self.assertIs(session.fit_result, result)
        previous_cube = session.current_cube
        result_ids = set(session.processing_results)
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')):
            w._on_generate_heatmap()
        self.assertIsNot(session.current_cube, previous_cube)
        np.testing.assert_array_equal(session.current_cube.z, previous_cube.z)
        self.assertGreater(len(session.processing_results), len(result_ids))
        self.assertTrue(result_ids.issubset(session.processing_results))
        self.assertTrue(any(record.cube is previous_cube for record in session.processing_results.values()))
        self.assertIsNone(session.fit_result)
        self.assertFalse(w._background_jobs.busy)

    def test_open_cancel_keeps_current_work_after_validating_candidate(self):
        w = self.loaded_window()
        path = w.save_project(self.folder / 'saved.spextra')
        previous = w.workspace
        oa.set_number(w, 'prominence', .4)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL):
            self.assertFalse(w.open_project(path))
        self.assertIs(w.workspace, previous)
        self.assertEqual(oa.value(w, 'prominence'), .4)

    def test_internal_plot_value_error_rolls_back_instead_of_installing_partial_view(self):
        w = self.loaded_window()
        w.fit_result = _fit_result(2)
        w._fit_gate_idx = 1
        path = w.save_project(self.folder / 'saved.spextra')
        previous, cube, result = w.workspace, w.current_cube, w.fit_result
        baseline = w._project_saved_digest
        original = w._plot_cube_impl
        calls = []
        def fail_once():
            calls.append(True)
            if len(calls) == 1:
                raise ValueError('injected plot failure')
            original()
        with patch.object(w, '_plot_cube_impl', side_effect=fail_once):
            with self.assertRaisesRegex(ValueError, 'injected plot failure'):
                w.open_project(path)
        self.assertIs(w.workspace, previous)
        self.assertIs(w.current_cube, cube)
        self.assertIs(w.fit_result, result)
        self.assertEqual(w._project_saved_digest, baseline)
        self.assertFalse(w._project_installing)
        self.assertIsNotNone(w._heat_mesh)

    def test_project_owner_suppresses_both_sidecars_and_source_axis_routes(self):
        w = self.loaded_window()
        w._load_dat_file_direct(self.paths[1])
        w.save_project(self.folder / 'saved.spextra')
        with patch.object(w._plot_state_store, 'save', side_effect=AssertionError('sidecar write')), \
             patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('axis source read')), \
             patch('spectra_app.main_window.prepare_dat', side_effect=AssertionError('async source read')):
            self.assertEqual(w._save_current_plot_state(), '')
            for session in w.workspace.datasets.values():
                w._save_plot_dataset(session)
            w._on_x_axis_override_changed()
            w._on_x_axis_override_async()
            w.activate_dataset(next(iter(w.workspace.datasets)))
        self.assertFalse(w._background_jobs.busy)
        w._set_dat_import_pending(True)
        w._set_dat_import_pending(False)
        w._refresh_processing_controls()
        self.assertFalse(state(w)['source_axis_enabled'])
        self.assertIn('new project', state(w)['source_axis_tooltip'])

    def test_close_busy_cancel_consumes_retry_and_later_job_does_not_close(self):
        w = self.loaded_window()
        w.root_window.show()
        release = threading.Event()
        self.addCleanup(release.set)
        w._start_background_job(lambda context: release.wait(5), lambda result: None, 'test')
        with self.assertRaises(ValueError):
            w.save_project(self.folder / 'busy.spextra')
        with self.assertRaises(ValueError):
            w.new_project()
        with patch('spectra_app.project_io.read_project', side_effect=AssertionError('busy read')):
            with self.assertRaises(ValueError):
                w.open_project(self.folder / 'busy.spextra')
        w.root_window.close()
        self.assertTrue(w.root_window.isVisible())
        self.assertTrue(w._close_pending)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL), \
             patch.object(w, 'close', wraps=w.close) as close:
            release.set()
            wait_jobs(w)
            self.wait_project(w)
            QTest.qWait(15)
            self.assertFalse(w._close_pending)
            self.assertEqual(close.call_count, 1)
            self.assertTrue(w.root_window.isVisible())
            w._start_background_job(lambda context: None, lambda result: None, 'later')
            wait_jobs(w)
            QTest.qWait(15)
            self.assertEqual(close.call_count, 1)

    def test_new_clears_visible_science_preview_histories_and_imports_again(self):
        w = self.loaded_window()
        w.save_project(self.folder / 'saved.spextra')
        with patch('spectra_app.main_window.FilePicker.getExistingDirectory',
                   return_value=str(self.paths[0].parent)):
            w._on_browse_root()
        wait_jobs(w)
        self.assertTrue(w.source_navigation.fileRows)
        w.source_navigation.setSearchText('sample')
        w._render_plot_page()
        self.assertTrue(w.figure.axes)
        self.assertIsNotNone(w.plot_page.rendered_page)
        w._cached_d2 = (np.arange(2), np.arange(2))
        old_generation = w._data_generation
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            self.assertTrue(w.new_project())
        self.assertEqual(w.figure.axes, [])
        self.assertIsNone(w._heat_mesh)
        self.assertIsNone(w._spec_ax)
        self.assertEqual(w.plot_page.preview.svg_bytes, b'')
        self.assertIsNone(w.plot_page.preview.snapshot)
        self.assertEqual(oa.summary(w), '')
        self.assertEqual(float(ah.text(w, 'gate')), 0)
        self.assertEqual(oa.state(w)['csvPath'], '')
        oa.reveal(w, tab=1)
        self.assertEqual(item(w, 'ordinaryCsvPath').property('text'), 'No CSV loaded')
        self.assertEqual(len(w.source_navigation.datasetRows), 0)
        self.assertEqual(w.source_navigation.searchText, '')
        self.assertEqual((w.source_navigation.rootPath, w.source_navigation.selectedFolder,
                          w.source_navigation.selectedFile, w.source_navigation.folderRows,
                          w.source_navigation.fileRows), ('', '', '', [], []))
        self.assertEqual(w._edit_histories, {})
        self.assertEqual(w._applied_derivative_sources, {})
        self.assertIsNone(w._cached_d2)
        self.assertGreater(w._data_generation, old_generation)
        w._import_dat_files([self.paths[1]])
        wait_jobs(w)
        self.assertIsNotNone(w._heat_mesh)
        self.assertEqual(w.current_dat_path, self.paths[1])
        self.assertTrue(state(w)['source_axis_enabled'])

    def test_restored_processing_ownership_pairs_and_pending_controls_are_distinct(self):
        w = self.loaded_window()
        session = w.workspace.active
        session.current_cube = replace(session.base_cube, z=-session.base_cube.z)
        session.applied.mode = 'energy_second_derivative'
        session.applied.original_signal_title = 'Intensity'
        w._plot_cube()
        w.processing.setMode('original')
        path = w.save_project(self.folder / 'processed.spextra')
        other = self.window()
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')):
            self.assertTrue(other.open_project(path))
        restored = other.workspace.active
        self.assertEqual(restored.applied.mode, 'energy_second_derivative')
        self.assertEqual(restored.pending.mode, 'original')
        self.assertEqual(other._applied_derivative_sources[restored.dataset_id],
                         (id(restored.base_cube), id(restored.current_cube)))
        self.assertIsNot(restored.base_cube, restored.current_cube)

    def test_all_nan_processed_array_and_existing_fit_restore_without_recompute(self):
        w = self.loaded_window()
        session = w.workspace.active
        session.current_cube = replace(session.base_cube, z=np.full_like(session.base_cube.z, np.nan))
        session.applied.mode = 'energy_second_derivative'
        session.applied.original_signal_title = 'Intensity'
        session.pending = replace(session.applied)
        w.restore_dataset_controls(session)
        w._plot_cube()
        session.fit_result, session.fit_gate_idx = _fit_result(2), 1
        path = w.save_project(self.folder / 'missing-values.spextra')
        other = self.window()
        with patch('spectra_app.main_window.energy_second_derivative', side_effect=AssertionError('recompute')):
            self.assertTrue(other.open_project(path))
        self.assertTrue(np.isnan(other.current_cube.z).all())
        self.assertIsNotNone(other.fit_result)
        self.assertEqual(other._processing_mode, 'energy_second_derivative')

    def test_project_export_folder_numbering_batch_csv_and_comparison_provenance(self):
        w = self.loaded_window()
        w.open_graph_document()
        w.peak_indices = np.array([4])
        w._peak_gate_idx = w._current_gate_index()
        w.fit_result = _fit_result(2)
        w._fit_gate_idx = w._current_gate_index()
        w.batch_fit_result_map = {w._fit_gate_idx: w.fit_result}
        project_folder = self.folder / 'projects'
        project_folder.mkdir()
        w.save_project(project_folder / 'saved.spextra')
        (project_folder / 'sample_Graph 1_export01.svg').write_text('reserved')
        self.assertEqual(w._heatmap_export_filename('svg'), 'sample_Graph 1_export02.svg')
        w._render_plot_page()
        saved = w.export_plot_page('svg')
        self.assertEqual(saved.parent, project_folder)
        defaults = []
        def choose(_window, _title, default, _filter):
            defaults.append(Path(default))
            return default, ''
        with patch('spectra_app.main_window.FilePicker.getSaveFileName', side_effect=choose):
            w._on_save_peaks_csv()
            w._on_export_current_fit_csv()
            w._on_export_all_fit_csv()
            w._on_export_spectrum_csv()
        self.assertEqual(len(defaults), 4, defaults)
        self.assertTrue(all(path.parent == project_folder and path.exists() for path in defaults))
        comparison = w.open_plot_comparison()
        with patch('spectra_app.plot_comparison.FilePicker.getSaveFileName', return_value=('', '')) as dialog:
            comparison._choose_export('svg')
        self.assertEqual(Path(dialog.call_args.args[2]).parent, project_folder)
        self.assertIn(str(self.paths[0]), comparison.bridge.sourceText)
        w.start_plot_batch([w.workspace.active_dataset_id], formats=('svg',))
        self.assertEqual(w._plot_batch_basenames[0].parent, project_folder)
        while not w._plot_batch.done:
            w._step_plot_batch()
        self.assertTrue(all(output.path.parent == project_folder
                            for result in w.plot_batch_summary.results for output in result.outputs))


if __name__ == '__main__':
    unittest.main()
