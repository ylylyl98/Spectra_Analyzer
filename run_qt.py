from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from spectra_app.main_window import MainWindow


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
QStatusBar { background: #ffffff; border-top: 1px solid #d6dce3; }
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_QSS)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
