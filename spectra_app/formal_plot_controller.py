"""Document-owned formal presentation, commands and drafts without widget state."""
from __future__ import annotations

from dataclasses import replace
import math
from uuid import uuid4
from PySide6.QtCore import QObject, Signal
from .formal_plot_bridge import FormalPlotBridge
from .formal_preview_controller import FormalPreviewController


from .plot_presentation import PlotAnnotation, PlotPresentation
from .plot_annotations import coordinates_to_page
from .svg_export import _bar_ticks, colorbar_labels_rounded


class FormalPlotController(QObject):
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

    def __init__(self, parent=None, *, prompt_window=None):
        super().__init__(parent)
        self.presentation = PlotPresentation()
        self.presentation_error = None
        self.rendered_page = None
        self.expected_owner = None
        self._colorbar_norms = {}
        self._zoom = 'Fit'
        self.display_scale = 1.
        self.dimensions_text = ''
        self.status_text = ''
        self.enabled = True
        self.retired = False
        self._input_generation = 0
        self._prompt_window = prompt_window
        self.formal = FormalPlotBridge(self)
        self.destroyed.connect(self.formal.retire_prompt)
        self.preview = FormalPreviewController(self)
        self.preview.propertyEditRequested.connect(self.propertyEditRequested)
        self.preview.graphCopyRequested.connect(lambda: self.formal.command('copy_svg'))
        self.preview.titleMoved.connect(self._move_title)
        self.preview.annotationSelected.connect(self.select_annotation)
        self.preview.annotationMoved.connect(self._replace_annotation)
        self.preview.annotationDeleteRequested.connect(self.delete_annotation)
        self.preview.displayScaleChanged.connect(self._set_display_scale)
        self.preview.set_mutation_guard(lambda: self.formal.allowed(authorize=False))
        self.preview.set_input_authorization(self.formal.allowed)
        self.set_presentation(self.presentation)

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled != self.enabled:
            self.enabled = enabled
            self._input_generation += 1
            self.preview._retire_gesture()
            self.formal.retire_prompt()
            self.formal.sync()

    def set_prompt_window(self, window):
        """Supply a QWindow or a callable returning the actual presentation window."""
        self._prompt_window = window

    def prompt_window(self):
        from PySide6.QtGui import QWindow
        window = self._prompt_window() if callable(self._prompt_window) else self._prompt_window
        if not isinstance(window, QWindow):
            raise ValueError('An actual presentation window is required for annotation entry.')
        return window

    def set_input_guard(self, guard):
        self.formal.set_input_guard(guard)

    def dispose(self):
        """Retire a discarded document immediately, before deferred deletion."""
        if self.retired:
            return
        self.invalidate(('removed',))
        self.retired = True
        self.enabled = False
        for entry in self.formal._commands.values():
            entry['enabled'] = False
        self.preview.dispose()
        self.formal.sync()

    @property
    def zoom_text(self):
        return self._zoom

    @property
    def selected_format(self):
        return self.formal._selected_format

    def set_mutation_guard(self, guard):
        self.formal.set_guard(guard)
        self.preview.set_mutation_guard(lambda: self.formal.allowed(authorize=False))

    def command_enabled(self, key):
        return self.formal.state['commands'][key]['enabled']

    def command_tooltip(self, key):
        return self.formal._commands[key]['tooltip']

    def set_command_availability(self, key, enabled, tooltip=None):
        entry = self.formal._commands[key]
        entry['enabled'] = bool(enabled)
        if tooltip is not None:
            entry['tooltip'] = tooltip
        self.formal.sync_command_availability()

    def dispatch_command(self, key):
        if self.retired:
            return
        if key.startswith('export_'):
            self.exportRequested.emit(key.removeprefix('export_'))
            return
        named = {'format_load': self.formatLoadRequested, 'format_delete': self.formatDeleteRequested,
                 'format_apply': self.formatApplyRequested}
        if key in named:
            named[key].emit(self.selected_format)
            return
        signals = {'analyze': self.analysisRequested, 'copy_svg': self.graphCopyRequested,
            'settings_copy': self.settingsCopyRequested, 'settings_paste': self.settingsPasteRequested,
            'settings_copy_to': self.settingsCopyToRequested, 'comparison': self.comparisonRequested,
            'format_save': self.formatSaveRequested, 'batch_export': self.batchExportRequested,
            'batch_cancel': self.batchCancelRequested, 'batch_results': self.batchResultsRequested}
        signals[key].emit()

    def reveal_property(self, kind, identifier=''):
        return self.formal.reveal(kind, identifier)

    def cancel_interaction(self):
        self.preview.cancel_interaction()
        # Escape cancels canvas selection/drag only. Annotation drafts remain
        # bound to their selected editor until Apply or formal source capture.

    def set_format_names(self, names, selected=None):
        names = list(names)
        selected = selected or self.selected_format
        self.formal._formats = names
        self.formal._selected_format = selected if selected in names else names[0] if names else ''
        for key in ('format_load', 'format_delete', 'format_apply'):
            self.formal._commands[key]['enabled'] = bool(names)
        self.formal.sync()

    def set_transfer_availability(self, available, source_label):
        guidance = ('Capture the current DAT settings and validated formal page.' if available else
                    'Load a DAT and correct any page editor errors first.')
        for key in ('settings_copy', 'settings_copy_to', 'comparison'):
            self.formal._commands[key].update(enabled=bool(available), tooltip=guidance)
        self.formal._commands['settings_paste'].update(enabled=bool(available and source_label is not None),
            tooltip=f'Paste frozen settings from {source_label}' if source_label else 'Copy settings from a valid source page first.')
        self.formal._commands['comparison']['tooltip'] = ('Open a read-only snapshot of the current DAT; its source and exports stay frozen.' if available else guidance)
        self.formal._clipboard = (f'Copied settings: {source_label} (frozen snapshot)' if source_label else
                                 'No copied settings. Copy a valid source page first.')
        self.formal.sync()

    def set_batch_state(self, *, visible=None, maximum=None, value=None, status=None, tooltip=None,
                        cancel_visible=None, export_enabled=None, results_enabled=None):
        for key, value_ in (('visible', visible), ('maximum', maximum), ('value', value),
                            ('status', status), ('tooltip', tooltip), ('cancelVisible', cancel_visible)):
            if value_ is not None:
                self.formal._batch[key] = value_
        if export_enabled is not None:
            self.formal._commands['batch_export']['enabled'] = export_enabled
        if results_enabled is not None:
            self.formal._commands['batch_results']['enabled'] = results_enabled
        if cancel_visible is not None:
            self.formal._commands['batch_cancel']['enabled'] = cancel_visible
        self.formal.sync()

    def has_pending_project_edits(self):
        return self.formal.has_drafts()

    def finish_editor(self):
        self.formal.finish_editor()

    def publish_presentation(self, presentation):
        changed = presentation != self.presentation
        self.presentation = presentation
        self.preview.set_geometry(self.preview.snapshot, presentation)
        self._update_dimensions_summary()
        if changed:
            self.presentationChanged.emit(presentation)
        self.formal.sync()

    def _move_title(self, position):
        if self.formal.allowed() and self.rendered_page is not None:
            self.publish_presentation(replace(self.presentation, title_position=position))

    def add_annotation(self, kind, text='', properties_source=None, properties_owner=None):
        if not self.formal.allowed(properties_source=properties_source, properties_owner=properties_owner) or self.rendered_page is None:
            raise ValueError('Wait for a valid formal page before adding an annotation.')
        item = PlotAnnotation('a' + uuid4().hex, kind=kind, text=text,
            position=(.15, .85) if kind == 'text' else (.3, .7), target=(.6, .45) if kind == 'arrow' else None,
            target_space='axes-fraction', font_role='numeric' if kind == 'arrow' else 'text')
        self._commit_annotations((*self.presentation.annotations, item), item.id)
        return item

    def _commit_annotations(self, annotations, selected_id=None):
        identifiers = {item.id for item in annotations}
        if self.formal._selected_annotation not in identifiers:
            self.formal._annotation_drafts.clear()
        self.formal._select_annotation_drafts(selected_id or '')
        self.preview.selected_id = selected_id
        self.publish_presentation(replace(self.presentation, annotations=tuple(annotations)))

    def select_annotation(self, identifier):
        self.formal.selectAnnotation(identifier)
        if self.formal.allowed() and self.formal._selected_annotation == identifier:
            self.formal.revealRequested.emit('annotations', 'annotation_text')

    def paper_display_size(self):
        """Current SVG paper dimensions used by its coordinate conversion."""
        return self.preview.paper_display_size()

    def validate_annotation_geometry(self, item):
        if self.rendered_page is not None and self.preview.snapshot is not None:
            size = self.paper_display_size()
            coordinates_to_page(item.position, size, self.rendered_page, self.preview.snapshot,
                                self.presentation, item.coordinate_space)
            if item.target is not None:
                coordinates_to_page(item.target, size, self.rendered_page, self.preview.snapshot,
                                    self.presentation, item.target_space)

    def _replace_annotation(self, item, properties_source=None, properties_owner=None):
        if not self.formal.allowed(properties_source=properties_source, properties_owner=properties_owner):
            return
        self.validate_annotation_geometry(item)
        self._commit_annotations(tuple(item if old.id == item.id else old for old in self.presentation.annotations), item.id)

    def delete_annotation(self, properties_source=None, properties_owner=None):
        if not self.formal.allowed(properties_source=properties_source, properties_owner=properties_owner):
            return
        selected = self.formal._selected_annotation or self.preview.selected_id
        if selected:
            self._commit_annotations(tuple(item for item in self.presentation.annotations if item.id != selected))

    def set_presentation(self, presentation):
        self.presentation = presentation
        self.preview.set_geometry(self.preview.snapshot, presentation)
        # Projection preserves unfinished Quick text; accepted commits clear
        # their own draft keys before publishing the immutable replacement.
        self._update_dimensions_summary()
        self.formal.sync()

    def _update_dimensions_summary(self, *, current_page=False):
        page = self.rendered_page if current_page else None
        if page is not None and page.owner_token == self.expected_owner:
            width, height = page.figure_size_in
            dpi = page.heatmap_dpi
            frame = f'{page.plot_size_in[0]:.3f} × {page.plot_size_in[1]:.3f} in (actual)'
        else:
            width, height, dpi = self.presentation.width_in, self.presentation.height_in, self.presentation.heatmap_dpi
            frame = ('Auto (preview pending)' if self.presentation.frame_width_in is None else
                     f'{self.presentation.frame_width_in:.3f} × {self.presentation.frame_height_in:.3f} in (requested; preview pending)')
        sizes = [f'{value:.3f}' for value in (width, height)]
        sizes = [value[:-1] if value.endswith('0') else value for value in sizes]
        pixels = tuple(round(value * dpi) for value in (width, height))
        self.dimensions_text = (f'Page including labels and colorbars: {sizes[0]} × {sizes[1]} in '
            f'({width*25.4:.1f} × {height*25.4:.1f} mm)\nData plot frame: {frame}\n'
            f'PNG: {pixels[0]} × {pixels[1]} px @ {dpi} DPI')

    def set_colorbar_norms(self, norms):
        self._colorbar_norms = dict(norms)
        self.formal.sync()

    def number_sample(self):
        bar_id = self.formal._selected_bar
        norm = self._colorbar_norms.get(bar_id)
        warnings = list(self.rendered_page.warnings) if self.rendered_page is not None else []
        sample = 'Selected bar unavailable'
        if norm is not None:
            style = self.presentation.colorbar
            custom = style.ticks.get(bar_id)
            try:
                values, labels, _positions = _bar_ticks(norm, custom, style)
                fallback = False
            except ValueError:
                values, labels, _positions = _bar_ticks(norm, None, style)
                custom, fallback = None, True
            sample = ' | '.join(labels)
            if fallback:
                warnings.append('Stored custom ticks are outside the current scale; sample shows automatic ticks.')
            if custom is not None and custom.labels is not None:
                warnings.append('Manual labels override the number format.')
            else:
                if len(set(labels)) < len(labels):
                    warnings.append('This precision produces duplicate labels.')
                if colorbar_labels_rounded(values, style):
                    warnings.append('Rounded display; tick values and color limits are unchanged.')
        return sample, ' '.join(warnings)

    def set_status_message(self, message):
        self.status_text = message
        self.formal.sync()

    def invalidate(self, owner_token, message='', *, preserve_input=False):
        # Scientific/presentation revisions retain the canvas selection. A new
        # dataset, project lifetime or graph retires it along with the gesture.
        # Snapshot tokens omit the optional graph suffix after revision fields.
        if self.retired:
            return
        previous_owner = self.expected_owner
        # Render-only updates may keep an editor's authority, but never across
        # dataset, lifetime, science, view, graph, or backwards revision changes.
        keep_input = (preserve_input and previous_owner is not None and owner_token is not None
                      and len(previous_owner) >= 5 and len(owner_token) >= 5
                      and previous_owner[:4] == owner_token[:4]
                      and previous_owner[5:] == owner_token[5:]
                      and owner_token[4] in (previous_owner[4], previous_owner[4] + 1))
        if not keep_input:
            self._input_generation += 1
        self.formal.retire_prompt()
        same_document = (previous_owner is not None and owner_token is not None
                         and previous_owner[:2] == owner_token[:2]
                         and previous_owner[5:] == owner_token[5:])
        selected_id = self.preview.selected_id if same_document else None
        title_selected = self.preview.title_selected if same_document else False
        self.expected_owner = owner_token
        self.rendered_page = None
        # Keep the last SVG paper as history while all geometry commands retire.
        self.preview.invalidate(owner_token)
        self.preview.selected_id = selected_id
        self.preview.title_selected = title_selected
        for key in ('export_svg', 'export_png', 'copy_svg'):
            self.formal._commands[key]['enabled'] = False
        self.status_text = message
        self._update_dimensions_summary()
        self.formal.sync()

    def commit_rendered_page(self, page):
        if self.retired or page.owner_token != self.expected_owner:
            return False
        if not self.preview.set_page(page):
            return False
        self.rendered_page = page
        self.formal._frame_auto_size = tuple(math.floor(value * 1000) / 1000 for value in page.plot_size_in)
        for key in ('export_svg', 'export_png', 'copy_svg'):
            self.formal._commands[key]['enabled'] = True
        self._update_dimensions_summary(current_page=True)
        self.status_text = ''
        self.formal.sync()
        return True

    def set_zoom(self, zoom):
        if zoom not in ('Fit', '100%', '150%', '200%'):
            raise ValueError('Unknown preview zoom.')
        self._zoom = zoom
        self.preview.set_zoom(zoom)
        self.formal.sync()
        self.displayZoomChanged.emit(zoom)

    def _set_display_scale(self, scale):
        if not math.isclose(self.display_scale, scale, rel_tol=1e-9):
            self.display_scale = scale
            self.displayScaleChanged.emit(scale)
