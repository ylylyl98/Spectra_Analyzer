"""Small native plot command graphics, rendered by Qt from inline SVG."""
from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QToolButton

PLOT_STYLES = (('line', 'Line'), ('scatter', 'Scatter'), ('line_symbol', 'Line + Symbol'))


def plot_icon(kind):
    line = '<path d="M3 19L9 11L15 15L22 4" fill="none" stroke="#2978ae" stroke-width="2"/>'
    dots = ''.join(f'<circle cx="{x}" cy="{y}" r="2.5" fill="#2978ae"/>'
                   for x, y in ((3, 19), (9, 11), (15, 15), (22, 4)))
    if kind == 'heatmap':
        content = ''.join(f'<rect x="{2+x*7}" y="{2+y*7}" width="6" height="6" fill="{color}"/>'
                          for y, row in enumerate((('#244f88', '#388dac', '#76c2b1'),
                                                   ('#388dac', '#b3d9a0', '#ebcf68'),
                                                   ('#76c2b1', '#ebcf68', '#db7047')))
                          for x, color in enumerate(row))
    elif kind == 'add':
        content = '<path d="M12 3V21M3 12H21" stroke="#2978ae" stroke-width="2"/>'
    else:
        content = (line if kind != 'scatter' else '') + (dots if kind != 'line' else '')
    renderer = QSvgRenderer(QByteArray(f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">{content}</svg>'.encode()))
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def plot_button(kind, text, parent=None):
    button = QToolButton(parent)
    button.setText(text)
    button.setIcon(plot_icon(kind))
    button.setIconSize(QSize(20, 20))
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    button.setAccessibleName(text)
    return button
