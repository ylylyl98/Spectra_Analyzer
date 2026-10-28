"""Interactive comparison editor; preview and exports share one renderer."""
from __future__ import annotations

from io import BytesIO
from copy import copy
from dataclasses import replace
from pathlib import Path
import math
from uuid import uuid4
import numpy as np
from matplotlib import rc_context
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Signal, QSignalBlocker
from PySide6.QtGui import QAction
from shiboken6 import isValid

from .file_picker import FilePicker
from .workbook_state import remove_curve, sheet_columns
from .comparison_display import prepare_comparison, comparison_y_label
from .comparison_labels import comparison_legend_labels
from .plot_icons import PLOT_STYLES
from .xy_canvas import XYFigureCanvas, XYNavigationController, XYCommandsBridge, XYCanvasBridge
from .xy_analysis_controller import XYAnalysisController
from .xy_inspector import XYInspectorBridge
from .xy_edit_history import XYEditHistory, capture_edit_state
from .xy_preview import sample_preview_curve
from .svg_export import apply_heatmap_axis_style
from .curve_palettes import palette_colors, read_palette_file
from .curve_symbols import SymbolLine2D, SymbolLegendHandler, apply_curve_style
from .common_prompts import PromptAction, prompt_float, prompt_message
from .curve_prompts import prompt_color


def _update_legend(axes, workspace, comparison):
    previous = axes.get_legend()
    if previous is not None:
        previous.remove()
    drawn = {line.get_gid() for line in axes.lines}
    labels = comparison_legend_labels(workspace, [curve for curve in comparison.curves.values()
                                                 if curve.curve_id in drawn])
    legend_entries = []
    for line in axes.lines:
        if line.get_gid() not in labels:
            continue
        label, automatic = labels[line.get_gid()]
        if automatic or label and not label.startswith('_'):
            legend_entries.append((line, label, automatic))
    if comparison.legend_visible and legend_entries:
        presentation = comparison.presentation
        options = {}
        if presentation is not None:
            rows = max(1, int((presentation.height_in - .9) * 72 / (presentation.numeric_pt * 1.45)))
            options = dict(frameon=False, prop=dict(family='Arial', size=presentation.numeric_pt),
                           handlelength=1.6, borderaxespad=.4, loc='upper right',
                           ncols=max(1, math.ceil(len(legend_entries) / rows)))
        legend = axes.legend(handles=[line for line, _label, _auto in legend_entries],
                             labels=[label for _line, label, _auto in legend_entries],
                             handler_map={SymbolLine2D: SymbolLegendHandler()}, **options)
        legend._comparison_curve_ids = [line.get_gid() for line, _label, _auto in legend_entries]
        for (_line, _label, automatic), text in zip(legend_entries, legend.get_texts()):
            if automatic:
                text.set_parse_math(False)


def _format_comparison(figure, axes, comparison):
    """Measure one physical page; the preview scales the entire page uniformly."""
    presentation = comparison.presentation
    axes._comparison_layout_error = ''
    if presentation is None:
        axes.grid(True, alpha=.2)
        figure.set_layout_engine('tight')
        return
    figure.set_layout_engine(None)
    display_dpi = figure.dpi
    figure.set_size_inches(presentation.width_in, presentation.height_in, forward=False)
    # A common measurement DPI keeps normalized frame geometry independent of
    # window size and of the requested PNG resolution.
    figure.set_dpi(72)
    apply_heatmap_axis_style(axes, presentation)
    renderer = figure._get_renderer()
    width, height = figure.get_size_inches()
    axes.set_position((.18, .15, .78, .80))
    for _ in range(8):
        frame = axes.get_window_extent(renderer)
        decorations = axes.get_tightbbox(renderer)
        left = max(0., frame.x0 - decorations.x0) / 72 + .07
        right = max(0., decorations.x1 - frame.x1) / 72 + .07
        bottom = max(0., frame.y0 - decorations.y0) / 72 + .07
        top = max(0., decorations.y1 - frame.y1) / 72 + .07
        available_width, available_height = max(.2, width-left-right), max(.2, height-bottom-top)
        frame_width = min(presentation.frame_width_in or available_width, available_width)
        frame_height = min(presentation.frame_height_in or available_height, available_height)
        bounds = ((left+(available_width-frame_width)/2)/width,
                  (bottom+(available_height-frame_height)/2)/height,
                  frame_width/width, frame_height/height)
        previous = axes.get_position().bounds
        axes.set_position(bounds)
        if max(abs(a-b) for a, b in zip(bounds, previous)) < .0001:
            break
    decorations = axes.get_tightbbox(renderer)
    fits = (decorations.x0 >= -.5 and decorations.y0 >= -.5
            and decorations.x1 <= width*72+.5 and decorations.y1 <= height*72+.5)
    figure.set_dpi(display_dpi)
    if not fits:
        axes._comparison_layout_error = ('Figure is too small for these font sizes and legend. '
            'Increase Width or Height, or reduce the tick/legend and axis/title sizes.')


def draw_comparison(figure, workspace, comparison, *, preview=False):
    """Render full resolution by default; only the interactive preview samples."""
    return _draw_comparison_display(figure, workspace, comparison,
                                    prepare_comparison(workspace, comparison), preview=preview)


def _draw_comparison_display(figure, workspace, comparison, display, *, preview):
    figure.clear()
    axes = figure.add_subplot(111)
    for item in display.curves:
        curve = item.curve
        x, y = sample_preview_curve(item.x, item.y) if preview else (item.x, item.y)
        line = SymbolLine2D(x, y, gid=curve.curve_id)
        apply_curve_style(line, curve)
        axes.add_line(line)
    axes.autoscale_view()
    axes.set(title=comparison.plot_title, xlabel=comparison.x_label, ylabel=display.y_label)
    if display.x_limits is not None:
        axes.set_xlim(display.x_limits)
    if not display.curves:
        axes.text(.5, .5, 'No common X range' if comparison.x_range_mode == 'common'
                  else 'No visible samples', ha='center', va='center', transform=axes.transAxes)
    axes._comparison_display = display
    _update_legend(axes, workspace, comparison)
    _format_comparison(figure, axes, comparison)
    if axes._comparison_layout_error and not preview:
        raise ValueError(axes._comparison_layout_error)
    return axes


class XYPlotController(QObject):
    changed = Signal()
    historyChanged = Signal()
    analysisRequested = Signal()
    sourceRequested = Signal(str)
    addDataRequested = Signal()
    importDataRequested = Signal()
    inspectorVisibilityRequested = Signal(bool)

    inspectorSectionRequested = Signal(int, str)
    canvasFocusRequested = Signal()
    inputRetired = Signal()

    def __init__(self, workspace, comparison_id, parent=None, *, services=None):
        super().__init__(parent)
        self.workspace, self.comparison_id = workspace, comparison_id
        if not hasattr(self.comparison, '_edit_history'):
            self.comparison._edit_history = XYEditHistory()
        self._edit_history = self.comparison._edit_history
        self._syncing = self._appearance_edit = self._disposed = False
        self._active_curve_id = None
        self._selection_ids = set()
        self._preview_styles = []
        self._input_guard = None
        self._canvas_actions = {}
        self._scene_generation = 0
        self._scene = None
        self._scene_state = dict(attached=False, visible=False, enabled=False)
        self.style_actions = {}
        command_actions = {}
        for style, label in PLOT_STYLES:
            action = QAction(label, self)
            action.setToolTip(f'Apply {label} to selected curves in this graph')
            action.triggered.connect(lambda _checked=False, value=style: self._set_selected_style(value))
            self.style_actions[style] = action
            command_actions['style:' + style] = action
        self.analyze_action = QAction('Analyze', self)
        self.analyze_action.setToolTip('Analyze exactly one selected source curve in this graph')
        self.analyze_action.triggered.connect(self.analysisRequested)
        self.add_sheets_action = QAction('Existing Sheets…', self)
        self.add_files_action = QAction('From files…', self)
        self.add_sheets_action.triggered.connect(self.addDataRequested)
        self.add_files_action.triggered.connect(self.importDataRequested)
        self.add_data_action = QAction('Add data…', self)
        self.add_data_action.triggered.connect(self.addDataRequested)
        self.inspector_action = QAction('Inspector', self)
        self.inspector_action.setCheckable(True)
        self.inspector_action.setChecked(True)
        self.inspector_action.triggered.connect(self._set_inspector_visible)
        self.resume_analysis_action = QAction('Resume saved analysis', self)
        self.resume_analysis_action.setEnabled(False)
        command_actions.update(analyze=self.analyze_action, resume=self.resume_analysis_action,
            add=self.add_data_action, addSheets=self.add_sheets_action,
            addFiles=self.add_files_action, inspector=self.inspector_action)
        self.figure = Figure(figsize=(7, 5), dpi=100)
        self.canvas = XYFigureCanvas(self.figure)
        self.canvas._render_timer.setParent(self)
        self.toolbar = XYNavigationController(self.canvas)
        self.command_bridge = XYCommandsBridge(self, self.toolbar, command_actions)
        self.navigation_bridge = self.command_bridge
        self.canvas_bridge = XYCanvasBridge(self.canvas, self, self)
        self.inspector_bridge = XYInspectorBridge(self)
        self.analysis_controller = XYAnalysisController(self, services=services)
        self.resume_analysis_action.triggered.connect(self.analysis_controller.resume_analysis)
        self._curve_click_connection = self.canvas.mpl_connect('button_press_event', self._plot_clicked)
        canvas, commands, bridge = self.canvas, self.command_bridge, self.canvas_bridge
        self.destroyed.connect(lambda: _retire_destroyed_controller(canvas, commands, bridge))
        self.refresh()

    def presentation_eligible(self):
        return (not self._disposed and self._scene_state['attached']
                and self._scene_state['visible'] and self._scene_state['enabled'])

    def prompt_window(self):
        if self._scene is None:
            raise ValueError('An attached XY presentation window is required.')
        return self._scene.prompt_window()

    def retire_input(self):
        self.command_bridge.retire()
        self.inspector_bridge.suspendBlur()
        self.analysis_controller.bridge.retire_editors()
        self.inputRetired.emit()

    def attach_scene(self, scene):
        if self._disposed:
            raise RuntimeError('The XY controller has been disposed.')
        if self._scene is not None:
            self._scene.detach()
        self._scene_generation += 1
        self._scene = scene
        self.retire_input()
        return self._scene_generation

    def update_scene(self, generation, *, attached, visible, enabled, viewport_size, device_pixel_ratio):
        if self._disposed or generation != self._scene_generation or self._scene is None:
            return False
        previous = self.presentation_eligible()
        self._scene_state = dict(attached=bool(attached), visible=bool(visible), enabled=bool(enabled))
        current = self.presentation_eligible()
        if previous and not current:
            self.retire_input()
        self.canvas_bridge.set_visible(current)
        if attached:
            self.canvas_bridge.set_device_pixel_ratio(device_pixel_ratio)
            self.canvas_bridge.setViewport(*viewport_size)
        self.refresh_input_state()
        self.inspector_bridge.changed.emit()
        return True

    def detach_scene(self, generation):
        if self._disposed or generation != self._scene_generation:
            return
        self._scene = None
        self._scene_generation += 1
        self._scene_state = dict(attached=False, visible=False, enabled=False)
        self.canvas_bridge.set_visible(False)
        self.retire_input()

    @property
    def comparison(self):
        return self.workspace.comparisons[self.comparison_id]

    def set_input_guard(self, guard):
        self._input_guard = guard
        self.refresh_input_state()

    def refresh_input_state(self):
        self.command_bridge.refresh()

    def set_add_data_enabled(self, enabled):
        for action in (self.add_data_action, self.add_sheets_action, self.add_files_action):
            action.setEnabled(enabled)

    def set_inspector_checked(self, checked):
        with QSignalBlocker(self.inspector_action):
            self.inspector_action.setChecked(checked)
        self.command_bridge.stateChanged.emit()

    def _selected_ids(self):
        return set(self._selection_ids)

    def sync_graph_name(self):
        self.inspector_bridge._drafts.pop('name', None)
        self.inspector_bridge.sync()

    def set_curve_selection(self, ids, current_id=None, *, preflight=True):
        """Identity-based selection used by Quick rows, plot picking, and analysis."""
        if preflight and not self._finish_curve_action():
            return False
        ids = set(ids) & self.comparison.curves.keys()
        if current_id is not None and current_id not in self.comparison.curves:
            return False
        self._selection_ids = ids
        self._active_curve_id = current_id
        self.inspector_bridge.sync()
        self._selection_changed()
        return True

    def _selection_changed(self, *, sync_analysis=True):
        count = len(self._selection_ids)
        self.command_bridge.stateChanged.emit()
        for action in self.style_actions.values():
            action.setEnabled(count > 0)
        self.inspector_bridge.sync()
        if sync_analysis:
            self.analysis_controller.sync()
        if hasattr(self, 'axes'):
            self._apply_selection_highlight()

    def revert_editor(self):
        self.inspector_bridge._drafts.clear()
        self.inspector_bridge.set_message('')
        self.inspector_bridge.sync()
        self._emit_changed(appearance_only=True)

    def refresh(self, *, appearance_only=False):
        curves = self.comparison.curves
        previous = self._active_curve_id
        self._selection_ids.intersection_update(curves)
        if previous not in curves:
            old_order = self.inspector_bridge._model._ids
            old_row = old_order.index(previous) if previous in old_order else 0
            order = list(curves)
            self._active_curve_id = order[min(old_row, len(order)-1)] if order else None
            if self._active_curve_id:
                self._selection_ids.add(self._active_curve_id)
            self.inspector_bridge._drafts = {k:v for k,v in self.inspector_bridge._drafts.items()
                if k not in ('label', 'color', 'width', 'y_offset') and k not in self.inspector_bridge._symbol_fields}
        self.inspector_bridge.sync()
        self._selection_changed(sync_analysis=not appearance_only)
        self.render(appearance_only=appearance_only)

    def refresh_sources(self):
        if self._active_curve_id not in self.comparison.curves:
            self.refresh()
            return
        self._selection_ids.intersection_update(self.comparison.curves)
        self.inspector_bridge.sync()
        self._selection_changed()

    def _update_display_status(self, error=''):
        display = self.axes._comparison_display
        self._display_status = '\n'.join(([error] if error else []) + [display.description, *display.notices])
        self.inspector_bridge.changed.emit()

    def _draft_error(self):
        return self.inspector_bridge.validation_error()

    def has_pending_project_edits(self):
        return bool(self.inspector_bridge._drafts)

    def finish_editor(self):
        self._commit_editor(include_symbols=True)

    def _commit_editor(self, *, include_symbols=False):
        bridge = self.inspector_bridge
        if not bridge.is_live() or not bridge._drafts:
            return
        self.validate_editor()
        v = bridge.parsed_values()
        before = self._capture_edit()
        c, curve = self.comparison, self._selected()
        data_changed = (c.normalization, c.stack_step, c.x_range_mode, c.x_range) != (
            v['normalization'], v['stack_step'], v['x_range_mode'], v['x_range'])
        appearance_before = (c.plot_title, c.x_label, c.y_label, c.legend_visible, c.presentation,
            (curve.label, curve.color, curve.width) if curve else None)
        c.name, c.plot_title, c.x_label, c.y_label = v['name'].strip(), v['plot_title'], v['x_label'], v['y_label']
        c.legend_visible, c.presentation = v['legend_visible'], v['presentation']
        c.normalization, c.stack_step, c.x_range_mode, c.x_range = v['normalization'], v['stack_step'], v['x_range_mode'], v['x_range']
        if curve is not None:
            data_changed = data_changed or curve.y_offset != v['y_offset']
            curve.label, curve.color, curve.width, curve.y_offset = v['label'], v['color'].strip(), v['width'], v['y_offset']
        symbols_changed = False
        if include_symbols and bridge.symbolPending:
            ids = self._selection_ids if bridge._symbol_target == 'selected' else {self._active_curve_id}
            for cid in ids:
                if cid in c.curves:
                    for field in bridge._symbol_fields:
                        symbols_changed = symbols_changed or getattr(c.curves[cid], field) != v[field]
                        setattr(c.curves[cid], field, v[field])
        bridge._drafts = {} if include_symbols else {k:value for k,value in bridge._drafts.items() if k in bridge._symbol_fields}
        appearance_changed = appearance_before != (c.plot_title, c.x_label, c.y_label, c.legend_visible, c.presentation,
            (curve.label, curve.color, curve.width) if curve else None)
        self._record_edit('Graph and curve settings', before)
        bridge.set_message('')
        bridge.sync()
        if data_changed or appearance_changed or symbols_changed:
            self.render(appearance_only=not data_changed)
        self._emit_changed(appearance_only=not data_changed)

    def _format_draft_error(self, values):
        c = self.comparison
        p = values['presentation']
        text = (values['plot_title'], values['x_label'], values['y_label'], values['legend_visible'])
        if p is None or (p == c.presentation and text == (c.plot_title, c.x_label, c.y_label, c.legend_visible)):
            return ''
        key = (p, text, tuple((curve.curve_id, curve.visible, curve.label) for curve in c.curves.values()))
        if getattr(self, '_format_draft_key', None) != key:
            candidate = replace(c, presentation=p, plot_title=text[0], x_label=text[1], y_label=text[2], legend_visible=text[3])
            display = replace(self.axes._comparison_display, y_label=comparison_y_label(candidate))
            axes = _draw_comparison_display(Figure(dpi=72), self.workspace, candidate, display, preview=True)
            self._format_draft_message = axes._comparison_layout_error
            self._format_draft_key = key
        return self._format_draft_message

    def _reset_display(self):
        before = self._capture_edit()
        for field in ('normalization', 'stack_step', 'x_range_mode', 'x_min', 'x_max', 'y_offset'):
            self.inspector_bridge._drafts.pop(field, None)
        c = self.comparison
        c.normalization, c.stack_step, c.x_range_mode, c.x_range = 'none', 0., 'all', None
        for curve in c.curves.values():
            curve.y_offset = 0.
        self._record_edit('Reset comparison display', before)
        self.refresh()
        self.changed.emit()

    def _select_curve_rows(self, selected):
        return self.set_curve_selection(set(self.comparison.curves) if selected else set(), self._active_curve_id)

    def set_curve_visibility(self, cid, visible):
        curve = self.comparison.curves.get(cid)
        if curve is None or not self._finish_curve_action():
            self.inspector_bridge.sync()
            return False
        if curve.visible != visible:
            before = self._capture_edit()
            curve.visible = visible
            self._record_edit('Curve visibility', before)
            self.refresh()
            self.changed.emit()
        return True

    def _choose_color(self):
        if not self._finish_curve_action():
            return
        curve = self._selected()
        if curve is not None:
            ids = {curve.curve_id}
            owner = self._capture_modal_action(ids)
            if owner is None:
                return
            color, accepted = prompt_color(self.prompt_window(), 'Curve color', curve.color)
            if accepted and self._modal_action_current(owner):
                self._apply_batch_style(ids, 'color', color)

    def import_palette(self, path):
        if not self._finish_curve_action():
            return False
        try:
            colors = read_palette_file(path)
        except (ValueError, OSError, UnicodeError) as exc:
            self.inspector_bridge.set_message(f'Cannot import color list: {exc}')
            return False
        key = 'imported:' + uuid4().hex
        bridge = self.inspector_bridge
        bridge._imported_palettes[key] = colors
        bridge._imported_names[key] = Path(path).name
        bridge._palette = key
        bridge.set_message(f'Imported {len(colors)} colors. Apply to selected curves to save their colors.')
        return True

    def _apply_palette(self):
        ids = self._selected_ids()
        if not ids or not self._finish_curve_action():
            return
        bridge = self.inspector_bridge
        curves = [c for c in self.comparison.curves.values() if c.curve_id in ids]
        imported = bridge._imported_palettes.get(bridge._palette)
        colors = ([imported[i % len(imported)] for i in range(len(curves))] if imported is not None
                  else palette_colors(bridge._palette, len(curves)))
        before = self._capture_edit()
        if any(c.color != color for c, color in zip(curves, colors)):
            for curve, color in zip(curves, colors):
                curve.color = color
            self._record_edit('Selected curve color list', before)
            self.refresh(appearance_only=True)
            self._emit_changed(appearance_only=True)

    def _apply_symbols(self):
        bridge = self.inspector_bridge
        if not bridge.symbolPending:
            for key in bridge._symbol_fields:
                bridge._drafts[key] = bridge.values_map()[key]
        self._finish_curve_action()

    def _inspector_section_changed(self, index):
        if index == 1:
            self.analysis_controller.activate(restore_selection=False)

    def _selected(self):
        return self.comparison.curves.get(self._active_curve_id)

    def _emit_changed(self, *, appearance_only=False):
        self._emit_notification(self.changed, appearance_only=appearance_only)

    def _emit_notification(self, signal, *, appearance_only=False):
        previous = self._appearance_edit
        self._appearance_edit = previous or appearance_only
        try:
            signal.emit()
        finally:
            self._appearance_edit = previous

    def _draft_changed(self, *_args):
        if not self._syncing:
            # Draft text has not changed the scientific input. Controllers may
            # update dirty state without asking analysis to capture it again.
            self._emit_changed(appearance_only=True)

    def _capture_edit(self):
        return capture_edit_state(self.comparison), (self._active_curve_id, tuple(self._selected_ids()))

    def _record_edit(self, label, before, *, retained_analysis=None):
        after = self._capture_edit()
        if self._edit_history.record(label, before[0], after[0], before_selection=before[1],
                                     after_selection=after[1], retained_analysis=retained_analysis):
            self._emit_notification(self.historyChanged,
                                    appearance_only=self._edit_history.undo_stack[-1].appearance_only)

    def can_undo(self):
        return self._edit_history.available(self.workspace, self.comparison)

    def can_redo(self):
        return self._edit_history.available(self.workspace, self.comparison, redo=True)

    def undo(self):
        return self._history_step()

    def redo(self):
        return self._history_step(redo=True)

    def _history_step(self, *, redo=False):
        if not self._finish_curve_action():
            return False
        history = self._edit_history
        if not history.available(self.workspace, self.comparison, redo=redo):
            self._emit_notification(self.historyChanged, appearance_only=True)
            return False
        source, destination = (history.redo_stack, history.undo_stack) if redo else (history.undo_stack, history.redo_stack)
        command = source.pop()
        retained = command.retained_analysis
        restore_analysis = (not redo and retained is not None and retained[2] is not None
                            and self.comparison.analysis_state is retained[2])
        redo_retained_analysis = (redo and retained is not None
                                  and self.comparison.analysis_state is retained[1])
        command.apply(self.comparison, redo=redo)
        destination.append(command)
        self.inspector_bridge._drafts.clear()
        if command.structural:
            active_id, selected_ids = command.after_selection if redo else command.before_selection
            self._active_curve_id = active_id
            self._selection_ids = set(selected_ids)
        self.refresh(appearance_only=command.appearance_only)
        if redo and retained is not None:
            command.retained_analysis = (retained[0], retained[1],
                                        self.comparison.analysis_state if redo_retained_analysis else None)
        if restore_analysis:
            self._restore_removed_analysis(retained)
        self.inspector_bridge.set_message('')
        self._emit_notification(self.historyChanged, appearance_only=command.appearance_only)
        self._emit_changed(appearance_only=command.appearance_only)
        return True

    def _restore_removed_analysis(self, retained):
        progress, payload, _removal_payload = retained
        if progress is None:
            return
        # Recheck source bytes, mapping and scientific interval. A deleted or
        # changed source must never acquire a fit from an earlier document state.
        from .xy_analysis import capture_input
        try:
            current = capture_input(self.workspace, self.comparison, progress.captured.curve_id,
                                    progress.captured.settings)
        except (ValueError, KeyError, IndexError):
            return
        if current.key != progress.captured.key:
            return
        self.comparison.analysis_state = payload
        panel = self.analysis_controller
        if panel._activated:
            panel.progress = copy(progress)
            panel._publish_progress()
            panel.sync()
            self.render()

    def _source_text(self, curve):
        sheet = self.workspace.sheets.get(curve.sheet_id)
        book = next((book for book in self.workspace.workbooks.values() if curve.sheet_id in book.sheet_ids), None)
        if sheet is None or book is None:
            return 'Source Sheet unavailable', 'Source Sheet unavailable'
        columns = sheet_columns(self.workspace, sheet, curve.mode)
        source = f'{book.name} / {sheet.name} / {columns[curve.x_column]} → {columns[curve.y_column]} ({curve.mode})'
        detail = (f'Workbook: {book.name}\nSheet: {sheet.name}\n'
                  f'X: {columns[curve.x_column]} · Y: {columns[curve.y_column]} · {curve.mode}\n'
                  f'{sheet.source_path or "Embedded values"}')
        return source, detail

    def render(self, *, appearance_only=False):
        if appearance_only and hasattr(self, 'axes'):
            # Restore preview emphasis before recording the new ordinary styles.
            for _cid, artist, style in self._preview_styles:
                artist.set(**style)
            for line in self.axes.lines:
                curve = self.comparison.curves.get(line.get_gid())
                if curve is not None:
                    apply_curve_style(line, curve)
                elif line.get_gid() == 'xy-analysis-peaks' and self.analysis_controller.captured is not None:
                    curve = self.comparison.curves.get(self.analysis_controller.captured.curve_id)
                    if curve is not None:
                        line.set_color(curve.color)
            display = self.axes._comparison_display
            display.y_label = comparison_y_label(self.comparison)
            self.axes.set(title=self.comparison.plot_title, xlabel=self.comparison.x_label, ylabel=display.y_label)
            _update_legend(self.axes, self.workspace, self.comparison)
            _format_comparison(self.figure, self.axes, self.comparison)
        else:
            self.axes = draw_comparison(self.figure, self.workspace, self.comparison, preview=True)
            self.analysis_controller.draw_overlays(self.axes)
            self.toolbar.update()
        self.canvas_bridge.fit_page()
        self._capture_preview_styles()
        self._apply_selection_highlight()
        self._update_display_status(self._draft_error() or self.axes._comparison_layout_error)
        self.canvas.draw_idle()

    def _capture_preview_styles(self):
        """Keep original artist properties local to this preview, including its legend."""
        lines = [line for line in self.axes.lines if line.get_gid() in self.comparison.curves]
        self._preview_styles = []
        for line in lines:
            self._preview_styles.append((line.get_gid(), line, dict(alpha=line.get_alpha(),
                linewidth=line.get_linewidth(), markersize=line.get_markersize(), zorder=line.get_zorder())))
        if self.analysis_controller.captured is not None:
            for line in self.axes.lines:
                if line.get_gid() in ('xy-analysis-peaks', 'xy-analysis-fit'):
                    self._preview_styles.append((self.analysis_controller.captured.curve_id, line,
                        dict(alpha=line.get_alpha())))
        legend = self.axes.get_legend()
        if legend is not None:
            for curve_id, handle, text in zip(legend._comparison_curve_ids,
                                              legend.legend_handles, legend.get_texts()):
                self._preview_styles.append((curve_id, handle, dict(alpha=handle.get_alpha(),
                    linewidth=handle.get_linewidth(), markersize=handle.get_markersize(), zorder=handle.get_zorder())))
                self._preview_styles.append((curve_id, text,
                    dict(alpha=text.get_alpha(), fontweight=text.get_fontweight())))

    def _apply_selection_highlight(self, *_args):
        visible = {curve_id for curve_id, _artist, _style in self._preview_styles
                   if curve_id in self.comparison.curves and self.comparison.curves[curve_id].visible}
        selected = self._selected_ids() & visible if self.inspector_bridge._highlight else set()
        top = max((style.get('zorder', 0) for _cid, _artist, style in self._preview_styles), default=2) + 1
        for curve_id, artist, original in self._preview_styles:
            style = dict(original)
            if selected:
                if curve_id in selected:
                    if 'linewidth' in style:
                        style.update(linewidth=style['linewidth'] * 1.5,
                                     markersize=style['markersize'] * 1.25, zorder=top)
                    elif 'fontweight' in style:
                        style['fontweight'] = 'bold'
                else:
                    style['alpha'] = .25 * (1. if style['alpha'] is None else style['alpha'])
            artist.set(**style)
        self.canvas.draw_idle()

    def _finish_curve_action(self):
        """Preflight before selection, ordering or an explicit batch operation."""
        try:
            if self.has_pending_project_edits():
                self.finish_editor()
        except ValueError as exc:
            self.inspector_bridge.set_message(str(exc))
            self._update_display_status(str(exc))
            return False
        return True

    def _capture_modal_action(self, ids):
        """Bind one modal operation to the existing command owner and curve objects."""
        if not self.command_bridge.allowed():
            return None
        comparison = self.comparison
        captured = (self.command_bridge.ownerToken, comparison,
                    tuple((cid, comparison.curves[cid]) for cid in ids),
                    self._active_curve_id, frozenset(self._selected_ids()))
        return captured if self._modal_action_current(captured) else None

    def _modal_action_current(self, captured):
        # Check QObject liveness before touching QWidget properties after a nested loop.
        if not isValid(self) or self._disposed:
            return False
        token, comparison, curves, active, selected = captured
        return (self.command_bridge.authorize(token) and self.comparison is comparison
                and self._active_curve_id == active and self._selected_ids() == selected
                and all(comparison.curves.get(cid) is curve for cid, curve in curves))

    def _set_selected_style(self, style):
        ids = self._selected_ids()
        if ids and self._finish_curve_action():
            self._apply_batch_style(ids, 'style', style)

    def _import_palette_dialog(self):
        if not self._finish_curve_action():
            return
        owner = self._capture_modal_action(self.comparison.curves)
        if owner is None:
            return
        path, _filter = FilePicker.getOpenFileName(self.prompt_window(), 'Import curve color list', '',
                                                   'Color lists (*.txt *.csv);;All files (*)')
        if path and self._modal_action_current(owner):
            self.import_palette(path)

    def _batch_color(self):
        if not self._finish_curve_action():
            return
        ids = self._selected_ids()
        if not ids:
            return
        owner = self._capture_modal_action(ids)
        if owner is None:
            return
        initial = next(curve.color for curve in self.comparison.curves.values() if curve.curve_id in ids)
        color, accepted = prompt_color(self.prompt_window(), f'Color for {len(ids)} selected curves', initial)
        if accepted and self._modal_action_current(owner):
            self._apply_batch_style(ids, 'color', color)

    def _batch_width(self):
        if not self._finish_curve_action():
            return
        ids = self._selected_ids()
        if not ids:
            return
        owner = self._capture_modal_action(ids)
        if owner is None:
            return
        initial = next(curve.width for curve in self.comparison.curves.values() if curve.curve_id in ids)
        width, accepted = prompt_float(self.prompt_window(), 'Selected curve width',
            f'Line width for {len(ids)} selected curves:', value=initial,
            minimum=min(.000001, initial), maximum=max(50., initial), decimals=6)
        if accepted and self._modal_action_current(owner):
            self._apply_batch_style(ids, 'width', width)

    def _apply_batch_style(self, ids, field, value):
        before = self._capture_edit()
        changed = False
        for curve_id in ids:
            curve = self.comparison.curves[curve_id]
            if getattr(curve, field) != value:
                setattr(curve, field, value)
                changed = True
        if changed:
            self._record_edit('Selected curve ' + field, before)
            self.refresh(appearance_only=True)
            self._emit_changed(appearance_only=True)

    def _batch_visibility(self, mode):
        ids = self._selected_ids()
        if (not ids and mode != 'all') or not self._finish_curve_action():
            return
        before = self._capture_edit()
        changed = False
        for curve in self.comparison.curves.values():
            if mode in ('only', 'all') or curve.curve_id in ids:
                visible = mode == 'all' or mode == 'show' or mode == 'only' and curve.curve_id in ids
                changed = changed or curve.visible != visible
                curve.visible = visible
        if changed:
            self._record_edit('Curve visibility', before)
            self.refresh()
            self.changed.emit()

    def _move_curves(self, ids, destination):
        if not self._finish_curve_action():
            return False
        order = list(self.comparison.curves)
        selected = set(ids)
        moving = [curve_id for curve_id in order if curve_id in selected]
        remaining = [curve_id for curve_id in order if curve_id not in selected]
        destination = max(0, min(destination, len(order)))
        insertion = sum(curve_id not in selected for curve_id in order[:destination])
        return self._set_curve_order(remaining[:insertion] + moving + remaining[insertion:])

    def _set_curve_order(self, order):
        curves = self.comparison.curves
        if order != list(curves):
            before = self._capture_edit()
            # The existing project format writes curves.values() in this order.
            self.comparison.curves = {curve_id: curves[curve_id] for curve_id in order}
            self._record_edit('Curve order', before)
            self.refresh()
            self.changed.emit()
        return True

    def _move_selected(self, direction):
        if not self._finish_curve_action():
            return
        selected, order = self._selected_ids(), list(self.comparison.curves)
        indices = range(1, len(order)) if direction < 0 else range(len(order) - 2, -1, -1)
        for index in indices:
            neighbor = index + direction
            if order[index] in selected and order[neighbor] not in selected:
                order[index], order[neighbor] = order[neighbor], order[index]
        self._set_curve_order(order)

    def _plot_clicked(self, event):
        if event.button != 1 or self.toolbar.mode or event.inaxes not in (None, self.axes):
            return
        if event.inaxes is None:
            if self._finish_curve_action():
                self._select_curve_rows(False)
            return
        # Measure distance to the displayed polyline, including transformed
        # normalization/stack offsets. NaN gaps never create a connecting segment.
        point = np.array([event.x, event.y])
        candidates = []
        tolerance = self.figure.dpi * 5 / 72
        for ordinal, line in enumerate(self.axes.lines):
            if line.get_gid() not in self.comparison.curves or not line.get_visible():
                continue
            points = line.get_transform().transform(line.get_xydata())
            finite = np.isfinite(points).all(axis=1)
            if not finite.any():
                continue
            distance = np.min(np.sum((points[finite] - point) ** 2, axis=1))
            valid = finite[:-1] & finite[1:]
            if line.get_linestyle() != 'None' and valid.any():
                start, end = points[:-1][valid], points[1:][valid]
                delta = end - start
                denominator = np.sum(delta * delta, axis=1)
                fraction = np.divide(np.sum((point - start) * delta, axis=1), denominator,
                                     out=np.zeros_like(denominator), where=denominator != 0)
                closest = start + np.clip(fraction, 0, 1)[:, None] * delta
                distance = min(distance, np.min(np.sum((closest - point) ** 2, axis=1)))
            if distance <= tolerance ** 2:
                candidates.append((distance, ordinal, line.get_gid()))
        if not self._finish_curve_action():
            return
        self.canvasFocusRequested.emit()
        if not candidates:
            self._select_curve_rows(False)
            return
        curve_id = min(candidates)[2]
        control = 'control' in (event.key or '') or 'ctrl' in (event.key or '')
        if self.set_curve_selection((self._selected_ids() | {curve_id}) if control else {curve_id}, curve_id):
            self.inspector_bridge.reveal_curve(curve_id)

    def _remove_selected(self):
        curve = self._selected()
        if curve and self._finish_curve_action():
            before = self._capture_edit()
            progress = self.analysis_controller.progress
            retained = ((copy(progress), self.comparison.analysis_state)
                        if progress is not None and progress.captured.curve_id == curve.curve_id else None)
            remove_curve(self.workspace, self.comparison_id, curve.curve_id)
            self.refresh()
            if retained is not None:
                retained = (*retained, self.comparison.analysis_state)
            self._record_edit('Remove curve', before, retained_analysis=retained)
            self.changed.emit()

    def _open_source(self):
        curve = self._selected()
        if curve:
            self.sourceRequested.emit(curve.sheet_id)

    def svg_bytes(self):
        self.finish_editor()
        # Copy and Export produce identical bytes for an unchanged graph.
        with rc_context({'svg.hashsalt': 'spectra-xy-page-v1', 'svg.fonttype': 'none',
                         'font.family': 'Arial'}):
            figure = Figure(figsize=(7, 5), dpi=72)
            draw_comparison(figure, self.workspace, self.comparison)
            output = BytesIO()
            figure.savefig(output, format='svg', transparent=True, facecolor='none',
                           edgecolor='none', metadata={'Date': None})
        return output.getvalue()

    def export(self, path, *, overwrite=False):
        path = Path(path)
        extension = path.suffix.lower().lstrip('.')
        if extension not in ('svg', 'png'):
            raise ValueError('Comparison exports support SVG or PNG.')
        if extension == 'svg':
            data = self.svg_bytes()
        else:
            self.finish_editor()
            figure = Figure(figsize=(7, 5), dpi=72)
            draw_comparison(figure, self.workspace, self.comparison)
            output = BytesIO()
            dpi = self.comparison.presentation.heatmap_dpi if self.comparison.presentation is not None else 150
            figure.savefig(output, format=extension, dpi=dpi)
            data = output.getvalue()
        with path.open('wb' if overwrite else 'xb') as handle:
            handle.write(data)
        return path

    def _export_dialog(self, extension):
        if not self._finish_curve_action():
            return
        owner = self._capture_modal_action(self.comparison.curves)
        if owner is None:
            return
        path, _ = FilePicker.getSaveFileName(self.prompt_window(), 'Export comparison', f'{self.comparison.name}.{extension}',
                                             f'{extension.upper()} (*.{extension})')
        if not path or not self._modal_action_current(owner):
            return
        target = Path(path)
        if target.suffix.lower() != '.' + extension:
            target = target.with_name(target.name + '.' + extension)
        overwrite = target.exists()
        if overwrite and prompt_message(self.prompt_window(), 'Replace file?', f'Replace {target}?',
                (PromptAction.YES, PromptAction.CANCEL), PromptAction.CANCEL) != PromptAction.YES:
            return
        if not self._modal_action_current(owner):
            return
        try:
            self.export(target, overwrite=overwrite)
            self.inspector_bridge.set_message(f'Exported {target.name}')
        except (ValueError, OSError) as exc:
            self.inspector_bridge.set_message(str(exc))

    def bind_canvas_actions(self, actions):
        self._canvas_actions = dict(actions)

    def edit_x_interval(self):
        self.inspectorSectionRequested.emit(2, 'interval')

    def _set_inspector_visible(self, visible):
        self.inspectorVisibilityRequested.emit(visible)

    def validate_editor(self):
        bridge = self.inspector_bridge
        error = self._draft_error()
        if not error:
            error = self._format_draft_error(bridge.parsed_values())
            if error:
                bridge._error_field = 'width_mm'
        if error:
            bridge.set_message(error)
            field = bridge._error_field
            self.set_inspector_checked(True)
            self._set_inspector_visible(True)
            section = 0 if field in ('label', 'color', 'width', 'y_offset') or field in bridge._symbol_fields else 2
            self.inspectorSectionRequested.emit(section, field)
            bridge.focusFieldRequested.emit(field)
            raise ValueError(error)

    def dispose(self):
        if self._disposed:
            return
        if self._scene is not None:
            self._scene.detach()
        self._disposed = True
        self.command_bridge.dispose()
        self.canvas_bridge.dispose()
        self.inspector_bridge.dispose()
        self.analysis_controller.dispose()
        self.canvas.mpl_disconnect(self._curve_click_connection)
        self._preview_styles.clear()
        self.figure.clear()


def _retire_destroyed_controller(canvas, commands, bridge):
    commands._closed = True
    bridge._disposed = True
    bridge._visible = False
    bridge._held_buttons.clear()
    canvas.dispose()
