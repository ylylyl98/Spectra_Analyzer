from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.colors import Normalize
from scipy.signal import savgol_filter
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFrame,
    QSizePolicy,
)

from .dat_io import DataCube, list_dat_files, list_dat_folders, load_dat_cube
from .fitting import FitResult, find_peak_indices, fit_multi_peak


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spectra Analysis (PySide6)")
        self.setMinimumSize(1200, 760)
        self.current_root = ""
        self.current_folder = ""
        self.current_cube: DataCube | None = None
        self.current_gate = 0.0
        self.peak_indices: np.ndarray = np.asarray([], dtype=int)
        self.batch_peak_coords: tuple[np.ndarray, np.ndarray] | None = None
        self.batch_peak_map: dict[int, np.ndarray] = {}
        self.fit_result: FitResult | None = None
        self._view_lock_controls: dict[QDoubleSpinBox, QCheckBox] = {}
        self._fit_lock_controls: dict[QDoubleSpinBox, QCheckBox] = {}
        self._heat_ax = None
        self._spec_ax = None
        self._gate_line = None
        self._heat_peak_artists: list[Any] = []
        self._heat_peak_texts: list[Any] = []
        self._heat_batch_peak_artists: list[Any] = []
        self._canvas_click_cid: int | None = None
        self._canvas_release_cid: int | None = None
        self._batch_delete_circle_start: tuple[float, float] | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        split = QSplitter(Qt.Horizontal)
        left_panel = self._build_left_panel()
        left_panel.setMinimumWidth(420)
        split.addWidget(left_panel)
        split.addWidget(self._build_plot_panel())
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([450, 860])
        outer = QHBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(split)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Select a root folder to start.")

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

    def _build_left_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(6)

        src = QGroupBox("")
        src_grid = QGridLayout(src)
        src_grid.setContentsMargins(6, 6, 6, 6)
        src_grid.setHorizontalSpacing(6)
        src_grid.setVerticalSpacing(6)
        self.root_edit = QLineEdit()
        self.root_edit.setReadOnly(True)
        self.browse_btn = QPushButton("Browse Root Folder")
        self.folder_combo = QComboBox()
        self.folder_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.folder_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        src_grid.addWidget(self.root_edit, 0, 0, 1, 2)
        src_grid.addWidget(self.browse_btn, 1, 0, 1, 2)
        src_grid.addWidget(QLabel("Process Folder"), 2, 0)
        src_grid.addWidget(self.folder_combo, 2, 1)
        self.process_folder_note = QLabel(
            "Why process folder: a root can contain multiple dat subfolders; choose one so file list stays scoped."
        )
        self.process_folder_note.setWordWrap(True)
        self.process_folder_note.setStyleSheet("color:#54606c; font-size: 11px;")
        src_grid.addWidget(self.process_folder_note, 3, 0, 1, 2)
        lay.addWidget(self._make_expander("Data Source", src, expanded=False))

        files = QGroupBox("")
        files_l = QVBoxLayout(files)
        files_l.setContentsMargins(6, 6, 6, 6)
        files_l.setSpacing(6)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.file_list.setMaximumHeight(110)
        self.load_btn = QPushButton("Load Selected File")
        files_l.addWidget(self.file_list)
        files_l.addWidget(self.load_btn)
        lay.addWidget(self._make_expander("DAT Files", files, expanded=False))

        params = QGroupBox("")
        params_form = QFormLayout(params)
        params_form.setContentsMargins(6, 6, 6, 6)
        params_form.setSpacing(6)
        params_form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["turbo", "viridis", "plasma", "inferno", "magma", "cividis", "RdBu_r"])
        self.cmap_combo.setCurrentText("RdBu_r")
        self.gate_spin = QDoubleSpinBox()
        self.gate_spin.setDecimals(6)
        self.gate_spin.setRange(-1e12, 1e12)
        self.gate_spin.setSingleStep(0.01)
        self.apply_gate_btn = QPushButton("Apply Gate")
        self.xmin_spin = QDoubleSpinBox()
        self.xmax_spin = QDoubleSpinBox()
        self.ymin_spin = QDoubleSpinBox()
        self.ymax_spin = QDoubleSpinBox()
        self.vmin_spin = QDoubleSpinBox()
        self.vmax_spin = QDoubleSpinBox()
        for spin in (
            self.xmin_spin,
            self.xmax_spin,
            self.ymin_spin,
            self.ymax_spin,
            self.vmin_spin,
            self.vmax_spin,
        ):
            spin.setDecimals(6)
            spin.setRange(-1e12, 1e12)
            spin.setSingleStep(0.01)
            spin.setMaximumWidth(150)
        self.gate_spin.setMaximumWidth(150)
        self.cmap_combo.setMaximumWidth(150)
        self.apply_view_btn = QPushButton("Apply View")
        self.auto_view_btn = QPushButton("Auto View")
        view_btn_row = QWidget()
        view_btn_l = QHBoxLayout(view_btn_row)
        view_btn_l.setContentsMargins(0, 0, 0, 0)
        view_btn_l.setSpacing(4)
        view_btn_l.addWidget(self.apply_view_btn)
        view_btn_l.addWidget(self.auto_view_btn)
        params_form.addRow("Colormap", self.cmap_combo)
        self.xmin_lock = QCheckBox("Fix")
        self.xmax_lock = QCheckBox("Fix")
        self.ymin_lock = QCheckBox("Fix")
        self.ymax_lock = QCheckBox("Fix")
        self.vmin_lock = QCheckBox("Fix")
        self.vmax_lock = QCheckBox("Fix")
        self._view_lock_controls = {
            self.xmin_spin: self.xmin_lock,
            self.xmax_spin: self.xmax_lock,
            self.ymin_spin: self.ymin_lock,
            self.ymax_spin: self.ymax_lock,
            self.vmin_spin: self.vmin_lock,
            self.vmax_spin: self.vmax_lock,
        }
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

        analysis = QGroupBox("")
        an = QVBoxLayout(analysis)
        an.setContentsMargins(6, 6, 6, 6)
        an.setSpacing(6)

        peak_box = QGroupBox("Find Peaks")
        peak_l = QVBoxLayout(peak_box)
        peak_l.setContentsMargins(6, 6, 6, 6)
        peak_l.setSpacing(6)
        self.peak_mode_combo = QComboBox()
        self.peak_mode_combo.addItems(["Peaks", "Dips"])
        self.prom_spin = QDoubleSpinBox()
        self.prom_spin.setDecimals(3)
        self.prom_spin.setRange(0.0, 1.0)
        self.prom_spin.setSingleStep(0.01)
        self.prom_spin.setValue(0.05)
        self.distance_spin = QSpinBox()
        self.distance_spin.setRange(1, 500)
        self.distance_spin.setValue(5)
        self.max_peaks_spin = QSpinBox()
        self.max_peaks_spin.setRange(1, 20)
        self.max_peaks_spin.setValue(6)
        self.find_btn = QPushButton("Find Peaks")
        self.batch_find_btn = QPushButton("Find Peaks All Gates")
        self.circle_delete_btn = QPushButton("Circle Delete All-Gate Peaks")
        self.circle_delete_btn.setCheckable(True)
        self.clear_batch_peaks_btn = QPushButton("Clear All-Gate Peaks")
        self.manual_hint = QLabel("Spectrum click: Left=Add peak, Right=Remove nearest peak. Heatmap drag with circle delete on: remove all-gate peaks inside circle.")
        self.manual_hint.setWordWrap(True)
        self.peak_delete_spin = QSpinBox()
        self.peak_delete_spin.setRange(1, 1)
        self.peak_delete_spin.setValue(1)
        self.peak_delete_spin.setMaximumWidth(70)
        self.delete_peak_btn = QPushButton("Delete #")
        self.clear_peaks_btn = QPushButton("Clear Peaks")
        delete_row = QWidget()
        delete_row_l = QHBoxLayout(delete_row)
        delete_row_l.setContentsMargins(0, 0, 0, 0)
        delete_row_l.setSpacing(4)
        delete_row_l.addWidget(QLabel("Peak #"))
        delete_row_l.addWidget(self.peak_delete_spin)
        delete_row_l.addWidget(self.delete_peak_btn)
        delete_row_l.addWidget(self.clear_peaks_btn)
        delete_row_l.addStretch(1)
        batch_peak_row = QWidget()
        batch_peak_row_l = QHBoxLayout(batch_peak_row)
        batch_peak_row_l.setContentsMargins(0, 0, 0, 0)
        batch_peak_row_l.setSpacing(4)
        batch_peak_row_l.addWidget(self.batch_find_btn)
        batch_peak_row_l.addWidget(self.circle_delete_btn)
        batch_peak_row_l.addWidget(self.clear_batch_peaks_btn)

        peak_form = QFormLayout()
        peak_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        peak_form.addRow("Peak Type", self.peak_mode_combo)
        peak_form.addRow("Prominence", self.prom_spin)
        peak_form.addRow("Min Distance", self.distance_spin)
        peak_form.addRow("Max Peaks", self.max_peaks_spin)
        self.peak_smooth_chk = QCheckBox("Smooth for peak finding")
        self.peak_smooth_win_spin = QSpinBox()
        self.peak_smooth_win_spin.setRange(3, 501)
        self.peak_smooth_win_spin.setValue(11)
        self.peak_smooth_win_spin.setMaximumWidth(75)
        peak_smooth_row = QWidget()
        peak_smooth_l = QHBoxLayout(peak_smooth_row)
        peak_smooth_l.setContentsMargins(0, 0, 0, 0)
        peak_smooth_l.setSpacing(4)
        peak_smooth_l.addWidget(self.peak_smooth_chk)
        peak_smooth_l.addWidget(QLabel("Win"))
        peak_smooth_l.addWidget(self.peak_smooth_win_spin)
        peak_smooth_l.addStretch(1)
        peak_form.addRow("Smoothing", peak_smooth_row)
        peak_l.addLayout(peak_form)
        peak_l.addWidget(self.find_btn)
        peak_l.addWidget(batch_peak_row)
        peak_l.addWidget(self.manual_hint)
        peak_l.addWidget(delete_row)

        fit_box = QGroupBox("Lorentz Fitting")
        fit_l = QVBoxLayout(fit_box)
        fit_l.setContentsMargins(6, 6, 6, 6)
        fit_l.setSpacing(6)
        fit_row = QWidget()
        fit_row_l = QHBoxLayout(fit_row)
        fit_row_l.setContentsMargins(0, 0, 0, 0)
        fit_row_l.setSpacing(4)
        self.fit_model_combo = QComboBox()
        self.fit_model_combo.addItems(["Lorentzian", "Gaussian"])
        self.fit_model_combo.setCurrentText("Lorentzian")
        self.fit_model_combo.setMaximumWidth(130)
        self.fit_btn = QPushButton("Fit Current Gate")
        self.clear_fit_btn = QPushButton("Clear Fit")
        self.export_fit_btn = QPushButton("Export Fit CSV")
        self.batch_fit_btn = QPushButton("Batch Fit All Gates")
        self.export_spec_btn = QPushButton("Export Current Spectrum CSV")
        fit_row_l.addWidget(QLabel("Model"))
        fit_row_l.addWidget(self.fit_model_combo)
        fit_row_l.addWidget(self.fit_btn)
        fit_row_l.addWidget(self.clear_fit_btn)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.fit_xmin_spin = QDoubleSpinBox()
        self.fit_xmax_spin = QDoubleSpinBox()
        for spin in (self.fit_xmin_spin, self.fit_xmax_spin):
            spin.setDecimals(6)
            spin.setRange(-1e12, 1e12)
            spin.setSingleStep(0.01)
            spin.setMaximumWidth(110)
        self.fit_xmin_lock = QCheckBox("Fix")
        self.fit_xmax_lock = QCheckBox("Fix")
        self._fit_lock_controls = {
            self.fit_xmin_spin: self.fit_xmin_lock,
            self.fit_xmax_spin: self.fit_xmax_lock,
        }
        self.fit_full_btn = QPushButton("Full")
        fit_xmax_row = QWidget()
        fit_xmax_l = QHBoxLayout(fit_xmax_row)
        fit_xmax_l.setContentsMargins(0, 0, 0, 0)
        fit_xmax_l.setSpacing(4)
        fit_xmax_l.addWidget(self._view_spin_row(self.fit_xmax_spin, self.fit_xmax_lock))
        fit_xmax_l.addWidget(self.fit_full_btn)
        form.addRow("Fit xmin", self._view_spin_row(self.fit_xmin_spin, self.fit_xmin_lock))
        form.addRow("Fit xmax", fit_xmax_row)

        self.fit_smooth_chk = QCheckBox("Smooth for fitting")
        self.fit_smooth_win_spin = QSpinBox()
        self.fit_smooth_win_spin.setRange(3, 501)
        self.fit_smooth_win_spin.setValue(11)
        self.fit_smooth_win_spin.setMaximumWidth(75)
        smooth_row = QWidget()
        smooth_l = QHBoxLayout(smooth_row)
        smooth_l.setContentsMargins(0, 0, 0, 0)
        smooth_l.setSpacing(4)
        smooth_l.addWidget(self.fit_smooth_chk)
        smooth_l.addWidget(QLabel("Win"))
        smooth_l.addWidget(self.fit_smooth_win_spin)
        smooth_l.addStretch(1)
        form.addRow("Smoothing", smooth_row)
        fit_l.addLayout(form)
        fit_l.addWidget(fit_row)
        fit_l.addWidget(self.export_spec_btn)
        fit_l.addWidget(self.export_fit_btn)
        fit_l.addWidget(self.batch_fit_btn)

        an.addWidget(peak_box)
        an.addWidget(fit_box)
        self.analysis_text = QPlainTextEdit()
        self.analysis_text.setReadOnly(True)
        self.analysis_text.setMaximumHeight(130)
        an.addWidget(self.analysis_text)
        lay.addWidget(self._make_expander("Spectrum Analysis", analysis, expanded=True))
        lay.addStretch(1)

        for w in [
            self.root_edit,
            self.browse_btn,
            self.folder_combo,
            self.load_btn,
            self.cmap_combo,
            self.gate_spin,
            self.xmin_spin,
            self.xmax_spin,
            self.ymin_spin,
            self.ymax_spin,
            self.vmin_spin,
            self.vmax_spin,
            self.apply_gate_btn,
            self.apply_view_btn,
            self.auto_view_btn,
            self.find_btn,
            self.circle_delete_btn,
            self.clear_batch_peaks_btn,
            self.delete_peak_btn,
            self.clear_peaks_btn,
            self.export_spec_btn,
            self.export_fit_btn,
            self.batch_find_btn,
            self.batch_fit_btn,
        ]:
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            if hasattr(w, "setMinimumWidth"):
                w.setMinimumWidth(0)

        self.browse_btn.clicked.connect(self._on_browse_root)
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        self.load_btn.clicked.connect(self._on_load_selected_file)
        self.file_list.itemDoubleClicked.connect(lambda _item: self._on_load_selected_file())
        self.apply_gate_btn.clicked.connect(self._on_apply_gate)
        self.gate_spin.valueChanged.connect(self._on_gate_spin_changed)
        self.cmap_combo.currentTextChanged.connect(lambda _t: self._plot_cube())
        for spin in (
            self.xmin_spin,
            self.xmax_spin,
            self.ymin_spin,
            self.ymax_spin,
            self.vmin_spin,
            self.vmax_spin,
        ):
            spin.valueChanged.connect(lambda _v: self._plot_cube())
        self.find_btn.clicked.connect(self._on_find_peaks)
        self.delete_peak_btn.clicked.connect(self._on_delete_peak_by_number)
        self.clear_peaks_btn.clicked.connect(self._on_clear_peaks)
        self.peak_delete_spin.valueChanged.connect(lambda _v: self._update_spectrum_only())
        self.fit_btn.clicked.connect(self._on_fit)
        self.clear_fit_btn.clicked.connect(self._on_clear_fit)
        self.apply_view_btn.clicked.connect(self._plot_cube)
        self.auto_view_btn.clicked.connect(self._on_auto_view)
        self.export_spec_btn.clicked.connect(self._on_export_spectrum_csv)
        self.export_fit_btn.clicked.connect(self._on_export_fit_csv)
        self.batch_find_btn.clicked.connect(self._on_batch_find_peaks_all_gates)
        self.clear_batch_peaks_btn.clicked.connect(self._on_clear_batch_peaks)
        self.circle_delete_btn.toggled.connect(self._on_toggle_circle_delete_mode)
        self.batch_fit_btn.clicked.connect(self._on_batch_fit_all_gates)
        self.fit_full_btn.clicked.connect(self._on_set_fit_full_range)
        self.fit_xmin_spin.valueChanged.connect(lambda _v: self._update_spectrum_only())
        self.fit_xmax_spin.valueChanged.connect(lambda _v: self._update_spectrum_only())
        self._refresh_peak_delete_controls()

        scroll.setWidget(panel)
        return scroll

    def _build_plot_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        self.figure = Figure(figsize=(8.0, 6.0), tight_layout=False)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.canvas, 1)
        return panel

    def _show_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Error", msg)

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

    def _sync_batch_peak_coords(self) -> None:
        if self.current_cube is None or not self.batch_peak_map:
            self.batch_peak_coords = None
            return
        x = self.current_cube.energy
        gates = self.current_cube.gate
        px: list[float] = []
        py: list[float] = []
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
        self.batch_peak_map = clean_map
        if px:
            self.batch_peak_coords = (np.asarray(px, dtype=float), np.asarray(py, dtype=float))
        else:
            self.batch_peak_coords = None

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
        try:
            self.current_cube = load_dat_cube(path)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.peak_indices = np.asarray([], dtype=int)
        self.batch_peak_map = {}
        self.batch_peak_coords = None
        self.fit_result = None
        self._refresh_peak_delete_controls()
        gate = float(np.nanmedian(self.current_cube.gate))
        self.gate_spin.blockSignals(True)
        self.gate_spin.setValue(gate)
        self.gate_spin.blockSignals(False)
        self.current_gate = gate
        self._set_view_limits_to_auto()
        self._set_fit_range_to_full()
        self._plot_cube()
        self.analysis_text.setPlainText("Loaded. Use heatmap click to pick gate. Use spectrum clicks for manual peaks.")
        self.statusBar().showMessage(f"Loaded: {path.name}")

    def _nearest_spectrum(self, gate_value: float) -> tuple[float, np.ndarray]:
        if self.current_cube is None:
            raise ValueError("No data loaded.")
        g = self.current_cube.gate
        idx = int(np.argmin(np.abs(g - float(gate_value))))
        return float(g[idx]), np.asarray(self.current_cube.z[idx, :], dtype=float)

    def _on_apply_gate(self) -> None:
        if self.current_cube is None:
            return
        self.current_gate = float(self.gate_spin.value())
        self._update_spectrum_only()

    def _on_auto_view(self) -> None:
        self._set_view_limits_to_auto()
        self._plot_cube()

    def _on_gate_spin_changed(self, value: float) -> None:
        self.current_gate = float(value)

    def _on_toggle_circle_delete_mode(self, on: bool) -> None:
        self._batch_delete_circle_start = None
        if on:
            self.statusBar().showMessage("Circle delete mode: drag on the heatmap to remove all-gate peaks inside the circle.")
        else:
            self.statusBar().showMessage("Circle delete mode off.")

    def _peak_mode(self) -> str:
        return str(self.peak_mode_combo.currentText())

    def _on_set_fit_full_range(self) -> None:
        self._set_fit_range_to_full()
        self._update_spectrum_only()

    def _set_fit_range_to_full(self) -> None:
        if self.current_cube is None:
            return
        xmin = float(np.nanmin(self.current_cube.energy))
        xmax = float(np.nanmax(self.current_cube.energy))
        for spin, val in (
            (self.fit_xmin_spin, xmin),
            (self.fit_xmax_spin, xmax),
        ):
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
        if finite.size == 0:
            vmin, vmax = 0.0, 1.0
        else:
            vmin, vmax = np.nanpercentile(finite, [0.5, 99.5])
        xmin = float(np.nanmin(cube.energy))
        xmax = float(np.nanmax(cube.energy))
        ymin = float(np.nanmin(cube.gate))
        ymax = float(np.nanmax(cube.gate))
        for spin, val in (
            (self.xmin_spin, xmin),
            (self.xmax_spin, xmax),
            (self.ymin_spin, ymin),
            (self.ymax_spin, ymax),
            (self.vmin_spin, float(vmin)),
            (self.vmax_spin, float(vmax)),
        ):
            lock_box = self._view_lock_controls.get(spin)
            if lock_box is not None and lock_box.isChecked():
                continue
            spin.blockSignals(True)
            spin.setValue(float(val))
            spin.blockSignals(False)

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

    def _fit_mask(self, x: np.ndarray) -> np.ndarray:
        xmin, xmax = self._fit_range_bounds()
        mask = (x >= xmin) & (x <= xmax) & np.isfinite(x)
        return np.asarray(mask, dtype=bool)

    @staticmethod
    def _clamp_sg_window(n: int, requested: int) -> int:
        win = max(3, int(requested))
        if win % 2 == 0:
            win += 1
        if win > n:
            win = n if n % 2 == 1 else n - 1
        return max(3, int(win))

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
            win = self._clamp_sg_window(xr.size, requested)
            if win >= 3 and xr.size > win:
                yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")
        return xr, yr, mask

    def _on_find_peaks(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        gate_used, y = self._nearest_spectrum(self.gate_spin.value())
        x = self.current_cube.energy
        mode = self._peak_mode()
        try:
            xr, yr, mask = self._analysis_xy(
                x,
                y,
                smooth=self.peak_smooth_chk.isChecked(),
                smooth_window=int(self.peak_smooth_win_spin.value()),
            )
            idx_local = find_peak_indices(
                xr,
                yr,
                mode=mode,  # type: ignore[arg-type]
                prominence_frac=float(self.prom_spin.value()),
                min_distance=int(self.distance_spin.value()),
                max_peaks=int(self.max_peaks_spin.value()),
            )
            idx = np.where(mask)[0][idx_local]
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.peak_indices = idx
        self.fit_result = None
        self._refresh_peak_delete_controls()
        self.gate_spin.blockSignals(True)
        self.gate_spin.setValue(gate_used)
        self.gate_spin.blockSignals(False)
        self.current_gate = gate_used
        self._update_spectrum_only()
        self._update_peak_text(gate_used, x, y, title_prefix="Auto-detected")

    def _on_fit(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        gate_used, y = self._nearest_spectrum(self.gate_spin.value())
        x = self.current_cube.energy
        try:
            xr, yr, mask = self._analysis_xy(
                x,
                y,
                smooth=self.fit_smooth_chk.isChecked(),
                smooth_window=int(self.fit_smooth_win_spin.value()),
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        mask_idx = np.where(mask)[0]
        peaks_in_range = self.peak_indices[np.isin(self.peak_indices, mask_idx)] if self.peak_indices.size else np.asarray([], dtype=int)
        if peaks_in_range.size == 0:
            self._show_error("Run Find Peaks for the current gate first, then fit.")
            return
        chosen = np.asarray(peaks_in_range, dtype=int)
        centers = np.asarray(x[chosen], dtype=float)
        try:
            res = fit_multi_peak(
                xr,
                yr,
                centers_hint=centers,
                model=self.fit_model_combo.currentText(),  # type: ignore[arg-type]
                mode=self._peak_mode(),  # type: ignore[arg-type]
            )
        except Exception as exc:
            self._show_error(f"Fit failed: {exc}")
            return
        self.peak_indices = chosen
        self.fit_result = res
        self._refresh_peak_delete_controls()
        self.gate_spin.blockSignals(True)
        self.gate_spin.setValue(gate_used)
        self.gate_spin.blockSignals(False)
        self.current_gate = gate_used
        self._update_spectrum_only()
        lines = [f"{res.model} fit @ gate {gate_used:.6g}", f"Baseline: {res.baseline:.6g}"]
        for i in range(res.centers.size):
            lines.append(
                f"{i+1}. center={res.centers[i]:.6g}, amp={res.amplitudes[i]:.6g}, width={res.widths[i]:.6g}"
            )
        self.analysis_text.setPlainText("\n".join(lines))

    def _on_clear_fit(self) -> None:
        self.fit_result = None
        self._update_spectrum_only()

    def _refresh_peak_delete_controls(self) -> None:
        n = int(self.peak_indices.size)
        self.peak_delete_spin.blockSignals(True)
        self.peak_delete_spin.setRange(1, max(1, n))
        if n > 0:
            self.peak_delete_spin.setValue(min(int(self.peak_delete_spin.value()), n))
        else:
            self.peak_delete_spin.setValue(1)
        self.peak_delete_spin.blockSignals(False)
        self.delete_peak_btn.setEnabled(n > 0)
        self.clear_peaks_btn.setEnabled(n > 0)

    def _on_delete_peak_by_number(self) -> None:
        if self.current_cube is None or self.peak_indices.size == 0:
            return
        n = int(self.peak_delete_spin.value())
        if n < 1 or n > int(self.peak_indices.size):
            return
        idx_to_remove = int(self.peak_indices[n - 1])
        keep = self.peak_indices != idx_to_remove
        self.peak_indices = np.asarray(self.peak_indices[keep], dtype=int)
        self.fit_result = None
        self._refresh_peak_delete_controls()
        gate_used, y = self._nearest_spectrum(self.current_gate)
        self._update_spectrum_only()
        self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")

    def _on_clear_peaks(self) -> None:
        if self.current_cube is None:
            return
        self.peak_indices = np.asarray([], dtype=int)
        self.fit_result = None
        self._refresh_peak_delete_controls()
        gate_used, y = self._nearest_spectrum(self.current_gate)
        self._update_spectrum_only()
        self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")

    def _on_export_fit_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        if self.fit_result is None:
            self._show_error("Run a fit first.")
            return
        cube = self.current_cube
        gate_used, _ = self._nearest_spectrum(self.current_gate)
        default = Path(self.current_folder) / f"{cube.title}_gate_{gate_used:.6g}_{self.fit_result.model}_fit.csv"
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Fit CSV",
            str(default),
            "CSV Files (*.csv)",
        )
        if not out_path:
            return
        try:
            with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
                wr = csv.writer(fh)
                wr.writerow(["gate", "peak_index", "center", "width", "amplitude", "baseline", "model"])
                for i in range(self.fit_result.centers.size):
                    wr.writerow(
                        [
                            f"{gate_used:.10g}",
                            i + 1,
                            f"{self.fit_result.centers[i]:.10g}",
                            f"{self.fit_result.widths[i]:.10g}",
                            f"{self.fit_result.amplitudes[i]:.10g}",
                            f"{self.fit_result.baseline:.10g}",
                            self.fit_result.model,
                        ]
                    )
        except Exception as exc:
            self._show_error(f"Failed to export CSV: {exc}")
            return
        self.statusBar().showMessage(f"Exported fit table: {out_path}")

    def _on_export_spectrum_csv(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        gate_used, y = self._nearest_spectrum(self.current_gate)
        x = self.current_cube.energy
        try:
            _xr, y_analysis, mask = self._analysis_xy(
                x,
                y,
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
                wr.writerow(["energy", "intensity", "in_fit_range", "analysis_intensity"])
                pos = 0
                for i in range(x.size):
                    in_range = bool(mask[i])
                    a_val = ""
                    if in_range and pos < y_analysis.size:
                        a_val = f"{float(y_analysis[pos]):.10g}"
                        pos += 1
                    wr.writerow([f"{x[i]:.10g}", f"{y[i]:.10g}", int(in_range), a_val])
        except Exception as exc:
            self._show_error(f"Failed to export spectrum CSV: {exc}")
            return
        self.statusBar().showMessage(f"Exported spectrum: {out_path}")

    def _on_batch_fit_all_gates(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        cube = self.current_cube
        if not self.batch_peak_map:
            self._show_error("Run Find Peaks All Gates first. Batch fitting uses those peak results.")
            return
        default = Path(self.current_folder) / f"{cube.title}_batch_fit.csv"
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Batch Fit CSV",
            str(default),
            "CSV Files (*.csv)",
        )
        if not out_path:
            return

        model = str(self.fit_model_combo.currentText())
        peak_mode = self._peak_mode()
        x = cube.energy
        mask = self._fit_mask(x)
        xr = np.asarray(x[mask], dtype=float)
        mask_idx = np.where(mask)[0]
        if xr.size < 5:
            self._show_error("Fit range is too narrow for batch fit.")
            return
        rows: list[list[str | int | float]] = []
        ok_count = 0
        fail_count = 0

        for gi, gate in enumerate(cube.gate):
            y = np.asarray(cube.z[gi, :], dtype=float)
            yr = np.asarray(y[mask], dtype=float)
            if self.fit_smooth_chk.isChecked():
                win = self._clamp_sg_window(yr.size, int(self.fit_smooth_win_spin.value()))
                if win >= 3 and yr.size > win:
                    yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")
            peaks = np.asarray(self.batch_peak_map.get(gi, np.asarray([], dtype=int)), dtype=int)
            peaks = peaks[np.isin(peaks, mask_idx)]
            if peaks.size == 0:
                fail_count += 1
                rows.append([f"{gate:.10g}", "", "", "", "", "", model, "no_peaks"])
                continue
            try:
                res = fit_multi_peak(
                    xr,
                    yr,
                    centers_hint=x[peaks],
                    model=model,  # type: ignore[arg-type]
                    mode=peak_mode,  # type: ignore[arg-type]
                )
            except Exception as exc:
                fail_count += 1
                rows.append([f"{gate:.10g}", "", "", "", "", "", model, f"fit_failed:{exc}"])
                continue

            ok_count += 1
            for pi in range(res.centers.size):
                rows.append(
                    [
                        f"{gate:.10g}",
                        pi + 1,
                        f"{res.centers[pi]:.10g}",
                        f"{res.widths[pi]:.10g}",
                        f"{res.amplitudes[pi]:.10g}",
                        f"{res.baseline:.10g}",
                        model,
                        "ok",
                    ]
                )
            if gi % 20 == 0:
                self.statusBar().showMessage(f"Batch fitting... gate {gi+1}/{cube.gate.size}")
                QApplication.processEvents()

        try:
            with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
                wr = csv.writer(fh)
                wr.writerow(["gate", "peak_index", "center", "width", "amplitude", "baseline", "model", "status"])
                wr.writerows(rows)
        except Exception as exc:
            self._show_error(f"Failed to write batch CSV: {exc}")
            return

        self.statusBar().showMessage(f"Batch fit exported: {out_path} (ok={ok_count}, fail={fail_count})")
        self.analysis_text.setPlainText(
            f"Batch fit done.\nTotal gates: {cube.gate.size}\nSuccess: {ok_count}\nFailed: {fail_count}\nCSV: {out_path}"
        )

    def _on_batch_find_peaks_all_gates(self) -> None:
        if self.current_cube is None:
            self._show_error("Load a DAT file first.")
            return
        cube = self.current_cube
        peak_mode = self._peak_mode()
        prom = float(self.prom_spin.value())
        min_dist = int(self.distance_spin.value())
        max_peaks = int(self.max_peaks_spin.value())
        x = cube.energy
        mask = self._fit_mask(x)
        xr = np.asarray(x[mask], dtype=float)
        mask_idx = np.where(mask)[0]
        if xr.size < 5:
            self._show_error("Fit range is too narrow for batch peak finding.")
            return

        peak_map: dict[int, np.ndarray] = {}
        ok_count = 0
        fail_count = 0
        peak_total = 0

        for gi, gate in enumerate(cube.gate):
            y = np.asarray(cube.z[gi, :], dtype=float)
            yr = np.asarray(y[mask], dtype=float)
            if self.peak_smooth_chk.isChecked():
                win = self._clamp_sg_window(yr.size, int(self.peak_smooth_win_spin.value()))
                if win >= 3 and yr.size > win:
                    yr = savgol_filter(yr, window_length=win, polyorder=2, mode="interp")
            try:
                peaks = find_peak_indices(
                    xr,
                    yr,
                    mode=peak_mode,  # type: ignore[arg-type]
                    prominence_frac=prom,
                    min_distance=min_dist,
                    max_peaks=max_peaks,
                )
            except Exception as exc:
                fail_count += 1
                continue

            if peaks.size == 0:
                fail_count += 1
                continue

            ok_count += 1
            peak_total += int(peaks.size)
            peak_map[gi] = np.asarray(mask_idx[peaks], dtype=int)
            if gi % 20 == 0:
                self.statusBar().showMessage(f"Batch peak finding... gate {gi+1}/{cube.gate.size}")
                QApplication.processEvents()

        self.batch_peak_map = peak_map
        self._sync_batch_peak_coords()
        self._update_spectrum_only()
        self.statusBar().showMessage(f"Batch peaks overlaid: gates={cube.gate.size}, peaks={peak_total}")
        self.analysis_text.setPlainText(
            f"Batch peak finding done.\nTotal gates: {cube.gate.size}\nGates with peaks: {ok_count}\nFailed: {fail_count}\nPeaks found: {peak_total}\nOverlay shown on heatmap."
        )

    def _on_clear_batch_peaks(self) -> None:
        self.batch_peak_map = {}
        self.batch_peak_coords = None
        self._batch_delete_circle_start = None
        self._update_spectrum_only()
        self.statusBar().showMessage("Cleared all-gate peak overlay.")

    def _plot_cube(self) -> None:
        if self.current_cube is None:
            return
        cube = self.current_cube
        self.figure.clear()
        gs = self.figure.add_gridspec(
            nrows=2,
            ncols=2,
            width_ratios=[1.0, 0.04],
            height_ratios=[1.0, 1.0],
            wspace=0.12,
            hspace=0.28,
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
            self._axis_edges(e),
            self._axis_edges(g),
            z,
            shading="flat",
            cmap=self.cmap_combo.currentText(),
            norm=Normalize(vmin=float(vmin), vmax=float(vmax)),
        )
        self.figure.colorbar(im, cax=cax, label="Intensity (a.u.)")
        self._heat_ax.set_title(cube.title)
        self._heat_ax.set_xlabel("Photon Energy (eV)")
        self._heat_ax.set_ylabel(cube.gate_label)
        xmin, xmax = self._ordered_pair(float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        ymin, ymax = self._ordered_pair(float(self.ymin_spin.value()), float(self.ymax_spin.value()))
        self._heat_ax.set_xlim((xmin, xmax))
        self._heat_ax.set_ylim((ymin, ymax))
        self._heat_ax.grid(alpha=0.15)
        self._update_spectrum_only()
        self.canvas.draw_idle()
        if self._canvas_click_cid is not None:
            self.canvas.mpl_disconnect(self._canvas_click_cid)
        if self._canvas_release_cid is not None:
            self.canvas.mpl_disconnect(self._canvas_release_cid)
        self._canvas_click_cid = self.canvas.mpl_connect("button_press_event", self._on_canvas_click)
        self._canvas_release_cid = self.canvas.mpl_connect("button_release_event", self._on_canvas_release)

    def _update_spectrum_only(self) -> None:
        if self.current_cube is None or self._spec_ax is None or self._heat_ax is None:
            return
        gate_used, y = self._nearest_spectrum(self.current_gate)
        x = self.current_cube.energy
        self._spec_ax.clear()
        self._spec_ax.plot(x, y, color="#1f77b4", lw=1.4, label="Spectrum")
        self._spec_ax.set_title(f"Spectrum @ {gate_used:.6g}")
        self._spec_ax.set_xlabel("Photon Energy (eV)")
        self._spec_ax.set_ylabel("Intensity (a.u.)")
        self._spec_ax.grid(alpha=0.25)
        xmin, xmax = self._fit_range_bounds()
        self._spec_ax.axvspan(xmin, xmax, color="#66c2a5", alpha=0.12, zorder=0, label="Fit Range")

        selected_peak_array_idx = self._selected_peak_array_index()
        if self.peak_indices.size:
            px = x[self.peak_indices]
            py = y[self.peak_indices]
            if selected_peak_array_idx is not None and 0 <= selected_peak_array_idx < px.size:
                mask = np.ones(px.size, dtype=bool)
                mask[selected_peak_array_idx] = False
                if np.any(mask):
                    self._spec_ax.scatter(px[mask], py[mask], s=36, color="#d62728", zorder=3, label="Peaks")
                self._spec_ax.scatter(
                    [px[selected_peak_array_idx]],
                    [py[selected_peak_array_idx]],
                    s=78,
                    color="#ff9800",
                    edgecolors="#111111",
                    linewidths=0.8,
                    zorder=4,
                    label="Selected Peak",
                )
            else:
                self._spec_ax.scatter(px, py, s=36, color="#d62728", zorder=3, label="Peaks")

        if self.fit_result is not None:
            self._spec_ax.plot(self.fit_result.x, self.fit_result.y_fit, lw=1.5, color="#111111", label="Fit")
            for k, comp in enumerate(self.fit_result.components, start=1):
                self._spec_ax.plot(
                    self.fit_result.x,
                    comp + self.fit_result.baseline,
                    lw=1.0,
                    alpha=0.65,
                    label=f"C{k}",
                )

        handles, labels = self._spec_ax.get_legend_handles_labels()
        if handles:
            self._spec_ax.legend(loc="upper right", fontsize=8)
        view_xmin, view_xmax = self._ordered_pair(float(self.xmin_spin.value()), float(self.xmax_spin.value()))
        self._spec_ax.set_xlim((view_xmin, view_xmax))
        y0, y1 = self._auto_spectrum_ylim(x, y, (view_xmin, view_xmax))
        self._spec_ax.set_ylim((y0, y1))

        self.gate_spin.blockSignals(True)
        self.gate_spin.setValue(gate_used)
        self.gate_spin.blockSignals(False)
        self.current_gate = gate_used
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
        if self._heat_peak_artists:
            for art in self._heat_peak_artists:
                try:
                    art.remove()
                except Exception:
                    pass
            self._heat_peak_artists = []
        if self._heat_batch_peak_artists:
            for art in self._heat_batch_peak_artists:
                try:
                    art.remove()
                except Exception:
                    pass
            self._heat_batch_peak_artists = []
        if self._heat_peak_texts:
            for t in self._heat_peak_texts:
                try:
                    t.remove()
                except Exception:
                    pass
            self._heat_peak_texts = []
        if self.batch_peak_coords is not None:
            bx, by = self.batch_peak_coords
            if bx.size and by.size:
                self._heat_batch_peak_artists.append(
                    self._heat_ax.scatter(
                        bx,
                        by,
                        s=16,
                        marker="o",
                        facecolors="none",
                        edgecolors="#00e5ff",
                        linewidths=0.7,
                        alpha=0.95,
                        zorder=24,
                        label="Peaks@AllGates",
                    )
                )
        if self.peak_indices.size == 0:
            return

        valid = self.peak_indices[(self.peak_indices >= 0) & (self.peak_indices < x.size)]
        if valid.size == 0:
            return
        px = np.asarray(x[valid], dtype=float)
        py = np.full_like(px, float(gate_used), dtype=float)
        selected_peak_array_idx = self._selected_peak_array_index()
        if selected_peak_array_idx is not None and 0 <= selected_peak_array_idx < px.size:
            mask = np.ones(px.size, dtype=bool)
            mask[selected_peak_array_idx] = False
            if np.any(mask):
                self._heat_peak_artists.append(
                    self._heat_ax.scatter(
                        px[mask],
                        py[mask],
                        s=42,
                        marker="o",
                        facecolors="#ffeb3b",
                        edgecolors="#1f1f1f",
                        linewidths=0.8,
                        zorder=30,
                        label="Peaks@Gate",
                    )
                )
            self._heat_peak_artists.append(
                self._heat_ax.scatter(
                    [px[selected_peak_array_idx]],
                    [py[selected_peak_array_idx]],
                    s=88,
                    marker="o",
                    facecolors="#ff9800",
                    edgecolors="#111111",
                    linewidths=0.9,
                    zorder=31,
                    label="Selected@Gate",
                )
            )
        else:
            self._heat_peak_artists.append(
                self._heat_ax.scatter(
                    px,
                    py,
                    s=42,
                    marker="o",
                    facecolors="#ffeb3b",
                    edgecolors="#1f1f1f",
                    linewidths=0.8,
                    zorder=30,
                    label="Peaks@Gate",
                )
            )
        y0, y1 = self._heat_ax.get_ylim()
        yoff = 0.018 * abs(float(y1 - y0))
        for i, xv in enumerate(px, start=1):
            is_sel = (selected_peak_array_idx is not None) and (i - 1 == selected_peak_array_idx)
            txt = self._heat_ax.text(
                float(xv),
                float(gate_used + yoff),
                str(i),
                fontsize=(9 if is_sel else 8),
                color=("#111111" if not is_sel else "#000000"),
                ha="center",
                va="bottom",
                zorder=31,
                bbox=dict(
                    boxstyle="round,pad=0.16",
                    fc=("#fff3cd" if is_sel else "white"),
                    ec=("#a65f00" if is_sel else "#222222"),
                    alpha=0.85,
                ),
            )
            self._heat_peak_texts.append(txt)

    def _selected_peak_array_index(self) -> int | None:
        if self.peak_indices.size == 0:
            return None
        n = int(self.peak_delete_spin.value())
        if n < 1 or n > int(self.peak_indices.size):
            return None
        return int(n - 1)

    def _update_peak_text(
        self,
        gate_used: float,
        x: np.ndarray,
        y: np.ndarray,
        *,
        title_prefix: str = "Selected",
    ) -> None:
        if self.peak_indices.size == 0:
            self.analysis_text.setPlainText(f"{title_prefix}: no peaks.")
            return
        lines = [f"{title_prefix} peaks @ gate {gate_used:.6g}:"]
        for i, p in enumerate(self.peak_indices, start=1):
            lines.append(f"{i}. E={x[p]:.6g}, I={y[p]:.6g}")
        self.analysis_text.setPlainText("\n".join(lines))

    def _add_manual_peak(self, x_click: float) -> bool:
        if self.current_cube is None:
            return False
        x = self.current_cube.energy
        idx = int(np.argmin(np.abs(x - float(x_click))))
        if self.peak_indices.size and np.any(self.peak_indices == idx):
            return False
        merged = np.concatenate([self.peak_indices, np.asarray([idx], dtype=int)])
        self.peak_indices = np.asarray(np.unique(merged), dtype=int)
        self.fit_result = None
        self._refresh_peak_delete_controls()
        return True

    def _remove_manual_peak_near(self, x_click: float) -> bool:
        if self.current_cube is None or self.peak_indices.size == 0:
            return False
        x = self.current_cube.energy
        peak_x = x[self.peak_indices]
        k = int(np.argmin(np.abs(peak_x - float(x_click))))
        nearest_idx = int(self.peak_indices[k])
        x_span = max(float(np.nanmax(x) - np.nanmin(x)), 1e-12)
        max_dx = 0.02 * x_span
        if abs(float(x[nearest_idx]) - float(x_click)) > max_dx:
            return False
        mask = self.peak_indices != nearest_idx
        self.peak_indices = np.asarray(self.peak_indices[mask], dtype=int)
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

    def _delete_batch_peaks_in_circle(self, center_x: float, center_y: float, edge_x: float, edge_y: float) -> int:
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

    def _on_canvas_click(self, event: Any) -> None:
        if self.current_cube is None:
            return
        if event.inaxes is self._spec_ax and event.xdata is not None:
            if event.button == 1:
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

        if self._heat_ax is None:
            return
        if (
            self.circle_delete_btn.isChecked()
            and event.inaxes is self._heat_ax
            and event.button == 1
            and event.xdata is not None
            and event.ydata is not None
        ):
            self._batch_delete_circle_start = (float(event.xdata), float(event.ydata))
            return
        if event.inaxes is self._heat_ax and event.button == 3 and event.xdata is not None and event.ydata is not None:
            if self._remove_peak_from_heatmap_click(float(event.xdata), float(event.ydata)):
                gate_used, y = self._nearest_spectrum(self.current_gate)
                self._update_spectrum_only()
                self._update_peak_text(gate_used, self.current_cube.energy, y, title_prefix="Manual")
            return
        if event.button != 1 or event.inaxes is not self._heat_ax or event.ydata is None:
            return
        g = self.current_cube.gate
        idx = int(np.argmin(np.abs(g - float(event.ydata))))
        self.current_gate = float(g[idx])
        self.fit_result = None
        self._update_spectrum_only()

    def _on_canvas_release(self, event: Any) -> None:
        if (
            self.current_cube is None
            or self._heat_ax is None
            or not self.circle_delete_btn.isChecked()
            or self._batch_delete_circle_start is None
        ):
            self._batch_delete_circle_start = None
            return
        start_x, start_y = self._batch_delete_circle_start
        self._batch_delete_circle_start = None
        if event.inaxes is not self._heat_ax or event.button != 1 or event.xdata is None or event.ydata is None:
            return
        deleted = self._delete_batch_peaks_in_circle(start_x, start_y, float(event.xdata), float(event.ydata))
        if deleted > 0:
            self.fit_result = None
            self._update_spectrum_only()
            self.statusBar().showMessage(f"Deleted {deleted} all-gate peak(s) inside circle.")
        else:
            self.statusBar().showMessage("No all-gate peaks inside circle.")

    @staticmethod
    def _axis_edges(values: np.ndarray) -> np.ndarray:
        v = np.asarray(values, float).ravel()
        if v.size == 1:
            return np.asarray([v[0] - 0.5, v[0] + 0.5], dtype=float)
        mids = 0.5 * (v[:-1] + v[1:])
        left = float(v[0] - 0.5 * (v[1] - v[0]))
        right = float(v[-1] + 0.5 * (v[-1] - v[-2]))
        return np.concatenate(([left], mids, [right]))
