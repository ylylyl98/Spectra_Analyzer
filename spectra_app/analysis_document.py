"""Independent, source-associated analysis documents; no widget or runtime job state.

The parent DatasetSession remains the workspace owner. Each result graph owns a
detached DatasetSession containing the bound scientific input and results. A job
retains that owner and ``job_token``; switching graphs never swaps shared science.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields, replace
import math
from typing import TYPE_CHECKING
from uuid import uuid4

import numpy as np

from .plot_presentation import ScatterStyle

if TYPE_CHECKING:
    from .dataset_state import DatasetSession
    from .peak_candidates import CandidateFilter, PeakCandidate


def _filter_default():
    from .peak_candidates import candidate_filter_preset
    return candidate_filter_preset('Balanced')


@dataclass(frozen=True)
class AnalysisSeed:
    position: float
    kind: str = 'peak'
    enabled: bool = True
    manual: bool = False
    source_index: int | None = None
    amplitude: float | None = None
    width: float | None = None
    gamma: float | None = None
    fixed_center: bool = False
    center_min: float | None = None
    center_max: float | None = None
    seed_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self):
        if self.kind not in ('peak', 'dip'):
            raise ValueError('Seed kind must be peak or dip.')
        if not isinstance(self.seed_id, str) or not self.seed_id.strip() or '\x00' in self.seed_id:
            raise ValueError('Seed ID must be a nonempty string.')
        for name in ('enabled', 'manual', 'fixed_center'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'Seed {name} must be boolean.')
        if self.source_index is not None and (type(self.source_index) is not int or self.source_index < 0):
            raise ValueError('Seed source index must be nonnegative.')
        for name in ('position', 'amplitude', 'width', 'gamma', 'center_min', 'center_max'):
            value = getattr(self, name)
            if value is None and name != 'position':
                continue
            if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)) or not math.isfinite(value):
                raise ValueError(f'Seed {name} must be finite.')
            object.__setattr__(self, name, float(value))
        if self.width is not None and self.width <= 0 or self.gamma is not None and self.gamma <= 0:
            raise ValueError('Seed widths must be positive.')
        if self.amplitude is not None and (self.amplitude == 0 or (self.amplitude > 0) != (self.kind == 'peak')):
            raise ValueError('Seed amplitude sign must match peak/dip kind.')
        if self.center_min is not None and self.position < self.center_min or self.center_max is not None and self.position > self.center_max:
            raise ValueError('Seed center must be inside its bounds.')
        if self.center_min is not None and self.center_max is not None and self.center_min >= self.center_max:
            raise ValueError('Seed center bounds must be increasing.')

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {item.name for item in fields(cls)}:
            raise ValueError('Invalid analysis seed fields.')
        return cls(**value)


def default_overlay_styles():
    return {
        'detected-peak': ScatterStyle(color='#d62728', marker='o'),
        'detected-dip': ScatterStyle(color='#17becf', marker='v'),
        'fitted-peak': ScatterStyle(color='#ffbf00', marker='+', size=6),
        'fitted-dip': ScatterStyle(color='#e377c2', marker='x', size=6),
    }


GATE_STATUSES = ('pending', 'detected', 'empty', 'no-seed', 'fitted', 'failed')


@dataclass
class AnalysisDocument:
    source_graph_id: str | None
    science: DatasetSession
    input_version: str = field(default_factory=lambda: uuid4().hex)
    input_label: str = ''
    source_mode: str = 'processed'
    gate_scope: str = 'all'
    candidate_pools: dict[int, list[PeakCandidate]] = field(default_factory=dict)
    filters: CandidateFilter = field(default_factory=_filter_default)
    filters_by_gate: dict[int, CandidateFilter] = field(default_factory=dict)
    seeds_by_gate: dict[int, list[AnalysisSeed]] = field(default_factory=dict)
    gate_status: dict[int, str] = field(default_factory=dict)
    gate_messages: dict[int, str] = field(default_factory=dict)
    overlay_styles: dict[str, ScatterStyle] = field(default_factory=default_overlay_styles)
    revision: int = 0

    def __post_init__(self):
        if self.source_mode not in ('raw', 'processed') or self.gate_scope not in ('current', 'all'):
            raise ValueError('Invalid analysis input mode or Gate scope.')

    @property
    def job_token(self):
        return self.input_version, self.revision

    def _gate(self, gate):
        if type(gate) is not int or not 0 <= gate < self.science.current_cube.gate.size:
            raise ValueError('Analysis Gate is outside its bound input.')
        return gate

    def accepted_seeds(self, gate):
        return [seed for seed in self.seeds_by_gate.get(self._gate(gate), ()) if seed.enabled]

    def retire_fit(self, gate=None):
        owner = self.science
        gates = set(owner.batch_fit_result_map) | set(owner.batch_fit_map)
        if owner.fit_gate_idx is not None:
            gates.add(owner.fit_gate_idx)
        if gate is not None:
            gates = {self._gate(gate)}
        for index in gates:
            owner.batch_fit_map.pop(index, None)
            owner.batch_fit_result_map.pop(index, None)
            if owner.fit_gate_idx == index:
                owner.fit_result, owner.fit_gate_idx = None, None
            if self.gate_status.get(index) == 'fitted':
                self.gate_status[index] = 'detected' if self.accepted_seeds(index) else 'no-seed'
        owner.has_batch_fit_results = bool(owner.batch_fit_result_map)
        # CSV rows mix status and fit data; rebuild them after the next fit.
        owner.fit_export_rows = []

    def set_seeds(self, gate, seeds):
        gate = self._gate(gate)
        seeds = list(seeds)
        if any(not isinstance(seed, AnalysisSeed) for seed in seeds) or len({seed.seed_id for seed in seeds}) != len(seeds):
            raise ValueError('Analysis seeds require unique validated records.')
        columns = self.science.current_cube.energy.size
        if any(seed.source_index is not None and seed.source_index >= columns for seed in seeds):
            raise ValueError('Seed source index is outside its bound input.')
        changed = self.seeds_by_gate.get(gate) != seeds
        if changed:
            self.retire_fit(gate)
            self.revision += 1
        self.seeds_by_gate[gate] = seeds
        if changed:
            self.gate_status[gate] = 'detected' if any(seed.enabled for seed in seeds) else 'no-seed'
        self._sync_legacy_peaks(gate)

    def _sync_legacy_peaks(self, gate):
        """Legacy linecut labels remain auxiliary; formal markers use exact seeds."""
        owner = self.science
        accepted = self.accepted_seeds(gate)
        x = owner.current_cube.energy
        located = [(seed.source_index if seed.source_index is not None and x[seed.source_index] == seed.position
                    else int(np.argmin(np.abs(x-seed.position))), seed) for seed in accepted]
        indices = np.array(sorted({index for index, seed in located}), dtype=int)
        owner.batch_peak_map[gate] = indices
        owner.batch_peak_kind_lookup = {key: value for key, value in owner.batch_peak_kind_lookup.items() if key[0] != gate}
        for index, seed in located:
            owner.batch_peak_kind_lookup[(gate, index)] = seed.kind == 'peak'
        if owner.peak_gate_idx == gate or int(np.argmin(np.abs(owner.current_cube.gate-owner.view.gate))) == gate:
            owner.peak_indices, owner.peak_gate_idx = indices.copy(), gate
            owner.peak_kind_lookup = {index: owner.batch_peak_kind_lookup[(gate, int(index))] for index in indices}

    def set_candidates(self, gate, pool, settings=None):
        from .peak_candidates import PeakCandidate
        gate = self._gate(gate)
        pool = list(pool)
        columns = self.science.current_cube.energy.size
        if any(not isinstance(item, PeakCandidate) or not 0 <= item.index < columns for item in pool):
            raise ValueError('Candidates must reference the bound input.')
        if len({(item.index, item.kind) for item in pool}) != len(pool):
            raise ValueError('Candidate pool contains duplicate peak/dip identities.')
        cube = self.science.current_cube
        if any(item.position != cube.energy[item.index] or item.amplitude != cube.z[gate, item.index] for item in pool):
            raise ValueError('Candidates must describe exact bound source samples.')
        # A new detection operation supersedes the previous fit, even if its
        # candidate identities happen to be unchanged.
        self.retire_fit(gate)
        self.revision += 1
        self.candidate_pools[gate] = pool
        self.apply_filter(settings or self.filters_by_gate.get(gate, self.filters), gate)
        if not pool and not self.accepted_seeds(gate):
            self.gate_status[gate] = 'empty'
        else:
            self.gate_status[gate] = 'detected' if self.accepted_seeds(gate) else 'no-seed'

    def apply_filter(self, settings, gate=None):
        from .peak_candidates import CandidateFilter, filter_peak_candidates
        if not isinstance(settings, CandidateFilter):
            raise ValueError('Analysis filtering requires CandidateFilter.')
        self.filters = settings
        gates = list(self.candidate_pools) if gate is None else [self._gate(gate)]
        for index in gates:
            self.filters_by_gate[index] = settings
            pool = self.candidate_pools.get(index, [])
            accepted = {(item.index, item.kind) for item in filter_peak_candidates(pool, settings)}
            old = {(seed.source_index, seed.kind): seed for seed in self.seeds_by_gate.get(index, ()) if seed.source_index is not None}
            seeds = []
            for item in pool:
                key = item.index, item.kind
                previous = old.get(key)
                # Explicit manual accept/exclude/move survives later filters.
                seed = previous if previous is not None and previous.manual else AnalysisSeed(
                    position=item.position, kind=item.kind, enabled=key in accepted,
                    source_index=item.index, seed_id=previous.seed_id if previous else uuid4().hex)
                seeds.append(seed)
            represented = {(item.index, item.kind) for item in pool}
            seeds.extend(seed for seed in self.seeds_by_gate.get(index, ()) if seed.manual
                         and (seed.source_index, seed.kind) not in represented)
            self.set_seeds(index, seeds)

    def record_fit(self, gate, fit, *, status='fitted', message=''):
        from .fitting import FitResult
        gate = self._gate(gate)
        if status not in GATE_STATUSES or not isinstance(message, str):
            raise ValueError('Invalid analysis fit outcome.')
        if fit is not None and not isinstance(fit, FitResult) or (status == 'fitted') != (fit is not None):
            raise ValueError('A fitted Gate requires a FitResult.')
        self.retire_fit(gate)
        if fit is not None:
            owner = self.science
            owner.batch_fit_result_map[gate] = fit
            owner.batch_fit_map[gate] = fit.centers.copy()
            owner.fit_result, owner.fit_gate_idx = fit, gate
            owner.has_batch_fit_results = True
        self.gate_status[gate], self.gate_messages[gate] = status, message


def analysis_owner(session, graph=None):
    graph = graph or session.active_graph
    return graph.analysis.science if graph.analysis is not None else session


def source_graph(session, graph=None):
    graph = graph or session.active_graph
    if graph.analysis is None or graph.analysis.source_graph_id is None:
        return graph
    source = session.graphs.get(graph.analysis.source_graph_id)
    if source is None or source.analysis is not None:
        raise ValueError('Analysis source must be an existing ordinary graph.')
    return source


def graph_format_view(session, graph=None):
    return source_graph(session, graph).view


def graph_presentation(session, graph=None):
    return source_graph(session, graph).presentation


def sync_analysis_format(session, graph=None):
    """Refresh compatibility visual getters, retaining independent current Gate."""
    graph = graph or session.active_graph
    if graph.analysis is not None:
        source = source_graph(session, graph)
        if source is not graph:
            graph.view = deepcopy(source.view)
            graph.presentation = deepcopy(source.presentation)
        graph.view.gate = graph.analysis.science.view.gate
    return graph


def _next_name(session, prefix):
    names = {graph.name for graph in session.graphs.values()}
    name, index = prefix, 2
    while name in names:
        name = f'{prefix} {index}'
        index += 1
    return name


def _bound_science(session, source, mode):
    from .dataset_state import DatasetSession, GraphState, ProcessingState
    result = session.processing_results.get(source.result_id)
    cube = session.base_cube if mode == 'raw' else result.cube if result is not None else session.current_cube
    if cube is None:
        raise ValueError('Load a source before creating an Analysis Graph.')
    cube = deepcopy(cube)
    graph = GraphState(name='Bound analysis input', view=deepcopy(source.view), presentation=deepcopy(source.presentation))
    applied = deepcopy(result.processing if result is not None else session.applied) if mode == 'processed' else ProcessingState()
    return DatasetSession(source_path=session.source_path, loaded_fingerprint=session.loaded_fingerprint,
        base_cube=cube, current_cube=cube, graphs={'default': graph},
        applied=applied, pending=deepcopy(applied), analysis_controls=deepcopy(session.analysis_controls),
        sidecar_writes_enabled=False)


def ensure_analysis_graph(session, source_graph_id=None, *, duplicate=False):
    from .dataset_state import GraphState
    graph = session.graphs[source_graph_id or session.active_graph_id]
    if graph.analysis is not None:
        if duplicate:
            return duplicate_analysis_graph(session, graph.graph_id)
        session.activate_graph(graph.graph_id)
        return sync_analysis_format(session, graph)
    if not duplicate:
        for candidate in session.graphs.values():
            if candidate.analysis is not None and candidate.analysis.source_graph_id == graph.graph_id:
                session.activate_graph(candidate.graph_id)
                return sync_analysis_format(session, candidate)
    science = _bound_science(session, graph, graph.source_mode)
    document = AnalysisDocument(graph.graph_id, science, source_mode=graph.source_mode,
        input_label=f'{graph.name} / {"Original" if graph.source_mode == "raw" else "Processed"} / bound input')
    result = GraphState(uuid4().hex, _next_name(session, f'Analysis — {graph.name}'),
        deepcopy(graph.view), deepcopy(graph.presentation), graph.source_mode,
        deepcopy(graph.gate_indices), True, document)
    session.graphs[result.graph_id] = result
    session.activate_graph(result.graph_id)
    return result


def duplicate_analysis_graph(session, graph_id=None):
    graph = session.graphs[graph_id or session.active_graph_id]
    if graph.analysis is None:
        return ensure_analysis_graph(session, graph.graph_id, duplicate=True)
    sync_analysis_format(session, graph)
    result = deepcopy(graph)
    result.graph_id = uuid4().hex
    result.name = _next_name(session, f'{graph.name} copy')
    result.analysis.input_version = uuid4().hex
    result.analysis.science.dataset_id = uuid4().hex
    session.graphs[result.graph_id] = result
    session.activate_graph(result.graph_id)
    return result


_KEEP_GATES = object()


def analysis_input_changed(session, graph=None, *, source_mode=None, gate_indices=_KEEP_GATES):
    """Compare the proposed input to the frozen snapshot, including its recipe.

    Display formatting is deliberately outside this comparison. Array equality
    also handles embedded/reopened sources whose runtime identity has changed.
    """
    from .dataset_state import ProcessingState, validate_graph_source
    graph = graph or session.active_graph
    if graph.analysis is None:
        raise ValueError('Choose an Analysis Graph before changing its input.')
    source = source_graph(session, graph)
    if source is graph:
        raise ValueError('The source graph was removed; retain this bound input or create another analysis.')
    doc = graph.analysis
    mode = doc.source_mode if source_mode is None else source_mode
    selected = graph.gate_indices if gate_indices is _KEEP_GATES else gate_indices
    result = session.processing_results.get(source.result_id)
    cube = session.base_cube if mode == 'raw' else result.cube if result is not None else session.current_cube
    validate_graph_source(mode, selected, cube.gate.size)
    applied = ProcessingState() if mode == 'raw' else result.processing if result is not None else session.applied
    if (mode != doc.source_mode or selected != graph.gate_indices
            or applied.recipe() != doc.science.applied.recipe()
            or session.loaded_fingerprint != doc.science.loaded_fingerprint):
        return True
    bound = doc.science.current_cube
    for descriptor in fields(cube):
        current, previous = getattr(cube, descriptor.name), getattr(bound, descriptor.name)
        if isinstance(current, np.ndarray) or isinstance(previous, np.ndarray):
            if current is None or previous is None or not np.array_equal(current, previous, equal_nan=True):
                return True
        elif current != previous:
            return True
    return False


def rebind_analysis_input(session, graph=None, *, source_mode=None, gate_indices=_KEEP_GATES):
    """Explicitly capture a new input version and retire all previous results."""
    from .dataset_state import validate_graph_source
    graph = graph or session.active_graph
    if graph.analysis is None:
        raise ValueError('Choose an Analysis Graph before changing its input.')
    source = source_graph(session, graph)
    if source is graph:
        raise ValueError('The source graph was removed; retain this bound input or create another analysis.')
    mode = graph.analysis.source_mode if source_mode is None else source_mode
    if gate_indices is _KEEP_GATES:
        gate_indices = graph.gate_indices
    validate_graph_source(mode, gate_indices, session.current_cube.gate.size)
    old = graph.analysis
    if not analysis_input_changed(session, graph, source_mode=mode, gate_indices=gate_indices):
        return old
    graph.analysis = AnalysisDocument(old.source_graph_id, _bound_science(session, source, mode),
        input_label=f'{source.name} / {"Original" if mode == "raw" else "Processed"} / bound input',
        source_mode=mode, gate_scope=old.gate_scope, filters=old.filters, overlay_styles=deepcopy(old.overlay_styles))
    graph.analysis.science.analysis_controls = deepcopy(old.science.analysis_controls)
    gates = graph.analysis.science.current_cube.gate
    graph.analysis.science.view.gate = float(gates[np.argmin(np.abs(gates-old.science.view.gate))])
    graph.source_mode, graph.gate_indices = mode, deepcopy(gate_indices)
    return graph.analysis


def analysis_parameter_rows(document):
    """All bound Gates and seeds, with fits paired by participating seed identity."""
    import json
    columns = ['Gate', 'Status', 'Kind', 'Seed X', 'Fit X', 'HWHM', 'Sigma', 'Gamma',
        'Amplitude', 'Baseline', 'RMSE', 'R-squared', 'Model', 'Enabled', 'Seed ID',
        'Initial amplitude', 'Initial width', 'Initial gamma', 'Fixed center',
        'Center min', 'Center max', 'Message', 'Input version', 'Source path',
        'Source graph', 'Input mode', 'Processing recipe', 'X label']
    science = document.science
    controls, cube = science.analysis_controls, science.current_cube
    rows, gates = [], []
    for gate, value in enumerate(cube.gate):
        fit = science.batch_fit_result_map.get(gate)
        participating = [s for s in document.accepted_seeds(gate)
                         if controls.fit_xmin <= s.position <= controls.fit_xmax]
        indices = {s.seed_id: index for index, s in enumerate(participating)}
        for seed in document.seeds_by_gate.get(gate, []) or [None]:
            index = indices.get(seed.seed_id) if seed else None
            fitted = fit is not None and index is not None and index < len(fit.centers)
            model = fit.model if fit is not None else controls.fit_model
            initial = (lambda field: getattr(seed, field) if seed is not None else None)
            rows.append([float(value), document.gate_status.get(gate, 'pending'),
                initial('kind'), initial('position'), fit.centers[index] if fitted else None,
                fit.widths[index] if fitted and model == 'Lorentzian' else None,
                fit.widths[index] if fitted and model in ('Gaussian', 'Voigt') else None,
                fit.gammas[index] if fitted and fit.gammas is not None else None,
                fit.amplitudes[index] if fitted else None,
                fit.baseline if fitted else None, fit.rmse if fitted else None,
                fit.r_squared if fitted else None, model, initial('enabled'), initial('seed_id'),
                initial('amplitude'), initial('width'), initial('gamma'), initial('fixed_center'),
                initial('center_min'), initial('center_max'), document.gate_messages.get(gate, ''),
                document.input_version, str(science.source_path or ''), document.source_graph_id or '',
                document.source_mode, json.dumps(science.applied.recipe(), ensure_ascii=False, sort_keys=True), cube.energy_label])
            gates.append(gate)
    return columns, rows, gates


def detach_analysis_sources(session, source_graph_id):
    for graph in session.graphs.values():
        if graph.analysis is not None and graph.analysis.source_graph_id == source_graph_id:
            sync_analysis_format(session, graph)
            graph.analysis.source_graph_id = None


# Fields used by the compatibility descriptors. View formatting still belongs
# to the graph; current Gate belongs to its independent analysis editor.
SCIENCE_FIELDS = frozenset(('base_cube', 'current_cube', 'applied', 'pending', 'analysis_controls',
    'peak_indices', 'peak_gate_idx', 'peak_kind_lookup', 'peak_label_lookup', 'batch_peak_map',
    'batch_peak_kind_lookup', 'csv_seeds_map', 'csv_seeds_path', 'fit_result', 'fit_gate_idx',
    'batch_fit_map', 'batch_fit_result_map', 'fit_export_rows', 'has_batch_fit_results', 'summary'))
