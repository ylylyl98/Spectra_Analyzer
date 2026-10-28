"""Real Quick editing of subplot-managed Matplotlib geometry."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.transforms import Bbox
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from shiboken6 import isValid

from tests.formal_quick_helpers import item, pump
from tests import test_analysis_navigation_bridge as navigation_fixture

APP = navigation_fixture.APP


class SubplotEditorQuickTests(unittest.TestCase):
    def setUp(self):
        navigation_fixture.NavigationTests.setUp(self)
        self.dialogs = []
        self.addCleanup(self.dispose_editor)

    def dispose_editor(self):
        self.bridge.close()
        for dialog in self.dialogs:
            if isValid(dialog):
                dialog.deleteLater()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def open_editor(self):
        dialog = self.toolbar.configure_subplots()
        self.dialogs.append(dialog)
        pump(60)
        self.assertEqual(dialog.window().status(), QQuickView.Ready)
        self.assertTrue(dialog.window().isVisible())
        self.assertTrue(dialog.bridge.active)
        return dialog

    def click(self, dialog, name):
        control = item(dialog, name)
        scroll = item(dialog, 'subplotContent')
        ancestor = control.parentItem()
        while ancestor is not None and ancestor is not scroll:
            ancestor = ancestor.parentItem()
        if ancestor is scroll:
            viewport = scroll.property('contentItem')
            top = control.mapToItem(viewport, QPointF(0, 0)).y()
            offset = viewport.property('contentY')
            if top < 0:
                offset += top - 4
            elif top + control.height() > viewport.height():
                offset += top + control.height() - viewport.height() + 4
            viewport.setProperty('contentY', max(0., min(offset,
                viewport.property('contentHeight') - viewport.height())))
            pump()
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
        pump()
        return control

    def edit(self, dialog, key, text, commit=True):
        control = self.click(dialog, 'subplotField_' + key)
        QTest.keyClick(dialog.window(), Qt.Key_A, Qt.ControlModifier)
        if not text:
            QTest.keyClick(dialog.window(), Qt.Key_Backspace)
        for char in text:
            QTest.keyClick(dialog.window(), char)
        if commit:
            QTest.keyClick(dialog.window(), Qt.Key_Return)
        pump()
        expected = f'{float(text):.3f}' if commit and not dialog.bridge.validationError else text
        self.assertEqual(control.property('text'), expected)
        return control

    def values(self):
        return {key: getattr(self.canvas.figure.subplotpars, key)
                for key in ('top', 'bottom', 'left', 'right', 'hspace', 'wspace')}

    def test_keyboard_live_numeric_edits_pointer_step_and_reset_geometry(self):
        self.canvas.figure.clear()
        grid = self.canvas.figure.subplots(2, 2)
        axes = tuple(grid.flat)
        self.ax = axes[0]
        opening = self.values()
        geometry = [ax.get_position().bounds for ax in axes]
        old_horizontal = axes[1].get_position().x0 - axes[0].get_position().x1
        old_vertical = axes[0].get_position().y0 - axes[2].get_position().y1
        dialog = self.open_editor()
        self.assertFalse(item(dialog, 'subplotNotice').property('visible'))
        with patch.object(self.canvas, 'draw_idle', wraps=self.canvas.draw_idle) as scheduled:
            self.edit(dialog, 'top', '0.820', commit=False)
            self.assertEqual(self.values(), opening)
            scheduled.assert_not_called()
            QTest.keyClick(dialog.window(), Qt.Key_Return)
            pump()
            self.assertAlmostEqual(self.values()['top'], .82)
            for key, text in [('bottom', '0.180'),
                              ('left', '0.200'), ('right', '0.850'),
                              ('hspace', '0.300'), ('wspace', '0.350')]:
                self.edit(dialog, key, text)
                self.assertAlmostEqual(self.values()[key], float(text))
            self.click(dialog, 'subplotIncrement_left')
            self.assertAlmostEqual(self.values()['left'], .205)
            self.assertEqual(item(dialog, 'subplotField_left').property('text'), '0.205')
            self.click(dialog, 'subplotDecrement_left')
            self.assertAlmostEqual(self.values()['left'], .2)
            self.assertGreater(scheduled.call_count, 0)
        self.assertNotEqual([ax.get_position().bounds for ax in axes], geometry)
        np.testing.assert_allclose(Bbox.union([ax.get_position() for ax in axes]).bounds,
                                   (.2, .18, .65, .64))
        self.assertNotAlmostEqual(axes[1].get_position().x0 - axes[0].get_position().x1,
                                  old_horizontal)
        self.assertNotAlmostEqual(axes[0].get_position().y0 - axes[2].get_position().y1,
                                  old_vertical)
        self.click(dialog, 'subplotReset')
        self.assertEqual(self.values(), opening)
        np.testing.assert_allclose([ax.get_position().bounds for ax in axes], geometry)
        self.assertFalse(dialog.bridge.validationError)

    def test_invalid_finite_range_and_correlated_drafts_stay_visible_without_write(self):
        dialog = self.open_editor()
        for key, text in [('bottom', ''), ('bottom', 'nan'), ('bottom', 'inf'), ('bottom', '1.001'),
                          ('bottom', '-1'), ('top', '.01'), ('right', '.01')]:
            with self.subTest(key=key, text=text):
                self.click(dialog, 'subplotReset')
                before = self.values()
                self.edit(dialog, key, text)
                self.assertEqual(self.values(), before)
                self.assertTrue(dialog.bridge.validationError)
                self.assertTrue(item(dialog, 'subplotValidation').property('visible'))
        self.click(dialog, 'subplotReset')
        self.assertFalse(dialog.bridge.validationError)
        self.edit(dialog, 'bottom', '.879')
        self.assertAlmostEqual(self.values()['bottom'], .879)
        self.edit(dialog, 'top', '.879')
        self.assertAlmostEqual(self.values()['top'], .88)
        self.assertTrue(dialog.bridge.validationError)

    def test_tight_layout_rereads_actual_geometry_and_exports_selectable_text(self):
        self.ax.set_xlabel('Energy (eV)')
        self.ax.set_ylabel('Intensity (counts)')
        self.ax.set_title('Measured spectrum')
        dialog = self.open_editor()
        self.edit(dialog, 'left', '.300')
        before = self.ax.get_position().bounds
        with patch.object(self.canvas.figure, 'tight_layout',
                          wraps=self.canvas.figure.tight_layout) as tight:
            self.click(dialog, 'subplotTightLayout')
        tight.assert_called_once()
        self.assertNotEqual(self.ax.get_position().bounds, before)
        for key, value in self.values().items():
            self.assertEqual(item(dialog, 'subplotField_' + key).property('text'), f'{value:.3f}')
        self.click(dialog, 'subplotExport')
        exported = item(dialog, 'subplotExportText')
        self.assertTrue(exported.property('visible'))
        self.assertTrue(exported.property('readOnly'))
        text = exported.property('text')
        self.assertTrue(text)
        positions = [text.index(key) for key in self.values()]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(text, ',\n'.join(f'{key}={value:.3}' for key, value in self.values().items()))
        self.click(dialog, 'subplotExportText')
        QTest.keyClick(dialog.window(), Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(exported.property('selectedText'), text)
        for char in 'replace':
            QTest.keyClick(dialog.window(), char)
        self.assertEqual(exported.property('text'), text)

    def test_close_escape_keep_applied_and_reopen_captures_new_reset_defaults(self):
        dialog = self.open_editor()
        self.edit(dialog, 'left', '.250')
        self.click(dialog, 'subplotClose')
        self.assertTrue(not isValid(dialog) or not dialog.window().isVisible())
        self.assertAlmostEqual(self.values()['left'], .25)
        self.assertFalse(dialog.bridge.check_owner())
        reopened = self.open_editor()
        self.assertIsNot(reopened, dialog)
        self.edit(reopened, 'left', '.350')
        self.click(reopened, 'subplotReset')
        self.assertAlmostEqual(self.values()['left'], .25)
        self.edit(reopened, 'left', '.300')
        QTest.keyClick(reopened.window(), Qt.Key_Escape)
        pump()
        self.assertTrue(not isValid(reopened) or not reopened.window().isVisible())
        self.assertAlmostEqual(self.values()['left'], .3)
        self.assertFalse(dialog.bridge.setDraft('left', '.450'))
        self.assertAlmostEqual(self.values()['left'], .3)


if __name__ == '__main__':
    unittest.main()
