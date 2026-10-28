"""Quick input commands and persisted plot semantics, without source mutation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import json
import unittest
import zipfile
from dataclasses import replace
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QTest

from spectra_app.plot_snapshot import snapshot_dataset
from spectra_app.project_io import read_project, write_project, ProjectFormatError
from spectra_app.plot_state import validate_plot_collection, PlotStateStore
from spectra_app.dataset_state import initialize_dataset
from spectra_app.dat_io import load_dat_cube
from PIL import Image
from spectra_app.workbook_state import ensure_dataset_sheet
from tests.quick_migration_case import QuickMigrationCase

from tests.xy_helpers import set_xy_draft, xy_field
from tests.workbook_quick_helpers import (click_header, click_item, context_action, drag_header,
    host_for, item, point_in, pump, set_metadata_draft, table_key, header_point, reveal_column)

from tests.xy_canvas_quick_helpers import command as xy_command
from tests.formal_quick_helpers import input_receiver


class QuickPlotTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        self.errors = self.quick_errors
        self.host.window.resize(1500, 920)
        pump()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.assertEqual(self.errors, [])

    def xy(self):
        path = self.folder / 'curves.csv'
        path.write_text('x,a,b,c\n0,1,3,7\n1,2,4,8\n2,3,5,9\n')
        book = self.w.import_xy_files([path])
        page = self.w.open_workbook(book.workbook_id).current_sheet
        pump()
        return book, page

    def select(self, page, columns):
        for i, column in enumerate(columns):
            click_header(page, column, Qt.ControlModifier if i else Qt.NoModifier)

    def context_plot(self, page, style):
        host = host_for(page)
        reveal_column(page, 1)
        QTest.mouseClick(self.host.window, Qt.RightButton, pos=header_point(page, 1))
        pump()
        root = self.w.graph_documents.content_item(('workbook', host.workbook_id, ''))
        menu = root.findChild(QObject, 'sheetContextMenu')
        self.assertIsNotNone(menu)
        self.assertTrue(menu.property('opened'))
        control = item(menu.property('contentItem'), 'sheetContextPlot_' + style)
        self.assertTrue(control.isVisible())
        self.assertTrue(control.isEnabled())
        point = control.mapToScene(control.boundingRect().center()).toPoint()
        QTest.mouseClick(input_receiver(self.w, control), Qt.LeftButton, pos=point)
        pump()

    def test_sheet_label_draft_marks_dirty_and_ctrl_s_saves_active_editor(self):
        book, page = self.xy()
        path = self.w.save_project(self.folder / 'metadata.spectra')
        page.editMetadata(1, 'long_name')
        set_metadata_draft(page, 'Emission')
        workbook = self.w.graph_documents.document_for(('workbook', book.workbook_id, ''))
        self.assertTrue(workbook.has_pending_project_edits())
        self.w._refresh_project_modified()
        self.assertTrue(self.w.projectModified)
        QTest.keyClick(self.host.window, Qt.Key_S, Qt.ControlModifier)
        for _ in range(500):
            if not self.w._project_io_jobs.busy:
                break
            pump(10)
        self.assertFalse(self.w._project_io_jobs.busy)
        pump()
        self.assertEqual(page.editorColumn, -1)
        restored = read_project(path)
        self.assertEqual(restored.sheets[page.sheet_id].column_metadata[1]['long_name'], 'Emission')
        self.assertFalse(self.w.projectModified)

    def test_native_header_and_commands_create_distinct_artists_without_row_slicing(self):
        book, page = self.xy()
        source = self.w.workspace.sheets[page.sheet_id].data.copy()
        self.assertTrue(hasattr(self.w, 'workbook_commands'), 'Workbook needs the shared Quick command row')
        commands = self.w
        self.assertFalse(item(commands, 'workbookHeatmap').isEnabled())
        heatmap_tip = next(child for child in item(commands, 'workbookHeatmap').children()
                           if child.metaObject().className() == 'QQuickToolTipAttached')
        self.assertIn('DAT', heatmap_tip.property('text'))
        for style, line, marker in [('line', '-', 'None'), ('scatter', 'None', 'o'), ('line_symbol', '-', 'o')]:
            self.w.open_workbook(book.workbook_id)
            self.select(page, [1, 3])
            self.assertEqual(self.w.workspace.sheets[page.sheet_id].y_columns, [1, 3])
            source_label = item(commands, 'workbookSourceContext')
            self.assertTrue(source_label.isVisible())
            self.assertIn('2 Y', source_label.property('text'))
            self.assertGreaterEqual(source_label.width(),
                                    QFontMetricsF(source_label.property('font')).horizontalAdvance('2 Y'))
            # Copy-only row selection must not become a plot row slice.
            page.selectRow(1)
            control = item(commands, 'workbookPlot_' + style)
            self.assertTrue(control.isVisible())
            self.assertEqual(control.property('text'),
                             {'line': 'Line', 'scatter': 'Scatter', 'line_symbol': 'Line+Symbol'}[style])
            click_item(commands, 'workbookPlot_' + style)
            graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
            self.assertEqual(len(graph.axes.lines), 2)
            for artist in graph.axes.lines:
                self.assertEqual((artist.get_linestyle(), artist.get_marker()), (line, marker))
                self.assertEqual(len(artist.get_xdata()), 3)
        np.testing.assert_array_equal(self.w.workspace.sheets[page.sheet_id].data, source)

    def test_dat_header_drag_commits_one_full_length_curve(self):
        path = self.folder / 'drag.dat'
        path.write_text('Energy(eV) 0 1 2 3\n' + '\n'.join(
            f'{1+i/10} {i+1} {i+2} {i+3} {i+4}' for i in range(12)))
        self.w._load_dat_file_direct(path)
        session = self.w.workspace.active
        sheet = ensure_dataset_sheet(self.w.workspace, session.dataset_id)
        book = self.w._sheet_owner(sheet.sheet_id)
        page = self.w.open_workbook(book.workbook_id, sheet.sheet_id).current_sheet
        pump()
        self.assertEqual(sheet.y_columns, [1, 2, 3, 4])
        drag_header(page, 0, 1)
        self.assertEqual(sorted(i.column() for i in page.selection_model.selectedColumns()), [0, 1])
        click_item(self.w, 'workbookPlot_line')
        graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.assertEqual([curve.y_column for curve in graph.comparison.curves.values()], [1])
        self.assertEqual(sheet.y_columns, [1])
        self.assertEqual(self.w._current_gate_index(), 0)
        self.assertEqual(len(graph.axes.lines), 1)
        np.testing.assert_array_equal(graph.axes.lines[0].get_xdata(), session.base_cube.energy)
        np.testing.assert_array_equal(graph.axes.lines[0].get_ydata(), session.base_cube.z[0])

    def test_dat_header_gate_requests_are_once_per_change_and_reactivate_after_navigation(self):
        path = self.folder / 'header-gates.dat'
        path.write_text('Energy(eV) 0 1 2 3\n' + '\n'.join(
            f'{1+i/10} {i+1} {i+2} {i+3} {i+4}' for i in range(12)))
        self.w._load_dat_file_direct(path)
        session = self.w.workspace.active
        sheet = ensure_dataset_sheet(self.w.workspace, session.dataset_id)
        book = self.w._sheet_owner(sheet.sheet_id)
        page = self.w.open_workbook(book.workbook_id, sheet.sheet_id).current_sheet
        pump()
        requests = []
        page.gateRequested.connect(lambda identity, gate: requests.append((identity, gate)))
        for column, modifiers in ((2, Qt.NoModifier), (4, Qt.ControlModifier), (3, Qt.ShiftModifier)):
            with self.subTest(column=column):
                before = len(requests)
                click_header(page, column, modifiers)
                self.assertEqual(requests[before:], [(sheet.sheet_id, column - 1)])
                self.assertEqual(self.w._current_gate_index(), column - 1)
        QTest.keyRelease(self.host.window, Qt.Key_Shift)
        self.w._set_current_gate_from_index(0)
        self.assertEqual(page.model.gate_column, 1)
        before = len(requests)
        self.select(page, [3])  # This is already the table's current column.
        self.assertEqual(requests[before:], [(sheet.sheet_id, 2)])
        self.select(page, [3])
        self.assertEqual(len(requests), before + 1)
        page.setCurrentCell(0, 4)
        self.assertEqual(requests[-1], (sheet.sheet_id, 3))
        self.assertEqual(self.w._current_gate_index(), 3)
        intended_y = sheet.y_columns.copy()
        page.selectRow(5)
        self.assertEqual(sheet.y_columns, intended_y)

        # A cached Sheet from an inactive DAT must still activate its owner.
        other = self.folder / 'other-header-gates.dat'
        other.write_text(path.read_text())
        self.w._load_dat_file_direct(other)
        self.assertNotEqual(self.w.workspace.active_dataset_id, session.dataset_id)
        page = self.w.open_workbook(book.workbook_id, sheet.sheet_id).current_sheet
        self.select(page, [4])
        self.assertEqual(self.w.workspace.active_dataset_id, session.dataset_id)
        self.assertEqual(self.w._current_gate_index(), 3)
        self.assertEqual(sheet.y_columns, [4])
        click_item(self.w, 'workbookPlot_line')
        graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.assertEqual([curve.y_column for curve in graph.comparison.curves.values()], [4])
        np.testing.assert_array_equal(graph.axes.lines[0].get_xdata(), session.base_cube.energy)
        np.testing.assert_array_equal(graph.axes.lines[0].get_ydata(), session.base_cube.z[3])

    def test_dat_select_all_gestures_replace_subset_for_original_and_processed_heatmaps(self):
        path = self.folder / 'select-all.dat'
        path.write_text('Energy(eV) 0 1 2 3\n' + '\n'.join(
            f'{1+i/10} {i+1} {i+2} {i+3} {i+4}' for i in range(12)))
        self.w._load_dat_file_direct(path)
        session = self.w.workspace.active
        raw = session.base_cube.z.copy()
        self.w._replace_current_data(replace(session.current_cube, z=raw * 3),
                                     mode='energy_second_derivative', smooth=False, window_length=5)
        processed = session.current_cube.z.copy()
        for role, mode, expected in [('original', 'raw', raw), ('processed', 'processed', processed)]:
            sheet = ensure_dataset_sheet(self.w.workspace, session.dataset_id, role)
            book = self.w._sheet_owner(sheet.sheet_id)
            for gesture, command in [('ctrl_a', 'toolbar'), ('corner', 'context'), ('selectAll', 'toolbar')]:
                with self.subTest(role=role, gesture=gesture, command=command):
                    page = self.w.open_workbook(book.workbook_id, sheet.sheet_id).current_sheet
                    self.select(page, [1, 2])
                    self.assertEqual(sheet.y_columns, [1, 2])
                    if gesture == 'ctrl_a':
                        table_key(page, Qt.Key_A, Qt.ControlModifier)
                    elif gesture == 'corner':
                        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=point_in(host_for(page), 'workbookSheetTable', 20, 10))
                        pump()
                    else:
                        page.selectAll()
                    pump()
                    self.assertEqual(sorted(index.column() for index in
                        page.selection_model.selectedColumns()), [0, 1, 2, 3, 4])
                    self.assertEqual(sheet.y_columns, [1, 2, 3, 4])
                    if command == 'toolbar':
                        click_item(self.w, 'workbookHeatmap')
                    else:
                        self.context_plot(page, 'heatmap')
                    graph = session.active_graph
                    self.assertEqual((graph.source_mode, graph.gate_indices), (mode, [0, 1, 2, 3]))
                    snapshot = snapshot_dataset(session)
                    np.testing.assert_array_equal(snapshot.y, [0, 1, 2, 3])
                    self.assertEqual(snapshot.z.shape, (4, 12))
                    np.testing.assert_array_equal(snapshot.z, expected)
                    np.testing.assert_array_equal(session.base_cube.z, raw)
                    np.testing.assert_array_equal(session.current_cube.z, processed)

    def test_name_does_not_redraw_and_title_style_export_roundtrip(self):
        book, sheet = self.xy()
        sheet.request_plot('line')
        graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.assertIsNot(xy_field(graph, 'name')[1], xy_field(graph, 'plot_title')[1],
                         'Graph name and Plot title need separate fields')
        with patch.object(graph, 'render', wraps=graph.render) as render:
            set_xy_draft(graph, 'name', 'Tree name')
            graph.inspector_bridge.commitField('name')
            render.assert_not_called()
            set_xy_draft(graph, 'plot_title', 'Figure title')
            graph.inspector_bridge.commitField('plot_title')
            self.assertEqual(render.call_count, 1)
        graph.inspector_bridge.selectAll(True)
        xy_command(graph, 'style:scatter')
        self.assertTrue(all(c.style == 'scatter' for c in graph.comparison.curves.values()))
        self.assertEqual(len(self.w.workspace.comparisons), 1)
        graph.export(self.folder / 'scatter.svg')
        graph.export(self.folder / 'scatter.png')
        self.assertIn('Figure title', (self.folder / 'scatter.svg').read_text())
        self.assertTrue((self.folder / 'scatter.png').read_bytes().startswith(b'\x89PNG'))
        saved = self.w.save_project(self.folder / 'styles.spectra')
        restored = read_project(saved)
        comp = restored.comparisons[graph.comparison_id]
        self.assertEqual((comp.name, comp.plot_title), ('Tree name', 'Figure title'))
        self.assertTrue(all(c.style == 'scatter' for c in comp.curves.values()))

    def test_default_mapping_context_action_append_and_duplicate_keep_existing_style(self):
        book, sheet = self.xy()
        click_item(self.w, 'workbookPlot_scatter')
        graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        target = graph.comparison
        self.assertEqual(len(target.curves), 3)  # First X, all remaining Ys, no selection required.
        path = self.folder / 'other.csv'
        path.write_text('time,signal\n0.5,30\n1.5,40\n')
        other = self.w.import_xy_files([path])
        page = self.w.open_workbook(other.workbook_id).current_sheet
        commands = self.w
        self.assertEqual(self.w.workbook_commands.targetId, target.comparison_id)
        self.w.workbook_commands.append()
        self.assertEqual([c.style for c in target.curves.values()], ['scatter'] * 3 + ['line'])
        np.testing.assert_array_equal(graph.axes.lines[-1].get_xdata(), [.5, 1.5])
        with patch('spectra_app.main_window.prompt_message', side_effect=AssertionError('Routine duplicate modal')):
            self.w.workbook_commands.append()
        self.assertEqual(len(target.curves), 4)
        self.context_plot(page, 'line_symbol')
        new_graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.assertEqual(new_graph.axes.lines[0].get_marker(), 'o')
        self.assertEqual(new_graph.axes.lines[0].get_linestyle(), '-')

    def test_scatter_hit_testing_does_not_pick_invisible_segments(self):
        book, page = self.xy()
        self.select(page, [1])
        click_item(self.w, 'workbookPlot_scatter')
        graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        graph.canvas.draw()
        graph.inspector_bridge.selectAll(False)
        x, y = graph.axes.transData.transform((.5, 1.5))
        graph._plot_clicked(SimpleNamespace(inaxes=graph.axes, x=x, y=y, button=1, key=None))
        self.assertEqual(list(graph._selected_ids()), [])

    def test_native_legacy_title_default_and_decoder_validation(self):
        book, sheet = self.xy()
        sheet.request_plot('line')
        graph = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        saved = self.w.save_project(self.folder / 'legacy.spectra')
        with zipfile.ZipFile(saved) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        def install(payload):
            with zipfile.ZipFile(saved, 'w') as archive:
                for name, value in members.items():
                    archive.writestr(name, json.dumps(payload).encode() if name == 'manifest.json' else value)
        legacy = copy.deepcopy(manifest)
        legacy['version'] = 6
        for comparison in legacy['comparisons']:
            comparison.pop('plot_title')
            comparison.pop('presentation', None)
            for curve in comparison['curves']:
                curve.pop('style')
                for name in ('marker_shape', 'marker_size', 'marker_fill', 'marker_edge_color', 'marker_edge_width'):
                    curve.pop(name)
        install(legacy)
        restored = read_project(saved).comparisons[graph.comparison_id]
        self.assertEqual(restored.plot_title, restored.name)
        self.assertTrue(all(c.style == 'line' for c in restored.curves.values()))
        for field, value in [('style', 'area'), ('style', None)]:
            invalid = copy.deepcopy(manifest)
            invalid['comparisons'][0]['curves'][0][field] = value
            install(invalid)
            with self.assertRaises(ProjectFormatError):
                read_project(saved)
        invalid = copy.deepcopy(manifest)
        invalid['comparisons'][0]['plot_title'] = 4
        install(invalid)
        with self.assertRaises(ProjectFormatError):
            read_project(saved)

    def test_sparse_original_and_processed_heatmaps_retain_source_binding(self):
        path = self.folder / 'matrix.dat'
        path.write_text('Energy(eV) 0 1 2 3\n' + '\n'.join(f'{1+i/10} {i+1} {i+2} {i+3} {i+4}' for i in range(12)))
        self.w._load_dat_file_direct(path)
        session = self.w.workspace.active
        original_graph = copy.deepcopy(session.active_graph)
        raw = session.base_cube.z.copy()
        self.w._replace_current_data(replace(session.current_cube, z=raw * 3),
                                     mode='energy_second_derivative', smooth=False, window_length=5)
        processed = session.current_cube.z.copy()
        self.assertTrue(hasattr(self.w, 'workbook_commands'), 'Heatmap command needs graph-specific source scope')
        created = []
        for role, mode, expected in [('original', 'raw', raw), ('processed', 'processed', processed)]:
            sheet = ensure_dataset_sheet(self.w.workspace, session.dataset_id, role)
            book = self.w._sheet_owner(sheet.sheet_id)
            page = self.w.open_workbook(book.workbook_id, sheet.sheet_id).current_sheet
            self.select(page, [1, 4])
            previous_graph = copy.deepcopy(session.active_graph)
            click_item(self.w, 'workbookHeatmap')
            graph = session.active_graph
            created.append(graph.graph_id)
            self.assertEqual((graph.source_mode, graph.gate_indices), (mode, [0, 3]))
            snapshot = snapshot_dataset(session)
            np.testing.assert_array_equal(snapshot.y, [0, 3])
            np.testing.assert_array_equal(snapshot.z, expected[[0, 3]])
            self.assertEqual('d²' in snapshot.signal_label, mode == 'processed')
            spectrum = next(line for line in self.w._spec_ax.lines if line.get_label() == 'Spectrum')
            np.testing.assert_array_equal(spectrum.get_ydata(), expected[3])
            np.testing.assert_allclose(snapshot.color_limits, np.percentile(expected[[0, 3]], [.5, 99.5]))
            self.assertEqual(session.graphs[previous_graph.graph_id], previous_graph)
            self.w._render_plot_page()
            self.assertIsNotNone(self.w.plot_page.rendered_page)
            self.assertEqual(self.w.plot_page.rendered_page.valid_count, expected[[0, 3]].size)
            with patch.object(self.w, '_output_folder', return_value=self.folder):
                svg = self.w.export_plot_page('svg')
                png = self.w.export_plot_page('png')
            self.assertIn('<svg', svg.read_text())
            with Image.open(png) as image:
                metadata = json.loads(image.info['SpectraAnalyzer'])
                self.assertEqual(metadata['source_mode'], mode)
                self.assertEqual(metadata['gate_indices'], [0, 3])
            key = (session.dataset_id, graph.graph_id)
            self.w.graph_documents.close_document(key)
            self.w.activate_graph(graph.graph_id)
            np.testing.assert_array_equal(snapshot_dataset(session).z, expected[[0, 3]])
        saved = self.w.save_project(self.folder / 'subset.spectra')
        restored = read_project(saved)
        for identity, expected in zip(created, [raw, processed]):
            restored.active.activate_graph(identity)
            np.testing.assert_array_equal(snapshot_dataset(restored.active).z, expected[[0, 3]])
        records = validate_plot_collection(session.plot_collection())['graphs']
        self.assertEqual(records[-1]['gate_indices'], [0, 3])
        store = PlotStateStore()
        store.save(path, session.loaded_fingerprint, session.plot_collection())
        collection, notice = store.load(path, session.loaded_fingerprint)
        self.assertFalse(notice)
        recalled = initialize_dataset(path, (session.base_cube, session.loaded_fingerprint, collection, ''),
                                      self.w.snapshot_dataset_defaults())
        self.assertEqual(recalled.active_graph.gate_indices, [0, 3])
        recalled.activate_graph(created[0])
        np.testing.assert_array_equal(snapshot_dataset(recalled).z, raw[[0, 3]])
        self.assertNotIn('d²', snapshot_dataset(recalled).signal_label)
        clone = recalled.create_graph('Raw clone')
        self.assertEqual((clone.source_mode, clone.gate_indices), ('raw', [0, 3]))
        # Validation happens on project decode, before a source or graph can be displayed.
        with zipfile.ZipFile(saved) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        for value in ([0, 99], [1, 1], [3, 0], [], [True], 'all'):
            invalid = copy.deepcopy(manifest)
            invalid['datasets'][0]['graphs'][-1]['gate_indices'] = value
            with zipfile.ZipFile(saved, 'w') as archive:
                for name, payload in members.items():
                    archive.writestr(name, json.dumps(invalid).encode() if name == 'manifest.json' else payload)
            with self.assertRaises(ProjectFormatError):
                read_project(saved)
        for value in ([1, 1], [], [True], 'all'):
            invalid = copy.deepcopy(collection)
            invalid['graphs'][-1]['gate_indices'] = value
            with self.assertRaises(ValueError):
                validate_plot_collection(invalid)
        # Build a pre-result projection before exercising the v6 migration.
        legacy_workspace = copy.deepcopy(restored)
        for owner in legacy_workspace.datasets.values():
            owner.processing_results.clear()
            for graph in owner.graphs.values():
                graph.result_id = None
        for sheet in legacy_workspace.sheets.values():
            sheet.result_id = None
        legacy_path = self.folder / 'legacy-subset.spectra'
        write_project(legacy_path, legacy_workspace)
        with zipfile.ZipFile(legacy_path) as archive:
            legacy_members = {name: archive.read(name) for name in archive.namelist()}
        legacy = json.loads(legacy_members['manifest.json'])
        legacy['version'] = 6
        for record in legacy['datasets'][0]['graphs']:
            record.pop('published')
            record.pop('source_mode')
            record.pop('gate_indices')
        with zipfile.ZipFile(legacy_path, 'w') as archive:
            for name, payload in legacy_members.items():
                archive.writestr(name, json.dumps(legacy).encode() if name == 'manifest.json' else payload)
        old = read_project(legacy_path).active
        self.assertEqual((old.active_graph.source_mode, old.active_graph.gate_indices), ('processed', None))
        np.testing.assert_array_equal(snapshot_dataset(old).z, processed)
        legacy_collection = copy.deepcopy(collection)
        for record in legacy_collection['graphs']:
            record.pop('published')
            record.pop('source_mode')
            record.pop('gate_indices')
        self.assertTrue(all(record['gate_indices'] is None for record in
                            validate_plot_collection(legacy_collection, version=4)['graphs']))
        np.testing.assert_array_equal(session.base_cube.z, raw)
        np.testing.assert_array_equal(session.current_cube.z, processed)
        self.assertEqual(session.graphs['default'].name, original_graph.name)

    def test_axis_override_preserves_sparse_graph_scope_and_auto_colors(self):
        path = self.folder / 'override.dat'
        path.write_text('Energy(eV) 0 1 2 3\n' + '\n'.join(
            f'{1+i/10} {i+1} {10000+i} {20000+i} {i+4}' for i in range(12)))
        self.w._load_dat_file_direct(path)
        session = self.w.workspace.active
        sheet = ensure_dataset_sheet(self.w.workspace, session.dataset_id)
        sheet.y_columns = [1, 4]
        self.w._quick_sheet_plot(sheet.sheet_id, 'heatmap')
        graph = session.active_graph
        self.w.activate_graph('default')  # Inactive graph caches must also use their own sample scope.
        cube = load_dat_cube(path, x_axis_override='pixel')
        self.w._apply_axis_override(cube, session.loaded_fingerprint, 0.)
        self.assertEqual(graph.gate_indices, [0, 3])
        np.testing.assert_allclose(graph.view.color_limits, np.percentile(cube.z[[0, 3]], [.5, 99.5]))
        session.activate_graph(graph.graph_id)
        np.testing.assert_array_equal(snapshot_dataset(session).z, cube.z[[0, 3]])

