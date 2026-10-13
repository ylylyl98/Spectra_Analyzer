"""Interactive comparison editor; preview and exports share one renderer."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import math
import re
from uuid import uuid4
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from PySide6.QtCore import Qt, Signal, QSignalBlocker, QMimeData, QItemSelectionModel, QSize, QEvent
from PySide6.QtGui import QColor, QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QColorDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSplitter, QVBoxLayout, QWidget, QSizePolicy, QAbstractItemView, QInputDialog, QMenu, QToolButton,
    QApplication, QTabWidget, QToolBar)

from .workbook_state import remove_curve, sheet_columns
from .comparison_display import prepare_comparison
from .plot_icons import PLOT_STYLES, plot_icon
from .xy_analysis_panel import XYAnalysisPanel


def draw_comparison(figure, workspace, comparison):
    figure.clear()
    axes = figure.add_subplot(111)
    display = prepare_comparison(workspace, comparison)
    for item in display.curves:
        curve = item.curve
        axes.plot(item.x, item.y, gid=curve.curve_id,
                  label=curve.label, color=curve.color, linewidth=curve.width,
                  linestyle='None' if curve.style == 'scatter' else '-',
                  marker='None' if curve.style == 'line' else 'o', markersize=4)
    axes.set(title=comparison.plot_title, xlabel=comparison.x_label, ylabel=display.y_label)
    if display.x_limits is not None:
        axes.set_xlim(display.x_limits)
    if not display.curves:
        axes.text(.5, .5, 'No common X range' if comparison.x_range_mode == 'common'
                  else 'No visible samples', ha='center', va='center', transform=axes.transAxes)
    axes._comparison_display = display
    axes.grid(True, alpha=.2)
    if comparison.legend_visible and axes.lines:
        axes.legend()
    figure.set_layout_engine('tight')
    return axes


class _XYToolbar(NavigationToolbar2QT):
    toolitems = tuple(item for item in NavigationToolbar2QT.toolitems
                     if item[3] in ('home', 'back', 'forward', 'pan', 'zoom'))


class _CurveList(QListWidget):
    """Native selection and checks, with same-list, ordered multirow moves."""
    _mime_type = 'application/x-spectra-curve-order'

    def __init__(self, page):
        super().__init__()
        self._page = page
        self._drag_owner = uuid4().hex
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)
        self.setIconSize(QSize(14, 14))

    def mimeTypes(self):
        return [self._mime_type]

    def mimeData(self, items):
        mime = QMimeData()
        # Never trust the incoming item order: Qt may enumerate selection ranges.
        ids = [item.data(Qt.UserRole) for item in sorted(items, key=self.row)]
        mime.setData(self._mime_type, json.dumps([self._drag_owner, ids]).encode('utf-8'))
        return mime

    def supportedDropActions(self):
        return Qt.MoveAction

    def _drag_ids(self, mime):
        if not mime.hasFormat(self._mime_type):
            return []
        try:
            owner, ids = json.loads(bytes(mime.data(self._mime_type)))
            if owner != self._drag_owner or not isinstance(ids, list) or not ids:
                return []
            if any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids):
                return []
            return ids if set(ids) <= self._page.comparison.curves.keys() else []
        except (ValueError, TypeError):
            return []

    def dropMimeData(self, row, mime, action):
        ids = self._drag_ids(mime)
        if action != Qt.MoveAction or not ids:
            return False
        return self._page._move_curves(ids, self.count() if row < 0 else row)

    def startDrag(self, supported_actions):
        if not self.selectedItems() or not self._page._finish_curve_action():
            return
        # Own the move, so QAbstractItemView does not remove source rows again
        # after our drop handler rebuilds the ordered view from the comparison.
        drag = QDrag(self)
        drag.setMimeData(self.mimeData(self.selectedItems()))
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.source() is self and self._drag_ids(event.mimeData()):
            super().dragEnterEvent(event)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.source() is self and self._drag_ids(event.mimeData()):
            super().dragMoveEvent(event)
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        index = self.indexAt(event.position().toPoint())
        row = index.row() if index.isValid() else self.count()
        if index.isValid() and self.dropIndicatorPosition() == QAbstractItemView.BelowItem:
            row += 1
        if self.dropMimeData(row, event.mimeData(), Qt.MoveAction):
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def mousePressEvent(self, event):
        if self._page._finish_curve_action():
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if self._page._finish_curve_action():
            if event.key() == Qt.Key_Escape:
                self.clearSelection()
                event.accept()
                return
            super().keyPressEvent(event)


class XYPlotPage(QWidget):
    changed = Signal()
    analysisRequested = Signal()
    sourceRequested = Signal(str)
    addDataRequested = Signal()
    importDataRequested = Signal()
    inspectorVisibilityRequested = Signal(bool)

    def __init__(self, workspace, comparison_id):
        super().__init__()
        self.workspace, self.comparison_id = workspace, comparison_id
        self._syncing = False
        self._active_curve_id = None
        self._selection_ids = set()
        self._preview_styles = []
        self._draft_fields = set()
        self._inspector_hosted = False
        self.command_bar = QToolBar('Comparison commands')
        self.command_bar.setMovable(False)
        self.command_bar.setFloatable(False)
        self.command_bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.command_bar.setIconSize(QSize(20, 20))
        self.command_bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.style_buttons = {}
        self.style_actions = {}
        for style, label in PLOT_STYLES:
            action = self.command_bar.addAction(plot_icon(style), label)
            action.setToolTip(f'Apply {label} to selected curves in this graph')
            action.triggered.connect(lambda _checked=False, value=style: self._set_selected_style(value))
            button = self.command_bar.widgetForAction(action)
            button.setAccessibleName(label)
            button.setFocusPolicy(Qt.StrongFocus)
            self.style_actions[style] = action
            self.style_buttons[style] = button
        self.analyze_action = self.command_bar.addAction('Analyze')
        self.analyze_action.setToolTip('Analyze exactly one selected source curve in this graph')
        self.analyze_action.triggered.connect(self.analysisRequested)
        self.analyze_btn = self.command_bar.widgetForAction(self.analyze_action)
        self.analyze_btn.setAccessibleName('Analyze selected curve')
        self.analyze_btn.setFocusPolicy(Qt.StrongFocus)
        self.command_selection_label = QLabel()
        self.command_bar.addWidget(self.command_selection_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.figure = Figure(figsize=(7, 5), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumSize(120, 120)
        self.canvas.setFocusPolicy(Qt.StrongFocus)
        self.toolbar = _XYToolbar(self.canvas, self)
        toolbar_row = QHBoxLayout()
        toolbar_row.addWidget(self.toolbar, 1)
        self.add_data_btn = QToolButton()
        self.add_data_btn.setText('Add data…')
        self.add_data_btn.setAccessibleName('Add data to this comparison')
        self.add_data_btn.setToolTip('Choose existing Sheets, or use the menu to import files into this graph.')
        self.add_data_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.add_data_btn.setFocusPolicy(Qt.StrongFocus)
        add_menu = QMenu(self.add_data_btn)
        self.add_sheets_action = add_menu.addAction('Existing Sheets…')
        self.add_files_action = add_menu.addAction('From files…')
        self.add_sheets_action.triggered.connect(self.addDataRequested)
        self.add_files_action.triggered.connect(self.importDataRequested)
        self.add_data_btn.setMenu(add_menu)
        self.add_data_btn.clicked.connect(self.addDataRequested)
        toolbar_row.addWidget(self.add_data_btn)
        self.inspector_toggle_btn = QToolButton()
        self.inspector_toggle_btn.setText('Inspector')
        self.inspector_toggle_btn.setAccessibleName('Show comparison inspector')
        self.inspector_toggle_btn.setToolTip('Show or hide curve, source and export controls; drafts are retained.')
        self.inspector_toggle_btn.setCheckable(True)
        self.inspector_toggle_btn.setChecked(True)
        self.inspector_toggle_btn.setFocusPolicy(Qt.StrongFocus)
        self.inspector_toggle_btn.toggled.connect(self._set_inspector_visible)
        toolbar_row.addWidget(self.inspector_toggle_btn)
        layout.addLayout(toolbar_row)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.canvas)
        self.inspector = QScrollArea()
        self.inspector.setWidgetResizable(True)
        self.inspector.setMinimumWidth(210)
        self.inspector.setMaximumWidth(380)
        content = QWidget()
        inspector_layout = QVBoxLayout(content)
        inspector_layout.setContentsMargins(0, 4, 0, 0)
        self.selection_label = QLabel()
        self.selection_label.setAccessibleName('Selected curve count')
        self.selection_label.setWordWrap(True)
        self.selection_label.setTextFormat(Qt.PlainText)
        self.selection_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        inspector_layout.addWidget(self.selection_label)
        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setAccessibleName('Comparison inspector sections')
        self.inspector_tabs.tabBar().setFocusPolicy(Qt.StrongFocus)
        inspector_layout.addWidget(self.inspector_tabs, 1)
        section_forms = []
        for name in ('Curves', 'Analysis', 'Display'):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            section = QWidget()
            section_form = QFormLayout(section)
            section_form.setRowWrapPolicy(QFormLayout.WrapAllRows)
            section_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            scroll.setWidget(section)
            self.inspector_tabs.addTab(scroll, name)
            section_forms.append(section_form)
        self.analysis_panel = XYAnalysisPanel(self)
        section_forms[1].addRow(self.analysis_panel)
        self.resume_analysis_action = self.command_bar.addAction('Resume saved analysis')
        self.resume_analysis_action.setToolTip('Select the source of the saved or retained analysis and resume it')
        self.resume_analysis_action.setEnabled(False)
        self.resume_analysis_action.triggered.connect(self.analysis_panel.resume_analysis)
        form = section_forms[0]
        self.curve_list = _CurveList(self)
        self.curve_list.setAccessibleName('Comparison curves')
        self.curve_list.setToolTip('Check to show. Ctrl/Shift selects multiple curves. Drag to change plot and stack order.')
        self.curve_list.setMinimumHeight(220)
        self.curve_list.setMaximumHeight(280)
        form.addRow('Curves · check to show', self.curve_list)
        self.highlight_selection_chk = QCheckBox('Highlight selection')
        self.highlight_selection_chk.setChecked(True)
        self.highlight_selection_chk.setAccessibleName('Highlight selected curves in preview')
        self.highlight_selection_chk.setToolTip(
            'Emphasize selected visible curves. Esc or an empty canvas click clears selection. Exports keep original styles.')
        self.highlight_selection_chk.toggled.connect(self._apply_selection_highlight)
        form.addRow(self.highlight_selection_chk)
        batch_row = QHBoxLayout()
        self.batch_color_btn = QPushButton('Color…')
        self.batch_width_btn = QPushButton('Width…')
        self.visibility_btn = QPushButton('Visibility')
        for button, name in ((self.batch_color_btn, 'Set color for selected curves'),
                             (self.batch_width_btn, 'Set width for selected curves'),
                             (self.visibility_btn, 'Curve visibility commands')):
            button.setAccessibleName(name)
            button.setToolTip(name)
            batch_row.addWidget(button)
        visibility_menu = QMenu(self.visibility_btn)
        self.show_selected_action = visibility_menu.addAction('Show selected')
        self.hide_selected_action = visibility_menu.addAction('Hide selected')
        self.only_selected_action = visibility_menu.addAction('Show only selected')
        visibility_menu.addSeparator()
        self.show_all_action = visibility_menu.addAction('Show all')
        self.visibility_btn.setMenu(visibility_menu)
        form.addRow(batch_row)
        move_row = QHBoxLayout()
        self.move_up_btn, self.move_down_btn = QPushButton('Move up'), QPushButton('Move down')
        self.move_up_btn.setAccessibleName('Move selected curves up')
        self.move_down_btn.setAccessibleName('Move selected curves down')
        move_row.addWidget(self.move_up_btn)
        move_row.addWidget(self.move_down_btn)
        form.addRow(move_row)
        form.addRow(QLabel('Current curve'))
        self.y_offset_edit = QLineEdit()
        self.y_offset_edit.setAccessibleName('Current curve extra Y offset')
        self.y_offset_edit.setToolTip('Extra Y shift after normalization and equal stack spacing.')
        form.addRow('Extra Y offset', self.y_offset_edit)
        self.label_edit = QLineEdit()
        self.label_edit.setAccessibleName('Curve legend text')
        form.addRow('Legend text', self.label_edit)
        self.color_edit = QLineEdit()
        self.color_edit.setAccessibleName('Curve color hex')
        self.color_btn = QPushButton('Choose color…')
        form.addRow('Color', self.color_edit)
        form.addRow(self.color_btn)
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setAccessibleName('Curve line width')
        self.width_spin.setRange(.1, 50)
        self.width_spin.setDecimals(6)
        self.width_spin.setSingleStep(.25)
        form.addRow('Line width', self.width_spin)
        self.source_label = QLabel()
        self.source_label.setTextFormat(Qt.PlainText)
        self.source_label.setWordWrap(True)
        self.source_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.source_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow('Source', self.source_label)
        self.source_btn = QPushButton('Open source Sheet')
        self.remove_btn = QPushButton('Remove curve')
        form.addRow(self.source_btn)
        form.addRow(self.remove_btn)
        form = section_forms[2]
        self._inspector_form = form
        form.addRow(QLabel('Graph settings'))
        self.name_edit = QLineEdit()
        self.plot_title_edit = QLineEdit()
        self.x_label_edit = QLineEdit()
        self.y_label_edit = QLineEdit()
        for label, edit in (('Graph name', self.name_edit), ('Plot title', self.plot_title_edit), ('X axis', self.x_label_edit), ('Y axis', self.y_label_edit)):
            edit.setAccessibleName(label)
            form.addRow(label, edit)
            edit.editingFinished.connect(self._graph_edited)
            edit.textChanged.connect(self.changed)
        self.legend_chk = QCheckBox('Show legend')
        self.legend_chk.toggled.connect(self._graph_edited)
        form.addRow(self.legend_chk)
        form.addRow(QLabel('Comparison display'))
        self.normalization_combo = QComboBox()
        self.normalization_combo.setAccessibleName('Comparison normalization')
        for label, value in (('None (original Y)', 'none'), ('Maximum absolute Y', 'maxabs'), ('Min–max (0–1)', 'minmax')):
            self.normalization_combo.addItem(label, value)
        form.addRow('Normalization', self.normalization_combo)
        self.x_range_combo = QComboBox()
        self.x_range_combo.setAccessibleName('Data X interval (also used for analysis)')
        self.x_range_combo.setToolTip('This interval selects data for display, normalization and analysis. Toolbar zoom only changes the view.')
        for label, value in (('All X', 'all'), ('Common overlap of visible curves', 'common'), ('Manual interval', 'manual')):
            self.x_range_combo.addItem(label, value)
        form.addRow('Data X interval (also used for analysis)', self.x_range_combo)
        self.x_min_edit, self.x_max_edit = QLineEdit(), QLineEdit()
        self.stack_step_edit = QLineEdit()
        for label, edit in (('X minimum', self.x_min_edit), ('X maximum', self.x_max_edit),
                            ('Stack step', self.stack_step_edit)):
            edit.setAccessibleName(label)
            form.addRow(label, edit)
            edit.editingFinished.connect(self._display_edited)
            edit.textChanged.connect(self.changed)
        self.stack_step_edit.setToolTip('Equal Y spacing in curve list order, including hidden curves. Signed values are allowed.')
        self.reset_display_btn = QPushButton('Reset comparison display')
        self.reset_display_btn.setAccessibleName('Reset normalization, X range and all Y offsets')
        self.reset_display_btn.clicked.connect(self._reset_display)
        form.addRow(self.reset_display_btn)
        self.display_status = QLabel()
        self.display_status.setTextFormat(Qt.PlainText)
        self.display_status.setAccessibleName('Comparison display status')
        self.display_status.setWordWrap(True)
        self.display_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        form.addRow(self.display_status)
        self.normalization_combo.currentIndexChanged.connect(self._display_edited)
        self.x_range_combo.currentIndexChanged.connect(self._range_mode_changed)
        export_row = QHBoxLayout()
        self.export_svg_btn = QPushButton('SVG…')
        self.export_png_btn = QPushButton('PNG…')
        export_row.addWidget(self.export_svg_btn)
        export_row.addWidget(self.export_png_btn)
        form.addRow('Export', export_row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        inspector_layout.addWidget(self.status)
        for edit in (self.name_edit, self.plot_title_edit, self.x_label_edit, self.y_label_edit, self.label_edit, self.color_edit,
                     self.x_min_edit, self.x_max_edit, self.stack_step_edit, self.y_offset_edit):
            edit.setMinimumWidth(0)
            edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            edit.textChanged.connect(lambda _text, field=edit: self._mark_draft(field))
        self.inspector.setWidget(content)
        self.inspector_tabs.currentChanged.connect(self._inspector_section_changed)
        self.splitter.addWidget(self.inspector)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setSizes([620, 330])
        self.splitter.setCollapsible(1, False)
        self._inspector_width = 330
        layout.addWidget(self.splitter, 1)
        self.curve_list.currentItemChanged.connect(self._select_curve)
        self.curve_list.itemSelectionChanged.connect(self._selection_changed)
        self.curve_list.itemChanged.connect(self._visibility_changed)
        self.batch_color_btn.clicked.connect(self._batch_color)
        self.batch_width_btn.clicked.connect(self._batch_width)
        self.show_selected_action.triggered.connect(lambda: self._batch_visibility('show'))
        self.hide_selected_action.triggered.connect(lambda: self._batch_visibility('hide'))
        self.only_selected_action.triggered.connect(lambda: self._batch_visibility('only'))
        self.show_all_action.triggered.connect(lambda: self._batch_visibility('all'))
        self.move_up_btn.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_btn.clicked.connect(lambda: self._move_selected(1))
        self.label_edit.editingFinished.connect(self._curve_edited)
        self.label_edit.textChanged.connect(self.changed)
        self.color_edit.editingFinished.connect(self._curve_edited)
        self.color_edit.textChanged.connect(self.changed)
        self.width_spin.valueChanged.connect(self._curve_edited)
        self.width_spin.lineEdit().textChanged.connect(lambda: self._mark_draft(self.width_spin))
        self.y_offset_edit.editingFinished.connect(self._curve_edited)
        self.y_offset_edit.textChanged.connect(self.changed)
        self.color_btn.clicked.connect(self._choose_color)
        self.remove_btn.clicked.connect(self._remove_selected)
        self.source_btn.clicked.connect(self._open_source)
        self.export_svg_btn.clicked.connect(lambda: self._export_dialog('svg'))
        self.export_png_btn.clicked.connect(lambda: self._export_dialog('png'))
        self._curve_click_connection = self.canvas.mpl_connect('button_press_event', self._plot_clicked)
        self.canvas.installEventFilter(self)
        for editor in [*self.inspector.findChildren(QLineEdit), self.width_spin]:
            editor.installEventFilter(self)
        self.refresh()

    def eventFilter(self, watched, event):
        if watched is self.canvas and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            if self._finish_curve_action():
                self.curve_list.clearSelection()
            return True
        if (watched is self.width_spin and self.width_spin in self._draft_fields
                and event.type() in (QEvent.Hide, QEvent.Show)):
            # QAbstractSpinBox normalizes pending text on visibility changes
            # as well as FocusOut. Section navigation must retain that draft.
            return True
        if (event.type() == QEvent.FocusOut
                and (QApplication.focusWidget() in (self.inspector_toggle_btn, self.inspector_tabs.tabBar(),
                                                   self.analysis_panel.edit_interval_btn)
                     or not watched.isVisible())):
            # Native navigation receives focus before switching/hiding the
            # editor. Preserve pending text during these presentation changes.
            return True
        return super().eventFilter(watched, event)

    def _inspector_section_changed(self, index):
        if index == 1:
            self.analysis_panel.activate(restore_selection=False)

    def edit_x_interval(self):
        """Navigate to the scientific interval without committing other editor drafts."""
        self.inspector_tabs.tabBar().setFocus()
        self.inspector_tabs.setCurrentIndex(2)
        self.x_range_combo.setFocus()
        self.inspector_tabs.widget(2).ensureWidgetVisible(self.x_range_combo)

    def _set_inspector_visible(self, visible):
        if self._inspector_hosted:
            self.inspectorVisibilityRequested.emit(visible)
            return
        if not visible:
            self._inspector_width = max(210, self.splitter.sizes()[1])
        # Hiding a focused native editor sends FocusOut/ editingFinished. Keep
        # that presentation-only transition from committing its pending draft.
        syncing = self._syncing
        width_value, width_text = self.width_spin.value(), self.width_spin.lineEdit().text()
        self._syncing = True
        try:
            self.inspector.setVisible(visible)
            if visible:
                self.splitter.setSizes([max(120, self.splitter.width() - self._inspector_width), self._inspector_width])
            with QSignalBlocker(self.width_spin):
                self.width_spin.setValue(width_value)
                self.width_spin.lineEdit().setText(width_text)
        finally:
            self._syncing = syncing

    @property
    def comparison(self):
        return self.workspace.comparisons[self.comparison_id]

    def _selected(self):
        # Editors own an ID independent of the selection model's signal order.
        # Qt changes currentItem before currentItemChanged commits old drafts.
        return self.comparison.curves.get(self._active_curve_id)

    def _mark_draft(self, field):
        if not self._syncing:
            self._draft_fields.add(field)

    def _selected_ids(self):
        return {item.data(Qt.UserRole) for item in self.curve_list.selectedItems()}

    def _source_text(self, curve):
        sheet = self.workspace.sheets[curve.sheet_id]
        book = next(book for book in self.workspace.workbooks.values() if sheet.sheet_id in book.sheet_ids)
        columns = sheet_columns(self.workspace, sheet, curve.mode)
        source = f'{book.name} / {sheet.name} / {columns[curve.x_column]} → {columns[curve.y_column]} ({curve.mode})'
        detail = (f'Workbook: {book.name}\nSheet: {sheet.name}\n'
                  f'X: {columns[curve.x_column]} · Y: {columns[curve.y_column]} · {curve.mode}\n'
                  f'{sheet.source_path or "Embedded values"}')
        return source, detail

    def _update_curve_item(self, item, curve):
        source, detail = self._source_text(curve)
        item.setText(f'{curve.label}\n{source}')
        item.setToolTip(f'{curve.label}\n{detail}')
        item.setData(Qt.AccessibleTextRole, f'{curve.label}. {source}')
        pixmap = QPixmap(14, 14)
        pixmap.fill(QColor(curve.color))
        item.setIcon(QIcon(pixmap))

    def refresh(self):
        # A controller refresh must not erase unfinished (including invalid) text.
        if (self._active_curve_id in self.comparison.curves and self._draft_fields
                and self.has_pending_project_edits()):
            self.refresh_sources()
            self.render()
            return
        current_id = self._active_curve_id
        selected_ids = self._selected_ids()
        old_row = self.curve_list.currentRow()
        scroll = self.curve_list.verticalScrollBar().value()
        self._syncing = True
        try:
            comparison = self.comparison
            self.name_edit.setText(comparison.name)
            self.plot_title_edit.setText(comparison.plot_title)
            self.x_label_edit.setText(comparison.x_label)
            self.y_label_edit.setText(comparison.y_label)
            self.legend_chk.setChecked(comparison.legend_visible)
            self._load_display_controls()
            self.curve_list.clear()
            for curve in comparison.curves.values():
                item = QListWidgetItem()
                item.setData(Qt.UserRole, curve.curve_id)
                self._update_curve_item(item, curve)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if curve.visible else Qt.Unchecked)
                self.curve_list.addItem(item)
                item.setSelected(curve.curve_id in selected_ids)
                if curve.curve_id == current_id:
                    self.curve_list.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            if self.curve_list.currentRow() < 0 and self.curve_list.count():
                self.curve_list.setCurrentRow(min(max(0, old_row), self.curve_list.count() - 1))
            item = self.curve_list.currentItem()
            self._active_curve_id = item.data(Qt.UserRole) if item else None
        finally:
            self._syncing = False
        self._load_curve_controls()
        self._draft_fields.clear()
        self._selection_changed()
        self.curve_list.verticalScrollBar().setValue(scroll)
        self.render()

    def refresh_sources(self):
        """Refresh ownership/deletion metadata without touching pending graph drafts."""
        with QSignalBlocker(self.curve_list):
            for row in range(self.curve_list.count() - 1, -1, -1):
                item = self.curve_list.item(row)
                curve = self.comparison.curves.get(item.data(Qt.UserRole))
                if curve is None:
                    self.curve_list.takeItem(row)
                else:
                    self._update_curve_item(item, curve)
        curve = self._selected()
        if curve is None:
            self._active_curve_id = None
            self._load_curve_controls()
        else:
            self.source_label.setText(self._source_text(curve)[1])
        self._selection_changed()

    def render(self):
        self.axes = draw_comparison(self.figure, self.workspace, self.comparison)
        self.analysis_panel.draw_overlays(self.axes)
        self._capture_preview_styles()
        self._apply_selection_highlight()
        self._update_display_status(self._draft_error())
        self.toolbar.update()
        self.canvas.draw_idle()

    def _capture_preview_styles(self):
        """Keep original artist properties local to this preview, including its legend."""
        lines = [line for line in self.axes.lines if line.get_gid() in self.comparison.curves]
        self._preview_styles = []
        for line in lines:
            self._preview_styles.append((line.get_gid(), line, dict(alpha=line.get_alpha(),
                linewidth=line.get_linewidth(), markersize=line.get_markersize(), zorder=line.get_zorder())))
        if self.analysis_panel.captured is not None:
            for line in self.axes.lines:
                if line.get_gid() in ('xy-analysis-peaks', 'xy-analysis-fit'):
                    self._preview_styles.append((self.analysis_panel.captured.curve_id, line,
                        dict(alpha=line.get_alpha())))
        legend = self.axes.get_legend()
        if legend is not None:
            labeled = [line for line in lines if not line.get_label().startswith('_')]
            for line, handle, text in zip(labeled, legend.legend_handles, legend.get_texts()):
                self._preview_styles.append((line.get_gid(), handle, dict(alpha=handle.get_alpha(),
                    linewidth=handle.get_linewidth(), markersize=handle.get_markersize(), zorder=handle.get_zorder())))
                self._preview_styles.append((line.get_gid(), text,
                    dict(alpha=text.get_alpha(), fontweight=text.get_fontweight())))

    def _apply_selection_highlight(self, *_args):
        visible = {curve_id for curve_id, _artist, _style in self._preview_styles
                   if curve_id in self.comparison.curves and self.comparison.curves[curve_id].visible}
        selected = self._selected_ids() & visible if self.highlight_selection_chk.isChecked() else set()
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

    def _update_display_status(self, error=''):
        display = self.axes._comparison_display
        self.display_status.setText('\n'.join(([error] if error else []) + [display.description, *display.notices]))

    def _load_display_controls(self):
        comparison = self.comparison
        self.normalization_combo.setCurrentIndex(self.normalization_combo.findData(comparison.normalization))
        self.x_range_combo.setCurrentIndex(self.x_range_combo.findData(comparison.x_range_mode))
        self.stack_step_edit.setText(repr(float(comparison.stack_step)))
        bounds = comparison.x_range
        self.x_min_edit.setText(repr(float(bounds[0])) if bounds else '')
        self.x_max_edit.setText(repr(float(bounds[1])) if bounds else '')
        for edit in (self.x_min_edit, self.x_max_edit):
            edit.setEnabled(comparison.x_range_mode == 'manual')
            self._inspector_form.setRowVisible(edit, comparison.x_range_mode == 'manual')

    def _range_mode_changed(self, *_args):
        if self._syncing:
            return
        manual = self.x_range_combo.currentData() == 'manual'
        if manual and not self.x_min_edit.text() and not self.x_max_edit.text():
            display = self.axes._comparison_display
            bounds = display.x_limits or tuple(self.axes.dataLim.intervalx)
            if not all(math.isfinite(value) for value in bounds):
                bounds = (0., 1.)
            elif bounds[0] == bounds[1]:
                padding = max(abs(bounds[0]) * .05, .5)
                bounds = (bounds[0] - padding, bounds[1] + padding)
                if not all(math.isfinite(value) for value in bounds):
                    bounds = (min(0., float(self.axes.dataLim.x0)), max(0., float(self.axes.dataLim.x1)))
            self.x_min_edit.setText(repr(float(bounds[0])))
            self.x_max_edit.setText(repr(float(bounds[1])))
        for edit in (self.x_min_edit, self.x_max_edit):
            edit.setEnabled(manual)
            self._inspector_form.setRowVisible(edit, manual)
        self._display_edited()

    @staticmethod
    def _number(edit, label):
        try:
            value = float(edit.text())
        except ValueError:
            raise ValueError(f'Enter a finite number for {label}.') from None
        if not math.isfinite(value):
            raise ValueError(f'Enter a finite number for {label}.')
        return value

    def _display_values(self):
        mode = self.x_range_combo.currentData()
        bounds = self.comparison.x_range
        if mode == 'manual':
            bounds = (self._number(self.x_min_edit, 'X minimum'), self._number(self.x_max_edit, 'X maximum'))
            if bounds[0] >= bounds[1]:
                raise ValueError('X minimum must be less than X maximum.')
        return (self.normalization_combo.currentData(), self._number(self.stack_step_edit, 'stack step'), mode, bounds)

    def _commit_display(self):
        comparison = self.comparison
        values = self._display_values()
        changed = (comparison.normalization, comparison.stack_step, comparison.x_range_mode, comparison.x_range) != values
        comparison.normalization, comparison.stack_step, comparison.x_range_mode, comparison.x_range = values
        self._draft_fields.difference_update((self.x_min_edit, self.x_max_edit, self.stack_step_edit))
        return changed

    def _display_edited(self, *_args):
        if self._syncing:
            return
        error = self._draft_error()
        if error:
            self.status.setText(error)
            self._update_display_status(error)
            self.changed.emit()
            return
        changed = self._commit_display()
        self.status.clear()
        self._update_display_status()
        if changed:
            self.render()
            self.changed.emit()

    def _reset_display(self):
        comparison = self.comparison
        comparison.normalization, comparison.stack_step = 'none', 0.0
        comparison.x_range_mode, comparison.x_range = 'all', None
        for curve in comparison.curves.values():
            curve.y_offset = 0.0
        self._syncing = True
        try:
            self._load_display_controls()
            self.y_offset_edit.setText('0.0' if self._selected() else '')
        finally:
            self._syncing = False
        self.status.setText(self._draft_error())
        self.render()
        self.changed.emit()

    def _select_curve(self, current=None, previous=None):
        if self._syncing:
            return
        current_id = current.data(Qt.UserRole) if current else None
        if current_id == self._active_curve_id:
            return
        if not self._finish_curve_action():
            with QSignalBlocker(self.curve_list):
                for row in range(self.curve_list.count()):
                    item = self.curve_list.item(row)
                    item.setSelected(item.data(Qt.UserRole) in self._selection_ids)
                    if item.data(Qt.UserRole) == self._active_curve_id:
                        self.curve_list.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            return
        self._active_curve_id = current_id
        self._load_curve_controls()

    def _load_curve_controls(self):
        curve = self._selected()
        self._syncing = True
        try:
            for widget in (self.label_edit, self.color_edit, self.color_btn, self.width_spin, self.y_offset_edit,
                           self.source_btn, self.remove_btn):
                widget.setEnabled(curve is not None)
            self.label_edit.setText(curve.label if curve else '')
            self.color_edit.setText(curve.color if curve else '')
            self.width_spin.setRange(min(.000001, curve.width) if curve else .000001,
                                     max(50, curve.width) if curve else 50)
            self.width_spin.setValue(curve.width if curve else 1.5)
            self._displayed_width = self.width_spin.value()
            self.y_offset_edit.setText(repr(float(curve.y_offset)) if curve else '')
            if curve:
                self.source_label.setText(self._source_text(curve)[1])
            else:
                self.source_label.clear()
        finally:
            self._syncing = False
        self._update_selection_context()

    def _graph_edited(self, *_args):
        if self._syncing:
            return
        error = self._draft_error()
        if error:
            self.status.setText(error)
            self._update_display_status(error)
            return
        comparison = self.comparison
        name = self.name_edit.text().strip()
        renamed = comparison.name != name
        changed = (comparison.plot_title, comparison.x_label, comparison.y_label, comparison.legend_visible) != (
            self.plot_title_edit.text(), self.x_label_edit.text(), self.y_label_edit.text(), self.legend_chk.isChecked())
        comparison.name, comparison.x_label, comparison.y_label = name, self.x_label_edit.text(), self.y_label_edit.text()
        comparison.plot_title = self.plot_title_edit.text()
        comparison.legend_visible = self.legend_chk.isChecked()
        self._draft_fields.difference_update((self.name_edit, self.plot_title_edit, self.x_label_edit, self.y_label_edit))
        changed = self._commit_display() or changed
        self.status.clear()
        self._update_display_status()
        if changed:
            self.render()
        if changed or renamed:
            self.changed.emit()

    def _curve_edited(self, *_args):
        curve = self._selected()
        if self._syncing or curve is None:
            return
        color = self.color_edit.text().strip()
        error = self._draft_error()
        if error:
            self.status.setText(error)
            self._update_display_status(error)
            return
        width = self._edited_width(curve)
        offset = self._number(self.y_offset_edit, 'extra Y offset')
        changed = (curve.label, curve.color, curve.width, curve.y_offset) != (self.label_edit.text(), color, width, offset)
        curve.label, curve.color, curve.width, curve.y_offset = self.label_edit.text(), color, width, offset
        self._update_selection_context()
        self._draft_fields.difference_update((self.label_edit, self.color_edit, self.y_offset_edit, self.width_spin))
        changed = self._commit_display() or changed
        self._displayed_width = self.width_spin.value()
        with QSignalBlocker(self.curve_list):
            for row in range(self.curve_list.count()):
                item = self.curve_list.item(row)
                if item.data(Qt.UserRole) == curve.curve_id:
                    self._update_curve_item(item, curve)
                    break
        self.status.clear()
        self._update_display_status()
        if changed:
            self.render()
            self.changed.emit()

    def _visibility_changed(self, item):
        if not self._syncing:
            curve = self.comparison.curves[item.data(Qt.UserRole)]
            visible = item.checkState() == Qt.Checked
            if not self._finish_curve_action():
                with QSignalBlocker(self.curve_list):
                    item.setCheckState(Qt.Checked if curve.visible else Qt.Unchecked)
                return
            curve.visible = visible
            self.render()
            self.changed.emit()

    def _finish_curve_action(self):
        """Preflight before selection, ordering or an explicit batch operation."""
        try:
            if self.has_pending_project_edits():
                self.finish_editor()
        except ValueError as exc:
            self.status.setText(str(exc))
            self._update_display_status(str(exc))
            return False
        return True

    def _selection_changed(self):
        if self._syncing:
            return
        selected = self._selected_ids()
        # Do not overwrite the accepted selection while rejecting a draft.
        if not self._draft_error():
            self._selection_ids = selected
        count = len(selected)
        self.command_selection_label.setText(f'{count} selected')
        for action in self.style_actions.values():
            action.setEnabled(count > 0)
        self._update_selection_context()
        for widget in (self.batch_color_btn, self.batch_width_btn, self.move_up_btn, self.move_down_btn):
            widget.setEnabled(count > 0)
        for action in (self.show_selected_action, self.hide_selected_action, self.only_selected_action):
            action.setEnabled(count > 0)
        self.show_all_action.setEnabled(bool(self.comparison.curves))
        self.analysis_panel.sync()
        self._apply_selection_highlight()

    def _update_selection_context(self):
        curve = self._selected()
        text = f'{len(self._selected_ids())} selected' + (f' · {curve.label}' if curve else '')
        self.selection_label.setText(text)
        self.selection_label.setToolTip(text)

    def _set_selected_style(self, style):
        ids = self._selected_ids()
        if ids and self._finish_curve_action():
            self._apply_batch_style(ids, 'style', style)

    def _batch_color(self):
        ids = self._selected_ids()
        if not ids or not self._finish_curve_action():
            return
        initial = next(curve.color for curve in self.comparison.curves.values() if curve.curve_id in ids)
        color = QColorDialog.getColor(QColor(initial), self, f'Color for {len(ids)} selected curves')
        if color.isValid():
            self._apply_batch_style(ids, 'color', color.name())

    def _batch_width(self):
        ids = self._selected_ids()
        if not ids or not self._finish_curve_action():
            return
        initial = next(curve.width for curve in self.comparison.curves.values() if curve.curve_id in ids)
        width, accepted = QInputDialog.getDouble(self, 'Selected curve width',
            f'Line width for {len(ids)} selected curves:', initial, min(.000001, initial), max(50., initial), 6)
        if accepted:
            self._apply_batch_style(ids, 'width', width)

    def _apply_batch_style(self, ids, field, value):
        changed = False
        for curve_id in ids:
            curve = self.comparison.curves[curve_id]
            if getattr(curve, field) != value:
                setattr(curve, field, value)
                changed = True
        if changed:
            self.refresh()
            self.changed.emit()

    def _batch_visibility(self, mode):
        ids = self._selected_ids()
        if (not ids and mode != 'all') or not self._finish_curve_action():
            return
        changed = False
        for curve in self.comparison.curves.values():
            if mode in ('only', 'all') or curve.curve_id in ids:
                visible = mode == 'all' or mode == 'show' or mode == 'only' and curve.curve_id in ids
                changed = changed or curve.visible != visible
                curve.visible = visible
        if changed:
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
            # The existing project format writes curves.values() in this order.
            self.comparison.curves = {curve_id: curves[curve_id] for curve_id in order}
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
                self.curve_list.clearSelection()
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
        self.canvas.setFocus(Qt.MouseFocusReason)
        if not candidates:
            self.curve_list.clearSelection()
            return
        curve_id = min(candidates)[2]
        control = 'control' in (event.key or '') or 'ctrl' in (event.key or '')
        for row in range(self.curve_list.count()):
            item = self.curve_list.item(row)
            if item.data(Qt.UserRole) == curve_id:
                command = QItemSelectionModel.Select if control else QItemSelectionModel.ClearAndSelect
                self.curve_list.setCurrentItem(item, command)
                self.curve_list.scrollToItem(item)
                self.inspector.verticalScrollBar().setValue(0)
                break

    def _choose_color(self):
        curve = self._selected()
        if curve:
            color = QColorDialog.getColor(QColor(curve.color), self, 'Curve color')
            if color.isValid():
                self.color_edit.setText(color.name())
                self._curve_edited()

    def _remove_selected(self):
        curve = self._selected()
        if curve and self._finish_curve_action():
            remove_curve(self.workspace, self.comparison_id, curve.curve_id)
            self.refresh()
            self.changed.emit()

    def _open_source(self):
        curve = self._selected()
        if curve:
            self.sourceRequested.emit(curve.sheet_id)

    def finish_editor(self):
        self.validate_editor()
        self.width_spin.interpretText()
        self._graph_edited()
        self._curve_edited()

    def validate_editor(self):
        """Check a pending draft without committing it during batch preflight."""
        error = self._draft_error()
        if error:
            raise ValueError(error)

    def _draft_error(self):
        name = self.name_edit.text().strip()
        if not name or '\x00' in name:
            return 'Enter a nonempty graph name.'
        if any('\x00' in edit.text() for edit in (self.plot_title_edit, self.x_label_edit, self.y_label_edit, self.label_edit)):
            return 'Labels cannot contain null characters.'
        if self._selected() is not None:
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', self.color_edit.text().strip()):
                return 'Use a six-digit color such as #1f77b4.'
            if not self.width_spin.hasAcceptableInput():
                return 'Enter a valid positive line width.'
        try:
            self._display_values()
            if self._selected() is not None:
                self._number(self.y_offset_edit, 'extra Y offset')
        except ValueError as exc:
            return str(exc)
        return ''

    def has_pending_project_edits(self):
        if self._draft_error():
            return True
        comparison, curve = self.comparison, self._selected()
        return ((comparison.name, comparison.plot_title, comparison.x_label, comparison.y_label, comparison.legend_visible) !=
                (self.name_edit.text().strip(), self.plot_title_edit.text(), self.x_label_edit.text(), self.y_label_edit.text(), self.legend_chk.isChecked())
                or (comparison.normalization, comparison.stack_step, comparison.x_range_mode, comparison.x_range) != self._display_values()
                or curve is not None and (curve.label, curve.color, curve.width, curve.y_offset) !=
                (self.label_edit.text(), self.color_edit.text().strip(), self._edited_width(curve),
                 self._number(self.y_offset_edit, 'extra Y offset')))

    def _edited_width(self, curve):
        value = self.width_spin.valueFromText(self.width_spin.text())
        # QDoubleSpinBox is a finite-precision editor; merely displaying and
        # saving an imported style must not round its stored float.
        return curve.width if value == self._displayed_width else value

    def svg_bytes(self):
        self.finish_editor()
        figure = Figure(figsize=(7, 5), dpi=150)
        draw_comparison(figure, self.workspace, self.comparison)
        output = BytesIO()
        figure.savefig(output, format='svg')
        return output.getvalue()

    def export(self, path, *, overwrite=False):
        self.finish_editor()
        path = Path(path)
        extension = path.suffix.lower().lstrip('.')
        if extension not in ('svg', 'png'):
            raise ValueError('Comparison exports support SVG or PNG.')
        figure = Figure(figsize=(7, 5), dpi=150)
        draw_comparison(figure, self.workspace, self.comparison)
        output = BytesIO()
        figure.savefig(output, format=extension, dpi=150)
        with path.open('wb' if overwrite else 'xb') as handle:
            handle.write(output.getvalue())
        return path

    def _export_dialog(self, extension):
        path, _ = QFileDialog.getSaveFileName(self, 'Export comparison', f'{self.comparison.name}.{extension}',
                                             f'{extension.upper()} (*.{extension})')
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != '.' + extension:
            target = target.with_name(target.name + '.' + extension)
        overwrite = target.exists()
        if overwrite and QMessageBox.question(self, 'Replace file?', f'Replace {target}?',
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        try:
            self.export(target, overwrite=overwrite)
            self.status.setText(f'Exported {target.name}')
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def dispose(self):
        self.analysis_panel.dispose()
        self.canvas.mpl_disconnect(self._curve_click_connection)
        self._preview_styles.clear()
        self.figure.clear()
