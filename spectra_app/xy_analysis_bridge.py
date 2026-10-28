"""Owner-checked Qt Quick editors for the comparison-owned XY analysis."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import math
import re
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QObject, Property, Signal, Slot

from .analysis_document import AnalysisSeed
from .peak_candidates import CandidateFilter, candidate_filter_preset
from .xy_analysis import AnalysisSettings, XYAnalysisProgress

# name: (record attribute, decimal places, minimum, maximum, integral)
FIELDS = {
    'prominence': ('min_prominence_fraction', 3, 0, 1, False),
    'noise': ('min_noise_strength', 2, 0, 1e9, False),
    'distance': ('min_distance', 6, 0, 1e12, False),
    'candidateWidthMin': ('min_width', 6, 0, 1e12, False),
    'candidateWidthMax': ('max_width', 6, 0, 1e12, False),
    'maxCount': ('max_count', 0, 0, 10000, True),
    'sgWindow': ('sg_window', 0, 3, 10001, True),
    'sgOrder': ('sg_polyorder', 0, 2, 10, True),
    'widthMin': ('width_min', 9, 1e-9, 1e12, False),
    'widthMax': ('width_max', 9, 0, 1e12, False),
}
FILTER_FIELDS = set(tuple(FIELDS)[:6])
CHOICES = {'mode': ('Peaks', 'Dips', 'Both'), 'detector': ('Original', 'D2'),
           'model': ('Gaussian', 'Lorentzian', 'Voigt'), 'preset': ('Loose', 'Balanced', 'Strict', 'Custom')}


def _display(value, decimals):
    return str(value) if type(value) is int else format(value, f'.{decimals}f')


class XYAnalysisBridge(QObject):
    changed = Signal()
    revealRequested = Signal(str)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self._drafts = {}
        self._tokens = {}
        self._selection = None
        self._generation = 0
        self._errors = {}
        self._installing = False

    state = Property('QVariantMap', lambda self: dict(self.panel._state), notify=changed)
    values = Property('QVariantMap', lambda self: {
        'mode': self.panel.settings().mode, 'model': self.panel.settings().model,
        'detector': self.panel.settings().detector, 'preset': self.panel.filter_settings().preset}, notify=changed)
    drafts = Property('QVariantMap', lambda self: {key: self._drafts.get(key, self._formatted(key)) for key in FIELDS}, notify=changed)
    errors = Property('QVariantMap', lambda self: dict(self._errors), notify=changed)
    canAccept = Property(bool, lambda self: True, constant=True)
    ownerToken = Property(str, lambda self: str(self._generation), notify=changed)

    def _record_value(self, key):
        record = self.panel.filter_settings() if key in FILTER_FIELDS else self.panel.settings()
        return getattr(record, FIELDS[key][0])

    def _formatted(self, key):
        return _display(self._record_value(key), FIELDS[key][1])

    def _owner_key(self):
        key = self.panel._key
        return (tuple(sorted(self.panel.page._selected_ids())),
                id(self.panel.progress) if self.panel.progress is not None else None,
                key[:-1] if key is not None else None,
                self.panel.page.command_bridge.ownerToken)

    def refresh(self):
        self.refresh_owner()
        self._validate()
        self.changed.emit()

    def retire_editors(self):
        self._generation += 1
        self._tokens.clear()

    def load(self, settings, filters, *, preserve_drafts=False):
        self.panel._settings, self.panel._filters = settings, filters
        if not preserve_drafts:
            self._drafts.clear()
        self._validate()
        self.changed.emit()

    @Slot(str, result=str)
    def beginEdit(self, field):
        self.panel.sync()
        self.refresh_owner()
        if (field not in FIELDS and field not in CHOICES or not self.panel._state.get('editable')
                or self.panel._disposed or not self.panel.page.command_bridge.authorize(
                    self.panel.page.command_bridge.ownerToken)):
            return ''
        token = uuid4().hex
        self._tokens[token] = (field, self._generation)
        return token

    def _valid_token(self, token, field=None):
        # A selection roundtrip has a new generation even if its final IDs match.
        self.panel.sync()
        self.refresh_owner()
        item = self._tokens.get(token)
        current = (not self.panel._disposed and self.panel._owns_selection() and item is not None
                   and item[1] == self._generation and (field is None or item[0] == field))
        if not current or not self.panel.page.command_bridge.authorize(self.panel.page.command_bridge.ownerToken):
            return False
        # Activation can synchronously change selection or replace progress
        # without changing the document command owner. Recheck this transaction.
        self.panel.sync()
        self.refresh_owner()
        return (not self.panel._disposed and self.panel._owns_selection()
                and self._tokens.get(token) == item and item[1] == self._generation)

    def refresh_owner(self):
        selection = self._owner_key()
        if selection != self._selection:
            previous = self._selection
            self._selection = selection
            # An accepted own setting edit may replace its detector progress.
            # That changes no source ownership; an external replacement does.
            own_record_refresh = (self._installing and previous is not None
                                  and selection[0] == previous[0] and selection[2:] == previous[2:])
            if not own_record_refresh:
                self.retire_editors()

    @Slot(str, str, str, result=bool)
    def setDraft(self, field, text, token):
        if field not in FIELDS or not self._valid_token(token, field):
            return False
        self._drafts[field] = text
        self.panel.sync()
        return True

    @Slot(str)
    def endEdit(self, token):
        self._tokens.pop(token, None)

    def _parsed(self):
        numbers, errors = {}, {}
        for key, (attribute, decimals, low, high, integral) in FIELDS.items():
            text = self._drafts.get(key, self._formatted(key)).strip()
            try:
                # Untouched display precision is presentation only, never a round trip.
                untouched = text == self._formatted(key)
                if untouched:
                    value = self._record_value(key)
                else:
                    if integral and not re.fullmatch(r'[+]?[0-9]+', text):
                        raise ValueError('Enter an exact integer.')
                    value = int(text) if integral else float(text)
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f'Use a value from {low:g} to {high:g}.')
                if not untouched and not integral:
                    # Reject excess effective fractional precision; never silently
                    # quantize a scientific value. Equivalent trailing zeroes are
                    # harmless, and exponents follow the same numeric precision.
                    decimal = Decimal(text).normalize()
                    if decimal.as_tuple().exponent < -decimals:
                        errors[key] = f'Use at most {decimals} decimal places for this value.'
                        continue
                numbers[key] = value
            except (ValueError, OverflowError, InvalidOperation):
                errors[key] = ('Enter an exact integer' if integral else 'Enter a finite number') + f' from {low:g} to {high:g}.'
        if errors:
            return None, None, errors
        settings = replace(self.panel.settings(), **{FIELDS[k][0]: v for k,v in numbers.items() if k not in FILTER_FIELDS})
        filters_values = {FIELDS[k][0]: v for k,v in numbers.items() if k in FILTER_FIELDS}
        try:
            settings.validate()
        except ValueError as exc:
            for key in ('sgWindow', 'sgOrder') if settings.sg_window % 2 != 1 or settings.sg_polyorder >= settings.sg_window else ('widthMin', 'widthMax'):
                errors[key] = str(exc)
        try:
            filters = replace(self.panel.filter_settings(), **filters_values)
        except ValueError as exc:
            errors['candidateWidthMin'] = errors['candidateWidthMax'] = str(exc)
            filters = None
        return settings, filters, errors

    def _validate(self):
        _, _, self._errors = self._parsed()
        return next(iter(self._errors.values()), '')

    def validation_error(self):
        return self._validate()

    def commit_current(self):
        if not self.panel._owns_selection():
            return True  # Browsing must not commit the retained owner's drafts.
        settings, filters, errors = self._parsed()
        self._errors = errors
        if errors:
            self.changed.emit()
            self.revealRequested.emit(next(iter(errors)))
            return False
        old_filter = self.panel.filter_settings()
        if replace(filters, preset=old_filter.preset) != old_filter:
            filters = replace(filters, preset='Custom')
        self._install_records(settings, filters)
        return True

    def _install_records(self, settings, filters):
        self.panel._settings, self.panel._filters = settings, filters
        self._drafts.clear()
        self._installing = True
        try:
            self.panel._records_committed()
        finally:
            self._installing = False

    @Slot(str, result=bool)
    def commit(self, token):
        return self._valid_token(token) and self.commit_current()

    @Slot(str, str, str, result=bool)
    def choose(self, field, value, token):
        if field not in CHOICES or value not in CHOICES[field] or not self._valid_token(token, field):
            return False
        settings, filters, errors = self._parsed()
        self._errors = errors
        if errors:
            self.changed.emit()
            self.revealRequested.emit(next(iter(errors)))
            return False
        if field == 'preset':
            filters = candidate_filter_preset(value) if value != 'Custom' else replace(filters, preset=value)
        else:
            settings = replace(settings, **{field: value})
            old_filter = self.panel._filters
            if replace(filters, preset=old_filter.preset) != old_filter:
                filters = replace(filters, preset='Custom')
        self._install_records(settings, filters)
        return True

    @Slot(str, int, str, result=bool)
    def step(self, field, direction, token):
        if field not in FIELDS or direction not in (-1, 1) or not self._valid_token(token, field):
            return False
        settings, filters, errors = self._parsed()
        if errors:
            self._errors = errors
            self.revealRequested.emit(next(iter(errors)))
            self.changed.emit()
            return False
        attr, decimals, low, high, integral = FIELDS[field]
        record = filters if field in FILTER_FIELDS else settings
        amount = .01 if field == 'prominence' else 2 if field == 'sgWindow' else 1
        value = min(high, max(low, getattr(record, attr) + direction * amount))
        value = int(value) if integral else round(value, decimals)
        try:
            candidate = replace(record, **{attr: value})
        except ValueError as exc:
            self._errors = {field: str(exc)}
            self.changed.emit(); self.revealRequested.emit(field)
            return False
        if field in FILTER_FIELDS:
            filters = replace(candidate, preset='Custom') if candidate != self.panel._filters else candidate
        else:
            settings = candidate
        try:
            settings.validate()
        except ValueError as exc:
            self._errors = {field: str(exc)}
            self.changed.emit(); self.revealRequested.emit(field)
            return False
        old_filter = self.panel._filters
        if replace(filters, preset=old_filter.preset) != old_filter:
            filters = replace(filters, preset='Custom')
        self._install_records(settings, filters)
        return True

    @Slot(str, str, result=bool)
    def command(self, name, owner):
        panel = self.panel
        panel.sync()
        if owner != self.ownerToken or not panel.page.command_bridge.authorize(
                panel.page.command_bridge.ownerToken):
            return False
        command = {
            'find': ('findEnabled', lambda: panel.start('find')),
            'fit': ('fitEnabled', lambda: panel.start('fit')),
            'resume': ('resumeEnabled', panel.resume_analysis),
            'start': ('startEnabled', panel.analyze_selected),
            'settings': ('editable', panel.show_settings), 'seeds': ('seedsEnabled', panel.edit_seeds),
            'save': ('saveEnabled', panel.save_results), 'graph': ('graphEnabled', panel.create_analysis_graph),
            'interval': (None, panel.page.edit_x_interval),
        }.get(name)
        if command is not None and not panel._disposed and (command[0] is None or panel._state.get(command[0])):
            command[1]()
            return True
        return False


SEED_TEXT = {'position': 'position', 'kind': 'kind', 'amplitude': 'amplitude', 'width': 'width',
             'gamma': 'gamma', 'centerMin': 'center_min', 'centerMax': 'center_max'}


def _seed_text(value):
    return '' if value is None else format(value, '.10g') if isinstance(value, float) else str(value)


class XYSeedsBridge(QObject):
    changed = Signal()
    revealRequested = Signal(str, str)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self._progress = panel.progress
        self._key = panel.progress.captured.key
        self._revision = panel._revision
        self._input_owner = panel.page.command_bridge.ownerToken
        self._closed = False
        self._accepted = False
        self._rows = []
        self._originals = {}
        self._resets = set()
        self._selected = []
        self._error = ''
        self._errors = {}
        for seed in self._progress.seeds:
            self._add_row(seed)
        fit = self._progress.fit_result
        self._result_model = fit.model if fit is not None else ''
        self._results = [] if fit is None else [dict(center=_seed_text(center), height=_seed_text(height),
            width=_seed_text(width), gamma=_seed_text(fit.gammas[index]) if fit.model == 'Voigt' else '')
            for index, (center,height,width) in enumerate(zip(fit.centers,fit.amplitudes,fit.widths))]

    rows = Property('QVariantList', lambda self: [dict(row) for row in self._rows], notify=changed)
    results = Property('QVariantList', lambda self: self._results, notify=changed)
    resultModel = Property(str, lambda self: self._result_model, notify=changed)
    selectedIds = Property('QStringList', lambda self: list(self._selected), notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    errors = Property('QVariantMap', lambda self: dict(self._errors), notify=changed)
    canAccept = Property(bool, lambda self: self._accepted or self._active(), notify=changed)

    def _active(self):
        if not self._closed and not self.panel._disposed:
            self.panel.sync()
        return (not self._closed and not self.panel._disposed and self.panel.progress is self._progress
                and self.panel._owns_selection() and self.panel._revision == self._revision
                and self._progress.captured.key == self._key
                and self.panel.page.command_bridge.allowed(self._input_owner))

    def _authorize(self):
        return (self._active() and self.panel.page.command_bridge.authorize(self._input_owner)
                and self._active())

    def _add_row(self, seed):
        self._originals[seed.seed_id] = seed
        self._rows.append(dict(seedId=seed.seed_id, enabled=seed.enabled, fixedCenter=seed.fixed_center,
                               **{key: _seed_text(getattr(seed, attr)) for key,attr in SEED_TEXT.items()}))

    @Slot('QStringList')
    def setSelected(self, ids):
        if self._authorize():
            self._selected = [identity for identity in dict.fromkeys(ids) if identity in self._originals]
            self.changed.emit()

    @Slot(str, str, 'QVariant', result=bool)
    def edit(self, seed_id, field, value):
        if not self._authorize() or field not in (*SEED_TEXT, 'enabled', 'fixedCenter'):
            return False
        if field in ('enabled', 'fixedCenter') and type(value) is not bool:
            return False
        row = next((row for row in self._rows if row['seedId'] == seed_id), None)
        if row is None:
            return False
        row[field] = value if type(value) is bool else str(value)
        self._error = ''; self._errors.clear()
        self.changed.emit()
        return True

    @Slot()
    def add(self):
        if self._authorize():
            data = self._progress.captured.data
            self._add_row(AnalysisSeed(float(np.median(data.x[data.valid])), manual=True))
            self.changed.emit()

    @Slot()
    def excludeSelected(self):
        if self._authorize():
            for row in self._rows:
                if row['seedId'] in self._selected:
                    row['enabled'] = False
            self.changed.emit()

    @Slot()
    def useFilterSelected(self):
        if not self._authorize():
            return
        for row in self._rows:
            if row['seedId'] not in self._selected:
                continue
            original = self._originals[row['seedId']]
            candidate = next((c for c in self._progress.candidate_pool if (c.index,c.kind) == (original.source_index,original.kind)), None)
            if candidate is not None:
                self._resets.add(original.seed_id)
                row['position'] = _seed_text(candidate.position)
                self._originals[original.seed_id] = replace(original, position=candidate.position)
        self.changed.emit()

    def _parse_rows(self):
        seeds, errors = [], {}
        for row in self._rows:
            identity = row['seedId']; original = self._originals[identity]
            values = {}
            for key, attr in SEED_TEXT.items():
                text = row[key].strip()
                stored = getattr(original, attr)
                try:
                    values[attr] = (text.lower() if key == 'kind' else stored if text == _seed_text(stored)
                                    else None if key != 'position' and not text else float(text))
                    if key != 'kind' and values[attr] is not None and not math.isfinite(values[attr]):
                        raise ValueError('Enter a finite number.')
                except (ValueError, OverflowError) as exc:
                    errors[f'{identity}.{key}'] = str(exc) or 'Enter a valid number.'
            if any(key.startswith(identity + '.') for key in errors):
                continue
            for field, condition, message in (
                ('kind', values['kind'] not in ('peak', 'dip'), 'Seed kind must be peak or dip.'),
                ('width', values['width'] is not None and values['width'] <= 0, 'Seed width must be positive.'),
                ('gamma', values['gamma'] is not None and values['gamma'] <= 0, 'Seed gamma must be positive.'),
                ('amplitude', values['amplitude'] is not None and (values['amplitude'] == 0 or
                    (values['amplitude'] > 0) != (values['kind'] == 'peak')), 'Seed amplitude sign must match peak/dip kind.'),
                ('centerMin', values['center_min'] is not None and values['position'] < values['center_min'], 'Seed center must be above its lower bound.'),
                ('centerMax', values['center_max'] is not None and values['position'] > values['center_max'], 'Seed center must be below its upper bound.'),
                ('centerMax', values['center_min'] is not None and values['center_max'] is not None and
                    values['center_min'] >= values['center_max'], 'Seed center bounds must be increasing.'),
            ):
                if condition:
                    errors[f'{identity}.{field}'] = message
            data = self._progress.captured.data
            low, high = data.x[data.valid][[0, -1]]
            if not low <= values['position'] <= high:
                errors[f'{identity}.position'] = 'Seed position must be inside the bound input.'
            if any(key.startswith(identity + '.') for key in errors):
                continue
            try:
                seed = replace(original, **values, enabled=row['enabled'], fixed_center=row['fixedCenter'])
                seed = replace(seed, manual=(seed.manual or seed != original) and identity not in self._resets)
                seeds.append(seed)
            except ValueError as exc:
                errors[f'{identity}.position'] = str(exc)
        return seeds, errors

    @Slot(result=bool)
    def accept(self):
        if not self._authorize():
            self._error = 'This analysis source changed. Reopen the seeds editor.'
            self.changed.emit(); return False
        seeds, self._errors = self._parse_rows()
        if self._errors:
            self._error = next(iter(self._errors.values()))
            identity, field = next(iter(self._errors)).split('.', 1)
            self.revealRequested.emit(identity, field)
            self.changed.emit(); return False
        try:
            # Validate and filter a detached record before exposing any mutation.
            preview = replace(self._progress, seeds=list(self._progress.seeds))
            preview.set_seeds(seeds)
            preview.apply_filter(self.panel.filter_settings())
            if preview.seeds != self._progress.seeds:
                self._progress.seeds = preview.seeds
                self._progress.fit_result = preview.fit_result
                self._progress.status, self._progress.message = preview.status, preview.message
                self.panel._revision += 1
                self.panel._request = None
                self.panel._result_ids = self.panel._graph_id = None
                self.panel._publish_progress(); self.panel._persist()
                self.panel._status = self.panel._summary()
                self.panel.page.render()
            self._accepted = self._closed = True
            self.changed.emit()
            return True
        except ValueError as exc:
            self._error = str(exc); self.changed.emit(); return False

    @Slot()
    def cancel(self):
        self.shutdown()

    def shutdown(self):
        self._closed = True
        self.changed.emit()
