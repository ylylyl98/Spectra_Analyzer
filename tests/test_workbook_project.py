import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import DatasetSession, Workspace
from spectra_app.project_io import ProjectFormatError, read_project, runtime_workspace_stamp, workspace_digest, write_project
from spectra_app.workbook_state import (add_curve, create_comparison, create_workbook,
    ensure_dataset_sheet, register_xy_sheet, resolve_curve, sheet_values)
from tests.test_project_io import archive_members, npy_bytes, overwrite_archive, rich_workspace


class WorkbookProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / 'xy.spectra'
        self.workspace = Workspace()
        self.populate(self.workspace)

    def populate(self, workspace):
        self.source = self.directory / 'xy.csv'
        self.source.write_text('Y,X\n10,2\n20,1\n', encoding='utf-8')
        self.book = create_workbook(workspace, 'Readings')
        self.sheet = register_xy_sheet(workspace, self.book.workbook_id, 'Measured',
            np.array([[10., 2.], [np.nan, 1.]]), ['Y', 'X'], source_path=self.source)
        self.sheet.x_column, self.sheet.y_columns = 1, [0]
        self.graph = create_comparison(workspace, 'Cross-file')
        self.curve = add_curve(workspace, self.graph.comparison_id, self.sheet.sheet_id, 1, 0,
                               label='Sample A', visible=False, color='#aabbcc', width=2)
        self.graph.x_label, self.graph.y_label, self.graph.legend_visible = 'Time', 'Signal', False
        bk, gk = ['workbook', self.book.workbook_id, ''], ['xy', self.graph.comparison_id, '']
        workspace.graph_workspace = {'mode': 'free', 'documents': [
            {'key': bk, 'open': False, 'rect': [12, 24, 500, 400]},
            {'key': gk, 'open': True, 'rect': [70, 40, 600, 450]}], 'active': gk, 'stacking': [gk]}

    def test_xy_only_source_free_roundtrip_and_canonical_digest(self):
        digest = write_project(self.path, self.workspace)
        self.source.unlink()
        with patch('pathlib.Path.resolve', side_effect=AssertionError('source probe')):
            restored = read_project(self.path)
        self.assertFalse(restored.datasets)
        self.assertEqual(digest, workspace_digest(restored))
        self.assertEqual(restored.graph_workspace, self.workspace.graph_workspace)
        actual = restored.comparisons[self.graph.comparison_id]
        self.assertEqual(actual, self.graph)
        self.assertEqual(restored.sheets[self.sheet.sheet_id].y_columns, [0])
        x, y = resolve_curve(restored, actual.curves[self.curve.curve_id])
        np.testing.assert_equal(x, [2, 1])
        np.testing.assert_equal(y, [10, np.nan])
        manifest = json.loads(archive_members(self.path)['manifest.json'])
        self.assertEqual(manifest['version'], 13)

    def test_mixed_roundtrip_retains_live_dat_curves_without_extra_dat_arrays(self):
        workspace = rich_workspace(self.directory)
        self.populate(workspace)
        sheet = ensure_dataset_sheet(workspace, 'owner-0')
        curve = add_curve(workspace, self.graph.comparison_id, sheet.sheet_id, 0, 2, mode='processed')
        write_project(self.path, workspace)
        for path in self.directory.glob('*.dat'):
            path.unlink()
        self.source.unlink()
        restored = read_project(self.path)
        np.testing.assert_equal(resolve_curve(restored, restored.comparisons[self.graph.comparison_id].curves[curve.curve_id])[1],
                                workspace.datasets['owner-0'].current_cube.z[1])
        for record in json.loads(archive_members(self.path)['manifest.json'])['sheets']:
            if record['kind'] == 'dat':
                self.assertIsNone(record['data'])

    def test_dat_filename_whitespace_roundtrips_without_source(self):
        source = self.directory / ' leading.dat'
        source.write_text('Energy(eV) 0 1\n1 10 20\n2 30 40\n', encoding='utf-8')
        cube = load_dat_cube(source)
        session = self.workspace.register(DatasetSession(source_path=source,
                                                         base_cube=cube, current_cube=cube))
        sheet = ensure_dataset_sheet(self.workspace, session.dataset_id)
        write_project(self.path, self.workspace)
        source.unlink()
        restored = read_project(self.path)
        actual = restored.sheets[sheet.sheet_id]
        self.assertEqual(actual.name, 'Original')
        self.assertEqual(actual.source_path, source)
        self.assertEqual(restored.datasets[session.dataset_id].source_path, source)
        owner = next(book for book in restored.workbooks.values() if sheet.sheet_id in book.sheet_ids)
        self.assertEqual(owner.name, 'leading.dat')
        np.testing.assert_equal(sheet_values(restored, actual), [[1, 10, 20], [2, 30, 40]])

    def test_runtime_and_exact_dirty_stamps_cover_metadata_and_arrays(self):
        cache = {}
        for mutate in (lambda: setattr(self.sheet, 'x_column', 0),
                       lambda: setattr(self.sheet, 'y_columns', []),
                       lambda: setattr(self.curve, 'label', 'Renamed'),
                       lambda: setattr(self.curve, 'visible', True),
                       lambda: setattr(self.curve, 'color', '#123456'),
                       lambda: self.sheet.data.__setitem__((0, 0), 42)):
            before, exact = runtime_workspace_stamp(self.workspace, cache), workspace_digest(self.workspace)
            mutate()
            self.assertNotEqual(before, runtime_workspace_stamp(self.workspace, cache))
            self.assertNotEqual(exact, workspace_digest(self.workspace))

    def test_invalid_references_memberships_columns_styles_layouts_and_unused_arrays(self):
        write_project(self.path, self.workspace)
        original = archive_members(self.path)
        manifest = json.loads(original['manifest.json'])
        mutations = [
            lambda m: m['workbooks'][0]['sheet_ids'].append(self.sheet.sheet_id),
            lambda m: m['workbooks'][0].update(sheet_ids=[]),
            lambda m: m['workbooks'].append(dict(m['workbooks'][0], workbook_id='other')),
            lambda m: m['sheets'][0].update(dataset_id='missing'),
            lambda m: m['sheets'][0].update(column_names=['only one']),
            lambda m: m['sheets'][0].update(x_column=99),
            lambda m: m['sheets'][0].update(mode='processed'),
            lambda m: m['comparisons'][0]['curves'][0].update(sheet_id='missing'),
            lambda m: m['comparisons'][0]['curves'][0].update(y_column=2),
            lambda m: m['comparisons'][0]['curves'][0].update(color='red'),
            lambda m: m['comparisons'][0]['curves'][0].update(width=0),
            lambda m: m['comparisons'][0]['curves'][0].update(width=True),
            lambda m: m['comparisons'][0]['curves'][0].update(visible=1),
            lambda m: m['graph_workspace']['documents'][0].update(key=['unknown', 'id', '']),
            lambda m: m['graph_workspace'].update(active=['workbook', self.book.workbook_id, '']),
            lambda m: m['graph_workspace'].update(stacking=[]),
            lambda m: m['graph_workspace']['documents'][1].update(rect=[0, 0, 0, 200]),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                bad = copy.deepcopy(manifest)
                mutate(bad)
                members = dict(original, **{'manifest.json': json.dumps(bad).encode('utf-8')})
                overwrite_archive(self.path, members)
                with self.assertRaises(ProjectFormatError):
                    read_project(self.path)
        for array in (np.array([1., 2.]), np.ones((0, 2))):
            with self.subTest(shape=array.shape):
                members = dict(original)
                members[manifest['sheets'][0]['data']] = npy_bytes(array)
                overwrite_archive(self.path, members)
                with self.assertRaises(ProjectFormatError):
                    read_project(self.path)
        members = dict(original)
        members['arrays/999999.npy'] = npy_bytes(np.array([1.]))
        overwrite_archive(self.path, members)
        with self.assertRaisesRegex(ProjectFormatError, 'unreferenced'):
            read_project(self.path)

    def test_rejects_invalid_dat_provenance_and_invalid_memory_before_replace(self):
        workspace = rich_workspace(self.directory)
        write_project(self.path, workspace)
        before = self.path.read_bytes()
        sheet = ensure_dataset_sheet(workspace, 'owner-0')
        sheet.source_path = self.directory / 'wrong.dat'
        with self.assertRaises(ProjectFormatError):
            write_project(self.path, workspace)
        self.assertEqual(before, self.path.read_bytes())
