"""Temporary visible QWidget adapter for the host-free formal Quick preview."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QUrl, Signal
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QSizePolicy
from .formal_preview_controller import FormalPreviewController, FormalPreviewBridge
from .formal_preview_scene import FormalPreviewScene


class FormalPreview(QQuickWidget):
    """Embeddable Quick viewport with explicit paper-local display geometry."""

    annotationSelected = Signal(str)
    annotationMoved = Signal(object)
    annotationDeleteRequested = Signal()
    titleMoved = Signal(object)
    propertyEditRequested = Signal(str, str)
    graphCopyRequested = Signal()
    displayScaleChanged = Signal(float)

    def __init__(self, parent=None, *, display_policy='editable', controller=None):
        if display_policy not in ('editable', 'readonly'):
            raise ValueError('Unknown formal preview display policy.')
        super().__init__(parent)
        self.display_policy = display_policy
        self._owns_controller = controller is None
        self.bridge = controller if controller is not None else FormalPreviewController(self, display_policy=display_policy)
        if self.bridge.readOnly != (display_policy == 'readonly'):
            raise ValueError('Controller display policy does not match the preview.')
        self.scene_attachment = FormalPreviewScene(self.bridge, self.engine(), self,
            hybrid_report=(self.isVisible(), self.isEnabled()))
        self.image_provider = self.scene_attachment.provider
        self.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(1, 1)
        self.setAccessibleName('Formal page annotation canvas')
        self.setToolTip('Double-click a title, note, axis or colorbar to edit it. Drag labels or arrow heads to move them. Escape cancels a drag; Ctrl+C copies the graph.')
        if display_policy == 'readonly':
            self.setAccessibleName('Read-only exact exported page')
            self.setToolTip('Wheel to zoom, drag to pan, double-click to fit. These display changes do not alter export.')
        for name in ('annotationSelected', 'annotationMoved', 'annotationDeleteRequested', 'titleMoved',
                     'propertyEditRequested', 'graphCopyRequested', 'displayScaleChanged'):
            getattr(self.bridge, name).connect(getattr(self, name))
        self.setInitialProperties({'bridge': self.bridge,
                                   'attachmentGeneration': self.scene_attachment.generation})
        self.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / 'FormalPreview.qml')))
        if self.status() == QQuickWidget.Error:
            raise RuntimeError('Cannot load formal preview: ' + '\n'.join(e.toString() for e in self.errors()))
        self.scene_attachment.bind_item(self.rootObject())

    page = property(lambda self: self.bridge.page)
    svg_bytes = property(lambda self: self.bridge.svg_bytes)
    snapshot = property(lambda self: self.bridge.snapshot)
    presentation = property(lambda self: self.bridge.presentation,
                            lambda self, value: self.bridge.set_geometry(self.snapshot, value))
    selected_id = property(lambda self: self.bridge._selected_id,
                           lambda self, value: self._select(value))
    _title_selected = property(lambda self: self.bridge._title_selected,
                               lambda self, value: self._select_title(value))

    def _select(self, identifier):
        self.bridge.select_annotation(identifier)

    def _select_title(self, selected):
        self.bridge.select_title(selected)

    def set_mutation_guard(self, guard):
        self.bridge.set_mutation_guard(guard)

    def dispose(self):
        """Detach this scene; permanently retire only an adapter-owned controller."""
        if self._owns_controller:
            self.bridge.dispose()
        else:
            self.scene_attachment.detach()

    def set_page(self, page):
        return self.bridge.set_page(page)

    def set_geometry(self, snapshot, presentation):
        self.bridge.set_geometry(snapshot, presentation)

    def invalidate(self, owner_token):
        """Bind the next accepted owner; retain pixels and retire pending input.

        Shared read-only surfaces must invalidate with a new document's owner
        before calling set_page. A mismatched page is never adopted implicitly.
        """
        self.bridge.invalidate(owner_token)

    def cancel_interaction(self):
        self.bridge.cancel_interaction()

    def set_zoom(self, zoom):
        self.bridge.set_zoom(zoom)

    def paper_display_size(self):
        return self.bridge.paper_display_size()

    def annotation_point(self, annotation, endpoint=False):
        return self.bridge.annotation_point(annotation, endpoint)

    def title_point(self):
        return self.bridge.title_point()

    def paper_to_viewport(self, point):
        """Map paper-local logical pixels to this host, including QML scrolling."""
        paper = self.rootObject().findChild(QObject, 'formalPaper')
        return paper.mapToScene(QPointF(point)).toPoint()

    def _selected(self):
        return self.bridge._selected()

    def hit_test(self, point):
        return self.bridge.hit_test(QPointF(point))

    def event(self, event):
        if event.type() == QEvent.DeferredDelete and hasattr(self, 'bridge'):
            self.dispose()
        result = super().event(event)
        if event.type() in (QEvent.Show, QEvent.Hide, QEvent.EnabledChange) and hasattr(self, 'scene_attachment'):
            self.scene_attachment.report_hybrid(self.isVisible(), self.isEnabled())
        return result


__all__ = ['FormalPreview', 'FormalPreviewBridge', 'FormalPreviewController']
