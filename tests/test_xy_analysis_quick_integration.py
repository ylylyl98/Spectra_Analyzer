"""Production XY record boundaries exercised through real Quick windows."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtCore import QCoreApplication
from tests.quick_migration_case import QuickMigrationCase
from tests import formal_quick_helpers as formal

from tests import test_xy_analysis_workflow as workflow_fixture
from tests.xy_helpers import select_xy_rows
from tests.xy_analysis_quick_helpers import (
    analysis_command, choose_analysis, set_analysis_draft, type_analysis_field,
    run_seed_dialog, type_seed_field, click_quick,
)

APP = QCoreApplication


class XYAnalysisQuickIntegrationTests(QuickMigrationCase):
    setUp = workflow_fixture.XYAnalysisWorkflowTests.setUp
    dispose = workflow_fixture.XYAnalysisWorkflowTests.dispose
    analyze = workflow_fixture.XYAnalysisWorkflowTests.analyze
    fit = workflow_fixture.XYAnalysisWorkflowTests.fit

    def test_atomic_settings_and_retired_owner_cannot_partially_publish(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        original_fit = self.panel.fit_result
        bridge = self.panel.bridge
        width = bridge.beginEdit('widthMin')
        count = bridge.beginEdit('maxCount')
        self.assertTrue(bridge.setDraft('widthMin', '.012345678', width))
        self.assertTrue(bridge.setDraft('maxCount', '1.5', count))
        self.assertFalse(bridge.commit(width))
        self.assertFalse(choose_analysis(self.panel, 'model', 'Voigt'))
        self.assertEqual(self.panel.settings().model, 'Gaussian')
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertIs(self.panel.fit_result, original_fit)
        self.assertIn('maxCount', bridge.errors)
        self.assertTrue(bridge.setDraft('maxCount', '0', count))
        self.assertTrue(bridge.commit(width))
        self.assertEqual(self.panel.settings().width_min, .012345678)
        self.assertEqual(self.page.comparison.analysis_state['settings']['width_min'], .012345678)
        self.assertIsNone(self.panel.fit_result)
        self.assertTrue(set_analysis_draft(self.panel, 'prominence', '.02'))
        with patch.object(self.panel.progress, 'apply_filter', wraps=self.panel.progress.apply_filter) as apply_filter, \
                patch.object(self.page, 'render', wraps=self.page.render) as render:
            self.assertTrue(choose_analysis(self.panel, 'model', 'Lorentzian'))
            self.assertEqual(apply_filter.call_count, 1)
            self.assertEqual(render.call_count, 1)
        self.assertEqual(self.panel.settings().model, 'Lorentzian')
        self.assertEqual(self.panel.filter_settings().min_prominence_fraction, .02)
        committed = deepcopy(self.page.comparison.analysis_state)
        stale_choice = bridge.beginEdit('model')
        self.assertTrue(stale_choice)
        stale_command = bridge.ownerToken
        select_xy_rows(self.page, [1]); select_xy_rows(self.page, [0])
        self.assertFalse(bridge.setDraft('widthMin', '.2', width))
        self.assertFalse(bridge.choose('model', 'Voigt', stale_choice))
        self.assertFalse(bridge.command('find', stale_command))
        self.assertEqual(self.page.comparison.analysis_state, committed)
        self.assertFalse(self.w._background_jobs.busy)

    def test_modeless_close_reopen_and_project_save_keep_committed_progress(self):
        self.fit()
        saved = deepcopy(self.page.comparison.analysis_state)
        fitted = self.panel.fit_result.y_fit.copy()
        window, field = type_analysis_field(self.panel, 'widthMin', '-')
        dialog = self.panel.details_dialog
        QTest.keyClick(window, Qt.Key_Escape); APP.processEvents()
        self.assertFalse(window.isVisible())
        self.assertTrue(analysis_command(self.panel, 'settings'))
        self.assertIs(self.panel.details_dialog, dialog)
        self.assertTrue(window.isVisible())
        self.assertEqual(field.property('text'), '-')
        target = self.folder / 'unfinished-analysis.spectra'
        comparison_id = self.page.comparison_id
        self.w.save_project(target)
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertTrue(self.w.open_project(target))
        self.w.open_xy_comparison(comparison_id)
        self.page = self.w.graph_documents.document_for(self.w.graph_documents.registry.active_key)
        self.panel = self.page.analysis_controller
        formal.pump(80)
        self.assertTrue(self.page.resume_analysis_action.isEnabled(),
            dict(services=self.panel.services is not None, busy=self.w._project_busy(),
                 payload=self.page.comparison.analysis_state is not None, state=self.panel.bridge.state))
        self.page.resume_analysis_action.trigger()
        APP.processEvents()
        self.assertEqual(self.page.comparison.analysis_state, saved)
        self.assertIsNotNone(self.panel.fit_result, self.panel.bridge.state)
        np.testing.assert_array_equal(self.panel.fit_result.y_fit, fitted)
        self.assertNotEqual(self.panel.bridge.drafts['widthMin'], '-')

    def test_seed_transaction_cancel_validation_reset_ids_and_untouched_precision(self):
        self.fit()
        candidate = self.panel.progress.seeds[0]
        precise = replace(candidate, position=.713456789012345, width=.123456789012345,
                          center_min=.601234567890123, center_max=.812345678901234, manual=True)
        self.panel.set_seeds([precise])
        saved = deepcopy(self.page.comparison.analysis_state)
        def cancel(dialog):
            bridge = dialog.bridge
            self.assertEqual(bridge.rows[0]['position'], format(precise.position, '.10g'))
            bridge.add()
            added = bridge.rows[-1]
            self.assertNotEqual(added['seedId'], precise.seed_id)
            self.assertEqual(float(added['position']), float(np.median(self.panel.captured.data.x)))
            bridge.setSelected([precise.seed_id]); bridge.excludeSelected()
            self.assertEqual(self.page.comparison.analysis_state, saved)
            click_quick(dialog, 'xy-seed-cancel')
        run_seed_dialog(self.panel, cancel)
        self.assertEqual(self.panel.progress.seeds, [precise])
        self.assertEqual(self.page.comparison.analysis_state, saved)
        def unchanged(dialog):
            click_quick(dialog, 'xy-seed-accept')
            self.assertFalse(dialog.window().isVisible())
        run_seed_dialog(self.panel, unchanged)
        self.assertEqual(self.panel.progress.seeds, [precise])
        self.assertEqual(self.page.comparison.analysis_state, saved)
        def validate_and_reset(dialog):
            type_seed_field(dialog, precise.seed_id, 'position', 'nan')
            click_quick(dialog, 'xy-seed-accept')
            self.assertTrue(dialog.window().isVisible())
            self.assertTrue(dialog.bridge.errors)
            self.assertEqual(self.page.comparison.analysis_state, saved)
            type_seed_field(dialog, precise.seed_id, 'position', '.72')
            dialog.bridge.setSelected([precise.seed_id])
            dialog.bridge.useFilterSelected()
            self.assertEqual(dialog.bridge.rows[0]['seedId'], precise.seed_id)
            self.assertEqual(dialog.bridge.rows[0]['position'], format(candidate.position, '.10g'))
            # Reset restores only candidate position/manual override, retaining other exact values.
            with patch.object(self.page, 'render', wraps=self.page.render) as render:
                click_quick(dialog, 'xy-seed-accept')
                self.assertEqual(render.call_count, 1)
            self.assertFalse(dialog.window().isVisible())
        run_seed_dialog(self.panel, validate_and_reset)
        restored = self.panel.progress.seeds[0]
        self.assertEqual(restored.seed_id, precise.seed_id)
        self.assertEqual(restored.position, candidate.position)
        self.assertFalse(restored.manual)
        self.assertEqual(restored.width, precise.width)
        self.assertEqual((restored.center_min, restored.center_max), (precise.center_min, precise.center_max))
        added_ids = []
        def add_and_exclude(dialog):
            bridge = dialog.bridge
            bridge.add()
            added = bridge.rows[-1]
            added_ids.append(added['seedId'])
            bridge.setSelected([added['seedId']]); bridge.useFilterSelected()
            self.assertEqual(bridge.rows[-1], added, 'Manual-only seeds cannot reset to detector candidates')
            bridge.setSelected([precise.seed_id]); bridge.excludeSelected()
            click_quick(dialog, 'xy-seed-accept')
            self.assertFalse(dialog.window().isVisible())
        run_seed_dialog(self.panel, add_and_exclude)
        self.assertFalse(next(seed for seed in self.panel.progress.seeds if seed.seed_id == precise.seed_id).enabled)
        accepted = self.panel.progress.accepted_seeds()
        self.assertEqual([(seed.seed_id, seed.position, seed.manual) for seed in accepted], [(added_ids[0], 0., True)])
        self.assertEqual(len({seed.seed_id for seed in self.panel.progress.seeds}), len(self.panel.progress.seeds))


if __name__ == '__main__':
    unittest.main()
