"""Read-only Qt Quick comparison windows over detached formal-page bytes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from PySide6.QtCore import (QByteArray, QEvent, QObject, Property, QRectF, QSize,
                            Qt, QUrl, Signal, Slot)
from PySide6.QtGui import QFont, QGuiApplication, QImage, QPainter, QWindow
from PySide6.QtQuick import QQuickImageProvider, QQuickView
from PySide6.QtSvg import QSvgRenderer

from .file_picker import FilePicker
from .svg_export import RenderedPage, render_svg_image, write_svg, write_svg_png
from .dataset_state import graph_export_stem


class _SnapshotImageProvider(QQuickImageProvider):
    """Render the captured vectors at the actual requested device-pixel size."""

    def __init__(self, svg_bytes: bytes):
        super().__init__(QQuickImageProvider.Image)
        self._svg_bytes = svg_bytes

    def requestImage(self, image_id, size, requested_size):
        renderer = QSvgRenderer(QByteArray(self._svg_bytes))
        target = requested_size if requested_size.isValid() else renderer.defaultSize()
        target = QSize(max(1, target.width()), max(1, target.height()))
        image = QImage(target, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.white)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, target.width(), target.height()))
        painter.end()
        if size is not None:
            size.setWidth(target.width())
            size.setHeight(target.height())
        return image


class ComparisonSnapshotBridge(QObject):
    """Detached snapshot data and the commands consumed by the QML window."""

    zoomChanged = Signal()
    statusChanged = Signal()
    applicationFontChanged = Signal()
    exportRequested = Signal(str)
    ZOOM_LEVELS = ('Fit', '100%', '150%', '200%')

    def __init__(self, page: RenderedPage, source_label: str, metadata_json: str,
                 png_dpi: int, parent=None):
        super().__init__(parent)
        QGuiApplication.instance().fontChanged.connect(self._application_font_changed)
        self._page = page
        self._zoom = 'Fit'
        self._status = 'Read-only snapshot'
        _, activation, science, view, presentation = page.owner_token[:5]
        title = json.loads(metadata_json).get('plot_title', '')
        self._source_text = (
            f'Snapshot: {source_label}\n'
            f'Version: activation {activation} · data {science} · view {view} · format {presentation}'
            + (f'\nTitle: {title}' if title else ''))
        width, height = page.figure_size_in
        self._dimensions_text = (
            f'Captured page: {width:g} × {height:g} in · '
            f'PNG: {round(width * png_dpi)} × {round(height * png_dpi)} px @ {png_dpi} DPI')

    sourceText = Property(str, lambda self: self._source_text, constant=True)
    dimensionsText = Property(str, lambda self: self._dimensions_text, constant=True)
    pageWidth = Property(float, lambda self: self._page.figure_size_in[0] * 96, constant=True)
    pageHeight = Property(float, lambda self: self._page.figure_size_in[1] * 96, constant=True)
    zoomLevels = Property('QStringList', lambda self: list(self.ZOOM_LEVELS), constant=True)
    zoom = Property(str, lambda self: self._zoom, notify=zoomChanged)
    statusText = Property(str, lambda self: self._status, notify=statusChanged)
    applicationFont = Property(QFont, lambda self: QGuiApplication.font(), notify=applicationFontChanged)

    @Slot(QFont)
    def _application_font_changed(self, font):
        self.applicationFontChanged.emit()

    @Slot(str)
    def setZoom(self, zoom: str):
        if zoom not in self.ZOOM_LEVELS:
            raise ValueError('Unknown comparison preview zoom.')
        if self._zoom != zoom:
            self._zoom = zoom
            self.zoomChanged.emit()

    @Slot(str)
    def chooseExport(self, extension: str):
        if extension not in ('svg', 'png'):
            raise ValueError('Unknown comparison export format.')
        self.exportRequested.emit(extension)

    def _set_status(self, status: str):
        if self._status != status:
            self._status = status
            self.statusChanged.emit()


class PlotComparisonWindow(QQuickView):
    """A real nonmodal QML window with captured exports and independent zoom.

    The SVG, dimensions, maps and JSON provenance are detached at construction.
    The window retains no source session, scientific array or render callback.
    """

    def __init__(self, page: RenderedPage, *, source_label: str, metadata: dict,
                 png_dpi: int = 300, output_folder: str | Path | None = None, parent=None):
        if not isinstance(page, RenderedPage):
            raise ValueError('A rendered page is required for comparison.')
        if not isinstance(source_label, str) or not source_label.strip():
            raise ValueError('A nonempty source label is required for comparison.')
        if isinstance(png_dpi, bool) or not isinstance(png_dpi, int) or png_dpi <= 0:
            raise ValueError('PNG DPI must be a positive integer.')
        if not isinstance(metadata, dict):
            raise ValueError('Snapshot metadata must be a JSON-serializable dictionary.')
        try:
            metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError('Snapshot metadata must be a JSON-serializable dictionary.') from exc
        captured = deepcopy(page)
        captured = replace(captured, svg_bytes=bytes(captured.svg_bytes),
                           figure_size_in=tuple(captured.figure_size_in),
                           owner_token=tuple(captured.owner_token))
        if QGuiApplication.instance() is None:
            raise ValueError('SVG preview/PNG export requires a running Qt application.')
        if QGuiApplication.platformName() == 'offscreen':
            # Windows' offscreen plugin needs export's font registration.
            render_svg_image(captured, dpi=72)
        else:
            # Preserve export's validation without painting the page twice.
            # Qt will rasterize the SVG at its display resolution below.
            pixels = tuple(round(value * 72) for value in captured.figure_size_in)
            if min(pixels) < 1:
                raise ValueError('PNG dimensions must be at least one pixel.')
            if not QSvgRenderer(QByteArray(captured.svg_bytes)).isValid():
                raise ValueError('QtSvg could not read the rendered SVG page.')
            if QImage(*pixels, QImage.Format_ARGB32).isNull():
                raise ValueError('Could not allocate the PNG/preview image.')
        super().__init__()
        if parent is not None:
            # QObject ownership does not turn this top-level window into a child
            # surface. QWidget and QWindow owners both retain native transience.
            QObject.setParent(self, parent)
            owner = parent.window() if hasattr(parent, 'window') else parent
            if not isinstance(owner, QWindow):
                owner.winId()
                owner = owner.windowHandle()
            if owner is not None:
                self.setTransientParent(owner)
        self._page = captured
        self._source = source_label
        self._metadata_json = metadata_json
        self._png_dpi = png_dpi
        self._output_folder = Path(output_folder) if output_folder is not None else None
        self.bridge = ComparisonSnapshotBridge(captured, source_label, metadata_json, png_dpi, self)
        self.bridge.exportRequested.connect(self._choose_export)
        self.setTitle('Plot comparison — ' + Path(source_label).name)
        self.setModality(Qt.NonModal)
        self.setResizeMode(QQuickView.SizeRootObjectToView)
        self.setMinimumSize(QSize(360, 420))
        self.engine().addImageProvider('comparison-snapshot', _SnapshotImageProvider(captured.svg_bytes))
        self.setInitialProperties({'bridge': self.bridge})
        self.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('qml') / 'ComparisonSnapshot.qml')))
        if self.status() == QQuickView.Error:
            errors = '\n'.join(error.toString() for error in self.errors())
            self.deleteLater()
            raise RuntimeError('Cannot load comparison snapshot: ' + errors)
        self.resize(900, 650)

    @property
    def svg_bytes(self) -> bytes:
        """The immutable bytes shown by this capture and written by SVG export."""
        return self._page.svg_bytes

    def set_zoom(self, zoom: str):
        """Change display scale without changing physical page/export geometry."""
        self.bridge.setZoom(zoom)

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Close and event.isAccepted():
            # QWindow has no WA_DeleteOnClose; preserve the snapshot lifetime
            # contract and MainWindow's destroyed-driven list cleanup explicitly.
            self.deleteLater()
        return result

    def export_svg(self, path: str | Path) -> Path:
        """Write the exact captured SVG bytes, preserving existing outputs."""
        return write_svg(self._page, path)

    def export_png(self, path: str | Path) -> Path:
        """Rasterize at creation DPI with the detached capture provenance."""
        return write_svg_png(self._page, path, dpi=self._png_dpi, metadata={
            'SpectraAnalyzer': self._metadata_json,
            'Source': self._source,
            'PlotOwner': json.dumps(self._page.owner_token, ensure_ascii=False),
            'PageDigest': self._page.digest,
        })

    def _default_export_path(self, extension: str) -> Path:
        metadata = json.loads(self._metadata_json)
        source = metadata.get('source_path')
        source = Path(source) if isinstance(source, str) and source else Path(self._source)
        stem = (graph_export_stem(source.stem, metadata['graph_id'], metadata['graph_name'])
                if 'graph_id' in metadata and 'graph_name' in metadata else source.stem)
        return (self._output_folder or source.parent) / (stem + '_comparison.' + extension)

    def _select_export_path(self, extension: str, default: Path) -> str:
        """Select an export with this actual Quick window as modal owner."""
        path, _ = FilePicker.getSaveFileName(
            self, f'Export comparison {extension.upper()}', str(default),
            f'{extension.upper()} files (*.{extension})', default_suffix=extension)
        return path

    @Slot(str)
    def _choose_export(self, extension: str):
        path = self._select_export_path(extension, self._default_export_path(extension))
        if not path:
            return
        try:
            saved = self.export_svg(path) if extension == 'svg' else self.export_png(path)
        except (OSError, ValueError) as exc:
            self.bridge._set_status(f'Failed to export {extension.upper()}: {exc}')
        else:
            self.bridge._set_status(f'Exported {extension.upper()}: {saved}')


__all__ = ['PlotComparisonWindow']
