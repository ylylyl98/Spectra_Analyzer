"""Typed import review state and bounded models for the production Quick view."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QAbstractListModel, QAbstractTableModel, QModelIndex, QObject, Property, Qt, Signal, Slot
from PySide6.QtGui import QFont, QGuiApplication


def validate_import_mapping(table, mapping, *, require_y=False):
    x, ys = mapping
    if (type(x) is not int or not isinstance(ys, list)
            or any(type(c) is not int or not 0 <= c < table.data.shape[1] for c in [x, *ys])):
        raise ValueError('Column index is outside the table.')
    if len(set(ys)) != len(ys) or x in ys:
        raise ValueError('Choose distinct X and Y columns.')
    if require_y and not ys:
        raise ValueError('Choose at least one Y column for every file to compare.')


class _PreviewModel(QAbstractTableModel):
    """Borrow only the first 100 rows; TableView requests visible cells lazily."""
    def __init__(self, table, parent=None):
        super().__init__(parent)
        self.table = table
        self.values = table.data[:100]

    def roleNames(self):
        return {int(Qt.DisplayRole): b'display'}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.values.shape[0]

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.values.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == Qt.DisplayRole:
            return format(float(self.values[index.row(), index.column()]), '.12g')
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.table.column_names[section] if orientation == Qt.Horizontal else str(section + 1)


class _ImportListModel(QAbstractListModel):
    def __init__(self, role_names, parent):
        super().__init__(parent)
        self.names = role_names
        self.rows = []

    def roleNames(self):
        return {int(Qt.UserRole) + i + 1: name.encode() for i, name in enumerate(self.names)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        position = int(role) - int(Qt.UserRole) - 1
        if index.isValid() and 0 <= index.row() < len(self.rows) and 0 <= position < len(self.names):
            return self.rows[index.row()].get(self.names[position])

    def reset(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def update(self, row, values):
        self.rows[row] = values
        self.dataChanged.emit(self.index(row), self.index(row), list(self.roleNames()))


class XYImportBridge(QObject):
    changed = Signal()
    previewChanged = Signal()
    commitRequested = Signal(bool)
    applicationFontChanged = Signal()

    def __init__(self, tables, workbooks, *, mappings=None, reused=None, comparisons=None,
                 comparison_id=None, entries=None):
        super().__init__()
        QGuiApplication.instance().fontChanged.connect(self.applicationFontChanged)
        # Metadata copies isolate type choices; numeric arrays/sessions stay borrowed.
        self.entries = None if entries is None else [replace(entry) for entry in entries]
        self.tables = list(tables)
        self.mappings = [(x, list(ys)) for x, ys in (mappings if mappings is not None else
            [(0, list(range(1, table.data.shape[1]))) for table in tables])]
        self.reused = list(reused) if reused is not None else [False] * len(tables)
        self.workbooks = workbooks
        self.comparisons = comparisons if comparisons is not None else {}
        self._fixed_comparison = comparison_id
        self._detected_types = [entry.kind for entry in self.entries] if self.entries is not None else []
        self._type_mappings = {}
        self._source = 0 if tables else -1
        self._destination = self._comparison = 0
        self._workbook_name = 'Workbook ' + str(len(workbooks) + 1)
        self._include_existing = False
        self._feedback = 'Named columns match by unique exact name; headerless files require the same column count and order.'
        self._validation = ''
        self._can_import = self._can_compare = False
        self._destinations = [('One Workbook per file', 'per_file'), ('One new Workbook', None)] + [
            (book.name, book.workbook_id) for book in workbooks.values()]
        self._comparisons = ([('New comparison', None)] + [(item.name, item.comparison_id)
            for item in self.comparisons.values()] if comparison_id is None else
            [(self.comparisons[comparison_id].name if comparison_id in self.comparisons else
              'Comparison no longer available', comparison_id)])
        self._sources = _ImportListModel(('caption', 'summary', 'sourceTooltip'), self)
        self._columns = _ImportListModel(('columnIndex', 'caption', 'selectedY', 'allowedY'), self)
        self._preview = None
        self._sources.reset([self._source_row(i) for i in range(len(self.tables))])
        self._refresh_source()

    @Property(QFont, notify=applicationFontChanged)
    def applicationFont(self):
        return QGuiApplication.font()

    @Property(QObject, constant=True)
    def sources(self):
        return self._sources

    @Property(QObject, constant=True)
    def columns(self):
        return self._columns

    @Property(QObject, notify=previewChanged)
    def preview(self):
        return self._preview

    @Property(int, notify=changed)
    def sourceIndex(self):
        return self._source

    @Property('QStringList', constant=True)
    def destinations(self):
        return [name for name, _ in self._destinations]

    @Property(int, notify=changed)
    def destinationIndex(self):
        return self._destination

    @property
    def destination(self):
        return self._destinations[self._destination][1]

    @Property(str, notify=changed)
    def workbookName(self):
        return self._workbook_name

    @Property('QStringList', constant=True)
    def comparisonNames(self):
        return [name for name, _ in self._comparisons]

    @Property(int, notify=changed)
    def comparisonIndex(self):
        return self._comparison

    @property
    def comparison_id(self):
        return self._comparisons[self._comparison][1]

    @Property(bool, constant=True)
    def fixedComparison(self):
        return self._fixed_comparison is not None

    @Property(bool, notify=changed)
    def newWorkbook(self):
        return self.destination is None

    @Property(bool, notify=changed)
    def includeExisting(self):
        return self._include_existing

    @Property(bool, constant=True)
    def hasTypes(self):
        return self.entries is not None

    @Property(str, notify=changed)
    def kind(self):
        return self.entries[self._source].kind if self.entries is not None and self._source >= 0 else 'xy'

    @Property(bool, notify=changed)
    def typeEditable(self):
        return self.entries is not None and self._source >= 0 and self.entries[self._source].sheet_id is None

    @Property('QVariantList', notify=changed)
    def typeChoices(self):
        entry = self.entries[self._source] if self.entries is not None and self._source >= 0 else None
        return [{'label': label, 'kind': kind, 'available': entry is None or kind in entry.tables,
                 'error': entry.option_errors.get(kind, '') if entry is not None else ''}
            for kind, label in (('xy', 'XY table'), ('dat', 'Gate matrix'))]

    @Property(str, notify=changed)
    def typeReason(self):
        if self.entries is None or self._source < 0:
            return ''
        entry = self.entries[self._source]
        detected = 'Gate matrix' if self._detected_types[self._source] == 'dat' else 'XY table'
        prefix = 'Automatic choice: ' + detected + '. ' if entry.sheet_id is None else 'Retained Sheet type is fixed. '
        if entry.kind != self._detected_types[self._source]:
            prefix = 'Manual type correction: ' + ('Gate matrix' if entry.kind == 'dat' else 'XY table') + '. Initial automatic choice: ' + detected + '. '
        return prefix + entry.reason + (' X is the spectral axis. Selected Gates set comparison curves; every Gate is imported.'
            if entry.kind == 'dat' else ' Choose X and Y independently; every column is imported.')

    @Property(str, notify=changed)
    def previewInfo(self):
        if self._source < 0:
            return ''
        table = self.tables[self._source]
        return f'{table.source_path.name} · {table.data.shape[0]} rows × {table.data.shape[1]} columns · first 100 rows' + (
            ' · Retained Sheet values; stays in its original workbook.' if self.reused[self._source] else '')

    @Property('QStringList', notify=changed)
    def columnNames(self):
        return [f'{i + 1}: {name}' for i, name in enumerate(self.tables[self._source].column_names)] if self._source >= 0 else []

    @Property(int, notify=changed)
    def xColumn(self):
        return self.mappings[self._source][0] if self._source >= 0 else -1

    @Property(bool, notify=changed)
    def xEditable(self):
        return self._source >= 0 and self.kind != 'dat'

    @Property(str, notify=changed)
    def feedback(self):
        return self._feedback

    @Property(str, notify=changed)
    def validation(self):
        return self._validation

    @Property(bool, notify=changed)
    def canImport(self):
        return self._can_import

    @Property(bool, notify=changed)
    def canCompare(self):
        return self._can_compare

    @Property(bool, notify=changed)
    def canAccept(self):
        return self._can_compare if self.fixedComparison else self._can_import

    @Property(str, notify=changed)
    def compareText(self):
        return 'Import and compare' if self.comparison_id is None else 'Add to comparison'

    def _source_row(self, index):
        table = self.tables[index]
        x, ys = self.mappings[index]
        def name(column):
            return table.column_names[column] if type(column) is int and 0 <= column < len(table.column_names) else '?'
        prefix = ('Gate matrix · ' if self.entries[index].kind == 'dat' else 'XY table · ') if self.entries is not None else ''
        return {'caption': prefix + table.source_path.name,
                'summary': 'X: ' + name(x) + ' · Y: ' + ', '.join(name(y) for y in ys),
                'sourceTooltip': str(table.source_path) + ('\nReuses the retained Sheet in its original workbook.' if self.reused[index] else '')}

    def _refresh_columns(self):
        x, ys = self.mappings[self._source] if self._source >= 0 else (-1, [])
        rows = [{'columnIndex': i, 'caption': name, 'selectedY': i in ys, 'allowedY': i != x}
            for i, name in enumerate(self.tables[self._source].column_names)] if self._source >= 0 else []
        if len(rows) != len(self._columns.rows):
            self._columns.reset(rows)
        else:
            for index, values in enumerate(rows):
                if values != self._columns.rows[index]:
                    self._columns.update(index, values)

    def _refresh_source(self):
        old = self._preview
        self._preview = _PreviewModel(self.tables[self._source], self) if self._source >= 0 else None
        self.previewChanged.emit()
        if old is not None:
            old.deleteLater()
        self._refresh_columns()
        self._validate()

    def _mapping_changed(self, index):
        self._sources.update(index, self._source_row(index))
        self._validate()

    def _validate(self):
        error, no_y = '', []
        if not self.tables or len(self.tables) != len(self.mappings):
            error = 'Select at least one valid source file.'
        elif self.destination is None and not self._workbook_name.strip():
            error = 'Enter a name for the new workbook.'
        elif self.destination not in ('per_file', None) and self.destination not in self.workbooks:
            error = 'The destination workbook is no longer available.'
        for index, (table, mapping) in enumerate(zip(self.tables, self.mappings)):
            try:
                validate_import_mapping(table, mapping)
                if table.data.shape[0] == 0 or len(table.column_names) != table.data.shape[1]:
                    raise ValueError('Source table is no longer available.')
                if self.entries is not None:
                    entry = self.entries[index]
                    if entry.kind not in entry.tables or entry.table is not table:
                        raise ValueError('Source interpretation is no longer available.')
                    if entry.sheet_id is not None and entry.kind != self._detected_types[index]:
                        raise ValueError('Retained Sheet type is fixed.')
                    if entry.kind == 'dat' and mapping[0] != 0:
                        raise ValueError('Gate matrices use the spectral axis as X.')
            except (ValueError, IndexError, KeyError) as exc:
                error = f'{table.source_path.name}: {exc}'
                break
            if not mapping[1]:
                no_y.append(table.source_path.name)
        comparison_error = ('The destination comparison is no longer available.'
            if self.comparison_id is not None and self.comparison_id not in self.comparisons else '')
        self._can_import = not bool(error)
        self._can_compare = not bool(error or no_y or comparison_error)
        self._validation = error or comparison_error or ('Select Y columns to compare: ' + ', '.join(no_y) if no_y else '')
        self.changed.emit()

    def validate(self, compare):
        self._validate()
        return self._can_compare if compare else self._can_import and not self.fixedComparison

    def replaceMappings(self, mappings):
        self.mappings = [(x, list(ys)) for x, ys in mappings]
        self._sources.reset([self._source_row(i) for i in range(min(len(self.tables), len(self.mappings)))])
        if self._source < len(self.mappings):
            self._refresh_columns()
        self._validate()

    @Slot(int)
    def selectSource(self, index):
        if 0 <= index < len(self.tables) and index != self._source:
            self._source = index
            self._refresh_source()

    @Slot(str)
    def setKind(self, kind):
        if not self.typeEditable:
            return
        entry = self.entries[self._source]
        if kind not in entry.tables:
            self.setFeedback(entry.option_errors.get(kind, 'This interpretation is unavailable.'))
            return
        if kind == entry.kind:
            return
        x, ys = self.mappings[self._source]
        self._type_mappings[self._source, entry.kind] = (x, list(ys))
        entry.kind = kind
        self.tables[self._source] = entry.table
        x, ys = self._type_mappings.get((self._source, kind), (0, list(range(1, entry.table.data.shape[1]))))
        self.mappings[self._source] = (x, list(ys))
        self._sources.update(self._source, self._source_row(self._source))
        self._refresh_source()

    @Slot(int)
    def setX(self, column):
        if not self.xEditable or not 0 <= column < self.tables[self._source].data.shape[1]:
            return
        self.mappings[self._source] = (column, [y for y in self.mappings[self._source][1] if y != column])
        self._refresh_columns()
        self._mapping_changed(self._source)

    @Slot(int, bool)
    def setY(self, column, checked):
        if self._source < 0 or not 0 <= column < self.tables[self._source].data.shape[1]:
            return
        x, ys = self.mappings[self._source]
        if column == x:
            return
        selected = set(ys)
        selected.add(column) if checked else selected.discard(column)
        self.mappings[self._source] = (x, sorted(selected))
        self._refresh_columns()
        self._mapping_changed(self._source)

    @Slot(int)
    def setDestinationIndex(self, index):
        if 0 <= index < len(self._destinations):
            self._destination = index
            self._validate()

    @Slot(str)
    def setWorkbookName(self, text):
        self._workbook_name = text
        self._validate()

    @Slot(int)
    def setComparisonIndex(self, index):
        if not self.fixedComparison and 0 <= index < len(self._comparisons):
            self._comparison = index
            self._validate()

    @Slot(bool)
    def setIncludeExisting(self, checked):
        self._include_existing = checked
        self.changed.emit()

    @Slot(str)
    def setFeedback(self, text):
        self._feedback = str(text)
        self.changed.emit()

    @Slot()
    def applyMatching(self):
        self._validate()
        if self._source < 0 or not self._can_import:
            return
        source = self.tables[self._source]
        x, ys = self.mappings[self._source]
        applied, skipped = 0, []
        for index, target in enumerate(self.tables):
            if index == self._source:
                continue
            columns = [x, *ys]
            if self.entries is not None and self.entries[index].kind != self.kind:
                skipped.append(target.source_path.name)
                continue
            if (source.has_header and target.has_header
                    and len(set(source.column_names)) == len(source.column_names)
                    and len(set(target.column_names)) == len(target.column_names)
                    and all(source.column_names[c] in target.column_names for c in columns)):
                remapped = [target.column_names.index(source.column_names[c]) for c in columns]
                if self.kind == 'dat' and remapped[0] != 0:
                    skipped.append(target.source_path.name)
                    continue
                self.mappings[index] = (remapped[0], remapped[1:])
            elif (not source.has_header and not target.has_header
                    and source.column_names == target.column_names and source.data.shape[1] == target.data.shape[1]):
                self.mappings[index] = (x, list(ys))
            else:
                skipped.append(target.source_path.name)
                continue
            applied += 1
            self._sources.update(index, self._source_row(index))
        self._feedback = f'Applied to {applied} other file(s); skipped {len(skipped)}.' + (
            ' Ambiguous or missing names / incompatible headerless columns: ' + ', '.join(skipped) if skipped else '')
        self._validate()

    @Slot(bool)
    def commit(self, compare):
        self.commitRequested.emit(compare)
