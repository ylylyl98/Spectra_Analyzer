import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import json
from base64 import b64decode
import unittest
from xml.etree import ElementTree
from unittest.mock import patch
import numpy as np
from matplotlib.figure import Figure
from PySide6.QtGui import QImage
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as formal
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve, validate_curve_style
from spectra_app.project_io import read_project, write_project, workspace_digest, runtime_workspace_stamp
from spectra_app.project_io import ProjectFormatError
from spectra_app.xy_plot_controller import XYPlotController, draw_comparison
from tests.test_project_io import archive_members, overwrite_archive

from tests.xy_helpers import set_xy_draft, select_xy_rows


def mount_comparison(case):
    case.controller.open_xy_comparison(case.graph.comparison_id)
    documents = case.controller.graph_documents
    case.page = documents.document_for(documents.registry.active_key)
    case.assertIsInstance(case.page, XYPlotController)
    case.host.window.resize(1040, 850)
    formal.pump(100)
    case.assertTrue(case.page.presentation_eligible())


class CurveSymbolTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        self.workspace = self.controller.workspace
        book = create_workbook(self.workspace, 'Symbols')
        self.sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Source',
            np.array([[0., 1.], [1., 3.], [2., 2.]]), ['X', 'Y'])
        self.graph = create_comparison(self.workspace)
        self.curves = [add_curve(self.workspace, self.graph.comparison_id, self.sheet.sheet_id, 0, 1,
                       label=f'Curve {i}') for i in range(3)]
        mount_comparison(self)

    def select(self, rows):
        select_xy_rows(self.page, rows)

    def test_select_all_and_clear_are_selection_only(self):
        self.curves[1].visible = False
        self.page.refresh()
        before = workspace_digest(self.workspace)
        self.page.inspector_bridge.selectAll(True)
        self.assertEqual(len(list(self.page._selected_ids())), 3)
        self.page.inspector_bridge.selectAll(False)
        self.assertEqual(list(self.page._selected_ids()), [])
        self.assertEqual([c.visible for c in self.curves], [True, False, True])
        self.assertEqual(workspace_digest(self.workspace), before)

    def test_symbol_batch_undo_roundtrip_and_appearance_cache(self):
        self.select([0, 2])
        self.page._set_selected_style('scatter')
        self.page.axes.set_xlim(.4, 1.4)
        artist = self.page.axes.lines[0]
        display = self.page.axes._comparison_display
        before = workspace_digest(self.workspace)
        stamp = runtime_workspace_stamp(self.workspace, {})
        history = len(self.page._edit_history.undo_stack)
        self.page.inspector_bridge.setSymbolTarget('selected')
        set_xy_draft(self.page, 'marker_shape', 'D')
        set_xy_draft(self.page, 'marker_size', '8.25')
        set_xy_draft(self.page, 'marker_fill', 'left')
        set_xy_draft(self.page, 'marker_edge_color', '#113355')
        set_xy_draft(self.page, 'marker_edge_width', '1.25')
        self.page.inspector_bridge.action('symbols')
        self.assertEqual([(c.marker_shape, c.marker_size, c.marker_fill) for c in self.curves],
                         [('D', 8.25, 'left'), ('ball', 5., 'full'), ('D', 8.25, 'left')])
        self.assertEqual(len(self.page._edit_history.undo_stack), history + 1)
        self.assertIs(self.page.axes.lines[0], artist)
        self.assertIs(self.page.axes._comparison_display, display)
        self.assertEqual(self.page.axes.get_xlim(), (.4, 1.4))
        self.assertNotEqual(before, workspace_digest(self.workspace))
        self.assertNotEqual(stamp, runtime_workspace_stamp(self.workspace, {}))
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'symbols.spectra'
            digest = write_project(path, self.workspace)
            restored = read_project(path)
            self.assertEqual(digest, workspace_digest(restored))
            self.assertEqual(restored.comparisons[self.graph.comparison_id], self.graph)
            self.assertEqual(json.loads(archive_members(path)['manifest.json'])['version'], 13)
        self.assertTrue(self.page.undo())
        self.assertEqual(workspace_digest(self.workspace), before)
        self.assertTrue(self.page.redo())
        self.assertEqual(self.curves[2].marker_edge_color, '#113355')

    def test_default_ball_and_legacy_circle_roundtrip(self):
        self.assertEqual((self.curves[0].marker_shape, self.curves[0].marker_size), ('ball', 5.))
        self.curves[0].style = 'line'
        axes = draw_comparison(Figure(), self.workspace, self.graph)
        self.assertEqual(axes.lines[0].get_marker(), 'None')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.spectra'
            write_project(path, self.workspace)
            members = archive_members(path)
            manifest = json.loads(members['manifest.json'])
            manifest['version'] = 12
            fields = ('marker_shape', 'marker_size', 'marker_fill', 'marker_edge_color', 'marker_edge_width')
            for record in manifest['comparisons'][0]['curves']:
                for name in fields:
                    record.pop(name)
            overwrite_archive(path, dict(members, **{'manifest.json': json.dumps(manifest).encode()}))
            restored = read_project(path)
            curve = restored.comparisons[self.graph.comparison_id].curves[self.curves[0].curve_id]
            self.assertEqual((curve.marker_shape, curve.marker_size, curve.marker_fill), ('o', 4., 'full'))
            write_project(path, restored)
            self.assertEqual(json.loads(archive_members(path)['manifest.json'])['version'], 12)

    def test_invalid_symbol_and_pending_drafts_are_atomic(self):
        self.select([0, 1])
        self.page.inspector_bridge.setSymbolTarget('selected')
        before = workspace_digest(self.workspace)
        set_xy_draft(self.page, 'marker_size', 'nan')
        self.page.inspector_bridge.action('symbols')
        self.assertEqual(before, workspace_digest(self.workspace))
        self.assertIn('size', self.page.inspector_bridge.message.lower())
        set_xy_draft(self.page, 'marker_size', '9')
        set_xy_draft(self.page, 'color', 'bad')
        self.page.inspector_bridge.action('symbols')
        self.assertEqual(before, workspace_digest(self.workspace))
        self.assertIn('six-digit', self.page.inspector_bridge.message)
        for field, value in (('marker_size', 0), ('marker_size', True), ('marker_size', float('inf')),
                             ('marker_edge_width', -1), ('marker_edge_width', True),
                             ('marker_fill', 'rainbow'), ('marker_shape', 'bad'), ('marker_edge_color', 'red')):
            with self.subTest(field=field, value=value):
                curve = copy.copy(self.curves[0])
                setattr(curve, field, value)
                with self.assertRaises(ValueError):
                    validate_curve_style(curve)

    def test_symbol_drafts_flush_for_save_export_and_selection_without_loading_side_effects(self):
        self.select([0, 2])
        self.curves[2].marker_size = 12
        self.page.inspector_bridge.setSymbolTarget('selected')
        self.assertFalse(self.page.has_pending_project_edits())
        self.page.finish_editor()
        self.assertEqual(self.curves[2].marker_size, 12)
        set_xy_draft(self.page, 'marker_size', '7.125')
        self.assertTrue(self.page.has_pending_project_edits())
        count = len(self.page._edit_history.undo_stack)
        self.page.finish_editor()
        self.assertFalse(self.page.has_pending_project_edits())
        self.assertEqual([c.marker_size for c in self.curves], [7.125, 5., 7.125])
        self.assertEqual(len(self.page._edit_history.undo_stack), count + 1)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'pending.spectra'
            write_project(path, self.workspace)
            restored = read_project(path).comparisons[self.graph.comparison_id]
            self.assertEqual([c.marker_size for c in restored.curves.values()], [7.125, 5., 7.125])
        self.page.inspector_bridge.setSymbolTarget('current')
        set_xy_draft(self.page, 'marker_size', '6')
        self.page.svg_bytes()
        self.assertEqual([c.marker_size for c in self.curves], [6., 5., 7.125])
        set_xy_draft(self.page, 'marker_size', '9')
        select_xy_rows(self.page, [1])
        self.assertEqual(self.curves[0].marker_size, 9.)
        self.assertEqual(self.curves[1].marker_size, 5.)
        self.assertFalse(self.page.has_pending_project_edits())

    def test_selected_symbol_apply_keeps_an_unselected_current_curve_unchanged(self):
        self.select([0, 2])
        self.page.set_curve_selection(self.page._selected_ids(), list(self.page.comparison.curves)[1])
        self.page.inspector_bridge.setSymbolTarget('selected')
        set_xy_draft(self.page, 'marker_size', '11')
        count = len(self.page._edit_history.undo_stack)
        self.page.inspector_bridge.action('symbols')
        self.assertEqual([c.marker_size for c in self.curves], [11., 5., 11.])
        self.assertEqual(len(self.page._edit_history.undo_stack), count + 1)
        self.assertTrue(self.page.undo())
        self.assertEqual([c.marker_size for c in self.curves], [5., 5., 5.])

    def test_palette_uses_visual_order_one_undo_and_atomic_import(self):
        self.page._set_curve_order([self.curves[i].curve_id for i in (2, 0, 1)])
        self.select([0, 2])
        before = [c.color for c in self.curves]
        count = len(self.page._edit_history.undo_stack)
        self.page.inspector_bridge.setPalette('Set1')
        self.page.inspector_bridge.action('palette')
        self.assertEqual(self.curves[2].color, '#e41a1c')
        self.assertEqual(self.curves[1].color, '#377eb8')
        self.assertEqual(self.curves[0].color, before[0])
        self.assertEqual(len(self.page._edit_history.undo_stack), count + 1)
        self.assertTrue(self.page.undo())
        self.assertEqual([c.color for c in self.curves], before)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'colors.txt'
            path.write_text('#123456\ninvalid', encoding='utf-8')
            palette_count = len(self.page.inspector_bridge.palettes)
            self.assertFalse(self.page.import_palette(path))
            self.assertEqual(len(self.page.inspector_bridge.palettes), palette_count)
            self.assertEqual([c.color for c in self.curves], before)
            path.write_text('#123456\n#abcdef', encoding='utf-8')
            self.assertTrue(self.page.import_palette(path))
            self.page.inspector_bridge.action('palette')
            self.assertEqual((self.curves[2].color, self.curves[1].color), ('#123456', '#abcdef'))

    def test_archive_rejects_invalid_symbol_fields_without_replacing_memory(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.spectra'
            write_project(path, self.workspace)
            original = archive_members(path)
            for field, value in (('marker_size', 0), ('marker_size', True), ('marker_size', float('nan')),
                                 ('marker_edge_width', -1), ('marker_edge_width', float('inf')),
                                 ('marker_shape', 'hexagram'), ('marker_fill', 'quarter'),
                                 ('marker_edge_color', '#12345')):
                with self.subTest(field=field, value=value):
                    manifest = json.loads(original['manifest.json'])
                    manifest['comparisons'][0]['curves'][0][field] = value
                    overwrite_archive(path, dict(original, **{'manifest.json': json.dumps(manifest).encode()}))
                    with self.assertRaises(ProjectFormatError):
                        read_project(path)
                    self.assertEqual(self.curves[0].marker_size, 5.)

    def test_ball_native_vector_export_legend_and_fills(self):
        from spectra_app.curve_symbols import SymbolLine2D
        curve = self.curves[0]
        curve.style = 'scatter'
        self.page.refresh()
        self.page.inspector_bridge.setHighlight(False)
        axes = self.page.axes
        line = axes.lines[0]
        self.assertIsInstance(line, SymbolLine2D)
        self.assertTrue(line.ball)
        self.assertEqual(len(axes.lines), 3)
        handle = axes.get_legend().legend_handles[0]
        self.assertIsInstance(handle, SymbolLine2D)
        self.assertTrue(handle.ball)
        before = (line.get_marker(), line.get_markersize(), line.get_alpha(), line.get_linestyle())
        self.page.canvas.draw()
        self.assertEqual(before, (line.get_marker(), line.get_markersize(), line.get_alpha(), line.get_linestyle()))
        svg = self.page.svg_bytes().decode()
        self.assertNotIn('<image', svg)
        self.assertIn('#ffffff', svg)
        ids = [element.attrib['id'] for element in ElementTree.fromstring(svg).iter() if 'id' in element.attrib]
        self.assertEqual(len(ids), len(set(ids)))
        for fill in ('full', 'none', 'left', 'right', 'top', 'bottom'):
            curve.marker_fill = fill
            self.page.refresh(appearance_only=True)
            self.assertEqual(self.page.axes.lines[0].get_fillstyle(), fill)
            self.page.canvas.draw()
        self.assertEqual({choice['key'] for choice in self.page.inspector_bridge.fills},
                         {'full', 'none', 'left', 'right', 'top', 'bottom'})
        for choice in self.page.inspector_bridge.fills:
            with self.subTest(fill=choice['key']):
                self.assertTrue(choice['icon'].startswith('data:image/png;base64,'))
                image = QImage.fromData(b64decode(choice['icon'].split(',', 1)[1], validate=True))
                self.assertFalse(image.isNull())

    def test_ball_renderer_failure_restores_style_and_curve_identity(self):
        from matplotlib.lines import Line2D
        from spectra_app.curve_symbols import SymbolLine2D
        line = SymbolLine2D([0, 1], [1, 2], marker='o', markersize=5, color='#123456',
                            alpha=.35, gid='curve-id', markevery=[1], linestyle='-')
        line.ball = True
        before = (line.get_marker(), line.get_markersize(), line.get_alpha(), line.get_linestyle(),
                  line.get_markerfacecolor(), line.get_markeredgecolor(), line.get_gid(), line.get_markevery())
        with patch.object(Line2D, 'draw', side_effect=[None, RuntimeError('renderer failure')]):
            with self.assertRaisesRegex(RuntimeError, 'renderer failure'):
                line.draw(self.page.canvas.get_renderer())
        self.assertEqual(before, (line.get_marker(), line.get_markersize(), line.get_alpha(), line.get_linestyle(),
                  line.get_markerfacecolor(), line.get_markeredgecolor(), line.get_gid(), line.get_markevery()))

if __name__ == '__main__':
    unittest.main()
