"""Detached reference-layout PNG export for the application's heatmap."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from matplotlib import rc_context
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.axes import Axes
from matplotlib.ticker import FuncFormatter, NullFormatter, NullLocator, ScalarFormatter
from matplotlib.collections import QuadMesh

from .signal_titles import RAW_LABEL, display_signal_label


EXPORT_FIGSIZE = (5.15, 4.14)
EXPORT_DPI = 300
PLOT_FONT_SIZE = 12
REGIONAL_COLORBAR_WIDTH = 0.60
REGIONAL_COLORBAR_HEIGHT = 0.045
REGIONAL_COLORBAR_GAP = 0.22
REGIONAL_COLORBAR_FONT_SIZE = 8.0
REGIONAL_COLORBAR_MIN_FONT_SIZE = 6.5
REGIONAL_COLORBAR_TICK_GAP = 4.0
REGIONAL_SIGNAL_LABEL_GAP = 0.02
_REGIONAL_LABELS = frozenset(("Left", "Middle", "Right"))


def normalize_png_path(path: str | Path) -> Path:
    """Return a path with an explicit PNG extension or reject other formats."""
    output = Path(path)
    if not output.suffix:
        return output.with_name(f"{output.name}.png")
    if output.suffix.lower() != ".png":
        raise ValueError("PNG export path must use a .png extension.")
    return output


def _cb_fmt_compact(value: float, _position: float | None = None) -> str:
    if not np.isfinite(value):
        return ""
    absolute = abs(float(value))
    if absolute < 1e-12:
        return "0"
    if 1e-2 <= absolute < 1e4:
        return f"{value:.4g}"
    return f"{value:.0e}".replace("+0", "+").replace("-0", "-")


def _cb_fmt_regional(value: float, _position: float | None = None) -> str:
    """Use two significant digits and compact, normalized scientific notation."""
    if not np.isfinite(value):
        return ""
    absolute = abs(float(value))
    if absolute < 1e-12:
        return "0"
    if 1e-2 <= absolute < 1e4:
        formatted = f"{value:.2g}"
    else:
        formatted = f"{value:.1e}"
    if "e" not in formatted:
        return formatted
    mantissa, exponent = formatted.split("e")
    return f"{mantissa.removesuffix('.0')}e{int(exponent)}"


def style_plot_axis(axis: Axes, *, font_size: float | None = None) -> None:
    """Keep every plot label, tick and scientific offset at the same point size."""
    if font_size is None:
        font_size = PLOT_FONT_SIZE
    for coordinate in (axis.xaxis, axis.yaxis):
        formatter = coordinate.get_major_formatter()
        if isinstance(formatter, ScalarFormatter):
            formatter.set_powerlimits((-3, 3))
    axis.tick_params(axis="both", which="both", labelsize=font_size)
    for text in (axis.title, axis.xaxis.label, axis.yaxis.label,
                 axis.xaxis.get_offset_text(), axis.yaxis.get_offset_text()):
        text.set_fontsize(font_size)


def style_heatmap_colorbar(
    colorbar,
    label: str = RAW_LABEL,
    *,
    font_size: float | None = None,
) -> None:
    """Reference-style top ticks and a signal label to the left of the bar."""
    if font_size is None:
        font_size = PLOT_FONT_SIZE
    axis = colorbar.ax
    vmin, vmax = colorbar.mappable.get_clim()
    colorbar.set_ticks([vmin, vmax])
    formatter = _cb_fmt_regional if label in _REGIONAL_LABELS else _cb_fmt_compact
    colorbar.formatter = FuncFormatter(formatter)
    colorbar.update_ticks()
    axis.xaxis.set_minor_locator(NullLocator())
    axis.xaxis.set_minor_formatter(NullFormatter())
    axis.xaxis.set_ticks_position("top")
    axis.tick_params(top=True, labeltop=True, bottom=False, labelbottom=False, pad=2)
    axis.set_xlabel("" if label in _REGIONAL_LABELS else display_signal_label(label), fontsize=font_size)
    axis.xaxis.set_label_coords(-0.10, 0.8)
    axis.xaxis.label.set_horizontalalignment("right")
    axis.xaxis.label.set_verticalalignment("center")
    style_plot_axis(axis, font_size=font_size)
    if label in _REGIONAL_LABELS:
        ticks = axis.get_xticklabels()
        if len(ticks) >= 2:
            ticks[0].set_horizontalalignment("left")
            ticks[-1].set_horizontalalignment("right")


def position_heatmap_title(heat_axis: Axes, colorbar_axis: Axes, title) -> None:
    """Keep the manual title near the upper-left corner, clear of other labels."""
    figure = heat_axis.figure
    renderer = figure.canvas.get_renderer()
    # Re-measure the normal anchor on every layout, so a temporary small canvas
    # does not leave the label raised after the view grows or its offset clears.
    colorbar_axis.xaxis.set_label_coords(-0.10, 0.8)
    # Refresh tick and scientific-offset text before measuring their bounds.
    colorbar_axis.get_tightbbox(renderer)
    heat_axis.yaxis.get_tightbbox(renderer)
    signal = colorbar_axis.xaxis.label
    offset = heat_axis.yaxis.get_offset_text()
    if signal.get_text() and offset.get_text():
        signal_bounds = signal.get_window_extent(renderer)
        offset_bounds = offset.get_window_extent(renderer)
        if signal_bounds.overlaps(offset_bounds):
            x, y = signal.get_position()
            rise = offset_bounds.y1 + 0.02 * figure.dpi - signal_bounds.y0
            colorbar_axis.xaxis.set_label_coords(
                x, y + rise / colorbar_axis.get_window_extent(renderer).height)
    plot = heat_axis.get_window_extent(renderer)
    title.set_transform(figure.transFigure)
    title.set_horizontalalignment("left")
    title.set_verticalalignment("bottom")
    bottom = plot.y1 + 0.04 * figure.dpi
    title.set_position(figure.transFigure.inverted().transform((plot.x0, bottom)))
    if not title.get_text():
        return
    bounds = title.get_window_extent(renderer)
    for label in (colorbar_axis.xaxis.label, *colorbar_axis.get_xticklabels(),
                  heat_axis.yaxis.get_offset_text()):
        if label.get_text():
            obstacle = label.get_window_extent(renderer)
            if bounds.x0 < obstacle.x1 and bounds.x1 > obstacle.x0:
                bottom = max(bottom, obstacle.y1 + 0.02 * figure.dpi)
    title.set_position(figure.transFigure.inverted().transform((plot.x0, bottom)))


def _resolve_heatmap_axes(
    figure: Figure,
    selected_axes: Sequence[Axes] | None,
) -> tuple[Axes, tuple[Axes, ...]]:
    if selected_axes is None:
        candidates = tuple(figure.axes)
    else:
        candidates = tuple(selected_axes)
        if not candidates:
            raise ValueError("At least one axis is required for a heatmap export.")
        if len(set(candidates)) != len(candidates) or any(axis not in figure.axes for axis in candidates):
            raise ValueError("Selected export axes must be unique axes from the figure.")
    heat_axis = next(
        (axis for axis in candidates if any(isinstance(collection, QuadMesh) for collection in axis.collections)),
        None,
    )
    if heat_axis is None:
        raise ValueError("Heatmap export requires a live heatmap mesh.")
    colorbar_axes = tuple(axis for axis in candidates if axis is not heat_axis)
    return heat_axis, colorbar_axes


def _find_heatmap_mesh(axis: Axes) -> QuadMesh:
    meshes = [collection for collection in axis.collections if isinstance(collection, QuadMesh)]
    if not meshes:
        raise ValueError("Heatmap export requires a live heatmap mesh.")
    return meshes[0]


def _find_heatmap_meshes(axis: Axes) -> list[QuadMesh]:
    meshes = [collection for collection in axis.collections if isinstance(collection, QuadMesh)]
    if not meshes:
        raise ValueError("Heatmap export requires a live heatmap mesh.")
    return meshes


def _mesh_data(mesh: QuadMesh) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    coordinates = np.asarray(mesh.get_coordinates())
    if coordinates.ndim != 3 or coordinates.shape[-1] != 2:
        raise ValueError("Heatmap mesh has unsupported coordinates.")
    rows, columns = coordinates.shape[0] - 1, coordinates.shape[1] - 1
    values = np.ma.asarray(mesh.get_array())
    if values.size != rows * columns:
        raise ValueError("Heatmap mesh has unsupported data dimensions.")
    return coordinates[..., 0], coordinates[..., 1], values.reshape(rows, columns)


def _fit_regional_tick_labels(figure: Figure, colorbar_axes: Sequence[Axes], *, render: bool = True) -> None:
    """Keep every regional endpoint label separated by a small measured gap."""
    labels = [
        tick
        for axis in colorbar_axes
        for tick in axis.get_xticklabels()
        if tick.get_visible() and tick.get_text()
    ]
    if not labels:
        return
    font_size = REGIONAL_COLORBAR_FONT_SIZE
    while True:
        (figure.canvas.draw if render else figure.draw_without_rendering)()
        boxes = sorted(
            (label.get_window_extent(figure.canvas.get_renderer()) for label in labels),
            key=lambda box: box.x0,
        )
        minimum_gap = min(
            current.x0 - previous.x1
            for previous, current in zip(boxes, boxes[1:])
        ) if len(boxes) > 1 else float("inf")
        if minimum_gap >= REGIONAL_COLORBAR_TICK_GAP:
            return
        next_size = font_size - 0.25
        if next_size < REGIONAL_COLORBAR_MIN_FONT_SIZE:
            return
        font_size = next_size
        for label in labels:
            label.set_fontsize(font_size)


def position_regional_header(heat_axis: Axes, colorbar_axes: Sequence[Axes], title, signal, *, render: bool = True) -> None:
    """Keep the title close to the data; move compact scales above it only on collision."""
    figure = heat_axis.figure
    (figure.canvas.draw if render else figure.draw_without_rendering)()
    renderer = figure.canvas.get_renderer()
    plot = heat_axis.get_window_extent(renderer)
    title.set_transform(figure.transFigure)
    title.set_horizontalalignment("left")
    title.set_verticalalignment("bottom")
    title.set_position(figure.transFigure.inverted().transform((plot.x0, plot.y1 + 0.04 * figure.dpi)))
    title_box = title.get_window_extent(renderer)
    offset = heat_axis.yaxis.get_offset_text()
    obstacles = [title_box] if title.get_text() else []
    if offset.get_text():
        offset_box = offset.get_window_extent(renderer)
        if title.get_text() and title_box.overlaps(offset_box):
            title.set_position(figure.transFigure.inverted().transform((offset_box.x1 + 0.02 * figure.dpi, title_box.y0)))
            obstacles[0] = title.get_window_extent(renderer)
        obstacles.append(offset_box)
    shift = 0.0
    for axis in colorbar_axes:
        boxes = [axis.get_window_extent(renderer)] + [tick.get_window_extent(renderer) for tick in axis.get_xticklabels() if tick.get_text()]
        overlap = [box for box in obstacles if any(box.overlaps(other) for other in boxes)]
        if overlap:
            shift = max(shift, (max(box.y1 for box in overlap) + 0.02 * figure.dpi - min(box.y0 for box in boxes)) / figure.bbox.height)
    if shift:
        for axis in colorbar_axes:
            position = axis.get_position()
            axis.set_position([position.x0, position.y0 + shift, position.width, position.height])
    (figure.canvas.draw if render else figure.draw_without_rendering)()
    if signal is not None:
        boxes = [tick.get_window_extent(renderer) for axis in colorbar_axes for tick in axis.get_xticklabels() if tick.get_text()]
        top = max([box.y1 for box in boxes] + [box.y1 for box in obstacles])
        signal.set_horizontalalignment("right")
        signal.set_verticalalignment("bottom")
        signal.set_position(figure.transFigure.inverted().transform((plot.x1, top + 0.02 * figure.dpi)))
        # Actual font metrics can extend a fraction of a pixel beyond the
        # nominal fixed header. Keep a small measured page-edge allowance.
        bounds = signal.get_window_extent(renderer)
        overflow = max(0.0, bounds.y1 - figure.bbox.y1 + 0.003 * figure.dpi)
        if overflow:
            signal.set_position(figure.transFigure.inverted().transform(
                (plot.x1, top + 0.02 * figure.dpi - overflow)))


def _build_heatmap_export_figure(
    figure: Figure,
    *,
    selected_axes: Sequence[Axes] | None = None,
) -> Figure:
    """Build a detached reference-layout figure from the live heatmap artists."""
    heat_axis, colorbar_axes = _resolve_heatmap_axes(figure, selected_axes)
    meshes = _find_heatmap_meshes(heat_axis)

    mesh_count = len(meshes)
    export_height = EXPORT_FIGSIZE[1]
    exported = Figure(figsize=(EXPORT_FIGSIZE[0], export_height), dpi=EXPORT_DPI, facecolor="white")
    FigureCanvasAgg(exported)
    # Compact fixed margins surround a data rectangle exactly 4 inches wide by 3 high.
    width, _base_height = EXPORT_FIGSIZE
    height = export_height
    ax = exported.add_axes([0.85 / width, 0.55 / height, 4.0 / width, 3.0 / height])
    exported_meshes = []
    for mesh in meshes:
        x_coordinates, y_coordinates, values = _mesh_data(mesh)
        exported_meshes.append(ax.pcolormesh(
            x_coordinates, y_coordinates, values, shading="flat",
            cmap=mesh.get_cmap(), norm=deepcopy(mesh.norm),
        ))
    exported_mesh = exported_meshes[0]
    ax.set_xlabel(heat_axis.get_xlabel())
    ax.set_ylabel(heat_axis.get_ylabel(), parse_math=False)
    ax.set_xlim(heat_axis.get_xlim())
    ax.set_ylim(heat_axis.get_ylim())
    ax.xaxis.set_major_locator(deepcopy(heat_axis.xaxis.get_major_locator()))
    ax.xaxis.set_major_formatter(deepcopy(heat_axis.xaxis.get_major_formatter()))
    ax.yaxis.set_major_locator(deepcopy(heat_axis.yaxis.get_major_locator()))
    ax.yaxis.set_major_formatter(deepcopy(heat_axis.yaxis.get_major_formatter()))
    style_plot_axis(ax)
    title = exported.text(
        0, 0, heat_axis.get_title(), ha="left", va="bottom",
        fontsize=PLOT_FONT_SIZE, wrap=True, parse_math=False,
    )
    if colorbar_axes:
        colorbar_labels = [axis.get_ylabel() or axis.get_xlabel() for axis in colorbar_axes]
    else:
        colorbar_labels = [""] * len(exported_meshes)
    if len(colorbar_labels) < len(exported_meshes):
        colorbar_labels.extend([colorbar_labels[-1] if colorbar_labels else ""] *
                               (len(exported_meshes) - len(colorbar_labels)))
    count = len(exported_meshes)
    signal_label = next(
        (artist.get_text() for artist in figure.texts
         if artist.get_gid() == "heatmap_signal_label" and artist.get_text()),
        "",
    )
    if not signal_label:
        signal_label = next((label for label in reversed(colorbar_labels) if label), "")
    for prefix in ("Left · ", "Middle · ", "Right · "):
        if signal_label.startswith(prefix):
            signal_label = signal_label[len(prefix):]
            break
    region_names = ("Left", "Right") if count == 2 else ("Left", "Middle", "Right")
    regional = count > 1
    bar_width = REGIONAL_COLORBAR_WIDTH if regional else min(1.10, 1.92)
    plot_right = 0.85 + 4.0
    strip_width = (
        mesh_count * bar_width + (mesh_count - 1) * REGIONAL_COLORBAR_GAP
        if regional else bar_width
    )
    left = plot_right - strip_width
    exported_colorbar_axes = []
    for index, (mesh, _label) in enumerate(zip(exported_meshes, colorbar_labels)):
        bar_left = left + index * (bar_width + REGIONAL_COLORBAR_GAP) if regional else left
        bar_bottom = 3.59
        bar_height = REGIONAL_COLORBAR_HEIGHT if regional else 0.07
        colorbar_axis_export = exported.add_axes([
            bar_left / width, bar_bottom / height,
            bar_width / width, bar_height / height,
        ])
        colorbar = exported.colorbar(mesh, cax=colorbar_axis_export, orientation="horizontal")
        label = region_names[index] if regional else signal_label
        style_heatmap_colorbar(
            colorbar,
            label,
            font_size=REGIONAL_COLORBAR_FONT_SIZE if regional else PLOT_FONT_SIZE,
        )
        if regional:
            colorbar.formatter = FuncFormatter(_cb_fmt_regional)
            colorbar.update_ticks()
            colorbar_axis_export.xaxis.set_label_coords(0.5, -1.0)
            colorbar_axis_export.xaxis.label.set_horizontalalignment("center")
            colorbar_axis_export.xaxis.label.set_verticalalignment("top")
            ticks = colorbar_axis_export.get_xticklabels()
            if len(ticks) >= 2:
                ticks[0].set_horizontalalignment("left")
                ticks[-1].set_horizontalalignment("right")
        exported_colorbar_axes.append(colorbar_axis_export)
    if regional:
        _fit_regional_tick_labels(exported, exported_colorbar_axes)
    if regional and signal_label:
        signal_artist = exported.text(
            0, 0,
            signal_label,
            ha="right", va="bottom", fontsize=REGIONAL_COLORBAR_FONT_SIZE,
            gid="heatmap_signal_label",
        )
    if regional:
        position_regional_header(ax, exported_colorbar_axes, title, signal_artist if signal_label else None)
    else:
        position_heatmap_title(ax, exported_colorbar_axes[-1], title)
    return exported


def export_figure_png(
    figure: Figure,
    path: str | Path,
    *,
    selected_axes: Sequence[Axes] | None = None,
    metadata: Mapping[str, str] | None = None,
) -> Path:
    """Save a 4 x 3-inch heatmap plot area plus compact outside labels at 300 DPI."""
    output = normalize_png_path(path)
    exported = _build_heatmap_export_figure(figure, selected_axes=selected_axes)
    # Explicitly disable user/global tight cropping so the physical page is invariant.
    with rc_context({"savefig.bbox": None}):
        exported.savefig(
            output, dpi=EXPORT_DPI, facecolor="white", edgecolor="none",
            transparent=False, bbox_inches=None,
            metadata=metadata,
        )
    return output


__all__ = [
    "EXPORT_DPI",
    "EXPORT_FIGSIZE",
    "export_figure_png",
    "normalize_png_path",
]
