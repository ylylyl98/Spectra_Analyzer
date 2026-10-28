"""Owner-scoped Qt Quick state for named Analysis Graph controls and editors."""
from __future__ import annotations

from dataclasses import replace
import math
from PySide6.QtCore import QObject, Property, Signal, Slot, Qt
from .analysis_document import AnalysisSeed, analysis_input_changed
from .peak_candidates import CandidateFilter, candidate_filter_preset

FILTER_FIELDS = ('min_prominence_fraction', 'min_noise_strength', 'min_width',
                 'max_width', 'min_distance', 'max_count')
NUMBER_FIELDS = {'xmin': 'fit_xmin', 'xmax': 'fit_xmax', 'd2Window': 'd2_window'}
SEED_FIELDS = {'position', 'kind', 'amplitude', 'width', 'gamma', 'bound', 'enabled', 'fixedCenter'}


def _text(value):
    return '' if value is None else str(value)


class AnalysisWorkbenchBridge(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)
    revealRequested = Signal(str, str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._closed = False
        self._drafts = {}
        self._seed_drafts = {}
        self._filter_context = None
        self._filter_draft = {}
        self._filter_preset = 'Balanced'
        self._inputs = {}
        self._selection = {}
        self._disclosures = {}
        self._errors = {}
        self._add = {}
        self._dialogs = {}
        self._binding = {}
        self._d2_note = ''

    def owner_key(self):
        w = self.window
        doc = w._workbench_document()
        if doc is None:
            return None
        return (id(w.workspace), w.workspace.active_dataset_id,
                w.workspace.active.active_graph_id, doc.input_version)

    @Property(str, notify=stateChanged)
    def ownerToken(self):
        return '|'.join(map(str, self.owner_key() or ()))

    def allowed(self, owner=None, *, busy=False):
        from shiboken6 import isValid
        w = self.window
        return (not self._closed and isValid(w) and not getattr(w, '_close_pending', False)
                and self.owner_key() is not None
                and (owner is None or owner == self.ownerToken)
                and (busy or not w._project_busy()))

    enabled = Property(bool, lambda self: self.allowed(busy=True), notify=stateChanged)
    canAccept = Property(bool, lambda self: self.allowed(), notify=stateChanged)
    busy = Property(bool, lambda self: self.window._project_busy(), notify=stateChanged)
    sourceAvailable = Property(bool, lambda self: self.allowed(busy=True) and
        self.window._workbench_document().source_graph_id in self.window.workspace.active.graphs,
        notify=stateChanged)
    auxLinecutVisible = Property(bool, lambda self: getattr(self.window, '_workbench_aux_linecut_visible', True),
        notify=stateChanged)
    errorText = Property(str, lambda self: self._errors.get(self.owner_key(), ('', ''))[1], notify=stateChanged)
    errorField = Property(str, lambda self: self._errors.get(self.owner_key(), ('', ''))[0], notify=stateChanged)

    def _error(self, field, message):
        self._errors[self.owner_key()] = field, str(message)
        section = 'seeds' if ':' in field else 'filter' if field in FILTER_FIELDS else 'detection'
        if section == 'seeds':
            self._selection[self.owner_key(), self.window._current_gate_index()] = field.split(':', 1)[0]
        self.stateChanged.emit()
        self.revealRequested.emit(section, field)
        self.focusRequested.emit(section, field)

    def _clear_error(self):
        self._errors.pop(self.owner_key(), None)

    def has_project_drafts(self):
        """Only durable edits for the current live owner affect the project marker."""
        if not self.allowed(busy=True):
            return False
        doc = self.window._workbench_document()
        for field, text in self._drafts.get(self.owner_key(), {}).items():
            try:
                value = float(text.strip())
                if not math.isfinite(value) or abs(value) > 1e12:
                    return True
                if field == 'd2Window' and (value != int(value) or not 3 <= value <= 10001):
                    return True
                if value != getattr(doc.science.analysis_controls, NUMBER_FIELDS[field]):
                    return True
            except (ValueError, OverflowError):
                return True
        for (gate, seed_id), fields in self._seed_drafts.get(self.owner_key(), {}).items():
            seed = next((seed for seed in doc.seeds_by_gate.get(gate, ()) if seed.seed_id == seed_id), None)
            if seed is None:
                continue
            try:
                if self.window._workbench_seed_values(seed, fields) != [seed]:
                    return True
            except (ValueError, OverflowError):
                return True
        return False

    def refresh(self):
        for host in tuple(self._dialogs.values()):
            if not host._finished and not host.bridge.allowed(busy=True):
                host.reject()
        self._sync_filter_context()
        self._refresh_binding()
        if self.window._workbench_document() is not None:
            self._d2_note = self.window._workbench_effective_d2_window()
        self.stateChanged.emit()

    def _refresh_binding(self):
        w, doc = self.window, self.window._workbench_document()
        if doc is None:
            return
        source = w.workspace.active.graphs.get(doc.source_graph_id)
        mode = self._inputs.get(self.owner_key(), doc.source_mode)
        self._binding[self.owner_key(), mode] = bool(source is not None and analysis_input_changed(
            w.workspace.active, source_mode=mode, gate_indices=source.gate_indices))

    def _sync_filter_context(self):
        doc = self.window._workbench_document()
        if doc is None:
            return
        gates = tuple(self.window._workbench_gates())
        context = self.owner_key(), gates
        if context != self._filter_context:
            self._filter_context = context
            applied = [doc.filters_by_gate.get(gate) for gate in gates]
            mixed = any(item != applied[0] for item in applied[1:])
            settings = doc.filters if mixed or applied[0] is None else applied[0]
            self._filter_draft = {field: _text(getattr(settings, field)) for field in FILTER_FIELDS}
            self._filter_preset = settings.preset

    def filter_settings(self, *, report=True):
        self._sync_filter_context()
        values = {}
        for field, text in self._filter_draft.items():
            try:
                number = float(text.strip())
                if not math.isfinite(number) or number < 0 or number > 1e12 or (field == 'max_count' and (number != int(number) or number > 10000)):
                    raise ValueError()
                values[field] = int(number) if field == 'max_count' else number
            except (ValueError, OverflowError):
                if report:
                    self._error(field, 'Enter a finite nonnegative ' + ('integer.' if field == 'max_count' else 'number.'))
                raise ValueError('Correct the pending candidate filter editor.') from None
        try:
            return CandidateFilter(preset=self._filter_preset, **values)
        except ValueError as exc:
            if report:
                self._error('max_width', str(exc))
            raise

    def _seed_rows(self, doc, gate):
        drafts = self._seed_drafts.get(self.owner_key(), {})
        rows = []
        for seed in doc.seeds_by_gate.get(gate, ()):
            bound = None if seed.center_min is None or seed.center_max is None else min(
                seed.position-seed.center_min, seed.center_max-seed.position)
            row = dict(seedId=seed.seed_id, enabled=seed.enabled, position=_text(seed.position),
                       kind=seed.kind, amplitude=_text(seed.amplitude), width=_text(seed.width),
                       gamma=_text(seed.gamma), fixedCenter=seed.fixed_center, bound=_text(bound),
                       gammaEnabled=doc.science.analysis_controls.fit_model == 'Voigt')
            for field, text in drafts.get((gate, seed.seed_id), {}).items():
                row[field] = text == 'true' if field in ('enabled', 'fixedCenter') else text
            rows.append(row)
        return rows

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        w, doc = self.window, self.window._workbench_document()
        if doc is None:
            return dict(owner='', busy=False, seeds=[], filter={}, error='', disclosures={})
        self._sync_filter_context()
        owner, gate = self.owner_key(), w._current_gate_index()
        controls, cube = doc.science.analysis_controls, doc.science.current_cube
        drafts = self._drafts.get(owner, {})
        gates = w._workbench_gates()
        applied = [doc.filters_by_gate.get(index) for index in gates]
        mixed = any(item != applied[0] for item in applied[1:])
        label = 'mixed' if mixed else applied[0].preset if applied[0] else 'not applied'
        try:
            pending = self.filter_settings(report=False)
            changed = any(item != pending for item in applied)
            suffix = ' · Pending: ' + pending.preset if changed else ''
        except ValueError:
            changed, suffix = False, ' · Pending: invalid settings'
        scope = f'All {len(gates)} Gates' if doc.gate_scope == 'all' else f'Gate {cube.gate[gate]:g}'
        source = w.workspace.active.graphs.get(doc.source_graph_id)
        mode = self._inputs.get(owner, doc.source_mode)
        can_bind = self._binding.get((owner, mode), False)
        seeds = doc.seeds_by_gate.get(gate, ())
        accepted = doc.accepted_seeds(gate)
        selection_key = owner, gate
        ids = {seed.seed_id for seed in seeds}
        if self._selection.get(selection_key) not in ids:
            self._selection.pop(selection_key, None)
        status = f'Status: {doc.gate_status.get(gate, "pending")} · {len(doc.science.batch_fit_result_map)} fitted Gates'
        summary = getattr(w, '_workbench_last_summary', None)
        if summary and summary[0] is doc and summary[1] == doc.job_token:
            status = summary[2]
        if w._project_busy() or getattr(w, '_workbench_running_job', None) is not None:
            status = getattr(w, '_workbench_status_text', '') or w._status_text or status
        low, high = controls.fit_xmin, controls.fit_xmax
        add = self._add.get(owner, {})
        return dict(owner=self.ownerToken, gate=gate, gateLabel=f'{cube.gate[gate]:g}', scope=doc.gate_scope,
            mode=controls.peak_mode, detector='D2' if controls.derivative_detection else 'Original',
            xmin=drafts.get('xmin', _text(low)), xmax=drafts.get('xmax', _text(high)),
            d2Window=drafts.get('d2Window', _text(controls.d2_window)), model=controls.fit_model,
            inputMode=mode, busy=w._project_busy(), d2Note=self._d2_note,
            sourceAvailable=source is not None, auxLinecutVisible=self.auxLinecutVisible,
            status=status, statusTooltip=status+'\n'+doc.gate_messages.get(gate, ''),
            counts=f'Gate {cube.gate[gate]:g}: {len(doc.candidate_pools.get(gate, ()))} candidates · {len(accepted)} accepted · {sum(not s.enabled for s in seeds)} excluded',
            filterPreset=self._filter_preset, filter=dict(self._filter_draft),
            filterState=f'{scope} · Applied: {label}{suffix}', filterCanApply=changed and not w._project_busy(),
            seeds=self._seed_rows(doc, gate), selectedSeedId=self._selection.get(selection_key, ''),
            detectedVisible=doc.overlay_styles['detected-peak'].visible,
            fittedVisible=doc.overlay_styles['fitted-peak'].visible, canBind=bool(can_bind and not w._project_busy()),
            bindText=f'Replace / reset {cube.gate.size} Gates…' if can_bind else 'Input unchanged',
            bindTooltip='Review replacement or duplicate before resetting input.' if can_bind else
                'Selected input already matches the frozen input.' if source else 'Source removed: frozen input retained.',
            canLinecut=not w._project_busy() and bool(accepted or gate in doc.science.batch_fit_result_map),
            addPosition=add.get('position', _text((low+high)/2)), addKind=add.get('kind', 'peak'),
            rebindCount=int(cube.gate.size), widthLabel='Sigma' if controls.fit_model in ('Gaussian', 'Voigt') else 'HWHM',
            xLabel=cube.energy_label, error=self.errorText, disclosures=dict(self._disclosures.get(owner, {})))

    @Slot(str, bool, str)
    def setDisclosure(self, section, expanded, owner):
        if self.allowed(owner, busy=True):
            self._disclosures.setdefault(self.owner_key(), {})[section] = expanded
            self.stateChanged.emit()

    @Slot(str, str, str, result=bool)
    def draftNumber(self, field, text, owner):
        if not self.allowed(owner):
            return False
        if field == 'addPosition':
            self._add.setdefault(self.owner_key(), {})['position'] = text
        elif field in NUMBER_FIELDS:
            self._drafts.setdefault(self.owner_key(), {})[field] = text
        else:
            return False
        self._clear_error()
        self.draftChanged.emit()
        self.stateChanged.emit()
        return True

    @Slot(str, str, str, result=bool)
    def draftFilter(self, field, text, owner):
        if self.allowed(owner) and field in FILTER_FIELDS:
            self._sync_filter_context()
            self._filter_draft[field] = text
            self._filter_preset = 'Custom'
            self._clear_error()
            self.draftChanged.emit()
            self.stateChanged.emit()
            return True
        return False

    @Slot(str, int, str, result=bool)
    def selectSeed(self, seed_id, gate, owner):
        if self._seed_allowed(owner, gate, seed_id):
            self._selection[self.owner_key(), gate] = seed_id
            self.stateChanged.emit()
            return True
        return False

    def _seed_allowed(self, owner, gate, seed_id):
        return (self.allowed(owner) and gate == self.window._current_gate_index() and
                any(seed.seed_id == seed_id for seed in self.window._workbench_document().seeds_by_gate.get(gate, ())))

    @Slot(str, int, str, str, str, result=bool)
    def draftSeed(self, seed_id, gate, field, text, owner):
        if self._seed_allowed(owner, gate, seed_id) and field in SEED_FIELDS:
            if field == 'gamma' and self.window._workbench_document().science.analysis_controls.fit_model != 'Voigt':
                return False
            self._seed_drafts.setdefault(self.owner_key(), {}).setdefault((gate, seed_id), {})[field] = text
            self._clear_error()
            self.draftChanged.emit()
            self.stateChanged.emit()
            return True
        return False

    def validate_editor(self):
        """Build every candidate first; an invalid sibling cannot partially commit."""
        doc = self.window._workbench_document()
        if doc is None:
            return None
        controls = doc.science.analysis_controls
        numbers = {}
        for field, text in self._drafts.get(self.owner_key(), {}).items():
            try:
                value = float(text.strip())
                if not math.isfinite(value) or abs(value) > 1e12:
                    raise ValueError()
                if field == 'd2Window':
                    if value != int(value) or not 3 <= value <= 10001:
                        raise ValueError()
                    value = int(value)
                numbers[NUMBER_FIELDS[field]] = value
            except (ValueError, OverflowError):
                self._error(field, 'Enter a finite number.' if field != 'd2Window' else 'Enter an integer from 3 to 10001.')
                raise ValueError('Correct the pending Analysis numeric editor.') from None
        low = numbers.get('fit_xmin', controls.fit_xmin)
        high = numbers.get('fit_xmax', controls.fit_xmax)
        if high <= low:
            self._error('xmax', 'Analysis interval maximum must exceed minimum.')
            raise ValueError(self.errorText)
        self.filter_settings()
        by_gate = {}
        for (gate, seed_id), fields in self._seed_drafts.get(self.owner_key(), {}).items():
            original = doc.seeds_by_gate.get(gate, ())
            seed = next((seed for seed in original if seed.seed_id == seed_id), None)
            if seed is None:
                continue  # A retired seed cannot be re-created by its former editor.
            rows = by_gate.setdefault(gate, list(original))
            index = next(index for index, item in enumerate(rows) if item.seed_id == seed_id)
            try:
                rows[index:index+1] = self.window._workbench_seed_values(seed, fields)
            except (ValueError, OverflowError) as exc:
                self._error(seed_id+':'+next(iter(fields)), str(exc))
                raise ValueError('Correct the pending Analysis seed editor: '+str(exc)) from None
        return numbers, by_gate

    def finish_editor(self):
        candidate = self.validate_editor()
        if candidate is None:
            return
        numbers, seeds = candidate
        if not self.allowed(busy=True):
            raise ValueError('This Analysis editor is no longer active.')
        # Busy commands cannot accept new edits; there should be no drafts after a job's preflight.
        if self.window._project_busy() and (numbers or seeds):
            raise ValueError('Wait for the current Analysis operation before committing edits.')
        self._drafts.pop(self.owner_key(), None)
        self._seed_drafts.pop(self.owner_key(), None)
        changed = self.window._workbench_apply_settings(numbers, refresh=False)
        doc = self.window._workbench_document()
        for gate, rows in seeds.items():
            changed = changed or rows != doc.seeds_by_gate.get(gate, [])
            doc.set_seeds(gate, rows)
        self._clear_error()
        if changed:
            self.window._workbench_changed()
        else:
            self.refresh()

    @Slot(str, result=bool)
    def commitEditors(self, owner):
        if not self.allowed(owner, busy=True):
            return False
        try:
            self.finish_editor()
            return True
        except ValueError:
            return False

    @Slot(str, str, str, result=bool)
    def setChoice(self, field, value, owner):
        choices = {'scope': ('current', 'all'), 'mode': ('Both', 'Peaks', 'Dips'),
                   'detector': ('Original', 'D2'), 'model': ('Lorentzian', 'Gaussian', 'Voigt'),
                   'inputMode': ('raw', 'processed'), 'filterPreset': ('Loose', 'Balanced', 'Strict', 'Custom'),
                   'addKind': ('peak', 'dip')}
        if not self.allowed(owner) or value not in choices.get(field, ()):
            return False
        if field == 'addKind':
            self._add.setdefault(self.owner_key(), {})['kind'] = value
            self.refresh()
            return True
        if not self.commitEditors(owner):
            return False
        if field == 'inputMode':
            self._inputs[self.owner_key()] = value
        elif field == 'filterPreset':
            self._filter_preset = value
            if value != 'Custom':
                settings = candidate_filter_preset(value)
                self._filter_draft = {name: _text(getattr(settings, name)) for name in FILTER_FIELDS}
        else:
            mapping = {'scope': 'gate_scope', 'mode': 'peak_mode', 'detector': 'derivative_detection', 'model': 'fit_model'}
            self.window._workbench_apply_settings({mapping[field]: value == 'D2' if field == 'detector' else value})
        self.refresh()
        return True

    @Slot(str, bool, str, result=bool)
    def setOverlay(self, layer, visible, owner):
        if self.allowed(owner) and layer in ('detected', 'fitted') and self.commitEditors(owner):
            doc = self.window._workbench_document()
            for name, style in list(doc.overlay_styles.items()):
                if name.startswith(layer):
                    doc.overlay_styles[name] = replace(style, visible=visible)
            self.window._workbench_changed()
            return True
        return False

    @Slot(str, str, result=bool)
    def command(self, action, owner):
        if action == 'stop':
            if self.allowed(owner, busy=True) and self.window._background_job_owner == 'analysis':
                self.window._cancel_background_job()
                return True
            return False
        if not self.allowed(owner):
            return False
        if action in ('seeds', 'filterSettings'):
            self.open_popup(action)
            return True
        if not self.commitEditors(owner):
            return False
        w = self.window
        callbacks = {'find': w._workbench_find, 'filter': w._workbench_filter, 'fit': w._workbench_fit,
                     'style': w._workbench_overlay_style, 'linecut': w._workbench_create_linecut_graph,
                     'remove': w._workbench_remove_seed, 'rebind': w._workbench_rebind_input,
                     'source': w._workbench_source_graph, 'original': w._workbench_original_data,
                     'results': w._workbench_results_table, 'duplicate': w._workbench_duplicate,
                     'exportSvg': lambda: w._on_export_plot_page('svg'),
                     'exportPng': lambda: w._on_export_plot_page('png'), 'copySvg': lambda: w._on_copy_graph_svg()}
        if action == 'source' and not self.sourceAvailable:
            return False
        if action in callbacks:
            callbacks[action]()
        elif action in ('seeds', 'filterSettings', 'add'):
            self.open_popup(action)
        else:
            return False
        return True

    @Slot(bool, str, result=bool)
    def setAuxLinecut(self, visible, owner):
        if not self.allowed(owner) or not self.commitEditors(owner):
            return False
        self.window._workbench_toggle_linecut(visible)
        return True

    def open_popup(self, kind):
        from .quick_dialog import QuickDialog
        if not self.allowed() or (kind not in ('seeds', 'filterSettings') and not self.commitEditors(self.ownerToken)):
            return None
        existing = self._dialogs.get(kind)
        if existing is not None and not existing._finished and existing.bridge.allowed(busy=True):
            existing.show()
            return existing
        if existing is not None:
            existing.reject()
            existing.deleteLater()
        names = {'seeds': ('AnalysisSeedEditor.qml', 'Edit accepted seeds — current Gate', (1050, 500)),
                 'filterSettings': ('AnalysisFilterEditor.qml', 'Candidate filter settings', (540, 560)),
                 'add': ('AnalysisAddSeed.qml', 'Add analysis seed', (440, 380)),
                 'rebind': ('AnalysisRebind.qml', 'Replace analysis input', (560, 400)),
                 'results': ('AnalysisResults.qml', 'Analysis results', (1100, 620)),
                 'style': ('AnalysisStyle.qml', 'Analysis scatter styles', (700, 470))}
        filename, title, size = names[kind]
        if kind == 'add':
            self._add.pop(self.owner_key(), None)
        if kind in ('results', 'style'):
            from .analysis_results_bridge import AnalysisResultsBridge, AnalysisStyleBridge
            popup = (AnalysisResultsBridge if kind == 'results' else AnalysisStyleBridge)(self)
        else:
            popup = AnalysisPopupBridge(self, kind)
        host = QuickDialog(popup, filename, self.window, title=title, size=size)
        self._dialogs[kind] = host
        alias = {'seeds': 'workbench_seed_dialog', 'filterSettings': 'workbench_filter_dialog',
                 'results': 'workbench_results_dialog', 'style': 'workbench_style_dialog'}.get(kind)
        if alias:
            setattr(self.window, alias, host)
        if kind in ('seeds', 'filterSettings'):
            host.window().setModality(Qt.NonModal)
        host.show()
        return host

    @Slot(str, result=bool)
    def acceptAdd(self, owner):
        if not self.allowed(owner) or not self.commitEditors(owner):
            return False
        doc = self.window._workbench_document()
        controls = doc.science.analysis_controls
        draft = self._add.get(self.owner_key(), {})
        try:
            position = float(draft.get('position', (controls.fit_xmin+controls.fit_xmax)/2))
            if not math.isfinite(position) or not controls.fit_xmin <= position <= controls.fit_xmax:
                raise ValueError('Enter a position inside the Analysis interval.')
            self.window._workbench_add_seed(position=position, kind=draft.get('kind', 'peak'))
            self._add.pop(self.owner_key(), None)
            return True
        except ValueError as exc:
            self._error('addPosition', str(exc))
            return False

    @Slot(str, str, result=bool)
    def chooseRebind(self, choice, owner):
        return self.allowed(owner) and choice in ('replace', 'duplicate', 'cancel')

    def close_popups(self):
        for host in tuple(self._dialogs.values()):
            if not host._finished:
                host.reject()

    def shutdown(self):
        self._closed = True
        self.stateChanged.emit()
        self.close_popups()


class AnalysisPopupBridge(QObject):
    """A popup captures ownership while drafts remain with the shared bridge."""
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)
    revealRequested = Signal(str, str)

    def __init__(self, shared, kind):
        super().__init__()
        self.shared, self.kind = shared, kind
        self._owner = shared.ownerToken
        self.gate = shared.window._current_gate_index()
        self._filter_scope = tuple(shared.window._workbench_gates())
        self._closed = False
        self.choice = 'cancel'
        shared.stateChanged.connect(self.stateChanged)
        shared.draftChanged.connect(self.draftChanged)
        shared.focusRequested.connect(self.focusRequested)
        shared.revealRequested.connect(self.revealRequested)

    def allowed(self, owner=None, *, busy=False):
        return (not self._closed and (owner is None or owner == self._owner) and
                self.shared.allowed(self._owner, busy=busy) and
                (self.kind != 'filterSettings' or self._filter_scope == tuple(self.shared.window._workbench_gates())) and
                (self.kind not in ('seeds', 'add') or self.gate == self.shared.window._current_gate_index()))

    ownerToken = Property(str, lambda self: self._owner, constant=True)
    state = Property('QVariantMap', lambda self: self.shared.state if self.allowed(busy=True) else {}, notify=stateChanged)
    enabled = Property(bool, lambda self: self.allowed(), notify=stateChanged)
    canAccept = Property(bool, lambda self: self.allowed(), notify=stateChanged)
    errorText = Property(str, lambda self: self.shared.errorText, notify=stateChanged)
    errorField = Property(str, lambda self: self.shared.errorField, notify=stateChanged)

    @Slot(str, str, str)
    def draftNumber(self, field, text, owner):
        if self.allowed(owner):
            self.shared.draftNumber(field, text, owner)

    @Slot(str, str, str)
    def draftFilter(self, field, text, owner):
        if self.allowed(owner):
            self.shared.draftFilter(field, text, owner)

    @Slot(str, str, str)
    def setChoice(self, field, value, owner):
        if self.allowed(owner):
            self.shared.setChoice(field, value, owner)

    @Slot(str, int, str, str, str)
    def draftSeed(self, seed_id, gate, field, text, owner):
        if self.allowed(owner) and gate == self.gate:
            self.shared.draftSeed(seed_id, gate, field, text, owner)

    @Slot(str, int, str)
    def selectSeed(self, seed_id, gate, owner):
        if self.allowed(owner) and gate == self.gate:
            self.shared.selectSeed(seed_id, gate, owner)

    @Slot(str, result=bool)
    def commitEditors(self, owner):
        return self.allowed(owner) and self.shared.commitEditors(owner)

    @Slot(str, str)
    def command(self, action, owner):
        if self.allowed(owner):
            self.shared.command(action, owner)

    @Slot(str, result=bool)
    def acceptAdd(self, owner):
        return self.allowed(owner) and self.shared.acceptAdd(owner)

    @Slot(str, str, result=bool)
    def chooseRebind(self, choice, owner):
        if self.allowed(owner) and self.shared.chooseRebind(choice, owner):
            self.choice = choice
            return True
        return False

    def shutdown(self):
        self._closed = True
