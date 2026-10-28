"""Visible formal-page composition adapter over document-owned controllers."""
from __future__ import annotations
from PySide6.QtCore import QEvent, QPoint, Qt, Signal, QTimer
from PySide6.QtWidgets import QScrollArea, QSplitter, QVBoxLayout, QWidget
from .quick_ui import QuickPanel
from .formal_plot_controller import FormalPlotController
from .formal_preview import FormalPreview


class PlotPage(QWidget):
    presentationChanged = Signal(object)
    presentationError = Signal(str)
    exportRequested = Signal(str)
    formatSaveRequested = Signal()
    formatLoadRequested = Signal(str)
    formatDeleteRequested = Signal(str)
    formatApplyRequested = Signal(str)
    settingsCopyRequested = Signal()
    settingsPasteRequested = Signal()
    settingsCopyToRequested = Signal()
    comparisonRequested = Signal()
    batchExportRequested = Signal()
    batchCancelRequested = Signal()
    batchResultsRequested = Signal()
    displayScaleChanged = Signal(float)
    displayZoomChanged = Signal(str)
    propertyEditRequested = Signal(str, str)
    graphCopyRequested = Signal()
    analysisRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller = FormalPlotController(self, prompt_window=self._prompt_window)
        self.controller.set_enabled(self.isEnabled())
        for name in ('presentationChanged', 'presentationError', 'exportRequested', 'formatSaveRequested', 'formatLoadRequested', 'formatDeleteRequested', 'formatApplyRequested', 'settingsCopyRequested', 'settingsPasteRequested', 'settingsCopyToRequested', 'comparisonRequested', 'batchExportRequested', 'batchCancelRequested', 'batchResultsRequested', 'displayScaleChanged', 'displayZoomChanged', 'propertyEditRequested', 'graphCopyRequested', 'analysisRequested'):
            getattr(self.controller, name).connect(getattr(self, name))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        self.command_bar = QuickPanel('FormalCommands.qml', self.formal, self, bridge_property='bridge')
        self.command_bar.setAccessibleName('Active graph commands')
        layout.addWidget(self.command_bar)
        splitter = QSplitter()
        self.control_scroll = QScrollArea()
        self.control_scroll.setWidgetResizable(True)
        self.control_scroll.setMinimumWidth(0)
        controls = QWidget()
        self.control_layout = QVBoxLayout(controls)
        self.control_layout.setContentsMargins(4, 4, 4, 4)
        # MainWindow moves its one shared DAT Color Scale / View Range / Labels
        # set here. These are the only native property controls in this host.
        self.properties_panel = QuickPanel('FormalProperties.qml', self.formal, controls, bridge_property='bridge')
        self.control_layout.addWidget(self.properties_panel)
        self.control_layout.addStretch()
        self.control_scroll.setWidget(controls)
        root = self.properties_panel.rootObject()
        root.revealY.connect(self._reveal_y)
        self.formal.focusRequested.connect(self._focus_properties)
        self.preview = FormalPreview(self, controller=self.controller.preview)
        splitter.addWidget(self.control_scroll)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([370, 630])
        layout.addWidget(splitter, 1)

    def _prompt_window(self):
        # Resolve the actual visible composition window only at prompt opening.
        window = self.window()
        window.winId()
        return window.windowHandle()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.EnabledChange and hasattr(self, 'controller'):
            self.controller.set_enabled(self.isEnabled())
        return result

    presentation = property(lambda self: self.controller.presentation,
        lambda self, value: setattr(self.controller, 'presentation', value))
    presentation_error = property(lambda self: self.controller.presentation_error,
        lambda self, value: setattr(self.controller, 'presentation_error', value))
    rendered_page = property(lambda self: self.controller.rendered_page,
        lambda self, value: setattr(self.controller, 'rendered_page', value))
    expected_owner = property(lambda self: self.controller.expected_owner,
        lambda self, value: setattr(self.controller, 'expected_owner', value))
    formal = property(lambda self: self.controller.formal)
    zoom_text = property(lambda self: self.controller.zoom_text)
    selected_format = property(lambda self: self.controller.selected_format)
    display_scale = property(lambda self: self.controller.display_scale)
    dimensions_text = property(lambda self: self.controller.dimensions_text)
    status_text = property(lambda self: self.controller.status_text)

    def dispose(self):
        return self.controller.dispose()

    def set_mutation_guard(self, guard):
        return self.controller.set_mutation_guard(guard)

    def set_input_guard(self, guard):
        return self.controller.set_input_guard(guard)

    def command_enabled(self, key):
        return self.controller.command_enabled(key)

    def command_tooltip(self, key):
        return self.controller.command_tooltip(key)

    def set_command_availability(self, key, enabled, tooltip=None):
        return self.controller.set_command_availability(key, enabled, tooltip)

    def dispatch_command(self, key):
        return self.controller.dispatch_command(key)

    def reveal_property(self, kind, identifier=''):
        return self.controller.reveal_property(kind, identifier)

    def cancel_interaction(self):
        return self.controller.cancel_interaction()

    def set_format_names(self, names, selected=None):
        return self.controller.set_format_names(names, selected)

    def set_transfer_availability(self, available, source_label):
        return self.controller.set_transfer_availability(available, source_label)

    def set_batch_state(self, *, visible=None, maximum=None, value=None, status=None, tooltip=None, cancel_visible=None, export_enabled=None, results_enabled=None):
        return self.controller.set_batch_state(visible=visible, maximum=maximum, value=value, status=status, tooltip=tooltip, cancel_visible=cancel_visible, export_enabled=export_enabled, results_enabled=results_enabled)

    def has_pending_project_edits(self):
        return self.controller.has_pending_project_edits()

    def finish_editor(self):
        return self.controller.finish_editor()

    def publish_presentation(self, presentation):
        return self.controller.publish_presentation(presentation)

    def _move_title(self, position):
        return self.controller._move_title(position)

    def add_annotation(self, kind, text=''):
        return self.controller.add_annotation(kind, text)

    def _commit_annotations(self, annotations, selected_id=None):
        return self.controller._commit_annotations(annotations, selected_id)

    def select_annotation(self, identifier):
        return self.controller.select_annotation(identifier)

    def paper_display_size(self):
        return self.controller.paper_display_size()

    def validate_annotation_geometry(self, item):
        return self.controller.validate_annotation_geometry(item)

    def _replace_annotation(self, item):
        return self.controller._replace_annotation(item)

    def delete_annotation(self):
        return self.controller.delete_annotation()

    def set_presentation(self, presentation):
        return self.controller.set_presentation(presentation)

    def set_colorbar_norms(self, norms):
        return self.controller.set_colorbar_norms(norms)

    def number_sample(self):
        return self.controller.number_sample()

    def set_status_message(self, message):
        return self.controller.set_status_message(message)

    def invalidate(self, owner_token, message=''):
        return self.controller.invalidate(owner_token, message)

    def commit_rendered_page(self, page):
        return self.controller.commit_rendered_page(page)

    def set_zoom(self, zoom):
        return self.controller.set_zoom(zoom)

    def take_control_scroll(self):
        self.control_scroll.setParent(None)
        return self.control_scroll

    def take_command_bar(self):
        self.command_bar.setParent(None)
        return self.command_bar

    def _focus_properties(self, _section, _field):
        # QML item focus alone does not transfer QWidget keyboard ownership
        # from the SVG canvas. Hidden cached pages must not steal that owner.
        if self.properties_panel.isVisible():
            self.properties_panel.setFocus(Qt.OtherFocusReason)

    def _reveal_y(self, y):
        # The Properties root occupies one native scroll item; reveal the
        # requested QML row, including the shared groups before this panel.
        def reveal():
            point = self.properties_panel.mapTo(self.control_scroll.widget(), QPoint(0, round(y)))
            self.control_scroll.ensureVisible(point.x(), point.y(), 0, 48)
        QTimer.singleShot(0, self, reveal)

