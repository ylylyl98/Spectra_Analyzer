"""RGB curve color drafts shared by visual Quick controls and exact entry."""
import math

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtGui import QColor
from shiboken6 import isValid

from .quick_dialog import QuickDialog


class CurveColorPromptBridge(QObject):
    changed = Signal()
    acceptRequested = Signal()
    rejectRequested = Signal()

    def __init__(self, initial):
        super().__init__()
        self._closed = self._requested = False
        self._draft = initial
        self._color = QColor('#000000')
        self._hue = 0.
        self._error = ''
        self._parse_draft()

    draft = Property(str, lambda s: s._draft, notify=changed)
    normalizedColor = Property(str, lambda s: s._color.name(), notify=changed)
    red = Property(int, lambda s: s._color.red(), notify=changed)
    green = Property(int, lambda s: s._color.green(), notify=changed)
    blue = Property(int, lambda s: s._color.blue(), notify=changed)
    hue = Property(float, lambda s: s._hue, notify=changed)
    saturation = Property(float, lambda s: s._color.hsvSaturationF(), notify=changed)
    value = Property(float, lambda s: s._color.valueF(), notify=changed)
    canAccept = Property(bool, lambda s: not s._closed and not s._error, notify=changed)
    validationError = Property(str, lambda s: s._error, notify=changed)

    def _parse_draft(self):
        color = QColor(self._draft.strip())
        self._error = '' if color.isValid() else 'Enter a color name or hexadecimal RGB color.'
        if not self._error:
            self._color = QColor(color.name())  # Deliberately discard alpha like QColor.name().
            if color.hsvHueF() >= 0:
                self._hue = color.hsvHueF()

    @Slot(str)
    def setDraft(self, text):
        if self._closed or self._requested or self._draft == text:
            return
        self._draft = text
        self._parse_draft()
        self.changed.emit()

    def _set_color(self, color):
        self._draft = color.name()
        self._parse_draft()
        self.changed.emit()

    @Slot(int, int, int)
    def setRgb(self, red, green, blue):
        if self._closed or self._requested:
            return
        if all(0 <= channel <= 255 for channel in (red, green, blue)):
            self._set_color(QColor(red, green, blue))

    @Slot(float, float, float)
    def setHsv(self, hue, saturation, value):
        if self._closed or self._requested:
            return
        if all(math.isfinite(channel) and 0 <= channel <= 1 for channel in (hue, saturation, value)):
            self._hue = hue % 1.
            self._set_color(QColor.fromHsvF(self._hue, saturation, value))

    @Slot()
    def accept(self):
        if not self.canAccept or self._requested:
            return
        self._requested = True
        self.acceptRequested.emit()

    @Slot()
    def cancel(self):
        if not self._closed and not self._requested:
            self._requested = True
            self.rejectRequested.emit()

    def input_value(self):
        return self._color.name()

    def shutdown(self):
        if not self._closed:
            self._closed = True
            self.changed.emit()


def _exec_prompt(parent, title, bridge):
    dialog = QuickDialog(bridge, 'CurveColorPrompt.qml', parent, title=title,
                         size=(500, 570), minimum_size=(380, 470))
    bridge.acceptRequested.connect(dialog.accept)
    bridge.rejectRequested.connect(dialog.reject)
    try:
        return dialog.exec() == QuickDialog.Accepted
    finally:
        if isValid(dialog):
            dialog.deleteLater()


def prompt_color(parent, title, initial):
    bridge = CurveColorPromptBridge(initial)
    accepted = _exec_prompt(parent, title, bridge)
    return (bridge.input_value() if accepted else initial), accepted
