"""Qt-free workbook ownership, numeric projections and live comparison curves."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
from uuid import uuid4

import numpy as np


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
    cube = session.base_cube if mode == 'raw' else session.current_cube
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


def sheet_columns(workspace, sheet, mode=None):
    sheet = _sheet(workspace, sheet)
    cube = _cube(workspace, sheet, mode)
    if sheet.role == 'fit_parameters':
        return list(_PARAMETER_COLUMNS)
    return list(sheet.column_names) if cube is None else [cube.energy_label] + [
        f'{cube.gate_label} = {gate:g}' for gate in cube.gate]


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


def ensure_dataset_sheet(workspace, dataset_id, role='original', fit_gate_idx=None):
    """Return/create a stable lightweight projection within the source owner."""
    for sheet in workspace.sheets.values():
        if sheet.kind == 'dat' and (sheet.dataset_id, sheet.role, sheet.fit_gate_idx) == (dataset_id, role, fit_gate_idx):
            return sheet
    session = workspace.datasets[dataset_id]
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
    workspace.sheets[sheet.sheet_id] = sheet
    book.sheet_ids.append(sheet.sheet_id)
    return sheet


def reconcile_dataset_sheets(workspace, dataset_id):
    """Retain valid column mappings after a DAT replacement; remove invalid curves."""
    removed = []
    session = workspace.datasets.get(dataset_id)
    if session is not None:
        ensure_dataset_sheet(workspace, dataset_id)
        if session.applied.mode != 'original' or session.current_cube is not session.base_cube:
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
        context = f' · Applied processing: {session.applied.recipe()}'
        context += f' · Analysis axis: {session.current_cube.energy_label}'
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
                     column_names=sheet_columns(workspace, source), x_column=source.x_column,
                     y_columns=list(source.y_columns), role='snapshot', text_columns=text_columns,
                     snapshot_note=note)
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
    workspace.sheets.pop(sheet_id)
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
    comparison = Comparison(name=_name(name))
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


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number.')


def validate_comparison_display(comparison):
    _text(comparison.plot_title, 'Plot title', nonempty=False)
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
        canonical = next((s for s in workspace.sheets.values()
                          if s.kind == 'dat' and s.dataset_id == sheet.dataset_id and s.role == role), None)
        sheet_id = canonical.sheet_id if canonical else ('dat', sheet.dataset_id, role)
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


def default_curve_label(workspace, sheet, y_column):
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
        if others:
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
        context = sheet.name
        if sheet.role == 'fit_curves':
            cube = workspace.datasets[sheet.dataset_id].current_cube
            context = f'Fit curves · {cube.gate_label} = {cube.gate[sheet.fit_gate_idx]:g}'
        label += f' · {context}'
    return f'{label}: {sheet_columns(workspace, sheet)[y_column]}'


def add_curve(workspace, comparison_id, sheet_id, x_column, y_column, *, mode=None,
              label=None, color=None, width=1.5, visible=True, y_offset=0.0, style='line'):
    comparison = workspace.comparisons[comparison_id]
    sheet = workspace.sheets[sheet_id]
    mode = sheet.mode if mode is None else mode
    if sheet.kind == 'dat' and sheet.role in ('original', 'processed'):
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
        if sheet.kind == 'dat':
            projection = (sheet.dataset_id, sheet.role, sheet.fit_gate_idx)
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
