"""DAT-specific presentation attachment around the existing scientific canvas."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Property, Signal, Slot
from PySide6.QtGui import QImage

from .analysis_canvas import AnalysisCanvasBridge


@dataclass(frozen=True)
class CanvasServices:
    validate_peak_menu: Callable = lambda request: False
    continue_peak_menu: Callable = lambda request, key: None
    cancel_canvas_gesture: Callable = lambda: None
    schedule_view_layout: Callable = lambda: None


class DatCanvasController(AnalysisCanvasBridge):
    """Keep scientific/input authority in the bridge; scenes only present it."""

    sceneChanged = Signal()

    def __init__(self, canvas, services=None, parent=None):
        super().__init__(canvas, parent=parent)
        self.services = services or CanvasServices()
        self._scene = None
        self._generation = 0
        self._scene_enabled = False
        self._last_frame = QImage()
        self.viewportChanged.connect(self.services.schedule_view_layout)
        # The Agg timer is deliberately not a QObject child of this bridge.
        # A QObject owner deletion must stop it before its next publication.
        self.destroyed.connect(canvas.dispose)

    sceneGeneration = Property(int, lambda self: self._generation, notify=sceneChanged)

    def allowed(self, owner=None):
        return self._scene_enabled and super().allowed(owner)

    def attach_scene(self, scene):
        if self._disposed:
            raise RuntimeError('The DAT canvas controller has been disposed.')
        if self._scene is not None:
            self._scene.detach()
        self._generation += 1
        self._scene = scene
        self.provider = scene.provider
        self.sceneChanged.emit()
        self.stateChanged.emit()
        if not self._last_frame.isNull():
            self._image_url = self.provider.accept(self._last_frame)
            self.imageChanged.emit()
        return self._generation

    def update_scene(self, generation, *, attached, visible, enabled, viewport_size,
                     device_pixel_ratio):
        if self._disposed or self._scene is None or generation != self._generation:
            return False
        enabled = bool(attached and enabled)
        if self._scene_enabled != enabled:
            self._scene_enabled = enabled
            self.retire_interaction()
        self.set_visible(bool(attached and visible))
        if attached:
            self.set_device_pixel_ratio(device_pixel_ratio)
            self.setViewport(*viewport_size)
        return True

    @Slot(int, float, float, result=bool)
    def reportViewport(self, generation, width, height):
        if self._disposed or self._scene is None or generation != self._generation:
            return False
        self.setViewport(width, height)
        return True

    def detach_scene(self, generation):
        if self._scene is None or generation != self._generation:
            return
        self.set_visible(False)
        self._scene_enabled = False
        self.retire_interaction()
        self._scene = None
        self._generation += 1
        self._image_url = ''
        self.imageChanged.emit()
        self.sceneChanged.emit()
        self.stateChanged.emit()

    def publish(self, image):
        if self._disposed:
            return
        self._last_frame = image.copy()
        if self._scene is not None:
            self._image_url = self.provider.accept(image)
            self.imageChanged.emit()

    @Slot(str, str, str, result=bool)
    def choosePeakMenu(self, request_id, key, owner):
        request = self._peak_menu_request
        if (key not in ('set_label', 'delete') or request is None or not self._peak_menu_ready
                or request.request_id != request_id or request.pointer.owner != owner):
            return False
        self.clear_peak_menu()
        if not self.allowed(owner) or not self.services.validate_peak_menu(request):
            self.restorePeakMenuFocus(owner)
            return False
        self.services.continue_peak_menu(request, key)
        self.restorePeakMenuFocus(owner)
        return True

    def retire_interaction(self):
        super().retire_interaction()
        self.services.cancel_canvas_gesture()

    def dispose(self):
        if self._disposed:
            return
        scene = self._scene
        if scene is not None:
            scene.detach()
        super().dispose()
        self._last_frame = QImage()
