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


EXPORT_FIGSIZE = (5.15, 4.14)
EXPORT_DPI = 300
PLOT_FONT_SIZE = 12


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


def style_plot_axis(axis: Axes) -> None:
    """Keep every plot label, tick and scientific offset at the same point size."""
    for coordinate in (axis.xaxis, axis.yaxis):
        formatter = coordinate.get_major_formatter()
        if isinstance(formatter, ScalarFormatter):
            formatter.set_powerlimits((-3, 3))
    axis.tick_params(axis="both", which="both", labelsize=PLOT_FONT_SIZE)
    for text in (axis.title, axis.xaxis.label, axis.yaxis.label,
                 axis.xaxis.get_offset_text(), axis.yaxis.get_offset_text()):
        text.set_fontsize(PLOT_FONT_SIZE)


def style_heatmap_colorbar(colorbar, label: str = "ΔR/R (a. u.)") -> None:
    """Reference-style top ticks and a signal label to the left of the bar."""
    axis = colorbar.ax
    vmin, vmax = colorbar.mappable.get_clim()
    colorbar.set_ticks([vmin, vmax])
    colorbar.formatter = FuncFormatter(_cb_fmt_compact)
    colorbar.update_ticks()
    axis.xaxis.set_minor_locator(NullLocator())
    axis.xaxis.set_minor_formatter(NullFormatter())
    axis.xaxis.set_ticks_position("top")
    axis.tick_params(top=True, labeltop=True, bottom=False, labelbottom=False, pad=2)
    axis.set_xlabel(label, fontsize=PLOT_FONT_SIZE)
    axis.xaxis.set_label_coords(-0.10, 0.8)
    axis.xaxis.label.set_horizontalalignment("right")
    axis.xaxis.label.set_verticalalignment("center")
    style_plot_axis(axis)


def position_heatmap_title(heat_axis: Axes, colorbar_axis: Axes, title) -> None:
    """Keep the manual title near the upper-left corner, clear of other labels."""
    figure = heat_axis.figure
    renderer = figure.canvas.get_renderer()
    # Refresh tick and scientific-offset text before measuring their bounds.
    colorbar_axis.get_tightbbox(renderer)
    heat_axis.yaxis.get_tightbbox(renderer)
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
) -> tuple[Axes, Axes | None]:
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
    colorbar_axis = next((axis for axis in candidates if axis is not heat_axis), None)
    return heat_axis, colorbar_axis


def _find_heatmap_mesh(axis: Axes) -> QuadMesh:
    meshes = [collection for collection in axis.collections if isinstance(collection, QuadMesh)]
    if not meshes:
        raise ValueError("Heatmap export requires a live heatmap mesh.")
    return meshes[0]


def _mesh_data(mesh: QuadMesh) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    coordinates = np.asarray(mesh.get_coordinates())
    if coordinates.ndim != 3 or coordinates.shape[-1] != 2:
        raise ValueError("Heatmap mesh has unsupported coordinates.")
    rows, columns = coordinates.shape[0] - 1, coordinates.shape[1] - 1
    values = np.ma.asarray(mesh.get_array())
    if values.size != rows * columns:
        raise ValueError("Heatmap mesh has unsupported data dimensions.")
    return coordinates[..., 0], coordinates[..., 1], values.reshape(rows, columns)


def _build_heatmap_export_figure(
    figure: Figure,
    *,
    selected_axes: Sequence[Axes] | None = None,
) -> Figure:
    """Build a detached reference-layout figure from the live heatmap artists."""
    heat_axis, colorbar_axis = _resolve_heatmap_axes(figure, selected_axes)
    mesh = _find_heatmap_mesh(heat_axis)
    x_coordinates, y_coordinates, values = _mesh_data(mesh)

    exported = Figure(figsize=EXPORT_FIGSIZE, dpi=EXPORT_DPI, facecolor="white")
    FigureCanvasAgg(exported)
    # Compact fixed margins surround a data rectangle exactly 4 inches wide by 3 high.
    width, height = EXPORT_FIGSIZE
    ax = exported.add_axes([0.85 / width, 0.55 / height, 4.0 / width, 3.0 / height])
    exported_mesh = ax.pcolormesh(
        x_coordinates, y_coordinates, values, shading="flat",
        cmap=mesh.get_cmap(), norm=deepcopy(mesh.norm),
    )
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
    colorbar_label = ""
    if colorbar_axis is not None:
        colorbar_label = colorbar_axis.get_ylabel() or colorbar_axis.get_xlabel()
    colorbar_axis_export = exported.add_axes([3.75 / width, 3.59 / height, 1.10 / width, 0.07 / height])
    colorbar = exported.colorbar(exported_mesh, cax=colorbar_axis_export, orientation="horizontal")
    style_heatmap_colorbar(colorbar, colorbar_label)
    position_heatmap_title(ax, colorbar_axis_export, title)
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
