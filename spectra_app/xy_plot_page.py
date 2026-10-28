"""Visible hybrid XY adapter; science, drafts and actions belong to its controller."""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal, QEvent, QUrl, QSignalBlocker
from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QScrollArea, QSplitter, QVBoxLayout, QWidget, QTabWidget
from .xy_plot_controller import XYPlotController, draw_comparison
from .xy_canvas import XYCanvasView
from .xy_analysis_panel import XYAnalysisPanel
from .quick_ui import QuickPanel


class XYPlotPage(QWidget):
    changed = Signal()
    historyChanged = Signal()
    analysisRequested = Signal()
    sourceRequested = Signal(str)
    addDataRequested = Signal()
    importDataRequested = Signal()
    inspectorVisibilityRequested = Signal(bool)

    def __init__(self, workspace, comparison_id):
        super().__init__()
        self._inspector_hosted = False
        self._view_disposed = False
        self.controller = XYPlotController(workspace, comparison_id, self)
        for name in ('changed', 'historyChanged', 'analysisRequested', 'sourceRequested',
                     'addDataRequested', 'importDataRequested'):
            getattr(self.controller, name).connect(getattr(self, name))
        self.controller.inspectorVisibilityRequested.connect(self._set_inspector_visible)
        self.controller.inspectorSectionRequested.connect(self._reveal_inspector)
        self.controller.canvasFocusRequested.connect(self._focus_canvas)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.command_bar = QuickPanel('XYCommands.qml', self.command_bridge, bridge_property='bridge')
        self.command_bar.setObjectName('XYCommandsHost')
        self.navigation_view = QuickPanel('XYNavigation.qml', self.navigation_bridge, bridge_property='bridge')
        self.navigation_view.setObjectName('XYNavigationHost')
        self.command_bar.installEventFilter(self)
        self.navigation_view.installEventFilter(self)
        layout.addWidget(self.navigation_view)
        self.splitter = QSplitter(Qt.Horizontal)
        self.canvas_view = XYCanvasView(self)
        self.preview = self.canvas_view
        self.splitter.addWidget(self.preview)
        self.inspector = QWidget()
        self.inspector.installEventFilter(self)
        self.inspector.setMinimumWidth(210)
        self.inspector.setMaximumWidth(420)
        inspector_layout = QVBoxLayout(self.inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setAccessibleName('Comparison inspector sections')
        self.inspector_tabs.tabBar().setFocusPolicy(Qt.StrongFocus)
        inspector_layout.addWidget(self.inspector_tabs)
        self.curves_panel = QuickPanel('XYInspectorCurves.qml', self.inspector_bridge, expanding=True, bridge_property='bridge')
        self.display_panel = QuickPanel('XYInspectorDisplay.qml', self.inspector_bridge, expanding=True, bridge_property='bridge')
        self.inspector_tabs.addTab(self.curves_panel, 'Curves')
        analysis_scroll = QScrollArea()
        analysis_scroll.setWidgetResizable(True)
        analysis_scroll.setFrameShape(QScrollArea.NoFrame)
        self.analysis_panel = XYAnalysisPanel(self.controller.analysis_controller)
        analysis_scroll.setWidget(self.analysis_panel)
        self.inspector_tabs.addTab(analysis_scroll, 'Analysis')
        self.inspector_tabs.addTab(self.display_panel, 'Display')
        self.inspector_tabs.currentChanged.connect(self._inspector_section_changed)
        self.splitter.addWidget(self.inspector)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setSizes([620, 330])
        self.splitter.setCollapsible(1, False)
        self._inspector_width = 330
        layout.addWidget(self.splitter, 1)
        for panel in (self.curves_panel, self.display_panel):
            panel.installEventFilter(self)
        self.installEventFilter(self)
        self.inspector_bridge.bind_panel(0, self.curves_panel.rootObject(),
            hybrid_report=lambda: (self.curves_panel.isVisible(), self.curves_panel.isEnabled(), self.curves_panel.hasFocus()))
        self.inspector_bridge.bind_panel(2, self.display_panel.rootObject(),
            hybrid_report=lambda: (self.display_panel.isVisible(), self.display_panel.isEnabled(), self.display_panel.hasFocus()))

    def _focus_canvas(self):
        if self.canvas_view.isVisible() and self.canvas_view.isEnabled():
            self.canvas_view.setFocus(Qt.MouseFocusReason)

    def _reveal_inspector(self, section, field):
        self.set_inspector_checked(True)
        self._set_inspector_visible(True)
        with QSignalBlocker(self.inspector_tabs):
            self.inspector_tabs.setCurrentIndex(section)
        if section == 1:
            self.inspector_tabs.widget(1).ensureWidgetVisible(self.analysis_panel)
            return
        panel = self.curves_panel if section == 0 else self.display_panel
        if field == 'interval':
            self.inspector_tabs.tabBar().setFocus()
            panel.rootObject().setProperty('showInterval', True)
        elif panel.isVisible() and panel.isEnabled():
            panel.setFocus(Qt.OtherFocusReason)
            editor = panel.rootObject().findChild(QQuickItem, 'xyField_' + field)
            if editor is not None:
                editor.forceActiveFocus(Qt.OtherFocusReason)

    def _set_inspector_visible(self, visible):
        if self._inspector_hosted:
            self.inspectorVisibilityRequested.emit(visible)
            return
        if not visible:
            self._inspector_width = max(210, self.splitter.sizes()[1])
        self.inspector.setVisible(visible)
        if visible:
            self.splitter.setSizes([max(120, self.splitter.width()-self._inspector_width), self._inspector_width])

    def bind_canvas_actions(self, actions):
        self.controller.bind_canvas_actions(actions)
        for action in actions.values():
            self.canvas_view.addAction(action)

    def eventFilter(self, watched, event):
        if hasattr(self, 'controller'):
            if event.type() in (QEvent.Hide, QEvent.ParentChange):
                self.controller.retire_input()
            elif event.type() in (QEvent.Show, QEvent.EnabledChange):
                self.canvas_view.report_presentation()
                self.controller.refresh_input_state()
        return super().eventFilter(watched, event)

    def event(self, event):
        if event.type() == QEvent.DeferredDelete and hasattr(self, 'controller'):
            self.dispose()
        return super().event(event)

    def dispose(self):
        if self._view_disposed:
            return
        self._view_disposed = True
        self.controller.dispose()
        self.canvas_view.dispose()
        for panel in (self.command_bar, self.navigation_view, self.curves_panel, self.display_panel, self.analysis_panel):
            panel.setSource(QUrl())

    @property
    def workspace(self):
        return self.controller.workspace

    @property
    def comparison_id(self):
        return self.controller.comparison_id

    @property
    def comparison(self):
        return self.controller.comparison

    @property
    def figure(self):
        return self.controller.figure

    @property
    def canvas(self):
        return self.controller.canvas

    @property
    def axes(self):
        return self.controller.axes

    @property
    def toolbar(self):
        return self.controller.toolbar

    @property
    def style_actions(self):
        return self.controller.style_actions

    @property
    def analyze_action(self):
        return self.controller.analyze_action

    @property
    def resume_analysis_action(self):
        return self.controller.resume_analysis_action

    @property
    def add_data_action(self):
        return self.controller.add_data_action

    @property
    def add_sheets_action(self):
        return self.controller.add_sheets_action

    @property
    def add_files_action(self):
        return self.controller.add_files_action

    @property
    def inspector_action(self):
        return self.controller.inspector_action

    @property
    def command_bridge(self):
        return self.controller.command_bridge

    @property
    def navigation_bridge(self):
        return self.controller.navigation_bridge

    @property
    def canvas_bridge(self):
        return self.controller.canvas_bridge

    @property
    def inspector_bridge(self):
        return self.controller.inspector_bridge

    @property
    def analysis_controller(self):
        return self.controller.analysis_controller

    @property
    def _appearance_edit(self):
        return self.controller._appearance_edit

    @property
    def _disposed(self):
        return self.controller._disposed

    @property
    def _active_curve_id(self):
        return self.controller._active_curve_id

    @property
    def _selection_ids(self):
        return self.controller._selection_ids

    @property
    def _edit_history(self):
        return self.controller._edit_history

    @property
    def _canvas_actions(self):
        return self.controller._canvas_actions

    @property
    def _preview_styles(self):
        return self.controller._preview_styles

    @property
    def _display_status(self):
        return self.controller._display_status

    @property
    def _input_guard(self):
        return self.controller._input_guard

    def set_input_guard(self, *args, **kwargs):
        return self.controller.set_input_guard(*args, **kwargs)

    def refresh_input_state(self, *args, **kwargs):
        return self.controller.refresh_input_state(*args, **kwargs)

    def set_add_data_enabled(self, *args, **kwargs):
        return self.controller.set_add_data_enabled(*args, **kwargs)

    def set_inspector_checked(self, *args, **kwargs):
        return self.controller.set_inspector_checked(*args, **kwargs)

    def edit_x_interval(self, *args, **kwargs):
        return self.controller.edit_x_interval(*args, **kwargs)

    def _inspector_section_changed(self, *args, **kwargs):
        return self.controller._inspector_section_changed(*args, **kwargs)

    def _selected_ids(self, *args, **kwargs):
        return self.controller._selected_ids(*args, **kwargs)

    def sync_graph_name(self, *args, **kwargs):
        return self.controller.sync_graph_name(*args, **kwargs)

    def set_curve_selection(self, *args, **kwargs):
        return self.controller.set_curve_selection(*args, **kwargs)

    def revert_editor(self, *args, **kwargs):
        return self.controller.revert_editor(*args, **kwargs)

    def refresh(self, *args, **kwargs):
        return self.controller.refresh(*args, **kwargs)

    def refresh_sources(self, *args, **kwargs):
        return self.controller.refresh_sources(*args, **kwargs)

    def validate_editor(self, *args, **kwargs):
        return self.controller.validate_editor(*args, **kwargs)

    def has_pending_project_edits(self, *args, **kwargs):
        return self.controller.has_pending_project_edits(*args, **kwargs)

    def finish_editor(self, *args, **kwargs):
        return self.controller.finish_editor(*args, **kwargs)

    def can_undo(self, *args, **kwargs):
        return self.controller.can_undo(*args, **kwargs)

    def can_redo(self, *args, **kwargs):
        return self.controller.can_redo(*args, **kwargs)

    def undo(self, *args, **kwargs):
        return self.controller.undo(*args, **kwargs)

    def redo(self, *args, **kwargs):
        return self.controller.redo(*args, **kwargs)

    def render(self, *args, **kwargs):
        return self.controller.render(*args, **kwargs)

    def svg_bytes(self, *args, **kwargs):
        return self.controller.svg_bytes(*args, **kwargs)

    def export(self, *args, **kwargs):
        return self.controller.export(*args, **kwargs)

    def set_curve_visibility(self, *args, **kwargs):
        return self.controller.set_curve_visibility(*args, **kwargs)

    def import_palette(self, *args, **kwargs):
        return self.controller.import_palette(*args, **kwargs)

