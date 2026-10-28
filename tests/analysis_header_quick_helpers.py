"""Real Quick pointer/key input for the shared Analysis header and Gate row."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlEngine, QQmlExpression
from tests.formal_quick_helpers import item, pump, input_receiver, replace_text


def reveal(window):
    assert window.request_outer_view('analysis')
    window.root_window.show()
    window.root_window.requestActivate()
    pump()
    host = item(window, 'analysisGate')
    assert host.isVisible()
    return host


def control(window, field):
    return item(window, {'gate': 'gateValue', 'index': 'gateIndex'}.get(field, field))


def text(window, field):
    return control(window, field).property('text')


def edit(window, field, value, *, commit=False):
    host = reveal(window)
    target = control(window, field)
    point = target.mapToScene(QPointF(min(30, target.width() / 2), target.height() / 2)).toPoint()
    receiver = input_receiver(window, target)
    QTest.mouseClick(receiver, Qt.LeftButton, pos=point)
    replace_text(receiver, value)
    assert target.property('text') == str(value), (field, target.property('text'), value)
    if commit:
        QTest.keyClick(receiver, Qt.Key_Return)
    pump()
    return target


def key(window, key):
    QTest.keyClick(window.root_window, key)
    pump()


def button(window, name, *, press=False, release=False):
    target = item(window, name)
    point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
    action = QTest.mousePress if press else QTest.mouseRelease if release else QTest.mouseClick
    action(input_receiver(window, target), Qt.LeftButton, pos=point)
    pump()
    return target


def header(window, name):
    return item(window, name)


def context(window):
    return '\n'.join(str(header(window, name).property('text'))
                     for name in ('contextTitle', 'contextSummary', 'contextWarning'))


def details(window):
    return item(window, 'analysisHeader').property('accessibleDetails')


def enum_value(target, name):
    """Read a real QML enum; PySide has no QQuickText enum converter."""
    expression = QQmlExpression(QQmlEngine.contextForObject(target), target, 'Number(' + name + ')')
    value, undefined = expression.evaluate()
    assert not undefined and not expression.hasError(), expression.error().toString()
    return int(value)
