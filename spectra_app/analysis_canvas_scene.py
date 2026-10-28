"""Bind the DAT bridge to an actual Quick item/window and private raster route."""
from __future__ import annotations

from uuid import uuid4
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider
from shiboken6 import isValid


class CanvasImageProvider(QQuickImageProvider):
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


class AnalysisCanvasScene(QObject):
    def __init__(self, controller, engine, parent=None, *, hybrid_report=None):
        super().__init__(parent)
        if controller._disposed:
            raise RuntimeError('The DAT canvas controller has been disposed.')
        self.controller, self.engine = controller, engine
        self.route = 'dat-canvas-' + uuid4().hex
        self.provider = CanvasImageProvider(self.route)
        self._item = self._window = None
        self._connections, self._window_connections = [], []
        self._owner_connections = []
        self._hybrid_report = hybrid_report
        self._retired = False
        self._menu_keys = set()
        engine.installEventFilter(self)
        engine.addImageProvider(self.route, self.provider)
        self.generation = controller.attach_scene(self)
        controller.destroyed.connect(self._retire_route)
        engine.destroyed.connect(self._engine_destroyed)
        self._owner_connections.extend(((controller.destroyed, self._retire_route),
                                        (engine.destroyed, self._engine_destroyed)))

    def bind_item(self, item):
        if self._retired or self._item is not None:
            raise RuntimeError('A DAT attachment binds exactly one live item.')
        self._item = item
        item.setProperty('attachmentGeneration', self.generation)
        for signal in (item.visibleChanged, item.enabledChanged,
                       item.widthChanged, item.heightChanged):
            signal.connect(self.refresh)
            self._connections.append((signal, self.refresh))
        item.windowChanged.connect(self._window_changed)
        item.parentChanged.connect(self._parent_changed)
        item.destroyed.connect(self._item_destroyed)
        self._connections.extend(((item.windowChanged, self._window_changed),
                                  (item.parentChanged, self._parent_changed),
                                  (item.destroyed, self._item_destroyed)))
        self._window_changed(item.window())

    def _parent_changed(self, *_):
        if not self._retired and isValid(self.controller):
            self.controller.retire_interaction()
            self.refresh()

    @staticmethod
    def _disconnect(connections):
        for signal, callback in connections:
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        connections.clear()

    def _window_changed(self, window):
        if self._retired:
            return
        if self._window is not None and isValid(self._window):
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
        if isValid(self.controller):
            self.controller.retire_interaction()
        self.refresh()

    def eventFilter(self, watched, event):
        if watched is self.engine and event.type() == QEvent.DeferredDelete:
            self.detach()
        if watched is self._window and isValid(self.controller):
            # The popup can close on a key press and return focus to the
            # scientific item before its release reaches the Quick window.
            if event.type() == QEvent.KeyPress and self.controller.peakMenu:
                self._menu_keys.add(event.key())
            elif event.type() == QEvent.KeyRelease and event.key() in self._menu_keys:
                self._menu_keys.discard(event.key())
                if not self.controller.peakMenu:
                    event.accept()
                    return True
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

    def _engine_destroyed(self, *_):
        # Providers are owned and deleted by the engine. Retire controller
        # state before any future publication can enter that deleted provider.
        self.engine = None
        self.detach()

    def report_hybrid(self, visible, enabled):
        self._hybrid_report = bool(visible), bool(enabled)
        self.refresh()

    def refresh(self, *_):
        if self._retired or not isValid(self.controller):
            return
        item, window = self._item, self._window
        attached = (item is not None and isValid(item) and window is not None
                    and isValid(window) and item.window() is window)
        visible = attached and item.isVisible()
        enabled = attached and item.isEnabled()
        if self._hybrid_report is None:
            visible = visible and window.isVisible() if attached else False
        else:
            visible = visible and self._hybrid_report[0]
            enabled = enabled and self._hybrid_report[1]
        self.controller.update_scene(self.generation, attached=attached, visible=visible,
            enabled=enabled, viewport_size=(item.width(), item.height()) if attached else (0., 0.),
            device_pixel_ratio=window.devicePixelRatio() if attached else 1.)

    def _retire_route(self, *_):
        if self._retired:
            return
        self._retired = True
        self._disconnect(self._owner_connections)
        self._disconnect(self._connections)
        if self._window is not None and isValid(self._window):
            self._window.removeEventFilter(self)
        self._disconnect(self._window_connections)
        if isValid(self.provider):
            self.provider.retire()
        if self.engine is not None and isValid(self.engine):
            self.engine.removeEventFilter(self)
            self.engine.removeImageProvider(self.route)
        self._item = self._window = None

    def detach(self):
        if not self._retired:
            if isValid(self.controller):
                self.controller.detach_scene(self.generation)
            self._retire_route()
