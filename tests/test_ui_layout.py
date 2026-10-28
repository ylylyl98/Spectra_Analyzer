"""Layout proofs through the actual production Quick shell and controls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import patch
from threading import Event
from PySide6.QtCore import QObject, QPointF, QRectF, QThread, Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from tests.quick_migration_case import QuickMigrationCase
from tests.settings_helpers import wait_for_background_jobs
from tests.quick_application_helpers import wait_until
from tests import source_navigation_helpers as sn
from tests import ordinary_analysis_quick_helpers as oa
from tests import analysis_quick_helpers as aq
from tests.formal_quick_helpers import click_item, item, pump, input_receiver


class UiLayoutTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        old_font = QGuiApplication.font()
        self.addCleanup(lambda: QGuiApplication.setFont(old_font))
        QGuiApplication.setFont(QFont('Segoe UI', 10))
        self.paths = [self.folder / name for name in ('layout-a.dat', 'layout-b.dat')]
        for path in self.paths:
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/100} {1+i*i/10000} {2+i*i/5000} {3+i*i/3000}' for i in range(100)))
        self.w = self.controller
        self.w._load_dat_file_direct(self.paths[0])
        self.host.window.resize(1250, 860)
        pump()

    def sidebar(self):
        sidebar = item(self.w, 'applicationProperties')
        self.assertTrue(sidebar.isVisible(), 'Task sidebar is missing')
        return sidebar

    def enter_analysis(self):
        self.w.open_graph_document()
        self.w.analyze_action.trigger()
        pump()

    def task(self, index):
        click_item(self.w, 'task_' + str(index))
        self.assertEqual(self.w.taskIndex, index)

    def outer(self, view):
        self.assertTrue(self.w.request_outer_view(view))
        pump()
        self.assertEqual(self.w.outerView, view)

    def resize_properties(self, width):
        pump()
        panel = item(self.w, 'dockedProperties')
        point = panel.mapToScene(QPointF(-2, panel.height() / 2)).toPoint()
        end = point + QPointF(panel.width() - width, 0).toPoint()
        QTest.mousePress(self.host.window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseMove(self.host.window, end, 30)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, Qt.NoModifier, end)
        pump()
        self.assertAlmostEqual(panel.width(), width, delta=2)

    def assert_inside(self, control, ancestor):
        point = control.mapToItem(ancestor, QPointF())
        bounds = QRectF(point.x(), point.y(), control.width(), control.height())
        self.assertTrue(QRectF(-1, -1, ancestor.width() + 2, ancestor.height() + 2).contains(bounds),
                        (control.objectName(), bounds, ancestor.size()))

    def preview(self):
        key = self.w.graph_documents.registry.active_key
        return item(self.w.graph_documents.content_item(key), 'formalPreview')

    def test_plot_has_one_host_and_restores_analysis_sidebar_selection(self):
        w, sidebar = self.w, self.sidebar()
        p = w.open_graph_document()
        self.outer('analysis')
        self.task(2)
        controls = item(w, 'propertiesScroll')
        editor = item(w, 'width_in')
        self.outer('workspace')
        self.task(2)
        self.assertEqual([item(w, 'task_' + str(i)).property('text') for i in range(3)],
                         ['Data', 'Analysis', 'View'])
        self.assertTrue(sidebar.isAncestorOf(controls))
        self.assertFalse(self.w.graph_documents.content_item(w.graph_documents.registry.active_key).isAncestorOf(controls),
                         'A second parameter pane still belongs to the preview')
        p.formal.setValue('width_in', 4.5)
        self.outer('analysis')
        self.assertEqual(w.taskIndex, 2)
        self.outer('workspace')
        self.assertIs(item(w, 'propertiesScroll'), controls)
        self.assertIs(item(w, 'width_in'), editor)
        self.assertEqual(float(editor.property('text')), 4.5)
        self.task(0)
        self.assertFalse(sn.control(w, 'sourceDatasets').isVisible())
        sn.click(w, 'sourceAdvancedDisclosure', menu=True)
        self.assertTrue(sn.control(w, 'sourceDatasets').isVisible())

    def test_hosted_pending_numbers_commit_together_before_source_capture(self):
        w = self.w
        self.sidebar()
        w.open_graph_document()
        self.task(2)
        pump()
        p = w.plot_page
        p.formal.setValue('aspect_locked', False)
        for field, text in (('width_in', '4.75'), ('height_in', '4.25'), ('heatmap_dpi', '410')):
            p.formal.setDraft(field, text)
        emissions = []
        p.presentationChanged.connect(emissions.append)
        copied = w.copy_plot_settings()
        self.assertEqual(len(emissions), 1)
        self.assertEqual((p.presentation.width_in, p.presentation.height_in, p.presentation.heatmap_dpi),
                         (4.75, 4.25, 410))
        fields = p.formal.state['fields']
        self.assertEqual(tuple(float(fields[field]) for field in ('width_in', 'height_in', 'heatmap_dpi')),
                         (4.75, 4.25, 410))
        self.assertEqual((copied.style.width_in, copied.style.height_in, copied.style.heatmap_dpi), (4.75, 4.25, 410))

    def test_hosted_editor_identity_and_dataset_settings_survive_switches(self):
        w = self.w
        w.open_graph_document()
        self.task(2)
        owner = w.workspace.active_dataset_id
        page = w.plot_page
        editor = item(w, 'width_in')
        page.formal.setValue('aspect_locked', False)
        page.formal.setValue('width_in', 4.75)
        w._load_dat_file_direct(self.paths[1])
        other_page = w.open_graph_document()
        self.task(2)
        # Production has one retained Properties tree; each document retains its
        # own controller and settings as the editor's explicit source changes.
        self.assertIs(item(w, 'width_in'), editor)
        self.assertIsNot(other_page.formal, page.formal)
        other_page.formal.setValue('width_in', 5.25)
        self.assertEqual(float(editor.property('text')), 5.25)
        self.task(0)
        w.activate_dataset(owner)
        pump()
        self.task(2)
        self.assertIs(item(w, 'width_in'), editor)
        self.assertEqual(float(editor.property('text')), 4.75)
        self.assertEqual(other_page.presentation.width_in, 5.25)
        self.assertEqual(len(w.source_navigation.datasetRows), 2)
        self.task(0)
        self.outer('analysis')
        self.outer('workspace')
        self.assertEqual(w.taskIndex, 0)
        self.task(2)
        self.assertEqual(float(editor.property('text')), 4.75)

    def test_menu_commands_follow_underlying_readiness_and_emit_once(self):
        p = self.w.open_graph_document()
        self.task(2)
        pump()
        p.set_transfer_availability(False, None)
        for command in ('settings_copy', 'settings_paste', 'comparison'):
            self.assertFalse(p.command_enabled(command))
            self.assertFalse(item(self.w, 'formal_' + command).isEnabled())
        p.set_format_names([])
        self.assertFalse(item(self.w, 'formal_format_apply').isEnabled())
        p.set_transfer_availability(True, 'sample.dat')
        p.set_format_names(['Journal', 'Talk'], selected='Talk')
        self.assertEqual(p.selected_format, 'Talk')
        self.assertEqual(item(item(self.w, 'formalProperties'), 'formal_format').property('currentText'), 'Talk')
        p.set_format_names(['Journal', 'Talk'])
        self.assertEqual(p.selected_format, 'Talk')
        p.set_format_names(['Journal'])
        self.assertEqual(p.selected_format, 'Journal')
        events = []
        p.formatApplyRequested.disconnect()
        p.formatApplyRequested.connect(events.append)
        p.formal.command('format_apply')
        self.assertEqual(events, ['Journal'])
        mount = self.w.graph_documents._presentations[self.w._document_key()].mount
        for command in p.formal.state['commands']:
            control = (item(mount.item, 'formalCopyPng') if command == 'copy_png'
                       else item(self.w, 'formal_' + command))
            p.set_command_availability(command, False)
            self.assertFalse(control.isEnabled(), command)
            p.set_command_availability(command, True)
            self.assertTrue(control.isEnabled(), command)
        for name in ('formal_settings_menu', 'formal_formats_menu', 'formal_batch_menu'):
            button = item(self.w, name)
            self.assertTrue(button.property('focusPolicy') & Qt.TabFocus)

    def test_disabled_menu_commands_display_tooltip_guidance(self):
        p = self.w.open_graph_document()
        self.task(2)
        pump()
        self.assertTrue(item(self.w, 'formalCommands').isVisible())
        self.assertGreater(self.host.window.width(), 0)
        self.assertGreater(self.host.window.height(), 0)
        p.set_transfer_availability(False, None)
        click_item(self.w, 'formal_settings_menu')
        try:
            for command, guidance in (
                    ('settings_copy', 'Load a DAT and correct any page editor errors first.'),
                    ('settings_paste', 'Copy settings from a valid source page first.')):
                with self.subTest(command=command):
                    control = item(self.w, 'formal_' + command)
                    self.assertTrue(control.isVisible())
                    self.assertFalse(control.isEnabled())
                    point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2))
                    QTest.mouseMove(input_receiver(self.w, control), point.toPoint())
                    pump(750)
                    tips = [child for child in control.findChildren(QObject)
                            if child.inherits('QQuickToolTip')]
                    self.assertEqual(len(tips), 1)
                    self.assertTrue(tips[0].property('visible'),
                                    'Disabled command guidance must be visible on hover')
                    self.assertEqual(tips[0].property('text'), guidance)
        finally:
            QTest.keyClick(input_receiver(self.w, control), Qt.Key_Escape)
            pump()

    def test_common_analysis_actions_are_at_top_of_their_task(self):
        w = self.w
        self.enter_analysis()
        viewport = item(w, 'propertiesScroll').property('contentItem')
        viewport.setProperty('contentY', 0)
        pump()
        for name in ('analysis_find', 'analysis_filter', 'analysis_fit'):
            self.assert_inside(aq.control(w, name), viewport)
            self.assertTrue(aq.control(w, 'analysis_status').isVisible())
        self.assertFalse(any(control.isVisible() for control in self.host.window.findChildren(QQuickItem)
                             if control.objectName() == 'sharedDatProperties'))
        self.task(0)
        self.assertFalse(sn.control(w, 'sourceDatasets').isVisible())

    def test_narrow_window_and_sidebar_controls_have_no_horizontal_overflow(self):
        w = self.w
        w.open_graph_document()
        w.graph_documents.setMode('tabbed')
        w.project_panel_action.setChecked(False)
        for font_size in (10, 13, 15):
            QGuiApplication.setFont(QFont('Segoe UI', font_size))
            for width, height in ((1250, 860), (1100, 720)):
                self.host.window.resize(width, height)
                pump()
                self.resize_properties(round(390 * font_size / 10))
                self.assertEqual((self.host.window.width(), self.host.window.height()), (width, height))
                for mode in ('analysis', 'workspace'):
                    self.outer(mode)
                    for index in range(3):
                        self.task(index)
                        for analysis_index in (0, 1):
                            oa.select_tab(w, analysis_index)
                            pump()
                            scroll = item(w, 'propertiesScroll')
                            viewport = scroll.property('contentItem')
                            self.assertTrue(scroll.isVisible())
                            self.assertGreater(viewport.width(), 0)
                            self.assertLessEqual(float(viewport.property('contentWidth')), viewport.width() + 1,
                                                 (font_size, width, mode, index, analysis_index))
                preview = self.preview()
                self.assertGreater(preview.width(), 650 if font_size == 10 else 450)
                self.assertGreater(preview.height(), (450 if font_size == 10 else 350) - 32)

    def test_standalone_page_keeps_usable_controls(self):
        # The former standalone QWidget is composed by the real application;
        # assert the same usable editor/preview boundary in its native host.
        p = self.w.open_graph_document()
        self.host.window.resize(1000, 720)
        self.task(2)
        controls = item(self.w, 'formalProperties')
        self.assertTrue(self.sidebar().isAncestorOf(controls))
        # Publication sections default to collapsed in the production shell.
        self.assertFalse(item(self.w, 'formal_page').property('expanded'))
        click_item(self.w, 'formal_page_toggle')
        self.assertTrue(item(controls, 'width_in').isVisible())
        viewport = item(self.w, 'propertiesScroll').property('contentItem')
        self.assertLessEqual(float(viewport.property('contentWidth')), viewport.width() + 1)
        self.assertIs(self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key), p)
        self.assertTrue(self.preview().isVisible())

    def test_shared_range_spin_and_fix_controls_do_not_overlap(self):
        w = self.w
        w.open_graph_document()
        for font_size in (9, 13, 15):
            QGuiApplication.setFont(QFont('Segoe UI', font_size))
            for size in ((1250, 860), (1100, 720)):
                self.host.window.resize(*size)
                for mode in ('analysis', 'workspace'):
                    self.outer(mode)
                    self.task(2)
                    root = item(w, 'sharedDatProperties')
                    for field in ('vmin', 'vmax', 'xmin', 'xmax', 'ymin', 'ymax'):
                        spin, lock = item(root, field), item(root, field + '_lock')
                        spin_rect = QRectF(spin.mapToItem(root, QPointF()), spin.size())
                        lock_rect = QRectF(lock.mapToItem(root, QPointF()), lock.size())
                        self.assertFalse(spin_rect.intersects(lock_rect),
                                         (font_size, size, mode, field, spin_rect, lock_rect))
                        self.assertGreaterEqual(spin.width(), 40)

    def test_current_detection_paints_local_busy_state_before_computation(self):
        from spectra_app.peak_candidates import collect_peak_candidates
        w = self.w
        self.enter_analysis()
        aq.set_choice(w, 'scope', 'current')
        pump()
        # Detection is asynchronous: prove native busy/cancel response while
        # its scientific worker is active rather than ordering that worker
        # behind the first painted frame.
        started, release = Event(), Event()
        painted, worker_is_gui = [], []
        def observe_frame():
            if started.is_set() and not release.is_set():
                painted.append(aq.control(w, 'analysis_status').property('text'))
        self.host.window.frameSwapped.connect(observe_frame)
        def detect(*args, **kwargs):
            worker_is_gui.append(QThread.currentThread() is self.host.window.thread())
            started.set()
            if not release.wait(5):
                raise RuntimeError('Busy-state test did not release the worker')
            return collect_peak_candidates(*args, **kwargs)
        try:
            with patch('spectra_app.peak_candidates.collect_peak_candidates', side_effect=detect):
                aq.click(w, 'analysis_find')
                wait_until(started.is_set, timeout=5)
                pump(120)
                self.assertEqual(worker_is_gui, [False])
                self.assertTrue(any('Finding candidates' in text for text in painted), painted)
                self.assertFalse(aq.control(w, 'analysis_find').isEnabled())
                self.assertTrue(aq.control(w, 'analysis_stop').isVisible())
                aq.click(w, 'analysis_stop')
                self.assertTrue(w._job_cancel_requested)
                release.set()
                wait_for_background_jobs(w)
        finally:
            release.set()
            wait_for_background_jobs(w, timeout=10)
            self.host.window.frameSwapped.disconnect(observe_frame)
        self.assertEqual(aq.control(w, 'analysis_find').property('text'), 'Find candidates')
        self.assertTrue(aq.control(w, 'analysis_find').isEnabled())

    def test_batch_progress_and_cancel_remain_visible_in_narrow_plot(self):
        w = self.w
        p = w.open_graph_document()
        self.task(2)
        p.set_batch_state(visible=True, maximum=100, value=88,
                          status='88/100 attempted · 12 failed · 0 canceled', cancel_visible=True)
        self.host.window.resize(1100, 720)
        self.resize_properties(390)
        pump(60)
        self.assertEqual(self.host.window.width(), 1100)
        bar = item(w, 'formalCommands')
        band = item(w, 'contextCommandBand')
        for name in ('formal_batch_progress', 'formal_batch_status', 'formal_batch_cancel'):
            control = item(w, name)
            self.assertTrue(control.isVisible())
            self.assert_inside(control, bar)
            self.assert_inside(control, band)
            self.assert_inside(control, self.host.window.contentItem())

    def test_analysis_numeric_editors_retain_space_in_compact_rows(self):
        w = self.w
        self.enter_analysis()
        aq.set_choice(w, 'detector', 'D2')
        self.resize_properties(390)
        for name in ('xmin', 'xmax', 'd2Window'):
            spin = aq.control(w, name)
            QTest.qWait(30)
            self.assertTrue(spin.isVisible())
            self.assertGreaterEqual(spin.width(), 40, 'A required numeric editor collapsed inside its row')
