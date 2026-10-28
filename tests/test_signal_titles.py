import tempfile
import unittest
from pathlib import Path
from spectra_app.dat_io import load_dat_cube
from spectra_app.plot_state import active_plot_recipe
from tests import test_annotation_persistence as annotations
from tests import dat_properties_quick_helpers as dp
from tests.background_helpers import wait_jobs as wait_for_background_jobs
from tests import quick_migration_case as migration
from spectra_app.plot_state import source_fingerprint

class SignalDetectionTests(unittest.TestCase):
    def test_unit_suppression_preserves_literal_manual_meanings(self):
        from spectra_app.signal_titles import display_signal_label
        for unit in ('a. u.', 'a.u.', 'a.u', 'A. U.'):
            literal = f'∂²(DR/R₀)/∂E² ({unit})'
            self.assertEqual(display_signal_label(literal), '∂²(DR/R₀)/∂E² (arb. units)')
            self.assertEqual(display_signal_label(literal, False), '∂²(DR/R₀)/∂E²')
        self.assertEqual(display_signal_label('DR literal (a. u.)', False), 'DR literal')
        self.assertEqual(display_signal_label('ΔR/R₀ (a. u.)', False), 'ΔR/R₀')
        self.assertEqual(display_signal_label('PL (arb. units)', False), 'PL')
        for text in ('Manual D (sample)', 'Unit (a. u.) followed by text', 'Manual D', 'PL (counts)'):
            self.assertEqual(display_signal_label(text), text)
        for text in ('Manual D (sample)', 'Unit (a. u.) followed by text', 'Manual D'):
            self.assertEqual(display_signal_label(text, False), text)

    def test_pl_and_reference_source_evidence_is_conservative(self):
        from spectra_app.signal_titles import infer_signal_kind, infer_source_signal_kind
        self.assertEqual(infer_signal_kind('scan_PL730nm.dat'), 'pl')
        self.assertEqual(infer_signal_kind('scan.dat', ['#Signal PL']), 'pl')
        self.assertEqual(infer_signal_kind('sample.dat', ['# Signal: photoluminescence']), 'pl')
        for name in ('sample.dat', 'apple.dat', 'scan_PLanning.dat'):
            self.assertEqual(infer_signal_kind(name), 'unknown')
        self.assertEqual(infer_source_signal_kind('scan.dat', ['# Signal: d²(ΔR/R₀)/dE²']), 'reflection_reference')
        self.assertEqual(infer_signal_kind('scan.dat', ['# Signal: d²(ΔR/R₀)/dE²']), 'energy_second_derivative')
        self.assertEqual(infer_signal_kind('scan.dat', ['# Signal: d²PL/dE²']), 'energy_second_derivative')
        self.assertEqual(infer_signal_kind('scan.dat', ['# Signal: ∂²(ΔR/R₀)/∂E²']), 'energy_second_derivative')
        self.assertEqual(infer_source_signal_kind('scan.dat', ['# Signal: ∂²(ΔR/R₀)/∂E²']), 'reflection_reference')
        self.assertEqual(infer_signal_kind('scan.dat', ['# Signal: ∂²PL/∂E²']), 'energy_second_derivative')

    def test_loader_signal_evidence(self):
        cases = [('scan_DR_R.dat', '', 'reflection'),
                 ('scan_DR_R_External_d2E_Regrid.dat', '', 'energy_second_derivative'),
                 ('scan_d2_E.dat', '', 'energy_second_derivative'),
                 ('scan_d2.dat', '', 'unknown'),
                 ('notd2energy.dat', '', 'unknown'),
                 ('scan.dat', '# Notes: second derivative considered later\n', 'unknown'),
                 ('scan.dat', '# Signal: second derivative\n', 'energy_second_derivative'),
                 ('scan.dat', '# Signal: d²(ΔR/R)/dE²\n', 'energy_second_derivative'),
                 ('scan_d2E.dat', '# Signal: ΔR/R\n', 'reflection'),
                 ('scan.dat', '# Signal: d2(DeltaR/R)/dE2\n', 'energy_second_derivative')]
        with tempfile.TemporaryDirectory(prefix='d2E_') as folder:
            for filename, header, kind in cases:
                with self.subTest(filename=filename, header=header):
                    path = Path(folder) / filename
                    path.write_text(header + 'Energy 0 1\n1 1 2\n2 3 4\n3 5 6\n', encoding='utf-8')
                    self.assertEqual(getattr(load_dat_cube(path), 'signal_kind', None), kind)

class AutoSignalTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.settings_path = self.folder / 'profile.ini'
        self.windows = []
        self.host_errors = [self.quick_errors]

    def tearDown(self):
        for errors in self.host_errors:
            self.assertEqual(errors, [], 'Unexpected production Quick error')

    def arrange_cube(self, window, cube):
        from dataclasses import replace
        import numpy as np
        path = self.folder / Path(cube.source_path).name
        cube = replace(cube, source_path=str(path))
        np.savetxt(path, np.column_stack((cube.energy, cube.z.T)),
                   header='Energy(eV) ' + ' '.join(map(str, cube.gate)), comments='')
        window._apply_loaded_dat(path, cube, source_fingerprint(path), None, '', '')
        window.open_graph_document()
        return cube

    make_window = annotations.AnnotationPersistenceTests.make_window
    load_source = annotations.AnnotationPersistenceTests.load_source

    def test_processing_auto_and_manual_override(self):
        from tests.test_heatmap_processing import _cube
        import numpy as np
        window = self.make_window()
        self.arrange_cube(window, _cube(np.ones((2, 13))))
        window._set_view_limits_to_auto()
        window._plot_cube()
        window.processing.setMode('energy_second_derivative')
        window._on_generate_heatmap()
        wait_for_background_jobs(window)
        self.assertIn('d²', dp.state(window)['signal_title'])
        window.processing.setMode('original')
        window._on_generate_heatmap()
        self.assertEqual(dp.state(window)['signal_title'], 'Signal (arb. units)')
        window.dat_properties.setLabel('signal_title', 'Manual', window.dat_properties.ownerToken)
        window.processing.setMode('energy_second_derivative')
        window._on_generate_heatmap()
        wait_for_background_jobs(window)
        self.assertEqual(dp.state(window)['signal_title'], 'Manual')
        dp.set_flag(window, 'signal_title_auto', True)
        self.assertIn('d²', dp.state(window)['signal_title'])

    def test_auto_tracks_files_despite_fixed_annotations(self):
        window = self.make_window()
        self.load_source(window, 'scan_DR_R.dat')
        self.load_source(window, 'scan_DR_R_d2E.dat')
        self.assertIn('d²', dp.state(window)['signal_title'])
        window.dat_properties.setLabel('signal_title', 'Custom', window.dat_properties.ownerToken)
        self.assertFalse(dp.state(window)['signal_title_auto'])
        self.load_source(window, 'scan_DR_R.dat')
        self.assertEqual(dp.state(window)['signal_title'], 'Custom')
        dp.set_flag(window, 'signal_title_auto', True)
        self.assertEqual(dp.state(window)['signal_title'], 'ΔR/R₀ (arb. units)')
        self.load_source(window, 'mystery.dat')
        self.assertIn('unrecognized', dp.state(window)['signal_title_tooltip'].lower())

    def test_applied_derivatives_follow_pl_and_unknown_sources(self):
        from dataclasses import replace
        from tests.test_heatmap_processing import _cube
        import numpy as np
        for source, expected in [('scan_PL730nm.dat', 'd²PL/dE² (arb. units)'),
                                 ('mystery.dat', 'd²Signal/dE² (arb. units)'),
                                 ('scan_DR_R.dat', 'd²(ΔR/R₀)/dE² (arb. units)')]:
            window = self.make_window()
            self.arrange_cube(window, replace(_cube(np.ones((2, 13))), source_path=source))
            window._set_view_limits_to_auto(); window._plot_cube()
            window.processing.setMode('energy_second_derivative')
            window._on_generate_heatmap()
            wait_for_background_jobs(window)
            self.assertEqual(dp.state(window)['signal_title'], expected)

    def test_applied_derivative_of_declared_derivative_has_fourth_order_label(self):
        from dataclasses import replace
        from tests.test_heatmap_processing import _cube
        import numpy as np
        for kind, expected in [('pl', 'd⁴PL/dE⁴ (arb. units)'),
                               ('reflection', 'd⁴(ΔR/R₀)/dE⁴ (arb. units)')]:
            window = self.make_window()
            self.arrange_cube(window, replace(_cube(np.ones((2, 13))),
                                          signal_kind='energy_second_derivative', signal_source_kind=kind))
            window._set_view_limits_to_auto(); window._plot_cube()
            self.assertIn('d²', dp.state(window)['signal_title'])
            window.processing.setMode('energy_second_derivative'); window._on_generate_heatmap()
            wait_for_background_jobs(window)
            self.assertEqual(dp.state(window)['signal_title'], expected)
            window.processing.setMode('original')
            window._on_generate_heatmap()
            self.assertIn('d²', dp.state(window)['signal_title'])

    def test_legacy_recipe_remains_manual_and_new_auto_recipe_roundtrips(self):
        import json
        window = self.make_window()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scan_d2E.dat'
            path.write_text('Energy 0 1\n1 1 2\n2 3 4\n3 5 6\n', encoding='utf-8')
            window._load_dat_file_direct(path)
            window._save_current_plot_state()
            recipe = window._plot_state_store.path_for(path)
            payload = json.loads(recipe.read_text(encoding='utf-8'))
            self.assertTrue(active_plot_recipe(payload['plot'])['signal_title_auto'])
            window.current_dat_path = None
            payload['version'] = 3
            payload['plot'] = active_plot_recipe(payload['plot'])
            payload['plot'].pop('signal_title_auto')
            payload['plot']['annotations']['signal_title'] = 'Legacy custom'
            recipe.write_text(json.dumps(payload), encoding='utf-8')
            window._load_dat_file_direct(path)
            self.assertFalse(dp.state(window)['signal_title_auto'])
            self.assertEqual(dp.state(window)['signal_title'], 'Legacy custom')
            dp.set_flag(window, 'signal_title_auto', True)
            self.assertIn('d²', dp.state(window)['signal_title'])
            window.current_dat_path = None
