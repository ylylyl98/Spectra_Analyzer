"""Cached owner activation lays out the final live view once, without stale exports."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtCore import QCoreApplication

from spectra_app import main_window
from tests import dat_properties_quick_helpers as dp
from tests.quick_migration_case import QuickMigrationCase




class SwitchLayoutPerformanceTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        folder = self.folder
        self.w = self.controller
        self.w._show_error = self.fail
        self.w.root_window.resize(1500, 1000)
        self.w.root_window.show()
        self.assertTrue(self.w.request_outer_view('analysis'))
        self.sessions = []
        energy, gates = np.linspace(1., 5., 121), np.linspace(-4., 4., 9)
        for index in range(2):
            path = folder / f'switch-{index}.dat'
            z = 3 + index + np.sin(energy[:, None]) + gates[None, :] / 20
            np.savetxt(path, np.column_stack((energy, z)),
                       header='Energy(eV) ' + ' '.join(map(str, gates)), comments='')
            self.w._load_dat_file_direct(path)
            self.sessions.append(self.w.workspace.active)
        self.assertEqual(self.w.outerView, 'analysis')
        self.expected = {}
        for index, session in enumerate(self.sessions):
            self.w.activate_dataset(session.dataset_id)
            dp.set_flag(self.w, 'fix_annotations', False)
            self.w.dat_properties.setLabel('plot_title', f'Owner {index}: final title', self.w.dat_properties.ownerToken)
            self.w.dat_properties.setLabel('y_axis_name', f'Bias {index}', self.w.dat_properties.ownerToken)
            self.w.dat_properties.setLabel('y_axis_unit', 'mV', self.w.dat_properties.ownerToken)
            dp.set_flag(self.w, 'signal_title_auto', False)
            self.w.dat_properties.setLabel('signal_title', f'Signal {index}', self.w.dat_properties.ownerToken)
            presentation = session.presentation
            self.w.set_plot_presentation(replace(presentation,
                frame_width_in=2.5 + index / 5, frame_height_in=1.8 + index / 5,
                axis=replace(presentation.axis, x_major_count=5, y_major_count=4)))
            self.w.plot_page.set_zoom('100%')
            self.w.current_gate = float(index + 1)
            self.w._update_spectrum_only()
            self.w._heat_ax.set_xlim(1.123456789 + index / 10, 4.876543219)
            self.w._heat_ax.set_ylim(-3.876543219, 3.123456789 - index / 10)
            self.w._heat_mesh.set_clim(2.123456789 + index, 4.987654321 + index)
            self.w._finish_graph_editors()
            self.w._restore_effective_plot_limits(session.view.plot_recipe(
                session.current_cube, session.applied, session.presentation))
            self.settle()
            self.expected[session.dataset_id] = self.view()

    def tearDown(self):
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def settle(self):
        # Includes the existing 40 ms view-layout timer and queued canvas draws.
        QCoreApplication.processEvents()
        QTest.qWait(70)
        QCoreApplication.processEvents()

    def view(self):
        w = self.w
        return dict(xlim=w._heat_ax.get_xlim(), ylim=w._heat_ax.get_ylim(),
                    clim=w._heat_mesh.get_clim(), title=w._heat_ax.get_title(),
                    xlabel=w._heat_ax.get_xlabel(), ylabel=w._heat_ax.get_ylabel(),
                    signal=w._heat_colorbar.ax.get_xlabel(), gate=w.current_gate,
                    xticks=w._heat_ax.get_xticks().copy(), yticks=w._heat_ax.get_yticks().copy())

    def assert_view(self, session, *, check_geometry=True):
        w = self.w
        actual, expected = self.view(), self.expected[session.dataset_id]
        for field in ('xlim', 'ylim', 'clim', 'xticks', 'yticks'):
            np.testing.assert_array_equal(actual[field], expected[field], err_msg=field)
        for field in ('title', 'xlabel', 'ylabel', 'signal', 'gate'):
            self.assertEqual(actual[field], expected[field], field)
        self.assertIs(w.workspace.active, session)
        self.assertIs(w.current_cube, session.current_cube)
        gate_index = int(np.argmin(abs(session.current_cube.gate - w.current_gate)))
        np.testing.assert_array_equal(w._spec_ax.lines[0].get_xdata(), session.current_cube.energy)
        np.testing.assert_array_equal(w._spec_ax.lines[0].get_ydata(), session.current_cube.z[gate_index])
        if check_geometry:
            scale = 96 * w._analysis_display_scale() * w.canvas.device_pixel_ratio / w.figure.dpi
            frame = w._heat_ax.get_position().size * w.figure.get_size_inches()
            np.testing.assert_allclose(frame, np.array([session.presentation.frame_width_in,
                session.presentation.frame_height_in]) * scale, rtol=1e-12, atol=1e-12)
        self.assertFalse(w._restoring_dataset)

    def assert_single_final_layout(self, session, action, *, deferred_geometry=False):
        w = self.w
        snapshots = []
        position = w._position_live_plots
        def observe():
            snapshots.append((w.workspace.active_dataset_id, self.view()))
            return position()
        with patch.object(w, '_position_live_plots', side_effect=observe), \
             patch.object(w.figure, 'draw_without_rendering', wraps=w.figure.draw_without_rendering) as measure:
            action()
            # Actual Quick view entry has asynchronous viewport reflow. Its
            # owner, science and exact limits must nevertheless change now.
            self.assert_view(session, check_geometry=not deferred_geometry)
            self.settle()
            self.assert_view(session)
        self.assertEqual(len(snapshots), 1, 'Only the final incoming view needs renderer-based layout')
        self.assertEqual(measure.call_count, 1, 'Queued work must not repeat label measurements')
        self.assertEqual(snapshots[0][0], session.dataset_id)
        for field in ('xlim', 'ylim', 'clim'):
            np.testing.assert_array_equal(snapshots[0][1][field], self.expected[session.dataset_id][field])

    def test_dataset_activation_measures_only_final_view_and_preserves_processing_drafts(self):
        w = self.w
        incoming, outgoing = self.sessions
        w.processing.setMode('energy_second_derivative')
        w.processing.setWindowDraft('31')
        pending = incoming.pending
        self.assert_single_final_layout(incoming, lambda: w.activate_dataset(incoming.dataset_id))
        self.assertEqual(outgoing.pending.window_length, 31)
        self.assertIs(incoming.pending, pending)
        w.processing.setWindowDraft('invalid owner draft')
        w.activate_dataset(outgoing.dataset_id)
        self.assertEqual(w.processing.windowDraft, '31')
        w.activate_dataset(incoming.dataset_id)
        self.assertEqual(w.processing.windowDraft, 'invalid owner draft')
        self.assert_view(incoming)

    def test_page_zoom_change_is_applied_before_single_final_layout(self):
        w = self.w
        incoming, outgoing = self.sessions
        w.activate_dataset(incoming.dataset_id)
        w.plot_page.set_zoom('150%')
        w.activate_dataset(outgoing.dataset_id)
        self.settle()
        self.assert_single_final_layout(incoming, lambda: w.activate_dataset(incoming.dataset_id))
        self.assertEqual(w.analysis_navigation.zoomText, '150%')

    def test_unchanged_display_zoom_does_not_measure_or_repaint(self):
        w = self.w
        self.settle()
        before = self.view()
        with patch.object(w, '_position_live_plots', wraps=w._position_live_plots) as layout, \
             patch.object(w.canvas, 'draw', wraps=w.canvas.draw) as draw:
            w._sync_display_zoom(w.analysis_navigation.zoomText)
            self.settle()
        self.assertEqual(layout.call_count, 0)
        self.assertEqual(draw.call_count, 0)
        np.testing.assert_array_equal(w._heat_ax.get_xlim(), before['xlim'])

    def test_cached_graph_switch_keeps_valid_owner_bytes_and_export(self):
        w = self.w
        incoming, outgoing = self.sessions
        pages = {}
        for session in self.sessions:
            w.open_graph_document(session.dataset_id)
            w._render_plot_page()
            self.settle()
            pages[session.dataset_id] = w.plot_page.rendered_page
            self.assertIsNotNone(pages[session.dataset_id])
        with patch.object(main_window, 'render_hybrid_svg', wraps=main_window.render_hybrid_svg) as render:
            # Opening the retained page exposes Workspace. Its hidden Analysis
            # geometry must wait until that actual view is entered.
            with patch.object(w, '_position_live_plots', wraps=w._position_live_plots) as layout, \
                 patch.object(w.figure, 'draw_without_rendering', wraps=w.figure.draw_without_rendering) as measure:
                w.open_graph_document(incoming.dataset_id)
                self.assertEqual(w.outerView, 'workspace')
                self.settle()
                self.assertEqual(layout.call_count, 0)
                self.assertEqual(measure.call_count, 0)
            self.assertIs(w.plot_page.rendered_page, pages[incoming.dataset_id])
            self.assertEqual(w.plot_page.expected_owner, w._plot_owner_token())
            self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())
            self.assertFalse(w.plot_page.commit_rendered_page(pages[outgoing.dataset_id]))
            self.assertTrue(w.plot_page.command_enabled('export_svg'))
            output = w.export_plot_page('svg')
            self.assertEqual(output.read_bytes(), pages[incoming.dataset_id].svg_bytes)
            self.assert_single_final_layout(incoming,
                lambda: self.assertTrue(w.request_outer_view('analysis')), deferred_geometry=True)
            # A queued layout must not draw hidden Analysis or retain the
            # outgoing owner when the user returns and changes graphs quickly.
            w.open_graph_document(outgoing.dataset_id)
            with patch.object(w, '_position_live_plots', wraps=w._position_live_plots) as layout, \
                 patch.object(w.figure, 'draw_without_rendering', wraps=w.figure.draw_without_rendering) as measure, \
                 patch.object(w.canvas, 'draw', wraps=w.canvas.draw) as draw:
                self.assertTrue(w.request_outer_view('analysis'))
                self.assertTrue(w.request_outer_view('workspace'))
                w.open_graph_document(incoming.dataset_id)
                self.settle()
                self.assertEqual(w.outerView, 'workspace')
                self.assertEqual(layout.call_count, 0)
                self.assertEqual(measure.call_count, 0)
                self.assertEqual(draw.call_count, 0)
                self.assertTrue(w._annotation_layout_pending)
                self.assert_view(incoming, check_geometry=False)
            self.assert_single_final_layout(incoming,
                lambda: self.assertTrue(w.request_outer_view('analysis')), deferred_geometry=True)
            self.assertIs(w.plot_page.rendered_page, pages[incoming.dataset_id])
        self.assertEqual(render.call_count, 0)

    def test_failed_activation_recovers_owner_and_next_activation_layout(self):
        w = self.w
        previous, incoming = self.sessions
        for method in ('_select_graph_page', '_restore_effective_plot_limits'):
            with self.subTest(failure=method):
                w.activate_dataset(previous.dataset_id)
                self.settle()
                restore = getattr(w, method)
                def fail_incoming(*args, **kwargs):
                    if w.workspace.active is incoming:
                        raise RuntimeError('injected activation failure')
                    return restore(*args, **kwargs)
                with patch.object(w, method, side_effect=fail_incoming):
                    with self.assertRaisesRegex(RuntimeError, 'injected activation failure'):
                        w.activate_dataset(incoming.dataset_id)
                self.assert_view(previous)
                self.assert_single_final_layout(incoming, lambda: w.activate_dataset(incoming.dataset_id))


if __name__ == '__main__':
    unittest.main()
