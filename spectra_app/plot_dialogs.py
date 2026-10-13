"""Native target selectors, kept separate from single-dataset navigation."""
from dataclasses import replace
import os
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .plot_templates import FormatApplicationOptions, apply_format
from .dataset_state import dataset_display_labels, detached_graph_session


class PlotTargetsDialog(QDialog):
    def __init__(self, sessions, active_id, parent=None):
        super().__init__(parent)
        self.sessions = {session.dataset_id: session for session in sessions}
        self.resize(600, 520)
        self.layout = QVBoxLayout(self)
        scope = QLabel('Choose active graphs — one per imported DAT:')
        scope.setWordWrap(True)
        self.layout.addWidget(scope)
        self.targets = QListWidget()
        self.targets.setAccessibleName('Active graph targets by imported DAT')
        self.targets.setSelectionMode(QListWidget.NoSelection)
        self._target_labels = dataset_display_labels(self.sessions.values())
        self._target_labels = {key: label + ' — Graph: ' + self.sessions[key].active_graph.name
                               for key, label in self._target_labels.items()}
        for session in self.sessions.values():
            path = session.source_path
            item = QListWidgetItem(self._target_labels[session.dataset_id])
            item.setData(Qt.UserRole, session.dataset_id)
            item.setToolTip(str(path.resolve()) if path else 'Untitled dataset')
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if session.dataset_id == active_id else Qt.Unchecked)
            self.targets.addItem(item)
        self.layout.addWidget(self.targets)
        row = QHBoxLayout()
        self.select_all_btn, self.clear_btn = QPushButton('Select all'), QPushButton('Clear')
        row.addWidget(self.select_all_btn); row.addWidget(self.clear_btn); row.addStretch()
        self.select_all_btn.clicked.connect(lambda: self._select(Qt.Checked))
        self.clear_btn.clicked.connect(lambda: self._select(Qt.Unchecked))
        self.layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.targets.itemChanged.connect(self._selection_changed)

    def selected_ids(self):
        return [self.targets.item(i).data(Qt.UserRole) for i in range(self.targets.count())
                if self.targets.item(i).checkState() == Qt.Checked]

    def _select(self, state):
        for i in range(self.targets.count()):
            self.targets.item(i).setCheckState(state)

    def _selection_changed(self, *_args):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.selected_ids()))


class ApplyFormatDialog(PlotTargetsDialog):
    def __init__(self, sessions, active_id, template, parent=None, *, source_label=None):
        super().__init__(sessions, active_id, parent)
        self.template = template
        self.setWindowTitle('Apply format — ' + template.name)
        self.resize(900, 640)
        targets_panel = QWidget()
        targets_layout = QVBoxLayout(targets_panel)
        targets_layout.setContentsMargins(0, 0, 0, 0)
        # Reuse the selector and its actions from PlotTargetsDialog.
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget() is not None:
                targets_layout.addWidget(item.widget(), 1 if item.widget() is self.targets else 0)
            else:
                targets_layout.addLayout(item.layout())
        source = QLabel('Source: ' + (source_label or template.name))
        source.setTextFormat(Qt.PlainText)
        source.setWordWrap(True)
        source.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.layout.addWidget(source)
        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.target_preview_splitter = QSplitter(Qt.Vertical)
        self.target_preview_splitter.setChildrenCollapsible(False)
        self.target_preview_splitter.addWidget(targets_panel)
        self.content_splitter.addWidget(self.target_preview_splitter)
        self.settings_area = QScrollArea()
        self.settings_area.setWidgetResizable(True)
        self.settings_area.setMinimumWidth(220)
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        quick = QHBoxLayout()
        self.only_format_btn = QPushButton('Only format')
        self.format_ranges_btn = QPushButton('Format + ranges')
        quick.addWidget(self.only_format_btn); quick.addWidget(self.format_ranges_btn); quick.addStretch()
        settings_layout.addLayout(quick)
        groups = QVBoxLayout()
        self.option_checks = {}
        basic = [('layout', 'Page / frame / DPI'), ('typography', 'Fonts'),
                 ('axis', 'Axis ticks / border / direction'), ('colormap', 'Colormap'), ('colorbar', 'Colorbar')]
        optional = [('x_limits', 'Data X range'), ('y_limits', 'Data Y range'),
                    ('color_limits', 'Color limits (V)'), ('mapping', 'Color mapping'),
                    ('title_position', 'Title position'), ('annotation_positions', 'Annotation positions')]
        for column, (name, entries) in enumerate((('Format groups', basic), ('Optional fields', optional))):
            box = QGroupBox(name)
            controls = QVBoxLayout(box)
            for key, label in entries:
                check = QCheckBox(label)
                available = (getattr(template, key) is not None if key in ('x_limits', 'y_limits', 'title_position')
                             else bool(template.annotation_layout) if key == 'annotation_positions' else True)
                check.setEnabled(available)
                check.setChecked(column == 0)
                if not available:
                    check.setText(label + ' — unavailable in source')
                self.option_checks[key] = check
                controls.addWidget(check)
                check.toggled.connect(self._selection_changed)
            groups.addWidget(box)
        settings_layout.addLayout(groups)
        self.copy_mapping = self.option_checks['mapping']
        self.copy_limits = self.option_checks['color_limits']
        self.lock_ranges = QCheckBox('Lock copied ranges (Fix their endpoints)')
        self.lock_ranges.setChecked(True)
        self.lock_ranges.toggled.connect(self._selection_changed)
        settings_layout.addWidget(self.lock_ranges)
        self.only_format_btn.clicked.connect(lambda: self._quick_options(False))
        self.format_ranges_btn.clicked.connect(lambda: self._quick_options(True))
        self.pair_area = QScrollArea()
        self.pair_area.setWidgetResizable(True)
        self.pair_area.setMaximumHeight(200)
        content = QWidget()
        self.pair_layout = QVBoxLayout(content)
        note = QLabel('Explicit source → target pairing. Skip keeps that target annotation in place.\n'
                      'Only the text / tail position transfers; target text, IDs and arrow heads stay intact.')
        note.setWordWrap(True)
        self.pair_layout.addWidget(note)
        self.pair_combos, self.pair_boxes = {}, {}
        for dataset_id, session in self.sessions.items():
            box = QGroupBox('Target annotation pairs')
            form = QFormLayout(box)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            target_label = QLabel(self._target_labels[dataset_id])
            target_label.setToolTip(str(session.source_path.resolve()) if session.source_path else 'Untitled dataset')
            target_label.setTextFormat(Qt.PlainText)
            target_label.setWordWrap(True)
            form.addRow(target_label)
            combos = {}
            for entry in template.annotation_layout:
                combo = QComboBox()
                combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                combo.setMinimumContentsLength(18)
                combo.addItem('Skip', None)
                for annotation in session.presentation.annotations:
                    if annotation.kind == entry.kind:
                        combo.addItem(f'{annotation.kind}: {annotation.text} [{annotation.id}]', annotation.id)
                label = QLabel(f'{entry.kind}: {entry.text} [{entry.id}] →')
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                form.addRow(label, combo)
                combo.currentIndexChanged.connect(self._selection_changed)
                combos[entry.id] = combo
            self.pair_combos[dataset_id], self.pair_boxes[dataset_id] = combos, box
            self.pair_layout.addWidget(box)
        self.pair_area.setWidget(content)
        settings_layout.addWidget(self.pair_area)
        settings_layout.addStretch(1)
        self.settings_area.setWidget(settings)
        self.content_splitter.addWidget(self.settings_area)
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_label = QLabel('Changes for each checked active graph:')
        preview_label.setWordWrap(True)
        preview_layout.addWidget(preview_label)
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        preview_layout.addWidget(self.preview_text, 1)
        self.target_preview_splitter.addWidget(preview_panel)
        self.target_preview_splitter.setSizes([220, 250])
        self.content_splitter.setSizes([420, 400])
        self.layout.addWidget(self.content_splitter, 1)
        self.layout.addWidget(self.buttons)
        self.buttons.button(QDialogButtonBox.Ok).setText('Apply format')
        self._selection_changed()

    def options(self):
        return FormatApplicationOptions(**{name: check.isChecked() and check.isEnabled()
                                           for name, check in self.option_checks.items()},
                                        lock_copied_ranges=self.lock_ranges.isChecked())

    def annotation_pairs(self):
        return {dataset_id: pairs for dataset_id in self.selected_ids()
                if (pairs := {source_id: combo.currentData()
                              for source_id, combo in self.pair_combos[dataset_id].items()
                              if combo.currentData() is not None})}

    def _quick_options(self, ranges):
        for name, check in self.option_checks.items():
            check.setChecked(check.isEnabled() and (name in ('layout', 'typography', 'axis', 'colormap', 'colorbar')
                             or ranges and name in ('x_limits', 'y_limits', 'color_limits')))

    @staticmethod
    def _range_detail(label, old, new, old_locks, new_locks):
        fixed = lambda states: '/'.join('Fixed' if value else 'Auto' for value in states)
        return f'{label}: {old} → {new}; {fixed(old_locks)} → {fixed(new_locks)}'

    @staticmethod
    def _changed_field_label(field, session):
        labels = {'width_in': 'Page width (in)', 'height_in': 'Page height (in)',
                  'frame_width_in': 'Data plot frame width (in)', 'frame_height_in': 'Data plot frame height (in)',
                  'heatmap_dpi': 'Heatmap DPI', 'aspect_locked': 'Page aspect ratio lock',
                  'font_family': 'Font family', 'numeric_pt': 'Numeric font size (pt)',
                  'text_pt': 'Text font size (pt)', 'x_limits': 'Data X range',
                  'y_limits': 'Data Y range', 'color_limits': 'Color limits (V)',
                  'norm': 'Color mapping', 'last_valid_color_limits': 'Stored color limits',
                  'last_valid_region_color_limits': 'Regional color limits',
                  'title_position': 'Title position'}
        if field in labels:
            return labels[field]
        group, _, name = field.partition('.')
        if group == 'bounds':
            return labels.get(name, name) + ' endpoints'
        if group == 'locks':
            axis, endpoint = name[:1].upper(), 'minimum' if name.endswith('min') else 'maximum'
            return f'{axis} {endpoint} lock'
        if group == 'annotations':
            identifier, _, setting = name.partition('.')
            annotation = next((item for item in session.presentation.annotations if item.id == identifier), None)
            title = annotation.text if annotation and annotation.text else 'Unlabelled annotation'
            setting = 'coordinates' if setting == 'coordinate_space' else 'position'
            return f'“{title}” {setting}'
        # Keep less common axis / colorbar settings specific, with readable units.
        if field.endswith(('_in', '_pt')):
            field = field[:-3] + f' ({field[-2:]})'
        return field.replace('_', ' ').replace('.', ' · ').capitalize()

    def _selection_changed(self, *_args):
        super()._selection_changed()
        if not hasattr(self, 'preview_text'):
            return
        options, pairs_by_target = self.options(), self.annotation_pairs()
        self.pair_area.setVisible(options.annotation_positions)
        for dataset_id, box in self.pair_boxes.items():
            box.setVisible(dataset_id in self.selected_ids())
        lines = []
        for dataset_id in self.selected_ids():
            session = self.sessions[dataset_id]
            pairs = pairs_by_target.get(dataset_id, {})
            effective = options
            notice = ''
            if options.annotation_positions and not pairs:
                effective = replace(options, annotation_positions=False)
                notice = '\nAnnotation positions skipped: no explicit usable pairs selected.'
            try:
                candidate = detached_graph_session(session)
                result = apply_format(candidate, self.template, options=effective, annotation_pairs=pairs)
                fields = ', '.join(check.text() for name, check in self.option_checks.items() if getattr(options, name))
                detail = 'Selected: ' + (fields or 'none') + '\nChanged: ' + (
                    ', '.join(self._changed_field_label(field, session) for field in result.changed_fields)
                    or 'Already matches these fields')
                for axis, group, label in (('x', 'x_limits', 'X'), ('y', 'y_limits', 'Y'),
                                           ('v', 'color_limits', 'V')):
                    if getattr(effective, group):
                        detail += '\n' + self._range_detail(label, getattr(session.view, group),
                            getattr(candidate.view, group), [session.view.locks[f'{axis}{end}'] for end in ('min', 'max')],
                            [candidate.view.locks[f'{axis}{end}'] for end in ('min', 'max')])
                        if axis == 'v' and self.template.region_color_limits:
                            detail += f'\nRegional V: {session.view.last_valid_region_color_limits} → {candidate.view.last_valid_region_color_limits}'
            except (ValueError, TypeError) as exc:
                detail = 'Cannot apply: ' + str(exc)
            lines.append(f'{self._target_labels[dataset_id]}\n{detail}{notice}')
        self.preview_text.setPlainText('\n\n'.join(lines))


class BatchExportDialog(QDialog):
    def __init__(self, sessions, active_id, parent=None):
        super().__init__(parent)
        self.sessions = {session.dataset_id: session for session in sessions}
        self.resize(600, 520)
        self.layout = QVBoxLayout(self)
        self.setWindowTitle('Batch export formal pages')
        self.layout.addWidget(QLabel('Choose graphs from imported DATs:'))
        self.targets = QTreeWidget()
        self.targets.setHeaderHidden(True)
        self.targets.setSelectionMode(QTreeWidget.NoSelection)
        labels = dataset_display_labels(self.sessions.values())
        for session in self.sessions.values():
            if not any(graph.published for graph in session.graphs.values()):
                continue
            item = QTreeWidgetItem(self.targets, [labels[session.dataset_id]])
            item.setData(0, Qt.UserRole, session.dataset_id)
            item.setToolTip(0, str(session.source_path.resolve()) if session.source_path else 'Untitled dataset')
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            for graph in session.graphs.values():
                if not graph.published:
                    continue
                child = QTreeWidgetItem(item, [graph.name])
                child.setData(0, Qt.UserRole, (session.dataset_id, graph.graph_id))
                child.setToolTip(0, item.toolTip(0))
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                current = session.dataset_id == active_id and graph.graph_id == session.active_graph_id
                child.setCheckState(0, Qt.Checked if current else Qt.Unchecked)
            item.setExpanded(True)
        self.layout.addWidget(self.targets)
        row = QHBoxLayout()
        self.select_all_btn, self.clear_btn = QPushButton('Select all'), QPushButton('Clear')
        row.addWidget(self.select_all_btn); row.addWidget(self.clear_btn); row.addStretch()
        self.select_all_btn.clicked.connect(lambda: self._select(Qt.Checked))
        self.clear_btn.clicked.connect(lambda: self._select(Qt.Unchecked))
        self.layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(QLabel('Output:'))
        self.formats_combo = QComboBox()
        self.formats_combo.addItems(['SVG + PNG', 'SVG', 'PNG'])
        row.addWidget(self.formats_combo)
        self.layout.addLayout(row)
        self.common_folder = QCheckBox('Use one output folder (default: beside each DAT)')
        self.layout.addWidget(self.common_folder)
        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setEnabled(False)
        browse = QPushButton('Browse…'); browse.setEnabled(False)
        self.common_folder.toggled.connect(self.folder_edit.setEnabled)
        self.common_folder.toggled.connect(browse.setEnabled)
        def choose():
            folder = QFileDialog.getExistingDirectory(self, 'Batch output folder', self.folder_edit.text())
            if folder:
                self.folder_edit.setText(folder)
        browse.clicked.connect(choose)
        row.addWidget(self.folder_edit); row.addWidget(browse)
        self.layout.addLayout(row)
        self.layout.addWidget(self.buttons)
        self.buttons.button(QDialogButtonBox.Ok).setText('Export selected')
        self.folder_validation_label = QLabel()
        self.folder_validation_label.setWordWrap(True)
        self.layout.insertWidget(self.layout.count() - 1, self.folder_validation_label)
        self.common_folder.toggled.connect(self._selection_changed)
        self.folder_edit.textChanged.connect(self._selection_changed)
        self.targets.itemChanged.connect(self._selection_changed)
        self._selection_changed()

    def selected_graph_targets(self):
        return [child.data(0, Qt.UserRole)
                for i in range(self.targets.topLevelItemCount())
                for item in (self.targets.topLevelItem(i),)
                for j in range(item.childCount())
                for child in (item.child(j),) if child.checkState(0) == Qt.Checked]

    def selected_ids(self):
        return list(dict.fromkeys(dataset_id for dataset_id, _graph_id in self.selected_graph_targets()))

    def _select(self, state):
        for i in range(self.targets.topLevelItemCount()):
            self.targets.topLevelItem(i).setCheckState(0, state)

    def _folder_error(self):
        if not self.common_folder.isChecked():
            return ''
        value = self.folder_edit.text().strip()
        if not value:
            return 'Choose an existing output folder before exporting.'
        path = Path(value)
        try:
            usable = path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        except (OSError, ValueError):
            usable = False
        return '' if usable else 'Choose an existing, writable output folder.'

    def _selection_changed(self, *_args):
        error = self._folder_error()
        self.folder_validation_label.setText(error)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.selected_graph_targets()) and not error)

    def accept(self):
        self._selection_changed()
        if self.selected_graph_targets() and not self._folder_error():
            super().accept()

    def formats(self):
        return [('svg','png'), ('svg',), ('png',)][self.formats_combo.currentIndex()]

    def destination(self):
        return self.folder_edit.text().strip() if self.common_folder.isChecked() else None
