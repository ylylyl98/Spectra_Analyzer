"""Qt-independent, serializable candidate collection and reversible filtering.

Collection never applies acceptance thresholds or a count limit. Keep its complete
result and reapply filters to that result when the user changes acceptance rules.
Metrics describe the detector response; amplitude and position always come from
the source samples. D2 widths are curvature-feature widths, not fitted linewidths.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping

import numpy as np
from scipy.signal import find_peaks, savgol_filter


@dataclass(frozen=True)
class PeakCandidate:
    index: int
    position: float
    amplitude: float
    kind: str
    prominence: float
    prominence_fraction: float
    width: float
    noise_strength: float
    detector: str

    def __post_init__(self) -> None:
        if isinstance(self.index, (bool, np.bool_)) or not isinstance(self.index, (int, np.integer)) or self.index < 0:
            raise ValueError('Candidate index must be a nonnegative integer.')
        if self.kind not in ('peak', 'dip') or self.detector not in ('Original', 'D2'):
            raise ValueError('Invalid candidate kind or detector.')
        for name in ('position', 'amplitude', 'prominence', 'prominence_fraction', 'width', 'noise_strength'):
            value = getattr(self, name)
            if not np.isfinite(value) or (name not in ('position', 'amplitude') and value < 0):
                raise ValueError(f'Invalid candidate {name}.')

    def to_dict(self) -> dict:
        return {key: value.item() if isinstance(value, np.generic) else value
                for key, value in asdict(self).items()}

    @classmethod
    def from_dict(cls, record: Mapping) -> PeakCandidate:
        try:
            return cls(**dict(record))
        except (TypeError, OverflowError) as error:
            raise ValueError('Invalid candidate record.') from error


@dataclass(frozen=True)
class CandidateFilter:
    preset: str = 'Balanced'
    min_prominence_fraction: float = .03
    min_noise_strength: float = 2.
    min_width: float = 0.
    max_width: float = 0.  # zero means no maximum
    min_distance: float = 0.  # source X units, applied separately per kind
    max_count: int = 0  # zero means unlimited, applied after every other filter

    def __post_init__(self) -> None:
        if self.preset not in ('Loose', 'Balanced', 'Strict', 'Custom'):
            raise ValueError('Unknown candidate filter preset.')
        for name in ('min_prominence_fraction', 'min_noise_strength', 'min_width', 'max_width', 'min_distance'):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0:
                raise ValueError(f'Invalid candidate filter {name}.')
        if self.max_width > 0 and self.max_width < self.min_width:
            raise ValueError('Maximum candidate width must not be below minimum width.')
        if isinstance(self.max_count, (bool, np.bool_)) or not isinstance(self.max_count, (int, np.integer)) or self.max_count < 0:
            raise ValueError('Candidate maximum count must be a nonnegative integer.')

    def to_dict(self) -> dict:
        return {key: value.item() if isinstance(value, np.generic) else value
                for key, value in asdict(self).items()}

    @classmethod
    def from_dict(cls, record: Mapping) -> CandidateFilter:
        try:
            return cls(**dict(record))
        except (TypeError, OverflowError) as error:
            raise ValueError('Invalid candidate filter record.') from error


def candidate_filter_preset(name: str) -> CandidateFilter:
    """Return editable defaults; preset names do not override saved thresholds."""
    if name == 'Loose':
        return CandidateFilter(preset=name, min_prominence_fraction=.005, min_noise_strength=0.)
    if name == 'Strict':
        return CandidateFilter(preset=name, min_prominence_fraction=.08, min_noise_strength=4.)
    return CandidateFilter(preset=name)


def filter_peak_candidates(pool: Iterable[PeakCandidate], settings: CandidateFilter) -> list[PeakCandidate]:
    """Select candidates without consuming/mutating the reusable pool.

    More prominent candidates win same-kind spacing conflicts. An optional total
    count limit is applied last. Returned candidates retain source index order.
    """
    eligible = [candidate for candidate in pool
                if candidate.prominence_fraction >= settings.min_prominence_fraction
                and candidate.noise_strength >= settings.min_noise_strength
                and candidate.width >= settings.min_width
                and (settings.max_width == 0 or candidate.width <= settings.max_width)]
    kept: list[PeakCandidate] = []
    for candidate in sorted(eligible, key=lambda c: (-c.prominence, c.index, c.kind)):
        if any(other.kind == candidate.kind and abs(other.position-candidate.position) < settings.min_distance
               for other in kept):
            continue
        kept.append(candidate)
    if settings.max_count:
        kept = kept[:settings.max_count]
    return sorted(kept, key=lambda c: (c.index, c.kind))


def _noise_level(values: np.ndarray) -> float:
    differences = np.diff(values)
    if differences.size == 0:
        return 0.
    return float(np.median(np.abs(differences-np.median(differences))) / (.67448975*np.sqrt(2.)))


def collect_peak_candidates(
    x: np.ndarray,
    y: np.ndarray,
    *,
    mode: str = 'Both',
    detector: str = 'Original',
    sg_window: int = 11,
    sg_polyorder: int = 3,
) -> list[PeakCandidate]:
    """Collect every local extremum on each contiguous finite source run.

    D2 uses Savitzky-Golay smoothing on a uniform physical-X auxiliary grid,
    differentiates in physical X units, then interpolates the response back onto
    source X. Thus irregular sampling is never mislabeled as a physical derivative
    of sample index. Source arrays, source amplitudes and indices are unchanged.
    Prominence fractions share one response range across all finite runs.
    """
    xv, yv = np.asarray(x, dtype=float).ravel(), np.asarray(y, dtype=float).ravel()
    if xv.size != yv.size:
        raise ValueError('Candidate X and Y lengths must match.')
    if mode not in ('Peaks', 'Dips', 'Both') or detector not in ('Original', 'D2'):
        raise ValueError('Unknown candidate mode or detector.')
    finite_x = xv[np.isfinite(xv)]
    difference = np.diff(finite_x)
    if difference.size and not (np.all(difference > 0) or np.all(difference < 0)):
        raise ValueError('Candidate X must be strictly monotonic.')
    finite = np.isfinite(xv) & np.isfinite(yv)
    boundaries = np.flatnonzero(np.diff(np.r_[False, finite, False]))
    runs: list[tuple[int, np.ndarray, np.ndarray]] = []
    for start, end in zip(boundaries[::2], boundaries[1::2]):
        source_x, source_y = xv[start:end], yv[start:end]
        if source_x.size < (5 if detector == 'D2' else 3):
            continue
        if detector == 'Original':
            response = source_y
        elif np.ptp(source_y) == 0:
            response = np.zeros(source_y.size)
        else:
            reverse = bool(source_x[0] > source_x[-1])
            ordered_x = source_x[::-1] if reverse else source_x
            ordered_y = source_y[::-1] if reverse else source_y
            uniform_x = np.linspace(ordered_x[0], ordered_x[-1], source_x.size)
            uniform_y = np.interp(uniform_x, ordered_x, ordered_y)
            window = max(3, int(sg_window))
            window += 1-window % 2
            window = min(window, source_x.size if source_x.size % 2 else source_x.size-1)
            order = min(max(2, int(sg_polyorder)), window-1)
            derivative = savgol_filter(uniform_y, window, order, deriv=2,
                                       delta=float(uniform_x[1]-uniform_x[0]))
            response = np.interp(ordered_x, uniform_x, derivative)
            if reverse:
                response = response[::-1]
        runs.append((int(start), source_x, response))
    if not runs:
        return []
    all_response = np.concatenate([response for _, _, response in runs])
    spread = float(np.ptp(all_response))
    if spread == 0:
        return []
    # Estimate noise within each run only; missing samples must not create jumps.
    noise_levels = [_noise_level(response) for _, _, response in runs]
    noise = max(float(np.median(noise_levels)), spread*np.finfo(float).eps)
    candidates = []
    kinds = ('peak', 'dip') if mode == 'Both' else (('peak',) if mode == 'Peaks' else ('dip',))
    for start, source_x, response in runs:
        for kind in kinds:
            sign = 1 if kind == 'peak' else -1
            if detector == 'D2':
                sign *= -1
            indices, properties = find_peaks(sign*response, prominence=(None, None), width=(None, None))
            positions = np.arange(source_x.size)
            for offset, index in enumerate(indices):
                prominence = float(properties['prominences'][offset])
                left = float(np.interp(properties['left_ips'][offset], positions, source_x))
                right = float(np.interp(properties['right_ips'][offset], positions, source_x))
                source_index = start+int(index)
                candidates.append(PeakCandidate(index=source_index, position=float(xv[source_index]),
                    amplitude=float(yv[source_index]), kind=kind, prominence=prominence,
                    prominence_fraction=prominence/spread, width=abs(right-left),
                    noise_strength=prominence/noise, detector=detector))
    return sorted(candidates, key=lambda c: (c.index, c.kind))
