"""Production project rollback preserves XY controller runtime authority."""
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtCore import QCoreApplication
from tests.quick_migration_case import QuickMigrationCase
from spectra_app.project_io import read_project
from spectra_app.xy_analysis_bridge import XYSeedsBridge
from tests import test_xy_analysis_workflow as workflow
from tests.xy_helpers import set_xy_draft
from tests.xy_analysis_quick_helpers import set_analysis_draft


class XYControllerRecoveryTests(QuickMigrationCase):
    setUp = workflow.XYAnalysisWorkflowTests.setUp
    dispose = workflow.XYAnalysisWorkflowTests.dispose
    analyze = workflow.XYAnalysisWorkflowTests.analyze
    fit = workflow.XYAnalysisWorkflowTests.fit

    def test_failed_install_keeps_exact_controller_fit_progress_and_pending_drafts(self):
        self.fit()
        path = self.w.save_project(self.folder / 'retained-controller.spectra')
        controller, analysis = self.page, self.panel
        progress, fit, captured = analysis.progress, analysis.fit_result, analysis.captured
        source = captured.data.y
        source_bytes = source.tobytes()
        inspector_drafts, analysis_drafts = controller.inspector_bridge._drafts, analysis.bridge._drafts
        set_xy_draft(self.page, 'color', 'invalid color')
        self.assertTrue(set_analysis_draft(analysis, 'widthMin', '-'))
        command_owner = controller.command_bridge.ownerToken
        canvas_owner = controller.canvas_bridge.ownerToken
        inspector_owner = controller.inspector_bridge.inputOwner
        setting_token = analysis.bridge.beginEdit('widthMin')
        seeds = XYSeedsBridge(analysis)
        seed_id = progress.seeds[0].seed_id
        lease = self.w.graph_documents.registry.capture_input(('xy', controller.comparison_id, ''))
        candidate = read_project(path)
        with patch.object(self.w, '_project_installed_workspace', side_effect=ValueError('injected rollback')):
            with self.assertRaisesRegex(ValueError, 'injected rollback'):
                self.w._install_project(candidate, path)
        QCoreApplication.processEvents()
        restored = self.w.graph_documents.document_for(('xy', controller.comparison_id, ''))
        self.assertIs(restored, self.page)
        self.assertIs(restored, controller)
        self.assertIs(controller.analysis_controller, analysis)
        self.assertIs(analysis.progress, progress)
        self.assertIs(analysis.fit_result, fit)
        self.assertIs(analysis.captured, captured)
        self.assertIs(analysis.captured.data.y, source)
        self.assertEqual(source.tobytes(), source_bytes)
        self.assertIs(controller.inspector_bridge._drafts, inspector_drafts)
        self.assertIs(analysis.bridge._drafts, analysis_drafts)
        self.assertEqual(inspector_drafts['color'], 'invalid color')
        self.assertEqual(analysis_drafts['widthMin'], '-')
        self.assertFalse(controller.command_bridge.invoke('home', command_owner))
        self.assertFalse(controller.canvas_bridge.keyEvent('press', Qt.Key_Delete.value, '', 0, canvas_owner))
        self.assertFalse(controller.inspector_bridge.draftFor('color', '#123456', inspector_owner))
        self.assertFalse(analysis.bridge.setDraft('widthMin', '.1', setting_token))
        self.assertFalse(seeds.edit(seed_id, 'position', '1.5'))
        self.assertFalse(seeds.accept())
        self.assertFalse(self.w.graph_documents.registry.authorize(lease))
        self.assertNotEqual(self.w.graph_documents.registry.capture_input(lease.key), lease)
        self.assertNotEqual(controller.command_bridge.ownerToken, command_owner)
        # Correct through the renewed real presentation and save without replacing science.
        set_xy_draft(self.page, 'color', '#123456')
        self.assertTrue(set_analysis_draft(analysis, 'widthMin', '.000000001', commit=True))
        self.w.save_project(self.folder / 'recovered-controller.spectra')
        self.assertIs(analysis.progress, progress)
        self.assertIs(analysis.fit_result, fit)
