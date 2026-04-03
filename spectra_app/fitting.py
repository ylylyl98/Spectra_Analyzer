from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks

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


def find_peak_indices(
    x: np.ndarray,
    y: np.ndarray,
    *,
    mode: PeakMode,
    prominence_frac: float,
    min_distance: int,
    max_peaks: int,
) -> np.ndarray:
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
) -> FitResult:
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

    p0: list[float] = [baseline0]
    lower: list[float] = [float(np.nanmin(yv) - abs(np.nanmax(yv - baseline0)) * 2.0)]
    upper: list[float] = [float(np.nanmax(yv) + abs(np.nanmax(yv - baseline0)) * 2.0)]
    for c in centers:
        idx = int(np.argmin(np.abs(xv - c)))
        amp0 = sign * max(abs(float(yv[idx] - baseline0)), 1e-6)
        p0.extend([amp0, float(c), width0])
        if sign > 0:
            lower.extend([0.0, float(np.nanmin(xv)), 1e-9])
            upper.extend([np.inf, float(np.nanmax(xv)), xrange * 2.0 + 1e-9])
        else:
            lower.extend([-np.inf, float(np.nanmin(xv)), 1e-9])
            upper.extend([0.0, float(np.nanmax(xv)), xrange * 2.0 + 1e-9])

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
        maxfev=20000,
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
    return FitResult(
        model=model,
        x=np.asarray(xv, dtype=float),
        y_fit=np.asarray(y_fit, dtype=float),
        components=np.asarray(comps, dtype=float),
        centers=np.asarray(cens, dtype=float),
        amplitudes=np.asarray(amps, dtype=float),
        widths=np.asarray(widths, dtype=float),
        baseline=float(popt[0]),
    )
