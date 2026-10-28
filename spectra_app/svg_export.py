"""Detached heatmap page: one raster layer, semantic text and vector geometry.

The final SVG bytes are the sole input to preview and both writers. Rendering
is serial: Matplotlib is not a concurrent rendering service.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Mapping
import xml.etree.ElementTree as ET

import numpy as np
from matplotlib import colormaps, rc_context
from matplotlib.backends.backend_agg import FigureCanvasAgg, RendererAgg
from matplotlib.collections import QuadMesh
from matplotlib.colors import LogNorm, Normalize, SymLogNorm, TwoSlopeNorm, to_hex
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties, findfont
from matplotlib.lines import TICKDOWN, TICKLEFT, TICKRIGHT, TICKUP
from matplotlib.patches import Polygon
from matplotlib.ticker import AutoMinorLocator, LinearLocator, MaxNLocator, MultipleLocator, NullLocator, ScalarFormatter
from matplotlib.transforms import Bbox
from PIL import Image, PngImagePlugin

from .custom_colormaps import resolve_colormap
from .signal_titles import display_signal_label
from .plot_presentation import (ColorRegion, ColorbarPresentation, ColorbarTicks,
                                HeatmapSnapshot, NormSpec, PlotPresentation)


SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_NS = 'http://www.w3.org/1999/xlink'
ET.register_namespace('', SVG_NS)
ET.register_namespace('xlink', XLINK_NS)
_NS = {'s': SVG_NS}
MISSING_COLOR = '#D9D9D9'
_QT_FONT_FILES: set[str] = set()


@dataclass(frozen=True)
class RenderedPage:
    svg_bytes: bytes
    digest: str
    owner_token: tuple[str, int, int, int, int] | tuple[str, int, int, int, int, str]
    figure_size_in: tuple[float, float]
    axes_bounds: tuple[float, float, float, float]
    plot_size_in: tuple[float, float]
    colorbar_bounds: dict[str, tuple[float, float, float, float]]
    colorbar_tick_positions: dict[str, tuple[float, float, float]]
    display_x_limits: tuple[float, float]
    display_y_limits: tuple[float, float]
    nonpositive_count: int
    under_count: int
    over_count: int
    missing_count: int
    valid_count: int
    header_line_count: int
    heatmap_dpi: int
    font_family: str
    # Visible semantic objects in normalized page coordinates (left, bottom,
    # width, height). Runtime-only; old comparison fixtures remain compatible.
    object_bounds: dict[str, tuple[tuple[float, float, float, float], ...]] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def build_color_norm(spec: NormSpec, limits: tuple[float, float]):
    """Shared Analysis/Plot boundary; return a fresh Matplotlib color norm."""
    if not isinstance(spec, NormSpec):
        raise ValueError('Color mapping requires a NormSpec.')
    try:
        if len(limits) != 2 or any(isinstance(v, (bool, np.bool_)) for v in limits):
            raise ValueError
        low, high = map(float, limits)
    except (TypeError, ValueError) as exc:
        raise ValueError('Color limits require two finite numbers.') from exc
    if not np.isfinite([low, high]).all() or low >= high:
        raise ValueError('Color limits require finite lower < upper.')
    if spec.kind == 'log':
        if low <= 0:
            raise ValueError('Log color limits must be positive: 0 < lower < upper.')
        return LogNorm(vmin=low, vmax=high, clip=False)
    if spec.kind == 'symlog':
        return SymLogNorm(spec.linthresh, linscale=1., base=10., vmin=low, vmax=high, clip=False)
    if spec.kind == 'zero-centered':
        if not low < 0 < high:
            raise ValueError('Zero-centered color limits must cross zero: lower < 0 < upper.')
        return TwoSlopeNorm(0., vmin=low, vmax=high)
    return Normalize(vmin=low, vmax=high, clip=False)


def prepare_display_values(values, spec: NormSpec) -> tuple[np.ma.MaskedArray, int]:
    """Copy a display mask; never modify a source array or its existing mask."""
    display = np.ma.masked_invalid(np.ma.array(values, dtype=float, copy=True))
    count = 0
    if spec.kind == 'log':
        nonpositive = (~np.ma.getmaskarray(display)) & (display.data <= 0)
        count = int(np.count_nonzero(nonpositive))
        display = np.ma.masked_where(nonpositive, display, copy=False)
    return display, count


def valid_color_values(values, spec: NormSpec) -> np.ndarray:
    """Finite display values used by the existing percentile auto-limit policy."""
    return prepare_display_values(values, spec)[0].compressed()


def _edges(values: np.ndarray, cell_count: int) -> np.ndarray:
    if values.size == cell_count + 1:
        return values
    if cell_count == 1:
        return np.array([values[0] - .5, values[0] + .5])
    midpoint = (values[:-1] + values[1:]) / 2
    return np.r_[values[0] - (midpoint[0] - values[0]), midpoint,
                 values[-1] + (values[-1] - midpoint[-1])]


def _coordinates(snapshot: HeatmapSnapshot):
    if snapshot.x.ndim == 1:
        return np.meshgrid(_edges(snapshot.x, snapshot.z.shape[1]), _edges(snapshot.y, snapshot.z.shape[0]))
    return snapshot.x, snapshot.y


_SI_PREFIXES = {-24: 'y', -21: 'z', -18: 'a', -15: 'f', -12: 'p', -9: 'n', -6: 'µ',
                -3: 'm', 0: '', 3: 'k', 6: 'M', 9: 'G', 12: 'T', 15: 'P',
                18: 'E', 21: 'Z', 24: 'Y'}
_SUPERSCRIPT = str.maketrans('-0123456789', '⁻⁰¹²³⁴⁵⁶⁷⁸⁹')


def _trim_number(text, trim):
    return text.rstrip('0').rstrip('.') if trim and '.' in text else text.rstrip('.')


def _colorbar_number(value: float, style: ColorbarPresentation) -> tuple[str, float]:
    """Return editable plain text and its rounded value; precision is display only."""
    mode, trim = style.label_format, style.trim_trailing_zeros
    if mode == 'decimal':
        text = f'{value:.{style.decimal_places}f}'
        rounded = float(text)
        if rounded == 0:
            text = text.lstrip('-')
        return _trim_number(text, trim), rounded
    if value == 0:
        return '0', 0.
    if mode == 'scientific':
        mantissa, exponent = f'{value:.{style.decimal_places}e}'.split('e')
        exponent = int(exponent)
        rounded = float(f'{mantissa}e{exponent}')
        mantissa = _trim_number(mantissa, trim)
        suffix = f'e{exponent}' if style.scientific_style == 'e' else '×10' + str(exponent).translate(_SUPERSCRIPT)
        return mantissa + suffix, rounded
    digits = style.significant_digits
    if mode == 'engineering':
        scientific = f'{value:.{digits - 1}e}'
        mantissa, exponent = scientific.split('e')
        exponent = int(exponent)
        engineering_exponent = exponent // 3 * 3
        remainder = exponent - engineering_exponent
        # Only scale the rounded mantissa by 1, 10 or 100, avoiding overflow
        # and retaining ordinary mantissas even at one significant digit.
        mantissa = f'{float(mantissa) * 10. ** remainder:.{max(0, digits - 1 - remainder)}f}'
        suffix = _SI_PREFIXES.get(engineering_exponent, f'e{engineering_exponent}')
        return _trim_number(mantissa, trim) + suffix, float(scientific)
    text = format(value, f'#.{digits}g')
    rounded = float(text)
    if 'e' in text:
        mantissa, exponent = text.split('e')
        exponent = int(exponent)
        if mode == 'auto' and 1e-3 <= abs(value) < 1e6:
            text = f'{rounded:.{max(0, digits - 1 - exponent)}f}'
        else:
            return _trim_number(mantissa, trim) + f'e{exponent}', rounded
    return _trim_number(text, trim), rounded


def format_colorbar_value(value: float, style: ColorbarPresentation) -> str:
    return _colorbar_number(value, style)[0]


def colorbar_labels_rounded(values, style: ColorbarPresentation) -> bool:
    return any(_colorbar_number(value, style)[1] != value for value in values)


def _bar_ticks(norm, custom: ColorbarTicks | None, style: ColorbarPresentation | None = None):
    values = custom.values if custom else (float(norm.vmin), float(norm.inverse(.5)), float(norm.vmax))
    if custom is None and norm.vmin < 0 < norm.vmax:
        tolerance = 4 * np.finfo(float).eps * max(abs(norm.vmin), abs(norm.vmax))
        if abs(values[1]) <= tolerance:
            values = (values[0], 0., values[2])
    if any(v < norm.vmin or v > norm.vmax for v in values) or isinstance(norm, LogNorm) and min(values) <= 0:
        raise ValueError('Colorbar tick values must be inside the valid color limits.')
    style = style or ColorbarPresentation()
    labels = custom.labels if custom and custom.labels is not None else tuple(format_colorbar_value(v, style) for v in values)
    return values, labels, tuple(float(v) for v in norm(values))


def apply_heatmap_colorbar_style(colorbar, presentation, bar_id, *, allow_invalid_custom=False, display_scale=1.):
    """Style the same three norm ticks in live and detached heatmaps.

    A live bar may fall back after its limits invalidate stored custom ticks;
    the detached renderer still rejects those ticks before preparing a page.
    The fallback never changes the stored presentation.
    """
    custom = presentation.colorbar.ticks.get(bar_id)
    try:
        ticks = _bar_ticks(colorbar.mappable.norm, custom, presentation.colorbar)
    except ValueError:
        if not allow_invalid_custom:
            raise
        ticks = _bar_ticks(colorbar.mappable.norm, None, presentation.colorbar)
    values, labels, _positions = ticks
    colorbar.set_ticks(values, labels=labels)
    axis = colorbar.ax
    axis.xaxis.set_ticks_position('top')
    axis.xaxis.set_minor_locator(NullLocator())
    axis.tick_params(top=True, bottom=False, labeltop=True, labelbottom=False,
                     labelsize=presentation.numeric_pt, pad=2*display_scale, length=2*display_scale,
                     width=.8*display_scale)
    axis.xaxis.get_offset_text().set_visible(False)
    for text in axis.get_xticklabels():
        text.set_fontfamily([presentation.font_family, 'DejaVu Sans'])
        text.set_fontsize(presentation.numeric_pt)
        text.set_horizontalalignment('center')
    for text in (axis.xaxis.label, axis.yaxis.label, axis.title):
        text.set_fontfamily([presentation.font_family, 'DejaVu Sans'])
        text.set_fontsize(presentation.text_pt)
    return ticks


def validate_heatmap_axis_ticks(ax, presentation):
    """Check retained coordinate scales without rebuilding live tick artists."""
    style = presentation.axis
    for scale, step, count, minor in (
            (ax.get_xscale(), style.x_major_step, style.x_major_count, style.x_minor_count),
            (ax.get_yscale(), style.y_major_step, style.y_major_count, style.y_minor_count)):
        if scale != 'linear' and minor not in (0, 1):
            raise ValueError('Manual minor tick counts require linear coordinate axes; use 0 to disable or 1 for automatic non-linear minors.')
        if scale != 'linear' and (step is not None or count is not None):
            raise ValueError('Manual major tick increments and counts require linear coordinate axes.')


def apply_heatmap_axis_style(ax, presentation, *, display_scale=1.):
    """Shared presentation only: never change coordinates, scales or limits."""
    validate_heatmap_axis_ticks(ax, presentation)
    style = presentation.axis
    for coordinate, step, count, minor in ((ax.xaxis, style.x_major_step, style.x_major_count, style.x_minor_count),
                                          (ax.yaxis, style.y_major_step, style.y_major_count, style.y_minor_count)):
        scale = ax.get_xscale() if coordinate is ax.xaxis else ax.get_yscale()
        if step is not None or count is not None:
            coordinate.set_major_locator(MultipleLocator(step) if step is not None else LinearLocator(count))
        elif scale == 'linear':
            coordinate.set_major_locator(MaxNLocator(nbins=6, min_n_ticks=3))
        if scale == 'linear':
            formatter = ScalarFormatter(useMathText=False)
            formatter.set_powerlimits((-3, 3))
            coordinate.set_major_formatter(formatter)
            coordinate.set_minor_locator(AutoMinorLocator(minor+1) if style.minor_ticks and minor else NullLocator())
        elif not style.minor_ticks or not minor:
            coordinate.set_minor_locator(NullLocator())
        elif isinstance(coordinate.get_minor_locator(), NullLocator):
            # Restore this existing scale's defaults, including its Log base
            # and subs, without replacing an unrelated major locator/formatter.
            major_locator, major_formatter = coordinate.get_major_locator(), coordinate.get_major_formatter()
            coordinate._scale.set_default_locators_and_formatters(coordinate)
            coordinate.set_major_locator(major_locator)
            coordinate.set_major_formatter(major_formatter)
        coordinate.get_offset_text().set_fontsize(presentation.numeric_pt)
        coordinate.get_offset_text().set_fontfamily([presentation.font_family, 'DejaVu Sans'])
    ax.xaxis.set_visible(style.show_x)
    ax.yaxis.set_visible(style.show_y)
    ax.xaxis.label.set_visible(style.show_x_label)
    ax.yaxis.label.set_visible(style.show_y_label)
    ax.tick_params(which='both', direction='in' if style.ticks_inside else 'out',
                   labelsize=presentation.numeric_pt, labelbottom=style.show_tick_labels,
                   labelleft=style.show_tick_labels, top=False, right=False)
    ax.tick_params(which='major', length=4*display_scale if style.major_ticks else 0,
                   width=.8*display_scale, pad=1*display_scale)
    ax.tick_params(which='minor', length=2*display_scale if style.minor_ticks else 0,
                   width=.6*display_scale)
    ax.xaxis.labelpad = ax.yaxis.labelpad = 1*display_scale
    for text in (*ax.get_xticklabels(), *ax.get_yticklabels()):
        text.set_fontfamily([presentation.font_family, 'DejaVu Sans'])
        text.set_fontsize(presentation.numeric_pt)
    for text in (ax.title, ax.xaxis.label, ax.yaxis.label):
        text.set_fontfamily([presentation.font_family, 'DejaVu Sans'])
        text.set_fontsize(presentation.text_pt)
    for spine in ax.spines.values():
        spine.set_visible(style.border)
        spine.set_linewidth(style.border_width)
    ax.grid(False, which='both')


def _contrast_array_signature(values):
    if values is None:
        return None
    array = np.asarray(values)
    if not array.size:
        return (array.shape, array.dtype.str, b'')
    # C and Fortran mesh buffers can both be hashed in memory order without a copy.
    # Content rather than object identity also catches supported in-place edits.
    buffer = (array.ravel(order='K') if array.flags.c_contiguous or array.flags.f_contiguous
              else np.ascontiguousarray(array))
    return (array.shape, array.dtype.str, array.strides,
            hashlib.blake2b(memoryview(buffer).cast('B'), digest_size=16).digest())


def _contrast_scalar_state(value):
    """Small norm/scale parameters, excluding callbacks and parent references."""
    return (type(value), tuple(sorted((key, item) for key, item in vars(value).items()
                                    if item is None or isinstance(item, (str, bool, int, float, np.number)))))


def _heatmap_contrast_signature(ax, renderer, meshes, strokes):
    state = []
    for mesh in meshes:
        values = mesh.get_array()
        cmap = mesh.get_cmap()
        if not cmap._isinit:
            cmap._init()
        clip_path, clip_transform = mesh.get_transformed_clip_path_and_affine()
        clip_box = mesh.get_clip_box()
        state.append((id(mesh), _contrast_array_signature(values),
                      _contrast_array_signature(np.ma.getmask(values)),
                      _contrast_array_signature(mesh.get_coordinates()),
                      _contrast_scalar_state(mesh.norm),
                      _contrast_scalar_state(mesh.norm._trf) if hasattr(mesh.norm, '_trf') else None,
                      _contrast_array_signature(cmap._lut),
                      _contrast_array_signature(mesh.get_alpha()), mesh.get_zorder(),
                      mesh.get_clip_on(), tuple(clip_box.bounds) if clip_box is not None else None,
                      _contrast_array_signature(clip_path.vertices) if clip_path is not None else None,
                      _contrast_array_signature(clip_path.codes) if clip_path is not None else None,
                      tuple(clip_transform.get_matrix().flat) if clip_transform is not None else None,
                      tuple(mesh.get_transform().get_affine().get_matrix().flat),
                      _contrast_array_signature(mesh.get_offsets()),
                      tuple(mesh.get_offset_transform().get_affine().get_matrix().flat),
                      _contrast_array_signature(mesh.get_facecolors()) if values is None else None,
                      _contrast_array_signature(mesh.get_edgecolors()),
                      _contrast_array_signature(mesh.get_linewidths()),
                      _contrast_array_signature(mesh.get_antialiased()), mesh._shading))
    return (tuple(ax.figure.bbox.bounds), ax.figure.dpi, renderer.points_to_pixels(1),
            tuple(ax.get_window_extent(renderer).bounds), ax.get_xlim(), ax.get_ylim(),
            _contrast_scalar_state(ax.xaxis._scale.get_transform()),
            _contrast_scalar_state(ax.yaxis._scale.get_transform()),
            ax.patch.get_visible(), ax.patch.get_facecolor(), ax.patch.get_alpha(), tuple(state),
            tuple((id(line), tuple(anchor), tuple(direction), length, width)
                  for line, anchor, direction, length, width in strokes))


def _update_heatmap_tick_contrast(ax, renderer):
    """Choose inward mark contrast, reusing colors for an unchanged underlay.

    Drawing only the patch and meshes preserves their actual masks, alpha,
    norms, regional order and coordinate transforms without sampling old ticks
    or overlays. Numeric labels, spines and colorbar artists are untouched.
    """
    directions = {TICKUP: (0., 1.), TICKDOWN: (0., -1.),
                  TICKLEFT: (-1., 0.), TICKRIGHT: (1., 0.)}
    marks, strokes = [], []
    for axis, inward in ((ax.xaxis, (TICKUP, TICKDOWN)),
                         (ax.yaxis, (TICKRIGHT, TICKLEFT))):
        visible_ticks = axis._update_ticks() if ax.get_visible() and axis.get_visible() else []
        visible_ids = {id(tick) for tick in visible_ticks}
        # Reset hidden/recycled and outward marks as well as the drawn marks.
        for tick in (*axis.majorTicks, *axis.minorTicks):
            for line, marker in zip((tick.tick1line, tick.tick2line), inward):
                marks.append(line)
                actual_marker = line.get_marker()
                if (id(tick) not in visible_ids or not line.get_visible()
                        or not isinstance(actual_marker, (int, np.integer))
                        or actual_marker != marker or line.get_markersize() <= 0
                        or line.get_markeredgewidth() <= 0):
                    continue
                anchor = line.get_transform().transform(line.get_xydata()[0])
                if np.isfinite(anchor).all():
                    length = renderer.points_to_pixels(line.get_markersize())
                    width = renderer.points_to_pixels(line.get_markeredgewidth())
                    strokes.append((line, anchor, np.array(directions[marker]), length, width))
    meshes = [artist for artist in ax.get_children()
              if isinstance(artist, QuadMesh) and artist.get_visible()]
    signature = _heatmap_contrast_signature(ax, renderer, meshes, strokes) if strokes and meshes else ()
    cached_signature, cached_colors = getattr(ax, '_heatmap_tick_contrast_cache', (None, None))
    colors = cached_colors if signature == cached_signature else {}
    if signature != cached_signature and strokes and meshes:
        bounds = ax.get_window_extent(renderer)
        width, height = ax.figure.bbox.size
        underlay = RendererAgg(width, height, ax.figure.dpi)
        if ax.patch.get_visible():
            ax.patch.draw(underlay)
        for mesh in sorted(meshes, key=lambda artist: artist.get_zorder()):
            mesh.draw(underlay)
        pixels = np.asarray(underlay.buffer_rgba())
        for line, anchor, direction, length, stroke_width in strokes:
            # Avoid the spine junction and cover both length and stroke width.
            along = np.linspace(.25, .85, 5) * length
            across = np.array([-.3, 0., .3]) * stroke_width
            normal = np.array([-direction[1], direction[0]])
            points = (anchor + along[:, None, None] * direction
                      + across[None, :, None] * normal).reshape(-1, 2)
            # Corner marks straddle the frame edge; sample their interior half.
            points[:, 0] = np.clip(points[:, 0], bounds.x0+.5, bounds.x1-.5)
            points[:, 1] = np.clip(points[:, 1], bounds.y0+.5, bounds.y1-.5)
            columns = np.clip(points[:, 0].astype(int), 0, pixels.shape[1]-1)
            rows = np.clip((pixels.shape[0]-points[:, 1]).astype(int), 0, pixels.shape[0]-1)
            rgba = pixels[rows, columns].astype(float) / 255.
            # Composite uncovered/transparent pixels onto the white page.
            rgb = rgba[:, :3]*rgba[:, 3, None] + 1.-rgba[:, 3, None]
            linear = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
            luminance = float(np.median(linear @ np.array([.2126, .7152, .0722])))
            black_contrast = (luminance+.05)/.05
            white_contrast = 1.05/(luminance+.05)
            colors[id(line)] = 'white' if white_contrast > black_contrast else 'black'
    ax._heatmap_tick_contrast_cache = (signature, colors)
    changed = False
    for line in marks:
        color = colors.get(id(line), 'black')
        if to_hex(line.get_color()) != to_hex(color) or to_hex(line.get_markeredgecolor()) != to_hex(color):
            line.set_color(color)
            line.set_markeredgecolor(color)
            changed = True
    return changed


def _align_heatmap_corner_tick_labels(ax, renderer):
    """Keep compact gaps by aligning only the measured colliding corner labels."""
    # Matplotlib reuses tick artists after pan/flip and locator changes. Clear
    # the previous endpoint's alignment before measuring the current view.
    for tick in ax.xaxis.get_major_ticks():
        tick.label1.set_horizontalalignment('center')
        tick.label2.set_horizontalalignment('center')
    for tick in ax.yaxis.get_major_ticks():
        tick.label1.set_verticalalignment('center_baseline')
        tick.label2.set_verticalalignment('center_baseline')
    if not ax.xaxis.get_visible() or not ax.yaxis.get_visible():
        return
    xlabels = [tick.label1 for tick in ax.xaxis._update_ticks()
               if tick.label1.get_visible() and tick.label1.get_text()]
    ylabels = [tick.label1 for tick in ax.yaxis._update_ticks()
               if tick.label1.get_visible() and tick.label1.get_text()]
    if not xlabels or not ylabels:
        return
    endpoint = min(xlabels, key=lambda label: label.get_window_extent(renderer).x0)
    box = endpoint.get_window_extent(renderer)
    colliding = [label for label in ylabels if box.overlaps(label.get_window_extent(renderer))]
    if not colliding:
        return
    endpoint.set_horizontalalignment('left')
    shifted = endpoint.get_window_extent(renderer)
    if any(shifted.overlaps(label.get_window_extent(renderer)) for label in xlabels if label is not endpoint):
        # A dense but valid manual count may have no horizontal room. Keep X
        # centered and lift just the corner Y label above its unchanged anchor.
        endpoint.set_horizontalalignment('center')
        for label in colliding:
            label.set_verticalalignment('bottom')


def _text_width(renderer, text: str, font: FontProperties) -> float:
    return renderer.get_text_width_height_descent(text, font, ismath=False)[0] / 72


def _wrap(text: str, width: float, renderer, font: FontProperties) -> list[str]:
    if not text:
        return []
    lines = []
    for paragraph in text.split('\n'):
        words = paragraph.split(' ')
        current = ''
        for word in words:
            candidate = f'{current} {word}' if current else word
            if _text_width(renderer, candidate, font) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ''
            # Long formula tokens can wrap at actual measured character widths.
            for character in word:
                if current and _text_width(renderer, current + character, font) > width:
                    lines.append(current)
                    current = ''
                current += character
                if _text_width(renderer, current, font) > width:
                    raise ValueError('Page layout is too small for the requested font.')
        lines.append(current)
    return lines


def _configure_axis(ax, snapshot, presentation, x_coordinates, y_coordinates):
    style = presentation.axis
    xlim = snapshot.x_limits or (float(x_coordinates.min()), float(x_coordinates.max()))
    ylim = snapshot.y_limits or (float(y_coordinates.min()), float(y_coordinates.max()))
    xlabel, ylabel = snapshot.x_label, snapshot.y_label
    xscale, yscale = snapshot.x_scale, snapshot.y_scale
    if style.exchange_xy:
        xlim, ylim = ylim, xlim
        xlabel, ylabel = ylabel, xlabel
        xscale, yscale = yscale, xscale
    if style.flip_x:
        xlim = xlim[::-1]
    if style.flip_y:
        ylim = ylim[::-1]
    for scale, limits in ((xscale, xlim), (yscale, ylim)):
        if scale == 'log' and min(limits) <= 0:
            raise ValueError('Log coordinate axes require positive limits.')
    ax.set_xscale(xscale)
    ax.set_yscale(yscale)
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.set_xlabel(xlabel if style.show_x_label and style.show_x else '', fontsize=presentation.text_pt, parse_math=False)
    ax.set_ylabel(ylabel if style.show_y_label and style.show_y else '', fontsize=presentation.text_pt, parse_math=False)
    apply_heatmap_axis_style(ax, presentation)
    return xlim, ylim


class _PageSizeRequired(ValueError):
    """A measured paper shortage, distinct from invalid data or overlapping ticks."""

    def __init__(self, message, width_in, height_in):
        super().__init__(message)
        self.minimum_size_in = (width_in, height_in)


def _layout(figure, ax, snapshot, presentation, bars):
    """Measure fixed-size text, then place an independent frame and its header."""
    width, height = presentation.width_in, presentation.height_in
    if min(width, height) < .8:
        raise ValueError('Page layout is too small for heatmap and labels.')
    figure.draw_without_rendering()
    renderer = figure.canvas.get_renderer()
    _align_heatmap_corner_tick_labels(ax, renderer)
    box, tight = ax.get_window_extent(renderer), ax.get_tightbbox(renderer)
    left = max(.035, (box.x0 - tight.x0) / 72 + .015)
    right = max(.035, (tight.x1 - box.x1) / 72 + .015)
    bottom = max(.035, (box.y0 - tight.y0) / 72 + .015)
    top_axis = max(0., (tight.y1 - box.y1) / 72)
    font = FontProperties(family=[presentation.font_family, 'DejaVu Sans'], size=presentation.text_pt)
    numeric = FontProperties(family=[presentation.font_family, 'DejaVu Sans'], size=presentation.numeric_pt)
    colorbar = presentation.colorbar
    for bar in bars:
        labels = bar['ticks'][1]
        label_widths = [_text_width(renderer, label, numeric) for label in labels]
        bar['tick_half_widths'] = [value / 2 for value in label_widths]
        bar['width'] = colorbar.width_in
    if colorbar.visible and presentation.frame_width_in is None:
        # Explicit Auto uses all remaining room, including header margins.
        right = max(right, max(bar['tick_half_widths'][-1] for bar in bars) + .015,
                    .075 if colorbar.show_extensions else 0)
        left = max(left, max(bar['tick_half_widths'][0] for bar in bars) + .015)
    # Fixed frames use axis margins only. Header overflow is measured against
    # that anchor below, so fitting label edits cannot move or resample data.
    available = width - left - right
    if available <= 0:
        raise ValueError('Page layout has no space for labels and heatmap.')
    signal_text = display_signal_label(snapshot.signal_label, colorbar.show_unit) if colorbar.show_label else ''
    signal = _wrap(signal_text, width - .08, renderer, font)
    text_line = presentation.text_pt / 72 * 1.15
    numeric_line = presentation.numeric_pt / 72 * 1.05
    # A visible missing-color note is part of the output, not only UI status.
    if snapshot.norm.kind == 'log':
        signal += _wrap('Nonpositive / missing: gray', width - .08, renderer, font)
    header_line_count = len(signal)
    def row_width(row):
        return sum(bar['width'] for bar in row) + colorbar.gap_in * (len(row)-1)

    # The configured bars form one fixed strip. Text can require more paper or
    # an overlap warning, but cannot change bar size, spacing or arrangement.
    rows = [bars] if colorbar.visible else []
    bar_height = colorbar.height_in
    # Numeric labels sit above the gradient. Region names get their own row
    # above those labels, leaving the frame-to-gradient gap independent.
    row_height = bar_height + .035 + numeric_line + (.02 + text_line if snapshot.regions else 0) + .015
    header_gap = colorbar.frame_gap_in
    inline_bottom = max(colorbar.frame_gap_in, top_axis + .01)
    inline = bool(rows and len(rows) == 1 and len(signal) == 1 and colorbar.signal_position == 'left')
    def frame_geometry(inline_signal):
        base_header = max(top_axis + .06, header_gap + len(rows) * row_height if rows else 0)
        header = max(base_header, inline_bottom + text_line if inline_signal else 0)
        if signal and not inline_signal:
            header += .04 + len(signal) * text_line
        room_height = height - .025 - bottom - header
        if presentation.frame_width_in is None:
            frame_width = frame_height = min(available, room_height)
            if frame_width < max(.65, 3 * presentation.text_pt / 72):
                raise ValueError('Page layout is too small for the requested text and colorbars; enlarge the page or shorten labels.')
        else:
            frame_width, frame_height = presentation.frame_width_in, presentation.frame_height_in
            if frame_width > available + 1e-9 or frame_height > room_height + 1e-9:
                raise _PageSizeRequired(
                    'Requested heatmap frame does not fit the page and labels; enlarge the page or reduce the frame dimensions.',
                    left + frame_width + right, .025 + bottom + header + frame_height)
        return left + (available-frame_width)/2, bottom, frame_width, frame_height

    def row_left_at(row, frame_left, frame_width):
        occupied = row_width(row)
        if colorbar.alignment == 'right':
            return frame_left + frame_width - occupied
        if colorbar.alignment == 'left':
            return frame_left
        return frame_left + (frame_width-occupied)/2

    plot_left, plot_bottom, frame_width, frame_height = frame_geometry(inline)
    if inline:
        signal_right = row_left_at(rows[0], plot_left, frame_width) - rows[0][0]['tick_half_widths'][0] - .025
        if signal_right - _text_width(renderer, signal[0], font) < .04:
            inline = False
        plot_left, plot_bottom, frame_width, frame_height = frame_geometry(inline)
    base_header = max(top_axis + .06, header_gap + len(rows) * row_height if rows else 0)
    ax.set_position([plot_left/width, plot_bottom/height, frame_width/width, frame_height/height])
    frame_top = plot_bottom + frame_height
    cursor = frame_top + header_gap
    for row in reversed(rows):
        row_left = row_left_at(row, plot_left, frame_width)
        row_right = row_left + row_width(row)
        cap = .035 if colorbar.show_extensions else 0.
        header_left, header_right = row_left-cap, row_right+cap
        bar_left = row_left
        for bar in row:
            # Custom interior ticks can extend farther than either endpoint,
            # including ticks on bars in the middle of a regional strip.
            for position, half_width in zip(bar['ticks'][2], bar['tick_half_widths']):
                tick_center = bar_left + position*bar['width']
                header_left = min(header_left, tick_center-half_width)
                header_right = max(header_right, tick_center+half_width)
            bar_left += bar['width'] + colorbar.gap_in
        if header_left < .015 or header_right > width-.015+1e-9:
            shortage = max(.015-header_left, header_right-(width-.015))
            raise _PageSizeRequired(
                'Colorbar header does not fit the page beside the requested frame; enlarge the page or shorten labels.',
                width + 2*shortage, height)
        for index, bar in enumerate(row):
            if bar['label']:
                figure.text((row_left+bar['width']/2)/width, (cursor+bar_height+.035+numeric_line+.02)/height, bar['label'],
                            ha='center', va='bottom', fontsize=presentation.text_pt, parse_math=False,
                            gid=f"colorbar-label-{bar['id']}")
            bar['bounds'] = (row_left/width, cursor/height, bar['width']/width, bar_height/height)
            row_left += bar['width'] + colorbar.gap_in
        cursor += row_height
    cursor = max(cursor, frame_top + base_header)
    if inline:
        figure.text(signal_right/width, (frame_top+inline_bottom)/height, signal[0], ha='right', va='bottom',
                    fontsize=presentation.text_pt, parse_math=False, gid='page-signal-inline')
        cursor = max(cursor, frame_top+inline_bottom+text_line)
    elif signal:
        cursor += .04
        for line in reversed(signal):
            figure.text(.04/width, cursor/height, line, ha='left', va='bottom',
                    fontsize=presentation.text_pt, parse_math=False, gid=f'page-signal-{len(figure.texts)}')
            cursor += text_line
    return header_line_count


def _add_annotations(ax, snapshot, presentation):
    if snapshot.title:
        # Literal text/newlines match the live title. Its clip and layout
        # exclusion keep long decoration from changing page geometry.
        ax.text(*presentation.title_position, snapshot.title, transform=ax.transAxes, ha='left', va='top',
                fontsize=presentation.text_pt, fontfamily=[presentation.font_family, 'DejaVu Sans'],
                gid='page-title-overlay', parse_math=False, clip_on=True, in_layout=False)

    def coordinates(point, space):
        if space == 'data' and presentation.axis.exchange_xy:
            return point[::-1]
        return point

    for item in presentation.annotations:
        if not item.visible:
            continue
        position = coordinates(item.position, item.coordinate_space)
        size = presentation.numeric_pt if item.font_role == 'numeric' else presentation.text_pt
        transform = ax.transData if item.coordinate_space == 'data' else ax.transAxes
        if item.kind == 'text':
            ax.text(*position, item.text, transform=transform, fontsize=size, color=item.color,
                    gid=f'annotation-{item.id}', parse_math=False, clip_on=True)
        else:
            target = coordinates(item.target, item.target_space)
            annotation = ax.annotate(item.text, xy=target, xytext=position,
                                    xycoords='data' if item.target_space == 'data' else 'axes fraction',
                                    textcoords='data' if item.coordinate_space == 'data' else 'axes fraction',
                                    fontsize=size, color=item.color, parse_math=False, clip_on=True,
                                    arrowprops={'arrowstyle': '->', 'color': item.color, 'linewidth': item.linewidth,
                                                'mutation_scale': item.head_size}, annotation_clip=False,
                                    gid=f'annotation-{item.id}')
            annotation.arrow_patch.set_clip_path(ax.patch)
            annotation.arrow_patch.set_clip_on(True)
            annotation.arrow_patch.set_gid(f'annotation-{item.id}-arrow')
    if presentation.include_peak_labels:
        for index, peak in enumerate(snapshot.peaks):
            point = coordinates((peak.x, peak.y), 'data')
            ax.plot(*point, marker='o', markersize=3, markerfacecolor='none', markeredgecolor='black',
                    linestyle='none', gid=f'peak-marker-{index}')
            ax.annotate(peak.text, point, xytext=(3, 3), textcoords='offset points',
                        fontsize=presentation.numeric_pt, parse_math=False, clip_on=True, gid=f'peak-label-{index}')
    for layer in snapshot.scatter_layers:
        style = layer.style
        if not style.visible or not layer.x.size:
            continue
        x, y = (layer.y, layer.x) if presentation.axis.exchange_xy else (layer.x, layer.y)
        ax.plot(x, y, linestyle='none', marker=style.marker, markersize=style.size,
                markerfacecolor=style.color if style.filled else 'none',
                markeredgecolor=style.color, markeredgewidth=style.edge_width,
                alpha=style.alpha, clip_on=True, zorder=5, gid=f'analysis-scatter-{layer.id}')


def _fit_default_tick_density(figure, ax, presentation):
    """Reduce automatic tick count after sizing, while preserving chosen fonts.

    An explicit interval is a user contract; an overcrowded explicit interval
    is rejected by the layout audit instead of silently changing that interval.
    """
    for coordinate, step, chosen_count, scale in ((ax.xaxis, presentation.axis.x_major_step, presentation.axis.x_major_count, ax.get_xscale()),
                                                 (ax.yaxis, presentation.axis.y_major_step, presentation.axis.y_major_count, ax.get_yscale())):
        if step is not None or chosen_count is not None or scale != 'linear' or not coordinate.get_visible():
            continue
        for count in (6, 5, 4, 3, 2, 1):
            coordinate.set_major_locator(MaxNLocator(nbins=count, min_n_ticks=2))
            figure.draw_without_rendering()
            renderer = figure.canvas.get_renderer()
            boxes = [tick.label1.get_window_extent(renderer) for tick in coordinate._update_ticks()
                     if tick.label1.get_visible() and tick.label1.get_text()]
            horizontal = coordinate is ax.xaxis
            boxes.sort(key=lambda box: box.x0 if horizontal else box.y0)
            gaps = [(current.x0-previous.x1) if horizontal else (current.y0-previous.y1)
                    for previous, current in zip(boxes, boxes[1:])]
            if not gaps or min(gaps) >= 2:
                break


def _replace_colorbar_gradients(root, bars, figure_size):
    defs = root.find('s:defs', _NS)
    if defs is None:
        defs = ET.SubElement(root, f'{{{SVG_NS}}}defs')
    width, height = (v * 72 for v in figure_size)
    for bar in bars:
        if 'bounds' not in bar:
            continue
        group = root.find(f".//s:g[@id='colorbar-solids-{bar['id']}']", _NS)
        if group is None:
            raise ValueError(f"SVG colorbar {bar['id']} is missing its known solids group.")
        gradient_id = f"colorbar-gradient-{bar['id']}"
        gradient = ET.SubElement(defs, f'{{{SVG_NS}}}linearGradient',
                                 {'id': gradient_id, 'x1': '0%', 'y1': '0%', 'x2': '100%', 'y2': '0%'})
        cmap = bar['cmap']
        # Duplicate stops retain exact palette steps and alpha, including ListedColormap.
        for index in range(cmap.N):
            color = cmap((index + .5)/cmap.N)
            for offset in (index/cmap.N, (index+1)/cmap.N):
                ET.SubElement(gradient, f'{{{SVG_NS}}}stop',
                              {'offset': f'{offset:.10g}', 'stop-color': to_hex(color[:3]),
                               'stop-opacity': f'{color[3] * bar["alpha"]:.10g}'})
        clip = next((element.attrib.get('clip-path') for element in group.iter()
                     if 'clip-path' in element.attrib), None)
        for child in list(group):
            group.remove(child)
        left, bottom, bar_width, bar_height = bar['bounds']
        attributes = {'x': f'{left*width:.10g}', 'y': f'{(1-bottom-bar_height)*height:.10g}',
                      'width': f'{bar_width*width:.10g}', 'height': f'{bar_height*height:.10g}',
                      'fill': f'url(#{gradient_id})'}
        if clip:
            attributes['clip-path'] = clip
        ET.SubElement(group, f'{{{SVG_NS}}}rect', attributes)


def _audit_layout(figure):
    figure.draw_without_rendering()
    renderer = figure.canvas.get_renderer()
    page = figure.bbox
    # Ignore intentionally clipped data labels; require all page/axis/header text to fit.
    texts = list(figure.texts)
    colorbar_text = {text for text in figure.texts
                     if (text.get_gid() or '').startswith('colorbar-label-')}
    for axis in figure.axes:
        if not axis.get_visible():
            continue
        texts.extend([axis.xaxis.label, axis.yaxis.label, axis.xaxis.get_offset_text(), axis.yaxis.get_offset_text()])
        for coordinate in (axis.xaxis, axis.yaxis):
            if coordinate.get_visible():
                # Only the ticks that Matplotlib actually draws inside view limits.
                for tick in coordinate._update_ticks():
                    texts.extend([tick.label1, tick.label2])
                    if axis is not figure.axes[0]:
                        colorbar_text.update((tick.label1, tick.label2))
    boxes = []
    for text in texts:
        if not text.get_visible() or not text.get_text():
            continue
        bounds = text.get_window_extent(renderer)
        if min(bounds.x0-page.x0, bounds.y0-page.y0, page.x1-bounds.x1, page.y1-bounds.y1) < -.1:
            raise ValueError('Page layout clips a label; enlarge the page, reduce precision or font size, or shorten text.')
        boxes.append((text, bounds))
    colorbar_overlap = False
    for index, (text, bounds) in enumerate(boxes):
        for other, other_bounds in boxes[index+1:]:
            if bounds.overlaps(other_bounds):
                if text in colorbar_text or other in colorbar_text:
                    colorbar_overlap = True
                    continue
                raise ValueError(f'Page layout overlaps labels {text.get_text()!r} and {other.get_text()!r}; enlarge the page.')
    offset = figure.axes[0].yaxis.get_offset_text()
    if offset.get_visible() and offset.get_text():
        bounds = offset.get_window_extent(renderer)
        if any(bounds.overlaps(axis.get_window_extent(renderer)) for axis in figure.axes[1:]):
            colorbar_overlap = True
    return (('Colorbar text overlaps nearby content. Reduce number precision or font size, or adjust colorbar width and spacing.',)
            if colorbar_overlap else ())


def _semantic_object_bounds(figure, ax, bars):
    """Measure the artists actually drawn, including clipping and tick density."""
    renderer = figure.canvas.get_renderer()
    page = figure.bbox
    bounds = {}

    def add(key, box, clip=None):
        box = Bbox.intersection(box, page)
        if box is not None and clip is not None:
            box = Bbox.intersection(box, clip)
        if box is None or box.width <= 0 or box.height <= 0:
            return
        bounds.setdefault(key, []).append((float(box.x0/page.width), float(box.y0/page.height),
                                          float(box.width/page.width), float(box.height/page.height)))

    def text(key, artist, clip=None):
        if artist.get_visible() and artist.get_text():
            # Annotation.get_window_extent also includes the whole arrow box;
            # use the text extent so empty space cannot intercept doubleclicks.
            from matplotlib.text import Text
            add(key, Text.get_window_extent(artist, renderer), clip)

    def ticks(key, coordinate):
        for tick in coordinate._update_ticks():
            for label in (tick.label1, tick.label2):
                text(key, label)
            for line in (tick.tick1line, tick.tick2line):
                if line.get_visible() and line.get_markersize() > 0:
                    add(key, line.get_window_extent(renderer))
        text(key, coordinate.get_offset_text())

    for name, coordinate, spines in (('horizontal', ax.xaxis, ('bottom', 'top')),
                                      ('vertical', ax.yaxis, ('left', 'right'))):
        if not coordinate.get_visible():
            continue
        text(name+'-label', coordinate.label)
        ticks(name+'-axis', coordinate)
        for spine in spines:
            if ax.spines[spine].get_visible():
                # A small physical tolerance makes the thin displayed frame usable.
                add(name+'-range', ax.spines[spine].get_window_extent(renderer).padded(2))
    for artist in ax.texts:
        gid = artist.get_gid() or ''
        if gid == 'page-title-overlay':
            text('title', artist, ax.bbox)
        elif gid.startswith('annotation-'):
            text('annotation:'+gid[len('annotation-'):], artist, ax.bbox)
    for artist in figure.texts:
        gid = artist.get_gid() or ''
        if gid.startswith('page-signal-'):
            text('signal-label', artist)
        elif gid.startswith('colorbar-label-'):
            text('colorbar:'+gid[len('colorbar-label-'):], artist)
    for bar in bars:
        cax = bar.get('axis')
        if cax is None:
            continue
        key = 'colorbar:'+bar['id']
        add(key, cax.bbox)
        ticks(key, cax.xaxis)
        for patch in cax.patches:
            if patch.get_visible():
                add(key, patch.get_window_extent(renderer))
    return {key: tuple(rects) for key, rects in bounds.items()}


def prepare_heatmap_page(snapshot: HeatmapSnapshot, presentation: PlotPresentation,
                         *, renderer=None) -> tuple[PlotPresentation, RenderedPage]:
    """Enlarge only paper for measured fixed-frame shortages; return its owner settings.

    The strict renderer still validates every final label and tick. Callers must
    adopt the returned presentation before publishing the page or its metadata.
    Auto-fit and unrelated errors retain their original strict behavior.
    """
    renderer = renderer or render_hybrid_svg
    resolved = presentation
    # Layout can discover header shortages after satisfying a frame shortage.
    # Bound the measured paper refinements explicitly.
    for attempt in range(8):
        try:
            return resolved, renderer(snapshot, resolved)
        except _PageSizeRequired as exc:
            if resolved.frame_width_in is None or attempt == 7:
                raise
            width, height = (max(current, required) for current, required in
                             zip((resolved.width_in, resolved.height_in), exc.minimum_size_in))
            if resolved.aspect_locked:
                scale = max(width/resolved.width_in, height/resolved.height_in)
                width, height = resolved.width_in*scale, resolved.height_in*scale
            if max(width, height) > 40:
                raise ValueError('Required page exceeds the supported 40 inch page size; reduce labels or the requested frame.') from exc
            if width <= resolved.width_in and height <= resolved.height_in:
                raise
            resolved = replace(resolved, width_in=width, height_in=height)
    raise AssertionError('Unreachable page preparation state.')


def render_hybrid_svg(snapshot: HeatmapSnapshot, presentation: PlotPresentation) -> RenderedPage:
    """Build one self-contained formal-page SVG from copied scientific data."""
    if not isinstance(snapshot, HeatmapSnapshot) or not isinstance(presentation, PlotPresentation):
        raise ValueError('Hybrid SVG rendering requires HeatmapSnapshot and PlotPresentation records.')
    x, y = _coordinates(snapshot)
    display, _ = prepare_display_values(snapshot.z, snapshot.norm)
    if not display.count():
        raise ValueError('Log heatmap has no positive values to display.' if snapshot.norm.kind == 'log'
                         else 'Heatmap has no finite values to display.')
    if snapshot.x.ndim == 1:
        # Inferred cell edges need not average back to nonuniform source centers.
        centers = tuple(values if values.size == count else (values[:-1] + values[1:]) / 2
                        for values, count in ((snapshot.x, snapshot.z.shape[1]),
                                              (snapshot.y, snapshot.z.shape[0])))
        visible = np.meshgrid(*centers)
    else:
        visible = ((x[:-1, :-1]+x[1:, 1:])/2, (y[:-1, :-1]+y[1:, 1:])/2)
    visible_mask = np.ones(snapshot.z.shape, dtype=bool)
    for centers, limits in zip(visible, (snapshot.x_limits, snapshot.y_limits)):
        if limits is not None:
            low, high = sorted(limits)
            visible_mask &= (centers >= low) & (centers <= high)
    nonpositive_count = int(np.count_nonzero(visible_mask & np.isfinite(snapshot.z) & (snapshot.z <= 0))) if snapshot.norm.kind == 'log' else 0
    if not valid_color_values(snapshot.z[visible_mask], snapshot.norm).size:
        raise ValueError('Visible Log heatmap has no positive values.' if snapshot.norm.kind == 'log'
                         else 'Visible heatmap has no finite values.')
    regions = snapshot.regions or (ColorRegion(id='global', start=0, stop=snapshot.z.shape[1], color_limits=snapshot.color_limits),)
    resolved = resolve_colormap(snapshot.cmap)
    cmap = (colormaps[resolved] if isinstance(resolved, str) else resolved).with_extremes(bad=MISSING_COLOR)
    bars = []
    for region in regions:
        try:
            norm = build_color_norm(snapshot.norm, region.color_limits)
            ticks = _bar_ticks(norm, presentation.colorbar.ticks.get(region.id), presentation.colorbar)
        except ValueError as exc:
            raise ValueError(f'Region {region.id}: {exc}') from exc
        bars.append({'id': region.id, 'norm': norm, 'ticks': ticks, 'cmap': cmap,
                     'alpha': snapshot.alpha,
                     'under': bool(np.ma.any(display[:, region.start:region.stop] < norm.vmin)),
                     'over': bool(np.ma.any(display[:, region.start:region.stop] > norm.vmax)),
                     'label': region.label if snapshot.regions and presentation.colorbar.show_label else ''})
    figure_size = (presentation.width_in, presentation.height_in)
    # Fixed hashsalt and omitted wall-clock date make unchanged renders byte-stable.
    with rc_context({'font.family': [presentation.font_family, 'DejaVu Sans'], 'svg.fonttype': 'none',
                     'svg.image_inline': True, 'svg.hashsalt': 'spectra-formal-page-v1',
                     'axes.formatter.use_mathtext': False, 'text.usetex': False,
                     'savefig.bbox': None}):
        figure = Figure(figsize=figure_size, dpi=72, facecolor='white')
        FigureCanvasAgg(figure)
        ax = figure.add_axes([.2, .16, .7, .55])
        display_limits = _configure_axis(ax, snapshot, presentation, x, y)
        for region, bar in zip(regions, bars):
            start, stop = region.start, region.stop
            xs, ys, zs = x[:, start:stop+1], y[:, start:stop+1], display[:, start:stop]
            if presentation.axis.exchange_xy:
                xs, ys, zs = ys.T, xs.T, zs.T
            # Adjacent meshes have equal zorder and no intervening vector artist:
            # Matplotlib's mixed-mode renderer composites them in a single pass.
            mesh = ax.pcolormesh(xs, ys, zs, cmap=cmap, norm=bar['norm'], shading='flat',
                                 rasterized=True, alpha=snapshot.alpha, edgecolors='none', zorder=0)
            bar['mesh'] = mesh
        header_lines = _layout(figure, ax, snapshot, presentation, bars)
        for bar in bars:
            if 'bounds' not in bar:
                continue
            cax = figure.add_axes(bar['bounds'])
            bar['axis'] = cax
            values, labels, positions = bar['ticks']
            colorbar = figure.colorbar(bar['mesh'], cax=cax, orientation='horizontal', ticks=values)
            colorbar.solids.set_rasterized(False)
            colorbar.solids.set_gid(f"colorbar-solids-{bar['id']}")
            apply_heatmap_colorbar_style(colorbar, presentation, bar['id'])
            # Explicit vector caps leave the inner [0,1] gradient/tick transform
            # untouched and retain the colormap's distinct under/over colors.
            fraction = .035 / (bar['bounds'][2] * presentation.width_in)
            for side, vertices, color in (
                ('under', [(-fraction, .5), (0, 0), (0, 1)], cmap.get_under()),
                ('over', [(1+fraction, .5), (1, 0), (1, 1)], cmap.get_over()),
            ):
                if presentation.colorbar.show_extensions and bar[side]:
                    cax.add_patch(Polygon(vertices, transform=cax.transAxes, facecolor=color,
                                         alpha=snapshot.alpha, edgecolor='black', linewidth=.6,
                                         clip_on=False, gid=f"colorbar-{side}-{bar['id']}"))
        _add_annotations(ax, snapshot, presentation)
        _fit_default_tick_density(figure, ax, presentation)
        _align_heatmap_corner_tick_labels(ax, figure.canvas.get_renderer())
        warnings = _audit_layout(figure)
        _update_heatmap_tick_contrast(ax, figure.canvas.get_renderer())
        object_bounds = _semantic_object_bounds(figure, ax, bars)
        output = BytesIO()
        # Only page/axes patches are transparent; data and annotation colors
        # retain their own alpha. Preview/PNG still composite onto white paper.
        figure.savefig(output, format='svg', dpi=presentation.heatmap_dpi, bbox_inches=None,
                       transparent=True, facecolor='none', edgecolor='none', metadata={'Date': None})
        root = ET.fromstring(output.getvalue())
        _replace_colorbar_gradients(root, bars, figure_size)
        title_group = root.find(".//s:g[@id='page-title-overlay']", _NS)
        if title_group is not None:
            # QtSvg ignores clip-path, but supports masks on native text. Keep
            # the standard SVG clip and add the same frame mask for preview/PNG.
            left, bottom, width, height = ax.get_position().bounds
            page_width, page_height = np.array(figure_size) * 72
            bounds = {'x': f'{left*page_width:.10g}', 'y': f'{(1-bottom-height)*page_height:.10g}',
                      'width': f'{width*page_width:.10g}', 'height': f'{height*page_height:.10g}'}
            mask_id = 'page-title-overlay-mask'
            mask = ET.SubElement(root.find('s:defs', _NS), f'{{{SVG_NS}}}mask',
                                 {'id': mask_id, 'maskUnits': 'userSpaceOnUse',
                                  'maskContentUnits': 'userSpaceOnUse', **bounds})
            ET.SubElement(mask, f'{{{SVG_NS}}}rect', {**bounds, 'fill': 'white'})
            title_group.set('mask', f'url(#{mask_id})')
        parents = {child: parent for parent in root.iter() for child in parent}
        for item in presentation.annotations:
            if item.kind == 'arrow' and item.visible:
                group = root.find(f".//s:g[@id='annotation-{item.id}']", _NS)
                arrow = root.find(f".//s:g[@id='annotation-{item.id}-arrow']", _NS)
                if group is not None and arrow is not None:
                    parents[arrow].remove(arrow)
                    group.insert(0, arrow)
        root.set('width', f'{presentation.width_in:.15g}in')
        root.set('height', f'{presentation.height_in:.15g}in')
        images = root.findall('.//s:image', _NS)
        if len(images) != 1 or not images[0].get(f'{{{XLINK_NS}}}href', '').startswith('data:image/png;base64,'):
            raise ValueError('Hybrid SVG must contain exactly one embedded heatmap PNG.')
        svg_bytes = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        axes_bounds = tuple(float(v) for v in ax.get_position().bounds)
        figure.clear()
    return RenderedPage(svg_bytes=svg_bytes, digest=hashlib.sha256(svg_bytes).hexdigest(),
                        owner_token=snapshot.owner_token, figure_size_in=figure_size,
                        axes_bounds=axes_bounds, plot_size_in=(axes_bounds[2]*figure_size[0], axes_bounds[3]*figure_size[1]),
                        colorbar_bounds={bar['id']: bar['bounds'] for bar in bars if 'bounds' in bar},
                        colorbar_tick_positions={bar['id']: bar['ticks'][2] for bar in bars},
                        display_x_limits=display_limits[0], display_y_limits=display_limits[1],
                        nonpositive_count=nonpositive_count,
                        under_count=sum(int(np.count_nonzero(visible_mask[:, region.start:region.stop] &
                            np.ma.filled(display[:, region.start:region.stop] < bar['norm'].vmin, False)))
                            for region, bar in zip(regions, bars)),
                        over_count=sum(int(np.count_nonzero(visible_mask[:, region.start:region.stop] &
                            np.ma.filled(display[:, region.start:region.stop] > bar['norm'].vmax, False)))
                            for region, bar in zip(regions, bars)),
                        missing_count=int(np.count_nonzero(visible_mask & np.isnan(snapshot.z))), header_line_count=header_lines,
                        valid_count=int(valid_color_values(snapshot.z[visible_mask], snapshot.norm).size),
                        heatmap_dpi=presentation.heatmap_dpi, font_family=presentation.font_family,
                        object_bounds=object_bounds, warnings=warnings or ())


def _output_path(path: str | Path, suffix: str) -> Path:
    path = Path(path)
    if not path.suffix:
        return path.with_suffix(suffix)
    if path.suffix.lower() != suffix:
        raise ValueError(f'Export path must use a {suffix} extension.')
    return path


def write_svg(page: RenderedPage, path: str | Path) -> Path:
    """Write these exact SVG bytes; never overwrite an existing output."""
    output = _output_path(path, '.svg')
    with output.open('xb') as handle:
        handle.write(page.svg_bytes)
    return output


def render_svg_image(page: RenderedPage, *, dpi: int | None = None):
    """Render final bytes with QtSvg at round(inches * DPI), returning a QImage.

    A QApplication/QGuiApplication must already exist; invoke on its GUI thread.
    This runtime object is a preview/export result and is never persistent state.
    """
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QFontDatabase, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    if QGuiApplication.instance() is None:
        raise ValueError('SVG preview/PNG export requires a running Qt application.')
    # Windows' offscreen Qt plugin reports system font families but draws
    # missing-glyph boxes until a real font file is registered. Register the
    # same fonts used by Matplotlib's measured layout, including symbol fallback.
    if QGuiApplication.platformName() == 'offscreen':
        for family in (page.font_family, 'DejaVu Sans'):
            font_path = findfont(FontProperties(family=family))
            if font_path not in _QT_FONT_FILES:
                if QFontDatabase.addApplicationFont(font_path) < 0:
                    raise ValueError(f'Qt could not load the preview font {family}.')
                _QT_FONT_FILES.add(font_path)
    dpi = page.heatmap_dpi if dpi is None else dpi
    if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
        raise ValueError('PNG DPI must be a positive integer.')
    pixels = tuple(round(value*dpi) for value in page.figure_size_in)
    if min(pixels) < 1:
        raise ValueError('PNG dimensions must be at least one pixel.')
    renderer = QSvgRenderer(QByteArray(page.svg_bytes))
    if not renderer.isValid():
        raise ValueError('QtSvg could not read the rendered SVG page.')
    image = QImage(*pixels, QImage.Format.Format_ARGB32)
    if image.isNull():
        raise ValueError('Could not allocate the PNG/preview image.')
    image.fill('white')
    painter = QPainter(image)
    try:
        renderer.render(painter, QRectF(0, 0, *pixels))
    finally:
        painter.end()
    return image


def write_svg_png(page: RenderedPage, path: str | Path, *, dpi: int | None = None,
                  metadata: Mapping[str, str] | None = None) -> Path:
    """Rasterize the same final SVG bytes and retain PNG metadata and physical DPI."""
    from PySide6.QtCore import QBuffer, QIODevice
    output = _output_path(path, '.png')
    # Check before rendering, then use exclusive creation to close the race.
    if output.exists():
        raise FileExistsError(output)
    dpi = page.heatmap_dpi if dpi is None else dpi
    image = render_svg_image(page, dpi=dpi)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise ValueError('Qt could not encode the PNG page.')
    info = PngImagePlugin.PngInfo()
    for key, value in (metadata or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError('PNG metadata keys and values must be strings.')
        info.add_text(key, value)
    with Image.open(BytesIO(bytes(buffer.data()))) as decoded:
        encoded = BytesIO()
        decoded.save(encoded, format='PNG', dpi=(dpi, dpi), pnginfo=info)
    with output.open('xb') as handle:
        handle.write(encoded.getvalue())
    return output


__all__ = ['RenderedPage', 'MISSING_COLOR', 'build_color_norm', 'prepare_display_values',
           'valid_color_values', 'prepare_heatmap_page', 'render_hybrid_svg', 'render_svg_image', 'write_svg', 'write_svg_png']
