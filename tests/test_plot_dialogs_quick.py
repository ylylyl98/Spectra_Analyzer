"""Real Quick window paths and isolated state for format/export decisions."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QObject, QPointF, Qt, QTimer, QEvent
from PySide6.QtGui import QFont
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from spectra_app.plot_dialogs import ApplyFormatDialog, BatchExportDialog, PlotTargetsDialog
from spectra_app.file_picker import FilePicker, _PickerRequest
from spectra_app.plot_dialog_bridge import apply_format
from spectra_app.plot_presentation import PlotAnnotation
from spectra_app.plot_templates import capture_format
from tests.settings_helpers import isolate_settings
from tests.test_plot_templates import dataset


APP = QApplication.instance() or QApplication([])


class QuickPlotDialogTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.a, self.b = dataset('a'), dataset('b')
        self.a.source_path = self.folder / 'left' / 'sample.dat'
        self.b.source_path = self.folder / 'right' / 'sample.dat'
        self.a.create_graph('Detail α')
        self.a.activate_graph('default')
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def show(self, dialog, size=(760, 560)):
        self.dialogs.append(dialog)
        dialog.resize(*size)
        dialog.show()
        QTest.qWait(60)
        self.assertIsInstance(dialog.window(), QQuickView)
        self.assertEqual(dialog.window().status(), QQuickView.Ready)
        self.assertIsNotNone(dialog.rootObject())
        return dialog

    def item(self, dialog, name):
        def find(root):
            if root.objectName() == name:
                return root
            for child in root.childItems():
                result = find(child)
                if result is not None:
                    return result
            return None
        item = find(dialog.rootObject())
        self.assertIsNotNone(item, name)
        return item

    def type_text(self, dialog, text):
        for char in text:
            QTest.keyClick(dialog.window(), char)

    def click(self, dialog, name):
        item = self.item(dialog, name)
        point = item.mapToScene(QPointF(item.property('width') / 2, item.property('height') / 2)).toPoint()
        QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
        APP.processEvents()
        return item

    def test_batch_actual_parent_child_mouse_keyboard_and_footer_gate(self):
        dialog = self.show(BatchExportDialog([self.a, self.b], 'a'), (760, 720))
        self.assertEqual(dialog.bridge.groups[0].checkState, Qt.PartiallyChecked.value)
        self.click(dialog, 'batchDataset_a')
        self.assertEqual(len(dialog.selected_graph_targets()), 2)
        child = self.item(dialog, 'batchGraph_a_default')
        child.forceActiveFocus()
        QTest.keyClick(dialog.window(), Qt.Key_Space)
        self.assertEqual(dialog.bridge.groups[0].checkState, Qt.PartiallyChecked.value)
        self.click(dialog, 'batchSelectAll')
        self.assertEqual(len(dialog.selected_graph_targets()), 3)
        self.click(dialog, 'batchClear')
        self.assertFalse(self.item(dialog, 'dialogAccept').property('enabled'))
        self.click(dialog, 'dialogAccept')
        self.assertEqual(dialog.result(), dialog.Rejected)
        self.assertTrue(dialog.window().isVisible())

    def test_batch_real_folder_editor_blocks_missing_path_and_cancel_creates_nothing(self):
        dialog = self.show(BatchExportDialog([self.a], 'a'), (760, 720))
        self.click(dialog, 'batchCommonFolder')
        self.assertFalse(self.item(dialog, 'dialogAccept').property('enabled'))
        folder = self.item(dialog, 'batchFolder')
        folder.forceActiveFocus()
        missing = self.folder / 'not-created'
        self.type_text(dialog, str(missing))
        self.assertEqual(dialog.destination(), str(missing))
        self.assertFalse(dialog.bridge.canAccept)
        self.assertFalse(missing.exists())
        QTest.keyClick(dialog.window(), Qt.Key_A, Qt.ControlModifier)
        self.type_text(dialog, str(self.folder))
        self.assertTrue(dialog.bridge.canAccept)
        self.assertTrue(self.item(dialog, 'dialogAccept').property('enabled'))
        formats = self.item(dialog, 'batchFormats')
        formats.forceActiveFocus()
        QTest.keyClick(dialog.window(), Qt.Key_End)
        self.assertEqual(dialog.formats(), ('png',))
        self.click(dialog, 'dialogCancel')
        self.assertEqual(dialog.result(), dialog.Rejected)
        self.assertFalse(missing.exists())

    def test_batch_browse_uses_its_own_quick_window_and_preserves_cancelled_folder(self):
        owner = QObject()
        self.addCleanup(owner.deleteLater)
        dialog = self.show(BatchExportDialog([self.a], 'a', owner), (760, 720))
        original = dialog.bridge.destinationFolder
        for chosen in (str(self.folder), ''):
            with self.subTest(chosen=chosen), patch(
                    'spectra_app.plot_dialogs.FilePicker.getExistingDirectory',
                    return_value=chosen) as picker:
                before = dialog.bridge.destinationFolder
                dialog.bridge.browseRequested.emit()
                picker.assert_called_once_with(dialog, 'Batch output folder', before)
                self.assertIs(picker.call_args.args[0].window(), dialog.window())
                self.assertEqual(dialog.bridge.destinationFolder, chosen or before)
        self.assertNotEqual(original, dialog.bridge.destinationFolder)

    def test_picker_preserves_existing_visible_widget_host_during_migration(self):
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        owner.show()
        APP.processEvents()
        windows = set(APP.allWindows())
        widgets = set(APP.allWidgets())
        def reject(request):
            self.assertIs(request.dialog.property('parentWindow'), owner.windowHandle())
            request.dialog.rejected.emit()
        with patch.object(_PickerRequest, '_open', reject):
            self.assertEqual(FilePicker.getOpenFileName(owner), ('', ''))
        self.assertEqual(set(APP.allWindows()), windows)
        self.assertEqual(set(APP.allWidgets()), widgets)

    def test_apply_real_presets_targets_and_render_preview_leave_sources_unchanged(self):
        self.a.presentation = replace(self.a.presentation, width_in=4.5, text_pt=15,
            frame_width_in=2.5, frame_height_in=1.5,
            annotations=(PlotAnnotation('source-note', text='Source α', position=(.2, .7)),))
        self.b.presentation = replace(self.b.presentation,
            annotations=(PlotAnnotation('target-note', text='Target β', position=(.8, .2)),))
        template = capture_format(self.a, 'Publication')
        before = [(deepcopy(s.view), s.presentation, s.current_cube, s.pending, s.current_cube.z.copy())
                  for s in (self.a, self.b)]
        dialog = self.show(ApplyFormatDialog([self.a, self.b], 'b', template))
        self.click(dialog, 'plotTargetsAll')
        self.click(dialog, 'formatRanges')
        self.assertEqual(dialog.selected_ids(), ['a', 'b'])
        self.assertTrue(dialog.options().x_limits)
        self.assertTrue(dialog.options().y_limits)
        self.assertTrue(dialog.options().color_limits)
        dialog.bridge.render_preview()
        self.assertTrue(dialog.bridge.previewUrl.startswith('image://format-preview/'))
        self.assertFalse(dialog.bridge.image_provider.image.isNull())
        self.assertFalse(dialog.bridge.previewError)
        self.assertIn('Fixed', dialog.bridge.previewText)
        for session, (view, presentation, cube, pending, values) in zip((self.a, self.b), before):
            self.assertEqual(session.view, view)
            self.assertEqual(session.presentation, presentation)
            self.assertIs(session.current_cube, cube)
            self.assertIs(session.pending, pending)
            np.testing.assert_array_equal(cube.z, values)
        self.click(dialog, 'dialogCancel')
        self.assertFalse(dialog.bridge._preview_timer.isActive())
        revision = dialog.bridge._revision
        dialog.bridge.render_preview()
        self.assertEqual(dialog.bridge._revision, revision)

    def test_bulk_preset_and_all_selection_prepare_each_final_target_once(self):
        sessions = [dataset(f'target-{i}') for i in range(9)]
        dialog = ApplyFormatDialog(sessions, sessions[0].dataset_id, capture_format(sessions[0], 'Bulk'))
        self.dialogs.append(dialog)
        with patch('spectra_app.plot_dialog_bridge.apply_format', wraps=apply_format) as apply:
            dialog.bridge.selectAll(True)
            self.assertEqual(apply.call_count, 9)
            apply.reset_mock()
            dialog.bridge.quickOptions(True)
            self.assertEqual(apply.call_count, 9)
            apply.reset_mock()
            dialog.bridge.selectAll(False)
            self.assertEqual(apply.call_count, 0)
            self.assertFalse(dialog.bridge.canAccept)

    def test_modal_results_owner_escape_window_close_and_synchronous_show_finish_once(self):
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        dialog = self.show(PlotTargetsDialog([self.a], 'a', owner))
        self.assertIs(dialog.window().transientParent(), owner.windowHandle())
        results, accepted, rejected = [], [], []
        dialog.finished.connect(results.append)
        dialog.accepted.connect(lambda: accepted.append(True))
        dialog.rejected.connect(lambda: rejected.append(True))
        QTest.keyClick(dialog.window(), Qt.Key_Escape)
        dialog.reject()
        dialog.accept()
        self.assertEqual((results, accepted, rejected), ([0], [], [True]))
        closed = self.show(PlotTargetsDialog([self.a], 'a'))
        close_results = []
        closed.finished.connect(close_results.append)
        closed.window().close()
        self.assertEqual(close_results, [0])
        modal = PlotTargetsDialog([self.a], 'a')
        self.dialogs.append(modal)
        QTimer.singleShot(0, modal.accept)
        self.assertEqual(modal.exec(), 1)
        modal.accept()
        modal.reject()
        self.assertEqual(modal.result(), 1)
        immediate = PlotTargetsDialog([self.a], 'a')
        self.dialogs.append(immediate)
        with patch.object(immediate, 'show', side_effect=immediate.accept):
            self.assertEqual(immediate.exec(), 1)

    def test_narrow_layout_keeps_actions_visible_and_application_font(self):
        old_font = APP.font()
        self.addCleanup(APP.setFont, old_font)
        APP.setFont(QFont('Segoe UI', 13))
        dialog = self.show(ApplyFormatDialog([self.a], 'a', capture_format(self.a, 'Readable')), (420, 560))
        accept = self.item(dialog, 'dialogAccept')
        self.assertEqual(accept.property('font').pointSize(), 13)
        top = accept.mapToScene(QPointF(0, 0))
        self.assertGreaterEqual(top.y(), 0)
        self.assertLessEqual(top.y() + accept.height(), dialog.height())
        self.assertFalse(dialog.rootObject().property('wide'))


if __name__ == '__main__':
    unittest.main()
