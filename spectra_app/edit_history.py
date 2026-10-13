"""Bounded in-memory edits of ordinary dataset fields and small peak records.

Commands contain only changed fields. Cubes, fits, processing recipes, analysis
controls and navigation are deliberately outside this history's capture API.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass, replace


COLOR_FIELDS = tuple('view.' + name for name in (
    'bounds', 'locks', 'color_limits', 'last_valid_color_limits',
    'last_valid_region_color_limits'))
RANGE_FIELDS = COLOR_FIELDS + ('view.x_limits', 'view.y_limits')
STYLE_FIELDS = RANGE_FIELDS + tuple('view.' + name for name in (
    'colormap', 'norm', 'color_scale_mode', 'color_region_boundaries'))
TITLE_FIELDS = ('view.annotations.plot_title', 'view.title_settings')


@dataclass(frozen=True)
class FieldChange:
    path: tuple
    before: object
    after: object
    before_exists: bool = True
    after_exists: bool = True


def _changes(before, after, path):
    if is_dataclass(before) and type(before) is type(after):
        return [change for field in fields(before) for change in
                _changes(getattr(before, field.name), getattr(after, field.name), (*path, field.name))]
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in before.keys() | after.keys():
            if key in before and key in after:
                result.extend(_changes(before[key], after[key], (*path, key)))
            else:
                result.append(FieldChange((*path, key), before.get(key), after.get(key),
                                          key in before, key in after))
        return result
    return [] if before == after else [FieldChange(path, deepcopy(before), deepcopy(after))]


def capture_fields(session, names):
    result = {}
    for name in names:
        path = tuple(name.split('.'))
        # Only this finite ordinary-state surface can enter a visual command.
        if path[0] != 'presentation' and (path[0] != 'view' or path[1] == 'gate'):
            raise ValueError('History can capture only presentation and non-navigation view fields.')
        value = session
        for key in path:
            value = value[key] if isinstance(value, dict) else getattr(value, key)
        result[path] = deepcopy(value)
    return result


def field_changes(before, after):
    return tuple(change for path, value in before.items() for change in _changes(value, after[path], path))


def _restore_tree(target, tree):
    """Replace coupled immutable fields together, validating the final record."""
    updates, deleted = {}, set()
    for key, node in tree.items():
        if isinstance(node, dict):
            current = target.get(key) if isinstance(target, dict) else getattr(target, key)
            updates[key] = _restore_tree(current, node)
        else:
            value, exists = node
            if exists:
                updates[key] = deepcopy(value)
            else:
                deleted.add(key)
    if isinstance(target, dict):
        updated_target = dict(target)
        updated_target.update(updates)
        for key in deleted:
            updated_target.pop(key, None)
        return updated_target
    if is_dataclass(target) and target.__dataclass_params__.frozen:
        return replace(target, **updates)
    for key, value in updates.items():
        setattr(target, key, value)
    return target


def restore_fields(session, changes, *, redo=False):
    tree = {}
    for change in changes:
        branch = tree
        for key in change.path[:-1]:
            branch = branch.setdefault(key, {})
        branch[change.path[-1]] = (change.after if redo else change.before,
                                  change.after_exists if redo else change.before_exists)
    _restore_tree(session, tree)


@dataclass(frozen=True)
class GatePeaks:
    """A gate's small seed maps, excluding all fit arrays and scientific data."""
    batch: tuple[int, ...] | None
    batch_kinds: dict[int, bool]
    labels: dict[int, int]
    trace: tuple[int, ...] | None
    trace_kinds: dict[int, bool]


def capture_peaks(session, gates):
    return {gate: GatePeaks(
        tuple(map(int, session.batch_peak_map[gate])) if gate in session.batch_peak_map else None,
        {int(ei): kind for (gi, ei), kind in session.batch_peak_kind_lookup.items() if gi == gate},
        {int(ei): label for (gi, ei), label in session.peak_label_lookup.items() if gi == gate},
        tuple(map(int, session.peak_indices)) if session.peak_gate_idx == gate else None,
        dict(session.peak_kind_lookup) if session.peak_gate_idx == gate else {}) for gate in gates}


@dataclass(frozen=True)
class EditCommand:
    label: str
    changes: tuple[FieldChange, ...] = ()
    peak_before: dict[int, GatePeaks] | None = None
    peak_after: dict[int, GatePeaks] | None = None
    graph_id: str | None = None


class EditHistory:
    def __init__(self, limit=100):
        self.limit = limit
        self.undo_stack = []
        self.redo_stack = []

    def record(self, command):
        if not command.changes and not command.peak_before:
            return False
        self.undo_stack.append(command)
        del self.undo_stack[:-self.limit]
        self.redo_stack.clear()
        return True

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()

    def remove_graph(self, graph_id):
        self.undo_stack[:] = [command for command in self.undo_stack if command.graph_id != graph_id]
        self.redo_stack[:] = [command for command in self.redo_stack if command.graph_id != graph_id]
