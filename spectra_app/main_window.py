from __future__ import annotations

from .file_picker import FilePicker
from .quick_ui import ApplicationBridge, CommandAction
from .common_prompts import PromptAction, prompt_message, prompt_text, prompt_item, prompt_int
from .ordinary_analysis_bridge import OrdinaryAnalysisBridge
from .source_navigation import SourceNavigation, SourceServices
from .project_model import ProjectRow
from .shared_dat_properties import SharedDatPropertiesBridge, BOUNDS, COLORMAPS

from .signal_titles import (infer_signal_kind, infer_source_signal_kind, automatic_signal_label,
                            display_signal_label, RAW_LABEL, DERIVATIVE_LABEL)

import csv
import json
import re
from copy import deepcopy
from contextlib import ExitStack, contextmanager
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

import numpy as np
from .analysis_navigation import AnalysisNavigationBridge, AnalysisNavigationController
from .analysis_canvas import (AnalysisFigureCanvas,
                              CanvasPointerContext, PeakMenuRequest)
from matplotlib.backend_bases import ResizeEvent
from matplotlib.figure import Figure
from matplotlib import colormaps
from matplotlib.path import Path as MplPath
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.widgets import LassoSelector
from scipy.signal import savgol_filter
from PySide6.QtCore import Qt, QObject, QEvent, QPoint, QPointF, QSettings, QSignalBlocker, QTimer, Signal, QSize, QMimeData, QUrl, QItemSelection, QItemSelectionModel, Property, Slot, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QWheelEvent, QIcon, QImage, QPixmap, QKeySequence, QDesktopServices, QGuiApplication


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
from .dataset_state import (AnalysisControls, DatasetDefaults,
                            DatasetSession, FixedRecipe, ProcessingState, SessionField,
                            Workspace, canonical_path, dataset_display_labels, initialize_dataset,
                            detached_graph_session, graph_export_stem, refresh_graph_colors,
                            graph_cube, graph_signal_label)
from .edit_history import (COLOR_FIELDS, RANGE_FIELDS, STYLE_FIELDS, TITLE_FIELDS,
                           EditCommand, EditHistory, FieldChange, capture_fields, field_changes,
                           restore_fields, capture_peaks)
from .plot_presentation import NormSpec, PlotPresentation
from .formal_plot_controller import FormalPlotController
from .quick_documents import QuickDocuments
from .plot_icons import plot_icon
from .workbook_page import WorkbookController, WorkbookPlotCommandsController
from .xy_plot_controller import XYPlotController
from .xy_analysis_controller import XYAnalysisServices
from .workbook_state import (create_workbook, register_xy_sheet, create_comparison, add_curve,
    remove_workbook, remove_sheet, remove_comparison, ensure_dataset_sheet,
    add_processing_result, retain_current_processing_result,
    reconcile_dataset_sheets, document_record_key, document_exists, set_sheet_mapping, _source_key)
from .workbook_state import save_sheet_snapshot, merge_workbooks, sheet_shape, validate_workbook_state
from .workbook_state import workbook_display_labels, workbook_sources
from .workbook_state import select_curve_additions, validate_mapping, initialize_comparison_labels
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
from .plot_paste import PASTE_OPTIONS_KEY, PasteOperation, graph_candidate, effective_paste_options, independently_formatted
from .plot_comparison import PlotComparisonWindow
from .plot_batch import BatchExportJob, BatchFileResult, BatchSummary, SerialPlotBatch
from .plot_dialogs import ApplyFormatDialog, BatchExportDialog
from .svg_export import (MISSING_COLOR, build_color_norm, prepare_display_values,
                         valid_color_values, prepare_heatmap_page, render_hybrid_svg, render_svg_image, write_svg, write_svg_png,
                         apply_heatmap_axis_style, apply_heatmap_colorbar_style, validate_heatmap_axis_ticks,
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


class ApplicationController(AnalysisWorkbenchMixin, QObject):

    shellChanged = Signal()
    statusChanged = Signal(str)
    revealProperties = Signal(str, float)
    inspectorRevealRequested = Signal(int, str)
    title = Property(str, lambda self: 'Spectra Analyzer' + (' — ' + self._project_path.name if self._project_path else '') + (' *' if self._project_modified else ''), notify=shellChanged)
    statusText = Property(str, lambda self: self._status_text, notify=shellChanged)
    projectPath = Property(str, lambda self: str(self._project_path or ''), notify=shellChanged)
    projectModified = Property(bool, lambda self: self._project_modified, notify=shellChanged)
    outerView = Property(str, lambda self: self._outer_view, notify=shellChanged)
    taskIndex = Property(int, lambda self: self._task_index, notify=shellChanged)
    projectPanelVisible = Property(bool, lambda self: self._project_panel_visible, notify=shellChanged)
    propertiesVisible = Property(bool, lambda self: self._dat_details_requested, notify=shellChanged)
    propertiesFloating = Property(bool, lambda self: self._properties_floating, notify=shellChanged)
    contextKind = Property(str, lambda self: self._context_kind, notify=shellChanged)
    propertiesController = Property(QObject, lambda self: self._properties_controller, notify=shellChanged)
    commandController = Property(QObject, lambda self: self._command_controller, notify=shellChanged)
    formalBridge = Property(QObject, lambda self: self.plot_page.formal, notify=shellChanged)
    sourceNavigation = Property(QObject, lambda self: self.source_navigation, constant=True)
    applicationBridge = Property(QObject, lambda self: self.app_bridge, constant=True)
    sharedDat = Property(QObject, lambda self: self.dat_properties, constant=True)
    ordinaryAnalysis = Property(QObject, lambda self: self.ordinary_analysis, constant=True)
    analysisWorkbench = Property(QObject, lambda self: self.analysis_workbench, constant=True)
    analysisHeader = Property(QObject, lambda self: self.analysis_header, constant=True)
    analysisNavigation = Property(QObject, lambda self: self.analysis_navigation, constant=True)
    documents = Property(QObject, lambda self: self.graph_documents, constant=True)
    workbookCommands = Property(QObject, lambda self: self.workbook_commands, constant=True)
    xyCommandBridge = Property(QObject, lambda self: self._command_controller.command_bridge if isinstance(self._command_controller, XYPlotController) else None, notify=shellChanged)
    xyInspectorBridge = Property(QObject, lambda self: self._properties_controller.inspector_bridge if isinstance(self._properties_controller, XYPlotController) else None, notify=shellChanged)
    xyAnalysisBridge = Property(QObject, lambda self: self._properties_controller.analysis_controller.bridge if isinstance(self._properties_controller, XYPlotController) else None, notify=shellChanged)
    linecutHeight = Property(float, lambda self: float(self._workbench_linecut_sizes[1]), notify=shellChanged)
    workbenchActive = Property(bool, lambda self: self._workbench_document() is not None and self._outer_view == 'analysis', notify=shellChanged)
    linecutVisible = Property(bool, lambda self: self._workbench_aux_linecut_visible, notify=shellChanged)
    cancelVisible = Property(bool, lambda self: self._cancel_visible, notify=shellChanged)
    busy = Property(bool, lambda self: self._project_busy(), notify=shellChanged)
    # Compatibility for canvas/analysis reveal callers; taskIndex owns navigation.
    inspectorSection = Property(int, lambda self: self._task_index, notify=shellChanged)
    contextSummary = Property(str, lambda self: self._properties_context_summary(), notify=shellChanged)
    contextFullSummary = Property(str, lambda self: self._properties_context_summary(detailed=True), notify=shellChanged)
    menus = Property('QVariantList', lambda self: [dict(title=name, actions=items) for name, items in self._menu_groups.items()], notify=shellChanged)
    recentProjects = Property('QVariantList', lambda self: [dict(path=value, title=Path(value).name + ' — ' + str(Path(value).parent)) for value in self._recent_project_paths], notify=shellChanged)

    def publish_status(self, text, timeout=0):
        self._status_text = str(text)
        self.statusChanged.emit(self._status_text)
        self.shellChanged.emit()

    def set_project_modified(self, modified):
        self._project_modified = bool(modified)
        self.shellChanged.emit()

    def set_cancel_visible(self, visible):
        self._cancel_visible = bool(visible)
        self.shellChanged.emit()

    @Slot(int, result=bool)
    def request_task(self, index):
        if index not in (0, 1, 2):
            return False
        previous = self._task_index
        if isinstance(self._properties_controller, XYPlotController):
            self._properties_controller.inspector_bridge.suspendBlur()
        self._task_index = index
        if self._outer_view == 'analysis':
            self._analysis_task_index = index
        else:
            self._heatmap_task_index = index
        self.shellChanged.emit()
        self.app_bridge.navigation.changed.emit()
        if previous != index:
            self._activate_visible_xy_analysis()
        return True

    @Slot(str, result=bool)
    def request_outer_view(self, view):
        if view not in ('analysis', 'workspace'):
            return False
        if view == self._outer_view:
            return True
        if not self._project_installing:
            try:
                self._finish_current_document()
            except ValueError as exc:
                self.publish_status(str(exc))
                return False
        if view == 'analysis':
            self._analysis_return_key = self.graph_documents.registry.active_key
        self._outer_view = view
        self._task_index = self._analysis_task_index if view == 'analysis' else self._heatmap_task_index
        self._outer_view_changed(defer_live_layout=view == 'analysis')
        return True

    @Slot(bool)
    def floatProperties(self, floating):
        self._properties_floating = bool(floating)
        self.shellChanged.emit()

    @Slot(int)
    def selectInspectorSection(self, section):
        self.request_task(section)

    @Slot(float, float)
    def rememberLinecutSizes(self, main, linecut):
        if self.linecutVisible and main > 0 and linecut > 0:
            self._workbench_linecut_sizes = [round(main), round(linecut)]

    def reveal_xy_inspector(self, page, section, field):
        if page is not self._properties_controller:
            return
        self._set_dat_details_requested(True)
        self.selectInspectorSection(section)
        self.inspectorRevealRequested.emit(section, field)

    @Slot(str)
    def revealInspectorField(self, field):
        page = self._properties_controller
        if isinstance(page, XYPlotController):
            page.inspector_bridge.focusFieldRequested.emit(field)

    @Slot()
    def refreshMenus(self):
        self._refresh_graph_commands()

    @Slot(str)
    def openRecent(self, path):
        self._open_project_path(path)

    @Slot()
    def clearRecent(self):
        self._clear_recent_projects()

    @Slot()
    def cancelOperation(self):
        self._cancel_background_job()

    @Slot()
    def close(self):
        if self.root_window is not None:
            self.root_window.close()

    @Slot('QVariantList', result=bool)
    def canAcceptDropUrls(self, urls):
        urls = [QUrl(value) if isinstance(value, str) else value for value in urls]
        paths = [Path(value.toLocalFile()) for value in urls if value.isLocalFile()]
        return bool(len(paths) == len(urls) and not self._drop_busy(paths)
                    and self._supported_drop(paths))

    @Slot('QVariantList', result=bool)
    def dropUrls(self, urls):
        if not self.canAcceptDropUrls(urls):
            self.publish_status(self._drop_rejection_message())
            return False
        urls = [QUrl(value) if isinstance(value, str) else value for value in urls]
        paths = [Path(value.toLocalFile()) for value in urls]
        if paths[0].suffix.lower() in ('.spectra', '.spextra'):
            self._open_project_path(paths[0])
        else:
            self._import_data_files(paths)
        return True

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
        self._unregistered_session.analysis_controls = AnalysisControls(
            linewidth_min=10, linewidth_max=50, peak_move_limit=10, maxfev=20000)
        self._restoring_dataset = False
        self._project_installing = False
        self.root_window = None
        self._status_text = ''
        self._project_modified = False
        self._outer_view = 'workspace'
        self._task_index = 0
        self._analysis_task_index = 0
        self._heatmap_task_index = 2
        self._cancel_visible = False
        self._properties_floating = False
        self._project_panel_visible = True
        self._project_panel_width = 240
        self._dat_details_requested = True
        self._properties_controller = None
        self._command_controller = None
        self._context_kind = 'empty'
        self._closing = False

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
        self._circle_delete_mode = False
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
        self._annotation_edit_requires_layout = False
        self._annotation_layout_pending = False
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
        self._latest_paste = None
        self._plot_settings_source_label = None
        self._plot_comparison_windows = []
        self._edit_histories = {}
        self._history_restoring = False
        self._history_depth = 0
        self._pending_history_edit = None
        self._unregistered_session.fixed_recipes['processing'].enabled = self._annotation_settings.value(
            'fixed_processing/enabled', False, type=bool)
        self.app_bridge = ApplicationBridge(self)
        self.processing = self.app_bridge.processing
        self._build_ui()
        self._build_graph_commands()
        self._build_quick_commands()
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
        self.set_cancel_visible(False)
        if self.processing.fixed:
            self._set_processing_controls(self._fixed_recipe("processing"))
        if (self.workspace.active or self._unregistered_session).fixed_recipes['regions'].enabled:
            regions = self._fixed_recipe("regions")
            self._color_scale_mode = regions.get("mode", "global")
            self.dat_properties.syncFromOwner()
        self._connect_dataset_control_projection()
        self._connect_control_feedback()
        self._connect_project_modified_editors()
        # Select the startup view after its tab-change dependencies are ready.
        self._refresh_workspace_shell()

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
            page = self.graph_documents.document_for(self.graph_documents.registry.active_key)
            return isinstance(page, XYPlotController) and not self._history_busy() and page.undo()
        return self._restore_history_command(redo=False)

    def redo_edit(self):
        if self._auxiliary_active():
            page = self.graph_documents.document_for(self.graph_documents.registry.active_key)
            return isinstance(page, XYPlotController) and not self._history_busy() and page.redo()
        return self._restore_history_command(redo=True)

    def _restore_history_command(self, *, redo):
        session = self.workspace.active
        if session is None or self._history_busy():
            self.publish_status('Wait for the current operation before Undo / Redo.')
            return False
        try:
            self._finish_graph_editors(capture_science=False)
        except ValueError as exc:
            self.publish_status(str(exc))
            return False
        history = self._history_for(session)
        source = history.redo_stack if redo else history.undo_stack
        destination = history.undo_stack if redo else history.redo_stack
        if not source:
            self._refresh_graph_commands()
            return False
        command = source[-1]
        # Rebuilding the ordinary canvas retires its keyboard owner and QML
        # releases focus. Retain an actual canvas user's focus across a
        # successful restore so the next Undo / Redo shortcut remains local.
        from shiboken6 import isValid
        root = getattr(self, 'root_window', None)
        focus = root.activeFocusItem() if root is not None else None
        focus_owner = (self.ordinary_analysis.ownerToken
                       if focus is not None and focus.objectName() == 'applicationDatCanvas'
                       and QGuiApplication.focusWindow() is root and root.isVisible()
                       and focus.isVisible() and focus.isEnabled()
                       and self._outer_view == 'analysis' else None)
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
        self.publish_status(notice or '')
        if (focus_owner is not None and isValid(root) and isValid(focus)
                and self.ordinary_analysis.ownerToken == focus_owner
                and self._outer_view == 'analysis' and root.isVisible()
                and QGuiApplication.focusWindow() is root
                and focus.isVisible() and focus.isEnabled()
                and root.contentItem().isAncestorOf(focus)
                and self.dat_canvas.allowed(self.dat_canvas.ownerToken)):
            current_focus = root.activeFocusItem()
            if current_focus is None or current_focus.isAncestorOf(focus):
                focus.forceActiveFocus(Qt.ShortcutFocusReason)
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
        self._update_fit_param_unit_labels()
        self._refresh_axis_status()
        self._refresh_fit_range_summary()
        self._refresh_peak_delete_controls()
        self._update_lasso_actions()
        self._refresh_analysis_controls()
        self._refresh_processing_controls()

    def _connect_dataset_control_projection(self) -> None:
        self.ordinary_analysis.draftChanged.connect(self._schedule_project_modified)

    def _capture_dataset_summary(self) -> None:
        # Summary is written directly to the canonical scientific owner.
        self.ordinary_analysis.refresh()

    def _set_analysis_summary(self, text) -> None:
        analysis_owner(self.workspace.active or self._unregistered_session).summary = str(text)
        self.ordinary_analysis.refresh()
        if not self._restoring_dataset:
            self._schedule_project_modified()

    def _analysis_controls(self):
        return analysis_owner(self.workspace.active or self._unregistered_session).analysis_controls

    def _analysis_status(self, **ui):
        if hasattr(self, 'ordinary_analysis'):
            self.ordinary_analysis.refresh(**ui)

    def _read_analysis_controls(self) -> AnalysisControls:
        return replace(analysis_owner(self.workspace.active or self._unregistered_session).analysis_controls)

    def _capture_dataset_controls(self, *_args, visual_only=False) -> None:
        if self._restoring_dataset:
            return
        self.dat_properties.syncFromOwner()
        self.ordinary_analysis.refresh()
        self._schedule_project_modified()

    def capture_active_dataset(self, *, render: bool = True) -> DatasetSession | None:
        if self._workbench_document() is not None:
            self._workbench_finish_editors()
        self.ordinary_analysis.finish_editor()
        self.processing.finish_editor()
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
        shared_candidate = self.dat_properties.validate_editor()
        gate_candidate = self.analysis_header.validate_editor()
        ordinary_candidate = self.ordinary_analysis.validate_editor()
        for page in self.graph_documents.cached_documents():
            if isinstance(page, FormalPlotController):
                page.formal.validate_editor()
        self.plot_page.formal.validate_editor()
        if self._workbench_document() is not None:
            self._workbench_finish_editors()
        self.processing.commit_for_save()
        self._finish_cached_formal_editors()
        with self._edit_transaction('DAT numeric properties', RANGE_FIELDS + COLOR_FIELDS + ('view.norm', 'view.color_region_boundaries')):
            for name in ('vmin', 'vmax'):
                if name in shared_candidate['numbers']:
                    self._dat_view().locks[name] = True
                    self._dat_view().bounds[name] = shared_candidate['numbers'][name]
            self.ordinary_analysis.commit_candidate(ordinary_candidate)
            self.analysis_header.commit_candidate(gate_candidate)
            self.dat_properties.commit_candidate(shared_candidate)
        for key in self.graph_documents.keys():
            if len(key) == 3 and key[0] == 'xy':
                self.graph_documents.document_for(key).finish_editor()
            elif len(key) == 3 and key[0] == 'workbook':
                self.graph_documents.document_for(key).finish_editor()
        self.capture_active_dataset(render=False)
        self.plot_page.cancel_interaction()
        self.workspace.graph_workspace = self._graph_workspace_snapshot()
        return self.workspace

    def _update_project_title(self):
        self.shellChanged.emit()
        if self.root_window is not None:
            self.root_window.setTitle(self.title)

    def _schedule_project_modified(self, *_args) -> None:
        if not self._restoring_dataset and not self._project_installing:
            self._project_revision += 1
            self._project_modified_timer.start()

    def _connect_project_modified_editors(self) -> None:
        # Explicit durable controls; search, sidebar state and Rows are preferences.
        self.analysis_header.draftChanged.connect(self._schedule_project_modified)
        self.analysis_workbench.draftChanged.connect(self._schedule_project_modified)
        self.dat_properties.draftChanged.connect(self._schedule_project_modified)

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
            session.summary = analysis_owner(active).summary
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
                for graph_id, graph in session.graphs.items() if graph.published and independently_formatted(graph)]
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
                    dirty |= self.ordinary_analysis.has_project_drafts()
                    dirty |= self.plot_page.has_pending_project_edits()
                    dirty |= self.processing.has_project_drafts()
                    dirty |= self.dat_properties.has_project_drafts()
                    dirty |= self.analysis_workbench.has_project_drafts()
                    dirty |= self.analysis_header.has_project_drafts()
                dirty |= any(isinstance(page, (XYPlotController, WorkbookController)) and page.has_pending_project_edits()
                             for page in self.graph_documents.cached_documents())
            except (ValueError, TypeError, AttributeError, OverflowError):
                # Invalid drafts are visible as unsaved; exact capture explains
                # their validation error at Save/Open/New/Close.
                dirty = True
        self.set_project_modified(bool(dirty))
        self._update_project_title()

    def _mark_project_saved(self) -> None:
        from .project_io import runtime_workspace_stamp
        self._project_stamp_cache.clear()
        self._project_saved_stamp = runtime_workspace_stamp(self._project_marker_workspace(), self._project_stamp_cache)
        self._project_modified_timer.stop()
        self.set_project_modified(False)
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

    def _refresh_recent_projects(self):
        self.shellChanged.emit()

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
            chosen, _ = FilePicker.getSaveFileName(self.root_window, 'Save Spectra project', str(self._output_folder() / 'Untitled.spectra'),
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
        self.publish_status('')
        return path

    def _validate_project_colors(self, workspace):
        # The viewer cannot project an invalid committed color mapping. Check
        # that same display boundary before writing; pending analysis controls
        # and missing scientific values remain valid project contents.
        for session in workspace.datasets.values():
            for graph in session.graphs.values():
                target = detached_graph_session(session)
                target.activate_graph(graph.graph_id)
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
        self.set_cancel_visible(True)
        self._refresh_graph_commands()

    def _on_project_io_idle(self):
        self._project_io_finalizing = False
        completion, self._project_io_completion = self._project_io_completion, None
        self.set_cancel_visible(False)
        self._refresh_graph_commands()
        if self._project_io_cancelled and not self._project_io_committed:
            self._project_io_cancelled = False
            self.publish_status('Project operation cancelled. Current workspace retained.')
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
            chosen, _ = FilePicker.getSaveFileName(self.root_window, 'Save Spectra project',
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
            self.set_project_modified(self._project_revision != revision)
            self._update_project_title()
            self._remember_recent_project(path)
            cancelled = self._project_io_cancel_event.is_set()
            self.publish_status(
                f'Saved project: {path} — cancellation arrived after the file was saved.' if cancelled else '')
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
        choice = prompt_message(self.root_window, 'Unsaved project changes', message,
            (PromptAction.SAVE, PromptAction.DISCARD, PromptAction.CANCEL), PromptAction.CANCEL, kind='warning')
        if choice == PromptAction.DISCARD:
            continuation()
        elif choice == PromptAction.SAVE:
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
        choice = prompt_message(self.root_window, 'Unsaved project changes', message,
            (PromptAction.SAVE, PromptAction.DISCARD, PromptAction.CANCEL), PromptAction.CANCEL, kind='warning')
        if choice == PromptAction.DISCARD:
            return True
        if choice != PromptAction.SAVE:
            return False
        try:
            return self.save_project() is not None
        except Exception as exc:
            self._show_error(f'Could not save project: {exc}')
            return False

    def _reset_project_runtime(self) -> None:
        self.dat_properties.retire_owner()
        self.analysis_header.retire_owner()
        self.ordinary_analysis.retire_owner()
        self.processing.reset()
        self._dat_analysis_graph_results.clear()
        self._analysis_return_key = None
        self._last_comparison_id = None
        self._comparison_target_pinned = False
        self.workbook_commands.set_target_pinned(False)
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
        self._annotation_edit_requires_layout = False
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
        self.plot_page.set_batch_state(visible=False, cancel_visible=False,
                                       results_enabled=False, status='', export_enabled=True)
        self.plot_page.preview.cancel_interaction()
        self.plot_page.preview.selected_id = None
        self.plot_page.preview._title_selected = False
        self.plot_page.preview.set_geometry(None, self.plot_page.presentation)
        self.set_cancel_visible(False)

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
            self.graph_documents.setMode('free')
            return
        # Cache open pages without activating their datasets or changing each
        # dataset's selected graph. The Analysis owner and last MDI owner can
        # legitimately differ.
        for record in metadata['documents']:
            if not record['open']:
                continue
            key = document_record_key(record)
            if self.graph_documents.document_for(key) is None:
                if len(key) == 3:
                    page = self._create_auxiliary_page(key)
                else:
                    page = self._create_plot_page(key)
                    page.set_presentation(self.workspace.datasets[key[0]].graphs[key[1]].presentation)
                    page.invalidate(self._plot_owner_token(key))
                self.graph_documents.cache_document(key, page)
        self.graph_documents.restore_workspace(metadata, self._document_title)
        active = tuple(metadata['active']) if metadata['active'] else None
        if active is not None and len(active) == 3:
            self._last_comparison_id = active[1] if active[0] == 'xy' else None
            self.request_outer_view('workspace')
            self._outer_view_changed()
        elif (metadata['stacking'] and metadata['active'] != list(self._document_key())
                and self._outer_view == 'workspace'):
            # An Analysis selection can differ from the last active graph.
            # Preserve both saved owners, showing the scientific selection in
            # Analysis until the user explicitly selects a graph again.
            self.request_outer_view('analysis')
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
        previous_processing_drafts = self.processing.snapshot_editor_drafts()
        previous_dat_editors = self.dat_properties.snapshot_editor_state()
        previous_header_editors = self.analysis_header.snapshot_editor_state()
        previous_analysis_editors = self.ordinary_analysis.snapshot_editor_state()
        previous_target = (self._last_comparison_id, self._comparison_target_pinned)
        previous_outer_view = self._outer_view
        previous_tasks = (self._task_index, self._analysis_task_index, self._heatmap_task_index)
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
            self.processing.restore_editor_drafts(previous_processing_drafts)
            self._edit_histories.update(previous_histories)
            (self._plot_revisions, self._graph_revisions, self._plot_cube_owners,
             self._formal_peak_label_signatures) = previous_render_state
            self.graph_documents.restore(previous_documents)
            for key, page in previous_documents.registry.cached_items():
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
                ApplicationController.activate_dataset(self, self.workspace.active_dataset_id, capture_outgoing=False)
            else:
                self._clear_empty_project_view()
            # Projection can reopen a selected-but-closed page. Restore the
            # exact visible set after its editor has been rebound.
            self._outer_view = previous_outer_view
            self._task_index, self._analysis_task_index, self._heatmap_task_index = previous_tasks
            self.dat_properties.restore_editor_state(previous_dat_editors)
            self.analysis_header.restore_editor_state(previous_header_editors)
            self.ordinary_analysis.restore_editor_state(previous_analysis_editors)
            # Reapply normal rectangles/stacking after selected-owner
            # projection, which may have briefly opened a closed graph.
            self.graph_documents.restore(previous_documents)
            self._last_comparison_id, self._comparison_target_pinned = previous_target
            self._refresh_comparison_targets()
            self._refresh_data_navigator()
            self._outer_view_changed()
            self._refresh_graph_commands()
            self._refresh_source_axis_control()
            self._update_project_title()
            raise
        finally:
            self._project_installing = False
        self.graph_documents.discard_parked(previous_documents)
        self._project_path, self._project_saved_digest = path, baseline
        self.current_root, self.current_folder = '', str(path.parent)
        self.source_navigation.reset_inventory()
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
                    self.set_project_modified(self._project_revision != revision)
                    self._update_project_title()
                    self._remember_recent_project(path)
                    self.publish_status('')

            self._start_project_io(work, finish, error_prefix='Cannot finalize opened project', commits=True)
            # Installation has committed successfully. Complete its saved
            # baseline before accepting another project transition.
            self._project_io_finalizing = True
            self.set_cancel_visible(False)
        else:
            self._mark_project_saved()
            self._remember_recent_project(path)
            self.publish_status('')
        return True

    def _clear_empty_project_view(self) -> None:
        self.dat_canvas.retire_interaction()
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
            self._set_analysis_summary('')
            self.plot_page.set_presentation(self._unregistered_session.presentation)
            self.dat_properties.retire_owner()
            self.analysis_header.retire_owner()
            self.ordinary_analysis.retire_owner()
            self._sync_gate_controls()
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
        self.set_project_modified(False)
        self.current_root = self.current_folder = ''
        self.source_navigation.reset_inventory()
        self._project_installed_workspace()
        self._update_project_title()
        self.publish_status('')
        return True

    def _on_new_project(self) -> None:
        try:
            self._confirm_project_discard_async(self._new_project_unchecked)
        except Exception as exc:
            self._show_error(f'Cannot create a new project: {exc}')

    def _on_open_project(self) -> None:
        try:
            self._require_project_idle()
            chosen, _ = FilePicker.getOpenFileName(self.root_window, 'Open Spectra project',
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
                chosen, _ = FilePicker.getSaveFileName(self.root_window, 'Save Spectra project as',
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
        if hasattr(self, 'dat_properties'):
            self.dat_properties.syncFromOwner()

    def _source_axis_override_allowed(self) -> bool:
        session = self.workspace.active or self._unregistered_session
        if session.sidecar_writes_enabled and not session.processing_results:
            return True
        if session.current_cube is not None:
            self.dat_properties._axis_override = session.current_cube.x_override
            self.dat_properties.syncFromOwner()
        self._refresh_source_axis_control()
        self.publish_status('Import the original DAT into a new project to change its axis interpretation.')
        return False

    def snapshot_dataset_defaults(self) -> DatasetDefaults:
        """Capture global new-file preferences once, before asynchronous import."""
        ordinary = self.ordinary_analysis.validate_editor()
        shared = self.dat_properties.validate_editor()
        gate = self.analysis_header.validate_editor()
        self.ordinary_analysis.commit_candidate(ordinary)
        self.analysis_header.commit_candidate(gate)
        self.dat_properties.commit_candidate(shared)
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
        choice = prompt_message(self.root_window, 'Remove dataset from project',
            f'Remove this dataset and its stored analysis from the project?\n\n{session.source_path}\n\n'
            'Its Sheet and dependent comparison curves will also be removed. The original DAT file will remain unchanged.',
            (PromptAction.YES, PromptAction.CANCEL), PromptAction.CANCEL, kind='warning')
        if choice != PromptAction.YES:
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
        self.dat_properties.retire_owner(dataset_id)
        self.analysis_header.retire_owner(dataset_id)
        self.ordinary_analysis.retire_owner(dataset_id)
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
        self.publish_status('')
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
        gate_candidate = self.analysis_header.validate_editor()
        ordinary_candidate = self.ordinary_analysis.validate_editor() if capture_science else None
        if self._workbench_document() is not None:
            self.analysis_workbench.validate_editor()
            self.plot_page.formal.validate_editor()
            if capture_science:
                self.processing.finish_editor()
            self._workbench_finish_editors()
            self._finish_bound_plot_editor()
            if ordinary_candidate is not None:
                self.ordinary_analysis.commit_candidate(ordinary_candidate)
            self.analysis_header.commit_candidate(gate_candidate)
            return
        # Collect all candidates before any handler can replace a sibling draft.
        candidate = self.dat_properties.validate_editor()
        self.plot_page.formal.validate_editor()
        if capture_science:
            self.processing.finish_editor()
        self._finish_bound_plot_editor()
        with self._edit_transaction('DAT numeric properties', RANGE_FIELDS + COLOR_FIELDS + ('view.norm', 'view.color_region_boundaries')):
            # Color drafts must be fixed before Gate changes can autoscale.
            for name in ('vmin', 'vmax'):
                if name in candidate['numbers']:
                    self._dat_view().locks[name] = True
                    self._dat_view().bounds[name] = candidate['numbers'][name]
            if ordinary_candidate is not None:
                self.ordinary_analysis.commit_candidate(ordinary_candidate)
            self.analysis_header.commit_candidate(gate_candidate)
            self.dat_properties.commit_candidate(candidate)
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

    def _finish_cached_formal_editors(self):
        # Validate hidden pages too, without changing the current document or
        # applying a cached graph's settings through an ambient session alias.
        pages = [(key, self.graph_documents.document_for(key)) for key in self.graph_documents.keys()]
        seen = set()
        for _key, page in pages:
            if isinstance(page, FormalPlotController) and page.has_pending_project_edits():
                page.formal.validate_editor()
        for key, page in pages:
            if not isinstance(page, FormalPlotController) or id(page) in seen:
                continue
            seen.add(id(page))
            if page.has_pending_project_edits():
                previous = self._binding_graph_document
                self._binding_graph_document = True
                try:
                    page.finish_editor()
                finally:
                    self._binding_graph_document = previous
        if id(self.plot_page) not in seen:
            self._finish_bound_plot_editor()

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
        self.publish_status(notice or '')

    def create_graph(self, name):
        session = self._graph_owner()
        self._finish_graph_editors()
        graph = session.create_graph(name)
        self._project_selected_graph(session)
        return graph

    def activate_graph(self, graph_id):
        session = self._graph_owner()
        if graph_id == session.active_graph_id:
            if self._outer_view == 'workspace':
                self._select_graph_page(session)
            return
        if graph_id not in session.graphs:
            raise ValueError('The graph no longer exists.')
        self._finish_graph_editors()
        previous_cube = session.current_cube
        session.activate_graph(graph_id)
        if session.current_cube is not previous_cube:
            self._clear_processing_analysis_state()
        if session.active_graph.analysis is None:
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
        self.dat_properties.retire_owner(session.dataset_id, graph_id)
        self.analysis_header.retire_owner(session.dataset_id, graph_id)
        self.ordinary_analysis.retire_owner(session.dataset_id, graph_id)
        key = (session.dataset_id, graph_id)
        if self.plot_page is self.graph_documents.document_for(key):
            self._bind_plot_page(self._fallback_plot_page)
        self.graph_documents.discard(key)
        self._graph_revisions.pop(key, None)
        self._history_for(session).remove_graph(graph_id)
        if session.active_graph.analysis is None:
            self._restoring_dataset = True
            try:
                self.restore_dataset_controls(session)
            finally:
                self._restoring_dataset = False
        self._project_selected_graph(session)

    def _refresh_graph_selector(self):
        if hasattr(self, 'source_navigation'):
            self.source_navigation.sync()

    def _refresh_graph_lifecycle_controls(self):
        if hasattr(self, 'source_navigation'):
            self.source_navigation.sync()

    def _source_dataset_action(self, action, dataset_id):
        if action == 'copy':
            self._copy_dataset_source_path(dataset_id)
        elif action == 'source':
            self._open_dataset_folder(dataset_id)
        elif action == 'export':
            self._open_dataset_folder(dataset_id, export=True)
        elif action == 'remove':
            self._on_remove_dataset(dataset_id)

    def _source_graph_action(self, action, dataset_id, graph_id):
        session = self.workspace.active
        if (session is None or session.dataset_id != dataset_id
                or session.active_graph_id != graph_id or self._project_busy()):
            return
        {'new': self._on_new_graph, 'rename': self._on_rename_graph,
         'remove': self._on_remove_graph}[action]()

    def _on_new_graph(self):
        try:
            session = self._graph_owner()
            graph_id = session.active_graph_id
            index = 1
            names = {graph.name for graph in session.graphs.values()}
            while f'Graph {index}' in names:
                index += 1
            name, accepted = prompt_text(self.root_window, 'New Graph',
                'Graph name (starts from the current graph settings):', text=f'Graph {index}')
            if accepted and self.workspace.active is session and session.active_graph_id == graph_id:
                self.create_graph(name)
        except ValueError as exc:
            self._show_error(str(exc))

    def _on_rename_graph(self):
        try:
            session = self._graph_owner()
            graph = session.active_graph
            name, accepted = prompt_text(self.root_window, 'Rename Graph', 'Graph name:', text=graph.name)
            if (accepted and self.workspace.active is session and session.active_graph is graph):
                self.rename_graph(graph.graph_id, name)
        except ValueError as exc:
            self._show_error(str(exc))

    def _on_remove_graph(self):
        try:
            session = self._graph_owner()
            graph = session.active_graph
            choice = prompt_message(self.root_window, 'Remove Graph',
                f'Remove graph “{graph.name}”?\n\nThe DAT data and analysis remain available in the other graphs.',
                (PromptAction.YES, PromptAction.CANCEL), PromptAction.CANCEL, kind='warning')
            if choice == PromptAction.YES and self.workspace.active is session and session.active_graph is graph:
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
        self._circle_delete_mode = False
        self._analysis_status()
        self.toolbar.cancel_interaction()
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
        self.dat_properties.syncFromOwner()
        self.ordinary_analysis.refresh()
        self._cached_d2 = None
        self.processing.syncFromOwner()

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
        self._restoring_dataset = True
        self._defer_dataset_layout = True
        try:
            self._select_graph_page(session)
            self.restore_dataset_controls(session)
            # Markers must match before _plot_cube: its default initialization
            # belongs only to first load, never to a cached owner.
            self._title_source = self._annotation_source = session.current_cube.source_path or id(session.current_cube)
            self._sync_batch_peak_coords()
            self._sync_batch_fit_coords()
            self._plot_cube()
            # Measure labels once, with the incoming owner, controls and exact
            # limits complete. Earlier page binding and plot construction only
            # request layout; they must not measure the outgoing/rounded view.
            self._defer_dataset_layout = False
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
            self._defer_dataset_layout = False
            self._restoring_dataset = False
        self._refresh_analysis_controls()
        self._refresh_processing_controls()
        self._refresh_source_axis_control()
        self._schedule_plot_page_render()
        self._refresh_imported_datasets()
        self._refresh_graph_selector()
        self.publish_status(notice or '')

    # =========================================================================
    #  UI construction
    # =========================================================================

    def _build_ui(self):
        self.source_navigation = SourceNavigation(SourceServices(
            workspace=lambda: self.workspace, busy=lambda: self._project_busy(),
            import_data=lambda: self._on_import_dats(),
            activate_dataset=lambda identity: self.activate_dataset(identity),
            activate_graph=lambda identity: self.activate_graph(identity),
            browse_root=lambda: self._on_browse_root(), select_folder=lambda folder: self._on_folder_changed(folder),
            load_paths=lambda paths: self._import_data_files(paths), dataset_action=self._source_dataset_action,
            graph_action=self._source_graph_action,
            save_rows=lambda rows: self._annotation_settings.setValue('datasets/list_rows', rows),
            error=self._show_error), self, visible_rows=self._annotation_settings.value('datasets/list_rows', 8, type=int))
        self.ordinary_analysis = OrdinaryAnalysisBridge(self)
        self._build_view_settings()
        self._build_plot_panel()
        self.app_bridge.navigation.bind(self)





    def _set_project_panel_visible(self, visible):
        self._project_panel_visible = bool(visible)
        self.shellChanged.emit()


    def _set_dat_details_requested(self, visible):
        previous = self._dat_details_requested
        self._dat_details_requested = bool(visible)
        self._refresh_workspace_shell()
        if not previous and self._dat_details_requested:
            self._activate_visible_xy_analysis()

    def _activate_visible_xy_analysis(self):
        page = self._properties_controller
        if (self._outer_view == 'workspace' and self._dat_details_requested
                and self._task_index == 1 and isinstance(page, XYPlotController)):
            # Enter only on a task, owner or visibility transition: activate()
            # reveals this same section synchronously through request_task().
            page.analysis_controller.activate(restore_selection=False)

    def _properties_context_summary(self, *, detailed=False):
        page = self._properties_controller
        if (isinstance(page, WorkbookController) and page.workspace is self.workspace
                and page.workbook_id in self.workspace.workbooks):
            return page.context_summary(detailed=detailed)
        if (isinstance(page, XYPlotController) and page.workspace is self.workspace
                and page.comparison_id in self.workspace.comparisons):
            return 'XY graph: ' + page.comparison.name
        if self._context_kind == 'dat' and self.workspace.active is not None:
            session = self.workspace.active
            source = session.source_path.name if session.source_path is not None else 'DAT'
            return source + ' · ' + session.active_graph.name
        return 'Open a Workbook or graph from Project.'



    def _refresh_workspace_shell(self):
        if not hasattr(self, 'dat_details_action'):
            return
        previous = self._properties_controller
        self.source_navigation.sync()
        key = self.graph_documents.registry.active_key
        page = self.graph_documents.document_for(key)
        dat = self._outer_view == 'analysis' or bool(key and len(key) == 2 and key in self.graph_documents.registry.open_keys())
        self._context_kind = 'dat' if dat else 'xy' if isinstance(page, XYPlotController) else 'workbook' if isinstance(page, WorkbookController) else 'empty'
        self._properties_controller = self.plot_page if dat else page
        with QSignalBlocker(self.dat_details_action):
            self.dat_details_action.setChecked(self._dat_details_requested)
        self.app_bridge.commands.sync()
        if isinstance(page, XYPlotController):
            page.set_inspector_checked(self._dat_details_requested)
        self.shellChanged.emit()
        if previous is not self._properties_controller:
            self._activate_visible_xy_analysis()


    # ── expander helper ───────────────────────────────────────────────────────


    # ── row / spin helpers ────────────────────────────────────────────────────



    # ── left panel ────────────────────────────────────────────────────────────


    # ── view settings content (used inside expander) ──────────────────────────


    def _build_view_settings(self):
        self.dat_properties = SharedDatPropertiesBridge(self)
        view = self._dat_view()
        view.colormap = 'RdBu_r'
        settings = self._annotation_settings
        view.fix_annotations = settings.value('plot_annotations/fixed', True, type=bool)
        if view.fix_annotations:
            for name in view.annotations:
                view.annotations[name] = settings.value('plot_annotations/' + name, view.annotations[name], type=str)
            view.signal_title_auto = settings.value('plot_annotations/signal_auto', not settings.contains('plot_annotations/signal_title'), type=bool)
        if not settings.contains('fixed_region_edges/enabled'):
            settings.setValue('fixed_region_edges/enabled', settings.value('fixed_regions/enabled', False, type=bool))
            settings.setValue('fixed_region_edges/recipe', json.dumps({'boundaries': self._fixed_recipe('regions').get('boundaries', [])}))
        for key in ('regions', 'region_edges'):
            (self.workspace.active or self._unregistered_session).fixed_recipes[key].enabled = settings.value('fixed_' + key + '/enabled', False, type=bool)
        self._colormap_manual = False

    def _reveal_ordinary_analysis_y(self, y):
        self.request_task(1)
        self.revealProperties.emit('ordinary', float(y))

    def _reveal_dat_property_y(self, y):
        self.request_task(2)
        self.revealProperties.emit('dat', float(y))

    def _dat_view(self):
        return (self.workspace.active or self._unregistered_session).view

    def _dat_number(self, field):
        return self.dat_properties.number(field)

    def _dat_project_number(self, field, value):
        view = self._dat_view()
        view.bounds[field] = float(value)
        if field in ('xmin', 'xmax'):
            view.x_limits = (view.bounds['xmin'], view.bounds['xmax'])
        elif field in ('ymin', 'ymax'):
            view.y_limits = (view.bounds['ymin'], view.bounds['ymax'])

    def _dat_lock(self, field):
        return bool(self._dat_view().locks[field])

    def _dat_annotation(self, field):
        return self._dat_view().annotations[field]

    def _dat_status(self, field, text):
        self.dat_properties.status(field, text)

    def _commit_dat_numbers(self, numbers):
        with self._edit_transaction('DAT numeric properties', RANGE_FIELDS + COLOR_FIELDS + ('view.norm', 'view.color_region_boundaries')):
            view = self._dat_view()
            # Install every numeric draft before an automatic handler reads one.
            for field, value in numbers.items():
                if field in BOUNDS:
                    view.bounds[field] = value
            for field in ('vmin', 'vmax'):
                if field in numbers:
                    view.locks[field] = True
            if 'linthresh' in numbers:
                view.norm = NormSpec(view.norm.kind, numbers['linthresh'])
            for field in ('edge0', 'edge1'):
                if field in numbers:
                    self._on_color_region_boundary_changed(int(field[-1]), numbers[field], render=False)
            xy = next((field for field in ('xmin', 'xmax', 'ymin', 'ymax') if field in numbers), None)
            if xy is not None:
                self._on_view_limit_changed(xy)
            elif 'linthresh' in numbers:
                self._on_color_mapping_changed()
            for field in ('vmin', 'vmax'):
                if field in numbers:
                    # Restore explicit drafts after automatic regional/range work.
                    view.bounds[field] = numbers[field]
                    self._on_view_limit_changed(field)
            if any(field in numbers for field in ('edge0', 'edge1')):
                self._plot_cube()
            self._capture_dataset_controls(visual_only=True)


    # ── Find Peaks tab ────────────────────────────────────────────────────────

    def _build_plot_panel(self):
        from .analysis_header_bridge import AnalysisHeaderBridge
        from .dat_canvas_controller import DatCanvasController, CanvasServices
        from PySide6.QtGui import QAction
        self.figure = Figure(figsize=(8., 6.), layout='none')
        self.canvas = AnalysisFigureCanvas(self.figure)
        self.canvas.mpl_connect('resize_event', self._layout_live_plots)
        self.canvas.mpl_connect('draw_event', self._update_live_heatmap_tick_contrast)
        self.toolbar = AnalysisNavigationController(self.canvas, self)
        self._analysis_display_zoom = 'Fit'
        self.analysis_header = AnalysisHeaderBridge(self)
        self.dat_canvas = DatCanvasController(self.canvas, CanvasServices(
            validate_peak_menu=self._peak_menu_request_valid, continue_peak_menu=self._continue_peak_menu,
            cancel_canvas_gesture=lambda: self._cancel_canvas_gesture() if hasattr(self, 'dat_canvas') else None,
            schedule_view_layout=self._schedule_view_layout), self)
        self.dat_canvas.window = self
        self.graph_documents = QuickDocuments(self._activate_document, self._finish_close_document, self,
            tooltip=self._document_tooltip,
            input_eligible=lambda: self._outer_view == 'workspace' and not self._project_busy() and not self._project_installing and not self._closing)
        self.graph_documents.layoutChanged.connect(self._schedule_plot_page_render)
        self.graph_documents.layoutChanged.connect(self._refresh_graph_commands)
        self.graph_documents.workspaceChanged.connect(self._schedule_project_modified)
        self.graph_documents.workspaceChanged.connect(self._refresh_navigator_active)
        self.graph_documents.workspaceChanged.connect(self._refresh_graph_commands)
        self.workbook_commands = WorkbookPlotCommandsController(self)
        self.workbook_commands.targetSelected.connect(self._select_comparison_target)
        self.workbook_commands.targetPinChanged.connect(self._pin_comparison_target)
        self.workbook_commands.targetShowRequested.connect(self._show_comparison_target)
        self.workbook_commands.repeatRequested.connect(lambda sheet, target: self._add_sheet_comparison(sheet, target, include_existing=True))
        self._fallback_plot_page = self._create_plot_page()
        self.plot_page = self._fallback_plot_page
        for name, text, handler in (
            ('_graph_copy_svg_action', 'Copy SVG', self._on_copy_graph_svg),
            ('_graph_copy_png_action', 'Copy PNG', self._on_copy_graph_png),
            ('_settings_copy_action', 'Copy settings', self._on_copy_plot_settings),
            ('_settings_paste_action', 'Paste Format', self._on_paste_plot_settings),
            ('_settings_paste_to_action', 'Paste Format to Graphs…', self._on_paste_settings_to_graphs),
            ('_settings_paste_options_action', 'Paste Format Options…', self._on_paste_settings_options),
            ('_settings_adjust_action', 'Adjust Last Paste…', self._on_adjust_last_paste),
            ('_settings_undo_paste_action', 'Undo Last Paste', self._on_undo_last_paste),
            ('_settings_copy_to_action', 'Copy Format to Graphs…', self._on_copy_settings_to_datasets),
            ('_comparison_action', 'Open comparison window', self._on_open_plot_comparison)):
            action = QAction(text, self); action.triggered.connect(handler); setattr(self, name, action)
        self.analysis_navigation = AnalysisNavigationBridge(self, self.toolbar, {
            'copy_svg': self._graph_copy_svg_action, 'copy_png': self._graph_copy_png_action,
            'settings_copy': self._settings_copy_action, 'settings_paste': self._settings_paste_action,
            'settings_copy_to': self._settings_copy_to_action, 'comparison': self._comparison_action,
            'settings_paste_to': self._settings_paste_to_action,
            'settings_paste_options': self._settings_paste_options_action,
            'settings_adjust': self._settings_adjust_action, 'settings_undo_paste': self._settings_undo_paste_action})
        self.analysis_header.stateChanged.connect(self.analysis_navigation.refresh)
        for bridge in (self.dat_properties, self.analysis_header, self.ordinary_analysis, self.processing):
            bridge.draftChanged.connect(self._refresh_paste_commands)
        self.dat_canvas.bind_navigation(self.analysis_navigation)
        self._refresh_transfer_controls()

    # =========================================================================
    #  Helper utilities
    # =========================================================================

    def _build_data_navigator(self):
        self.project = self.app_bridge.project
        # These remain the authoritative QActions for menus and keyboard routes.
        from PySide6.QtGui import QAction
        def action(text, handler):
            result = QAction(text, self)
            result.triggered.connect(handler)
            return result
        self.new_comparison_action = action('New comparison graph', self._new_xy_comparison)
        self.compare_sheets_btn = action('Compare selected data…', self._compare_selected_sheets)
        self.merge_workbooks_btn = action('Move sheets and merge…', self._on_merge_workbooks)
        self.project_analyze_action = action('Analyze', lambda: self._open_analysis(self._project_analysis_source()))
        self._navigator_buttons = {
            text: action(text if text == 'Open' else text + '…', handler)
            for text, handler in (('Open', self._open_navigator_item), ('Rename', self._rename_navigator_item),
                                  ('Remove', self._remove_navigator_item))}
        self.reload_source_action = action('Reload as new copy…', self._on_reload_source_copy)
        self._menu_groups['File'].append(CommandAction('reload', self.reload_source_action, self))
        self.add_gate_comparison_btn = action('Add current DAT Gate to comparison', self._add_current_gate_comparison)
        self.add_gate_again_action = action('Add current DAT Gate again',
            lambda: self._add_current_gate_comparison(include_existing=True))
        actions = [('import_options', self._graph_command_actions['import_options']),
            ('new_comparison', self.new_comparison_action), ('compare', self.compare_sheets_btn),
            ('merge', self.merge_workbooks_btn), ('analyze', self.project_analyze_action),
            ('open', self._navigator_buttons['Open']), ('rename', self._navigator_buttons['Rename']),
            ('remove', self._navigator_buttons['Remove']), ('reload', self.reload_source_action),
            ('add_gate', self.add_gate_comparison_btn), ('add_gate_again', self.add_gate_again_action)]
        adapters = {key: CommandAction(key, value, self.project) for key, value in actions}
        self.project.bind_actions(CommandAction('import', self._graph_command_actions['import'], self.project),
            list(adapters.values()), [adapters[key] for key in
                ('open', 'analyze', 'compare', 'merge', 'rename', 'reload', 'remove')])
        self._refresh_data_navigator()

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
        if not hasattr(self, 'project'):
            return
        rows, items, source_items, owners = [], {}, {}, {}
        source_labels = self._navigator_source_labels()

        def add(parent, key, label, tooltip, description):
            row = ProjectRow(key, label, parent, tooltip, description)
            rows.append(row)
            items[key] = row
            return key

        def source_key(sheet):
            return _source_key(sheet.source_path) if sheet.source_path is not None else None

        for book in self.workspace.workbooks.values():
            sheets = [self.workspace.sheets[sid] for sid in book.sheet_ids]
            sources = {}
            for sheet in sheets:
                sources.setdefault(source_key(sheet), []).append(sheet)
            paths = [key for key in sources if key is not None]
            label = book.name
            if len(sources) == 1 and paths:
                path = sources[paths[0]][0].source_path
                label = source_labels[paths[0]] if book.name == path.name else (
                    book.name + '\u2028' + source_labels[paths[0]])
            elif paths:
                label += f' · {len(paths)} sources'
                if None in sources:
                    label += ' + embedded values'
            else:
                label += f' · {len(sheets)} embedded Sheets'
            item = add(None, ('workbook', book.workbook_id), label,
                       self._workbook_tooltip(book), 'Workbook')
            for path_key, source_sheets in sources.items():
                parent = item
                if len(sources) > 1:
                    path = source_sheets[0].source_path
                    parent = add(item, ('group', ('source', book.workbook_id, path_key)),
                                 source_labels[path_key] if path_key is not None else 'Embedded values',
                                 str(path or 'Embedded values'), 'Source group')
                source_items[(book.workbook_id, path_key)] = parent
                data = add(parent, ('group', ('worksheets', book.workbook_id, path_key)),
                           f'Worksheets ({len(source_sheets)})',
                           'Data sheets for this source', 'Worksheets group')
                for sheet in source_sheets:
                    add(data, ('sheet', sheet.sheet_id), sheet.name,
                        str(sheet.source_path or 'Embedded values'), 'Worksheet')
                    if sheet.kind == 'dat' and sheet.role == 'original':
                        owners.setdefault(sheet.dataset_id, (book.workbook_id, path_key))
        duplicate_labels = {}
        for book in self.workspace.workbooks.values():
            row = items[('workbook', book.workbook_id)]
            duplicate_labels.setdefault(row.label.casefold(), []).append(row.key)
        for duplicates in duplicate_labels.values():
            if len(duplicates) > 1:
                for index, key in enumerate(duplicates, 1):
                    original = items[key]
                    updated = replace(original, label=original.label + f' ({index})')
                    rows[rows.index(original)] = updated
                    items[key] = updated
        heatmaps = {}
        for session in self.workspace.datasets.values():
            graphs = [graph for graph in session.graphs.values() if graph.published and independently_formatted(graph)]
            if not graphs:
                continue
            parent = source_items.get(owners.get(session.dataset_id))
            if parent is None:
                path = session.source_path
                parent = add(None, ('group', ('dataset', session.dataset_id)),
                             source_labels[_source_key(path)] if path is not None else 'Embedded DAT',
                             str(path or 'Embedded DAT'), 'DAT source group')
            heatmaps.setdefault(parent, []).extend((session, graph) for graph in graphs)
        for parent, graphs in heatmaps.items():
            group = add(parent, ('group', ('heatmaps', parent)), f'Heatmaps ({len(graphs)})',
                        'Published heatmap graphs for this source', 'Heatmaps group')
            for session, graph in graphs:
                add(group, ('heatmap', (session.dataset_id, graph.graph_id)), graph.name,
                    f'Heatmap: {graph.name}\n{session.source_path or "Embedded DAT"}', 'Heatmap graph')
        if self.workspace.comparisons:
            group = add(None, ('group', ('comparisons',)),
                        f'Comparisons ({len(self.workspace.comparisons)})',
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
                    f'XY graph: {comparison.name}\n{len(sources)} sources\n{source_paths}', 'XY comparison graph')
        self.project.set_rows(rows)
        self._refresh_navigator_active()
        self._refresh_navigator_actions()

    def _workbook_tooltip(self, book):
        paths = workbook_sources(self.workspace, book)
        return (f'{book.name} · {len(book.sheet_ids)} Sheets · {len(paths)} sources\n'
                + ('\n'.join(str(path) for path in paths) if paths else 'Embedded values'))

    def _refresh_navigator_active(self):
        if not hasattr(self, 'project'):
            return
        key = self.graph_documents.registry.active_key if self._outer_view == 'workspace' else self._document_key()
        active = set()
        if key:
            active.add((key[0], key[1]) if len(key) == 3 else ('heatmap', key))
            page = self.graph_documents.document_for(key)
            if len(key) == 3 and key[0] == 'workbook' and isinstance(page, WorkbookController):
                sheet_page = page.current_sheet
                if sheet_page is not None:
                    active.add(('sheet', sheet_page.sheet_id))
        self.project.set_active(active)

    def _selected_sheet_ids(self):
        selected = set(self.project.selected_keys())
        return [sid for book in self.workspace.workbooks.values() for sid in book.sheet_ids
                if ('sheet', sid) in selected or (('workbook', book.workbook_id) in selected
                    and self.workspace.sheets[sid].role == 'original')]

    def _refresh_navigator_actions(self):
        if not hasattr(self, 'compare_sheets_btn'):
            return
        idle = not self._project_busy()
        selected = self.project.selected_keys()
        key = selected[0] if len(selected) == 1 else None
        editable = bool(key and key[0] in ('sheet', 'workbook', 'xy'))
        current_key = self.project.current_key()
        self.compare_sheets_btn.setEnabled(idle and any(key[0] in ('sheet', 'workbook') for key in selected))
        self.merge_workbooks_btn.setEnabled(idle and len(self._selected_workbook_ids()) >= 2)
        for name in ('Rename', 'Remove'):
            self._navigator_buttons[name].setEnabled(idle and editable)
        remove = self._navigator_buttons['Remove']
        target = self.project.row_for_key(selected[0]).label if len(selected) == 1 else f'{len(selected)} selected items'
        remove.setText(f'Remove {target}…' if selected else 'Remove…')
        reason = self._navigator_remove_reason(key)
        remove.setEnabled(idle and editable and not reason)
        remove.setToolTip(reason or (f'Remove {target} from this project. Source files remain unchanged.' if selected else 'Select one Project item.'))
        self.reload_source_action.setEnabled(idle and bool(self._selected_reload_paths()))
        self.reload_source_action.setToolTip('Read selected original sources from disk into new Workbooks. Existing data, graphs and results stay unchanged.')
        self._navigator_buttons['Open'].setEnabled(idle and bool(current_key and
            current_key[0] in ('sheet', 'workbook', 'xy', 'heatmap')))
        self.new_comparison_action.setEnabled(idle)
        self.add_gate_comparison_btn.setEnabled(idle and self.workspace.active is not None
            and not self._auxiliary_active() and (self._outer_view == 'analysis' or
            self._document_key() in self.graph_documents.registry.open_keys())
            and (not self._comparison_target_pinned or self._last_comparison_id in self.workspace.comparisons))
        self.add_gate_again_action.setEnabled(self.add_gate_comparison_btn.isEnabled()
            and self._last_comparison_id in self.workspace.comparisons)
        self.project_analyze_action.setEnabled(idle and self._project_analysis_source() is not None)
        self.project.sync()

    def _navigator_selection_changed(self):
        self._refresh_navigator_actions()
        key = self.project.current_key()
        if key and key[0] == 'xy' and not self._comparison_target_pinned:
            self._last_comparison_id = key[1]
            self._refresh_comparison_targets(self._last_comparison_id)

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
                page = self.graph_documents.document_for(('workbook', identity, ''))
                if isinstance(page, WorkbookController) and page.current_sheet is not None:
                    current = self._resolve_analysis_source(('sheet', page.current_sheet.sheet_id))
                    if current is not None:
                        return current
                sources = [self._resolve_analysis_source(('sheet', sid)) for sid in book.sheet_ids]
                sources = [item for item in sources if item is not None]
                if len({self.workspace.sheets[item[1]].dataset_id for item in sources}) == 1:
                    return sources[0]
        return None

    def _project_analysis_source(self):
        selected = self.project.selected_keys()
        return self._resolve_analysis_source(selected[0]) if len(selected) == 1 else None

    def _active_analysis_source(self):
        if self._outer_view == 'analysis':
            return self._resolve_analysis_source(('heatmap', self._document_key()))
        key = self.graph_documents.registry.active_key
        if key is None or key not in self.graph_documents.registry.open_keys():
            return None
        if len(key) == 2:
            return self._resolve_analysis_source(('heatmap', key))
        if key[0] == 'xy':
            return self._resolve_analysis_source(('xy', key[1]))
        if key[0] == 'workbook':
            page = self.graph_documents.document_for(key)
            sheet_page = page.current_sheet if isinstance(page, WorkbookController) else None
            if sheet_page is not None:
                return self._resolve_analysis_source(('sheet', sheet_page.sheet_id))
        return None

    def _open_analysis(self, source):
        source = self._resolve_analysis_source(source)
        if source is None:
            self.publish_status('Choose an XY graph, DAT heatmap, Original sheet or Processed sheet to analyze.')
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
                page.analysis_controller.analyze_selected()
                return
            if kind == 'heatmap':
                dataset_id, graph_id = identity
                if self._outer_view == 'analysis' and identity == self._document_key():
                    self._set_dat_details_requested(True)
                    self.request_task(1)
                    self._refresh_analysis_context()
                    return
                self.open_graph_document(dataset_id, graph_id)
            else:
                sheet = self.workspace.sheets[identity]
                dataset_id = sheet.dataset_id
                self.open_workbook(self._sheet_owner(identity).workbook_id, identity)
            return_key = self.graph_documents.registry.active_key
            self.request_outer_view('analysis')
            if self._outer_view != 'analysis':
                return
            if dataset_id != self.workspace.active_dataset_id:
                self.activate_dataset(dataset_id)
            self._analysis_return_key = return_key
            self._set_dat_details_requested(True)
            self.request_task(1)
            self._refresh_graph_commands()
        except ValueError as exc:
            self.publish_status(str(exc))

    def _back_to_workspace(self):
        try:
            self._require_project_idle()
            self._finish_current_document()
            key = self._analysis_return_key
            if key is None:
                key = self.graph_documents.registry.active_key
            if key and document_exists(self.workspace, key) and key in self.graph_documents.registry.open_keys():
                if len(key) == 2:
                    self.open_graph_document(*key)
                else:
                    self._open_auxiliary_document(key)
            else:
                # A closed/removed source stays closed. Do not reopen the
                # scientific selection just because the container changes.
                self.request_outer_view('workspace')
                active = self.graph_documents.registry.active_key
                if active and document_exists(self.workspace, active):
                    self._activate_document(active)
                self._outer_view_changed(restore_graph=False)
        except ValueError as exc:
            self.publish_status(str(exc))

    def _refresh_analysis_context(self):
        if not hasattr(self, 'analysis_header'):
            return
        context = self._workbench_update_input_context()
        session = self.workspace.active
        if context is None and session is not None and session.current_cube is not None:
            cube, graph = session.current_cube, session.active_graph
            applied = 'Energy 2nd derivative' if session.applied.mode == 'energy_second_derivative' else 'Original'
            index = self._current_gate_index()
            title = session.source_path.name if session.source_path else 'Embedded data'
            displayed = 'Original' if graph.source_mode == 'raw' else applied
            count = cube.gate.size if graph.gate_indices is None else len(graph.gate_indices)
            details = (f'Analysis — {title} · {graph.name}\n{session.source_path or title}\n'
                f'Analysis input: {applied} · Gate: {cube.gate[index]:g} ({index+1}/{cube.gate.size})\n'
                f'Batch: all {cube.gate.size} Gates\n'
                f'Graph display: {displayed} · {count} of {cube.gate.size} Gates\n'
                f'Processing recipe: {session.applied.recipe()}')
            if displayed != applied or count != cube.gate.size:
                details += ('\nThe live heatmap/spectrum use the graph display; analysis uses the input above, '
                            'and batch analysis includes every Gate.')
            context = dict(title=title, summary=f'{applied} · {count}/{cube.gate.size} Gates · analyzed all',
                           warning='', details=details)
        self.analysis_header.refresh(context)
        self.ordinary_analysis.refresh()

    def _selected_workbook_ids(self):
        selected = set(self.project.selected_keys())
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
                page = self.graph_documents.document_for(key)
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
        selected, accepted = prompt_item(self.root_window, 'Move sheets and merge', 'Destination Workbook:', labels, 0)
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
                if prompt_message(self.root_window, 'Move sheets and merge', summary,
                        (PromptAction.YES, PromptAction.CANCEL), PromptAction.CANCEL, kind='warning') != PromptAction.YES:
                    return
                self.merge_workbooks(identities, destination)
            except ValueError as exc:
                self._show_error(str(exc))

    def _on_save_sheet_snapshot(self, sheet_id):
        try:
            self._require_project_idle()
            if sheet_shape(self.workspace, sheet_id)[0] == 0:
                raise ValueError('There are no current results to snapshot.')
            name, accepted = prompt_text(self.root_window, 'Save snapshot', 'Snapshot name:')
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
        key = self.project.current_key()
        if key is None or not self.project.enabled:
            return
        kind, identity = key
        if kind == 'group':
            if toggle_group:
                self.project.set_expanded(key, not self.project.is_expanded(key))
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
        selected = self.project.selected_keys()
        if len(selected) != 1:
            return
        kind, identity = selected[0]
        if kind not in ('sheet', 'workbook', 'xy'):
            return
        try:
            self._require_project_idle()
            self._finish_current_document()
            record = {'sheet': self.workspace.sheets, 'workbook': self.workspace.workbooks,
                      'xy': self.workspace.comparisons}[kind][identity]
            name, ok = prompt_text(self.root_window, 'Rename', 'Name:', text=record.name)
            if ok and name.strip() and '\x00' not in name:
                record.name = name.strip()
                if kind == 'xy':
                    page = self.graph_documents.document_for(('xy', identity, ''))
                    if page is not None:
                        page.sync_graph_name()
                else:
                    self._refresh_auxiliary_documents()
                self._auxiliary_changed()
        except ValueError as exc:
            self._show_error(str(exc))

    def _remove_navigator_item(self):
        selected = self.project.selected_keys()
        if len(selected) != 1:
            return
        kind, identity = selected[0]
        target_name = self.project.row_for_key(selected[0]).label
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
            if sheet is not None and sheet.kind == 'dat' and sheet.result_id is None:
                self.remove_dataset(sheet.dataset_id)
                return
            message = (f'Remove Processed result “{target_name}” and its linked Heatmaps? Its curves are also removed from comparisons. Other results and source files remain unchanged.'
                       if sheet is not None and sheet.result_id is not None else
                       f'Remove {target_name}? Removing a Sheet or workbook also removes its curves from comparisons. Source files remain unchanged.')
            if prompt_message(self.root_window, 'Remove from project', message,
                    (PromptAction.YES, PromptAction.CANCEL), PromptAction.CANCEL, kind='warning') != PromptAction.YES:
                return
            self._require_project_idle()
            result_owner = self.workspace.datasets.get(sheet.dataset_id) if sheet is not None and sheet.result_id is not None else None
            {'sheet': remove_sheet, 'workbook': remove_workbook, 'xy': remove_comparison}[kind](self.workspace, identity)
            self._refresh_auxiliary_documents()
            if result_owner is self.workspace.active and result_owner is not None:
                self.activate_dataset(result_owner.dataset_id, capture_outgoing=False)
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
            if sheet.kind == 'dat' and sheet.role != 'original' and sheet.result_id is None:
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
        if not hasattr(self, 'project'):
            return []
        selected = self.project.selected_keys()
        if len(selected) != 1:
            return []
        kind, identity = selected[0]
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
        self.set_cancel_visible(True)
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
        self.set_cancel_visible(False)
        failures = '\n'.join(f'{error.path}: {error.reason}' for error in errors)
        if new_copy and failures:
            message = 'Cannot reload as new copy. No sources were added:\n' + failures
            self.source_navigation.set_import_status(message)
            self.publish_status(message)
            self._show_error(message)
            return
        if not entries:
            self.source_navigation.set_import_status('No data imported.\n' + failures)
            self.publish_status('No data imported. See the file errors for details.')
            self._show_error('No data imported:\n' + failures)
            return
        dialog = None
        try:
            if review or comparison_id is not None:
                dialog = XYImportDialog([entry.table for entry in entries], self.workspace.workbooks, self.root_window,
                    entries=entries, mappings=[(entry.mapping[0], list(entry.mapping[1])) for entry in entries],
                    reused=[entry.sheet_id is not None for entry in entries],
                    comparisons=self.workspace.comparisons, comparison_id=comparison_id)
                if failures:
                    dialog.set_feedback('These files could not be read; the valid files are ready to review:\n' + failures)
                if dialog.exec() != 1:
                    message = 'Data import cancelled. No sources were added.'
                    self.source_navigation.set_import_status(message)
                    self.publish_status(message)
                    return
                if token != self._dat_import_token or workspace is not self.workspace or self._close_pending:
                    return
                entries = dialog.entries
                destination = dialog.destination
                self.import_data_files(entries, None if destination == 'per_file' else destination,
                    mappings=dialog.mappings, compare=dialog.compare, workbook_name=dialog.workbook_name,
                    grouping='per_file' if destination == 'per_file' else 'single',
                    comparison_id=(comparison_id if comparison_id is not None else
                                   dialog.comparison_id) if dialog.compare else None,
                    include_existing=dialog.include_existing, new_copy=new_copy)
            else:
                self.import_data_files(entries, new_copy=new_copy)
            notices = [entry.session.initialization_notice for entry in entries
                       if entry.kind == 'dat' and entry.session is not None and entry.session.initialization_notice]
            if failures:
                self.publish_status(self._status_text
                    + f' {len(errors)} file(s) failed; see the file errors for details.')
            self.source_navigation.set_import_status(self._status_text
                + ('\n' + failures if failures else '') + ('\n' + '\n'.join(notices) if notices else ''))
            if failures and dialog is None:
                self._show_error('Valid data imported into Workspace. These files could not be imported:\n' + failures)
        except (ValueError, OSError, KeyError) as exc:
            message = f'Cannot import data: {exc}'
            self.source_navigation.set_import_status(message)
            self.publish_status(message)
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
            self.publish_status(self._status_text + ' '
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
            if comparison_id is None:
                initialize_comparison_labels(staged, comparison)
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
            self.publish_status(self._status_text + ' '
                + self._import_source_summary(new_sources, reused_sources, new_copy=new_copy))
        else:
            # Other imported owners remain available in Project without opening more windows.
            self.open_workbook(book.workbook_id, sheets[0].sheet_id)
            self.publish_status(self._import_source_summary(new_sources, reused_sources, new_copy=new_copy))
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
            self.publish_status(self._status_text + ' '
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
            if comparison_id is None:
                initialize_comparison_labels(self.workspace, comparison)
            if target_page is not None:
                target_page.refresh()
            self._auxiliary_changed()
            self.open_xy_comparison(comparison.comparison_id)
            self._report_curve_additions(comparison, len(additions), skipped)
            self.publish_status(self._status_text + ' '
                + self._import_source_summary(len(keys)-reused, reused))
        else:
            self.open_workbook(book.workbook_id, sheets[0].sheet_id)
            self.publish_status(self._import_source_summary(len(keys)-reused, reused))
        return book

    def _on_import_xy(self, comparison_id=None):
        # Compatibility hook used by comparison pages and existing integrations.
        self._on_import_data(comparison_id, review=True)

    def _on_import_data(self, comparison_id=None, *, review=False):
        if comparison_id is False:
            comparison_id = None
        if self._project_busy() or self._close_pending:
            return
        paths, _ = FilePicker.getOpenFileNames(self.root_window, 'Import with options' if review else 'Import data', self.current_folder, DATA_FILTER)
        if paths:
            self._import_data_files(paths, comparison_id, review=review)

    def _auxiliary_active(self):
        key = self.graph_documents.registry.active_key
        return bool(self._outer_view == 'workspace' and key and len(key) == 3)

    def _finish_auxiliary_editor(self, key):
        page = self.graph_documents.document_for(key)
        if isinstance(page, (XYPlotController, WorkbookController)):
            page.finish_editor()

    def _finish_current_document(self):
        if self._auxiliary_active():
            self._finish_auxiliary_editor(self.graph_documents.registry.active_key)
        elif self.workspace.active is not None:
            self._finish_graph_editors()

    def _create_auxiliary_page(self, key):
        if key[0] == 'workbook':
            page = WorkbookController(self.workspace, key[1], self)
            page.draftChanged.connect(self._schedule_project_modified)
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
            page = XYPlotController(self.workspace, key[1], self)
            page.bind_canvas_actions({name: self._graph_command_actions[name] for name in ('rescale', 'undo', 'redo')})
            page.historyChanged.connect(self._refresh_graph_commands)
            page.analysis_controller.services = XYAnalysisServices(
                project_busy=self._project_busy, require_project_idle=self._require_project_idle,
                current_workspace=lambda: self.workspace,
                background_owner=lambda: self._background_job_owner,
                start_background_job=self._start_background_job,
                publish_results=self._save_xy_analysis_results)
            page.analysis_controller.sync()
            page.analysisRequested.connect(lambda owner=key[1]: self._open_analysis(('xy', owner)))
            page.inspectorVisibilityRequested.connect(self._set_dat_details_requested)
            page.inspectorSectionRequested.connect(lambda section, field, target=page: self.reveal_xy_inspector(target, section, field))
            page.canvasFocusRequested.connect(lambda owner=key: self.graph_documents.focus_document(owner))
            page.sourceRequested.connect(lambda identity: self.open_workbook(self._sheet_owner(identity).workbook_id, identity))
            page.addDataRequested.connect(lambda owner=key[1]: self._show_sheet_comparison_dialog(comparison_id=owner))
            page.importDataRequested.connect(lambda owner=key[1]: self._on_import_xy(comparison_id=owner))
        page.changed.connect(self._auxiliary_changed)
        return page

    def _open_auxiliary_document(self, key):
        self._require_project_idle()
        if not document_exists(self.workspace, key):
            raise ValueError('The document no longer exists.')
        if key != self.graph_documents.registry.active_key or self._outer_view != 'workspace':
            self._finish_current_document()
        page = self.graph_documents.document_for(key)
        if page is None:
            page = self._create_auxiliary_page(key)
            self.graph_documents.cache_document(key, page)
        self.graph_documents.show_document(key, self._document_title(key))
        if key[0] == 'xy' and not self._comparison_target_pinned:
            self._last_comparison_id = key[1]
            self._refresh_comparison_targets(self._last_comparison_id)
        self.request_outer_view('workspace')
        self._outer_view_changed()
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
            page = self.graph_documents.document_for(key)
            if isinstance(page, XYPlotController):
                page.analysis_controller.background_finished(cancelled=cancelled)

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
        self.workbook_commands.set_target_pinned(self._comparison_target_pinned)
        for key in self.graph_documents.keys():
            page = self.graph_documents.document_for(key)
            if isinstance(page, WorkbookController):
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
            # Capture the outgoing controls before changing even a same-DAT
            # graph alias. The incoming activation can then project its final
            # source and view once, using the normal owner/job lifecycle.
            self._finish_graph_editors()
            notice = self._save_current_plot_state()
            previous_dataset_id = self.workspace.active_dataset_id
            previous_graph_id = session.active_graph_id
            # A repeated command opens its exact source scope; existing graph visuals are retained.
            graph = next((graph for graph in session.graphs.values()
                          if graph.analysis is None and graph.source_mode == sheet.mode
                          and graph.result_id == sheet.result_id and graph.gate_indices == gates), None)
            created = graph is None
            published = graph.published if graph is not None else False
            try:
                if created:
                    base_name = f'{sheet.name} Heatmap'
                    name, count = base_name, 2
                    while any(graph.name == name for graph in session.graphs.values()):
                        name, count = f'{base_name} {count}', count + 1
                    graph = session.create_graph(name)
                    graph.source_mode, graph.gate_indices = sheet.mode, gates
                    graph.result_id = sheet.result_id
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
                self.activate_dataset(session.dataset_id, capture_outgoing=False)
            except Exception:
                session.activate_graph(previous_graph_id)
                if graph is not None:
                    graph.published = published
                    if created:
                        del session.graphs[graph.graph_id]
                if self.workspace.active_dataset_id == session.dataset_id:
                    self._activate_dataset(previous_dataset_id, capture_outgoing=False)
                if created and graph is not None:
                    key = (session.dataset_id, graph.graph_id)
                    self.graph_documents.discard(key)
                    self._graph_revisions.pop(key, None)
                raise
            if graph.analysis is not None:
                self._project_selected_graph(session)
                if notice:
                    self.publish_status(self._status_text + ' — ' + notice)
            else:
                self._refresh_document_titles()
                incoming_notice = self._save_current_plot_state()
                self._refresh_project_modified()
                self.publish_status(' — '.join(part for part in (notice, incoming_notice) if part))
            self._select_graph_page(session, show=True)
            self.request_outer_view('workspace')
            self._outer_view_changed()
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
            return self.graph_documents.document_for(('xy', comparison.comparison_id, ''))
        except ValueError as exc:
            self._show_error(str(exc))

    def _compare_selected_sheets(self):
        selected = self._selected_sheet_ids()
        if selected:
            self._show_sheet_comparison_dialog(selected_sheet_ids=selected)
        else:
            self.publish_status('The selected Workbooks contain no Original Sheets. Select individual result Sheets to compare.')

    def _show_sheet_comparison_dialog(self, *, selected_sheet_ids=(), comparison_id=None):
        try:
            self._require_project_idle()
            if comparison_id is not None and comparison_id not in self.workspace.comparisons:
                raise ValueError('The destination comparison no longer exists.')
            dialog = SheetComparisonDialog(self.workspace, self.root_window, selected_sheet_ids=selected_sheet_ids,
                                           comparison_id=comparison_id)
            try:
                if dialog.exec() == 1:
                    target = comparison_id if comparison_id is not None else dialog.comparison_id
                    self.compare_sheets(dialog.selected_mappings(), target,
                                        include_existing=dialog.include_existing)
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
        target_page = self.graph_documents.document_for(('xy', comparison_id, '')) if comparison_id else None
        current_page = self.graph_documents.document_for(self.graph_documents.registry.active_key) if self._auxiliary_active() else None
        # Check both drafts first: an invalid target must not commit a valid
        # current graph's draft, or any source defaults, before it fails.
        for page in (current_page, target_page):
            if isinstance(page, XYPlotController):
                page.validate_editor()
        return target_page

    def _finish_comparison_append(self, target_page):
        current_page = self.graph_documents.document_for(self.graph_documents.registry.active_key) if self._auxiliary_active() else None
        self._finish_current_document()
        if target_page is not None and target_page is not current_page:
            target_page.finish_editor()

    def _report_curve_additions(self, comparison, added, skipped):
        self.publish_status(f'Added {added} curve(s) to {comparison.name}; skipped {skipped} existing curve(s).')

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
        if comparison_id is None:
            initialize_comparison_labels(self.workspace, comparison)
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
            workbook = self.graph_documents.document_for(key)
            if not isinstance(workbook, WorkbookController):
                continue
            for sheet_id in sheet_ids.intersection(workbook.sheet_pages):
                page = workbook.sheet_pages[sheet_id]
                state = page.capture_view_state()
                page.refresh()
                page.restore_view_state(state)

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
            if creating:
                initialize_comparison_labels(self.workspace, self.workspace.comparisons[comparison_id])
            if not self._comparison_target_pinned:
                self._last_comparison_id = comparison_id
            page = self.graph_documents.document_for(('xy', comparison_id, ''))
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
            self.publish_status(str(exc))

    def _indicate_sheet_gate(self):
        for key in self.graph_documents.keys():
            page = self.graph_documents.document_for(key)
            if isinstance(page, WorkbookController):
                for sheet_page in page.sheet_pages.values():
                    sheet = self.workspace.sheets.get(sheet_page.sheet_id)
                    if sheet is None:
                        continue
                    active = sheet.kind == 'dat' and sheet.role in ('original', 'processed') and sheet.dataset_id == self.workspace.active_dataset_id
                    sheet_page.model.indicate_gate(self._current_gate_index() + 1 if active else None)

    def _refresh_auxiliary_documents(self):
        for key in self.graph_documents.keys():
            if len(key) == 2 and not document_exists(self.workspace, key):
                if self.plot_page is self.graph_documents.document_for(key):
                    self._bind_plot_page(self._fallback_plot_page)
                self.graph_documents.discard(key)
                self._graph_revisions.pop(key, None)
                session = self.workspace.datasets.get(key[0])
                if session is not None:
                    self._history_for(session).remove_graph(key[1])
                continue
            if len(key) == 3:
                if not document_exists(self.workspace, key):
                    self.graph_documents.discard(key)
                else:
                    page = self.graph_documents.document_for(key)
                    if isinstance(page, XYPlotController) and page.has_pending_project_edits():
                        page.refresh_sources()
                        page.render()
                    else:
                        page.refresh()
                    self.graph_documents.refresh_document_input(key)
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
        page = FormalPlotController(self, prompt_window=lambda: self.root_window)
        page.analysisRequested.connect(lambda owner=key:
            self._open_analysis(('heatmap', owner)) if owner is not None else None)
        page.set_mutation_guard(lambda owner=key, target=page: self._formal_page_can_mutate(target, owner))
        page.preview.set_format_guard(lambda target=page:
            self.plot_page is target and self._outer_view == 'workspace')
        page.formal.set_properties_input_guard(lambda source, owner=key, target=page:
            self._formal_properties_input_allowed(target, owner, source))
        for signal, callback in (

                (page.exportRequested, self._on_export_plot_page),
                (page.graphCopyRequested, self._on_copy_graph_svg),
                (page.graphPngCopyRequested, self._on_copy_graph_png),
                (page.propertyEditRequested, self._show_plot_property),
                (page.formatSaveRequested, self._on_save_plot_format),
                (page.formatLoadRequested, self._on_load_plot_format),
                (page.formatDeleteRequested, self._on_delete_plot_format),
                (page.formatApplyRequested, self._on_apply_plot_format),
                (page.settingsCopyRequested, self._on_copy_plot_settings),
                (page.settingsPasteRequested, self._on_paste_plot_settings),
                (page.settingsPasteToRequested, self._on_paste_settings_to_graphs),
                (page.settingsPasteOptionsRequested, self._on_paste_settings_options),
                (page.settingsAdjustRequested, self._on_adjust_last_paste),
                (page.settingsUndoPasteRequested, self._on_undo_last_paste),
                (page.settingsCopyToRequested, self._on_copy_settings_to_datasets),
                (page.comparisonRequested, self._on_open_plot_comparison),
                (page.batchExportRequested, self._on_batch_export_plot),
                (page.batchCancelRequested, self.cancel_plot_batch),
                (page.batchResultsRequested, self._on_plot_batch_results)):
            signal.connect(lambda *args, owner=key, handler=callback:
                           self._dispatch_document(owner, handler, *args))
        page.displayZoomChanged.connect(lambda text: self._sync_display_zoom(text)
                                        if self.plot_page is page else None)
        # Formal preview Fit/scale follows its own paper viewport. Analysis
        # derives scale from owner zoom and its DAT viewport/DPR instead.
        page.presentationChanged.connect(lambda presentation, owner=key, target=page:
            self._owned_plot_presentation(target, owner, presentation))
        page.presentationError.connect(lambda message, owner=key, target=page:
            self._owned_plot_error(target, owner, message))
        page.formal.draftChanged.connect(self._schedule_project_modified)
        page.formal.draftChanged.connect(self._refresh_paste_commands)
        page.formal.focusRequested.connect(lambda section, field, target=page:
            self._reveal_formal_editor(target, section, field))
        return page

    def _reveal_formal_editor(self, page, section, field):
        if page is not self.plot_page or page is not self._properties_controller:
            return
        self._set_dat_details_requested(True)
        if self.taskIndex == 2:
            return
        owner = page.expected_owner
        self.request_task(2)

        def focus_owner():
            if (page is self.plot_page and page is self._properties_controller
                    and page.expected_owner == owner and self.taskIndex == 2):
                page.formal.focusRequested.emit(section, field)

        QTimer.singleShot(0, self, focus_owner)

    def _formal_properties_input_allowed(self, page, key, source):
        from PySide6.QtQuick import QQuickItem
        from shiboken6 import isValid
        host = getattr(self, '_quick_host', None)
        if (host is None or self._closing or self._context_kind != 'dat'
                or not self.propertiesVisible or self.taskIndex != 2
                or page is not self.plot_page or page is not self._properties_controller
                or self._workbench_document() is not None
                or not self._formal_page_can_mutate(page, key)):
            return False
        if key is not None and (key != self._document_key()
                or self.graph_documents.document_for(key) is not page):
            return False
        if self._outer_view == 'workspace' and key != self.graph_documents.registry.active_key:
            return False
        properties = host.window.findChild(QQuickItem, 'applicationProperties')
        receiver = host._floating_properties if self.propertiesFloating else host.window
        return bool(isinstance(source, QQuickItem) and isValid(source)
            and properties is not None and isValid(properties)
            and properties.findChild(QQuickItem, 'formalProperties') is source
            and source.property('bridge') is page.formal
            and properties.isVisible() and properties.isEnabled()
            and source.isVisible() and source.isEnabled()
            and properties.isAncestorOf(source)
            and receiver is not None and isValid(receiver) and receiver.isVisible()
            and receiver.contentItem().isAncestorOf(properties))

    def _formal_page_can_mutate(self, page, key):
        if page is getattr(self, 'plot_page', None) and self._workbench_document() is not None:
            return False
        if (self._project_busy() or self._close_pending or self._project_io_close_pending
                or self._restoring_dataset or self._project_installing):
            return False
        if key is None:
            return page is self._fallback_plot_page if hasattr(self, '_fallback_plot_page') else True
        if not document_exists(self.workspace, key):
            return False
        return page.expected_owner is None or page.expected_owner == self._plot_owner_token(key)

    def _owned_plot_presentation(self, page, key, presentation):
        if not self._formal_page_can_mutate(page, key):
            return
        if not self._binding_graph_document:
            self._dispatch_document(key, self.set_plot_presentation, presentation)
            return
        session = (self.workspace.datasets.get(key[0]) if key is not None else
                   self.workspace.active or self._unregistered_session)
        graph_id = key[1] if key is not None else session.active_graph_id
        if session is None or graph_id not in session.graphs:
            return
        graph = session.graphs[graph_id]
        if graph.presentation == presentation:
            return
        owner = replace(session, active_graph_id=graph_id)
        before = capture_fields(owner, ('presentation',)) if self._history_enabled() else None
        if before is not None:
            self._finish_history_edit()
        previous = graph.presentation
        graph.presentation = presentation
        page.set_presentation(presentation)
        self._invalidate_graph_document((session.dataset_id, graph_id), kind='presentation')
        if before is not None:
            label = 'Edit annotation' if previous.annotations != presentation.annotations else 'Plot presentation'
            self._record_history(session, label, before, capture_fields(owner, ('presentation',)), graph_id=graph_id)
        self._schedule_project_modified()

    def _owned_plot_error(self, page, key, message):
        if key is not None and document_exists(self.workspace, key):
            self._invalidate_graph_document(key, kind='presentation')
        elif page is self.plot_page:
            self._invalidate_plot_page(kind='presentation', schedule=False)
        page.set_status_message(f'Cannot prepare page: {message}')

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
                self.graph_documents.update_title(key, self._document_title(key))
        self._refresh_data_navigator()

    def _document_tooltip(self, key):
        if not document_exists(self.workspace, key):
            return ''
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
        if self.plot_page is page:
            return
        self.plot_page = page
        self._refresh_graph_command_host()
        self._sync_display_zoom(page.zoom_text)
        if hasattr(self, '_plot_format_store'):
            self._refresh_plot_formats()
        if hasattr(self, '_settings_paste_action'):
            self._refresh_transfer_controls()

    def _refresh_graph_command_host(self):
        self.workbook_commands.bind(None)
        if hasattr(self, 'analyze_action'):
            self.analyze_action.setEnabled(not self._project_busy() and self._active_analysis_source() is not None)
        key = self.graph_documents.registry.active_key
        page = self.graph_documents.document_for(key)
        self._command_controller = page if self._outer_view == 'workspace' else None
        if isinstance(page, WorkbookController) and self._outer_view == 'workspace':
            sheet = page.current_sheet
            if sheet is not None and sheet.sheet_id in self.workspace.sheets:
                self.workbook_commands.bind(sheet)
        self.shellChanged.emit()

    def _select_graph_page(self, session, *, show=None):
        key = (session.dataset_id, session.active_graph_id)
        page = self.graph_documents.document_for(key)
        if page is None:
            page = self._create_plot_page(key)
            page.set_presentation(graph_presentation(session))
            page.invalidate(self._plot_owner_token(key))
            self.graph_documents.cache_document(key, page)
        self._bind_plot_page(page)
        if show is None:
            show = self._outer_view == 'workspace' and not self._auxiliary_active()
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
            self._finish_auxiliary_editor(self.graph_documents.registry.active_key)
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
                    self.publish_status(self._status_text + ' — ' + notice)
            else:
                self.activate_graph(graph_id)
        if not session.graphs[graph_id].published:
            session.graphs[graph_id].published = True
            self._refresh_graph_selector()
            self._refresh_document_titles()
            notice = self._save_current_plot_state()
            if notice:
                self.publish_status(notice)
            self._schedule_project_modified()
        page = self._select_graph_page(session, show=True)
        self.request_outer_view('workspace')
        self._outer_view_changed()
        self._refresh_graph_commands()
        return page

    def _activate_document(self, key):
        if len(key) == 3:
            if key == self.graph_documents.registry.active_key and self._auxiliary_active():
                return not self._project_busy()
            try:
                self._require_project_idle()
                if not document_exists(self.workspace, key):
                    raise ValueError('The document no longer exists.')
                self._finish_current_document()
                # Registry activation invokes this before committing its active
                # key. Project the domain owner only; opening a frame here
                # would recursively invoke the same activation callback.
                self._outer_view = 'workspace'
                self._task_index = self._heatmap_task_index
                return True
            except ValueError as exc:
                self.publish_status(str(exc))
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
            self.publish_status(str(exc))
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
            self.publish_status(str(exc))
            return False

    def _close_visible_document(self):
        if self._outer_view != 'workspace':
            return False
        return self.graph_documents.close_document()

    def _discard_dataset_documents(self, dataset_id):
        self._formal_peak_label_signatures.pop(dataset_id, None)
        keys = [key for key in self.graph_documents.keys() if len(key) == 2 and key[0] == dataset_id]
        if any(self.graph_documents.document_for(key) is self.plot_page for key in keys):
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
        from PySide6.QtGui import QAction
        self._menu_groups = {name: [] for name in ('File', 'Edit', 'View', 'Analysis', 'Help')}
        def action(key, text, callback=None, group='View', shortcut='', checkable=False, checked=False):
            result = QAction(text, self)
            if callback is not None:
                result.triggered.connect(callback)
            result.setShortcut(QKeySequence(shortcut))
            result.setCheckable(checkable)
            result.setChecked(checked)
            self._menu_groups[group].append(CommandAction(key, result, self))
            return result
        self.check_updates_action = action('updates', 'Check for Updates…', self._show_updates, 'Help')
        self.analyze_action = action('analyze', 'Analyze', lambda: self._open_analysis(self._active_analysis_source()), 'Analysis')
        self.back_to_workspace_action = action('back', 'Back to Workspace', self._back_to_workspace, 'Analysis')
        self.project_panel_action = action('project', 'Project tree', group='View', shortcut='Ctrl+Shift+P', checkable=True, checked=True)
        self.project_panel_action.toggled.connect(self._set_project_panel_visible)
        self.dat_details_action = action('properties', 'Properties', checkable=True, checked=True)
        self.dat_details_action.toggled.connect(self._set_dat_details_requested)
        self.open_graph_action = action('open_graph', 'Open Graph', self._on_open_graph_document)
        self.free_graphs_action = action('free', 'Free Windows', lambda: self.graph_documents.setMode('free'), checkable=True)
        self.tabbed_graphs_action = action('tabbed', 'Tabbed View', lambda: self.graph_documents.setMode('tabbed'), checkable=True)
        self.cascade_graphs_action = action('cascade', 'Cascade Graphs', self.graph_documents.cascade)
        self.tile_graphs_action = action('tile', 'Tile Graphs', self.graph_documents.tile)
        self.close_graph_view_action = action('close_document', 'Close Document View', self._close_visible_document)
        self._graph_command_actions = {}
        for key, group, text, shortcut, callback in (
            ('new_project', 'File', 'New Project', 'Ctrl+N', self._on_new_project),
            ('open_project', 'File', 'Open Project…', 'Ctrl+Shift+O', self._on_open_project),
            ('save_project', 'File', 'Save Project', 'Ctrl+S', self._on_save_project),
            ('save_project_as', 'File', 'Save Project As…', 'Ctrl+Shift+S', lambda: self._on_save_project(save_as=True)),
            ('import', 'File', 'Import data…', 'Ctrl+O', self._on_import_data),
            ('import_options', 'File', 'Import with options…', '', lambda: self._on_import_data(review=True)),
            ('undo', 'Edit', 'Undo', 'Ctrl+Z', self.undo_edit),
            ('redo', 'Edit', 'Redo', 'Ctrl+Y', self.redo_edit),
            ('copy_graph', 'Edit', 'Copy Graph as SVG', 'Ctrl+Alt+J', self._on_copy_graph_svg),
            ('rescale', 'View', 'Rescale Graph', 'Ctrl+R', self._on_rescale_graph),
            ('cancel', 'Edit', 'Cancel Graph Selection / Drag', 'Esc', self._cancel_graph_interaction)):
            self._graph_command_actions[key] = action(key, text, callback, group, shortcut)
        self._graph_command_actions['import'].setToolTip('Automatically import full tables: first column X, all remaining columns Y.')
        self._graph_command_actions['import_options'].setToolTip('Preview detected types, choose X/Y columns, group Workbooks or compare.')
        self._graph_command_actions['redo'].setShortcuts([QKeySequence('Ctrl+Y'), QKeySequence('Ctrl+Shift+Z')])
        self.source_navigation.availabilityChanged.connect(self._refresh_source_import_commands)
        self._refresh_graph_commands()

    def _build_quick_commands(self):
        actions = [(key, self._graph_command_actions[key]) for key in ('import', 'open_project', 'save_project', 'undo', 'redo')]
        actions.extend((('analyze', self.analyze_action), ('back', self.back_to_workspace_action), ('project', self.project_panel_action), ('properties', self.dat_details_action)))
        self.app_bridge.commands.bind(actions)

    def _refresh_source_import_commands(self):
        if hasattr(self, '_graph_command_actions'):
            for key in ('import', 'import_options'):
                self._graph_command_actions[key].setEnabled(self.source_navigation.importEnabled)

    def _refresh_graph_commands(self):
        self._refresh_paste_commands()
        self.processing.syncFromOwner()
        self._refresh_analysis_context()
        self._refresh_navigator_actions()
        self._refresh_workspace_shell()
        for key in self.graph_documents.keys():
            page = self.graph_documents.document_for(key)
            if isinstance(page, XYPlotController):
                page.set_add_data_enabled(not self._project_busy() and bool(self.workspace.sheets))
                page.add_files_action.setEnabled(not self._project_busy())
                page.refresh_input_state()
                page.analyze_action.setEnabled(not self._project_busy())
                page.analysis_controller.bridge.refresh()
        self._refresh_graph_lifecycle_controls()
        self._refresh_graph_command_host()
        self._refresh_navigator_active()
        for key in self.graph_documents.keys():
            if document_exists(self.workspace, key):
                self.graph_documents.update_title(key, self._document_title(key))
        if not hasattr(self, '_graph_command_actions'):
            return
        ready = self.current_cube is not None
        idle = not self._project_busy()
        self.analyze_action.setEnabled(idle and self._active_analysis_source() is not None)
        self.back_to_workspace_action.setEnabled(idle and self._outer_view == 'analysis')
        self.analysis_header.refresh()
        self.ordinary_analysis.refresh()
        for key in self.graph_documents.keys():
            page = self.graph_documents.document_for(key)
            if isinstance(page, FormalPlotController):
                page.set_command_availability('analyze', idle and self._resolve_analysis_source(('heatmap', key)) is not None)
        self.open_graph_action.setEnabled(ready and self.workspace.active is not None and idle)
        self.close_graph_view_action.setEnabled(
            idle and self._outer_view == 'workspace'
            and self.graph_documents.registry.active_key in self.graph_documents.registry.open_keys())
        visible = bool(self.graph_documents.registry.open_keys())
        self.tabbed_graphs_action.setEnabled(visible)
        self.tile_graphs_action.setEnabled(visible)
        self.free_graphs_action.setEnabled(visible)
        self.cascade_graphs_action.setEnabled(visible)
        tabbed = self.graph_documents.mode == 'tabbed'
        self.tabbed_graphs_action.setChecked(tabbed)
        self.free_graphs_action.setChecked(not tabbed)
        for key in ('new_project', 'open_project', 'save_project', 'save_project_as'):
            self._graph_command_actions[key].setEnabled(not self._project_busy())
        self._refresh_source_import_commands()
        auxiliary = self._auxiliary_active()
        xy = auxiliary and self.graph_documents.registry.active_key[0] == 'xy'
        heatmap_visible = (self._outer_view != 'workspace' or
                           self.graph_documents.registry.active_key == self._document_key() and
                           self._document_key() in self.graph_documents.registry.open_keys())
        self._graph_command_actions['copy_graph'].setEnabled(idle and (xy or (heatmap_visible and not auxiliary and ready and self.plot_page.presentation_error is None)))
        self._graph_command_actions['rescale'].setEnabled(idle and (xy or (not auxiliary and ready)))
        self._graph_command_actions['cancel'].setEnabled(not auxiliary)
        session = self.workspace.active
        if xy:
            page = self.graph_documents.document_for(self.graph_documents.registry.active_key)
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
            page = self.graph_documents.document_for(self.graph_documents.registry.active_key)
            if isinstance(page, XYPlotController):
                page.render()
            return
        if self.current_cube is not None:
            self._on_auto_view()

    def _cancel_graph_interaction(self):
        if self._auxiliary_active():
            return
        self.plot_page.cancel_interaction()
        if self._outer_view != 'workspace':
            if self._motion_cid is not None:
                self.canvas.mpl_disconnect(self._motion_cid)
                self._motion_cid = None
            self._drag_peak_arr_idx = None
            self._drag_current_x = None
            self._drag_scatter = None
            self._on_lasso_mode_toggled(False)
            self._on_toggle_circle_delete_mode(False)
            self._batch_delete_circle_start = None
            self._lasso_selected_peak_ids = []
            self._clear_lasso_selection_visual()
            self._update_lasso_actions()
            self._update_spectrum_only()

    def _show_plot_property(self, kind, identifier=''):
        page = self.plot_page.rendered_page
        if self._outer_view != 'workspace' or page is None or page.owner_token != self._plot_owner_token():
            return
        field = {'title': 'plot_title', 'signal-label': 'signal_title'}.get(kind)
        if kind in ('horizontal-label', 'vertical-label', 'horizontal-range', 'vertical-range'):
            source_y = kind.startswith('horizontal') == self.plot_page.presentation.axis.exchange_xy
            field = ('ymin' if source_y else 'xmin') if kind.endswith('-range') else ('y_axis_name' if source_y else 'axis_override')
        self._set_dat_details_requested(True)
        self.request_task(2)
        if field is None:
            self.plot_page.reveal_property(kind, identifier)
        else:
            self.dat_properties.reveal(field)

    def _plot_owner_token(self, key=None):
        dataset_id, graph_id = key or self._document_key()
        science = self._plot_revisions.setdefault(dataset_id, [0, 0, 0])[0]
        view, presentation = self._graph_revisions.setdefault((dataset_id, graph_id), [0, 0])
        return (dataset_id, self._plot_lifetime, science, view, presentation, graph_id)

    def _invalidate_graph_document(self, key, *, kind='view', preserve_input=False):
        revisions = self._graph_revisions.setdefault(key, [0, 0])
        revisions[1 if kind == 'presentation' else 0] += 1
        page = self.graph_documents.document_for(key)
        if page is None and key == self._document_key():
            page = self.plot_page
        if page is not None:
            page.invalidate(self._plot_owner_token(key), preserve_input=preserve_input)

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
        if self._workbench_document() is not None and self._outer_view == 'analysis':
            self._plot_render_timer.start(180 if delay is None else delay)
            return
        if (self._outer_view != 'workspace'
                or self.current_cube is None or self._annotation_edit_pending
                or (getattr(self, '_plot_batch', None) and not self._plot_batch.done)):
            return
        self._plot_render_timer.start(180 if delay is None else delay)

    def _outer_view_changed(self, *, restore_graph=True, defer_live_layout=False):
        self._refresh_graph_command_host()
        self._refresh_workspace_shell()
        if hasattr(self, '_plot_render_timer'):
            self._plot_render_timer.stop()
        if self._outer_view == 'analysis' and getattr(self, '_annotation_layout_pending', False):
            if defer_live_layout and self._workbench_document() is None:
                # Quick's command/header geometry settles after view entry.
                # Measure only its final viewport with the existing burst timer.
                self._schedule_view_layout()
            else:
                self._layout_live_plots()
                self.canvas.draw_idle()
        if hasattr(self, '_plot_render_timer') and self.plot_page.rendered_page is None:
            self._schedule_plot_page_render()
        if hasattr(self, 'analysis_workbench'):
            self._workbench_set_visible()
        self.graph_documents.refresh_eligibility()
        self._refresh_graph_commands()

    def _render_visible_plot_page(self):
        if self._workbench_document() is not None and self._outer_view == 'analysis':
            self._workbench_render()
            return
        if self._outer_view == 'workspace':
            for key in self.graph_documents.visible_keys():
                page = self.graph_documents.document_for(key)
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
        self._invalidate_plot_page(kind='presentation', schedule=False)
        if self._outer_view == 'workspace':
            self._annotation_layout_pending = True
            if self._heat_ax is not None:
                try:
                    validate_heatmap_axis_ticks(self._heat_ax, presentation)
                except ValueError as exc:
                    self.plot_page.set_status_message(f'Cannot prepare page: {exc}')
        else:
            self._layout_live_plots()
            self.canvas.draw_idle()
        if self._outer_view != 'workspace' and getattr(self, '_live_heatmap_style_error', None):
            self.plot_page.set_status_message(f'Cannot prepare page: {self._live_heatmap_style_error}')
        if before is not None:
            label = 'Plot presentation'
            if previous.annotations != presentation.annotations:
                label = ('Add annotation' if len(presentation.annotations) > len(previous.annotations)
                         else 'Delete annotation' if len(presentation.annotations) < len(previous.annotations)
                         else 'Edit annotation')
            elif previous.title_position != presentation.title_position:
                label = 'Move title'
            self._record_history(session, label, before, capture_fields(session, ('presentation',)))
        # Draft text has its own commit boundary. Coalesce accepted edits in
        # this event turn without adding typing debounce to a completed edit.
        self._schedule_plot_page_render(delay=0)

    def _on_plot_presentation_error(self, message):
        self._invalidate_plot_page(kind='presentation', schedule=False)
        self.plot_page.set_status_message(f'Cannot prepare page: {message}')

    def snapshot_plot_dataset(self, session=None):
        """Batch-safe snapshot hook. Inactive sessions never project into widgets."""
        session = session or self.workspace.active or self._unregistered_session
        owner = self._plot_owner_token((session.dataset_id, session.active_graph_id))
        return snapshot_dataset(session, activation_epoch=owner[1],
                                science_revision=owner[2], view_revision=owner[3],
                                presentation_revision=owner[4])

    def _formal_page_matches_view(self, target, session):
        """Check the view fields that capture can sync without a revision event."""
        if target is None or target.presentation_error is not None or target.has_pending_project_edits():
            return False
        page, snapshot = target.rendered_page, target.preview.snapshot
        owner = self._plot_owner_token((session.dataset_id, session.active_graph_id))
        return (page is not None and page.owner_token == target.expected_owner == owner
                and snapshot is not None and snapshot.owner_token == owner
                and target.presentation == session.presentation
                and snapshot.x_limits == session.view.x_limits
                and snapshot.y_limits == session.view.y_limits
                and snapshot.color_limits == session.view.color_limits)

    @staticmethod
    def _refresh_formal_colorbar_norms(target, snapshot):
        # Properties samples follow the owned formal data even while Analysis
        # styling/layout is deferred, including cached inactive graph pages.
        limits = ({region.id: region.color_limits for region in snapshot.regions}
                  if snapshot.regions else {'global': snapshot.color_limits})
        target.set_colorbar_norms({key: build_color_norm(snapshot.norm, pair)
                                  for key, pair in limits.items()})

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
        target = self.plot_page if active else self.graph_documents.document_for(key)
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
            target.invalidate(owner, 'Finish or correct the current page editor before preparing the page.',
                              preserve_input=owner == target.expected_owner)
            return
        if active and self._heat_ax is not None:
            try:
                validate_heatmap_axis_ticks(self._heat_ax, session.presentation)
            except ValueError as exc:
                target.invalidate(owner, f'Cannot prepare page: {exc}')
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
            self._refresh_formal_colorbar_norms(target, snapshot)
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
                if self._outer_view == 'workspace':
                    self._annotation_layout_pending = layout_pending
                else:
                    if layout_pending:
                        self._layout_live_plots()
                    self.canvas.draw_idle()
        except Exception as exc:
            if owner == self._plot_owner_token(key):
                target.invalidate(owner, f'Cannot prepare page: {exc}')

    def _adopt_prepared_page_size(self, session, key, presentation):
        """Commit measured paper size to the graph that supplied the snapshot."""
        previous = session.graphs[key[1]].presentation
        target = self.graph_documents.document_for(key)
        if target is None and key == self._document_key():
            target = self.plot_page
        # Measured paper growth preserves every other presentation choice.
        preserve_input = (target is not None and target.presentation == previous
                          and replace(presentation, width_in=previous.width_in,
                                      height_in=previous.height_in) == previous)
        session.graphs[key[1]].presentation = presentation
        if target is not None:
            target.set_presentation(presentation)
        self._invalidate_graph_document(key, kind='presentation', preserve_input=preserve_input)
        self._schedule_project_modified()

    def export_plot_page(self, extension):
        """Flush annotation drafts, then write validated current formal bytes."""
        if self._workbench_document() is not None and not self._auxiliary_active():
            self._workbench_finish_editors()
            self._workbench_render()
        if self._auxiliary_active():
            page = self.graph_documents.document_for(self.graph_documents.registry.active_key)
            if isinstance(page, XYPlotController):
                page._export_dialog(extension)
                return
            raise ValueError('Choose a comparison graph to export; a workbook has no graph image.')
        if self._outer_view == 'workspace' and self.graph_documents.registry.active_key is None:
            raise ValueError('Open a graph document to export.')
        # FocusOut can finish a title draft before its queued page preparation.
        # Page readiness is independent of whether live geometry needs layout.
        if self._annotation_edit_pending or self.plot_page.rendered_page is None:
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
        self.publish_status(notice or '')
        return saved

    def _on_export_plot_page(self, extension):
        try:
            self.export_plot_page(extension)
        except Exception as exc:
            self._show_error(f'Failed to export {extension.upper()}: {exc}')

    def copy_graph_svg(self):
        """Publish only the final hybrid SVG after fresh validation succeeds."""
        if self._auxiliary_active():
            page = self.graph_documents.document_for(self.graph_documents.registry.active_key)
            if not isinstance(page, XYPlotController):
                raise ValueError('Select table cells and use Ctrl+C, or choose a comparison graph.')
            mime = QMimeData()
            mime.setData('image/svg+xml', page.svg_bytes())
            QGuiApplication.clipboard().setMimeData(mime)
            return page
        if self._outer_view == 'workspace' and self.graph_documents.registry.active_key is None:
            raise ValueError('Open a graph document to copy.')
        _session, _snapshot, page = self._capture_formal_source()
        mime = QMimeData()
        mime.setData('image/svg+xml', page.svg_bytes)
        QGuiApplication.clipboard().setMimeData(mime)
        self.publish_status('')
        return page

    def _on_copy_graph_svg(self):
        try:
            self.copy_graph_svg()
        except Exception as exc:
            self._show_error(f'Cannot copy graph as SVG: {exc}')

    def copy_graph_png(self):
        """Publish PNG bytes and a native image only after final-page encoding succeeds."""
        if self._auxiliary_active():
            raise ValueError('Open a DAT graph document to copy as PNG.')
        if self._outer_view == 'workspace' and self.graph_documents.registry.active_key is None:
            raise ValueError('Open a graph document to copy.')
        _session, _snapshot, page = self._capture_formal_source()
        image = render_svg_image(page)
        dots_per_meter = round(page.heatmap_dpi / .0254)
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        if not buffer.open(QIODevice.WriteOnly):
            raise ValueError('Could not prepare the PNG clipboard buffer.')
        try:
            if not image.save(buffer, 'PNG') or encoded.isEmpty():
                raise ValueError('Could not encode the graph as PNG.')
        finally:
            buffer.close()
        mime = QMimeData()
        mime.setData('image/png', encoded)
        mime.setImageData(image)
        QGuiApplication.clipboard().setMimeData(mime)
        self.publish_status('')
        return page

    def _on_copy_graph_png(self):
        try:
            self.copy_graph_png()
        except Exception as exc:
            self._show_error(f'Cannot copy graph as PNG: {exc}')

    def _refresh_plot_formats(self, selected=None):
        try:
            self.plot_page.set_format_names(self._plot_format_store.list_names(), selected)
        except (ValueError, OSError, TypeError) as exc:
            self.plot_page.set_format_names(())
            self.plot_page.set_status_message(f'Cannot read named formats: {exc}')

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
            self.plot_page.set_command_availability('comparison', True,
                self.plot_page.command_tooltip('comparison') + f'\nSource: {session.source_path} [ID: {session.dataset_id}]')
        if hasattr(self, '_settings_copy_action'):
            self._graph_copy_svg_action.setEnabled(available)
            self._graph_copy_png_action.setEnabled(available)
            self._settings_copy_action.setEnabled(available)
            self._settings_copy_to_action.setEnabled(available)
            self._comparison_action.setEnabled(available)
            self._comparison_action.setToolTip(self.plot_page.command_tooltip('comparison'))

    def _capture_formal_source(self):
        """Finalize editors and validate fresh formal bytes before freezing a source."""
        if self._workbench_document() is not None:
            self._require_project_idle()
            self._workbench_finish_editors()
            self._workbench_render()
            page = self.plot_page.rendered_page
            if page is None or page.owner_token != self._plot_owner_token():
                raise ValueError(self.plot_page.status_text)
            return self.workspace.active, self.plot_page.preview.snapshot, page
        session = self.workspace.active
        if session is None or session.current_cube is None:
            raise ValueError('Load a DAT before capturing its plot settings.')
        if self._background_jobs.busy or (self._plot_batch is not None and not self._plot_batch.done):
            raise ValueError('Wait for the current operation before capturing this page.')
        if self.plot_page.presentation_error is not None:
            raise ValueError(f'Correct the current page editor first: {self.plot_page.presentation_error}')
        self._finish_graph_editors(capture_science=False)
        self._finish_annotation_edit()
        self._finish_history_edit()
        self._sync_formal_plot_view()
        # Toolbar limits may reach the captured view before their invalidation
        # callback. A matching revision alone cannot establish freshness here.
        if not self._formal_page_matches_view(self.plot_page, session):
            self._render_plot_page()
        elif self._heat_ax is not None:
            validate_heatmap_axis_ticks(self._heat_ax, session.presentation)
        page = self.plot_page.rendered_page
        if page is None or page.owner_token != self._plot_owner_token():
            raise ValueError(self.plot_page.status_text)
        return session, self.plot_page.preview.snapshot, page

    def copy_plot_settings(self):
        session, snapshot, page = self._capture_formal_source()
        template = capture_format(session, 'Copied settings', source_snapshot=snapshot, source_page=page)
        # Replace the clipboard only after every capture/validation step succeeds.
        self._plot_settings_clipboard = template
        self._plot_settings_source_label = f'{session.source_path} — {session.active_graph.name} [ID: {session.dataset_id}]'
        self._refresh_transfer_controls()
        self.publish_status('')
        return template

    def _paste_options(self):
        try:
            payload = json.loads(self._annotation_settings.value(PASTE_OPTIONS_KEY, ''))
            names = {item.name for item in fields(FormatApplicationOptions)}
            if not isinstance(payload, dict) or set(payload) != names:
                raise ValueError('Invalid saved paste options.')
            return FormatApplicationOptions(**payload)
        except (ValueError, TypeError):
            return FormatApplicationOptions()

    def _has_paste_options(self):
        value = self._annotation_settings.value(PASTE_OPTIONS_KEY)
        if value is None:
            return False
        try:
            payload = json.loads(value)
            return (isinstance(payload, dict)
                    and set(payload) == {item.name for item in fields(FormatApplicationOptions)}
                    and all(type(value) is bool for value in payload.values()))
        except (TypeError, ValueError):
            return False

    def _save_paste_options(self, options):
        self._annotation_settings.setValue(PASTE_OPTIONS_KEY, json.dumps(
            {item.name: getattr(options, item.name) for item in fields(options)}))

    def _paste_graph_targets(self):
        return [(session.dataset_id, graph.graph_id) for session in self.workspace.datasets.values()
                if session.current_cube is not None for graph in session.graphs.values()
                if graph.published and independently_formatted(graph)]

    def create_paste_settings_dialog(self, *, current_only=False):
        if self._plot_settings_clipboard is None:
            raise ValueError('Copy settings from a valid source page first.')
        if self.workspace.active is None:
            raise ValueError('Choose an imported target dataset first.')
        self.capture_active_dataset()
        targets = [self._document_key()] if current_only else self._paste_graph_targets()
        sessions = (self.workspace.active,) if current_only else self.workspace.datasets.values()
        return ApplyFormatDialog(sessions, self.workspace.active_dataset_id,
            self._plot_settings_clipboard, self.root_window, source_label=self._plot_settings_source_label,
            initial_options=self._paste_options(), graph_targets=None if current_only else targets,
            selected_targets=[self._document_key()])

    def _paste_history_snapshot(self, sessions):
        return {session.dataset_id: (self._history_for(session),
            tuple(self._history_for(session).undo_stack), tuple(self._history_for(session).redo_stack))
            for session in sessions}

    def _capture_paste_ownership(self, operation):
        operation.states = {}
        for key in operation.baselines:
            session = self.workspace.datasets[key[0]]
            graph = session.graphs[key[1]]
            operation.states[key] = (session, graph, deepcopy(graph.view), graph.presentation,
                self._plot_owner_token(key), session.current_cube, session.base_cube,
                graph.source_mode, deepcopy(graph.gate_indices), graph.result_id, graph.published,
                None if graph.analysis is None else
                (id(graph.analysis), graph.analysis.job_token, id(graph.analysis.science.current_cube)))
        operation.expected_histories = self._paste_history_snapshot(
            {state[0].dataset_id: state[0] for state in operation.states.values()}.values())

    def _validate_latest_paste(self):
        operation = self._latest_paste
        if operation is None:
            raise ValueError('There is no latest paste to revise or undo.')
        if (self._history_busy() or self._pending_history_edit is not None
                or any(bridge.has_project_drafts() for bridge in
                       (self.dat_properties, self.analysis_header, self.ordinary_analysis, self.processing))):
            raise ValueError('Finish the current operation or pending edit before changing the latest paste.')
        for key, state in operation.states.items():
            session, graph, view, presentation, token, cube, base, mode, gates, result, published, analysis = state
            if (self.workspace.datasets.get(key[0]) is not session
                    or session.graphs.get(key[1]) is not graph or graph.published != published):
                raise ValueError('A pasted target was deleted or replaced.')
            if (graph.view != view or graph.presentation != presentation
                    or self._plot_owner_token(key) != token
                    or session.current_cube is not cube or session.base_cube is not base
                    or graph.source_mode != mode or graph.gate_indices != gates or graph.result_id != result
                    or (None if graph.analysis is None else
                        (id(graph.analysis), graph.analysis.job_token, id(graph.analysis.science.current_cube))) != analysis):
                raise ValueError('A pasted graph or its data changed after the latest paste.')
            page = self.graph_documents.document_for(key)
            if page is None and key == self._document_key():
                page = self.plot_page
            if page is not None and (page.presentation_error is not None or page.has_pending_project_edits()):
                raise ValueError('Finish the pending pasted graph editor before changing the latest paste.')
            if key == self._document_key() and self._heat_ax is not None:
                # Toolbar pan/zoom can precede its revision callback.
                recipe = graph.view.plot_recipe(graph_cube(session, graph), session.applied, graph.presentation)
                if (tuple(self._heat_ax.get_xlim()) != tuple(recipe['x_limits'])
                        or tuple(self._heat_ax.get_ylim()) != tuple(recipe['y_limits'])):
                    raise ValueError('The pasted graph view changed after the latest paste.')
        for dataset_id, (history, undo, redo) in operation.expected_histories.items():
            current = self._history_for(self.workspace.datasets[dataset_id])
            if (current is not history or len(current.undo_stack) != len(undo)
                    or len(current.redo_stack) != len(redo)
                    or any(a is not b for a, b in zip(current.undo_stack, undo))
                    or any(a is not b for a, b in zip(current.redo_stack, redo))):
                raise ValueError('Edit history changed after the latest paste.')
        return operation

    def paste_plot_settings(self, dataset_ids=None, *, options=None, annotation_pairs=None, graph_targets=None):
        if self._plot_settings_clipboard is None:
            raise ValueError('Copy settings from a valid source page first.')
        self._finish_graph_editors(capture_science=False)
        self._finish_history_edit()
        legacy_targets = graph_targets is None
        if graph_targets is None:
            ids = [self.workspace.active_dataset_id] if dataset_ids is None else dataset_ids
            graph_targets = [(identifier, self.workspace.datasets[identifier].active_graph_id
                              if identifier in self.workspace.datasets else None) for identifier in ids]
        targets = list(dict.fromkeys(tuple(key) for key in graph_targets))
        baselines = {}
        for key in targets:
            session = self.workspace.datasets.get(key[0])
            if session is None or key[1] not in session.graphs:
                continue
            try:
                baselines[key] = graph_candidate(session, key[1])
            except (KeyError, ValueError, TypeError):
                # The application path reports this target's preparation error
                # alongside successes instead of aborting the entire batch.
                continue
        histories = self._paste_history_snapshot(
            {key[0]: self.workspace.datasets[key[0]] for key in baselines}.values())
        chosen = options if options is not None else self._paste_options()
        normalized_pairs = {key: deepcopy((annotation_pairs or {}).get(key,
            (annotation_pairs or {}).get(key[0], {}))) for key in baselines}
        operation = PasteOperation(deepcopy(self._plot_settings_clipboard), baselines, chosen,
                                   normalized_pairs, histories)
        outcomes = self._apply_plot_template(operation.source, (),
            options=effective_paste_options(operation.source, chosen),
            annotation_pairs=operation.pairs, graph_targets=targets, _allow_unpublished=legacy_targets)
        applied = {(item['dataset_id'], item['graph_id']) for item in outcomes if item.get('applied')}
        operation.baselines = {key: value for key, value in baselines.items() if key in applied}
        operation.histories = {key: value for key, value in histories.items()
                               if any(target[0] == key for target in applied)}
        if operation.baselines:
            self._latest_paste = operation
            self._capture_paste_ownership(operation)
        self._refresh_paste_commands()
        return outcomes

    def create_adjust_paste_dialog(self):
        operation = self._validate_latest_paste()
        targets = list(operation.baselines)
        return ApplyFormatDialog(self.workspace.datasets.values(), self.workspace.active_dataset_id,
            operation.source, self.root_window, initial_options=operation.options,
            graph_targets=targets, selected_targets=targets, baseline_sessions=operation.baselines,
            adjustment=True, initial_annotation_pairs=operation.pairs)

    def adjust_last_paste(self, options, annotation_pairs=None):
        operation = self._validate_latest_paste()
        pairs = operation.pairs if annotation_pairs is None else annotation_pairs
        normalized_pairs = {key: deepcopy(pairs.get(key, pairs.get(key[0], {}))) for key in operation.baselines}
        outcomes = self._apply_plot_template(operation.source, (),
            options=effective_paste_options(operation.source, options),
            annotation_pairs=normalized_pairs, graph_targets=list(operation.baselines),
            _baselines=operation.baselines, _history_prefixes=operation.histories, _status_action='revised')
        if all(item.get('applied') for item in outcomes):
            operation.options, operation.pairs = options, normalized_pairs
            self._capture_paste_ownership(operation)
            self._save_paste_options(options)
        self._refresh_paste_commands()
        return outcomes

    def undo_last_paste(self):
        operation = self._validate_latest_paste()
        # The staging path validates/renders every baseline before touching any target.
        outcomes = self._apply_plot_template(None, (), graph_targets=list(operation.baselines),
            _baselines=operation.baselines, _history_prefixes=operation.histories, _status_action='restored')
        if not all(item.get('applied') for item in outcomes):
            self._report_format_outcomes(outcomes)
            return False
        self._latest_paste = None
        self._refresh_paste_commands()
        self._report_format_outcomes(outcomes)
        return all(item['success'] for item in outcomes)

    def _refresh_paste_commands(self):
        if not hasattr(self, 'plot_page'):
            return
        available = (self.workspace.active is not None and self.workspace.active.current_cube is not None
                     and self.plot_page.presentation_error is None and not self._auxiliary_active()
                     and not self._history_busy())
        copied = self._plot_settings_clipboard is not None
        independent = self.workspace.active is not None and independently_formatted(self.workspace.active.active_graph)
        current_available = available and independent
        try:
            self._validate_latest_paste()
            revisable, reason = True, ''
        except ValueError as exc:
            revisable, reason = False, str(exc)
        for key, enabled in (('settings_paste', current_available and copied),
                ('settings_paste_to', available and copied), ('settings_paste_options', current_available and copied),
                ('settings_adjust', revisable), ('settings_undo_paste', revisable)):
            tooltip = reason if key in ('settings_adjust', 'settings_undo_paste') else (
                'This Analysis Graph inherits its source format; select the source graph.'
                if key in ('settings_paste', 'settings_paste_options') and not independent else None)
            self.plot_page.set_command_availability(key, enabled, tooltip)
            action = getattr(self, '_' + key + '_action', None)
            if action is not None:
                action.setEnabled(enabled)
                action.setToolTip(self.plot_page.command_tooltip(key))

    def open_plot_comparison(self):
        session, snapshot, page = self._capture_formal_source()
        label = f'{session.source_path} — {session.active_graph.name} [ID: {session.dataset_id}]'
        window = PlotComparisonWindow(page, source_label=label,
            metadata=self._plot_dataset_metadata(session, snapshot), png_dpi=session.presentation.heatmap_dpi,
            output_folder=self._output_folder(session), parent=self.root_window)
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
        if (not self._has_paste_options() or self._paste_options().annotation_positions
                and getattr(self._plot_settings_clipboard, 'annotation_layout', ())):
            self._show_paste_settings_dialog(current_only=True)
            return
        try:
            self._report_format_outcomes(self.paste_plot_settings())
        except Exception as exc:
            self._show_error(f'Cannot paste settings: {exc}')

    def _on_paste_settings_to_graphs(self):
        self._show_paste_settings_dialog(current_only=False)

    def _on_paste_settings_options(self):
        self._show_paste_settings_dialog(current_only=True)

    def _on_adjust_last_paste(self):
        dialog = None
        try:
            dialog = self.create_adjust_paste_dialog()
            if dialog.exec() == 1:
                outcomes = self.adjust_last_paste(dialog.options(), dialog.annotation_pairs())
                if outcomes and all(item.get('applied') for item in outcomes):
                    self._latest_paste.options = dialog.preference_options()
                    self._save_paste_options(dialog.preference_options())
                self._report_format_outcomes(outcomes)
        except Exception as exc:
            self._show_error(f'Cannot adjust latest paste: {exc}')
        finally:
            if dialog is not None:
                dialog.deleteLater()

    def _on_undo_last_paste(self):
        try:
            self.undo_last_paste()
        except Exception as exc:
            self._show_error(f'Cannot undo latest paste: {exc}')

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
            if dialog.exec() == 1:
                outcomes = self.paste_plot_settings(
                    dialog.selected_ids() if current_only else None,
                    graph_targets=None if current_only else dialog.selected_graph_targets(),
                    options=dialog.options(), annotation_pairs=dialog.annotation_pairs())
                if any(item.get('applied') for item in outcomes):
                    self._latest_paste.options = dialog.preference_options()
                    self._save_paste_options(dialog.preference_options())
                self._report_format_outcomes(outcomes)
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
                                 self._plot_format_store.load(name), self.root_window)

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
                             options=None, annotation_pairs=None, graph_targets=None,
                             _baselines=None, _history_prefixes=None, _allow_unpublished=False, _status_action='applied'):
        self._finish_history_edit()
        exact = graph_targets is not None
        targets = list(dict.fromkeys(tuple(key) for key in graph_targets)) if exact else [
            (identifier, self.workspace.datasets[identifier].active_graph_id
             if identifier in self.workspace.datasets else None) for identifier in dataset_ids]
        outcomes, staged = [], []
        for key in targets:
            dataset_id, graph_id = key
            session = self.workspace.datasets.get(dataset_id)
            graph = session.graphs.get(graph_id) if session else None
            label = f'{session.source_path} — {graph.name} [ID: {dataset_id}/{graph_id}]' if graph else str(key)
            notice = ''
            try:
                if session is None or session.current_cube is None or graph is None:
                    raise ValueError('The imported graph is unavailable.')
                if exact and not independently_formatted(graph):
                    raise ValueError('This Analysis Graph inherits its format; paste to its source graph instead.')
                if exact and not _allow_unpublished and _baselines is None and not graph.published:
                    raise ValueError('Only published heatmap graphs support Paste Format.')
                current = graph_candidate(session, graph_id)
                before = capture_fields(current, STYLE_FIELDS + ('presentation',))
                candidate = graph_candidate((_baselines or {}).get(key, session), graph_id)
                baseline = capture_fields(candidate, STYLE_FIELDS + ('presentation',))
                pairs = (annotation_pairs or {}).get(key, (annotation_pairs or {}).get(dataset_id, {}))
                effective = options
                if options is not None and options.annotation_positions and not pairs:
                    effective = replace(options, annotation_positions=False)
                    notice = 'Annotation positions skipped: no explicit usable pairs selected.'
                result = (apply_format(candidate, template, copy_mapping=copy_mapping,
                    copy_color_limits=copy_color_limits, options=effective, annotation_pairs=pairs)
                    if template is not None else None)
                target = self.graph_documents.document_for(key)
                if target is None and key == self._document_key():
                    target = self.plot_page
                unchanged = not field_changes(before, capture_fields(candidate, STYLE_FIELDS + ('presentation',)))
                snapshot = rendered = None
                if not (unchanged and self._formal_page_matches_view(target, candidate)):
                    snapshot = self.snapshot_plot_dataset(candidate)
                    candidate.presentation, rendered = prepare_heatmap_page(
                        snapshot, candidate.presentation, renderer=render_hybrid_svg)
                staged.append((key, session, candidate, before, baseline, target, snapshot, rendered))
                outcomes.append({'dataset_id': dataset_id, 'graph_id': graph_id, 'source': label,
                    'success': True, 'applied': False,
                    'changed_fields': result.changed_fields if result is not None else (),
                    'notice': notice, 'error': ''})
            except Exception as exc:
                outcomes.append({'dataset_id': dataset_id, 'graph_id': graph_id, 'source': label,
                                 'success': False, 'applied': False, 'error': str(exc)})
        if _history_prefixes is not None and any(not item['success'] for item in outcomes):
            for item in outcomes:
                if item['success']:
                    item.update(success=False, error='Batch unchanged: another target failed validation.')
            return outcomes
        # Adjust/Undo owns each dataset's whole suffix, including multiple graphs.
        if _history_prefixes is not None:
            for history, undo, redo in _history_prefixes.values():
                history.undo_stack[:] = undo
                history.redo_stack[:] = redo
        active_changed = False
        prepared = {}
        by_key = {(item['dataset_id'], item['graph_id']): item for item in outcomes}
        for key, session, candidate, before, baseline, target, snapshot, rendered in staged:
            graph = session.graphs[key[1]]
            graph.view, graph.presentation = candidate.view, candidate.presentation
            after = capture_fields(candidate, STYLE_FIELDS + ('presentation',))
            if template is not None:
                history_before = baseline if _baselines is not None else before
                if field_changes(history_before, after):
                    self._record_history(session, 'Paste plot format', history_before, after, graph_id=key[1])
            if field_changes(before, after):
                self._invalidate_graph_document(key, kind='presentation')
                if target is not None:
                    target.set_presentation(candidate.presentation)
                active_changed |= key == self._document_key()
            if rendered is not None:
                prepared[key] = (session, snapshot, candidate.presentation, rendered)
            by_key[key]['applied'] = True
        if active_changed:
            self._project_plot_dataset(self.workspace.active)
        for key, (session, snapshot, presentation, rendered) in prepared.items():
            if key != self._document_key():
                continue
            target = self.graph_documents.document_for(key)
            if target is None:
                target = self.plot_page
            if (target is None or session.graphs[key[1]].presentation != presentation
                    or target.presentation_error is not None or target.has_pending_project_edits()):
                continue
            owner = self._plot_owner_token(key)
            if target.expected_owner != owner:
                target.invalidate(owner)
            snapshot = replace(snapshot, activation_epoch=owner[1], science_revision=owner[2],
                               view_revision=owner[3], presentation_revision=owner[4])
            target.preview.set_geometry(snapshot, presentation)
            self._refresh_formal_colorbar_norms(target, snapshot)
            target.commit_rendered_page(replace(rendered, owner_token=owner))
        # Save after every graph in a dataset has committed; a failed save is
        # still an applied in-memory target owned by the latest operation.
        for dataset_id in dict.fromkeys(key[0] for key, *_ in staged):
            try:
                self._save_plot_dataset(self.workspace.datasets[dataset_id])
            except (ValueError, OSError, TypeError) as exc:
                for item in outcomes:
                    if item['dataset_id'] == dataset_id and item['applied']:
                        item.update(success=False, error=f'Applied; sidecar save failed: {exc}')
        if self.plot_page.rendered_page is not None:
            self._plot_render_timer.stop()
        if any(self.graph_documents.document_for(key).rendered_page is None
               for key in self.graph_documents.visible_keys() if len(key) == 2):
            self._schedule_plot_page_render()
        self._refresh_graph_commands()
        failures = sum(not item['success'] for item in outcomes)
        self.publish_status(f'Format {_status_action}: {len(outcomes)-failures}/{len(outcomes)} graphs; '
            f'{failures} failed. ' + '; '.join(item['source'] + (': ' + item['error'] if item['error'] else ': ' + _status_action)
                                           for item in outcomes))
        skipped = [item['source'] for item in outcomes if item.get('notice')]
        if skipped:
            self.plot_page.set_status_message('Annotation positions skipped (no explicit usable pairs): ' + '; '.join(skipped))
        return outcomes

    def _on_save_plot_format(self):
        name, accepted = prompt_text(self.root_window, 'Save named plot format', 'Format name:',
                                             text=self.plot_page.selected_format)
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
            if dialog.exec() == 1:
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
                target_page = self.graph_documents.document_for((dataset_id, graph_id))
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
        self.plot_page.set_batch_state(export_enabled=False)
        self._refresh_graph_commands()
        self.plot_page.set_batch_state(visible=True, maximum=len(entries), cancel_visible=True)
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
        target = self.graph_documents.document_for(key)
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
        self.plot_page.set_batch_state(value=summary.completed+summary.canceled)
        warning_count = len(self._plot_batch_save_notices)
        warning_status = f' · {warning_count} settings warning' + ('s' if warning_count != 1 else '') if warning_count else ''
        self.plot_page.set_batch_state(status=f'{summary.completed}/{summary.total} attempted · '
            f'{summary.failed} failed · {summary.canceled} canceled{warning_status}',
            tooltip=self.plot_batch_report, results_enabled=bool(self.plot_batch_report))

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
        self.plot_page.set_batch_state(export_enabled=True)
        self._refresh_graph_commands()
        self.plot_page.set_batch_state(cancel_visible=False)
        summary = self.plot_batch_summary
        self.publish_status(self.plot_page.formal.state['batch']['status']
            if summary.failed or summary.canceled or self._plot_batch_save_notices else '')
        if self.plot_page.rendered_page is None and self.current_cube is not None:
            self._schedule_plot_page_render()

    def _on_batch_export_plot(self):
        dialog = BatchExportDialog(self.workspace.datasets.values(), self.workspace.active_dataset_id, self.root_window)
        try:
            if self._project_path is not None:
                dialog.set_destination_label('Use one output folder (default: project folder for saved datasets)')
                dialog.set_destination_folder(str(self._output_folder()))
            if dialog.exec() == 1:
                self.start_plot_batch(graph_targets=dialog.selected_graph_targets(),
                    formats=dialog.formats(),common_folder=dialog.destination())
        except (ValueError, OSError, TypeError) as exc:
            self._show_error(f'Cannot start batch export: {exc}')
        finally:
            dialog.deleteLater()

    def create_plot_batch_results_dialog(self):
        return self._quick_host.show_batch_report(self.plot_batch_report)

    def _on_plot_batch_results(self):
        self.create_plot_batch_results_dialog()

    def _on_color_mapping_changed(self, *_args):
        with self._edit_transaction('Color mapping', ('view.norm',) + COLOR_FIELDS):
            session = self.workspace.active or self._unregistered_session
            session.view.norm = NormSpec(str(self._dat_view().norm.kind), self._dat_view().norm.linthresh)
            session.view.last_valid_region_color_limits = []
            self._invalidate_plot_page()
            if self.current_cube is None:
                return
            try:
                self._update_auto_color_limits_from_visible_view(use_spin_values=True)
                self._plot_cube()
            except ValueError as exc:
                self._on_plot_presentation_error(str(exc))
                self.publish_status(str(exc))

    def _current_norm_spec(self):
        return (self.workspace.active or self._unregistered_session).view.norm

    def _update_mapping_status(self):
        session = self.workspace.active or self._unregistered_session
        cube, view = graph_cube(session), session.view
        if view.norm.kind != 'log' or cube is None:
            self._dat_status('mapping_status', '')
            return
        xlo, xhi = sorted(view.x_limits)
        ylo, yhi = sorted(view.y_limits)
        rows = (cube.gate >= ylo) & (cube.gate <= yhi)
        columns = (cube.energy >= xlo) & (cube.energy <= xhi)
        values = cube.z[np.ix_(rows, columns)]
        count = int(np.count_nonzero(np.isfinite(values) & (values <= 0)))
        self._dat_status('mapping_status', f'Log: {count} visible nonpositive cells shown gray.')

    def _display_mapping_error(self, message):
        self._invalidate_plot_page(schedule=False)
        self.plot_page.set_status_message(f'Cannot prepare page: {message}')
        self.publish_status(message)

    def _show_error(self, msg: str) -> None:
        prompt_message(self.root_window, "Error", msg, (PromptAction.OK,), PromptAction.OK, kind="error")

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
            self.publish_status(f"Lasso init failed: {exc}")
            self._lasso_mode = False
            self._analysis_status()
            self._lasso_selector = None

    def _set_canvas_cursor_for_event(self, event: Any | None = None) -> None:
        if self.toolbar.mode:
            return
        if not self._lasso_mode:
            self.dat_canvas.set_cursor(Qt.ArrowCursor)
            return
        if event is not None and event.inaxes is self._heat_ax:
            self.dat_canvas.set_cursor(Qt.CrossCursor)
            return
        self.dat_canvas.set_cursor(Qt.ArrowCursor)

    def _cancel_canvas_gesture(self) -> None:
        """Retire view-local drags without applying edits to a replacement owner."""
        if getattr(self, '_motion_cid', None) is not None:
            self.canvas.mpl_disconnect(self._motion_cid)
            self._motion_cid = None
        marker = getattr(self, '_drag_scatter', None)
        self._drag_peak_arr_idx = self._drag_current_x = self._drag_scatter = None
        if marker is not None and marker.axes is not None:
            axes = marker.axes
            marker.remove()
            handles, labels = axes.get_legend_handles_labels()
            if handles:
                axes.legend(loc='upper right', fontsize=PLOT_FONT_SIZE)
            elif axes.get_legend() is not None:
                axes.get_legend().remove()
            self.canvas.draw_idle()
        self._batch_delete_circle_start = None
        selector = getattr(self, '_lasso_selector', None)
        if selector is not None:
            selector.clear()
            selector.verts = None
            selector._eventpress = selector._eventrelease = None

    def _clear_lasso_selection_visual(self) -> None:
        if self._lasso_selection_patch is not None:
            try:
                self._lasso_selection_patch.remove()
            except Exception:
                pass
            self._lasso_selection_patch = None

    def _update_lasso_actions(self) -> None:
        has_sel = bool(self._lasso_selected_peak_ids)
        self._analysis_status(deleteLassoEnabled=has_sel)
        self._analysis_status(clearLassoEnabled=has_sel)

    def _selected_x_axis_override(self) -> str | None:
        return self.dat_properties._axis_override

    def _refresh_axis_status(self) -> None:
        if self.current_cube is None:
            self._dat_status('axis_status', "X-axis: no file loaded.")
            self._analysis_status(axisText="Analysis axis: no file loaded.")
            return

        cube = self.current_cube
        override = self._selected_x_axis_override()
        override_txt = "Auto Detect" if override is None else dict(AXIS_OVERRIDE_ITEMS).get(override, str(override))
        ambiguity = "Ambiguous auto-detect. Manual override is recommended." if cube.x_is_ambiguous else "Auto-detect is confident."
        self._dat_status('axis_status', 
            f"Detected: {cube.x_original_label} via {cube.x_detection_source}. {cube.x_detection_note}\n"
            f"Conversion: {cube.photon_energy_note}\n"
            f"Override: {override_txt}. {ambiguity}"
        )
        self._analysis_status(axisText=
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
        a = float(self._analysis_controls().fit_xmin)
        b = float(self._analysis_controls().fit_xmax)
        return (a, b) if a <= b else (b, a)

    def _peak_search_bounds(self) -> tuple[float, float]:
        a = float(self._dat_number('xmin'))
        b = float(self._dat_number('xmax'))
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
            requested = int(self._analysis_controls().fit_smooth_window) if smooth_window is None else int(smooth_window)
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
        controls = self._analysis_controls()
        gaussian = controls.fit_model == 'Gaussian'
        parameter = 'sigma' if gaussian else 'HWHM gamma'
        model = 'Gaussian' if gaussian else 'Lorentzian'
        fwhm = 'FWHM ≈ 2.35482 × sigma' if gaussian else 'FWHM = 2 × gamma'
        row_name = 'sigma' if gaussian else 'HWHM'
        self._analysis_status(
            minWidthText=self._format_pts_conversion(controls.linewidth_min),
            maxWidthText=self._format_pts_conversion(controls.linewidth_max) if controls.linewidth_max else 'Auto',
            moveText=self._format_pts_conversion(controls.peak_move_limit),
            minWidthLabel=f'Min {row_name}:', maxWidthLabel=f'Max {row_name}:',
            widthHelp=f'{model} width is {parameter}; {fwhm}. Bounds use points, converted to X-axis units.')

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
        if not self._analysis_controls().derivative_detection:
            return None
        mask = self._peak_search_mask(x)
        xr = np.asarray(x[mask], dtype=float)
        yr = np.asarray(y[mask], dtype=float)
        if xr.size < 5:
            return None
        win = _clamp_sg_window(xr.size, int(self._analysis_controls().d2_window))
        polyorder = min(max(1, int(self._analysis_controls().d2_polyorder)), win - 1)
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
        if self._analysis_controls().derivative_detection:
            return str(self._analysis_controls().d2_mode)
        return str(self._analysis_controls().peak_mode)

    def _refresh_peak_delete_controls(self) -> None:
        # This narrow epoch also survives a consumed menu while its label prompt
        # runs. Replacing peaks and returning to the old values cannot revive it.
        signature = (id(self.peak_indices), tuple(int(i) for i in self.peak_indices),
                     self._peak_gate_idx)
        previous = getattr(self, '_peak_menu_targets', signature)
        if signature != previous:
            self._retire_peak_menu()
        self._peak_menu_targets = signature
        n = int(self._current_trace_peak_indices().size)
        self._analysis_status(deletePeakEnabled=n > 0, clearPeaksEnabled=n > 0)

    def _selected_peak_array_index(self) -> int | None:
        if self._current_trace_peak_indices().size == 0:
            return None
        n = int(self.ordinary_analysis.state['peakNumber'])
        if n < 1 or n > int(self._current_trace_peak_indices().size):
            return None
        return int(n - 1)

    def _sync_gate_controls(self) -> None:
        self._refresh_analysis_context()
        if hasattr(self, 'graph_documents'):
            self._indicate_sheet_gate()

    def _set_current_gate_from_index(self, gate_idx: int, *, update_plot: bool = True) -> None:
        if self.current_cube is None or self.current_cube.gate.size == 0:
            return
        gate_idx = int(np.clip(gate_idx, 0, self.current_cube.gate.size - 1))
        if self._workbench_document() is not None and not self._restoring_dataset:
            try:
                self._workbench_finish_editors()
            except ValueError as exc:
                self.publish_status(str(exc))
                self._sync_gate_controls()
                return
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
        self._retire_peak_menu()
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
            self._set_analysis_summary(f"{title_prefix}: no peaks.")
            return
        x_name = self.current_cube.energy_label if self.current_cube is not None else "X"
        lines = [f"{title_prefix} peaks @ gate {gate_used:.6g}:"]
        for i, p in enumerate(trace_peaks, start=1):
            lines.append(f"  {i}. {x_name}={x[p]:.6g}, I={y[p]:.6g}")
        self._set_analysis_summary("\n".join(lines))

    # ── CSV seeds helpers ─────────────────────────────────────────────────────

    def _on_seed_source_changed(self, *_args) -> None:
        self._refresh_fit_readiness()
        self.ordinary_analysis.refresh()

    def _fit_seed_status(self) -> tuple[np.ndarray, np.ndarray, str, bool]:
        """Return active seeds, in-range seeds, explanation, and detect availability."""
        if self.current_cube is None:
            return np.asarray([], dtype=float), np.asarray([], dtype=float), "Fit preparation: load data first.", False
        seeds = np.asarray(self._get_seeds_for_current_gate(), dtype=float)
        xlo, xhi = self._fit_range_bounds()
        in_range = seeds[(seeds >= xlo) & (seeds <= xhi)] if seeds.size else np.asarray([], dtype=float)
        if self._analysis_controls().csv_source:
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
        if not hasattr(self, 'ordinary_analysis'):
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
        if not error and self.current_cube is not None and self._analysis_controls().fit_smooth:
            points = int(np.count_nonzero(self._fit_mask(self.current_cube.energy)))
            note = self._sg_window_note(self._analysis_controls().fit_smooth_window, points)
            if note:
                message += '\n' + note
        self._analysis_status(readinessText=f"Fit preparation: {message}")
        self._analysis_status(fitText='Fitting Current Gate…' if busy else
                             "Detect Then Fit\nCurrent Gate" if can_detect else "Fit Current Gate")
        self._analysis_status(fitEnabled=not self._dat_import_pending and not error and not busy
                                and bool(can_detect or in_range.size))
        self._analysis_status(batchFitText='Batch Fitting…' if batch_busy else 'Batch Fit All Gates')
        self._analysis_status(batchFitEnabled=not self._dat_import_pending and self.current_cube is not None
                                      and not self._fit_validation_error() and not batch_busy
                                      and bool(self._csv_seeds_map if self._analysis_controls().csv_source else self.batch_peak_map))
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
        if self._analysis_controls().derivative_detection:
            return self._sg_window_error('Detection SG', self._analysis_controls().d2_window,
                                         polyorder=self._analysis_controls().d2_polyorder)
        if self._analysis_controls().peak_smooth:
            return self._sg_window_error('Detection pre-smooth', self._analysis_controls().peak_smooth_window)
        return ''

    def _fit_validation_error(self) -> str:
        minimum, maximum = self._analysis_controls().linewidth_min, self._analysis_controls().linewidth_max
        if maximum != 0 and maximum <= minimum:
            return ('Max linewidth must exceed Min linewidth; use 0 for an automatic upper bound '
                    '(about 2 × the fitted X span).')
        if self._analysis_controls().fit_smooth:
            return self._sg_window_error('Fit pre-smooth', self._analysis_controls().fit_smooth_window)
        return ''

    def _refresh_analysis_controls(self, *_args) -> None:
        if self._workbench_document() is not None:
            self._workbench_refresh()
            return
        error = self._detection_validation_error()
        note = ''
        if self.current_cube is not None:
            points = int(np.count_nonzero(self._peak_search_mask(self.current_cube.energy)))
            if self._analysis_controls().derivative_detection:
                note = self._sg_window_note(self._analysis_controls().d2_window, points,
                                            polyorder=self._analysis_controls().d2_polyorder)
            elif self._analysis_controls().peak_smooth:
                note = self._sg_window_note(self._analysis_controls().peak_smooth_window, points)
        self._analysis_status(detectionText=error or note)
        batch_busy = self._analysis_request_matches('batch-find', None)
        self._analysis_status(findEnabled=not self._dat_import_pending and self.current_cube is not None and not error)
        self._analysis_status(batchFindEnabled=not self._dat_import_pending and self.current_cube is not None
                                       and not error and not batch_busy)
        self._analysis_status(fullRangeEnabled=self.current_cube is not None, currentRangeEnabled=self.current_cube is not None,
                              findText='Find Peaks (This Gate)')
        self._analysis_status(batchFindText='Batch Detecting…' if batch_busy else 'Find Peaks — All Gates')
        self._refresh_fit_readiness()

    def _guard_analysis_controls(self, *, fitting=False, detecting=False) -> bool:
        if self._workbench_document() is None:
            try:
                self.ordinary_analysis.finish_editor()
            except ValueError as exc:
                self.publish_status(str(exc))
                return False
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
            self.analysis_workbench.setChoice('scope', 'current', self.analysis_workbench.ownerToken)
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
        if self._analysis_controls().csv_source:
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
        if self._analysis_controls().csv_source:
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
        if self._analysis_controls().csv_source:
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
        self._csv_seeds_path = ''
        self._analysis_controls().csv_source = False
        self._analysis_status()

    def _on_browse_root(self) -> None:
        if self._dat_import_pending:
            return
        folder = FilePicker.getExistingDirectory(self.root_window, "Choose Root Folder", self.current_root or ".")
        if not folder:
            return
        self.current_root = folder
        self.current_folder = ''
        ticket = self.source_navigation.begin_root(folder)
        root = Path(folder)
        self._start_background_job(
            lambda context: discover_folders(root, context),
            lambda folders: self._apply_dat_folders(ticket, folders), "Finding DAT folders…")

    def _apply_dat_folders(self, ticket, folders: list[Path]) -> None:
        if not self.source_navigation.apply_folders(ticket, folders):
            return
        if not folders:
            self.current_folder = ''
            self.publish_status("No .dat files found under selected root.")

    def _on_folder_changed(self, folder=None) -> None:
        if self._dat_import_pending:
            return
        folder = folder or self.source_navigation.selectedFolder
        if not folder:
            return
        self.current_folder = str(folder)
        ticket = self.source_navigation.begin_folder(folder)
        path = Path(folder)
        self._start_background_job(
            lambda context: discover_files(path, context),
            lambda files: self._apply_dat_files(ticket, files), "Reading folder…")

    def _apply_dat_files(self, ticket, files: list[str]) -> None:
        if not self.source_navigation.apply_files(ticket, files):
            return
        if files:
            self.publish_status('')
        else:
            self.publish_status("No dat files in selected process folder.")

    def _on_load_selected_file(self) -> None:
        if self._dat_import_pending:
            return
        if not self.source_navigation.selectedFile:
            self._show_error("Select one DAT file first.")
            return
        self.source_navigation.loadSelected()

    def _refresh_imported_datasets(self) -> None:
        if not hasattr(self, 'source_navigation'):
            return
        self.source_navigation.sync(reset_status=True)
        self._schedule_project_modified()

    def _copy_dataset_source_path(self, dataset_id: str) -> None:
        session = self.workspace.datasets.get(dataset_id)
        if session is not None and session.source_path is not None:
            QGuiApplication.clipboard().setText(str(session.source_path))
            self.publish_status('')

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

    def _set_dat_import_pending(self, pending):
        self._dat_import_pending = bool(pending)
        self.source_navigation.set_import_pending(pending)
        if not pending:
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
        self.set_cancel_visible(True)
        self.publish_status(f'Importing {len(ordered)} DAT file(s)…')

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
            self.publish_status(message)

    def _dat_import_failed(self, token: int, error: Exception) -> None:
        if not self._dat_import_is_current(token):
            return
        self._set_dat_import_pending(False)
        self.set_cancel_visible(False)
        message = f'Data import failed: {error}'
        self.source_navigation.set_import_status(message)
        self.publish_status(message)
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
            active_notice = self._status_text
            if save_notice:
                active_notice += ' — ' + save_notice
        else:
            active_notice = 'No DAT files imported; current dataset preserved.'
        self.set_cancel_visible(False)
        message = f'Imported {len(candidates)} new dataset(s). {active_notice}'
        if errors:
            failed = '\n'.join(f'{failure.path}: {failure.reason}' for failure in errors)
            message += f' {len(errors)} file(s) failed.'
            self.source_navigation.set_import_status(message + '\n' + failed + ('\n' + '\n'.join(notices) if notices else ''))
            self.publish_status(message)
            self._show_error('DAT import completed with file errors:\n' + failed)
        else:
            if notices:
                message += ' ' + ' '.join(notices)
            self.source_navigation.set_import_status(message)
            self.publish_status(message)

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
        self.set_cancel_visible(True)
        self.publish_status(description)
        self._background_jobs.start(
            work, apply, on_error=on_error or (lambda exc: self._show_error(str(exc))),
            on_progress=self.publish_status)
        self._refresh_analysis_controls()
        self._refresh_processing_controls()
        self._refresh_graph_commands()

    def _cancel_background_job(self) -> None:
        if self._project_io_jobs.busy:
            if self._project_io_finalizing:
                self.publish_status('Finishing opened project…')
                return
            self._project_io_cancelled = True
            if not self._project_io_committed:
                self._project_io_completion = None
            self._project_io_cancel_event.set()
            self.publish_status('Cancelling project operation…')
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
            self.set_cancel_visible(False)
            message = 'Data import cancelled. No new sources were added.'
            self.source_navigation.set_import_status(message)
            self.publish_status(message)
            return
        self._background_jobs.cancel()
        self._job_cancel_requested = self._background_jobs.busy
        if self.current_cube is not None:
            self.dat_properties._axis_override = self.current_cube.x_override
            self.dat_properties.syncFromOwner()
        self.publish_status(
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
            self.set_cancel_visible(False)
        if self._job_cancel_requested:
            self._job_cancel_requested = False
            self.publish_status("Operation cancelled.")
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
        settings.setValue('fixed_processing/enabled', state.enabled)
        if state.enabled:
            pending = self.processing.owner().pending
            state.recipe = {'mode': pending.mode, 'smooth': pending.smooth,
                            'window_length': pending.window_length}
            settings.setValue('fixed_processing/recipe', json.dumps(state.recipe))
        self._sync_annotation_settings()

    def _remember_fixed_regions(self, *_args) -> None:
        if self._restoring_dataset:
            return
        settings = self._annotation_settings
        state = (self.workspace.active or self._unregistered_session).fixed_recipes['regions']
        state.enabled = (self.workspace.active or self._unregistered_session).fixed_recipes['regions'].enabled
        settings.setValue("fixed_regions/enabled", (self.workspace.active or self._unregistered_session).fixed_recipes['regions'].enabled)
        if (self.workspace.active or self._unregistered_session).fixed_recipes['regions'].enabled:
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
        state.enabled = (self.workspace.active or self._unregistered_session).fixed_recipes['region_edges'].enabled
        settings.setValue("fixed_region_edges/enabled", (self.workspace.active or self._unregistered_session).fixed_recipes['region_edges'].enabled)
        if (self.workspace.active or self._unregistered_session).fixed_recipes['region_edges'].enabled:
            state.recipe = {
                "boundaries": self._color_region_boundaries,
            }
            settings.setValue("fixed_region_edges/recipe", json.dumps(state.recipe))
        self._sync_annotation_settings()

    def _apply_fixed_region_edges(self) -> None:
        if (self.workspace.active or self._unregistered_session).fixed_recipes['region_edges'].enabled:
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
        settings = settings or {'mode': 'original', 'smooth': True, 'window_length': 21,
                                'original_signal_title': RAW_LABEL}
        mode = settings.get('mode', 'original')
        if mode not in ('original', 'energy_second_derivative'):
            mode = 'original'
        owner = self.processing.owner()
        owner.pending = ProcessingState(mode, bool(settings.get('smooth', True)),
                                        int(settings.get('window_length', 21)))
        self._processing_smooth = owner.pending.smooth
        self._processing_window_length = owner.pending.window_length
        self._processing_original_signal_title = (
            str(settings.get('original_signal_title', RAW_LABEL))
            if mode == 'energy_second_derivative' else None)
        self._refresh_processing_controls()

    def _processing_validation_error(self) -> str:
        return self.processing.validation_error()

    def _refresh_processing_controls(self, *_args) -> None:
        self._refresh_analysis_context()
        self._refresh_source_axis_control()
        self.processing.syncFromOwner()

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

    def _on_generate_heatmap(self) -> None:
        if not self.processing.enabled:
            return
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        self._refresh_processing_controls()
        error = self._processing_validation_error()
        if error:
            self._show_error(error)
            return
        if self.processing.mode == "energy_second_derivative" and not self.processing.commitWindow():
            self._show_error(self.processing.error)
            return
        mode = self.processing.mode
        if mode == "original":
            self._restore_original_data()
            return
        self._apply_energy_second_derivative(
            smooth=self.processing.smoothing,
            window_length=self.processing.windowLength,
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
        applied = (self._processing_mode, self._processing_smooth, self._processing_window_length)
        if (applied == recipe and self.current_cube is not base
                and self._applied_derivative_sources.get(session.dataset_id) == source):
            self._cancel_processing_job()
            if session.dataset_id not in self.workspace.datasets or session.active_graph.analysis is not None:
                return
            # Cached values are immutable scientific buffers. A fresh cube and
            # result identity still give this explicit run its own Sheet/graph.
            cube = replace(self.current_cube)
            self._replace_current_data(cube, mode=recipe[0], smooth=recipe[1], window_length=recipe[2])
            self._applied_derivative_sources[session.dataset_id] = (id(base), id(cube))
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
                    and (self.processing.mode, self.processing.smoothing,
                         self.processing.windowLength) == recipe)

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
        session = self.workspace.active
        if session is not None and session.active_graph.analysis is None:
            self._finish_graph_editors()
            retain_current_processing_result(self.workspace, session.dataset_id)
        if session is not None and session.active_graph.analysis is None and session.processing_results:
            gates = deepcopy(session.active_graph.gate_indices)
            graph = next((graph for graph in session.graphs.values() if graph.analysis is None
                          and graph.source_mode == 'raw' and graph.gate_indices == gates), None)
            if graph is None:
                name, count = 'Original Heatmap', 2
                while any(graph.name == name for graph in session.graphs.values()):
                    name, count = f'Original Heatmap {count}', count + 1
                graph = session.create_graph(name)
                graph.source_mode, graph.result_id = 'raw', None
            session.activate_graph(graph.graph_id)
            self._select_graph_page(session, show=True)
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
        self.dat_properties._drafts.pop(self.dat_properties.owner_key(), None)
        if self._base_cube is None:
            self._base_cube = self.current_cube
        if self._base_cube is None:
            self._base_cube = cube
        xlim = self._heat_ax.get_xlim() if self._heat_ax is not None else (
            self._dat_number('xmin'), self._dat_number('xmax'))
        ylim = self._heat_ax.get_ylim() if self._heat_ax is not None else (
            self._dat_number('ymin'), self._dat_number('ymax'))
        base_cube = self._base_cube or self.current_cube or cube
        if float(xlim[0]) == float(xlim[1]):
            xlim = (float(np.nanmin(base_cube.energy)), float(np.nanmax(base_cube.energy)))
        if float(ylim[0]) == float(ylim[1]):
            ylim = (float(np.nanmin(base_cube.gate)), float(np.nanmax(base_cube.gate)))
        session = self.workspace.active or self._unregistered_session
        grouped = session.dataset_id in self.workspace.datasets and session.active_graph.analysis is None
        if grouped and mode == 'energy_second_derivative':
            self._finish_graph_editors()
            add_processing_result(self.workspace, session.dataset_id, cube,
                ProcessingState(mode, bool(smooth), int(window_length), session.applied.original_signal_title))
            self._select_graph_page(session, show=True)
        elif grouped and mode == 'original':
            retain_current_processing_result(self.workspace, session.dataset_id)
        self.current_cube = cube
        self._processing_mode = mode
        self._data_generation += 1
        self._processing_smooth = bool(smooth)
        self._processing_window_length = int(window_length)
        for graph in session.graphs.values():
            if grouped and graph is not session.active_graph:
                continue
            if graph is not session.active_graph and (graph.result_id is not None or graph.analysis is not None):
                continue
            refresh_graph_colors(graph, graph_cube(session, graph))
            if graph.view.signal_title_auto:
                graph.view.annotations['signal_title'] = graph_signal_label(session, graph)
        self._clear_processing_analysis_state()
        self._refresh_auto_signal_title()
        self._last_valid_color_limits = None
        self._last_valid_region_color_limits = []
        for spin, value in (('xmin', xlim[0]), ('xmax', xlim[1]),
                            ('ymin', ylim[0]), ('ymax', ylim[1])):
            self._dat_project_number(spin, value)
        self._update_auto_color_limits_from_visible_view(use_spin_values=True)
        self._plot_cube()
        self._heat_ax.set_xlim(xlim)
        self._heat_ax.set_ylim(ylim)
        self._update_auto_color_limits_from_visible_view(xlim=xlim, ylim=ylim)
        self._refresh_axis_status()
        self._refresh_processing_controls()
        self.publish_status('')
        if self.workspace.active_dataset_id is not None:
            self._sync_result_sheets()
            self._refresh_source_axis_control()
            self._refresh_document_titles()
            self._refresh_graph_selector()
            self._refresh_graph_commands()

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
            if any(canonical_colormap(recipe['colormap']) not in self._available_colormaps() for recipe in recipes):
                saved_plot = None
                recall_notice = "Saved plot settings ignored: the colormap is unavailable."
            override = saved_plot["axis_override"] if saved_plot else self._selected_x_axis_override()
            cube = load_dat_cube(path, x_axis_override=override)
            if source_fingerprint(path) != fingerprint:
                raise ValueError("DAT content changed while loading. Please load it again.")
        except Exception as exc:
            self._show_error(str(exc))
            return
        try:
            self._apply_loaded_dat(path, cube, fingerprint, saved_plot, recall_notice, save_notice)
        except ValueError as exc:
            self._show_error(str(exc))

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
        self.publish_status(notice or '')

    def _current_plot_state(self) -> dict | None:
        if (self.current_dat_path is None or self.current_cube is None
                or self._loaded_source_fingerprint is None
                or self._heat_ax is None or self._heat_mesh is None):
            return None
        bounds = ("xmin", "xmax", "ymin", "ymax", "vmin", "vmax")
        return {
            "axis_override": self.current_cube.x_override,
            "colormap": self._selected_colormap(),
            "annotations": dict(self._dat_view().annotations),
            "fix_annotations": self._dat_view().fix_annotations,
            "signal_title_auto": self._dat_view().signal_title_auto,
            "title_settings": self._copy_title_settings(self._title_settings),
            "bounds": {name: self._dat_number(name) for name in bounds},
            "locks": {name: self._dat_lock(name) for name in bounds},
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
        view = session.view
        view.norm = NormSpec.from_dict(plot.get('norm', {}))
        session.presentation = PlotPresentation.from_dict(plot.get('presentation', {}))
        if self.plot_page.presentation != session.presentation:
            self.plot_page.set_presentation(session.presentation)
        view.title_settings = self._copy_title_settings(plot['title_settings'])
        view.colormap = canonical_colormap(plot['colormap'])
        view.annotations = dict(plot['annotations'])
        view.fix_annotations = plot['fix_annotations']
        view.signal_title_auto = plot.get('signal_title_auto', False)
        view.bounds = dict(plot['bounds'])
        view.locks = dict(plot['locks'])
        view.x_limits = tuple(plot.get('x_limits', view.x_limits))
        view.y_limits = tuple(plot.get('y_limits', view.y_limits))
        view.color_limits = tuple(plot.get('color_limits', view.color_limits))
        self.dat_properties._axis_override = plot['axis_override']
        self._title_source = self.current_cube.source_path or id(self.current_cube)
        self._show_title_settings()
        mode = plot.get('color_scale_mode', 'global')
        view.color_scale_mode = mode if mode in ('global', '2', '3') else 'global'
        view.color_region_boundaries = [float(value) for value in plot.get('color_region_boundaries', [])]
        view.last_valid_region_color_limits = [tuple(pair) for pair in plot.get('color_region_limits', [])]
        self._sync_region_controls()
        self._annotation_source = self.current_cube.source_path or id(self.current_cube)
        gate_idx = int(np.argmin(np.abs(self.current_cube.gate - plot['gate'])))
        if float(self.current_gate) != float(self.current_cube.gate[gate_idx]):
            self._retire_peak_menu()
        self.current_gate = float(self.current_cube.gate[gate_idx])
        self._sync_gate_controls()
        self.dat_properties.syncFromOwner()

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
            self._update_controller = UpdateController(self.root_window)
            self._update_controller.install_requested.connect(self.close)
        self._update_controller.open()

    def closeEvent(self, event) -> None:
        updater = getattr(self, '_update_controller', None)
        if updater is not None and not updater.prepare_close():
            self.publish_status('Stopping the update operation before closing…')
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
            self.publish_status("Stopping operation before closing…")
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
            self.publish_status(notice)
        for window in tuple(self._plot_comparison_windows):
            window.close()
        event.accept()
        if event.isAccepted():
            self._closing = True
            self._quick_host.detach_scenes()
            self.dat_canvas.dispose()
            self.analysis_navigation.close()
            self.analysis_header.close()
            self.ordinary_analysis.close()
        if event.isAccepted() and updater is not None:
            try:
                updater.accept_close()
            except Exception as exc:
                event.ignore()
                updater.install_failed(exc)
        if event.isAccepted():
            # Focus-out emitted as Quick items retire must not edit a closed
            # owner after native commands have begun QObject destruction.
            self.analysis_workbench.shutdown()
            self.dat_properties._closed = True

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
            self.dat_properties._axis_override = self.current_cube.x_override
            self.dat_properties.syncFromOwner()
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
            self.dat_properties._axis_override = self.current_cube.x_override
            self.dat_properties.syncFromOwner()
            self._show_error(str(exc))
        self._start_background_job(
            lambda context: prepare_dat(request, context),
            lambda prepared: self._apply_axis_override(prepared[0], prepared[1], gate),
            "Updating axis interpretation…", on_error=failed)

    def _apply_axis_override(self, cube, fingerprint, gate) -> None:
        session = self.workspace.active or self._unregistered_session
        if session.processing_results:
            self.dat_properties._axis_override = session.base_cube.x_override
            self.dat_properties.syncFromOwner()
            self._show_error('Retained processing results use this saved axis. Import the DAT as a new copy to change its axis interpretation.')
            return
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
        return self._dat_view().colormap

    def _available_colormaps(self) -> tuple[str, ...]:
        return tuple(value for _label, value in COLORMAPS)

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

    def _lock_user_color_bound(self, field) -> None:
        with self._edit_transaction('Color range', RANGE_FIELDS):
            if self._restoring_dataset or self._view_syncing or field not in ('vmin', 'vmax'):
                return
            self._dat_view().locks[field] = True
            self._refresh_color_range_status()

    def _on_auto_colors(self) -> None:
        with self._edit_transaction('Auto colors', COLOR_FIELDS):
            self._dat_view().locks.update(vmin=False, vmax=False)
            self._update_auto_color_limits_from_visible_view()
            self._capture_dataset_controls()
            self._refresh_color_range_status()

    def _refresh_color_range_status(self, *, visible_values=None) -> None:
        regional = self._color_scale_mode in ('2', '3')
        low, high = self._dat_lock('vmin'), self._dat_lock('vmax')
        mode = ('Manual' if low and high else 'Partial manual (min)' if low else
                'Partial manual (max)' if high else 'Auto')
        regional_mode = ('Fixed endpoints' if low and high else 'Partial Fixed (min)' if low else
                         'Partial Fixed (max)' if high else 'independent Auto')
        self._dat_status('color_range_status', 
            f'{self._color_scale_mode} regions: {regional_mode}; global bound editors disabled. '
            'Visible data, 0.5–99.5 percentiles for unlocked endpoints.'
            if regional else f'Global: {mode}. Visible data, 0.5–99.5 percentiles for unlocked bounds.')
        if self.current_cube is None:
            self._dat_status('color_clipping_status', '')
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
        self._dat_status('color_clipping_status', 
            f'Below: {under} ({100*under/total if total else 0:.2f}%) · Above: {over} ({100*over/total if total else 0:.2f}%) of {total} visible valid cells')

    def _on_view_limit_changed(self, field: str) -> None:
        if self._view_syncing:
            return
        with self._edit_transaction('View range', RANGE_FIELDS):
            if field.startswith('x'):
                self._invalidate_analysis_jobs()
            self._apply_view_limit_change(field)
            self._capture_dataset_controls(visual_only=True)

    def _apply_view_limit_change(self, field):
        xy_changed = field in ('xmin', 'xmax', 'ymin', 'ymax')
        if xy_changed:
            intended_x = self._ordered_pair(self._dat_number('xmin'), self._dat_number('xmax'))
            intended_y = self._ordered_pair(self._dat_number('ymin'), self._dat_number('ymax'))
            self._update_auto_color_limits_from_visible_view(use_spin_values=True)
            self._dat_view().x_limits, self._dat_view().y_limits = intended_x, intended_y
        else:
            self._last_valid_color_limits = self._ordered_pair(self._dat_number('vmin'), self._dat_number('vmax'))
        if self._heat_ax is None:
            self._plot_cube()
            return
        previous_syncing = self._view_syncing
        self._view_syncing = True
        try:
            if xy_changed:
                self._heat_ax.set_xlim(intended_x)
                self._heat_ax.set_ylim(intended_y)
                self._dat_view().x_limits = intended_x
                self._dat_view().y_limits = intended_y
                self._update_spectrum_only()
                self._schedule_view_layout()
            else:
                self._apply_color_limits_to_mesh()
        finally:
            self._view_syncing = previous_syncing
        self._invalidate_plot_page()
        self.canvas.draw_idle()

    def _on_view_lock_toggled(self, field: str, checked: bool) -> None:
        with self._edit_transaction('Range lock', RANGE_FIELDS):
            self._refresh_color_range_status()
            if not checked and self.current_cube is not None and field in ('vmin', 'vmax'):
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
            if (self._dat_number('xmin'), self._dat_number('xmax')) != (xmin, xmax):
                self._invalidate_analysis_jobs()
            self._dat_view().x_limits = (xmin, xmax)
            for spin, value in (('xmin', xmin), ('xmax', xmax)):
                self._dat_project_number(spin, value)
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
            self._dat_view().y_limits = (ymin, ymax)
            for spin, value in (('ymin', ymin), ('ymax', ymax)):
                self._dat_project_number(spin, value)
            self._update_auto_color_limits_from_visible_view(ylim=(ymin, ymax))
        finally:
            self._view_syncing = False
        self._invalidate_plot_page()

    def _on_color_scale_changed(self) -> None:
        with self._edit_transaction('Color regions', STYLE_FIELDS):
            self._color_scale_mode = str(self._dat_view().color_scale_mode or "global")
            self._last_valid_region_color_limits = []
            # A user-selected color mode starts independent autoscaling. Projection
            # restores stored regional locks with signals blocked in its own path.
            self._dat_view().locks.update(vmin=False, vmax=False)
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
        if not hasattr(self, 'dat_properties'):
            return
        regional = self._color_scale_mode in ('2', '3')
        _edges, _slices, boundaries = self._region_layout()
        self._color_region_boundaries = boundaries if regional else []
        self.dat_properties.syncFromOwner()
        self._refresh_color_range_status()

    def _on_color_region_boundary_changed(self, boundary_idx: int, value: float, *, render=True) -> None:
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
            if render:
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
            xlim = (float(self._dat_number('xmin')), float(self._dat_number('xmax')))
        elif xlim is None:
            xlim = self._heat_ax.get_xlim()
        if ylim is None and (use_spin_values or self._heat_ax is None):
            ylim = (float(self._dat_number('ymin')), float(self._dat_number('ymax')))
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
        low_locked, high_locked = self._dat_lock('vmin'), self._dat_lock('vmax')
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
                low, high = float(self._dat_number('vmin')), float(self._dat_number('vmax'))
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
        return ranges or [(float(self._dat_number('vmin')), float(self._dat_number('vmax')))]

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
            xlim = (float(self._dat_number('xmin')), float(self._dat_number('xmax')))
        elif xlim is None:
            xlim = self._heat_ax.get_xlim()
        if ylim is None and (use_spin_values or self._heat_ax is None):
            ylim = (float(self._dat_number('ymin')), float(self._dat_number('ymax')))
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
        is_low_locked = self._dat_lock('vmin')
        is_high_locked = self._dat_lock('vmax')

        if not values.size and self._current_norm_spec().kind == 'log':
            self._display_mapping_error('Log mapping requires positive visible values; none are available.')
            return
        if is_low_locked and is_high_locked:
            candidate = (float(self._dat_number('vmin')), float(self._dat_number('vmax')))
        elif values.size:
            candidate_low, candidate_high = np.nanpercentile(values, [0.5, 99.5])
            candidate = (float(candidate_low), float(candidate_high))
        elif self._last_valid_color_limits is not None:
            candidate = self._last_valid_color_limits
        else:
            candidate = (
                float(self._dat_number('vmin')), float(self._dat_number('vmax'))
            )

        low = float(self._dat_number('vmin')) if is_low_locked else float(candidate[0])
        high = float(self._dat_number('vmax')) if is_high_locked else float(candidate[1])
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
            ('vmin', low, is_low_locked),
            ('vmax', high, is_high_locked),
        ):
            if locked:
                continue
            self._dat_project_number(spin, value)

        self._last_valid_color_limits = self._ordered_pair(
            float(self._dat_number('vmin')), float(self._dat_number('vmax'))
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
            float(self._dat_number('vmin')), float(self._dat_number('vmax'))
        )
        self._heat_mesh.set_norm(build_color_norm(self._current_norm_spec(), (float(vmin), float(vmax))))
        if self._heat_colorbar is not None:
            self._heat_colorbar.update_normal(self._heat_mesh)
        self._layout_live_plots()
        self.canvas.draw_idle()

    def _on_toggle_circle_delete_mode(self, on: bool) -> None:
        self._circle_delete_mode = bool(on)
        self._analysis_status()
        self._batch_delete_circle_start = None
        msg = "Circle delete ON: drag on the heatmap to remove all-gate peaks inside the circle." if on else "Circle delete OFF."
        self.publish_status(msg)

    def _on_set_fit_full_range(self) -> None:
        before = (self._analysis_controls().fit_xmin, self._analysis_controls().fit_xmax)
        self._set_fit_range_to_full()
        if before != (self._analysis_controls().fit_xmin, self._analysis_controls().fit_xmax):
            self._invalidate_analysis_jobs()
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
        controls = self._analysis_controls()
        before = (controls.fit_xmin, controls.fit_xmax)
        xmin, xmax = self._ordered_pair(float(self._dat_number('xmin')), float(self._dat_number('xmax')))
        controls.fit_xmin = self.ordinary_analysis.normalized_number('fit_xmin', xmin)
        controls.fit_xmax = self.ordinary_analysis.normalized_number('fit_xmax', xmax)
        if before != (controls.fit_xmin, controls.fit_xmax):
            self._invalidate_analysis_jobs()
        self._refresh_fit_range_summary()
        self._refresh_fit_readiness()
        self._capture_dataset_controls()
        self._update_spectrum_only()

    def _refresh_fit_range_summary(self) -> None:
        if not hasattr(self, 'ordinary_analysis'):
            return
        if self.current_cube is None:
            self._analysis_status(rangeText="Detection range: —\nFit range: —")
            return
        detection = self._ordered_pair(float(self._dat_number('xmin')), float(self._dat_number('xmax')))
        fitting = self._fit_range_bounds()
        unit = self.current_cube.energy_label
        self._analysis_status(rangeText=
            f"Detection range (visible X): {detection[0]:.6g}–{detection[1]:.6g} {unit}\n"
            f"Fit range (independent): {fitting[0]:.6g}–{fitting[1]:.6g} {unit}"
        )
        if hasattr(self, 'ordinary_analysis'):
            self._refresh_analysis_controls()

    def _set_fit_range_to_full(self) -> None:
        if self.current_cube is None:
            return
        controls = self._analysis_controls()
        if not controls.fit_xmin_fixed:
            controls.fit_xmin = self.ordinary_analysis.normalized_number('fit_xmin', np.nanmin(self.current_cube.energy))
        if not controls.fit_xmax_fixed:
            controls.fit_xmax = self.ordinary_analysis.normalized_number('fit_xmax', np.nanmax(self.current_cube.energy))
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
            ('xmin', xmin), ('xmax', xmax),
            ('ymin', ymin), ('ymax', ymax),
        ):
            if self._dat_lock(spin):
                continue
            self._dat_project_number(spin, val)
        intended_x, intended_y = self._dat_view().x_limits, self._dat_view().y_limits
        self._update_auto_color_limits_from_visible_view(use_spin_values=True)
        self._dat_view().x_limits, self._dat_view().y_limits = intended_x, intended_y

    # =========================================================================
    #  Find Peak logic
    # =========================================================================

    def _on_detect_mode_changed(self, orig_is_checked: bool) -> None:
        self._cached_d2 = None
        self._analysis_status()

    def _on_orig_type_changed(self, text: str) -> None:
        self._analysis_status()

    def _on_d2_type_changed(self, text: str) -> None:
        self._analysis_status()

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

    def _retire_peak_menu(self) -> None:
        self._peak_menu_epoch = getattr(self, '_peak_menu_epoch', 0) + 1
        view = getattr(self, 'dat_canvas', None)
        if view is not None:
            view.clear_peak_menu()

    def _peak_menu_request_valid(self, request: PeakMenuRequest) -> bool:
        session = self.workspace.active or self._unregistered_session
        bridge = self.dat_canvas
        if (not bridge.allowed(request.pointer.owner) or self.toolbar.mode
                or request.request_id != str(bridge._peak_menu_serial)
                or self._dat_import_pending or session is not request.session
                or session.active_graph is not request.graph
                or self.current_cube is not request.cube or self._spec_ax is not request.axes
                or self._current_gate_index() != request.gate
                or getattr(self, '_peak_menu_epoch', 0) != request.epoch
                or self.peak_indices is not request.targets
                or tuple(int(i) for i in self._current_trace_peak_indices()) != request.target_indices):
            return False
        key = (request.gate, request.energy_index)
        return (request.energy_index in request.target_indices
                and 0 <= request.energy_index < self.current_cube.energy.size
                and float(self.current_cube.energy[request.energy_index]) == request.energy
                and self._resolved_peak_label(*key) == request.label
                and self.peak_label_lookup.get(key) == request.override)

    def _show_peak_context_menu(self, x_click: float, pointer: CanvasPointerContext) -> None:
        """Capture science on press; the canvas publishes only after right release."""
        if (not isinstance(pointer, CanvasPointerContext) or self.current_cube is None
                or not self.dat_canvas.allowed(pointer.owner)):
            return
        trace_peak_indices = self._current_trace_peak_indices()
        if trace_peak_indices.size == 0:
            self.dat_canvas.request_format_menu(pointer)
            return
        x = self.current_cube.energy
        peak_x = x[trace_peak_indices]
        x_span = max(float(np.nanmax(x) - np.nanmin(x)), 1e-12)
        dists = np.abs(peak_x - x_click)
        nearest_arr_idx = int(np.argmin(dists))
        if dists[nearest_arr_idx] > 0.02 * x_span:
            self.dat_canvas.request_format_menu(pointer)
            return
        nearest_idx = int(trace_peak_indices[nearest_arr_idx])
        gate_idx = self._current_gate_index()
        current_label = self._resolved_peak_label(gate_idx, nearest_idx)

        session = self.workspace.active or self._unregistered_session
        bridge = self.dat_canvas
        request = PeakMenuRequest(bridge.next_peak_menu_id(), pointer, session,
            session.active_graph, self.current_cube, self._spec_ax, gate_idx,
            nearest_idx, float(x[nearest_idx]), current_label,
            self.peak_label_lookup.get((gate_idx, nearest_idx)),
            getattr(self, '_peak_menu_epoch', 0), self.peak_indices,
            tuple(int(i) for i in trace_peak_indices))
        if self._peak_menu_request_valid(request):
            bridge.request_peak_menu(request)

    def _continue_peak_menu(self, request: PeakMenuRequest, choice: str) -> None:
        if choice not in ('delete', 'set_label') or not self._peak_menu_request_valid(request):
            return
        if choice == 'delete':
            if self._remove_manual_peak_near(request.energy):
                gate_used, y = self._nearest_spectrum(self.current_gate)
                self._update_spectrum_only()
                self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")
        else:
            new_label, ok = prompt_int(self.root_window, "Set Peak Label",
                "Label index (1 = first peak, 2 = second, …):",
                value=request.label, min=1, max=99,
            )
            if (ok and isinstance(new_label, int) and 1 <= new_label <= 99
                    and new_label != request.label and self._peak_menu_request_valid(request)):
                self._finish_history_edit()
                session = request.session
                key = (request.gate, request.energy_index)
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
            self.analysis_workbench.setChoice('scope', 'current', self.analysis_workbench.ownerToken)
            return self._workbench_find()
        if self._dat_import_pending:
            return
        if not self._guard_analysis_controls(detecting=True):
            return
        self._invalidate_analysis_jobs()
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        self._analysis_status(findText='Detecting Current Gate…')
        self._analysis_status(findEnabled=False)
        self._analysis_status(detectionText='Detecting current gate…')
        self.publish_status('Detecting current gate…')
        # Paint local feedback before the synchronous finder occupies the GUI.
        try:
            self._find_current_gate_peaks()
        finally:
            self._analysis_status(findText='Find Peaks (This Gate)')
            if self._status_text == 'Detecting current gate…':
                self.publish_status('')
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

        if self._analysis_controls().derivative_detection:
            # Second-derivative mode
            try:
                idx_local, kinds_local, d2y = find_peaks_second_derivative(
                    xr, yr,
                    mode=self._peak_mode(),  # type: ignore[arg-type]
                    sg_window=int(self._analysis_controls().d2_window),
                    sg_polyorder=int(self._analysis_controls().d2_polyorder),
                    d2_prom_frac=float(self._analysis_controls().d2_prominence),
                    d2_distance=int(self._analysis_controls().d2_distance),
                    d2_sep_ev=float(self._analysis_controls().d2_separation),
                    max_peaks=int(self._analysis_controls().d2_max_peaks),
                    max_dips=int(self._analysis_controls().d2_max_dips),
                    target_count=int(self._analysis_controls().d2_target),
                )
                self._cached_d2 = (xr, d2y)
                window = _clamp_sg_window(xr.size, int(self._analysis_controls().d2_window))
                polyorder = min(max(1, int(self._analysis_controls().d2_polyorder)), window - 1)
                self._cached_d2_key = self._second_derivative_key(mask, window, polyorder)
            except Exception as exc:
                self._show_error(str(exc))
                return
        else:
            # Original spectrum mode
            if self._analysis_controls().peak_smooth:
                win = _clamp_sg_window(yr.size, int(self._analysis_controls().peak_smooth_window))
                if win >= 3 and yr.size > win:
                    yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")
            try:
                idx_local, kinds_local = find_peak_indices(
                    xr, yr,
                    mode=self._peak_mode(),  # type: ignore[arg-type]
                    prominence_frac=float(self._analysis_controls().prominence),
                    min_distance=int(self._analysis_controls().distance),
                    max_peaks=int(self._analysis_controls().max_peaks),
                    max_dips=int(self._analysis_controls().max_dips),
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
        if float(self.current_gate) != float(gate_used):
            self._retire_peak_menu()
        self.current_gate = gate_used
        self._sync_gate_controls()
        self._update_spectrum_only()
        self._update_peak_text(gate_used, x, y, title_prefix="Auto-detected")

    def _on_batch_find_peaks_all_gates(self) -> None:
        if self._workbench_document() is not None:
            self.analysis_workbench.setChoice('scope', 'all', self.analysis_workbench.ownerToken)
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

        use_d2 = self._analysis_controls().derivative_detection
        peak_mode = self._peak_mode()
        prom = float(self._analysis_controls().prominence)
        min_dist = int(self._analysis_controls().distance)
        max_peaks_o = int(self._analysis_controls().max_peaks)
        max_dips_o = int(self._analysis_controls().max_dips)
        sg_win = int(self._analysis_controls().d2_window)
        sg_poly = int(self._analysis_controls().d2_polyorder)
        d2_prom = float(self._analysis_controls().d2_prominence)
        d2_dist = int(self._analysis_controls().d2_distance)
        d2_sep = float(self._analysis_controls().d2_separation)
        d2_max = int(self._analysis_controls().d2_max_peaks)
        d2_max_dips = int(self._analysis_controls().d2_max_dips)
        d2_tgt = int(self._analysis_controls().d2_target)

        key = self._analysis_job_key()
        smooth = self._analysis_controls().peak_smooth
        smooth_window = int(self._analysis_controls().peak_smooth_window)
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
                self.publish_status("Batch peaks discarded because analysis inputs changed.")
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
            self.publish_status('')
            self._set_analysis_summary(
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
        out_path, _ = FilePicker.getSaveFileName(self.root_window, "Save Peaks CSV", str(default), "CSV Files (*.csv)")
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
        self.publish_status('')

    def _on_delete_peak_by_number(self) -> None:
        with self._peak_edit('Remove peak', (self._current_gate_index(),)):
            if self._dat_import_pending:
                return
            if self.current_cube is None or self.peak_indices.size == 0:
                return
            if self._peak_gate_idx != self._current_gate_index():
                return
            n = int(self.ordinary_analysis.state['peakNumber'])
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
            if not self.analysis_workbench.allowed() or not self.analysis_workbench.commitEditors(self.analysis_workbench.ownerToken):
                return
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
            if not self.analysis_workbench.allowed() or not self.analysis_workbench.commitEditors(self.analysis_workbench.ownerToken):
                return
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
            self.publish_status('')

    # =========================================================================
    #  Lasso selection
    # =========================================================================

    def _on_detect_mode_changed_lasso_guard(self) -> None:
        """Called when detection mode changes; disconnect lasso if active."""
        if self._lasso_mode:
            self._on_lasso_mode_toggled(False)

    def _on_lasso_mode_toggled(self, on: bool) -> None:
        self._lasso_mode = on
        self._analysis_status()
        if not on:
            self._lasso_selected_peak_ids = []
            self._clear_lasso_selection_visual()
            self._update_lasso_actions()
        self._disconnect_lasso_selector()
        if on:
            self._connect_lasso_selector()
            if not self._lasso_mode:
                return
            self.publish_status(
                "Lasso mode active: drag on heatmap to select peaks. Normal heatmap click is temporarily disabled."
            )
        else:
            self.publish_status('')
        self._set_canvas_cursor_for_event(None)
        self._update_spectrum_only()

    def _on_lasso_complete(self, verts: Any) -> None:
        if self._dat_import_pending:
            return
        if self.current_cube is None or not self.batch_peak_records or self.batch_peak_coords is None:
            self._lasso_selected_peak_ids = []
            self._clear_lasso_selection_visual()
            self._update_lasso_actions()
            self.publish_status("Lasso: no all-gate heatmap peaks are available to select.")
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
            self.publish_status(
                f"Lasso: selected {n_sel} of {n_total} heatmap peaks. Press 'Delete Lasso Selection (Heatmap)' to remove."
            )
        else:
            self.publish_status(f"Lasso: 0 of {n_total} heatmap peaks fell inside the selection.")

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
            self.publish_status('')

    def _on_clear_lasso_selection(self) -> None:
        self._lasso_selected_peak_ids = []
        self._clear_lasso_selection_visual()
        self._update_lasso_actions()
        self._update_spectrum_only()
        self.publish_status('')

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
            self._analysis_controls().fit_smooth, self._analysis_controls().fit_smooth_window,
            self._analysis_controls().fit_model, self._analysis_controls().linewidth_min,
            self._analysis_controls().linewidth_max, self._analysis_controls().peak_move_limit, self._analysis_controls().maxfev,
            self._fit_range_bounds(), self._peak_search_bounds(), self._peak_mode(),
            self._analysis_controls().csv_source, self._analysis_controls().derivative_detection,
            self._analysis_controls().prominence, self._analysis_controls().distance, self._analysis_controls().max_peaks,
            self._analysis_controls().max_dips, self._analysis_controls().peak_smooth,
            self._analysis_controls().peak_smooth_window, self._analysis_controls().d2_window,
            self._analysis_controls().d2_polyorder, self._analysis_controls().d2_prominence, self._analysis_controls().d2_distance,
            self._analysis_controls().d2_separation, self._analysis_controls().d2_max_peaks,
            self._analysis_controls().d2_max_dips, self._analysis_controls().d2_target,
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
            self.analysis_workbench.setChoice('scope', 'current', self.analysis_workbench.ownerToken)
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
                smooth=self._analysis_controls().fit_smooth,
                smooth_window=int(self._analysis_controls().fit_smooth_window),
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

        lw_min = float(self._pts_to_axis_units(int(self._analysis_controls().linewidth_min)) or 0.0)
        lw_max_v = float(self._pts_to_axis_units(int(self._analysis_controls().linewidth_max)) or 0.0)
        lw_max = lw_max_v
        peak_move = float(self._pts_to_axis_units(int(self._analysis_controls().peak_move_limit)) or 0.0)
        maxfev = int(self._analysis_controls().maxfev)

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
        model = str(self._analysis_controls().fit_model)
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
                self.publish_status("Fit discarded because analysis inputs changed.")
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
                self.publish_status('')
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
            self._set_analysis_summary("\n".join(lines))
            self.publish_status('')

        self._start_analysis_job('fit', gate_idx, key, work, apply, "Fitting current gate…",
                                 prefix='Fit failed: ')

    def _on_clear_fit(self) -> None:
        if self._workbench_document() is not None:
            if not self.analysis_workbench.allowed() or not self.analysis_workbench.commitEditors(self.analysis_workbench.ownerToken):
                return
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
            self.analysis_workbench.setChoice('scope', 'all', self.analysis_workbench.ownerToken)
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
        if self._analysis_controls().csv_source and self._csv_seeds_map is None:
            self._show_error("CSV seed source is selected, but no seeds CSV is loaded.")
            return
        use_csv = self._analysis_controls().csv_source
        if not use_csv and not self.batch_peak_map:
            self._show_error(
                "No seeds available for batch fit.\n"
                "Run 'Find Peaks – All Gates' in Find Peaks tab first,\n"
                "or load a seeds CSV in the Peak Fit tab."
            )
            return

        model = str(self._analysis_controls().fit_model)
        peak_mode = self._peak_mode()
        use_kinds = (peak_mode == "Both")
        x = cube.energy
        mask = self._fit_mask(x)
        xr_base = np.asarray(x[mask], dtype=float)
        if xr_base.size < 5:
            self._show_error("Fit range is too narrow for batch fit.")
            return

        lw_min = float(self._pts_to_axis_units(int(self._analysis_controls().linewidth_min)) or 0.0)
        lw_max_v = float(self._pts_to_axis_units(int(self._analysis_controls().linewidth_max)) or 0.0)
        lw_max = lw_max_v
        peak_move = float(self._pts_to_axis_units(int(self._analysis_controls().peak_move_limit)) or 0.0)
        maxfev = int(self._analysis_controls().maxfev)
        xlo, xhi = self._fit_range_bounds()

        key = self._analysis_job_key()
        smooth = self._analysis_controls().fit_smooth
        smooth_window = int(self._analysis_controls().fit_smooth_window)
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
                self.publish_status("Batch fit discarded because analysis inputs changed.")
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
            self.publish_status(f"Batch fit done: OK={ok_count}, fail={fail_count}" if fail_count else '')
            self._set_analysis_summary(
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
        path, _ = FilePicker.getOpenFileName(self.root_window, "Load Seeds CSV", default_dir, "CSV Files (*.csv)")
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
            self._csv_seeds_path = str(path.resolve())
            self._schedule_project_modified()
            self._analysis_controls().csv_source = True
            self._analysis_status()
            self.publish_status('')
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
        if not hasattr(self, 'ordinary_analysis'):
            return
        current_available = self._active_fit_result() is not None if self.current_cube is not None else False
        live = self.current_cube is not None and not self._dat_import_pending
        self._analysis_status(exportCurrentFitEnabled=current_available,
            clearFitEnabled=current_available or bool(self.batch_fit_result_map or self.batch_fit_map),
            exportSpectrumEnabled=live, loadCsvEnabled=live,
            savePeaksEnabled=live and (any(np.asarray(indices).size for indices in self.batch_peak_map.values())
                                       or bool(self._current_trace_peak_indices().size)),
            lassoEnabled=live, circleEnabled=live,
            clearBatchEnabled=live and bool(self.batch_peak_map))
        graph_available = current_available or bool(self._active_graph_peaks()[0].size)
        self._analysis_status(createAnalysisEnabled=graph_available and self.workspace.active is not None
                                                  and not self._project_busy())
        # Keep refreshes cheap during axis navigation; serialize rows only when
        # the user invokes the all-results export action.
        self._analysis_status(exportAllFitEnabled=bool(
            self._batch_fit_export_rows or self.batch_fit_result_map or self.fit_result
        ))
        if hasattr(self, 'plot_page'):
            self.plot_page.set_command_availability('export_png',
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


    def _show_title_settings(self) -> None:
        settings = self._title_settings
        preset = settings['preset'] if settings['mode'] == 'template' else BUILTIN_PRESET
        notice = 'Load a DAT to preview this template.'
        if settings['manual_override'] is not None:
            notice = 'Manual override for this DAT; Apply template to regenerate.'
        elif settings['mode'] == 'template' and self.current_cube is not None:
            try:
                notice = 'Preview: ' + render_title(self.current_cube.source_path or '', preset)
            except ValueError as exc:
                notice = str(exc)
        self._dat_status('title_template_status', notice)

    def _remember_title_selection(self) -> None:
        self._default_title_settings = self._copy_title_settings(self._title_settings)
        self._default_title_settings["manual_override"] = None
        self._annotation_settings.setValue("plot_titles/selection", json.dumps(self._default_title_settings))
        self._sync_annotation_settings()

    def _on_title_preset_selected(self, name: str) -> None:
        with self._edit_transaction('Title preset', TITLE_FIELDS):
            preset = None if name == 'Manual' else (BUILTIN_PRESET if name == 'Current group' else self._title_presets[name])
            self.dat_properties._templates.pop(self.dat_properties.owner_key(), None)
            self._title_settings = (manual_settings(self._dat_annotation('plot_title')) if preset is None else
                                    {"mode": "template", "preset": self._copy_title_settings(preset), "manual_override": None})
            self._show_title_settings()
            self._remember_title_selection()
            if preset is not None:
                self._apply_title_template()

    def _edited_title_preset(self, name: str | None = None) -> dict:
        preset = {"name": name or self._title_settings["preset"]["name"],
                  "pattern": self.dat_properties.template('title_pattern'),
                  "angle_map": parse_angle_map(self.dat_properties.template('title_angle_map'))}
        return validate_preset(preset)

    def _on_title_template_edited(self, *_args) -> None:
        if self._title_settings["mode"] != "template":
            return
        try:
            preset = self._edited_title_preset()
        except ValueError as exc:
            self._title_settings["manual_override"] = ""
            self._set_title_text("")
            self._dat_status('title_template_status', str(exc))
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
                self._dat_status('title_template_status', str(exc))
                return
            self._title_settings = {"mode": "template", "preset": preset, "manual_override": None}
            self._apply_title_template()

    def _save_title_preset(self) -> None:
        with self._edit_transaction('Title preset', TITLE_FIELDS):
            name = self.dat_properties.template('title_preset_name').strip()
            try:
                if not name or name in ("Manual", "Current group"):
                    raise ValueError("Choose a name other than Manual or Current group.")
                preset = self._edited_title_preset(name)
            except ValueError as exc:
                self._dat_status('title_template_status', str(exc))
                return
            self._title_presets[name] = preset
            self._annotation_settings.setValue("plot_titles/presets", json.dumps(self._title_presets))
            self._title_settings = {"mode": "template", "preset": preset, "manual_override": None}
            self._show_title_settings()
            self._remember_title_selection()
            self._apply_title_template()

    def _set_title_text(self, text: str) -> None:
        self._dat_view().annotations['plot_title'] = text
        self._update_plot_annotations(layout=False, title_only=True)
        self.canvas.draw_idle()
        self.dat_properties.syncFromOwner()

    def _apply_title_template(self) -> None:
        self._title_settings["manual_override"] = None
        if self.current_cube is None:
            self._set_title_text("")
            self._dat_status('title_template_status', "Load a DAT to preview this template.")
            return
        try:
            title = render_title(self.current_cube.source_path or "", self._title_settings["preset"])
            notice = "Preview: " + title
        except ValueError as exc:
            title, notice = "", str(exc)
        self._set_title_text(title)
        self._dat_status('title_template_status', notice)

    def _initialize_plot_annotations(self) -> None:
        cube = self.current_cube
        source = cube.source_path or id(cube)
        session = self.workspace.active
        if session is not None:
            if source != self._title_source or source != self._annotation_source:
                self._title_source = self._annotation_source = source
                self._show_title_settings()
            self._refresh_auto_signal_title()
            return
        if source != self._title_source:
            self._title_source = source
            self._title_settings = self._copy_title_settings(self._default_title_settings)
            self._show_title_settings()
            if self._title_settings['mode'] == 'template':
                self._apply_title_template()
            else:
                self._set_title_text(self._annotation_settings.value('plot_annotations/plot_title', '', type=str)
                                     if self._dat_view().fix_annotations else '')
        if source != self._annotation_source:
            self._annotation_source = source
            match = re.fullmatch(r'(.*?)\s*\(([^()]*)\)\s*', cube.gate_label)
            name, unit = match.groups() if match else (cube.gate_label, '')
            for field, text in (('y_axis_name', name), ('y_axis_unit', unit)):
                if self._dat_view().fix_annotations and self._annotation_settings.contains('plot_annotations/' + field):
                    text = self._annotation_settings.value('plot_annotations/' + field, '', type=str)
                self._dat_view().annotations[field] = text
        self._refresh_auto_signal_title()

    def _refresh_auto_signal_title(self) -> None:
        if not self._dat_view().signal_title_auto:
            return
        cube = self._base_cube or self.current_cube
        kind = cube.signal_kind if cube is not None else 'unknown'
        if kind == 'unknown' and cube is not None:
            kind = infer_signal_kind(cube.source_path)
        source = cube.signal_source_kind if cube is not None else 'unknown'
        if source == 'unknown' and cube is not None:
            source = infer_source_signal_kind(cube.source_path or '')
        session = self.workspace.active or self._unregistered_session
        derivative = session.active_graph.source_mode == 'processed' and self._processing_mode == 'energy_second_derivative'
        session.view.annotations['signal_title'] = automatic_signal_label(source, derivative, kind == 'energy_second_derivative')
        self.dat_properties.syncFromOwner()

    def _on_signal_auto_toggled(self, checked: bool) -> None:
        self._finish_annotation_edit(render=False)
        with self._edit_transaction('Automatic signal title', ('view.signal_title_auto', 'view.annotations.signal_title')):
            if self._dat_view().fix_annotations:
                self._annotation_settings.setValue("plot_annotations/signal_auto", checked)
                self._sync_annotation_settings()
            self._refresh_auto_signal_title()
            self._update_plot_annotations()

    def _save_plot_annotation(self, field: str, text: str) -> None:
        names = (TITLE_FIELDS if field == 'plot_title' else
                 ('view.annotations.signal_title', 'view.signal_title_auto') if field == 'signal_title' else
                 ('view.annotations.' + field,))
        self._begin_history_text('Edit ' + field.replace('_', ' '), names)
        self._dat_view().annotations[field] = text
        if field == "signal_title":
            # Avoid the auto-toggle handler doing a synchronous layout while typing.
            self._dat_view().signal_title_auto = False
            if self._dat_view().fix_annotations:
                self._annotation_settings.setValue("plot_annotations/signal_auto", False)
        if field == "plot_title":
            self._title_settings["manual_override"] = text
            if self._title_settings["mode"] == "template":
                self._dat_status('title_template_status', "Manual override for this DAT; Apply template to regenerate.")
        if self._dat_view().fix_annotations and not (
                field == "plot_title" and self._title_settings["mode"] == "template"):
            self._annotation_settings.setValue(f"plot_annotations/{field}", text)
        # Retire export bytes once per draft, retaining the last displayed page.
        # Enter/focus-out (or explicit capture) is the render boundary.
        first_edit = not self._annotation_edit_pending
        can_export = first_edit and self.plot_page.rendered_page is not None
        # The clipped transAxes title does not size the frame. Accumulate all
        # labels in this draft so a later title cannot hide an axis/signal edit.
        self._annotation_edit_requires_layout = (
            field != 'plot_title' or (not first_edit and self._annotation_edit_requires_layout))
        self._annotation_edit_pending = True
        self._plot_render_timer.stop()
        self._update_plot_annotations(layout=False, invalidate=first_edit,
                                      title_only=field == 'plot_title')
        if can_export:
            # A valid text draft can be committed by Export itself. The
            # controller prepares fresh bytes before writing, including when
            # the click's FocusOut has already committed the text.
            for command in ('export_svg', 'export_png', 'copy_svg', 'copy_png'):
                self.plot_page.set_command_availability(command, True)
        self._refresh_graph_commands()

    def _finish_annotation_edit(self, *, render: bool = True) -> None:
        if not self._annotation_edit_pending:
            return
        needs_layout = self._annotation_edit_requires_layout
        self._annotation_edit_pending = False
        self._annotation_edit_requires_layout = False
        self._annotation_edit_timer.stop()
        self._sync_annotation_settings()
        # Capture/save may finish without drawing; preserve any outstanding
        # geometry until a real layout succeeds, including across hidden views.
        self._annotation_layout_pending |= needs_layout
        if render:
            visible = self._outer_view == 'workspace'
            self._update_plot_annotations(
                layout=self._annotation_layout_pending and not visible, invalidate=False,
                title_only=not needs_layout)
            if not visible:
                self.canvas.draw_idle()
            self._schedule_plot_page_render(delay=0)

    def _on_fix_annotations_toggled(self, checked: bool) -> None:
        with self._edit_transaction('Fix annotations', ('view.fix_annotations',)):
            self._annotation_settings.setValue("plot_annotations/fixed", checked)
            if checked:
                self._annotation_settings.setValue("plot_annotations/signal_auto", self._dat_view().signal_title_auto)
                for field, text in self._dat_view().annotations.items():
                    if field == 'plot_title' and self._title_settings['mode'] == 'template':
                        continue
                    self._annotation_settings.setValue('plot_annotations/' + field, text)
                self._annotation_settings.setValue(
                    "plot_annotations/signal_title", self._dat_annotation('signal_title'))
            self._sync_annotation_settings()

    def _sync_annotation_settings(self) -> None:
        self._annotation_settings.sync()
        if self._annotation_settings.status() != QSettings.NoError:
            self.publish_status("Could not save plot annotations for the next session.")

    def _update_plot_annotations(self, *, layout: bool = True, invalidate: bool = True,
                                 title_only: bool = False) -> None:
        owner = self._plot_owner_token()
        page = self.plot_page.rendered_page
        cached_owner, cached_frame = getattr(self, '_annotation_auto_frame', (None, None))
        frame = (page.plot_size_in if page is not None and page.owner_token == owner
                 else cached_frame if cached_owner == owner else None)
        if invalidate:
            self._invalidate_plot_page(schedule=not self._annotation_edit_pending)
        if frame is not None:
            # Annotation typing retires export bytes, while the current Auto
            # frame remains visible until the replacement page is prepared.
            self._annotation_auto_frame = (self._plot_owner_token(), frame)
        if self._heat_ax is None:
            return
        self._heat_ax.title.set_text(self._dat_annotation('plot_title'))
        self._heat_ax.title.set_parse_math(False)
        if not title_only:
            name, unit = self._dat_annotation('y_axis_name'), self._dat_annotation('y_axis_unit')
            self._heat_ax.set_ylabel(f"{name} ({unit})" if unit else name, parse_math=False)
            # Styling resets colorbar label coordinates; only a full layout
            # restores their measured clearance. Title overlays leave them alone.
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
            self._view_layout_timer.timeout.connect(
                lambda: self.canvas.draw_idle() if self._outer_view == 'analysis' else None)
        if self._view_layout_timer.isActive() and self._view_layout_timer.remainingTime() == 0:
            # Reflow delivered after a synchronous render must not postpone an
            # already-due final layout for another debounce interval.
            return
        self._view_layout_timer.start(40)

    def _sync_display_zoom(self, zoom):
        # The cache records only the applied layout projection. The FormalPlotController
        # remains the zoom owner, including when graph pages are rebound.
        if self._analysis_display_zoom == zoom:
            return
        self._analysis_display_zoom = zoom
        self.analysis_navigation.refresh()
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
        zoom = self.plot_page.zoom_text
        if zoom != 'Fit':
            return int(zoom.rstrip('%')) / 100
        # Before the window is shown Qt supplies placeholder viewport geometry.
        # Hidden Analysis tabs in a shown window keep their own real viewport.
        if self.root_window is None or not self.root_window.isVisible():
            return 1.
        saved = (self.workspace.active or self._unregistered_session).presentation
        frame_width, frame_height = self._live_heatmap_frame_size(saved)
        header = max(.4, saved.text_pt/72*2 + saved.colorbar.height_in
                     + saved.colorbar.frame_gap_in + .06)
        if len(self._heat_colorbars) > 1:
            header += .2
        viewport = QSize(round(self.dat_canvas.viewportWidth), round(self.dat_canvas.viewportHeight))
        # Fit the Analysis footprint while keeping its smallest text readable
        # at 9 logical pixels. A short viewport uses the existing canvas scroll.
        # Derive the floor from saved point sizes; DPR is applied only later.
        readable_scale = 9 * 72 / (96 * min(saved.numeric_pt, saved.text_pt))
        # Never measure the grown canvas: that would feed zoom back into Fit.
        return max(.1, readable_scale, min((viewport.width()-12) / (96*(frame_width+1.35)),
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
        signal = (display_signal_label(self._dat_annotation('signal_title'), presentation.colorbar.show_unit)
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
        if self._outer_view == 'workspace':
            # Cached graph activation still rebuilds artists and restores exact
            # limits. Only hidden presentation geometry waits for Analysis.
            self._annotation_layout_pending = True
            return
        if (getattr(self, '_defer_dataset_layout', False)
                or getattr(self, '_laying_out_live_plots', False)):
            return
        if isinstance(_event, ResizeEvent):
            # Width/height reflow can report several intermediate viewports.
            # Direct science/style edits still keep their synchronous layout.
            self._schedule_view_layout()
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
        self.dat_canvas.set_content_minimum(minimum_width, minimum_height)
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
            signal_label = (display_signal_label(self._dat_annotation('signal_title'), colorbar_style.show_unit)
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
        self._heat_ax.set_title(self._dat_annotation('plot_title'), fontsize=presentation.text_pt,
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
        # Do not consume pending work on hidden, reentrant or empty requests.
        self._annotation_layout_pending = False

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
            self.ordinary_analysis.validate_editor()
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
            self.publish_status('')
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
        out_path, _ = FilePicker.getSaveFileName(self.root_window, "Export Current Gate CSV", str(default), "CSV Files (*.csv)")
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
        self.publish_status('')

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
        out_path, _ = FilePicker.getSaveFileName(self.root_window, "Export All Results CSV", str(default), "CSV Files (*.csv)")
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
        self.publish_status('')

    def _on_export_spectrum_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        gate_used, y = self._nearest_spectrum(self.current_gate)
        x = self.current_cube.energy
        try:
            _xr, y_analysis, mask = self._analysis_xy(
                x, y,
                smooth=self._analysis_controls().fit_smooth,
                smooth_window=int(self._analysis_controls().fit_smooth_window),
            )
        except Exception:
            y_analysis = np.asarray([], dtype=float)
            mask = self._fit_mask(x)
        default = self._output_folder() / f"{self.current_cube.title}_gate_{gate_used:.6g}_spectrum.csv"
        out_path, _ = FilePicker.getSaveFileName(self.root_window, "Export Spectrum CSV", str(default), "CSV Files (*.csv)")
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
        self.publish_status('')

    # =========================================================================
    #  Drag-and-drop
    # =========================================================================


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
            self._invalidate_plot_page(kind='view', sync=False)
        try:
            self._plot_cube_impl()
        except ValueError as exc:
            if self._project_installing:
                raise
            self._display_mapping_error(str(exc))

    def _plot_cube_impl(self) -> None:
        self.dat_canvas.retire_interaction()
        effective_x = self._dat_view().x_limits
        effective_y = self._dat_view().y_limits
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
            vmin, vmax = self._ordered_pair(float(self._dat_number('vmin')), float(self._dat_number('vmax')))
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
        xmin, xmax = self._ordered_pair(*effective_x)
        ymin, ymax = self._ordered_pair(*effective_y)
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
        if self._workbench_document() is not None and hasattr(self, 'analysis_workbench'):
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
                view_xlo = float(self._dat_number('xmin'))
                view_xhi = float(self._dat_number('xmax'))
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
        view_xmin, view_xmax = self._ordered_pair(float(self._dat_number('xmin')), float(self._dat_number('xmax')))
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

        if float(self.current_gate) != float(gate_used):
            self._retire_peak_menu()
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
        if self.toolbar.mode:
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
                self._show_peak_context_menu(float(event.xdata), getattr(event, 'guiEvent', None))
                return

        # ── Heatmap ───────────────────────────────────────────────────────────
        if self._heat_ax is None:
            if event.button == 3:
                self.dat_canvas.request_format_menu(getattr(event, 'guiEvent', None))
            return
        if self._lasso_mode and event.inaxes is self._heat_ax:
            return

        # Circle-delete drag start
        if (
            self._circle_delete_mode
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
            else:
                self.dat_canvas.request_format_menu(getattr(event, 'guiEvent', None))
            return

        if event.button == 3:
            self.dat_canvas.request_format_menu(getattr(event, 'guiEvent', None))
            return

        # Left-click on heatmap → select gate (ORIGINAL BEHAVIOR, UNCHANGED)
        if event.button != 1 or event.inaxes is not self._heat_ax or event.ydata is None:
            return
        if self._circle_delete_mode:
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
            and self._circle_delete_mode
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
                    self.publish_status('')
                else:
                    self.publish_status("No all-gate peaks inside circle.")
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
