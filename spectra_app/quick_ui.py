"""Small Qt Quick presentation adapters for the existing application owners.

Scientific state lives in DatasetSession. Only unfinished editor text belongs to
this layer, scoped to its scientific owner so switching documents cannot lose it.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Property, QEvent, QTimer, QUrl, Signal, Slot
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QSizePolicy

from .analysis_document import analysis_owner
from .project_model import ProjectBridge


class CommandAction(QObject):
    changed = Signal()

    def __init__(self, key, action, parent):
        super().__init__(parent)
        self._key, self._action = key, action
        action.changed.connect(self.changed)

    key = Property(str, lambda self: self._key, constant=True)
    text = Property(str, lambda self: self._action.text().replace('&', ''), notify=changed)
    tooltip = Property(str, lambda self: self._action.toolTip(), notify=changed)
    enabled = Property(bool, lambda self: self._action.isEnabled(), notify=changed)
    checked = Property(bool, lambda self: self._action.isChecked(), notify=changed)
    checkable = Property(bool, lambda self: self._action.isCheckable(), notify=changed)

    @Slot()
    def trigger(self):
        if self._action.isEnabled():
            self._action.trigger()


class CommandsBridge(QObject):
    changed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self._actions = []

    actions = Property('QVariantList', lambda self: self._actions, notify=changed)

    def bind(self, actions):
        self._actions = [CommandAction(key, action, self) for key, action in actions]
        self.changed.emit()

    def sync(self):
        for action in self._actions:
            action.changed.emit()


class NavigationBridge(QObject):
    changed = Signal()
    def __init__(self, parent):
        super().__init__(parent)
        self._owner = None
    labels = Property('QStringList', lambda self: ['Data', 'Analysis', 'View'], constant=True)
    currentIndex = Property(int, lambda self: self._owner._task_index if self._owner else 0, notify=changed)
    enabled = Property(bool, lambda self: self._owner is not None and not self._owner._closing, notify=changed)
    def bind(self, owner):
        self._owner = owner
        owner.shellChanged.connect(self.changed)
        self.changed.emit()
    @Slot(int)
    def setCurrentIndex(self, index):
        if self.enabled:
            self._owner.request_task(index)


class ProcessingBridge(QObject):
    stateChanged = Signal()
    draftChanged = Signal()

    def __init__(self, window, parent):
        super().__init__(parent)
        self._window = window
        self._drafts = {}

    def _session(self):
        return self._window.workspace.active or self._window._unregistered_session

    def owner(self):
        return analysis_owner(self._session())

    def _key(self):
        return self._session().dataset_id, id(self.owner())

    mode = Property(str, lambda self: self.owner().pending.mode, notify=stateChanged)
    smoothing = Property(bool, lambda self: self.owner().pending.smooth, notify=stateChanged)
    fixed = Property(bool, lambda self: self._session().fixed_recipes['processing'].enabled,
                     notify=stateChanged)
    windowLength = Property(int, lambda self: self.owner().pending.window_length, notify=stateChanged)
    windowDraft = Property(str, lambda self: self._drafts.get(self._key(), str(self.windowLength)),
                           notify=draftChanged)
    hasDraft = Property(bool, lambda self: self.windowDraft != str(self.windowLength), notify=draftChanged)
    busy = Property(bool, lambda self: self._window._project_busy(), notify=stateChanged)
    enabled = Property(bool, lambda self: not self.busy and not self._window._close_pending
                       and self._window._workbench_document() is None,
                       notify=stateChanged)
    smoothingEnabled = Property(bool, lambda self: self.enabled and self.mode == 'energy_second_derivative',
                                notify=stateChanged)
    windowEnabled = Property(bool, lambda self: self.smoothingEnabled and self.smoothing,
                             notify=stateChanged)

    @staticmethod
    def _parse_window(text):
        text = text.strip()
        if not text or not text.isascii() or not text.isdecimal():
            return None
        text = text.lstrip('0')
        if not text or len(text) > 4:
            return None
        value = int(text)
        return value if 5 <= value <= 1001 and value % 2 else None

    def _draft_error(self):
        if self._parse_window(self.windowDraft) is None:
            return 'Processing SG window must be odd and from 5 to 1001 points.'
        return ''

    error = Property(str, lambda self: self._draft_error() if self.hasDraft or self.mode ==
                     'energy_second_derivative' else '', notify=stateChanged)

    def validation_error(self):
        # Original does not consume the smoothing editor. Its unfinished text
        # remains available when the user returns to derivative mode.
        return self.error if self.mode == 'energy_second_derivative' else ''

    def _status(self):
        window = self._window
        applied = 'D²' if window._processing_mode == 'energy_second_derivative' else 'Original'
        message = f'Applied: {applied}'
        if window.current_cube is None:
            return message + ' — no data loaded.'
        pending = (self.mode, self.smoothing, self.windowLength)
        actual = (window._processing_mode, window._processing_smooth, window._processing_window_length)
        derivative = self.mode == 'energy_second_derivative'
        if window._background_job_owner == 'processing' and window._processing_request is not None:
            message += ' | Applying processing…'
        elif pending[0] != actual[0] or derivative and pending != actual or derivative and self.hasDraft:
            recipe = 'Original'
            if derivative:
                if not self.smoothing:
                    recipe = 'D², no smoothing'
                elif self.error:
                    recipe = 'D², SG window needs correction'
                else:
                    recipe = f'D², SG {self._parse_window(self.windowDraft)} points'
            message += f' | Pending: {recipe} — choose Apply processing.'
        if not self.error and derivative and self.smoothing:
            base = window._base_cube or window.current_cube
            note = window._sg_window_note(self._parse_window(self.windowDraft), base.energy.size)
            if note:
                message += '\n' + note
        return message

    status = Property(str, _status, notify=stateChanged)
    applyEnabled = Property(bool, lambda self: self.enabled and self._window.current_cube is not None
                            and not self.validation_error(), notify=stateChanged)

    def _change(self, **values):
        if not self.enabled or self._window._restoring_dataset:
            return
        owner = self.owner()
        pending = replace(owner.pending, **values)
        if pending != owner.pending:
            self._window._cancel_processing_job()
            owner.pending = pending
            self._window._remember_fixed_processing()
            self._window._schedule_project_modified()
        self.syncFromOwner()

    @Slot(str)
    def setMode(self, mode):
        if mode in ('original', 'energy_second_derivative'):
            self._change(mode=mode)

    @Slot(bool)
    def setSmoothing(self, smoothing):
        self._change(smooth=smoothing)

    @Slot(bool)
    def setFixed(self, fixed):
        if self.enabled and not self._window._restoring_dataset:
            self._session().fixed_recipes['processing'].enabled = fixed
            self._window._remember_fixed_processing()
            self._window._schedule_project_modified()
            self.syncFromOwner()

    @Slot(str)
    def setWindowDraft(self, text):
        if not self.enabled or self._window._restoring_dataset:
            return
        if text == str(self.windowLength):
            self._drafts.pop(self._key(), None)
        else:
            self._drafts[self._key()] = text
        self._window._schedule_project_modified()
        self.syncFromOwner()

    @Slot(result=bool)
    def commitWindow(self):
        if self._draft_error():
            self.syncFromOwner()
            return False
        if not self.hasDraft:
            return True
        if not self.enabled:
            return False
        value = self._parse_window(self.windowDraft)
        self._drafts.pop(self._key(), None)
        self._change(window_length=value)
        return True

    def commit_for_save(self):
        # Save must validate every owner's editor, including hidden documents.
        owners = self._owners()
        self._drafts = {key: text for key, text in self._drafts.items() if key in owners}
        if self._drafts:
            for text in self._drafts.values():
                if self._parse_window(text) is None:
                    raise ValueError('Correct the pending processing window before saving the project.')
            for key, text in self._drafts.items():
                owners[key].pending = replace(owners[key].pending, window_length=self._parse_window(text))
            self._drafts.clear()
            self._window._remember_fixed_processing()
            self.syncFromOwner()

    def _owners(self):
        sessions = [self._window._unregistered_session, *self._window.workspace.datasets.values()]
        owners = {}
        for session in sessions:
            owners[(session.dataset_id, id(session))] = session
            for graph in session.graphs.values():
                if graph.analysis is not None:
                    owner = graph.analysis.science
                    owners[(session.dataset_id, id(owner))] = owner
        return owners

    def finish_editor(self):
        # Valid text commits on owner changes; invalid text is retained for its
        # owner and is explained by Save rather than silently rewritten.
        if self.hasDraft and not self._draft_error():
            self.commitWindow()

    def has_project_drafts(self):
        owners = self._owners()
        return any(key in owners for key in self._drafts)

    def reset(self):
        self._drafts.clear()
        self.syncFromOwner()

    def snapshot_editor_drafts(self):
        return dict(self._drafts)

    def restore_editor_drafts(self, drafts):
        self._drafts = dict(drafts)
        self.syncFromOwner()

    @Slot()
    def apply(self):
        if self.enabled:
            self._window._on_generate_heatmap()

    @Slot()
    def syncFromOwner(self):
        self.draftChanged.emit()
        self.stateChanged.emit()


class ApplicationBridge(QObject):
    def __init__(self, window):
        super().__init__(window)
        self._commands = CommandsBridge(self)
        self._navigation = NavigationBridge(self)
        self._processing = ProcessingBridge(window, self)
        self._project = ProjectBridge(window, self)

    commands = Property(QObject, lambda self: self._commands, constant=True)
    navigation = Property(QObject, lambda self: self._navigation, constant=True)
    processing = Property(QObject, lambda self: self._processing, constant=True)
    project = Property(QObject, lambda self: self._project, constant=True)


class QuickPanel(QQuickWidget):
    """QQuickWidget with a height driven by its width-aware QML content."""
    def __init__(self, filename, bridge, parent=None, *, expanding=False, bridge_property=None):
        super().__init__(parent)
        self.setObjectName(filename.removesuffix('.qml') + 'Host')
        self.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self._expanding = expanding
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding if expanding else QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self._sync_height)
        if bridge_property is None:
            self.rootContext().setContextProperty('appBridge', bridge)
        else:
            self.setInitialProperties({bridge_property: bridge})
        self.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / 'qml' / filename)))
        if self.status() == QQuickWidget.Error:
            raise RuntimeError('\n'.join(error.toString() for error in self.errors()))
        root = self.rootObject()
        self._sync_font()
        if root is not None and not self._expanding:
            root.implicitHeightChanged.connect(self._height_timer.start)
            self._sync_height()

    def _sync_height(self):
        root = self.rootObject()
        if root is not None and not self._expanding:
            self.setFixedHeight(max(1, round(root.implicitHeight())))

    def _sync_font(self):
        root = self.rootObject() if hasattr(self, '_height_timer') else None
        if root is not None and root.metaObject().indexOfProperty('controlFont') >= 0:
            root.setProperty('controlFont', self.font())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.ApplicationFontChange):
            self._sync_font()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._expanding:
            self._height_timer.start()
