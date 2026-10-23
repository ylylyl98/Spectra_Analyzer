"""Observable DAT layout and lightweight Workbook context at supported sizes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from run_qt import APP_QSS
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings, wait_for_background_jobs

APP = QApplication.instance() or QApplication([])


class DatLayoutCompactTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.old_font, self.old_qss, self.old_style = APP.font(), APP.styleSheet(), APP.style().objectName()
        APP.setStyle('Fusion')
        APP.setFont(QFont('Segoe UI', 10))
        APP.setStyleSheet(APP_QSS)
        path = self.folder / 'compact.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/100} {1+i*i/10000} {2+i*i/5000} {3+i*i/3000}' for i in range(100)))
        self.w = MainWindow()
        self.w.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.w._load_dat_file_direct(path)
        self.w.open_graph_document()
        self.w.resize(1100, 720)
        self.w.show()
        self.w.analyze_action.trigger()
        APP.processEvents()

    def tearDown(self):
        wait_for_background_jobs(self.w)
        self.w.close()
        self.w.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.setFont(self.old_font)
        APP.setStyleSheet(self.old_qss)
        APP.setStyle(self.old_style)

    def layout_at(self, width, height):
        w = self.w
        w.resize(width, height)
        w.resizeDocks([w.dat_details_dock], [390], Qt.Horizontal)
        w._main_splitter.setSizes([240, max(1, w._main_splitter.width()-244)])
        QTest.qWait(25)
        self.assertEqual((w.width(), w.height()), (width, height))
        self.assertTrue(w.project_panel.isVisible())
        self.assertTrue(w.dat_details_dock.isVisible())

    def test_find_filter_fit_fully_visible_with_both_sidebars(self):
        w = self.w
        for size in ((1250, 860), (1100, 720)):
            with self.subTest(size=size):
                self.layout_at(*size)
                scroll = w._analysis_sidebar_scroll
                scroll.verticalScrollBar().setValue(0)
                for control in (w.workbench_find_btn, w.workbench_filter_btn,
                                w.workbench_model, w.workbench_fit_btn, w.workbench_status):
                    self.assertTrue(control.isVisible())
                    area = QRect(control.mapTo(scroll.viewport(), control.rect().topLeft()), control.size())
                    self.assertTrue(scroll.viewport().rect().contains(area), (control, area))
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                self.assertGreaterEqual(w.workbench_preview.height(), 300)

    def test_linecut_toggle_reclaims_heatmap_space_and_preserves_data_and_split(self):
        w = self.w
        self.layout_at(1100, 720)
        action = getattr(w, 'workbench_linecut_action', None)
        self.assertIsNotNone(action, 'Auxiliary linecut needs a visible collapse command')
        self.assertTrue(action.isChecked())
        w.workbench_splitter.setSizes([310, 160])
        APP.processEvents()
        before = w.workbench_splitter.sizes()
        svg = w.workbench_preview.svg_bytes
        figure = w.workbench_linecut_canvas.figure
        lines = [(line.get_xdata().copy(), line.get_ydata().copy()) for line in figure.axes[0].lines]
        action.trigger()
        APP.processEvents()
        self.assertFalse(w.workbench_linecut_canvas.isVisible())
        self.assertGreater(w.workbench_preview.height(), before[0] + before[1] - 5)
        action.trigger()
        APP.processEvents()
        self.assertTrue(w.workbench_linecut_canvas.isVisible())
        self.assertLessEqual(abs(w.workbench_splitter.sizes()[1] - before[1]), 2)
        self.assertIs(w.workbench_linecut_canvas.figure, figure)
        self.assertEqual(w.workbench_preview.svg_bytes, svg)
        for line, (x, y) in zip(figure.axes[0].lines, lines):
            self.assertEqual(line.get_xdata().tolist(), x.tolist())
            self.assertEqual(line.get_ydata().tolist(), y.tolist())

    def test_workbook_properties_follow_sheet_mapping_and_selection_without_dirtying(self):
        w = self.w
        csv = self.folder / 'summary.csv'
        csv.write_text('time,signal,reference\n1,2,3\n4,5,6\n7,8,9\n')
        book = w.import_xy_files([csv], mappings=[(0, [1])], compare=False)
        page = w.open_workbook(book.workbook_id)
        sheet_page = page.tabs.currentWidget()
        APP.processEvents()
        text = w._context_properties.text()
        for expected in ('3 rows', '3 columns', 'time', 'signal'):
            self.assertIn(expected, text)
        changes = []
        page.changed.connect(lambda: changes.append(True))
        sheet_page.table.setCurrentIndex(sheet_page.model.index(1, 2))
        sheet_page.table.selectColumn(2)
        APP.processEvents()
        text = w._context_properties.text()
        self.assertIn('Current column: C: reference', text)
        self.assertIn('Selected columns: C: reference', text)
        self.assertEqual(changes, [], 'Selection-only context must not mutate the project')
        sheet_page.x_combo.setCurrentIndex(2)
        APP.processEvents()
        self.assertIn('X: C: reference', w._context_properties.text())

    def test_workbook_properties_follow_current_dat_sheet_and_value_mode(self):
        from spectra_app.workbook_state import ensure_dataset_sheet
        w = self.w
        processed = ensure_dataset_sheet(w.workspace, w.workspace.active_dataset_id, 'processed')
        book = w._sheet_owner(processed.sheet_id)
        page = w.open_workbook(book.workbook_id)
        original = next(w.workspace.sheets[identity] for identity in book.sheet_ids
                        if w.workspace.sheets[identity].role == 'original')
        page.select_sheet(original.sheet_id)
        APP.processEvents()
        self.assertIn('Current Sheet: ' + original.name, w._context_properties.text())
        self.assertIn('Values: Original', w._context_properties.text())
        changes = []
        page.changed.connect(lambda: changes.append(True))
        page.select_sheet(processed.sheet_id)
        APP.processEvents()
        self.assertIn('Current Sheet: ' + processed.name, w._context_properties.text())
        self.assertIn('Values: Processed', w._context_properties.text())
        self.assertEqual(changes, [])

    def test_compact_command_menu_preserves_exact_copy_and_source_navigation(self):
        w = self.w
        self.layout_at(1100, 720)
        expected = w.workbench_preview.svg_bytes
        copy_action = next(action for action in w.workbench_export_menu.actions() if action.text() == 'Copy SVG')
        # The offscreen clipboard owns its QMimeData until explicitly released.
        self.addCleanup(APP.clipboard().clear)
        copy_action.trigger()
        self.assertEqual(bytes(APP.clipboard().mimeData().data('image/svg+xml')), expected)
        source_id = w._workbench_document().source_graph_id
        source_action = next(action for action in w.workbench_links.actions() if action.text() == 'Source graph')
        source_action.trigger()
        self.assertEqual(w.workspace.active.active_graph_id, source_id)
        self.assertIs(w.plot_tabs.currentWidget(), w.graph_documents)

    def test_many_column_workbook_context_fits_and_retains_full_details(self):
        w = self.w
        csv = self.folder / 'wide.csv'
        csv.write_text(','.join(['time', *[f'signal_{i}' for i in range(100)]]) + '\n' +
                       '\n'.join(','.join(str(i + row) for i in range(101)) for row in range(3)))
        book = w.import_xy_files([csv], mappings=[(0, list(range(1, 101)))], compare=False)
        page = w.open_workbook(book.workbook_id)
        sheet_page = page.tabs.currentWidget()
        sheet_page.table.selectAll()
        APP.processEvents()
        label = w._context_properties
        self.assertIn('100 total', label.text())
        self.assertIn('101 total', label.text())
        self.assertNotIn('signal_99', label.text())
        self.assertIn('signal_99', label.toolTip())
        bounds = label.fontMetrics().boundingRect(
            QRect(0, 0, label.width() - 2*label.margin(), 10000), Qt.TextWordWrap, label.text())
        self.assertLessEqual(bounds.height(), label.height() - 2*label.margin())

