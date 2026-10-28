"""Production Analysis drawing hosts preserve canonical ownership and science."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from matplotlib.backends.backend_agg import FigureCanvasAgg

from spectra_app.formal_preview_controller import FormalPreviewController
from spectra_app.analysis_linecut_controller import AnalysisLinecutController
from tests import test_analysis_workbench as wb
from tests import analysis_quick_helpers as aq
from tests.settings_helpers import wait_for_background_jobs
from tests.formal_quick_helpers import item, pump
from tests.quick_migration_case import QuickMigrationCase


class AnalysisDrawingQuickTests(QuickMigrationCase):
    setUp = wb.AnalysisWorkbenchTests.setUp
    dispose = wb.AnalysisWorkbenchTests.dispose
    enter = wb.AnalysisWorkbenchTests.enter
    find = wb.AnalysisWorkbenchTests.find

    def show_analysis(self):
        self.w.root_window.resize(1500, 920)
        self.w.root_window.show()
        graph = self.enter()
        pump(50)
        self.w._workbench_render()
        self.w._plot_render_timer.stop()
        self.w._workbench_render_timer.stop()
        pump(35)
        return graph

    def assert_exact_page(self):
        page = self.w.plot_page.rendered_page
        self.assertIsNotNone(page)
        self.assertIs(self.w.workbench_preview.page, page)
        self.assertEqual(self.w.workbench_preview.svg_bytes, page.svg_bytes)
        self.assertEqual(self.w.copy_graph_svg().svg_bytes, page.svg_bytes)

    def test_production_named_surfaces_are_loaded_quick_scenes(self):
        self.show_analysis()
        preview, linecut = self.w.workbench_preview, self.w.workbench_linecut_canvas
        self.assertIsInstance(preview, FormalPreviewController)
        self.assertTrue(preview.readOnly)
        self.assertIsInstance(linecut, AnalysisLinecutController)
        for controller, name in ((preview, 'applicationWorkbenchPreview'),
                                 (linecut, 'applicationLinecut')):
            surface = item(self.w, name)
            self.assertIsInstance(surface, QQuickItem)
            self.assertTrue(surface.isVisible())
            self.assertTrue(controller.imageUrl)
        self.assertTrue(item(self.w, 'applicationLinecut').property('loadedReady'))
        self.assertIsNotNone(item(item(self.w, 'applicationWorkbenchPreview'), 'formalReadonlyPointer'))
        self.assertIsNotNone(item(item(self.w, 'applicationLinecut'), 'analysisLinecutImage'))
        self.assertIsInstance(self.w.workbench_linecut_figure.canvas, FigureCanvasAgg)
        self.assertFalse(self.w.root_window.grabWindow().isNull())
        self.assert_exact_page()

    def test_canonical_rejection_and_wrong_active_document_cannot_publish(self):
        graph = self.show_analysis()
        preview, target = self.w.workbench_preview, self.w.plot_page
        previous = preview.page
        with patch.object(target, 'commit_rendered_page', return_value=False) as commit, \
                patch.object(preview, 'set_page', wraps=preview.set_page) as publish:
            self.w._workbench_render()
        commit.assert_called_once()
        publish.assert_not_called()
        self.assertIs(preview.page, previous)
        self.assertEqual(preview.svg_bytes, previous.svg_bytes)
        original_commit = target.commit_rendered_page
        def stale(page):
            self.w._invalidate_graph_document((self.session.dataset_id, graph.graph_id))
            return original_commit(page)
        with patch.object(target, 'commit_rendered_page', side_effect=stale), \
                patch.object(preview, 'set_page', wraps=preview.set_page) as publish:
            self.w._workbench_render()
        publish.assert_not_called()
        self.assertIs(preview.page, previous)
        self.assertFalse(preview.canView)
        self.w._workbench_render()
        self.assert_exact_page()
        previous = preview.page
        def switch_active(page):
            accepted = original_commit(page)
            self.session.activate_graph(self.source_id)
            self.w._workbench_refresh()
            return accepted
        with patch.object(target, 'commit_rendered_page', side_effect=switch_active), \
                patch.object(preview, 'set_page', wraps=preview.set_page) as publish:
            self.w._workbench_render((self.session.dataset_id, graph.graph_id))
        publish.assert_not_called()
        self.assertIs(preview.page, previous)
        self.assertFalse(preview.canView)

    def test_real_wheel_drag_doubleclick_preserve_export_and_science(self):
        graph = self.show_analysis()
        self.find(all_gates=False)
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        pump(35)
        self.w._workbench_render()
        self.w._plot_render_timer.stop()
        self.w._workbench_render_timer.stop()
        preview = self.w.workbench_preview
        page, svg = preview.page, preview.svg_bytes
        doc = graph.analysis
        pools, seeds = deepcopy(doc.candidate_pools), deepcopy(doc.seeds_by_gate)
        cube = doc.science.current_cube.z.copy()
        fits = {gate: (fit, fit.x.copy(), fit.y_fit.copy(), deepcopy(fit.baseline),
                       [value.copy() for value in fit.components])
                for gate, fit in doc.science.batch_fit_result_map.items()}
        surface = item(self.w, 'applicationWorkbenchPreview')
        start = surface.mapToScene(QPointF(surface.width() / 2, surface.height() / 2)).toPoint()
        receiver = self.w.root_window
        paper = item(surface, 'formalPaper')
        with patch('spectra_app.svg_export.render_hybrid_svg') as render, \
                patch('spectra_app.formal_preview_controller.render_svg_image') as raster, \
                patch('spectra_app.peak_candidates.collect_peak_candidates') as detect, \
                patch('spectra_app.fitting.fit_multi_peak') as refit:
            event = QWheelEvent(QPointF(start), QPointF(receiver.mapToGlobal(start)), QPoint(), QPoint(0, 120),
                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            QCoreApplication.sendEvent(receiver, event)
            pump()
            self.assertTrue(event.isAccepted())
            self.assertAlmostEqual(preview.relativeZoom, 1.2)
            origin = paper.position()
            QTest.mousePress(receiver, Qt.LeftButton, pos=start)
            QTest.mouseMove(receiver, start + QPoint(32, 19))
            QTest.mouseRelease(receiver, Qt.LeftButton, pos=start + QPoint(32, 19))
            pump()
            self.assertEqual(paper.position()-origin, QPointF(32, 19))
            QTest.mouseDClick(receiver, Qt.LeftButton, pos=start)
            QTest.mouseRelease(receiver, Qt.LeftButton, pos=start)
            pump()
            self.assertEqual(preview.relativeZoom, 1.)
            self.assertEqual((preview.panX, preview.panY), (0., 0.))
        for mock in (render, raster, detect, refit):
            mock.assert_not_called()
        self.assertIs(preview.page, page)
        self.assertEqual(preview.svg_bytes, svg)
        self.assertEqual(doc.candidate_pools, pools)
        self.assertEqual(doc.seeds_by_gate, seeds)
        np.testing.assert_array_equal(doc.science.current_cube.z, cube)
        for gate, (fit, x, y, baseline, components) in fits.items():
            self.assertIs(doc.science.batch_fit_result_map[gate], fit)
            for actual, expected in ((fit.x, x), (fit.y_fit, y), (fit.baseline, baseline),
                                     *zip(fit.components, components)):
                np.testing.assert_array_equal(actual, expected)
        self.assert_exact_page()

    def test_shared_owner_handoff_retires_inflight_gesture(self):
        graph = self.show_analysis()
        preview = self.w.workbench_preview
        surface = item(self.w, 'applicationWorkbenchPreview')
        start = surface.mapToScene(QPointF(surface.width() / 2, surface.height() / 2)).toPoint()
        receiver = self.w.root_window
        QTest.mousePress(receiver, Qt.LeftButton, pos=start)
        QTest.mouseMove(receiver, start + QPoint(12, 8))
        pump()
        pan = preview.panX, preview.panY
        generation = preview.gestureGeneration
        self.w._workbench_duplicate()
        pump(35)
        self.assertIs(self.w.workbench_preview, preview)
        self.assertIsNot(self.session.active_graph, graph)
        self.assertNotEqual(preview.page.owner_token[-1], graph.graph_id)
        QTest.mouseMove(receiver, start + QPoint(80, 60))
        QTest.mouseRelease(receiver, Qt.LeftButton, pos=start + QPoint(80, 60))
        preview.viewMove(generation, 900, 700)
        self.assertFalse(preview.viewFit(generation))
        pump()
        self.assertEqual((preview.panX, preview.panY), pan)
        self.assert_exact_page()
        # A revision on the same document retires gestures before rerendering.
        QTest.mousePress(receiver, Qt.LeftButton, pos=start)
        generation = preview.gestureGeneration
        self.w._workbench_changed()
        self.assertGreater(preview.gestureGeneration, generation)
        self.assertFalse(preview.canView)
        QTest.mouseRelease(receiver, Qt.LeftButton, pos=start + QPoint(20, 20))
        pump()

    def test_rebind_and_dataset_change_retire_shared_gestures(self):
        graph = self.show_analysis()
        preview = self.w.workbench_preview
        surface = item(self.w, 'applicationWorkbenchPreview')
        start = surface.mapToScene(QPointF(surface.width() / 2, surface.height() / 2)).toPoint()
        receiver = self.w.root_window
        def press():
            QTest.mousePress(receiver, Qt.LeftButton, pos=start)
            QTest.mouseMove(receiver, start + QPoint(10, 5))
            pump()
            return preview.gestureGeneration, (preview.panX, preview.panY)
        def stale_release(generation, pan):
            QTest.mouseMove(receiver, start + QPoint(80, 60))
            QTest.mouseRelease(receiver, Qt.LeftButton, pos=start + QPoint(80, 60))
            preview.viewMove(generation, 900, 700)
            self.assertFalse(preview.viewFit(generation))
            pump()
            self.assertEqual((preview.panX, preview.panY), pan)
            self.assert_exact_page()
        version = graph.analysis.input_version
        self.session.current_cube = replace(self.session.current_cube, z=self.session.current_cube.z * 2)
        aq.set_choice(self.w, 'inputMode', 'processed')
        generation, pan = press()
        with patch.object(self.w, '_workbench_confirm_rebind', return_value='replace'):
            aq.command(self.w, 'rebind')
        self.assertNotEqual(graph.analysis.input_version, version)
        stale_release(generation, pan)
        generation, pan = press()
        path = self.folder / 'second-owner.dat'
        path.write_text('Energy(eV) 0 1\n1 2 3\n1.5 3 4\n2 4 5\n')
        self.w._load_dat_file_direct(path)
        second = self.w.workspace.active
        self.w.open_graph_document()
        self.w._open_analysis(('heatmap', (second.dataset_id, second.active_graph_id)))
        self.assertNotEqual(second.dataset_id, self.session.dataset_id)
        self.assertEqual(preview.page.owner_token[0], second.dataset_id)
        stale_release(generation, pan)

    def test_linecut_gate_seeds_fit_components_and_split_restore(self):
        graph = self.show_analysis()
        self.find()
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        self.w._set_current_gate_from_index(2, update_plot=True)
        pump(50)
        doc = graph.analysis
        figure = self.w.workbench_linecut_figure
        ax = figure.axes[0]
        fit = doc.science.batch_fit_result_map[2]
        np.testing.assert_array_equal(ax.lines[0].get_xdata(), doc.science.current_cube.energy)
        np.testing.assert_array_equal(ax.lines[0].get_ydata(), doc.science.current_cube.z[2])
        self.assertEqual(ax.lines[0].get_label(), 'Bound input')
        np.testing.assert_array_equal(ax.lines[1].get_xdata(), fit.x)
        np.testing.assert_array_equal(ax.lines[1].get_ydata(), fit.y_fit)
        self.assertEqual(ax.lines[1].get_label(), 'Total fit')
        self.assertEqual(len(ax.lines), 2 + len(fit.components))
        for line, component in zip(ax.lines[2:], fit.components):
            np.testing.assert_array_equal(line.get_ydata(), component + fit.baseline)
        positions = sorted(float(value) for collection in ax.collections for value in collection.get_offsets()[:, 0])
        self.assertEqual(positions, sorted(seed.position for seed in doc.accepted_seeds(2)))
        self.assertIn('Gate 5', ax.get_title())
        self.assertEqual(ax.get_xlabel(), doc.science.current_cube.energy_label)
        self.assertIn('Total fit', [text.get_text() for text in ax.legend_.texts])
        linecut = item(self.w, 'applicationLinecut')
        previous_height = linecut.height()
        boundary = linecut.mapToScene(QPointF(linecut.width() / 2, -3)).toPoint()
        receiver = self.w.root_window
        QTest.mouseMove(receiver, boundary)
        pump()
        QTest.mousePress(receiver, Qt.LeftButton, pos=boundary)
        pump()
        QTest.mouseMove(receiver, boundary - QPoint(0, 15), delay=15)
        pump()
        QTest.mouseMove(receiver, boundary - QPoint(0, 35), delay=15)
        pump()
        QTest.mouseRelease(receiver, Qt.LeftButton, pos=boundary - QPoint(0, 35))
        pump()
        height, svg = linecut.height(), self.w.workbench_preview.svg_bytes
        self.assertGreater(height - previous_height, 20, 'The real split handle must move')
        self.assertAlmostEqual(self.w.linecutHeight, height, delta=2)
        with patch('spectra_app.fitting.fit_multi_peak') as refit, \
                patch('spectra_app.peak_candidates.collect_peak_candidates') as detect:
            self.w._workbench_toggle_linecut(False)
            pump()
            self.assertFalse(linecut.isVisible())
            self.w._workbench_toggle_linecut(True)
            pump()
        refit.assert_not_called()
        detect.assert_not_called()
        self.assertTrue(linecut.isVisible())
        self.assertLessEqual(abs(linecut.height() - height), 2)
        self.assertIs(self.w.workbench_linecut_figure, figure)
        self.assertIs(figure.axes[0], ax)
        self.assertEqual(self.w.workbench_preview.svg_bytes, svg)
