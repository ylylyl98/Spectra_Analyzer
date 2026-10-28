"""Qt-free navigation logic and typed, captured-owner command boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from matplotlib.backend_bases import NavigationToolbar2, MouseEvent, MouseButton
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QAction, QWindow
from shiboken6 import isValid

from spectra_app.analysis_navigation import AnalysisNavigationBridge, AnalysisNavigationController
from spectra_app.analysis_canvas import AnalysisCanvasBridge, AnalysisFigureCanvas

APP = QApplication.instance() or QApplication([])


class Owner(QObject):
    def __init__(self):
        super().__init__()
        self.token = 'dataset|graph|generation1'
        self.current_cube = object()
        self._close_pending = self._dat_import_pending = False
        self.document = None
        self._outer_view = 'analysis'
        self.root_window = QWindow()
        self.analysis_header = SimpleNamespace(ownerToken=self.token, allowed=lambda: True)
        self.ordinary_analysis = SimpleNamespace(ownerToken=self.token, allowed=lambda: True)
        self.plot_page = SimpleNamespace(zoom_text='Fit', set_zoom=self.set_zoom)
        self.zoom_changes = 0
        self.history_finishes = 0
        self.actions = {key: QAction(key, self) for key in
                        ('settings_copy', 'settings_paste', 'settings_copy_to', 'comparison')}

    def set_zoom(self, text):
        if text != self.plot_page.zoom_text:
            self.plot_page.zoom_text = text
            self.zoom_changes += 1

    def _finish_history_edit(self):
        self.history_finishes += 1

    def _workbench_document(self):
        return self.document

    def _project_busy(self):
        return False


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.w = Owner()
        self.canvas = AnalysisFigureCanvas(Figure(figsize=(4, 3)))
        self.canvas_view = AnalysisCanvasBridge(self.canvas)
        self.ax = self.canvas.figure.subplots()
        self.ax.plot([0, 1], [0, 1])
        self.canvas.draw()
        self.toolbar = AnalysisNavigationController(self.canvas, self.w)
        self.bridge = AnalysisNavigationBridge(self.w, self.toolbar, self.w.actions)
        self.owner = self.bridge.ownerToken
        self.addCleanup(NavigationTests.dispose_navigation, self)

    def dispose_navigation(self):
        if isValid(self.w):
            self.bridge.close()
            self.w.deleteLater()
        if isValid(self.w.root_window):
            self.w.root_window.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def test_real_controller_history_and_exclusive_modes(self):
        self.assertIsInstance(self.toolbar, NavigationToolbar2)
        self.toolbar.push_current()
        original = self.ax.get_xlim()
        self.ax.set_xlim(.2, .8)
        self.toolbar.push_current()
        self.assertTrue(self.bridge.backEnabled)
        self.assertTrue(self.bridge.invoke('back', self.owner))
        self.assertEqual(self.ax.get_xlim(), original)
        self.assertTrue(self.bridge.forwardEnabled)
        self.bridge.invoke('forward', self.owner)
        self.assertEqual(self.ax.get_xlim(), (.2, .8))
        self.bridge.invoke('pan', self.owner)
        self.assertEqual(self.bridge.mode, 'pan/zoom')
        self.bridge.invoke('zoom', self.owner)
        self.assertEqual(self.bridge.mode, 'zoom rect')
        self.bridge.invoke('zoom', self.owner)
        self.assertEqual(self.bridge.mode, '')
        self.bridge.invoke('home', self.owner)
        self.assertEqual(self.ax.get_xlim(), original)

    def test_zoom_action_authority_stale_and_disabled_dispatch(self):
        self.assertTrue(self.bridge.setZoom('Fit', self.owner))
        self.assertEqual(self.w.zoom_changes, 0)
        self.assertTrue(self.bridge.setZoom('150%', self.owner))
        self.assertEqual(self.w.zoom_changes, 1)
        self.assertEqual(self.bridge.zoomText, '150%')
        self.assertFalse(self.bridge.setZoom('invalid', self.owner))
        called = Mock()
        action = self.w.actions['settings_copy']
        action.triggered.connect(called)
        self.assertTrue(self.bridge.invoke('settings_copy', self.owner))
        called.assert_called_once()
        action.setEnabled(False)
        self.assertFalse(self.bridge.invoke('settings_copy', self.owner))
        self.assertFalse(next(row for row in self.bridge.actions if row['key'] == 'settings_copy')['enabled'])
        self.w.ordinary_analysis.ownerToken = 'dataset|graph|generation2'
        self.assertFalse(self.bridge.invoke('home', self.owner))
        self.assertFalse(self.bridge.setZoom('200%', self.owner))
        self.w._outer_view = 'workspace'
        self.assertFalse(self.bridge.enabled)
        self.assertFalse(self.bridge.invoke('comparison', self.bridge.ownerToken))

    def test_message_and_physical_rubberband_adapter(self):
        self.toolbar.set_message('x=1 y=2')
        self.assertEqual(self.bridge.message, 'x=1 y=2')
        for ratio in (1., 2.):
            self.canvas_view.set_device_pixel_ratio(ratio)
            self.toolbar.draw_rubberband(None, 10, 20, 40, 70)
            height = self.canvas.get_width_height(physical=True)[1]
            self.assertEqual(self.canvas_view.rubberband, dict(
                visible=True, x=10/ratio, y=(height-70)/ratio,
                width=30/ratio, height=50/ratio))
            self.toolbar.remove_rubberband()
            self.assertFalse(self.canvas_view.rubberband['visible'])

    def test_real_canvas_events_zoom_pan_and_pointer_message(self):
        self.bridge.invoke('zoom', self.owner)
        x0, y0 = self.ax.transData.transform((.2, .2))
        x1, y1 = self.ax.transData.transform((.8, .8))
        for name, x, y in [('button_press_event', x0, y0),
                           ('button_release_event', x1, y1)]:
            self.canvas.callbacks.process(name, MouseEvent(name, self.canvas, x, y, button=1))
        zoomed = self.ax.get_xlim()
        self.assertAlmostEqual(zoomed[0], .2, places=2)
        self.assertAlmostEqual(zoomed[1], .8, places=2)
        self.bridge.invoke('pan', self.owner)
        x, y = self.ax.transData.transform((.5, .5))
        press = MouseEvent('button_press_event', self.canvas, x, y, button=1)
        self.canvas.callbacks.process(press.name, press)
        motion = MouseEvent('motion_notify_event', self.canvas, x + 25, y + 10,
                            buttons={MouseButton.LEFT})
        self.canvas.callbacks.process(motion.name, motion)
        release = MouseEvent('button_release_event', self.canvas, x + 25, y + 10, button=1)
        self.canvas.callbacks.process(release.name, release)
        self.assertNotEqual(self.ax.get_xlim(), zoomed)
        self.assertTrue(self.bridge.backEnabled)
        self.bridge.invoke('back', self.owner)
        self.assertEqual(self.ax.get_xlim(), zoomed)
        self.bridge.invoke('pan', self.owner)
        motion = MouseEvent('motion_notify_event', self.canvas, x, y)
        self.canvas.callbacks.process(motion.name, motion)
        self.assertEqual(self.bridge.message, self.ax.format_coord(motion.xdata, motion.ydata))

    def test_zoom_gesture_rejects_owner_switch_without_push(self):
        self.bridge.invoke('zoom', self.owner)
        x, y = self.ax.transData.transform((.2, .2))
        press = MouseEvent('button_press_event', self.canvas, x, y, button=1)
        self.toolbar.press_zoom(press)
        self.assertIsNotNone(self.toolbar._zoom_info)
        count = len(self.toolbar._nav_stack)
        limits = self.ax.get_xlim()
        self.w.ordinary_analysis.ownerToken = 'other'
        x, y = self.ax.transData.transform((.7, .7))
        release = MouseEvent('button_release_event', self.canvas, x, y, button=1)
        self.toolbar.release_zoom(release)
        self.assertEqual(self.ax.get_xlim(), limits)
        self.assertEqual(len(self.toolbar._nav_stack), count)
        self.assertIsNone(self.toolbar._zoom_info)

    def test_busy_analysis_keeps_view_tools_and_action_enablement_authoritative(self):
        self.w._project_busy = lambda: True
        self.w.actions['settings_copy'].setEnabled(False)
        self.assertTrue(self.bridge.enabled)
        self.assertTrue(self.bridge.invoke('pan', self.owner))
        self.assertTrue(self.bridge.invoke('zoom', self.owner))
        self.assertTrue(self.bridge.setZoom('200%', self.owner))
        self.assertFalse(self.bridge.invoke('settings_copy', self.owner))

    def assert_retired_owner_rejected(self, owner):
        called = Mock()
        self.w.actions['settings_copy'].triggered.connect(called)
        zoom = self.bridge.zoomText
        with patch.object(self.toolbar, 'pan') as pan:
            self.assertFalse(self.bridge.invoke('pan', owner))
            self.assertFalse(self.bridge.invoke('settings_copy', owner))
            self.assertFalse(self.bridge.setZoom('200%', owner))
            pan.assert_not_called()
        called.assert_not_called()
        self.assertEqual(self.bridge.zoomText, zoom)

    def test_inactive_roundtrip_retires_navigation_zoom_and_settings_owner(self):
        for inactive in ('workbench', 'workspace'):
            with self.subTest(inactive=inactive):
                retired = self.bridge.ownerToken
                if inactive == 'workbench':
                    self.w.document = object()
                else:
                    self.w._outer_view = 'workspace'
                self.bridge.refresh()
                self.assertFalse(self.bridge.enabled)
                self.w.document, self.w._outer_view = None, 'analysis'
                self.bridge.refresh()
                self.assertTrue(self.bridge.enabled)
                self.assertNotEqual(self.bridge.ownerToken, retired)
                self.assert_retired_owner_rejected(retired)
                self.assertTrue(self.bridge.invoke('settings_copy', self.bridge.ownerToken))

    def test_owner_roundtrip_retires_navigation_zoom_and_settings_owner(self):
        original = self.w.ordinary_analysis.ownerToken
        self.w.ordinary_analysis.ownerToken = 'other'
        self.assert_retired_owner_rejected(self.owner)
        self.bridge.refresh()
        other = self.bridge.ownerToken
        self.w.ordinary_analysis.ownerToken = original
        self.bridge.refresh()
        self.assertNotEqual(self.bridge.ownerToken, self.owner)
        self.assert_retired_owner_rejected(self.owner)
        self.assert_retired_owner_rejected(other)

    def test_stable_refresh_preserves_owner_mode_and_live_gesture(self):
        self.bridge.invoke('zoom', self.owner)
        x, y = self.ax.transData.transform((.2, .2))
        self.toolbar.press_zoom(MouseEvent('button_press_event', self.canvas, x, y, button=1))
        gesture = self.toolbar._zoom_info
        self.assertIsNotNone(gesture)
        for _ in range(3):
            self.bridge.refresh()
        self.assertEqual(self.bridge.ownerToken, self.owner)
        self.assertEqual(self.bridge.mode, 'zoom rect')
        self.assertIs(self.toolbar._zoom_info, gesture)
        self.assertTrue(self.toolbar._gesture_allowed())

    def test_customize_no_axes_duplicate_titles_selection_and_cancel(self):
        from spectra_app.quick_dialog import QuickDialog
        from PySide6.QtCore import Qt
        self.addCleanup(self.bridge.close)
        with patch('spectra_app.common_prompts.prompt_item') as select, \
                patch('spectra_app.common_prompts.prompt_message') as warning:
            dialog = self.toolbar.edit_parameters()
            self.assertIsInstance(dialog, QuickDialog)
            self.assertIs(dialog.parent(), self.w.root_window)
            self.assertEqual(dialog.window().modality(), Qt.NonModal)
            self.assertTrue(dialog.window().isVisible())
            self.assertIs(dialog.bridge._axes, self.ax)
            select.assert_not_called()
            retired = dialog.bridge
            second_dialog = self.toolbar.edit_parameters()
            self.assertIsNot(second_dialog, dialog)
            self.assertFalse(retired.active)
            second = self.canvas.figure.add_subplot(212)
            self.ax.set_title('Signal')
            second.set_title('Signal')
            def choose(parent, title, prompt, titles):
                self.assertIs(parent, self.w.root_window)
                self.assertEqual((title, prompt), ('Customize', 'Select Axes:'))
                self.assertEqual(len(set(titles)), 2)
                self.assertTrue(all(text.startswith('Signal (id:') for text in titles))
                return titles[1], True
            select.side_effect = choose
            chosen = self.toolbar.edit_parameters()
            self.assertIs(chosen.bridge._axes, second)
            chosen.bridge.setDraft('axes.title', 'Unapplied title')
            select.side_effect = None
            select.return_value = ('', False)
            self.assertIsNone(self.toolbar.edit_parameters())
            self.assertIs(self.toolbar._figure_dialog, chosen)
            self.assertTrue(chosen.bridge.active)
            self.assertTrue(chosen.window().isVisible())
            self.assertEqual(chosen.bridge.drafts['axes.title'], 'Unapplied title')
            self.assertEqual(second.get_title(), 'Signal')
            self.canvas.figure.clear()
            self.assertIsNone(self.toolbar.edit_parameters())
            warning.assert_called_once_with(self.w.root_window, 'Error', 'There are no Axes to edit.', kind='warning')

    def test_customize_caption_precedence_and_selector_revalidation(self):
        self.addCleanup(self.bridge.close)
        axes = [self.ax] + [self.canvas.figure.add_axes((.1, .1, .2, .2)) for _ in range(5)]
        axes[0].set_label('Axes label')
        axes[0].set_title('Ignored title')
        axes[1].set_title('Centered title')
        axes[2].set_title('Left title', loc='left')
        axes[3].set_title('Right title', loc='right')
        axes[4].set_xlabel('Energy')
        axes[4].set_ylabel('Intensity')
        expected = ['Axes label', 'Centered title', 'Left title', 'Right title',
                    'Energy - Intensity', '<anonymous Axes>']
        with patch('spectra_app.common_prompts.prompt_item') as select:
            def choose(parent, title, prompt, captions):
                self.assertEqual(captions, expected)
                return captions[3], True
            select.side_effect = choose
            chosen = self.toolbar.edit_parameters()
            self.assertIs(chosen.bridge._axes, axes[3])
            def remove_during_selector(parent, title, prompt, captions):
                axes[3].remove()
                return captions[3], True
            select.side_effect = remove_during_selector
            self.assertIsNone(self.toolbar.edit_parameters())
            self.assertIsNone(self.toolbar._figure_dialog)
            def switch_during_selector(parent, title, prompt, captions):
                self.w.ordinary_analysis.ownerToken = 'different-owner'
                return captions[0], True
            select.side_effect = switch_during_selector
            self.assertIsNone(self.toolbar.edit_parameters())

    def test_customize_retirement_on_owner_activity_artist_and_canvas_close(self):
        from matplotlib.backend_bases import CloseEvent
        self.addCleanup(self.bridge.close)
        for reason in ('owner', 'inactive', 'artist', 'close'):
            with self.subTest(reason=reason):
                dialog = self.toolbar.edit_parameters()
                retired = dialog.bridge
                retired.setDraft('axes.title', 'Unapplied')
                if reason == 'owner':
                    original = self.w.ordinary_analysis.ownerToken
                    self.w.ordinary_analysis.ownerToken = 'new-owner'
                    self.bridge.refresh()
                    self.w.ordinary_analysis.ownerToken = original
                    self.bridge.refresh()
                elif reason == 'inactive':
                    self.w._outer_view = 'workspace'
                    self.bridge.refresh()
                    self.w._outer_view = 'analysis'
                    self.bridge.refresh()
                elif reason == 'artist':
                    self.ax.lines[0].remove()
                    self.canvas.draw()
                else:
                    self.canvas.callbacks.process('close_event', CloseEvent('close_event', self.canvas))
                self.assertFalse(dialog.window().isVisible())
                self.assertIsNone(self.toolbar._figure_dialog)
                self.assertFalse(retired.apply())
                self.assertFalse(retired.setDraft('axes.title', 'Stale'))
                self.assertEqual(self.ax.get_title(), '')

    def test_subplot_quick_dialog_reused_and_retired_on_canvas_close(self):
        from matplotlib.backend_bases import CloseEvent
        from PySide6.QtQuick import QQuickView
        from spectra_app.quick_dialog import QuickDialog
        callbacks = self.canvas.callbacks.callbacks
        before = len(callbacks.get('close_event', {}))
        dialog = self.toolbar.configure_subplots()
        from shiboken6 import isValid
        self.addCleanup(lambda: dialog.deleteLater() if isValid(dialog) else None)
        self.addCleanup(self.bridge.close)
        self.assertIsInstance(dialog, QuickDialog)
        self.assertIsInstance(dialog.window(), QQuickView)
        self.assertIs(dialog.parent(), self.w.root_window)
        self.assertIs(self.toolbar.configure_subplots(), dialog)
        self.assertEqual(len(callbacks.get('close_event', {})), before + 1)
        self.assertTrue(dialog.window().isVisible())
        self.canvas.callbacks.process('close_event', CloseEvent('close_event', self.canvas))
        self.assertFalse(dialog.window().isVisible())
        self.assertFalse(dialog.bridge.active)
        self.assertIsNone(self.toolbar._subplot_dialog)
        self.assertEqual(len(callbacks.get('close_event', {})), before)
        before_left = self.canvas.figure.subplotpars.left
        self.assertFalse(dialog.bridge.setDraft('left', '.250'))
        self.assertEqual(self.canvas.figure.subplotpars.left, before_left)

    def test_inactive_and_closed_bridge_reject_commands(self):
        self.w.document = object()
        self.assertFalse(self.bridge.invoke('pan', self.owner))
        self.w.document = None
        self.bridge.close()
        self.assertFalse(self.bridge.enabled)
        self.assertFalse(self.bridge.invoke('home', self.owner))


if __name__ == '__main__':
    unittest.main()
