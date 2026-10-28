"""Editable labels are independent of scientific identity and survive projects."""
from dataclasses import replace
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from spectra_app.dat_io import load_dat_cube
from spectra_app.dataset_state import DatasetSession, ProcessingState, Workspace
from spectra_app.project_io import (ProjectFormatError, read_project, runtime_workspace_stamp,
    workspace_digest, write_project)
from spectra_app.workbook_state import (add_curve, add_processing_result, create_comparison, create_workbook,
    ensure_dataset_sheet, reconcile_dataset_sheets, register_xy_sheet, save_sheet_snapshot,
    set_sheet_column_metadata, sheet_column_metadata, sheet_columns, sheet_values)
from tests.test_project_io import archive_members, overwrite_archive


class SheetMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.workspace = Workspace()
        self.book = create_workbook(self.workspace)
        self.sheet = register_xy_sheet(self.workspace, self.book.workbook_id, 'Signal',
            np.array([[1., 3.], [2., 4.]]), ['Energy (eV)', 'Signal (counts/s)'])

    def dat(self):
        source = self.directory / 'signal.dat'
        source.write_text('Energy(eV) -6 -5.8\n1 2 3\n2 4 5\n', encoding='utf-8')
        cube = load_dat_cube(source)
        session = self.workspace.register(DatasetSession(source_path=source, base_cube=cube, current_cube=cube))
        return session, ensure_dataset_sheet(self.workspace, session.dataset_id)

    def test_defaults_edits_and_explicit_empty_preserve_identity_and_values(self):
        self.assertEqual(sheet_column_metadata(self.workspace, self.sheet, 0),
                         {'long_name': 'Energy', 'units': 'eV'})
        values = self.sheet.data.copy()
        set_sheet_column_metadata(self.workspace, self.sheet.sheet_id, 1, long_name='PL', units='')
        self.assertEqual(self.sheet.column_names, ['Energy (eV)', 'Signal (counts/s)'])
        self.assertEqual(sheet_columns(self.workspace, self.sheet), ['Energy (eV)', 'PL'])
        self.assertEqual(sheet_column_metadata(self.workspace, self.sheet, 1), {'long_name': 'PL', 'units': ''})
        np.testing.assert_equal(self.sheet.data, values)
        self.assertEqual(self.sheet.y_columns, [1])
        set_sheet_column_metadata(self.workspace, self.sheet.sheet_id, 1, long_name='', units='')
        snapshot = save_sheet_snapshot(self.workspace, self.sheet.sheet_id, 'Blank label')
        self.assertEqual(sheet_column_metadata(self.workspace, snapshot, 1), {'long_name': '', 'units': ''})
        self.assertEqual(snapshot.column_names[1], 'Signal (counts/s)')

    def test_only_generated_curve_labels_follow_repeated_edits(self):
        graph = create_comparison(self.workspace)
        automatic = add_curve(self.workspace, graph.comparison_id, self.sheet.sheet_id, 0, 1)
        manual = add_curve(self.workspace, graph.comparison_id, self.sheet.sheet_id, 0, 1, label='My label')
        graph.x_label, graph.y_label = 'Manual X', 'Manual Y'
        for name in ('First', 'Second'):
            set_sheet_column_metadata(self.workspace, self.sheet.sheet_id, 1, long_name=name)
            self.assertTrue(automatic.label.endswith(f'{name} (counts/s)'))
            self.assertEqual(manual.label, 'My label')
            self.assertEqual((graph.x_label, graph.y_label), ('Manual X', 'Manual Y'))

    def test_numeric_gate_long_names_signal_units_and_frozen_snapshot(self):
        session, sheet = self.dat()
        self.assertEqual(sheet_column_metadata(self.workspace, sheet, 1), {'long_name': '-6', 'units': ''})
        self.assertEqual(sheet_column_metadata(self.workspace, sheet, 2), {'long_name': '-5.8', 'units': ''})
        self.assertEqual(sheet_column_metadata(self.workspace, sheet, 0), {'long_name': 'Photon Energy', 'units': 'eV'})
        legacy = sheet_columns(self.workspace, sheet)
        self.assertIn('Gate = -6', legacy[1])
        before = sheet_values(self.workspace, sheet).copy()
        set_sheet_column_metadata(self.workspace, sheet.sheet_id, 2, units='a.u.')
        snapshot = save_sheet_snapshot(self.workspace, sheet.sheet_id, 'Frozen')
        self.assertEqual(sheet_column_metadata(self.workspace, snapshot, 1), {'long_name': '-6', 'units': ''})
        self.assertEqual(sheet_column_metadata(self.workspace, snapshot, 2), {'long_name': '-5.8', 'units': 'a.u.'})
        set_sheet_column_metadata(self.workspace, sheet.sheet_id, 2, long_name='Renamed')
        session.base_cube = session.current_cube = replace(session.base_cube, gate=np.array([9., 10.]))
        self.assertEqual(sheet_column_metadata(self.workspace, snapshot, 2)['long_name'], '-5.8')
        np.testing.assert_equal(sheet_values(self.workspace, sheet), before)
        self.assertEqual(sheet.column_names, [])

    def test_roundtrip_without_sources_and_dirty_stamps_legacy_version(self):
        session, dat = self.dat()
        path = self.directory / 'labels.spectra'
        write_project(path, self.workspace)
        self.assertEqual(json.loads(archive_members(path)['manifest.json'])['version'], 8)
        cache = {}
        graph = create_comparison(self.workspace)
        existing = add_curve(self.workspace, graph.comparison_id, dat.sheet_id, 0, 1, label='Manual label')
        before = runtime_workspace_stamp(self.workspace, cache), workspace_digest(self.workspace)
        set_sheet_column_metadata(self.workspace, dat.sheet_id, 1, long_name='Emission', units='counts/s')
        self.assertNotEqual(before[0], runtime_workspace_stamp(self.workspace, cache))
        self.assertNotEqual(before[1], workspace_digest(self.workspace))
        digest = write_project(path, self.workspace)
        # The new curve's ball/5-point markers require schema 13 alongside metadata.
        self.assertEqual(json.loads(archive_members(path)['manifest.json'])['version'], 13)
        session.source_path.unlink()
        restored = read_project(path)
        self.assertEqual(digest, workspace_digest(restored))
        self.assertEqual(sheet_columns(restored, dat.sheet_id)[1], 'Emission (counts/s)')
        self.assertEqual(restored.comparisons[graph.comparison_id].curves[existing.curve_id].label, 'Manual label')
        np.testing.assert_equal(sheet_values(restored, dat.sheet_id), sheet_values(self.workspace, dat))

    def test_invalid_edits_rejected_without_mutation_and_reconcile_prunes(self):
        for column, fields in ((True, {'units': 'V'}), (-1, {'units': 'V'}), (2, {'units': 'V'}),
                               (1, {'units': 3}), (1, {'long_name': 'bad\x00name'}),
                               (1, {'long_name': 'two\nlines'})):
            with self.subTest(column=column, fields=fields), self.assertRaises(ValueError):
                set_sheet_column_metadata(self.workspace, self.sheet.sheet_id, column, **fields)
            self.assertEqual(self.sheet.column_metadata, {})
        session, sheet = self.dat()
        set_sheet_column_metadata(self.workspace, sheet.sheet_id, 2, long_name='Removed')
        session.base_cube = session.current_cube = replace(session.base_cube,
            gate=session.base_cube.gate[:1], z=session.base_cube.z[:1])
        reconcile_dataset_sheets(self.workspace, session.dataset_id)
        self.assertEqual(sheet.column_metadata, {})

    def test_strict_project_metadata_validation(self):
        set_sheet_column_metadata(self.workspace, self.sheet.sheet_id, 1, units='')
        path = self.directory / 'invalid.spectra'
        write_project(path, self.workspace)
        original = archive_members(path)
        manifest = json.loads(original['manifest.json'])
        for metadata in ([], {'01': {'units': ''}}, {'2': {'units': ''}}, {'-1': {'units': ''}},
                         {'1': {}}, {'1': {'extra': 'a'}}, {'1': {'long_name': 1}},
                         {'1': {'units': 'bad\x00unit'}}, {'1': {'units': 'bad\nunit'}}):
            bad = copy.deepcopy(manifest)
            bad['sheets'][0]['column_metadata'] = metadata
            overwrite_archive(path, dict(original, **{'manifest.json': json.dumps(bad).encode()}))
            with self.subTest(metadata=metadata), self.assertRaises(ProjectFormatError):
                read_project(path)
        bad = copy.deepcopy(manifest)
        bad['version'] = 10
        overwrite_archive(path, dict(original, **{'manifest.json': json.dumps(bad).encode()}))
        with self.assertRaises(ProjectFormatError):
            read_project(path)

    def test_retained_groups_still_save_v10_until_metadata_is_added(self):
        session, sheet = self.dat()
        add_processing_result(self.workspace, session.dataset_id,
            replace(session.base_cube, z=session.base_cube.z * 2),
            ProcessingState('energy_second_derivative', True, 7))
        path = self.directory / 'groups.spectra'
        write_project(path, self.workspace)
        self.assertEqual(json.loads(archive_members(path)['manifest.json'])['version'], 10)
        set_sheet_column_metadata(self.workspace, sheet.sheet_id, 1, units='counts/s')
        write_project(path, self.workspace)
        self.assertEqual(json.loads(archive_members(path)['manifest.json'])['version'], 11)
