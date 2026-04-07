from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks, savgol_filter

PeakMode = Literal["Peaks", "Dips"]
FitModel = Literal["Lorentzian", "Gaussian"]


@dataclass(frozen=True)
class FitResult:
    model: FitModel
    x: np.ndarray
    y_fit: np.ndarray
    components: np.ndarray  # shape: (n_peaks, n_x)
    centers: np.ndarray
    amplitudes: np.ndarray
    widths: np.ndarray
    baseline: float
    rmse: float
    r_squared: float


def _clamp_sg_window(n: int, requested: int) -> int:
    win = max(3, int(requested))
    if win % 2 == 0:
        win += 1
    if win > n:
        win = n if n % 2 == 1 else n - 1
    return max(3, int(win))


def find_peak_indices(
    x: np.ndarray,
    y: np.ndarray,
    *,
    mode: PeakMode,
    prominence_frac: float,
    min_distance: int,
    max_peaks: int,
) -> np.ndarray:
    """Find peaks in the original signal using scipy find_peaks.

    Returns indices into the x/y arrays.
    """
    xv = np.asarray(x, float).ravel()
    yv = np.asarray(y, float).ravel()
    if xv.size < 3 or yv.size != xv.size:
        return np.asarray([], dtype=int)
    signal = yv if mode == "Peaks" else -yv
    spread = float(np.nanmax(signal) - np.nanmin(signal))
    prom = max(0.0, float(prominence_frac)) * spread
    peaks, props = find_peaks(signal, prominence=prom, distance=max(1, int(min_distance)))
    if peaks.size == 0:
        return peaks.astype(int)
    prom_arr = np.asarray(props.get("prominences", np.zeros_like(peaks, dtype=float)), float)
    rank = np.argsort(prom_arr)[::-1]
    keep = peaks[rank[: max(1, int(max_peaks))]]
    return np.asarray(np.sort(keep), dtype=int)


def find_peaks_second_derivative(
    x: np.ndarray,
    y: np.ndarray,
    *,
    mode: PeakMode = "Peaks",
    sg_window: int = 11,
    sg_polyorder: int = 3,
    d2_prom_frac: float = 0.03,
    d2_distance: int = 3,
    d2_sep_ev: float = 0.0,
    max_peaks: int = 6,
    target_count: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Find peaks using the second derivative of the Savitzky-Golay smoothed signal.

    For 'Peaks' mode: minima of d²y/dx² (concave-down) → peaks in original signal.
    For 'Dips' mode: maxima of d²y/dx² (concave-up) → dips in original signal.

    Returns:
        (peak_indices, d2y) where peak_indices are indices into x/y and d2y is
        the second derivative array (same length as x/y).
    """
    xv = np.asarray(x, float).ravel()
    yv = np.asarray(y, float).ravel()
    n = xv.size
    if n < 5 or yv.size != n:
        return np.asarray([], dtype=int), np.zeros(n, dtype=float)

    win = _clamp_sg_window(n, sg_window)
    polyorder = min(max(1, int(sg_polyorder)), win - 1)

    # Second derivative via SG filter with deriv=2
    d2y = savgol_filter(yv, window_length=win, polyorder=polyorder, deriv=2)

    # Peaks in original → minima in d2y → find peaks in -d2y
    # Dips in original → maxima in d2y → find peaks in d2y
    signal_d2 = -d2y if mode == "Peaks" else d2y

    spread = float(np.nanmax(signal_d2) - np.nanmin(signal_d2))
    prom = max(0.0, float(d2_prom_frac)) * spread

    effective_distance = max(1, int(d2_distance))
    if d2_sep_ev > 0.0 and n > 1:
        dx = float(np.nanmedian(np.abs(np.diff(xv))))
        if dx > 0:
            sep_pts = max(1, int(d2_sep_ev / dx))
            effective_distance = max(effective_distance, sep_pts)

    peaks, props = find_peaks(signal_d2, prominence=prom, distance=effective_distance)
    if peaks.size == 0:
        return peaks.astype(int), d2y

    prom_arr = np.asarray(props.get("prominences", np.zeros_like(peaks, dtype=float)), float)

    n_keep = int(target_count) if target_count > 0 else int(max_peaks)
    n_keep = max(1, n_keep)

    rank = np.argsort(prom_arr)[::-1]
    keep = peaks[rank[:n_keep]]
    return np.asarray(np.sort(keep), dtype=int), d2y


def _lorentz(x: np.ndarray, amp: float, cen: float, wid: float) -> np.ndarray:
    ww = max(abs(float(wid)), 1e-12)
    return amp * (ww * ww / ((x - cen) * (x - cen) + ww * ww))


def _gauss(x: np.ndarray, amp: float, cen: float, wid: float) -> np.ndarray:
    ww = max(abs(float(wid)), 1e-12)
    return amp * np.exp(-0.5 * ((x - cen) / ww) ** 2)


def fit_multi_peak(
    x: np.ndarray,
    y: np.ndarray,
    *,
    centers_hint: np.ndarray,
    model: FitModel,
    mode: PeakMode,
    linewidth_min: float = 1e-9,
    linewidth_max: float = 0.0,   # 0 = auto (2× x_range)
    peak_move_limit: float = 0.0,  # 0 = unconstrained
    maxfev: int = 20000,
) -> FitResult:
    """Fit a multi-peak model (Lorentzian or Gaussian) + constant baseline.

    New parameters vs original:
        linewidth_min: minimum FWHM/width bound in data units (eV). 0 = tiny.
        linewidth_max: maximum FWHM/width bound in data units (eV). 0 = auto.
        peak_move_limit: max allowed center shift from seed (eV). 0 = free.
        maxfev: max function evaluations for curve_fit.
    """
    xv = np.asarray(x, float).ravel()
    yv = np.asarray(y, float).ravel()
    centers = np.asarray(centers_hint, float).ravel()
    if xv.size < 5 or yv.size != xv.size:
        raise ValueError("Not enough valid data points for fitting.")
    if centers.size == 0:
        raise ValueError("No peak centers provided.")

    fn = _lorentz if model == "Lorentzian" else _gauss
    n = int(centers.size)
    xrange = float(np.nanmax(xv) - np.nanmin(xv))
    width0 = max(xrange / max(8.0 * n, 20.0), 1e-6)
    baseline0 = float(np.nanmedian(yv))
    sign = 1.0 if mode == "Peaks" else -1.0

    lw_min = max(float(linewidth_min), 1e-9)
    lw_max_v = float(linewidth_max)
    lw_max = lw_max_v if lw_max_v > lw_min else (xrange * 2.0 + 1e-9)

    p0: list[float] = [baseline0]
    lower: list[float] = [float(np.nanmin(yv) - abs(np.nanmax(yv - baseline0)) * 2.0)]
    upper: list[float] = [float(np.nanmax(yv) + abs(np.nanmax(yv - baseline0)) * 2.0)]

    for c in centers:
        idx = int(np.argmin(np.abs(xv - c)))
        amp0 = sign * max(abs(float(yv[idx] - baseline0)), 1e-6)
        p0.extend([amp0, float(c), width0])

        pml = float(peak_move_limit)
        if pml > 0:
            c_lo = float(max(np.nanmin(xv), c - pml))
            c_hi = float(min(np.nanmax(xv), c + pml))
        else:
            c_lo = float(np.nanmin(xv))
            c_hi = float(np.nanmax(xv))

        if sign > 0:
            lower.extend([0.0, c_lo, lw_min])
            upper.extend([np.inf, c_hi, lw_max])
        else:
            lower.extend([-np.inf, c_lo, lw_min])
            upper.extend([0.0, c_hi, lw_max])

    def model_fn(xarr: np.ndarray, *p: float) -> np.ndarray:
        base = p[0]
        out = np.full_like(xarr, base, dtype=float)
        for i in range(n):
            a, c, w = p[1 + 3 * i : 1 + 3 * (i + 1)]
            out = out + fn(xarr, a, c, w)
        return out

    popt, _ = curve_fit(
        model_fn,
        xv,
        yv,
        p0=np.asarray(p0, dtype=float),
        bounds=(np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)),
        maxfev=int(maxfev),
    )
    y_fit = model_fn(xv, *popt)
    comps = []
    amps = []
    cens = []
    widths = []
    for i in range(n):
        a, c, w = popt[1 + 3 * i : 1 + 3 * (i + 1)]
        comps.append(fn(xv, float(a), float(c), float(w)))
        amps.append(float(a))
        cens.append(float(c))
        widths.append(float(abs(w)))
    residual = np.asarray(yv - y_fit, dtype=float)
    rmse = float(np.sqrt(np.nanmean(residual * residual)))
    ss_res = float(np.nansum(residual * residual))
    y_mean = float(np.nanmean(yv))
    ss_tot = float(np.nansum((yv - y_mean) * (yv - y_mean)))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 1.0
    return FitResult(
        model=model,
        x=np.asarray(xv, dtype=float),
        y_fit=np.asarray(y_fit, dtype=float),
        components=np.asarray(comps, dtype=float),
        centers=np.asarray(cens, dtype=float),
        amplitudes=np.asarray(amps, dtype=float),
        widths=np.asarray(widths, dtype=float),
        baseline=float(popt[0]),
        rmse=rmse,
        r_squared=float(r_squared),
    )
