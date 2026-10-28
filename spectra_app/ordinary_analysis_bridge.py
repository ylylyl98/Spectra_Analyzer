"""Typed, owner-scoped ordinary DAT Analysis editors for the Quick sidebar.

Only unfinished numeric text and presentation selections live here. Scientific
values, seeds, results and summary always belong to analysis_owner(session).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from copy import deepcopy
import math
from PySide6.QtCore import QObject, Property, QLocale, QTimer, Signal, Slot
from PySide6.QtGui import QDoubleValidator, QIntValidator, QValidator
from .analysis_document import analysis_owner


def _number(name, label, tab, low, high, decimals=0, step=1, help=''):
    return dict(name=name, label=label, kind='number', tab=tab, minimum=low,
                maximum=high, decimals=decimals, step=step, choices=[], help=help)


def _choice(name, label, tab, choices):
    return dict(name=name, label=label, kind='choice', tab=tab, choices=choices,
                minimum=0, maximum=0, decimals=0, step=1, help='')


def _boolean(name, label, tab):
    return dict(name=name, label=label, kind='bool', tab=tab, choices=[],
                minimum=0, maximum=1, decimals=0, step=1, help='')


FIELD_METADATA = (
    _boolean('derivative_detection', 'Second derivative detection', 0),
    _choice('peak_mode', 'Type', 0, ['Peaks', 'Dips', 'Both']),
    _number('prominence', 'Prominence', 0, 0, 1, 4, .005),
    _number('distance', 'Minimum distance (pt)', 0, 1, 1000),
    _number('max_peaks', 'Maximum peaks', 0, 1, 30),
    _number('max_dips', 'Maximum dips', 0, 1, 30),
    _boolean('peak_smooth', 'Pre-smooth detection', 0),
    _number('peak_smooth_window', 'Smoothing window (pt)', 0, 3, 501, step=2),
    _choice('d2_mode', 'Derivative type', 0, ['Peaks', 'Dips', 'Both']),
    _number('d2_window', 'SG window (pt)', 0, 3, 501, step=2),
    _number('d2_polyorder', 'SG polynomial order', 0, 1, 6),
    _number('d2_prominence', 'Derivative prominence', 0, 0, 1, 4, .005),
    _number('d2_distance', 'Derivative distance (pt)', 0, 1, 1000),
    _number('d2_separation', 'Minimum separation', 0, 0, 100, 5, .001),
    _number('d2_max_peaks', 'Maximum derivative peaks', 0, 1, 30),
    _number('d2_max_dips', 'Maximum derivative dips', 0, 1, 30),
    _number('d2_target', 'Target count (0 = automatic)', 0, 0, 30),
    _number('fit_xmin', 'Fit minimum', 1, -1e12, 1e12, 6, .01),
    _number('fit_xmax', 'Fit maximum', 1, -1e12, 1e12, 6, .01),
    _boolean('fit_xmin_fixed', 'Fix fit minimum', 1),
    _boolean('fit_xmax_fixed', 'Fix fit maximum', 1),
    _boolean('fit_smooth', 'Pre-smooth fit', 1),
    _number('fit_smooth_window', 'Fit smoothing window (pt)', 1, 3, 501, step=2),
    _choice('fit_model', 'Line shape', 1, ['Lorentzian', 'Gaussian', 'Voigt']),
    _number('linewidth_min', 'Minimum linewidth (pt)', 1, 0, 10000),
    _number('linewidth_max', 'Maximum linewidth (pt)', 1, 0, 10000,
            help='0 = automatic upper bound, about twice the fitted X span.'),
    _number('peak_move_limit', 'Maximum peak shift (pt)', 1, 0, 10000,
            help='0 = unconstrained. Points are converted to the analysis axis.'),
    _number('maxfev', 'Maximum evaluations', 1, 100, 2000000, step=5000),
    _boolean('csv_source', 'Use CSV seeds', 1),
)
_FIELD_HELP = {
    'derivative_detection': 'Choose original signal extrema or extrema of the second derivative.',
    'peak_mode': 'Peaks are maxima; dips are minima. Both detects each independently.',
    'prominence': 'Minimum prominence as a fraction of the signal span. Increase to reject weak features.',
    'distance': 'Minimum distance between features, in data points.',
    'max_peaks': 'Maximum retained peaks, or dips when Dips is selected.',
    'max_dips': 'Maximum retained dips when Both is selected.',
    'peak_smooth': 'Apply Savitzky-Golay smoothing before original-signal detection.',
    'peak_smooth_window': 'Odd Savitzky-Golay window; clamped to the available trace length.',
    'd2_mode': 'Detect peaks, dips, or both using the second derivative.',
    'd2_window': 'Odd Savitzky-Golay window for the second derivative; clamped to the trace length.',
    'd2_polyorder': 'Polynomial order must be below the requested SG window.',
    'd2_prominence': 'Prominence fraction of the second derivative span; increase to reject weak features.',
    'd2_distance': 'Minimum derivative feature distance in data points.',
    'd2_separation': 'Minimum feature separation in the displayed analysis-axis units.',
    'd2_max_peaks': 'Maximum retained derivative peaks, or dips when Dips is selected.',
    'd2_max_dips': 'Maximum retained derivative dips when Both is selected.',
    'd2_target': '0 selects the automatic count; otherwise retain the requested strongest features.',
    'fit_xmin': 'Independent fit lower endpoint. Detection uses the visible X range.',
    'fit_xmax': 'Independent fit upper endpoint. Reversed endpoints are ordered for fitting.',
    'fit_xmin_fixed': 'Full Range keeps this endpoint. Use Current X Range copies both endpoints.',
    'fit_xmax_fixed': 'Full Range keeps this endpoint. Use Current X Range copies both endpoints.',
    'fit_smooth': 'Apply Savitzky-Golay smoothing to the trace before fitting.',
    'fit_smooth_window': 'Odd fit smoothing window; clamped to the available trace length.',
    'fit_model': 'Lorentzian has broader tails; Gaussian describes Gaussian broadening; Voigt combines both.',
    'linewidth_min': 'Minimum allowed linewidth parameter in data points, converted to analysis-axis units.',
    'linewidth_max': 'Maximum linewidth in data points; 0 uses an automatic bound about twice the fitted X span.',
    'peak_move_limit': 'Maximum fitted center shift from its seed in data points; 0 is unconstrained.',
    'maxfev': 'Maximum optimizer evaluations. Increase if fitting fails to converge.',
    'csv_source': 'CSV columns are Gate and peak position; seeds at the nearest Gate are used without detection fallback.',
}
for _item in FIELD_METADATA:
    _item['help'] = _FIELD_HELP[_item['name']]
FIELD_BY_NAME = {item['name']: item for item in FIELD_METADATA}


@dataclass(frozen=True)
class OrdinaryCandidate:
    owner: str
    drafts: tuple
    controls: object | None


class OrdinaryAnalysisBridge(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._closed = False
        self._drafts = {}
        self._errors = {}
        self._peak_numbers = {}
        self._selected_tab = 0
        self._ui = {}

    def owner(self):
        return analysis_owner(self.window.workspace.active or self.window._unregistered_session)

    def owner_key(self):
        w = self.window
        session = w.workspace.active or w._unregistered_session
        graph = session.active_graph
        return (id(w.workspace), session.dataset_id, graph.graph_id, id(graph),
                id(w.current_cube), w._data_generation)

    @Property(str, notify=stateChanged)
    def ownerToken(self):
        return '|'.join(map(str, self.owner_key()))

    def allowed(self, owner=None):
        from shiboken6 import isValid
        w = self.window
        return (not self._closed and isValid(w) and not w._close_pending
                and not w._dat_import_pending
                and w._workbench_document() is None
                and not (hasattr(w, 'graph_documents') and w._auxiliary_active())
                and (owner is None or owner == self.ownerToken))

    enabled = Property(bool, lambda self: self.allowed(), notify=stateChanged)
    errorText = Property(str, lambda self: self._errors.get(self.owner_key(), ('', ''))[1], notify=stateChanged)
    errorField = Property(str, lambda self: self._errors.get(self.owner_key(), ('', ''))[0], notify=stateChanged)
    fields = Property('QVariantList', lambda self: list(FIELD_METADATA), constant=True)
    selectedTab = Property(int, lambda self: self._selected_tab, notify=stateChanged)

    def _locale(self):
        locale = QLocale()
        locale.setNumberOptions(locale.numberOptions() | QLocale.OmitGroupSeparator)
        return locale

    def format(self, name, value):
        item = FIELD_BY_NAME[name]
        return (self._locale().toString(float(value), 'f', item['decimals'])
                if item['decimals'] else self._locale().toString(int(value)))

    def normalized_number(self, name, value):
        """Native numeric projection without constructing an editor.

        Qt's locale fixed-point formatter preserves the former spin box's
        rounding, including half cases; programmatic bounds clamp first.
        """
        item = FIELD_BY_NAME[name]
        value = max(item['minimum'], min(item['maximum'], float(value)))
        if item['decimals']:
            text = self._locale().toString(value, 'f', item['decimals'])
            return self._locale().toDouble(text)[0]
        return int(value)

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        owner = self.owner()
        values = asdict(owner.analysis_controls)
        drafts = self._drafts.get(self.owner_key(), {})
        texts = {name: drafts.get(name, self.format(name, values[name]))
                 for name, item in FIELD_BY_NAME.items() if item['kind'] == 'number'}
        count = int(self.window._current_trace_peak_indices().size) if self.window.current_cube is not None else 0
        number = min(max(1, self._peak_numbers.get(self.owner_key(), 1)), max(1, count))
        return dict(values=values, texts=texts, ui=dict(self._ui), summary=owner.summary,
                    peakNumber=number, peakCount=count, lassoActive=self.window._lasso_mode,
                    circleActive=getattr(self.window, '_circle_delete_mode', False),
                    csvPath=owner.csv_seeds_path or '', selectedTab=self._selected_tab)

    def refresh(self, **ui):
        self._ui.update(ui)
        self.stateChanged.emit()

    def retire_owner(self, dataset_id=None, graph_id=None):
        for cache in (self._drafts, self._errors, self._peak_numbers):
            for key in tuple(cache):
                if dataset_id is None or (key[1] == dataset_id and (graph_id is None or key[2] == graph_id)):
                    cache.pop(key, None)
        self.refresh()

    def snapshot_editor_state(self):
        return deepcopy((self.owner_key(), self._drafts, self._errors,
                         self._peak_numbers, self._selected_tab, self._ui))

    def restore_editor_state(self, state):
        (owner, self._drafts, self._errors, self._peak_numbers,
         self._selected_tab, self._ui) = deepcopy(state)
        current = self.owner_key()
        if owner[:-1] == current[:-1]:
            for cache in (self._drafts, self._errors, self._peak_numbers):
                if owner in cache:
                    cache[current] = cache.pop(owner)
        self.stateChanged.emit()
        self.draftChanged.emit()

    def close(self):
        self._closed = True
        self.retire_owner()

    def _error(self, field, message):
        self._errors[self.owner_key()] = field, message
        if field in FIELD_BY_NAME:
            self._selected_tab = FIELD_BY_NAME[field]['tab']
        self.stateChanged.emit()
        owner = self.ownerToken
        self.window._set_dat_details_requested(True)
        self.window.request_task(1)

        def focus_owner():
            if self.allowed(owner):
                self.focusRequested.emit(field, owner)

        QTimer.singleShot(0, self, focus_owner)
        raise ValueError(message)

    def parse(self, name, text):
        item = FIELD_BY_NAME[name]
        if item['kind'] != 'number':
            raise ValueError('This field is not numeric.')
        text = text.strip()
        locale = QLocale()
        low, high, decimals = item['minimum'], item['maximum'], item['decimals']
        group = locale.groupSeparator()
        if group and (text.startswith(group) or group * 2 in text):
            raise ValueError(f"Enter a valid {item['label']}.")
        if decimals:
            decimal = locale.decimalPoint()
            point = text.find(decimal)
            if point >= 0 and (len(text) - point - 1 > decimals or group in text[point + 1:]):
                raise ValueError(f"{item['label']} accepts up to {decimals} decimal places.")
            validator = QDoubleValidator(low, high, decimals)
            validator.setNotation(QDoubleValidator.ScientificNotation)
            value, okay = locale.toDouble(text)
        else:
            validator = QIntValidator(low, high)
            value, okay = locale.toInt(text)
        validator.setLocale(locale)
        normalized = text
        if not okay and group and (low <= -1000 or high >= 1000):
            normalized = text.replace(group, '')
            value, okay = locale.toDouble(normalized) if decimals else locale.toInt(normalized)
        if (not okay or not math.isfinite(value) or not low <= value <= high
                or validator.validate(normalized, 0)[0] != QValidator.Acceptable):
            raise ValueError(f"{item['label']} must be from {low:g} to {high:g}.")
        return float(value) if decimals else int(value)

    def validate_editor(self):
        drafts = self._drafts.get(self.owner_key(), {})
        if not drafts:
            return OrdinaryCandidate(self.ownerToken, (), replace(self.owner().analysis_controls))
        if not self.allowed():
            self._error(next(iter(drafts)), 'This Analysis editor is no longer active.')
        values = {}
        for name, text in drafts.items():
            try:
                values[name] = self.parse(name, text)
            except ValueError as exc:
                self._error(name, 'Invalid pending numeric editor: ' + str(exc))
        return OrdinaryCandidate(self.ownerToken, tuple(drafts.items()), replace(self.owner().analysis_controls, **values))

    def commit_candidate(self, candidate, controls=None):
        if not candidate.drafts:
            return
        if not self.allowed(candidate.owner):
            raise ValueError('This Analysis editor is no longer active.')
        key = self.owner_key()
        if candidate.drafts != tuple(self._drafts.get(key, {}).items()):
            raise ValueError('The Analysis editor changed after validation.')
        self._drafts.pop(key, None)
        self._errors.pop(key, None)
        self._install(candidate.controls if controls is None else controls)

    def _install(self, controls):
        previous = self.owner().analysis_controls
        if controls != previous:
            redraw = (controls.fit_xmin, controls.fit_xmax) != (previous.fit_xmin, previous.fit_xmax)
            self.owner().analysis_controls = controls
            w = self.window
            w._invalidate_analysis_jobs()
            if controls.derivative_detection != previous.derivative_detection:
                w._cached_d2 = None
            w._schedule_project_modified()
            w._update_fit_param_unit_labels()
            w._refresh_fit_range_summary()
            if w.current_cube is None:
                w._refresh_analysis_controls()
            if redraw and w._spec_ax is not None:
                w._update_spectrum_only()
        self.refresh()

    def finish_editor(self):
        self.commit_candidate(self.validate_editor())

    def has_project_drafts(self):
        for name, text in self._drafts.get(self.owner_key(), {}).items():
            try:
                if self.parse(name, text) != getattr(self.owner().analysis_controls, name):
                    return True
            except ValueError:
                return True
        return False

    @Slot(str, str, str, result=bool)
    def setDraft(self, field, text, owner):
        if field not in FIELD_BY_NAME or FIELD_BY_NAME[field]['kind'] != 'number' or not self.allowed(owner):
            return False
        self._drafts.setdefault(self.owner_key(), {})[field] = text
        self._errors.pop(self.owner_key(), None)
        self.draftChanged.emit()
        self.refresh()
        return True

    @Slot(str, result=bool)
    def commit(self, owner):
        if not self.allowed(owner):
            return False
        try:
            self.finish_editor()
            return True
        except ValueError as exc:
            self.window.publish_status(str(exc))
            return False

    @Slot(str, 'QVariant', str, result=bool)
    def setValue(self, field, value, owner):
        if not self.allowed(owner) or field not in FIELD_BY_NAME:
            return False
        item = FIELD_BY_NAME[field]
        if item['kind'] == 'number':
            return self.setDraft(field, self.format(field, value), owner) and self.commit(owner)
        if item['kind'] == 'bool':
            if not isinstance(value, bool):
                return False
        elif value not in item['choices']:
            return False
        try:
            candidate = self.validate_editor()
            controls = replace(candidate.controls, **{field: value})
            if candidate.drafts:
                self.commit_candidate(candidate, controls)
            else:
                self._install(controls)
            return True
        except ValueError:
            return False

    @Slot(str, int, str, result=bool)
    def step(self, field, direction, owner):
        if not self.allowed(owner) or field not in FIELD_BY_NAME or FIELD_BY_NAME[field]['kind'] != 'number':
            return False
        try:
            candidate = self.validate_editor()
            item = FIELD_BY_NAME[field]
            value = self.normalized_number(field, getattr(candidate.controls, field) + item['step'] * direction)
            controls = replace(candidate.controls, **{field: value})
            if candidate.drafts:
                self.commit_candidate(candidate, controls)
            else:
                self._install(controls)
            return True
        except ValueError:
            return False

    @Slot(int)
    def selectTab(self, tab):
        if tab in (0, 1):
            self._selected_tab = tab
            self.refresh()

    @Slot(int, str, result=bool)
    def setPeakNumber(self, number, owner):
        if not self.allowed(owner):
            return False
        count = int(self.window._current_trace_peak_indices().size)
        self._peak_numbers[self.owner_key()] = max(1, min(max(1, count), number))
        self.refresh()
        self.window._update_spectrum_only()
        return True

    @Slot(str, str, result=bool)
    def trigger(self, command, owner):
        if not self.allowed(owner):
            return False
        w = self.window
        commands = dict(find=w._on_find_peaks, batchFind=w._on_batch_find_peaks_all_gates,
                        fit=w._on_fit_or_detect_current_gate, batchFit=w._on_batch_fit_all_gates,
                        fullRange=w._on_set_fit_full_range, currentRange=w._on_use_current_x_range,
                        loadCsv=w._on_load_seeds_csv, clearFit=w._on_clear_fit,
                        exportCurrentFit=w._on_export_current_fit_csv, exportAllFit=w._on_export_all_fit_csv,
                        exportSpectrum=w._on_export_spectrum_csv, savePeaks=w._on_save_peaks_csv,
                        deleteLasso=w._on_delete_lasso_selection, clearLasso=w._on_clear_lasso_selection,
                        clearBatch=w._on_clear_batch_peaks, deletePeak=w._on_delete_peak_by_number,
                        clearPeaks=w._on_clear_peaks, createAnalysis=w._on_create_analysis_graph,
                        lasso=lambda: w._on_lasso_mode_toggled(not w._lasso_mode),
                        circle=lambda: w._on_toggle_circle_delete_mode(not w._circle_delete_mode))
        if command not in commands:
            return False
        try:
            candidate = self.validate_editor()
            gate = w.analysis_header.validate_editor()
            shared = w.dat_properties.validate_editor()
            self.commit_candidate(candidate)
            w.analysis_header.commit_candidate(gate)
            w.dat_properties.commit_candidate(shared)
            if not self.allowed(owner):
                return False
            if command + 'Enabled' in self._ui and not self._ui[command + 'Enabled']:
                return False
            commands[command]()
            self.refresh()
            return True
        except ValueError as exc:
            w.publish_status(str(exc))
            return False
