from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtGui import QGuiApplication
from matplotlib import rcParams

from spectra_app.main_window import ApplicationController
from spectra_app.quick_root import QuickApplication
from spectra_app.app_identity import configure_application_icon, configure_process_identity


def schedule_startup_project(window: ApplicationController, arguments: list[str]) -> None:
    """Dispatch Qt's remaining filename arguments once the window is visible."""
    filenames = arguments[1:]
    if not filenames:
        return

    def open_requested():
        if len(filenames) != 1 or Path(filenames[0]).suffix.lower() not in ('.spectra', '.spextra'):
            window._show_error('Start with one Spectra project filename (.spectra or .spextra).')
            return
        window._open_project_path(filenames[0])

    QTimer.singleShot(0, window, open_requested)


def configure_application_fonts(app: QGuiApplication) -> None:
    """Use the same default face for Qt controls and unstyled plot text."""
    app.setFont(QFont("Arial", 10))
    rcParams.update({
        "font.family": ["Arial", "DejaVu Sans"],
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "mathtext.fontset": "custom",
        "mathtext.rm": "Arial",
        "mathtext.it": "Arial:italic",
        "mathtext.bf": "Arial:bold",
        "mathtext.bfit": "Arial:italic:bold",
        "mathtext.sf": "Arial",
        "mathtext.tt": "Arial",
        "mathtext.cal": "Arial",
        "mathtext.fallback": "stixsans",
    })


def main() -> int:
    configure_process_identity()
    app = QGuiApplication(sys.argv)
    configure_application_icon(app)
    configure_application_fonts(app)
    host = QuickApplication()
    win = host.controller
    host.show()
    arguments = app.arguments()
    # Windows Qt can replace non-ANSI characters with '?' even though Python's
    # argv retains the Unicode filename. Let Qt consume its own options first,
    # then recover the sole native-project filename from the original argv.
    filenames = [value for value in sys.argv[1:]
                 if Path(value).suffix.lower() in ('.spectra', '.spextra')]
    if len(arguments) == 2 and len(filenames) == 1:
        arguments[1] = filenames[0]
    schedule_startup_project(win, arguments)
    result = app.exec()
    host.dispose()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
