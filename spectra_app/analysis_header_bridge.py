"""Shared, owner-scoped Analysis context and Gate navigation for Qt Quick."""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import math

import numpy as np
from PySide6.QtCore import QObject, Property, QLocale, Signal, Slot
from PySide6.QtGui import QDoubleValidator, QIntValidator, QValidator


@dataclass(frozen=True)
class GateCandidate:
    owner: str
    drafts: tuple
    index: int | None


class AnalysisHeaderBridge(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._closed = False
        self._drafts = {}
        self._errors = {}
        self._context = dict(title='Analysis — no DAT selected', summary='', warning='', details='')

    def owner_key(self):
        w = self.window
        cube = w.current_cube
        if cube is None or not cube.gate.size:
            return None
        session = w.workspace.active or w._unregistered_session
        graph = session.active_graph
        doc = w._workbench_document()
        identity = ('named', id(doc), doc.input_version) if doc else (
            'dat', id(cube), w._data_generation)
        return (id(w.workspace), session.dataset_id, graph.graph_id, id(graph), *identity)

    @Property(str, notify=stateChanged)
    def ownerToken(self):
        return '|'.join(map(str, self.owner_key() or ('empty', id(self.window.workspace),
                                                     id(self.window._unregistered_session))))

    def allowed(self, owner=None):
        from shiboken6 import isValid
        return (not self._closed and isValid(self.window)
                and not getattr(self.window, '_close_pending', False)
                and self.owner_key() is not None
                and (owner is None or owner == self.ownerToken))

    enabled = Property(bool, lambda self: self.allowed(), notify=stateChanged)

    @Property(bool, notify=stateChanged)
    def backEnabled(self):
        action = getattr(self.window, 'back_to_workspace_action', None)
        return (not self._closed and not self.window._project_busy()
                and not self.window._close_pending and action is not None and action.isEnabled())

    errorText = Property(str, lambda self: self._errors.get(self.owner_key(), ('', ''))[1], notify=stateChanged)
    errorField = Property(str, lambda self: self._errors.get(self.owner_key(), ('', ''))[0], notify=stateChanged)

    def _format_gate(self, value):
        return self._display_locale().toString(float(value), 'f', 6)

    def _display_locale(self):
        locale = QLocale()
        locale.setNumberOptions(locale.numberOptions() | QLocale.OmitGroupSeparator)
        return locale

    def _rounded_gate(self, value):
        return QLocale().toDouble(self._format_gate(value))[0]

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        w = self.window
        live = self.owner_key() is not None
        index = w._current_gate_index() if live else 0
        count = int(w.current_cube.gate.size) if live else 0
        drafts = self._drafts.get(self.owner_key(), {})
        return dict(self._context,
                    gateText=drafts.get('gate', self._format_gate(w.current_cube.gate[index]) if live else '0.000000'),
                    indexText=drafts.get('index', self._display_locale().toString(index + 1)), count=count, index=index + 1,
                    canPrevious=self.enabled and index > 0,
                    canNext=self.enabled and index < count - 1)

    def refresh(self, context=None):
        if context is not None:
            self._context = dict(context)
        if self.owner_key() is None:
            self._context = dict(title='Analysis — no DAT selected', summary='', warning='', details='')
        self.stateChanged.emit()

    def retire_owner(self, dataset_id=None, graph_id=None):
        for cache in (self._drafts, self._errors):
            for key in tuple(cache):
                if dataset_id is None or (key[1] == dataset_id and (graph_id is None or key[2] == graph_id)):
                    cache.pop(key, None)
        self.refresh()

    def snapshot_editor_state(self):
        return deepcopy((self.owner_key(), self._drafts, self._errors, self._context))

    def restore_editor_state(self, state):
        owner, self._drafts, self._errors, self._context = deepcopy(state)
        current = self.owner_key()
        # Reset revoked the old generation; the restored scientific owner gets
        # its unfinished text under the new input lifetime.
        if owner is not None and current is not None and owner[:-1] == current[:-1]:
            for cache in (self._drafts, self._errors):
                if owner in cache:
                    cache[current] = cache.pop(owner)
        self.stateChanged.emit()
        self.draftChanged.emit()

    def close(self):
        self._closed = True
        self.retire_owner()

    def _error(self, field, message):
        self._errors[self.owner_key()] = field, message
        self.stateChanged.emit()
        self.focusRequested.emit(field, self.ownerToken)
        raise ValueError(message)

    def parse_gate(self, text):
        """Match the former Qt editor's locale grammar, precision, and range."""
        gates = self.window.current_cube.gate
        low, high = self._rounded_gate(np.nanmin(gates)), self._rounded_gate(np.nanmax(gates))
        validator = QDoubleValidator(low, high, 6)
        validator.setLocale(QLocale())
        validator.setNotation(QDoubleValidator.ScientificNotation)
        text = text.strip()
        decimal = QLocale().decimalPoint()
        group = QLocale().groupSeparator()
        point = text.find(decimal)
        # QDoubleSpinBox counts the exponent suffix after a decimal point in
        # its precision limit, and permits loosely grouped integral text.
        if (point >= 0 and (len(text) - point - 1 > 6 or group in text[point + 1:])):
            raise ValueError('Enter a Gate value with at most 6 characters after the decimal point.')
        if group and (text.startswith(group) or group * 2 in text):
            raise ValueError('Enter a valid Gate value.')
        value, okay = QLocale().toDouble(text)
        normalized = text
        if not okay and group and (low <= -1000 or high >= 1000):
            normalized = text.replace(group, '')
            value, okay = QLocale().toDouble(normalized)
        if (validator.validate(normalized, 0)[0] != QValidator.Acceptable or not okay
                or not math.isfinite(value) or not low <= value <= high):
            raise ValueError('Enter a finite Gate value within the displayed range (up to 6 decimal places).')
        return value

    def parse_index(self, text):
        count = int(self.window.current_cube.gate.size)
        validator = QIntValidator(1, count)
        validator.setLocale(QLocale())
        text = text.strip()
        value, okay = QLocale().toInt(text)
        group = QLocale().groupSeparator()
        if not okay and count >= 1000 and not text.startswith(group) and group * 2 not in text:
            text = text.replace(group, '')
            value, okay = QLocale().toInt(text)
        if validator.validate(text, 0)[0] != QValidator.Acceptable or not okay:
            raise ValueError('Enter a Gate index from 1 to ' + str(self.window.current_cube.gate.size) + '.')
        return value - 1

    def validate_editor(self):
        owner = self.ownerToken
        drafts = self._drafts.get(self.owner_key(), {})
        if not self.allowed():
            return GateCandidate('', (), None)
        parsed = {}
        for field, text in drafts.items():
            try:
                parsed[field] = self.parse_gate(text) if field == 'gate' else self.parse_index(text)
            except ValueError as exc:
                self._error(field, str(exc))
        index = self.window._current_gate_index()
        target = None
        if 'index' in parsed and parsed['index'] != index:
            target = parsed['index']
        elif 'gate' in parsed:
            nearest = int(np.argmin(np.abs(self.window.current_cube.gate - parsed['gate'])))
            if nearest != index:
                target = nearest
        return GateCandidate(owner, tuple(drafts.items()), target)

    def commit_candidate(self, candidate):
        if not candidate.owner:
            return
        if not self.allowed(candidate.owner):
            raise ValueError('This Gate editor is no longer active.')
        key = self.owner_key()
        if candidate.drafts != tuple(self._drafts.get(key, {}).items()):
            raise ValueError('The Gate editor changed after validation.')
        if candidate.index is not None:
            # Preserve the existing named editor guard before consuming Gate text.
            if self.window._workbench_document() is not None:
                named = self.window.analysis_workbench.validate_editor()
                if named is not None and self.window._project_busy() and any(named):
                    raise ValueError('Wait for the current Analysis operation before committing edits.')
            self.window._set_current_gate_from_index(candidate.index, update_plot=True)
        self._drafts.pop(key, None)
        self._errors.pop(key, None)
        self.refresh()

    def has_project_drafts(self):
        if not self.allowed():
            return False
        index = self.window._current_gate_index()
        for field, text in self._drafts.get(self.owner_key(), {}).items():
            try:
                if field == 'index':
                    if self.parse_index(text) != index:
                        return True
                else:
                    nearest = int(np.argmin(np.abs(self.window.current_cube.gate - self.parse_gate(text))))
                    if nearest != index:
                        return True
            except ValueError:
                return True
        return False

    @Slot(str, str, str, result=bool)
    def setDraft(self, field, text, owner):
        if field not in ('gate', 'index') or not self.allowed(owner):
            return False
        self._drafts.setdefault(self.owner_key(), {})[field] = text
        self._errors.pop(self.owner_key(), None)
        self.draftChanged.emit()
        self.stateChanged.emit()
        return True

    @Slot(str, result=bool)
    def commit(self, owner):
        if not self.allowed(owner):
            return False
        try:
            self.commit_candidate(self.validate_editor())
            return True
        except ValueError as exc:
            self.window.publish_status(str(exc))
            return False

    @Slot(int, str, result=bool)
    def step(self, delta, owner):
        if not self.allowed(owner):
            return False
        try:
            candidate = self.validate_editor()
            index = self.window._current_gate_index() if candidate.index is None else candidate.index
            self.commit_candidate(GateCandidate(candidate.owner, candidate.drafts,
                                               max(0, min(self.state['count'] - 1, index + delta))))
            return True
        except ValueError as exc:
            self.window.publish_status(str(exc))
            return False

    @Slot(str, result=bool)
    def back(self, owner):
        if owner != self.ownerToken or not self.backEnabled:
            return False
        self.window.back_to_workspace_action.trigger()
        return True
