"""Cheap record-boundary proof for the XY Quick editors (no GUI windows)."""
from dataclasses import replace
from copy import copy
from unittest.mock import patch
import unittest

import numpy as np

from spectra_app.analysis_document import AnalysisSeed
from spectra_app.dataset_state import Workspace
from spectra_app.peak_candidates import CandidateFilter
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_analysis import AnalysisSettings, capture_input, collect_candidates, XYAnalysisProgress
from spectra_app.xy_analysis_bridge import XYAnalysisBridge, XYSeedsBridge


class _InputAuthority:
    ownerToken = 'record-fixture:1'
    def allowed(self, owner=None):
        return owner is None or owner == self.ownerToken
    def authorize(self, owner):
        return self.allowed(owner)


class _Page:
    def __init__(self):
        self.command_bridge = _InputAuthority()
        self.workspace = Workspace()
        book = create_workbook(self.workspace)
        x = np.linspace(-4, 4, 81)
        sheet = register_xy_sheet(self.workspace, book.workbook_id, 'Source',
            np.column_stack([x, 1 + np.exp(-.5*(x/.5)**2)]), ['X', 'Y'])
        self.comparison = create_comparison(self.workspace)
        self.curve = add_curve(self.workspace, self.comparison.comparison_id, sheet.sheet_id, 0, 1)
        self.selection = {self.curve.curve_id}
        self.renders = 0

    def _selected_ids(self):
        return self.selection

    def render(self):
        self.renders += 1


class _RecordOwner:
    """Scientific owner fixture; presentation and legacy widget APIs are absent."""
    def __init__(self):
        self.page = _Page()
        self._settings, self._filters = AnalysisSettings(), CandidateFilter()
        self.progress = XYAnalysisProgress(capture_input(self.page.workspace, self.page.comparison,
                                                        self.page.curve.curve_id, self._settings))
        self.progress.set_candidates(collect_candidates(self.progress.captured.data, self._settings))
        self._state = {'editable': True}
        self._disposed = False
        self._key = self.progress.captured.key
        self._revision = 0
        self._request = self._result_ids = self._graph_id = None
        self.commits = self.publishes = self.persists = 0
        self.bridge = XYAnalysisBridge(self)
        self.bridge.refresh()

    def settings(self):
        return self._settings

    def filter_settings(self):
        return self._filters

    def _owns_selection(self):
        return self.page.selection == {self.page.curve.curve_id}

    def sync(self):
        self.bridge.refresh()

    def _records_committed(self):
        self.commits += 1
        self.sync()

    def _publish_progress(self):
        self.publishes += 1

    def _persist(self):
        self.persists += 1

    def _summary(self):
        return 'summary'


class XYAnalysisBridgeTests(unittest.TestCase):
    def setUp(self):
        self.owner = _RecordOwner()
        self.bridge = self.owner.bridge

    def draft(self, field, text):
        token = self.bridge.beginEdit(field)
        self.assertTrue(token)
        self.assertTrue(self.bridge.setDraft(field, text, token))
        return token

    def test_authorization_callback_rechecks_selection_and_progress_before_edit(self):
        for change in ('selection', 'progress'):
            with self.subTest(change=change):
                owner = _RecordOwner()
                bridge = owner.bridge
                token = bridge.beginEdit('widthMin')
                before = dict(bridge._drafts), owner.settings()
                def activate(_owner):
                    if change == 'selection':
                        owner.page.selection = set()
                    else:
                        owner.progress = copy(owner.progress)
                    owner.sync()
                    return True
                with patch.object(owner.page.command_bridge, 'authorize', side_effect=activate):
                    self.assertFalse(bridge.setDraft('widthMin', '.012345678', token))
                self.assertEqual(bridge._drafts, before[0])
                self.assertIs(owner.settings(), before[1])

    def test_atomic_parse_preserves_all_committed_records_on_invalid_draft(self):
        previous = self.owner.settings(), self.owner.filter_settings()
        self.draft('noise', '7.25')
        token = self.draft('maxCount', '2.0')
        self.assertFalse(self.bridge.commit(token))
        self.assertEqual((self.owner.settings(), self.owner.filter_settings()), previous)
        self.assertEqual(self.owner.commits, 0)
        self.assertIn('maxCount', self.bridge.errors)
        self.assertEqual(self.bridge.drafts['noise'], '7.25')
        self.assertTrue(self.bridge.setDraft('maxCount', '2', token))
        self.assertTrue(self.bridge.commit(token))
        self.assertEqual(self.owner.filter_settings().min_noise_strength, 7.25)
        self.assertEqual(self.owner.filter_settings().max_count, 2)
        self.assertEqual(self.owner.filter_settings().preset, 'Custom')
        self.assertEqual(self.owner.settings().prominence, .05)
        self.assertEqual(self.owner.settings().min_distance, 3)
        self.assertEqual(self.owner.settings().max_peaks, 6)

    def test_coupled_width_smoothing_and_finite_constraints(self):
        for field, text in [('widthMin', '5'), ('widthMax', '3')]:
            token = self.draft(field, text)
        self.assertFalse(self.bridge.commit(token))
        self.assertIn('widthMax', self.bridge.errors)
        self.bridge.load(AnalysisSettings(), CandidateFilter())
        for field, text in [('sgWindow', '4'), ('noise', 'nan'), ('sgOrder', '11')]:
            with self.subTest(field=field):
                token = self.draft(field, text)
                self.assertFalse(self.bridge.commit(token))
                self.assertIn(field, self.bridge.errors)
                self.bridge.load(AnalysisSettings(), CandidateFilter())

    def test_selection_roundtrip_retires_editor_and_command_owner(self):
        token = self.draft('noise', '8')
        command_owner = self.bridge.ownerToken
        self.owner.page.selection = {'another-curve'}
        self.owner.sync()
        self.owner.page.selection = {self.owner.page.curve.curve_id}
        self.owner.sync()
        self.assertFalse(self.bridge.setDraft('noise', '9', token))
        self.assertFalse(self.bridge.commit(token))
        self.assertFalse(self.bridge.choose('preset', 'Strict', token))
        self.assertFalse(self.bridge.command('unknown', command_owner))
        self.assertEqual(self.bridge.drafts['noise'], '8')
        self.assertEqual(self.owner.filter_settings().min_noise_strength, 2)

    def test_stable_refresh_and_return_keep_live_editor(self):
        token = self.draft('noise', '3')
        owner = self.bridge.ownerToken
        self.owner.sync()
        self.assertTrue(self.bridge.commit(token))
        self.assertEqual(self.bridge.ownerToken, owner)
        self.assertTrue(self.bridge.setDraft('noise', '4', token))
        self.assertTrue(self.bridge.commit(token))
        self.bridge.endEdit(token)
        self.assertFalse(self.bridge.setDraft('noise', '5', token))

    def test_untouched_display_keeps_precise_records(self):
        exact = .12345678912345678
        self.bridge.load(replace(AnalysisSettings(), width_min=exact), replace(CandidateFilter(), min_noise_strength=exact))
        token = self.draft('widthMin', self.bridge.drafts['widthMin'])
        self.assertTrue(self.bridge.commit(token))
        self.assertEqual(self.owner.settings().width_min, exact)
        self.assertEqual(self.owner.filter_settings().min_noise_strength, exact)

    def test_excess_effective_precision_rejected_without_quantizing(self):
        previous = self.owner.settings(), self.owner.filter_settings()
        for field, text in [('prominence', '0.0301'), ('widthMin', '1.4e-9'), ('noise', '2.125')]:
            with self.subTest(field=field):
                token = self.draft(field, text)
                self.assertFalse(self.bridge.commit(token))
                self.assertIn('decimal places', self.bridge.errors[field])
                self.assertEqual((self.owner.settings(), self.owner.filter_settings()), previous)
                self.bridge.load(*previous)
        token = self.draft('noise', '2.12000')
        self.assertTrue(self.bridge.commit(token))
        self.assertEqual(self.owner.filter_settings().min_noise_strength, 2.12)
        token = self.draft('widthMin', '1e-9')
        self.assertTrue(self.bridge.commit(token))
        self.assertEqual(self.owner.settings().width_min, 1e-9)

    def test_preset_and_step_commit_one_validated_update(self):
        token = self.bridge.beginEdit('preset')
        self.assertTrue(self.bridge.choose('preset', 'Strict', token))
        self.assertEqual(self.owner.filter_settings().min_prominence_fraction, .08)
        self.assertEqual(self.owner.filter_settings().min_noise_strength, 4)
        self.draft('noise', '6')
        token = self.bridge.beginEdit('prominence')
        before = self.owner.commits
        self.assertTrue(self.bridge.step('prominence', 1, token))
        self.assertEqual(self.owner.commits, before + 1)
        self.assertEqual(self.owner.filter_settings().min_prominence_fraction, .09)
        self.assertEqual(self.owner.filter_settings().min_noise_strength, 6)
        self.assertEqual(self.owner.filter_settings().preset, 'Custom')
        token = self.draft('sgWindow', '12')
        before = self.owner.commits
        self.assertFalse(self.bridge.step('sgWindow', 1, token))
        self.assertEqual(self.owner.commits, before)
        self.assertEqual(self.bridge.drafts['sgWindow'], '12')

    def test_own_detector_progress_replacement_keeps_token_external_replacement_retires(self):
        def install_detection():
            self.owner.commits += 1
            captured = replace(self.owner.progress.captured, settings=self.owner.settings(),
                key=(*self.owner._key[:-1], self.owner.settings()))
            self.owner.progress = XYAnalysisProgress(captured)
            self.owner._key = captured.key
            self.owner.sync()
        self.owner._records_committed = install_detection
        token = self.draft('sgWindow', '13')
        owner_token = self.bridge.ownerToken
        self.assertTrue(self.bridge.commit(token))
        self.assertEqual(self.bridge.ownerToken, owner_token)
        self.assertTrue(self.bridge.setDraft('sgWindow', '15', token))
        self.assertTrue(self.bridge.commit(token))
        self.assertEqual(self.owner.settings().sg_window, 15)
        self.owner.progress = replace(self.owner.progress)
        self.owner.sync()
        self.assertFalse(self.bridge.setDraft('sgWindow', '17', token))

    def test_choose_with_pending_draft_installs_once(self):
        self.draft('noise', '6')
        token = self.bridge.beginEdit('model')
        self.assertTrue(self.bridge.choose('model', 'Voigt', token))
        self.assertEqual(self.owner.commits, 1)
        self.assertEqual(self.owner.settings().model, 'Voigt')
        self.assertEqual(self.owner.filter_settings().min_noise_strength, 6)

    def test_source_fingerprint_retires_token_without_settings_churn(self):
        token = self.draft('noise', '3')
        self.owner._key = (*self.owner._key[:-2], 'new-source-digest', self.owner._key[-1])
        self.owner.sync()
        self.assertFalse(self.bridge.commit(token))


class XYSeedsBridgeTests(unittest.TestCase):
    def setUp(self):
        self.owner = _RecordOwner()

    def test_authorization_callback_cannot_edit_or_accept_replaced_seed_owner(self):
        for operation in ('edit', 'accept'):
            with self.subTest(operation=operation):
                owner = _RecordOwner()
                bridge = XYSeedsBridge(owner)
                before = bridge.rows
                identity = owner.progress.seeds[0].seed_id
                def activate(_owner):
                    owner.progress = copy(owner.progress)
                    owner.sync()
                    return True
                with patch.object(owner.page.command_bridge, 'authorize', side_effect=activate):
                    accepted = bridge.edit(identity, 'position', '1.5') if operation == 'edit' else bridge.accept()
                self.assertFalse(accepted)
                self.assertEqual(bridge.rows, before)
                self.assertEqual(owner.persists, 0)

    def test_cancel_and_invalid_accept_are_atomic(self):
        bridge = XYSeedsBridge(self.owner)
        previous = list(self.owner.progress.seeds)
        identity = previous[0].seed_id
        bridge.edit(identity, 'position', '999')
        self.assertFalse(bridge.accept())
        self.assertEqual(self.owner.progress.seeds, previous)
        self.assertEqual(self.owner.persists, 0)
        bridge.cancel()
        self.assertFalse(bridge.edit(identity, 'position', '0'))
        self.assertEqual(self.owner.progress.seeds, previous)

    def test_untouched_full_float_and_independent_bounds_commit_once(self):
        exact = .12345678912345678
        seed = AnalysisSeed(exact, manual=True, center_min=-1.1234567891234567,
                            center_max=1.9876543219876543, amplitude=2.1234567891234567)
        self.owner.progress.set_seeds([seed])
        self.owner.progress.candidate_pool = []
        bridge = XYSeedsBridge(self.owner)
        self.assertTrue(bridge.accept())
        self.assertEqual(self.owner.progress.seeds, [seed])
        self.assertEqual(self.owner.persists, 0)
        self.assertTrue(bridge.canAccept)
        bridge = XYSeedsBridge(self.owner)
        bridge.edit(seed.seed_id, 'centerMax', '3')
        self.assertTrue(bridge.accept())
        committed = self.owner.progress.seeds[0]
        self.assertEqual(committed.seed_id, seed.seed_id)
        self.assertEqual(committed.position, exact)
        self.assertEqual(committed.center_min, seed.center_min)
        self.assertEqual(committed.center_max, 3)
        self.assertEqual(self.owner.persists, 1)
        self.assertEqual(self.owner.page.renders, 1)
        self.assertTrue(bridge.canAccept)

    def test_kind_edit_keeps_id_and_adds_separate_candidate_exclusion(self):
        original = self.owner.progress.seeds[0]
        bridge = XYSeedsBridge(self.owner)
        bridge.edit(original.seed_id, 'kind', 'dip')
        self.assertTrue(bridge.accept())
        edited = next(seed for seed in self.owner.progress.seeds if seed.seed_id == original.seed_id)
        self.assertEqual(edited.kind, 'dip')
        self.assertTrue(edited.manual)
        self.assertIsNone(edited.source_index)
        excluded = next(seed for seed in self.owner.progress.seeds if seed.seed_id != original.seed_id)
        self.assertEqual(excluded.kind, original.kind)
        self.assertFalse(excluded.enabled)
        self.assertEqual(excluded.source_index, original.source_index)
        self.assertNotEqual(excluded.seed_id, edited.seed_id)

    def test_reset_manual_candidate_and_manual_only_seed_behavior(self):
        original = self.owner.progress.seeds[0]
        self.owner.progress.set_seeds([replace(original, position=.1, manual=True), AnalysisSeed(1,manual=True)])
        bridge = XYSeedsBridge(self.owner)
        bridge.setSelected([seed.seed_id for seed in self.owner.progress.seeds])
        bridge.useFilterSelected()
        self.assertTrue(bridge.accept())
        candidate = next(seed for seed in self.owner.progress.seeds if seed.source_index is not None)
        manual = next(seed for seed in self.owner.progress.seeds if seed.source_index is None)
        self.assertEqual(candidate.position, original.position)
        self.assertFalse(candidate.manual)
        self.assertEqual(manual.position, 1)
        self.assertTrue(manual.manual)

    def test_seed_validation_reveals_actual_width_bound_and_position_field(self):
        identity = self.owner.progress.seeds[0].seed_id
        for field, text in [('width', '-1'), ('centerMin', '2'), ('centerMax', '-2'), ('position', '999')]:
            with self.subTest(field=field):
                bridge = XYSeedsBridge(self.owner)
                revealed = []
                bridge.revealRequested.connect(lambda seed, key: revealed.append((seed, key)))
                bridge.edit(identity, field, text)
                self.assertFalse(bridge.accept())
                self.assertIn(f'{identity}.{field}', bridge.errors)
                self.assertEqual(revealed[0], (identity, field))
                self.assertEqual(self.owner.persists, 0)

    def test_owner_replacement_rejects_late_seed_accept(self):
        bridge = XYSeedsBridge(self.owner)
        bridge.add()
        self.owner.progress = replace(self.owner.progress)
        self.assertFalse(bridge.accept())
        self.assertEqual(self.owner.persists, 0)


if __name__ == '__main__':
    unittest.main()
