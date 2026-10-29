"""Typed presentation models for target selection and isolated format previews."""
from dataclasses import replace
from copy import copy
import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

from .dataset_state import dataset_display_labels, detached_graph_session
from .plot_templates import FormatApplicationOptions, apply_format
from .plot_snapshot import snapshot_dataset
from .svg_export import prepare_heatmap_page


class TargetChoice(QObject):
    changed = Signal()

    def __init__(self, dataset_id, graph_id, label, tooltip, checked=False, parent=None):
        super().__init__(parent)
        self.dataset_id, self.graph_id = dataset_id, graph_id
        self._label, self._tooltip, self._checked = label, tooltip, checked

    label = Property(str, lambda self: self._label, constant=True)
    tooltip = Property(str, lambda self: self._tooltip, constant=True)
    datasetId = Property(str, lambda self: self.dataset_id, constant=True)
    graphId = Property(str, lambda self: self.graph_id or '', constant=True)
    checked = Property(bool, lambda self: self._checked, notify=changed)

    @Slot(bool)
    def setChecked(self, checked):
        if self._checked != checked:
            self._checked = checked
            self.changed.emit()


class TargetGroup(QObject):
    changed = Signal()

    def __init__(self, label, tooltip, children, parent=None):
        super().__init__(parent)
        self._label, self._tooltip, self._children = label, tooltip, children
        for child in children:
            child.setParent(self)
            child.changed.connect(self.changed)

    label = Property(str, lambda self: self._label, constant=True)
    tooltip = Property(str, lambda self: self._tooltip, constant=True)
    children = Property('QVariantList', lambda self: self._children, constant=True)
    checkState = Property(int, lambda self: 2 if all(c.checked for c in self._children)
                          else 1 if any(c.checked for c in self._children) else 0, notify=changed)

    @Slot(bool)
    def setChecked(self, checked):
        for child in self._children:
            child.setChecked(checked)


class FormatOption(QObject):
    changed = Signal()

    def __init__(self, key, label, available, checked, parent):
        super().__init__(parent)
        self._key, self._label, self._available = key, label, available
        self._checked = bool(checked)

    key = Property(str, lambda self: self._key, constant=True)
    label = Property(str, lambda self: self._label + ('' if self._available else ' — unavailable in source'), constant=True)
    available = Property(bool, lambda self: self._available, constant=True)
    checked = Property(bool, lambda self: self._checked, notify=changed)

    @Slot(bool)
    def setChecked(self, checked):
        checked = bool(checked)
        if self._checked != checked:
            self._checked = checked
            self.changed.emit()


class AnnotationPair(QObject):
    changed = Signal()

    def __init__(self, dataset_id, target_label, source, annotations, parent):
        super().__init__(parent)
        self.dataset_id, self.source_id = dataset_id, source.id
        self._target_label = target_label
        self._label = f'{source.kind}: {source.text} [{source.id}]'
        compatible = [a for a in annotations if a.kind == source.kind]
        self.target_ids = [None] + [a.id for a in compatible]
        self._choices = ['Skip'] + [f'{a.kind}: {a.text} [{a.id}]' for a in compatible]
        self._index = 0

    datasetId = Property(str, lambda self: self.dataset_id, constant=True)
    graphId = Property(str, lambda self: self.graph_id, constant=True)
    sourceId = Property(str, lambda self: self.source_id, constant=True)
    targetLabel = Property(str, lambda self: self._target_label, constant=True)
    label = Property(str, lambda self: self._label, constant=True)
    choices = Property('QStringList', lambda self: self._choices, constant=True)
    currentIndex = Property(int, lambda self: self._index, notify=changed)

    @Slot(int)
    def setCurrentIndex(self, index):
        if 0 <= index < len(self.target_ids) and index != self._index:
            self._index = index
            self.changed.emit()


class FormatPreviewProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.image = QImage()

    def requestImage(self, _identifier, size, requested_size):
        image = self.image
        if not image.isNull() and requested_size.width() > 0 and requested_size.height() > 0:
            image = image.scaled(requested_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        size.setWidth(image.width())
        size.setHeight(image.height())
        return image

    def set_svg(self, svg):
        renderer = QSvgRenderer(svg)
        size = renderer.defaultSize()
        size.scale(QSize(1000, 800), Qt.KeepAspectRatio)
        image = QImage(size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.white)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        self.image = image


class PlotTargetsBridge(QObject):
    changed = Signal()

    def __init__(self, sessions, active_id):
        super().__init__()
        self._bulk_depth = 0
        self.sessions = {s.dataset_id: s for s in sessions}
        labels = dataset_display_labels(self.sessions.values())
        self._target_labels = {key: label + ' — Graph: ' + self.sessions[key].active_graph.name
                               for key, label in labels.items()}
        self.targets = [TargetChoice(s.dataset_id, s.active_graph_id, self._target_labels[s.dataset_id],
                        str(s.source_path.resolve()) if s.source_path else 'Untitled dataset',
                        s.dataset_id == active_id, self) for s in self.sessions.values()]
        for target in self.targets:
            target.changed.connect(self._selection_changed)

    targetItems = Property('QVariantList', lambda self: self.targets, constant=True)
    canAccept = Property(bool, lambda self: bool(self.selected_ids()), notify=changed)
    selectedCount = Property(int, lambda self: len(self.selected_ids()), notify=changed)

    def selected_ids(self):
        return [t.dataset_id for t in self.targets if t.checked]

    @Slot(bool)
    def selectAll(self, checked):
        self._bulk_depth += 1
        try:
            for target in self.targets:
                target.setChecked(checked)
        finally:
            self._bulk_depth -= 1
        self._selection_changed()

    def _selection_changed(self):
        self.changed.emit()


class ApplyFormatBridge(PlotTargetsBridge):
    previewChanged = Signal()

    _basic = [('layout', 'Page / frame / DPI'), ('typography', 'Fonts'),
              ('axis', 'Axis ticks / border / direction'), ('colormap', 'Colormap'), ('colorbar', 'Colorbar')]
    _optional = [('x_limits', 'Data X range'), ('y_limits', 'Data Y range'),
                 ('color_limits', 'Color limits (V)'), ('mapping', 'Color mapping'),
                 ('title_position', 'Title position'), ('annotation_positions', 'Annotation positions')]

    def __init__(self, sessions, active_id, template, source_label=None, *, initial_options=None,
                 graph_targets=None, selected_targets=None, baseline_sessions=None,
                 adjustment=False, initial_annotation_pairs=None):
        super().__init__(sessions, active_id)
        self._graph_mode = graph_targets is not None
        self._adjustment = bool(adjustment)
        self._baselines = dict(baseline_sessions or {})
        self.groups = []
        if self._graph_mode:
            allowed = set(map(tuple, graph_targets))
            selected = set(map(tuple, selected_targets)) if selected_targets is not None else {(active_id, self.sessions[active_id].active_graph_id)} if active_id in self.sessions else set()
            labels = dataset_display_labels(self.sessions.values())
            self.targets, self._target_labels = [], {}
            for session in self.sessions.values():
                tooltip = str(session.source_path.resolve()) if session.source_path else 'Untitled dataset'
                children = []
                for graph in session.graphs.values():
                    key = (session.dataset_id, graph.graph_id)
                    if key not in allowed or not graph.published and not (adjustment and key in self._baselines):
                        continue
                    target = TargetChoice(*key, graph.name, tooltip, key in selected)
                    target.changed.connect(self._selection_changed)
                    children.append(target)
                    self.targets.append(target)
                    self._target_labels[key] = labels[session.dataset_id] + ' — Graph: ' + graph.name
                if children:
                    self.groups.append(TargetGroup(labels[session.dataset_id], tooltip, children, self))
        self.template = template
        self._source_label = source_label or template.name
        self.option_items = {}
        for key, label in self._basic + self._optional:
            available = (getattr(template, key) is not None if key in ('x_limits', 'y_limits', 'title_position')
                         else bool(template.annotation_layout) if key == 'annotation_positions' else True)
            option = FormatOption(key, label, available, getattr(initial_options, key) if initial_options is not None else key in dict(self._basic), self)
            option.changed.connect(self._selection_changed)
            self.option_items[key] = option
        self._lock_ranges = initial_options.lock_copied_ranges if initial_options is not None else True
        self.pairs = []
        for key in self._all_target_keys():
            session = self._target_session(key)
            dataset_id = key[0] if self._graph_mode else key
            for entry in template.annotation_layout:
                pair = AnnotationPair(dataset_id, self._target_labels[key], entry, session.presentation.annotations, self)
                pair.target_key = key
                pair.graph_id = key[1] if self._graph_mode else session.active_graph_id
                saved = (initial_annotation_pairs or {}).get(key, {}).get(entry.id)
                if saved in pair.target_ids:
                    pair._index = pair.target_ids.index(saved)
                pair.changed.connect(self._selection_changed)
                self.pairs.append(pair)
        self._preview_text, self._preview_url, self._preview_error = '', '', ''
        self._preview_index, self._revision, self._closed = 0, 0, False
        self._candidates = []
        self.image_provider = FormatPreviewProvider()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self.render_preview)
        self._selection_changed()

    graphTargetMode = Property(bool, lambda self: self._graph_mode, constant=True)
    adjustment = Property(bool, lambda self: self._adjustment, constant=True)
    targetGroups = Property('QVariantList', lambda self: self.groups, constant=True)
    actionText = Property(str, lambda self: (f'Revise {len(self.selected_graph_targets())} graphs' if self._adjustment else f'Apply to {len(self.selected_graph_targets())} graphs') if self._graph_mode else 'Apply format', notify=previewChanged)
    selectedCount = Property(int, lambda self: len(self.selected_graph_targets()), notify=PlotTargetsBridge.changed)

    def _all_target_keys(self):
        return [(t.dataset_id, t.graph_id) if self._graph_mode else t.dataset_id for t in self.targets]

    def _selected_keys(self):
        return [(t.dataset_id, t.graph_id) if self._graph_mode else t.dataset_id for t in self.targets if t.checked]

    def selected_graph_targets(self):
        return [(t.dataset_id, t.graph_id) for t in self.targets if t.checked]

    def selected_ids(self):
        return list(dict.fromkeys(t.dataset_id for t in self.targets if t.checked))

    @Slot(bool)
    def selectAll(self, checked):
        if not self._adjustment:
            super().selectAll(checked)

    def _target_session(self, key):
        if key in self._baselines:
            return self._baselines[key]
        if not self._graph_mode:
            return self.sessions[key]
        session = copy(self.sessions[key[0]])
        session.graphs = dict(session.graphs)
        session.activate_graph(key[1])
        return session

    def preference_options(self):
        return FormatApplicationOptions(**{key: option.checked for key, option in self.option_items.items()},
                                        lock_copied_ranges=self._lock_ranges)

    sourceLabel = Property(str, lambda self: self._source_label, constant=True)
    basicOptions = Property('QVariantList', lambda self: [self.option_items[k] for k, _ in self._basic], constant=True)
    optionalOptions = Property('QVariantList', lambda self: [self.option_items[k] for k, _ in self._optional], constant=True)
    annotationPairs = Property('QVariantList', lambda self: [p for p in self.pairs if p.target_key in self._selected_keys()], notify=previewChanged)
    annotationPositions = Property(bool, lambda self: self.option_items['annotation_positions'].checked, notify=previewChanged)
    lockRanges = Property(bool, lambda self: self._lock_ranges, notify=previewChanged)
    previewText = Property(str, lambda self: self._preview_text, notify=previewChanged)
    previewUrl = Property(str, lambda self: self._preview_url, notify=previewChanged)
    previewError = Property(str, lambda self: self._preview_error, notify=previewChanged)
    previewLabels = Property('QStringList', lambda self: [self._target_labels[key] for key in self._selected_keys()], notify=previewChanged)
    previewIndex = Property(int, lambda self: self._preview_index, notify=previewChanged)

    def options(self):
        return FormatApplicationOptions(**{key: option.checked and option.available for key, option in self.option_items.items()},
                                        lock_copied_ranges=self._lock_ranges)

    def annotation_pairs(self):
        result = {}
        for pair in self.pairs:
            if pair.target_key in self._selected_keys() and pair.currentIndex:
                result.setdefault(pair.target_key, {})[pair.source_id] = pair.target_ids[pair.currentIndex]
        return result

    @Slot(bool)
    def setLockRanges(self, checked):
        self._lock_ranges = checked
        self._selection_changed()

    @Slot(bool)
    def quickOptions(self, ranges):
        self._bulk_depth += 1
        try:
            for key, option in self.option_items.items():
                option.setChecked(key in dict(self._basic) or ranges and key in ('x_limits', 'y_limits', 'color_limits'))
        finally:
            self._bulk_depth -= 1
        self._selection_changed()

    @Slot(int)
    def setPreviewIndex(self, index):
        if 0 <= index < len(self._selected_keys()) and index != self._preview_index:
            self._preview_index = index
            self.changed.emit()
            self.previewChanged.emit()
            self._preview_timer.start()

    @staticmethod
    def _changed_field_label(field, session):
        labels = {'width_in': 'Page width (in)', 'height_in': 'Page height (in)',
                  'frame_width_in': 'Data plot frame width (in)', 'frame_height_in': 'Data plot frame height (in)',
                  'heatmap_dpi': 'Heatmap DPI', 'aspect_locked': 'Page aspect ratio lock',
                  'font_family': 'Font family', 'numeric_pt': 'Numeric font size (pt)',
                  'text_pt': 'Text font size (pt)', 'x_limits': 'Data X range',
                  'y_limits': 'Data Y range', 'color_limits': 'Color limits (V)',
                  'norm': 'Color mapping', 'last_valid_color_limits': 'Stored color limits',
                  'last_valid_region_color_limits': 'Regional color limits', 'title_position': 'Title position'}
        if field in labels:
            return labels[field]
        group, _, name = field.partition('.')
        if group == 'bounds':
            return labels.get(name, name) + ' endpoints'
        if group == 'locks':
            return f"{name[:1].upper()} {'minimum' if name.endswith('min') else 'maximum'} lock"
        if group == 'annotations':
            identifier, _, setting = name.partition('.')
            annotation = next((item for item in session.presentation.annotations if item.id == identifier), None)
            title = annotation.text if annotation and annotation.text else 'Unlabelled annotation'
            return f'“{title}” {"coordinates" if setting == "coordinate_space" else "position"}'
        if field.endswith(('_in', '_pt')):
            field = field[:-3] + f' ({field[-2:]})'
        return field.replace('_', ' ').replace('.', ' · ').capitalize()

    def _selection_changed(self):
        if not hasattr(self, 'option_items') or self._bulk_depth:
            return super()._selection_changed()
        options, pair_map = self.options(), self.annotation_pairs()
        lines, candidates = [], []
        for dataset_id in self._selected_keys():
            session, pairs = self._target_session(dataset_id), pair_map.get(dataset_id, {})
            effective, notice = options, ''
            if options.annotation_positions and not pairs:
                effective = replace(options, annotation_positions=False)
                notice = '\nAnnotation positions skipped: no explicit usable pairs selected.'
            try:
                candidate = detached_graph_session(session)
                result = apply_format(candidate, self.template, options=effective, annotation_pairs=pairs)
                selected = ', '.join(o.label for key, o in self.option_items.items() if getattr(options, key))
                detail = 'Selected: ' + (selected or 'none') + '\nChanged: ' + (
                    ', '.join(self._changed_field_label(field, session) for field in result.changed_fields)
                    or 'Already matches these fields')
                for axis, group, label in (('x', 'x_limits', 'X'), ('y', 'y_limits', 'Y'), ('v', 'color_limits', 'V')):
                    if getattr(effective, group):
                        old_locks = '/'.join('Fixed' if session.view.locks[f'{axis}{end}'] else 'Auto' for end in ('min', 'max'))
                        new_locks = '/'.join('Fixed' if candidate.view.locks[f'{axis}{end}'] else 'Auto' for end in ('min', 'max'))
                        detail += f'\n{label}: {getattr(session.view, group)} → {getattr(candidate.view, group)}; {old_locks} → {new_locks}'
                        if axis == 'v' and self.template.region_color_limits:
                            detail += f'\nRegional V: {session.view.last_valid_region_color_limits} → {candidate.view.last_valid_region_color_limits}'
                candidates.append(candidate)
            except (ValueError, TypeError) as exc:
                detail = 'Cannot apply: ' + str(exc)
                candidates.append(None)
            lines.append(f'{self._target_labels[dataset_id]}\n{detail}{notice}')
        self._preview_text, self._candidates = '\n\n'.join(lines), candidates
        self._preview_index = min(self._preview_index, max(0, len(candidates) - 1))
        self._preview_url, self._preview_error = '', ''
        self.changed.emit()
        self.previewChanged.emit()
        if not self._closed:
            self._preview_timer.start()

    @Slot()
    def render_preview(self):
        if self._closed:
            return
        self._preview_timer.stop()
        self._preview_url, self._preview_error = '', ''
        if self._candidates:
            candidate = self._candidates[self._preview_index]
            if candidate is not None:
                try:
                    _presentation, page = prepare_heatmap_page(snapshot_dataset(candidate), candidate.presentation)
                    self.image_provider.set_svg(page.svg_bytes)
                    self._revision += 1
                    self._preview_url = f'image://format-preview/page-{self._revision}'
                except (ValueError, TypeError, RuntimeError) as exc:
                    self._preview_error = 'Preview unavailable: ' + str(exc)
        self.previewChanged.emit()

    def shutdown(self):
        self._closed = True
        self._preview_timer.stop()


class BatchExportBridge(QObject):
    changed = Signal()
    browseRequested = Signal()

    def __init__(self, sessions, active_id):
        super().__init__()
        self.sessions = {s.dataset_id: s for s in sessions}
        labels = dataset_display_labels(self.sessions.values())
        self.groups = []
        for session in self.sessions.values():
            tooltip = str(session.source_path.resolve()) if session.source_path else 'Untitled dataset'
            children = [TargetChoice(session.dataset_id, graph.graph_id, graph.name, tooltip,
                        session.dataset_id == active_id and graph.graph_id == session.active_graph_id)
                        for graph in session.graphs.values() if graph.published]
            if children:
                group = TargetGroup(labels[session.dataset_id], tooltip, children, self)
                group.changed.connect(self.changed)
                self.groups.append(group)
        self._format_index, self._common_folder, self._folder = 0, False, ''
        self._destination_label = 'Use one output folder (default: beside each DAT)'

    targetGroups = Property('QVariantList', lambda self: self.groups, constant=True)
    selectedCount = Property(int, lambda self: len(self.selected_graph_targets()), notify=changed)
    canAccept = Property(bool, lambda self: bool(self.selected_graph_targets()) and not self._folder_error(), notify=changed)
    formatIndex = Property(int, lambda self: self._format_index, notify=changed)
    commonFolder = Property(bool, lambda self: self._common_folder, notify=changed)
    destinationFolder = Property(str, lambda self: self._folder, notify=changed)
    destinationLabel = Property(str, lambda self: self._destination_label, notify=changed)
    folderError = Property(str, lambda self: self._folder_error(), notify=changed)

    def selected_graph_targets(self):
        return [(c.dataset_id, c.graph_id) for g in self.groups for c in g._children if c.checked]

    def selected_ids(self):
        return list(dict.fromkeys(dataset_id for dataset_id, _ in self.selected_graph_targets()))

    @Slot(bool)
    def selectAll(self, checked):
        for group in self.groups:
            group.setChecked(checked)

    @Slot(int)
    def setFormatIndex(self, index):
        if 0 <= index <= 2:
            self._format_index = index
            self.changed.emit()

    @Slot(bool)
    def setCommonFolder(self, checked):
        self._common_folder = checked
        self.changed.emit()

    @Slot(str)
    def setDestinationFolder(self, folder):
        self._folder = folder
        self.changed.emit()

    def set_destination_label(self, label):
        self._destination_label = label
        self.changed.emit()

    @Slot()
    def browse(self):
        self.browseRequested.emit()

    def _folder_error(self):
        if not self._common_folder:
            return ''
        value = self._folder.strip()
        if not value:
            return 'Choose an existing output folder before exporting.'
        try:
            path = Path(value)
            usable = path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        except (OSError, ValueError):
            usable = False
        return '' if usable else 'Choose an existing, writable output folder.'

    def formats(self):
        return [('svg', 'png'), ('svg',), ('png',)][self._format_index]

    def destination(self):
        return self._folder.strip() if self._common_folder else None



