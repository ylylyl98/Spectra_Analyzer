"""Dialog choices describe and publish the same graph changes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from spectra_app.dataset_state import Workspace
from spectra_app.fitting import FitResult
from spectra_app.plot_page import PlotPage
from spectra_app.plot_presentation import HeatmapSnapshot, PlotAnnotation
from spectra_app.sheet_comparison_dialog import SheetComparisonDialog
from spectra_app.workbook_state import (add_curve, create_comparison, create_workbook,
                                        ensure_dataset_sheet, register_xy_sheet)
from tests.settings_helpers import isolate_settings
from tests.test_plot_templates import dataset

APP = QApplication.instance() or QApplication([])


class DialogUsabilityTests(unittest.TestCase):
    def setUp(self):
        isolate_settings(self)
        self.widgets = []
        self.addCleanup(self.dispose)

    def dispose(self):
        APP.clipboard().clear()
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def test_workbook_checkbox_represents_all_children_and_originals_is_explicit(self):
        workspace = Workspace()
        session = dataset('source')
        session.fit_result = FitResult('Gaussian', session.base_cube.energy,
            session.base_cube.z[0], np.array([session.base_cube.z[0]]),
            np.array([2.]), np.array([1.]), np.array([.3]), 0., 0., 1.)
        session.fit_gate_idx = 0
        workspace.datasets[session.dataset_id] = session
        original, processed, fit = [ensure_dataset_sheet(workspace, session.dataset_id, role,
            0 if role == 'fit_curves' else None) for role in ('original', 'processed', 'fit_curves')]
        dialog = self.keep(SheetComparisonDialog(workspace,
            selected_sheet_ids=[original.sheet_id, fit.sheet_id]))
        dialog.show()
        APP.processEvents()
        tree = dialog.source_tree
        parent = tree.topLevelItem(0)
        self.assertEqual(parent.checkState(0), Qt.PartiallyChecked)
        tree.setCurrentItem(parent)
        tree.setFocus()
        QTest.keyClick(tree, Qt.Key_Space)
        self.assertEqual(set(dialog.selected_mappings()), {original.sheet_id, processed.sheet_id, fit.sheet_id})
        self.assertEqual(parent.checkState(0), Qt.Checked)
        tree.setCurrentItem(dialog._items[processed.sheet_id])
        QTest.keyClick(tree, Qt.Key_Space)
        self.assertEqual(parent.checkState(0), Qt.PartiallyChecked)
        self.assertIn(fit.sheet_id, dialog.selected_mappings())
        tree.setCurrentItem(parent)
        QTest.keyClick(tree, Qt.Key_Space)
        QTest.keyClick(tree, Qt.Key_Space)
        self.assertFalse(dialog.selected_mappings())
        self.assertEqual(parent.checkState(0), Qt.Unchecked)
        dialog.select_originals_btn.click()
        self.assertEqual(list(dialog.selected_mappings()), [original.sheet_id])
        self.assertEqual(parent.checkState(0), Qt.PartiallyChecked)

    def test_duplicate_preview_reacts_to_destination_mapping_and_explicit_repeat(self):
        workspace = Workspace()
        book = create_workbook(workspace, 'Measurements')
        sheet = register_xy_sheet(workspace, book.workbook_id, 'Original',
            np.array([[1., 2., 3.], [2., 4., 6.]]), ['X', 'A', 'B'])
        target = create_comparison(workspace, 'Existing')
        empty = create_comparison(workspace, 'Empty')
        add_curve(workspace, target.comparison_id, sheet.sheet_id, 0, 1)
        dialog = self.keep(SheetComparisonDialog(workspace, selected_sheet_ids=[sheet.sheet_id]))
        dialog.destination_combo.setCurrentIndex(dialog.destination_combo.findData(target.comparison_id))
        self.assertIn('1 new', dialog.summary.text())
        self.assertIn('1 already present', dialog.summary.text())
        self.assertTrue(dialog.commit_btn.isEnabled())
        dialog.y_list.item(2).setCheckState(Qt.Unchecked)
        self.assertIn('0 new', dialog.summary.text())
        self.assertFalse(dialog.commit_btn.isEnabled())
        self.assertIn('Include existing curves', dialog.feedback.text())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Rejected)
        dialog.include_existing_chk.setChecked(True)
        self.assertTrue(dialog.commit_btn.isEnabled())
        self.assertIn('1 to add', dialog.summary.text())
        dialog.include_existing_chk.setChecked(False)
        dialog.destination_combo.setCurrentIndex(dialog.destination_combo.findData(empty.comparison_id))
        self.assertIn('1 new', dialog.summary.text())
        self.assertTrue(dialog.commit_btn.isEnabled())
        dialog.destination_combo.setCurrentIndex(0)
        self.assertEqual(dialog.commit_btn.text(), 'Create comparison')
        self.assertTrue(dialog.commit_btn.isEnabled())

    def test_preview_additions_match_published_graph_and_explicit_repeat(self):
        from spectra_app.main_window import MainWindow
        window = self.keep(MainWindow())
        book = create_workbook(window.workspace, 'Measurements')
        sheet = register_xy_sheet(window.workspace, book.workbook_id, 'Original',
            np.array([[1., 2., 3.], [2., 4., 6.]]), ['X', 'A', 'B'])
        target = create_comparison(window.workspace, 'Existing')
        existing = add_curve(window.workspace, target.comparison_id, sheet.sheet_id, 0, 1,
                             label='Preserve style', color='#123456', width=2.5)
        before = deepcopy(existing)
        graph = window.open_xy_comparison(target.comparison_id)
        dialog = self.keep(SheetComparisonDialog(window.workspace,
            selected_sheet_ids=[sheet.sheet_id], comparison_id=target.comparison_id))
        self.assertIn('1 new', dialog.summary.text())
        window.compare_sheets(dialog.selected_mappings(), target.comparison_id,
                              include_existing=dialog.include_existing_chk.isChecked())
        self.assertEqual(len(target.curves), 2)
        self.assertEqual(len(graph.axes.lines), 2)
        self.assertEqual(existing, before)
        dialog._validate()
        self.assertIn('0 new', dialog.summary.text())
        self.assertIn('2 already present', dialog.summary.text())
        self.assertFalse(dialog.commit_btn.isEnabled())
        dialog.include_existing_chk.setChecked(True)
        self.assertIn('2 to add', dialog.summary.text())
        window.compare_sheets(dialog.selected_mappings(), target.comparison_id,
                              include_existing=dialog.include_existing_chk.isChecked())
        self.assertEqual(len(target.curves), 4)
        self.assertEqual(len(graph.axes.lines), 4)
        self.assertEqual(existing, before)

    def test_annotation_choices_keep_serialized_spaces_and_location_until_apply(self):
        page = self.keep(PlotPage())
        item = PlotAnnotation('arrow', kind='arrow', text='Peak', position=(.25, .75),
                              target=(.6, .4), target_space='axes-fraction')
        page.set_presentation(replace(page.presentation, annotations=(item,)))
        page.preview.snapshot = HeatmapSnapshot([10., 20.], [1., 3.], [[1., 2.], [3., 4.]],
            x_label='Energy (eV)', y_label='Gate (V)')
        page.rendered_page = SimpleNamespace(axes_bounds=(.1, .1, .8, .8),
            display_x_limits=(10., 20.), display_y_limits=(1., 3.), figure_size_in=(6., 4.))
        page.preview.page = page.rendered_page
        page.select_annotation(item.id)
        before = page.preview.annotation_point(item)
        data_index = page.annotation_space.findData('data')
        self.assertGreaterEqual(data_index, 0)
        page.annotation_space.setCurrentIndex(data_index)
        page.annotation_target_space.setCurrentIndex(page.annotation_target_space.findData('data'))
        page.annotation_font_role.setCurrentIndex(page.annotation_font_role.findData('numeric'))
        self.assertEqual(page.presentation.annotations[0], item)
        self.assertIn('Energy (eV)', page.annotation_x.accessibleName())
        self.assertIn('Gate (V)', page.annotation_y.accessibleName())
        page.annotation_apply_btn.click()
        changed = page.presentation.annotations[0]
        self.assertEqual(changed.coordinate_space, 'data')
        self.assertEqual(changed.target_space, 'data')
        self.assertEqual(changed.font_role, 'numeric')
        self.assertEqual(changed.id, item.id)
        self.assertLessEqual((before - page.preview.annotation_point(changed)).manhattanLength(), 1)
        self.assertAlmostEqual(changed.position[0], 12.5)
        self.assertAlmostEqual(changed.target[0], 16.)
        text = replace(item, id='text', kind='text', target=None)
        page.set_presentation(replace(page.presentation, annotations=(text,)))
        self.assertFalse(page.annotation_target_space.isEnabled())
        self.assertFalse(page.annotation_target_x.isEnabled())


if __name__ == '__main__':
    unittest.main()
