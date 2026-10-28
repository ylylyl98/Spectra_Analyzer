"""Contextual XY analysis in the graph inspector, with owner-checked delivery."""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, replace
from typing import Callable
from PySide6.QtCore import QObject, Qt, Signal, Slot

from .quick_dialog import QuickDialog
from .xy_analysis_bridge import XYAnalysisBridge, XYSeedsBridge

from .xy_analysis import (AnalysisSettings, capture_input, collect_candidates as detect_peaks,
                          fit_seeds, result_workbook, XYAnalysisProgress, input_fingerprint)
from .peak_candidates import CandidateFilter
from .comparison_display import transform_y
from .analysis_graph import saved_result_records


class _ModelessSettingsDialog(QuickDialog):
    """A live editor hides on every close route and never enters finished state."""
    @Slot()
    def reject(self):
        self.bridge.retire_editors()
        self.window().hide()

    @Slot()
    def accept(self):
        self.reject()

    def close(self):
        self.reject()
        return True


@dataclass(frozen=True)
class XYAnalysisServices:
    project_busy: Callable[[], bool]
    require_project_idle: Callable[[], None]
    current_workspace: Callable[[], object]
    background_owner: Callable[[], object]
    start_background_job: Callable
    publish_results: Callable


class XYAnalysisController(QObject):
    revealRequested = Signal()

    def __init__(self, page, *, services=None):
        super().__init__(page)
        self.page = page
        self.services = services
        self.captured = self.peaks = self.fit_result = None
        self._key = self._request = None
        self._revision = 0
        self._result_ids = self._graph_id = None
        self._disposed = self._activated = self._loading = self._restore_attempted = False
        self.progress = None
        self._context_valid = False
        self._settings, self._filters = AnalysisSettings(), CandidateFilter()
        self._status = 'Find candidates, adjust filters or seeds, then optionally fit.'
        self._state = dict(context='', status=self._status, error='', widthLabel='Sigma in source X units',
            editable=False, busy=False, resumeText='Resume saved analysis', resumeVisible=False,
            resumeEnabled=False, startText='Start analysis for selected curve', startVisible=False,
            startEnabled=False, findEnabled=False, fitEnabled=False, seedsEnabled=False,
            saveEnabled=False, graphEnabled=False)
        self.bridge = XYAnalysisBridge(self)
        self.bridge.setParent(self)
        self.details_dialog = None
        self._seeds_dialog = None

    @property
    def busy(self):
        return self._request is not None

    def _owns_selection(self):
        return self.progress is None or self.page._selected_ids() == {self.progress.captured.curve_id}

    def _load_settings(self, *, preserve_drafts=False):
        self.bridge.load(self.progress.captured.settings, self.progress.filters, preserve_drafts=preserve_drafts)

    def analyze_selected(self):
        """Explicitly open/start analysis for the selected curve, without selecting a saved source."""
        try:
            self.page.finish_editor()
            self.finish_settings()
        except ValueError as exc:
            self._set_status(str(exc))
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
                self._set_status(self._summary())
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
            self._set_status(str(exc))
            return
        self.page.set_inspector_checked(True)
        self.page._set_inspector_visible(True)
        if self.progress is None:
            self._restore_attempted = False
            self.activate()
            return
        self._loading = True
        try:
            self._load_settings(preserve_drafts=True)
            self.page.set_curve_selection({self.progress.captured.curve_id},
                                          self.progress.captured.curve_id, preflight=False)
        finally:
            self._loading = False
        self.activate()
        self.sync()
        self._set_status('Resumed retained analysis. ' + self._summary())
        self.page.render()

    def finish_settings(self):
        if not self.bridge.commit_current():
            raise ValueError(self.bridge.validation_error())

    def activate(self, *, restore_selection=True):
        self.page.inspectorSectionRequested.emit(1, '')
        self.revealRequested.emit()
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
                        self.page.set_curve_selection({self.progress.captured.curve_id},
                                                      self.progress.captured.curve_id, preflight=False)
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
            self._set_status(restore_message)
            self.page.render()

    def filter_settings(self, *, draft=False):
        if draft:
            _, filters, errors = self.bridge._parsed()
            if errors:
                raise ValueError(next(iter(errors.values())))
            return filters
        return self._filters

    def _settings_error(self):
        return self.bridge.validation_error()

    def _records_committed(self):
        if self._loading or not self._owns_selection():
            return
        self.sync()
        if self.progress is not None and self._filters != self.progress.filters:
            self._revision += 1
            self._request = None
            self.progress.apply_filter(self._filters)
            self._result_ids = self._graph_id = None
            self._publish_progress()
            self._persist()
            self._status = self._summary()
            self.page.render()
        self.sync()

    def show_settings(self):
        if self.details_dialog is None:
            self.details_dialog = _ModelessSettingsDialog(self.bridge, 'XYAnalysisSettings.qml', self,
                title='Candidate filter and fit settings', size=(620, 720))
            self.bridge.setParent(self)
            self.details_dialog.window().setTransientParent(self.page.prompt_window())
            self.details_dialog.window().setModality(Qt.NonModal)
        self.details_dialog.show()

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
        if draft:
            settings, _, errors = self.bridge._parsed()
            if errors:
                raise ValueError(next(iter(errors.values())))
            return settings
        return self._settings

    def current_input(self):
        ids = self.page._selected_ids()
        if len(ids) != 1:
            raise ValueError('Select exactly one curve in the Curves tab to analyze.')
        return capture_input(self.page.workspace, self.page.comparison, next(iter(ids)), self.settings())

    def _set_context(self, text):
        self._state['context'] = text

    def sync(self, *_args):
        if self._disposed or self._loading:
            return
        if self.page._appearance_edit:
            # The page guarantees committed scientific inputs are unchanged
            # during this synchronous presentation/draft notification.
            return
        payload = self.page.comparison.analysis_state
        resume_action = getattr(self.page, 'resume_analysis_action', None)
        idle = self.services is not None and not self.services.project_busy() and not self.busy
        if resume_action is not None:
            resume_action.setEnabled(idle and payload is not None)
        if not self._activated:
            curve_id = payload.get('curve_id') if isinstance(payload, dict) else None
            curve = self.page.comparison.curves.get(curve_id) if isinstance(curve_id, str) else None
            if resume_action is not None:
                resume_action.setText(f'Resume {curve.label}' if curve else 'Resume saved analysis')
            for key in ('findEnabled', 'fitEnabled', 'saveEnabled', 'graphEnabled', 'seedsEnabled'):
                self._state[key] = False
            self._refresh_bridge()
            return
        self._state['widthLabel'] = ({'Gaussian': 'Sigma', 'Lorentzian': 'HWHM', 'Voigt': 'Sigma and gamma'}[
            self._settings.model] + ' in source X units')
        draft_error = self._settings_error()
        self._state['error'] = draft_error
        owns_selection = self._owns_selection()
        editable = owns_selection and self._restore_attempted
        self._state['editable'] = editable
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
        idle = self.services is not None and not self.services.project_busy() and not self.busy
        retained_id = (self.progress.captured.curve_id if self.progress is not None else
                       payload.get('curve_id') if isinstance(payload, dict) else None)
        retained = self.page.comparison.curves.get(retained_id) if isinstance(retained_id, str) else None
        source = (self.progress.captured.provenance['curve'] if self.progress is not None else
                  retained.label if retained else 'saved analysis')
        self._state.update(resumeText=f'Resume {source}',
            resumeVisible=payload is not None and (not owns_selection or not self._restore_attempted),
            resumeEnabled=idle and payload is not None,
            startText='Start analysis for ' + (current.provenance['curve'] if current else 'selected curve'),
            startVisible=not owns_selection or not self._restore_attempted,
            startEnabled=idle and key is not None and not draft_error)
        resume_action = getattr(self.page, 'resume_analysis_action', None)
        if resume_action is not None:
            resume_action.setText(f'Resume {source}')
            resume_action.setEnabled(idle and payload is not None)
        if not self._restore_attempted:
            self._set_status('Saved progress is available. Resume selects its source; '
                                'Start analysis or Find candidates uses the selected curve.')
            self._state['findEnabled'] = bool(idle and key is not None and not draft_error)
            for field in ('fitEnabled', 'saveEnabled', 'graphEnabled', 'seedsEnabled'):
                self._state[field] = False
            self._refresh_bridge()
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
        if context is not None and (old is None or old.key != context.key):
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
                self._set_status('Fit settings changed. Candidates and seeds retained.')
            else:
                self.progress = XYAnalysisProgress(context, filters=filters)
                if had_result:
                    self._set_status('Inputs changed. Find candidates again.')
            self._persist()
            self._publish_progress()
            self._remove_overlays()
        elif not self._context_valid and self.fit_result is not None:
            # A changed/unusable scientific source still invalidates its fit.
            # Only editor drafts retain the last committed result.
            self.progress.retire_fit()
            self._result_ids = self._graph_id = None
            self._publish_progress()
            self._persist()
            self._remove_overlays()
            self._set_status('Source or data X interval is invalid. Correct it and find candidates again.')
        matching = self._context_valid and key is not None and self.captured is not None and key == self.captured.key
        if self.progress is not None and self.page._selected_ids() != {self.progress.captured.curve_id}:
            source = self.progress.captured.provenance['curve']
            self._set_context(self._state['context'] + f'\nRetained analysis: {source}.')
            self._set_status(f'Resume {source} to edit its analysis, or start analysis for the selected curve.\n' + self._summary())
        elif matching and selection_changed and not self.busy:
            self._set_status(self._summary())
        if draft_error:
            self._set_status(draft_error + '\nPrevious results are retained until valid settings are committed.')
        elif self._status.endswith('Previous results are retained until valid settings are committed.'):
            self._set_status(self._summary())
        idle = self.services is not None and not self.services.project_busy() and not self.busy and not draft_error
        self._state['findEnabled'] = bool(idle and key is not None)
        self._state['seedsEnabled'] = bool(idle and matching)
        has_peaks = self.peaks is not None and bool(self.peaks.indices.size)
        self._state['fitEnabled'] = bool(idle and matching and has_peaks)
        saved = saved_result_records(self.page.workspace, self._result_ids)
        self._state['saveEnabled'] = bool(idle and matching and has_peaks and saved is None)
        self._state['graphEnabled'] = bool(idle and matching and has_peaks)

        self._refresh_bridge()

    def _refresh_bridge(self):
        self._state.update(status=self._status, busy=self.busy)
        self.bridge.refresh()

    def _set_status(self, text):
        self._status = text
        self._state['status'] = text
        self.bridge.changed.emit()

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
        if self.services is None:
            return
        try:
            self.services.require_project_idle()
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
            self._set_status(str(exc))
            return
        seeds = list(self.progress.seeds)
        revision = self._revision
        request = object()
        workspace = self.page.workspace
        owner = ('xy-analysis', self.page.comparison_id)
        self._request = request
        self._set_status('Finding peaks…' if operation == 'find' else 'Fitting source values…')

        def matches():
            if self._disposed or self.services.current_workspace() is not workspace or self.services.background_owner() != owner:
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
            self._set_status(self._summary())
            self.page.render()

        def error(exc):
            if matches():
                self._request = None
                self.progress.fit_result = None
                self.progress.status = 'failed'
                self.progress.message = str(exc)
                self._publish_progress()
                self._persist()
                self._set_status(str(exc))

        self.services.start_background_job(work, apply, self._status, on_error=error, owner=owner)

    def background_finished(self, *, cancelled=False):
        if self._request is not None:
            self._request = None
            self._set_status('Operation cancelled.' if cancelled else 'Operation finished without a current result. Find peaks again.')
            if self.progress is not None:
                self.progress.message = self._status
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
        self._set_status(self._summary())
        self.page.render()

    def edit_seeds(self):
        self.sync()
        if self.progress is None or not self._owns_selection() or self._settings_error():
            return
        bridge = XYSeedsBridge(self)
        dialog = QuickDialog(bridge, 'XYAnalysisSeeds.qml', self, title='XY analysis seeds', size=(1080, 560))
        dialog.window().setTransientParent(self.page.prompt_window())
        self._seeds_dialog = dialog
        try:
            dialog.exec()
        finally:
            bridge.shutdown()
            self._seeds_dialog = None
            dialog.deleteLater()

    def create_analysis_graph(self):
        self._save_results(create_graph=True)

    def _save_results(self, *, create_graph):
        if self.services is None:
            return
        try:
            self.services.require_project_idle()
            self.page.finish_editor()
            self.finish_settings()
            self.sync()
            if (self.busy or self._key is None or self.captured is None or self.peaks is None
                    or self._key != self.captured.key or not self._owns_selection()):
                raise ValueError('Find peaks before saving results.')
            saved = saved_result_records(self.page.workspace, self._result_ids)
            book, sheets = saved or result_workbook(self.captured, self.peaks, self.fit_result,
                                                   seeds=self.progress.accepted_seeds())
            graph_id = self.services.publish_results(self.page, book, sheets,
                create_graph=create_graph, graph_id=self._graph_id if saved else None,
                has_fit=self.fit_result is not None)
            self._result_ids = (book.workbook_id, tuple(sheet.sheet_id for sheet in sheets))
            if create_graph:
                self._graph_id = graph_id
            self._set_status(f'Saved {len(sheets)} detached result sheet(s) to {book.name}.' +
                                (' Analysis graph is ready in Workspace.' if create_graph else ''))
            self.sync()
        except ValueError as exc:
            self._set_status(str(exc))

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._request = None
        self.bridge.retire_editors()
        if self._seeds_dialog is not None:
            self._seeds_dialog.reject()
        if self.details_dialog is not None:
            self.details_dialog.window().hide()
            self.details_dialog.deleteLater()
            self.details_dialog = None
