"""Results snapshot fidelity and atomic four-layer Style edit contracts."""
import csv
from copy import deepcopy
from dataclasses import replace
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QPointF, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
from spectra_app.analysis_document import AnalysisSeed, analysis_parameter_rows
from spectra_app.analysis_results_bridge import AnalysisResultsBridge, AnalysisStyleBridge
from tests import test_analysis_workbench_bridge as unit_fixture
from tests import test_analysis_workbench as production
from tests import analysis_quick_helpers as aq
from tests.settings_helpers import wait_for_background_jobs
from tests.quick_migration_case import QuickMigrationCase

LAYERS = ['detected-peak', 'detected-dip', 'fitted-peak', 'fitted-dip']


class AnalysisResultsBridgeTests(unittest.TestCase):
    setUp = unit_fixture.AnalysisWorkbenchBridgeTests.setUp

    def result(self):
        bridge = AnalysisResultsBridge(self.b)
        self.addCleanup(bridge.shutdown)
        return bridge

    def test_model_reorders_message_but_preserves_full_canonical_snapshot(self):
        seed = AnalysisSeed(2.123456789123, enabled=False, manual=True)
        self.doc.set_seeds(0, [seed])
        self.doc.gate_status[1] = 'failed'
        message = 'Invalid data: ' + 'long detail; ' * 80
        self.doc.gate_messages[1] = message
        columns, rows, gates = analysis_parameter_rows(self.doc)
        bridge = self.result()
        self.assertEqual(list(bridge.snapshot_columns), columns)
        self.assertEqual([list(row) for row in bridge.snapshot_rows], rows)
        self.assertEqual(list(bridge.snapshot_gates), gates)
        model = bridge.tableModel
        self.assertEqual((model.rowCount(), model.columnCount()), (len(rows), 28))
        order = [0, 1, 21, *[i for i in range(2, 28) if i != 21]]
        for display, canonical in enumerate(order):
            self.assertEqual(model.headerData(display, Qt.Horizontal, Qt.DisplayRole), columns[canonical])
            for row, values in enumerate(rows):
                expected = '' if values[canonical] is None else str(values[canonical])
                self.assertEqual(model.data(model.index(row, display), Qt.DisplayRole), expected)
        self.assertEqual(bridge.state['selectedRow'], 0)
        self.assertTrue(bridge.selectCell(1, 2, bridge.ownerToken))
        self.assertEqual(bridge.state['selectedHeader'], 'Message')
        self.assertEqual(bridge.state['selectedText'], message)
        self.doc.gate_messages[1] = 'new message'
        self.doc.set_seeds(0, [])
        self.assertEqual([list(row) for row in bridge.snapshot_rows], rows)
        self.assertEqual(model.data(model.index(1, 2), Qt.DisplayRole), message)
        self.assertEqual(model.data(model.index(1, 2), Qt.ToolTipRole), message)
        self.assertFalse(bridge.selectCell(-1, 0, bridge.ownerToken))
        self.assertFalse(bridge.selectCell(0, 28, bridge.ownerToken))

    def test_named_commands_and_aux_toggle_reject_wrong_busy_stale_closed_owner(self):
        actions = ('source', 'original', 'results', 'duplicate', 'exportSvg', 'exportPng', 'copySvg')
        self.w._workbench_toggle_linecut = Mock()
        for kind in ('wrong', 'busy', 'stale', 'closed'):
            with self.subTest(kind=kind):
                self.w.busy = False
                owner = self.b.ownerToken
                self.assertTrue(self.b.allowed(owner))
                self.w.busy = kind == 'busy'
                if kind == 'stale':
                    self.doc.input_version = 'replacement-owner'
                if kind == 'closed':
                    self.b.shutdown()
                token = 'wrong-owner' if kind == 'wrong' else owner
                for action in actions:
                    self.assertFalse(self.b.command(action, token), (kind, action))
                self.assertFalse(self.b.setAuxLinecut(False, token))
        self.w._workbench_toggle_linecut.assert_not_called()
        self.assertEqual(self.w.changes, 0)

    def test_results_not_gate_scoped_and_locate_uses_snapshot_row_mapping(self):
        self.doc.set_seeds(0, [AnalysisSeed(1.5, manual=True), AnalysisSeed(2., manual=True)])
        bridge = self.result()
        self.w.gate = 2
        self.assertTrue(bridge.canAccept)
        self.assertTrue(bridge.selectCell(2, 0, bridge.ownerToken))
        self.w._set_current_gate_from_index = Mock()
        self.assertTrue(bridge.locate(bridge.ownerToken))
        self.w._set_current_gate_from_index.assert_called_once_with(1, update_plot=True)

    def test_results_direct_slots_reject_busy_stale_and_closed_owners(self):
        self.w._set_current_gate_from_index = Mock()
        self.w._workbench_save_parameters = Mock(return_value=True)
        self.w._workbench_export_parameters = Mock(return_value=True)
        for kind in ('wrong', 'busy', 'stale', 'closed'):
            with self.subTest(kind=kind):
                self.w.busy = False
                bridge = self.result()
                owner = bridge.ownerToken
                self.assertTrue(bridge.canAccept)
                self.w.busy = kind == 'busy'
                if kind == 'stale':
                    self.doc.input_version = 'rebound'
                if kind == 'closed':
                    bridge.shutdown()
                token = 'wrong-owner' if kind == 'wrong' else owner
                self.assertFalse(bridge.selectCell(1, 0, token))
                self.assertFalse(bridge.locate(token))
                self.assertFalse(bridge.saveWorkbook(token))
                self.assertFalse(bridge.exportCsv(token))
        self.w._set_current_gate_from_index.assert_not_called()
        self.w._workbench_save_parameters.assert_not_called()
        self.w._workbench_export_parameters.assert_not_called()


class AnalysisStyleBridgeTests(unittest.TestCase):
    setUp = unit_fixture.AnalysisWorkbenchBridgeTests.setUp

    def style(self):
        bridge = AnalysisStyleBridge(self.b)
        self.addCleanup(bridge.shutdown)
        return bridge

    def test_all_layer_drafts_are_local_until_one_atomic_apply(self):
        for i, name in enumerate(LAYERS):
            self.doc.overlay_styles[name] = replace(self.doc.overlay_styles[name],
                visible=i % 2 == 0, edge_width=.25 + i / 10, alpha=.5 + i / 10,
                size=4.123456789 + i)
        before = deepcopy(self.doc.overlay_styles)
        bridge = self.style()
        self.assertEqual(list(bridge.layerIds), LAYERS)
        self.assertEqual(set(bridge.state['styles']), set(LAYERS))
        for i, name in enumerate(LAYERS):
            self.assertTrue(bridge.draftStyle(name, 'color', 'tab:blue', bridge.ownerToken))
            self.assertTrue(bridge.draftStyle(name, 'marker', ['s', '^', 'v', 'D'][i], bridge.ownerToken))
            self.assertTrue(bridge.draftStyle(name, 'filled', 'true', bridge.ownerToken))
        self.assertEqual(self.doc.overlay_styles, before)
        self.assertEqual(self.w.changes, 0)
        self.w.gate = 2
        self.assertTrue(bridge.canAccept, 'Styles belong to the Analysis owner, across Gate changes')
        self.assertTrue(bridge.applyStyles(bridge.ownerToken))
        self.assertEqual(self.w.changes, 1)
        for name in LAYERS:
            after = self.doc.overlay_styles[name]
            self.assertEqual(after.color, 'tab:blue')
            self.assertTrue(after.filled)
            for field in ('size', 'visible', 'edge_width', 'alpha'):
                self.assertEqual(getattr(after, field), getattr(before[name], field), (name, field))

    def test_invalid_color_marker_or_size_retains_raw_text_and_rolls_back_all_layers(self):
        for field, invalid in [('color', 'not a color'), ('marker', '?'), ('size', '-'),
                               ('size', 'nan'), ('size', 'inf'), ('size', '0.09'), ('size', '100.1')]:
            with self.subTest(field=field, invalid=invalid):
                bridge = self.style()
                before = deepcopy(self.doc.overlay_styles)
                focused = []
                bridge.focusRequested.connect(lambda *args: focused.append(args))
                bridge.draftStyle(LAYERS[0], 'color', 'green', bridge.ownerToken)
                self.assertTrue(bridge.draftStyle(LAYERS[-1], field, invalid, bridge.ownerToken))
                self.assertFalse(bridge.applyStyles(bridge.ownerToken))
                self.assertEqual(self.doc.overlay_styles, before)
                self.assertEqual(self.w.changes, 0)
                self.assertEqual(bridge.state['styles'][LAYERS[-1]][field], invalid)
                self.assertTrue(bridge.errorText)
                self.assertEqual(focused[-1], (LAYERS[-1], field))
                bridge.shutdown()

    def test_cancel_discards_drafts_and_busy_stale_closed_slots_are_inert(self):
        before = deepcopy(self.doc.overlay_styles)
        bridge = self.style()
        bridge.draftStyle(LAYERS[0], 'color', 'purple', bridge.ownerToken)
        bridge.shutdown()
        reopened = self.style()
        self.assertEqual(reopened.state['styles'][LAYERS[0]]['color'], before[LAYERS[0]].color)
        self.assertEqual(self.doc.overlay_styles, before)
        for kind in ('wrong', 'busy', 'stale', 'closed'):
            with self.subTest(kind=kind):
                target = self.style()
                owner = target.ownerToken
                self.w.busy = kind == 'busy'
                if kind == 'stale':
                    self.doc.input_version = 'new-input-version'
                if kind == 'closed':
                    target.shutdown()
                token = 'wrong-owner' if kind == 'wrong' else owner
                self.assertFalse(target.draftStyle(LAYERS[0], 'color', 'purple', token))
                self.assertFalse(target.applyStyles(token))
                self.assertEqual(self.doc.overlay_styles, before)
                self.w.busy = False


class AnalysisResultsPersistenceTests(QuickMigrationCase):
    setUp = production.AnalysisWorkbenchTests.setUp
    dispose = production.AnalysisWorkbenchTests.dispose
    enter = production.AnalysisWorkbenchTests.enter

    def test_snapshot_csv_and_workbook_preserve_all_cells_precision_and_provenance(self):
        doc = self.enter().analysis
        doc.set_seeds(0, [AnalysisSeed(1.351234567891, manual=True, amplitude=2.123456789123,
                                     width=.012345678912, center_min=1.3, center_max=1.4)])
        doc.set_seeds(1, [AnalysisSeed(1.685678912345, kind='dip', enabled=False, manual=True)])
        doc.set_seeds(2, [])
        doc.gate_status.update({0: 'detected', 1: 'failed', 2: 'empty'})
        doc.gate_messages[1] = 'Precise failure, with comma\nand a new line'
        self.w._workbench_refresh()
        aq.set_choice(self.w, 'scope', 'current')
        self.w._set_current_gate_from_index(0, update_plot=True)
        aq.set_choice(self.w, 'model', 'Gaussian')
        aq.command(self.w, 'fit')
        wait_for_background_jobs(self.w, timeout=15)
        self.assertEqual(doc.gate_status[0], 'fitted', doc.gate_messages)
        self.assertIsNotNone(doc.science.batch_fit_result_map[0])
        bridge = AnalysisResultsBridge(self.w.analysis_workbench)
        self.addCleanup(bridge.shutdown)
        columns, rows, gates = analysis_parameter_rows(doc)
        provenance = deepcopy(self.w._workbench_provenance())
        doc.set_seeds(0, [AnalysisSeed(1.9, manual=True)])
        doc.gate_messages[1] = 'Changed after opening'
        doc.source_mode = 'raw'
        doc.science.applied.window_length += 2
        target = self.folder / 'snapshot.csv'
        with patch('spectra_app.analysis_workbench.FilePicker.getSaveFileName', return_value=(str(target), 'CSV')):
            self.assertTrue(bridge.exportCsv(bridge.ownerToken))
        self.assertTrue(target.read_bytes().startswith(b'\xef\xbb\xbf'))
        with target.open(newline='', encoding='utf-8-sig') as handle:
            exported = list(csv.reader(handle))
        self.assertEqual(exported, [columns, *[['' if value is None else str(value) for value in row] for row in rows]])
        self.assertTrue(bridge.saveWorkbook(bridge.ownerToken))
        sheet = next(sheet for sheet in self.w.workspace.sheets.values() if sheet.name == 'Analysis parameters')
        self.assertEqual(sheet.column_names, columns)
        self.assertEqual(sheet.data.shape, (len(rows), 28))
        for col in range(28):
            if any(isinstance(row[col], str) for row in rows):
                self.assertEqual(sheet.text_columns[col], ['' if row[col] is None else str(row[col]) for row in rows])
            else:
                np.testing.assert_array_equal(sheet.data[:, col], [np.nan if row[col] is None else row[col] for row in rows])
        self.assertEqual(json.loads(sheet.snapshot_note), provenance)
        self.assertEqual(list(bridge.snapshot_gates), gates)

    def test_csv_cancel_and_owner_change_during_file_picker_write_nothing(self):
        self.enter()
        bridge = AnalysisResultsBridge(self.w.analysis_workbench)
        self.addCleanup(bridge.shutdown)
        with patch('spectra_app.analysis_workbench.FilePicker.getSaveFileName', return_value=('', '')):
            self.assertFalse(bridge.exportCsv(bridge.ownerToken))
        self.assertTrue(bridge.canAccept)
        target = self.folder / 'stale.csv'
        def switch_owner(*args, **kwargs):
            self.w._workbench_duplicate()
            return str(target), 'CSV'
        with patch('spectra_app.analysis_workbench.FilePicker.getSaveFileName', side_effect=switch_owner):
            self.assertFalse(bridge.exportCsv(bridge.ownerToken))
        self.assertFalse(target.exists())

    def test_csv_picker_return_rechecks_same_owner_popup_close_and_busy_state(self):
        self.enter()
        for state in ('closed', 'busy'):
            with self.subTest(state=state):
                bridge = AnalysisResultsBridge(self.w.analysis_workbench)
                self.addCleanup(bridge.shutdown)
                target = self.folder / (state + '.csv')
                busy = [False]
                def retire(*args, **kwargs):
                    if state == 'closed':
                        bridge.shutdown()
                    else:
                        busy[0] = True
                    return str(target), 'CSV'
                with patch('spectra_app.analysis_workbench.FilePicker.getSaveFileName', side_effect=retire), \
                        patch.object(self.w, '_project_busy', side_effect=lambda: busy[0]):
                    self.assertFalse(bridge.exportCsv(bridge.ownerToken))
                self.assertFalse(target.exists())

    def test_direct_compatibility_exports_return_success_and_guard_closed_owner(self):
        self.enter()
        target = self.folder / 'compatibility.csv'
        with patch('spectra_app.analysis_workbench.FilePicker.getSaveFileName', return_value=(str(target), 'CSV')):
            self.assertTrue(self.w._workbench_export_parameters())
        self.assertTrue(self.w._workbench_save_parameters())
        before = len(self.w.workspace.sheets)
        self.w.analysis_workbench.shutdown()
        with patch('spectra_app.analysis_workbench.FilePicker.getSaveFileName') as chooser:
            self.assertFalse(self.w._workbench_export_parameters())
            self.assertFalse(self.w._workbench_save_parameters())
        chooser.assert_not_called()
        self.assertEqual(len(self.w.workspace.sheets), before)

    def test_failed_workbook_publication_does_not_report_success(self):
        self.enter()
        host = self.w._workbench_results_table()
        self.addCleanup(host.reject)
        bridge = host.bridge
        errors = []
        self.w._show_error = errors.append
        aq.pump()
        with patch.object(self.w, '_publish_analysis_results', side_effect=ValueError('publication failed')):
            self.click_dialog(host, 'resultsSave')
        self.assertFalse(host._finished, 'Failed publication must keep the Results window open')
        self.assertTrue(host.window().isVisible())
        self.assertTrue(bridge.canAccept)
        self.assertTrue(errors or bridge.errorText)
        self.assertFalse(any(sheet.name == 'Analysis parameters' for sheet in self.w.workspace.sheets.values()))


    def click_dialog(self, host, name):
        target = aq.item(host, name)
        point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
        QTest.mouseClick(host.window(), Qt.LeftButton, pos=point)
        aq.pump()

    def test_production_style_typing_cancel_escape_preserve_canonical_styles(self):
        doc = self.enter().analysis
        before = deepcopy(doc.overlay_styles)
        host = self.w._workbench_overlay_style()
        self.addCleanup(host.reject)
        aq.pump()
        color = aq.type_text(host, 'style_detected-peak_color', 'tab:green')
        self.assertTrue(color.hasActiveFocus())
        size = aq.type_text(host, 'style_detected-peak_size', '1.')
        self.assertTrue(size.hasActiveFocus(), 'State projection must retain the live editor')
        self.assertEqual(host.bridge.state['styles']['detected-peak']['size'], '1.')
        self.assertEqual(doc.overlay_styles, before, 'Typing is local to the open Style dialog')
        self.click_dialog(host, 'styleCancel')
        self.assertTrue(host._finished)
        self.assertEqual(doc.overlay_styles, before)
        reopened = self.w._workbench_overlay_style()
        self.addCleanup(reopened.reject)
        aq.pump()
        self.assertEqual(reopened.bridge.state['styles']['detected-peak']['color'], before['detected-peak'].color)
        aq.type_text(reopened, 'style_detected-peak_size', '-')
        self.click_dialog(reopened, 'styleApply')
        self.assertFalse(reopened._finished)
        self.assertEqual(aq.item(reopened, 'style_detected-peak_size').property('text'), '-')
        self.assertTrue(aq.item(reopened, 'style_detected-peak_size').hasActiveFocus())
        self.assertEqual(doc.overlay_styles, before)
        QTest.keyClick(reopened.window(), Qt.Key_Escape)
        aq.pump()
        self.assertTrue(reopened._finished)
        self.assertEqual(doc.overlay_styles, before)


    def capture_qml_messages(self):
        messages = []
        previous = qInstallMessageHandler(lambda kind, context, text: messages.append(text))
        self.addCleanup(qInstallMessageHandler, previous)
        return messages

    def assert_bindable_state(self, messages):
        warnings = [message for message in messages
                    if 'non-bindable' in message.lower() or 'non-notifyable' in message.lower()]
        self.assertEqual(warnings, [], '\n'.join(warnings))

    def test_production_results_selection_updates_bound_qml_text_and_cell(self):
        messages = self.capture_qml_messages()
        doc = self.enter().analysis
        detail = 'Retained failed Gate detail, exact provenance.'
        doc.gate_status[1] = 'failed'
        doc.gate_messages[1] = detail
        host = self.w._workbench_results_table()
        self.addCleanup(host.reject)
        aq.pump()
        self.assertTrue(host.bridge.selectCell(1, 2, host.bridge.ownerToken))
        aq.pump()
        for name, expected in [('resultsSelectedHeader', 'Message'), ('resultsSelectedText', detail)]:
            with self.subTest(control=name):
                self.assertEqual(aq.item(host, name).property('text'), expected, '\n'.join(messages))
        self.assertTrue(aq.item(host, 'resultCell_1_2').property('selected'), '\n'.join(messages))
        self.assert_bindable_state(messages)

    def test_production_style_bridge_drafts_update_bound_marker_and_filled_controls(self):
        messages = self.capture_qml_messages()
        doc = self.enter().analysis
        before = deepcopy(doc.overlay_styles)
        host = self.w._workbench_overlay_style()
        self.addCleanup(host.reject)
        aq.pump()
        bridge = host.bridge
        self.assertTrue(bridge.draftStyle('detected-peak', 'marker', 's', bridge.ownerToken))
        self.assertTrue(bridge.draftStyle('detected-peak', 'filled', 'true', bridge.ownerToken))
        aq.pump()
        with self.subTest(control='marker'):
            self.assertEqual(aq.item(host, 'style_detected-peak_marker').property('text'), 's ▾', '\n'.join(messages))
        with self.subTest(control='filled'):
            self.assertTrue(aq.item(host, 'style_detected-peak_filled').property('checked'), '\n'.join(messages))
        self.assertEqual(doc.overlay_styles, before)
        self.assert_bindable_state(messages)
