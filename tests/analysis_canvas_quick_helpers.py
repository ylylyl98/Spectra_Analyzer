"""Real Quick canvas input; data points cross actual Matplotlib transforms.

Geometry is read from the production image/Flickable. No QWidget canvas or
scroll adapter is supplied by these test helpers.
"""
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtCore import QCoreApplication

from tests.formal_quick_helpers import item, pump, input_receiver, replace_text


def peak_menu(window):
    from PySide6.QtCore import QObject
    menu = item(window, 'applicationDatCanvas').findChild(QObject, 'analysisPeakMenu')
    assert menu is not None
    return menu


def click_peak_menu(window, key):
    names = {'set_label': 'analysisPeakSetLabel', 'delete': 'analysisPeakDelete'}
    host = item(window, 'applicationDatCanvas')
    control = item(host, names[key])
    point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
    QTest.mouseClick(input_receiver(window, control), Qt.LeftButton, pos=point)
    pump(60)


def choose_peak_label(window, value=None, *, accept=True, before_accept=None):
    """Drive the real synchronous CommonPrompt after the actual Quick MenuItem."""
    from unittest.mock import patch
    from PySide6.QtCore import QTimer
    from spectra_app.quick_dialog import QuickDialog
    errors, observed = [], []
    original_show = QuickDialog.show

    def show(dialog):
        original_show(dialog)

        def drive():
            try:
                assert not peak_menu(window).property('visible'), 'Menu must close before the prompt'
                assert not window.dat_canvas.peakMenu
                editor = item(dialog, 'commonPromptEditor')
                assert editor.hasActiveFocus(), 'Menu close must not steal prompt focus'
                observed.append((dialog.bridge.mode, dialog.bridge.draft,
                                 dialog.bridge._minimum, dialog.bridge._maximum))
                if before_accept is not None:
                    before_accept(dialog)
                if value is not None:
                    replace_text(dialog.window(), value)
                QTest.keyClick(dialog.window(), Qt.Key_Return if accept else Qt.Key_Escape)
            except BaseException as error:
                errors.append(error)
                dialog.reject()

        QTimer.singleShot(40, drive)

    with patch.object(QuickDialog, 'show', show):
        click_peak_menu(window, 'set_label')
    if errors:
        raise errors[0]
    assert len(observed) == 1, 'The actual label prompt must open exactly once'
    return observed[0]


def reveal(window):
    assert window.request_outer_view('analysis')
    window.root_window.show()
    window.root_window.requestActivate()
    host = item(window, 'applicationDatCanvas')
    host.forceActiveFocus(Qt.OtherFocusReason)
    pump(180)
    window.canvas.draw()
    assert host.isVisible()
    return host


def data_point(window, ax, xy, *, ensure_visible=True):
    """Project data through the real axes and actual scrolled image item."""
    host = item(window, 'applicationDatCanvas')
    image = item(host, 'analysisCanvasImage')
    viewport = item(host, 'analysisCanvasViewport')
    def project():
        x, y = ax.transData.transform(xy)
        ratio = window.canvas.device_pixel_ratio
        height = window.canvas.get_width_height(physical=True)[1]
        return QPointF(x / ratio, (height-y) / ratio)

    logical = project()
    if ensure_visible:
        for axis, value, extent, total in (
            ('X', logical.x(), viewport.width(), viewport.property('contentWidth')),
            ('Y', logical.y(), viewport.height(), viewport.property('contentHeight')),
        ):
            offset = viewport.property('content'+axis)
            if value < offset+5 or value > offset+extent-5:
                viewport.setProperty('content'+axis, max(0., min(value-extent/2, total-extent)))
        pump(20)
        # Pending layout can change the axes transform during the event loop.
        logical = project()
    return image.mapToScene(logical).toPoint()


def press(window, point, *, button=Qt.LeftButton, modifiers=Qt.NoModifier):
    QTest.mousePress(window.root_window, button, modifiers, point)


def move(window, point, *, buttons=Qt.LeftButton, modifiers=Qt.NoModifier):
    host = window.root_window
    local = QPointF(point)
    event = QMouseEvent(QEvent.MouseMove, local, local,
                        QPointF(host.mapToGlobal(point)), Qt.NoButton, buttons, modifiers)
    QCoreApplication.sendEvent(host, event)


def release(window, point, *, button=Qt.LeftButton, modifiers=Qt.NoModifier):
    QTest.mouseRelease(window.root_window, button, modifiers, point)


def click_data(window, ax, xy, *, button=Qt.LeftButton):
    point = data_point(window, ax, xy)
    QTest.mouseClick(window.root_window, button, pos=point)
    pump(50)


def drag_data(window, ax, start, end, *, steps=4):
    start_point = data_point(window, ax, start)
    end_point = data_point(window, ax, end, ensure_visible=False)
    press(window, start_point)
    for index in range(1, steps+1):
        move(window, start_point+(end_point-start_point)*index/steps)
    release(window, end_point)
    pump(80)


def path_data(window, ax, vertices):
    points = [data_point(window, ax, xy, ensure_visible=index == 0)
              for index, xy in enumerate(vertices)]
    press(window, points[0])
    for point in points[1:]:
        move(window, point)
    release(window, points[-1])
    pump(80)
