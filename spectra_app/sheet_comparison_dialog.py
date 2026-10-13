"""Existing-Sheet comparison choices, staged until one validated batch is accepted."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QSizePolicy, QSplitter, QTableView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

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


class SheetComparisonDialog(QDialog):
    def __init__(self, workspace, parent=None, *, selected_sheet_ids=(), comparison_id=None):
        super().__init__(parent)
        self.workspace = workspace
        self._fixed_target = comparison_id
        self._syncing = False
        self._choices = {}
        self._items = {}
        self._names = {}
        self.setWindowTitle('Add data to comparison' if comparison_id is not None else 'Compare selected data')
        self.resize(960, 680)
        layout = QVBoxLayout(self)
        introduction = QLabel('Check Sheets, then review X and Y columns. A Workbook checkbox selects all its Sheets.')
        introduction.setToolTip('Original X grids are retained.')
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        destination = QHBoxLayout()
        destination.addWidget(QLabel('Destination:'))
        self.destination_combo = QComboBox(self)
        self.destination_combo.setAccessibleName('Destination comparison')
        self.destination_combo.setMinimumContentsLength(12)
        self.destination_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        if comparison_id is None:
            self.destination_combo.addItem('New comparison', None)
            for comparison in workspace.comparisons.values():
                self.destination_combo.addItem(comparison.name, comparison.comparison_id)
        else:
            target = workspace.comparisons.get(comparison_id)
            self.destination_combo.addItem(target.name if target else 'Comparison no longer available', comparison_id)
            self.destination_combo.setEnabled(False)
        if comparison_id is None:
            destination.addWidget(self.destination_combo, 1)
        else:
            # A fixed destination is information, so keep it readable rather
            # than making the owning graph's name disabled/low-contrast text.
            self.destination_combo.hide()
            fixed_destination = QLabel(self.destination_combo.currentText())
            fixed_destination.setTextFormat(Qt.PlainText)
            fixed_destination.setWordWrap(True)
            destination.addWidget(fixed_destination, 1)
        layout.addLayout(destination)
        self.splitter = QSplitter(Qt.Horizontal)
        source_panel = QWidget()
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(0, 0, 0, 0)
        self.select_originals_btn = QPushButton('Select only Originals\n(all Workbooks)')
        self.select_originals_btn.setAccessibleName('Select only Original Sheets in all Workbooks')
        self.select_originals_btn.clicked.connect(self._select_originals)
        source_layout.addWidget(self.select_originals_btn)
        self.source_tree = QTreeWidget()
        self.source_tree.setHeaderLabels(['Include Sheets', 'Mapping'])
        self.source_tree.setAccessibleName('Workbook and Sheet sources to include')
        self.source_tree.setMinimumWidth(200)
        self.source_tree.setColumnWidth(0, 160)
        source_layout.addWidget(self.source_tree, 1)
        self.splitter.addWidget(source_panel)
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.provenance = QLabel()
        self.provenance.setWordWrap(True)
        self.provenance.setTextFormat(Qt.PlainText)
        self.provenance.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.provenance.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        editor_layout.addWidget(self.provenance)
        form = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.setAccessibleName('Values for selected Sheet')
        self.x_combo = QComboBox()
        self.x_combo.setAccessibleName('X column for selected Sheet')
        for combo in (self.mode_combo, self.x_combo):
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        form.addRow('Values:', self.mode_combo)
        form.addRow('X column:', self.x_combo)
        editor_layout.addLayout(form)
        self.mapping_splitter = QSplitter(Qt.Horizontal)
        self.mapping_splitter.setChildrenCollapsible(False)
        y_panel = QWidget()
        y_layout = QVBoxLayout(y_panel)
        y_layout.setContentsMargins(0, 0, 0, 0)
        y_label = QLabel('Y columns · check one or more')
        y_label.setWordWrap(True)
        y_layout.addWidget(y_label)
        self.y_list = QListWidget()
        self.y_list.setAccessibleName('Y columns for selected Sheet')
        self.y_list.setMinimumHeight(74)
        y_layout.addWidget(self.y_list, 1)
        self.match_gates_btn = QPushButton('Match selected Gates')
        self.match_gates_btn.setAccessibleName('Match selected Gates')
        self.match_gates_btn.setToolTip('Apply this source’s selected physical Gate values to checked DAT Original and Processed Sheets. Every value must match exactly.')
        self.match_gates_btn.clicked.connect(self._match_selected_gates)
        y_layout.addWidget(self.match_gates_btn)
        self.mapping_splitter.addWidget(y_panel)
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_info = QLabel()
        self.preview_info.setWordWrap(True)
        self.preview_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        preview_layout.addWidget(self.preview_info)
        self.preview = QTableView()
        self.preview.setAccessibleName('Selected Sheet numeric preview')
        self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview.horizontalHeader().setDefaultSectionSize(110)
        preview_layout.addWidget(self.preview, 1)
        self.mapping_splitter.addWidget(preview_panel)
        self.mapping_splitter.setStretchFactor(0, 1)
        self.mapping_splitter.setStretchFactor(1, 2)
        self.mapping_splitter.setSizes([180, 330])
        editor_layout.addWidget(self.mapping_splitter, 1)
        self.splitter.addWidget(editor)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([350, 590])
        layout.addWidget(self.splitter, 1)
        self.summary = QLabel()
        self.summary.setAccessibleName('Included Sheet and curve count')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.PlainText)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.include_existing_chk = QCheckBox('Include existing curves (add again)')
        self.include_existing_chk.setToolTip('Unchecked: skip the same source Sheet, X column, Y column and values already in the graph.')
        self.include_existing_chk.toggled.connect(self._validate)
        layout.addWidget(self.include_existing_chk)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.commit_btn = QPushButton('Add data' if comparison_id is not None else 'Create comparison')
        self.commit_btn.setDefault(True)
        buttons.addButton(self.commit_btn, QDialogButtonBox.AcceptRole)
        buttons.rejected.connect(self.reject)
        self.commit_btn.clicked.connect(self.accept)
        layout.addWidget(buttons)
        selected = set(selected_sheet_ids)
        for book in workspace.workbooks.values():
            parent_item = QTreeWidgetItem([book.name])
            parent_item.setFlags(parent_item.flags() | Qt.ItemIsUserCheckable)
            parent_item.setCheckState(0, Qt.Unchecked)
            self.source_tree.addTopLevelItem(parent_item)
            for sheet_id in book.sheet_ids:
                sheet = workspace.sheets[sheet_id]
                self._choices[sheet_id] = (sheet.x_column, list(sheet.y_columns), sheet.mode)
                self._names[sheet_id] = f'{book.name} / {sheet.name}'
                item = QTreeWidgetItem([sheet.name])
                item.setData(0, Qt.UserRole, sheet_id)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked if sheet_id in selected else Qt.Unchecked)
                item.setToolTip(0, f'{self._names[sheet_id]}\n{sheet.source_path or "Embedded values"}')
                parent_item.addChild(item)
                self._items[sheet_id] = item
                self._update_mapping_text(sheet_id)
            parent_item.setExpanded(True)
            self._refresh_parent_check(parent_item)
        self.source_tree.currentItemChanged.connect(self._load_editor)
        self.source_tree.itemChanged.connect(self._source_check_changed)
        self.x_combo.currentIndexChanged.connect(self._mapping_changed)
        self.y_list.itemChanged.connect(self._mapping_changed)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.destination_combo.currentIndexChanged.connect(self._validate)
        first = next((self._items[sid] for sid in self._items if sid in selected), next(iter(self._items.values()), None))
        self.source_tree.setCurrentItem(first)
        if first is None:
            self._load_editor()
        self._validate()
        self.source_tree.setFocus()

    def selected_mappings(self):
        return {sid: (x, list(ys), mode) for sid, (x, ys, mode) in self._choices.items()
                if self._items[sid].checkState(0) == Qt.Checked}

    def _current_id(self):
        item = self.source_tree.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def _refresh_parent_check(self, parent):
        children = [parent.child(i) for i in range(parent.childCount())]
        checked = any(item.checkState(0) == Qt.Checked for item in children)
        state = (Qt.Checked if children and all(item.checkState(0) == Qt.Checked for item in children)
                 else Qt.PartiallyChecked if checked else Qt.Unchecked)
        parent.setCheckState(0, state)

    def _source_check_changed(self, item, column):
        if self._syncing or column != 0:
            return
        self._syncing = True
        try:
            if item.parent() is None:
                checked = item.checkState(0) == Qt.Checked
                for index in range(item.childCount()):
                    child = item.child(index)
                    child.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                self._refresh_parent_check(item)
            else:
                self._refresh_parent_check(item.parent())
        finally:
            self._syncing = False
        self._validate()

    def _select_originals(self):
        self._syncing = True
        try:
            for sid, item in self._items.items():
                sheet = self.workspace.sheets.get(sid)
                item.setCheckState(0, Qt.Checked if sheet is not None and sheet.role == 'original' else Qt.Unchecked)
            for index in range(self.source_tree.topLevelItemCount()):
                self._refresh_parent_check(self.source_tree.topLevelItem(index))
        finally:
            self._syncing = False
        self._validate()

    def _match_selected_gates(self):
        """Stage physical-value matches across the entire batch before changing choices."""
        source_id = self._current_id()
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
            self.feedback.setText(str(exc))
            return
        self._choices.update(staged)
        for sid in staged:
            self._update_mapping_text(sid)
        self._load_editor()
        self._validate()
        self.feedback.setText(f'Matched {len(values)} physical Gate value(s) in {matched} checked DAT Sheet(s).')

    def _load_editor(self, *_args):
        sheet_id = self._current_id()
        sheet = self.workspace.sheets.get(sheet_id)
        self._syncing = True
        old_model = self.preview.model()
        self.preview.setModel(None)
        if old_model is not None:
            old_model.deleteLater()
        try:
            self.x_combo.clear()
            self.y_list.clear()
            self.mode_combo.clear()
            for widget in (self.x_combo, self.mode_combo, self.y_list):
                widget.setEnabled(sheet is not None)
            self.provenance.setText(self._names.get(sheet_id, 'Select a Sheet to review its mapping.'))
            self.preview_info.clear()
            self.match_gates_btn.setEnabled(bool(sheet is not None and sheet.kind == 'dat'
                and sheet.role in ('original', 'processed')))
            if sheet is not None:
                x, ys, mode = self._choices[sheet_id]
                self.provenance.setText(self._names[sheet_id])
                self.provenance.setToolTip(f'{self._names[sheet_id]}\n{sheet.snapshot_note or sheet.source_path or "Embedded values"}')
                self.mode_combo.addItem(sheet.role.replace('_', ' ').title(), sheet.mode)
                self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))
                self.mode_combo.setEnabled(False)
                columns = sheet_columns(self.workspace, sheet, mode)
                numeric = sheet_numeric_columns(self.workspace, sheet)
                for index, label in enumerate(columns):
                    text = f'{index + 1}: {label}'
                    self.x_combo.addItem(text, index)
                    self.x_combo.model().item(index).setEnabled(index in numeric)
                    item = QListWidgetItem(text)
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    if index == x or index not in numeric:
                        item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                    item.setCheckState(Qt.Checked if index in ys else Qt.Unchecked)
                    self.y_list.addItem(item)
                self.x_combo.setCurrentIndex(x)
                self.x_combo.setEnabled(sheet.kind != 'dat' or sheet.role == 'fit_parameters')
                model = _SheetPreviewModel(self.workspace, sheet, mode, self.preview)
                self.preview.setModel(model)
                count = sheet_shape(self.workspace, sheet, mode)[0]
                self.preview_info.setText(f'Preview: first {model.rowCount()} of {count} rows · all rows will be plotted.' +
                    ('\nDAT X is fixed to the energy axis. Gate labels show this source’s values.' if sheet.kind == 'dat' else ''))
        except (ValueError, KeyError) as exc:
            self.preview_info.setText(f'Preview unavailable: {exc}')
        finally:
            self._syncing = False

    def _mapping_changed(self, *_args):
        sheet_id = self._current_id()
        if self._syncing or sheet_id not in self._choices:
            return
        x = self.x_combo.currentData()
        numeric = sheet_numeric_columns(self.workspace, sheet_id)
        self._syncing = True
        try:
            for index in range(self.y_list.count()):
                item = self.y_list.item(index)
                item.setFlags(item.flags() | Qt.ItemIsEnabled if index != x and index in numeric else item.flags() & ~Qt.ItemIsEnabled)
                if index == x:
                    item.setCheckState(Qt.Unchecked)
        finally:
            self._syncing = False
        ys = [i for i in range(self.y_list.count()) if i in numeric and self.y_list.item(i).checkState() == Qt.Checked]
        self._choices[sheet_id] = (x, ys, self.mode_combo.currentData())
        self._update_mapping_text(sheet_id)
        self._validate()

    def _mode_changed(self, *_args):
        sheet_id = self._current_id()
        if self._syncing or sheet_id not in self._choices:
            return
        x, ys, _ = self._choices[sheet_id]
        self._choices[sheet_id] = (x, ys, self.mode_combo.currentData())
        self._load_editor()
        self._update_mapping_text(sheet_id)
        self._validate()

    def _update_mapping_text(self, sheet_id):
        x, ys, mode = self._choices[sheet_id]
        try:
            columns = sheet_columns(self.workspace, sheet_id, mode)
            text = f'X: {columns[x]} · Y: {", ".join(columns[y] for y in ys) or "none"} · {mode}'
        except (ValueError, KeyError, IndexError, TypeError):
            text = 'Review mapping'
        self._items[sheet_id].setText(1, text)
        self._items[sheet_id].setToolTip(1, text)

    def _validate(self, *_args):
        if self._syncing:
            return False
        mappings = self.selected_mappings()
        self.summary.setText(f'{len(mappings)} Sheet(s) included · {sum(len(ys) for _, ys, _ in mappings.values())} curve(s)')
        target = self._fixed_target if self._fixed_target is not None else self.destination_combo.currentData()
        self.commit_btn.setText('Create comparison' if target is None else 'Add data')
        try:
            validate_sheet_comparison(self.workspace, mappings, names=self._names)
            if target is not None and target not in self.workspace.comparisons:
                raise ValueError('The destination comparison no longer exists.')
            candidates = [(sid, x, y, mode) for sid, (x, ys, mode) in mappings.items() for y in ys]
            additions, existing = select_curve_additions(self.workspace, target, candidates)
            total = len(candidates) if self.include_existing_chk.isChecked() else len(additions)
            self.summary.setText(f'{len(mappings)} Sheet(s) included · {len(additions)} new · '
                                 f'{existing} already present · {total} to add')
            if not total:
                raise ValueError('All selected curves are already present. Select “Include existing curves (add again)” to add them again.')
        except ValueError as exc:
            self.feedback.setText(str(exc))
            self.commit_btn.setEnabled(False)
            return False
        self.feedback.clear()
        self.commit_btn.setEnabled(True)
        return True

    def accept(self):
        if self._validate():
            super().accept()
