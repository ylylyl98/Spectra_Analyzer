"""Gate parser parity, captured ownership, and single-change navigation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
from PySide6.QtCore import QObject, QLocale
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QSpinBox

from spectra_app.analysis_header_bridge import AnalysisHeaderBridge


APP = QApplication.instance() or QApplication([])


class ReferenceGate(QDoubleSpinBox):
    """Test-only pre-Stage12 editor; never part of production projection."""
    def __init__(self):
        super().__init__()
        self.setDecimals(6)
        self.setRange(-2, 5)
        self.setLocale(QLocale('en_US'))


class BridgeWindow(QObject):
    def __init__(self):
        super().__init__()
        self.current_cube = SimpleNamespace(gate=np.array([-2., 0., 2., 5.]))
        graph = SimpleNamespace(graph_id='g')
        self.session = SimpleNamespace(dataset_id='d', active_graph=graph)
        self.workspace = SimpleNamespace(active=self.session)
        self._unregistered_session = self.session
        self._data_generation = 0
        self._close_pending = False
        self.busy = False
        self.index = 0
        self.changes = 0
        self.document = None
        self._workbench_finish_editors = Mock()
        self.analysis_workbench = SimpleNamespace(validate_editor=Mock(return_value=({}, {})))
        self.back_to_workspace_action = QAction(self)
        self.message = Mock()
        self.bridge = AnalysisHeaderBridge(self)

    def _project_busy(self):
        return self.busy

    def _workbench_document(self):
        return self.document

    def _current_gate_index(self):
        return self.index

    def _set_current_gate_from_index(self, index, **_kwargs):
        index = max(0, min(len(self.current_cube.gate) - 1, index))
        if index != self.index:
            self.changes += 1
        self.index = index
        self.bridge.refresh()

    def publish_status(self, text, timeout=0):
        self.message(str(text), timeout)


class AnalysisHeaderBridgeTests(unittest.TestCase):
    def setUp(self):
        # QObject controllers read the application default, not a widget locale.
        self.addCleanup(QLocale.setDefault, QLocale())
        QLocale.setDefault(QLocale('en_US'))
        self.w = BridgeWindow()
        self.b = self.w.bridge
        self.owner = self.b.ownerToken
        self.addCleanup(self.w.deleteLater)

    def draft(self, field, text):
        self.assertTrue(self.b.setDraft(field, text, self.owner))

    def test_numeric_reference_acceptance(self):
        reference = ReferenceGate()
        self.addCleanup(reference.deleteLater)
        for locale, texts in (
            ('en_US', ('1', ' 1 ', '1e0', '+1', '1.', '-2', '5', '', '-', '.',
                       'nan', 'inf', '1.0000001', '5.000001', '-2.000001', '1e309',
                       '1e-7', '5e-7', '15e-7', '1.234e-3', '1.23456e-3', '1.2e-7', '0,0')),
            ('de_DE', ('1,5', ' 1,5 ', '1e0', '-2', '5', '1,0000001', '5,000001', 'nan',
                       '1,234e-3', '1,23456e-3', '1,2e-7', '0.0'))):
            QLocale.setDefault(QLocale(locale))
            reference.setLocale(QLocale(locale))
            for text in texts:
                with self.subTest(locale=locale, text=text):
                    reference.lineEdit().setText(text)
                    accepted = reference.hasAcceptableInput()
                    try:
                        self.b.parse_gate(text)
                        actual = True
                    except ValueError:
                        actual = False
                    self.assertEqual(actual, accepted)
                    if actual:
                        reference.interpretText()
                        self.assertEqual(self.b.parse_gate(text), reference.value())

    def test_exponent_value_and_shared_display_precision_preserve_native_selection(self):
        self.w.current_cube.gate = np.array([0., 1e-7, 2e-7, 1.])
        self.owner = self.b.ownerToken
        self.w.index = 2
        self.assertEqual(self.b.state['gateText'], '0.000000')
        self.assertFalse(self.b.has_project_drafts())
        self.draft('gate', '1e-7')
        self.assertTrue(self.b.has_project_drafts())
        self.assertTrue(self.b.commit(self.owner))
        self.assertEqual(self.w.index, 1)
        self.assertEqual(self.w.current_cube.gate[self.w.index], 1e-7)

    def test_range_rounding_matches_native_qt_halfway_values(self):
        reference = ReferenceGate()
        self.addCleanup(reference.deleteLater)
        for low, high in ((-.0000005, .0000005), (-1.2345675, 1.2345675),
                          (-.0000015, .0000015)):
            self.w.current_cube.gate = np.array([low, high])
            reference.setRange(low, high)
            self.assertEqual(self.b._rounded_gate(low), reference.minimum())
            self.assertEqual(self.b._rounded_gate(high), reference.maximum())
            for endpoint in (reference.minimum(), reference.maximum()):
                text = QLocale().toString(endpoint, 'f', 6)
                self.assertEqual(self.b.parse_gate(text), endpoint)

    def test_locale_display_omits_groups_and_keeps_localized_index_digits(self):
        gate, index = ReferenceGate(), QSpinBox()
        self.addCleanup(gate.deleteLater)
        self.addCleanup(index.deleteLater)
        self.w.current_cube.gate = np.arange(1200.)
        self.w.index = 1000
        for language in ('en_US', 'de_DE', 'ar_EG'):
            locale = QLocale(language)
            QLocale.setDefault(locale)
            gate.setLocale(locale)
            gate.setRange(0, 1199)
            gate.setValue(1000)
            index.setLocale(locale)
            index.setRange(1, 1200)
            index.setValue(1001)
            with self.subTest(locale=language):
                self.assertEqual(self.b.state['gateText'], gate.text())
                self.assertEqual(self.b.state['indexText'], index.text())
                self.assertEqual(self.b.parse_gate(gate.text()), 1000)
                self.assertEqual(self.b.parse_index(index.text()), 1000)

    def test_typed_rounding_range_and_first_nearest_tie(self):
        self.assertEqual(self.b.parse_gate('1e0'), 1.)
        self.draft('gate', '1')
        self.assertTrue(self.b.commit(self.owner))
        self.assertEqual(self.w.index, 1)  # 0 and 2 are equidistant: first wins.
        self.assertEqual(self.w.changes, 1)
        self.assertTrue(self.b.commit(self.owner))
        self.assertEqual(self.w.changes, 1)

    def test_invalid_sibling_prevents_partial_gate_commit_and_keeps_raw(self):
        self.draft('gate', '5')
        self.draft('index', '-')
        self.assertFalse(self.b.commit(self.owner))
        self.assertEqual(self.w.index, 0)
        self.assertEqual(self.b.state['gateText'], '5')
        self.assertEqual(self.b.state['indexText'], '-')
        self.assertTrue(self.b.has_project_drafts())
        self.w.message.assert_called_once_with('Enter a Gate index from 1 to 4.', 0)
        self.draft('index', '3')
        self.assertTrue(self.b.commit(self.owner))
        self.assertEqual(self.w.index, 2)

    def test_pending_gate_and_step_apply_once_and_preserve_array_positions(self):
        self.w.current_cube.gate = np.array([-2., .12345678, 2., 5.])
        self.owner = self.b.ownerToken
        self.draft('gate', '2')
        self.assertTrue(self.b.step(1, self.owner))
        self.assertEqual(self.w.index, 3)
        self.assertEqual(self.w.changes, 1)
        self.assertTrue(self.b.step(-2, self.owner))
        self.assertEqual(self.w.current_cube.gate[self.w.index], .12345678)

    def test_same_owner_refresh_retains_invalid_draft_new_cube_rejects_stale(self):
        self.draft('gate', '-')
        self.b.refresh(dict(title='full filename.dat', summary='Original', warning='', details='full provenance'))
        self.assertEqual(self.b.state['gateText'], '-')
        self.w.current_cube = SimpleNamespace(gate=np.array([-2., 0., 2., 5.]))
        self.assertFalse(self.b.setDraft('gate', '5', self.owner))
        self.assertFalse(self.b.commit(self.owner))
        self.assertFalse(self.b.step(1, self.owner))
        self.assertFalse(self.b.back(self.owner))
        self.assertEqual(self.w.changes, 0)

    def test_gate_allowed_busy_back_idle_and_named_guard_precedes_gate(self):
        self.w.busy = True
        self.assertTrue(self.b.enabled)
        self.assertFalse(self.b.backEnabled)
        self.draft('index', '2')
        self.assertTrue(self.b.commit(self.owner))
        self.w.document = SimpleNamespace(input_version='frozen')
        self.owner = self.b.ownerToken
        self.w.analysis_workbench.validate_editor.side_effect = ValueError('Invalid seed')
        self.draft('index', '3')
        self.assertFalse(self.b.commit(self.owner))
        self.assertEqual(self.w.index, 1)
        self.assertEqual(self.b.state['indexText'], '3')
        self.w.message.assert_called_once_with('Invalid seed', 0)

    def test_reset_clears_context_and_close_rejects_completion(self):
        self.draft('gate', 'invalid')
        self.w.current_cube = None
        self.b.retire_owner()
        self.assertEqual(self.b.state['details'], '')
        self.assertEqual(self.b.state['warning'], '')
        self.assertFalse(self.b.enabled)
        self.b.close()
        self.assertFalse(self.b.commit(self.owner))

    def test_empty_back_uses_current_project_and_save_candidate_is_noop(self):
        self.w.current_cube = None
        empty_owner = self.b.ownerToken
        triggered = Mock()
        self.w.back_to_workspace_action.triggered.connect(triggered)
        self.assertFalse(self.b.enabled)
        self.assertTrue(self.b.backEnabled)
        self.assertTrue(self.b.back(empty_owner))
        self.assertEqual(triggered.call_count, 1)
        self.b.commit_candidate(self.b.validate_editor())
        self.w.workspace = SimpleNamespace(active=None)
        self.assertFalse(self.b.back(empty_owner))
        self.assertEqual(triggered.call_count, 1)


if __name__ == '__main__':
    unittest.main()
