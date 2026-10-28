"""Analysis strip projection over Matplotlib's Qt-free navigation controller.

Subplot spacing and Figure options use captured-owner Quick editors.
"""
from __future__ import annotations

from matplotlib.backend_bases import NavigationToolbar2
from PySide6.QtCore import QObject, Property, Signal, Slot


class AnalysisNavigationController(NavigationToolbar2):
    """Genuine Matplotlib navigation with a small canvas/presentation adapter."""

    def __init__(self, canvas, window):
        self.window = window
        self.bridge = None
        self._message = ''
        self._gesture_owner = None
        self._subplot_dialog = None
        self._subplot_close_cid = None
        self._figure_dialog = None
        super().__init__(canvas)

    def _notify(self):
        if self.bridge is not None:
            self.bridge.stateChanged.emit()

    def set_history_buttons(self):
        self._notify()

    def set_message(self, message):
        if message != self._message:
            self._message = message
            if self.bridge is not None:
                self.bridge.messageChanged.emit()

    def pan(self, *args):
        super().pan(*args)
        self._notify()

    def zoom(self, *args):
        super().zoom(*args)
        self._notify()

    def draw_rubberband(self, event, x0, y0, x1, y1):
        height = self.canvas.get_width_height(physical=True)[1]
        self.canvas.view_bridge.set_rubberband((x0, height - y0, x1 - x0, y0 - y1))

    def remove_rubberband(self):
        self.canvas.view_bridge.set_rubberband(None)

    def _capture_gesture(self):
        if self.bridge is None or not self.bridge.allowed():
            return False
        self._gesture_owner = self.bridge.ownerToken
        return True

    def _gesture_allowed(self):
        return (self.bridge is not None and self._gesture_owner is not None
                and self.bridge.allowed(self._gesture_owner))

    def cancel_interaction(self):
        """Retire a captured canvas gesture without committing another owner."""
        had_gesture = self._pan_info is not None or self._zoom_info is not None
        if self._pan_info is not None:
            self.canvas.mpl_disconnect(self._pan_info.cid)
            self._id_drag = self.canvas.mpl_connect('motion_notify_event', self.mouse_move)
            for ax in self._pan_info.axes:
                ax.end_pan()
            self._pan_info = None
        if self._zoom_info is not None:
            self.canvas.mpl_disconnect(self._zoom_info.cid)
            self.remove_rubberband()
            self._zoom_info = None
        self._gesture_owner = None
        self._last_cursor = None
        if had_gesture and self.window is not None:
            # The pending edit already captures its source session/graph. Its
            # finisher records that source even if refresh observes a new one.
            self.window._finish_history_edit()

    def press_pan(self, event):
        if self._capture_gesture():
            super().press_pan(event)

    def press_zoom(self, event):
        if self._capture_gesture():
            super().press_zoom(event)

    def drag_pan(self, event):
        if self._gesture_allowed():
            super().drag_pan(event)
        else:
            self.cancel_interaction()

    def drag_zoom(self, event):
        if self._gesture_allowed():
            super().drag_zoom(event)
        else:
            self.cancel_interaction()

    def release_pan(self, event):
        if self._gesture_allowed():
            super().release_pan(event)
        else:
            self.cancel_interaction()
        self._gesture_owner = None

    def release_zoom(self, event):
        if self._gesture_allowed():
            super().release_zoom(event)
        else:
            self.cancel_interaction()
        self._gesture_owner = None

    def configure_subplots(self):
        from .quick_dialog import QuickDialog
        from .subplot_editor import SubplotEditorBridge
        if self.bridge is None or not self.bridge.allowed():
            return None
        current = self._subplot_dialog
        if current is not None and current.bridge.check_owner():
            current.show()
            return current
        editor = SubplotEditorBridge(self.bridge)
        dialog = QuickDialog(editor, 'SubplotEditor.qml', self.window.root_window,
                             title='Subplot spacing', size=(520, 620),
                             minimum_size=(380, 420))
        self._subplot_dialog = dialog
        editor.retired.connect(dialog.reject)
        self._subplot_close_cid = self.canvas.mpl_connect('close_event', self._close_subplots)
        dialog.finished.connect(lambda result: self._subplot_finished(dialog))
        dialog.show()
        return dialog

    def _close_subplots(self, event=None):
        if self._subplot_dialog is not None:
            self._subplot_dialog.reject()

    def _subplot_finished(self, dialog):
        if self._subplot_dialog is dialog:
            if self._subplot_close_cid is not None:
                self.canvas.mpl_disconnect(self._subplot_close_cid)
            self._subplot_close_cid = None
            self._subplot_dialog = None
        dialog.deleteLater()

    def edit_parameters(self):
        from PySide6.QtCore import Qt
        from .common_prompts import prompt_item, prompt_message
        from .figure_options import FigureOptionsBridge
        from .quick_dialog import QuickDialog
        if self.bridge is None or not self.bridge.allowed():
            return None
        if self._figure_dialog is not None:
            self._figure_dialog.bridge.check_owner()
        owner, figure = self.bridge.ownerToken, self.canvas.figure
        axes = tuple(figure.get_axes())
        if not axes:
            prompt_message(self.window.root_window, 'Error', 'There are no Axes to edit.', kind='warning')
            return None
        if len(axes) == 1:
            ax, = axes
        else:
            titles = [ax.get_label() or ax.get_title() or ax.get_title('left')
                      or ax.get_title('right')
                      or ' - '.join(filter(None, [ax.get_xlabel(), ax.get_ylabel()]))
                      or f'<anonymous {type(ax).__name__}>' for ax in axes]
            duplicates = {title for title in titles if titles.count(title) > 1}
            titles = [title + f' (id: {id(ax):#x})' if title in duplicates else title
                      for title, ax in zip(titles, axes)]
            item, okay = prompt_item(self.window.root_window, 'Customize', 'Select Axes:', titles)
            if self._figure_dialog is not None:
                self._figure_dialog.bridge.check_owner()
            if not okay or item not in titles:
                return None
            ax = axes[titles.index(item)]
        current_axes = self.canvas.figure.get_axes()
        if (not self.bridge.allowed(owner) or self.canvas.figure is not figure
                or len(current_axes) != len(axes)
                or any(a is not b for a, b in zip(current_axes, axes))):
            return None
        self._close_figure_options()
        editor = FigureOptionsBridge(self.bridge, ax)
        dialog = QuickDialog(editor, 'FigureOptions.qml', self.window.root_window,
                             title='Figure options', size=(760, 740), minimum_size=(480, 420))
        # Figure options has always been a nonmodal live editor.
        dialog.window().setModality(Qt.NonModal)
        self._figure_dialog = dialog
        editor.retired.connect(dialog.reject)
        dialog.finished.connect(lambda result: self._figure_options_finished(dialog))
        dialog.show()
        return dialog

    def _close_figure_options(self):
        if self._figure_dialog is not None:
            self._figure_dialog.reject()

    def _figure_options_finished(self, dialog):
        if self._figure_dialog is dialog:
            self._figure_dialog = None
        dialog.deleteLater()


class AnalysisNavigationBridge(QObject):
    """Typed command boundary; datasets, page zoom and QActions stay authoritative."""
    stateChanged = Signal()
    messageChanged = Signal()

    def __init__(self, window, toolbar, actions):
        super().__init__(window)
        self.window, self.toolbar = window, toolbar
        self._actions = dict(actions)
        self._closed = False
        self._owner_epoch = 0
        self._last_owner = self.ownerToken
        self._last_active = self.allowed()
        toolbar.bridge = self
        for action in self._actions.values():
            action.changed.connect(self.stateChanged)

    @Property(str, notify=stateChanged)
    def ownerToken(self):
        # Keep the live source identity so a switch rejects commands even before
        # refresh; the epoch prevents a retired identity reviving on return.
        return f'{self.window.ordinary_analysis.ownerToken}|navigation:{self._owner_epoch}'

    def allowed(self, owner=None):
        from shiboken6 import isValid
        w = self.window
        return (not self._closed and isValid(w) and not w._close_pending
                and not w._dat_import_pending and w.current_cube is not None
                and w._workbench_document() is None
                and w._outer_view == 'analysis'
                and w.ordinary_analysis.allowed()
                and (owner is None or owner == self.ownerToken))

    enabled = Property(bool, lambda self: self.allowed(), notify=stateChanged)
    backEnabled = Property(bool, lambda self: self.enabled and self.toolbar._nav_stack._pos > 0,
                           notify=stateChanged)
    forwardEnabled = Property(bool, lambda self: self.enabled and self.toolbar._nav_stack._pos
                              < len(self.toolbar._nav_stack) - 1, notify=stateChanged)
    mode = Property(str, lambda self: str(self.toolbar.mode), notify=stateChanged)
    message = Property(str, lambda self: self.toolbar._message, notify=messageChanged)
    zoomText = Property(str, lambda self: self.window.plot_page.zoom_text, notify=stateChanged)

    @Property('QVariantList', notify=stateChanged)
    def actions(self):
        return [dict(key=key, text=action.text().replace('&', ''), tooltip=action.toolTip(),
                     enabled=self.enabled and action.isEnabled())
                for key, action in self._actions.items()]

    def refresh(self):
        owner = self.ownerToken
        active = self.allowed()
        changed = owner != self._last_owner or active != self._last_active
        if changed:
            self._owner_epoch += 1
            owner = self.ownerToken
        if changed or not active:
            self.toolbar.cancel_interaction()
            if self.toolbar.mode == 'pan/zoom':
                self.toolbar.pan()
            elif self.toolbar.mode == 'zoom rect':
                self.toolbar.zoom()
            self.toolbar.set_message('')
        self._last_owner = owner
        self._last_active = active
        if self.toolbar._subplot_dialog is not None:
            self.toolbar._subplot_dialog.bridge.check_owner()
        if self.toolbar._figure_dialog is not None:
            self.toolbar._figure_dialog.bridge.check_owner()
        self.stateChanged.emit()

    def close(self):
        self._closed = True
        self.toolbar.cancel_interaction()
        self.toolbar._close_subplots()
        self.toolbar._close_figure_options()
        self.refresh()

    @Slot(str, str, result=bool)
    def invoke(self, command, owner):
        if not self.allowed(owner):
            return False
        action = self._actions.get(command)
        if action is not None:
            if not action.isEnabled():
                return False
            action.trigger()
            return True
        commands = dict(home=self.toolbar.home, back=self.toolbar.back,
                        forward=self.toolbar.forward, pan=self.toolbar.pan,
                        zoom=self.toolbar.zoom, subplots=self.toolbar.configure_subplots,
                        customize=self.toolbar.edit_parameters)
        if command not in commands or (command == 'back' and not self.backEnabled
                                       or command == 'forward' and not self.forwardEnabled):
            return False
        commands[command]()
        self.stateChanged.emit()
        return True

    @Slot(str, str, result=bool)
    def setZoom(self, text, owner):
        if not self.allowed(owner) or text not in ('Fit', '100%', '150%', '200%'):
            return False
        if text != self.window.plot_page.zoom_text:
            self.window.plot_page.set_zoom(text)
            self.stateChanged.emit()
        return True
