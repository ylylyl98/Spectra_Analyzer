"""XY modal commands through actual Quick controls and canonical document state."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import shiboken6
from PySide6.QtCore import QEvent, QPointF, QTimer, Qt
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from spectra_app.dataset_state import Workspace
from spectra_app.project_io import workspace_digest
from spectra_app.quick_dialog import QuickDialog
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_plot_page import XYPlotPage
from tests.xy_helpers import select_xy_rows, set_xy_draft

APP = QApplication.instance() or QApplication([])


def quick_item(dialog, name):
    pending = [dialog.rootObject()]
    while pending:
        item = pending.pop()
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(f'No Quick control {name}')


def click_prompt(dialog, name):
    item = quick_item(dialog, name)
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
    APP.processEvents()
    return item


def run_quick_prompt(page, command, *, mode='color', text=None, accept=True,
                     mutate=None, default=False, expected_initial=None):
    """Run real modal UI with a bounded callback; propagate callback assertions."""
    errors, seen = [], []
    prompt_owner = page.controller.prompt_window()
    active_dialog = None
    watchdog = QTimer()
    watchdog.setSingleShot(True)

    def owned_dialogs():
        return prompt_owner.findChildren(QuickDialog) if shiboken6.isValid(prompt_owner) else []

    def reject_prompt():
        dialogs = ([active_dialog] if active_dialog is not None and shiboken6.isValid(active_dialog)
                   else owned_dialogs())
        for dialog in dialogs:
            dialog.reject()

    def expire():
        errors.append(AssertionError('Quick prompt did not finish within 3 seconds'))
        reject_prompt()
    watchdog.timeout.connect(expire)

    def interact():
        nonlocal active_dialog
        try:
            dialogs = [dialog for dialog in owned_dialogs()
                       if dialog.window().isVisible()]
            assert len(dialogs) == 1, f'Expected one visible Quick prompt, got {len(dialogs)}'
            dialog = active_dialog = dialogs[0]
            assert isinstance(dialog.window(), QQuickView)
            assert dialog.window().status() == QQuickView.Ready
            assert not dialog.findChildren(QWidget)
            QTest.qWait(35)
            prefix = 'curveColor' if mode == 'color' else 'commonPrompt'
            if expected_initial is not None:
                assert quick_item(dialog, prefix + 'Editor').property('text') == expected_initial
            if text is not None:
                click_prompt(dialog, prefix + 'Editor')
                QTest.keyClick(dialog.window(), Qt.Key_A, Qt.ControlModifier)
                for char in text:
                    QTest.keyClick(dialog.window(), char)
                APP.processEvents()
                assert quick_item(dialog, prefix + 'Editor').property('text') == text
            seen.append(dialog.window().title())
            if mutate is not None:
                mutate(dialog)
            if shiboken6.isValid(dialog):
                if default:
                    cancel = quick_item(dialog, prefix + 'Action_cancel')
                    assert cancel.hasActiveFocus()
                    assert cancel.property('primary')
                    QTest.keyClick(dialog.window(), Qt.Key_Return)
                else:
                    action = 'yes' if mode == 'message' and accept else ('ok' if accept else 'cancel')
                    click_prompt(dialog, prefix + 'Action_' + action)
        except BaseException as exc:
            errors.append(exc)
            reject_prompt()

    callback = QTimer()
    callback.setSingleShot(True)
    callback.timeout.connect(interact)
    callback.start(0)
    watchdog.start(3000)
    try:
        command()
    finally:
        watchdog.stop()
        callback.stop()
    if errors:
        raise errors[0]
    assert len(seen) == 1, 'The command never opened its Quick prompt'
    return seen[0]


class CurvePromptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        self.book = create_workbook(self.workspace, 'Experiment')
        self.sheet = register_xy_sheet(self.workspace, self.book.workbook_id, 'Source',
            np.array([[0., 1., 3.], [1., 2., 2.], [2., 3., 1.]]), ['Time', 'A', 'B'])
        self.graph = create_comparison(self.workspace, 'Comparison')
        self.curves = [add_curve(self.workspace, self.graph.comparison_id, self.sheet.sheet_id,
                      0, column, label=f'Curve {index}') for index, column in enumerate((1, 2, 1))]
        self.page = XYPlotPage(self.workspace, self.graph.comparison_id)
        self.host = None
        self.addCleanup(self.dispose)
        self.page.resize(1000, 750)
        self.page.show()
        APP.processEvents()
        select_xy_rows(self.page, [0, 2], current=2)

    def dispose(self):
        if shiboken6.isValid(self.page):
            self.page.close()
            self.page.dispose()
            self.page.deleteLater()
        if self.host is not None and shiboken6.isValid(self.host):
            self.host.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def prompt(self, command='batchColor', **kwargs):
        return run_quick_prompt(self.page, lambda: self.page.inspector_bridge.action(command), **kwargs)

    def history_count(self):
        return len(self.page._edit_history.undo_stack)

    def test_prompt_callback_failure_and_timeout_reject_without_edit(self):
        values = [curve.color for curve in self.curves]

        def fail(_dialog):
            raise AssertionError('injected callback failure')

        with self.assertRaisesRegex(AssertionError, 'injected callback failure'):
            self.prompt(mutate=fail)
        with patch(__name__ + '.click_prompt'):
            with self.assertRaisesRegex(AssertionError, 'did not finish within 3 seconds'):
                self.prompt()
        self.assertEqual([curve.color for curve in self.curves], values)
        self.assertEqual(self.history_count(), 0)
        self.assertFalse(any(dialog.window().isVisible()
                             for dialog in self.page.controller.prompt_window().findChildren(QuickDialog)))

    def test_current_color_targets_current_only_and_one_appearance_edit(self):
        before = [(curve.color, curve.width, curve.label, curve.y_offset) for curve in self.curves]
        data = self.sheet.data.tobytes()
        selected = self.page._selected_ids()
        self.prompt('color', text='navy')
        self.assertEqual([curve.color for curve in self.curves], [before[0][0], before[1][0], '#000080'])
        self.assertEqual(self.page._selected_ids(), selected)
        self.assertEqual(self.page._active_curve_id, self.curves[2].curve_id)
        self.assertEqual(self.history_count(), 1)
        self.assertTrue(self.page._edit_history.undo_stack[-1].appearance_only)
        self.assertEqual(self.sheet.data.tobytes(), data)
        self.assertEqual([(c.width, c.label, c.y_offset) for c in self.curves], [row[1:] for row in before])
        self.assertTrue(self.page.undo())
        self.assertEqual([c.color for c in self.curves], [row[0] for row in before])
        self.assertTrue(self.page.redo())
        self.assertEqual(self.curves[2].color, '#000080')

    def test_color_and_width_reject_stale_modal_targets_without_history(self):
        original_data = self.sheet.data
        original_curve = self.curves[0]
        original_graph = self.graph

        # Exercise both top-level hiding and a canvas visibility roundtrip
        # inside a real outer host, as when switching document presentations.
        for command, mode, value in [('batchColor', 'color', '#8844aa'), ('batchWidth', 'float', '3.25')]:
            with self.subTest(case='hidden', command=command):
                self.page.show()
                values = [(c.color, c.width) for c in self.curves]
                history = self.history_count()
                self.prompt(command, mode=mode, text=value,
                            mutate=lambda dialog: dialog.finished.connect(lambda _result: self.page.hide()))
                self.assertFalse(self.page.isVisible())
                self.assertEqual([(c.color, c.width) for c in self.curves], values)
                self.assertEqual(self.history_count(), history)
        self.page.show()

        def activity_roundtrip(_dialog):
            self.page.canvas_view.hide()
            self.assertFalse(self.page.controller.presentation_eligible())
            self.page.canvas_view.show()
            self.assertTrue(self.page.controller.presentation_eligible())

        self.host = QWidget()
        self.page.setParent(self.host)
        self.host.show()
        self.page.show()
        self.page.refresh_input_state()
        mutations = [
            ('selection', lambda _d: select_xy_rows(self.page, [1])),
            ('curve', lambda _d: self.graph.curves.__setitem__(original_curve.curve_id, replace(original_curve))),
            ('source', lambda _d: setattr(self.sheet, 'data', original_data.copy())),
            ('comparison', lambda _d: self.workspace.comparisons.__setitem__(self.graph.comparison_id, replace(self.graph))),
            ('retired', lambda _d: self.page.command_bridge.retire()),
            ('source_roundtrip', lambda _d: self.source_roundtrip(original_data)),
            ('activity_roundtrip', activity_roundtrip),
        ]
        for name, mutation in mutations:
            for command, mode, value in [('batchColor', 'color', '#8844aa'), ('batchWidth', 'float', '3.25')]:
                with self.subTest(case=name, command=command):
                    self.workspace.comparisons[self.graph.comparison_id] = original_graph
                    self.graph.curves[original_curve.curve_id] = original_curve
                    self.sheet.data = original_data
                    self.page.show()
                    select_xy_rows(self.page, [0, 2], current=2)
                    self.page.refresh_input_state()
                    values = [(c.color, c.width) for c in self.curves]
                    history = self.history_count()
                    self.prompt(command, mode=mode, text=value, mutate=mutation)
                    self.assertEqual([(c.color, c.width) for c in self.curves], values)
                    self.assertEqual(self.history_count(), history)

    def source_roundtrip(self, original):
        self.sheet.data = original.copy()
        self.page.refresh_input_state()
        self.sheet.data = original
        self.page.refresh_input_state()

    def test_parent_deletion_during_color_prompt_returns_without_edit(self):
        values = [curve.color for curve in self.curves]
        history = self.page._edit_history

        def delete(_dialog):
            self.page.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)

        self.prompt(text='#8844aa', mutate=delete)
        self.assertFalse(shiboken6.isValid(self.page))
        self.assertEqual([curve.color for curve in self.curves], values)
        self.assertEqual(history.undo_stack, [])

    def test_input_guard_blocks_opening_and_rejects_accepted_modal_commands(self):
        self.page.set_input_guard(lambda: False)
        with patch('spectra_app.xy_plot_controller.prompt_color') as color, \
                patch('spectra_app.xy_plot_controller.prompt_float') as width, \
                patch('spectra_app.xy_plot_controller.FilePicker.getSaveFileName') as picker:
            self.page.inspector_bridge.action('color')
            self.page.inspector_bridge.action('batchColor')
            self.page.inspector_bridge.action('batchWidth')
            self.page.controller._export_dialog('svg')
            color.assert_not_called()
            width.assert_not_called()
            picker.assert_not_called()
        values = [(curve.color, curve.width) for curve in self.curves]
        for command, mode, value in [('batchColor', 'color', '#8844aa'), ('batchWidth', 'float', '3.25')]:
            with self.subTest(command=command):
                self.page.set_input_guard(lambda: True)
                self.prompt(command, mode=mode, text=value,
                            mutate=lambda _d: self.page.set_input_guard(lambda: False))
                self.assertEqual([(curve.color, curve.width) for curve in self.curves], values)
        with TemporaryDirectory() as folder:
            target = Path(folder) / 'figure.svg'
            target.write_bytes(b'preserve existing')
            self.page.set_input_guard(lambda: True)
            with patch('spectra_app.xy_plot_controller.FilePicker.getSaveFileName', return_value=(str(target), '')):
                run_quick_prompt(self.page, lambda: self.page.controller._export_dialog('svg'), mode='message',
                                 mutate=lambda _d: self.page.set_input_guard(lambda: False))
            self.assertEqual(target.read_bytes(), b'preserve existing')
        self.assertEqual(self.history_count(), 0)

    def test_export_cancel_suffix_overwrite_default_and_accept_preserve_document(self):
        original = workspace_digest(self.workspace)
        with TemporaryDirectory() as folder:
            target = Path(folder) / 'figure.unusual.svg'
            with patch('spectra_app.xy_plot_controller.FilePicker.getSaveFileName', return_value=('', '')):
                self.page.controller._export_dialog('svg')
            self.assertEqual(list(Path(folder).iterdir()), [])
            with patch('spectra_app.xy_plot_controller.FilePicker.getSaveFileName', return_value=(str(target.with_suffix('')), '')):
                self.page.controller._export_dialog('svg')
            self.assertTrue(target.exists())
            self.assertIn('<svg', target.read_text(encoding='utf8'))
            target.write_bytes(b'original bytes')
            with patch('spectra_app.xy_plot_controller.FilePicker.getSaveFileName', return_value=(str(target), '')):
                run_quick_prompt(self.page, lambda: self.page.controller._export_dialog('svg'), mode='message', default=True)
                self.assertEqual(target.read_bytes(), b'original bytes')
                run_quick_prompt(self.page, lambda: self.page.controller._export_dialog('svg'), mode='message', accept=False)
                self.assertEqual(target.read_bytes(), b'original bytes')
                run_quick_prompt(self.page, lambda: self.page.controller._export_dialog('svg'), mode='message')
                self.assertIn(b'<svg', target.read_bytes())
        self.assertEqual(workspace_digest(self.workspace), original)
        self.assertEqual(self.history_count(), 0)

    def test_export_preflight_stale_picker_confirmation_and_exclusive_create_race(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / 'figure.svg'
            set_xy_draft(self.page, 'color', 'invalid')
            with patch('spectra_app.xy_plot_controller.FilePicker.getSaveFileName') as picker:
                self.page.controller._export_dialog('svg')
                picker.assert_not_called()
            self.assertFalse(target.exists())
            self.page.revert_editor()

            def stale_picker(*_args):
                self.page.command_bridge.retire()
                return str(target), ''
            with patch('spectra_app.xy_plot_controller.FilePicker.getSaveFileName', side_effect=stale_picker):
                self.page.controller._export_dialog('svg')
            self.assertFalse(target.exists())
            target.write_bytes(b'preserve existing')
            with patch('spectra_app.xy_plot_controller.FilePicker.getSaveFileName', return_value=(str(target), '')):
                run_quick_prompt(self.page, lambda: self.page.controller._export_dialog('svg'), mode='message',
                                 mutate=lambda _d: self.page.command_bridge.retire())
            self.assertEqual(target.read_bytes(), b'preserve existing')
            target.unlink()
            original_svg = self.page.controller.svg_bytes

            def racing_svg():
                target.write_bytes(b'racing writer')
                return original_svg()
            with patch('spectra_app.xy_plot_controller.FilePicker.getSaveFileName', return_value=(str(target), '')), \
                    patch.object(self.page.controller, 'svg_bytes', side_effect=racing_svg):
                self.page.controller._export_dialog('svg')
            self.assertEqual(target.read_bytes(), b'racing writer')
            self.assertNotIn('Exported', self.page.inspector_bridge.message)
            self.assertTrue(self.page.inspector_bridge.message)
        self.assertEqual(self.history_count(), 0)


if __name__ == '__main__':
    unittest.main()
