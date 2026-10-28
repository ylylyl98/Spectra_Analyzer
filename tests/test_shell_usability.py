"""Observable shell actions preserve the visible owner and retained data."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
import sys
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlEngine, QQmlExpression

from spectra_app.common_prompts import PromptAction
from spectra_app.project_io import read_project
from spectra_app.xy_import_dialog import XYImportDialog
from spectra_app.workbook_state import create_workbook, create_comparison, add_curve, ensure_dataset_sheet, resolve_curve, sheet_values
from tests.background_helpers import wait_jobs
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import pump
from tests.project_helpers import project_row, project_label

from tests.xy_helpers import xy_field, click_xy_item
from tests import xy_canvas_quick_helpers as canvas_input
from tests import dat_properties_quick_helpers as dp
from tests.formal_quick_helpers import item, input_receiver


class ShellUsabilityTests(QuickMigrationCase):
    def setUp(self):
        if sys.platform == 'win32' and self._testMethodName == 'test_dat_reload_keeps_selected_auto_axis_when_sidecar_belongs_to_pixel_original':
            # Source-axis ComboBox input needs the native Windows popup route.
            self.enterContext(patch.dict(os.environ, QT_QPA_PLATFORM='windows'))
        super().setUp()
        self.callback_errors = []
        errors = patch.object(sys, 'excepthook', lambda *args: self.callback_errors.append(args))
        errors.start()
        self.addCleanup(errors.stop)
        self.w = self.controller
        self.errors = self.quick_errors
        self.host.window.resize(1500, 920)
        pump()
        self.assertEqual(self.callback_errors, [])
        self.addCleanup(self.dispose)

    def dispose(self):
        wait_jobs(self.w)
        self.assertEqual(self.quick_errors, [])
        self.assertEqual(self.callback_errors, [])

    def source(self, name='table.csv', text='x,a\n1,10\n2,20\n'):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def dat(self):
        path = self.source('spectrum.dat', 'Energy(eV) 0 1\n' + '\n'.join(
            f'{1+i/20} {(i/20)**2} {2*(i/20)**2}' for i in range(31)))
        self.w._load_dat_file_direct(path)
        return self.w.workspace.active

    def select(self, kind, identity):
        project = self.w.app_bridge.project
        row = project.row_for_key((kind, identity))
        self.assertIsNotNone(row, f'Missing Project item: {kind} {identity}')
        project.select_key(row.key)
        return row

    def tooltip(self, control):
        name = ('documentTitleTooltip' if control.objectName() == 'documentTitleBar'
                else 'documentTabTooltip')
        tooltips = [child for child in control.findChildren(QObject)
                    if child.objectName() == name and child.inherits('QQuickToolTip')]
        self.assertEqual(len(tooltips), 1,
                         f'{control.objectName()} needs its actual source-path tooltip binding')
        return tooltips[0]

    def hover_tooltip(self, control, path):
        self.assertTrue(control.isVisible())
        before = self.w.graph_documents.registry.active_key
        point = control.mapToScene(QPointF(min(80, control.width()/2), control.height()/2)).toPoint()
        QTest.mouseMove(self.host.window, point)
        pump(750)
        tooltip = self.tooltip(control)
        self.assertTrue(tooltip.property('visible'))
        self.assertIn(str(path), tooltip.property('text'))
        content = tooltip.property('contentItem')
        self.assertEqual(content.property('text'), tooltip.property('text'))
        expression = QQmlExpression(QQmlEngine.contextForObject(content), content,
                                    'textFormat === Text.PlainText && wrapMode === Text.WrapAnywhere')
        self.assertTrue(expression.evaluate()[0])
        self.assertGreater(content.width(), 0)
        self.assertEqual(self.w.graph_documents.registry.active_key, before)
        QTest.mouseMove(self.host.window, self.host.window.contentItem().mapToScene(QPointF(2, 2)).toPoint())
        pump()

    def test_close_view_during_analysis_cannot_close_hidden_source(self):
        w = self.w
        w.import_xy_files([self.source()])
        session = self.dat()
        source_key = (session.dataset_id, session.active_graph_id)
        source_page = w.open_graph_document(*source_key)
        w.analyze_action.trigger()
        self.assertEqual(w.outerView, 'analysis')
        self.assertFalse(w.close_graph_view_action.isEnabled())
        before = tuple(w.graph_documents.registry.open_keys())
        w._close_visible_document()
        self.assertEqual(tuple(w.graph_documents.registry.open_keys()), before)
        self.assertEqual(w.outerView, 'analysis')
        w._back_to_workspace()
        w.open_graph_document(*source_key)
        w.close_graph_view_action.trigger()
        self.assertNotIn(source_key, w.graph_documents.registry.open_keys())
        pump()
        self.assertIsNone(w.graph_documents.content_item(source_key))
        self.assertIn(source_key[1], session.graphs)
        self.assertIs(w.open_graph_document(*source_key), source_page)

    def test_xy_rescale_keyboard_matches_menu_and_ignores_editor(self):
        w = self.w
        w.import_xy_files([self.source()], compare=True)
        page = w.graph_documents.document_for(w.graph_documents.registry.active_key)
        expected = page.axes.get_xlim()
        page.axes.set_xlim(50, 60)
        canvas_input.key(page, Qt.Key_R, Qt.ControlModifier)
        np.testing.assert_allclose(page.axes.get_xlim(), expected)
        page.axes.set_xlim(50, 60)
        panel, editor = xy_field(page, 'name')
        click_xy_item(panel, editor)
        pump()
        self.assertTrue(editor.hasActiveFocus())
        QTest.keyClick(input_receiver(panel, editor), Qt.Key_R, Qt.ControlModifier)
        np.testing.assert_allclose(page.axes.get_xlim(), (50, 60))
        w._graph_command_actions['rescale'].trigger()
        np.testing.assert_allclose(page.axes.get_xlim(), expected)
        identity = page.comparison_id
        saved = w.save_project(self.folder / 'xy-shortcut.spectra')
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Saved project unexpectedly dirty')):
            self.assertTrue(w.open_project(saved))
        page = w.open_xy_comparison(identity)
        page.axes.set_xlim(50, 60)
        canvas_input.key(page, Qt.Key_R, Qt.ControlModifier)
        np.testing.assert_allclose(page.axes.get_xlim(), expected)

    def test_processing_both_mode_choices_wait_for_apply(self):
        w = self.w
        session = self.dat()
        original = w.current_cube
        w.processing.setMode('energy_second_derivative')
        self.assertIs(w.current_cube, original)
        self.assertIn('Pending: D²', w.processing.status)
        w.processing.apply()
        wait_jobs(w)
        derivative = w.current_cube
        self.assertIsNot(derivative, original)
        source_key = (session.dataset_id, session.active_graph_id)
        w.open_graph_document(*source_key)
        w.analyze_action.trigger()
        frozen = w._workbench_document()
        frozen_values = frozen.science.current_cube.z.copy()
        w.open_graph_document(*source_key)
        w.peak_indices = np.asarray([3])
        w.processing.setMode('original')
        self.assertIs(w.current_cube, derivative)
        self.assertEqual(w.peak_indices.tolist(), [3])
        self.assertIn('Applied: D²', w.processing.status)
        self.assertIn('Pending: Original', w.processing.status)
        w.processing.apply()
        self.assertIs(w.current_cube, original)
        self.assertEqual(w.peak_indices.tolist(), [])
        self.assertNotIn('Pending:', w.processing.status)
        np.testing.assert_equal(frozen.science.current_cube.z, frozen_values)

    def test_pending_original_survives_dataset_switch_without_applying(self):
        w = self.w
        first = self.dat()
        w.processing.setMode('energy_second_derivative')
        w.processing.apply()
        wait_jobs(w)
        processed = first.current_cube
        w.processing.setMode('original')
        other = self.source('other.dat', 'Energy(eV) 0 1\n1 4 5\n2 8 9\n3 12 13\n')
        w._load_dat_file_direct(other)
        w.activate_dataset(first.dataset_id)
        self.assertIs(first.current_cube, processed)
        self.assertEqual(w.processing.mode, 'original')
        self.assertIn('Applied: D²', w.processing.status)
        self.assertIn('Pending: Original', w.processing.status)
        w.processing.apply()
        self.assertIs(first.current_cube, first.base_cube)

    def test_default_reimport_reports_reuse_and_reload_keeps_old_curve_values(self):
        w = self.w
        path = self.source()
        w.import_xy_files([path], compare=True)
        old_sheet = next(iter(w.workspace.sheets.values()))
        graph = next(iter(w.workspace.comparisons.values()))
        curve = next(iter(graph.curves.values()))
        path.write_text('x,a\n1,999\n2,888\n')
        w._import_data_files([path, self.source('new.csv')])
        wait_jobs(w)
        self.assertIn('1 new', w.statusText)
        self.assertIn('1 reused', w.statusText)
        np.testing.assert_equal(resolve_curve(w.workspace, curve)[1], [10, 20])
        self.select('sheet', old_sheet.sheet_id)
        w.reload_source_action.trigger()
        wait_jobs(w)
        copies = [s for s in w.workspace.sheets.values() if s.source_path == path]
        self.assertEqual(len(copies), 2)
        self.assertNotEqual(copies[0].sheet_id, copies[1].sheet_id)
        np.testing.assert_equal(copies[1].data[:, 1], [999, 888])
        np.testing.assert_equal(resolve_curve(w.workspace, curve)[1], [10, 20])
        self.assertNotEqual(w._sheet_owner(copies[0].sheet_id), w._sheet_owner(copies[1].sheet_id))
        w.import_xy_files([path])
        self.assertIn('0 new, 1 reused', w.statusText)
        self.assertEqual(w.graph_documents.registry.active_key[1], w._sheet_owner(old_sheet.sheet_id).workbook_id)
        path.unlink()
        before = (tuple(w.workspace.sheets), tuple(w.workspace.workbooks))
        self.select('sheet', old_sheet.sheet_id)
        w.reload_source_action.trigger()
        wait_jobs(w)
        self.assertEqual((tuple(w.workspace.sheets), tuple(w.workspace.workbooks)), before)
        self.assertEqual(len(self.errors), 1)
        self.assertIn(str(path), self.errors.pop())

    def test_dat_reload_retains_two_scientific_owners_and_roundtrips(self):
        w = self.w
        original = self.dat()
        path = original.source_path
        before = original.base_cube.z.copy()
        old_sheet = ensure_dataset_sheet(w.workspace, original.dataset_id)
        comparison = create_comparison(w.workspace, 'Original graph')
        old_curve = add_curve(w.workspace, comparison.comparison_id, old_sheet.sheet_id, 0, 1)
        path.write_text('Energy(eV) 0 1\n1 999 888\n2 777 666\n3 555 444\n')
        self.select('sheet', old_sheet.sheet_id)
        w.reload_source_action.trigger()
        wait_jobs(w)
        self.assertFalse(self.errors)
        sessions = list(w.workspace.datasets.values())
        self.assertEqual(len(sessions), 2)
        copied = sessions[1]
        self.assertEqual(copied.source_path, original.source_path)
        self.assertIs(w.workspace.dataset_for_path(path), original)
        np.testing.assert_equal(original.base_cube.z, before)
        np.testing.assert_equal(copied.base_cube.z, [[999, 777, 555], [888, 666, 444]])
        self.assertFalse(copied.sidecar_writes_enabled)
        copied_sheet = ensure_dataset_sheet(w.workspace, copied.dataset_id)
        copied_curve = add_curve(w.workspace, comparison.comparison_id, copied_sheet.sheet_id, 0, 1)
        saved = w.save_project(self.folder / 'copies.spectra')
        restored = read_project(saved)
        self.assertEqual(list(restored.datasets), [original.dataset_id, copied.dataset_id])
        self.assertIs(restored.dataset_for_path(path), restored.datasets[original.dataset_id])
        np.testing.assert_equal(restored.datasets[copied.dataset_id].base_cube.z, copied.base_cube.z)
        restored_graph = restored.comparisons[comparison.comparison_id]
        np.testing.assert_equal(resolve_curve(restored, restored_graph.curves[old_curve.curve_id])[1], before[0])
        np.testing.assert_equal(resolve_curve(restored, restored_graph.curves[copied_curve.curve_id])[1], [999, 777, 555])
        restored.remove(copied.dataset_id)
        self.assertIs(restored.dataset_for_path(path), restored.datasets[original.dataset_id])
        restored = read_project(saved)
        restored.remove(original.dataset_id)
        self.assertIs(restored.dataset_for_path(path), restored.datasets[copied.dataset_id])
        path.unlink()
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Saved project unexpectedly dirty')):
            self.assertTrue(w.open_project(saved))
        self.assertEqual(list(w.workspace.datasets), [original.dataset_id, copied.dataset_id])

    def test_dat_reload_keeps_selected_auto_axis_when_sidecar_belongs_to_pixel_original(self):
        w = self.w
        original = self.dat()
        original_sheet = ensure_dataset_sheet(w.workspace, original.dataset_id)
        self.select('sheet', original_sheet.sheet_id)
        w.reload_source_action.trigger()
        wait_jobs(w)
        auto_copy = list(w.workspace.datasets.values())[-1]
        self.assertIsNone(auto_copy.base_cube.x_override)
        copied_sheet = ensure_dataset_sheet(w.workspace, auto_copy.dataset_id)
        comparison = create_comparison(w.workspace, 'Auto copy graph')
        old_curve = add_curve(w.workspace, comparison.comparison_id, copied_sheet.sheet_id, 0, 1)
        old_x, old_y = (values.copy() for values in resolve_curve(w.workspace, old_curve))

        w.open_graph_document(original.dataset_id, original.active_graph_id)
        self.assertTrue(dp.state(w)['source_axis_enabled'],
                        (original.sidecar_writes_enabled, list(original.processing_results), w.contextKind))
        dp.choose(w, 'axis_override', 'pixel')
        wait_jobs(w)
        self.assertEqual(original.base_cube.x_override, 'pixel')
        self.assertEqual(w._save_current_plot_state(), '')
        self.assertTrue(w._plot_state_store.path_for(original.source_path).is_file())

        self.select('sheet', copied_sheet.sheet_id)
        w.reload_source_action.trigger()
        wait_jobs(w)
        self.assertFalse(self.errors)
        self.assertEqual(len(w.workspace.datasets), 3)
        reloaded = list(w.workspace.datasets.values())[-1]
        self.assertIsNone(reloaded.base_cube.x_override)
        self.assertIsNone(auto_copy.base_cube.x_override)
        self.assertEqual(original.base_cube.x_override, 'pixel')
        np.testing.assert_equal(resolve_curve(w.workspace, old_curve)[0], old_x)
        np.testing.assert_equal(resolve_curve(w.workspace, old_curve)[1], old_y)

        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            self.assertTrue(w.new_project())
        w._import_data_files([original.source_path])
        wait_jobs(w)
        self.assertFalse(self.errors)
        self.assertEqual(len(w.workspace.datasets), 1)
        recalled = next(iter(w.workspace.datasets.values()))
        self.assertEqual(recalled.base_cube.x_override, 'pixel')

    def test_reload_preserves_reviewed_xy_type_and_mapping_or_rejects_changed_shape(self):
        w = self.w
        path = self.source('reviewed.csv', 'Energy(eV),2,-1\n1,10,20\n2,30,40\n')
        def review(dialog):
            self.assertEqual(dialog.entries[0].kind, 'dat')
            dialog.bridge.setKind('xy')
            dialog.mappings = [(2, [0])]
            return XYImportDialog.Accepted
        with patch.object(XYImportDialog, 'exec', review):
            w._import_data_files([path], review=True)
            wait_jobs(w)
        original = next(iter(w.workspace.sheets.values()))
        self.assertEqual((original.kind, original.x_column, original.y_columns), ('xy', 2, [0]))
        graph = create_comparison(w.workspace)
        curve = add_curve(w.workspace, graph.comparison_id, original.sheet_id, 2, 0)
        old_values = original.data.copy()
        path.write_text('Energy(eV),2,-1\n1,999,333\n2,888,222\n')
        self.select('sheet', original.sheet_id)
        w.reload_source_action.trigger()
        wait_jobs(w)
        copied = list(w.workspace.sheets.values())[-1]
        self.assertEqual((copied.kind, copied.x_column, copied.y_columns), ('xy', 2, [0]))
        np.testing.assert_equal(copied.data, [[1, 999, 333], [2, 888, 222]])
        np.testing.assert_equal(original.data, old_values)
        np.testing.assert_equal(resolve_curve(w.workspace, curve)[0], [20, 40])
        self.assertFalse(w.workspace.datasets)
        self.assertFalse(self.errors)
        path.write_text('x,a\n1,3\n2,4\n')
        self.select('sheet', original.sheet_id)
        before = (tuple(w.workspace.sheets), tuple(w.workspace.workbooks), tuple(w.workspace.datasets))
        w.reload_source_action.trigger()
        wait_jobs(w)
        self.assertEqual((tuple(w.workspace.sheets), tuple(w.workspace.workbooks), tuple(w.workspace.datasets)), before)
        self.assertEqual(len(self.errors), 1)
        error = self.errors.pop()
        self.assertIn(str(path), error)
        self.assertIn('mapping', error)

    def test_colliding_names_have_distinct_titles_paths_and_active_marker(self):
        w = self.w
        paths = [self.source('left/shared/table.csv'), self.source('right/shared/table.csv')]
        w.import_xy_files(paths)
        books = list(w.workspace.workbooks.values())
        for book in books:
            w.open_workbook(book.workbook_id)
        first = self.select('workbook', books[0].workbook_id)
        project = w.app_bridge.project
        project.select_key(('workbook', books[1].workbook_id), Qt.ControlModifier)
        selection = set(project.selected_keys())
        w.open_workbook(books[1].workbook_id)
        w._refresh_data_navigator()
        first, second = [project.row_for_key(('workbook', book.workbook_id)) for book in books]
        self.assertEqual(set(project.selected_keys()), selection)
        self.assertNotEqual(first.label, second.label)
        self.assertIn('left', first.label)
        self.assertIn('right', second.label)
        self.assertFalse(project_label(w, first.key).property('font').underline())
        self.assertTrue(project_label(w, second.key).property('font').underline())
        self.assertFalse(project_row(w, first.key).property('active'))
        self.assertTrue(project_row(w, second.key).property('active'))
        w.graph_documents.setMode('free')
        pump()
        for row, book, path in zip((first, second), books, paths):
            key = ('workbook', book.workbook_id, '')
            w.open_workbook(book.workbook_id)
            pump()
            frame = w.graph_documents._presentations[key].frame
            self.assertIsNotNone(frame)
            self.assertEqual(frame.property('documentTitle'), row.label)
            titlebar = item(frame, 'documentTitleBar')
            self.hover_tooltip(titlebar, path)
            self.assertIn(str(path), row.tooltip)
            self.assertEqual(book.name, 'table.csv')
        self.select('workbook', books[0].workbook_id)
        self.assertIn(first.label, w._navigator_buttons['Remove'].text())
        w.graph_documents.setMode('tabbed')
        pump()
        for book, path in zip(books, paths):
            key = ('workbook', book.workbook_id, '')
            tab = item(w, 'documentTab_' + '_'.join(key))
            self.assertEqual(tab.property('text'), project.row_for_key(('workbook', book.workbook_id)).label)
            self.hover_tooltip(tab, path)

    def test_project_dat_remove_routes_source_and_disables_projection(self):
        w = self.w
        session = self.dat()
        original = ensure_dataset_sheet(w.workspace, session.dataset_id)
        projection = ensure_dataset_sheet(w.workspace, session.dataset_id, 'processed')
        w._refresh_data_navigator()
        self.select('sheet', projection.sheet_id)
        self.assertFalse(w._navigator_buttons['Remove'].isEnabled())
        self.assertIn('Original', w._navigator_buttons['Remove'].toolTip())
        self.select('sheet', original.sheet_id)
        self.assertTrue(w._navigator_buttons['Remove'].isEnabled())
        self.select('workbook', w._sheet_owner(original.sheet_id).workbook_id)
        self.assertTrue(w._navigator_buttons['Remove'].isEnabled())
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as warning:
            w._navigator_buttons['Remove'].trigger()
        self.assertEqual(warning.call_args.args[-1], PromptAction.CANCEL)
        self.assertIn(session.dataset_id, w.workspace.datasets)
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.YES):
            w._navigator_buttons['Remove'].trigger()
        self.assertNotIn(session.dataset_id, w.workspace.datasets)
        self.assertTrue(session.source_path.exists())
        self.assertFalse(self.errors)

    def test_mixed_workbook_remove_selects_one_dat_source_and_keeps_other_sheets(self):
        w = self.w
        first = self.dat()
        first_sheet = ensure_dataset_sheet(w.workspace, first.dataset_id)
        other = self.source('other.dat', 'Energy(eV) 0 1\n1 4 5\n2 8 9\n3 12 13\n')
        w._load_dat_file_direct(other)
        second = w.workspace.active
        w.import_xy_files([self.source()])
        books = list(w.workspace.workbooks)
        merged = w.merge_workbooks(books, books[0])
        self.select('workbook', merged.workbook_id)
        self.assertFalse(w._navigator_buttons['Remove'].isEnabled())
        self.assertIn('multiple sources', w._navigator_buttons['Remove'].toolTip())
        self.select('sheet', first_sheet.sheet_id)
        retained = set(w.workspace.sheets) - {s.sheet_id for s in w.workspace.sheets.values() if s.dataset_id == first.dataset_id}
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.YES):
            w._navigator_buttons['Remove'].trigger()
        self.assertIn(second.dataset_id, w.workspace.datasets)
        self.assertEqual(set(w.workspace.sheets), retained)
        self.assertIn(merged.workbook_id, w.workspace.workbooks)
        self.assertTrue(first.source_path.exists())
        self.assertTrue(second.source_path.exists())
        self.assertFalse(self.errors)

    def test_reload_mixed_workbook_is_atomic_when_one_source_is_invalid(self):
        w = self.w
        session = self.dat()
        xy = self.source()
        w.import_xy_files([xy])
        books = list(w.workspace.workbooks)
        merged = w.merge_workbooks(books, books[0])
        self.select('workbook', merged.workbook_id)
        xy.write_text('no numeric data remains\n')
        before = (tuple(w.workspace.datasets), tuple(w.workspace.sheets), tuple(w.workspace.workbooks))
        values = session.base_cube.z.copy()
        w.reload_source_action.trigger()
        wait_jobs(w)
        self.assertEqual((tuple(w.workspace.datasets), tuple(w.workspace.sheets), tuple(w.workspace.workbooks)), before)
        np.testing.assert_equal(session.base_cube.z, values)
        self.assertEqual(len(self.errors), 1)
        error = self.errors.pop()
        self.assertIn(str(xy), error)
        self.assertIn('No sources were added', error)

    def test_manual_duplicate_workbook_names_remain_distinct_without_renaming(self):
        w = self.w
        books = [create_workbook(w.workspace, 'Results') for _ in range(2)]
        for book in books:
            w.open_workbook(book.workbook_id)
        w._refresh_document_titles()
        labels = [w.app_bridge.project.row_for_key(('workbook', book.workbook_id)).label for book in books]
        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(all('embedded' in label for label in labels))
        self.assertEqual([book.name for book in books], ['Results', 'Results'])

    def test_merge_preview_cancel_preserves_sheets_and_confirm_moves_them(self):
        w = self.w
        w.import_xy_files([self.source('a/table.csv'), self.source('b/table.csv')], compare=True)
        books = list(w.workspace.workbooks.values())
        expected_sheets = list(w.workspace.sheets)
        expected_curves = list(next(iter(w.workspace.comparisons.values())).curves)
        self.select('workbook', books[0].workbook_id)
        w.app_bridge.project.select_key(('workbook', books[1].workbook_id), Qt.ControlModifier)
        def destination(*args, **kwargs):
            self.assertNotEqual(args[3][0], args[3][1])
            return args[3][0], True
        with patch('spectra_app.main_window.prompt_item', side_effect=destination), \
                patch('spectra_app.main_window.prompt_message', return_value=PromptAction.CANCEL) as preview:
            w.merge_workbooks_btn.trigger()
        self.assertEqual(list(w.workspace.workbooks), [b.workbook_id for b in books])
        self.assertEqual(preview.call_args.args[3:], ((PromptAction.YES, PromptAction.CANCEL), PromptAction.CANCEL))
        self.assertEqual(preview.call_args.kwargs, {'kind': 'warning'})
        self.assertIn('2 Workbooks', preview.call_args.args[2])
        self.assertIn('2 Sheets', preview.call_args.args[2])
        self.assertIn('cannot be undone', preview.call_args.args[2])
        with patch('spectra_app.main_window.prompt_item', side_effect=destination), \
                patch('spectra_app.main_window.prompt_message', return_value=PromptAction.YES):
            w.merge_workbooks_btn.trigger()
        self.assertEqual(list(w.workspace.workbooks), [books[0].workbook_id])
        self.assertEqual(books[0].sheet_ids, expected_sheets)
        self.assertEqual(list(next(iter(w.workspace.comparisons.values())).curves), expected_curves)


if __name__ == '__main__':
    unittest.main()
