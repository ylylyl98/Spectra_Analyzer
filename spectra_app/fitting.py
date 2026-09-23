from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks, savgol_filter

PeakMode = Literal["Peaks", "Dips", "Both"]
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
    max_dips: int = 6,
) -> tuple[np.ndarray, np.ndarray]:
    """Find peaks/dips in the original signal using scipy find_peaks.

    Returns (indices, kinds) where kinds is a bool array: True=peak, False=dip.
    In 'Both' mode detects peaks and dips independently with separate limits,
    then merges and deduplicates collisions within min_distance.
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
        # Deduplicate within dist: keep the more prominent of any pair
        n = all_idx.size
        keep = np.ones(n, dtype=bool)
        for i in range(n):
            if not keep[i]:
                continue
            for j in range(i + 1, n):
                if all_idx[j] - all_idx[i] > dist:
                    break
                if keep[j]:
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
                if keep[j]:
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
) -> FitResult:
    """Fit a multi-peak model (Lorentzian or Gaussian) + constant baseline.

    Parameters
    ----------
    kinds : optional bool array, length == len(centers_hint).
        When mode == 'Both', supplies per-component sign: True → positive amplitude
        (peak), False → negative amplitude (dip).  Ignored in Peaks/Dips modes.
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
    baseline0 = float(np.nanmedian(yv))

    # Global sign for non-Both modes; per-component sign computed below for Both.
    global_sign = 1.0 if mode == "Peaks" else (-1.0 if mode == "Dips" else None)

    lw_min = max(float(linewidth_min), 1e-9)
    lw_max_v = float(linewidth_max)
    lw_max = lw_max_v if lw_max_v > lw_min else (xrange * 2.0 + 1e-9)
    lw_max = max(lw_max, lw_min)
    # Keep the optimizer's initial width inside the configured bounds.  This
    # matters when a cropped range makes the heuristic width narrower than the
    # GUI's point-based minimum.
    width0 = float(np.clip(xrange / max(8.0 * n, 20.0), lw_min, lw_max))

    p0: list[float] = [baseline0]
    lower: list[float] = [float(np.nanmin(yv) - abs(np.nanmax(yv - baseline0)) * 2.0)]
    upper: list[float] = [float(np.nanmax(yv) + abs(np.nanmax(yv - baseline0)) * 2.0)]

    for i, c in enumerate(centers):
        idx = int(np.argmin(np.abs(xv - c)))
        if global_sign is None:
            # Both mode: determine sign per component
            if kinds is not None and i < kinds.size:
                sign_i = 1.0 if bool(kinds[i]) else -1.0
            else:
                sign_i = 1.0  # default to peak if kinds unavailable
        else:
            sign_i = global_sign

        amp0 = sign_i * max(abs(float(yv[idx] - baseline0)), 1e-6)
        p0.extend([amp0, float(c), width0])

        pml = float(peak_move_limit)
        if pml > 0:
            c_lo = float(max(np.nanmin(xv), c - pml))
            c_hi = float(min(np.nanmax(xv), c + pml))
        else:
            c_lo = float(np.nanmin(xv))
            c_hi = float(np.nanmax(xv))

        if sign_i > 0:
            lower.extend([0.0, c_lo, lw_min])
            upper.extend([np.inf, c_hi, lw_max])
        else:
            lower.extend([-np.inf, c_lo, lw_min])
            upper.extend([0.0, c_hi, lw_max])

    def model_fn(xarr: np.ndarray, *p: float) -> np.ndarray:
        base = p[0]
        out = np.full_like(xarr, base, dtype=float)
        for k in range(n):
            a, c, w = p[1 + 3 * k : 1 + 3 * (k + 1)]
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
    for k in range(n):
        a, c, w = popt[1 + 3 * k : 1 + 3 * (k + 1)]
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
