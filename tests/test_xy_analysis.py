"""Quantitative XY analysis independent of display or source ordering."""
import unittest
from dataclasses import replace
import numpy as np
import importlib


class XYAnalysisTests(unittest.TestCase):
    def setUp(self):
        global analysis, transform_y
        self.assertIsNotNone(importlib.util.find_spec('spectra_app.xy_analysis'), 'XY analysis implementation is missing')
        analysis = importlib.import_module('spectra_app.xy_analysis')
        from spectra_app.comparison_display import transform_y

    def test_known_gaussian_and_lorentzian_parameters_and_source_preservation(self):
        x = np.linspace(-6, 6, 601)
        for model, y in [('Gaussian', 2 + 5*np.exp(-.5*((x-.7)/.6)**2)),
                         ('Lorentzian', 2 + 5*.6**2/((x-.7)**2+.6**2))]:
            original = y.copy()
            settings = analysis.AnalysisSettings(model=model, width_min=.1, width_max=2)
            data = analysis.prepare_xy(x, y)
            peaks = analysis.detect_peaks(data, settings)
            self.assertEqual(len(peaks.indices), 1)
            fit = analysis.fit_peaks(data, settings, peaks)
            np.testing.assert_allclose(fit.centers, [.7], atol=1e-5)
            np.testing.assert_allclose(fit.widths, [.6], atol=1e-5)
            np.testing.assert_allclose(fit.amplitudes, [5], atol=1e-5)
            self.assertAlmostEqual(fit.baseline, 2, places=5)
            np.testing.assert_array_equal(y, original)
            self.assertFalse(np.shares_memory(data.x, x))

    def test_descending_nonuniform_interval_and_gaps(self):
        x = np.array([0., .1, .3, 1., 2., 3., 4.])
        y = np.array([0., 1., 3., np.nan, 4., 2., 0.])
        data = analysis.prepare_xy(x[::-1], y[::-1], (.1, 4))
        np.testing.assert_equal(data.x, x[1:])
        # Neither side of the missing sample invents a local maximum.
        peaks = analysis.detect_peaks(data, analysis.AnalysisSettings())
        self.assertEqual(len(peaks.indices), 0)
        self.assertEqual(data.gap_count, 1)

    def test_reject_duplicate_nonmonotonic_and_insufficient_samples(self):
        for x in ([0, 1, 1, 2, 3], [0, 2, 1, 3, 4]):
            with self.assertRaisesRegex(ValueError, 'strictly monotonic'):
                analysis.prepare_xy(x, [1, 2, 3, 2, 1])
        with self.assertRaisesRegex(ValueError, 'five finite'):
            analysis.prepare_xy([0, 1, 2, 3, 4], [1, 2, np.nan, 2, np.nan])

    def test_normalization_uses_source_reference_including_hidden_ordinals(self):
        values = np.array([3., 9.])
        np.testing.assert_allclose(transform_y(values, np.array([2., 10.]), 'maxabs', 6, -.5), [5.8, 6.4])
        np.testing.assert_allclose(transform_y(values, np.array([2., 10.]), 'minmax', 6, -.5), [5.625, 6.375])
        np.testing.assert_allclose(transform_y(values, np.array([2., 10.]), 'none', 6, -.5), [8.5, 14.5])

    def test_no_peaks_and_invalid_width_bounds(self):
        data = analysis.prepare_xy(np.arange(10), np.ones(10))
        peaks = analysis.detect_peaks(data, analysis.AnalysisSettings())
        self.assertEqual(peaks.indices.size, 0)
        with self.assertRaisesRegex(ValueError, 'Find peaks'):
            analysis.fit_peaks(data, analysis.AnalysisSettings(), peaks)
        with self.assertRaisesRegex(ValueError, 'maximum width'):
            analysis.AnalysisSettings(width_min=2, width_max=1).validate()

    def test_segmented_detection_uses_one_global_threshold_and_count_cap(self):
        data = analysis.prepare_xy(np.arange(13), [0, 2, 0, np.nan, 0, 10, 0, np.nan, 0, 1, 0, 0, 0])
        result = analysis.detect_peaks(data, analysis.AnalysisSettings(prominence=.3, max_peaks=2))
        np.testing.assert_equal(result.indices, [5])
        result = analysis.detect_peaks(data, analysis.AnalysisSettings(prominence=0, max_peaks=2))
        np.testing.assert_equal(result.indices, [1, 5])

    def test_dips_and_both_use_signed_amplitudes(self):
        x = np.linspace(-5, 5, 501)
        y = 2 + 4*np.exp(-.5*((x+1.5)/.4)**2) - 3*np.exp(-.5*((x-1.5)/.5)**2)
        data = analysis.prepare_xy(x, y)
        settings = analysis.AnalysisSettings(mode='Both', max_peaks=2)
        peaks = analysis.detect_peaks(data, settings)
        np.testing.assert_equal(peaks.kinds, [True, False])
        fit = analysis.fit_peaks(data, settings, peaks)
        np.testing.assert_allclose(fit.centers, [-1.5, 1.5], atol=1e-5)
        np.testing.assert_allclose(fit.amplitudes, [4, -3], atol=1e-5)
        np.testing.assert_allclose(fit.widths, [.4, .5], atol=1e-5)
