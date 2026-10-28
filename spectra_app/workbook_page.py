"""Virtual Quick Workbooks with retained, range-based sheet state."""
from __future__ import annotations

from PySide6.QtCore import (QAbstractTableModel, QItemSelection, QItemSelectionModel,
    QModelIndex, QObject, Property, Qt, Signal, Slot, QSignalBlocker)
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication

from .quick_ui import QuickPanel
from .workbook_state import (sheet_columns, sheet_column_values, sheet_column_metadata,
    sheet_shape, sheet_numeric_columns, set_sheet_mapping, set_sheet_column_metadata)

_CURRENT_TARGET = object()


def _cell_text(value, precision='.12g'):
    return str(value) if isinstance(value, str) else format(float(value), precision)


def _column_letter(column):
    letters = ''
    while column >= 0:
        letters = chr(ord('A') + column % 26) + letters
        column = column // 26 - 1
    return letters


class SheetTableModel(QAbstractTableModel):
    columnLabelsChanged = Signal(int)

    def __init__(self, workspace, sheet_id, parent=None):
        super().__init__(parent)
        self.workspace, self.sheet_id = workspace, sheet_id
        self.gate_column = None
        self.refresh()

    def refresh(self):
        self.beginResetModel()
        sheet = self.workspace.sheets[self.sheet_id]
        self._sheet = sheet  # Delayed callbacks may follow source removal.
        self.columns = sheet_columns(self.workspace, sheet, sheet.mode)
        self.shape = sheet_shape(self.workspace, sheet, sheet.mode)
        self.metadata = [sheet_column_metadata(self.workspace, sheet, column) for column in range(self.shape[1])]
        self.values = [sheet_column_values(self.workspace, sheet, index, sheet.mode)
                       for index in range(self.shape[1])]
        self.endResetModel()

    def roleNames(self):
        return {int(Qt.DisplayRole): b'display'}

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
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            if orientation == Qt.Vertical:
                return str(section + 1)
            designation = self.header_rows(section)[0]
            return designation if role == Qt.DisplayRole else f'{designation}: {self.columns[section]}'
        if role == Qt.FontRole and orientation == Qt.Horizontal and section == self.gate_column:
            font = QFont()
            font.setBold(True)
            return font

    def header_rows(self, column):
        sheet = self._sheet
        role = '(X)' if column == sheet.x_column else '(Y)' if column in sheet.y_columns else ''
        metadata = self.metadata[column]
        return f'{_column_letter(column)}{role}', metadata['long_name'], metadata['units']

    def set_column_metadata(self, column, field, text):
        sheet = self.workspace.sheets[self.sheet_id]
        if sheet_column_metadata(self.workspace, sheet, column)[field] == text:
            return
        if set_sheet_column_metadata(self.workspace, self.sheet_id, column, **{field: text}):
            self.columns = sheet_columns(self.workspace, sheet)
            self.metadata[column] = sheet_column_metadata(self.workspace, sheet, column)
            self.headerDataChanged.emit(Qt.Horizontal, column, column)
            self.columnLabelsChanged.emit(column)

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable if index.isValid() else Qt.NoItemFlags

    def indicate_gate(self, column):
        if column == self.gate_column:
            return
        previous, self.gate_column = self.gate_column, column
        for changed in (previous, column):
            if changed is not None and 0 <= changed < self.columnCount():
                self.headerDataChanged.emit(Qt.Horizontal, changed, changed)


class SheetHeaderModel(QAbstractTableModel):
    """Three virtual rows; never asks the scientific matrix for its contents."""
    TextRole, TooltipRole, GateRole = Qt.UserRole + 1, Qt.UserRole + 2, Qt.UserRole + 3

    def __init__(self, cells, parent):
        super().__init__(parent)
        self.cells = cells
        cells.modelAboutToBeReset.connect(self.beginResetModel)
        cells.modelReset.connect(self.endResetModel)
        cells.headerDataChanged.connect(self._headers_changed)

    def roleNames(self):
        return {self.TextRole: b'label', self.TooltipRole: b'tooltip', self.GateRole: b'gate'}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.cells.columnCount()

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.DisplayRole, self.TextRole):
            return self.cells.header_rows(index.column())[index.row()]
        if role == self.TooltipRole:
            return f'{("Designation", "Long Name", "Units")[index.row()]}: {self.cells.header_rows(index.column())[index.row()]}'
        if role == self.GateRole:
            return self.cells.gate_column == index.column()

    def _headers_changed(self, orientation, first, last):
        if orientation == Qt.Horizontal and self.columnCount():
            self.dataChanged.emit(self.index(0, first), self.index(2, last), [])


class SheetPage(QObject):
    """Retained controller/model/selection; the Workbook owns the sole scene."""
    changed = Signal()
    draftChanged = Signal()
    contextChanged = Signal()
    stateChanged = Signal()
    selectionChanged = Signal()
    widthsChanged = Signal()
    editorChanged = Signal()
    comparisonRequested = Signal(str, object)
    quickPlotRequested = Signal(str, str, object)
    targetSelected = Signal(object)
    gateRequested = Signal(str, int)
    snapshotRequested = Signal(str)
    viewRestoreRequested = Signal()
    revealCellRequested = Signal(int, int)
    revealColumnRequested = Signal(int)

    def __init__(self, workspace, sheet_id, parent=None):
        super().__init__(parent)
        self.workspace, self.sheet_id = workspace, sheet_id
        self._retired = self._syncing = self._header_gesture = False
        self._guard = None
        self._selection_revision = self._width_revision = 0
        self._anchor = (0, 0)
        self._gesture_selection = QItemSelection()
        self._widths, self._manual_widths = [], {}
        self._font = QFont(QGuiApplication.font())
        self._editor = None
        self._editor_original = self._editor_draft = self._editor_error = ''
        self._target_id = None
        self.scroll_x = self.scroll_y = 0.0
        self.model = SheetTableModel(workspace, sheet_id, self)
        self.header_model = SheetHeaderModel(self.model, self)
        self.selection_model = QItemSelectionModel(self.model, self)
        self.selection_model.selectionChanged.connect(self._selection_changed)
        self.selection_model.currentChanged.connect(self._current_changed)
        self.model.columnLabelsChanged.connect(self._metadata_changed)
        self.refresh()

    tableModel = Property(QObject, lambda self: self.model, constant=True)
    headerModel = Property(QObject, lambda self: self.header_model, constant=True)
    selectionRevision = Property(int, lambda self: self._selection_revision, notify=selectionChanged)
    widthRevision = Property(int, lambda self: self._width_revision, notify=widthsChanged)
    rowCount = Property(int, lambda self: self.model.rowCount(), notify=stateChanged)
    columnCount = Property(int, lambda self: self.model.columnCount(), notify=stateChanged)
    sheetId = Property(str, lambda self: self.sheet_id, constant=True)
    name = Property(str, lambda self: self.model._sheet.name, notify=stateChanged)
    xColumn = Property(int, lambda self: self.model._sheet.x_column, notify=stateChanged)
    yColumns = Property('QVariantList', lambda self: list(self.model._sheet.y_columns), notify=stateChanged)
    xEditable = Property(bool, lambda self: self.model._sheet.role not in ('processed', 'fit_curves')
        and self.model._sheet.kind != 'dat' or self.model._sheet.role == 'fit_parameters', notify=stateChanged)
    plotEnabled = Property(bool, lambda self: bool(self.model._sheet.y_columns) and self.model.rowCount() > 0
                           and not self._retired, notify=stateChanged)
    heatmapEnabled = Property(bool, lambda self: self.plotEnabled and self.supports_heatmap(), notify=stateChanged)
    currentRow = Property(int, lambda self: self.selection_model.currentIndex().row(), notify=selectionChanged)
    currentColumn = Property(int, lambda self: self.selection_model.currentIndex().column(), notify=selectionChanged)
    editorColumn = Property(int, lambda self: self._editor[0] if self._editor else -1, notify=editorChanged)
    editorRow = Property(int, lambda self: 1 if self._editor and self._editor[1] == 'long_name' else 2, notify=editorChanged)
    editorText = Property(str, lambda self: self._editor_draft, notify=editorChanged)
    editorError = Property(str, lambda self: self._editor_error, notify=editorChanged)
    editorLabel = Property(str, lambda self: f'Column {_column_letter(self._editor[0])} '
        + ('Long Name' if self._editor[1] == 'long_name' else 'Units') if self._editor else '', notify=editorChanged)
    savedScrollX = Property(float, lambda self: self.scroll_x, notify=stateChanged)
    savedScrollY = Property(float, lambda self: self.scroll_y, notify=stateChanged)
    columnChoices = Property('QVariantList', lambda self: [dict(label=f'{i + 1}: {name}', value=i,
        numeric=i in self.numeric_columns, checked=i in self.model._sheet.y_columns)
        for i, name in enumerate(self.model.columns)], notify=stateChanged)
    targets = Property('QVariantList', lambda self: [dict(label=c.name, value=c.comparison_id)
        for c in self.workspace.comparisons.values()], notify=stateChanged)
    targetId = Property('QVariant', lambda self: self._target_id, notify=stateChanged)

    def _provenance(self):
        sheet = self.model._sheet
        context = [sheet.snapshot_note] if sheet.snapshot_note else []
        session = self.workspace.datasets.get(sheet.dataset_id)
        if session and sheet.role in ('fit_parameters', 'fit_curves'):
            context.append(f'Analysis axis (X, center and width): {session.current_cube.energy_label}')
        if session and sheet.role == 'fit_curves' and 0 <= sheet.fit_gate_idx < len(session.current_cube.gate):
            context.append(f'Gate {sheet.fit_gate_idx} = {session.current_cube.gate[sheet.fit_gate_idx]:g}; components include baseline.')
        if not self.model.rowCount():
            context.append('No current results. This Sheet will update when results are available.')
        return '\n'.join(context)

    provenance = Property(str, _provenance, notify=stateChanged)

    def _allowed(self):
        return not self._retired and self.sheet_id in self.workspace.sheets and (self._guard is None or self._guard())

    def refresh(self):
        if self._retired or self.sheet_id not in self.workspace.sheets:
            return
        self._syncing = True
        try:
            self.numeric_columns = set(sheet_numeric_columns(self.workspace, self.sheet_id))
            self.model.refresh()
            self._fit_widths()
            self.scroll_x = self.scroll_y = 0.0
            self.refresh_targets()
            self._selection_changed()
        finally:
            self._syncing = False
        self.stateChanged.emit()

    def _fit_widths(self):
        font = QFont(self._font)
        font.setBold(True)
        metrics = QFontMetrics(font)
        self._widths = [self._manual_widths.get(c, max(100,
            max(metrics.horizontalAdvance(text) for text in self.model.header_rows(c)) + 24))
            for c in range(self.model.columnCount())]
        self._width_revision += 1
        self.widthsChanged.emit()

    def set_font(self, font):
        self._font = QFont(font)
        self._fit_widths()

    @Slot(int, result=float)
    def columnWidth(self, column):
        return self._widths[column] if 0 <= column < len(self._widths) else 100.0

    @Slot(int, result=float)
    def columnPosition(self, column):
        return float(sum(self._widths[:max(0, column)]))

    @Slot(int, float)
    def resizeColumn(self, column, width):
        if self._allowed() and 0 <= column < len(self._widths):
            self._manual_widths[column] = self._widths[column] = max(40, round(width))
            self._width_revision += 1
            self.widthsChanged.emit()

    @Slot(int, int, result=bool)
    def isSelected(self, row, column):
        return any(r.top() <= row <= r.bottom() and r.left() <= column <= r.right()
                   for r in self.selection_model.selection())

    def _selection_changed(self, *_):
        self._selection_revision += 1
        self.selectionChanged.emit()
        self.contextChanged.emit()

    def _current_changed(self, current, previous):
        self._selection_changed()
        if not self._syncing and not self._header_gesture and current.isValid() and current.column() != previous.column():
            self._header_selected(current.column())

    def _select_rectangle(self, first, last, modifiers, *, base=None):
        if not self.model.rowCount() or not self.model.columnCount():
            return
        top, bottom = sorted((max(0, min(first[0], self.model.rowCount() - 1)), max(0, min(last[0], self.model.rowCount() - 1))))
        left, right = sorted((max(0, min(first[1], self.model.columnCount() - 1)), max(0, min(last[1], self.model.columnCount() - 1))))
        selection = QItemSelection(self.model.index(top, left), self.model.index(bottom, right))
        if modifiers & Qt.ControlModifier.value:
            merged = QItemSelection(base if base is not None else self.selection_model.selection())
            merged.merge(selection, QItemSelectionModel.Toggle)
            selection = merged
        self.selection_model.select(selection, QItemSelectionModel.ClearAndSelect)

    @Slot(int, int, int)
    def beginBody(self, row, column, modifiers=0):
        if not self._allowed() or not self.finish_editor() or not (0 <= row < self.model.rowCount() and 0 <= column < self.model.columnCount()):
            return
        self._gesture_selection = self.selection_model.selection()
        if not modifiers & Qt.ShiftModifier.value:
            self._anchor = row, column
        self._body_modifiers = modifiers
        self._select_rectangle(self._anchor, (row, column), modifiers, base=self._gesture_selection)
        self.selection_model.setCurrentIndex(self.model.index(row, column), QItemSelectionModel.NoUpdate)

    @Slot(int, int)
    def dragBody(self, row, column):
        if self._allowed():
            self._select_rectangle(self._anchor, (row, column), getattr(self, '_body_modifiers', 0), base=self._gesture_selection)

    @Slot(int, int)
    def beginHeader(self, column, modifiers=0):
        if not self._allowed() or not self.finish_editor() or not 0 <= column < self.model.columnCount():
            return
        self._header_gesture = True
        self._gesture_selection = self.selection_model.selection()
        if not modifiers & Qt.ShiftModifier.value:
            self._header_anchor = column
        self._header_modifiers = modifiers
        self._header_last = column
        self._anchor = (0, column)
        self._select_rectangle((0, getattr(self, '_header_anchor', column)), (self.model.rowCount() - 1, column), modifiers, base=self._gesture_selection)
        self.selection_model.setCurrentIndex(self.model.index(0, column), QItemSelectionModel.NoUpdate)

    @Slot(int)
    def dragHeader(self, column):
        if self._header_gesture and self._allowed():
            self._header_last = column
            self._select_rectangle((0, self._header_anchor), (self.model.rowCount() - 1, column), self._header_modifiers, base=self._gesture_selection)

    @Slot()
    def endHeader(self):
        if self._header_gesture:
            self._header_gesture = False
            if self._allowed():
                self._columns_chosen()
                self._header_selected(self._header_last)

    @Slot(int, int)
    def selectRow(self, row, modifiers=0):
        if self._allowed() and self.finish_editor():
            if not modifiers & Qt.ShiftModifier.value:
                self._row_anchor = row
            self._select_rectangle((getattr(self, '_row_anchor', row), 0), (row, self.model.columnCount() - 1), modifiers)

    @Slot(int, int)
    def beginRow(self, row, modifiers=0):
        if self._allowed() and self.finish_editor():
            self._gesture_selection = self.selection_model.selection()
            self._row_modifiers = modifiers
            if not modifiers & Qt.ShiftModifier.value:
                self._row_anchor = row
            self.dragRow(row)

    @Slot(int)
    def dragRow(self, row):
        if self._allowed():
            self._select_rectangle((getattr(self, '_row_anchor', row), 0),
                (row, self.model.columnCount() - 1), getattr(self, '_row_modifiers', 0), base=self._gesture_selection)

    @Slot(int, int, result=str)
    def headerTooltip(self, column, row):
        if 0 <= column < self.model.columnCount() and 0 <= row < 3:
            return f'{("Designation", "Long Name", "Units")[row]}: {self.model.header_rows(column)[row]}'
        return ''

    @Slot(result=bool)
    def openContext(self):
        return self._allowed()

    @Slot(int, int, result=bool)
    def openColumnContext(self, column, row):
        if not self._allowed() or not self.finish_editor():
            return False
        if row == 0 and column not in self._selected_whole_columns():
            self.selectColumn(column)
            self._columns_chosen()
        return True

    @Slot(int)
    def selectColumn(self, column):
        if self._allowed():
            self._select_rectangle((0, column), (self.model.rowCount() - 1, column), 0)

    @Slot()
    def selectAll(self):
        if self._allowed() and self.finish_editor():
            self._select_rectangle((0, 0), (self.model.rowCount() - 1, self.model.columnCount() - 1), 0)
            self._columns_chosen()

    @Slot()
    def clearSelection(self):
        if self._allowed():
            self.selection_model.clearSelection()

    @Slot(int, int)
    def setCurrentCell(self, row, column):
        if self._allowed():
            self.selection_model.setCurrentIndex(self.model.index(row, column), QItemSelectionModel.NoUpdate)
            self._anchor = (row, column)
            self.revealCellRequested.emit(row, column)

    @Slot(int, int, int)
    def moveCurrent(self, row_delta, column_delta, modifiers=0):
        row = min(max(0, self.currentRow + row_delta), self.model.rowCount() - 1)
        column = min(max(0, self.currentColumn + column_delta), self.model.columnCount() - 1)
        if modifiers & Qt.ControlModifier.value:
            self.setCurrentCell(row, column)
        else:
            self.beginBody(row, column, modifiers)
            self.revealCellRequested.emit(row, column)

    @Slot()
    def chooseCurrentColumn(self):
        self.beginHeader(self.currentColumn, 0)
        self.endHeader()

    def _selected_whole_columns(self):
        rows = self.model.rowCount()
        if not rows:
            return []
        intervals = {}
        for span in self.selection_model.selection():
            for column in range(span.left(), span.right() + 1):
                intervals.setdefault(column, []).append((span.top(), span.bottom()))
        columns = []
        for column, ranges in sorted(intervals.items()):
            covered = 0
            for top, bottom in sorted(ranges):
                if top > covered:
                    break
                covered = max(covered, bottom + 1)
                if covered >= rows:
                    columns.append(column)
                    break
        return columns

    def _columns_chosen(self):
        if not self._syncing:
            sheet = self.model._sheet
            ys = [c for c in self._selected_whole_columns() if c != sheet.x_column and c in self.numeric_columns]
            if ys:
                self.set_mapping(sheet.x_column, ys)

    def set_mapping(self, x, ys):
        if not self._allowed():
            self.stateChanged.emit()  # Restore a Quick control's attempted edit.
            return
        sheet = self.model._sheet
        if x != sheet.x_column and (not self.xEditable or x not in self.numeric_columns):
            return
        ys = sorted({c for c in ys if c != x and c in self.numeric_columns})
        if (x, ys) == (sheet.x_column, sheet.y_columns):
            return
        changed_columns = set(ys).symmetric_difference(sheet.y_columns)
        if x != sheet.x_column:
            changed_columns.update((x, sheet.x_column))
        set_sheet_mapping(self.workspace, self.sheet_id, x, ys, sheet.mode)
        for column in sorted(changed_columns):
            self.model.headerDataChanged.emit(Qt.Horizontal, column, column)
        self.stateChanged.emit()
        self.changed.emit()
        self.contextChanged.emit()

    @Slot(int)
    def setX(self, column):
        self.set_mapping(column, self.model._sheet.y_columns)

    @Slot(int, bool)
    def setY(self, column, checked):
        ys = set(self.model._sheet.y_columns)
        ys.add(column) if checked else ys.discard(column)
        self.set_mapping(self.model._sheet.x_column, ys)

    def _header_selected(self, column):
        sheet = self.workspace.sheets.get(self.sheet_id)
        if not self._syncing and sheet is not None and sheet.kind == 'dat' and sheet.role in ('original', 'processed') and column > 0 and column != self.model.gate_column:
            self.gateRequested.emit(self.sheet_id, column - 1)

    @Slot(int, str, result=bool)
    def editMetadata(self, column, field='long_name'):
        if not self._allowed() or field not in ('long_name', 'units') or not 0 <= column < self.model.columnCount() or not self.finish_editor():
            return False
        self._editor = column, field
        self._editor_original = self._editor_draft = self.model.metadata[column][field]
        self._editor_error = ''
        self.revealColumnRequested.emit(column)
        self.editorChanged.emit()
        return True

    @Slot(str)
    def setEditorText(self, text):
        if self._editor is not None and self._allowed() and text != self._editor_draft:
            self._editor_draft, self._editor_error = text, ''
            self.draftChanged.emit()

    @Slot(result=bool)
    def commitEditor(self):
        return self._allowed() and self.finish_editor()

    @Slot()
    def cancelEditor(self):
        if self._allowed():
            self.finish_editor(cancel=True)

    def finish_editor(self, *, cancel=False):
        if self._editor is None:
            return True
        if not cancel:
            try:
                self.model.set_column_metadata(*self._editor, self._editor_draft)
            except (ValueError, KeyError) as exc:
                self._editor_error = str(exc)
                self.editorChanged.emit()
                return False
        self._editor = None
        self._editor_draft = self._editor_error = ''
        self.editorChanged.emit()
        self.draftChanged.emit()
        return True

    def has_pending_project_edits(self):
        return self._editor is not None and self._editor_draft != self._editor_original

    def _metadata_changed(self, column):
        if column not in self._manual_widths:
            font = QFont(self._font)
            font.setBold(True)
            metrics = QFontMetrics(font)
            self._widths[column] = max(100, max(metrics.horizontalAdvance(text) for text in self.model.header_rows(column)) + 24)
            self._width_revision += 1
            self.widthsChanged.emit()
        self.stateChanged.emit()
        self.changed.emit()

    def supports_heatmap(self):
        sheet = self.model._sheet
        return sheet.kind == 'dat' and sheet.role in ('original', 'processed')

    @Slot(str)
    def plot(self, style):
        self.request_plot(style)

    def request_plot(self, style, target=None):
        if self._allowed() and self.finish_editor() and self.plotEnabled and (style != 'heatmap' or self.heatmapEnabled):
            self.quickPlotRequested.emit(self.sheet_id, style, target)

    @Slot(str)
    def appendTo(self, target):
        if self._allowed() and target in self.workspace.comparisons:
            self._target_id = target
            self.targetSelected.emit(target)
            self.request_plot('line', target)

    @Slot(str)
    def chooseTarget(self, target):
        if self._allowed() and target in self.workspace.comparisons:
            self._target_id = target
            self.targetSelected.emit(target)
            self.stateChanged.emit()

    @Slot()
    def snapshot(self):
        if self._allowed() and self.model.rowCount() and self.finish_editor():
            self.snapshotRequested.emit(self.sheet_id)

    def refresh_targets(self, preferred=_CURRENT_TARGET):
        previous = self._target_id if preferred is _CURRENT_TARGET else preferred
        self._target_id = previous if previous in self.workspace.comparisons else None
        self.stateChanged.emit()

    @Slot(result=str)
    def copy_selection(self):
        if not self._allowed():
            return ''
        spans = list(self.selection_model.selection())
        if not spans:
            return ''
        rows = range(min(r.top() for r in spans), max(r.bottom() for r in spans) + 1)
        columns = range(min(r.left() for r in spans), max(r.right() for r in spans) + 1)
        # Explicit clipboard creation is the sole per-cell selection operation.
        text = '\n'.join('\t'.join(_cell_text(self.model.values[c][r], '.17g')
            if any(s.top() <= r <= s.bottom() and s.left() <= c <= s.right() for s in spans)
            else '' for c in columns) for r in rows)
        QGuiApplication.clipboard().setText(text)
        return text

    @Slot(float, float)
    def rememberScroll(self, x, y):
        if not self._retired:
            self.scroll_x, self.scroll_y = max(0.0, x), max(0.0, y)

    def capture_view_state(self):
        current = self.selection_model.currentIndex()
        return dict(ranges=[(r.top(), r.left(), r.bottom(), r.right()) for r in self.selection_model.selection()],
            current=(current.row(), current.column()), scroll=(self.scroll_x, self.scroll_y), widths=dict(self._manual_widths))

    def restore_view_state(self, state):
        syncing, self._syncing = self._syncing, True
        try:
            with QSignalBlocker(self.selection_model):
                selection = QItemSelection()
                rows, columns = self.model.rowCount(), self.model.columnCount()
                for top, left, bottom, right in state['ranges']:
                    if rows and columns and top < rows and left < columns:
                        selection.select(self.model.index(max(0, top), max(0, left)),
                            self.model.index(min(bottom, rows - 1), min(right, columns - 1)))
                self.selection_model.select(selection, QItemSelectionModel.ClearAndSelect)
                self.selection_model.setCurrentIndex(self.model.index(*state['current']), QItemSelectionModel.NoUpdate)
            self.scroll_x, self.scroll_y = state['scroll']
            self._manual_widths = dict(state.get('widths', self._manual_widths))
            self._selection_changed()
            self.viewRestoreRequested.emit()
        finally:
            self._syncing = syncing

    def dispose(self):
        self._retired = True
        self._guard = lambda: False


class WorkbookController(QObject):
    """Retained Workbook state, independent of the lifetime of its Quick scene."""
    changed = Signal()
    draftChanged = Signal()
    contextChanged = Signal()
    comparisonRequested = Signal(str, object)
    quickPlotRequested = Signal(str, str, object)
    targetSelected = Signal(object)
    sheetActivated = Signal()
    gateRequested = Signal(str, int)
    snapshotRequested = Signal(str)
    stateChanged = Signal()
    sheetAboutToChange = Signal()

    def __init__(self, workspace, workbook_id, parent=None, *, font=None):
        super().__init__(parent)
        self.workspace, self.workbook_id = workspace, workbook_id
        self.sheet_pages = {}
        self._current_id = self._input_guard = None
        self._retired = False
        self._font = QFont(QGuiApplication.font() if font is None else font)
        self.refresh()

    currentSheet = Property(QObject, lambda self: self.current_sheet, notify=stateChanged)
    sheets = Property('QVariantList', lambda self: [dict(value=sid, label=self.workspace.sheets[sid].name,
        tooltip=f'{self.workspace.sheets[sid].name}\nRole: {self.workspace.sheets[sid].role.replace("_", " ")}\nSource: {self.workspace.sheets[sid].source_path or "Embedded values"}')
        for sid in self.sheet_pages if sid in self.workspace.sheets], notify=stateChanged)
    currentSheetId = Property(str, lambda self: self._current_id or '', notify=stateChanged)

    @property
    def current_sheet(self):
        return self.sheet_pages.get(self._current_id)

    def set_input_guard(self, guard):
        self._input_guard = guard

    def _allowed(self):
        return not self._retired and self.workbook_id in self.workspace.workbooks and (self._input_guard is None or self._input_guard())

    def finish_editor(self):
        for page in self.sheet_pages.values():
            if not page.finish_editor():
                raise ValueError('Correct the Sheet Long Name or Units before saving the project.')

    def has_pending_project_edits(self):
        return any(page.has_pending_project_edits() for page in self.sheet_pages.values())

    def refresh(self):
        if self._retired or self.workbook_id not in self.workspace.workbooks:
            return
        book = self.workspace.workbooks[self.workbook_id]
        for identity in list(self.sheet_pages):
            if identity not in book.sheet_ids:
                page = self.sheet_pages.pop(identity)
                page.dispose()
                page.deleteLater()
        for identity in book.sheet_ids:
            if identity not in self.workspace.sheets:
                continue
            page = self.sheet_pages.get(identity)
            if page is None:
                page = SheetPage(self.workspace, identity, self)
                page._guard = self._allowed
                page.set_font(self._font)
                for name in ('changed', 'draftChanged', 'contextChanged', 'comparisonRequested',
                             'quickPlotRequested', 'targetSelected', 'gateRequested', 'snapshotRequested'):
                    getattr(page, name).connect(getattr(self, name))
                self.sheet_pages[identity] = page
            else:
                page.refresh()
        if self._current_id not in self.sheet_pages:
            self._current_id = next(iter(self.sheet_pages), None)
        self.stateChanged.emit()
        self.contextChanged.emit()

    @Slot(str, result=bool)
    def selectSheet(self, sheet_id):
        return self.select_sheet(sheet_id)

    def select_sheet(self, sheet_id):
        if sheet_id == self._current_id:
            return True
        if sheet_id not in self.sheet_pages or not self._allowed():
            return False
        if self.current_sheet is not None and not self.current_sheet.finish_editor():
            return False
        self.sheetAboutToChange.emit()
        self._current_id = sheet_id
        self.stateChanged.emit()
        self.sheetActivated.emit()
        self.contextChanged.emit()
        return True

    def context_summary(self, *, detailed=False):
        book, page = self.workspace.workbooks[self.workbook_id], self.current_sheet
        def short(text):
            return text if detailed or len(text) <= 32 else text[:31] + '…'
        def items(values):
            visible = values if detailed else values[:3]
            return (', '.join(visible) or 'None') + (f' … ({len(values)} total)' if len(visible) < len(values) else '')
        lines = ['Workbook: ' + short(book.name)]
        if page is not None and page.sheet_id in self.workspace.sheets:
            sheet, model = self.workspace.sheets[page.sheet_id], page.model
            def label(c):
                return f'{_column_letter(c)}: {short(model.columns[c])}' if 0 <= c < len(model.columns) else 'None'
            columns = sorted({c for span in page.selection_model.selection() for c in range(span.left(), span.right() + 1)})
            lines.extend(['', 'Current Sheet: ' + short(sheet.name), f'{model.rowCount()} rows · {model.columnCount()} columns',
                'Role: ' + sheet.role.replace('_', ' '), 'Values: ' + ('Original' if sheet.mode == 'raw' else 'Processed'), '',
                'X: ' + label(sheet.x_column), 'Y: ' + items([label(c) for c in sheet.y_columns]), '',
                'Current column: ' + label(page.currentColumn), 'Selected columns: ' + items([label(c) for c in columns])])
        lines.extend(['', 'Sheets: ' + items([short(self.workspace.sheets[sid].name) for sid in book.sheet_ids if sid in self.workspace.sheets]), '',
            'Choose X and Y columns in the Workbook to create or add to a graph.'])
        return '\n'.join(lines)

    def refresh_targets(self, preferred=_CURRENT_TARGET):
        for page in self.sheet_pages.values():
            page.refresh_targets(preferred)

    def capture_view_state(self):
        return {sid: page.capture_view_state() for sid, page in self.sheet_pages.items()}

    def restore_view_state(self, states):
        for sid, state in states.items():
            if sid in self.sheet_pages:
                self.sheet_pages[sid].restore_view_state(state)

    def set_font(self, font):
        self._font = QFont(font)
        for page in self.sheet_pages.values():
            if page._font != self._font:
                page.set_font(self._font)

    def dispose(self):
        self._retired = True
        for page in self.sheet_pages.values():
            page.dispose()


class WorkbookPage(QuickPanel):
    """The visible document host for the retained Workbook controller."""
    changed = Signal()
    draftChanged = Signal()
    contextChanged = Signal()
    comparisonRequested = Signal(str, object)
    quickPlotRequested = Signal(str, str, object)
    targetSelected = Signal(object)
    sheetActivated = Signal()
    gateRequested = Signal(str, int)
    snapshotRequested = Signal(str)
    stateChanged = Signal()
    sheetAboutToChange = Signal()

    def __init__(self, workspace, workbook_id):
        self.controller = WorkbookController(workspace, workbook_id)
        super().__init__('Workbook.qml', self.controller, expanding=True, bridge_property='bridge')
        self.controller.setParent(self)
        self.controller.set_font(self.font())
        for name in ('changed', 'draftChanged', 'contextChanged', 'comparisonRequested',
                     'quickPlotRequested', 'targetSelected', 'sheetActivated', 'gateRequested',
                     'snapshotRequested', 'stateChanged', 'sheetAboutToChange'):
            getattr(self.controller, name).connect(getattr(self, name))

    workspace = property(lambda self: self.controller.workspace)
    workbook_id = property(lambda self: self.controller.workbook_id)
    sheet_pages = property(lambda self: self.controller.sheet_pages)
    current_sheet = property(lambda self: self.controller.current_sheet)
    currentSheet = Property(QObject, lambda self: self.controller.currentSheet, notify=stateChanged)
    sheets = Property('QVariantList', lambda self: self.controller.sheets, notify=stateChanged)
    currentSheetId = Property(str, lambda self: self.controller.currentSheetId, notify=stateChanged)

    def set_input_guard(self, guard):
        self.controller.set_input_guard(guard)

    def finish_editor(self):
        self.controller.finish_editor()

    def has_pending_project_edits(self):
        return self.controller.has_pending_project_edits()

    def refresh(self):
        self.controller.refresh()

    @Slot(str, result=bool)
    def selectSheet(self, sheet_id):
        return self.controller.selectSheet(sheet_id)

    def select_sheet(self, sheet_id):
        return self.controller.select_sheet(sheet_id)

    def context_summary(self, *, detailed=False):
        return self.controller.context_summary(detailed=detailed)

    def refresh_targets(self, preferred=_CURRENT_TARGET):
        self.controller.refresh_targets(preferred)

    def capture_view_state(self):
        return self.controller.capture_view_state()

    def restore_view_state(self, states):
        self.controller.restore_view_state(states)

    def changeEvent(self, event):
        super().changeEvent(event)
        self.controller.set_font(self.font())

    def dispose(self):
        self.controller.dispose()
        self.setSource('')


class WorkbookPlotCommandsController(QObject):
    """Retained command authority for an existing Workbook-owned SheetPage."""
    targetSelected = Signal(object)
    targetPinChanged = Signal(bool)
    targetShowRequested = Signal()
    repeatRequested = Signal(str, object)
    stateChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sheet_page = None
        self._pinned = False

    ready = Property(bool, lambda self: self.sheet_page is not None and self.sheet_page.plotEnabled, notify=stateChanged)
    heatmapReady = Property(bool, lambda self: self.sheet_page is not None and self.sheet_page.heatmapEnabled, notify=stateChanged)
    targets = Property('QVariantList', lambda self: self.sheet_page.targets if self.sheet_page else [], notify=stateChanged)
    targetId = Property('QVariant', lambda self: self.sheet_page.targetId if self.sheet_page else None, notify=stateChanged)
    targetPinned = Property(bool, lambda self: self._pinned, notify=stateChanged)
    hasTarget = Property(bool, lambda self: self.targetId is not None, notify=stateChanged)
    targetLabel = Property(str, lambda self: next((t['label'] for t in self.targets if t['value'] == self.targetId), 'No target graph'), notify=stateChanged)
    sourceText = Property(str, lambda self: f'{len(self.sheet_page.yColumns)} Y · {self.sheet_page.name}' if self.sheet_page else '', notify=stateChanged)
    sourceTooltip = Property(str, lambda self: self.sourceText + (f' · {self.sheet_page.model._sheet.mode} · {self.sheet_page.model._sheet.source_path or "Embedded values"}' if self.sheet_page else ''), notify=stateChanged)
    boundSheet = Property(QObject, lambda self: self.sheet_page, notify=stateChanged)

    @Slot(result=bool)
    def openTargets(self):
        return self.sheet_page is not None and self.sheet_page._allowed()

    def bind(self, page):
        if self.sheet_page is not page:
            if self.sheet_page is not None:
                try:
                    self.sheet_page.stateChanged.disconnect(self.stateChanged)
                except (RuntimeError, TypeError):
                    pass
            self.sheet_page = page
            if page is not None:
                page.stateChanged.connect(self.stateChanged)
        self.stateChanged.emit()

    def set_target_pinned(self, pinned):
        self._pinned = bool(pinned)
        self.stateChanged.emit()

    @Slot(str)
    def plot(self, style):
        if self.sheet_page is not None:
            self.sheet_page.request_plot(style)

    @Slot()
    def append(self):
        if self.sheet_page is not None and self.targetId is not None:
            self.sheet_page.request_plot('line', self.targetId)

    @Slot(str)
    def appendTo(self, target):
        if self.sheet_page is not None:
            self.sheet_page.appendTo(target)

    @Slot(str)
    def chooseTarget(self, target):
        if self.sheet_page is not None and self.sheet_page._allowed() and target in self.sheet_page.workspace.comparisons:
            self.sheet_page.refresh_targets(target)
            self.targetSelected.emit(target)
            self.stateChanged.emit()

    @Slot(bool)
    def pinTarget(self, pinned):
        if self.sheet_page is not None and self.sheet_page._allowed() and (self.hasTarget or not pinned):
            self._pinned = pinned
            self.targetPinChanged.emit(pinned)
        self.stateChanged.emit()

    @Slot()
    def showTarget(self):
        if self.sheet_page is not None and self.sheet_page._allowed() and self.hasTarget:
            self.targetShowRequested.emit()

    @Slot()
    def repeat(self):
        if self.sheet_page is not None and self.sheet_page._allowed() and self.sheet_page.finish_editor() and self.ready and self.hasTarget:
            self.repeatRequested.emit(self.sheet_page.sheet_id, self.targetId)

    def dispose(self):
        self.bind(None)


class WorkbookPlotCommands(QuickPanel):
    """Visible command row, composing the retained QObject command controller."""
    targetSelected = Signal(object)
    targetPinChanged = Signal(bool)
    targetShowRequested = Signal()
    repeatRequested = Signal(str, object)
    stateChanged = Signal()

    def __init__(self, parent=None):
        self.controller = WorkbookPlotCommandsController()
        super().__init__('WorkbookPlotCommands.qml', self.controller, parent, bridge_property='bridge')
        self.controller.setParent(self)
        for name in ('targetSelected', 'targetPinChanged', 'targetShowRequested', 'repeatRequested', 'stateChanged'):
            getattr(self.controller, name).connect(getattr(self, name))

    sheet_page = property(lambda self: self.controller.sheet_page)
    ready = Property(bool, lambda self: self.controller.ready, notify=stateChanged)
    heatmapReady = Property(bool, lambda self: self.controller.heatmapReady, notify=stateChanged)
    targets = Property('QVariantList', lambda self: self.controller.targets, notify=stateChanged)
    targetId = Property('QVariant', lambda self: self.controller.targetId, notify=stateChanged)
    targetPinned = Property(bool, lambda self: self.controller.targetPinned, notify=stateChanged)
    hasTarget = Property(bool, lambda self: self.controller.hasTarget, notify=stateChanged)
    targetLabel = Property(str, lambda self: self.controller.targetLabel, notify=stateChanged)
    sourceText = Property(str, lambda self: self.controller.sourceText, notify=stateChanged)
    sourceTooltip = Property(str, lambda self: self.controller.sourceTooltip, notify=stateChanged)
    boundSheet = Property(QObject, lambda self: self.controller.boundSheet, notify=stateChanged)

    @Slot(result=bool)
    def openTargets(self):
        return self.controller.openTargets()

    def bind(self, page):
        self.controller.bind(page)

    def set_target_pinned(self, pinned):
        self.controller.set_target_pinned(pinned)

    @Slot(str)
    def plot(self, style):
        self.controller.plot(style)

    @Slot()
    def append(self):
        self.controller.append()

    @Slot(str)
    def appendTo(self, target):
        self.controller.appendTo(target)

    @Slot(str)
    def chooseTarget(self, target):
        self.controller.chooseTarget(target)

    @Slot(bool)
    def pinTarget(self, pinned):
        self.controller.pinTarget(pinned)

    @Slot()
    def showTarget(self):
        self.controller.showTarget()

    @Slot()
    def repeat(self):
        self.controller.repeat()

    def dispose(self):
        self.controller.dispose()
        self.setSource('')
