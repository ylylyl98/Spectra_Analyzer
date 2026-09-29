import numpy as np
import pytest

from spectra_app.processing import energy_second_derivative


def test_quadratic_has_exact_second_derivative_on_uniform_energy():
    energy = np.linspace(1.0, 9.0, 9)
    z = (energy**2 + 3.0 * energy - 4.0)[None, :]

    result = energy_second_derivative(energy, z)

    np.testing.assert_allclose(result, 2.0, atol=1e-12)


def test_quadratic_uses_real_nonuniform_energy_coordinates():
    energy = np.array([0.0, 0.2, 0.9, 1.7, 3.1, 4.0, 6.5])
    z = (energy**2 - 2.0 * energy + 5.0)[None, :]

    result = energy_second_derivative(energy, z, smooth=False)

    np.testing.assert_allclose(result, 2.0, atol=1e-12)


def test_constant_and_linear_signals_have_zero_second_derivative():
    energy = np.array([0.0, 0.15, 0.7, 1.8, 3.0, 5.5])
    z = np.vstack((np.full(energy.size, 7.0), 4.0 * energy - 2.0))

    result = energy_second_derivative(energy, z)

    np.testing.assert_allclose(result, 0.0, atol=1e-12)


def test_result_does_not_modify_inputs():
    energy = np.array([1.0, 2.0, 4.0, 7.0])
    z = np.vstack((energy**2, energy + 1.0))
    original_energy = energy.copy()
    original_z = z.copy()

    energy_second_derivative(energy, z)

    np.testing.assert_array_equal(energy, original_energy)
    np.testing.assert_array_equal(z, original_z)


def test_nan_gaps_are_preserved_and_processed_per_finite_segment():
    energy = np.arange(9.0)
    z = (energy**2)[None, :]
    z[0, 4] = np.nan

    result = energy_second_derivative(energy, z)

    assert np.isnan(result[0, 4])
    np.testing.assert_allclose(result[0, :4], 2.0, atol=1e-12)
    np.testing.assert_allclose(result[0, 5:], 2.0, atol=1e-12)


def test_short_finite_segments_return_nan_without_raising():
    energy = np.arange(7.0)
    z = np.array([[1.0, np.nan, 2.0, 3.0, np.nan, 4.0, np.nan]])

    result = energy_second_derivative(energy, z)

    assert result.shape == z.shape
    assert np.isnan(result).all()


def test_descending_energy_is_supported():
    ascending = np.arange(1.0, 8.0)
    energy = ascending[::-1]
    z = (energy**2)[None, :]

    result = energy_second_derivative(energy, z, smooth=False)

    np.testing.assert_allclose(result, 2.0, atol=1e-12)


@pytest.mark.parametrize(
    ("energy", "z", "message"),
    [
        (np.arange(4.0), np.ones(4), "z must be a 2D"),
        (np.arange(4.0), np.ones((1, 3)), "same number of columns"),
        (np.array([0.0, 1.0, 1.0, 2.0]), np.ones((1, 4)), "strictly monotonic"),
        (np.array([0.0, 1.0, np.nan, 2.0]), np.ones((1, 4)), "finite"),
    ],
)
def test_input_validation_errors_are_actionable(energy, z, message):
    with pytest.raises(ValueError, match=message):
        energy_second_derivative(energy, z)


@pytest.mark.parametrize(
    ("window_length", "polyorder", "message"),
    [
        (6, 2, "odd"),
        (5, 5, "less than window_length"),
        (1, 0, "at least 3"),
    ],
)
def test_smoothing_parameter_validation_errors_are_actionable(
    window_length, polyorder, message
):
    energy = np.arange(5.0)
    z = energy[None, :] ** 2

    with pytest.raises(ValueError, match=message):
        energy_second_derivative(
            energy,
            z,
            window_length=window_length,
            polyorder=polyorder,
        )


def _reference_second_derivative(energy, z, smooth):
    """Original local least-squares algorithm, retained as a numerical oracle."""
    result = np.full_like(z, np.nan)
    for row_index, row in enumerate(z):
        indices = np.flatnonzero(np.isfinite(row))
        for segment in np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1):
            if segment.size < 3:
                continue
            x, y = energy[segment], row[segment].copy()
            window = min(11, segment.size)
            window -= 1 - window % 2
            if smooth and window > 3:
                smoothed = y.copy()
                for i in range(y.size):
                    start = max(0, min(i - window // 2, y.size - window))
                    offset = x[start:start + window] - x[i]
                    design = np.vander(offset / np.max(np.abs(offset)), 4, increasing=True)
                    smoothed[i] = np.linalg.lstsq(design, y[start:start + window], rcond=None)[0][0]
                y = smoothed
            for i in range(y.size):
                start = max(0, min(i - 1, y.size - 3))
                offset = x[start:start + 3] - x[i]
                scale = np.max(np.abs(offset))
                design = np.vander(offset / scale, 3, increasing=True)
                result[row_index, segment[i]] = 2 * np.linalg.lstsq(
                    design, y[start:start + 3], rcond=None
                )[0][2] / scale**2
    return result


@pytest.mark.parametrize('smooth', [False])
@pytest.mark.parametrize('nonuniform', [False, True])
@pytest.mark.parametrize('descending', [False, True])
def test_batched_processing_matches_original_local_fits(smooth, nonuniform, descending):
    rng = np.random.default_rng(17)
    energy = np.linspace(1.65, 2.0, 101)
    if nonuniform:
        energy[1:-1] += rng.uniform(-0.001, 0.001, energy.size - 2)
    z = np.vstack((np.ones(energy.size), energy * 4, energy**2,
                   rng.normal(size=(3, energy.size))))
    # Shared and different gap boundaries, plus short edge segments.
    z[3:5, 20:24] = np.nan
    z[5, [2, 8, 50, 98]] = np.inf
    if descending:
        energy, z = energy[::-1], z[:, ::-1]
    expected = _reference_second_derivative(energy, z, smooth)
    actual = energy_second_derivative(energy, z, smooth=smooth)
    np.testing.assert_allclose(actual, expected, rtol=2e-9, atol=2e-8)


@pytest.mark.parametrize("descending", [False, True])
def test_direct_sg_matches_scipy_including_edges(descending):
    from scipy.signal import savgol_filter

    energy = np.linspace(1.6, 2.0, 101)
    z = np.random.default_rng(19).normal(size=(5, energy.size))
    expected = savgol_filter(z, 21, 3, deriv=2, delta=energy[1]-energy[0],
                             axis=-1, mode="interp")
    if descending:
        energy, z, expected = energy[::-1], z[:, ::-1], expected[:, ::-1]
    np.testing.assert_allclose(energy_second_derivative(energy, z), expected,
                               rtol=1e-10, atol=1e-8)


def test_direct_sg_nonuniform_cubic_including_edges_and_gaps():
    energy = np.cumsum(np.random.default_rng(3).uniform(0.1, 0.5, 35))
    z = np.vstack((energy**2, energy**3 - 2*energy**2 + energy))
    z[:, 15:18] = np.nan
    expected = np.vstack((np.full(energy.size, 2.0), 6*energy - 4))
    expected[:, 15:18] = np.nan
    np.testing.assert_allclose(energy_second_derivative(energy, z), expected,
                               rtol=1e-10, atol=1e-10)


@pytest.mark.parametrize("size", [3, 4, 5, 6, 8, 10])
def test_direct_sg_adapts_short_segments(size):
    energy = np.arange(size, dtype=float)
    np.testing.assert_allclose(energy_second_derivative(energy, (energy**2)[None, :]),
                               2.0, atol=1e-12)


@pytest.mark.parametrize("polyorder", [0, 1])
def test_direct_second_derivative_requires_quadratic_order(polyorder):
    with pytest.raises(ValueError, match="at least 2"):
        energy_second_derivative(np.arange(12.0), np.ones((1, 12)), polyorder=polyorder)


def test_direct_sg_window_changes_noise_and_differs_from_smooth_then_difference():
    energy = np.linspace(0, 1, 101)
    z = np.random.default_rng(11).normal(size=(1, 101))
    direct = energy_second_derivative(energy, z)
    narrow = energy_second_derivative(energy, z, window_length=5)
    old = _reference_second_derivative(energy, z, True)
    assert np.std(direct[:, 5:-5]) < np.std(narrow[:, 5:-5])
    assert not np.allclose(direct, old)


def test_direct_sg_keeps_independent_gapped_rows_and_short_segments():
    from scipy.signal import savgol_filter

    energy = np.arange(45.0)
    z = np.random.default_rng(7).normal(size=(3, energy.size))
    z[0:2, 16:19] = np.nan
    z[2, [2, 5, 10, 30]] = np.inf
    expected = np.full_like(z, np.nan)
    for row_index, row in enumerate(z):
        indices = np.flatnonzero(np.isfinite(row))
        for segment in np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1):
            if segment.size < 3:
                continue
            window = min(21, segment.size)
            window -= 1 - window % 2
            expected[row_index, segment] = savgol_filter(
                row[segment], window, min(3, window - 1), deriv=2, mode="interp"
            )
    original = z.copy()
    np.testing.assert_allclose(energy_second_derivative(energy, z), expected, atol=1e-12)
    np.testing.assert_array_equal(z, original)
