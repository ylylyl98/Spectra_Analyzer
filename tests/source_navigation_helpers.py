"""Production source scene controls and actual Quick input for focused tests."""
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from tests.formal_quick_helpers import item, pump, ensure_visible, input_receiver, replace_text


def control(window, name):
    item(window, 'sourceNavigation')
    try:
        return item(window, name)
    except AssertionError:
        # Menus are retained nonvisual QML popup objects.
        result = window.root_window.findChild(QObject, name)
        assert result is not None, f'Missing source Quick control {name}'
        return result


def reveal(window, *, inventory=False):
    window._set_dat_details_requested(True)
    assert window.request_task(0)
    pump()
    root = item(window, 'sourceNavigation')
    root.setProperty('advancedExpanded', True)
    if inventory:
        root.setProperty('inventoryExpanded', True)
    input_receiver(window, root).show()
    pump()


def click(window, name, button=Qt.LeftButton, *, menu=False):
    if not menu:
        reveal(window, inventory=name in ('sourceLoad', 'sourceBrowse', 'sourceFolders', 'sourceFiles'))
    target = control(window, name)
    if not menu:
        ensure_visible(target)
    point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
    QTest.mouseClick(input_receiver(window, target), button, pos=point)
    pump()
    return target


def key(window, name, key, modifier=Qt.NoModifier):
    reveal(window, inventory=name == 'sourceFiles')
    target = control(window, name)
    target.forceActiveFocus()
    QTest.keyClick(input_receiver(window, target), key, modifier)
    pump()


def search(window, text):
    target = click(window, 'sourceSearch')
    replace_text(input_receiver(window, target), text)
    pump()
