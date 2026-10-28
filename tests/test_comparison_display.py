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
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as formal

from spectra_app.dataset_state import Workspace
from spectra_app.project_io import (ProjectFormatError, read_project, runtime_workspace_stamp,
                                   workspace_digest, write_project)
from spectra_app.workbook_state import (add_curve, create_comparison, create_workbook,
                                       ensure_dataset_sheet, register_xy_sheet, resolve_curve)
from spectra_app.xy_plot_controller import XYPlotController, draw_comparison
from tests.test_project_io import archive_members, overwrite_archive

from tests.xy_helpers import set_xy_draft, xy_value, select_xy_rows, xy_field


def mount_comparison(case):
    case.controller.open_xy_comparison(case.graph.comparison_id)
    documents = case.controller.graph_documents
    case.page = documents.document_for(documents.registry.active_key)
    case.assertIsInstance(case.page, XYPlotController)
    case.host.window.resize(1040, 850)
    formal.pump(100)
    case.assertTrue(case.page.presentation_eligible())


def samples(workspace=None):
    workspace = workspace if workspace is not None else Workspace()
    book = create_workbook(workspace)
    comparison = create_comparison(workspace, 'Three measurements')
    comparison.presentation = None  # These display fixtures exercise the v8 metadata contract.
    curves = []
    for name, values in (
            ('A', [[4, 4], [0, 10000], [2, -2], [3, 3], [2, 2]]),
            ('B', [[5, 10], [1, 2], [3, 6], [2, 4]]),
            ('C', [[2, 20], [4, 40], [3, 30]])):
        sheet = register_xy_sheet(workspace, book.workbook_id, name, np.array(values), ['X', 'Y'])
        curves.append(add_curve(workspace, comparison.comparison_id, sheet.sheet_id, 0, 1, label=name))
    for curve in curves:
        curve.marker_shape, curve.marker_size = 'o', 4.  # Legacy symbol encoding.
    return workspace, comparison, curves


class ComparisonDisplayRenderingTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        self.workspace, self.graph, self.curves = samples(self.controller.workspace)

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
        mount_comparison(self)
        page = self.page
        self.assertEqual(len(page.axes.lines), 2)
        np.testing.assert_equal(page.axes.lines[0].get_ydata(), [0, 0])
        np.testing.assert_equal(page.axes.lines[1].get_ydata(), [0, 0])
        self.assertIn('Missing', page.inspector_bridge.displayStatus)
        self.assertIn('constant', page.inspector_bridge.displayStatus.lower())
        self.graph.x_range_mode, self.graph.x_range = 'manual', (10, 20)
        page.refresh()
        self.assertFalse(page.axes.lines)
        self.assertIn('no samples', page.inspector_bridge.displayStatus.lower())
        self.graph.x_range_mode = 'common'
        sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Distant',
                                 np.array([[5, 1], [6, 2]]), ['X', 'Y'])
        distant = add_curve(self.workspace, self.graph.comparison_id, sheet.sheet_id, 0, 1, label='Distant')
        page.refresh()
        self.assertFalse(page.axes.lines)
        self.assertIn('no common', page.inspector_bridge.displayStatus.lower())
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
        mount_comparison(self)
        page = self.page
        self.assertEqual(page.axes.get_xlim(), (4, 6))
        self.assertEqual(len(page.axes.lines), 1)
        self.assertIn('Sparse: no samples', page.inspector_bridge.displayStatus)


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


class ComparisonDisplayEditorTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        self.workspace, self.graph, self.curves = samples(self.controller.workspace)
        mount_comparison(self)

    def choose(self, field, value):
        if field == 'x_range_mode':
            self.page.inspector_bridge.setRangeMode(value)
        else:
            set_xy_draft(self.page, field, value, commit=True)

    def edit(self, field, value):
        set_xy_draft(self.page, field, value, commit=True)

    def test_ui_modes_precision_selection_styles_and_reset(self):
        page = self.page
        self.choose('normalization', 'maxabs')
        self.choose('x_range_mode', 'common')
        self.edit('stack_step', '1.2345678901234567e-1')
        self.edit('y_offset', '-.9876543210987654')
        select_xy_rows(page, [1])
        self.edit('y_offset', '2')
        set_xy_draft(page, 'color', '#334455')
        page.inspector_bridge.commitField('color')
        select_xy_rows(page, [0])
        page.finish_editor()
        self.assertEqual(self.curves[0].y_offset, -.9876543210987654)
        self.assertEqual(self.curves[1].y_offset, 2)
        self.assertEqual(self.graph.stack_step, .12345678901234567)
        self.assertFalse(page.has_pending_project_edits())
        self.choose('x_range_mode', 'manual')
        self.assertTrue(xy_field(page, 'x_min')[1].isVisible())
        self.assertEqual(self.graph.x_range, (2, 4))
        self.edit('x_min', '2.5e0')
        self.assertEqual(self.graph.x_range, (2.5, 4))
        self.curves[2].visible = False
        base = [(c.label, c.color, c.width, c.visible) for c in self.curves]
        page.inspector_bridge.action('reset')
        self.assertEqual((self.graph.normalization, self.graph.x_range_mode, self.graph.stack_step, self.graph.x_range),
                         ('none', 'all', 0, None))
        self.assertTrue(all(c.y_offset == 0 for c in self.curves))
        self.assertEqual(base, [(c.label, c.color, c.width, c.visible) for c in self.curves])
        self.assertFalse(xy_field(page, 'x_min')[1].isVisible())

    def test_invalid_display_drafts_block_commit_selection_and_preserve_other_drafts(self):
        page = self.page
        self.choose('x_range_mode', 'manual')
        saved = self.graph.x_range
        self.edit('x_min', '9')
        self.assertEqual(self.graph.x_range, saved)
        self.assertIn('less than', page.inspector_bridge.error or page.inspector_bridge.message)
        self.assertTrue(page.has_pending_project_edits())
        with self.assertRaisesRegex(ValueError, 'less than'):
            page.finish_editor()
        select_xy_rows(page, [1])
        self.assertEqual(list(page.comparison.curves).index(page._active_curve_id), 0)
        self.assertEqual(xy_value(page, 'x_min'), '9')
        self.edit('x_min', '1')
        set_xy_draft(page, 'name', 'Pending name')
        set_xy_draft(page, 'color', 'invalid')
        self.choose('normalization', 'minmax')
        self.assertEqual(xy_value(page, 'color'), 'invalid')
        self.assertEqual(xy_value(page, 'name'), 'Pending name')
        self.edit('color', '#334455')
        page.finish_editor()
        self.assertEqual(self.graph.name, 'Pending name')
        self.assertEqual(self.graph.normalization, 'minmax')
        for text in ('nan', 'inf', '-inf', '', 'oops'):
            self.edit('stack_step', text)
            with self.assertRaises(ValueError):
                page.validate_editor()
        self.edit('stack_step', '0')
        self.assertNotIn('finite number', page.inspector_bridge.error or page.inspector_bridge.message)
        self.edit('y_offset', 'nan')
        self.assertIn('finite number', page.inspector_bridge.error or page.inspector_bridge.message)
        self.edit('y_offset', '0')
        self.assertNotIn('finite number', page.inspector_bridge.error or page.inspector_bridge.message)

    def test_manual_seed_uses_configured_samples_and_reset_keeps_unrelated_invalid_draft(self):
        page = self.page
        page.axes.set_xlim(2.2, 2.3)
        self.choose('x_range_mode', 'manual')
        self.assertEqual(self.graph.x_range, (0, 5))
        set_xy_draft(page, 'color', 'invalid color draft')
        set_xy_draft(page, 'name', 'Pending graph name')
        set_xy_draft(page, 'stack_step', 'not a number')
        page.inspector_bridge.action('reset')
        self.assertEqual(xy_value(page, 'color'), 'invalid color draft')
        self.assertEqual(xy_value(page, 'name'), 'Pending graph name')
        self.assertEqual(xy_value(page, 'stack_step'), '0.0')
        self.assertTrue(page.has_pending_project_edits())

    def test_png_svg_clipboard_use_transformed_renderer_and_configured_limits(self):
        page = self.page
        self.choose('normalization', 'maxabs')
        self.choose('x_range_mode', 'common')
        self.edit('stack_step', '2')
        rendered = []
        original = draw_comparison
        def record(*args):
            axes = original(*args)
            rendered.append((axes.get_xlim(), [line.get_ydata().copy() for line in axes.lines], axes.get_ylabel()))
            return axes
        with tempfile.TemporaryDirectory() as directory, patch('spectra_app.xy_plot_controller.draw_comparison', side_effect=record):
            for extension in ('png', 'svg'):
                page.export(Path(directory) / ('display.' + extension))
            self.assertIn(b'<svg', page.svg_bytes())
        self.assertEqual(len(rendered), 3)
        for limits, arrays, label in rendered:
            self.assertEqual(limits, (2, 4))
            np.testing.assert_equal(arrays[0], page.axes.lines[0].get_ydata())
            self.assertIn('offset', label.lower())


class ComparisonDisplayLifecycleTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.errors = self.quick_errors
        self.addCleanup(lambda: self.assertEqual(self.errors, []))

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
        set_xy_draft(page, 'normalization', 'minmax', commit=True)
        page.inspector_bridge.setRangeMode('common')
        set_xy_draft(page, 'stack_step', '2')
        page.inspector_bridge.commitField('stack_step')
        select_xy_rows(page, [1])
        set_xy_draft(page, 'y_offset', '.25')
        page.inspector_bridge.commitField('y_offset')
        base_bytes = session.base_cube.z.tobytes()
        updated = replace(session.current_cube, z=session.current_cube.z ** 2)
        current_bytes = updated.z.tobytes()
        set_xy_draft(page, 'stack_step', 'bad draft')
        w._replace_current_data(updated, mode='energy_second_derivative', smooth=False, window_length=5)
        self.assertEqual(xy_value(page, 'stack_step'), 'bad draft')
        np.testing.assert_allclose(page.axes.lines[1].get_ydata(), [2.25, 2.25 + 1/3, 3.25])
        self.assertEqual(session.base_cube.z.tobytes(), base_bytes)
        self.assertEqual(session.current_cube.z.tobytes(), current_bytes)
        with self.assertRaises(ValueError):
            w.save_project(self.folder / 'invalid.spectra')
        # Processing publishes a heatmap graph; activate the drafted XY owner
        # before testing its document-navigation guard.
        self.assertIs(w.open_xy_comparison(graph.comparison_id), page)
        with self.assertRaises(ValueError):
            w.open_workbook(w._sheet_owner(sheet.sheet_id).workbook_id)
        self.assertFalse(w.graph_documents.close_document())
        set_xy_draft(page, 'stack_step', '2')
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
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Clean saved project unexpectedly requested discard')):
            self.assertTrue(w.open_project(saved))
        reopened = w.open_xy_comparison(graph.comparison_id)
        self.assertEqual(reopened.comparison.stack_step, 2)
        np.testing.assert_allclose(reopened.axes.lines[1].get_ydata(), [2.25, 2.25 + 1/3, 3.25])
        self.assertFalse(self.errors)
