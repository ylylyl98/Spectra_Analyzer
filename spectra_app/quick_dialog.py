"""Modal Qt Quick windows with the result contract used by application dialogs.

The QObject is a discoverable lifetime owner. All editor state belongs to the
dialog bridge, and all rendered controls belong to the QQuickView.
"""
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, QEventLoop, QSize, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QWindow
from PySide6.QtQuick import QQuickView


def _transient_window(parent):
    """Resolve actual windows through QWidget, Quick item and QObject owners."""
    owner = parent
    while owner is not None:
        window = getattr(owner, 'root_window', None)
        if window is None:
            accessor = getattr(owner, 'window', None)
            window = accessor() if callable(accessor) else accessor if accessor is not None else owner
        if hasattr(window, 'winId'):
            window.winId()
        handle = window.windowHandle() if hasattr(window, 'windowHandle') else window
        if isinstance(handle, QWindow):
            return handle
        owner = owner.parent()
    return None


class QuickDialog(QObject):
    accepted = Signal()
    rejected = Signal()
    finished = Signal(int)
    Accepted, Rejected = 1, 0

    def __init__(self, bridge, qml_name, parent=None, *, title='', size=(600, 520),
                 image_provider=None, minimum_size=(380, 360)):
        super().__init__(parent)
        self.bridge = bridge
        bridge.setParent(self)
        self._result = self.Rejected
        self._finished = False
        self._loop = None
        self._view = QQuickView()
        self._view.setTitle(title)
        self._view.setResizeMode(QQuickView.SizeRootObjectToView)
        self._view.setMinimumSize(QSize(*minimum_size))
        self._view.setModality(Qt.ApplicationModal)
        if parent is not None:
            window = _transient_window(parent)
            if window is not None:
                self._view.setTransientParent(window)
            parent.destroyed.connect(self.reject)
        self._view.installEventFilter(self)
        if image_provider is not None:
            self._view.engine().addImageProvider('format-preview', image_provider)
        self._view.setInitialProperties({'bridge': bridge, 'dialogHost': self})
        self._view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / qml_name)))
        if self._view.status() == QQuickView.Error:
            errors = '\n'.join(error.toString() for error in self._view.errors())
            self._view.deleteLater()
            raise RuntimeError('Cannot load dialog: ' + errors)
        self.resize(*size)
        self.destroyed.connect(self._view.deleteLater)

    def rootObject(self):
        return self._view.rootObject()

    def window(self):
        return self._view

    def resize(self, width, height):
        self._view.resize(width, height)

    def width(self):
        return self._view.width()

    def height(self):
        return self._view.height()

    def show(self):
        self._view.show()
        self._view.requestActivate()
        self.rootObject().forceActiveFocus()

    def exec(self):
        if self._finished:
            return self._result
        # The parent can be deleted during the nested modal loop. Keep the loop
        # alive independently until exec() returns, and always wake it on teardown.
        loop = self._loop = QEventLoop()
        self.destroyed.connect(loop.quit)
        self.show()
        if not self._finished:
            loop.exec()
        loop.deleteLater()
        self._loop = None
        return self._result

    def result(self):
        return self._result

    @Slot()
    def accept(self):
        if self.bridge.canAccept:
            self._finish(self.Accepted)

    @Slot()
    def reject(self):
        self._finish(self.Rejected)

    def close(self):
        self.reject()
        return True

    def _finish(self, result):
        if self._finished:
            return
        self._finished, self._result = True, result
        shutdown = getattr(self.bridge, 'shutdown', None)
        if shutdown is not None:
            shutdown()
        self._view.hide()
        if self._loop is not None:
            self._loop.quit()
        (self.accepted if result == self.Accepted else self.rejected).emit()
        self.finished.emit(result)

    def eventFilter(self, watched, event):
        if watched is self._view and event.type() == QEvent.Close:
            self.reject()
        elif watched is self._view and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.reject()
            return True
        return False

    def deleteLater(self):
        self.reject()
        super().deleteLater()
