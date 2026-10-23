"""Native graph windows, with retained pages independent of view lifetime."""
from __future__ import annotations

from contextlib import contextmanager

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QMdiArea, QMdiSubWindow, QTabBar, QWidget, QVBoxLayout, QToolButton
from .workbook_state import document_record_key, encode_document_key


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

    def __init__(self, activate, finish_close, parent=None):
        super().__init__(parent)
        self._activate = activate
        self._finish_close = finish_close
        self._pages = {}
        self._windows = {}
        self._normal_rects = {}
        self._active_key = None
        self.initial_view = True
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
                        self._place_window(window, self._normal_rects[key], clamp=True)
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
        if rect != self._normal_rects.get(window.key):
            self._normal_rects[window.key] = QRect(rect)
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

    def page_for(self, key):
        return self._pages.get(key)

    def window_for(self, key):
        return self._windows.get(key)

    def cache_page(self, key, page):
        self._pages[key] = page
        page.setParent(self)
        page.hide()
        self.refresh_page_inputs(key)

    def refresh_page_inputs(self, key):
        page = self._pages[key]
        # Select before native editing starts, including annotation drags and
        # graph shortcuts. A rejected activation consumes the original event.
        for root in self._page_roots(page):
            for widget in (root, *root.findChildren(QWidget)):
                widget.setProperty('graphDocumentKey', key)
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
            page = self._pages.get(tuple(key)) if key is not None else None
            # Another MainWindow may have the same document keys. Keep this
            # input filter separate from QMdiArea's native lifecycle filter.
            belongs = page is not None and isinstance(watched, QWidget) and any(
                watched is root or root.isAncestorOf(watched) for root in self._page_roots(page))
            if belongs and not self._changing and not self._activate(tuple(key)):
                return True
        return False

    def show_document(self, key, title):
        self.initial_view = False
        opened = key not in self._windows
        with self._change():
            window = self._windows.get(key)
            if window is None:
                rect = self._normal_rects.get(key) or self._new_rectangle()
                window = _GraphWindow(self, key)
                window.setWidget(self._pages[key])
                self.addSubWindow(window)
                self._windows[key] = window
                self._normal_rects[key] = QRect(rect)
                self._place_window(window, rect, clamp=True)
                window.show()
            window.setWindowTitle(title)
            self._pages[key].show()
            self._active_key = key
            self.setActiveSubWindow(window)
            self._empty.hide()
        if opened:
            self.layoutChanged.emit()
        self.workspaceChanged.emit()
        return self._pages[key]

    def _window_activated(self, window):
        if self._changing or window is None:
            return
        if self._activate(window.key):
            self._active_key = window.key
            self.workspaceChanged.emit()
        else:
            self._changing = True
            try:
                self.setActiveSubWindow(self._windows.get(self._active_key))
            finally:
                self._changing = False

    def _detach_window(self, key):
        window = self._windows.pop(key, None)
        if window is None:
            return
        with self._change():
            page = self._pages[key]
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
                self._active_key = neighbor
        self._detach_window(key)
        if not self._windows:
            self._active_key = None
        self.layoutChanged.emit()
        self.workspaceChanged.emit()
        return True

    def discard(self, key):
        was_active = self._active_key == key
        if was_active:
            self._active_key = None
        self._detach_window(key)
        page = self._pages.pop(key, None)
        if page is not None:
            self._dispose_page(key, page)
        self._normal_rects.pop(key, None)
        if was_active:
            for window in reversed(self.subWindowList(QMdiArea.StackingOrder)):
                if self._activate(window.key):
                    self._active_key = window.key
                    with self._change():
                        self.setActiveSubWindow(window)
                    break
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    def keys(self):
        return tuple(self._pages)

    def park(self):
        """Keep cached pages alive while a candidate project is installed."""
        self._restore_timer.stop()
        self._restore_pending = False
        state = (self._pages.copy(), [(window.key, window.windowTitle(), self._document_rectangle(window))
                 for window in self.subWindowList(QMdiArea.StackingOrder)], self.viewMode(),
                 self._active_key, {key: QRect(rect) for key, rect in self._normal_rects.items()},
                 self.initial_view)
        for key in tuple(self._windows):
            self._detach_window(key)
        self._pages.clear()
        self._normal_rects.clear()
        self._active_key = None
        return state

    def restore(self, state):
        self._restore_timer.stop()
        self._restore_pending = False
        pages, windows, mode, active_key, rectangles, initial_view = state
        with self._change():
            self._pages.update(pages)
            self._normal_rects = {key: QRect(rect) for key, rect in rectangles.items()}
            self._set_mode(QMdiArea.SubWindowView)
            for key, title, geometry in windows:
                self.show_document(key, title)
                self._place_window(self._windows[key], geometry)
            self._set_mode(mode)
            self._active_key = active_key
            self.setActiveSubWindow(self._windows.get(active_key))
            self.initial_view = initial_view
        self._empty.setVisible(not windows)

    @staticmethod
    def discard_parked(state):
        for key, page in state[0].items():
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
                self._place_window(window, self._normal_rects[key], clamp=self._restore_pending)
            self.setActiveSubWindow(self._windows.get(self._active_key))
        self.layoutChanged.emit()
        self.workspaceChanged.emit()
        self._schedule_restore_placement()

    def tile_graphs(self):
        self.free_view()
        with self._change():
            self.tileSubWindows()
            self._normal_rects.update({key: self._document_rectangle(window) for key, window in self._windows.items()})
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    def cascade_graphs(self):
        self.free_view()
        with self._change():
            self.cascadeSubWindows()
            self._normal_rects.update({key: self._document_rectangle(window) for key, window in self._windows.items()})
        self.layoutChanged.emit()
        self.workspaceChanged.emit()

    def workspace_snapshot(self, keys):
        """Detached JSON metadata, including retained geometry of closed graphs."""
        keys = tuple(keys)
        existing = set(keys)
        stacking = [window.key for window in self.subWindowList(QMdiArea.StackingOrder)
                    if window.key in existing]
        return {'mode': 'tabbed' if self.viewMode() == QMdiArea.TabbedView else 'free',
                'documents': [{'key': encode_document_key(key), 'open': key in self._windows,
                    'rect': list(self._normal_rects[key].getRect()) if key in self._normal_rects else None}
                    for key in keys],
                'active': list(self._active_key) if self._active_key in stacking else None,
                'stacking': [list(key) for key in stacking]}

    def restore_workspace(self, metadata, title):
        """Restore a validated layout after its saved open pages have been cached."""
        with self._change():
            self.initial_view = False
            self._restore_pending = True
            for key in tuple(self._windows):
                self._detach_window(key)
            self._normal_rects = {
                document_record_key(record): QRect(*record['rect'])
                for record in metadata['documents'] if record['rect'] is not None}
            self._set_mode(QMdiArea.SubWindowView)
            for identity in metadata['stacking']:
                key = tuple(identity)
                self.show_document(key, title(key))
            if metadata['mode'] == 'tabbed':
                self._set_mode(QMdiArea.TabbedView)
            self._active_key = tuple(metadata['active']) if metadata['active'] else None
            self.setActiveSubWindow(self._windows.get(self._active_key))
            self._empty.setVisible(not self._windows)
        self.layoutChanged.emit()
        self._schedule_restore_placement()

    def reset_layout(self):
        self._restore_timer.stop()
        self._normal_rects.clear()
        self._active_key = None
        self.initial_view = True
        self._restore_pending = False
        with self._change():
            self._set_mode(QMdiArea.SubWindowView)
