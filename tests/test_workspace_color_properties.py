"""Complete heatmap color controls follow the active Properties owner."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from tests.quick_migration_case import QuickMigrationCase
from tests.formal_quick_helpers import item, pump
from tests.dat_properties_quick_helpers import dat_item, reveal, edit_number, choose, set_choice, state
from tests.formal_quick_helpers import ensure_visible, input_receiver


class WorkspaceColorPropertiesTests(QuickMigrationCase):
    def setUp(self):
        if os.name == 'nt' and self._testMethodName in (
                'test_workspace_global_control_replaces_two_rendered_bars_without_changing_data',
                'test_workspace_color_edits_retain_each_graph_owner'):
            self.enterContext(patch.dict(os.environ, QT_QPA_PLATFORM='windows'))
        super().setUp()
        self.callback_errors = []
        capture = patch.object(sys, 'excepthook', lambda *error: self.callback_errors.append(error))
        capture.start()
        self.addCleanup(capture.stop)
        self.w = self.controller
        self.errors = []
        self.w._show_error = self.errors.append
        path = self.folder / 'spectrum.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(15)))
        self.w._load_dat_file_direct(path)
        self.w.open_graph_document()
        self.w.request_task(2)
        self.w.root_window.resize(1500, 920)
        self.w.root_window.show()
        self.w.root_window.requestActivate()
        pump()

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.assertEqual(self.callback_errors, [])
        self.assertFalse(self.quick_errors)
        super().tearDown()

    def assert_reachable(self, page, name):
        self.assertIs(self.w.propertiesController, page)
        control = reveal(self.w, name)
        panel = item(self.w, 'sharedDatProperties')
        self.assertTrue(panel.isAncestorOf(control))
        self.assertTrue(control.isVisible() and control.isEnabled())
        self.assertGreater(control.width(), 0)
        self.assertGreater(control.height(), 0)
        viewport = item(self.w, 'propertiesScroll').property('contentItem')
        center = control.mapToItem(viewport, QPointF(control.width()/2, control.height()/2))
        self.assertTrue(0 <= center.x() <= viewport.width() and 0 <= center.y() <= viewport.height(),
                        'The actual Properties viewport must expose its active DAT editor')

    def assert_quick_reachable(self, page, name):
        self.assertIs(self.w.propertiesController, page)
        control = ensure_visible(item(self.w, name))
        self.assertTrue(item(self.w, 'formalProperties').isAncestorOf(control))
        self.assertTrue(control.isVisible() and control.isEnabled())
        self.assertGreater(control.width(), 0)
        self.assertGreater(control.height(), 0)
        viewport = item(self.w, 'propertiesScroll').property('contentItem')
        center = control.mapToItem(viewport, QPointF(control.width()/2, control.height()/2))
        self.assertTrue(0 <= center.x() <= viewport.width() and 0 <= center.y() <= viewport.height(),
                        'The actual Properties viewport must expose its formal editor')

    def render(self):
        self.w._render_plot_page()
        pump()
        page = self.w.plot_page.rendered_page
        self.assertIsNotNone(page, self.w.plot_page.status_text)
        return page

    def test_workspace_global_control_replaces_two_rendered_bars_without_changing_data(self):
        w = self.w
        session = w.workspace.active
        base, current = session.base_cube.z.copy(), session.current_cube.z.copy()
        set_choice(w, 'color_scale', '2')
        self.assertEqual(len(self.render().colorbar_bounds), 2)
        choose(w, 'color_scale', 'global')
        self.assertEqual(len(self.render().colorbar_bounds), 1)
        self.assertEqual(len(w._heat_colorbars), 1)
        self.assertEqual(session.view.color_scale_mode, 'global')
        np.testing.assert_array_equal(session.base_cube.z, base)
        np.testing.assert_array_equal(session.current_cube.z, current)
        choose(w, 'mapping', 'symlog')
        for name in ('colormap', 'mapping', 'linthresh', 'vmin', 'vmin_lock',
                     'vmax', 'vmax_lock', 'shared_auto_colors', 'fix_regions'):
            self.assert_reachable(w.plot_page, name)
        choose(w, 'color_scale', '3')
        for name in ('edge0', 'edge1'):
            self.assert_reachable(w.plot_page, name)

    def test_workspace_color_edits_retain_each_graph_owner(self):
        w = self.w
        session = w.workspace.active
        original = session.current_cube.z.copy()
        choose(w, 'color_scale', '2')
        graph_a = session.active_graph
        graph_b = w.create_graph('Graph B')
        w.open_graph_document(session.dataset_id, graph_b.graph_id)
        saved_a = deepcopy(graph_a.view)
        choose(w, 'color_scale', 'global')
        choose(w, 'mapping', 'log')
        choose(w, 'colormap', 'magma')
        edit_number(w, 'vmin', '2', commit=True)
        self.assertTrue(state(w)['vmin_lock'])
        self.assertEqual(len(self.render().colorbar_bounds), 1)
        self.assertEqual(graph_a.view, saved_a)
        w.open_graph_document(session.dataset_id, graph_a.graph_id)
        self.assertEqual(state(w)['color_scale'], '2')
        self.assertEqual(len(self.render().colorbar_bounds), 2)
        w.open_graph_document(session.dataset_id, graph_b.graph_id)
        self.assertEqual(state(w)['color_scale'], 'global')
        self.assertEqual(state(w)['mapping'], 'log')
        self.assertEqual(state(w)['colormap'], 'magma')
        self.assertEqual(w._dat_view().bounds['vmin'], 2.)
        self.assertTrue(state(w)['vmin_lock'])
        np.testing.assert_array_equal(session.current_cube.z, original)

    def test_same_color_controls_move_between_graph_pages_and_analysis(self):
        w = self.w
        session = w.workspace.active
        graph_a = session.active_graph
        page_a = w.plot_page
        host = item(w, 'sharedDatProperties')
        controls = tuple(dat_item(w, name) for name in ('color_scale', 'colormap', 'mapping', 'shared_auto_colors'))
        graph_b = w.create_graph('Graph B')
        page_b = w.open_graph_document(session.dataset_id, graph_b.graph_id)
        self.assertIsNot(page_a, page_b)
        for _ in range(2):
            for graph, page, inactive in ((graph_a, page_a, page_b), (graph_b, page_b, page_a)):
                w.open_graph_document(session.dataset_id, graph.graph_id)
                self.assertIs(w.graph_documents.document_for((session.dataset_id, graph.graph_id)), page)
                for control in controls:
                    self.assert_reachable(page, control.objectName())
                    self.assertIsNot(w.propertiesController, inactive)
                self.assertTrue(w.request_outer_view('analysis'))
                for control in controls:
                    self.assert_reachable(w.plot_page, control.objectName())
                self.assertEqual(w.outerView, 'analysis')
                self.assertTrue(w.request_outer_view('workspace'))
                self.assert_reachable(page, 'color_scale')
                self.assertIs(item(w, 'sharedDatProperties'), host)
                self.assertEqual(host.objectName(), 'sharedDatProperties')
        self.assertEqual(controls, tuple(dat_item(w, name) for name in
                         ('color_scale', 'colormap', 'mapping', 'shared_auto_colors')))

    def test_colorbar_doubleclick_reveals_complete_properties_and_keeps_tick_focus(self):
        w = self.w
        set_choice(w, 'color_scale', '2')
        rendered = self.render()
        page = w.plot_page
        left, bottom, width, height = rendered.colorbar_bounds['region-2']
        w.dat_details_action.setChecked(False)
        w.request_task(0)
        pump()
        # Hiding Properties can resize the preview; use its final dimensions.
        paper_width, paper_height = page.preview.paper_display_size()
        key = w.graph_documents.registry.active_key
        scene = w.graph_documents._presentations[key].mount.item
        paper = item(scene, 'formalPaper')
        point = paper.mapToScene(QPointF((left+width/2)*paper_width,
            (1-bottom-height/2)*paper_height)).toPoint()
        receiver = input_receiver(w, paper)
        QTest.mouseDClick(receiver, Qt.LeftButton, pos=point)
        QTest.mouseRelease(receiver, Qt.LeftButton, pos=point)
        pump(70)
        self.assertTrue(w.propertiesVisible)
        self.assertIs(w.propertiesController, page)
        self.assertEqual(w.taskIndex, 2)
        self.assertEqual(page.formal.state['selectedBar'], 'region-2')
        self.assertTrue(item(w, 'tick_values').hasActiveFocus()
                        or item(w, 'custom_ticks').hasActiveFocus())
        self.assertTrue(item(w, 'applicationProperties').isAncestorOf(item(w, 'sharedDatProperties')))
        self.assertTrue(dat_item(w, 'color_scale').isVisible())
        self.assertTrue(dat_item(w, 'mapping').isVisible())
        self.assert_quick_reachable(page, 'bar_label_format')
        self.assert_quick_reachable(page, 'custom_ticks')


if __name__ == '__main__':
    unittest.main()
