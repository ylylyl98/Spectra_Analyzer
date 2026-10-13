import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QEvent, QObject
from PySide6.QtGui import QFont, QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QToolButton, QToolTip, QTabBar

from run_qt import APP_QSS
from spectra_app.plot_page import PlotPage
from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings
from tests.background_helpers import wait_jobs

APP = QApplication.instance() or QApplication([])


class UiLayoutTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.old_font, self.old_qss, self.old_style = APP.font(), APP.styleSheet(), APP.style().objectName()
        APP.setStyle('Fusion')
        APP.setFont(QFont('Segoe UI', 10))
        APP.setStyleSheet(APP_QSS)
        self.paths = [self.folder / name for name in ('layout-a.dat', 'layout-b.dat')]
        for path in self.paths:
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/100} {1+i*i/10000} {2+i*i/5000} {3+i*i/3000}' for i in range(100)))
        self.w = MainWindow()
        self.w.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.w._load_dat_file_direct(self.paths[0])
        self.w.resize(1250, 860)
        self.extra_widgets = []
        self.w.show()
        QTest.qWait(30)

    def tearDown(self):
        wait_jobs(self.w)
        self.w.close()
        self.w.deleteLater()
        for widget in self.extra_widgets:
            widget.close()
            widget.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.setFont(self.old_font)
        APP.setStyleSheet(self.old_qss)
        APP.setStyle(self.old_style)

    def sidebar(self):
        self.assertTrue(hasattr(self.w, '_sidebar_tabs'), 'Task sidebar is missing')
        return self.w._sidebar_tabs

    def enter_analysis(self):
        self.w.open_graph_document()
        self.w.analyze_action.trigger()
        APP.processEvents()

    def test_plot_has_one_host_and_restores_analysis_sidebar_selection(self):
        w, sidebar = self.w, self.sidebar()
        w.open_graph_document()
        w.plot_tabs.setCurrentIndex(0)
        sidebar.setCurrentIndex(2)
        p = w.plot_page
        controls, spin = p.control_scroll, p.width_spin
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        APP.processEvents()
        self.assertEqual([sidebar.tabText(i) for i in range(sidebar.count())], ['Data', 'Analysis', 'View'])
        self.assertIs(sidebar.currentWidget(), controls)
        self.assertFalse(p.isAncestorOf(controls), 'A second parameter pane still belongs to the preview')
        p.width_spin.setValue(4.5)
        w.plot_tabs.setCurrentIndex(0)
        APP.processEvents()
        self.assertEqual([sidebar.tabText(i) for i in range(sidebar.count())], ['Data', 'Analysis', 'View'])
        self.assertEqual(sidebar.currentIndex(), 2)
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        self.assertIs(p.control_scroll, controls)
        self.assertIs(p.width_spin, spin)
        self.assertEqual(p.width_spin.value(), 4.5)
        sidebar.setCurrentIndex(0)
        self.assertFalse(w.imported_dataset_list.isVisible())
        expander = w._source_navigation_content.parentWidget().findChild(QToolButton)
        expander.click()
        self.assertTrue(w.imported_dataset_list.isVisible())

    def test_hosted_pending_numbers_commit_together_before_source_capture(self):
        w = self.w
        self.sidebar()
        w.open_graph_document()
        APP.processEvents()
        p = w.plot_page
        p.aspect_lock.setChecked(False)
        p._loading = True
        try:
            for spin, text in ((p.width_spin, '4.75'), (p.height_spin, '4.25'), (p.dpi_spin, '410')):
                spin.lineEdit().setText(text)
        finally:
            p._loading = False
        emissions = []
        p.presentationChanged.connect(emissions.append)
        copied = w.copy_plot_settings()
        self.assertEqual(len(emissions), 1)
        self.assertEqual((p.presentation.width_in, p.presentation.height_in, p.presentation.heatmap_dpi),
                         (4.75, 4.25, 410))
        self.assertEqual((p.width_spin.value(), p.height_spin.value(), p.dpi_spin.value()), (4.75, 4.25, 410))
        self.assertEqual((copied.style.width_in, copied.style.height_in, copied.style.heatmap_dpi), (4.75, 4.25, 410))

    def test_hosted_editor_identity_and_dataset_settings_survive_switches(self):
        w = self.w
        sidebar = self.sidebar()
        w.open_graph_document()
        owner = w.workspace.active_dataset_id
        editor = w.plot_page.width_spin
        w.plot_page.aspect_lock.setChecked(False)
        editor.setValue(4.75)
        w._load_dat_file_direct(self.paths[1])
        w.open_graph_document()
        other_editor = w.plot_page.width_spin
        self.assertIsNot(other_editor, editor)
        other_editor.setValue(5.25)
        sidebar.setCurrentWidget(w._data_sidebar_scroll)
        w.activate_dataset(owner)
        self.assertIs(w.plot_page.width_spin, editor)
        self.assertEqual(editor.value(), 4.75)
        self.assertEqual(other_editor.value(), 5.25)
        self.assertEqual(w.imported_dataset_list.count(), 2)
        w.plot_tabs.setCurrentIndex(0)
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        self.assertIs(sidebar.currentWidget(), w._data_sidebar_scroll)
        self.assertEqual(editor.value(), 4.75)

    def test_menu_commands_follow_underlying_readiness_and_emit_once(self):
        p = self.w.plot_page
        self.assertTrue(hasattr(p, '_command_actions'), 'Compact native command groups are missing')
        p.set_transfer_availability(False, None)
        for button in (p.copy_settings_btn, p.paste_settings_btn, p.comparison_btn):
            self.assertFalse(p._command_actions[button].isEnabled())
        p.set_format_names([])
        self.assertFalse(p._command_actions[p.format_apply_btn].isEnabled())
        p.set_transfer_availability(True, 'sample.dat')
        p.set_format_names(['Journal', 'Talk'], selected='Talk')
        self.assertEqual(p.format_combo.currentText(), 'Talk')
        p.set_format_names(['Journal', 'Talk'])
        self.assertEqual(p.format_combo.currentText(), 'Talk')
        p.set_format_names(['Journal'])
        self.assertEqual(p.format_combo.currentText(), 'Journal')
        events = []
        p.formatApplyRequested.disconnect()
        p.formatApplyRequested.connect(events.append)
        p._command_actions[p.format_apply_btn].trigger()
        self.assertEqual(events, ['Journal'])
        for button, action in p._command_actions.items():
            button.setEnabled(False)
            self.assertFalse(action.isEnabled())
            button.setEnabled(True)
            self.assertTrue(action.isEnabled())
        for button in (p.settings_menu_btn, p.formats_menu_btn, p.batch_menu_btn):
            self.assertIsInstance(button, QToolButton)
            self.assertTrue(button.focusPolicy() & Qt.TabFocus)

    def test_disabled_menu_commands_display_tooltip_guidance(self):
        p = self.w.plot_page
        self.w.plot_tabs.setCurrentWidget(self.w.graph_documents)
        p.set_transfer_availability(False, None)
        menu = p.settings_menu_btn.menu()
        menu.popup(p.settings_menu_btn.mapToGlobal(p.settings_menu_btn.rect().bottomLeft()))
        QTest.qWait(20)
        try:
            self.assertTrue(menu.isVisible())
            for button, guidance in (
                    (p.copy_settings_btn, 'Load a DAT and correct any page editor errors first.'),
                    (p.paste_settings_btn, 'Copy settings from a valid source page first.')):
                with self.subTest(command=button.text()):
                    action = p._command_actions[button]
                    self.assertFalse(action.isEnabled())
                    point = menu.actionGeometry(action).center()
                    QTest.mouseMove(menu, point)
                    APP.sendEvent(menu, QHelpEvent(QEvent.ToolTip, point, menu.mapToGlobal(point)))
                    QTest.qWait(10)
                    self.assertTrue(QToolTip.isVisible(), 'Disabled command guidance must be visible on hover')
                    self.assertEqual(QToolTip.text(), guidance)
        finally:
            menu.hide()
            QToolTip.hideText()
            QTest.qWait(350)

    def test_common_analysis_actions_are_at_top_of_their_task(self):
        w, sidebar = self.w, self.sidebar()
        self.enter_analysis()
        for button in (w.workbench_find_btn, w.workbench_filter_btn, w.workbench_fit_btn):
            QTest.qWait(20)
            scroll = sidebar.currentWidget()
            point = button.mapTo(scroll.viewport(), button.rect().center())
            self.assertTrue(scroll.viewport().rect().contains(point), 'Primary analysis action requires scrolling')
            self.assertTrue(w.workbench_status.isVisible())
        self.assertFalse(w.cmap_combo.isVisible())
        self.assertFalse(w.imported_dataset_list.isVisible())

    def test_narrow_window_and_sidebar_controls_have_no_horizontal_overflow(self):
        w, sidebar = self.w, self.sidebar()
        w.open_graph_document()
        # This viewport allowance is for a full-area document. Free windows
        # have intentionally user-controlled sizes, covered by workspace tests.
        w.graph_documents.tabbed_view()
        # DAT detail editing can reclaim the persistent Project pane's space.
        # The optional dock can be resized for larger fonts without forcing
        # that width on the Project tree or the whole application window.
        w.project_panel_action.setChecked(False)
        for font_size in (10, 13, 15):
            APP.setFont(QFont('Segoe UI', font_size))
            for width, height in ((1250, 860), (1100, 720)):
                w.resize(width, height)
                w.resizeDocks([w.dat_details_dock], [round(390 * font_size / 10)], Qt.Horizontal)
                QTest.qWait(30)
                self.assertEqual((w.width(), w.height()), (width, height))
                for mode in (0, 1):
                    w.plot_tabs.setCurrentIndex(mode)
                    for index in range(sidebar.count()):
                        sidebar.setCurrentIndex(index)
                        for analysis_index in (0, 1):
                            w._analysis_tabs.setCurrentIndex(analysis_index)
                            QTest.qWait(30)
                            scroll = sidebar.currentWidget()
                            self.assertIsInstance(scroll, QScrollArea)
                            self.assertNotEqual(scroll.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
                            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0,
                                             (font_size, width, sidebar.tabText(index), analysis_index,
                                              scroll.widget().minimumSizeHint().width()))
                self.assertGreater(w.plot_page.preview_scroll.viewport().width(), 650 if font_size == 10 else 450)
                # Independent documents add a native tab strip below the
                # existing Analysis / Plot tabs; preserve the same usable
                # viewport allowance after accounting for that visible chrome.
                document_tabs = w.graph_documents.findChild(QTabBar)
                self.assertGreater(w.plot_page.preview_scroll.viewport().height(),
                                   (450 if font_size == 10 else 350) - document_tabs.height())

    def test_standalone_page_keeps_usable_controls(self):
        p = PlotPage()
        self.extra_widgets.append(p)
        self.assertTrue(hasattr(p, 'control_scroll'))
        p.resize(1000, 720)
        p.show()
        APP.processEvents()
        self.assertTrue(p.isAncestorOf(p.control_scroll))
        self.assertTrue(p.width_spin.isVisible())
        self.assertEqual(p.control_scroll.horizontalScrollBar().maximum(), 0)

    def test_shared_range_spin_and_fix_controls_do_not_overlap(self):
        w = self.w
        for font_size in (9, 13, 15):
            APP.setFont(QFont('Segoe UI', font_size))
            for size in ((1250, 860), (1100, 720)):
                w.resize(*size)
                for mode in (0, 1):
                    w.plot_tabs.setCurrentIndex(mode)
                    if mode == 0:
                        self.sidebar().setCurrentIndex(2)
                    APP.processEvents()
                    for spin, lock in w._view_lock_controls.items():
                        self.assertIs(spin.parentWidget(), lock.parentWidget())
                        self.assertFalse(spin.geometry().intersects(lock.geometry()),
                            (font_size, size, mode, spin.geometry(), lock.geometry()))
                        self.assertGreaterEqual(spin.geometry().width(), spin.minimumWidth())

    def test_current_detection_paints_local_busy_state_before_computation(self):
        from spectra_app.peak_candidates import collect_peak_candidates
        w = self.w
        self.enter_analysis()
        w.workbench_scope.setCurrentIndex(0)
        APP.processEvents()
        painted = []
        class PaintObserver(QObject):
            def eventFilter(self, watched, event):
                if event.type() == QEvent.Paint:
                    painted.append(watched.text())
                return False
        observer = PaintObserver(w)
        w.workbench_find_btn.installEventFilter(observer)
        w.workbench_status.installEventFilter(observer)
        seen_before_computation = []
        def detect(*args, **kwargs):
            seen_before_computation.append(any('Finding candidates' in text for text in painted))
            return collect_peak_candidates(*args, **kwargs)
        with patch('spectra_app.peak_candidates.collect_peak_candidates', side_effect=detect):
            w.workbench_find_btn.click()
            wait_jobs(w)
        self.assertEqual(seen_before_computation, [True], painted)
        self.assertEqual(w.workbench_find_btn.text(), 'Find candidates')
        self.assertTrue(w.workbench_find_btn.isEnabled())

    def test_batch_progress_and_cancel_remain_visible_in_narrow_plot(self):
        w = self.w
        p = w.open_graph_document()
        self.sidebar().setCurrentWidget(p.control_scroll)
        p.batch_progress.show()
        p.batch_cancel_btn.show()
        p.batch_status.setText('88/100 attempted · 12 failed · 0 canceled')
        w.resize(1100, 720)
        w.resizeDocks([w.dat_details_dock], [390], Qt.Horizontal)
        QTest.qWait(30)
        self.assertEqual(w.width(), 1100)
        for control in (p.batch_progress, p.batch_status, p.batch_cancel_btn):
            self.assertTrue(control.isVisible())
            self.assertTrue(p.control_scroll.viewport().rect().contains(
                control.mapTo(p.control_scroll.viewport(), control.rect().bottomRight())))

    def test_analysis_numeric_editors_retain_space_in_compact_rows(self):
        w = self.w
        self.enter_analysis()
        w.workbench_detector.setCurrentText('D2')
        w.resizeDocks([w.dat_details_dock], [390], Qt.Horizontal)
        for spin in (w.workbench_xmin, w.workbench_xmax, w.workbench_sg_window):
            QTest.qWait(30)
            self.assertTrue(spin.isVisible())
            self.assertGreaterEqual(spin.width(), 40, 'A required numeric editor collapsed inside its row')
