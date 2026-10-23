"""Native workspace start actions and identity-preserving Project filtering."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from spectra_app.main_window import MainWindow
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])


class ProjectNavigationTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_empty_workspace_actions_reuse_file_actions_and_recent_menu(self):
        documents = self.window.graph_documents
        self.assertIs(documents.empty_import_button.defaultAction(), self.window._graph_command_actions['import'])
        self.assertIs(documents.empty_open_button.defaultAction(), self.window._graph_command_actions['open_project'])
        self.assertIs(documents.empty_recent_button.menu(), self.window._recent_projects_menu)
        with patch.object(self.window, '_open_project_async', return_value=True) as opened, patch(
                'spectra_app.main_window.QFileDialog.getOpenFileName', return_value=(str(self.folder / 'a.spectra'), '')):
            documents.empty_open_button.click()
        opened.assert_called_once()

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
        tree = self.window.data_navigator
        selected = next(item for item in self.window._navigator_items()
                        if item.data(0, Qt.UserRole) == ('sheet', book.sheet_ids[0]))
        tree.setCurrentItem(selected)
        active = self.window.graph_documents._active_key
        self.window.project_search.setText(str(paths[1].parent))
        self.assertIs(tree.currentItem(), selected)
        self.assertTrue(selected.isSelected())
        self.assertTrue(selected.isHidden())
        shown = [item for item in self.window._navigator_items() if not item.isHidden()]
        self.assertTrue(any(str(paths[1]) in item.toolTip(0) for item in shown))
        self.assertEqual(self.window.graph_documents._active_key, active)
        self.window.project_search.clear()
        self.assertTrue(all(not item.isHidden() for item in self.window._navigator_items()))
        self.assertIs(tree.currentItem(), selected)
        self.window.project_search.setText('Original')
        self.assertFalse(selected.isHidden())
        ancestor = selected.parent()
        while ancestor is not None:
            self.assertFalse(ancestor.isHidden())
            ancestor = ancestor.parent()

    def test_search_covers_workbook_sheet_heatmap_and_xy_names(self):
        source = self.folder / 'table.csv'
        source.write_text('X,Y\n0,1\n1,2\n', encoding='utf-8')
        book = self.window.import_xy_files([source])
        book.name = 'Spectral workbook'
        sheet = self.window.workspace.sheets[book.sheet_ids[0]]
        sheet.name = 'Input samples'
        page = self.window._add_sheet_comparison(sheet.sheet_id)
        page.name_edit.setText('Overlay result')
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
                self.window.project_search.setText(text)
                item = next(item for item in self.window._navigator_items() if item.data(0, Qt.UserRole) == key)
                while item is not None:
                    self.assertFalse(item.isHidden())
                    item = item.parent()
