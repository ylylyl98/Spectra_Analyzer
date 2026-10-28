import tempfile
import unittest
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure

from spectra_app.dat_io import load_dat_cube, matrix_layout_reason


class DatPreambleTests(unittest.TestCase):
    def load_text(self, text, filename="BGonly.dat"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / filename
        path.write_text(text, encoding="utf-8")
        return load_dat_cube(path)

    def test_tg_minus_bg_filename_sets_physical_gate_label_without_changing_data(self):
        text = "Photon energy(eV)\t-1\t2\n1.6\t10\t20\n1.7\t30\t40\n"
        for filename in (
            "TG-1BG=0.dat", "scan_tg-1bg=0_rep01.dat", "scan_Tg-1bG=0.dat",
            "scan_TG\u22121BG=0_PIXIS.dat",
            "scan_TG\u22121BG=0_PIXIS_d2E_W21_O2_Regrid.dat",
        ):
            with self.subTest(filename=filename):
                cube = self.load_text(text, filename)
                self.assertEqual(cube.gate_label, "Tg+Bg (V)")
                np.testing.assert_array_equal(cube.gate, [-1, 2])
                np.testing.assert_array_equal(cube.energy, [1.6, 1.7])
                np.testing.assert_array_equal(cube.z, [[10, 30], [20, 40]])

    def test_tg_minus_bg_filename_labels_embedded_numeric_gate_coordinates(self):
        cube = self.load_text(
            "0\t-5\t0\t5\n1.6\t10\t20\t30\n1.61\t40\t50\t60\n1.62\t70\t80\t90\n",
            "scan_TG-1BG=0.dat",
        )
        self.assertEqual(cube.gate_label, "Tg+Bg (V)")
        np.testing.assert_array_equal(cube.gate, [-5, 0, 5])

    def test_gate_conditions_set_default_label_for_the_free_sweep(self):
        text = "Energy(eV)\t-1\t2\n1.6\t10\t20\n1.7\t30\t40\n"
        for condition, expected in (
            ("BG=0", "Tg (V)"), ("bg=-1.25V", "Tg (V)"),
            ("TG=+2.5", "Bg (V)"), ("tg=\u2212.5v", "Bg (V)"),
            ("TG-BG=-3", "Tg+Bg (V)"), ("BG-TG=2V", "Tg+Bg (V)"),
            ("BG-1TG=0.5", "Tg+Bg (V)"), ("TG-1BG=01", "Tg+Bg (V)"),
            ("TG+BG=0", "Tg\u2212Bg (V)"), ("TG+1BG=-2.5V", "Tg\u2212Bg (V)"),
            ("bg+tg=+.5v", "Tg\u2212Bg (V)"), ("BG+1TG=2", "Tg\u2212Bg (V)"),
        ):
            with self.subTest(condition=condition):
                cube = self.load_text(text, f"scan_{condition}_d2E_W21_O2_Regrid.dat")
                self.assertEqual(cube.gate_label, expected)
                np.testing.assert_array_equal(cube.gate, [-1, 2])
                np.testing.assert_array_equal(cube.z, [[10, 30], [20, 40]])

    def test_unsupported_filename_relations_keep_generic_gate_label(self):
        text = "Energy(eV)\t-1\t2\n1.6\t10\t20\n1.7\t30\t40\n"
        for filename in (
            "BGonly.dat", "scan_0TG-BG=18.dat", "scan_TG-0BG=18.dat",
            "scan_-0.95TG-BG=18.dat", "scan_TG--2BG=0.dat",
            "scan_TG-+2BG=0.dat", "scan_0.9.5TG-BG=18.dat",
            "scan_1e2TG-BG=18.dat", "scan_nanTG-BG=18.dat", "scan_infBG=0.dat",
            "scan_TG-1BG=0e1.dat", "scan_NTG-1BG=0.dat", "scan_TG+TG=0.dat",
            "scan_TG+BG=0mV.dat", "scan_TG-BG=0.5a.dat", "scan_BG=0=1.dat",
            "scan_BG=nan.dat", "scan_BG=inf.dat", "scan_BG=0Vextra.dat",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(self.load_text(text, filename).gate_label, "Gate")

    def test_weighted_gate_conditions_preserve_coefficients_and_measurements(self):
        text = "Energy(eV)\t-1\t2\n1.6\t10\t20\n1.7\t30\t40\n"
        for condition, expected in (
            ("0.95TG-BG=18", "0.95Tg+Bg (V)"),
            ("TG-2BG=0", "Tg+2Bg (V)"),
            ("0.95TG+BG=18", "0.95Tg\u2212Bg (V)"),
            ("2BG-.95TG=-18", "0.95Tg+2Bg (V)"),
            ("2bg+.95tg=18", "0.95Tg\u22122Bg (V)"),
            ("01.00TG\u221202.50BG=0", "Tg+2.5Bg (V)"),
            ("0.95TG=18", "Bg (V)"), ("2BG=0", "Tg (V)"),
        ):
            with self.subTest(condition=condition):
                cube = self.load_text(text, f"{condition}.dat")
                self.assertEqual(cube.gate_label, expected)
                np.testing.assert_array_equal(cube.gate, [-1, 2])
                np.testing.assert_array_equal(cube.energy, [1.6, 1.7])
                np.testing.assert_array_equal(cube.z, [[10, 30], [20, 40]])

    def test_multiple_distinct_gate_conditions_do_not_guess_a_sweep(self):
        text = "Energy(eV)\t-1\t2\n1.6\t10\t20\n1.7\t30\t40\n"
        for condition in (
            "TG=0_BG=1", "BG=0_BG=1", "TG-BG=0_TG+BG=0",
            "TG-BG=1_BG-TG=1", "TG-BG=0_TG-2BG=0",
            "0.95TG-BG=18_0.95TG-BG=19", "0.95TG-BG=18_BG-0.95TG=18",
            "TG-BG=0_2TG-2BG=0", "TG=1_2TG=2",
        ):
            with self.subTest(condition=condition):
                self.assertEqual(self.load_text(text, f"scan_{condition}.dat").gate_label, "Gate")

    def test_repeated_equivalent_gate_condition_is_unambiguous(self):
        text = "Energy(eV)\t-1\t2\n1.6\t10\t20\n1.7\t30\t40\n"
        for condition, expected in (
            ("BG=0_bg=-0.00V", "Tg (V)"),
            ("TG-BG=1_BG-1TG=-1V", "Tg+Bg (V)"),
            ("TG+BG=1.5_BG+1TG=+1.50V", "Tg\u2212Bg (V)"),
            (".95TG-2BG=18_2.00BG-0.950TG=-18.0V", "0.95Tg+2Bg (V)"),
            (".95TG+2BG=18_2.00BG+0.950TG=18.0V", "0.95Tg\u22122Bg (V)"),
            ("0.95TG=18_0.950tg=18.0", "Bg (V)"),
        ):
            with self.subTest(condition=condition):
                self.assertEqual(self.load_text(text, f"scan_{condition}.dat").gate_label, expected)

    def test_tg_minus_bg_filename_keeps_synthetic_gate_index_label(self):
        for condition in ("TG-1BG=0", "BG=0", "TG=1", "TG+BG=2", "0.95TG-BG=18", "TG-2BG=0"):
            with self.subTest(condition=condition):
                cube = self.load_text("1.6\t10\t20\n1.7\t30\t40\n", f"scan_{condition}.dat")
                self.assertEqual(cube.gate_label, "Gate Index")
                np.testing.assert_array_equal(cube.gate, [0, 1])

    def test_title_zero_padding_and_embedded_gate_row(self):
        cube = self.load_text(
            "BGonly.dat\t0\t0\t0\n"
            "0\t0\t0\t0\n"
            "0\t-5\t0\t5\n"
            "1.6\t0\t0\t0\n"
            "1.7\t-0.1\t0.2\t0.3\n"
            "1.8\t0.4\t0.5\t0.7\n"
        )
        self.assertEqual(cube.z.shape, (3, 3))
        np.testing.assert_array_equal(cube.gate, [-5, 0, 5])
        np.testing.assert_allclose(cube.energy, [1.6, 1.7, 1.8])
        np.testing.assert_allclose(cube.z, [[0, -0.1, 0.4], [0, 0.2, 0.5], [0, 0.3, 0.7]])
        mesh = Figure().subplots().pcolormesh(cube.energy, cube.gate, cube.z, shading="auto")
        self.assertTrue(np.all(np.diff(mesh.get_coordinates()[:, 0, 1]) > 0))

    def test_normal_headers_preserve_zero_measurement(self):
        for header in ("Energy\t-5\t0\t5", "0\t-5\t0\t5"):
            with self.subTest(header=header):
                cube = self.load_text(header + "\n1.6\t0\t0\t0\n1.61\t1\t2\t3\n1.62\t4\t5\t6\n")
                self.assertEqual(cube.z.shape, (3, 3))
                np.testing.assert_array_equal(cube.gate, [-5, 0, 5])
                np.testing.assert_array_equal(cube.z[:, 0], [0, 0, 0])

    def test_delimited_headers_preserve_spaced_axis_labels(self):
        for separator in ("\t", ","):
            with self.subTest(separator=separator):
                cube = self.load_text("\n".join(separator.join(row) for row in (
                    ("Photon energy(eV)", "2", "-1"),
                    ("1.7", "20", "10"),
                    ("1.6", "40", "30"),
                )))
                self.assertEqual(cube.x_detection_source, "header label")
                self.assertEqual(cube.x_detection_note, "Detected from x-axis header 'Photon energy(eV)'.")
                self.assertEqual(cube.x_original_type, "ev")
                np.testing.assert_array_equal(cube.gate, [-1, 2])
                np.testing.assert_array_equal(cube.energy, [1.6, 1.7])
                np.testing.assert_array_equal(cube.z, [[30, 10], [40, 20]])

    def test_spaced_header_requires_complete_finite_numeric_gate_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incomplete.dat"
            for separator in ("\t", ","):
                for last_gate in ("missing", "nan", "inf"):
                    with self.subTest(separator=separator, last_gate=last_gate):
                        path.write_text("\n".join(separator.join(row) for row in (
                            ("Photon energy(eV)", "-1", last_gate),
                            ("1.6", "10", "20"), ("1.7", "30", "40"),
                        )), encoding="utf-8")
                        self.assertFalse(matrix_layout_reason(path))

    def test_numeric_rows_keep_mixed_whitespace_tab_comma_separators(self):
        cube = self.load_text(
            "Energy(eV)\t-1\t1\n"
            "1.6 10\t20\n"
            "1.7,30 40\n"
        )
        np.testing.assert_array_equal(cube.gate, [-1, 1])
        np.testing.assert_array_equal(cube.energy, [1.6, 1.7])
        np.testing.assert_array_equal(cube.z, [[10, 30], [20, 40]])

    def test_zero_gate_header_without_full_preamble_preserves_rows(self):
        for middle in ("0\t1\t0\t0", "0\t0\t0\t0"):
            with self.subTest(middle=middle):
                # No monotonic embedded gate axis: this is ordinary measured data.
                cube = self.load_text("Energy\t0\t0\t0\n" + middle + "\n0.1\t1\t0\t1\n0.2\t2\t3\t4\n")
                self.assertEqual(cube.z.shape, (3, 3))
                np.testing.assert_array_equal(cube.gate, [0, 0, 0])
                np.testing.assert_allclose(cube.energy, [0, 0.1, 0.2])


if __name__ == "__main__":
    unittest.main()
