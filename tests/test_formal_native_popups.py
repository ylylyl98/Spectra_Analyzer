"""Formal command popups through their real windows, beyond the Quick host."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.plot_page import PlotPage
from spectra_app.quick_ui import QuickPanel
from tests.formal_quick_helpers import click_item, item, pump

APP = QApplication.instance() or QApplication([])


class FormalNativePopupsTests(unittest.TestCase):
    def setUp(self):
        self.page = PlotPage()
        self.host = QuickPanel('FormalCommands.qml', self.page.formal, bridge_property='bridge')
        self.host.resize(1100, self.host.height())
        self.host.show()
        self.host.activateWindow()
        pump(50)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        self.page.close()
        self.page.deleteLater()
        APP.processEvents()

    def assert_row_fits(self, row):
        window = row.window()
        self.assertIsNot(window, self.host.quickWindow())
        top = row.mapToScene(QPointF(0, 0))
        bottom = row.mapToScene(QPointF(row.width(), row.height()))
        self.assertGreaterEqual(top.x(), -1)
        self.assertGreaterEqual(top.y(), -1)
        self.assertLessEqual(bottom.x(), window.width() + 1)
        self.assertLessEqual(bottom.y(), window.height() + 1)
        return window

    def test_command_menus_anchor_to_buttons_and_reopen_without_clipping(self):
        menus = (
            ('settings', ('settings_copy', 'settings_paste', 'settings_copy_to', 'comparison')),
            ('formats', ('format_save', 'format_load', 'format_delete', 'format_apply')),
            ('batch', ('batch_export', 'batch_results')),
        )
        for name, commands in menus:
            for attempt in range(2):
                with self.subTest(menu=name, attempt=attempt):
                    click_item(self.host, 'formal_' + name + '_menu')
                    window = None
                    for command in commands:
                        window = self.assert_row_fits(item(self.host, 'formal_' + command))
                    button = item(self.host, 'formal_' + name + '_menu')
                    anchor = button.mapToGlobal(QPointF(0, button.height()))
                    self.assertAlmostEqual(window.y(), anchor.y(), delta=2)
                    QTest.keyClick(window, Qt.Key_Escape)
                    pump()
                    self.assertFalse(window.isVisible())

    def test_zoom_popup_fits_all_options_and_accepts_native_pointer_selection(self):
        click_item(self.host, 'formal_zoom')
        zoom = item(self.host, 'formal_zoom')
        popup = next(child for child in zoom.findChildren(QObject) if child.inherits('QQuickPopup'))
        view = popup.property('contentItem')
        rows = [child for child in view.childItems()[0].childItems()
                if child.inherits('QQuickItemDelegate')]
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assert_row_fits(row)
        row = next(child for child in rows if child.property('index') == 3)
        point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2))
        QTest.mouseClick(row.window(), Qt.LeftButton, pos=point.toPoint())
        pump()
        self.assertEqual(self.page.formal.state['zoom'], '200%')
        self.assertFalse(popup.property('visible'))

    def test_enabled_format_command_dispatches_once_from_actual_popup_window(self):
        self.page.set_format_names(['Literal <b>format</b>'])
        requests = []
        self.page.formatLoadRequested.connect(requests.append)
        click_item(self.host, 'formal_formats_menu')
        row = item(self.host, 'formal_format_load')
        window = self.assert_row_fits(row)
        self.assertTrue(row.isEnabled())
        point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2))
        QTest.mouseClick(window, Qt.LeftButton, pos=point.toPoint())
        pump()
        self.assertEqual(requests, ['Literal <b>format</b>'])
        self.assertFalse(window.isVisible())

