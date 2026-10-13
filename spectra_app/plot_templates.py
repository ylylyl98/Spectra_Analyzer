"""Named ordinary page formats, stored separately from dataset sidecars.

Application only replaces presentation and view records after validation. It
never activates a dataset, transforms arrays, or changes processing or fits.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields, replace
import json
import math
from collections.abc import Mapping
from types import SimpleNamespace

from matplotlib import colormaps

from .custom_colormaps import resolve_colormap, canonical_colormap
from .plot_presentation import (AxisPresentation, ColorbarPresentation, NormSpec,
                                PlotAnnotation, PlotPresentation)
from .plot_annotations import coordinates_to_page
from .svg_export import build_color_norm


FORMAT_TEMPLATES_KEY = 'plot/format_templates_v1'


def _object(payload, names, label):
    if not isinstance(payload, dict) or set(payload) != set(names):
        raise ValueError(f'Invalid {label} fields.')


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Saved format templates contain duplicate JSON keys.')
        result[key] = value
    return result


def _name(value):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError('A format requires a nonempty name without control characters.')
    return value.strip()


def _palette(value):
    if not isinstance(value, str) or not value:
        raise ValueError('Format palette must be a known colormap name.')
    try:
        colormaps.get_cmap(resolve_colormap(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Unknown format palette: {value}.') from exc
    return canonical_colormap(value)


def _limits(value, label):
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)):
        raise ValueError(f'{label} requires two finite numbers.')
    result = tuple(float(v) for v in value)
    if not all(math.isfinite(v) for v in result) or result[0] >= result[1]:
        raise ValueError(f'{label} requires finite lower < upper.')
    return result


def _mode(value):
    if value not in ('global', '2', '3'):
        raise ValueError('Format color mode must be global, 2 or 3.')
    return value


def _axis_limits(value, label):
    """Original data axes may descend; exchange and flips remain separate."""
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)):
        raise ValueError(f'{label} requires two finite numbers.')
    pair = tuple(float(v) for v in value)
    if not all(math.isfinite(v) for v in pair) or pair[0] == pair[1]:
        raise ValueError(f'{label} requires finite different endpoints.')
    return pair


@dataclass(frozen=True)
class FormatApplicationOptions:
    """Independent groups for a new application; legacy calls retain their locks."""
    layout: bool = True
    typography: bool = True
    axis: bool = True
    colormap: bool = True
    colorbar: bool = True
    x_limits: bool = False
    y_limits: bool = False
    color_limits: bool = False
    mapping: bool = False
    title_position: bool = False
    annotation_positions: bool = False
    lock_copied_ranges: bool = True

    def __post_init__(self):
        for item in fields(self):
            if type(getattr(self, item.name)) is not bool:
                raise ValueError(f'Format option {item.name} must be a boolean.')


@dataclass(frozen=True)
class AnnotationLayout:
    """Source text anchor and pairing label, without dataset text or arrow heads."""
    id: str
    kind: str
    text: str
    position: tuple[float, float]
    coordinate_space: str = 'axes-fraction'

    def __post_init__(self):
        if self.coordinate_space != 'axes-fraction':
            raise ValueError('Saved annotation layout requires axes-fraction coordinates.')
        annotation = PlotAnnotation(self.id, self.kind, self.text, self.position,
                                    target=(0., 0.) if self.kind == 'arrow' else None)
        object.__setattr__(self, 'position', annotation.position)

    def to_dict(self):
        return {'id': self.id, 'kind': self.kind, 'text': self.text,
                'position': list(self.position), 'coordinate_space': self.coordinate_space}

    @classmethod
    def from_dict(cls, payload):
        _object(payload, (item.name for item in fields(cls)), 'annotation layout')
        return cls(**deepcopy(payload))


@dataclass(frozen=True)
class FormatStyle:
    """Only physical dimensions, typography and axis/colorbar layout are reusable."""
    width_in: float = 91.5 / 25.4
    height_in: float = 3.45
    aspect_locked: bool = True
    frame_width_in: float | None = 2.90
    frame_height_in: float | None = 2.80
    numeric_pt: float = 10.
    text_pt: float = 12.
    font_family: str = 'Arial'
    heatmap_dpi: int = 300
    axis: AxisPresentation = field(default_factory=AxisPresentation)
    colorbar: ColorbarPresentation = field(default_factory=ColorbarPresentation)

    def __post_init__(self):
        colorbar = self.colorbar
        if isinstance(colorbar, dict):
            colorbar = ColorbarPresentation.from_dict(colorbar)
        if not isinstance(colorbar, ColorbarPresentation) or colorbar.ticks:
            raise ValueError('A format colorbar contains layout only; custom ticks belong to each dataset.')
        # Reuse the renderer's strict ordinary-record validation, without keeping
        # annotations, peak labels or a mutable ticks dictionary from the source.
        page = PlotPresentation(**{item.name: getattr(self, item.name) for item in fields(self)
                                   if item.name != 'colorbar'}, colorbar=colorbar)
        for item in fields(self):
            value = getattr(page, item.name)
            if item.name == 'axis':
                value = AxisPresentation.from_dict(value.to_dict())
            elif item.name == 'colorbar':
                value = ColorbarPresentation.from_dict(value.to_dict())
            object.__setattr__(self, item.name, value)

    def to_dict(self):
        result = {item.name: getattr(self, item.name) for item in fields(self)}
        result['axis'] = self.axis.to_dict()
        result['colorbar'] = self.colorbar.to_dict()
        result['colorbar'].pop('ticks')
        return result

    @classmethod
    def from_dict(cls, payload):
        # v1 formats predate optional frame/header settings. Migrate only those
        # known additions; the original complete field sets stay strict.
        payload = deepcopy(payload)
        if isinstance(payload, dict):
            if not {'frame_width_in', 'frame_height_in'} & payload.keys():
                payload.update(frame_width_in=None, frame_height_in=None)
            axis = payload.get('axis')
            if isinstance(axis, dict):
                defaults = AxisPresentation()
                for name in ('x_major_count', 'y_major_count', 'x_minor_count', 'y_minor_count'):
                    axis.setdefault(name, getattr(defaults, name))
            colorbar = payload.get('colorbar')
            if isinstance(colorbar, dict):
                defaults = ColorbarPresentation()
                for name in ('alignment', 'signal_position', 'frame_gap_in', 'show_extensions', 'show_unit',
                             'label_format', 'significant_digits', 'decimal_places',
                             'scientific_style', 'trim_trailing_zeros'):
                    colorbar.setdefault(name, getattr(defaults, name))
        _object(payload, (item.name for item in fields(cls)), 'format style')
        _object(payload['axis'], (item.name for item in fields(AxisPresentation)), 'format axis')
        _object(payload['colorbar'], (item.name for item in fields(ColorbarPresentation)
                                     if item.name != 'ticks'), 'format colorbar')
        return cls(**deepcopy(payload))


@dataclass(frozen=True)
class FormatTemplate:
    name: str
    style: FormatStyle
    colormap: str
    norm: NormSpec
    color_scale_mode: str
    color_limits: tuple[float, float]
    region_color_limits: tuple[tuple[float, float], ...] = ()
    x_limits: tuple[float, float] | None = None
    y_limits: tuple[float, float] | None = None
    title_position: tuple[float, float] | None = None
    annotation_layout: tuple[AnnotationLayout, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'name', _name(self.name))
        style = FormatStyle.from_dict(self.style) if isinstance(self.style, dict) else self.style
        norm = NormSpec.from_dict(self.norm) if isinstance(self.norm, dict) else self.norm
        if not isinstance(style, FormatStyle) or not isinstance(norm, NormSpec):
            raise ValueError('A format requires FormatStyle and NormSpec records.')
        style = FormatStyle.from_dict(style.to_dict())
        object.__setattr__(self, 'style', style)
        object.__setattr__(self, 'norm', NormSpec.from_dict(norm.to_dict()))
        object.__setattr__(self, 'colormap', _palette(self.colormap))
        object.__setattr__(self, 'color_scale_mode', _mode(self.color_scale_mode))
        object.__setattr__(self, 'color_limits', _limits(self.color_limits, 'Format global color limits'))
        if not isinstance(self.region_color_limits, (tuple, list)):
            raise ValueError('Format regional color limits must be a sequence.')
        regions = tuple(_limits(pair, f'Format region-{i+1} color limits')
                        for i, pair in enumerate(self.region_color_limits))
        count = 0 if self.color_scale_mode == 'global' else int(self.color_scale_mode)
        if len(regions) != count:
            raise ValueError('Format regional color limits must match its color mode.')
        object.__setattr__(self, 'region_color_limits', regions)
        for name in ('x_limits', 'y_limits'):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _axis_limits(value, f'Format {name}'))
        if self.title_position is not None:
            object.__setattr__(self, 'title_position',
                               PlotPresentation(title_position=self.title_position).title_position)
        if not isinstance(self.annotation_layout, (tuple, list)):
            raise ValueError('Format annotation layout must be a sequence.')
        layout = tuple(AnnotationLayout.from_dict(entry) if isinstance(entry, dict) else entry
                       for entry in self.annotation_layout)
        if any(not isinstance(entry, AnnotationLayout) for entry in layout):
            raise ValueError('Format annotation layout requires AnnotationLayout records.')
        if len({entry.id for entry in layout}) != len(layout):
            raise ValueError('Format annotation layout ids must be unique.')
        object.__setattr__(self, 'annotation_layout', layout)
        for bar, limits in _bar_limits(self.color_scale_mode, self.color_limits, regions).items():
            _validate_mapping(norm, limits, bar)

    def to_dict(self):
        return {'name': self.name, 'style': self.style.to_dict(), 'colormap': self.colormap,
                'norm': self.norm.to_dict(), 'color_scale_mode': self.color_scale_mode,
                'color_limits': list(self.color_limits),
                'region_color_limits': [list(pair) for pair in self.region_color_limits],
                'x_limits': list(self.x_limits) if self.x_limits is not None else None,
                'y_limits': list(self.y_limits) if self.y_limits is not None else None,
                'title_position': list(self.title_position) if self.title_position is not None else None,
                'annotation_layout': [entry.to_dict() for entry in self.annotation_layout]}

    @classmethod
    def from_dict(cls, payload):
        # Only these newly captured components may be absent in a v1 template.
        # Keep the original required fields and unknown-field checks strict.
        payload = deepcopy(payload)
        if isinstance(payload, dict):
            for name, value in (('x_limits', None), ('y_limits', None),
                                ('title_position', None), ('annotation_layout', [])):
                payload.setdefault(name, value)
        _object(payload, (item.name for item in fields(cls)), 'format template')
        return cls(**payload)


def _bar_limits(mode, global_limits, regions):
    _mode(mode)
    if mode == 'global':
        return {'global': global_limits}
    if len(regions) != int(mode):
        raise ValueError('Regional color limits must be available for every target bar before copying mapping or limits.')
    return {f'region-{i+1}': pair for i, pair in enumerate(regions)}


def _validate_mapping(norm, limits, bar):
    try:
        build_color_norm(norm, limits)
    except ValueError as exc:
        raise ValueError(f'{bar}: {exc}') from exc


def capture_format(session, name: str, *, source_snapshot=None, source_page=None) -> FormatTemplate:
    """Capture original data ranges and frame text anchors.

    The ordinary formal snapshot uses linear coordinate axes. Supply both the
    actual snapshot and rendered page to capture any alternative coordinate
    scales or rendered limits. Data-space transfer is never a fallback.
    """
    page, view = session.presentation, session.view
    if (source_snapshot is None) != (source_page is None):
        raise ValueError('Annotation capture requires both source_snapshot and source_page.')
    if source_snapshot is not None and source_snapshot.dataset_id not in ('', session.dataset_id):
        raise ValueError('Annotation capture snapshot belongs to another source dataset.')
    if source_page is not None:
        page_token = getattr(source_page, 'owner_token', None)
        if page_token is not None:
            if not isinstance(page_token, (tuple, list)) or len(page_token) not in (5, 6):
                raise ValueError('Annotation capture page has an invalid owner token.')
            if page_token[0] not in ('', session.dataset_id):
                raise ValueError('Annotation capture page belongs to another source dataset.')
            if len(page_token) == 6 and page_token[5] != session.active_graph_id:
                raise ValueError('Annotation capture page belongs to another source graph.')
            if tuple(page_token[1:]) != source_snapshot.owner_token[1:]:
                raise ValueError('Annotation capture page is stale for the source snapshot.')
    style = FormatStyle(**{item.name: getattr(page, item.name) for item in fields(FormatStyle)
                           if item.name != 'colorbar'}, colorbar=replace(page.colorbar, ticks={}))
    x_limits = _axis_limits(source_snapshot.x_limits if source_snapshot is not None else view.x_limits,
                            'Source X limits')
    y_limits = _axis_limits(source_snapshot.y_limits if source_snapshot is not None else view.y_limits,
                            'Source Y limits')
    if any(annotation.coordinate_space == 'data' for annotation in page.annotations):
        if source_snapshot is None:
            # Use the same declared scales as the formal renderer, rather than
            # infer a scale from coordinate magnitudes or live artist state.
            from .plot_snapshot import snapshot_dataset
            source_snapshot = snapshot_dataset(session)
            display_x, display_y = (y_limits, x_limits) if page.axis.exchange_xy else (x_limits, y_limits)
            if page.axis.flip_x:
                display_x = display_x[::-1]
            if page.axis.flip_y:
                display_y = display_y[::-1]
            source_page = SimpleNamespace(axes_bounds=(0., 0., 1., 1.),
                                          display_x_limits=display_x, display_y_limits=display_y)
    layout = []
    for annotation in page.annotations:
        position = annotation.position
        if annotation.coordinate_space == 'data':
            x, top = coordinates_to_page(position, (1., 1.), source_page, source_snapshot,
                                         page, 'data')
            left, bottom, width, height = source_page.axes_bounds
            position = ((x-left)/width, (1-top-bottom)/height)
        layout.append(AnnotationLayout(annotation.id, annotation.kind, annotation.text, position))
    return FormatTemplate(name, style, view.colormap, view.norm, view.color_scale_mode,
                          view.color_limits, tuple(view.last_valid_region_color_limits)
                          if view.color_scale_mode != 'global' else (),
                          x_limits, y_limits, page.title_position, tuple(layout))


class FormatTemplateStore:
    """Caller-owned QSettings boundary. Reading/importing never writes defaults."""
    def __init__(self, settings):
        self.settings = settings

    def _read(self):
        raw = self.settings.value(FORMAT_TEMPLATES_KEY)
        if raw is None:
            return {}
        if not isinstance(raw, str):
            raise ValueError('Saved format templates must be JSON text.')
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_json_object)
        except (ValueError, TypeError) as exc:
            raise ValueError('Saved format templates contain malformed JSON.') from exc
        _object(payload, ('version', 'templates'), 'format store')
        if type(payload['version']) is not int or payload['version'] != 1:
            raise ValueError('Unsupported format template version.')
        if not isinstance(payload['templates'], list):
            raise ValueError('Saved format templates must be a list.')
        result = {}
        for item in payload['templates']:
            template = FormatTemplate.from_dict(item)
            if template.name in result:
                raise ValueError('Saved format template names must be unique.')
            result[template.name] = template
        return result

    def _write(self, templates):
        payload = {'version': 1, 'templates': [templates[name].to_dict() for name in sorted(templates)]}
        self.settings.setValue(FORMAT_TEMPLATES_KEY, json.dumps(payload, ensure_ascii=False, allow_nan=False))
        self.settings.sync()
        if self.settings.status() != self.settings.Status.NoError:
            raise OSError('Could not persist named plot formats.')

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._read()))

    def load(self, name: str) -> FormatTemplate:
        return self._read()[_name(name)]

    def save(self, template: FormatTemplate) -> None:
        if not isinstance(template, FormatTemplate):
            raise ValueError('Only FormatTemplate records can be saved.')
        template = FormatTemplate.from_dict(template.to_dict())
        templates = self._read()
        templates[template.name] = template
        self._write(templates)

    def delete(self, name: str) -> bool:
        templates = self._read()
        if templates.pop(_name(name), None) is None:
            return False
        self._write(templates)
        return True


@dataclass(frozen=True)
class FormatApplication:
    dataset_id: str
    changed_fields: tuple[str, ...]


def _paired_annotations(presentation, template, pairs):
    if not template.annotation_layout:
        raise ValueError('Source annotation layout is unavailable.')
    if not isinstance(pairs, Mapping) or not pairs:
        raise ValueError('Annotation positions require explicit source-to-target ID pairs.')
    source = {entry.id: entry for entry in template.annotation_layout}
    target = {entry.id: entry for entry in presentation.annotations}
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in pairs.items()):
        raise ValueError('Annotation pairs must map source IDs to target IDs.')
    if len(set(pairs.values())) != len(pairs):
        raise ValueError('Each target annotation may be assigned only once.')
    replacements = {}
    for source_id, target_id in pairs.items():
        if source_id not in source or target_id not in target:
            raise ValueError(f'Annotation pair references a missing ID: {source_id} -> {target_id}.')
        entry, annotation = source[source_id], target[target_id]
        if entry.kind != annotation.kind:
            raise ValueError(f'Annotation pair kinds differ: {source_id} -> {target_id}.')
        replacements[target_id] = replace(annotation, position=entry.position,
                                           coordinate_space=entry.coordinate_space)
    return tuple(replacements.get(annotation.id, annotation) for annotation in presentation.annotations)


def _prepare(target, template, copy_mapping, copy_color_limits, options, annotation_pairs):
    if not isinstance(template, FormatTemplate):
        raise ValueError('Application requires a FormatTemplate record.')
    if type(copy_mapping) is not bool or type(copy_color_limits) is not bool:
        raise ValueError('Format copy options must be booleans.')
    if options is None:
        # Existing callers copy all basic groups and preserve endpoint Fix flags.
        options = FormatApplicationOptions(mapping=copy_mapping, color_limits=copy_color_limits,
                                            lock_copied_ranges=False)
    elif not isinstance(options, FormatApplicationOptions):
        raise ValueError('Application requires a FormatApplicationOptions record.')
    elif copy_mapping or copy_color_limits:
        raise ValueError('Use either options or legacy copy flags, not both.')
    template = FormatTemplate.from_dict(template.to_dict())
    view = deepcopy(target.view)
    if options.colormap:
        view.colormap = template.colormap
    if options.mapping:
        view.norm = NormSpec.from_dict(template.norm.to_dict())
    for axis in ('x', 'y'):
        if getattr(options, f'{axis}_limits'):
            limits = getattr(template, f'{axis}_limits')
            if limits is None:
                raise ValueError(f'Source {axis.upper()} limits are unavailable.')
            setattr(view, f'{axis}_limits', limits)
            low, high = sorted(limits)
            view.bounds.update({f'{axis}min': low, f'{axis}max': high})
            if options.lock_copied_ranges:
                view.locks.update({f'{axis}min': True, f'{axis}max': True})
    if options.color_limits:
        if view.color_scale_mode != template.color_scale_mode:
            raise ValueError('Copying color limits requires the same global or regional color mode.')
        view.color_limits = tuple(template.color_limits)
        view.bounds.update(vmin=view.color_limits[0], vmax=view.color_limits[1])
        view.last_valid_color_limits = view.color_limits
        view.last_valid_region_color_limits = [tuple(pair) for pair in template.region_color_limits]
        if options.lock_copied_ranges:
            view.locks.update(vmin=True, vmax=True)
    changes = {}
    style = template.style
    if options.layout:
        for name in ('width_in', 'height_in', 'aspect_locked', 'frame_width_in', 'frame_height_in', 'heatmap_dpi'):
            changes[name] = getattr(style, name)
    if options.typography:
        for name in ('numeric_pt', 'text_pt', 'font_family'):
            changes[name] = getattr(style, name)
    if options.axis:
        changes['axis'] = AxisPresentation.from_dict(style.axis.to_dict())
    colorbar = style.colorbar if options.colorbar else target.presentation.colorbar
    changes['colorbar'] = replace(colorbar, ticks=deepcopy(target.presentation.colorbar.ticks))
    if options.title_position:
        if template.title_position is None:
            raise ValueError('Source title position is unavailable.')
        changes['title_position'] = template.title_position
    if options.annotation_positions:
        changes['annotations'] = _paired_annotations(target.presentation, template, annotation_pairs)
    presentation = replace(target.presentation, **changes)
    if options.mapping or options.color_limits:
        for bar, limits in _bar_limits(view.color_scale_mode, view.color_limits,
                                      view.last_valid_region_color_limits).items():
            _validate_mapping(view.norm, limits, bar)
            custom = presentation.colorbar.ticks.get(bar)
            if custom is not None and (custom.values[0] < limits[0] or custom.values[-1] > limits[1]
                                       or (view.norm.kind == 'log' and custom.values[0] <= 0)):
                raise ValueError(f'{bar}: preserved custom ticks are invalid for the requested color limits or mapping.')
    changed = []
    for item in fields(presentation):
        old, new = getattr(target.presentation, item.name), getattr(presentation, item.name)
        if item.name in ('axis', 'colorbar'):
            for nested in fields(old):
                if nested.name != 'ticks' and getattr(old, nested.name) != getattr(new, nested.name):
                    changed.append(f'{item.name}.{nested.name}')
        elif item.name == 'annotations':
            for old_annotation, new_annotation in zip(old, new):
                for name in ('position', 'coordinate_space'):
                    if getattr(old_annotation, name) != getattr(new_annotation, name):
                        changed.append(f'annotations.{old_annotation.id}.{name}')
        elif old != new:
            changed.append(item.name)
    for name in ('colormap', 'norm', 'x_limits', 'y_limits', 'color_limits', 'last_valid_color_limits',
                 'last_valid_region_color_limits'):
        if getattr(target.view, name) != getattr(view, name):
            changed.append(name)
    for axis, group in (('x', 'x_limits'), ('y', 'y_limits'), ('v', 'color_limits')):
        if any(target.view.bounds[f'{axis}{end}'] != view.bounds[f'{axis}{end}'] for end in ('min', 'max')):
            changed.append(f'bounds.{group}')
        for end in ('min', 'max'):
            name = f'{axis}{end}'
            if target.view.locks[name] != view.locks[name]:
                changed.append(f'locks.{name}')
    return view, presentation, FormatApplication(target.dataset_id, tuple(changed))


def preview_format(target, template: FormatTemplate, *, copy_mapping=False,
                   copy_color_limits=False, options: FormatApplicationOptions | None = None,
                   annotation_pairs: Mapping[str, str] | None = None) -> FormatApplication:
    """Validate and list concrete changed fields without mutating the target."""
    return _prepare(target, template, copy_mapping, copy_color_limits, options, annotation_pairs)[2]


def apply_format(target, template: FormatTemplate, *, copy_mapping=False,
                 copy_color_limits=False, options: FormatApplicationOptions | None = None,
                 annotation_pairs: Mapping[str, str] | None = None) -> FormatApplication:
    """Apply atomically to one target; failures leave its original records intact."""
    view, presentation, result = _prepare(target, template, copy_mapping, copy_color_limits, options, annotation_pairs)
    target.view, target.presentation = view, presentation
    return result


__all__ = ['FORMAT_TEMPLATES_KEY', 'FormatStyle', 'FormatTemplate', 'FormatTemplateStore',
           'FormatApplicationOptions', 'AnnotationLayout', 'FormatApplication',
           'capture_format', 'preview_format', 'apply_format']
