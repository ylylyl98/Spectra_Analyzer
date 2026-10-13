"""Portable, explicit scientific workspace archives, with v1 migration.

Source and CSV paths are provenance; decoding never opens them. Version 1 stores
manifest.json and uniquely referenced real numeric NPY members, without pickle.
Limits allow large local experiments while bounding hostile headers and ZIPs:
65,536 entries, 16 MiB JSON, 1 GiB per member, 4 GiB expanded archive, and
128 million elements per array. Arrays have at most two dimensions and NPY
headers at most 16 KiB. Larger workspaces require a future storage version.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
import weakref
import zipfile
import zlib

import numpy as np

from .dat_io import AXIS_OVERRIDE_ITEMS, DataCube
from .dataset_state import (AnalysisControls, BOUND_NAMES, DatasetSession, FixedRecipe,
                            GraphState, ProcessingState, ViewState, Workspace, validate_graph_source)
from .fitting import FitResult
from .plot_presentation import NormSpec, PlotPresentation
from .title_templates import validate_title_settings
from .workbook_state import (Comparison, Curve, Sheet, Workbook, decode_document_key,
                             document_exists, ensure_dataset_sheet,
                             reconcile_dataset_sheets, validate_workbook_state,
                             validate_mapping, _validate_sheet_source)


SCHEMA_VERSION = 9
MAX_MEMBERS = 65_536
MAX_MANIFEST_BYTES = 16 * 1024**2
MAX_MEMBER_BYTES = 1024**3
MAX_TOTAL_BYTES = 4 * 1024**3
MAX_ARRAY_ELEMENTS = 128 * 1024**2
MAX_NPY_HEADER_BYTES = 16 * 1024
_ARRAY_NAME = re.compile(r'arrays/[0-9]{6}\.npy\Z')
_AXIS_TYPES = {item[0] for item in AXIS_OVERRIDE_ITEMS if item[0] is not None}
_PROCESSING_MODES = ('original', 'energy_second_derivative')
_SIGNAL_KINDS = ('unknown', 'pl', 'reflection', 'reflection_reference', 'energy_second_derivative')
_ANNOTATIONS = ('plot_title', 'y_axis_name', 'y_axis_unit', 'signal_title')
_CONTROL_BOOL = ('derivative_detection', 'peak_smooth', 'fit_xmin_fixed', 'fit_xmax_fixed',
                 'fit_smooth', 'csv_source')
_CONTROL_INT = ('distance', 'max_peaks', 'max_dips', 'peak_smooth_window', 'd2_window',
                'd2_polyorder', 'd2_distance', 'd2_max_peaks', 'd2_max_dips', 'd2_target',
                'fit_smooth_window', 'linewidth_min', 'linewidth_max', 'peak_move_limit', 'maxfev')
_CONTROL_NUMBER = ('prominence', 'd2_prominence', 'd2_separation', 'fit_xmin', 'fit_xmax')
_CONTROL_ENUM = {'peak_mode': ('Peaks', 'Dips', 'Both'), 'd2_mode': ('Peaks', 'Dips', 'Both'),
                 'fit_model': ('Lorentzian', 'Gaussian', 'Voigt')}
_PROCESSING_FIELDS = ('mode', 'smooth', 'window_length', 'original_signal_title')
_CUBE_ARRAYS = ('energy', 'gate', 'z', 'x_original', 'photon_energy')
_CUBE_STRINGS = ('gate_label', 'title', 'source_path', 'x_original_type', 'x_original_label',
                 'x_detection_source', 'x_detection_note', 'photon_energy_note', 'energy_label',
                 'signal_kind', 'signal_source_kind')
_CUBE_FIELDS = _CUBE_ARRAYS + _CUBE_STRINGS + ('x_is_ambiguous', 'x_override')
_VIEW_FIELDS = ('gate', 'colormap', 'norm', 'annotations', 'fix_annotations', 'signal_title_auto',
                'title_settings', 'bounds', 'locks', 'x_limits', 'y_limits', 'color_limits',
                'color_scale_mode', 'color_region_boundaries', 'last_valid_color_limits',
                'last_valid_region_color_limits')
_FIT_ARRAYS = ('x', 'y_fit', 'components', 'centers', 'amplitudes', 'widths')
_FIT_FIELDS = ('model',) + _FIT_ARRAYS + ('baseline', 'rmse', 'r_squared')
_PRESENTATION_FIELDS = ('width_in', 'height_in', 'aspect_locked', 'frame_width_in',
                        'frame_height_in', 'numeric_pt', 'text_pt', 'font_family',
                        'heatmap_dpi', 'include_peak_labels', 'axis', 'colorbar',
                        'annotations', 'title_position')
_AXIS_FIELDS = ('exchange_xy', 'flip_x', 'flip_y', 'show_x', 'show_y', 'show_x_label',
                'show_y_label', 'show_tick_labels', 'major_ticks', 'minor_ticks',
                'ticks_inside', 'border', 'border_width', 'x_major_step', 'y_major_step',
                'x_major_count', 'y_major_count', 'x_minor_count', 'y_minor_count')
_COLORBAR_FIELDS = ('visible', 'show_label', 'show_unit', 'width_in', 'height_in', 'gap_in',
                    'alignment', 'signal_position', 'frame_gap_in', 'show_extensions',
                    'label_format', 'significant_digits', 'decimal_places', 'scientific_style',
                    'trim_trailing_zeros', 'ticks')
_PLOT_ANNOTATION_FIELDS = ('id', 'kind', 'text', 'position', 'coordinate_space', 'target',
                          'target_space', 'font_role', 'visible', 'color', 'linewidth', 'head_size')
_V1_SESSION_FIELDS = (
    'dataset_id', 'kind', 'source_path', 'loaded_fingerprint', 'base_cube', 'current_cube',
    'current_is_base', 'view', 'presentation', 'applied', 'pending', 'fixed_recipes',
    'analysis_controls', 'peak_indices', 'peak_gate_idx', 'peak_kind_lookup',
    'peak_label_lookup', 'batch_peak_map', 'batch_peak_kind_lookup', 'csv_seeds_map',
    'csv_seeds_path', 'fit_result', 'fit_gate_idx', 'batch_fit_map', 'batch_fit_result_map',
    'fit_export_rows', 'has_batch_fit_results', 'summary', 'initialization_notice')
_SESSION_FIELDS = tuple(name for name in _V1_SESSION_FIELDS if name not in ('view', 'presentation')) + (
    'graphs', 'active_graph_id')
_WORKBOOK_FIELDS = ('workbook_id', 'name', 'sheet_ids')
_V5_SHEET_FIELDS = ('sheet_id', 'name', 'kind', 'source_path', 'data', 'column_names',
                 'dataset_id', 'x_column', 'y_columns', 'mode')
_SHEET_FIELDS = _V5_SHEET_FIELDS + ('role', 'fit_gate_idx', 'text_columns', 'snapshot_note')
_V4_CURVE_FIELDS = ('curve_id', 'sheet_id', 'x_column', 'y_column', 'mode', 'label', 'visible', 'color', 'width')
_V4_COMPARISON_FIELDS = ('comparison_id', 'name', 'x_label', 'y_label', 'legend_visible', 'curves')
_V6_CURVE_FIELDS = _V4_CURVE_FIELDS + ('y_offset',)
_CURVE_FIELDS = _V6_CURVE_FIELDS + ('style',)
_V6_COMPARISON_FIELDS = _V4_COMPARISON_FIELDS[:-1] + ('normalization', 'stack_step', 'x_range_mode', 'x_range', 'curves')
_COMPARISON_FIELDS = _V6_COMPARISON_FIELDS[:-1] + ('plot_title', 'curves')


class ProjectFormatError(ValueError):
    """The project or in-memory snapshot violates the supported format."""


def _fail(message):
    raise ProjectFormatError(message)


def _object(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail(f'{name} has invalid or missing fields.')
    return value


def _list(value, name):
    if not isinstance(value, list):
        _fail(f'{name} must be a list.')
    return value


def _string(value, name, *, nonempty=False):
    if not isinstance(value, str) or '\x00' in value or (nonempty and not value.strip()):
        _fail(f'{name} must be a valid {"nonempty " if nonempty else ""}string.')
    return value


def _boolean(value, name):
    if type(value) is not bool:
        _fail(f'{name} must be a boolean.')
    return value


def _integer(value, name):
    if type(value) is not int:
        _fail(f'{name} must be an integer.')
    return value


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f'{name} must be a finite number.')
    try:
        result = float(value)
    except (ValueError, OverflowError):
        _fail(f'{name} must be a finite number.')
    if not math.isfinite(result):
        _fail(f'{name} must be a finite number.')
    return result


def _enum(value, choices, name):
    if not isinstance(value, str) or value not in choices:
        _fail(f'{name} is not a supported value.')
    return value


def _pair(value, name):
    if not isinstance(value, list) or len(value) != 2:
        _fail(f'{name} requires two numbers.')
    return tuple(_number(item, name) for item in value)


def _index(value, size, name, *, optional=False):
    if optional and value is None:
        return None
    index = _integer(value, name)
    if not 0 <= index < size:
        _fail(f'{name} is outside its array.')
    return index


def _path_key(path):
    # This mirrors registry identity without Path.resolve probing missing DAT
    # paths or their parents. Relative provenance uses the current directory,
    # just as Workspace.register does, but requires no source-file access.
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def _plain(value):
    """Convert only explicitly selected schema values to JSON primitives."""
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _fields(record, names):
    return {name: _plain(getattr(record, name)) for name in names}


def _metric_encode(value):
    # JSON null is the explicit missing-fit-metric representation, not NaN JSON.
    value = _plain(value)
    return None if isinstance(value, float) and math.isnan(value) else value


def _metric_decode(value, name):
    return float('nan') if value is None else _number(value, name)


def _array_bounds(shape, dtype):
    if (not isinstance(shape, tuple) or len(shape) not in (1, 2)
            or any(type(size) is not int or size < 0 for size in shape)):
        _fail('NPY arrays must have one or two valid dimensions.')
    if (dtype.fields is not None or dtype.subdtype is not None or dtype.hasobject
            or dtype.metadata is not None or dtype.kind not in 'iuf' or dtype.itemsize > 16):
        _fail('NPY arrays must have a plain real numeric dtype.')
    elements = math.prod(shape)
    if elements > MAX_ARRAY_ELEMENTS or elements * dtype.itemsize > MAX_MEMBER_BYTES:
        _fail('NPY array exceeds the supported allocation limit.')
    return elements * dtype.itemsize


def _npy_member_size(array):
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(header, np.lib.format.header_data_from_array_1_0(array))
    return header.tell() + array.nbytes


class _ArrayWriter:
    def __init__(self, *, copy_arrays=True):
        self.arrays = {}
        self.total_bytes = 0
        self.copy_arrays = copy_arrays

    def add(self, array):
        if not isinstance(array, np.ndarray):
            _fail('Scientific array fields must contain NumPy arrays.')
        _array_bounds(array.shape, array.dtype)
        size = _npy_member_size(array)
        if size > MAX_MEMBER_BYTES:
            _fail('NPY member exceeds the supported size including its header.')
        self.total_bytes += size
        if self.total_bytes > MAX_TOTAL_BYTES or len(self.arrays) + 2 > MAX_MEMBERS:
            _fail('Workspace exceeds the supported archive limit.')
        name = f'arrays/{len(self.arrays):06d}.npy'
        self.arrays[name] = np.array(array, copy=True, order='C') if self.copy_arrays else array
        return name


class _ArrayReader:
    def __init__(self, arrays):
        self.arrays = arrays
        self.used = set()

    def get(self, reference, name, *, ndim, indices=False, missing=False, nonfinite=False):
        if not isinstance(reference, str) or not _ARRAY_NAME.fullmatch(reference):
            _fail(f'{name} has an invalid array reference.')
        if reference in self.used:
            _fail(f'{name} duplicates an array reference.')
        self.used.add(reference)
        if reference not in self.arrays:
            _fail(f'{name} references a missing array.')
        array = self.arrays[reference]
        if array.ndim != ndim or (indices and array.dtype.kind not in 'iu'):
            _fail(f'{name} has an invalid shape or index dtype.')
        if not nonfinite and (np.isinf(array).any() or (not missing and not np.isfinite(array).all())):
            _fail(f'{name} has nonfinite coordinates or values.')
        return array


def _encode_cube(cube, arrays):
    if not isinstance(cube, DataCube):
        _fail('Registered datasets require both scientific cubes.')
    result = _fields(cube, _CUBE_STRINGS + ('x_is_ambiguous', 'x_override'))
    for name in _CUBE_ARRAYS:
        value = getattr(cube, name)
        result[name] = None if name == 'photon_energy' and value is None else arrays.add(value)
    return result


def _decode_cube(value, arrays):
    value = _object(value, _CUBE_FIELDS, 'DataCube')
    result = {name: _string(value[name], f'cube.{name}') for name in _CUBE_STRINGS}
    for name in ('signal_kind', 'signal_source_kind'):
        _enum(result[name], _SIGNAL_KINDS, name)
    _enum(result['x_original_type'], _AXIS_TYPES, 'x_original_type')
    result['x_override'] = (None if value['x_override'] is None else
                            _enum(value['x_override'], _AXIS_TYPES, 'x_override'))
    result['x_is_ambiguous'] = _boolean(value['x_is_ambiguous'], 'x_is_ambiguous')
    for name in _CUBE_ARRAYS:
        result[name] = (None if name == 'photon_energy' and value[name] is None else
                        arrays.get(value[name], f'cube.{name}', ndim=2 if name == 'z' else 1,
                                   nonfinite=name == 'z'))
    columns, rows = result['energy'].size, result['gate'].size
    if (not columns or not rows or result['z'].shape != (rows, columns)
            or result['x_original'].size != columns
            or result['photon_energy'] is not None and result['photon_energy'].size != columns):
        _fail('Cube axes and Z shapes do not agree.')
    return DataCube(**result)


def _decode_processing(value):
    value = _object(value, _PROCESSING_FIELDS, 'ProcessingState')
    return ProcessingState(_enum(value['mode'], _PROCESSING_MODES, 'processing mode'),
                           _boolean(value['smooth'], 'processing smooth'),
                           _integer(value['window_length'], 'processing window'),
                           None if value['original_signal_title'] is None else
                           _string(value['original_signal_title'], 'original signal title'))


def _decode_controls(value):
    value = _object(value, _CONTROL_BOOL + _CONTROL_INT + _CONTROL_NUMBER + tuple(_CONTROL_ENUM),
                    'AnalysisControls')
    result = {name: _boolean(value[name], name) for name in _CONTROL_BOOL}
    result.update({name: _integer(value[name], name) for name in _CONTROL_INT})
    result.update({name: _number(value[name], name) for name in _CONTROL_NUMBER})
    result.update({name: _enum(value[name], choices, name) for name, choices in _CONTROL_ENUM.items()})
    # These are editable controls, not validated requests to run an algorithm.
    return AnalysisControls(**result)


def _decode_fixed(value):
    value = _object(value, ('processing', 'regions', 'region_edges'), 'FixedRecipes')
    result = {}
    for name, record in value.items():
        record = _object(record, ('enabled', 'recipe'), f'fixed {name}')
        recipe = record['recipe']
        allowed = _PROCESSING_FIELDS if name == 'processing' else ('mode', 'boundaries')
        if not isinstance(recipe, dict) or set(recipe) - set(allowed):
            _fail(f'Invalid fixed {name} recipe.')
        decoded = {}
        for field, item in recipe.items():
            if field == 'mode':
                decoded[field] = _enum(item, _PROCESSING_MODES if name == 'processing' else
                                       ('global', '2', '3'), f'fixed {name} mode')
            elif field == 'smooth':
                decoded[field] = _boolean(item, 'fixed smooth')
            elif field == 'window_length':
                decoded[field] = _integer(item, 'fixed window')
            elif field == 'original_signal_title':
                decoded[field] = None if item is None else _string(item, field)
            elif field == 'boundaries':
                decoded[field] = [_number(v, 'fixed boundary') for v in _list(item, field)]
                if len(decoded[field]) > 2:
                    _fail('Fixed region boundaries exceed the supported region count.')
        result[name] = FixedRecipe(_boolean(record['enabled'], f'fixed {name} enabled'), decoded)
    return result


def _encode_view(view):
    result = _fields(view, tuple(name for name in _VIEW_FIELDS if name != 'norm'))
    result['norm'] = view.norm.to_dict()
    return result


def _decode_presentation(value):
    # Native version 1 has complete records. Legacy sidecar defaulting must not
    # silently repair a truncated native project, but the semantic validators
    # remain the same presentation contract used by the rest of the app.
    value = _object(value, _PRESENTATION_FIELDS, 'PlotPresentation')
    _object(value['axis'], _AXIS_FIELDS, 'AxisPresentation')
    colorbar = _object(value['colorbar'], _COLORBAR_FIELDS, 'ColorbarPresentation')
    if not isinstance(colorbar['ticks'], dict):
        _fail('Colorbar ticks must be an object.')
    for record in colorbar['ticks'].values():
        _object(record, ('values', 'labels'), 'ColorbarTicks')
    for record in _list(value['annotations'], 'plot annotations'):
        _object(record, _PLOT_ANNOTATION_FIELDS, 'PlotAnnotation')
    return PlotPresentation.from_dict(value)


def _decode_view(value):
    value = _object(value, _VIEW_FIELDS, 'ViewState')
    result = {'gate': _number(value['gate'], 'view gate'),
              'colormap': _string(value['colormap'], 'colormap', nonempty=True),
              'norm': NormSpec.from_dict(_object(value['norm'], ('kind', 'linthresh'), 'NormSpec'))}
    result['annotations'] = {name: _string(item, name) for name, item in
                             _object(value['annotations'], _ANNOTATIONS, 'annotations').items()}
    for name in ('fix_annotations', 'signal_title_auto'):
        result[name] = _boolean(value[name], name)
    result['title_settings'] = validate_title_settings(value['title_settings'])
    result['bounds'] = {name: _number(item, name) for name, item in
                        _object(value['bounds'], BOUND_NAMES, 'bounds').items()}
    result['locks'] = {name: _boolean(item, name) for name, item in
                       _object(value['locks'], BOUND_NAMES, 'locks').items()}
    for name in ('x_limits', 'y_limits', 'color_limits'):
        result[name] = _pair(value[name], name)
    result['color_scale_mode'] = _enum(value['color_scale_mode'], ('global', '2', '3'), 'color mode')
    result['color_region_boundaries'] = [_number(item, 'region boundary') for item in
                                        _list(value['color_region_boundaries'], 'region boundaries')]
    if len(result['color_region_boundaries']) > 2:
        _fail('Region boundaries exceed the supported region count.')
    result['last_valid_color_limits'] = (None if value['last_valid_color_limits'] is None else
                                        _pair(value['last_valid_color_limits'], 'last valid colors'))
    result['last_valid_region_color_limits'] = [_pair(item, 'last region colors') for item in
                                               _list(value['last_valid_region_color_limits'], 'region colors')]
    if len(result['last_valid_region_color_limits']) > 3:
        _fail('Too many saved region color limits.')
    return ViewState(**result)


def _encode_fit(result, arrays):
    if result is None:
        return None
    if not isinstance(result, FitResult):
        _fail('Fit state must contain FitResult records.')
    encoded = {'model': result.model,
            **{name: arrays.add(getattr(result, name)) for name in _FIT_ARRAYS},
            **{name: _metric_encode(getattr(result, name)) for name in ('baseline', 'rmse', 'r_squared')}}
    if result.model == 'Voigt' or result.sigmas is not None or result.gammas is not None:
        encoded.update({name: None if getattr(result, name) is None else arrays.add(getattr(result, name))
                        for name in ('sigmas', 'gammas')})
    return encoded


def _decode_fit(value, arrays):
    if value is None:
        return None
    extra = ('sigmas', 'gammas') if isinstance(value, dict) and ('sigmas' in value or 'gammas' in value) else ()
    value = _object(value, _FIT_FIELDS + extra, 'FitResult')
    result = {'model': _enum(value['model'], ('Lorentzian', 'Gaussian', 'Voigt'), 'fit model')}
    for name in _FIT_ARRAYS:
        result[name] = arrays.get(value[name], f'fit.{name}', ndim=2 if name == 'components' else 1,
                                  missing=name in ('y_fit', 'components'))
    size, peaks = result['x'].size, result['centers'].size
    if (not size or result['y_fit'].size != size or result['components'].shape != (peaks, size)
            or result['amplitudes'].size != peaks or result['widths'].size != peaks):
        _fail('Fit arrays have inconsistent shapes.')
    for name in ('baseline', 'rmse', 'r_squared'):
        result[name] = _metric_decode(value[name], f'fit {name}')
    if result['rmse'] < 0:
        _fail('Fit RMSE cannot be negative.')
    for name in extra:
        result[name] = None if value[name] is None else arrays.get(value[name], f'fit.{name}', ndim=1)
    if result['model'] == 'Voigt':
        if (any(result.get(name) is None or result[name].shape != (peaks,) or np.any(result[name] <= 0)
                for name in ('sigmas', 'gammas')) or not np.array_equal(result['sigmas'], result['widths'])):
            _fail('Voigt fits require matching positive sigma and gamma components.')
    elif any(result.get(name) is not None for name in ('sigmas', 'gammas')):
        _fail('Only Voigt fits may store sigma/gamma arrays.')
    return FitResult(**result)


def _encode_gate_arrays(mapping, arrays):
    return [{'gate': _plain(key), 'array': arrays.add(value)} for key, value in sorted(mapping.items())]


def _encode_tuple_map(mapping, value_name):
    return [{'gate': _plain(key[0]), 'energy': _plain(key[1]), value_name: _plain(value)}
            for key, value in sorted(mapping.items())]


def _encode_session(session, arrays, *, analysis_science=False):
    if not isinstance(session, DatasetSession):
        _fail('Workspace owners must be DatasetSession records.')
    if not isinstance(session.graphs, dict) or any(
            not isinstance(graph, GraphState) or key != graph.graph_id
            for key, graph in session.graphs.items()):
        _fail('Graph collection keys must match graph IDs.')
    if analysis_science and any(graph.analysis is not None for graph in session.graphs.values()):
        _fail('Nested analysis science cannot contain analysis graphs.')
    same = session.current_cube is session.base_cube
    result = _fields(session, ('dataset_id', 'kind', 'loaded_fingerprint', 'peak_gate_idx',
                               'fit_gate_idx', 'csv_seeds_path', 'fit_export_rows',
                               'has_batch_fit_results', 'summary', 'initialization_notice'))
    result.update({
        'source_path': str(session.source_path) if session.source_path is not None else None,
        'base_cube': _encode_cube(session.base_cube, arrays), 'current_is_base': same,
        'current_cube': None if same else _encode_cube(session.current_cube, arrays),
        'graphs': [{'graph_id': graph_id, 'name': graph.name, 'view': _encode_view(graph.view),
                    'source_mode': graph.source_mode, 'gate_indices': _plain(graph.gate_indices),
                    'published': graph.published,
                    'presentation': graph.presentation.to_dict(),
                    **({'analysis': _encode_analysis(graph.analysis, arrays)} if graph.analysis is not None else {})}
                   for graph_id, graph in session.graphs.items()],
        'active_graph_id': session.active_graph_id,
        'applied': _fields(session.applied, _PROCESSING_FIELDS),
        'pending': _fields(session.pending, _PROCESSING_FIELDS),
        'fixed_recipes': {name: {'enabled': item.enabled, 'recipe': _plain(item.recipe)}
                          for name, item in session.fixed_recipes.items()},
        'analysis_controls': _fields(session.analysis_controls,
                                    _CONTROL_BOOL + _CONTROL_INT + _CONTROL_NUMBER + tuple(_CONTROL_ENUM)),
        'peak_indices': arrays.add(session.peak_indices),
        'peak_kind_lookup': [{'energy': _plain(key), 'peak': _plain(value)}
                             for key, value in sorted(session.peak_kind_lookup.items())],
        'peak_label_lookup': _encode_tuple_map(session.peak_label_lookup, 'label'),
        'batch_peak_map': _encode_gate_arrays(session.batch_peak_map, arrays),
        'batch_peak_kind_lookup': _encode_tuple_map(session.batch_peak_kind_lookup, 'peak'),
        'csv_seeds_map': None if session.csv_seeds_map is None else
        [{'gate': _plain(key), 'centers': _plain(value)} for key, value in sorted(session.csv_seeds_map.items())],
        'fit_result': _encode_fit(session.fit_result, arrays),
        'batch_fit_map': _encode_gate_arrays(session.batch_fit_map, arrays),
        'batch_fit_result_map': [{'gate': _plain(key), 'result': _encode_fit(value, arrays)}
                                 for key, value in sorted(session.batch_fit_result_map.items())],
    })
    return result


def _decode_records(value, keys, name, decode_key, decode_value):
    result = {}
    for record in _list(value, name):
        record = _object(record, keys, name)
        key = decode_key(record)
        if key in result:
            _fail(f'{name} contains duplicate keys.')
        result[key] = decode_value(record)
    return result


def _encode_analysis(document, arrays):
    from .analysis_document import AnalysisDocument
    if not isinstance(document, AnalysisDocument):
        _fail('Analysis graphs require AnalysisDocument records.')
    def records(mapping, field, encode):
        return [{'gate': _plain(gate), field: encode(value)} for gate, value in sorted(mapping.items())]
    def candidates(pool):
        # Fixed columns replace repeated metadata keys without discarding the
        # loose pool. Codes are explicitly checked by the decoder.
        table = np.empty((len(pool), 9), dtype=float)
        for i, item in enumerate(pool):
            table[i] = (item.index, item.position, item.amplitude,
                1 if item.kind == 'peak' else 0 if item.kind == 'dip' else np.nan,
                item.prominence, item.prominence_fraction, item.width, item.noise_strength,
                0 if item.detector == 'Original' else 1 if item.detector == 'D2' else np.nan)
        return arrays.add(table)
    def seeds(values):
        table = np.empty((len(values), 11), dtype=float)
        optional = lambda value: np.nan if value is None else value
        for i, seed in enumerate(values):
            table[i] = (seed.position, 1 if seed.kind == 'peak' else 0 if seed.kind == 'dip' else np.nan,
                seed.enabled, seed.manual, optional(seed.source_index), optional(seed.amplitude),
                optional(seed.width), optional(seed.gamma), seed.fixed_center,
                optional(seed.center_min), optional(seed.center_max))
        return {'gate': None, 'seeds': arrays.add(table), 'ids': [seed.seed_id for seed in values]}
    seed_records = []
    for gate, values in sorted(document.seeds_by_gate.items()):
        record = seeds(values)
        record['gate'] = _plain(gate)
        seed_records.append(record)
    return {
        'source_graph_id': document.source_graph_id,
        'science': _encode_session(document.science, arrays, analysis_science=True),
        'input_version': document.input_version, 'input_label': document.input_label,
        'source_mode': document.source_mode, 'gate_scope': document.gate_scope, 'revision': document.revision,
        'candidate_pools': records(document.candidate_pools, 'candidates', candidates),
        'filters': document.filters.to_dict(),
        'filters_by_gate': records(document.filters_by_gate, 'filter', lambda item: item.to_dict()),
        'seeds_by_gate': seed_records,
        'gate_status': records(document.gate_status, 'status', lambda item: item),
        'gate_messages': records(document.gate_messages, 'message', lambda item: item),
        'overlay_styles': {name: style.to_dict() for name, style in document.overlay_styles.items()},
    }


def _decode_analysis(value, arrays, version):
    from dataclasses import fields
    from .analysis_document import AnalysisDocument, AnalysisSeed, GATE_STATUSES, default_overlay_styles
    from .peak_candidates import CandidateFilter, PeakCandidate
    from .plot_presentation import ScatterStyle
    value = _object(value, ('source_graph_id', 'science', 'input_version', 'input_label', 'source_mode', 'gate_scope',
        'revision', 'candidate_pools', 'filters', 'filters_by_gate', 'seeds_by_gate', 'gate_status',
        'gate_messages', 'overlay_styles'), 'AnalysisDocument')
    owner = _decode_session(value['science'], arrays, version, analysis_science=True)
    if len(owner.graphs) != 1 or owner.current_cube is not owner.base_cube:
        _fail('Analysis science requires one graph and one bound input cube.')
    rows, columns = owner.current_cube.z.shape
    gi = lambda item: _index(item['gate'], rows, 'analysis Gate index')
    source_id = value['source_graph_id']
    if source_id is not None:
        source_id = _string(source_id, 'analysis source graph ID', nonempty=True)
    def decode_filter(record):
        record = _object(record, tuple(item.name for item in fields(CandidateFilter)), 'CandidateFilter')
        decoded = {name: _number(item, f'candidate filter {name}') for name, item in record.items()
                   if name not in ('preset', 'max_count')}
        decoded['preset'] = _string(record['preset'], 'candidate filter preset')
        decoded['max_count'] = _integer(record['max_count'], 'candidate filter count')
        return CandidateFilter.from_dict(decoded)
    try:
        document = AnalysisDocument(source_id, owner,
            input_version=_string(value['input_version'], 'analysis input version', nonempty=True),
            input_label=_string(value['input_label'], 'analysis input label'),
            source_mode=_enum(value['source_mode'], ('raw', 'processed'), 'analysis source mode'),
            gate_scope=_enum(value['gate_scope'], ('current', 'all'), 'analysis Gate scope'),
            revision=_integer(value['revision'], 'analysis revision'), filters=decode_filter(value['filters']))
        document.filters_by_gate = _decode_records(value['filters_by_gate'], ('gate', 'filter'),
            'analysis Gate filters', gi, lambda item: decode_filter(item['filter']))
        def decode_pool(item):
            gate = gi(item)
            table = arrays.get(item['candidates'], 'analysis candidate table', ndim=2)
            if table.shape[1] != 9:
                _fail('Candidate tables require exactly nine columns.')
            indices = table[:, 0]
            if (np.any(indices != np.floor(indices)) or np.any(indices < 0) or np.any(indices >= columns)
                    or not np.isin(table[:, 3], [0., 1.]).all() or not np.isin(table[:, 8], [0., 1.]).all()):
                _fail('Candidate table contains invalid indices or kind/detector codes.')
            indices = indices.astype(int)
            if (not np.array_equal(table[:, 1], owner.current_cube.energy[indices])
                    or not np.array_equal(table[:, 2], owner.current_cube.z[gate, indices])):
                _fail('Candidate coordinates do not match the bound input.')
            pool = []
            identities = set()
            for index, row in zip(indices, table):
                candidate = PeakCandidate(int(index), float(row[1]), float(row[2]),
                    'peak' if row[3] else 'dip', *map(float, row[4:8]), 'D2' if row[8] else 'Original')
                identity = index, candidate.kind
                if identity in identities:
                    _fail('Candidate pool contains duplicate peak/dip identities.')
                identities.add(identity)
                pool.append(candidate)
            return pool
        document.candidate_pools = _decode_records(value['candidate_pools'], ('gate', 'candidates'),
            'analysis candidate pools', gi, decode_pool)
        def decode_seeds(item):
            table = arrays.get(item['seeds'], 'analysis seed table', ndim=2, missing=True)
            ids = [_string(value, 'analysis seed ID', nonempty=True) for value in _list(item['ids'], 'analysis seed IDs')]
            if table.shape != (len(ids), 11):
                _fail('Seed tables require eleven columns and one identity per row.')
            if len(set(ids)) != len(ids):
                _fail('Analysis seeds contain duplicate identities.')
            if (not np.isfinite(table[:, [0, 1, 2, 3, 8]]).all()
                    or not np.isin(table[:, [1, 2, 3, 8]], [0., 1.]).all()):
                _fail('Seed table requires finite positions and boolean codes.')
            indices = table[:, 4]
            finite = np.isfinite(indices)
            if np.any(indices[finite] != np.floor(indices[finite])) or np.any(indices[finite] < 0) or np.any(indices[finite] >= columns):
                _fail('Seed table contains invalid source indices.')
            seeds = []
            optional = lambda value: None if np.isnan(value) else float(value)
            for seed_id, row in zip(ids, table):
                seeds.append(AnalysisSeed(float(row[0]), 'peak' if row[1] else 'dip', bool(row[2]), bool(row[3]),
                    None if np.isnan(row[4]) else int(row[4]), *map(optional, row[5:8]), bool(row[8]),
                    optional(row[9]), optional(row[10]), seed_id))
            return seeds
        document.seeds_by_gate = _decode_records(value['seeds_by_gate'], ('gate', 'seeds', 'ids'),
            'analysis Gate seeds', gi, decode_seeds)
        document.gate_status = _decode_records(value['gate_status'], ('gate', 'status'),
            'analysis Gate status', gi, lambda item: _enum(item['status'], GATE_STATUSES, 'analysis Gate status'))
        document.gate_messages = _decode_records(value['gate_messages'], ('gate', 'message'),
            'analysis Gate messages', gi, lambda item: _string(item['message'], 'analysis Gate message'))
        styles = _object(value['overlay_styles'], default_overlay_styles(), 'analysis overlay styles')
        document.overlay_styles = {name: ScatterStyle.from_dict(record) for name, record in styles.items()}
        for gate, status in document.gate_status.items():
            if status == 'fitted' and gate not in owner.batch_fit_result_map and not (
                    owner.fit_gate_idx == gate and owner.fit_result is not None):
                _fail('Fitted analysis Gate has no retained fit.')
        return document
    except ProjectFormatError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ProjectFormatError(f'Invalid analysis document: {exc}') from exc


def _json_progress(value, depth=0):
    """Restrict optional XY editor progress to finite, plain JSON state."""
    if depth > 24:
        _fail('XY analysis progress is nested too deeply.')
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) in (int, float):
        if not math.isfinite(value):
            _fail('XY analysis progress contains nonfinite values.')
        return value
    if isinstance(value, list):
        return [_json_progress(item, depth+1) for item in value]
    if isinstance(value, dict):
        return {_string(key, 'XY analysis field'): _json_progress(item, depth+1) for key, item in value.items()}
    _fail('XY analysis progress requires plain JSON values.')


def _decode_session(value, arrays, version=SCHEMA_VERSION, *, analysis_science=False):
    if version == 1:
        value = _object(value, _V1_SESSION_FIELDS, 'DatasetSession')
        value = dict(value)
        value['graphs'] = [{'graph_id': 'default', 'name': 'Graph 1',
                            'view': value.pop('view'), 'presentation': value.pop('presentation')}]
        value['active_graph_id'] = 'default'
    value = _object(value, _SESSION_FIELDS, 'DatasetSession')
    graphs, names, analysis_records = {}, set(), {}
    for record in _list(value['graphs'], 'graphs'):
        fields = ('graph_id', 'name', 'view', 'presentation')
        if version >= 7:
            fields += ('source_mode', 'gate_indices')
        if version >= 8:
            fields += ('published',)
        if version >= 9 and isinstance(record, dict) and 'analysis' in record:
            if analysis_science:
                _fail('Nested analysis science cannot contain analysis graphs.')
            fields += ('analysis',)
        record = _object(record, fields, 'GraphState')
        graph_id = _string(record['graph_id'], 'graph ID', nonempty=True)
        name = _string(record['name'], 'graph name', nonempty=True)
        if graph_id in graphs or name in names or name != name.strip():
            _fail('Graph IDs and trimmed names must be unique.')
        graphs[graph_id] = GraphState(graph_id, name, _decode_view(record['view']),
                                      _decode_presentation(record['presentation']),
                                      record.get('source_mode', 'processed'), record.get('gate_indices'),
                                      _boolean(record['published'], 'graph publication') if version >= 8 else True)
        names.add(name)
        if 'analysis' in record:
            analysis_records[graph_id] = record['analysis']
    active_graph_id = _string(value['active_graph_id'], 'active graph ID', nonempty=True)
    if not graphs or active_graph_id not in graphs:
        _fail('Every DAT requires graphs and a valid active graph.')
    source = Path(_string(value['source_path'], 'dataset source path', nonempty=True))
    dataset_id = _string(value['dataset_id'], 'dataset ID', nonempty=True)
    base = _decode_cube(value['base_cube'], arrays)
    same = _boolean(value['current_is_base'], 'cube alias')
    if same and value['current_cube'] is not None:
        _fail('Aliased current cube must use the base cube reference.')
    current = base if same else _decode_cube(value['current_cube'], arrays)
    for cube in (base, current):
        if _path_key(cube.source_path) != _path_key(source):
            _fail('Cube provenance does not match its dataset owner.')
    if not same:
        for name in _CUBE_ARRAYS:
            if name == 'z':
                if base.z.shape != current.z.shape:
                    _fail('Base and current Z shapes disagree.')
                continue
            a, b = getattr(base, name), getattr(current, name)
            if (a is None) != (b is None) or a is not None and not np.array_equal(a, b):
                _fail('Base and current axes disagree.')
        for name in _CUBE_STRINGS + ('x_is_ambiguous', 'x_override'):
            if getattr(base, name) != getattr(current, name):
                _fail('Base and current cube metadata disagree.')
    rows, columns = current.z.shape
    for graph in graphs.values():
        if graph.graph_id in analysis_records:
            continue  # Validate display scope against this document's bound cube.
        try:
            validate_graph_source(graph.source_mode, graph.gate_indices, rows)
        except ValueError as exc:
            _fail(str(exc))
    gi = lambda item: _index(item['gate'], rows, 'gate index')
    ei = lambda item: _index(item['energy'], columns, 'energy index')
    pair = lambda item: (gi(item), ei(item))

    def index_array(reference, name):
        array = arrays.get(reference, name, ndim=1, indices=True)
        if np.any(array < 0) or np.any(array >= columns) or np.unique(array).size != array.size:
            _fail(f'{name} contains invalid or duplicate indices.')
        return array

    fingerprint = value['loaded_fingerprint']
    session = DatasetSession(
        dataset_id=dataset_id, kind=_enum(value['kind'], ('spectrum_matrix',), 'dataset kind'),
        source_path=source,
        loaded_fingerprint=None if fingerprint is None else _string(fingerprint, 'fingerprint'),
        base_cube=base, current_cube=current, graphs=graphs, active_graph_id=active_graph_id,
        applied=_decode_processing(value['applied']), pending=_decode_processing(value['pending']),
        fixed_recipes=_decode_fixed(value['fixed_recipes']),
        analysis_controls=_decode_controls(value['analysis_controls']),
        peak_indices=index_array(value['peak_indices'], 'peak indices'),
        peak_gate_idx=_index(value['peak_gate_idx'], rows, 'peak gate', optional=True),
        fit_gate_idx=_index(value['fit_gate_idx'], rows, 'fit gate', optional=True),
        csv_seeds_path=_string(value['csv_seeds_path'], 'CSV source path'),
        fit_result=_decode_fit(value['fit_result'], arrays),
        has_batch_fit_results=_boolean(value['has_batch_fit_results'], 'batch fit state'),
        summary=_string(value['summary'], 'summary'),
        initialization_notice=_string(value['initialization_notice'], 'initialization notice'),
        sidecar_writes_enabled=False)
    session.peak_kind_lookup = _decode_records(value['peak_kind_lookup'], ('energy', 'peak'),
                                               'peak kinds', ei, lambda item: _boolean(item['peak'], 'peak kind'))
    session.peak_label_lookup = _decode_records(value['peak_label_lookup'], ('gate', 'energy', 'label'),
                                                'peak labels', pair, lambda item: _integer(item['label'], 'peak label'))
    session.batch_peak_kind_lookup = _decode_records(
        value['batch_peak_kind_lookup'], ('gate', 'energy', 'peak'), 'batch peak kinds', pair,
        lambda item: _boolean(item['peak'], 'batch peak kind'))
    session.batch_peak_map = _decode_records(value['batch_peak_map'], ('gate', 'array'), 'batch peaks', gi,
                                              lambda item: index_array(item['array'], 'batch peak indices'))
    session.batch_fit_map = _decode_records(value['batch_fit_map'], ('gate', 'array'), 'batch centers', gi,
                                             lambda item: arrays.get(item['array'], 'fit centers', ndim=1))
    session.batch_fit_result_map = _decode_records(value['batch_fit_result_map'], ('gate', 'result'),
                                                    'batch fits', gi, lambda item: _decode_fit(item['result'], arrays))
    if any(result is None for result in session.batch_fit_result_map.values()):
        _fail('Batch fit records cannot be missing.')
    session.csv_seeds_map = (None if value['csv_seeds_map'] is None else _decode_records(
        value['csv_seeds_map'], ('gate', 'centers'), 'CSV seeds', gi,
        lambda item: [_number(center, 'CSV center') for center in _list(item['centers'], 'CSV centers')]))
    session.fit_export_rows = []
    for row in _list(value['fit_export_rows'], 'fit export rows'):
        if not isinstance(row, dict):
            _fail('Fit export rows must be string mappings.')
        session.fit_export_rows.append({_string(key, 'export field'): _string(item, 'export value')
                                        for key, item in row.items()})
    for graph_id, record in analysis_records.items():
        document = _decode_analysis(record, arrays, version)
        graph = graphs[graph_id]
        graph.analysis = document
        if document.science.source_path != source or document.source_mode != graph.source_mode:
            _fail('Analysis provenance does not match its graph and dataset.')
        try:
            validate_graph_source(graph.source_mode, graph.gate_indices, document.science.current_cube.gate.size)
        except ValueError as exc:
            _fail(str(exc))
    owner_ids, input_versions = {session.dataset_id}, set()
    for graph in graphs.values():
        if graph.analysis is not None and graph.analysis.source_graph_id is not None:
            source_graph = graphs.get(graph.analysis.source_graph_id)
            if source_graph is None or source_graph.analysis is not None:
                _fail('Analysis source link is missing, cyclic, or references another analysis.')
        if graph.analysis is not None:
            document = graph.analysis
            if document.science.dataset_id in owner_ids or document.input_version in input_versions:
                _fail('Analysis owners and input versions must have independent identities.')
            owner_ids.add(document.science.dataset_id)
            input_versions.add(document.input_version)
    return session


def _encode_workbooks(workspace, arrays):
    sheets = []
    for sheet in workspace.sheets.values():
        record = _fields(sheet, tuple(name for name in _SHEET_FIELDS if name not in ('source_path', 'data')))
        record['source_path'] = None if sheet.source_path is None else str(sheet.source_path)
        record['data'] = None if sheet.data is None else arrays.add(sheet.data)
        record['text_columns'] = {str(key): values for key, values in sheet.text_columns.items()}
        sheets.append(record)
    return {
        'workbooks': [_fields(book, _WORKBOOK_FIELDS) for book in workspace.workbooks.values()],
        'sheets': sheets,
        'comparisons': [{**_fields(comparison, _COMPARISON_FIELDS[:-1]),
                         'curves': [_fields(curve, _CURVE_FIELDS) for curve in comparison.curves.values()],
                         **({'analysis_state': _json_progress(comparison.analysis_state)}
                            if getattr(comparison, 'analysis_state', None) is not None else {})}
                        for comparison in workspace.comparisons.values()]}


def _decode_workbooks(manifest, workspace, arrays, version):
    for record in _list(manifest['workbooks'], 'workbooks'):
        record = _object(record, _WORKBOOK_FIELDS, 'Workbook')
        key = _string(record['workbook_id'], 'workbook ID', nonempty=True)
        if key in workspace.workbooks:
            _fail('Workbook IDs must be unique.')
        workspace.workbooks[key] = Workbook(key, _string(record['name'], 'workbook name', nonempty=True),
            [_string(key, 'Sheet member ID', nonempty=True) for key in _list(record['sheet_ids'], 'Sheet members')])
    for record in _list(manifest['sheets'], 'sheets'):
        record = _object(record, _SHEET_FIELDS if version >= 6 else _V5_SHEET_FIELDS, 'Sheet')
        key = _string(record['sheet_id'], 'Sheet ID', nonempty=True)
        if key in workspace.sheets:
            _fail('Sheet IDs must be unique.')
        source = record['source_path']
        source = None if source is None else Path(_string(source, 'Sheet source path', nonempty=True))
        data = None if record['data'] is None else arrays.get(record['data'], 'XY Sheet', ndim=2, nonfinite=True)
        dataset_id = record['dataset_id']
        if dataset_id is not None:
            dataset_id = _string(dataset_id, 'Sheet dataset reference', nonempty=True)
        workspace.sheets[key] = Sheet(key, _string(record['name'], 'Sheet name', nonempty=True),
            _enum(record['kind'], ('xy', 'dat', 'snapshot') if version >= 6 else ('xy', 'dat'), 'Sheet source kind'), source, data,
            [_string(name, 'column label', nonempty=True) for name in _list(record['column_names'], 'column labels')],
            dataset_id, _integer(record['x_column'], 'X column'),
            [_integer(index, 'Y column') for index in _list(record['y_columns'], 'Y columns')],
            _enum(record['mode'], ('raw', 'processed'), 'Sheet mode'))
        if version >= 6:
            sheet = workspace.sheets[key]
            sheet.role = _enum(record['role'], ('original', 'processed', 'fit_parameters', 'fit_curves', 'snapshot'), 'Sheet role')
            sheet.fit_gate_idx = None if record['fit_gate_idx'] is None else _integer(record['fit_gate_idx'], 'Fit Gate index')
            sheet.snapshot_note = _string(record['snapshot_note'], 'Snapshot provenance')
            if not isinstance(record['text_columns'], dict):
                _fail('Text columns must be a mapping.')
            for column, values in record['text_columns'].items():
                if not isinstance(column, str) or not column.isascii() or not column.isdecimal() or str(int(column)) != column:
                    _fail('Text column keys must be canonical nonnegative indices.')
                sheet.text_columns[int(column)] = [_string(value, 'Text cell') for value in _list(values, 'Text column')]
    for record in _list(manifest['comparisons'], 'comparisons'):
        comparison_fields = (_COMPARISON_FIELDS if version >= 7 else
                             _V6_COMPARISON_FIELDS if version >= 5 else _V4_COMPARISON_FIELDS)
        if version >= 9 and isinstance(record, dict) and 'analysis_state' in record:
            comparison_fields += ('analysis_state',)
        record = _object(record, comparison_fields, 'Comparison')
        key = _string(record['comparison_id'], 'comparison ID', nonempty=True)
        if key in workspace.comparisons:
            _fail('Comparison IDs must be unique.')
        comparison = Comparison(key, _string(record['name'], 'comparison name', nonempty=True),
            _string(record['x_label'], 'X axis label'), _string(record['y_label'], 'Y axis label'),
            _boolean(record['legend_visible'], 'legend visibility'))
        comparison.plot_title = _string(record['plot_title'], 'Plot title') if version >= 7 else comparison.name
        if 'analysis_state' in record:
            if not isinstance(record['analysis_state'], dict):
                _fail('XY analysis progress requires an object.')
            comparison.analysis_state = _json_progress(record['analysis_state'])
        if version >= 5:
            comparison.normalization = _enum(record['normalization'], ('none', 'maxabs', 'minmax'), 'normalization')
            comparison.stack_step = _number(record['stack_step'], 'stack step')
            comparison.x_range_mode = _enum(record['x_range_mode'], ('all', 'common', 'manual'), 'X range mode')
            if record['x_range'] is not None:
                bounds = _list(record['x_range'], 'X range')
                if len(bounds) != 2:
                    _fail('X range requires two bounds.')
                comparison.x_range = tuple(_number(value, 'X range bound') for value in bounds)
        for item in _list(record['curves'], 'curves'):
            item = _object(item, _CURVE_FIELDS if version >= 7 else
                           _V6_CURVE_FIELDS if version >= 5 else _V4_CURVE_FIELDS, 'Curve')
            curve_id = _string(item['curve_id'], 'curve ID', nonempty=True)
            if curve_id in comparison.curves:
                _fail('Curve IDs must be unique within their comparison.')
            comparison.curves[curve_id] = Curve(curve_id, _string(item['sheet_id'], 'curve Sheet', nonempty=True),
                _integer(item['x_column'], 'curve X column'), _integer(item['y_column'], 'curve Y column'),
                _enum(item['mode'], ('raw', 'processed'), 'curve mode'), _string(item['label'], 'curve label'),
                _boolean(item['visible'], 'curve visibility'), _string(item['color'], 'curve color'),
                _number(item['width'], 'curve width'),
                _number(item['y_offset'], 'curve Y offset') if version >= 5 else 0.0,
                _enum(item['style'], ('line', 'scatter', 'line_symbol'), 'curve style') if version >= 7 else 'line')
        workspace.comparisons[key] = comparison
    if version < 6:
        members = [key for book in workspace.workbooks.values() for key in book.sheet_ids]
        if len(members) != len(set(members)) or set(members) != set(workspace.sheets):
            _fail('Each legacy Sheet requires exactly one workbook owner.')
        dataset_refs = [s.dataset_id for s in workspace.sheets.values() if s.kind == 'dat']
        if len(dataset_refs) != len(set(dataset_refs)) or not set(dataset_refs).issubset(workspace.datasets):
            _fail('Legacy DAT Sheets require unique existing datasets.')
        try:
            for sheet in workspace.sheets.values():
                _validate_sheet_source(workspace, replace(sheet, mode='raw') if sheet.kind == 'dat' else sheet)
                validate_mapping(workspace, sheet, sheet.x_column, sheet.y_columns, sheet.mode)
            for comparison in workspace.comparisons.values():
                for curve in comparison.curves.values():
                    validate_mapping(workspace, workspace.sheets[curve.sheet_id],
                                     curve.x_column, [curve.y_column], curve.mode)
        except (ValueError, TypeError, KeyError) as exc:
            raise ProjectFormatError(f'Invalid legacy workbook state: {exc}') from exc
        # Legacy DAT identity remains Original. Processed references move without
        # changing Curve identity, style, ordering or the old Workbook owner.
        for sheet in list(workspace.sheets.values()):
            if sheet.kind == 'dat':
                if sheet.mode == 'processed':
                    processed = ensure_dataset_sheet(workspace, sheet.dataset_id, 'processed')
                    processed.x_column, processed.y_columns = sheet.x_column, list(sheet.y_columns)
                sheet.mode = 'raw'
        for comparison in workspace.comparisons.values():
            for curve in comparison.curves.values():
                sheet = workspace.sheets.get(curve.sheet_id)
                if sheet is not None and sheet.kind == 'dat' and curve.mode == 'processed':
                    curve.sheet_id = ensure_dataset_sheet(workspace, sheet.dataset_id, 'processed').sheet_id
        for dataset_id in workspace.datasets:
            reconcile_dataset_sheets(workspace, dataset_id)
    try:
        validate_workbook_state(workspace)
    except (ValueError, TypeError, KeyError) as exc:
        raise ProjectFormatError(f'Invalid workbook state: {exc}') from exc


def _decode_graph_workspace(value, workspace):
    if value is None:
        return None
    value = _object(value, ('mode', 'documents', 'active', 'stacking'), 'graph workspace')
    mode = _enum(value['mode'], ('free', 'tabbed'), 'graph workspace mode')
    records, identities, opened = [], set(), set()

    def identity(value, name):
        if not isinstance(value, list):
            _fail(f'{name} must be an identity array.')
        try:
            return decode_document_key(value)
        except ValueError as exc:
            raise ProjectFormatError(f'{name}: {exc}') from exc

    for record in _list(value['documents'], 'graph documents'):
        if isinstance(record, dict) and 'key' in record:
            record = _object(record, ('key', 'open', 'rect'), 'graph document')
            key = identity(record['key'], 'document identity')
        else:
            record = _object(record, ('dataset_id', 'graph_id', 'open', 'rect'), 'graph document')
            key = identity([record['dataset_id'], record['graph_id']], 'graph identity')
        if not document_exists(workspace, key) or key in identities:
            _fail('Workspace identities must be unique existing documents.')
        if len(key) == 2 and not workspace.datasets[key[0]].graphs[key[1]].published:
            _fail('Workspace cannot reference an unpublished graph document.')
        identities.add(key)
        is_open = _boolean(record['open'], 'graph open state')
        if is_open:
            opened.add(key)
        rect = record['rect']
        if rect is not None:
            if not isinstance(rect, list) or len(rect) != 4:
                _fail('Graph rectangle requires x, y, width and height.')
            rect = [_integer(item, 'graph rectangle') for item in rect]
            if (any(abs(item) > 100000 for item in rect[:2])
                    or any(not 1 <= item <= 16384 for item in rect[2:])):
                _fail('Graph rectangle is outside supported bounds.')
        records.append(dict(key=list(key), open=is_open, rect=rect))
    stacking = [identity(key, 'graph stacking identity') for key in _list(value['stacking'], 'graph stacking')]
    if len(set(stacking)) != len(stacking) or set(stacking) != opened:
        _fail('Graph stacking must include every open graph exactly once.')
    active = identity(value['active'], 'active graph identity') if value['active'] is not None else None
    if active is not None and active not in opened:
        _fail('Active graph view must be open.')
    return dict(mode=mode, documents=records, active=list(active) if active else None,
                stacking=[list(key) for key in stacking])


def _decode_workspace(manifest, array_values):
    if (not isinstance(manifest, dict) or manifest.get('format') != 'spectra-project'
            or type(manifest.get('version')) is not int or manifest['version'] not in range(1, SCHEMA_VERSION+1)):
        _fail('Unsupported Spectra project format or version.')
    fields = ('format', 'version', 'active_dataset_id', 'datasets')
    if manifest['version'] >= 3:
        fields += ('graph_workspace',)
    if manifest['version'] >= 4:
        fields += ('workbooks', 'sheets', 'comparisons')
    manifest = _object(manifest, fields, 'manifest')
    arrays = _ArrayReader(array_values)
    workspace = Workspace()
    for record in _list(manifest['datasets'], 'datasets'):
        session = _decode_session(record, arrays, manifest['version'])
        key = _path_key(session.source_path)
        if session.dataset_id in workspace.datasets:
            _fail('Dataset IDs must be unique.')
        workspace.datasets[session.dataset_id] = session
        workspace.path_index.setdefault(key, session.dataset_id)
    active = manifest['active_dataset_id']
    if active is not None:
        active = _string(active, 'active dataset ID', nonempty=True)
        if active not in workspace.datasets:
            _fail('Active dataset does not exist.')
    if manifest['version'] >= 4:
        _decode_workbooks(manifest, workspace, arrays, manifest['version'])
    else:
        for dataset_id in workspace.datasets:
            reconcile_dataset_sheets(workspace, dataset_id)
    if arrays.used != set(array_values):
        _fail('Archive contains unreferenced scientific arrays.')
    workspace.active_dataset_id = active
    workspace.graph_workspace = _decode_graph_workspace(manifest.get('graph_workspace'), workspace)
    return workspace


def _json_bytes(manifest):
    try:
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise ProjectFormatError(f'Project metadata is invalid: {exc}') from exc
    if len(encoded) > MAX_MANIFEST_BYTES:
        _fail('Project manifest exceeds the supported size.')
    return encoded


def _snapshot(workspace):
    try:
        if not isinstance(workspace, Workspace) or not isinstance(workspace.datasets, dict):
            _fail('Project requires a Workspace.')
        arrays = _ArrayWriter()
        for key, session in workspace.datasets.items():
            if key != session.dataset_id:
                _fail('Workspace keys must match dataset IDs.')
        validate_workbook_state(workspace)
        manifest = {'format': 'spectra-project', 'version': SCHEMA_VERSION,
                    'active_dataset_id': workspace.active_dataset_id,
                    'graph_workspace': workspace.graph_workspace,
                    'datasets': [_encode_session(session, arrays) for session in workspace.datasets.values()]}
        manifest.update(_encode_workbooks(workspace, arrays))
        # Preserve ordinary v8 output; use v9 only for its additional scientific
        # records, keeping existing project/sidecar migration contracts stable.
        if not _needs_analysis_schema(workspace):
            manifest['version'] = 8
        # Apply the same complete validation to saves and loads, before touching disk.
        encoded = _json_bytes(manifest)
        normalized = _decode_workspace(json.loads(encoded), arrays.arrays)
        # Float-valued fields can legitimately contain integer literals in
        # Python. Canonicalize them through the typed decoder so a load keeps
        # the exact same content digest; reuse the already detached array copy.
        canonical_arrays = _ArrayWriter(copy_arrays=False)
        manifest['datasets'] = [_encode_session(session, canonical_arrays)
                                for session in normalized.datasets.values()]
        manifest.update(_encode_workbooks(normalized, canonical_arrays))
        manifest['graph_workspace'] = normalized.graph_workspace
        encoded = _json_bytes(manifest)
        if len(encoded) > MAX_MEMBER_BYTES:
            _fail('Project manifest exceeds the supported member size.')
        if len(encoded) + canonical_arrays.total_bytes > MAX_TOTAL_BYTES:
            _fail('Workspace metadata and arrays exceed the supported expanded size.')
        return encoded, canonical_arrays.arrays
    except ProjectFormatError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, OverflowError, RecursionError) as exc:
        raise ProjectFormatError(f'Invalid project snapshot: {exc}') from exc


def _needs_analysis_schema(workspace):
    for session in workspace.datasets.values():
        if any(graph.analysis is not None for graph in session.graphs.values()):
            return True
        if session.analysis_controls.fit_model == 'Voigt' or any(result is not None and (
                result.model == 'Voigt' or result.sigmas is not None or result.gammas is not None)
                for result in (session.fit_result, *session.batch_fit_result_map.values())):
            return True
    return any(getattr(comparison, 'analysis_state', None) is not None for comparison in workspace.comparisons.values())


def _digest(manifest_bytes, arrays):
    digest = hashlib.sha256()

    def part(value):
        digest.update(len(value).to_bytes(8, 'big'))
        digest.update(value)

    part(manifest_bytes)
    for name, array in arrays.items():
        part(name.encode('ascii'))
        part(array.dtype.str.encode('ascii'))
        part(json.dumps(array.shape, separators=(',', ':')).encode('ascii'))
        part(array.tobytes(order='C'))
    return digest.hexdigest()


def _runtime_array_hash(array):
    """Content hash without retaining/copying ordinary contiguous buffers."""
    array = np.asarray(array)
    if not array.size:
        return hashlib.sha256(b'').hexdigest()
    contiguous = array if array.flags.c_contiguous else np.ascontiguousarray(array)
    return hashlib.sha256(memoryview(contiguous).cast('B')).hexdigest()


def runtime_workspace_stamp(workspace: Workspace, cache: dict) -> str:
    """Lightweight title status; the exact archive digest still guards destruction.

    Supported edits replace frozen cube/FitResult buffers. Verify cached identity
    with weak references and retain only buffers still owned by this workspace.
    Mutable peak/index/center maps are always hashed, even between redraws.
    """
    stable = {}
    mutable = set()
    for session in workspace.datasets.values():
        for cube in (session.base_cube, session.current_cube):
            if cube is not None:
                for name in _CUBE_ARRAYS:
                    array = getattr(cube, name)
                    if array is not None:
                        stable[id(array)] = array
        for result in (session.fit_result, *session.batch_fit_result_map.values()):
            if result is not None:
                for name in ('x', 'y_fit', 'components'):
                    array = getattr(result, name)
                    stable[id(array)] = array
        mutable.update(id(array) for array in (
            session.peak_indices, *session.batch_peak_map.values(), *session.batch_fit_map.values()))
    for key in list(cache):
        if key not in stable or key in mutable or cache[key][0]() is not stable[key]:
            del cache[key]

    class ArrayStamps:
        def add(self, value):
            array = np.asarray(value)
            key = id(array)
            cached = cache.get(key)
            signature = (array.dtype.str, tuple(array.shape))
            if (key in stable and key not in mutable and cached is not None
                    and cached[0]() is array and cached[1][:2] == signature):
                return cached[1]
            stamp = (*signature, _runtime_array_hash(array))
            if key in stable and key not in mutable:
                cache[key] = (weakref.ref(array), stamp)
            return stamp

    def canonical_numbers(value):
        # The archive decoder normalizes numeric metadata; equivalently collapse
        # integral floats here without decoding/scanning scientific arrays.
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {key: canonical_numbers(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [canonical_numbers(item) for item in value]
        return value

    collector = ArrayStamps()
    metadata = {'active_dataset_id': workspace.active_dataset_id,
                'graph_workspace': _decode_graph_workspace(workspace.graph_workspace, workspace),
                'datasets': [_encode_session(session, collector) for session in workspace.datasets.values()]}
    metadata.update(_encode_workbooks(workspace, collector))
    encoded = json.dumps(canonical_numbers(metadata), sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def workspace_digest(workspace: Workspace) -> str:
    """Hash all durable content, excluding path indices and sidecar ownership."""
    return _digest(*_snapshot(workspace))


def write_project(path: str | Path, workspace: Workspace) -> str:
    """Atomically replace a project; return only the successfully saved digest."""
    manifest, arrays = _snapshot(workspace)
    digest = _digest(manifest, arrays)
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w+b', dir=destination.parent,
                                         prefix=f'.{destination.name}.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                archive.writestr('manifest.json', manifest)
                for name, array in arrays.items():
                    with archive.open(name, 'w', force_zip64=True) as member:
                        np.save(member, array, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return digest


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('Project JSON contains duplicate keys.')
        result[key] = value
    return result


def _invalid_json_constant(value):
    _fail(f'Project JSON contains a nonstandard number: {value}.')


def _read_array(archive, info):
    # Inspect the small NPY header before np.load can allocate an attacker shape.
    with archive.open(info) as member:
        version = np.lib.format.read_magic(member)
        if version not in ((1, 0), (2, 0)):
            _fail('Unsupported NPY format version.')
        length_size = 2 if version == (1, 0) else 4
        length_bytes = member.read(length_size)
        if len(length_bytes) != length_size:
            _fail('Truncated NPY header length.')
        header_size = struct.unpack('<H' if length_size == 2 else '<I', length_bytes)[0]
        if header_size > MAX_NPY_HEADER_BYTES or 8 + length_size + header_size > info.file_size:
            _fail('NPY header exceeds its size limit or member payload.')
        member.seek(8)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(member, max_header_size=MAX_NPY_HEADER_BYTES)
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(member, max_header_size=MAX_NPY_HEADER_BYTES)
        size = _array_bounds(shape, dtype)
        if type(fortran) is not bool or member.tell() + size != info.file_size:
            _fail('NPY header and member payload size do not agree.')
    with archive.open(info) as member:
        array = np.load(member, allow_pickle=False, max_header_size=MAX_NPY_HEADER_BYTES)
        if array.shape != shape or array.dtype != dtype or member.read(1):
            _fail('NPY member content disagrees with its header.')
    return array


def read_project(path: str | Path) -> Workspace:
    """Fully validate and decode a detached workspace without source-file I/O."""
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_MEMBERS:
                _fail('Archive has an unsupported member count.')
            names = set()
            total = 0
            for info in entries:
                if info.filename in names:
                    _fail('Archive contains duplicate members.')
                names.add(info.filename)
                if info.filename != 'manifest.json' and not _ARRAY_NAME.fullmatch(info.filename):
                    _fail('Archive contains an unexpected or unsafe member name.')
                if info.flag_bits & 0x41 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    _fail('Encrypted or unsupported compressed archive members are not allowed.')
                if info.is_dir() or info.file_size > MAX_MEMBER_BYTES:
                    _fail('Archive member exceeds the supported size.')
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    _fail('Expanded archive exceeds the supported size.')
            if 'manifest.json' not in names:
                _fail('Archive is missing manifest.json.')
            manifest_info = archive.getinfo('manifest.json')
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                _fail('Project manifest exceeds the supported size.')
            manifest = json.loads(archive.read(manifest_info).decode('utf-8'),
                                  object_pairs_hook=_unique_json_object,
                                  parse_constant=_invalid_json_constant)
            arrays = {info.filename: _read_array(archive, info) for info in entries if info.filename != 'manifest.json'}
            return _decode_workspace(manifest, arrays)
    except ProjectFormatError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, UnicodeError,
            EOFError, RecursionError, zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError) as exc:
        raise ProjectFormatError(f'Invalid Spectra project: {exc}') from exc
