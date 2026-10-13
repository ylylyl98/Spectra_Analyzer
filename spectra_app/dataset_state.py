"""In-process scientific state for the shared spectrum-matrix viewer.

No Qt objects, artists, jobs or lifecycle counters belong in these records.
The explicit window bindings below bridge existing algorithms to one owner.
"""
from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, field, replace
import os
import hashlib
from pathlib import Path
import re
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import numpy as np

from .dat_io import DataCube
from .fitting import FitResult
from .processing import energy_second_derivative
from .signal_titles import RAW_LABEL, automatic_signal_label, infer_source_signal_kind
from .title_templates import manual_settings, render_title
from .plot_presentation import NormSpec, PlotPresentation
from .svg_export import valid_color_values
from .workbook_state import (Comparison, Sheet, Workbook, ensure_dataset_sheet,
                             reconcile_dataset_sheets, remove_dataset_sheets, _source_key)

if TYPE_CHECKING:
    from .analysis_document import AnalysisDocument


BOUND_NAMES = ('xmin', 'xmax', 'ymin', 'ymax', 'vmin', 'vmax')


def canonical_path(path: str | Path) -> str:
    """Absolute platform-normalized index key; the file need not still exist."""
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


def dataset_display_labels(sessions) -> dict[str, str]:
    """Filename labels with the shortest distinguishing parent for duplicates."""
    sessions = list(sessions)
    same_names = {}
    for session in sessions:
        name = session.source_path.name if session.source_path else 'Untitled dataset'
        same_names.setdefault(name.casefold(), []).append(session)
    labels = {}
    for session in sessions:
        path = session.source_path
        label = path.name if path else 'Untitled dataset'
        siblings = same_names[label.casefold()]
        if path and len(siblings) > 1:
            parts = path.parent.parts
            depth = 1
            while depth < len(parts) and any(
                    other.dataset_id != session.dataset_id and other.source_path is not None
                    and tuple(part.casefold() for part in other.source_path.parent.parts[-depth:])
                    == tuple(part.casefold() for part in parts[-depth:])
                    for other in siblings):
                depth += 1
            label += f' — {Path(*parts[-depth:])}'
        labels[session.dataset_id] = label
    duplicates = {}
    for identity, label in labels.items():
        duplicates.setdefault(label.casefold(), []).append(identity)
    for identities in duplicates.values():
        if len(identities) > 1:
            for index, identity in enumerate(identities, 1):
                labels[identity] += f' ({index})'
    return labels


@dataclass
class ProcessingState:
    mode: str = 'original'
    smooth: bool = True
    window_length: int = 21
    original_signal_title: str | None = None

    def recipe(self) -> dict:
        return {'mode': self.mode, 'smooth': self.smooth,
                'window_length': self.window_length,
                'original_signal_title': self.original_signal_title or RAW_LABEL}


@dataclass
class FixedRecipe:
    enabled: bool = False
    recipe: dict = field(default_factory=dict)


@dataclass
class ViewState:
    gate: float = 0.0
    colormap: str = 'viridis'
    norm: NormSpec = field(default_factory=NormSpec)
    annotations: dict[str, str] = field(default_factory=lambda: {
        'plot_title': '', 'y_axis_name': '', 'y_axis_unit': '', 'signal_title': RAW_LABEL})
    fix_annotations: bool = True
    signal_title_auto: bool = True
    title_settings: dict = field(default_factory=lambda: manual_settings(''))
    bounds: dict[str, float] = field(default_factory=lambda: dict.fromkeys(BOUND_NAMES, 0.0))
    locks: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(BOUND_NAMES, False))
    x_limits: tuple[float, float] = (0.0, 1.0)
    y_limits: tuple[float, float] = (0.0, 1.0)
    color_limits: tuple[float, float] = (0.0, 1.0)
    color_scale_mode: str = 'global'
    color_region_boundaries: list[float] = field(default_factory=list)
    last_valid_color_limits: tuple[float, float] | None = None
    last_valid_region_color_limits: list[tuple[float, float]] = field(default_factory=list)

    def plot_recipe(self, cube: DataCube, applied: ProcessingState,
                    presentation: PlotPresentation | None = None) -> dict:
        return {'axis_override': cube.x_override, 'colormap': self.colormap,
                'annotations': dict(self.annotations), 'fix_annotations': self.fix_annotations,
                'signal_title_auto': self.signal_title_auto,
                'title_settings': deepcopy(self.title_settings), 'bounds': dict(self.bounds),
                'locks': dict(self.locks), 'x_limits': list(self.x_limits),
                'y_limits': list(self.y_limits), 'color_limits': list(self.color_limits),
                'color_scale_mode': self.color_scale_mode,
                'color_region_boundaries': list(self.color_region_boundaries),
                'color_region_limits': [list(pair) for pair in self.last_valid_region_color_limits],
                'processing': applied.recipe(), 'gate': self.gate,
                'norm': self.norm.to_dict(),
                'presentation': (presentation or PlotPresentation()).to_dict()}


@dataclass
class AnalysisControls:
    derivative_detection: bool = False
    peak_mode: str = 'Peaks'
    prominence: float = 0.05
    distance: int = 5
    max_peaks: int = 6
    max_dips: int = 6
    peak_smooth: bool = False
    peak_smooth_window: int = 11
    d2_mode: str = 'Peaks'
    d2_window: int = 11
    d2_polyorder: int = 3
    d2_prominence: float = 0.03
    d2_distance: int = 3
    d2_separation: float = 0.0
    d2_max_peaks: int = 6
    d2_max_dips: int = 6
    d2_target: int = 0
    fit_xmin: float = 0.0
    fit_xmax: float = 1.0
    fit_xmin_fixed: bool = False
    fit_xmax_fixed: bool = False
    fit_smooth: bool = False
    fit_smooth_window: int = 11
    fit_model: str = 'Lorentzian'
    linewidth_min: int = 0
    linewidth_max: int = 0
    peak_move_limit: int = 0
    maxfev: int = 10000
    csv_source: bool = False


# (record field, window control, accessor, setter). This is an ownership catalog,
# not a dynamic snapshot of arbitrary widgets or window attributes.
ANALYSIS_BINDINGS = (
    ('derivative_detection', '_d2_radio', 'isChecked', 'setChecked'),
    ('peak_mode', 'peak_mode_combo', 'currentText', 'setCurrentText'),
    ('prominence', 'prom_spin', 'value', 'setValue'),
    ('distance', 'distance_spin', 'value', 'setValue'),
    ('max_peaks', 'max_peaks_spin', 'value', 'setValue'),
    ('max_dips', 'max_dips_spin', 'value', 'setValue'),
    ('peak_smooth', 'peak_smooth_chk', 'isChecked', 'setChecked'),
    ('peak_smooth_window', 'peak_smooth_win_spin', 'value', 'setValue'),
    ('d2_mode', 'd2_mode_combo', 'currentText', 'setCurrentText'),
    ('d2_window', 'd2_sg_win_spin', 'value', 'setValue'),
    ('d2_polyorder', 'd2_sg_poly_spin', 'value', 'setValue'),
    ('d2_prominence', 'd2_prom_spin', 'value', 'setValue'),
    ('d2_distance', 'd2_dist_spin', 'value', 'setValue'),
    ('d2_separation', 'd2_sep_spin', 'value', 'setValue'),
    ('d2_max_peaks', 'd2_max_peaks_spin', 'value', 'setValue'),
    ('d2_max_dips', 'd2_max_dips_spin', 'value', 'setValue'),
    ('d2_target', 'd2_target_spin', 'value', 'setValue'),
    ('fit_xmin', 'fit_xmin_spin', 'value', 'setValue'),
    ('fit_xmax', 'fit_xmax_spin', 'value', 'setValue'),
    ('fit_xmin_fixed', 'fit_xmin_lock', 'isChecked', 'setChecked'),
    ('fit_xmax_fixed', 'fit_xmax_lock', 'isChecked', 'setChecked'),
    ('fit_smooth', 'fit_smooth_chk', 'isChecked', 'setChecked'),
    ('fit_smooth_window', 'fit_smooth_win_spin', 'value', 'setValue'),
    ('fit_model', 'fit_model_combo', 'currentText', 'setCurrentText'),
    ('linewidth_min', 'lw_min_spin', 'value', 'setValue'),
    ('linewidth_max', 'lw_max_spin', 'value', 'setValue'),
    ('peak_move_limit', 'peak_move_limit_spin', 'value', 'setValue'),
    ('maxfev', 'maxfev_spin', 'value', 'setValue'),
    ('csv_source', '_seeds_from_csv_radio', 'isChecked', 'setChecked'),
)


@dataclass
class GraphState:
    graph_id: str = 'default'
    name: str = 'Graph 1'
    view: ViewState = field(default_factory=ViewState)
    presentation: PlotPresentation = field(default_factory=PlotPresentation)
    source_mode: str = 'processed'
    gate_indices: list[int] | None = None
    published: bool = True
    analysis: AnalysisDocument | None = None


def validate_graph_source(source_mode, gate_indices, gate_count=None):
    if source_mode not in ('raw', 'processed'):
        raise ValueError('Graph source mode must be raw or processed.')
    if gate_indices is not None:
        if (not isinstance(gate_indices, list) or not gate_indices
                or any(type(index) is not int or index < 0 for index in gate_indices)
                or gate_indices != sorted(set(gate_indices))
                or gate_count is not None and gate_indices[-1] >= gate_count):
            raise ValueError('Graph Gate indices must be nonempty, unique, ordered and within the source.')


def graph_cube(session, graph=None):
    """Read the graph's source and exact sparse Gate selection, without mutation."""
    graph = graph or session.active_graph
    cube = (graph.analysis.science.current_cube if graph.analysis is not None else
            session.base_cube if graph.source_mode == 'raw' else session.current_cube)
    validate_graph_source(graph.source_mode, graph.gate_indices, None if cube is None else cube.gate.size)
    if cube is None or graph.gate_indices is None:
        return cube
    indices = graph.gate_indices
    return replace(cube, gate=cube.gate[indices], z=cube.z[indices])


def graph_signal_label(session, graph=None):
    graph = graph or session.active_graph
    if graph.analysis is not None:
        session = graph.analysis.science
    cube = session.base_cube
    source = cube.signal_source_kind
    if source == 'unknown':
        source = infer_source_signal_kind(cube.source_path or '')
    return automatic_signal_label(source,
        graph.source_mode == 'processed' and session.applied.mode == 'energy_second_derivative',
        cube.signal_kind == 'energy_second_derivative')


def graph_name(name: str, graphs, *, except_id=None) -> str:
    if not isinstance(name, str) or not name.strip() or '\x00' in name:
        raise ValueError('Enter a nonempty graph name.')
    name = name.strip()
    if any(graph.graph_id != except_id and graph.name == name for graph in graphs):
        raise ValueError('A graph with this name already exists in this DAT.')
    return name


def detached_graph_session(session):
    """Borrow science, detach the collection and active visuals for a candidate."""
    candidate = copy(session)
    candidate.graphs = dict(session.graphs)
    graph = copy(session.active_graph)
    graph.view = deepcopy(graph.view)
    graph.presentation = deepcopy(graph.presentation)
    graph.gate_indices = deepcopy(graph.gate_indices)
    # A render/format candidate borrows scientific ownership. Explicit analysis
    # duplication is the operation that copies candidate pools and fit results.
    candidate.graphs[session.active_graph_id] = graph
    return candidate


def graph_export_stem(source_stem, graph_id, name):
    """Windows-safe, graph-distinct basename; scientific provenance stays in metadata."""
    def safe(value):
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', value).strip(' .') or 'graph'
    suffix = '' if graph_id == 'default' else '_' + hashlib.sha256(graph_id.encode('utf-8')).hexdigest()[:12]
    return safe(source_stem) + '_' + safe(name) + suffix


def refresh_graph_colors(graph, cube, *, unlock=False):
    """Retire data-derived caches and refresh automatic endpoints for one graph."""
    view = graph.view
    view.last_valid_color_limits = None
    view.last_valid_region_color_limits = []
    if unlock:
        view.locks['vmin'] = view.locks['vmax'] = False
    xlo, xhi = sorted(view.x_limits)
    ylo, yhi = sorted(view.y_limits)
    values = valid_color_values(cube.z[np.ix_((cube.gate >= ylo) & (cube.gate <= yhi),
                                             (cube.energy >= xlo) & (cube.energy <= xhi))], view.norm)
    candidate = np.percentile(values, [.5, 99.5]) if values.size else view.color_limits
    low = view.bounds['vmin'] if view.locks['vmin'] else float(candidate[0])
    high = view.bounds['vmax'] if view.locks['vmax'] else float(candidate[1])
    if low >= high:
        gap = max(abs(low) * 1e-6, 1e-3)
        if view.norm.kind == 'log' and low > 0:
            gap = min(gap, low / 2)
        if not view.locks['vmin']:
            low = high - gap
        if not view.locks['vmax']:
            high = low + 2 * gap
    if view.norm.kind == 'zero-centered':
        if not view.locks['vmin']:
            low = min(low, -max(abs(high) * 1e-6, 1e-3))
        if not view.locks['vmax']:
            high = max(high, max(abs(low) * 1e-6, 1e-3))
    if not view.locks['vmin']:
        view.bounds['vmin'] = low
    if not view.locks['vmax']:
        view.bounds['vmax'] = high
    view.color_limits = (low, high)


@dataclass
class DatasetSession:
    dataset_id: str = field(default_factory=lambda: uuid4().hex)
    kind: Literal['spectrum_matrix'] = 'spectrum_matrix'
    source_path: Path | None = None
    loaded_fingerprint: str | None = None
    base_cube: DataCube | None = None
    current_cube: DataCube | None = None
    graphs: dict[str, GraphState] = field(default_factory=lambda: {'default': GraphState(published=False)})
    active_graph_id: str = 'default'
    applied: ProcessingState = field(default_factory=ProcessingState)
    pending: ProcessingState = field(default_factory=ProcessingState)
    fixed_recipes: dict[str, FixedRecipe] = field(default_factory=lambda: {
        name: FixedRecipe() for name in ('processing', 'regions', 'region_edges')})
    analysis_controls: AnalysisControls = field(default_factory=AnalysisControls)
    peak_indices: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    peak_gate_idx: int | None = None
    peak_kind_lookup: dict[int, bool] = field(default_factory=dict)
    peak_label_lookup: dict[tuple[int, int], int] = field(default_factory=dict)
    batch_peak_map: dict[int, np.ndarray] = field(default_factory=dict)
    batch_peak_kind_lookup: dict[tuple[int, int], bool] = field(default_factory=dict)
    csv_seeds_map: dict[int, list[float]] | None = None
    csv_seeds_path: str = ''
    fit_result: FitResult | None = None
    fit_gate_idx: int | None = None
    batch_fit_map: dict[int, np.ndarray] = field(default_factory=dict)
    batch_fit_result_map: dict[int, FitResult] = field(default_factory=dict)
    fit_export_rows: list[dict[str, str]] = field(default_factory=list)
    has_batch_fit_results: bool = False
    summary: str = ''
    initialization_notice: str = ''
    sidecar_writes_enabled: bool = True

    @property
    def active_graph(self) -> GraphState:
        return self.graphs[self.active_graph_id]

    @property
    def view(self) -> ViewState:
        return self.active_graph.view

    @view.setter
    def view(self, value):
        self.active_graph.view = value

    @property
    def presentation(self) -> PlotPresentation:
        if self.active_graph.analysis is not None:
            from .analysis_document import graph_presentation
            return graph_presentation(self)
        return self.active_graph.presentation

    @presentation.setter
    def presentation(self, value):
        self.active_graph.presentation = value

    def create_graph(self, name) -> GraphState:
        name = graph_name(name, self.graphs.values())
        if self.active_graph.analysis is not None:
            from .analysis_document import duplicate_analysis_graph
            graph = duplicate_analysis_graph(self)
            graph.name = name
            return graph
        graph = GraphState(uuid4().hex, name, deepcopy(self.view), deepcopy(self.presentation),
                           self.active_graph.source_mode, deepcopy(self.active_graph.gate_indices))
        self.graphs[graph.graph_id] = graph
        self.active_graph_id = graph.graph_id
        return graph

    def activate_graph(self, graph_id):
        if graph_id not in self.graphs:
            raise ValueError('The graph no longer exists.')
        self.active_graph_id = graph_id
        if self.active_graph.analysis is not None:
            from .analysis_document import sync_analysis_format
            sync_analysis_format(self)

    def rename_graph(self, graph_id, name):
        if graph_id not in self.graphs:
            raise ValueError('The graph no longer exists.')
        self.graphs[graph_id].name = graph_name(name, self.graphs.values(), except_id=graph_id)

    def remove_graph(self, graph_id):
        if graph_id not in self.graphs:
            raise ValueError('The graph no longer exists.')
        if len(self.graphs) <= 1:
            raise ValueError('Every DAT must retain at least one graph.')
        from .analysis_document import detach_analysis_sources
        detach_analysis_sources(self, graph_id)
        index = list(self.graphs).index(graph_id)
        removed = self.graphs.pop(graph_id)
        if self.active_graph_id == graph_id:
            self.active_graph_id = list(self.graphs)[min(index, len(self.graphs)-1)]
        return removed

    def plot_collection(self):
        from .plot_state import PlotCollection
        records = []
        for graph in self.graphs.values():
            # Analysis state is portable project content, never a source DAT
            # sidecar that could later reinterpret the result as source science.
            if graph.analysis is not None:
                continue
            recipe = graph.view.plot_recipe(self.current_cube, self.applied, graph.presentation)
            recipe.pop('axis_override')
            recipe.pop('processing')
            records.append({'graph_id': graph.graph_id, 'name': graph.name, 'plot': recipe,
                            'source_mode': graph.source_mode, 'gate_indices': deepcopy(graph.gate_indices),
                            'published': graph.published})
        if not records:
            raise ValueError('Analysis Graphs are saved with Save Project.')
        active_id = self.active_graph_id if any(record['graph_id'] == self.active_graph_id for record in records) else records[0]['graph_id']
        return PlotCollection(axis_override=self.current_cube.x_override,
                              processing=self.applied.recipe(), graphs=records,
                              active_graph_id=active_id)


@dataclass
class Workspace:
    datasets: dict[str, DatasetSession] = field(default_factory=dict)
    path_index: dict[str, str] = field(default_factory=dict)
    active_dataset_id: str | None = None
    # None initializes the default layout for legacy/uninitialized workspaces;
    # an explicit empty record preserves an intentionally all-closed workspace.
    graph_workspace: dict | None = None
    workbooks: dict[str, Workbook] = field(default_factory=dict)
    sheets: dict[str, Sheet] = field(default_factory=dict)
    comparisons: dict[str, Comparison] = field(default_factory=dict)

    @property
    def active(self) -> DatasetSession | None:
        return self.datasets.get(self.active_dataset_id)

    def dataset_for_path(self, path: str | Path) -> DatasetSession | None:
        return self.datasets.get(self.path_index.get(canonical_path(path)))

    def register(self, session: DatasetSession, *, replace_existing: bool = False,
                 as_new_copy: bool = False) -> DatasetSession:
        if session.source_path is None:
            raise ValueError('Registered datasets require a source path')
        if as_new_copy and replace_existing:
            raise ValueError('Choose a new copy or replacement, not both.')
        if as_new_copy and session.dataset_id in self.datasets:
            raise ValueError('A new copy requires an independent dataset identity.')
        key = canonical_path(session.source_path)
        existing = self.dataset_for_path(session.source_path)
        if existing is not None and not as_new_copy:
            if not replace_existing:
                return existing
            session.dataset_id = existing.dataset_id
        elif session.dataset_id in self.datasets:
            raise ValueError('Dataset ID already belongs to a different source')
        self.datasets[session.dataset_id] = session
        # Normal imports deterministically retain the first surviving owner.
        # Explicit copies preserve the real source path and get a fresh identity.
        if existing is None or replace_existing:
            self.path_index[key] = session.dataset_id
        if session.base_cube is not None and session.current_cube is not None:
            ensure_dataset_sheet(self, session.dataset_id)
            reconcile_dataset_sheets(self, session.dataset_id)
        return session

    def remove(self, dataset_id: str) -> DatasetSession:
        """Remove a cached owner and its path, preserving import-order navigation."""
        order = list(self.datasets)
        index = order.index(dataset_id) if dataset_id in self.datasets else None
        session = self.datasets.pop(dataset_id)
        retired_paths = [path for path, owner in self.path_index.items() if owner == dataset_id]
        self.path_index = {path: owner for path, owner in self.path_index.items()
                           if owner != dataset_id}
        # Repoint the existing index key without probing a possibly missing
        # source or network directory during removal.
        if retired_paths and session.source_path is not None:
            replacement = next((retained for retained in self.datasets.values()
                                if retained.source_path is not None and
                                _source_key(retained.source_path) == _source_key(session.source_path)), None)
            if replacement is not None:
                for path in retired_paths:
                    self.path_index[path] = replacement.dataset_id
        remove_dataset_sheets(self, dataset_id)
        if self.active_dataset_id == dataset_id:
            remaining = list(self.datasets)
            self.active_dataset_id = remaining[min(index, len(remaining) - 1)] if remaining else None
        return session


class SessionField:
    """Compatibility descriptor; scientific data is never duplicated on the window."""
    def __init__(self, *path: str):
        self.path = path

    def _target(self, window):
        target = window.workspace.active or window._unregistered_session
        if target.active_graph.analysis is not None:
            from .analysis_document import SCIENCE_FIELDS, analysis_owner
            if self.path[0] in SCIENCE_FIELDS or self.path == ('view', 'gate'):
                target = analysis_owner(target)
        for name in self.path[:-1]:
            target = getattr(target, name)
        return target

    def __get__(self, window, owner=None):
        return self if window is None else getattr(self._target(window), self.path[-1])

    def __set__(self, window, value):
        setattr(self._target(window), self.path[-1], value)


@dataclass
class DatasetDefaults:
    """Detached request-time defaults; reusable for all candidates in one import."""
    view: ViewState
    analysis_controls: AnalysisControls
    fixed_recipes: dict[str, FixedRecipe]
    annotation_defaults: dict[str, str]
    colormap_manual: bool = False


def initialize_dataset(path: Path, prepared: tuple, defaults: DatasetDefaults,
                       dataset_id: str | None = None) -> DatasetSession:
    """Prepare an isolated candidate without changing any window or registry.

    ``prepared`` is the existing prepare_dat result (cube, fingerprint, plot,
    recall_notice). Reads and fingerprints have already finished at this boundary.
    """
    cube, fingerprint, saved_plot, recall_notice = prepared
    from .plot_state import active_plot_recipe
    saved_collection = saved_plot if saved_plot and 'graphs' in saved_plot else None
    saved_plot = active_plot_recipe(saved_plot) if saved_plot else None
    session = DatasetSession(source_path=Path(path), loaded_fingerprint=fingerprint,
                             base_cube=cube, current_cube=cube,
                             graphs={'default': GraphState(view=deepcopy(defaults.view),
                                                           published=bool(saved_plot))},
                             analysis_controls=deepcopy(defaults.analysis_controls),
                             fixed_recipes=deepcopy(defaults.fixed_recipes))
    if dataset_id is not None:
        session.dataset_id = dataset_id
    view = session.view
    source_kind = cube.signal_source_kind if cube.signal_source_kind != 'unknown' else infer_source_signal_kind(cube.source_path or '')
    if not saved_plot and not defaults.colormap_manual:
        if source_kind == 'pl':
            view.colormap = 'jet'
        elif source_kind.startswith('reflection'):
            view.colormap = 'RdBu_r'
    notices = [recall_notice] if recall_notice else []
    processing = (session.fixed_recipes['processing'].recipe
                  if session.fixed_recipes['processing'].enabled
                  else saved_plot.get('processing', {}) if saved_plot else {})
    session.applied = ProcessingState(
        mode='original', smooth=bool(processing.get('smooth', True)),
        window_length=int(processing.get('window_length', 21)),
        original_signal_title=(str(processing.get('original_signal_title', RAW_LABEL))
                               if processing.get('mode') == 'energy_second_derivative' else None))
    if processing.get('mode') == 'energy_second_derivative':
        try:
            if cube.photon_energy is None:
                raise ValueError('Energy 2nd derivative requires genuine photon energy.')
            transformed = energy_second_derivative(
                cube.photon_energy, cube.z, smooth=session.applied.smooth,
                window_length=session.applied.window_length, polyorder=3)
            session.current_cube = replace(cube, z=np.asarray(transformed, dtype=float))
            session.applied.mode = 'energy_second_derivative'
        except (ValueError, TypeError, OverflowError) as exc:
            notices.append(f'Using Original for this file: {exc}')
            session.applied.original_signal_title = None
    session.pending = replace(session.applied)
    view.gate = float(np.nanmedian(cube.gate))
    view.color_scale_mode, view.color_region_boundaries = 'global', []
    view.last_valid_color_limits, view.last_valid_region_color_limits = None, []
    match = re.fullmatch(r'(.*?)\s*\(([^()]*)\)\s*', cube.gate_label)
    name, unit = match.groups() if match else (cube.gate_label, '')
    view.annotations = {'plot_title': '', 'y_axis_name': name, 'y_axis_unit': unit,
                        'signal_title': RAW_LABEL}
    if view.fix_annotations:
        view.annotations.update(defaults.annotation_defaults)
    if view.title_settings.get('mode') == 'template':
        try:
            view.annotations['plot_title'] = render_title(cube.source_path or '', view.title_settings['preset'])
        except ValueError as exc:
            view.annotations['plot_title'] = ''
            notices.append(str(exc))
    for name, value in (('xmin', np.nanmin(cube.energy)), ('xmax', np.nanmax(cube.energy)),
                        ('ymin', np.nanmin(cube.gate)), ('ymax', np.nanmax(cube.gate))):
        if not view.locks[name]:
            view.bounds[name] = float(value)
    controls = session.analysis_controls
    if not controls.fit_xmin_fixed:
        controls.fit_xmin = float(np.nanmin(cube.energy))
    if not controls.fit_xmax_fixed:
        controls.fit_xmax = float(np.nanmax(cube.energy))
    controls.csv_source = False
    if saved_plot:
        view.norm = NormSpec.from_dict(saved_plot.get('norm', {}))
        session.presentation = PlotPresentation.from_dict(saved_plot.get('presentation', {}))
        view.last_valid_region_color_limits = [tuple(pair) for pair in saved_plot.get('color_region_limits', [])]
        for name in ('colormap', 'annotations', 'fix_annotations', 'signal_title_auto',
                     'title_settings', 'bounds', 'locks', 'color_scale_mode', 'color_region_boundaries'):
            setattr(view, name, deepcopy(saved_plot[name]))
        for name in ('x_limits', 'y_limits', 'color_limits'):
            setattr(view, name, tuple(saved_plot[name]))
        view.gate = float(cube.gate[int(np.argmin(np.abs(cube.gate - saved_plot['gate'])))])
    else:
        view.x_limits = (view.bounds['xmin'], view.bounds['xmax'])
        view.y_limits = (view.bounds['ymin'], view.bounds['ymax'])
    if session.fixed_recipes['regions'].enabled:
        view.color_scale_mode = session.fixed_recipes['regions'].recipe.get('mode', 'global')
    edges = session.fixed_recipes['region_edges']
    if edges.enabled and len(edges.recipe.get('boundaries', [])) == (
            int(view.color_scale_mode) - 1 if view.color_scale_mode in ('2', '3') else 0):
        view.color_region_boundaries = list(edges.recipe.get('boundaries', []))
    refresh_colors = any(item.enabled for item in session.fixed_recipes.values())
    if refresh_colors:
        view.locks['vmin'] = view.locks['vmax'] = False
    if not saved_plot or refresh_colors:
        xlo, xhi = sorted(view.x_limits)
        ylo, yhi = sorted(view.y_limits)
        xmask = (cube.energy >= xlo) & (cube.energy <= xhi)
        ymask = (cube.gate >= ylo) & (cube.gate <= yhi)
        values = session.current_cube.z[np.ix_(ymask, xmask)]
        values = valid_color_values(values, view.norm)
        candidate_low, candidate_high = (np.nanpercentile(values, [0.5, 99.5])
                                        if values.size else
                                        (view.bounds['vmin'], view.bounds['vmax']))
        low_fixed, high_fixed = view.locks['vmin'], view.locks['vmax']
        low = view.bounds['vmin'] if low_fixed else float(candidate_low)
        high = view.bounds['vmax'] if high_fixed else float(candidate_high)
        # Preserve the same single-bound Fix semantics as live auto colors:
        # only move the unlocked endpoint when the new range crosses the lock.
        if not low_fixed and not high_fixed and low == high:
            pad = max(abs(low)*1e-6, 1e-3)
            if view.norm.kind == 'log':
                pad = min(pad, low / 2)
            low, high = low-pad, high+pad
        elif low_fixed and not high_fixed and high <= low:
            high = low + max(abs(low)*1e-6, 1e-3)
        elif high_fixed and not low_fixed and low >= high:
            low = high - max(abs(high)*1e-6, 1e-3)
        if view.norm.kind == 'zero-centered':
            if not low_fixed:
                low = min(low, -max(abs(high)*1e-6, 1e-3))
            if not high_fixed:
                high = max(high, max(abs(low)*1e-6, 1e-3))
        for name, value in (('vmin', low), ('vmax', high)):
            if not view.locks[name]:
                view.bounds[name] = float(value)
        view.color_limits = tuple(sorted((view.bounds['vmin'], view.bounds['vmax'])))
    if view.signal_title_auto:
        view.annotations['signal_title'] = automatic_signal_label(
            source_kind, session.applied.mode == 'energy_second_derivative', cube.signal_kind == 'energy_second_derivative')
    if saved_collection:
        graphs = {}
        for record in saved_collection['graphs']:
            graph_view = view if record['graph_id'] == saved_collection['active_graph_id'] else deepcopy(view)
            recipe = record['plot']
            if record['graph_id'] != saved_collection['active_graph_id']:
                for name in ('colormap', 'annotations', 'fix_annotations', 'signal_title_auto',
                             'title_settings', 'bounds', 'locks', 'color_scale_mode', 'color_region_boundaries'):
                    setattr(graph_view, name, deepcopy(recipe[name]))
                for name in ('x_limits', 'y_limits', 'color_limits'):
                    setattr(graph_view, name, tuple(recipe[name]))
                graph_view.norm = NormSpec.from_dict(recipe['norm'])
                graph_view.gate = float(cube.gate[int(np.argmin(np.abs(cube.gate - recipe['gate'])))])
                graph_view.last_valid_color_limits = None
                graph_view.last_valid_region_color_limits = [tuple(pair) for pair in recipe['color_region_limits']]
                if graph_view.signal_title_auto:
                    graph_view.annotations['signal_title'] = automatic_signal_label(
                        source_kind, session.applied.mode == 'energy_second_derivative',
                        cube.signal_kind == 'energy_second_derivative')
                if refresh_colors:
                    graph_view.last_valid_region_color_limits = []
            graphs[record['graph_id']] = GraphState(record['graph_id'], record['name'], graph_view,
                PlotPresentation.from_dict(recipe['presentation']), record.get('source_mode', 'processed'),
                deepcopy(record.get('gate_indices')), record.get('published', True))
            graph = graphs[record['graph_id']]
            selected_cube = graph_cube(session, graph)
            if graph_view.signal_title_auto:
                graph_view.annotations['signal_title'] = graph_signal_label(session, graph)
            if refresh_colors:
                if session.fixed_recipes['regions'].enabled:
                    graph_view.color_scale_mode = session.fixed_recipes['regions'].recipe.get('mode', 'global')
                if edges.enabled and len(edges.recipe.get('boundaries', [])) == (
                        int(graph_view.color_scale_mode) - 1 if graph_view.color_scale_mode in ('2', '3') else 0):
                    graph_view.color_region_boundaries = list(edges.recipe.get('boundaries', []))
                refresh_graph_colors(graph, selected_cube, unlock=True)
        session.graphs = graphs
        session.active_graph_id = saved_collection['active_graph_id']
    session.summary = (f'Loaded: {path.name}\nDetected x-axis: {cube.x_original_label}\n'
                       f'Analysis axis: {cube.energy_label}\n'
                       'Use Find Peaks tab to detect peaks, then Lorentz Fit tab to fit.\n'
                       'Drag-and-drop a .dat or .csv file onto the window as an alternative to browse.')
    session.initialization_notice = ' '.join(notices)
    return session
