"""Owner-scoped, detached Results snapshots and local Analysis style drafts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import math

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Property, Qt, Signal, Slot


def _text(value):
    return '' if value is None else str(value)


class AnalysisResultsModel(QAbstractTableModel):
    """Virtual table projection; the canonical snapshot keeps its export order."""
    def __init__(self, columns, rows, parent=None):
        super().__init__(parent)
        self.columns, self.rows = columns, rows
        message = columns.index('Message')
        self.display_columns = (0, 1, message) + tuple(
            index for index in range(len(columns)) if index not in (0, 1, message))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.display_columns)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and 0 <= index.row() < len(self.rows) and 0 <= index.column() < len(self.display_columns):
            if role in (Qt.DisplayRole, Qt.ToolTipRole):
                return _text(self.rows[index.row()][self.display_columns[index.column()]])
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal and 0 <= section < len(self.display_columns):
                return self.columns[self.display_columns[section]]
            if orientation == Qt.Vertical and 0 <= section < len(self.rows):
                return str(section + 1)
        return None


class _AnalysisOwnedBridge(QObject):
    stateChanged = Signal()

    def __init__(self, shared):
        super().__init__()
        self.shared = shared
        self._owner = shared.ownerToken
        self._closed = False
        self._error = ''
        shared.stateChanged.connect(self.stateChanged)

    def allowed(self, owner=None, *, busy=False):
        return (not self._closed and (owner is None or owner == self._owner)
                and self.shared.allowed(self._owner, busy=busy))

    ownerToken = Property(str, lambda self: self._owner, constant=True)
    enabled = Property(bool, lambda self: self.allowed(), notify=stateChanged)
    canAccept = Property(bool, lambda self: self.allowed(), notify=stateChanged)
    errorText = Property(str, lambda self: self._error, notify=stateChanged)
    state = Property('QVariantMap', lambda self: self._state(), notify=stateChanged)

    def shutdown(self):
        self._closed = True
        self.stateChanged.emit()


class AnalysisResultsBridge(_AnalysisOwnedBridge):
    def __init__(self, shared):
        super().__init__(shared)
        from .analysis_document import analysis_parameter_rows
        window = shared.window
        self.snapshot_columns, self.snapshot_rows, self.snapshot_gates = deepcopy(
            analysis_parameter_rows(window._workbench_document()))
        self.snapshot_provenance = deepcopy(window._workbench_provenance())
        self.snapshot_graph_name = window.workspace.active.active_graph.name
        self._table = AnalysisResultsModel(self.snapshot_columns, self.snapshot_rows, self)
        self._selected_row = 0 if self.snapshot_rows else -1
        self._selected_column = 0 if self.snapshot_rows else -1

    tableModel = Property(QObject, lambda self: self._table, constant=True)

    def _state(self):
        selected = self._table.index(self._selected_row, self._selected_column)
        return dict(rowCount=len(self.snapshot_rows), columnCount=len(self.snapshot_columns),
                    selectedRow=self._selected_row, selectedColumn=self._selected_column,
                    selectedHeader=self._table.headerData(self._selected_column, Qt.Horizontal) or '',
                    selectedText=self._table.data(selected) or '')

    @Slot(int, int, str, result=bool)
    def selectCell(self, row, displayColumn, owner):
        if not self.allowed(owner) or not (0 <= row < len(self.snapshot_rows) and
                                           0 <= displayColumn < len(self.snapshot_columns)):
            return False
        self._selected_row, self._selected_column = row, displayColumn
        self.stateChanged.emit()
        return True

    @Slot(str, result=bool)
    def locate(self, owner):
        if not self.allowed(owner) or not 0 <= self._selected_row < len(self.snapshot_gates):
            return False
        self.shared.window._set_current_gate_from_index(self.snapshot_gates[self._selected_row], update_plot=True)
        return True

    @Slot(str, result=bool)
    def saveWorkbook(self, owner):
        if not self.allowed(owner):
            return False
        return self.shared.window._workbench_save_parameters(
            owner=self._owner, snapshot=(self.snapshot_columns, self.snapshot_rows, self.snapshot_gates),
            provenance=self.snapshot_provenance, graph_name=self.snapshot_graph_name,
            owner_guard=lambda: self.allowed(owner))

    @Slot(str, result=bool)
    def exportCsv(self, owner):
        if not self.allowed(owner):
            return False
        return self.shared.window._workbench_export_parameters(
            owner=self._owner, snapshot=(self.snapshot_columns, self.snapshot_rows, self.snapshot_gates),
            owner_guard=lambda: self.allowed(owner))


class AnalysisStyleBridge(_AnalysisOwnedBridge):
    focusRequested = Signal(str, str)
    LAYERS = ('detected-peak', 'detected-dip', 'fitted-peak', 'fitted-dip')
    MARKERS = ('o', 's', '^', 'v', 'D', '+', 'x', '*', '.')

    def __init__(self, shared):
        super().__init__(shared)
        self._original = dict(shared.window._workbench_document().overlay_styles)
        self._styles = {name: dict(color=style.color, marker=style.marker,
                                  size=str(style.size), filled=style.filled)
                        for name, style in self._original.items() if name in self.LAYERS}

    layerIds = Property('QStringList', lambda self: list(self.LAYERS), constant=True)

    def _state(self):
        return dict(styles=deepcopy(self._styles))

    @Slot(str, str, str, str, result=bool)
    def draftStyle(self, layer, field, text, owner):
        if not self.allowed(owner) or layer not in self.LAYERS or field not in ('color', 'marker', 'size', 'filled'):
            return False
        if field == 'filled' and text not in ('true', 'false'):
            return False
        self._styles[layer][field] = text == 'true' if field == 'filled' else text
        self._error = ''
        self.stateChanged.emit()
        return True

    @Slot(str, result=bool)
    def applyStyles(self, owner):
        if not self.allowed(owner):
            return False
        from matplotlib.colors import is_color_like
        updated = {}
        for layer in self.LAYERS:
            draft = self._styles[layer]
            for field in ('color', 'marker', 'size'):
                try:
                    value = draft[field]
                    if field == 'color' and not is_color_like(value):
                        raise ValueError('Enter a valid matplotlib color.')
                    if field == 'marker' and value not in self.MARKERS:
                        raise ValueError('Choose a supported marker.')
                    if field == 'size' and (not math.isfinite(float(value)) or not .1 <= float(value) <= 100.):
                        raise ValueError('Enter a finite size from 0.1 to 100.0.')
                except (ValueError, TypeError, OverflowError) as exc:
                    self._error = str(exc) if field != 'size' else 'Enter a finite size from 0.1 to 100.0.'
                    self.stateChanged.emit()
                    self.focusRequested.emit(layer, field)
                    return False
            updated[layer] = replace(self._original[layer], color=draft['color'], marker=draft['marker'],
                                     size=float(draft['size']), filled=draft['filled'])
        if not self.allowed(owner):
            return False
        self.shared.window._workbench_document().overlay_styles = updated
        self._error = ''
        self.shared.window._workbench_changed()
        return True
