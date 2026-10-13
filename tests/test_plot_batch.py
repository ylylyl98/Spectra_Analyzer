"""Serial detached export retains per-format failures, bytes and owner isolation."""
import importlib
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication
from spectra_app.dataset_state import Workspace
from spectra_app.plot_presentation import PlotPresentation
from spectra_app.plot_snapshot import snapshot_dataset
from spectra_app.svg_export import render_hybrid_svg, write_svg, write_svg_png
from tests.settings_helpers import isolate_settings
from tests.test_plot_templates import dataset

try:
    batch = importlib.import_module('spectra_app.plot_batch')
except ModuleNotFoundError:
    batch = None

APP = QApplication.instance() or QApplication([])


class PlotBatchTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(batch, 'Serial detached batch core is not implemented')
        self.folder = isolate_settings(self).parent

    def job(self, identifier='a', **kwargs):
        session = dataset(identifier)
        defaults = dict(snapshot=snapshot_dataset(session), presentation=PlotPresentation(heatmap_dpi=80),
                        basename=self.folder / identifier, metadata={'Dataset': identifier})
        defaults.update(kwargs)
        return batch.BatchExportJob(**defaults)

    def test_svg_png_share_one_real_page_and_keep_metadata_and_physical_dpi(self):
        pages = []
        def render(snapshot, presentation):
            page = render_hybrid_svg(snapshot, presentation)
            pages.append(page)
            return page
        def svg(page, path):
            self.assertIs(page, pages[-1])
            return write_svg(page, path)
        def png(page, path, **kwargs):
            self.assertIs(page, pages[-1])
            return write_svg_png(page, path, **kwargs)
        runner = batch.SerialPlotBatch([self.job()], formats=('svg', 'png'),
                                      renderer=render, svg_writer=svg, png_writer=png)
        result = runner.step()
        self.assertTrue(result.success)
        self.assertEqual(len(pages), 1)
        self.assertEqual(result.dataset_id, 'a')
        paths = {item.format: item.path for item in result.outputs}
        self.assertEqual(paths['svg'].read_bytes(), pages[0].svg_bytes)
        with Image.open(paths['png']) as image:
            self.assertEqual(image.size, (288, 276))
            self.assertEqual(image.info['Dataset'], 'a')
            self.assertAlmostEqual(image.info['dpi'][0], 80, delta=.05)
        self.assertIsNone(runner.step())
        self.assertTrue(runner.summary.success)
        self.assertEqual(runner.summary.completed, 1)

    def test_pair_naming_skips_either_existing_format_without_overwrite(self):
        (self.folder / 'a_export01.svg').write_bytes(b'existing-svg')
        (self.folder / 'a_export02.png').write_bytes(b'existing-png')
        runner = batch.SerialPlotBatch([self.job(), self.job()], formats=('svg', 'png'))
        first, second = runner.step(), runner.step()
        self.assertTrue(first.success and second.success)
        self.assertEqual([item.path.name for item in first.outputs], ['a_export03.svg', 'a_export03.png'])
        self.assertEqual([item.path.name for item in second.outputs], ['a_export04.svg', 'a_export04.png'])
        self.assertEqual((self.folder / 'a_export01.svg').read_bytes(), b'existing-svg')
        self.assertEqual((self.folder / 'a_export02.png').read_bytes(), b'existing-png')

    def test_render_failure_and_one_format_failure_continue_next_datasets(self):
        def render(snapshot, presentation):
            if snapshot.dataset_id == 'bad-render':
                raise ValueError('synthetic invalid render')
            return render_hybrid_svg(snapshot, presentation)
        def png(page, path, **kwargs):
            if page.owner_token[0] == 'bad-png':
                raise OSError('synthetic write denied')
            return write_svg_png(page, path, **kwargs)
        runner = batch.SerialPlotBatch([self.job('bad-render'), self.job('bad-png'), self.job('good')],
                                      formats=('svg', 'png'), renderer=render, png_writer=png)
        failed_render, partial, good = runner.step(), runner.step(), runner.step()
        self.assertFalse(failed_render.success)
        self.assertEqual(failed_render.error_stage, 'render')
        self.assertIn('invalid render', failed_render.error)
        self.assertFalse(partial.success)
        self.assertEqual([(item.format, item.success) for item in partial.outputs], [('svg', True), ('png', False)])
        self.assertEqual(partial.outputs[1].error_stage, 'write')
        self.assertEqual(partial.error_stage, 'write')
        self.assertIn('png: synthetic write denied', partial.error)
        self.assertTrue(partial.outputs[0].path.exists())
        self.assertTrue(good.success)
        self.assertFalse(runner.summary.success)
        self.assertEqual((runner.summary.completed, runner.summary.failed, runner.summary.canceled), (3, 2, 0))

    def test_cancel_between_files_preserves_finished_files_and_skips_remaining_jobs(self):
        runner = batch.SerialPlotBatch([self.job('a'), self.job('b'), self.job('c')])
        first = runner.step()
        runner.cancel()
        self.assertIsNone(runner.step())
        self.assertTrue(first.outputs[0].path.exists())
        self.assertFalse((self.folder / 'b_export01.svg').exists())
        self.assertEqual([item.dataset_id for item in runner.summary.results], ['a', 'b', 'c'])
        self.assertEqual([item.canceled for item in runner.summary.results], [False, True, True])
        self.assertEqual((runner.summary.completed, runner.summary.failed, runner.summary.canceled), (1, 0, 2))
        self.assertFalse(runner.summary.success)

    def test_inactive_snapshot_export_never_activates_or_mutates_an_owner(self):
        active, inactive = dataset('active'), dataset('inactive')
        workspace = Workspace(datasets={'active': active, 'inactive': inactive}, active_dataset_id='active')
        values = inactive.current_cube.z.copy()
        snapshot = snapshot_dataset(inactive, activation_epoch=8, science_revision=3)
        metadata = {'Dataset': 'inactive'}
        job = self.job('inactive', snapshot=snapshot, presentation=inactive.presentation, metadata=metadata)
        metadata['Dataset'] = 'changed'
        inactive.current_cube.z[:] = 99
        runner = batch.SerialPlotBatch([job])
        result = runner.step()
        self.assertTrue(result.success)
        self.assertEqual(workspace.active_dataset_id, 'active')
        np.testing.assert_array_equal(snapshot.z, values)
        np.testing.assert_array_equal(inactive.current_cube.z, np.full((3, 4), 99.))
        self.assertEqual(job.metadata, {'Dataset': 'inactive'})
        self.assertEqual(inactive.presentation, job.presentation)
        self.assertIsNot(inactive.presentation, job.presentation)
        self.assertEqual(snapshot.owner_token, ('inactive', 8, 3, 0, 0, 'default'))

    def test_exclusive_writer_closes_file_exists_race_and_reports_failure(self):
        def racing_svg(page, path):
            path.write_bytes(b'other-writer')
            return write_svg(page, path)
        runner = batch.SerialPlotBatch([self.job()], svg_writer=racing_svg)
        result = runner.step()
        self.assertFalse(result.success)
        self.assertEqual(result.outputs[0].path.read_bytes(), b'other-writer')
        self.assertEqual(result.outputs[0].error_stage, 'write')
        self.assertFalse(runner.summary.success)

    def test_invalid_declared_formats_and_metadata_fail_before_output(self):
        for formats in ((), ('pdf',), ('svg', 'svg'), ('SVG',)):
            with self.subTest(formats=formats), self.assertRaises(ValueError):
                batch.SerialPlotBatch([self.job()], formats=formats)
        with self.assertRaises(ValueError):
            self.job(metadata={'not-text': 42})
        self.assertEqual(list(self.folder.glob('*_export*')), [])

    def test_cancel_requested_during_one_file_finishes_its_pair_then_stops(self):
        def svg(page, path):
            result = write_svg(page, path)
            runner.cancel()
            return result
        runner = batch.SerialPlotBatch([self.job('a'), self.job('b')],
                                      formats=('svg', 'png'), svg_writer=svg)
        first = runner.step()
        self.assertTrue(first.success)
        self.assertEqual([result.dataset_id for result in runner.summary.results], ['a', 'b'])
        self.assertTrue(first.outputs[1].path.exists())
        self.assertIsNone(runner.step())
        self.assertEqual(runner.summary.canceled, 1)

    def test_pending_jobs_cannot_be_reported_as_global_success(self):
        runner = batch.SerialPlotBatch([self.job('a'), self.job('b')])
        self.assertFalse(runner.summary.success)
        self.assertEqual(runner.summary.completed, 0)
        runner.step()
        self.assertFalse(runner.done)
        self.assertFalse(runner.summary.success)
        self.assertEqual(runner.summary.completed, 1)


if __name__ == '__main__':
    unittest.main()
