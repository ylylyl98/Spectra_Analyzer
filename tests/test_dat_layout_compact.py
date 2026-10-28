"""Observable DAT layout and lightweight Workbook context at supported sizes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtTest import QTest
from tests.quick_migration_case import QuickMigrationCase
from tests import analysis_quick_helpers as aq
from tests.formal_quick_helpers import item, pump, input_receiver


class DatLayoutCompactTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        old_font = QGuiApplication.font()
        self.addCleanup(lambda: QGuiApplication.setFont(old_font))
        QGuiApplication.setFont(QFont('Segoe UI', 10))
        path = self.folder / 'compact.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/100} {1+i*i/10000} {2+i*i/5000} {3+i*i/3000}' for i in range(100)))
        self.w = self.controller
        self.w._load_dat_file_direct(path)
        self.w.open_graph_document()
        self.host.window.resize(1100, 720)
        self.w.analyze_action.trigger()
        pump()

    def layout_at(self, width, height):
        self.host.window.resize(width, height)
        pump()
        panel = item(self.w, 'dockedProperties')
        start = panel.mapToScene(QPointF(-2, panel.height() / 2)).toPoint()
        end = start + QPointF(panel.width() - 390, 0).toPoint()
        QTest.mousePress(self.host.window, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(self.host.window, end, 30)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, Qt.NoModifier, end)
        pump()
        self.assertAlmostEqual(panel.width(), 390, delta=2)
        self.assertEqual((self.host.window.width(), self.host.window.height()), (width, height))
        project = item(self.w, 'projectNavigator')
        self.assertTrue(project.isVisible())
        self.assertAlmostEqual(project.width(), 240, delta=1)
        self.assertTrue(item(self.w, 'dockedProperties').isVisible())

    def context_label(self):
        self.assertTrue(self.w.request_task(0))
        pump()
        label = item(self.w, 'propertiesContextHint')
        self.assertTrue(label.isVisible())
        return label

    def test_find_filter_fit_fully_visible_with_both_sidebars(self):
        w = self.w
        for size in ((1250, 860), (1100, 720)):
            self.layout_at(*size)
            for detector in ('Original', 'D2'):
                with self.subTest(size=size, detector=detector):
                    aq.set_choice(w, 'detector', detector)
                    scroll = item(w, 'propertiesScroll')
                    viewport = scroll.property('contentItem')
                    viewport.setProperty('contentY', 0)
                    pump()
                    for name in ('analysis_find', 'analysis_filter', 'model', 'analysis_fit', 'analysis_status'):
                        control = aq.control(w, name)
                        self.assertTrue(control.isVisible())
                        area = aq.viewport_rect(w, name)
                        self.assertTrue(QRectF(0, 0, viewport.width(), viewport.height()).contains(QRectF(area)), (name, area))
                    self.assertEqual(aq.control(w, 'analysis_d2Note').isVisible(), detector == 'D2')
                    self.assertLessEqual(float(viewport.property('contentWidth')), viewport.width() + 1)
                    for name in ('model', 'filterPreset'):
                        self.assertGreater(aq.control(w, name).width(), 100,
                                           'A primary choice must retain a usable text area')
                    self.assertGreaterEqual(item(w, 'applicationWorkbenchPreview').height(), 300)

    def test_linecut_toggle_reclaims_heatmap_space_and_preserves_data_and_split(self):
        w = self.w
        self.layout_at(1100, 720)
        action = item(w, 'command_auxLinecut')
        self.assertTrue(action.isVisible(), 'Auxiliary linecut needs a visible collapse command')
        self.assertTrue(action.property('checked'))
        linecut = item(w, 'applicationLinecut')
        preview = item(w, 'applicationWorkbenchPreview')
        start = linecut.mapToScene(QPointF(linecut.width() / 2, -2)).toPoint()
        delta = round(linecut.height() - 160)
        QTest.mousePress(self.host.window, Qt.LeftButton, Qt.NoModifier, start)
        end = start + QPointF(0, delta).toPoint()
        QTest.mouseMove(self.host.window, end, 30)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, Qt.NoModifier, end)
        pump()
        before = [preview.height(), linecut.height()]
        self.assertAlmostEqual(before[1], 160, delta=2)
        svg = w.workbench_preview.svg_bytes
        figure = w.workbench_linecut_figure
        lines = [(line.get_xdata().copy(), line.get_ydata().copy()) for line in figure.axes[0].lines]
        aq.click_command(w, 'command_auxLinecut')
        pump()
        self.assertFalse(item(w, 'applicationLinecut').isVisible())
        self.assertGreater(item(w, 'applicationWorkbenchPreview').height(), before[0] + before[1] - 5)
        aq.click_command(w, 'command_auxLinecut')
        pump()
        self.assertTrue(item(w, 'applicationLinecut').isVisible())
        self.assertLessEqual(abs(item(w, 'applicationLinecut').height() - before[1]), 2)
        self.assertIs(w.workbench_linecut_figure, figure)
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
        sheet_page = page.current_sheet
        pump()
        text = self.context_label().property('text')
        for expected in ('3 rows', '3 columns', 'time', 'signal'):
            self.assertIn(expected, text)
        changes = []
        page.changed.connect(lambda: changes.append(True))
        sheet_page.setCurrentCell(1, 2)
        sheet_page.selectColumn(2)
        pump()
        text = self.context_label().property('text')
        self.assertIn('Current column: C: reference', text)
        self.assertIn('Selected columns: C: reference', text)
        self.assertEqual(changes, [], 'Selection-only context must not mutate the project')
        sheet_page.setX(2)
        pump()
        self.assertIn('X: C: reference', self.context_label().property('text'))

    def test_workbook_properties_follow_current_dat_sheet_and_value_mode(self):
        from spectra_app.workbook_state import ensure_dataset_sheet
        w = self.w
        processed = ensure_dataset_sheet(w.workspace, w.workspace.active_dataset_id, 'processed')
        book = w._sheet_owner(processed.sheet_id)
        page = w.open_workbook(book.workbook_id)
        original = next(w.workspace.sheets[identity] for identity in book.sheet_ids
                        if w.workspace.sheets[identity].role == 'original')
        page.select_sheet(original.sheet_id)
        pump()
        self.assertIn('Current Sheet: ' + original.name, self.context_label().property('text'))
        self.assertIn('Values: Original', self.context_label().property('text'))
        changes = []
        page.changed.connect(lambda: changes.append(True))
        page.select_sheet(processed.sheet_id)
        pump()
        self.assertIn('Current Sheet: ' + processed.name, self.context_label().property('text'))
        self.assertIn('Values: Processed', self.context_label().property('text'))
        self.assertEqual(changes, [])

    def test_compact_command_menu_preserves_exact_copy_and_source_navigation(self):
        w = self.w
        self.layout_at(1100, 720)
        expected = w.workbench_preview.svg_bytes
        aq.click_command(w, 'command_exportMenu')
        self.assertTrue(item(w, 'command_copySvg').isVisible())
        # The offscreen clipboard owns its QMimeData until explicitly released.
        self.addCleanup(QGuiApplication.clipboard().clear)
        aq.click_command(w, 'command_copySvg')
        self.assertEqual(bytes(QGuiApplication.clipboard().mimeData().data('image/svg+xml')), expected)
        source_id = w._workbench_document().source_graph_id
        aq.click_command(w, 'command_source')
        self.assertEqual(w.workspace.active.active_graph_id, source_id)
        self.assertEqual(w.outerView, 'workspace')
        self.assertEqual(w.graph_documents.registry.active_key, (w.workspace.active_dataset_id, source_id))

    def test_many_column_workbook_context_fits_and_retains_full_details(self):
        w = self.w
        csv = self.folder / 'wide.csv'
        csv.write_text(','.join(['time', *[f'signal_{i}' for i in range(100)]]) + '\n' +
                       '\n'.join(','.join(str(i + row) for i in range(101)) for row in range(3)))
        book = w.import_xy_files([csv], mappings=[(0, list(range(1, 101)))], compare=False)
        page = w.open_workbook(book.workbook_id)
        sheet_page = page.current_sheet
        sheet_page.selectAll()
        pump()
        label = self.context_label()
        self.assertIn('100 total', label.property('text'))
        self.assertIn('101 total', label.property('text'))
        self.assertNotIn('signal_99', label.property('text'))
        point = label.mapToScene(QPointF(label.width() / 2, label.height() / 2)).toPoint()
        QTest.mouseMove(input_receiver(w, label), point)
        pump(750)
        tips = [child for child in label.findChildren(QObject)
                if child.inherits('QQuickToolTip') and child.property('visible')]
        self.assertTrue(tips, 'Truncated Workbook context must expose full details on hover')
        self.assertTrue(any('signal_99' in tip.property('text') for tip in tips),
                        [tip.property('text') for tip in tips])
        self.assertLessEqual(float(label.property('contentHeight')),
                             label.height() - 2 * float(label.property('padding')))

