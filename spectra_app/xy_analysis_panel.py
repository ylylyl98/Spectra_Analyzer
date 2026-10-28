"""Actual visible XY analysis inspector adapter."""
from .quick_ui import QuickPanel


class XYAnalysisPanel(QuickPanel):
    def __init__(self, controller):
        self.controller = controller
        self.bridge = controller.bridge
        super().__init__('XYAnalysisPanel.qml', self.bridge, bridge_property='bridge')
        controller.revealRequested.connect(self.show)
        self.hide()

    @property
    def page(self):
        return self.controller.page

    @property
    def captured(self):
        return self.controller.captured

    @property
    def peaks(self):
        return self.controller.peaks

    @property
    def fit_result(self):
        return self.controller.fit_result

    @property
    def progress(self):
        return self.controller.progress

    @property
    def busy(self):
        return self.controller.busy

    @property
    def details_dialog(self):
        return self.controller.details_dialog

    @property
    def _seeds_dialog(self):
        return self.controller._seeds_dialog

    @property
    def _state(self):
        return self.controller._state

    @property
    def _disposed(self):
        return self.controller._disposed

    def _owns_selection(self, *args, **kwargs):
        return self.controller._owns_selection(*args, **kwargs)

    def _load_settings(self, *args, **kwargs):
        return self.controller._load_settings(*args, **kwargs)

    def analyze_selected(self, *args, **kwargs):
        return self.controller.analyze_selected(*args, **kwargs)

    def resume_analysis(self, *args, **kwargs):
        return self.controller.resume_analysis(*args, **kwargs)

    def finish_settings(self, *args, **kwargs):
        return self.controller.finish_settings(*args, **kwargs)

    def activate(self, *args, **kwargs):
        return self.controller.activate(*args, **kwargs)

    def filter_settings(self, *args, **kwargs):
        return self.controller.filter_settings(*args, **kwargs)

    def _settings_error(self, *args, **kwargs):
        return self.controller._settings_error(*args, **kwargs)

    def _records_committed(self, *args, **kwargs):
        return self.controller._records_committed(*args, **kwargs)

    def show_settings(self, *args, **kwargs):
        return self.controller.show_settings(*args, **kwargs)

    def _summary(self, *args, **kwargs):
        return self.controller._summary(*args, **kwargs)

    def _publish_progress(self, *args, **kwargs):
        return self.controller._publish_progress(*args, **kwargs)

    def _persist(self, *args, **kwargs):
        return self.controller._persist(*args, **kwargs)

    def settings(self, *args, **kwargs):
        return self.controller.settings(*args, **kwargs)

    def current_input(self, *args, **kwargs):
        return self.controller.current_input(*args, **kwargs)

    def _set_context(self, *args, **kwargs):
        return self.controller._set_context(*args, **kwargs)

    def sync(self, *args, **kwargs):
        return self.controller.sync(*args, **kwargs)

    def _refresh_bridge(self, *args, **kwargs):
        return self.controller._refresh_bridge(*args, **kwargs)

    def _set_status(self, *args, **kwargs):
        return self.controller._set_status(*args, **kwargs)

    def _remove_overlays(self, *args, **kwargs):
        return self.controller._remove_overlays(*args, **kwargs)

    def start(self, *args, **kwargs):
        return self.controller.start(*args, **kwargs)

    def background_finished(self, *args, **kwargs):
        return self.controller.background_finished(*args, **kwargs)

    def draw_overlays(self, *args, **kwargs):
        return self.controller.draw_overlays(*args, **kwargs)

    def save_results(self, *args, **kwargs):
        return self.controller.save_results(*args, **kwargs)

    def set_seeds(self, *args, **kwargs):
        return self.controller.set_seeds(*args, **kwargs)

    def edit_seeds(self, *args, **kwargs):
        return self.controller.edit_seeds(*args, **kwargs)

    def create_analysis_graph(self, *args, **kwargs):
        return self.controller.create_analysis_graph(*args, **kwargs)

    def _save_results(self, *args, **kwargs):
        return self.controller._save_results(*args, **kwargs)

    def dispose(self, *args, **kwargs):
        return self.controller.dispose(*args, **kwargs)

