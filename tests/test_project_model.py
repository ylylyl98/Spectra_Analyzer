"""Focused Project identity, selection, filtering and incremental-model proof."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from spectra_app.project_model import ProjectBridge, ProjectRow, row_key

APP = QApplication.instance() or QApplication([])


class Owner(QObject):
    _close_pending = False

    def _project_busy(self):
        return False

    def _navigator_selection_changed(self):
        pass

    def _open_navigator_item(self):
        self.opened = True


class ProjectModelTests(unittest.TestCase):
    def setUp(self):
        self.owner = Owner()
        self.project = ProjectBridge(self.owner, self.owner)
        self.book = ('workbook', 'book')
        self.group = ('group', ('worksheets', 'book', None))
        self.a, self.b = ('sheet', 'a'), ('sheet', 'b')
        self.rows = [ProjectRow(self.book, 'Alias\u2028exact source.csv'),
                     ProjectRow(self.group, 'Worksheets (2)', self.book),
                     ProjectRow(self.a, 'Alpha', self.group, '/run_a/exact source.csv'),
                     ProjectRow(self.b, 'Beta', self.group, '/run_b/exact source.csv')]
        self.project.set_rows(self.rows)

    def test_unchanged_and_active_only_projection_never_reset(self):
        reset = QSignalSpy(self.project.model.modelReset)
        changed = QSignalSpy(self.project.model.dataChanged)
        self.project.set_rows(self.rows)
        self.assertEqual(reset.count(), 0)
        self.assertEqual(changed.count(), 0)
        self.project.set_active({self.a})
        self.assertEqual(reset.count(), 0)
        self.assertEqual(changed.count(), 1)
        roles = changed.at(0)[2]
        names = {self.project.model.roleNames()[role].decode() for role in roles}
        self.assertEqual(names, {'active', 'description', 'tooltip'})
        self.project.select_key(self.b)
        self.assertEqual(reset.count(), 0)
        self.assertEqual(self.project.current_key(), self.b)

    def test_search_keeps_hidden_selection_and_restores_expansion(self):
        self.project.select_key(self.a)
        self.project.set_expanded(self.group, False)
        self.project.setSearchText('run_b')
        self.assertEqual(self.project.visible_keys(), [self.book, self.group, self.b])
        self.assertEqual(self.project.selected_keys(), [self.a])
        self.assertEqual(self.project.current_key(), self.a)
        self.project.set_expanded(self.group, False)
        self.assertEqual(self.project.visible_keys(), [self.book, self.group])
        self.project.select_key(self.book)
        self.assertEqual(self.project.visible_keys(), [self.book, self.group])
        self.project.setSearchText('Beta')
        self.assertIn(self.b, self.project.visible_keys())
        self.project.setSearchText('')
        self.assertFalse(self.project.is_expanded(self.group))
        self.assertEqual(self.project.visible_keys(), [self.book, self.group])
        self.project.setSearchText('exact source.csv')
        self.assertEqual(self.project.visible_keys(), [row.key for row in self.rows])

    def test_shift_ctrl_current_keyboard_and_context_preserve_identity(self):
        self.project.select_key(self.a)
        self.project.select_key(self.b, Qt.ShiftModifier)
        self.assertEqual(self.project.selected_keys(), [self.a, self.b])
        self.project.prepareContext(row_key(self.a))
        self.assertEqual(self.project.selected_keys(), [self.a, self.b])
        self.assertEqual(self.project.current_key(), self.a)
        self.project.navigate('down', Qt.ControlModifier.value)
        self.assertEqual(self.project.current_key(), self.b)
        self.assertEqual(self.project.selected_keys(), [self.a, self.b])
        self.project.select(row_key(self.b), Qt.ControlModifier.value)
        self.assertEqual(self.project.selected_keys(), [self.a])
        self.project.prepareContext(row_key(self.group))
        self.assertEqual(self.project.selected_keys(), [self.group])
        self.assertTrue(self.project.contextIsGroup)
        self.project.toggleContextGroup()
        self.assertFalse(self.project.is_expanded(self.group))

    def test_filtered_right_moves_to_first_visible_child(self):
        self.project.setSearchText('run_b')
        for modifiers in (0, Qt.ControlModifier.value):
            with self.subTest(modifiers=modifiers):
                self.project.select_key(self.group)
                reveal = QSignalSpy(self.project.revealRequested)
                self.project.navigate('right', modifiers)
                self.assertEqual(self.project.current_key(), self.b)
                self.assertEqual(self.project.currentIndex, 2)
                expected_selection = [self.group] if modifiers else [self.b]
                self.assertEqual(self.project.selected_keys(), expected_selection)
                self.assertEqual(reveal.count(), 1)
                self.assertEqual(reveal.at(0)[0], 2)

    def test_filtered_right_expands_group_before_moving_to_visible_child(self):
        self.project.setSearchText('run_b')
        self.project.set_expanded(self.group, False)
        self.project.select_key(self.group)
        self.project.navigate('right')
        self.assertTrue(self.project.is_expanded(self.group))
        self.assertEqual(self.project.current_key(), self.group)
        self.assertEqual(self.project.selected_keys(), [self.group])
        self.project.navigate('right')
        self.assertEqual(self.project.current_key(), self.b)

    def test_right_without_visible_children_preserves_current_and_selection(self):
        self.project.setSearchText('run_b')
        self.project.select_key(self.group)
        self.project.set_expanded(self.book, False)
        self.assertEqual(self.project.visible_keys(), [self.book])
        self.project.navigate('right')
        self.assertEqual(self.project.current_key(), self.group)
        self.assertEqual(self.project.selected_keys(), [self.group])

    def test_removed_rows_drop_selection_current_and_invalid_keys_are_inert(self):
        self.project.select_key(self.a)
        self.project.set_rows(self.rows[:2])
        self.assertEqual(self.project.selected_keys(), [])
        self.assertIsNone(self.project.current_key())
        self.project.select('["sheet","missing"]', 0)
        self.project.activate('malformed')
        self.assertIsNone(self.project.current_key())
        self.assertFalse(hasattr(self.owner, 'opened'))


if __name__ == '__main__':
    unittest.main()
