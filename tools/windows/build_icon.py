"""Regenerate the checked-in multi-resolution Windows icon from its SVG."""
from io import BytesIO
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main() -> None:
    resources = Path(__file__).resolve().parents[2] / 'spectra_app/resources'
    renderer = QSvgRenderer(str(resources / 'spectra_analyzer.svg'))
    image = QImage(1024, 1024, QImage.Format_RGBA8888)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, 1024, 1024))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    bitmap = Image.open(BytesIO(bytes(data)))
    bitmap.save(resources / 'spectra_analyzer.ico', format='ICO', bitmap_format='bmp',
                sizes=[(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)])


if __name__ == '__main__':
    main()
