import tempfile
import unittest
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure

from spectra_app.dat_io import load_dat_cube, matrix_layout_reason


class DatPreambleTests(unittest.TestCase):
    def load_text(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "BGonly.dat"
        path.write_text(text, encoding="utf-8")
        return load_dat_cube(path)

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
