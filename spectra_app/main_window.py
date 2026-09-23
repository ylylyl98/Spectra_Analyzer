from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.colors import Normalize
from matplotlib.path import Path as MplPath
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.widgets import LassoSelector
from scipy.signal import savgol_filter
from PySide6.QtCore import Qt, QObject, QEvent, QPointF, QSettings, QSignalBlocker
from PySide6.QtGui import QCursor, QDragEnterEvent, QDropEvent, QWheelEvent
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
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFrame,
    QSizePolicy,
)

from .dat_io import AXIS_OVERRIDE_ITEMS, DataCube, list_dat_files, list_dat_folders, load_dat_cube
from .custom_colormaps import resolve_colormap
from .plot_state import PlotStateStore, source_fingerprint
from .title_templates import (BUILTIN_PRESET, FIELDS, manual_settings, parse_angle_map,
                              render_title, validate_preset, validate_title_settings)
from .image_export import PLOT_FONT_SIZE, export_figure_png, style_plot_axis, style_heatmap_colorbar, position_heatmap_title
from .fitting import (
    FitResult,
    find_peak_indices,
    find_peaks_second_derivative,
    fit_multi_peak,
    _clamp_sg_window,
)


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spectra Analyzer (PySide6)")
        self.setMinimumSize(1440, 860)

        # ── data state ──────────────────────────────────────────────────────
        self.current_root = ""
        self.current_folder = ""
        self.current_cube: DataCube | None = None
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
        self._last_valid_color_limits: tuple[float, float] | None = None

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

        # ── CSV seeds for Lorentz Fit tab ────────────────────────────────────
        self._csv_seeds_map: dict[int, list[float]] | None = None
        self._csv_seeds_path: str = ""

        self._annotation_source = None
        self._annotation_settings = QSettings("SpectraAnalyzer", "SpectraAnalyzer")
        self._title_source = None
        self._title_presets, self._default_title_settings = self._load_title_preferences()
        self._title_settings = self._copy_title_settings(self._default_title_settings)
        self._plot_state_store = PlotStateStore()
        self._loaded_source_fingerprint: str | None = None
        self._build_ui()
        app = QApplication.instance()
        if not hasattr(app, "_page_wheel_filter"):
            app._page_wheel_filter = _PageWheelFilter(app)
            app.installEventFilter(app._page_wheel_filter)
        self.setAcceptDrops(True)

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
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

        left = self._build_left_panel()
        left.setMinimumWidth(490)
        plot_panel = self._build_plot_panel()

        split.addWidget(left)
        split.addWidget(plot_panel)
        split.setSizes([510, 930])

        outer = QHBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(split)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            "Select a root folder or drag-and-drop a .dat file to start."
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

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        cw = self.centralWidget()
        if cw and hasattr(self, "_dnd_overlay"):
            self._dnd_overlay.setGeometry(0, 0, cw.width(), cw.height())

    # ── expander helper ───────────────────────────────────────────────────────

    def _make_expander(self, title: str, content: QWidget, *, expanded: bool = True) -> QWidget:
        box = QFrame()
        box.setFrameShape(QFrame.StyledPanel)
        box.setFrameShadow(QFrame.Plain)
        box.setStyleSheet(
            "QFrame[frameShape='5'] {"  # StyledPanel = 5
            "  border: 1px solid #d0d0d0; border-radius: 5px;"
            "}"
        )
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(4)

        btn = QToolButton()
        btn.setText(f"  {title}")
        btn.setCheckable(True)
        btn.setChecked(expanded)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setStyleSheet(
            "QToolButton {"
            "  font-weight: 600; font-size: 11px; color: #333;"
            "  border: none; border-radius: 3px;"
            "  padding: 4px 4px; text-align: left;"
            "  background: #f0f4f8;"
            "}"
            "QToolButton:hover { background: #e3eaf2; }"
        )
        content.setVisible(expanded)

        def _toggle(on: bool) -> None:
            content.setVisible(on)
            btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

        btn.toggled.connect(_toggle)
        lay.addWidget(btn)
        lay.addWidget(content)
        return box

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
        lay.addWidget(spin)
        lay.addWidget(lock_box)
        lay.addStretch(1)
        return row

    # ── left panel ────────────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        panel = QWidget()
        panel.setMinimumWidth(470)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        # ── Data Source (expander, default expanded) ──────────────────────────
        src_content = QWidget()
        src_lay = QVBoxLayout(src_content)
        src_lay.setContentsMargins(2, 4, 2, 4)
        src_lay.setSpacing(6)

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
        src_lay.addLayout(root_row)

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
        src_lay.addLayout(folder_row)

        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setFrameShadow(QFrame.Sunken)
        sep1.setStyleSheet("color: #ddd;")
        src_lay.addWidget(sep1)

        # File list
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.file_list.setMinimumHeight(55)
        self.file_list.setMaximumHeight(80)
        self.file_list.setToolTip("Double-click or select and press Load to open a .dat file")
        self.file_list.setStyleSheet(
            "QListWidget { border: 1px solid #ddd; border-radius: 3px; font-size: 11px; }"
            "QListWidget::item:selected { background: #1976d2; color: white; }"
        )
        src_lay.addWidget(self.file_list)

        self.load_btn = QPushButton("Load Selected File")
        self.load_btn.setToolTip("Load the highlighted .dat file (double-click also works)")
        self.load_btn.setStyleSheet(_STYLE_PRIMARY)
        src_lay.addWidget(self.load_btn)

        lay.addWidget(self._make_expander("Data Source", src_content, expanded=True))

        # ── View Settings (expander, default expanded) ────────────────────────
        view_content = self._build_view_settings()
        lay.addWidget(self._make_expander("View Settings", view_content, expanded=True))

        # ── Spectrum Analysis ─────────────────────────────────────────────────
        analysis_box = QGroupBox("Spectrum Analysis")
        ao_lay = QVBoxLayout(analysis_box)
        ao_lay.setContentsMargins(6, 14, 6, 6)
        ao_lay.setSpacing(4)

        self._analysis_tabs = QTabWidget()
        self._analysis_tabs.addTab(self._build_find_peak_tab(), "Find Peaks")
        self._analysis_tabs.addTab(self._build_lorentz_fit_tab(), "Lorentz Fit")
        ao_lay.addWidget(self._analysis_tabs)

        # Analysis output label
        out_hdr = QLabel("Analysis Output")
        out_hdr.setStyleSheet("font-weight: 600; color: #444; font-size: 11px; margin-top: 2px;")
        ao_lay.addWidget(out_hdr)

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

        lay.addWidget(analysis_box, 1)

        # ── wire up non-tab signals ───────────────────────────────────────────
        self.browse_btn.clicked.connect(self._on_browse_root)
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        self.load_btn.clicked.connect(self._on_load_selected_file)
        self.file_list.itemDoubleClicked.connect(lambda _: self._on_load_selected_file())
        self.x_axis_override_combo.currentIndexChanged.connect(self._on_x_axis_override_changed)
        self.cmap_combo.currentTextChanged.connect(lambda _: self._plot_cube())
        self.lw_min_spin.valueChanged.connect(lambda _: self._update_fit_param_unit_labels())
        self.lw_max_spin.valueChanged.connect(lambda _: self._update_fit_param_unit_labels())
        self.peak_move_limit_spin.valueChanged.connect(lambda _: self._update_fit_param_unit_labels())
        for spin in (self.xmin_spin, self.xmax_spin, self.ymin_spin,
                     self.ymax_spin, self.vmin_spin, self.vmax_spin):
            spin.valueChanged.connect(
                lambda _value, changed_spin=spin: self._on_view_limit_changed(changed_spin)
            )
        self.apply_view_btn.clicked.connect(self._plot_cube)
        self.auto_view_btn.clicked.connect(self._on_auto_view)

        scroll.setWidget(panel)
        return scroll

    # ── view settings content (used inside expander) ──────────────────────────

    def _build_view_settings(self) -> QWidget:
        """Build the View Settings panel content (X-axis, colormap, bounds, gate)."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 4)
        lay.setSpacing(6)

        # ── X-axis override + colormap (2-column form) ────────────────────────
        self.x_axis_override_combo = QComboBox()
        for value, label in AXIS_OVERRIDE_ITEMS:
            self.x_axis_override_combo.addItem(label, value)
        self.x_axis_override_combo.setToolTip(
            "Override the auto-detected x-axis type. Use 'Auto Detect' unless the detection is wrong."
        )

        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems([
            "turbo", "viridis", "plasma", "inferno", "magma", "cividis", "RdBu_r",
            "RdBu_r_p0p45", "RdBu_r_p0p6",
        ])
        self.cmap_combo.setCurrentText("RdBu_r")
        self.cmap_combo.setToolTip("Colormap used for the intensity heatmap")

        top_form = QFormLayout()
        top_form.setContentsMargins(0, 0, 0, 0)
        top_form.setSpacing(4)
        top_form.setHorizontalSpacing(8)
        top_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        top_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top_form.addRow("X-Axis:", self.x_axis_override_combo)
        top_form.addRow("Colormap:", self.cmap_combo)
        self.fix_annotations_chk = QCheckBox("Fix annotations")
        self.fix_annotations_chk.setChecked(
            self._annotation_settings.value("plot_annotations/fixed", True, type=bool))
        self.fix_annotations_chk.setToolTip(
            "Keep Y/Z annotations for new files. In Manual title mode, also keep the title. "
            "Templates always generate a fresh title for each new file; edits override only that DAT. "
            "Previously opened files always recall their own saved plot settings.")
        top_form.addRow("", self.fix_annotations_chk)
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
        self._build_title_controls(top_form)
        top_form.addRow("Y-axis name:", self.y_axis_name_edit)
        top_form.addRow("Y-axis unit:", self.y_axis_unit_edit)
        self.signal_title_combo = QComboBox()
        self.signal_title_combo.setEditable(True)
        self.signal_title_combo.setInsertPolicy(QComboBox.NoInsert)
        self.signal_title_combo.addItems(["ΔR/R (a. u.)", "d²(ΔR/R)/dE² (a. u.)", "PL (a. u.)"])
        self.signal_title_combo.setToolTip(
            "Colorbar annotation only; choosing a derivative label does not process the data.")
        if self.fix_annotations_chk.isChecked():
            self.signal_title_combo.setCurrentText(self._annotation_settings.value(
                "plot_annotations/signal_title", "ΔR/R (a. u.)", type=str))
        self.signal_title_combo.currentTextChanged.connect(
            lambda text: self._save_plot_annotation("signal_title", text))
        top_form.addRow("Z signal title:", self.signal_title_combo)
        self.fix_annotations_chk.toggled.connect(self._on_fix_annotations_toggled)
        lay.addLayout(top_form)

        # Axis status label
        self.axis_status_label = QLabel("X-axis: no file loaded.")
        self.axis_status_label.setWordWrap(True)
        self.axis_status_label.setStyleSheet(
            "color: #607d8b; font-size: 10px;"
            "background: #f0f4f8; border-radius: 3px; padding: 3px 5px;"
        )
        lay.addWidget(self.axis_status_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color: #e0e0e0;")
        lay.addWidget(sep)

        # ── View bounds grid (Min / Max per axis) ─────────────────────────────
        self.xmin_spin = QDoubleSpinBox()
        self.xmax_spin = QDoubleSpinBox()
        self.ymin_spin = QDoubleSpinBox()
        self.ymax_spin = QDoubleSpinBox()
        self.vmin_spin = QDoubleSpinBox()
        self.vmax_spin = QDoubleSpinBox()
        for sp in (self.xmin_spin, self.xmax_spin, self.ymin_spin,
                   self.ymax_spin, self.vmin_spin, self.vmax_spin):
            sp.setDecimals(4)
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
        for lock_box in self._view_lock_controls.values():
            lock_box.toggled.connect(
                lambda checked, changed_lock=lock_box: self._on_view_lock_toggled(changed_lock, checked)
            )

        bounds_box = QGroupBox("Heatmap Bounds")
        bounds_grid = QGridLayout(bounds_box)
        bounds_grid.setContentsMargins(8, 14, 8, 6)
        bounds_grid.setSpacing(4)
        bounds_grid.setHorizontalSpacing(5)

        # Header row
        hdr_min = QLabel("Min")
        hdr_max = QLabel("Max")
        for lbl in (hdr_min, hdr_max):
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("font-weight: 600; color: #666; font-size: 10px;")
        bounds_grid.addWidget(hdr_min, 0, 1, 1, 2, Qt.AlignCenter)
        bounds_grid.addWidget(hdr_max, 0, 3, 1, 2, Qt.AlignCenter)

        # Row labels
        for row_idx, txt, tip in (
            (1, "Energy (X)", "Horizontal axis range shown on the heatmap"),
            (2, "Gate (Y)", "Vertical axis range shown on the heatmap"),
            (3, "Intensity", "Color (value) range for the heatmap colormap"),
        ):
            lbl = QLabel(txt)
            lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            lbl.setToolTip(tip)
            lbl.setStyleSheet("font-size: 10px; color: #444;")
            bounds_grid.addWidget(lbl, row_idx, 0)

        bounds_grid.addWidget(self.xmin_spin, 1, 1)
        bounds_grid.addWidget(self.xmin_lock, 1, 2)
        bounds_grid.addWidget(self.xmax_spin, 1, 3)
        bounds_grid.addWidget(self.xmax_lock, 1, 4)

        bounds_grid.addWidget(self.ymin_spin, 2, 1)
        bounds_grid.addWidget(self.ymin_lock, 2, 2)
        bounds_grid.addWidget(self.ymax_spin, 2, 3)
        bounds_grid.addWidget(self.ymax_lock, 2, 4)

        bounds_grid.addWidget(self.vmin_spin, 3, 1)
        bounds_grid.addWidget(self.vmin_lock, 3, 2)
        bounds_grid.addWidget(self.vmax_spin, 3, 3)
        bounds_grid.addWidget(self.vmax_lock, 3, 4)

        bounds_grid.setColumnStretch(1, 1)
        bounds_grid.setColumnStretch(3, 1)
        bounds_grid.setColumnMinimumWidth(0, 62)

        # View action buttons
        btn_row = QHBoxLayout()
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
        bounds_grid.addLayout(btn_row, 4, 0, 1, 5)
        lay.addWidget(bounds_box)

        return w

    # ── Find Peaks tab ────────────────────────────────────────────────────────

    def _build_find_peak_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(5)

        # ── Detection Mode ────────────────────────────────────────────────────
        mode_box = QGroupBox("Detection Mode")
        mode_l = QHBoxLayout(mode_box)
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
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.find_btn = QPushButton("Find Peaks (This Gate)")
        self.batch_find_btn = QPushButton("Find Peaks — All Gates")
        self.find_btn.setToolTip(
            "Detect peaks on the currently displayed spectrum trace only.\n"
            "Result appears as markers on the spectrum plot."
        )
        self.batch_find_btn.setToolTip(
            "Run peak detection on every gate and overlay results on the heatmap.\n"
            "Use this before 'Batch Fit All Gates' in the Lorentz Fit tab."
        )
        self.find_btn.setStyleSheet(_STYLE_PRIMARY)
        self.batch_find_btn.setStyleSheet(_STYLE_PRIMARY)
        action_row.addWidget(self.find_btn, 1)
        action_row.addWidget(self.batch_find_btn, 1)
        lay.addLayout(action_row)

        # ── Heatmap Tools ─────────────────────────────────────────────────────
        heatmap_box = QGroupBox("Heatmap Peak Editing")
        heatmap_l = QVBoxLayout(heatmap_box)
        heatmap_l.setContentsMargins(8, 14, 8, 8)
        heatmap_l.setSpacing(5)

        # Lasso + circle toggles row
        mode_btn_row = QHBoxLayout()
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
        lasso_action_row = QHBoxLayout()
        lasso_action_row.setSpacing(6)
        self.delete_lasso_btn = QPushButton("Delete Lasso Selection")
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
        global_row = QHBoxLayout()
        global_row.setSpacing(6)
        self.clear_batch_peaks_btn = QPushButton("Clear All Heatmap Peaks")
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

        del_row = QHBoxLayout()
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

    # ── Lorentz Fit tab ───────────────────────────────────────────────────────

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
        xmax_l.addWidget(self.fit_full_btn)

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
        self.fit_model_combo.addItems(["Lorentzian", "Gaussian"])
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
        self.lw_min_spin.setToolTip(
            "Minimum allowed FWHM linewidth in data points.\n"
            "The equivalent size in the current analysis-axis units is shown to the right."
        )
        self.lw_min_value_label = QLabel(self._format_pts_conversion(int(self.lw_min_spin.value())))
        self.lw_min_value_label.setStyleSheet("color: #607d8b; font-size: 10px;")

        self.lw_max_spin = QSpinBox()
        self.lw_max_spin.setRange(0, 10000)
        self.lw_max_spin.setSingleStep(1)
        self.lw_max_spin.setValue(50)
        self.lw_max_spin.setSuffix(" pt")
        self.lw_max_value_label = QLabel(self._format_pts_conversion(int(self.lw_max_spin.value())))
        self.lw_max_value_label.setStyleSheet("color: #607d8b; font-size: 10px;")
        self.lw_max_spin.setToolTip(
            "Maximum allowed FWHM linewidth. 0 = unconstrained (uses 2× fit range)."
        )

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
        params_form.addRow("Min Linewidth:", self._hrow([self.lw_min_spin, self.lw_min_value_label]))
        params_form.addRow("Max Linewidth:", self._hrow([self.lw_max_spin, self.lw_max_value_label]))
        params_form.addRow("Max Peak Shift:", self._hrow([self.peak_move_limit_spin, self.peak_move_value_label]))
        params_form.addRow("Max Iterations:", self.maxfev_spin)
        lay.addWidget(params_box)

        # ── Primary fit actions ────────────────────────────────────────────────
        fit_action_row = QHBoxLayout()
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
        lay.addLayout(fit_action_row)

        # One context-sensitive current-gate action: it detects first when no
        # Find Peaks seeds exist, and fits directly once seeds are ready.
        self.detect_then_fit_btn = self.fit_btn
        self.fit_readiness_label = QLabel("Fit preparation: load data first.")
        self.fit_readiness_label.setWordWrap(True)
        self.fit_readiness_label.setStyleSheet("color: #607d8b; font-size: 10px;")
        lay.addWidget(self.fit_readiness_label)

        # ── Secondary actions ──────────────────────────────────────────────────
        secondary_row = QHBoxLayout()
        secondary_row.setSpacing(6)
        self.clear_fit_btn = QPushButton("Clear Fit")
        self.export_current_fit_btn = QPushButton("Export Current Gate CSV")
        self.export_all_fit_btn = QPushButton("Export All Results CSV")
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
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)

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
        self.export_png_btn = QPushButton("Export PNG")
        self.export_png_btn.setToolTip(
            "Plot area: 4 inches wide by 3 inches high; 300 DPI; 12 pt fonts. Saves directly beside the DAT with the next export number. Title, axes labels and colorbar are outside this area."
        )
        self.export_png_btn.setStyleSheet(_STYLE_MUTED)
        self.export_png_btn.setEnabled(False)
        self.export_png_btn.clicked.connect(self._on_export_png)
        toolbar_lay.addWidget(self.export_png_btn)

        lay.addWidget(toolbar_row)
        lay.addWidget(gate_bar)
        lay.addWidget(self.canvas, 1)
        return panel

    # =========================================================================
    #  Helper utilities
    # =========================================================================

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

    def _sync_batch_peak_coords(self) -> None:
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
        self.batch_peak_map = clean_map
        self.batch_peak_records = records
        valid_ids = set(records)
        self._lasso_selected_peak_ids = [pid for pid in self._lasso_selected_peak_ids if pid in valid_ids]
        self._update_lasso_actions()
        if px:
            self.batch_peak_coords = (np.asarray(px, dtype=float), np.asarray(py, dtype=float))
        else:
            self.batch_peak_coords = None

    def _sync_batch_fit_coords(self) -> None:
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
        self.lw_max_value_label.setText(self._format_pts_conversion(int(self.lw_max_spin.value())))
        self.peak_move_value_label.setText(self._format_pts_conversion(int(self.peak_move_limit_spin.value())))

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
        d2y = savgol_filter(yr, window_length=win, polyorder=polyorder, deriv=2)
        return xr, np.asarray(d2y, dtype=float)

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
        if self.current_cube is None or self.current_cube.gate.size == 0:
            self.plot_gate_spin.blockSignals(True)
            self.plot_gate_spin.set_allowed_values(np.asarray([], dtype=float))
            self.plot_gate_spin.blockSignals(False)
            self.plot_gate_index_spin.blockSignals(True)
            self.plot_gate_index_spin.setRange(1, 1)
            self.plot_gate_index_spin.setValue(1)
            self.plot_gate_index_spin.blockSignals(False)
            return
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
        self.current_gate = float(self.current_cube.gate[gate_idx])
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
        self.fit_readiness_label.setText(f"Fit preparation: {message}")
        self.fit_btn.setText("Detect Then Fit Current Gate" if can_detect else "Fit Current Gate")
        self.fit_btn.setEnabled(bool(can_detect or in_range.size))
        self._refresh_export_actions()

    def _on_fit_or_detect_current_gate(self) -> None:
        _seeds, _in_range, _message, can_detect = self._fit_seed_status()
        if can_detect:
            self._on_detect_then_fit_current_gate()
        else:
            self._on_fit()

    def _on_detect_then_fit_current_gate(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        if self._seeds_from_csv_radio.isChecked():
            self._show_error("Detect Then Fit is available only for the Find Peaks source.")
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
        folder = QFileDialog.getExistingDirectory(self, "Choose Root Folder", self.current_root or ".")
        if not folder:
            return
        self.current_root = folder
        self.root_edit.setText(folder)
        folders = list_dat_folders(folder)
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
        folder = self.folder_combo.currentData()
        if not folder:
            return
        self.current_folder = str(folder)
        files = list_dat_files(folder)
        self.file_list.clear()
        self.file_list.addItems(files)
        if files:
            self.file_list.setCurrentRow(0)
            self.statusBar().showMessage(f"Folder ready: {len(files)} dat files.")
        else:
            self.statusBar().showMessage("No dat files in selected process folder.")

    def _on_load_selected_file(self) -> None:
        item = self.file_list.currentItem()
        if item is None:
            self._show_error("Select one DAT file first.")
            return
        path = Path(self.current_folder) / item.text().strip()
        self._load_dat_file_direct(path)

    def _load_dat_file_direct(self, path: Path) -> None:
        """Load a .dat file directly (called from file list or drag-and-drop)."""
        try:
            fingerprint = source_fingerprint(path)
            save_notice = self._save_current_plot_state()
            saved_plot, recall_notice = self._plot_state_store.load(path, fingerprint)
            if saved_plot is not None and self.cmap_combo.findText(saved_plot["colormap"]) < 0:
                saved_plot = None
                recall_notice = "Saved plot settings ignored: the colormap is unavailable."
            override = saved_plot["axis_override"] if saved_plot else self._selected_x_axis_override()
            cube = load_dat_cube(path, x_axis_override=override)
            if source_fingerprint(path) != fingerprint:
                raise ValueError("DAT content changed while loading. Please load it again.")
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.current_cube = cube
        self._loaded_source_fingerprint = fingerprint
        self._annotation_source = None
        self._title_source = None
        self._last_valid_color_limits = None
        self.current_dat_path = path
        self.peak_indices = np.asarray([], dtype=int)
        self._peak_gate_idx = None
        self.peak_label_lookup = {}
        self.batch_peak_map = {}
        self.batch_peak_coords = None
        self.batch_peak_records = []
        self.fit_result = None
        self._fit_gate_idx = None
        self.batch_fit_map = {}
        self.batch_fit_coords = None
        self.batch_fit_result_map = {}
        self._batch_fit_export_rows = []
        self._has_batch_fit_results = False
        self._reset_csv_seed_source()
        self._lasso_selected_peak_ids = []
        self._clear_lasso_selection_visual()
        self._update_lasso_actions()
        self._cached_d2 = None
        self._refresh_peak_delete_controls()
        gate = float(np.nanmedian(cube.gate))
        self.current_gate = gate
        self._sync_gate_controls()
        # Update folder for CSV dialog defaults
        self.current_folder = str(path.parent)
        if saved_plot is None:
            with QSignalBlocker(self.fix_annotations_chk):
                self.fix_annotations_chk.setChecked(
                    self._annotation_settings.value("plot_annotations/fixed", True, type=bool))
        self._set_view_limits_to_auto()
        self._set_fit_range_to_full()
        if saved_plot is not None:
            self._restore_plot_controls(saved_plot)
        self._refresh_axis_status()
        self._refresh_fit_range_summary()
        self._refresh_fit_readiness()
        self._update_fit_param_unit_labels()
        self._plot_cube()
        if saved_plot is not None:
            self._restore_effective_plot_limits(saved_plot)
        self.analysis_text.setPlainText(
            f"Loaded: {path.name}\n"
            f"Detected x-axis: {cube.x_original_label}\n"
            f"Analysis axis: {cube.energy_label}\n"
            "Use Find Peaks tab to detect peaks, then Lorentz Fit tab to fit.\n"
            "Drag-and-drop a .dat or .csv file onto the window as an alternative to browse."
        )
        notice = " ".join(part for part in (save_notice, recall_notice) if part)
        suffix = f" — {notice}" if notice else (" — saved plot restored" if saved_plot else "")
        self.statusBar().showMessage(f"Loaded: {path.name}{suffix}")

    def _save_current_plot_state(self) -> str:
        if (self.current_dat_path is None or self.current_cube is None
                or self._loaded_source_fingerprint is None
                or self._heat_ax is None or self._heat_mesh is None):
            return ""
        bounds = ("xmin", "xmax", "ymin", "ymax", "vmin", "vmax")
        plot = {
            "axis_override": self.current_cube.x_override,
            "colormap": self.cmap_combo.currentText(),
            "annotations": {**{name: edit.text() for name, edit in self._annotation_edits.items()},
                            "signal_title": self.signal_title_combo.currentText()},
            "fix_annotations": self.fix_annotations_chk.isChecked(),
            "title_settings": self._copy_title_settings(self._title_settings),
            "bounds": {name: getattr(self, f"{name}_spin").value() for name in bounds},
            "locks": {name: getattr(self, f"{name}_lock").isChecked() for name in bounds},
            "x_limits": list(map(float, self._heat_ax.get_xlim())),
            "y_limits": list(map(float, self._heat_ax.get_ylim())),
            "color_limits": list(map(float, self._heat_mesh.get_clim())),
            "gate": float(self.current_gate),
        }
        try:
            # Keep the fingerprint of the loaded data, even if its disk file changed.
            self._plot_state_store.save(self.current_dat_path, self._loaded_source_fingerprint, plot)
        except (OSError, ValueError, TypeError) as exc:
            return f"Could not save plot settings: {exc}"
        return ""

    def _restore_plot_controls(self, plot: dict) -> None:
        self._title_settings = self._copy_title_settings(plot["title_settings"])
        self._title_source = self.current_cube.source_path or id(self.current_cube)
        self._show_title_settings()
        controls = [(self.x_axis_override_combo, "index", self.x_axis_override_combo.findData(plot["axis_override"])),
                    (self.cmap_combo, "text", plot["colormap"]),
                    (self.signal_title_combo, "text", plot["annotations"]["signal_title"]),
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
        self._annotation_source = self.current_cube.source_path or id(self.current_cube)
        gate_idx = int(np.argmin(np.abs(self.current_cube.gate - plot["gate"])))
        self.current_gate = float(self.current_cube.gate[gate_idx])
        self._sync_gate_controls()

    def _restore_effective_plot_limits(self, plot: dict) -> None:
        # The spin boxes round to four decimals; preserve the actual displayed view.
        previous_syncing = self._view_syncing
        self._view_syncing = True
        try:
            self._heat_ax.set_xlim(plot["x_limits"])
            self._heat_ax.set_ylim(plot["y_limits"])
            self._heat_mesh.set_clim(*plot["color_limits"])
            self._last_valid_color_limits = tuple(plot["color_limits"])
            style_heatmap_colorbar(self._heat_colorbar, self.signal_title_combo.currentText())
            self._layout_live_plots()
        finally:
            self._view_syncing = previous_syncing
        self.canvas.draw_idle()

    def closeEvent(self, event) -> None:
        notice = self._save_current_plot_state()
        if notice:
            self.statusBar().showMessage(notice)
        super().closeEvent(event)

    def _on_x_axis_override_changed(self) -> None:
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
        self.current_cube = cube
        self._loaded_source_fingerprint = fingerprint
        self._annotation_source = None
        self._last_valid_color_limits = None
        self.peak_indices = np.asarray([], dtype=int)
        self._peak_gate_idx = None
        self.peak_label_lookup = {}
        self.batch_peak_map = {}
        self.batch_peak_coords = None
        self.batch_peak_records = []
        self.fit_result = None
        self._fit_gate_idx = None
        self.batch_fit_map = {}
        self.batch_fit_coords = None
        self.batch_fit_result_map = {}
        self._batch_fit_export_rows = []
        self._has_batch_fit_results = False
        self._reset_csv_seed_source()
        self._cached_d2 = None
        self._lasso_selected_peak_ids = []
        self._clear_lasso_selection_visual()
        self._update_lasso_actions()
        if cube.gate.size:
            gate_idx = int(np.argmin(np.abs(cube.gate - float(gate))))
            self.current_gate = float(cube.gate[gate_idx])
        self._sync_gate_controls()
        self._refresh_peak_delete_controls()
        self._set_view_limits_to_auto()
        self._set_fit_range_to_full()
        self._refresh_axis_status()
        self._refresh_fit_range_summary()
        self._refresh_fit_readiness()
        self._update_fit_param_unit_labels()
        self._plot_cube()

    # =========================================================================
    #  View / cursor controls
    # =========================================================================

    def _on_auto_view(self) -> None:
        self._set_view_limits_to_auto()
        self._plot_cube()

    def _on_view_limit_changed(self, spin: QDoubleSpinBox) -> None:
        if self._view_syncing:
            return
        xy_spins = {
            self.xmin_spin, self.xmax_spin, self.ymin_spin, self.ymax_spin,
        }
        if spin in xy_spins:
            self._update_auto_color_limits_from_visible_view(use_spin_values=True)
        else:
            self._last_valid_color_limits = self._ordered_pair(
                float(self.vmin_spin.value()), float(self.vmax_spin.value())
            )
        self._plot_cube()

    def _on_view_lock_toggled(self, lock_box: QCheckBox, checked: bool) -> None:
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

    def _on_plot_y_limits_changed(self, axes: Any) -> None:
        if not self._view_callbacks_ready or self._view_syncing or self.current_cube is None:
            return
        self._view_syncing = True
        try:
            ymin, ymax = self._ordered_pair(*self._heat_ax.get_ylim())
            for spin, value in ((self.ymin_spin, ymin), (self.ymax_spin, ymax)):
                spin.blockSignals(True)
                spin.setValue(float(value))
                spin.blockSignals(False)
            self._update_auto_color_limits_from_visible_view(ylim=(ymin, ymax))
            self._layout_live_plots()
        finally:
            self._view_syncing = False

    def _visible_color_values(
        self,
        *,
        use_spin_values: bool = False,
        xlim: tuple[float, float] | None = None,
        ylim: tuple[float, float] | None = None,
    ) -> np.ndarray:
        if self.current_cube is None:
            return np.asarray([], dtype=float)
        cube = self.current_cube
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
        return values[np.isfinite(values)]

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
        values = self._visible_color_values(use_spin_values=use_spin_values, xlim=xlim, ylim=ylim)
        low_locked = self._view_lock_controls.get(self.vmin_spin)
        high_locked = self._view_lock_controls.get(self.vmax_spin)
        is_low_locked = bool(low_locked is not None and low_locked.isChecked())
        is_high_locked = bool(high_locked is not None and high_locked.isChecked())

        if values.size:
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
                low, high = low - pad, high + pad
        elif is_low_locked and not is_high_locked:
            if high <= low:
                high = low + self._color_gap(low)
        elif not is_low_locked and is_high_locked:
            if low >= high:
                low = high - self._color_gap(high)

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
        self._apply_color_limits_to_mesh()

    def _apply_color_limits_to_mesh(self) -> None:
        if self._heat_mesh is None:
            return
        vmin, vmax = self._ordered_pair(
            float(self.vmin_spin.value()), float(self.vmax_spin.value())
        )
        self._heat_mesh.set_clim(vmin=float(vmin), vmax=float(vmax))
        if self._heat_colorbar is not None:
            self._heat_colorbar.update_normal(self._heat_mesh)
            style_heatmap_colorbar(self._heat_colorbar, self.signal_title_combo.currentText())
            position_heatmap_title(self._heat_ax, self._heat_colorbar.ax, self._heat_ax.title)
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
        self._update_spectrum_only()

    def _on_fit_range_changed(self, _value: float) -> None:
        self._refresh_fit_range_summary()
        self._refresh_fit_readiness()
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
        cube = self.current_cube
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
            self.peak_label_lookup.pop((gate_idx, nearest_idx), None)
            self.peak_indices = np.asarray(
                self.peak_indices[self.peak_indices != nearest_idx], dtype=int
            )
            self._sync_current_trace_peaks_to_batch_map()
            self.fit_result = None
            self._refresh_peak_delete_controls()
            gate_used, y = self._nearest_spectrum(self.current_gate)
            self._update_spectrum_only()
            self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")
        elif chosen_data == "set_label":
            new_label, ok = QInputDialog.getInt(
                self, "Set Peak Label",
                "Label index (1 = first peak, 2 = second, …):",
                value=current_label, min=1, max=99,
            )
            if ok:
                self.peak_label_lookup[(gate_idx, nearest_idx)] = new_label
                gate_used, y = self._nearest_spectrum(self.current_gate)
                self._update_spectrum_only()
                self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")

    def _on_find_peaks(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
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
        self.fit_result = None
        self._refresh_peak_delete_controls()
        self.current_gate = gate_used
        self._sync_gate_controls()
        self._update_spectrum_only()
        self._update_peak_text(gate_used, x, y, title_prefix="Auto-detected")

    def _on_batch_find_peaks_all_gates(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
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

        peak_map: dict[int, np.ndarray] = {}
        batch_kind_lookup: dict[tuple[int, int], bool] = {}
        ok_count = 0
        peak_total = 0

        for gi, gate in enumerate(cube.gate):
            y = np.asarray(cube.z[gi, :], dtype=float)
            yr = np.asarray(y[mask], dtype=float)

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
                    if self.peak_smooth_chk.isChecked():
                        win = _clamp_sg_window(yr.size, int(self.peak_smooth_win_spin.value()))
                        if win >= 3 and yr.size > win:
                            yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")
                    idx_local, kinds_local = find_peak_indices(
                        xr, yr,
                        mode=peak_mode,  # type: ignore[arg-type]
                        prominence_frac=prom, min_distance=min_dist,
                        max_peaks=max_peaks_o, max_dips=max_dips_o,
                    )
            except Exception:
                continue

            if idx_local.size == 0:
                continue
            ok_count += 1
            peak_total += int(idx_local.size)
            full_idx = np.asarray(mask_idx[idx_local], dtype=int)
            peak_map[gi] = full_idx
            for fi, ki in zip(full_idx, kinds_local):
                batch_kind_lookup[(gi, int(fi))] = bool(ki)

            if gi % 25 == 0:
                self.statusBar().showMessage(f"Batch peak finding… gate {gi+1}/{cube.gate.size}")
                QApplication.processEvents()

        self.batch_peak_map = peak_map
        self.batch_peak_kind_lookup = batch_kind_lookup
        current_gate_idx = self._current_gate_index()
        self.peak_indices = np.asarray(peak_map.get(current_gate_idx, []), dtype=int)
        self.peak_kind_lookup = {
            int(ei): batch_kind_lookup.get((current_gate_idx, int(ei)), True)
            for ei in self.peak_indices
        }
        self._peak_gate_idx = current_gate_idx
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

    def _on_save_peaks_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        cube = self.current_cube
        self._sync_current_trace_peaks_to_batch_map()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = Path(self.current_folder) / f"{cube.title}_peaks_{stamp}.csv"
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
        if self.current_cube is None or self.peak_indices.size == 0:
            return
        if self._peak_gate_idx != self._current_gate_index():
            return
        n = int(self.peak_delete_spin.value())
        if n < 1 or n > int(self.peak_indices.size):
            return
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
        if self.current_cube is None:
            return
        gate_idx = self._current_gate_index()
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
        self.batch_peak_map = {}
        self.batch_peak_kind_lookup = {}
        self.peak_label_lookup = {}
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
        self.fit_result = None
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

    def _on_fit(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
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
        lw_max = lw_max_v if lw_max_v > lw_min else 0.0
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
        try:
            res = fit_multi_peak(
                xr, yr,
                centers_hint=centers_in,
                model=self.fit_model_combo.currentText(),  # type: ignore[arg-type]
                mode=fit_mode,  # type: ignore[arg-type]
                linewidth_min=lw_min,
                linewidth_max=lw_max,
                peak_move_limit=peak_move,
                maxfev=maxfev,
                kinds=fit_kinds,
            )
        except Exception as exc:
            self._show_error(f"Fit failed: {exc}")
            return

        self.fit_result = res
        self._fit_gate_idx = self._current_gate_index()
        self.batch_fit_map[self._fit_gate_idx] = np.asarray(res.centers, dtype=float)
        self.batch_fit_result_map[self._fit_gate_idx] = res
        fit_row = self._fit_result_to_export_row(
            float(gate_used), res.model, self.current_cube.energy_label, res, status="ok"
        )
        self._upsert_fit_export_row(fit_row)
        self._refresh_export_actions()
        self._sync_batch_fit_coords()
        self.current_gate = gate_used
        self._update_spectrum_only()
        lines = [
            f"{res.model} fit @ gate {gate_used:.6g}",
            f"Baseline: {res.baseline:.6g}",
            f"R^2: {res.r_squared:.6g}",
            f"RMSE: {res.rmse:.6g}",
        ]
        for i in range(res.centers.size):
            lines.append(f"  {i+1}. cen={res.centers[i]:.6g}, amp={res.amplitudes[i]:.6g}, wid={res.widths[i]:.6g}")
        self.analysis_text.setPlainText("\n".join(lines))

    def _on_clear_fit(self) -> None:
        self.fit_result = None
        self._fit_gate_idx = None
        self.batch_fit_map = {}
        self.batch_fit_coords = None
        self.batch_fit_result_map = {}
        self._batch_fit_export_rows = []
        self._has_batch_fit_results = False
        self._refresh_export_actions()
        self._update_spectrum_only()

    def _on_batch_fit_all_gates(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
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
                "or load a seeds CSV in the Lorentz Fit tab."
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
        lw_max = lw_max_v if lw_max_v > lw_min else 0.0
        peak_move = float(self._pts_to_axis_units(int(self.peak_move_limit_spin.value())) or 0.0)
        maxfev = int(self.maxfev_spin.value())
        xlo, xhi = self._fit_range_bounds()

        rows: list[dict[str, str]] = []
        ok_count = 0
        fail_count = 0
        fit_map: dict[int, np.ndarray] = {}
        fit_result_map: dict[int, FitResult] = {}

        for gi, gate in enumerate(cube.gate):
            y = np.asarray(cube.z[gi, :], dtype=float)
            yr = np.asarray(y[mask], dtype=float)
            if self.fit_smooth_chk.isChecked():
                win = _clamp_sg_window(yr.size, int(self.fit_smooth_win_spin.value()))
                if win >= 3 and yr.size > win:
                    yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")

            centers = self._get_seeds_for_gate_idx(gi)
            centers_in = centers[(centers >= xlo) & (centers <= xhi)] if centers.size else centers
            if centers_in.size == 0:
                fail_count += 1
                rows.append(
                    self._fit_result_to_export_row(
                        float(gate), model, cube.energy_label, None, status="no_seeds"
                    )
                )
                continue

            batch_kinds: np.ndarray | None = None
            if use_kinds and self.current_cube is not None:
                x_arr = cube.energy
                batch_kinds = np.asarray([
                    self.batch_peak_kind_lookup.get(
                        (gi, int(np.argmin(np.abs(x_arr - c)))), True
                    )
                    for c in centers_in
                ], dtype=bool)
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
            except Exception as exc:
                fail_count += 1
                rows.append(
                    self._fit_result_to_export_row(
                        float(gate), model, cube.energy_label, None, status=f"fit_failed:{exc}"
                    )
                )
                continue

            ok_count += 1
            fit_map[gi] = np.asarray(res.centers, dtype=float)
            fit_result_map[gi] = res
            rows.append(
                self._fit_result_to_export_row(float(gate), model, cube.energy_label, res, status="ok")
            )

            if gi % 20 == 0:
                self.statusBar().showMessage(f"Batch fitting… gate {gi+1}/{cube.gate.size}")
                QApplication.processEvents()

        self.batch_fit_map = fit_map
        self.batch_fit_result_map = fit_result_map
        self._batch_fit_export_rows = [dict(row) for row in rows]
        self._has_batch_fit_results = True
        self._sync_batch_fit_coords()
        self._refresh_export_actions()
        if self.current_cube.gate.size:
            current_gate_idx = self._current_gate_index()
            self.fit_result = self.batch_fit_result_map.get(current_gate_idx)
            self._fit_gate_idx = current_gate_idx if self.fit_result is not None else None
        self.statusBar().showMessage(f"Batch fit done: OK={ok_count}, fail={fail_count}")
        self.analysis_text.setPlainText(
            f"Batch fit done.\nGates: {cube.gate.size}  OK: {ok_count}  Failed: {fail_count}\n"
            "Use 'Export All Results CSV' to save the results."
        )
        self._update_spectrum_only()

    # =========================================================================
    #  CSV seeds loading
    # =========================================================================

    def _on_load_seeds_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        default_dir = str(self.current_folder) if self.current_folder else "."
        path, _ = QFileDialog.getOpenFileName(self, "Load Seeds CSV", default_dir, "CSV Files (*.csv)")
        if not path:
            return
        self._load_seeds_csv_file(Path(path))

    def _load_seeds_csv_file(self, path: Path) -> None:
        """Load a 'gate,peak_energy' CSV as initial seeds for the Lorentz Fit tab."""
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
            self._csv_seeds_map = seeds_map
            self._csv_seeds_path = str(path)
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
        if not hasattr(self, "export_current_fit_btn"):
            return
        current_available = self._active_fit_result() is not None if self.current_cube is not None else False
        self.export_current_fit_btn.setEnabled(current_available)
        # Keep refreshes cheap during axis navigation; serialize rows only when
        # the user invokes the all-results export action.
        self.export_all_fit_btn.setEnabled(bool(
            self._batch_fit_export_rows or self.batch_fit_result_map or self.fit_result
        ))
        if hasattr(self, "export_png_btn"):
            self.export_png_btn.setEnabled(
                self.current_cube is not None
                and self._heat_ax is not None
                and self._heat_colorbar is not None
            )

    def _heatmap_export_metadata(self) -> dict[str, Any]:
        """Preserve full source provenance and the settings represented by the image."""
        cube = self.current_cube
        source_path = cube.source_path or (
            str(self.current_dat_path) if self.current_dat_path else ""
        )
        return {
            "source_path": source_path,
            "source_stem": Path(source_path).stem if source_path else "heatmap",
            "x_limits": [float(value) for value in self._heat_ax.get_xlim()],
            "y_limits": [float(value) for value in self._heat_ax.get_ylim()],
            "color_limits": [float(value) for value in self._heat_mesh.get_clim()],
            "x_label": self._heat_ax.get_xlabel(),
            "y_label": self._heat_ax.get_ylabel(),
            "signal_label": self._heat_colorbar.ax.get_xlabel(),
            "colormap": self._heat_mesh.get_cmap().name,
            "axis_original_type": cube.x_original_type,
            "axis_original_label": cube.x_original_label,
            "axis_override": cube.x_override,
            "axis_detection_source": cube.x_detection_source,
            "axis_detection_note": cube.x_detection_note,
            "axis_is_ambiguous": cube.x_is_ambiguous,
            "photon_energy_note": cube.photon_energy_note,
            "processing": "raw",
            "plot_title": self._heat_ax.get_title(),
        }

    def _heatmap_export_filename(self) -> str:
        """Keep source conditions readable and number exports in the target folder."""
        metadata = self._heatmap_export_metadata()
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", metadata["source_stem"]).rstrip(" .") or "heatmap"
        folder = self.current_dat_path.parent if self.current_dat_path else Path(self.current_folder)
        index = 1
        while (folder / f"{stem}_export{index:02d}.png").exists():
            index += 1
        filename = f"{stem}_export{index:02d}.png"
        if len(filename.encode("utf-16-le")) // 2 > 255:
            raise ValueError("The source filename is too long to append an export number. Shorten the source filename before exporting.")
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
        fields = QHBoxLayout()
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
        actions = QHBoxLayout()
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
        self._title_settings = {"mode": "template", "preset": preset, "manual_override": None}
        self._remember_title_selection()
        self._apply_title_template()

    def _apply_current_title_template(self) -> None:
        try:
            preset = self._edited_title_preset()
        except ValueError as exc:
            self.title_template_status.setText(str(exc))
            return
        self._title_settings = {"mode": "template", "preset": preset, "manual_override": None}
        self._apply_title_template()

    def _save_title_preset(self) -> None:
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
        with QSignalBlocker(self.signal_title_combo):
            self.signal_title_combo.setCurrentText(
                self._annotation_settings.value("plot_annotations/signal_title", "ΔR/R (a. u.)", type=str)
                if self.fix_annotations_chk.isChecked() else "ΔR/R (a. u.)")

    def _save_plot_annotation(self, field: str, text: str) -> None:
        if field == "plot_title":
            self._title_settings["manual_override"] = text
            if self._title_settings["mode"] == "template":
                self.title_template_status.setText("Manual override for this DAT; Apply template to regenerate.")
                self._update_plot_annotations()
                return
        if self.fix_annotations_chk.isChecked():
            self._annotation_settings.setValue(f"plot_annotations/{field}", text)
            self._sync_annotation_settings()
        self._update_plot_annotations()

    def _on_fix_annotations_toggled(self, checked: bool) -> None:
        self._annotation_settings.setValue("plot_annotations/fixed", checked)
        if checked:
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

    def _update_plot_annotations(self) -> None:
        if self._heat_ax is None:
            return
        self._heat_ax.set_title(self.plot_title_edit.text(), fontsize=PLOT_FONT_SIZE, parse_math=False)
        name, unit = self.y_axis_name_edit.text(), self.y_axis_unit_edit.text()
        self._heat_ax.set_ylabel(f"{name} ({unit})" if unit else name, fontsize=PLOT_FONT_SIZE, parse_math=False)
        if self._heat_colorbar is not None:
            style_heatmap_colorbar(self._heat_colorbar, self.signal_title_combo.currentText())
        self._layout_live_plots()
        self.canvas.draw_idle()

    def _layout_live_plots(self, _event=None) -> None:
        if self._heat_ax is None or self._heat_colorbar is None:
            return
        width, height = self.figure.get_size_inches()
        plot_height = min((width - 1.35) * 3 / 4, height * 0.51, height * 0.64 - 0.9)
        plot_width = plot_height * 4 / 3
        left = (width - plot_width) / 2
        bottom = height * 0.36
        self._heat_ax.set_position([left / width, bottom / height, plot_width / width, plot_height / height])
        bar_width = min(1.1, plot_width * 0.48)
        self._heat_colorbar.ax.set_position([
            (left + plot_width - bar_width) / width, (bottom + plot_height + 0.04) / height,
            bar_width / width, 0.07 / height,
        ])
        self._heat_ax.set_title(self.plot_title_edit.text(), fontsize=PLOT_FONT_SIZE,
                                parse_math=False, y=1, pad=0)
        position_heatmap_title(self._heat_ax, self._heat_colorbar.ax, self._heat_ax.title)
        # Reserve space for the heatmap X label and the 12 pt spectrum title.
        spectrum_height = min(height * 0.17, bottom - 0.55 - 0.80)
        self._spec_ax.set_position([
            0.75 / width, 0.55 / height, (width - 1.05) / width, spectrum_height / height,
        ])

    def _on_export_png(self) -> None:
        if self.current_cube is None or self._heat_ax is None or self._heat_colorbar is None:
            self._show_error("Load a DAT file first.")
            return
        try:
            folder = self.current_dat_path.parent if self.current_dat_path else Path(self.current_folder)
            out_path = folder / self._heatmap_export_filename()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        try:
            saved_path = export_figure_png(
                self.figure,
                out_path,
                selected_axes=(self._heat_ax, self._heat_colorbar.ax),
                metadata={"SpectraAnalyzer": json.dumps(self._heatmap_export_metadata(), ensure_ascii=False, sort_keys=True)},
            )
        except ValueError as exc:
            self._show_error(str(exc))
            return
        except OSError as exc:
            self._show_error(f"Failed to export PNG: {exc}")
            return
        except Exception as exc:
            self._show_error(f"Failed to export PNG: {exc}")
            return
        finally:
            self.canvas.draw_idle()
        notice = self._save_current_plot_state()
        suffix = f" — {notice}" if notice else ""
        self.statusBar().showMessage(f"Exported PNG: {saved_path}{suffix}")

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
        default = Path(self.current_folder) / f"{cube.title}_gate_{gate_used:.6g}_{active_fit.model}_fit_{stamp}.csv"
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
        default = Path(self.current_folder) / f"{cube.title}_all_fit_{stamp}.csv"
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
        default = Path(self.current_folder) / f"{self.current_cube.title}_gate_{gate_used:.6g}_spectrum.csv"
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
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if len(urls) == 1:
                path = Path(urls[0].toLocalFile())
                ext = path.suffix.lower()
                if ext in (".dat", ".csv"):
                    event.acceptProposedAction()
                    cw = self.centralWidget()
                    if cw:
                        self._dnd_overlay.resize(cw.size())
                    label = ".dat → load data" if ext == ".dat" else ".csv → load seeds"
                    self._dnd_overlay.setText(f"Drop  {path.name}\n({label})")
                    self._dnd_overlay.setVisible(True)
                    self._dnd_overlay.raise_()
                    return
        event.ignore()

    def dragLeaveEvent(self, event: Any) -> None:
        self._dnd_overlay.setVisible(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self._dnd_overlay.setVisible(False)
        urls = event.mimeData().urls()
        if not urls:
            return
        path = Path(urls[0].toLocalFile())
        ext = path.suffix.lower()
        if ext == ".dat":
            self._load_dat_file_direct(path)
        elif ext == ".csv":
            # Route to seeds loader (works for both tabs' "save peaks" format and seeds format)
            self._load_seeds_csv_file(path)
            # Switch to Lorentz Fit tab to make the outcome obvious
            self._analysis_tabs.setCurrentIndex(1)
        else:
            self.statusBar().showMessage(f"Unsupported file type: {ext}  (only .dat and .csv accepted)")

    # =========================================================================
    #  Matplotlib plotting
    # =========================================================================

    def _plot_cube(self) -> None:
        if self.current_cube is None:
            return
        cube = self.current_cube
        self._view_callbacks_ready = False
        self._heat_mesh = None
        self._heat_colorbar = None

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
        cax = self.figure.add_axes([0.65, 0.88, 0.15, 0.01])
        self._spec_ax = self.figure.add_axes([0.15, 0.1, 0.75, 0.17], sharex=self._heat_ax)
        self._heat_peak_artists = []
        self._heat_peak_texts = []
        self._heat_batch_peak_artists = []

        e = cube.energy
        g = cube.gate
        z = cube.z
        vmin, vmax = self._ordered_pair(float(self.vmin_spin.value()), float(self.vmax_spin.value()))
        im = self._heat_ax.pcolormesh(
            self._axis_edges(e), self._axis_edges(g), z,
            shading="flat",
            cmap=resolve_colormap(self.cmap_combo.currentText()),
            norm=Normalize(vmin=float(vmin), vmax=float(vmax)),
        )
        self._heat_mesh = im
        self._heat_colorbar = self.figure.colorbar(im, cax=cax, orientation="horizontal")
        style_heatmap_colorbar(self._heat_colorbar, self.signal_title_combo.currentText())
        self._last_valid_color_limits = (float(vmin), float(vmax))
        self._update_plot_annotations()
        self._heat_ax.set_xlabel(cube.energy_label)
        style_plot_axis(self._heat_ax)
        xmin, xmax = self._ordered_pair(float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        ymin, ymax = self._ordered_pair(float(self.ymin_spin.value()), float(self.ymax_spin.value()))
        self._heat_ax.set_xlim((xmin, xmax))
        self._heat_ax.set_ylim((ymin, ymax))
        self._layout_live_plots()
        self._heat_ax.grid(alpha=0.15)

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
        self._refresh_export_actions()

    def _update_spectrum_only(self) -> None:
        previous_view_syncing = self._view_syncing
        self._view_syncing = True
        try:
            self._update_spectrum_only_impl()
        finally:
            self._view_syncing = previous_view_syncing

    def _update_spectrum_only_impl(self) -> None:
        if self.current_cube is None or self._spec_ax is None or self._heat_ax is None:
            return
        gate_used, y = self._nearest_spectrum(self.current_gate)
        x = self.current_cube.energy
        trace_peak_indices = self._current_trace_peak_indices()
        fit_centers = self._current_fit_centers()
        active_fit = self._active_fit_result()
        self._spec_ax.clear()
        self._spec_ax.plot(x, y, color="#1f77b4", lw=1.4, label="Spectrum")
        self._spec_ax.set_title(f"Spectrum @ {gate_used:.6g}", fontsize=PLOT_FONT_SIZE)
        self._spec_ax.set_xlabel(self.current_cube.energy_label)
        self._spec_ax.set_ylabel("Intensity (a.u.)")
        self._spec_ax.grid(alpha=0.25)

        xmin, xmax = self._fit_range_bounds()
        self._spec_ax.axvspan(xmin, xmax, color="#66c2a5", alpha=0.12, zorder=0, label="Fit Range")

        # ── Second-derivative overlay ─────────────────────────────────────────
        d2_trace = self._current_second_derivative_trace(x, y)
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
        if self._drag_peak_arr_idx is not None and self._drag_current_x is not None:
            snap_idx = int(np.argmin(np.abs(x - self._drag_current_x)))
            self._spec_ax.scatter(
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
        self._spec_ax.callbacks.connect("xlim_changed", self._on_plot_x_limits_changed)
        self.canvas.draw_idle()

    def _update_gate_line(self, gate: float) -> None:
        if self._heat_ax is None:
            return
        if self._gate_line is None or getattr(self._gate_line, "axes", None) is not self._heat_ax:
            self._gate_line = self._heat_ax.axhline(gate, color="#222222", ls="--", lw=1.2, alpha=0.9)
        else:
            self._gate_line.set_ydata([gate, gate])

    def _update_heatmap_peak_labels(self, gate_used: float, x: np.ndarray) -> None:
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
                    self.fit_result = None
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
        merged = np.concatenate([self.peak_indices, np.asarray([idx], dtype=int)])
        self.peak_indices = np.asarray(np.unique(merged), dtype=int)
        _, y_cur = self._nearest_spectrum(self.current_gate)
        self.peak_kind_lookup[idx] = self._auto_kind(y_cur, idx)
        self._sync_current_trace_peaks_to_batch_map()
        self.fit_result = None
        self._refresh_peak_delete_controls()
        return True

    def _remove_manual_peak_near(self, x_click: float) -> bool:
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
