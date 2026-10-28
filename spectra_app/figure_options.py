"""Typed, live Figure options drafts; no native form or hidden widget state."""
from __future__ import annotations

from datetime import datetime, timezone
import math

from matplotlib import colormaps, colors, image, markers
from matplotlib.dates import DateConverter, _SwitchableDateConverter, num2date, date2num
from PySide6.QtCore import QObject, Property, Signal, Slot
from shiboken6 import isValid


LINESTYLES = {'-': 'Solid', '--': 'Dashed', '-.': 'DashDot', ':': 'Dotted', 'None': 'None'}
DRAWSTYLES = {'default': 'Default', 'steps-pre': 'Steps (Pre)', 'steps': 'Steps (Pre)',
              'steps-mid': 'Steps (Mid)', 'steps-post': 'Steps (Post)'}
SCALES = ('linear', 'log', 'symlog', 'logit')


def _choices(mapping, initial):
    """Match installed prepare_data's synonym canonicalization, preserving odd styles."""
    mapping = dict(mapping)
    try:
        known = initial in mapping
    except TypeError:
        known = False
    if not known:
        # A Path or custom tuple may not be hashable; keep it as an explicit choice.
        mapping['@current'] = str(initial)
        initial_key = '@current'
    else:
        initial_key = initial
    name_to_short = {name: short for short, name in mapping.items()}
    canonical = name_to_short[mapping[initial_key]]
    values, entries, tokens = {}, [], {}
    for index, (short, name) in enumerate(sorted(
            ((short, name) for name, short in name_to_short.items()), key=lambda p: p[1])):
        token = short if isinstance(short, str) else f'@choice:{index}'
        values[token] = initial if short == '@current' else short
        tokens[short] = token
        entries.append({'value': token, 'label': str(name)})
    # Accept native shorthand aliases too, while the view shows each style once.
    for short in mapping:
        if isinstance(short, str) and short != '@current':
            values.setdefault(short, short)
    return entries, values, tokens[canonical]


def _plain_choices(values):
    return [{'value': value, 'label': value} for value in values]


class FigureOptionsBridge(QObject):
    """One frozen navigation owner, figure, axes and set of live artist targets."""
    changed = Signal()
    draftChanged = Signal()
    errorsChanged = Signal()
    retired = Signal()

    def __init__(self, navigation, axes):
        super().__init__()
        self._navigation = navigation
        self._owner = navigation.ownerToken
        self._canvas = navigation.toolbar.canvas
        self._figure = self._canvas.figure
        self._axes = axes
        self._figure_axes = tuple(self._figure.axes)
        self._axis_map = dict(axes._axis_map)
        self._converters = {name: axis.get_converter() for name, axis in self._axis_map.items()}
        self._units = {name: axis.get_units() for name, axis in self._axis_map.items()}
        self._lines = tuple(line for line in axes.get_lines() if line.get_label() != '_nolegend_')
        self._mappables = tuple(m for m in [*axes.images, *axes.collections]
                                if m.get_label() != '_nolegend_' and m.get_array() is not None)
        self._drafts, self._errors, self._choices = {}, {}, {}
        self._axis_schema, self._curve_schema, self._mappable_schema = [], [], []
        self._error, self._closed = '', False
        self._build_schema()
        self._draw_cid = self._canvas.mpl_connect('draw_event', self._check_event)
        self._close_cid = self._canvas.mpl_connect('close_event', self._retire_event)
        if hasattr(navigation, 'stateChanged'):
            navigation.stateChanged.connect(self._check_event)

    axes = Property('QVariantList', lambda s: s._axis_schema, constant=True)
    curves = Property('QVariantList', lambda s: s._curve_schema, constant=True)
    mappables = Property('QVariantList', lambda s: s._mappable_schema, constant=True)
    drafts = Property('QVariantMap', lambda s: dict(s._drafts), notify=draftChanged)
    errors = Property('QVariantMap', lambda s: dict(s._errors), notify=errorsChanged)
    validationError = Property(str, lambda s: s._error, notify=errorsChanged)
    active = Property(bool, lambda s: not s._closed, notify=changed)
    canAccept = Property(bool, lambda s: s.check_owner(), notify=changed)
    notice = Property(str, lambda s: (
        'Some appearance changes may reset when data refreshes. '
        'Use Properties for saved plot and export formatting.'),
        constant=True)

    def _build_schema(self):
        self._drafts.update({'axes.title': self._axes.get_title(), 'axes.legend': False})
        for name, axis in self._axis_map.items():
            is_date = isinstance(self._converters[name], (DateConverter, _SwitchableDateConverter))
            low, high = getattr(self._axes, f'get_{name}lim')()
            if is_date:
                low, high = (num2date(value, tz=self._units[name]).isoformat() for value in (low, high))
            else:
                low, high = repr(float(low)), repr(float(high))
            self._axis_schema.append({'name': name, 'title': f'{name.title()}-Axis',
                                      'isDate': is_date, 'scales': _plain_choices(SCALES)})
            self._drafts.update({f'axis.{name}.min': low, f'axis.{name}.max': high,
                                 f'axis.{name}.label': axis.label.get_text(),
                                 f'axis.{name}.scale': axis.get_scale()})
        for index, line in enumerate(self._lines):
            prefix = f'curve.{index}.'
            schema = {'index': index, 'title': str(line.get_label())}
            for field, metadata, mapping, initial in (
                    ('linestyle', 'lineStyles', LINESTYLES, line.get_linestyle()),
                    ('drawstyle', 'drawStyles', DRAWSTYLES, line.get_drawstyle()),
                    ('marker', 'markers', markers.MarkerStyle.markers, line.get_marker())):
                entries, values, token = _choices(mapping, initial)
                schema[metadata], self._choices[prefix + field] = entries, values
                self._drafts[prefix + field] = token
            self._curve_schema.append(schema)
            self._drafts.update({prefix + 'label': str(line.get_label()),
                                 prefix + 'width': repr(float(line.get_linewidth())),
                                 prefix + 'size': repr(float(line.get_markersize()))})
            for field, value in [('color', line.get_color()), ('facecolor', line.get_markerfacecolor()),
                                 ('edgecolor', line.get_markeredgecolor())]:
                self._drafts[prefix + field] = colors.to_hex(
                    colors.to_rgba(value, line.get_alpha()), keep_alpha=True)
        for index, mappable in enumerate(self._mappables):
            prefix = f'mappable.{index}.'
            current = mappable.get_cmap()
            # Always retain the actual current object, even when its name shadows a registry entry.
            cmap_values = {'@current': current, **{name: colormaps[name] for name in sorted(colormaps)}}
            self._choices[prefix + 'cmap'] = cmap_values
            schema = {'index': index, 'title': str(mappable.get_label()),
                      'isImage': hasattr(mappable, 'get_interpolation'),
                      'colormaps': [{'value': key, 'label': current.name + ' (current)' if key == '@current'
                                    else key} for key in cmap_values]}
            low, high = mappable.get_clim()
            self._drafts.update({prefix + 'label': str(mappable.get_label()), prefix + 'cmap': '@current',
                                 prefix + 'min': str(low), prefix + 'max': str(high)})
            if schema['isImage']:
                schema['interpolations'] = _plain_choices(sorted(image.interpolations_names))
                schema['stages'] = _plain_choices(('data', 'rgba', 'auto'))
                self._drafts[prefix + 'interpolation'] = mappable.get_interpolation()
                self._drafts[prefix + 'stage'] = mappable.get_interpolation_stage()
            self._mappable_schema.append(schema)

    def _check_event(self, *args):
        self.check_owner()

    def _retire_event(self, *args):
        if not self._closed:
            self.shutdown()
            self.retired.emit()

    def check_owner(self):
        if self._closed:
            return False
        navigation = self._navigation
        current_axes = self._figure.axes
        valid = (isValid(navigation) and navigation.allowed(self._owner)
                 and navigation.toolbar.canvas is self._canvas
                 and self._canvas.figure is self._figure
                 and len(current_axes) == len(self._figure_axes)
                 and all(a is b for a, b in zip(current_axes, self._figure_axes))
                 and self._axes.get_figure() is self._figure
                 and list(self._axes._axis_map) == list(self._axis_map)
                 and all(self._axes._axis_map[name] is axis for name, axis in self._axis_map.items())
                 and all(line.axes is self._axes and line in self._axes.lines for line in self._lines)
                 and all(m.axes is self._axes and (m in self._axes.images or m in self._axes.collections)
                         for m in self._mappables))
        if not valid:
            self._retire_event()
        return valid

    def _parse(self):
        values, errors = {}, {}
        for key, draft in self._drafts.items():
            parts = key.split('.')
            field = parts[-1]
            try:
                if key in self._choices:
                    values[key] = self._choices[key][draft]
                elif key == 'axes.legend':
                    if not isinstance(draft, bool):
                        raise ValueError('Choose whether to generate a legend.')
                    values[key] = draft
                elif field in ('min', 'max', 'width', 'size'):
                    if parts[0] == 'axis' and self._axis_schema[list(self._axis_map).index(parts[1])]['isDate']:
                        value = datetime.fromisoformat(str(draft).strip().replace('Z', '+00:00'))
                        if value.tzinfo is None:
                            unit = self._units[parts[1]]
                            value = value.replace(tzinfo=num2date(0, tz=unit).tzinfo if unit else timezone.utc)
                        value = float(date2num(value))
                    else:
                        value = float(draft)
                    if not math.isfinite(value) or (field in ('width', 'size') and value < 0):
                        raise ValueError('Enter a finite, nonnegative number.' if field in ('width', 'size')
                                         else 'Enter a finite number or valid date/time.')
                    values[key] = value
                elif field in ('color', 'facecolor', 'edgecolor'):
                    values[key] = colors.to_rgba(str(draft))
                elif field == 'scale':
                    if draft not in SCALES:
                        raise ValueError('Choose a supported axis scale.')
                    values[key] = draft
                elif field in ('interpolation', 'stage'):
                    choices = image.interpolations_names if field == 'interpolation' else ('data', 'rgba', 'auto')
                    if draft not in choices:
                        raise ValueError('Choose a supported image setting.')
                    values[key] = draft
                else:
                    values[key] = str(draft)
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                errors[key] = str(error) if isinstance(error, ValueError) else 'Choose a valid value.'
        for name in self._axis_map:
            prefix = f'axis.{name}.'
            scale = values.get(prefix + 'scale')
            for field in ('min', 'max'):
                key = prefix + field
                if key not in values:
                    continue
                value = values[key]
                if scale == 'log' and value <= 0:
                    errors[key] = 'Log scale limits must be greater than zero.'
                elif scale == 'logit' and not 0 < value < 1:
                    errors[key] = 'Logit scale limits must be between zero and one.'
        return values, errors

    def _validate(self):
        values, self._errors = self._parse()
        self._error = next(iter(self._errors.values()), '')
        self.errorsChanged.emit()
        return values

    @Slot(str, 'QVariant', result=bool)
    def setDraft(self, key, value):
        if not self.check_owner() or key not in self._drafts:
            return False
        self._drafts[key] = value
        self._validate()
        self.draftChanged.emit()
        self.changed.emit()
        return not self._errors

    @Slot(str, result='QVariantList')
    def colorPreview(self, key):
        try:
            return list(colors.to_rgba(self._drafts[key]))
        except (KeyError, ValueError, TypeError):
            return [0., 0., 0., 0.]

    @Slot(result=bool)
    def apply(self):
        if not self.check_owner():
            return False
        values = self._validate()
        if self._errors or not self.check_owner():
            return False
        original_limits = {name: getattr(self._axes, f'get_{name}lim')() for name in self._axis_map}
        self._axes.title.set_text(values['axes.title'])
        for name, axis in self._axis_map.items():
            prefix = f'axis.{name}.'
            if axis.get_scale() != values[prefix + 'scale']:
                getattr(self._axes, f'set_{name}scale')(values[prefix + 'scale'])
            axis._set_lim(values[prefix + 'min'], values[prefix + 'max'], auto=False)
            axis.set_label_text(values[prefix + 'label'])
            axis._set_converter(self._converters[name])
            axis.set_units(self._units[name])
        for index, line in enumerate(self._lines):
            prefix = f'curve.{index}.'
            line.set_label(values[prefix + 'label'])
            line.set_linestyle(values[prefix + 'linestyle'])
            line.set_drawstyle(values[prefix + 'drawstyle'])
            line.set_linewidth(values[prefix + 'width'])
            line.set_alpha(None)
            line.set_color(values[prefix + 'color'])
            if values[prefix + 'marker'] != 'none':
                line.set_marker(values[prefix + 'marker'])
                line.set_markersize(values[prefix + 'size'])
                line.set_markerfacecolor(values[prefix + 'facecolor'])
                line.set_markeredgecolor(values[prefix + 'edgecolor'])
        for index, mappable in enumerate(self._mappables):
            prefix = f'mappable.{index}.'
            if self._mappable_schema[index]['isImage']:
                mappable.set_interpolation(values[prefix + 'interpolation'])
                mappable.set_interpolation_stage(values[prefix + 'stage'])
            mappable.set_label(values[prefix + 'label'])
            mappable.set_cmap(values[prefix + 'cmap'])
            mappable.set_clim(*sorted((values[prefix + 'min'], values[prefix + 'max'])))
        if values['axes.legend']:
            old = self._axes.get_legend()
            new = self._axes.legend(ncols=old._ncols if old is not None else 1)
            if new is not None:
                new.set_draggable(old._draggable is not None if old is not None else None)
        self._canvas.draw()
        if any(getattr(self._axes, f'get_{name}lim')() != limits for name, limits in original_limits.items()):
            self._navigation.toolbar.push_current()
        return True

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._canvas.mpl_disconnect(self._draw_cid)
        self._canvas.mpl_disconnect(self._close_cid)
        if isValid(self._navigation) and hasattr(self._navigation, 'stateChanged'):
            self._navigation.stateChanged.disconnect(self._check_event)
        self.changed.emit()
