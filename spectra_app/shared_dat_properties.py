"""Owner-aware DAT property projection; only unfinished editor text is runtime state."""
from __future__ import annotations

import math
from copy import deepcopy
from functools import lru_cache
from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from .custom_colormaps import COMMON_COLORMAPS, resolve_colormap
from .dat_io import AXIS_OVERRIDE_ITEMS
from .edit_history import COLOR_FIELDS, RANGE_FIELDS, STYLE_FIELDS, TITLE_FIELDS
from .plot_presentation import NormSpec
from .signal_titles import RAW_LABEL, DERIVATIVE_LABEL, infer_source_signal_kind
from .title_templates import BUILTIN_PRESET, FIELDS

BOUNDS = ('xmin', 'xmax', 'ymin', 'ymax', 'vmin', 'vmax')
NUMBERS = {**{name: (-1e12, 1e12, 4, .01) for name in BOUNDS},
           'linthresh': (1e-8, 1e12, 8, .01),
           'edge0': (-1e12, 1e12, 4, .01), 'edge1': (-1e12, 1e12, 4, .01)}
COLORMAPS = tuple(COMMON_COLORMAPS) + tuple((name, name) for name in
    ('turbo', 'viridis', 'plasma', 'inferno', 'magma', 'cividis', 'RdBu_r_p0p45'))


@lru_cache(maxsize=len(COMMON_COLORMAPS))
def _colormap_preview(name):
    """Small immutable swatches; label edits reuse the sampled palette bytes."""
    import numpy as np
    from matplotlib import colormaps
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImage

    cmap = resolve_colormap(name)
    cmap = colormaps[cmap] if isinstance(cmap, str) else cmap
    pixels = np.tile(cmap(np.linspace(0., 1., 96), bytes=True), (12, 1, 1))
    image = QImage(pixels.data, 96, 12, pixels.strides[0], QImage.Format.Format_RGBA8888)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    return 'data:image/png;base64,' + bytes(data.toBase64()).decode('ascii')


class SharedDatPropertiesBridge(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)
    revealRequested = Signal(str, str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._closed = False
        self._drafts = {}
        self._templates = {}
        self._disclosures = {}
        self._status = {}
        self._projected_owner = None
        self._errors = {}
        self._axis_overrides = {}

    def owner_key(self):
        w = self.window
        s = w.workspace.active or w._unregistered_session
        return (id(w.workspace), s.dataset_id, s.active_graph_id)

    @Property(str, notify=stateChanged)
    def ownerToken(self):
        return '|'.join(map(str, self.owner_key()))

    def allowed(self, owner=None):
        if self._closed:
            return False
        from shiboken6 import isValid
        w = self.window
        if not isValid(w):
            return False
        panel = getattr(w, 'dat_properties_panel', None)
        if panel is not None and not isValid(panel):
            return False
        return ((owner is None or owner == self.ownerToken)
                and w._workbench_document() is None
                and (panel is None or panel.isEnabled()))

    @property
    def _axis_override(self):
        cube = self.window.current_cube
        return self._axis_overrides.get(self.owner_key(), cube.x_override if cube else None)

    @_axis_override.setter
    def _axis_override(self, value):
        self._axis_overrides[self.owner_key()] = value

    @property
    def _error_field(self):
        return self._errors.get(self.owner_key(), ('', ''))[0]

    @_error_field.setter
    def _error_field(self, value):
        self._errors[self.owner_key()] = (value, self._error_text)

    @property
    def _error_text(self):
        return self._errors.get(self.owner_key(), ('', ''))[1]

    @_error_text.setter
    def _error_text(self, value):
        self._errors[self.owner_key()] = (self._error_field, value)

    enabled = Property(bool, allowed, notify=stateChanged)
    errorField = Property(str, lambda self: self._error_field, notify=draftChanged)
    errorText = Property(str, lambda self: self._error_text, notify=draftChanged)

    def number(self, field):
        v = self.window._dat_view()
        if field in BOUNDS:
            return float(v.bounds[field])
        if field == 'linthresh':
            return float(v.norm.linthresh)
        index = int(field[-1])
        return float(v.color_region_boundaries[index]) if index < len(v.color_region_boundaries) else 0.

    def template(self, field):
        draft = self._templates.setdefault(self.owner_key(), {})
        preset = self.window._title_settings.get('preset') or BUILTIN_PRESET
        default = {'title_pattern': preset['pattern'],
                   'title_angle_map': ';'.join(f'{a}={b}' for a, b in preset['angle_map'].items()),
                   'title_preset_name': '' if preset['name'] in ('Manual', 'Current group') else preset['name']}
        return draft.get(field, default[field])

    def status(self, field, text):
        values = self._status.setdefault(self.owner_key(), {})
        if values.get(field) != text:
            values[field] = text
            self.stateChanged.emit()

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        w = self.window
        s = w.workspace.active or w._unregistered_session
        v = s.view
        cube = w.current_cube
        source_cube = w._base_cube or cube
        source_kind = source_cube.signal_source_kind if source_cube is not None else 'unknown'
        if source_kind == 'unknown' and source_cube is not None:
            source_kind = infer_source_signal_kind(source_cube.source_path or '')
        drafts = self._drafts.get(self.owner_key(), {})
        result = {name: drafts.get(name, f'{self.number(name):.{spec[2]}f}') for name, spec in NUMBERS.items()}
        result.update({name + '_lock': bool(v.locks[name]) for name in BOUNDS})
        result.update(v.annotations)
        result.update(fix_annotations=v.fix_annotations, signal_title_auto=v.signal_title_auto,
            signal_title_tooltip=(
                'Manual colorbar label. Enable Auto from data to use signal metadata or filename.'
                if not v.signal_title_auto else
                'Signal unrecognized; neutral Signal label. Edit to set a manual title.'
                if source_kind == 'unknown' else
                'Automatic signal title from explicit metadata, filename, or applied processing.'),
            colormap=v.colormap, mapping=v.norm.kind, color_scale=v.color_scale_mode,
            axis_override=self._axis_override or '', regional=v.color_scale_mode in ('2', '3'),
            linthresh_enabled=v.norm.kind == 'symlog',
            fix_regions=s.fixed_recipes['regions'].enabled,
            fix_region_edges=s.fixed_recipes['region_edges'].enabled,
            source_axis_enabled=bool(cube is not None and w.current_dat_path is not None
                and s.sidecar_writes_enabled and not s.processing_results
                and not w._dat_import_pending and not w._background_jobs.busy),
            source_axis_tooltip=("Override the auto-detected x-axis type. Use Auto Detect unless detection is wrong."
                if s.sidecar_writes_enabled and not s.processing_results else
                'Retained processing results use these source data and axes. Import the DAT as a new copy to change its axis interpretation.'
                if s.processing_results else
                'This project uses its saved axis interpretation. Import the original DAT into a new project to change its axis interpretation.'),
            template_active=w._title_settings['mode'] == 'template',
            title_preset=(w._title_settings['preset']['name'] if w._title_settings['mode'] == 'template' else 'Manual'),
            edge_count=min(2, len(v.color_region_boundaries)),
            edge_unit=cube.energy_label if cube else '',
            x_label=cube.energy_label if cube else 'X', y_label=cube.gate_label if cube else 'Y',
            disclosures=dict(self._disclosures.get(self.owner_key(), {})))
        result.update({field: self.template(field) for field in
                       ('title_pattern', 'title_angle_map', 'title_preset_name')})
        result.update({name: self._status.get(self.owner_key(), {}).get(name, '') for name in ('axis_status', 'mapping_status',
            'color_range_status', 'color_clipping_status', 'title_template_status')})
        result.update(colormaps=[{'label': label, 'value': name,
                'preview': _colormap_preview(name) if (label, name) in COMMON_COLORMAPS else ''}
                for label, name in COLORMAPS],
            mappings=[{'label': label, 'value': name} for label, name in
                (('Linear', 'linear'), ('Log', 'log'), ('SymLog', 'symlog'), ('Zero-centered', 'zero-centered'))],
            color_scales=[{'label': label, 'value': name} for label, name in
                (('Global', 'global'), ('2 regions', '2'), ('3 regions', '3'))],
            axis_overrides=[{'label': label, 'value': value or ''} for value, label in AXIS_OVERRIDE_ITEMS],
            signal_presets=[{'label': text, 'value': text} for text in
                (RAW_LABEL, DERIVATIVE_LABEL, 'PL (arb. units)', 'Signal (arb. units)')],
            title_presets=list(dict.fromkeys(['Manual', 'Current group', *w._title_presets,
                result['title_preset']])), title_fields=list(FIELDS))
        return result

    def _error(self, field, message):
        self._error_field, self._error_text = field, str(message)
        self.draftChanged.emit()
        self.reveal(field)

    def reveal(self, field):
        section = 'ranges' if field in ('xmin', 'xmax', 'ymin', 'ymax', 'axis_override') else (
            'colors' if field in NUMBERS or field in ('colormap', 'mapping', 'color_scale') else 'labels')
        owner = self.owner_key()
        self.window._set_dat_details_requested(True)
        self.window.request_task(2)

        def focus_owner():
            if not self._closed and owner == self.owner_key():
                self.revealRequested.emit(section, field)
                self.focusRequested.emit(section, field)

        # Selecting View mounts its Loader. Address that new editor only after
        # the shell has projected the task, and retire stale owner requests.
        QTimer.singleShot(0, self, focus_owner)

    @Slot(str, str, str)
    def draftNumber(self, field, text, owner):
        if self.allowed(owner) and field in NUMBERS:
            drafts = self._drafts.setdefault(self.owner_key(), {})
            # A round trip through the display projection must preserve the
            # exact scientific value and must not introduce an implicit Fix.
            projected = f'{self.number(field):.{NUMBERS[field][2]}f}'
            if text == projected:
                drafts.pop(field, None)
            else:
                drafts[field] = text
            self._error_field = self._error_text = ''
            self.draftChanged.emit()
            self.stateChanged.emit()
            self.window._schedule_project_modified()

    def validate_editor(self):
        candidate = {}
        for field, text in self._drafts.get(self.owner_key(), {}).items():
            try:
                value = float(text.strip())
                low, high, _decimals, _step = NUMBERS[field]
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError()
            except (TypeError, ValueError):
                self._error(field, f'Enter a number between {NUMBERS[field][0]:g} and {NUMBERS[field][1]:g}.')
                raise ValueError(f'Correct the pending {field} editor.') from None
            projected = float(f'{self.number(field):.{NUMBERS[field][2]}f}')
            if value != projected:
                candidate[field] = value
        return {'owner': self.ownerToken, 'numbers': candidate,
                'fields': tuple(self._drafts.get(self.owner_key(), {}))}

    def commit_candidate(self, candidate):
        if candidate['owner'] != self.ownerToken:
            raise ValueError('The property editor belongs to a different graph.')
        numbers = candidate['numbers']
        if numbers:
            self.window._commit_dat_numbers(numbers)
        drafts = self._drafts.get(self.owner_key(), {})
        for field in candidate.get('fields', numbers):
            drafts.pop(field, None)
        self._error_field = self._error_text = ''
        self.draftChanged.emit()
        self.syncFromOwner()

    def finish_editor(self):
        self.commit_candidate(self.validate_editor())

    def has_project_drafts(self):
        # Valid template text is already canonical; preset names are UI-only.
        if any(self._drafts.values()):
            return True
        values = self._templates.get(self.owner_key(), {})
        preset = self.window._title_settings.get('preset') or BUILTIN_PRESET
        defaults = {'title_pattern': preset['pattern'],
                    'title_angle_map': ';'.join(f'{a}={b}' for a, b in preset['angle_map'].items())}
        return any(values.get(field, text) != text for field, text in defaults.items())

    @Slot(str, str, result=bool)
    def commitNumber(self, field, owner):
        if not self.allowed(owner) or field not in NUMBERS:
            return False
        try:
            self.window._finish_graph_editors(capture_science=False)
            return True
        except ValueError as exc:
            if not self._error_text:
                self._error(field, exc)
            return False

    @Slot(str, int, str, result=bool)
    def stepNumber(self, field, steps, owner):
        if not self.allowed(owner) or field not in NUMBERS:
            return False
        try:
            pending = self.validate_editor()['numbers']
            value = pending.get(field, self.number(field)) + steps * NUMBERS[field][3]
            value = max(NUMBERS[field][0], min(NUMBERS[field][1], value))
            self.draftNumber(field, f'{value:.{NUMBERS[field][2]}f}', owner)
            return self.commitNumber(field, owner)
        except ValueError:
            return False

    @Slot(str, bool, str, result=bool)
    def setFlag(self, field, value, owner):
        if not self.allowed(owner):
            return False
        w, v = self.window, self.window._dat_view()
        paths = RANGE_FIELDS if field.endswith('_lock') else ('view.' + field,)
        if field not in (*[name + '_lock' for name in BOUNDS], 'fix_annotations',
                          'signal_title_auto', 'fix_regions', 'fix_region_edges'):
            return False
        if field in ('fix_regions', 'fix_region_edges'):
            paths = STYLE_FIELDS
        with w._edit_transaction('DAT property', paths):
            if field.endswith('_lock'):
                name = field.removesuffix('_lock')
                v.locks[name] = value
                w._on_view_lock_toggled(name, value)
            elif field.startswith('fix_region'):
                key = 'regions' if field == 'fix_regions' else 'region_edges'
                (w.workspace.active or w._unregistered_session).fixed_recipes[key].enabled = value
                getattr(w, '_remember_fixed_' + key)()
            else:
                setattr(v, field, value)
                getattr(w, '_on_fix_annotations_toggled' if field == 'fix_annotations' else '_on_signal_auto_toggled')(value)
            w._capture_dataset_controls(visual_only=True)
        self.syncFromOwner()
        return True

    @Slot(str, str, str, result=bool)
    def setChoice(self, field, value, owner):
        if not self.allowed(owner):
            return False
        w, v = self.window, self.window._dat_view()
        if field == 'title_preset':
            if value not in self.state['title_presets']:
                return False
            w._on_title_preset_selected(value)
        elif field == 'axis_override':
            if not self.state['source_axis_enabled'] or value not in [x['value'] for x in self.state['axis_overrides']]:
                return False
            try:
                w._finish_graph_editors()
            except ValueError as exc:
                self._error(self._error_field or 'xmin', exc)
                return False
            self._axis_override = value or None
            w._on_x_axis_override_async()
        elif field in ('colormap', 'mapping', 'color_scale'):
            choices = self.state[{'colormap': 'colormaps', 'mapping': 'mappings', 'color_scale': 'color_scales'}[field]]
            if value not in [item['value'] for item in choices]:
                return False
            with w._edit_transaction('DAT display', STYLE_FIELDS + ('view.norm',)):
                if field == 'colormap':
                    v.colormap = value
                    w._on_colormap_changed()
                elif field == 'mapping':
                    v.norm = NormSpec(value, v.norm.linthresh)
                    w._on_color_mapping_changed()
                else:
                    v.color_scale_mode = value
                    w._on_color_scale_changed()
                w._capture_dataset_controls(visual_only=True)
        else:
            return False
        self.syncFromOwner()
        return True

    @Slot(str, str, str, result=bool)
    def setLabel(self, field, text, owner):
        if not self.allowed(owner) or field not in self.window._dat_view().annotations:
            return False
        self.window._save_plot_annotation(field, text)
        self.syncFromOwner()
        return True

    @Slot(str, result=bool)
    def finishLabels(self, owner):
        if not self.allowed(owner):
            return False
        self.window._finish_annotation_edit()
        self.window._finish_history_edit()
        self.syncFromOwner()
        return True

    @Slot(str, str, str, result=bool)
    def setTemplate(self, field, text, owner):
        if not self.allowed(owner) or field not in ('title_pattern', 'title_angle_map', 'title_preset_name'):
            return False
        self._templates.setdefault(self.owner_key(), {})[field] = text
        if field != 'title_preset_name':
            self.window._on_title_template_edited()
        self.window._schedule_project_modified()
        self.syncFromOwner()
        return True

    @Slot(str, bool, str)
    def setDisclosure(self, section, expanded, owner):
        if self.allowed(owner):
            saved = self._disclosures.setdefault(self.owner_key(), {})
            if saved.get(section) != expanded:
                saved[section] = expanded
                self.stateChanged.emit()

    @Slot(str, str, result=bool)
    def command(self, action, owner):
        if not self.allowed(owner):
            return False
        functions = {'apply_view': self.window._plot_cube, 'auto_view': self.window._on_auto_view,
            'auto_colors': self.window._on_auto_colors, 'template_apply': self.window._apply_current_title_template,
            'template_save': self.window._save_title_preset}
        if action not in functions:
            return False
        try:
            self.window._finish_graph_editors(capture_science=False)
            functions[action]()
        except ValueError as exc:
            self._error(self._error_field or 'xmin', exc)
            return False
        self.syncFromOwner()
        return True

    @Slot()
    def syncFromOwner(self):
        self.stateChanged.emit()
        owner = self.owner_key()
        if owner != self._projected_owner:
            self._projected_owner = owner
            self.draftChanged.emit()

    def retire_owner(self, dataset_id=None, graph_id=None):
        for values in (self._drafts, self._templates, self._disclosures, self._errors, self._axis_overrides, self._status):
            for key in list(values):
                if (dataset_id is None or key[1] == dataset_id) and (graph_id is None or key[2] == graph_id):
                    values.pop(key)
        self.syncFromOwner()

    def snapshot_editor_state(self):
        """Detach runtime editor text for a reversible project installation."""
        return deepcopy((self._drafts, self._templates, self._disclosures,
                         self._errors, self._axis_overrides, self._status,
                         self._projected_owner))

    def restore_editor_state(self, state):
        (self._drafts, self._templates, self._disclosures, self._errors,
         self._axis_overrides, self._status, self._projected_owner) = deepcopy(state)
        self.stateChanged.emit()
        self.draftChanged.emit()
