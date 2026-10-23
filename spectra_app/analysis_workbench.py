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
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QStackedWidget, QSplitter, QAbstractItemView,
    QDialog, QDialogButtonBox, QInputDialog, QAbstractSpinBox, QLineEdit, QSizePolicy,
    QToolBar, QToolButton, QMenu, QMessageBox, QFileDialog)


class AnalysisResultPreview(QWidget):
    """Read-only export bytes; formatting changes use the explicit source link."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = QSvgRenderer(self)
        self.svg_bytes = b''
        self.page = None
        self._zoom = 1.
        self._offset = QPointF()
        self._drag_start = None
        self.setMinimumSize(160, 160)
        self.setAccessibleName('Analysis result heatmap — exact exported page')
        self.setToolTip('Wheel to zoom, drag to pan, double-click to fit. These display changes do not alter export.')

    def set_page(self, page):
        self.page = page
        self.svg_bytes = page.svg_bytes if page else b''
        if page:
            self.renderer.load(QByteArray(self.svg_bytes))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        if self.svg_bytes:
            size = self.renderer.viewBoxF().size()
            scale = min(self.width()/max(size.width(), 1), self.height()/max(size.height(), 1))*self._zoom
            width, height = size.width()*scale, size.height()*scale
            self.renderer.render(painter, QRectF((self.width()-width)/2+self._offset.x(),
                (self.height()-height)/2+self._offset.y(), width, height))
        else:
            painter.drawText(self.rect(), Qt.AlignCenter, 'Preparing result heatmap…')

    def wheelEvent(self, event):
        self._zoom = min(12., max(.25, self._zoom*1.2**(event.angleDelta().y()/120)))
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            self._offset += event.position()-self._drag_start
            self._drag_start = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        self._zoom, self._offset = 1., QPointF()
        self.update()


def _number(minimum=0., maximum=1e12, decimals=6):
    control = QDoubleSpinBox()
    control.setRange(minimum, maximum)
    control.setDecimals(decimals)
    control.setKeyboardTracking(False)
    control.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    control.setMinimumWidth(64)
    return control


class _CurrentPageStack(QStackedWidget):
    """A hidden legacy editor must not dictate the workbench's minimum width."""
    def minimumSizeHint(self):
        return self.currentWidget().minimumSizeHint() if self.currentWidget() else super().minimumSizeHint()

    def sizeHint(self):
        return self.currentWidget().sizeHint() if self.currentWidget() else super().sizeHint()

    def hasHeightForWidth(self):
        return self.currentWidget().hasHeightForWidth() if self.currentWidget() else False

    def heightForWidth(self, width):
        return self.currentWidget().heightForWidth(width) if self.currentWidget() else -1


class AnalysisWorkbenchMixin:
    def _workbench_document(self):
        session = self.workspace.active
        return getattr(session.active_graph, 'analysis', None) if session else None

    def _workbench_update_input_context(self):
        """Compact frozen-input context; the shell owns the label's placement."""
        from .analysis_document import analysis_input_changed
        doc = self._workbench_document()
        if doc is None or not hasattr(self, 'analysis_context_label'):
            return
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
        title = session.source_path.name if session.source_path else graph.name
        display_title = title if len(title) <= 24 else title[:21] + '…'
        message = f'{display_title} · v{doc.input_version[:8]}\nFrozen {kind}: {detail}\nvisible {visible}/{cube.gate.size} Gates · analyzed {scope}'
        if changed:
            message += ' · Source changed'
        elif source is None:
            message += ' · Source removed'
        self.analysis_context_label.setText(message)
        self.analysis_context_label.setToolTip(self.analysis_context_label.toolTip() +
            f'\n{title} / {graph.name}\nFrozen processing recipe: {recipe}\nSource changes do not replace this bound input.\nDetection Direct uses this bound input; D2-assisted differentiates it for candidate detection.')

    def _workbench_cancel_job(self):
        """Remember explicit cancellation so completed Gate outcomes survive idle."""
        state = getattr(self, '_workbench_running_job', None)
        if state is not None:
            state['cancelled'] = True

    def _build_analysis_workbench(self):
        self._workbench_syncing = False
        self._workbench_running_job = None
        self._workbench_last_summary = None
        self._workbench_filter_context = None
        self._background_jobs.idle.connect(self._workbench_job_idle)
        self._workbench_render_timer = QTimer(self)
        self._workbench_render_timer.setSingleShot(True)
        self._workbench_render_timer.timeout.connect(self._workbench_render)
        legacy = self._analysis_sidebar_scroll.takeWidget()
        self._analysis_control_stack = _CurrentPageStack()
        self._analysis_control_stack.addWidget(legacy)
        self.workbench_controls = QWidget()
        layout = QVBoxLayout(self.workbench_controls)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(7)
        self.workbench_controls.setToolTip('Find a loose candidate pool, filter it, edit accepted seeds, then optionally fit.')
        grid = QGridLayout()
        self.workbench_scope = QComboBox()
        self.workbench_scope.addItems(['Current Gate', 'All Gates'])
        self.workbench_mode = QComboBox()
        self.workbench_mode.addItems(['Both', 'Peaks', 'Dips'])
        self.workbench_detector = QComboBox()
        self.workbench_detector.addItem('Direct', 'Original')
        self.workbench_detector.addItem('D2-assisted', 'D2')
        self.workbench_detector.setToolTip('Direct: find extrema on the frozen input. D2-assisted: use its second derivative to locate candidates.')
        self.workbench_input = QComboBox()
        self.workbench_input.addItems(['Original', 'Processed'])
        self.workbench_bind_btn = QPushButton('Use input')
        self.workbench_bind_btn.setToolTip('Capture this input version and retire results from the previous input.')
        self.workbench_bind_btn.clicked.connect(self._workbench_rebind_input)
        self.workbench_input.currentTextChanged.connect(self._workbench_refresh)
        grid.addWidget(QLabel('Input'), 0, 0)
        grid.addWidget(self.workbench_input, 0, 1)
        grid.addWidget(self.workbench_bind_btn, 0, 2)
        grid.addWidget(QLabel('Scope'), 1, 0)
        grid.addWidget(self.workbench_scope, 1, 1, 1, 2)
        grid.addWidget(QLabel('Detect'), 2, 0)
        grid.addWidget(self.workbench_mode, 2, 1)
        grid.addWidget(self.workbench_detector, 2, 2)
        self.workbench_xmin, self.workbench_xmax = _number(-1e12), _number(-1e12)
        grid.addWidget(QLabel('X interval'), 3, 0)
        grid.addWidget(self.workbench_xmin, 3, 1)
        grid.addWidget(self.workbench_xmax, 3, 2)
        self.workbench_sg_window = QSpinBox()
        self.workbench_sg_window.setRange(3, 10001)
        self.workbench_sg_window.setSingleStep(2)
        self.workbench_sg_window.setValue(11)
        self.workbench_sg_window.setKeyboardTracking(False)
        self.workbench_sg_window.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        grid.addWidget(QLabel('D2 window'), 4, 0)
        grid.addWidget(self.workbench_sg_window, 4, 1, 1, 2)
        layout.addLayout(grid)
        self.workbench_d2_note = QLabel()
        self.workbench_d2_note.setWordWrap(True)
        self.workbench_d2_note.setAccessibleName('Effective D2 window')
        layout.addWidget(self.workbench_d2_note)
        self.workbench_find_btn = QPushButton('Find candidates')
        self.workbench_find_btn.clicked.connect(self._workbench_find)
        layout.addWidget(self.workbench_find_btn)
        filter_grid = QGridLayout()
        self.workbench_filter_preset = QComboBox()
        self.workbench_filter_preset.addItems(['Loose', 'Balanced', 'Strict', 'Custom'])
        self.workbench_filter_preset.setCurrentText('Balanced')
        filter_grid.addWidget(QLabel('Filter'), 0, 0)
        filter_grid.addWidget(self.workbench_filter_preset, 0, 1)
        self.workbench_filter_dialog = QDialog(self)
        self.workbench_filter_dialog.setWindowTitle('Candidate filter settings')
        advanced_layout = QVBoxLayout(self.workbench_filter_dialog)
        advanced_grid = QGridLayout()
        advanced_layout.addLayout(advanced_grid)
        advanced_button = QPushButton('Settings…')
        advanced_button.clicked.connect(self.workbench_filter_dialog.show)
        filter_grid.addWidget(advanced_button, 0, 2)
        self.workbench_filter_controls = {}
        for row, (name, label) in enumerate((('min_prominence_fraction', 'Prominence fraction'),
            ('min_noise_strength', 'Noise strength'), ('min_width', 'Min width (X)'),
            ('max_width', 'Max width (0 = any)'), ('min_distance', 'Distance (X)'),
            ('max_count', 'Max count (0 = all)')), 1):
            control = QSpinBox() if name == 'max_count' else _number()
            if name == 'max_count':
                control.setRange(0, 10000)
                control.setKeyboardTracking(False)
            control.valueChanged.connect(self._workbench_custom_filter)
            self.workbench_filter_controls[name] = control
            advanced_grid.addWidget(QLabel(label), row, 0)
            advanced_grid.addWidget(control, row, 1)
        advanced_buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        advanced_buttons.button(QDialogButtonBox.Apply).clicked.connect(self._workbench_filter)
        advanced_buttons.rejected.connect(self.workbench_filter_dialog.hide)
        advanced_layout.addWidget(advanced_buttons)
        layout.addLayout(filter_grid)
        self.workbench_filter_btn = QPushButton('Apply filter')
        self.workbench_filter_btn.setToolTip('Apply these criteria to the retained loose candidate pool.')
        self.workbench_filter_btn.clicked.connect(self._workbench_filter)
        self.workbench_filter_preset.currentTextChanged.connect(self._workbench_preset_changed)
        layout.addWidget(self.workbench_filter_btn)
        self.workbench_filter_state = QLabel()
        self.workbench_filter_state.setWordWrap(True)
        self.workbench_filter_state.setAccessibleName('Applied and pending candidate filter')
        layout.addWidget(self.workbench_filter_state)
        row = QHBoxLayout()
        self.workbench_model = QComboBox()
        self.workbench_model.addItems(['Lorentzian', 'Gaussian', 'Voigt'])
        self.workbench_fit_btn = QPushButton('Fit seeds')
        self.workbench_fit_btn.clicked.connect(self._workbench_fit)
        row.addWidget(self.workbench_model)
        row.addWidget(self.workbench_fit_btn)
        layout.addLayout(row)
        self.workbench_status = QLabel()
        self.workbench_status.setWordWrap(True)
        self.workbench_status.setAccessibleName('Analysis task status')
        layout.addWidget(self.workbench_status)
        self.statusBar().messageChanged.connect(self._workbench_progress_message)
        self.workbench_counts = QLabel()
        self.workbench_counts.setWordWrap(True)
        layout.addWidget(self.workbench_counts)
        layout.addWidget(QLabel('Seeds — current Gate'))
        self.workbench_seed_dialog = QDialog(self)
        self.workbench_seed_dialog.setWindowTitle('Edit accepted seeds — current Gate')
        self.workbench_seed_dialog.resize(880, 480)
        seed_layout = QVBoxLayout(self.workbench_seed_dialog)
        seed_layout.addWidget(QLabel('Uncheck to exclude. Edit position, kind and initial fit parameters; blank = estimate.'))
        self.workbench_seeds = QTableWidget(0, 8)
        self.workbench_seeds.setHorizontalHeaderLabels(['Use', 'Position', 'Kind', 'Amplitude', 'Width / σ', 'γ', 'Fixed', '± bound'])
        self.workbench_seeds.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.workbench_seeds.setSelectionMode(QAbstractItemView.SingleSelection)
        self.workbench_seeds.setMinimumHeight(260)
        self.workbench_seeds.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.workbench_seeds.itemChanged.connect(self._workbench_seed_changed)
        seed_layout.addWidget(self.workbench_seeds)
        seed_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        seed_buttons.rejected.connect(self.workbench_seed_dialog.hide)
        seed_layout.addWidget(seed_buttons)
        self.workbench_seed_summary = QTableWidget(0, 3)
        self.workbench_seed_summary.setHorizontalHeaderLabels(['Use', 'Position', 'Kind'])
        self.workbench_seed_summary.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.workbench_seed_summary.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.workbench_seed_summary.setMinimumHeight(90)
        self.workbench_seed_summary.setMaximumHeight(120)
        self.workbench_seed_summary.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.workbench_seed_summary.itemChanged.connect(self._workbench_seed_changed)
        self.workbench_seed_summary.currentCellChanged.connect(
            lambda row, *_: self.workbench_seeds.setCurrentCell(row, 1))
        layout.addWidget(self.workbench_seed_summary)
        self.workbench_edit_seeds_btn = QPushButton('Edit seeds…')
        self.workbench_edit_seeds_btn.setToolTip('Edit seeds and initial fit parameters')
        self.workbench_edit_seeds_btn.clicked.connect(self.workbench_seed_dialog.show)
        row = QHBoxLayout()
        self.workbench_add_btn = QPushButton('Add seed')
        self.workbench_remove_btn = QPushButton('Remove seed')
        self.workbench_add_btn.clicked.connect(self._workbench_add_seed)
        self.workbench_remove_btn.clicked.connect(self._workbench_remove_seed)
        row.addWidget(self.workbench_edit_seeds_btn)
        row.addWidget(self.workbench_add_btn)
        row.addWidget(self.workbench_remove_btn)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.workbench_show_detection = QCheckBox('Detected')
        self.workbench_show_detection.setToolTip('Show detected and manual seed positions.')
        self.workbench_show_fit = QCheckBox('Fitted')
        self.workbench_show_detection.setChecked(True)
        self.workbench_show_fit.setChecked(True)
        row.addWidget(self.workbench_show_detection)
        row.addWidget(self.workbench_show_fit)
        self.workbench_style_btn = QPushButton('Style…')
        self.workbench_style_btn.clicked.connect(self._workbench_overlay_style)
        row.addWidget(self.workbench_style_btn)
        layout.addLayout(row)
        self.workbench_show_detection.toggled.connect(self._workbench_overlay_changed)
        self.workbench_show_fit.toggled.connect(self._workbench_overlay_changed)
        self.workbench_linecut_btn = QPushButton('Create current-Gate linecut graph')
        self.workbench_linecut_btn.setToolTip('Add an editable detached graph and its source/result Workbook to this project.')
        self.workbench_linecut_btn.clicked.connect(self._workbench_create_linecut_graph)
        layout.addWidget(self.workbench_linecut_btn)
        layout.addStretch(1)
        self._analysis_control_stack.addWidget(self.workbench_controls)
        self._analysis_sidebar_scroll.setWidget(self._analysis_control_stack)

        root = self.plot_tabs.widget(0).layout()
        self.workbench_links = QToolBar('Analysis commands')
        self.workbench_links.setMovable(False)
        self.workbench_links.setFloatable(False)
        self.workbench_links.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        actions = [('Source graph', self._workbench_source_graph), ('Original data', self._workbench_original_data),
                   ('Results table', self._workbench_results_table), ('Duplicate analysis', self._workbench_duplicate)]
        self.workbench_link_buttons = {}
        self._workbench_link_actions = {}
        for label, callback in actions:
            action = self.workbench_links.addAction(label, callback)
            self._workbench_link_actions[label] = action
            self.workbench_link_buttons[label] = self.workbench_links.widgetForAction(action)
        source_button = self.workbench_link_buttons['Source graph']
        source_button.setToolTip('Open source graph to edit its format')
        self.workbench_link_buttons['Edit source format'] = source_button
        self.workbench_export_menu = QMenu('Export', self.workbench_links)
        for label, callback in [('Export SVG', lambda: self._on_export_plot_page('svg')),
                                ('Export PNG', lambda: self._on_export_plot_page('png')),
                                ('Copy SVG', self._on_copy_graph_svg)]:
            self.workbench_link_buttons[label] = self.workbench_export_menu.addAction(label, callback)
        export_action = self.workbench_links.addAction('Export')
        self._workbench_link_actions['Export'] = export_action
        export_action.setMenu(self.workbench_export_menu)
        self.workbench_links.widgetForAction(export_action).setPopupMode(QToolButton.InstantPopup)
        self.workbench_linecut_action = self.workbench_links.addAction('Linecut')
        self.workbench_linecut_action.setToolTip('Show auxiliary Gate linecut; heatmap export is unchanged')
        self.workbench_linecut_action.setCheckable(True)
        self.workbench_linecut_action.setChecked(True)
        self.workbench_linecut_action.toggled.connect(self._workbench_toggle_linecut)
        root.insertWidget(1, self.workbench_links)
        root.removeWidget(self.analysis_scroll)
        self._analysis_canvas_stack = QStackedWidget()
        self._analysis_canvas_stack.addWidget(self.analysis_scroll)
        self.workbench_splitter = QSplitter(Qt.Vertical)
        self.workbench_preview = AnalysisResultPreview()
        self.workbench_splitter.addWidget(self.workbench_preview)
        self.workbench_linecut_figure = Figure(figsize=(7., 1.8), layout='tight')
        self.workbench_linecut_canvas = FigureCanvasQTAgg(self.workbench_linecut_figure)
        self.workbench_linecut_canvas.setMinimumHeight(130)
        self.workbench_linecut_canvas.setAccessibleName('Auxiliary Gate linecut — excluded from heatmap export')
        self.workbench_splitter.addWidget(self.workbench_linecut_canvas)
        self.workbench_splitter.setStretchFactor(0, 4)
        self.workbench_splitter.setStretchFactor(1, 1)
        self.workbench_splitter.setSizes([550, 170])
        self._workbench_linecut_sizes = [550, 170]
        self._analysis_canvas_stack.addWidget(self.workbench_splitter)
        root.addWidget(self._analysis_canvas_stack, 1)
        self.workbench_links.hide()
        self._workbench_preset_changed('Balanced')
        for control in (self.workbench_scope, self.workbench_mode, self.workbench_detector, self.workbench_model):
            control.currentTextChanged.connect(self._workbench_settings_changed)
        for control in (self.workbench_xmin, self.workbench_xmax, self.workbench_sg_window):
            control.valueChanged.connect(self._workbench_settings_changed)

    def _workbench_toggle_linecut(self, visible):
        if visible:
            self.workbench_linecut_canvas.show()
            self.workbench_splitter.setSizes(self._workbench_linecut_sizes)
        else:
            self._workbench_linecut_sizes = self.workbench_splitter.sizes()
            self.workbench_linecut_canvas.hide()

    def _workbench_progress_message(self, message):
        if self._background_job_owner == 'analysis' and self._project_busy() and self._workbench_document() is not None:
            self.workbench_status.setText(message)

    def _workbench_start_job(self, work, apply, description, *, results, gates, operation):
        # Paint the local acknowledgement before the worker starts computing.
        self.workbench_status.setText(description)
        for button in (self.workbench_find_btn, self.workbench_filter_btn, self.workbench_fit_btn):
            button.setEnabled(False)
        self.workbench_status.repaint()
        state = dict(results=results, gates=gates, operation=operation, cancelled=False,
                     document=self._workbench_document(), token=self._workbench_job_token())
        def complete(outcomes):
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
            self.workbench_status.setText(summary)
            self.workbench_status.setToolTip(summary + '\nOpen Results table to inspect messages and locate a Gate.')
        state['complete'] = complete
        self._workbench_running_job = state
        self._workbench_last_summary = None
        self._start_background_job(work, complete, description, owner='analysis')

    def _workbench_job_idle(self):
        state = self._workbench_running_job
        if state is not None:
            self._workbench_running_job = None
            if state['cancelled']:
                state['complete'](dict(state['results']))
        self._workbench_refresh()

    def _workbench_set_visible(self):
        if not hasattr(self, '_analysis_canvas_stack'):
            return
        active = self._workbench_document() is not None and self.plot_tabs.currentIndex() == 0
        self._analysis_canvas_stack.setCurrentIndex(int(active))
        self._analysis_control_stack.setCurrentIndex(int(active))
        self._analysis_control_stack.updateGeometry()
        self._analysis_sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff if active else Qt.ScrollBarAsNeeded)
        if not active:
            self.workbench_seed_dialog.hide()
            self.workbench_filter_dialog.hide()
        self.workbench_links.setVisible(active)
        self.toolbar.setVisible(not active)
        self.toolbar.parentWidget().setVisible(not active)
        self.analysis_zoom_combo.setVisible(not active)
        self._view_sidebar_scroll.setEnabled(not active)
        self.plot_page.control_scroll.setEnabled(not active)
        self._processing_content.setEnabled(not active)
        for box in (self._shared_annotations_box, self._shared_view_range_box, self._shared_color_scale_box):
            box.setEnabled(not active)
        if active:
            self._workbench_refresh()

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
                              and g.source_mode == ('raw' if sheet.role == 'original' else 'processed')), None)
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
                    deepcopy(template.presentation), source_mode=mode, published=False)
            mode = 'raw' if sheet.role == 'original' else 'processed'
            graph = next((g for g in session.graphs.values() if g.analysis is not None
                          and g.analysis.source_graph_id == source_id and g.analysis.source_mode == mode), None)
            duplicate = graph is None and any(g.analysis is not None and g.analysis.source_graph_id == source_id
                                             for g in session.graphs.values())
            self.open_workbook(self._sheet_owner(identity).workbook_id, identity)
            return_key = self.graph_documents._active_key
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
        with QSignalBlocker(self.plot_tabs):
            self.plot_tabs.setCurrentIndex(0)
        self._select_graph_page(session, show=False)
        self._restoring_dataset = True
        try:
            self.restore_dataset_controls(session)
            self._plot_cube()
            self._sync_gate_controls()
        finally:
            self._restoring_dataset = False
        self._on_plot_tab_changed(0, restore_graph=False)
        self._set_dat_details_requested(True)
        self._sidebar_tabs.setCurrentWidget(self._analysis_sidebar_scroll)
        self._refresh_document_titles()
        self._refresh_graph_selector()
        self._workbench_restore_settings()
        self._workbench_render()
        self._refresh_project_modified()

    def _workbench_restore_settings(self):
        doc = self._workbench_document()
        if doc is None:
            return
        controls = doc.science.analysis_controls
        self._workbench_syncing = True
        try:
            self.workbench_mode.setCurrentText(controls.peak_mode)
            self.workbench_detector.setCurrentIndex(int(controls.derivative_detection))
            self.workbench_model.setCurrentText(controls.fit_model)
            self.workbench_sg_window.setValue(controls.d2_window)
            self.workbench_xmin.setValue(controls.fit_xmin)
            self.workbench_xmax.setValue(controls.fit_xmax)
            self.workbench_input.setCurrentText('Original' if doc.source_mode == 'raw' else 'Processed')
            self.workbench_scope.setCurrentText('All Gates' if getattr(doc, 'gate_scope', 'current') == 'all' else 'Current Gate')
            self._workbench_set_filter_controls(doc.filters_by_gate.get(self._current_gate_index(), doc.filters))
            self.workbench_show_detection.setChecked(doc.overlay_styles['detected-peak'].visible)
            self.workbench_show_fit.setChecked(doc.overlay_styles['fitted-peak'].visible)
        finally:
            self._workbench_syncing = False
        self._workbench_refresh()

    def _workbench_settings_changed(self, *_args):
        doc = self._workbench_document()
        if doc is None or self._workbench_syncing:
            return
        controls = doc.science.analysis_controls
        changed_interval = (controls.fit_xmin, controls.fit_xmax) != (self.workbench_xmin.value(), self.workbench_xmax.value())
        model_changed = controls.fit_model != self.workbench_model.currentText()
        controls.peak_mode = self.workbench_mode.currentText()
        controls.derivative_detection = self.workbench_detector.currentData() == 'D2'
        controls.d2_window = self.workbench_sg_window.value()
        controls.fit_model = self.workbench_model.currentText()
        controls.fit_xmin, controls.fit_xmax = self.workbench_xmin.value(), self.workbench_xmax.value()
        doc.gate_scope = 'all' if self.workbench_scope.currentIndex() else 'current'
        for widget, value in ((self.fit_xmin_spin, controls.fit_xmin), (self.fit_xmax_spin, controls.fit_xmax)):
            with QSignalBlocker(widget):
                widget.setValue(value)
        with QSignalBlocker(self.fit_model_combo):
            self.fit_model_combo.setCurrentText(controls.fit_model)
        if changed_interval or model_changed:
            self._workbench_last_summary = None
            doc.retire_fit()
            self._workbench_changed()
            return
        self._invalidate_analysis_jobs()
        self._schedule_project_modified()
        self._workbench_refresh()

    def _workbench_set_filter_controls(self, settings):
        with QSignalBlocker(self.workbench_filter_preset):
            self.workbench_filter_preset.setCurrentText(settings.preset)
        for name, control in self.workbench_filter_controls.items():
            with QSignalBlocker(control):
                control.setValue(getattr(settings, name))

    def _workbench_preset_changed(self, name):
        if name != 'Custom':
            from .peak_candidates import candidate_filter_preset
            self._workbench_set_filter_controls(candidate_filter_preset(name))
        self._workbench_filter_context_state()

    def _workbench_custom_filter(self, *_args):
        with QSignalBlocker(self.workbench_filter_preset):
            self.workbench_filter_preset.setCurrentText('Custom')
        self._workbench_filter_context_state()

    def _workbench_filter_context_state(self):
        doc = self._workbench_document()
        if doc is None or self._workbench_syncing or not hasattr(self, 'workbench_filter_state'):
            return
        gates = self._workbench_gates()
        scope = f'All {len(gates)} Gates' if self.workbench_scope.currentIndex() else f'Gate {self.current_cube.gate[gates[0]]:g}'
        context = self._document_key(), tuple(gates)
        applied = [doc.filters_by_gate.get(gate) for gate in gates]
        mixed = any(settings != applied[0] for settings in applied[1:])
        if context != self._workbench_filter_context:
            # Draft criteria belong to one scope. A scope change restores that
            # scope's applied criteria, instead of mislabeling the previous Gate.
            self._workbench_filter_context = context
            self._workbench_set_filter_controls(doc.filters if mixed or applied[0] is None else applied[0])
        label = 'mixed' if mixed else applied[0].preset if applied[0] is not None else 'not applied'
        try:
            pending = self._workbench_filter_settings()
            changed = any(settings != pending for settings in applied)
            suffix = f' · Pending: {pending.preset}' if changed else ''
        except ValueError as exc:
            changed, suffix = False, ' · Pending: invalid settings'
            self.workbench_filter_state.setToolTip(str(exc))
        else:
            self.workbench_filter_state.setToolTip('Apply filter explicitly to this scope. Changing Gate or scope restores its applied criteria.\n' +
                '\n'.join(f'Gate {self.current_cube.gate[gate]:g}: {settings.to_dict() if settings else "not applied"}'
                          for gate, settings in zip(gates, applied)))
        self.workbench_filter_state.setText(f'{scope} · Applied: {label}{suffix}')
        self.workbench_filter_btn.setEnabled(changed and not self._project_busy())

    def _workbench_effective_d2_window(self):
        doc = self._workbench_document()
        if doc is None:
            return
        enabled = self.workbench_detector.currentData() == 'D2'
        self.workbench_sg_window.setEnabled(enabled and not self._project_busy())
        self.workbench_d2_note.setVisible(enabled)
        if not enabled:
            return
        cube = doc.science.current_cube
        selected = (cube.energy >= self.workbench_xmin.value()) & (cube.energy <= self.workbench_xmax.value())
        requested = self.workbench_sg_window.value()
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
        self.workbench_d2_note.setText(message)
        self.workbench_d2_note.setToolTip('Even requests round up to odd. Each finite segment caps its own window to its largest odd length. Segments shorter than 5 points are skipped.')

    def _workbench_filter_settings(self):
        from .peak_candidates import CandidateFilter
        return CandidateFilter(preset=self.workbench_filter_preset.currentText(),
            **{name: control.value() for name, control in self.workbench_filter_controls.items()})

    def _workbench_gates(self):
        return list(range(self.current_cube.gate.size)) if self.workbench_scope.currentIndex() else [self._current_gate_index()]

    def _workbench_job_token(self):
        doc = self._workbench_document()
        return (self._document_key(), doc.job_token) if doc else None

    def _workbench_find(self):
        from .peak_candidates import collect_peak_candidates
        doc = self._workbench_document()
        if doc is None or self._project_busy():
            return
        try:
            self._workbench_finish_editors()
            settings = self._workbench_filter_settings()
            gates = self._workbench_gates()
            cube = doc.science.current_cube
            low, high = self.workbench_xmin.value(), self.workbench_xmax.value()
            if high <= low:
                raise ValueError('Analysis interval maximum must exceed minimum.')
            mask = (cube.energy >= low) & (cube.energy <= high)
            index = np.flatnonzero(mask)
            x = np.asarray(cube.energy[mask]).copy()
            traces = {gate: np.asarray(cube.z[gate, mask]).copy() for gate in gates}
            mode, detector = self.workbench_mode.currentText(), self.workbench_detector.currentData()
            window = self.workbench_sg_window.value()
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
                        self.statusBar().showMessage(message)
                self._workbench_changed()
            self._workbench_start_job(work, apply, 'Finding candidates…', results=results, gates=gates, operation='Find')
        except ValueError as exc:
            self._show_error(str(exc))

    def _workbench_filter(self):
        doc = self._workbench_document()
        if doc is None or self._project_busy():
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
        if doc is None or self._project_busy():
            return
        try:
            self._workbench_finish_editors()
            cube = doc.science.current_cube
            low, high = self.workbench_xmin.value(), self.workbench_xmax.value()
            if high <= low:
                raise ValueError('Analysis interval maximum must exceed minimum.')
            model = self.workbench_model.currentText()
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
        # Moving focus commits the active table delegate, including Ctrl+S
        # invoked while its line editor still owns keyboard focus.
        editor = self.workbench_seeds.findChild(QLineEdit)
        if editor is not None and editor.isVisible():
            self.workbench_seeds.commitData(editor)
        self.workbench_find_btn.setFocus(Qt.OtherFocusReason)
        for spin in (*self.workbench_controls.findChildren(QAbstractSpinBox),
                     *self.workbench_filter_dialog.findChildren(QAbstractSpinBox)):
            if not spin.hasAcceptableInput():
                raise ValueError('Correct the Analysis numeric editor before continuing.')
            spin.interpretText()
        doc = self._workbench_document()
        if doc is not None:
            controls = doc.science.analysis_controls
            current = (controls.peak_mode, controls.derivative_detection, controls.d2_window,
                       controls.fit_model, controls.fit_xmin, controls.fit_xmax, doc.gate_scope)
            desired = (self.workbench_mode.currentText(), self.workbench_detector.currentData() == 'D2',
                       self.workbench_sg_window.value(), self.workbench_model.currentText(),
                       self.workbench_xmin.value(), self.workbench_xmax.value(),
                       'all' if self.workbench_scope.currentIndex() else 'current')
            if current != desired:
                self._workbench_settings_changed()

    def _workbench_refresh(self):
        doc = self._workbench_document()
        if doc is None or not hasattr(self, 'workbench_seeds') or self._workbench_syncing:
            return
        gate = self._current_gate_index()
        seeds = doc.seeds_by_gate.get(gate, [])
        accepted = doc.accepted_seeds(gate)
        pool = doc.candidate_pools.get(gate, [])
        self.workbench_counts.setText(f'Gate {self.current_cube.gate[gate]:g}: {len(pool)} candidates · {len(accepted)} accepted · '
                                     f'{sum(not s.enabled for s in seeds)} excluded')
        status = f'Status: {doc.gate_status.get(gate, "pending")} · {len(doc.science.batch_fit_result_map)} fitted Gates'
        message = doc.gate_messages.get(gate, '')
        summary = self._workbench_last_summary
        status_text = summary[2] if summary and summary[0] is doc and summary[1] == doc.job_token else status
        self.workbench_status.setText(self.statusBar().currentMessage() if self._project_busy() else status_text)
        self.workbench_status.setToolTip(status_text + ('\n' + message if message else '') +
                                        '\nOpen Results table for Gate messages and navigation.')
        model = self.workbench_model.currentText()
        units = doc.science.current_cube.energy_label
        width = 'Sigma' if model in ('Gaussian', 'Voigt') else 'HWHM'
        self.workbench_seeds.setHorizontalHeaderLabels(['Use', f'Position ({units})', 'Kind', 'Amplitude',
            f'{width} ({units})', f'Gamma ({units})', 'Fixed center', f'Center ±bound ({units})'])
        with QSignalBlocker(self.workbench_seeds):
            self.workbench_seeds.setRowCount(len(seeds))
            for row, seed in enumerate(seeds):
                distance = None if seed.center_min is None or seed.center_max is None else min(seed.position-seed.center_min, seed.center_max-seed.position)
                values = ('', str(seed.position), seed.kind,
                          '' if seed.amplitude is None else str(seed.amplitude),
                          '' if seed.width is None else str(seed.width),
                          '' if seed.gamma is None else str(seed.gamma), '',
                          '' if distance is None else str(distance))
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column in (0, 6):
                        item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        item.setCheckState(Qt.Checked if (seed.enabled if column == 0 else seed.fixed_center) else Qt.Unchecked)
                    if column == 5 and model != 'Voigt':
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                        item.setToolTip('Gamma applies only to Voigt. The stored initial value is retained for switching back.')
                    self.workbench_seeds.setItem(row, column, item)
        with QSignalBlocker(self.workbench_seed_summary):
            self.workbench_seed_summary.setRowCount(len(seeds))
            for row in range(len(seeds)):
                for column in range(3):
                    # The Python-created copy is invalidated when Qt replaces it.
                    # PySide 6.11 clone() wrappers retain stale native addresses.
                    self.workbench_seed_summary.setItem(row, column, QTableWidgetItem(self.workbench_seeds.item(row, column)))
        busy = self._project_busy()
        for button in (self.workbench_find_btn, self.workbench_filter_btn, self.workbench_fit_btn,
                       self.workbench_bind_btn, self.workbench_add_btn, self.workbench_remove_btn):
            button.setEnabled(not busy)
        self.workbench_seeds.setEnabled(not busy)
        self.workbench_seed_summary.setEnabled(not busy)
        self.workbench_edit_seeds_btn.setEnabled(not busy)
        for button in self.workbench_link_buttons.values():
            button.setEnabled(not busy)
        for action in self._workbench_link_actions.values():
            action.setEnabled(not busy)
        self.workbench_linecut_btn.setEnabled(not busy and bool(accepted or gate in doc.science.batch_fit_result_map))
        source_exists = doc.source_graph_id in self.workspace.active.graphs
        from .analysis_document import analysis_input_changed
        source = self.workspace.active.graphs.get(doc.source_graph_id)
        changed = source_exists and analysis_input_changed(self.workspace.active,
            source_mode='raw' if self.workbench_input.currentIndex() == 0 else 'processed',
            gate_indices=source.gate_indices)
        self.workbench_bind_btn.setEnabled(not busy and changed)
        count = doc.science.current_cube.gate.size
        self.workbench_bind_btn.setText(f'Replace / reset {count} Gates…' if changed else 'Input unchanged')
        self.workbench_bind_btn.setToolTip('Review replacement or duplicate this analysis before resetting its input.' if changed
            else 'The selected input, content, recipe, and Gate display scope already match the frozen input.' if source_exists
            else 'Source removed: the frozen input cannot be replaced.')
        self.workbench_link_buttons['Source graph'].setEnabled(not busy and source_exists)
        self.workbench_link_buttons['Edit source format'].setEnabled(not busy and source_exists)
        self._workbench_link_actions['Source graph'].setEnabled(not busy and source_exists)
        self._workbench_draw_linecut()
        self._workbench_filter_context_state()
        self._workbench_effective_d2_window()
        self._refresh_analysis_context()

    def _workbench_seed_changed(self, item):
        doc = self._workbench_document()
        if doc is None or self._workbench_syncing or self._project_busy():
            return
        gate, row, column = self._current_gate_index(), item.row(), item.column()
        seeds = list(doc.seeds_by_gate.get(gate, []))
        if row >= len(seeds):
            return
        seed = seeds[row]
        try:
            if column in (0, 6):
                updates = {'enabled' if column == 0 else 'fixed_center': item.checkState() == Qt.Checked, 'manual': True}
            elif column == 2:
                kind = item.text().strip().lower()
                if kind not in ('peak', 'dip'):
                    raise ValueError('Seed kind must be peak or dip.')
                updates = {'kind': kind, 'amplitude': None, 'manual': True}
                if seed.source_index is not None and kind != seed.kind:
                    # Preserve the explicit replacement across filter relaxation.
                    seeds[row] = replace(seed, enabled=False, manual=True)
                    from .analysis_document import AnalysisSeed
                    seeds.insert(row+1, AnalysisSeed(seed.position, kind=kind, manual=True,
                        width=seed.width, gamma=seed.gamma, fixed_center=seed.fixed_center,
                        center_min=seed.center_min, center_max=seed.center_max))
                    doc.set_seeds(gate, seeds)
                    self._workbench_changed()
                    return
            elif column == 7:
                distance = float(item.text()) if item.text().strip() else None
                if distance is not None and (not np.isfinite(distance) or distance <= 0):
                    raise ValueError('The center bound must be positive, or blank for the full interval.')
                updates = dict(center_min=None if distance is None else seed.position-distance,
                               center_max=None if distance is None else seed.position+distance, manual=True)
            else:
                field = {1: 'position', 3: 'amplitude', 4: 'width', 5: 'gamma'}[column]
                value = float(item.text()) if item.text().strip() else None
                if value is None and field == 'position' or value is not None and not np.isfinite(value):
                    raise ValueError('Enter a finite seed position or parameter.')
                if field in ('width', 'gamma') and value is not None and value <= 0:
                    raise ValueError('Seed widths must be positive.')
                updates = {field: value, 'manual': True}
                if field == 'position':
                    updates['center_min'], updates['center_max'] = None, None
            seeds[row] = replace(seed, **updates)
            doc.set_seeds(gate, seeds)
            self._workbench_changed()
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            self._workbench_refresh()

    def _workbench_add_seed(self, checked=False, *, position=None, kind='peak'):
        from .analysis_document import AnalysisSeed
        doc = self._workbench_document()
        if doc is None:
            return
        if position is None:
            position, accepted = QInputDialog.getDouble(self, 'Add analysis seed', 'Position (X)',
                (self.workbench_xmin.value()+self.workbench_xmax.value())/2,
                self.workbench_xmin.value(), self.workbench_xmax.value(), 8)
            if not accepted:
                return
        gate = self._current_gate_index()
        seeds = list(doc.seeds_by_gate.get(gate, []))
        seeds.append(AnalysisSeed(float(position), kind=kind, manual=True))
        doc.set_seeds(gate, seeds)
        self._workbench_changed()

    def _workbench_remove_seed(self):
        doc = self._workbench_document()
        row = self.workbench_seeds.currentRow()
        if doc is None or row < 0:
            return
        gate = self._current_gate_index()
        seeds = list(doc.seeds_by_gate.get(gate, []))
        if row < len(seeds):
            if seeds[row].source_index is not None:
                seeds[row] = replace(seeds[row], enabled=False, manual=True)
            else:
                seeds.pop(row)
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
        self.workbench_linecut_canvas.draw_idle()

    def _workbench_render(self, key=None):
        from .analysis_document import graph_presentation, sync_analysis_format
        from .svg_export import prepare_heatmap_page, render_hybrid_svg
        key = key or self._document_key()
        session = self.workspace.datasets.get(key[0])
        if session is None or key[1] not in session.graphs or session.graphs[key[1]].analysis is None:
            return
        target = self.graph_documents.page_for(key)
        if target is None:
            return
        graph = session.graphs[key[1]]
        sync_analysis_format(session, graph)
        owner = self._plot_owner_token(key)
        try:
            candidate = replace(session, active_graph_id=key[1])
            presentation = graph_presentation(session, graph)
            snapshot = self.snapshot_plot_dataset(candidate)
            presentation, page = prepare_heatmap_page(snapshot, presentation, renderer=render_hybrid_svg)
            target.set_presentation(presentation)
            target.preview.set_geometry(snapshot, presentation)
            target.commit_rendered_page(page)
            if key == self._document_key():
                self.workbench_preview.set_page(page)
        except (ValueError, RuntimeError) as exc:
            target.invalidate(owner, f'Cannot prepare result: {exc}')
            if key == self._document_key():
                self.workbench_preview.set_page(None)
                self.workbench_status.setText(str(exc))

    def _workbench_source_graph(self):
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
            self.statusBar().showMessage('Created current-Gate linecut graph and detached source/results in the project.')
        except ValueError as exc:
            self._show_error(str(exc))

    def _workbench_save_parameters(self):
        from .analysis_document import analysis_parameter_rows
        from .workbook_state import Sheet, Workbook
        if self._workbench_document() is None:
            return
        try:
            self._require_project_idle()
            self._workbench_finish_editors()
            doc = self._workbench_document()
            columns, rows, _ = analysis_parameter_rows(doc)
            values = np.full((len(rows), len(columns)), np.nan)
            text_columns = {}
            for column in range(len(columns)):
                if any(isinstance(row[column], str) for row in rows):
                    text_columns[column] = ['' if row[column] is None else str(row[column]) for row in rows]
                else:
                    values[:, column] = [np.nan if row[column] is None else row[column] for row in rows]
            sheet = Sheet(name='Analysis parameters', kind='snapshot', role='snapshot', data=values,
                column_names=columns, text_columns=text_columns, y_columns=[3, 4],
                snapshot_note=json.dumps(self._workbench_provenance(), ensure_ascii=False, indent=2))
            book = Workbook(name=f'Analysis parameters — {self.workspace.active.active_graph.name}', sheet_ids=[sheet.sheet_id])
            self._publish_analysis_results(book, [sheet])
            self.statusBar().showMessage('Saved all-Gate analysis parameters to a new project Workbook.')
        except ValueError as exc:
            self._show_error(str(exc))

    def _workbench_export_parameters(self):
        from .analysis_document import analysis_parameter_rows
        if self._workbench_document() is None:
            return
        try:
            self._require_project_idle()
            self._workbench_finish_editors()
            doc = self._workbench_document()
            columns, rows, _ = analysis_parameter_rows(doc)
            path, _ = QFileDialog.getSaveFileName(self, 'Export all-Gate analysis parameters',
                str(self._output_folder() / 'analysis_parameters.csv'), 'CSV Files (*.csv)')
            if not path:
                return
            with Path(path).open('w', newline='', encoding='utf-8-sig') as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                writer.writerows(rows)
            self.statusBar().showMessage(f'Exported all-Gate analysis parameters: {path}')
        except (ValueError, OSError) as exc:
            self._show_error(str(exc))

    def _workbench_confirm_rebind(self, affected_gates):
        box = QMessageBox(self)
        box.setWindowTitle('Replace analysis input')
        box.setIcon(QMessageBox.Warning)
        box.setText(f'Replace the frozen input and reset analysis for {affected_gates} Gates?')
        box.setInformativeText('Candidate pools, seeds, and fits for those Gates will be cleared. '
            'Duplicate then replace preserves this analysis and replaces the input only in its copy.')
        replace_button = box.addButton('Replace and reset', QMessageBox.DestructiveRole)
        duplicate_button = box.addButton('Duplicate then replace', QMessageBox.ActionRole)
        cancel_button = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return ('replace' if box.clickedButton() is replace_button else
                'duplicate' if box.clickedButton() is duplicate_button else 'cancel')

    def _workbench_results_table(self):
        from .analysis_document import analysis_parameter_rows
        doc = self._workbench_document()
        if doc is None:
            return
        self._workbench_finish_editors()
        dialog = QDialog(self)
        dialog.setWindowTitle('Analysis results')
        dialog.resize(960, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('All bound Gates, including excluded seeds and failed or cancelled work.'))
        columns, rows, gates = analysis_parameter_rows(doc)
        table = QTableWidget(len(rows), len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem('' if value is None else str(value))
                item.setToolTip(item.text())
                table.setItem(row, column, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        message_column = columns.index('Message')
        table.horizontalHeader().moveSection(message_column, 2)
        table.horizontalHeader().setSectionResizeMode(message_column, QHeaderView.Interactive)
        table.horizontalHeader().resizeSection(message_column, 250)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        locate = buttons.addButton('Locate Gate', QDialogButtonBox.ActionRole)
        locate.setObjectName('analysisLocateGate')
        def locate_gate():
            row = table.currentRow()
            if 0 <= row < len(gates):
                self._set_current_gate_from_index(gates[row], update_plot=True)
                dialog.accept()
        locate.clicked.connect(locate_gate)
        table.cellDoubleClicked.connect(lambda *_: locate_gate())
        locate.setEnabled(bool(rows))
        if rows:
            table.setCurrentCell(0, 0)
        save = buttons.addButton('Save all Gates to Workbook', QDialogButtonBox.ActionRole)
        save.setToolTip('Add a detached parameter table to this project. Save the project to retain it.')
        save.clicked.connect(lambda: (self._workbench_save_parameters(), dialog.accept()))
        export = buttons.addButton('Export all Gates CSV…', QDialogButtonBox.ActionRole)
        export.setToolTip('Write the exact all-Gate parameter table to a CSV file on disk.')
        export.clicked.connect(self._workbench_export_parameters)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _workbench_overlay_changed(self):
        doc = self._workbench_document()
        if doc is None or self._workbench_syncing:
            return
        for name, style in list(doc.overlay_styles.items()):
            visible = self.workbench_show_detection.isChecked() if name.startswith('detected') else self.workbench_show_fit.isChecked()
            doc.overlay_styles[name] = replace(style, visible=visible)
        self._workbench_changed()

    def _workbench_overlay_style(self):
        doc = self._workbench_document()
        if doc is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Analysis scatter styles')
        layout = QVBoxLayout(dialog)
        grid = QGridLayout()
        fields = {}
        for col, label in enumerate(('Layer', 'Color', 'Marker', 'Size', 'Filled')):
            grid.addWidget(QLabel(label), 0, col)
        for row, (name, style) in enumerate(doc.overlay_styles.items(), 1):
            color = QLineEdit(style.color)
            marker = QComboBox()
            marker.addItems(['o', 's', '^', 'v', 'D', '+', 'x', '*', '.'])
            marker.setCurrentText(style.marker)
            size = _number(.1, 100., 1)
            size.setValue(style.size)
            filled = QCheckBox()
            filled.setChecked(style.filled)
            fields[name] = color, marker, size, filled
            for col, widget in enumerate((QLabel(name.replace('-', ' ')), color, marker, size, filled)):
                grid.addWidget(widget, row, col)
        layout.addLayout(grid)
        error = QLabel()
        error.setWordWrap(True)
        layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        def apply():
            try:
                updated = {name: replace(doc.overlay_styles[name], color=color.text(), marker=marker.currentText(),
                           size=size.value(), filled=filled.isChecked())
                           for name, (color, marker, size, filled) in fields.items()}
            except ValueError as exc:
                error.setText(str(exc))
                return
            doc.overlay_styles = updated
            self._workbench_changed()
            dialog.accept()
        buttons.accepted.connect(apply)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

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
            mode = 'raw' if self.workbench_input.currentIndex() == 0 else 'processed'
            if not analysis_input_changed(session, source_mode=mode, gate_indices=source.gate_indices):
                self._workbench_refresh()
                return
            choice = self._workbench_confirm_rebind(doc.science.current_cube.gate.size)
            if choice == 'cancel':
                return
            if choice == 'duplicate':
                duplicate_analysis_graph(session)
            rebind_analysis_input(session, source_mode=mode, gate_indices=source.gate_indices)
            self._workbench_last_summary = None
            self._workbench_filter_context = None
            self._workbench_enter(session)
        except ValueError as exc:
            self._show_error(str(exc))
