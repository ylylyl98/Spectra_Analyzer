"""Signed true Voigt parameters and user-provided seed constraints."""
import unittest
from dataclasses import replace
import numpy as np
from scipy.special import voigt_profile

from spectra_app.fitting import fit_multi_peak


class VoigtFittingTests(unittest.TestCase):
    def test_true_signed_voigt_recovers_both_widths_and_peak_heights(self):
        x = np.linspace(-5, 5, 1001)
        y = 1.2 + 4*voigt_profile(x+1.2, .28, .16)/voigt_profile(0, .28, .16)
        y -= 2.5*voigt_profile(x-1.3, .35, .22)/voigt_profile(0, .35, .22)
        result = fit_multi_peak(x, y, centers_hint=np.array([-1.1, 1.4]),
            model='Voigt', mode='Both', kinds=np.array([True, False]),
            initial_amplitudes=np.array([3.5, -2.]), initial_widths=np.array([.3, .4]),
            initial_gammas=np.array([.2, .25]), linewidth_min=.01, linewidth_max=1.)
        np.testing.assert_allclose(result.centers, [-1.2, 1.3], atol=1e-6)
        np.testing.assert_allclose(result.amplitudes, [4, -2.5], atol=1e-6)
        np.testing.assert_allclose(result.widths, [.28, .35], atol=1e-6)
        np.testing.assert_allclose(result.sigmas, result.widths, atol=0)
        np.testing.assert_allclose(result.gammas, [.16, .22], atol=1e-6)
        np.testing.assert_allclose(result.y_fit, y, atol=1e-7)
        np.testing.assert_allclose(result.components.sum(axis=0)+result.baseline, y, atol=1e-7)

    def test_fixed_and_bounded_centers_preserve_exact_manual_seeds(self):
        x = np.linspace(-4, 4, 601)
        y = 2 + 4*np.exp(-.5*((x+1.1)/.3)**2) - 2*np.exp(-.5*((x-1.2)/.4)**2)
        result = fit_multi_peak(x, y, centers_hint=np.array([-1., 1.]), model='Gaussian', mode='Both',
            kinds=np.array([True, False]), fixed_centers=np.array([True, False]),
            center_bounds=(np.array([-1.5, .95]), np.array([-.5, 1.05])),
            initial_widths=np.array([.3, .4]), initial_amplitudes=np.array([4., -2.]))
        self.assertEqual(result.centers[0], -1.)
        self.assertAlmostEqual(result.centers[1], 1.05, places=6)
        self.assertIsNone(result.gammas)
        self.assertIsNone(result.sigmas)

    def test_all_fixed_voigt_uses_all_finite_samples_and_does_not_mutate(self):
        x = np.linspace(-3, 3, 301)
        y = 2 - 3*voigt_profile(x-.2, .3, .12)/voigt_profile(0, .3, .12)
        y[150] = np.nan
        x[42] = np.nan
        old_x, old_y = x.copy(), y.copy()
        result = fit_multi_peak(x, y, centers_hint=np.array([.2]), model='Voigt', mode='Dips',
            fixed_centers=True, initial_widths=[.35], initial_gammas=[.1], initial_amplitudes=[-2.5])
        self.assertEqual(result.centers[0], .2)
        self.assertEqual(result.x.size, 299)
        np.testing.assert_allclose(result.widths, [.3], atol=1e-6)
        np.testing.assert_allclose(result.gammas, [.12], atol=1e-6)
        np.testing.assert_equal(x, old_x)
        np.testing.assert_equal(y, old_y)

    def test_parameter_count_accounts_for_voigt_and_fixed_centers(self):
        x = np.linspace(-1, 1, 5)
        y = 2 + 3*voigt_profile(x, .3, .1)/voigt_profile(0, .3, .1)
        with self.assertRaisesRegex(ValueError, 'free parameters'):
            fit_multi_peak(x, y, centers_hint=[0], model='Voigt', mode='Peaks')
        result = fit_multi_peak(x, y, centers_hint=[0], model='Voigt', mode='Peaks',
            fixed_centers=True, initial_widths=[.3], initial_gammas=[.1], initial_amplitudes=[3])
        self.assertLess(result.rmse, 1e-6)
        with self.assertRaisesRegex(ValueError, 'sigma'):
            replace(result, sigmas=result.widths+1)

    def test_invalid_seeds_and_bounds_are_reported_before_optimizer(self):
        x = np.linspace(-2, 2, 51)
        y = np.exp(-x*x)
        for extra in ({'initial_widths': [1, 2]}, {'initial_gammas': [float('nan')]},
                      {'fixed_centers': [True, False]}, {'center_bounds': ([.5], [1.])},
                      {'initial_amplitudes': [-1]}, {'linewidth_min': 1., 'linewidth_max': .5}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                fit_multi_peak(x, y, centers_hint=[0], model='Voigt', mode='Peaks', **extra)

    def test_existing_gaussian_and_lorentzian_width_and_height_semantics(self):
        x = np.linspace(-5, 5, 501)
        for model, shape in [('Gaussian', np.exp(-.5*((x-.7)/.6)**2)),
                             ('Lorentzian', .6**2/((x-.7)**2+.6**2))]:
            result = fit_multi_peak(x, 2+5*shape, centers_hint=[.8], model=model, mode='Peaks')
            np.testing.assert_allclose(result.centers, [.7], atol=1e-6)
            np.testing.assert_allclose(result.widths, [.6], atol=1e-6)
            np.testing.assert_allclose(result.amplitudes, [5], atol=1e-6)
            self.assertAlmostEqual(result.baseline, 2., places=6)

    def test_flat_data_has_a_valid_baseline_solution(self):
        x = np.linspace(0, 1, 41)
        result = fit_multi_peak(x, np.ones_like(x), centers_hint=[.5], model='Gaussian', mode='Peaks',
            fixed_centers=True)
        self.assertLess(result.rmse, 1e-6)
