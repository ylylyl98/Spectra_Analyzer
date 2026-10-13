"""Ordinary annotation coordinates against the final rendered axes rectangle."""
from math import log10


def _scales(snapshot, presentation):
    scales = (snapshot.x_scale, snapshot.y_scale)
    return scales[::-1] if presentation.axis.exchange_xy else scales


def _fraction(value, limits, scale):
    low, high = limits
    if scale == 'log':
        if min(value, low, high) <= 0:
            raise ValueError('Data annotation coordinates on a log axis must be positive.')
        value, low, high = log10(value), log10(low), log10(high)
    return (value-low)/(high-low)


def _value(fraction, limits, scale):
    low, high = limits
    if scale == 'log':
        return 10**(log10(low)+fraction*(log10(high)-log10(low)))
    return low+fraction*(high-low)


def coordinates_to_page(position, size, page, snapshot, presentation, space):
    """Return top-left page pixels for display only; positions remain ordinary units."""
    x, y = position
    if space == 'data':
        if presentation.axis.exchange_xy:
            x, y = y, x
        sx, sy = _scales(snapshot,presentation)
        x = _fraction(x,page.display_x_limits,sx)
        y = _fraction(y,page.display_y_limits,sy)
    left,bottom,width,height = page.axes_bounds
    return ((left+x*width)*size[0], (1-bottom-y*height)*size[1])


def page_to_coordinates(point, size, page, snapshot, presentation, space):
    left,bottom,width,height = page.axes_bounds
    x = (point[0]/size[0]-left)/width
    y = (1-point[1]/size[1]-bottom)/height
    if space == 'data':
        sx,sy = _scales(snapshot,presentation)
        x = _value(x,page.display_x_limits,sx)
        y = _value(y,page.display_y_limits,sy)
        if presentation.axis.exchange_xy:
            x,y = y,x
    return x,y
