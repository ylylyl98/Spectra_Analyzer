"""Matplotlib symbols, including a bounded, vector-rendered glossy ball."""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.legend_handler import HandlerLine2D
from matplotlib.lines import Line2D
from matplotlib.markers import MarkerStyle
from matplotlib.transforms import Affine2D


SYMBOL_SHAPES = (('ball', 'Ball'), ('o', 'Circle'), ('s', 'Square'),
                 ('^', 'Triangle up'), ('v', 'Triangle down'), ('<', 'Triangle left'),
                 ('>', 'Triangle right'), ('D', 'Diamond'), ('p', 'Pentagon'),
                 ('h', 'Hexagon'), ('*', 'Star'), ('+', 'Plus'), ('x', 'X'))
SYMBOL_FILLS = (('full', 'Solid'), ('none', 'Hollow'), ('left', 'Half left'),
                ('right', 'Half right'), ('top', 'Half top'), ('bottom', 'Half bottom'))
_BALL_LAYERS = tuple((MarkerStyle('o').transformed(Affine2D().scale(scale).translate(-.12, .12)), opacity)
                     for scale, opacity in ((.70, .16), (.44, .28), (.19, .68)))


class SymbolLine2D(Line2D):
    """One logical artist; ball highlights reuse native marker drawing in O(1) layers.

    Highlights follow the same sampled data, NaN gaps and markevery as the base.
    Temporary style changes are restored even when a renderer raises. Hollow and
    half-filled balls use the native circle fill, without an unfilled-side glint.
    """
    ball = False

    def draw(self, renderer):
        super().draw(renderer)
        if (not self.ball or not self.get_visible() or self.get_markersize() <= 0
                or self.get_marker() in ('None', 'none', '', ' ') or self.get_fillstyle() != 'full'):
            return
        original = dict(marker=self.get_marker(), fillstyle=self.get_fillstyle(),
                        linestyle=self.get_linestyle(), markerfacecolor=self.get_markerfacecolor(),
                        markerfacecoloralt=self.get_markerfacecoloralt(),
                        markeredgecolor=self.get_markeredgecolor(), markeredgewidth=self.get_markeredgewidth(),
                        alpha=self.get_alpha(), gid=self.get_gid())
        try:
            self.set(linestyle='None', markerfacecolor='white', markeredgecolor='none', markeredgewidth=0, gid=None)
            alpha = 1. if original['alpha'] is None else original['alpha']
            for marker, opacity in _BALL_LAYERS:
                self.set(marker=marker, alpha=alpha * opacity)
                super().draw(renderer)
        finally:
            self.set(**original)


class SymbolLegendHandler(HandlerLine2D):
    def create_artists(self, legend, orig_handle, *args):
        ordinary = super().create_artists(legend, orig_handle, *args)[0]
        symbol = SymbolLine2D(*ordinary.get_data())
        symbol.update_from(ordinary)
        symbol.set_markevery(ordinary.get_markevery())
        symbol.ball = orig_handle.ball
        return [symbol]


def apply_curve_style(line, curve):
    line.ball = curve.marker_shape == 'ball'
    line.set(color=curve.color, linewidth=curve.width, label=curve.label,
             linestyle='None' if curve.style == 'scatter' else '-',
             marker='None' if curve.style == 'line' else 'o' if line.ball else curve.marker_shape,
             markersize=curve.marker_size, fillstyle=curve.marker_fill,
             markerfacecolor=curve.color, markerfacecoloralt='none',
             markeredgecolor=curve.color if curve.marker_edge_color == 'auto' else curve.marker_edge_color,
             markeredgewidth=curve.marker_edge_width)


@lru_cache(maxsize=32)
def symbol_icon_png(shape, fill='full'):
    """Use the same native Matplotlib symbol in the Qt combo and plot."""
    figure = Figure(figsize=(.28, .28), dpi=100)
    figure.patch.set_alpha(0)
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_axes((0, 0, 1, 1), xlim=(0, 1), ylim=(0, 1))
    axes.set_axis_off()
    line = SymbolLine2D([.5], [.5], marker='o' if shape == 'ball' else shape,
                        markersize=13, linestyle='None', color='#2674b8', markeredgewidth=1,
                        fillstyle=fill, markerfacecoloralt='none')
    line.ball = shape == 'ball'
    axes.add_line(line)
    output = BytesIO()
    canvas.print_png(output)
    return output.getvalue()
