"""Qt Quick target, selective format, and batch export dialog windows."""
from .file_picker import FilePicker

from .plot_dialog_bridge import ApplyFormatBridge, BatchExportBridge, PlotTargetsBridge
from .quick_dialog import QuickDialog


class PlotTargetsDialog(QuickDialog):
    def __init__(self, sessions, active_id, parent=None):
        bridge = PlotTargetsBridge(sessions, active_id)
        super().__init__(bridge, 'PlotTargetsDialog.qml', parent, title='Choose active graphs')
        self.sessions = bridge.sessions

    def selected_ids(self):
        return self.bridge.selected_ids()


class ApplyFormatDialog(QuickDialog):
    def __init__(self, sessions, active_id, template, parent=None, *, source_label=None, initial_options=None,
                 graph_targets=None, selected_targets=None, baseline_sessions=None,
                 adjustment=False, initial_annotation_pairs=None):
        bridge = ApplyFormatBridge(sessions, active_id, template, source_label,
            initial_options=initial_options, graph_targets=graph_targets, selected_targets=selected_targets,
            baseline_sessions=baseline_sessions, adjustment=adjustment, initial_annotation_pairs=initial_annotation_pairs)
        super().__init__(bridge, 'ApplyFormatDialog.qml', parent, title=('Adjust last paste — ' if adjustment else 'Apply format — ') + template.name,
                         size=(940, 680), image_provider=bridge.image_provider)
        self.sessions, self.template = bridge.sessions, template

    def selected_ids(self):
        return self.bridge.selected_ids()

    def selected_graph_targets(self):
        return self.bridge.selected_graph_targets()

    def preference_options(self):
        return self.bridge.preference_options()

    def options(self):
        return self.bridge.options()

    def annotation_pairs(self):
        return self.bridge.annotation_pairs()


class BatchExportDialog(QuickDialog):
    def __init__(self, sessions, active_id, parent=None):
        bridge = BatchExportBridge(sessions, active_id)
        super().__init__(bridge, 'BatchExportDialog.qml', parent, title='Batch export formal pages', size=(660, 600))
        self.sessions = bridge.sessions
        bridge.browseRequested.connect(self._browse)

    def _browse(self):
        folder = FilePicker.getExistingDirectory(self, 'Batch output folder', self.bridge.destinationFolder)
        if folder:
            self.set_destination_folder(folder)

    def set_destination_label(self, label):
        self.bridge.set_destination_label(label)

    def set_destination_folder(self, folder):
        self.bridge.setDestinationFolder(str(folder))

    def selected_graph_targets(self):
        return self.bridge.selected_graph_targets()

    def selected_ids(self):
        return self.bridge.selected_ids()

    def formats(self):
        return self.bridge.formats()

    def destination(self):
        return self.bridge.destination()
