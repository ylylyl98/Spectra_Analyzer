"""Host-free formal-page state, geometry and input over accepted SVG bytes."""
from __future__ import annotations
from dataclasses import dataclass, replace
from PySide6.QtCore import QByteArray, QObject, QPointF, Property, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QFont, QFontMetricsF, QGuiApplication
from PySide6.QtSvg import QSvgRenderer
from .plot_annotations import coordinates_to_page, page_to_coordinates
from .plot_presentation import PlotPresentation
from .svg_export import render_svg_image


@dataclass
class _Gesture:
    generation: int
    press: QPointF
    offset: QPointF
    annotation: object = None
    endpoint: bool = False
    title: tuple | None = None
    moved: bool = False


class FormalPreviewController(QObject):
    """Reusable controller for a Quick paper scene; contains no renderer widget."""

    stateChanged = Signal()
    overlayChanged = Signal()
    geometryChanged = Signal()
    displayScaleChanged = Signal(float)
    annotationSelected = Signal(str)
    annotationMoved = Signal(object)
    annotationDeleteRequested = Signal()
    titleMoved = Signal(object)
    propertyEditRequested = Signal(str, str)
    graphCopyRequested = Signal()
    formatCommandRequested = Signal(str)

    def __init__(self, parent=None, *, display_policy='editable', authorization=None):
        if display_policy not in ('editable', 'readonly'):
            raise ValueError('Unknown formal preview display policy.')
        super().__init__(parent)
        self.page = None
        self.snapshot = None
        self.presentation = PlotPresentation()
        self.expected_owner = None
        self.svg_bytes = b''
        self._image_url = ''
        self._zoom = 'Fit'
        self._selected_id = None
        self._title_selected = False
        self._gesture = None
        self._generation = 0
        self._paper_size = (1., 1.)
        self._scale = 1.
        self._guard = authorization or (lambda: True)
        self._input_authorization = lambda: True
        self._accepted = False
        self._read_only = display_policy == 'readonly'
        self._relative_zoom = 1.
        self._pan = QPointF()
        self._view_gesture = None
        self._disposed = False
        self._scene_generation = 0
        self._scene_attached = False
        self._scene_visible = False
        self._scene_enabled = False
        self._viewport_size = (0., 0.)
        self._device_pixel_ratio = 1.
        self._publish_image = None
        self._retire_image = None
        self._scroll = (0., 0.)
        self._format_commands = {}
        self._format_guard = lambda: True

    def _can_view(self):
        return bool(self._accepted and self.page is not None
                    and self.page.owner_token == self.expected_owner
                    and self._scene_interactive())

    def _scene_interactive(self):
        return (not self._disposed and self._scene_attached
                and self._scene_visible and self._scene_enabled)

    def _editable_geometry(self):
        return bool(not self._read_only and self._accepted and self.page is not None
                    and self.snapshot is not None
                    and self.page.owner_token == self.snapshot.owner_token == self.expected_owner
                    and self._scene_interactive())

    def _can_edit(self, *, authorize=False):
        generation = self._generation
        return bool(self._editable_geometry() and self._guard()
                    and (not authorize or self._input_authorization())
                    and generation == self._generation and self._editable_geometry())

    canEdit = Property(bool, _can_edit, notify=stateChanged)
    readOnly = Property(bool, lambda self: self._read_only, constant=True)
    canView = Property(bool, _can_view, notify=stateChanged)
    relativeZoom = Property(float, lambda self: self._relative_zoom, notify=stateChanged)
    panX = Property(float, lambda self: self._pan.x(), notify=stateChanged)
    panY = Property(float, lambda self: self._pan.y(), notify=stateChanged)
    gestureGeneration = Property(int, lambda self: self._generation, notify=overlayChanged)
    viewportVisible = Property(bool, lambda self: self._scene_visible, notify=stateChanged)
    scrollX = Property(float, lambda self: self._scroll[0], notify=stateChanged)
    scrollY = Property(float, lambda self: self._scroll[1], notify=stateChanged)
    imageUrl = Property(str, lambda self: self._image_url, notify=stateChanged)
    pageWidth = Property(float, lambda self: self._physical_size()[0] * 96, notify=stateChanged)
    pageHeight = Property(float, lambda self: self._physical_size()[1] * 96, notify=stateChanged)
    zoom = Property(str, lambda self: self._zoom, notify=stateChanged)
    overlay = Property('QVariantMap', lambda self: self._overlay(), notify=overlayChanged)
    formatCommands = Property('QVariantMap', lambda self: dict(self._format_commands), notify=stateChanged)

    def set_format_commands(self, commands):
        commands = {key: bool(commands[key]['enabled'])
                    for key in ('copy_svg', 'copy_png', 'settings_copy', 'settings_paste')}
        if commands != self._format_commands:
            self._format_commands = commands
            self.stateChanged.emit()

    def set_format_guard(self, guard):
        self._format_guard = guard

    @Slot(int, result=int)
    def sceneFormatMenu(self, scene):
        if not self._current_scene(scene) or not self._format_commands or not self._can_edit(authorize=True):
            return -1
        self._retire_gesture()
        return self._generation

    @Slot(int, int, str, result=bool)
    def sceneFormatCommand(self, scene, generation, key):
        if (not self._current_scene(scene) or generation != self._generation
                or not self._format_commands.get(key, False) or not self._format_guard()
                or not self._can_edit()):
            return False
        self._retire_gesture()
        self.formatCommandRequested.emit(key)
        return True

    def set_mutation_guard(self, guard):
        self._retire_gesture()
        self._guard = guard
        self.stateChanged.emit()

    def set_input_authorization(self, authorize):
        """Authorize actual input only; canEdit and overlay remain pure projections."""
        self._retire_gesture()
        self._input_authorization = authorize

    def attach_scene(self, publish_image, retire_image):
        """Bind one actual scene's image sink; accepted bytes outlive this sink."""
        if self._disposed:
            raise RuntimeError('The formal preview controller has been disposed.')
        self.detach_scene(self._scene_generation)
        self._scene_generation += 1
        self._publish_image, self._retire_image = publish_image, retire_image
        self._image_url = publish_image(self.svg_bytes) if self.svg_bytes else ''
        self.stateChanged.emit()
        return self._scene_generation

    def update_scene(self, generation, *, attached, visible, enabled, viewport_size, device_pixel_ratio):
        if self._disposed or generation != self._scene_generation:
            return
        state = bool(attached), bool(visible), bool(enabled)
        previous = self._scene_attached, self._scene_visible, self._scene_enabled
        self._scene_attached, self._scene_visible, self._scene_enabled = state
        self._viewport_size = tuple(viewport_size)
        self._device_pixel_ratio = float(device_pixel_ratio)
        if state != previous:
            if not all(state):
                self._retire_gesture()
            self.stateChanged.emit()
            self.overlayChanged.emit()

    def detach_scene(self, generation):
        if generation != self._scene_generation:
            return
        retire, self._retire_image = self._retire_image, None
        self._publish_image = None
        self._image_url = ''
        self._scene_attached = self._scene_visible = self._scene_enabled = False
        self._scene_generation += 1
        self._retire_gesture()
        self.stateChanged.emit()
        if retire is not None:
            retire()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.detach_scene(self._scene_generation)
        self._accepted = False
        self.stateChanged.emit()

    selected_id = property(lambda self: self._selected_id,
                           lambda self, value: self.select_annotation(value))
    title_selected = property(lambda self: self._title_selected,
                              lambda self, value: self.select_title(value))

    def select_annotation(self, identifier):
        if not self._read_only and not self._disposed:
            self._selected_id = identifier
            self.overlayChanged.emit()

    def select_title(self, selected):
        if not self._read_only and not self._disposed:
            self._title_selected = bool(selected)
            self.overlayChanged.emit()

    def set_zoom(self, zoom):
        if zoom not in ('Fit', '100%', '150%', '200%'):
            raise ValueError('Unknown preview zoom.')
        if self._disposed:
            return
        if self._read_only:
            if zoom != 'Fit':
                raise ValueError('Read-only preview uses zoom relative to Fit.')
            self.viewFit(self._generation)
            return
        self._zoom = zoom
        self.stateChanged.emit()

    def paper_display_size(self):
        return self._paper_size

    def annotation_point(self, annotation, endpoint=False):
        return self._point(annotation, endpoint).toPoint()

    def title_point(self):
        return self._title_point().toPoint()

    def _current_scene(self, generation):
        return generation == self._scene_generation and self._scene_interactive()

    @Slot(int, float, float, float)
    def sceneDisplayGeometry(self, generation, width, height, scale):
        if not self._disposed and generation == self._scene_generation:
            self.setDisplayGeometry(width, height, scale)

    @Slot(int, float, float)
    def saveScroll(self, generation, x, y):
        if self._current_scene(generation):
            self._scroll = (max(0., x), max(0., y))

    @Slot(int, float, float, result=int)
    def scenePress(self, scene, x, y):
        return self._generation if self._current_scene(scene) and self.press(x, y) else -1

    @Slot(int, int, float, float)
    def sceneMove(self, scene, gesture, x, y):
        if self._current_scene(scene) and gesture == self._generation:
            self.move(x, y)

    @Slot(int, int, float, float)
    def sceneRelease(self, scene, gesture, x, y):
        if self._current_scene(scene) and gesture == self._generation:
            self.release(x, y)

    @Slot(int, float, float, result=bool)
    def sceneDoubleClick(self, scene, x, y):
        return bool(self._current_scene(scene) and self.doubleClick(x, y))

    @Slot(int, int, int, result=bool)
    def sceneKey(self, scene, key, modifiers):
        return bool(self._current_scene(scene) and self.key(key, modifiers))

    @Slot(int)
    def sceneCancel(self, scene):
        if scene == self._scene_generation:
            self.cancel_interaction()

    @Slot(int, float)
    def sceneWheelZoom(self, scene, delta):
        if self._current_scene(scene):
            self.wheelZoom(delta)

    @Slot(int, float, float, result=int)
    def sceneViewPress(self, scene, x, y):
        return self.viewPress(x, y) if self._current_scene(scene) else -1

    @Slot(int, int, float, float)
    def sceneViewMove(self, scene, gesture, x, y):
        if self._current_scene(scene):
            self.viewMove(gesture, x, y)

    @Slot(int, int, float, float)
    def sceneViewRelease(self, scene, gesture, x, y):
        if self._current_scene(scene):
            self.viewRelease(gesture, x, y)

    @Slot(int, int, result=bool)
    def sceneViewFit(self, scene, gesture):
        return bool(self._current_scene(scene) and self.viewFit(gesture))

    def _physical_size(self):
        return self.page.figure_size_in if self.page is not None else (
            self.presentation.width_in, self.presentation.height_in)

    def _retire_gesture(self):
        self._generation += 1
        self._gesture = None
        self._view_gesture = None
        self.overlayChanged.emit()

    @Slot(float)
    def wheelZoom(self, delta):
        if not self._read_only or not self._can_view():
            return
        self._retire_gesture()
        # Bound the exponent as well as the result for unusually large deltas.
        self._relative_zoom = min(12., max(.25, self._relative_zoom * 1.2 ** max(-100., min(100., delta / 120))))
        self.stateChanged.emit()

    @Slot(float, float, result=int)
    def viewPress(self, x, y):
        if not self._read_only or not self._can_view():
            return -1
        self._view_gesture = _Gesture(self._generation, QPointF(x, y), QPointF(self._pan))
        return self._generation

    @Slot(int, float, float)
    def viewMove(self, generation, x, y):
        gesture = self._view_gesture
        if (not self._read_only or not self._can_view() or gesture is None
                or generation != self._generation or gesture.generation != generation):
            return
        self._pan = gesture.offset + QPointF(x, y) - gesture.press
        self.stateChanged.emit()

    @Slot(int, float, float)
    def viewRelease(self, generation, x, y):
        self.viewMove(generation, x, y)
        if generation == self._generation:
            self._view_gesture = None

    @Slot(int, result=bool)
    def viewFit(self, generation):
        if not self._read_only or not self._can_view() or generation != self._generation:
            return False
        self._retire_gesture()
        self._relative_zoom, self._pan = 1., QPointF()
        self.stateChanged.emit()
        return True

    def set_geometry(self, snapshot, presentation):
        if self._disposed:
            return
        if snapshot is not self.snapshot or presentation != self.presentation:
            self._retire_gesture()
        self.snapshot, self.presentation = snapshot, presentation
        if self.expected_owner is None and snapshot is not None:
            self.expected_owner = snapshot.owner_token
        self.stateChanged.emit()
        self.overlayChanged.emit()

    def set_page(self, page):
        if self._disposed:
            return False
        if page is not None and self.expected_owner is not None and page.owner_token != self.expected_owner:
            return False
        if page is not None:
            if not QSvgRenderer(QByteArray(page.svg_bytes)).isValid():
                raise ValueError('The generated SVG could not be previewed.')
            # Windows' offscreen plugin requires export's font registration.
            # Native Qt already resolves these fonts: avoid a redundant full
            # raster before the image provider draws at the display resolution.
            if QGuiApplication.platformName() == 'offscreen':
                render_svg_image(page, dpi=72)
        self._retire_gesture()
        self.page = page
        self.svg_bytes = bytes(page.svg_bytes) if page is not None else b''
        self._image_url = self._publish_image(self.svg_bytes) if page is not None and self._publish_image else ''
        self._accepted = page is not None
        if page is not None and self.expected_owner is None:
            self.expected_owner = page.owner_token
        self.stateChanged.emit()
        return True

    def invalidate(self, owner_token):
        """Retain paper and scroll while retiring all editable geometry."""
        if self._disposed:
            return
        self.expected_owner = owner_token
        self._accepted = False
        self.cancel_interaction()
        self.stateChanged.emit()

    @Slot(float, float, float)
    def setDisplayGeometry(self, width, height, scale):
        if self._disposed:
            return
        size = max(1., width), max(1., height)
        changed = size != self._paper_size
        self._paper_size = size
        if changed:
            self._retire_gesture()
            self.geometryChanged.emit()
        if self._scale != scale:
            self._scale = scale
            self.displayScaleChanged.emit(scale)

    def _point(self, annotation, endpoint=False):
        position = annotation.target if endpoint else annotation.position
        space = annotation.target_space if endpoint else annotation.coordinate_space
        return QPointF(*coordinates_to_page(position, self._paper_size, self.page,
                                            self.snapshot, self.presentation, space))

    def _title_point(self, position=None):
        position = self.presentation.title_position if position is None else position
        return QPointF(*coordinates_to_page(position, self._paper_size, self.page,
                                            self.snapshot, self.presentation, 'axes-fraction'))

    def _page_rect(self, bounds):
        left, bottom, width, height = bounds
        w, h = self._paper_size
        return QRectF(left * w, (1 - bottom - height) * h, width * w, height * h)

    def _frame(self):
        return self._page_rect(self.page.axes_bounds)

    def _measured_rect(self, key):
        rectangles = self.page.object_bounds.get(key, ())
        rect = QRectF()
        for bounds in rectangles:
            rect = rect.united(self._page_rect(bounds))
        return rect

    def _label_rect(self, annotation):
        rect = self._measured_rect('annotation:' + annotation.id)
        original = next((a for a in self.presentation.annotations if a.id == annotation.id), annotation)
        if not rect.isEmpty():
            delta = self._point(annotation) - self._point(original)
            return rect.translated(delta)
        point = self._point(annotation)
        font = QFont(self.presentation.font_family)
        pt = self.presentation.numeric_pt if annotation.font_role == 'numeric' else self.presentation.text_pt
        font.setPixelSize(max(1, round(pt * self._paper_size[0] / (self.presentation.width_in * 72))))
        metrics = QFontMetricsF(font)
        lines = annotation.text.splitlines() or ['']
        return QRectF(point.x() - 3, point.y() - metrics.ascent() - 3,
                      max(10, *(metrics.horizontalAdvance(line) for line in lines)) + 6,
                      metrics.height() * len(lines) + 6)

    def _title_rect(self, position=None):
        rect = self._measured_rect('title')
        point = self._title_point(position)
        if not rect.isEmpty():
            return rect.translated(point - self._title_point()).intersected(self._frame())
        font = QFont(self.presentation.font_family)
        font.setPixelSize(max(1, round(self.presentation.text_pt * self._paper_size[0]
                                      / (self.presentation.width_in * 72))))
        metrics = QFontMetricsF(font)
        lines = self.snapshot.title.splitlines() or ['']
        return QRectF(point.x() - 3, point.y() - 3,
                      max(10, *(metrics.horizontalAdvance(line) for line in lines)) + 6,
                      metrics.height() * len(lines) + 6).intersected(self._frame())

    def _selected(self):
        return next((a for a in self.presentation.annotations if a.id == self._selected_id), None)

    def hit_test(self, point):
        if self.page is None or self.snapshot is None:
            return None
        for annotation in reversed(self.presentation.annotations):
            if not annotation.visible:
                continue
            if annotation.kind == 'arrow' and (self._point(annotation, True) - point).manhattanLength() <= 12:
                return annotation, True
            if (self._point(annotation) - point).manhattanLength() <= 12 or self._label_rect(annotation).contains(point):
                return annotation, False
            if annotation.kind == 'arrow':
                a, b = self._point(annotation), self._point(annotation, True)
                dx, dy = b.x() - a.x(), b.y() - a.y()
                length = dx * dx + dy * dy
                t = max(0, min(1, ((point.x() - a.x()) * dx + (point.y() - a.y()) * dy) / length)) if length else 0
                if (point - QPointF(a.x() + t * dx, a.y() + t * dy)).manhattanLength() <= 8:
                    return annotation, False
        return None

    @Slot(float, float, result=bool)
    def press(self, x, y):
        if not self._can_edit(authorize=True):
            return False
        point = QPointF(x, y)
        generation = self._generation
        if self.snapshot.title and self._title_rect().contains(point):
            self._selected_id = None
            self.annotationSelected.emit('')
            if not self._can_edit(authorize=True) or self._generation != generation:
                return False
            self._title_selected = True
            self._gesture = _Gesture(generation, point, self._title_point() - point,
                                     title=self.presentation.title_position)
        else:
            found = self.hit_test(point)
            if found is None:
                return False
            annotation, endpoint = found
            self._title_selected = False
            self._selected_id = annotation.id
            self.annotationSelected.emit(annotation.id)
            if not self._can_edit(authorize=True) or self._generation != generation:
                return False
            self._gesture = _Gesture(generation, point, self._point(annotation, endpoint) - point,
                                     annotation=annotation, endpoint=endpoint)
        self.overlayChanged.emit()
        return True

    @Slot(float, float)
    def move(self, x, y):
        gesture = self._gesture
        if gesture is None:
            return
        if not self._can_edit(authorize=True) or gesture.generation != self._generation:
            self._retire_gesture()
            return
        point = QPointF(x, y)
        delta = point - gesture.press
        # Paper coordinates are displayed logical pixels, including preview zoom.
        # Flickable pixel alignment can shift a stationary press by a fraction
        # of a pixel. Once a real drag starts, retain every precise movement.
        if not gesture.moved and delta.x()**2 + delta.y()**2 < 1.:
            return
        gesture.moved = True
        point += gesture.offset
        space = ('axes-fraction' if gesture.title is not None else
                 gesture.annotation.target_space if gesture.endpoint else gesture.annotation.coordinate_space)
        position = page_to_coordinates((point.x(), point.y()), self._paper_size, self.page,
                                       self.snapshot, self.presentation, space)
        if gesture.title is not None:
            gesture.title = tuple(max(0., min(1., value)) for value in position)
        else:
            gesture.annotation = replace(gesture.annotation, **{
                'target' if gesture.endpoint else 'position': position})
        self.overlayChanged.emit()

    @Slot(float, float)
    def release(self, x, y):
        self.move(x, y)
        gesture, self._gesture = self._gesture, None
        self.overlayChanged.emit()
        if (gesture is None or not gesture.moved or not self._can_edit(authorize=True)
                or gesture.generation != self._generation):
            return
        if gesture.title is not None:
            if gesture.title != self.presentation.title_position:
                self.titleMoved.emit(gesture.title)
        else:
            original = self._selected()
            if original is not None and gesture.annotation != original:
                self.annotationMoved.emit(gesture.annotation)

    @Slot(float, float, result=bool)
    def doubleClick(self, x, y):
        if not self._can_edit(authorize=True):
            return False
        point = QPointF(x, y)
        self._retire_gesture()
        # Renderer-measured targets retain their order and clipping. An arrow's
        # broad label rectangle must never replace its narrow line/head fallback.
        for key, rectangles in self.page.object_bounds.items():
            if any(self._page_rect(bounds).contains(point) for bounds in rectangles):
                kind, _, identifier = key.partition(':')
                self.propertyEditRequested.emit(kind, identifier)
                return True
        found = self.hit_test(point) if self._frame().contains(point) else None
        if found is not None and found[0].kind == 'arrow':
            self.propertyEditRequested.emit('annotation', found[0].id)
            return True
        return False

    @Slot(int, int, result=bool)
    def key(self, key, modifiers):
        if self._disposed:
            return False
        if key == Qt.Key_Escape:
            self.cancel_interaction()
            return True
        if not self._can_edit(authorize=True):
            return False
        if key == Qt.Key_C and modifiers == Qt.ControlModifier.value:
            self.graphCopyRequested.emit()
            return True
        annotation = self._selected()
        if annotation is not None and key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.annotationDeleteRequested.emit()
            return True
        shifts = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if annotation is not None and annotation.visible and key in shifts:
            dx, dy = shifts[key]
            step = 10 if modifiers & Qt.ShiftModifier.value else 1
            point = self._point(annotation) + QPointF(dx * step, dy * step)
            position = page_to_coordinates((point.x(), point.y()), self._paper_size, self.page,
                                           self.snapshot, self.presentation, annotation.coordinate_space)
            self.annotationMoved.emit(replace(annotation, position=position))
            return True
        return False

    @Slot()
    def cancel_interaction(self):
        self._retire_gesture()
        self._selected_id = None
        self._title_selected = False
        self.overlayChanged.emit()

    def _overlay(self):
        if not self._can_edit():
            return {}
        gesture = self._gesture
        if self._title_selected and self.snapshot.title:
            rect = self._title_rect(gesture.title if gesture is not None else None)
            return dict(visible=True, x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height(), arrow=False, anchor=False)
        annotation = gesture.annotation if gesture is not None else self._selected()
        if annotation is None or not annotation.visible:
            return {}
        rect, point = self._label_rect(annotation), self._point(annotation)
        target = self._point(annotation, True) if annotation.kind == 'arrow' else point
        return dict(visible=True, x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height(),
                    anchor=True, pointX=point.x(), pointY=point.y(), arrow=annotation.kind == 'arrow',
                    targetX=target.x(), targetY=target.y())


FormalPreviewBridge = FormalPreviewController
