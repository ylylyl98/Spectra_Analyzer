"""Native sidebar sections whose navigation never commits an editor draft."""
from contextlib import ExitStack

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QLineEdit, QSizePolicy,
                               QScrollArea, QToolButton, QVBoxLayout, QWidget)


class _DisclosureButton(QToolButton):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
        elif event.key() in (Qt.Key_Left, Qt.Key_Right):
            self.setChecked(event.key() == Qt.Key_Right)
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class _DraftGuard(QObject):
    def eventFilter(self, watched, event):
        # Qt transfers focus before the header's clicked/toggled signal. A
        # snapshot in the toggle slot alone would already have lost the draft.
        if (event.type() == QEvent.FocusOut
                and isinstance(QApplication.focusWidget(), _DisclosureButton)):
            return True
        return super().eventFilter(watched, event)


def protect_disclosure_drafts(root):
    """Call once editors have been built, including fields outside sections."""
    guard = _DraftGuard(root)
    for editor in [*root.findChildren(QAbstractSpinBox), *root.findChildren(QLineEdit)]:
        editor.installEventFilter(guard)


class DisclosureSection(QWidget):
    def __init__(self, title, content, *, expanded=False, parent=None):
        super().__init__(parent)
        self.content = content
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.button = _DisclosureButton()
        self.button.setText(title)
        self.button.setAccessibleName(title)
        self.button.setAccessibleDescription('Expand or collapse settings. Use Space, Enter or arrow keys.')
        self.button.setCheckable(True)
        self.button.setFocusPolicy(Qt.StrongFocus)
        self.button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.button)
        layout.addWidget(content)
        self.button.toggled.connect(self._toggle)
        self.button.setChecked(expanded)
        self._toggle(expanded)

    def _toggle(self, expanded):
        spins = self.content.findChildren(QAbstractSpinBox)
        drafts = [(spin, spin.value(), spin.lineEdit().text()) for spin in spins]
        with ExitStack() as blockers:
            for editor in [*spins, *self.content.findChildren(QLineEdit)]:
                blockers.enter_context(QSignalBlocker(editor))
            focus = QApplication.focusWidget()
            if not expanded and focus is not None and self.content.isAncestorOf(focus):
                self.button.setFocus(Qt.OtherFocusReason)
            self.content.setVisible(expanded)
            for spin, value, text in drafts:
                spin.setValue(value)
                spin.lineEdit().setText(text)
        self.button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)


def reveal_editor(editor):
    """Open containing sections before scrolling or focusing a requested field."""
    sections = []
    parent = editor.parentWidget()
    while parent is not None:
        if isinstance(parent, DisclosureSection):
            sections.append(parent)
        parent = parent.parentWidget()
    for section in reversed(sections):
        section.button.setChecked(True)
    # ensureWidgetVisible needs the expanded layout immediately, including when
    # called from a preview double click before Qt's next layout event.
    parent = editor.parentWidget()
    while parent is not None:
        if parent.layout() is not None:
            parent.layout().activate()
        if isinstance(parent, QScrollArea):
            QApplication.sendEvent(parent, QEvent(QEvent.LayoutRequest))
        parent = parent.parentWidget()
