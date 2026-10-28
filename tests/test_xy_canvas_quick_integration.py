"""XY science and ownership through production Quick scenes and real Qt events."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch
import numpy as np
import shiboken6
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QWheelEvent
from PySide6.QtQuick import QQuickItem
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from spectra_app.plot_presentation import PlotPresentation
from spectra_app.project_io import workspace_digest
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve, document_key
from spectra_app.xy_plot_controller import XYPlotController
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as formal
from tests import xy_canvas_quick_helpers as qc
from tests.xy_helpers import select_xy_rows, set_xy_draft

APP = QCoreApplication


class XYCanvasQuickIntegrationTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        self.workspace = self.controller.workspace
        book = create_workbook(self.workspace)
        x = np.linspace(0, 10, 101)
        self.sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Source',
            np.column_stack((x, 1+x/2, 8-x/3)), ['X', 'A', 'B'])
        self.graph = create_comparison(self.workspace)
        self.graph.presentation = None
        self.curves = [add_curve(self.workspace, self.graph.comparison_id,
                                self.sheet.sheet_id, 0, col) for col in (1, 2)]
        self.controller.open_xy_comparison(self.graph.comparison_id)
        documents = self.controller.graph_documents
        self.page = documents.document_for(documents.registry.active_key)
        self.assertIsInstance(self.page, XYPlotController)
        self.host.window.resize(1100, 760)
        qc.pump(120)
        self.page.canvas.draw()
        self.assertIsInstance(self.page.canvas, FigureCanvasAgg)
        self.assertNotIsInstance(self.page.canvas, QWidget)
        self.assertIsInstance(qc.canvas_host(self.page), QQuickItem)
        self.assertTrue(self.page.canvas_bridge.enabled)
        expected = float(os.environ.get('QT_SCALE_FACTOR', '1'))
        self.assertAlmostEqual(self.host.window.devicePixelRatio(), expected)
        self.assertAlmostEqual(self.page.canvas.device_pixel_ratio, expected)
        self.assertAlmostEqual(self.page.canvas_bridge.contentWidth, self.page.canvas_bridge.viewportWidth)
        self.assertAlmostEqual(self.page.canvas_bridge.contentHeight, self.page.canvas_bridge.viewportHeight)

    def test_real_pointer_pick_control_blank_and_letterbox_physical_transform(self):
        page = self.page
        self.graph.presentation = PlotPresentation(width_in=8, height_in=3,
            frame_width_in=None, frame_height_in=None)
        page.render(); page.canvas_bridge.fit_page(); qc.pump(100)
        self.assertGreater(page.canvas_bridge.contentY, 0)
        select_xy_rows(page, [])
        seen = []
        cid = page.canvas.mpl_connect('button_press_event', seen.append)
        self.addCleanup(page.canvas.mpl_disconnect, cid)
        qc.click_data(page, (2, 2))
        self.assertEqual(page._selected_ids(), {self.curves[0].curve_id})
        self.assertIs(seen[-1].inaxes, page.axes)
        self.assertAlmostEqual(seen[-1].xdata, 2, delta=.04)
        self.assertAlmostEqual(seen[-1].ydata, 2, delta=.04)
        qc.click_data(page, (8, 8-8/3), modifiers=Qt.ControlModifier)
        QTest.keyRelease(qc.canvas_receiver(page), Qt.Key_Control)
        self.assertEqual(page._selected_ids(), {curve.curve_id for curve in self.curves})
        set_xy_draft(page, 'color', 'invalid')
        qc.click_data(page, (.5, .95), axes=True)
        self.assertEqual(page._selected_ids(), {curve.curve_id for curve in self.curves})
        page.revert_editor()
        qc.click_data(page, (.5, .95), axes=True)
        self.assertEqual(page._selected_ids(), set())
        qc.click_data(page, (2, 2))
        QTest.mouseClick(qc.canvas_receiver(page), Qt.LeftButton,
                         pos=qc.canvas_host(page).mapToScene(QPointF(3, 3)).toPoint())
        qc.pump(40)
        self.assertIsNone(seen[-1].inaxes)
        self.assertEqual(page._selected_ids(), set())

    def test_navigation_quick_commands_history_pan_zoom_and_grabbed_outside_release(self):
        page = self.page
        select_xy_rows(page, [0])
        selected = page._selected_ids()
        original = page.axes.get_xlim(), page.axes.get_ylim()
        qc.navigation(page, 'zoom')
        self.assertEqual(str(page.toolbar.mode), 'zoom rect')
        start, end = qc.data_point(page, (2, 2)), qc.data_point(page, (7, 6))
        qc.press(page, start); qc.move(page, end); qc.pump(20)
        self.assertTrue(page.canvas_bridge.rubberband['visible'])
        qc.release(page, end)
        self.assertFalse(page.canvas_bridge.rubberband['visible'])
        zoomed = page.axes.get_xlim(), page.axes.get_ylim()
        np.testing.assert_allclose(zoomed[0], (2, 7), atol=.04)
        self.assertEqual(page._selected_ids(), selected)
        qc.navigation(page, 'back')
        np.testing.assert_allclose(page.axes.get_xlim(), original[0])
        qc.navigation(page, 'forward')
        np.testing.assert_allclose(page.axes.get_xlim(), zoomed[0])
        qc.navigation(page, 'pan')
        qc.drag(page, (3, 3), (4, 4))
        self.assertNotEqual(page.axes.get_xlim(), zoomed[0])
        self.assertEqual(page._selected_ids(), selected)
        start = qc.data_point(page, (.5, .5), axes=True)
        outside = qc.canvas_host(page).mapToScene(QPointF(
            qc.canvas_host(page).width()+50, qc.canvas_host(page).height()+50)).toPoint()
        qc.press(page, start); qc.move(page, outside); qc.release(page, outside)
        self.assertIsNone(page.toolbar._pan_info)
        self.assertIsNone(page.canvas_bridge._gesture_owner)
        self.assertEqual(page.canvas_bridge._held_buttons, set())
        qc.navigation(page, 'home')
        np.testing.assert_allclose(page.axes.get_xlim(), original[0])
        np.testing.assert_allclose(page.axes.get_ylim(), original[1])

    def test_physical_page_resize_preserves_inches_and_reuses_appearance_artists(self):
        page = self.page
        self.graph.presentation = PlotPresentation(width_in=8, height_in=3,
            frame_width_in=None, frame_height_in=None)
        page.render(); page.canvas_bridge.fit_page(); qc.pump(80)
        for size in ((1100, 760), (1450, 600)):
            self.host.window.resize(*size); qc.pump(100); page.canvas.draw()
            bridge = page.canvas_bridge
            np.testing.assert_allclose(page.figure.get_size_inches(), (8, 3))
            self.assertAlmostEqual(page.figure.dpi, bridge.contentWidth*page.canvas.device_pixel_ratio/8)
            self.assertAlmostEqual(bridge.contentWidth/bridge.contentHeight, 8/3, delta=.01)
            self.assertAlmostEqual(2*bridge.contentX+bridge.contentWidth, bridge.viewportWidth)
            self.assertAlmostEqual(2*bridge.contentY+bridge.contentHeight, bridge.viewportHeight)
        axes, line = page.axes, page.axes.lines[0]
        data = line.get_xdata()
        page.toolbar.push_current(); page.axes.set_xlim(2, 5); page.toolbar.push_current()
        history = len(page.toolbar._nav_stack)
        with patch('spectra_app.xy_plot_controller.prepare_comparison', side_effect=AssertionError('prepare')):
            page._apply_batch_style({self.curves[0].curve_id}, 'color', '#abcdef')
        self.assertIs(page.axes, axes); self.assertIs(page.axes.lines[0], line)
        self.assertIs(line.get_xdata(), data)
        self.assertEqual(page.axes.get_xlim(), (2, 5))
        self.assertEqual(len(page.toolbar._nav_stack), history)

    def test_wheel_and_context_do_not_zoom_or_change_scientific_model(self):
        page = self.page
        qc.focus_canvas(page)
        before = page.axes.get_xlim(), page.axes.get_ylim(), workspace_digest(self.workspace)
        host = qc.canvas_receiver(page)
        canvas = qc.canvas_host(page)
        point = canvas.mapToScene(QPointF(canvas.width()/2, canvas.height()/2))
        wheel = QWheelEvent(point, QPointF(host.mapToGlobal(point.toPoint())), QPoint(),
            QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QCoreApplication.sendEvent(host, wheel)
        context = QContextMenuEvent(QContextMenuEvent.Mouse, point.toPoint(),
                                    host.mapToGlobal(point.toPoint()))
        QCoreApplication.sendEvent(host, context)
        qc.pump(40)
        self.assertEqual((page.axes.get_xlim(), page.axes.get_ylim(), workspace_digest(self.workspace)), before)
        self.assertFalse(any(window.isVisible() and (window.flags() & Qt.WindowType_Mask) == Qt.Popup
                             for window in QGuiApplication.allWindows()))


class XYCanvasOwnerQuickIntegrationTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        self.w = self.controller
        source = self.folder/'quick-owner.csv'
        source.write_text('X,A,B\n0,1,5\n1,2,4\n2,3,3\n3,4,2\n', encoding='utf-8')
        book = self.w.import_xy_files([source])
        self.sheet_id = book.sheet_ids[0]
        self.page = self.w._add_sheet_comparison(book.sheet_ids[0])
        self.host.window.resize(1400, 950)
        qc.pump(140)

    def test_focused_escape_delete_undo_redo_rescale_and_rejected_shortcuts(self):
        page = self.page
        select_xy_rows(page, [0, 1])
        qc.key(page, Qt.Key_Escape)
        self.assertEqual(page._selected_ids(), set())
        select_xy_rows(page, [0, 1])
        active = page._active_curve_id
        selected_before = page._selected_ids()
        curve = page.comparison.curves[active]
        qc.key(page, Qt.Key_Delete)
        self.assertNotIn(active, page.comparison.curves)
        self.assertEqual(len(page.comparison.curves), 1)
        qc.key(page, Qt.Key_Z, Qt.ControlModifier)
        self.assertIs(page.comparison.curves[active], curve)
        self.assertEqual(page._selected_ids(), selected_before)
        qc.key(page, Qt.Key_Y, Qt.ControlModifier)
        self.assertNotIn(active, page.comparison.curves)
        qc.key(page, Qt.Key_Z, Qt.ControlModifier)
        qc.key(page, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
        self.assertNotIn(active, page.comparison.curves)
        qc.key(page, Qt.Key_Z, Qt.ControlModifier)
        page.toolbar.push_current(); page.axes.set_xlim(.5, 1.5); page.toolbar.push_current()
        qc.key(page, Qt.Key_R, Qt.ControlModifier)
        self.assertLess(page.axes.get_xlim()[0], 0)
        self.assertEqual(len(page.toolbar._nav_stack), 0)
        page.axes.set_xlim(.5, 1.5)
        qc.focus_canvas(page)
        page.set_input_guard(lambda: False)
        counts = len(page.comparison.curves), len(page._edit_history.undo_stack), len(page._edit_history.redo_stack)
        for code in (Qt.Key_R, Qt.Key_Z, Qt.Key_Y):
            QTest.keyClick(qc.canvas_receiver(page), code, Qt.ControlModifier); qc.pump(30)
            self.assertEqual(page.axes.get_xlim(), (.5, 1.5), 'Rejected owner guard must cover native QAction shortcuts')
            self.assertEqual((len(page.comparison.curves), len(page._edit_history.undo_stack),
                              len(page._edit_history.redo_stack)), counts)
        page.set_input_guard(None)
        qc.item(qc.canvas_host(page), 'XYCanvasRoot').setFocus(False); qc.focus_canvas(page)
        old_owner = page.canvas_bridge.ownerToken
        sheet = self.w.workspace.sheets[self.sheet_id]
        sheet.data = sheet.data.copy()
        page.refresh_input_state(); qc.pump(30)
        self.assertNotEqual(page.canvas_bridge.ownerToken, old_owner)
        QTest.keyClick(qc.canvas_receiver(page), Qt.Key_R, Qt.ControlModifier); qc.pump(40)
        self.assertEqual(page.axes.get_xlim(), (.5, 1.5), 'A stale focus token must not reach the QAction')
        qc.item(qc.canvas_host(page), 'XYCanvasRoot').setFocus(False); qc.focus_canvas(page)
        QTest.keyClick(qc.canvas_receiver(page), Qt.Key_R, Qt.ControlModifier); qc.pump(40)
        self.assertLess(page.axes.get_xlim()[0], 0, 'Fresh focus must permit the canonical rescale action')
        button = qc.item(self.controller, 'XYNavigation_home')
        button.forceActiveFocus()
        page.axes.set_xlim(.5, 1.5)
        page.set_input_guard(lambda: False)
        QTest.keyClick(self.host.window, Qt.Key_R, Qt.ControlModifier); qc.pump(30)
        self.assertEqual(page.axes.get_xlim(), (.5, 1.5), 'Toolbar focus must retain the owner guard')
        page.set_input_guard(None)
        qc.item(qc.canvas_host(page), 'XYCanvasRoot').forceActiveFocus()
        button.forceActiveFocus()
        QTest.keyClick(self.host.window, Qt.Key_R, Qt.ControlModifier); qc.pump(30)
        self.assertLess(page.axes.get_xlim()[0], 0, 'Fresh toolbar focus must permit rescale')

    def test_held_gesture_and_popup_owner_roundtrip_and_hide_are_retired(self):
        page = self.page
        select_xy_rows(page, [0])
        bar = qc.item(self.controller, 'XYCommandsRoot')
        original_width = bar.width()
        bar.setWidth(225); qc.pump(50)
        qc.click_named(self.controller, 'XYCommandsOverflow')
        entry = qc.item(self.controller, 'XYOverflow_style_line_symbol')
        self.assertTrue(entry.isVisible())
        popup = formal.input_receiver(self.controller, entry)
        point = entry.mapToScene(QPointF(entry.width()/2, entry.height()/2)).toPoint()
        global_point = popup.mapToGlobal(point)
        style_before = [curve.style for curve in page.comparison.curves.values()]
        old_owner = page.command_bridge.ownerToken
        self.w.open_workbook(next(iter(self.w.workspace.workbooks)))
        self.w.open_xy_comparison(page.comparison_id); qc.pump(60)
        self.assertNotEqual(page.command_bridge.ownerToken, old_owner)
        self.assertTrue(not shiboken6.isValid(entry) or not entry.isVisible(),
                        'A popup opened for the previous activity must close or be destroyed')
        if shiboken6.isValid(popup):
            self.assertFalse(popup.isVisible())
            QTest.mouseClick(popup, Qt.LeftButton, pos=point)
        else:
            QTest.mouseClick(self.host.window, Qt.LeftButton,
                             pos=self.host.window.mapFromGlobal(global_point))
        qc.pump(30)
        self.assertEqual([curve.style for curve in page.comparison.curves.values()], style_before)
        bar = qc.item(self.controller, 'XYCommandsRoot')
        bar.setWidth(original_width)
        qc.navigation(page, 'zoom')
        start, end = qc.data_point(page, (.5, 2)), qc.data_point(page, (2, 3))
        original = page.axes.get_xlim()
        qc.press(page, start); qc.move(page, end); qc.pump(20)
        self.assertTrue(page.canvas_bridge.rubberband['visible'])
        old = page.canvas_bridge.ownerToken
        self.w.open_workbook(next(iter(self.w.workspace.workbooks)))
        self.w.open_xy_comparison(page.comparison_id); qc.pump(80)
        self.assertNotEqual(page.canvas_bridge.ownerToken, old)
        self.assertFalse(page.canvas_bridge.rubberband['visible'])
        self.assertIsNone(page.toolbar._zoom_info)
        qc.release(page, end)
        self.assertEqual(page.axes.get_xlim(), original)
        qc.navigation(page, 'zoom')
        qc.press(page, start); qc.move(page, end); qc.pump(20)
        scene = qc.canvas_host(page)
        scene.setVisible(False); qc.pump(20); scene.setVisible(True); qc.pump(40)
        qc.release(page, end)
        self.assertIsNone(page.toolbar._zoom_info)
        self.assertEqual(page.axes.get_xlim(), original)
        qc.navigation(page, 'zoom')
        qc.press(page, start); qc.move(page, end); qc.pump(20)
        old = page.canvas_bridge.ownerToken
        sheet = self.w.workspace.sheets[self.sheet_id]
        sheet.data = sheet.data.copy()
        page.refresh_input_state(); qc.pump(30)
        self.assertNotEqual(page.canvas_bridge.ownerToken, old)
        self.assertIsNone(page.toolbar._zoom_info)
        qc.release(page, end)
        self.assertEqual(page.axes.get_xlim(), original)

    def test_native_preactivation_routes_inactive_visible_canvas_to_its_owner(self):
        first = self.page
        second = self.w._add_sheet_comparison(self.sheet_id)
        self.assertIsNot(second, first)
        documents = self.w.graph_documents
        documents.setMode('free')
        documents.tile(); qc.pump(60)
        self.w.open_xy_comparison(first.comparison_id); qc.pump(60)
        self.assertEqual(documents.registry.active_key, document_key('xy', first.comparison_id))
        self.assertTrue(qc.canvas_host(second).isVisible())
        select_xy_rows(second, [])
        qc.click_data(second, (1, 2))
        self.assertEqual(documents.registry.active_key, document_key('xy', second.comparison_id))
        expected = next(curve.curve_id for curve in second.comparison.curves.values() if curve.y_column == 1)
        self.assertEqual(second._selected_ids(), {expected})
        self.assertTrue(second.canvas_bridge.enabled)


if __name__ == '__main__':
    unittest.main()

