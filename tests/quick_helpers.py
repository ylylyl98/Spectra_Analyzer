"""Semantic processing controls shared by tests migrated from Widgets."""
from PySide6.QtCore import QObject


def set_processing_window(processing, value):
    processing.setWindowDraft(str(value))
    return processing.commitWindow()


def processing_button_text(window):
    return window.processing_panel.rootObject().findChild(QObject, 'processingApply').property('text')
