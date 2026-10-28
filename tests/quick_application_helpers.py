"""Real production Quick fixtures, isolated from the user's persistent profile."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
import shiboken6


@contextmanager
def isolated_settings():
    with TemporaryDirectory(prefix='spectra-quick-settings-') as folder:
        class TestSettings(QSettings):
            def __init__(self, *args, **kwargs):
                super().__init__(str(Path(folder) / 'profile.ini'), QSettings.IniFormat)
        with patch('spectra_app.main_window.QSettings', TestSettings):
            yield Path(folder)


def pump(milliseconds=45):
    QTest.qWait(milliseconds)
    QCoreApplication.processEvents()


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        pump(10)
    assert predicate(), 'Quick operation timed out'
    pump()


def item(host, name):
    def visit(candidate):
        if candidate.objectName() == name:
            return candidate
        for child in candidate.childItems():
            found = visit(child)
            if found is not None:
                return found
    result = visit(host.window.contentItem())
    assert result is not None, f'Missing actual Quick item {name}'
    return result


def click(host, target):
    point = target.mapToScene(QPointF(target.width()/2, target.height()/2)).toPoint()
    QTest.mouseClick(host.window, Qt.LeftButton, Qt.NoModifier, point)
    pump()


@contextmanager
def application():
    from run_qt import configure_application_fonts
    from spectra_app.quick_root import QuickApplication
    from PySide6.QtWidgets import QWidget, QMdiArea
    from PySide6.QtQuickWidgets import QQuickWidget
    app = QGuiApplication.instance() or QGuiApplication([])
    assert type(app) is QGuiApplication
    app.setQuitOnLastWindowClosed(False)
    configure_application_fonts(app)
    def forbidden(*args, **kwargs):
        raise AssertionError('Actual Quick fixture constructed a legacy QWidget')
    with isolated_settings() as folder, patch.object(QWidget, '__init__', forbidden), patch.object(QMdiArea, '__init__', forbidden), patch.object(QQuickWidget, '__init__', forbidden):
        host = QuickApplication()
        errors = []
        host.controller._show_error = errors.append
        host.show()
        pump(100)
        try:
            yield host, folder, errors
        finally:
            owner = host.controller
            lifetime_valid = all(shiboken6.isValid(value) for value in (host, owner, host.window))
            if shiboken6.isValid(owner):
                wait_until(lambda: not owner._project_io_jobs.busy and not owner._background_jobs.busy)
            if shiboken6.isValid(host.window) and not owner._closing:
                with patch.object(owner, '_confirm_project_discard_async', lambda callback: callback()):
                    host.window.close()
                    wait_until(lambda: owner._closing)
            if shiboken6.isValid(host.window):
                host.dispose()
            elif shiboken6.isValid(host.engine):
                # Preserve a lifespan assertion's original failure while still
                # releasing the real engine and its attached scene routes.
                host.detach_scenes()
                shiboken6.delete(host.engine)
            if shiboken6.isValid(host):
                host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            pump()
            assert lifetime_valid, 'The production host, controller or window died before application disposal.'
