"""Staged existing-Sheet comparison choices rendered in a real Qt Quick window."""
from __future__ import annotations

from PySide6.QtCore import (QAbstractListModel, QAbstractTableModel, QModelIndex,
                            QObject, Property, Qt, Signal, Slot)

from PySide6.QtGui import QFont, QGuiApplication

from .quick_dialog import QuickDialog
from .workbook_state import (sheet_columns, sheet_column_values, sheet_shape,
                             sheet_numeric_columns, validate_mapping, select_curve_additions)
from .workbook_page import _cell_text


def validate_sheet_comparison(workspace, mappings, *, names=None):
    """Validate the whole batch without changing Sheet defaults or curves."""
    if not mappings:
        raise ValueError('Check at least one Sheet to compare.')
    owners = {sid: book.name for book in workspace.workbooks.values() for sid in book.sheet_ids}
    for sheet_id, (x, ys, mode) in mappings.items():
        sheet = workspace.sheets.get(sheet_id)
        label = (names or {}).get(sheet_id) or (f'{owners.get(sheet_id, "Workbook")} / {sheet.name}'
                                              if sheet else f'Sheet {sheet_id}')
        try:
            if sheet is None or sheet_id not in owners:
                raise ValueError('The source Sheet no longer exists in a workbook.')
            validate_mapping(workspace, sheet, x, ys, mode)
            if not ys:
                raise ValueError('Choose at least one Y column.')
            if x in ys:
                raise ValueError('Choose distinct X and Y columns.')
            if sheet.kind == 'dat' and sheet.role != 'fit_parameters' and x != 0:
                raise ValueError('DAT X must use the energy axis.')
            if mode != sheet.mode:
                raise ValueError('Select the Sheet for the requested values.')
            if sheet_shape(workspace, sheet)[0] == 0:
                raise ValueError('No current results are available.')
        except (ValueError, KeyError) as exc:
            raise ValueError(f'{label}: {exc}') from exc


class _SheetPreviewModel(QAbstractTableModel):
    """Bounded rows and borrowed column views, never a materialized DAT matrix."""
    def __init__(self, workspace, sheet, mode, parent):
        super().__init__(parent)
        self.columns = sheet_columns(workspace, sheet, mode)
        self.rows = min(100, sheet_shape(workspace, sheet, mode)[0])
        self.values = [sheet_column_values(workspace, sheet, index, mode)[:self.rows]
                       for index in range(len(self.columns))]

    def roleNames(self):
        return {int(Qt.DisplayRole): b'display'}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.rows

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == Qt.DisplayRole:
            return _cell_text(self.values[index.column()][index.row()])
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.columns[section] if orientation == Qt.Horizontal else str(section + 1)


class _ChoiceRows(QAbstractListModel):
    """Small metadata rows; source values stay in the authoritative workspace."""
    def __init__(self, fields, parent=None):
        super().__init__(parent)
        self._roles = {int(Qt.UserRole) + i + 1: key.encode() for i, key in enumerate(fields)}
        self.rows = []

    def roleNames(self):
        return self._roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        key = self._roles.get(role)
        if index.isValid() and key is not None and 0 <= index.row() < len(self.rows):
            return self.rows[index.row()].get(key.decode())

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def refresh(self):
        if self.rows:
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1), list(self._roles))


class SheetComparisonBridge(QObject):
    applicationFontChanged = Signal()
    changed = Signal()
    previewChanged = Signal()

    def __init__(self, workspace, *, selected_sheet_ids=(), comparison_id=None):
        super().__init__()
        QGuiApplication.instance().fontChanged.connect(self.applicationFontChanged)
        self.workspace = workspace
        self._fixed_target = comparison_id
        self._target = comparison_id
        self._include_existing = False
        self._choices = {}
        self._names = {}
        self._included = set(selected_sheet_ids)
        self._current_id = ''
        self._preview = None
        self._can_accept = False
        self._feedback = self._summary = self._provenance = self._preview_info = ''
        self._x_options = []
        self._x_index = -1
        self._x_editable = self._can_match = False
        self._value_label = ''
        self._source_model = _ChoiceRows(('stableId', 'parentId', 'rowKind', 'checkState',
            'indent', 'label', 'summary', 'tooltip'), self)
        self._column_model = _ChoiceRows(('column', 'label', 'included', 'available'), self)
        rows = []
        for book in workspace.workbooks.values():
            rows.append(dict(stableId=book.workbook_id, parentId='', rowKind='workbook',
                checkState=0, indent=0, label=book.name, summary='', tooltip=book.name))
            for sid in book.sheet_ids:
                sheet = workspace.sheets.get(sid)
                if sheet is None:
                    continue
                self._choices[sid] = (sheet.x_column, list(sheet.y_columns), sheet.mode)
                self._names[sid] = f'{book.name} / {sheet.name}'
                rows.append(dict(stableId=sid, parentId=book.workbook_id, rowKind='sheet',
                    checkState=0, indent=1, label=sheet.name, summary='',
                    tooltip=f'{self._names[sid]}\n{sheet.snapshot_note or sheet.source_path or "Embedded values"}'))
        self._source_model.replace(rows)
        self._destinations = ([dict(label='New comparison', value='')] +
            [dict(label=c.name, value=c.comparison_id) for c in workspace.comparisons.values()]
            if comparison_id is None else [dict(label=workspace.comparisons[comparison_id].name
                if comparison_id in workspace.comparisons else 'Comparison no longer available', value=comparison_id)])
        first = next((sid for sid in self._choices if sid in self._included), next(iter(self._choices), ''))
        self._refresh_sources()
        self.selectSource(first)
        self.validate()

    applicationFont = Property(QFont, lambda self: QGuiApplication.font(), notify=applicationFontChanged)
    sourceModel = Property(QObject, lambda self: self._source_model, constant=True)
    columnModel = Property(QObject, lambda self: self._column_model, constant=True)
    previewModel = Property(QObject, lambda self: self._preview, notify=previewChanged)
    destinations = Property('QVariantList', lambda self: self._destinations, constant=True)
    fixedDestination = Property(bool, lambda self: self._fixed_target is not None, constant=True)
    destinationIndex = Property(int, lambda self: next((i for i, row in enumerate(self._destinations)
        if row['value'] == (self._target or '')), 0), notify=changed)
    destinationLabel = Property(str, lambda self: self._destinations[self.destinationIndex]['label'], notify=changed)
    currentId = Property(str, lambda self: self._current_id, notify=changed)
    currentRow = Property(int, lambda self: next((i for i, row in enumerate(self._source_model.rows)
        if row['stableId'] == self._current_id), -1), notify=changed)
    xOptions = Property('QVariantList', lambda self: self._x_options, notify=changed)
    xIndex = Property(int, lambda self: self._x_index, notify=changed)
    xEditable = Property(bool, lambda self: self._x_editable, notify=changed)
    valueLabel = Property(str, lambda self: self._value_label, notify=changed)
    canMatchGates = Property(bool, lambda self: self._can_match, notify=changed)
    provenance = Property(str, lambda self: self._provenance, notify=changed)
    previewInfo = Property(str, lambda self: self._preview_info, notify=changed)
    summary = Property(str, lambda self: self._summary, notify=changed)
    feedback = Property(str, lambda self: self._feedback, notify=changed)
    canAccept = Property(bool, lambda self: self._can_accept, notify=changed)
    includeExisting = Property(bool, lambda self: self._include_existing, notify=changed)
    actionText = Property(str, lambda self: 'Create comparison' if self._target is None else 'Add data', notify=changed)

    def selected_mappings(self):
        return {sid: (x, list(ys), mode) for sid, (x, ys, mode) in self._choices.items() if sid in self._included}

    def _refresh_sources(self):
        for row in self._source_model.rows:
            sid = row['stableId']
            if row['rowKind'] == 'sheet':
                row['checkState'] = 2 if sid in self._included else 0
                x, ys, mode = self._choices[sid]
                try:
                    columns = sheet_columns(self.workspace, sid, mode)
                    row['summary'] = f'X: {columns[x]} · Y: {", ".join(columns[y] for y in ys) or "none"} · {mode}'
                except (ValueError, KeyError, IndexError, TypeError):
                    row['summary'] = 'Review mapping'
            else:
                children = [child['stableId'] for child in self._source_model.rows if child['parentId'] == sid]
                count = sum(child in self._included for child in children)
                row['checkState'] = 2 if children and count == len(children) else 1 if count else 0
        self._source_model.refresh()

    @Slot(int, result=str)
    def sourceIdAt(self, index):
        rows = self._source_model.rows
        return rows[index]['stableId'] if 0 <= index < len(rows) else ''

    @Slot(str)
    def toggleSource(self, source_id):
        row = next((row for row in self._source_model.rows if row['stableId'] == source_id), None)
        if row is None:
            return
        children = ([row['stableId']] if row['rowKind'] == 'sheet' else
                    [child['stableId'] for child in self._source_model.rows if child['parentId'] == source_id])
        if row['checkState'] == 2:
            self._included.difference_update(children)
        else:
            self._included.update(children)
        self._refresh_sources()
        self.validate()

    @Slot()
    def selectOriginals(self):
        self._included = {sid for sid in self._choices if (sheet := self.workspace.sheets.get(sid)) is not None
                          and sheet.role == 'original'}
        self._refresh_sources()
        self.validate()

    @Slot(str)
    def selectSource(self, source_id):
        if source_id != self._current_id or self._preview is None:
            self._current_id = source_id
            self._load_editor()
            self.changed.emit()

    def _load_editor(self):
        old_model, self._preview = self._preview, None
        self._x_options = []
        self._x_index = -1
        self._x_editable = self._can_match = False
        self._value_label = self._preview_info = ''
        self._provenance = self._names.get(self._current_id, 'Select a Sheet to review its mapping.')
        self._column_model.replace([])
        sheet = self.workspace.sheets.get(self._current_id)
        try:
            if sheet is not None and self._current_id in self._choices:
                x, ys, mode = self._choices[self._current_id]
                columns = sheet_columns(self.workspace, sheet, mode)
                numeric = sheet_numeric_columns(self.workspace, sheet, mode)
                self._x_options = [dict(label=f'{index + 1}: {columns[index]}', column=index) for index in numeric]
                self._x_index = next((i for i, option in enumerate(self._x_options) if option['column'] == x), -1)
                self._x_editable = sheet.kind != 'dat' or sheet.role == 'fit_parameters'
                self._can_match = sheet.kind == 'dat' and sheet.role in ('original', 'processed')
                self._value_label = sheet.role.replace('_', ' ').title()
                self._column_model.replace([dict(column=i, label=f'{i + 1}: {label}', included=i in ys,
                    available=i in numeric and i != x) for i, label in enumerate(columns)])
                self._preview = _SheetPreviewModel(self.workspace, sheet, mode, self)
                count = sheet_shape(self.workspace, sheet, mode)[0]
                self._preview_info = f'Preview: first {self._preview.rows} of {count} rows · all rows will be plotted.'
                if sheet.kind == 'dat' and sheet.role != 'fit_parameters':
                    self._preview_info += ' DAT X is fixed to the energy axis.'
        except (ValueError, KeyError, IndexError) as exc:
            self._preview_info = f'Preview unavailable: {exc}'
        self.previewChanged.emit()
        if old_model is not None:
            old_model.deleteLater()

    def _mapping_changed(self):
        self._refresh_sources()
        # A mapping edit does not replace the borrowed-value preview.
        if self._current_id in self._choices:
            x, ys, _ = self._choices[self._current_id]
            self._x_index = next((i for i, row in enumerate(self._x_options) if row['column'] == x), -1)
            sheet = self.workspace.sheets.get(self._current_id)
            numeric = sheet_numeric_columns(self.workspace, sheet) if sheet is not None else []
            for row in self._column_model.rows:
                row['included'] = row['column'] in ys
                row['available'] = row['column'] in numeric and row['column'] != x
            self._column_model.refresh()
        self.validate()

    @Slot(int)
    def setX(self, column):
        sheet = self.workspace.sheets.get(self._current_id)
        if sheet is None or not self._x_editable or column not in sheet_numeric_columns(self.workspace, sheet):
            return
        _, ys, mode = self._choices[self._current_id]
        self._choices[self._current_id] = (column, sorted(y for y in ys if y != column), mode)
        self._mapping_changed()

    @Slot(int, bool)
    def setY(self, column, included):
        sheet = self.workspace.sheets.get(self._current_id)
        if sheet is None or self._current_id not in self._choices:
            return
        x, ys, mode = self._choices[self._current_id]
        if column == x or column not in sheet_numeric_columns(self.workspace, sheet):
            return
        selected = set(ys)
        (selected.add if included else selected.discard)(column)
        self._choices[self._current_id] = (x, sorted(selected), mode)
        self._mapping_changed()

    @Slot(int)
    def setDestination(self, index):
        if self._fixed_target is None and 0 <= index < len(self._destinations):
            self._target = self._destinations[index]['value'] or None
            self.validate()

    @Slot(bool)
    def setIncludeExisting(self, included):
        self._include_existing = included
        self.validate()

    @Slot()
    def matchSelectedGates(self):
        """Stage exact physical-value matches atomically across the checked batch."""
        source_id = self._current_id
        try:
            source = self.workspace.sheets.get(source_id)
            if source is None or source.kind != 'dat' or source.role not in ('original', 'processed'):
                raise ValueError('Select a DAT Original or Processed Sheet as the Gate source.')
            choice = self._choices[source_id]
            validate_sheet_comparison(self.workspace, {source_id: choice}, names=self._names)
            session = self.workspace.datasets[source.dataset_id]
            cube = session.base_cube if choice[2] == 'raw' else session.current_cube
            values = [float(cube.gate[column - 1]) for column in choice[1]]
            staged = self.selected_mappings()
            matched = 0
            for sid, (x, ys, mode) in list(staged.items()):
                sheet = self.workspace.sheets.get(sid)
                if sheet is None:
                    raise ValueError(f'{self._names[sid]}: The source Sheet no longer exists.')
                if sheet.kind != 'dat' or sheet.role not in ('original', 'processed'):
                    continue
                owner = self.workspace.datasets[sheet.dataset_id]
                gates = (owner.base_cube if mode == 'raw' else owner.current_cube).gate
                columns = []
                for value in values:
                    indices = [i + 1 for i, gate in enumerate(gates) if float(gate) == value]
                    if len(indices) != 1:
                        issue = 'is missing' if not indices else 'has ambiguous matches for'
                        raise ValueError(f'{self._names[sid]} {issue} Gate {value:.17g} from {self._names[source_id]}. No choices changed.')
                    columns.append(indices[0])
                staged[sid] = (x, columns, mode)
                matched += 1
            validate_sheet_comparison(self.workspace, staged, names=self._names)
        except (ValueError, KeyError, IndexError, AttributeError) as exc:
            self._feedback = str(exc)
            self.changed.emit()
            return
        self._choices.update(staged)
        self._refresh_sources()
        self._mapping_changed()
        self._feedback = f'Matched {len(values)} physical Gate value(s) in {matched} checked DAT Sheet(s).'
        self.changed.emit()

    @Slot(result=bool)
    def validate(self):
        mappings = self.selected_mappings()
        self._summary = f'{len(mappings)} Sheet(s) included · {sum(len(ys) for _, ys, _ in mappings.values())} curve(s)'
        try:
            validate_sheet_comparison(self.workspace, mappings, names=self._names)
            if self._target is not None and self._target not in self.workspace.comparisons:
                raise ValueError('The destination comparison no longer exists.')
            candidates = [(sid, x, y, mode) for sid, (x, ys, mode) in mappings.items() for y in ys]
            additions, existing = select_curve_additions(self.workspace, self._target, candidates)
            total = len(candidates) if self._include_existing else len(additions)
            self._summary = f'{len(mappings)} Sheet(s) included · {len(additions)} new · {existing} already present · {total} to add'
            if not total:
                raise ValueError('All selected curves are already present. Select “Include existing curves (add again)” to add them again.')
        except (ValueError, KeyError) as exc:
            self._feedback, self._can_accept = str(exc), False
        else:
            self._feedback, self._can_accept = '', True
        self.changed.emit()
        return self._can_accept

    def shutdown(self):
        old_model, self._preview = self._preview, None
        self.previewChanged.emit()
        if old_model is not None:
            old_model.deleteLater()


class SheetComparisonDialog(QuickDialog):
    def __init__(self, workspace, parent=None, *, selected_sheet_ids=(), comparison_id=None):
        bridge = SheetComparisonBridge(workspace, selected_sheet_ids=selected_sheet_ids, comparison_id=comparison_id)
        super().__init__(bridge, 'SheetComparisonDialog.qml', parent,
            title='Add data to comparison' if comparison_id is not None else 'Compare selected data', size=(960, 680))

    @property
    def comparison_id(self):
        return self.bridge._target

    @property
    def include_existing(self):
        return self.bridge.includeExisting

    def selected_mappings(self):
        return self.bridge.selected_mappings()

    @Slot()
    def accept(self):
        # The workspace may have changed while this modal window was open.
        if self.bridge.validate():
            self._finish(self.Accepted)
