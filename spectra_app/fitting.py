from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks, savgol_filter
from scipy.special import voigt_profile

PeakMode = Literal["Peaks", "Dips", "Both"]
FitModel = Literal["Lorentzian", "Gaussian", "Voigt"]


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
    sigmas: np.ndarray | None = None  # Voigt only; identical to widths
    gammas: np.ndarray | None = None  # Voigt only; Lorentzian HWHM

    def __post_init__(self) -> None:
        if self.model == 'Voigt' and self.sigmas is not None and not np.array_equal(self.sigmas, self.widths):
            raise ValueError('Voigt sigmas must equal the Gaussian widths.')


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
    max_dips: int = 6,
) -> tuple[np.ndarray, np.ndarray]:
    """Find peaks/dips in the original signal using scipy find_peaks.

    Returns (indices, kinds) where kinds is a bool array: True=peak, False=dip.
    In 'Both' mode detects peaks and dips independently with separate limits,
    then merges; distance suppression applies only within each kind.
    """
    xv = np.asarray(x, float).ravel()
    yv = np.asarray(y, float).ravel()
    if xv.size < 3 or yv.size != xv.size:
        return np.asarray([], dtype=int), np.asarray([], dtype=bool)

    dist = max(1, int(min_distance))
    spread = float(np.nanmax(yv) - np.nanmin(yv))
    prom = max(0.0, float(prominence_frac)) * spread

    def _run(signal: np.ndarray, n_keep: int) -> tuple[np.ndarray, np.ndarray]:
        ps, props = find_peaks(signal, prominence=prom, distance=dist)
        if ps.size == 0:
            return ps.astype(int), np.asarray([], dtype=float)
        pa = np.asarray(props.get("prominences", np.zeros_like(ps, dtype=float)), float)
        rank = np.argsort(pa)[::-1]
        sel = rank[: max(1, n_keep)]
        return ps[sel].astype(int), pa[sel]

    if mode == "Both":
        peak_idx, peak_prom = _run(yv, max_peaks)
        dip_idx, dip_prom = _run(-yv, max_dips)
        if peak_idx.size == 0 and dip_idx.size == 0:
            return np.asarray([], dtype=int), np.asarray([], dtype=bool)
        all_idx = np.concatenate([peak_idx, dip_idx]).astype(int)
        all_kinds = np.concatenate([
            np.ones(peak_idx.size, dtype=bool),
            np.zeros(dip_idx.size, dtype=bool),
        ])
        all_prom = np.concatenate([peak_prom, dip_prom])
        order = np.argsort(all_idx, kind="stable")
        all_idx = all_idx[order]
        all_kinds = all_kinds[order]
        all_prom = all_prom[order]
        # Never suppress a peak because of a nearby dip (or vice versa).
        n = all_idx.size
        keep = np.ones(n, dtype=bool)
        for i in range(n):
            if not keep[i]:
                continue
            for j in range(i + 1, n):
                if all_idx[j] - all_idx[i] > dist:
                    break
                if keep[j] and all_kinds[i] == all_kinds[j]:
                    if all_prom[i] >= all_prom[j]:
                        keep[j] = False
                    else:
                        keep[i] = False
                        break
        return all_idx[keep], all_kinds[keep]

    signal = yv if mode == "Peaks" else -yv
    idx, _ = _run(signal, max_peaks)
    idx = np.asarray(np.sort(idx), dtype=int)
    return idx, np.full(idx.size, mode == "Peaks", dtype=bool)


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
    max_dips: int = 6,
    target_count: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Find peaks/dips using the second derivative of the Savitzky-Golay smoothed signal.

    For 'Peaks' mode: minima of d²y/dx² (concave-down) → peaks in original signal.
    For 'Dips'  mode: maxima of d²y/dx² (concave-up)   → dips in original signal.
    For 'Both'  mode: runs both detections independently, then merges.

    Returns:
        (peak_indices, kinds, d2y)
        kinds is a bool array: True=peak, False=dip.
        d2y is the second derivative array (same length as x/y).
    """
    xv = np.asarray(x, float).ravel()
    yv = np.asarray(y, float).ravel()
    n = xv.size
    empty_kinds = np.asarray([], dtype=bool)
    empty_idx = np.asarray([], dtype=int)
    if n < 5 or yv.size != n:
        return empty_idx, empty_kinds, np.zeros(n, dtype=float)

    win = _clamp_sg_window(n, sg_window)
    polyorder = min(max(1, int(sg_polyorder)), win - 1)
    d2y = savgol_filter(yv, window_length=win, polyorder=polyorder, deriv=2)

    effective_distance = max(1, int(d2_distance))
    if d2_sep_ev > 0.0 and n > 1:
        dx = float(np.nanmedian(np.abs(np.diff(xv))))
        if dx > 0:
            sep_pts = max(1, int(d2_sep_ev / dx))
            effective_distance = max(effective_distance, sep_pts)

    def _run_d2(signal: np.ndarray, n_keep: int) -> tuple[np.ndarray, np.ndarray]:
        spread = float(np.nanmax(signal) - np.nanmin(signal))
        prom = max(0.0, float(d2_prom_frac)) * spread
        ps, props = find_peaks(signal, prominence=prom, distance=effective_distance)
        if ps.size == 0:
            return ps.astype(int), np.asarray([], dtype=float)
        pa = np.asarray(props.get("prominences", np.zeros_like(ps, dtype=float)), float)
        rank = np.argsort(pa)[::-1]
        sel = rank[: max(1, n_keep)]
        return ps[sel].astype(int), pa[sel]

    if mode == "Both":
        n_peaks_keep = max(1, int(target_count) if target_count > 0 else int(max_peaks))
        n_dips_keep  = max(1, int(target_count) if target_count > 0 else int(max_dips))
        peak_idx, peak_prom = _run_d2(-d2y, n_peaks_keep)
        dip_idx,  dip_prom  = _run_d2(d2y,  n_dips_keep)
        if peak_idx.size == 0 and dip_idx.size == 0:
            return empty_idx, empty_kinds, d2y
        all_idx = np.concatenate([peak_idx, dip_idx]).astype(int)
        all_kinds = np.concatenate([
            np.ones(peak_idx.size, dtype=bool),
            np.zeros(dip_idx.size, dtype=bool),
        ])
        all_prom = np.concatenate([peak_prom, dip_prom])
        order = np.argsort(all_idx, kind="stable")
        all_idx = all_idx[order]
        all_kinds = all_kinds[order]
        all_prom = all_prom[order]
        nall = all_idx.size
        keep = np.ones(nall, dtype=bool)
        for i in range(nall):
            if not keep[i]:
                continue
            for j in range(i + 1, nall):
                if all_idx[j] - all_idx[i] > effective_distance:
                    break
                if keep[j] and all_kinds[i] == all_kinds[j]:
                    if all_prom[i] >= all_prom[j]:
                        keep[j] = False
                    else:
                        keep[i] = False
                        break
        return all_idx[keep], all_kinds[keep], d2y

    # Peaks: find minima of d2y → peaks in (-d2y)
    # Dips:  find maxima of d2y → peaks in ( d2y)
    signal_d2 = -d2y if mode == "Peaks" else d2y
    n_keep = max(1, int(target_count) if target_count > 0 else int(max_peaks))
    idx, _ = _run_d2(signal_d2, n_keep)
    idx = np.asarray(np.sort(idx), dtype=int)
    return idx, np.full(idx.size, mode == "Peaks", dtype=bool), d2y


def _lorentz(x: np.ndarray, amp: float, cen: float, wid: float) -> np.ndarray:
    ww = max(abs(float(wid)), 1e-12)
    return amp * (ww * ww / ((x - cen) * (x - cen) + ww * ww))


def _gauss(x: np.ndarray, amp: float, cen: float, wid: float) -> np.ndarray:
    ww = max(abs(float(wid)), 1e-12)
    return amp * np.exp(-0.5 * ((x - cen) / ww) ** 2)


def _voigt(x: np.ndarray, amp: float, cen: float, sigma: float, gamma: float) -> np.ndarray:
    """True Voigt convolution normalized to signed peak height, not area."""
    return amp * voigt_profile(x-cen, sigma, gamma) / voigt_profile(0., sigma, gamma)


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
    kinds: np.ndarray | None = None,  # per-component: True=peak, False=dip (used in Both mode)
    initial_amplitudes: np.ndarray | None = None,
    initial_widths: np.ndarray | None = None,
    initial_gammas: np.ndarray | None = None,
    fixed_centers: bool | np.ndarray = False,
    center_bounds: tuple[np.ndarray, np.ndarray] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> FitResult:
    """Fit signed components plus a constant baseline to every finite X/Y pair.

    Amplitudes are signed heights in all models. Gaussian widths are sigma,
    Lorentzian widths are gamma (HWHM), and Voigt widths/sigmas are Gaussian
    sigma with separate Lorentzian gammas. Both Voigt widths obey linewidth
    bounds. Optional seed arrays must contain one value per component; initial
    widths are clipped into valid bounds. In Both mode, kinds assigns component
    signs (True=peak, False=dip), otherwise the global mode assigns the sign.

    Fixed centers are excluded from optimizer variables and returned exactly.
    center_bounds is a (lower, upper) pair of scalars or per-component arrays,
    intersected with the data interval and peak_move_limit when specified.

    Optimization uses centered, scaled Y values internally; all public seeds,
    components and results retain the input Y units. R-squared is NaN for a
    constant signal, where explained variance is undefined. check_cancelled may
    raise to stop fitting before, during, or immediately after optimization.
    """
    if check_cancelled is not None:
        check_cancelled()
    xv = np.asarray(x, float).ravel()
    yv = np.asarray(y, float).ravel()
    centers = np.asarray(centers_hint, float).ravel().copy()
    if yv.size != xv.size:
        raise ValueError("X and Y lengths must match for fitting.")
    finite = np.isfinite(xv) & np.isfinite(yv)
    xv, yv = xv[finite], yv[finite]
    if xv.size < 5:
        raise ValueError("Not enough valid data points for fitting.")
    if centers.size == 0 or not np.all(np.isfinite(centers)):
        raise ValueError("Finite peak centers must be provided.")
    if model not in ('Lorentzian', 'Gaussian', 'Voigt') or mode not in ('Peaks', 'Dips', 'Both'):
        raise ValueError('Unknown fit model or mode.')
    fn = {'Lorentzian': _lorentz, 'Gaussian': _gauss, 'Voigt': _voigt}[model]
    n = int(centers.size)
    xmin, xmax = float(np.min(xv)), float(np.max(xv))
    xrange = xmax-xmin
    if xrange <= 0:
        raise ValueError('Fitting requires a nonzero X interval.')
    y_offset = float(np.median(yv))
    y_scale = float(np.max(np.abs(yv-y_offset)))
    if y_scale == 0.:
        y_scale = abs(y_offset) or 1.
    scaled_y = (yv-y_offset)/y_scale
    baseline0 = 0.
    for name, value in (('linewidth_min', linewidth_min), ('linewidth_max', linewidth_max),
                        ('peak_move_limit', peak_move_limit)):
        if not np.isfinite(value) or value < 0:
            raise ValueError(f'{name} must be finite and nonnegative.')
    lw_min = max(float(linewidth_min), 1e-9)
    lw_max = float(linewidth_max) if linewidth_max > 0 else max(xrange*2.+1e-9, lw_min*2.)
    if lw_max <= lw_min:
        raise ValueError('Maximum linewidth must be greater than minimum linewidth.')
    width0 = float(np.clip(xrange / max(8.0 * n, 20.0), lw_min, lw_max))

    def seed_array(values, name: str, default: np.ndarray) -> np.ndarray:
        if values is None:
            return default.copy()
        result = np.asarray(values, dtype=float).ravel()
        if result.size != n or not np.all(np.isfinite(result)):
            raise ValueError(f'{name} must contain one finite value per component.')
        return result.copy()

    fixed = np.asarray(fixed_centers, dtype=bool)
    fixed = np.full(n, bool(fixed)) if fixed.ndim == 0 else fixed.ravel().copy()
    if fixed.size != n:
        raise ValueError('fixed_centers must contain one flag per component.')
    signs = np.full(n, -1. if mode == 'Dips' else 1.)
    if mode == 'Both' and kinds is not None:
        kind_values = np.asarray(kinds, dtype=bool).ravel()
        if kind_values.size != n:
            raise ValueError('kinds must contain one flag per component.')
        signs = np.where(kind_values, 1., -1.)
    default_amplitudes = np.array([
        signs[i]*max(abs(float(scaled_y[np.argmin(np.abs(xv-c))])), 1e-6)
        for i, c in enumerate(centers)])
    amplitudes = (default_amplitudes if initial_amplitudes is None else
                  seed_array(initial_amplitudes, 'initial_amplitudes', default_amplitudes)/y_scale)
    if np.any(amplitudes*signs < 0):
        raise ValueError('Initial amplitude signs must agree with the peak/dip kinds.')
    widths = seed_array(initial_widths, 'initial_widths', np.full(n, width0))
    gammas = seed_array(initial_gammas, 'initial_gammas', np.full(n, width0))
    if np.any(widths <= 0) or np.any(gammas < 0):
        raise ValueError('Initial widths must be positive and gammas nonnegative.')
    if initial_gammas is not None and model != 'Voigt':
        raise ValueError('initial_gammas is only supported for Voigt fits.')
    widths, gammas = np.clip(widths, lw_min, lw_max), np.clip(gammas, lw_min, lw_max)
    center_lower, center_upper = np.full(n, xmin), np.full(n, xmax)
    if peak_move_limit > 0:
        center_lower = np.maximum(center_lower, centers-peak_move_limit)
        center_upper = np.minimum(center_upper, centers+peak_move_limit)
    if center_bounds is not None:
        try:
            lower_values, upper_values = center_bounds
            lower_values = np.broadcast_to(np.asarray(lower_values, float), (n,))
            upper_values = np.broadcast_to(np.asarray(upper_values, float), (n,))
        except (TypeError, ValueError) as error:
            raise ValueError('center_bounds must be a lower/upper pair of scalar or component arrays.') from error
        if not np.all(np.isfinite(lower_values)) or not np.all(np.isfinite(upper_values)):
            raise ValueError('Center bounds must be finite.')
        center_lower, center_upper = np.maximum(center_lower, lower_values), np.minimum(center_upper, upper_values)
    if np.any(center_lower > center_upper) or np.any(centers < center_lower) or np.any(centers > center_upper):
        raise ValueError('Initial centers must lie inside the data interval and center bounds.')
    fixed |= center_lower == center_upper

    # Use full component arrays for evaluation and only optimize free entries.
    stride = 4 if model == 'Voigt' else 3
    parameters = np.empty((n, stride), dtype=float)
    parameters[:, 0], parameters[:, 1], parameters[:, 2] = amplitudes, centers, widths
    if model == 'Voigt':
        parameters[:, 3] = gammas
    parameter_lower = np.empty_like(parameters)
    parameter_upper = np.empty_like(parameters)
    parameter_lower[:, 0], parameter_upper[:, 0] = np.where(signs > 0, 0., -np.inf), np.where(signs > 0, np.inf, 0.)
    parameter_lower[:, 1], parameter_upper[:, 1] = center_lower, center_upper
    parameter_lower[:, 2:], parameter_upper[:, 2:] = lw_min, lw_max
    free_mask = np.ones_like(parameters, dtype=bool)
    free_mask[:, 1] = ~fixed
    baseline_margin = max(float(np.max(np.abs(scaled_y)))*2., 1e-6)
    p0 = np.r_[baseline0, parameters[free_mask]]
    lower = np.r_[np.min(scaled_y)-baseline_margin, parameter_lower[free_mask]]
    upper = np.r_[np.max(scaled_y)+baseline_margin, parameter_upper[free_mask]]
    if xv.size <= p0.size:
        raise ValueError(f'Fitting needs more valid samples than free parameters ({p0.size}).')

    def unpack(p: np.ndarray) -> np.ndarray:
        values = parameters.copy()
        values[free_mask] = p[1:]
        return values

    def model_fn(xarr: np.ndarray, *p: float) -> np.ndarray:
        if check_cancelled is not None:
            check_cancelled()
        return np.full_like(xarr, p[0], dtype=float) + np.sum([
            fn(xarr, *component) for component in unpack(np.asarray(p))], axis=0)

    popt, _ = curve_fit(
        model_fn,
        xv,
        scaled_y,
        p0=p0,
        bounds=(lower, upper),
        maxfev=int(maxfev),
    )
    if check_cancelled is not None:
        check_cancelled()
    scaled_fit = model_fn(xv, *popt)
    fitted = unpack(popt)
    comps = np.asarray([fn(xv, *component) for component in fitted], dtype=float)*y_scale
    baseline = y_offset + float(popt[0])*y_scale
    y_fit = baseline + np.sum(comps, axis=0)
    fitted[:, 0] *= y_scale
    # Dimensionless residual sums avoid squaring physical values that may
    # underflow or overflow merely because the caller changed signal units.
    residual = scaled_y-scaled_fit
    rmse = float(np.sqrt(np.mean(residual*residual))*y_scale)
    ss_res = float(np.sum(residual*residual))
    ss_tot = float(np.sum((scaled_y-np.mean(scaled_y))**2))
    r_squared = 1.0-ss_res/ss_tot if ss_tot > 0. else float('nan')
    return FitResult(
        model=model,
        x=np.asarray(xv, dtype=float),
        y_fit=np.asarray(y_fit, dtype=float),
        components=np.asarray(comps, dtype=float),
        centers=fitted[:, 1].copy(),
        amplitudes=fitted[:, 0].copy(),
        widths=fitted[:, 2].copy(),
        baseline=baseline,
        rmse=rmse,
        r_squared=float(r_squared),
        sigmas=fitted[:, 2].copy() if model == 'Voigt' else None,
        gammas=fitted[:, 3].copy() if model == 'Voigt' else None,
    )
