"""Real Quick input helpers; these are not native widget control adapters."""
from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from tests.formal_quick_helpers import (item as scene_item, input_receiver as scene_input_receiver,
                                        replace_text)


def pump(milliseconds=20):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def item(host, name):
    if hasattr(host, 'workbook_id') and hasattr(host, 'sheet_pages'):
        owner = host.parent()
        if hasattr(owner, 'graph_documents'):
            host = owner.graph_documents.content_item(('workbook', host.workbook_id, ''))
    return scene_item(host, name)


def input_receiver(host, control):
    if hasattr(host, 'workbook_id') and hasattr(host, 'sheet_pages'):
        owner = host.parent()
        if hasattr(owner, '_quick_host'):
            host = owner._quick_host
    return scene_input_receiver(host, control)


def host_for(sheet):
    # Sheet models belong to the controller; input goes to its real visible host.
    owner = sheet.parent().parent()
    return sheet.parent() if hasattr(owner, '_quick_host') else owner


def point_in(host, name, x=12, y=12):
    point = item(host, name).mapToScene(QPointF(x, y))
    return QPoint(round(point.x()), round(point.y()))


def click_item(host, name):
    control = item(host, name)
    point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2))
    QTest.mouseClick(input_receiver(host, control), Qt.LeftButton,
                     pos=QPoint(round(point.x()), round(point.y())))
    pump()


def header_point(sheet, column, row=0, offset=12):
    host = host_for(sheet)
    headers = item(host, 'sheetHeaders')
    row_height = headers.height() / 3
    return point_in(host, 'sheetHeaders', sheet.columnPosition(column) - float(headers.property('contentX')) + offset,
                    row * row_height + row_height / 2)


def reveal_column(sheet, column):
    host = host_for(sheet)
    headers, body = item(host, 'sheetHeaders'), item(host, 'sheetCells')
    left, width = sheet.columnPosition(column), sheet.columnWidth(column)
    scroll, viewport = float(body.property('contentX')), headers.width()
    if left < scroll:
        body.setProperty('contentX', left)
    elif left + min(width, viewport) > scroll + viewport:
        body.setProperty('contentX', max(left, left + width - viewport) if width > viewport else left + width - viewport)
    pump()


def click_header(sheet, column, modifiers=Qt.NoModifier):
    host = host_for(sheet)
    reveal_column(sheet, column)
    QTest.mouseClick(input_receiver(host, item(host, 'sheetHeaders')),
                     Qt.LeftButton, modifiers, header_point(sheet, column))
    pump()


def drag_header(sheet, first, last, modifiers=Qt.NoModifier):
    host = host_for(sheet)
    receiver = input_receiver(host, item(host, 'sheetHeaders'))
    QTest.mousePress(receiver, Qt.LeftButton, modifiers, header_point(sheet, first))
    QTest.mouseMove(receiver, header_point(sheet, last))
    QTest.mouseRelease(receiver, Qt.LeftButton, modifiers, header_point(sheet, last))
    pump()


def focus_table(sheet):
    host = host_for(sheet)
    body = item(host, 'sheetCells')
    receiver = input_receiver(host, body)
    if hasattr(receiver, 'requestActivate'):
        receiver.requestActivate()
    else:
        receiver.setFocus()
    body.forceActiveFocus()
    pump()


def table_key(sheet, key, modifiers=Qt.NoModifier):
    focus_table(sheet)
    host = host_for(sheet)
    QTest.keyClick(input_receiver(host, item(host, 'sheetCells')), key, modifiers)
    pump()


def set_metadata_draft(sheet, text):
    host = host_for(sheet)
    editor = item(host, 'sheetMetadataEditor')
    editor.forceActiveFocus()
    receiver = input_receiver(host, editor)
    if text:
        replace_text(receiver, text)
    else:
        QTest.keyClick(receiver, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(receiver, Qt.Key_Backspace)
        pump()
    return editor


def selection_ranges(sheet):
    return [(s.top(), s.left(), s.bottom(), s.right()) for s in sheet.selection_model.selection()]


def context_action(sheet, column, action):
    host = host_for(sheet)
    reveal_column(sheet, column)
    QTest.mouseClick(input_receiver(host, item(host, 'sheetHeaders')),
                     Qt.RightButton, pos=header_point(sheet, column))
    pump()
    click_item(host, 'sheetContext' + action)
