from __future__ import annotations

import csv
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
from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
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
from .fitting import (
    FitResult,
    find_peak_indices,
    find_peaks_second_derivative,
    fit_multi_peak,
    _clamp_sg_window,
)


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
        self.setMinimumSize(1240, 800)

        # ── data state ──────────────────────────────────────────────────────
        self.current_root = ""
        self.current_folder = ""
        self.current_cube: DataCube | None = None
        self.current_dat_path: Path | None = None
        self.current_gate = 0.0
        self.peak_indices: np.ndarray = np.asarray([], dtype=int)
        self._peak_gate_idx: int | None = None
        self.batch_peak_coords: tuple[np.ndarray, np.ndarray] | None = None
        self.batch_peak_records: list[tuple[int, int]] = []
        self.batch_peak_map: dict[int, np.ndarray] = {}
        self.fit_result: FitResult | None = None
        self._fit_gate_idx: int | None = None
        self.batch_fit_map: dict[int, np.ndarray] = {}
        self.batch_fit_coords: tuple[np.ndarray, np.ndarray] | None = None
        self.batch_fit_result_map: dict[int, FitResult] = {}
        self._batch_fit_export_rows: list[dict[str, str]] = []

        # ── UI lock helpers ──────────────────────────────────────────────────
        self._view_lock_controls: dict[QDoubleSpinBox, QCheckBox] = {}
        self._fit_lock_controls: dict[QDoubleSpinBox, QCheckBox] = {}

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

        self._build_ui()
        self.setAcceptDrops(True)

    # =========================================================================
    #  UI construction
    # =========================================================================

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        split = QSplitter(Qt.Horizontal)
        plot_panel = self._build_plot_panel()
        left = self._build_left_panel()
        left.setMinimumWidth(430)
        split.addWidget(left)
        split.addWidget(plot_panel)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([460, 860])
        outer = QHBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(split)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Select a root folder or drag-and-drop a .dat file to start.")

        # DnD overlay (child of centralWidget so it floats on top)
        self._dnd_overlay = QLabel(self.centralWidget())
        self._dnd_overlay.setAlignment(Qt.AlignCenter)
        self._dnd_overlay.setStyleSheet(
            "background: rgba(33,150,243,0.18);"
            "color: #0d47a1;"
            "font-size: 20px;"
            "font-weight: bold;"
            "border: 3px dashed #1565c0;"
            "border-radius: 10px;"
        )
        self._dnd_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._dnd_overlay.setVisible(False)
        self.plot_gate_spin.valueChanged.connect(self._on_plot_gate_spin_changed)
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
        box.setFrameShadow(QFrame.Raised)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        btn = QToolButton()
        btn.setText(title)
        btn.setCheckable(True)
        btn.setChecked(expanded)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        btn.setStyleSheet("QToolButton { font-weight: 600; border: none; padding: 2px 4px; text-align: left; }")
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

        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(6)

        # ── Data Source ───────────────────────────────────────────────────────
        src = QGroupBox("")
        src_grid = QVBoxLayout(src)
        src_grid.setContentsMargins(6, 6, 6, 6)
        src_grid.setSpacing(4)
        self.root_edit = QLineEdit()
        self.root_edit.setReadOnly(True)
        self.root_edit.setPlaceholderText("Root folder path")
        self.browse_btn = QPushButton("Browse Root Folder")
        self.folder_combo = QComboBox()
        self.folder_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.folder_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        src_grid.addWidget(self.root_edit)
        src_grid.addWidget(self.browse_btn)
        src_grid.addWidget(QLabel("Process Folder:"))
        src_grid.addWidget(self.folder_combo)
        lay.addWidget(self._make_expander("Data Source", src, expanded=False))

        # ── DAT Files ─────────────────────────────────────────────────────────
        files = QGroupBox("")
        files_l = QVBoxLayout(files)
        files_l.setContentsMargins(6, 6, 6, 6)
        files_l.setSpacing(4)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.file_list.setMaximumHeight(110)
        self.load_btn = QPushButton("Load Selected File")
        files_l.addWidget(self.file_list)
        files_l.addWidget(self.load_btn)
        lay.addWidget(self._make_expander("DAT Files", files, expanded=False))

        # ── Plot / Cursor ─────────────────────────────────────────────────────
        params = QGroupBox("")
        params_form = QFormLayout(params)
        params_form.setContentsMargins(6, 6, 6, 6)
        params_form.setSpacing(4)
        params_form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)

        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["turbo", "viridis", "plasma", "inferno", "magma", "cividis", "RdBu_r"])
        self.cmap_combo.setCurrentText("RdBu_r")
        self.cmap_combo.setMaximumWidth(150)

        self.x_axis_override_combo = QComboBox()
        for value, label in AXIS_OVERRIDE_ITEMS:
            self.x_axis_override_combo.addItem(label, value)
        self.x_axis_override_combo.setMaximumWidth(220)
        self.axis_status_label = QLabel("X-axis: no file loaded.")
        self.axis_status_label.setWordWrap(True)
        self.axis_status_label.setStyleSheet("color:#54606c; font-size: 10px;")

        self.gate_spin = GateValueSpinBox()
        self.gate_spin.setDecimals(6)
        self.gate_spin.setRange(-1e12, 1e12)
        self.gate_spin.setSingleStep(0.01)
        self.gate_spin.setMaximumWidth(150)
        self.apply_gate_btn = QPushButton("Apply Gate")

        self.xmin_spin = QDoubleSpinBox()
        self.xmax_spin = QDoubleSpinBox()
        self.ymin_spin = QDoubleSpinBox()
        self.ymax_spin = QDoubleSpinBox()
        self.vmin_spin = QDoubleSpinBox()
        self.vmax_spin = QDoubleSpinBox()
        for spin in (self.xmin_spin, self.xmax_spin, self.ymin_spin,
                     self.ymax_spin, self.vmin_spin, self.vmax_spin):
            spin.setDecimals(6)
            spin.setRange(-1e12, 1e12)
            spin.setSingleStep(0.01)
            spin.setMaximumWidth(150)
        self.xmin_lock = QCheckBox("Fix")
        self.xmax_lock = QCheckBox("Fix")
        self.ymin_lock = QCheckBox("Fix")
        self.ymax_lock = QCheckBox("Fix")
        self.vmin_lock = QCheckBox("Fix")
        self.vmax_lock = QCheckBox("Fix")
        self._view_lock_controls = {
            self.xmin_spin: self.xmin_lock, self.xmax_spin: self.xmax_lock,
            self.ymin_spin: self.ymin_lock, self.ymax_spin: self.ymax_lock,
            self.vmin_spin: self.vmin_lock, self.vmax_spin: self.vmax_lock,
        }
        self.apply_view_btn = QPushButton("Apply View")
        self.auto_view_btn = QPushButton("Auto View")
        view_btn_row = self._hrow([self.apply_view_btn, self.auto_view_btn])

        params_form.addRow("X-Axis Override", self.x_axis_override_combo)
        params_form.addRow("X-Axis Status", self.axis_status_label)
        params_form.addRow("Colormap", self.cmap_combo)
        params_form.addRow("xmin", self._view_spin_row(self.xmin_spin, self.xmin_lock))
        params_form.addRow("xmax", self._view_spin_row(self.xmax_spin, self.xmax_lock))
        params_form.addRow("ymin", self._view_spin_row(self.ymin_spin, self.ymin_lock))
        params_form.addRow("ymax", self._view_spin_row(self.ymax_spin, self.ymax_lock))
        params_form.addRow("vmin", self._view_spin_row(self.vmin_spin, self.vmin_lock))
        params_form.addRow("vmax", self._view_spin_row(self.vmax_spin, self.vmax_lock))
        params_form.addRow("Gate", self.gate_spin)
        params_form.addRow("", self.apply_gate_btn)
        params_form.addRow("", view_btn_row)
        lay.addWidget(self._make_expander("Plot / Cursor", params, expanded=False))

        # ── Spectrum Analysis (tabs) ──────────────────────────────────────────
        analysis_outer = QGroupBox("")
        ao_lay = QVBoxLayout(analysis_outer)
        ao_lay.setContentsMargins(4, 4, 4, 4)
        ao_lay.setSpacing(4)

        self._analysis_tabs = QTabWidget()
        self._analysis_tabs.addTab(self._build_find_peak_tab(), "Find Peaks")
        self._analysis_tabs.addTab(self._build_lorentz_fit_tab(), "Lorentz Fit")

        self.analysis_text = QPlainTextEdit()
        self.analysis_text.setReadOnly(True)
        self.analysis_text.setMaximumHeight(120)

        ao_lay.addWidget(self._analysis_tabs)
        ao_lay.addWidget(self.analysis_text)
        lay.addWidget(self._make_expander("Spectrum Analysis", analysis_outer, expanded=True))
        lay.addStretch(1)

        # ── wire up non-tab signals ───────────────────────────────────────────
        self.browse_btn.clicked.connect(self._on_browse_root)
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        self.load_btn.clicked.connect(self._on_load_selected_file)
        self.file_list.itemDoubleClicked.connect(lambda _: self._on_load_selected_file())
        self.apply_gate_btn.clicked.connect(self._on_apply_gate)
        self.gate_spin.valueChanged.connect(self._on_gate_spin_changed)
        self.x_axis_override_combo.currentIndexChanged.connect(self._on_x_axis_override_changed)
        self.cmap_combo.currentTextChanged.connect(lambda _: self._plot_cube())
        for spin in (self.xmin_spin, self.xmax_spin, self.ymin_spin,
                     self.ymax_spin, self.vmin_spin, self.vmax_spin):
            spin.valueChanged.connect(lambda _: self._plot_cube())
        self.apply_view_btn.clicked.connect(self._plot_cube)
        self.auto_view_btn.clicked.connect(self._on_auto_view)

        scroll.setWidget(panel)
        return scroll

    # ── Find Peaks tab ────────────────────────────────────────────────────────

    def _build_find_peak_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(5)

        # Detection mode
        mode_box = QGroupBox("Detection Mode")
        mode_l = QVBoxLayout(mode_box)
        mode_l.setContentsMargins(6, 6, 6, 4)
        mode_l.setSpacing(2)
        self._orig_radio = QRadioButton("Original Spectrum")
        self._d2_radio = QRadioButton("Second Derivative")
        self._orig_radio.setToolTip("Find peaks directly from the displayed spectrum. Best for clean traces.")
        self._d2_radio.setToolTip("Use the second derivative to separate overlapping peaks in noisier spectra.")
        self._orig_radio.setChecked(True)
        mode_l.addWidget(self._orig_radio)
        mode_l.addWidget(self._d2_radio)
        lay.addWidget(mode_box)

        # ── Original spectrum params ──────────────────────────────────────────
        self._orig_params_widget = QWidget()
        orig_form = QFormLayout(self._orig_params_widget)
        orig_form.setContentsMargins(0, 0, 0, 0)
        orig_form.setSpacing(3)
        orig_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.peak_mode_combo = QComboBox()
        self.peak_mode_combo.addItems(["Peaks", "Dips"])
        self.peak_mode_combo.setToolTip("Choose whether the algorithm should look for upward peaks or downward dips.")

        self.prom_spin = QDoubleSpinBox()
        self.prom_spin.setDecimals(4)
        self.prom_spin.setRange(0.0, 1.0)
        self.prom_spin.setSingleStep(0.005)
        self.prom_spin.setValue(0.05)
        self.prom_spin.setToolTip("Fraction of signal range required as minimum prominence.")

        self.distance_spin = QSpinBox()
        self.distance_spin.setRange(1, 1000)
        self.distance_spin.setValue(5)
        self.distance_spin.setToolTip("Minimum distance between peaks in sample points.")

        self.max_peaks_spin = QSpinBox()
        self.max_peaks_spin.setRange(1, 30)
        self.max_peaks_spin.setValue(6)
        self.max_peaks_spin.setToolTip("Maximum number of peaks to keep after detection.")

        self.peak_smooth_chk = QCheckBox("Smooth")
        self.peak_smooth_chk.setToolTip("Apply light smoothing before peak finding to reduce small noise spikes.")
        self.peak_smooth_win_spin = QSpinBox()
        self.peak_smooth_win_spin.setRange(3, 501)
        self.peak_smooth_win_spin.setValue(11)
        self.peak_smooth_win_spin.setMaximumWidth(70)
        self.peak_smooth_win_spin.setToolTip("Savitzky-Golay window for smoothing before peak finding.")

        orig_form.addRow("Peak Type", self.peak_mode_combo)
        orig_form.addRow("Prominence", self.prom_spin)
        orig_form.addRow("Min Distance (pts)", self.distance_spin)
        orig_form.addRow("Max Peaks", self.max_peaks_spin)
        orig_form.addRow("Smoothing", self._hrow([self.peak_smooth_chk, QLabel("Win"), self.peak_smooth_win_spin]))

        # ── Second-derivative params ──────────────────────────────────────────
        self._d2_params_widget = QWidget()
        d2_form = QFormLayout(self._d2_params_widget)
        d2_form.setContentsMargins(0, 0, 0, 0)
        d2_form.setSpacing(3)
        d2_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.d2_mode_combo = QComboBox()
        self.d2_mode_combo.addItems(["Peaks", "Dips"])
        self.d2_mode_combo.setToolTip("Choose whether second-derivative detection should return peaks or dips.")

        self.d2_sg_win_spin = QSpinBox()
        self.d2_sg_win_spin.setRange(3, 501)
        self.d2_sg_win_spin.setValue(11)
        self.d2_sg_win_spin.setToolTip("Savitzky-Golay window for computing second derivative (must be odd).")

        self.d2_sg_poly_spin = QSpinBox()
        self.d2_sg_poly_spin.setRange(1, 6)
        self.d2_sg_poly_spin.setValue(3)
        self.d2_sg_poly_spin.setToolTip("Polynomial order for Savitzky-Golay filter.")

        self.d2_prom_spin = QDoubleSpinBox()
        self.d2_prom_spin.setDecimals(4)
        self.d2_prom_spin.setRange(0.0, 1.0)
        self.d2_prom_spin.setSingleStep(0.005)
        self.d2_prom_spin.setValue(0.03)
        self.d2_prom_spin.setToolTip("Fraction of d² signal range required as prominence.")

        self.d2_dist_spin = QSpinBox()
        self.d2_dist_spin.setRange(1, 1000)
        self.d2_dist_spin.setValue(3)
        self.d2_dist_spin.setToolTip("Minimum distance between detected peaks in sample points.")

        self.d2_sep_spin = QDoubleSpinBox()
        self.d2_sep_spin.setDecimals(5)
        self.d2_sep_spin.setRange(0.0, 100.0)
        self.d2_sep_spin.setSingleStep(0.001)
        self.d2_sep_spin.setValue(0.0)
        self.d2_sep_spin.setToolTip("Minimum separation in energy (eV). 0 = use Min Distance only.")

        self.d2_max_peaks_spin = QSpinBox()
        self.d2_max_peaks_spin.setRange(1, 30)
        self.d2_max_peaks_spin.setValue(6)
        self.d2_max_peaks_spin.setToolTip("Maximum number of candidates kept from second-derivative detection.")

        self.d2_target_spin = QSpinBox()
        self.d2_target_spin.setRange(0, 30)
        self.d2_target_spin.setValue(0)
        self.d2_target_spin.setToolTip("0 = keep top-N by Max Peaks. N > 0 = keep exactly top N by d² prominence.")

        d2_form.addRow("Peak Type", self.d2_mode_combo)
        d2_form.addRow("SG Window", self.d2_sg_win_spin)
        d2_form.addRow("SG Poly Order", self.d2_sg_poly_spin)
        d2_form.addRow("D² Prominence", self.d2_prom_spin)
        d2_form.addRow("Min Distance (pts)", self.d2_dist_spin)
        d2_form.addRow("Min Sep (eV)", self.d2_sep_spin)
        d2_form.addRow("Max Peaks", self.d2_max_peaks_spin)
        d2_form.addRow("Target Count (0=auto)", self.d2_target_spin)
        self._d2_params_widget.setVisible(False)

        params_box = QGroupBox("Parameters")
        params_l = QVBoxLayout(params_box)
        params_l.setContentsMargins(6, 6, 6, 4)
        params_l.setSpacing(2)
        params_l.addWidget(self._orig_params_widget)
        params_l.addWidget(self._d2_params_widget)
        lay.addWidget(params_box)

        # ── Actions ───────────────────────────────────────────────────────────
        self.find_btn = QPushButton("Find Peaks (Current Gate)")
        self.batch_find_btn = QPushButton("Find Peaks – All Gates")
        self.find_btn.setToolTip("Detect peaks only for the currently selected gate trace.")
        self.batch_find_btn.setToolTip("Run peak detection across every gate and show the result on the heatmap.")
        lay.addWidget(self.find_btn)
        lay.addWidget(self.batch_find_btn)

        self.lasso_mode_btn = QPushButton("Heatmap Lasso Mode")
        self.lasso_mode_btn.setCheckable(True)
        self.lasso_mode_btn.setToolTip(
            "Turn on heatmap lasso capture.\n"
            "Drag on the heatmap to select many all-gate peaks at once."
        )
        self.delete_lasso_btn = QPushButton("Delete Lasso Selection (Heatmap)")
        self.delete_lasso_btn.setEnabled(False)
        self.delete_lasso_btn.setToolTip("Delete every heatmap peak currently selected by the lasso.")
        self.clear_lasso_btn = QPushButton("Clear Heatmap Selection")
        self.clear_lasso_btn.setEnabled(False)
        self.clear_lasso_btn.setToolTip("Keep the peaks, but clear the current lasso selection highlight.")

        self.lasso_helper_label = QLabel(
            "Heatmap lasso: drag on the colormap to select multiple all-gate peaks, then delete or clear the selection."
        )
        self.lasso_helper_label.setWordWrap(True)
        self.lasso_helper_label.setStyleSheet("color:#54606c; font-size: 10px;")
        self.lasso_helper_label.setVisible(False)

        self.save_peaks_csv_btn = QPushButton("Save Peaks CSV")
        self.save_peaks_csv_btn.setToolTip(
            "Save all-gates peak positions (batch_peak_map) to CSV.\n"
            "If no all-gates data, saves current gate peaks."
        )
        self.circle_delete_btn = QPushButton("Circle Delete on Heatmap")
        self.circle_delete_btn.setCheckable(True)
        self.circle_delete_btn.setToolTip("Drag on heatmap to delete all-gate peaks inside the circle.")
        self.clear_batch_peaks_btn = QPushButton("Clear All Peaks (All Gates)")
        self.clear_batch_peaks_btn.setToolTip("Remove every detected all-gates heatmap peak from the current batch result.")

        # ── Per-peak delete ───────────────────────────────────────────────────
        self.peak_delete_spin = QSpinBox()
        self.peak_delete_spin.setRange(1, 1)
        self.peak_delete_spin.setValue(1)
        self.peak_delete_spin.setMaximumWidth(60)
        self.delete_peak_btn = QPushButton("Delete Peak # From Current Trace")
        self.clear_peaks_btn = QPushButton("Clear Current-Trace Peaks")
        self.peak_delete_spin.setToolTip("Select which numbered peak to remove from the current gate.")
        self.delete_peak_btn.setToolTip("Delete the numbered peak from the current gate.")
        self.clear_peaks_btn.setToolTip("Remove all currently selected peaks from the current gate.")

        selection_box = QGroupBox("Heatmap Batch Selection")
        selection_l = QVBoxLayout(selection_box)
        selection_l.setContentsMargins(6, 6, 6, 4)
        selection_l.setSpacing(4)
        selection_l.addWidget(self._hrow([self.lasso_mode_btn, self.delete_lasso_btn, self.clear_lasso_btn]))
        selection_l.addWidget(self.lasso_helper_label)
        lay.addWidget(selection_box)

        single_box = QGroupBox("Current Trace Editing")
        single_l = QVBoxLayout(single_box)
        single_l.setContentsMargins(6, 6, 6, 4)
        single_l.setSpacing(4)
        single_l.addWidget(self._hrow([QLabel("Peak #"), self.peak_delete_spin]))
        single_l.addWidget(self.delete_peak_btn)
        single_l.addWidget(self.clear_peaks_btn)
        lay.addWidget(single_box)

        global_box = QGroupBox("All-Gates Cleanup")
        global_l = QVBoxLayout(global_box)
        global_l.setContentsMargins(6, 6, 6, 4)
        global_l.setSpacing(4)
        global_l.addWidget(self.circle_delete_btn)
        global_l.addWidget(self.clear_batch_peaks_btn)
        global_l.addWidget(self.save_peaks_csv_btn)
        lay.addWidget(global_box)

        hint = QLabel(
            "Spectrum: Left-click=Add peak  |  Right-click=Remove nearest  |  "
            "Drag near peak=Move peak.\n"
            "Heatmap: Left-click=Select gate  |  Lasso mode=Select all-gate peaks  |  Circle mode=Delete in region."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#54606c; font-size: 10px;")
        lay.addWidget(hint)
        lay.addStretch(1)

        # ── wire signals ──────────────────────────────────────────────────────
        self._orig_radio.toggled.connect(self._on_detect_mode_changed)
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
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(5)

        # ── Seeds source ──────────────────────────────────────────────────────
        seeds_box = QGroupBox("Initial Seeds Source")
        seeds_l = QVBoxLayout(seeds_box)
        seeds_l.setContentsMargins(6, 6, 6, 4)
        seeds_l.setSpacing(3)
        self._seeds_from_find_peak_radio = QRadioButton("From Find Peaks tab (default)")
        self._seeds_from_csv_radio = QRadioButton("From CSV file")
        self._seeds_from_find_peak_radio.setToolTip("Use peaks found in the Find Peaks tab as fit starting points.")
        self._seeds_from_csv_radio.setToolTip("Load your own per-gate seed positions from a CSV file.")
        self._seeds_from_find_peak_radio.setChecked(True)
        seeds_l.addWidget(self._seeds_from_find_peak_radio)
        seeds_l.addWidget(self._seeds_from_csv_radio)

        self._csv_seeds_row = QWidget()
        csv_row_l = QHBoxLayout(self._csv_seeds_row)
        csv_row_l.setContentsMargins(0, 2, 0, 0)
        csv_row_l.setSpacing(4)
        self.load_seeds_csv_btn = QPushButton("Load Seeds CSV…")
        self.load_seeds_csv_btn.setToolTip("Load a CSV of initial peak positions to guide fitting.")
        self._csv_path_label = QLabel("(none loaded)")
        self._csv_path_label.setStyleSheet("color:#54606c; font-size: 10px;")
        csv_row_l.addWidget(self.load_seeds_csv_btn)
        csv_row_l.addWidget(self._csv_path_label, 1)
        self._csv_seeds_row.setVisible(False)
        seeds_l.addWidget(self._csv_seeds_row)
        lay.addWidget(seeds_box)

        self.fit_axis_label = QLabel("Analysis axis: no file loaded.")
        self.fit_axis_label.setWordWrap(True)
        self.fit_axis_label.setStyleSheet("color:#54606c; font-size: 10px;")
        lay.addWidget(self.fit_axis_label)

        # ── Fit range ─────────────────────────────────────────────────────────
        range_box = QGroupBox("Fit Range")
        range_form = QFormLayout(range_box)
        range_form.setContentsMargins(6, 6, 6, 4)
        range_form.setSpacing(3)
        range_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.fit_xmin_spin = QDoubleSpinBox()
        self.fit_xmax_spin = QDoubleSpinBox()
        for sp in (self.fit_xmin_spin, self.fit_xmax_spin):
            sp.setDecimals(6)
            sp.setRange(-1e12, 1e12)
            sp.setSingleStep(0.01)
            sp.setMaximumWidth(120)
            sp.setToolTip("Limit fitting to this x-axis window so the optimizer ignores unrelated features.")
        self.fit_xmin_lock = QCheckBox("Fix")
        self.fit_xmax_lock = QCheckBox("Fix")
        self._fit_lock_controls = {
            self.fit_xmin_spin: self.fit_xmin_lock,
            self.fit_xmax_spin: self.fit_xmax_lock,
        }
        self.fit_full_btn = QPushButton("Full")
        self.fit_full_btn.setMaximumWidth(46)
        self.fit_full_btn.setToolTip("Reset the fit range to the full currently loaded x-axis span.")
        xmax_w = QWidget()
        xmax_l = QHBoxLayout(xmax_w)
        xmax_l.setContentsMargins(0, 0, 0, 0)
        xmax_l.setSpacing(4)
        xmax_l.addWidget(self._view_spin_row(self.fit_xmax_spin, self.fit_xmax_lock))
        xmax_l.addWidget(self.fit_full_btn)

        self.fit_smooth_chk = QCheckBox("Smooth")
        self.fit_smooth_chk.setToolTip("Smooth the trace before fitting when noise is making the fit unstable.")
        self.fit_smooth_win_spin = QSpinBox()
        self.fit_smooth_win_spin.setRange(3, 501)
        self.fit_smooth_win_spin.setValue(11)
        self.fit_smooth_win_spin.setMaximumWidth(70)
        self.fit_smooth_win_spin.setToolTip("Savitzky-Golay window size used when fit smoothing is enabled.")

        range_form.addRow("Fit xmin", self._view_spin_row(self.fit_xmin_spin, self.fit_xmin_lock))
        range_form.addRow("Fit xmax", xmax_w)
        range_form.addRow("Smoothing", self._hrow([self.fit_smooth_chk, QLabel("Win"), self.fit_smooth_win_spin]))
        lay.addWidget(range_box)

        # ── Fit parameters / bounds ───────────────────────────────────────────
        bounds_box = QGroupBox("Fit Parameters")
        bounds_form = QFormLayout(bounds_box)
        bounds_form.setContentsMargins(6, 6, 6, 4)
        bounds_form.setSpacing(3)
        bounds_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.fit_model_combo = QComboBox()
        self.fit_model_combo.addItems(["Lorentzian", "Gaussian"])
        self.fit_model_combo.setCurrentText("Lorentzian")
        self.fit_model_combo.setMaximumWidth(130)
        self.fit_model_combo.setToolTip("Choose the line shape used to model each detected peak.")

        self.lw_min_spin = QDoubleSpinBox()
        self.lw_min_spin.setDecimals(6)
        self.lw_min_spin.setRange(0.0, 100.0)
        self.lw_min_spin.setSingleStep(0.001)
        self.lw_min_spin.setValue(0.001)
        self.lw_min_spin.setToolTip("Minimum linewidth / FWHM in eV. 0 = nearly zero.")

        self.lw_max_spin = QDoubleSpinBox()
        self.lw_max_spin.setDecimals(6)
        self.lw_max_spin.setRange(0.0, 100.0)
        self.lw_max_spin.setSingleStep(0.01)
        self.lw_max_spin.setValue(0.05)
        self.lw_max_spin.setToolTip("Maximum linewidth / FWHM in eV. 0 = auto (2× fit range).")

        self.peak_move_limit_spin = QDoubleSpinBox()
        self.peak_move_limit_spin.setDecimals(6)
        self.peak_move_limit_spin.setRange(0.0, 100.0)
        self.peak_move_limit_spin.setSingleStep(0.005)
        self.peak_move_limit_spin.setValue(0.01)
        self.peak_move_limit_spin.setToolTip(
            "Maximum shift of fitted peak center from seed (eV). 0 = unconstrained."
        )

        self.maxfev_spin = QSpinBox()
        self.maxfev_spin.setRange(100, 2000000)
        self.maxfev_spin.setValue(20000)
        self.maxfev_spin.setSingleStep(5000)
        self.maxfev_spin.setToolTip("Maximum function evaluations for scipy curve_fit.")

        bounds_form.addRow("Model", self.fit_model_combo)
        bounds_form.addRow("Linewidth min (eV)", self.lw_min_spin)
        bounds_form.addRow("Linewidth max (eV)", self.lw_max_spin)
        bounds_form.addRow("Peak move limit (eV)", self.peak_move_limit_spin)
        bounds_form.addRow("Max iterations", self.maxfev_spin)
        lay.addWidget(bounds_box)

        # ── Action buttons ────────────────────────────────────────────────────
        self.fit_btn = QPushButton("Fit Current Gate")
        self.batch_fit_btn = QPushButton("Batch Fit All Gates")
        self.clear_fit_btn = QPushButton("Clear Fit")
        self.export_fit_btn = QPushButton("Export Fit CSV")
        self.export_spec_btn = QPushButton("Export Spectrum CSV")
        self.fit_btn.setToolTip("Fit the currently selected gate trace using the active seed list.")
        self.batch_fit_btn.setToolTip("Repeat fitting for all gates and export the collected results.")
        self.clear_fit_btn.setToolTip("Remove the current fit overlay from the spectrum plot.")
        self.export_fit_btn.setToolTip("Save the current fit parameters to CSV.")
        self.export_spec_btn.setToolTip("Save the current spectrum, fit-range mask, and analysis trace to CSV.")
        lay.addWidget(self.fit_btn)
        lay.addWidget(self.batch_fit_btn)
        lay.addWidget(self._hrow([self.clear_fit_btn, self.export_fit_btn]))
        lay.addWidget(self.export_spec_btn)
        lay.addStretch(1)

        # ── wire signals ──────────────────────────────────────────────────────
        self._seeds_from_csv_radio.toggled.connect(self._csv_seeds_row.setVisible)
        self.load_seeds_csv_btn.clicked.connect(self._on_load_seeds_csv)
        self.fit_xmin_spin.valueChanged.connect(lambda _: self._update_spectrum_only())
        self.fit_xmax_spin.valueChanged.connect(lambda _: self._update_spectrum_only())
        self.fit_full_btn.clicked.connect(self._on_set_fit_full_range)
        self.fit_btn.clicked.connect(self._on_fit)
        self.batch_fit_btn.clicked.connect(self._on_batch_fit_all_gates)
        self.clear_fit_btn.clicked.connect(self._on_clear_fit)
        self.export_fit_btn.clicked.connect(self._on_export_fit_csv)
        self.export_spec_btn.clicked.connect(self._on_export_spectrum_csv)
        return tab

    # ── plot panel ────────────────────────────────────────────────────────────

    def _build_plot_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        self.figure = Figure(figsize=(8.0, 6.0), tight_layout=False)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.plot_gate_spin = GateValueSpinBox()
        self.plot_gate_spin.setDecimals(6)
        self.plot_gate_spin.setRange(-1e12, 1e12)
        self.plot_gate_spin.setMaximumWidth(150)
        self.plot_gate_index_spin = QSpinBox()
        self.plot_gate_index_spin.setRange(1, 1)
        self.plot_gate_index_spin.setMaximumWidth(80)
        self.plot_prev_gate_btn = QPushButton("<")
        self.plot_prev_gate_btn.setMaximumWidth(32)
        self.plot_next_gate_btn = QPushButton(">")
        self.plot_next_gate_btn.setMaximumWidth(32)
        self.plot_gate_row = self._hrow([
            QLabel("Gate"), self.plot_gate_spin,
            QLabel("Index"), self.plot_gate_index_spin,
            self.plot_prev_gate_btn, self.plot_next_gate_btn,
        ])
        lay.addWidget(self.toolbar)
        lay.addWidget(self.plot_gate_row)
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

    def _sync_current_trace_peaks_to_batch_map(self) -> None:
        if self.current_cube is None or not self.batch_peak_map:
            return
        gate_idx = self._current_gate_index()
        if self._peak_gate_idx != gate_idx:
            return
        valid = self._current_trace_peak_indices()
        if valid.size > 0:
            self.batch_peak_map[gate_idx] = np.asarray(valid, dtype=int)
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
            for spin in (self.gate_spin, self.plot_gate_spin):
                spin.blockSignals(True)
                spin.set_allowed_values(np.asarray([], dtype=float))
                spin.blockSignals(False)
            self.plot_gate_index_spin.blockSignals(True)
            self.plot_gate_index_spin.setRange(1, 1)
            self.plot_gate_index_spin.setValue(1)
            self.plot_gate_index_spin.blockSignals(False)
            return
        gates = np.asarray(self.current_cube.gate, dtype=float)
        gate_idx = self._current_gate_index()
        gate_value = float(gates[gate_idx])
        for spin in (self.gate_spin, self.plot_gate_spin):
            spin.blockSignals(True)
            spin.set_allowed_values(gates)
            spin.setValue(gate_value)
            spin.blockSignals(False)
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
            self._refresh_peak_delete_controls()
        self._sync_gate_controls()
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

    def _get_seeds_for_current_gate(self) -> np.ndarray:
        """Return energy values (eV) to use as fit seeds for the current gate."""
        if self._seeds_from_csv_radio.isChecked() and self._csv_seeds_map is not None:
            gi = self._current_gate_index()
            return np.asarray(self._csv_seeds_map.get(gi, []), dtype=float)
        trace_peaks = self._current_trace_peak_indices()
        if trace_peaks.size == 0 or self.current_cube is None:
            return np.asarray([], dtype=float)
        return np.asarray(self.current_cube.energy[trace_peaks], dtype=float)

    def _get_seeds_for_gate_idx(self, gi: int) -> np.ndarray:
        """Return energy values (eV) to use as fit seeds for gate index gi."""
        if self._seeds_from_csv_radio.isChecked() and self._csv_seeds_map is not None:
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
            cube = load_dat_cube(path, x_axis_override=self._selected_x_axis_override())
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.current_cube = cube
        self.current_dat_path = path
        self.peak_indices = np.asarray([], dtype=int)
        self._peak_gate_idx = None
        self.batch_peak_map = {}
        self.batch_peak_coords = None
        self.batch_peak_records = []
        self.fit_result = None
        self._fit_gate_idx = None
        self.batch_fit_map = {}
        self.batch_fit_coords = None
        self.batch_fit_result_map = {}
        self._batch_fit_export_rows = []
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
        self._set_view_limits_to_auto()
        self._set_fit_range_to_full()
        self._refresh_axis_status()
        self._plot_cube()
        self.analysis_text.setPlainText(
            f"Loaded: {path.name}\n"
            f"Detected x-axis: {cube.x_original_label}\n"
            f"Analysis axis: {cube.energy_label}\n"
            "Use Find Peaks tab to detect peaks, then Lorentz Fit tab to fit.\n"
            "Drag-and-drop a .dat or .csv file onto the window as an alternative to browse."
        )
        self.statusBar().showMessage(f"Loaded: {path.name}")

    def _on_x_axis_override_changed(self) -> None:
        if self.current_dat_path is None:
            self._refresh_axis_status()
            return
        gate = self.current_gate
        try:
            cube = load_dat_cube(self.current_dat_path, x_axis_override=self._selected_x_axis_override())
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.current_cube = cube
        self.peak_indices = np.asarray([], dtype=int)
        self._peak_gate_idx = None
        self.batch_peak_map = {}
        self.batch_peak_coords = None
        self.batch_peak_records = []
        self.fit_result = None
        self._fit_gate_idx = None
        self.batch_fit_map = {}
        self.batch_fit_coords = None
        self.batch_fit_result_map = {}
        self._batch_fit_export_rows = []
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
        self._plot_cube()

    # =========================================================================
    #  View / cursor controls
    # =========================================================================

    def _on_apply_gate(self) -> None:
        if self.current_cube is None:
            return
        self._set_current_gate_from_value(float(self.gate_spin.value()), update_plot=True)

    def _on_auto_view(self) -> None:
        self._set_view_limits_to_auto()
        self._plot_cube()

    def _on_gate_spin_changed(self, value: float) -> None:
        self.current_gate = float(value)
        if self.batch_peak_map:
            gate_idx = self._current_gate_index()
            self.peak_indices = np.asarray(self.batch_peak_map.get(gate_idx, []), dtype=int)
            self._peak_gate_idx = gate_idx
            self._refresh_peak_delete_controls()

    def _on_plot_gate_spin_changed(self, value: float) -> None:
        self._set_current_gate_from_value(float(value), update_plot=True)

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

    def _set_view_limits_to_auto(self) -> None:
        if self.current_cube is None:
            return
        cube = self.current_cube
        finite = cube.z[np.isfinite(cube.z)]
        vmin, vmax = (np.nanpercentile(finite, [0.5, 99.5]) if finite.size else (0.0, 1.0))
        xmin = float(np.nanmin(cube.energy))
        xmax = float(np.nanmax(cube.energy))
        ymin = float(np.nanmin(cube.gate))
        ymax = float(np.nanmax(cube.gate))
        for spin, val in (
            (self.xmin_spin, xmin), (self.xmax_spin, xmax),
            (self.ymin_spin, ymin), (self.ymax_spin, ymax),
            (self.vmin_spin, float(vmin)), (self.vmax_spin, float(vmax)),
        ):
            lock_box = self._view_lock_controls.get(spin)
            if lock_box is not None and lock_box.isChecked():
                continue
            spin.blockSignals(True)
            spin.setValue(float(val))
            spin.blockSignals(False)

    # =========================================================================
    #  Find Peak logic
    # =========================================================================

    def _on_detect_mode_changed(self, orig_is_checked: bool) -> None:
        self._orig_params_widget.setVisible(orig_is_checked)
        self._d2_params_widget.setVisible(not orig_is_checked)
        self._cached_d2 = None

    def _on_find_peaks(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        gate_used, y = self._nearest_spectrum(self.gate_spin.value())
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
                idx_local, d2y = find_peaks_second_derivative(
                    xr, yr,
                    mode=self._peak_mode(),  # type: ignore[arg-type]
                    sg_window=int(self.d2_sg_win_spin.value()),
                    sg_polyorder=int(self.d2_sg_poly_spin.value()),
                    d2_prom_frac=float(self.d2_prom_spin.value()),
                    d2_distance=int(self.d2_dist_spin.value()),
                    d2_sep_ev=float(self.d2_sep_spin.value()),
                    max_peaks=int(self.d2_max_peaks_spin.value()),
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
                idx_local = find_peak_indices(
                    xr, yr,
                    mode=self._peak_mode(),  # type: ignore[arg-type]
                    prominence_frac=float(self.prom_spin.value()),
                    min_distance=int(self.distance_spin.value()),
                    max_peaks=int(self.max_peaks_spin.value()),
                )
            except Exception as exc:
                self._show_error(str(exc))
                return
            self._cached_d2 = None

        idx = np.asarray(mask_idx[idx_local], dtype=int) if idx_local.size else np.asarray([], dtype=int)
        self.peak_indices = idx
        self._peak_gate_idx = int(np.argmin(np.abs(self.current_cube.gate - float(gate_used))))
        self.fit_result = None
        self._refresh_peak_delete_controls()
        self.gate_spin.blockSignals(True)
        self.gate_spin.setValue(gate_used)
        self.gate_spin.blockSignals(False)
        self.current_gate = gate_used
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
        sg_win = int(self.d2_sg_win_spin.value())
        sg_poly = int(self.d2_sg_poly_spin.value())
        d2_prom = float(self.d2_prom_spin.value())
        d2_dist = int(self.d2_dist_spin.value())
        d2_sep = float(self.d2_sep_spin.value())
        d2_max = int(self.d2_max_peaks_spin.value())
        d2_tgt = int(self.d2_target_spin.value())

        peak_map: dict[int, np.ndarray] = {}
        ok_count = 0
        peak_total = 0

        for gi, gate in enumerate(cube.gate):
            y = np.asarray(cube.z[gi, :], dtype=float)
            yr = np.asarray(y[mask], dtype=float)

            try:
                if use_d2:
                    idx_local, _ = find_peaks_second_derivative(
                        xr, yr,
                        mode=peak_mode,  # type: ignore[arg-type]
                        sg_window=sg_win, sg_polyorder=sg_poly,
                        d2_prom_frac=d2_prom, d2_distance=d2_dist,
                        d2_sep_ev=d2_sep, max_peaks=d2_max, target_count=d2_tgt,
                    )
                else:
                    if self.peak_smooth_chk.isChecked():
                        win = _clamp_sg_window(yr.size, int(self.peak_smooth_win_spin.value()))
                        if win >= 3 and yr.size > win:
                            yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")
                    idx_local = find_peak_indices(
                        xr, yr,
                        mode=peak_mode,  # type: ignore[arg-type]
                        prominence_frac=prom, min_distance=min_dist, max_peaks=max_peaks_o,
                    )
            except Exception:
                continue

            if idx_local.size == 0:
                continue
            ok_count += 1
            peak_total += int(idx_local.size)
            peak_map[gi] = np.asarray(mask_idx[idx_local], dtype=int)

            if gi % 25 == 0:
                self.statusBar().showMessage(f"Batch peak finding… gate {gi+1}/{cube.gate.size}")
                QApplication.processEvents()

        self.batch_peak_map = peak_map
        current_gate_idx = self._current_gate_index()
        self.peak_indices = np.asarray(peak_map.get(current_gate_idx, []), dtype=int)
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
        default = Path(self.current_folder) / f"{cube.title}_peaks.csv"
        out_path, _ = QFileDialog.getSaveFileName(self, "Save Peaks CSV", str(default), "CSV Files (*.csv)")
        if not out_path:
            return
        rows: list[tuple[float, float]] = []
        if self.batch_peak_map:
            # All-gates data
            for gi, idxs in sorted(self.batch_peak_map.items()):
                gate_v = float(cube.gate[gi])
                arr = np.asarray(idxs, dtype=int)
                arr = arr[(arr >= 0) & (arr < cube.energy.size)]
                for i in arr:
                    rows.append((gate_v, float(cube.energy[i])))
        else:
            # Current gate only
            valid = self._current_trace_peak_indices()
            if valid.size == 0:
                self._show_error("No peaks to save. Find peaks first.")
                return
            gate_used, _ = self._nearest_spectrum(self.current_gate)
            for i in valid:
                rows.append((gate_used, float(cube.energy[i])))
        try:
            with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
                wr = csv.writer(fh)
                wr.writerow(["gate", "peak_x", "analysis_axis", "original_x", "original_axis"])
                for gate_v, e_v in rows:
                    idx = int(np.argmin(np.abs(cube.energy - e_v)))
                    wr.writerow([
                        f"{gate_v:.10g}",
                        f"{e_v:.10g}",
                        cube.energy_label,
                        f"{cube.x_original[idx]:.10g}",
                        cube.x_original_label,
                    ])
        except Exception as exc:
            self._show_error(f"Failed to save peaks CSV: {exc}")
            return
        self.statusBar().showMessage(f"Saved peaks CSV: {out_path} ({len(rows)} rows)")

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
        self._sync_current_trace_peaks_to_batch_map()
        self.fit_result = None
        self._refresh_peak_delete_controls()
        gate_used, y = self._nearest_spectrum(self.current_gate)
        self._update_spectrum_only()
        self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")

    def _on_clear_peaks(self) -> None:
        if self.current_cube is None:
            return
        self.peak_indices = np.asarray([], dtype=int)
        self._peak_gate_idx = self._current_gate_index()
        self._sync_current_trace_peaks_to_batch_map()
        self.fit_result = None
        self._refresh_peak_delete_controls()
        gate_used, y = self._nearest_spectrum(self.current_gate)
        self._update_spectrum_only()
        self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")

    def _on_clear_batch_peaks(self) -> None:
        self.batch_peak_map = {}
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
        gate_used, y = self._nearest_spectrum(self.gate_spin.value())
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
            self._show_error(
                "No peak seeds in fit range.\n"
                "Run 'Find Peaks' in the Find Peaks tab first,\n"
                "or load a seeds CSV in the Lorentz Fit tab."
            )
            return

        lw_min = float(self.lw_min_spin.value())
        lw_max_v = float(self.lw_max_spin.value())
        lw_max = lw_max_v if lw_max_v > lw_min else 0.0
        peak_move = float(self.peak_move_limit_spin.value())
        maxfev = int(self.maxfev_spin.value())

        try:
            res = fit_multi_peak(
                xr, yr,
                centers_hint=centers_in,
                model=self.fit_model_combo.currentText(),  # type: ignore[arg-type]
                mode=self._peak_mode(),  # type: ignore[arg-type]
                linewidth_min=lw_min,
                linewidth_max=lw_max,
                peak_move_limit=peak_move,
                maxfev=maxfev,
            )
        except Exception as exc:
            self._show_error(f"Fit failed: {exc}")
            return

        self.fit_result = res
        self._fit_gate_idx = self._current_gate_index()
        self.batch_fit_map[self._fit_gate_idx] = np.asarray(res.centers, dtype=float)
        self.batch_fit_result_map[self._fit_gate_idx] = res
        self._batch_fit_export_rows = [
            self._fit_result_to_export_row(float(self.current_gate), res.model, self.current_cube.energy_label, res, status="ok")
        ]
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
        self._update_spectrum_only()

    def _on_batch_fit_all_gates(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        cube = self.current_cube
        use_csv = self._seeds_from_csv_radio.isChecked() and self._csv_seeds_map is not None
        if not use_csv and not self.batch_peak_map:
            self._show_error(
                "No seeds available for batch fit.\n"
                "Run 'Find Peaks – All Gates' in Find Peaks tab first,\n"
                "or load a seeds CSV in the Lorentz Fit tab."
            )
            return

        model = str(self.fit_model_combo.currentText())
        peak_mode = self._peak_mode()
        x = cube.energy
        mask = self._fit_mask(x)
        xr_base = np.asarray(x[mask], dtype=float)
        if xr_base.size < 5:
            self._show_error("Fit range is too narrow for batch fit.")
            return

        lw_min = float(self.lw_min_spin.value())
        lw_max_v = float(self.lw_max_spin.value())
        lw_max = lw_max_v if lw_max_v > lw_min else 0.0
        peak_move = float(self.peak_move_limit_spin.value())
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

            try:
                res = fit_multi_peak(
                    xr_base, yr,
                    centers_hint=centers_in,
                    model=model,  # type: ignore[arg-type]
                    mode=peak_mode,  # type: ignore[arg-type]
                    linewidth_min=lw_min, linewidth_max=lw_max,
                    peak_move_limit=peak_move, maxfev=maxfev,
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
        self._sync_batch_fit_coords()
        if self.current_cube.gate.size:
            current_gate_idx = self._current_gate_index()
            self.fit_result = self.batch_fit_result_map.get(current_gate_idx)
            self._fit_gate_idx = current_gate_idx if self.fit_result is not None else None
        self.statusBar().showMessage(f"Batch fit done: OK={ok_count}, fail={fail_count}")
        self.analysis_text.setPlainText(
            f"Batch fit done.\nGates: {cube.gate.size}  OK: {ok_count}  Failed: {fail_count}\nUse 'Export Fit CSV' to save the results."
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
        except Exception as exc:
            self._show_error(f"Failed to load seeds CSV: {exc}")

    # =========================================================================
    #  CSV export
    # =========================================================================

    def _on_export_fit_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        active_fit = self._active_fit_result()
        has_batch = bool(self._batch_fit_export_rows)
        if not has_batch and active_fit is None:
            self._show_error("Run a fit first.")
            return
        cube = self.current_cube
        gate_used, _ = self._nearest_spectrum(self.current_gate)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if has_batch:
            default = Path(self.current_folder) / f"{cube.title}_batch_fit_{stamp}.csv"
        else:
            default = Path(self.current_folder) / f"{cube.title}_gate_{gate_used:.6g}_{active_fit.model}_fit_{stamp}.csv"
        out_path, _ = QFileDialog.getSaveFileName(self, "Export Fit CSV", str(default), "CSV Files (*.csv)")
        if not out_path:
            return
        try:
            with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
                if has_batch:
                    rows = [dict(row) for row in self._batch_fit_export_rows]
                else:
                    rows = [
                        self._fit_result_to_export_row(
                            float(gate_used), active_fit.model, cube.energy_label, active_fit, status="ok"
                        )
                    ]
                wr = csv.DictWriter(fh, fieldnames=self._export_fieldnames(rows))
                wr.writeheader()
                if has_batch:
                    wr.writerows(rows)
                else:
                    wr.writerows(rows)
        except Exception as exc:
            self._show_error(f"Failed to export fit CSV: {exc}")
            return
        self.statusBar().showMessage(f"Exported fit CSV: {out_path}")

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
        gs = self.figure.add_gridspec(
            nrows=2, ncols=2,
            width_ratios=[1.0, 0.04],
            height_ratios=[1.0, 1.0],
            wspace=0.12, hspace=0.28,
        )
        self._heat_ax = self.figure.add_subplot(gs[0, 0])
        cax = self.figure.add_subplot(gs[0, 1])
        self._spec_ax = self.figure.add_subplot(gs[1, 0], sharex=self._heat_ax)
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
            cmap=self.cmap_combo.currentText(),
            norm=Normalize(vmin=float(vmin), vmax=float(vmax)),
        )
        self.figure.colorbar(im, cax=cax, label="Intensity (a.u.)")
        self._heat_ax.set_title(cube.title)
        self._heat_ax.set_xlabel(cube.energy_label)
        self._heat_ax.set_ylabel(cube.gate_label)
        xmin, xmax = self._ordered_pair(float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        ymin, ymax = self._ordered_pair(float(self.ymin_spin.value()), float(self.ymax_spin.value()))
        self._heat_ax.set_xlim((xmin, xmax))
        self._heat_ax.set_ylim((ymin, ymax))
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

    def _update_spectrum_only(self) -> None:
        if self.current_cube is None or self._spec_ax is None or self._heat_ax is None:
            return
        gate_used, y = self._nearest_spectrum(self.current_gate)
        x = self.current_cube.energy
        trace_peak_indices = self._current_trace_peak_indices()
        fit_centers = self._current_fit_centers()
        active_fit = self._active_fit_result()
        self._spec_ax.clear()
        self._spec_ax.plot(x, y, color="#1f77b4", lw=1.4, label="Spectrum")
        self._spec_ax.set_title(f"Spectrum @ {gate_used:.6g}")
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
            self._spec_ax.vlines(px, y0, y1, colors="#d62728", linestyles=":", linewidth=0.9, alpha=0.55, zorder=2)
            for i, (xi, yi) in enumerate(zip(px, py)):
                if int(trace_peak_indices[i]) in lasso_energy_idxs:
                    self._spec_ax.scatter([xi], [yi], s=104, facecolors="#fff176",
                                          edgecolors="#ff00c8", linewidths=1.6, zorder=6)
                elif sel_idx is not None and i == sel_idx:
                    # Selected via spinbox: orange
                    self._spec_ax.scatter([xi], [yi], s=88, color="#ff9800",
                                          edgecolors="#111111", linewidths=0.9, zorder=5)
                else:
                    self._spec_ax.scatter([xi], [yi], s=36, color="#d62728", zorder=4)
            # Add legend proxy once
            self._spec_ax.scatter([], [], s=36, color="#d62728", label="Peaks")
            if lasso_energy_idxs:
                self._spec_ax.scatter([], [], s=104, facecolors="#fff176",
                                      edgecolors="#ff00c8", linewidths=1.6, label="Heatmap Lasso Selected")

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
                ha="left", va="top", fontsize=8, color="#111111",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#444444", alpha=0.82),
                zorder=10,
            )

        handles, labels = self._spec_ax.get_legend_handles_labels()
        if handles:
            self._spec_ax.legend(loc="upper right", fontsize=7)

        self._spec_ax.set_xlim((view_xmin, view_xmax))
        self._spec_ax.set_ylim((y0, y1))

        self.current_gate = gate_used
        self._sync_gate_controls()
        self._update_gate_line(gate_used)
        self._update_heatmap_peak_labels(gate_used, x)
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

        # Batch peaks (all gates, cyan circles)
        if self.batch_peak_coords is not None:
            bx, by = self.batch_peak_coords
            if bx.size and by.size:
                selected_ids = set(self._lasso_selected_peak_ids)
                selected_mask = np.asarray([rec in selected_ids for rec in self.batch_peak_records], dtype=bool)
                unselected_mask = ~selected_mask if selected_mask.size else np.asarray([], dtype=bool)
                if selected_mask.size == 0 or np.any(unselected_mask):
                    self._heat_batch_peak_artists.append(
                        self._heat_ax.scatter(
                            bx[unselected_mask] if selected_mask.size else bx,
                            by[unselected_mask] if selected_mask.size else by,
                            s=20, marker="o",
                            facecolors="none", edgecolors="#00e5ff", linewidths=0.85,
                            alpha=0.98, zorder=24, label="All-Gate Peaks",
                        )
                    )
                if selected_mask.size and np.any(selected_mask):
                    self._heat_batch_peak_artists.append(
                        self._heat_ax.scatter(
                            bx[selected_mask], by[selected_mask], s=64, marker="o",
                            facecolors="#fff176", edgecolors="#ff00c8", linewidths=1.6,
                            alpha=1.0, zorder=25, label="Lasso Selected",
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
        if valid.size == 0:
            return
        px = np.asarray(x[valid], dtype=float)
        py = np.full_like(px, float(gate_used), dtype=float)
        sel_idx = self._selected_peak_array_index()

        if sel_idx is not None and 0 <= sel_idx < px.size:
            mask = np.ones(px.size, dtype=bool)
            mask[sel_idx] = False
            if np.any(mask):
                self._heat_peak_artists.append(
                    self._heat_ax.scatter(
                        px[mask], py[mask], s=42, marker="o",
                        facecolors="#ffeb3b", edgecolors="#1f1f1f", linewidths=0.8,
                        zorder=30, label="Peaks@Gate",
                    )
                )
            self._heat_peak_artists.append(
                self._heat_ax.scatter(
                    [px[sel_idx]], [py[sel_idx]], s=88, marker="o",
                    facecolors="#ff9800", edgecolors="#111111", linewidths=0.9,
                    zorder=31, label="Selected@Gate",
                )
            )
        else:
            self._heat_peak_artists.append(
                self._heat_ax.scatter(
                    px, py, s=42, marker="o",
                    facecolors="#ffeb3b", edgecolors="#1f1f1f", linewidths=0.8,
                    zorder=30, label="Peaks@Gate",
                )
            )

        y0, y1 = self._heat_ax.get_ylim()
        yoff = 0.018 * abs(float(y1 - y0))
        for i, xv in enumerate(px, start=1):
            is_sel = (sel_idx is not None) and (i - 1 == sel_idx)
            txt = self._heat_ax.text(
                float(xv), float(gate_used + yoff), str(i),
                fontsize=(9 if is_sel else 8),
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
                if self._remove_manual_peak_near(float(event.xdata)):
                    gate_used, y = self._nearest_spectrum(self.current_gate)
                    self._update_spectrum_only()
                    self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")
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
        self.current_gate = float(g[idx])
        if self.batch_peak_map:
            self.peak_indices = np.asarray(self.batch_peak_map.get(idx, []), dtype=int)
            self._peak_gate_idx = idx
            self._refresh_peak_delete_controls()
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
            self._peak_gate_idx = gate_idx
        x = self.current_cube.energy
        idx = int(np.argmin(np.abs(x - float(x_click))))
        if self.peak_indices.size and np.any(self.peak_indices == idx):
            return False
        merged = np.concatenate([self.peak_indices, np.asarray([idx], dtype=int)])
        self.peak_indices = np.asarray(np.unique(merged), dtype=int)
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
            if np.any(keep):
                new_map[int(gi)] = np.asarray(arr[keep], dtype=int)
        self.batch_peak_map = new_map
        self._sync_batch_peak_coords()
        return deleted
