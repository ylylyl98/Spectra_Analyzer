from __future__ import annotations

from .signal_titles import (infer_signal_kind, infer_source_signal_kind, automatic_signal_label,
                            display_signal_label, RAW_LABEL, DERIVATIVE_LABEL)

import csv
import json
import re
from copy import deepcopy
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib import colormaps
from matplotlib.path import Path as MplPath
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.widgets import LassoSelector
from scipy.signal import savgol_filter
from PySide6.QtCore import Qt, QObject, QEvent, QPointF, QSettings, QSignalBlocker, QTimer, Signal, QSize, QMimeData, QUrl, QItemSelection, QItemSelectionModel
from PySide6.QtGui import QCursor, QDragEnterEvent, QDropEvent, QWheelEvent, QIcon, QImage, QPixmap, QKeySequence, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QAbstractSlider,
    QScrollBar,
    QTabBar,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QToolButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QFrame,
    QSizePolicy,
    QStyle,
)

from .dat_io import AXIS_OVERRIDE_ITEMS, DataCube, load_dat_cube
from .background_jobs import (CancelledError, BackgroundJobs, JobContext, DatImportError, LoadRequest,
                              prepare_dat, prepare_dat_batch, discover_folders, discover_files)
from .custom_colormaps import resolve_colormap, COMMON_COLORMAPS, canonical_colormap
from .plot_state import PlotStateStore, source_fingerprint
from .title_templates import (BUILTIN_PRESET, FIELDS, manual_settings, parse_angle_map,
                              render_title, validate_preset, validate_title_settings)
from .image_export import (
    PLOT_FONT_SIZE,
    style_plot_axis,
)
from .fitting import (
    FitResult,
    find_peak_indices,
    find_peaks_second_derivative,
    fit_multi_peak,
    _clamp_sg_window,
)
from .processing import energy_second_derivative
from .dataset_state import (ANALYSIS_BINDINGS, AnalysisControls, DatasetDefaults,
                            DatasetSession, FixedRecipe, ProcessingState, SessionField,
                            Workspace, canonical_path, dataset_display_labels, initialize_dataset,
                            detached_graph_session, graph_export_stem, refresh_graph_colors,
                            graph_cube, graph_signal_label)
from .edit_history import (COLOR_FIELDS, RANGE_FIELDS, STYLE_FIELDS, TITLE_FIELDS,
                           EditCommand, EditHistory, FieldChange, capture_fields, field_changes,
                           restore_fields, capture_peaks)
from .plot_presentation import NormSpec, PlotPresentation
from .plot_page import PlotPage
from .disclosure import DisclosureSection, protect_disclosure_drafts, reveal_editor
from .graph_documents import GraphDocuments
from .project_tree import ProjectTreeWidget
from .plot_icons import plot_icon
from .workbook_page import WorkbookPage, WorkbookPlotCommands
from .xy_plot_page import XYPlotPage
from .workbook_state import (create_workbook, register_xy_sheet, create_comparison, add_curve,
    remove_workbook, remove_sheet, remove_comparison, ensure_dataset_sheet,
    reconcile_dataset_sheets, document_record_key, document_exists, set_sheet_mapping, _source_key)
from .workbook_state import save_sheet_snapshot, merge_workbooks, sheet_shape, validate_workbook_state
from .workbook_state import workbook_display_labels, workbook_sources
from .workbook_state import select_curve_additions, validate_mapping
from .analysis_graph import fit_curve_sheet, marker_curve_sheet, saved_result_records, add_analysis_comparison
from .analysis_workbench import AnalysisWorkbenchMixin
from .analysis_document import analysis_owner, graph_presentation, sync_analysis_format
from .workbook_state import Workbook
from .xy_io import load_xy, XYTable
from .xy_import_dialog import XYImportDialog, validate_import_mapping
from .data_import import DATA_SUFFIXES, DATA_FILTER, PreparedDataFile, prepare_data_batch
from .workbook_state import sheet_values, sheet_columns
from .sheet_comparison_dialog import SheetComparisonDialog, validate_sheet_comparison
from .plot_snapshot import region_layout, snapshot_dataset
from .plot_templates import FormatApplicationOptions, FormatTemplateStore, capture_format, apply_format
from .plot_comparison import PlotComparisonWindow
from .plot_batch import BatchExportJob, BatchFileResult, BatchSummary, SerialPlotBatch
from .plot_dialogs import ApplyFormatDialog, BatchExportDialog
from .svg_export import (MISSING_COLOR, build_color_norm, prepare_display_values,
                         valid_color_values, prepare_heatmap_page, render_hybrid_svg, write_svg, write_svg_png,
                         apply_heatmap_axis_style, apply_heatmap_colorbar_style,
                         _align_heatmap_corner_tick_labels, _update_heatmap_tick_contrast)


# ── Matplotlib tab10 palette (used for peak labels) ──────────────────────────
_TAB10 = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

# ── Button style constants ────────────────────────────────────────────────────
_STYLE_PRIMARY = (
    "QPushButton {"
    "  background: #1976d2; color: white;"
    "  border: none; border-radius: 4px;"
    "  padding: 5px 10px; font-weight: 600;"
    "}"
    "QPushButton:hover { background: #1565c0; }"
    "QPushButton:pressed { background: #0d47a1; }"
    "QPushButton:disabled { background: #b0bec5; color: #78909c; }"
)
_STYLE_TOGGLE = (
    "QPushButton {"
    "  background: #f5f5f5; color: #333;"
    "  border: 1px solid #bdbdbd; border-radius: 4px;"
    "  padding: 4px 8px;"
    "}"
    "QPushButton:checked { background: #f57c00; color: white; border-color: #e65100; }"
    "QPushButton:hover:!checked { background: #eeeeee; }"
    "QPushButton:disabled { color: #bdbdbd; border-color: #e0e0e0; }"
)
_STYLE_MUTED = (
    "QPushButton {"
    "  background: #fafafa; color: #455a64;"
    "  border: 1px solid #cfd8dc; border-radius: 4px;"
    "  padding: 4px 8px;"
    "}"
    "QPushButton:hover { background: #eceff1; }"
    "QPushButton:disabled { color: #bdbdbd; border-color: #eee; }"
)
_STYLE_DANGER = (
    "QPushButton {"
    "  background: #fafafa; color: #c62828;"
    "  border: 1px solid #ef9a9a; border-radius: 4px;"
    "  padding: 4px 8px;"
    "}"
    "QPushButton:hover { background: #ffebee; }"
    "QPushButton:disabled { color: #bdbdbd; border-color: #e0e0e0; }"
)


class _AnalysisNavigationToolbar(NavigationToolbar2QT):
    """Analysis navigation tools; image exports belong to the formal Plot page."""
    toolitems = tuple(item for item in NavigationToolbar2QT.toolitems if item[3] != 'save_figure')


class _PageWheelFilter(QObject):
    """Wheel gestures scroll pages; value controls and plots never consume them."""
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Wheel or not isinstance(watched, QWidget):
            return False
        widget = watched
        blocked = False
        while widget is not None:
            if isinstance(widget, (QAbstractSpinBox, QComboBox, QTabBar, FigureCanvasQTAgg)):
                blocked = True
            if isinstance(widget, QAbstractSlider) and not isinstance(widget, QScrollBar):
                blocked = True
            if isinstance(widget, QScrollArea):
                if blocked:
                    viewport = widget.viewport()
                    forwarded = QWheelEvent(
                        QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                        event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                        event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                    )
                    QApplication.sendEvent(viewport, forwarded)
                    event.accept()
                    return True
                return False
            widget = widget.parentWidget()
        if blocked:
            event.accept()
        return blocked


class GateValueSpinBox(QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._allowed_values = np.asarray([], dtype=float)

    def set_allowed_values(self, values: np.ndarray) -> None:
        self._allowed_values = np.asarray(values, dtype=float).ravel()
        if self._allowed_values.size:
            self.setRange(float(np.nanmin(self._allowed_values)), float(np.nanmax(self._allowed_values)))

    def stepBy(self, steps: int) -> None:
        if self._allowed_values.size == 0:
            super().stepBy(steps)
            return
        idx = int(np.argmin(np.abs(self._allowed_values - float(self.value()))))
        idx = int(np.clip(idx + int(steps), 0, self._allowed_values.size - 1))
        self.setValue(float(self._allowed_values[idx]))


class _ColorBoundSpinBox(QDoubleSpinBox):
    """Distinguish native numeric edits from refresh/recipe setValue calls."""
    userCommitted = Signal()

    def __init__(self):
        super().__init__()
        self._edit_start = None
        self.lineEdit().textEdited.connect(self._begin_edit)
        self.editingFinished.connect(self._finish_edit)

    def _begin_edit(self, _text):
        if self._edit_start is None:
            self._edit_start = self.value()

    def _finish_edit(self):
        previous, self._edit_start = self._edit_start, None
        if previous is not None and previous != self.value():
            self.userCommitted.emit()

    def setValue(self, value):
        self._edit_start = None
        super().setValue(value)

    def stepBy(self, steps):
        previous = self.value()
        super().stepBy(steps)
        self._edit_start = None
        if previous != self.value():
            self.userCommitted.emit()


class MainWindow(AnalysisWorkbenchMixin, QMainWindow):
    # Existing scientific algorithms use these names. The descriptors always
    # read/write the active owner; there is no second window copy of this data.
    current_cube = SessionField('current_cube')
    _base_cube = SessionField('base_cube')
    current_dat_path = SessionField('source_path')
    _loaded_source_fingerprint = SessionField('loaded_fingerprint')
    current_gate = SessionField('view', 'gate')
    _processing_mode = SessionField('applied', 'mode')
    _processing_smooth = SessionField('applied', 'smooth')
    _processing_window_length = SessionField('applied', 'window_length')
    _processing_original_signal_title = SessionField('applied', 'original_signal_title')
    _title_settings = SessionField('view', 'title_settings')
    _color_scale_mode = SessionField('view', 'color_scale_mode')
    _color_region_boundaries = SessionField('view', 'color_region_boundaries')
    _last_valid_color_limits = SessionField('view', 'last_valid_color_limits')
    _last_valid_region_color_limits = SessionField('view', 'last_valid_region_color_limits')
    peak_indices = SessionField('peak_indices')
    _peak_gate_idx = SessionField('peak_gate_idx')
    peak_kind_lookup = SessionField('peak_kind_lookup')
    peak_label_lookup = SessionField('peak_label_lookup')
    batch_peak_map = SessionField('batch_peak_map')
    batch_peak_kind_lookup = SessionField('batch_peak_kind_lookup')
    fit_result = SessionField('fit_result')
    _fit_gate_idx = SessionField('fit_gate_idx')
    batch_fit_map = SessionField('batch_fit_map')
    batch_fit_result_map = SessionField('batch_fit_result_map')
    _batch_fit_export_rows = SessionField('fit_export_rows')
    _has_batch_fit_results = SessionField('has_batch_fit_results')
    _csv_seeds_map = SessionField('csv_seeds_map')
    _csv_seeds_path = SessionField('csv_seeds_path')

    def __init__(self):
        super().__init__()
        self.workspace = Workspace()
        self._dat_analysis_graph_results = {}
        self._project_path: Path | None = None
        self._project_saved_digest: str | None = None
        self._project_saved_stamp: str | None = None
        self._project_stamp_cache = {}
        # A coherent owner for existing fixtures/embedded uses that set a cube
        # directly before any source file is registered.
        self._unregistered_session = DatasetSession()
        self._restoring_dataset = False
        self._project_installing = False
        self.setWindowTitle("Spectra Analyzer (PySide6)")
        self.setMinimumSize(1000, 680)
        self.resize(1250, 860)
        self._sidebar_initial_sizing_pending = True

        # ── data state ──────────────────────────────────────────────────────
        self.current_root = ""
        self.current_folder = ""
        self.current_cube: DataCube | None = None
        self._base_cube: DataCube | None = None
        self._processing_mode = "original"
        self._processing_smooth = True
        self._processing_window_length = 21
        self._processing_original_signal_title: str | None = None
        self.current_dat_path: Path | None = None
        self.current_gate = 0.0
        self.peak_indices: np.ndarray = np.asarray([], dtype=int)
        self._peak_gate_idx: int | None = None
        self.peak_kind_lookup: dict[int, bool] = {}              # energy_idx → True=peak
        self.peak_label_lookup: dict[tuple[int, int], int] = {}  # (gate_idx, energy_idx) → label (1-based override)
        self.batch_peak_coords: tuple[np.ndarray, np.ndarray] | None = None
        self.batch_peak_records: list[tuple[int, int]] = []
        self.batch_peak_map: dict[int, np.ndarray] = {}
        self.batch_peak_kind_lookup: dict[tuple[int, int], bool] = {}  # (gate_idx, energy_idx) → True=peak
        self.fit_result: FitResult | None = None
        self._fit_gate_idx: int | None = None
        self.batch_fit_map: dict[int, np.ndarray] = {}
        self.batch_fit_coords: tuple[np.ndarray, np.ndarray] | None = None
        self.batch_fit_result_map: dict[int, FitResult] = {}
        self._batch_fit_export_rows: list[dict[str, str]] = []
        self._has_batch_fit_results: bool = False

        # ── UI lock helpers ──────────────────────────────────────────────────
        self._view_lock_controls: dict[QDoubleSpinBox, QCheckBox] = {}
        self._fit_lock_controls: dict[QDoubleSpinBox, QCheckBox] = {}
        self._view_callbacks_ready = False
        self._view_syncing = False
        self._heat_mesh = None
        self._heat_colorbar = None
        self._heat_meshes: list[Any] = []
        self._heat_colorbars: list[Any] = []
        self._heat_signal_label = None
        self._last_valid_color_limits: tuple[float, float] | None = None
        self._last_valid_region_color_limits: list[tuple[float, float]] = []
        self._color_scale_mode = "global"
        self._color_region_boundaries: list[float] = []

        # ── matplotlib axes/artists ──────────────────────────────────────────
        self._heat_ax = None
        self._spec_ax = None
        self._gate_line = None
        self._heat_peak_artists: list[Any] = []
        self._heat_peak_texts: list[Any] = []
        self._heat_batch_peak_artists: list[Any] = []
        self._heat_fit_artists: list[Any] = []

        # ── canvas event connection IDs ──────────────────────────────────────
        self._canvas_click_cid: int | None = None
        self._canvas_release_cid: int | None = None
        self._canvas_hover_cid: int | None = None
        self._motion_cid: int | None = None

        # ── circle delete state ──────────────────────────────────────────────
        self._batch_delete_circle_start: tuple[float, float] | None = None

        # ── lasso state ──────────────────────────────────────────────────────
        self._lasso_mode: bool = False
        self._lasso_selector: LassoSelector | None = None
        self._lasso_selected_peak_ids: list[tuple[int, int]] = []
        self._lasso_selection_patch: Any = None

        # ── drag state (peak dragging in spectrum plot) ──────────────────────
        self._drag_peak_arr_idx: int | None = None
        self._drag_current_x: float | None = None
        self._drag_scatter: Any = None  # scatter artist for drag feedback

        # ── second-derivative cache ──────────────────────────────────────────
        self._cached_d2: tuple[np.ndarray, np.ndarray] | None = None  # (xr, d2y)

        # ── CSV seeds for Peak Fit tab ───────────────────────────────────────
        self._csv_seeds_map: dict[int, list[float]] | None = None
        self._csv_seeds_path: str = ""

        self._annotation_source = None
        self._annotation_settings = QSettings("SpectraAnalyzer", "SpectraAnalyzer")
        self._recent_project_paths = []
        for value in self._annotation_settings.value('projects/recent', [], type=list):
            if isinstance(value, str) and value and Path(value).suffix.lower() in ('.spectra', '.spextra'):
                path = str(Path(value).resolve())
                if not any(canonical_path(item) == canonical_path(path) for item in self._recent_project_paths):
                    self._recent_project_paths.append(path)
        self._recent_project_paths = self._recent_project_paths[:10]
        self._project_modified_timer = QTimer(self)
        self._project_modified_timer.setSingleShot(True)
        self._project_modified_timer.setInterval(150)
        self._project_modified_timer.timeout.connect(self._refresh_project_modified)
        self._annotation_edit_timer = QTimer(self)
        self._annotation_edit_timer.setSingleShot(True)
        self._annotation_edit_timer.setInterval(250)
        self._annotation_edit_timer.timeout.connect(self._finish_annotation_edit)
        self._annotation_edit_pending = False
        self._title_source = None
        self._title_presets, self._default_title_settings = self._load_title_preferences()
        self._title_settings = self._copy_title_settings(self._default_title_settings)
        self._plot_state_store = PlotStateStore()
        self._loaded_source_fingerprint: str | None = None
        self._background_jobs = BackgroundJobs(self)
        self._background_jobs.idle.connect(self._on_background_idle)
        # Persistence owns detached snapshots and must never supersede a
        # scientific job. One bounded controller also owns shutdown lifetime.
        self._project_io_jobs = BackgroundJobs(self)
        self._project_io_jobs.idle.connect(self._on_project_io_idle)
        self._project_io_completion = None
        self._project_io_cancelled = False
        self._project_io_cancel_event = Event()
        self._project_io_committed = False
        self._project_io_finalizing = False
        self._project_io_close_pending = False
        self._project_revision = 0
        self._project_close_approved = False
        self._background_job_owner = None
        self._processing_request = None
        self._analysis_request = None
        self._analysis_return_key = None
        self._applied_derivative_sources = {}
        self._close_pending = False
        self._job_cancel_requested = False
        self._dat_import_token = 0
        self._dat_import_pending = False
        self._dat_import_enabled_states = {}
        self._pending_data_review = None
        self._data_generation = 0
        self._plot_revisions = {}
        self._graph_revisions = {}
        self._formal_peak_label_signatures = {}
        self._plot_lifetime = 0
        self._binding_graph_document = False
        self._last_comparison_id = None
        self._comparison_target_pinned = False
        self._plot_cube_owners = {}
        self._plot_settings_clipboard = None
        self._plot_settings_source_label = None
        self._plot_comparison_windows = []
        self._edit_histories = {}
        self._history_restoring = False
        self._history_depth = 0
        self._pending_history_edit = None
        self._build_ui()
        self._build_graph_commands()
        self._build_data_navigator()
        self._build_analysis_workbench()
        self._plot_render_timer = QTimer(self)
        self._plot_render_timer.setSingleShot(True)
        self._plot_render_timer.setInterval(180)
        self._plot_render_timer.timeout.connect(self._render_visible_plot_page)
        self._plot_format_store = FormatTemplateStore(self._annotation_settings)
        self._plot_batch = None
        self._plot_batch_entries = []
        self._plot_batch_basenames = ()
        self.plot_batch_summary = BatchSummary(0, ())
        self.plot_batch_report = ''
        self._plot_batch_timer = QTimer(self)
        self._plot_batch_timer.setSingleShot(True)
        self._plot_batch_timer.setInterval(0)
        self._plot_batch_timer.timeout.connect(self._step_plot_batch)
        self._refresh_plot_formats()
        self._job_cancel_button = QPushButton("Cancel operation")
        self._job_cancel_button.clicked.connect(self._cancel_background_job)
        self.statusBar().addPermanentWidget(self._job_cancel_button)
        self._job_cancel_button.hide()
        if self.fix_processing_chk.isChecked():
            self._set_processing_controls(self._fixed_recipe("processing"))
        if self.fix_regions_chk.isChecked():
            regions = self._fixed_recipe("regions")
            self._color_scale_mode = regions.get("mode", "global")
            with QSignalBlocker(self.color_scale_combo):
                self.color_scale_combo.setCurrentIndex(
                    self.color_scale_combo.findData(self._color_scale_mode))
        app = QApplication.instance()
        if not hasattr(app, "_page_wheel_filter"):
            app._page_wheel_filter = _PageWheelFilter(app)
            app.installEventFilter(app._page_wheel_filter)
        self.setAcceptDrops(True)
        self._connect_dataset_control_projection()
        self._connect_control_feedback()
        for edit in (*self._annotation_edits.values(), self.signal_title_combo.lineEdit(),
                     self.title_pattern_edit, self.title_angle_map_edit):
            if edit in (*self._annotation_edits.values(), self.signal_title_combo.lineEdit()):
                edit.editingFinished.connect(self._finish_annotation_edit)
            edit.editingFinished.connect(self._finish_history_edit)
        for spin in self._view_lock_controls:
            spin.editingFinished.connect(self._finish_history_edit)
        self._connect_project_modified_editors()
        # Select the startup view after its tab-change dependencies are ready.
        self.plot_tabs.setCurrentWidget(self.graph_documents)

    def _history_for(self, session):
        return self._edit_histories.setdefault(session.dataset_id, EditHistory())

    def _history_enabled(self):
        return (self.workspace.active is not None and not self._restoring_dataset
                and not self._history_restoring and not self._history_depth and not self._view_syncing)

    def _record_history(self, session, label, before, after, *, graph_id=None):
        changes = field_changes(before, after)
        if any(change.path[0] == 'presentation' for change in changes):
            # Deferred layout may grow paper after this command is recorded.
            # Retain its size endpoints so Undo restores the initiating edit;
            # Redo can measure the required paper again without a render edit.
            paths = {change.path for change in changes}
            for name in ('width_in', 'height_in'):
                path = ('presentation', name)
                if path not in paths:
                    changes += (FieldChange(path, getattr(before[('presentation',)], name),
                                            getattr(after[('presentation',)], name)),)
        self._history_for(session).record(EditCommand(label, changes,
            graph_id=graph_id or session.active_graph_id))
        self._refresh_graph_commands()
        self._schedule_project_modified()

    @contextmanager
    def _edit_transaction(self, label, names):
        """Capture before handlers mutate the session; nested refreshes are silent."""
        if not self._history_enabled():
            yield
            return
        pending = self._pending_history_edit
        if (pending and pending[0] is self.workspace.active and pending[2] == names
                and pending[4] == self.workspace.active.active_graph_id):
            # A numeric color commit emits valueChanged then its implicit Fix.
            # Both belong to the pending editor transaction.
            self._history_depth += 1
            try:
                yield
            finally:
                self._history_depth -= 1
            return
        self._finish_history_edit()
        session = self.workspace.active
        before = capture_fields(session, names)
        self._history_depth += 1
        try:
            yield
        except Exception:
            raise
        else:
            self._sync_formal_plot_view()
            self._record_history(session, label, before, capture_fields(session, names))
        finally:
            self._history_depth -= 1

    def _begin_history_text(self, label, names):
        if not self._history_enabled():
            return
        session = self.workspace.active
        pending = self._pending_history_edit
        if pending and (pending[0] is not session or pending[2] != names or pending[4] != session.active_graph_id):
            self._finish_history_edit()
            pending = None
        if pending is None:
            self._pending_history_edit = (session, label, names, capture_fields(session, names), session.active_graph_id)

    def _finish_history_edit(self):
        pending, self._pending_history_edit = self._pending_history_edit, None
        if pending is None:
            return
        session, label, names, before, graph_id = pending
        if graph_id not in session.graphs:
            return
        if session is self.workspace.active and graph_id == session.active_graph_id:
            self._capture_dataset_controls(visual_only=True)
        if self.workspace.datasets.get(session.dataset_id) is session:
            target = detached_graph_session(session)
            target.active_graph_id = graph_id
            self._record_history(session, label, before, capture_fields(target, names), graph_id=graph_id)

    def _clear_edit_history(self, session):
        if self._pending_history_edit and self._pending_history_edit[0] is session:
            self._pending_history_edit = None
        self._history_for(session).clear()
        self._refresh_graph_commands()

    def _history_busy(self):
        return (self._background_jobs.busy or self._project_io_jobs.busy or self._dat_import_pending
                or (getattr(self, '_plot_batch', None) is not None and not self._plot_batch.done))

    def undo_edit(self):
        if self._auxiliary_active():
            page = self.graph_documents.page_for(self.graph_documents._active_key)
            return isinstance(page, XYPlotPage) and not self._history_busy() and page.undo()
        return self._restore_history_command(redo=False)

    def redo_edit(self):
        if self._auxiliary_active():
            page = self.graph_documents.page_for(self.graph_documents._active_key)
            return isinstance(page, XYPlotPage) and not self._history_busy() and page.redo()
        return self._restore_history_command(redo=True)

    def _restore_history_command(self, *, redo):
        session = self.workspace.active
        if session is None or self._history_busy():
            self.statusBar().showMessage('Wait for the current operation before Undo / Redo.')
            return False
        try:
            self._finish_graph_editors(capture_science=False)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return False
        history = self._history_for(session)
        source = history.redo_stack if redo else history.undo_stack
        destination = history.undo_stack if redo else history.redo_stack
        if not source:
            self._refresh_graph_commands()
            return False
        command = source[-1]
        self._history_restoring = True
        try:
            self._cancel_graph_interaction()
            if command.graph_id is not None:
                if command.graph_id not in session.graphs:
                    raise ValueError('The edited graph no longer exists.')
                session.activate_graph(command.graph_id)
                self._select_graph_page(session)
            restore_fields(session, command.changes, redo=redo)
            peaks = command.peak_after if redo else command.peak_before
            if peaks:
                self._restore_history_peaks(session, peaks)
                self._invalidate_peak_fits(session, peaks)
            self._project_history_view(session)
            if peaks:
                self._invalidate_peak_documents(force=True)
            if peaks and self._current_gate_index() in peaks:
                gate_used, y = self._nearest_spectrum(self.current_gate)
                self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix='Manual')
            self._restore_history_defaults(session, command.changes)
            notice = self._save_current_plot_state()
            source.pop()
            destination.append(command)
        finally:
            self._history_restoring = False
        self._refresh_graph_commands()
        self.statusBar().showMessage(('Redo ' if redo else 'Undo ') + command.label
                                    + (f' — {session.active_graph.name}' if command.graph_id else '')
                                    + (f' — {notice}' if notice else ''))
        return True

    def _project_history_view(self, session, *, normalize_science=True, invalidate=True):
        """Project visual records without reapplying analysis or processing controls."""
        view = deepcopy(session.view)
        recipe = view.plot_recipe(session.current_cube, session.applied, session.presentation)
        self._restoring_dataset = True
        try:
            self._restore_plot_controls(recipe)
            self._sync_batch_peak_coords(normalize=normalize_science)
            self._sync_batch_fit_coords(normalize=normalize_science)
            self._plot_cube()
            session.view = view
            self._restore_effective_plot_limits(recipe)
            self._refresh_peak_delete_controls()
            self._refresh_export_actions()
        finally:
            self._restoring_dataset = False
        if invalidate:
            self._invalidate_plot_page(kind='presentation', sync=False)
        self._refresh_graph_selector()

    def _restore_history_defaults(self, session, changes):
        paths = [change.path for change in changes]
        settings = self._annotation_settings
        if ('view', 'fix_annotations') in paths:
            settings.setValue('plot_annotations/fixed', session.view.fix_annotations)
        if session.view.fix_annotations:
            if ('view', 'signal_title_auto') in paths:
                settings.setValue('plot_annotations/signal_auto', session.view.signal_title_auto)
            for path in paths:
                if path[:2] != ('view', 'annotations'):
                    continue
                field = path[2]
                if field == 'plot_title' and session.view.title_settings['mode'] == 'template':
                    continue
                settings.setValue('plot_annotations/' + field, session.view.annotations[field])
        self._sync_annotation_settings()

    @contextmanager
    def _peak_edit(self, label, gates):
        if self._history_depth or self._history_restoring or self._restoring_dataset:
            yield
            return
        record = self._history_enabled()
        if record:
            self._finish_history_edit()
        session = self.workspace.active or self._unregistered_session
        gates = set(gates)
        before = capture_peaks(session, gates)
        self._history_depth += 1
        try:
            yield
        except Exception:
            raise
        else:
            self._clean_edited_peaks(session, gates)
            after = capture_peaks(session, gates)
            changed = {gate for gate in gates if before[gate] != after[gate]}
            if changed:
                self._invalidate_peak_fits(session, changed)
                if record:
                    self._history_for(session).record(EditCommand(label,
                        peak_before={gate: before[gate] for gate in changed},
                        peak_after={gate: after[gate] for gate in changed}))
                # Bulk handlers may have drawn before their current trace was
                # reconciled with batch deletion. Finish with the cleaned maps.
                self._refresh_peak_delete_controls()
                self._update_spectrum_only()
                if self.current_cube is not None and self._current_gate_index() in changed:
                    gate_used, y = self._nearest_spectrum(self.current_gate)
                    self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix='Manual')
                self._refresh_graph_commands()
        finally:
            self._history_depth -= 1

    def _clean_edited_peaks(self, session, gates):
        valid = {(gi, int(ei)) for gi, indices in session.batch_peak_map.items() for ei in indices}
        trace_gate = session.peak_gate_idx
        # Batch deletion must also retire the corresponding current trace seeds.
        if trace_gate in gates and trace_gate in session.batch_peak_map:
            session.peak_indices = np.asarray(session.batch_peak_map[trace_gate], dtype=int).copy()
        elif trace_gate in gates and any(gi == trace_gate for gi, _ in session.batch_peak_kind_lookup):
            session.peak_indices = np.array([], dtype=int)
        if trace_gate in gates:
            indices = set(map(int, session.peak_indices))
            session.peak_kind_lookup = {ei: kind for ei, kind in session.peak_kind_lookup.items() if ei in indices}
            valid.update((trace_gate, ei) for ei in indices)
        session.batch_peak_kind_lookup = {key: kind for key, kind in session.batch_peak_kind_lookup.items()
                                         if key[0] not in gates or key in valid}
        session.peak_label_lookup = {key: label for key, label in session.peak_label_lookup.items()
                                     if key[0] not in gates or key in valid}
        self._sync_batch_peak_coords()

    def _restore_history_peaks(self, session, states):
        for gate, state in states.items():
            if state.batch is None:
                session.batch_peak_map.pop(gate, None)
            else:
                session.batch_peak_map[gate] = np.array(state.batch, dtype=int)
            for mapping, saved in ((session.batch_peak_kind_lookup, state.batch_kinds),
                                   (session.peak_label_lookup, state.labels)):
                for key in [key for key in mapping if key[0] == gate]:
                    del mapping[key]
                mapping.update({(gate, ei): value for ei, value in saved.items()})
        current = self._current_gate_index()
        if current in states:
            state = states[current]
            session.peak_indices = np.array(state.trace if state.trace is not None else state.batch or (), dtype=int)
            session.peak_gate_idx = current
            session.peak_kind_lookup = dict(state.trace_kinds if state.trace is not None else state.batch_kinds)
        elif session.peak_gate_idx in states:
            if current in session.batch_peak_map:
                session.peak_indices = session.batch_peak_map[current].copy()
                session.peak_gate_idx = current
                session.peak_kind_lookup = {int(ei): session.batch_peak_kind_lookup.get((current, int(ei)), True)
                                            for ei in session.peak_indices}
            else:
                gate = session.peak_gate_idx
                state = states[gate]
                session.peak_indices = np.array(state.trace or (), dtype=int)
                session.peak_kind_lookup = dict(state.trace_kinds)

    def _invalidate_peak_fits(self, session, gates):
        """Edited seeds retire their fits; Undo never resurrects historical analysis."""
        self._invalidate_analysis_jobs()
        for gate in gates:
            session.batch_fit_map.pop(gate, None)
            session.batch_fit_result_map.pop(gate, None)
        if session.fit_gate_idx in gates:
            session.fit_result = None
            session.fit_gate_idx = None
        values = {f'{float(session.current_cube.gate[gate]):.10g}' for gate in gates}
        session.fit_export_rows = [row for row in session.fit_export_rows if row.get('gate') not in values]
        session.has_batch_fit_results = bool(session.fit_export_rows)
        self._sync_batch_fit_coords()
        self._refresh_export_actions()
        self._sync_result_sheets(session.dataset_id)

    def _connect_control_feedback(self) -> None:
        for _, name, _, _ in ANALYSIS_BINDINGS:
            control = getattr(self, name)
            if isinstance(control, QAbstractSpinBox):
                control.setKeyboardTracking(False)
            signal = (control.toggled if isinstance(control, (QCheckBox, QRadioButton))
                      else control.currentTextChanged if isinstance(control, QComboBox)
                      else control.valueChanged)
            signal.connect(self._refresh_analysis_controls)
        for control in (self.processing_mode_combo, self.processing_smooth_chk,
                        self.processing_window_spin):
            signal = (control.currentIndexChanged if isinstance(control, QComboBox)
                      else control.toggled if isinstance(control, QCheckBox)
                      else control.valueChanged)
            signal.connect(self._refresh_processing_controls)
        self.plot_gate_index_spin.setKeyboardTracking(False)
        self.processing_window_spin.setKeyboardTracking(False)
        for spin in (self.peak_smooth_win_spin, self.fit_smooth_win_spin, self.d2_sg_win_spin):
            spin.setSingleStep(2)
        self._refresh_analysis_controls()
        self._refresh_processing_controls()

    def _connect_dataset_control_projection(self) -> None:
        controls = [getattr(self, name) for _, name, _, _ in ANALYSIS_BINDINGS]
        controls.extend([self.processing_mode_combo, self.processing_smooth_chk,
                         self.processing_window_spin, self.cmap_combo,
                         self.signal_title_combo, self.signal_title_auto_chk,
                         self.fix_annotations_chk, self.color_scale_combo,
                         *self._annotation_edits.values(),
                         *self._view_lock_controls.keys(), *self._view_lock_controls.values()])
        for control in controls:
            signal = (control.toggled if isinstance(control, (QCheckBox, QRadioButton))
                      else control.currentTextChanged if isinstance(control, QComboBox)
                      else control.textChanged if isinstance(control, QLineEdit)
                      else control.valueChanged)
            signal.connect(self._capture_dataset_controls)
            if control in (*self._view_lock_controls.keys(),
                           *self._view_lock_controls.values(), self.cmap_combo):
                signal.connect(self._invalidate_plot_page)
        self.analysis_text.textChanged.connect(self._capture_dataset_summary)

    def _capture_dataset_summary(self) -> None:
        if not self._restoring_dataset:
            analysis_owner(self.workspace.active or self._unregistered_session).summary = self.analysis_text.toPlainText()
            self._schedule_project_modified()

    def _read_analysis_controls(self) -> AnalysisControls:
        return AnalysisControls(**{field: getattr(getattr(self, name), getter)()
                                   for field, name, getter, _ in ANALYSIS_BINDINGS})

    def _capture_dataset_controls(self, *_args, visual_only=False) -> None:
        if self._restoring_dataset:
            return
        session = self.workspace.active or self._unregistered_session
        if self._workbench_document() is not None:
            # The workbench owns its scientific controls. Source format controls
            # are read-only here and must not capture stale widget projections.
            self._schedule_project_modified()
            return
        analysis = session.analysis_controls if visual_only else self._read_analysis_controls()
        # Visible X bounds also define detection inputs. Visual capture must
        # invalidate their jobs before replacing the previous bounds below.
        if (analysis != session.analysis_controls or any(
                session.view.bounds.get(name) != getattr(self, name + '_spin').value()
                for name in ('xmin', 'xmax'))):
            self._invalidate_analysis_jobs()
        if not visual_only:
            session.analysis_controls = analysis
            pending = ProcessingState(
                mode=str(self.processing_mode_combo.currentData() or 'original'),
                smooth=self.processing_smooth_chk.isChecked(),
                window_length=int(self.processing_window_spin.value()))
            if pending != session.pending:
                self._cancel_processing_job()
            session.pending = pending
        view = session.view
        view.colormap = self._selected_colormap()
        view.annotations = {**{name: edit.text() for name, edit in self._annotation_edits.items()},
                            'signal_title': self.signal_title_combo.currentText()}
        view.fix_annotations = self.fix_annotations_chk.isChecked()
        view.signal_title_auto = self.signal_title_auto_chk.isChecked()
        view.bounds = {name: getattr(self, f'{name}_spin').value()
                       for name in ('xmin', 'xmax', 'ymin', 'ymax', 'vmin', 'vmax')}
        view.locks = {name: getattr(self, f'{name}_lock').isChecked() for name in view.bounds}
        self._schedule_project_modified()

    def capture_active_dataset(self, *, render: bool = True) -> DatasetSession | None:
        self._finish_annotation_edit(render=render)
        self._capture_dataset_controls()
        self._finish_history_edit()
        self._capture_dataset_summary()
        session = self.workspace.active
        if session is not None and self._workbench_document() is None and self._heat_ax is not None and self._heat_mesh is not None:
            session.view.x_limits = tuple(map(float, self._heat_ax.get_xlim()))
            session.view.y_limits = tuple(map(float, self._heat_ax.get_ylim()))
            session.view.color_limits = tuple(map(float, self._heat_mesh.get_clim()))
        return session

    # Project snapshots use ordinary dataset records; Qt/runtime state stays here.
    def _project_busy(self) -> bool:
        batch = getattr(self, '_plot_batch', None)
        return (self._dat_import_pending or self._background_jobs.busy or self._project_io_jobs.busy
                or (batch is not None and not batch.done))

    def _require_project_idle(self) -> None:
        if self._project_busy():
            raise ValueError('Wait for the current operation or cancel it before changing projects.')

    def _capture_project_workspace(self) -> Workspace:
        if self._workbench_document() is not None:
            self._workbench_finish_editors()
        # Interpret all scientific editors before formal projection can replace
        # controls. Signals are blocked until every pending numeric value is read.
        controls = [control for control in self.findChildren(QAbstractSpinBox)
                    if control is not self.imported_dataset_rows_spin]
        previous_values = {control: control.value() for control in controls}
        for control in controls:
            if not control.hasAcceptableInput():
                raise ValueError('Correct the pending numeric editor before saving the project.')
        with ExitStack() as stack:
            for control in controls:
                stack.enter_context(QSignalBlocker(control))
                control.interpretText()
        changed = {control: control.value() for control in controls
                   if control.value() != previous_values[control]}
        self._commit_plot_numeric_edits(changed)
        if self.processing_window_spin in changed:
            self._remember_fixed_processing()
        self._finish_bound_plot_editor()
        for key in self.graph_documents.keys():
            if len(key) == 3 and key[0] == 'xy':
                self.graph_documents.page_for(key).finish_editor()
        self.capture_active_dataset(render=False)
        self.plot_page.cancel_interaction()
        self.workspace.graph_workspace = self._graph_workspace_snapshot()
        return self.workspace

    def _update_project_title(self) -> None:
        title = 'Spectra Analyzer (PySide6)'
        if self._project_path is not None:
            title += ' — ' + self._project_path.name
        if self.isWindowModified():
            title += ' *'
        self.setWindowTitle(title)
        self.setWindowFilePath(str(self._project_path or ''))
        self.statusBar().setToolTip(str(self._project_path or ''))

    def _schedule_project_modified(self, *_args) -> None:
        if not self._restoring_dataset and not self._project_installing:
            self._project_revision += 1
            self._project_modified_timer.start()

    def _connect_project_modified_editors(self) -> None:
        # Explicit durable controls; search, sidebar state and Rows are preferences.
        self._project_numeric_editors = [getattr(self, name) for _, name, _, _ in ANALYSIS_BINDINGS
                                         if isinstance(getattr(self, name), QAbstractSpinBox)]
        self._project_numeric_editors.extend((self.processing_window_spin, self.plot_gate_spin,
            self.plot_gate_index_spin, self.linthresh_spin, *self._view_lock_controls,
            *self.region_boundary_spins))
        self._project_numeric_editors.extend(self.workbench_controls.findChildren(QAbstractSpinBox))
        for control in self._project_numeric_editors:
            control.lineEdit().textChanged.connect(self._schedule_project_modified)
            control.valueChanged.connect(self._schedule_project_modified)
        for control in (self.fix_processing_chk, self.fix_regions_chk, self.fix_region_edges_chk):
            control.toggled.connect(self._schedule_project_modified)

    @staticmethod
    def _project_spin_has_draft(control) -> bool:
        if not control.isEnabled():
            return False
        return (not control.hasAcceptableInput()
                or control.valueFromText(control.text()) != control.value())

    def _project_marker_workspace(self) -> Workspace:
        """Borrow scientific buffers and inspect active controls without committing."""
        datasets = dict(self.workspace.datasets)
        active = self.workspace.active
        if active is not None and active.active_graph.analysis is None:
            session = detached_graph_session(active)
            view = session.view
            session.view = view
            session.analysis_controls = self._read_analysis_controls()
            session.pending = ProcessingState(mode=str(self.processing_mode_combo.currentData() or 'original'),
                smooth=self.processing_smooth_chk.isChecked(), window_length=int(self.processing_window_spin.value()))
            session.summary = self.analysis_text.toPlainText()
            view.colormap = self._selected_colormap()
            view.annotations = {**{name: edit.text() for name, edit in self._annotation_edits.items()},
                                'signal_title': self.signal_title_combo.currentText()}
            view.fix_annotations = self.fix_annotations_chk.isChecked()
            view.signal_title_auto = self.signal_title_auto_chk.isChecked()
            view.bounds = {name: getattr(self, name + '_spin').value() for name in active.view.bounds}
            view.locks = {name: getattr(self, name + '_lock').isChecked() for name in active.view.bounds}
            if self._heat_ax is not None and self._heat_mesh is not None:
                view.x_limits = tuple(map(float, self._heat_ax.get_xlim()))
                view.y_limits = tuple(map(float, self._heat_ax.get_ylim()))
                view.color_limits = tuple(map(float, self._heat_mesh.get_clim()))
            datasets[session.dataset_id] = session
        return Workspace(datasets=datasets, active_dataset_id=self.workspace.active_dataset_id,
                         graph_workspace=self._graph_workspace_snapshot(),
                         workbooks=self.workspace.workbooks, sheets=self.workspace.sheets,
                         comparisons=self.workspace.comparisons)

    def _graph_workspace_snapshot(self):
        keys = [(session.dataset_id, graph_id) for session in self.workspace.datasets.values()
                for graph_id, graph in session.graphs.items() if graph.published]
        keys.extend(('workbook', identity, '') for identity in self.workspace.workbooks)
        keys.extend(('xy', identity, '') for identity in self.workspace.comparisons)
        return self.graph_documents.workspace_snapshot(keys)

    def _refresh_project_modified(self) -> None:
        from .project_io import runtime_workspace_stamp
        if self._project_installing or self._restoring_dataset:
            return
        if self._project_io_jobs.busy:
            # The detached worker owns archive hashing. Defer the ordinary
            # title marker so large live Sheet buffers do not stall I/O UI.
            self._project_modified_timer.start()
            return
        dirty = False
        if (self.workspace.datasets or self.workspace.workbooks or self.workspace.comparisons
                or self._project_saved_stamp is not None):
            try:
                dirty = (runtime_workspace_stamp(self._project_marker_workspace(), self._project_stamp_cache)
                         != self._project_saved_stamp)
                if self.workspace.datasets:
                    dirty |= any(self._project_spin_has_draft(control) for control in self._project_numeric_editors)
                    dirty |= self.plot_page.has_pending_project_edits()
                dirty |= any(isinstance(page, XYPlotPage) and page.has_pending_project_edits()
                             for page in self.graph_documents._pages.values())
            except (ValueError, TypeError, AttributeError, OverflowError):
                # Invalid drafts are visible as unsaved; exact capture explains
                # their validation error at Save/Open/New/Close.
                dirty = True
        self.setWindowModified(bool(dirty))
        self._update_project_title()

    def _mark_project_saved(self) -> None:
        from .project_io import runtime_workspace_stamp
        self._project_stamp_cache.clear()
        self._project_saved_stamp = runtime_workspace_stamp(self._project_marker_workspace(), self._project_stamp_cache)
        self._project_modified_timer.stop()
        self.setWindowModified(False)
        self._update_project_title()

    def _remember_recent_project(self, path: Path) -> None:
        value = str(path.resolve())
        self._recent_project_paths = [value, *(item for item in self._recent_project_paths
                                             if canonical_path(item) != canonical_path(value))][:10]
        self._annotation_settings.setValue('projects/recent', self._recent_project_paths)
        self._refresh_recent_projects()

    def _clear_recent_projects(self) -> None:
        self._recent_project_paths = []
        self._annotation_settings.remove('projects/recent')
        self._refresh_recent_projects()

    def _refresh_recent_projects(self) -> None:
        if not hasattr(self, '_recent_projects_menu'):
            return
        menu = self._recent_projects_menu
        menu.clear()
        for value in self._recent_project_paths:
            path = Path(value)
            action = menu.addAction(f'{path.name} — {path.parent}')
            action.setData(value)
            action.setToolTip(str(path))
            action.triggered.connect(lambda checked=False, path=path: self._open_project_path(path))
        if not self._recent_project_paths:
            menu.addAction('No recent projects').setEnabled(False)
        menu.addSeparator()
        clear = menu.addAction('Clear Recent Projects', self._clear_recent_projects)
        clear.setEnabled(bool(self._recent_project_paths))

    def _open_project_path(self, path: str | Path) -> bool:
        """All user entry routes share project validation, guard and reporting."""
        try:
            return self._open_project_async(path)
        except Exception as exc:
            self._show_error(f'Cannot open project: {exc}')
            return False

    def save_project(self, path: str | Path | None = None) -> Path | None:
        """Atomically save the captured workspace; cancellation returns None."""
        from .project_io import write_project
        self._require_project_idle()
        if path is None:
            path = self._project_path
        if path is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self, 'Save Spectra project', str(self._output_folder() / 'Untitled.spectra'),
                'Spectra projects (*.spectra)')
            if not chosen:
                return None
            path = chosen
        path = Path(path).resolve()
        if path.suffix.lower() not in ('.spectra', '.spextra'):
            path = path.with_name(path.name + '.spectra')
        workspace = self._capture_project_workspace()
        self._validate_project_colors(workspace)
        digest = write_project(path, workspace)
        # Ownership and baseline change only after the atomic replacement.
        self._project_path, self._project_saved_digest = path, digest
        for session in workspace.datasets.values():
            session.sidecar_writes_enabled = False
        self._refresh_source_axis_control()
        self._mark_project_saved()
        self._remember_recent_project(path)
        self.statusBar().showMessage(f'Saved project: {path}')
        return path

    def _validate_project_colors(self, workspace):
        # The viewer cannot project an invalid committed color mapping. Check
        # that same display boundary before writing; pending analysis controls
        # and missing scientific values remain valid project contents.
        for session in workspace.datasets.values():
            for graph in session.graphs.values():
                target = detached_graph_session(session)
                target.active_graph_id = graph.graph_id
                view = target.view
                try:
                    limits = ([region.color_limits for region in snapshot_dataset(target).regions]
                              if view.color_scale_mode in ('2', '3') else
                              [tuple(sorted((view.bounds['vmin'], view.bounds['vmax']))), view.color_limits])
                    for pair in limits:
                        build_color_norm(view.norm, pair)
                except ValueError as exc:
                    raise ValueError(f'Correct the color mapping for {session.source_path.name} — {graph.name}: {exc}') from exc

    @staticmethod
    def _detached_project_snapshot(workspace):
        # Undo commands can retain retired analysis buffers. They are transient
        # and must never be cloned merely to save durable project records.
        memo = {id(history): None for comparison in workspace.comparisons.values()
                if (history := getattr(comparison, '_edit_history', None)) is not None}
        snapshot = deepcopy(workspace, memo)
        for comparison in snapshot.comparisons.values():
            if hasattr(comparison, '_edit_history'):
                del comparison._edit_history
        return snapshot

    def _start_project_io(self, work, apply, *, error_prefix, commits=False, on_error=None):
        self._require_project_idle()
        self._project_io_cancelled = False
        self._project_io_committed = False
        cancelled = self._project_io_cancel_event = Event()
        self._project_io_completion = None

        def result(value):
            self._project_io_committed = commits
            self._project_io_completion = lambda: apply(value)

        def failed(exc):
            def report():
                if on_error is not None:
                    on_error(exc)
                else:
                    self._show_error(f'{error_prefix}: {exc}')
                    self._schedule_plot_page_render()
            self._project_io_completion = report

        # Keep the controller's delivery alive after an atomic save commits.
        # A separate cancellation token is checked before replacement; the
        # generic job's post-work cancellation check cannot hide that commit.
        def run(context):
            return work(JobContext(cancelled, context.report))

        self._project_io_jobs.start(run, result, on_error=failed)
        self._job_cancel_button.show()
        self._refresh_graph_commands()

    def _on_project_io_idle(self):
        self._project_io_finalizing = False
        completion, self._project_io_completion = self._project_io_completion, None
        self._job_cancel_button.hide()
        self._refresh_graph_commands()
        if self._project_io_cancelled and not self._project_io_committed:
            self._project_io_cancelled = False
            self.statusBar().showMessage('Project operation cancelled. Current workspace retained.')
            self._schedule_plot_page_render()
            if self._project_io_close_pending:
                self._project_io_close_pending = False
                QTimer.singleShot(0, self, self.close)
            return
        if completion is not None:
            try:
                completion()
            except Exception as exc:
                self._show_error(f'Cannot complete project operation: {exc}')
        if self._project_io_close_pending and not self._project_io_jobs.busy:
            self._project_io_close_pending = False
            QTimer.singleShot(0, self, self.close)

    def _save_project_async(self, path=None, *, on_saved=None):
        from .project_io import write_project, runtime_workspace_stamp
        self._require_project_idle()
        path = path or self._project_path
        if path is None:
            chosen, _ = QFileDialog.getSaveFileName(self, 'Save Spectra project',
                str(self._output_folder() / 'Untitled.spectra'), 'Spectra projects (*.spectra)')
            if not chosen:
                return False
            path = chosen
        path = Path(path).resolve()
        if path.suffix.lower() not in ('.spectra', '.spextra'):
            path = path.with_name(path.name + '.spectra')
        workspace = self._capture_project_workspace()
        self._validate_project_colors(workspace)
        # GUI editors are committed before copying. The worker owns every
        # buffer it serializes, including mutable peak and Sheet arrays.
        snapshot = self._detached_project_snapshot(workspace)
        revision = self._project_revision

        def work(context):
            context.report(f'Saving project: {path.name}…')
            stamp = runtime_workspace_stamp(snapshot, {})
            digest = write_project(path, snapshot, check_cancelled=context.check_cancelled)
            return digest, stamp

        def apply(result):
            if self.workspace is not workspace:
                return
            self._project_path, self._project_saved_digest = path, result[0]
            self._project_saved_stamp = result[1]
            for session in workspace.datasets.values():
                session.sidecar_writes_enabled = False
            self._refresh_source_axis_control()
            self._project_modified_timer.stop()
            self.setWindowModified(self._project_revision != revision)
            self._update_project_title()
            self._remember_recent_project(path)
            cancelled = self._project_io_cancel_event.is_set()
            self.statusBar().showMessage(f'Saved project: {path}' +
                (' — cancellation arrived after the file was saved.' if cancelled else ''))
            if on_saved is not None and not cancelled:
                # An edit made while compression ran belongs to the live
                # workspace, not the successfully written older snapshot.
                self._confirm_project_discard_async(on_saved)

        self._start_project_io(work, apply, error_prefix='Cannot save project', commits=True)
        return True

    def _confirm_project_discard_async(self, continuation):
        from .project_io import workspace_digest
        self._require_project_idle()
        try:
            workspace = self._capture_project_workspace()
            if not (workspace.datasets or workspace.workbooks or workspace.comparisons) and self._project_saved_digest is None:
                continuation()
                return
            snapshot = self._detached_project_snapshot(workspace)
            revision = self._project_revision
        except Exception as exc:
            self._ask_project_discard(continuation, str(exc))
            return

        def work(context):
            context.check_cancelled()
            return workspace_digest(snapshot)

        def apply(digest=None, *, error=None):
            if self.workspace is not workspace:
                return
            if self._project_revision != revision:
                self._confirm_project_discard_async(continuation)
            elif error is not None:
                self._ask_project_discard(continuation, str(error))
            elif digest == self._project_saved_digest:
                continuation()
            else:
                self._ask_project_discard(continuation)

        self._start_project_io(work, apply, error_prefix='Cannot inspect current project',
            on_error=lambda exc: apply(error=exc))

    def _ask_project_discard(self, continuation, capture_error=None):
        message = 'Save changes to the current project before continuing?'
        if capture_error:
            message += '\n\nThe current edits cannot be saved yet: ' + capture_error
        choice = QMessageBox.warning(self, 'Unsaved project changes', message,
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
        if choice == QMessageBox.Discard:
            continuation()
        elif choice == QMessageBox.Save:
            try:
                if not self._save_project_async(on_saved=continuation):
                    self._schedule_plot_page_render()
            except Exception as exc:
                self._show_error(f'Could not save project: {exc}')
                self._schedule_plot_page_render()
        else:
            self._schedule_plot_page_render()

    def _open_project_async(self, path):
        from .project_io import read_project
        self._require_project_idle()
        path = Path(path).resolve()
        owner = self.workspace

        def work(context):
            context.report(f'Opening project: {path.name}…')
            candidate = read_project(path, check_cancelled=context.check_cancelled)
            return candidate

        def apply(result):
            if self.workspace is owner:
                self._confirm_project_discard_async(
                    lambda: self._install_project(result, path, async_baseline=True))

        self._start_project_io(work, apply, error_prefix='Cannot open project')
        return True

    def _confirm_project_discard(self) -> bool:
        """Exact capture/digest guard shared by Open, New and Close."""
        from .project_io import workspace_digest
        capture_error = None
        try:
            workspace = self._capture_project_workspace()
            if not (workspace.datasets or workspace.workbooks or workspace.comparisons) and self._project_saved_digest is None:
                return True
            if workspace_digest(workspace) == self._project_saved_digest:
                return True
        except Exception as exc:
            capture_error = str(exc)
        message = 'Save changes to the current project before continuing?'
        if capture_error:
            message += '\n\nThe current edits cannot be saved yet: ' + capture_error
        choice = QMessageBox.warning(self, 'Unsaved project changes', message,
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
        if choice == QMessageBox.Discard:
            return True
        if choice != QMessageBox.Save:
            return False
        try:
            return self.save_project() is not None
        except Exception as exc:
            self._show_error(f'Could not save project: {exc}')
            return False

    def _reset_project_runtime(self) -> None:
        self._dat_analysis_graph_results.clear()
        self._analysis_return_key = None
        self._last_comparison_id = None
        self._comparison_target_pinned = False
        with QSignalBlocker(self.workbook_commands.pin_action):
            self.workbook_commands.pin_action.setChecked(False)
        self._plot_lifetime += 1
        self._bind_plot_page(self._fallback_plot_page)
        for key in self.graph_documents.keys():
            self.graph_documents.discard(key)
        self.graph_documents.reset_layout()
        self._graph_revisions.clear()
        self._formal_peak_label_signatures.clear()
        self._background_jobs.cancel()
        self._background_job_owner = self._processing_request = self._analysis_request = None
        self._job_cancel_requested = self._close_pending = False
        self._dat_import_token += 1
        self._data_generation += 1
        self._annotation_edit_timer.stop()
        self._project_modified_timer.stop()
        self._annotation_edit_pending = False
        for name in ('_plot_render_timer', '_plot_batch_timer', '_view_layout_timer', '_tick_contrast_timer'):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        self._pending_history_edit = None
        self._edit_histories.clear()
        self._plot_revisions.clear()
        self._plot_cube_owners.clear()
        self._applied_derivative_sources.clear()
        self._annotation_source = self._title_source = None
        self._annotation_auto_frame = (None, None)
        self._annotation_layout_pending = False
        self._clear_dataset_interactions()
        self.batch_peak_coords = self.batch_fit_coords = None
        self.batch_peak_records = []
        self._plot_batch = None
        self._plot_batch_entries = []
        self._plot_batch_basenames = ()
        self.plot_batch_summary = BatchSummary(0, ())
        self.plot_batch_report = ''
        self.plot_page.batch_progress.setVisible(False)
        self.plot_page.batch_cancel_btn.setVisible(False)
        self.plot_page.batch_results_btn.setEnabled(False)
        self.plot_page.batch_status.clear()
        self.plot_page.batch_export_btn.setEnabled(True)
        self.plot_page.preview.cancel_interaction()
        self.plot_page.preview.selected_id = None
        self.plot_page.preview._title_selected = False
        self.plot_page.preview.snapshot = None
        self._job_cancel_button.hide()

    def _project_installed_workspace(self) -> None:
        self._refresh_imported_datasets()
        self._refresh_data_navigator()
        self._refresh_comparison_targets()
        for session in self.workspace.datasets.values():
            if session.applied.mode == 'energy_second_derivative':
                self._applied_derivative_sources[session.dataset_id] = (
                    id(session.base_cube), id(session.current_cube))
        if self.workspace.active_dataset_id is not None:
            self.activate_dataset(self.workspace.active_dataset_id, capture_outgoing=False)
        else:
            self._clear_empty_project_view()
        self._refresh_source_axis_control()

    def _restore_graph_workspace(self, metadata):
        if metadata is None:
            self.graph_documents.free_view()
            return
        # Cache open pages without activating their datasets or changing each
        # dataset's selected graph. The Analysis owner and last MDI owner can
        # legitimately differ.
        for record in metadata['documents']:
            if not record['open']:
                continue
            key = document_record_key(record)
            if self.graph_documents.page_for(key) is None:
                if len(key) == 3:
                    page = self._create_auxiliary_page(key)
                else:
                    page = self._create_plot_page(key)
                    page.set_presentation(self.workspace.datasets[key[0]].graphs[key[1]].presentation)
                    page.invalidate(self._plot_owner_token(key))
                self.graph_documents.cache_page(key, page)
        self.graph_documents.restore_workspace(metadata, self._document_title)
        active = tuple(metadata['active']) if metadata['active'] else None
        if active is not None and len(active) == 3:
            self._last_comparison_id = active[1] if active[0] == 'xy' else None
            self.plot_tabs.setCurrentWidget(self.graph_documents)
            self._on_plot_tab_changed(self.plot_tabs.currentIndex())
        elif (metadata['stacking'] and metadata['active'] != list(self._document_key())
                and self.plot_tabs.currentWidget() is self.graph_documents):
            # An Analysis selection can differ from the last active graph.
            # Preserve both saved owners, showing the scientific selection in
            # Analysis until the user explicitly selects a graph again.
            self.plot_tabs.setCurrentIndex(0)
        if self._workbench_document() is not None:
            self._workbench_enter(self.workspace.active)
        self._refresh_graph_commands()

    def open_project(self, path: str | Path) -> bool:
        """Validate a detached project, guard current work, then install it."""
        from .project_io import read_project, workspace_digest
        self._require_project_idle()
        path = Path(path).resolve()
        candidate = read_project(path)
        if not self._confirm_project_discard():
            return False
        return self._install_project(candidate, path)

    def _install_project(self, candidate, path, *, baseline=None, async_baseline=False):
        from .project_io import workspace_digest
        previous = (self.workspace, self._unregistered_session, self._project_path,
                    self._project_saved_digest, self.current_folder, self.current_root)
        previous_histories = dict(self._edit_histories)
        previous_target = (self._last_comparison_id, self._comparison_target_pinned)
        previous_plot_tab = self.plot_tabs.currentIndex()
        self._bind_plot_page(self._fallback_plot_page)
        previous_documents = self.graph_documents.park()
        previous_render_state = (deepcopy(self._plot_revisions), deepcopy(self._graph_revisions),
                                 dict(self._plot_cube_owners), dict(self._formal_peak_label_signatures))
        self._reset_project_runtime()
        self.workspace = candidate
        self._unregistered_session = DatasetSession()
        try:
            self._project_installing = True
            self._project_installed_workspace()
            self._restore_graph_workspace(candidate.graph_workspace)
            captured = self._capture_project_workspace()
            if baseline is None and not async_baseline:
                baseline = workspace_digest(captured)
        except Exception:
            self._project_installing = False
            (self.workspace, self._unregistered_session, self._project_path,
             self._project_saved_digest, self.current_folder, self.current_root) = previous
            self._reset_project_runtime()
            self._edit_histories.update(previous_histories)
            (self._plot_revisions, self._graph_revisions, self._plot_cube_owners,
             self._formal_peak_label_signatures) = previous_render_state
            self.graph_documents.restore(previous_documents)
            for key, page in previous_documents[0].items():
                if len(key) == 3:
                    continue
                owner = self._plot_owner_token(key)
                page.expected_owner = owner
                if page.rendered_page is not None:
                    page.preview.set_geometry(replace(page.preview.snapshot,
                        activation_epoch=self._plot_lifetime), page.presentation)
                    page.commit_rendered_page(replace(page.rendered_page, owner_token=owner))
            for session in self.workspace.datasets.values():
                if session.applied.mode == 'energy_second_derivative':
                    self._applied_derivative_sources[session.dataset_id] = (
                        id(session.base_cube), id(session.current_cube))
            # Use the shared projection directly so an injected/failed
            # activation hook cannot prevent rollback of the prior owner.
            if self.workspace.active_dataset_id is not None:
                MainWindow.activate_dataset(self, self.workspace.active_dataset_id, capture_outgoing=False)
            else:
                self._clear_empty_project_view()
            # Projection can reopen a selected-but-closed page. Restore the
            # exact visible set after its editor has been rebound.
            open_keys = {record[0] for record in previous_documents[1]}
            for key in self.graph_documents.keys():
                if key not in open_keys:
                    self.graph_documents._detach_window(key)
            self.plot_tabs.setCurrentIndex(previous_plot_tab)
            # Reapply normal rectangles/stacking after selected-owner
            # projection, which may have briefly opened a closed graph.
            self.graph_documents.restore(previous_documents)
            self._last_comparison_id, self._comparison_target_pinned = previous_target
            self._refresh_comparison_targets()
            self._refresh_data_navigator()
            self._on_plot_tab_changed(self.plot_tabs.currentIndex())
            self._refresh_graph_commands()
            self._refresh_source_axis_control()
            self._update_project_title()
            raise
        finally:
            self._project_installing = False
        self.graph_documents.discard_parked(previous_documents)
        self._project_path, self._project_saved_digest = path, baseline
        self.current_root, self.current_folder = '', str(path.parent)
        with QSignalBlocker(self.folder_combo), QSignalBlocker(self.imported_dataset_search):
            self.folder_combo.clear()
            self.imported_dataset_search.clear()
        self.file_list.clear()
        self._refresh_imported_datasets()
        self._refresh_data_navigator()
        if async_baseline:
            # Reachable MDI rectangles and active editor projections can differ
            # from archive metadata. Hash their actual installed baseline in
            # the worker, rather than retaining the pre-install file digest.
            from .project_io import runtime_workspace_stamp
            workspace = self.workspace
            snapshot = self._detached_project_snapshot(self._capture_project_workspace())
            revision = self._project_revision

            def work(context):
                return workspace_digest(snapshot), runtime_workspace_stamp(snapshot, {})

            def finish(result):
                if self.workspace is workspace:
                    self._project_saved_digest, self._project_saved_stamp = result
                    self._project_modified_timer.stop()
                    self.setWindowModified(self._project_revision != revision)
                    self._update_project_title()
                    self._remember_recent_project(path)
                    self.statusBar().showMessage(f'Opened project: {path}')

            self._start_project_io(work, finish, error_prefix='Cannot finalize opened project', commits=True)
            # Installation has committed successfully. Complete its saved
            # baseline before accepting another project transition.
            self._project_io_finalizing = True
            self._job_cancel_button.hide()
        else:
            self._mark_project_saved()
            self._remember_recent_project(path)
            self.statusBar().showMessage(f'Opened project: {path}')
        return True

    def _clear_empty_project_view(self) -> None:
        self._refresh_graph_selector()
        # _plot_cube returns early for None and leaves the old image visible.
        for name in ('_canvas_click_cid', '_canvas_release_cid', '_canvas_hover_cid', '_motion_cid'):
            cid = getattr(self, name)
            if cid is not None:
                self.canvas.mpl_disconnect(cid)
            setattr(self, name, None)
        self._view_callbacks_ready = False
        self.figure.clear()
        self._heat_ax = self._spec_ax = self._heat_mesh = self._heat_colorbar = None
        self._gate_line = self._heat_signal_label = None
        for name in ('_heat_meshes', '_heat_colorbars', '_heat_peak_artists', '_heat_peak_texts',
                     '_heat_batch_peak_artists', '_heat_fit_artists'):
            setattr(self, name, [])
        self.canvas.draw_idle()
        self._restoring_dataset = True
        try:
            self.analysis_text.clear()
            self.plot_page.set_presentation(self._unregistered_session.presentation)
            with ExitStack() as stack:
                widgets = [getattr(self, name) for _, name, _, _ in ANALYSIS_BINDINGS]
                widgets += [self._orig_radio, self._seeds_from_find_peak_radio,
                            self.processing_mode_combo, self.processing_smooth_chk,
                            self.processing_window_spin, self.plot_gate_spin,
                            self.fix_processing_chk, self.fix_regions_chk, self.fix_region_edges_chk,
                            self.cmap_combo,
                            self.mapping_combo, self.linthresh_spin, self.color_scale_combo,
                            self.signal_title_auto_chk, self.fix_annotations_chk,
                            *self._annotation_edits.values(), self.signal_title_combo,
                            *self._view_lock_controls.keys(), *self._view_lock_controls.values()]
                for widget in widgets:
                    stack.enter_context(QSignalBlocker(widget))
                for field, name, _, setter in ANALYSIS_BINDINGS:
                    getattr(getattr(self, name), setter)(getattr(self._unregistered_session.analysis_controls, field))
                self._orig_radio.setChecked(True)
                self._seeds_from_find_peak_radio.setChecked(True)
                self.processing_mode_combo.setCurrentIndex(self.processing_mode_combo.findData('original'))
                self.processing_smooth_chk.setChecked(True)
                self.processing_window_spin.setValue(21)
                for name, checkbox in (('processing', self.fix_processing_chk), ('regions', self.fix_regions_chk),
                                       ('region_edges', self.fix_region_edges_chk)):
                    checkbox.setChecked(self._unregistered_session.fixed_recipes[name].enabled)
                self.cmap_combo.setCurrentIndex(self.cmap_combo.findData(self._unregistered_session.view.colormap))
                self.mapping_combo.setCurrentIndex(self.mapping_combo.findData('linear'))
                self.linthresh_spin.setValue(self._unregistered_session.view.norm.linthresh)
                self.color_scale_combo.setCurrentIndex(self.color_scale_combo.findData('global'))
                self.signal_title_auto_chk.setChecked(True)
                self.fix_annotations_chk.setChecked(True)
                for name, edit in self._annotation_edits.items():
                    edit.setText(self._unregistered_session.view.annotations[name])
                self.signal_title_combo.setCurrentText(RAW_LABEL)
                for name in self._unregistered_session.view.bounds:
                    getattr(self, name + '_spin').setValue(0)
                    getattr(self, name + '_lock').setChecked(False)
                self.plot_gate_spin.setRange(-1e12, 1e12)
                self.plot_gate_spin.setValue(0)
            self._csv_path_label.setText('(none loaded)')
            self._csv_seeds_row.setVisible(False)
            self._sync_gate_controls()
            self.plot_prev_gate_btn.setEnabled(False)
            self.plot_next_gate_btn.setEnabled(False)
            self._show_title_settings()
            self._sync_region_controls()
        finally:
            self._restoring_dataset = False
        self.plot_page.preview.set_geometry(None, self._unregistered_session.presentation)
        self.plot_page.invalidate(self._plot_owner_token(), 'No project datasets. Import data files to begin.')
        self._refresh_axis_status()
        self._update_fit_param_unit_labels()
        self._refresh_analysis_controls()
        self._refresh_processing_controls()
        self._refresh_export_actions()
        self._refresh_peak_delete_controls()
        self._refresh_fit_range_summary()
        self._update_lasso_actions()
        self._refresh_transfer_controls()

    def new_project(self) -> bool:
        self._require_project_idle()
        if not self._confirm_project_discard():
            return False
        return self._new_project_unchecked()

    def _new_project_unchecked(self):
        self._reset_project_runtime()
        self.workspace = Workspace()
        self._dat_analysis_graph_results = {}
        self._unregistered_session = DatasetSession()
        self._project_path = self._project_saved_digest = None
        self._project_saved_stamp = None
        self._project_stamp_cache.clear()
        self.setWindowModified(False)
        self.current_root = self.current_folder = ''
        with QSignalBlocker(self.folder_combo), QSignalBlocker(self.imported_dataset_search):
            self.folder_combo.clear()
            self.imported_dataset_search.clear()
        self.file_list.clear()
        self._project_installed_workspace()
        self._update_project_title()
        self.statusBar().showMessage('New project. Import data files to begin.')
        return True

    def _on_new_project(self) -> None:
        try:
            self._confirm_project_discard_async(self._new_project_unchecked)
        except Exception as exc:
            self._show_error(f'Cannot create a new project: {exc}')

    def _on_open_project(self) -> None:
        try:
            self._require_project_idle()
            chosen, _ = QFileDialog.getOpenFileName(self, 'Open Spectra project',
                str(self._output_folder()), 'Spectra projects (*.spectra *.spextra)')
            if chosen:
                self._open_project_path(chosen)
        except Exception as exc:
            self._show_error(f'Cannot open project: {exc}')

    def _on_save_project(self, *, save_as=False) -> None:
        try:
            self._require_project_idle()
            if save_as:
                default = (self._project_path.with_suffix('.spectra') if self._project_path else
                           self._output_folder() / 'Untitled.spectra')
                chosen, _ = QFileDialog.getSaveFileName(self, 'Save Spectra project as',
                    str(default), 'Spectra projects (*.spectra)')
                if not chosen:
                    return
                self._save_project_async(chosen)
            else:
                self._save_project_async()
        except Exception as exc:
            self._show_error(f'Cannot save project: {exc}')

    def _output_folder(self, session: DatasetSession | None = None) -> Path:
        session = session or self.workspace.active or self._unregistered_session
        if self._project_path is not None and (not session.sidecar_writes_enabled or session.source_path is None):
            return self._project_path.parent
        return session.source_path.parent if session.source_path else Path(self.current_folder or '.')

    def _refresh_source_axis_control(self) -> None:
        if not hasattr(self, 'x_axis_override_combo'):
            return
        session = self.workspace.active or self._unregistered_session
        source_owned = session.sidecar_writes_enabled
        self.x_axis_override_combo.setEnabled(source_owned and not self._dat_import_pending
                                             and not self._background_jobs.busy)
        self.x_axis_override_combo.setToolTip(
            "Override the auto-detected x-axis type. Use 'Auto Detect' unless the detection is wrong."
            if source_owned else 'This project uses its saved axis interpretation. Import the original DAT into a new project to change its axis interpretation.')

    def _source_axis_override_allowed(self) -> bool:
        session = self.workspace.active or self._unregistered_session
        if session.sidecar_writes_enabled:
            return True
        if session.current_cube is not None:
            with QSignalBlocker(self.x_axis_override_combo):
                self.x_axis_override_combo.setCurrentIndex(
                    self.x_axis_override_combo.findData(session.current_cube.x_override))
        self._refresh_source_axis_control()
        self.statusBar().showMessage('Import the original DAT into a new project to change its axis interpretation.')
        return False

    def snapshot_dataset_defaults(self) -> DatasetDefaults:
        """Capture global new-file preferences once, before asynchronous import."""
        self._capture_dataset_controls()
        view = deepcopy((self.workspace.active or self._unregistered_session).view)
        settings = self._annotation_settings
        view.fix_annotations = settings.value('plot_annotations/fixed', True, type=bool)
        view.signal_title_auto = (settings.value(
            'plot_annotations/signal_auto', not settings.contains('plot_annotations/signal_title'), type=bool)
            if view.fix_annotations else True)
        view.title_settings = self._copy_title_settings(self._default_title_settings)
        annotations = {name: settings.value(f'plot_annotations/{name}', '', type=str)
                       for name in ('plot_title', 'y_axis_name', 'y_axis_unit', 'signal_title')
                       if settings.contains(f'plot_annotations/{name}')}
        fixed = {name: FixedRecipe(settings.value(f'fixed_{name}/enabled', False, type=bool),
                                   self._default_fixed_recipe(name))
                 for name in ('processing', 'regions', 'region_edges')}
        return DatasetDefaults(view, self._read_analysis_controls(), fixed, annotations, self._colormap_manual)

    def initialize_dataset(self, path: Path, prepared: tuple,
                           defaults_snapshot: DatasetDefaults,
                           dataset_id: str | None = None) -> DatasetSession:
        return initialize_dataset(path, prepared, defaults_snapshot, dataset_id)

    def register_dataset(self, session: DatasetSession, *, replace_existing: bool = False) -> DatasetSession:
        previous = self.workspace.dataset_for_path(session.source_path)
        registered = self.workspace.register(session, replace_existing=replace_existing)
        if self._project_path is not None:
            registered.sidecar_writes_enabled = False
        if registered is session:
            if previous is not None and previous is not registered:
                self._clear_edit_history(previous)
                self._discard_dataset_documents(previous.dataset_id)
            if registered.applied.mode == 'energy_second_derivative':
                self._applied_derivative_sources[registered.dataset_id] = (
                    id(registered.base_cube), id(registered.current_cube))
            else:
                self._applied_derivative_sources.pop(registered.dataset_id, None)
        self._refresh_imported_datasets()
        self._refresh_document_titles()
        self._refresh_auxiliary_documents()
        self._refresh_data_navigator()
        return registered

    def remove_dataset(self, dataset_id: str) -> bool:
        """Confirm and remove one project dataset, without touching its source files."""
        self._require_project_idle()
        session = self.workspace.datasets[dataset_id]
        choice = QMessageBox.warning(self, 'Remove dataset from project',
            f'Remove this dataset and its stored analysis from the project?\n\n{session.source_path}\n\n'
            'Its Sheet and dependent comparison curves will also be removed. The original DAT file will remain unchanged.',
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if choice != QMessageBox.Yes:
            return False
        # The modal dialog runs an event loop: a newly started job must still
        # protect its owner at the actual mutation boundary.
        self._require_project_idle()
        if self.workspace.datasets.get(dataset_id) is not session:
            return False
        active = self.workspace.active_dataset_id == dataset_id
        if active:
            self._finish_annotation_edit(render=False)
        if self._pending_history_edit and self._pending_history_edit[0] is session:
            self._pending_history_edit = None
        self.workspace.remove(dataset_id)
        self._discard_dataset_documents(dataset_id)
        preview = self.plot_page.preview
        if preview.snapshot is not None and preview.snapshot.owner_token[0] == dataset_id:
            preview.cancel_interaction()
            preview.selected_id = None
            preview._title_selected = False
            preview.set_geometry(None, self.plot_page.presentation)
        for cache in (self._edit_histories, self._plot_revisions, self._plot_cube_owners,
                      self._applied_derivative_sources):
            cache.pop(dataset_id, None)
        if active:
            if self.workspace.active_dataset_id is not None:
                self.activate_dataset(self.workspace.active_dataset_id, capture_outgoing=False)
            else:
                self._bind_plot_page(self._fallback_plot_page)
                self._unregistered_session = DatasetSession(sidecar_writes_enabled=self._project_path is None)
                self._clear_empty_project_view()
                self._refresh_source_axis_control()
        self._refresh_imported_datasets()
        self._refresh_auxiliary_documents()
        self._refresh_data_navigator()
        self._refresh_graph_commands()
        self._refresh_project_modified()
        self.statusBar().showMessage(f'Removed from project: {session.source_path.name}')
        return True

    def _on_remove_dataset(self, dataset_id: str) -> None:
        try:
            self.remove_dataset(dataset_id)
        except (ValueError, KeyError) as exc:
            self._show_error(f'Cannot remove dataset: {exc}')

    def _graph_owner(self):
        if self._project_busy():
            raise ValueError('Wait for the current operation before changing graphs.')
        session = self.workspace.active
        if session is None or session.current_cube is None:
            raise ValueError('Choose an imported DAT first.')
        return session

    def _finish_graph_editors(self, *, capture_science=True):
        if self._workbench_document() is not None:
            self._workbench_finish_editors()
            return
        if self.plot_page.presentation_error is not None:
            raise ValueError('Correct the current plot editor before changing graphs.')
        spins = tuple(spin for spin in (*self._view_lock_controls, *self.region_boundary_spins,
                      self.plot_gate_spin, self.plot_gate_index_spin, self.linthresh_spin)
                      if spin.isEnabled())
        previous_values = {spin: spin.value() for spin in spins}
        for spin in spins:
            if not spin.hasAcceptableInput():
                raise ValueError('Correct the current range editor before changing graphs.')
        if any(not spin.hasAcceptableInput() for spin in
               self.plot_page.control_scroll.findChildren(QAbstractSpinBox)):
            raise ValueError('Correct the current page editor first: invalid numeric value.')
        # Read every draft before a value-change handler projects automatic
        # bounds or redraws the formal page over the other pending editors.
        with ExitStack() as stack:
            for spin in spins:
                stack.enter_context(QSignalBlocker(spin))
                spin.interpretText()
        self._finish_bound_plot_editor()
        self._commit_plot_numeric_edits({spin: spin.value() for spin in spins
                                        if spin.value() != previous_values[spin]})
        if capture_science:
            self.capture_active_dataset(render=False)
        else:
            self._finish_annotation_edit(render=False)
            self._finish_history_edit()
            self._sync_formal_plot_view()

    def _finish_bound_plot_editor(self):
        # The scientific alias remains bound while a workbook/XY document is
        # visible. Programmatic capture must commit it without activating its
        # signal owner as if the user clicked that hidden page.
        previous = self._binding_graph_document
        self._binding_graph_document = True
        try:
            self.plot_page.finish_editor()
        finally:
            self._binding_graph_document = previous

    def _commit_plot_numeric_edits(self, changed):
        if self.current_cube is None:
            return
        # Lock explicit color drafts before range/gate changes can autoscale.
        for control in (self.vmin_spin, self.vmax_spin):
            if control in changed:
                self._lock_user_color_bound(control)
        if self.plot_gate_index_spin in changed:
            self._on_plot_gate_index_changed(changed[self.plot_gate_index_spin])
        elif self.plot_gate_spin in changed:
            self._on_plot_gate_spin_changed(changed[self.plot_gate_spin])
        if self.linthresh_spin in changed:
            self._on_color_mapping_changed()
        for index, control in enumerate(self.region_boundary_spins):
            if control in changed:
                self._on_color_region_boundary_changed(index, changed[control])
        xy_changed = next((control for control in (
            self.xmin_spin, self.xmax_spin, self.ymin_spin, self.ymax_spin) if control in changed), None)
        if xy_changed is not None:
            self._on_view_limit_changed(xy_changed)
        for control in (self.vmin_spin, self.vmax_spin):
            if control in changed:
                self._on_view_limit_changed(control)

    def _project_selected_graph(self, session):
        if session.active_graph.analysis is not None:
            self._workbench_enter(session)
            return
        self._clear_dataset_interactions()
        self._select_graph_page(session)
        self._project_history_view(session, normalize_science=False, invalidate=False)
        self._refresh_fit_range_summary()
        self._refresh_graph_selector()
        self._refresh_document_titles()
        self._refresh_graph_commands()
        notice = self._save_current_plot_state()
        self._refresh_project_modified()
        self.statusBar().showMessage(f'Active: {session.source_path.name} — {session.active_graph.name}'
                                    + (f' — {notice}' if notice else ''))

    def create_graph(self, name):
        session = self._graph_owner()
        self._finish_graph_editors()
        graph = session.create_graph(name)
        self._project_selected_graph(session)
        return graph

    def activate_graph(self, graph_id):
        session = self._graph_owner()
        if graph_id == session.active_graph_id:
            if self.plot_tabs.currentWidget() is self.graph_documents:
                self._select_graph_page(session)
            return
        if graph_id not in session.graphs:
            raise ValueError('The graph no longer exists.')
        outgoing_analysis = session.active_graph.analysis is not None
        self._finish_graph_editors()
        session.activate_graph(graph_id)
        if outgoing_analysis and session.active_graph.analysis is None:
            self._restoring_dataset = True
            try:
                self.restore_dataset_controls(session)
            finally:
                self._restoring_dataset = False
        self._project_selected_graph(session)

    def rename_graph(self, graph_id, name):
        session = self._graph_owner()
        self._finish_graph_editors()
        session.rename_graph(graph_id, name)
        self._refresh_document_titles()
        self._refresh_graph_selector()
        self._save_current_plot_state()
        self._refresh_project_modified()
        self._refresh_graph_commands()

    def remove_graph(self, graph_id):
        session = self._graph_owner()
        self._finish_graph_editors()
        session.remove_graph(graph_id)
        key = (session.dataset_id, graph_id)
        if self.plot_page is self.graph_documents.page_for(key):
            self._bind_plot_page(self._fallback_plot_page)
        self.graph_documents.discard(key)
        self._graph_revisions.pop(key, None)
        self._history_for(session).remove_graph(graph_id)
        self._project_selected_graph(session)

    def _refresh_graph_selector(self):
        if not hasattr(self, 'graph_combo'):
            return
        session = self.workspace.active
        with QSignalBlocker(self.graph_combo):
            self.graph_combo.clear()
            if session is not None:
                for graph in session.graphs.values():
                    if graph.published:
                        self.graph_combo.addItem(graph.name, graph.graph_id)
                self.graph_combo.setCurrentIndex(self.graph_combo.findData(session.active_graph_id))
        self._refresh_graph_lifecycle_controls()

    def _refresh_graph_lifecycle_controls(self):
        if not hasattr(self, 'graph_combo'):
            return
        session = self.workspace.active
        available = bool(session and session.current_cube is not None and not self._project_busy())
        published = available and session.active_graph.published
        self.graph_combo.setEnabled(available and self.graph_combo.count() > 0)
        self.graph_actions_btn.setEnabled(available)
        self.new_graph_action.setEnabled(available)
        self.rename_graph_action.setEnabled(published)
        self.remove_graph_action.setEnabled(published and len(session.graphs) > 1 if session else False)

    def _on_graph_selected(self, _index):
        graph_id = self.graph_combo.currentData()
        if graph_id is not None:
            try:
                self.activate_graph(graph_id)
            except ValueError as exc:
                self._refresh_graph_selector()
                self._show_error(str(exc))

    def _on_new_graph(self):
        try:
            session = self._graph_owner()
            graph_id = session.active_graph_id
            index = 1
            names = {graph.name for graph in session.graphs.values()}
            while f'Graph {index}' in names:
                index += 1
            name, accepted = QInputDialog.getText(self, 'New Graph',
                'Graph name (starts from the current graph settings):', text=f'Graph {index}')
            if accepted and self.workspace.active is session and session.active_graph_id == graph_id:
                self.create_graph(name)
        except ValueError as exc:
            self._show_error(str(exc))

    def _on_rename_graph(self):
        try:
            session = self._graph_owner()
            graph = session.active_graph
            name, accepted = QInputDialog.getText(self, 'Rename Graph', 'Graph name:', text=graph.name)
            if (accepted and self.workspace.active is session and session.active_graph is graph):
                self.rename_graph(graph.graph_id, name)
        except ValueError as exc:
            self._show_error(str(exc))

    def _on_remove_graph(self):
        try:
            session = self._graph_owner()
            graph = session.active_graph
            choice = QMessageBox.warning(self, 'Remove Graph',
                f'Remove graph “{graph.name}”?\n\nThe DAT data and analysis remain available in the other graphs.',
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if choice == QMessageBox.Yes and self.workspace.active is session and session.active_graph is graph:
                self.remove_graph(graph.graph_id)
        except ValueError as exc:
            self._show_error(str(exc))

    def _clear_dataset_interactions(self) -> None:
        if hasattr(self, '_view_layout_timer'):
            self._view_layout_timer.stop()
        self._disconnect_lasso_selector()
        self._clear_lasso_selection_visual()
        self._lasso_mode = False
        self._lasso_selected_peak_ids = []
        self._batch_delete_circle_start = None
        self._drag_peak_arr_idx = self._drag_current_x = None
        self._drag_scatter = None
        self._cached_d2 = None
        for button in (self.lasso_mode_btn, self.circle_delete_btn):
            with QSignalBlocker(button):
                button.setChecked(False)
        self.toolbar._nav_stack.clear()
        self.toolbar.set_history_buttons()
        if self.toolbar.mode == 'pan/zoom':
            self.toolbar.pan()
        elif self.toolbar.mode == 'zoom rect':
            self.toolbar.zoom()

    def restore_dataset_controls(self, session: DatasetSession) -> None:
        sync_analysis_format(session)
        science = analysis_owner(session)
        plot = session.view.plot_recipe(session.current_cube, session.applied, session.presentation)
        self._restore_plot_controls(plot)
        if self.plot_page.presentation != session.presentation:
            self.plot_page.set_presentation(session.presentation)
        widgets = [getattr(self, name) for _, name, _, _ in ANALYSIS_BINDINGS]
        widgets += [self._orig_radio, self._seeds_from_find_peak_radio,
                    self.processing_mode_combo, self.processing_smooth_chk, self.processing_window_spin,
                    self.fix_processing_chk, self.fix_regions_chk, self.fix_region_edges_chk]
        with ExitStack() as stack:
            for widget in widgets:
                stack.enter_context(QSignalBlocker(widget))
            for field, name, _, setter in ANALYSIS_BINDINGS:
                getattr(getattr(self, name), setter)(getattr(science.analysis_controls, field))
            self._orig_radio.setChecked(not science.analysis_controls.derivative_detection)
            self._seeds_from_find_peak_radio.setChecked(not science.analysis_controls.csv_source)
            self.processing_mode_combo.setCurrentIndex(
                self.processing_mode_combo.findData(science.pending.mode))
            self.processing_smooth_chk.setChecked(science.pending.smooth)
            self.processing_window_spin.setValue(science.pending.window_length)
            for name, checkbox in (('processing', self.fix_processing_chk), ('regions', self.fix_regions_chk),
                                   ('region_edges', self.fix_region_edges_chk)):
                checkbox.setChecked(session.fixed_recipes[name].enabled)
        self._csv_path_label.setText(Path(science.csv_seeds_path).name if science.csv_seeds_path else '(none loaded)')
        self._csv_seeds_row.setVisible(science.analysis_controls.csv_source)
        self._on_detect_mode_changed(not science.analysis_controls.derivative_detection)
        self._on_orig_type_changed(science.analysis_controls.peak_mode)
        self._on_d2_type_changed(science.analysis_controls.d2_mode)
        self.analysis_text.setPlainText(science.summary)

    def activate_dataset(self, dataset_id: str, *, capture_outgoing: bool = True) -> None:
        previous_id = self.workspace.active_dataset_id
        try:
            self._activate_dataset(dataset_id, capture_outgoing=capture_outgoing)
        except Exception:
            if previous_id in self.workspace.datasets and self.workspace.active_dataset_id != previous_id:
                # Restore the last valid owner without capturing partially
                # projected controls from the failed incoming document.
                self._activate_dataset(previous_id, capture_outgoing=False)
            raise

    def _activate_dataset(self, dataset_id: str, *, capture_outgoing: bool = True) -> None:
        """Project a cached owner onto the one viewer; never load or recompute."""
        if self._dat_import_pending:
            return
        session = self.workspace.datasets[dataset_id]
        notice = ''
        if capture_outgoing:
            if self._project_busy():
                raise ValueError('Wait for the current operation before changing graphs.')
            self._finish_graph_editors()
            notice = self._save_current_plot_state()
        self._background_jobs.cancel()
        self._background_job_owner = None
        self._processing_request = None
        self._data_generation += 1
        self._invalidate_analysis_jobs()
        self._clear_dataset_interactions()
        self.workspace.active_dataset_id = dataset_id
        self._select_graph_page(session)
        self._restoring_dataset = True
        try:
            self.restore_dataset_controls(session)
            # Markers must match before _plot_cube: its default initialization
            # belongs only to first load, never to a cached owner.
            self._title_source = self._annotation_source = session.current_cube.source_path or id(session.current_cube)
            self._sync_batch_peak_coords()
            self._sync_batch_fit_coords()
            self._plot_cube()
            self._restore_effective_plot_limits(session.view.plot_recipe(session.current_cube, session.applied,
                                                                          session.presentation))
            self._refresh_axis_status()
            self._refresh_fit_range_summary()
            self._refresh_fit_readiness()
            self._update_fit_param_unit_labels()
            self._refresh_peak_delete_controls()
            self._update_lasso_actions()
            self._refresh_export_actions()
        finally:
            self._restoring_dataset = False
        self._refresh_analysis_controls()
        self._refresh_processing_controls()
        self._refresh_source_axis_control()
        self._schedule_plot_page_render()
        self._refresh_imported_datasets()
        self._refresh_graph_selector()
        self.statusBar().showMessage(f'Active: {session.source_path.name} — {session.active_graph.name}' + (f' — {notice}' if notice else ''))

    # =========================================================================
    #  UI construction
    # =========================================================================

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        # Apply a minimal global stylesheet for consistent widget appearance
        self.setStyleSheet(
            "QGroupBox {"
            "  border: 1px solid #d0d0d0; border-radius: 5px;"
            "  margin-top: 8px; font-weight: 600; font-size: 11px;"
            "}"
            "QGroupBox::title {"
            "  subcontrol-origin: margin; subcontrol-position: top left;"
            "  left: 8px; padding: 0 4px; color: #333;"
            "}"
            "QTabWidget::pane {"
            "  border: 1px solid #d0d0d0; border-radius: 3px;"
            "  top: -1px;"
            "}"
            "QTabBar::tab {"
            "  padding: 4px 12px; font-size: 11px; border: 1px solid #ccc;"
            "  border-bottom: none; border-top-left-radius: 3px; border-top-right-radius: 3px;"
            "  background: #f0f0f0; color: #555;"
            "}"
            "QTabBar::tab:selected { background: white; color: #1565c0; font-weight: 600; }"
            "QTabBar::tab:hover:!selected { background: #e8e8e8; }"
            "QStatusBar { font-size: 10px; color: #555; }"
            "QScrollArea { border: none; }"
            "QSplitter::handle { background: #e0e0e0; }"
            "QSplitter::handle:horizontal { width: 3px; }"
        )

        split = QSplitter(Qt.Horizontal)
        self._main_splitter = split
        split.setChildrenCollapsible(False)

        self.project_panel = QWidget()
        self.project_panel.setObjectName('project_panel')
        self.project_panel.setAccessibleName('Project navigation panel')
        self.project_panel.setMinimumWidth(140)
        self._project_panel_layout = QVBoxLayout(self.project_panel)
        self._project_panel_layout.setContentsMargins(0, 0, 0, 0)
        self._project_panel_width = 240
        self._dat_details_requested = True
        details = self._build_left_panel()
        self._dat_properties = details
        self._properties_stack = QStackedWidget()
        self._properties_stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._properties_stack.setMinimumWidth(320)
        self._properties_stack.addWidget(details)
        self._context_properties = QLabel()
        self._context_properties.setTextFormat(Qt.PlainText)
        self._context_properties.setWordWrap(True)
        self._context_properties.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._context_properties.setMargin(12)
        self._context_properties.setAccessibleName('Current document properties')
        self._properties_stack.addWidget(self._context_properties)
        self.dat_details_dock = QDockWidget('Properties', self)
        self.dat_details_dock.setObjectName('dat_details_dock')
        self.dat_details_dock.setAccessibleName('Current document properties')
        self.dat_details_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        self.dat_details_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable)
        self.dat_details_dock.setWidget(self._properties_stack)
        self.dat_details_dock.installEventFilter(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dat_details_dock)
        self.resizeDocks([self.dat_details_dock], [390], Qt.Horizontal)
        plot_panel = self._build_plot_panel()

        split.addWidget(self.project_panel)
        split.addWidget(plot_panel)
        split.setCollapsible(0, True)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 800])
        split.splitterMoved.connect(self._project_splitter_moved)

        outer = QHBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(split)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            "Import data files, open a project, or choose a recent project to begin."
        )

        # DnD overlay (child of centralWidget so it floats on top)
        self._dnd_overlay = QLabel(self.centralWidget())
        self._dnd_overlay.setAlignment(Qt.AlignCenter)
        self._dnd_overlay.setStyleSheet(
            "background: rgba(33,150,243,0.18);"
            "color: #0d47a1; font-size: 20px; font-weight: bold;"
            "border: 3px dashed #1565c0; border-radius: 10px;"
        )
        self._dnd_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._dnd_overlay.setVisible(False)

        # Gate navigation signals (wired here because plot panel builds the widgets)
        self.plot_gate_spin.valueChanged.connect(self._on_plot_gate_spin_changed)
        self.plot_gate_spin.editingFinished.connect(self._on_plot_gate_spin_edit_finished)
        self.plot_gate_index_spin.valueChanged.connect(self._on_plot_gate_index_changed)
        self.plot_prev_gate_btn.clicked.connect(lambda: self._step_gate_index(-1))
        self.plot_next_gate_btn.clicked.connect(lambda: self._step_gate_index(1))
        self._watch_dat_editor_focus(self._sidebar_tabs)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        cw = self.centralWidget()
        if cw and hasattr(self, "_dnd_overlay"):
            self._dnd_overlay.setGeometry(0, 0, cw.width(), cw.height())

    def showEvent(self, event):
        super().showEvent(event)
        if self._sidebar_initial_sizing_pending:
            self._sidebar_initial_sizing_pending = False
            split = self._main_splitter
            QTimer.singleShot(0, self, lambda: split.setSizes([self._project_panel_width,
                                                             max(1, split.width() - self._project_panel_width)]))

    def changeEvent(self, event):
        super().changeEvent(event)
        if (event.type() in (QEvent.FontChange, QEvent.ApplicationFontChange, QEvent.StyleChange)
                and hasattr(self, '_fallback_plot_page')):
            self._size_workspace_commands()

    def _size_workspace_commands(self):
        # Measure the shared row on font/style changes, never on activation.
        height = max(self._fallback_plot_page.command_bar.sizeHint().height(),
                     self.workbook_commands.sizeHint().height()) + 4
        self.graph_command_host.setFixedHeight(max(40, height))

    def _set_project_panel_visible(self, visible):
        split = self._main_splitter
        if not visible and split.sizes()[0] > 0:
            self._project_panel_width = split.sizes()[0]
        self.project_panel.setVisible(visible)
        if visible:
            width = max(140, min(self._project_panel_width, max(140, split.width() // 2)))
            split.setSizes([width, max(1, split.width() - width - split.handleWidth())])
        with QSignalBlocker(self.project_panel_action):
            self.project_panel_action.setChecked(visible)

    def _project_splitter_moved(self, *_args):
        width = self._main_splitter.sizes()[0]
        if width > 0:
            self._project_panel_width = width
        if hasattr(self, 'project_panel_action'):
            with QSignalBlocker(self.project_panel_action):
                self.project_panel_action.setChecked(width > 0)

    def _set_dat_details_requested(self, visible):
        self._dat_details_requested = visible
        self._refresh_workspace_shell()

    def createPopupMenu(self):
        # Reuse the same visibility intent and draft preservation as View.
        menu = QMenu(self)
        menu.setProperty('preserveDatDrafts', True)
        menu.addAction(self.project_panel_action)
        menu.addAction(self.dat_details_action)
        return menu

    def _watch_dat_editor_focus(self, root):
        for editor in [*root.findChildren(QLineEdit), *root.findChildren(QAbstractSpinBox)]:
            editor.installEventFilter(self)

    def _refresh_workspace_shell(self):
        if not hasattr(self, 'dat_details_action'):
            return
        session = self.workspace.active
        context = 'No DAT source selected.'
        if session is not None:
            context = f'Source: {session.source_path.name if session.source_path else "Embedded data"}'
            if session.active_graph.published:
                context = f'Graph: {session.active_graph.name}\n' + context
        self._source_context_label.setText(context)
        key = self.graph_documents._active_key
        dat_context = (self.plot_tabs.currentIndex() == 0 or bool(
            key and len(key) == 2 and self.graph_documents.window_for(key) is not None))
        page = self.graph_documents.page_for(key)
        content = self._dat_properties if dat_context else self._context_properties
        if not dat_context and isinstance(page, XYPlotPage):
            content = page.inspector
        elif (not dat_context and isinstance(page, WorkbookPage)
              and key[1] in self.workspace.workbooks):
            self._context_properties.setText(page.context_summary())
            self._context_properties.setToolTip(page.context_summary(detailed=True))
        elif not dat_context:
            self._context_properties.setText('No active document.\n\nOpen a Workbook or graph from Project to edit its properties.')
            self._context_properties.setToolTip('')
        if self._properties_stack.currentWidget() is not content:
            self._properties_stack.setCurrentWidget(content)
        self.dat_details_action.setEnabled(True)
        with QSignalBlocker(self.dat_details_action):
            self.dat_details_action.setChecked(self._dat_details_requested)
        if isinstance(page, XYPlotPage):
            with QSignalBlocker(page.inspector_toggle_btn):
                page.inspector_toggle_btn.setChecked(self._dat_details_requested)
        visible = self._dat_details_requested
        if visible != (not self.dat_details_dock.isHidden()):
            # Native spin boxes normalize unfinished text on hide and show.
            # A panel toggle is presentation only, so preserve both the value
            # and draft while suppressing the associated editing signals.
            editors = self.dat_details_dock.findChildren(QLineEdit)
            spins = self.dat_details_dock.findChildren(QAbstractSpinBox)
            drafts = [(spin, spin.value(), spin.lineEdit().text()) for spin in spins]
            with ExitStack() as blockers:
                for editor in [*editors, *spins]:
                    blockers.enter_context(QSignalBlocker(editor))
                self.dat_details_dock.setVisible(visible)
                for spin, value, text in drafts:
                    spin.setValue(value)
                    spin.lineEdit().setText(text)

    def eventFilter(self, watched, event):
        if (watched is getattr(self, 'plot_tabs', None) and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Tab, Qt.Key_Backtab)
                and event.modifiers() & Qt.ControlModifier):
            # This is an internal page container, not user-facing peer tabs.
            return True
        if event.type() == QEvent.FocusOut and isinstance(watched, (QLineEdit, QAbstractSpinBox)):
            # Opening a panel menu must not normalize a
            # spin-box draft before the visibility action can preserve it.
            popup = QApplication.activePopupWidget()
            if ((hasattr(self, '_view_menu') and self._view_menu.isVisible())
                    or (popup is not None and popup.property('preserveDatDrafts'))):
                return True
        if watched is getattr(self, 'dat_details_dock', None) and event.type() == QEvent.Close:
            self._set_dat_details_requested(False)
        if (watched is getattr(self, 'data_navigator', None) and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)):
            self._open_navigator_item()
            return True
        datasets = getattr(self, 'imported_dataset_list', None)
        if (datasets is not None and watched is datasets.viewport()
                and event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease)
                and event.button() == Qt.RightButton):
            # A context-menu gesture targets the clicked owner without changing
            # the single viewer's selection before the menu is shown.
            return True
        if watched is getattr(self, 'import_dats_btn', None) and event.type() == QEvent.EnabledChange:
            if hasattr(self, '_graph_command_actions'):
                self._graph_command_actions['import'].setEnabled(watched.isEnabled())
                self._graph_command_actions['import_options'].setEnabled(watched.isEnabled())
        scroll = getattr(self, 'analysis_scroll', None)
        if scroll is not None and watched is scroll.viewport() and event.type() in (QEvent.Resize, QEvent.Show):
            self._schedule_view_layout()
        return super().eventFilter(watched, event)

    # ── expander helper ───────────────────────────────────────────────────────

    def _make_expander(self, title: str, content: QWidget, *, expanded: bool = True) -> QWidget:
        section = DisclosureSection(title, content, expanded=expanded)
        protect_disclosure_drafts(content)
        return section

    # ── row / spin helpers ────────────────────────────────────────────────────

    @staticmethod
    def _hrow(widgets: list) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for w in widgets:
            lay.addWidget(w)
        lay.addStretch(1)
        return row

    @staticmethod
    def _view_spin_row(spin: QDoubleSpinBox, lock_box: QCheckBox) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        # The sidebar permits numeric fields to shrink. Give this field the
        # available row space so the trailing Fix starts after its real bounds.
        lay.addWidget(spin, 1)
        lay.addWidget(lock_box)
        return row

    # ── left panel ────────────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        self._sidebar_tabs = QTabWidget()
        self._sidebar_tabs.setObjectName('taskSidebarTabs')
        self._sidebar_tabs.setAccessibleName('Data, analysis and view tasks')
        self._analysis_sidebar_index = 0
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        # ── Data Source (expander, default expanded) ──────────────────────────
        src_content = QWidget()
        src_lay = QVBoxLayout(src_content)
        src_lay.setContentsMargins(2, 4, 2, 4)
        src_lay.setSpacing(6)

        self.import_dats_btn = QPushButton('Import data…')
        self.import_dats_btn.setObjectName('importDatsButton')
        self.import_dats_btn.setAccessibleName('Import data files')
        self.import_dats_btn.setToolTip('Automatically import full XY tables and Gate matrices into Workspace: first column X, all remaining columns Y. Optional preview: File → Import with options.')
        src_lay.addWidget(self.import_dats_btn)
        imported_header = QHBoxLayout()
        self.imported_dataset_label = QLabel('Imported datasets (0)')
        self.imported_dataset_label.setObjectName('importedDatasetLabel')
        self.imported_dataset_label.setWordWrap(True)
        imported_header.addWidget(self.imported_dataset_label, 1)
        self.imported_dataset_label.setToolTip(
            'Use File → Save Project to save the complete workspace as .spectra. Ordinary DAT '
            'imports save plot settings beside each source as .dat.plot.json until saved into a project.')
        rows_label = QLabel('Rows:')
        self.imported_dataset_rows_spin = QSpinBox()
        self.imported_dataset_rows_spin.setObjectName('importedDatasetRows')
        self.imported_dataset_rows_spin.setAccessibleName('Imported dataset visible rows')
        self.imported_dataset_rows_spin.setRange(4, 12)
        self.imported_dataset_rows_spin.setKeyboardTracking(False)
        self.imported_dataset_rows_spin.setValue(self._annotation_settings.value('datasets/list_rows', 8, type=int))
        self.imported_dataset_rows_spin.setToolTip('Visible rows in the dataset list')
        rows_label.setBuddy(self.imported_dataset_rows_spin)
        imported_header.addWidget(rows_label)
        imported_header.addWidget(self.imported_dataset_rows_spin)
        src_lay.addLayout(imported_header)
        search_row = QHBoxLayout()
        search_label = QLabel('Search:')
        self.imported_dataset_search = QLineEdit()
        self.imported_dataset_search.setObjectName('importedDatasetSearch')
        self.imported_dataset_search.setAccessibleName('Search imported datasets')
        self.imported_dataset_search.setPlaceholderText('Filename or folder path…')
        self.imported_dataset_search.setClearButtonEnabled(True)
        self.imported_dataset_search.setToolTip('Filter by filename or folder path; the active dataset stays open')
        search_label.setBuddy(self.imported_dataset_search)
        search_row.addWidget(search_label)
        search_row.addWidget(self.imported_dataset_search, 1)
        src_lay.addLayout(search_row)
        self.imported_dataset_list = QListWidget()
        self.imported_dataset_list.setObjectName('importedDatasetList')
        self.imported_dataset_list.setAccessibleName('Imported datasets')
        self.imported_dataset_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.imported_dataset_list.viewport().installEventFilter(self)
        self.imported_dataset_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.imported_dataset_list.customContextMenuRequested.connect(self._show_dataset_context_menu)
        self.imported_dataset_rows_spin.valueChanged.connect(self._resize_imported_dataset_list)
        self._resize_imported_dataset_list()
        self.imported_dataset_list.setToolTip('Select a dataset to restore its cached plot and analysis')
        self.imported_dataset_label.setBuddy(self.imported_dataset_list)
        src_lay.addWidget(self.imported_dataset_list)
        graph_row = QHBoxLayout()
        graph_label = QLabel('Graph name:')
        self.graph_combo = QComboBox()
        self.graph_combo.setObjectName('graphSelector')
        self.graph_combo.setAccessibleName('Named graph for the active DAT')
        self.graph_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.graph_combo.setMinimumContentsLength(14)
        self.graph_combo.setToolTip('Each graph keeps its own ranges, gate, titles and style')
        graph_label.setBuddy(self.graph_combo)
        self.graph_actions_btn = QToolButton()
        self.graph_actions_btn.setText('Graphs')
        self.graph_actions_btn.setAccessibleName('Graph actions')
        self.graph_actions_btn.setPopupMode(QToolButton.InstantPopup)
        graph_menu = QMenu(self.graph_actions_btn)
        self.new_graph_action = graph_menu.addAction('New Graph…', self._on_new_graph)
        self.rename_graph_action = graph_menu.addAction('Rename Graph…', self._on_rename_graph)
        self.remove_graph_action = graph_menu.addAction('Remove Graph…', self._on_remove_graph)
        self.graph_actions_btn.setMenu(graph_menu)
        self.graph_combo.currentIndexChanged.connect(self._on_graph_selected)
        graph_row.addWidget(graph_label)
        graph_row.addWidget(self.graph_combo, 1)
        graph_row.addWidget(self.graph_actions_btn)
        src_lay.addLayout(graph_row)
        self._refresh_graph_selector()
        self.imported_dataset_filter_status_label = QLabel('No matches. Clear search to show all datasets.')
        self.imported_dataset_filter_status_label.setObjectName('importedDatasetFilterStatusLabel')
        self.imported_dataset_filter_status_label.setAccessibleName('Dataset search status')
        self.imported_dataset_filter_status_label.setWordWrap(True)
        self.imported_dataset_filter_status_label.hide()
        src_lay.addWidget(self.imported_dataset_filter_status_label)
        self.dat_import_status_label = QLabel('No imported datasets. Import or drop DAT files to begin.')
        self.dat_import_status_label.setObjectName('datImportStatusLabel')
        self.dat_import_status_label.setAccessibleName('DAT import status')
        self.dat_import_status_label.setWordWrap(True)
        src_lay.addWidget(self.dat_import_status_label)

        # Folder inventory stays available as a secondary data workflow.
        folder_content = QWidget()
        folder_lay = QVBoxLayout(folder_content)
        folder_lay.setContentsMargins(0, 0, 0, 0)
        folder_lay.setSpacing(6)
        # Root folder row
        root_row = QHBoxLayout()
        root_row.setSpacing(6)
        self.root_edit = QLineEdit()
        self.root_edit.setReadOnly(True)
        self.root_edit.setPlaceholderText("Root folder path…")
        self.root_edit.setToolTip("Path to the root folder containing .dat experiment folders")
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setToolTip("Open a dialog to choose the root folder")
        self.browse_btn.setMinimumWidth(72)
        self.browse_btn.setStyleSheet(_STYLE_PRIMARY)
        root_row.addWidget(self.root_edit, 1)
        root_row.addWidget(self.browse_btn)
        folder_lay.addLayout(root_row)

        # Process folder row
        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        folder_lbl = QLabel("Folder:")
        folder_lbl.setFixedWidth(48)
        folder_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.folder_combo = QComboBox()
        self.folder_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.folder_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.folder_combo.setToolTip("Select the process sub-folder to list .dat files from")
        folder_row.addWidget(folder_lbl)
        folder_row.addWidget(self.folder_combo, 1)
        folder_lay.addLayout(folder_row)

        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setFrameShadow(QFrame.Sunken)
        sep1.setStyleSheet("color: #ddd;")
        folder_lay.addWidget(sep1)

        # File list
        folder_lay.addWidget(QLabel('Folder files (inventory)'))
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.file_list.setMinimumHeight(55)
        self.file_list.setMaximumHeight(80)
        self.file_list.setToolTip("Double-click or select and press Load to open a .dat file")
        self.file_list.setStyleSheet(
            "QListWidget { border: 1px solid #ddd; border-radius: 3px; font-size: 11px; }"
            "QListWidget::item:selected { background: #1976d2; color: white; }"
        )
        folder_lay.addWidget(self.file_list)

        self.load_btn = QPushButton("Load Selected File")
        self.load_btn.setToolTip("Load the highlighted .dat file (double-click also works)")
        self.load_btn.setStyleSheet(_STYLE_PRIMARY)
        folder_lay.addWidget(self.load_btn)
        src_lay.addWidget(self._make_expander('Folder inventory', folder_content, expanded=False))

        self._source_context_label = QLabel('No DAT source selected.')
        self._source_context_label.setTextFormat(Qt.PlainText)
        self._source_context_label.setWordWrap(True)
        self._source_context_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self._source_context_label)
        self._source_navigation_content = src_content
        lay.addWidget(self._make_expander('Advanced source navigation', src_content, expanded=False))

        # ── Data Processing (expander, default expanded) ─────────────────────
        processing_content = self._build_data_processing()
        self._processing_content = processing_content
        lay.addStretch(1)

        # ── View Settings (expander, default expanded) ────────────────────────
        view_content = self._build_view_settings()

        # ── Spectrum Analysis ─────────────────────────────────────────────────
        analysis_box = QWidget()
        ao_lay = QVBoxLayout(analysis_box)
        ao_lay.setContentsMargins(0, 0, 0, 0)
        ao_lay.setSpacing(4)

        self._analysis_tabs = QTabWidget()
        self._analysis_tabs.addTab(self._build_find_peak_tab(), "Find Peaks")
        self._analysis_tabs.addTab(self._build_lorentz_fit_tab(), "Peak Fit")
        ao_lay.addWidget(self._analysis_tabs)

        # Analysis output label
        out_hdr = QLabel("Analysis Output")
        out_hdr.setStyleSheet("font-weight: 600; color: #444; font-size: 11px; margin-top: 2px;")
        ao_lay.addWidget(out_hdr)
        self.create_analysis_graph_btn = QPushButton('Create analysis graph')
        self.create_analysis_graph_btn.setToolTip(
            'Create an editable Workspace graph from current Gate peaks/dips or a completed fit, with applied source values.')
        self.create_analysis_graph_btn.clicked.connect(self._on_create_analysis_graph)
        ao_lay.addWidget(self.create_analysis_graph_btn)

        self.analysis_text = QPlainTextEdit()
        self.analysis_text.setReadOnly(True)
        self.analysis_text.setMinimumHeight(60)
        self.analysis_text.setMaximumHeight(105)
        self.analysis_text.setStyleSheet(
            "QPlainTextEdit {"
            "  background: #f8f9fa; border: 1px solid #dee2e6;"
            "  border-radius: 3px; font-family: Consolas, monospace; font-size: 10px;"
            "  padding: 4px;"
            "}"
        )
        ao_lay.addWidget(self.analysis_text)

        analysis_panel = QWidget()
        analysis_lay = QVBoxLayout(analysis_panel)
        analysis_lay.setContentsMargins(6, 4, 6, 4)
        analysis_lay.addWidget(self._make_expander('Data Processing', processing_content, expanded=True))
        analysis_lay.addWidget(analysis_box)
        analysis_lay.addStretch(1)
        self._analysis_sidebar_scroll = QScrollArea()
        self._analysis_sidebar_scroll.setWidgetResizable(True)
        self._analysis_sidebar_scroll.setWidget(analysis_panel)
        self._view_sidebar_scroll = QScrollArea()
        self._view_sidebar_scroll.setWidgetResizable(True)
        self._view_sidebar_scroll.setWidget(view_content)

        # ── wire up non-tab signals ───────────────────────────────────────────
        self.browse_btn.clicked.connect(self._on_browse_root)
        self.import_dats_btn.clicked.connect(self._on_import_dats)
        self.imported_dataset_search.textChanged.connect(self._filter_imported_datasets)
        self.imported_dataset_list.currentItemChanged.connect(self._on_imported_dataset_changed)
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        self.load_btn.clicked.connect(self._on_load_selected_file)
        self.file_list.itemDoubleClicked.connect(lambda _: self._on_load_selected_file())
        self.x_axis_override_combo.currentIndexChanged.connect(self._on_x_axis_override_async)
        self.processing_mode_combo.currentIndexChanged.connect(self._on_processing_mode_changed)
        self.generate_heatmap_btn.clicked.connect(self._on_generate_heatmap)
        self.cmap_combo.currentIndexChanged.connect(self._on_colormap_changed)
        self.cmap_combo.activated.connect(lambda _index: setattr(self, '_colormap_manual', True))
        self.lw_min_spin.valueChanged.connect(lambda _: self._update_fit_param_unit_labels())
        self.lw_max_spin.valueChanged.connect(lambda _: self._update_fit_param_unit_labels())
        self.peak_move_limit_spin.valueChanged.connect(lambda _: self._update_fit_param_unit_labels())
        self.fit_model_combo.currentTextChanged.connect(lambda _: self._update_fit_param_unit_labels())
        for spin in (self.xmin_spin, self.xmax_spin, self.ymin_spin,
                     self.ymax_spin, self.vmin_spin, self.vmax_spin):
            spin.valueChanged.connect(
                lambda _value, changed_spin=spin: self._on_view_limit_changed(changed_spin)
            )
        self.apply_view_btn.clicked.connect(self._plot_cube)
        self.auto_view_btn.clicked.connect(self._on_auto_view)

        scroll.setWidget(panel)
        self._data_sidebar_scroll = scroll
        self._sidebar_tabs.addTab(scroll, 'Data')
        self._sidebar_tabs.addTab(self._analysis_sidebar_scroll, 'Analysis')
        self._sidebar_tabs.addTab(self._view_sidebar_scroll, 'View')
        # Forms wrap labels above fields when the adjustable sidebar narrows.
        for form in self._sidebar_tabs.findChildren(QFormLayout):
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for label in self._sidebar_tabs.findChildren(QLabel):
            label.setWordWrap(True)
        for combo in self._sidebar_tabs.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # The active name may be arbitrarily long; let this row fit the sidebar.
        self.graph_combo.setMinimumContentsLength(0)
        self.graph_combo.setMinimumWidth(0)
        self.graph_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        for spin in self._sidebar_tabs.findChildren(QAbstractSpinBox):
            spin.setMinimumWidth(min(90, spin.maximumWidth()))
            spin.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        # Header rows must reserve their spinner width; the form-field policy
        # above lets QHBoxLayout allocate zero width and clips this preference.
        self.imported_dataset_rows_spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.imported_dataset_rows_spin.setMinimumWidth(self.imported_dataset_rows_spin.minimumSizeHint().width())
        # Inline conversions must also reserve the numeric editor's width.
        for spin in (self.lw_min_spin, self.lw_max_spin, self.peak_move_limit_spin):
            spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            spin.setMinimumWidth(spin.minimumSizeHint().width())
        return self._sidebar_tabs

    # ── view settings content (used inside expander) ──────────────────────────

    def _build_data_processing(self) -> QWidget:
        """Build the current-data transform controls for the heatmap."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 4)
        lay.setSpacing(4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        form.setHorizontalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.processing_mode_combo = QComboBox()
        self.processing_mode_combo.addItem("Original", "original")
        self.processing_mode_combo.addItem("Energy 2nd derivative", "energy_second_derivative")
        self.processing_mode_combo.setToolTip(
            "Choose the data represented by the heatmap and accompanying spectrum."
        )
        self.fix_processing_chk = QCheckBox("Fix")
        self.fix_processing_chk.setChecked(self._annotation_settings.value(
            "fixed_processing/enabled", False, type=bool))
        self.fix_processing_chk.setToolTip("Carry mode and smoothing settings across files and restarts.")
        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(self.processing_mode_combo, 1)
        mode_layout.addWidget(self.fix_processing_chk)
        form.addRow("Mode:", mode_row)

        self.processing_smooth_chk = QCheckBox("SG smoothing")
        self.processing_smooth_chk.setChecked(True)
        self.processing_smooth_chk.setToolTip(
            "Compute the second derivative directly from a local polynomial fit (SG). "
            "When disabled, use ordinary finite differences."
        )
        form.addRow("", self.processing_smooth_chk)

        self.processing_window_spin = QSpinBox()
        self.processing_window_spin.setRange(5, 1001)
        self.processing_window_spin.setSingleStep(2)
        self.processing_window_spin.setValue(21)
        self.processing_window_spin.setToolTip("Odd smoothing window length in energy points.")
        form.addRow("Window points:", self.processing_window_spin)
        self.fix_processing_chk.toggled.connect(self._remember_fixed_processing)
        self.processing_mode_combo.currentIndexChanged.connect(self._remember_fixed_processing)
        self.processing_smooth_chk.toggled.connect(self._remember_fixed_processing)
        self.processing_window_spin.valueChanged.connect(self._remember_fixed_processing)
        lay.addLayout(form)

        self.processing_status_label = QLabel('Applied: Original — no data loaded.')
        self.processing_status_label.setWordWrap(True)
        self.processing_status_label.setAccessibleName('Applied and pending data processing')
        lay.addWidget(self.processing_status_label)
        self.generate_heatmap_btn = QPushButton('Apply processing')
        self.generate_heatmap_btn.setAccessibleName('Apply processing')
        self.generate_heatmap_btn.setStyleSheet(_STYLE_PRIMARY)
        self.generate_heatmap_btn.setToolTip(
            "Apply the selected transform to the current loaded data."
        )
        lay.addWidget(self.generate_heatmap_btn)
        return w

    def _build_view_settings(self) -> QWidget:
        """Build the View Settings panel content (X-axis, colormap, bounds, gate)."""
        w = QWidget()
        lay = QVBoxLayout(w)
        self._view_settings_layout = lay
        lay.setContentsMargins(2, 2, 2, 4)
        lay.setSpacing(6)

        # ── Frequently used view and annotation controls ─────────────────────
        self.x_axis_override_combo = QComboBox()
        for value, label in AXIS_OVERRIDE_ITEMS:
            self.x_axis_override_combo.addItem(label, value)
        self.x_axis_override_combo.setToolTip(
            "Override the auto-detected x-axis type. Use 'Auto Detect' unless the detection is wrong."
        )

        self.cmap_combo = QComboBox()
        self.cmap_combo.setIconSize(QSize(48, 12))
        for label, name in COMMON_COLORMAPS:
            resolved = resolve_colormap(name)
            palette = colormaps[resolved] if isinstance(resolved, str) else resolved
            rgba = np.asarray(palette(np.linspace(0, 1, 72)) * 255, dtype=np.uint8)
            pixels = np.ascontiguousarray(np.repeat(rgba[None, :, :], 12, axis=0))
            preview = QImage(pixels.data, 72, 12, pixels.strides[0], QImage.Format_RGBA8888).copy()
            self.cmap_combo.addItem(QIcon(QPixmap.fromImage(preview)), label, name)
        self.cmap_combo.insertSeparator(self.cmap_combo.count())
        for name in ('turbo', 'viridis', 'plasma', 'inferno', 'magma', 'cividis', 'RdBu_r_p0p45'):
            self.cmap_combo.addItem(name, name)
        self.cmap_combo.setCurrentText("RdBu_r")
        self._colormap_manual = False
        self.cmap_combo.setToolTip("Colormap used for the intensity heatmap")

        self.color_scale_combo = QComboBox()
        self.color_scale_combo.addItem("Global", "global")
        self.color_scale_combo.addItem("2 regions", "2")
        self.color_scale_combo.addItem("3 regions", "3")
        self.color_scale_combo.setToolTip(
            "Use one color scale or independent scales across contiguous photon-energy regions."
        )

        top_form = QFormLayout()
        top_form.setContentsMargins(0, 0, 0, 0)
        top_form.setSpacing(4)
        top_form.setHorizontalSpacing(8)
        top_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        top_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fix_annotations_chk = QCheckBox("Fix annotations")
        self.fix_annotations_chk.setChecked(
            self._annotation_settings.value("plot_annotations/fixed", True, type=bool))
        self.fix_annotations_chk.setToolTip(
            "Keep Y/Z annotations for new files. In Manual title mode, also keep the title. "
            "Templates always generate a fresh title for each new file; edits override only that DAT. "
            "Previously opened files always recall their own saved plot settings.")
        template_content = QWidget()
        template_form = QFormLayout(template_content)
        template_form.setContentsMargins(4, 4, 4, 4)
        template_form.addRow(self.fix_annotations_chk)
        self.plot_title_edit = QLineEdit()
        self.plot_title_edit.setPlaceholderText("Optional plot title (blank by default)")
        self.plot_title_edit.setMaxLength(2147483647)
        self.y_axis_name_edit = QLineEdit()
        self.y_axis_unit_edit = QLineEdit()
        self.y_axis_unit_edit.setPlaceholderText("Optional unit")
        self._annotation_edits = {
            "plot_title": self.plot_title_edit,
            "y_axis_name": self.y_axis_name_edit,
            "y_axis_unit": self.y_axis_unit_edit,
        }
        for field, edit in self._annotation_edits.items():
            edit.setToolTip("Plot annotation only; does not change the data or its units.")
            if self.fix_annotations_chk.isChecked():
                edit.setText(self._annotation_settings.value(f"plot_annotations/{field}", "", type=str))
            edit.textChanged.connect(
                lambda text, field=field: self._save_plot_annotation(field, text))
        top_form.addRow("Plot title:", self.plot_title_edit)
        self._build_title_controls(template_form)
        top_form.addRow("Y-axis label:", self.y_axis_name_edit)
        top_form.addRow("Y-axis unit:", self.y_axis_unit_edit)
        self.signal_title_combo = QComboBox()
        self.signal_title_combo.setEditable(True)
        self.signal_title_combo.setInsertPolicy(QComboBox.NoInsert)
        self.signal_title_combo.addItems([RAW_LABEL, DERIVATIVE_LABEL, 'PL (arb. units)',
                                         'Signal (arb. units)'])
        self.signal_title_combo.setToolTip(
            "Colorbar annotation only; choosing a derivative label does not process the data.")
        if self.fix_annotations_chk.isChecked():
            self.signal_title_combo.setCurrentText(self._annotation_settings.value(
                "plot_annotations/signal_title", RAW_LABEL, type=str))
        self.signal_title_combo.currentTextChanged.connect(
            lambda text: self._save_plot_annotation("signal_title", text))
        self.signal_title_combo.activated.connect(lambda _index: self._finish_annotation_edit())
        self.signal_title_auto_chk = QCheckBox("Auto from data")
        self.signal_title_auto_chk.setChecked(self._annotation_settings.value(
            "plot_annotations/signal_auto",
            not self._annotation_settings.contains("plot_annotations/signal_title"), type=bool)
            if self.fix_annotations_chk.isChecked() else True)
        self.signal_title_auto_chk.toggled.connect(self._on_signal_auto_toggled)
        top_form.addRow("Colorbar label:", self.signal_title_combo)
        top_form.addRow('', self.signal_title_auto_chk)
        self.fix_annotations_chk.toggled.connect(self._on_fix_annotations_toggled)
        # Axis status label
        self.axis_status_label = QLabel("X-axis: no file loaded.")
        self.axis_status_label.setWordWrap(True)
        self.axis_status_label.setStyleSheet(
            "color: #607d8b; font-size: 10px;"
            "background: #f0f4f8; border-radius: 3px; padding: 3px 5px;"
        )
        # ── View bounds grid (Min / Max per axis) ─────────────────────────────
        self.xmin_spin = QDoubleSpinBox()
        self.xmax_spin = QDoubleSpinBox()
        self.ymin_spin = QDoubleSpinBox()
        self.ymax_spin = QDoubleSpinBox()
        self.vmin_spin = _ColorBoundSpinBox()
        self.vmax_spin = _ColorBoundSpinBox()
        for sp in (self.xmin_spin, self.xmax_spin, self.ymin_spin,
                   self.ymax_spin, self.vmin_spin, self.vmax_spin):
            sp.setDecimals(4)
            sp.setKeyboardTracking(False)
            sp.setRange(-1e12, 1e12)
            sp.setSingleStep(0.01)
            sp.setMinimumWidth(90)
            sp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.xmin_lock = QCheckBox("Fix")
        self.xmax_lock = QCheckBox("Fix")
        self.ymin_lock = QCheckBox("Fix")
        self.ymax_lock = QCheckBox("Fix")
        self.vmin_lock = QCheckBox("Fix")
        self.vmax_lock = QCheckBox("Fix")
        for ck in (self.xmin_lock, self.xmax_lock, self.ymin_lock,
                   self.ymax_lock, self.vmin_lock, self.vmax_lock):
            ck.setToolTip("Fix this bound; Auto View and automatic color limits leave it unchanged")
        self._view_lock_controls = {
            self.xmin_spin: self.xmin_lock, self.xmax_spin: self.xmax_lock,
            self.ymin_spin: self.ymin_lock, self.ymax_spin: self.ymax_lock,
            self.vmin_spin: self.vmin_lock, self.vmax_spin: self.vmax_lock,
        }
        for spin in (self.vmin_spin, self.vmax_spin):
            spin.userCommitted.connect(lambda changed_spin=spin: self._lock_user_color_bound(changed_spin))
        for lock_box in self._view_lock_controls.values():
            lock_box.toggled.connect(
                lambda checked, changed_lock=lock_box: self._on_view_lock_toggled(changed_lock, checked)
            )

        bounds_box = QWidget()
        bounds_grid = QFormLayout(bounds_box)
        bounds_grid.setContentsMargins(0, 0, 0, 0)
        bounds_grid.setSpacing(4)
        bounds_grid.setHorizontalSpacing(5)

        bounds_grid.setRowWrapPolicy(QFormLayout.WrapLongRows)
        bounds_grid.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for label, spin, lock in (
            ('X minimum:', self.xmin_spin, self.xmin_lock),
            ('X maximum:', self.xmax_spin, self.xmax_lock),
            ('Y minimum:', self.ymin_spin, self.ymin_lock),
            ('Y maximum:', self.ymax_spin, self.ymax_lock),
        ):
            bounds_grid.addRow(label, self._view_spin_row(spin, lock))

        region_edges_label = QLabel("Region edges")
        region_edges_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        region_edges_label.setStyleSheet("font-size: 10px; color: #444;")
        region_edges_label.setToolTip("Internal photon-energy boundaries, snapped to heatmap cell edges")
        self.region_boundary_spins: list[QDoubleSpinBox] = []
        boundary_row = QWidget()
        boundary_lay = QFormLayout(boundary_row)
        boundary_lay.setContentsMargins(0, 0, 0, 0)
        boundary_lay.setSpacing(4)
        boundary_lay.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for idx in range(2):
            spin = QDoubleSpinBox()
            spin.setDecimals(4)
            spin.setRange(-1e12, 1e12)
            spin.setSingleStep(0.01)
            spin.setMinimumWidth(90)
            spin.setKeyboardTracking(False)
            spin.setToolTip("Boundary is snapped to a valid photon-energy cell edge")
            spin.valueChanged.connect(
                lambda value, boundary_idx=idx: self._on_color_region_boundary_changed(boundary_idx, value)
            )
            self.region_boundary_spins.append(spin)
            boundary_lay.addRow(f'Edge {idx + 1}:', spin)
        self._region_edges_row = QWidget()
        region_row_lay = QVBoxLayout(self._region_edges_row)
        region_row_lay.setContentsMargins(0, 0, 0, 0)
        region_row_lay.setSpacing(5)
        region_row_lay.addWidget(region_edges_label)
        region_row_lay.addWidget(boundary_row, 1)
        self.fix_region_edges_chk = QCheckBox("Fix")
        settings = self._annotation_settings
        if not settings.contains("fixed_region_edges/enabled"):
            settings.setValue("fixed_region_edges/enabled", settings.value(
                "fixed_regions/enabled", False, type=bool))
            settings.setValue("fixed_region_edges/recipe", json.dumps({
                "boundaries": self._fixed_recipe("regions").get("boundaries", []),
            }))
        self.fix_region_edges_chk.setChecked(settings.value(
            "fixed_region_edges/enabled", False, type=bool))
        self.fix_region_edges_chk.setToolTip(
            "Carry energy boundaries across files with the same region count; intensity ranges remain automatic.")
        self.fix_region_edges_chk.toggled.connect(self._remember_fixed_region_edges)
        region_row_lay.addWidget(self.fix_region_edges_chk)
        self._region_edges_row.setVisible(False)

        # View action buttons
        btn_row = QVBoxLayout()
        btn_row.setSpacing(6)
        self.apply_view_btn = QPushButton("Apply View")
        self.apply_view_btn.setToolTip("Apply the current bounds to the heatmap")
        self.apply_view_btn.setStyleSheet(_STYLE_MUTED)
        self.auto_view_btn = QPushButton("Auto View")
        self.auto_view_btn.setToolTip(
            "Reset unfixed X/Y bounds to the full data and update unfixed intensity bounds from the visible heatmap"
        )
        self.auto_view_btn.setStyleSheet(_STYLE_MUTED)
        btn_row.addWidget(self.apply_view_btn, 1)
        btn_row.addWidget(self.auto_view_btn, 1)
        bounds_grid.addRow(btn_row)

        # Keep the most frequently adjusted color controls together.
        color_box = QGroupBox("Color Scale")
        self._shared_color_scale_box = color_box
        color_lay = QVBoxLayout(color_box)
        color_lay.setContentsMargins(8, 14, 8, 6)
        color_lay.setSpacing(4)
        color_form = QFormLayout()
        color_form.setContentsMargins(0, 0, 0, 0)
        color_form.setSpacing(4)
        color_form.setHorizontalSpacing(8)
        color_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        color_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        color_form.addRow("Colormap:", self.cmap_combo)
        self.mapping_combo = QComboBox()
        for label, kind in (('Linear', 'linear'), ('Log', 'log'), ('SymLog', 'symlog'),
                            ('Zero-centered', 'zero-centered')):
            self.mapping_combo.addItem(label, kind)
        self.mapping_combo.setToolTip('Display mapping only; preserves scientific data and completed fits.')
        color_form.addRow('Mapping:', self.mapping_combo)
        self.linthresh_spin = QDoubleSpinBox()
        self.linthresh_spin.setDecimals(8)
        self.linthresh_spin.setRange(1e-8, 1e12)
        self.linthresh_spin.setValue(1.0)
        self.linthresh_spin.setKeyboardTracking(False)
        self.linthresh_spin.setEnabled(False)
        color_form.addRow('SymLog threshold:', self.linthresh_spin)
        self.mapping_status_label = QLabel()
        self.mapping_status_label.setWordWrap(True)
        self.mapping_status_label.hide()
        color_form.addRow(self.mapping_status_label)
        self.mapping_combo.currentIndexChanged.connect(self._on_color_mapping_changed)
        self.linthresh_spin.valueChanged.connect(self._on_color_mapping_changed)
        self.fix_regions_chk = QCheckBox("Fix")
        self.fix_regions_chk.setChecked(self._annotation_settings.value(
            "fixed_regions/enabled", False, type=bool))
        self.fix_regions_chk.setToolTip(
            "Carry region count across files; intensity ranges remain automatic.")
        self.fix_regions_chk.toggled.connect(self._remember_fixed_regions)
        scale_row = QWidget()
        scale_layout = QHBoxLayout(scale_row)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.addWidget(self.color_scale_combo, 1)
        scale_layout.addWidget(self.fix_regions_chk)
        color_form.addRow("Color scale:", scale_row)
        color_lay.addLayout(color_form)

        color_grid = QFormLayout()
        color_grid.setContentsMargins(0, 0, 0, 0)
        color_grid.setSpacing(4)
        color_grid.setHorizontalSpacing(5)
        color_grid.setRowWrapPolicy(QFormLayout.WrapLongRows)
        color_grid.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        color_grid.addRow('Minimum:', self._view_spin_row(self.vmin_spin, self.vmin_lock))
        color_grid.addRow('Maximum:', self._view_spin_row(self.vmax_spin, self.vmax_lock))
        color_grid.addRow(self._region_edges_row)
        color_lay.addLayout(color_grid)
        self.auto_color_btn = QPushButton('Auto / recalculate\ncolors')
        self.auto_color_btn.setToolTip('Unlock both color endpoints and recalculate 0.5–99.5 percentiles from the visible current heatmap.')
        self.auto_color_btn.clicked.connect(self._on_auto_colors)
        color_lay.addWidget(self.auto_color_btn)
        self.color_range_status = QLabel()
        self.color_range_status.setWordWrap(True)
        color_lay.addWidget(self.color_range_status)
        self.color_clipping_status = QLabel()
        self.color_clipping_status.setWordWrap(True)
        color_lay.addWidget(self.color_clipping_status)

        # X interpretation, bounds and actions form one view-range task.
        view_box = QGroupBox("View Range")
        self._shared_view_range_box = view_box
        view_lay = QVBoxLayout(view_box)
        view_lay.setContentsMargins(8, 14, 8, 6)
        view_lay.setSpacing(4)
        axis_content = QWidget()
        axis_form = QFormLayout(axis_content)
        axis_form.setContentsMargins(0, 0, 0, 0)
        axis_form.setSpacing(4)
        axis_form.setHorizontalSpacing(8)
        axis_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        axis_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        axis_form.addRow("X-axis:", self.x_axis_override_combo)
        axis_form.addRow(self.axis_status_label)
        view_lay.addWidget(bounds_box)
        view_lay.addWidget(self._make_expander('X-axis interpretation', axis_content, expanded=False))

        annotation_box = QGroupBox("Labels")
        self._shared_annotations_box = annotation_box
        top_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        annotation_lay = QVBoxLayout(annotation_box)
        annotation_lay.setContentsMargins(8, 14, 8, 6)
        annotation_lay.setSpacing(4)
        annotation_lay.addLayout(top_form)
        annotation_lay.addWidget(self._make_expander('Label templates', template_content, expanded=False))

        lay.addWidget(color_box)
        lay.addWidget(view_box)
        lay.addWidget(annotation_box)

        self.color_scale_combo.currentIndexChanged.connect(self._on_color_scale_changed)
        self._sync_region_controls()
        protect_disclosure_drafts(w)

        return w

    # ── Find Peaks tab ────────────────────────────────────────────────────────

    def _build_find_peak_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(5)

        # ── Detection Mode ────────────────────────────────────────────────────
        mode_box = QGroupBox("Detection Mode")
        mode_l = QVBoxLayout(mode_box)
        mode_l.setContentsMargins(10, 14, 10, 8)
        mode_l.setSpacing(16)
        self._orig_radio = QRadioButton("Original Spectrum")
        self._d2_radio = QRadioButton("2nd Derivative")
        self._orig_radio.setToolTip(
            "Find peaks directly from the spectrum intensity.\nBest for clean, well-separated traces."
        )
        self._d2_radio.setToolTip(
            "Compute the second derivative to locate shoulders and overlapping peaks.\n"
            "More robust for noisy or closely spaced features."
        )
        self._orig_radio.setChecked(True)
        mode_l.addWidget(self._orig_radio)
        mode_l.addWidget(self._d2_radio)
        mode_l.addStretch(1)
        lay.addWidget(mode_box)

        # ── Original spectrum params ──────────────────────────────────────────
        self._orig_params_widget = QWidget()
        orig_form = QFormLayout(self._orig_params_widget)
        orig_form.setContentsMargins(2, 2, 2, 2)
        orig_form.setSpacing(3)
        orig_form.setHorizontalSpacing(8)
        orig_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        orig_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.peak_mode_combo = QComboBox()
        self.peak_mode_combo.addItems(["Peaks", "Dips", "Both"])
        self.peak_mode_combo.setToolTip("Peaks = maxima; Dips = minima; Both = detect each independently.")

        self.prom_spin = QDoubleSpinBox()
        self.prom_spin.setDecimals(4)
        self.prom_spin.setRange(0.0, 1.0)
        self.prom_spin.setSingleStep(0.005)
        self.prom_spin.setValue(0.05)
        self.prom_spin.setToolTip(
            "Minimum prominence as a fraction of the signal range.\n"
            "Higher = only the tallest peaks pass; Lower = more sensitive."
        )

        self.distance_spin = QSpinBox()
        self.distance_spin.setRange(1, 1000)
        self.distance_spin.setValue(5)
        self.distance_spin.setToolTip("Minimum separation between peaks in sample points.")

        self.max_peaks_spin = QSpinBox()
        self.max_peaks_spin.setRange(1, 30)
        self.max_peaks_spin.setValue(6)
        self.max_peaks_spin.setToolTip("Keep at most this many peaks after sorting by prominence.")

        self.max_dips_spin = QSpinBox()
        self.max_dips_spin.setRange(1, 30)
        self.max_dips_spin.setValue(6)
        self.max_dips_spin.setToolTip("Keep at most this many dips after sorting by prominence (Both mode only).")

        self.peak_smooth_chk = QCheckBox("Enable")
        self.peak_smooth_chk.setToolTip("Smooth the trace before peak finding to suppress noise spikes.")
        self.peak_smooth_win_spin = QSpinBox()
        self.peak_smooth_win_spin.setRange(3, 501)
        self.peak_smooth_win_spin.setValue(11)
        self.peak_smooth_win_spin.setMaximumWidth(65)
        self.peak_smooth_win_spin.setToolTip("Savitzky-Golay window length (must be odd).")
        smooth_row = self._hrow([self.peak_smooth_chk, QLabel("Win:"), self.peak_smooth_win_spin])

        orig_form.addRow("Type:", self.peak_mode_combo)
        orig_form.addRow("Prominence:", self.prom_spin)
        orig_form.addRow("Min Distance:", self.distance_spin)
        orig_form.addRow("Max Peaks:", self.max_peaks_spin)
        orig_form.addRow("Max Dips:", self.max_dips_spin)
        orig_form.addRow("Pre-smooth:", smooth_row)
        # Hide Max Dips row until "Both" is selected
        orig_form.labelForField(self.max_dips_spin).setVisible(False)
        self.max_dips_spin.setVisible(False)

        # ── Second-derivative params ──────────────────────────────────────────
        self._d2_params_widget = QWidget()
        d2_form = QFormLayout(self._d2_params_widget)
        d2_form.setContentsMargins(2, 2, 2, 2)
        d2_form.setSpacing(3)
        d2_form.setHorizontalSpacing(8)
        d2_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        d2_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.d2_mode_combo = QComboBox()
        self.d2_mode_combo.addItems(["Peaks", "Dips", "Both"])
        self.d2_mode_combo.setToolTip("Peaks = maxima; Dips = minima; Both = detect each independently.")

        self.d2_sg_win_spin = QSpinBox()
        self.d2_sg_win_spin.setRange(3, 501)
        self.d2_sg_win_spin.setValue(11)
        self.d2_sg_win_spin.setToolTip("Savitzky-Golay window for computing the second derivative (must be odd).")

        self.d2_sg_poly_spin = QSpinBox()
        self.d2_sg_poly_spin.setRange(1, 6)
        self.d2_sg_poly_spin.setValue(3)
        self.d2_sg_poly_spin.setToolTip("Polynomial order for the Savitzky-Golay filter.")

        self.d2_prom_spin = QDoubleSpinBox()
        self.d2_prom_spin.setDecimals(4)
        self.d2_prom_spin.setRange(0.0, 1.0)
        self.d2_prom_spin.setSingleStep(0.005)
        self.d2_prom_spin.setValue(0.03)
        self.d2_prom_spin.setToolTip("Prominence threshold as fraction of the d² signal range.")

        self.d2_dist_spin = QSpinBox()
        self.d2_dist_spin.setRange(1, 1000)
        self.d2_dist_spin.setValue(3)
        self.d2_dist_spin.setToolTip("Minimum separation between detected peaks in sample points.")

        self.d2_sep_spin = QDoubleSpinBox()
        self.d2_sep_spin.setDecimals(5)
        self.d2_sep_spin.setRange(0.0, 100.0)
        self.d2_sep_spin.setSingleStep(0.001)
        self.d2_sep_spin.setValue(0.0)
        self.d2_sep_spin.setToolTip("Minimum peak separation in energy units. 0 = use sample-point distance only.")

        self.d2_max_peaks_spin = QSpinBox()
        self.d2_max_peaks_spin.setRange(1, 30)
        self.d2_max_peaks_spin.setValue(6)
        self.d2_max_peaks_spin.setToolTip("Maximum candidates to keep after sorting by d² prominence.")

        self.d2_max_dips_spin = QSpinBox()
        self.d2_max_dips_spin.setRange(1, 30)
        self.d2_max_dips_spin.setValue(6)
        self.d2_max_dips_spin.setToolTip("Maximum dip candidates to keep by d² prominence (Both mode only).")

        self.d2_target_spin = QSpinBox()
        self.d2_target_spin.setRange(0, 30)
        self.d2_target_spin.setValue(0)
        self.d2_target_spin.setToolTip(
            "0 = keep top-N by Max Peaks.\n"
            "N > 0 = keep exactly N candidates sorted by d² prominence."
        )

        d2_form.addRow("Type:", self.d2_mode_combo)
        d2_form.addRow("SG Window:", self.d2_sg_win_spin)
        d2_form.addRow("SG Poly Order:", self.d2_sg_poly_spin)
        d2_form.addRow("D² Prominence:", self.d2_prom_spin)
        d2_form.addRow("Min Distance:", self.d2_dist_spin)
        d2_form.addRow("Min Sep (energy):", self.d2_sep_spin)
        d2_form.addRow("Max Peaks:", self.d2_max_peaks_spin)
        d2_form.addRow("Max Dips:", self.d2_max_dips_spin)
        d2_form.addRow("Target Count:", self.d2_target_spin)
        # Hide Max Dips row until "Both" is selected
        d2_form.labelForField(self.d2_max_dips_spin).setVisible(False)
        self.d2_max_dips_spin.setVisible(False)
        self._d2_params_widget.setVisible(False)

        params_box = QGroupBox("Detection Parameters")
        params_l = QVBoxLayout(params_box)
        params_l.setContentsMargins(8, 14, 8, 8)
        params_l.setSpacing(4)
        params_l.addWidget(self._orig_params_widget)
        params_l.addWidget(self._d2_params_widget)
        lay.addWidget(params_box)

        # ── Primary Actions ───────────────────────────────────────────────────
        action_row = QVBoxLayout()
        action_row.setSpacing(6)
        self.find_btn = QPushButton("Find Peaks (This Gate)")
        self.batch_find_btn = QPushButton("Find Peaks — All Gates")
        self.find_btn.setToolTip(
            "Detect peaks on the currently displayed spectrum trace only.\n"
            "Result appears as markers on the spectrum plot."
        )
        self.batch_find_btn.setToolTip(
            "Run peak detection on every gate and overlay results on the heatmap.\n"
            "Use this before 'Batch Fit All Gates' in the Peak Fit tab."
        )
        self.find_btn.setStyleSheet(_STYLE_PRIMARY)
        self.batch_find_btn.setStyleSheet(_STYLE_PRIMARY)
        action_row.addWidget(self.find_btn, 1)
        action_row.addWidget(self.batch_find_btn, 1)
        lay.insertLayout(0, action_row)
        self.peak_validation_label = QLabel()
        self.peak_validation_label.setWordWrap(True)
        self.peak_validation_label.setAccessibleName('Peak detection input guidance')
        lay.insertWidget(1, self.peak_validation_label)

        # ── Heatmap Tools ─────────────────────────────────────────────────────
        heatmap_box = QGroupBox("Heatmap Peak Editing")
        heatmap_l = QVBoxLayout(heatmap_box)
        heatmap_l.setContentsMargins(8, 14, 8, 8)
        heatmap_l.setSpacing(5)

        # Lasso + circle toggles row
        mode_btn_row = QVBoxLayout()
        mode_btn_row.setSpacing(6)
        self.lasso_mode_btn = QPushButton("Lasso Select")
        self.lasso_mode_btn.setCheckable(True)
        self.lasso_mode_btn.setToolTip(
            "Enable lasso mode: drag a freehand region on the heatmap to select\n"
            "all-gate peaks inside it. Normal gate selection is temporarily disabled."
        )
        self.lasso_mode_btn.setStyleSheet(_STYLE_TOGGLE)
        self.circle_delete_btn = QPushButton("Circle Delete")
        self.circle_delete_btn.setCheckable(True)
        self.circle_delete_btn.setToolTip(
            "Enable circle-delete mode: drag on the heatmap to define a circle\n"
            "and delete all all-gate peaks inside it."
        )
        self.circle_delete_btn.setStyleSheet(_STYLE_TOGGLE)
        mode_btn_row.addWidget(self.lasso_mode_btn, 1)
        mode_btn_row.addWidget(self.circle_delete_btn, 1)
        heatmap_l.addLayout(mode_btn_row)

        # Lasso helper hint (shown only when lasso is active)
        self.lasso_helper_label = QLabel(
            "Lasso active — drag on the heatmap to select peaks, then delete or clear below."
        )
        self.lasso_helper_label.setWordWrap(True)
        self.lasso_helper_label.setStyleSheet(
            "color: #e65100; font-size: 10px;"
            "background: #fff3e0; border-radius: 3px; padding: 3px 5px;"
        )
        self.lasso_helper_label.setVisible(False)
        heatmap_l.addWidget(self.lasso_helper_label)

        # Lasso action row
        lasso_action_row = QVBoxLayout()
        lasso_action_row.setSpacing(6)
        self.delete_lasso_btn = QPushButton("Delete Lasso\nSelection")
        self.delete_lasso_btn.setEnabled(False)
        self.delete_lasso_btn.setToolTip("Delete every heatmap peak inside the current lasso selection.")
        self.delete_lasso_btn.setStyleSheet(_STYLE_DANGER)
        self.clear_lasso_btn = QPushButton("Clear Selection")
        self.clear_lasso_btn.setEnabled(False)
        self.clear_lasso_btn.setToolTip("Keep the peaks but clear the lasso highlight.")
        self.clear_lasso_btn.setStyleSheet(_STYLE_MUTED)
        lasso_action_row.addWidget(self.delete_lasso_btn, 1)
        lasso_action_row.addWidget(self.clear_lasso_btn, 1)
        heatmap_l.addLayout(lasso_action_row)

        # Global cleanup row
        global_row = QVBoxLayout()
        global_row.setSpacing(6)
        self.clear_batch_peaks_btn = QPushButton("Clear All\nHeatmap Peaks")
        self.clear_batch_peaks_btn.setToolTip(
            "Remove every detected all-gates peak from the heatmap overlay.\n"
            "This does not affect single-gate trace peaks."
        )
        self.clear_batch_peaks_btn.setStyleSheet(_STYLE_DANGER)
        self.save_peaks_csv_btn = QPushButton("Save Peaks CSV")
        self.save_peaks_csv_btn.setToolTip(
            "Save the peaks currently overlaid on the heatmap to a CSV file.\n"
            "If no all-gates overlay exists, exports the current gate peaks only."
        )
        self.save_peaks_csv_btn.setStyleSheet(_STYLE_MUTED)
        global_row.addWidget(self.clear_batch_peaks_btn, 1)
        global_row.addWidget(self.save_peaks_csv_btn, 1)
        heatmap_l.addLayout(global_row)
        lay.addWidget(heatmap_box)

        # ── Current Trace Editing ─────────────────────────────────────────────
        trace_box = QGroupBox("Current-Trace Peak Editing")
        trace_l = QVBoxLayout(trace_box)
        trace_l.setContentsMargins(8, 14, 8, 8)
        trace_l.setSpacing(5)

        self.peak_delete_spin = QSpinBox()
        self.peak_delete_spin.setRange(1, 1)
        self.peak_delete_spin.setValue(1)
        self.peak_delete_spin.setMaximumWidth(60)
        self.peak_delete_spin.setToolTip("Choose which numbered peak to remove from the current gate trace.")

        self.delete_peak_btn = QPushButton("Delete Peak #")
        self.delete_peak_btn.setToolTip("Delete the peak selected by the spinner above from the current trace.")
        self.delete_peak_btn.setStyleSheet(_STYLE_DANGER)

        self.clear_peaks_btn = QPushButton("Clear All Trace Peaks")
        self.clear_peaks_btn.setToolTip("Remove all peaks from the current gate trace.")
        self.clear_peaks_btn.setStyleSheet(_STYLE_DANGER)

        del_row = QVBoxLayout()
        del_row.setSpacing(6)
        del_row.addWidget(QLabel("Peak #:"))
        del_row.addWidget(self.peak_delete_spin)
        del_row.addWidget(self.delete_peak_btn, 1)
        trace_l.addLayout(del_row)
        trace_l.addWidget(self.clear_peaks_btn)
        lay.addWidget(trace_box)

        # ── Interaction hint ──────────────────────────────────────────────────
        hint = QLabel(
            "Spectrum: L-click = add peak  |  R-click = remove nearest  |  Drag near peak = move\n"
            "Heatmap: L-click = jump to gate  |  R-click = remove peak on gate line"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "color: #607d8b; font-size: 10px;"
            "background: #f0f4f8; border-radius: 3px; padding: 3px 5px;"
        )
        lay.addWidget(hint)
        lay.addStretch(1)

        # ── wire signals ──────────────────────────────────────────────────────
        self._orig_radio.toggled.connect(self._on_detect_mode_changed)
        self.peak_mode_combo.currentTextChanged.connect(self._on_orig_type_changed)
        self.d2_mode_combo.currentTextChanged.connect(self._on_d2_type_changed)
        self.find_btn.clicked.connect(self._on_find_peaks)
        self.batch_find_btn.clicked.connect(self._on_batch_find_peaks_all_gates)
        self.lasso_mode_btn.toggled.connect(self._on_lasso_mode_toggled)
        self.delete_lasso_btn.clicked.connect(self._on_delete_lasso_selection)
        self.clear_lasso_btn.clicked.connect(self._on_clear_lasso_selection)
        self.save_peaks_csv_btn.clicked.connect(self._on_save_peaks_csv)
        self.circle_delete_btn.toggled.connect(self._on_toggle_circle_delete_mode)
        self.clear_batch_peaks_btn.clicked.connect(self._on_clear_batch_peaks)
        self.peak_delete_spin.valueChanged.connect(lambda _: self._update_spectrum_only())
        self.delete_peak_btn.clicked.connect(self._on_delete_peak_by_number)
        self.clear_peaks_btn.clicked.connect(self._on_clear_peaks)

        self._refresh_peak_delete_controls()
        return tab

    # ── Peak Fit tab ──────────────────────────────────────────────────────────

    def _build_lorentz_fit_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)

        # ── Seeds Source ──────────────────────────────────────────────────────
        seeds_box = QGroupBox("Initial Peak Seeds")
        seeds_l = QVBoxLayout(seeds_box)
        seeds_l.setContentsMargins(8, 14, 8, 8)
        seeds_l.setSpacing(5)

        self._seeds_from_find_peak_radio = QRadioButton("From Find Peaks tab")
        self._seeds_from_csv_radio = QRadioButton("From CSV file")
        self._seeds_from_find_peak_radio.setToolTip(
            "Use the peaks detected in the Find Peaks tab as initial centers for fitting.\n"
            "Run 'Find Peaks – All Gates' first for batch fitting."
        )
        self._seeds_from_csv_radio.setToolTip(
            "Load a CSV with columns [gate, peak_energy] to use as fit seeds.\n"
            "Useful when you have known peak positions from a reference."
        )
        self._seeds_from_find_peak_radio.setChecked(True)
        seeds_l.addWidget(self._seeds_from_find_peak_radio)
        seeds_l.addWidget(self._seeds_from_csv_radio)

        self._csv_seeds_row = QWidget()
        csv_row_l = QHBoxLayout(self._csv_seeds_row)
        csv_row_l.setContentsMargins(0, 2, 0, 0)
        csv_row_l.setSpacing(6)
        self.load_seeds_csv_btn = QPushButton("Load Seeds CSV…")
        self.load_seeds_csv_btn.setToolTip("Open a CSV file with [gate, peak_energy] columns.")
        self.load_seeds_csv_btn.setStyleSheet(_STYLE_MUTED)
        self._csv_path_label = QLabel("(none loaded)")
        self._csv_path_label.setStyleSheet(
            "color: #607d8b; font-size: 10px; font-style: italic;"
        )
        csv_row_l.addWidget(self.load_seeds_csv_btn)
        csv_row_l.addWidget(self._csv_path_label, 1)
        self._csv_seeds_row.setVisible(False)
        seeds_l.addWidget(self._csv_seeds_row)
        lay.addWidget(seeds_box)

        # Axis info label
        self.fit_axis_label = QLabel("Analysis axis: no file loaded.")
        self.fit_axis_label.setWordWrap(True)
        self.fit_axis_label.setStyleSheet(
            "color: #607d8b; font-size: 10px;"
            "background: #f0f4f8; border-radius: 3px; padding: 3px 5px;"
        )
        lay.addWidget(self.fit_axis_label)

        # ── Fit Range ─────────────────────────────────────────────────────────
        range_box = QGroupBox("Fit Range")
        range_form = QFormLayout(range_box)
        range_form.setContentsMargins(8, 14, 8, 8)
        range_form.setSpacing(5)
        range_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.fit_xmin_spin = QDoubleSpinBox()
        self.fit_xmax_spin = QDoubleSpinBox()
        for sp in (self.fit_xmin_spin, self.fit_xmax_spin):
            sp.setDecimals(6)
            sp.setRange(-1e12, 1e12)
            sp.setSingleStep(0.01)
            sp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            sp.setToolTip(
                "Constrain the fit to this x-axis window.\n"
                "Data outside this range is ignored by the optimizer."
            )
        self.fit_xmin_lock = QCheckBox("Fix")
        self.fit_xmax_lock = QCheckBox("Fix")
        for ck in (self.fit_xmin_lock, self.fit_xmax_lock):
            ck.setToolTip("When checked, this limit is not overwritten by 'Full Range'")
        self._fit_lock_controls = {
            self.fit_xmin_spin: self.fit_xmin_lock,
            self.fit_xmax_spin: self.fit_xmax_lock,
        }

        self.fit_full_btn = QPushButton("Full Range")
        self.fit_full_btn.setToolTip("Reset the fit range to the full x-axis span of the loaded file.")
        self.fit_full_btn.setStyleSheet(_STYLE_MUTED)

        self.use_current_x_range_btn = QPushButton("Use Current X Range")
        self.use_current_x_range_btn.setToolTip(
            "Copy the currently visible heatmap X bounds into the independent fit range."
        )
        self.use_current_x_range_btn.setStyleSheet(_STYLE_MUTED)

        xmax_w = QWidget()
        xmax_l = QHBoxLayout(xmax_w)
        xmax_l.setContentsMargins(0, 0, 0, 0)
        xmax_l.setSpacing(4)
        xmax_l.addWidget(self._view_spin_row(self.fit_xmax_spin, self.fit_xmax_lock))

        self.fit_smooth_chk = QCheckBox("Enable")
        self.fit_smooth_chk.setToolTip(
            "Apply Savitzky-Golay smoothing to the trace before fitting.\n"
            "Helps when noise is destabilizing curve_fit convergence."
        )
        self.fit_smooth_win_spin = QSpinBox()
        self.fit_smooth_win_spin.setRange(3, 501)
        self.fit_smooth_win_spin.setValue(11)
        self.fit_smooth_win_spin.setMaximumWidth(65)
        self.fit_smooth_win_spin.setToolTip("Savitzky-Golay window length (must be odd).")
        smooth_row = self._hrow([self.fit_smooth_chk, QLabel("Win:"), self.fit_smooth_win_spin])

        range_form.addRow("xmin:", self._view_spin_row(self.fit_xmin_spin, self.fit_xmin_lock))
        range_form.addRow("xmax:", xmax_w)
        range_form.addRow(self.fit_full_btn)
        range_form.addRow("Visible X:", self.use_current_x_range_btn)
        self.fit_range_summary_label = QLabel("Detection range: —\nFit range: —")
        self.fit_range_summary_label.setWordWrap(True)
        self.fit_range_summary_label.setStyleSheet(
            "color: #607d8b; font-size: 10px; background: #f0f4f8;"
            " border-radius: 3px; padding: 3px 5px;"
        )
        range_form.addRow(self.fit_range_summary_label)
        range_form.addRow("Pre-smooth:", smooth_row)
        lay.addWidget(range_box)

        # ── Fit Parameters ────────────────────────────────────────────────────
        params_box = QGroupBox("Fit Parameters")
        params_form = QFormLayout(params_box)
        params_form.setContentsMargins(8, 14, 8, 8)
        params_form.setSpacing(5)
        params_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.fit_model_combo = QComboBox()
        self.fit_model_combo.addItems(["Lorentzian", "Gaussian", "Voigt"])
        self.fit_model_combo.setCurrentText("Lorentzian")
        self.fit_model_combo.setToolTip(
            "Lorentzian: Cauchy-Lorentz lineshape — wider tails, typical for spectral lines.\n"
            "Gaussian: Gaussian lineshape — narrower tails, typical for inhomogeneous broadening."
        )

        self.lw_min_spin = QSpinBox()
        self.lw_min_spin.setRange(0, 10000)
        self.lw_min_spin.setSingleStep(1)
        self.lw_min_spin.setValue(10)
        self.lw_min_spin.setSuffix(" pt")
        self.lw_min_value_label = QLabel(self._format_pts_conversion(int(self.lw_min_spin.value())))
        self.lw_min_value_label.setStyleSheet("color: #607d8b; font-size: 10px;")

        self.lw_max_spin = QSpinBox()
        self.lw_max_spin.setRange(0, 10000)
        self.lw_max_spin.setSingleStep(1)
        self.lw_max_spin.setValue(50)
        self.lw_max_spin.setSuffix(" pt")
        self.lw_max_value_label = QLabel(self._format_pts_conversion(int(self.lw_max_spin.value())))
        self.lw_max_value_label.setStyleSheet("color: #607d8b; font-size: 10px;")

        self.peak_move_limit_spin = QSpinBox()
        self.peak_move_limit_spin.setRange(0, 10000)
        self.peak_move_limit_spin.setSingleStep(1)
        self.peak_move_limit_spin.setValue(10)
        self.peak_move_limit_spin.setSuffix(" pt")
        self.peak_move_limit_spin.setToolTip(
            "Maximum allowed shift of the fitted center from its seed in data points.\n"
            "0 = unconstrained. The equivalent size in the current analysis-axis units is shown to the right."
        )
        self.peak_move_value_label = QLabel(self._format_pts_conversion(int(self.peak_move_limit_spin.value())))
        self.peak_move_value_label.setStyleSheet("color: #607d8b; font-size: 10px;")

        self.maxfev_spin = QSpinBox()
        self.maxfev_spin.setRange(100, 2000000)
        self.maxfev_spin.setValue(20000)
        self.maxfev_spin.setSingleStep(5000)
        self.maxfev_spin.setToolTip(
            "Maximum number of function evaluations for scipy.optimize.curve_fit.\n"
            "Increase if fits are failing due to non-convergence."
        )

        params_form.addRow("Line Shape:", self.fit_model_combo)
        self.lw_min_name_label = QLabel()
        self.lw_max_name_label = QLabel()
        params_form.addRow(self.lw_min_name_label, self._hrow([self.lw_min_spin, self.lw_min_value_label]))
        params_form.addRow(self.lw_max_name_label, self._hrow([self.lw_max_spin, self.lw_max_value_label]))
        self.fit_width_help_label = QLabel()
        self.fit_width_help_label.setWordWrap(True)
        self.fit_width_help_label.setStyleSheet("color: #607d8b; font-size: 10px;")
        params_form.addRow(self.fit_width_help_label)
        params_form.addRow("Max Peak Shift:", self._hrow([self.peak_move_limit_spin, self.peak_move_value_label]))
        params_form.addRow("Max Iterations:", self.maxfev_spin)
        self._update_fit_param_unit_labels()
        lay.addWidget(params_box)

        # ── Primary fit actions ────────────────────────────────────────────────
        fit_action_row = QVBoxLayout()
        fit_action_row.setSpacing(6)
        self.fit_btn = QPushButton("Fit Current Gate")
        self.batch_fit_btn = QPushButton("Batch Fit All Gates")
        self.fit_btn.setToolTip(
            "Fit the currently displayed gate trace using seeds from the active source.\n"
            "Result overlaid immediately on the spectrum plot."
        )
        self.batch_fit_btn.setToolTip(
            "Fit every gate using seeds from the active source.\n"
            "Requires 'Find Peaks – All Gates' (or a seeds CSV) to have been run first."
        )
        self.fit_btn.setStyleSheet(_STYLE_PRIMARY)
        self.batch_fit_btn.setStyleSheet(_STYLE_PRIMARY)
        fit_action_row.addWidget(self.fit_btn, 1)
        fit_action_row.addWidget(self.batch_fit_btn, 1)
        lay.insertLayout(0, fit_action_row)

        # One context-sensitive current-gate action: it detects first when no
        # Find Peaks seeds exist, and fits directly once seeds are ready.
        self.detect_then_fit_btn = self.fit_btn
        self.fit_readiness_label = QLabel("Fit preparation: load data first.")
        self.fit_readiness_label.setWordWrap(True)
        self.fit_readiness_label.setStyleSheet("color: #607d8b; font-size: 10px;")
        lay.insertWidget(1, self.fit_readiness_label)

        # ── Secondary actions ──────────────────────────────────────────────────
        secondary_row = QVBoxLayout()
        secondary_row.setSpacing(6)
        self.clear_fit_btn = QPushButton("Clear Fit")
        self.export_current_fit_btn = QPushButton("Export Current Gate\nCSV")
        self.export_all_fit_btn = QPushButton("Export All Results\nCSV")
        self.clear_fit_btn.setToolTip("Remove the fit overlay from the spectrum plot.")
        self.export_current_fit_btn.setToolTip("Save only the selected gate's fit result to CSV.")
        self.export_all_fit_btn.setToolTip(
            "Save all individually fitted and batch-fitted gates, including recorded failures, to CSV."
        )
        self.clear_fit_btn.setStyleSheet(_STYLE_MUTED)
        self.export_current_fit_btn.setStyleSheet(_STYLE_MUTED)
        self.export_all_fit_btn.setStyleSheet(_STYLE_MUTED)
        secondary_row.addWidget(self.clear_fit_btn, 1)
        secondary_row.addWidget(self.export_current_fit_btn, 1)
        secondary_row.addWidget(self.export_all_fit_btn, 1)
        lay.addLayout(secondary_row)

        self.export_spec_btn = QPushButton("Export Spectrum CSV")
        self.export_spec_btn.setToolTip(
            "Save the current gate's raw spectrum, fit-range mask, and smoothed analysis trace to CSV."
        )
        self.export_spec_btn.setStyleSheet(_STYLE_MUTED)
        lay.addWidget(self.export_spec_btn)

        lay.addStretch(1)

        # ── wire signals ──────────────────────────────────────────────────────
        self._seeds_from_csv_radio.toggled.connect(self._on_seed_source_changed)
        self._seeds_from_find_peak_radio.toggled.connect(self._on_seed_source_changed)
        self.load_seeds_csv_btn.clicked.connect(self._on_load_seeds_csv)
        self.fit_xmin_spin.valueChanged.connect(self._on_fit_range_changed)
        self.fit_xmax_spin.valueChanged.connect(self._on_fit_range_changed)
        self.fit_full_btn.clicked.connect(self._on_set_fit_full_range)
        self.use_current_x_range_btn.clicked.connect(self._on_use_current_x_range)
        self.batch_fit_btn.clicked.connect(self._on_batch_fit_all_gates)
        self.fit_btn.clicked.connect(self._on_fit_or_detect_current_gate)
        self.clear_fit_btn.clicked.connect(self._on_clear_fit)
        self.export_current_fit_btn.clicked.connect(self._on_export_current_fit_csv)
        self.export_all_fit_btn.clicked.connect(self._on_export_all_fit_csv)
        self.export_spec_btn.clicked.connect(self._on_export_spectrum_csv)
        return tab

    # ── plot panel ────────────────────────────────────────────────────────────

    def _build_plot_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.figure = Figure(figsize=(8.0, 6.0), layout="none")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("resize_event", self._layout_live_plots)
        self.canvas.mpl_connect("draw_event", self._update_live_heatmap_tick_contrast)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.toolbar = _AnalysisNavigationToolbar(self.canvas, self)

        analysis_header = QWidget()
        header_layout = QVBoxLayout(analysis_header)
        header_layout.setContentsMargins(8, 6, 8, 6)
        self.analysis_back_btn = QPushButton('← Back to Workspace')
        self.analysis_back_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.analysis_back_btn.clicked.connect(self._back_to_workspace)
        header_layout.addWidget(self.analysis_back_btn, 0, Qt.AlignLeft)
        self.analysis_context_label = QLabel()
        self.analysis_context_label.setAccessibleName('Analysis source and scope')
        self.analysis_context_label.setTextFormat(Qt.PlainText)
        self.analysis_context_label.setWordWrap(True)
        self.analysis_context_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header_layout.addWidget(self.analysis_context_label, 1)
        lay.addWidget(analysis_header)

        # ── Gate navigation bar ───────────────────────────────────────────────
        gate_bar = QFrame()
        gate_bar.setFrameShape(QFrame.StyledPanel)
        gate_bar.setStyleSheet(
            "QFrame { background: #f5f7fa; border: none; border-bottom: 1px solid #ddd; }"
        )
        gate_lay = QHBoxLayout(gate_bar)
        gate_lay.setContentsMargins(8, 4, 8, 4)
        gate_lay.setSpacing(6)

        gate_lbl = QLabel("Gate:")
        gate_lbl.setStyleSheet("font-weight: 600; color: #444; font-size: 11px;")

        self.plot_gate_spin = GateValueSpinBox()
        self.plot_gate_spin.setDecimals(6)
        self.plot_gate_spin.setRange(-1e12, 1e12)
        self.plot_gate_spin.setKeyboardTracking(False)
        self.plot_gate_spin.setMinimumWidth(120)
        self.plot_gate_spin.setMaximumWidth(160)
        self.plot_gate_spin.setToolTip(
            "Current gate value. Spin or type to navigate; plot updates immediately."
        )

        sep_lbl = QLabel("|")
        sep_lbl.setStyleSheet("color: #ccc; font-size: 14px;")

        idx_lbl = QLabel("Index:")
        idx_lbl.setStyleSheet("color: #555; font-size: 11px;")

        self.plot_gate_index_spin = QSpinBox()
        self.plot_gate_index_spin.setRange(1, 1)
        self.plot_gate_index_spin.setMinimumWidth(55)
        self.plot_gate_index_spin.setMaximumWidth(70)
        self.plot_gate_index_spin.setToolTip("Gate index (1-based). Spin to step through all gates.")

        self.plot_prev_gate_btn = QPushButton("◀")
        self.plot_next_gate_btn = QPushButton("▶")
        for btn in (self.plot_prev_gate_btn, self.plot_next_gate_btn):
            btn.setFixedWidth(30)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                "QPushButton {"
                "  background: #fff; border: 1px solid #ccc; border-radius: 3px;"
                "  font-size: 10px; color: #333;"
                "}"
                "QPushButton:hover { background: #e8f0fe; border-color: #1976d2; }"
                "QPushButton:pressed { background: #c5d8fb; }"
                "QPushButton:disabled { color: #bbb; border-color: #e0e0e0; }"
            )
        self.plot_prev_gate_btn.setToolTip("Step to the previous gate (←)")
        self.plot_next_gate_btn.setToolTip("Step to the next gate (→)")

        gate_lay.addWidget(gate_lbl)
        gate_lay.addWidget(self.plot_gate_spin)
        gate_lay.addWidget(self.plot_prev_gate_btn)
        gate_lay.addWidget(self.plot_next_gate_btn)
        gate_lay.addWidget(sep_lbl)
        gate_lay.addWidget(idx_lbl)
        gate_lay.addWidget(self.plot_gate_index_spin)
        gate_lay.addStretch(1)

        # Keep reference for legacy attribute access
        self.plot_gate_row = gate_bar

        toolbar_row = QWidget()
        toolbar_lay = QHBoxLayout(toolbar_row)
        toolbar_lay.setContentsMargins(0, 0, 0, 0)
        toolbar_lay.setSpacing(6)
        toolbar_lay.addWidget(self.toolbar, 1)
        toolbar_lay.addWidget(QLabel('Display:'))
        self.analysis_zoom_combo = QComboBox()
        self.analysis_zoom_combo.addItems(['Fit', '100%', '150%', '200%'])
        toolbar_lay.addWidget(self.analysis_zoom_combo)
        lay.addWidget(toolbar_row)
        lay.addWidget(gate_bar)
        self.analysis_scroll = QScrollArea()
        self.analysis_scroll.setWidgetResizable(True)
        self.analysis_scroll.setAlignment(Qt.AlignCenter)
        self.analysis_scroll.setWidget(self.canvas)
        lay.addWidget(self.analysis_scroll, 1)
        self.plot_tabs = QTabWidget()
        self.plot_tabs.addTab(panel, 'Analysis')
        self.graph_documents = GraphDocuments(self._activate_document, self._finish_close_document)
        self.graph_documents.layoutChanged.connect(self._schedule_plot_page_render)
        self.graph_documents.layoutChanged.connect(self._refresh_graph_commands)
        self.graph_documents.workspaceChanged.connect(self._schedule_project_modified)
        self.graph_documents.workspaceChanged.connect(self._refresh_navigator_active)
        self.graph_command_host = QWidget()
        self.graph_command_host.setAccessibleName('Active graph command bar')
        self._graph_command_layout = QVBoxLayout(self.graph_command_host)
        self._graph_command_layout.setContentsMargins(4, 2, 4, 2)
        # A single command row reserves the same footprint for every document.
        self.graph_command_host.setFixedHeight(40)
        self._graph_command_stack = QStackedWidget()
        self._graph_command_stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._graph_command_layout.addWidget(self._graph_command_stack)
        self.workspace_command_context = QLabel('Open a Workbook or graph from Project.')
        self.workspace_command_context.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._graph_command_stack.addWidget(self.workspace_command_context)
        self.workbook_commands = WorkbookPlotCommands()
        self.workbook_commands.targetSelected.connect(self._select_comparison_target)
        self.workbook_commands.targetPinChanged.connect(self._pin_comparison_target)
        self.workbook_commands.targetShowRequested.connect(self._show_comparison_target)
        self.workbook_commands.repeatRequested.connect(
            lambda sheet, target: self._add_sheet_comparison(sheet, target, include_existing=True))
        self._graph_command_stack.addWidget(self.workbook_commands)
        self._fallback_plot_page = self._create_plot_page()
        self._size_workspace_commands()
        self._fallback_plot_page.setParent(self.graph_documents)
        self._fallback_plot_page.hide()
        self.plot_page = self._fallback_plot_page
        self.analysis_scroll.viewport().installEventFilter(self)
        self.analysis_zoom_combo.currentTextChanged.connect(lambda text: self.plot_page.set_zoom(text))
        self.export_png_btn = self.plot_page.export_png_btn
        self._settings_menu = QMenu('Plot settings', self)
        self._settings_copy_action = self._settings_menu.addAction('Copy settings', self._on_copy_plot_settings)
        self._settings_paste_action = self._settings_menu.addAction('Paste settings…', self._on_paste_plot_settings)
        self._settings_copy_to_action = self._settings_menu.addAction('Copy settings to active graphs…', self._on_copy_settings_to_datasets)
        settings_button = QToolButton()
        settings_button.setText('Settings')
        settings_button.setMenu(self._settings_menu)
        settings_button.setPopupMode(QToolButton.InstantPopup)
        self.toolbar.addWidget(settings_button)
        self._comparison_action = self.toolbar.addAction('Open comparison window')
        self._comparison_action.triggered.connect(self._on_open_plot_comparison)
        self._refresh_transfer_controls()
        self.plot_tabs.addTab(self.graph_documents, 'Workspace')
        self.plot_tabs.tabBar().hide()
        self.plot_tabs.installEventFilter(self)
        self.plot_tabs.currentChanged.connect(self._on_plot_tab_changed)
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self.graph_command_host)
        workspace_layout.addWidget(self.plot_tabs, 1)
        return workspace

    # =========================================================================
    #  Helper utilities
    # =========================================================================

    def _build_data_navigator(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        header = QHBoxLayout()
        header.addWidget(QLabel('Project'), 1)
        self.import_xy_btn = QToolButton()
        self.import_xy_btn.setText('Import')
        self.import_xy_btn.setAccessibleName('Import data files')
        self.import_xy_btn.setToolTip('Automatically import full tables into Workbooks: first column X, all remaining columns Y. Use Actions → Import with options for a preview.')
        self.import_xy_btn.setFocusPolicy(Qt.StrongFocus)
        self.import_xy_btn.clicked.connect(self._on_import_data)
        header.addWidget(self.import_xy_btn)
        self.project_actions_btn = QToolButton()
        self.project_actions_btn.setText('Actions')
        self.project_actions_btn.setAccessibleName('Project actions')
        self.project_actions_btn.setToolTip('Open, compare, organize or remove Project items.')
        self.project_actions_btn.setFocusPolicy(Qt.StrongFocus)
        self.project_actions_menu = QMenu(self.project_actions_btn)
        self.project_actions_menu.setToolTipsVisible(True)
        self.project_actions_menu.addAction(self._graph_command_actions['import_options'])
        self.project_actions_btn.setMenu(self.project_actions_menu)
        self.project_actions_btn.setPopupMode(QToolButton.InstantPopup)
        header.addWidget(self.project_actions_btn)
        layout.addLayout(header)
        self.new_comparison_action = self.project_actions_menu.addAction('New comparison graph', self._new_xy_comparison)
        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText('Search names or source paths')
        self.project_search.setClearButtonEnabled(True)
        self.project_search.setAccessibleName('Search Project names and source paths')
        layout.addWidget(self.project_search)
        self.data_navigator = ProjectTreeWidget()
        self.project_search.textChanged.connect(self.data_navigator.filter_items)
        self.data_navigator.setHeaderHidden(True)
        self.data_navigator.setAccessibleName('Data and graphs navigator')
        self.data_navigator.setToolTip('Ctrl/Shift selects Sheets or Workbooks. Double-click or press Enter to open.')
        self.data_navigator.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.data_navigator.setContextMenuPolicy(Qt.CustomContextMenu)
        self.data_navigator.itemDoubleClicked.connect(lambda *_: self._open_navigator_item(toggle_group=False))
        self.data_navigator.installEventFilter(self)
        self.data_navigator.itemSelectionChanged.connect(self._navigator_selection_changed)
        self.data_navigator.customContextMenuRequested.connect(self._navigator_menu)
        layout.addWidget(self.data_navigator, 1)
        # Keep established command hooks as real menu actions, rather than
        # maintaining invisible duplicate button stacks.
        self.compare_sheets_btn = self.project_actions_menu.addAction('Compare selected data…', self._compare_selected_sheets)
        self.merge_workbooks_btn = self.project_actions_menu.addAction('Move sheets and merge…', self._on_merge_workbooks)
        self.project_actions_menu.addSeparator()
        self.project_analyze_action = self.project_actions_menu.addAction(
            'Analyze', lambda: self._open_analysis(self._project_analysis_source()))
        self._navigator_buttons = {}
        for text, handler in (('Open', self._open_navigator_item), ('Rename', self._rename_navigator_item),
                              ('Remove', self._remove_navigator_item)):
            self._navigator_buttons[text] = self.project_actions_menu.addAction(
                text if text == 'Open' else text + '…', handler)
        self.reload_source_action = self.project_actions_menu.addAction('Reload as new copy…', self._on_reload_source_copy)
        self._file_menu.addAction(self.reload_source_action)
        self.project_actions_menu.addSeparator()
        self.add_gate_comparison_btn = self.project_actions_menu.addAction('Add current DAT Gate to comparison', self._add_current_gate_comparison)
        self.add_gate_again_action = self.project_actions_menu.addAction('Add current DAT Gate again',
            lambda: self._add_current_gate_comparison(include_existing=True))
        self.project_actions_menu.aboutToShow.connect(self._refresh_navigator_actions)
        self._project_panel_layout.addWidget(panel)
        self._refresh_data_navigator()

    def _navigator_items(self):
        """Visit every row, including collapsed source and document groups."""
        def descendants(parent):
            for index in range(parent.childCount()):
                item = parent.child(index)
                yield item
                yield from descendants(item)
        yield from descendants(self.data_navigator.invisibleRootItem())

    def _navigator_source_labels(self):
        paths = {}
        for sheet in self.workspace.sheets.values():
            if sheet.source_path is not None:
                paths.setdefault(_source_key(sheet.source_path), sheet.source_path)
        for session in self.workspace.datasets.values():
            if session.source_path is not None:
                paths.setdefault(_source_key(session.source_path), session.source_path)
        labels = {}
        for key, path in paths.items():
            peers = [other for identity, other in paths.items()
                     if identity != key and other.name.casefold() == path.name.casefold()]
            label = path.name
            if peers:
                parts = path.parent.parts
                suffix = str(path.parent)
                for depth in range(1, len(parts) + 1):
                    tail = tuple(part.casefold() for part in parts[-depth:])
                    if all(tuple(part.casefold() for part in other.parent.parts[-depth:]) != tail
                           for other in peers):
                        suffix = str(Path(*parts[-depth:]))
                        break
                label += ' — ' + suffix
            labels[key] = label
        return labels

    def _refresh_data_navigator(self):
        if not hasattr(self, 'data_navigator'):
            return
        tree = self.data_navigator
        current = tree.currentItem()
        current_key = current.data(0, Qt.UserRole) if current else None
        selected_keys = {item.data(0, Qt.UserRole) for item in tree.selectedItems()}
        expanded = {item.data(0, Qt.UserRole): item.isExpanded() for item in self._navigator_items()}
        scroll = (tree.horizontalScrollBar().value(), tree.verticalScrollBar().value())
        items, source_items, owners = {}, {}, {}
        source_labels = self._navigator_source_labels()
        folder_icon = tree.style().standardIcon(QStyle.SP_DirIcon)
        sheet_icon = tree.style().standardIcon(QStyle.SP_FileIcon)
        heatmap_icon, comparison_icon = plot_icon('heatmap'), plot_icon('line')

        def add(parent, key, label, icon, tooltip, description):
            item = QTreeWidgetItem(parent, [label])
            item.setData(0, Qt.UserRole, key)
            item.setIcon(0, icon)
            item.setToolTip(0, tooltip)
            item.setData(0, Qt.UserRole + 2, tooltip)
            item.setData(0, Qt.AccessibleDescriptionRole, description)
            items[key] = item
            return item

        def source_key(sheet):
            return _source_key(sheet.source_path) if sheet.source_path is not None else None

        with QSignalBlocker(tree):
            tree.clear()
            for book in self.workspace.workbooks.values():
                sheets = [self.workspace.sheets[sid] for sid in book.sheet_ids]
                sources = {}
                for sheet in sheets:
                    sources.setdefault(source_key(sheet), []).append(sheet)
                paths = [key for key in sources if key is not None]
                label = book.name
                if len(sources) == 1 and paths:
                    path = sources[paths[0]][0].source_path
                    # A user alias never replaces the full source filename.
                    label = source_labels[paths[0]] if book.name == path.name else (
                        book.name + '\u2028' + source_labels[paths[0]])
                elif paths:
                    label += f' · {len(paths)} sources'
                    if None in sources:
                        label += ' + embedded values'
                else:
                    label += f' · {len(sheets)} embedded Sheets'
                item = add(tree, ('workbook', book.workbook_id), label, folder_icon,
                           self._workbook_tooltip(book), 'Workbook')
                for path_key, source_sheets in sources.items():
                    parent = item
                    if len(sources) > 1:
                        path = source_sheets[0].source_path
                        parent = add(item, ('group', ('source', book.workbook_id, path_key)),
                                     source_labels[path_key] if path_key is not None else 'Embedded values',
                                     folder_icon, str(path or 'Embedded values'), 'Source group')
                    source_items[(book.workbook_id, path_key)] = parent
                    data = add(parent, ('group', ('worksheets', book.workbook_id, path_key)),
                               f'Worksheets ({len(source_sheets)})', sheet_icon,
                               'Data sheets for this source', 'Worksheets group')
                    for sheet in source_sheets:
                        add(data, ('sheet', sheet.sheet_id), sheet.name, sheet_icon,
                            str(sheet.source_path or 'Embedded values'), 'Worksheet')
                        if sheet.kind == 'dat' and sheet.role == 'original':
                            owners.setdefault(sheet.dataset_id, (book.workbook_id, path_key))

            # Reloaded copies may have the same name and the same provenance.
            # Retain the established numeric collision qualifier without hiding
            # any part of the source filename or changing the Workbook name.
            duplicate_labels = {}
            for book in self.workspace.workbooks.values():
                item = items[('workbook', book.workbook_id)]
                duplicate_labels.setdefault(item.text(0).casefold(), []).append(item)
            for duplicates in duplicate_labels.values():
                if len(duplicates) > 1:
                    for index, item in enumerate(duplicates, 1):
                        item.setText(0, item.text(0) + f' ({index})')

            # Match dataset identity to its Original sheet, never a filename.
            # A detached/legacy dataset still has a stable reachable source row.
            heatmaps = {}
            for session in self.workspace.datasets.values():
                graphs = [graph for graph in session.graphs.values() if graph.published]
                if not graphs:
                    continue
                owner = owners.get(session.dataset_id)
                parent = source_items.get(owner)
                if parent is None:
                    path = session.source_path
                    parent = add(tree, ('group', ('dataset', session.dataset_id)),
                                 source_labels[_source_key(path)] if path is not None else 'Embedded DAT',
                                 folder_icon, str(path or 'Embedded DAT'), 'DAT source group')
                parent_key = parent.data(0, Qt.UserRole)
                heatmaps.setdefault(parent_key, (parent, []))[1].extend((session, graph) for graph in graphs)
            for parent_key, (parent, graphs) in heatmaps.items():
                group = add(parent, ('group', ('heatmaps', parent_key)), f'Heatmaps ({len(graphs)})',
                            heatmap_icon, 'Published heatmap graphs for this source', 'Heatmaps group')
                for session, graph in graphs:
                    add(group, ('heatmap', (session.dataset_id, graph.graph_id)), graph.name,
                        heatmap_icon, f'Heatmap: {graph.name}\n{session.source_path or "Embedded DAT"}',
                        'Heatmap graph')
            if self.workspace.comparisons:
                group = add(tree, ('group', ('comparisons',)),
                            f'Comparisons ({len(self.workspace.comparisons)})', comparison_icon,
                            'Independent XY graphs; curves may use multiple sources', 'Comparisons group')
                for comparison in self.workspace.comparisons.values():
                    sources = set()
                    for curve in comparison.curves.values():
                        sheet = self.workspace.sheets.get(curve.sheet_id)
                        if sheet is not None:
                            sources.add(('path', source_key(sheet)) if sheet.source_path is not None
                                        else ('embedded', sheet.sheet_id))
                    context = f' · {len(sources)} sources' if len(sources) > 1 else ''
                    source_paths = '\n'.join(str(self.workspace.sheets[curve.sheet_id].source_path or '')
                        for curve in comparison.curves.values() if curve.sheet_id in self.workspace.sheets)
                    add(group, ('xy', comparison.comparison_id), comparison.name + context,
                        comparison_icon, f'XY graph: {comparison.name}\n{len(sources)} sources\n{source_paths}', 'XY comparison graph')
            if current_key in items:
                tree.setCurrentItem(items[current_key], 0, QItemSelectionModel.NoUpdate)
            for key, item in items.items():
                item.setExpanded(expanded.get(key, True))
                if key in selected_keys:
                    item.setSelected(True)
            self._refresh_navigator_active()
            tree.filter_items(self.project_search.text())
            tree.doItemsLayout()
            tree.horizontalScrollBar().setValue(scroll[0])
            tree.verticalScrollBar().setValue(scroll[1])
        self._refresh_navigator_actions()

    def _workbook_tooltip(self, book):
        paths = workbook_sources(self.workspace, book)
        return (f'{book.name} · {len(book.sheet_ids)} Sheets · {len(paths)} sources\n'
                + ('\n'.join(str(path) for path in paths) if paths else 'Embedded values'))

    def _refresh_navigator_active(self):
        if not hasattr(self, 'data_navigator'):
            return
        key = self.graph_documents._active_key if self.plot_tabs.currentWidget() is self.graph_documents else self._document_key()
        active = set()
        if key:
            active.add((key[0], key[1]) if len(key) == 3 else ('heatmap', key))
            page = self.graph_documents.page_for(key)
            if len(key) == 3 and key[0] == 'workbook' and isinstance(page, WorkbookPage):
                sheet_page = page.tabs.currentWidget()
                if sheet_page is not None:
                    active.add(('sheet', sheet_page.sheet_id))
        for item in self._navigator_items():
            item_key = item.data(0, Qt.UserRole)
            current = item_key in active
            font = item.font(0)
            font.setBold(current or item_key[0] in ('workbook', 'group'))
            font.setUnderline(current)
            item.setFont(0, font)
            item.setData(0, Qt.UserRole + 1, current)
            description = (item.data(0, Qt.AccessibleDescriptionRole) or '').removesuffix(' · Active document')
            item.setData(0, Qt.AccessibleDescriptionRole, description + (' · Active document' if current else ''))
            tooltip = item.toolTip(0).removesuffix('\nActive document')
            item.setToolTip(0, tooltip + ('\nActive document' if current else ''))

    def _selected_sheet_ids(self):
        selected = {item.data(0, Qt.UserRole) for item in self.data_navigator.selectedItems()}
        return [sid for book in self.workspace.workbooks.values() for sid in book.sheet_ids
                if ('sheet', sid) in selected or (('workbook', book.workbook_id) in selected
                    and self.workspace.sheets[sid].role == 'original')]

    def _refresh_navigator_actions(self):
        if not hasattr(self, 'compare_sheets_btn'):
            return
        idle = not self._project_busy()
        selected = self.data_navigator.selectedItems()
        key = selected[0].data(0, Qt.UserRole) if len(selected) == 1 else None
        editable = bool(key and key[0] in ('sheet', 'workbook', 'xy'))
        current = self.data_navigator.currentItem()
        current_key = current.data(0, Qt.UserRole) if current else None
        self.compare_sheets_btn.setEnabled(idle and any(item.data(0, Qt.UserRole)[0] in ('sheet', 'workbook') for item in selected))
        self.merge_workbooks_btn.setEnabled(idle and len(self._selected_workbook_ids()) >= 2)
        for name in ('Rename', 'Remove'):
            self._navigator_buttons[name].setEnabled(idle and editable)
        remove = self._navigator_buttons['Remove']
        target = selected[0].text(0) if len(selected) == 1 else f'{len(selected)} selected items'
        remove.setText(f'Remove {target}…' if selected else 'Remove…')
        reason = self._navigator_remove_reason(key)
        remove.setEnabled(idle and editable and not reason)
        remove.setToolTip(reason or (f'Remove {target} from this project. Source files remain unchanged.' if selected else 'Select one Project item.'))
        self.reload_source_action.setEnabled(idle and bool(self._selected_reload_paths()))
        self.reload_source_action.setToolTip('Read selected original sources from disk into new Workbooks. Existing data, graphs and results stay unchanged.')
        self._navigator_buttons['Open'].setEnabled(idle and bool(current_key and
            current_key[0] in ('sheet', 'workbook', 'xy', 'heatmap')))
        self.import_xy_btn.setEnabled(idle)
        self.new_comparison_action.setEnabled(idle)
        self.add_gate_comparison_btn.setEnabled(idle and self.workspace.active is not None
            and not self._auxiliary_active() and (self.plot_tabs.currentIndex() == 0 or
            self.graph_documents.window_for(self._document_key()) is not None)
            and (not self._comparison_target_pinned or self._last_comparison_id in self.workspace.comparisons))
        self.add_gate_again_action.setEnabled(self.add_gate_comparison_btn.isEnabled()
            and self._last_comparison_id in self.workspace.comparisons)
        self.project_analyze_action.setEnabled(idle and self._project_analysis_source() is not None)

    def _navigator_selection_changed(self):
        self._refresh_navigator_actions()
        item = self.data_navigator.currentItem()
        if item and item.data(0, Qt.UserRole)[0] == 'xy' and not self._comparison_target_pinned:
            self._last_comparison_id = item.data(0, Qt.UserRole)[1]
            self._refresh_comparison_targets(self._last_comparison_id)

    def _navigator_menu(self, position):
        item = self.data_navigator.itemAt(position)
        if item is None:
            return
        if item.isSelected():
            self.data_navigator.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
        else:
            self.data_navigator.setCurrentItem(item)
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self._refresh_navigator_actions()
        if item.data(0, Qt.UserRole)[0] == 'group':
            menu.addAction('Collapse' if item.isExpanded() else 'Expand',
                           lambda: item.setExpanded(not item.isExpanded()))
            menu.exec(self.data_navigator.viewport().mapToGlobal(position))
            menu.deleteLater()
            return
        menu.addAction(self._navigator_buttons['Open'])
        menu.addAction(self.project_analyze_action)
        menu.addAction(self.compare_sheets_btn)
        menu.addAction(self.merge_workbooks_btn)
        menu.addAction(self._navigator_buttons['Rename'])
        menu.addAction(self.reload_source_action)
        menu.addAction(self._navigator_buttons['Remove'])
        menu.exec(self.data_navigator.viewport().mapToGlobal(position))
        menu.deleteLater()

    def _sheet_owner(self, sheet_id):
        return next(book for book in self.workspace.workbooks.values() if sheet_id in book.sheet_ids)

    def _resolve_analysis_source(self, source):
        """Resolve only the explicitly requested graph or live DAT data sheet."""
        if source is None:
            return None
        kind, identity = source
        if kind == 'heatmap':
            session = self.workspace.datasets.get(identity[0])
            if session is not None and session.current_cube is not None and identity[1] in session.graphs:
                return source
        elif kind == 'xy':
            return source if identity in self.workspace.comparisons else None
        elif kind == 'sheet':
            sheet = self.workspace.sheets.get(identity)
            if sheet is not None and sheet.kind == 'dat' and sheet.role in ('original', 'processed'):
                session = self.workspace.datasets.get(sheet.dataset_id)
                if session is not None and session.current_cube is not None:
                    return source
        elif kind == 'workbook':
            book = self.workspace.workbooks.get(identity)
            if book is not None:
                page = self.graph_documents.page_for(('workbook', identity, ''))
                if isinstance(page, WorkbookPage) and page.tabs.currentWidget() is not None:
                    current = self._resolve_analysis_source(('sheet', page.tabs.currentWidget().sheet_id))
                    if current is not None:
                        return current
                sources = [self._resolve_analysis_source(('sheet', sid)) for sid in book.sheet_ids]
                sources = [item for item in sources if item is not None]
                if len({self.workspace.sheets[item[1]].dataset_id for item in sources}) == 1:
                    return sources[0]
        return None

    def _project_analysis_source(self):
        selected = self.data_navigator.selectedItems()
        return self._resolve_analysis_source(selected[0].data(0, Qt.UserRole)) if len(selected) == 1 else None

    def _active_analysis_source(self):
        if self.plot_tabs.currentIndex() == 0:
            return self._resolve_analysis_source(('heatmap', self._document_key()))
        key = self.graph_documents._active_key
        if key is None or self.graph_documents.window_for(key) is None:
            return None
        if len(key) == 2:
            return self._resolve_analysis_source(('heatmap', key))
        if key[0] == 'xy':
            return self._resolve_analysis_source(('xy', key[1]))
        if key[0] == 'workbook':
            page = self.graph_documents.page_for(key)
            sheet_page = page.tabs.currentWidget() if isinstance(page, WorkbookPage) else None
            if sheet_page is not None:
                return self._resolve_analysis_source(('sheet', sheet_page.sheet_id))
        return None

    def _open_analysis(self, source):
        source = self._resolve_analysis_source(source)
        if source is None:
            self.statusBar().showMessage('Choose an XY graph, DAT heatmap, Original sheet or Processed sheet to analyze.')
            return
        try:
            if source[0] != 'xy':
                return self._workbench_open(source)
            self._require_project_idle()
            self._finish_current_document()
            kind, identity = source
            if kind == 'xy':
                page = self.open_xy_comparison(identity)
                page.finish_editor()
                self._set_dat_details_requested(True)
                page.analysis_panel.analyze_selected()
                page.inspector.ensureWidgetVisible(page.analysis_panel)
                return
            if kind == 'heatmap':
                dataset_id, graph_id = identity
                if self.plot_tabs.currentIndex() == 0 and identity == self._document_key():
                    self._set_dat_details_requested(True)
                    self._sidebar_tabs.setCurrentWidget(self._analysis_sidebar_scroll)
                    self._refresh_analysis_context()
                    return
                self.open_graph_document(dataset_id, graph_id)
            else:
                sheet = self.workspace.sheets[identity]
                dataset_id = sheet.dataset_id
                self.open_workbook(self._sheet_owner(identity).workbook_id, identity)
            return_key = self.graph_documents._active_key
            self.plot_tabs.setCurrentIndex(0)
            if self.plot_tabs.currentIndex() != 0:
                return
            if dataset_id != self.workspace.active_dataset_id:
                self.activate_dataset(dataset_id)
            self._analysis_return_key = return_key
            self._set_dat_details_requested(True)
            self._sidebar_tabs.setCurrentWidget(self._analysis_sidebar_scroll)
            self._refresh_graph_commands()
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))

    def _back_to_workspace(self):
        try:
            self._require_project_idle()
            self._finish_current_document()
            key = self._analysis_return_key
            if key is None:
                key = self.graph_documents._active_key
            if key and document_exists(self.workspace, key) and self.graph_documents.window_for(key) is not None:
                if len(key) == 2:
                    self.open_graph_document(*key)
                else:
                    self._open_auxiliary_document(key)
            else:
                # A closed/removed source stays closed. Do not reopen the
                # scientific selection just because the container changes.
                with QSignalBlocker(self.plot_tabs):
                    self.plot_tabs.setCurrentWidget(self.graph_documents)
                active = self.graph_documents._active_key
                if active and document_exists(self.workspace, active):
                    self._activate_document(active)
                self._on_plot_tab_changed(1, restore_graph=False)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))

    def _refresh_analysis_context(self):
        if not hasattr(self, 'analysis_context_label'):
            return
        session = self.workspace.active
        document = self._workbench_document()
        if document is not None:
            cube = document.science.current_cube
            index = self._current_gate_index()
            graph = session.active_graph
            source = session.graphs.get(document.source_graph_id)
            count = cube.gate.size if graph.gate_indices is None else len(graph.gate_indices)
            details = (
                f'Analysis — {session.source_path.name if session.source_path else "Embedded data"} · {graph.name}\n'
                f'Analysis input: {document.input_label} · version {document.input_version[:8]} · '
                f'Gate: {cube.gate[index]:g} ({index+1}/{cube.gate.size})\n'
                f'Graph display: {count} of {cube.gate.size} Gates · All Gates analyzes all {cube.gate.size} bound Gates · '
                f'Format source: {source.name if source else "Retained format (source removed)"}')
            title = session.source_path.name if session.source_path else 'Embedded data'
            display_title = '\u200b'.join(title[start:start + 24] for start in range(0, len(title), 24))
            scope = f'All {cube.gate.size} Gates' if self.workbench_scope.currentIndex() else f'Current Gate {index+1}/{cube.gate.size}'
            input_name = 'Original' if document.source_mode == 'raw' else 'Processed'
            self.analysis_context_label.setText(f'{display_title}\n{input_name} input · {scope}')
            self.analysis_context_label.setToolTip(details + '\n' + str(session.source_path or 'Embedded data'))
            self._workbench_update_input_context()
            return
        if session is None or session.current_cube is None:
            self.analysis_context_label.setText('Analysis — no DAT selected. Return to Workspace to open data.')
            return
        cube, graph = session.current_cube, session.active_graph
        applied = 'Energy 2nd derivative' if session.applied.mode == 'energy_second_derivative' else 'Original'
        index = int(np.argmin(np.abs(cube.gate - self.current_gate)))
        title = session.source_path.name if session.source_path else 'Embedded data'
        # Plain-text labels wrap at these invisible opportunities, including
        # filenames with no spaces. The tooltip retains the exact full path.
        display_title = '\u200b'.join(title[start:start + 24] for start in range(0, len(title), 24))
        text = (f'Analysis — {display_title}\nAnalysis input: {applied} · Gate: {cube.gate[index]:g} '
                f'({index + 1}/{cube.gate.size}) · Batch: all {cube.gate.size} Gates')
        displayed = 'Original' if graph.source_mode == 'raw' else applied
        count = cube.gate.size if graph.gate_indices is None else len(graph.gate_indices)
        text += f'\nGraph display: {displayed} · {count} of {cube.gate.size} Gates · {graph.name}'
        if displayed != applied or count != cube.gate.size:
            text += ('\nThe live heatmap/spectrum use the graph display; analysis uses the input above, '
                     'and batch analysis includes every Gate.')
        self.analysis_context_label.setText(text)
        self.analysis_context_label.setToolTip(str(session.source_path or title))

    def _selected_workbook_ids(self):
        selected = {item.data(0, Qt.UserRole) for item in self.data_navigator.selectedItems()}
        return [key for key in self.workspace.workbooks if ('workbook', key) in selected]

    def merge_workbooks(self, workbook_ids, destination_id):
        self._require_project_idle()
        selected = set(workbook_ids)
        if len(selected) < 2 or destination_id not in selected or not selected.issubset(self.workspace.workbooks):
            raise ValueError('Select at least two existing Workbooks and a selected destination.')
        validate_workbook_state(self.workspace)
        # Validate every open comparison draft before changing ownership. Rendering
        # alone cannot detect an invalid editor that has not reached the model.
        for key in self.graph_documents.keys():
            if len(key) == 3 and key[0] == 'xy':
                page = self.graph_documents.page_for(key)
                page.validate_editor()
        self._finish_current_document()
        destination = merge_workbooks(self.workspace, workbook_ids, destination_id)
        self._refresh_auxiliary_documents()
        self._auxiliary_changed()
        return destination

    def _on_merge_workbooks(self):
        identities = self._selected_workbook_ids()
        if len(identities) < 2:
            return
        display = workbook_display_labels(self.workspace)
        labels = [f'{index + 1}. {display[key]}' for index, key in enumerate(identities)]
        selected, accepted = QInputDialog.getItem(self, 'Move sheets and merge', 'Destination Workbook:', labels, 0, False)
        if accepted:
            try:
                self._require_project_idle()
                if not set(identities).issubset(self.workspace.workbooks):
                    raise ValueError('A selected Workbook is no longer in the project.')
                destination = identities[labels.index(selected)]
                count = sum(len(self.workspace.workbooks[key].sheet_ids) for key in identities)
                summary = (f'{len(identities)} Workbooks / {count} Sheets → {display[destination]}\n\n'
                    + '\n'.join(display[key] for key in identities)
                    + f'\n\nMove all Sheets into the destination and remove {len(identities)-1} empty Workbook containers. '
                    'Sheet values, identities, comparison curves and source files are preserved. '
                    'This does not concatenate numeric rows and cannot be undone.')
                if QMessageBox.warning(self, 'Move sheets and merge', summary,
                        QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                    return
                self.merge_workbooks(identities, destination)
            except ValueError as exc:
                self._show_error(str(exc))

    def _on_save_sheet_snapshot(self, sheet_id):
        try:
            self._require_project_idle()
            if sheet_shape(self.workspace, sheet_id)[0] == 0:
                raise ValueError('There are no current results to snapshot.')
            name, accepted = QInputDialog.getText(self, 'Save snapshot', 'Snapshot name:')
            if not accepted:
                return
            self._require_project_idle()
            self._finish_current_document()
            snapshot = save_sheet_snapshot(self.workspace, sheet_id, name)
            self._refresh_auxiliary_documents()
            self._auxiliary_changed()
            self.open_workbook(self._sheet_owner(snapshot.sheet_id).workbook_id, snapshot.sheet_id)
        except (ValueError, KeyError) as exc:
            self._show_error(str(exc))

    def _open_navigator_item(self, *, toggle_group=True):
        item = self.data_navigator.currentItem()
        if item is None:
            return
        kind, identity = item.data(0, Qt.UserRole)
        if kind == 'group':
            if toggle_group:
                item.setExpanded(not item.isExpanded())
            return
        try:
            if kind == 'sheet':
                self.open_workbook(self._sheet_owner(identity).workbook_id, identity)
            elif kind == 'workbook':
                self.open_workbook(identity)
            elif kind == 'xy':
                self.open_xy_comparison(identity)
            elif kind == 'heatmap':
                self.open_graph_document(*identity)
        except ValueError as exc:
            self._show_error(str(exc))

    def _rename_navigator_item(self):
        selected = self.data_navigator.selectedItems()
        if len(selected) != 1:
            return
        item = selected[0]
        kind, identity = item.data(0, Qt.UserRole)
        if kind not in ('sheet', 'workbook', 'xy'):
            return
        try:
            self._require_project_idle()
            self._finish_current_document()
            record = {'sheet': self.workspace.sheets, 'workbook': self.workspace.workbooks,
                      'xy': self.workspace.comparisons}[kind][identity]
            name, ok = QInputDialog.getText(self, 'Rename', 'Name:', text=record.name)
            if ok and name.strip() and '\x00' not in name:
                record.name = name.strip()
                if kind == 'xy':
                    page = self.graph_documents.page_for(('xy', identity, ''))
                    if page is not None:
                        with QSignalBlocker(page.name_edit):
                            page.name_edit.setText(record.name)
                else:
                    self._refresh_auxiliary_documents()
                self._auxiliary_changed()
        except ValueError as exc:
            self._show_error(str(exc))

    def _remove_navigator_item(self):
        selected = self.data_navigator.selectedItems()
        if len(selected) != 1:
            return
        item = selected[0]
        kind, identity = item.data(0, Qt.UserRole)
        target_name = item.text(0)
        if kind not in ('sheet', 'workbook', 'xy'):
            return
        try:
            self._require_project_idle()
            self._finish_current_document()
            reason = self._navigator_remove_reason((kind, identity))
            if reason:
                raise ValueError(reason)
            sheet = self.workspace.sheets[identity] if kind == 'sheet' else None
            if kind == 'workbook':
                sheet = next((self.workspace.sheets[key] for key in self.workspace.workbooks[identity].sheet_ids
                              if self.workspace.sheets[key].kind == 'dat'), None)
            if sheet is not None and sheet.kind == 'dat':
                self.remove_dataset(sheet.dataset_id)
                return
            if QMessageBox.warning(self, 'Remove from project',
                    f'Remove {target_name}? Removing a Sheet or workbook also removes its curves from comparisons. Source files remain unchanged.',
                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                return
            self._require_project_idle()
            {'sheet': remove_sheet, 'workbook': remove_workbook, 'xy': remove_comparison}[kind](self.workspace, identity)
            self._refresh_auxiliary_documents()
            self._auxiliary_changed()
        except ValueError as exc:
            self._show_error(str(exc))

    def _navigator_remove_reason(self, key):
        if not key:
            return ''
        kind, identity = key
        if kind == 'sheet':
            sheet = self.workspace.sheets.get(identity)
            if sheet is None:
                return 'This Sheet is no longer in the project.'
            if sheet.kind == 'dat' and sheet.role != 'original':
                return 'This is a live DAT projection. Select its Original Sheet to remove the source and dependent results.'
        elif kind == 'workbook':
            if identity not in self.workspace.workbooks:
                return 'This Workbook is no longer in the project.'
            sheets = [self.workspace.sheets[sid] for sid in self.workspace.workbooks[identity].sheet_ids]
            dat = [sheet for sheet in sheets if sheet.kind == 'dat']
            if dat and (len(dat) != len(sheets) or len({sheet.dataset_id for sheet in dat}) != 1
                        or not any(sheet.role == 'original' for sheet in dat)):
                return 'This Workbook contains multiple sources or projections. Select an Original DAT Sheet to remove one source.'
        return ''

    def _selected_reload_sheets(self):
        if not hasattr(self, 'data_navigator'):
            return []
        selected = self.data_navigator.selectedItems()
        if len(selected) != 1:
            return []
        kind, identity = selected[0].data(0, Qt.UserRole)
        if kind == 'workbook':
            if identity not in self.workspace.workbooks:
                return []
            sheets = [self.workspace.sheets[sid] for sid in self.workspace.workbooks[identity].sheet_ids]
        elif kind == 'sheet':
            if identity not in self.workspace.sheets:
                return []
            sheets = [self.workspace.sheets[identity]]
        else:
            return []
        sources = {}
        for sheet in sheets:
            if sheet.role == 'original' and sheet.source_path is not None:
                sources.setdefault(_source_key(sheet.source_path), sheet)
        return list(sources.values())

    def _selected_reload_paths(self):
        return [sheet.source_path for sheet in self._selected_reload_sheets()]

    def _on_reload_source_copy(self):
        sheets = self._selected_reload_sheets()
        if sheets:
            choices = {}
            for sheet in sheets:
                session = self.workspace.datasets.get(sheet.dataset_id)
                override = session.base_cube.x_override if session is not None else None
                choices[_source_key(sheet.source_path)] = (
                    sheet.kind, sheet.x_column, tuple(sheet.y_columns), override)
            self._import_data_files([sheet.source_path for sheet in sheets], new_copy=True,
                                    reload_choices=choices)

    def _prepare_xy_import(self, paths):
        """Read once and preview the actual retained values for duplicate sources."""
        existing = {_source_key(sheet.source_path): sheet for sheet in reversed(tuple(self.workspace.sheets.values()))
                    if sheet.kind == 'xy' and sheet.source_path is not None}
        tables, mappings, reused = [], [], []
        for path in paths:
            sheet = existing.get(_source_key(path))
            if sheet is not None:
                # Header origin is not persisted for retained Sheets. Treat their
                # labels as names; never guess that a literal Column N is generated.
                table = XYTable(sheet.data, list(sheet.column_names), sheet.source_path)
            else:
                try:
                    table = load_xy(path)
                except (ValueError, OSError) as exc:
                    raise ValueError(f'{Path(path).name}: {exc}') from exc
            tables.append(table)
            mappings.append((sheet.x_column, [y for y in sheet.y_columns if y != sheet.x_column]) if sheet else (0, list(range(1, table.data.shape[1]))))
            reused.append(sheet is not None)
        if not tables:
            raise ValueError('Choose at least one numeric table.')
        return tables, mappings, reused

    def _retained_data_import(self, path):
        """Resolve ownership before touching a source which may have disappeared."""
        sheet = next((sheet for sheet in self.workspace.sheets.values()
                      if sheet.role == 'original' and sheet.source_path is not None
                      and _source_key(sheet.source_path) == _source_key(path)), None)
        if sheet is None:
            if self.workspace.dataset_for_path(path) is not None:
                raise ValueError('The retained matrix no longer has an Original Sheet.')
            return None
        if not any(sheet.sheet_id in book.sheet_ids for book in self.workspace.workbooks.values()):
            raise ValueError('The retained source Sheet no longer has a Workbook.')
        table = XYTable(sheet_values(self.workspace, sheet), sheet_columns(self.workspace, sheet), path)
        return PreparedDataFile(path, sheet.kind, 'Retained source: original type, values and Workbook are reused.',
            {sheet.kind: table}, sheet_id=sheet.sheet_id,
            mapping=(0 if sheet.kind == 'dat' else sheet.x_column,
                     [y for y in sheet.y_columns if y != (0 if sheet.kind == 'dat' else sheet.x_column)]))

    def _import_data_files(self, paths, comparison_id=None, *, review=False, new_copy=False,
                           reload_choices=None):
        """Single asynchronous entry for menus, numeric drops and graph imports."""
        if self._dat_import_pending or self._close_pending:
            return
        try:
            self._require_project_idle()
            ordered, seen = [], set()
            for value in paths:
                key = _source_key(value)
                if key not in seen:
                    ordered.append(Path(value).absolute())
                    seen.add(key)
            if not ordered:
                return
            if any(path.suffix.lower() not in DATA_SUFFIXES for path in ordered):
                raise ValueError('Choose numeric data files; open native projects separately.')
            self._preflight_comparison_append(comparison_id)
            retained = {} if new_copy else {str(path): entry for path in ordered
                        if (entry := self._retained_data_import(path)) is not None}
            defaults = self.snapshot_dataset_defaults()
            choices = dict(reload_choices or {})
            requests = tuple(LoadRequest(path, choices[_source_key(path)][3] if _source_key(path) in choices
                                        else self._selected_x_axis_override(), self._available_colormaps(),
                                        recall=_source_key(path) not in choices)
                             for path in ordered if str(path) not in retained)
        except (ValueError, OSError) as exc:
            self._show_error(f'Cannot import data: {exc}')
            return
        self._dat_import_token += 1
        token, workspace = self._dat_import_token, self.workspace
        self._pending_data_review = None
        self._job_cancel_requested = False
        self._set_dat_import_pending(True)
        self._job_cancel_button.show()
        self._background_job_owner = None
        self._processing_request = self._analysis_request = None

        def received(result):
            if self._dat_import_is_current(token) and workspace is self.workspace:
                prepared, errors = result
                for entry in prepared:
                    choice = choices.get(_source_key(entry.path))
                    if choice is not None:
                        entry.mapping = (choice[1], list(choice[2]))
                        try:
                            validate_import_mapping(entry.table, entry.mapping)
                        except ValueError as exc:
                            errors.append(DatImportError(entry.path,
                                f'The retained {choice[0].upper()} column mapping cannot be used: {exc} '
                                'Review the source columns or adjust the Original Sheet mapping, then reload again.'))
                by_path = {str(entry.path): entry for entry in prepared}
                by_path.update(retained)
                entries = [by_path[str(path)] for path in ordered if str(path) in by_path]
                self._pending_data_review = (token, workspace, entries, errors, comparison_id,
                                             review or comparison_id is not None, new_copy)

        self._background_jobs.start(lambda context: prepare_data_batch(requests, defaults, context,
            preferred_kinds={str(path): choices[_source_key(path)][0] for path in ordered
                             if _source_key(path) in choices}), received,
            on_error=lambda exc: self._dat_import_failed(token, exc),
            on_progress=lambda message: self._dat_import_progress(token, message))

    def _review_data_import(self, token, workspace, entries, errors, comparison_id, review=False, new_copy=False):
        """Deliver only after the worker is idle; preview is an explicit request."""
        if not self._dat_import_is_current(token) or workspace is not self.workspace:
            return
        self._set_dat_import_pending(False)
        self._job_cancel_button.hide()
        failures = '\n'.join(f'{error.path}: {error.reason}' for error in errors)
        if new_copy and failures:
            message = 'Cannot reload as new copy. No sources were added:\n' + failures
            self.dat_import_status_label.setText(message)
            self.statusBar().showMessage(message)
            self._show_error(message)
            return
        if not entries:
            self.dat_import_status_label.setText('No data imported.\n' + failures)
            self.statusBar().showMessage('No data imported. See the file errors for details.')
            self._show_error('No data imported:\n' + failures)
            return
        dialog = None
        try:
            if review or comparison_id is not None:
                dialog = XYImportDialog([entry.table for entry in entries], self.workspace.workbooks, self,
                    entries=entries, mappings=[(entry.mapping[0], list(entry.mapping[1])) for entry in entries],
                    reused=[entry.sheet_id is not None for entry in entries],
                    comparisons=self.workspace.comparisons, comparison_id=comparison_id)
                if failures:
                    dialog.feedback.setText('These files could not be read; the valid files are ready to review:\n' + failures)
                if dialog.exec() != QDialog.Accepted:
                    message = 'Data import cancelled. No sources were added.'
                    self.dat_import_status_label.setText(message)
                    self.statusBar().showMessage(message)
                    return
                if token != self._dat_import_token or workspace is not self.workspace or self._close_pending:
                    return
                destination = dialog.destination_combo.currentData()
                self.import_data_files(entries, None if destination == 'per_file' else destination,
                    mappings=dialog.mappings, compare=dialog.compare, workbook_name=dialog.name_edit.text(),
                    grouping='per_file' if destination == 'per_file' else 'single',
                    comparison_id=(comparison_id if comparison_id is not None else
                                   dialog.comparison_combo.currentData()) if dialog.compare else None,
                    include_existing=dialog.include_existing_chk.isChecked(), new_copy=new_copy)
            else:
                self.import_data_files(entries, new_copy=new_copy)
            notices = [entry.session.initialization_notice for entry in entries
                       if entry.kind == 'dat' and entry.session is not None and entry.session.initialization_notice]
            if failures:
                self.statusBar().showMessage(self.statusBar().currentMessage()
                    + f' {len(errors)} file(s) failed; see the file errors for details.')
            self.dat_import_status_label.setText(self.statusBar().currentMessage()
                + ('\n' + failures if failures else '') + ('\n' + '\n'.join(notices) if notices else ''))
            if failures and dialog is None:
                self._show_error('Valid data imported into Workspace. These files could not be imported:\n' + failures)
        except (ValueError, OSError, KeyError) as exc:
            message = f'Cannot import data: {exc}'
            self.dat_import_status_label.setText(message)
            self.statusBar().showMessage(message)
            self._show_error(message)
        finally:
            if dialog is not None:
                dialog.deleteLater()

    def import_data_files(self, entries, workbook_id=None, *, mappings=None, compare=False,
                          workbook_name=None, grouping='per_file', comparison_id=None, include_existing=False,
                          new_copy=False):
        """Validate/stage the entire mixed batch, then publish into the live Workspace."""
        self._require_project_idle()
        compare = compare or comparison_id is not None
        if not entries:
            raise ValueError('Choose at least one data file.')
        if grouping not in ('per_file', 'single'):
            raise ValueError('Choose per_file or single Workbook grouping.')
        if workbook_id is not None and workbook_id not in self.workspace.workbooks:
            raise ValueError('Select an existing workbook.')
        if workbook_name is not None and (not workbook_name.strip() or '\x00' in workbook_name):
            raise ValueError('Enter a valid new workbook name.')
        mappings = ([(entry.mapping[0], list(entry.mapping[1])) for entry in entries]
                    if mappings is None else mappings)
        if len(mappings) != len(entries):
            raise ValueError('Provide one X/Y mapping for every file.')
        target_page = self._preflight_comparison_append(comparison_id)
        staged = replace(self.workspace, datasets=dict(self.workspace.datasets), path_index=dict(self.workspace.path_index),
            workbooks={key: replace(book, sheet_ids=list(book.sheet_ids)) for key, book in self.workspace.workbooks.items()},
            sheets={key: replace(sheet, y_columns=list(sheet.y_columns)) for key, sheet in self.workspace.sheets.items()})
        shared = staged.workbooks.get(workbook_id)
        sheets, seen, new_sessions = [], {}, []
        new_sources = reused_sources = 0
        for entry, mapping in zip(entries, mappings):
            validate_import_mapping(entry.table, mapping, require_y=compare)
            if entry.kind == 'dat' and mapping[0] != 0:
                raise ValueError('Gate matrices use the interpreted spectral axis as X.')
            key = _source_key(entry.path)
            if key in seen:
                sheet, previous = seen[key]
                if previous != mapping or sheet.kind != entry.kind:
                    raise ValueError('The same source has conflicting types or mappings in this batch.')
            else:
                retained = None if new_copy else self._retained_data_import(entry.path)
                if entry.sheet_id is not None and (retained is None or retained.sheet_id != entry.sheet_id):
                    raise ValueError('The retained source or its Workbook no longer exists.')
                if retained is not None:
                    if retained.kind != entry.kind:
                        raise ValueError('An imported source keeps its original data type.')
                    sheet = staged.sheets[retained.sheet_id]
                    reused_sources += 1
                else:
                    new_sources += 1
                    if shared is None and grouping == 'single':
                        shared = create_workbook(staged, workbook_name or 'Workbook ' + str(len(staged.workbooks) + 1))
                    if entry.kind == 'dat':
                        if entry.session is None:
                            raise ValueError('The matrix was not prepared.')
                        session = staged.register(entry.session, as_new_copy=new_copy)
                        if new_copy:
                            # Copies share provenance, so only the original owner
                            # may write the source's companion plot settings.
                            session.sidecar_writes_enabled = False
                        new_sessions.append(session)
                        sheet = ensure_dataset_sheet(staged, session.dataset_id)
                        owner = next(book for book in staged.workbooks.values() if sheet.sheet_id in book.sheet_ids)
                        if shared is not None:
                            shared.sheet_ids.extend(owner.sheet_ids)
                            del staged.workbooks[owner.workbook_id]
                    elif entry.kind == 'xy':
                        owner = shared or create_workbook(staged, entry.path.name)
                        sheet = register_xy_sheet(staged, owner.workbook_id, 'Original',
                            entry.table.data, entry.table.column_names, source_path=entry.path,
                            allow_duplicate=new_copy)
                    else:
                        raise ValueError('Choose XY table or Gate matrix.')
                seen[key] = sheet, mapping
            if not any(sheet.sheet_id in book.sheet_ids for book in staged.workbooks.values()):
                raise ValueError('The source Sheet no longer has a Workbook.')
            set_sheet_mapping(staged, sheet.sheet_id, *mapping)
            sheets.append(sheet)
        candidates = [(sheet.sheet_id, x, y, sheet.mode)
                      for sheet, (x, ys) in zip(sheets, mappings) for y in ys]
        additions, skipped = (select_curve_additions(staged, comparison_id, candidates, include_existing=include_existing)
                              if compare else ([], 0))
        if compare and not additions:
            comparison = self.workspace.comparisons[comparison_id]
            self.open_xy_comparison(comparison_id)
            self._report_curve_additions(comparison, 0, skipped)
            self.statusBar().showMessage(self.statusBar().currentMessage() + ' '
                + self._import_source_summary(new_sources, reused_sources, new_copy=new_copy))
            return self._sheet_owner(sheets[0].sheet_id)
        # Build curves before publishing so an invalid source cannot leave a partial import.
        staged.comparisons = dict(self.workspace.comparisons)
        if compare:
            comparison = (create_comparison(staged, 'Comparison ' + str(len(staged.comparisons) + 1))
                          if comparison_id is None else replace(staged.comparisons[comparison_id],
                                                               curves=dict(staged.comparisons[comparison_id].curves)))
            staged.comparisons[comparison.comparison_id] = comparison
            for sheet_id, x, y, mode in additions:
                add_curve(staged, comparison.comparison_id, sheet_id, x, y, mode=mode)
        self._finish_comparison_append(target_page)
        self.workspace.datasets.update(staged.datasets)
        self.workspace.path_index.update(staged.path_index)
        for identity, owner in staged.workbooks.items():
            if identity in self.workspace.workbooks:
                self.workspace.workbooks[identity].sheet_ids[:] = owner.sheet_ids
            else:
                self.workspace.workbooks[identity] = owner
        for identity, sheet in staged.sheets.items():
            if identity in self.workspace.sheets:
                original = self.workspace.sheets[identity]
                original.x_column, original.y_columns = sheet.x_column, list(sheet.y_columns)
            else:
                self.workspace.sheets[identity] = sheet
        for session in new_sessions:
            if self._project_path is not None:
                session.sidecar_writes_enabled = False
            if session.applied.mode == 'energy_second_derivative':
                self._applied_derivative_sources[session.dataset_id] = (id(session.base_cube), id(session.current_cube))
        book = self._sheet_owner(sheets[0].sheet_id)
        self.current_folder = str(entries[0].path.parent)
        self._refresh_imported_datasets()
        self._refresh_auxiliary_documents()
        if compare:
            if comparison_id is None:
                self.workspace.comparisons[comparison.comparison_id] = comparison
            else:
                comparison = self.workspace.comparisons[comparison_id]
                comparison.curves.update(staged.comparisons[comparison_id].curves)
            if target_page is not None:
                target_page.refresh()
            self.open_xy_comparison(comparison.comparison_id)
            self._report_curve_additions(comparison, len(additions), skipped)
            self.statusBar().showMessage(self.statusBar().currentMessage() + ' '
                + self._import_source_summary(new_sources, reused_sources, new_copy=new_copy))
        else:
            # Other imported owners remain available in Project without opening more windows.
            self.open_workbook(book.workbook_id, sheets[0].sheet_id)
            self.statusBar().showMessage(self._import_source_summary(new_sources, reused_sources, new_copy=new_copy))
        self._auxiliary_changed()
        return book

    @staticmethod
    def _import_source_summary(new, reused, *, new_copy=False):
        message = f'Import complete: {new} new, {reused} reused data source(s).'
        if reused:
            message += ' Reused retained values and Workbooks; source files were not reread. Use Reload as new copy to read disk changes.'
        elif new_copy:
            message += ' Reloaded from disk into new Workbooks; existing data and graphs are unchanged.'
        return message

    def import_xy_files(self, paths, workbook_id=None, *, tables=None, mappings=None,
                        compare=False, workbook_name=None, grouping='per_file',
                        comparison_id=None, include_existing=False):
        """Prevalidate import; return the first affected owner, including reused sources."""
        self._require_project_idle()
        compare = compare or comparison_id is not None
        if comparison_id is not None and comparison_id not in self.workspace.comparisons:
            raise ValueError('The destination comparison no longer exists.')
        if grouping not in ('per_file', 'single'):
            raise ValueError('Choose per_file or single Workbook grouping.')
        if workbook_id is not None and workbook_id not in self.workspace.workbooks:
            raise ValueError('Select an existing workbook.')
        tables = self._prepare_xy_import(paths)[0] if tables is None else tables
        if not tables:
            raise ValueError('Choose at least one numeric table.')
        if mappings is not None and len(mappings) != len(tables):
            raise ValueError('Provide one X/Y mapping for every file.')
        if workbook_id is None and workbook_name is not None and (not workbook_name.strip() or '\x00' in workbook_name):
            raise ValueError('Enter a valid new workbook name.')
        existing = {_source_key(sheet.source_path): sheet for sheet in reversed(tuple(self.workspace.sheets.values()))
                    if sheet.kind == 'xy' and sheet.source_path is not None}
        reused_keys = set(existing)
        keys = {_source_key(table.source_path) for table in tables}
        reused = len(keys & reused_keys)
        validated = []
        seen = {}
        for index, table in enumerate(tables):
            sheet = existing.get(_source_key(table.source_path))
            effective = XYTable(sheet.data, sheet.column_names, sheet.source_path) if sheet else table
            mapping = mappings[index] if mappings is not None else (sheet.x_column, list(sheet.y_columns)) if sheet else (0, list(range(1, table.data.shape[1])))
            try:
                validate_import_mapping(effective, mapping, require_y=compare)
                key = _source_key(table.source_path)
                if key in seen and seen[key] != mapping:
                    raise ValueError('The same source has conflicting mappings in this batch.')
                seen[key] = mapping
            except ValueError as exc:
                raise ValueError(f'{table.source_path.name}: {exc}') from exc
            validated.append(mapping)
        # Stage ownership and new sources without copying their numeric arrays.
        # Even a malformed supplied table must fail before any project mutation.
        staged = replace(self.workspace,
            workbooks={key: replace(book, sheet_ids=list(book.sheet_ids)) for key, book in self.workspace.workbooks.items()},
            sheets=dict(self.workspace.sheets))
        shared = staged.workbooks.get(workbook_id)
        sheets = []
        for table in tables:
            key = _source_key(table.source_path)
            sheet = existing.get(key)
            if sheet is None:
                if shared is None and grouping == 'single':
                    shared = create_workbook(staged, workbook_name or 'Workbook ' + str(len(staged.workbooks) + 1))
                owner = shared or create_workbook(staged, table.source_path.name)
                sheet = register_xy_sheet(staged, owner.workbook_id, 'Original',
                    table.data, table.column_names, source_path=table.source_path)
                existing[key] = sheet
            sheets.append(sheet)
        for sheet in sheets:
            if not any(sheet.sheet_id in owner.sheet_ids for owner in staged.workbooks.values()):
                raise ValueError('The retained source Sheet no longer has a workbook.')
        target_page = self._preflight_comparison_append(comparison_id)
        candidates = [(sheet.sheet_id, x, y, sheet.mode)
                      for sheet, (x, ys) in zip(sheets, validated) for y in ys]
        additions, skipped = select_curve_additions(staged, comparison_id, candidates,
                                                    include_existing=include_existing) if compare else ([], 0)
        if compare and not additions:
            comparison = self.workspace.comparisons[comparison_id]
            self._report_curve_additions(comparison, 0, skipped)
            self.statusBar().showMessage(self.statusBar().currentMessage() + ' '
                + self._import_source_summary(len(keys)-reused, reused))
            return self._sheet_owner(sheets[0].sheet_id)
        self._finish_comparison_append(target_page)
        for identity, owner in staged.workbooks.items():
            if identity in self.workspace.workbooks:
                self.workspace.workbooks[identity].sheet_ids[:] = owner.sheet_ids
            else:
                self.workspace.workbooks[identity] = owner
        self.workspace.sheets.update(staged.sheets)
        for sheet, (x, ys) in zip(sheets, validated):
            set_sheet_mapping(self.workspace, sheet.sheet_id, x, ys)
        book = self._sheet_owner(sheets[0].sheet_id)
        self._refresh_auxiliary_documents()
        self._auxiliary_changed()
        if compare:
            comparison = (create_comparison(self.workspace, 'Comparison ' + str(len(self.workspace.comparisons) + 1))
                          if comparison_id is None else self.workspace.comparisons[comparison_id])
            for sheet_id, x, y, mode in additions:
                add_curve(self.workspace, comparison.comparison_id, sheet_id, x, y, mode=mode)
            if target_page is not None:
                target_page.refresh()
            self._auxiliary_changed()
            self.open_xy_comparison(comparison.comparison_id)
            self._report_curve_additions(comparison, len(additions), skipped)
            self.statusBar().showMessage(self.statusBar().currentMessage() + ' '
                + self._import_source_summary(len(keys)-reused, reused))
        else:
            self.open_workbook(book.workbook_id, sheets[0].sheet_id)
            self.statusBar().showMessage(self._import_source_summary(len(keys)-reused, reused))
        return book

    def _on_import_xy(self, comparison_id=None):
        # Compatibility hook used by comparison pages and existing integrations.
        self._on_import_data(comparison_id, review=True)

    def _on_import_data(self, comparison_id=None, *, review=False):
        if comparison_id is False:
            comparison_id = None
        if self._project_busy() or self._close_pending:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, 'Import with options' if review else 'Import data', self.current_folder, DATA_FILTER)
        if paths:
            self._import_data_files(paths, comparison_id, review=review)

    def _auxiliary_active(self):
        key = self.graph_documents._active_key
        return bool(self.plot_tabs.currentWidget() is self.graph_documents and key and len(key) == 3)

    def _finish_auxiliary_editor(self, key):
        page = self.graph_documents.page_for(key)
        if isinstance(page, XYPlotPage):
            page.finish_editor()

    def _finish_current_document(self):
        if self._auxiliary_active():
            self._finish_auxiliary_editor(self.graph_documents._active_key)
        elif self.workspace.active is not None:
            self._finish_graph_editors()

    def _create_auxiliary_page(self, key):
        if key[0] == 'workbook':
            page = WorkbookPage(self.workspace, key[1])
            page.snapshotRequested.connect(self._on_save_sheet_snapshot)
            page.comparisonRequested.connect(self._add_sheet_comparison)
            page.quickPlotRequested.connect(self._quick_sheet_plot)
            page.targetSelected.connect(self._select_comparison_target)
            page.sheetActivated.connect(self._refresh_graph_command_host)
            page.sheetActivated.connect(self._refresh_navigator_active)
            page.contextChanged.connect(self._refresh_workspace_shell)
            page.gateRequested.connect(self._sheet_gate_requested)
            page.refresh_targets(self._last_comparison_id)
        else:
            page = XYPlotPage(self.workspace, key[1])
            page.canvas.addAction(self._graph_command_actions['rescale'])
            for name in ('undo', 'redo'):
                page.canvas.addAction(self._graph_command_actions[name])
                page.curve_list.addAction(self._graph_command_actions[name])
            page.historyChanged.connect(self._refresh_graph_commands)
            page.analysis_panel.host = self
            page.analysisRequested.connect(lambda owner=key[1]: self._open_analysis(('xy', owner)))
            page.command_bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self._graph_command_stack.addWidget(page.command_bar)
            page._inspector_hosted = True
            page.inspector.setMaximumWidth(16777215)
            self._properties_stack.addWidget(page.inspector)
            page.inspectorVisibilityRequested.connect(self._set_dat_details_requested)
            self._watch_dat_editor_focus(page.inspector)
            page.sourceRequested.connect(lambda identity: self.open_workbook(self._sheet_owner(identity).workbook_id, identity))
            page.addDataRequested.connect(lambda owner=key[1]: self._show_sheet_comparison_dialog(comparison_id=owner))
            page.importDataRequested.connect(lambda owner=key[1]: self._on_import_xy(comparison_id=owner))
        page.changed.connect(self._auxiliary_changed)
        return page

    def _open_auxiliary_document(self, key):
        self._require_project_idle()
        if not document_exists(self.workspace, key):
            raise ValueError('The document no longer exists.')
        if key != self.graph_documents._active_key or self.plot_tabs.currentWidget() is not self.graph_documents:
            self._finish_current_document()
        page = self.graph_documents.page_for(key)
        if page is None:
            page = self._create_auxiliary_page(key)
            self.graph_documents.cache_page(key, page)
        self.graph_documents.show_document(key, self._document_title(key))
        if key[0] == 'xy' and not self._comparison_target_pinned:
            self._last_comparison_id = key[1]
            self._refresh_comparison_targets(self._last_comparison_id)
        self.plot_tabs.setCurrentWidget(self.graph_documents)
        self._on_plot_tab_changed(self.plot_tabs.currentIndex())
        self._refresh_graph_commands()
        return page

    def _save_xy_analysis_results(self, page, book, sheets, *, create_graph=False, graph_id=None, has_fit=True):
        self._require_project_idle()
        if page.workspace is not self.workspace or page.comparison_id not in self.workspace.comparisons:
            raise ValueError('The source graph no longer belongs to this project.')
        return self._publish_analysis_results(book, sheets, create_graph=create_graph, graph_id=graph_id, has_fit=has_fit)

    def _publish_analysis_results(self, book, sheets, *, create_graph=False, graph_id=None,
                             source_label='Source data', has_fit=True):
        """Preflight editors and build records before publishing, rolling back new records on failure."""
        self._require_project_idle()
        self._finish_current_document()
        new_book = book.workbook_id not in self.workspace.workbooks
        staged = replace(self.workspace, workbooks=dict(self.workspace.workbooks),
                         sheets=dict(self.workspace.sheets), comparisons=dict(self.workspace.comparisons))
        staged.workbooks[book.workbook_id] = book
        staged.sheets.update({sheet.sheet_id: sheet for sheet in sheets})
        comparison = staged.comparisons.get(graph_id) if create_graph else None
        new_graph = create_graph and comparison is None
        if new_graph:
            comparison = add_analysis_comparison(staged, book, sheets, source_label=source_label, has_fit=has_fit)
        try:
            if new_book:
                self.workspace.workbooks[book.workbook_id] = book
                self.workspace.sheets.update({sheet.sheet_id: sheet for sheet in sheets})
            if new_graph:
                self.workspace.comparisons[comparison.comparison_id] = comparison
            self._refresh_data_navigator()
            self._auxiliary_changed()
            if create_graph:
                self.open_xy_comparison(comparison.comparison_id)
                return comparison.comparison_id
            self.open_workbook(book.workbook_id, sheets[0].sheet_id)
        except Exception:
            if new_graph:
                self.graph_documents.discard(('xy', comparison.comparison_id, ''))
                remove_comparison(self.workspace, comparison.comparison_id)
            if new_book:
                self.graph_documents.discard(('workbook', book.workbook_id, ''))
                remove_workbook(self.workspace, book.workbook_id)
            self._refresh_data_navigator()
            self._refresh_comparison_targets()
            raise

    def _finish_xy_analysis_jobs(self, *, cancelled=False):
        for key in self.graph_documents.keys():
            page = self.graph_documents.page_for(key)
            if isinstance(page, XYPlotPage):
                page.analysis_panel.background_finished(cancelled=cancelled)

    def open_workbook(self, workbook_id, sheet_id=None):
        page = self._open_auxiliary_document(('workbook', workbook_id, ''))
        if sheet_id is not None:
            page.select_sheet(sheet_id)
        return page

    def open_xy_comparison(self, comparison_id):
        return self._open_auxiliary_document(('xy', comparison_id, ''))

    def _refresh_comparison_targets(self, preferred=None):
        if preferred is not None and not self._comparison_target_pinned:
            self._last_comparison_id = preferred
        if self._last_comparison_id not in self.workspace.comparisons:
            self._last_comparison_id = None
        with QSignalBlocker(self.workbook_commands.pin_action):
            self.workbook_commands.pin_action.setChecked(self._comparison_target_pinned)
        for key in self.graph_documents.keys():
            page = self.graph_documents.page_for(key)
            if isinstance(page, WorkbookPage):
                page.refresh_targets(self._last_comparison_id)
        self._refresh_graph_command_host()

    def _select_comparison_target(self, comparison_id):
        """Explicit destination choice also replaces the pinned destination."""
        self._last_comparison_id = comparison_id if comparison_id in self.workspace.comparisons else None
        self._refresh_comparison_targets()
        self._refresh_navigator_actions()

    def _pin_comparison_target(self, pinned):
        self._comparison_target_pinned = bool(pinned)
        self._refresh_comparison_targets()
        self._refresh_navigator_actions()

    def _show_comparison_target(self):
        if self._last_comparison_id in self.workspace.comparisons:
            try:
                self.open_xy_comparison(self._last_comparison_id)
            except ValueError as exc:
                self._show_error(str(exc))

    def _quick_sheet_plot(self, sheet_id, style, comparison_id=None):
        if style != 'heatmap':
            return self._add_sheet_comparison(sheet_id, comparison_id, style=style)
        try:
            self._require_project_idle()
            sheet = self.workspace.sheets[sheet_id]
            if sheet.kind != 'dat' or sheet.role not in ('original', 'processed'):
                raise ValueError('Heatmap requires a DAT Original or Processed Sheet with physical Gate coordinates.')
            gates = sorted({column - 1 for column in sheet.y_columns if column > 0})
            if not gates:
                raise ValueError('Select at least one Gate column for the Heatmap.')
            self._finish_current_document()
            session = self.workspace.datasets[sheet.dataset_id]
            self.activate_dataset(session.dataset_id)
            # A repeated command opens its exact source scope; existing graph visuals are retained.
            graph = next((graph for graph in session.graphs.values()
                          if graph.source_mode == sheet.mode and graph.gate_indices == gates), None)
            if graph is None:
                base_name = f'{sheet.name} Heatmap'
                name, count = base_name, 2
                while any(graph.name == name for graph in session.graphs.values()):
                    name, count = f'{base_name} {count}', count + 1
                graph = session.create_graph(name)
                graph.source_mode, graph.gate_indices = sheet.mode, gates
                cube = graph_cube(session)
                view = graph.view
                view.x_limits = (float(np.min(cube.energy)), float(np.max(cube.energy)))
                lo, hi = float(np.min(cube.gate)), float(np.max(cube.gate))
                if lo == hi:
                    lo, hi = lo - .5, hi + .5
                view.y_limits = (lo, hi)
                for field, value in zip(('xmin', 'xmax', 'ymin', 'ymax'), (*view.x_limits, *view.y_limits)):
                    view.bounds[field], view.locks[field] = value, False
                if view.signal_title_auto:
                    view.annotations['signal_title'] = graph_signal_label(session)
                refresh_graph_colors(graph, cube, unlock=True)
            graph.published = True
            session.activate_graph(graph.graph_id)
            self._project_selected_graph(session)
            self._select_graph_page(session, show=True)
            self.plot_tabs.setCurrentWidget(self.graph_documents)
            self._on_plot_tab_changed(self.plot_tabs.currentIndex())
            self._refresh_graph_commands()
            return graph
        except ValueError as exc:
            self._show_error(str(exc))

    def _add_sheet_comparison(self, sheet_id, comparison_id=None, *, style='line', include_existing=False):
        try:
            self._require_project_idle()
            sheet = self.workspace.sheets[sheet_id]
            mappings = {sheet_id: (sheet.x_column, list(sheet.y_columns), sheet.mode)}
            validate_sheet_comparison(self.workspace, mappings)
            comparison = self.compare_sheets(mappings, comparison_id, include_existing=include_existing, style=style)
            return self.graph_documents.page_for(('xy', comparison.comparison_id, ''))
        except ValueError as exc:
            self._show_error(str(exc))

    def _compare_selected_sheets(self):
        selected = self._selected_sheet_ids()
        if selected:
            self._show_sheet_comparison_dialog(selected_sheet_ids=selected)
        else:
            self.statusBar().showMessage('The selected Workbooks contain no Original Sheets. Select individual result Sheets to compare.')

    def _show_sheet_comparison_dialog(self, *, selected_sheet_ids=(), comparison_id=None):
        try:
            self._require_project_idle()
            if comparison_id is not None and comparison_id not in self.workspace.comparisons:
                raise ValueError('The destination comparison no longer exists.')
            dialog = SheetComparisonDialog(self.workspace, self, selected_sheet_ids=selected_sheet_ids,
                                           comparison_id=comparison_id)
            try:
                if dialog.exec() == QDialog.Accepted:
                    target = comparison_id if comparison_id is not None else dialog.destination_combo.currentData()
                    self.compare_sheets(dialog.selected_mappings(), target,
                                        include_existing=dialog.include_existing_chk.isChecked())
                    if target is not None:
                        self._select_comparison_target(target)
            finally:
                dialog.deleteLater()
        except ValueError as exc:
            self._show_error(str(exc))

    def _preflight_comparison_append(self, comparison_id):
        """Validate current and target graph drafts before committing either."""
        self._require_project_idle()
        if comparison_id is not None and comparison_id not in self.workspace.comparisons:
            raise ValueError('The destination comparison no longer exists.')
        target_page = self.graph_documents.page_for(('xy', comparison_id, '')) if comparison_id else None
        current_page = self.graph_documents.page_for(self.graph_documents._active_key) if self._auxiliary_active() else None
        # Check both drafts first: an invalid target must not commit a valid
        # current graph's draft, or any source defaults, before it fails.
        for page in (current_page, target_page):
            if isinstance(page, XYPlotPage):
                page.validate_editor()
        return target_page

    def _finish_comparison_append(self, target_page):
        current_page = self.graph_documents.page_for(self.graph_documents._active_key) if self._auxiliary_active() else None
        self._finish_current_document()
        if target_page is not None and target_page is not current_page:
            target_page.finish_editor()

    def _report_curve_additions(self, comparison, added, skipped):
        self.statusBar().showMessage(f'Added {added} curve(s) to {comparison.name}; skipped {skipped} existing curve(s).')

    def compare_sheets(self, mappings, comparison_id=None, *, include_existing=False, style='line'):
        """Commit one existing-Sheet batch after every source/target passes preflight."""
        self._require_project_idle()
        if style not in ('line', 'scatter', 'line_symbol'):
            raise ValueError('Unknown curve style.')
        validate_sheet_comparison(self.workspace, mappings)
        target_page = self._preflight_comparison_append(comparison_id)
        candidates = [(sheet_id, mappings[sheet_id][0], y, mappings[sheet_id][2])
                      for book in self.workspace.workbooks.values() for sheet_id in book.sheet_ids
                      if sheet_id in mappings for y in mappings[sheet_id][1]]
        additions, skipped = select_curve_additions(self.workspace, comparison_id, candidates,
                                                    include_existing=include_existing)
        if not additions:
            comparison = self.workspace.comparisons[comparison_id]
            self._report_curve_additions(comparison, 0, skipped)
            return comparison
        self._finish_comparison_append(target_page)
        comparison = (create_comparison(self.workspace, 'Comparison ' + str(len(self.workspace.comparisons) + 1))
                      if comparison_id is None else self.workspace.comparisons[comparison_id])
        changed_sheets = set()
        # Project order is deterministic, independently of tree click order.
        for book in self.workspace.workbooks.values():
            for sheet_id in book.sheet_ids:
                if sheet_id not in mappings:
                    continue
                x, ys, mode = mappings[sheet_id]
                sheet = self.workspace.sheets[sheet_id]
                if (sheet.x_column, sheet.y_columns, sheet.mode) != (x, ys, mode):
                    changed_sheets.add(sheet_id)
                    set_sheet_mapping(self.workspace, sheet_id, x, ys, mode)
        for sheet_id, x, y, mode in additions:
            add_curve(self.workspace, comparison.comparison_id, sheet_id, x, y, mode=mode, style=style)
        if not self._comparison_target_pinned:
            self._last_comparison_id = comparison.comparison_id
        if target_page is not None:
            target_page.refresh()
        self._refresh_batch_sheet_pages(changed_sheets)
        self._auxiliary_changed()
        self._report_curve_additions(comparison, len(additions), skipped)
        if comparison_id is None:
            self.open_xy_comparison(comparison.comparison_id)
        # Existing targets keep the source document active and closed targets
        # closed. A graph-origin action therefore also stays on its own graph.
        return comparison

    def _refresh_batch_sheet_pages(self, sheet_ids):
        for key in self.graph_documents.keys():
            workbook = self.graph_documents.page_for(key)
            if not isinstance(workbook, WorkbookPage):
                continue
            for sheet_id in sheet_ids.intersection(workbook.sheet_pages):
                page = workbook.sheet_pages[sheet_id]
                selection_model = page.table.selectionModel()
                ranges = [(r.top(), r.left(), r.bottom(), r.right()) for r in selection_model.selection()]
                index = selection_model.currentIndex()
                current = (index.row(), index.column())
                scroll = (page.table.horizontalScrollBar().value(), page.table.verticalScrollBar().value())
                # Restoring a DAT current column must not request dataset/Gate
                # activation through SheetPage.currentColumnChanged.
                with QSignalBlocker(selection_model):
                    page.refresh()
                    selection = QItemSelection()
                    for top, left, bottom, right in ranges:
                        if top < page.model.rowCount() and left < page.model.columnCount():
                            selection.select(page.model.index(top, left), page.model.index(
                                min(bottom, page.model.rowCount() - 1), min(right, page.model.columnCount() - 1)))
                    selection_model.select(selection, QItemSelectionModel.ClearAndSelect)
                    selection_model.setCurrentIndex(page.model.index(*current), QItemSelectionModel.NoUpdate)
                    page.table.horizontalScrollBar().setValue(scroll[0])
                    page.table.verticalScrollBar().setValue(scroll[1])

    def _new_xy_comparison(self):
        try:
            self._require_project_idle()
            self._finish_current_document()
            comparison = create_comparison(self.workspace, 'Comparison ' + str(len(self.workspace.comparisons) + 1))
            self._auxiliary_changed()
            self.open_xy_comparison(comparison.comparison_id)
        except ValueError as exc:
            self._show_error(str(exc))

    def _add_current_gate_comparison(self, *, include_existing=False):
        try:
            self._require_project_idle()
            if self.workspace.active is None:
                raise ValueError('Choose an imported DAT first.')
            session = self.workspace.active
            sheet = ensure_dataset_sheet(self.workspace, session.dataset_id,
                role='original' if session.applied.mode == 'original' else 'processed')
            comparison_id = self._last_comparison_id
            if comparison_id not in self.workspace.comparisons:
                if self._comparison_target_pinned:
                    raise ValueError('Choose a comparison destination before adding this Gate.')
                comparison_id = None
            target_page = self._preflight_comparison_append(comparison_id)
            y = self._current_gate_index() + 1
            validate_mapping(self.workspace, sheet, 0, [y], sheet.mode)
            candidates = [(sheet.sheet_id, 0, y, sheet.mode)]
            additions, skipped = select_curve_additions(self.workspace, comparison_id, candidates,
                include_existing=include_existing)
            if not additions:
                self._report_curve_additions(self.workspace.comparisons[comparison_id], 0, skipped)
                return
            self._finish_comparison_append(target_page)
            creating = comparison_id is None
            if comparison_id is None:
                comparison_id = create_comparison(self.workspace, 'Comparison ' + str(len(self.workspace.comparisons) + 1)).comparison_id
            add_curve(self.workspace, comparison_id, sheet.sheet_id, 0, y, mode=sheet.mode)
            if not self._comparison_target_pinned:
                self._last_comparison_id = comparison_id
            page = self.graph_documents.page_for(('xy', comparison_id, ''))
            if page:
                page.refresh()
            self._auxiliary_changed()
            if creating:
                self.open_xy_comparison(comparison_id)
            self._report_curve_additions(self.workspace.comparisons[comparison_id], 1, skipped)
        except ValueError as exc:
            self._show_error(str(exc))

    def _sheet_gate_requested(self, sheet_id, gate_index):
        try:
            self._require_project_idle()
            sheet = self.workspace.sheets[sheet_id]
            owner = self._sheet_owner(sheet_id)
            if sheet.dataset_id != self.workspace.active_dataset_id:
                self.activate_dataset(sheet.dataset_id)
            self._set_current_gate_from_index(gate_index)
            self.open_workbook(owner.workbook_id, sheet_id)
            self._indicate_sheet_gate()
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))

    def _indicate_sheet_gate(self):
        for key in self.graph_documents.keys():
            page = self.graph_documents.page_for(key)
            if isinstance(page, WorkbookPage):
                for sheet_page in page.sheet_pages.values():
                    sheet = self.workspace.sheets.get(sheet_page.sheet_id)
                    if sheet is None:
                        continue
                    active = sheet.kind == 'dat' and sheet.role in ('original', 'processed') and sheet.dataset_id == self.workspace.active_dataset_id
                    sheet_page.model.indicate_gate(self._current_gate_index() + 1 if active else None)

    def _refresh_auxiliary_documents(self):
        for key in self.graph_documents.keys():
            if len(key) == 3:
                if not document_exists(self.workspace, key):
                    self.graph_documents.discard(key)
                else:
                    page = self.graph_documents.page_for(key)
                    if isinstance(page, XYPlotPage) and page.has_pending_project_edits():
                        page.refresh_sources()
                        page.render()
                    else:
                        page.refresh()
                    self.graph_documents.refresh_page_inputs(key)
        self._refresh_comparison_targets()
        self._indicate_sheet_gate()

    def _sync_result_sheets(self, dataset_id=None):
        dataset_id = dataset_id or self.workspace.active_dataset_id
        if dataset_id is not None:
            # SessionField writes science directly into the authoritative session.
            reconcile_dataset_sheets(self.workspace, dataset_id)
            self._refresh_auxiliary_documents()
            self._auxiliary_changed()

    def _auxiliary_changed(self):
        self._refresh_document_titles()
        self._refresh_comparison_targets()
        self._refresh_graph_commands()
        self._schedule_project_modified()

    def _create_plot_page(self, key=None):
        page = PlotPage()
        page.analysisRequested.connect(lambda owner=key:
            self._open_analysis(('heatmap', owner)) if owner is not None else None)
        self._watch_dat_editor_focus(page.control_scroll)
        page.take_control_scroll().setParent(self._sidebar_tabs)
        page.control_scroll.hide()
        page.take_command_bar().setParent(page)
        page.command_bar.hide()
        for signal, callback in (
                (page.presentationChanged, self.set_plot_presentation),
                (page.presentationError, self._on_plot_presentation_error),
                (page.exportRequested, self._on_export_plot_page),
                (page.graphCopyRequested, self._on_copy_graph_svg),
                (page.propertyEditRequested, self._show_plot_property),
                (page.formatSaveRequested, self._on_save_plot_format),
                (page.formatLoadRequested, self._on_load_plot_format),
                (page.formatDeleteRequested, self._on_delete_plot_format),
                (page.formatApplyRequested, self._on_apply_plot_format),
                (page.settingsCopyRequested, self._on_copy_plot_settings),
                (page.settingsPasteRequested, self._on_paste_plot_settings),
                (page.settingsCopyToRequested, self._on_copy_settings_to_datasets),
                (page.comparisonRequested, self._on_open_plot_comparison),
                (page.batchExportRequested, self._on_batch_export_plot),
                (page.batchCancelRequested, self.cancel_plot_batch),
                (page.batchResultsRequested, self._on_plot_batch_results)):
            signal.connect(lambda *args, owner=key, handler=callback:
                           self._dispatch_document(owner, handler, *args))
        page.displayZoomChanged.connect(lambda text: self._sync_display_zoom(text)
                                        if self.plot_page is page else None)
        page.displayScaleChanged.connect(lambda *args: self._on_display_scale_changed(*args)
                                         if self.plot_page is page else None)
        for control in page.control_scroll.findChildren(QAbstractSpinBox):
            control.lineEdit().textChanged.connect(self._schedule_project_modified)
            control.valueChanged.connect(self._schedule_project_modified)
        for control in page.control_scroll.findChildren(QLineEdit):
            if not isinstance(control.parent(), QAbstractSpinBox):
                control.textChanged.connect(self._schedule_project_modified)
        page.annotation_visible.toggled.connect(self._schedule_project_modified)
        page.annotation_font_role.currentTextChanged.connect(self._schedule_project_modified)
        for name, action in getattr(self, '_graph_command_actions', {}).items():
            if name in ('undo', 'redo', 'rescale', 'cancel'):
                page.preview.addAction(action)
        return page

    def _dispatch_document(self, key, callback, *args):
        if key is None or self._activate_document(key):
            callback(*args)

    def _document_key(self):
        session = self.workspace.active or self._unregistered_session
        return (session.dataset_id, session.active_graph_id)

    def _document_title(self, key):
        if len(key) == 3:
            if key[0] == 'workbook':
                return workbook_display_labels(self.workspace)[key[1]]
            collection = self.workspace.workbooks if key[0] == 'workbook' else self.workspace.comparisons
            return collection[key[1]].name
        session = self.workspace.datasets[key[0]]
        labels = dataset_display_labels(self.workspace.datasets.values())
        return f'{labels[session.dataset_id]} — {session.graphs[key[1]].name}'

    def _refresh_document_titles(self):
        for key in self.graph_documents.keys():
            if document_exists(self.workspace, key):
                self.graph_documents.update_title(key, self._document_title(key), self._document_tooltip(key))
        self._refresh_data_navigator()

    def _document_tooltip(self, key):
        if len(key) == 2:
            return str(self.workspace.datasets[key[0]].source_path or 'Embedded data')
        if key[0] == 'workbook':
            return self._workbook_tooltip(self.workspace.workbooks[key[1]])
        comparison = self.workspace.comparisons[key[1]]
        paths = dict.fromkeys(str(self.workspace.sheets[curve.sheet_id].source_path)
                             for curve in comparison.curves.values()
                             if self.workspace.sheets[curve.sheet_id].source_path is not None)
        return f'{comparison.name} · {len(comparison.curves)} curves\n' + ('\n'.join(paths) or 'Embedded values')

    def _bind_plot_page(self, page):
        previous = self.plot_page
        if previous is page:
            return
        sidebar = self._sidebar_tabs
        in_sidebar = sidebar.indexOf(previous.control_scroll) >= 0
        if in_sidebar:
            current = sidebar.currentWidget()
            sidebar.removeTab(sidebar.indexOf(previous.control_scroll))
            previous.control_scroll.hide()
            for index, box in enumerate((self._shared_color_scale_box, self._shared_view_range_box,
                                         self._shared_annotations_box)):
                previous.control_layout.removeWidget(box)
                page.control_layout.insertWidget(index + 1, box)
                box.show()
            sidebar.addTab(page.control_scroll, 'View')
            sidebar.setCurrentWidget(page.control_scroll if current is previous.control_scroll else current)
        self.plot_page = page
        if self._graph_command_stack.indexOf(page.command_bar) < 0:
            self._graph_command_stack.addWidget(page.command_bar)
        self._refresh_graph_command_host()
        self.export_png_btn = page.export_png_btn
        self._sync_display_zoom(page.zoom_combo.currentText())
        if hasattr(self, '_plot_format_store'):
            self._refresh_plot_formats()
        if hasattr(self, '_settings_paste_action'):
            self._refresh_transfer_controls()

    def _refresh_graph_command_host(self):
        self.workbook_commands.sheet_page = None
        if hasattr(self, 'analyze_action'):
            self.analyze_action.setEnabled(not self._project_busy() and self._active_analysis_source() is not None)
        if self.plot_tabs.currentIndex() == 0:
            self.graph_command_host.hide()
            return
        heatmap = (self.plot_tabs.currentWidget() is self.graph_documents
                   and not self._auxiliary_active()
                   and self.graph_documents.window_for(self._document_key()) is not None)
        if heatmap:
            if self._graph_command_stack.indexOf(self.plot_page.command_bar) < 0:
                self._graph_command_stack.addWidget(self.plot_page.command_bar)
            self._graph_command_stack.setCurrentWidget(self.plot_page.command_bar)
        else:
            key = self.graph_documents._active_key
            page = self.graph_documents.page_for(key) if key else None
            if self.plot_tabs.currentWidget() is self.graph_documents and isinstance(page, WorkbookPage):
                sheet_page = page.tabs.currentWidget()
                if sheet_page is not None and sheet_page.sheet_id in self.workspace.sheets:
                    self.workbook_commands.bind(sheet_page)
                    self._graph_command_stack.setCurrentWidget(self.workbook_commands)
                    self.graph_command_host.show()
                    return
            if self.plot_tabs.currentWidget() is self.graph_documents and isinstance(page, XYPlotPage):
                self._graph_command_stack.setCurrentWidget(page.command_bar)
                self.graph_command_host.show()
                return
            self.workspace_command_context.setText(
                self._document_title(key) if key and document_exists(self.workspace, key)
                else 'Open a Workbook or graph from Project.')
            self._graph_command_stack.setCurrentWidget(self.workspace_command_context)
        self.graph_command_host.show()

    def _select_graph_page(self, session, *, show=None):
        key = (session.dataset_id, session.active_graph_id)
        page = self.graph_documents.page_for(key)
        if page is None:
            page = self._create_plot_page(key)
            page.set_presentation(graph_presentation(session))
            page.invalidate(self._plot_owner_token(key))
            self.graph_documents.cache_page(key, page)
        self._bind_plot_page(page)
        if show is None:
            show = self.plot_tabs.currentWidget() is self.graph_documents and not self._auxiliary_active()
        if show and session.active_graph.published and session.active_graph.analysis is None:
            self.graph_documents.show_document(key, self._document_title(key))
            self._schedule_plot_page_render()
        self._refresh_graph_commands()
        return page

    def open_graph_document(self, dataset_id=None, graph_id=None):
        """Open or reactivate a retained graph after committing the outgoing owner."""
        dataset_id = dataset_id or self.workspace.active_dataset_id
        session = self.workspace.datasets.get(dataset_id)
        if session is None or session.current_cube is None:
            raise ValueError('Choose an imported DAT first.')
        graph_id = graph_id or session.active_graph_id
        if graph_id not in session.graphs:
            raise ValueError('The graph no longer exists.')
        if session.graphs[graph_id].analysis is not None:
            return self._workbench_open(('heatmap', (dataset_id, graph_id)))
        key = (dataset_id, graph_id)
        if self._project_busy():
            raise ValueError('Wait for the current operation before changing graphs.')
        if self._auxiliary_active():
            self._finish_auxiliary_editor(self.graph_documents._active_key)
        if key != self._document_key() or not session.graphs[graph_id].published:
            self._finish_graph_editors()
            if dataset_id != self.workspace.active_dataset_id:
                notice = self._save_current_plot_state()
                previous_graph_id = session.active_graph_id
                session.activate_graph(graph_id)
                try:
                    self.activate_dataset(dataset_id, capture_outgoing=False)
                except Exception:
                    session.activate_graph(previous_graph_id)
                    raise
                if notice:
                    self.statusBar().showMessage(self.statusBar().currentMessage() + ' — ' + notice)
            else:
                self.activate_graph(graph_id)
        if not session.graphs[graph_id].published:
            session.graphs[graph_id].published = True
            self._refresh_graph_selector()
            self._refresh_document_titles()
            notice = self._save_current_plot_state()
            if notice:
                self.statusBar().showMessage(notice)
            self._schedule_project_modified()
        page = self._select_graph_page(session, show=True)
        self.plot_tabs.setCurrentWidget(self.graph_documents)
        self._on_plot_tab_changed(self.plot_tabs.currentIndex())
        self._refresh_graph_commands()
        return page

    def _activate_document(self, key):
        if len(key) == 3:
            if key == self.graph_documents._active_key and self._auxiliary_active():
                return not self._project_busy()
            try:
                self._open_auxiliary_document(key)
                return True
            except ValueError as exc:
                self.statusBar().showMessage(str(exc))
                return False
        if self._binding_graph_document:
            return key == self._document_key()
        if key == self._document_key() and not self._auxiliary_active():
            return True
        self._binding_graph_document = True
        try:
            self.open_graph_document(*key)
            return True
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return False
        finally:
            self._binding_graph_document = False

    def _finish_close_document(self, key):
        try:
            if self._project_busy():
                raise ValueError('Wait for the current operation before closing a graph view.')
            if len(key) == 3:
                self._finish_auxiliary_editor(key)
            elif key == self._document_key():
                self._finish_graph_editors()
            return True
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return False

    def _close_visible_document(self):
        if self.plot_tabs.currentWidget() is not self.graph_documents:
            return False
        return self.graph_documents.close_document()

    def _discard_dataset_documents(self, dataset_id):
        self._formal_peak_label_signatures.pop(dataset_id, None)
        keys = [key for key in self.graph_documents.keys() if len(key) == 2 and key[0] == dataset_id]
        if any(self.graph_documents.page_for(key) is self.plot_page for key in keys):
            self._bind_plot_page(self._fallback_plot_page)
        for key in keys:
            self.graph_documents.discard(key)
            self._graph_revisions.pop(key, None)

    def _on_open_graph_document(self):
        try:
            self.open_graph_document()
        except ValueError as exc:
            self._show_error(str(exc))

    def _build_graph_commands(self):
        """Shared everyday command hooks; Edit can also host dataset history."""
        self._file_menu = self.menuBar().addMenu('&File')
        self._edit_menu = self.menuBar().addMenu('&Edit')
        self._view_menu = self.menuBar().addMenu('&View')
        self._analysis_menu = self.menuBar().addMenu('&Analysis')
        self._help_menu = self.menuBar().addMenu('&Help')
        self.check_updates_action = self._help_menu.addAction('Check for Updates…', self._show_updates)
        self.analyze_action = self._analysis_menu.addAction(
            'Analyze', lambda: self._open_analysis(self._active_analysis_source()))
        self.back_to_workspace_action = self._analysis_menu.addAction('Back to Workspace', self._back_to_workspace)
        self._analysis_menu.aboutToShow.connect(self._refresh_graph_commands)
        self.project_panel_action = self._view_menu.addAction('Project tree')
        self.project_panel_action.setCheckable(True)
        self.project_panel_action.setChecked(True)
        self.project_panel_action.setShortcut(QKeySequence('Ctrl+Shift+P'))
        self.project_panel_action.toggled.connect(self._set_project_panel_visible)
        self.dat_details_action = self._view_menu.addAction('Properties')
        self.dat_details_action.setToolTip('Show properties for the active Workbook or graph.')
        self.dat_details_action.setCheckable(True)
        self.dat_details_action.setChecked(self._dat_details_requested)
        self.dat_details_action.toggled.connect(self._set_dat_details_requested)
        self._view_menu.addSeparator()
        self.open_graph_action = self._view_menu.addAction('Open Graph', self._on_open_graph_document)
        self.free_graphs_action = self._view_menu.addAction('Free Windows', self.graph_documents.free_view)
        self.tabbed_graphs_action = self._view_menu.addAction('Tabbed View', self.graph_documents.tabbed_view)
        self.free_graphs_action.setCheckable(True)
        self.tabbed_graphs_action.setCheckable(True)
        self.cascade_graphs_action = self._view_menu.addAction('Cascade Graphs', self.graph_documents.cascade_graphs)
        self.tile_graphs_action = self._view_menu.addAction('Tile Graphs', self.graph_documents.tile_graphs)
        self.close_graph_view_action = self._view_menu.addAction(
            'Close Document View', self._close_visible_document)
        self._graph_command_actions = {}
        for key, menu, text, shortcut, callback in (
                ('new_project', self._file_menu, 'New Project', 'Ctrl+N', self._on_new_project),
                ('open_project', self._file_menu, 'Open Project…', 'Ctrl+Shift+O', self._on_open_project),
                ('save_project', self._file_menu, 'Save Project', 'Ctrl+S', self._on_save_project),
                ('save_project_as', self._file_menu, 'Save Project As…', 'Ctrl+Shift+S',
                 lambda: self._on_save_project(save_as=True)),
                ('import', self._file_menu, 'Import data…', 'Ctrl+O', self._on_import_data),
                ('import_options', self._file_menu, 'Import with options…', '',
                 lambda: self._on_import_data(review=True)),
                ('undo', self._edit_menu, 'Undo', 'Ctrl+Z', self.undo_edit),
                ('redo', self._edit_menu, 'Redo', 'Ctrl+Y', self.redo_edit),
                ('copy_graph', self._edit_menu, 'Copy Graph as SVG', 'Ctrl+Alt+J', self._on_copy_graph_svg),
                ('rescale', self._view_menu, 'Rescale Graph', 'Ctrl+R', self._on_rescale_graph),
                ('cancel', self._edit_menu, 'Cancel Graph Selection / Drag', 'Esc', self._cancel_graph_interaction)):
            action = menu.addAction(text, callback)
            action.setShortcut(QKeySequence(shortcut))
            if key in ('undo', 'redo', 'rescale', 'cancel'):
                # Associate graph-only shortcuts with the canvases. Explicit
                # menu activation still works while a text editor has focus.
                action.setShortcutContext(Qt.WidgetShortcut)
                self.canvas.addAction(action)
                self.plot_page.preview.addAction(action)
            else:
                action.setShortcutContext(Qt.WindowShortcut)
            self._graph_command_actions[key] = action
        self._graph_command_actions['import'].setToolTip('Automatically import full tables: first column X, all remaining columns Y.')
        self._graph_command_actions['import_options'].setToolTip('Preview detected types, choose X/Y columns, group Workbooks or compare.')
        self._graph_command_actions['redo'].setShortcuts([QKeySequence('Ctrl+Y'), QKeySequence('Ctrl+Shift+Z')])
        self._recent_projects_menu = QMenu('Recent Projects', self)
        self._recent_projects_menu.setToolTipsVisible(True)
        self._file_menu.insertMenu(self._graph_command_actions['save_project'], self._recent_projects_menu)
        self._refresh_recent_projects()
        self.graph_documents.set_start_actions(self._graph_command_actions['import'],
            self._graph_command_actions['open_project'], self._recent_projects_menu)
        self._edit_menu.aboutToShow.connect(self._refresh_graph_commands)
        self._file_menu.aboutToShow.connect(self._refresh_graph_commands)
        self.import_dats_btn.installEventFilter(self)
        self._refresh_graph_commands()

    def _refresh_graph_commands(self):
        self._refresh_analysis_context()
        self._refresh_navigator_actions()
        self._refresh_workspace_shell()
        for key in self.graph_documents.keys():
            page = self.graph_documents.page_for(key)
            if isinstance(page, XYPlotPage):
                page.add_data_btn.setEnabled(not self._project_busy() and bool(self.workspace.sheets))
                page.analyze_action.setEnabled(not self._project_busy())
                page.analysis_panel.sync()
        self._refresh_graph_lifecycle_controls()
        self._refresh_graph_command_host()
        self._refresh_navigator_active()
        for key in self.graph_documents.keys():
            if document_exists(self.workspace, key):
                self.graph_documents.update_title(key, self._document_title(key), self._document_tooltip(key))
        if not hasattr(self, '_graph_command_actions'):
            return
        ready = self.current_cube is not None
        idle = not self._project_busy()
        self.analyze_action.setEnabled(idle and self._active_analysis_source() is not None)
        self.back_to_workspace_action.setEnabled(idle and self.plot_tabs.currentIndex() == 0)
        self.analysis_back_btn.setEnabled(idle)
        for key in self.graph_documents.keys():
            page = self.graph_documents.page_for(key)
            if isinstance(page, PlotPage):
                page.analyze_btn.setEnabled(idle and self._resolve_analysis_source(('heatmap', key)) is not None)
        self.open_graph_action.setEnabled(ready and self.workspace.active is not None and idle)
        self.close_graph_view_action.setEnabled(
            idle and self.plot_tabs.currentWidget() is self.graph_documents
            and self.graph_documents.window_for(self.graph_documents._active_key) is not None)
        visible = bool(self.graph_documents.subWindowList())
        self.tabbed_graphs_action.setEnabled(visible)
        self.tile_graphs_action.setEnabled(visible)
        self.free_graphs_action.setEnabled(visible)
        self.cascade_graphs_action.setEnabled(visible)
        tabbed = self.graph_documents.viewMode() == GraphDocuments.ViewMode.TabbedView
        self.tabbed_graphs_action.setChecked(tabbed)
        self.free_graphs_action.setChecked(not tabbed)
        for key in ('new_project', 'open_project', 'save_project', 'save_project_as'):
            self._graph_command_actions[key].setEnabled(not self._project_busy())
        if hasattr(self, '_recent_projects_menu'):
            self._recent_projects_menu.setEnabled(not self._project_busy())
        self._graph_command_actions['import'].setEnabled(self.import_dats_btn.isEnabled())
        self._graph_command_actions['import_options'].setEnabled(self.import_dats_btn.isEnabled())
        auxiliary = self._auxiliary_active()
        xy = auxiliary and self.graph_documents._active_key[0] == 'xy'
        heatmap_visible = (self.plot_tabs.currentWidget() is not self.graph_documents or
                           self.graph_documents._active_key == self._document_key() and
                           self.graph_documents.window_for(self._document_key()) is not None)
        self._graph_command_actions['copy_graph'].setEnabled(idle and (xy or (heatmap_visible and not auxiliary and ready and self.plot_page.presentation_error is None)))
        self._graph_command_actions['rescale'].setEnabled(idle and (xy or (not auxiliary and ready)))
        self._graph_command_actions['cancel'].setEnabled(not auxiliary)
        session = self.workspace.active
        if xy:
            page = self.graph_documents.page_for(self.graph_documents._active_key)
            for key in ('undo', 'redo'):
                action = self._graph_command_actions[key]
                action.setText(key.title())
                action.setEnabled(not self._history_busy() and getattr(page, f'can_{key}')())
            return
        history = self._history_for(session) if session else None
        pending = self._pending_history_edit
        if pending and not field_changes(pending[3], capture_fields(pending[0], pending[2])):
            pending = None
        for key, stack in (('undo', history.undo_stack if history else []),
                           ('redo', history.redo_stack if history else [])):
            command = stack[-1] if stack else None
            label = (pending[1] if key == 'undo' and pending else command.label if command else '')
            graph_id = pending[4] if key == 'undo' and pending else command.graph_id if command else None
            if label and graph_id in session.graphs:
                label += ' — ' + session.graphs[graph_id].name
            action = self._graph_command_actions[key]
            action.setText(key.title() + (' ' + label if label else ''))
            action.setEnabled(not auxiliary and bool(session and label) and not self._history_busy())

    def _on_rescale_graph(self):
        if self._auxiliary_active():
            page = self.graph_documents.page_for(self.graph_documents._active_key)
            if isinstance(page, XYPlotPage):
                page.render()
            return
        if self.current_cube is not None:
            self._on_auto_view()

    def _cancel_graph_interaction(self):
        if self._auxiliary_active():
            return
        self.plot_page.cancel_interaction()
        if self.plot_tabs.currentWidget() is not self.graph_documents:
            if self._motion_cid is not None:
                self.canvas.mpl_disconnect(self._motion_cid)
                self._motion_cid = None
            self._drag_peak_arr_idx = None
            self._drag_current_x = None
            self._drag_scatter = None
            self.lasso_mode_btn.setChecked(False)
            self.circle_delete_btn.setChecked(False)
            self._batch_delete_circle_start = None
            self._lasso_selected_peak_ids = []
            self._clear_lasso_selection_visual()
            self._update_lasso_actions()
            self._update_spectrum_only()

    def _show_plot_property(self, kind, identifier=''):
        page = self.plot_page.rendered_page
        if (self.plot_tabs.currentWidget() is not self.graph_documents or page is None
                or page.owner_token != self._plot_owner_token()):
            return
        editor = None
        if kind == 'title':
            editor = self.plot_title_edit
        elif kind == 'signal-label':
            editor = self.signal_title_combo
        elif kind in ('horizontal-label', 'vertical-label', 'horizontal-range', 'vertical-range'):
            horizontal = kind.startswith('horizontal')
            source_y = horizontal == self.plot_page.presentation.axis.exchange_xy
            if kind.endswith('-range'):
                editor = self.ymin_spin if source_y else self.xmin_spin
            else:
                editor = self.y_axis_name_edit if source_y else self.x_axis_override_combo
        else:
            editor = self.plot_page.property_editor(kind, identifier)
        if editor is not None:
            self._set_dat_details_requested(True)
            self._sidebar_tabs.setCurrentWidget(self.plot_page.control_scroll)
            reveal_editor(editor)
            self.plot_page.control_scroll.ensureWidgetVisible(editor)
            editor.setFocus(Qt.MouseFocusReason)
            if isinstance(editor, QLineEdit):
                editor.selectAll()

    def _plot_owner_token(self, key=None):
        dataset_id, graph_id = key or self._document_key()
        science = self._plot_revisions.setdefault(dataset_id, [0, 0, 0])[0]
        view, presentation = self._graph_revisions.setdefault((dataset_id, graph_id), [0, 0])
        return (dataset_id, self._plot_lifetime, science, view, presentation, graph_id)

    def _invalidate_graph_document(self, key, *, kind='view'):
        revisions = self._graph_revisions.setdefault(key, [0, 0])
        revisions[1 if kind == 'presentation' else 0] += 1
        page = self.graph_documents.page_for(key)
        if page is None and key == self._document_key():
            page = self.plot_page
        if page is not None:
            page.invalidate(self._plot_owner_token(key))

    def _invalidate_dataset_documents(self, dataset_id):
        self._plot_revisions.setdefault(dataset_id, [0, 0, 0])[0] += 1
        keys = [key for key in self.graph_documents.keys() if len(key) == 2 and key[0] == dataset_id]
        if self._document_key()[0] == dataset_id and self._document_key() not in keys:
            keys.append(self._document_key())
        for key in keys:
            self._invalidate_graph_document(key)

    def _sync_formal_plot_view(self):
        if self._workbench_document() is not None:
            return
        if self._restoring_dataset:
            return
        self._capture_dataset_controls(visual_only=True)
        session = self.workspace.active or self._unregistered_session
        if self._heat_ax is not None:
            session.view.x_limits = tuple(map(float, self._heat_ax.get_xlim()))
            session.view.y_limits = tuple(map(float, self._heat_ax.get_ylim()))
        if self._last_valid_color_limits is not None:
            session.view.color_limits = tuple(self._last_valid_color_limits)

    def _invalidate_plot_page(self, *_args, kind='view', schedule=True, sync=True):
        self._schedule_project_modified()
        if not hasattr(self, 'plot_page') or self._restoring_dataset:
            return
        self._refresh_transfer_controls()
        if sync:
            self._sync_formal_plot_view()
        self._update_mapping_status()
        if kind == 'science':
            self._invalidate_dataset_documents(self._document_key()[0])
        else:
            self._invalidate_graph_document(self._document_key(), kind=kind)
        if hasattr(self, '_plot_render_timer'):
            self._plot_render_timer.stop()
            if schedule:
                self._schedule_plot_page_render()

    def _schedule_plot_page_render(self, *, delay=None):
        if self._workbench_document() is not None and self.plot_tabs.currentIndex() == 0:
            self._plot_render_timer.start(180 if delay is None else delay)
            return
        if (self.plot_tabs.currentWidget() is not self.graph_documents
                or self.current_cube is None or self._annotation_edit_pending
                or (getattr(self, '_plot_batch', None) and not self._plot_batch.done)):
            return
        self._plot_render_timer.start(180 if delay is None else delay)

    def _on_plot_tab_changed(self, _index, *, restore_graph=True):
        previous = getattr(self, '_previous_plot_tab_index', 0)
        current = self.plot_tabs.currentIndex()
        active = self.graph_documents._active_key
        if (previous == 1 and current == 0 and active
                and not self._project_installing):
            try:
                if len(active) == 3:
                    self._finish_auxiliary_editor(active)
                else:
                    self._finish_graph_editors()
            except ValueError as exc:
                with QSignalBlocker(self.plot_tabs):
                    self.plot_tabs.setCurrentIndex(previous)
                self.statusBar().showMessage(str(exc))
                return
        self._previous_plot_tab_index = current
        if current == 0 and previous != 0:
            self._analysis_return_key = active
        plot_mode = self.plot_tabs.currentWidget() is self.graph_documents and not self._auxiliary_active()
        if plot_mode and self.workspace.active is not None and restore_graph:
            self._select_graph_page(self.workspace.active, show=(self.graph_documents.initial_view
                                    or bool(self.graph_documents.subWindowList())))
        self._refresh_graph_command_host()
        sidebar = self._sidebar_tabs
        if plot_mode and sidebar.indexOf(self.plot_page.control_scroll) < 0:
            self._analysis_sidebar_index = sidebar.currentIndex()
            self._analysis_sidebar_widget = sidebar.currentWidget()
            sidebar.removeTab(sidebar.indexOf(self._view_sidebar_scroll))
            sidebar.addTab(self.plot_page.control_scroll, 'View')
            preferred = getattr(self, '_heatmap_sidebar_widget', None)
            sidebar.setCurrentWidget(preferred if preferred in (self._data_sidebar_scroll, self._analysis_sidebar_scroll)
                                     else self.plot_page.control_scroll)
            for index, box in enumerate((self._shared_color_scale_box, self._shared_view_range_box,
                                         self._shared_annotations_box)):
                self._view_settings_layout.removeWidget(box)
                self.plot_page.control_layout.insertWidget(index + 1, box)
                box.show()
        elif not plot_mode and sidebar.indexOf(self.plot_page.control_scroll) >= 0:
            self._heatmap_sidebar_widget = sidebar.currentWidget()
            sidebar.removeTab(sidebar.indexOf(self.plot_page.control_scroll))
            sidebar.insertTab(2, self._view_sidebar_scroll, 'View')
            sidebar.setCurrentWidget(self._analysis_sidebar_widget)
            for box in (self._shared_color_scale_box, self._shared_view_range_box,
                        self._shared_annotations_box):
                self.plot_page.control_layout.removeWidget(box)
                self._view_settings_layout.addWidget(box)
                box.show()
        self._plot_render_timer.stop()
        if (self.plot_tabs.currentWidget() is not self.graph_documents
                and getattr(self, '_annotation_layout_pending', False)):
            self._annotation_layout_pending = False
            self._layout_live_plots()
            self.canvas.draw_idle()
        if self.plot_page.rendered_page is None:
            self._schedule_plot_page_render()
        self._workbench_set_visible()
        self._refresh_graph_commands()

    def _render_visible_plot_page(self):
        if self._workbench_document() is not None and self.plot_tabs.currentIndex() == 0:
            self._workbench_render()
            return
        if self.plot_tabs.currentWidget() is self.graph_documents:
            for key in self.graph_documents.visible_keys():
                page = self.graph_documents.page_for(key)
                if len(key) == 3:
                    continue
                if key == self._document_key() and self._annotation_edit_pending:
                    continue
                if page is not None and page.rendered_page is None:
                    self._render_plot_page(key)

    def set_plot_presentation(self, presentation: PlotPresentation):
        """Presentation edit/application hook, shared by controls and format tools."""
        if self._workbench_document() is not None:
            return
        if not isinstance(presentation, PlotPresentation):
            raise ValueError('Expected formal plot presentation settings.')
        session = self.workspace.active or self._unregistered_session
        if presentation == session.presentation:
            # Formal capture calls finish_editor even when nothing was edited.
            return
        before = capture_fields(session, ('presentation',)) if self._history_enabled() else None
        if before is not None:
            self._finish_history_edit()
        previous = session.presentation
        session.presentation = presentation
        self.plot_page.set_presentation(presentation)
        # Retire old export bytes before projecting fallible coordinate styles.
        self._invalidate_plot_page(kind='presentation')
        if self.plot_tabs.currentWidget() is self.graph_documents:
            self._apply_live_heatmap_style()
            self._annotation_layout_pending = True
        else:
            self._annotation_layout_pending = False
            self._layout_live_plots()
            self.canvas.draw_idle()
        if getattr(self, '_live_heatmap_style_error', None):
            self.plot_page.status_label.setText(f'Cannot prepare page: {self._live_heatmap_style_error}')
        if before is not None:
            label = 'Plot presentation'
            if previous.annotations != presentation.annotations:
                label = ('Add annotation' if len(presentation.annotations) > len(previous.annotations)
                         else 'Delete annotation' if len(presentation.annotations) < len(previous.annotations)
                         else 'Edit annotation')
            elif previous.title_position != presentation.title_position:
                label = 'Move title'
            self._record_history(session, label, before, capture_fields(session, ('presentation',)))

    def _on_plot_presentation_error(self, message):
        self._invalidate_plot_page(kind='presentation', schedule=False)
        self.plot_page.status_label.setText(f'Cannot prepare page: {message}')

    def snapshot_plot_dataset(self, session=None):
        """Batch-safe snapshot hook. Inactive sessions never project into widgets."""
        session = session or self.workspace.active or self._unregistered_session
        owner = self._plot_owner_token((session.dataset_id, session.active_graph_id))
        return snapshot_dataset(session, activation_epoch=owner[1],
                                science_revision=owner[2], view_revision=owner[3],
                                presentation_revision=owner[4])

    def _render_plot_page(self, key=None):
        """Render one owned snapshot serially; navigation never retires other pages."""
        render_key = key or self._document_key()
        render_session = self.workspace.datasets.get(render_key[0])
        if render_session is not None and render_session.graphs[render_key[1]].analysis is not None:
            return self._workbench_render(render_key)
        self._plot_render_timer.stop()
        if getattr(self, '_plot_batch', None) and not self._plot_batch.done:
            return
        if self._background_jobs.busy:
            self._schedule_plot_page_render()
            return
        key = key or self._document_key()
        active = key == self._document_key()
        if active and self._annotation_edit_pending:
            self._finish_annotation_edit(render=False)
        target = self.plot_page if active else self.graph_documents.page_for(key)
        session = self.workspace.datasets.get(key[0])
        if session is None and active:
            session = self._unregistered_session
        if target is None or session is None or key[1] not in session.graphs:
            return
        owner = self._plot_owner_token(key)
        if target.presentation_error is not None:
            target.invalidate(owner, f'Cannot prepare page: {target.presentation_error}')
            return
        if target.has_pending_project_edits():
            target.invalidate(owner, 'Finish or correct the current page editor before preparing the page.')
            return
        if active and self._heat_ax is not None:
            self._apply_live_heatmap_style()
            if self._live_heatmap_style_error:
                target.invalidate(owner, f'Cannot prepare page: {self._live_heatmap_style_error}')
                return
        if session.current_cube is None:
            target.invalidate(owner, 'Load a DAT file to prepare a formal page.')
            return
        try:
            graph_session = detached_graph_session(replace(session, active_graph_id=key[1]))
            if not active and target.presentation != graph_session.presentation:
                target.set_presentation(graph_session.presentation)
            frame_before = self._live_heatmap_frame_size(session.presentation) if active else None
            snapshot = self.snapshot_plot_dataset(graph_session)
            presentation, page = prepare_heatmap_page(snapshot, graph_session.presentation,
                                                       renderer=render_hybrid_svg)
            current = self.workspace.datasets.get(key[0])
            if (owner != self._plot_owner_token(key) or
                    (current is not session and session is not self._unregistered_session) or
                    key[1] not in session.graphs or target.has_pending_project_edits()):
                return
            if presentation != graph_session.presentation:
                self._adopt_prepared_page_size(session, key, presentation)
                owner = self._plot_owner_token(key)
                snapshot = replace(snapshot, presentation_revision=owner[4])
                page = replace(page, owner_token=owner)
            target.preview.set_geometry(snapshot, presentation)
            if target.commit_rendered_page(page) and active:
                layout_pending = (getattr(self, '_annotation_layout_pending', False)
                                  or not np.allclose(frame_before, page.plot_size_in, rtol=1e-9, atol=1e-9))
                if self.plot_tabs.currentWidget() is self.graph_documents:
                    self._annotation_layout_pending = layout_pending
                else:
                    self._annotation_layout_pending = False
                    if layout_pending:
                        self._layout_live_plots()
                    self.canvas.draw_idle()
        except Exception as exc:
            if owner == self._plot_owner_token(key):
                target.invalidate(owner, f'Cannot prepare page: {exc}')

    def _adopt_prepared_page_size(self, session, key, presentation):
        """Commit measured paper size to the graph that supplied the snapshot."""
        session.graphs[key[1]].presentation = presentation
        target = self.graph_documents.page_for(key)
        if target is None and key == self._document_key():
            target = self.plot_page
        if target is not None:
            target.set_presentation(presentation)
        self._invalidate_graph_document(key, kind='presentation')
        self._schedule_project_modified()

    def export_plot_page(self, extension):
        """Flush annotation drafts, then write validated current formal bytes."""
        if self._workbench_document() is not None and not self._auxiliary_active():
            self._workbench_finish_editors()
            self._workbench_render()
        if self._auxiliary_active():
            page = self.graph_documents.page_for(self.graph_documents._active_key)
            if isinstance(page, XYPlotPage):
                page._export_dialog(extension)
                return
            raise ValueError('Choose a comparison graph to export; a workbook has no graph image.')
        if self.plot_tabs.currentWidget() is self.graph_documents and self.graph_documents._active_key is None:
            raise ValueError('Open a graph document to export.')
        if (self._annotation_edit_pending or (self.plot_page.rendered_page is None
                and getattr(self, '_annotation_layout_pending', False))):
            self._finish_annotation_edit(render=False)
            self._render_plot_page()
        page = self.plot_page.rendered_page
        if page is None or page.owner_token != self._plot_owner_token():
            raise ValueError('The formal page is not ready. Wait for the current preview.')
        if extension not in ('svg', 'png'):
            raise ValueError('Choose SVG or PNG output.')
        folder = self._output_folder()
        path = folder / self._heatmap_export_filename(extension)
        if extension == 'svg':
            saved = write_svg(page, path)
        else:
            saved = write_svg_png(page, path, metadata={
                'SpectraAnalyzer': json.dumps(self._heatmap_export_metadata(), ensure_ascii=False, sort_keys=True)})
        notice = self._save_current_plot_state()
        self.statusBar().showMessage(f'Exported {extension.upper()}: {saved}' + (f' — {notice}' if notice else ''))
        return saved

    def _on_export_plot_page(self, extension):
        try:
            self.export_plot_page(extension)
        except Exception as exc:
            self._show_error(f'Failed to export {extension.upper()}: {exc}')

    def copy_graph_svg(self):
        """Publish only the final hybrid SVG after fresh validation succeeds."""
        if self._auxiliary_active():
            page = self.graph_documents.page_for(self.graph_documents._active_key)
            if not isinstance(page, XYPlotPage):
                raise ValueError('Select table cells and use Ctrl+C, or choose a comparison graph.')
            mime = QMimeData()
            mime.setData('image/svg+xml', page.svg_bytes())
            QApplication.clipboard().setMimeData(mime)
            return page
        if self.plot_tabs.currentWidget() is self.graph_documents and self.graph_documents._active_key is None:
            raise ValueError('Open a graph document to copy.')
        _session, _snapshot, page = self._capture_formal_source()
        mime = QMimeData()
        mime.setData('image/svg+xml', page.svg_bytes)
        QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage(f'Copied hybrid SVG with embedded heatmap at {page.heatmap_dpi} DPI.')
        return page

    def _on_copy_graph_svg(self):
        try:
            self.copy_graph_svg()
        except Exception as exc:
            self._show_error(f'Cannot copy graph as SVG: {exc}')

    def _refresh_plot_formats(self, selected=None):
        try:
            self.plot_page.set_format_names(self._plot_format_store.list_names(), selected)
        except (ValueError, OSError, TypeError) as exc:
            self.plot_page.set_format_names(())
            self.plot_page.status_label.setText(f'Cannot read named formats: {exc}')

    def save_plot_format(self, name):
        session, snapshot, page = self._capture_formal_source()
        self._plot_format_store.save(capture_format(session, name, source_snapshot=snapshot, source_page=page))
        self._refresh_plot_formats(name)

    def _refresh_transfer_controls(self):
        if not hasattr(self, 'plot_page'):
            return
        session = self.workspace.active
        available = session is not None and session.current_cube is not None and self.plot_page.presentation_error is None
        self.plot_page.set_transfer_availability(available, self._plot_settings_source_label)
        self._refresh_graph_commands()
        if available:
            self.plot_page.comparison_btn.setToolTip(self.plot_page.comparison_btn.toolTip() +
                f'\nSource: {session.source_path} [ID: {session.dataset_id}]')
        if hasattr(self, '_settings_copy_action'):
            self._settings_copy_action.setEnabled(available)
            self._settings_copy_to_action.setEnabled(available)
            self._settings_paste_action.setEnabled(available and self._plot_settings_clipboard is not None)
            self._settings_paste_action.setToolTip(self.plot_page.paste_settings_btn.toolTip())
            self._comparison_action.setEnabled(available)
            self._comparison_action.setToolTip(self.plot_page.comparison_btn.toolTip())

    def _capture_formal_source(self):
        """Finalize editors and validate fresh formal bytes before freezing a source."""
        if self._workbench_document() is not None:
            self._require_project_idle()
            self._workbench_finish_editors()
            self._workbench_render()
            page = self.plot_page.rendered_page
            if page is None or page.owner_token != self._plot_owner_token():
                raise ValueError(self.plot_page.status_label.text())
            return self.workspace.active, self.plot_page.preview.snapshot, page
        session = self.workspace.active
        if session is None or session.current_cube is None:
            raise ValueError('Load a DAT before capturing its plot settings.')
        if self._background_jobs.busy or (self._plot_batch is not None and not self._plot_batch.done):
            raise ValueError('Wait for the current operation before capturing this page.')
        if self.plot_page.presentation_error is not None:
            raise ValueError(f'Correct the current page editor first: {self.plot_page.presentation_error}')
        for name in ('xmin', 'xmax', 'ymin', 'ymax', 'vmin', 'vmax'):
            control = getattr(self, name + '_spin')
            if not control.hasAcceptableInput():
                raise ValueError(f'Correct the current range editor first: {name}.')
            control.interpretText()
        self.plot_page.finish_editor()
        self._finish_annotation_edit()
        self._finish_history_edit()
        self._sync_formal_plot_view()
        self._render_plot_page()
        page = self.plot_page.rendered_page
        if page is None or page.owner_token != self._plot_owner_token():
            raise ValueError(self.plot_page.status_label.text())
        return session, self.plot_page.preview.snapshot, page

    def copy_plot_settings(self):
        session, snapshot, page = self._capture_formal_source()
        template = capture_format(session, 'Copied settings', source_snapshot=snapshot, source_page=page)
        # Replace the clipboard only after every capture/validation step succeeds.
        self._plot_settings_clipboard = template
        self._plot_settings_source_label = f'{session.source_path} — {session.active_graph.name} [ID: {session.dataset_id}]'
        self._refresh_transfer_controls()
        self.statusBar().showMessage('Copied settings from ' + self._plot_settings_source_label)
        return template

    def create_paste_settings_dialog(self, *, current_only=False):
        if self._plot_settings_clipboard is None:
            raise ValueError('Copy settings from a valid source page first.')
        sessions = (self.workspace.active,) if current_only else self.workspace.datasets.values()
        if self.workspace.active is None:
            raise ValueError('Choose an imported target dataset first.')
        self.capture_active_dataset()
        return ApplyFormatDialog(sessions, self.workspace.active_dataset_id, self._plot_settings_clipboard,
                                 self, source_label=self._plot_settings_source_label)

    def paste_plot_settings(self, dataset_ids=None, *, options=None, annotation_pairs=None):
        if self._plot_settings_clipboard is None:
            raise ValueError('Copy settings from a valid source page first.')
        ids = [self.workspace.active_dataset_id] if dataset_ids is None else dataset_ids
        return self._apply_plot_template(self._plot_settings_clipboard, ids,
            options=options or FormatApplicationOptions(), annotation_pairs=annotation_pairs)

    def open_plot_comparison(self):
        session, snapshot, page = self._capture_formal_source()
        label = f'{session.source_path} — {session.active_graph.name} [ID: {session.dataset_id}]'
        window = PlotComparisonWindow(page, source_label=label,
            metadata=self._plot_dataset_metadata(session, snapshot), png_dpi=session.presentation.heatmap_dpi,
            output_folder=self._output_folder(session), parent=self)
        self._plot_comparison_windows.append(window)
        window.destroyed.connect(lambda: self._forget_plot_comparison(window))
        window.show()
        return window

    def _forget_plot_comparison(self, window):
        if window in self._plot_comparison_windows:
            self._plot_comparison_windows.remove(window)

    def _on_open_plot_comparison(self):
        try:
            self.open_plot_comparison()
        except Exception as exc:
            self._show_error(f'Cannot capture comparison: {exc}')

    def _on_copy_plot_settings(self):
        try:
            self.copy_plot_settings()
        except Exception as exc:
            self._show_error(f'Cannot copy settings: {exc}')

    def _on_paste_plot_settings(self):
        self._show_paste_settings_dialog(current_only=True)

    def _on_copy_settings_to_datasets(self):
        try:
            self.copy_plot_settings()
        except Exception as exc:
            self._show_error(f'Cannot copy settings: {exc}')
            return
        self._show_paste_settings_dialog(current_only=False)

    def _show_paste_settings_dialog(self, *, current_only):
        dialog = None
        try:
            dialog = self.create_paste_settings_dialog(current_only=current_only)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._report_format_outcomes(self.paste_plot_settings(dialog.selected_ids(),
                    options=dialog.options(), annotation_pairs=dialog.annotation_pairs()))
        except Exception as exc:
            self._show_error(f'Cannot paste settings: {exc}')
        finally:
            if dialog is not None:
                dialog.deleteLater()

    def delete_plot_format(self, name):
        removed = self._plot_format_store.delete(name)
        self._refresh_plot_formats()
        return removed

    def create_format_dialog(self, name, *, current_only=False):
        self.capture_active_dataset()
        sessions = (self.workspace.active,) if current_only else self.workspace.datasets.values()
        if current_only and self.workspace.active is None:
            raise ValueError('Choose an imported target dataset first.')
        return ApplyFormatDialog(sessions, self.workspace.active_dataset_id,
                                 self._plot_format_store.load(name), self)

    def _save_plot_dataset(self, session):
        if not session.sidecar_writes_enabled:
            return
        if session.source_path is None or session.loaded_fingerprint is None:
            raise ValueError('The dataset has no loaded source fingerprint for its plot sidecar.')
        self._plot_state_store.save(session.source_path, session.loaded_fingerprint, session.plot_collection())

    def _project_plot_dataset(self, session):
        """Project applied records without capturing the previous widgets or autoscaling."""
        view = deepcopy(session.view)
        recipe = view.plot_recipe(session.current_cube, session.applied, session.presentation)
        self._restoring_dataset = True
        try:
            self._invalidate_plot_page(schedule=False, sync=False)
            self.restore_dataset_controls(session)
            self._plot_cube()
            session.view = view
            self._restore_effective_plot_limits(recipe)
        finally:
            self._restoring_dataset = False
        self._invalidate_plot_page(kind='presentation', sync=False)

    def apply_plot_format(self, name, dataset_ids, *, copy_mapping=False, copy_color_limits=False,
                          options=None, annotation_pairs=None):
        template = self._plot_format_store.load(name)
        return self._apply_plot_template(template, dataset_ids, copy_mapping=copy_mapping,
            copy_color_limits=copy_color_limits, options=options, annotation_pairs=annotation_pairs)

    def _apply_plot_template(self, template, dataset_ids, *, copy_mapping=False, copy_color_limits=False,
                             options=None, annotation_pairs=None):
        self._finish_history_edit()
        outcomes = []
        active_changed = False
        for dataset_id in dataset_ids:
            session = self.workspace.datasets.get(dataset_id)
            label = f'{session.source_path} — {session.active_graph.name}' if session else dataset_id
            notice = ''
            try:
                if session is None or session.current_cube is None:
                    raise ValueError('The imported dataset is unavailable.')
                before = capture_fields(session, STYLE_FIELDS + ('presentation',))
                candidate = detached_graph_session(session)
                pairs = (annotation_pairs or {}).get(dataset_id, {})
                effective = options
                if options is not None and options.annotation_positions and not pairs:
                    effective = replace(options, annotation_positions=False)
                    notice = 'Annotation positions skipped: no explicit usable pairs selected.'
                result = apply_format(candidate, template, copy_mapping=copy_mapping,
                                      copy_color_limits=copy_color_limits, options=effective, annotation_pairs=pairs)
                # Validate the target's retained text/ticks/data against the actual
                # renderer before committing either of its ordinary records.
                candidate.presentation, _page = prepare_heatmap_page(
                    self.snapshot_plot_dataset(candidate), candidate.presentation, renderer=render_hybrid_svg)
                session.view, session.presentation = candidate.view, candidate.presentation
            except Exception as exc:
                outcomes.append({'dataset_id':dataset_id,'source':label,'success':False,'error':str(exc)})
                continue
            self._record_history(session, 'Paste plot format', before,
                                 capture_fields(session, STYLE_FIELDS + ('presentation',)))
            key = (dataset_id, session.active_graph_id)
            self._invalidate_graph_document(key, kind='presentation')
            page = self.graph_documents.page_for(key)
            if page is not None:
                page.set_presentation(session.presentation)
            active_changed |= dataset_id == self.workspace.active_dataset_id
            try:
                self._save_plot_dataset(session)
            except (ValueError, OSError, TypeError) as exc:
                outcomes.append({'dataset_id':dataset_id,'source':label,'success':False,
                                 'changed_fields':result.changed_fields,'notice':notice,'error':f'Applied; sidecar save failed: {exc}'})
            else:
                outcomes.append({'dataset_id':dataset_id,'source':label,'success':True,
                                 'changed_fields':result.changed_fields,'notice':notice,'error':''})
        if active_changed:
            self._project_plot_dataset(self.workspace.active)
        self._schedule_plot_page_render()
        failures = sum(not item['success'] for item in outcomes)
        self.statusBar().showMessage(f'Format applied: {len(outcomes)-failures}/{len(outcomes)} datasets; {failures} failed')
        skipped = [item['source'] for item in outcomes if item.get('notice')]
        if skipped:
            self.plot_page.status_label.setText('Annotation positions skipped (no explicit usable pairs): ' + '; '.join(skipped))
        return outcomes

    def _on_save_plot_format(self):
        name, accepted = QInputDialog.getText(self, 'Save named plot format', 'Format name:',
                                             text=self.plot_page.format_combo.currentText())
        if accepted:
            try:
                self.save_plot_format(name)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self._show_error(f'Cannot save format: {exc}')

    def _report_format_outcomes(self, outcomes):
        failed = [item for item in outcomes if not item['success']]
        if failed:
            self._show_error('\n\n'.join(f"{item['source']}: {item['error']}" for item in failed))

    def _on_load_plot_format(self, name):
        try:
            self._show_named_format_dialog(name, current_only=True)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            self._show_error(f'Cannot load format: {exc}')

    def _on_delete_plot_format(self, name):
        try:
            self.delete_plot_format(name)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            self._show_error(f'Cannot delete format: {exc}')

    def _on_apply_plot_format(self, name):
        try:
            self._show_named_format_dialog(name, current_only=False)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            self._show_error(f'Cannot apply format: {exc}')

    def _show_named_format_dialog(self, name, *, current_only):
        dialog = self.create_format_dialog(name, current_only=current_only)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._report_format_outcomes(self.apply_plot_format(name, dialog.selected_ids(),
                    options=dialog.options(), annotation_pairs=dialog.annotation_pairs()))
        finally:
            dialog.deleteLater()

    def _plot_dataset_metadata(self, session, snapshot):
        cube = graph_cube(session)
        source = str(session.source_path or cube.source_path or '')
        return {'source_path':source, 'source_stem':Path(source).stem if source else 'heatmap',
                'graph_id':session.active_graph_id, 'graph_name':session.active_graph.name,
                'source_mode':session.active_graph.source_mode, 'gate_indices':session.active_graph.gate_indices,
                'x_limits':list(snapshot.x_limits), 'y_limits':list(snapshot.y_limits),
                'color_limits':list(snapshot.color_limits), 'color_scale_mode':session.view.color_scale_mode,
                'color_region_boundaries':list(session.view.color_region_boundaries),
                'color_region_limits':[list(region.color_limits) for region in snapshot.regions],
                'x_label':snapshot.x_label, 'y_label':snapshot.y_label,
                'signal_label':snapshot.signal_label, 'plot_title':snapshot.title, 'colormap':snapshot.cmap,
                'axis_original_type':cube.x_original_type, 'axis_original_label':cube.x_original_label,
                'axis_override':cube.x_override, 'axis_detection_source':cube.x_detection_source,
                'axis_detection_note':cube.x_detection_note, 'axis_is_ambiguous':cube.x_is_ambiguous,
                'photon_energy_note':cube.photon_energy_note,
                'processing':'energy_second_derivative' if session.active_graph.source_mode == 'processed' and session.applied.mode=='energy_second_derivative' else 'raw',
                'processing_smooth':session.applied.smooth, 'processing_window_length':session.applied.window_length,
                'norm':snapshot.norm.to_dict(), 'presentation':session.presentation.to_dict()}

    def start_plot_batch(self, dataset_ids=None, *, graph_targets=None, formats=('svg','png'), common_folder=None):
        """Freeze selected graph pairs; dataset-ID callers retain their active-graph behavior."""
        if self._plot_batch is not None and not self._plot_batch.done:
            raise ValueError('A plot batch is already running.')
        if graph_targets is not None and dataset_ids is not None:
            raise ValueError('Choose dataset IDs or graph targets, not both.')
        if graph_targets is None:
            targets = [(dataset_id, session.active_graph_id if
                        (session := self.workspace.datasets.get(dataset_id)) else 'default')
                       for dataset_id in (dataset_ids or ())]
        else:
            targets = list(graph_targets)
            if any(not isinstance(target, (tuple, list)) or len(target) != 2 or
                   any(not isinstance(value, str) or not value.strip() for value in target) for target in targets):
                raise ValueError('Graph targets require dataset and graph IDs.')
        if not targets:
            raise ValueError('Choose at least one imported graph.')
        if common_folder is not None and (not str(common_folder).strip() or not Path(common_folder).is_dir()):
            raise ValueError('Choose an existing common output folder.')
        SerialPlotBatch((),formats=formats)  # Validate options before preparing jobs or saving sidecars.
        self._plot_render_timer.stop()
        active = self.workspace.active
        editor_error = ''
        if active is not None and active.current_cube is not None:
            try:
                self._finish_graph_editors(capture_science=False)
            except Exception as exc:
                editor_error = str(exc)
        labels = dataset_display_labels(self.workspace.datasets.values())
        jobs, entries = [], []
        selected_sessions = {}
        for dataset_id, graph_id in targets:
            session = self.workspace.datasets.get(dataset_id)
            graph = session.graphs.get(graph_id) if session else None
            label = f'{labels.get(dataset_id, "Unavailable DAT")} — {graph.name if graph else "Unavailable graph"}'
            basename = Path(common_folder or self._output_folder(session)) / (
                graph_export_stem(session.source_path.stem if session.source_path else 'heatmap', graph_id, graph.name)
                if graph else 'unavailable-graph')
            try:
                if session is None:
                    raise ValueError('The imported dataset is unavailable.')
                selected_sessions[dataset_id] = session
                if graph is None:
                    raise ValueError('The selected graph is unavailable.')
                if not graph.published:
                    raise ValueError('The selected graph is unpublished. Open it before exporting.')
                if editor_error and active is session and graph_id == active.active_graph_id:
                    raise ValueError(editor_error)
                target_page = self.graph_documents.page_for((dataset_id, graph_id))
                if target_page is not None and target_page.has_pending_project_edits():
                    raise ValueError('Finish or correct the selected graph page editor before exporting.')
                candidate = detached_graph_session(replace(session, active_graph_id=graph_id))
                snapshot = self.snapshot_plot_dataset(candidate)
                metadata = {'SpectraAnalyzer':json.dumps(self._plot_dataset_metadata(candidate,snapshot),
                                                         ensure_ascii=False,sort_keys=True)}
                jobs.append(BatchExportJob(snapshot,candidate.presentation,basename,metadata,target_label=label))
                entries.append(len(jobs)-1)
            except Exception as exc:
                entries.append(BatchFileResult(dataset_id,basename,error=str(exc),error_stage='prepare',
                    graph_id=graph_id,target_label=label))
        # Save the actual collection once per DAT, never the detached selected owner.
        # A sidecar failure cannot suppress exports of other valid graphs in that DAT.
        self._plot_batch_save_notices = {}
        for dataset_id, session in selected_sessions.items():
            try:
                self._save_plot_dataset(session)
            except Exception as exc:
                self._plot_batch_save_notices[dataset_id] = f'{labels[dataset_id]}: Plot sidecar not saved: {exc}'
        self._plot_batch_entries = entries
        self._plot_batch_basenames = tuple(job.basename for job in jobs)
        self._plot_batch_labels = tuple(job.target_label for job in jobs)
        self._plot_batch = SerialPlotBatch(jobs,formats=formats, prepare_page=self._prepare_batch_page)
        self.plot_page.batch_export_btn.setEnabled(False)
        self._refresh_graph_commands()
        self.plot_page.batch_progress.setVisible(True)
        self.plot_page.batch_progress.setRange(0,len(entries))
        self.plot_page.batch_cancel_btn.setVisible(True)
        self._update_plot_batch_report()
        if self._plot_batch.done:
            self._finish_plot_batch()
        else:
            self._plot_batch_timer.start()

    def _prepare_batch_page(self, job):
        """Resolve a frozen export job without replacing a newer graph or draft."""
        presentation, page = prepare_heatmap_page(job.snapshot, job.presentation, renderer=render_hybrid_svg)
        if presentation == job.presentation:
            return job, page
        metadata = dict(job.metadata)
        details = json.loads(metadata['SpectraAnalyzer'])
        details['presentation'] = presentation.to_dict()
        metadata['SpectraAnalyzer'] = json.dumps(details, ensure_ascii=False, sort_keys=True)
        key = (job.snapshot.dataset_id, job.snapshot.graph_id)
        session = self.workspace.datasets.get(key[0])
        graph = session.graphs.get(key[1]) if session is not None else None
        target = self.graph_documents.page_for(key)
        snapshot = job.snapshot
        if (graph is not None and graph.presentation == job.presentation
                and snapshot.owner_token == self._plot_owner_token(key)
                and (target is None or not target.has_pending_project_edits())):
            self._adopt_prepared_page_size(session, key, presentation)
            owner = self._plot_owner_token(key)
            snapshot = replace(snapshot, presentation_revision=owner[4])
            page = replace(page, owner_token=owner)
            if target is not None:
                target.preview.set_geometry(snapshot, presentation)
                target.commit_rendered_page(page)
            try:
                self._save_plot_dataset(session)
            except Exception as exc:
                self._plot_batch_save_notices[key[0]] = f'{job.target_label}: Plot sidecar not saved: {exc}'
        return replace(job, snapshot=snapshot, presentation=presentation, metadata=metadata), page

    def _update_plot_batch_report(self):
        done = self._plot_batch.summary.results
        results = tuple(entry if isinstance(entry,BatchFileResult) else done[entry]
                        for entry in self._plot_batch_entries
                        if isinstance(entry,BatchFileResult) or entry < len(done))
        self.plot_batch_summary = BatchSummary(len(self._plot_batch_entries),results)
        lines = []
        for result in results:
            if result.canceled:
                detail = 'Canceled before export'
            elif result.outputs:
                detail = '; '.join(f'{item.format.upper()}: {item.path}' if item.success else
                                   f'{item.format.upper()} failed ({item.error_stage}): {item.error}'
                                   for item in result.outputs)
            else:
                formats = ' + '.join(kind.upper() for kind in self._plot_batch.formats)
                detail = f'{formats} {result.error_stage} failed: {result.error}'
            lines.append(f'{result.target_label} [{result.basename}]: {detail}')
        for entry in self._plot_batch_entries:
            if isinstance(entry,int) and entry>=len(done):
                lines.append(f'{self._plot_batch_labels[entry]} [{self._plot_batch_basenames[entry]}]: Pending export')
        lines.extend(self._plot_batch_save_notices.values())
        self.plot_batch_report = '\n'.join(lines)
        summary = self.plot_batch_summary
        self.plot_page.batch_progress.setValue(summary.completed+summary.canceled)
        warning_count = len(self._plot_batch_save_notices)
        warning_status = f' · {warning_count} settings warning' + ('s' if warning_count != 1 else '') if warning_count else ''
        self.plot_page.batch_status.setText(f'{summary.completed}/{summary.total} attempted · '
                                           f'{summary.failed} failed · {summary.canceled} canceled{warning_status}')
        self.plot_page.batch_status.setToolTip(self.plot_batch_report)
        self.plot_page.batch_results_btn.setEnabled(bool(self.plot_batch_report))

    def _step_plot_batch(self):
        self._plot_batch_timer.stop()
        if self._plot_batch is None:
            return
        self._plot_batch.step()
        self._update_plot_batch_report()
        if self._plot_batch.done:
            self._finish_plot_batch()
        else:
            self._plot_batch_timer.start()

    def cancel_plot_batch(self):
        self._plot_batch_timer.stop()
        if self._plot_batch is not None:
            self._plot_batch.cancel()
            self._update_plot_batch_report()
            self._finish_plot_batch()

    def _finish_plot_batch(self):
        self.plot_page.batch_export_btn.setEnabled(True)
        self._refresh_graph_commands()
        self.plot_page.batch_cancel_btn.setVisible(False)
        self.statusBar().showMessage(self.plot_page.batch_status.text())
        if self.plot_page.rendered_page is None and self.current_cube is not None:
            self._schedule_plot_page_render()

    def _on_batch_export_plot(self):
        dialog = BatchExportDialog(self.workspace.datasets.values(),self.workspace.active_dataset_id,self)
        try:
            if self._project_path is not None:
                dialog.common_folder.setText('Use one output folder (default: project folder for saved datasets)')
                dialog.folder_edit.setText(str(self._output_folder()))
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.start_plot_batch(graph_targets=dialog.selected_graph_targets(),
                    formats=dialog.formats(),common_folder=dialog.destination())
        except (ValueError, OSError, TypeError) as exc:
            self._show_error(f'Cannot start batch export: {exc}')
        finally:
            dialog.deleteLater()

    def create_plot_batch_results_dialog(self):
        dialog=QDialog(self)
        dialog.setWindowTitle('Batch export results')
        dialog.resize(760,480)
        layout=QVBoxLayout(dialog)
        report=QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(self.plot_batch_report)
        report.setAccessibleName('Per-DAT, per-graph and per-format batch export outcomes')
        dialog.report_text=report
        layout.addWidget(report)
        buttons=QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def _on_plot_batch_results(self):
        dialog=self.create_plot_batch_results_dialog()
        dialog.exec()
        dialog.deleteLater()

    def _on_color_mapping_changed(self, *_args):
        with self._edit_transaction('Color mapping', ('view.norm',) + COLOR_FIELDS):
            session = self.workspace.active or self._unregistered_session
            session.view.norm = NormSpec(str(self.mapping_combo.currentData()), self.linthresh_spin.value())
            session.view.last_valid_region_color_limits = []
            self.linthresh_spin.setEnabled(session.view.norm.kind == 'symlog')
            self._invalidate_plot_page()
            if self.current_cube is None:
                return
            try:
                self._update_auto_color_limits_from_visible_view(use_spin_values=True)
                self._plot_cube()
            except ValueError as exc:
                self._on_plot_presentation_error(str(exc))
                self.statusBar().showMessage(str(exc))

    def _current_norm_spec(self):
        return (self.workspace.active or self._unregistered_session).view.norm

    def _update_mapping_status(self):
        session = self.workspace.active or self._unregistered_session
        cube, view = graph_cube(session), session.view
        if not hasattr(self, 'mapping_status_label'):
            return
        self.mapping_status_label.setVisible(view.norm.kind == 'log' and cube is not None)
        if view.norm.kind != 'log' or cube is None:
            return
        xlo, xhi = sorted(view.x_limits)
        ylo, yhi = sorted(view.y_limits)
        rows = (cube.gate >= ylo) & (cube.gate <= yhi)
        columns = (cube.energy >= xlo) & (cube.energy <= xhi)
        values = cube.z[np.ix_(rows, columns)]
        count = int(np.count_nonzero(np.isfinite(values) & (values <= 0)))
        self.mapping_status_label.setText(f'Log: {count} visible nonpositive cells shown gray.')

    def _display_mapping_error(self, message):
        self._invalidate_plot_page(schedule=False)
        self.plot_page.status_label.setText(f'Cannot prepare page: {message}')
        self.statusBar().showMessage(message)

    def _show_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Error", msg)

    def _disconnect_lasso_selector(self) -> None:
        if self._lasso_selector is None:
            return
        try:
            self._lasso_selector.disconnect_events()
        except Exception:
            pass
        self._lasso_selector = None

    def _connect_lasso_selector(self) -> None:
        self._disconnect_lasso_selector()
        if not self._lasso_mode or self._heat_ax is None:
            return
        try:
            self._lasso_selector = LassoSelector(
                self._heat_ax,
                onselect=self._on_lasso_complete,
                useblit=False,
                button=1,
                props={
                    "color": "#f8ff3d",
                    "linewidth": 2.8,
                    "alpha": 0.98,
                    "linestyle": "-",
                },
            )
        except Exception as exc:
            self.statusBar().showMessage(f"Lasso init failed: {exc}")
            self._lasso_mode = False
            self.lasso_helper_label.setVisible(False)
            self.lasso_mode_btn.blockSignals(True)
            self.lasso_mode_btn.setChecked(False)
            self.lasso_mode_btn.blockSignals(False)
            self._lasso_selector = None

    def _set_canvas_cursor_for_event(self, event: Any | None = None) -> None:
        if not self._lasso_mode:
            self.canvas.unsetCursor()
            return
        if event is not None and event.inaxes is self._heat_ax:
            self.canvas.setCursor(Qt.CrossCursor)
            return
        self.canvas.unsetCursor()

    def _clear_lasso_selection_visual(self) -> None:
        if self._lasso_selection_patch is not None:
            try:
                self._lasso_selection_patch.remove()
            except Exception:
                pass
            self._lasso_selection_patch = None

    def _update_lasso_actions(self) -> None:
        has_sel = bool(self._lasso_selected_peak_ids)
        self.delete_lasso_btn.setEnabled(has_sel)
        self.clear_lasso_btn.setEnabled(has_sel)

    def _selected_x_axis_override(self) -> str | None:
        return self.x_axis_override_combo.currentData()

    def _refresh_axis_status(self) -> None:
        if self.current_cube is None:
            self.axis_status_label.setText("X-axis: no file loaded.")
            self.fit_axis_label.setText("Analysis axis: no file loaded.")
            return

        cube = self.current_cube
        override = self._selected_x_axis_override()
        override_txt = "Auto Detect" if override is None else self.x_axis_override_combo.currentText()
        ambiguity = "Ambiguous auto-detect. Manual override is recommended." if cube.x_is_ambiguous else "Auto-detect is confident."
        self.axis_status_label.setText(
            f"Detected: {cube.x_original_label} via {cube.x_detection_source}. {cube.x_detection_note}\n"
            f"Conversion: {cube.photon_energy_note}\n"
            f"Override: {override_txt}. {ambiguity}"
        )
        self.fit_axis_label.setText(
            f"Analysis axis: {cube.energy_label}. Original axis preserved as {cube.x_original_label}."
        )

    def _sync_batch_peak_coords(self, *, normalize=True) -> None:
        self._invalidate_peak_documents()
        if self.current_cube is None or not self.batch_peak_map:
            self.batch_peak_coords = None
            self.batch_peak_records = []
            self._lasso_selected_peak_ids = []
            self._update_lasso_actions()
            return
        x = self.current_cube.energy
        gates = self.current_cube.gate
        px: list[float] = []
        py: list[float] = []
        records: list[tuple[int, int]] = []
        clean_map: dict[int, np.ndarray] = {}
        for gi, indices in self.batch_peak_map.items():
            if gi < 0 or gi >= gates.size:
                continue
            arr = np.asarray(indices, dtype=int)
            arr = arr[(arr >= 0) & (arr < x.size)]
            if arr.size == 0:
                continue
            arr = np.asarray(np.unique(arr), dtype=int)
            clean_map[int(gi)] = arr
            px.extend(float(v) for v in x[arr])
            py.extend(float(gates[gi]) for _ in range(int(arr.size)))
            records.extend((int(gi), int(idx)) for idx in arr)
        if normalize:
            self.batch_peak_map = clean_map
        self.batch_peak_records = records
        valid_ids = set(records)
        self._lasso_selected_peak_ids = [pid for pid in self._lasso_selected_peak_ids if pid in valid_ids]
        self._update_lasso_actions()
        if px:
            self.batch_peak_coords = (np.asarray(px, dtype=float), np.asarray(py, dtype=float))
        else:
            self.batch_peak_coords = None

    def _sync_batch_fit_coords(self, *, normalize=True) -> None:
        if self.current_cube is None or not self.batch_fit_map:
            self.batch_fit_coords = None
            return
        x = self.current_cube.energy
        gates = self.current_cube.gate
        px: list[float] = []
        py: list[float] = []
        clean_map: dict[int, np.ndarray] = {}
        for gi, centers in self.batch_fit_map.items():
            if gi < 0 or gi >= gates.size:
                continue
            arr = np.asarray(centers, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size == 0:
                continue
            arr = np.asarray(np.unique(arr), dtype=float)
            clean_map[int(gi)] = arr
            px.extend(float(v) for v in arr)
            py.extend(float(gates[gi]) for _ in range(int(arr.size)))
        if normalize:
            self.batch_fit_map = clean_map
        self.batch_fit_coords = (
            (np.asarray(px, dtype=float), np.asarray(py, dtype=float)) if px else None
        )

    @staticmethod
    def _ordered_pair(a: float, b: float) -> tuple[float, float]:
        lo, hi = (float(a), float(b)) if a <= b else (float(b), float(a))
        if lo == hi:
            pad = max(abs(lo) * 1e-9, 1e-9)
            lo -= pad
            hi += pad
        return lo, hi

    @staticmethod
    def _auto_spectrum_ylim(x: np.ndarray, y: np.ndarray, xlim: tuple[float, float]) -> tuple[float, float]:
        xmin, xmax = xlim
        mask = np.isfinite(x) & np.isfinite(y) & (x >= xmin) & (x <= xmax)
        if not np.any(mask):
            mask = np.isfinite(y)
        vals = np.asarray(y[mask], dtype=float)
        if vals.size == 0:
            return (-1.0, 1.0)
        lo = float(np.nanmin(vals))
        hi = float(np.nanmax(vals))
        if lo == hi:
            pad = max(abs(lo) * 0.05, 1.0)
            return lo - pad, hi + pad
        pad = max((hi - lo) * 0.08, 1e-12)
        return lo - pad, hi + pad

    def _fit_range_bounds(self) -> tuple[float, float]:
        a = float(self.fit_xmin_spin.value())
        b = float(self.fit_xmax_spin.value())
        return (a, b) if a <= b else (b, a)

    def _peak_search_bounds(self) -> tuple[float, float]:
        a = float(self.xmin_spin.value())
        b = float(self.xmax_spin.value())
        return (a, b) if a <= b else (b, a)

    def _fit_mask(self, x: np.ndarray) -> np.ndarray:
        xmin, xmax = self._fit_range_bounds()
        return np.asarray((x >= xmin) & (x <= xmax) & np.isfinite(x), dtype=bool)

    def _peak_search_mask(self, x: np.ndarray) -> np.ndarray:
        xmin, xmax = self._peak_search_bounds()
        return np.asarray((x >= xmin) & (x <= xmax) & np.isfinite(x), dtype=bool)

    def _analysis_xy(
        self,
        x: np.ndarray,
        y: np.ndarray,
        *,
        smooth: bool = False,
        smooth_window: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        mask = self._fit_mask(x)
        xr = np.asarray(x[mask], dtype=float)
        yr = np.asarray(y[mask], dtype=float)
        if xr.size < 5:
            raise ValueError("Fit range is too narrow. Need at least 5 points.")
        if smooth:
            requested = int(self.fit_smooth_win_spin.value()) if smooth_window is None else int(smooth_window)
            win = _clamp_sg_window(xr.size, requested)
            if win >= 3 and xr.size > win:
                yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")
        return xr, yr, mask

    def _nearest_spectrum(self, gate_value: float) -> tuple[float, np.ndarray]:
        if self.current_cube is None:
            raise ValueError("No data loaded.")
        g = self.current_cube.gate
        idx = int(np.argmin(np.abs(g - float(gate_value))))
        return float(g[idx]), np.asarray(self.current_cube.z[idx, :], dtype=float)

    def _current_gate_index(self) -> int:
        if self.current_cube is None:
            return 0
        g = self.current_cube.gate
        return int(np.argmin(np.abs(g - float(self.current_gate))))

    def _current_trace_peak_indices(self) -> np.ndarray:
        if self.current_cube is None or self._peak_gate_idx is None:
            return np.asarray([], dtype=int)
        if int(self._peak_gate_idx) != int(self._current_gate_index()):
            return np.asarray([], dtype=int)
        x = self.current_cube.energy
        return np.asarray(self.peak_indices[(self.peak_indices >= 0) & (self.peak_indices < x.size)], dtype=int)

    def _current_gate_batch_peak_indices(self) -> np.ndarray:
        if self.current_cube is None:
            return np.asarray([], dtype=int)
        x = self.current_cube.energy
        gi = self._current_gate_index()
        peaks_idx = np.asarray(self.batch_peak_map.get(gi, []), dtype=int)
        return np.asarray(peaks_idx[(peaks_idx >= 0) & (peaks_idx < x.size)], dtype=int)

    def _current_fit_centers(self) -> np.ndarray:
        if self.current_cube is None:
            return np.asarray([], dtype=float)
        gate_idx = self._current_gate_index()
        if self.fit_result is not None and self._fit_gate_idx == gate_idx:
            arr = np.asarray(self.fit_result.centers, dtype=float)
        else:
            arr = np.asarray(self.batch_fit_map.get(gate_idx, []), dtype=float)
        return np.asarray(arr[np.isfinite(arr)], dtype=float)

    def _active_fit_result(self) -> FitResult | None:
        gate_idx = self._current_gate_index()
        if self.fit_result is not None and self._fit_gate_idx == gate_idx:
            return self.fit_result
        return self.batch_fit_result_map.get(gate_idx)

    def _active_graph_peaks(self):
        """Only current-Gate retained detected/manual points with finite source pairs."""
        indices = self._current_trace_peak_indices()
        if self.current_cube is None:
            return indices, np.asarray([], dtype=bool)
        gate = self._current_gate_index()
        lookup = self.peak_kind_lookup
        if not indices.size:
            indices = self._current_gate_batch_peak_indices()
            lookup = {int(i): self.batch_peak_kind_lookup.get((gate, int(i)), True) for i in indices}
        finite = np.isfinite(self.current_cube.energy[indices]) & np.isfinite(self.current_cube.z[gate, indices])
        indices = indices[finite]
        return indices, np.asarray([lookup.get(int(i), True) for i in indices], dtype=bool)

    def _fit_result_to_export_row(
        self,
        gate_value: float,
        model: str,
        analysis_axis: str,
        result: FitResult | None,
        *,
        status: str = "ok",
    ) -> dict[str, str]:
        row: dict[str, str] = {
            "gate": f"{gate_value:.10g}",
            "model": model,
            "status": status,
            "analysis_axis": analysis_axis,
        }
        if result is None:
            return row
        row["baseline"] = f"{result.baseline:.10g}"
        row["peak_count"] = str(int(result.centers.size))
        row["r_squared"] = f"{result.r_squared:.10g}"
        row["rmse"] = f"{result.rmse:.10g}"
        for i in range(result.centers.size):
            peak_no = i + 1
            row[f"peak_{peak_no}_center"] = f"{result.centers[i]:.10g}"
            row[f"peak_{peak_no}_width"] = f"{result.widths[i]:.10g}"
            row[f"peak_{peak_no}_amplitude"] = f"{result.amplitudes[i]:.10g}"
        return row

    @staticmethod
    def _export_fieldnames(rows: list[dict[str, str]]) -> list[str]:
        base = ["gate", "model", "status", "analysis_axis", "peak_count", "baseline", "r_squared", "rmse"]
        extra: list[str] = []
        seen = set(base)
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    extra.append(key)
                    seen.add(key)
        return [name for name in base if any(name in row for row in rows)] + extra

    def _upsert_fit_export_row(self, row: dict[str, str]) -> None:
        gate_key = row.get("gate")
        if gate_key is None:
            self._batch_fit_export_rows.append(dict(row))
            return
        for idx, existing in enumerate(self._batch_fit_export_rows):
            if existing.get("gate") == gate_key:
                self._batch_fit_export_rows[idx] = dict(row)
                return
        self._batch_fit_export_rows.append(dict(row))

    def _analysis_axis_step(self) -> float | None:
        if self.current_cube is None:
            return None
        x = np.asarray(self.current_cube.energy, dtype=float)
        if x.size < 2:
            return None
        finite = x[np.isfinite(x)]
        if finite.size < 2:
            return None
        span = float(np.nanmax(finite) - np.nanmin(finite))
        n_points = max(1, int(finite.size))
        if span <= 0:
            return None
        return span / n_points

    def _pts_to_axis_units(self, points: int) -> float | None:
        step = self._analysis_axis_step()
        if step is None:
            return None
        return float(max(0, int(points))) * step

    def _format_pts_conversion(self, points: int) -> str:
        unit = self.current_cube.energy_label if self.current_cube is not None else "x-axis units"
        axis_value = self._pts_to_axis_units(points)
        if axis_value is None:
            return f"{int(points)} pt"
        return f"{int(points)} pt ({axis_value:.6g} {unit})"

    def _update_fit_param_unit_labels(self) -> None:
        if not hasattr(self, "lw_min_value_label"):
            return
        self.lw_min_value_label.setText(self._format_pts_conversion(int(self.lw_min_spin.value())))
        maximum = int(self.lw_max_spin.value())
        self.lw_max_value_label.setText(self._format_pts_conversion(maximum) if maximum else "Auto")
        self.peak_move_value_label.setText(self._format_pts_conversion(int(self.peak_move_limit_spin.value())))
        gaussian = self.fit_model_combo.currentText() == "Gaussian"
        parameter = "sigma" if gaussian else "HWHM gamma"
        model = "Gaussian" if gaussian else "Lorentzian"
        fwhm = "FWHM ≈ 2.35482 × sigma" if gaussian else "FWHM = 2 × gamma"
        row_name = "sigma" if gaussian else "HWHM"
        self.lw_min_name_label.setText(f"Min {row_name}:")
        self.lw_max_name_label.setText(f"Max {row_name}:")
        self.fit_width_help_label.setText(
            f"{model} width is {parameter}; {fwhm}. "
            "Bounds use points, converted to X-axis units."
        )
        unit_note = "The equivalent size in the current analysis-axis units is shown beside the control."
        self.lw_min_spin.setToolTip(
            f"Minimum allowed {model} linewidth parameter ({parameter}) in data points.\n{unit_note}"
        )
        self.lw_max_spin.setToolTip(
            f"Maximum allowed {model} linewidth parameter ({parameter}) in data points.\n"
            "0 = automatic upper bound (about 2 × the fitted X span).\n"
            f"{unit_note}"
        )

    def _current_peak_overlay_rows(self) -> list[tuple[float, float, float]]:
        if self.current_cube is None:
            return []
        cube = self.current_cube
        rows: list[tuple[float, float, float]] = []
        if self.batch_peak_map:
            for gi, idxs in sorted(self.batch_peak_map.items()):
                gate_v = float(cube.gate[gi])
                arr = np.asarray(idxs, dtype=int)
                arr = arr[(arr >= 0) & (arr < cube.energy.size)]
                for idx in arr:
                    rows.append((gate_v, float(cube.energy[idx]), float(cube.z[gi, idx])))
            return rows
        valid = self._current_trace_peak_indices()
        if valid.size == 0:
            return []
        gate_used, _ = self._nearest_spectrum(self.current_gate)
        gi = self._current_gate_index()
        for idx in valid:
            rows.append((gate_used, float(cube.energy[idx]), float(cube.z[gi, idx])))
        return rows

    def _current_peak_overlay_by_gate(self) -> list[tuple[float, list[tuple[float, float, float, str]]]]:
        if self.current_cube is None:
            return []
        cube = self.current_cube
        grouped: list[tuple[float, list[tuple[float, float, float, str]]]] = []
        if self.batch_peak_map:
            for gi, idxs in sorted(self.batch_peak_map.items()):
                gate_v = float(cube.gate[gi])
                arr = np.asarray(idxs, dtype=int)
                arr = arr[(arr >= 0) & (arr < cube.energy.size)]
                # Sort peaks by resolved label so CSV columns align with labels
                sorted_arr = sorted(
                    [int(ei) for ei in arr],
                    key=lambda ei: self._resolved_peak_label(gi, ei),
                )
                peaks: list[tuple[float, float, float, str]] = []
                for idx in sorted_arr:
                    kind_str = "peak" if self.batch_peak_kind_lookup.get((gi, int(idx)), True) else "dip"
                    peaks.append((float(cube.energy[idx]), float(cube.z[gi, idx]), float(cube.x_original[idx]), kind_str))
                if peaks:
                    grouped.append((gate_v, peaks))
            return grouped
        valid = self._current_trace_peak_indices()
        if valid.size == 0:
            return []
        gate_used, _ = self._nearest_spectrum(self.current_gate)
        gi = self._current_gate_index()
        sorted_valid = sorted(
            [int(ei) for ei in valid],
            key=lambda ei: self._resolved_peak_label(gi, ei),
        )
        peaks = []
        for idx in sorted_valid:
            kind_str = "peak" if self.peak_kind_lookup.get(int(idx), True) else "dip"
            peaks.append((float(cube.energy[idx]), float(cube.z[gi, idx]), float(cube.x_original[idx]), kind_str))
        return [(gate_used, peaks)] if peaks else []

    def _sync_current_trace_peaks_to_batch_map(self) -> None:
        if self.current_cube is None or not self.batch_peak_map:
            return
        gate_idx = self._current_gate_index()
        if self._peak_gate_idx != gate_idx:
            return
        valid = self._current_trace_peak_indices()
        if valid.size > 0:
            self.batch_peak_map[gate_idx] = np.asarray(valid, dtype=int)
            for ei in valid:
                self.batch_peak_kind_lookup[(gate_idx, int(ei))] = self.peak_kind_lookup.get(int(ei), True)
        else:
            self.batch_peak_map.pop(gate_idx, None)
        self._sync_batch_peak_coords()

    def _current_second_derivative_trace(
        self, x: np.ndarray, y: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray] | None:
        if not self._d2_radio.isChecked():
            return None
        mask = self._peak_search_mask(x)
        xr = np.asarray(x[mask], dtype=float)
        yr = np.asarray(y[mask], dtype=float)
        if xr.size < 5:
            return None
        win = _clamp_sg_window(xr.size, int(self.d2_sg_win_spin.value()))
        polyorder = min(max(1, int(self.d2_sg_poly_spin.value())), win - 1)
        if win < 3 or polyorder < 1:
            return None
        key = self._second_derivative_key(mask, win, polyorder)
        if self._cached_d2 is not None and getattr(self, '_cached_d2_key', None) == key:
            return self._cached_d2
        d2y = savgol_filter(yr, window_length=win, polyorder=polyorder, deriv=2)
        self._cached_d2 = (xr, np.asarray(d2y, dtype=float))
        self._cached_d2_key = key
        return self._cached_d2

    def _second_derivative_key(self, mask: np.ndarray, window: int, polyorder: int) -> tuple:
        session = self.workspace.active or self._unregistered_session
        return (session.dataset_id, id(self.current_cube), self._data_generation,
                self._current_gate_index(), self._peak_search_bounds(), mask.tobytes(),
                window, polyorder)

    def _peak_mode(self) -> str:
        if self._d2_radio.isChecked():
            return str(self.d2_mode_combo.currentText())
        return str(self.peak_mode_combo.currentText())

    def _refresh_peak_delete_controls(self) -> None:
        n = int(self._current_trace_peak_indices().size)
        self.peak_delete_spin.blockSignals(True)
        self.peak_delete_spin.setRange(1, max(1, n))
        if n > 0:
            self.peak_delete_spin.setValue(min(int(self.peak_delete_spin.value()), n))
        else:
            self.peak_delete_spin.setValue(1)
        self.peak_delete_spin.blockSignals(False)
        self.delete_peak_btn.setEnabled(n > 0)
        self.clear_peaks_btn.setEnabled(n > 0)

    def _selected_peak_array_index(self) -> int | None:
        if self._current_trace_peak_indices().size == 0:
            return None
        n = int(self.peak_delete_spin.value())
        if n < 1 or n > int(self._current_trace_peak_indices().size):
            return None
        return int(n - 1)

    def _sync_gate_controls(self) -> None:
        self._refresh_analysis_context()
        if hasattr(self, 'graph_documents'):
            self._indicate_sheet_gate()
        if self.current_cube is None or self.current_cube.gate.size == 0:
            self.plot_gate_spin.setEnabled(False)
            self.plot_gate_index_spin.setEnabled(False)
            self.plot_prev_gate_btn.setEnabled(False)
            self.plot_next_gate_btn.setEnabled(False)
            self.plot_gate_spin.blockSignals(True)
            self.plot_gate_spin.set_allowed_values(np.asarray([], dtype=float))
            self.plot_gate_spin.blockSignals(False)
            self.plot_gate_index_spin.blockSignals(True)
            self.plot_gate_index_spin.setRange(1, 1)
            self.plot_gate_index_spin.setValue(1)
            self.plot_gate_index_spin.blockSignals(False)
            return
        self.plot_gate_spin.setEnabled(True)
        self.plot_gate_index_spin.setEnabled(True)
        gates = np.asarray(self.current_cube.gate, dtype=float)
        gate_idx = self._current_gate_index()
        gate_value = float(gates[gate_idx])
        self.plot_gate_spin.blockSignals(True)
        self.plot_gate_spin.set_allowed_values(gates)
        self.plot_gate_spin.setValue(gate_value)
        self.plot_gate_spin.blockSignals(False)
        self.plot_gate_index_spin.blockSignals(True)
        self.plot_gate_index_spin.setRange(1, int(gates.size))
        self.plot_gate_index_spin.setValue(gate_idx + 1)
        self.plot_gate_index_spin.blockSignals(False)
        self.plot_prev_gate_btn.setEnabled(gate_idx > 0)
        self.plot_next_gate_btn.setEnabled(gate_idx < int(gates.size) - 1)

    def _set_current_gate_from_index(self, gate_idx: int, *, update_plot: bool = True) -> None:
        if self.current_cube is None or self.current_cube.gate.size == 0:
            return
        gate_idx = int(np.clip(gate_idx, 0, self.current_cube.gate.size - 1))
        same_peaks = (not self.batch_peak_map or (
            self._peak_gate_idx == gate_idx and
            np.array_equal(self.peak_indices, self.batch_peak_map.get(gate_idx, [])) and
            all(self.peak_kind_lookup.get(int(ei), True) ==
                self.batch_peak_kind_lookup.get((gate_idx, int(ei)), True)
                for ei in self.peak_indices)))
        if gate_idx == self._current_gate_index() and same_peaks:
            self._sync_gate_controls()
            self._refresh_fit_readiness()
            return
        self.current_gate = float(self.current_cube.gate[gate_idx])
        self._schedule_project_modified()
        if self.batch_peak_map:
            self.peak_indices = np.asarray(self.batch_peak_map.get(gate_idx, []), dtype=int)
            self._peak_gate_idx = gate_idx
            self.peak_kind_lookup = {
                int(ei): self.batch_peak_kind_lookup.get((gate_idx, int(ei)), True)
                for ei in self.peak_indices
            }
            self._refresh_peak_delete_controls()
        self._sync_gate_controls()
        self._refresh_fit_readiness()
        if update_plot:
            self._update_spectrum_only()

    def _set_current_gate_from_value(self, gate_value: float, *, update_plot: bool = True) -> None:
        if self.current_cube is None or self.current_cube.gate.size == 0:
            return
        gate_idx = int(np.argmin(np.abs(self.current_cube.gate - float(gate_value))))
        self._set_current_gate_from_index(gate_idx, update_plot=update_plot)

    def _update_peak_text(self, gate_used: float, x: np.ndarray, y: np.ndarray, *, title_prefix: str = "Selected") -> None:
        trace_peaks = self._current_trace_peak_indices()
        if trace_peaks.size == 0:
            self.analysis_text.setPlainText(f"{title_prefix}: no peaks.")
            return
        x_name = self.current_cube.energy_label if self.current_cube is not None else "X"
        lines = [f"{title_prefix} peaks @ gate {gate_used:.6g}:"]
        for i, p in enumerate(trace_peaks, start=1):
            lines.append(f"  {i}. {x_name}={x[p]:.6g}, I={y[p]:.6g}")
        self.analysis_text.setPlainText("\n".join(lines))

    # ── CSV seeds helpers ─────────────────────────────────────────────────────

    def _on_seed_source_changed(self, checked: bool) -> None:
        if hasattr(self, "_csv_seeds_row"):
            self._csv_seeds_row.setVisible(self._seeds_from_csv_radio.isChecked())
        self._refresh_fit_readiness()

    def _fit_seed_status(self) -> tuple[np.ndarray, np.ndarray, str, bool]:
        """Return active seeds, in-range seeds, explanation, and detect availability."""
        if self.current_cube is None:
            return np.asarray([], dtype=float), np.asarray([], dtype=float), "Fit preparation: load data first.", False
        seeds = np.asarray(self._get_seeds_for_current_gate(), dtype=float)
        xlo, xhi = self._fit_range_bounds()
        in_range = seeds[(seeds >= xlo) & (seeds <= xhi)] if seeds.size else np.asarray([], dtype=float)
        if self._seeds_from_csv_radio.isChecked():
            if self._csv_seeds_map is None:
                return seeds, in_range, "CSV source selected, but no seeds CSV is loaded.", False
            if seeds.size == 0:
                return seeds, in_range, f"CSV source has no seeds for Current Gate {self.current_gate:.6g}.", False
            if in_range.size == 0:
                return seeds, in_range, (
                    f"CSV seeds for Current Gate are outside fit range "
                    f"({xlo:.6g}–{xhi:.6g})."
                ), False
            return seeds, in_range, f"CSV seeds ready: {in_range.size} in fit range.", False
        if seeds.size == 0:
            return seeds, in_range, (
                f"No Current Gate seeds. Detect peaks in visible X range "
                f"({self._peak_search_bounds()[0]:.6g}–{self._peak_search_bounds()[1]:.6g}) first."
            ), True
        if in_range.size == 0:
            return seeds, in_range, (
                f"Current Gate seeds are outside fit range ({xlo:.6g}–{xhi:.6g}); "
                "edit the fit range or the seeds."
            ), False
        return seeds, in_range, f"Find Peaks seeds ready: {in_range.size} in fit range.", False

    def _refresh_fit_readiness(self) -> None:
        if not hasattr(self, "fit_readiness_label"):
            return
        _seeds, in_range, message, can_detect = self._fit_seed_status()
        error = self._fit_validation_error()
        if can_detect and not error:
            error = self._detection_validation_error()
        busy = self._analysis_request_matches('fit', self._current_gate_index())
        batch_busy = self._analysis_request_matches('batch-fit', None)
        if error:
            message = error
        elif busy:
            message = 'Fitting current gate… Use Cancel operation to stop.'
        if not error and self.current_cube is not None and self.fit_smooth_chk.isChecked():
            points = int(np.count_nonzero(self._fit_mask(self.current_cube.energy)))
            note = self._sg_window_note(self.fit_smooth_win_spin.value(), points)
            if note:
                message += '\n' + note
        self.fit_readiness_label.setText(f"Fit preparation: {message}")
        self.fit_btn.setText('Fitting Current Gate…' if busy else
                             "Detect Then Fit\nCurrent Gate" if can_detect else "Fit Current Gate")
        self.fit_btn.setEnabled(not self._dat_import_pending and not error and not busy
                                and bool(can_detect or in_range.size))
        self.batch_fit_btn.setText('Batch Fitting…' if batch_busy else 'Batch Fit All Gates')
        self.batch_fit_btn.setEnabled(not self._dat_import_pending and self.current_cube is not None
                                      and not self._fit_validation_error() and not batch_busy)
        self._refresh_export_actions()

    @staticmethod
    def _sg_window_error(label: str, requested: int, *, minimum=3,
                         polyorder: int | None = None) -> str:
        if requested < minimum or requested % 2 == 0:
            return f'{label} window must be odd and at least {minimum} points.'
        if polyorder is not None and polyorder >= requested:
            return f'{label} poly order must be below the requested window ({requested} points).'
        return ''

    @staticmethod
    def _sg_window_note(requested: int, points: int, *, polyorder: int | None = None) -> str:
        effective = _clamp_sg_window(points, requested) if points else requested
        if effective >= requested:
            return ''
        note = f'SG window: {requested} → {effective} points (limited by trace).'
        if polyorder is not None:
            note += f' Effective poly {min(polyorder, effective - 1)}.'
        return note

    def _detection_validation_error(self) -> str:
        if self._d2_radio.isChecked():
            return self._sg_window_error('Detection SG', self.d2_sg_win_spin.value(),
                                         polyorder=self.d2_sg_poly_spin.value())
        if self.peak_smooth_chk.isChecked():
            return self._sg_window_error('Detection pre-smooth', self.peak_smooth_win_spin.value())
        return ''

    def _fit_validation_error(self) -> str:
        minimum, maximum = self.lw_min_spin.value(), self.lw_max_spin.value()
        if maximum != 0 and maximum <= minimum:
            return ('Max linewidth must exceed Min linewidth; use 0 for an automatic upper bound '
                    '(about 2 × the fitted X span).')
        if self.fit_smooth_chk.isChecked():
            return self._sg_window_error('Fit pre-smooth', self.fit_smooth_win_spin.value())
        return ''

    def _refresh_analysis_controls(self, *_args) -> None:
        if self._workbench_document() is not None:
            self._workbench_refresh()
            return
        self.peak_smooth_win_spin.setEnabled(self.peak_smooth_chk.isChecked())
        self.fit_smooth_win_spin.setEnabled(self.fit_smooth_chk.isChecked())
        error = self._detection_validation_error()
        note = ''
        if self.current_cube is not None:
            points = int(np.count_nonzero(self._peak_search_mask(self.current_cube.energy)))
            if self._d2_radio.isChecked():
                note = self._sg_window_note(self.d2_sg_win_spin.value(), points,
                                            polyorder=self.d2_sg_poly_spin.value())
            elif self.peak_smooth_chk.isChecked():
                note = self._sg_window_note(self.peak_smooth_win_spin.value(), points)
        self.peak_validation_label.setText(error or note)
        batch_busy = self._analysis_request_matches('batch-find', None)
        self.find_btn.setEnabled(not self._dat_import_pending and self.current_cube is not None and not error)
        self.batch_find_btn.setEnabled(not self._dat_import_pending and self.current_cube is not None
                                       and not error and not batch_busy)
        self.batch_find_btn.setText('Batch Detecting…' if batch_busy else 'Find Peaks — All Gates')
        self._refresh_fit_readiness()

    def _guard_analysis_controls(self, *, fitting=False, detecting=False) -> bool:
        error = self._fit_validation_error() if fitting else ''
        if detecting and not error:
            error = self._detection_validation_error()
        self._refresh_analysis_controls()
        if error:
            self._show_error(error)
            return False
        return True

    def _on_fit_or_detect_current_gate(self) -> None:
        if self._workbench_document() is not None:
            self.workbench_scope.setCurrentIndex(0)
            return self._workbench_fit()
        if self._dat_import_pending:
            return
        _seeds, _in_range, _message, can_detect = self._fit_seed_status()
        if can_detect:
            self._on_detect_then_fit_current_gate()
        else:
            self._on_fit()

    def _on_detect_then_fit_current_gate(self) -> None:
        if self._dat_import_pending:
            return
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        if self._seeds_from_csv_radio.isChecked():
            self._show_error("Detect Then Fit is available only for the Find Peaks source.")
            return
        if not self._guard_analysis_controls(fitting=True, detecting=True):
            return
        seeds, in_range, _message, _can_detect = self._fit_seed_status()
        if seeds.size:
            self._on_fit()
            return
        self._on_find_peaks()
        _seeds, in_range, message, _can_detect = self._fit_seed_status()
        if in_range.size:
            self._on_fit()
        elif not _seeds.size:
            self._show_error(f"No peaks found in visible X range. {message}")

    def _get_seeds_for_current_gate(self) -> np.ndarray:
        """Return energy values (eV) to use as fit seeds for the current gate."""
        if self._seeds_from_csv_radio.isChecked():
            if self._csv_seeds_map is None:
                return np.asarray([], dtype=float)
            gi = self._current_gate_index()
            return np.asarray(self._csv_seeds_map.get(gi, []), dtype=float)
        trace_peaks = self._current_trace_peak_indices()
        if trace_peaks.size == 0 or self.current_cube is None:
            return np.asarray([], dtype=float)
        return np.asarray(self.current_cube.energy[trace_peaks], dtype=float)

    def _get_seeds_for_gate_idx(self, gi: int) -> np.ndarray:
        """Return energy values (eV) to use as fit seeds for gate index gi."""
        if self._seeds_from_csv_radio.isChecked():
            if self._csv_seeds_map is None:
                return np.asarray([], dtype=float)
            return np.asarray(self._csv_seeds_map.get(gi, []), dtype=float)
        peaks_idx = np.asarray(self.batch_peak_map.get(gi, []), dtype=int)
        if peaks_idx.size == 0 or self.current_cube is None:
            return np.asarray([], dtype=float)
        x = self.current_cube.energy
        valid = peaks_idx[(peaks_idx >= 0) & (peaks_idx < x.size)]
        return np.asarray(x[valid], dtype=float)

    @staticmethod
    def _axis_edges(values: np.ndarray) -> np.ndarray:
        v = np.asarray(values, float).ravel()
        if v.size == 1:
            return np.asarray([v[0] - 0.5, v[0] + 0.5], dtype=float)
        mids = 0.5 * (v[:-1] + v[1:])
        left = float(v[0] - 0.5 * (v[1] - v[0]))
        right = float(v[-1] + 0.5 * (v[-1] - v[-2]))
        return np.concatenate(([left], mids, [right]))

    # =========================================================================
    #  File loading & data state
    # =========================================================================

    def _reset_csv_seed_source(self) -> None:
        self._csv_seeds_map = None
        self._csv_seeds_path = ""
        if hasattr(self, "_csv_path_label"):
            self._csv_path_label.setText("(none loaded)")
        if hasattr(self, "_seeds_from_find_peak_radio"):
            self._seeds_from_find_peak_radio.setChecked(True)

    def _on_browse_root(self) -> None:
        if self._dat_import_pending:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose Root Folder", self.current_root or ".")
        if not folder:
            return
        self.current_root = folder
        self.root_edit.setText(folder)
        self.file_list.clear()
        self.folder_combo.clear()
        self.current_folder = ""
        root = Path(folder)
        self._start_background_job(
            lambda context: discover_folders(root, context),
            lambda folders: self._apply_dat_folders(folder, folders), "Finding DAT folders…")

    def _apply_dat_folders(self, folder: str, folders: list[Path]) -> None:
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        for p in folders:
            rel = "." if p == Path(folder) else str(p.relative_to(folder))
            self.folder_combo.addItem(rel, str(p))
        self.folder_combo.blockSignals(False)
        if not folders:
            self.current_folder = ""
            self.file_list.clear()
            self.statusBar().showMessage("No .dat files found under selected root.")
            return
        self.folder_combo.setCurrentIndex(0)
        self._on_folder_changed()

    def _on_folder_changed(self) -> None:
        if self._dat_import_pending:
            return
        folder = self.folder_combo.currentData()
        if not folder:
            return
        self.current_folder = str(folder)
        self.file_list.clear()
        path = Path(folder)
        self._start_background_job(
            lambda context: discover_files(path, context), self._apply_dat_files, "Reading folder…")

    def _apply_dat_files(self, files: list[str]) -> None:
        self.file_list.clear()
        for name in files:
            path = (Path(self.current_folder) / name).resolve()
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self.file_list.addItem(item)
        if files:
            self.file_list.setCurrentRow(0)
            self.statusBar().showMessage(f"Folder ready: {len(files)} dat files.")
        else:
            self.statusBar().showMessage("No dat files in selected process folder.")

    def _on_load_selected_file(self) -> None:
        if self._dat_import_pending:
            return
        item = self.file_list.currentItem()
        if item is None:
            self._show_error("Select one DAT file first.")
            return
        inventory_path = item.data(Qt.UserRole)
        path = Path(inventory_path) if inventory_path else Path(self.current_folder) / item.text().strip()
        self._import_data_files([path])

    def _refresh_imported_datasets(self) -> None:
        if not hasattr(self, 'imported_dataset_list'):
            return
        with QSignalBlocker(self.imported_dataset_list):
            self.imported_dataset_list.clear()
            sessions = list(self.workspace.datasets.values())
            labels = dataset_display_labels(sessions)
            for session in sessions:
                path = session.source_path
                label = labels[session.dataset_id]
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, session.dataset_id)
                item.setToolTip(str(path.resolve()))
                self.imported_dataset_list.addItem(item)
                if session.dataset_id == self.workspace.active_dataset_id:
                    self.imported_dataset_list.setCurrentItem(item)
            self._filter_imported_datasets()
        self._resize_imported_dataset_list(persist=False)
        self._schedule_project_modified()
        if not self._dat_import_pending:
            self.dat_import_status_label.setText(
                f'{len(sessions)} imported dataset(s).' if sessions
                else 'No imported datasets. Import or drop DAT files to begin.')

    def _resize_imported_dataset_list(self, *_args, persist=True) -> None:
        rows = self.imported_dataset_rows_spin.value()
        row_height = self.imported_dataset_list.sizeHintForRow(0)
        row_height = max(row_height, self.imported_dataset_list.fontMetrics().height() + 6)
        margins = self.imported_dataset_list.contentsMargins()
        height = rows * row_height + margins.top() + margins.bottom() + 2 * self.imported_dataset_list.frameWidth()
        self.imported_dataset_list.setFixedHeight(height)
        if persist:
            self._annotation_settings.setValue('datasets/list_rows', rows)

    def _filter_imported_datasets(self) -> None:
        query = self.imported_dataset_search.text().strip().casefold()
        visible = 0
        with QSignalBlocker(self.imported_dataset_list):
            for row in range(self.imported_dataset_list.count()):
                item = self.imported_dataset_list.item(row)
                path = self.workspace.datasets[item.data(Qt.UserRole)].source_path
                matches = not query or query in path.name.casefold() or query in str(path.parent).casefold()
                item.setHidden(not matches)
                visible += matches
        total = self.imported_dataset_list.count()
        count = f'{visible} / {total} shown' if query else str(total)
        self.imported_dataset_label.setText(f'Imported datasets ({count})')
        self.imported_dataset_filter_status_label.setVisible(bool(total and not visible))

    def _on_imported_dataset_changed(self, item, _previous) -> None:
        if self._dat_import_pending or item is None:
            return
        dataset_id = item.data(Qt.UserRole)
        if dataset_id != self.workspace.active_dataset_id:
            try:
                self.activate_dataset(dataset_id)
            except ValueError as exc:
                self._refresh_imported_datasets()
                self._show_error(str(exc))

    def _show_dataset_context_menu(self, position) -> None:
        item = self.imported_dataset_list.itemAt(position)
        if item is None:
            return
        dataset_id = item.data(Qt.UserRole)
        if dataset_id not in self.workspace.datasets:
            return
        menu = QMenu(self)
        menu.addAction('Copy Source Path', lambda: self._copy_dataset_source_path(dataset_id))
        menu.addAction('Open Source Folder', lambda: self._open_dataset_folder(dataset_id))
        menu.addAction('Open Export Folder', lambda: self._open_dataset_folder(dataset_id, export=True))
        menu.addSeparator()
        remove = menu.addAction('Remove from Project', lambda: self._on_remove_dataset(dataset_id))
        remove.setEnabled(not self._project_busy())
        menu.exec(self.imported_dataset_list.viewport().mapToGlobal(position))
        menu.deleteLater()

    def _copy_dataset_source_path(self, dataset_id: str) -> None:
        session = self.workspace.datasets.get(dataset_id)
        if session is not None and session.source_path is not None:
            QApplication.clipboard().setText(str(session.source_path))
            self.statusBar().showMessage(f'Copied source path: {session.source_path}')

    def _open_dataset_folder(self, dataset_id: str, *, export: bool = False) -> bool:
        session = self.workspace.datasets.get(dataset_id)
        if session is None or session.source_path is None:
            self._show_error('The selected dataset is no longer available.')
            return False
        folder = self._output_folder(session) if export else session.source_path.parent
        if not folder.is_dir():
            self._show_error(f'The {"export" if export else "source"} folder is unavailable:\n{folder}')
            return False
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
            self._show_error(f'Could not open folder:\n{folder}')
            return False
        return True

    def _on_import_dats(self) -> None:
        self._on_import_data()

    def _set_dat_import_pending(self, pending: bool) -> None:
        """The request owns this lock, independently of retained cancelled threads."""
        controls = (self.import_dats_btn, self.imported_dataset_search, self.imported_dataset_list, self.browse_btn,
                    self.folder_combo, self.file_list, self.load_btn,
                    self.x_axis_override_combo, self._processing_content, self._analysis_tabs)
        self._dat_import_pending = pending
        if pending:
            self._dat_import_enabled_states = {control: control.isEnabled() for control in controls}
            for control in controls:
                control.setEnabled(False)
            self.dat_import_status_label.setText('Preparing data… Dataset switching and analysis are paused.')
            self.import_dats_btn.setAccessibleDescription('Import in progress; use Cancel operation to stop.')
        else:
            for control, enabled in self._dat_import_enabled_states.items():
                control.setEnabled(enabled)
            self._dat_import_enabled_states.clear()
            self.import_dats_btn.setAccessibleDescription('')
            self._refresh_fit_readiness()
            self._refresh_export_actions()
            self._refresh_imported_datasets()
        self._refresh_source_axis_control()
        self._refresh_graph_commands()

    def _import_dat_files(self, paths) -> None:
        """User import entry point: cached paths reuse owners; new paths commit as one batch."""
        if self._dat_import_pending or self._close_pending:
            return
        ordered, seen = [], set()
        for value in paths:
            path = Path(value).resolve()
            key = canonical_path(path)
            if key not in seen:
                ordered.append(path)
                seen.add(key)
        if not ordered:
            return
        defaults = self.snapshot_dataset_defaults()
        override = self._selected_x_axis_override()
        colormaps = self._available_colormaps()
        requests = tuple(LoadRequest(path, override, colormaps) for path in ordered
                         if self.workspace.dataset_for_path(path) is None)
        self._dat_import_token += 1
        token = self._dat_import_token
        self._job_cancel_requested = False
        self._set_dat_import_pending(True)
        self._job_cancel_button.show()
        self.statusBar().showMessage(f'Importing {len(ordered)} DAT file(s)…')

        def work(context):
            batch = prepare_dat_batch(requests, context)
            candidates, errors = [], list(batch.errors)
            for path, prepared in batch.prepared:
                context.report(f'Preparing dataset: {path.name}…')
                try:
                    # This method is pure: no Qt controls or registry access.
                    candidate = self.initialize_dataset(path, prepared, defaults)
                except (OSError, ValueError) as exc:
                    errors.append(DatImportError(path, str(exc)))
                else:
                    candidates.append(candidate)
                context.check_cancelled()
            return candidates, errors

        self._background_job_owner = None
        self._processing_request = None
        self._analysis_request = None
        self._background_jobs.start(
            work, lambda result: self._accept_dat_import(token, ordered, *result),
            on_error=lambda exc: self._dat_import_failed(token, exc),
            on_progress=lambda message: self._dat_import_progress(token, message))

    def _dat_import_is_current(self, token: int) -> bool:
        return self._dat_import_pending and token == self._dat_import_token and not self._close_pending

    def _dat_import_progress(self, token: int, message: str) -> None:
        if self._dat_import_is_current(token):
            self.statusBar().showMessage(message)

    def _dat_import_failed(self, token: int, error: Exception) -> None:
        if not self._dat_import_is_current(token):
            return
        self._set_dat_import_pending(False)
        self._job_cancel_button.hide()
        message = f'Data import failed: {error}'
        self.dat_import_status_label.setText(message)
        self.statusBar().showMessage(message)
        self._show_error(message)

    def _accept_dat_import(self, token: int, ordered, candidates, errors) -> None:
        if not self._dat_import_is_current(token):
            return
        candidates_by_path = {canonical_path(session.source_path): session for session in candidates}
        notices = []
        for path in ordered:
            session = candidates_by_path.get(canonical_path(path))
            if session is not None:
                self.register_dataset(session)
                if session.initialization_notice:
                    notices.append(f'{path}: {session.initialization_notice}')
        # Activation captures the latest outgoing view edits at commit time.
        self._set_dat_import_pending(False)
        first = next((session for path in ordered
                      if (session := self.workspace.dataset_for_path(path)) is not None), None)
        if first is not None:
            # This is the import job's own result callback; its thread remains
            # busy until delivery returns. Capture the outgoing owner here,
            # then use the trusted projection path instead of a user switch.
            self._finish_graph_editors()
            save_notice = self._save_current_plot_state()
            self.current_folder = str(first.source_path.parent)
            self.activate_dataset(first.dataset_id, capture_outgoing=False)
            active_notice = self.statusBar().currentMessage()
            if save_notice:
                active_notice += ' — ' + save_notice
        else:
            active_notice = 'No DAT files imported; current dataset preserved.'
        self._job_cancel_button.hide()
        message = f'Imported {len(candidates)} new dataset(s). {active_notice}'
        if errors:
            failed = '\n'.join(f'{failure.path}: {failure.reason}' for failure in errors)
            message += f' {len(errors)} file(s) failed.'
            self.dat_import_status_label.setText(message + '\n' + failed + ('\n' + '\n'.join(notices) if notices else ''))
            self.statusBar().showMessage(message)
            self._show_error('DAT import completed with file errors:\n' + failed)
        else:
            if notices:
                message += ' ' + ' '.join(notices)
            self.dat_import_status_label.setText(message)
            self.statusBar().showMessage(message)

    def _start_background_job(self, work, apply, description: str, *, on_error=None,
                              owner=None) -> None:
        """Run work(context) off-thread; deliver only the newest result on the GUI thread."""
        if self._close_pending or self._dat_import_pending:
            return
        self._background_job_owner = owner
        if owner != 'processing':
            self._processing_request = None
        if owner != 'analysis':
            self._analysis_request = None
        self._job_cancel_requested = False
        self._job_cancel_button.show()
        self.statusBar().showMessage(description)
        self._background_jobs.start(
            work, apply, on_error=on_error or (lambda exc: self._show_error(str(exc))),
            on_progress=self.statusBar().showMessage)
        self._refresh_analysis_controls()
        self._refresh_processing_controls()
        self._refresh_graph_commands()

    def _cancel_background_job(self) -> None:
        if self._project_io_jobs.busy:
            if self._project_io_finalizing:
                self.statusBar().showMessage('Finishing opened project…')
                return
            self._project_io_cancelled = True
            if not self._project_io_committed:
                self._project_io_completion = None
            self._project_io_cancel_event.set()
            self.statusBar().showMessage('Cancelling project operation…')
            return
        self._workbench_cancel_job()
        self._finish_xy_analysis_jobs(cancelled=True)
        self._pending_data_review = None
        self._background_job_owner = None
        self._processing_request = None
        self._analysis_request = None
        if self._dat_import_pending:
            self._dat_import_token += 1
            self._background_jobs.cancel()
            self._set_dat_import_pending(False)
            self._job_cancel_requested = False
            self._job_cancel_button.hide()
            message = 'Data import cancelled. No new sources were added.'
            self.dat_import_status_label.setText(message)
            self.statusBar().showMessage(message)
            return
        self._background_jobs.cancel()
        self._job_cancel_requested = self._background_jobs.busy
        if self.current_cube is not None:
            with QSignalBlocker(self.x_axis_override_combo):
                self.x_axis_override_combo.setCurrentIndex(
                    self.x_axis_override_combo.findData(self.current_cube.x_override))
        self.statusBar().showMessage(
            "Cancelling; waiting for the current operation…"
            if self._job_cancel_requested else "Operation cancelled.")
        self._refresh_analysis_controls()
        self._refresh_processing_controls()

    def _on_background_idle(self) -> None:
        self._finish_xy_analysis_jobs()
        self._schedule_project_modified()
        self._background_job_owner = None
        self._processing_request = None
        self._analysis_request = None
        self._refresh_analysis_controls()
        self._refresh_processing_controls()
        self._refresh_graph_commands()
        if not self._dat_import_pending:
            self._job_cancel_button.hide()
        if self._job_cancel_requested:
            self._job_cancel_requested = False
            self.statusBar().showMessage("Operation cancelled.")
        if self._close_pending:
            QTimer.singleShot(0, self._retry_pending_close)
        pending, self._pending_data_review = self._pending_data_review, None
        if pending is not None:
            self._review_data_import(*pending)

    def _retry_pending_close(self) -> None:
        if self._close_pending:
            self.close()

    def _load_dat_file_async(self, path: Path) -> None:
        if self._dat_import_pending:
            return
        self._finish_annotation_edit(render=False)
        save_notice = self._save_current_plot_state()
        defaults = self.snapshot_dataset_defaults()
        request = LoadRequest(Path(path), self._selected_x_axis_override(),
                              self._available_colormaps())
        self._start_background_job(
            lambda context: prepare_dat(request, context),
            lambda prepared: self._accept_loaded_dat(path, prepared, save_notice, defaults),
            f"Loading {path.name}…")

    def _accept_loaded_dat(self, path: Path, prepared, save_notice: str,
                           defaults_snapshot: DatasetDefaults | None = None) -> None:
        # Controls remain editable while I/O runs. Commit the outgoing state at
        # replacement time, rather than losing edits made after the request began.
        self._finish_annotation_edit(render=False)
        cube, fingerprint, saved_plot, recall_notice = prepared
        if (self.current_dat_path == path
                and self._loaded_source_fingerprint == fingerprint):
            latest_plot = self._current_plot_state()
            if latest_plot is not None:
                saved_plot, recall_notice = latest_plot, ""
        latest_notice = self._save_current_plot_state()
        notices = " ".join(dict.fromkeys(part for part in (save_notice, latest_notice) if part))
        self._apply_loaded_dat(path, cube, fingerprint, saved_plot, recall_notice, notices, defaults_snapshot)

    # =========================================================================
    #  Current-data processing
    # =========================================================================

    def _remember_fixed_processing(self, *_args) -> None:
        if self._restoring_dataset:
            return
        settings = self._annotation_settings
        state = (self.workspace.active or self._unregistered_session).fixed_recipes['processing']
        state.enabled = self.fix_processing_chk.isChecked()
        settings.setValue("fixed_processing/enabled", self.fix_processing_chk.isChecked())
        if self.fix_processing_chk.isChecked():
            state.recipe = {
                "mode": self.processing_mode_combo.currentData(),
                "smooth": self.processing_smooth_chk.isChecked(),
                "window_length": self.processing_window_spin.value(),
            }
            settings.setValue("fixed_processing/recipe", json.dumps(state.recipe))
        self._sync_annotation_settings()

    def _remember_fixed_regions(self, *_args) -> None:
        if self._restoring_dataset:
            return
        settings = self._annotation_settings
        state = (self.workspace.active or self._unregistered_session).fixed_recipes['regions']
        state.enabled = self.fix_regions_chk.isChecked()
        settings.setValue("fixed_regions/enabled", self.fix_regions_chk.isChecked())
        if self.fix_regions_chk.isChecked():
            state.recipe = {
                "mode": self._color_scale_mode,
            }
            settings.setValue("fixed_regions/recipe", json.dumps(state.recipe))
        self._sync_annotation_settings()

    def _remember_fixed_region_edges(self, *_args) -> None:
        if self._restoring_dataset:
            return
        settings = self._annotation_settings
        state = (self.workspace.active or self._unregistered_session).fixed_recipes['region_edges']
        state.enabled = self.fix_region_edges_chk.isChecked()
        settings.setValue("fixed_region_edges/enabled", self.fix_region_edges_chk.isChecked())
        if self.fix_region_edges_chk.isChecked():
            state.recipe = {
                "boundaries": self._color_region_boundaries,
            }
            settings.setValue("fixed_region_edges/recipe", json.dumps(state.recipe))
        self._sync_annotation_settings()

    def _apply_fixed_region_edges(self) -> None:
        if self.fix_region_edges_chk.isChecked():
            boundaries = self._fixed_recipe("region_edges").get("boundaries", [])
            if len(boundaries) == self._requested_region_count() - 1:
                self._color_region_boundaries = list(boundaries)

    def _fixed_recipe(self, group: str) -> dict:
        if self.workspace.active is not None:
            return deepcopy(self.workspace.active.fixed_recipes[group].recipe)
        return self._default_fixed_recipe(group)

    def _default_fixed_recipe(self, group: str) -> dict:
        try:
            recipe = json.loads(self._annotation_settings.value(
                f"fixed_{group}/recipe", "{}", type=str))
            if not isinstance(recipe, dict):
                return {}
            if group == "processing":
                window = max(5, min(1001, int(recipe.get("window_length", 21))))
                return {
                    "mode": ("energy_second_derivative" if recipe.get("mode") ==
                             "energy_second_derivative" else "original"),
                    "smooth": bool(recipe.get("smooth", True)),
                    "window_length": window if window % 2 else window + 1,
                }
            mode = str(recipe.get("mode", "global"))
            boundaries = [float(value) for value in recipe.get("boundaries", [])]
            if mode not in ("global", "2", "3") or not all(np.isfinite(boundaries)):
                return {}
            return {"mode": mode, "boundaries": boundaries}
        except (ValueError, TypeError, OverflowError):
            return {}

    def _set_processing_controls(self, settings: dict | None = None) -> None:
        settings = settings or {"mode": "original", "smooth": True, "window_length": 21,
                                "original_signal_title": RAW_LABEL}
        mode = settings.get("mode", "original")
        index = self.processing_mode_combo.findData(mode)
        if index < 0:
            index = self.processing_mode_combo.findData("original")
        with QSignalBlocker(self.processing_mode_combo):
            self.processing_mode_combo.setCurrentIndex(index)
        with QSignalBlocker(self.processing_smooth_chk):
            self.processing_smooth_chk.setChecked(bool(settings.get("smooth", True)))
        with QSignalBlocker(self.processing_window_spin):
            self.processing_window_spin.setValue(int(settings.get("window_length", 21)))
        self._processing_smooth = self.processing_smooth_chk.isChecked()
        self._processing_window_length = int(self.processing_window_spin.value())
        self._processing_original_signal_title = (
            str(settings.get("original_signal_title", RAW_LABEL))
            if mode == "energy_second_derivative" else None
        )
        self._refresh_processing_controls()

    def _processing_validation_error(self) -> str:
        if self.processing_mode_combo.currentData() == 'energy_second_derivative':
            return self._sg_window_error('Processing SG', self.processing_window_spin.value(),
                                         minimum=5)
        return ''

    def _refresh_processing_controls(self, *_args) -> None:
        self._refresh_analysis_context()
        if not hasattr(self, 'processing_status_label'):
            return
        self._refresh_source_axis_control()
        derivative = self.processing_mode_combo.currentData() == 'energy_second_derivative'
        self.processing_smooth_chk.setEnabled(derivative)
        self.processing_window_spin.setEnabled(derivative and self.processing_smooth_chk.isChecked())
        applied = 'D²' if self._processing_mode == 'energy_second_derivative' else 'Original'
        message = f'Applied: {applied}'
        if self.current_cube is None:
            message += ' — no data loaded.'
        else:
            pending = (str(self.processing_mode_combo.currentData() or 'original'),
                       self.processing_smooth_chk.isChecked(), self.processing_window_spin.value())
            actual = (self._processing_mode, self._processing_smooth, self._processing_window_length)
            if self._background_job_owner == 'processing' and self._processing_request is not None:
                message += ' | Applying processing…'
            elif pending[0] != actual[0] or derivative and pending != actual:
                recipe = (f'D², SG {pending[2]} points' if pending[1] else 'D², no smoothing') if derivative else 'Original'
                message += f' | Pending: {recipe} — choose Apply processing.'
            error = self._processing_validation_error()
            if error:
                message += f'\n{error}'
            elif derivative and pending[1]:
                base = self._base_cube or self.current_cube
                note = self._sg_window_note(pending[2], base.energy.size)
                if note:
                    message += '\n' + note
        self.processing_status_label.setText(message)
        running = self._background_job_owner == 'processing' and self._processing_request is not None
        self.generate_heatmap_btn.setText('Applying processing…' if running else 'Apply processing')
        self.generate_heatmap_btn.setEnabled(not self._dat_import_pending and self.current_cube is not None
                                             and not self._processing_validation_error() and not running)

    def _clear_processing_analysis_state(self) -> None:
        self._schedule_project_modified()
        self._invalidate_analysis_jobs()
        """Drop analyses whose coordinates or values refer to the prior data."""
        self.peak_indices = np.asarray([], dtype=int)
        self._peak_gate_idx = None
        self.peak_kind_lookup = {}
        self.peak_label_lookup = {}
        self.batch_peak_map = {}
        self.batch_peak_coords = None
        self.batch_peak_records = []
        self.batch_peak_kind_lookup = {}
        self.fit_result = None
        self._fit_gate_idx = None
        self.batch_fit_map = {}
        self.batch_fit_coords = None
        self.batch_fit_result_map = {}
        self._batch_fit_export_rows = []
        self._has_batch_fit_results = False
        self._cached_d2 = None
        self._lasso_selected_peak_ids = []
        self._clear_lasso_selection_visual()
        self._update_lasso_actions()
        self._refresh_peak_delete_controls()

    def _on_processing_mode_changed(self) -> None:
        if self._dat_import_pending:
            return
        self._refresh_processing_controls()

    def _on_generate_heatmap(self) -> None:
        if self._dat_import_pending:
            return
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        self._refresh_processing_controls()
        error = self._processing_validation_error()
        if error:
            self._show_error(error)
            return
        mode = str(self.processing_mode_combo.currentData() or "original")
        if mode == "original":
            self._restore_original_data()
            return
        self._apply_energy_second_derivative(
            smooth=self.processing_smooth_chk.isChecked(),
            window_length=int(self.processing_window_spin.value()),
        )

    def _apply_energy_second_derivative(self, *, smooth: bool, window_length: int) -> None:
        if self._dat_import_pending:
            return
        base = self._base_cube or self.current_cube
        if base is None:
            self._show_error("Load a DAT file first.")
            return
        if base.photon_energy is None:
            self._show_error(
                "Energy 2nd derivative requires genuine photon energy. Set X-axis to energy."
            )
            return
        if type(window_length) is not int or window_length < 5 or window_length % 2 == 0:
            self._show_error("Smoothing window must be an odd integer of at least 5 points.")
            return
        session = self.workspace.active or self._unregistered_session
        recipe = ('energy_second_derivative', bool(smooth), window_length)
        source = (id(base), id(self.current_cube))
        applied = (self._processing_mode, self._processing_smooth,
                   self._processing_window_length)
        if (applied == recipe and self.current_cube is not base
                and self._applied_derivative_sources.get(session.dataset_id) == source):
            self._cancel_processing_job()
            return
        key = (session.dataset_id, id(base), id(self.current_cube), self._data_generation,
               self.current_dat_path, self._loaded_source_fingerprint, recipe)
        if self._processing_request == key and self._background_job_owner == 'processing':
            return

        def current():
            active = self.workspace.active or self._unregistered_session
            return (self._processing_request == key
                    and active is session
                    and (self._base_cube or self.current_cube) is base
                    and id(self.current_cube) == key[2]
                    and self._data_generation == key[3]
                    and self.current_dat_path == key[4]
                    and self._loaded_source_fingerprint == key[5]
                    and (str(self.processing_mode_combo.currentData() or 'original'),
                         self.processing_smooth_chk.isChecked(),
                         int(self.processing_window_spin.value())) == recipe)

        def work(context):
            context.check_cancelled()
            transformed = energy_second_derivative(
                np.asarray(base.photon_energy, dtype=float),
                np.asarray(base.z, dtype=float),
                smooth=bool(smooth),
                window_length=window_length,
                polyorder=3,
                check_cancelled=context.check_cancelled,
            )
            context.check_cancelled()
            return replace(base, z=np.asarray(transformed, dtype=float))

        def apply(cube):
            if not current():
                return
            self._processing_request = None
            self._background_job_owner = None
            self._replace_current_data(cube, mode=recipe[0], smooth=recipe[1],
                                       window_length=recipe[2])
            self._applied_derivative_sources[session.dataset_id] = (id(base), id(cube))

        def failed(exc):
            if current():
                self._processing_request = None
                self._background_job_owner = None
                self._refresh_processing_controls()
                self._show_error(str(exc))

        self._processing_request = key
        self._start_background_job(work, apply, 'Applying processing…',
                                   on_error=failed, owner='processing')

    def _cancel_processing_job(self) -> None:
        if self._background_job_owner == 'processing':
            self._background_jobs.cancel()
            self._background_job_owner = None
        self._processing_request = None
        self._refresh_processing_controls()

    def _restore_original_data(self) -> None:
        if self._dat_import_pending:
            return
        self._cancel_processing_job()
        base = self._base_cube or self.current_cube
        if base is None:
            return
        if self._processing_mode == "original" and self.current_cube is base:
            return
        self._replace_current_data(base, mode="original", smooth=self._processing_smooth,
                                   window_length=self._processing_window_length)

    def _replace_current_data(
        self,
        cube: DataCube,
        *,
        mode: str,
        smooth: bool,
        window_length: int,
    ) -> None:
        self._clear_edit_history(self.workspace.active or self._unregistered_session)
        if self._base_cube is None:
            self._base_cube = self.current_cube
        if self._base_cube is None:
            self._base_cube = cube
        xlim = self._heat_ax.get_xlim() if self._heat_ax is not None else (
            self.xmin_spin.value(), self.xmax_spin.value())
        ylim = self._heat_ax.get_ylim() if self._heat_ax is not None else (
            self.ymin_spin.value(), self.ymax_spin.value())
        base_cube = self._base_cube or self.current_cube or cube
        if float(xlim[0]) == float(xlim[1]):
            xlim = (float(np.nanmin(base_cube.energy)), float(np.nanmax(base_cube.energy)))
        if float(ylim[0]) == float(ylim[1]):
            ylim = (float(np.nanmin(base_cube.gate)), float(np.nanmax(base_cube.gate)))
        self.current_cube = cube
        self._processing_mode = mode
        self._data_generation += 1
        self._processing_smooth = bool(smooth)
        self._processing_window_length = int(window_length)
        session = self.workspace.active or self._unregistered_session
        for graph in session.graphs.values():
            refresh_graph_colors(graph, graph_cube(session, graph))
            if graph.view.signal_title_auto:
                graph.view.annotations['signal_title'] = graph_signal_label(session, graph)
        self._clear_processing_analysis_state()
        self._refresh_auto_signal_title()
        self._last_valid_color_limits = None
        self._last_valid_region_color_limits = []
        for spin, value in ((self.xmin_spin, xlim[0]), (self.xmax_spin, xlim[1]),
                            (self.ymin_spin, ylim[0]), (self.ymax_spin, ylim[1])):
            with QSignalBlocker(spin):
                spin.setValue(float(value))
        self._update_auto_color_limits_from_visible_view(use_spin_values=True)
        self._plot_cube()
        self._heat_ax.set_xlim(xlim)
        self._heat_ax.set_ylim(ylim)
        self._update_auto_color_limits_from_visible_view(xlim=xlim, ylim=ylim)
        self._refresh_axis_status()
        self._refresh_processing_controls()
        self.statusBar().showMessage(
            "Heatmap restored to original data." if mode == "original"
            else 'Applied energy 2nd derivative processing.'
        )
        if self.workspace.active_dataset_id is not None:
            self._sync_result_sheets()

    def _load_dat_file_direct(self, path: Path) -> None:
        """Synchronous internal entry point, sharing GUI application with async loading."""
        if self._dat_import_pending:
            return
        self._background_jobs.cancel()
        self._background_job_owner = None
        self._processing_request = None
        try:
            fingerprint = source_fingerprint(path)
            save_notice = self._save_current_plot_state()
            saved_plot, recall_notice = self._plot_state_store.load(path, fingerprint)
            recipes = ([record['plot'] for record in saved_plot['graphs']] if saved_plot and 'graphs' in saved_plot
                       else [saved_plot] if saved_plot else [])
            if any(self.cmap_combo.findData(canonical_colormap(recipe['colormap'])) < 0 for recipe in recipes):
                saved_plot = None
                recall_notice = "Saved plot settings ignored: the colormap is unavailable."
            override = saved_plot["axis_override"] if saved_plot else self._selected_x_axis_override()
            cube = load_dat_cube(path, x_axis_override=override)
            if source_fingerprint(path) != fingerprint:
                raise ValueError("DAT content changed while loading. Please load it again.")
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._apply_loaded_dat(path, cube, fingerprint, saved_plot, recall_notice, save_notice)

    def _apply_loaded_dat(self, path, cube, fingerprint, saved_plot, recall_notice, save_notice,
                          defaults_snapshot: DatasetDefaults | None = None) -> None:
        defaults = defaults_snapshot or self.snapshot_dataset_defaults()
        self.capture_active_dataset(render=False)
        latest_notice = self._save_current_plot_state()
        existing = self.workspace.dataset_for_path(path)
        session = self.initialize_dataset(
            Path(path), (cube, fingerprint, saved_plot, recall_notice), defaults,
            existing.dataset_id if existing else None)
        self.register_dataset(session, replace_existing=True)
        self.current_folder = str(Path(path).parent)
        self.activate_dataset(session.dataset_id, capture_outgoing=False)
        notice = ' '.join(dict.fromkeys(part for part in
                         (save_notice, latest_notice, session.initialization_notice) if part))
        suffix = f' — {notice}' if notice else (' — saved plot restored' if saved_plot else '')
        self.statusBar().showMessage(f'Loaded: {Path(path).name}{suffix}')

    def _current_plot_state(self) -> dict | None:
        if (self.current_dat_path is None or self.current_cube is None
                or self._loaded_source_fingerprint is None
                or self._heat_ax is None or self._heat_mesh is None):
            return None
        bounds = ("xmin", "xmax", "ymin", "ymax", "vmin", "vmax")
        return {
            "axis_override": self.current_cube.x_override,
            "colormap": self._selected_colormap(),
            "annotations": {**{name: edit.text() for name, edit in self._annotation_edits.items()},
                            "signal_title": self.signal_title_combo.currentText()},
            "fix_annotations": self.fix_annotations_chk.isChecked(),
            "signal_title_auto": self.signal_title_auto_chk.isChecked(),
            "title_settings": self._copy_title_settings(self._title_settings),
            "bounds": {name: getattr(self, f"{name}_spin").value() for name in bounds},
            "locks": {name: getattr(self, f"{name}_lock").isChecked() for name in bounds},
            "x_limits": list(map(float, self._heat_ax.get_xlim())),
            "y_limits": list(map(float, self._heat_ax.get_ylim())),
            "color_limits": list(map(float, self._heat_mesh.get_clim())),
            "color_scale_mode": self._color_scale_mode,
            "color_region_boundaries": [float(value) for value in self._color_region_boundaries],
            'color_region_limits': [list(pair) for pair in self._last_valid_region_color_limits],
            "processing": {
                "mode": self._processing_mode,
                "smooth": bool(self._processing_smooth),
                "window_length": int(self._processing_window_length),
                "original_signal_title": self._processing_original_signal_title
                or RAW_LABEL,
            },
            "gate": float(self.current_gate),
            'norm': self._current_norm_spec().to_dict(),
            'presentation': (self.workspace.active or self._unregistered_session).presentation.to_dict(),
        }

    def _save_current_plot_state(self) -> str:
        if self._workbench_document() is not None:
            return ''  # Analysis progress belongs to Save Project, not DAT sidecars.
        if not (self.workspace.active or self._unregistered_session).sidecar_writes_enabled:
            return ''
        plot = self._current_plot_state()
        if plot is None:
            return ""
        try:
            # Keep the fingerprint of the loaded data, even if its disk file changed.
            session = self.workspace.active or self._unregistered_session
            collection = session.plot_collection()
            active = next(record for record in collection['graphs'] if record['graph_id'] == session.active_graph_id)
            active['plot'] = {key: value for key, value in plot.items()
                              if key not in ('axis_override', 'processing')}
            self._plot_state_store.save(self.current_dat_path, self._loaded_source_fingerprint,
                                        collection)
        except (OSError, ValueError, TypeError) as exc:
            return f"Could not save plot settings: {exc}"
        return ""

    def _restore_plot_controls(self, plot: dict) -> None:
        session = self.workspace.active or self._unregistered_session
        session.view.norm = NormSpec.from_dict(plot.get('norm', {}))
        session.presentation = PlotPresentation.from_dict(plot.get('presentation', {}))
        if self.plot_page.presentation != session.presentation:
            self.plot_page.set_presentation(session.presentation)
        with QSignalBlocker(self.mapping_combo), QSignalBlocker(self.linthresh_spin):
            self.mapping_combo.setCurrentIndex(self.mapping_combo.findData(session.view.norm.kind))
            self.linthresh_spin.setValue(session.view.norm.linthresh)
        self.linthresh_spin.setEnabled(session.view.norm.kind == 'symlog')
        self._title_settings = self._copy_title_settings(plot["title_settings"])
        self._title_source = self.current_cube.source_path or id(self.current_cube)
        self._show_title_settings()
        controls = [(self.x_axis_override_combo, "index", self.x_axis_override_combo.findData(plot["axis_override"])),
                    (self.cmap_combo, "index", self.cmap_combo.findData(canonical_colormap(plot["colormap"]))),
                    (self.signal_title_combo, "text", plot["annotations"]["signal_title"]),
                    (self.signal_title_auto_chk, "checked", plot.get("signal_title_auto", False)),
                    (self.fix_annotations_chk, "checked", plot["fix_annotations"])]
        controls.extend((edit, "edit", plot["annotations"][name])
                        for name, edit in self._annotation_edits.items())
        controls.extend((getattr(self, f"{name}_spin"), "value", value)
                        for name, value in plot["bounds"].items())
        controls.extend((getattr(self, f"{name}_lock"), "checked", value)
                        for name, value in plot["locks"].items())
        setters = {"index": "setCurrentIndex", "text": "setCurrentText", "edit": "setText",
                   "checked": "setChecked", "value": "setValue"}
        for widget, kind, value in controls:
            with QSignalBlocker(widget):
                getattr(widget, setters[kind])(value)
        mode = plot.get("color_scale_mode", "global")
        mode_index = self.color_scale_combo.findData(mode)
        with QSignalBlocker(self.color_scale_combo):
            self.color_scale_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self._color_scale_mode = mode if mode in ("global", "2", "3") else "global"
        self._color_region_boundaries = [float(value) for value in plot.get("color_region_boundaries", [])]
        self._last_valid_region_color_limits = [tuple(pair) for pair in plot.get('color_region_limits', [])]
        self._sync_region_controls()
        self._annotation_source = self.current_cube.source_path or id(self.current_cube)
        gate_idx = int(np.argmin(np.abs(self.current_cube.gate - plot["gate"])))
        self.current_gate = float(self.current_cube.gate[gate_idx])
        self._sync_gate_controls()

    def _restore_effective_plot_limits(self, plot: dict, *, refresh_colors: bool = False) -> None:
        if self._heat_mesh is None:
            return
        # The spin boxes round to four decimals; preserve the actual displayed view.
        previous_syncing = self._view_syncing
        self._view_syncing = True
        try:
            self._heat_ax.set_xlim(plot["x_limits"])
            self._heat_ax.set_ylim(plot["y_limits"])
            if refresh_colors or (self._color_scale_mode in ("2", "3") and not self._last_valid_region_color_limits):
                self._update_auto_color_limits_from_visible_view(use_spin_values=True)
            elif self._color_scale_mode in ('2', '3'):
                self._apply_color_limits_to_mesh()
            else:
                self._heat_mesh.set_norm(build_color_norm(self._current_norm_spec(), plot['color_limits']))
                self._last_valid_color_limits = tuple(plot["color_limits"])
            self._layout_live_plots()
        finally:
            self._view_syncing = previous_syncing
        self._refresh_color_range_status()
        self.canvas.draw_idle()


    def _show_updates(self):
        from .update_ui import UpdateController
        if getattr(self, '_update_controller', None) is None:
            self._update_controller = UpdateController(self)
            self._update_controller.install_requested.connect(self.close)
        self._update_controller.open()

    def closeEvent(self, event) -> None:
        updater = getattr(self, '_update_controller', None)
        if updater is not None and not updater.prepare_close():
            self.statusBar().showMessage('Stopping the update operation before closing…')
            event.ignore()
            return
        self._plot_render_timer.stop()
        if self._project_io_jobs.busy:
            self._project_io_close_pending = True
            self._cancel_background_job()
            event.ignore()
            return
        self.cancel_plot_batch()
        self._plot_render_timer.stop()
        if self._background_jobs.busy:
            self._close_pending = True
            self._cancel_background_job()
            self.statusBar().showMessage("Stopping operation before closing…")
            event.ignore()
            return
        # Consume the retry before asking: Cancel must not leave a later idle
        # signal able to close a window whose user elected to keep editing.
        self._close_pending = False
        approval = self._project_close_approved
        if approval and (approval[0] is not self.workspace or approval[1] != self._project_revision):
            self._project_close_approved = False
        if not self._project_close_approved:
            event.ignore()
            install_request = updater.take_install_request() if updater is not None else None
            def approved():
                if install_request is not None and not updater.arm_install(install_request):
                    return
                self._project_close_approved = (self.workspace, self._project_revision)
                QTimer.singleShot(0, self, self.close)
            self._confirm_project_discard_async(approved)
            return
        self._project_close_approved = False
        notice = self._save_current_plot_state()
        if notice:
            self.statusBar().showMessage(notice)
        for window in tuple(self._plot_comparison_windows):
            window.close()
        super().closeEvent(event)
        if event.isAccepted() and updater is not None:
            try:
                updater.accept_close()
            except Exception as exc:
                event.ignore()
                updater.install_failed(exc)

    def _on_x_axis_override_changed(self) -> None:
        """Synchronous internal axis-change entry point."""
        if not self._source_axis_override_allowed():
            return
        if self._dat_import_pending:
            return
        self._background_jobs.cancel()
        self._background_job_owner = None
        self._processing_request = None
        if self.current_dat_path is None:
            self._refresh_axis_status()
            return
        gate = self.current_gate
        try:
            fingerprint = (source_fingerprint(self.current_dat_path)
                           if self._loaded_source_fingerprint is not None else None)
            cube = load_dat_cube(self.current_dat_path, x_axis_override=self._selected_x_axis_override())
            if fingerprint is not None and source_fingerprint(self.current_dat_path) != fingerprint:
                raise ValueError("DAT content changed while loading. Please load it again.")
        except Exception as exc:
            with QSignalBlocker(self.x_axis_override_combo):
                self.x_axis_override_combo.setCurrentIndex(
                    self.x_axis_override_combo.findData(self.current_cube.x_override))
            self._show_error(str(exc))
            return
        self._apply_axis_override(cube, fingerprint, gate)

    def _on_x_axis_override_async(self) -> None:
        if not self._source_axis_override_allowed():
            return
        if self._dat_import_pending:
            return
        if self.current_dat_path is None:
            self._refresh_axis_status()
            return
        request = LoadRequest(self.current_dat_path, self._selected_x_axis_override(), recall=False)
        gate = self.current_gate
        def failed(exc):
            with QSignalBlocker(self.x_axis_override_combo):
                self.x_axis_override_combo.setCurrentIndex(
                    self.x_axis_override_combo.findData(self.current_cube.x_override))
            self._show_error(str(exc))
        self._start_background_job(
            lambda context: prepare_dat(request, context),
            lambda prepared: self._apply_axis_override(prepared[0], prepared[1], gate),
            "Updating axis interpretation…", on_error=failed)

    def _apply_axis_override(self, cube, fingerprint, gate) -> None:
        session = self.workspace.active or self._unregistered_session
        self._clear_edit_history(session)
        candidate = replace(session, base_cube=cube, current_cube=cube)
        for graph in session.graphs.values():
            selected = graph_cube(candidate, graph)
            view = graph.view
            view.gate = float(cube.gate[int(np.argmin(np.abs(cube.gate - view.gate)))])
            view.x_limits = (float(np.min(cube.energy)), float(np.max(cube.energy)))
            lo, hi = float(np.min(selected.gate)), float(np.max(selected.gate))
            view.y_limits = (lo, hi) if lo != hi else (lo - .5, hi + .5)
            for name, value in zip(('xmin', 'xmax', 'ymin', 'ymax'), (*view.x_limits, *view.y_limits)):
                view.bounds[name] = value
                view.locks[name] = False
            view.color_region_boundaries = []
            refresh_graph_colors(graph, selected, unlock=True)
            if view.signal_title_auto:
                view.annotations['signal_title'] = automatic_signal_label(
                    cube.signal_source_kind, False, cube.signal_kind == 'energy_second_derivative')
            # Custom tick positions from the retired source axis cannot be reused.
            graph.presentation = replace(graph.presentation,
                colorbar=replace(graph.presentation.colorbar, ticks={}))
        self._data_generation += 1
        self._base_cube = cube
        self.current_cube = cube
        self._processing_original_signal_title = None
        self._set_processing_controls()
        self._processing_mode = "original"
        self._loaded_source_fingerprint = fingerprint
        self._annotation_source = self._title_source = cube.source_path or id(cube)
        self._last_valid_color_limits = None
        self._last_valid_region_color_limits = []
        self._clear_processing_analysis_state()
        self._reset_csv_seed_source()
        if cube.gate.size:
            gate_idx = int(np.argmin(np.abs(cube.gate - float(gate))))
            self.current_gate = float(cube.gate[gate_idx])
        self._sync_gate_controls()
        self._refresh_peak_delete_controls()
        self._restoring_dataset = True
        try:
            self._restore_plot_controls(session.view.plot_recipe(cube, session.applied, session.presentation))
        finally:
            self._restoring_dataset = False
        self._set_view_limits_to_auto()
        self._set_fit_range_to_full()
        self._refresh_axis_status()
        self._refresh_fit_range_summary()
        self._refresh_fit_readiness()
        self._update_fit_param_unit_labels()
        self._plot_cube()

        if self.workspace.active_dataset_id is not None:
            self._sync_result_sheets()

    # =========================================================================
    #  View / cursor controls
    # =========================================================================

    def _on_auto_view(self) -> None:
        with self._edit_transaction('Rescale graph', RANGE_FIELDS):
            self._set_view_limits_to_auto()
            self._plot_cube()

    def _selected_colormap(self) -> str:
        return str(self.cmap_combo.currentData() or 'RdBu_r')

    def _available_colormaps(self) -> tuple[str, ...]:
        return tuple(self.cmap_combo.itemData(i) for i in range(self.cmap_combo.count())
                     if self.cmap_combo.itemData(i))

    def _on_colormap_changed(self, *_args) -> None:
        with self._edit_transaction('Colormap', ('view.colormap',)):
            if not self._restoring_dataset:
                self._colormap_manual = True
            if not self._heat_meshes:
                self._plot_cube()
                return
            resolved = resolve_colormap(self._selected_colormap())
            palette = (colormaps[resolved] if isinstance(resolved, str) else resolved).with_extremes(bad=MISSING_COLOR)
            for mesh in self._heat_meshes:
                mesh.set_cmap(palette)
            for bar, mesh in zip(self._heat_colorbars, self._heat_meshes):
                bar.update_normal(mesh)
            self._invalidate_plot_page()
            self.canvas.draw_idle()

    def _lock_user_color_bound(self, spin) -> None:
        with self._edit_transaction('Color range', RANGE_FIELDS):
            if self._restoring_dataset or self._view_syncing or not spin.isEnabled():
                return
            self._view_lock_controls[spin].setChecked(True)
            self._refresh_color_range_status()

    def _on_auto_colors(self) -> None:
        with self._edit_transaction('Auto colors', COLOR_FIELDS):
            for lock in (self.vmin_lock, self.vmax_lock):
                with QSignalBlocker(lock):
                    lock.setChecked(False)
            self._update_auto_color_limits_from_visible_view()
            self._capture_dataset_controls()
            self._refresh_color_range_status()

    def _refresh_color_range_status(self, *, visible_values=None) -> None:
        if not hasattr(self, 'color_range_status'):
            return
        regional = self._color_scale_mode in ('2', '3')
        low, high = self.vmin_lock.isChecked(), self.vmax_lock.isChecked()
        mode = ('Manual' if low and high else 'Partial manual (min)' if low else
                'Partial manual (max)' if high else 'Auto')
        regional_mode = ('Fixed endpoints' if low and high else 'Partial Fixed (min)' if low else
                         'Partial Fixed (max)' if high else 'independent Auto')
        self.color_range_status.setText(
            f'{self._color_scale_mode} regions: {regional_mode}; global bound editors disabled. '
            'Visible data, 0.5–99.5 percentiles for unlocked endpoints.'
            if regional else f'Global: {mode}. Visible data, 0.5–99.5 percentiles for unlocked bounds.')
        if self.current_cube is None:
            self.color_clipping_status.setText('')
            return
        values = (visible_values if visible_values is not None else
                  self._visible_color_values_by_region() if regional else [self._visible_color_values()])
        ranges = self._last_valid_region_color_limits if regional else [self._last_valid_color_limits]
        total = under = over = 0
        for cells, limits in zip(values, ranges):
            if limits is not None:
                total += cells.size
                under += int(np.count_nonzero(cells < limits[0]))
                over += int(np.count_nonzero(cells > limits[1]))
        self.color_clipping_status.setText(
            f'Below: {under} ({100*under/total if total else 0:.2f}%) · Above: {over} ({100*over/total if total else 0:.2f}%) of {total} visible valid cells')

    def _on_view_limit_changed(self, spin: QDoubleSpinBox) -> None:
        if self._view_syncing:
            return
        if spin in (self.vmin_spin, self.vmax_spin):
            self._begin_history_text('Color range', RANGE_FIELDS)
        with self._edit_transaction('View range', RANGE_FIELDS):
            self._apply_view_limit_change(spin)

    def _apply_view_limit_change(self, spin):
        xy_spins = {
            self.xmin_spin, self.xmax_spin, self.ymin_spin, self.ymax_spin,
        }
        if spin in xy_spins:
            self._update_auto_color_limits_from_visible_view(use_spin_values=True)
        else:
            self._last_valid_color_limits = self._ordered_pair(
                float(self.vmin_spin.value()), float(self.vmax_spin.value())
            )
        if self._heat_ax is None:
            self._plot_cube()
            return
        previous_syncing = self._view_syncing
        self._view_syncing = True
        try:
            if spin in xy_spins:
                self._heat_ax.set_xlim(self._ordered_pair(self.xmin_spin.value(), self.xmax_spin.value()))
                self._heat_ax.set_ylim(self._ordered_pair(self.ymin_spin.value(), self.ymax_spin.value()))
                self._update_spectrum_only()
                self._schedule_view_layout()
            else:
                self._apply_color_limits_to_mesh()
        finally:
            self._view_syncing = previous_syncing
        self._invalidate_plot_page()
        self.canvas.draw_idle()

    def _on_view_lock_toggled(self, lock_box: QCheckBox, checked: bool) -> None:
        with self._edit_transaction('Range lock', RANGE_FIELDS):
            self._refresh_color_range_status()
            if checked or self.current_cube is None:
                return
            color_spins = (self.vmin_spin, self.vmax_spin)
            unlocked_spin = next(
                (spin for spin, current_lock in self._view_lock_controls.items() if current_lock is lock_box),
                None,
            )
            if unlocked_spin in color_spins:
                self._update_auto_color_limits_from_visible_view()

    def _on_plot_x_limits_changed(self, axes: Any) -> None:
        if not self._view_callbacks_ready or self._view_syncing or self.current_cube is None:
            return
        if self.toolbar.mode:
            self._begin_history_text('View range', RANGE_FIELDS)
        with self._edit_transaction('View range', RANGE_FIELDS):
            self._sync_plot_x_limits(axes)

    def _sync_plot_x_limits(self, axes):
        self._view_syncing = True
        try:
            xmin, xmax = self._ordered_pair(*axes.get_xlim())
            for spin, value in ((self.xmin_spin, xmin), (self.xmax_spin, xmax)):
                spin.blockSignals(True)
                spin.setValue(float(value))
                spin.blockSignals(False)
            self._update_auto_color_limits_from_visible_view(xlim=(xmin, xmax))
            self._refresh_fit_range_summary()
            self._refresh_fit_readiness()
        finally:
            self._view_syncing = False
        self._invalidate_plot_page()

    def _on_plot_y_limits_changed(self, axes: Any) -> None:
        if not self._view_callbacks_ready or self._view_syncing or self.current_cube is None:
            return
        if self.toolbar.mode:
            self._begin_history_text('View range', RANGE_FIELDS)
        with self._edit_transaction('View range', RANGE_FIELDS):
            self._sync_plot_y_limits(axes)

    def _sync_plot_y_limits(self, axes):
        self._view_syncing = True
        try:
            ymin, ymax = self._ordered_pair(*self._heat_ax.get_ylim())
            for spin, value in ((self.ymin_spin, ymin), (self.ymax_spin, ymax)):
                spin.blockSignals(True)
                spin.setValue(float(value))
                spin.blockSignals(False)
            self._update_auto_color_limits_from_visible_view(ylim=(ymin, ymax))
        finally:
            self._view_syncing = False
        self._invalidate_plot_page()

    def _on_color_scale_changed(self) -> None:
        with self._edit_transaction('Color regions', STYLE_FIELDS):
            self._color_scale_mode = str(self.color_scale_combo.currentData() or "global")
            self._last_valid_region_color_limits = []
            # A user-selected color mode starts independent autoscaling. Projection
            # restores stored regional locks with signals blocked in its own path.
            for lock in (self.vmin_lock, self.vmax_lock):
                with QSignalBlocker(lock):
                    lock.setChecked(False)
            self._apply_fixed_region_edges()
            self._sync_region_controls()
            self._remember_fixed_regions()
            if self.current_cube is not None:
                self._plot_cube()

    def _requested_region_count(self) -> int:
        if self._color_scale_mode not in ("2", "3"):
            return 1
        return int(self._color_scale_mode)

    def _region_layout(self) -> tuple[np.ndarray, list[tuple[int, int]], list[float]]:
        if self.current_cube is None:
            return np.asarray([], dtype=float), [(0, 0)], []
        return region_layout(self.current_cube, (self.workspace.active or self._unregistered_session).view)

    def _sync_region_controls(self) -> None:
        if not hasattr(self, "region_boundary_spins"):
            return
        regional = self._color_scale_mode in ("2", "3")
        for spin in (self.vmin_spin, self.vmax_spin):
            spin.setEnabled(not regional)
        for lock in (self.vmin_lock, self.vmax_lock):
            lock.setEnabled(True)
            lock.setToolTip('Fix this endpoint independently for every regional bar.' if regional else
                            'Fix this global color endpoint.')
        if hasattr(self, 'auto_color_btn'):
            self.auto_color_btn.setEnabled(True)
        edges, slices, boundaries = self._region_layout()
        count = len(slices)
        if regional and len(boundaries) != count - 1:
            self._color_region_boundaries = boundaries
        elif not regional:
            self._color_region_boundaries = []
        else:
            self._color_region_boundaries = boundaries
        for idx, spin in enumerate(self.region_boundary_spins):
            visible = regional and idx < count - 1
            spin.setVisible(visible)
            if visible:
                with QSignalBlocker(spin):
                    spin.setRange(float(edges[1]), float(edges[-2]))
                    spin.setValue(float(self._color_region_boundaries[idx]))
        if hasattr(self, "_region_edges_row"):
            self._region_edges_row.setVisible(regional and count > 1)
        self._refresh_color_range_status()

    def _on_color_region_boundary_changed(self, boundary_idx: int, value: float) -> None:
        with self._edit_transaction('Region boundary', ('view.color_region_boundaries',) + COLOR_FIELDS):
            if self.current_cube is None or self._color_scale_mode not in ("2", "3"):
                return
            edges, slices, boundaries = self._region_layout()
            if boundary_idx >= len(slices) - 1:
                return
            requested = list(boundaries)
            requested[boundary_idx] = float(value)
            internal = np.arange(1, len(edges) - 1, dtype=int)
            indices = [int(np.argmin(np.abs(edges[internal] - item)) + 1) for item in requested]
            for idx in range(len(indices)):
                indices[idx] = max(idx + 1, min(len(edges) - 2 - (len(indices) - idx - 1), indices[idx]))
            for idx in range(1, len(indices)):
                indices[idx] = max(indices[idx], indices[idx - 1] + 1)
            self._color_region_boundaries = [float(edges[index]) for index in indices]
            self._last_valid_region_color_limits = []
            self._sync_region_controls()
            self._remember_fixed_region_edges()
            self._plot_cube()

    def _visible_color_values_by_region(
        self,
        *,
        use_spin_values: bool = False,
        xlim: tuple[float, float] | None = None,
        ylim: tuple[float, float] | None = None,
    ) -> list[np.ndarray]:
        if self.current_cube is None:
            return [np.asarray([], dtype=float)]
        cube = graph_cube(self.workspace.active or self._unregistered_session)
        if xlim is None and (use_spin_values or self._heat_ax is None):
            xlim = (float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        elif xlim is None:
            xlim = self._heat_ax.get_xlim()
        if ylim is None and (use_spin_values or self._heat_ax is None):
            ylim = (float(self.ymin_spin.value()), float(self.ymax_spin.value()))
        elif ylim is None:
            ylim = self._heat_ax.get_ylim()
        xmin, xmax = self._ordered_pair(*xlim)
        ymin, ymax = self._ordered_pair(*ylim)
        xmask = np.isfinite(cube.energy) & (cube.energy >= xmin) & (cube.energy <= xmax)
        ymask = np.isfinite(cube.gate) & (cube.gate >= ymin) & (cube.gate <= ymax)
        _, slices, _ = self._region_layout()
        if not np.any(xmask) or not np.any(ymask):
            return [np.asarray([], dtype=float) for _ in slices]
        out = []
        for start, end in slices:
            columns = np.flatnonzero(xmask)
            columns = columns[(columns >= start) & (columns < end)]
            if columns.size == 0:
                out.append(np.asarray([], dtype=float))
                continue
            values = np.asarray(cube.z[np.ix_(np.flatnonzero(ymask), columns)], dtype=float)
            out.append(valid_color_values(values, self._current_norm_spec()))
        return out

    def _regional_color_ranges(self, values: list[np.ndarray]) -> list[tuple[float, float]]:
        previous = self._last_valid_region_color_limits
        low_locked, high_locked = self.vmin_lock.isChecked(), self.vmax_lock.isChecked()
        ranges: list[tuple[float, float]] = []
        for index, region_values in enumerate(values):
            if low_locked and high_locked and index < len(previous):
                low, high = previous[index]
            elif region_values.size:
                low, high = np.nanpercentile(region_values, [0.5, 99.5])
                low, high = float(low), float(high)
            elif index < len(previous):
                low, high = previous[index]
            else:
                low, high = float(self.vmin_spin.value()), float(self.vmax_spin.value())
            # A copied regional view keeps independent cached endpoints. Global
            # bound editors describe the first bar, never all regional bars.
            if index < len(previous):
                if low_locked:
                    low = previous[index][0]
                if high_locked:
                    high = previous[index][1]
            if low == high:
                pad = self._color_gap(low)
                if self._current_norm_spec().kind == 'log':
                    pad = min(pad, low / 2)
                low, high = low - pad, high + pad
            elif low > high:
                low, high = high, low
            if self._current_norm_spec().kind == 'zero-centered':
                low = min(low, -self._color_gap(high))
                high = max(high, self._color_gap(low))
            ranges.append((low, high))
        return ranges or [(float(self.vmin_spin.value()), float(self.vmax_spin.value()))]

    def _visible_color_values(
        self,
        *,
        use_spin_values: bool = False,
        xlim: tuple[float, float] | None = None,
        ylim: tuple[float, float] | None = None,
    ) -> np.ndarray:
        if self.current_cube is None:
            return np.asarray([], dtype=float)
        cube = graph_cube(self.workspace.active or self._unregistered_session)
        if xlim is None and (use_spin_values or self._heat_ax is None):
            xlim = (float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        elif xlim is None:
            xlim = self._heat_ax.get_xlim()
        if ylim is None and (use_spin_values or self._heat_ax is None):
            ylim = (float(self.ymin_spin.value()), float(self.ymax_spin.value()))
        elif ylim is None:
            ylim = self._heat_ax.get_ylim()
        xmin, xmax = self._ordered_pair(*xlim)
        ymin, ymax = self._ordered_pair(*ylim)
        xmask = np.isfinite(cube.energy) & (cube.energy >= xmin) & (cube.energy <= xmax)
        ymask = np.isfinite(cube.gate) & (cube.gate >= ymin) & (cube.gate <= ymax)
        if not np.any(xmask) or not np.any(ymask):
            return np.asarray([], dtype=float)
        values = np.asarray(cube.z[np.ix_(ymask, xmask)], dtype=float)
        return valid_color_values(values, self._current_norm_spec())

    @staticmethod
    def _color_gap(value: float) -> float:
        return max(abs(float(value)) * 1e-6, 1e-3)

    def _update_auto_color_limits_from_visible_view(
        self,
        *,
        use_spin_values: bool = False,
        xlim: tuple[float, float] | None = None,
        ylim: tuple[float, float] | None = None,
    ) -> None:
        if self._color_scale_mode in ("2", "3"):
            values_by_region = self._visible_color_values_by_region(
                use_spin_values=use_spin_values, xlim=xlim, ylim=ylim)
            self._last_valid_region_color_limits = self._regional_color_ranges(values_by_region)
            self._last_valid_color_limits = self._last_valid_region_color_limits[0]
            self._apply_color_limits_to_mesh(visible_values=values_by_region)
            return
        values = self._visible_color_values(use_spin_values=use_spin_values, xlim=xlim, ylim=ylim)
        low_locked = self._view_lock_controls.get(self.vmin_spin)
        high_locked = self._view_lock_controls.get(self.vmax_spin)
        is_low_locked = bool(low_locked is not None and low_locked.isChecked())
        is_high_locked = bool(high_locked is not None and high_locked.isChecked())

        if not values.size and self._current_norm_spec().kind == 'log':
            self._display_mapping_error('Log mapping requires positive visible values; none are available.')
            return
        if is_low_locked and is_high_locked:
            candidate = (float(self.vmin_spin.value()), float(self.vmax_spin.value()))
        elif values.size:
            candidate_low, candidate_high = np.nanpercentile(values, [0.5, 99.5])
            candidate = (float(candidate_low), float(candidate_high))
        elif self._last_valid_color_limits is not None:
            candidate = self._last_valid_color_limits
        else:
            candidate = (
                float(self.vmin_spin.value()), float(self.vmax_spin.value())
            )

        low = float(self.vmin_spin.value()) if is_low_locked else float(candidate[0])
        high = float(self.vmax_spin.value()) if is_high_locked else float(candidate[1])
        if not is_low_locked and not is_high_locked:
            if low == high:
                pad = self._color_gap(low)
                if self._current_norm_spec().kind == 'log':
                    pad = min(pad, low / 2)
                low, high = low - pad, high + pad
        elif is_low_locked and not is_high_locked:
            if high <= low:
                high = low + self._color_gap(low)
        elif not is_low_locked and is_high_locked:
            if low >= high:
                low = high - self._color_gap(high)

        if self._current_norm_spec().kind == 'zero-centered':
            if not is_low_locked:
                low = min(low, -self._color_gap(high))
            if not is_high_locked:
                high = max(high, self._color_gap(low))

        for spin, value, locked in (
            (self.vmin_spin, low, is_low_locked),
            (self.vmax_spin, high, is_high_locked),
        ):
            if locked:
                continue
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)

        self._last_valid_color_limits = self._ordered_pair(
            float(self.vmin_spin.value()), float(self.vmax_spin.value())
        )
        self._apply_color_limits_to_mesh(visible_values=[values])

    def _apply_color_limits_to_mesh(self, *, visible_values=None) -> None:
        self._invalidate_plot_page()
        if not self._heat_meshes:
            return
        try:
            self._apply_color_limits_to_mesh_impl()
            self._refresh_color_range_status(visible_values=visible_values)
        except ValueError as exc:
            self._display_mapping_error(str(exc))

    def _apply_color_limits_to_mesh_impl(self) -> None:
        if self._color_scale_mode in ("2", "3"):
            ranges = self._last_valid_region_color_limits
            for mesh, (vmin, vmax) in zip(self._heat_meshes, ranges):
                mesh.set_norm(build_color_norm(self._current_norm_spec(), (float(vmin), float(vmax))))
            for colorbar, mesh in zip(self._heat_colorbars, self._heat_meshes):
                colorbar.update_normal(mesh)
            self._apply_live_heatmap_style()
            self._schedule_view_layout()
            self.canvas.draw_idle()
            return
        vmin, vmax = self._ordered_pair(
            float(self.vmin_spin.value()), float(self.vmax_spin.value())
        )
        self._heat_mesh.set_norm(build_color_norm(self._current_norm_spec(), (float(vmin), float(vmax))))
        if self._heat_colorbar is not None:
            self._heat_colorbar.update_normal(self._heat_mesh)
        self._layout_live_plots()
        self.canvas.draw_idle()

    def _on_plot_gate_spin_changed(self, value: float) -> None:
        self._set_current_gate_from_value(float(value), update_plot=True)

    def _on_plot_gate_spin_edit_finished(self) -> None:
        self._set_current_gate_from_value(float(self.plot_gate_spin.value()), update_plot=True)

    def _on_plot_gate_index_changed(self, value: int) -> None:
        if self.current_cube is None:
            return
        self._set_current_gate_from_index(int(value) - 1, update_plot=True)

    def _step_gate_index(self, step: int) -> None:
        if self.current_cube is None:
            return
        self._set_current_gate_from_index(self._current_gate_index() + int(step), update_plot=True)

    def _on_toggle_circle_delete_mode(self, on: bool) -> None:
        self._batch_delete_circle_start = None
        msg = "Circle delete ON: drag on the heatmap to remove all-gate peaks inside the circle." if on else "Circle delete OFF."
        self.statusBar().showMessage(msg)

    def _on_set_fit_full_range(self) -> None:
        self._set_fit_range_to_full()
        self._capture_dataset_controls()
        self._update_spectrum_only()

    def _on_fit_range_changed(self, _value: float) -> None:
        self._refresh_fit_range_summary()
        self._refresh_fit_readiness()
        self._capture_dataset_controls()
        self._update_spectrum_only()

    def _on_use_current_x_range(self) -> None:
        if self.current_cube is None:
            return
        xmin, xmax = self._ordered_pair(float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        for spin, value in ((self.fit_xmin_spin, xmin), (self.fit_xmax_spin, xmax)):
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)
        self._refresh_fit_range_summary()
        self._refresh_fit_readiness()
        self._capture_dataset_controls()
        self._update_spectrum_only()

    def _refresh_fit_range_summary(self) -> None:
        if not hasattr(self, "fit_range_summary_label"):
            return
        if self.current_cube is None:
            self.fit_range_summary_label.setText("Detection range: —\nFit range: —")
            return
        detection = self._ordered_pair(float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        fitting = self._fit_range_bounds()
        unit = self.current_cube.energy_label
        self.fit_range_summary_label.setText(
            f"Detection range (visible X): {detection[0]:.6g}–{detection[1]:.6g} {unit}\n"
            f"Fit range (independent): {fitting[0]:.6g}–{fitting[1]:.6g} {unit}"
        )
        if hasattr(self, 'peak_validation_label'):
            self._refresh_analysis_controls()

    def _set_fit_range_to_full(self) -> None:
        if self.current_cube is None:
            return
        xmin = float(np.nanmin(self.current_cube.energy))
        xmax = float(np.nanmax(self.current_cube.energy))
        for spin, val in ((self.fit_xmin_spin, xmin), (self.fit_xmax_spin, xmax)):
            lock_box = self._fit_lock_controls.get(spin)
            if lock_box is not None and lock_box.isChecked():
                continue
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
        self._refresh_fit_range_summary()

    def _set_view_limits_to_auto(self) -> None:
        if self.current_cube is None:
            return
        cube = graph_cube(self.workspace.active or self._unregistered_session)
        xmin = float(np.nanmin(cube.energy))
        xmax = float(np.nanmax(cube.energy))
        ymin = float(np.nanmin(cube.gate))
        ymax = float(np.nanmax(cube.gate))
        for spin, val in (
            (self.xmin_spin, xmin), (self.xmax_spin, xmax),
            (self.ymin_spin, ymin), (self.ymax_spin, ymax),
        ):
            lock_box = self._view_lock_controls.get(spin)
            if lock_box is not None and lock_box.isChecked():
                continue
            spin.blockSignals(True)
            spin.setValue(float(val))
            spin.blockSignals(False)
        self._update_auto_color_limits_from_visible_view(use_spin_values=True)

    # =========================================================================
    #  Find Peak logic
    # =========================================================================

    def _on_detect_mode_changed(self, orig_is_checked: bool) -> None:
        self._orig_params_widget.setVisible(orig_is_checked)
        self._d2_params_widget.setVisible(not orig_is_checked)
        self._cached_d2 = None

    def _on_orig_type_changed(self, text: str) -> None:
        both = (text == "Both")
        orig_form = self._orig_params_widget.layout()
        orig_form.labelForField(self.max_dips_spin).setVisible(both)
        self.max_dips_spin.setVisible(both)

    def _on_d2_type_changed(self, text: str) -> None:
        both = (text == "Both")
        d2_form = self._d2_params_widget.layout()
        d2_form.labelForField(self.d2_max_dips_spin).setVisible(both)
        self.d2_max_dips_spin.setVisible(both)

    def _auto_kind(self, y: np.ndarray, idx: int) -> bool:
        """Return True if index looks like a peak (concave-down), False for dip."""
        n = y.size
        lo = max(0, idx - 2)
        hi = min(n - 1, idx + 2)
        if hi - lo < 2:
            return True
        d2 = float(np.diff(y[lo : hi + 1], 2).mean())
        return d2 < 0

    def _auto_rank_by_x(self, gate_idx: int) -> dict[int, int]:
        """Return {energy_idx: 1-based rank} for all peaks in a gate, sorted by x-position."""
        idxs = list(self.batch_peak_map.get(gate_idx, np.asarray([], dtype=int)))
        if not idxs and self._peak_gate_idx == gate_idx:
            idxs = list(self.peak_indices)
        sorted_idxs = sorted(int(i) for i in idxs)
        return {ei: rank for rank, ei in enumerate(sorted_idxs, start=1)}

    def _resolved_peak_label(self, gate_idx: int, energy_idx: int) -> int:
        """Return the peak label: manual override if set, otherwise auto x-rank."""
        override = self.peak_label_lookup.get((int(gate_idx), int(energy_idx)))
        if override is not None:
            return override
        return self._auto_rank_by_x(gate_idx).get(int(energy_idx), 1)

    def _peak_label_color(self, label: int) -> str:
        """Return tab10 hex color string for a 1-based label index."""
        return _TAB10[(int(label) - 1) % len(_TAB10)]

    def _show_peak_context_menu(self, x_click: float) -> None:
        """Right-click context menu on spectrum plot: Set Label or Delete Peak."""
        if self.current_cube is None:
            return
        trace_peak_indices = self._current_trace_peak_indices()
        if trace_peak_indices.size == 0:
            return
        x = self.current_cube.energy
        peak_x = x[trace_peak_indices]
        x_span = max(float(np.nanmax(x) - np.nanmin(x)), 1e-12)
        dists = np.abs(peak_x - x_click)
        nearest_arr_idx = int(np.argmin(dists))
        if dists[nearest_arr_idx] > 0.02 * x_span:
            return
        nearest_idx = int(trace_peak_indices[nearest_arr_idx])
        gate_idx = self._current_gate_index()
        current_label = self._resolved_peak_label(gate_idx, nearest_idx)

        menu = QMenu(self)
        set_label_action = menu.addAction(f"Set Label…  (current: {current_label})")
        set_label_action.setData("set_label")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Peak")
        delete_action.setData("delete")
        chosen = menu.exec(QCursor.pos())

        if chosen is None:
            return
        chosen_data = chosen.data()

        if chosen_data == "delete":
            if self._remove_manual_peak_near(float(x[nearest_idx])):
                gate_used, y = self._nearest_spectrum(self.current_gate)
                self._update_spectrum_only()
                self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")
        elif chosen_data == "set_label":
            new_label, ok = QInputDialog.getInt(
                self, "Set Peak Label",
                "Label index (1 = first peak, 2 = second, …):",
                value=current_label, min=1, max=99,
            )
            if ok and new_label != current_label:
                self._finish_history_edit()
                session = self.workspace.active or self._unregistered_session
                key = (gate_idx, nearest_idx)
                old = session.peak_label_lookup.get(key)
                change = FieldChange(('peak_label_lookup', key), old, new_label, key in session.peak_label_lookup, True)
                session.peak_label_lookup[key] = new_label
                if self._history_enabled():
                    self._history_for(session).record(EditCommand('Peak label', (change,)))
                    self._refresh_graph_commands()
                gate_used, y = self._nearest_spectrum(self.current_gate)
                self._update_spectrum_only()
                self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")

    def _on_find_peaks(self) -> None:
        if self._workbench_document() is not None:
            self.workbench_scope.setCurrentIndex(0)
            return self._workbench_find()
        if self._dat_import_pending:
            return
        if not self._guard_analysis_controls(detecting=True):
            return
        self._invalidate_analysis_jobs()
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        self.find_btn.setText('Detecting Current Gate…')
        self.find_btn.setEnabled(False)
        self.peak_validation_label.setText('Detecting current gate…')
        self.statusBar().showMessage('Detecting current gate…')
        # Paint local feedback before the synchronous finder occupies the GUI.
        self.find_btn.repaint()
        self.peak_validation_label.repaint()
        try:
            self._find_current_gate_peaks()
        finally:
            self.find_btn.setText('Find Peaks (This Gate)')
            self._refresh_analysis_controls()

    def _find_current_gate_peaks(self) -> None:
        gate_used, y = self._nearest_spectrum(self.current_gate)
        x = self.current_cube.energy
        mask = self._peak_search_mask(x)
        xr = np.asarray(x[mask], dtype=float)
        yr = np.asarray(y[mask], dtype=float)
        if xr.size < 5:
            self._show_error("Visible x-range is too narrow for peak finding.")
            return
        mask_idx = np.where(mask)[0]

        if self._d2_radio.isChecked():
            # Second-derivative mode
            try:
                idx_local, kinds_local, d2y = find_peaks_second_derivative(
                    xr, yr,
                    mode=self._peak_mode(),  # type: ignore[arg-type]
                    sg_window=int(self.d2_sg_win_spin.value()),
                    sg_polyorder=int(self.d2_sg_poly_spin.value()),
                    d2_prom_frac=float(self.d2_prom_spin.value()),
                    d2_distance=int(self.d2_dist_spin.value()),
                    d2_sep_ev=float(self.d2_sep_spin.value()),
                    max_peaks=int(self.d2_max_peaks_spin.value()),
                    max_dips=int(self.d2_max_dips_spin.value()),
                    target_count=int(self.d2_target_spin.value()),
                )
                self._cached_d2 = (xr, d2y)
                window = _clamp_sg_window(xr.size, int(self.d2_sg_win_spin.value()))
                polyorder = min(max(1, int(self.d2_sg_poly_spin.value())), window - 1)
                self._cached_d2_key = self._second_derivative_key(mask, window, polyorder)
            except Exception as exc:
                self._show_error(str(exc))
                return
        else:
            # Original spectrum mode
            if self.peak_smooth_chk.isChecked():
                win = _clamp_sg_window(yr.size, int(self.peak_smooth_win_spin.value()))
                if win >= 3 and yr.size > win:
                    yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")
            try:
                idx_local, kinds_local = find_peak_indices(
                    xr, yr,
                    mode=self._peak_mode(),  # type: ignore[arg-type]
                    prominence_frac=float(self.prom_spin.value()),
                    min_distance=int(self.distance_spin.value()),
                    max_peaks=int(self.max_peaks_spin.value()),
                    max_dips=int(self.max_dips_spin.value()),
                )
            except Exception as exc:
                self._show_error(str(exc))
                return
            self._cached_d2 = None

        idx = np.asarray(mask_idx[idx_local], dtype=int) if idx_local.size else np.asarray([], dtype=int)
        self.peak_indices = idx
        self.peak_kind_lookup = {int(ei): bool(k) for ei, k in zip(idx, kinds_local)}
        self._peak_gate_idx = int(np.argmin(np.abs(self.current_cube.gate - float(gate_used))))
        gate_idx = self._peak_gate_idx
        # Remove stale kind entries for this gate then write new ones
        for key in [k for k in self.batch_peak_kind_lookup if k[0] == gate_idx]:
            del self.batch_peak_kind_lookup[key]
        if idx.size > 0:
            self.batch_peak_map[gate_idx] = np.asarray(idx, dtype=int)
            for ei, k in zip(idx, kinds_local):
                self.batch_peak_kind_lookup[(gate_idx, int(ei))] = bool(k)
        else:
            self.batch_peak_map.pop(gate_idx, None)
        self._sync_batch_peak_coords()
        self._invalidate_peak_fits(self.workspace.active or self._unregistered_session, {gate_idx})
        self._refresh_peak_delete_controls()
        self.current_gate = gate_used
        self._sync_gate_controls()
        self._update_spectrum_only()
        self._update_peak_text(gate_used, x, y, title_prefix="Auto-detected")

    def _on_batch_find_peaks_all_gates(self) -> None:
        if self._workbench_document() is not None:
            self.workbench_scope.setCurrentIndex(1)
            return self._workbench_find()
        if self._dat_import_pending:
            return
        if not self._guard_analysis_controls(detecting=True):
            return
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        if self._analysis_request_matches('batch-find', None):
            return
        cube = self.current_cube
        x = cube.energy
        mask = self._peak_search_mask(x)
        xr = np.asarray(x[mask], dtype=float)
        mask_idx = np.where(mask)[0]
        if xr.size < 5:
            self._show_error("Visible x-range is too narrow for batch peak finding.")
            return

        use_d2 = self._d2_radio.isChecked()
        peak_mode = self._peak_mode()
        prom = float(self.prom_spin.value())
        min_dist = int(self.distance_spin.value())
        max_peaks_o = int(self.max_peaks_spin.value())
        max_dips_o = int(self.max_dips_spin.value())
        sg_win = int(self.d2_sg_win_spin.value())
        sg_poly = int(self.d2_sg_poly_spin.value())
        d2_prom = float(self.d2_prom_spin.value())
        d2_dist = int(self.d2_dist_spin.value())
        d2_sep = float(self.d2_sep_spin.value())
        d2_max = int(self.d2_max_peaks_spin.value())
        d2_max_dips = int(self.d2_max_dips_spin.value())
        d2_tgt = int(self.d2_target_spin.value())

        key = self._analysis_job_key()
        smooth = self.peak_smooth_chk.isChecked()
        smooth_window = int(self.peak_smooth_win_spin.value())
        cube = replace(cube, energy=cube.energy.copy(), gate=cube.gate.copy(), z=cube.z.copy())

        def work(context):
            peak_map: dict[int, np.ndarray] = {}
            batch_kind_lookup: dict[tuple[int, int], bool] = {}
            ok_count = 0
            peak_total = 0

            completed_gates = set()
            for gi, gate in enumerate(cube.gate):
                context.report(f"Batch peak finding… gate {gi+1}/{cube.gate.size}")
                y = np.asarray(cube.z[gi, :], dtype=float)
                yr = np.asarray(y[mask], dtype=float)

                context.check_cancelled()
                try:
                    if use_d2:
                        idx_local, kinds_local, _ = find_peaks_second_derivative(
                            xr, yr,
                            mode=peak_mode,  # type: ignore[arg-type]
                            sg_window=sg_win, sg_polyorder=sg_poly,
                            d2_prom_frac=d2_prom, d2_distance=d2_dist,
                            d2_sep_ev=d2_sep, max_peaks=d2_max, max_dips=d2_max_dips,
                            target_count=d2_tgt,
                        )
                    else:
                        if smooth:
                            win = _clamp_sg_window(yr.size, smooth_window)
                            if win >= 3 and yr.size > win:
                                yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")
                        idx_local, kinds_local = find_peak_indices(
                            xr, yr,
                            mode=peak_mode,  # type: ignore[arg-type]
                            prominence_frac=prom, min_distance=min_dist,
                            max_peaks=max_peaks_o, max_dips=max_dips_o,
                        )
                except CancelledError:
                    raise
                except Exception:
                    continue

                context.check_cancelled()
                completed_gates.add(gi)
                if idx_local.size == 0:
                    continue
                ok_count += 1
                peak_total += int(idx_local.size)
                full_idx = np.asarray(mask_idx[idx_local], dtype=int)
                peak_map[gi] = full_idx
                for fi, ki in zip(full_idx, kinds_local):
                    batch_kind_lookup[(gi, int(fi))] = bool(ki)


            context.check_cancelled()
            return peak_map, batch_kind_lookup, ok_count, peak_total, completed_gates

        def apply(payload):
            if key != self._analysis_job_key():
                self.statusBar().showMessage("Batch peaks discarded because analysis inputs changed.")
                return
            peak_map, batch_kind_lookup, ok_count, peak_total, completed_gates = payload
            self.batch_peak_map = peak_map
            self.batch_peak_kind_lookup = batch_kind_lookup
            current_gate_idx = self._current_gate_index()
            self.peak_indices = np.asarray(peak_map.get(current_gate_idx, []), dtype=int)
            self.peak_kind_lookup = {
                int(ei): batch_kind_lookup.get((current_gate_idx, int(ei)), True)
                for ei in self.peak_indices
            }
            self._peak_gate_idx = current_gate_idx
            if completed_gates:
                self._invalidate_peak_fits(self.workspace.active or self._unregistered_session, completed_gates)
            self._lasso_selected_peak_ids = []
            self._clear_lasso_selection_visual()
            self._sync_batch_peak_coords()
            self._update_spectrum_only()
            gate_used, y = self._nearest_spectrum(self.current_gate)
            self._update_peak_text(gate_used, x, y, title_prefix="UI-detected")
            mode_str = "2nd-derivative" if use_d2 else "original-spectrum"
            self.statusBar().showMessage(f"Batch peaks ({mode_str}): {ok_count} gates, {peak_total} peaks total.")
            self.analysis_text.setPlainText(
                f"Batch peak finding ({mode_str}).\n"
                f"Gates processed: {cube.gate.size}\n"
                f"Gates with peaks: {ok_count}\n"
                f"Total peaks: {peak_total}\n"
                "Shown as cyan circles on heatmap."
            )

        self._start_analysis_job('batch-find', None, key, work, apply, "Batch peak finding…")

    def _on_save_peaks_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        cube = self.current_cube
        self._sync_current_trace_peaks_to_batch_map()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = self._output_folder() / f"{cube.title}_peaks_{stamp}.csv"
        out_path, _ = QFileDialog.getSaveFileName(self, "Save Peaks CSV", str(default), "CSV Files (*.csv)")
        if not out_path:
            return
        rows_by_gate = self._current_peak_overlay_by_gate()
        if not rows_by_gate:
            self._show_error("No peaks to save. Find peaks first.")
            return
        try:
            with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
                wr = csv.writer(fh)
                max_peaks = max(len(peaks) for _, peaks in rows_by_gate)
                header = ["gate", "analysis_axis", "original_axis", "peak_count"]
                for peak_no in range(1, max_peaks + 1):
                    header.extend([
                        f"peak_{peak_no}_type",
                        f"peak_{peak_no}_x",
                        f"peak_{peak_no}_intensity",
                        f"peak_{peak_no}_original_x",
                    ])
                wr.writerow(header)
                for gate_v, peaks in rows_by_gate:
                    row = [
                        f"{gate_v:.10g}",
                        cube.energy_label,
                        cube.x_original_label,
                        str(len(peaks)),
                    ]
                    for e_v, intensity_v, orig_x_v, kind_str in peaks:
                        row.extend([
                            kind_str,
                            f"{e_v:.10g}",
                            f"{intensity_v:.10g}",
                            f"{orig_x_v:.10g}",
                        ])
                    missing = max_peaks - len(peaks)
                    if missing > 0:
                        row.extend([""] * (4 * missing))
                    wr.writerow(row)
        except Exception as exc:
            self._show_error(f"Failed to save peaks CSV: {exc}")
            return
        self.statusBar().showMessage(f"Saved peaks CSV: {out_path} ({len(rows_by_gate)} gates)")

    def _on_delete_peak_by_number(self) -> None:
        with self._peak_edit('Remove peak', (self._current_gate_index(),)):
            if self._dat_import_pending:
                return
            if self.current_cube is None or self.peak_indices.size == 0:
                return
            if self._peak_gate_idx != self._current_gate_index():
                return
            n = int(self.peak_delete_spin.value())
            if n < 1 or n > int(self.peak_indices.size):
                return
            self._invalidate_analysis_jobs()
            idx_to_remove = int(self.peak_indices[n - 1])
            self.peak_indices = np.asarray(self.peak_indices[self.peak_indices != idx_to_remove], dtype=int)
            self.peak_label_lookup.pop((self._current_gate_index(), idx_to_remove), None)
            self._sync_current_trace_peaks_to_batch_map()
            self.fit_result = None
            self._refresh_peak_delete_controls()
            gate_used, y = self._nearest_spectrum(self.current_gate)
            self._update_spectrum_only()
            self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")

    def _on_clear_peaks(self) -> None:
        if self._workbench_document() is not None:
            self._workbench_document().set_seeds(self._current_gate_index(), [])
            return self._workbench_changed()
        with self._peak_edit('Clear gate peaks', (self._current_gate_index(),)):
            if self._dat_import_pending:
                return
            if self.current_cube is None:
                return
            gate_idx = self._current_gate_index()
            if (not self._current_trace_peak_indices().size
                    and not len(self.batch_peak_map.get(gate_idx, ()))
                    and not any(key[0] == gate_idx for key in self.peak_label_lookup)):
                return
            self._invalidate_analysis_jobs()
            for key in [k for k in self.peak_label_lookup if k[0] == gate_idx]:
                del self.peak_label_lookup[key]
            self.peak_indices = np.asarray([], dtype=int)
            self.peak_kind_lookup = {}
            self._peak_gate_idx = gate_idx
            self._sync_current_trace_peaks_to_batch_map()
            self.fit_result = None
            self._refresh_peak_delete_controls()
            gate_used, y = self._nearest_spectrum(self.current_gate)
            self._update_spectrum_only()
            self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")

    def _on_clear_batch_peaks(self) -> None:
        if self._workbench_document() is not None:
            for gate in range(self.current_cube.gate.size):
                self._workbench_document().set_seeds(gate, [])
            return self._workbench_changed()
        with self._peak_edit('Clear all gate peaks', set(self.batch_peak_map) | ({self._peak_gate_idx} if self._peak_gate_idx is not None else set())):
            if self._dat_import_pending:
                return
            self._invalidate_analysis_jobs()
            self.batch_peak_map = {}
            self.batch_peak_kind_lookup = {}
            self.peak_label_lookup = {}
            self.peak_indices = np.array([], dtype=int)
            self.peak_kind_lookup = {}
            self.batch_peak_coords = None
            self.batch_peak_records = []
            self._batch_delete_circle_start = None
            self._lasso_selected_peak_ids = []
            self._clear_lasso_selection_visual()
            self._update_lasso_actions()
            self._update_spectrum_only()
            self.statusBar().showMessage("Cleared all-gate peak overlay.")

    # =========================================================================
    #  Lasso selection
    # =========================================================================

    def _on_detect_mode_changed_lasso_guard(self) -> None:
        """Called when detection mode changes; disconnect lasso if active."""
        if self._lasso_mode:
            self.lasso_mode_btn.setChecked(False)

    def _on_lasso_mode_toggled(self, on: bool) -> None:
        self._lasso_mode = on
        self.lasso_helper_label.setVisible(on)
        if not on:
            self._lasso_selected_peak_ids = []
            self._clear_lasso_selection_visual()
            self._update_lasso_actions()
        self._disconnect_lasso_selector()
        if on:
            self._connect_lasso_selector()
            if not self._lasso_mode:
                return
            self.statusBar().showMessage(
                "Lasso mode active: drag on heatmap to select peaks. Normal heatmap click is temporarily disabled."
            )
        else:
            self.statusBar().showMessage("Lasso mode OFF. Heatmap clicks select gate again.")
        self._set_canvas_cursor_for_event(None)
        self._update_spectrum_only()

    def _on_lasso_complete(self, verts: Any) -> None:
        if self._dat_import_pending:
            return
        if self.current_cube is None or not self.batch_peak_records or self.batch_peak_coords is None:
            self._lasso_selected_peak_ids = []
            self._clear_lasso_selection_visual()
            self._update_lasso_actions()
            self.statusBar().showMessage("Lasso: no all-gate heatmap peaks are available to select.")
            return
        if not hasattr(verts, "__len__") or len(verts) < 3:
            return
        polygon = MplPath(verts)
        bx, by = self.batch_peak_coords
        points = np.column_stack([bx, by])
        inside = polygon.contains_points(points)
        self._lasso_selected_peak_ids = [self.batch_peak_records[i] for i, keep in enumerate(inside) if keep]
        self._clear_lasso_selection_visual()
        if self._heat_ax is not None:
            self._lasso_selection_patch = MplPolygon(
                verts,
                closed=True,
                facecolor="#f8ff3d",
                edgecolor="#ff00c8",
                linewidth=2.2,
                alpha=0.14,
                zorder=26,
            )
            self._heat_ax.add_patch(self._lasso_selection_patch)
        self._update_lasso_actions()
        self._update_spectrum_only()
        n_sel = len(self._lasso_selected_peak_ids)
        n_total = len(self.batch_peak_records)
        if n_sel:
            self.statusBar().showMessage(
                f"Lasso: selected {n_sel} of {n_total} heatmap peaks. Press 'Delete Lasso Selection (Heatmap)' to remove."
            )
        else:
            self.statusBar().showMessage(f"Lasso: 0 of {n_total} heatmap peaks fell inside the selection.")

    def _on_delete_lasso_selection(self) -> None:
        with self._peak_edit('Delete lasso peaks', {gi for gi, _ in self._lasso_selected_peak_ids}):
            if self._dat_import_pending:
                return
            self._invalidate_analysis_jobs()
            if not self._lasso_selected_peak_ids:
                return
            delete_map: dict[int, set[int]] = {}
            for gi, ei in self._lasso_selected_peak_ids:
                delete_map.setdefault(int(gi), set()).add(int(ei))
            new_map: dict[int, np.ndarray] = {}
            deleted = 0
            for gi, indices in self.batch_peak_map.items():
                arr = np.asarray(indices, dtype=int)
                keep = np.asarray([idx not in delete_map.get(int(gi), set()) for idx in arr], dtype=bool)
                deleted += int(np.count_nonzero(~keep))
                kept = np.asarray(arr[keep], dtype=int)
                if kept.size:
                    new_map[int(gi)] = kept
            self.batch_peak_map = new_map
            for gi, ei_set in delete_map.items():
                for ei in ei_set:
                    self.peak_label_lookup.pop((int(gi), int(ei)), None)
            self._lasso_selected_peak_ids = []
            self._clear_lasso_selection_visual()
            self._update_lasso_actions()
            self._sync_batch_peak_coords()
            if self.current_cube is not None:
                gate_used, y = self._nearest_spectrum(self.current_gate)
                self._update_spectrum_only()
                self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")
            self.statusBar().showMessage(f"Deleted {deleted} heatmap peak(s) from the all-gate peak set.")

    def _on_clear_lasso_selection(self) -> None:
        self._lasso_selected_peak_ids = []
        self._clear_lasso_selection_visual()
        self._update_lasso_actions()
        self._update_spectrum_only()
        self.statusBar().showMessage("Cleared the current heatmap lasso selection.")

    # =========================================================================
    #  Lorentz fit logic
    # =========================================================================

    def _invalidate_analysis_jobs(self) -> None:
        self._analysis_revision = getattr(self, "_analysis_revision", 0) + 1
        if getattr(self, '_background_job_owner', None) == 'analysis':
            self._background_jobs.cancel()
            self._background_job_owner = None
        self._analysis_request = None

    def _analysis_request_matches(self, kind: str, target: int | None) -> bool:
        request = getattr(self, '_analysis_request', None)
        if not request or request[:2] != (kind, target) or self._background_job_owner != 'analysis':
            return False
        # Revision invalidation already tracks edited settings and seeds. Do not
        # traverse all gate seeds just to update a local button or title.
        return request[2][:4] == (self.workspace.active_dataset_id, id(self.current_cube),
                                  self._data_generation, getattr(self, '_analysis_revision', 0))

    def _start_analysis_job(self, kind, target, key, work, apply, description, *, prefix='') -> None:
        request = (kind, target, key)
        if self._analysis_request == request and self._background_job_owner == 'analysis':
            return
        self._analysis_request = request

        def finish(callback, value):
            if self._analysis_request == request:
                self._analysis_request = None
                self._background_job_owner = None
            try:
                callback(value)
            finally:
                self._refresh_analysis_controls()

        self._start_background_job(work, lambda result: finish(apply, result), description,
            on_error=lambda exc: finish(lambda error: self._analysis_job_error(key, error, prefix), exc),
            owner='analysis')

    def _analysis_job_key(self) -> tuple:
        # Read only on the GUI thread. Gate navigation deliberately is not part
        # of the key: completed fits belong to their captured gate.
        controls = (
            self.fit_smooth_chk.isChecked(), self.fit_smooth_win_spin.value(),
            self.fit_model_combo.currentText(), self.lw_min_spin.value(),
            self.lw_max_spin.value(), self.peak_move_limit_spin.value(), self.maxfev_spin.value(),
            self._fit_range_bounds(), self._peak_search_bounds(), self._peak_mode(),
            self._seeds_from_csv_radio.isChecked(), self._d2_radio.isChecked(),
            self.prom_spin.value(), self.distance_spin.value(), self.max_peaks_spin.value(),
            self.max_dips_spin.value(), self.peak_smooth_chk.isChecked(),
            self.peak_smooth_win_spin.value(), self.d2_sg_win_spin.value(),
            self.d2_sg_poly_spin.value(), self.d2_prom_spin.value(), self.d2_dist_spin.value(),
            self.d2_sep_spin.value(), self.d2_max_peaks_spin.value(),
            self.d2_max_dips_spin.value(), self.d2_target_spin.value(),
        )
        seeds = (
            tuple((gi, tuple(np.asarray(indices, dtype=int))) for gi, indices in sorted(self.batch_peak_map.items())),
            tuple((gi, tuple(values)) for gi, values in sorted((self._csv_seeds_map or {}).items())),
            tuple((gi, int(ei), self.batch_peak_kind_lookup.get((gi, int(ei)), True))
                  for gi, indices in sorted(self.batch_peak_map.items()) for ei in indices),
        )
        return (self.workspace.active_dataset_id, id(self.current_cube), self._data_generation,
                getattr(self, "_analysis_revision", 0), controls, seeds)

    def _analysis_job_error(self, key: tuple, error: Exception, prefix: str = "") -> None:
        if key == self._analysis_job_key():
            self._show_error(f"{prefix}{error}")

    def _on_fit(self) -> None:
        if self._workbench_document() is not None:
            self.workbench_scope.setCurrentIndex(0)
            return self._workbench_fit()
        if self._dat_import_pending:
            return
        if not self._guard_analysis_controls(fitting=True):
            return
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        if self._analysis_request_matches('fit', self._current_gate_index()):
            return
        gate_used, y = self._nearest_spectrum(self.current_gate)
        x = self.current_cube.energy
        try:
            xr, yr, mask = self._analysis_xy(
                x, y,
                smooth=self.fit_smooth_chk.isChecked(),
                smooth_window=int(self.fit_smooth_win_spin.value()),
            )
        except Exception as exc:
            self._show_error(str(exc))
            return

        centers = self._get_seeds_for_current_gate()
        xlo, xhi = self._fit_range_bounds()
        centers_in = centers[(centers >= xlo) & (centers <= xhi)] if centers.size else centers
        if centers_in.size == 0:
            _seeds, _in_range, message, can_detect = self._fit_seed_status()
            action = "Use 'Detect Then Fit Current Gate' first." if can_detect else "Adjust the source or fit range."
            self._show_error(f"{message}\n{action}")
            return

        lw_min = float(self._pts_to_axis_units(int(self.lw_min_spin.value())) or 0.0)
        lw_max_v = float(self._pts_to_axis_units(int(self.lw_max_spin.value())) or 0.0)
        lw_max = lw_max_v
        peak_move = float(self._pts_to_axis_units(int(self.peak_move_limit_spin.value())) or 0.0)
        maxfev = int(self.maxfev_spin.value())

        fit_mode = self._peak_mode()
        fit_kinds: np.ndarray | None = None
        if fit_mode == "Both" and self.current_cube is not None:
            x_arr = self.current_cube.energy
            fit_kinds = np.asarray([
                self.peak_kind_lookup.get(int(np.argmin(np.abs(x_arr - c))), True)
                for c in centers_in
            ], dtype=bool)
        key = self._analysis_job_key()
        gate_idx = self._current_gate_index()
        model = str(self.fit_model_combo.currentText())
        energy_label = self.current_cube.energy_label
        xr, yr, centers_in = xr.copy(), yr.copy(), centers_in.copy()

        def work(context):
            context.check_cancelled()
            result = fit_multi_peak(
                xr, yr,
                centers_hint=centers_in,
                model=model,  # type: ignore[arg-type]
                mode=fit_mode,  # type: ignore[arg-type]
                linewidth_min=lw_min,
                linewidth_max=lw_max,
                peak_move_limit=peak_move,
                maxfev=maxfev,
                kinds=fit_kinds,
            )
            context.check_cancelled()
            return result

        def apply(res):
            if key != self._analysis_job_key():
                self.statusBar().showMessage("Fit discarded because analysis inputs changed.")
                return
            self.batch_fit_map[gate_idx] = np.asarray(res.centers, dtype=float)
            self.batch_fit_result_map[gate_idx] = res
            fit_row = self._fit_result_to_export_row(
                float(gate_used), res.model, energy_label, res, status="ok"
            )
            self._upsert_fit_export_row(fit_row)
            self._sync_result_sheets()
            self._refresh_export_actions()
            self._sync_batch_fit_coords()
            if self._current_gate_index() != gate_idx:
                self.statusBar().showMessage(f"Fit completed for gate {gate_used:.6g}.")
                self._update_spectrum_only()
                return
            self.fit_result = res
            self._fit_gate_idx = gate_idx
            self._update_spectrum_only()
            width_name = "sigma" if res.model == "Gaussian" else "HWHM gamma"
            lines = [
                f"{res.model} fit @ gate {gate_used:.6g}",
                f"Baseline: {res.baseline:.6g}",
                f"R^2: {res.r_squared:.6g}",
                f"RMSE: {res.rmse:.6g}",
            ]
            for i in range(res.centers.size):
                lines.append(f"  {i+1}. cen={res.centers[i]:.6g}, amp={res.amplitudes[i]:.6g}, {width_name}={res.widths[i]:.6g}")
            self.analysis_text.setPlainText("\n".join(lines))
            self.statusBar().showMessage(f"Fit completed for gate {gate_used:.6g}.")

        self._start_analysis_job('fit', gate_idx, key, work, apply, "Fitting current gate…",
                                 prefix='Fit failed: ')

    def _on_clear_fit(self) -> None:
        if self._workbench_document() is not None:
            self._workbench_document().retire_fit(self._current_gate_index())
            return self._workbench_changed()
        if self._dat_import_pending:
            return
        self._invalidate_analysis_jobs()
        self.fit_result = None
        self._fit_gate_idx = None
        self.batch_fit_map = {}
        self.batch_fit_coords = None
        self.batch_fit_result_map = {}
        self._batch_fit_export_rows = []
        self._has_batch_fit_results = False
        self._sync_result_sheets()
        self._refresh_export_actions()
        self._update_spectrum_only()

    def _on_batch_fit_all_gates(self) -> None:
        if self._workbench_document() is not None:
            self.workbench_scope.setCurrentIndex(1)
            return self._workbench_fit()
        if self._dat_import_pending:
            return
        if not self._guard_analysis_controls(fitting=True):
            return
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        if self._analysis_request_matches('batch-fit', None):
            return
        cube = self.current_cube
        if self._seeds_from_csv_radio.isChecked() and self._csv_seeds_map is None:
            self._show_error("CSV seed source is selected, but no seeds CSV is loaded.")
            return
        use_csv = self._seeds_from_csv_radio.isChecked()
        if not use_csv and not self.batch_peak_map:
            self._show_error(
                "No seeds available for batch fit.\n"
                "Run 'Find Peaks – All Gates' in Find Peaks tab first,\n"
                "or load a seeds CSV in the Peak Fit tab."
            )
            return

        model = str(self.fit_model_combo.currentText())
        peak_mode = self._peak_mode()
        use_kinds = (peak_mode == "Both")
        x = cube.energy
        mask = self._fit_mask(x)
        xr_base = np.asarray(x[mask], dtype=float)
        if xr_base.size < 5:
            self._show_error("Fit range is too narrow for batch fit.")
            return

        lw_min = float(self._pts_to_axis_units(int(self.lw_min_spin.value())) or 0.0)
        lw_max_v = float(self._pts_to_axis_units(int(self.lw_max_spin.value())) or 0.0)
        lw_max = lw_max_v
        peak_move = float(self._pts_to_axis_units(int(self.peak_move_limit_spin.value())) or 0.0)
        maxfev = int(self.maxfev_spin.value())
        xlo, xhi = self._fit_range_bounds()

        key = self._analysis_job_key()
        smooth = self.fit_smooth_chk.isChecked()
        smooth_window = int(self.fit_smooth_win_spin.value())
        seeds = tuple(self._get_seeds_for_gate_idx(gi).copy() for gi in range(cube.gate.size))
        kind_lookup = dict(self.batch_peak_kind_lookup)
        cube = replace(cube, energy=cube.energy.copy(), gate=cube.gate.copy(), z=cube.z.copy())

        def work(context):
            rows = []
            ok_count = 0
            fail_count = 0
            fit_map: dict[int, np.ndarray] = {}
            fit_result_map: dict[int, FitResult] = {}

            for gi, gate in enumerate(cube.gate):
                context.report(f"Batch fitting… gate {gi+1}/{cube.gate.size}")
                y = np.asarray(cube.z[gi, :], dtype=float)
                yr = np.asarray(y[mask], dtype=float)
                if smooth:
                    win = _clamp_sg_window(yr.size, smooth_window)
                    if win >= 3 and yr.size > win:
                        yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")

                centers = seeds[gi]
                centers_in = centers[(centers >= xlo) & (centers <= xhi)] if centers.size else centers
                if centers_in.size == 0:
                    fail_count += 1
                    rows.append((float(gate), None, "no_seeds"))
                    continue

                batch_kinds: np.ndarray | None = None
                if use_kinds:
                    x_arr = cube.energy
                    batch_kinds = np.asarray([
                        kind_lookup.get(
                            (gi, int(np.argmin(np.abs(x_arr - c)))), True
                        )
                        for c in centers_in
                    ], dtype=bool)
                context.check_cancelled()
                try:
                    res = fit_multi_peak(
                        xr_base, yr,
                        centers_hint=centers_in,
                        model=model,  # type: ignore[arg-type]
                        mode=peak_mode,  # type: ignore[arg-type]
                        linewidth_min=lw_min, linewidth_max=lw_max,
                        peak_move_limit=peak_move, maxfev=maxfev,
                        kinds=batch_kinds,
                    )
                except CancelledError:
                    raise
                except Exception as exc:
                    fail_count += 1
                    rows.append((float(gate), None, f"fit_failed:{exc}"))
                    continue

                context.check_cancelled()
                ok_count += 1
                fit_map[gi] = np.asarray(res.centers, dtype=float)
                fit_result_map[gi] = res
                rows.append((float(gate), res, "ok"))

            context.check_cancelled()
            return fit_map, fit_result_map, rows, ok_count, fail_count

        def apply(payload):
            if key != self._analysis_job_key():
                self.statusBar().showMessage("Batch fit discarded because analysis inputs changed.")
                return
            fit_map, fit_result_map, rows, ok_count, fail_count = payload
            self.batch_fit_map = fit_map
            self.batch_fit_result_map = fit_result_map
            self._batch_fit_export_rows = [self._fit_result_to_export_row(
                gate, model, cube.energy_label, result, status=status) for gate, result, status in rows]
            self._has_batch_fit_results = True
            self._sync_batch_fit_coords()
            self._refresh_export_actions()
            if self.current_cube.gate.size:
                current_gate_idx = self._current_gate_index()
                self.fit_result = self.batch_fit_result_map.get(current_gate_idx)
                self._fit_gate_idx = current_gate_idx if self.fit_result is not None else None
            self._sync_result_sheets()
            self.statusBar().showMessage(f"Batch fit done: OK={ok_count}, fail={fail_count}")
            self.analysis_text.setPlainText(
                f"Batch fit done.\nGates: {cube.gate.size}  OK: {ok_count}  Failed: {fail_count}\n"
                "Use 'Export All Results CSV' to save the results."
            )
            self._update_spectrum_only()

        self._start_analysis_job('batch-fit', None, key, work, apply, "Batch fitting…")

    # =========================================================================
    #  CSV seeds loading
    # =========================================================================

    def _on_load_seeds_csv(self) -> None:
        if self._dat_import_pending:
            return
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        default_dir = str(self.current_folder) if self.current_folder else "."
        path, _ = QFileDialog.getOpenFileName(self, "Load Seeds CSV", default_dir, "CSV Files (*.csv)")
        if not path:
            return
        self._load_seeds_csv_file(Path(path))

    def _load_seeds_csv_file(self, path: Path) -> None:
        """Load a 'gate,peak_energy' CSV as initial seeds for the Peak Fit tab."""
        if self._dat_import_pending:
            return
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        try:
            seeds_map: dict[int, list[float]] = {}
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                first_line = fh.readline().strip()
                fh.seek(0)
                # Determine if there is a header
                try:
                    float(first_line.split(",")[0])
                    has_header = False
                except ValueError:
                    has_header = True
                reader = csv.reader(fh)
                if has_header:
                    next(reader, None)  # skip header
                for row in reader:
                    if len(row) < 2:
                        continue
                    try:
                        gate_v = float(row[0])
                        peak_e = float(row[1])
                    except ValueError:
                        continue
                    gi = int(np.argmin(np.abs(self.current_cube.gate - gate_v)))
                    seeds_map.setdefault(gi, []).append(peak_e)
            self._invalidate_analysis_jobs()
            self._csv_seeds_map = seeds_map
            self._csv_seeds_path = str(path)
            self._schedule_project_modified()
            self._csv_path_label.setText(path.name)
            self._seeds_from_csv_radio.setChecked(True)
            total = sum(len(v) for v in seeds_map.values())
            self.statusBar().showMessage(
                f"Loaded seeds CSV: {path.name}  ({len(seeds_map)} gates, {total} seeds)"
            )
            self._refresh_fit_readiness()
        except Exception as exc:
            self._show_error(f"Failed to load seeds CSV: {exc}")

    # =========================================================================
    #  CSV export
    # =========================================================================

    def _all_fit_export_rows(self) -> list[dict[str, str]]:
        if self.current_cube is None:
            return []
        rows = [dict(row) for row in self._batch_fit_export_rows]
        for gi, result in sorted(self.batch_fit_result_map.items()):
            if gi < 0 or gi >= self.current_cube.gate.size:
                continue
            self._upsert_row_in(rows, self._fit_result_to_export_row(
                float(self.current_cube.gate[gi]), result.model,
                self.current_cube.energy_label, result, status="ok"
            ))
        return rows

    @staticmethod
    def _upsert_row_in(rows: list[dict[str, str]], row: dict[str, str]) -> None:
        gate_key = row.get("gate")
        for idx, existing in enumerate(rows):
            if gate_key is not None and existing.get("gate") == gate_key:
                rows[idx] = dict(row)
                return
        rows.append(dict(row))

    def _refresh_export_actions(self) -> None:
        self._schedule_project_modified()
        if not hasattr(self, "export_current_fit_btn"):
            return
        current_available = self._active_fit_result() is not None if self.current_cube is not None else False
        self.export_current_fit_btn.setEnabled(current_available)
        graph_available = current_available or bool(self._active_graph_peaks()[0].size)
        self.create_analysis_graph_btn.setEnabled(graph_available and self.workspace.active is not None
                                                  and not self._project_busy())
        # Keep refreshes cheap during axis navigation; serialize rows only when
        # the user invokes the all-results export action.
        self.export_all_fit_btn.setEnabled(bool(
            self._batch_fit_export_rows or self.batch_fit_result_map or self.fit_result
        ))
        if hasattr(self, "export_png_btn"):
            self.export_png_btn.setEnabled(
                self.current_cube is not None
                and self._heat_ax is not None
                and self.plot_page.rendered_page is not None
                and self.plot_page.rendered_page.owner_token == self._plot_owner_token()
            )

    def _heatmap_export_metadata(self) -> dict[str, Any]:
        """Preserve full source provenance and the settings represented by the image."""
        session = self.workspace.active or self._unregistered_session
        cube = graph_cube(session)
        source_path = cube.source_path or (
            str(self.current_dat_path) if self.current_dat_path else ""
        )
        return {
            "source_path": source_path,
            'graph_id': (self.workspace.active or self._unregistered_session).active_graph_id,
            'graph_name': (self.workspace.active or self._unregistered_session).active_graph.name,
            'source_mode': session.active_graph.source_mode,
            'gate_indices': session.active_graph.gate_indices,
            "source_stem": Path(source_path).stem if source_path else "heatmap",
            "x_limits": [float(value) for value in self._heat_ax.get_xlim()],
            "y_limits": [float(value) for value in self._heat_ax.get_ylim()],
            "color_limits": [float(value) for value in self._heat_mesh.get_clim()],
            "color_scale_mode": self._color_scale_mode,
            "color_region_boundaries": [float(value) for value in self._color_region_boundaries],
            "color_region_limits": [list(map(float, mesh.get_clim())) for mesh in self._heat_meshes],
            "x_label": self._heat_ax.get_xlabel(),
            "y_label": self._heat_ax.get_ylabel(),
            "signal_label": (
                self._heat_signal_label.get_text() if self._heat_signal_label is not None
                else self._heat_colorbars[-1].ax.get_xlabel().split(" · ", 1)[-1]
            ),
            "colormap": self._heat_mesh.get_cmap().name,
            "axis_original_type": cube.x_original_type,
            "axis_original_label": cube.x_original_label,
            "axis_override": cube.x_override,
            "axis_detection_source": cube.x_detection_source,
            "axis_detection_note": cube.x_detection_note,
            "axis_is_ambiguous": cube.x_is_ambiguous,
            "photon_energy_note": cube.photon_energy_note,
            "processing": (
                "energy_second_derivative"
                if session.active_graph.source_mode == 'processed' and self._processing_mode == "energy_second_derivative" else "raw"
            ),
            "processing_smooth": bool(self._processing_smooth),
            "processing_window_length": int(self._processing_window_length),
            "plot_title": self._heat_ax.get_title(),
            'norm': self._current_norm_spec().to_dict(),
            'presentation': (self.workspace.active or self._unregistered_session).presentation.to_dict(),
        }

    def _heatmap_export_filename(self, extension='png', session=None) -> str:
        """Keep source conditions readable and number exports in the target folder."""
        session = session or self.workspace.active or self._unregistered_session
        source = session.source_path or (Path(session.current_cube.source_path) if session.current_cube.source_path else None)
        source_stem = source.stem if source else 'heatmap'
        stem = graph_export_stem(source_stem, session.active_graph_id, session.active_graph.name)
        folder = self._output_folder(session)
        index = 1
        while (folder / f"{stem}_export{index:02d}.{extension}").exists():
            index += 1
        filename = f"{stem}_export{index:02d}.{extension}"
        if len(filename.encode("utf-16-le")) // 2 > 255:
            raise ValueError("The source or graph name is too long for an export filename. Shorten the graph name or source filename.")
        return filename

    @staticmethod
    def _copy_title_settings(settings: dict) -> dict:
        return json.loads(json.dumps(settings))

    def _load_title_preferences(self) -> tuple[dict, dict]:
        presets = {}
        try:
            stored = json.loads(self._annotation_settings.value("plot_titles/presets", "{}", type=str))
            if isinstance(stored, dict):
                for name, preset in stored.items():
                    try:
                        validate_preset(preset)
                        if name == preset["name"] and name not in ("Manual", "Current group"):
                            presets[name] = preset
                    except (ValueError, TypeError):
                        continue
        except (ValueError, TypeError):
            pass
        try:
            default = json.loads(self._annotation_settings.value("plot_titles/selection", "null", type=str))
            validate_title_settings(default)
            default["manual_override"] = None
        except (ValueError, TypeError):
            default = manual_settings()
        return presets, default

    def _build_title_controls(self, form: QFormLayout) -> None:
        self.title_preset_combo = QComboBox()
        form.addRow("Title preset:", self.title_preset_combo)
        self.title_template_panel = QWidget()
        editor = QFormLayout(self.title_template_panel)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.setSpacing(3)
        self.title_pattern_edit = QLineEdit()
        self.title_pattern_edit.setToolTip(
            "Arrange any fields: {voltage}, {field}, {angle}, {valley}, {temperature}, {sample}. "
            "Add units as text. Use {{ and }} for literal braces. No expressions or formatting codes.")
        editor.addRow("Format:", self.title_pattern_edit)
        fields = QVBoxLayout()
        self.title_field_combo = QComboBox()
        self.title_field_combo.addItems(FIELDS)
        insert = QPushButton("Insert field")
        insert.clicked.connect(lambda: self.title_pattern_edit.insert("{" + self.title_field_combo.currentText() + "}"))
        fields.addWidget(self.title_field_combo)
        fields.addWidget(insert)
        editor.addRow("Fields:", fields)
        self.title_angle_map_edit = QLineEdit()
        self.title_angle_map_edit.setPlaceholderText("85=K;175=Kp (optional)")
        self.title_angle_map_edit.setToolTip("Map filename angles to valley labels for this preset only. Separate entries with semicolons.")
        editor.addRow("Angle map:", self.title_angle_map_edit)
        actions = QVBoxLayout()
        self.title_preset_name_edit = QLineEdit()
        self.title_preset_name_edit.setPlaceholderText("Preset name")
        self.title_save_button = QPushButton("Save preset")
        self.title_save_button.clicked.connect(self._save_title_preset)
        self.title_apply_button = QPushButton("Apply template")
        self.title_apply_button.clicked.connect(self._apply_current_title_template)
        actions.addWidget(self.title_preset_name_edit)
        actions.addWidget(self.title_save_button)
        actions.addWidget(self.title_apply_button)
        editor.addRow(actions)
        form.addRow(self.title_template_panel)
        self.title_template_status = QLabel()
        self.title_template_status.setWordWrap(True)
        form.addRow(self.title_template_status)
        self._show_title_settings()
        self.title_preset_combo.currentIndexChanged.connect(self._on_title_preset_selected)
        self.title_pattern_edit.textChanged.connect(self._on_title_template_edited)
        self.title_angle_map_edit.textChanged.connect(self._on_title_template_edited)

    def _show_title_settings(self) -> None:
        settings = self._title_settings
        preset = settings["preset"] if settings["mode"] == "template" else BUILTIN_PRESET
        selected = preset["name"] if settings["mode"] == "template" else "Manual"
        with QSignalBlocker(self.title_preset_combo):
            self.title_preset_combo.clear()
            self.title_preset_combo.addItem("Manual", None)
            self.title_preset_combo.addItem("Current group", BUILTIN_PRESET)
            for name, saved in self._title_presets.items():
                self.title_preset_combo.addItem(name, saved)
            index = self.title_preset_combo.findText(selected)
            if index < 0:
                self.title_preset_combo.addItem(selected, preset)
                index = self.title_preset_combo.count() - 1
            self.title_preset_combo.setCurrentIndex(index)
        with QSignalBlocker(self.title_pattern_edit), QSignalBlocker(self.title_angle_map_edit):
            self.title_pattern_edit.setText(preset["pattern"])
            self.title_angle_map_edit.setText(";".join(f"{angle}={label}" for angle, label in preset["angle_map"].items()))
        self.title_preset_name_edit.setText("" if selected in ("Manual", "Current group") else selected)
        active = settings["mode"] == "template"
        self.title_template_panel.setVisible(active)
        self.title_template_status.setVisible(active)
        notice = "Load a DAT to preview this template."
        if settings["manual_override"] is not None:
            notice = "Manual override for this DAT; Apply template to regenerate."
        elif active and self.current_cube is not None:
            try:
                notice = "Preview: " + render_title(self.current_cube.source_path or "", preset)
            except ValueError as exc:
                notice = str(exc)
        self.title_template_status.setText(notice)

    def _remember_title_selection(self) -> None:
        self._default_title_settings = self._copy_title_settings(self._title_settings)
        self._default_title_settings["manual_override"] = None
        self._annotation_settings.setValue("plot_titles/selection", json.dumps(self._default_title_settings))
        self._sync_annotation_settings()

    def _on_title_preset_selected(self, _index: int) -> None:
        with self._edit_transaction('Title preset', TITLE_FIELDS):
            preset = self.title_preset_combo.currentData()
            self._title_settings = (manual_settings(self.plot_title_edit.text()) if preset is None else
                                    {"mode": "template", "preset": self._copy_title_settings(preset), "manual_override": None})
            self._show_title_settings()
            self._remember_title_selection()
            if preset is not None:
                self._apply_title_template()

    def _edited_title_preset(self, name: str | None = None) -> dict:
        preset = {"name": name or self._title_settings["preset"]["name"],
                  "pattern": self.title_pattern_edit.text(),
                  "angle_map": parse_angle_map(self.title_angle_map_edit.text())}
        return validate_preset(preset)

    def _on_title_template_edited(self, *_args) -> None:
        if self._title_settings["mode"] != "template":
            return
        try:
            preset = self._edited_title_preset()
        except ValueError as exc:
            self._title_settings["manual_override"] = ""
            self._set_title_text("")
            self.title_template_status.setText(str(exc))
            return
        self._begin_history_text('Title template', TITLE_FIELDS)
        self._title_settings = {"mode": "template", "preset": preset, "manual_override": None}
        self._remember_title_selection()
        self._apply_title_template()

    def _apply_current_title_template(self) -> None:
        with self._edit_transaction('Apply title template', TITLE_FIELDS):
            try:
                preset = self._edited_title_preset()
            except ValueError as exc:
                self.title_template_status.setText(str(exc))
                return
            self._title_settings = {"mode": "template", "preset": preset, "manual_override": None}
            self._apply_title_template()

    def _save_title_preset(self) -> None:
        with self._edit_transaction('Title preset', TITLE_FIELDS):
            name = self.title_preset_name_edit.text().strip()
            try:
                if not name or name in ("Manual", "Current group"):
                    raise ValueError("Choose a name other than Manual or Current group.")
                preset = self._edited_title_preset(name)
            except ValueError as exc:
                self.title_template_status.setText(str(exc))
                return
            self._title_presets[name] = preset
            self._annotation_settings.setValue("plot_titles/presets", json.dumps(self._title_presets))
            self._title_settings = {"mode": "template", "preset": preset, "manual_override": None}
            self._show_title_settings()
            self._remember_title_selection()
            self._apply_title_template()

    def _set_title_text(self, text: str) -> None:
        with QSignalBlocker(self.plot_title_edit):
            self.plot_title_edit.setText(text)
        (self.workspace.active or self._unregistered_session).view.annotations['plot_title'] = text
        self._update_plot_annotations()

    def _apply_title_template(self) -> None:
        self._title_settings["manual_override"] = None
        if self.current_cube is None:
            self._set_title_text("")
            self.title_template_status.setText("Load a DAT to preview this template.")
            return
        try:
            title = render_title(self.current_cube.source_path or "", self._title_settings["preset"])
            notice = "Preview: " + title
        except ValueError as exc:
            title, notice = "", str(exc)
        self._set_title_text(title)
        self.title_template_status.setText(notice)

    def _initialize_plot_annotations(self) -> None:
        cube = self.current_cube
        source = cube.source_path or id(cube)
        session = self.workspace.active
        if session is not None:
            if source != self._title_source or source != self._annotation_source:
                self._title_source = self._annotation_source = source
                self._show_title_settings()
                for name, edit in self._annotation_edits.items():
                    with QSignalBlocker(edit):
                        edit.setText(session.view.annotations[name])
                with QSignalBlocker(self.signal_title_auto_chk), QSignalBlocker(self.signal_title_combo):
                    self.signal_title_auto_chk.setChecked(session.view.signal_title_auto)
                    self.signal_title_combo.setCurrentText(session.view.annotations['signal_title'])
            self._refresh_auto_signal_title()
            return
        if source != self._title_source:
            self._title_source = source
            self._title_settings = self._copy_title_settings(self._default_title_settings)
            self._show_title_settings()
            if self._title_settings["mode"] == "template":
                self._apply_title_template()
            else:
                text = (self._annotation_settings.value("plot_annotations/plot_title", "", type=str)
                        if self.fix_annotations_chk.isChecked() else "")
                self._set_title_text(text)
        if source == self._annotation_source:
            self._refresh_auto_signal_title()
            return
        self._annotation_source = source
        match = re.fullmatch(r"(.*?)\s*\(([^()]*)\)\s*", cube.gate_label)
        name, unit = match.groups() if match else (cube.gate_label, "")
        for field, text in (("y_axis_name", name), ("y_axis_unit", unit)):
            # Key presence distinguishes an intentional blank from a source default.
            if (self.fix_annotations_chk.isChecked()
                    and self._annotation_settings.contains(f"plot_annotations/{field}")):
                text = self._annotation_settings.value(f"plot_annotations/{field}", "", type=str)
            edit = self._annotation_edits[field]
            edit.blockSignals(True)
            edit.setText(text)
            edit.blockSignals(False)
        with QSignalBlocker(self.signal_title_auto_chk):
            self.signal_title_auto_chk.setChecked(self._annotation_settings.value(
                "plot_annotations/signal_auto",
                not self._annotation_settings.contains("plot_annotations/signal_title"), type=bool)
                if self.fix_annotations_chk.isChecked() else True)
        if not self.signal_title_auto_chk.isChecked():
            with QSignalBlocker(self.signal_title_combo):
                self.signal_title_combo.setCurrentText(self._annotation_settings.value(
                    "plot_annotations/signal_title", self.signal_title_combo.currentText(), type=str))
        self._refresh_auto_signal_title()

    def _refresh_auto_signal_title(self) -> None:
        if not self.signal_title_auto_chk.isChecked():
            self.signal_title_combo.setToolTip("Manual colorbar label. Enable Auto from data to use signal metadata or filename.")
            return
        cube = self._base_cube or self.current_cube
        kind = cube.signal_kind if cube is not None else "unknown"
        if kind == "unknown" and cube is not None:
            kind = infer_signal_kind(cube.source_path)
        source = cube.signal_source_kind if cube is not None else 'unknown'
        if source == 'unknown' and cube is not None:
            source = infer_source_signal_kind(cube.source_path or '')
        session = self.workspace.active or self._unregistered_session
        derivative = session.active_graph.source_mode == 'processed' and self._processing_mode == "energy_second_derivative"
        with QSignalBlocker(self.signal_title_combo):
            self.signal_title_combo.setCurrentText(automatic_signal_label(source, derivative, kind == 'energy_second_derivative'))
        (self.workspace.active or self._unregistered_session).view.annotations['signal_title'] = self.signal_title_combo.currentText()
        self.signal_title_combo.setToolTip(
            "Signal unrecognized; neutral Signal label. Edit to set a manual title."
            if source == "unknown" else "Automatic signal title from explicit metadata, filename, or applied processing.")

    def _on_signal_auto_toggled(self, checked: bool) -> None:
        self._finish_annotation_edit(render=False)
        with self._edit_transaction('Automatic signal title', ('view.signal_title_auto', 'view.annotations.signal_title')):
            if self.fix_annotations_chk.isChecked():
                self._annotation_settings.setValue("plot_annotations/signal_auto", checked)
                self._sync_annotation_settings()
            self._refresh_auto_signal_title()
            self._update_plot_annotations()

    def _save_plot_annotation(self, field: str, text: str) -> None:
        names = (TITLE_FIELDS if field == 'plot_title' else
                 ('view.annotations.signal_title', 'view.signal_title_auto') if field == 'signal_title' else
                 ('view.annotations.' + field,))
        self._begin_history_text('Edit ' + field.replace('_', ' '), names)
        if field == "signal_title":
            # Avoid the auto-toggle handler doing a synchronous layout while typing.
            with QSignalBlocker(self.signal_title_auto_chk):
                self.signal_title_auto_chk.setChecked(False)
            if self.fix_annotations_chk.isChecked():
                self._annotation_settings.setValue("plot_annotations/signal_auto", False)
        if field == "plot_title":
            self._title_settings["manual_override"] = text
            if self._title_settings["mode"] == "template":
                self.title_template_status.setText("Manual override for this DAT; Apply template to regenerate.")
        if self.fix_annotations_chk.isChecked() and not (
                field == "plot_title" and self._title_settings["mode"] == "template"):
            self._annotation_settings.setValue(f"plot_annotations/{field}", text)
        # Retire export bytes once per draft, retaining the last displayed page.
        # Enter/focus-out (or explicit capture) is the render boundary.
        first_edit = not self._annotation_edit_pending
        can_export = first_edit and self.plot_page.rendered_page is not None
        self._annotation_edit_pending = True
        self._plot_render_timer.stop()
        self._update_plot_annotations(layout=False, invalidate=first_edit)
        if can_export:
            # A valid text draft can be committed by Export itself. The
            # controller prepares fresh bytes before writing, including when
            # the click's FocusOut has already committed the text.
            self.plot_page.export_svg_btn.setEnabled(True)
            self.plot_page.export_png_btn.setEnabled(True)
            self.plot_page.copy_graph_btn.setEnabled(True)
        self._refresh_graph_commands()

    def _finish_annotation_edit(self, *, render: bool = True) -> None:
        if not self._annotation_edit_pending:
            return
        self._annotation_edit_pending = False
        self._annotation_edit_timer.stop()
        self._sync_annotation_settings()
        if render:
            visible = self.plot_tabs.currentWidget() is self.graph_documents
            self._annotation_layout_pending = visible
            self._update_plot_annotations(layout=not visible, invalidate=False)
            self._schedule_plot_page_render(delay=0)

    def _on_fix_annotations_toggled(self, checked: bool) -> None:
        with self._edit_transaction('Fix annotations', ('view.fix_annotations',)):
            self._annotation_settings.setValue("plot_annotations/fixed", checked)
            if checked:
                self._annotation_settings.setValue("plot_annotations/signal_auto", self.signal_title_auto_chk.isChecked())
                for field, edit in self._annotation_edits.items():
                    if field == "plot_title" and self._title_settings["mode"] == "template":
                        continue
                    self._annotation_settings.setValue(f"plot_annotations/{field}", edit.text())
                self._annotation_settings.setValue(
                    "plot_annotations/signal_title", self.signal_title_combo.currentText())
            self._sync_annotation_settings()

    def _sync_annotation_settings(self) -> None:
        self._annotation_settings.sync()
        if self._annotation_settings.status() != QSettings.NoError:
            self.statusBar().showMessage("Could not save plot annotations for the next session.")

    def _update_plot_annotations(self, *, layout: bool = True, invalidate: bool = True) -> None:
        owner = self._plot_owner_token()
        page = self.plot_page.rendered_page
        cached_owner, cached_frame = getattr(self, '_annotation_auto_frame', (None, None))
        frame = (page.plot_size_in if page is not None and page.owner_token == owner
                 else cached_frame if cached_owner == owner else None)
        if invalidate:
            self._invalidate_plot_page()
        if frame is not None:
            # Annotation typing retires export bytes, while the current Auto
            # frame remains visible until the replacement page is prepared.
            self._annotation_auto_frame = (self._plot_owner_token(), frame)
        if self._heat_ax is None:
            return
        self._heat_ax.title.set_text(self.plot_title_edit.text())
        self._heat_ax.title.set_parse_math(False)
        name, unit = self.y_axis_name_edit.text(), self.y_axis_unit_edit.text()
        self._heat_ax.set_ylabel(f"{name} ({unit})" if unit else name, parse_math=False)
        self._apply_live_heatmap_style()
        if not layout:
            self._position_live_heatmap_title()
        if layout:
            self._layout_live_plots()
            self.canvas.draw_idle()

    def _schedule_view_layout(self) -> None:
        # Color values update immediately; renderer-based label measurements can
        # wait until a burst of pan/zoom events has settled.
        if not hasattr(self, "_view_layout_timer"):
            self._view_layout_timer = QTimer(self)
            self._view_layout_timer.setSingleShot(True)
            self._view_layout_timer.timeout.connect(lambda: self._layout_live_plots())
            self._view_layout_timer.timeout.connect(self.canvas.draw_idle)
        self._view_layout_timer.start(40)

    def _sync_display_zoom(self, zoom):
        with QSignalBlocker(self.analysis_zoom_combo):
            self.analysis_zoom_combo.setCurrentText(zoom)
        self._layout_live_plots()
        self.canvas.draw_idle()

    def _on_display_scale_changed(self, _scale):
        self._layout_live_plots()
        self.canvas.draw_idle()

    def _live_heatmap_frame_size(self, saved):
        # Legacy Auto follows only a successful page from this exact owner.
        if saved.frame_width_in is not None:
            return saved.frame_width_in, saved.frame_height_in
        page = self.plot_page.rendered_page
        owner = self._plot_owner_token()
        cached_owner, cached_frame = getattr(self, '_annotation_auto_frame', (None, None))
        return (page.plot_size_in if page is not None and page.owner_token == owner
                else cached_frame if cached_owner == owner
                else (PlotPresentation().frame_width_in, PlotPresentation().frame_height_in))

    def _analysis_display_scale(self):
        zoom = self.plot_page.zoom_combo.currentText()
        if zoom != 'Fit':
            return int(zoom.rstrip('%')) / 100
        # Before the window is shown Qt supplies placeholder viewport geometry.
        # Hidden Analysis tabs in a shown window keep their own real viewport.
        if not self.isVisible():
            return 1.
        saved = (self.workspace.active or self._unregistered_session).presentation
        frame_width, frame_height = self._live_heatmap_frame_size(saved)
        header = max(.4, saved.text_pt/72*2 + saved.colorbar.height_in
                     + saved.colorbar.frame_gap_in + .06)
        if len(self._heat_colorbars) > 1:
            header += .2
        viewport = self.analysis_scroll.viewport().size()
        # Fit the Analysis footprint, including its fixed spectrum reserve.
        # Never measure the grown canvas: that would feed zoom back into Fit.
        return max(.1, min((viewport.width()-12) / (96*(frame_width+1.35)),
                           (viewport.height()-180-12) / (96*(frame_height+header+.85))))

    def _live_display_presentation(self):
        saved = (self.workspace.active or self._unregistered_session).presentation
        # Matplotlib's figure and fonts use physical pixels; Qt controls and
        # the SVG preview use logical pixels. Apply DPR exactly once here.
        factor = 96 * self._analysis_display_scale() * self.canvas.device_pixel_ratio / self.figure.dpi
        return replace(saved, numeric_pt=saved.numeric_pt*factor, text_pt=saved.text_pt*factor,
                       axis=replace(saved.axis, border_width=saved.axis.border_width*factor),
                       colorbar=replace(saved.colorbar,
                                        width_in=saved.colorbar.width_in*factor,
                                        height_in=saved.colorbar.height_in*factor,
                                        gap_in=saved.colorbar.gap_in*factor,
                                        frame_gap_in=saved.colorbar.frame_gap_in*factor)), factor

    def _apply_live_heatmap_style(self) -> None:
        if self._heat_ax is None:
            return
        presentation, factor = self._live_display_presentation()
        self._live_heatmap_style_error = None
        try:
            apply_heatmap_axis_style(self._heat_ax, presentation, display_scale=factor)
        except ValueError as exc:
            self._live_heatmap_style_error = str(exc)
            # Keep the interactive viewer usable while the formal page reports
            # an invalid manual tick choice on a non-linear coordinate axis.
            axis = presentation.axis
            values = {f'{prefix}_major_{kind}': None for prefix, scale in (
                ('x', self._heat_ax.get_xscale()), ('y', self._heat_ax.get_yscale()))
                if scale != 'linear' for kind in ('step', 'count')}
            values.update({f'{prefix}_minor_count': 1 for prefix, scale in (
                ('x', self._heat_ax.get_xscale()), ('y', self._heat_ax.get_yscale()))
                if scale != 'linear' and getattr(axis, f'{prefix}_minor_count') not in (0, 1)})
            apply_heatmap_axis_style(self._heat_ax, replace(presentation, axis=replace(axis, **values)),
                                     display_scale=factor)
        regional = len(self._heat_colorbars) > 1
        self.plot_page.set_colorbar_norms({
            f'region-{index+1}' if regional else 'global': bar.mappable.norm
            for index, bar in enumerate(self._heat_colorbars)})
        signal = (display_signal_label(self.signal_title_combo.currentText(), presentation.colorbar.show_unit)
                  if presentation.colorbar.show_label else '')
        for index, bar in enumerate(self._heat_colorbars):
            bar_id = f'region-{index+1}' if regional else 'global'
            apply_heatmap_colorbar_style(bar, presentation, bar_id, allow_invalid_custom=True, display_scale=factor)
            bar.ax.set_visible(presentation.colorbar.visible)
            bar.ax.set_xlabel('' if regional else signal, parse_math=False)
            bar.ax.xaxis.set_label_coords(-.10, .8)
            bar.ax.xaxis.label.set_horizontalalignment('right')
            bar.ax.xaxis.label.set_verticalalignment('center')
        if self._heat_signal_label is not None:
            self._heat_signal_label.set_text(signal)
            self._heat_signal_label.set_fontsize(presentation.text_pt)
            self._heat_signal_label.set_fontfamily([presentation.font_family, 'DejaVu Sans'])
            self._heat_signal_label.set_visible(presentation.colorbar.visible)

    def _layout_live_plots(self, _event=None) -> None:
        if getattr(self, '_laying_out_live_plots', False):
            return
        self._laying_out_live_plots = True
        try:
            self._position_live_plots()
        finally:
            self._laying_out_live_plots = False

    def _update_live_heatmap_tick_contrast(self, event) -> None:
        if self._heat_ax is None or getattr(self, '_laying_out_live_plots', False):
            return
        if _update_heatmap_tick_contrast(self._heat_ax, event.renderer):
            # Qt ignores draw_idle during its active draw. Coalesce one repaint
            # after this callback so a palette/norm-only change reaches pixels.
            if not hasattr(self, '_tick_contrast_timer'):
                self._tick_contrast_timer = QTimer(self)
                self._tick_contrast_timer.setSingleShot(True)
                self._tick_contrast_timer.timeout.connect(self.canvas.draw_idle)
            if not self._tick_contrast_timer.isActive():
                self._tick_contrast_timer.start(0)

    def _position_live_plots(self) -> None:
        if hasattr(self, "_view_layout_timer"):
            self._view_layout_timer.stop()
        if self._heat_ax is None or not self._heat_colorbars:
            return
        saved = (self.workspace.active or self._unregistered_session).presentation
        presentation, factor = self._live_display_presentation()
        colorbar_style = presentation.colorbar
        self._apply_live_heatmap_style()
        bars = self._heat_colorbars
        regional = len(bars) > 1
        frame_size = self._live_heatmap_frame_size(saved)
        plot_width, plot_height = (value*factor for value in frame_size)
        logical_scale = 96*self._analysis_display_scale()
        header_reserve = max(.4*logical_scale, saved.text_pt/72*logical_scale*2
                             + (saved.colorbar.height_in+saved.colorbar.frame_gap_in+.06)*logical_scale)
        if regional:
            header_reserve += .2*logical_scale
        # The canvas grows and scrolls instead of reducing the heatmap. The
        # lower spectrum remains an interactive 12 pt view with a fixed reserve.
        minimum_width = round(frame_size[0]*logical_scale + 1.35*logical_scale)
        minimum_height = round(frame_size[1]*logical_scale + header_reserve + .85*logical_scale + 180)
        if self.canvas.minimumWidth() != minimum_width or self.canvas.minimumHeight() != minimum_height:
            self.canvas.setMinimumSize(minimum_width, minimum_height)
        if self.canvas.width() < minimum_width or self.canvas.height() < minimum_height:
            self.canvas.resize(max(self.canvas.width(), minimum_width), max(self.canvas.height(), minimum_height))
        width, height = self.figure.get_size_inches()
        left = (width - plot_width) / 2
        bottom = (180 + .70*logical_scale)*self.canvas.device_pixel_ratio/self.figure.dpi
        self._heat_ax.set_position([left / width, bottom / height, plot_width / width, plot_height / height])
        self.figure.draw_without_rendering()
        renderer = self.canvas.get_renderer()
        _align_heatmap_corner_tick_labels(self._heat_ax, renderer)
        bar_widths = [colorbar_style.width_in] * len(bars)
        bar_gaps = [colorbar_style.gap_in] * (len(bars)-1)
        strip_width = sum(bar_widths) + sum(bar_gaps)
        if colorbar_style.alignment == 'left':
            bar_left = left
        elif colorbar_style.alignment == 'center':
            bar_left = left + (plot_width - strip_width) / 2
        else:
            bar_left = left + plot_width - strip_width
        bar_bottom = bottom + plot_height + colorbar_style.frame_gap_in
        bar_height = colorbar_style.height_in
        cursor = bar_left
        for index, colorbar in enumerate(bars):
            colorbar.ax.set_position([
                cursor / width, bar_bottom / height,
                bar_widths[index] / width, bar_height / height,
            ])
            cursor += bar_widths[index] + (bar_gaps[index] if index < len(bar_gaps) else 0.)
        if regional:
            signal_label = (display_signal_label(self.signal_title_combo.currentText(), colorbar_style.show_unit)
                            if colorbar_style.show_label else '')
            if self._heat_signal_label is None:
                self._heat_signal_label = self.figure.text(
                    0.0, 0.0, signal_label, ha="right", va="bottom",
                    fontsize=presentation.text_pt, fontfamily=[presentation.font_family, 'DejaVu Sans'],
                    gid="heatmap_signal_label",
                )
            self._heat_signal_label.set_text(signal_label)
            self._heat_signal_label.set_fontsize(presentation.text_pt)
            self._heat_signal_label.set_visible(colorbar_style.visible)
            self._heat_signal_label.set_position(
                ((bar_left - 0.04*factor) / width,
                 (bar_bottom + bar_height / 2) / height)
            )
            self._heat_signal_label.set_horizontalalignment("right")
            self._heat_signal_label.set_verticalalignment("center")
        elif self._heat_signal_label is not None:
            self._heat_signal_label.remove()
            self._heat_signal_label = None
        self._heat_ax.set_title(self.plot_title_edit.text(), fontsize=presentation.text_pt,
                                parse_math=False, y=.98, pad=0)
        if not regional and colorbar_style.visible:
            # Preserve the signal's clearance independently of overlay text.
            signal = bars[0].ax.xaxis.label
            if signal.get_text():
                renderer = self.canvas.get_renderer()
                bars[0].ax.get_tightbbox(renderer)
                frame = self._heat_ax.get_window_extent(renderer)
                bounds = signal.get_window_extent(renderer)
                rise = max(0., frame.y1 + .02 * factor * self.figure.dpi - bounds.y0)
                offset = self._heat_ax.yaxis.get_offset_text()
                if offset.get_text() and bounds.overlaps(offset.get_window_extent(renderer)):
                    rise = max(rise, offset.get_window_extent(renderer).y1 + .02*factor*self.figure.dpi - bounds.y0)
                if rise:
                    x, y = signal.get_position()
                    bars[0].ax.xaxis.set_label_coords(x, y + rise / bars[0].ax.get_window_extent(renderer).height)
        # Reserve space for the heatmap X label and the 12 pt spectrum title.
        spectrum_bottom = 55*self.canvas.device_pixel_ratio/self.figure.dpi
        spectrum_height = 95*self.canvas.device_pixel_ratio/self.figure.dpi
        self._spec_ax.set_position([
            min(.75, width * .2) / width, spectrum_bottom / height,
            max(.1, width - 1.05) / width, spectrum_height / height,
        ])
        if regional:
            self.figure.draw_without_rendering()
            renderer = self.canvas.get_renderer()
            offset = self._heat_ax.yaxis.get_offset_text()
            offset_bounds = offset.get_window_extent(renderer) if offset.get_visible() and offset.get_text() else None
            boxes = [tick.get_window_extent(renderer) for bar in bars for tick in bar.ax.get_xticklabels()
                     if tick.get_visible() and tick.get_text()]
            top = max([bar.ax.get_window_extent(renderer).y1 for bar in bars]
                      + [box.y1 for box in boxes] + ([offset_bounds.y1] if offset_bounds is not None else []))
            signal = self._heat_signal_label
            signal.set_horizontalalignment('right'); signal.set_verticalalignment('bottom')
            signal.set_position(self.figure.transFigure.inverted().transform(
                (self._heat_ax.get_window_extent(renderer).x1, top+.02*factor*self.figure.dpi)))
        self._position_live_heatmap_title()

        _update_heatmap_tick_contrast(self._heat_ax, self.canvas.get_renderer())

    def _position_live_heatmap_title(self):
        if self._heat_ax is None:
            return
        title = self._heat_ax.title
        title.set_transform(self._heat_ax.transAxes)
        title.set_horizontalalignment('left'); title.set_verticalalignment('top')
        session = self.workspace.active or self._unregistered_session
        title.set_position(session.presentation.title_position)
        title.set_clip_path(self._heat_ax.patch)
        title.set_clip_on(True)
        title.set_in_layout(False)

    def _on_export_png(self) -> None:
        self._on_export_plot_page('png')

    def _on_create_analysis_graph(self) -> None:
        try:
            self._require_project_idle()
            # Current analysis controls are drafts, never historical fit/detection settings.
            for _, name, _, _ in ANALYSIS_BINDINGS:
                control = getattr(self, name)
                if isinstance(control, QAbstractSpinBox) and control.isEnabled() and not control.hasAcceptableInput():
                    raise ValueError('Correct the pending numeric editor before creating an analysis graph.')
            self._finish_current_document()
            session = self.workspace.active
            if session is None or self.current_cube is None:
                raise ValueError('Choose an imported DAT first.')
            fit = self._active_fit_result()
            indices, kinds = self._active_graph_peaks()
            if fit is None and not indices.size:
                raise ValueError('Find peaks or dips for the current Gate first.')
            gate, cube = self._current_gate_index(), self.current_cube
            key = (session.dataset_id, session.active_graph_id, gate)
            signature = (tuple(indices), tuple(kinds)) if fit is None else None
            retained = self._dat_analysis_graph_results.get(key)
            saved = (saved_result_records(self.workspace, retained[3]) if retained
                     and retained[0] is cube and retained[1] is fit and retained[2] == signature else None)
            graph_id = retained[4] if saved else None
            if saved:
                book, sheets = saved
            else:
                x, y = cube.energy, cube.z[gate]
                if fit is not None:
                    selected = np.isin(x, fit.x)
                    x, y = x[selected], y[selected]
                    if x.size == 0 or not np.array_equal(x, fit.x):
                        raise ValueError('The completed fit does not align with the current applied source coordinates.')
                source_kind = cube.signal_source_kind
                if source_kind == 'unknown':
                    source_kind = infer_source_signal_kind(cube.source_path or '')
                science = analysis_owner(session)
                y_label = automatic_signal_label(source_kind, science.applied.mode == 'energy_second_derivative',
                                                  science.base_cube.signal_kind == 'energy_second_derivative')
                provenance = dict(dataset_id=session.dataset_id, source_path=str(session.source_path or ''),
                    gate_index=gate, gate=float(cube.gate[gate]), gate_label=cube.gate_label,
                    x_label=cube.energy_label, y_label=y_label, applied_processing=science.applied.recipe(),
                    actual_interval=[float(np.nanmin(x)), float(np.nanmax(x))],
                    captured_at=datetime.now().astimezone().isoformat())
                if fit is not None:
                    provenance.update(model=fit.model, baseline=float(fit.baseline), rmse=float(fit.rmse),
                        r_squared=float(fit.r_squared) if np.isfinite(fit.r_squared) else None,
                        source_values='Applied source values before fit-only smoothing; historical fit-only smoothing settings are not retained.')
                else:
                    provenance.update(point_indices=indices.tolist(), point_kinds=['Peak' if k else 'Dip' for k in kinds],
                        source_values='Entire applied current Gate; markers use source amplitudes at retained detected/manual points. Historical detection interval and settings are not retained.')
                note = json.dumps(provenance, ensure_ascii=False, indent=2)
                options = dict(x_label=cube.energy_label, y_label=y_label, note=note)
                sheet = (fit_curve_sheet(x, y, np.ones(x.size, dtype=bool), fit, **options) if fit is not None else
                         marker_curve_sheet(x, y, indices, kinds, **options))
                book = Workbook(name=f'{cube.title} · {cube.gate_label} = {cube.gate[gate]:g}',
                                sheet_ids=[sheet.sheet_id])
                sheets = [sheet]
            graph_id = self._publish_analysis_results(book, sheets, create_graph=True,
                graph_id=graph_id, source_label='Source data (applied)', has_fit=fit is not None)
            self._dat_analysis_graph_results[key] = (cube, fit, signature,
                (book.workbook_id, tuple(s.sheet_id for s in sheets)), graph_id)
            self.statusBar().showMessage('Created analysis graph in Workspace with detached source and result curves.')
        except ValueError as exc:
            self._show_error(str(exc))

    def _on_export_current_fit_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        active_fit = self._active_fit_result()
        if active_fit is None:
            self._show_error("Run a fit for the current gate first.")
            return
        cube = self.current_cube
        gate_used, _ = self._nearest_spectrum(self.current_gate)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = self._output_folder() / f"{cube.title}_gate_{gate_used:.6g}_{active_fit.model}_fit_{stamp}.csv"
        out_path, _ = QFileDialog.getSaveFileName(self, "Export Current Gate CSV", str(default), "CSV Files (*.csv)")
        if not out_path:
            return
        rows = [self._fit_result_to_export_row(
            float(gate_used), active_fit.model, cube.energy_label, active_fit, status="ok"
        )]
        try:
            with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
                wr = csv.DictWriter(fh, fieldnames=self._export_fieldnames(rows))
                wr.writeheader()
                wr.writerows(rows)
        except Exception as exc:
            self._show_error(f"Failed to export current-gate fit: {exc}")
            return
        self.statusBar().showMessage(f"Exported current-gate fit CSV: {out_path}")

    def _on_export_all_fit_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        rows = self._all_fit_export_rows()
        if not rows:
            self._show_error("Run at least one fit first.")
            return
        cube = self.current_cube
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = self._output_folder() / f"{cube.title}_all_fit_{stamp}.csv"
        out_path, _ = QFileDialog.getSaveFileName(self, "Export All Results CSV", str(default), "CSV Files (*.csv)")
        if not out_path:
            return
        try:
            with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
                wr = csv.DictWriter(fh, fieldnames=self._export_fieldnames(rows))
                wr.writeheader()
                wr.writerows(rows)
        except Exception as exc:
            self._show_error(f"Failed to export all fit results: {exc}")
            return
        self.statusBar().showMessage(f"Exported all fit results CSV: {out_path}")

    def _on_export_spectrum_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        gate_used, y = self._nearest_spectrum(self.current_gate)
        x = self.current_cube.energy
        try:
            _xr, y_analysis, mask = self._analysis_xy(
                x, y,
                smooth=self.fit_smooth_chk.isChecked(),
                smooth_window=int(self.fit_smooth_win_spin.value()),
            )
        except Exception:
            y_analysis = np.asarray([], dtype=float)
            mask = self._fit_mask(x)
        default = self._output_folder() / f"{self.current_cube.title}_gate_{gate_used:.6g}_spectrum.csv"
        out_path, _ = QFileDialog.getSaveFileName(self, "Export Spectrum CSV", str(default), "CSV Files (*.csv)")
        if not out_path:
            return
        try:
            with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
                wr = csv.writer(fh)
                wr.writerow([
                    "analysis_x",
                    "intensity",
                    "in_fit_range",
                    "analysis_intensity",
                    "analysis_axis",
                    "original_x",
                    "original_axis",
                ])
                pos = 0
                for i in range(x.size):
                    in_range = bool(mask[i])
                    a_val = ""
                    if in_range and pos < y_analysis.size:
                        a_val = f"{float(y_analysis[pos]):.10g}"
                        pos += 1
                    wr.writerow([
                        f"{x[i]:.10g}",
                        f"{y[i]:.10g}",
                        int(in_range),
                        a_val,
                        self.current_cube.energy_label,
                        f"{self.current_cube.x_original[i]:.10g}",
                        self.current_cube.x_original_label,
                    ])
        except Exception as exc:
            self._show_error(f"Failed to export spectrum CSV: {exc}")
            return
        self.statusBar().showMessage(f"Exported spectrum CSV: {out_path}")

    # =========================================================================
    #  Drag-and-drop
    # =========================================================================

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths = self._drop_paths(event)
        if paths and not self._drop_busy(paths) and self._supported_drop(paths):
            event.acceptProposedAction()
            cw = self.centralWidget()
            if cw:
                self._dnd_overlay.resize(cw.size())
            label = (f'Open project: {paths[0].name}' if paths[0].suffix.lower() in ('.spectra', '.spextra')
                     else f'Import {len(paths)} data file(s) into Workspace')
            self._dnd_overlay.setText(f'Drop to {label.lower()}')
            self._dnd_overlay.setVisible(True)
            self._dnd_overlay.raise_()
            return
        if paths:
            self.statusBar().showMessage(self._drop_rejection_message())
        event.ignore()

    @staticmethod
    def _drop_paths(event) -> list[Path]:
        urls = event.mimeData().urls()
        if not urls or any(not url.isLocalFile() for url in urls):
            return []
        return [Path(url.toLocalFile()) for url in urls]

    @staticmethod
    def _supported_drop(paths: list[Path]) -> bool:
        return bool(paths) and (all(path.suffix.lower() in DATA_SUFFIXES for path in paths)
                or len(paths) == 1 and paths[0].suffix.lower() in ('.spectra', '.spextra'))

    def _drop_busy(self, paths) -> bool:
        return self._project_busy()

    def _drop_rejection_message(self) -> str:
        if self._dat_import_pending:
            return 'Data import in progress. Cancel or wait before dropping more files.'
        if self._project_busy():
            return 'Wait for the current operation or cancel it before dropping files.'
        return 'Drop numeric data files together, or one Spectra project separately. Projects and data cannot be mixed.'

    def dragLeaveEvent(self, event: Any) -> None:
        self._dnd_overlay.setVisible(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self._dnd_overlay.setVisible(False)
        paths = self._drop_paths(event)
        if not paths or self._drop_busy(paths) or not self._supported_drop(paths):
            self.statusBar().showMessage(self._drop_rejection_message())
            event.ignore()
            return
        event.acceptProposedAction()
        if paths[0].suffix.lower() in ('.spectra', '.spextra'):
            self._open_project_path(paths[0])
        else:
            self._import_data_files(paths)

    # =========================================================================
    #  Matplotlib plotting
    # =========================================================================

    def _plot_cube(self) -> None:
        session = self.workspace.active or self._unregistered_session
        cube_changed = self._plot_cube_owners.get(session.dataset_id) is not self.current_cube
        if cube_changed and session.dataset_id in self._plot_cube_owners:
            self._clear_edit_history(session)
        self._plot_cube_owners[session.dataset_id] = self.current_cube
        if cube_changed:
            self._invalidate_dataset_documents(session.dataset_id)
        else:
            self._invalidate_plot_page(kind='view')
        try:
            self._plot_cube_impl()
        except ValueError as exc:
            if self._project_installing:
                raise
            self._display_mapping_error(str(exc))

    def _plot_cube_impl(self) -> None:
        self._finish_annotation_edit(render=False)
        if self.current_cube is None:
            return
        cube = graph_cube(self.workspace.active or self._unregistered_session)
        self._view_callbacks_ready = False
        self._heat_mesh = None
        self._heat_colorbar = None
        self._heat_meshes = []
        self._heat_colorbars = []
        self._heat_signal_label = None

        # Disconnect lasso before clearing axes
        self._disconnect_lasso_selector()
        self._clear_lasso_selection_visual()

        if self._canvas_click_cid is not None:
            self.canvas.mpl_disconnect(self._canvas_click_cid)
        if self._canvas_release_cid is not None:
            self.canvas.mpl_disconnect(self._canvas_release_cid)
        if self._canvas_hover_cid is not None:
            self.canvas.mpl_disconnect(self._canvas_hover_cid)
            self._canvas_hover_cid = None
        if self._motion_cid is not None:
            self.canvas.mpl_disconnect(self._motion_cid)
            self._motion_cid = None
        self._drag_peak_arr_idx = None
        self._drag_current_x = None
        self._drag_scatter = None

        self.figure.clear()
        self._initialize_plot_annotations()
        self._heat_ax = self.figure.add_axes([0.15, 0.4, 0.65, 0.45])
        self._spec_ax = self.figure.add_axes([0.15, 0.1, 0.75, 0.17], sharex=self._heat_ax)
        self._heat_peak_artists = []
        self._heat_peak_texts = []
        self._heat_batch_peak_artists = []

        e = cube.energy
        g = cube.gate
        z = cube.z
        self._sync_region_controls()
        x_edges, slices, boundaries = self._region_layout()
        if self._color_scale_mode in ("2", "3"):
            self._color_region_boundaries = boundaries
            ranges = (list(self._last_valid_region_color_limits)
                      if len(self._last_valid_region_color_limits) == len(slices) else
                      self._regional_color_ranges(self._visible_color_values_by_region(use_spin_values=True)))
        else:
            vmin, vmax = self._ordered_pair(float(self.vmin_spin.value()), float(self.vmax_spin.value()))
            ranges = [(float(vmin), float(vmax))]
        cmap = resolve_colormap(self._selected_colormap())
        cmap = (colormaps[cmap] if isinstance(cmap, str) else cmap).with_extremes(bad=MISSING_COLOR)
        values, nonpositive_count = prepare_display_values(z, self._current_norm_spec())
        norms = [build_color_norm(self._current_norm_spec(), pair) for pair in ranges]
        y_edges = self._axis_edges(g)
        for index, (start, end) in enumerate(slices):
            im = self._heat_ax.pcolormesh(
                x_edges[start:end + 1], y_edges, values[:, start:end],
                shading="flat", cmap=cmap,
                norm=norms[index],
            )
            cax = self.figure.add_axes([0.65, 0.88 - index * 0.025, 0.15, 0.01])
            colorbar = self.figure.colorbar(im, cax=cax, orientation="horizontal")
            self._heat_meshes.append(im)
            self._heat_colorbars.append(colorbar)
        self._heat_mesh = self._heat_meshes[0]
        self._heat_colorbar = self._heat_colorbars[0]
        if self._color_scale_mode in ("2", "3"):
            self._last_valid_region_color_limits = list(ranges)
        self._last_valid_color_limits = tuple(ranges[0])
        self._update_plot_annotations(layout=False)
        self._heat_ax.set_xlabel(cube.energy_label)
        xmin, xmax = self._ordered_pair(float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        ymin, ymax = self._ordered_pair(float(self.ymin_spin.value()), float(self.ymax_spin.value()))
        self._heat_ax.set_xlim((xmin, xmax))
        self._heat_ax.set_ylim((ymin, ymax))
        self._layout_live_plots()

        self._update_spectrum_only()
        self.canvas.draw_idle()

        # Reconnect events
        self._canvas_click_cid = self.canvas.mpl_connect("button_press_event", self._on_canvas_click)
        self._canvas_release_cid = self.canvas.mpl_connect("button_release_event", self._on_canvas_release)
        self._canvas_hover_cid = self.canvas.mpl_connect("motion_notify_event", self._on_canvas_hover)

        # Reconnect lasso if mode is active
        self._connect_lasso_selector()
        self._set_canvas_cursor_for_event(None)
        self._heat_ax.callbacks.connect("xlim_changed", self._on_plot_x_limits_changed)
        self._heat_ax.callbacks.connect("ylim_changed", self._on_plot_y_limits_changed)
        self._view_callbacks_ready = True
        self._invalidate_plot_page()
        self._refresh_export_actions()

        self._refresh_color_range_status()

    def _update_spectrum_only(self) -> None:
        if self._workbench_document() is not None and hasattr(self, 'workbench_seeds'):
            self._workbench_refresh()
            self._sync_gate_controls()
            self._schedule_project_modified()
            return
        previous_view_syncing = self._view_syncing
        self._view_syncing = True
        try:
            self._update_spectrum_only_impl()
        finally:
            self._view_syncing = previous_view_syncing

    def _update_spectrum_only_impl(self) -> None:
        if self.current_cube is None or self._spec_ax is None or self._heat_ax is None:
            return
        session = self.workspace.active or self._unregistered_session
        cube = graph_cube(session)
        gate_index = int(np.argmin(np.abs(cube.gate - self.current_gate)))
        gate_used, y = float(cube.gate[gate_index]), cube.z[gate_index]
        x = cube.energy
        analysis_matches = session.active_graph.source_mode == 'processed' or session.current_cube is session.base_cube
        trace_peak_indices = self._current_trace_peak_indices() if analysis_matches else np.array([], dtype=int)
        fit_centers = self._current_fit_centers() if analysis_matches else np.array([], dtype=float)
        active_fit = self._active_fit_result() if analysis_matches else None
        self._spec_ax.clear()
        self._drag_scatter = None
        self._spec_ax.plot(x, y, color="#1f77b4", lw=1.4, label="Spectrum")
        source_label = 'Original' if session.active_graph.source_mode == 'raw' else 'Processed'
        self._spec_ax.set_title(f"Spectrum @ {gate_used:.6g} · {source_label}", fontsize=PLOT_FONT_SIZE)
        self._spec_ax.set_xlabel(self.current_cube.energy_label)
        self._spec_ax.set_ylabel("Intensity (arb. units)")
        self._spec_ax.grid(alpha=0.25)

        xmin, xmax = self._fit_range_bounds()
        self._spec_ax.axvspan(xmin, xmax, color="#66c2a5", alpha=0.12, zorder=0, label="Fit Range")

        # ── Second-derivative overlay ─────────────────────────────────────────
        d2_trace = self._current_second_derivative_trace(x, y) if analysis_matches else None
        if d2_trace is not None:
            d2_xr, d2y = d2_trace
            if d2_xr.size == d2y.size and d2_xr.size > 0:
                view_xlo = float(self.xmin_spin.value())
                view_xhi = float(self.xmax_spin.value())
                vy = y[(x >= view_xlo) & (x <= view_xhi)]
                y0_v = float(np.nanmin(vy)) if vy.size else 0.0
                y1_v = float(np.nanmax(vy)) if vy.size else 1.0
                d2_min, d2_max = float(np.nanmin(d2y)), float(np.nanmax(d2y))
                if abs(d2_max - d2_min) > 1e-30:
                    d2_norm = (d2y - d2_min) / (d2_max - d2_min) * (y1_v - y0_v) + y0_v
                    self._spec_ax.plot(
                        d2_xr, d2_norm, color="#9c27b0", lw=0.9, ls="--", alpha=0.55,
                        label="2nd Deriv (scaled)"
                    )

        # ── Peak scatter ──────────────────────────────────────────────────────
        sel_idx = self._selected_peak_array_index()
        current_gate_idx = self._current_gate_index()
        lasso_energy_idxs = {int(ei) for gi, ei in self._lasso_selected_peak_ids if int(gi) == int(current_gate_idx)}
        view_xmin, view_xmax = self._ordered_pair(float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        y0, y1 = self._auto_spectrum_ylim(x, y, (view_xmin, view_xmax))

        if trace_peak_indices.size > 0:
            px = x[trace_peak_indices]
            py = y[trace_peak_indices]
            trace_kinds = np.asarray(
                [self.peak_kind_lookup.get(int(ei), True) for ei in trace_peak_indices], dtype=bool
            )
            gate_idx_cur = self._current_gate_index()
            trace_labels = [
                self._resolved_peak_label(gate_idx_cur, int(ei)) for ei in trace_peak_indices
            ]
            trace_colors = [self._peak_label_color(lbl) for lbl in trace_labels]
            # Vertical dashed lines: colored by label
            for xi_v, col_v in zip(px, trace_colors):
                self._spec_ax.vlines(
                    xi_v, y0, y1, colors=col_v, linestyles=":", linewidth=0.9, alpha=0.55, zorder=2
                )
            has_lasso_drawn = False
            legend_labels_added: set[int] = set()
            for i, (xi, yi) in enumerate(zip(px, py)):
                is_peak = bool(trace_kinds[i])
                marker = "^" if is_peak else "v"
                lbl = trace_labels[i]
                base_color = trace_colors[i]
                if int(trace_peak_indices[i]) in lasso_energy_idxs:
                    self._spec_ax.scatter(
                        [xi], [yi], s=110, marker=marker,
                        facecolors="#fff176", edgecolors="#ff00c8", linewidths=1.6, zorder=6,
                    )
                    has_lasso_drawn = True
                elif sel_idx is not None and i == sel_idx:
                    self._spec_ax.scatter(
                        [xi], [yi], s=90, marker=marker,
                        color="#ff9800", edgecolors="#111111", linewidths=0.9, zorder=5,
                    )
                else:
                    kind_str = "Peak" if is_peak else "Dip"
                    leg_label = f"{kind_str} {lbl}" if lbl not in legend_labels_added else ""
                    self._spec_ax.scatter(
                        [xi], [yi], s=50, marker=marker, color=base_color, zorder=4,
                        **({"label": leg_label} if leg_label else {}),
                    )
                    legend_labels_added.add(lbl)
            if has_lasso_drawn:
                self._spec_ax.scatter(
                    [], [], s=110, marker="^", facecolors="#fff176",
                    edgecolors="#ff00c8", linewidths=1.6, label="Heatmap Lasso Selected",
                )

        if fit_centers.size > 0:
            fit_y = np.interp(fit_centers, x, y)
            self._spec_ax.vlines(
                fit_centers, y0, y1, colors="#8e24aa", linestyles="-.", linewidth=1.0, alpha=0.68, zorder=2.1
            )
            self._spec_ax.scatter(
                fit_centers, fit_y, s=52, marker="D",
                facecolors="white", edgecolors="#8e24aa", linewidths=1.2,
                zorder=6, label="Fit Peaks"
            )

        # ── Drag marker ───────────────────────────────────────────────────────
        if analysis_matches and self._drag_peak_arr_idx is not None and self._drag_current_x is not None:
            snap_idx = int(np.argmin(np.abs(x - self._drag_current_x)))
            self._drag_scatter = self._spec_ax.scatter(
                [float(x[snap_idx])], [float(y[snap_idx])],
                s=120, color="gold", edgecolors="k", linewidths=1.2, zorder=9, label="Dragging"
            )

        # ── Fit overlay ───────────────────────────────────────────────────────
        if active_fit is not None:
            self._spec_ax.plot(active_fit.x, active_fit.y_fit, lw=1.5, color="#111111", label="Fit")
            comp_colors = ["#ff7043", "#26a69a", "#7e57c2", "#ec407a", "#8d6e63", "#42a5f5"]
            for k, comp in enumerate(active_fit.components, start=1):
                self._spec_ax.plot(
                    active_fit.x, comp + active_fit.baseline,
                    lw=1.2, alpha=0.9, color=comp_colors[(k - 1) % len(comp_colors)], zorder=4.2, label=f"C{k}",
                )
            self._spec_ax.text(
                0.015, 0.98,
                f"R^2={active_fit.r_squared:.4f}\nRMSE={active_fit.rmse:.4g}",
                transform=self._spec_ax.transAxes,
                ha="left", va="top", fontsize=PLOT_FONT_SIZE, color="#111111",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#444444", alpha=0.82),
                zorder=10,
            )

        handles, labels = self._spec_ax.get_legend_handles_labels()
        if handles:
            self._spec_ax.legend(loc="upper right", fontsize=PLOT_FONT_SIZE)

        style_plot_axis(self._spec_ax)
        self._spec_ax.set_xlim((view_xmin, view_xmax))
        self._spec_ax.set_ylim((y0, y1))

        self.current_gate = gate_used
        self._sync_gate_controls()
        self._refresh_fit_range_summary()
        self._refresh_fit_readiness()
        self._update_gate_line(gate_used)
        self._update_heatmap_peak_labels(gate_used, x)
        # Matplotlib emits on each sibling after propagating a shared X view.
        # The heatmap callback alone sees both toolbar and spectrum changes.
        # Clearing the spectrum also resets its shared X locator/formatter.
        # Restore the heatmap style without touching either scientific view.
        self._apply_live_heatmap_style()
        self.canvas.draw_idle()

    def _update_gate_line(self, gate: float) -> None:
        if self._heat_ax is None:
            return
        if self._gate_line is None or getattr(self._gate_line, "axes", None) is not self._heat_ax:
            self._gate_line = self._heat_ax.axhline(gate, color="#222222", ls="--", lw=1.2, alpha=0.9)
        else:
            self._gate_line.set_ydata([gate, gate])

    def _invalidate_peak_documents(self, *, force=False):
        session = self.workspace.active or self._unregistered_session
        if session.current_cube is None:
            return
        selected = dict(session.batch_peak_map)
        if session.peak_gate_idx is not None:
            selected[session.peak_gate_idx] = session.peak_indices
        labels = tuple((int(gi), int(ei), str(session.peak_label_lookup.get((gi, int(ei)), rank+1)))
                       for gi, indices in sorted(selected.items())
                       if 0 <= gi < session.current_cube.gate.size
                       for rank, ei in enumerate(indices) if 0 <= int(ei) < session.current_cube.energy.size)
        previous = self._formal_peak_label_signatures.get(session.dataset_id)
        self._formal_peak_label_signatures[session.dataset_id] = labels
        if self._restoring_dataset or (not force and (previous is None or labels == previous)):
            return
        for graph in session.graphs.values():
            if graph.presentation.include_peak_labels:
                self._invalidate_graph_document((session.dataset_id, graph.graph_id))
        self._schedule_plot_page_render()

    def _update_heatmap_peak_labels(self, gate_used: float, x: np.ndarray) -> None:
        self._invalidate_peak_documents()
        if self._heat_ax is None:
            return
        for art_list in (self._heat_peak_artists, self._heat_batch_peak_artists, self._heat_fit_artists):
            for art in art_list:
                try:
                    art.remove()
                except Exception:
                    pass
        for t in self._heat_peak_texts:
            try:
                t.remove()
            except Exception:
                pass
        self._heat_peak_artists = []
        self._heat_batch_peak_artists = []
        self._heat_fit_artists = []
        self._heat_peak_texts = []

        # Batch peaks (all gates) — colored by resolved label (tab10)
        session = self.workspace.active or self._unregistered_session
        if session.active_graph.source_mode == 'raw' and session.current_cube is not session.base_cube:
            return  # Analysis results belong to the processed source, not this Original graph.
        if self.batch_peak_coords is not None:
            bx, by = self.batch_peak_coords
            if bx.size and by.size:
                b_kinds = np.asarray(
                    [self.batch_peak_kind_lookup.get(rec, True) for rec in self.batch_peak_records], dtype=bool
                )
                b_labels = [self._resolved_peak_label(rec[0], rec[1]) for rec in self.batch_peak_records]
                selected_ids = set(self._lasso_selected_peak_ids)
                selected_mask = np.asarray([rec in selected_ids for rec in self.batch_peak_records], dtype=bool)
                unsel = ~selected_mask
                # Unselected: draw per unique label so each gets its tab10 color
                unique_labels = sorted(set(b_labels))
                legend_added: set[int] = set()
                for lbl in unique_labels:
                    lbl_mask = np.asarray([l == lbl for l in b_labels], dtype=bool)
                    col = self._peak_label_color(lbl)
                    m_pk = unsel & lbl_mask & b_kinds
                    m_dp = unsel & lbl_mask & ~b_kinds
                    leg_pk = f"Peak {lbl}" if lbl not in legend_added else ""
                    if m_pk.any():
                        self._heat_batch_peak_artists.append(
                            self._heat_ax.scatter(
                                bx[m_pk], by[m_pk], s=20, marker="^",
                                facecolors="none", edgecolors=col, linewidths=0.85,
                                alpha=0.98, zorder=24,
                                **({"label": leg_pk} if leg_pk else {}),
                            )
                        )
                        legend_added.add(lbl)
                    leg_dp = f"Dip {lbl}" if lbl not in legend_added else ""
                    if m_dp.any():
                        self._heat_batch_peak_artists.append(
                            self._heat_ax.scatter(
                                bx[m_dp], by[m_dp], s=20, marker="v",
                                facecolors="none", edgecolors=col, linewidths=0.85,
                                alpha=0.98, zorder=24,
                                **({"label": leg_dp} if leg_dp else {}),
                            )
                        )
                        legend_added.add(lbl)
                # Lasso-selected (all labels) — yellow fill, magenta edge
                m_sel_pk = selected_mask & b_kinds
                if m_sel_pk.any():
                    self._heat_batch_peak_artists.append(
                        self._heat_ax.scatter(
                            bx[m_sel_pk], by[m_sel_pk], s=64, marker="^",
                            facecolors="#fff176", edgecolors="#ff00c8", linewidths=1.6,
                            alpha=1.0, zorder=25, label="Lasso Selected",
                        )
                    )
                m_sel_dp = selected_mask & ~b_kinds
                if m_sel_dp.any():
                    self._heat_batch_peak_artists.append(
                        self._heat_ax.scatter(
                            bx[m_sel_dp], by[m_sel_dp], s=64, marker="v",
                            facecolors="#fff176", edgecolors="#ff00c8", linewidths=1.6,
                            alpha=1.0, zorder=25,
                        )
                    )

        if self.batch_fit_coords is not None:
            fx, fy = self.batch_fit_coords
            if fx.size and fy.size:
                self._heat_fit_artists.append(
                    self._heat_ax.scatter(
                        fx, fy, s=28, marker="D",
                        facecolors="white", edgecolors="#8e24aa", linewidths=1.0,
                        alpha=0.95, zorder=27, label="Fit Peaks",
                    )
                )

        trace_peak_indices = self._current_trace_peak_indices()
        if trace_peak_indices.size == 0:
            return
        valid = trace_peak_indices
        px = np.asarray(x[valid], dtype=float)
        py = np.full_like(px, float(gate_used), dtype=float)
        tr_kinds = np.asarray(
            [self.peak_kind_lookup.get(int(ei), True) for ei in valid], dtype=bool
        )
        gate_idx_tr = self._current_gate_index()
        tr_labels = [self._resolved_peak_label(gate_idx_tr, int(ei)) for ei in valid]
        tr_colors = [self._peak_label_color(lbl) for lbl in tr_labels]
        sel_idx = self._selected_peak_array_index()

        # Draw trace peak markers one-by-one so each gets its label color
        for i in range(px.size):
            is_peak = bool(tr_kinds[i])
            marker = "^" if is_peak else "v"
            col = tr_colors[i]
            if sel_idx is not None and i == sel_idx:
                self._heat_peak_artists.append(
                    self._heat_ax.scatter(
                        [px[i]], [py[i]], s=88, marker=marker,
                        facecolors="#ff9800", edgecolors="#111111", linewidths=0.9,
                        zorder=31, label="Selected@Gate",
                    )
                )
            else:
                self._heat_peak_artists.append(
                    self._heat_ax.scatter(
                        [px[i]], [py[i]], s=42, marker=marker,
                        facecolors=col, edgecolors="#1f1f1f", linewidths=0.8,
                        zorder=30,
                    )
                )

        y0, y1 = self._heat_ax.get_ylim()
        yoff = 0.018 * abs(float(y1 - y0))
        for i, (xv, lbl) in enumerate(zip(px, tr_labels)):
            is_sel = (sel_idx is not None) and (i == sel_idx)
            txt = self._heat_ax.text(
                float(xv), float(gate_used + yoff), str(lbl),
                fontsize=PLOT_FONT_SIZE,
                color="#111111",
                ha="center", va="bottom", zorder=31,
                bbox=dict(
                    boxstyle="round,pad=0.16",
                    fc=("#fff3cd" if is_sel else "white"),
                    ec=("#a65f00" if is_sel else "#222222"),
                    alpha=0.85,
                ),
            )
            self._heat_peak_texts.append(txt)

    # =========================================================================
    #  Canvas event handlers
    # =========================================================================

    def _on_canvas_click(self, event: Any) -> None:
        if self.current_cube is None:
            return
        if self._dat_import_pending:
            if event.inaxes is self._heat_ax and event.button == 1 and event.ydata is not None:
                self._set_current_gate_from_value(float(event.ydata), update_plot=True)
            return

        # ── Spectrum plot ─────────────────────────────────────────────────────
        if event.inaxes is self._spec_ax and event.xdata is not None:
            if event.button == 1:
                # Check if near existing peak → start drag
                trace_peak_indices = self._current_trace_peak_indices()
                if trace_peak_indices.size > 0:
                    x = self.current_cube.energy
                    peak_x = x[trace_peak_indices]
                    x_span = float(max(np.nanmax(x) - np.nanmin(x), 1e-12))
                    dists = np.abs(peak_x - float(event.xdata))
                    nearest_arr_idx = int(np.argmin(dists))
                    if dists[nearest_arr_idx] < 0.025 * x_span:
                        # Start drag
                        self._drag_peak_arr_idx = nearest_arr_idx
                        self._drag_current_x = float(event.xdata)
                        if self._motion_cid is None:
                            self._motion_cid = self.canvas.mpl_connect(
                                "motion_notify_event", self._on_canvas_motion
                            )
                        return
                # Add peak
                if self._add_manual_peak(float(event.xdata)):
                    gate_used, y = self._nearest_spectrum(self.current_gate)
                    self._update_spectrum_only()
                    self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")
                return

            if event.button == 3:
                self._show_peak_context_menu(float(event.xdata))
                return

        # ── Heatmap ───────────────────────────────────────────────────────────
        if self._heat_ax is None:
            return
        if self._lasso_mode and event.inaxes is self._heat_ax:
            return

        # Circle-delete drag start
        if (
            self.circle_delete_btn.isChecked()
            and event.inaxes is self._heat_ax
            and event.button == 1
            and event.xdata is not None
            and event.ydata is not None
        ):
            self._batch_delete_circle_start = (float(event.xdata), float(event.ydata))
            return

        # Right-click on heatmap → remove peak at gate line
        if event.inaxes is self._heat_ax and event.button == 3 and event.xdata is not None and event.ydata is not None:
            if self._remove_peak_from_heatmap_click(float(event.xdata), float(event.ydata)):
                gate_used, y = self._nearest_spectrum(self.current_gate)
                self._update_spectrum_only()
                self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")
            return

        # Left-click on heatmap → select gate (ORIGINAL BEHAVIOR, UNCHANGED)
        if event.button != 1 or event.inaxes is not self._heat_ax or event.ydata is None:
            return
        if self.circle_delete_btn.isChecked():
            return  # already handled above
        g = self.current_cube.gate
        idx = int(np.argmin(np.abs(g - float(event.ydata))))
        self._set_current_gate_from_index(idx, update_plot=False)
        self.fit_result = None
        self._update_spectrum_only()

    def _on_canvas_hover(self, event: Any) -> None:
        self._set_canvas_cursor_for_event(event)

    def _on_canvas_motion(self, event: Any) -> None:
        """Handle peak drag motion in the spectrum plot."""
        if self._dat_import_pending:
            return
        if self._drag_peak_arr_idx is None:
            return
        if event.inaxes is not self._spec_ax or event.xdata is None or self.current_cube is None:
            return
        self._drag_current_x = float(event.xdata)
        # Lightweight: update only spec_ax without full redraw
        x = self.current_cube.energy
        _, y = self._nearest_spectrum(self.current_gate)
        snap_idx = int(np.argmin(np.abs(x - self._drag_current_x)))
        # Update the drag scatter in-place if already created
        if self._drag_scatter is not None:
            try:
                self._drag_scatter.set_offsets([[float(x[snap_idx]), float(y[snap_idx])]])
                self.canvas.draw_idle()
                return
            except Exception:
                self._drag_scatter = None
        # Fallback: full update
        self._update_spectrum_only()

    def _on_canvas_release(self, event: Any) -> None:
        if self._dat_import_pending:
            self._drag_peak_arr_idx = self._drag_current_x = None
            self._drag_scatter = None
            self._batch_delete_circle_start = None
            return
        if self.toolbar.mode:
            self._finish_history_edit()
            return
        # ── Commit peak drag ──────────────────────────────────────────────────
        if self._drag_peak_arr_idx is not None:
            if self._motion_cid is not None:
                self.canvas.mpl_disconnect(self._motion_cid)
                self._motion_cid = None
            if event.inaxes is self._spec_ax and event.xdata is not None and self.current_cube is not None:
                if self._peak_gate_idx != self._current_gate_index():
                    self._drag_peak_arr_idx = None
                    self._drag_current_x = None
                    self._drag_scatter = None
                    self._update_spectrum_only()
                    return
                x = self.current_cube.energy
                snap_idx = int(np.argmin(np.abs(x - float(event.xdata))))
                # Replace the dragged peak index
                old_idx = int(self.peak_indices[self._drag_peak_arr_idx])
                if old_idx != snap_idx:
                    with self._peak_edit('Move peak', (self._current_gate_index(),)):
                        self._invalidate_analysis_jobs()
                        gate_idx = self._current_gate_index()
                        old_kind = self.peak_kind_lookup.pop(old_idx, True)
                        old_label = self.peak_label_lookup.pop((gate_idx, old_idx), None)
                        self.peak_kind_lookup.setdefault(snap_idx, old_kind)
                        if old_label is not None:
                            self.peak_label_lookup.setdefault((gate_idx, snap_idx), old_label)
                        self.peak_indices[self._drag_peak_arr_idx] = snap_idx
                        self.peak_indices = np.asarray(np.sort(np.unique(self.peak_indices)), dtype=int)
                        self._sync_current_trace_peaks_to_batch_map()
                        self.fit_result = None
                self._refresh_peak_delete_controls()
                if self.current_cube is not None:
                    gate_used, y = self._nearest_spectrum(self.current_gate)
                    self._update_peak_text(gate_used, x, y, title_prefix="Manual")
            self._drag_peak_arr_idx = None
            self._drag_current_x = None
            self._drag_scatter = None
            self._update_spectrum_only()
            return

        # ── Circle-delete commit ───────────────────────────────────────────────
        if self._lasso_mode and event.inaxes is self._heat_ax:
            return

        if (
            self.current_cube is not None
            and self._heat_ax is not None
            and self.circle_delete_btn.isChecked()
            and self._batch_delete_circle_start is not None
        ):
            start_x, start_y = self._batch_delete_circle_start
            self._batch_delete_circle_start = None
            if (
                event.inaxes is self._heat_ax
                and event.button == 1
                and event.xdata is not None
                and event.ydata is not None
            ):
                deleted = self._delete_batch_peaks_in_circle(
                    start_x, start_y, float(event.xdata), float(event.ydata)
                )
                if deleted > 0:
                    self._update_spectrum_only()
                    self.statusBar().showMessage(f"Deleted {deleted} all-gate peak(s) inside circle.")
                else:
                    self.statusBar().showMessage("No all-gate peaks inside circle.")
            else:
                self._batch_delete_circle_start = None

    # =========================================================================
    #  Manual peak editing helpers
    # =========================================================================

    def _add_manual_peak(self, x_click: float) -> bool:
        with self._peak_edit('Add peak', (self._current_gate_index(),)):
            if self._dat_import_pending:
                return False
            if self.current_cube is None:
                return False
            gate_idx = self._current_gate_index()
            if self._peak_gate_idx != gate_idx:
                self.peak_indices = np.asarray([], dtype=int)
                self.peak_kind_lookup = {}
                self._peak_gate_idx = gate_idx
            x = self.current_cube.energy
            idx = int(np.argmin(np.abs(x - float(x_click))))
            if self.peak_indices.size and np.any(self.peak_indices == idx):
                return False
            self._invalidate_analysis_jobs()
            merged = np.concatenate([self.peak_indices, np.asarray([idx], dtype=int)])
            self.peak_indices = np.asarray(np.unique(merged), dtype=int)
            _, y_cur = self._nearest_spectrum(self.current_gate)
            self.peak_kind_lookup[idx] = self._auto_kind(y_cur, idx)
            self._sync_current_trace_peaks_to_batch_map()
            self.fit_result = None
            self._refresh_peak_delete_controls()
            return True

    def _remove_manual_peak_near(self, x_click: float) -> bool:
        with self._peak_edit('Remove peak', (self._current_gate_index(),)):
            if self._dat_import_pending:
                return False
            if self.current_cube is None or self.peak_indices.size == 0:
                return False
            gate_idx = self._current_gate_index()
            if self._peak_gate_idx != gate_idx:
                return False
            x = self.current_cube.energy
            peak_x = x[self.peak_indices]
            k = int(np.argmin(np.abs(peak_x - float(x_click))))
            nearest_idx = int(self.peak_indices[k])
            x_span = max(float(np.nanmax(x) - np.nanmin(x)), 1e-12)
            if abs(float(x[nearest_idx]) - float(x_click)) > 0.02 * x_span:
                return False
            self._invalidate_analysis_jobs()
            self.peak_indices = np.asarray(self.peak_indices[self.peak_indices != nearest_idx], dtype=int)
            self.peak_label_lookup.pop((gate_idx, nearest_idx), None)
            self._sync_current_trace_peaks_to_batch_map()
            self.fit_result = None
            self._refresh_peak_delete_controls()
            return True

    def _remove_peak_from_heatmap_click(self, x_click: float, y_click: float) -> bool:
        if self.current_cube is None or self.peak_indices.size == 0 or self._heat_ax is None:
            return False
        gate_used, _ = self._nearest_spectrum(self.current_gate)
        y0, y1 = self._heat_ax.get_ylim()
        y_tol = 0.03 * abs(float(y1 - y0))
        if abs(float(y_click) - float(gate_used)) > max(y_tol, 1e-9):
            return False
        return self._remove_manual_peak_near(float(x_click))

    def _delete_batch_peaks_in_circle(
        self, center_x: float, center_y: float, edge_x: float, edge_y: float
    ) -> int:
        with self._peak_edit('Delete circle peaks', self.batch_peak_map):
            if self._dat_import_pending:
                return 0
            self._invalidate_analysis_jobs()
            if self.current_cube is None or self._heat_ax is None or not self.batch_peak_map:
                return 0
            x0, x1 = self._heat_ax.get_xlim()
            y0, y1 = self._heat_ax.get_ylim()
            xspan = max(abs(float(x1 - x0)), 1e-12)
            yspan = max(abs(float(y1 - y0)), 1e-12)
            radius_n = float(np.hypot((edge_x - center_x) / xspan, (edge_y - center_y) / yspan))
            if radius_n <= 0.0:
                return 0
            energy = self.current_cube.energy
            gates = self.current_cube.gate
            deleted = 0
            new_map: dict[int, np.ndarray] = {}
            for gi, indices in self.batch_peak_map.items():
                arr = np.asarray(indices, dtype=int)
                arr = arr[(arr >= 0) & (arr < energy.size)]
                if arr.size == 0 or gi < 0 or gi >= gates.size:
                    continue
                px = np.asarray(energy[arr], dtype=float)
                py = np.full(px.shape, float(gates[gi]), dtype=float)
                keep = np.hypot((px - center_x) / xspan, (py - center_y) / yspan) > radius_n
                deleted += int(np.count_nonzero(~keep))
                for ei in arr[~keep]:
                    self.peak_label_lookup.pop((int(gi), int(ei)), None)
                if np.any(keep):
                    new_map[int(gi)] = np.asarray(arr[keep], dtype=int)
            self.batch_peak_map = new_map
            self._sync_batch_peak_coords()
            return deleted
