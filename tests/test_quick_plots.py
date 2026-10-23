"""Native quick commands and persisted plot semantics, without source mutation."""
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
from PySide6.QtCore import Qt, QPoint, QEvent, QItemSelectionModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication

from spectra_app.main_window import MainWindow
from spectra_app.plot_snapshot import snapshot_dataset
from spectra_app.project_io import read_project, write_project, ProjectFormatError
from spectra_app.plot_state import validate_plot_collection, PlotStateStore
from spectra_app.dataset_state import initialize_dataset
from spectra_app.dat_io import load_dat_cube
from PIL import Image
from spectra_app.workbook_state import ensure_dataset_sheet
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])


class QuickPlotTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.errors = []
        self.w._show_error = self.errors.append
        self.w.resize(1500, 920)
        self.w.show()
        APP.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.assertEqual(self.errors, [])

    def xy(self):
        path = self.folder / 'curves.csv'
        path.write_text('x,a,b,c\n0,1,3,7\n1,2,4,8\n2,3,5,9\n')
        book = self.w.import_xy_files([path])
        page = self.w.open_workbook(book.workbook_id).tabs.currentWidget()
        APP.processEvents()
        return book, page

    def select(self, page, columns):
        header = page.table.horizontalHeader()
        for i, column in enumerate(columns):
            QTest.mouseClick(header.viewport(), Qt.LeftButton,
                             Qt.ControlModifier if i else Qt.NoModifier,
                             QPoint(header.sectionViewportPosition(column) + 12, header.height() // 2))
        APP.processEvents()

    def test_native_header_and_commands_create_distinct_artists_without_row_slicing(self):
        book, page = self.xy()
        source = self.w.workspace.sheets[page.sheet_id].data.copy()
        self.assertTrue(hasattr(self.w, 'workbook_commands'), 'Workbook needs the shared icon command row')
        commands = self.w.workbook_commands
        self.assertFalse(commands.heatmap_btn.isEnabled())
        self.assertIn('DAT', commands.heatmap_btn.toolTip())
        for style, line, marker in [('line', '-', 'None'), ('scatter', 'None', 'o'), ('line_symbol', '-', 'o')]:
            self.w.open_workbook(book.workbook_id)
            self.select(page, [1, 3])
            self.assertEqual(self.w.workspace.sheets[page.sheet_id].y_columns, [1, 3])
            self.assertTrue(commands.source_label.isVisible())
            self.assertGreaterEqual(commands.source_label.width(), commands.source_label.fontMetrics().horizontalAdvance('2 Y'))
            # Copy-only row selection must not become a plot row slice.
            page.table.selectRow(1)
            self.assertFalse(commands.style_buttons[style].icon().isNull())
            QTest.mouseClick(commands.style_buttons[style], Qt.LeftButton)
            graph = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
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
        page = self.w.open_workbook(book.workbook_id, sheet.sheet_id).tabs.currentWidget()
        APP.processEvents()
        self.assertEqual(sheet.y_columns, [1, 2, 3, 4])
        header = page.table.horizontalHeader()
        start = QPoint(header.sectionViewportPosition(0) + 12, header.height() // 2)
        end = QPoint(header.sectionViewportPosition(1) + 12, header.height() // 2)
        QTest.mousePress(header.viewport(), Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(header.viewport(), end)
        QTest.mouseRelease(header.viewport(), Qt.LeftButton, Qt.NoModifier, end)
        APP.processEvents()
        self.assertEqual(sorted(i.column() for i in page.table.selectionModel().selectedColumns()), [0, 1])
        QTest.mouseClick(self.w.workbook_commands.style_buttons['line'], Qt.LeftButton)
        graph = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        self.assertEqual([curve.y_column for curve in graph.comparison.curves.values()], [1])
        self.assertEqual(sheet.y_columns, [1])
        self.assertEqual(self.w._current_gate_index(), 0)
        self.assertEqual(len(graph.axes.lines), 1)
        np.testing.assert_array_equal(graph.axes.lines[0].get_xdata(), session.base_cube.energy)
        np.testing.assert_array_equal(graph.axes.lines[0].get_ydata(), session.base_cube.z[0])

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
                    page = self.w.open_workbook(book.workbook_id, sheet.sheet_id).tabs.currentWidget()
                    self.select(page, [1, 2])
                    self.assertEqual(sheet.y_columns, [1, 2])
                    if gesture == 'ctrl_a':
                        page.table.setFocus()
                        QTest.keyClick(page.table, Qt.Key_A, Qt.ControlModifier)
                    elif gesture == 'corner':
                        corner = next(button for button in page.table.findChildren(QAbstractButton)
                                      if button.metaObject().className() == 'QTableCornerButton')
                        QTest.mouseClick(corner, Qt.LeftButton)
                    else:
                        page.table.selectAll()
                    APP.processEvents()
                    self.assertEqual(sorted(index.column() for index in
                        page.table.selectionModel().selectedColumns()), [0, 1, 2, 3, 4])
                    self.assertEqual(sheet.y_columns, [1, 2, 3, 4])
                    if command == 'toolbar':
                        QTest.mouseClick(self.w.workbook_commands.heatmap_btn, Qt.LeftButton)
                    else:
                        menu = page.column_menu(1)
                        next(action for action in menu.actions() if action.text() == 'Heatmap').trigger()
                        menu.deleteLater()
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
        sheet.new_comparison_btn.click()
        graph = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        self.assertTrue(hasattr(graph, 'plot_title_edit'), 'Graph name and Plot title need separate fields')
        with patch.object(graph, 'render', wraps=graph.render) as render:
            graph.name_edit.setText('Tree name')
            graph.name_edit.editingFinished.emit()
            render.assert_not_called()
            graph.plot_title_edit.setText('Figure title')
            graph.plot_title_edit.editingFinished.emit()
            self.assertEqual(render.call_count, 1)
        graph.curve_list.selectAll()
        graph.style_buttons['scatter'].click()
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
        self.w.workbook_commands.style_buttons['scatter'].click()
        graph = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        target = graph.comparison
        self.assertEqual(len(target.curves), 3)  # First X, all remaining Ys, no selection required.
        path = self.folder / 'other.csv'
        path.write_text('time,signal\n0.5,30\n1.5,40\n')
        other = self.w.import_xy_files([path])
        page = self.w.open_workbook(other.workbook_id).tabs.currentWidget()
        commands = self.w.workbook_commands
        self.assertEqual(commands.target_combo.currentData(), target.comparison_id)
        commands.add_btn.click()
        self.assertEqual([c.style for c in target.curves.values()], ['scatter'] * 3 + ['line'])
        np.testing.assert_array_equal(graph.axes.lines[-1].get_xdata(), [.5, 1.5])
        with patch('spectra_app.main_window.QMessageBox.exec', side_effect=AssertionError('Routine duplicate modal')):
            commands.add_btn.click()
        self.assertEqual(len(target.curves), 4)
        menu = page.column_menu(1)
        next(action for action in menu.actions() if action.text() == 'Line + Symbol').trigger()
        new_graph = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        self.assertEqual(new_graph.axes.lines[0].get_marker(), 'o')
        self.assertEqual(new_graph.axes.lines[0].get_linestyle(), '-')
        menu.deleteLater()

    def test_scatter_hit_testing_does_not_pick_invisible_segments(self):
        book, page = self.xy()
        self.select(page, [1])
        self.w.workbook_commands.style_buttons['scatter'].click()
        graph = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
        graph.canvas.draw()
        graph.curve_list.clearSelection()
        x, y = graph.axes.transData.transform((.5, 1.5))
        graph._plot_clicked(SimpleNamespace(inaxes=graph.axes, x=x, y=y, button=1, key=None))
        self.assertEqual(graph.curve_list.selectedItems(), [])

    def test_native_legacy_title_default_and_decoder_validation(self):
        book, sheet = self.xy()
        sheet.new_comparison_btn.click()
        graph = self.w.graph_documents.page_for(self.w.graph_documents._active_key)
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
            for curve in comparison['curves']:
                curve.pop('style')
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
            page = self.w.open_workbook(book.workbook_id, sheet.sheet_id).tabs.currentWidget()
            self.select(page, [1, 4])
            previous_graph = copy.deepcopy(session.active_graph)
            self.w.workbook_commands.heatmap_btn.click()
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
        legacy = copy.deepcopy(manifest)
        legacy['version'] = 6
        for record in legacy['datasets'][0]['graphs']:
            record.pop('published')
            record.pop('source_mode')
            record.pop('gate_indices')
        with zipfile.ZipFile(saved, 'w') as archive:
            for name, payload in members.items():
                archive.writestr(name, json.dumps(legacy).encode() if name == 'manifest.json' else payload)
        old = read_project(saved).active
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

