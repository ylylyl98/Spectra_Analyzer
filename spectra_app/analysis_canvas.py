"""Ordinary DAT Agg renderer and explicit Qt Quick presentation/input boundary."""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
import math

from matplotlib.backend_bases import Event, KeyEvent, MouseButton, MouseEvent, ResizeEvent
from matplotlib.backend_tools import Cursors
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PySide6.QtCore import QObject, Property, QEvent, QSizeF, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QSizePolicy
from shiboken6 import isValid


@dataclass(frozen=True)
class CanvasPointerContext:
    owner: str
    anchor_x: float
    anchor_y: float


@dataclass(frozen=True)
class CanvasMenuRequest:
    request_id: str
    pointer: CanvasPointerContext


@dataclass(frozen=True)
class PeakMenuRequest(CanvasMenuRequest):
    session: object
    graph: object
    cube: object
    axes: object
    gate: int
    energy_index: int
    energy: float
    label: int
    override: int | None
    epoch: int
    targets: object
    target_indices: tuple[int, ...]


class _CanvasImages(QQuickImageProvider):
    """URLs denote immutable detached frames; bound retained raster memory."""

    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.images = {}
        self.revision = 0

    def accept(self, image):
        self.revision += 1
        self.images[str(self.revision)] = image.copy()
        self.images = dict(list(self.images.items())[-3:])
        return f'image://analysis-canvas/{self.revision}'

    def requestImage(self, image_id, size, requested_size):
        image = QImage(self.images.get(image_id, QImage()))
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image


class AnalysisFigureCanvas(FigureCanvasAgg):
    """Real scientific Agg canvas, with one GUI-thread idle render timer."""

    def __init__(self, figure):
        super().__init__(figure)
        self.view_bridge = None
        self._dirty = True
        self._drawing = False
        self._disposed = False
        self._lastx = self._lasty = None
        self._render_timer = QTimer()
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(16)
        self._render_timer.timeout.connect(self._render_if_visible)

    def draw_idle(self, *args, **kwargs):
        if self._disposed:
            return
        self._dirty = True
        if (self.view_bridge is not None and self.view_bridge.visible
                and not self._render_timer.isActive()):
            self._render_timer.start()

    def _render_if_visible(self):
        if (not self._disposed and self._dirty and self.view_bridge is not None
                and self.view_bridge.visible):
            self.draw()

    def draw(self):
        if self._disposed:
            return
        if self._drawing:
            self.draw_idle()
            return
        self._render_timer.stop()
        self._dirty = False
        self._drawing = True
        try:
            super().draw()
            if self.view_bridge is not None:
                width, height = self.get_width_height(physical=True)
                frame = QImage(self.buffer_rgba(), width, height, width * 4,
                               QImage.Format_RGBA8888).copy()
                frame.setDevicePixelRatio(self.device_pixel_ratio)
                self.view_bridge.publish(frame)
        finally:
            self._drawing = False
            # draw_event callbacks may request another frame; do not consume it
            # merely because the current frame was successfully published.
            if self._dirty:
                self.draw_idle()

    def set_cursor(self, cursor):
        shapes = {Cursors.POINTER: Qt.ArrowCursor, Cursors.HAND: Qt.PointingHandCursor,
                  Cursors.SELECT_REGION: Qt.CrossCursor, Cursors.MOVE: Qt.SizeAllCursor,
                  Cursors.WAIT: Qt.WaitCursor, Cursors.RESIZE_HORIZONTAL: Qt.SizeHorCursor,
                  Cursors.RESIZE_VERTICAL: Qt.SizeVerCursor}
        if self.view_bridge is not None:
            self.view_bridge.set_cursor(shapes.get(cursor, Qt.ArrowCursor))

    def dispose(self):
        if not self._disposed:
            self._disposed = True
            self._render_timer.stop()
            self._dirty = False
            Event('close_event', self)._process()


class AnalysisCanvasBridge(QObject):
    """View geometry and Matplotlib events; no parallel scientific model."""

    imageChanged = Signal()
    geometryChanged = Signal()
    stateChanged = Signal()
    cursorChanged = Signal()
    rubberbandChanged = Signal()
    viewportChanged = Signal()
    peakMenuChanged = Signal()
    peakMenuFocusRequested = Signal(str)

    def __init__(self, canvas, window=None, parent=None):
        super().__init__(parent)
        self.canvas, self.window = canvas, window
        canvas.view_bridge = self
        self.provider = _CanvasImages()
        self._image_url = ''
        self._viewport = QSizeF(1, 1)
        self._content = QSizeF(*canvas.get_width_height())
        self._minimum = QSizeF(1, 1)
        self._visible = False
        self._disposed = False
        self._epoch = 0
        self._navigation = None
        self._last_owner = ''
        self._last_allowed = False
        self._gesture_owner = None
        self._held_buttons = set()
        self._peak_menu_request = None
        self._peak_menu_ready = False
        self._peak_menu_serial = 0
        self._cursor = int(Qt.ArrowCursor.value)
        self._rubberband = dict(visible=False, x=0., y=0., width=0., height=0.)

    imageUrl = Property(str, lambda self: self._image_url, notify=imageChanged)
    contentWidth = Property(float, lambda self: self._content.width(), notify=geometryChanged)
    contentHeight = Property(float, lambda self: self._content.height(), notify=geometryChanged)
    viewportWidth = Property(float, lambda self: self._viewport.width(), notify=geometryChanged)
    viewportHeight = Property(float, lambda self: self._viewport.height(), notify=geometryChanged)
    devicePixelRatio = Property(float, lambda self: self.canvas.device_pixel_ratio, notify=geometryChanged)
    cursorShape = Property(int, lambda self: self._cursor, notify=cursorChanged)
    rubberband = Property('QVariantMap', lambda self: dict(self._rubberband), notify=rubberbandChanged)

    @property
    def visible(self):
        return self._visible and not self._disposed

    @Property(str, notify=stateChanged)
    def ownerToken(self):
        source = self._navigation.ownerToken if self._navigation is not None else 'standalone'
        return f'{source}|canvas:{self._epoch}'

    def allowed(self, owner=None):
        return (isValid(self) and self.visible and (self._navigation is None or self._navigation.allowed())
                and (owner is None or owner == self.ownerToken))

    enabled = Property(bool, lambda self: self.allowed(), notify=stateChanged)

    @Property('QVariantMap', notify=peakMenuChanged)
    def peakMenu(self):
        request = self._peak_menu_request
        if request is None or not self._peak_menu_ready:
            return {}
        peak = isinstance(request, PeakMenuRequest)
        return dict(requestId=request.request_id, owner=request.pointer.owner, hasPeak=peak,
                    anchorX=request.pointer.anchor_x, anchorY=request.pointer.anchor_y,
                    labelText=f'Set Label…  (current: {request.label})' if peak else '')

    @Property('QVariantMap', notify=stateChanged)
    def formatCommands(self):
        return {action['key']: action['enabled'] for action in self._navigation.actions
                if action['key'] in ('copy_svg', 'copy_png', 'settings_copy', 'settings_paste', 'settings_paste_to', 'settings_paste_options', 'settings_adjust', 'settings_undo_paste')} if self._navigation is not None else {}

    def request_format_menu(self, pointer):
        if isinstance(pointer, CanvasPointerContext) and self.allowed(pointer.owner) and self._navigation is not None:
            self.request_peak_menu(CanvasMenuRequest(self.next_peak_menu_id(), pointer))

    def _invoke_format_command(self, key, owner):
        if not self.allowed(owner) or self._navigation is None:
            return False
        return self._navigation.invoke(key, self._navigation.ownerToken)

    def next_peak_menu_id(self):
        self._peak_menu_serial += 1
        return str(self._peak_menu_serial)

    def request_peak_menu(self, request):
        self.clear_peak_menu()
        self._peak_menu_request = request

    def clear_peak_menu(self):
        if self._peak_menu_request is not None:
            self._peak_menu_request = None
            self._peak_menu_ready = False
            self.peakMenuChanged.emit()

    @Slot(str, str, result=bool)
    def dismissPeakMenu(self, request_id, owner):
        request = self._peak_menu_request
        if request is None or request.request_id != request_id or request.pointer.owner != owner:
            return False
        self.clear_peak_menu()
        return True

    @Slot(str, result=bool)
    def restorePeakMenuFocus(self, owner):
        if not self.allowed(owner) or self._peak_menu_request is not None:
            return False
        self.peakMenuFocusRequested.emit(owner)
        return True

    @Slot(str, str, str, result=bool)
    def choosePeakMenu(self, request_id, key, owner):
        request = self._peak_menu_request
        if (key not in ('set_label', 'delete', 'copy_svg', 'copy_png', 'settings_copy', 'settings_paste', 'settings_paste_to', 'settings_paste_options', 'settings_adjust', 'settings_undo_paste')
                or request is None or not self._peak_menu_ready
                or request.request_id != request_id or request.pointer.owner != owner):
            return False
        self.clear_peak_menu()  # Consume before the prompt's nested event loop.
        if key in ('copy_svg', 'copy_png', 'settings_copy', 'settings_paste', 'settings_paste_to', 'settings_paste_options', 'settings_adjust', 'settings_undo_paste'):
            accepted = self._invoke_format_command(key, owner)
            self.restorePeakMenuFocus(owner)
            return accepted
        if not isinstance(request, PeakMenuRequest) or self.window is None or not self.window._peak_menu_request_valid(request):
            self.restorePeakMenuFocus(owner)
            return False
        self.window._continue_peak_menu(request, key)
        self.restorePeakMenuFocus(owner)
        return True

    def bind_navigation(self, navigation):
        self._navigation = navigation
        navigation.stateChanged.connect(self.refresh)
        self._last_owner = navigation.ownerToken
        self._last_allowed = navigation.allowed()
        self.refresh()

    def refresh(self):
        owner = self._navigation.ownerToken if self._navigation is not None else 'standalone'
        active = self._navigation.allowed() if self._navigation is not None else True
        if owner != self._last_owner or active != self._last_allowed:
            self.retire_interaction()
        self._last_owner, self._last_allowed = owner, active
        self.stateChanged.emit()

    def set_visible(self, visible):
        if bool(visible) == self._visible or self._disposed:
            return
        self._visible = bool(visible)
        if not visible:
            self.canvas._render_timer.stop()
            self.retire_interaction()
        elif self.canvas._dirty:
            self.canvas.draw_idle()
        self.stateChanged.emit()

    def publish(self, image):
        if not self._disposed:
            self._image_url = self.provider.accept(image)
            self.imageChanged.emit()

    @Slot(float, float)
    def setViewport(self, width, height):
        if self._disposed or width < 1 or height < 1:
            return
        size = QSizeF(round(width), round(height))
        if size == self._viewport:
            return
        self._viewport = size
        self._resize_canvas()
        self.geometryChanged.emit()
        self.viewportChanged.emit()

    def set_content_minimum(self, width, height):
        self._minimum = QSizeF(max(1, round(width)), max(1, round(height)))
        self._resize_canvas()

    def set_device_pixel_ratio(self, ratio):
        if self._disposed or ratio <= 0:
            return
        if self.canvas._set_device_pixel_ratio(float(ratio)):
            self._resize_canvas(force=True)

    def _resize_canvas(self, *, force=False):
        size = QSizeF(max(self._minimum.width(), self._viewport.width()),
                      max(self._minimum.height(), self._viewport.height()))
        if self._disposed or (size == self._content and not force):
            return
        self._content = size
        dpi = self.canvas.figure._original_dpi
        self.canvas.figure.set_size_inches(size.width() / dpi, size.height() / dpi, forward=False)
        ResizeEvent('resize_event', self.canvas)._process()
        self.geometryChanged.emit()
        self.canvas.draw_idle()

    def set_cursor(self, shape):
        shape = int(shape.value) if hasattr(shape, 'value') else int(shape)
        if shape != self._cursor:
            self._cursor = shape
            self.cursorChanged.emit()

    def set_rubberband(self, rect):
        if rect is None:
            value = dict(visible=False, x=0., y=0., width=0., height=0.)
        else:
            x, y, width, height = [float(v) / self.devicePixelRatio for v in rect]
            value = dict(visible=True, x=min(x, x + width), y=min(y, y + height),
                         width=abs(width), height=abs(height))
        if value != self._rubberband:
            self._rubberband = value
            self.rubberbandChanged.emit()

    def retire_interaction(self):
        self._epoch += 1
        self.clear_peak_menu()
        self._gesture_owner = None
        self._held_buttons.clear()
        self.canvas._button = None
        self.canvas._key = None
        if self._navigation is not None:
            self._navigation.toolbar.cancel_interaction()
        if self.window is not None:
            self.window._cancel_canvas_gesture()
        self.set_rubberband(None)
        self.stateChanged.emit()

    @Slot()
    def cancelGesture(self):
        self.retire_interaction()

    @staticmethod
    def _modifiers(flags):
        return [name for bit, name in ((Qt.ControlModifier.value, 'ctrl'),
                                      (Qt.AltModifier.value, 'alt'),
                                      (Qt.ShiftModifier.value, 'shift'),
                                      (Qt.MetaModifier.value, 'super')) if flags & bit]

    @staticmethod
    def _buttons(flags):
        return {button for bit, button in ((Qt.LeftButton.value, MouseButton.LEFT),
                                          (Qt.MiddleButton.value, MouseButton.MIDDLE),
                                          (Qt.RightButton.value, MouseButton.RIGHT),
                                          (Qt.BackButton.value, MouseButton.BACK),
                                          (Qt.ForwardButton.value, MouseButton.FORWARD)) if flags & bit}

    def _position(self, x, y):
        return (x * self.devicePixelRatio,
                self.canvas.get_width_height(physical=True)[1] - y * self.devicePixelRatio)

    @Slot(str, float, float, int, int, int, bool, str, result=bool)
    @Slot(str, float, float, int, int, int, bool, str, float, float, result=bool)
    def pointerEvent(self, kind, x, y, button, buttons, modifiers, doubleClick, owner,
                     anchor_x=None, anchor_y=None):
        if kind not in ('press', 'move', 'release', 'enter', 'leave') or not self.allowed(owner):
            if self._gesture_owner is not None and (not self.allowed()
                    or self._gesture_owner != self.ownerToken):
                self.retire_interaction()
            return False
        if kind == 'press':
            if self._gesture_owner is not None and owner != self._gesture_owner:
                return False
            self._gesture_owner = owner
            self.clear_peak_menu()
        elif kind == 'release' or kind == 'move' and buttons:
            if self._gesture_owner is None or owner != self._gesture_owner:
                return False
        physical_x, physical_y = self._position(x, y)
        context = (CanvasPointerContext(owner, float(anchor_x), float(anchor_y))
                   if anchor_x is not None and anchor_y is not None
                   and math.isfinite(anchor_x) and math.isfinite(anchor_y) else None)
        held = self._buttons(buttons)
        mpl_button = next(iter(self._buttons(button)), None)
        if kind in ('enter', 'leave'):
            from matplotlib.backend_bases import LocationEvent
            name = 'figure_enter_event' if kind == 'enter' else 'figure_leave_event'
            event = LocationEvent(name, self.canvas, physical_x, physical_y)
        else:
            name = dict(press='button_press_event', move='motion_notify_event',
                        release='button_release_event')[kind]
            if kind == 'press':
                self.canvas._button = mpl_button
            self._held_buttons = held
            event = MouseEvent(name, self.canvas, physical_x, physical_y,
                               button=mpl_button if kind != 'move' else self.canvas._button,
                               key=self.canvas._key, dblclick=doubleClick,
                               buttons=held if kind == 'move' else None,
                               modifiers=self._modifiers(modifiers), guiEvent=context)
        event._process()
        self.canvas._lastx, self.canvas._lasty = physical_x, physical_y
        if kind == 'release':
            self.canvas._button = next(iter(held), None)
            if held and mpl_button == MouseButton.RIGHT:
                self.clear_peak_menu()
            if not held:
                self._gesture_owner = None
                request = self._peak_menu_request
                if (mpl_button == MouseButton.RIGHT and request is not None
                        and request.pointer.owner == owner and self.allowed(owner)):
                    self._peak_menu_ready = True
                    self.peakMenuChanged.emit()
                elif request is not None:
                    self.clear_peak_menu()
        return True

    @Slot(str, int, str, int, str, result=bool)
    def keyEvent(self, kind, key, text, modifiers, owner):
        if kind not in ('press', 'release') or not self.allowed(owner):
            return False
        special = {Qt.Key_Escape.value: 'escape', Qt.Key_Tab.value: 'tab',
                   Qt.Key_Backspace.value: 'backspace', Qt.Key_Return.value: 'enter',
                   Qt.Key_Enter.value: 'enter', Qt.Key_Insert.value: 'insert',
                   Qt.Key_Delete.value: 'delete', Qt.Key_Home.value: 'home',
                   Qt.Key_End.value: 'end', Qt.Key_Left.value: 'left', Qt.Key_Up.value: 'up',
                   Qt.Key_Right.value: 'right', Qt.Key_Down.value: 'down',
                   Qt.Key_PageUp.value: 'pageup', Qt.Key_PageDown.value: 'pagedown',
                   Qt.Key_Control.value: 'control', Qt.Key_Shift.value: 'shift',
                   Qt.Key_Alt.value: 'alt', Qt.Key_Meta.value: 'super'}
        value = special.get(key)
        mods = self._modifiers(modifiers)
        if value is None:
            if Qt.Key_F1.value <= key <= Qt.Key_F35.value:
                value = f'f{key - Qt.Key_F1.value + 1}'
            elif 0 <= key <= 0x10ffff:
                value = chr(key)
                if 'shift' in mods:
                    mods.remove('shift')
                else:
                    value = value.lower()
            else:
                return False
        own_modifier = {'control': 'ctrl', 'shift': 'shift', 'alt': 'alt', 'super': 'super'}.get(value)
        if own_modifier in mods:
            mods.remove(own_modifier)
        value = '+'.join(mods + [value])
        name = 'key_press_event' if kind == 'press' else 'key_release_event'
        if kind == 'press':
            self.canvas._key = value
        KeyEvent(name, self.canvas, value, self.canvas._lastx, self.canvas._lasty)._process()
        if kind == 'release':
            self.canvas._key = None
        return True

    @Slot(float, float, float, int, str, result=bool)
    def scrollEvent(self, x, y, step, modifiers, owner):
        if not self.allowed(owner):
            return False
        x, y = self._position(x, y)
        MouseEvent('scroll_event', self.canvas, x, y, step=step,
                   key=self.canvas._key, modifiers=self._modifiers(modifiers))._process()
        return True

    def dispose(self):
        if self._disposed:
            return
        self.retire_interaction()
        self._disposed = True
        self._visible = False
        self.canvas.dispose()
        self.provider.images.clear()
        self._image_url = ''
        self.imageChanged.emit()
        self.stateChanged.emit()


class AnalysisCanvasView(QQuickWidget):
    """Temporary host; Quick owns visible pixels, scrolling, focus and input."""

    def __init__(self, canvas, window, parent=None, *, controller=None):
        super().__init__(parent)
        from .dat_canvas_controller import CanvasServices, DatCanvasController
        from .analysis_canvas_scene import AnalysisCanvasScene
        self.setObjectName('AnalysisCanvasHost')
        self.setAccessibleName('Ordinary DAT scientific canvas')
        self.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self._peak_menu_keys = set()
        self._owns_controller = controller is None
        self.bridge = controller or DatCanvasController(canvas, CanvasServices(
            validate_peak_menu=window._peak_menu_request_valid,
            continue_peak_menu=window._continue_peak_menu,
            cancel_canvas_gesture=window._cancel_canvas_gesture,
            schedule_view_layout=window._schedule_view_layout), self)
        self.scene = AnalysisCanvasScene(self.bridge, self.engine(), self,
                                        hybrid_report=(False, self.isEnabled()))
        self.setInitialProperties({'bridge': self.bridge,
                                   'attachmentGeneration': self.scene.generation})
        self.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / 'AnalysisCanvas.qml')))
        if self.status() == QQuickWidget.Error:
            raise RuntimeError('Cannot load ordinary DAT canvas: ' + '\n'.join(e.toString() for e in self.errors()))
        self.scene.bind_item(self.rootObject())

    def viewport_size(self):
        return QSizeF(self.bridge.viewportWidth, self.bridge.viewportHeight)

    def set_content_minimum(self, width, height):
        self.bridge.set_content_minimum(width, height)

    def set_cursor(self, shape):
        self.bridge.set_cursor(shape)

    def set_rubberband(self, rect):
        self.bridge.set_rubberband(rect)

    def retire_interaction(self):
        self.bridge.retire_interaction()

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
        # The canvas also has widget-scoped graph shortcuts (including Escape).
        # An item popup must receive those keys before QWidget shortcuts run.
        if (event.type() == QEvent.ShortcutOverride and hasattr(self, 'bridge')
                and self.bridge.peakMenu):
            event.accept()
            return True
        if event.type() == QEvent.KeyPress and hasattr(self, 'bridge') and self.bridge.peakMenu:
            self._peak_menu_keys.add(event.key())
        elif event.type() == QEvent.KeyRelease and event.key() in getattr(self, '_peak_menu_keys', ()):
            self._peak_menu_keys.discard(event.key())
            # The popup can close on the press and return focus to the canvas.
            # Keep that paired release out of the scientific event registry.
            menu = self.rootObject().findChild(QObject, 'analysisPeakMenu')
            if menu is None or not menu.property('visible'):
                event.accept()
                return True
        result = super().event(event)
        if event.type() in (QEvent.DevicePixelRatioChange, QEvent.EnabledChange) and hasattr(self, 'scene'):
            self.scene.report_hybrid(self.isVisible(), self.isEnabled())
        return result

    def dispose(self):
        if self._owns_controller:
            self.bridge.dispose()
        else:
            self.scene.detach()

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)
