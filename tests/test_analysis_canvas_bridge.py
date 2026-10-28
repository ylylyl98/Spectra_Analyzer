"""Cheap Agg/publication and actual Matplotlib callback-boundary proofs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
import sys
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock

from matplotlib.backend_bases import MouseButton, MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from spectra_app.analysis_canvas import AnalysisCanvasBridge, AnalysisFigureCanvas
from spectra_app.analysis_navigation import AnalysisNavigationController


APP = QApplication.instance() or QApplication([])
LEFT = Qt.LeftButton.value
SHIFT = Qt.ShiftModifier.value


class NavigationOwner(QObject):
    stateChanged = Signal()
    messageChanged = Signal()

    def __init__(self, canvas):
        super().__init__()
        self.ownerToken = 'source:a|activity:1'
        self.active = True
        self.toolbar = AnalysisNavigationController(canvas, None)
        self.toolbar.bridge = self

    def allowed(self, owner=None):
        return self.active and (owner is None or owner == self.ownerToken)


class AnalysisCanvasBridgeTests(unittest.TestCase):
    def setUp(self):
        self.callback_errors = []
        trap = patch.object(sys, 'excepthook', lambda *args: self.callback_errors.append(args))
        trap.start()
        self.addCleanup(trap.stop)
        self.addCleanup(lambda: self.assertEqual(self.callback_errors, []))
        self.figure = Figure(figsize=(4, 3), dpi=100)
        self.canvas = AnalysisFigureCanvas(self.figure)
        self.bridge = AnalysisCanvasBridge(self.canvas)
        self.bridge.setViewport(400, 300)
        self.ax = self.figure.add_axes([.1, .1, .8, .8])
        self.ax.set_xlim(1, 100)
        self.ax.set_ylim(-5, 5)
        self.bridge.set_visible(True)
        self.addCleanup(self.bridge.dispose)

    def pointer(self, kind, x=200, y=150, *, button=LEFT, buttons=0, owner=None,
                modifiers=0, double=False):
        return self.bridge.pointerEvent(kind, x, y, button, buttons, modifiers, double,
                                        self.bridge.ownerToken if owner is None else owner)

    def test_genuine_agg_not_widget_and_viewport_separate_from_grown_content(self):
        self.assertIsInstance(self.canvas, FigureCanvasAgg)
        self.assertNotIsInstance(self.canvas, QWidget)
        self.bridge.set_content_minimum(750, 600)
        self.assertEqual((self.bridge.contentWidth, self.bridge.contentHeight), (750, 600))
        self.assertEqual((self.bridge.viewportWidth, self.bridge.viewportHeight), (400, 300))
        self.bridge.set_content_minimum(100, 100)
        self.assertEqual((self.bridge.contentWidth, self.bridge.contentHeight), (400, 300))

    def test_idle_burst_schedules_one_render(self):
        count = []
        self.canvas.mpl_connect('draw_event', lambda event: count.append(event))
        for _ in range(30):
            self.canvas.draw_idle()
        QTest.qWait(60)
        self.assertEqual(len(count), 1)
        self.assertEqual(self.bridge.provider.revision, 1)

    def test_draw_request_during_draw_event_not_lost(self):
        count = []
        def requested(event):
            count.append(event)
            if len(count) == 1:
                self.canvas.draw_idle()
        self.canvas.mpl_connect('draw_event', requested)
        self.canvas.draw_idle()
        QTest.qWait(100)
        self.assertEqual(len(count), 2)
        self.assertFalse(self.canvas._dirty)

    def test_hidden_dirty_renders_once_on_reveal(self):
        self.bridge.set_visible(False)
        for _ in range(10):
            self.canvas.draw_idle()
        QTest.qWait(40)
        self.assertTrue(self.canvas._dirty)
        self.assertEqual(self.bridge.provider.revision, 0)
        self.assertFalse(self.canvas._render_timer.isActive())
        self.bridge.set_visible(True)
        QTest.qWait(60)
        self.assertEqual(self.bridge.provider.revision, 1)

    def test_frame_detached_from_reused_agg_buffer_and_bounded(self):
        self.figure.set_facecolor('red')
        self.canvas.draw()
        first = self.bridge.provider.requestImage('1', None, None)
        before = first.pixelColor(0, 0)
        self.figure.set_facecolor('blue')
        for _ in range(4):
            self.canvas.draw()
        self.assertEqual(first.pixelColor(0, 0), before)
        self.assertNotEqual(first.pixelColor(0, 0),
                            self.bridge.provider.requestImage('5', None, None).pixelColor(0, 0))
        self.assertEqual(list(self.bridge.provider.images), ['3', '4', '5'])

    def test_dpr_applied_once_to_raster_and_actual_log_inverted_axes_events(self):
        received = []
        self.canvas.mpl_connect('button_press_event', received.append)
        self.ax.set_xscale('log')
        self.ax.invert_yaxis()
        for dpr in (1, 2, 1):
            with self.subTest(dpr=dpr):
                self.bridge.set_device_pixel_ratio(dpr)
                self.canvas.draw()
                self.assertEqual(self.canvas.get_width_height(physical=True), (400*dpr, 300*dpr))
                self.assertEqual(self.canvas.get_width_height(), (400, 300))
                image = self.bridge.provider.requestImage(str(self.bridge.provider.revision), None, None)
                self.assertEqual((image.width(), image.height(), image.devicePixelRatio()),
                                 (400*dpr, 300*dpr, dpr))
                px, py = self.ax.transData.transform((10, 2.5))
                x, y = px/dpr, (300*dpr-py)/dpr
                self.assertTrue(self.pointer('press', x, y, buttons=LEFT, double=True, modifiers=SHIFT))
                event = received[-1]
                self.assertIsInstance(event, MouseEvent)
                self.assertIs(event.inaxes, self.ax)
                self.assertAlmostEqual(event.xdata, 10)
                self.assertAlmostEqual(event.ydata, 2.5)
                self.assertTrue(event.dblclick)
                self.assertIn('shift', event.modifiers)
                self.pointer('release', x, y)

    def test_real_callbacks_held_buttons_and_release_outside(self):
        received = []
        for name in ('button_press_event', 'motion_notify_event', 'button_release_event'):
            self.canvas.mpl_connect(name, received.append)
        self.assertTrue(self.pointer('press', buttons=LEFT))
        self.assertTrue(self.pointer('move', 220, 130, button=0, buttons=LEFT))
        self.assertEqual(received[-1].buttons, frozenset({MouseButton.LEFT}))
        self.assertTrue(self.pointer('release', -10, 330))
        self.assertEqual([event.name for event in received],
                         ['button_press_event', 'motion_notify_event', 'button_release_event'])
        self.assertIsNone(received[-1].inaxes)
        self.assertEqual(received[-1].button, MouseButton.LEFT)
        self.assertIsNone(self.bridge._gesture_owner)

    def test_owner_transition_retires_pan_and_rejects_stale_release_roundtrip(self):
        navigation = NavigationOwner(self.canvas)
        self.bridge.bind_navigation(navigation)
        navigation.toolbar.pan()
        owner = self.bridge.ownerToken
        self.pointer('press', buttons=LEFT, owner=owner)
        self.assertIsNotNone(navigation.toolbar._pan_info)
        navigation.ownerToken = 'source:b|activity:2'
        navigation.stateChanged.emit()
        self.assertIsNone(navigation.toolbar._pan_info)
        navigation.ownerToken = 'source:a|activity:1'
        navigation.stateChanged.emit()
        self.assertNotEqual(self.bridge.ownerToken, owner)
        self.assertFalse(self.pointer('release', owner=owner))

    def test_stable_owner_refresh_preserves_held_gesture_and_stale_press_cannot_cancel(self):
        navigation = NavigationOwner(self.canvas)
        self.bridge.bind_navigation(navigation)
        owner = self.bridge.ownerToken
        self.pointer('press', buttons=LEFT)
        navigation.stateChanged.emit()
        self.assertEqual(self.bridge.ownerToken, owner)
        self.assertEqual(self.bridge._gesture_owner, owner)
        self.assertFalse(self.pointer('press', buttons=LEFT, owner='stale'))
        self.assertEqual(self.bridge._gesture_owner, owner)
        self.assertTrue(self.pointer('move', buttons=LEFT, button=0, owner=owner))
        self.assertTrue(self.pointer('release', owner=owner))

    def test_live_source_change_rejects_before_refresh_and_cancels(self):
        navigation = NavigationOwner(self.canvas)
        self.bridge.bind_navigation(navigation)
        self.pointer('press', buttons=LEFT)
        owner = self.bridge.ownerToken
        navigation.ownerToken = 'source:b|activity:2'
        self.assertFalse(self.pointer('release', owner=owner))
        self.assertIsNone(self.bridge._gesture_owner)

    def test_hide_cancel_and_dispose_retire_tokens_and_stop_publication(self):
        owner = self.bridge.ownerToken
        self.pointer('press', buttons=LEFT)
        self.bridge.set_visible(False)
        self.bridge.set_visible(True)
        self.assertNotEqual(self.bridge.ownerToken, owner)
        self.assertFalse(self.pointer('release', owner=owner))
        self.bridge.cancelGesture()
        self.assertIsNone(self.bridge._gesture_owner)
        self.bridge.dispose()
        revision = self.bridge.provider.revision
        self.canvas.draw_idle()
        self.canvas.draw()
        QTest.qWait(30)
        self.assertEqual(self.bridge.provider.revision, revision)
        self.assertEqual(self.bridge.imageUrl, '')
        self.assertEqual(self.bridge.provider.images, {})

    def test_rubberband_normalizes_physical_coordinates_at_dpr2(self):
        self.bridge.set_device_pixel_ratio(2)
        navigation = NavigationOwner(self.canvas)
        self.bridge.bind_navigation(navigation)
        navigation.toolbar.draw_rubberband(None, 200, 500, 100, 200)
        self.assertEqual(self.bridge.rubberband,
                         dict(visible=True, x=50., y=50., width=50., height=150.))
        navigation.toolbar.remove_rubberband()
        self.assertFalse(self.bridge.rubberband['visible'])

    def test_cancel_removes_transient_marker_and_legend_without_scientific_mutation(self):
        from spectra_app.main_window import ApplicationController
        self.ax.plot([1, 100], [0, 1], label='Spectrum')
        marker = self.ax.scatter([50], [1], label='Dragging')
        self.ax.legend()
        fit = object()
        target = SimpleNamespace(canvas=self.canvas, _motion_cid=None, _drag_scatter=marker,
                                 _drag_peak_arr_idx=0, _drag_current_x=50,
                                 _batch_delete_circle_start=(10, 2), _lasso_selector=None,
                                 fit_result=fit)
        ApplicationController._cancel_canvas_gesture(target)
        self.assertIsNone(marker.axes)
        self.assertEqual([text.get_text() for text in self.ax.get_legend().get_texts()], ['Spectrum'])
        self.assertIs(target.fit_result, fit)
        self.assertIsNone(target._drag_peak_arr_idx)
        self.assertIsNone(target._batch_delete_circle_start)
        self.assertTrue(self.canvas._dirty)

    def test_cancelled_navigation_finishes_captured_history_once(self):
        navigation = NavigationOwner(self.canvas)
        self.bridge.bind_navigation(navigation)
        window = SimpleNamespace(_finish_history_edit=Mock())
        navigation.toolbar.window = window
        navigation.toolbar.pan()
        self.pointer('press', buttons=LEFT)
        self.assertIsNotNone(navigation.toolbar._pan_info)
        self.bridge.cancelGesture()
        window._finish_history_edit.assert_called_once_with()
        self.bridge.cancelGesture()
        window._finish_history_edit.assert_called_once_with()

    def test_key_enter_leave_scroll_are_actual_registry_events(self):
        received = []
        for name in ('key_press_event', 'key_release_event', 'figure_enter_event',
                     'figure_leave_event', 'scroll_event'):
            self.canvas.mpl_connect(name, received.append)
        owner = self.bridge.ownerToken
        self.assertTrue(self.bridge.keyEvent('press', Qt.Key_A.value, 'a', 0, owner))
        self.assertTrue(self.bridge.keyEvent('release', Qt.Key_A.value, 'a', 0, owner))
        self.pointer('enter', button=0)
        self.pointer('leave', button=0)
        limits = self.ax.get_xlim(), self.ax.get_ylim()
        self.assertTrue(self.bridge.scrollEvent(200, 150, -1, SHIFT, owner))
        self.assertEqual(received[0].key, 'a')
        self.assertEqual(received[1].key, 'a')
        self.assertEqual(received[-1].step, -1)
        self.assertEqual(limits, (self.ax.get_xlim(), self.ax.get_ylim()))


if __name__ == '__main__':
    unittest.main()
