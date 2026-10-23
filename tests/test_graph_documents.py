import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import json
from copy import deepcopy
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, Qt, QPoint, QPointF, QRect
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMdiArea, QMessageBox, QStyle, QWidget

from spectra_app.main_window import MainWindow
from spectra_app.graph_documents import GraphDocuments
from spectra_app.plot_page import PlotPage
from spectra_app.project_io import read_project, write_project
from tests.settings_helpers import isolate_settings


APP = QApplication.instance() or QApplication([])


class GraphDocumentTests(unittest.TestCase):
    def setUp(self):
        self.directory = isolate_settings(self).parent
        self.window = MainWindow()
        self.errors = []
        self.window._show_error = self.errors.append
        self.addCleanup(self.close)
        self.paths = []
        for name in ('first', 'second'):
            path = self.directory / f'{name}.dat'
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
            self.paths.append(path)
        self.window._load_dat_file_direct(self.paths[0])
        self.window.show()
        APP.processEvents()

    def close(self):
        self.window.close()
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def open(self, dataset_id=None, graph_id=None):
        self.assertTrue(callable(getattr(self.window, 'open_graph_document', None)),
                        'Each graph needs an independent, reopenable document')
        page = self.window.open_graph_document(dataset_id, graph_id)
        self.assertTrue(page.isVisible(), 'An open document must display its retained page')
        self.window._render_plot_page()
        self.assertIsNotNone(page.rendered_page, page.status_label.text())
        return page

    def test_three_free_windows_keep_normal_geometry_across_tabs_close_and_resize(self):
        w = self.window
        self.open()
        for name in ('Detail', 'Overview'):
            w.create_graph(name)
            self.open()
        area = w.graph_documents
        self.assertEqual(area.viewMode(), QMdiArea.SubWindowView)
        windows = area.subWindowList()
        self.assertEqual(len({window.pos().toTuple() for window in windows}), 3)
        rectangles = [QRect(12 + i * 55, 14 + i * 32, 370, 255) for i in range(3)]
        for window, rect in zip(windows, rectangles):
            window.setGeometry(rect)
        area.tabbed_view()
        self.assertTrue(area.activeSubWindow().isMaximized(), 'Tabs must display one full-size graph')
        area.free_view()
        APP.processEvents()
        self.assertEqual([window.geometry() for window in windows], rectangles)
        selected = windows[-1].key
        area.close_document(selected)
        w.open_graph_document(*selected)
        self.assertEqual(area.window_for(selected).geometry(), rectangles[-1])
        w.resize(1100, 720)
        APP.processEvents()
        self.assertEqual([area.window_for(window.key).geometry() for window in windows], rectangles)
        self.assertTrue(w.free_graphs_action.isChecked())
        self.assertFalse(w.tabbed_graphs_action.isChecked())

    def test_restored_layout_adapts_once_after_final_free_or_tabbed_layout(self):
        key, closed = ('owner', 'open'), ('owner', 'closed')
        records = [{'key': list(key), 'open': True, 'rect': [99999, -500, 560, 390]},
                   {'key': list(closed), 'open': False, 'rect': [55, 65, 380, 270]}]
        for mode in ('free', 'tabbed'):
            with self.subTest(mode=mode):
                area = GraphDocuments(lambda key: True, lambda key: True)
                try:
                    area.cache_page(key, QWidget())
                    area.resize(800, 600)
                    if mode == 'free':
                        area.show()
                        APP.processEvents()
                    metadata = {'mode': mode, 'documents': records,
                                'active': list(key), 'stacking': [list(key)]}
                    area.restore_workspace(metadata, lambda key: key[1])
                    area.show()
                    area.resize(400, 300)
                    APP.processEvents()
                    window = area.window_for(key)
                    if mode == 'tabbed':
                        self.assertTrue(window.isMaximized())
                        self.assertEqual(area.workspace_snapshot((key, closed))['documents'], records)
                        area.free_view()
                        area.resize(360, 280)
                        APP.processEvents()
                    title = QRect(window.geometry())
                    title.setHeight(window.style().pixelMetric(QStyle.PM_TitleBarHeight, None, window))
                    reachable = area.viewport().rect().intersected(title)
                    self.assertGreaterEqual(reachable.width(), 120)
                    self.assertEqual(reachable.height(), title.height())
                    self.assertEqual(window.size().toTuple(), (560, 390))
                    self.assertEqual(area.workspace_snapshot((key, closed))['documents'], records)
                    self.assertIsNone(area.window_for(closed))
                    # Once restoration settles, even a deliberately offscreen
                    # user position remains under the user's control on resize.
                    manual = QRect(600, 45, 560, 390)
                    window.setGeometry(manual)
                    area.resize(320, 260)
                    APP.processEvents()
                    self.assertEqual(window.geometry(), manual)
                    if mode == 'free':
                        # A candidate layout's queued adaptation must not move
                        # the runtime geometry reinstated by a failed load.
                        parked = area.park()
                        area.restore(parked)
                        area.restore_workspace(metadata, lambda key: key[1])
                        area.restore(parked)
                        APP.processEvents()
                        self.assertEqual(area.window_for(key).geometry(), manual)
                        # Resetting before the next event-loop turn also ends
                        # the pending adaptation before new user positioning.
                        area.restore_workspace(metadata, lambda key: key[1])
                        area.reset_layout()
                        area.window_for(key).setGeometry(manual)
                        APP.processEvents()
                        self.assertEqual(area.window_for(key).geometry(), manual)
                finally:
                    area.close()
                    area.deleteLater()
                    APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_shared_commands_follow_active_owner_with_stable_host(self):
        w = self.window
        first = self.open()
        w.create_graph('Detail')
        second = self.open()
        self.assertTrue(hasattr(w, 'graph_command_host'), 'Active graph commands need a shared host')
        self.assertTrue(w.graph_command_host.isAncestorOf(second.export_svg_btn))
        self.assertFalse(first.command_bar.isVisible())
        self.assertFalse(second.isAncestorOf(second.export_svg_btn))
        w.open_graph_document(graph_id='default')
        self.assertTrue(w.graph_command_host.isAncestorOf(first.zoom_combo))
        self.assertTrue(first.command_bar.isVisible())
        self.assertFalse(second.command_bar.isVisible())
        w.plot_tabs.setCurrentIndex(0)
        self.assertFalse(w.graph_command_host.isVisible())
        self.assertFalse(first.command_bar.isVisible())
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        for key in tuple(w.graph_documents.visible_keys()):
            w.graph_documents.close_document(key)
        self.assertTrue(w.graph_command_host.isVisible())
        self.assertFalse(first.command_bar.isVisible())

    def test_scrolled_workspace_stores_document_coordinates_and_keeps_dragged_layout(self):
        self.window.open_graph_document()
        area = GraphDocuments(lambda key: True, lambda key: True)
        area.resize(600, 450)
        area.show()
        try:
            session = self.window.workspace.active
            second_graph = session.create_graph('Scrolled detail')
            keys = [(session.dataset_id, 'default'), (session.dataset_id, second_graph.graph_id)]
            for key in keys:
                page = PlotPage()
                page.take_control_scroll().setParent(page)
                page.control_scroll.hide()
                page.take_command_bar().setParent(page)
                page.command_bar.hide()
                area.cache_page(key, page)
                area.show_document(key, key[1])
            first, second = [area.window_for(key) for key in keys]
            first.setGeometry(20, 30, 400, 300)
            second.setGeometry(500, 60, 400, 300)
            APP.processEvents()
            area.horizontalScrollBar().setValue(250)
            APP.processEvents()
            snapshot = area.workspace_snapshot(keys)
            self.assertEqual([entry['rect'] for entry in snapshot['documents']],
                             [[20, 30, 400, 300], [500, 60, 400, 300]])
            # User movement in a scrolled viewport changes document position by
            # the same amount, without folding the viewport origin into it.
            second.move(second.pos() + QPoint(30, 15))
            snapshot = area.workspace_snapshot(keys)
            self.assertEqual(snapshot['documents'][1]['rect'], [530, 75, 400, 300])
            area.tabbed_view()
            area.free_view()
            APP.processEvents()
            self.assertEqual(area.workspace_snapshot(keys)['documents'], snapshot['documents'])
            self.assertEqual(second.x() - first.x(), 510)
            self.window.workspace.graph_workspace = area.workspace_snapshot(keys)
            destination = self.directory / 'scrolled-layout.spectra'
            write_project(destination, self.window.workspace)
            restored = read_project(destination).graph_workspace
            self.assertEqual(restored['documents'], snapshot['documents'])
            area.restore_workspace(restored, lambda key: key[1])
            self.assertEqual(area.workspace_snapshot(keys)['documents'], snapshot['documents'])
        finally:
            area.close()
            area.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_native_frame_drag_resize_keeps_scientific_page_and_free_layout(self):
        w = self.window
        page = self.open()
        area = w.graph_documents
        self.assertEqual(area.viewMode(), QMdiArea.SubWindowView)
        window = area.activeSubWindow()
        window.setGeometry(20, 20, 410, 300)
        rendered = page.rendered_page
        before = window.geometry()
        APP.processEvents()
        # Offscreen QTest.mouseMove omits held buttons. Deliver the move to the
        # native frame handler with the same button state as a physical drag.
        def move(point, buttons=Qt.NoButton):
            APP.sendEvent(window, QMouseEvent(QEvent.MouseMove, QPointF(point),
                QPointF(window.mapToGlobal(point)), Qt.NoButton, buttons, Qt.NoModifier))
        move(QPoint(140, 12))
        QTest.mousePress(window, Qt.LeftButton, pos=QPoint(140, 12))
        move(QPoint(180, 44), Qt.LeftButton)
        QTest.mouseRelease(window, Qt.LeftButton, pos=QPoint(180, 44))
        self.assertNotEqual(window.pos(), before.topLeft())
        edge = QPoint(window.width() - 2, window.height() - 2)
        size = window.size()
        move(edge)
        QTest.mousePress(window, Qt.LeftButton, pos=edge)
        move(edge + QPoint(28, 25), Qt.LeftButton)
        QTest.mouseRelease(window, Qt.LeftButton, pos=edge + QPoint(28, 25))
        self.assertNotEqual(window.size(), size)
        self.assertIs(page.rendered_page, rendered)

    def test_maximize_and_tab_transitions_keep_original_normal_rectangle(self):
        w = self.window
        self.open()
        area = w.graph_documents
        window = area.activeSubWindow()
        rect = QRect(35, 45, 400, 285)
        window.setGeometry(rect)
        window.showMaximized()
        APP.processEvents()
        area.tabbed_view()
        area.free_view()
        APP.processEvents()
        self.assertEqual(window.geometry(), rect)
        window.showMaximized()
        APP.processEvents()
        window.showNormal()
        self.assertEqual(window.geometry(), rect)

    def test_failed_open_after_all_closed_keeps_closed_on_plot_entry(self):
        w = self.window
        self.open()
        w.graph_documents.close_document()
        w.plot_tabs.setCurrentIndex(0)
        saved = w.save_project(self.directory / 'closed-rollback.spectra')
        with patch.object(w, '_project_installed_workspace', side_effect=ValueError('install failed')):
            with self.assertRaisesRegex(ValueError, 'install failed'):
                w.open_project(saved)
        w.plot_tabs.setCurrentWidget(w.graph_documents)
        self.assertEqual(w.graph_documents.subWindowList(), [])
        self.assertTrue(w.graph_command_host.isVisible())
        self.assertTrue(w.workspace_command_context.isVisible())
        self.assertEqual(w.workspace_command_context.text(), 'Open a Workbook or graph from Project.')

    def test_graphs_retain_pages_bytes_and_zoom_across_tab_and_tile_switches(self):
        w = self.window
        first = self.open()
        first.set_zoom('150%')
        original = first.rendered_page
        graph = w.create_graph('Detail')
        second = self.open(graph_id=graph.graph_id)
        self.assertIsNot(first, second)
        w.set_plot_presentation(replace(w.workspace.active.presentation, width_in=4.2, aspect_locked=False))
        w._render_plot_page()
        second_bytes = second.rendered_page.svg_bytes
        w.graph_documents.tile_graphs()
        self.assertEqual(w.graph_documents.viewMode(), QMdiArea.SubWindowView)
        w.resize(1100, 720)
        w.graph_documents.tile_graphs()
        APP.processEvents()
        self.assertEqual((w.width(), w.height()), (1100, 720))
        self.assertIs(w.open_graph_document(graph_id='default'), first)
        self.assertIs(first.rendered_page, original)
        self.assertEqual(first.zoom_combo.currentText(), '150%')
        self.assertEqual(second.rendered_page.svg_bytes, second_bytes)
        w.graph_documents.tabbed_view()
        self.assertEqual(len(w.graph_documents.subWindowList()), 2)

    def test_two_datasets_keep_independent_pages_and_inactive_actions_select_owner(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        original = first.rendered_page
        w._load_dat_file_direct(self.paths[1])
        second_id = w.workspace.active_dataset_id
        second = self.open()
        self.assertIsNot(first, second)
        self.assertIs(first.rendered_page, original)
        w.graph_documents.tile_graphs()
        APP.processEvents()
        QTest.mouseClick(first.preview, Qt.LeftButton, pos=QPoint(5, 5))
        self.assertEqual(w.workspace.active_dataset_id, first_id)
        self.assertIs(w.plot_page, first)
        first.propertyEditRequested.emit('title', '')
        w.plot_title_edit.setText('First graph only')
        w._finish_graph_editors()
        w._render_plot_page()
        self.assertEqual(w.workspace.datasets[first_id].view.annotations['plot_title'], 'First graph only')
        self.assertNotEqual(w.workspace.datasets[second_id].view.annotations['plot_title'], 'First graph only')
        w.open_graph_document(second_id)
        first.exportRequested.emit('svg')
        self.assertEqual(w.workspace.active_dataset_id, first_id)
        saved, = list(w._output_folder().glob('first*.svg'))
        self.assertEqual(saved.read_bytes(), first.rendered_page.svg_bytes)

    def test_tiling_with_application_style_keeps_compact_window_size(self):
        from run_qt import APP_QSS
        self.addCleanup(APP.setStyleSheet, APP.styleSheet())
        APP.setStyleSheet(APP_QSS)
        w = self.window
        self.open()
        w.create_graph('Detail')
        self.open()
        w.resize(1100, 720)
        w.graph_documents.tile_graphs()
        APP.processEvents()
        self.assertEqual((w.width(), w.height()), (1100, 720))
        for window in w.graph_documents.subWindowList():
            self.assertTrue(w.graph_documents.viewport().rect().contains(window.geometry()))

    def test_native_events_stay_in_their_window_even_with_identical_document_keys(self):
        w = self.window
        self.open()
        w.plot_title_edit.setText('Editable title')
        graph = w.create_graph('Detail')
        detail = self.open()
        project = w.save_project(self.directory / 'shared.spectra')
        other = MainWindow()
        try:
            other.open_project(project)
            page = other.open_graph_document(graph_id='default')
            other.resize(1250, 900)
            other.show()
            other.activateWindow()
            other._render_plot_page()
            APP.processEvents()
            self.assertEqual(other.workspace.active_dataset_id, w.workspace.active_dataset_id)
            self.assertIsNotNone(w.graph_documents.page_for((w.workspace.active_dataset_id, 'default')))
            detail.width_spin.lineEdit().setText('-')
            other._sidebar_tabs.setCurrentIndex(0)
            left, bottom, width, height = page.rendered_page.object_bounds['title'][0]
            point = QPoint(round((left + width / 2) * page.preview.width()),
                           round((1 - bottom - height / 2) * page.preview.height()))
            QTest.mouseDClick(page.preview, Qt.LeftButton, pos=point)
            APP.processEvents()
            self.assertIs(other._sidebar_tabs.currentWidget(), page.control_scroll)
            self.assertTrue(other.plot_title_edit.hasFocus())
            self.assertIs(w.plot_page, detail)
            self.assertEqual(w.workspace.active.active_graph_id, graph.graph_id)
            self.assertEqual(detail.width_spin.lineEdit().text(), '-')
        finally:
            detail.width_spin.setValue(4.)
            other.close()
            other.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()

    def test_close_last_view_and_reopen_retains_graph_and_project_content(self):
        w = self.window
        page = self.open()
        page.set_zoom('150%')
        page.width_spin.setValue(4.2)
        w._render_plot_page()
        original = page.rendered_page
        w._finish_graph_editors()
        w.save_project(self.directory / 'close-view.spectra')
        before = deepcopy(w.workspace.active.plot_collection())
        cube = w.workspace.active.current_cube
        w.graph_documents.activeSubWindow().close()
        self.assertEqual(len(w.graph_documents.subWindowList()), 0)
        self.assertIs(w.plot_page, page)
        self.assertEqual(w.workspace.active.plot_collection(), before)
        self.assertIs(w.workspace.active.current_cube, cube)
        w._refresh_project_modified()
        self.assertTrue(w.isWindowModified())
        self.assertIs(w.open_graph_document(), page)
        self.assertIs(page.rendered_page, original)
        self.assertEqual(page.zoom_combo.currentText(), '150%')
        self.assertEqual(page.width_spin.value(), 4.2)
        self.assertEqual(len(w.graph_documents.subWindowList()), 1)

    def test_invalid_draft_rejects_document_switch_and_close_atomically(self):
        w = self.window
        first = self.open()
        graph = w.create_graph('Detail')
        second = self.open()
        second.width_spin.lineEdit().setText('-')
        with self.assertRaises(ValueError):
            w.open_graph_document(graph_id='default')
        self.assertIs(w.plot_page, second)
        self.assertEqual(w.workspace.active.active_graph_id, graph.graph_id)
        self.assertIs(w.graph_documents.activeSubWindow().widget(), second)
        w.graph_documents.setActiveSubWindow(w.graph_documents.window_for((w.workspace.active_dataset_id, 'default')))
        self.assertIs(w.graph_documents.activeSubWindow().widget(), second)
        w.graph_documents.activeSubWindow().close()
        self.assertEqual(len(w.graph_documents.subWindowList()), 2)
        second.width_spin.setValue(4.)
        self.assertIs(w.open_graph_document(graph_id='default'), first)

    def test_close_active_view_selects_visible_neighbor_before_further_edits(self):
        w = self.window
        first = self.open()
        graph = w.create_graph('Detail')
        detail = self.open()
        w.graph_documents.activeSubWindow().close()
        self.assertIs(w.plot_page, first)
        self.assertEqual(w.workspace.active.active_graph_id, 'default')
        self.assertIs(w.graph_documents.activeSubWindow().widget(), first)
        w.plot_title_edit.setText('Visible neighbor')
        w._finish_graph_editors()
        self.assertNotEqual(w.workspace.active.graphs[graph.graph_id].view.annotations['plot_title'],
                            'Visible neighbor')
        self.assertIs(w.open_graph_document(graph_id=graph.graph_id), detail)

    def test_shared_science_invalidates_all_dataset_graphs_only(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        w.create_graph('Detail')
        detail = self.open()
        w._load_dat_file_direct(self.paths[1])
        other = self.open()
        untouched = other.rendered_page
        w.open_graph_document(first_id, 'default')
        w._replace_current_data(replace(w.current_cube, z=w.current_cube.z * 2),
                                mode='original', smooth=True, window_length=21)
        self.assertIsNone(first.rendered_page)
        self.assertIsNone(detail.rendered_page)
        self.assertIs(other.rendered_page, untouched)
        w.graph_documents.tile_graphs()
        w._render_visible_plot_page()
        self.assertIsNotNone(first.rendered_page)
        self.assertIsNotNone(detail.rendered_page)
        self.assertEqual(detail.rendered_page.owner_token[-1], detail.preview.snapshot.graph_id)

    def test_inactive_format_application_retires_only_target_dataset_graph(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        w._load_dat_file_direct(self.paths[1])
        source = self.open()
        w.set_plot_presentation(replace(w.workspace.active.presentation, width_in=4.8, aspect_locked=False))
        w.save_plot_format('wide')
        original = source.rendered_page
        outcomes = w.apply_plot_format('wide', [first_id])
        self.assertTrue(outcomes[0]['success'], outcomes)
        self.assertIsNone(first.rendered_page)
        self.assertIs(source.rendered_page, original)
        self.assertIs(w.plot_page, source)
        w.graph_documents.tile_graphs()
        w._render_visible_plot_page()
        self.assertEqual(first.rendered_page.figure_size_in[0], 4.8)
        self.assertEqual(first.width_spin.value(), 4.8)

    def test_graph_removal_and_project_reset_release_cached_documents(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        graph = w.create_graph('Detail')
        detail = self.open()
        stale = detail.rendered_page
        w.remove_graph(graph.graph_id)
        self.assertIsNone(w.graph_documents.page_for((first_id, graph.graph_id)))
        self.assertEqual(len(w.graph_documents.subWindowList()), 1)
        self.assertIs(w.plot_page, first)
        self.assertFalse(first.commit_rendered_page(stale))
        w.new_project()
        self.assertEqual(len(w.graph_documents.subWindowList()), 0)
        self.assertIsNone(w.graph_documents.page_for((first_id, 'default')))
        self.assertIsNone(w.plot_page.rendered_page)

    def test_dataset_removal_releases_all_its_graphs(self):
        w = self.window
        dataset_id = w.workspace.active_dataset_id
        self.open()
        graph = w.create_graph('Detail')
        self.open()
        w._load_dat_file_direct(self.paths[1])
        other = self.open()
        with patch('spectra_app.main_window.QMessageBox.warning', return_value=QMessageBox.Yes):
            self.assertTrue(w.remove_dataset(dataset_id))
        self.assertIsNone(w.graph_documents.page_for((dataset_id, 'default')))
        self.assertIsNone(w.graph_documents.page_for((dataset_id, graph.graph_id)))
        self.assertIs(w.plot_page, other)
        self.assertEqual(len(w.graph_documents.subWindowList()), 1)

    def test_owner_restore_failure_keeps_previous_page_and_controls(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        w._load_dat_file_direct(self.paths[1])
        second_id = w.workspace.active_dataset_id
        second = self.open()
        w.cmap_combo.setCurrentText('magma')
        restore = w.restore_dataset_controls

        def fail_first(session):
            if session.dataset_id == first_id:
                raise ValueError('projection failed')
            restore(session)

        with patch.object(w, 'restore_dataset_controls', side_effect=fail_first):
            with self.assertRaisesRegex(ValueError, 'projection failed'):
                w.open_graph_document(first_id)
        self.assertEqual(w.workspace.active_dataset_id, second_id)
        self.assertIs(w.plot_page, second)
        self.assertEqual(w.cmap_combo.currentText(), 'magma')
        self.assertIs(w.graph_documents.activeSubWindow().widget(), second)
        self.assertIs(w.open_graph_document(first_id), first)

    def test_document_commands_follow_busy_empty_and_closed_states(self):
        w = self.window
        self.open()
        w._refresh_graph_commands()
        self.assertTrue(w.close_graph_view_action.isEnabled())
        with patch.object(w, '_project_busy', return_value=True):
            w._refresh_graph_commands()
            self.assertFalse(w.open_graph_action.isEnabled())
            self.assertFalse(w.close_graph_view_action.isEnabled())
        w.graph_documents.activeSubWindow().close()
        w._refresh_graph_commands()
        self.assertTrue(w.open_graph_action.isEnabled())
        self.assertFalse(w.close_graph_view_action.isEnabled())
        self.assertFalse(w.tile_graphs_action.isEnabled())
        w.new_project()
        self.assertFalse(w.open_graph_action.isEnabled())

    def test_peak_edit_retires_inactive_labeled_graph_but_retains_unlabeled_page(self):
        w = self.window
        labeled = self.open()
        labeled.include_peaks.setChecked(True)
        w._render_plot_page()
        w.create_graph('No labels')
        plain = self.open()
        plain.include_peaks.setChecked(False)
        w._render_plot_page()
        unchanged = plain.rendered_page
        w._add_manual_peak(1.5)
        self.assertIsNone(labeled.rendered_page)
        self.assertIs(plain.rendered_page, unchanged)
        w.open_graph_document(graph_id='default')
        w._render_plot_page()
        self.assertEqual([peak.x for peak in labeled.preview.snapshot.peaks], [1.5])

    def test_cross_dataset_document_switch_saves_outgoing_sidecar(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        w._load_dat_file_direct(self.paths[1])
        second_id = w.workspace.active_dataset_id
        self.open()
        w.open_graph_document(first_id)
        first.aspect_lock.setChecked(False)
        first.width_spin.setValue(4.8)
        w.open_graph_document(second_id)
        payload = json.loads(w._plot_state_store.path_for(self.paths[0]).read_text(encoding='utf-8'))
        self.assertEqual(payload['plot']['graphs'][0]['plot']['presentation']['width_in'], 4.8)

    def test_failed_project_install_restores_open_and_closed_document_runtime(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        first.set_zoom('150%')
        graph = w.create_graph('Closed detail')
        closed = self.open()
        closed.set_zoom('100%')
        w.graph_documents.activeSubWindow().close()
        w._load_dat_file_direct(self.paths[1])
        second = self.open()
        second.set_zoom('200%')
        w.graph_documents.tile_graphs()
        original_bytes = first.rendered_page.svg_bytes
        project = w.save_project(self.directory / 'restore.spectra')
        with patch.object(w, '_project_installed_workspace', side_effect=ValueError('installation failed')):
            with self.assertRaisesRegex(ValueError, 'installation failed'):
                w.open_project(project)
        self.assertIs(w.plot_page, second)
        self.assertIs(w.graph_documents.page_for((first_id, 'default')), first)
        self.assertIs(w.graph_documents.page_for((first_id, graph.graph_id)), closed)
        self.assertEqual(len(w.graph_documents.subWindowList()), 2)
        self.assertEqual(w.graph_documents.viewMode(), QMdiArea.SubWindowView)
        self.assertEqual((first.zoom_combo.currentText(), second.zoom_combo.currentText()), ('150%', '200%'))
        self.assertEqual(first.rendered_page.svg_bytes, original_bytes)
        self.assertIs(w.open_graph_document(first_id, graph.graph_id), closed)


if __name__ == '__main__':
    unittest.main()
