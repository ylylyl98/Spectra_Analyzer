"""Native workspace start actions and identity-preserving Project filtering."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest, QSignalSpy

from tests.quick_migration_case import QuickMigrationCase
from tests.formal_quick_helpers import click_item, replace_text
from tests.project_helpers import click_project_row, click_project_control, project_control, settle_project

from tests.xy_helpers import set_xy_draft


class ProjectNavigationTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller

    def tearDown(self):
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def test_empty_workspace_actions_reuse_file_actions_and_recent_menu(self):
        window = self.window
        self.assertEqual(window.graph_documents.count, 0)
        self.assertIs(window.project.importAction._action, window._graph_command_actions['import'])
        opened_control = project_control(window, 'command_open_project')
        self.assertIs(opened_control.property('modelData')._action,
                      window._graph_command_actions['open_project'])
        recent = project_control(window, 'recentProjectsMenu')
        self.assertEqual(recent.property('title'), 'Recent projects')
        recent_path = self.folder / 'recent.spectra'
        window._remember_recent_project(recent_path)
        settle_project(window)
        self.assertEqual(window.recentProjects[0]['path'], str(recent_path))
        self.assertEqual(recent.property('count'), 3)  # project, separator, Clear
        with patch.object(window, '_open_project_async', return_value=True) as opened, patch(
                'spectra_app.main_window.FilePicker.getOpenFileName', return_value=(str(self.folder / 'a.spectra'), '')):
            click_item(window, 'command_open_project')
        opened.assert_called_once()

    def test_actual_qml_import_reuses_action_and_busy_lock_without_legacy_tree(self):
        window = self.window
        self.host.show()
        settle_project(window)
        self.assertTrue(project_control(window, 'projectNavigator').isVisible())
        self.assertIs(project_control(window, 'projectNavigator').property('project'), window.project)
        self.assertIsNone(getattr(window, 'project_panel', None))
        imported = QSignalSpy(window._graph_command_actions['import'].triggered)
        with patch('spectra_app.main_window.FilePicker.getOpenFileNames', return_value=([], '')):
            click_project_control(window, 'projectImport')
        self.assertEqual(imported.count(), 1)
        with patch.object(window, '_project_busy', return_value=True):
            window._refresh_navigator_actions()
            settle_project(window)
            self.assertFalse(project_control(window, 'projectImport').property('enabled'))
            self.assertFalse(project_control(window, 'projectSearch').property('enabled'))
            self.assertFalse(window.new_comparison_action.isEnabled())

    def test_actual_qml_keyboard_current_search_and_context_action_routes(self):
        path = self.folder / 'keys.csv'
        path.write_text('X,Y\n0,1\n1,2\n', encoding='utf-8')
        book = self.window.import_xy_files([path])
        from spectra_app.workbook_state import register_xy_sheet
        import numpy as np
        second = register_xy_sheet(self.window.workspace, book.workbook_id, 'Second',
                                   np.array([[0., 3.], [1., 4.]]), ['X', 'Y'])
        second.source_path = path
        self.window._refresh_data_navigator()
        self.host.show()
        first_key, second_key = ('sheet', book.sheet_ids[0]), ('sheet', second.sheet_id)
        click_project_row(self.window, first_key)
        QTest.keyClick(self.window.root_window, Qt.Key_Down, Qt.ControlModifier)
        self.assertEqual(self.window.project.current_key(), second_key)
        self.assertEqual(self.window.project.selected_keys(), [first_key])
        QTest.keyClick(self.window.root_window, Qt.Key_Space, Qt.ControlModifier)
        self.assertEqual(self.window.project.selected_keys(), [first_key, second_key])
        click_project_control(self.window, 'projectSearch')
        replace_text(self.window.root_window, 'Second')
        settle_project(self.window)
        self.assertEqual(self.window.project.searchText, 'Second')
        self.assertIn(second_key, self.window.project.visible_keys())
        self.assertNotIn(first_key, self.window.project.visible_keys())
        click_project_control(self.window, 'projectSearchClear')
        self.assertEqual(self.window.project.searchText, '')
        click_project_row(self.window, second_key, button=Qt.RightButton)
        self.assertTrue(project_control(self.window, 'projectContextMenu').property('visible'))
        with patch.object(self.window, 'open_workbook') as opened:
            QTest.keyClick(self.window.root_window, Qt.Key_Down)
            QTest.keyClick(self.window.root_window, Qt.Key_Return)
            settle_project(self.window)
        opened.assert_called_once_with(book.workbook_id, second.sheet_id)

    def test_short_actions_menu_scrolls_keyboard_target_fully_into_view_and_triggers_once(self):
        window = self.window
        path = self.folder / 'menu.dat'
        path.write_text('Energy(eV) 0 1\n1 2 3\n2 4 5\n3 6 7\n', encoding='utf-8')
        window._load_dat_file_direct(path)
        window.open_graph_document()
        from spectra_app.workbook_state import create_comparison
        comparison = create_comparison(window.workspace, 'Menu target')
        window._last_comparison_id = comparison.comparison_id
        window._refresh_navigator_actions()
        self.assertTrue(window.add_gate_again_action.isEnabled())
        self.host.window.resize(1100, 620)
        self.host.show()
        panel = project_control(window, 'projectNavigator')
        font = QFont(panel.property('controlFont'))
        font.setPointSize(15)
        panel.setProperty('controlFont', font)
        settle_project(window)
        click_project_control(window, 'projectActions')
        menu = project_control(window, 'projectActionsMenu')
        self.assertTrue(menu.property('visible'))
        self.assertLessEqual(menu.property('height'), panel.height())
        view = menu.property('contentItem')
        self.assertGreater(view.property('contentHeight'), view.height(),
                           (panel.height(), menu.property('height'),
                            view.property('contentHeight'), view.height()))
        last_index = len(window.project.actions) - 1
        for _ in range(len(window.project.actions) * 2):
            QTest.keyClick(window.root_window, Qt.Key_Down)
            if menu.property('currentIndex') == last_index:
                break
        settle_project(window)
        self.assertEqual(menu.property('currentIndex'), last_index)
        item = project_control(window, 'projectCommand_add_gate_again')
        view = menu.property('contentItem')
        top = item.mapToScene(QPointF(0, 0)).y()
        bottom = item.mapToScene(QPointF(0, item.height())).y()
        view_top = view.mapToScene(QPointF(0, 0)).y()
        self.assertGreaterEqual(top, view_top - 1)
        self.assertLessEqual(bottom, view_top + view.height() + 1)
        self.assertGreater(view.property('contentY'), 0)
        triggered = QSignalSpy(window.add_gate_again_action.triggered)
        before = len(comparison.curves)
        QTest.keyClick(window.root_window, Qt.Key_Return)
        settle_project(window)
        self.assertEqual(triggered.count(), 1)
        self.assertEqual(len(comparison.curves), before + 1)

    def test_name_and_source_search_keeps_ancestors_selection_and_active_document(self):
        paths = []
        for name in ('alpha', 'beta'):
            folder = self.folder / name
            folder.mkdir()
            path = folder / 'sample.csv'
            path.write_text('X,Y\n0,1\n1,2\n', encoding='utf-8')
            paths.append(path)
        book = self.window.import_xy_files(paths)
        self.window.open_workbook(book.workbook_id)
        project = self.window.project
        key = ('sheet', book.sheet_ids[0])
        project.select_key(key)
        active = self.window.graph_documents.registry.active_key
        project.setSearchText(str(paths[1].parent))
        self.assertEqual(project.current_key(), key)
        self.assertIn(key, project.selected_keys())
        self.assertNotIn(key, project.visible_keys())
        shown = [project.row_for_key(key) for key in project.visible_keys()]
        self.assertTrue(any(str(paths[1]) in row.tooltip for row in shown))
        self.assertEqual(self.window.graph_documents.registry.active_key, active)
        project.setSearchText('')
        self.assertEqual(set(project.visible_keys()), {row.key for row in project.all_rows()})
        self.assertEqual(project.current_key(), key)
        project.setSearchText('Original')
        self.assertIn(key, project.visible_keys())
        ancestor = project.row_for_key(key).parent
        while ancestor is not None:
            self.assertIn(ancestor, project.visible_keys())
            ancestor = project.row_for_key(ancestor).parent

    def test_search_covers_workbook_sheet_heatmap_and_xy_names(self):
        source = self.folder / 'table.csv'
        source.write_text('X,Y\n0,1\n1,2\n', encoding='utf-8')
        book = self.window.import_xy_files([source])
        book.name = 'Spectral workbook'
        sheet = self.window.workspace.sheets[book.sheet_ids[0]]
        sheet.name = 'Input samples'
        page = self.window._add_sheet_comparison(sheet.sheet_id)
        set_xy_draft(page, 'name', 'Overlay result')
        page.finish_editor()
        dat = self.folder / 'scan.dat'
        dat.write_text('Energy(eV) 0 1\n1 2 3\n2 4 5\n3 6 7\n', encoding='utf-8')
        self.window._load_dat_file_direct(dat)
        self.window.open_graph_document()
        session = self.window.workspace.active
        session.active_graph.name = 'Thermal map'
        self.window._refresh_data_navigator()
        for text, key in (
                ('Spectral workbook', ('workbook', book.workbook_id)),
                ('Input samples', ('sheet', sheet.sheet_id)),
                ('Overlay result', ('xy', page.comparison_id)),
                ('Thermal map', ('heatmap', (session.dataset_id, session.active_graph_id)))):
            with self.subTest(name=text):
                self.window.project.setSearchText(text)
                row = self.window.project.row_for_key(key)
                while row is not None:
                    self.assertIn(row.key, self.window.project.visible_keys())
                    row = self.window.project.row_for_key(row.parent)
