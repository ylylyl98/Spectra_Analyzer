"""Actual XY Quick attachment with an isolated immutable Agg image route."""
from __future__ import annotations
from uuid import uuid4
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider
import shiboken6


class XYImageProvider(QQuickImageProvider):
    """Each attachment retains at most three detached immutable raster frames."""
    def __init__(self, route):
        super().__init__(QQuickImageProvider.Image)
        self.route = route
        self.images = {}
        self.revision = 0
        self._retired = False

    def accept(self, image):
        if self._retired:
            return ''
        self.revision += 1
        self.images[str(self.revision)] = image.copy()
        self.images = dict(list(self.images.items())[-3:])
        return f'image://{self.route}/{self.revision}'

    def requestImage(self, image_id, size, requested_size):
        image = QImage(self.images.get(image_id, QImage())) if not self._retired else QImage()
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image

    def retire(self):
        self._retired = True
        self.images.clear()


class XYCanvasScene(QObject):
    """Bind one controller to a real QQuickItem/window and a unique image sink.

    A hybrid adapter supplies its actual QWidget presentation report because
    QQuickWidget's internal render window need not be exposed. A pure Quick
    scene derives presentation from its real item and window.
    """

    def __init__(self, controller, engine, parent=None, *, hybrid_report=None, prompt_window=None):
        super().__init__(parent)
        if controller._disposed:
            raise RuntimeError('The XY canvas controller has been disposed.')
        self.controller = controller
        self.engine = engine
        self.route = 'xy-canvas-' + uuid4().hex
        self.provider = XYImageProvider(self.route)
        self._item = None
        self._window = None
        self._connections = []
        self._window_connections = []
        self._prompt_window = prompt_window
        self._hybrid_report = hybrid_report
        self._retired = False
        engine.addImageProvider(self.route, self.provider)
        self.generation = controller.attach_scene(self)
        controller.canvas_bridge.attach_sink(self.provider)
        # The document owner can disappear before a retained presentation item.
        # This route cleanup never calls back into the deleted controller.
        self._lifetime_connections = [(controller.destroyed, self._retire_route),
                                      (engine.destroyed, self._engine_destroyed)]
        controller.destroyed.connect(self._retire_route)
        engine.destroyed.connect(self._engine_destroyed)

    def bind_item(self, item):
        if self._retired:
            raise RuntimeError('The XY canvas scene has been detached.')
        if self._item is not None:
            raise RuntimeError('An XY canvas attachment binds exactly one item.')
        self._item = item
        item.setProperty('attachmentGeneration', self.generation)
        for signal in (item.visibleChanged, item.enabledChanged,
                       item.widthChanged, item.heightChanged):
            signal.connect(self.refresh)
            self._connections.append((signal, self.refresh))
        item.parentChanged.connect(self._parent_changed)
        self._connections.append((item.parentChanged, self._parent_changed))
        item.windowChanged.connect(self._window_changed)
        item.destroyed.connect(self._item_destroyed)
        self._connections.extend(((item.windowChanged, self._window_changed),
                                  (item.destroyed, self._item_destroyed)))
        self._window_changed(item.window())

    def _parent_changed(self, *_):
        if self._retired:
            return
        self.controller.retire_input()
        self.refresh()

    def _window_changed(self, window):
        if self._window is not None and shiboken6.isValid(self._window):
            self._window.removeEventFilter(self)
        self._disconnect(self._window_connections)
        self._window = window
        if window is not None:
            window.installEventFilter(self)
            for signal in (window.visibleChanged, window.widthChanged, window.heightChanged,
                           window.screenChanged):
                signal.connect(self.refresh)
                self._window_connections.append((signal, self.refresh))
            window.destroyed.connect(self._window_destroyed)
            self._window_connections.append((window.destroyed, self._window_destroyed))
        # Losing or changing the actual window always retires the previous
        # gesture, even if both windows happen to be visible and enabled.
        self.controller.retire_input()
        self.refresh()

    def eventFilter(self, watched, event):
        if watched is self._window and event.type() == QEvent.DevicePixelRatioChange:
            self.refresh()
        return super().eventFilter(watched, event)

    def _window_destroyed(self, *_):
        self._window_connections.clear()
        self._window = None
        self.refresh()

    def _item_destroyed(self, *_):
        self._connections.clear()
        self._item = None
        self.detach()

    @staticmethod
    def _disconnect(connections):
        for signal, callback in connections:
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        connections.clear()

    def report_hybrid(self, visible, enabled):
        self._hybrid_report = bool(visible), bool(enabled)
        self.refresh()

    def refresh(self, *_):
        if self._retired or not shiboken6.isValid(self.controller):
            return
        item, window = self._item, self._window
        attached = (item is not None and shiboken6.isValid(item)
                    and window is not None and shiboken6.isValid(window)
                    and item.window() is window)
        visible = attached and item.isVisible()
        enabled = attached and item.isEnabled()
        if self._hybrid_report is None:
            visible = visible and window.isVisible() if attached else False
        else:
            visible = visible and self._hybrid_report[0]
            enabled = enabled and self._hybrid_report[1]
        self.controller.update_scene(self.generation, attached=attached,
            visible=visible, enabled=enabled,
            viewport_size=(item.width(), item.height()) if attached else (0., 0.),
            device_pixel_ratio=window.devicePixelRatio() if attached else 1.)

    def _retire_route(self):
        if self._retired:
            return
        self._retired = True
        self._disconnect(self._lifetime_connections)
        self._disconnect(self._connections)
        if self._window is not None and shiboken6.isValid(self._window):
            self._window.removeEventFilter(self)
        self._disconnect(self._window_connections)
        if shiboken6.isValid(self.provider):
            self.provider.retire()
        if shiboken6.isValid(self.engine):
            self.engine.removeImageProvider(self.route)
        self._item = self._window = None

    def _engine_destroyed(self, *_):
        self.detach()

    def detach(self):
        if not self._retired:
            if shiboken6.isValid(self.controller):
                self.controller.canvas_bridge.detach_sink(self.provider)
                self.controller.detach_scene(self.generation)
            self._retire_route()

    def prompt_window(self):
        if self._retired or self._window is None or not shiboken6.isValid(self._window):
            raise ValueError('An attached XY presentation window is required.')
        # Hybrid hosts supply the actual outer window; never use a render window
        # as the transient parent for application dialogs.
        window = self._prompt_window() if callable(self._prompt_window) else self._prompt_window
        return window if window is not None else self._window
