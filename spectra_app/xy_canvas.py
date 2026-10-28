"""Comparison Agg raster, physical-page viewport, and owner-checked Quick commands."""
from __future__ import annotations

from pathlib import Path

from matplotlib.backend_bases import ResizeEvent
from PySide6.QtCore import QObject, Property, QEvent, QSizeF, Qt, QUrl, Signal, Slot
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QSizePolicy

from .analysis_canvas import AnalysisFigureCanvas, AnalysisCanvasBridge
from .analysis_navigation import AnalysisNavigationController


class XYFigureCanvas(AnalysisFigureCanvas):
    """The actual scientific canvas; it owns no native widget."""


class XYNavigationController(AnalysisNavigationController):
    """Use the existing Qt-free Matplotlib gesture/history adapter."""

    def __init__(self, canvas):
        super().__init__(canvas, None)


class XYCommandsBridge(QObject):
    """Projection of real QActions and navigation, scoped to one cached page."""

    stateChanged = Signal()
    messageChanged = Signal()

    def __init__(self, page, toolbar, actions):
        super().__init__(page)
        self.page, self.toolbar = page, toolbar
        self._actions = actions
        self._epoch = 0
        self._closed = False
        self._last_identity = self._identity()
        toolbar.bridge = self
        for action in actions.values():
            action.changed.connect(self.stateChanged)

    def _identity(self):
        from shiboken6 import isValid
        p = self.page
        if not isValid(p):
            return ('deleted',)
        owner = p.workspace.comparisons.get(p.comparison_id)
        sources = tuple(sorted((sheet_id, id(sheet), id(getattr(sheet, 'data', None)))
                               for sheet_id in {curve.sheet_id for curve in owner.curves.values()}
                               if (sheet := p.workspace.sheets.get(sheet_id)) is not None)) if owner else ()
        return id(owner), sources, p._scene_generation

    @Property(str, notify=stateChanged)
    def ownerToken(self):
        return f'xy:{self.page.comparison_id}:{self._identity()!r}|epoch:{self._epoch}'

    def allowed(self, owner=None):
        from shiboken6 import isValid
        p = self.page
        return (not self._closed and isValid(p) and p.presentation_eligible()
                and p.comparison_id in p.workspace.comparisons
                and (owner is None or owner == self.ownerToken))

    def authorize(self, owner):
        # Validate the captured token before any activation callback. A callback
        # may itself replace/activate/hide the document, so check again afterward.
        if not self.allowed(owner):
            return False
        guard = self.page._input_guard
        return (guard is None or bool(guard())) and self.allowed(owner)

    enabled = Property(bool, lambda s: s.allowed(), notify=stateChanged)
    backEnabled = Property(bool, lambda s: s.enabled and s.toolbar._nav_stack._pos > 0,
                           notify=stateChanged)
    forwardEnabled = Property(bool, lambda s: s.enabled and s.toolbar._nav_stack._pos
                              < len(s.toolbar._nav_stack) - 1, notify=stateChanged)
    mode = Property(str, lambda s: str(s.toolbar.mode), notify=stateChanged)
    message = Property(str, lambda s: s.toolbar._message, notify=messageChanged)
    selectionText = Property(str, lambda s: f'{len(s.page._selection_ids)} selected', notify=stateChanged)
    inspectorChecked = Property(bool, lambda s: s.page.inspector_action.isChecked(), notify=stateChanged)

    @Property('QVariantList', notify=stateChanged)
    def actions(self):
        return [dict(key=k, text=a.text().replace('&', ''), tooltip=a.toolTip(),
                     enabled=self.enabled and a.isEnabled(), checked=a.isChecked(),
                     checkable=a.isCheckable()) for k, a in self._actions.items()]

    def refresh(self):
        if self._closed:
            return
        identity = self._identity()
        if identity != self._last_identity:
            self._last_identity = identity
            self.retire()
        self.stateChanged.emit()

    def retire(self):
        self._epoch += 1
        inspector = getattr(self.page, 'inspector_bridge', None)
        if inspector is not None:
            inspector.suspendBlur()
            inspector.changed.emit()
        analysis = getattr(self.page, 'analysis_controller', None)
        if analysis is not None:
            analysis.bridge.retire_editors()
        self.toolbar.cancel_interaction()
        if self.toolbar.mode == 'pan/zoom':
            self.toolbar.pan()
        elif self.toolbar.mode == 'zoom rect':
            self.toolbar.zoom()
        self.toolbar.set_message('')
        canvas_bridge = getattr(self.page, 'canvas_bridge', None)
        if canvas_bridge is not None:
            canvas_bridge.retire_interaction()
        self.stateChanged.emit()

    @Slot(str, str, result=bool)
    def invoke(self, command, owner):
        if not self.authorize(owner):
            return False
        action = self._actions.get(command)
        if action is not None:
            if not action.isEnabled():
                return False
            action.trigger()
            return True
        commands = dict(home=self.toolbar.home, back=self.toolbar.back,
                        forward=self.toolbar.forward, pan=self.toolbar.pan, zoom=self.toolbar.zoom)
        if command not in commands or (command == 'back' and not self.backEnabled
                                      or command == 'forward' and not self.forwardEnabled):
            return False
        commands[command]()
        self.stateChanged.emit()
        return True

    def dispose(self):
        if not self._closed:
            self.retire()
            self._closed = True
            self.stateChanged.emit()


class XYCanvasBridge(AnalysisCanvasBridge):
    """The shared input/raster primitive with XY physical-page geometry."""

    pageGeometryChanged = Signal()

    def __init__(self, canvas, page, parent=None):
        super().__init__(canvas, None, parent)
        self.page = page
        self._offset = QSizeF(0, 0)
        self._last_frame = None
        self._sink = None
        self.geometryChanged.connect(self.pageGeometryChanged)
        self.bind_navigation(page.navigation_bridge)

    def attach_sink(self, provider):
        self._sink = self.provider = provider
        if self._last_frame is not None:
            self._image_url = provider.accept(self._last_frame)
            self.imageChanged.emit()

    def detach_sink(self, provider):
        if self._sink is provider:
            self._sink = None
            self._image_url = ''
            self.imageChanged.emit()

    def publish(self, image):
        if self._disposed:
            return
        self._last_frame = image.copy()
        if self._sink is not None:
            self._image_url = self._sink.accept(self._last_frame)
            self.imageChanged.emit()

    @Slot(float, float, int, result=bool)
    def setSceneViewport(self, width, height, generation):
        if (self._disposed or self.page._scene is None
                or generation != self.page._scene_generation):
            return False
        self.setViewport(width, height)
        return True

    contentX = Property(float, lambda s: s._offset.width(), notify=pageGeometryChanged)
    contentY = Property(float, lambda s: s._offset.height(), notify=pageGeometryChanged)

    def _resize_canvas(self, *, force=False):
        if self._disposed or self.page.comparison_id not in self.page.workspace.comparisons:
            return
        width, height = self._viewport.width(), self._viewport.height()
        presentation = self.page.comparison.presentation
        if presentation is not None:
            ratio = presentation.width_in / presentation.height_in
            if width / max(1, height) > ratio:
                width = max(1, round(height * ratio))
            else:
                height = max(1, round(width / ratio))
        size = QSizeF(width, height)
        offset = QSizeF((self._viewport.width()-width)/2, (self._viewport.height()-height)/2)
        if self._disposed or (size == self._content and offset == self._offset and not force):
            return
        self._content, self._offset = size, offset
        figure = self.canvas.figure
        ratio = self.canvas.device_pixel_ratio
        if presentation is not None:
            figure.set_dpi(width * ratio / presentation.width_in)
            figure.set_size_inches(presentation.width_in, presentation.height_in, forward=False)
        else:
            figure.set_dpi(100 * ratio)
            figure.set_size_inches(width / 100, height / 100, forward=False)
        ResizeEvent('resize_event', self.canvas)._process()
        self.geometryChanged.emit()
        self.canvas.draw_idle()

    def fit_page(self):
        self._resize_canvas(force=True)

    def _position(self, x, y):
        return super()._position(x - self.contentX, y - self.contentY)

    @Slot(str, float, float, int, int, int, bool, str, result=bool)
    def pointerEvent(self, kind, x, y, button, buttons, modifiers, doubleClick, owner):
        if not self.allowed(owner) or not self.page.navigation_bridge.authorize(
                self.page.navigation_bridge.ownerToken):
            if self._gesture_owner is not None:
                self.retire_interaction()
            return False
        self.canvas._key = '+'.join(self._modifiers(modifiers)) or None
        return super().pointerEvent(kind, x, y, button, buttons, modifiers, doubleClick, owner)

    @Slot(str, int, str, int, str, result=bool)
    def keyEvent(self, kind, key, text, modifiers, owner):
        if not self.allowed(owner) or not self.page.navigation_bridge.authorize(
                self.page.navigation_bridge.ownerToken):
            return False
        if kind == 'press' and modifiers & Qt.ControlModifier.value:
            command = None
            if key == Qt.Key_R.value:
                command = 'rescale'
            elif key == Qt.Key_Z.value:
                command = 'redo' if modifiers & Qt.ShiftModifier.value else 'undo'
            elif key == Qt.Key_Y.value:
                command = 'redo'
            if command is not None:
                action = self.page._canvas_actions.get(command)
                if action is None or not action.isEnabled():
                    return False
                action.trigger()
                return True
        if kind == 'press' and key == Qt.Key_Escape.value:
            if self.page._finish_curve_action():
                self.page._select_curve_rows(False)
            return True
        if kind == 'press' and key == Qt.Key_Delete.value:
            self.page._remove_selected()
            return True
        return super().keyEvent(kind, key, text, modifiers, owner)


class XYCanvasView(QQuickWidget):
    """Temporary native host; the scene graph presents the complete XY viewport."""

    def __init__(self, page):
        super().__init__()
        self.setObjectName('XYCanvasHost')
        self.setAccessibleName('Comparison scientific canvas')
        self.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(120, 120)
        self.setFocusPolicy(Qt.StrongFocus)
        self.page = page
        self.bridge = page.controller.canvas_bridge
        from .xy_canvas_scene import XYCanvasScene
        self.scene = XYCanvasScene(page.controller, self.engine(), self,
            hybrid_report=(False, self.isEnabled()), prompt_window=self._prompt_window)
        self._focus_owner = ''
        self.setInitialProperties({'bridge': self.bridge})
        self.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / 'XYCanvas.qml')))
        if self.status() == QQuickWidget.Error:
            raise RuntimeError('Cannot load XY canvas: ' + '\n'.join(e.toString() for e in self.errors()))
        self.scene.bind_item(self.rootObject())
        self.bridge.set_device_pixel_ratio(self.devicePixelRatioF())

    def _prompt_window(self):
        self.window().winId()
        return self.window().windowHandle()

    def report_presentation(self):
        if hasattr(self, 'scene'):
            self.scene.report_hybrid(self.isVisible(), self.isEnabled())

    def fit_page(self):
        self.bridge.fit_page()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'bridge'):
            self.bridge.set_device_pixel_ratio(self.devicePixelRatioF())

    def showEvent(self, event):
        super().showEvent(event)
        self.bridge.set_device_pixel_ratio(self.devicePixelRatioF())
        self.report_presentation()
        self.bridge.page.refresh_input_state()

    def hideEvent(self, event):
        if hasattr(self, 'bridge'):
            self.scene.report_hybrid(False, self.isEnabled())
            self.bridge.page.retire_input()
        super().hideEvent(event)

    def event(self, event):
        if hasattr(self, 'bridge'):
            shortcut = (event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress)
                        and event.modifiers() & Qt.ControlModifier
                        and event.key() in (Qt.Key_R, Qt.Key_Z, Qt.Key_Y))
            if shortcut:
                # The same canonical QAction is attached to this host, but Qt's
                # Shortcut event has no captured page identity. Claim the key
                # and dispatch through the guarded source-focus boundary.
                if event.type() == QEvent.KeyPress:
                    self.bridge.keyEvent('press', event.key(), event.text(),
                        event.modifiers().value, self._focus_owner)
                event.accept()
                return True
            if event.type() in (QEvent.FocusIn, QEvent.MouseButtonPress):
                self._focus_owner = self.bridge.ownerToken
        result = super().event(event)
        if hasattr(self, 'bridge'):
            if event.type() == QEvent.DevicePixelRatioChange:
                self.bridge.set_device_pixel_ratio(self.devicePixelRatioF())
            elif event.type() == QEvent.ParentChange:
                self.bridge.page.retire_input()
                self.report_presentation()
            elif event.type() == QEvent.EnabledChange:
                self.report_presentation()
        return result

    def dispose(self):
        self.scene.detach()
        self.setSource(QUrl())

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)
