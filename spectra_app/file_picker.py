"""Synchronous local-path facade over the platform Qt Quick file dialogs.

Each call owns its QML engine and dialog until the nested modal loop returns.
Qt chooses the platform dialog; no widget or hidden proxy window is created.
"""
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, QEventLoop, QUrl, Qt
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlProperty
from shiboken6 import delete, isValid

from .quick_dialog import _transient_window


def _parent_window(parent):
    owner = parent
    while owner is not None:
        if isinstance(owner, QWindow):
            return owner
        window = owner.window() if hasattr(owner, 'window') else None
        if isinstance(window, QWindow):
            return window
        if window is not None and hasattr(window, 'windowHandle'):
            # During migration an existing, visible QWidget may still own a
            # caller. Resolve its real window, without constructing any proxy.
            return _transient_window(owner)
        owner = owner.parent()
    return None


class _PickerRequest(QObject):
    """One modal invocation, including terminal-signal and teardown ownership."""

    def __init__(self, parent, caption, directory, filters, selected_filter, mode, suffix):
        super().__init__(parent)
        self.dialog = self.component = self.engine = None
        self._connections = []
        self._loop = None
        self._finished = False
        self.paths, self.selected_filter = [], ''
        self._filters = filters.split(';;') if filters else []
        self._folder = mode is None
        try:
            app = QGuiApplication.instance()
            if app is None:
                raise RuntimeError('File selection requires a running Qt application.')
            self.engine = QQmlEngine()
            name = 'NativeFolderPicker.qml' if self._folder else 'NativeFilePicker.qml'
            self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(
                str(Path(__file__).with_name('qml') / name)))
            self.component.setParent(self.engine)
            properties = {'parentWindow': _parent_window(parent), 'title': caption,
                          'modality': Qt.ApplicationModal}
            if not self._folder:
                properties.update(fileMode=mode, nameFilters=self._filters, defaultSuffix=suffix)
            if directory:
                path = Path(directory).absolute()
                folder = path if self._folder or path.is_dir() else path.parent
                properties['currentFolder'] = QUrl.fromLocalFile(str(folder))
                if not self._folder and not path.is_dir():
                    properties['selectedFile'] = QUrl.fromLocalFile(str(path))
            self.dialog = self.component.createWithInitialProperties(properties)
            if self.dialog is None:
                errors = '\n'.join(error.toString() for error in self.component.errors())
                raise RuntimeError('Cannot load file picker: ' + errors)
            self.dialog.setParent(self.engine)
            if self._filters:
                index = self._filters.index(selected_filter) if selected_filter in self._filters else 0
                QQmlProperty.write(self.dialog, 'selectedNameFilter.index', index)
            self._connect(self.dialog.accepted, self._accept)
            self._connect(self.dialog.rejected, self._cancel)
            self._connect(app.aboutToQuit, self._cancel)
            if parent is not None:
                self._connect(parent.destroyed, self._cancel)
            window = properties['parentWindow']
            if window is not None:
                window.installEventFilter(self)
                if window is not parent:
                    self._connect(window.destroyed, self._cancel)
        except BaseException:
            self.dispose()
            raise

    def _connect(self, signal, callback):
        self._connections.append(signal.connect(callback))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Close:
            self._cancel()
        return False

    def _accept(self):
        if self._finished:
            return
        urls = ([self.dialog.property('selectedFolder')] if self._folder
                else self.dialog.property('selectedFiles'))
        # A mixed local/remote selection is not a partial successful selection.
        if urls and all(url.isLocalFile() and url.toLocalFile() for url in urls):
            self.paths = [url.toLocalFile() for url in urls]
            if self._filters:
                index = QQmlProperty.read(self.dialog, 'selectedNameFilter.index')
                if 0 <= index < len(self._filters):
                    self.selected_filter = self._filters[index]
        self._finish()

    def _cancel(self, *args):
        if not self._finished:
            self.paths, self.selected_filter = [], ''
            self._finish()

    def _finish(self):
        self._finished = True
        if self.dialog is not None and isValid(self.dialog):
            self.dialog.close()
        if self._loop is not None:
            self._loop.quit()

    def _open(self):
        self.dialog.open()

    def exec(self):
        # The caller can disappear during exec; the loop must outlive that owner.
        self._loop = QEventLoop()
        try:
            self._open()
            if not self._finished:
                self._loop.exec()
            return self.paths, self.selected_filter
        finally:
            delete(self._loop)
            self._loop = None

    def dispose(self):
        self._cancel()
        for connection in self._connections:
            QObject.disconnect(connection)
        self._connections.clear()
        # The QML type has a native extension object. Destroy through QObject
        # ownership rather than deleting its Python extension wrapper directly.
        for obj in (self.engine, self):
            if obj is not None and isValid(obj):
                delete(obj)


class FilePicker:
    """Existing chooser call contracts with actual QWindow ownership."""

    @staticmethod
    def _select(parent, caption, directory, filters, selected_filter, mode, suffix=''):
        request = _PickerRequest(parent, caption, directory, filters, selected_filter, mode, suffix)
        try:
            return request.exec()
        finally:
            request.dispose()

    @staticmethod
    def getOpenFileName(parent=None, caption='', dir='', filter='', selectedFilter=''):
        paths, selected = FilePicker._select(parent, caption, dir, filter, selectedFilter, 0)
        return (paths[0], selected) if paths else ('', '')

    @staticmethod
    def getOpenFileNames(parent=None, caption='', dir='', filter='', selectedFilter=''):
        return FilePicker._select(parent, caption, dir, filter, selectedFilter, 1)

    @staticmethod
    def getSaveFileName(parent=None, caption='', dir='', filter='', selectedFilter='', *, default_suffix=''):
        paths, selected = FilePicker._select(parent, caption, dir, filter, selectedFilter, 2, default_suffix)
        return (paths[0], selected) if paths else ('', '')

    @staticmethod
    def getExistingDirectory(parent=None, caption='', dir=''):
        paths, _ = FilePicker._select(parent, caption, dir, '', '', None)
        return paths[0] if paths else ''
