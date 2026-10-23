"""Bounded display-only samples; analysis and export keep the original arrays."""
from __future__ import annotations

import numpy as np


def sample_preview_curve(x, y, max_points=8000):
    """Keep row order, bucket extrema and gaps within a fixed artist budget.

    Buckets divide usable samples, so masked rows never dilute their budget.
    Six finite samples per bucket retain endpoints and X/Y extrema. A NaN
    separates retained samples whenever the original rows between them contain
    a gap, including buckets with more gaps than the display budget can retain.
    No interpolated values or false connections are introduced.
    """
    if len(x) <= max_points:
        return x, y
    if max_points < 12:
        raise ValueError('Preview budget must be at least twelve points.')
    finite = np.isfinite(x) & np.isfinite(y)
    usable = np.flatnonzero(finite)
    gaps = np.diff(usable) > 1
    gap_count = np.cumsum(np.r_[False, gaps])
    if usable.size + np.count_nonzero(gaps) <= max_points:
        selected = range(usable.size)
    else:
        buckets = max(1, (max_points - 2) // 12)
        edges = np.linspace(0, usable.size, buckets + 1, dtype=int)
        selected = []
        for start, stop in zip(edges[:-1], edges[1:]):
            indices = usable[start:stop]
            values = y[indices]
            coordinates = x[indices]
            selected.extend(sorted({start, stop - 1,
                                    start + int(np.argmin(values)), start + int(np.argmax(values)),
                                    start + int(np.argmin(coordinates)), start + int(np.argmax(coordinates))}))
    shown_x, shown_y = [], []
    previous = None
    for position in selected:
        if previous is not None and gap_count[position] != gap_count[previous]:
            shown_x.append(np.nan); shown_y.append(np.nan)
        index = usable[position]
        shown_x.append(x[index]); shown_y.append(y[index])
        previous = position
    return np.asarray(shown_x), np.asarray(shown_y)
