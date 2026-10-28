"""Cheap scientific/Quick boundary checks for the comparison viewport."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from matplotlib.backend_bases import NavigationToolbar2
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QAction
from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from spectra_app.dataset_state import Workspace
from spectra_app.plot_presentation import PlotPresentation
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_plot_page import XYPlotPage

APP = QApplication.instance() or QApplication([])


class XYCanvasBridgeTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        book = create_workbook(self.workspace)
        self.sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Signals',
            np.column_stack((np.arange(11.), np.arange(11.))), ['X', 'Y'])
        self.graph = create_comparison(self.workspace)
        self.curve = add_curve(self.workspace, self.graph.comparison_id, self.sheet.sheet_id, 0, 1)
        self.page = XYPlotPage(self.workspace, self.graph.comparison_id)
        self.page.resize(900, 600)
        self.page.show()
        APP.processEvents()
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(self.page.dispose)

    def test_physical_letterbox_dpr_and_real_axes_event(self):
        p = self.page
        self.graph.presentation = replace(PlotPresentation(), width_in=8., height_in=4.)
        b = p.canvas_bridge
        b.setViewport(500., 400.)
        b.set_device_pixel_ratio(2.)
        b.fit_page()
        p.canvas.draw()
        self.assertIsInstance(p.canvas, FigureCanvasAgg)
        self.assertNotIsInstance(p.canvas, QWidget)
        self.assertEqual((b.contentWidth, b.contentHeight, b.contentX, b.contentY), (500., 250., 0., 75.))
        np.testing.assert_allclose(p.figure.get_size_inches(), [8., 4.])
        self.assertEqual(p.figure.dpi, 125.)
        self.assertEqual(p.canvas.get_width_height(physical=True), (1000, 500))
        events = []
        cid = p.canvas.mpl_connect('button_press_event', events.append)
        self.addCleanup(p.canvas.mpl_disconnect, cid)
        x, y = p.axes.transData.transform((5., 5.))
        logical_x, logical_y = b.contentX+x/2, b.contentY+(500-y)/2
        token = b.ownerToken
        self.assertTrue(b.pointerEvent('press', logical_x, logical_y,
            Qt.LeftButton.value, Qt.LeftButton.value, Qt.ControlModifier.value, True, token))
        event = events[-1]
        self.assertIs(event.inaxes, p.axes)
        self.assertAlmostEqual(event.xdata, 5.)
        self.assertAlmostEqual(event.ydata, 5.)
        self.assertTrue(event.dblclick)
        self.assertIn('ctrl', event.key)
        self.assertTrue(b.pointerEvent('release', -20., 450., Qt.LeftButton.value, 0, 0, False, token))
        self.assertIsNone(b._gesture_owner)

    def test_without_presentation_fills_viewport_at_dpr_once(self):
        self.graph.presentation = None
        b = self.page.canvas_bridge
        b.setViewport(360., 220.)
        b.set_device_pixel_ratio(2.)
        self.assertEqual((b.contentWidth, b.contentHeight, b.contentX, b.contentY), (360., 220., 0., 0.))
        self.assertEqual(self.page.canvas.get_width_height(physical=True), (720, 440))

    def test_same_owner_refresh_preserves_token_draft_and_view_history(self):
        p = self.page
        token, command_token = p.canvas_bridge.ownerToken, p.command_bridge.ownerToken
        axes, line = p.axes, p.axes.lines[0]
        p.toolbar.push_current()
        p.axes.set_xlim(2., 4.)
        p.toolbar.push_current()
        history = len(p.toolbar._nav_stack)
        p.inspector_bridge._drafts['plot_title'] = 'unfinished title'
        p.controller._selection_changed(sync_analysis=False)
        p.render(appearance_only=True)
        p.refresh_input_state()
        self.assertEqual(p.canvas_bridge.ownerToken, token)
        self.assertEqual(p.command_bridge.ownerToken, command_token)
        self.assertEqual(p.inspector_bridge._drafts['plot_title'], 'unfinished title')
        self.assertIs(p.axes, axes)
        self.assertIs(p.axes.lines[0], line)
        self.assertEqual(len(p.toolbar._nav_stack), history)
        self.assertEqual(p.axes.get_xlim(), (2., 4.))

    def test_source_replacement_and_hide_retire_captured_commands(self):
        p = self.page
        token = p.command_bridge.ownerToken
        action = p.analyze_action
        calls = []
        action.triggered.connect(lambda: calls.append(True))
        action.setEnabled(True)
        self.sheet.data = self.sheet.data.copy()
        p.refresh_input_state()
        self.assertFalse(p.command_bridge.invoke('analyze', token))
        fresh = p.command_bridge.ownerToken
        self.assertTrue(p.command_bridge.invoke('analyze', fresh))
        p.hide()
        p.show()
        APP.processEvents()
        self.assertFalse(p.command_bridge.invoke('analyze', fresh))
        self.assertEqual(calls, [True])

    def test_commands_follow_qaction_and_real_navigation_history(self):
        p = self.page
        self.assertIsInstance(p.toolbar, NavigationToolbar2)
        self.assertNotIsInstance(p.toolbar, QWidget)
        self.assertFalse(p.command_bridge.invoke('analyze', 'stale'))
        p.analyze_action.setEnabled(False)
        self.assertFalse(p.command_bridge.invoke('analyze', p.command_bridge.ownerToken))
        initial = p.axes.get_xlim()
        p.toolbar.push_current()
        p.axes.set_xlim(2., 3.)
        p.toolbar.push_current()
        owner = p.command_bridge.ownerToken
        self.assertTrue(p.command_bridge.backEnabled)
        self.assertTrue(p.command_bridge.invoke('back', owner))
        np.testing.assert_allclose(p.axes.get_xlim(), initial)
        self.assertTrue(p.command_bridge.invoke('forward', owner))
        self.assertEqual(p.axes.get_xlim(), (2., 3.))
        self.assertTrue(p.command_bridge.invoke('pan', owner))
        self.assertTrue(p.canvas.widgetlock.isowner(p.toolbar))
        p.hide()
        self.assertFalse(p.canvas.widgetlock.locked())
        self.assertEqual(str(p.toolbar.mode), '')

    def test_focused_shortcut_calls_canonical_action_only_for_captured_owner(self):
        p = self.page
        action = QAction('Rescale', p)
        calls = []
        action.triggered.connect(lambda: calls.append(True))
        p.bind_canvas_actions({'rescale': action})
        token = p.canvas_bridge.ownerToken
        self.assertTrue(p.canvas_bridge.keyEvent('press', Qt.Key_R.value, 'r', Qt.ControlModifier.value, token))
        p.command_bridge.retire()
        self.assertFalse(p.canvas_bridge.keyEvent('press', Qt.Key_R.value, 'r', Qt.ControlModifier.value, token))
        self.assertEqual(calls, [True])

    def test_explicit_document_guard_activates_only_for_actual_input(self):
        p = self.page
        activation = []
        p.set_input_guard(lambda: activation.append(True) or True)
        owner = p.command_bridge.ownerToken
        self.assertTrue(p.command_bridge.enabled)
        p.refresh_input_state()
        self.assertEqual(activation, [])
        self.assertTrue(p.command_bridge.invoke('home', owner))
        self.assertEqual(activation, [True])
        p.set_input_guard(lambda: False)
        self.assertFalse(p.command_bridge.invoke('home', owner))
        p.hide(); p.show(); APP.processEvents()
        self.assertFalse(p.command_bridge.invoke('home', owner))

    def test_delete_later_and_parent_deletion_stop_renderer_without_callback_errors(self):
        errors = []
        for delete_parent in (False, True):
            p = XYPlotPage(self.workspace, self.graph.comparison_id)
            parent = QWidget() if delete_parent else None
            if parent is not None:
                p.setParent(parent)
                parent.show()
            p.show()
            APP.processEvents()
            canvas, commands = p.canvas, p.command_bridge
            canvas.draw_idle()
            with patch.object(sys, 'excepthook', side_effect=lambda *args: errors.append(args)):
                (parent if parent is not None else p).deleteLater()
                APP.sendPostedEvents(None, QEvent.DeferredDelete)
                QTest.qWait(40)
                self.assertTrue(canvas._disposed)
                self.assertFalse(isValid(canvas._render_timer) and canvas._render_timer.isActive())
                self.assertEqual(commands._identity(), ('deleted',))
                commands.refresh()
            self.assertFalse(errors)

    def test_validation_reveal_stops_after_focus_loss_or_hide(self):
        p = self.page
        p.inspector_bridge.setDraft('raster_dpi', '-')
        with self.assertRaises(ValueError):
            p.finish_editor()
        editor = p.display_panel.rootObject().findChild(QQuickItem, 'xyField_raster_dpi')
        reveal = editor.parentItem()
        self.assertTrue(editor.hasActiveFocus())
        self.assertTrue(reveal.property('revealPending'))
        p.canvas_view.setFocus(Qt.OtherFocusReason)
        p.canvas_view.rootObject().forceActiveFocus()
        APP.processEvents()
        self.assertFalse(reveal.property('revealPending'))
        p.resize(780, 500)
        QTest.qWait(30)
        self.assertTrue(p.canvas_view.hasFocus())
        self.assertFalse(editor.hasActiveFocus())
        with self.assertRaises(ValueError):
            p.finish_editor()
        self.assertTrue(reveal.property('revealPending'))
        p.controller._set_inspector_visible(False)
        APP.processEvents()
        self.assertFalse(reveal.property('revealPending'))


if __name__ == '__main__':
    unittest.main()
