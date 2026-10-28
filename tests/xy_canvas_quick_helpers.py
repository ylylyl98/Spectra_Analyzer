"""Actual XY Quick input; coordinates cross the published image and axes."""
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtCore import QCoreApplication

from tests.formal_quick_helpers import pump
from tests import formal_quick_helpers as formal
from tests.xy_helpers import application_owner


def descendants(root):
    yield root
    for child in root.childItems():
        yield from descendants(child)


def item(host, name):
    return formal.item(host, name)


def click_item(host, control):
    assert control.isVisible(), control.objectName()
    assert control.isEnabled(), control.objectName()
    formal.ensure_visible(control)
    point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
    QTest.mouseClick(formal.input_receiver(host, control), Qt.LeftButton, pos=point)
    pump(40)


def click_named(host, name):
    click_item(host, item(host, name))


def command(page, key):
    host = application_owner(page)
    if host is None:
        host = page.command_bar
        host.show()
    pump(40)
    name = 'XYCommand_' + key.replace(':', '_')
    control = item(host, name)
    if not control.isVisible():
        click_named(host, 'XYCommandsOverflow')
        control = item(host, 'XYOverflow_' + key.replace(':', '_'))
    click_item(host, control)


def navigation(page, key):
    click_named(application_owner(page) or page.navigation_view, 'XYNavigation_' + key)


def data_point(page, xy, *, axes=False):
    page.canvas.draw()
    transform = page.axes.transAxes if axes else page.axes.transData
    x, y = transform.transform(xy)
    ratio = page.canvas.device_pixel_ratio
    height = page.canvas.get_width_height(physical=True)[1]
    image = item(canvas_host(page), 'XYCanvasImage')
    return image.mapToScene(QPointF(x/ratio, (height-y)/ratio)).toPoint()


def canvas_host(page):
    owner = application_owner(page)
    if owner is not None:
        return owner.graph_documents.content_item(('xy', page.comparison_id, ''))
    return page.canvas_view


def canvas_receiver(page):
    owner = application_owner(page)
    return formal.input_receiver(owner or page.canvas_view, item(canvas_host(page), 'XYCanvasRoot'))


def click_data(page, xy, *, modifiers=Qt.NoModifier, axes=False):
    QTest.mouseClick(canvas_receiver(page), Qt.LeftButton, modifiers, data_point(page, xy, axes=axes))
    pump(50)


def press(page, point, *, modifiers=Qt.NoModifier):
    QTest.mousePress(canvas_receiver(page), Qt.LeftButton, modifiers, point)


def move(page, point, *, modifiers=Qt.NoModifier):
    host = canvas_receiver(page)
    point = QPoint(round(point.x()), round(point.y()))
    local = QPointF(point)
    event = QMouseEvent(QEvent.MouseMove, local, local,
                        QPointF(host.mapToGlobal(point)), Qt.NoButton, Qt.LeftButton, modifiers)
    QCoreApplication.sendEvent(host, event)


def release(page, point):
    QTest.mouseRelease(canvas_receiver(page), Qt.LeftButton, pos=point)
    pump(60)


def drag(page, start, end):
    start, end = data_point(page, start), data_point(page, end)
    press(page, start)
    move(page, start+(end-start)/2)
    move(page, end)
    release(page, end)


def focus_canvas(page):
    root = item(canvas_host(page), 'XYCanvasRoot')
    root.forceActiveFocus(Qt.OtherFocusReason)
    receiver = canvas_receiver(page)
    if hasattr(receiver, 'requestActivate'):
        receiver.requestActivate()
    QCoreApplication.processEvents()
    assert root.hasActiveFocus()


def key(page, code, modifiers=Qt.NoModifier):
    focus_canvas(page)
    QTest.keyClick(canvas_receiver(page), code, modifiers)
    pump(60)
