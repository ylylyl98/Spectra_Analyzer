"""Numeric import review, detached from the workspace until the user imports."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSplitter, QTableView, QVBoxLayout, QWidget)


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
    """Bounded preview; import always retains every original row."""
    def __init__(self, table, parent):
        super().__init__(parent)
        self.table = table

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else min(100, self.table.data.shape[0])

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.table.data.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == Qt.DisplayRole:
            return format(float(self.table.data[index.row(), index.column()]), '.12g')
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.table.column_names[section] if orientation == Qt.Horizontal else str(section + 1)


class XYImportDialog(QDialog):
    def __init__(self, tables, workbooks, parent=None, *, workbook_id=None, mappings=None, reused=None,
                 comparisons=None, comparison_id=None, entries=None):
        super().__init__(parent)
        self.setWindowTitle('Import data' if entries is not None else 'Import numeric XY files')
        self.resize(920, 620)
        self.tables = tables
        self.entries = entries
        self._detected_types = [entry.kind for entry in entries] if entries is not None else []
        self._type_mappings = {}
        self.mappings = [(x, list(ys)) for x, ys in (
            mappings if mappings is not None else [(0, list(range(1, table.data.shape[1]))) for table in tables])]
        self.reused = reused or [False] * len(tables)
        self.compare = False
        self._syncing = False
        layout = QVBoxLayout(self)
        introduction = QLabel('Optional import settings: review detected types, column roles and destinations. All rows and columns are retained. Defaults: first column X, all remaining columns Y.'
                             if entries is not None else 'Review each file’s X and Y columns. Defaults: first column X, all remaining columns Y. All rows and columns are retained; no axis detection is applied.')
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        destination = QHBoxLayout()
        self.destination_combo = QComboBox()
        self.destination_combo.setAccessibleName('Destination workbook')
        self.destination_combo.addItem('One Workbook per file', 'per_file')
        self.destination_combo.addItem('One new Workbook', None)
        for book in workbooks.values():
            self.destination_combo.addItem(book.name, book.workbook_id)
        self.name_edit = QLineEdit('Workbook ' + str(len(workbooks) + 1))
        self.name_edit.setAccessibleName('New workbook name')
        destination.addWidget(QLabel('Destination:'))
        destination.addWidget(self.destination_combo, 1)
        destination.addWidget(self.name_edit, 1)
        layout.addLayout(destination)
        comparison_row = QHBoxLayout()
        comparison_row.addWidget(QLabel('Comparison:'))
        self.comparison_combo = QComboBox(self)
        self.comparison_combo.setAccessibleName('Destination comparison')
        self.comparison_combo.setMinimumContentsLength(12)
        self.comparison_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        if comparison_id is None:
            self.comparison_combo.addItem('New comparison', None)
            for comparison in (comparisons or {}).values():
                self.comparison_combo.addItem(comparison.name, comparison.comparison_id)
            comparison_row.addWidget(self.comparison_combo, 1)
        else:
            target = (comparisons or {}).get(comparison_id)
            self.comparison_combo.addItem(target.name if target else 'Comparison no longer available', comparison_id)
            self.comparison_combo.setEnabled(False)
            self.comparison_combo.hide()
            target_label = QLabel(self.comparison_combo.currentText())
            target_label.setTextFormat(Qt.PlainText)
            target_label.setWordWrap(True)
            comparison_row.addWidget(target_label, 1)
        layout.addLayout(comparison_row)
        self.destination_combo.currentIndexChanged.connect(self._destination_changed)
        self.name_edit.textChanged.connect(self._validate)
        splitter = QSplitter(Qt.Horizontal)
        self.file_list = QListWidget()
        self.file_list.setAccessibleName('Selected files and mappings')
        self.file_list.setMinimumWidth(180)
        splitter.addWidget(self.file_list)
        editor = QWidget()
        edit_layout = QVBoxLayout(editor)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        self.type_combo = QComboBox()
        self.type_combo.setAccessibleName('Data type for selected file')
        self.type_combo.addItem('XY table', 'xy')
        self.type_combo.addItem('Gate matrix', 'dat')
        self.type_reason = QLabel()
        self.type_reason.setTextFormat(Qt.PlainText)
        self.type_reason.setWordWrap(True)
        if entries is not None:
            type_row = QFormLayout()
            type_row.addRow('Data type:', self.type_combo)
            edit_layout.addLayout(type_row)
            edit_layout.addWidget(self.type_reason)
        else:
            self.type_combo.hide()
            self.type_reason.hide()
        self.type_combo.currentIndexChanged.connect(self._change_type)
        self.preview_info = QLabel()
        self.preview_info.setWordWrap(True)
        edit_layout.addWidget(self.preview_info)
        self.preview = QTableView()
        self.preview.setAccessibleName('Numeric import preview')
        self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        edit_layout.addWidget(self.preview, 1)
        form = QFormLayout()
        self.x_combo = QComboBox()
        self.x_combo.setAccessibleName('X column for selected file')
        self.y_list = QListWidget()
        self.y_list.setAccessibleName('Y columns for selected file')
        self.y_list.setMaximumHeight(100)
        form.addRow('X column:', self.x_combo)
        form.addRow('Y columns:', self.y_list)
        edit_layout.addLayout(form)
        self.apply_btn = QPushButton('Apply to matching files')
        self.apply_btn.setToolTip('Unique exact names may move between columns. Headerless tables require the same column count and generated order.')
        self.apply_btn.clicked.connect(self._apply_matching)
        edit_layout.addWidget(self.apply_btn)
        splitter.addWidget(editor)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 630])
        layout.addWidget(splitter, 1)
        self.feedback = QLabel('Named columns match by unique exact name; headerless files match only the same column count and order.')
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        self.include_existing_chk = QCheckBox('Include existing curves (add again)')
        self.include_existing_chk.setToolTip('Unchecked: skip the same source Sheet, X column, Y column and values already in the graph.')
        layout.addWidget(self.include_existing_chk)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.import_btn = buttons.addButton('Import only', QDialogButtonBox.AcceptRole)
        self.compare_btn = buttons.addButton('Import and compare', QDialogButtonBox.AcceptRole)
        self.import_btn.setDefault(comparison_id is None)
        self.import_btn.setVisible(comparison_id is None)
        self.compare_btn.setDefault(comparison_id is not None)
        self.import_btn.clicked.connect(lambda: self._commit(False))
        self.compare_btn.clicked.connect(lambda: self._commit(True))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.file_list.currentRowChanged.connect(self._select_file)
        self.x_combo.currentIndexChanged.connect(self._edit_mapping)
        self.y_list.itemChanged.connect(self._edit_mapping)
        self.comparison_combo.currentIndexChanged.connect(self._validate)
        for table in tables:
            self.file_list.addItem(table.source_path.name)
        for row in range(len(tables)):
            self._summary(row)
        self.destination_combo.setCurrentIndex(0)
        self._destination_changed()
        self.file_list.setCurrentRow(0)

    def _destination_changed(self, *_):
        self.name_edit.setEnabled(self.destination_combo.currentData() is None)
        self._validate()

    def _summary(self, row):
        table = self.tables[row]
        x, ys = self.mappings[row]
        label = f'{table.source_path.name}\nX: {table.column_names[x]} · Y: ' + ', '.join(table.column_names[y] for y in ys)
        if self.entries is not None:
            label = ('Gate matrix · ' if self.entries[row].kind == 'dat' else 'XY table · ') + label
        item = self.file_list.item(row)
        item.setText(label)
        item.setToolTip(str(table.source_path) + ('\nReuses the retained Sheet in its original workbook.' if self.reused[row] else ''))

    def _select_file(self, row):
        if row < 0:
            return
        table = self.tables[row]
        self._syncing = True
        try:
            matrix = self.entries is not None and self.entries[row].kind == 'dat'
            if self.entries is not None:
                entry = self.entries[row]
                self.type_combo.setCurrentIndex(self.type_combo.findData(entry.kind))
                self.type_combo.setEnabled(entry.sheet_id is None)
                for index in range(self.type_combo.count()):
                    kind = self.type_combo.itemData(index)
                    self.type_combo.model().item(index).setEnabled(kind in entry.tables)
                    self.type_combo.setItemData(index, entry.option_errors.get(kind, ''), Qt.ToolTipRole)
                detected = 'Gate matrix' if self._detected_types[row] == 'dat' else 'XY table'
                prefix = ('Automatic choice: ' + detected + '. ' if entry.sheet_id is None else '')
                if entry.kind != self._detected_types[row]:
                    chosen = 'Gate matrix' if matrix else 'XY table'
                    prefix = f'Manual type correction: {chosen}. Initial automatic choice: {detected}. '
                self.type_reason.setText(prefix + entry.reason + (' X is the interpreted spectral axis; each Y is one Gate, sorted by its value. Gate selections set comparison curves; every Gate is imported.' if matrix else ' Choose X and any Y columns independently; every column is imported.'))
            self.x_combo.setEnabled(not matrix)
            self.x_combo.clear()
            self.y_list.clear()
            x, ys = self.mappings[row]
            for column, name in enumerate(table.column_names):
                text = f'{column + 1}: {name}'
                self.x_combo.addItem(text, column)
                item = QListWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if column in ys else Qt.Unchecked)
                if column == x:
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                self.y_list.addItem(item)
            self.x_combo.setCurrentIndex(x)
            old = self.preview.model()
            self.preview.setModel(_PreviewModel(table, self.preview))
            self.preview.resizeColumnsToContents()
            if old is not None:
                old.deleteLater()
            suffix = ' Retained Sheet values; source is already imported and stays in its original workbook.' if self.reused[row] else ''
            self.preview_info.setText(f'{table.source_path.name} · {table.data.shape[0]} rows × {table.data.shape[1]} columns. Preview: first 100 rows.' + suffix)
        finally:
            self._syncing = False
        self._validate()

    def _change_type(self, *_):
        if self._syncing or self.entries is None or self.file_list.currentRow() < 0:
            return
        row = self.file_list.currentRow()
        entry = self.entries[row]
        kind = self.type_combo.currentData()
        if entry.sheet_id is not None or kind not in entry.tables:
            return
        self._type_mappings[row, entry.kind] = self.mappings[row]
        entry.kind = kind
        self.tables[row] = entry.table
        self.mappings[row] = self._type_mappings.get((row, kind), (0, list(range(1, entry.table.data.shape[1]))))
        self._summary(row)
        self._select_file(row)

    def _edit_mapping(self, *_):
        if self._syncing or self.file_list.currentRow() < 0:
            return
        x = self.x_combo.currentIndex()
        self._syncing = True
        try:
            for column in range(self.y_list.count()):
                item = self.y_list.item(column)
                item.setFlags((item.flags() | Qt.ItemIsEnabled) if column != x else (item.flags() & ~Qt.ItemIsEnabled))
                if column == x:
                    item.setCheckState(Qt.Unchecked)
            ys = [c for c in range(self.y_list.count()) if c != x and self.y_list.item(c).checkState() == Qt.Checked]
            row = self.file_list.currentRow()
            self.mappings[row] = (x, ys)
            self._summary(row)
        finally:
            self._syncing = False
        self._validate()

    def _apply_matching(self):
        row = self.file_list.currentRow()
        source = self.tables[row]
        x, ys = self.mappings[row]
        applied, skipped = 0, []
        for index, target in enumerate(self.tables):
            if index == row:
                continue
            if self.entries is not None and self.entries[index].kind != self.entries[row].kind:
                skipped.append(target.source_path.name)
                continue
            columns = [x, *ys]
            if (source.has_header and target.has_header
                    and len(set(source.column_names)) == len(source.column_names)
                    and len(set(target.column_names)) == len(target.column_names)
                    and all(source.column_names[c] in target.column_names for c in columns)):
                remapped = [target.column_names.index(source.column_names[c]) for c in columns]
                self.mappings[index] = (remapped[0], remapped[1:])
            elif (not source.has_header and not target.has_header
                    and source.column_names == target.column_names
                    and source.data.shape[1] == target.data.shape[1]):
                self.mappings[index] = (x, list(ys))
            else:
                skipped.append(target.source_path.name)
                continue
            applied += 1
            self._summary(index)
        self.feedback.setText(f'Applied to {applied} other file(s); skipped {len(skipped)}.' +
            (' Ambiguous or missing names / incompatible headerless columns: ' + ', '.join(skipped) if skipped else ''))
        self._validate()

    def _validate(self, *_):
        error, no_y = '', []
        if self.destination_combo.currentData() is None and not self.name_edit.text().strip():
            error = 'Enter a name for the new workbook.'
        for table, mapping in zip(self.tables, self.mappings):
            try:
                validate_import_mapping(table, mapping)
            except ValueError as exc:
                error = f'{table.source_path.name}: {exc}'
                break
            if not mapping[1]:
                no_y.append(table.source_path.name)
        self.validation.setText(error or ('Select Y columns to compare: ' + ', '.join(no_y) if no_y else ''))
        self.import_btn.setEnabled(not error)
        self.compare_btn.setEnabled(not error and not no_y)
        self.apply_btn.setEnabled(not error)
        self.compare_btn.setText('Import and compare' if self.comparison_combo.currentData() is None else 'Add to comparison')

    def _commit(self, compare):
        self._validate()
        if (self.compare_btn if compare else self.import_btn).isEnabled():
            self.compare = compare
            self.accept()
