"""Qt Quick comparison inspector: document-backed rows and owner-scoped drafts."""
from __future__ import annotations

from dataclasses import replace
from base64 import b64encode
from functools import lru_cache
import math
import re

from shiboken6 import isValid

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, QEvent, QTimer, Qt, Signal, Slot

from .curve_palettes import CURVE_PALETTES, palette_colors
from .curve_symbols import SYMBOL_SHAPES, SYMBOL_FILLS, symbol_icon_png
from .plot_presentation import PlotPresentation
from .comparison_labels import comparison_curve_list_labels


@lru_cache(maxsize=32)
def _symbol_preview(shape, fill='full'):
    return 'data:image/png;base64,' + b64encode(symbol_icon_png(shape, fill)).decode('ascii')


class XYCurveModel(QAbstractListModel):
    """Rows read canonical curve objects; no copied curve/style authority."""
    _roles = {Qt.UserRole + i: name.encode() for i, name in enumerate(
        ('curveId', 'caption', 'curveColor', 'shown', 'selected', 'current', 'sourceDescription'), 1)}

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self._ids = []
        self._captions = {}

    def roleNames(self):
        return self._roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._ids)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._ids):
            return None
        cid = self._ids[index.row()]
        owner = self.page.workspace.comparisons.get(self.page.comparison_id)
        curve = owner.curves.get(cid) if owner is not None else None
        if curve is None:
            return None
        name = self._roles.get(role, b'').decode()
        return dict(curveId=cid, caption=self._captions[cid][0], curveColor=curve.color,
                    shown=curve.visible, selected=cid in self.page._selection_ids,
                    current=cid == self.page._active_curve_id,
                    sourceDescription=curve.label + '\n' + self.page._source_text(curve)[1]).get(name)

    def sync(self):
        owner = self.page.workspace.comparisons.get(self.page.comparison_id)
        ids = list(owner.curves) if owner is not None else []
        captions = comparison_curve_list_labels(self.page.workspace, list(owner.curves.values())) if owner is not None else {}
        if ids != self._ids:
            self.beginResetModel()
            self._ids, self._captions = ids, captions
            self.endResetModel()
        else:
            self._captions = captions
            if ids:
                self.dataChanged.emit(self.index(0), self.index(len(ids)-1), list(self._roles))


class XYInspectorBridge(QObject):
    """Only drafts, selection presentation, and session palette choice live here."""
    changed = Signal()
    focusFieldRequested = Signal(str)
    revealCurveRequested = Signal(int)

    _symbol_fields = frozenset(('marker_shape', 'marker_fill', 'marker_size', 'marker_edge_color', 'marker_edge_width'))
    _format_specs = (('width_mm', 'width_in', 25.4, 50.8, 508.),
                     ('height_mm', 'height_in', 25.4, 50.8, 508.),
                     ('numeric_pt', 'numeric_pt', 1., 6., 24.),
                     ('text_pt', 'text_pt', 1., 6., 30.),
                     ('raster_dpi', 'heatmap_dpi', 1., 72., 1200.))
    _labels = dict(width_mm='Width (mm)', height_mm='Height (mm)', numeric_pt='Tick and legend size (pt)',
                   text_pt='Axis and title size (pt)', raster_dpi='PNG resolution (DPI)',
                   width='line width', y_offset='extra Y offset', stack_step='stack step',
                   x_min='X minimum', x_max='X maximum', marker_size='symbol size',
                   marker_edge_width='symbol outline width')

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self._disposed = False
        self._model = XYCurveModel(page, self)
        self._drafts = {}
        self._message = ''
        self._highlight = True
        self._symbol_target = 'current'
        self._palette = next(iter(CURVE_PALETTES))
        self._imported_palettes = {}
        self._imported_names = {}
        self._anchor = None
        self._error_field = ''
        self._blur_epoch = 0
        self._panels = {}
        self._panel_serial = 0
        page.destroyed.connect(self.detach_panels)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.EnabledChange, QEvent.Show, QEvent.Hide):
            self.changed.emit()
        return False

    def is_live(self):
        return (not self._disposed and isValid(self.page)
                and self.page.comparison_id in self.page.workspace.comparisons)

    curves = Property(QObject, lambda self: self._model, constant=True)
    enabled = Property(bool, lambda self: self.is_live() and self.page.presentation_eligible(), notify=changed)
    @Property(str, notify=changed)
    def inputOwner(self):
        if not self.is_live():
            return ''
        selected = tuple((cid, id(self.page.comparison.curves[cid])) for cid in sorted(self.page._selected_ids()))
        return f'{self.page.command_bridge.ownerToken}|inspector:{self._blur_epoch}|selection:{self.page._active_curve_id}:{selected!r}'

    def _authorize(self, owner=None):
        current = self.inputOwner
        if owner is not None and owner != current:
            return False
        command_owner = self.page.command_bridge.ownerToken
        return self.page.command_bridge.authorize(command_owner) and self.inputOwner == current
    graphName = Property(str, lambda self: self.page.comparison.name if self.is_live() else '', notify=changed)
    currentId = Property(str, lambda self: (self.page._active_curve_id or '') if self.is_live() else '', notify=changed)
    currentRow = Property(int, lambda self: list(self.page.comparison.curves).index(self.page._active_curve_id)
                          if self.is_live() and self.page._active_curve_id in self.page.comparison.curves else -1, notify=changed)
    currentLabel = Property(str, lambda self: self.page._selected().label if self.is_live() and self.page._selected() else 'No current curve', notify=changed)
    selectedCount = Property(int, lambda self: len(self.page._selection_ids) if self.is_live() else 0, notify=changed)
    curveCount = Property(int, lambda self: len(self.page.comparison.curves) if self.is_live() else 0, notify=changed)
    hasCurrent = Property(bool, lambda self: self.is_live() and self.page._selected() is not None, notify=changed)
    highlight = Property(bool, lambda self: self._highlight, notify=changed)
    symbolTarget = Property(str, lambda self: self._symbol_target, notify=changed)
    symbolPending = Property(bool, lambda self: bool(self._symbol_fields & self._drafts.keys()), notify=changed)
    paletteKey = Property(str, lambda self: self._palette, notify=changed)
    palettes = Property('QVariantList', lambda self: [dict(key=k, label=k, swatches=palette_colors(k, 10)) for k in CURVE_PALETTES] +
                        [dict(key=k, label=self._imported_names[k], swatches=self._imported_palettes[k][:10]) for k in self._imported_palettes], notify=changed)
    shapes = Property('QVariantList', lambda self: [dict(key=k, label=v, icon=_symbol_preview(k)) for k, v in SYMBOL_SHAPES], constant=True)
    fills = Property('QVariantList', lambda self: [dict(key=k, label=v, icon=_symbol_preview('o', k)) for k, v in SYMBOL_FILLS], constant=True)
    values = Property('QVariantMap', lambda self: self.values_map(), notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    source = Property(str, lambda self: self.page._source_text(self.page._selected())[1] if self.is_live() and self.page._selected() else '', notify=changed)
    displayStatus = Property(str, lambda self: getattr(self.page, '_display_status', '') if self.is_live() else '', notify=changed)
    hasDraft = Property(bool, lambda self: bool(self._drafts), notify=changed)
    error = Property(str, lambda self: self.validation_error(), notify=changed)
    errorField = Property(str, lambda self: self._error_field, notify=changed)

    def canonical_values(self):
        if not self.is_live():
            return dict(name='', plot_title='', x_label='', y_label='', legend_visible=False,
                        physical=False, normalization='none', x_range_mode='all', stack_step='0.0',
                        x_min='', x_max='', width_mm='', height_mm='', numeric_pt='', text_pt='',
                        raster_dpi='', label='', color='', width='1.5', y_offset='0.0',
                        marker_shape='ball', marker_fill='full', marker_size='5.0',
                        marker_edge_color='auto', marker_edge_width='1.0')
        c, curve = self.page.comparison, self.page._selected()
        p = c.presentation or PlotPresentation()
        values = dict(name=c.name, plot_title=c.plot_title, x_label=c.x_label, y_label=c.y_label,
                      legend_visible=c.legend_visible, physical=c.presentation is not None,
                      normalization=c.normalization, x_range_mode=c.x_range_mode,
                      stack_step=repr(float(c.stack_step)), x_min=repr(float(c.x_range[0])) if c.x_range else '',
                      x_max=repr(float(c.x_range[1])) if c.x_range else '')
        for field, attr, factor, _low, _high in self._format_specs:
            values[field] = repr(float(getattr(p, attr))*factor) if field != 'raster_dpi' else str(p.heatmap_dpi)
        values.update(label=curve.label if curve else '', color=curve.color if curve else '',
                      width=repr(float(curve.width)) if curve else '1.5',
                      y_offset=repr(float(curve.y_offset)) if curve else '0.0',
                      marker_shape=curve.marker_shape if curve else 'ball', marker_fill=curve.marker_fill if curve else 'full',
                      marker_size=repr(float(curve.marker_size)) if curve else '5.0',
                      marker_edge_color=curve.marker_edge_color if curve else 'auto',
                      marker_edge_width=repr(float(curve.marker_edge_width)) if curve else '1.0')
        return values

    def values_map(self):
        return self.canonical_values() | (self._drafts if self.is_live() else {})

    def dispose(self):
        self.detach_panels()
        self._disposed = True
        self._blur_epoch += 1
        self._drafts.clear()
        self._model.beginResetModel()
        self._model._ids, self._model._captions = [], {}
        self._model.endResetModel()
        self.changed.emit()

    def sync(self):
        self._model.sync()
        self.changed.emit()

    def set_message(self, text):
        self._message = text
        self.changed.emit()

    @Slot(str, 'QVariant')
    def setDraft(self, field, value):
        if not self.enabled or not self._authorize() or field not in self.canonical_values():
            return
        if value == self.canonical_values()[field]:
            self._drafts.pop(field, None)
        else:
            self._drafts[field] = value
        self._message = ''
        self.changed.emit()
        self.page._draft_changed()

    @Slot(str, result=bool)
    def commitField(self, field=''):
        if not self.enabled or not self._authorize():
            return False
        try:
            self.page._commit_editor(include_symbols=False)
            return True
        except ValueError as exc:
            self.set_message(str(exc))
            return False

    def bind_panel(self, section, item, *, hybrid_report=None):
        previous = self._panels.get(section)
        if previous is not None:
            self._disconnect_panel(previous)
        self._panel_serial += 1
        generation = self._panel_serial
        connections = []
        self._panels[section] = (item, generation, hybrid_report, connections)
        self.suspendBlur()
        callback = lambda: self._panel_destroyed(section, generation)
        item.destroyed.connect(callback)
        connections.append((item.destroyed, callback))
        for signal in (item.visibleChanged, item.enabledChanged, item.windowChanged):
            signal.connect(self.suspendBlur)
            connections.append((signal, self.suspendBlur))

    @staticmethod
    def _disconnect_panel(panel):
        for signal, callback in panel[3]:
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        panel[3].clear()

    def detach_panels(self):
        for panel in self._panels.values():
            self._disconnect_panel(panel)
        self._panels.clear()
        self.suspendBlur()

    @Slot(int, QObject)
    def bindPanel(self, section, item):
        self.bind_panel(section, item)

    @Slot(int, QObject)
    def detachPanel(self, section, item):
        current = self._panels.get(section)
        if current is not None and current[0] == item:
            self._disconnect_panel(current)
            self._panels.pop(section, None)
            self.suspendBlur()

    def _panel_destroyed(self, section, generation):
        current = self._panels.get(section)
        if current is not None and current[1] == generation:
            self._panels.pop(section, None)
            self.suspendBlur()

    def _panel_focused(self, section):
        current = self._panels.get(section)
        if current is None:
            return False
        item, _generation, report, _connections = current
        if not isValid(item) or not item.isVisible() or not item.isEnabled():
            return False
        if report is not None and not all(report()):
            return False
        window = item.window()
        if window is not None:
            # window() can parent its wrapper to this disposable panel. Keep
            # the wrapper with the window's actual QObject owner instead.
            QObject.setParent(window, QObject.parent(window))
        focus = window.activeFocusItem() if window is not None else None
        while focus is not None:
            if focus is item:
                return True
            focus = focus.parentItem()
        return False

    @Slot(str, result=bool)
    def authorizeOwner(self, owner):
        return self._authorize(owner)

    @Slot(str, result=bool)
    def revertFor(self, owner):
        if not self._authorize(owner):
            return False
        self.page.revert_editor()
        return True

    @Slot(str, 'QVariant', str, result=bool)
    def draftFor(self, field, value, owner):
        if not self._authorize(owner):
            return False
        self.setDraft(field, value)
        return True

    @Slot(str, str, result=bool)
    def commitFor(self, field, owner):
        return self._authorize(owner) and self.commitField(field)

    @Slot(str, str)
    def blurFor(self, field, owner):
        section = 0 if field in ('label', 'color', 'width', 'y_offset') or field in self._symbol_fields else 2
        epoch = self._blur_epoch
        QTimer.singleShot(0, self, lambda: self.commitFor(field, owner)
            if epoch == self._blur_epoch and self._panel_focused(section) else None)

    @Slot(str)
    def blurField(self, field):
        self.blurFor(field, self.inputOwner)

    @Slot()
    def suspendBlur(self):
        self._blur_epoch += 1

    @Slot()
    def revert(self):
        if self.enabled and self._authorize():
            self.page.revert_editor()

    @staticmethod
    def _finite(value, label):
        try:
            result = float(value)
        except (ValueError, TypeError):
            raise ValueError(f'Enter a finite number for {label}.') from None
        if not math.isfinite(result):
            raise ValueError(f'Enter a finite number for {label}.')
        return result

    def parsed_values(self):
        v = self.values_map()
        self._error_field = 'name'
        if not v['name'].strip() or '\x00' in v['name']:
            raise ValueError('Enter a nonempty graph name.')
        for field in ('plot_title', 'x_label', 'y_label', 'label'):
            self._error_field = field
            if '\x00' in v[field]:
                raise ValueError(f'{field.replace("_", " ").capitalize()} cannot contain null characters.')
        for field in ('stack_step', 'width', 'y_offset', 'marker_size', 'marker_edge_width'):
            self._error_field = field
            v[field] = self._finite(v[field], self._labels[field])
        if self.page._selected() is not None:
            self._error_field = 'color'
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', v['color'].strip()):
                raise ValueError('Use a six-digit color such as #1f77b4.')
            self._error_field = 'width'
            if v['width'] <= 0:
                raise ValueError('Line width must be positive.')
            self._error_field = 'marker_size'
            if v['marker_size'] <= 0:
                raise ValueError('Symbol size must be positive.')
            self._error_field = 'marker_edge_width'
            if v['marker_edge_width'] < 0:
                raise ValueError('Symbol outline width cannot be negative.')
            self._error_field = 'marker_edge_color'
            if v['marker_edge_color'] != 'auto' and not re.fullmatch(r'#[0-9a-fA-F]{6}', v['marker_edge_color'].strip()):
                raise ValueError('Symbol outline must be auto or a six-digit hex color.')
            if v['marker_shape'] not in dict(SYMBOL_SHAPES) or v['marker_fill'] not in dict(SYMBOL_FILLS):
                raise ValueError('Choose a supported symbol shape and fill.')
        if self.symbolPending and self._symbol_target == 'selected' and not self.page._selection_ids:
            raise ValueError('Select at least one curve for the pending symbol settings.')
        if v['normalization'] not in ('none', 'maxabs', 'minmax') or v['x_range_mode'] not in ('all', 'common', 'manual'):
            raise ValueError('Choose a supported comparison display mode.')
        v['x_range'] = self.page.comparison.x_range
        if v['x_range_mode'] == 'manual':
            self._error_field = 'x_min'
            low = self._finite(v['x_min'], 'X minimum')
            self._error_field = 'x_max'
            high = self._finite(v['x_max'], 'X maximum')
            if low >= high:
                raise ValueError('X minimum must be less than X maximum.')
            v['x_range'] = (low, high)
        v['presentation'] = None
        if v['physical']:
            p = self.page.comparison.presentation or PlotPresentation()
            changes = {}
            for field, attr, factor, low, high in self._format_specs:
                if field not in self._drafts:
                    continue  # imported precision and frame dimensions remain exact
                self._error_field = field
                number = self._finite(v[field], self._labels[field])
                if not low <= number <= high or field == 'raster_dpi' and not number.is_integer():
                    raise ValueError(f'{self._labels[field]} must be between {low:g} and {high:g}' +
                                     (' and a whole number.' if field == 'raster_dpi' else '.'))
                changes[attr] = int(number) if field == 'raster_dpi' else number/factor
            for dimension in ('width', 'height'):
                attr, frame = f'{dimension}_in', f'frame_{dimension}_in'
                if attr in changes and getattr(p, frame) is not None:
                    changes[frame] = getattr(p, frame)*changes[attr]/getattr(p, attr)
            v['presentation'] = replace(p, **changes) if changes else p
        self._error_field = ''
        return v

    def validation_error(self):
        if not self.is_live():
            return ''
        try:
            self.parsed_values()
        except ValueError as exc:
            return str(exc)
        return ''

    def reveal_curve(self, cid):
        if cid in self.page.comparison.curves:
            self.revealCurveRequested.emit(list(self.page.comparison.curves).index(cid))

    @Slot(int, result=str)
    def curveIdAt(self, row):
        if not self.is_live():
            return ''
        ids = list(self.page.comparison.curves)
        return ids[row] if 0 <= row < len(ids) else ''

    @Slot(str, int)
    def selectCurve(self, cid, modifiers=0):
        if not self.enabled or not self._authorize() or cid not in self.page.comparison.curves:
            return
        control, shift = bool(modifiers & Qt.ControlModifier.value), bool(modifiers & Qt.ShiftModifier.value)
        ids = list(self.page.comparison.curves)
        selected = set(self.page._selection_ids) if control or shift else set()
        if shift and self._anchor in ids:
            start, stop = sorted((ids.index(self._anchor), ids.index(cid)))
            selected.update(ids[start:stop+1])
        elif control:
            selected.symmetric_difference_update({cid})
        else:
            selected.add(cid)
        if self.page.set_curve_selection(selected, cid):
            if not shift:
                self._anchor = cid

    @Slot(bool)
    def selectAll(self, selected):
        if self.enabled and self._authorize():
            self.page.set_curve_selection(set(self.page.comparison.curves) if selected else set(), self.page._active_curve_id)

    @Slot(str, bool)
    def setVisibility(self, cid, shown):
        if self.enabled and self._authorize():
            self.page.set_curve_visibility(cid, shown)

    @Slot(bool)
    def setHighlight(self, checked):
        if not self.enabled or not self._authorize():
            return
        self._highlight = checked
        self.page._apply_selection_highlight()
        self.changed.emit()

    @Slot(str)
    def setSymbolTarget(self, target):
        if self.enabled and self._authorize() and target in ('current', 'selected'):
            self._symbol_target = target
            self.changed.emit()

    @Slot(str)
    def setPalette(self, key):
        if self.enabled and self._authorize() and (key in CURVE_PALETTES or key in self._imported_palettes):
            self._palette = key
            self.changed.emit()

    @Slot(str)
    def setRangeMode(self, mode):
        if not self.enabled or not self._authorize():
            return
        if mode == 'manual' and not self.values_map()['x_min'] and not self.values_map()['x_max']:
            bounds = self.page.axes._comparison_display.x_limits or tuple(self.page.axes.dataLim.intervalx)
            if not all(math.isfinite(v) for v in bounds) or bounds[0] >= bounds[1]:
                bounds = (0., 1.)
            self.setDraft('x_min', repr(float(bounds[0])))
            self.setDraft('x_max', repr(float(bounds[1])))
        self.setDraft('x_range_mode', mode)
        self.commitField('x_range_mode')

    @Slot(int)
    def moveSelected(self, direction):
        if self.enabled and self._authorize():
            self.page._move_selected(direction)

    @Slot(int)
    def moveTo(self, destination):
        if self.enabled and self._authorize():
            self.page._move_curves(self.page._selection_ids, destination)

    @Slot(str)
    def applyStyle(self, style):
        if self.enabled and self._authorize() and style in ('line', 'scatter', 'line_symbol'):
            self.page._set_selected_style(style)

    @Slot(str)
    def visibilityCommand(self, mode):
        if self.enabled and self._authorize():
            self.page._batch_visibility(mode)

    @Slot(str)
    def action(self, action):
        if not self.enabled or not self._authorize():
            return
        methods = dict(color=self.page._choose_color, batchColor=self.page._batch_color,
                       batchWidth=self.page._batch_width, palette=self.page._apply_palette,
                       importPalette=self.page._import_palette_dialog, symbols=self.page._apply_symbols,
                       source=self.page._open_source, remove=self.page._remove_selected,
                       reset=self.page._reset_display,
                       svg=lambda: self.page._export_dialog('svg'), png=lambda: self.page._export_dialog('png'))
        if action in methods:
            methods[action]()
