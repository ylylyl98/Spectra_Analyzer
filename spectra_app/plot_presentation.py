"""Ordinary, validated formal-page settings and detached scientific snapshots.

These records own no Qt object, artist, or job. Presentation dictionaries are
the sidecar/template boundary; snapshots copy only the heatmap data needed by
the renderer. Use ``dataclasses.replace`` to change an immutable setting.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
import math
import re
from typing import Mapping

import numpy as np


def _number(value, name: str, *, positive: bool = False) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f'{name} must be a finite number.')
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f'{name} must be a finite {"positive " if positive else ""}number.')
    return result


def _integer(value, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}.')
    return int(value)


def _string(value, name: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f'{name} must be {"a nonempty" if nonempty else "a"} string.')
    return value


def _boolean(value, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f'{name} must be a boolean.')
    return value


def _pair(value, name: str) -> tuple[float, float]:
    if not isinstance(value, (tuple, list, np.ndarray)) or len(value) != 2:
        raise ValueError(f'{name} requires two finite numbers.')
    return tuple(_number(v, name) for v in value)


def _limits(value, name: str) -> tuple[float, float]:
    result = _pair(value, name)
    if result[0] >= result[1]:
        raise ValueError(f'{name} requires lower < upper.')
    return result


def _identifier(value, name: str = 'id') -> str:
    value = _string(value, name, nonempty=True)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]*', value):
        raise ValueError(f'{name} must start with a letter and contain letters, digits, _, . or -.')
    return value


def _encode(value):
    if isinstance(value, _Record):
        return value.to_dict()
    if isinstance(value, np.ndarray):
        # Missing scientific cells have an explicit JSON null representation.
        return _encode(value.tolist())
    if isinstance(value, Mapping):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


class _Record:
    def to_dict(self) -> dict:
        return {item.name: _encode(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def from_dict(cls, payload: dict):
        if not isinstance(payload, dict):
            raise ValueError(f'{cls.__name__} must be an object.')
        unknown = payload.keys() - {item.name for item in fields(cls)}
        if unknown:
            raise ValueError(f'Unknown {cls.__name__} field: {sorted(unknown)[0]}.')
        try:
            return cls(**payload)
        except TypeError as exc:
            raise ValueError(f'Invalid {cls.__name__}: {exc}') from exc


@dataclass(frozen=True)
class NormSpec(_Record):
    kind: str = 'linear'
    linthresh: float = 1.0

    def __post_init__(self):
        if self.kind not in ('linear', 'log', 'symlog', 'zero-centered'):
            raise ValueError('Color mapping must be linear, log, symlog or zero-centered.')
        object.__setattr__(self, 'linthresh', _number(self.linthresh, 'SymLog linthresh threshold', positive=True))


@dataclass(frozen=True)
class AxisPresentation(_Record):
    exchange_xy: bool = False
    flip_x: bool = False
    flip_y: bool = False
    show_x: bool = True
    show_y: bool = True
    show_x_label: bool = True
    show_y_label: bool = True
    show_tick_labels: bool = True
    major_ticks: bool = True
    minor_ticks: bool = True
    ticks_inside: bool = True
    border: bool = True
    border_width: float = 1.2
    x_major_step: float | None = None
    y_major_step: float | None = None
    x_major_count: int | None = None
    y_major_count: int | None = None
    x_minor_count: int = 1
    y_minor_count: int = 1

    def __post_init__(self):
        for name in ('exchange_xy', 'flip_x', 'flip_y', 'show_x', 'show_y', 'show_x_label',
                     'show_y_label', 'show_tick_labels', 'major_ticks', 'minor_ticks', 'ticks_inside', 'border'):
            _boolean(getattr(self, name), name)
        object.__setattr__(self, 'border_width', _number(self.border_width, 'border_width', positive=True))
        for name in ('x_major_step', 'y_major_step'):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, _number(getattr(self, name), name, positive=True))
        for prefix in ('x', 'y'):
            count = getattr(self, f'{prefix}_major_count')
            if count is not None:
                count = _integer(count, f'{prefix}_major_count', minimum=2)
                if count > 100:
                    raise ValueError('Major tick count must be between 2 and 100.')
                if getattr(self, f'{prefix}_major_step') is not None:
                    raise ValueError('Choose either a major tick increment or a count.')
                object.__setattr__(self, f'{prefix}_major_count', count)
            minor = _integer(getattr(self, f'{prefix}_minor_count'), f'{prefix}_minor_count')
            if minor > 20:
                raise ValueError('Minor tick count must be between 0 and 20.')
            object.__setattr__(self, f'{prefix}_minor_count', minor)


@dataclass(frozen=True)
class ColorbarTicks(_Record):
    values: tuple[float, float, float]
    labels: tuple[str, str, str] | None = None

    def __post_init__(self):
        if not isinstance(self.values, (tuple, list)) or len(self.values) != 3:
            raise ValueError('Colorbar ticks require exactly three values.')
        values = tuple(_number(v, 'Colorbar tick') for v in self.values)
        if not values[0] < values[1] < values[2]:
            raise ValueError('Colorbar tick values must increase.')
        object.__setattr__(self, 'values', values)
        if self.labels is not None:
            if not isinstance(self.labels, (tuple, list)) or len(self.labels) != 3:
                raise ValueError('Colorbar ticks require exactly three labels.')
            object.__setattr__(self, 'labels', tuple(_string(v, 'Colorbar tick label') for v in self.labels))


@dataclass(frozen=True)
class ColorbarPresentation(_Record):
    visible: bool = True
    show_label: bool = True
    show_unit: bool = True
    width_in: float = .70
    height_in: float = .065
    gap_in: float = .12
    alignment: str = 'right'
    signal_position: str = 'left'
    frame_gap_in: float = .025
    show_extensions: bool = False
    label_format: str = 'auto'
    significant_digits: int = 2
    decimal_places: int = 2
    scientific_style: str = 'e'
    trim_trailing_zeros: bool = True
    ticks: dict[str, ColorbarTicks] = field(default_factory=dict)

    def __post_init__(self):
        _boolean(self.visible, 'colorbar.visible')
        _boolean(self.show_label, 'colorbar.show_label')
        _boolean(self.show_unit, 'colorbar.show_unit')
        _boolean(self.show_extensions, 'colorbar.show_extensions')
        _boolean(self.trim_trailing_zeros, 'colorbar.trim_trailing_zeros')
        if self.label_format not in ('auto', 'decimal', 'scientific', 'engineering'):
            raise ValueError('Colorbar label format must be auto, decimal, scientific or engineering.')
        if self.scientific_style not in ('e', 'times-ten'):
            raise ValueError('Scientific label style must be e or times-ten.')
        for name, minimum in (('significant_digits', 1), ('decimal_places', 0)):
            value = _integer(getattr(self, name), f'colorbar.{name}', minimum=minimum)
            if value > 12:
                raise ValueError(f'Colorbar {name} must be between {minimum} and 12.')
            object.__setattr__(self, name, value)
        if self.alignment not in ('right', 'center', 'left'):
            raise ValueError('Colorbar alignment must be right, center or left.')
        if self.signal_position not in ('left', 'above'):
            raise ValueError('Colorbar signal position must be left or above.')
        for name in ('width_in', 'height_in', 'gap_in'):
            object.__setattr__(self, name, _number(getattr(self, name), name, positive=True))
        frame_gap = _number(self.frame_gap_in, 'colorbar.frame_gap_in')
        if frame_gap < 0:
            raise ValueError('Colorbar frame gap must be nonnegative.')
        object.__setattr__(self, 'frame_gap_in', frame_gap)
        if not isinstance(self.ticks, dict):
            raise ValueError('Colorbar ticks must be keyed by bar id.')
        ticks = {}
        for key, value in self.ticks.items():
            _identifier(key, 'bar id')
            ticks[key] = ColorbarTicks.from_dict(value) if isinstance(value, dict) else value
            if not isinstance(ticks[key], ColorbarTicks):
                raise ValueError('Colorbar ticks must contain ColorbarTicks records.')
        object.__setattr__(self, 'ticks', ticks)


@dataclass(frozen=True)
class PlotAnnotation(_Record):
    id: str
    kind: str = 'text'
    text: str = ''
    position: tuple[float, float] = (.8, .9)
    coordinate_space: str = 'axes-fraction'
    target: tuple[float, float] | None = None
    target_space: str = 'data'
    font_role: str = 'text'
    visible: bool = True
    color: str = 'black'
    linewidth: float = 1.0
    head_size: float = 10.0

    def __post_init__(self):
        _identifier(self.id)
        if self.kind not in ('text', 'arrow'):
            raise ValueError('Annotation kind must be text or arrow.')
        _string(self.text, 'annotation text')
        for name in ('coordinate_space', 'target_space'):
            if getattr(self, name) not in ('axes-fraction', 'data'):
                raise ValueError(f'{name} must be axes-fraction or data.')
        object.__setattr__(self, 'position', _pair(self.position, 'annotation position'))
        if self.target is not None:
            object.__setattr__(self, 'target', _pair(self.target, 'annotation target'))
        elif self.kind == 'arrow':
            raise ValueError('Arrow annotation requires a target.')
        if self.font_role not in ('text', 'numeric'):
            raise ValueError('font_role must be text or numeric.')
        _boolean(self.visible, 'annotation visible')
        _string(self.color, 'annotation color', nonempty=True)
        for name in ('linewidth', 'head_size'):
            object.__setattr__(self, name, _number(getattr(self, name), name, positive=True))


@dataclass(frozen=True)
class PlotPresentation(_Record):
    width_in: float = 91.5 / 25.4
    height_in: float = 3.45
    aspect_locked: bool = True
    frame_width_in: float | None = 2.90
    frame_height_in: float | None = 2.80
    numeric_pt: float = 10.0
    text_pt: float = 12.0
    font_family: str = 'Arial'
    heatmap_dpi: int = 300
    include_peak_labels: bool = False
    axis: AxisPresentation = field(default_factory=AxisPresentation)
    colorbar: ColorbarPresentation = field(default_factory=ColorbarPresentation)
    annotations: tuple[PlotAnnotation, ...] = ()
    title_position: tuple[float, float] = (.02, .98)

    @classmethod
    def from_dict(cls, payload):
        # A missing pair denotes an older Auto recipe, rather than the new
        # explicit default. A partial pair remains an invalid boundary value.
        if isinstance(payload, dict):
            # Serialized records without a paper width predate the new
            # 91.5 mm default and retain their former 3.30 inch paper.
            payload = dict(payload)
            payload.setdefault('width_in', 3.30)
            present = {'frame_width_in', 'frame_height_in'} & payload.keys()
            if len(present) == 1:
                raise ValueError('Heatmap frame width and height must both be provided.')
            if not present:
                payload = dict(payload, frame_width_in=None, frame_height_in=None)
        return super().from_dict(payload)

    def __post_init__(self):
        for name in ('width_in', 'height_in', 'numeric_pt', 'text_pt'):
            object.__setattr__(self, name, _number(getattr(self, name), name, positive=True))
        if (self.frame_width_in is None) != (self.frame_height_in is None):
            raise ValueError('Heatmap frame width and height must both be set or both be automatic.')
        for name in ('frame_width_in', 'frame_height_in'):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, _number(getattr(self, name), name, positive=True))
        object.__setattr__(self, 'heatmap_dpi', _integer(self.heatmap_dpi, 'heatmap_dpi', minimum=1))
        _string(self.font_family, 'font_family', nonempty=True)
        _boolean(self.aspect_locked, 'aspect_locked')
        _boolean(self.include_peak_labels, 'include_peak_labels')
        title_position = _pair(self.title_position, 'title position')
        if any(not 0 <= value <= 1 for value in title_position):
            raise ValueError('Title position must be inside the axes fraction bounds [0, 1].')
        object.__setattr__(self, 'title_position', title_position)
        for name, cls in (('axis', AxisPresentation), ('colorbar', ColorbarPresentation)):
            value = getattr(self, name)
            if isinstance(value, dict):
                value = cls.from_dict(value)
            if not isinstance(value, cls):
                raise ValueError(f'{name} must contain {cls.__name__}.')
            object.__setattr__(self, name, value)
        if not isinstance(self.annotations, (tuple, list)):
            raise ValueError('annotations must be a sequence.')
        annotations = tuple(PlotAnnotation.from_dict(v) if isinstance(v, dict) else v for v in self.annotations)
        if any(not isinstance(v, PlotAnnotation) for v in annotations):
            raise ValueError('annotations must contain PlotAnnotation records.')
        if len({v.id for v in annotations}) != len(annotations):
            raise ValueError('Annotation ids must be unique.')
        object.__setattr__(self, 'annotations', annotations)


@dataclass(frozen=True)
class ColorRegion(_Record):
    id: str
    start: int
    stop: int
    color_limits: tuple[float, float]
    label: str = ''

    def __post_init__(self):
        _identifier(self.id, 'region id')
        object.__setattr__(self, 'start', _integer(self.start, 'region start'))
        object.__setattr__(self, 'stop', _integer(self.stop, 'region stop', minimum=1))
        if self.stop <= self.start:
            raise ValueError('Region stop must follow start.')
        object.__setattr__(self, 'color_limits', _limits(self.color_limits, 'region color limits'))
        _string(self.label, 'region label')


@dataclass(frozen=True)
class PeakLabel(_Record):
    x: float
    y: float
    text: str

    def __post_init__(self):
        object.__setattr__(self, 'x', _number(self.x, 'peak x'))
        object.__setattr__(self, 'y', _number(self.y, 'peak y'))
        _string(self.text, 'peak text')


@dataclass(frozen=True)
class ScatterStyle(_Record):
    visible: bool = True
    color: str = '#d62728'
    marker: str = 'o'
    size: float = 4.0
    edge_width: float = 1.0
    filled: bool = False
    alpha: float = 1.0

    def __post_init__(self):
        from matplotlib.colors import is_color_like
        _boolean(self.visible, 'scatter visibility')
        _boolean(self.filled, 'scatter fill')
        if not isinstance(self.color, str) or not is_color_like(self.color):
            raise ValueError('Scatter color must be a valid color.')
        if self.marker not in ('o', 's', '^', 'v', 'D', '+', 'x', '*', '.'):
            raise ValueError('Unsupported scatter marker.')
        for name in ('size', 'edge_width'):
            object.__setattr__(self, name, _number(getattr(self, name), f'scatter {name}', positive=True))
        alpha = _number(self.alpha, 'scatter alpha')
        if not 0 <= alpha <= 1:
            raise ValueError('Scatter alpha must be between zero and one.')
        object.__setattr__(self, 'alpha', alpha)


@dataclass(frozen=True, eq=False)
class ScatterLayer(_Record):
    """Continuous scientific coordinates, independent from text peak labels."""
    id: str
    x: np.ndarray
    y: np.ndarray
    role: str = 'detected'
    kind: str = 'peak'
    style: ScatterStyle = field(default_factory=ScatterStyle)

    def __post_init__(self):
        _identifier(self.id, 'scatter layer ID')
        if self.role not in ('detected', 'fitted') or self.kind not in ('peak', 'dip'):
            raise ValueError('Scatter layers require detected/fitted and peak/dip types.')
        for name in ('x', 'y'):
            object.__setattr__(self, name, _readonly_array(getattr(self, name), f'scatter {name}'))
        if self.x.ndim != 1 or self.y.shape != self.x.shape:
            raise ValueError('Scatter coordinates must be matching one-dimensional arrays.')
        style = ScatterStyle.from_dict(self.style) if isinstance(self.style, dict) else self.style
        if not isinstance(style, ScatterStyle):
            raise ValueError('Scatter layer requires ScatterStyle.')
        object.__setattr__(self, 'style', style)


def _readonly_array(values, name: str, *, missing: bool = False) -> np.ndarray:
    try:
        array = np.asarray(np.ma.filled(np.ma.asarray(values, dtype=float), np.nan), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} requires numeric data.') from exc
    if np.isinf(array).any() or (not missing and not np.isfinite(array).all()):
        raise ValueError(f'{name} contains nonfinite coordinates or values.')
    # Bytes backing also prevents a caller from restoring the writeable flag.
    return np.frombuffer(array.tobytes(), dtype=float).reshape(array.shape)


@dataclass(frozen=True, eq=False)
class HeatmapSnapshot(_Record):
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    x_label: str = ''
    y_label: str = ''
    signal_label: str = ''
    title: str = ''
    cmap: str = 'viridis'
    color_limits: tuple[float, float] = (0., 1.)
    x_limits: tuple[float, float] | None = None
    y_limits: tuple[float, float] | None = None
    x_scale: str = 'linear'
    y_scale: str = 'linear'
    norm: NormSpec = field(default_factory=NormSpec)
    regions: tuple[ColorRegion, ...] = ()
    peaks: tuple[PeakLabel, ...] = ()
    alpha: float = 1.0
    dataset_id: str = ''
    activation_epoch: int = 0
    science_revision: int = 0
    view_revision: int = 0
    presentation_revision: int = 0
    graph_id: str = ''
    scatter_layers: tuple[ScatterLayer, ...] = ()

    def __post_init__(self):
        for name in ('x', 'y', 'z'):
            object.__setattr__(self, name, _readonly_array(getattr(self, name), name, missing=name == 'z'))
        if self.z.ndim != 2 or min(self.z.shape) < 1:
            raise ValueError('Heatmap z must be a nonempty two-dimensional array.')
        rows, columns = self.z.shape
        if self.x.ndim == self.y.ndim == 1:
            for name, size in (('x', columns), ('y', rows)):
                array = getattr(self, name)
                if len(array) not in (size, size + 1):
                    raise ValueError(f'{name} must contain cell centers or cell edges.')
                difference = np.diff(array)
                if difference.size and not (np.all(difference > 0) or np.all(difference < 0)):
                    raise ValueError(f'{name} coordinates must be strictly monotonic.')
        elif self.x.shape != (rows + 1, columns + 1) or self.y.shape != self.x.shape:
            raise ValueError('Heatmap mesh coordinates must have shape (rows+1, columns+1).')
        for name in ('x_label', 'y_label', 'signal_label', 'title', 'cmap', 'dataset_id', 'graph_id'):
            _string(getattr(self, name), name, nonempty=name == 'cmap')
        object.__setattr__(self, 'color_limits', _limits(self.color_limits, 'color limits'))
        for name in ('x_limits', 'y_limits'):
            if getattr(self, name) is not None:
                # Explicit ranges may already be reversed by the scientific view.
                pair = _pair(getattr(self, name), name)
                if pair[0] == pair[1]:
                    raise ValueError(f'{name} requires different endpoints.')
                object.__setattr__(self, name, pair)
        for name in ('x_scale', 'y_scale'):
            if getattr(self, name) not in ('linear', 'log'):
                raise ValueError(f'{name} must be linear or log.')
        norm = NormSpec.from_dict(self.norm) if isinstance(self.norm, dict) else self.norm
        if not isinstance(norm, NormSpec):
            raise ValueError('norm must be a NormSpec.')
        object.__setattr__(self, 'norm', norm)
        for name, cls in (('regions', ColorRegion), ('peaks', PeakLabel), ('scatter_layers', ScatterLayer)):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)):
                raise ValueError(f'{name} must be a sequence.')
            sequence = tuple(cls.from_dict(v) if isinstance(v, dict) else v for v in value)
            if any(not isinstance(v, cls) for v in sequence):
                raise ValueError(f'{name} must contain {cls.__name__} records.')
            object.__setattr__(self, name, sequence)
        if len({layer.id for layer in self.scatter_layers}) != len(self.scatter_layers):
            raise ValueError('Scatter layer IDs must be unique.')
        if self.regions:
            if len(self.regions) not in (2, 3) or len({v.id for v in self.regions}) != len(self.regions):
                raise ValueError('Regional heatmaps require two or three unique region ids.')
            expected_start = 0
            for region in self.regions:
                if region.start != expected_start or region.stop > columns:
                    raise ValueError('Color regions must cover all columns in order without gaps or overlap.')
                expected_start = region.stop
            if expected_start != columns:
                raise ValueError('Regions must cover every data column.')
        alpha = _number(self.alpha, 'alpha')
        if not 0 <= alpha <= 1:
            raise ValueError('alpha must be between zero and one.')
        object.__setattr__(self, 'alpha', alpha)
        for name in ('activation_epoch', 'science_revision', 'view_revision', 'presentation_revision'):
            object.__setattr__(self, name, _integer(getattr(self, name), name))

    @property
    def owner_token(self) -> tuple[str, int, int, int, int] | tuple[str, int, int, int, int, str]:
        token = (self.dataset_id, self.activation_epoch, self.science_revision,
                 self.view_revision, self.presentation_revision)
        return (*token, self.graph_id) if self.graph_id else token


__all__ = ['NormSpec', 'AxisPresentation', 'ColorbarTicks', 'ColorbarPresentation',
           'PlotAnnotation', 'PlotPresentation', 'ColorRegion', 'PeakLabel', 'ScatterStyle', 'ScatterLayer', 'HeatmapSnapshot']
