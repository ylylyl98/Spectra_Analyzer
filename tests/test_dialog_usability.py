"""Dialog choices describe and publish the same graph changes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from dataclasses import replace
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QAccessible
from spectra_app.dataset_state import Workspace
from spectra_app.fitting import FitResult
from spectra_app.plot_presentation import PlotAnnotation
from spectra_app.sheet_comparison_dialog import SheetComparisonDialog
from spectra_app.workbook_state import (add_curve, create_comparison, create_workbook,
                                        ensure_dataset_sheet, register_xy_sheet)
from tests.test_plot_templates import dataset
from tests.quick_migration_case import QuickMigrationCase
from tests.formal_quick_helpers import click_item, item as quick_item, pump


def sheet_control(dialog, name):
    return quick_item(dialog, name)


def settle_sheet(dialog):
    dialog.window().update()
    pump()


def click_sheet_control(dialog, name):
    click_item(dialog, name)
    settle_sheet(dialog)


def set_sheet_destination(dialog, comparison_id):
    dialog.bridge.setDestination(next(i for i, row in enumerate(dialog.bridge.destinations)
                                      if row['value'] == (comparison_id or '')))


class DialogUsabilityTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.widgets = []
        self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))
        self.addCleanup(self.dispose)

    def dispose(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()

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
        pump()
        source = sheet_control(dialog, 'sheetSources')
        parent_id = dialog.bridge.sourceIdAt(0)
        parent = dialog.bridge._source_model.rows[0]
        self.assertEqual(parent['checkState'], 1)
        dialog.bridge.selectSource(parent_id)
        source.forceActiveFocus()
        QTest.keyClick(dialog.window(), Qt.Key_Space)
        self.assertEqual(set(dialog.selected_mappings()), {original.sheet_id, processed.sheet_id, fit.sheet_id})
        self.assertEqual(parent['checkState'], 2)
        dialog.bridge.selectSource(processed.sheet_id)
        QTest.keyClick(dialog.window(), Qt.Key_Space)
        self.assertEqual(parent['checkState'], 1)
        self.assertIn(fit.sheet_id, dialog.selected_mappings())
        dialog.bridge.selectSource(parent_id)
        QTest.keyClick(dialog.window(), Qt.Key_Space)
        QTest.keyClick(dialog.window(), Qt.Key_Space)
        self.assertFalse(dialog.selected_mappings())
        self.assertEqual(parent['checkState'], 0)
        click_sheet_control(dialog, 'sheetOriginals')
        self.assertEqual(list(dialog.selected_mappings()), [original.sheet_id])
        self.assertEqual(parent['checkState'], 1)

    def test_duplicate_preview_reacts_to_destination_mapping_and_explicit_repeat(self):
        workspace = Workspace()
        book = create_workbook(workspace, 'Measurements')
        sheet = register_xy_sheet(workspace, book.workbook_id, 'Original',
            np.array([[1., 2., 3.], [2., 4., 6.]]), ['X', 'A', 'B'])
        target = create_comparison(workspace, 'Existing')
        empty = create_comparison(workspace, 'Empty')
        add_curve(workspace, target.comparison_id, sheet.sheet_id, 0, 1)
        dialog = self.keep(SheetComparisonDialog(workspace, selected_sheet_ids=[sheet.sheet_id]))
        set_sheet_destination(dialog, target.comparison_id)
        self.assertIn('1 new', dialog.bridge.summary)
        self.assertIn('1 already present', dialog.bridge.summary)
        self.assertTrue(dialog.bridge.canAccept)
        dialog.bridge.setY(2, False)
        self.assertIn('0 new', dialog.bridge.summary)
        self.assertFalse(dialog.bridge.canAccept)
        self.assertIn('Include existing curves', dialog.bridge.feedback)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.Rejected)
        dialog.bridge.setIncludeExisting(True)
        self.assertTrue(dialog.bridge.canAccept)
        self.assertIn('1 to add', dialog.bridge.summary)
        dialog.bridge.setIncludeExisting(False)
        set_sheet_destination(dialog, empty.comparison_id)
        self.assertIn('1 new', dialog.bridge.summary)
        self.assertTrue(dialog.bridge.canAccept)
        dialog.bridge.setDestination(0)
        self.assertEqual(dialog.bridge.actionText, 'Create comparison')
        self.assertTrue(dialog.bridge.canAccept)

    def test_preview_additions_match_published_graph_and_explicit_repeat(self):
        window = self.controller
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
        self.assertIn('1 new', dialog.bridge.summary)
        window.compare_sheets(dialog.selected_mappings(), target.comparison_id,
                              include_existing=dialog.include_existing)
        self.assertEqual(len(target.curves), 2)
        self.assertEqual(len(graph.axes.lines), 2)
        self.assertEqual(existing, before)
        dialog.bridge.validate()
        self.assertIn('0 new', dialog.bridge.summary)
        self.assertIn('2 already present', dialog.bridge.summary)
        self.assertFalse(dialog.bridge.canAccept)
        dialog.bridge.setIncludeExisting(True)
        self.assertIn('2 to add', dialog.bridge.summary)
        window.compare_sheets(dialog.selected_mappings(), target.comparison_id,
                              include_existing=dialog.include_existing)
        self.assertEqual(len(target.curves), 4)
        self.assertEqual(len(graph.axes.lines), 4)
        self.assertEqual(existing, before)

    def test_annotation_choices_keep_serialized_spaces_and_location_until_apply(self):
        window = self.controller
        path = self.folder / 'annotation.dat'
        path.write_text('Energy(eV) 1 3\n10 1 3\n20 2 4\n')
        window._load_dat_file_direct(path)
        page = window.open_graph_document()
        self.assertTrue(window.request_task(2))
        pump()
        item = PlotAnnotation('arrow', kind='arrow', text='Peak', position=(.25, .75),
                              target=(.6, .4), target_space='axes-fraction')
        session = window.workspace.active
        session.view.x_limits = (10., 20.)
        session.view.y_limits = (1., 3.)
        session.view.annotations.update(y_axis_name='Gate', y_axis_unit='V')
        window.set_plot_presentation(replace(page.presentation, annotations=(item,)))
        window._render_plot_page()
        self.assertIsNotNone(page.rendered_page)
        self.assertEqual(page.expected_owner, window._plot_owner_token(window._document_key()))
        self.assertEqual(page.preview.snapshot.x_limits, (10., 20.))
        self.assertEqual(page.preview.snapshot.y_limits, (1., 3.))
        self.assertIn('Energy (eV)', page.preview.snapshot.x_label)
        self.assertEqual(page.preview.snapshot.y_label, 'Gate (V)')
        page.select_annotation(item.id)
        pump()
        before = page.preview.annotation_point(item)
        source = quick_item(window, 'formalProperties')
        for field, value in (('annotation_coordinate_space', 'data'),
                             ('annotation_target_space', 'data'),
                             ('annotation_font_role', 'numeric')):
            token = page.formal.capturePropertiesInput(source)
            self.assertTrue(token)
            page.formal.setValue(field, value, source, token)
        self.assertEqual(page.presentation.annotations[0], item)
        page.formal.focusRequested.emit('annotations', 'annotation_x')
        pump(60)
        for field, unit in (('annotation_x', 'Energy (eV)'), ('annotation_y', 'Gate (V)')):
            accessible = QAccessible.queryAccessibleInterface(quick_item(quick_item(window, 'formalProperties'), field))
            self.assertIsNotNone(accessible)
            self.assertIn(unit, accessible.text(QAccessible.Name))
        click_item(window, 'annotation_apply')
        changed = page.presentation.annotations[0]
        self.assertEqual(changed.coordinate_space, 'data')
        self.assertEqual(changed.target_space, 'data')
        self.assertEqual(changed.font_role, 'numeric')
        self.assertEqual(changed.id, item.id)
        self.assertLessEqual((before - page.preview.annotation_point(changed)).manhattanLength(), 1)
        self.assertAlmostEqual(changed.position[0], 12.5)
        self.assertAlmostEqual(changed.target[0], 16.)
        text = replace(item, id='text', kind='text', target=None)
        window.set_plot_presentation(replace(page.presentation, annotations=(text,)))
        page.select_annotation(text.id)
        pump()
        self.assertFalse(quick_item(quick_item(window, 'formalProperties'), 'annotation_target_space').isVisible())
        self.assertFalse(quick_item(quick_item(window, 'formalProperties'), 'annotation_target_x').isVisible())


if __name__ == '__main__':
    import unittest
    unittest.main()
