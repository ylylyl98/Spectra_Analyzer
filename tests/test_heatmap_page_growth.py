"""Measured page growth must retain physical frames and graph ownership."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication

from spectra_app import svg_export
from spectra_app.main_window import MainWindow
from spectra_app.plot_presentation import AxisPresentation, ColorRegion, HeatmapSnapshot, PlotPresentation
from spectra_app.plot_state import active_plot_recipe
from tests.settings_helpers import isolate_settings

APP = QApplication.instance() or QApplication([])


class HeatmapPageGrowthTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        self.w = MainWindow()
        self.addCleanup(self.w.close)

    def load(self, name, extent):
        path = self.folder / (name + '.dat')
        path.write_text(f'# Signal: PL\nEnergy(eV) {-extent} 0 {extent}\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
        self.w._load_dat_file_direct(path)
        return path, self.w.workspace.active

    def render(self, key=None):
        self.w._render_plot_page(key)
        target = self.w.graph_documents.page_for(key) if key else self.w.plot_page
        self.assertIsNotNone(target.rendered_page, target.status_label.text())
        return target.rendered_page

    def test_fresh_dat_wide_and_scientific_gate_labels_expand_only_paper(self):
        for extent in (1000, 100000):
            with self.subTest(extent=extent):
                path, session = self.load(str(extent), extent)
                original = session.presentation
                data = session.current_cube.z.copy()
                limits = (session.view.x_limits, session.view.y_limits)
                page = self.render()
                resolved = session.presentation
                np.testing.assert_allclose(page.plot_size_in, (2.9, 2.8), atol=1e-12)
                self.assertEqual(replace(resolved, width_in=original.width_in,
                                         height_in=original.height_in), original)
                self.assertGreater(resolved.width_in, original.width_in)
                self.assertGreater(resolved.height_in, original.height_in)
                self.assertLess(resolved.width_in, original.width_in + .25)
                self.assertLess(resolved.height_in, original.height_in + .25)
                self.assertAlmostEqual(resolved.width_in/resolved.height_in,
                                       original.width_in/original.height_in)
                self.assertEqual(page.figure_size_in, (resolved.width_in, resolved.height_in))
                self.assertEqual(self.w.plot_page.presentation, resolved)
                self.assertEqual(self.w.plot_page.preview.presentation, resolved)
                self.assertEqual(page.owner_token, self.w._plot_owner_token())
                self.assertIn('actual', self.w.plot_page.dimensions_label.text())
                self.assertAlmostEqual(self.w.plot_page.width_spin.value(), resolved.width_in, delta=.00051)
                np.testing.assert_array_equal(session.current_cube.z, data)
                self.assertEqual((session.view.x_limits, session.view.y_limits), limits)
                root = ET.fromstring(page.svg_bytes)
                self.assertAlmostEqual(float(root.get('width')[:-2]), resolved.width_in)
                self.assertAlmostEqual(float(root.get('height')[:-2]), resolved.height_in)
                # A second render is stable and must not add paper repeatedly.
                self.assertEqual(self.render().svg_bytes, page.svg_bytes)

    def test_export_metadata_and_sidecar_reopen_share_grown_dimensions(self):
        path, session = self.load('wide', 1000)
        page = self.render()
        resolved = session.presentation
        saved = self.w.export_plot_page('png')
        svg = self.w.export_plot_page('svg')
        self.assertEqual(svg.read_bytes(), page.svg_bytes)
        with Image.open(saved) as image:
            self.assertEqual(image.size, tuple(round(v*300) for v in page.figure_size_in))
            self.assertEqual(json.loads(image.info['SpectraAnalyzer'])['presentation'], resolved.to_dict())
        sidecar = json.loads(self.w._plot_state_store.path_for(path).read_text(encoding='utf-8'))
        self.assertEqual(active_plot_recipe(sidecar['plot'])['presentation'], resolved.to_dict())
        self.w.close()
        self.w = MainWindow()
        self.addCleanup(self.w.close)
        self.w._load_dat_file_direct(path)
        self.assertEqual(self.w.workspace.active.presentation, resolved)
        self.assertEqual(self.render().figure_size_in, page.figure_size_in)

    def test_inactive_tiled_graph_growth_does_not_change_active_owner(self):
        _, first = self.load('inactive', 1000)
        key = (first.dataset_id, first.active_graph_id)
        target = self.w.plot_page
        _, active = self.load('active', 1)
        active_presentation = active.presentation
        active_target = self.w.plot_page
        self.render(key)
        self.assertIs(self.w.workspace.active, active)
        self.assertIs(self.w.plot_page, active_target)
        self.assertEqual(active.presentation, active_presentation)
        self.assertGreater(first.presentation.width_in, PlotPresentation().width_in)
        self.assertEqual(target.presentation, first.presentation)
        self.assertEqual(target.rendered_page.owner_token, self.w._plot_owner_token(key))

    def test_invalid_pending_draft_survives_growth_attempt(self):
        _, session = self.load('draft', 1000)
        target = self.w.plot_page
        original = session.presentation
        target.width_spin.lineEdit().setText('-')
        self.w._render_plot_page()
        self.assertEqual(target.width_spin.lineEdit().text(), '-')
        self.assertEqual(session.presentation, original)
        self.assertIsNone(target.rendered_page)
        self.assertFalse(target.export_svg_btn.isEnabled())

    def test_batch_growth_updates_frozen_job_metadata_and_saved_owner(self):
        path, session = self.load('batch', 100000)
        self.w.start_plot_batch([session.dataset_id], formats=('svg', 'png'), common_folder=self.folder)
        self.w._plot_batch_timer.stop()
        self.w._step_plot_batch()
        self.assertTrue(self.w.plot_batch_summary.success, self.w.plot_batch_report)
        job = self.w._plot_batch._jobs[0]
        self.assertEqual(job.presentation, session.presentation)
        self.assertGreater(job.presentation.height_in, PlotPresentation().height_in)
        result = self.w.plot_batch_summary.results[0]
        png = next(item.path for item in result.outputs if item.format == 'png')
        with Image.open(png) as image:
            self.assertEqual(json.loads(image.info['SpectraAnalyzer'])['presentation'], session.presentation.to_dict())
            self.assertEqual(image.size, (round(session.presentation.width_in*300),
                                          round(session.presentation.height_in*300)))
        sidecar = json.loads(self.w._plot_state_store.path_for(path).read_text(encoding='utf-8'))
        self.assertEqual(active_plot_recipe(sidecar['plot'])['presentation'], session.presentation.to_dict())

    def test_batch_growth_keeps_newer_live_settings_and_pending_drafts(self):
        _, session = self.load('frozen-batch', 100000)
        self.w.start_plot_batch([session.dataset_id], formats=('png',), common_folder=self.folder)
        self.w._plot_batch_timer.stop()
        newer = replace(session.presentation, numeric_pt=11.)
        self.w.set_plot_presentation(newer)
        self.w.plot_page.width_spin.lineEdit().setText('-')
        self.w._step_plot_batch()
        self.assertTrue(self.w.plot_batch_summary.success, self.w.plot_batch_report)
        self.assertEqual(session.presentation, newer)
        self.assertEqual(self.w.plot_page.width_spin.lineEdit().text(), '-')
        self.assertIsNone(self.w.plot_page.rendered_page)
        job = self.w._plot_batch._jobs[0]
        self.assertEqual(job.presentation.numeric_pt, 10.)
        self.assertGreater(job.presentation.height_in, newer.height_in)
        with Image.open(self.w.plot_batch_summary.results[0].outputs[0].path) as image:
            self.assertEqual(json.loads(image.info['SpectraAnalyzer'])['presentation'], job.presentation.to_dict())

    def test_inactive_invalid_draft_is_not_replaced_or_exported(self):
        _, first = self.load('inactive-draft', 1000)
        key = (first.dataset_id, first.active_graph_id)
        target = self.w.plot_page
        _, active = self.load('other-owner', 1)
        original = first.presentation
        target.width_spin.lineEdit().setText('-')
        self.w._render_plot_page(key)
        self.assertEqual(target.width_spin.lineEdit().text(), '-')
        self.assertEqual(first.presentation, original)
        self.assertIsNone(target.rendered_page)
        self.w.start_plot_batch(graph_targets=[key], formats=('svg',), common_folder=self.folder)
        self.assertFalse(self.w.plot_batch_summary.success)
        self.assertEqual(self.w.plot_batch_summary.results[0].error_stage, 'prepare')
        self.assertEqual(first.presentation, original)
        self.assertIs(self.w.workspace.active, active)

    def test_changed_owner_during_preparation_cannot_publish_or_grow_page(self):
        _, session = self.load('stale', 1000)
        original = session.presentation
        prepare = svg_export.prepare_heatmap_page
        def prepare_then_invalidate(*args, **kwargs):
            result = prepare(*args, **kwargs)
            self.w._invalidate_graph_document(self.w._document_key(), kind='presentation')
            return result
        with patch('spectra_app.main_window.prepare_heatmap_page', side_effect=prepare_then_invalidate):
            self.w._render_plot_page()
        self.assertEqual(session.presentation, original)
        self.assertIsNone(self.w.plot_page.rendered_page)

    def test_regional_gradient_does_not_cover_scientific_gate_offset(self):
        self.load('regional-offset', 100000)
        self.w.color_scale_combo.setCurrentIndex(self.w.color_scale_combo.findData('3'))
        audit = svg_export._audit_layout
        checked = []
        def audit_gradient_clearance(figure):
            audit(figure)
            offset = figure.axes[0].yaxis.get_offset_text()
            self.assertTrue(offset.get_text())
            bounds = offset.get_window_extent(figure.canvas.get_renderer())
            self.assertFalse(any(bounds.overlaps(axis.get_window_extent()) for axis in figure.axes[1:]),
                             'A regional gradient covers the scientific Gate multiplier.')
            checked.append(True)
        with patch.object(svg_export, '_audit_layout', side_effect=audit_gradient_clearance):
            page = self.render()
        self.assertTrue(checked)
        np.testing.assert_allclose(page.plot_size_in, (2.9, 2.8), atol=1e-12)


class PreparedHeatmapTests(unittest.TestCase):
    def snapshot(self, extent=1000):
        return HeatmapSnapshot(x=[1., 1.6, 2.2], y=[-extent, 0, extent], z=np.arange(9).reshape(3, 3),
                               x_limits=(1., 2.2), y_limits=(-extent, extent),
                               color_limits=(1.19, 38.43),
                               x_label='Energy (eV)', y_label='Gate (V)', signal_label='PL (arb. units)')

    def prepare(self, snapshot, presentation):
        return svg_export.prepare_heatmap_page(snapshot, presentation)

    def test_unlocked_paper_grows_only_required_dimension_and_strict_renderer_stays_strict(self):
        snapshot = self.snapshot()
        original = PlotPresentation(aspect_locked=False)
        with self.assertRaisesRegex(ValueError, 'frame does not fit'):
            svg_export.render_hybrid_svg(snapshot, original)
        resolved, page = self.prepare(snapshot, original)
        self.assertGreater(resolved.width_in, original.width_in)
        self.assertEqual(resolved.height_in, original.height_in)
        np.testing.assert_allclose(page.plot_size_in, (2.9, 2.8), atol=1e-12)
        self.assertEqual(svg_export.render_hybrid_svg(snapshot, resolved).svg_bytes, page.svg_bytes)

    def test_fitting_and_explicit_auto_pages_are_unchanged(self):
        for p in (PlotPresentation(width_in=5., height_in=4.5, frame_width_in=2.913, frame_height_in=2.174),
                  PlotPresentation(frame_width_in=None, frame_height_in=None)):
            expected = svg_export.render_hybrid_svg(self.snapshot(), p)
            resolved, page = self.prepare(self.snapshot(), p)
            self.assertIs(resolved, p)
            self.assertEqual(page.svg_bytes, expected.svg_bytes)

    def test_invalid_ticks_and_over_limit_paper_do_not_become_valid_by_growth(self):
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.prepare(self.snapshot(), PlotPresentation(axis=AxisPresentation(x_major_count=100)))
        with self.assertRaisesRegex(ValueError, '40'):
            self.prepare(self.snapshot(), PlotPresentation(frame_width_in=40.))

    def test_regional_headers_reflow_without_frame_or_font_changes(self):
        snapshot = HeatmapSnapshot(x=np.linspace(1., 2.2, 12), y=[-1., 0., 1.],
            z=np.arange(36).reshape(3, 12), x_limits=(1., 2.2), y_limits=(-1., 1.),
            x_label='Photon Energy (eV)', y_label='Gate', signal_label='d²(ΔR/R)/dE² (arb. units)',
            regions=tuple(ColorRegion(f'region-{i}', i*4, (i+1)*4, limits, label)
                for i, (limits, label) in enumerate(zip(
                    ((-89000., 60000.), (-2500., 2400.), (-.006, .008)), ('Left', 'Middle', 'Right')))))
        for alignment in ('left', 'center', 'right'):
            original = PlotPresentation(aspect_locked=False)
            original = replace(original, colorbar=replace(original.colorbar, alignment=alignment))
            resolved, page = self.prepare(snapshot, original)
            self.assertGreater(resolved.height_in, original.height_in)
            np.testing.assert_allclose(page.plot_size_in, (2.9, 2.8), atol=1e-12)
            self.assertEqual(replace(resolved, width_in=original.width_in,
                                     height_in=original.height_in), original)
            self.assertEqual(len(page.colorbar_bounds), 3)
            self.assertEqual(svg_export.render_hybrid_svg(snapshot, resolved).svg_bytes, page.svg_bytes)


if __name__ == '__main__':
    unittest.main()
