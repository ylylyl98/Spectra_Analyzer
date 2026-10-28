"""Semantic Project setup plus real QML item and pointer probes."""
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest
from tests.formal_quick_helpers import item as quick_item, input_receiver, pump

from spectra_app.project_model import row_key


def settle_project(window):
    window.root_window.update()
    pump(45)


def project_control(window, name):
    try:
        return quick_item(window, name)
    except AssertionError:
        # Menus are retained QML popup objects rather than visual QQuickItems.
        result = window.root_window.findChild(QObject, name)
        assert result is not None, f'Missing QML Project control: {name}'
        return result


def reveal_project_row(window, key):
    row = window.project.row_for_key(key)
    if row is None:
        raise AssertionError(f'Missing Project row: {key}')
    ancestor = row.parent
    while ancestor is not None:
        window.project.set_expanded(ancestor, True)
        ancestor = window.project.row_for_key(ancestor).parent
    index = window.project.visible_index(key)
    window.project.revealRequested.emit(index)
    settle_project(window)


def project_row(window, key):
    reveal_project_row(window, key)
    return project_control(window, 'projectRow_' + row_key(key))


def project_label(window, key):
    reveal_project_row(window, key)
    return project_control(window, 'projectLabel_' + row_key(key))


def click_project_control(window, name, button=Qt.LeftButton, modifiers=Qt.NoModifier):
    item = project_control(window, name)
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    QTest.mouseClick(input_receiver(window, item), button, modifiers, point)
    settle_project(window)


def click_project_row(window, key, modifiers=Qt.NoModifier, button=Qt.LeftButton, lower=False):
    item = project_row(window, key)
    x = max(36, item.width() - 18)
    y = item.height() - 4 if lower else item.height()/2
    point = item.mapToScene(QPointF(x, y)).toPoint()
    QTest.mouseClick(input_receiver(window, item), button, modifiers, point)
    settle_project(window)
    return item
