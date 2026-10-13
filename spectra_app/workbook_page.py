"""Read-only virtual numeric Sheets and retained per-Sheet plotting choices."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, QSignalBlocker
from PySide6.QtGui import QKeySequence, QShortcut, QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QToolBar,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QTabWidget, QTableView, QVBoxLayout, QWidget, QSizePolicy, QToolButton, QMenu)

from .workbook_state import sheet_columns, sheet_column_values, sheet_shape, sheet_numeric_columns, set_sheet_mapping
from .plot_icons import PLOT_STYLES, plot_icon

_CURRENT_TARGET = object()


class WorkbookPlotCommands(QToolBar):
    """One workspace toolbar, bound to the currently visible Sheet."""
    targetSelected = Signal(object)
    targetPinChanged = Signal(bool)
    targetShowRequested = Signal()
    repeatRequested = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__('Workbook plots', parent)
        self.sheet_page = None
        self.setMovable(False)
        self.setFloatable(False)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.style_buttons = {}
        self.style_actions = {}
        for style, label in PLOT_STYLES:
            action = self.addAction(plot_icon(style), label)
            action.setToolTip(f'Create a {label} graph from this Sheet’s X and Y columns')
            action.triggered.connect(lambda _checked=False, value=style: self._plot(value))
            self.style_actions[style] = action
            self.style_buttons[style] = self.widgetForAction(action)
        self.heatmap_action = self.addAction(plot_icon('heatmap'), 'Heatmap')
        self.heatmap_action.triggered.connect(lambda: self._plot('heatmap'))
        self.heatmap_btn = self.widgetForAction(self.heatmap_action)
        self.add_action = self.addAction(plot_icon('add'), 'Add to…')
        self.add_action.triggered.connect(self._append)
        self.add_btn = self.widgetForAction(self.add_action)
        self.add_menu = QMenu(self)
        self.add_action.setMenu(self.add_menu)
        self.add_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.target_combo = QComboBox()
        self.target_combo.setAccessibleName('Add to target XY graph')
        self.target_combo.setMinimumWidth(130)
        self.target_combo.setMaximumWidth(170)
        self.target_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.target_combo.setMinimumContentsLength(12)
        self.addWidget(self.target_combo)
        self.target_combo.currentIndexChanged.connect(self._target_changed)
        # QWidgetActions do not expose combo boxes in Qt's narrow overflow.
        # Keep destination selection available even when Add is disabled.
        self.choose_target_action = self.addAction('Target…')
        self.choose_target_action.setToolTip('Choose comparison destination')
        self.target_menu = QMenu(self)
        self.choose_target_action.setMenu(self.target_menu)
        self.widgetForAction(self.choose_target_action).setPopupMode(QToolButton.InstantPopup)
        self.pin_action = self.addAction('Pin target')
        self.pin_action.setCheckable(True)
        self.pin_action.setToolTip('Keep this comparison destination while visiting other graphs and Workbooks')
        self.pin_action.toggled.connect(self.targetPinChanged)
        self.show_target_action = self.addAction('Show target')
        self.show_target_action.setToolTip('Open and activate the comparison destination')
        self.show_target_action.triggered.connect(self.targetShowRequested)
        self.add_again_action = self.add_menu.addAction('Add again')
        self.add_again_action.setToolTip('Add intentional copies of the selected Sheet curves to the target')
        self.add_again_action.triggered.connect(self._repeat)
        self.source_label = QLabel()
        self.source_label.setMinimumWidth(0)
        self.source_label.setMaximumWidth(170)
        self.source_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.insertWidget(self.actions()[0], self.source_label)

    def bind(self, page):
        self.sheet_page = page
        sheet = page.workspace.sheets[page.sheet_id]
        has_y = bool(sheet.y_columns) and sheet_shape(page.workspace, sheet)[0] > 0
        for action in self.style_actions.values():
            action.setEnabled(has_y)
        supported = sheet.kind == 'dat' and sheet.role in ('original', 'processed')
        self.heatmap_action.setEnabled(supported and has_y)
        self.heatmap_action.setToolTip('Plot selected physical Gate columns from this DAT Sheet' if supported else
            'Heatmap requires a DAT Original or Processed Sheet with physical Gate coordinates.')
        previous = page.target_combo.currentData()
        with QSignalBlocker(self.target_combo):
            self.target_combo.clear()
            for comparison in page.workspace.comparisons.values():
                self.target_combo.addItem(comparison.name, comparison.comparison_id)
            self.target_combo.setCurrentIndex(self.target_combo.findData(previous))
        self.add_action.setEnabled(has_y and self.target_combo.currentData() is not None)
        self.target_menu.clear()
        self.choose_target_action.setEnabled(bool(page.workspace.comparisons))
        for comparison in page.workspace.comparisons.values():
            action = self.target_menu.addAction(comparison.name)
            action.setCheckable(True)
            action.setChecked(comparison.comparison_id == previous)
            action.triggered.connect(lambda _checked=False, target=comparison.comparison_id: self.targetSelected.emit(target))
        # Shared actions also live in the Add menu, including native overflow.
        for action in self.add_menu.actions():
            self.add_menu.removeAction(action)
            if action not in (self.pin_action, self.show_target_action, self.add_again_action):
                action.deleteLater()
        for comparison in page.workspace.comparisons.values():
            action = self.add_menu.addAction(comparison.name)
            action.setCheckable(True)
            action.setChecked(comparison.comparison_id == previous)
            action.triggered.connect(lambda _checked=False, target=comparison.comparison_id: self._append_to(target))
        self.add_menu.addSeparator()
        self.add_menu.addAction(self.pin_action)
        self.add_menu.addAction(self.show_target_action)
        self.add_menu.addAction(self.add_again_action)
        has_target = self.target_combo.currentData() is not None
        self.pin_action.setEnabled(has_target or self.pin_action.isChecked())
        self.show_target_action.setEnabled(has_target)
        self.add_again_action.setEnabled(has_y and has_target)
        target = self.target_combo.currentText() or 'No target graph'
        self.add_action.setToolTip(f'Append Line curves to {target}; existing curves are skipped')
        self.target_combo.setToolTip(f'{"Pinned target" if self.pin_action.isChecked() else "Target"}: {target}')
        text = f'{len(sheet.y_columns)} Y · {sheet.name}'
        self.source_label.setText(text)
        self.source_label.setToolTip(f'{text} · {sheet.mode} · {sheet.source_path or "Embedded values"}')

    def _target_changed(self):
        if self.sheet_page is not None:
            combo = self.sheet_page.target_combo
            combo.setCurrentIndex(combo.findData(self.target_combo.currentData()))
            self.targetSelected.emit(self.target_combo.currentData())
            self.bind(self.sheet_page)

    def _plot(self, style):
        if self.sheet_page is not None:
            self.sheet_page.request_plot(style)

    def _append(self):
        if self.sheet_page is not None and self.target_combo.currentData() is not None:
            self.sheet_page.request_plot('line', self.target_combo.currentData())

    def _append_to(self, target):
        if self.sheet_page is not None:
            self.sheet_page.target_combo.setCurrentIndex(self.sheet_page.target_combo.findData(target))
            self.targetSelected.emit(target)
            self.sheet_page.request_plot('line', target)

    def _repeat(self):
        if self.sheet_page is not None and self.target_combo.currentData() is not None:
            self.repeatRequested.emit(self.sheet_page.sheet_id, self.target_combo.currentData())


def _cell_text(value, precision='.12g'):
    return str(value) if isinstance(value, str) else format(float(value), precision)


def _column_letter(column):
    letters = ''
    while column >= 0:
        letters = chr(ord('A') + column % 26) + letters
        column = column // 26 - 1
    return letters


class _SheetTableView(QTableView):
    columnsChosen = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and event.modifiers() == Qt.ControlModifier:
            if self.currentIndex().isValid():
                self.selectColumn(self.currentIndex().column())
            self.columnsChosen.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SheetTableModel(QAbstractTableModel):
    def __init__(self, workspace, sheet_id, parent=None):
        super().__init__(parent)
        self.workspace, self.sheet_id = workspace, sheet_id
        self.gate_column = None
        self.refresh()

    def refresh(self):
        self.beginResetModel()
        sheet = self.workspace.sheets[self.sheet_id]
        self.columns = sheet_columns(self.workspace, sheet, sheet.mode)
        self.shape = sheet_shape(self.workspace, sheet, sheet.mode)
        self.values = [sheet_column_values(self.workspace, sheet, index, sheet.mode)
                       for index in range(self.shape[1])]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.shape[0]

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.DisplayRole, Qt.EditRole):
            return _cell_text(self.values[index.column()][index.row()])
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            if orientation == Qt.Vertical:
                return str(section + 1)
            prefix = '▶ ' if section == self.gate_column else ''
            sheet = self.workspace.sheets.get(self.sheet_id)
            role = '' if sheet is None else '(X)' if section == sheet.x_column else '(Y)' if section in sheet.y_columns else ''
            return f'{prefix}{_column_letter(section)}{role}: {self.columns[section]}'
        if role == Qt.FontRole and orientation == Qt.Horizontal and section == self.gate_column:
            font = QFont()
            font.setBold(True)
            return font

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable if index.isValid() else Qt.NoItemFlags

    def indicate_gate(self, column):
        self.gate_column = column
        self.headerDataChanged.emit(Qt.Horizontal, 0, self.columnCount() - 1)


class SheetPage(QWidget):
    changed = Signal()
    comparisonRequested = Signal(str, object)
    quickPlotRequested = Signal(str, str, object)
    targetSelected = Signal(object)
    gateRequested = Signal(str, int)
    snapshotRequested = Signal(str)

    def __init__(self, workspace, sheet_id):
        super().__init__()
        self.workspace, self.sheet_id = workspace, sheet_id
        self._syncing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.provenance = QLabel()
        self.provenance.setTextFormat(Qt.PlainText)
        self.provenance.setWordWrap(True)
        self.provenance.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.provenance.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.provenance)
        row = QHBoxLayout()
        self.x_combo = QComboBox()
        self.x_combo.setAccessibleName('X column')
        self.x_combo.setMinimumContentsLength(8)
        self.x_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mode_combo = QComboBox()
        self.mode_combo.setAccessibleName('Sheet values')
        sheet = workspace.sheets[sheet_id]
        self.mode_combo.addItem(sheet.role.replace('_', ' ').title(), sheet.mode)
        self.mode_combo.hide()
        self.mapping_toggle = QToolButton()
        self.mapping_toggle.setText('X/Y controls')
        self.mapping_toggle.setCheckable(True)
        self.mapping_toggle.setArrowType(Qt.RightArrow)
        self.mapping_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.mapping_toggle.setAccessibleName('Show X and Y column controls')
        row.addWidget(self.mapping_toggle)
        row.addStretch(1)
        self.snapshot_btn = QPushButton('Save snapshot…')
        self.snapshot_btn.setAccessibleName('Save named Sheet snapshot')
        self.snapshot_btn.clicked.connect(lambda: self.snapshotRequested.emit(sheet_id))
        row.addWidget(self.snapshot_btn)
        layout.addLayout(row)
        self.mapping_controls = QWidget()
        mapping_layout = QVBoxLayout(self.mapping_controls)
        mapping_layout.setContentsMargins(0, 0, 0, 0)
        x_row = QHBoxLayout()
        x_row.addWidget(QLabel('X:'))
        x_row.addWidget(self.x_combo, 1)
        mapping_layout.addLayout(x_row)
        self.y_list = QListWidget()
        self.y_list.setAccessibleName('Y columns — check one or more')
        self.y_list.setMaximumHeight(86)
        self.y_list.setMinimumHeight(52)
        self.y_list.setToolTip('Check one or more Y columns; each becomes a separate curve.')
        mapping_layout.addWidget(self.y_list)
        layout.addWidget(self.mapping_controls)
        self.mapping_controls.hide()
        self.mapping_toggle.toggled.connect(self._toggle_mapping)
        # Compatibility controls for callers; visible commands live in the shared host.
        self.new_comparison_btn = QPushButton('New comparison', self)
        self.add_comparison_btn = QPushButton('Add to', self)
        self.add_comparison_btn.setFocusPolicy(Qt.TabFocus)
        self.target_combo = QComboBox(self)
        self.target_combo.setAccessibleName('Target comparison')
        self.target_combo.setMinimumContentsLength(8)
        self.target_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for control in (self.new_comparison_btn, self.add_comparison_btn, self.target_combo):
            control.hide()
        self.table = _SheetTableView()
        self.table.setAccessibleName('Read-only numeric Sheet')
        self.model = SheetTableModel(workspace, sheet_id, self)
        self.table.setModel(self.model)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.horizontalHeader().setDefaultSectionSize(125)
        self.table.verticalHeader().setDefaultSectionSize(24)
        layout.addWidget(self.table, 1)
        self.status = QLabel('Select column headers for Y · Ctrl/Shift adds columns · right-click a header to set X · Ctrl+C copies cells')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        self.copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self.copy_selection)
        self.x_combo.currentIndexChanged.connect(self._mapping_changed)
        self.y_list.itemChanged.connect(self._mapping_changed)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.new_comparison_btn.clicked.connect(lambda: self.comparisonRequested.emit(sheet_id, None))
        self.add_comparison_btn.clicked.connect(lambda: self.comparisonRequested.emit(sheet_id, self.target_combo.currentData()))
        self.table.selectionModel().currentColumnChanged.connect(self._column_selected)
        self.table.horizontalHeader().sectionClicked.connect(self._header_selected)
        self.table.horizontalHeader().sectionClicked.connect(self._columns_chosen)
        self.table.columnsChosen.connect(self._columns_chosen)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._header_menu)
        self.refresh()

    def refresh(self):
        self._syncing = True
        try:
            sheet = self.workspace.sheets[self.sheet_id]
            columns = sheet_columns(self.workspace, sheet, sheet.mode)
            self.numeric_columns = set(sheet_numeric_columns(self.workspace, sheet))
            context = sheet.snapshot_note or f'{sheet.name} · {sheet.role.replace("_", " ")} · {sheet.source_path or "Embedded values"}'
            if sheet.role in ('fit_parameters', 'fit_curves'):
                context += f'\nAnalysis axis (X, center and width): {self.workspace.datasets[sheet.dataset_id].current_cube.energy_label}'
            if sheet.role == 'fit_curves':
                session = self.workspace.datasets[sheet.dataset_id]
                context += f'\nGate {sheet.fit_gate_idx} = {session.current_cube.gate[sheet.fit_gate_idx]:g}; components include baseline.'
            if sheet_shape(self.workspace, sheet)[0] == 0:
                context += '\nNo current results. This Sheet will update when results are available.'
            self.provenance.setText(context)
            self.x_combo.clear()
            self.y_list.clear()
            for index, label in enumerate(columns):
                text = f'{index + 1}: {label}'
                self.x_combo.addItem(text, index)
                self.x_combo.model().item(index).setEnabled(index in self.numeric_columns)
                item = QListWidgetItem(text)
                if index in self.numeric_columns:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setCheckState(Qt.Checked if index in sheet.y_columns else Qt.Unchecked)
                self.y_list.addItem(item)
            self.x_combo.setCurrentIndex(sheet.x_column)
            self.x_combo.setEnabled(sheet.role not in ('processed', 'fit_curves') and sheet.kind != 'dat' or sheet.role == 'fit_parameters')
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(sheet.mode))
            self.model.refresh()
            self.refresh_targets()
        finally:
            self._syncing = False
        self._refresh_actions()

    def _toggle_mapping(self, checked):
        self.mapping_controls.setVisible(checked)
        self.mapping_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def _columns_chosen(self, *_):
        if self._syncing:
            return
        sheet = self.workspace.sheets[self.sheet_id]
        ys = sorted(index.column() for index in self.table.selectionModel().selectedColumns()
                    if index.column() != sheet.x_column and index.column() in self.numeric_columns)
        if not ys:
            return  # X-only or cleared selection retains the Sheet's intended Ys.
        self._syncing = True
        try:
            for column in range(self.y_list.count()):
                self.y_list.item(column).setCheckState(Qt.Checked if column in ys else Qt.Unchecked)
        finally:
            self._syncing = False
        self._mapping_changed()

    def _header_menu(self, point):
        header = self.table.horizontalHeader()
        column = header.logicalIndexAt(point)
        if column < 0:
            return
        selected = {index.column() for index in self.table.selectionModel().selectedColumns()}
        if column not in selected:
            self.table.selectColumn(column)
            self._columns_chosen()
        menu = self.column_menu(column)
        menu.exec(header.mapToGlobal(point))
        menu.deleteLater()

    def column_menu(self, column):
        menu = QMenu(self)
        action = menu.addAction('Set as X')
        action.setEnabled(self.x_combo.isEnabled() and column in self.numeric_columns)
        action.triggered.connect(lambda: self.x_combo.setCurrentIndex(column))
        menu.addSeparator()
        sheet = self.workspace.sheets[self.sheet_id]
        ready = bool(sheet.y_columns) and self.model.rowCount() > 0
        for style, label in (*PLOT_STYLES, ('heatmap', 'Heatmap')):
            action = menu.addAction(plot_icon(style), label)
            action.setEnabled(ready and (style != 'heatmap' or self.supports_heatmap()))
            if style == 'heatmap' and not self.supports_heatmap():
                action.setToolTip('Heatmap requires physical Gate columns from a DAT Original or Processed Sheet.')
            action.triggered.connect(lambda _checked=False, value=style: self.request_plot(value))
        append = menu.addMenu(plot_icon('add'), 'Add to…')
        append.setEnabled(ready and bool(self.workspace.comparisons))
        for comparison in self.workspace.comparisons.values():
            action = append.addAction(comparison.name)
            action.triggered.connect(lambda _checked=False, target=comparison.comparison_id: self._append_to(target))
        return menu

    def supports_heatmap(self):
        sheet = self.workspace.sheets[self.sheet_id]
        return sheet.kind == 'dat' and sheet.role in ('original', 'processed')

    def request_plot(self, style, target=None):
        self.quickPlotRequested.emit(self.sheet_id, style, target)

    def _append_to(self, target):
        self.targetSelected.emit(target)
        self.request_plot('line', target)

    def refresh_targets(self, preferred=_CURRENT_TARGET):
        previous = self.target_combo.currentData() if preferred is _CURRENT_TARGET else preferred
        with QSignalBlocker(self.target_combo):
            self.target_combo.clear()
            for comparison in self.workspace.comparisons.values():
                self.target_combo.addItem(comparison.name, comparison.comparison_id)
            index = self.target_combo.findData(previous)
            self.target_combo.setCurrentIndex(index)
        self._refresh_actions()

    def _refresh_actions(self):
        sheet = self.workspace.sheets.get(self.sheet_id)
        if sheet is None:
            for button in (self.snapshot_btn, self.new_comparison_btn, self.add_comparison_btn):
                button.setEnabled(False)
            return
        has_y = bool(sheet.y_columns)
        has_rows = sheet_shape(self.workspace, self.sheet_id)[0] > 0
        self.snapshot_btn.setEnabled(has_rows)
        has_y = has_y and has_rows
        self.new_comparison_btn.setEnabled(has_y)
        self.add_comparison_btn.setEnabled(has_y and self.target_combo.currentData() is not None)

    def _mapping_changed(self, *_args):
        if self._syncing:
            return
        x = self.x_combo.currentIndex()
        self._syncing = True
        try:
            for column in range(self.y_list.count()):
                item = self.y_list.item(column)
                item.setFlags((item.flags() | Qt.ItemIsEnabled) if column != x and column in self.numeric_columns else (item.flags() & ~Qt.ItemIsEnabled))
                if column == x:
                    item.setCheckState(Qt.Unchecked)
        finally:
            self._syncing = False
        ys = [index for index in range(self.y_list.count()) if index != x and index in self.numeric_columns and self.y_list.item(index).checkState() == Qt.Checked]
        set_sheet_mapping(self.workspace, self.sheet_id, x, ys, self.mode_combo.currentData())
        self.model.headerDataChanged.emit(Qt.Horizontal, 0, self.model.columnCount() - 1)
        self._refresh_actions()
        self.changed.emit()

    def _mode_changed(self, *_args):
        if not self._syncing:
            self._mapping_changed()
            self.refresh()

    def _column_selected(self, current, _previous):
        if current.isValid():
            self._header_selected(current.column())

    def _header_selected(self, column):
        if not self._syncing and self.workspace.sheets[self.sheet_id].kind == 'dat' and self.workspace.sheets[self.sheet_id].role in ('original', 'processed') and column > 0:
            self.gateRequested.emit(self.sheet_id, column - 1)

    def copy_selection(self):
        indexes = self.table.selectionModel().selectedIndexes()
        if not indexes:
            return ''
        selected = {(index.row(), index.column()) for index in indexes}
        rows = range(min(row for row, _ in selected), max(row for row, _ in selected) + 1)
        columns = range(min(column for _, column in selected), max(column for _, column in selected) + 1)
        text = '\n'.join('\t'.join(_cell_text(self.model.values[column][row], '.17g')
                          if (row, column) in selected else '' for column in columns) for row in rows)
        QApplication.clipboard().setText(text)
        return text


class WorkbookPage(QWidget):
    changed = Signal()
    contextChanged = Signal()
    comparisonRequested = Signal(str, object)
    quickPlotRequested = Signal(str, str, object)
    targetSelected = Signal(object)
    sheetActivated = Signal()
    gateRequested = Signal(str, int)
    snapshotRequested = Signal(str)

    def __init__(self, workspace, workbook_id):
        super().__init__()
        self.workspace, self.workbook_id = workspace, workbook_id
        self.sheet_pages = {}
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self.sheetActivated)
        self.tabs.currentChanged.connect(self.contextChanged)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.refresh()

    def refresh(self):
        book = self.workspace.workbooks[self.workbook_id]
        for identity in list(self.sheet_pages):
            if identity not in book.sheet_ids:
                page = self.sheet_pages.pop(identity)
                self.tabs.removeTab(self.tabs.indexOf(page))
                page.deleteLater()
        for identity in book.sheet_ids:
            page = self.sheet_pages.get(identity)
            if page is None:
                page = SheetPage(self.workspace, identity)
                page.changed.connect(self.changed)
                page.changed.connect(self.contextChanged)
                page.table.selectionModel().selectionChanged.connect(self.contextChanged)
                page.table.selectionModel().currentChanged.connect(self.contextChanged)
                page.model.modelReset.connect(self.contextChanged)
                page.comparisonRequested.connect(self.comparisonRequested)
                page.quickPlotRequested.connect(self.quickPlotRequested)
                page.targetSelected.connect(self.targetSelected)
                page.gateRequested.connect(self.gateRequested)
                page.snapshotRequested.connect(self.snapshotRequested)
                self.sheet_pages[identity] = page
                self.tabs.addTab(page, self.workspace.sheets[identity].name)
            else:
                page.refresh()
                self.tabs.setTabText(self.tabs.indexOf(page), self.workspace.sheets[identity].name)

    def select_sheet(self, sheet_id):
        self.tabs.setCurrentWidget(self.sheet_pages[sheet_id])

    def context_summary(self, *, detailed=False):
        """Describe existing model metadata without scanning values or changing selection."""
        book = self.workspace.workbooks[self.workbook_id]
        page = self.tabs.currentWidget()
        def short(text):
            return text if detailed or len(text) <= 32 else text[:31] + '…'
        def items(values):
            visible = values if detailed else values[:3]
            text = ', '.join(visible) or 'None'
            return text + (f' … ({len(values)} total)' if len(visible) < len(values) else '')
        lines = ['Workbook: ' + short(book.name)]
        if page is not None and page.sheet_id in self.workspace.sheets:
            sheet, model = self.workspace.sheets[page.sheet_id], page.model
            def column_label(column):
                return f'{_column_letter(column)}: {short(model.columns[column])}' if 0 <= column < len(model.columns) else 'None'
            selection = page.table.selectionModel()
            columns = sorted({column for span in selection.selection()
                              for column in range(span.left(), span.right() + 1)})
            lines.extend(['', 'Current Sheet: ' + short(sheet.name),
                          f'{model.rowCount()} rows · {model.columnCount()} columns',
                          'Role: ' + sheet.role.replace('_', ' '),
                          'Values: ' + ('Original' if sheet.mode == 'raw' else 'Processed'), '',
                          'X: ' + column_label(sheet.x_column),
                          'Y: ' + items([column_label(column) for column in sheet.y_columns]), '',
                          'Current column: ' + column_label(selection.currentIndex().column()),
                          'Selected columns: ' + items([column_label(column) for column in columns])])
        lines.extend(['', 'Sheets: ' + items([short(self.workspace.sheets[identity].name) for identity in book.sheet_ids
                                     if identity in self.workspace.sheets]), '',
                      'Choose X and Y columns in the Workbook to create or add to a graph.'])
        return '\n'.join(lines)

    def refresh_targets(self, preferred=_CURRENT_TARGET):
        for identity, page in list(self.sheet_pages.items()):
            if identity in self.workspace.sheets:
                page.refresh_targets(preferred)

    def dispose(self):
        pass
