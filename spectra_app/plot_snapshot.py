"""Detached heatmap inputs from a dataset, including inactive batch targets."""
from __future__ import annotations

import numpy as np

from .plot_presentation import ColorRegion, HeatmapSnapshot, PeakLabel, ScatterLayer
from .svg_export import valid_color_values


def axis_edges(values):
    values = np.asarray(values, dtype=float).ravel()
    if values.size == 1:
        return np.array([values[0] - .5, values[0] + .5])
    middle = (values[:-1] + values[1:]) / 2
    return np.concatenate(([values[0] - (middle[0] - values[0])], middle,
                           [values[-1] + (values[-1] - middle[-1])]))


def region_layout(cube, view):
    """Use original data columns; axis presentation never changes these slices."""
    energy = np.asarray(cube.energy, dtype=float).ravel()
    edges = axis_edges(energy)
    count = min(int(view.color_scale_mode) if view.color_scale_mode in ('2', '3') else 1,
                energy.size)
    if count <= 1:
        return edges, [(0, energy.size)], []
    values = list(view.color_region_boundaries)
    if len(values) != count - 1:
        values = list(np.linspace(edges[0], edges[-1], count + 1)[1:-1])
    internal = np.arange(1, energy.size)
    indices = [int(np.argmin(np.abs(edges[internal] - value))) + 1 for value in values]
    indices = [max(i + 1, min(energy.size - (len(indices) - i - 1) - 1, index))
               for i, index in enumerate(indices)]
    for i in range(1, len(indices)):
        indices[i] = max(indices[i], indices[i - 1] + 1)
    return edges, list(zip([0, *indices], [*indices, energy.size])), [float(edges[i]) for i in indices]


def snapshot_dataset(session, *, activation_epoch=0, science_revision=0,
                     view_revision=0, presentation_revision=0):
    """Copy only declared scientific/view state; never inspect the live canvas."""
    from .dataset_state import graph_cube, graph_signal_label
    from .analysis_document import graph_format_view, graph_presentation
    cube, view = graph_cube(session), graph_format_view(session)
    graph = session.active_graph
    if cube is None:
        raise ValueError('Load a DAT file first.')
    _, slices, _ = region_layout(cube, view)
    regions = []
    if len(slices) > 1:
        ranges = view.last_valid_region_color_limits
        xlo, xhi = sorted(view.x_limits)
        ylo, yhi = sorted(view.y_limits)
        rows = np.flatnonzero((cube.gate >= ylo) & (cube.gate <= yhi))
        for i, (start, stop) in enumerate(slices):
            if i < len(ranges):
                limits = ranges[i]
            else:
                columns = np.flatnonzero((cube.energy >= xlo) & (cube.energy <= xhi))
                columns = columns[(columns >= start) & (columns < stop)]
                values = valid_color_values(cube.z[np.ix_(rows, columns)], view.norm)
                limits = tuple(np.percentile(values, [.5, 99.5])) if values.size else view.color_limits
                if limits[0] == limits[1]:
                    gap = max(abs(limits[0]) * 1e-6, 1e-3)
                    if view.norm.kind == 'log':
                        gap = min(gap, limits[0] / 2)
                    limits = (limits[0] - gap, limits[1] + gap)
                if view.norm.kind == 'zero-centered':
                    low, high = limits
                    limits = (min(low, -max(abs(high)*1e-6, 1e-3)),
                              max(high, max(abs(low)*1e-6, 1e-3)))
            regions.append(ColorRegion(f'region-{i+1}', start, stop, limits,
                                       ('Left', 'Right')[i] if len(slices) == 2 else ('Left', 'Middle', 'Right')[i]))
    peaks = []
    analysis_matches = session.active_graph.source_mode == 'processed' or session.current_cube is session.base_cube
    if graph.analysis is None and graph_presentation(session).include_peak_labels and analysis_matches:
        selected = {int(gi): np.asarray(indices) for gi, indices in session.batch_peak_map.items()}
        if session.peak_gate_idx is not None:
            selected[session.peak_gate_idx] = np.asarray(session.peak_indices)
        source = session.base_cube if session.active_graph.source_mode == 'raw' else session.current_cube
        gates = session.active_graph.gate_indices
        for gi, indices in sorted(selected.items()):
            if 0 <= gi < source.gate.size and (gates is None or gi in gates):
                for rank, ei in enumerate(indices):
                    if 0 <= int(ei) < cube.energy.size:
                        label = session.peak_label_lookup.get((gi, int(ei)), rank + 1)
                        peaks.append(PeakLabel(float(cube.energy[int(ei)]), float(source.gate[gi]), str(label)))
    annotations = view.annotations
    name, unit = annotations['y_axis_name'], annotations['y_axis_unit']
    return HeatmapSnapshot(cube.energy, cube.gate, cube.z, x_label=cube.energy_label,
                           y_label=f'{name} ({unit})' if unit else name,
                           signal_label=graph_signal_label(session) if view.signal_title_auto else annotations['signal_title'],
                           title=annotations['plot_title'],
                           cmap=view.colormap, color_limits=view.color_limits,
                           x_limits=view.x_limits, y_limits=view.y_limits, norm=view.norm,
                           regions=tuple(regions), peaks=tuple(peaks), dataset_id=session.dataset_id,
                           activation_epoch=activation_epoch, science_revision=science_revision,
                           view_revision=view_revision, presentation_revision=presentation_revision,
                           graph_id=session.active_graph_id,
                           scatter_layers=_analysis_scatter(graph))


def _analysis_scatter(graph):
    """Use exact centers and physical Gates; only the formal renderer exchanges axes."""
    document = graph.analysis
    if document is None:
        return ()
    owner = document.science
    cube = owner.current_cube
    allowed = None if graph.gate_indices is None else set(graph.gate_indices)
    points = {name: ([], []) for name in document.overlay_styles}

    def add(role, kind, gi, position):
        if 0 <= gi < cube.gate.size and (allowed is None or gi in allowed) and np.isfinite(position):
            x, y = points[f'{role}-{kind}']
            x.append(float(position))
            y.append(float(cube.gate[gi]))

    # The durable seeds hold continuous manual positions and separate peak/dip
    # identities even when both map to the same sampled index in legacy views.
    for gi, seeds in sorted(document.seeds_by_gate.items()):
        for seed in seeds:
            if seed.enabled:
                add('detected', seed.kind, gi, seed.position)
    results = dict(owner.batch_fit_result_map)
    if owner.fit_result is not None and owner.fit_gate_idx is not None:
        results[owner.fit_gate_idx] = owner.fit_result
    for gi, fit in sorted(results.items()):
        for position, amplitude in zip(fit.centers, fit.amplitudes):
            add('fitted', 'peak' if amplitude >= 0 else 'dip', gi, position)
    return tuple(ScatterLayer(name, x, y, *name.split('-', 1), document.overlay_styles[name])
                 for name, (x, y) in points.items())
