"""Palette pixels and owner-derived guidance on the real shared Quick panel."""
import base64
from dataclasses import replace
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

import numpy as np
from matplotlib import colormaps
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QAccessible, QImage
from PySide6.QtTest import QTest

from spectra_app.custom_colormaps import resolve_colormap
from tests.quick_migration_case import QuickMigrationCase
from spectra_app.shared_dat_properties import _colormap_preview
from tests import dat_properties_quick_helpers as dat
from tests.formal_quick_helpers import item, pump, input_receiver, replace_text, ensure_visible


class SharedDatPropertyAffordanceTests(QuickMigrationCase):
    def setUp(self):
        if os.name == 'nt' and self._testMethodName == 'test_common_swatches_render_in_selected_box_and_popup_without_resampling_on_labels':
            self.enterContext(patch.dict(os.environ, QT_QPA_PLATFORM='windows'))
        super().setUp()
        folder = self.folder
        self.w = self.controller
        self.w._show_error = self.fail
        path = folder / 'unknown.dat'
        path.write_text('Energy(eV) 0 1\n1 1 2\n2 2 4\n3 3 6\n')
        self.w._load_dat_file_direct(path)
        self.w.open_graph_document()
        self.w.request_task(2)
        self.w.root_window.resize(1250, 920)
        self.w.root_window.show()
        pump(60)

    def tearDown(self):
        self.assertFalse(self.quick_errors)
        super().tearDown()

    def test_common_swatches_render_in_selected_box_and_popup_without_resampling_on_labels(self):
        entries = self.w.dat_properties.state['colormaps'][:3]
        self.assertEqual([(entry['label'], entry['value']) for entry in entries],
                         [('jet', 'jet'), ('RdBu_r', 'RdBu_r'), ('RdBu_r_0.6', 'RdBu_r_p0p6')])
        for entry in entries:
            image = QImage.fromData(base64.b64decode(entry['preview'].split(',', 1)[1]))
            self.assertFalse(image.isNull())
            cmap = resolve_colormap(entry['value'])
            cmap = colormaps[cmap] if isinstance(cmap, str) else cmap
            expected = cmap(np.linspace(0., 1., 96), bytes=True)
            actual = np.asarray([image.pixelColor(x, 6).getRgb() for x in range(96)])
            np.testing.assert_array_equal(actual, expected)
        dat.reveal(self.w, 'colormap')
        selected = item(self.w, 'colormap_preview_selected')
        self.assertTrue(selected.isVisible())
        self.assertGreater(selected.width(), 0)
        self.assertEqual(selected.property('source').toString(), entries[1]['preview'])
        self.assertGreater(selected.property('sourceSize').width(), 0)
        combo = dat.click(self.w, 'colormap')
        popup = next(child for child in combo.findChildren(QObject) if child.inherits('QQuickPopup'))
        rows = [child for container in popup.property('contentItem').childItems()
                for child in container.childItems() if child.inherits('QQuickItemDelegate')]
        for index in range(3):
            row = next(row for row in rows if row.property('index') == index)
            preview = row.findChild(QObject, f'colormap_preview_{index}')
            self.assertIsNotNone(preview)
            self.assertTrue(preview.isVisible())
            self.assertEqual(preview.property('source').toString(), entries[index]['preview'])
            self.assertGreater(preview.property('sourceSize').width(), 0)
            shot = input_receiver(self.w, row).grabWindow()
            self.assertFalse(shot.isNull())
            center = preview.mapToScene(QPointF(preview.width() / 2, preview.height() / 2))
            scale = shot.devicePixelRatio()
            pixel = shot.pixelColor(round(center.x() * scale), round(center.y() * scale))
            expected = QImage.fromData(base64.b64decode(entries[index]['preview'].split(',', 1)[1])).pixelColor(48, 6)
            self.assertLess(max(abs(a - b) for a, b in zip(pixel.getRgb(), expected.getRgb())), 12)
        popup.setProperty('visible', False)
        before = _colormap_preview.cache_info().misses
        with patch('spectra_app.shared_dat_properties.resolve_colormap', side_effect=AssertionError('resampled')):
            editor = dat.reveal(self.w, 'plot_title')
            receiver = input_receiver(self.w, editor)
            point = editor.mapToScene(QPointF(20., editor.height()/2)).toPoint()
            QTest.mouseClick(receiver, Qt.LeftButton, pos=point)
            replace_text(receiver, 'literal label')
            self.assertEqual(editor.property('text'), 'literal label')
        self.assertEqual(_colormap_preview.cache_info().misses, before)

    def test_guidance_tracks_manual_and_recognized_source_and_is_exposed_on_controls(self):
        bridge = self.w.dat_properties
        unknown = 'Signal unrecognized; neutral Signal label. Edit to set a manual title.'
        self.assertEqual(bridge.state['signal_title_tooltip'], unknown)
        editor = dat.reveal(self.w, 'signal_title')
        self.assertEqual(QAccessible.queryAccessibleInterface(editor).text(QAccessible.Text.Description), unknown)
        auto = dat.reveal(self.w, 'signal_title_auto')
        self.assertEqual(QAccessible.queryAccessibleInterface(auto).text(QAccessible.Text.Description), unknown)
        dat.set_flag(self.w, 'signal_title_auto', False)
        manual = 'Manual colorbar label. Enable Auto from data to use signal metadata or filename.'
        self.assertEqual(bridge.state['signal_title_tooltip'], manual)
        self.assertEqual(QAccessible.queryAccessibleInterface(editor).text(QAccessible.Text.Description), manual)
        source = self.w._base_cube or self.w.current_cube
        self.w._base_cube = replace(source, signal_source_kind='pl')
        dat.set_flag(self.w, 'signal_title_auto', True)
        recognized = 'Automatic signal title from explicit metadata, filename, or applied processing.'
        self.assertEqual(bridge.state['signal_title_tooltip'], recognized)
        self.assertEqual(QAccessible.queryAccessibleInterface(auto).text(QAccessible.Text.Description), recognized)
        self.w.workspace.active.sidecar_writes_enabled = False
        bridge.syncFromOwner()
        bridge.focusRequested.emit('ranges', 'axis_override')
        pump(65)
        self.assertFalse(dat.dat_item(self.w, 'axis_override').isEnabled())
        hint = ensure_visible(item(self.w, 'shared_source_axis_hint'))
        self.assertTrue(hint.isVisible())
        self.assertEqual(hint.property('text'), bridge.state['source_axis_tooltip'])
        self.assertIn('saved axis interpretation', hint.property('text'))

    def test_colorbar_guidance_appears_on_real_pointer_hover(self):
        bridge = self.w.dat_properties
        for section in ('colors', 'ranges'):
            bridge.setDisclosure(section, False, bridge.ownerToken)
        editor = dat.reveal(self.w, 'signal_title')
        host = input_receiver(self.w, editor)
        host.requestActivate()
        pump(70)
        self.assertTrue(editor.property('hoverEnabled'))
        point = editor.mapToScene(QPointF(editor.width() / 2, editor.height() / 2)).toPoint()
        QTest.mouseMove(host, QPoint(1, 1))
        QTest.mouseMove(host, point)
        pump(750)
        self.assertTrue(editor.property('hovered'))
        attached = next(child for child in editor.findChildren(QObject)
                        if child.inherits('QQuickToolTipAttached'))
        self.assertTrue(attached.property('visible'))
        self.assertEqual(attached.property('text'), bridge.state['signal_title_tooltip'])
        tooltip = next(child for child in self.host.engine.findChildren(QObject)
                       if child.inherits('QQuickToolTip') and child.property('visible'))
        self.assertEqual(tooltip.property('text'), bridge.state['signal_title_tooltip'])


if __name__ == '__main__':
    unittest.main()
