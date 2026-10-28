"""Real Quick input reaches captured Matplotlib axes, curves and mappables."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import datetime
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.colors import ListedColormap, to_rgba
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from shiboken6 import isValid

from spectra_app.figure_options import FigureOptionsBridge
from spectra_app.quick_dialog import QuickDialog
from tests.formal_quick_helpers import item, pump
from tests import test_analysis_navigation_bridge as navigation_fixture

APP = navigation_fixture.APP


class FigureOptionsQuickTests(unittest.TestCase):
    def setUp(self):
        navigation_fixture.NavigationTests.setUp(self)
        self.dialogs = []
        self.addCleanup(self.dispose)

    def dispose(self):
        self.bridge.close()
        for dialog in self.dialogs:
            if isValid(dialog):
                dialog.deleteLater()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def open_editor(self):
        bridge = FigureOptionsBridge(self.bridge, self.ax)
        dialog = QuickDialog(bridge, 'FigureOptions.qml', self.w.root_window, size=(620, 720))
        self.dialogs.append(dialog)
        dialog.show()
        pump(60)
        self.assertEqual(dialog.window().status(), QQuickView.Ready)
        self.assertTrue(bridge.active)
        return dialog

    def click(self, dialog, name):
        # A nonmodal editor can remain behind its owner's axes selector.
        # Match a user's window activation before sending native pointer input.
        dialog.window().requestActivate()
        if APP.platformName() == 'windows':
            self.assertTrue(QTest.qWaitForWindowActive(dialog.window(), 1500))
        pump()
        control = item(dialog, name)
        ancestor = control.parentItem()
        while ancestor is not None:
            if ancestor.objectName() in ('figureAxesContent', 'figureCurvesContent', 'figureMappablesContent'):
                viewport = ancestor.property('contentItem')
                top = control.mapToItem(viewport, QPointF(0, 0)).y()
                offset = viewport.property('contentY')
                if top < 0:
                    offset += top - 4
                elif top + control.height() > viewport.height():
                    offset += top + control.height() - viewport.height() + 4
                viewport.setProperty('contentY', max(0., min(offset,
                    viewport.property('contentHeight') - viewport.height())))
                pump()
                break
            ancestor = ancestor.parentItem()
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
        pump()
        return control

    def edit(self, dialog, key, text):
        control = self.click(dialog, 'figureField_' + key)
        QTest.keyClick(dialog.window(), Qt.Key_A, Qt.ControlModifier)
        if not text:
            QTest.keyClick(dialog.window(), Qt.Key_Backspace)
        for char in text:
            QTest.keyClick(dialog.window(), char)
        pump()
        self.assertEqual(control.property('text'), text)
        self.assertEqual(dialog.bridge.drafts[key], text)
        return control

    def select(self, dialog, name, steps=1):
        self.click(dialog, name)
        for _ in range(steps):
            QTest.keyClick(dialog.window(), Qt.Key_Down)
        QTest.keyClick(dialog.window(), Qt.Key_Return)
        pump()

    def test_axes_invalid_draft_atomic_apply_then_cancel_keeps_applied(self):
        dialog = self.open_editor()
        original = self.ax.get_xlim()
        with patch.object(self.canvas, 'draw', wraps=self.canvas.draw) as draw:
            self.edit(dialog, 'axes.title', 'Measured signal')
            control = self.edit(dialog, 'axis.x.min', 'bad')
            self.assertEqual(self.ax.get_title(), '')
            draw.assert_not_called()
            self.click(dialog, 'figureApply')
            self.assertEqual(self.ax.get_title(), '')
            self.assertEqual(self.ax.get_xlim(), original)
            self.assertEqual(control.property('text'), 'bad')
            self.assertTrue(item(dialog, 'figureError_axis.x.min').property('visible'))
            self.edit(dialog, 'axis.x.min', '.2')
            self.edit(dialog, 'axis.x.max', '.8')
            self.click(dialog, 'figureApply')
            self.assertEqual(self.ax.get_title(), 'Measured signal')
            np.testing.assert_allclose(self.ax.get_xlim(), (.2, .8))
            self.assertGreater(draw.call_count, 0)
        self.edit(dialog, 'axes.title', 'Unapplied')
        self.click(dialog, 'figureCancel')
        self.assertFalse(dialog.window().isVisible())
        self.assertEqual(self.ax.get_title(), 'Measured signal')
        self.assertFalse(dialog.bridge.active)

    def test_curve_rgba_marker_choices_and_retained_delegate(self):
        line = self.ax.lines[0]
        line.set_label('Long measured signal ' * 8)
        line.set_marker('o')
        line.set_alpha(.75)
        second, = self.ax.plot([0, 1], [1, 0], label='Reference')
        dialog = self.open_editor()
        self.click(dialog, 'figureTabCurves')
        field = self.edit(dialog, 'curve.0.label', 'Signal')
        self.edit(dialog, 'curve.0.width', '2.75')
        self.edit(dialog, 'curve.0.color', '#12345680')
        preview = item(dialog, 'figureColorFill_curve.0.color').property('color')
        self.assertAlmostEqual(preview.redF(), 0x12/255, places=3)
        self.assertAlmostEqual(preview.greenF(), 0x34/255, places=3)
        self.assertAlmostEqual(preview.blueF(), 0x56/255, places=3)
        self.assertAlmostEqual(preview.alphaF(), 0x80/255, places=3)
        self.edit(dialog, 'curve.0.size', '7.5')
        self.edit(dialog, 'curve.0.facecolor', '#ff000040')
        self.edit(dialog, 'curve.0.edgecolor', '#00ff00c0')
        self.select(dialog, 'figureChoice_curve.0.drawstyle')
        self.select(dialog, 'figureChoice_curve.0.linestyle')
        self.assertEqual(line.get_label(), 'Long measured signal ' * 8)
        self.assertIs(item(dialog, 'figureField_curve.0.label'), field)
        self.select(dialog, 'figureCurveSelector')
        self.edit(dialog, 'curve.1.label', 'Reference renamed')
        self.click(dialog, 'figureApply')
        self.assertEqual(line.get_label(), 'Signal')
        self.assertEqual(second.get_label(), 'Reference renamed')
        self.assertEqual(line.get_linewidth(), 2.75)
        self.assertEqual(line.get_markersize(), 7.5)
        self.assertIsNone(line.get_alpha())
        np.testing.assert_allclose(to_rgba(line.get_color()), to_rgba('#12345680'))
        np.testing.assert_allclose(to_rgba(line.get_markerfacecolor()), to_rgba('#ff000040'))
        np.testing.assert_allclose(to_rgba(line.get_markeredgecolor()), to_rgba('#00ff00c0'))
        self.assertEqual(line.get_drawstyle(), dialog.bridge.drafts['curve.0.drawstyle'])
        self.assertEqual(line.get_linestyle(), dialog.bridge.drafts['curve.0.linestyle'])

    def test_image_collection_custom_cmap_and_ok_application(self):
        cmap = ListedColormap(['red', 'blue'], name='Private colors')
        image = self.ax.imshow([[0., 1.], [2., 3.]], cmap=cmap, label='Camera')
        mesh = self.ax.pcolormesh([[1., 2.], [3., 4.]], label='Mesh')
        dialog = self.open_editor()
        self.click(dialog, 'figureTabMappables')
        self.assertTrue(any(entry['label'].startswith('Private colors') for entry in dialog.bridge.mappables[0]['colormaps']))
        self.edit(dialog, 'mappable.0.label', 'Camera view')
        self.edit(dialog, 'mappable.0.min', '5')
        self.edit(dialog, 'mappable.0.max', '-2')
        self.select(dialog, 'figureChoice_mappable.0.interpolation')
        self.select(dialog, 'figureChoice_mappable.0.stage')
        self.select(dialog, 'figureMappableSelector')
        self.assertFalse(item(dialog, 'figureChoice_mappable.1.interpolation').isVisible())
        self.edit(dialog, 'mappable.1.label', 'Mesh view')
        self.edit(dialog, 'mappable.1.min', '-3')
        self.edit(dialog, 'mappable.1.max', '6')
        self.select(dialog, 'figureChoice_mappable.1.cmap')
        selected = dialog.bridge.drafts['mappable.1.cmap']
        self.click(dialog, 'figureAccept')
        self.assertFalse(dialog.window().isVisible())
        self.assertEqual(dialog.result(), dialog.Accepted)
        self.assertEqual(image.get_label(), 'Camera view')
        self.assertIs(image.get_cmap(), cmap)
        self.assertEqual(image.get_clim(), (-2., 5.))
        self.assertEqual(mesh.get_label(), 'Mesh view')
        self.assertEqual(mesh.get_clim(), (-3., 6.))
        self.assertNotEqual(mesh.get_cmap().name, 'viridis')
        self.assertTrue(selected)

    def test_date_limits_and_all_axis_fields_remain_usable_at_minimum_size(self):
        self.ax.clear()
        self.ax.plot([datetime.datetime(2024, 1, 1), datetime.datetime(2024, 1, 3)], [0, 1], label='Date series')
        converter, units = self.ax.xaxis.get_converter(), self.ax.xaxis.units
        dialog = self.open_editor()
        dialog.resize(400, 440)
        pump()
        self.assertTrue(item(dialog, 'figurePersistenceHint').isVisible())
        self.edit(dialog, 'axis.x.min', '2024-01-03 12:00:00')
        self.edit(dialog, 'axis.x.max', '2024-01-01 12:00:00')
        self.edit(dialog, 'axis.y.label', 'Intensity')
        self.click(dialog, 'figureLegend')
        self.click(dialog, 'figureApply')
        self.assertGreater(*self.ax.get_xlim())
        self.assertIs(self.ax.xaxis.get_converter(), converter)
        self.assertEqual(self.ax.xaxis.units, units)
        self.assertEqual(self.ax.get_ylabel(), 'Intensity')
        self.assertTrue(item(dialog, 'figureAccept').isVisible())


if __name__ == '__main__':
    unittest.main()
