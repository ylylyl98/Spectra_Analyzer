"""Peak-menu provenance, release ordering and consume-once boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from spectra_app.analysis_canvas import (
    AnalysisCanvasBridge, AnalysisFigureCanvas, CanvasPointerContext, PeakMenuRequest,
)


APP = QApplication.instance() or QApplication([])
RIGHT = Qt.RightButton.value


class PeakMenuBridgeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.window = SimpleNamespace(
            _cancel_canvas_gesture=lambda: None,
            _peak_menu_request_valid=lambda request: self.bridge.allowed(request.pointer.owner),
            _continue_peak_menu=lambda request, key: self.calls.append((request, key)),
        )
        self.canvas = AnalysisFigureCanvas(Figure(figsize=(4, 3), dpi=100))
        self.bridge = AnalysisCanvasBridge(self.canvas, self.window)
        self.bridge.setViewport(400, 300)
        self.bridge.set_visible(True)
        self.addCleanup(self.bridge.dispose)
        self.events = []
        self.contexts = []
        self.canvas.mpl_connect('button_press_event', self.prepare)
        self.canvas.mpl_connect('button_release_event', self.events.append)

    def prepare(self, event):
        self.events.append(event)
        self.contexts.append(event.guiEvent)
        if not isinstance(event.guiEvent, CanvasPointerContext):
            return
        self.request = PeakMenuRequest(self.bridge.next_peak_menu_id(), event.guiEvent,
            self.window, self.window, self.canvas, None, 0, 2, 5., 1, None, 0,
            (), (2,))
        self.bridge.request_peak_menu(self.request)

    def pointer(self, kind, buttons=0, *, anchors=True):
        args = (kind, 120., 80., RIGHT, buttons, 0, False, self.bridge.ownerToken)
        return self.bridge.pointerEvent(*args, *((25., 35.) if anchors else ()))

    def test_immutable_per_event_logical_anchor_and_physical_conversion(self):
        self.bridge.set_device_pixel_ratio(2.)
        self.pointer('press', RIGHT)
        event = self.events[-1]
        self.assertEqual((event.x, event.y), (240, 440))
        self.assertEqual(self.contexts[-1], CanvasPointerContext(self.bridge.ownerToken, 25., 35.))
        with self.assertRaises(FrozenInstanceError):
            self.contexts[-1].anchor_x = 99
        with self.assertRaises(FrozenInstanceError):
            self.request.label = 7
        self.assertEqual(self.bridge.peakMenu, {})
        self.assertFalse(self.bridge.choosePeakMenu(self.request.request_id, 'delete', self.request.pointer.owner))
        released = []
        self.bridge.peakMenuChanged.connect(lambda: released.append(
            (self.events[-1].name, self.canvas._button, self.bridge._gesture_owner)))
        self.pointer('release')
        self.assertEqual(released[-1], ('button_release_event', None, None))
        self.assertEqual(self.bridge.peakMenu['anchorX'], 25.)
        self.assertEqual(self.bridge.peakMenu['anchorY'], 35.)

    def test_whitelist_consume_once_and_retirement(self):
        self.pointer('press', RIGHT)
        self.pointer('release')
        request = self.request
        owner = request.pointer.owner
        self.assertFalse(self.bridge.choosePeakMenu(request.request_id, 'arbitrary', owner))
        self.assertFalse(self.bridge.choosePeakMenu(request.request_id, 'delete', owner + ':stale'))
        self.assertTrue(self.bridge.choosePeakMenu(request.request_id, 'set_label', owner))
        self.assertEqual(self.calls, [(request, 'set_label')])
        self.assertEqual(self.bridge.peakMenu, {})
        self.assertFalse(self.bridge.choosePeakMenu(request.request_id, 'delete', owner))
        self.pointer('press', RIGHT)
        replacement = self.request
        self.assertNotEqual(replacement.request_id, request.request_id)
        self.bridge.cancelGesture()
        self.assertFalse(self.bridge.choosePeakMenu(replacement.request_id, 'delete', replacement.pointer.owner))
        self.assertFalse(self.bridge.restorePeakMenuFocus(owner))

    def test_eight_argument_pointer_preserves_science_without_popup_provenance(self):
        self.pointer('press', RIGHT, anchors=False)
        self.assertIsNone(self.contexts[-1])
        self.pointer('release', anchors=False)
        self.assertEqual(self.bridge.peakMenu, {})

    def test_chorded_right_release_cannot_publish_a_later_stale_request(self):
        self.pointer('press', RIGHT)
        request = self.request
        # A right release while another button remains held cannot open a menu.
        self.pointer('release', Qt.LeftButton.value)
        self.assertIsNone(self.bridge._peak_menu_request)
        self.bridge.pointerEvent('release', 120., 80., Qt.LeftButton.value, 0, 0,
                                 False, self.bridge.ownerToken, 25., 35.)
        self.assertEqual(self.bridge.peakMenu, {})
        self.assertFalse(self.bridge.choosePeakMenu(request.request_id, 'delete', request.pointer.owner))

    def test_rejected_live_choice_restores_focus_without_executing(self):
        self.pointer('press', RIGHT)
        self.pointer('release')
        request = self.request
        focus = []
        self.bridge.peakMenuFocusRequested.connect(focus.append)
        self.window._peak_menu_request_valid = lambda request: False
        self.assertFalse(self.bridge.choosePeakMenu(request.request_id, 'delete', request.pointer.owner))
        self.assertEqual(self.calls, [])
        self.assertEqual(focus, [request.pointer.owner])


if __name__ == '__main__':
    unittest.main()
