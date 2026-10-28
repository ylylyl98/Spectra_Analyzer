"""Real GUI event paths must not repeat expensive rendering for one edit."""
from tests import analysis_header_quick_helpers as ah
from tests import dat_properties_quick_helpers as dat
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.colors import Normalize, to_hex
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtCore import QCoreApplication

from spectra_app import main_window, svg_export
from spectra_app.plot_snapshot import snapshot_dataset
from spectra_app.workbook_state import ensure_dataset_sheet
from tests.quick_migration_case import QuickMigrationCase
from tests.background_helpers import wait_jobs
from tests.formal_quick_helpers import click_item, edit_field
from tests.workbook_quick_helpers import item, pump



class RenderEventPerformanceTests(QuickMigrationCase):
    _pure_cases = {
        'test_cached_contrast_refreshes_for_inplace_data_mask_geometry_and_tick_edits',
        'test_memory_order_change_with_identical_physical_bytes_refreshes_contrast',
    }

    def setUp(self):
        if self._testMethodName in self._pure_cases:
            return
        super().setUp()
        path = self.folder / 'events.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/100} {i+1} {2*i+1} {3*i+1}' for i in range(100)))
        self.w = self.controller
        self.w._show_error = self.fail
        self.w._load_dat_file_direct(path)
        self.w.open_graph_document()
        self.assertTrue(self.w.request_outer_view('analysis'))
        QTest.qWait(300)

    def tearDown(self):
        if self._testMethodName in self._pure_cases:
            return
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def settle(self):
        for _ in range(8):
            QCoreApplication.processEvents()

    def test_sheet_heatmap_projects_final_source_once_and_reuses_its_view(self):
        w = self.w
        session = w.workspace.active
        raw_buffer = session.base_cube.z
        raw = raw_buffer.copy()
        w._replace_current_data(replace(session.current_cube, z=raw * 3),
                                mode='energy_second_derivative', smooth=False, window_length=5)
        processed = session.current_cube.z.copy()
        original = ensure_dataset_sheet(w.workspace, session.dataset_id, 'original')
        processed_sheet = ensure_dataset_sheet(w.workspace, session.dataset_id, 'processed')
        retained = None
        for sheet in (original, processed_sheet, original):
            sheet.y_columns = [1, 3]
            book = w._sheet_owner(sheet.sheet_id)
            w.open_workbook(book.workbook_id, sheet.sheet_id)
            projected = []
            plot = w._plot_cube
            def observe():
                projected.append((session.active_graph_id, session.active_graph.source_mode,
                                  deepcopy(session.active_graph.gate_indices)))
                return plot()
            with patch.object(w, '_plot_cube', side_effect=observe):
                click_item(w, 'workbookHeatmap')
            graph = session.active_graph
            self.assertEqual(projected, [(graph.graph_id, sheet.mode, [0, 2])])
            self.assertEqual(w.graph_documents.registry.active_key, (session.dataset_id, graph.graph_id))
            expected = raw if sheet.mode == 'raw' else processed
            np.testing.assert_array_equal(snapshot_dataset(session).z, expected[[0, 2]])
            np.testing.assert_array_equal(w._heat_mesh.get_array(), expected[[0, 2]])
            if sheet is original:
                if retained is None:
                    dat.setup_number(w, 'xmin', 1.25)
                    w.dat_properties.setLabel('plot_title', 'Retained raw graph', w.dat_properties.ownerToken)
                    w._finish_graph_editors()
                    retained = deepcopy(graph)
                else:
                    self.assertEqual(graph, retained)
                    self.assertEqual(w._heat_ax.get_xlim()[0], 1.25)
                    self.assertEqual(w._heat_ax.get_title(), 'Retained raw graph')
            w._render_plot_page()
            self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())
            self.assertEqual(w.plot_page.expected_owner, w._plot_owner_token())
            self.assertTrue(w.plot_page.command_enabled('export_png'))
        self.assertIs(session.base_cube.z, raw_buffer)
        np.testing.assert_array_equal(session.base_cube.z, raw)
        np.testing.assert_array_equal(session.current_cube.z, processed)

    def test_cross_dataset_heatmap_commits_outgoing_draft_before_choosing_target(self):
        w = self.w
        outgoing = w.workspace.active
        target_path = self.folder / 'target.dat'
        target_path.write_text('Energy(eV) 10 20 30\n' + '\n'.join(
            f'{3+i/100} {i+2} {i+3} {i+4}' for i in range(100)))
        w._load_dat_file_direct(target_path)
        target = w.workspace.active
        sheet = ensure_dataset_sheet(w.workspace, target.dataset_id)
        sheet.y_columns = [1, 3]
        w.open_graph_document(outgoing.dataset_id)
        w.dat_properties.setLabel('plot_title', 'Outgoing draft', w.dat_properties.ownerToken)
        projected = []
        plot = w._plot_cube
        def observe():
            projected.append((w.workspace.active_dataset_id, w.workspace.active.active_graph_id))
            return plot()
        with patch.object(w, '_plot_cube', side_effect=observe):
            graph = w._quick_sheet_plot(sheet.sheet_id, 'heatmap')
        self.assertEqual(projected, [(target.dataset_id, graph.graph_id)])
        self.assertEqual(outgoing.view.annotations['plot_title'], 'Outgoing draft')
        self.assertEqual(target.view.annotations['plot_title'], '')
        np.testing.assert_allclose(w._heat_ax.get_xlim(), [3., 3.99])
        np.testing.assert_allclose(w._heat_ax.get_ylim(), [10., 30.])
        self.assertEqual(w.graph_documents.registry.active_key, (target.dataset_id, graph.graph_id))
        w._render_plot_page()
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())

    def test_heatmap_incoming_failure_restores_existing_graph_and_live_owner(self):
        w = self.w
        session = w.workspace.active
        sheet = ensure_dataset_sheet(w.workspace, session.dataset_id)
        sheet.y_columns = [1, 3]
        previous = deepcopy(session.active_graph)
        key = w._document_key()
        limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim()
        restore = w.restore_dataset_controls
        errors = []
        w._show_error = errors.append
        def fail_incoming(owner):
            if owner.active_graph_id != key[1]:
                raise ValueError('Cannot restore incoming graph')
            return restore(owner)
        with patch.object(w, 'restore_dataset_controls', side_effect=fail_incoming):
            self.assertIsNone(w._quick_sheet_plot(sheet.sheet_id, 'heatmap'))
        self.assertEqual(errors, ['Cannot restore incoming graph'])
        self.assertEqual(w._document_key(), key)
        self.assertEqual(session.active_graph, previous)
        self.assertEqual(list(session.graphs), [key[1]])
        self.assertEqual(w.graph_documents.registry.active_key, key)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim()), limits)
        w._render_plot_page()
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())

    def test_invalid_outgoing_heatmap_editor_does_not_create_or_select_graph(self):
        w = self.w
        session = w.workspace.active
        sheet = ensure_dataset_sheet(w.workspace, session.dataset_id)
        sheet.y_columns = [1, 3]
        key = w._document_key()
        graphs = deepcopy(session.graphs)
        errors = []
        w._show_error = errors.append
        w.dat_properties.draftNumber('xmin', '-', w.dat_properties.ownerToken)
        with patch.object(w, '_plot_cube', wraps=w._plot_cube) as plot:
            self.assertIsNone(w._quick_sheet_plot(sheet.sheet_id, 'heatmap'))
        self.assertEqual(plot.call_count, 0)
        self.assertTrue(errors)
        self.assertEqual(w._document_key(), key)
        self.assertEqual(session.graphs, graphs)
        w.dat_properties.draftNumber('xmin', str(w.dat_properties.number('xmin')), w.dat_properties.ownerToken)

    def test_cross_dataset_heatmap_projection_failure_keeps_outgoing_document(self):
        w = self.w
        outgoing = w.workspace.active
        target_path = self.folder / 'failed-target.dat'
        target_path.write_text('Energy(eV) 10 20 30\n' + '\n'.join(
            f'{3+i/100} {i+2} {i+3} {i+4}' for i in range(100)))
        w._load_dat_file_direct(target_path)
        target = w.workspace.active
        target_graphs = deepcopy(target.graphs)
        target_graph_id = target.active_graph_id
        sheet = ensure_dataset_sheet(w.workspace, target.dataset_id)
        sheet.y_columns = [1, 3]
        previous_page = w.open_graph_document(outgoing.dataset_id)
        dat.set_choice(w, 'colormap', 'magma')
        key = w._document_key()
        restore = w.restore_dataset_controls
        errors = []
        w._show_error = errors.append
        def fail_incoming(owner):
            if owner is target:
                raise ValueError('Cannot restore target DAT')
            return restore(owner)
        with patch.object(w, 'restore_dataset_controls', side_effect=fail_incoming):
            self.assertIsNone(w._quick_sheet_plot(sheet.sheet_id, 'heatmap'))
        self.assertEqual(errors, ['Cannot restore target DAT'])
        self.assertIs(w.workspace.active, outgoing)
        self.assertEqual(w._document_key(), key)
        self.assertIs(w.plot_page, previous_page)
        self.assertIs(w.graph_documents.document_for(w.graph_documents.registry.active_key), previous_page)
        self.assertEqual(next(entry['label'] for entry in dat.state(w)['colormaps'] if entry['value'] == dat.state(w)['colormap']), 'magma')
        self.assertEqual(target.active_graph_id, target_graph_id)
        self.assertEqual(target.graphs, target_graphs)
        self.assertFalse(any(page_key[0] == target.dataset_id and page_key[1] not in target_graphs
                             for page_key in w.graph_documents.keys()))
        w._render_plot_page()
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())

    def test_analysis_edit_retires_bytes_and_defers_formal_render_until_plot_entry(self):
        w = self.w
        w._render_plot_page()
        old = w.plot_page.rendered_page
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render:
            w.dat_properties.draftNumber('xmin', '1.25', w.dat_properties.ownerToken)
            self.assertTrue(w.dat_properties.commitNumber('xmin', w.dat_properties.ownerToken))
            self.assertIsNone(w.plot_page.rendered_page)
            self.assertFalse(w.plot_page.command_enabled('export_png'))
            QTest.qWait(350)
            self.assertEqual(render.call_count, 0)
            self.assertTrue(w.request_outer_view('workspace'))
            QTest.qWait(400)
            self.assertEqual(render.call_count, 1)
        page = w.plot_page.rendered_page
        self.assertIsNotNone(page, w.plot_page.status_text)
        self.assertNotEqual(page.owner_token, old.owner_token)
        self.assertEqual(page.owner_token, w._plot_owner_token())
        self.assertEqual(w.plot_page.preview.snapshot.x_limits[0], 1.25)

    def test_leaving_plot_cancels_pending_automatic_render(self):
        w = self.w
        self.assertTrue(w.request_outer_view('workspace'))
        w._render_plot_page()
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render:
            w.dat_properties.draftNumber('xmin', '1.2', w.dat_properties.ownerToken)
            self.assertTrue(w.dat_properties.commitNumber('xmin', w.dat_properties.ownerToken))
            self.assertTrue(w.request_outer_view('analysis'))
            QTest.qWait(350)
            self.assertEqual(render.call_count, 0)
        self.assertIsNone(w.plot_page.rendered_page)
        w._render_plot_page()  # Explicit preparation remains available while hidden.
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())

    def test_visible_title_burst_produces_one_svg_and_defers_analysis_layout(self):
        w = self.w
        self.assertTrue(w.request_outer_view('workspace'))
        w._render_plot_page()
        self.settle()
        w.root_window.show()
        dat.reveal(w, 'plot_title')
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render, \
             patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout, \
             patch.object(w._annotation_settings, 'sync', wraps=w._annotation_settings.sync) as sync:
            dat.edit_label(w, 'plot_title', 'One final title')
            self.assertEqual(w._heat_ax.get_title(), 'One final title')
            self.assertEqual(w.workspace.active.view.annotations['plot_title'], 'One final title')
            self.assertIsNone(w.plot_page.rendered_page)
            self.assertEqual(render.call_count, 0)
            QTest.keyClick(w.root_window, Qt.Key_Return)
            QTest.qWait(700)
            self.assertEqual(render.call_count, 1)
            self.assertEqual(layout.call_count, 0)
            self.assertEqual(sync.call_count, 1)
            self.assertTrue(w.request_outer_view('analysis'))
            self.assertEqual(w._heat_ax.get_title(), 'One final title')
            self.assertEqual(w.workspace.active.view.annotations['plot_title'], 'One final title')
            self.assertEqual(render.call_count, 1)
            QCoreApplication.processEvents()
            QTest.qWait(70)
            QCoreApplication.processEvents()
            self.assertEqual(layout.call_count, 1)
        self.assertIn('One final title', w.plot_page.rendered_page.svg_bytes.decode())

    def test_precision_edit_keeps_current_preview_and_defers_hidden_analysis_draw(self):
        w = self.w
        w.root_window.resize(1200, 800)
        w.root_window.show()
        self.assertTrue(w.request_outer_view('workspace'))
        saved = w.workspace.active.presentation
        w.set_plot_presentation(replace(saved, width_in=4.4, height_in=4.2,
                                       colorbar=replace(saved.colorbar, significant_digits=4)))
        w._render_plot_page()
        page = w.plot_page
        page.set_zoom('200%')
        QTest.qWait(300)
        preview = page.preview
        mount = w.graph_documents._presentations[w._document_key()].mount
        provider, scene = mount.scene.provider, mount.item
        old = page.rendered_page
        self.assertIsNotNone(old, page.status_text)
        viewport = item(scene, 'formalViewport')
        viewport.setProperty('contentX', 55)
        viewport.setProperty('contentY', 65)
        pump()
        self.assertEqual((viewport.property('contentX'), viewport.property('contentY')), (55, 65))
        viewport_size, paper_size = (scene.width(), scene.height()), preview.paper_display_size()
        cube, mesh, norm = w.current_cube, w._heat_mesh, w._heat_mesh.norm
        data = cube.z.copy()
        limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), mesh.get_clim()
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render, \
             patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout, \
             patch.object(w.canvas, 'draw', wraps=w.canvas.draw) as draw:
            # Observe commit invalidation before delivering the queued render.
            with patch.object(w, '_schedule_plot_page_render') as schedule:
                edit_field(w, 'bar_precision', '2', commit=True)
                self.assertIsNone(page.rendered_page)
                self.assertIs(preview.page, old)
                self.assertEqual(preview.svg_bytes, old.svg_bytes)
                self.assertFalse(preview.canEdit)
                self.assertFalse(preview.canView)
                self.assertFalse(page.command_enabled('export_svg'))
                self.assertFalse(page.command_enabled('export_png'))
                self.assertEqual(render.call_count, 0)
                sample = page.formal.state['sample'].split(' | ')
                schedule.assert_called_once_with(delay=0)
            w._schedule_plot_page_render(delay=0)
            QTest.qWait(450)
            self.assertEqual(render.call_count, 1)
            self.assertIsNotNone(page.rendered_page, page.status_text)
            self.assertNotEqual(page.rendered_page.svg_bytes, old.svg_bytes)
            self.assertEqual(layout.call_count, 0, 'Workspace precision edits must not lay out hidden Analysis')
            self.assertEqual(draw.call_count, 0, 'Workspace precision edits must not draw hidden Analysis')
            self.assertIs(page.preview, preview)
            self.assertIs(mount.scene.provider, provider)
            self.assertIs(mount.item, scene)
            self.assertEqual(preview.svg_bytes, page.rendered_page.svg_bytes)
            self.assertEqual(page.zoom_text, '200%')
            self.assertEqual((viewport.property('contentX'), viewport.property('contentY')), (55, 65))
            self.assertEqual((scene.width(), scene.height()), viewport_size)
            self.assertEqual(preview.paper_display_size(), paper_size)
            self.assertTrue(page.command_enabled('export_svg'))
            self.assertTrue(w.request_outer_view('analysis'))
            QTest.qWait(200)
            self.assertEqual(layout.call_count, 1)
            self.assertGreater(draw.call_count, 0)
        self.assertEqual([label.get_text() for label in w._heat_colorbar.ax.get_xticklabels()], sample)
        self.assertIs(w.current_cube, cube)
        self.assertIs(w._heat_mesh, mesh)
        self.assertIs(mesh.norm, norm)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), mesh.get_clim()), limits)
        np.testing.assert_array_equal(cube.z, data)

    def test_gate_enter_and_focus_out_rebuild_changed_trace_once(self):
        w = self.w
        w._set_current_gate_from_index(0)
        ah.reveal(w)
        with patch.object(w, '_update_spectrum_only_impl', wraps=w._update_spectrum_only_impl) as rebuild:
            ah.edit(w, 'gate', '1', commit=True)
            ah.button(w, 'contextDetails')
            ah.button(w, 'contextDetailsClose')
            self.assertEqual(rebuild.call_count, 1)
        self.assertEqual(w.current_gate, 1.)
        self.assertEqual(int(ah.text(w, 'index')), 2)
        np.testing.assert_equal(w._spec_ax.lines[0].get_ydata(), np.arange(100)*2+1)
        with patch.object(w, '_update_spectrum_only_impl', wraps=w._update_spectrum_only_impl) as rebuild:
            ah.edit(w, 'gate', '1', commit=True)
            w._set_current_gate_from_value(1.01)
            self.assertEqual(rebuild.call_count, 0)
            w._update_spectrum_only()  # Explicit peak/fit refresh is never suppressed.
            self.assertEqual(rebuild.call_count, 1)

    def test_cancel_paste_dialog_after_pending_title_restores_current_export_page(self):
        w = self.w
        self.assertTrue(w.request_outer_view('workspace'))
        w.copy_plot_settings()
        self.settle()
        w.root_window.show()
        dat.reveal(w, 'plot_title')
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render:
            dat.edit_label(w, 'plot_title', 'Captured title')
            dialog = w.create_paste_settings_dialog(current_only=True)
            dialog.reject()
            QTest.qWait(700)
            self.assertEqual(render.call_count, 1)
        self.assertTrue(w.plot_page.command_enabled('export_png'))
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())
        self.assertIn('Captured title', w.plot_page.rendered_page.svg_bytes.decode())

    def test_locked_regional_bounds_skip_scans_and_keep_independent_limits(self):
        w = self.w
        for count in (2, 3):
            dat.set_choice(w, 'color_scale', ('global', '2', '3')[count - 1])
            dat.set_flag(w, 'vmin_lock', True)
            dat.set_flag(w, 'vmax_lock', True)
            ranges = [(1. + 10*i, 5. + 10*i) for i in range(count)]
            w._last_valid_region_color_limits = ranges.copy()
            with patch.object(np, 'nanpercentile', wraps=np.nanpercentile) as percentile:
                actual = w._regional_color_ranges([np.array([100., 200.])] * count)
                self.assertEqual(percentile.call_count, 0)
            self.assertEqual(actual, ranges)
            self.assertEqual(w._regional_color_ranges([np.array([])] * count), ranges)

    def test_leaving_plot_finishes_title_layout_while_background_render_waits(self):
        w = self.w
        self.assertTrue(w.request_outer_view('workspace'))
        w._render_plot_page()
        release = threading.Event()
        self.addCleanup(release.set)
        def work(context):
            release.wait(2)
            return 1
        w._start_background_job(work, lambda value: None, 'Waiting')
        w.root_window.show()
        dat.reveal(w, 'plot_title')
        with patch.object(w, '_layout_live_plots', wraps=w._layout_live_plots) as layout:
            dat.edit_label(w, 'plot_title', 'Deferred title')
            QTest.keyClick(w.root_window, Qt.Key_Return)
            QTest.qWait(350)
            self.assertEqual(layout.call_count, 0)
            self.assertTrue(w._background_jobs.busy)
            self.assertTrue(w.request_outer_view('analysis'))
            self.assertEqual(w._heat_ax.get_title(), 'Deferred title')
            self.assertEqual(w.workspace.active.view.annotations['plot_title'], 'Deferred title')
            QCoreApplication.processEvents()
            QTest.qWait(70)
            QCoreApplication.processEvents()
            self.assertEqual(layout.call_count, 1)
            self.assertTrue(w._background_jobs.busy)
        release.set()
        wait_jobs(w)
        self.assertTrue(w.request_outer_view('workspace'))
        QTest.qWait(350)
        self.assertIn('Deferred title', w.plot_page.rendered_page.svg_bytes.decode())

    def test_gate_transfer_keeps_batch_peaks_and_unchanged_formal_owner(self):
        w = self.w
        w.set_plot_presentation(replace(w.workspace.active.presentation, include_peak_labels=True))
        w.batch_peak_map = {0: np.array([5]), 1: np.array([12])}
        w._set_current_gate_from_index(0)
        w._render_plot_page()
        before = w._plot_owner_token()
        w._set_current_gate_from_index(1)
        np.testing.assert_equal(w.peak_indices, [12])
        self.assertEqual(w._peak_gate_idx, 1)
        self.assertEqual(w._plot_owner_token(), before)
        w.peak_indices = np.array([15])
        w._update_spectrum_only()
        self.assertNotEqual(w._plot_owner_token(), before)
        w._render_plot_page()
        self.assertIn(1.15, [peak.x for peak in w.plot_page.preview.snapshot.peaks])

    def test_no_peak_spectrum_refresh_does_not_retire_formal_page(self):
        w = self.w
        w.set_plot_presentation(replace(w.workspace.active.presentation, include_peak_labels=True))
        w._render_plot_page()
        page = w.plot_page.rendered_page
        w._set_current_gate_from_index(1)
        w._update_spectrum_only()
        self.assertTrue(w.plot_page.rendered_page is page, 'Unchanged formal peak labels must retain current bytes')

    def test_shared_x_limit_updates_auto_ranges_and_stats_once_from_either_axes(self):
        w = self.w
        for ax, limits in ((w._heat_ax, (1.2, 1.8)), (w._spec_ax, (1.3, 1.7))):
            with patch.object(np, 'nanpercentile', wraps=np.nanpercentile) as percentile, \
                 patch.object(w, '_visible_color_values', wraps=w._visible_color_values) as visible:
                ax.set_xlim(*limits)
                self.assertEqual(percentile.call_count, 1)
                self.assertEqual(visible.call_count, 1)
            self.assertEqual(w.dat_properties.number('xmin'), limits[0])
            np.testing.assert_allclose(w._heat_mesh.get_clim(), w._last_valid_color_limits)
            self.assertIn('visible valid cells', dat.state(w)['color_clipping_status'])

    def test_numeric_x_range_clipping_counts_the_new_view_once(self):
        w = self.w
        with patch.object(w, '_visible_color_values', wraps=w._visible_color_values) as visible:
            w.dat_properties.draftNumber('xmin', '1.5', w.dat_properties.ownerToken)
            self.assertTrue(w.dat_properties.commitNumber('xmin', w.dat_properties.ownerToken))
            self.assertIn('of 150 visible valid cells', dat.state(w)['color_clipping_status'])
            self.assertEqual(visible.call_count, 1)
        self.assertEqual(w._heat_ax.get_xlim()[0], 1.5)

    def test_locked_color_bounds_skip_percentiles_and_refresh_visible_statistics(self):
        w = self.w
        dat.setup_number(w, 'vmin', 50)
        dat.setup_number(w, 'vmax', 100)
        dat.set_flag(w, 'vmin_lock', True)
        dat.set_flag(w, 'vmax_lock', True)
        with patch.object(np, 'nanpercentile', wraps=np.nanpercentile) as percentile:
            w._heat_ax.set_xlim(1., 1.09)
            self.assertEqual(percentile.call_count, 0)
        np.testing.assert_equal(w._heat_mesh.get_clim(), (50., 100.))
        self.assertEqual(dat.state(w)['color_clipping_status'],
            'Below: 30 (100.00%) · Above: 0 (0.00%) of 30 visible valid cells')

    def test_marker_only_draws_reuse_contrast_and_layout_reuses_it(self):
        w = self.w
        w._drag_peak_arr_idx, w._drag_current_x = 0, 1.2
        w._update_spectrum_only()
        w.canvas.draw()
        self.settle()
        mesh = w._heat_mesh
        with patch.object(mesh, 'draw', wraps=mesh.draw) as draw:
            for value in (1.3, 1.4, 1.5):
                w._on_canvas_motion(SimpleNamespace(inaxes=w._spec_ax, xdata=value))
                w.canvas.draw()
            self.assertEqual(draw.call_count, 3, 'Each figure draw must rasterize the heatmap only once')
        with patch.object(svg_export, 'RendererAgg', wraps=svg_export.RendererAgg) as underlay:
            w._layout_live_plots()
            self.assertEqual(underlay.call_count, 0)
        self.assertAlmostEqual(w._drag_scatter.get_offsets()[0, 0], 1.5)

    def test_cached_contrast_refreshes_for_inplace_data_mask_geometry_and_tick_edits(self):
        # The shared helper's real colored marks must remain correct after cache hits.
        from tests import test_heatmap_tick_contrast as contrast_helpers
        helper = contrast_helpers.HeatmapTickContrastTests()
        figure, canvas, ax = helper.axes()
        mesh = ax.pcolormesh([0., 1., 2.], [0., 1., 2.], np.zeros((2, 2)), cmap='jet', norm=Normalize(0, 1))
        ax.set_xlim(0., 2.); ax.set_ylim(.1, 1.9); ax.set_xticks([.5, 1.5])
        helper.update(ax, canvas)
        with patch.object(mesh, 'draw', wraps=mesh.draw) as draw:
            svg_export._update_heatmap_tick_contrast(ax, canvas.get_renderer())
            self.assertEqual(draw.call_count, 0)
        mesh.get_array()[...] = .65
        helper.update(ax, canvas)
        self.assertEqual(to_hex(ax.xaxis.get_major_ticks()[0].tick1line.get_color()), '#000000')

        mesh.get_array().mask = np.ones((2, 2), dtype=bool)
        helper.update(ax, canvas)
        self.assertEqual(to_hex(ax.xaxis.get_major_ticks()[0].tick1line.get_color()), '#000000')
        mesh.set_array(np.zeros((2, 2))); helper.update(ax, canvas)
        self.assertEqual(to_hex(ax.xaxis.get_major_ticks()[0].tick1line.get_color()), '#ffffff')
        for action in (lambda: figure.set_size_inches(5, 4),
                       lambda: ax.set_position([.1, .1, .8, .8]),
                       lambda: ax.set_xticks([.25, 1.25]),
                       lambda: mesh.get_coordinates().__setitem__((slice(None), slice(None), 0), 10.)):
            action()
            with patch.object(svg_export, 'RendererAgg', wraps=svg_export.RendererAgg) as underlay:
                svg_export._update_heatmap_tick_contrast(ax, canvas.get_renderer())
                self.assertEqual(underlay.call_count, 1)
        self.assertEqual(to_hex(ax.xaxis.get_major_ticks()[0].tick1line.get_color()), '#000000')

    def test_marker_contrast_does_not_copy_contiguous_fortran_data(self):
        w = self.w
        w._heat_mesh.set_array(np.asfortranarray(w._heat_mesh.get_array()))
        w.canvas.draw()
        self.settle()
        original = np.ascontiguousarray
        copies = []
        def observe(values, *args, **kwargs):
            result = original(values, *args, **kwargs)
            if np.asarray(values).nbytes >= 100 and not np.shares_memory(result, values):
                copies.append(result.shape)
            return result
        with patch.object(np, 'ascontiguousarray', side_effect=observe):
            svg_export._update_heatmap_tick_contrast(w._heat_ax, w.canvas.get_renderer())
        self.assertEqual(copies, [], 'Marker-only contrast must hash contiguous buffers without copying them')

    def test_memory_order_change_with_identical_physical_bytes_refreshes_contrast(self):
        from tests import test_heatmap_tick_contrast as contrast_helpers
        helper = contrast_helpers.HeatmapTickContrastTests()
        _figure, canvas, ax = helper.axes()
        mesh = ax.pcolormesh([0., 1., 2.], [0., 1., 2.], [[0., .65], [0., .65]],
                             cmap='jet', norm=Normalize(0, 1))
        ax.set_xlim(0., 2.); ax.set_ylim(.1, 1.9); ax.set_xticks([.5, 1.5])
        helper.update(ax, canvas)
        self.assertEqual([to_hex(t.tick1line.get_color()) for t in ax.xaxis.get_major_ticks()],
                         ['#ffffff', '#000000'])
        # Physical bytes are unchanged, but the logical bright half is now above.
        mesh.set_array(np.array([[0., 0.], [.65, .65]], order='F'))
        helper.update(ax, canvas)
        self.assertEqual([to_hex(t.tick1line.get_color()) for t in ax.xaxis.get_major_ticks()],
                         ['#ffffff', '#ffffff'])
