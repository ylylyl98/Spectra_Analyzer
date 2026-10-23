"""One persistent Windows taskbar identity and application icon."""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


# Keep this ID stable across application upgrades and shortcut reinstalls.
APP_ID = 'SpectraAnalyzer.Desktop'
ICON_PATH = Path(__file__).resolve().parent / 'resources' / 'spectra_analyzer.ico'


def configure_process_identity() -> None:
    """Set the Windows identity before constructing QApplication or any window."""
    if sys.platform == 'win32':
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        if result < 0:
            raise OSError(f'Windows could not set the application identity (HRESULT {result:#x}).')


def configure_application_icon(app: QApplication) -> None:
    icon = QIcon(str(ICON_PATH))
    if icon.isNull():
        raise FileNotFoundError(f'Application icon is missing or invalid: {ICON_PATH}')
    app.setWindowIcon(icon)
