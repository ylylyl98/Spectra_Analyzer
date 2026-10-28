"""Explicit, cancellable updates using the existing application job owner."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile

from PySide6.QtCore import QObject, Property, QEvent, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtQuick import QQuickView

from .app_version import APP_VERSION, UPDATE_REPOSITORY
from .background_jobs import BackgroundJobs
from .updates import ReleaseClient, launch_installer


class UpdatePresentation(QObject):
    """Update state and user intent; rendered controls belong only to QML."""
    stateChanged = Signal()
    applicationFontChanged = Signal()
    checkRequested = Signal()
    downloadRequested = Signal()
    installRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self._status = 'Checking for updates…'
        self._notes = ''
        self._busy = False
        self._release_available = self._installer_verified = False
        self._source_mode = not bool(getattr(sys, 'frozen', False))
        QGuiApplication.instance().fontChanged.connect(self.applicationFontChanged)

    applicationFont = Property(QFont, lambda self: QGuiApplication.font(), notify=applicationFontChanged)
    currentVersion = Property(str, lambda self: APP_VERSION, constant=True)
    sourceMode = Property(bool, lambda self: self._source_mode, constant=True)
    statusText = Property(str, lambda self: self._status, notify=stateChanged)
    releaseNotes = Property(str, lambda self: self._notes, notify=stateChanged)
    busy = Property(bool, lambda self: self._busy, notify=stateChanged)
    canCheck = Property(bool, lambda self: not self._busy, notify=stateChanged)
    canDownload = Property(bool, lambda self: not self._busy and self._release_available
                           and not self._installer_verified, notify=stateChanged)
    canInstall = Property(bool, lambda self: not self._busy and self._installer_verified
                          and not self._source_mode, notify=stateChanged)

    def set_status(self, text):
        self._status = str(text)
        self.stateChanged.emit()

    def set_notes(self, text):
        self._notes = str(text)
        self.stateChanged.emit()

    def set_actions(self, *, busy, release_available, installer_verified):
        self._busy = bool(busy)
        self._release_available = bool(release_available)
        self._installer_verified = bool(installer_verified)
        self.stateChanged.emit()

    @Slot()
    def checkUpdates(self):
        if self.canCheck:
            self.checkRequested.emit()

    @Slot()
    def downloadUpdate(self):
        if self.canDownload:
            self.downloadRequested.emit()

    @Slot()
    def installUpdate(self):
        if self.canInstall:
            self.installRequested.emit()

    @Slot()
    def dismiss(self):
        self.closeRequested.emit()


class UpdateDialog(QQuickView):
    """Reusable nonmodal window; dismissal never destroys an owned worker."""
    rejected = Signal()

    def __init__(self, parent):
        super().__init__()
        self.setTitle('Spectra Analyzer Updates')
        self.setResizeMode(QQuickView.SizeRootObjectToView)
        self.setMinimumSize(QSize(340, 360))
        self.setModality(Qt.NonModal)
        self.installEventFilter(self)
        owner = parent.window() if hasattr(parent, 'window') else parent
        if owner is not None:
            if hasattr(owner, 'winId'):
                owner.winId()
            handle = owner.windowHandle() if hasattr(owner, 'windowHandle') else owner
            if handle is not None:
                self.setTransientParent(handle)
        self.bridge = UpdatePresentation(self)
        self.bridge.closeRequested.connect(self.reject)
        self.setInitialProperties({'bridge': self.bridge})
        self.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / 'UpdateWindow.qml')))
        if self.status() == QQuickView.Error:
            errors = '\n'.join(error.toString() for error in self.errors())
            self.deleteLater()
            raise RuntimeError('Cannot load update window: ' + errors)
        self.resize(560, 390)

    def show(self):
        super().show()
        self.requestActivate()
        self.rootObject().forceActiveFocus()

    @Slot()
    def reject(self):
        if self.isVisible():
            self.hide()
            self.rejected.emit()

    def eventFilter(self, watched, event):
        if watched is self and event.type() == QEvent.Close:
            self.reject()
            event.accept()
            return True
        if watched is self and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.reject()
            return True
        return False


class UpdateController(QObject):
    """Retain workers until idle, and revoke stale save/close continuations."""
    install_requested = Signal()

    def __init__(self, parent, *, client=None):
        super().__init__(parent)
        self.client = client or ReleaseClient(UPDATE_REPOSITORY)
        self.jobs = BackgroundJobs(self)
        self.jobs.idle.connect(self._idle)
        self.dialog = None
        self.release = None
        self.installer = None
        self.install_request = None
        self._armed = None
        self._generation = 0
        self._directory = None
        self._close_when_idle = False
        self._installation_started = False

    @property
    def busy(self):
        return self.jobs.busy

    def open(self):
        if self.dialog is None:
            self.dialog = UpdateDialog(self.parent())
            self.destroyed.connect(self.dialog.deleteLater)
            self.dialog.bridge.checkRequested.connect(self.check)
            self.dialog.bridge.downloadRequested.connect(self.download)
            self.dialog.bridge.installRequested.connect(self.request_install)
            self.dialog.rejected.connect(self.cancel)
        if self.dialog.isVisible():
            self.dialog.raise_()
            self.dialog.requestActivate()
            return
        self.dialog.show()
        if not self.busy:
            self.check()

    def _status(self, text):
        self.dialog.bridge.set_status(text)

    def _set_busy(self, value):
        self.dialog.bridge.set_actions(busy=value, release_available=self.release is not None,
                                       installer_verified=self.installer is not None)

    def check(self):
        if self.busy:
            return
        self._generation += 1
        self.install_request = None
        self._armed = None
        self.release = self.installer = None
        self._cleanup()
        self.dialog.bridge.set_notes('')
        self._set_busy(True)
        self.jobs.start(lambda context: self.client.check(APP_VERSION, context), self._checked,
                        on_error=self._error, on_progress=self._status)

    def _checked(self, release):
        self.release = release
        if release is None:
            self._status(f'Spectra Analyzer {APP_VERSION} is up to date.')
        else:
            self._status(f'Version {release.version} is available. Download it to review and install.')
            self.dialog.bridge.set_notes(release.notes)

    def download(self):
        if self.busy or self.release is None:
            return
        self._cleanup()
        self._directory = Path(tempfile.mkdtemp(prefix='SpectraAnalyzer-update-'))
        directory, release = self._directory, self.release
        self._set_busy(True)
        self.jobs.start(lambda context: self.client.download(release, directory, context), self._downloaded,
                        on_error=self._error, on_progress=self._status)

    def _downloaded(self, installer):
        self.installer = installer
        self._status('Download verified. Save your work, then choose Close app and install.')
        self._set_busy(self.busy)

    def _error(self, error):
        self._status(str(error))

    def request_install(self):
        if self.busy or self.installer is None:
            return
        self._generation += 1
        self.install_request = (self._generation, self.installer)
        self.install_requested.emit()

    def take_install_request(self):
        # A revision change invalidates close approval. Consume any previously
        # armed request before another Save / Discard / Cancel decision.
        request = self.install_request or self._armed
        self.install_request = self._armed = None
        return request

    def request_is_current(self, request):
        return (request is not None and request[0] == self._generation
                and request[1] is self.installer and self.dialog is not None and self.dialog.isVisible())

    def arm_install(self, request):
        if not self.request_is_current(request):
            return False
        self._armed = request
        return True

    def cancel(self):
        self._generation += 1
        self.install_request = self._armed = None
        self.jobs.cancel()
        if not self.busy:
            self._cleanup()

    def prepare_close(self):
        if self.busy:
            self._close_when_idle = True
            self.cancel()
            if self.dialog:
                self.dialog.hide()
            return False
        return True

    def accept_close(self):
        request, self._armed = self._armed, None
        if request is not None and self.request_is_current(request):
            # Re-check the file immediately before crossing the process boundary.
            launch_installer(request[1])
            self._installation_started = True
        if self.dialog:
            self.dialog.hide()
        if not self._installation_started:
            self._cleanup()

    def install_failed(self, error):
        self._status(f'Could not start the installer: {error}')
        self.dialog.show()

    def _idle(self):
        if self.dialog:
            self._set_busy(False)
            if not self.dialog.isVisible():
                self._cleanup()
        if self._close_when_idle:
            self._close_when_idle = False
            QTimer.singleShot(0, self.parent(), self.parent().close)

    def _cleanup(self):
        if self._directory is not None and not self._installation_started:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None
        self.installer = None
