"""Page-owned Quick controls; only unfinished edits live outside PlotPresentation."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP, localcontext
import math

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtQuick import QQuickItem
import shiboken6

from .plot_annotations import coordinates_to_page, page_to_coordinates
from .plot_presentation import ColorbarTicks, PlotPresentation


# These are the former formal editors' physical ranges and display precision.
NUMBERS = {
    'width_in': (.1, 40, 8), 'height_in': (.1, 40, 3),
    'numeric_pt': (1, 100, 1), 'text_pt': (1, 100, 1),
    'heatmap_dpi': (1, 2400, 0), 'frame_width_in': (.01, 40, 3),
    'frame_height_in': (.01, 40, 3), 'title_x': (0, 1, 8), 'title_y': (0, 1, 8),
    'axis_border_width': (.01, 20, 3),
    'bar_width_in': (.01, 40, 3), 'bar_height_in': (.001, 20, 3),
    'bar_gap_in': (.001, 20, 3), 'bar_frame_gap_in': (0, 20, 3),
    'bar_precision': (0, 12, 0),
}
for _axis in ('x', 'y'):
    NUMBERS.update({f'axis_{_axis}_step': (.000001, 1e12, 6),
                    f'axis_{_axis}_count': (2, 100, 0),
                    f'axis_{_axis}_minor_count': (0, 20, 0)})
ANNOTATION_NUMBERS = {'annotation_x', 'annotation_y', 'annotation_target_x', 'annotation_target_y'}
BOOLEANS = {'aspect_locked', 'include_peak_labels', 'frame_auto', 'custom_ticks'}
BOOLEANS.update('axis_' + key for key in ('exchange_xy', 'flip_x', 'flip_y', 'show_x', 'show_y',
    'show_x_label', 'show_y_label', 'show_tick_labels', 'major_ticks', 'minor_ticks', 'ticks_inside', 'border'))
BOOLEANS.update('bar_' + key for key in ('visible', 'show_label', 'show_unit', 'trim_zeros', 'show_extensions'))
CHOICES = {'axis_x_mode': ('Auto', 'By increment', 'By count'),
           'axis_y_mode': ('Auto', 'By increment', 'By count'),
           'bar_label_format': ('auto', 'decimal', 'scientific', 'engineering'),
           'bar_scientific_style': ('e', 'times-ten'), 'bar_alignment': ('right', 'center', 'left'),
           'bar_signal_position': ('left', 'above')}
COMMANDS = ('analyze', 'copy_svg', 'copy_png', 'export_svg', 'export_png', 'settings_copy', 'settings_paste', 'settings_paste_to', 'settings_paste_options', 'settings_adjust', 'settings_undo_paste',
    'settings_copy_to', 'comparison', 'format_save', 'format_load', 'format_delete', 'format_apply',
    'batch_export', 'batch_results', 'batch_cancel')


def section_for(field):
    if field.startswith('annotation_'):
        return 'annotations'
    if field.startswith('bar_') or field in ('custom_ticks', 'tick_values', 'tick_labels'):
        return 'colorbar'
    if field.startswith('axis_'):
        return 'axes'
    if field.startswith('frame_') or field.startswith('title_'):
        return 'frame'
    return 'page'


class FormalPlotBridge(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)
    revealRequested = Signal(str, str)

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self._guard = lambda: True
        self._input_guard = lambda: True
        self._properties_input_guard = lambda source: self._input_guard()
        self._prompt_generation = 0
        self._prompt_request = None
        self._drafts = {}
        self._values = {}
        self._annotation_drafts = {}
        self._annotation_pending = {}
        self._selected_annotation = ''
        self._selected_bar = 'global'
        self._formats = []
        self._selected_format = ''
        self._commands = {key: {'enabled': key not in ('copy_svg', 'copy_png', 'export_svg', 'export_png',
            'format_load', 'format_delete', 'format_apply', 'batch_results', 'batch_cancel',
            'settings_adjust', 'settings_undo_paste'),
            'tooltip': ''} for key in COMMANDS}
        self._clipboard = ''
        self._batch = {'visible': False, 'maximum': 0, 'value': 0, 'status': '', 'tooltip': '',
                       'cancelVisible': False}
        self._prompt = {'open': False, 'kind': 'text', 'text': ''}
        self._error_field = ''
        self._frame_auto_size = (2.90, 2.80)
        self._annotation_dialog = None
        self._notified_availability = None

    def system_allowed(self):
        """Hidden validation never activates a registry input lease."""
        generation = self.page._input_generation
        return bool(not self.page.retired and self.page.enabled and self._guard()
                    and generation == self.page._input_generation
                    and not self.page.retired and self.page.enabled)

    def allowed(self, *, authorize=True, properties_source=None, properties_owner=None):
        generation = self.page._input_generation
        owner, rendered = self.page.expected_owner, self.page.rendered_page
        return bool(self.system_allowed() and (not authorize or (
                    self._input_guard() if properties_source is None and properties_owner is None
                    else self._properties_input_current(properties_source, properties_owner)))
                    and generation == self.page._input_generation
                    and owner == self.page.expected_owner and rendered is self.page.rendered_page
                    and self.system_allowed()
                    and generation == self.page._input_generation
                    and owner == self.page.expected_owner and rendered is self.page.rendered_page)

    enabled = Property(bool, lambda self: self.allowed(authorize=False), notify=stateChanged)

    def set_guard(self, guard):
        self.page._input_generation += 1
        self.retire_prompt()
        self._guard = guard
        self.page.preview._retire_gesture()
        self._emit_state_changed()

    def set_input_guard(self, guard):
        self.page._input_generation += 1
        self.retire_prompt()
        self._input_guard = guard
        self.page.preview._retire_gesture()
        self._emit_state_changed()

    def set_properties_input_guard(self, guard):
        self.page._input_generation += 1
        self.retire_prompt()
        self._properties_input_guard = guard

    def _properties_input_token(self, source):
        if (not isinstance(source, QQuickItem) or not shiboken6.isValid(source)
                or not source.isVisible() or not source.isEnabled()
                or not self._properties_input_guard(source)):
            return ''
        # The same Properties item moves between the retained dock/floating
        # parents. Include that presentation owner without calling window(),
        # which can change native-wrapper ownership in PySide.
        parent = source
        while parent.parentItem() is not None and parent.objectName() != 'applicationProperties':
            parent = parent.parentItem()
        placement = parent.parentItem() or parent
        return (f'{id(self)}:{self.page._input_generation}:'
                f'{shiboken6.getCppPointer(source)[0]}:{shiboken6.getCppPointer(placement)[0]}')

    @Slot(QObject, result=str)
    def capturePropertiesInput(self, source):
        generation = self.page._input_generation
        token = self._properties_input_token(source) if self.system_allowed() else ''
        return token if generation == self.page._input_generation and self.system_allowed() else ''

    def _properties_input_current(self, source, owner):
        return bool(owner and owner == self.capturePropertiesInput(source))

    @Slot(QObject, str, result=bool)
    def propertiesInputCurrent(self, source, owner):
        return self.allowed(properties_source=source, properties_owner=owner)

    def _fields(self, include_drafts=True):
        p = self.page.presentation
        fields = {name: getattr(p, name) for name in ('width_in', 'height_in', 'numeric_pt', 'text_pt',
            'heatmap_dpi', 'aspect_locked', 'include_peak_labels')}
        fields.update(frame_auto=p.frame_width_in is None,
            frame_width_in=p.frame_width_in if p.frame_width_in is not None else self._frame_auto_size[0],
            frame_height_in=p.frame_height_in if p.frame_height_in is not None else self._frame_auto_size[1],
            title_x=p.title_position[0], title_y=p.title_position[1])
        for name in p.axis.__dataclass_fields__:
            if not name.endswith(('_major_step', '_major_count')):
                fields['axis_' + name] = getattr(p.axis, name)
        for axis in ('x', 'y'):
            step, count = getattr(p.axis, axis + '_major_step'), getattr(p.axis, axis + '_major_count')
            fields.update({f'axis_{axis}_mode': 'By increment' if step is not None else 'By count' if count is not None else 'Auto',
                f'axis_{axis}_step': step if step is not None else 1.,
                f'axis_{axis}_count': count if count is not None else 7})
        for name in ('visible', 'show_label', 'show_unit', 'width_in', 'height_in', 'gap_in', 'frame_gap_in',
                     'alignment', 'signal_position', 'show_extensions', 'label_format', 'scientific_style'):
            fields['bar_' + name] = getattr(p.colorbar, name)
        fields['bar_trim_zeros'] = p.colorbar.trim_trailing_zeros
        mode = self._values.get('bar_label_format', p.colorbar.label_format) if include_drafts else p.colorbar.label_format
        fields['bar_precision'] = p.colorbar.significant_digits if mode in ('auto', 'engineering') else p.colorbar.decimal_places
        ticks = p.colorbar.ticks.get(self._selected_bar)
        fields.update(custom_ticks=ticks is not None,
                      tick_values=', '.join(str(value) for value in ticks.values) if ticks else '',
                      tick_labels=' | '.join(ticks.labels) if ticks and ticks.labels else '')
        item = self.selected_annotation()
        fields.update(annotation_text=item.text if item else '',
            annotation_coordinate_space=item.coordinate_space if item else 'axes-fraction',
            annotation_target_space=item.target_space if item else 'axes-fraction',
            annotation_font_role=item.font_role if item else 'text', annotation_visible=item.visible if item else True)
        for name, value in zip(('annotation_x', 'annotation_y', 'annotation_target_x', 'annotation_target_y'),
            (*item.position, *(item.target or (0., 0.))) if item else (0., 0., 0., 0.)):
            fields[name] = self._display(value, 8)
        for name, (_, _, precision) in NUMBERS.items():
            fields[name] = self._display(fields[name], precision)
        if include_drafts:
            fields.update(self._values)
            fields.update(self._drafts)
            fields.update(self._annotation_drafts)
        return fields

    @staticmethod
    def _display(value, precision):
        # QDoubleSpinBox displays exact positive decimal ties upward. Formatting
        # the draft must retain that UI rule without quantizing saved records.
        if not precision:
            return str(int(value))
        with localcontext() as context:
            context.prec = max(40, len(str(value)) + precision + 2)
            shown = Decimal(str(value)).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
        return format(shown, 'f').rstrip('0').rstrip('.') or '0'

    def _state(self):
        item = self.selected_annotation()
        sample, warning = self.page.number_sample()
        snapshot = getattr(getattr(self.page, 'preview', None), 'snapshot', None)
        return {'fields': self._fields(), 'error': self.page.presentation_error or '',
            'errorField': self._error_field, 'dimensions': self.page.dimensions_text,
            'status': self.page.status_text, 'sample': sample, 'warning': warning,
            'selectedBar': self._selected_bar, 'barIds': ['global', 'region-1', 'region-2', 'region-3'],
            'annotations': [{'id': a.id, 'kind': a.kind, 'label': f'{a.kind}: {a.text or "(unlabelled)"}'
                + (' (hidden)' if not a.visible else '')} for a in self.page.presentation.annotations],
            'selectedAnnotation': self._selected_annotation, 'annotationKind': item.kind if item else '',
            'annotationUnits': {axis: getattr(snapshot, axis + '_label', '') or axis.upper() + ' data units'
                                for axis in ('x', 'y')},
            'formats': list(self._formats), 'selectedFormat': self._selected_format,
            'zoom': self.page.zoom_text, 'ready': self.page.rendered_page is not None,
            'commands': self._command_state(self.allowed(authorize=False)),
            'clipboard': self._clipboard, 'batch': dict(self._batch), 'prompt': dict(self._prompt)}

    state = Property('QVariantMap', _state, notify=stateChanged)

    def _command_state(self, enabled):
        return {key: {**value, 'enabled': value['enabled'] and (enabled or key == 'batch_cancel')
                      and not self.page.retired} for key, value in self._commands.items()}

    def _availability(self):
        enabled = self.allowed(authorize=False)
        return enabled, self._command_state(enabled)

    def _emit_state_changed(self):
        # Capture before emission: a synchronous observer may publish a newer
        # state, whose signature must survive when this emission returns.
        self._notified_availability = self._availability()
        self.stateChanged.emit()

    def sync_command_availability(self):
        # Re-evaluate guards even when the raw command flags are unchanged.
        # The same signal also notifies enabled, including all-disabled pages.
        if self._availability() != self._notified_availability:
            self.sync()

    def sync(self):
        identifiers = {item.id for item in self.page.presentation.annotations}
        self._annotation_pending = {key: value for key, value in self._annotation_pending.items()
                                    if key in identifiers and value}
        if self._selected_annotation and self.selected_annotation() is None:
            self._selected_annotation = ''
            self._annotation_drafts.clear()
        self._emit_state_changed()

    def selected_annotation(self):
        return next((item for item in self.page.presentation.annotations if item.id == self._selected_annotation), None)

    @Slot(str, str)
    @Slot(str, str, QObject, str)
    def setDraft(self, field, text, properties_source=None, properties_owner=None):
        if not self.allowed(properties_source=properties_source, properties_owner=properties_owner):
            self.sync()
            return
        if field.startswith('annotation_'):
            if self.selected_annotation() is None or field not in ANNOTATION_NUMBERS | {'annotation_text'}:
                return
            if text == self._fields(False)[field]:
                self._annotation_drafts.pop(field, None)
            else:
                self._annotation_drafts[field] = text
        elif field in NUMBERS or field in ('tick_values', 'tick_labels'):
            if text == self._fields(False)[field]:
                self._drafts.pop(field, None)
            else:
                self._drafts[field] = text
        else:
            return
        if not (self._drafts or self._values or self._annotation_drafts or self._annotation_pending):
            self.page.presentation_error = None
            self._error_field = ''
        self.draftChanged.emit()
        self.sync()

    @Slot(str, 'QVariant')
    @Slot(str, 'QVariant', QObject, str)
    def setValue(self, field, value, properties_source=None, properties_owner=None):
        if not self.allowed(properties_source=properties_source, properties_owner=properties_owner):
            self.sync()
            return
        if field.startswith('annotation_'):
            if self.selected_annotation() is None:
                return
            if field in ('annotation_coordinate_space', 'annotation_target_space'):
                self._convert_annotation_space(field, value)
            elif field == 'annotation_font_role' and value in ('text', 'numeric'):
                self._annotation_drafts[field] = value
            elif field == 'annotation_visible' and isinstance(value, bool):
                self._annotation_drafts[field] = value
            if value == self._fields(False).get(field):
                self._annotation_drafts.pop(field, None)
            self.draftChanged.emit()
            self.sync()
            return
        if field in NUMBERS:
            self.setDraft(field, str(value), properties_source, properties_owner)
        elif field in BOOLEANS and isinstance(value, bool):
            self._values[field] = value
        elif field in CHOICES and value in CHOICES[field]:
            self._values[field] = value
            if field == 'bar_label_format':
                self._drafts.pop('bar_precision', None)
        else:
            return
        self.commitField(field, properties_source, properties_owner)

    @Slot(str, int, result=bool)
    @Slot(str, int, QObject, str, result=bool)
    def stepField(self, field, direction, properties_source=None, properties_owner=None):
        """Keep the former numeric editors' keyboard increments in Quick."""
        if not self.allowed(properties_source=properties_source, properties_owner=properties_owner) or direction not in (-1, 1) or field not in NUMBERS.keys() | ANNOTATION_NUMBERS:
            return False
        if field in ANNOTATION_NUMBERS and self.selected_annotation() is None:
            return False
        try:
            value = self._parse(field, self._fields()[field])
        except ValueError as exc:
            return self._fail(exc, field)
        low, high, precision = NUMBERS.get(field, (-1e15, 1e15, 8))
        step = .05
        if precision == 0 or field in ('numeric_pt', 'text_pt'):
            step = 100 if field == 'heatmap_dpi' else 1
        elif field == 'axis_border_width':
            step = .1
        elif field in ('title_x', 'title_y'):
            step = .01
        elif field in ANNOTATION_NUMBERS:
            step = .01
            endpoint = field.startswith('annotation_target_')
            space = self._fields()['annotation_target_space' if endpoint else 'annotation_coordinate_space']
            snapshot = self.page.preview.snapshot
            if space == 'data' and snapshot is not None:
                axis = 'x' if field.endswith('_x') else 'y'
                coordinates = sorted({float(item) for item in getattr(snapshot, axis) if math.isfinite(item)})
                spacing = sorted(b - a for a, b in zip(coordinates, coordinates[1:]) if b > a)
                if spacing:
                    step = spacing[len(spacing) // 2]
            step = max(10 ** -precision, step)
        if field == 'bar_precision' and self._fields()['bar_label_format'] in ('auto', 'engineering'):
            low = 1
        value = round(max(low, min(high, value + direction * step)), precision)
        self.setDraft(field, self._display(value, precision), properties_source, properties_owner)
        return True if field in ANNOTATION_NUMBERS else self.commitField(field, properties_source, properties_owner)

    def _parse(self, field, text):
        low, high, precision = NUMBERS.get(field, (-1e15, 1e15, 8))
        if field == 'bar_precision' and self._values.get('bar_label_format', self.page.presentation.colorbar.label_format) in ('auto', 'engineering'):
            low = 1
        try:
            value = float(text)
            if not math.isfinite(value) or not low <= value <= high or (precision == 0 and not value.is_integer()):
                raise ValueError()
            return int(value) if precision == 0 else value
        except (ValueError, OverflowError):
            raise ValueError(f'{field.replace("_", " ")} requires a finite {"integer" if NUMBERS.get(field, (0,0,8))[2] == 0 else "number"} from {low:g} to {high:g}.') from None

    def _candidate(self):
        p = self.page.presentation
        fields = dict(self._values)
        for field, text in self._drafts.items():
            if field in NUMBERS:
                try:
                    fields[field] = self._parse(field, text)
                except ValueError:
                    self._error_field = field
                    raise
        basic = {field: value for field, value in fields.items() if field in ('width_in', 'height_in',
            'numeric_pt', 'text_pt', 'heatmap_dpi', 'aspect_locked', 'include_peak_labels')}
        locked = basic.get('aspect_locked', p.aspect_locked)
        if locked and ('width_in' in basic) != ('height_in' in basic):
            if 'width_in' in basic:
                basic['height_in'] = round(basic['width_in'] * p.height_in / p.width_in, 3)
            else:
                basic['width_in'] = round(basic['height_in'] * p.width_in / p.height_in, 8)
        automatic = fields.get('frame_auto', p.frame_width_in is None)
        basic['frame_width_in'] = None if automatic else fields.get('frame_width_in', p.frame_width_in or self._frame_auto_size[0])
        basic['frame_height_in'] = None if automatic else fields.get('frame_height_in', p.frame_height_in or self._frame_auto_size[1])
        basic['title_position'] = (fields.get('title_x', p.title_position[0]), fields.get('title_y', p.title_position[1]))
        axes = {name[5:]: value for name, value in fields.items() if name.startswith('axis_')
                and name[5:] in p.axis.__dataclass_fields__}
        for axis in ('x', 'y'):
            mode = fields.get(f'axis_{axis}_mode', self._fields()[f'axis_{axis}_mode'])
            axes[axis + '_major_step'] = fields.get(f'axis_{axis}_step', getattr(p.axis, axis + '_major_step') or 1.) if mode == 'By increment' else None
            axes[axis + '_major_count'] = fields.get(f'axis_{axis}_count', getattr(p.axis, axis + '_major_count') or 7) if mode == 'By count' else None
        bar = {name[4:]: value for name, value in fields.items() if name.startswith('bar_')
               and name[4:] in p.colorbar.__dataclass_fields__}
        if 'bar_trim_zeros' in fields:
            bar['trim_trailing_zeros'] = fields['bar_trim_zeros']
        if 'bar_precision' in fields:
            key = 'significant_digits' if fields.get('bar_label_format', p.colorbar.label_format) in ('auto', 'engineering') else 'decimal_places'
            bar[key] = fields['bar_precision']
        ticks = dict(p.colorbar.ticks)
        if fields.get('custom_ticks', self._fields()['custom_ticks']):
            try:
                data = self._fields()
                values = tuple(float(value.strip()) for value in data['tick_values'].split(',')) if data['tick_values'].strip() else ()
                labels = tuple(label.strip() for label in data['tick_labels'].split('|')) if data['tick_labels'].strip() else None
                ticks[self._selected_bar] = ColorbarTicks(values, labels)
            except (ValueError, TypeError):
                self._error_field = 'tick_values'
                raise
        else:
            ticks.pop(self._selected_bar, None)
        return replace(p, **basic, axis=replace(p.axis, **axes), colorbar=replace(p.colorbar, **bar, ticks=ticks))

    def _fail(self, exc, field='', *, focus_error=True):
        self._error_field = field or self._error_field
        self.page.presentation_error = str(exc)
        self.page.presentationError.emit(str(exc))
        self.draftChanged.emit()
        self.sync()
        if focus_error:
            self.focusRequested.emit(section_for(self._error_field), self._error_field)
        return False

    @Slot(str, result=bool)
    @Slot(str, QObject, str, result=bool)
    def commitField(self, field='', properties_source=None, properties_owner=None):
        return self._commit_field(field, properties_source, properties_owner)

    @Slot(str, QObject, str, result=bool)
    def commitFieldOnBlur(self, field, properties_source, properties_owner):
        return self._commit_field(field, properties_source, properties_owner,
                                  focus_error=False)

    def _commit_field(self, field, properties_source, properties_owner, *, focus_error=True):
        if not self.allowed(properties_source=properties_source, properties_owner=properties_owner):
            return False
        if field.startswith('annotation_'):
            return True
        try:
            candidate = self._candidate()
        except (ValueError, TypeError) as exc:
            return self._fail(exc, self._error_field or field, focus_error=focus_error)
        self._drafts.clear()
        self._values.clear()
        self._error_field = ''
        self.page.presentation_error = None
        self.page.publish_presentation(candidate)
        self.sync()
        return True

    def annotation_candidate(self, identifier=None, drafts=None):
        item = (self.selected_annotation() if identifier is None else next(
            (item for item in self.page.presentation.annotations if item.id == identifier), None))
        if item is None:
            return None
        values = {}
        for field, text in (self._annotation_drafts if drafts is None else drafts).items():
            if field in ANNOTATION_NUMBERS:
                try:
                    values[field] = self._parse(field, text)
                except ValueError:
                    self._error_field = field
                    raise
            else:
                values[field] = text
        return replace(item, text=values.get('annotation_text', item.text),
            position=(values.get('annotation_x', item.position[0]), values.get('annotation_y', item.position[1])),
            coordinate_space=values.get('annotation_coordinate_space', item.coordinate_space),
            target=(values.get('annotation_target_x', item.target[0]), values.get('annotation_target_y', item.target[1])) if item.target is not None else None,
            target_space=values.get('annotation_target_space', item.target_space),
            font_role=values.get('annotation_font_role', item.font_role), visible=values.get('annotation_visible', item.visible))

    @Slot(result=bool)
    @Slot(QObject, str, result=bool)
    def applyAnnotation(self, properties_source=None, properties_owner=None):
        if not self.allowed(properties_source=properties_source, properties_owner=properties_owner):
            return False
        try:
            candidate = self.annotation_candidate()
            if candidate is not None:
                self.page.validate_annotation_geometry(candidate)
        except (ValueError, TypeError, OverflowError) as exc:
            return self._fail(exc, self._error_field or 'annotation_text')
        self._annotation_drafts.clear()
        self.page.presentation_error = None
        self._error_field = ''
        if candidate is not None:
            self.page._replace_annotation(candidate, properties_source, properties_owner)
        self.sync()
        return True

    def validate_editor(self):
        """Validate every unfinished field without publishing or clearing it."""
        if not self.has_drafts():
            return self.page.presentation
        if not self.system_allowed():
            raise ValueError('Wait for the current operation before finishing page edits.')
        # Validate everything before publishing any part of the captured source.
        try:
            candidate = self._candidate()
            annotation_drafts = dict(self._annotation_pending)
            if self._selected_annotation and self._annotation_drafts:
                annotation_drafts[self._selected_annotation] = self._annotation_drafts
            annotations = {}
            for identifier, drafts in annotation_drafts.items():
                try:
                    annotation = self.annotation_candidate(identifier, drafts)
                    self.page.validate_annotation_geometry(annotation)
                    annotations[identifier] = annotation
                except (ValueError, TypeError, OverflowError):
                    self._select_annotation_drafts(identifier)
                    raise
            if annotations:
                candidate = replace(candidate, annotations=tuple(annotations.get(old.id, old) for old in candidate.annotations))
        except (ValueError, TypeError, OverflowError) as exc:
            self._fail(exc)
            raise ValueError(f'Correct the current page editor first: {exc}') from exc
        return candidate

    def finish_editor(self):
        if not self.has_drafts():
            return
        candidate = self.validate_editor()
        self._drafts.clear()
        self._values.clear()
        self._annotation_drafts.clear()
        self._annotation_pending.clear()
        self._error_field = ''
        self.page.presentation_error = None
        self.page.publish_presentation(candidate)
        self.sync()

    def has_drafts(self):
        return bool(self._drafts or self._values or self._annotation_drafts or self._annotation_pending or self.page.presentation_error)

    def _select_annotation_drafts(self, identifier):
        if identifier != self._selected_annotation:
            if self._selected_annotation and self._annotation_drafts:
                self._annotation_pending[self._selected_annotation] = self._annotation_drafts
            self._selected_annotation = identifier
            self._annotation_drafts = self._annotation_pending.pop(identifier, {})
        self.page.preview.select_annotation(identifier or None)
        self.page.preview.select_title(False)

    @Slot(str)
    @Slot(str, QObject, str)
    def selectAnnotation(self, identifier, properties_source=None, properties_owner=None):
        if not self.allowed(properties_source=properties_source, properties_owner=properties_owner):
            return
        if identifier and not any(a.id == identifier for a in self.page.presentation.annotations):
            return
        self._select_annotation_drafts(identifier)
        self.sync()

    @Slot()
    @Slot(QObject, str)
    def deleteAnnotation(self, properties_source=None, properties_owner=None):
        if self.allowed(properties_source=properties_source, properties_owner=properties_owner) and self._selected_annotation:
            self.page.delete_annotation(properties_source, properties_owner)

    def _convert_annotation_space(self, field, value):
        if value not in ('axes-fraction', 'data'):
            return
        old = self._fields()[field]
        if old == value:
            return
        endpoint = field == 'annotation_target_space'
        xs, ys = ('annotation_target_x', 'annotation_target_y') if endpoint else ('annotation_x', 'annotation_y')
        try:
            if self.page.rendered_page is None or self.page.preview.snapshot is None:
                raise ValueError('Wait for the current preview before converting annotation coordinates.')
            data = self._fields()
            point = tuple(self._parse(name, data[name]) for name in (xs, ys))
            size = self.page.paper_display_size()
            point = coordinates_to_page(point, size, self.page.rendered_page, self.page.preview.snapshot, self.page.presentation, old)
            converted = page_to_coordinates(point, size, self.page.rendered_page, self.page.preview.snapshot, self.page.presentation, value)
        except (ValueError, TypeError, OverflowError) as exc:
            self.page.set_status_message(str(exc))
            return
        self._annotation_drafts.update({field: value, xs: repr(converted[0]), ys: repr(converted[1])})

    @Slot(str)
    @Slot(str, QObject, str)
    def selectBar(self, identifier, properties_source=None, properties_owner=None):
        if self.allowed(properties_source=properties_source, properties_owner=properties_owner) and identifier in ('global', 'region-1', 'region-2', 'region-3'):
            if self._drafts or self._values:
                if not self.commitField('tick_values', properties_source, properties_owner):
                    return
            self._selected_bar = identifier
            self.sync()

    @Slot(str)
    @Slot(str, QObject, str)
    def selectFormat(self, name, properties_source=None, properties_owner=None):
        if self.allowed(properties_source=properties_source, properties_owner=properties_owner) and name in self._formats:
            self._selected_format = name
            self.sync()

    @Slot(str)
    def setZoom(self, text):
        if self.allowed():
            self.page.set_zoom(text)

    @Slot(str)
    def command(self, key):
        if self.page.retired or key not in self._commands or not self._commands[key]['enabled']:
            return
        if key != 'batch_cancel' and not self.allowed():
            return
        if self.page.retired or not self._commands[key]['enabled']:
            return
        # The controller may explicitly re-enable source capture for a pending
        # native label edit. It must receive that intent to finish the label
        # and prepare current bytes; ordinary invalidation disables these keys.
        self.page.dispatch_command(key)

    def retire_prompt(self):
        """Retire captured callbacks before closing a superseded presentation."""
        self._prompt_generation += 1
        self._prompt_request = None
        self._prompt['open'] = False
        dialog, self._annotation_dialog = self._annotation_dialog, None
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()

    def _prompt_valid(self, request, *, authorize=False):
        if request is None or request != self._prompt_request or not self._prompt['open']:
            return False
        generation, owner, page, source, properties_owner = request[1:]
        return bool(generation == self.page._input_generation
                    and owner == self.page.expected_owner and page is self.page.rendered_page
                    and page is not None and self.allowed(authorize=authorize,
                        properties_source=source, properties_owner=properties_owner)
                    and request == self._prompt_request
                    and generation == self.page._input_generation
                    and owner == self.page.expected_owner and page is self.page.rendered_page)

    @Slot(str)
    @Slot(str, QObject, str)
    def openPrompt(self, kind, properties_source=None, properties_owner=None):
        if self.allowed(properties_source=properties_source, properties_owner=properties_owner) and self.page.rendered_page is not None and kind in ('text', 'arrow'):
            window = self.page.prompt_window()
            self.retire_prompt()
            self._prompt = {'open': True, 'kind': kind, 'text': '9T σ⁺' if kind == 'text' else ''}
            request = (self._prompt_generation, self.page._input_generation,
                       self.page.expected_owner, self.page.rendered_page, properties_source, properties_owner)
            self._prompt_request = request
            from .quick_dialog import QuickDialog
            bridge = AnnotationPromptBridge(self, request)
            dialog = QuickDialog(bridge, 'FormalAnnotationPrompt.qml', window,
                title='Add text' if kind == 'text' else 'Add arrow', size=(520, 360))
            self._annotation_dialog = dialog
            dialog.accepted.connect(lambda: self.acceptPrompt(request))
            dialog.rejected.connect(lambda: self.cancelPrompt(request))
            dialog.show()
            self.sync()

    @Slot(str)
    def setPromptText(self, text, request=None):
        request = self._prompt_request if request is None else request
        if self._prompt_valid(request, authorize=True):
            self._prompt['text'] = text

    @Slot()
    def acceptPrompt(self, request=None):
        request = self._prompt_request if request is None else request
        if self._prompt_valid(request, authorize=True):
            kind, text = self._prompt['kind'], self._prompt['text']
            # Retire before publishing; synchronous presentation callbacks can
            # invalidate the owner or open a new request during this commit.
            self._prompt_request = None
            self._prompt['open'] = False
            self.page.add_annotation(kind, text, request[4], request[5])
            self.sync()

    @Slot()
    def cancelPrompt(self, request=None):
        request = self._prompt_request if request is None else request
        if request is not None and request == self._prompt_request:
            self._prompt_request = None
            self._prompt['open'] = False
            self.sync()

    def reveal(self, kind, identifier=''):
        if kind == 'annotation':
            self.selectAnnotation(identifier)
            section, field = 'annotations', 'annotation_text'
        elif kind in ('horizontal-axis', 'vertical-axis'):
            section, field = 'axes', 'axis_x_mode' if kind == 'horizontal-axis' else 'axis_y_mode'
        elif kind == 'colorbar':
            self.selectBar(identifier)
            section, field = 'colorbar', 'tick_values' if self._fields()['custom_ticks'] else 'custom_ticks'
        else:
            return False
        self.focusRequested.emit(section, field)
        return True


class AnnotationPromptBridge(QObject):
    """Every callback belongs to the request captured when this dialog opened."""
    stateChanged = Signal()

    def __init__(self, formal, request):
        super().__init__()
        self.formal, self.request = formal, request
        self._kind, self._text = formal._prompt['kind'], formal._prompt['text']
        formal.stateChanged.connect(self.stateChanged)

    kind = Property(str, lambda self: self._kind, constant=True)
    text = Property(str, lambda self: self.formal._prompt['text']
                    if self.formal._prompt_valid(self.request) else self._text, notify=stateChanged)
    enabled = Property(bool, lambda self: self.formal._prompt_valid(self.request), notify=stateChanged)
    canAccept = Property(bool, lambda self: self.formal._prompt_valid(self.request), notify=stateChanged)

    @Slot(str)
    def setText(self, text):
        self.formal.setPromptText(text, self.request)
