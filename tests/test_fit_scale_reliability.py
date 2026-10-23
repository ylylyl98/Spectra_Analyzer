"""Physical units must not change signed fits or the meaning of their metrics."""
import json
import unittest
from unittest.mock import patch

import numpy as np
from scipy.special import voigt_profile

from spectra_app.analysis_document import AnalysisSeed
from spectra_app.fitting import fit_multi_peak
from spectra_app import fitting, xy_analysis


class FitScaleReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.x = np.linspace(-4., 4., 401)

    def shape(self, model):
        offset = self.x - .7
        if model == 'Gaussian':
            return np.exp(-.5*(offset/.6)**2)
        if model == 'Lorentzian':
            return .6**2/(offset**2 + .6**2)
        return voigt_profile(offset, .6, .25)/voigt_profile(0., .6, .25)

    def assert_solution(self, result, expected, scale, sign, model):
        np.testing.assert_allclose(result.centers, [.7], atol=2e-6)
        np.testing.assert_allclose(result.widths, [.6], atol=2e-6)
        np.testing.assert_allclose(result.amplitudes/scale, [sign*5.], atol=2e-6)
        self.assertAlmostEqual(result.baseline/scale, 2., places=6)
        if model == 'Voigt':
            np.testing.assert_allclose(result.sigmas, result.widths, atol=0.)
            np.testing.assert_allclose(result.gammas, [.25], atol=2e-6)
        np.testing.assert_allclose(result.y_fit/scale, expected/scale, atol=2e-6)
        np.testing.assert_allclose(
            result.y_fit/scale, (result.baseline + result.components.sum(axis=0))/scale,
            atol=1e-12)
        self.assertLess(result.rmse/scale, 1e-6)
        self.assertGreater(result.r_squared, .999999999)

    def test_units_do_not_change_peak_or_dip_solution(self):
        for model in ('Gaussian', 'Lorentzian', 'Voigt'):
            for mode, sign in (('Peaks', 1), ('Dips', -1)):
                for scale in (1., 1e-9, 1e-18, 1e18):
                    with self.subTest(model=model, mode=mode, scale=scale):
                        y = scale*(2. + sign*5.*self.shape(model))
                        original_y = y.copy()
                        result = fit_multi_peak(self.x, y, centers_hint=[.8], model=model, mode=mode)
                        self.assert_solution(result, y, scale, sign, model)
                        np.testing.assert_array_equal(y, original_y)

    def test_scaled_explicit_seeds_keep_signed_fixed_and_bounded_components(self):
        x = self.x
        expected = 2. + 4.*np.exp(-.5*((x+1.1)/.3)**2) - 2.*np.exp(-.5*((x-1.2)/.4)**2)
        for scale in (1e-18, 1., 1e18):
            with self.subTest(scale=scale):
                result = fit_multi_peak(x, scale*expected, centers_hint=[-1.1, 1.],
                    model='Gaussian', mode='Both', kinds=[True, False],
                    fixed_centers=[True, False], center_bounds=([-1.5, .95], [-.5, 1.25]),
                    initial_amplitudes=scale*np.array([3.5, -1.5]), initial_widths=[.35, .45])
                self.assertEqual(result.centers[0], -1.1)
                np.testing.assert_allclose(result.centers, [-1.1, 1.2], atol=2e-6)
                np.testing.assert_allclose(result.widths, [.3, .4], atol=2e-6)
                np.testing.assert_allclose(result.amplitudes/scale, [4., -2.], atol=2e-6)

    def test_large_constant_offset_does_not_mask_small_signal(self):
        y = 1e8 + 5.*self.shape('Gaussian')
        result = fit_multi_peak(self.x, y, centers_hint=[.8], model='Gaussian', mode='Peaks')
        np.testing.assert_allclose(result.centers, [.7], atol=2e-6)
        np.testing.assert_allclose(result.widths, [.6], atol=2e-6)
        np.testing.assert_allclose(result.amplitudes, [5.], atol=2e-6)
        self.assertLess(result.rmse, 1e-6)

    def test_scaled_explicit_voigt_seeds_preserve_sigma_gamma_and_fixed_center(self):
        for scale in (1e-18, 1., 1e18):
            with self.subTest(scale=scale):
                y = scale*(2. - 5.*self.shape('Voigt'))
                result = fit_multi_peak(self.x, y, centers_hint=[.7], model='Voigt', mode='Dips',
                    initial_amplitudes=[-4.*scale], initial_widths=[.65], initial_gammas=[.2],
                    linewidth_min=.1, linewidth_max=.9, fixed_centers=True)
                self.assertEqual(result.centers[0], .7)
                self.assert_solution(result, y, scale, -1, 'Voigt')

    def test_r_squared_matches_normalized_residual_for_imperfect_small_fit(self):
        y = 2. + 5.*self.shape('Gaussian') + .2*np.sin(5*self.x)
        results = []
        for scale in (1., 1e-18, 1e18):
            with self.subTest(scale=scale):
                result = fit_multi_peak(self.x, scale*y, centers_hint=[.8], model='Gaussian', mode='Peaks')
                normalized_fit = result.y_fit/scale
                expected_r2 = 1. - np.sum((y-normalized_fit)**2)/np.sum((y-y.mean())**2)
                self.assertAlmostEqual(result.r_squared, expected_r2, places=12)
                self.assertLess(result.r_squared, .999)
                results.append(result)
        for result in results[1:]:
            np.testing.assert_allclose(result.centers, results[0].centers, atol=1e-7)
            self.assertAlmostEqual(result.r_squared, results[0].r_squared, places=12)

    def test_constant_signal_r_squared_is_undefined_and_json_safe(self):
        from spectra_app.project_io import _metric_encode, _metric_decode
        for constant in (0., 1., 1e-18):
            with self.subTest(constant=constant):
                result = fit_multi_peak(self.x, np.full(self.x.size, constant), centers_hint=[.7],
                                        model='Gaussian', mode='Peaks', fixed_centers=True)
                self.assertTrue(np.isnan(result.r_squared))
                encoded = _metric_encode(result.r_squared)
                self.assertIsNone(encoded)
                self.assertEqual(json.dumps({'r_squared': encoded}, allow_nan=False), '{"r_squared": null}')
                self.assertTrue(np.isnan(_metric_decode(encoded, 'r_squared')))

    def test_xy_edited_seed_defaults_follow_signal_units(self):
        for scale in (1e-18, 1., 1e18):
            with self.subTest(scale=scale):
                y = scale*(2. + 5.*self.shape('Gaussian'))
                data = xy_analysis.prepare_xy(self.x, y)
                result = xy_analysis.fit_seeds(data, xy_analysis.AnalysisSettings(), [AnalysisSeed(.8)])
                self.assert_solution(result, y, scale, 1, 'Gaussian')

    def test_cancellation_before_optimizer_and_during_evaluations(self):
        class StopFit(Exception):
            pass

        def cancel():
            raise StopFit('cancel requested')

        with patch('spectra_app.fitting.curve_fit') as solver:
            with self.assertRaises(StopFit):
                fit_multi_peak(self.x, self.shape('Gaussian'), centers_hint=[.8],
                               model='Gaussian', mode='Peaks', check_cancelled=cancel)
            solver.assert_not_called()

        count = 0

        def cancel_during_evaluations():
            nonlocal count
            count += 1
            if count >= 4:
                raise StopFit('cancel during model evaluation')

        with self.assertRaises(StopFit):
            fit_multi_peak(self.x, self.shape('Gaussian'), centers_hint=[.8],
                           model='Gaussian', mode='Peaks', check_cancelled=cancel_during_evaluations)
        self.assertEqual(count, 4)

    def test_cancellation_is_checked_after_optimizer_returns(self):
        class StopFit(Exception):
            pass

        finished = False
        real_solver = fitting.curve_fit

        def solver(*args, **kwargs):
            nonlocal finished
            result = real_solver(*args, **kwargs)
            finished = True
            return result

        def check():
            if finished:
                raise StopFit('cancel after optimization')

        with patch('spectra_app.fitting.curve_fit', side_effect=solver), self.assertRaises(StopFit):
            fit_multi_peak(self.x, self.shape('Gaussian'), centers_hint=[.8],
                           model='Gaussian', mode='Peaks', check_cancelled=check)
        self.assertTrue(finished)

    def test_xy_fit_entry_points_forward_cancellation(self):
        class StopFit(Exception):
            pass

        def cancel():
            raise StopFit('cancel XY fit')

        data = xy_analysis.prepare_xy(self.x, self.shape('Gaussian'))
        settings = xy_analysis.AnalysisSettings()
        peaks = xy_analysis.detect_peaks(data, settings)
        for fit, selected in ((xy_analysis.fit_peaks, peaks), (xy_analysis.fit_seeds, [AnalysisSeed(.8)])):
            with self.subTest(entry_point=fit.__name__), self.assertRaises(StopFit):
                fit(data, settings, selected, check_cancelled=cancel)
