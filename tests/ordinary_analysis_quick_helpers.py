"""Semantic setup and real Quick input for the ordinary DAT Analysis panel.

No helper emulates a removed QWidget. Setup helpers use the public bridge;
pointer/key helpers exercise the QML consumer and preserve gesture ownership.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtTest import QTest

from tests.formal_quick_helpers import item, pump, ensure_visible, input_receiver, replace_text


def state(window):
    return window.ordinary_analysis.state


def value(window, field):
    return state(window)['values'][field]


def text(window, field):
    return state(window)['texts'][field]


def ui(window, key):
    return state(window)['ui'][key]


def command_enabled(window, command):
    return item(window, 'ordinaryCommand_' + command).isEnabled()


def command_text(window, command):
    return item(window, 'ordinaryCommand_' + command).property('text')


def summary(window):
    return state(window)['summary']


def set_summary(window, text):
    """Arrange an existing scientific result, without an editable summary UI."""
    from spectra_app.analysis_document import analysis_owner
    owner = analysis_owner(window.workspace.active or window._unregistered_session)
    owner.summary = text
    window.ordinary_analysis.refresh()
    window._schedule_project_modified()


def draft(window, field, text):
    bridge = window.ordinary_analysis
    assert bridge.setDraft(field, str(text), bridge.ownerToken), field


def commit(window):
    bridge = window.ordinary_analysis
    return bridge.commit(bridge.ownerToken)


def set_number(window, field, number):
    # Programmatic numeric fixtures retain the former native value rounding;
    # edit()/draft() intentionally preserve exact user-entered raw text.
    draft(window, field, window.ordinary_analysis.format(field, number))
    assert commit(window), (field, number, window.ordinary_analysis.errorText)


def set_value(window, field, value):
    bridge = window.ordinary_analysis
    assert bridge.setValue(field, value, bridge.ownerToken), (field, value)


def set_mode(window, mode, active):
    bridge = window.ordinary_analysis
    key = {'circle': 'circleActive', 'lasso': 'lassoActive'}[mode]
    if bool(state(window)[key]) != bool(active):
        assert bridge.trigger(mode, bridge.ownerToken)


def select_tab(window, index):
    window.ordinary_analysis.selectTab(index)
    pump()


def control(window, field):
    return item(window, 'ordinaryField_' + field)


def reveal(window, field=None, tab=None):
    """Reveal the controller's real DAT Analysis Properties task."""
    window.dat_details_action.setChecked(True)
    assert window.request_task(1)
    pump()
    bridge = window.ordinary_analysis
    if field is not None:
        bridge.focusRequested.emit(field, bridge.ownerToken)
    elif tab is not None:
        bridge.selectTab(tab)
    pump(65)
    host = item(window, 'ordinaryAnalysisRoot')
    assert host.isVisible()
    input_receiver(window, host).requestActivate()
    return host


def focus(window, field):
    host = reveal(window, field)
    target = ensure_visible(control(window, field))
    point = target.mapToScene(QPointF(min(30, target.width()/2), target.height()/2)).toPoint()
    QTest.mouseClick(input_receiver(window, target), Qt.LeftButton, pos=point)
    pump()
    return target


def edit(window, field, value, *, commit=False):
    target = focus(window, field)
    receiver = input_receiver(window, target)
    replace_text(receiver, value)
    assert target.property('text') == str(value), (field, target.property('text'), value)
    if commit:
        QTest.keyClick(receiver, Qt.Key_Return)
    pump()
    return target


def key(window, field, key):
    target = focus(window, field)
    QTest.keyClick(input_receiver(window, target), key)
    pump()


def click(window, command):
    host = reveal(window, tab=1 if command in {
        'fit', 'batchFit', 'fullRange', 'currentRange', 'loadCsv', 'clearFit',
        'exportCurrentFit', 'exportAllFit', 'exportSpectrum'} else 0)
    target = ensure_visible(item(host, 'ordinaryCommand_' + command))
    point = target.mapToScene(QPointF(target.width()/2, target.height()/2)).toPoint()
    QTest.mouseClick(input_receiver(window, target), Qt.LeftButton, pos=point)
    pump()
    return target


def enabled(window, field):
    return control(window, field).isEnabled()


def acceptable(window, field):
    """Query raw parsing without committing an editor or scientific mutation."""
    try:
        window.ordinary_analysis.validate_editor()
    except ValueError:
        return False
    return True


def scene_rect(target):
    return QRectF(target.mapToScene(QPointF()), target.size())
