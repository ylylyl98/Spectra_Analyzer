"""Small Qt Quick prompts with synchronous application-facing results."""
from enum import Enum
import math
import re

from PySide6.QtCore import QObject, Property, Signal, Slot
from shiboken6 import isValid

from .quick_dialog import QuickDialog


class PromptAction(Enum):
    OK = 'ok'
    YES = 'yes'
    SAVE = 'save'
    DISCARD = 'discard'
    CANCEL = 'cancel'


_ACTION_LABELS = {
    PromptAction.OK: 'OK', PromptAction.YES: 'Yes', PromptAction.SAVE: 'Save',
    PromptAction.DISCARD: 'Discard', PromptAction.CANCEL: 'Cancel',
}


class CommonPromptBridge(QObject):
    changed = Signal()
    acceptRequested = Signal()
    rejectRequested = Signal()

    def __init__(self, mode, label='', *, message='', kind='information',
                 choices=(PromptAction.OK,), default=PromptAction.OK, text='',
                 items=(), current=0, value=0, minimum=0, maximum=2147483647,
                 decimals=6):
        super().__init__()
        if mode not in ('message', 'text', 'item', 'int', 'float'):
            raise ValueError('Unknown prompt mode: ' + mode)
        if minimum > maximum:
            raise ValueError('Numeric minimum must not exceed maximum')
        if mode == 'float':
            if not isinstance(decimals, int) or not 0 <= decimals <= 323:
                raise ValueError('Decimal places must be an integer from 0 to 323')
            if not all(math.isfinite(number) for number in (value, minimum, maximum)):
                raise ValueError('Numeric value and bounds must be finite')
            # QDoubleSpinBox rounds its bounds as well as its value. In particular,
            # an old sub-micro width can have a six-decimal effective minimum of 0.
            minimum, maximum = (float(f'{number:.{decimals}f}') for number in (minimum, maximum))
        self._mode, self._label, self._message, self._kind = mode, label, message, kind
        self._choices = tuple(choices) if mode == 'message' else (PromptAction.OK, PromptAction.CANCEL)
        if any(not isinstance(action, PromptAction) for action in self._choices):
            raise TypeError('Prompt choices must be PromptAction members')
        self._default = (default if default in self._choices else PromptAction.CANCEL) if mode == 'message' else PromptAction.OK
        self._items = list(items)
        self._index = current if 0 <= current < len(self._items) else (0 if self._items else -1)
        self._minimum, self._maximum = minimum, maximum
        self._decimals = decimals
        self._draft = str(min(maximum, max(minimum, value))) if mode == 'int' else text
        if mode == 'float':
            self._draft = f'{min(maximum, max(minimum, value)):.{decimals}f}'
        self._error = ''
        self._closed = False
        self._requested = False
        self.action = PromptAction.CANCEL

    mode = Property(str, lambda self: self._mode, constant=True)
    kind = Property(str, lambda self: self._kind, constant=True)
    label = Property(str, lambda self: self._label, constant=True)
    message = Property(str, lambda self: self._message, constant=True)
    actions = Property('QVariantList', lambda self: [
        {'key': action.value, 'label': _ACTION_LABELS[action]} for action in self._choices
    ], constant=True)
    defaultAction = Property(str, lambda self: self._default.value, constant=True)
    draft = Property(str, lambda self: self._draft, notify=changed)
    items = Property('QStringList', lambda self: self._items, constant=True)
    currentIndex = Property(int, lambda self: self._index, notify=changed)
    canAccept = Property(bool, lambda self: not self._closed and not self._validation_error(), notify=changed)
    validationError = Property(str, lambda self: self._error, notify=changed)

    def _validation_error(self):
        if self._mode == 'item' and not 0 <= self._index < len(self._items):
            return 'Choose an item before continuing.'
        if self._mode == 'int':
            draft = self._draft.strip()
            if not re.fullmatch(r'[+-]?[0-9]+', draft):
                return 'Enter a whole number.'
            try:
                value = int(draft)
            except ValueError:
                return 'Enter a whole number within the allowed range.'
            if not self._minimum <= value <= self._maximum:
                return f'Enter a whole number from {self._minimum} to {self._maximum}.'
        if self._mode == 'float':
            try:
                value = float(self._draft.strip())
            except ValueError:
                return 'Enter a finite number.'
            if not math.isfinite(value):
                return 'Enter a finite number.'
            if not self._minimum <= value <= self._maximum:
                return f'Enter a number from {self._minimum:g} to {self._maximum:g}.'
        return ''

    @Slot(str)
    def setDraft(self, text):
        if self._closed or self._requested or text == self._draft:
            return
        self._draft = text
        if self._error:
            self._error = self._validation_error()
        self.changed.emit()

    @Slot(int)
    def setCurrentIndex(self, index):
        if self._closed or self._requested or index == self._index:
            return
        self._index = index if 0 <= index < len(self._items) else -1
        if self._error:
            self._error = self._validation_error()
        self.changed.emit()

    @Slot(str)
    def chooseAction(self, key):
        if self._closed or self._requested:
            return
        if key == PromptAction.CANCEL.value:
            self.cancel()
            return
        action = next((action for action in self._choices if action.value == key), None)
        if action is None:
            return
        self._error = self._validation_error()
        if self._error:
            self.changed.emit()
            return
        self.action = action
        self._requested = True
        self.acceptRequested.emit()

    @Slot()
    def activateDefault(self):
        self.chooseAction(self._default.value)

    @Slot()
    def cancel(self):
        if not self._closed and not self._requested:
            self.action = PromptAction.CANCEL
            self._requested = True
            self.rejectRequested.emit()

    def input_value(self):
        if self._mode == 'float':
            return float(f'{float(self._draft.strip()):.{self._decimals}f}')
        if self._mode == 'int':
            return int(self._draft.strip())
        if self._mode == 'item':
            return self._items[self._index] if 0 <= self._index < len(self._items) else ''
        return self._draft

    def shutdown(self):
        if not self._closed:
            self._closed = True
            self.changed.emit()


def _exec_prompt(parent, title, bridge):
    dialog = QuickDialog(bridge, 'CommonPrompt.qml', parent, title=title,
                         size=(460, 250), minimum_size=(300, 180))
    bridge.acceptRequested.connect(dialog.accept)
    bridge.rejectRequested.connect(dialog.reject)
    try:
        return dialog.exec() == QuickDialog.Accepted
    finally:
        if isValid(dialog):
            dialog.deleteLater()


def prompt_message(parent, title, message, choices=(PromptAction.OK,),
                   default=PromptAction.OK, *, kind='information'):
    bridge = CommonPromptBridge('message', message=message, kind=kind,
                                choices=choices, default=default)
    accepted = _exec_prompt(parent, title, bridge)
    return bridge.action if accepted else PromptAction.CANCEL


def prompt_text(parent, title, label, *, text=''):
    bridge = CommonPromptBridge('text', label, text=text)
    accepted = _exec_prompt(parent, title, bridge)
    return (bridge.input_value() if accepted else text), accepted


def prompt_item(parent, title, label, items, current=0):
    bridge = CommonPromptBridge('item', label, items=items, current=current)
    original = bridge.input_value()
    accepted = _exec_prompt(parent, title, bridge)
    return (bridge.input_value() if accepted else original), accepted


def prompt_int(parent, title, label, *, value=0, min=0, max=2147483647):
    bridge = CommonPromptBridge('int', label, value=value, minimum=min, maximum=max)
    original = bridge.input_value()
    accepted = _exec_prompt(parent, title, bridge)
    return (bridge.input_value() if accepted else original), accepted


def prompt_float(parent, title, label, *, value=0., minimum=0., maximum=100., decimals=6):
    bridge = CommonPromptBridge('float', label, value=value, minimum=minimum,
                                maximum=maximum, decimals=decimals)
    accepted = _exec_prompt(parent, title, bridge)
    return (bridge.input_value() if accepted else value), accepted
