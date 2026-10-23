"""Numerical processing helpers for spectra and 2D gate data."""

from __future__ import annotations

from typing import Callable

import numpy as np


def _validate_smoothing_parameters(window_length: int, polyorder: int) -> None:
    """Validate parameters for the local polynomial fit."""
    if isinstance(window_length, (bool, np.bool_)) or not isinstance(
        window_length, (int, np.integer)
    ):
        raise ValueError("window_length must be an integer")
    if window_length < 3:
        raise ValueError("window_length must be at least 3")
    if window_length % 2 == 0:
        raise ValueError("window_length must be odd")
    if isinstance(polyorder, (bool, np.bool_)) or not isinstance(
        polyorder, (int, np.integer)
    ):
        raise ValueError("polyorder must be an integer")
    if polyorder < 0:
        raise ValueError("polyorder must be non-negative")
    if polyorder >= window_length:
        raise ValueError("polyorder must be less than window_length")


def _local_polynomial_second_derivative(
    energy: np.ndarray,
    values: np.ndarray,
    window_length: int,
    polyorder: int,
    check_cancelled: Callable[[], None] | None = None,
) -> np.ndarray:
    """Differentiate local fits, reusing coordinate weights across finite rows."""
    effective_window = min(window_length, energy.size)
    if effective_window % 2 == 0:
        effective_window -= 1
    effective_order = min(polyorder, effective_window - 1)

    result = np.empty_like(values, dtype=float)
    target = np.zeros(effective_order + 1)
    target[2] = 2.0
    for index in range(energy.size):
        if check_cancelled is not None:
            check_cancelled()
        start = max(0, min(index - effective_window // 2, energy.size - effective_window))
        stop = start + effective_window
        offset = energy[start:stop] - energy[index]
        scale = np.max(np.abs(offset))
        coordinate = offset / scale
        design = np.vander(coordinate, N=effective_order + 1, increasing=True)
        # Twice the quadratic coefficient gives d2/dE2 after rescaling.
        # Reuse these derivative weights for every gate with the same bounds.
        weights = np.linalg.lstsq(design.T, target, rcond=None)[0]
        result[:, index] = (values[:, start:stop] @ weights) / scale**2
    return result


def _quadratic_second_derivative(
    energy: np.ndarray,
    values: np.ndarray,
) -> np.ndarray:
    """Evaluate the same three-point quadratics by divided differences."""
    slopes = np.diff(values, axis=1) / np.diff(energy)
    interior = 2.0 * np.diff(slopes, axis=1) / (energy[2:] - energy[:-2])
    result = np.empty_like(values, dtype=float)
    result[:, 1:-1] = interior
    # The edge samples use the first/last triple, as in the local fit.
    result[:, 0] = interior[:, 0]
    result[:, -1] = interior[:, -1]
    return result


def energy_second_derivative(
    energy: np.ndarray,
    z: np.ndarray,
    *,
    smooth: bool = True,
    window_length: int = 21,
    polyorder: int = 3,
    check_cancelled: Callable[[], None] | None = None,
) -> np.ndarray:
    """Compute the second energy derivative for rows of a 2D spectrum.

    Parameters
    ----------
    energy:
        One-dimensional energy coordinates. They must contain at least three
        finite, unique, strictly monotonic values in either direction.
    z:
        Two-dimensional gate-by-energy values. Non-finite values delimit gaps
        and remain non-finite in the result.
    smooth:
        Directly differentiate coordinate-local polynomial fits (Savitzky-Golay
        style) within each finite segment. False uses three-point differences.
    window_length, polyorder:
        Smoothing window and polynomial order. The window must be odd, at
        least three, and longer than ``polyorder``. With smoothing enabled,
        the order must be at least two. Short segments use the largest odd
        window that fits and reduce the order when necessary (minimum two).
        Segments shorter than three points return NaN. Edge fits use the
        first/last full window, matching SG interpolation on uniform grids.
    check_cancelled:
        Optional cooperative cancellation callback. Called before processing,
        between finite segments and between local polynomial fits.

    Returns
    -------
    numpy.ndarray
        A new floating-point array with the same shape as ``z``.
    """
    if check_cancelled is not None:
        check_cancelled()
    energy_array = np.asarray(energy, dtype=float)
    if energy_array.ndim != 1:
        raise ValueError("energy must be a 1D array")
    if energy_array.size < 3:
        raise ValueError("energy must contain at least 3 points")
    if not np.isfinite(energy_array).all():
        raise ValueError("energy must contain only finite values")
    differences = np.diff(energy_array)
    if not (np.all(differences > 0) or np.all(differences < 0)):
        raise ValueError("energy must be strictly monotonic with unique values")

    values = np.asarray(z, dtype=float)
    if values.ndim != 2:
        raise ValueError("z must be a 2D array with rows as gates")
    if values.shape[1] != energy_array.size:
        raise ValueError("z must have the same number of columns as energy")
    if not isinstance(smooth, (bool, np.bool_)):
        raise ValueError("smooth must be a boolean")
    _validate_smoothing_parameters(window_length, polyorder)
    if smooth and polyorder < 2:
        raise ValueError("polyorder must be at least 2 for a second derivative")

    descending = differences[0] < 0
    work_energy = energy_array[::-1] if descending else energy_array
    work_values = values[:, ::-1] if descending else values
    result = np.full(work_values.shape, np.nan, dtype=float)

    # Group equal finite bounds so derivative fits depend on coordinates, not
    # the number of gates. These groups live only for this call.
    segments: dict[tuple[int, int], list[int]] = {}
    finite = np.isfinite(work_values)
    complete_rows = np.flatnonzero(finite.all(axis=1))
    if complete_rows.size:
        segments[(0, work_energy.size)] = complete_rows.tolist()
    for row_index in np.flatnonzero(~finite.all(axis=1)):
        if check_cancelled is not None:
            check_cancelled()
        boundaries = np.flatnonzero(np.diff(np.r_[False, finite[row_index], False]))
        for start, stop in boundaries.reshape(-1, 2):
            if stop - start >= 3:
                segments.setdefault((int(start), int(stop)), []).append(int(row_index))

    for (start, stop), rows in segments.items():
        if check_cancelled is not None:
            check_cancelled()
        segment_energy = work_energy[start:stop]
        segment_values = work_values[rows, start:stop]
        if smooth:
            derivative = _local_polynomial_second_derivative(
                segment_energy, segment_values, window_length, polyorder, check_cancelled
            )
        else:
            derivative = _quadratic_second_derivative(segment_energy, segment_values)
        result[rows, start:stop] = derivative

    if check_cancelled is not None:
        check_cancelled()
    return result[:, ::-1] if descending else result
