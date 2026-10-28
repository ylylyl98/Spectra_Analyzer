"""Real Quick XY analysis controls and typed record-boundary helpers."""
from PySide6.QtCore import QObject, Qt, QPointF, QPoint, QTimer
from PySide6.QtTest import QTest
from PySide6.QtCore import QCoreApplication
from tests import formal_quick_helpers as formal


def analysis_command(panel, command):
    owner = panel.bridge.ownerToken
    result = panel.bridge.command(command, owner)
    QCoreApplication.processEvents()
    return result


def choose_analysis(panel, field, value):
    bridge = panel.bridge
    token = bridge.beginEdit(field)
    try:
        return bridge.choose(field, value, token)
    finally:
        bridge.endEdit(token)
        QCoreApplication.processEvents()


def set_analysis_draft(panel, field, text, *, commit=False):
    bridge = panel.bridge
    token = bridge.beginEdit(field)
    try:
        changed = bridge.setDraft(field, str(text), token)
        return changed and (bridge.commit(token) if commit else True)
    finally:
        bridge.endEdit(token)
        QCoreApplication.processEvents()


def analysis_field(panel, field):
    if panel.details_dialog is None or not panel.details_dialog.window().isVisible():
        analysis_command(panel, 'settings')
    dialog = panel.details_dialog
    item = dialog.rootObject().findChild(QObject, 'xy-analysis-' + field)
    if item is None:
        raise AssertionError('Missing real Quick analysis editor: ' + field)
    return dialog.window(), item


def type_analysis_field(panel, field, text):
    window, item = analysis_field(panel, field)
    formal.ensure_visible(item)
    item.forceActiveFocus()
    window.requestActivate()
    QCoreApplication.processEvents()
    formal.replace_text(window, text)
    return window, item


def quick_descendants(item):
    for child in item.childItems():
        yield child
        yield from quick_descendants(child)


def seed_field(dialog, seed_id, field):
    for item in quick_descendants(dialog.rootObject()):
        if item.objectName() == 'xy-seed-' + field and item.property('seedId') == seed_id:
            return item
    raise AssertionError('Missing seed Quick editor: ' + seed_id + '.' + field)


def type_seed_field(dialog, seed_id, field, text):
    item = seed_field(dialog, seed_id, field)
    formal.ensure_visible(item)
    item.forceActiveFocus()
    window = dialog.window()
    window.requestActivate()
    QCoreApplication.processEvents()
    formal.replace_text(window, text)
    return item


def click_quick(dialog, name):
    item = dialog.rootObject().findChild(QObject, name)
    if item is None:
        raise AssertionError('Missing Quick control: ' + name)
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
    QTest.mouseClick(dialog.window(), Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
    QCoreApplication.processEvents()


def run_seed_dialog(panel, action):
    """Run the production modal route and surface timer assertion failures."""
    failures, visited = [], []
    def interact():
        dialog = panel._seeds_dialog
        if dialog is None:
            failures.append(AssertionError('Seeds command did not open its Quick window'))
            return
        visited.append(True)
        try:
            action(dialog)
        except BaseException as exc:
            failures.append(exc)
        finally:
            if dialog.window().isVisible():
                dialog.reject()
    timer = QTimer(panel)
    timer.setSingleShot(True)
    timer.timeout.connect(interact)
    timer.start(50)
    try:
        analysis_command(panel, 'seeds')
    finally:
        timer.stop()
        timer.deleteLater()
    if failures:
        raise failures[0]
    assert visited, 'Seeds command did not enter the modal Quick route'


def choose_seed_kind(dialog, seed_id, kind):
    item = seed_field(dialog, seed_id, 'kind')
    item.forceActiveFocus()
    dialog.window().requestActivate()
    QCoreApplication.processEvents()
    QTest.keyClick(dialog.window(), Qt.Key_Down if kind == 'dip' else Qt.Key_Up)
    QCoreApplication.processEvents()
    assert item.property('currentText') == kind
