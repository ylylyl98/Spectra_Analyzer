"""Ephemeral comparison display arrays. Source views are never modified."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .workbook_state import Curve, resolve_curve, validate_comparison_display


@dataclass
class DisplayCurve:
    curve: Curve
    x: np.ndarray
    y: np.ndarray


@dataclass
class ComparisonDisplay:
    curves: list[DisplayCurve]
    x_limits: tuple[float, float] | None
    notices: list[str]
    description: str
    y_label: str


def transform_y(values, reference, normalization, stack_offset=0., curve_offset=0.):
    """Transform source or model Y using the same finite source reference."""
    values = np.asarray(values, dtype=float).copy()
    reference = np.asarray(reference, dtype=float)
    if normalization == 'maxabs':
        scale = np.max(np.abs(reference))
        values = values / scale if scale else np.zeros_like(values)
    elif normalization == 'minmax':
        low, high = reference.min(), reference.max()
        if low == high:
            values = np.zeros_like(values)
        elif low < 0 < high:
            scale = max(abs(low), abs(high))
            values = (values / scale - low / scale) / (high / scale - low / scale)
        else:
            values = (values - low) / (high - low)
    with np.errstate(over='ignore', invalid='ignore'):
        return values + stack_offset + curve_offset


def prepare_comparison(workspace, comparison):
    """Resolve live samples, select a scientific domain, then scale and offset.

    Row order, duplicate coordinates and gaps remain intact. The list ordinal
    includes hidden curves, so visibility never changes another curve's offset.
    """
    validate_comparison_display(comparison)
    usable, notices = [], []
    for ordinal, curve in enumerate(comparison.curves.values()):
        if not curve.visible:
            continue
        x, y = resolve_curve(workspace, curve)
        valid = np.isfinite(x) & np.isfinite(y)
        name = curve.label or workspace.sheets[curve.sheet_id].name
        if not valid.any():
            notices.append(f'{name}: no finite X/Y pairs; omitted.')
            continue
        usable.append((ordinal, curve, x, y, valid, name))

    mode = comparison.x_range_mode
    limits = None
    if mode == 'manual':
        limits = tuple(comparison.x_range)
    elif mode == 'common':
        if usable:
            limits = (max(float(x[valid].min()) for _, _, x, _, valid, _ in usable),
                      min(float(x[valid].max()) for _, _, x, _, valid, _ in usable))
        if limits is None or limits[0] >= limits[1]:
            notices.append('No common X range with positive width.')
            usable, limits = [], None

    displayed = []
    for ordinal, curve, x, y, valid, name in usable:
        if limits is not None:
            valid = valid & (x >= limits[0]) & (x <= limits[1])
        if not valid.any():
            notices.append(f'{name}: no samples in the selected X range; omitted.')
            continue
        # Cast before arithmetic, including abs(), to handle signed integer minima.
        values = np.asarray(y[valid], dtype=float)
        if comparison.normalization == 'minmax' and values.min() == values.max():
            notices.append(f'{name}: constant curve in this range; normalized to zero.')
        values = transform_y(values, values, comparison.normalization,
                             ordinal * comparison.stack_step, curve.y_offset)
        finite = np.isfinite(values)
        if not finite.all():
            notices.append(f'{name}: offset overflow; nonfinite display values omitted.')
        if not finite.any():
            continue
        shown_x = np.full(x.shape, np.nan, dtype=float)
        shown_y = np.full(y.shape, np.nan, dtype=float)
        shown_x[valid] = np.where(finite, x[valid], np.nan)
        shown_y[valid] = np.where(finite, values, np.nan)
        displayed.append(DisplayCurve(curve, shown_x, shown_y))

    normalization = {'none': 'Original Y', 'maxabs': 'Y / max |Y|', 'minmax': 'Min–max Y (0–1)'}[comparison.normalization]
    range_text = {'all': 'All X', 'common': 'Common overlap of visible curves',
                  'manual': 'Manual X'}[mode]
    if limits is not None:
        range_text += f': {limits[0]:g} to {limits[1]:g}'
    elif mode == 'all' and displayed:
        low = min(float(item.x[np.isfinite(item.x)].min()) for item in displayed)
        high = max(float(item.x[np.isfinite(item.x)].max()) for item in displayed)
        range_text += f': {low:g} to {high:g}'
    if not displayed and not notices:
        notices.append('No visible curves with usable samples.')
    description = f'Data X interval: {range_text} · {normalization}'
    annotations = []
    if comparison.normalization != 'none':
        annotations.append(normalization)
    if comparison.stack_step or any(curve.y_offset for curve in comparison.curves.values()):
        annotations.append('display offsets')
        description += f' · Stack step {comparison.stack_step:g}'
    y_label = comparison.y_label
    if annotations:
        y_label = f'{y_label}\n({"; ".join(annotations)})'.lstrip()
    return ComparisonDisplay(displayed, limits, notices, description, y_label)
