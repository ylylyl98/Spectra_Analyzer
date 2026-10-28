"""Shared header transactions through actual Quick controls and project owners."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, Qt

from tests.quick_migration_case import QuickMigrationCase
from spectra_app.analysis_document import AnalysisSeed
from tests import analysis_header_quick_helpers as ah
from tests import dat_properties_quick_helpers as dp
from tests.formal_quick_helpers import pump
from tests.settings_helpers import wait_for_background_jobs

APP = QCoreApplication


class AnalysisHeaderIntegrationTests(QuickMigrationCase):
    def setUp(self):
        QuickMigrationCase.setUp(self)
        self.w = self.controller
        self.errors = []
        self.w._show_error = self.errors.append
        self.w.root_window.resize(1250, 860)
        self.session = self.load('first')
        ah.reveal(self.w)
        ah.edit(self.w, 'index', '1', commit=True)
        self.addCleanup(self.dispose)

    def dispose(self):
        wait_for_background_jobs(self.w)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.quick_errors, [])

    def load(self, name):
        path = self.folder / (name + '.dat')
        path.write_text('Energy(eV) 0 1 2 3\n' + '\n'.join(
            f'{1+i/20} {i+1} {2*i+1} {3*i+1} {4*i+1}' for i in range(31)))
        self.w._load_dat_file_direct(path)
        return self.w.workspace.active

    def named(self):
        self.w.open_graph_document()
        self.w.analyze_action.trigger()
        pump()
        self.assertIsNotNone(self.w._workbench_document())
        return self.w._workbench_document()

    def test_invalid_raw_draft_refresh_retains_cursor_and_blocks_save_and_back(self):
        w = self.w
        control = ah.edit(w, 'gate', '1.0000001')
        ah.key(w, Qt.Key_Left)
        cursor = control.property('cursorPosition')
        w._refresh_analysis_context()
        w.analysis_header.refresh()
        pump()
        self.assertTrue(control.hasActiveFocus())
        self.assertEqual(control.property('text'), '1.0000001')
        self.assertEqual(control.property('cursorPosition'), cursor)
        self.assertEqual(w.current_gate, 0.)
        with self.assertRaises(ValueError):
            w.save_project(self.folder / 'invalid.spectra')
        self.assertFalse((self.folder / 'invalid.spectra').exists())
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'analysis')
        self.assertEqual(ah.text(w, 'gate'), '1.0000001')
        self.assertEqual(w.current_gate, 0.)
        self.assertTrue(w.analysis_header.errorText)
        ah.edit(w, 'gate', '1e0', commit=True)
        self.assertEqual(w.current_gate, 1.)

    def test_save_and_switch_validate_gate_and_siblings_before_partial_gate_commit(self):
        w = self.w
        other = w.create_graph('Other')
        ah.edit(w, 'index', '3')
        w.dat_properties.draftNumber('vmin', 'invalid', w.dat_properties.ownerToken)
        with self.assertRaises(ValueError):
            w.save_project(self.folder / 'invalid-siblings.spectra')
        self.assertEqual(w.current_gate, 0.)
        self.assertEqual(ah.text(w, 'index'), '3')
        with self.assertRaises(ValueError):
            w.activate_graph('default')
        self.assertEqual(w.workspace.active.active_graph_id, other.graph_id)
        self.assertEqual(w.current_gate, 0.)
        self.assertEqual(dp.state(w)['vmin'], 'invalid')
        w.dat_properties.draftNumber('vmin', '10', w.dat_properties.ownerToken)
        w.activate_graph('default')
        self.assertEqual(other.view.gate, 2.)
        self.assertEqual(other.view.bounds['vmin'], 10.)
        self.assertTrue(other.view.locks['vmin'])

    def test_invalid_gate_does_not_commit_valid_formal_or_color_sibling(self):
        w = self.w
        ah.edit(w, 'index', '-')
        width = w.workspace.active.presentation.width_in
        w.plot_page.formal.setDraft('width_in', str(width + 1))
        w.dat_properties.draftNumber('vmin', '10', w.dat_properties.ownerToken)
        with self.assertRaises(ValueError):
            w.save_project(self.folder / 'invalid-gate.spectra')
        self.assertEqual(ah.text(w, 'index'), '-')
        self.assertEqual(w.current_gate, 0.)
        self.assertEqual(w.workspace.active.presentation.width_in, width)
        self.assertEqual(dp.state(w)['vmin'], '10')
        self.assertFalse(w.workspace.active.view.locks['vmin'])

    def test_named_invalid_seed_draft_blocks_gate_and_back_without_consuming_raw_text(self):
        w = self.w
        doc = self.named()
        seed = AnalysisSeed(1.5, manual=True)
        doc.set_seeds(0, [seed])
        w._workbench_refresh()
        ah.edit(w, 'index', '3')
        w.analysis_workbench.draftSeed(seed.seed_id, 0, 'position', '-', w.analysis_workbench.ownerToken)
        ah.key(w, Qt.Key_Return)
        self.assertEqual(w.current_gate, 0.)
        self.assertEqual(ah.text(w, 'index'), '3')
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'analysis')
        self.assertIs(w._workbench_document(), doc)
        self.assertEqual(doc.seeds_by_gate[0][0].position, 1.5)
        self.assertEqual(ah.text(w, 'index'), '3')

    def test_index_selected_gate_preserves_source_precision_beyond_display(self):
        w = self.w
        gates = np.array([0., 1.0000002, 1.0000004, 3.])
        self.session.current_cube = replace(self.session.current_cube, gate=gates)
        w._plot_cube()
        ah.edit(w, 'index', '3', commit=True)
        self.assertEqual(w.current_gate, gates[2])
        self.assertEqual(w._current_gate_index(), 2)
        self.assertEqual(float(ah.text(w, 'gate')), 1.)
        np.testing.assert_array_equal(w.current_cube.gate, gates)

    def test_gate_remains_available_while_back_is_blocked_for_captured_job(self):
        w = self.w
        with patch.object(w, '_project_busy', return_value=True):
            w._refresh_analysis_context()
            w.analysis_header.refresh()
            pump()
            self.assertFalse(ah.header(w, 'analysisBack').isEnabled())
            self.assertTrue(ah.control(w, 'index').isEnabled())
            ah.edit(w, 'index', '3', commit=True)
            self.assertEqual(w.current_gate, 2.)
            ah.button(w, 'analysisBack')
            self.assertEqual(w.outerView, 'analysis')

    def test_named_busy_gate_preserves_valid_sibling_drafts_until_idle_commit(self):
        w = self.w
        doc = self.named()
        named = w.analysis_workbench
        with patch.object(w, '_project_busy', return_value=True):
            w._refresh_analysis_context()
            w.analysis_header.refresh()
            pump()
            self.assertTrue(ah.control(w, 'index').isEnabled())
            self.assertFalse(ah.header(w, 'analysisBack').isEnabled())
            with patch.object(w, '_update_spectrum_only', wraps=w._update_spectrum_only) as trace:
                ah.edit(w, 'index', '3', commit=True)
                self.assertEqual(w._current_gate_index(), 2)
                self.assertEqual(trace.call_count, 1)

        ah.edit(w, 'index', '1', commit=True)
        seed = AnalysisSeed(1.5, manual=True)
        doc.set_seeds(0, [seed])
        w._workbench_refresh()
        ah.edit(w, 'index', '3')
        self.assertTrue(named.draftNumber('xmin', '1.1000', named.ownerToken))
        self.assertTrue(named.draftSeed(seed.seed_id, 0, 'position', '1.5700', named.ownerToken))
        candidate = named.validate_editor()
        old_minimum = doc.science.analysis_controls.fit_xmin
        with patch.object(w, '_project_busy', return_value=True):
            w._refresh_analysis_context()
            w.analysis_header.refresh()
            with patch.object(w, '_update_spectrum_only', wraps=w._update_spectrum_only) as trace:
                ah.key(w, Qt.Key_Return)
                self.assertEqual(trace.call_count, 0)
            self.assertEqual(w._current_gate_index(), 0)
            self.assertEqual(ah.text(w, 'index'), '3')
            self.assertEqual(named.state['xmin'], '1.1000')
            self.assertEqual(named.state['seeds'][0]['position'], '1.5700')
            self.assertEqual(named.validate_editor(), candidate)
            self.assertEqual(doc.science.analysis_controls.fit_xmin, old_minimum)
            self.assertEqual(doc.seeds_by_gate[0][0].position, 1.5)

        with patch.object(w, '_update_spectrum_only', wraps=w._update_spectrum_only) as trace:
            ah.key(w, Qt.Key_Return)
            self.assertEqual(trace.call_count, 1)
            # Enter followed by a real blur must not reapply the Gate candidate.
            ah.button(w, 'contextDetails')
            self.assertEqual(trace.call_count, 1)
            ah.button(w, 'contextDetailsClose')
        self.assertEqual(w._current_gate_index(), 2)
        self.assertEqual(ah.text(w, 'index'), '3')
        self.assertEqual(doc.science.analysis_controls.fit_xmin, 1.1)
        self.assertEqual(doc.seeds_by_gate[0][0].position, 1.57)
        self.assertEqual(named.validate_editor(), ({}, {}))

    def test_pending_index_arrow_applies_final_trace_once(self):
        w = self.w
        ah.edit(w, 'index', '3')
        with patch.object(w, '_update_spectrum_only_impl', wraps=w._update_spectrum_only_impl) as rebuild:
            ah.key(w, Qt.Key_Up)
            self.assertEqual(rebuild.call_count, 1)
        self.assertEqual(w.current_gate, 3.)
        self.assertEqual(ah.text(w, 'index'), '4')
        np.testing.assert_array_equal(w._spec_ax.lines[0].get_ydata(), w.current_cube.z[3])

    def test_pressed_step_completion_is_inert_after_dataset_or_graph_switch(self):
        w = self.w
        first = self.session
        second = self.load('second')
        ah.edit(w, 'index', '1', commit=True)
        w.activate_dataset(first.dataset_id)
        ah.reveal(w)
        ah.button(w, 'gateNext', press=True)
        w.activate_dataset(second.dataset_id)
        ah.button(w, 'gateNext', release=True)
        self.assertEqual(w.current_gate, 0.)
        self.assertEqual(first.view.gate, 0.)
        other = w.create_graph('Other')
        ah.reveal(w)
        ah.button(w, 'gateNext', press=True)
        w.activate_graph('default')
        ah.button(w, 'gateNext', release=True)
        self.assertEqual(w.current_gate, 0.)
        self.assertEqual(other.view.gate, 0.)

    def test_pressed_step_completion_is_inert_after_named_rebind_or_project_replacement(self):
        w = self.w
        doc = self.named()
        self.session.applied.window_length += 2
        w._workbench_refresh()
        ah.button(w, 'gateNext', press=True)
        with patch.object(w, '_workbench_confirm_rebind', return_value='replace'):
            w._workbench_rebind_input()
        self.assertNotEqual(w._workbench_document().input_version, doc.input_version)
        ah.button(w, 'gateNext', release=True)
        self.assertEqual(w.current_gate, 0.)
        path = w.save_project(self.folder / 'replacement.spectra')
        ah.button(w, 'gateNext', press=True)
        self.assertTrue(w.open_project(path))
        ah.button(w, 'gateNext', release=True)
        self.assertEqual(w.current_gate, 0.)

    def test_context_details_are_selectable_idempotent_and_clear_on_empty_reset(self):
        w = self.w
        doc = self.named()
        details = ah.details(w)
        for _ in range(3):
            self.assertEqual(w._workbench_update_input_context()['details'], details)
            self.assertEqual(ah.details(w), details)
        w._refresh_analysis_context()
        self.assertEqual(ah.details(w), details)
        self.assertIn(doc.input_version[:8], details)
        self.assertIn(str(self.session.source_path), details)
        ah.button(w, 'contextDetails')
        content = ah.header(w, 'contextDetailsText')
        self.assertEqual(content.property('text'), details)
        self.assertTrue(content.property('readOnly'))
        self.assertTrue(content.property('selectByMouse'))
        ah.button(w, 'contextDetailsClose')
        w.save_project(self.folder / 'clear.spectra')
        self.assertTrue(w.new_project())
        self.assertEqual(ah.details(w), '')
        self.assertEqual(ah.header(w, 'contextSummary').property('text'), '')
        self.assertEqual(ah.header(w, 'contextWarning').property('text'), '')
        self.assertFalse(ah.control(w, 'gate').isEnabled())
        self.assertEqual(float(ah.text(w, 'gate')), 0.)
        ah.reveal(w)
        ah.button(w, 'analysisBack', press=True)
        self.assertTrue(w.new_project())
        ah.reveal(w)
        ah.button(w, 'analysisBack', release=True)
        self.assertEqual(w.outerView, 'analysis', 'A stale empty-project Back press must be inert')
        ah.button(w, 'analysisBack')
        self.assertEqual(w.outerView, 'workspace')


if __name__ == '__main__':
    unittest.main()
