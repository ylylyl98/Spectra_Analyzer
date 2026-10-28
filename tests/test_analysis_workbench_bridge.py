"""Atomic drafts, popup ownership and guards independent of rendered delegates."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject
from spectra_app.analysis_document import AnalysisSeed, ensure_analysis_graph
from spectra_app.analysis_workbench import AnalysisWorkbenchMixin
from spectra_app.analysis_workbench_bridge import AnalysisWorkbenchBridge, AnalysisPopupBridge
from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import AnalysisControls, DatasetDefaults, FixedRecipe, ViewState, Workspace, initialize_dataset


class BridgeWindow(QObject, AnalysisWorkbenchMixin):
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        self.busy = False
        self.gate = 0
        self.changes = 0
        self._restoring_dataset = self._workbench_syncing = False
        self._workbench_last_summary = None
        self._background_job_owner = None
        self.analysis_workbench = AnalysisWorkbenchBridge(self)

    def _project_busy(self):
        return self.busy

    def _current_gate_index(self):
        return self.gate

    def statusBar(self):
        return SimpleNamespace(currentMessage=lambda: '')

    def _workbench_changed(self):
        self.changes += 1
        self.analysis_workbench.refresh()


class AnalysisWorkbenchBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / 'source.dat'
        path.write_text('Energy(eV) -2 0 3\n1 1 2 3\n2 3 4 5\n3 2 3 4\n')
        defaults = DatasetDefaults(ViewState(), AnalysisControls(),
            {name: FixedRecipe() for name in ('processing', 'regions', 'region_edges')}, {})
        session = initialize_dataset(path, (load_dat_cube(path), None, None, ''), defaults)
        workspace = Workspace()
        workspace.register(session)
        workspace.active_dataset_id = session.dataset_id
        self.graph = ensure_analysis_graph(session)
        self.doc = self.graph.analysis
        self.w = BridgeWindow(workspace)
        self.b = self.w.analysis_workbench
        self.b.refresh()
        self.owner = self.b.ownerToken

    def seed(self, **kwargs):
        seed = AnalysisSeed(2., manual=True, **kwargs)
        self.doc.set_seeds(0, [seed])
        return seed

    def test_invalid_sibling_seed_blocks_numeric_commit_atomically(self):
        seed = self.seed()
        old = self.doc.science.analysis_controls.fit_xmin
        self.assertTrue(self.b.draftNumber('xmin', '1.25', self.owner))
        self.assertTrue(self.b.draftSeed(seed.seed_id, 0, 'width', '-1', self.owner))
        self.assertFalse(self.b.commitEditors(self.owner))
        self.assertEqual(self.doc.science.analysis_controls.fit_xmin, old)
        self.assertIsNone(self.doc.seeds_by_gate[0][0].width)
        self.assertEqual(self.w.changes, 0)
        self.assertEqual(self.b.state['selectedSeedId'], seed.seed_id)
        self.b.draftSeed(seed.seed_id, 0, 'width', '.1', self.owner)
        self.assertTrue(self.b.commitEditors(self.owner))
        self.assertEqual(self.doc.science.analysis_controls.fit_xmin, 1.25)
        self.assertEqual(self.doc.seeds_by_gate[0][0].width, .1)
        self.assertEqual(self.w.changes, 1)

    def test_blank_estimates_fixed_bounds_move_and_voigt_gamma_retention(self):
        seed = self.seed(amplitude=2, width=.1, gamma=.2, center_min=1.8, center_max=2.2)
        self.b.setChoice('model', 'Voigt', self.owner)
        for field, value in [('amplitude', ''), ('width', ''), ('fixedCenter', 'true'), ('position', '2.1')]:
            self.assertTrue(self.b.draftSeed(seed.seed_id, 0, field, value, self.owner))
        self.assertTrue(self.b.commitEditors(self.owner))
        updated = self.doc.seeds_by_gate[0][0]
        self.assertIsNone(updated.amplitude)
        self.assertIsNone(updated.width)
        self.assertIsNone(updated.center_min)
        self.assertIsNone(updated.center_max)
        self.assertTrue(updated.fixed_center)
        self.assertEqual(updated.gamma, .2)
        self.b.setChoice('model', 'Gaussian', self.owner)
        self.assertFalse(self.b.draftSeed(seed.seed_id, 0, 'gamma', '.7', self.owner))
        self.b.setChoice('model', 'Voigt', self.owner)
        self.assertEqual(self.b.state['seeds'][0]['gamma'], '0.2')
        self.b.draftSeed(seed.seed_id, 0, 'bound', '.15', self.owner)
        self.assertTrue(self.b.commitEditors(self.owner))
        updated = self.doc.seeds_by_gate[0][0]
        self.assertAlmostEqual(updated.center_min, 1.95)
        self.assertAlmostEqual(updated.center_max, 2.25)

    def test_invalid_filter_projection_is_pure_and_draft_keystrokes_do_not_scan_input(self):
        notices = []
        self.b.stateChanged.connect(lambda: notices.append(True))
        with patch('spectra_app.analysis_workbench_bridge.analysis_input_changed') as compare:
            self.b.draftFilter('max_count', 'x', self.owner)
            self.b.draftNumber('xmin', '1.', self.owner)
            before = len(notices)
            for _ in range(5):
                self.assertIn('invalid', self.b.state['filterState'])
            self.assertEqual(len(notices), before)
            compare.assert_not_called()
        self.assertFalse(self.b.commitEditors(self.owner))
        self.b.draftFilter('max_count', '10001', self.owner)
        self.assertFalse(self.b.commitEditors(self.owner))
        self.b.draftFilter('max_count', '10000', self.owner)
        self.assertTrue(self.b.commitEditors(self.owner))

    def test_filter_draft_reopen_and_scope_reset(self):
        self.b.draftFilter('min_width', '-', self.owner)
        with patch.object(self.b, 'open_popup') as popup:
            self.assertTrue(self.b.command('filterSettings', self.owner))
            popup.assert_called_once_with('filterSettings')
        self.assertEqual(self.b.state['filter']['min_width'], '-')
        self.b.draftFilter('min_width', '0.1', self.owner)
        self.assertTrue(self.b.commitEditors(self.owner))
        self.b.setChoice('scope', 'current', self.owner)
        self.assertEqual(self.b.state['filter']['min_width'], '0.0')
        self.b.draftFilter('min_width', '0.2', self.owner)
        popup = AnalysisPopupBridge(self.b, 'filterSettings')
        self.w.gate = 1
        self.b.refresh()
        self.assertFalse(popup.allowed(self.owner))
        self.assertEqual(self.b.state['filter']['min_width'], '0.0')

    def test_invalid_seed_reopen_preserves_draft_and_deleted_seed_cannot_return(self):
        seed = self.seed()
        self.b.draftSeed(seed.seed_id, 0, 'position', 'x', self.owner)
        popup = AnalysisPopupBridge(self.b, 'seeds')
        popup.shutdown()
        with patch.object(self.b, 'open_popup') as open_popup:
            self.assertTrue(self.b.command('seeds', self.owner))
            open_popup.assert_called_once_with('seeds')
        self.assertEqual(self.b.state['seeds'][0]['position'], 'x')
        self.doc.set_seeds(0, [])
        self.assertFalse(self.b.draftSeed(seed.seed_id, 0, 'position', '2.2', self.owner))
        self.assertTrue(self.b.commitEditors(self.owner))
        self.assertEqual(self.doc.seeds_by_gate[0], [])

    def test_busy_closed_and_rebound_owners_reject_direct_slots(self):
        seed = self.seed()
        self.w.busy = True
        self.assertTrue(self.b.enabled, 'A valid busy owner keeps Stop reachable.')
        self.assertFalse(self.b.draftNumber('xmin', '1.2', self.owner))
        self.assertFalse(self.b.draftFilter('min_width', '1', self.owner))
        self.assertFalse(self.b.draftSeed(seed.seed_id, 0, 'position', '2.2', self.owner))
        self.assertFalse(self.b.command('remove', self.owner))
        self.w._workbench_add_seed(position=2.3)
        self.assertEqual(len(self.doc.seeds_by_gate[0]), 1)
        self.w.busy = False
        popup = AnalysisPopupBridge(self.b, 'seeds')
        self.doc.input_version = 'replacement'
        self.assertFalse(self.b.draftNumber('xmin', '1.2', self.owner))
        self.assertFalse(popup.allowed())
        current = self.b.ownerToken
        self.b.shutdown()
        self.assertFalse(self.b.command('fit', current))
        self.w._workbench_find()
        self.w._workbench_filter()
        self.w._workbench_fit()
        self.assertEqual(self.w.changes, 0)

    def test_durable_draft_marker_ignores_preferences_and_retired_owner(self):
        self.assertFalse(self.b.has_project_drafts())
        self.b.draftFilter('min_width', '0.2', self.owner)
        self.b.setChoice('inputMode', 'raw', self.owner)
        self.b.draftNumber('addPosition', '1.5', self.owner)
        self.assertFalse(self.b.has_project_drafts())
        self.b.draftNumber('xmin', str(self.doc.science.analysis_controls.fit_xmin), self.owner)
        self.assertFalse(self.b.has_project_drafts())
        self.b.draftNumber('xmin', '-', self.owner)
        self.assertTrue(self.b.has_project_drafts())
        self.b.draftNumber('xmin', '1.1', self.owner)
        self.assertTrue(self.b.has_project_drafts())
        self.assertEqual(self.doc.science.analysis_controls.fit_xmin, 1.)
        self.assertTrue(self.b.commitEditors(self.owner))
        self.assertFalse(self.b.has_project_drafts())
        seed = self.seed()
        self.b.draftSeed(seed.seed_id, 0, 'width', '-', self.owner)
        self.assertTrue(self.b.has_project_drafts())
        self.assertIsNone(self.doc.seeds_by_gate[0][0].width)
        self.doc.input_version = 'new input'
        self.assertFalse(self.b.has_project_drafts())

    def test_gate_retirement_hides_popup_but_busy_keeps_current_popup(self):
        self.seed()
        popup = AnalysisPopupBridge(self.b, 'seeds')
        host = SimpleNamespace(_finished=False, bridge=popup, reject=Mock(side_effect=popup.shutdown))
        self.b._dialogs['seeds'] = host
        self.w.busy = True
        self.b.refresh()
        host.reject.assert_not_called()
        self.w.busy = False
        self.w.gate = 1
        self.b.refresh()
        host.reject.assert_called_once_with()
        self.assertFalse(popup.allowed())

    def test_detected_kind_replacement_exclusion_survives_remove(self):
        seed = AnalysisSeed(2., source_index=1)
        self.doc.set_seeds(0, [seed])
        self.b.draftSeed(seed.seed_id, 0, 'kind', 'dip', self.owner)
        self.assertTrue(self.b.commitEditors(self.owner))
        original, manual = self.doc.seeds_by_gate[0]
        self.assertEqual(original.seed_id, seed.seed_id)
        self.assertFalse(original.enabled)
        self.assertTrue(original.manual)
        self.assertTrue(manual.manual)
        self.assertEqual(manual.kind, 'dip')
        self.assertNotEqual(manual.seed_id, seed.seed_id)
        self.b.selectSeed(manual.seed_id, 0, self.owner)
        self.w._workbench_remove_seed()
        self.assertEqual(self.doc.seeds_by_gate[0], [original])
        self.b.selectSeed(original.seed_id, 0, self.owner)
        self.w._workbench_remove_seed()
        self.assertEqual(self.doc.seeds_by_gate[0], [original])


if __name__ == '__main__':
    unittest.main()
