"""Display-only Agg renderer and coalesced publication, without a widget host."""
from __future__ import annotations

from matplotlib.backends.backend_agg import FigureCanvasAgg
from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from PySide6.QtGui import QImage


class AnalysisLinecutController(QObject):
    imageChanged = Signal()
    stateChanged = Signal()

    def __init__(self, figure, parent=None):
        super().__init__(parent)
        self.figure = figure
        self._base_dpi = figure.dpi
        self._canvas = FigureCanvasAgg(figure)
        self._image_url = ''
        self._last_frame = QImage()
        self._dirty = True
        self._disposed = False
        self._visible = False
        self._viewport = (1., 1.)
        self._dpr = 1.
        self._scene = None
        self._generation = 0
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(16)
        self._render_timer.timeout.connect(self._render_if_visible)

    imageUrl = Property(str, lambda self: self._image_url, notify=imageChanged)
    sceneGeneration = Property(int, lambda self: self._generation, notify=stateChanged)

    def attach_scene(self, scene):
        if self._disposed:
            raise RuntimeError('The linecut controller has been disposed.')
        if self._scene is not None:
            self._scene.detach()
        self._generation += 1
        self._scene = scene
        self.stateChanged.emit()
        if not self._last_frame.isNull():
            self._image_url = scene.provider.accept(self._last_frame)
            self.imageChanged.emit()
        return self._generation

    def update_scene(self, generation, *, attached, visible, enabled, viewport_size,
                     device_pixel_ratio):
        if self._disposed or self._scene is None or generation != self._generation:
            return False
        size = tuple(max(1, round(v)) for v in viewport_size)
        dpr = max(.01, float(device_pixel_ratio))
        if attached and (size != self._viewport or dpr != self._dpr):
            self._viewport, self._dpr = size, dpr
            self._dirty = True
        self._visible = bool(attached and visible and enabled)
        if not self._visible:
            self._render_timer.stop()
        elif self._dirty:
            self.schedule_render()
        return True

    @Slot(int, float, float, result=bool)
    def reportViewport(self, generation, width, height):
        if self._disposed or self._scene is None or generation != self._generation:
            return False
        size = max(1, round(width)), max(1, round(height))
        if size != self._viewport:
            self._viewport = size
            self.schedule_render()
        return True

    def detach_scene(self, generation):
        if self._scene is None or generation != self._generation:
            return
        self._render_timer.stop()
        self._visible = False
        self._scene = None
        self._generation += 1
        self._image_url = ''
        self.imageChanged.emit()
        self.stateChanged.emit()

    def schedule_render(self):
        if self._disposed:
            return
        self._dirty = True
        if self._visible:
            self._render_timer.start()

    def _render_if_visible(self):
        if self._disposed or not self._dirty or not self._visible:
            return
        width, height = self._viewport
        self.figure.set_dpi(self._base_dpi * self._dpr)
        # Round the one physical-pixel conversion before giving Agg its inch
        # dimensions; fractional screen DPR must not truncate an extra pixel.
        pixels = round(width * self._dpr), round(height * self._dpr)
        self.figure.set_size_inches(pixels[0] / self.figure.dpi,
                                    pixels[1] / self.figure.dpi, forward=False)
        self._dirty = False
        self._canvas.draw()
        width, height = self._canvas.get_width_height()
        image = QImage(self._canvas.buffer_rgba(), width, height, width * 4,
                       QImage.Format_RGBA8888).copy()
        image.setDevicePixelRatio(self._dpr)
        self._last_frame = image
        if self._scene is not None:
            self._image_url = self._scene.provider.accept(image)
            self.imageChanged.emit()

    def dispose(self):
        if self._disposed:
            return
        if self._scene is not None:
            self._scene.detach()
        self._disposed = True
        self._render_timer.stop()
        self._dirty = False
        self._last_frame = QImage()
