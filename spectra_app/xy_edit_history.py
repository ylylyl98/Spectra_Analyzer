"""Session-only comparison edits containing scalars and stable Curve references.

Scientific arrays, Sheet ownership and analysis payloads are never copied here.
Commands validate their changed fields against the current document so external
source deletion/reconciliation cannot resurrect stale curves.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .workbook_state import resolve_curve


GRAPH_FIELDS = ('name', 'plot_title', 'x_label', 'y_label', 'legend_visible',
                'normalization', 'stack_step', 'x_range_mode', 'x_range')
CURVE_FIELDS = ('label', 'visible', 'color', 'width', 'y_offset', 'style')


def capture_edit_state(comparison):
    return (tuple(getattr(comparison, name) for name in GRAPH_FIELDS),
            tuple((curve_id, curve, tuple(getattr(curve, name) for name in CURVE_FIELDS))
                  for curve_id, curve in comparison.curves.items()))


@dataclass
class XYEditCommand:
    label: str
    graph_changes: tuple
    curve_changes: tuple
    before_order: tuple
    after_order: tuple
    removed: dict = field(default_factory=dict)
    added: dict = field(default_factory=dict)
    before_selection: tuple = (None, ())
    after_selection: tuple = (None, ())
    retained_analysis: object = None

    @property
    def structural(self):
        return self.before_order != self.after_order

    @property
    def appearance_only(self):
        return (not self.structural
                and all(name not in ('normalization', 'stack_step', 'x_range_mode', 'x_range')
                        for name, _before, _after in self.graph_changes)
                and all(name not in ('visible', 'y_offset') for _cid, name, _before, _after in self.curve_changes))

    def valid(self, workspace, comparison, *, redo=False):
        current = 0 if redo else 1
        if self.structural and tuple(comparison.curves) != (self.before_order if redo else self.after_order):
            return False
        for name, before, after in self.graph_changes:
            if getattr(comparison, name) != (before, after)[current]:
                return False
        for curve_id, name, before, after in self.curve_changes:
            curve = comparison.curves.get(curve_id)
            if curve is None or getattr(curve, name) != (before, after)[current]:
                return False
        for curve in (self.added if redo else self.removed).values():
            try:
                resolve_curve(workspace, curve)
            except (ValueError, KeyError, IndexError):
                return False
        return True

    def apply(self, comparison, *, redo=False):
        for name, before, after in self.graph_changes:
            setattr(comparison, name, after if redo else before)
        for curve_id, name, before, after in self.curve_changes:
            setattr(comparison.curves[curve_id], name, after if redo else before)
        if self.structural:
            curves = dict(comparison.curves)
            for curve_id in (self.removed if redo else self.added):
                curves.pop(curve_id, None)
            curves.update(self.added if redo else self.removed)
            comparison.curves = {curve_id: curves[curve_id]
                                 for curve_id in (self.after_order if redo else self.before_order)}


class XYEditHistory:
    def __init__(self, limit=100):
        self.limit = limit
        self.undo_stack, self.redo_stack = [], []

    def __deepcopy__(self, memo):
        # Detached save/clone snapshots must not duplicate runtime histories or
        # retain their source/fit arrays. Live document history is untouched.
        result = type(self)(self.limit)
        memo[id(self)] = result
        return result

    def record(self, label, before, after, *, before_selection, after_selection, retained_analysis=None):
        graph_before, curves_before = before
        graph_after, curves_after = after
        graph_changes = tuple((name, a, b) for name, a, b in zip(GRAPH_FIELDS, graph_before, graph_after) if a != b)
        old = {cid: (curve, values) for cid, curve, values in curves_before}
        new = {cid: (curve, values) for cid, curve, values in curves_after}
        curve_changes = tuple((cid, name, a, b) for cid in old.keys() & new.keys()
                              for name, a, b in zip(CURVE_FIELDS, old[cid][1], new[cid][1]) if a != b)
        before_order, after_order = tuple(old), tuple(new)
        if not graph_changes and not curve_changes and before_order == after_order:
            return False
        self.undo_stack.append(XYEditCommand(label, graph_changes, curve_changes, before_order, after_order,
            {cid: old[cid][0] for cid in old.keys() - new.keys()},
            {cid: new[cid][0] for cid in new.keys() - old.keys()},
            before_selection, after_selection, retained_analysis))
        del self.undo_stack[:-self.limit]
        self.redo_stack.clear()
        return True

    def available(self, workspace, comparison, *, redo=False):
        stack = self.redo_stack if redo else self.undo_stack
        while stack and not stack[-1].valid(workspace, comparison, redo=redo):
            stack.pop()
        return bool(stack)
