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


def isolate_settings(test_case, *, project_dialogs=False) -> Path:
    """Isolate preferences; opt into real project guards with project_dialogs=True.

    Legacy fixture cleanup discards automatically so closing a dirty test window
    cannot block on an offscreen dialog. Project guard tests select real dialogs.
    """
    directory = TemporaryDirectory()
    test_case.addCleanup(directory.cleanup)
    path = Path(directory.name) / "preferences.ini"
    class IsolatedSettings(QSettings):
        def __init__(self, *args):
            super().__init__(str(path), QSettings.IniFormat)

    settings_patch = patch("spectra_app.main_window.QSettings", IsolatedSettings)
    settings_patch.start()
    test_case.addCleanup(settings_patch.stop)
    if not project_dialogs:
        discard_patch = patch('spectra_app.main_window.MainWindow._confirm_project_discard', return_value=True)
        discard_patch.start()
        test_case.addCleanup(discard_patch.stop)
    return path
