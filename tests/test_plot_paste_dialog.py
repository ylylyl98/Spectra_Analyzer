import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from dataclasses import replace
import unittest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickView
from PySide6.QtWidgets import QApplication
from spectra_app.plot_dialogs import ApplyFormatDialog
from spectra_app.plot_templates import capture_format, FormatApplicationOptions
from spectra_app.plot_presentation import PlotAnnotation
from spectra_app.formal_plot_controller import FormalPlotController
from tests.test_plot_templates import dataset
APP = QApplication.instance() or QApplication([])
class PasteDialogTests(unittest.TestCase):
    def test_exact_targets_baselines_and_intent_load_in_qml(self):
        a = dataset('a')
        a.active_graph.published = True
        other = a.create_graph('Other')
        a.activate_graph('default')
        targets = [('a', 'default'), ('a', other.graph_id)]
        baseline = deepcopy(a)
        baseline.presentation = replace(baseline.presentation, numeric_pt=11)
        a.presentation = replace(a.presentation, numeric_pt=22)
        template = replace(capture_format(a, 'Source'), x_limits=None)
        dialog = ApplyFormatDialog([a], 'a', template, graph_targets=targets,
            selected_targets=targets, baseline_sessions={targets[0]: baseline},
            initial_options=FormatApplicationOptions(x_limits=True, typography=False), adjustment=True)
        try:
            dialog.show()
            APP.processEvents()
            self.assertEqual(dialog.window().status(), QQuickView.Ready)
            self.assertEqual(dialog.selected_graph_targets(), targets)
            self.assertEqual(dialog.bridge.selectedCount, 2)
            self.assertTrue(dialog.preference_options().x_limits)
            self.assertFalse(dialog.options().x_limits)
            self.assertEqual(dialog.bridge._candidates[0].presentation.numeric_pt, 11)
            dialog.bridge.selectAll(False)
            self.assertEqual(dialog.selected_graph_targets(), targets)
        finally:
            dialog.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()

    def item(self, dialog, name):
        def find(root):
            if root.objectName() == name:
                return root
            for child in root.childItems():
                found = find(child)
                if found is not None:
                    return found
        result = find(dialog.rootObject())
        self.assertIsNotNone(result, name)
        return result

    def click(self, dialog, name):
        item = self.item(dialog, name)
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
        QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=point)
        APP.processEvents()

    def test_grouped_picker_exact_pairs_and_cancel(self):
        a, b = dataset('a'), dataset('b')
        a.active_graph.published = b.active_graph.published = True
        a.presentation = replace(a.presentation, annotations=(PlotAnnotation('original', text='Original'),))
        other = a.create_graph('Detail')
        other.presentation = replace(other.presentation, annotations=(PlotAnnotation('detail', text='Detail'),))
        a.activate_graph('default')
        source = deepcopy(a)
        source.presentation = replace(source.presentation, annotations=(PlotAnnotation('source', text='Copied'),))
        targets = [('a', 'default'), ('a', other.graph_id), ('b', 'default')]
        dialog = ApplyFormatDialog([a, b], 'a', capture_format(source, 'Copied format'),
            graph_targets=targets, initial_options=FormatApplicationOptions(annotation_positions=True),
            initial_annotation_pairs={targets[0]: {'source': 'original'}, targets[1]: {'source': 'detail'}})
        try:
            dialog.resize(940, 780)
            dialog.show()
            QTest.qWait(100)
            self.assertEqual(dialog.window().status(), QQuickView.Ready)
            self.click(dialog, 'formatDataset_a')
            self.assertEqual(dialog.selected_graph_targets(), targets[:2])
            self.click(dialog, 'formatGraph_b_default')
            self.assertEqual(dialog.selected_graph_targets(), targets)
            self.assertEqual(dialog.annotation_pairs(), {targets[0]: {'source': 'original'}, targets[1]: {'source': 'detail'}})
            self.assertEqual(a.active_graph_id, 'default')
            self.assertEqual(dialog.bridge.selectedCount, 3)
            image = dialog.window().grabWindow()
            image.save('C:/Users/yanli/Documents/Codex/ScientificAnalysis/artifacts/paste-format-2026-10-28/batch-dialog.png')
            self.click(dialog, 'dialogCancel')
            self.assertEqual(dialog.result(), dialog.Rejected)
            self.assertEqual(a.presentation.annotations[0].id, 'original')
        finally:
            dialog.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()

    def test_formal_controller_routes_new_commands_and_availability(self):
        controller = FormalPlotController()
        calls = []
        routes = [('settings_paste_to', controller.settingsPasteToRequested),
                  ('settings_paste_options', controller.settingsPasteOptionsRequested),
                  ('settings_adjust', controller.settingsAdjustRequested),
                  ('settings_undo_paste', controller.settingsUndoPasteRequested)]
        try:
            controller.set_transfer_availability(True, 'Source')
            self.assertTrue(controller.command_enabled('settings_paste_to'))
            self.assertTrue(controller.command_enabled('settings_paste_options'))
            self.assertFalse(controller.command_enabled('settings_adjust'))
            for key, signal in routes:
                signal.connect(lambda key=key: calls.append(key))
                controller.set_command_availability(key, True)
                controller.formal.command(key)
            self.assertEqual(calls, [key for key, _ in routes])
            self.assertTrue(controller.preview.formatCommands['settings_adjust'])
            controller.set_command_availability('settings_adjust', False)
            controller.formal.command('settings_adjust')
            self.assertEqual(len(calls), 4)
        finally:
            controller.dispose()
            controller.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_baseline_annotations_and_page_signal_forwarding(self):
        from spectra_app.plot_page import PlotPage
        a = dataset('a')
        a.active_graph.published = True
        original = deepcopy(a)
        original.presentation = replace(original.presentation, annotations=(PlotAnnotation('baseline', text='Original'),))
        a.presentation = replace(a.presentation, annotations=(PlotAnnotation('newer', text='Live'),))
        source = deepcopy(a)
        source.presentation = replace(source.presentation, annotations=(PlotAnnotation('source', text='Source'),))
        key = ('a', 'default')
        dialog = ApplyFormatDialog([a], 'a', capture_format(source, 'Frozen'), graph_targets=[key],
            baseline_sessions={key: original}, adjustment=True,
            initial_options=FormatApplicationOptions(annotation_positions=True),
            initial_annotation_pairs={key: {'source': 'baseline'}})
        page = PlotPage()
        try:
            self.assertEqual(dialog.annotation_pairs(), {key: {'source': 'baseline'}})
            self.assertEqual(dialog.bridge.pairs[0].target_ids, [None, 'baseline'])
            calls = []
            page.settingsPasteToRequested.connect(lambda: calls.append('to'))
            page.settingsPasteOptionsRequested.connect(lambda: calls.append('options'))
            page.settingsAdjustRequested.connect(lambda: calls.append('adjust'))
            page.settingsUndoPasteRequested.connect(lambda: calls.append('undo'))
            for key in ('settings_paste_to', 'settings_paste_options', 'settings_adjust', 'settings_undo_paste'):
                page.set_command_availability(key, True)
                page.formal.command(key)
            self.assertEqual(calls, ['to', 'options', 'adjust', 'undo'])
            page.controller.preview.set_format_commands({'settings_paste': {'enabled': True}})
            self.assertTrue(page.controller.preview.formatCommands['settings_paste'])
            self.assertFalse(page.controller.preview.formatCommands['settings_adjust'])
        finally:
            page.dispose()
            page.deleteLater()
            dialog.deleteLater()
            APP.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()
