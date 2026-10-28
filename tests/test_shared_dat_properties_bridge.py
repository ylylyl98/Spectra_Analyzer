"""Scientific/owner integration boundary for the shared DAT Quick bridge."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
import unittest
from unittest.mock import patch

import numpy as np

from tests.quick_migration_case import QuickMigrationCase
from tests.formal_quick_helpers import item, pump


class SharedDatPropertiesBridgeTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        self.w._show_error = self.fail
        self.path = self.folder / 'shared.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)))
        self.w._load_dat_file_direct(self.path)
        self.w.open_graph_document()
        self.w.request_task(2)
        self.w.request_outer_view('analysis')
        pump(60)
        self.s = self.w.workspace.active
        self.b = self.w.dat_properties
        self.w._plot_render_timer.stop()

    def tearDown(self):
        self.assertFalse(self.quick_errors)
        super().tearDown()

    def test_projection_is_four_decimal_text_but_unrelated_color_preserves_exact_axes(self):
        xlim, ylim = (1.123456789, 1.876543219), (.123456789, 1.876543219)
        self.w._heat_ax.set_xlim(xlim)
        self.w._heat_ax.set_ylim(ylim)
        self.w.capture_active_dataset(render=False)
        self.assertEqual(self.b.state['xmin'], '1.1235')
        self.assertEqual(self.s.view.x_limits, xlim)
        self.assertTrue(self.b.setChoice('mapping', 'symlog', self.b.ownerToken))
        np.testing.assert_array_equal(self.w._heat_ax.get_xlim(), xlim)
        np.testing.assert_array_equal(self.w._heat_ax.get_ylim(), ylim)

    def test_projection_round_trip_is_noop_and_keeps_color_unfixed(self):
        self.s.view.bounds['vmin'] = 1.123456789
        self.s.view.locks['vmin'] = False
        self.b.draftNumber('vmin', '99', self.b.ownerToken)
        self.b.draftNumber('vmin', '1.1235', self.b.ownerToken)
        self.assertEqual(self.b.validate_editor()['numbers'], {})
        self.assertTrue(self.b.commitNumber('vmin', self.b.ownerToken))
        self.assertEqual(self.s.view.bounds['vmin'], 1.123456789)
        self.assertFalse(self.s.view.locks['vmin'])

    def test_real_quick_fractional_typing_preserves_every_keystroke(self):
        from tests.dat_properties_quick_helpers import edit_number
        self.w.root_window.resize(1250, 920)
        self.w.root_window.show()
        pump()
        edit_number(self.w, 'xmin', '1.1234')
        self.assertEqual(self.b.state['xmin'], '1.1234')
        self.assertEqual(self.s.view.bounds['xmin'], 1.)
        self.assertTrue(self.b.commitNumber('xmin', self.b.ownerToken), self.b.errorText)
        self.assertEqual(self.s.view.bounds['xmin'], 1.1234)

    def test_explicit_color_draft_and_implicit_fix_are_one_undo(self):
        before = self.s.view.bounds['vmin']
        self.w._clear_edit_history(self.s)
        self.b.draftNumber('vmin', '3.1250', self.b.ownerToken)
        self.assertTrue(self.b.commitNumber('vmin', self.b.ownerToken), self.b.errorText)
        self.assertTrue(self.s.view.locks['vmin'])
        self.assertEqual(self.s.view.bounds['vmin'], 3.125)
        self.assertTrue(self.w.undo_edit())
        self.assertFalse(self.s.view.locks['vmin'])
        self.assertAlmostEqual(self.s.view.bounds['vmin'], before)
        self.assertFalse(self.w.undo_edit())

    def test_shared_and_formal_all_draft_validation_precedes_any_effect(self):
        self.assertEqual(self.w.outerView, 'analysis')
        self.w.dat_details_action.setChecked(True)
        self.assertTrue(self.w.request_task(2))
        pump(60)
        source = item(self.w, 'formalProperties')
        self.assertTrue(source.isVisible())
        formal = self.w.plot_page.formal
        self.assertIs(source.property('bridge'), formal)

        def draft_width(text):
            owner = formal.capturePropertiesInput(source)
            self.assertTrue(owner)
            self.assertTrue(formal.propertiesInputCurrent(source, owner))
            formal.setDraft('width_in', text, source, owner)
            self.assertEqual(formal._drafts['width_in'], text)

        before = deepcopy(self.s.view)
        self.b.draftNumber('xmin', '1.2345', self.b.ownerToken)
        self.b.draftNumber('vmax', '-', self.b.ownerToken)
        with self.assertRaises(ValueError):
            self.w._finish_graph_editors(capture_science=False)
        self.assertEqual(self.s.view, before)
        self.b.draftNumber('vmax', '30', self.b.ownerToken)
        draft_width('-')
        with self.assertRaises(ValueError):
            self.w._finish_graph_editors(capture_science=False)
        self.assertEqual(self.s.view, before)
        draft_width('3.5')
        self.w._finish_graph_editors(capture_science=False)
        self.assertEqual(self.s.view.bounds['xmin'], 1.2345)
        self.assertEqual(self.s.view.bounds['vmax'], 30)
        self.assertTrue(self.s.view.locks['vmax'])
        self.assertAlmostEqual(self.s.presentation.width_in, 3.5)

    def test_stale_owner_and_disabled_analysis_host_reject_mutations(self):
        stale = self.b.ownerToken
        self.w.create_graph('Second')
        before = deepcopy(self.s.view)
        self.assertNotEqual(stale, self.b.ownerToken)
        self.assertFalse(self.b.setLabel('plot_title', 'old owner', stale))
        self.b.draftNumber('xmin', '8', stale)
        self.assertEqual(self.s.view, before)
        source_graph = self.s.active_graph
        source_view = deepcopy(source_graph.view)
        self.w._open_analysis(('heatmap', (self.s.dataset_id, source_graph.graph_id)))
        self.w.request_task(2)
        pump(60)
        self.assertTrue(self.w.workbenchActive)
        self.assertIsNotNone(self.w._workbench_document())
        self.assertFalse(item(self.w, 'sharedDatProperties').isEnabled())
        readonly = deepcopy(self.s.view)
        self.assertFalse(self.b.setLabel('plot_title', 'disabled', self.b.ownerToken))
        self.assertFalse(self.b.setChoice('colormap', 'jet', self.b.ownerToken))
        self.assertEqual(self.s.view, readonly)
        self.assertEqual(source_graph.view, source_view)

    def test_pending_import_keeps_outgoing_labels_colors_ranges_and_frozen_defaults(self):
        defaults = self.w.snapshot_dataset_defaults()
        frozen = deepcopy(defaults)
        self.w._set_dat_import_pending(True)
        try:
            self.assertTrue(self.b.setLabel('plot_title', 'outgoing latest', self.b.ownerToken))
            self.assertTrue(self.b.setChoice('colormap', 'jet', self.b.ownerToken))
            self.b.draftNumber('xmin', '1.2', self.b.ownerToken)
            self.assertTrue(self.b.commitNumber('xmin', self.b.ownerToken), self.b.errorText)
            self.assertFalse(self.b.setChoice('axis_override', 'meV', self.b.ownerToken))
            self.assertEqual(self.s.view.annotations['plot_title'], 'outgoing latest')
            self.assertEqual(defaults, frozen)
        finally:
            self.w._set_dat_import_pending(False)

    def test_label_typing_is_cheap_and_finishes_once(self):
        cube = self.s.current_cube
        with patch.object(self.w, '_schedule_plot_page_render') as render:
            self.b.setLabel('plot_title', '$literal$', self.b.ownerToken)
            self.b.setLabel('plot_title', '$literal text$', self.b.ownerToken)
            render.assert_not_called()
            self.b.finishLabels(self.b.ownerToken)
            self.assertEqual(render.call_count, 1)
            self.b.finishLabels(self.b.ownerToken)
            self.assertEqual(render.call_count, 1)
        self.assertIs(self.s.current_cube, cube)
        self.assertEqual(self.s.view.annotations['plot_title'], '$literal text$')

    def test_axis_pan_invalidates_analysis_and_auto_view_restores_unfixed_exact_bounds(self):
        with patch.object(self.w, '_invalidate_analysis_jobs') as invalidate:
            self.w._heat_ax.set_xlim((1.123456789, 1.876543219))
            self.assertGreater(invalidate.call_count, 0)
        self.w._heat_ax.set_ylim((.25, 1.75))
        self.s.view.locks['xmin'] = True
        self.assertTrue(self.b.command('auto_view', self.b.ownerToken))
        self.assertEqual(self.w._heat_ax.get_xlim()[0], 1.123456789)
        self.assertAlmostEqual(self.w._heat_ax.get_xlim()[1], 2.1)
        self.assertEqual(tuple(self.w._heat_ax.get_ylim()), (0., 2.))

    def test_runtime_drafts_and_disclosures_are_stable_across_revisions(self):
        token = self.b.ownerToken
        self.b.draftNumber('xmin', '-', token)
        self.b.setDisclosure('source_axis', True, token)
        self.w._invalidate_plot_page()
        self.assertEqual(self.b.ownerToken, token)
        self.assertEqual(self.b.state['xmin'], '-')
        self.assertTrue(self.b.state['disclosures']['source_axis'])
        self.assertTrue(self.b.has_project_drafts())


if __name__ == '__main__':
    unittest.main()
