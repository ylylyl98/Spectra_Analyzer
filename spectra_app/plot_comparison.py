"""Read-only comparison windows over detached final formal-page bytes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QRectF, QSignalBlocker, Qt
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .svg_export import RenderedPage, render_svg_image, write_svg, write_svg_png
from .dataset_state import graph_export_stem


class _ReadOnlySvgPreview(QWidget):
    """Paint a fixed page without the formal editor's annotation interactions."""

    def __init__(self, page: RenderedPage, parent=None):
        super().__init__(parent)
        self._svg_bytes = page.svg_bytes
        self._renderer = QSvgRenderer(self)
        # Use the shared helper's offscreen font registration and SVG validation.
        render_svg_image(page, dpi=72)
        if not self._renderer.load(QByteArray(self._svg_bytes)):
            raise ValueError('The captured SVG page could not be previewed.')
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName('Read-only comparison snapshot canvas')
        self.setToolTip('Captured formal page. Use display zoom to inspect this snapshot.')

    @property
    def svg_bytes(self) -> bytes:
        return self._svg_bytes

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        self._renderer.render(painter, QRectF(self.rect()))


class PlotComparisonWindow(QWidget):
    """A nonmodal snapshot with independent display zoom and captured exports.

    ``metadata`` is the ordinary JSON-serializable source/settings dictionary,
    saved intact in the PNG's ``SpectraAnalyzer`` text entry. No source session,
    scientific arrays, or live rendering callbacks are retained by this window.
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
        # RenderedPage is frozen, but its maps (and defensively borrowed byte
        # buffers/dimension sequences) must not stay linked to the caller.
        captured = deepcopy(page)
        captured = replace(captured, svg_bytes=bytes(captured.svg_bytes),
                           figure_size_in=tuple(captured.figure_size_in),
                           owner_token=tuple(captured.owner_token))
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.NonModal)
        self._page = captured
        self._source = source_label
        self._metadata_json = metadata_json
        self._png_dpi = png_dpi
        self._output_folder = Path(output_folder) if output_folder is not None else None
        self._zoom = 'Fit'
        self.setWindowTitle('Plot comparison — ' + Path(source_label).name)
        self.resize(900, 650)

        layout = QVBoxLayout(self)
        self.source_label = QLabel()
        self.source_label.setTextFormat(Qt.PlainText)
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.source_label.setAccessibleName('Captured source, title and version')
        _, activation, science, view, presentation = self._page.owner_token[:5]
        title = json.loads(metadata_json).get('plot_title', '')
        self.source_label.setText(
            f'Snapshot: {source_label}\n'
            f'Version: activation {activation} · data {science} · view {view} · format {presentation}'
            + (f'\nTitle: {title}' if title else ''))
        layout.addWidget(self.source_label)

        actions = QHBoxLayout()
        actions.addWidget(QLabel('Display:'))
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(['Fit', '100%', '150%', '200%'])
        self.zoom_combo.setAccessibleName('Comparison display zoom')
        self.zoom_combo.currentTextChanged.connect(self.set_zoom)
        actions.addWidget(self.zoom_combo)
        actions.addStretch()
        self.export_svg_btn = QPushButton('Export SVG')
        self.export_png_btn = QPushButton('Export PNG')
        self.export_svg_btn.clicked.connect(lambda checked=False: self._choose_export('svg'))
        self.export_png_btn.clicked.connect(lambda checked=False: self._choose_export('png'))
        actions.addWidget(self.export_svg_btn)
        actions.addWidget(self.export_png_btn)
        layout.addLayout(actions)

        width, height = self._page.figure_size_in
        self.dimensions_label = QLabel(
            f'Captured page: {width:g} × {height:g} in · '
            f'PNG: {round(width * png_dpi)} × {round(height * png_dpi)} px @ {png_dpi} DPI')
        self.dimensions_label.setWordWrap(True)
        self.dimensions_label.setAccessibleName('Comparison export dimensions')
        layout.addWidget(self.dimensions_label)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setAlignment(Qt.AlignCenter)
        self.preview = _ReadOnlySvgPreview(self._page)
        self.preview_scroll.setWidget(self.preview)
        layout.addWidget(self.preview_scroll, 1)
        self.status_label = QLabel('Read-only snapshot')
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.preview_scroll.viewport().installEventFilter(self)
        self._resize_preview()

    def set_zoom(self, zoom: str):
        """Change only this window's display size, never its physical page."""
        if zoom not in ('Fit', '100%', '150%', '200%'):
            raise ValueError('Unknown comparison preview zoom.')
        self._zoom = zoom
        with QSignalBlocker(self.zoom_combo):
            self.zoom_combo.setCurrentText(zoom)
        self._resize_preview()

    def _resize_preview(self):
        width, height = (value * 96 for value in self._page.figure_size_in)
        if self._zoom == 'Fit':
            viewport = self.preview_scroll.viewport().size()
            scale = min(max(1, viewport.width() - 12) / width,
                        max(1, viewport.height() - 12) / height)
        else:
            scale = int(self._zoom.rstrip('%')) / 100
        self.preview.setFixedSize(max(1, round(width * scale)), max(1, round(height * scale)))

    def eventFilter(self, watched, event):
        if watched is self.preview_scroll.viewport() and event.type() == QEvent.Resize:
            self._resize_preview()
        return super().eventFilter(watched, event)

    def export_svg(self, path: str | Path) -> Path:
        """Write the exact captured SVG bytes, preserving existing outputs."""
        return write_svg(self._page, path)

    def export_png(self, path: str | Path) -> Path:
        """Rasterize this capture at its creation DPI with detached provenance."""
        return write_svg_png(self._page, path, dpi=self._png_dpi, metadata={
            'SpectraAnalyzer': self._metadata_json,
            'Source': self._source,
            'PlotOwner': json.dumps(self._page.owner_token, ensure_ascii=False),
            'PageDigest': self._page.digest,
        })

    def _choose_export(self, extension: str):
        metadata = json.loads(self._metadata_json)
        source = metadata.get('source_path')
        source = Path(source) if isinstance(source, str) and source else Path(self._source)
        stem = (graph_export_stem(source.stem, metadata['graph_id'], metadata['graph_name'])
                if 'graph_id' in metadata and 'graph_name' in metadata else source.stem)
        default = (self._output_folder or source.parent) / (stem + '_comparison.' + extension)
        path, _ = QFileDialog.getSaveFileName(
            self, f'Export comparison {extension.upper()}', str(default),
            f'{extension.upper()} files (*.{extension})')
        if not path:
            return
        try:
            saved = self.export_svg(path) if extension == 'svg' else self.export_png(path)
        except (OSError, ValueError) as exc:
            self.status_label.setText(f'Failed to export {extension.upper()}: {exc}')
        else:
            self.status_label.setText(f'Exported {extension.upper()}: {saved}')


__all__ = ['PlotComparisonWindow']
