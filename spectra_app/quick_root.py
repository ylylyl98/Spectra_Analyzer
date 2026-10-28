"""Production Qt Quick window and scene attachments for the application owner."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Property, QEvent, QTimer, QUrl, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
import shiboken6

from .main_window import ApplicationController
from .analysis_canvas_scene import AnalysisCanvasScene
from .analysis_linecut_scene import AnalysisLinecutScene
from .formal_preview_scene import FormalPreviewScene


class QuickApplication(QObject):
    """Own windows and their disposable scene attachments, never scientific state."""
    def __init__(self, controller=None, parent=None):
        super().__init__(parent)
        self.controller = controller or ApplicationController()
        self.controller.setParent(self)
        self.controller._quick_host = self
        self.controller.floatProperties(self.controller._annotation_settings.value(
            'quick_shell/properties_floating', False, type=bool))
        self._properties_floating = self.controller.propertiesFloating
        self.controller.shellChanged.connect(self._properties_mode_changed)
        self.engine = QQmlApplicationEngine(self)
        self._reports = []
        self._detached = False
        self._disposed = False
        self.dat_scene = AnalysisCanvasScene(self.controller.dat_canvas, self.engine, self)
        self.preview_scene = FormalPreviewScene(self.controller.workbench_preview, self.engine, self)
        self.linecut_scene = AnalysisLinecutScene(self.controller.workbench_linecut_canvas, self.engine, self)
        self.engine.setInitialProperties(dict(application=self.controller, host=self))
        self.engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / 'ApplicationRoot.qml')))
        roots = self.engine.rootObjects()
        if len(roots) != 1 or not isinstance(roots[0], QQuickWindow):
            self.detach_scenes()
            raise RuntimeError('The production Quick application did not construct its window.')
        self.window = roots[0]
        self.controller.root_window = self.window
        self.window.installEventFilter(self)
        self._floating_properties = self.window.findChild(QQuickWindow, 'floatingPropertiesWindow')
        # Keep the real QML owner wrapper alive when window() readers mirror
        # that QObject parent into Shiboken's wrapper ownership tree.
        self._floating_properties_owner = (QObject.parent(self._floating_properties)
                                           if self._floating_properties is not None else None)
        if self._floating_properties is not None:
            self._floating_properties.installEventFilter(self)
        for name, scene in (('applicationDatCanvas', self.dat_scene),
                            ('applicationWorkbenchPreview', self.preview_scene),
                            ('applicationLinecut', self.linecut_scene)):
            item = self.window.findChild(QObject, name)
            if item is None:
                raise RuntimeError(f'Missing application scene: {name}')
            scene.bind_item(item)
        self.controller._update_project_title()

    datBridge = Property(QObject, lambda self: self.controller.dat_canvas, constant=True)
    previewBridge = Property(QObject, lambda self: self.controller.workbench_preview, constant=True)
    linecutBridge = Property(QObject, lambda self: self.controller.workbench_linecut_canvas, constant=True)
    datGeneration = Property(int, lambda self: self.dat_scene.generation, constant=True)
    previewGeneration = Property(int, lambda self: self.preview_scene.generation, constant=True)
    linecutGeneration = Property(int, lambda self: self.linecut_scene.generation, constant=True)
    projectPanelWidth = Property(float, lambda self: self.controller._annotation_settings.value(
        'quick_shell/project_width', 240, type=float), constant=True)
    propertiesPanelWidth = Property(float, lambda self: self.controller._annotation_settings.value(
        'quick_shell/properties_width', 350, type=float), constant=True)
    floatingPropertiesGeometry = Property('QVariantMap', lambda self: {
        name: self.controller._annotation_settings.value('quick_shell/properties_geometry/' + name, default, type=int)
        for name, default in (('x', 0), ('y', 0), ('width', 390), ('height', 760))}, constant=True)

    @Slot(float, float)
    def rememberPanelWidths(self, project, properties):
        settings = self.controller._annotation_settings
        if self.controller.projectPanelVisible and project >= 170:
            settings.setValue('quick_shell/project_width', round(project))
        if self.controller.propertiesVisible and not self.controller.propertiesFloating and properties >= 280:
            settings.setValue('quick_shell/properties_width', round(properties))

    @Slot()
    def hideProperties(self):
        self.controller.dat_details_action.setChecked(False)

    def _properties_mode_changed(self):
        floating = self.controller.propertiesFloating
        if floating == self._properties_floating:
            return
        self._properties_floating = floating
        self.controller._annotation_settings.setValue('quick_shell/properties_floating', floating)

    @Slot(bool)
    def moveProperties(self, floating):
        if bool(floating) == self.controller.propertiesFloating:
            return
        editor = None
        properties = self.window.findChild(QObject, 'applicationProperties')
        for window in (getattr(self, 'window', None), getattr(self, '_floating_properties', None)):
            focus = window.activeFocusItem() if window is not None else None
            if properties is not None and focus is not None and (focus is properties or properties.isAncestorOf(focus)):
                editor = focus
                break
        inspector = self.controller.xyInspectorBridge if editor is not None else None
        # Capture before the controller's notify signal reparents the editor tree.
        # A fresh focus transition also renews its input owner after blur retirement.
        if editor is not None:
            editor.setFocus(False)
        if inspector is not None:
            inspector.suspendBlur()
        self.controller.floatProperties(floating)

        def restore():
            if (self._disposed or self.controller._closing or not self.controller.propertiesVisible
                    or self.controller.propertiesFloating != bool(floating)):
                return
            if inspector is not None and self.controller.xyInspectorBridge is not inspector:
                return
            if inspector is not None and shiboken6.isValid(inspector):
                inspector.suspendBlur()
            target = self._floating_properties if floating else self.window
            if target is None or not shiboken6.isValid(target):
                return
            target.requestActivate()
            if editor is not None and shiboken6.isValid(editor) and editor.isVisible():
                editor.forceActiveFocus()
        QTimer.singleShot(0, self, restore)

    def show(self):
        self.window.show()

    def eventFilter(self, watched, event):
        if watched in (getattr(self, 'window', None), getattr(self, '_floating_properties', None)):
            if (watched is getattr(self, '_floating_properties', None)
                    and watched.isVisible() and event.type() in (QEvent.Move, QEvent.Resize)):
                geometry = watched.geometry()
                settings = self.controller._annotation_settings
                for name in ('x', 'y', 'width', 'height'):
                    settings.setValue('quick_shell/properties_geometry/' + name, getattr(geometry, name)())
            if watched is getattr(self, 'window', None) and event.type() == QEvent.Close:
                self.controller.closeEvent(event)
                return not event.isAccepted()
            if event.type() == QEvent.KeyPress:
                focus = watched.activeFocusItem()
                editor = focus is not None and (focus.metaObject().indexOfProperty('cursorPosition') >= 0)
                sequence = QKeySequence(event.keyCombination())
                for action in self.controller.findChildren(QAction):
                    if any(sequence.matches(key) == QKeySequence.ExactMatch for key in action.shortcuts()):
                        if editor and sequence in (QKeySequence('Ctrl+Z'), QKeySequence('Ctrl+Y'), QKeySequence('Ctrl+Shift+Z')):
                            return False
                        graph_actions = self.controller._graph_command_actions
                        if action in [graph_actions[key] for key in ('undo', 'redo', 'rescale', 'cancel')]:
                            if not self._graph_shortcut_allowed(watched, focus):
                                return False
                        if action.isEnabled():
                            action.trigger()
                            event.accept()
                            return True
        return super().eventFilter(watched, event)

    def _graph_shortcut_allowed(self, window, focus):
        if window is not self.window:
            return False
        names = set()
        item = focus
        while item is not None:
            names.add(item.objectName())
            item = item.parentItem()
        if self.controller.outerView == 'analysis':
            if not names.intersection(('applicationDatCanvas', 'applicationWorkbenchPreview', 'AnalysisNavigation', 'analysisCommands')):
                return False
            return (self.controller.analysis_workbench.allowed() if self.controller.workbenchActive
                    else self.controller.dat_canvas.allowed(self.controller.dat_canvas.ownerToken))
        documents = self.controller.graph_documents
        key = documents.registry.active_key
        if key not in documents.registry.open_keys():
            return False
        content = documents.content_item(key)
        item = focus
        in_document = False
        while item is not None:
            in_document = in_document or item is content
            item = item.parentItem()
        if not in_document and not names.intersection(('formalCommands', 'XYCommandsRoot', 'XYNavigationRoot')):
            return False
        from .xy_plot_controller import XYPlotController
        document = documents.document_for(key)
        if isinstance(document, XYPlotController):
            item = focus
            while item is not None:
                if item.objectName() == 'XYCanvasRoot':
                    owner = item.property('keyboardOwner')
                    return (document.canvas_bridge.allowed(owner)
                            and document.command_bridge.authorize(document.command_bridge.ownerToken))
                if item.metaObject().indexOfProperty('actionOwner') >= 0:
                    return document.command_bridge.authorize(item.property('actionOwner'))
                item = item.parentItem()
            return False
        lease = documents.registry.capture_input(key)
        return documents.registry.authorize(lease)

    def detach_scenes(self):
        if self._detached:
            return
        self._detached = True
        self.controller.graph_documents.detach()
        for scene in (self.dat_scene, self.preview_scene, self.linecut_scene):
            scene.detach()

    def show_batch_report(self, text):
        component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / 'BatchReport.qml')))
        report = component.createWithInitialProperties(dict(reportText=str(text)))
        if report is None:
            raise RuntimeError('\n'.join(error.toString() for error in component.errors()))
        QQmlEngine.setObjectOwnership(report, QQmlEngine.CppOwnership)
        QObject.setParent(report, self)
        report.setTransientParent(self.window)
        self._reports.append(report)
        report.show()
        return report

    def dispose(self):
        """Explicit application shutdown, after the guarded window close succeeds."""
        if self._disposed:
            return
        self._disposed = True
        self.detach_scenes()
        for report in self._reports:
            report.close()
        self._reports.clear()
        self.window.hide()
        # Destroy QML while every referenced bridge remains alive.
        shiboken6.delete(self.engine)
        self.controller.root_window = None
        self.controller.dat_canvas.dispose()
        self.controller.workbench_preview.dispose()
        self.controller.workbench_linecut_canvas.dispose()
        for key in self.controller.graph_documents.keys():
            self.controller.graph_documents.document_for(key).dispose()
        self.controller._fallback_plot_page.dispose()

