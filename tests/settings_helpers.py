from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def wait_for_background_jobs(window, timeout=5):
    """Pump the GUI until asynchronous application has completed, with a hard bound."""
    deadline = time.monotonic() + timeout
    while window._background_jobs.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert not window._background_jobs.busy, "Background operation timed out"


def isolate_settings(test_case) -> Path:
    """Use real disk-backed settings without touching the user's preferences."""
    directory = TemporaryDirectory()
    test_case.addCleanup(directory.cleanup)
    path = Path(directory.name) / "preferences.ini"
    class IsolatedSettings(QSettings):
        def __init__(self, *args):
            super().__init__(str(path), QSettings.IniFormat)

    settings_patch = patch("spectra_app.main_window.QSettings", IsolatedSettings)
    settings_patch.start()
    test_case.addCleanup(settings_patch.stop)
    return path
