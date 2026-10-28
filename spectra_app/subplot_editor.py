"""Live Qt Quick subplot spacing drafts bound to one navigation owner."""
import math

from PySide6.QtCore import QObject, Property, Signal, Slot
from shiboken6 import isValid


class SubplotEditorBridge(QObject):
    changed = Signal()
    retired = Signal()
    KEYS = ('top', 'bottom', 'left', 'right', 'hspace', 'wspace')

    def __init__(self, navigation):
        super().__init__()
        self._navigation = navigation
        self._owner = navigation.ownerToken
        self._canvas = navigation.toolbar.canvas
        self._figure = self._canvas.figure
        self._axes = tuple(self._figure.axes)
        self._defaults = self._values()
        self._drafts = {}
        self._errors = {}
        self._error = self._export = ''
        self._closed = False
        self._notice = ('' if any(ax.get_subplotspec() is not None for ax in self._axes)
                        else 'This figure has no subplot-managed axes. These values do not '
                        'move its plots; use Properties to change physical plot geometry. '
                        'Tight layout has no compatible subplot group.')
        self._read_actual()

    fields = Property('QVariantList', lambda s: [
        {'key': key, 'label': key.capitalize()} for key in s.KEYS], constant=True)
    drafts = Property('QVariantMap', lambda s: dict(s._drafts), notify=changed)
    errors = Property('QVariantMap', lambda s: dict(s._errors), notify=changed)
    validationError = Property(str, lambda s: s._error, notify=changed)
    notice = Property(str, lambda s: s._notice, constant=True)
    exportText = Property(str, lambda s: s._export, notify=changed)
    active = Property(bool, lambda s: s.check_owner(), notify=changed)
    canAccept = Property(bool, lambda s: s.check_owner(), notify=changed)

    def _values(self):
        return {key: getattr(self._figure.subplotpars, key) for key in self.KEYS}

    def _read_actual(self):
        self._drafts = {key: f'{value:.3f}' for key, value in self._values().items()}
        self._dirty = False
        self._errors, self._error = {}, ''
        if self._export:
            self._export = self._export_actual()

    def check_owner(self):
        if self._closed:
            return False
        navigation = self._navigation
        axes = self._figure.axes
        valid = (isValid(navigation) and navigation.allowed(self._owner)
                 and navigation.toolbar.canvas is self._canvas
                 and self._canvas.figure is self._figure
                 and len(axes) == len(self._axes)
                 and all(current is captured for current, captured in zip(axes, self._axes)))
        if not valid:
            self.shutdown()
            self.retired.emit()
        return valid

    def _parse(self, drafts):
        values, errors = {}, {}
        for key, text in drafts.items():
            try:
                value = float(text.strip())
            except ValueError:
                value = math.nan
            if not math.isfinite(value) or not 0 <= value <= 1:
                errors[key] = 'Enter a finite number from 0 to 1.'
            else:
                values[key] = round(value, 3)
        for lower, higher in (('bottom', 'top'), ('left', 'right')):
            if lower in values and higher in values and (
                    round((values[higher] - values[lower]) * 1000) < 1):
                message = f'{higher.capitalize()} must exceed {lower} by at least 0.001.'
                errors[lower] = errors[higher] = message
        return values, errors

    def _apply(self, values):
        if any(getattr(self._figure.subplotpars, key) != value for key, value in values.items()):
            self._figure.subplots_adjust(**values)
            self._canvas.draw_idle()
        if self._export:
            self._export = self._export_actual()

    @Slot(str, str, result=bool)
    def setDraft(self, key, text):
        if not self.check_owner() or key not in self.KEYS:
            return False
        if text == self._drafts[key]:
            return not self._errors
        self._drafts[key] = text
        self._dirty = True
        _, self._errors = self._parse(self._drafts)
        self._error = next(iter(self._errors.values()), '')
        self.changed.emit()
        return not self._errors

    @Slot(result=bool)
    def commitDrafts(self):
        if not self.check_owner():
            return False
        if not self._dirty:
            return True
        values, self._errors = self._parse(self._drafts)
        self._error = next(iter(self._errors.values()), '')
        if self._errors:
            self.changed.emit()
            return False
        self._apply(values)
        self._read_actual()
        self.changed.emit()
        return True

    @Slot(str, int, result=bool)
    def step(self, key, direction):
        if not self.check_owner() or key not in self.KEYS or direction not in (-1, 1):
            return False
        values, errors = self._parse(self._drafts)
        if errors:
            return False
        proposal = dict(self._drafts)
        proposal[key] = f'{min(1., max(0., values[key] + direction * .005)):.3f}'
        if self._parse(proposal)[1]:
            return False
        self.setDraft(key, proposal[key])
        return self.commitDrafts()

    @Slot(result=bool)
    def reset(self):
        if not self.check_owner():
            return False
        self._apply(self._defaults)
        self._read_actual()
        self.changed.emit()
        return True

    @Slot(result=bool)
    def tightLayout(self):
        if not self.check_owner():
            return False
        try:
            self._figure.tight_layout()
        except (ValueError, RuntimeError) as error:
            self._error = f'Tight layout could not be applied: {error}'
            self.changed.emit()
            return False
        self._canvas.draw_idle()
        self._read_actual()
        self.changed.emit()
        return True

    def _export_actual(self):
        return ',\n'.join(f'{key}={value:.3}' for key, value in self._values().items())

    @Slot(result=bool)
    def exportValues(self):
        if not self.check_owner():
            return False
        self._export = self._export_actual()
        self.changed.emit()
        return True

    def shutdown(self):
        if not self._closed:
            self._closed = True
            self.changed.emit()
