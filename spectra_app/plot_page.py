"""Native formal-page controls and a preview of the exact export SVG bytes."""
from __future__ import annotations

from dataclasses import replace
import math
from uuid import uuid4
from PySide6.QtCore import QByteArray, QEvent, QPoint, QPointF, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton,
                               QProgressBar, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
                               QMenu, QToolButton, QSizePolicy)

from .plot_presentation import ColorbarTicks, PlotAnnotation, PlotPresentation
from .plot_annotations import coordinates_to_page, page_to_coordinates
from .svg_export import _bar_ticks, colorbar_labels_rounded, render_svg_image


class SvgPagePreview(QWidget):
    annotationSelected = Signal(str)
    annotationMoved = Signal(object)
    annotationDeleteRequested = Signal()
    titleMoved = Signal(object)
    propertyEditRequested = Signal(str, str)
    graphCopyRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = QSvgRenderer(self)
        self.svg_bytes = b''
        self.page = None
        self.snapshot = None
        self.presentation = PlotPresentation()
        self.selected_id = None
        self._drag_annotation = None
        self._drag_endpoint = False
        self._drag_offset = QPointF()
        self._title_selected = False
        self._drag_title = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('Formal page annotation canvas')
        self.setToolTip('Double-click a title, note, axis or colorbar to edit it. Drag labels or arrow heads to move them. Escape cancels a drag; Ctrl+C copies the graph.')
        self.setMinimumSize(1, 1)

    def set_page(self, page):
        self.page = page
        self._drag_annotation = None
        self._drag_title = None
        self.svg_bytes = page.svg_bytes if page is not None else b''
        if page is not None:
            # The helper registers renderer-selected fonts for offscreen Qt.
            # Painting still reads the final bytes directly through QSvgRenderer.
            render_svg_image(page, dpi=72)
            if not self.renderer.load(QByteArray(self.svg_bytes)):
                raise ValueError('The generated SVG could not be previewed.')
        self.update()

    def set_geometry(self, snapshot, presentation):
        self.snapshot = snapshot
        self.presentation = presentation

    def annotation_point(self, annotation, endpoint=False):
        point=self._point(annotation,endpoint)
        return QPoint(round(point.x()),round(point.y()))

    def _title_point(self):
        position = self._drag_title or self.presentation.title_position
        x, y = coordinates_to_page(position, (self.width(), self.height()), self.page,
                                   self.snapshot, self.presentation, 'axes-fraction')
        return QPointF(x, y)

    def title_point(self):
        point = self._title_point()
        return QPoint(round(point.x()), round(point.y()))

    def _title_rect(self):
        point = self._title_point()
        font = QFont(self.presentation.font_family)
        font.setPixelSize(max(1, round(self.presentation.text_pt*self.width()/(self.presentation.width_in*72))))
        metrics = QFontMetricsF(font)
        lines = self.snapshot.title.splitlines() or ['']
        rect = QRectF(point.x()-3, point.y()-3,
                      max(10, *(metrics.horizontalAdvance(line) for line in lines))+6,
                      metrics.height()*len(lines)+6)
        left, bottom, width, height = self.page.axes_bounds
        frame = QRectF(left*self.width(), (1-bottom-height)*self.height(),
                       width*self.width(), height*self.height())
        return rect.intersected(frame)

    def _point(self, annotation, endpoint=False):
        position = annotation.target if endpoint else annotation.position
        space = annotation.target_space if endpoint else annotation.coordinate_space
        x,y = coordinates_to_page(position,(self.width(),self.height()),self.page,self.snapshot,
                                  self.presentation,space)
        return QPointF(x,y)

    def _label_rect(self, annotation):
        point = self._point(annotation)
        font = QFont(self.presentation.font_family)
        pt = self.presentation.numeric_pt if annotation.font_role=='numeric' else self.presentation.text_pt
        font.setPixelSize(max(1,round(pt*self.width()/(self.presentation.width_in*72))))
        metrics = QFontMetricsF(font)
        return QRectF(point.x()-3,point.y()-metrics.ascent()-3,
                      max(10,metrics.horizontalAdvance(annotation.text))+6,metrics.height()+6)

    def _selected(self):
        return next((item for item in self.presentation.annotations if item.id==self.selected_id),None)

    def hit_test(self, point):
        if self.page is None or self.snapshot is None:
            return None
        for item in reversed(self.presentation.annotations):
            if not item.visible:
                continue
            if item.kind=='arrow' and (self._point(item,True)-point).manhattanLength()<=12:
                return item,True
            if (self._point(item)-point).manhattanLength()<=12 or self._label_rect(item).contains(point):
                return item,False
            if item.kind=='arrow':
                a,b = self._point(item),self._point(item,True)
                dx,dy = b.x()-a.x(),b.y()-a.y()
                length = dx*dx+dy*dy
                t = max(0,min(1,((point.x()-a.x())*dx+(point.y()-a.y())*dy)/length)) if length else 0
                distance = (point-QPointF(a.x()+t*dx,a.y()+t*dy)).manhattanLength()
                if distance<=8:
                    return item,False
        return None

    def cancel_interaction(self):
        """Discard the pending drag and selection without emitting an edit."""
        self._drag_annotation = None
        self._drag_title = None
        self._title_selected = False
        self.selected_id = None
        self.update()

    def mouseDoubleClickEvent(self, event):
        if (event.button() != Qt.LeftButton or not self.isVisible() or self.page is None
                or self.snapshot is None or self.page.owner_token != self.snapshot.owner_token):
            super().mouseDoubleClickEvent(event)
            return
        point = event.position()
        # The measured rectangles already reflect exchange, clipping and the
        # exact renderer's tick layout; only page-to-widget scaling happens here.
        for key, rectangles in self.page.object_bounds.items():
            for left, bottom, width, height in rectangles:
                rect = QRectF(left*self.width(), (1-bottom-height)*self.height(),
                              width*self.width(), height*self.height())
                if rect.contains(point):
                    self._drag_annotation = None
                    self._drag_title = None
                    kind, _, identifier = key.partition(':')
                    self.propertyEditRequested.emit(kind, identifier)
                    event.accept()
                    return
        # Arrow lines/heads also select their annotation, but their bounding
        # boxes contain empty space and must not intercept unrelated objects.
        left, bottom, width, height = self.page.axes_bounds
        frame = QRectF(left*self.width(), (1-bottom-height)*self.height(),
                       width*self.width(), height*self.height())
        found = self.hit_test(point) if frame.contains(point) else None
        if found and found[0].kind == 'arrow':
            self._drag_annotation = None
            self._drag_title = None
            self.propertyEditRequested.emit('annotation', found[0].id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button()==Qt.LeftButton:
            if self.page is not None and self.snapshot is not None and self.snapshot.title and self._title_rect().contains(event.position()):
                self._title_selected = True
                self.selected_id = None
                self._drag_title = self.presentation.title_position
                self._drag_offset = self._title_point() - event.position()
                self.setFocus()
                self.update()
                event.accept()
                return
            found = self.hit_test(event.position())
            if found:
                item,endpoint = found
                self._title_selected = False
                self.selected_id=item.id
                self.annotationSelected.emit(item.id)
                self._drag_annotation=item
                self._drag_endpoint=endpoint
                self._drag_offset=self._point(item,endpoint)-event.position()
                self.setFocus()
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def _drag_to(self, point):
        if self._drag_title is not None:
            point = point + self._drag_offset
            position = page_to_coordinates((point.x(), point.y()), (self.width(), self.height()),
                                            self.page, self.snapshot, self.presentation, 'axes-fraction')
            self._drag_title = tuple(max(0., min(1., value)) for value in position)
            self.update()
            return
        annotation=self._drag_annotation
        if annotation is None:
            return
        point=point+self._drag_offset
        space=annotation.target_space if self._drag_endpoint else annotation.coordinate_space
        position=page_to_coordinates((point.x(),point.y()),(self.width(),self.height()),self.page,
                                      self.snapshot,self.presentation,space)
        self._drag_annotation=replace(annotation,**({'target':position} if self._drag_endpoint else {'position':position}))
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag_annotation is not None or self._drag_title is not None:
            self._drag_to(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_title is not None and event.button()==Qt.LeftButton:
            self._drag_to(event.position())
            position = self._drag_title
            self._drag_title = None
            self.titleMoved.emit(position)
            event.accept()
            return
        if self._drag_annotation is not None and event.button()==Qt.LeftButton:
            self._drag_to(event.position())
            item=self._drag_annotation
            self._drag_annotation=None
            self.annotationMoved.emit(item)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancel_interaction()
            event.accept()
            return
        if event.key() == Qt.Key_C and event.modifiers() == Qt.ControlModifier:
            self.graphCopyRequested.emit()
            event.accept()
            return
        item=self._selected()
        if item and event.key() in (Qt.Key_Delete,Qt.Key_Backspace):
            self.annotationDeleteRequested.emit()
            event.accept()
            return
        shifts={Qt.Key_Left:(-1,0),Qt.Key_Right:(1,0),Qt.Key_Up:(0,-1),Qt.Key_Down:(0,1)}
        if item and self.page is not None and event.key() in shifts:
            x,y=shifts[event.key()]
            step=10 if event.modifiers() & Qt.ShiftModifier else 1
            point=self._point(item)+QPointF(x*step,y*step)
            position=page_to_coordinates((point.x(),point.y()),(self.width(),self.height()),self.page,
                                          self.snapshot,self.presentation,item.coordinate_space)
            self.annotationMoved.emit(replace(item,position=position))
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        if self.svg_bytes and self.renderer.isValid():
            self.renderer.render(painter, QRectF(self.rect()))
            if self._title_selected and self.snapshot is not None and self.snapshot.title:
                painter.setPen(QPen(Qt.blue, 1, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self._title_rect())
            selected=self._drag_annotation or self._selected()
            if selected and selected.visible and self.snapshot is not None:
                painter.setPen(QPen(Qt.blue,1,Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self._label_rect(selected))
                point=self._point(selected)
                painter.drawEllipse(point,5,5)
                if selected.kind=='arrow':
                    target=self._point(selected,True)
                    painter.drawLine(point,target)
                    painter.drawEllipse(target,6,6)


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
        self.presentation = PlotPresentation()
        self.presentation_error = None
        self.rendered_page = None
        self.expected_owner = None
        self._loading = False
        self._colorbar_norms = {}
        self._zoom = 'Fit'
        self.display_scale = 1.
        self._command_actions = {}
        layout = QVBoxLayout(self)
        # Keep room for the paper at the supported window sizes even with the
        # shared native File/Edit/View menu above the task tabs.
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        self.command_bar = QWidget()
        self.command_bar.setAccessibleName('Active graph commands')
        commands = QVBoxLayout(self.command_bar)
        commands.setContentsMargins(0, 0, 0, 0)
        commands.setSpacing(2)
        layout.addWidget(self.command_bar)
        self.action_layout = QHBoxLayout()
        self.analyze_btn = QPushButton('Analyze')
        self.analyze_btn.setToolTip('Open this DAT in the full-size Analysis view.')
        self.analyze_btn.clicked.connect(self.analysisRequested)
        self.action_layout.addWidget(self.analyze_btn)
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(['Fit', '100%', '150%', '200%'])
        self.zoom_combo.currentTextChanged.connect(self.set_zoom)
        self.action_layout.addWidget(QLabel('Display:'))
        self.action_layout.addWidget(self.zoom_combo)
        self.export_svg_btn = QPushButton('Export SVG')
        self.export_png_btn = QPushButton('Export PNG')
        self.copy_graph_btn = QPushButton('Copy SVG')
        self.copy_graph_btn.setToolTip('Copy the same hybrid SVG as Export SVG, with embedded heatmap at the selected DPI (Ctrl+Alt+J).')
        self.copy_graph_btn.setEnabled(False)
        self.copy_graph_btn.clicked.connect(self.graphCopyRequested)
        for button, extension in ((self.export_svg_btn, 'svg'), (self.export_png_btn, 'png')):
            button.setEnabled(False)
            button.clicked.connect(lambda checked=False, ext=extension: self.exportRequested.emit(ext))
        self.action_layout.addStretch()
        self.dimensions_label = QLabel()
        self.dimensions_label.setWordWrap(True)
        self.dimensions_label.setAccessibleName('Export dimensions')
        self.copy_settings_btn = QPushButton('Copy settings')
        self.paste_settings_btn = QPushButton('Paste settings…')
        self.copy_settings_to_btn = QPushButton('Copy settings to active graphs…')
        self.comparison_btn = QPushButton('Open comparison window')
        for button, signal in ((self.copy_settings_btn, self.settingsCopyRequested),
                               (self.paste_settings_btn, self.settingsPasteRequested),
                               (self.copy_settings_to_btn, self.settingsCopyToRequested),
                               (self.comparison_btn, self.comparisonRequested)):
            button.clicked.connect(signal)
        self.settings_menu_btn = self._command_menu('Settings', (
            self.copy_settings_btn, self.paste_settings_btn, self.copy_settings_to_btn, self.comparison_btn))
        self.clipboard_label = QLabel('No copied settings. Copy a valid source page first.')
        self.clipboard_label.setWordWrap(True)
        self.clipboard_label.setTextFormat(Qt.PlainText)
        self.clipboard_label.setAccessibleName('Copied settings source')
        self.set_transfer_availability(False, None)
        self.format_combo = QComboBox()
        self.format_combo.setMinimumWidth(120)
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.format_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.format_combo.setAccessibleName('Named plot format')
        self.format_combo.setPlaceholderText('Saved format…')
        self.format_combo.setToolTip('Select a saved plot format, then use the Formats menu to load or apply it.')
        self.format_save_btn = QPushButton('Save format…')
        self.format_load_btn = QPushButton('Load for current')
        self.format_delete_btn = QPushButton('Delete format')
        self.format_apply_btn = QPushButton('Apply to active graphs…')
        self.batch_export_btn = QPushButton('Batch export…')
        self.format_save_btn.clicked.connect(self.formatSaveRequested)
        for button, signal in ((self.format_load_btn,self.formatLoadRequested),
                               (self.format_delete_btn,self.formatDeleteRequested),
                               (self.format_apply_btn,self.formatApplyRequested)):
            button.clicked.connect(lambda checked=False, destination=signal:
                                   destination.emit(self.format_combo.currentText()))
        self.batch_export_btn.clicked.connect(self.batchExportRequested)
        self.formats_menu_btn = self._command_menu('Formats', (
            self.format_save_btn, self.format_load_btn, self.format_delete_btn, self.format_apply_btn))
        batch = QVBoxLayout()
        batch_progress_row = QHBoxLayout()
        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        self.batch_status = QLabel('')
        self.batch_status.setWordWrap(True)
        self.batch_cancel_btn = QPushButton('Cancel batch')
        self.batch_cancel_btn.setVisible(False)
        self.batch_cancel_btn.clicked.connect(self.batchCancelRequested)
        self.batch_results_btn = QPushButton('Results…')
        self.batch_results_btn.setEnabled(False)
        self.batch_results_btn.clicked.connect(self.batchResultsRequested)
        batch_progress_row.addWidget(self.batch_progress, 1)
        batch_progress_row.addWidget(self.batch_cancel_btn)
        batch.addLayout(batch_progress_row)
        batch.addWidget(self.batch_status)
        self.batch_menu_btn = self._command_menu('Batch', (self.batch_export_btn, self.batch_results_btn))
        for button in (self.copy_graph_btn, self.export_svg_btn, self.export_png_btn):
            self.action_layout.addWidget(button)
        commands.addLayout(self.action_layout)
        format_row = QHBoxLayout()
        for button in (self.settings_menu_btn, self.formats_menu_btn, self.batch_menu_btn):
            format_row.addWidget(button)
        format_row.addWidget(self.format_combo, 1)
        self.clipboard_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.clipboard_label.hide()
        self.settings_menu_btn.setToolTip(self.clipboard_label.text())
        splitter = QSplitter()
        self.control_scroll = control_scroll = QScrollArea()
        control_scroll.setWidgetResizable(True)
        controls = QWidget()
        self.control_layout = QVBoxLayout(controls)
        self.control_layout.addLayout(batch)
        self.control_layout.addWidget(self.dimensions_label)
        self.control_layout.addLayout(format_row)
        control_scroll.setWidget(controls)
        control_scroll.setMinimumWidth(320)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setAlignment(Qt.AlignCenter)
        self.preview_scroll.viewport().installEventFilter(self)
        self.preview = SvgPagePreview()
        self.preview.propertyEditRequested.connect(self.propertyEditRequested)
        self.preview.graphCopyRequested.connect(self.graphCopyRequested)
        self.preview_scroll.setWidget(self.preview)
        splitter.addWidget(control_scroll)
        splitter.addWidget(self.preview_scroll)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.status_label = QLabel('Load a DAT file to prepare a formal page.')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._build_controls()
        for label in controls.findChildren(QLabel):
            label.setWordWrap(True)
        for spin in controls.findChildren(QAbstractSpinBox):
            # Native editors scroll long numbers while retaining every digit.
            spin.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        for check in controls.findChildren(QCheckBox):
            words = check.text().split()
            lines, line = [], ''
            for word in words:
                if line and len(line) + len(word) + 1 > 16:
                    lines.append(line)
                    line = word
                else:
                    line = f'{line} {word}'.strip()
            check.setText('\n'.join((*lines, line)))
        splitter.setSizes([370, 630])
        self.set_presentation(self.presentation)

    def _command_menu(self, title, buttons):
        """Route native menu actions through the existing command buttons."""
        tool = QToolButton(self)
        tool.setText(title)
        tool.setAccessibleName(f'{title} commands')
        tool.setFocusPolicy(Qt.StrongFocus)
        tool.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(tool)
        menu.setToolTipsVisible(True)
        tool.setMenu(menu)
        for button in buttons:
            button.setParent(self)
            button.hide()
            action = menu.addAction(button.text())
            action.setEnabled(button.isEnabled())
            action.setToolTip(button.toolTip())
            action.triggered.connect(lambda checked=False, command=button: command.click())
            self._command_actions[button] = action
            button.installEventFilter(self)
        return tool

    def take_control_scroll(self):
        """Host the original editor elsewhere without rebuilding its controls."""
        self.control_scroll.setParent(None)
        return self.control_scroll

    def take_command_bar(self):
        """Retain page-owned commands while a workspace hosts them once."""
        self.command_bar.setParent(None)
        return self.command_bar

    def property_editor(self, kind, identifier=''):
        """Return the existing editor for a displayed page object."""
        if kind == 'annotation':
            self.select_annotation(identifier)
            return self.annotation_text
        if kind in ('horizontal-axis', 'vertical-axis'):
            return self.x_mode_combo if kind == 'horizontal-axis' else self.y_mode_combo
        if kind == 'colorbar':
            self.bar_id_combo.setCurrentText(identifier)
            return self.tick_values_edit if self.custom_ticks.isChecked() else self.custom_ticks
        return None

    def cancel_interaction(self):
        self.preview.cancel_interaction()
        self.annotation_list.setCurrentRow(-1)

    def set_format_names(self, names, selected=None):
        selected = selected or self.format_combo.currentText()
        with QSignalBlocker(self.format_combo):
            self.format_combo.clear()
            self.format_combo.addItems(names)
            if selected in names:
                self.format_combo.setCurrentText(selected)
            elif names:
                self.format_combo.setCurrentIndex(0)
        for button in (self.format_load_btn,self.format_delete_btn,self.format_apply_btn):
            button.setEnabled(bool(names))

    def set_transfer_availability(self, available, source_label):
        guidance = 'Capture the current DAT settings and validated formal page.' if available else 'Load a DAT and correct any page editor errors first.'
        for button in (self.copy_settings_btn, self.copy_settings_to_btn, self.comparison_btn):
            button.setEnabled(available)
            button.setToolTip(guidance)
        self.paste_settings_btn.setEnabled(available and source_label is not None)
        self.paste_settings_btn.setToolTip(f'Paste frozen settings from {source_label}' if source_label else
                                         'Copy settings from a valid source page first.')
        self.comparison_btn.setToolTip('Open a read-only snapshot of the current DAT; its source and exports stay frozen.'
                                       if available else guidance)
        self.clipboard_label.setText(f'Copied settings: {source_label} (frozen snapshot)' if source_label else
                                    'No copied settings. Copy a valid source page first.')
        self.settings_menu_btn.setToolTip(self.clipboard_label.text())

    def has_pending_project_edits(self):
        """Inspect drafts without interpreting numbers, emitting or committing."""
        if self.presentation_error or self.font_edit.text() != self.presentation.font_family:
            return True
        for control in self.control_scroll.findChildren(QAbstractSpinBox):
            if control.isEnabled() and (not control.hasAcceptableInput()
                    or control.valueFromText(control.text()) != control.value()):
                return True
        ticks = self.presentation.colorbar.ticks.get(self.bar_id_combo.currentText())
        if self.custom_ticks.isChecked():
            try:
                values = tuple(float(value.strip()) for value in self.tick_values_edit.text().split(','))
                text = self.tick_labels_edit.text()
                labels = tuple(label.strip() for label in text.split('|')) if text.strip() else None
                if ticks is None or values != ticks.values or labels != ticks.labels:
                    return True
            except ValueError:
                return True
        item = self.preview._selected()
        return item is not None and self._edited_annotation(item) != item

    def finish_editor(self):
        """Commit pending typed values before a formal source capture."""
        # Interpreting one spin must not project the full presentation and erase
        # pending text/numbers in the other editors before they are read.
        self._loading = True
        try:
            for control in self.control_scroll.findChildren(QAbstractSpinBox):
                if not control.hasAcceptableInput():
                    raise ValueError('Correct the current page editor first: invalid numeric value.')
                control.interpretText()
            item = self.preview._selected()
            if item is not None:
                edited = self._edited_annotation(item)
                self.presentation = replace(self.presentation, annotations=tuple(
                    edited if annotation.id == item.id else annotation for annotation in self.presentation.annotations))
        finally:
            self._loading = False
        self._emit_presentation()
        if self.presentation_error:
            raise ValueError(f'Correct the current page editor first: {self.presentation_error}')

    def _form(self, name):
        box = QGroupBox(name)
        form = QFormLayout(box)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.control_layout.addWidget(box)
        return form

    def _number(self, form, label, minimum=.01, maximum=100., decimals=3, step=.05):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setKeyboardTracking(False)
        form.addRow(label, spin)
        return spin

    def _check(self, form, label):
        check = QCheckBox(label)
        form.addRow(check)
        check.toggled.connect(self._emit_presentation)
        return check

    def _build_controls(self):
        form = self._form('Page including labels and colorbars')
        self.width_spin = self._number(form, 'Width (in):', .1, 40, 8)
        self.height_spin = self._number(form, 'Height (in):', .1, 40)
        self.aspect_lock = self._check(form, 'Lock aspect ratio')
        self.width_spin.valueChanged.connect(lambda: self._dimension_changed('width_in'))
        self.height_spin.valueChanged.connect(lambda: self._dimension_changed('height_in'))
        self.numeric_spin = self._number(form, 'Numeric (pt):', 1, 100, 1, step=1)
        self.text_spin = self._number(form, 'Text (pt):', 1, 100, 1, step=1)
        self.font_edit = QLineEdit()
        form.addRow('Font:', self.font_edit)
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(1, 2400)
        self.dpi_spin.setSingleStep(100)
        self.dpi_spin.setKeyboardTracking(False)
        form.addRow('Heatmap DPI:', self.dpi_spin)
        self.include_peaks = self._check(form, 'Include selected peak labels')
        for spin in (self.numeric_spin, self.text_spin, self.dpi_spin):
            spin.valueChanged.connect(self._emit_presentation)
        self.font_edit.editingFinished.connect(self._emit_presentation)
        form = self._form('Data plot frame')
        frame_note = QLabel('Area inside the axes. Page size also includes labels and colorbars.')
        frame_note.setWordWrap(True)
        form.addRow(frame_note)
        self.frame_auto = self._check(form, 'Fit data frame to page automatically')
        self.frame_width_spin = self._number(form, 'Width (in):', .01, 40)
        self.frame_height_spin = self._number(form, 'Height (in):', .01, 40)
        for spin in (self.frame_width_spin, self.frame_height_spin):
            spin.valueChanged.connect(self._emit_presentation)
        form = self._form('Title position')
        title_note = QLabel('Position relative to the data plot frame (0–1). Drag the title in the preview to move it.')
        title_note.setWordWrap(True)
        form.addRow(title_note)
        self.title_x_spin = self._number(form, 'X (0–1):', 0, 1, 8, step=.01)
        self.title_y_spin = self._number(form, 'Y (0–1):', 0, 1, 8, step=.01)
        for control in (self.title_x_spin, self.title_y_spin):
            control.valueChanged.connect(self._emit_presentation)
        self.preview.titleMoved.connect(self._move_title)
        form = self._form('Axes')
        labels = {'exchange_xy': 'Exchange horizontal / vertical', 'flip_x': 'Flip horizontal',
                  'flip_y': 'Flip vertical', 'show_x': 'Show horizontal axis',
                  'show_y': 'Show vertical axis', 'show_x_label': 'Horizontal label',
                  'show_y_label': 'Vertical label', 'show_tick_labels': 'Tick labels',
                  'major_ticks': 'Major ticks', 'minor_ticks': 'Minor ticks',
                  'ticks_inside': 'Ticks inside', 'border': 'Border'}
        self.axis_controls = {key: self._check(form, label) for key, label in labels.items()}
        self.border_width_spin = self._number(form, 'Border width:', .01, 20, step=.1)
        for prefix, label in (('x', 'Horizontal'), ('y', 'Vertical')):
            mode = QComboBox(); mode.addItems(['Auto', 'By increment', 'By count'])
            mode.setToolTip('Manual increments and counts require linear coordinate axes.')
            form.addRow(f'{label} major ticks:', mode)
            setattr(self, f'{prefix}_mode_combo', mode)
            step = self._number(form, f'{label} increment:', .000001, 1e12, 6)
            setattr(self, f'{prefix}_step_spin', step)
            count = QSpinBox(); count.setRange(2, 100); count.setValue(7); count.setKeyboardTracking(False)
            form.addRow(f'{label} major count:', count)
            setattr(self, f'{prefix}_count_spin', count)
            minor = QSpinBox(); minor.setRange(0, 20); minor.setKeyboardTracking(False)
            minor.setToolTip('Count between major ticks on linear coordinate axes. On non-linear axes, 0 disables minors and 1 keeps automatic scale defaults.')
            form.addRow(f'{label} count between major ticks:', minor)
            setattr(self, f'{prefix}_minor_count_spin', minor)
            mode.currentTextChanged.connect(self._emit_presentation)
            count.valueChanged.connect(self._emit_presentation)
            minor.valueChanged.connect(self._emit_presentation)
        for spin in (self.border_width_spin, self.x_step_spin, self.y_step_spin):
            spin.valueChanged.connect(self._emit_presentation)
        form = self._form('Top colorbar')
        self.colorbar_visible = self._check(form, 'Visible')
        self.colorbar_label = self._check(form, 'Show signal label')
        self.colorbar_unit = self._check(form, 'Show signal unit')
        self.bar_width_spin = self._number(form, 'Preferred width (in):', .01, 40)
        self.bar_height_spin = self._number(form, 'Height (in):', .001, 20)
        self.bar_gap_spin = self._number(form, 'Between bars (in):', .001, 20)
        self.bar_frame_gap_spin = self._number(form, 'Above frame (in):', 0, 20)
        self.bar_alignment_combo = QComboBox()
        self.bar_alignment_combo.addItems(['right', 'center', 'left'])
        form.addRow('Alignment:', self.bar_alignment_combo)
        self.signal_position_combo = QComboBox()
        self.signal_position_combo.addItems(['left', 'above'])
        form.addRow('Signal position:', self.signal_position_combo)
        self.bar_extensions = self._check(form, 'Show under / over triangles')
        self.bar_format_combo = QComboBox()
        self.bar_format_combo.addItems(['Auto', 'Decimal', 'Scientific', 'Engineering'])
        form.addRow('Number format:', self.bar_format_combo)
        self.bar_precision_label = QLabel('Significant digits:')
        self.bar_precision_spin = QSpinBox()
        self.bar_precision_spin.setRange(1, 12)
        self.bar_precision_spin.setValue(4)
        self.bar_precision_spin.setKeyboardTracking(False)
        form.addRow(self.bar_precision_label, self.bar_precision_spin)
        self.bar_scientific_style_combo = QComboBox()
        self.bar_scientific_style_combo.addItem('Compact e', 'e')
        self.bar_scientific_style_combo.addItem('×10 superscript', 'times-ten')
        form.addRow('Scientific style:', self.bar_scientific_style_combo)
        self.bar_trim_zeros = self._check(form, 'Trim trailing zeros')
        self.bar_format_combo.currentTextChanged.connect(self._change_number_format)
        self.bar_precision_spin.valueChanged.connect(self._emit_presentation)
        self.bar_scientific_style_combo.currentIndexChanged.connect(self._emit_presentation)
        self.bar_sample_label = QLabel('Selected bar unavailable')
        self.bar_sample_label.setWordWrap(True)
        self.bar_sample_label.setAccessibleName('Selected colorbar sample labels')
        form.addRow('Sample labels:', self.bar_sample_label)
        self.bar_format_warning = QLabel()
        self.bar_format_warning.setWordWrap(True)
        self.bar_format_warning.setAccessibleName('Colorbar precision warning')
        form.addRow(self.bar_format_warning)
        for combo in (self.bar_alignment_combo, self.signal_position_combo):
            combo.currentTextChanged.connect(self._emit_presentation)
        for spin in (self.bar_width_spin, self.bar_height_spin, self.bar_gap_spin, self.bar_frame_gap_spin):
            spin.valueChanged.connect(self._emit_presentation)
        self.bar_id_combo = QComboBox()
        self.bar_id_combo.addItems(['global', 'region-1', 'region-2', 'region-3'])
        form.addRow('Bar:', self.bar_id_combo)
        self.custom_ticks = QCheckBox('Custom three ticks')
        form.addRow(self.custom_ticks)
        self.tick_values_edit = QLineEdit()
        self.tick_values_edit.setPlaceholderText('low, middle, high')
        self.tick_labels_edit = QLineEdit()
        self.tick_labels_edit.setPlaceholderText('Optional: label | label | label')
        form.addRow('Values:', self.tick_values_edit)
        form.addRow('Labels:', self.tick_labels_edit)
        self.bar_id_combo.currentTextChanged.connect(self._show_ticks)
        self.custom_ticks.toggled.connect(self._emit_presentation)
        self.tick_values_edit.editingFinished.connect(self._emit_presentation)
        self.tick_labels_edit.editingFinished.connect(self._emit_presentation)
        self._build_annotation_controls()
        self.control_layout.addStretch()

    def _move_title(self, position):
        self.presentation = replace(self.presentation, title_position=position)
        self.set_presentation(self.presentation)
        self.presentationChanged.emit(self.presentation)

    def _build_annotation_controls(self):
        form=self._form('Text and arrows')
        self._annotation_form = form
        actions=QVBoxLayout()
        self.add_text_btn=QPushButton('Add text…')
        self.add_arrow_btn=QPushButton('Add arrow…')
        self.add_text_btn.setEnabled(False)
        self.add_arrow_btn.setEnabled(False)
        self.add_text_btn.clicked.connect(self._on_add_text)
        self.add_arrow_btn.clicked.connect(self._on_add_arrow)
        actions.addWidget(self.add_text_btn); actions.addWidget(self.add_arrow_btn)
        form.addRow(actions)
        self.annotation_list=QListWidget()
        self.annotation_list.setMaximumHeight(100)
        self.annotation_list.setAccessibleName('Formal page annotations')
        self.annotation_list.currentRowChanged.connect(self._show_annotation)
        form.addRow(self.annotation_list)
        self.annotation_text=QLineEdit()
        form.addRow('Text / number:',self.annotation_text)
        self.annotation_space=QComboBox()
        self.annotation_target_space=QComboBox()
        for combo, name in ((self.annotation_space, 'Text or arrow tail coordinates'),
                            (self.annotation_target_space, 'Arrow head coordinates')):
            combo.addItem('Relative to plot frame (0–1)', 'axes-fraction')
            combo.addItem('Data coordinates', 'data')
            combo.setAccessibleName(name)
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        form.addRow('Text / tail coordinates:',self.annotation_space)
        self.annotation_x=self._number(form,'Text / tail X:',-1e15,1e15,8)
        self.annotation_y=self._number(form,'Text / tail Y:',-1e15,1e15,8)
        form.addRow('Arrow head coordinates:',self.annotation_target_space)
        self.annotation_target_x=self._number(form,'Arrow head X:',-1e15,1e15,8)
        self.annotation_target_y=self._number(form,'Arrow head Y:',-1e15,1e15,8)
        self.annotation_font_role=QComboBox()
        self.annotation_font_role.addItem('Text size', 'text')
        self.annotation_font_role.addItem('Numeric size', 'numeric')
        self.annotation_font_role.setAccessibleName('Annotation font size role')
        form.addRow('Use page font size:',self.annotation_font_role)
        self.annotation_visible=QCheckBox('Visible')
        form.addRow(self.annotation_visible)
        actions=QVBoxLayout()
        self.annotation_apply_btn=QPushButton('Apply annotation\nedit')
        self.annotation_delete_btn=QPushButton('Delete annotation')
        actions.addWidget(self.annotation_apply_btn); actions.addWidget(self.annotation_delete_btn)
        form.addRow(actions)
        self.annotation_apply_btn.clicked.connect(self._apply_annotation_edit)
        self.annotation_delete_btn.clicked.connect(self.delete_annotation)
        self.annotation_space.currentIndexChanged.connect(lambda:self._change_annotation_space(False))
        self.annotation_target_space.currentIndexChanged.connect(lambda:self._change_annotation_space(True))
        self.preview.annotationSelected.connect(self.select_annotation)
        self.preview.annotationMoved.connect(self._replace_annotation)
        self.preview.annotationDeleteRequested.connect(self.delete_annotation)
        self._annotation_editor_spaces=('axes-fraction','axes-fraction')

    def _on_add_text(self):
        text,accepted=QInputDialog.getMultiLineText(self,'Add text','Text (Unicode supported):','9T σ⁺')
        if accepted:
            self.add_annotation('text',text)

    def _on_add_arrow(self):
        text,accepted=QInputDialog.getText(self,'Add arrow','Optional number / label:')
        if accepted:
            self.add_annotation('arrow',text)

    def add_annotation(self, kind, text=''):
        if self.rendered_page is None:
            raise ValueError('Wait for a valid formal page before adding an annotation.')
        item=PlotAnnotation('a'+uuid4().hex,kind=kind,text=text,
                            position=(.15,.85) if kind=='text' else (.3,.7),
                            target=(.6,.45) if kind=='arrow' else None,
                            target_space='axes-fraction',font_role='numeric' if kind=='arrow' else 'text')
        self._commit_annotations((*self.presentation.annotations,item),item.id)
        return item

    def _commit_annotations(self, annotations, selected_id=None):
        self.presentation=replace(self.presentation,annotations=tuple(annotations))
        self._refresh_annotations(selected_id)
        self.presentationChanged.emit(self.presentation)

    def select_annotation(self, annotation_id):
        self.preview._title_selected = False
        for i in range(self.annotation_list.count()):
            if self.annotation_list.item(i).data(Qt.UserRole)==annotation_id:
                self.annotation_list.setCurrentRow(i)
                self._show_annotation()
                return

    def _refresh_annotations(self, selected_id=None):
        selected_id=selected_id or self.preview.selected_id
        with QSignalBlocker(self.annotation_list):
            self.annotation_list.clear()
            row=-1
            for i,item in enumerate(self.presentation.annotations):
                label=QListWidgetItem(f'{item.kind}: {item.text or "(unlabelled)"}' + (' (hidden)' if not item.visible else ''))
                label.setData(Qt.UserRole,item.id)
                self.annotation_list.addItem(label)
                if item.id==selected_id:
                    row=i
            self.annotation_list.setCurrentRow(row if row>=0 else (0 if self.presentation.annotations else -1))
        self.preview.presentation=self.presentation
        self._show_annotation()

    def _show_annotation(self, *_args):
        row=self.annotation_list.currentRow()
        item=self.presentation.annotations[row] if 0<=row<len(self.presentation.annotations) else None
        controls=(self.annotation_text,self.annotation_space,self.annotation_x,self.annotation_y,
                  self.annotation_font_role,self.annotation_visible,self.annotation_apply_btn,self.annotation_delete_btn)
        for widget in controls:
            widget.setEnabled(item is not None)
        for widget in (self.annotation_target_space,self.annotation_target_x,self.annotation_target_y):
            widget.setEnabled(item is not None and item.kind=='arrow')
            self._annotation_form.setRowVisible(widget, item is not None and item.kind=='arrow')
        if not self._loading:
            self.preview._title_selected = False
        self.preview.selected_id=item.id if item else None
        loading=self._loading; self._loading=True
        try:
            self.annotation_text.setText(item.text if item else '')
            if item:
                self.annotation_space.setCurrentIndex(self.annotation_space.findData(item.coordinate_space))
                self.annotation_x.setValue(item.position[0]); self.annotation_y.setValue(item.position[1])
                self.annotation_target_space.setCurrentIndex(self.annotation_target_space.findData(item.target_space))
                target=item.target or (0.,0.)
                self.annotation_target_x.setValue(target[0]); self.annotation_target_y.setValue(target[1])
                self.annotation_font_role.setCurrentIndex(self.annotation_font_role.findData(item.font_role))
                self.annotation_visible.setChecked(item.visible)
                self._annotation_editor_spaces=(item.coordinate_space,item.target_space)
                self._set_annotation_steps(False)
                self._set_annotation_steps(True)
                self._annotation_display_values = {control: control.value() for control in
                    (self.annotation_x, self.annotation_y, self.annotation_target_x, self.annotation_target_y)}
        finally:
            self._loading=loading
        self.preview.update()

    def _set_annotation_steps(self, endpoint):
        space = (self.annotation_target_space if endpoint else self.annotation_space).currentData()
        controls = ((self.annotation_target_x, self.annotation_target_y) if endpoint
                    else (self.annotation_x, self.annotation_y))
        snapshot = self.preview.snapshot
        for axis, control in zip(('x', 'y'), controls):
            coordinate = (getattr(snapshot, axis + '_label', '') or f'{axis.upper()} data units'
                          if space == 'data' else 'relative 0–1')
            label = f'{"Arrow head" if endpoint else "Text / tail"} {axis.upper()} ({coordinate})'
            field_label = self._annotation_form.labelForField(control)
            field_label.setText(label + ':')
            field_label.setWordWrap(True)
            control.setAccessibleName(label)
            step = .01
            if space == 'data' and snapshot is not None:
                values = sorted({float(value) for value in getattr(snapshot, axis) if math.isfinite(value)})
                spacing = sorted(b - a for a, b in zip(values, values[1:]) if b > a)
                if spacing:
                    step = spacing[len(spacing) // 2]
            control.setSingleStep(max(10 ** -control.decimals(), step))

    def _replace_annotation(self, item):
        if self.rendered_page is not None and self.preview.snapshot is not None:
            size=(self.preview.width(),self.preview.height())
            coordinates_to_page(item.position,size,self.rendered_page,self.preview.snapshot,
                                self.presentation,item.coordinate_space)
            if item.target is not None:
                coordinates_to_page(item.target,size,self.rendered_page,self.preview.snapshot,
                                    self.presentation,item.target_space)
        self._commit_annotations(tuple(item if old.id==item.id else old for old in self.presentation.annotations),item.id)

    def _change_annotation_space(self, endpoint):
        if self._loading:
            return
        combo=self.annotation_target_space if endpoint else self.annotation_space
        oldspace=self._annotation_editor_spaces[int(endpoint)]
        newspace=combo.currentData()
        if oldspace==newspace:
            return
        if self.rendered_page is None or self.preview.snapshot is None:
            with QSignalBlocker(combo):
                combo.setCurrentIndex(combo.findData(oldspace))
            self.status_label.setText('Wait for the current preview before converting annotation coordinates.')
            return
        xs=self.annotation_target_x if endpoint else self.annotation_x
        ys=self.annotation_target_y if endpoint else self.annotation_y
        try:
            size=(self.preview.width(),self.preview.height())
            point=coordinates_to_page((xs.value(),ys.value()),size,self.rendered_page,self.preview.snapshot,
                                      self.presentation,oldspace)
            x,y=page_to_coordinates(point,size,self.rendered_page,self.preview.snapshot,self.presentation,newspace)
            xs.setValue(x); ys.setValue(y)
            spaces=list(self._annotation_editor_spaces); spaces[int(endpoint)]=newspace
            self._annotation_editor_spaces=tuple(spaces)
            self._set_annotation_steps(endpoint)
        except (ValueError,OverflowError) as exc:
            with QSignalBlocker(combo):
                combo.setCurrentIndex(combo.findData(oldspace))
            self.status_label.setText(str(exc))

    def _edited_annotation(self, item):
        def position_value(control, saved):
            return saved if control.value() == self._annotation_display_values.get(control) else control.value()
        return replace(item, text=self.annotation_text.text(),
            position=(position_value(self.annotation_x, item.position[0]),
                      position_value(self.annotation_y, item.position[1])),
            coordinate_space=self.annotation_space.currentData(),
            target=(position_value(self.annotation_target_x, item.target[0]),
                    position_value(self.annotation_target_y, item.target[1])) if item.kind=='arrow' else None,
            target_space=self.annotation_target_space.currentData(),
            font_role=self.annotation_font_role.currentData(), visible=self.annotation_visible.isChecked())

    def _apply_annotation_edit(self):
        item=self.preview._selected()
        if item is None:
            return
        try:
            self._replace_annotation(self._edited_annotation(item))
        except (ValueError,TypeError) as exc:
            self.status_label.setText(f'Cannot edit annotation: {exc}')

    def delete_annotation(self):
        selected=self.preview.selected_id
        if selected:
            self.preview.selected_id=None
            self._commit_annotations(tuple(item for item in self.presentation.annotations if item.id!=selected))

    def _dimension_changed(self, field):
        if self._loading:
            return
        if self.aspect_lock.isChecked():
            if field == 'width_in':
                value = self.width_spin.value() * self.presentation.height_in / self.presentation.width_in
                with QSignalBlocker(self.height_spin):
                    self.height_spin.setValue(value)
            else:
                value = self.height_spin.value() * self.presentation.width_in / self.presentation.height_in
                with QSignalBlocker(self.width_spin):
                    self.width_spin.setValue(value)
        self._emit_presentation()

    def _emit_presentation(self, *_args):
        if self._loading:
            return
        automatic = self.frame_auto.isChecked()
        self.frame_width_spin.setEnabled(not automatic)
        self.frame_height_spin.setEnabled(not automatic)
        tick_fields = {}
        for prefix in ('x', 'y'):
            mode = getattr(self, f'{prefix}_mode_combo').currentText()
            step = getattr(self, f'{prefix}_step_spin')
            count = getattr(self, f'{prefix}_count_spin')
            step.setEnabled(mode == 'By increment'); count.setEnabled(mode == 'By count')
            tick_fields[f'{prefix}_major_step'] = step.value() if mode == 'By increment' else None
            tick_fields[f'{prefix}_major_count'] = count.value() if mode == 'By count' else None
            tick_fields[f'{prefix}_minor_count'] = getattr(self, f'{prefix}_minor_count_spin').value()
        try:
            def dimension(control, saved):
                # An unrelated editor must not replace an exact saved inch
                # value with the rounded value displayed by its spin box.
                if saved is not None and control.value() == self._dimension_display_values.get(control):
                    return saved
                return control.value()
            axis = replace(self.presentation.axis,
                           **{key: control.isChecked() for key, control in self.axis_controls.items()},
                           border_width=self.border_width_spin.value(),
                           **tick_fields)
            ticks = dict(self.presentation.colorbar.ticks)
            bar_id = self.bar_id_combo.currentText()
            if self.custom_ticks.isChecked():
                values = tuple(float(value.strip()) for value in self.tick_values_edit.text().split(','))
                labels_text = self.tick_labels_edit.text()
                labels = tuple(label.strip() for label in labels_text.split('|')) if labels_text.strip() else None
                ticks[bar_id] = ColorbarTicks(values, labels)
            else:
                ticks.pop(bar_id, None)
            colorbar = replace(self.presentation.colorbar, visible=self.colorbar_visible.isChecked(),
                               show_label=self.colorbar_label.isChecked(), show_unit=self.colorbar_unit.isChecked(),
                               width_in=dimension(self.bar_width_spin, self.presentation.colorbar.width_in),
                               height_in=dimension(self.bar_height_spin, self.presentation.colorbar.height_in),
                               gap_in=dimension(self.bar_gap_spin, self.presentation.colorbar.gap_in),
                               alignment=self.bar_alignment_combo.currentText(),
                               signal_position=self.signal_position_combo.currentText(),
                               frame_gap_in=dimension(self.bar_frame_gap_spin, self.presentation.colorbar.frame_gap_in),
                               show_extensions=self.bar_extensions.isChecked(), ticks=ticks,
                               label_format=self.bar_format_combo.currentText().lower(),
                               **{('significant_digits' if self.bar_format_combo.currentText() in ('Auto', 'Engineering')
                                   else 'decimal_places'): self.bar_precision_spin.value()},
                               scientific_style=self.bar_scientific_style_combo.currentData(),
                               trim_trailing_zeros=self.bar_trim_zeros.isChecked())
            presentation = replace(self.presentation, width_in=dimension(self.width_spin, self.presentation.width_in),
                                   height_in=dimension(self.height_spin, self.presentation.height_in), aspect_locked=self.aspect_lock.isChecked(),
                                   frame_width_in=None if automatic else dimension(self.frame_width_spin, self.presentation.frame_width_in),
                                   frame_height_in=None if automatic else dimension(self.frame_height_spin, self.presentation.frame_height_in),
                                   numeric_pt=self.numeric_spin.value(), text_pt=self.text_spin.value(),
                                   font_family=self.font_edit.text(), heatmap_dpi=self.dpi_spin.value(),
                                   include_peak_labels=self.include_peaks.isChecked(), axis=axis, colorbar=colorbar)
            presentation = replace(presentation, title_position=(
                dimension(self.title_x_spin, self.presentation.title_position[0]),
                dimension(self.title_y_spin, self.presentation.title_position[1])))
        except (ValueError, TypeError) as exc:
            self.presentation_error = str(exc)
            self._update_dimensions_summary()
            self.presentationError.emit(str(exc))
            return
        self.presentation_error = None
        self.presentation = presentation
        self._update_number_sample()
        self._update_dimensions_summary()
        self.presentationChanged.emit(presentation)

    def set_presentation(self, presentation):
        self._loading = True
        try:
            self.presentation = presentation
            self.presentation_error = None
            for control, value in ((self.width_spin, presentation.width_in), (self.height_spin, presentation.height_in),
                                   (self.numeric_spin, presentation.numeric_pt), (self.text_spin, presentation.text_pt),
                                   (self.dpi_spin, presentation.heatmap_dpi), (self.border_width_spin, presentation.axis.border_width),
                                   (self.title_x_spin, presentation.title_position[0]),
                                   (self.title_y_spin, presentation.title_position[1]),
                                   (self.x_step_spin, presentation.axis.x_major_step or 1),
                                   (self.y_step_spin, presentation.axis.y_major_step or 1),
                                   (self.bar_width_spin, presentation.colorbar.width_in),
                                   (self.bar_height_spin, presentation.colorbar.height_in),
                                   (self.bar_gap_spin, presentation.colorbar.gap_in),
                                   (self.bar_frame_gap_spin, presentation.colorbar.frame_gap_in)):
                control.setValue(value)
            for prefix in ('x', 'y'):
                step = getattr(presentation.axis, f'{prefix}_major_step')
                count = getattr(presentation.axis, f'{prefix}_major_count')
                mode = 'By increment' if step is not None else 'By count' if count is not None else 'Auto'
                getattr(self, f'{prefix}_mode_combo').setCurrentText(mode)
                getattr(self, f'{prefix}_step_spin').setEnabled(mode == 'By increment')
                getattr(self, f'{prefix}_count_spin').setEnabled(mode == 'By count')
                getattr(self, f'{prefix}_count_spin').setValue(count or 7)
                getattr(self, f'{prefix}_minor_count_spin').setValue(getattr(presentation.axis, f'{prefix}_minor_count'))
            self.aspect_lock.setChecked(presentation.aspect_locked)
            automatic = presentation.frame_width_in is None
            self.frame_auto.setChecked(automatic)
            self.frame_width_spin.setEnabled(not automatic)
            self.frame_height_spin.setEnabled(not automatic)
            self.frame_width_spin.setValue(presentation.frame_width_in or PlotPresentation().frame_width_in)
            self.frame_height_spin.setValue(presentation.frame_height_in or PlotPresentation().frame_height_in)
            self._dimension_display_values = {control: control.value() for control in
                                              (self.width_spin, self.height_spin,
                                               self.frame_width_spin, self.frame_height_spin,
                                               self.bar_width_spin, self.bar_height_spin,
                                               self.bar_gap_spin, self.bar_frame_gap_spin,
                                               self.title_x_spin, self.title_y_spin)}
            self.include_peaks.setChecked(presentation.include_peak_labels)
            self.font_edit.setText(presentation.font_family)
            for key, control in self.axis_controls.items():
                control.setChecked(getattr(presentation.axis, key))
            self.colorbar_visible.setChecked(presentation.colorbar.visible)
            self.colorbar_label.setChecked(presentation.colorbar.show_label)
            self.colorbar_unit.setChecked(presentation.colorbar.show_unit)
            self.bar_alignment_combo.setCurrentText(presentation.colorbar.alignment)
            self.signal_position_combo.setCurrentText(presentation.colorbar.signal_position)
            self.bar_extensions.setChecked(presentation.colorbar.show_extensions)
            self.bar_format_combo.setCurrentText(presentation.colorbar.label_format.capitalize())
            self.bar_scientific_style_combo.setCurrentIndex(
                self.bar_scientific_style_combo.findData(presentation.colorbar.scientific_style))
            self.bar_trim_zeros.setChecked(presentation.colorbar.trim_trailing_zeros)
            self._show_number_precision()
            self._show_ticks()
            self._refresh_annotations()
            if self.preview._title_selected:
                self.preview.selected_id = None
        finally:
            self._loading = False
        self._update_dimensions_summary()
        self._update_number_sample()
        self._resize_preview()

    def _update_dimensions_summary(self, *, current_page=False):
        page = self.rendered_page if current_page else None
        if page is not None and page.owner_token == self.expected_owner:
            width, height = page.figure_size_in
            dpi = page.heatmap_dpi
            frame = f'{page.plot_size_in[0]:.3f} × {page.plot_size_in[1]:.3f} in (actual)'
        else:
            width, height = self.presentation.width_in, self.presentation.height_in
            dpi = self.presentation.heatmap_dpi
            if self.presentation_error is not None:
                width, height = self.width_spin.value(), self.height_spin.value()
                dpi = self.dpi_spin.value()
            frame = ('Auto (preview pending)' if self.frame_auto.isChecked() else
                     f'{self.frame_width_spin.value():.3f} × {self.frame_height_spin.value():.3f} in '
                     '(requested; preview pending)')
        # Keep two decimal places for ordinary page sizes and the editor's third
        # decimal where requested. PNG uses the writer's exact rounding rule.
        sizes = [f'{value:.3f}' for value in (width, height)]
        sizes = [value[:-1] if value.endswith('0') else value for value in sizes]
        pixels = tuple(round(value * dpi) for value in (width, height))
        self.dimensions_label.setText(f'Page including labels and colorbars: {sizes[0]} × {sizes[1]} in '
                                      f'({width*25.4:.1f} × {height*25.4:.1f} mm)\n'
                                      f'Data plot frame: {frame}\n'
                                      f'PNG: {pixels[0]} × {pixels[1]} px @ {dpi} DPI')

    def _show_ticks(self, *_args):
        loading = self._loading
        self._loading = True
        try:
            ticks = self.presentation.colorbar.ticks.get(self.bar_id_combo.currentText())
            self.custom_ticks.setChecked(ticks is not None)
            self.tick_values_edit.setText(', '.join(str(value) for value in ticks.values) if ticks else '')
            self.tick_labels_edit.setText(' | '.join(ticks.labels) if ticks and ticks.labels else '')
        finally:
            self._loading = loading
        self._update_number_sample()

    def _show_number_precision(self):
        mode = self.bar_format_combo.currentText()
        significant = mode in ('Auto', 'Engineering')
        self.bar_precision_label.setText('Significant digits:' if significant else
                                        'Mantissa decimal places:' if mode == 'Scientific' else 'Decimal places:')
        self.bar_precision_spin.setToolTip(
            ('Digits retained in each label. ' if significant else
             'Digits after the mantissa decimal point; 0 rounds 930 to 9e2. ' if mode == 'Scientific' else
             'Digits after the decimal point. ') +
            'Labels are rounded; tick values and color limits are unchanged.')
        with QSignalBlocker(self.bar_precision_spin):
            self.bar_precision_spin.setRange(1 if significant else 0, 12)
            self.bar_precision_spin.setValue(self.presentation.colorbar.significant_digits if significant
                                             else self.presentation.colorbar.decimal_places)
        self.bar_scientific_style_combo.setEnabled(mode == 'Scientific')

    def _change_number_format(self, *_args):
        if self._loading:
            return
        self._show_number_precision()
        self._emit_presentation()

    def set_colorbar_norms(self, norms):
        """Receive current live norms even while the formal preview is pending."""
        self._colorbar_norms = dict(norms)
        self._update_number_sample()

    def _update_number_sample(self):
        bar_id = self.bar_id_combo.currentText()
        norm = self._colorbar_norms.get(bar_id)
        self.bar_format_warning.setVisible(False)
        self.bar_format_warning.setText('')
        if norm is None:
            self.bar_sample_label.setText('Selected bar unavailable')
            return
        style = self.presentation.colorbar
        custom = style.ticks.get(bar_id)
        try:
            values, labels, _positions = _bar_ticks(norm, custom, style)
            fallback = False
        except ValueError:
            values, labels, _positions = _bar_ticks(norm, None, style)
            custom, fallback = None, True
        self.bar_sample_label.setText(' | '.join(labels))
        warnings = []
        if fallback:
            warnings.append('Stored custom ticks are outside the current scale; sample shows automatic ticks.')
        if custom is not None and custom.labels is not None:
            warnings.append('Manual labels override the number format.')
        else:
            if len(set(labels)) < len(labels):
                warnings.append('This precision produces duplicate labels.')
            if colorbar_labels_rounded(values, style):
                warnings.append('Rounded display; tick values and color limits are unchanged.')
        self.bar_format_warning.setText(' '.join(warnings))
        self.bar_format_warning.setVisible(bool(warnings))

    def invalidate(self, owner_token, message='Page changed; preparing preview…'):
        self.expected_owner = owner_token
        self.rendered_page = None
        # Display the last successful paper while new bytes are prepared. It
        # is visual history only: export and geometry interaction stay retired.
        self.preview._drag_annotation = None
        self.preview._drag_title = None
        self.preview.setEnabled(False)
        self.export_svg_btn.setEnabled(False)
        self.export_png_btn.setEnabled(False)
        self.copy_graph_btn.setEnabled(False)
        self.add_text_btn.setEnabled(False)
        self.add_arrow_btn.setEnabled(False)
        self.status_label.setText(message)
        self._update_dimensions_summary()
        self._resize_preview()

    def commit_rendered_page(self, page):
        if page.owner_token != self.expected_owner:
            return False
        self.preview.set_page(page)
        self.preview.setEnabled(True)
        self.rendered_page = page
        self._set_annotation_steps(False)
        self._set_annotation_steps(True)
        if self.presentation.frame_width_in is None:
            for control, value in zip((self.frame_width_spin, self.frame_height_spin), page.plot_size_in):
                with QSignalBlocker(control):
                    # Rounding upward can make an unchanged Auto → custom
                    # toggle exceed the measured paper space. Display the
                    # nearest fitting value at the editor's physical precision.
                    precision = 10 ** control.decimals()
                    control.setValue(math.floor(value * precision) / precision)
        self.export_svg_btn.setEnabled(True)
        self.export_png_btn.setEnabled(True)
        self.copy_graph_btn.setEnabled(True)
        self.add_text_btn.setEnabled(True)
        self.add_arrow_btn.setEnabled(True)
        self._update_dimensions_summary(current_page=True)
        self.status_label.setText(f'{page.nonpositive_count} nonpositive cells masked · '
                                 f'{page.under_count} below ({100*page.under_count/page.valid_count if page.valid_count else 0:.2f}%) / '
                                 f'{page.over_count} above ({100*page.over_count/page.valid_count if page.valid_count else 0:.2f}%) scale · '
                                 f'{page.missing_count} missing · {page.valid_count} visible valid cells')
        self._resize_preview()
        return True

    def set_zoom(self, zoom):
        if zoom not in ('Fit', '100%', '150%', '200%'):
            raise ValueError('Unknown preview zoom.')
        self._zoom = zoom
        with QSignalBlocker(self.zoom_combo):
            self.zoom_combo.setCurrentText(zoom)
        self._resize_preview()
        self.displayZoomChanged.emit(zoom)

    def _resize_preview(self):
        page = self.rendered_page or self.preview.page
        width, height = page.figure_size_in if page else (self.presentation.width_in, self.presentation.height_in)
        width, height = width * 96, height * 96
        if self._zoom == 'Fit':
            viewport = self.preview_scroll.viewport().size()
            # Hidden tabs initially have placeholder geometry. Keep the last
            # sensible multiplier until the actual Plot viewport is visible.
            scale = self.display_scale
            if self.preview_scroll.isVisible() and viewport.width() > 24 and viewport.height() > 24:
                scale = min((viewport.width()-12)/width, (viewport.height()-12)/height)
        else:
            scale = int(self._zoom.rstrip('%')) / 100
        self.preview.setFixedSize(max(1, round(width * scale)), max(1, round(height * scale)))
        if not math.isclose(self.display_scale, scale, rel_tol=1e-9):
            self.display_scale = scale
            self.displayScaleChanged.emit(scale)

    def eventFilter(self, watched, event):
        action = self._command_actions.get(watched)
        if action is not None and event.type() in (QEvent.EnabledChange, QEvent.ToolTipChange):
            action.setEnabled(watched.isEnabled())
            action.setToolTip(watched.toolTip())
        preview_scroll = getattr(self, 'preview_scroll', None)
        if preview_scroll is not None and watched is preview_scroll.viewport() and event.type() == QEvent.Resize:
            self._resize_preview()
        return super().eventFilter(watched, event)
