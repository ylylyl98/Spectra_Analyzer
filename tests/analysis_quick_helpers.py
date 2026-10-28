"""Semantic Analysis fixtures and real Quick input for editor transactions."""
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtTest import QTest
from tests.formal_quick_helpers import item, pump, ensure_visible, input_receiver, replace_text


def state(window):
    return window.analysis_workbench.state


def set_choice(window, field, value):
    bridge = window.analysis_workbench
    bridge.setChoice(field, str(value), bridge.ownerToken)
    assert state(window)[field] == value, (field, value, state(window)[field], bridge.errorText)


def setup_number(window, field, value):
    """Canonical fixture setup through the production commit boundary."""
    bridge = window.analysis_workbench
    bridge.draftNumber(field, str(value), bridge.ownerToken)
    assert bridge.commitEditors(bridge.ownerToken), bridge.errorText


def setup_filter(window, field, value):
    bridge = window.analysis_workbench
    bridge.draftFilter(field, str(value), bridge.ownerToken)
    assert bridge.commitEditors(bridge.ownerToken), bridge.errorText


def command(window, action):
    bridge = window.analysis_workbench
    bridge.command(action, bridge.ownerToken)


def edit_seed(window, seed_id, field, value):
    bridge = window.analysis_workbench
    bridge.draftSeed(seed_id, state(window)['gate'], field, str(value), bridge.ownerToken)
    assert bridge.commitEditors(bridge.ownerToken), bridge.errorText


def select_seed(window, seed_id):
    bridge = window.analysis_workbench
    bridge.selectSeed(seed_id, state(window)['gate'], bridge.ownerToken)
    assert state(window)['selectedSeedId'] == seed_id


def control(window, name):
    # The production Properties retains View editors with the same field names.
    # Component hosts and command panels remain valid independent input roots.
    if hasattr(window, 'root_window'):
        return item(item(window, 'analysisWorkbench'), name)
    return item(window, name)


def viewport_rect(window, name):
    target = control(window, name)
    viewport = item(window, 'propertiesScroll').property('contentItem')
    point = target.mapToItem(viewport, QPointF())
    return QRect(round(point.x()), round(point.y()), round(target.width()), round(target.height()))


def reveal(window, name):
    window.dat_details_action.setChecked(True)
    assert window.request_task(1)
    pump(40)
    if name in ('xmin', 'xmax', 'd2Window'):
        window.analysis_workbench.focusRequested.emit('detection', name)
    pump(60)
    target = ensure_visible(control(window, name))
    input_receiver(window, target).requestActivate()
    return target


def type_text(host, name, text, *, commit=False):
    """Edit through the actual control window, retaining its draft transaction."""
    target = ensure_visible(control(host, name))
    point = target.mapToScene(QPointF(min(30, target.width() / 2), target.height() / 2))
    receiver = input_receiver(host, target)
    QTest.mouseClick(receiver, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
    replace_text(receiver, text)
    assert target.property('text') == str(text), (name, target.property('text'), text)
    if commit:
        QTest.keyClick(receiver, Qt.Key_Return)
    pump()
    return target


def edit_number(window, field, text, *, commit=False):
    reveal(window, field)
    return type_text(window, field, text, commit=commit)


def click(window, name):
    target = reveal(window, name)
    point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
    QTest.mouseClick(input_receiver(window, target), Qt.LeftButton, pos=point)
    pump()
    return target


def click_command(window, name):
    """Send input to the actual command panel or its Popup.Window menu."""
    target = ensure_visible(item(window, name))
    point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
    QTest.mouseClick(input_receiver(window, target), Qt.LeftButton, pos=point)
    pump()
    return target
