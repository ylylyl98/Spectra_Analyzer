"""Isolated production Quick probes for the two retained command-sizing tests.

Do not import test_workbook_quick: its adapter tests construct a QApplication.
The production fixture enforces QGuiApplication and forbids construction of
QWidget, QMdiArea and QQuickWidget throughout startup, interaction and teardown.
"""
from pathlib import Path
import sys
import unittest

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPointF
from PySide6.QtGui import QFont
import shiboken6

from tests.quick_application_helpers import application, item, pump, wait_until


WORKBOOK_COMMANDS = ('workbookPlot_line', 'workbookPlot_scatter',
    'workbookPlot_line_symbol', 'workbookHeatmap', 'workbookAppend',
    'workbookChooseTarget', 'workbookPinTarget', 'workbookShowTarget', 'workbookAddAgain')
FORMAL_COMMANDS = ('formal_analyze', 'formal_zoom', 'formal_copy_svg',
    'formal_export_svg', 'formal_export_png', 'formal_settings_menu',
    'formal_formats_menu', 'formal_batch_menu')


class CommandSizingProbe(unittest.TestCase):
    def book(self, owner, folder):
        source = folder / 'commands.csv'
        source.write_text('X,Y\n0,10\n1,20\n2,15\n', encoding='utf-8')
        book = owner.import_xy_files([source])
        pump()
        self.assertEqual(owner.contextKind, 'workbook')
        return book

    def workbook_root(self, host):
        return item(host, 'workbookPlot_line').parentItem().parentItem()

    def assert_band(self, band, commands):
        self.assertTrue(commands.isVisible())
        self.assertGreater(commands.implicitHeight(), 0)
        self.assertAlmostEqual(band.height(), min(82, commands.implicitHeight()), delta=1)

    def assert_scrolled_commands_reachable(self, host, band, commands, names):
        # Find the real ScrollView's Flickable through the loaded command root.
        viewport = commands.parentItem()
        while viewport is not None and viewport is not band:
            if viewport.metaObject().indexOfProperty('contentY') >= 0:
                break
            viewport = viewport.parentItem()
        self.assertIsNotNone(viewport)
        self.assertIsNot(viewport, band)
        self.assertGreater(commands.implicitHeight(), 82)
        self.assertGreater(viewport.property('contentHeight'), viewport.height())
        self.assertAlmostEqual(viewport.property('contentHeight'), commands.implicitHeight(), delta=1)
        self.assertAlmostEqual(viewport.height(), band.height(), delta=1)
        for name in names:
            control = item(host, name)
            self.assertTrue(control.isVisible(), name)
            top = control.mapToItem(viewport, QPointF()).y() + viewport.property('contentY')
            viewport.setProperty('contentY', max(0., min(top,
                viewport.property('contentHeight') - viewport.height())))
            pump()
            point = control.mapToItem(band, QPointF())
            self.assertGreaterEqual(point.x(), -1, name)
            self.assertLessEqual(point.x() + control.width(), band.width() + 1, name)
            self.assertGreaterEqual(point.y(), -1, name)
            self.assertLessEqual(point.y() + control.height(), band.height() + 1, name)
        self.assertGreater(viewport.property('contentY'), 0)

    def enlarge_commands(self, commands, point_size=20):
        # Exercise the existing font property: real larger text creates wrapping
        # at the supported 900px window minimum, without altering layout rules.
        font = QFont(commands.property('controlFont'))
        font.setPointSize(point_size)
        self.assertTrue(commands.setProperty('controlFont', font))
        pump(100)

    def test_formal_height(self):
        with application() as (host, folder, errors):
            owner = host.controller
            source = folder / 'command-sizing.dat'
            source.write_text('Energy(eV) 0 1\n1 1 2\n2 3 4\n', encoding='utf-8')
            owner._load_dat_file_direct(source)
            owner.open_graph_document()
            pump()
            self.assertEqual(owner.contextKind, 'dat')
            band = item(host, 'contextCommandBand')
            formal = item(host, 'formalCommands')
            self.assertIs(formal.property('bridge'), owner.formalBridge)
            # Arial 10 fits in one row at 900px after font metrics settle.
            # A representative 13pt control font makes this a real wrap probe.
            self.enlarge_commands(formal, 13)
            def command_y(name):
                return item(host, name).mapToItem(formal, QPointF()).y()
            host.window.resize(1440, 800)
            pump(100)
            wait_until(lambda: command_y(FORMAL_COMMANDS[-1]) == command_y(FORMAL_COMMANDS[0]))
            self.assert_band(band, formal)
            wide_height = band.height()
            self.assertLess(wide_height, 82)
            host.window.resize(900, 800)
            pump(100)
            wait_until(lambda: command_y(FORMAL_COMMANDS[-1]) > command_y(FORMAL_COMMANDS[0]))
            self.assert_band(band, formal)
            self.assertGreater(formal.implicitHeight(), wide_height)
            self.enlarge_commands(formal)
            self.assert_band(band, formal)
            self.assert_scrolled_commands_reachable(host, band, formal, FORMAL_COMMANDS)

            dataset_id = owner.workspace.active_dataset_id
            graph_id = owner.workspace.active.active_graph_id
            self.book(owner, folder)
            self.assertFalse(shiboken6.isValid(formal))
            workbook = self.workbook_root(host)
            self.assertIs(workbook.property('bridge'), owner.workbookCommands)
            self.assert_band(band, workbook)
            self.enlarge_commands(workbook, 32)
            self.assert_band(band, workbook)
            self.assert_scrolled_commands_reachable(host, band, workbook, WORKBOOK_COMMANDS)

            owner.open_graph_document(dataset_id, graph_id)
            pump()
            self.assertFalse(shiboken6.isValid(workbook))
            restored = item(host, 'formalCommands')
            self.assert_band(band, restored)
            self.assertLess(band.height(), 82)
            self.assertFalse(errors, errors)

    def test_hidden_wrapping(self):
        from spectra_app.workbook_state import add_curve, create_comparison
        with application() as (host, folder, errors):
            owner = host.controller
            host.window.resize(1920, 800)
            book = self.book(owner, folder)
            book_key = ('workbook', book.workbook_id, '')
            book_owner = owner.graph_documents.document_for(book_key)
            band = item(host, 'contextCommandBand')
            workbook = self.workbook_root(host)
            self.assert_band(band, workbook)
            wide_height = workbook.implicitHeight()
            self.assertLess(wide_height, 82)
            graph = create_comparison(owner.workspace, 'Command isolation')
            add_curve(owner.workspace, graph.comparison_id, book.sheet_ids[0], 0, 1)
            owner.open_xy_comparison(graph.comparison_id)
            pump()
            # Inactive contexts are unloaded by the production Loader. There is
            # no retained hidden command widget to measure or resize anymore.
            self.assertFalse(shiboken6.isValid(workbook))
            self.assertEqual(owner.contextKind, 'xy')
            xy = item(host, 'XYCommandsRoot')
            self.assertTrue(xy.isVisible())
            self.assert_band(band, xy.parentItem())
            active_key = owner.graph_documents.registry.active_key
            document = owner.graph_documents.content_item(active_key)
            host.window.resize(900, 800)
            pump(100)
            self.assert_band(band, xy.parentItem())
            xy_band_height = band.height()
            document_height = document.height()
            # A refresh from the inactive workbook cannot alter this document's
            # command band or usable height, even at the narrower window width.
            owner.workbookCommands.stateChanged.emit()
            pump()
            self.assertAlmostEqual(document.height(), document_height, delta=1)
            self.assertAlmostEqual(band.height(), xy_band_height, delta=1)
            self.assertEqual(owner.graph_documents.registry.active_key, active_key)
            self.assertIs(owner.graph_documents.document_for(book_key), book_owner)

            owner.open_workbook(book.workbook_id)
            pump()
            narrow = self.workbook_root(host)
            self.assert_band(band, narrow)
            self.assertGreater(narrow.implicitHeight(), wide_height)
            owner.request_outer_view('analysis')
            pump()
            self.assertFalse(shiboken6.isValid(narrow))
            navigation = item(host, 'AnalysisNavigation')
            self.assert_band(band, navigation)
            navigation_height = band.height()
            self.assertLess(navigation_height, wide_height + 1)
            host.window.resize(1920, 800)
            pump(100)
            self.assert_band(band, navigation)
            self.assertAlmostEqual(band.height(), navigation_height, delta=1)
            owner.request_outer_view('workspace')
            pump()
            self.assertEqual(owner.contextKind, 'workbook')
            restored = self.workbook_root(host)
            self.assert_band(band, restored)
            self.assertAlmostEqual(restored.implicitHeight(), wide_height, delta=1)
            self.assertIs(owner.graph_documents.document_for(book_key), book_owner)
            self.assertFalse(errors, errors)


if __name__ == '__main__':
    name = sys.argv[1]
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(
        unittest.TestSuite([CommandSizingProbe('test_' + name)]))
    if result.wasSuccessful():
        print(name + ': PASS after Quick teardown', flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
