"""Temporary visible adapter for the host-free, display-only linecut renderer."""
from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import QEvent, Property, QUrl, Signal
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QSizePolicy

from .analysis_linecut_controller import AnalysisLinecutController
from .analysis_linecut_scene import AnalysisLinecutScene


class AnalysisLinecutPreview(QQuickWidget):
    """Native presentation delegate; renderer state belongs to the controller."""

    imageChanged = Signal()

    def __init__(self, figure, parent=None, *, controller=None):
        super().__init__(parent)
        self._owns_controller = controller is None
        self.controller = controller or AnalysisLinecutController(figure, self)
        self.figure = self.controller.figure
        self.controller.imageChanged.connect(self.imageChanged)
        self.scene = AnalysisLinecutScene(self.controller, self.engine(), self,
                                         hybrid_report=(False, self.isEnabled()))
        self.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(130)
        self.setAccessibleName('Auxiliary Gate linecut — excluded from heatmap export')
        self.setInitialProperties({'preview': self.controller,
                                   'attachmentGeneration': self.scene.generation})
        self.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / 'AnalysisLinecut.qml')))
        if self.status() == QQuickWidget.Error:
            raise RuntimeError('Cannot load auxiliary linecut: ' + '\n'.join(e.toString() for e in self.errors()))
        self.scene.bind_item(self.rootObject())

    imageUrl = Property(str, lambda self: self.controller.imageUrl, notify=imageChanged)

    def schedule_render(self):
        self.controller.schedule_render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'scene'):
            self.scene.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.scene.report_hybrid(True, self.isEnabled())

    def hideEvent(self, event):
        if hasattr(self, 'scene'):
            self.scene.report_hybrid(False, self.isEnabled())
        super().hideEvent(event)

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.DevicePixelRatioChange, QEvent.EnabledChange) and hasattr(self, 'scene'):
            self.scene.report_hybrid(self.isVisible(), self.isEnabled())
        return result

    def dispose(self):
        if self._owns_controller:
            self.controller.dispose()
        else:
            self.scene.detach()

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)
