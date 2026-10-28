"""Shared DAT Analysis editor, with a formal result and auxiliary Gate linecut."""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import csv
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QSignalBlocker, QTimer

from .file_picker import FilePicker
from .formal_preview_controller import FormalPreviewController
from .analysis_linecut_controller import AnalysisLinecutController


class AnalysisWorkbenchMixin:
    def _workbench_document(self):
        session = self.workspace.active
        return getattr(session.active_graph, 'analysis', None) if session else None

    def _workbench_update_input_context(self):
        """Return plain frozen-input provenance without mutating a presentation."""
        from .analysis_document import analysis_input_changed
        doc = self._workbench_document()
        if doc is None:
            return None
        session, cube = self.workspace.active, doc.science.current_cube
        graph = session.active_graph
        source = session.graphs.get(doc.source_graph_id)
        visible = cube.gate.size if graph.gate_indices is None else len(graph.gate_indices)
        scope = cube.gate.size if doc.gate_scope == 'all' else 1
        recipe = doc.science.applied.recipe()
        kind = 'Original' if doc.source_mode == 'raw' else 'Processed'
        detail = ('D2, SG ' + str(recipe['window_length']) if recipe['smooth'] else 'D2, no smoothing') if recipe['mode'] == 'energy_second_derivative' else 'original signal'
        changed = source is not None and analysis_input_changed(session, graph,
            source_mode=doc.source_mode, gate_indices=source.gate_indices)
        title = session.source_path.name if session.source_path else 'Embedded data'
        index = self._current_gate_index()
        warning = 'Source changed' if changed else 'Source removed' if source is None else ''
        details = (f'Analysis — {title} · {graph.name}\n'
            f'{session.source_path or "Embedded data"}\n'
            f'Analysis input: {doc.input_label} · version {doc.input_version}\n'
            f'Frozen {kind}: {detail}\n'
            f'Gate: {cube.gate[index]:g} ({index+1}/{cube.gate.size})\n'
            f'Graph display: {visible} of {cube.gate.size} Gates · analyzed {scope}\n'
            f'All Gates analyzes all {cube.gate.size} bound Gates\n'
            f'Format source: {source.name if source else "Retained format (source removed)"}\n'
            f'Frozen processing recipe: {recipe}\n'
            'Source changes do not replace this bound input.\n'
            'Detection Direct uses this bound input; D2-assisted differentiates it for candidate detection.')
        if warning:
            details += '\n' + warning
        return dict(title=title, summary=f'Frozen {kind} · {visible}/{cube.gate.size} Gates · analyzed {scope}',
                    warning=warning, details=details)

    def _workbench_cancel_job(self):
        """Remember explicit cancellation so completed Gate outcomes survive idle."""
        state = getattr(self, '_workbench_running_job', None)
        if state is not None:
            state['cancelled'] = True

    def _build_analysis_workbench(self):
        from .analysis_workbench_bridge import AnalysisWorkbenchBridge
        self._workbench_syncing = False
        self._workbench_running_job = None
        self._workbench_last_summary = None
        self._workbench_filter_context = None
        self._background_jobs.idle.connect(self._workbench_job_idle)
        self._workbench_render_timer = QTimer(self)
        self._workbench_render_timer.setSingleShot(True)
        self._workbench_render_timer.timeout.connect(self._workbench_render)
        self.analysis_workbench = AnalysisWorkbenchBridge(self)
        self.statusChanged.connect(self._workbench_progress_message)
        self._workbench_status_text = ''
        self._workbench_aux_linecut_visible = True
        self.workbench_preview = FormalPreviewController(self, display_policy='readonly')
        self._workbench_preview_context = None
        self.workbench_linecut_figure = Figure(figsize=(7., 1.8), layout='tight')
        self.workbench_linecut_canvas = AnalysisLinecutController(self.workbench_linecut_figure, self)
        self._workbench_linecut_sizes = [550, 170]

    def _workbench_toggle_linecut(self, visible):
        self._workbench_aux_linecut_visible = bool(visible)
        self.analysis_workbench.stateChanged.emit()
        self.shellChanged.emit()

    def _workbench_progress_message(self, message):
        if self._background_job_owner == 'analysis' and self._project_busy() and self._workbench_document() is not None:
            self._workbench_status_text = message
            self.analysis_workbench.stateChanged.emit()

    def _workbench_start_job(self, work, apply, description, *, results, gates, operation):
        state = dict(results=results, gates=gates, operation=operation, cancelled=False,
                     document=self._workbench_document(), token=self._workbench_job_token())
        def complete(outcomes):
            if self._workbench_running_job is state:
                self._workbench_running_job = None
            if state['token'] != self._workbench_job_token():
                return
            apply(outcomes)
            doc = state['document']
            cancelled = len(gates)-len(outcomes) if state['cancelled'] else 0
            if cancelled:
                for gate in set(gates)-set(outcomes):
                    doc.gate_messages[gate] = 'Cancelled before this Gate completed; previous results retained.'
                self._schedule_project_modified()
            counts = {name: sum(doc.gate_status.get(gate) == name for gate in outcomes)
                      for name in ('fitted', 'detected', 'failed', 'no-seed', 'empty')}
            success = 'fitted' if operation == 'Fit' else 'detected'
            summary = (f'{operation}: {len(outcomes)}/{len(gates)} processed · {counts[success]} {success} · '
                       f'{counts["failed"]} failed · {counts["no-seed"]} no-seed · {cancelled} cancelled')
            if operation == 'Find':
                summary += f' · {counts["empty"]} empty'
            self._workbench_last_summary = doc, doc.job_token, summary
            self._workbench_status_text = summary
            self.analysis_workbench.refresh()
        state['complete'] = complete
        self._workbench_running_job = state
        self._workbench_last_summary = None
        # The acknowledgement must reach the Quick host before its worker runs.
        self._workbench_status_text = description
        self.analysis_workbench.stateChanged.emit()
        self._start_background_job(work, complete, description, owner='analysis')

    def _workbench_job_idle(self):
        state = self._workbench_running_job
        if state is not None:
            self._workbench_running_job = None
            if state['cancelled']:
                state['complete'](dict(state['results']))
        self._workbench_refresh()

    def _workbench_set_visible(self):
        active = self._workbench_document() is not None and self._outer_view == 'analysis'
        self.plot_page.set_enabled(self._workbench_document() is None)
        self.processing.syncFromOwner()
        self.dat_properties.syncFromOwner()
        self._workbench_sync_preview_owner()
        if not active:
            self.analysis_workbench.close_popups()
        self.analysis_navigation.refresh()
        if active:
            self._workbench_refresh()
        self.shellChanged.emit()

    def _workbench_open(self, source, *, duplicate=False):
        from .analysis_document import ensure_analysis_graph
        source = self._resolve_analysis_source(source)
        if source is None:
            return
        self._require_project_idle()
        self._finish_current_document()
        kind, identity = source
        if kind == 'heatmap':
            dataset_id, source_id = identity
            session = self.workspace.datasets[dataset_id]
            requested = session.graphs[source_id]
            if getattr(requested, 'analysis', None) is not None:
                graph = requested
                source_id = graph.analysis.source_graph_id
            else:
                graph = None
            return_key = (dataset_id, source_id) if source_id else None
        else:
            sheet = self.workspace.sheets[identity]
            dataset_id = sheet.dataset_id
            session = self.workspace.datasets[dataset_id]
            source_id = next((g.graph_id for g in session.graphs.values() if getattr(g, 'analysis', None) is None
                              and g.source_mode == ('raw' if sheet.role == 'original' else 'processed')
                              and g.result_id == sheet.result_id), None)
            if source_id is None:
                from .dataset_state import GraphState
                template = next((g for g in session.graphs.values() if g.analysis is None),
                                session.active_graph)
                source_id = uuid4().hex
                mode = 'raw' if sheet.role == 'original' else 'processed'
                name = 'Original input' if mode == 'raw' else 'Processed input'
                names = {g.name for g in session.graphs.values()}
                while name in names:
                    name += ' copy'
                session.graphs[source_id] = GraphState(source_id, name, deepcopy(template.view),
                    deepcopy(template.presentation), source_mode=mode, published=False, result_id=sheet.result_id)
            mode = 'raw' if sheet.role == 'original' else 'processed'
            graph = next((g for g in session.graphs.values() if g.analysis is not None
                          and g.analysis.source_graph_id == source_id and g.analysis.source_mode == mode), None)
            duplicate = graph is None and any(g.analysis is not None and g.analysis.source_graph_id == source_id
                                             for g in session.graphs.values())
            self.open_workbook(self._sheet_owner(identity).workbook_id, identity)
            return_key = self.graph_documents.registry.active_key
        if dataset_id != self.workspace.active_dataset_id:
            self.activate_dataset(dataset_id)
        if graph is None:
            graph = ensure_analysis_graph(session, source_id, duplicate=duplicate)
        else:
            session.activate_graph(graph.graph_id)
        self._analysis_return_key = return_key
        self._workbench_enter(session)
        return self.plot_page

    def _workbench_enter(self, session):
        from .analysis_document import sync_analysis_format
        sync_analysis_format(session)
        self._workbench_restore_settings()
        self._clear_dataset_interactions()
        self.request_outer_view('analysis')
        self._select_graph_page(session, show=False)
        self._restoring_dataset = True
        try:
            self.restore_dataset_controls(session)
            self._plot_cube()
            self._sync_gate_controls()
        finally:
            self._restoring_dataset = False
        self._outer_view_changed(restore_graph=False)
        self._set_dat_details_requested(True)
        self.request_task(1)
        self._refresh_document_titles()
        self._refresh_graph_selector()
        self._workbench_restore_settings()
        self._workbench_render()
        self._refresh_project_modified()

    def _workbench_restore_settings(self):
        self.analysis_workbench.refresh()

    def _workbench_apply_settings(self, values, *, refresh=True):
        """Apply validated named settings directly to the detached science owner."""
        doc = self._workbench_document()
        if doc is None:
            return False
        controls = doc.science.analysis_controls
        changed, retire = False, False
        for field, value in values.items():
            target = doc if field == 'gate_scope' else controls
            if getattr(target, field) != value:
                setattr(target, field, value)
                changed = True
                retire = retire or field in ('fit_xmin', 'fit_xmax', 'fit_model')
        if changed and retire:
            self._workbench_last_summary = None
            doc.retire_fit()
        if changed and refresh:
            self._workbench_changed()
        return changed

    def _workbench_filter_settings(self):
        return self.analysis_workbench.filter_settings()

    def _workbench_effective_d2_window(self):
        doc = self._workbench_document()
        if doc is None:
            return
        controls = doc.science.analysis_controls
        if not controls.derivative_detection:
            return ''
        cube = doc.science.current_cube
        selected = (cube.energy >= doc.science.analysis_controls.fit_xmin) & (cube.energy <= doc.science.analysis_controls.fit_xmax)
        requested = doc.science.analysis_controls.d2_window
        odd = max(3, requested) + 1-max(3, requested) % 2
        windows, skipped, flat = set(), 0, 0
        for gate in self._workbench_gates():
            x, y = cube.energy[selected], cube.z[gate, selected]
            finite = np.isfinite(x) & np.isfinite(y)
            edges = np.flatnonzero(np.diff(np.r_[False, finite, False]))
            for start, end in zip(edges[::2], edges[1::2]):
                count = end-start
                if count < 5:
                    skipped += 1
                elif np.ptp(y[start:end]) == 0:
                    flat += 1
                else:
                    windows.add(min(odd, count if count % 2 else count-1))
        effective = ', '.join(map(str, sorted(windows))) if windows else 'none'
        message = f'D2 requested {requested} → effective {effective} points'
        if len(windows) > 1:
            message += ' (by segment)'
        if skipped or flat:
            message += f'; {skipped} short segments skipped, {flat} flat segments need no SG'
        return message

    def _workbench_gates(self):
        doc = self._workbench_document()
        if doc is None:
            return []
        return list(range(doc.science.current_cube.gate.size)) if doc.gate_scope == 'all' else [self._current_gate_index()]

    def _workbench_job_token(self):
        doc = self._workbench_document()
        return (id(self.workspace), self._document_key(), doc.job_token) if doc and not self.analysis_workbench._closed else None

    def _workbench_find(self):
        from .peak_candidates import collect_peak_candidates
        doc = self._workbench_document()
        if doc is None or not self.analysis_workbench.allowed():
            return
        try:
            self._workbench_finish_editors()
            settings = self._workbench_filter_settings()
            gates = self._workbench_gates()
            cube = doc.science.current_cube
            low, high = doc.science.analysis_controls.fit_xmin, doc.science.analysis_controls.fit_xmax
            if high <= low:
                raise ValueError('Analysis interval maximum must exceed minimum.')
            mask = (cube.energy >= low) & (cube.energy <= high)
            index = np.flatnonzero(mask)
            x = np.asarray(cube.energy[mask]).copy()
            traces = {gate: np.asarray(cube.z[gate, mask]).copy() for gate in gates}
            mode, detector = doc.science.analysis_controls.peak_mode, 'D2' if doc.science.analysis_controls.derivative_detection else 'Original'
            window = doc.science.analysis_controls.d2_window
            token = self._workbench_job_token()
            results = {}
            def work(context):
                for gate, y in traces.items():
                    context.check_cancelled()
                    try:
                        pool = collect_peak_candidates(x, y, mode=mode, detector=detector, sg_window=window)
                        pool = [replace(candidate, index=int(index[candidate.index])) for candidate in pool]
                        outcome = (pool, 'detected' if pool else 'empty', '')
                    except ValueError as exc:
                        outcome = ([], 'failed', str(exc))
                    context.check_cancelled()
                    results[gate] = outcome
                    context.report(f'Finding candidates: {len(results)}/{len(gates)} Gates')
                return results
            def apply(results):
                if token != self._workbench_job_token():
                    return
                for gate, (pool, status, message) in results.items():
                    doc.set_candidates(gate, pool, settings=settings)
                    doc.gate_status[gate] = ('no-seed' if pool and not doc.accepted_seeds(gate) and status != 'failed' else status)
                    doc.gate_messages[gate] = message
                    if message:
                        self.publish_status(message)
                self._workbench_changed()
            self._workbench_start_job(work, apply, 'Finding candidates…', results=results, gates=gates, operation='Find')
        except ValueError as exc:
            self._show_error(str(exc))

    def _workbench_filter(self):
        doc = self._workbench_document()
        if doc is None or not self.analysis_workbench.allowed():
            return
        try:
            self._workbench_finish_editors()
            settings = self._workbench_filter_settings()
            for gate in self._workbench_gates():
                doc.apply_filter(settings, gate=gate)
            self._workbench_changed()
        except ValueError as exc:
            self._show_error(str(exc))

    def _workbench_fit(self):
        from .fitting import fit_multi_peak
        doc = self._workbench_document()
        if doc is None or not self.analysis_workbench.allowed():
            return
        try:
            self._workbench_finish_editors()
            cube = doc.science.current_cube
            low, high = doc.science.analysis_controls.fit_xmin, doc.science.analysis_controls.fit_xmax
            if high <= low:
                raise ValueError('Analysis interval maximum must exceed minimum.')
            model = doc.science.analysis_controls.fit_model
            requests = []
            for gate in self._workbench_gates():
                valid = (cube.energy >= low) & (cube.energy <= high) & np.isfinite(cube.energy) & np.isfinite(cube.z[gate])
                seeds = [seed for seed in doc.accepted_seeds(gate) if low <= seed.position <= high]
                requests.append((gate, cube.energy[valid].copy(), cube.z[gate, valid].copy(), seeds))
            token = self._workbench_job_token()
            results = {}
            def work(context):
                for gate, x, y, seeds in requests:
                    context.check_cancelled()
                    if not seeds:
                        results[gate] = (None, 'no-seed', 'No accepted seeds in the fit interval.')
                        context.report(f'Fitting: {len(results)}/{len(requests)} Gates processed')
                        continue
                    try:
                        centers = np.asarray([seed.position for seed in seeds])
                        span = float(np.ptp(x)) if x.size else 0.
                        step = float(np.median(np.abs(np.diff(x)))) if x.size > 1 else 1e-6
                        baseline = float(np.nanmedian(y)) if y.size else 0.
                        amplitude_scale = float(np.max(np.abs(y-baseline))) if y.size else 0.
                        amplitude_scale = amplitude_scale or abs(baseline) or 1.
                        # A manual shoulder seed can lie at the median height.
                        amplitude_floor = max(float(np.ptp(y))*.1, amplitude_scale*1e-6) if y.size else amplitude_scale*1e-6
                        amplitudes = [seed.amplitude if seed.amplitude is not None else
                            (1 if seed.kind == 'peak' else -1)*max(abs(float(y[np.argmin(np.abs(x-seed.position))])-baseline), amplitude_floor)
                            for seed in seeds]
                        widths = [seed.width if seed.width is not None else max(step*2, span/40) for seed in seeds]
                        gammas = [seed.gamma if seed.gamma is not None else width for seed, width in zip(seeds, widths)]
                        bounds = ([seed.center_min if seed.center_min is not None else low for seed in seeds],
                                  [seed.center_max if seed.center_max is not None else high for seed in seeds])
                        fit = fit_multi_peak(x, y, centers_hint=centers, model=model, mode='Both',
                            kinds=np.asarray([seed.kind == 'peak' for seed in seeds]), initial_amplitudes=amplitudes,
                            initial_widths=widths, initial_gammas=gammas if model == 'Voigt' else None,
                            fixed_centers=[seed.fixed_center for seed in seeds], center_bounds=bounds,
                            check_cancelled=context.check_cancelled)
                        outcome = (fit, 'fitted', '')
                    except (ValueError, RuntimeError) as exc:
                        outcome = (None, 'failed', str(exc))
                    context.check_cancelled()
                    results[gate] = outcome
                    context.report(f'Fitting: {len(results)}/{len(requests)} Gates processed')
                return results
            def apply(results):
                if token != self._workbench_job_token():
                    return
                for gate, (fit, status, message) in results.items():
                    doc.record_fit(gate, fit, status=status, message=message)
                self._workbench_changed()
            self._workbench_start_job(work, apply, 'Fitting accepted seeds…', results=results,
                                      gates=[request[0] for request in requests], operation='Fit')
        except ValueError as exc:
            self._show_error(str(exc))

    def _workbench_finish_editors(self):
        if self._restoring_dataset or self._workbench_syncing:
            return
        self.analysis_workbench.finish_editor()

    def _workbench_sync_preview_owner(self):
        """Retire shared-view gestures as soon as its active owner changes."""
        doc = self._workbench_document()
        active = doc is not None and self._outer_view == 'analysis'
        owner = self._plot_owner_token() if active else None
        context = (self._document_key(), id(doc), doc.input_version, owner) if active else None
        if context != self._workbench_preview_context:
            self._workbench_preview_context = context
            self.workbench_preview.invalidate(owner)
        return owner

    def _workbench_refresh(self):
        self._workbench_sync_preview_owner()
        doc = self._workbench_document()
        if doc is None or not hasattr(self, 'analysis_workbench') or self._workbench_syncing:
            return
        self.analysis_workbench.refresh()
        self._workbench_draw_linecut()
        self._refresh_analysis_context()

    @staticmethod
    def _workbench_seed_values(seed, fields):
        """Validate a complete seed draft without changing canonical rows."""
        from .analysis_document import AnalysisSeed
        updates = {'manual': True}
        for field in ('enabled', 'fixedCenter'):
            if field in fields:
                if fields[field] not in ('true', 'false'):
                    raise ValueError('Seed flags must be boolean.')
                updates['enabled' if field == 'enabled' else 'fixed_center'] = fields[field] == 'true'
        kind = fields.get('kind', seed.kind).strip().lower()
        if kind not in ('peak', 'dip'):
            raise ValueError('Seed kind must be peak or dip.')
        updates['kind'] = kind
        if kind != seed.kind:
            updates['amplitude'] = None
        for field in ('position', 'amplitude', 'width', 'gamma'):
            if field not in fields:
                continue
            text = fields[field].strip()
            value = float(text) if text else None
            if value is None and field == 'position' or value is not None and not np.isfinite(value):
                raise ValueError('Enter a finite seed position or parameter.')
            updates[field] = value
        position = updates.get('position', seed.position)
        if position != seed.position:
            updates.update(center_min=None, center_max=None)
        if 'bound' in fields:
            text = fields['bound'].strip()
            distance = float(text) if text else None
            if distance is not None and (not np.isfinite(distance) or distance <= 0):
                raise ValueError('The center bound must be positive, or blank for the full interval.')
            updates.update(center_min=None if distance is None else position-distance,
                           center_max=None if distance is None else position+distance)
        if seed.source_index is not None and kind != seed.kind:
            replacement = AnalysisSeed(position, kind=kind, manual=True,
                amplitude=updates.get('amplitude'), width=updates.get('width', seed.width),
                gamma=updates.get('gamma', seed.gamma), enabled=updates.get('enabled', True),
                fixed_center=updates.get('fixed_center', seed.fixed_center),
                center_min=updates.get('center_min', seed.center_min), center_max=updates.get('center_max', seed.center_max))
            return [replace(seed, enabled=False, manual=True), replacement]
        return [replace(seed, **updates)]

    def _workbench_add_seed(self, checked=False, *, position=None, kind='peak'):
        from .analysis_document import AnalysisSeed
        doc = self._workbench_document()
        if doc is None or self._project_busy() or not self.analysis_workbench.allowed():
            return
        if position is None:
            self.analysis_workbench.command('add', self.analysis_workbench.ownerToken)
            return
        self._workbench_finish_editors()
        controls = doc.science.analysis_controls
        if not controls.fit_xmin <= float(position) <= controls.fit_xmax:
            raise ValueError('Seed position must be inside the Analysis interval.')
        gate = self._current_gate_index()
        seeds = list(doc.seeds_by_gate.get(gate, []))
        seed = AnalysisSeed(float(position), kind=kind, manual=True)
        seeds.append(seed)
        doc.set_seeds(gate, seeds)
        self.analysis_workbench._selection[self.analysis_workbench.owner_key(), gate] = seed.seed_id
        self._workbench_changed()

    def _workbench_remove_seed(self):
        doc = self._workbench_document()
        if doc is None or self._project_busy() or not self.analysis_workbench.allowed():
            return
        self._workbench_finish_editors()
        gate = self._current_gate_index()
        seed_id = self.analysis_workbench._selection.get((self.analysis_workbench.owner_key(), gate))
        seeds = list(doc.seeds_by_gate.get(gate, []))
        index = next((i for i, seed in enumerate(seeds) if seed.seed_id == seed_id), None)
        if index is not None:
            if seeds[index].source_index is not None:
                seeds[index] = replace(seeds[index], enabled=False, manual=True)
            else:
                seeds.pop(index)
            doc.set_seeds(gate, seeds)
            self._workbench_changed()

    def _workbench_changed(self):
        self._invalidate_analysis_jobs()
        self._invalidate_plot_page(sync=False)
        self._schedule_project_modified()
        self._workbench_refresh()
        self._workbench_render_timer.start(0)

    def _workbench_draw_linecut(self):
        doc = self._workbench_document()
        if doc is None:
            return
        gate = self._current_gate_index()
        cube = doc.science.current_cube
        figure = self.workbench_linecut_figure
        figure.clear()
        ax = figure.add_subplot(111)
        ax.plot(cube.energy, cube.z[gate], lw=1., label='Bound input')
        seeds = doc.accepted_seeds(gate)
        for kind, marker, color in (('peak', '^', '#d62728'), ('dip', 'v', '#2ca02c')):
            positions = [seed.position for seed in seeds if seed.kind == kind]
            if positions:
                amplitudes = [cube.z[gate, np.argmin(np.abs(cube.energy-position))] for position in positions]
                ax.scatter(positions, amplitudes, marker=marker, color=color, s=30, label=kind)
        fit = doc.science.batch_fit_result_map.get(gate)
        if fit is not None:
            ax.plot(fit.x, fit.y_fit, color='#a020f0', lw=1.3, label='Total fit')
            for component in fit.components:
                ax.plot(fit.x, component+fit.baseline, lw=.7, alpha=.6)
        ax.set_title(f'Auxiliary linecut · Gate {cube.gate[gate]:g} · excluded from heatmap export', fontsize=9)
        ax.set_xlabel(cube.energy_label, fontsize=9)
        ax.tick_params(labelsize=8)
        ax.legend(fontsize=7, loc='best')
        self.workbench_linecut_canvas.schedule_render()

    def _workbench_render(self, key=None):
        from .analysis_document import graph_presentation, sync_analysis_format
        from .svg_export import prepare_heatmap_page, render_hybrid_svg
        key = key or self._document_key()
        session = self.workspace.datasets.get(key[0])
        if session is None or key[1] not in session.graphs or session.graphs[key[1]].analysis is None:
            return
        target = self.graph_documents.document_for(key)
        if target is None:
            return
        graph = session.graphs[key[1]]
        sync_analysis_format(session, graph)
        owner = self._plot_owner_token(key)
        self._workbench_sync_preview_owner()
        document = graph.analysis
        try:
            candidate = replace(session, active_graph_id=key[1])
            presentation = graph_presentation(session, graph)
            snapshot = self.snapshot_plot_dataset(candidate)
            presentation, page = prepare_heatmap_page(snapshot, presentation, renderer=render_hybrid_svg)
            target.set_presentation(presentation)
            target.preview.set_geometry(snapshot, presentation)
            accepted = target.commit_rendered_page(page)
            current_owner = self._workbench_sync_preview_owner()
            if (accepted and key == self._document_key()
                    and self._workbench_document() is document
                    and current_owner == page.owner_token == owner):
                self.workbench_preview.set_geometry(snapshot, presentation)
                self.workbench_preview.set_page(page)
        except (ValueError, RuntimeError) as exc:
            target.invalidate(owner, f'Cannot prepare result: {exc}')
            if key == self._document_key():
                self.workbench_preview.invalidate(self._workbench_sync_preview_owner())
                self._workbench_status_text = str(exc)
                self.analysis_workbench.refresh()

    def _workbench_source_graph(self):
        if not self.analysis_workbench.allowed():
            return
        doc = self._workbench_document()
        if doc and doc.source_graph_id in self.workspace.active.graphs:
            self.open_graph_document(self.workspace.active_dataset_id, doc.source_graph_id)

    def _workbench_original_data(self):
        if self._workbench_document() is None:
            return
        self._require_project_idle()
        self._finish_current_document()
        sheet = next((sheet for sheet in self.workspace.sheets.values()
                      if sheet.dataset_id == self.workspace.active_dataset_id and sheet.role == 'original'), None)
        if sheet:
            self.open_workbook(self._sheet_owner(sheet.sheet_id).workbook_id, sheet.sheet_id)

    def _workbench_duplicate(self):
        from .analysis_document import duplicate_analysis_graph
        if self._workbench_document() is None:
            return
        self._require_project_idle()
        self._finish_current_document()
        session = self.workspace.active
        duplicate_analysis_graph(session)
        self._workbench_enter(session)

    def _workbench_provenance(self):
        doc = self._workbench_document()
        science = doc.science
        return dict(dataset_id=self.workspace.active_dataset_id,
            source_graph_id=doc.source_graph_id, source_path=str(science.source_path or ''),
            input_version=doc.input_version, input_mode=doc.source_mode,
            applied_processing=science.applied.recipe(), x_label=science.current_cube.energy_label)

    def _workbench_create_linecut_graph(self):
        from .analysis_graph import fit_curve_sheet, seed_curve_sheet
        from .workbook_state import Workbook
        from .signal_titles import automatic_signal_label, infer_source_signal_kind
        try:
            self._require_project_idle()
            self._workbench_finish_editors()
            doc = self._workbench_document()
            if doc is None:
                return
            cube, gate = doc.science.current_cube, self._current_gate_index()
            fit, seeds = doc.science.batch_fit_result_map.get(gate), doc.accepted_seeds(gate)
            if fit is None and not seeds:
                raise ValueError('Accept seeds or fit the current Gate first.')
            provenance = self._workbench_provenance()
            provenance.update(gate_index=gate, gate=float(cube.gate[gate]), gate_label=cube.gate_label,
                              seeds=[seed.to_dict() for seed in seeds])
            source_kind = cube.signal_source_kind
            if source_kind == 'unknown':
                source_kind = infer_source_signal_kind(cube.source_path or '')
            y_label = automatic_signal_label(source_kind, doc.science.applied.mode == 'energy_second_derivative',
                doc.science.base_cube.signal_kind == 'energy_second_derivative')
            provenance['y_label'] = y_label
            if fit is not None:
                provenance.update(model=fit.model, baseline=float(fit.baseline), rmse=float(fit.rmse),
                                  r_squared=float(fit.r_squared) if np.isfinite(fit.r_squared) else None)
            options = dict(x_label=cube.energy_label, y_label=y_label,
                           note=json.dumps(provenance, ensure_ascii=False, indent=2))
            x, y = cube.energy, cube.z[gate]
            sheet = (fit_curve_sheet(x, y, np.isin(x, fit.x), fit, **options) if fit is not None else
                     seed_curve_sheet(x, y, seeds, **options))
            book = Workbook(name=f'{cube.title} · {cube.gate_label} = {cube.gate[gate]:g}', sheet_ids=[sheet.sheet_id])
            self._publish_analysis_results(book, [sheet], create_graph=True,
                source_label='Frozen input', has_fit=fit is not None)
            self.publish_status('Created current-Gate linecut graph and detached source/results in the project.')
        except ValueError as exc:
            self._show_error(str(exc))

    def _workbench_save_parameters(self, *, snapshot=None, provenance=None, graph_name=None, owner=None, owner_guard=None):
        from .analysis_document import analysis_parameter_rows
        from .workbook_state import Sheet, Workbook
        owner = self.analysis_workbench.ownerToken if owner is None else owner
        allowed = lambda: self.analysis_workbench.allowed(owner) and (owner_guard is None or owner_guard())
        if not allowed():
            return False
        try:
            self._require_project_idle()
            self._workbench_finish_editors()
            if not allowed():
                return False
            doc = self._workbench_document()
            columns, rows, _ = snapshot if snapshot is not None else analysis_parameter_rows(doc)
            provenance = self._workbench_provenance() if provenance is None else provenance
            graph_name = self.workspace.active.active_graph.name if graph_name is None else graph_name
            values = np.full((len(rows), len(columns)), np.nan)
            text_columns = {}
            for column in range(len(columns)):
                if any(isinstance(row[column], str) for row in rows):
                    text_columns[column] = ['' if row[column] is None else str(row[column]) for row in rows]
                else:
                    values[:, column] = [np.nan if row[column] is None else row[column] for row in rows]
            sheet = Sheet(name='Analysis parameters', kind='snapshot', role='snapshot', data=values,
                column_names=columns, text_columns=text_columns, y_columns=[3, 4],
                snapshot_note=json.dumps(provenance, ensure_ascii=False, indent=2))
            book = Workbook(name=f'Analysis parameters — {graph_name}', sheet_ids=[sheet.sheet_id])
            if not allowed():
                return False
            if self._publish_analysis_results(book, [sheet]) is False:
                return False
            self.publish_status('Saved all-Gate analysis parameters to a new project Workbook.')
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            self._show_error(str(exc))
            return False

    def _workbench_export_parameters(self, *, snapshot=None, owner=None, owner_guard=None):
        from .analysis_document import analysis_parameter_rows
        owner = self.analysis_workbench.ownerToken if owner is None else owner
        allowed = lambda: self.analysis_workbench.allowed(owner) and (owner_guard is None or owner_guard())
        if not allowed():
            return False
        try:
            self._require_project_idle()
            self._workbench_finish_editors()
            if not allowed():
                return False
            doc = self._workbench_document()
            columns, rows, _ = deepcopy(snapshot if snapshot is not None else analysis_parameter_rows(doc))
            path, _ = FilePicker.getSaveFileName(self.root_window, 'Export all-Gate analysis parameters',
                str(self._output_folder() / 'analysis_parameters.csv'), 'CSV Files (*.csv)')
            if not path or not allowed():
                return False
            with Path(path).open('w', newline='', encoding='utf-8-sig') as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                writer.writerows(rows)
            self.publish_status(f'Exported all-Gate analysis parameters: {path}')
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            self._show_error(str(exc))
            return False

    def _workbench_confirm_rebind(self, affected_gates):
        host = self.analysis_workbench.open_popup('rebind')
        if host is None:
            return 'cancel'
        host.exec()
        return host.bridge.choice if host.result() == host.Accepted else 'cancel'

    def _workbench_results_table(self):
        if self.analysis_workbench.allowed() and self.analysis_workbench.commitEditors(self.analysis_workbench.ownerToken):
            return self.analysis_workbench.open_popup('results')

    def _workbench_overlay_style(self):
        if self.analysis_workbench.allowed() and self.analysis_workbench.commitEditors(self.analysis_workbench.ownerToken):
            return self.analysis_workbench.open_popup('style')

    def _workbench_rebind_input(self):
        from .analysis_document import analysis_input_changed, rebind_analysis_input, duplicate_analysis_graph
        doc = self._workbench_document()
        if doc is None:
            return
        try:
            self._require_project_idle()
            self._workbench_finish_editors()
            session = self.workspace.active
            source = session.graphs.get(doc.source_graph_id)
            if source is None:
                raise ValueError('The source graph was removed; retain this bound input or create another analysis.')
            mode = self.analysis_workbench.state['inputMode']
            if not analysis_input_changed(session, source_mode=mode, gate_indices=source.gate_indices):
                self._workbench_refresh()
                return
            owner = self.analysis_workbench.ownerToken
            choice = self._workbench_confirm_rebind(doc.science.current_cube.gate.size)
            if not self.analysis_workbench.allowed(owner) or doc.source_graph_id not in session.graphs:
                return
            if choice not in ('replace', 'duplicate'):
                return
            if choice == 'duplicate':
                duplicate_analysis_graph(session)
            rebind_analysis_input(session, source_mode=mode, gate_indices=source.gate_indices)
            self._workbench_last_summary = None
            self._workbench_filter_context = None
            self._workbench_enter(session)
        except ValueError as exc:
            self._show_error(str(exc))
