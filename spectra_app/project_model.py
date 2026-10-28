"""Keyed Project presentation; canonical data and actions stay in MainWindow."""
from __future__ import annotations

from dataclasses import dataclass
import json

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Qt, Signal, Slot


def row_key(key):
    return json.dumps(key, ensure_ascii=False, separators=(',', ':')) if key is not None else ''


@dataclass(frozen=True)
class ProjectRow:
    key: tuple
    label: str
    parent: tuple | None = None
    tooltip: str = ''
    description: str = ''


class ProjectListModel(QAbstractListModel):
    _names = ('rowKey', 'label', 'tooltip', 'description', 'depth', 'kind', 'hasChildren',
              'expanded', 'selected', 'current', 'active', 'sectionStart')
    _roles = {Qt.UserRole + i + 1: name.encode() for i, name in enumerate(_names)}

    def __init__(self, parent):
        super().__init__(parent)
        self.rows = []

    def roleNames(self):
        return self._roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        if role == Qt.DisplayRole:
            return self.rows[index.row()]['label']
        name = self._roles.get(role)
        return self.rows[index.row()].get(name.decode()) if name else None

    def update_rows(self, rows):
        if [row['rowKey'] for row in rows] != [row['rowKey'] for row in self.rows]:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()
            return
        previous, self.rows = self.rows, rows
        for index, (before, after) in enumerate(zip(previous, rows)):
            roles = [role for role, name in self._roles.items()
                     if before[name.decode()] != after[name.decode()]]
            if roles:
                self.dataChanged.emit(self.index(index), self.index(index), roles)


class ProjectBridge(QObject):
    stateChanged = Signal()
    revealRequested = Signal(int)

    def __init__(self, window, parent):
        super().__init__(parent)
        self._window = window
        self._model = ProjectListModel(self)
        self._rows = {}
        self._children = {}
        self._expanded = {}
        self._search = ''
        self._search_expanded = None
        self._selected = set()
        self._current = None
        self._anchor = None
        self._active = set()
        self._context_key = None
        self._actions = []
        self._context_actions = []
        self._import_action = None

    model = Property(QObject, lambda self: self._model, constant=True)
    searchText = Property(str, lambda self: self._search, notify=stateChanged)
    enabled = Property(bool, lambda self: not self._window._project_busy()
                       and not self._window._close_pending, notify=stateChanged)
    currentKey = Property(str, lambda self: row_key(self._current), notify=stateChanged)
    selectedKeys = Property('QStringList', lambda self: [row_key(key) for key in self.selected_keys()],
                            notify=stateChanged)
    currentIndex = Property(int, lambda self: self.visible_index(self._current), notify=stateChanged)
    actions = Property('QVariantList', lambda self: self._actions, notify=stateChanged)
    contextActions = Property('QVariantList', lambda self: self._context_actions, notify=stateChanged)
    importAction = Property(QObject, lambda self: self._import_action, notify=stateChanged)
    contextIsGroup = Property(bool, lambda self: bool(self._context_key and self._context_key[0] == 'group'),
                              notify=stateChanged)
    contextExpanded = Property(bool, lambda self: self.is_expanded(self._context_key), notify=stateChanged)

    def bind_actions(self, import_action, actions, context_actions):
        self._import_action = import_action
        self._actions = actions
        self._context_actions = context_actions
        self.stateChanged.emit()

    def all_rows(self):
        return list(self._rows.values())

    def row_for_key(self, key):
        return self._rows.get(key)

    def children(self, key=None):
        return [self._rows[child] for child in self._children.get(key, [])]

    def current_key(self):
        return self._current

    def selected_keys(self):
        return [key for key in self._rows if key in self._selected]

    def visible_keys(self):
        return [self._decode(row['rowKey']) for row in self._model.rows]

    def visible_index(self, key):
        encoded = row_key(key)
        return next((i for i, row in enumerate(self._model.rows) if row['rowKey'] == encoded), -1)

    def is_expanded(self, key):
        return self._expanded.get(key, True)

    def set_rows(self, rows):
        self._rows = {row.key: row for row in rows}
        self._children = {}
        for row in rows:
            self._children.setdefault(row.parent, []).append(row.key)
        self._selected.intersection_update(self._rows)
        if self._current not in self._rows:
            self._current = None
        if self._anchor not in self._rows:
            self._anchor = None
        if self._context_key not in self._rows:
            self._context_key = None
        self._expanded = {key: value for key, value in self._expanded.items() if key in self._rows}
        self._publish(expand_matches=True)

    def set_active(self, keys):
        if set(keys) != self._active:
            self._active = set(keys)
            self._publish()

    def _publish(self, *, expand_matches=False):
        query = self._search.strip().casefold()
        included = set()
        forced = set()
        if query:
            def include(key, ancestor_match=False):
                row = self._rows[key]
                matches = query in (row.label + '\n' + row.tooltip).casefold()
                descendants = False
                for child in self._children.get(key, []):
                    descendants |= include(child, ancestor_match or matches)
                if matches or ancestor_match or descendants:
                    included.add(key)
                if descendants:
                    forced.add(key)
                return key in included
            for key in self._children.get(None, []):
                include(key)
            if expand_matches:
                for key in forced:
                    self._expanded[key] = True
        output = []
        def append(key, depth):
            if query and key not in included:
                return
            row = self._rows[key]
            children = bool(self._children.get(key))
            expanded = self.is_expanded(key)
            active = key in self._active
            output.append(dict(rowKey=row_key(key), label=row.label,
                tooltip=row.tooltip + ('\nActive document' if active else ''),
                description=row.description + (' · Active document' if active else ''),
                depth=depth, kind=key[0], hasChildren=children, expanded=expanded,
                selected=key in self._selected, current=key == self._current, active=active,
                sectionStart=depth == 0 and bool(output)))
            if children and expanded:
                for child in self._children[key]:
                    append(child, depth + 1)
        for key in self._children.get(None, []):
            append(key, 0)
        self._model.update_rows(output)
        self.stateChanged.emit()

    @staticmethod
    def _decode(encoded):
        def tuples(value):
            return tuple(tuples(item) for item in value) if isinstance(value, list) else value
        try:
            result = tuples(json.loads(encoded))
            return result if isinstance(result, tuple) else None
        except (ValueError, TypeError):
            return None

    def _selection_changed(self, reveal=False):
        self._publish()
        self._window._navigator_selection_changed()
        if reveal and self.currentIndex >= 0:
            self.revealRequested.emit(self.currentIndex)

    def select_key(self, key, modifiers=0, context=False, reveal=False):
        if key not in self._rows:
            return
        modifiers = int(modifiers.value) if hasattr(modifiers, 'value') else int(modifiers)
        control = bool(modifiers & Qt.ControlModifier.value)
        shift = bool(modifiers & Qt.ShiftModifier.value)
        if context and key in self._selected:
            self._current = key
        elif shift and self._anchor in self.visible_keys() and key in self.visible_keys():
            visible = self.visible_keys()
            start, end = sorted((visible.index(self._anchor), visible.index(key)))
            self._selected = (self._selected if control else set()) | set(visible[start:end + 1])
            self._current = key
        else:
            if control and not context:
                if key in self._selected:
                    self._selected.remove(key)
                else:
                    self._selected.add(key)
            else:
                self._selected = {key}
            self._current = self._anchor = key
        self._selection_changed(reveal)

    @Slot(str, int)
    @Slot(str, int, bool)
    def select(self, key, modifiers=0, contextClick=False):
        self.select_key(self._decode(key), modifiers, contextClick)

    @Slot()
    def clearSelection(self):
        self._selected.clear()
        self._selection_changed()

    @Slot(str)
    def setSearchText(self, text):
        if text == self._search:
            return
        if text.strip() and not self._search.strip():
            self._search_expanded = dict(self._expanded)
        elif not text.strip() and self._search_expanded is not None:
            self._expanded = self._search_expanded
            self._search_expanded = None
        self._search = text
        self._publish(expand_matches=True)

    def set_expanded(self, key, expanded):
        if key in self._rows and self._children.get(key):
            self._expanded[key] = bool(expanded)
            self._publish()

    @Slot(str)
    def toggleExpanded(self, encoded):
        key = self._decode(encoded)
        self.set_expanded(key, not self.is_expanded(key))

    @Slot(str)
    def activate(self, encoded):
        if not self.enabled:
            return
        key = self._decode(encoded)
        if key not in self._rows:
            return
        if key != self._current:
            self.select_key(key)
        self._window._open_navigator_item()

    @Slot(str)
    def prepareContext(self, encoded):
        key = self._decode(encoded)
        if key not in self._rows:
            return
        self.select_key(key, context=True)
        self._context_key = key
        self.stateChanged.emit()

    @Slot()
    def toggleContextGroup(self):
        self.set_expanded(self._context_key, not self.is_expanded(self._context_key))

    @Slot(str, int)
    def navigate(self, command, modifiers=0):
        visible = self.visible_keys()
        if not visible:
            return
        index = self.currentIndex
        if command == 'enter':
            self.activate(self.currentKey)
            return
        if command == 'right' and self._current in self._rows:
            if self._children.get(self._current):
                if not self.is_expanded(self._current):
                    self.set_expanded(self._current, True)
                    return
                key = next((child for child in self._children[self._current]
                            if child in visible), None)
                if key is None:
                    return
            else:
                return
        elif command == 'left' and self._current in self._rows:
            if self._children.get(self._current) and self.is_expanded(self._current):
                self.set_expanded(self._current, False)
                return
            key = self._rows[self._current].parent
            if key is None:
                return
        else:
            target = {'home': 0, 'end': len(visible)-1,
                      'up': max(0, index-1), 'down': min(len(visible)-1, index+1)}.get(command)
            if target is None:
                return
            key = visible[target]
        value = int(modifiers.value) if hasattr(modifiers, 'value') else int(modifiers)
        if value & Qt.ControlModifier.value and not value & Qt.ShiftModifier.value:
            self._current = key
            self._selection_changed(reveal=True)
        else:
            self.select_key(key, modifiers, reveal=True)

    def sync(self):
        self.stateChanged.emit()
