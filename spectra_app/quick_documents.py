"""Real Quick document frames; retained identity and layout live in the registry."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QObject, Property,
                            QRect, Qt, QTimer, QUrl, Signal, Slot)
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem
import shiboken6

from .document_registry import DocumentRegistry, ParkedRegistry
from .formal_plot_controller import FormalPlotController
from .formal_preview_scene import FormalPreviewScene
from .workbook_page import WorkbookController
from .xy_canvas_scene import XYCanvasScene
from .xy_plot_controller import XYPlotController


@dataclass
class _Presentation:
    title: str
    rectangle: QRect | None = None
    maximized: bool = False
    frame: QQuickItem | None = None
    mount: DocumentMount | None = None


@dataclass
class ParkedQuickDocuments:
    registry: ParkedRegistry
    frames: tuple
    disposed: bool = False


class DocumentMount(QObject):
    """One disposable scene attachment, never an owner of its document."""

    def __init__(self, controller, engine, host, parent):
        super().__init__(parent)
        self.controller, self.scene, self.item = controller, None, None
        self._engine = engine
        engine.installEventFilter(self)
        if isinstance(controller, FormalPlotController):
            self.scene = FormalPreviewScene(controller.preview, engine, self)
            filename = 'FormalPreview.qml'
            properties = dict(bridge=controller.preview, attachmentGeneration=self.scene.generation)
            controller.set_prompt_window(lambda: self.item.window() if self.item is not None else None)
        elif isinstance(controller, WorkbookController):
            filename, properties = 'Workbook.qml', dict(bridge=controller)
        elif isinstance(controller, XYPlotController):
            self.scene = XYCanvasScene(controller, engine, self)
            filename, properties = 'XYCanvas.qml', dict(bridge=controller.canvas_bridge)
        else:
            raise TypeError('Quick documents require a Formal, Workbook or XY controller.')
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / filename)), self)
        if not component.isReady():
            self.detach()
            raise RuntimeError('\n'.join(error.toString() for error in component.errors()))
        self.item = component.createWithInitialProperties(properties)
        if self.item is None:
            self.detach()
            raise RuntimeError('\n'.join(error.toString() for error in component.errors()))
        self.item.setParent(host)
        self.item.setParentItem(host)
        self._host = host
        host.widthChanged.connect(self._resize)
        host.heightChanged.connect(self._resize)
        self._resize()
        if self.scene is not None:
            self.scene.bind_item(self.item)

    def _resize(self):
        if self.item is not None and shiboken6.isValid(self.item):
            self.item.setWidth(self._host.width())
            self.item.setHeight(self._host.height())

    def eventFilter(self, watched, event):
        # Tear down while QML bindings still exist. QQmlEngine.destroyed is
        # emitted after its declarative contexts have already been dismantled.
        if watched is self._engine and event.type() == QEvent.DeferredDelete:
            self.detach()
        return super().eventFilter(watched, event)

    def detach(self):
        if self.item is not None and shiboken6.isValid(self.item):
            window = self.item.window()
            if window is not None:
                # QQuickItem.window() assigns a Shiboken return-value parent.
                # The window outlives this disposable item: restore its actual
                # QObject ownership before deleting the item's wrapper tree.
                QObject.setParent(window, QObject.parent(window))
        if self._engine is not None and shiboken6.isValid(self._engine):
            self._engine.removeEventFilter(self)
        self._engine = None
        if self.scene is not None:
            self.scene.detach()
            self.scene = None
        if self.item is not None:
            if shiboken6.isValid(self._host):
                for signal in (self._host.widthChanged, self._host.heightChanged):
                    try:
                        signal.disconnect(self._resize)
                    except (RuntimeError, TypeError):
                        pass
        if self.item is not None and shiboken6.isValid(self.item):
            self.item.setParentItem(None)
            self.item.deleteLater()
        self.item = None


class DocumentFrames(QAbstractListModel):
    """Rows project registry open keys; roles contain presentation only."""
    _names = ('documentKey', 'documentTitle', 'frameX', 'frameY', 'frameWidth',
              'frameHeight', 'selected', 'frameMaximized', 'frameLayer', 'documentTooltip')

    def __init__(self, documents):
        super().__init__(documents)
        self.documents, self._keys = documents, ()

    def roleNames(self):
        return {Qt.UserRole + i: name.encode() for i, name in enumerate(self._names)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._keys)

    def data(self, index, role):
        if not index.isValid() or not 0 <= index.row() < len(self._keys):
            return None
        key = self._keys[index.row()]
        documents = self.documents
        if role == Qt.UserRole + self._names.index('documentTooltip'):
            return documents._tooltip(key)
        presentation = documents._presentations[key]
        rect = documents.display_rectangle(key)
        values = (list(key), presentation.title, rect.x(), rect.y(), rect.width(), rect.height(),
                  key == documents.registry.active_key, presentation.maximized,
                  documents._stacking().index(key))
        offset = role - Qt.UserRole
        return values[offset] if 0 <= offset < len(values) else None

    def refresh(self, *, reset=False):
        keys = self.documents.registry.open_keys()
        if reset:
            self.beginResetModel()
            self._keys = keys
            self.endResetModel()
        else:
            for index in reversed(range(len(self._keys))):
                if self._keys[index] not in keys:
                    self.beginRemoveRows(QModelIndex(), index, index)
                    self._keys = self._keys[:index] + self._keys[index + 1:]
                    self.endRemoveRows()
            for index, key in enumerate(keys):
                if key not in self._keys:
                    self.beginInsertRows(QModelIndex(), index, index)
                    self._keys = self._keys[:index] + (key,) + self._keys[index:]
                    self.endInsertRows()
        if keys:
            self.dataChanged.emit(self.index(0), self.index(len(keys) - 1))


class _RestorePlacementRequest(QObject):
    """Deliver one completed frame to the document owner's GUI thread."""

    def __init__(self, documents, window):
        super().__init__(documents)
        self.window = window
        self._connection = window.afterFrameEnd.connect(self._ready, Qt.QueuedConnection)
        window.update()

    def cancel(self):
        if self._connection is not None:
            QObject.disconnect(self._connection)
            self._connection = None
        self.deleteLater()

    @Slot()
    def _ready(self):
        if self._connection is None:
            return
        QObject.disconnect(self._connection)
        self._connection = None
        documents = self.parent()
        if documents._restore_pending is not self:
            return
        if (documents._window is self.window and documents._root_available()
                and documents.mode == 'free' and min(documents._viewport) > 0):
            documents._placement.start(0)
        else:
            documents._stop_restore_placement(preserve_pending=True)


class QuickDocuments(QObject):
    """Semantic document operations used directly by the application root.

    Application callbacks finish/activate domain owners. ``input_eligible`` is
    the application's Workspace/not-busy predicate; actual item/window state
    is additionally checked here. Call ``refresh_eligibility`` when it changes.
    """
    stateChanged = Signal()
    layoutChanged = Signal()
    workspaceChanged = Signal()
    mountFailed = Signal(str)

    def __init__(self, activate, finish_close, parent=None, *, input_eligible=None,
                 tooltip: Callable[[tuple], str] | None = None):
        super().__init__(parent)
        self._activate, self._finish_close = activate, finish_close
        self._tooltip = tooltip or (lambda key: '')
        self._host_input_eligible = input_eligible or (lambda: True)
        self.registry = DocumentRegistry(self._activate_input, self._input_eligible, self)
        # A fresh Quick workspace starts with one readable document. Restoring
        # an existing project still installs its explicit (or legacy) mode.
        self.registry.set_mode('tabbed')
        self.frames = DocumentFrames(self)
        self._presentations = {}
        self._viewport = (0, 0)
        self._scroll = (0, 0)
        self._root = self._window = None
        self._root_connections, self._window_connections = [], []
        self._restore_pending = False
        self._placement = QTimer(self)
        self._placement.setSingleShot(True)
        self._placement.timeout.connect(self._restore_placement)
        self._eligibility = {}

    model = Property(QObject, lambda self: self.frames, constant=True)
    mode = Property(str, lambda self: self.registry.mode, notify=stateChanged)
    count = Property(int, lambda self: len(self.registry.open_keys()), notify=stateChanged)
    contentWidth = Property(float, lambda self: max([self._viewport[0]] +
        [self.display_rectangle(key).right() + 12 for key in self.registry.open_keys()]), notify=stateChanged)
    contentHeight = Property(float, lambda self: max([self._viewport[1]] +
        [self.display_rectangle(key).bottom() + 12 for key in self.registry.open_keys()]), notify=stateChanged)

    def _stacking(self):
        return tuple(tuple(key) for key in self.registry.workspace_snapshot(self.registry.keys())['stacking'])

    def _publish(self, *, reset=False):
        self.frames.refresh(reset=reset)
        self.stateChanged.emit()

    def cache_document(self, key, controller, title=''):
        if not isinstance(controller, (FormalPlotController, WorkbookController, XYPlotController)):
            raise TypeError('Expected a retained document controller.')
        self.registry.register(key, controller)
        self._presentations.setdefault(key, _Presentation(title))
        self.refresh_document_input(key)

    def document_for(self, key):
        return self.registry.document_for(key)

    def cached_items(self):
        return self.registry.cached_items()

    def cached_documents(self):
        return self.registry.cached_documents()

    def keys(self):
        return self.registry.keys()

    def visible_keys(self):
        return self.registry.open_keys() if self.mode == 'free' else ((self.registry.active_key,)
            if self.registry.active_key is not None else ())

    def content_item(self, key):
        presentation = self._presentations.get(key)
        return presentation.mount.item if presentation is not None and presentation.mount is not None else None

    def focus_document(self, key):
        item = self.content_item(key)
        if item is not None and shiboken6.isValid(item) and self._input_eligible(key, self.document_for(key)):
            item.forceActiveFocus(Qt.OtherFocusReason)

    def display_rectangle(self, key):
        presentation = self._presentations[key]
        if self.mode == 'tabbed' or presentation.maximized:
            return QRect(*self._scroll, *self._viewport)
        return QRect(presentation.rectangle or self.registry.normal_rectangle(key) or QRect())

    def _reachable(self, rect):
        rect = QRect(rect)
        if self._root is not None and self._root.isVisible():
            x, y = self._scroll
            rect.moveLeft(x + max(0, min(rect.x() - x, max(0, self._viewport[0] - 120))))
            rect.moveTop(y + max(0, min(rect.y() - y, max(0, self._viewport[1] - 32))))
        return rect

    def _new_rectangle(self, index=None):
        offset = 24 * ((self.count if index is None else index) % 10)
        return self._reachable(QRect(12 + offset + self._scroll[0], 12 + offset + self._scroll[1],
            max(300, min(620, self._viewport[0] - 48)), max(220, min(460, self._viewport[1] - 48))))

    def show_document(self, key, title, *, user=True):
        if self.document_for(key) is None:
            raise KeyError(key)
        if not self._activate(key):
            return None
        opened = key not in self.registry.open_keys()
        presentation = self._presentations[key]
        presentation.title = title
        self.registry.opened(key, self._new_rectangle())
        if opened:
            presentation.rectangle = self._reachable(self.registry.normal_rectangle(key))
        self.registry.activated(key)
        if opened:
            self.refresh_document_input(key)
        self._publish()
        if opened:
            self.layoutChanged.emit()
        if user:
            self.workspaceChanged.emit()
        return self.document_for(key)

    def update_title(self, key, title):
        presentation = self._presentations[key]
        if presentation.title == title:
            return
        presentation.title = title
        self._publish()

    def _activate_input(self, key):
        controller, presentation = self.document_for(key), self._presentations.get(key)
        if (presentation is None or not self._activate(key) or self.document_for(key) is not controller
                or self._presentations.get(key) is not presentation or key not in self.registry.open_keys()):
            return False
        changed = self.registry.active_key != key
        self.registry.activated(key)
        self._publish()
        if changed:
            self.workspaceChanged.emit()
        return True

    def requestActivate(self, identity):
        key = tuple(identity)
        return bool(key in self.registry.open_keys() and self._host_input_eligible()
                    and self._root_available() and self._activate_input(key))

    def _input_token(self, key):
        lease = self.registry.capture_input(key)
        return f'{lease.registration}:{lease.lifetime}'

    def _token_current(self, key, token):
        return bool(token and key in self.registry.open_keys() and token == self._input_token(key))

    def _valid_frame_input(self, key, frame, token):
        presentation = self._presentations.get(key)
        return bool(self._token_current(key, token) and presentation is not None
            and presentation.frame is frame and self._input_eligible(key, self.document_for(key)))

    @Slot('QVariantList', QObject, result=str)
    def captureFrameInput(self, identity, frame):
        key = tuple(identity)
        if key not in self.registry.open_keys():
            return ''
        token = self._input_token(key)
        return token if self._valid_frame_input(key, frame, token) else ''

    @Slot('QVariantList', QObject, str, result=bool)
    def activateFrame(self, identity, frame, token):
        key = tuple(identity)
        return bool(self._valid_frame_input(key, frame, token)
            and self.registry.authorize(self.registry.capture_input(key))
            and self._valid_frame_input(key, frame, token))

    @Slot('QVariantList', QObject, result=str)
    def captureTabInput(self, identity, item):
        key = tuple(identity)
        if (key not in self.registry.open_keys() or not self._host_input_eligible()
                or not self._root_available() or not isinstance(item, QQuickItem)
                or not item.isVisible() or not item.isEnabled() or item.window() is not self._window
                or not self._root.isAncestorOf(item)):
            return ''
        return self._input_token(key)

    @Slot('QVariantList', QObject, str, result=bool)
    def activateTab(self, identity, item, token):
        key = tuple(identity)
        return bool(self._token_current(key, token) and self.captureTabInput(identity, item) == token
                    and self.requestActivate(identity))

    def _root_available(self):
        return bool(self._root is not None and shiboken6.isValid(self._root) and self._root.isVisible()
                    and self._root.isEnabled() and self._window is not None
                    and shiboken6.isValid(self._window) and self._window.isVisible())

    def _input_eligible(self, key, controller):
        presentation = self._presentations.get(key)
        frame = presentation.frame if presentation is not None else None
        item = self.content_item(key)
        return bool(self._host_input_eligible() and self._root_available() and frame is not None
            and shiboken6.isValid(frame) and frame.isVisible() and frame.isEnabled()
            and item is not None and shiboken6.isValid(item) and item.isVisible() and item.isEnabled()
            and frame.window() is self._window and item.window() is self._window
            and (self.mode == 'free' or key == self.registry.active_key))

    def refresh_document_input(self, key):
        controller = self.document_for(key)
        lease = self.registry.capture_input(key)
        controller.set_input_guard(lambda captured=lease: self.registry.authorize(captured))

    def _retire_input(self, key):
        self.registry.revoke_input(key)
        controller = self.document_for(key)
        if isinstance(controller, XYPlotController):
            controller.retire_input()
        self.refresh_document_input(key)

    @Slot()
    def refresh_eligibility(self):
        for key in self.registry.keys():
            eligible = self._input_eligible(key, self.document_for(key))
            if self._eligibility.get(key) != eligible:
                self._eligibility[key] = eligible
                self._retire_input(key)
        if (self._restore_pending is True and self._root_available()
                and self.mode == 'free' and min(self._viewport) > 0):
            # A zero-delay timer can precede the resize/layout polish. Wait for
            # this restore's completed frame before queuing the existing timer.
            self._restore_pending = _RestorePlacementRequest(self, self._window)

    @staticmethod
    def _disconnect(connections):
        for signal, callback in connections:
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        connections.clear()

    @Slot(QObject)
    def attachRoot(self, root):
        self._disconnect(self._root_connections)
        self._root = root
        for signal in (root.visibleChanged, root.enabledChanged):
            signal.connect(self.refresh_eligibility)
            self._root_connections.append((signal, self.refresh_eligibility))
        root.windowChanged.connect(self._window_changed)
        root.parentChanged.connect(self._root_reparented)
        root.destroyed.connect(self._root_destroyed)
        self._root_connections.extend(((root.windowChanged, self._window_changed),
                                       (root.parentChanged, self._root_reparented),
                                       (root.destroyed, self._root_destroyed)))
        self._window_changed(root.window())

    def _root_reparented(self):
        self._stop_restore_placement(preserve_pending=True)
        for key in self.registry.keys():
            self._retire_input(key)
        self.refresh_eligibility()

    def _root_destroyed(self):
        self._root = None
        self._root_connections.clear()
        self._window_changed(None)

    def _window_changed(self, window):
        self._stop_restore_placement(preserve_pending=True)
        self._disconnect(self._window_connections)
        self._window = window
        if window is not None:
            window.visibleChanged.connect(self.refresh_eligibility)
            self._window_connections.append((window.visibleChanged, self.refresh_eligibility))
        for key in self.registry.keys():
            self._retire_input(key)
        self.refresh_eligibility()

    @Slot(float, float, float, float)
    def reportViewport(self, width, height, scroll_x, scroll_y):
        self._viewport = (max(0, round(width)), max(0, round(height)))
        self._scroll = (round(scroll_x), round(scroll_y))
        self._publish()
        self.refresh_eligibility()

    @Slot('QVariantList', QObject, QObject)
    def attachFrame(self, identity, frame, content):
        key = tuple(identity)
        if key not in self.registry.open_keys():
            return
        presentation = self._presentations[key]
        self._detach_frame(presentation)
        presentation.frame = frame
        self._retire_input(key)
        try:
            presentation.mount = DocumentMount(self.document_for(key), QQmlEngine.contextForObject(frame).engine(), content, self)
        except Exception as error:
            self.mountFailed.emit(str(error))
            raise
        frame.destroyed.connect(lambda: self._frame_destroyed(key, presentation, frame))
        frame.visibleChanged.connect(self.refresh_eligibility)
        frame.enabledChanged.connect(self.refresh_eligibility)
        frame.parentChanged.connect(lambda: self._frame_reparented(key, presentation, frame))
        self.refresh_eligibility()

    def _frame_reparented(self, key, presentation, frame):
        if self._presentations.get(key) is presentation and presentation.frame is frame:
            self._retire_input(key)
            self.refresh_eligibility()

    def _frame_destroyed(self, key, presentation, frame):
        if self._presentations.get(key) is presentation and presentation.frame is frame:
            self._detach_frame(presentation)
            if self.document_for(key) is not None:
                self._retire_input(key)

    def _detach_frame(self, presentation):
        if presentation.mount is not None:
            presentation.mount.detach()
            presentation.mount.deleteLater()
            presentation.mount = None
        presentation.frame = None

    @Slot('QVariantList', QObject, str, float, float, float, float, result=bool)
    def moveResize(self, identity, frame, token, x, y, width, height):
        key = tuple(identity)
        if (self.mode != 'free' or key not in self.registry.open_keys()
                or self._presentations[key].maximized or not self.activateFrame(identity, frame, token)):
            return False
        rect = QRect(max(0, round(x)), max(0, round(y)), max(240, round(width)), max(180, round(height)))
        if rect != self.registry.normal_rectangle(key):
            self.registry.remember_rectangle(key, rect)
            self._presentations[key].rectangle = rect
            self._publish()
            self.workspaceChanged.emit()
        return True

    @Slot('QVariantList', QObject, str, result=bool)
    def toggleMaximize(self, identity, frame, token):
        key = tuple(identity)
        if self.mode != 'free' or not self.activateFrame(identity, frame, token):
            return False
        self._presentations[key].maximized = not self._presentations[key].maximized
        self._publish()
        return True

    @Slot('QVariantList', QObject, str, result=bool)
    def closeDocument(self, identity, frame, token):
        key = tuple(identity)
        return bool(self._valid_frame_input(key, frame, token)
                    and self.close_document(key, input_frame=frame, input_token=token))

    def close_document(self, key=None, *, input_frame=None, input_token=None):
        key = key or self.registry.active_key
        if key not in self.registry.open_keys() or not self._finish_close(key):
            return False
        if input_token is not None and not self._valid_frame_input(key, input_frame, input_token):
            return False
        if key == self.registry.active_key:
            neighbors = [candidate for candidate in self._stacking() if candidate != key]
            if neighbors and not self._activate_input(neighbors[-1]):
                return False
        self.registry.closed(key)
        self._detach_frame(self._presentations[key])
        self._publish()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()
        return True

    def discard(self, key):
        controller = self.registry.remove(key)
        presentation = self._presentations.pop(key, None)
        if presentation is not None:
            self._detach_frame(presentation)
        if controller is not None:
            controller.dispose()
            controller.deleteLater()
        if self.registry.active_key is None:
            for candidate in reversed(self._stacking()):
                if self._activate_input(candidate):
                    break
        self._publish()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    @Slot(str)
    def setMode(self, mode):
        if self.mode == mode:
            return
        self.registry.set_mode(mode)
        if mode == 'free':
            self._scroll = (0, 0)
            for key in self.registry.open_keys():
                self._presentations[key].maximized = False
                self._presentations[key].rectangle = self.registry.normal_rectangle(key)
        self._publish()
        self.refresh_eligibility()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    def reset_layout(self):
        """Retire project layout, including closed rectangles and scroll."""
        self._stop_restore_placement()
        self.registry.reset_layout()
        self.registry.set_mode('tabbed')
        self._scroll = (0, 0)
        for presentation in self._presentations.values():
            presentation.rectangle = None
            presentation.maximized = False
        self._publish()
        self.refresh_eligibility()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    @Slot()
    def tile(self):
        self._arrange(False)

    @Slot()
    def cascade(self):
        self._arrange(True)

    def _arrange(self, cascade):
        import math
        self.registry.set_mode('free')
        self._scroll = (0, 0)
        keys = self.registry.open_keys()
        columns = max(1, math.ceil(math.sqrt(len(keys))))
        rows = max(1, math.ceil(len(keys) / columns))
        for index, key in enumerate(keys):
            if cascade:
                offset = 24 * (index % 10)
                rect = QRect(12 + offset, 12 + offset, max(300, self._viewport[0] - 72), max(220, self._viewport[1] - 72))
            else:
                width, height = max(240, self._viewport[0] // columns), max(180, self._viewport[1] // rows)
                rect = QRect(index % columns * width, index // columns * height, width, height)
            self.registry.remember_rectangle(key, rect)
            self._presentations[key].rectangle, self._presentations[key].maximized = rect, False
        self._publish()
        self.refresh_eligibility()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    def workspace_snapshot(self, keys):
        return self.registry.workspace_snapshot(keys)

    def restore_workspace(self, metadata, title):
        self._stop_restore_placement()
        for presentation in self._presentations.values():
            self._detach_frame(presentation)
        self.registry.restore_workspace(metadata)
        for index, key in enumerate(self.registry.open_keys()):
            presentation = self._presentations[key]
            rect = self.registry.normal_rectangle(key)
            if rect is None:
                rect = self._new_rectangle(index)
                self.registry.remember_rectangle(key, rect)
            presentation.title, presentation.rectangle = title(key), rect
            presentation.maximized = False
            self.refresh_document_input(key)
        self._restore_pending = True
        self._publish(reset=True)
        self.layoutChanged.emit()
        self.refresh_eligibility()

    def _stop_restore_placement(self, *, preserve_pending=False):
        request = self._restore_pending
        self._placement.stop()
        self._restore_pending = False
        if preserve_pending:
            self._restore_pending = bool(request)
        if isinstance(request, _RestorePlacementRequest):
            request.cancel()

    def _restore_placement(self):
        if self._restore_pending and self._root_available() and self.mode == 'free' and min(self._viewport) > 0:
            for key in self.registry.open_keys():
                self._presentations[key].rectangle = self._reachable(self.registry.normal_rectangle(key))
            self._stop_restore_placement()
            self._publish()
        elif isinstance(self._restore_pending, _RestorePlacementRequest):
            self._stop_restore_placement(preserve_pending=True)

    def park(self):
        self._stop_restore_placement()
        frames = tuple((key, presentation.title, QRect(presentation.rectangle) if presentation.rectangle is not None else None,
                        presentation.maximized) for key, presentation in self._presentations.items())
        registry = self.registry.park()
        for presentation in self._presentations.values():
            self._detach_frame(presentation)
        self._presentations.clear()
        self._publish()
        return ParkedQuickDocuments(registry, frames)

    def restore(self, state):
        if state.disposed:
            raise ValueError('Disposed parked documents cannot be restored.')
        self._stop_restore_placement()
        for presentation in self._presentations.values():
            self._detach_frame(presentation)
        self.registry.restore(state.registry)
        self._presentations = {key: _Presentation(title, QRect(rect) if rect is not None else None, maximized)
                               for key, title, rect, maximized in state.frames}
        for key in self.registry.keys():
            self.refresh_document_input(key)
        self._publish(reset=True)

    @staticmethod
    def discard_parked(state):
        if state.disposed:
            return
        state.disposed = True
        for key, controller in state.registry.cached_items():
            controller.dispose()
            controller.deleteLater()

    def detach(self):
        """Release presentation before engine teardown; retained documents survive."""
        self._stop_restore_placement()
        for key, presentation in self._presentations.items():
            self._retire_input(key)
            self._detach_frame(presentation)
        self._disconnect(self._root_connections)
        self._disconnect(self._window_connections)
        self._root = self._window = None
