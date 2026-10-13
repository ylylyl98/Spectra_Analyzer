import copy
from dataclasses import replace
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QApplication

from spectra_app.dataset_state import Workspace
from spectra_app.project_io import (ProjectFormatError, read_project, runtime_workspace_stamp,
                                   workspace_digest, write_project)
from spectra_app.workbook_state import (add_curve, create_comparison, create_workbook,
                                       ensure_dataset_sheet, register_xy_sheet, resolve_curve)
from spectra_app.xy_plot_page import XYPlotPage, draw_comparison
from tests.test_project_io import archive_members, overwrite_archive

APP = QApplication.instance() or QApplication([])


def samples():
    workspace = Workspace()
    book = create_workbook(workspace)
    comparison = create_comparison(workspace, 'Three measurements')
    curves = []
    for name, values in (
            ('A', [[4, 4], [0, 10000], [2, -2], [3, 3], [2, 2]]),
            ('B', [[5, 10], [1, 2], [3, 6], [2, 4]]),
            ('C', [[2, 20], [4, 40], [3, 30]])):
        sheet = register_xy_sheet(workspace, book.workbook_id, name, np.array(values), ['X', 'Y'])
        curves.append(add_curve(workspace, comparison.comparison_id, sheet.sheet_id, 0, 1, label=name))
    return workspace, comparison, curves


class ComparisonDisplayRenderingTests(unittest.TestCase):
    def setUp(self):
        self.workspace, self.graph, self.curves = samples()

    def draw(self):
        return draw_comparison(Figure(), self.workspace, self.graph)

    def test_neutral_defaults_and_common_interval_normalization_offsets_preserve_samples(self):
        self.assertEqual(self.graph.normalization, 'none')
        self.assertEqual(self.graph.x_range_mode, 'all')
        before = {k: s.data.tobytes() for k, s in self.workspace.sheets.items()}
        for line, curve in zip(self.draw().lines, self.curves):
            x, y = resolve_curve(self.workspace, curve)
            np.testing.assert_equal(line.get_xdata(), x)
            np.testing.assert_equal(line.get_ydata(), y)
        self.graph.normalization = 'maxabs'
        self.graph.x_range_mode = 'common'
        self.graph.stack_step = 2
        self.curves[2].y_offset = -.5
        axes = self.draw()
        self.assertEqual(axes.get_xlim(), (2, 4))
        for line, expected in zip(axes.lines, ([1, np.nan, -.5, .75, .5],
                                               [np.nan, np.nan, 3, 2 + 2/3], [4, 4.5, 4.25])):
            np.testing.assert_allclose(line.get_ydata(), expected)
        self.assertLess(axes.get_ylim()[1], 5)
        self.assertIn('max', axes.get_ylabel().lower())
        self.assertIn('offset', axes.get_ylabel().lower())
        self.assertEqual(before, {k: s.data.tobytes() for k, s in self.workspace.sheets.items()})
        self.curves[1].visible = False
        axes = self.draw()
        np.testing.assert_allclose(axes.lines[1].get_ydata(), [4, 4.5, 4.25])

    def test_manual_domain_minmax_and_transient_zoom_do_not_change_reference(self):
        self.graph.normalization = 'minmax'
        self.graph.x_range_mode = 'manual'
        self.graph.x_range = (2., 3.)
        axes = self.draw()
        np.testing.assert_allclose(axes.lines[0].get_ydata(), [np.nan, np.nan, 0, 1, .8])
        self.assertEqual(axes.get_xlim(), (2, 3))
        axes.set_xlim(2.4, 2.6)
        self.assertEqual(self.draw().get_xlim(), (2, 3))

    def test_empty_nonfinite_disjoint_zero_and_constant_feedback(self):
        self.graph.curves.clear()
        book = next(iter(self.workspace.workbooks.values()))
        for name, values in (('Missing', [[0, np.nan], [1, np.inf]]),
                             ('Zero', [[0, 0], [1, 0]]), ('Constant', [[0, 7], [1, 7]])):
            sheet = register_xy_sheet(self.workspace, book.workbook_id, name, np.array(values), ['X', 'Y'])
            add_curve(self.workspace, self.graph.comparison_id, sheet.sheet_id, 0, 1, label=name)
        self.graph.normalization = 'minmax'
        page = XYPlotPage(self.workspace, self.graph.comparison_id)
        self.addCleanup(page.deleteLater)
        self.assertEqual(len(page.axes.lines), 2)
        np.testing.assert_equal(page.axes.lines[0].get_ydata(), [0, 0])
        np.testing.assert_equal(page.axes.lines[1].get_ydata(), [0, 0])
        self.assertIn('Missing', page.display_status.text())
        self.assertIn('constant', page.display_status.text().lower())
        self.graph.x_range_mode, self.graph.x_range = 'manual', (10, 20)
        page.refresh()
        self.assertFalse(page.axes.lines)
        self.assertIn('no samples', page.display_status.text().lower())
        self.graph.x_range_mode = 'common'
        sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Distant',
                                 np.array([[5, 1], [6, 2]]), ['X', 'Y'])
        distant = add_curve(self.workspace, self.graph.comparison_id, sheet.sheet_id, 0, 1, label='Distant')
        page.refresh()
        self.assertFalse(page.axes.lines)
        self.assertIn('no common', page.display_status.text().lower())
        distant.visible = False
        page.refresh()
        self.assertEqual(page.axes.get_xlim(), (0, 1))
        self.assertEqual(len(page.axes.lines), 2)

    def test_large_finite_arithmetic_nonfinite_gaps_single_sample_and_offset_overflow(self):
        self.graph.curves.clear()
        book = next(iter(self.workspace.workbooks.values()))
        sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Huge',
            np.array([[0, -1e308], [1, 0], [2, 1e308], [3, np.inf], [np.nan, 8]]), ['X', 'Y'])
        curve = add_curve(self.workspace, self.graph.comparison_id, sheet.sheet_id, 0, 1, label='Huge')
        before = sheet.data.tobytes()
        self.graph.normalization = 'minmax'
        np.testing.assert_equal(self.draw().lines[0].get_ydata(), [0, .5, 1, np.nan, np.nan])
        self.graph.normalization = 'maxabs'
        np.testing.assert_equal(self.draw().lines[0].get_ydata(), [-1, 0, 1, np.nan, np.nan])
        self.graph.normalization = 'minmax'
        self.graph.x_range_mode, self.graph.x_range = 'manual', (.5, 1.5)
        np.testing.assert_equal(self.draw().lines[0].get_ydata(), [np.nan, 0, np.nan, np.nan, np.nan])
        self.graph.x_range_mode, self.graph.normalization = 'all', 'none'
        curve.y_offset = 1e308
        axes = self.draw()
        self.assertTrue(np.isnan(axes.lines[0].get_ydata()[2]))
        self.assertEqual(sheet.data.tobytes(), before)

    def test_zero_maxabs_signed_integer_minimum_and_empty_or_touching_common_range(self):
        self.graph.curves.clear()
        book = next(iter(self.workspace.workbooks.values()))
        a = register_xy_sheet(self.workspace, book.workbook_id, 'Integer',
            np.array([[0, np.iinfo(np.int64).min], [1, 0]], dtype=np.int64), ['X', 'Y'])
        b = register_xy_sheet(self.workspace, book.workbook_id, 'Zero', np.array([[1, 0], [2, 0]]), ['X', 'Y'])
        add_curve(self.workspace, self.graph.comparison_id, a.sheet_id, 0, 1)
        add_curve(self.workspace, self.graph.comparison_id, b.sheet_id, 0, 1)
        self.graph.normalization = 'maxabs'
        before = a.data.tobytes()
        axes = self.draw()
        np.testing.assert_equal(axes.lines[0].get_ydata(), [-1, 0])
        np.testing.assert_equal(axes.lines[1].get_ydata(), [0, 0])
        self.assertEqual(a.data.tobytes(), before)
        self.graph.x_range_mode = 'common'
        self.assertFalse(self.draw().lines, 'A single shared endpoint has no positive common width')
        self.graph.curves.clear()
        self.assertFalse(self.draw().lines)

    def test_common_extents_use_only_finite_pairs_and_sparse_curve_omission_is_named(self):
        self.graph.curves.clear()
        book = next(iter(self.workspace.workbooks.values()))
        for name, values in (('Sparse', [[0, 1], [10, 2], [100, np.nan]]),
                             ('Dense', [[4, 4], [5, 5], [6, 6]])):
            sheet = register_xy_sheet(self.workspace, book.workbook_id, name, np.array(values), ['X', 'Y'])
            add_curve(self.workspace, self.graph.comparison_id, sheet.sheet_id, 0, 1, label=name)
        self.graph.x_range_mode = 'common'
        page = XYPlotPage(self.workspace, self.graph.comparison_id)
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.axes.get_xlim(), (4, 6))
        self.assertEqual(len(page.axes.lines), 1)
        self.assertIn('Sparse: no samples', page.display_status.text())


class ComparisonDisplayPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'view.spectra'
        self.workspace, self.graph, self.curves = samples()

    def test_each_display_field_dirties_roundtrips_and_stores_only_metadata(self):
        initial_arrays = None
        for obj, attr, value in ((self.graph, 'normalization', 'maxabs'),
                                  (self.graph, 'stack_step', .12345678901234567),
                                  (self.graph, 'x_range', (2., 3.)),
                                  (self.graph, 'x_range_mode', 'manual'),
                                  (self.curves[1], 'y_offset', -.9876543210987654)):
            exact, runtime = workspace_digest(self.workspace), runtime_workspace_stamp(self.workspace, {})
            setattr(obj, attr, value)
            self.assertNotEqual(exact, workspace_digest(self.workspace), attr)
            self.assertNotEqual(runtime, runtime_workspace_stamp(self.workspace, {}), attr)
            digest = write_project(self.path, self.workspace)
            restored = read_project(self.path)
            self.assertEqual(restored.comparisons[self.graph.comparison_id], self.graph)
            self.assertEqual(workspace_digest(restored), digest)
            members = archive_members(self.path)
            arrays = {k: v for k, v in members.items() if k.endswith('.npy')}
            initial_arrays = arrays if initial_arrays is None else initial_arrays
            self.assertEqual(arrays, initial_arrays)
        self.assertEqual(json.loads(members['manifest.json'])['version'], 8)

    def test_v4_neutral_migration_keeps_strict_old_field_sets(self):
        write_project(self.path, self.workspace)
        members = archive_members(self.path)
        manifest = json.loads(members['manifest.json'])
        manifest['version'] = 4
        for sheet in manifest['sheets']:
            for field in ('role', 'fit_gate_idx', 'text_columns', 'snapshot_note'):
                sheet.pop(field)
        for comparison in manifest['comparisons']:
            for field in ('normalization', 'stack_step', 'x_range_mode', 'x_range', 'plot_title'):
                comparison.pop(field, None)
            for curve in comparison['curves']:
                curve.pop('y_offset', None)
                curve.pop('style')
        def install(value):
            overwrite_archive(self.path, dict(members, **{'manifest.json': json.dumps(value).encode()}))
        install(manifest)
        graph = read_project(self.path).comparisons[self.graph.comparison_id]
        self.assertEqual((graph.normalization, graph.stack_step, graph.x_range_mode, graph.x_range),
                         ('none', 0, 'all', None))
        self.assertTrue(all(c.y_offset == 0 for c in graph.curves.values()))
        for path in ('graph', 'curve'):
            malformed = copy.deepcopy(manifest)
            record = malformed['comparisons'][0]
            if path == 'curve':
                record = record['curves'][0]
            record['extra'] = 1
            install(malformed)
            with self.assertRaises(ProjectFormatError):
                read_project(self.path)

    def test_v8_rejects_malformed_display_metadata_on_save_and_load(self):
        write_project(self.path, self.workspace)
        members = archive_members(self.path)
        manifest = json.loads(members['manifest.json'])
        self.assertEqual(manifest['version'], 8)
        for target, attr, value in (
                ('graph', 'normalization', 'baseline'), ('graph', 'normalization', []),
                ('graph', 'stack_step', True), ('graph', 'stack_step', float('inf')),
                ('graph', 'x_range_mode', 'linked'), ('graph', 'x_range_mode', 'manual'),
                ('graph', 'x_range', [2, 1]), ('graph', 'x_range', [2, 2]),
                ('graph', 'x_range', [True, 2]), ('graph', 'x_range', [0, float('nan')]),
                ('graph', 'x_range', [0]), ('curve', 'y_offset', False),
                ('curve', 'y_offset', float('nan')), ('curve', 'y_offset', '2')):
            with self.subTest(target=target, attr=attr, value=value):
                malformed = copy.deepcopy(manifest)
                record = malformed['comparisons'][0]
                obj = self.graph
                if target == 'curve':
                    record, obj = record['curves'][0], self.curves[0]
                record[attr] = value
                overwrite_archive(self.path, dict(members, **{'manifest.json': json.dumps(malformed).encode()}))
                with self.assertRaises(ProjectFormatError):
                    read_project(self.path)
                previous = getattr(obj, attr)
                setattr(obj, attr, value)
                try:
                    with self.assertRaises(ProjectFormatError):
                        write_project(self.path, self.workspace)
                finally:
                    setattr(obj, attr, previous)


class ComparisonDisplayEditorTests(unittest.TestCase):
    def setUp(self):
        self.workspace, self.graph, self.curves = samples()
        self.page = XYPlotPage(self.workspace, self.graph.comparison_id)
        self.addCleanup(self.page.deleteLater)

    def choose(self, combo, value):
        combo.setCurrentIndex(combo.findData(value))

    def edit(self, edit, value):
        edit.setText(value)
        edit.editingFinished.emit()

    def test_ui_modes_precision_selection_styles_and_reset(self):
        page = self.page
        self.choose(page.normalization_combo, 'maxabs')
        self.choose(page.x_range_combo, 'common')
        self.edit(page.stack_step_edit, '1.2345678901234567e-1')
        self.edit(page.y_offset_edit, '-.9876543210987654')
        page.curve_list.setCurrentRow(1)
        self.edit(page.y_offset_edit, '2')
        page.color_edit.setText('#334455')
        page.color_edit.editingFinished.emit()
        page.curve_list.setCurrentRow(0)
        page.finish_editor()
        self.assertEqual(self.curves[0].y_offset, -.9876543210987654)
        self.assertEqual(self.curves[1].y_offset, 2)
        self.assertEqual(self.graph.stack_step, .12345678901234567)
        self.assertFalse(page.has_pending_project_edits())
        self.choose(page.x_range_combo, 'manual')
        self.assertTrue(page.x_min_edit.isEnabled())
        self.assertEqual(self.graph.x_range, (2, 4))
        self.edit(page.x_min_edit, '2.5e0')
        self.assertEqual(self.graph.x_range, (2.5, 4))
        self.curves[2].visible = False
        base = [(c.label, c.color, c.width, c.visible) for c in self.curves]
        page.reset_display_btn.click()
        self.assertEqual((self.graph.normalization, self.graph.x_range_mode, self.graph.stack_step, self.graph.x_range),
                         ('none', 'all', 0, None))
        self.assertTrue(all(c.y_offset == 0 for c in self.curves))
        self.assertEqual(base, [(c.label, c.color, c.width, c.visible) for c in self.curves])
        self.assertFalse(page.x_min_edit.isEnabled())

    def test_invalid_display_drafts_block_commit_selection_and_preserve_other_drafts(self):
        page = self.page
        self.choose(page.x_range_combo, 'manual')
        saved = self.graph.x_range
        self.edit(page.x_min_edit, '9')
        self.assertEqual(self.graph.x_range, saved)
        self.assertIn('less than', page.display_status.text())
        self.assertTrue(page.has_pending_project_edits())
        with self.assertRaisesRegex(ValueError, 'less than'):
            page.finish_editor()
        page.curve_list.setCurrentRow(1)
        self.assertEqual(page.curve_list.currentRow(), 0)
        self.assertEqual(page.x_min_edit.text(), '9')
        self.edit(page.x_min_edit, '1')
        page.name_edit.setText('Pending name')
        page.color_edit.setText('invalid')
        self.choose(page.normalization_combo, 'minmax')
        self.assertEqual(page.color_edit.text(), 'invalid')
        self.assertEqual(page.name_edit.text(), 'Pending name')
        self.edit(page.color_edit, '#334455')
        page.finish_editor()
        self.assertEqual(self.graph.name, 'Pending name')
        self.assertEqual(self.graph.normalization, 'minmax')
        for text in ('nan', 'inf', '-inf', '', 'oops'):
            self.edit(page.stack_step_edit, text)
            with self.assertRaises(ValueError):
                page.validate_editor()
        self.edit(page.stack_step_edit, '0')
        self.assertNotIn('finite number', page.display_status.text())
        self.edit(page.y_offset_edit, 'nan')
        self.assertIn('finite number', page.display_status.text())
        self.edit(page.y_offset_edit, '0')
        self.assertNotIn('finite number', page.display_status.text())

    def test_manual_seed_uses_configured_samples_and_reset_keeps_unrelated_invalid_draft(self):
        page = self.page
        page.axes.set_xlim(2.2, 2.3)
        self.choose(page.x_range_combo, 'manual')
        self.assertEqual(self.graph.x_range, (0, 5))
        page.color_edit.setText('invalid color draft')
        page.name_edit.setText('Pending graph name')
        page.stack_step_edit.setText('not a number')
        page.reset_display_btn.click()
        self.assertEqual(page.color_edit.text(), 'invalid color draft')
        self.assertEqual(page.name_edit.text(), 'Pending graph name')
        self.assertEqual(page.stack_step_edit.text(), '0.0')
        self.assertTrue(page.has_pending_project_edits())

    def test_png_svg_clipboard_use_transformed_renderer_and_configured_limits(self):
        page = self.page
        self.choose(page.normalization_combo, 'maxabs')
        self.choose(page.x_range_combo, 'common')
        self.edit(page.stack_step_edit, '2')
        rendered = []
        original = draw_comparison
        def record(*args):
            axes = original(*args)
            rendered.append((axes.get_xlim(), [line.get_ydata().copy() for line in axes.lines], axes.get_ylabel()))
            return axes
        with tempfile.TemporaryDirectory() as directory, patch('spectra_app.xy_plot_page.draw_comparison', side_effect=record):
            for extension in ('png', 'svg'):
                page.export(Path(directory) / ('display.' + extension))
            self.assertIn(b'<svg', page.svg_bytes())
        self.assertEqual(len(rendered), 3)
        for limits, arrays, label in rendered:
            self.assertEqual(limits, (2, 4))
            np.testing.assert_equal(arrays[0], page.axes.lines[0].get_ydata())
            self.assertIn('offset', label.lower())


class ComparisonDisplayLifecycleTests(unittest.TestCase):
    def setUp(self):
        from spectra_app.main_window import MainWindow
        from tests.settings_helpers import isolate_settings
        self.folder = isolate_settings(self).parent
        self.window = MainWindow()
        self.errors = []
        self.window._show_error = self.errors.append
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window._confirm_project_discard = lambda: True
        self.window.close()
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def test_actual_ui_live_dat_add_close_reopen_and_source_free_project(self):
        w = self.window
        path = self.folder / 'measurement.dat'
        path.write_text('Energy(eV) 0 1\n1 10 20\n2 30 40\n3 50 60\n', encoding='utf-8')
        w._load_dat_file_direct(path)
        session = w.workspace.active
        sheet = ensure_dataset_sheet(w.workspace, session.dataset_id)
        graph = create_comparison(w.workspace)
        raw = add_curve(w.workspace, graph.comparison_id, sheet.sheet_id, 0, 1)
        processed = add_curve(w.workspace, graph.comparison_id, sheet.sheet_id, 0, 1, mode='processed')
        page = w.open_xy_comparison(graph.comparison_id)
        page.normalization_combo.setCurrentIndex(page.normalization_combo.findData('minmax'))
        page.x_range_combo.setCurrentIndex(page.x_range_combo.findData('common'))
        page.stack_step_edit.setText('2')
        page.stack_step_edit.editingFinished.emit()
        page.curve_list.setCurrentRow(1)
        page.y_offset_edit.setText('.25')
        page.y_offset_edit.editingFinished.emit()
        base_bytes = session.base_cube.z.tobytes()
        updated = replace(session.current_cube, z=session.current_cube.z ** 2)
        current_bytes = updated.z.tobytes()
        page.stack_step_edit.setText('bad draft')
        w._replace_current_data(updated, mode='energy_second_derivative', smooth=False, window_length=5)
        self.assertEqual(page.stack_step_edit.text(), 'bad draft')
        np.testing.assert_allclose(page.axes.lines[1].get_ydata(), [2.25, 2.25 + 1/3, 3.25])
        self.assertEqual(session.base_cube.z.tobytes(), base_bytes)
        self.assertEqual(session.current_cube.z.tobytes(), current_bytes)
        with self.assertRaises(ValueError):
            w.save_project(self.folder / 'invalid.spectra')
        with self.assertRaises(ValueError):
            w.open_workbook(w._sheet_owner(sheet.sheet_id).workbook_id)
        self.assertFalse(w.graph_documents.close_document())
        page.stack_step_edit.setText('2')
        page.finish_editor()
        # Use the real batch append boundary; display settings and insertion ordinal survive.
        w.compare_sheets({sheet.sheet_id: (0, [2], 'raw')}, graph.comparison_id)
        self.assertEqual(len(graph.curves), 3)
        self.assertEqual(graph.stack_step, 2)
        np.testing.assert_allclose(page.axes.lines[2].get_ydata(), [4, 4.5, 5])
        self.assertTrue(w.graph_documents.close_document())
        self.assertIs(w.open_xy_comparison(graph.comparison_id), page)
        self.assertEqual(processed.y_offset, .25)
        saved = w.save_project(self.folder / 'display.spectra')
        path.unlink()
        self.assertTrue(w.open_project(saved))
        reopened = w.open_xy_comparison(graph.comparison_id)
        self.assertEqual(reopened.comparison.stack_step, 2)
        np.testing.assert_allclose(reopened.axes.lines[1].get_ydata(), [2.25, 2.25 + 1/3, 3.25])
        self.assertFalse(self.errors)
