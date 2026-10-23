from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from spectra_app.main_window import MainWindow
from spectra_app.app_identity import configure_application_icon, configure_process_identity


APP_QSS = """
QMainWindow { background: #f4f6f8; }
QGroupBox {
    background: #ffffff;
    border: 1px solid #d8dee6;
    border-radius: 8px;
    margin-top: 9px;
    padding: 8px;
    font-weight: 500;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }
QPushButton {
    background: #ecf2f9;
    border: 1px solid #cad4de;
    border-radius: 8px;
    min-height: 28px;
    padding: 3px 9px;
}
QPushButton:hover { background: #deebf7; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QListWidget, QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #ccd4dc;
    border-radius: 8px;
    min-height: 28px;
    padding: 3px 6px;
}
/* The native popup delegate reads selection colors from the combo box. */
QComboBox {
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
}
QStatusBar { background: #ffffff; border-top: 1px solid #d6dce3; }
"""


def schedule_startup_project(window: MainWindow, arguments: list[str]) -> None:
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


def main() -> int:
    configure_process_identity()
    app = QApplication(sys.argv)
    configure_application_icon(app)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_QSS)
    win = MainWindow()
    win.show()
    arguments = app.arguments()
    # Windows Qt can replace non-ANSI characters with '?' even though Python's
    # argv retains the Unicode filename. Let Qt consume its own options first,
    # then recover the sole native-project filename from the original argv.
    filenames = [value for value in sys.argv[1:]
                 if Path(value).suffix.lower() in ('.spectra', '.spextra')]
    if len(arguments) == 2 and len(filenames) == 1:
        arguments[1] = filenames[0]
    schedule_startup_project(win, arguments)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
