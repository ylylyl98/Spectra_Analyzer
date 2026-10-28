"""Production ordinary Analysis navigation keeps real Matplotlib/Qt behavior."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import time
import unittest
import warnings
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from matplotlib.backend_bases import MouseButton, MouseEvent, NavigationToolbar2
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtGui import QWindow
from shiboken6 import isValid
from PySide6.QtWidgets import QToolBar

from tests.quick_migration_case import QuickMigrationCase
from tests.formal_quick_helpers import item, input_receiver, replace_text
from tests.settings_helpers import wait_for_background_jobs
from tests.scientific_fixtures import workflow_fit_result as _fit_result


APP = QCoreApplication


def wait_until(predicate, timeout=2000):
    deadline = time.monotonic() + timeout / 1000
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        QTest.qWait(10)
    assert predicate(), 'Condition did not become true within the bounded Qt wait'


class AnalysisNavigationIntegrationTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.w = self.controller
        self.addCleanup(self.dispose)
        self.w._show_error = self.fail
        self.w.root_window.resize(1250, 860)
        self.sessions = []
        for index in range(2):
            path = self.folder / f'navigation-{index}.dat'
            energy = np.linspace(1., 5., 31)
            signal = np.column_stack([energy + index + gate for gate in range(3)])
            np.savetxt(path, np.column_stack((energy, signal)),
                       header='Energy(eV) 0 1 2', comments='')
            self.w._load_dat_file_direct(path)
            self.sessions.append(self.w.workspace.active)
        # Generic import opens a Workbook. Explicitly restore the ordinary
        # dataset/canvas context before asserting navigation is enabled.
        self.w.activate_dataset(self.sessions[0].dataset_id)
        self.assertTrue(self.w.request_outer_view('analysis'))
        self.w.root_window.show()
        self.w.root_window.requestActivate()
        self.settle()
        wait_until(lambda: self.w.analysis_navigation.enabled)
        self.assertTrue(self.w.analysis_navigation.enabled)

    def dispose(self):
        wait_for_background_jobs(self.w)
        for comparison in list(self.w._plot_comparison_windows):
            comparison.close()
        self.assertEqual(self.quick_errors, [])

    def settle(self):
        APP.processEvents()
        QTest.qWait(80)
        APP.processEvents()

    def invoke(self, command):
        bridge = self.w.analysis_navigation
        self.assertTrue(bridge.enabled, command)
        names = {'home': 'analysisNavHome', 'back': 'analysisNavBack',
                 'forward': 'analysisNavForward', 'pan': 'analysisNavPan',
                 'zoom': 'analysisNavZoom'}
        if command in names:
            host = item(self.w, 'AnalysisNavigation')
            control = item(host, names[command])
            self.assertTrue(control.isEnabled(), command)
            point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2))
            QTest.mouseClick(self.w.root_window, Qt.LeftButton,
                             pos=QPoint(round(point.x()), round(point.y())))
        else:
            self.assertTrue(bridge.invoke(command, bridge.ownerToken), command)
        APP.processEvents()

    def view(self):
        return self.w._heat_ax.get_xlim(), self.w._heat_ax.get_ylim()

    def assert_view(self, expected):
        np.testing.assert_allclose(self.view(), expected, rtol=0, atol=1e-12)

    def drag(self, start, end):
        canvas, axes = self.w.canvas, self.w._heat_ax
        canvas.draw()
        x0, y0 = axes.transData.transform(start)
        x1, y1 = axes.transData.transform(end)
        for name, x, y in [('button_press_event', x0, y0),
                           ('motion_notify_event', x1, y1),
                           ('button_release_event', x1, y1)]:
            event = MouseEvent(name, canvas, x, y, button=MouseButton.LEFT,
                               buttons={MouseButton.LEFT} if name == 'motion_notify_event' else None)
            canvas.callbacks.process(name, event)
        self.settle()

    def test_real_pan_zoom_history_back_forward_and_home(self):
        w, bridge = self.w, self.w.analysis_navigation
        self.assertIsInstance(w.toolbar, NavigationToolbar2)
        self.assertNotIsInstance(w.toolbar, QToolBar)
        cube = w.current_cube
        science = cube.z.copy()
        gate, peaks = w.current_gate, w.peak_indices.copy()
        fit = _fit_result(2.)
        w.fit_result = fit
        initial = self.view()
        self.invoke('pan')
        self.assertEqual(bridge.mode, 'pan/zoom')
        self.drag((2., 1.4), (2.5, 1.7))
        panned = self.view()
        self.assertNotEqual(panned, initial)
        self.assertEqual(w.current_gate, gate, 'Navigation must not select a scientific Gate')
        self.assertIs(w.fit_result, fit, 'Navigation must not clear an existing fit')
        np.testing.assert_array_equal(w.peak_indices, peaks)
        self.assertTrue(bridge.backEnabled)
        self.invoke('zoom')
        self.assertEqual(bridge.mode, 'zoom rect')
        xlim, ylim = panned
        self.drag((xlim[0] + .2 * np.ptp(xlim), ylim[0] + .2 * np.ptp(ylim)),
                  (xlim[0] + .8 * np.ptp(xlim), ylim[0] + .8 * np.ptp(ylim)))
        zoomed = self.view()
        self.assertLess(np.ptp(zoomed[0]), np.ptp(panned[0]))
        self.invoke('back')
        self.assert_view(panned)
        self.assertTrue(bridge.forwardEnabled)
        self.invoke('forward')
        self.assert_view(zoomed)
        self.invoke('home')
        self.assert_view(initial)
        self.invoke('zoom')
        self.assertEqual(bridge.mode, '')
        self.assertIs(w.current_cube, cube)
        np.testing.assert_array_equal(cube.z, science)
        self.assertEqual(w.current_gate, gate)
        self.assertIs(w.fit_result, fit)
        np.testing.assert_array_equal(w.peak_indices, peaks)

    def test_same_zoom_and_coordinate_projection_do_not_measure_or_repaint(self):
        w, bridge = self.w, self.w.analysis_navigation
        # Initial QML sizing and delayed Agg work are outside the no-op probe.
        QTest.qWait(250)
        w.canvas.draw()
        self.settle()
        self.assertFalse(w.canvas._render_timer.isActive())
        initial = self.view()
        size = (item(w, 'AnalysisNavigation').width(), item(w, 'AnalysisNavigation').height())
        with patch.object(w, '_position_live_plots', wraps=w._position_live_plots) as layout, \
             patch.object(w.canvas, 'draw', wraps=w.canvas.draw) as draw:
            self.assertTrue(bridge.setZoom(bridge.zoomText, bridge.ownerToken))
            for index in range(20):
                w.toolbar.set_message(f'x={index}.123456789 y={index}.987654321 ' * 10)
            self.settle()
        self.assertEqual(layout.call_count, 0)
        self.assertEqual(draw.call_count, 0)
        self.assertEqual((item(w, 'AnalysisNavigation').width(), item(w, 'AnalysisNavigation').height()), size)
        self.assert_view(initial)
        self.assertEqual(bridge.zoomText, w.plot_page.zoom_text)

    def test_retired_owner_cannot_navigate_change_zoom_or_dispatch_settings(self):
        w, bridge = self.w, self.w.analysis_navigation
        retired = bridge.ownerToken
        w.activate_dataset(self.sessions[1].dataset_id)
        self.settle()
        self.assertTrue(bridge.enabled)
        self.assertNotEqual(bridge.ownerToken, retired)
        initial, zoom = self.view(), bridge.zoomText
        with patch.object(w._settings_copy_action, 'trigger', wraps=w._settings_copy_action.trigger) as copy:
            for command in ('home', 'pan', 'zoom', 'settings_copy', 'comparison'):
                self.assertFalse(bridge.invoke(command, retired), command)
            self.assertFalse(bridge.setZoom('200%', retired))
        self.assertEqual(copy.call_count, 0)
        self.assertEqual(bridge.mode, '')
        self.assertEqual(bridge.zoomText, zoom)
        self.assert_view(initial)

    def test_settings_actions_remain_authoritative_and_comparison_opens(self):
        w, bridge = self.w, self.w.analysis_navigation
        actions = {'copy_svg': w._graph_copy_svg_action,
                   'copy_png': w._graph_copy_png_action,
                   'settings_copy': w._settings_copy_action,
                   'settings_paste': w._settings_paste_action,
                   'settings_copy_to': w._settings_copy_to_action,
                   'comparison': w._comparison_action}
        for row in bridge.actions:
            action = actions[row['key']]
            self.assertEqual(row['enabled'], action.isEnabled())
            self.assertEqual(row['text'], action.text())
            self.assertEqual(row['tooltip'], action.toolTip())
        self.assertFalse(w._settings_paste_action.isEnabled())
        self.assertFalse(bridge.invoke('settings_paste', bridge.ownerToken))
        self.assertTrue(w._settings_copy_action.isEnabled())
        captured = bridge.ownerToken
        w._set_current_gate_from_index(2)
        w.set_plot_presentation(replace(w.workspace.active.presentation,
                                       text_pt=w.workspace.active.presentation.text_pt + 1))
        self.assertEqual(bridge.ownerToken, captured,
                         'Gate and visual edits must preserve a captured settings owner')
        self.assertTrue(bridge.invoke('settings_copy', captured))
        self.assertIsNotNone(w._plot_settings_clipboard)
        self.assertTrue(w._settings_paste_action.isEnabled())
        self.assertTrue(next(row for row in bridge.actions if row['key'] == 'settings_paste')['enabled'])
        for key, current_only in [('settings_paste', True), ('settings_copy_to', False)]:
            self.assertTrue(actions[key].isEnabled())
            # Keep the authoritative QAction and handler; stop at the dialog
            # boundary, where independent format-dialog tests own editing.
            with patch.object(w, '_show_paste_settings_dialog') as show:
                self.invoke(key)
            show.assert_called_once_with(current_only=current_only)
        self.assertTrue(w._comparison_action.isEnabled())
        before = len(w._plot_comparison_windows)
        self.invoke('comparison')
        self.assertEqual(len(w._plot_comparison_windows), before + 1)
        comparison = w._plot_comparison_windows[-1]
        self.assertTrue(comparison.isVisible())
        comparison.close()
        w._settings_copy_action.setEnabled(False)
        w._settings_copy_action.setToolTip('Source temporarily unavailable')
        row = next(row for row in bridge.actions if row['key'] == 'settings_copy')
        self.assertFalse(row['enabled'])
        self.assertEqual(row['tooltip'], 'Source temporarily unavailable')
        self.assertEqual(bridge.ownerToken, captured)
        self.assertFalse(bridge.invoke('settings_copy', bridge.ownerToken))

    def open_subplots_from_overflow(self):
        host = item(self.w, 'AnalysisNavigation')
        control = item(host, 'analysisNavMore')
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        QTest.mouseClick(self.w.root_window, Qt.LeftButton, pos=point)
        self.settle()
        entry = item(host, 'analysisNavSubplots')
        self.assertTrue(entry.isVisible())
        self.assertTrue(entry.isEnabled())
        point = entry.mapToScene(QPointF(entry.width()/2, entry.height()/2)).toPoint()
        QTest.mouseClick(input_receiver(self.w, entry), Qt.LeftButton, pos=point)
        self.settle()
        dialog = self.w.toolbar._subplot_dialog
        self.assertIsNotNone(dialog)
        self.addCleanup(lambda: dialog.deleteLater() if isValid(dialog) else None)
        self.assertTrue(dialog.window().isVisible())
        return dialog

    def test_quick_subplot_overflow_ordinary_notice_keeps_canonical_physical_geometry(self):
        from spectra_app.quick_dialog import QuickDialog
        from PySide6.QtQuick import QQuickView
        w = self.w
        axes = tuple(w.canvas.figure.axes)
        self.assertTrue(axes)
        self.assertTrue(all(ax.get_subplotspec() is None for ax in axes))
        before = [ax.get_position().bounds for ax in axes]
        presentation = w.workspace.active.presentation
        cube, science = w.current_cube, w.current_cube.z.copy()
        initial = self.view()
        dialog = self.open_subplots_from_overflow()
        self.assertIsInstance(dialog, QuickDialog)
        self.assertIsInstance(dialog.window(), QQuickView)
        self.assertIs(dialog.parent(), w.root_window)
        self.assertTrue(w.root_window.isAncestorOf(dialog.window(), QWindow.IncludeTransients))
        notice = item(dialog, 'subplotNotice')
        self.assertTrue(notice.property('visible'))
        self.assertIn('Properties', notice.property('text'))
        field = item(dialog, 'subplotField_left')
        point = field.mapToScene(QPointF(field.width()/2, field.height()/2)).toPoint()
        QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
        QTest.keyClick(dialog.window(), Qt.Key_A, Qt.ControlModifier)
        replace_text(dialog.window(), '.250')
        QTest.keyClick(dialog.window(), Qt.Key_Return)
        self.settle()
        self.assertAlmostEqual(w.canvas.figure.subplotpars.left, .25)
        np.testing.assert_allclose([ax.get_position().bounds for ax in axes], before)
        tight = item(dialog, 'subplotTightLayout')
        point = tight.mapToScene(QPointF(tight.width()/2, tight.height()/2)).toPoint()
        with warnings.catch_warnings(record=True):
            QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
            self.settle()
        np.testing.assert_allclose([ax.get_position().bounds for ax in axes], before)
        self.assertEqual(w.workspace.active.presentation, presentation)
        self.assertIs(w.current_cube, cube)
        np.testing.assert_array_equal(cube.z, science)
        self.assert_view(initial)
        QTest.keyClick(dialog.window(), Qt.Key_Escape)
        self.assertTrue(not isValid(dialog) or not dialog.window().isVisible())
        self.assertIsNone(w.toolbar._subplot_dialog)
        self.assertAlmostEqual(w.canvas.figure.subplotpars.left, .25)

    def test_subplot_dataset_roundtrip_retires_opening_and_stale_commands(self):
        w = self.w
        dialog = self.open_subplots_from_overflow()
        retired = dialog.bridge
        w.activate_dataset(self.sessions[1].dataset_id)
        self.settle()
        self.assertTrue(not isValid(dialog) or not dialog.window().isVisible())
        self.assertFalse(retired.check_owner())
        self.assertIsNone(w.toolbar._subplot_dialog)
        before = w.canvas.figure.subplotpars.left
        self.assertFalse(retired.setDraft('left', '.450'))
        self.assertEqual(w.canvas.figure.subplotpars.left, before)
        w.activate_dataset(self.sessions[0].dataset_id)
        self.settle()
        reopened = self.open_subplots_from_overflow()
        self.assertIsNot(reopened, dialog)
        self.assertFalse(retired.reset())
        self.assertFalse(retired.tightLayout())
        self.assertFalse(retired.exportValues())
        self.assertFalse(retired.setDraft('left', '.450'))
        self.assertEqual(w.canvas.figure.subplotpars.left, before)
        reopened.close()

    def customize_from_overflow(self, *, accept=True):
        from spectra_app.quick_dialog import QuickDialog
        from PySide6.QtQuick import QQuickView
        w = self.w
        opening_editor = w.toolbar._figure_dialog
        host = item(w, 'AnalysisNavigation')
        more = item(host, 'analysisNavMore')
        QTest.mouseClick(self.w.root_window, Qt.LeftButton,
                        pos=more.mapToScene(QPointF(more.width()/2, more.height()/2)).toPoint())
        self.settle()
        entry = item(host, 'analysisNavCustomize')
        self.assertTrue(entry.isEnabled())
        failures = []
        def respond():
            dialogs = [dialog for dialog in w.root_window.findChildren(QuickDialog)
                       if dialog.window().isVisible() and dialog.window().title() == 'Customize']
            try:
                self.assertEqual(len(dialogs), 1)
                prompt = dialogs[0]
                self.assertEqual(prompt.window().status(), QQuickView.Ready)
                self.assertIs(prompt.parent(), w.root_window)
                if accept:
                    selector = item(prompt, 'commonPromptChoice')
                    # Exercise the closed combobox's keyboard selection. This
                    # also works when its popup would be a separate native window.
                    prompt.window().requestActivate()
                    selector.forceActiveFocus(Qt.TabFocusReason)
                    self.settle()
                    QTest.keyClick(prompt.window(), Qt.Key_Home)
                    for _ in range(w.canvas.figure.axes.index(w._heat_ax)):
                        QTest.keyClick(prompt.window(), Qt.Key_Down)
                    QTest.keyClick(prompt.window(), Qt.Key_Tab)
                    button = item(prompt, 'commonPromptAction_ok')
                else:
                    button = item(prompt, 'commonPromptAction_cancel')
                QTest.mouseClick(prompt.window(), Qt.LeftButton,
                    pos=button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint())
            except BaseException as error:
                failures.append(error)
                for dialog in dialogs:
                    dialog.reject()
        QTimer.singleShot(0, respond)
        QTest.mouseClick(input_receiver(w, entry), Qt.LeftButton,
                        pos=entry.mapToScene(QPointF(entry.width()/2, entry.height()/2)).toPoint())
        self.settle()
        if failures:
            raise failures[0]
        editor = w.toolbar._figure_dialog
        if accept:
            self.assertIsNotNone(editor)
            self.assertIsInstance(editor.window(), QQuickView)
            self.assertEqual(editor.window().modality(), Qt.NonModal)
            self.assertTrue(editor.window().isVisible())
            self.assertIs(editor.bridge._axes, w._heat_ax)
        else:
            self.assertIs(editor, opening_editor)
        return editor

    def test_quick_customize_overflow_selector_cancel_apply_and_project_capture(self):
        from tests.test_figure_options_quick import FigureOptionsQuickTests
        w = self.w
        initial = self.view()
        self.customize_from_overflow(accept=False)
        self.assert_view(initial)
        editor = self.customize_from_overflow()
        helper = FigureOptionsQuickTests()
        helper.edit(editor, 'axes.title', 'Unapplied opening draft')
        self.assertIs(self.customize_from_overflow(accept=False), editor)
        self.assertTrue(editor.bridge.active)
        self.assertEqual(editor.bridge.drafts['axes.title'], 'Unapplied opening draft')
        self.assertEqual(w._heat_ax.get_title(), '')
        helper.edit(editor, 'axis.x.min', '1.5')
        helper.edit(editor, 'axis.x.max', '4.5')
        helper.edit(editor, 'axes.title', 'Transient title')
        helper.click(editor, 'figureTabMappables')
        helper.edit(editor, 'mappable.0.min', '-2')
        helper.edit(editor, 'mappable.0.max', '10')
        helper.click(editor, 'figureApply')
        self.assertTrue(editor.bridge.active)
        np.testing.assert_allclose(w._heat_ax.get_xlim(), (1.5, 4.5))
        self.assertEqual((w._dat_number('xmin'), w._dat_number('xmax')), (1.5, 4.5))
        self.assertEqual(w.workspace.active.view.x_limits, (1.5, 4.5))
        self.assertEqual(w._heat_mesh.get_clim(), (-2., 10.))
        # Canonical range callbacks refresh the ordinary plot from declared
        # settings; a live title edit is intentionally transient.
        self.assertEqual(w._heat_ax.get_title(), '')
        helper.click(editor, 'figureTabAxes')
        helper.edit(editor, 'axes.title', 'Unapplied title')
        helper.click(editor, 'figureCancel')
        self.assertIsNone(w.toolbar._figure_dialog)
        self.assertEqual(w._heat_ax.get_title(), '')
        captured = w.capture_active_dataset(render=False)
        self.assertEqual(captured.view.color_limits, (-2., 10.))
        self.assertEqual(captured.view.x_limits, (1.5, 4.5))
        np.testing.assert_allclose(w._heat_ax.get_xlim(), (1.5, 4.5))

    def test_customize_dataset_roundtrip_retires_stale_editor(self):
        editor = self.customize_from_overflow()
        retired = editor.bridge
        self.w.activate_dataset(self.sessions[1].dataset_id)
        self.settle()
        self.assertIsNone(self.w.toolbar._figure_dialog)
        self.assertFalse(isValid(retired) and retired.active)
        self.w.activate_dataset(self.sessions[0].dataset_id)
        self.settle()
        before = self.view()
        self.assertFalse(retired.setDraft('axis.x.min', '2.25'))
        self.assertFalse(retired.apply())
        self.assert_view(before)

    def test_named_workbench_hides_strip_and_return_restores_ordinary_owner(self):
        w, bridge = self.w, self.w.analysis_navigation
        self.assertFalse(hasattr(w, 'analysis_zoom_combo'))
        self.assertNotIsInstance(w.toolbar, QToolBar)
        retired = bridge.ownerToken
        ordinary_graph_id = self.sessions[0].active_graph_id
        w._workbench_open(('heatmap', (self.sessions[0].dataset_id, ordinary_graph_id)))
        self.settle()
        self.assertIsNotNone(w._workbench_document())
        self.assertFalse(bridge.enabled)
        self.assertTrue(item(w, 'analysisCommands').isVisible())
        self.assertFalse(bridge.invoke('home', retired))
        w.open_graph_document(self.sessions[0].dataset_id, ordinary_graph_id)
        self.assertTrue(w.request_outer_view('analysis'))
        self.settle()
        self.assertIsNone(w._workbench_document())
        self.assertTrue(item(w, 'AnalysisNavigation').isVisible())
        self.assertTrue(bridge.enabled)
        self.assertNotEqual(bridge.ownerToken, retired)
        view, zoom = self.view(), bridge.zoomText
        self.assertFalse(bridge.invoke('pan', retired))
        self.assertFalse(bridge.setZoom('200%', retired))
        self.assertEqual(bridge.mode, '')
        self.assertEqual(bridge.zoomText, zoom)
        self.assert_view(view)


if __name__ == '__main__':
    unittest.main()
