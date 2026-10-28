"""Actual Quick pointer and keyboard input for formal Properties tests."""
from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


def pump(milliseconds=30):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def root_item(host):
    """Resolve an actual Quick host, controller, item, or component widget."""
    if isinstance(host, QQuickItem):
        return host
    if hasattr(host, 'rootObject'):
        return host.rootObject()
    if hasattr(host, 'root_window'):
        return host.root_window.contentItem()
    window = getattr(host, 'window', None)
    if window is not None and not callable(window):
        return window.contentItem()
    if hasattr(host, 'contentItem'):
        return host.contentItem()
    raise TypeError(f'Unsupported Quick host {type(host).__name__}')


def item(host, name):
    def find(root):
        if root.objectName() == name:
            return root
        for child in root.childItems():
            result = find(child)
            if result is not None:
                return result
    root = root_item(host)
    result = find(root)
    if result is None:
        result = root.findChild(QQuickItem, name)
    if result is None and hasattr(host, 'root_window'):
        result = host.root_window.findChild(QQuickItem, name)
    if result is None and getattr(host, 'window', None) is not None and not callable(host.window):
        result = host.window.findChild(QQuickItem, name)
    if result is None:
        raise AssertionError(f'Quick item {name!r} not found')
    return result


def input_receiver(host, control):
    """Use retained production windows, or the component/popup's real receiver."""
    production = getattr(host, '_quick_host', None)
    if production is None and hasattr(host, 'controller') and hasattr(host, 'engine'):
        production = host
    if production is not None:
        for receiver in (production.window, production._floating_properties):
            if receiver is not None:
                content = receiver.contentItem()
                if control is content or content.isAncestorOf(control):
                    return receiver
        # Popup.Window menus own a separate actual QQuickWindow.
        receiver = control.window()
        assert receiver is not None, control.objectName()
        return receiver
    if hasattr(host, 'quickWindow') and control.window() is host.quickWindow():
        return host
    if hasattr(host, 'window') and callable(host.window) and not isinstance(host, QQuickItem):
        return host.window()
    receiver = control.window()
    assert receiver is not None, control.objectName()
    return receiver


def replace_text(receiver, text):
    """Deliver actual input-method text to either a QWindow or component widget."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QInputMethodEvent
    QTest.keyClick(receiver, Qt.Key_A, Qt.ControlModifier)
    event = QInputMethodEvent()
    event.setCommitString(str(text))
    QCoreApplication.sendEvent(receiver, event)
    pump()


def ensure_visible(control):
    """Reveal the target through its actual Flickable ancestors."""
    ancestor = control.parentItem()
    while ancestor is not None:
        if ancestor.metaObject().indexOfProperty('contentY') >= 0:
            top = control.mapToItem(ancestor, QPointF()).y() + ancestor.property('contentY')
            maximum = max(0., ancestor.property('contentHeight') - ancestor.height())
            ancestor.setProperty('contentY', max(0., min(top - 16., maximum)))
        ancestor = ancestor.parentItem()
    pump()
    return control


def click_item(host, name):
    control = ensure_visible(item(host, name))
    point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2))
    QTest.mouseClick(input_receiver(host, control), Qt.LeftButton,
                     pos=QPoint(round(point.x()), round(point.y())))
    pump()


def edit_field(page, field, text, commit=False):
    from spectra_app.formal_plot_bridge import section_for
    if hasattr(page, 'root_window'):
        page.dat_details_action.setChecked(True)
        assert page.request_task(2)
        pump()
        host = item(page, 'formalProperties')
        page.plot_page.formal.focusRequested.emit(section_for(field), field)
        pump(65)
        control = ensure_visible(item(host, field))
        point = control.mapToScene(QPointF(min(30, control.width() / 2), control.height() / 2)).toPoint()
        receiver = input_receiver(page, control)
        receiver.requestActivate()
        QTest.mouseClick(receiver, Qt.LeftButton, pos=point)
        replace_text(receiver, text)
        if commit:
            QTest.keyClick(receiver, Qt.Key_Return)
        pump()
        return control
    host = page.properties_panel
    page.formal.focusRequested.emit(section_for(field), field)
    pump(60)
    control = item(host, field)
    point = control.mapToScene(QPointF(min(30, control.width() / 2), control.height() / 2))
    scroll = page.control_scroll
    target = host.mapTo(scroll.widget(), point.toPoint())
    scroll.ensureVisible(target.x(), target.y())
    pump()
    viewport_point = host.mapTo(scroll.viewport(), point.toPoint())
    assert scroll.viewport().rect().contains(viewport_point), (field, viewport_point)
    QTest.mouseClick(host, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
    QTest.keyClick(host, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(host, text)
    if commit:
        QTest.keyClick(host, Qt.Key_Return)
    pump()
    return control
