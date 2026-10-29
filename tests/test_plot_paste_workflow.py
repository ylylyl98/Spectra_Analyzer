"""Exact graph paste, revision and whole-operation rollback at the real host boundary."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import replace
import unittest
from unittest.mock import patch
from tests.quick_migration_case import QuickMigrationCase
from spectra_app.plot_templates import FormatApplicationOptions


def layout_only(enabled=True):
    return FormatApplicationOptions(layout=enabled, typography=False, axis=False,
        colormap=False, colorbar=False)


class PlotPasteWorkflowTests(QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        for name in ('a', 'b'):
            path = self.folder / (name + '.dat')
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)))
            self.w._load_dat_file_direct(path)
        self.a, self.b = self.w.workspace.datasets.values()
        self.w.open_graph_document(self.a.dataset_id)
        self.w.open_graph_document(self.b.dataset_id)
        self.w.set_plot_presentation(replace(self.b.presentation, width_in=5.1, aspect_locked=False))
        self.w.copy_plot_settings()
        graph = deepcopy(self.a.active_graph)
        graph.graph_id, graph.name = 'second', 'Graph 2'
        graph.presentation = replace(graph.presentation, width_in=4.1, aspect_locked=False)
        self.a.graphs[graph.graph_id] = graph
        self.targets = [(self.a.dataset_id, 'default'), (self.a.dataset_id, 'second'),
                        (self.b.dataset_id, 'default')]

    def tearDown(self):
        self.assertEqual(self.quick_errors, [])
        super().tearDown()

    def test_exact_batch_adjusts_frozen_source_and_undo_restores_independent_baselines(self):
        w = self.w
        originals = {key: deepcopy(w.workspace.datasets[key[0]].graphs[key[1]].presentation)
                     for key in self.targets}
        active = (w.workspace.active_dataset_id, self.a.active_graph_id, self.b.active_graph_id)
        history_before = {s.dataset_id: tuple(w._history_for(s).undo_stack) for s in (self.a, self.b)}
        report = w.paste_plot_settings(graph_targets=self.targets, options=layout_only())
        self.assertTrue(all(x['success'] for x in report), report)
        self.assertEqual({(x['dataset_id'], x['graph_id']) for x in report}, set(self.targets))
        self.assertEqual((w.workspace.active_dataset_id, self.a.active_graph_id, self.b.active_graph_id), active)
        self.assertEqual(self.a.graphs['second'].presentation.width_in, 5.1)
        self.assertEqual(len(w._history_for(self.a).undo_stack), len(history_before[self.a.dataset_id])+2)
        self.assertTrue(all(x['success'] for x in w.adjust_last_paste(layout_only(False))))
        for key, old in originals.items():
            self.assertEqual(w.workspace.datasets[key[0]].graphs[key[1]].presentation, old)
        self.assertEqual(tuple(w._history_for(self.a).undo_stack), history_before[self.a.dataset_id])
        # New clipboard capture must not change the operation's frozen source.
        w._plot_settings_clipboard = replace(w._plot_settings_clipboard,
            style=replace(w._plot_settings_clipboard.style, width_in=6.2))
        self.assertTrue(all(x['success'] for x in w.adjust_last_paste(layout_only())))
        self.assertEqual(self.a.graphs['second'].presentation.width_in, 5.1)
        self.assertTrue(w._settings_undo_paste_action.isEnabled())
        w._settings_undo_paste_action.trigger()
        self.assertIsNone(w._latest_paste)
        for key, old in originals.items():
            self.assertEqual(w.workspace.datasets[key[0]].graphs[key[1]].presentation, old)
        for s in (self.a, self.b):
            self.assertEqual(tuple(w._history_for(s).undo_stack), history_before[s.dataset_id])

    def test_stale_one_target_prevents_whole_batch_restore(self):
        w = self.w
        w.paste_plot_settings(graph_targets=self.targets, options=layout_only())
        changed = replace(self.a.graphs['second'].presentation, text_pt=17)
        self.a.graphs['second'].presentation = changed
        before = deepcopy(self.a.graphs['default'].presentation)
        with self.assertRaisesRegex(ValueError, 'changed|edit|stale'):
            w.undo_last_paste()
        self.assertEqual(self.a.graphs['default'].presentation, before)
        self.assertEqual(self.a.graphs['second'].presentation, changed)

    def test_applied_save_failure_remains_undoable(self):
        w = self.w
        before = deepcopy(self.a.presentation)
        with patch.object(w, '_save_plot_dataset', side_effect=OSError('disk full')):
            report = w.paste_plot_settings(graph_targets=self.targets[:2], options=layout_only())
        self.assertTrue(all(not x['success'] and x['applied'] for x in report), report)
        self.assertTrue(w.undo_last_paste())
        self.assertEqual(self.a.presentation, before)

    def test_confirmed_options_roundtrip_and_malformed_values_use_defaults(self):
        w = self.w
        from tests.quick_application_helpers import application
        w._save_paste_options(layout_only(False))
        with patch('tests.quick_application_helpers.isolated_settings', lambda: nullcontext(self.folder)):
            other, _, errors = self.enterContext(application())
        self.assertEqual(other.controller._paste_options(), layout_only(False))
        self.assertEqual(errors, [])
        w._annotation_settings.setValue('plot_formats/paste_options', '{"layout": "yes"}')
        self.assertEqual(w._paste_options(), FormatApplicationOptions())

    def test_partial_deselection_restores_each_original_and_retains_selected_group(self):
        w = self.w
        self.a.graphs['default'].presentation = replace(self.a.presentation, text_pt=11)
        self.a.graphs['second'].presentation = replace(self.a.graphs['second'].presentation, text_pt=12)
        original = {key: w.workspace.datasets[key[0]].graphs[key[1]].presentation for key in self.targets}
        options = replace(layout_only(), typography=True)
        report = w.paste_plot_settings(graph_targets=self.targets, options=options)
        self.assertTrue(all(x['success'] for x in report), report)
        self.assertEqual(self.a.graphs['second'].presentation.text_pt, self.b.presentation.text_pt)
        revised = w.adjust_last_paste(replace(options, layout=False))
        self.assertTrue(all(x['success'] for x in revised), revised)
        for key in self.targets:
            value = w.workspace.datasets[key[0]].graphs[key[1]].presentation
            self.assertEqual(value.width_in, original[key].width_in)
            self.assertEqual(value.text_pt, self.b.presentation.text_pt)
        # Repeated revision retains one contribution per changed graph.
        count = len(w._history_for(self.a).undo_stack)
        w.adjust_last_paste(replace(options, layout=False))
        self.assertEqual(len(w._history_for(self.a).undo_stack), count)
        self.assertTrue(w.undo_last_paste())
        for key in self.targets:
            self.assertEqual(w.workspace.datasets[key[0]].graphs[key[1]].presentation, original[key])

    def test_quick_action_uses_preferences_and_only_current_graph_cancel_never_saves(self):
        w = self.w
        w._save_paste_options(layout_only())
        w.set_plot_presentation(replace(self.b.presentation, width_in=6.3))
        before = self.a.presentation
        with patch.object(w, 'create_paste_settings_dialog', side_effect=AssertionError('Quick paste opened options')):
            w._on_paste_plot_settings()
        self.assertEqual(self.b.presentation.width_in, 5.1)
        self.assertEqual(self.a.presentation, before)
        self.assertEqual(set(w._latest_paste.baselines), {self.targets[-1]})
        dialog = w.create_paste_settings_dialog(current_only=True)
        self.addCleanup(dialog.close)
        dialog.bridge.option_items['typography'].setChecked(True)
        with patch.object(w, 'create_paste_settings_dialog', return_value=dialog), patch.object(dialog, 'exec', return_value=0):
            w._show_paste_settings_dialog(current_only=True)
        self.assertEqual(w._paste_options(), layout_only())

    def test_history_undo_and_redo_invalidate_latest_operation(self):
        w = self.w
        w.paste_plot_settings(graph_targets=self.targets[:2], options=layout_only())
        w.activate_dataset(self.a.dataset_id)
        self.assertTrue(w.undo_edit())
        with self.assertRaisesRegex(ValueError, 'changed|history'):
            w.adjust_last_paste(layout_only(False))
        self.assertTrue(w.redo_edit())
        # Even restoring the same values cannot reclaim a foreign history path.
        with self.assertRaisesRegex(ValueError, 'changed|history'):
            w.undo_last_paste()

    def test_target_and_science_changes_block_before_any_restore(self):
        w = self.w
        w.paste_plot_settings(graph_targets=self.targets, options=layout_only())
        before = self.a.presentation
        graph = self.a.graphs['second']
        self.a.graphs['second'] = deepcopy(graph)
        with self.assertRaisesRegex(ValueError, 'deleted|replaced'):
            w.undo_last_paste()
        self.assertEqual(self.a.presentation, before)
        self.a.graphs['second'] = graph
        w._invalidate_dataset_documents(self.b.dataset_id)
        with self.assertRaisesRegex(ValueError, 'data|changed'):
            w.undo_last_paste()
        self.assertEqual(self.a.presentation, before)

    def test_failed_target_is_not_owned_and_atomic_adjust_failure_keeps_all_targets(self):
        w = self.w
        missing = (self.a.dataset_id, 'missing')
        report = w.paste_plot_settings(graph_targets=[self.targets[0], missing], options=layout_only())
        self.assertTrue(report[0]['applied'])
        self.assertFalse(report[1]['applied'])
        self.assertEqual(set(w._latest_paste.baselines), {self.targets[0]})
        w.paste_plot_settings(graph_targets=self.targets, options=layout_only())
        before = {key: deepcopy(w.workspace.datasets[key[0]].graphs[key[1]].presentation) for key in self.targets}
        with patch('spectra_app.main_window.prepare_heatmap_page', side_effect=ValueError('invalid render')):
            outcomes = w.adjust_last_paste(layout_only(False))
        self.assertTrue(all(not item['applied'] for item in outcomes), outcomes)
        for key in self.targets:
            self.assertEqual(w.workspace.datasets[key[0]].graphs[key[1]].presentation, before[key])
        self.assertTrue(w.undo_last_paste())

    def test_quick_missing_source_fields_masks_application_and_retains_intent(self):
        w = self.w
        intent = replace(layout_only(), x_limits=True, y_limits=True, title_position=True)
        w._save_paste_options(intent)
        w._plot_settings_clipboard = replace(w._plot_settings_clipboard,
            x_limits=None, y_limits=None, title_position=None)
        w.set_plot_presentation(replace(self.b.presentation, width_in=6.1))
        before_limits = self.b.view.x_limits, self.b.view.y_limits, self.b.presentation.title_position
        with patch.object(w, 'create_paste_settings_dialog', side_effect=AssertionError('Unexpected options')):
            w._on_paste_plot_settings()
        self.assertEqual(self.b.presentation.width_in, 5.1)
        self.assertEqual((self.b.view.x_limits, self.b.view.y_limits, self.b.presentation.title_position), before_limits)
        self.assertEqual(w._paste_options(), intent)
        self.assertEqual(w._latest_paste.options, intent)
        self.assertTrue(all(item['success'] for item in w.adjust_last_paste(intent)))
        self.assertEqual(w._latest_paste.options, intent)

    def test_current_dialog_pairs_survive_unchanged_graph_mode_adjustment(self):
        from spectra_app.plot_presentation import PlotAnnotation
        w = self.w
        source = (PlotAnnotation('source', text='note', position=(.2, .7)),
                  PlotAnnotation('source-arrow', kind='arrow', text='arrow', position=(.3, .6), target=(.7, .1)))
        w.set_plot_presentation(replace(self.b.presentation, annotations=source))
        w.copy_plot_settings()
        w.activate_dataset(self.a.dataset_id)
        original = (PlotAnnotation('target', text='note', position=(.8, .1)),
                    PlotAnnotation('target-arrow', kind='arrow', text='arrow', position=(.9, .2), target=(.11, .22)))
        w.set_plot_presentation(replace(self.a.presentation, annotations=original))
        w._save_paste_options(replace(layout_only(False), annotation_positions=True))
        dialog = w.create_paste_settings_dialog(current_only=True)
        self.addCleanup(dialog.close)
        for pair in dialog.bridge.pairs:
            target = {'source': 'target', 'source-arrow': 'target-arrow'}[pair.source_id]
            pair.setCurrentIndex(pair.target_ids.index(target))
        self.assertEqual(dialog.annotation_pairs()[self.a.dataset_id], {'source': 'target', 'source-arrow': 'target-arrow'})
        with patch.object(w, 'create_paste_settings_dialog', return_value=dialog), patch.object(dialog, 'exec', return_value=1):
            w._show_paste_settings_dialog(current_only=True)
        key = (self.a.dataset_id, self.a.active_graph_id)
        self.assertEqual(w._latest_paste.pairs[key], {'source': 'target', 'source-arrow': 'target-arrow'})
        pasted = self.a.presentation.annotations
        self.assertEqual(pasted[0].position, (.2, .7))
        self.assertEqual(pasted[1].target, original[1].target)
        adjust = w.create_adjust_paste_dialog()
        self.addCleanup(adjust.close)
        self.assertEqual(adjust.annotation_pairs()[key], w._latest_paste.pairs[key])
        with patch.object(w, 'create_adjust_paste_dialog', return_value=adjust), patch.object(adjust, 'exec', return_value=1):
            w._on_adjust_last_paste()
        self.assertEqual(self.a.presentation.annotations, pasted)
        self.assertEqual(self.a.presentation.annotations[0].text, original[0].text)
        self.assertEqual(self.a.presentation.annotations[1].target, original[1].target)

    def test_pending_numeric_editor_disables_adjust_and_prevents_rollback(self):
        w = self.w
        w.paste_plot_settings(options=layout_only())
        self.assertTrue(w._settings_adjust_action.isEnabled())
        w.dat_properties.draftNumber('xmin', '1.4', w.dat_properties.ownerToken)
        self.assertFalse(w._settings_adjust_action.isEnabled())
        before = self.b.presentation
        with self.assertRaisesRegex(ValueError, 'pending edit'):
            w.undo_last_paste()
        self.assertEqual(self.b.presentation, before)

    def test_bound_analysis_target_is_rejected_and_preparation_never_mutates_live_graph(self):
        from spectra_app.analysis_document import ensure_analysis_graph
        from spectra_app.plot_paste import graph_candidate
        w = self.w
        linked = ensure_analysis_graph(self.a, 'default')
        self.a.activate_graph('default')
        linked.view.colormap = 'magma'
        linked.presentation = replace(linked.presentation, width_in=6.7)
        before_view, before_page = deepcopy(linked.view), linked.presentation
        candidate = graph_candidate(self.a, linked.graph_id)
        self.assertIsNot(candidate.active_graph, linked)
        self.assertEqual(linked.view, before_view)
        self.assertEqual(linked.presentation, before_page)
        key = (self.a.dataset_id, linked.graph_id)
        self.assertNotIn(key, w._paste_graph_targets())
        report = w.paste_plot_settings(graph_targets=[self.targets[0], key], options=layout_only())
        self.assertTrue(report[0]['success'], report)
        self.assertFalse(report[1]['applied'])
        self.assertIn('source graph', report[1]['error'])
        self.assertEqual(linked.view, before_view)
        self.assertEqual(linked.presentation, before_page)
        w.activate_dataset(self.a.dataset_id)
        w.activate_graph(linked.graph_id)
        self.assertFalse(w._settings_paste_action.isEnabled())
        self.assertFalse(w._settings_paste_options_action.isEnabled())
        self.assertTrue(w._settings_paste_to_action.isEnabled())
        self.assertIn('source graph', w._settings_paste_action.toolTip())
        w.activate_graph('default')
        # A detached standalone analysis owns its own format and remains supported.
        linked.analysis.source_graph_id = None
        report = w.paste_plot_settings(graph_targets=[key], options=layout_only())
        self.assertTrue(report[0]['success'], report)
        self.a.activate_graph(linked.graph_id)
        self.assertEqual(self.a.presentation.width_in, 5.1)

    def test_pending_range_color_and_gate_drafts_protect_whole_batch(self):
        w = self.w
        w.paste_plot_settings(graph_targets=self.targets, options=layout_only())
        baseline = {key: deepcopy(w.workspace.datasets[key[0]].graphs[key[1]].presentation)
                    for key in self.targets}
        for field, text in (('xmin', '1.4'), ('vmin', '-99')):
            w.dat_properties.draftNumber(field, text, w.dat_properties.ownerToken)
            self.assertFalse(w._settings_adjust_action.isEnabled())
            with self.assertRaisesRegex(ValueError, 'pending edit'):
                w.adjust_last_paste(layout_only(False))
            for key in self.targets:
                self.assertEqual(w.workspace.datasets[key[0]].graphs[key[1]].presentation, baseline[key])
            w.dat_properties._drafts.clear()
        changed_index = '1' if w._current_gate_index() != 0 else '2'
        self.assertTrue(w.analysis_header.setDraft('index', changed_index, w.analysis_header.ownerToken))
        self.assertTrue(w.analysis_header.has_project_drafts())
        with self.assertRaisesRegex(ValueError, 'pending edit'):
            w.undo_last_paste()
        for key in self.targets:
            self.assertEqual(w.workspace.datasets[key[0]].graphs[key[1]].presentation, baseline[key])


if __name__ == '__main__':
    unittest.main()
