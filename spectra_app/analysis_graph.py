"""Detached detection and fit-curve records shared by XY and current-Gate graph creation."""
from __future__ import annotations

import numpy as np
from .workbook_state import Sheet, create_comparison, add_curve


def fit_curve_sheet(x, y, valid, fit, *, x_label, y_label, note):
    """Keep source rows and gaps, with every component on the baseline scale."""
    x, y, valid = np.asarray(x), np.asarray(y), np.asarray(valid, dtype=bool)
    count = int(valid.sum())
    if (x.ndim != 1 or y.shape != x.shape or valid.shape != x.shape
            or not np.array_equal(x[valid], fit.x)
            or np.shape(fit.y_fit) != (count,)
            or np.shape(fit.components) != (len(fit.centers), count)):
        raise ValueError('The completed fit does not align with its source coordinates.')
    values = np.full((x.size, 3 + len(fit.centers)), np.nan)
    values[:, 0], values[:, 1] = x, y
    values[valid, 2] = fit.y_fit
    values[valid, 3:] = (fit.components + fit.baseline).T
    return Sheet(name='Fit curves', kind='snapshot', role='snapshot', snapshot_note=note,
        data=values, column_names=[x_label, y_label, 'Total fit'] +
        [f'Component {i+1} + baseline' for i in range(len(fit.centers))],
        y_columns=list(range(1, values.shape[1])))


def marker_curve_sheet(x, y, indices, kinds, *, x_label, y_label, note):
    """Sparse marker columns retain exact source coordinates and amplitudes."""
    x, y = np.asarray(x), np.asarray(y)
    indices, kinds = np.asarray(indices, dtype=int), np.asarray(kinds, dtype=bool)
    if (x.ndim != 1 or y.shape != x.shape or indices.shape != kinds.shape or not indices.size
            or np.any(indices < 0) or np.any(indices >= x.size)
            or not np.isfinite(x[indices]).all() or not np.isfinite(y[indices]).all()):
        raise ValueError('Find at least one valid peak or dip before creating an analysis graph.')
    # Fixed physical columns keep graph semantics independent of editable labels/roles.
    values = np.full((x.size, 4), np.nan)
    values[:, 0], values[:, 1] = x, y
    for column, kind in ((2, True), (3, False)):
        selected = indices[kinds == kind]
        values[selected, column] = y[selected]
    return Sheet(name='Peak curves', kind='snapshot', role='snapshot', snapshot_note=note,
                 data=values, column_names=[x_label, y_label, 'Peaks', 'Dips'], y_columns=[1, 2, 3])


def seed_curve_sheet(x, y, seeds, *, x_label, y_label, note):
    """Keep each continuous seed as its own marker, including coincident kinds.

    Source samples remain intact. Marker amplitudes follow the auxiliary DAT
    linecut's nearest-sample convention; their X coordinates are never rounded.
    """
    x, y, seeds = np.asarray(x), np.asarray(y), list(seeds)
    if x.ndim != 1 or y.shape != x.shape or not x.size or not seeds:
        raise ValueError('Accept at least one seed before creating a linecut graph.')
    values = np.full((x.size + len(seeds), 4), np.nan)
    values[:x.size, 0], values[:x.size, 1] = x, y
    for row, seed in enumerate(seeds, x.size):
        values[row, 0] = seed.position
        values[row, 2 if seed.kind == 'peak' else 3] = y[np.argmin(np.abs(x-seed.position))]
    return Sheet(name='Seed curves', kind='snapshot', role='snapshot', snapshot_note=note,
        data=values, column_names=[x_label, y_label, 'Peaks', 'Dips'], y_columns=[1, 2, 3])


def saved_result_records(workspace, identities):
    """Runtime reuse is valid only while every saved result record is retained."""
    if identities is None:
        return None
    book_id, sheet_ids = identities
    book = workspace.workbooks.get(book_id)
    if book is None or not all(sid in book.sheet_ids and sid in workspace.sheets for sid in sheet_ids):
        return None
    return book, [workspace.sheets[sid] for sid in sheet_ids]


def add_analysis_comparison(workspace, book, sheets, *, has_fit=True, source_label='Source data'):
    """Build an ordinary comparison in the caller's detached staging workspace."""
    # Result builders put the plotted source/result table last; runtime IDs preserve that order
    # even when the user renames/reorders sheets or changes their default roles.
    sheet = sheets[-1]
    prefix = 'Fit' if has_fit else 'Peaks and dips'
    comparison = create_comparison(workspace, f'{prefix} — {book.name.removeprefix("Analysis — ")}')
    comparison.x_label, comparison.y_label = sheet.column_names[:2]
    comparison.plot_title = 'Fit result' if has_fit else 'Peaks and dips'
    for column in range(1, sheet.data.shape[1]):
        if not has_fit:
            if column > 1 and not np.isfinite(sheet.data[:, column]).any():
                continue
            options = (dict(label=source_label, color='#1f77b4') if column == 1 else
                       dict(label='Peaks' if column == 2 else 'Dips', style='scatter',
                            color='#d62728' if column == 2 else '#2ca02c'))
        else:
            options = dict(label=source_label, color='#1f77b4', style='scatter') if column == 1 else (
                dict(label='Total fit', color='#d62728', width=2.2) if column == 2 else
                dict(label=f'Component {column-2} + baseline',
                     color=('#2ca02c', '#9467bd', '#ff7f0e', '#8c564b', '#e377c2', '#7f7f7f')[(column-3) % 6]))
        add_curve(workspace, comparison.comparison_id, sheet.sheet_id, 0, column, **options)
    return comparison
