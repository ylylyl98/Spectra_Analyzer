"""Native graph windows, with retained pages independent of view lifetime."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QMdiArea, QMdiSubWindow, QTabBar, QWidget, QVBoxLayout, QToolButton
from .document_registry import DocumentRegistry, ParkedRegistry


@dataclass
class ParkedDocuments:
    registry: ParkedRegistry
    windows: tuple
    disposed: bool = False


class _GraphWindow(QMdiSubWindow):
    def __init__(self, documents, key):
        super().__init__()
        self.documents = documents
        self.key = key

    def closeEvent(self, event):
        event.ignore()
        self.documents.close_document(self.key)

    def moveEvent(self, event):
        super().moveEvent(event)
        self.documents._remember_geometry(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.documents._remember_geometry(self)


class _GraphInputFilter(QObject):
    def __init__(self, documents):
        super().__init__(documents)
        self.documents = documents

    def eventFilter(self, watched, event):
        return self.documents._filter_page_input(watched, event)


class GraphDocuments(QMdiArea):
    """Own cached PlotPages and optional windows; delegate domain guards to the owner."""
    layoutChanged = Signal()
    workspaceChanged = Signal()

    def __init__(self, activate, finish_close, parent=None, *, input_eligible=None):
        super().__init__(parent)
        self._activate = activate
        self._finish_close = finish_close
        self._host_input_eligible = input_eligible or (lambda: True)
        self.registry = DocumentRegistry(self._activate_input, self._input_eligible, self)
        self._windows = {}
        self._restore_pending = False
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._restore_visible_rectangles)
        self._changing = False
        self._input_filter = _GraphInputFilter(self)
        self.setViewMode(QMdiArea.SubWindowView)
        self.setOption(QMdiArea.DontMaximizeSubWindowOnActivation)
        self.setTabsClosable(True)
        self.setTabsMovable(True)
        self.setDocumentMode(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setAccessibleName('Workspace documents')
        self._empty = QWidget(self.viewport())
        layout = QVBoxLayout(self._empty)
        layout.addStretch()
        message = QLabel('Open a Workbook or graph from Project, or start with data files.')
        message.setAlignment(Qt.AlignCenter)
        message.setWordWrap(True)
        layout.addWidget(message)
        self.empty_import_button = QToolButton()
        self.empty_open_button = QToolButton()
        self.empty_recent_button = QToolButton()
        for button in (self.empty_import_button, self.empty_open_button, self.empty_recent_button):
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            layout.addWidget(button, 0, Qt.AlignHCenter)
        self.empty_recent_button.setText('Recent projects')
        self.empty_recent_button.setPopupMode(QToolButton.InstantPopup)
        guidance = QLabel('Data: DAT, CSV, TSV, TXT and XY tables. Projects: .spectra and .spextra.')
        guidance.setWordWrap(True)
        guidance.setAlignment(Qt.AlignCenter)
        layout.addWidget(guidance)
        layout.addStretch()
        self.subWindowActivated.connect(self._window_activated)

    def set_start_actions(self, import_action, open_action, recent_menu):
        self.empty_import_button.setDefaultAction(import_action)
        self.empty_open_button.setDefaultAction(open_action)
        self.empty_recent_button.setMenu(recent_menu)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._empty.setGeometry(self.viewport().rect())
        self._schedule_restore_placement()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_restore_placement()

    def _schedule_restore_placement(self):
        if self._restore_pending and self.isVisible() and self.viewMode() == QMdiArea.SubWindowView:
            self._restore_timer.start(0)

    def _restore_visible_rectangles(self):
        # A project can load before the main window is shown. Reachability is
        # a display adaptation after dock/command layout settles, not a user edit
        # to the saved normal rectangle or an arrangement on every later resize.
        if self._restore_pending and self.isVisible() and self.viewMode() == QMdiArea.SubWindowView:
            with self._change():
                for key, window in self._windows.items():
                    if not window.isMaximized() and not window.isMinimized():
                        self._place_window(window, self.registry.normal_rectangle(key), clamp=True)
                self._restore_pending = False

    @contextmanager
    def _change(self):
        previous = self._changing
        self._changing = True
        try:
            yield
        finally:
            self._changing = previous

    def _remember_geometry(self, window):
        if (self._changing or self.viewMode() != QMdiArea.SubWindowView
                or window.isMaximized() or window.isMinimized()
                or self._windows.get(window.key) is not window):
            return
        rect = self._document_rectangle(window)
        if rect != self.registry.normal_rectangle(window.key):
            self.registry.remember_rectangle(window.key, rect)
            self.workspaceChanged.emit()

    def _reachable(self, rect):
        rect = QRect(rect)
        if not self.isVisible():
            return rect
        offset = self._scroll_offset()
        rect.translate(-offset)
        viewport = self.viewport().rect()
        rect.moveLeft(max(0, min(rect.x(), max(0, viewport.width() - 120))))
        rect.moveTop(max(0, min(rect.y(), max(0, viewport.height() - 32))))
        rect.translate(offset)
        return rect

    def _scroll_offset(self):
        return QPoint(self.horizontalScrollBar().value(), self.verticalScrollBar().value())

    def _document_rectangle(self, window):
        return window.geometry().translated(self._scroll_offset())

    def _place_window(self, window, rect, *, clamp=False):
        rect = self._reachable(rect) if clamp else QRect(rect)
        window.setGeometry(rect.translated(-self._scroll_offset()))

    def _set_mode(self, mode):
        self.setOption(QMdiArea.DontMaximizeSubWindowOnActivation, mode == QMdiArea.SubWindowView)
        self.setViewMode(mode)
        self.registry.set_mode('tabbed' if mode == QMdiArea.TabbedView else 'free')
        if mode == QMdiArea.TabbedView:
            active = self._windows.get(self._active_key)
            if active is not None:
                active.showMaximized()

    def _new_rectangle(self):
        area = self.maximumViewportSize()
        offset = 24 * (len(self._windows) % 10)
        return self._reachable(QRect(12 + offset, 12 + offset,
            max(300, min(620, area.width() - 48)), max(220, min(460, area.height() - 48)))
            .translated(self._scroll_offset()))

    @property
    def _active_key(self):
        return self.registry.active_key

    @property
    def initial_view(self):
        return self.registry.initial_view

    @initial_view.setter
    def initial_view(self, value):
        self.registry.initial_view = value

    def page_for(self, key):
        return self.registry.document_for(key)

    def cached_documents(self):
        return self.registry.cached_documents()

    def cached_items(self):
        return self.registry.cached_items()

    def _report_stacking(self):
        self.registry.report_stacking(window.key for window in self.subWindowList(QMdiArea.StackingOrder))

    def _input_eligible(self, key, page):
        window = self._windows.get(key)
        if (self._changing or not self._host_input_eligible() or not self.isVisible()
                or not self.isEnabled() or window is None or not window.isVisible()
                or not window.isEnabled() or not page.isVisible() or not page.isEnabled()):
            return False
        return self.registry.mode == 'free' or self.activeSubWindow() is window

    def _activate_input(self, key):
        page, window = self.page_for(key), self._windows.get(key)
        if not self._activate(key):
            return False
        if (window is None or self._windows.get(key) is not window
                or self.page_for(key) is not page):
            return False
        # A same-owner domain fast path need not select its native frame. In
        # particular, deleting an old closed frame can clear QMdiArea's active
        # pointer after its retained page has already been reopened.
        with self._change():
            self.setActiveSubWindow(window)
            if self.activeSubWindow() is not window:
                return False
            self.registry.activated(key)
        return True

    def window_for(self, key):
        return self._windows.get(key)

    def cache_page(self, key, page):
        self.registry.register(key, page)
        page.setParent(self)
        page.hide()
        self.refresh_page_inputs(key)

    def refresh_page_inputs(self, key):
        page = self.page_for(key)
        lease = self.registry.capture_input(key)
        # Quick popup items are outside the QWidget ancestry. Their controllers
        # must renew the same owner guard at the action boundary.
        if hasattr(page, 'set_input_guard'):
            page.set_input_guard(lambda captured=lease: self.registry.authorize(captured))
        # Select before native editing starts, including annotation drags and
        # graph shortcuts. A rejected activation consumes the original event.
        for root in self._page_roots(page):
            for widget in (root, *root.findChildren(QWidget)):
                widget.setProperty('graphDocumentKey', key)
                widget._graph_document_input_lease = lease
                widget.installEventFilter(self._input_filter)

    @staticmethod
    def _page_roots(page):
        return (page, *(root for name in ('control_scroll', 'command_bar', 'inspector')
                         if (root := getattr(page, name, None)) is not None))

    @staticmethod
    def _dispose_page(key, page):
        dispose = getattr(page, 'dispose', None)
        if dispose is not None:
            dispose()
        else:
            page.invalidate(('removed', key))
        for root in GraphDocuments._page_roots(page)[1:]:
            root.deleteLater()
        page.deleteLater()

    def _filter_page_input(self, watched, event):
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick,
                            QEvent.ContextMenu, QEvent.KeyPress, QEvent.ShortcutOverride):
            key = watched.property('graphDocumentKey')
            lease = getattr(watched, '_graph_document_input_lease', None)
            page = self.page_for(tuple(key)) if key is not None else None
            # Another MainWindow may have the same document keys. Keep this
            # input filter separate from QMdiArea's native lifecycle filter.
            belongs = page is not None and isinstance(watched, QWidget) and any(
                watched is root or root.isAncestorOf(watched) for root in self._page_roots(page))
            if (self.registry.owns_input(lease)
                    and (not belongs or not self.registry.authorize(lease))):
                return True
        return False

    def show_document(self, key, title):
        self.initial_view = False
        opened = key not in self._windows
        with self._change():
            window = self._windows.get(key)
            if window is None:
                rect = self.registry.normal_rectangle(key) or self._new_rectangle()
                window = _GraphWindow(self, key)
                window.setWidget(self.page_for(key))
                self.addSubWindow(window)
                self._windows[key] = window
                self.registry.opened(key, rect)
                self._place_window(window, rect, clamp=True)
                window.show()
            window.setWindowTitle(title)
            self.page_for(key).show()
            self.registry.activated(key)
            self.setActiveSubWindow(window)
            self._empty.hide()
        if not self._changing:
            self._report_stacking()
        if opened:
            self.refresh_page_inputs(key)
            self.layoutChanged.emit()
        self.workspaceChanged.emit()
        return self.page_for(key)

    def _window_activated(self, window):
        if self._changing or window is None or self._windows.get(window.key) is not window:
            return
        previous = self._active_key
        if self.registry.authorize(self.registry.capture_input(window.key)):
            self._report_stacking()
            self.workspaceChanged.emit()
        else:
            with self._change():
                if previous is None or previous in self.registry.open_keys():
                    self.registry.activated(previous)
                    self.setActiveSubWindow(self._windows.get(previous))

    def _detach_window(self, key, page=None):
        window = self._windows.pop(key, None)
        if window is None:
            return
        with self._change():
            page = page if page is not None else self.page_for(key)
            self.registry.closed(key)
            page.setParent(self)
            page.hide()
            self.removeSubWindow(window)
            window.hide()
            window.deleteLater()
            self.setActiveSubWindow(self._windows.get(self._active_key))
            self._empty.setVisible(not self._windows)

    def close_document(self, key=None):
        key = key or self._active_key
        if key not in self._windows or not self._finish_close(key):
            return False
        if key == self._active_key:
            neighbors = [window.key for window in self.subWindowList() if window.key != key]
            if neighbors:
                neighbor = neighbors[-1]
                if not self._activate(neighbor):
                    return False
                self.registry.activated(neighbor)
        self._detach_window(key)
        self._report_stacking()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()
        return True

    def discard(self, key):
        was_active = self._active_key == key
        # Retire authority before detaching or disposing any live view roots.
        page = self.registry.remove(key)
        self._detach_window(key, page)
        if page is not None:
            self._dispose_page(key, page)
        if was_active:
            for window in reversed(self.subWindowList(QMdiArea.StackingOrder)):
                if self._activate(window.key):
                    self.registry.activated(window.key)
                    with self._change():
                        self.setActiveSubWindow(window)
                    break
        self._report_stacking()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    def keys(self):
        return self.registry.keys()

    def park(self):
        """Keep exact cached documents alive while a candidate is installed."""
        self._restore_timer.stop()
        self._restore_pending = False
        self._report_stacking()
        windows = tuple((window.key, window.windowTitle(), self._document_rectangle(window))
                        for window in self.subWindowList(QMdiArea.StackingOrder))
        registry = self.registry.park()
        pages = dict(registry.cached_items())
        for key in tuple(self._windows):
            self._detach_window(key, pages[key])
        return ParkedDocuments(registry, windows)

    def restore(self, state):
        if state.disposed:
            raise ValueError('Disposed parked documents cannot be restored.')
        self._restore_timer.stop()
        self._restore_pending = False
        # Rollback deliberately restores twice, around scientific projection.
        with self._change():
            for key in tuple(self._windows):
                self._detach_window(key)
            self.registry.restore(state.registry)
            self._set_mode(QMdiArea.SubWindowView)
            for key, title, geometry in state.windows:
                self.show_document(key, title)
                self._place_window(self._windows[key], geometry)
            self._set_mode(QMdiArea.TabbedView if state.registry.mode == 'tabbed' else QMdiArea.SubWindowView)
            self.registry.activated(state.registry.active_key)
            self.setActiveSubWindow(self._windows.get(self._active_key))
            self.registry.report_stacking(state.registry.stacking)
            self.initial_view = state.registry.initial_view
            for key in self.keys():
                self.refresh_page_inputs(key)
        self._empty.setVisible(not state.windows)

    @staticmethod
    def discard_parked(state):
        if state.disposed:
            return
        state.disposed = True
        for key, page in state.registry.cached_items():
            GraphDocuments._dispose_page(key, page)

    def visible_keys(self):
        if self.viewMode() == QMdiArea.SubWindowView:
            return tuple(self._windows)
        # Native activation can wait until the main window is shown. Keep the
        # selected open document eligible for deferred preparation meanwhile.
        window = self.activeSubWindow() or self._windows.get(self._active_key)
        return (window.key,) if window is not None else ()

    def update_title(self, key, title, tooltip=None):
        window = self._windows.get(key)
        if window is not None:
            window.setWindowTitle(title)
            if tooltip is not None:
                window.setToolTip(tooltip)
            self._refresh_tab_tooltips()

    def _refresh_tab_tooltips(self):
        tips = {window.windowTitle(): window.toolTip() for window in self._windows.values()}
        for bar in self.findChildren(QTabBar, options=Qt.FindDirectChildrenOnly):
            for index in range(bar.count()):
                bar.setTabToolTip(index, tips.get(bar.tabText(index), ''))

    def tabbed_view(self):
        with self._change():
            self._set_mode(QMdiArea.TabbedView)
        self._report_stacking()
        self._refresh_tab_tooltips()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    def free_view(self):
        with self._change():
            self._set_mode(QMdiArea.SubWindowView)
            for window in self._windows.values():
                window.showNormal()
            self.horizontalScrollBar().setValue(0)
            self.verticalScrollBar().setValue(0)
            for key, window in self._windows.items():
                self._place_window(window, self.registry.normal_rectangle(key), clamp=self._restore_pending)
            self.setActiveSubWindow(self._windows.get(self._active_key))
        self._report_stacking()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()
        self._schedule_restore_placement()

    def tile_graphs(self):
        self.free_view()
        with self._change():
            self.tileSubWindows()
            for key, window in self._windows.items():
                self.registry.remember_rectangle(key, self._document_rectangle(window))
        self._report_stacking()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    def cascade_graphs(self):
        self.free_view()
        with self._change():
            self.cascadeSubWindows()
            for key, window in self._windows.items():
                self.registry.remember_rectangle(key, self._document_rectangle(window))
        self._report_stacking()
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    def workspace_snapshot(self, keys):
        """Detached JSON metadata, including retained geometry of closed graphs."""
        self._report_stacking()
        return self.registry.workspace_snapshot(keys)

    def restore_workspace(self, metadata, title):
        """Restore a validated layout after its saved open pages have been cached."""
        with self._change():
            self._restore_timer.stop()
            self._restore_pending = True
            for key in tuple(self._windows):
                self._detach_window(key)
            self.registry.restore_workspace(metadata)
            self._set_mode(QMdiArea.SubWindowView)
            for identity in metadata['stacking']:
                key = tuple(identity)
                self.show_document(key, title(key))
            if metadata['mode'] == 'tabbed':
                self._set_mode(QMdiArea.TabbedView)
            self.registry.activated(tuple(metadata['active']) if metadata['active'] else None)
            self.setActiveSubWindow(self._windows.get(self._active_key))
            self._report_stacking()
            self._empty.setVisible(not self._windows)
        self.layoutChanged.emit()
        self._schedule_restore_placement()

    def reset_layout(self):
        self._restore_timer.stop()
        self.registry.reset_layout()
        self._restore_pending = False
        with self._change():
            self._set_mode(QMdiArea.SubWindowView)
