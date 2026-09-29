import tempfile
import unittest
from pathlib import Path
from spectra_app.dat_io import load_dat_cube
from tests import test_annotation_persistence as annotations

class SignalDetectionTests(unittest.TestCase):
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

class AutoSignalTests(unittest.TestCase):
    setUp = annotations.AnnotationPersistenceTests.setUp
    tearDown = annotations.AnnotationPersistenceTests.tearDown
    make_window = annotations.AnnotationPersistenceTests.make_window
    load_source = annotations.AnnotationPersistenceTests.load_source

    def test_processing_auto_and_manual_override(self):
        from tests.test_heatmap_processing import _cube
        import numpy as np
        window = self.make_window()
        window.current_cube = _cube(np.ones((2, 13)))
        window._set_view_limits_to_auto()
        window._plot_cube()
        window.processing_mode_combo.setCurrentIndex(1)
        window._on_generate_heatmap()
        self.assertIn('d²', window.signal_title_combo.currentText())
        window.processing_mode_combo.setCurrentIndex(0)
        self.assertEqual(window.signal_title_combo.currentText(), 'ΔR/R (a. u.)')
        window.signal_title_combo.setCurrentText('Manual')
        window.processing_mode_combo.setCurrentIndex(1)
        window._on_generate_heatmap()
        self.assertEqual(window.signal_title_combo.currentText(), 'Manual')
        window.signal_title_auto_chk.setChecked(True)
        self.assertIn('d²', window.signal_title_combo.currentText())

    def test_auto_tracks_files_despite_fixed_annotations(self):
        window = self.make_window()
        self.load_source(window, 'scan_DR_R.dat')
        self.load_source(window, 'scan_DR_R_d2E.dat')
        self.assertIn('d²', window.signal_title_combo.currentText())
        window.signal_title_combo.setCurrentText('Custom')
        self.assertFalse(window.signal_title_auto_chk.isChecked())
        self.load_source(window, 'scan_DR_R.dat')
        self.assertEqual(window.signal_title_combo.currentText(), 'Custom')
        window.signal_title_auto_chk.setChecked(True)
        self.assertEqual(window.signal_title_combo.currentText(), 'ΔR/R (a. u.)')
        self.load_source(window, 'mystery.dat')
        self.assertIn('unrecognized', window.signal_title_combo.toolTip().lower())

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
            self.assertTrue(payload['plot']['signal_title_auto'])
            window.current_dat_path = None
            payload['plot'].pop('signal_title_auto')
            payload['plot']['annotations']['signal_title'] = 'Legacy custom'
            recipe.write_text(json.dumps(payload), encoding='utf-8')
            window._load_dat_file_direct(path)
            self.assertFalse(window.signal_title_auto_chk.isChecked())
            self.assertEqual(window.signal_title_combo.currentText(), 'Legacy custom')
            window.signal_title_auto_chk.setChecked(True)
            self.assertIn('d²', window.signal_title_combo.currentText())
            window.current_dat_path = None
