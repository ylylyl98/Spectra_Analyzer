from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QSettings


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
