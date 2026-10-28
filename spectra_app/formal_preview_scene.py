"""Actual Quick scene attachment and private immutable SVG publication routes."""
from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer
import shiboken6


class FormalImageProvider(QQuickImageProvider):
    """Display rasterization only; retain at most two immutable byte revisions."""

    def __init__(self, route):
        super().__init__(QQuickImageProvider.Image)
        self.route = route
        self._revisions = {}
        self._revision = 0
        self._last_request = QSize()
        self._retired = False

    def accept(self, svg_bytes):
        if self._retired:
            return ''
        self._revision += 1
        self._revisions[str(self._revision)] = bytes(svg_bytes)
        self._revisions = dict(list(self._revisions.items())[-2:])
        return f'image://{self.route}/{self._revision}'

    def retire(self):
        self._retired = True
        self._revisions.clear()

    def requestImage(self, image_id, size, requested_size):
        svg_bytes = self._revisions.get(image_id)
        if svg_bytes is None or self._retired:
            return QImage()
        renderer = QSvgRenderer(QByteArray(svg_bytes))
        target = requested_size if requested_size.isValid() else renderer.defaultSize()
        target = QSize(max(1, target.width()), max(1, target.height()))
        image = QImage(target, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.white)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, target.width(), target.height()))
        painter.end()
        if size is not None:
            size.setWidth(target.width())
            size.setHeight(target.height())
        self._last_request = image.size()
        return image


class FormalPreviewScene(QObject):
    """Bind one controller to a real QQuickItem/window and a unique image sink.

    A hybrid adapter supplies its actual QWidget presentation report because
    QQuickWidget's internal render window need not be exposed. A pure Quick
    scene derives presentation from its real item and window.
    """

    def __init__(self, controller, engine, parent=None, *, hybrid_report=None):
        super().__init__(parent)
        if controller._disposed:
            raise RuntimeError('The formal preview controller has been disposed.')
        self.controller = controller
        self.engine = engine
        self.route = 'formal-page-' + uuid4().hex
        self.provider = FormalImageProvider(self.route)
        self._item = None
        self._window = None
        self._connections = []
        self._window_connections = []
        self._hybrid_report = hybrid_report
        self._retired = False
        engine.addImageProvider(self.route, self.provider)
        self.generation = controller.attach_scene(self.provider.accept, self._retire_route)
        # The document owner can disappear before a retained presentation item.
        # This route cleanup never calls back into the deleted controller.
        controller.destroyed.connect(self._retire_route)
        engine.destroyed.connect(self._engine_destroyed)
        self._lifetime_connections = [(controller.destroyed, self._retire_route),
                                      (engine.destroyed, self._engine_destroyed)]

    def bind_item(self, item):
        if self._retired:
            raise RuntimeError('The formal preview scene has been detached.')
        if self._item is not None:
            raise RuntimeError('A formal preview attachment binds exactly one item.')
        self._item = item
        for signal in (item.visibleChanged, item.enabledChanged, item.parentChanged,
                       item.widthChanged, item.heightChanged):
            signal.connect(self.refresh)
            self._connections.append((signal, self.refresh))
        item.windowChanged.connect(self._window_changed)
        item.destroyed.connect(self._item_destroyed)
        self._connections.extend(((item.windowChanged, self._window_changed),
                                  (item.destroyed, self._item_destroyed)))
        self._window_changed(item.window())

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
        self.controller._retire_gesture()
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

    def _engine_destroyed(self, *_):
        # The engine owns provider deletion. Do not remove it reentrantly while
        # the engine is being destroyed; retire the controller's sink instead.
        self.engine = None
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
        if self._retired:
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
        if self.engine is not None and shiboken6.isValid(self.engine):
            self.engine.removeImageProvider(self.route)
        self._item = self._window = None

    def detach(self):
        if not self._retired:
            if shiboken6.isValid(self.controller):
                self.controller.detach_scene(self.generation)
            self._retire_route()
