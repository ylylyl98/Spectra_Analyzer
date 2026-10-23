"""Contextual XY analysis in the graph inspector, with owner-checked delivery."""
from __future__ import annotations

import numpy as np
from dataclasses import replace
from PySide6.QtCore import Qt, QSignalBlocker, QEvent
from PySide6.QtWidgets import (QGroupBox, QFormLayout, QLabel, QComboBox, QDoubleSpinBox,
                             QSpinBox, QPushButton, QHBoxLayout, QSizePolicy, QDialog,
                             QVBoxLayout, QDialogButtonBox, QTableWidget, QTableWidgetItem,
                             QTabWidget, QAbstractItemView, QApplication)

from .xy_analysis import (AnalysisSettings, capture_input, collect_candidates as detect_peaks,
                          fit_seeds, result_workbook, XYAnalysisProgress, input_fingerprint)
from .analysis_document import AnalysisSeed
from .peak_candidates import CandidateFilter, candidate_filter_preset
from .comparison_display import transform_y
from .analysis_graph import saved_result_records


class XYAnalysisPanel(QGroupBox):
    def __init__(self, page):
        super().__init__('Curve analysis')
        self.page = page
        self.host = None
        self.captured = self.peaks = self.fit_result = None
        self._key = self._request = None
        self._revision = 0
        self._result_ids = self._graph_id = None
        self._disposed = False
        self._activated = False
        self._loading = self._restore_attempted = False
        self.progress = None
        self._context_valid = False
        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.context_label = QLabel()
        self.context_label.setWordWrap(True)
        self.context_label.setTextFormat(Qt.PlainText)
        self.context_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        form.addRow(self.context_label)
        self.resume_btn = QPushButton('Resume saved analysis')
        self.start_selected_btn = QPushButton('Start analysis for selected curve')
        for button in (self.resume_btn, self.start_selected_btn):
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        form.addRow(self.resume_btn)
        form.addRow(self.start_selected_btn)
        self.resume_btn.clicked.connect(self.resume_analysis)
        self.start_selected_btn.clicked.connect(self.analyze_selected)
        self.edit_interval_btn = QPushButton('Edit X interval…')
        self.edit_interval_btn.setToolTip('Edit the data X interval in the Display tab; toolbar zoom only changes the view.')
        self.edit_interval_btn.setFocusPolicy(Qt.StrongFocus)
        self.edit_interval_btn.clicked.connect(self.page.edit_x_interval)
        form.addRow(self.edit_interval_btn)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(['Peaks', 'Dips', 'Both'])
        form.addRow('Detect', self.mode_combo)
        self.detector_combo = QComboBox()
        self.detector_combo.addItems(['Original', 'D2'])
        form.addRow('Candidate method', self.detector_combo)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(['Loose', 'Balanced', 'Strict', 'Custom'])
        self.preset_combo.setCurrentText('Balanced')
        form.addRow('Candidate filter', self.preset_combo)
        self.details_dialog = QDialog(self)
        self.details_dialog.setWindowTitle('Candidate filter and fit settings')
        details = QFormLayout(self.details_dialog)
        details.addRow(QLabel('Candidate widths and spacing use physical source X units.\nZero maximum count means unlimited.'))
        self.prominence_spin = QDoubleSpinBox()
        self.prominence_spin.setRange(0, 1)
        self.prominence_spin.setDecimals(3)
        self.prominence_spin.setSingleStep(.01)
        self.prominence_spin.setValue(.03)
        self.prominence_spin.setToolTip('Fraction of the full selected source Y range; shared across missing-data spans.')
        details.addRow('Prominence fraction', self.prominence_spin)
        self.noise_spin = QDoubleSpinBox()
        self.noise_spin.setRange(0, 1e9)
        self.noise_spin.setValue(2)
        details.addRow('Minimum noise strength', self.noise_spin)
        self.distance_spin = QDoubleSpinBox()
        self.distance_spin.setDecimals(6)
        self.distance_spin.setRange(0, 1e12)
        details.addRow('Minimum spacing (X)', self.distance_spin)
        self.candidate_width_min_spin, self.candidate_width_max_spin = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self.candidate_width_min_spin, self.candidate_width_max_spin):
            spin.setDecimals(6); spin.setRange(0, 1e12)
        self.candidate_width_max_spin.setSpecialValueText('Unlimited')
        details.addRow('Minimum candidate width', self.candidate_width_min_spin)
        details.addRow('Maximum candidate width', self.candidate_width_max_spin)
        self.max_peaks_spin = QSpinBox()
        self.max_peaks_spin.setRange(0, 10000)
        self.max_peaks_spin.setSpecialValueText('Unlimited')
        self.max_peaks_spin.setToolTip('Optional accepted count cap applied after all other candidate filters.')
        details.addRow('Maximum count', self.max_peaks_spin)
        self.sg_window_spin, self.sg_order_spin = QSpinBox(), QSpinBox()
        self.sg_window_spin.setRange(3, 10001); self.sg_window_spin.setSingleStep(2); self.sg_window_spin.setValue(11)
        self.sg_order_spin.setRange(2, 10); self.sg_order_spin.setValue(3)
        details.addRow('D2 smoothing window', self.sg_window_spin)
        details.addRow('D2 polynomial order', self.sg_order_spin)
        self.model_combo = QComboBox()
        self.model_combo.addItems(['Gaussian', 'Lorentzian', 'Voigt'])
        form.addRow('Fit model + baseline', self.model_combo)
        self.width_label = QLabel()
        details.addRow(self.width_label)
        self.width_min_spin, self.width_max_spin = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self.width_min_spin, self.width_max_spin):
            spin.setDecimals(9)
            spin.setRange(0, 1e12)
        self.width_min_spin.setMinimum(1e-9)
        self.width_min_spin.setValue(1e-9)
        self.width_max_spin.setSpecialValueText('Auto')
        details.addRow('Minimum fitted width', self.width_min_spin)
        details.addRow('Maximum fitted width', self.width_max_spin)
        self.settings_error = QLabel()
        self.settings_error.setWordWrap(True)
        self.settings_error.setAccessibleName('Analysis settings validation')
        details.addRow(self.settings_error)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.details_dialog.hide)
        details.addRow(close)
        self.details_btn = QPushButton('Filter and fit settings…')
        form.addRow(self.details_btn)
        self.details_btn.clicked.connect(self.details_dialog.show)
        self.seeds_btn = QPushButton('Seeds and fit results…')
        form.addRow(self.seeds_btn)
        self.seeds_btn.clicked.connect(self.edit_seeds)
        buttons = QHBoxLayout()
        self.find_btn, self.fit_btn = QPushButton('Find candidates'), QPushButton('Fit seeds')
        buttons.addWidget(self.find_btn)
        buttons.addWidget(self.fit_btn)
        form.addRow(buttons)
        self.save_btn = QPushButton('Save results to Workbook')
        form.addRow(self.save_btn)
        self.create_graph_btn = QPushButton('Create analysis graph')
        self.create_graph_btn.setToolTip('Create an editable graph with detached source and detected peaks/dips, or completed fit curves.')
        form.addRow(self.create_graph_btn)
        self.status = QLabel('Find candidates, adjust filters or seeds, then optionally fit.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        form.addRow(self.status)
        self.export_note = QLabel('Progress is saved with the Project. Crosses: accepted seeds. Dashed red: fit. Create analysis graph makes an editable, exportable result.')
        self.export_note.setWordWrap(True)
        self.export_note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        form.addRow(self.export_note)
        for label, field in ((details.labelForField(self.prominence_spin), self.prominence_spin),
                             (details.labelForField(self.distance_spin), self.distance_spin),
                             (details.labelForField(self.max_peaks_spin), self.max_peaks_spin),
                             (details.labelForField(self.width_min_spin), self.width_min_spin),
                             (details.labelForField(self.width_max_spin), self.width_max_spin)):
            field.setAccessibleName('Curve analysis ' + label.text())
        for combo in (self.mode_combo, self.model_combo, self.detector_combo):
            combo.currentTextChanged.connect(self.sync)
        for spin in (self.width_min_spin, self.width_max_spin, self.sg_window_spin, self.sg_order_spin):
            spin.valueChanged.connect(self.sync)
        self.preset_combo.currentTextChanged.connect(self._preset_changed)
        for spin in self._filter_spins():
            spin.valueChanged.connect(self._filter_changed)
        for spin in self._numeric_spins():
            spin.setKeyboardTracking(False)
            spin.setAccessibleName('Curve analysis ' + details.labelForField(spin).text())
            spin.lineEdit().textChanged.connect(self.sync)
            spin.installEventFilter(self)
            spin.lineEdit().installEventFilter(self)
        self.find_btn.clicked.connect(lambda: self.start('find'))
        self.fit_btn.clicked.connect(lambda: self.start('fit'))
        self.save_btn.clicked.connect(self.save_results)
        self.create_graph_btn.clicked.connect(self.create_analysis_graph)
        self.hide()

    @property
    def busy(self):
        return self._request is not None

    def _owns_selection(self):
        return self.progress is None or self.page._selected_ids() == {self.progress.captured.curve_id}

    def _numeric_spins(self):
        return (*self._filter_spins(), self.width_min_spin, self.width_max_spin,
                self.sg_window_spin, self.sg_order_spin)

    def eventFilter(self, watched, event):
        # Qt otherwise corrects intermediate spin text on blur/hide. Navigation
        # preserves the draft; Return, a normal editor blur, or an operation commits.
        if event.type() in (QEvent.FocusOut, QEvent.Hide, QEvent.Show):
            spin = next((s for s in self._numeric_spins() if watched in (s, s.lineEdit())), None)
            if spin is not None:
                navigation = (QApplication.focusWidget() in
                    (self.page.inspector_tabs.tabBar(), self.page.inspector_toggle_btn, self.edit_interval_btn)
                    or not watched.isVisible())
                if not spin.hasAcceptableInput() or navigation:
                    return True
        return super().eventFilter(watched, event)

    def _load_settings(self):
        settings = self.progress.captured.settings
        self.mode_combo.setCurrentText(settings.mode)
        self.detector_combo.setCurrentText(settings.detector)
        self.model_combo.setCurrentText(settings.model)
        self.width_min_spin.setValue(settings.width_min)
        self.width_max_spin.setValue(settings.width_max)
        self.sg_window_spin.setValue(settings.sg_window)
        self.sg_order_spin.setValue(settings.sg_polyorder)
        self._load_filter(self.progress.filters)

    def analyze_selected(self):
        """Explicitly open/start analysis for the selected curve, without selecting a saved source."""
        try:
            self.page.finish_editor()
            self.finish_settings()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.activate(restore_selection=False)
        try:
            current = self.current_input()
            if self.progress is None or self.progress.captured.curve_id != current.curve_id:
                self._restore_attempted = True
                self._revision += 1
                self._request = None
                self.progress = XYAnalysisProgress(current, filters=self.filter_settings())
                self._result_ids = self._graph_id = None
                self._publish_progress()
                self._persist()
                self.status.setText(self._summary())
            self.sync()
            self.page.render()
        except (ValueError, KeyError) as exc:
            self._set_context(str(exc))

    def resume_analysis(self):
        """Explicitly select the retained source after validating the current curve editor."""
        try:
            self.page.finish_editor()
            if self._owns_selection():
                self.finish_settings()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.page.inspector_toggle_btn.setChecked(True)
        if self.progress is None:
            self._restore_attempted = False
            self.activate()
            return
        self._loading = True
        try:
            self._load_settings()
            self.page.curve_list.clearSelection()
            for row in range(self.page.curve_list.count()):
                item = self.page.curve_list.item(row)
                if item.data(Qt.UserRole) == self.progress.captured.curve_id:
                    self.page.curve_list.setCurrentItem(item)
                    item.setSelected(True)
        finally:
            self._loading = False
        self.activate()
        self.sync()
        self.status.setText('Resumed retained analysis. ' + self._summary())
        self.page.render()

    def finish_settings(self):
        error = self._settings_error()
        if error:
            raise ValueError(error)
        self._loading = True
        try:
            for spin in self._numeric_spins():
                spin.interpretText()
        finally:
            self._loading = False
        self.sync()
        self._filter_changed()

    def activate(self, *, restore_selection=True):
        if self.page.inspector_tabs.currentIndex() != 1:
            with QSignalBlocker(self.page.inspector_tabs):
                self.page.inspector_tabs.setCurrentIndex(1)
        self.show()
        if self._activated and self._restore_attempted:
            return
        self._activated = True
        restore_message = ''
        if not self._restore_attempted:
            payload = self.page.comparison.analysis_state
            if (payload is not None and not restore_selection
                    and (not isinstance(payload, dict)
                         or not isinstance(payload.get('curve_id'), str)
                         or self.page._selected_ids() != {payload.get('curve_id')})):
                # Navigation cannot select a saved source or replace its progress.
                # Resume restores it; Analyze/Find explicitly starts the selection.
                self.sync()
                return
            self._restore_attempted = True
            if payload is not None:
                self._loading = True
                try:
                    self.progress = XYAnalysisProgress.from_payload(self.page.workspace, self.page.comparison, payload)
                    self._load_settings()
                    if restore_selection:
                        self.page.curve_list.clearSelection()
                        for row in range(self.page.curve_list.count()):
                            item = self.page.curve_list.item(row)
                            if item.data(Qt.UserRole) == self.progress.captured.curve_id:
                                self.page.curve_list.setCurrentItem(item)
                                item.setSelected(True)
                    self._key = self.progress.captured.key
                    self._publish_progress()
                    restore_message = 'Restored saved progress. ' + self._summary()
                except (ValueError, KeyError, TypeError) as exc:
                    self.progress = None
                    restore_message = f'Saved progress could not be restored: {exc}'
                finally:
                    self._loading = False
        self.sync()
        if restore_message:
            self.status.setText(restore_message)
            self.page.render()

    def _filter_spins(self):
        return (self.prominence_spin, self.noise_spin, self.candidate_width_min_spin,
                self.candidate_width_max_spin, self.distance_spin, self.max_peaks_spin)

    def filter_settings(self, *, draft=False):
        if draft and any(not spin.hasAcceptableInput() for spin in self._filter_spins()):
            raise ValueError('Enter valid candidate filter settings.')
        return CandidateFilter(self.preset_combo.currentText(),
            *(self._spin_value(spin, draft) for spin in self._filter_spins()))

    @staticmethod
    def _spin_value(spin, draft):
        if not draft or spin.text() == spin.specialValueText():
            return spin.value()
        return spin.valueFromText(spin.text())

    def _settings_error(self):
        try:
            for spin in self._numeric_spins():
                if not spin.hasAcceptableInput():
                    label = spin.accessibleName().removeprefix('Curve analysis ')
                    raise ValueError(f'Enter a valid value for {label.lower()}.')
            self.settings(draft=True).validate()
            self.filter_settings(draft=True)
        except ValueError as exc:
            return str(exc)
        return ''

    def _load_filter(self, settings):
        controls = (self.preset_combo, *self._filter_spins())
        blockers = [QSignalBlocker(control) for control in (*controls, *(spin.lineEdit() for spin in self._filter_spins()))]
        self.preset_combo.setCurrentText(settings.preset)
        for spin, value in zip(self._filter_spins(), (settings.min_prominence_fraction, settings.min_noise_strength,
            settings.min_width, settings.max_width, settings.min_distance, settings.max_count)):
            spin.setValue(value)
        del blockers

    def _preset_changed(self, name):
        if self._loading or not self._owns_selection() or not self._restore_attempted:
            return
        if name != 'Custom':
            self._load_filter(candidate_filter_preset(name))
        self._filter_changed(custom=False)

    def _filter_changed(self, *_args, custom=True):
        if self._loading or not self._owns_selection() or not self._restore_attempted:
            return
        if self._settings_error():
            self.sync()
            return
        if custom:
            settings = self.filter_settings()
            if self.progress is None or replace(settings, preset=self.progress.filters.preset) != self.progress.filters:
                with QSignalBlocker(self.preset_combo):
                    self.preset_combo.setCurrentText('Custom')
        try:
            settings = self.filter_settings()
            if self.progress is not None and settings != self.progress.filters:
                self._revision += 1
                self._request = None
                self.progress.apply_filter(settings)
                self._result_ids = self._graph_id = None
                self._publish_progress()
                self._persist()
                self.status.setText(self._summary())
                self.page.render()
            else:
                self.sync()
        except ValueError as exc:
            self.status.setText(str(exc))
            self.sync()

    def _summary(self):
        if self.progress is None:
            return 'Find candidates or add seeds to begin.'
        summary = f'{len(self.progress.candidate_pool)} candidates · {len(self.progress.accepted_seeds())} accepted seeds.'
        if self.progress.status == 'empty':
            return 'No peaks found. Adjust the candidate method or scientific X interval.'
        if self.progress.status in ('failed', 'cancelled'):
            summary += '\n' + self.progress.status.title() + ': ' + self.progress.message
        if self.progress.fit_result is not None:
            fit = self.progress.fit_result
            summary += f'\n{fit.model} · RMSE {fit.rmse:.4g} · R² {fit.r_squared:.5g}'
        return summary

    def _publish_progress(self):
        progress = self.progress
        self.captured = progress.captured if progress else None
        self.peaks = progress.peaks() if progress and progress.status != 'pending' else None
        self.fit_result = progress.fit_result if progress else None

    def _persist(self):
        if self.progress is not None:
            payload = self.progress.to_payload()
            if self.page.comparison.analysis_state != payload:
                self.page.comparison.analysis_state = payload
                self.page.changed.emit()

    def settings(self, *, draft=False):
        for spin in (self.width_min_spin, self.width_max_spin, self.sg_window_spin, self.sg_order_spin):
            if draft and not spin.hasAcceptableInput():
                raise ValueError('Enter valid analysis settings before computing.')
        return AnalysisSettings(mode=self.mode_combo.currentText(),
            model=self.model_combo.currentText(), width_min=self._spin_value(self.width_min_spin, draft),
            width_max=self._spin_value(self.width_max_spin, draft), detector=self.detector_combo.currentText(),
            sg_window=self._spin_value(self.sg_window_spin, draft), sg_polyorder=self._spin_value(self.sg_order_spin, draft))

    def current_input(self):
        ids = self.page._selected_ids()
        if len(ids) != 1:
            raise ValueError('Select exactly one curve in the Curves tab to analyze.')
        return capture_input(self.page.workspace, self.page.comparison, next(iter(ids)), self.settings())

    def _set_context(self, text):
        # Long acquisition filenames need line-break opportunities in a narrow
        # inspector. Keep exact source text available to hover and accessibility.
        self.context_label.setText(text.replace('_', '_\u200b'))
        self.context_label.setToolTip(text)
        self.context_label.setAccessibleName(text)

    def sync(self, *_args):
        if self._disposed or self._loading:
            return
        if self.page._appearance_edit:
            # The page guarantees committed scientific inputs are unchanged
            # during this synchronous presentation/draft notification.
            return
        payload = self.page.comparison.analysis_state
        resume_action = getattr(self.page, 'resume_analysis_action', None)
        idle = self.host is not None and not self.host._project_busy() and not self.busy
        if resume_action is not None:
            resume_action.setEnabled(idle and payload is not None)
        if not self._activated:
            curve_id = payload.get('curve_id') if isinstance(payload, dict) else None
            curve = self.page.comparison.curves.get(curve_id) if isinstance(curve_id, str) else None
            if resume_action is not None:
                resume_action.setText(f'Resume {curve.label}' if curve else 'Resume saved analysis')
            for button in (self.find_btn, self.fit_btn, self.save_btn, self.create_graph_btn, self.seeds_btn):
                button.setEnabled(False)
            return
        self.width_label.setText({'Gaussian': 'Sigma', 'Lorentzian': 'HWHM', 'Voigt': 'Sigma and gamma'}[
            self.model_combo.currentText()] + ' in source X units')
        draft_error = self._settings_error()
        self.settings_error.setText(draft_error)
        owns_selection = self._owns_selection()
        editable = owns_selection and self._restore_attempted
        for control in (self.mode_combo, self.model_combo, self.detector_combo, self.preset_combo,
                        self.details_btn, *self._numeric_spins()):
            control.setEnabled(editable)
        current = None
        try:
            current = self.current_input()
            filters = self.filter_settings()
            key = current.key
            info = current.provenance
            low, high = info['actual_interval']
            self._set_context(f"{info['graph']} · {info['curve']}\n{info['sheet']}: {info['x_label']} → {info['y_label']} ({info['source_mode']})\n"
                f"Data X interval ({info['interval_mode']}): {low:g} to {high:g} · source values\n"
                'Toolbar zoom is not the analysis interval.' +
                (f'\n{current.data.gap_count} missing row(s): detection separates spans; fitting uses finite pairs.'
                 if current.data.gap_count else ''))
        except (ValueError, KeyError) as exc:
            key = None
            self._set_context(str(exc))
        idle = self.host is not None and not self.host._project_busy() and not self.busy
        retained_id = (self.progress.captured.curve_id if self.progress is not None else
                       payload.get('curve_id') if isinstance(payload, dict) else None)
        retained = self.page.comparison.curves.get(retained_id) if isinstance(retained_id, str) else None
        source = (self.progress.captured.provenance['curve'] if self.progress is not None else
                  retained.label if retained else 'saved analysis')
        self.resume_btn.setText(f'Resume {source}')
        self.resume_btn.setToolTip(self.resume_btn.text())
        self.resume_btn.setVisible(payload is not None and (not owns_selection or not self._restore_attempted))
        self.resume_btn.setEnabled(idle and payload is not None)
        self.start_selected_btn.setText('Start analysis for ' + (current.provenance['curve'] if current else 'selected curve'))
        self.start_selected_btn.setToolTip(self.start_selected_btn.text())
        self.start_selected_btn.setVisible(not owns_selection or not self._restore_attempted)
        self.start_selected_btn.setEnabled(idle and key is not None and not draft_error)
        resume_action = getattr(self.page, 'resume_analysis_action', None)
        if resume_action is not None:
            resume_action.setText(f'Resume {source}')
            resume_action.setEnabled(idle and payload is not None)
        if not self._restore_attempted:
            self.status.setText('Saved progress is available. Resume selects its source; '
                                'Start analysis or Find candidates uses the selected curve.')
            self.find_btn.setEnabled(idle and key is not None and not draft_error)
            for button in (self.fit_btn, self.save_btn, self.create_graph_btn, self.seeds_btn):
                button.setEnabled(False)
            return
        selection_changed = (key[1] if key else None) != (self._key[1] if self._key else None)
        if key != self._key:
            # Browsing invalidates in-flight delivery, but never replaces the
            # captured analysis source or its completed pool/seeds/fit.
            self._key = key
            self._revision += 1
            self._request = None
        old = self.progress.captured if self.progress is not None else None
        try:
            filters = self.filter_settings()
            context = (current if old is None or current is not None and current.curve_id == old.curve_id
                       else capture_input(self.page.workspace, self.page.comparison, old.curve_id, old.settings))
            self._context_valid = context is not None
        except (ValueError, KeyError):
            context = None
            self._context_valid = False
        if context is not None and not draft_error and (old is None or old.key != context.key):
            had_result = self.peaks is not None or self.fit_result is not None
            self._revision += 1
            self._request = None
            self._result_ids = self._graph_id = None
            detection = lambda settings: (settings.mode, settings.detector, settings.sg_window, settings.sg_polyorder)
            if (old is not None and input_fingerprint(old) == input_fingerprint(context)
                    and detection(old.settings) == detection(context.settings)):
                self.progress.captured = replace(old, settings=context.settings, key=context.key,
                    provenance={**old.provenance, 'settings': context.provenance['settings']})
                self.progress.retire_fit()
                self.status.setText('Fit settings changed. Candidates and seeds retained.')
            else:
                self.progress = XYAnalysisProgress(context, filters=filters)
                if had_result:
                    self.status.setText('Inputs changed. Find candidates again.')
            self._persist()
            self._publish_progress()
            self._remove_overlays()
        elif not self._context_valid and not draft_error and self.fit_result is not None:
            # A changed/unusable scientific source still invalidates its fit.
            # Only editor drafts retain the last committed result.
            self.progress.retire_fit()
            self._result_ids = self._graph_id = None
            self._publish_progress()
            self._persist()
            self._remove_overlays()
            self.status.setText('Source or data X interval is invalid. Correct it and find candidates again.')
        matching = self._context_valid and key is not None and self.captured is not None and key == self.captured.key
        if self.progress is not None and self.page._selected_ids() != {self.progress.captured.curve_id}:
            source = self.progress.captured.provenance['curve']
            self._set_context(self.context_label.toolTip() + f'\nRetained analysis: {source}.')
            self.status.setText(f'Resume {source} to edit its analysis, or start analysis for the selected curve.\n' + self._summary())
        elif matching and selection_changed and not self.busy:
            self.status.setText(self._summary())
        if draft_error:
            self.status.setText(draft_error + '\nPrevious results are retained until valid settings are committed.')
        elif self.status.text().endswith('Previous results are retained until valid settings are committed.'):
            self.status.setText(self._summary())
        idle = self.host is not None and not self.host._project_busy() and not self.busy and not draft_error
        self.find_btn.setEnabled(idle and key is not None)
        self.seeds_btn.setEnabled(idle and matching)
        has_peaks = self.peaks is not None and bool(self.peaks.indices.size)
        self.fit_btn.setEnabled(idle and matching and has_peaks)
        saved = saved_result_records(self.page.workspace, self._result_ids)
        self.save_btn.setEnabled(idle and matching and has_peaks and saved is None)
        self.create_graph_btn.setEnabled(idle and matching and has_peaks)

    def _remove_overlays(self):
        axes = getattr(self.page, 'axes', None)
        if axes is not None:
            removed = False
            for line in list(axes.lines):
                if line.get_gid() in ('xy-analysis-peaks', 'xy-analysis-fit'):
                    line.remove()
                    removed = True
            if removed:
                self.page.canvas.draw_idle()

    def start(self, operation):
        if self.host is None:
            return
        try:
            self.host._require_project_idle()
            self.page.finish_editor()
            self.finish_settings()
            captured = self.current_input()
            self.filter_settings()
            self._restore_attempted = True
            self.sync()
            if operation == 'find' and (self.progress is None or self.progress.captured.key != captured.key):
                self.progress = XYAnalysisProgress(captured, filters=self.filter_settings())
                self._result_ids = self._graph_id = None
                self._context_valid = True
                self._publish_progress()
                self._persist()
                self._remove_overlays()
            if self.progress is None or self._key is None:
                raise ValueError('Enter valid analysis settings before computing.')
            if operation == 'fit' and captured.key != self.progress.captured.key:
                raise ValueError('Select the retained analysis curve or Find candidates for this curve before fitting.')
            if operation == 'fit' and (self.peaks is None or not self.peaks.indices.size):
                raise ValueError('Find peaks before fitting.')
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        seeds = list(self.progress.seeds)
        revision = self._revision
        request = object()
        workspace = self.page.workspace
        owner = ('xy-analysis', self.page.comparison_id)
        self._request = request
        self.status.setText('Finding peaks…' if operation == 'find' else 'Fitting source values…')

        def matches():
            if self._disposed or self.host.workspace is not workspace or self.host._background_job_owner != owner:
                return False
            self.sync()
            return self._request is request and self._revision == revision and self._key == captured.key

        def work(context):
            context.check_cancelled()
            result = (detect_peaks(captured.data, captured.settings) if operation == 'find' else
                      fit_seeds(captured.data, captured.settings, seeds, check_cancelled=context.check_cancelled))
            context.check_cancelled()
            return result

        def apply(result):
            if not matches():
                return
            self._request = None
            self.progress.captured = captured
            self._result_ids = self._graph_id = None
            if operation == 'find':
                self.progress.set_candidates(result)
            else:
                self.progress.fit_result = result
                self.progress.status = 'fitted'
                self.progress.message = ''
            self._publish_progress()
            self._persist()
            self.status.setText(self._summary())
            self.page.render()

        def error(exc):
            if matches():
                self._request = None
                self.progress.fit_result = None
                self.progress.status = 'failed'
                self.progress.message = str(exc)
                self._publish_progress()
                self._persist()
                self.status.setText(str(exc))

        self.host._start_background_job(work, apply, self.status.text(), on_error=error, owner=owner)

    def background_finished(self, *, cancelled=False):
        if self._request is not None:
            self._request = None
            self.status.setText('Operation cancelled.' if cancelled else 'Operation finished without a current result. Find peaks again.')
            if self.progress is not None:
                self.progress.message = self.status.text()
                if self.fit_result is None:
                    self.progress.status = 'cancelled'
                self._persist()
        self.sync()

    def draw_overlays(self, axes):
        self.sync()
        if not self._context_valid or self.captured is None or self.peaks is None:
            return
        comparison = self.page.comparison
        curve = comparison.curves[self.captured.curve_id]
        if not curve.visible:
            return
        ordinal = list(comparison.curves).index(curve.curve_id)
        data = self.captured.data
        reference = data.y[data.valid]
        def transform(values):
            return transform_y(values, reference, comparison.normalization,
                               ordinal * comparison.stack_step, curve.y_offset)
        for kind, marker in (('peak', 'x'), ('dip', '+')):
            positions = np.array([seed.position for seed in self.progress.accepted_seeds() if seed.kind == kind])
            if positions.size:
                values = np.interp(positions, data.x[data.valid], data.y[data.valid])
                axes.plot(positions, transform(values), linestyle='None', marker=marker,
                          markersize=9, markeredgewidth=2, color=curve.color, gid='xy-analysis-peaks', label='_detected')
        if self.fit_result is not None:
            values = np.full(data.y.shape, np.nan)
            values[data.valid] = transform(self.fit_result.y_fit)
            axes.plot(data.x, values, '--', color='#b91c1c', linewidth=2,
                      gid='xy-analysis-fit', label='_fit')

    def save_results(self):
        self._save_results(create_graph=False)

    def set_seeds(self, seeds):
        if self.progress is None or not self._owns_selection() or self._settings_error():
            raise ValueError('Select a valid curve before editing seeds.')
        previous = self.progress.seeds
        self.progress.set_seeds(seeds)
        if self.progress.seeds == previous:
            return
        self._revision += 1
        self._request = None
        self._result_ids = self._graph_id = None
        self._publish_progress()
        self._persist()
        self.status.setText(self._summary())
        self.page.render()

    def edit_seeds(self):
        self.sync()
        if self.progress is None or not self._owns_selection() or self._settings_error():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('XY analysis seeds')
        dialog.resize(1020, 460)
        layout = QVBoxLayout(dialog)
        note = QLabel('All candidates remain in the pool. Edited rows override future filters. Uncheck Use to exclude.\n'
            'Blank height/width/γ means estimate. Heights are signed components above the baseline; X and widths use source units.\n'
            'Width: Gaussian σ, Lorentzian HWHM, Voigt σ with a separate γ. Fix X or set lower/upper bounds if needed.')
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(0, 9)
        table.setObjectName('xy-seed-table')
        table.setHorizontalHeaderLabels(['Use', 'Center X', 'Kind', 'Height', 'Width / σ', 'γ (Voigt)', 'Fix X', 'Lower X', 'Upper X'])
        tabs = QTabWidget()
        tabs.addTab(table, 'Edit seeds')
        fit = self.fit_result
        if fit is not None:
            results = QTableWidget(len(fit.centers), 4 if fit.model == 'Voigt' else 3)
            labels = ['Fitted center', 'Signed height', 'σ' if fit.model != 'Lorentzian' else 'HWHM']
            columns = [fit.centers, fit.amplitudes, fit.widths]
            if fit.model == 'Voigt':
                labels.append('γ'); columns.append(fit.gammas)
            results.setHorizontalHeaderLabels(labels)
            results.setEditTriggers(QAbstractItemView.NoEditTriggers)
            for column, values in enumerate(columns):
                for row, value in enumerate(values):
                    results.setItem(row, column, QTableWidgetItem(format(value, '.10g')))
            results.resizeColumnsToContents()
            tabs.addTab(results, fit.model + ' fit')
        layout.addWidget(tabs)
        originals = {}

        def add_row(seed):
            row = table.rowCount()
            table.insertRow(row)
            originals[seed.seed_id] = seed
            for column, value in ((0, seed.enabled), (6, seed.fixed_center)):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if value else Qt.Unchecked)
                item.setData(Qt.UserRole, seed.seed_id)
                table.setItem(row, column, item)
            for column, value in ((1, seed.position), (2, seed.kind), (3, seed.amplitude), (4, seed.width),
                                  (5, seed.gamma), (7, seed.center_min), (8, seed.center_max)):
                item = QTableWidgetItem('' if value is None else
                    format(value, '.10g') if isinstance(value, float) else str(value))
                item.setData(Qt.UserRole, value)
                table.setItem(row, column, item)

        for seed in self.progress.seeds:
            add_row(seed)
        table.resizeColumnsToContents()
        actions = QHBoxLayout()
        add = QPushButton('Add seed')
        exclude = QPushButton('Exclude selected')
        reset = QPushButton('Use filter for selected')
        actions.addWidget(add); actions.addWidget(exclude); actions.addWidget(reset)
        layout.addLayout(actions)
        add.clicked.connect(lambda: add_row(AnalysisSeed(
            float(np.median(self.progress.captured.data.x[self.progress.captured.data.valid])), manual=True)))

        def selected_rows():
            return sorted({item.row() for item in table.selectedItems()})

        def exclude_rows():
            for row in selected_rows():
                table.item(row, 0).setCheckState(Qt.Unchecked)

        resets = set()
        def reset_rows():
            for row in selected_rows():
                identity = table.item(row, 0).data(Qt.UserRole)
                original = originals[identity]
                candidate = next((candidate for candidate in self.progress.candidate_pool
                    if (candidate.index, candidate.kind) == (original.source_index, original.kind)), None)
                if candidate is not None:
                    resets.add(identity)
                    table.item(row, 1).setText(format(candidate.position, '.10g'))
                    table.item(row, 1).setData(Qt.UserRole, candidate.position)

        exclude.clicked.connect(exclude_rows)
        reset.clicked.connect(reset_rows)
        feedback = QLabel()
        feedback.setWordWrap(True)
        layout.addWidget(feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)

        def accept():
            try:
                seeds = []
                for row in range(table.rowCount()):
                    original = originals[table.item(row, 0).data(Qt.UserRole)]
                    def number(column, optional=False):
                        item = table.item(row, column)
                        value = item.text().strip()
                        stored = item.data(Qt.UserRole)
                        if stored is not None and value == format(stored, '.10g'):
                            return stored
                        return None if optional and not value else float(value)
                    seed = replace(original, position=number(1), kind=table.item(row, 2).text().strip().lower(),
                        enabled=table.item(row, 0).checkState() == Qt.Checked,
                        amplitude=number(3, True), width=number(4, True), gamma=number(5, True),
                        fixed_center=table.item(row, 6).checkState() == Qt.Checked,
                        center_min=number(7, True), center_max=number(8, True))
                    seed = replace(seed, manual=(seed.manual or seed != original) and seed.seed_id not in resets)
                    seeds.append(seed)
                self.set_seeds(seeds)
                self.progress.apply_filter(self.filter_settings())
                self._publish_progress(); self._persist(); self.page.render()
                dialog.accept()
            except ValueError as exc:
                feedback.setText(str(exc))

        buttons.accepted.connect(accept)
        dialog.exec()
        dialog.deleteLater()

    def create_analysis_graph(self):
        self._save_results(create_graph=True)

    def _save_results(self, *, create_graph):
        if self.host is None:
            return
        try:
            self.host._require_project_idle()
            self.page.finish_editor()
            self.finish_settings()
            self.sync()
            if (self.busy or self._key is None or self.captured is None or self.peaks is None
                    or self._key != self.captured.key or not self._owns_selection()):
                raise ValueError('Find peaks before saving results.')
            saved = saved_result_records(self.page.workspace, self._result_ids)
            book, sheets = saved or result_workbook(self.captured, self.peaks, self.fit_result,
                                                   seeds=self.progress.accepted_seeds())
            graph_id = self.host._save_xy_analysis_results(self.page, book, sheets,
                create_graph=create_graph, graph_id=self._graph_id if saved else None,
                has_fit=self.fit_result is not None)
            self._result_ids = (book.workbook_id, tuple(sheet.sheet_id for sheet in sheets))
            if create_graph:
                self._graph_id = graph_id
            self.status.setText(f'Saved {len(sheets)} detached result sheet(s) to {book.name}.' +
                                (' Analysis graph is ready in Workspace.' if create_graph else ''))
            self.sync()
        except ValueError as exc:
            self.status.setText(str(exc))

    def dispose(self):
        self._disposed = True
        self._request = None
