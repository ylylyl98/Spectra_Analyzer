"""Qt-free workbook ownership, numeric projections and live comparison curves."""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
from uuid import uuid4

import numpy as np

from .plot_presentation import PlotPresentation
from .signal_titles import automatic_signal_label, infer_source_signal_kind


def _id():
    return uuid4().hex


@dataclass
class Workbook:
    workbook_id: str = field(default_factory=_id)
    name: str = 'Workbook'
    sheet_ids: list[str] = field(default_factory=list)


@dataclass
class Sheet:
    sheet_id: str = field(default_factory=_id)
    name: str = 'Sheet'
    kind: str = 'xy'
    source_path: Path | None = None
    data: np.ndarray | None = None
    column_names: list[str] = field(default_factory=list)
    dataset_id: str | None = None
    x_column: int = 0
    y_columns: list[int] = field(default_factory=lambda: [1])
    mode: str = 'raw'
    role: str = 'original'
    fit_gate_idx: int | None = None
    text_columns: dict[int, list[str]] = field(default_factory=dict)
    snapshot_note: str = ''
    result_id: str | None = None
    column_metadata: dict[int, dict[str, str]] = field(default_factory=dict)


@dataclass
class Curve:
    curve_id: str = field(default_factory=_id)
    sheet_id: str = ''
    x_column: int = 0
    y_column: int = 1
    mode: str = 'raw'
    label: str = ''
    visible: bool = True
    color: str = '#1f77b4'
    width: float = 1.5
    y_offset: float = 0.0
    style: str = 'line'
    marker_shape: str = 'ball'
    marker_size: float = 5.0
    marker_fill: str = 'full'
    marker_edge_color: str = 'auto'
    marker_edge_width: float = 1.0


@dataclass
class Comparison:
    comparison_id: str = field(default_factory=_id)
    name: str = 'Comparison'
    x_label: str = 'X'
    y_label: str = 'Y'
    legend_visible: bool = True
    curves: dict[str, Curve] = field(default_factory=dict)
    normalization: str = 'none'
    stack_step: float = 0.0
    x_range_mode: str = 'all'
    x_range: tuple[float, float] | None = None
    plot_title: str = ''
    analysis_state: dict | None = None
    presentation: PlotPresentation | None = None


_COLORS = ('#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f')


def _text(value, name, *, nonempty=True):
    if not isinstance(value, str) or '\x00' in value or nonempty and not value.strip():
        raise ValueError(f'{name} must be a valid {"nonempty " if nonempty else ""}string.')
    return value


def _name(value):
    return _text(value, 'Name').strip()


def _source_key(path):
    # Provenance comparison must never probe a source or its parent directories.
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def workbook_sources(workspace, book):
    """Unique provenance paths in Sheet order, without probing the filesystem."""
    sources = {}
    for identity in book.sheet_ids:
        path = workspace.sheets[identity].source_path
        if path is not None:
            sources.setdefault(_source_key(path), path)
    return list(sources.values())


def workbook_display_labels(workspace):
    """Disambiguate presentation only; ownership and user names stay unchanged."""
    groups, result = {}, {}
    for book in workspace.workbooks.values():
        groups.setdefault(book.name.casefold(), []).append(book)
    for books in groups.values():
        paths = {book.workbook_id: workbook_sources(workspace, book) for book in books}
        for book in books:
            label = book.name
            sources = paths[book.workbook_id]
            if len(books) == 1:
                if len(sources) > 1:
                    label += f' · {len(sources)} sources'
                elif not sources:
                    label += f' · {len(book.sheet_ids)} embedded Sheets'
            if len(books) > 1:
                if len(sources) == 1:
                    path = sources[0]
                    peers = [values[0] for identity, values in paths.items()
                             if identity != book.workbook_id and len(values) == 1]
                    parts = path.parent.parts
                    suffix = str(path.parent)
                    for depth in range(1, len(parts) + 1):
                        tail = tuple(part.casefold() for part in parts[-depth:])
                        if all(tuple(part.casefold() for part in other.parent.parts[-depth:]) != tail
                               for other in peers):
                            suffix = str(Path(*parts[-depth:]))
                            break
                    else:
                        suffix = str(path)
                    label += ' — ' + suffix
                else:
                    label += f' — {len(sources)} sources' if sources else f' — {len(book.sheet_ids)} embedded Sheets'
            result[book.workbook_id] = label
        duplicate_labels = {}
        for book in books:
            duplicate_labels.setdefault(result[book.workbook_id].casefold(), []).append(book)
        for duplicates in duplicate_labels.values():
            if len(duplicates) > 1:
                for index, book in enumerate(duplicates, 1):
                    result[book.workbook_id] += f' ({index})'
    return result


def _sheet(workspace, sheet):
    return workspace.sheets[sheet] if isinstance(sheet, str) else sheet


def _cube(workspace, sheet, mode):
    mode = sheet.mode if mode is None else mode
    if mode not in ('raw', 'processed') or sheet.kind != 'dat' and mode != 'raw':
        raise ValueError('Ordinary XY Sheets support raw data only; DAT supports raw/processed.')
    if sheet.kind != 'dat' or sheet.role in ('fit_parameters', 'fit_curves'):
        return None
    session = workspace.datasets.get(sheet.dataset_id)
    if session is None:
        raise ValueError('DAT Sheet references a missing dataset.')
    cube = (session.processing_results[sheet.result_id].cube if sheet.result_id is not None and mode == 'processed'
            else session.base_cube if mode == 'raw' else session.current_cube)
    if cube is None:
        raise ValueError('DAT Sheet requires an available scientific cube.')
    return cube


_PARAMETER_COLUMNS = ['Gate', 'Model', 'Status', 'Peak', 'Center', 'Amplitude', 'Width', 'Baseline', 'RMSE', 'R-squared']


def _retained_fits(session):
    results = dict(session.batch_fit_result_map)
    if not results and session.fit_result is not None and session.fit_gate_idx is not None:
        results[session.fit_gate_idx] = session.fit_result
    return results


def _parameter_values(session):
    rows = []
    retained_gates = set()
    for gate, result in sorted(_retained_fits(session).items()):
        if not 0 <= gate < session.current_cube.gate.size:
            continue
        value = float(session.current_cube.gate[gate])
        retained_gates.add(f'{value:.10g}')
        for peak in range(len(result.centers)):
            rows.append([value, result.model, 'ok', peak + 1, result.centers[peak],
                         result.amplitudes[peak], result.widths[peak], result.baseline,
                         result.rmse, result.r_squared])
    for record in session.fit_export_rows:
        if record.get('gate') in retained_gates:
            continue
        try:
            gate = float(record['gate'])
        except (KeyError, ValueError):
            continue
        # Rounded export values describe status only, never successful parameters.
        rows.append([gate, record.get('model', ''), record.get('status', ''), *([np.nan] * 7)])
    return rows


def sheet_shape(workspace, sheet, mode=None):
    sheet = _sheet(workspace, sheet)
    cube = _cube(workspace, sheet, mode)
    if sheet.role == 'fit_parameters':
        return len(_parameter_values(workspace.datasets[sheet.dataset_id])), len(_PARAMETER_COLUMNS)
    if sheet.role == 'fit_curves':
        result = _retained_fits(workspace.datasets[sheet.dataset_id]).get(sheet.fit_gate_idx)
        return (0 if result is None else result.x.size, len(sheet.column_names))
    return sheet.data.shape if cube is None else (cube.energy.size, cube.gate.size + 1)


def _source_sheet_columns(workspace, sheet, mode=None):
    sheet = _sheet(workspace, sheet)
    cube = _cube(workspace, sheet, mode)
    if sheet.role == 'fit_parameters':
        return list(_PARAMETER_COLUMNS)
    return list(sheet.column_names) if cube is None else [cube.energy_label] + [
        f'{cube.gate_label} = {gate:g}' for gate in cube.gate]


_LABEL_UNITS = frozenset(('eV', 'meV', 'keV', 'nm', 'µm', 'um', 'm', 'cm', 'mm', 's', 'ms',
    'µs', 'us', 'Hz', 'kHz', 'MHz', 'GHz', 'V', 'mV', 'A', 'mA', 'µA', 'uA', 'nA',
    'K', '°C', 'T', 'mT', 'W', 'mW', 'cm^-1', 'counts', 'counts/s', 'cps', 'a.u.', 'a. u.', '%'))


def sheet_column_metadata(workspace, sheet, column, mode=None):
    """Effective display cells, separate from source coordinates and identities."""
    sheet = _sheet(workspace, sheet)
    if type(column) is not int or not 0 <= column < sheet_shape(workspace, sheet, mode)[1]:
        raise ValueError('Column index is outside the Sheet.')
    units = ''
    cube = _cube(workspace, sheet, mode)
    if cube is not None:
        # Gate is the series coordinate, never the unit of the measured signal.
        name = f'{cube.gate[column - 1]:g}' if column > 0 else cube.energy_label
    else:
        name = _PARAMETER_COLUMNS[column] if sheet.role == 'fit_parameters' else sheet.column_names[column]
    if cube is None or column == 0:
        match = re.fullmatch(r'(.*?)\s*\(([^()]*)\)', name)
        if match and match[2] in _LABEL_UNITS:
            name, units = match[1].rstrip(), match[2]
    return dict(long_name=name, units=units) | sheet.column_metadata.get(column, {})


def _metadata_label(metadata):
    name, units = metadata['long_name'], metadata['units']
    return f'{name} ({units})' if units and name else name or units


def initialize_comparison_labels(workspace, comparison):
    """Choose common quantities once, after a new graph's source batch is known."""
    def numeric_name(name):
        try:
            float(name)
        except ValueError:
            return False
        return True

    x_metadata, y_metadata = [], []
    for curve in comparison.curves.values():
        sheet = workspace.sheets[curve.sheet_id]
        x_metadata.append(sheet_column_metadata(workspace, sheet, curve.x_column, curve.mode))
        if sheet.kind == 'dat' and sheet.role in ('original', 'processed') and curve.y_column > 0:
            session = workspace.datasets[sheet.dataset_id]
            cube = session.base_cube
            kind = cube.signal_source_kind
            if kind == 'unknown':
                kind = infer_source_signal_kind(cube.source_path or str(sheet.source_path or ''))
            processing = (session.processing_results[sheet.result_id].processing
                          if sheet.result_id is not None else session.applied)
            label = automatic_signal_label(kind,
                curve.mode == 'processed' and processing.mode == 'energy_second_derivative',
                cube.signal_kind == 'energy_second_derivative')
            name, units = label.rsplit(' (', 1)
            metadata = dict(long_name=name, units=units[:-1])
            overrides = dict(sheet.column_metadata.get(curve.y_column, {}))
            if 'long_name' in overrides and numeric_name(overrides['long_name']):
                del overrides['long_name']
            metadata.update(overrides)
        else:
            metadata = sheet_column_metadata(workspace, sheet, curve.y_column, curve.mode)
            # Embedded Gate snapshots retain numeric series names; they do not
            # declare the measured signal's physical quantity.
            if numeric_name(metadata['long_name']):
                metadata = dict(metadata, long_name='Signal')
        y_metadata.append(metadata)

    def common(metadata, fallback):
        if not metadata:
            return fallback
        names = {item['long_name'] for item in metadata}
        units = {item['units'] for item in metadata}
        return _metadata_label(dict(long_name=next(iter(names)) if len(names) == 1 else fallback,
                                    units=next(iter(units)) if len(units) == 1 else ''))

    comparison.x_label = common(x_metadata, 'X')
    comparison.y_label = common(y_metadata, 'Signal')


def sheet_columns(workspace, sheet, mode=None):
    """Consumer labels; untouched columns keep their exact legacy strings."""
    sheet = _sheet(workspace, sheet)
    names = _source_sheet_columns(workspace, sheet, mode)
    for column in sheet.column_metadata:
        if type(column) is int and 0 <= column < len(names):
            names[column] = _metadata_label(sheet_column_metadata(workspace, sheet, column, mode))
    return names


def _validate_column_metadata(metadata, count):
    if not isinstance(metadata, dict):
        raise ValueError('Column metadata must be a mapping.')
    for column, fields in metadata.items():
        if type(column) is not int or not 0 <= column < count:
            raise ValueError('Column metadata index is outside the Sheet.')
        if not isinstance(fields, dict) or not fields or not set(fields) <= {'long_name', 'units'}:
            raise ValueError('Column metadata requires Long Name or Units fields.')
        for value in fields.values():
            _text(value, 'Column metadata', nonempty=False)
            if '\n' in value or '\r' in value:
                raise ValueError('Column metadata must fit on one line.')


def set_sheet_column_metadata(workspace, sheet_id, column, **fields):
    sheet = workspace.sheets[sheet_id]
    _validate_column_metadata({column: fields}, sheet_shape(workspace, sheet)[1])
    updated = dict(sheet.column_metadata.get(column, {})) | fields
    if updated == sheet.column_metadata.get(column):
        return False
    automatic = [curve for comparison in workspace.comparisons.values() for curve in comparison.curves.values()
                 if curve.sheet_id == sheet_id and curve.y_column == column and curve.label in (
                     default_curve_label(workspace, sheet, column),
                     default_curve_label(workspace, sheet, column, disambiguate_source=False))]
    sheet.column_metadata[column] = updated
    for curve in automatic:
        curve.label = default_curve_label(workspace, sheet, column)
    return True


def sheet_numeric_columns(workspace, sheet, mode=None):
    sheet = _sheet(workspace, sheet)
    text = {1, 2} if sheet.role == 'fit_parameters' else set(sheet.text_columns)
    return [index for index in range(sheet_shape(workspace, sheet, mode)[1]) if index not in text]


def validate_mapping(workspace, sheet, x_column, y_columns, mode=None):
    sheet = _sheet(workspace, sheet)
    count = sheet_shape(workspace, sheet, mode)[1]
    if not isinstance(y_columns, list):
        raise ValueError('Y columns must be a list.')
    indices = [x_column, *y_columns]
    if any(type(index) is not int or not 0 <= index < count for index in indices):
        raise ValueError('Column index is outside the Sheet.')
    if len(set(y_columns)) != len(y_columns):
        raise ValueError('Y columns must be unique.')
    if any(index not in sheet_numeric_columns(workspace, sheet, mode) for index in indices):
        raise ValueError('Text columns cannot be used for X/Y plotting.')


def set_sheet_mapping(workspace, sheet_id, x_column, y_columns, mode=None):
    sheet = workspace.sheets[sheet_id]
    validate_mapping(workspace, sheet, x_column, y_columns, mode)
    mode = sheet.mode if mode is None else mode
    if sheet.kind == 'dat' and mode != sheet.mode:
        raise ValueError('Choose the Original or Processed Sheet for these values.')
    sheet.x_column, sheet.y_columns, sheet.mode = x_column, list(y_columns), mode


def sheet_column_values(workspace, sheet, column, mode=None):
    """Return one numeric or text column; source cubes remain authoritative."""
    sheet = _sheet(workspace, sheet)
    if type(column) is not int or not 0 <= column < sheet_shape(workspace, sheet, mode)[1]:
        raise ValueError('Column index is outside the Sheet.')
    if sheet.role == 'fit_parameters':
        return np.asarray([row[column] for row in _parameter_values(workspace.datasets[sheet.dataset_id])],
                          dtype=str if column in (1, 2) else float)
    if sheet.role == 'fit_curves':
        result = _retained_fits(workspace.datasets[sheet.dataset_id]).get(sheet.fit_gate_idx)
        if result is None:
            return np.empty(0)
        if column == 0:
            return result.x
        if column == 1:
            return result.y_fit
        return (result.components[column - 2] + result.baseline if column - 2 < len(result.components)
                else np.full(result.x.size, np.nan))
    if column in sheet.text_columns:
        return np.asarray(sheet.text_columns[column], dtype=str)
    cube = _cube(workspace, sheet, mode)
    return sheet.data[:, column] if cube is None else cube.energy if column == 0 else cube.z[column - 1]


def sheet_values(workspace, sheet, mode=None):
    """Materialize a table when needed for copying; virtual tables use column views."""
    sheet = _sheet(workspace, sheet)
    if sheet.role in ('fit_parameters', 'fit_curves') or sheet.text_columns:
        values = np.empty(sheet_shape(workspace, sheet, mode), dtype=object if sheet.role == 'fit_parameters' or sheet.text_columns else float)
        for column in range(values.shape[1]):
            values[:, column] = sheet_column_values(workspace, sheet, column, mode)
        return values
    cube = _cube(workspace, sheet, mode)
    return sheet.data if cube is None else np.column_stack((cube.energy, cube.z.T))


def create_workbook(workspace, name='Workbook'):
    book = Workbook(name=_name(name))
    workspace.workbooks[book.workbook_id] = book
    return book


def register_xy_sheet(workspace, workbook_id, name, data, column_names, *, source_path=None,
                      allow_duplicate=False):
    book = workspace.workbooks[workbook_id]
    if source_path is not None and not allow_duplicate:
        existing = next((sheet for sheet in workspace.sheets.values() if sheet.kind == 'xy'
                         and sheet.source_path is not None
                         and _source_key(sheet.source_path) == _source_key(source_path)), None)
        if existing is not None:
            return existing
    sheet = Sheet(name=_name(name), source_path=None if source_path is None else Path(source_path),
                  data=data, column_names=list(column_names), y_columns=list(range(1, len(column_names))))
    _validate_sheet_source(workspace, sheet)
    validate_mapping(workspace, sheet, sheet.x_column, sheet.y_columns)
    workspace.sheets[sheet.sheet_id] = sheet
    book.sheet_ids.append(sheet.sheet_id)
    return sheet


def ensure_dataset_sheet(workspace, dataset_id, role='original', fit_gate_idx=None, *, result_id=None):
    """Return/create a stable lightweight projection within the source owner."""
    session = workspace.datasets[dataset_id]
    if role == 'processed' and result_id is None:
        result_id = next((key for key, result in session.processing_results.items()
                          if result.cube is session.current_cube), None)
    for sheet in workspace.sheets.values():
        if sheet.kind == 'dat' and (sheet.dataset_id, sheet.role, sheet.fit_gate_idx, sheet.result_id) == (dataset_id, role, fit_gate_idx, result_id):
            return sheet
    name = session.source_path.name if session.source_path else 'DAT'
    original = next((s for s in workspace.sheets.values() if s.dataset_id == dataset_id and s.role == 'original'), None)
    book = (next(b for b in workspace.workbooks.values() if original.sheet_id in b.sheet_ids)
            if original else create_workbook(workspace, name))
    names = {'original': 'Original', 'processed': 'Processed', 'fit_parameters': 'Fit parameters',
             'fit_curves': f'Fit curves · Gate {fit_gate_idx}'}
    sheet = Sheet(name=names[role], kind='dat', source_path=session.source_path, dataset_id=dataset_id,
                  role=role, fit_gate_idx=fit_gate_idx, mode='processed' if role == 'processed' else 'raw',
                  y_columns=([4] if role == 'fit_parameters' else [1] if role == 'fit_curves' else
                             list(range(1, (session.current_cube if role == 'processed' else session.base_cube).gate.size + 1))),
                  column_names=['X', 'Total fit'] if role == 'fit_curves' else [])
    sheet.result_id = result_id
    workspace.sheets[sheet.sheet_id] = sheet
    book.sheet_ids.append(sheet.sheet_id)
    return sheet


def _processing_result_name(workspace, session, processing):
    number = 1
    names = {sheet.name for sheet in workspace.sheets.values() if sheet.dataset_id == session.dataset_id}
    order = 'd⁴/dE⁴' if session.base_cube.signal_kind == 'energy_second_derivative' else 'd²/dE²'
    smoothing = f'SG {processing.window_length}, p3' if processing.smooth else 'SG off'
    while any(name.startswith(f'Processed {number} ·') for name in names):
        number += 1
    return f'Processed {number} · {order} · {smoothing}'


def retain_current_processing_result(workspace, dataset_id):
    """Promote the legacy moving Processed projection once, before replacing it."""
    from .dataset_state import ProcessingResult
    session = workspace.datasets[dataset_id]
    if session.current_cube is session.base_cube and session.applied.mode == 'original':
        for graph in session.graphs.values():
            if graph.analysis is None and graph.source_mode == 'processed' and graph.result_id is None:
                graph.source_mode = 'raw'
        return None
    existing = next((result for result in session.processing_results.values()
                     if result.cube is session.current_cube), None)
    if existing is not None:
        return existing
    sheet = ensure_dataset_sheet(workspace, dataset_id, 'processed')
    result = ProcessingResult(_id(), session.current_cube, deepcopy(session.applied))
    session.processing_results[result.result_id] = result
    sheet.result_id = result.result_id
    if sheet.name == 'Processed':
        sheet.name = _processing_result_name(workspace, session, result.processing)
    for graph in session.graphs.values():
        if graph.analysis is None and graph.source_mode == 'processed' and graph.result_id is None:
            graph.result_id = result.result_id
    return result


def add_processing_result(workspace, dataset_id, cube, processing):
    """Publish one independent run and a linked Heatmap using current visuals."""
    from .dataset_state import GraphState, ProcessingResult, graph_signal_label, refresh_graph_colors
    session = workspace.datasets[dataset_id]
    retain_current_processing_result(workspace, dataset_id)
    name = _processing_result_name(workspace, session, processing)
    result = ProcessingResult(_id(), cube, deepcopy(processing))
    session.processing_results[result.result_id] = result
    if cube is not session.current_cube:
        session._clear_derived_results()
    session.current_cube = cube
    session.applied, session.pending = deepcopy(processing), deepcopy(processing)
    source = session.active_graph
    graph_name, count = f'{name} Heatmap', 2
    while any(graph.name == graph_name for graph in session.graphs.values()):
        graph_name, count = f'{name} Heatmap {count}', count + 1
    graph = GraphState(_id(), graph_name, deepcopy(source.view), deepcopy(source.presentation),
                       'processed', deepcopy(source.gate_indices) if source.gate_indices is not None else list(range(cube.gate.size)),
                       True, result_id=result.result_id)
    session.graphs[graph.graph_id] = graph
    session.activate_graph(graph.graph_id)
    refresh_graph_colors(graph, cube)
    if graph.view.signal_title_auto:
        graph.view.annotations['signal_title'] = graph_signal_label(session, graph)
    sheet = ensure_dataset_sheet(workspace, dataset_id, 'processed', result_id=result.result_id)
    sheet.name = name
    if graph.gate_indices is not None:
        sheet.y_columns = [index + 1 for index in graph.gate_indices]
    return sheet, graph


def reconcile_dataset_sheets(workspace, dataset_id):
    """Retain valid column mappings after a DAT replacement; remove invalid curves."""
    removed = []
    session = workspace.datasets.get(dataset_id)
    if session is not None:
        ensure_dataset_sheet(workspace, dataset_id)
        if session.processing_results:
            for result_id in session.processing_results:
                ensure_dataset_sheet(workspace, dataset_id, 'processed', result_id=result_id)
        elif session.applied.mode != 'original' or session.current_cube is not session.base_cube:
            ensure_dataset_sheet(workspace, dataset_id, 'processed')
        if _retained_fits(session) or session.fit_export_rows:
            ensure_dataset_sheet(workspace, dataset_id, 'fit_parameters')
        for gate, result in sorted(_retained_fits(session).items()):
            if 0 <= gate < session.current_cube.gate.size:
                sheet = ensure_dataset_sheet(workspace, dataset_id, 'fit_curves', gate)
                for index in range(len(sheet.column_names) - 2, len(result.components)):
                    sheet.column_names.append(f'Component {index + 1} + baseline')
    for sheet in list(workspace.sheets.values()):
        if sheet.kind != 'dat' or sheet.dataset_id != dataset_id:
            continue
        if session is None or (sheet.role == 'fit_curves' and sheet.fit_gate_idx >= session.current_cube.gate.size):
            removed.extend(remove_sheet(workspace, sheet.sheet_id))
            continue
        sheet.source_path = workspace.datasets[dataset_id].source_path
        count = sheet_shape(workspace, sheet)[1]
        sheet.column_metadata = {column: fields for column, fields in sheet.column_metadata.items()
                                 if type(column) is int and 0 <= column < count}
        sheet.x_column = sheet.x_column if 0 <= sheet.x_column < count else 0
        sheet.y_columns = [index for index in sheet.y_columns if 0 <= index < count]
        for comparison in workspace.comparisons.values():
            for curve in list(comparison.curves.values()):
                if curve.sheet_id == sheet.sheet_id:
                    try:
                        validate_mapping(workspace, sheet, curve.x_column, [curve.y_column], curve.mode)
                    except ValueError:
                        removed.append(comparison.curves.pop(curve.curve_id))
    return removed


def save_sheet_snapshot(workspace, sheet_id, name):
    source = workspace.sheets[sheet_id]
    name = _name(name)
    rows, columns = sheet_shape(workspace, source)
    if rows == 0:
        raise ValueError('There are no current results to snapshot.')
    data = np.full((rows, columns), np.nan)
    text_columns = {}
    numeric = sheet_numeric_columns(workspace, source)
    for column in range(columns):
        values = sheet_column_values(workspace, source, column)
        if column in numeric:
            data[:, column] = values
        else:
            text_columns[column] = [str(value) for value in values]
    context = ''
    if source.dataset_id is not None:
        session = workspace.datasets[source.dataset_id]
        result = session.processing_results.get(source.result_id)
        processing = result.processing if result is not None else session.applied
        cube = result.cube if result is not None else session.current_cube
        context = f' · Applied processing: {processing.recipe()}'
        context += f' · Analysis axis: {cube.energy_label}'
        if source.fit_gate_idx is not None:
            context += f' · Gate {source.fit_gate_idx} = {session.current_cube.gate[source.fit_gate_idx]:g}'
            result = _retained_fits(session).get(source.fit_gate_idx)
            if result is not None:
                context += f' · Fit model: {result.model}'
    note = (f'Snapshot captured {datetime.now(timezone.utc).isoformat()} from {source.name} '
            f'({source.role}) · {source.source_path or "Embedded values"}{context}. '
            'Captured values are independent of later source changes.')
    if source.snapshot_note:
        note += '\n' + source.snapshot_note
    snapshot = Sheet(name=name, kind='snapshot', source_path=source.source_path, data=data,
                     column_names=_source_sheet_columns(workspace, source), x_column=source.x_column,
                     y_columns=list(source.y_columns), role='snapshot', text_columns=text_columns,
                     snapshot_note=note, column_metadata={column: sheet_column_metadata(workspace, source, column)
                                                         for column in range(columns)})
    _validate_sheet_source(workspace, snapshot)
    owner = next(book for book in workspace.workbooks.values() if sheet_id in book.sheet_ids)
    workspace.sheets[snapshot.sheet_id] = snapshot
    owner.sheet_ids.append(snapshot.sheet_id)
    return snapshot


def merge_workbooks(workspace, workbook_ids, destination_id):
    selected = set(workbook_ids)
    if len(selected) < 2 or destination_id not in selected or not selected.issubset(workspace.workbooks):
        raise ValueError('Select at least two existing Workbooks and one selected destination.')
    validate_workbook_state(workspace)
    destination = workspace.workbooks[destination_id]
    for identity, book in list(workspace.workbooks.items()):
        if identity in selected and identity != destination_id:
            destination.sheet_ids.extend(book.sheet_ids)
            del workspace.workbooks[identity]
    prune_document_layout(workspace)
    return destination


def remove_sheet(workspace, sheet_id):
    sheet = workspace.sheets.pop(sheet_id)
    if sheet.result_id is not None and sheet.dataset_id in workspace.datasets:
        session = workspace.datasets[sheet.dataset_id]
        graph_ids = [graph.graph_id for graph in session.graphs.values() if graph.result_id == sheet.result_id]
        if len(graph_ids) == len(session.graphs):
            from .dataset_state import GraphState
            fallback = GraphState(_id(), name='Original Heatmap', source_mode='raw', published=False)
            session.graphs[fallback.graph_id] = fallback
        for graph_id in graph_ids:
            session.remove_graph(graph_id)
        removed_result = session.processing_results.pop(sheet.result_id)
        if session.current_cube is removed_result.cube:
            session._clear_derived_results()
            if session.active_graph.result_id is not None:
                session.activate_graph(session.active_graph_id)
            elif session.processing_results:
                result = session.processing_results[next(reversed(session.processing_results))]
                session.current_cube = result.cube
                session.applied, session.pending = deepcopy(result.processing), deepcopy(result.processing)
            else:
                from .dataset_state import ProcessingState
                session.current_cube = session.base_cube
                session.applied, session.pending = ProcessingState(), ProcessingState()
        prune_document_layout(workspace)
    for book in workspace.workbooks.values():
        book.sheet_ids[:] = [identity for identity in book.sheet_ids if identity != sheet_id]
    removed = []
    for comparison in workspace.comparisons.values():
        for key, curve in list(comparison.curves.items()):
            if curve.sheet_id == sheet_id:
                removed.append(comparison.curves.pop(key))
    return removed


def remove_workbook(workspace, workbook_id):
    book = workspace.workbooks[workbook_id]
    removed = []
    for sheet_id in list(book.sheet_ids):
        removed.extend(remove_sheet(workspace, sheet_id))
    del workspace.workbooks[workbook_id]
    prune_document_layout(workspace)
    return removed


def remove_dataset_sheets(workspace, dataset_id):
    owners = {book.workbook_id for book in workspace.workbooks.values()
              if any(workspace.sheets[key].dataset_id == dataset_id for key in book.sheet_ids)}
    removed = reconcile_dataset_sheets(workspace, dataset_id)
    for key in owners:
        if not workspace.workbooks[key].sheet_ids:
            del workspace.workbooks[key]
    prune_document_layout(workspace)
    return removed


def create_comparison(workspace, name='Comparison'):
    comparison = Comparison(name=_name(name), presentation=PlotPresentation())
    workspace.comparisons[comparison.comparison_id] = comparison
    return comparison


def validate_curve_style(curve):
    if curve.style not in ('line', 'scatter', 'line_symbol'):
        raise ValueError('Unknown curve style.')
    _text(curve.label, 'Curve label', nonempty=False)
    if type(curve.visible) is not bool:
        raise ValueError('Curve visibility must be boolean.')
    if not isinstance(curve.color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', curve.color):
        raise ValueError('Curve color must be a six-digit hex color.')
    if (isinstance(curve.width, bool) or not isinstance(curve.width, (int, float))
            or not math.isfinite(curve.width) or curve.width <= 0):
        raise ValueError('Curve width must be finite and positive.')
    _finite_number(curve.y_offset, 'Curve Y offset')
    if curve.marker_shape not in ('ball', 'o', 's', '^', 'v', '<', '>', 'D', 'p', 'h', '*', '+', 'x'):
        raise ValueError('Unknown curve symbol shape.')
    if curve.marker_fill not in ('full', 'none', 'left', 'right', 'top', 'bottom'):
        raise ValueError('Unknown curve symbol fill.')
    _finite_number(curve.marker_size, 'Curve symbol size')
    if curve.marker_size <= 0:
        raise ValueError('Curve symbol size must be positive.')
    _finite_number(curve.marker_edge_width, 'Curve symbol outline width')
    if curve.marker_edge_width < 0:
        raise ValueError('Curve symbol outline width cannot be negative.')
    if (not isinstance(curve.marker_edge_color, str) or curve.marker_edge_color != 'auto'
            and not re.fullmatch(r'#[0-9a-fA-F]{6}', curve.marker_edge_color)):
        raise ValueError('Curve symbol outline must be auto or a six-digit hex color.')


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number.')


def validate_comparison_display(comparison):
    _text(comparison.plot_title, 'Plot title', nonempty=False)
    if comparison.presentation is not None and not isinstance(comparison.presentation, PlotPresentation):
        raise ValueError('Comparison presentation requires PlotPresentation or None.')
    if comparison.normalization not in ('none', 'maxabs', 'minmax'):
        raise ValueError('Unknown comparison normalization.')
    if comparison.x_range_mode not in ('all', 'common', 'manual'):
        raise ValueError('Unknown comparison X range mode.')
    _finite_number(comparison.stack_step, 'Stack step')
    bounds = comparison.x_range
    if bounds is not None:
        if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
            raise ValueError('Comparison X range requires two bounds.')
        for value in bounds:
            _finite_number(value, 'X range bound')
        if bounds[0] >= bounds[1]:
            raise ValueError('X minimum must be less than X maximum.')
    elif comparison.x_range_mode == 'manual':
        raise ValueError('Manual X range requires two bounds.')


def curve_source_key(workspace, sheet_id, x_column, y_column, mode=None):
    """Canonical source identity, without creating a missing DAT projection."""
    sheet = workspace.sheets[sheet_id]
    mode = sheet.mode if mode is None else mode
    if sheet.kind == 'dat' and sheet.role in ('original', 'processed'):
        role = 'processed' if mode == 'processed' else 'original'
        result_id = sheet.result_id if role == 'processed' else None
        if role == 'processed' and sheet.role == 'original':
            session = workspace.datasets[sheet.dataset_id]
            result_id = next((key for key, result in session.processing_results.items()
                              if result.cube is session.current_cube), None)
        canonical = next((s for s in workspace.sheets.values()
                          if s.kind == 'dat' and s.dataset_id == sheet.dataset_id and s.role == role
                          and s.result_id == result_id), None)
        sheet_id = canonical.sheet_id if canonical else ('dat', sheet.dataset_id, role, result_id)
    return sheet_id, x_column, y_column, mode


def select_curve_additions(workspace, comparison_id, candidates, *, include_existing=False):
    """Filter exact source/mapping duplicates, including repetitions in one batch."""
    comparison = workspace.comparisons.get(comparison_id)
    seen = {curve_source_key(workspace, c.sheet_id, c.x_column, c.y_column, c.mode)
            for c in comparison.curves.values()} if comparison else set()
    additions, skipped = [], 0
    for candidate in candidates:
        key = curve_source_key(workspace, *candidate)
        if key in seen and not include_existing:
            skipped += 1
        else:
            additions.append(candidate)
        seen.add(key)
    return additions, skipped


def _curve_sheet_context(workspace, sheet):
    if sheet.role == 'fit_curves':
        cube = workspace.datasets[sheet.dataset_id].current_cube
        return f'Fit curves · {cube.gate_label} = {cube.gate[sheet.fit_gate_idx]:g}'
    return sheet.name


def default_curve_label(workspace, sheet, y_column, *, disambiguate_source=True):
    """Describe the retained source and values only when creating a new curve."""
    owner = next((b.name for b in workspace.workbooks.values() if sheet.sheet_id in b.sheet_ids), 'Workbook')
    source = sheet.source_path
    if source is None:
        label = owner
    else:
        label = source.name
        others = {s.source_path for s in workspace.sheets.values()
                  if s.source_path is not None and s.source_path.name.casefold() == source.name.casefold()
                  and _source_key(s.source_path) != _source_key(source)}
        if others and disambiguate_source:
            # Shortest useful folder suffix; source paths are provenance only.
            # abspath is lexical: relative paths need a parent for display,
            # without resolving symlinks, probing files or changing provenance.
            parent = Path(os.path.abspath(source)).parent
            other_parents = [Path(os.path.abspath(other)).parent for other in others]
            for depth in range(1, len(parent.parts) + 1):
                suffix = parent.parts[-depth:]
                if all(tuple(p.casefold() for p in other.parts[-depth:]) !=
                       tuple(p.casefold() for p in suffix) for other in other_parents):
                    break
            label += f' [{str(Path(*suffix))}]'
    if sheet.kind == 'dat' or sheet.role != 'original' or sheet.name != 'Original':
        label += f' · {_curve_sheet_context(workspace, sheet)}'
    return f'{label}: {sheet_columns(workspace, sheet)[y_column]}'


def add_curve(workspace, comparison_id, sheet_id, x_column, y_column, *, mode=None,
              label=None, color=None, width=1.5, visible=True, y_offset=0.0, style='line'):
    comparison = workspace.comparisons[comparison_id]
    sheet = workspace.sheets[sheet_id]
    mode = sheet.mode if mode is None else mode
    if sheet.kind == 'dat' and sheet.role in ('original', 'processed') and not (mode == 'processed' and sheet.result_id is not None):
        sheet = ensure_dataset_sheet(workspace, sheet.dataset_id, 'processed' if mode == 'processed' else 'original')
        sheet_id = sheet.sheet_id
    if mode != sheet.mode:
        raise ValueError('Curve mode must match its source Sheet role.')
    validate_mapping(workspace, sheet, x_column, [y_column], mode)
    curve = Curve(sheet_id=sheet_id, x_column=x_column, y_column=y_column, mode=mode,
                  label=default_curve_label(workspace, sheet, y_column) if label is None else label,
                  color=_COLORS[len(comparison.curves) % len(_COLORS)] if color is None else color,
                  width=width, visible=visible, y_offset=y_offset, style=style)
    validate_curve_style(curve)
    comparison.curves[curve.curve_id] = curve
    return curve


def resolve_curve(workspace, curve):
    """Return original X/Y column views, including nonfinite gaps and row order."""
    sheet = workspace.sheets.get(curve.sheet_id)
    if sheet is None:
        raise ValueError('Curve references a missing Sheet.')
    if curve.mode != sheet.mode:
        raise ValueError('Curve mode must match its source Sheet role.')
    validate_mapping(workspace, sheet, curve.x_column, [curve.y_column], curve.mode)
    return (sheet_column_values(workspace, sheet, curve.x_column, curve.mode),
            sheet_column_values(workspace, sheet, curve.y_column, curve.mode))


def remove_curve(workspace, comparison_id, curve_id):
    return workspace.comparisons[comparison_id].curves.pop(curve_id)


def remove_comparison(workspace, comparison_id):
    removed = workspace.comparisons.pop(comparison_id)
    prune_document_layout(workspace)
    return removed


def document_key(kind, owner_id, graph_id=''):
    if kind == 'heatmap':
        return decode_document_key([owner_id, graph_id])
    return decode_document_key([kind, owner_id, graph_id])


def decode_document_key(value):
    if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
        raise ValueError('Document identity requires two heatmap or three auxiliary key parts.')
    for part in value[:2]:
        _text(part, 'Document identity')
    if len(value) == 3 and (value[0] not in ('workbook', 'xy') or value[2] != ''):
        raise ValueError('Auxiliary document identity must be workbook/xy, ID, empty string.')
    return tuple(value)


def encode_document_key(key):
    return list(decode_document_key(key))


def document_record_key(record):
    return decode_document_key(record['key'] if 'key' in record else
                               [record['dataset_id'], record['graph_id']])


def document_exists(workspace, key):
    key = decode_document_key(key)
    if len(key) == 2:
        session = workspace.datasets.get(key[0])
        return session is not None and key[1] in session.graphs
    return key[1] in (workspace.workbooks if key[0] == 'workbook' else workspace.comparisons)


def prune_document_layout(workspace):
    """Remove stale identities while retaining intentional closed documents."""
    layout = workspace.graph_workspace
    if layout is None:
        return
    layout['documents'] = [record for record in layout['documents']
                           if document_exists(workspace, document_record_key(record))]
    opened = {document_record_key(record) for record in layout['documents'] if record['open']}
    layout['stacking'] = [key for key in layout['stacking'] if decode_document_key(key) in opened]
    if layout['active'] is not None and decode_document_key(layout['active']) not in opened:
        layout['active'] = None


def _validate_sheet_source(workspace, sheet):
    if sheet.source_path is not None and (not isinstance(sheet.source_path, (Path, str))
                                         or not _text(str(sheet.source_path), 'Sheet source path')):
        raise ValueError('Invalid Sheet source path.')
    if sheet.role not in ('original', 'processed', 'fit_parameters', 'fit_curves', 'snapshot'):
        raise ValueError('Unknown Sheet role.')
    if sheet.result_id is not None:
        _text(sheet.result_id, 'Processing result reference')
        session = workspace.datasets.get(sheet.dataset_id)
        if (sheet.kind != 'dat' or sheet.role != 'processed' or session is None
                or sheet.result_id not in session.processing_results):
            raise ValueError('Processed Sheet references a missing processing result.')
    if sheet.role == 'fit_curves':
        if type(sheet.fit_gate_idx) is not int or sheet.fit_gate_idx < 0:
            raise ValueError('Fit curves require a nonnegative Gate index.')
    elif sheet.fit_gate_idx is not None:
        raise ValueError('Only fit curves can have a Gate index.')
    if not isinstance(sheet.text_columns, dict):
        raise ValueError('Text columns must be a mapping.')
    _text(sheet.snapshot_note, 'Snapshot provenance', nonempty=False)
    if sheet.kind != 'snapshot' and (sheet.text_columns or sheet.snapshot_note):
        raise ValueError('Only snapshots store text columns and frozen provenance.')
    expected_mode = 'processed' if sheet.role == 'processed' else 'raw'
    if sheet.mode != expected_mode:
        raise ValueError('Sheet mode must match its role.')
    if sheet.kind in ('xy', 'snapshot'):
        if sheet.role != ('original' if sheet.kind == 'xy' else 'snapshot'):
            raise ValueError('Embedded Sheet role must match its source kind.')
        if (sheet.dataset_id is not None or not isinstance(sheet.data, np.ndarray)
                or sheet.data.ndim != 2 or sheet.data.shape[0] < 1 or sheet.data.shape[1] < 2
                or sheet.data.dtype.kind not in 'iuf' or sheet.data.dtype.fields is not None):
            raise ValueError('XY Sheets require a nonempty real numeric matrix and no dataset reference.')
        if not isinstance(sheet.column_names, list) or len(sheet.column_names) != sheet.data.shape[1]:
            raise ValueError('Sheet column labels must match the numeric matrix.')
        for name in sheet.column_names:
            _text(name, 'Column label')
        if sheet.kind == 'snapshot':
            _text(sheet.snapshot_note, 'Snapshot provenance')
        for column, values in sheet.text_columns.items():
            if (type(column) is not int or not 0 <= column < sheet.data.shape[1]
                    or not isinstance(values, list) or len(values) != sheet.data.shape[0]):
                raise ValueError('Text column indices and lengths must match the snapshot.')
            for value in values:
                _text(value, 'Text cell', nonempty=False)
            if not np.isnan(sheet.data[:, column]).all():
                raise ValueError('Text columns require empty numeric backing.')
    elif sheet.kind == 'dat':
        if sheet.role == 'snapshot' or sheet.data is not None or (sheet.role != 'fit_curves' and sheet.column_names != []):
            raise ValueError('DAT Sheets must reference cubes without stored arrays or columns.')
        _text(sheet.dataset_id, 'DAT dataset reference')
        session = workspace.datasets.get(sheet.dataset_id)
        if session is None:
            raise ValueError('DAT Sheet references a missing dataset.')
        if sheet.source_path is None or _source_key(sheet.source_path) != _source_key(session.source_path):
            raise ValueError('DAT Sheet source provenance must match its dataset.')
        if sheet.role == 'fit_curves':
            if sheet.fit_gate_idx >= session.current_cube.gate.size:
                raise ValueError('Fit curves reference a missing Gate.')
            expected = ['X', 'Total fit'] + [f'Component {index + 1} + baseline' for index in range(len(sheet.column_names) - 2)]
            if len(sheet.column_names) < 2 or sheet.column_names != expected:
                raise ValueError('Fit-curve column identities are invalid.')
            result = _retained_fits(session).get(sheet.fit_gate_idx)
            if result is not None and len(sheet.column_names) < 2 + len(result.components):
                raise ValueError('Fit-curve columns omit retained components.')
    else:
        raise ValueError('Unknown Sheet source kind.')


def validate_workbook_state(workspace):
    """Validate IDs, unique memberships, source/mapping references and styles."""
    owners, datasets = {}, set()
    for collection, cls, field_name in ((workspace.workbooks, Workbook, 'workbook_id'),
                                       (workspace.sheets, Sheet, 'sheet_id'),
                                       (workspace.comparisons, Comparison, 'comparison_id')):
        if not isinstance(collection, dict):
            raise ValueError('Workbook collections must be mappings.')
        for key, record in collection.items():
            _text(key, 'Record ID')
            if not isinstance(record, cls) or getattr(record, field_name) != key:
                raise ValueError('Collection keys must match record IDs.')
            if _name(record.name) != record.name:
                raise ValueError('Names must be trimmed.')
    for book in workspace.workbooks.values():
        if not isinstance(book.sheet_ids, list):
            raise ValueError('Workbook members must be a list.')
        for sheet_id in book.sheet_ids:
            _text(sheet_id, 'Sheet member ID')
            if sheet_id not in workspace.sheets or sheet_id in owners:
                raise ValueError('Each Sheet requires exactly one workbook owner.')
            owners[sheet_id] = book.workbook_id
    if set(owners) != set(workspace.sheets):
        raise ValueError('Each Sheet requires exactly one workbook owner.')
    for sheet in workspace.sheets.values():
        _validate_sheet_source(workspace, sheet)
        _validate_column_metadata(sheet.column_metadata, sheet_shape(workspace, sheet)[1])
        if sheet.kind == 'dat':
            projection = (sheet.dataset_id, sheet.role, sheet.fit_gate_idx, sheet.result_id)
            if projection in datasets:
                raise ValueError('A DAT may have only one Sheet for each role and Gate.')
            datasets.add(projection)
        validate_mapping(workspace, sheet, sheet.x_column, sheet.y_columns, sheet.mode)
    for comparison in workspace.comparisons.values():
        validate_comparison_display(comparison)
        _text(comparison.x_label, 'X axis label', nonempty=False)
        _text(comparison.y_label, 'Y axis label', nonempty=False)
        if type(comparison.legend_visible) is not bool or not isinstance(comparison.curves, dict):
            raise ValueError('Comparison requires boolean legend visibility and a curve mapping.')
        for key, curve in comparison.curves.items():
            _text(key, 'Curve ID')
            if not isinstance(curve, Curve) or curve.curve_id != key:
                raise ValueError('Curve collection keys must match curve IDs.')
            _text(curve.sheet_id, 'Curve Sheet reference')
            validate_curve_style(curve)
            resolve_curve(workspace, curve)
