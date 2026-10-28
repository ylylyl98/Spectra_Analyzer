"""Semantic shared DAT Properties operations; user actions use the real Quick panel.

``setup_number`` is canonical programmatic setup, so it never adds an implicit
color Fix. ``edit_number`` types into the actual rendered editor and finishes on
Return only when requested. Use bridge.draftNumber explicitly for runtime-only
setup of several simultaneous drafts; moving focus may finish an editor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from tests.quick_application_helpers import pump

if TYPE_CHECKING:
    from spectra_app.main_window import ApplicationController


def state(window: ApplicationController):
    return window.dat_properties.state


def _input_window(window: ApplicationController):
    # Use the production host's retained wrappers. QQuickItem.window() may
    # reparent a QML window wrapper under a temporary item wrapper in PySide.
    host = window._quick_host
    return host._floating_properties if window.propertiesFloating else window.root_window


def dat_item(window: ApplicationController, name):
    properties = window.root_window.findChild(QQuickItem, 'applicationProperties')
    assert properties is not None, 'Missing actual ApplicationProperties root'
    panel = properties.findChild(QQuickItem, 'sharedDatProperties')
    assert panel is not None, 'DAT View task has not loaded SharedDatProperties'
    def find(candidate):
        if candidate.objectName() == name:
            return candidate
        for child in candidate.childItems():
            match = find(child)
            if match is not None:
                return match
    control = find(panel)
    assert control is not None, f'Missing actual DAT Quick item {name!r}'
    return control


def section_for(field):
    if field in ('xmin', 'xmax', 'ymin', 'ymax', 'axis_override') or field.startswith(('xmin_', 'xmax_', 'ymin_', 'ymax_')):
        return 'ranges'
    if field in ('plot_title', 'y_axis_name', 'y_axis_unit', 'signal_title', 'signal_preset', 'fix_annotations', 'signal_title_auto') or field.startswith('title_'):
        return 'labels'
    if field.startswith('shared_'):
        if any(part in field for part in ('view', 'range', 'source_axis')):
            return 'ranges'
        if any(part in field for part in ('label', 'template')):
            return 'labels'
    return 'colors'


def reveal(window: ApplicationController, field):
    window.dat_details_action.setChecked(True)
    assert window.request_task(2)
    pump()
    properties = window.root_window.findChild(QQuickItem, 'applicationProperties')
    assert properties is not None and properties.isVisible()
    host = _input_window(window)
    assert host is not None, 'Properties has no real Quick window'
    host.requestActivate()
    owner = window.dat_properties.ownerToken
    window.dat_properties.focusRequested.emit(section_for(field), field)
    pump(65)
    control = dat_item(window, field)
    scroll = properties.findChild(QQuickItem, 'propertiesScroll')
    assert scroll is not None, 'Shared DAT panel has no active Properties scroll owner'
    viewport = scroll.property('contentItem')
    assert viewport is not None and viewport.metaObject().indexOfProperty('contentY') >= 0
    top = control.mapToItem(viewport, QPointF()).y() + viewport.property('contentY')
    maximum = max(0., viewport.property('contentHeight') - viewport.height())
    viewport.setProperty('contentY', max(0., min(top - 16., maximum)))
    pump()
    center = control.mapToItem(viewport, QPointF(control.width() / 2, control.height() / 2))
    assert control.isVisible() and control.isEnabled(), f'{field} is not an active Quick control'
    assert 0 <= center.x() <= viewport.width() and 0 <= center.y() <= viewport.height(), (field, center)
    assert host.contentItem().isAncestorOf(control), 'DAT editor belongs to another Quick window'
    assert window.dat_properties.ownerToken == owner, 'DAT owner changed while revealing editor'
    return control


def click(window: ApplicationController, name):
    control = reveal(window, name)
    host = _input_window(window)
    point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2))
    QTest.mouseClick(host, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
    pump()
    return control


def _edit(window, field, text, finish, *, label=False):
    control = reveal(window, field)
    host = _input_window(window)
    point = control.mapToScene(QPointF(min(30, control.width() / 2), control.height() / 2))
    QTest.mouseClick(host, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
    QTest.keyClick(host, Qt.Key_A, Qt.ControlModifier)
    if label:
        # Deliver text exactly while retaining one native typing event per
        # character, including case/Unicode and title-latency observations.
        if not str(text):
            QTest.keyClick(host, Qt.Key_Backspace)
        for character in str(text):
            event = QInputMethodEvent()
            event.setCommitString(character)
            QCoreApplication.sendEvent(host, event)
    else:
        # QtTest.keyClicks accepts QWidget only; the production host is QQuickWindow.
        for character in str(text):
            QTest.keyClick(host, ord(character.upper()),
                           Qt.ShiftModifier if character.isupper() else Qt.NoModifier)
    assert control.property('text') == str(text), (field, control.property('text'), text)
    if finish:
        QTest.keyClick(host, Qt.Key_Return)
    pump()
    return control


def edit_number(window, field, text, commit=False):
    return _edit(window, field, text, commit)


def edit_label(window, field, text, finish=False):
    return _edit(window, field, text, finish, label=True)


def choose(window, field, value):
    """Open an actual Quick ComboBox popup and click one visible target delegate."""
    from tests.formal_quick_helpers import input_receiver
    models = {'colormap': 'colormaps', 'mapping': 'mappings', 'color_scale': 'color_scales',
              'axis_override': 'axis_overrides', 'title_preset': 'title_presets',
              'signal_preset': 'signal_presets'}
    choices = state(window)[models[field]]
    values = [entry.get('value') if isinstance(entry, dict) else entry for entry in choices]
    if value not in values:
        raise AssertionError(f'{value!r} not in {field}: {values!r}')
    control = click(window, field)
    popup = next(child for child in control.findChildren(QObject) if child.inherits('QQuickPopup'))
    assert popup.property('visible') and popup.property('opened'), 'Native Quick choice popup did not remain open'
    view = popup.property('contentItem')
    rows = [child for container in view.childItems() for child in container.childItems()
            if child.inherits('QQuickItemDelegate')]
    row = next(child for child in rows if child.property('index') == values.index(value))
    point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2))
    receiver = input_receiver(window, row)
    assert receiver is not None and receiver.isVisible(), 'Choice delegate has no visible native input window'
    assert row.isVisible() and row.isEnabled(), 'Choice delegate is not available for input'
    QTest.mouseClick(receiver, Qt.LeftButton,
                     pos=QPoint(round(point.x()), round(point.y())))
    pump()
    return control


def set_choice(window, field, value):
    bridge = window.dat_properties
    assert bridge.setChoice(field, value, bridge.ownerToken)


def set_flag(window, field, value):
    bridge = window.dat_properties
    assert bridge.setFlag(field, bool(value), bridge.ownerToken)


def command(window, action):
    bridge = window.dat_properties
    assert bridge.command(action, bridge.ownerToken)


def setup_number(window, field, value):
    """Set canonical setup values without treating them as a user color edit."""
    if field.startswith('edge'):
        window._on_color_region_boundary_changed(int(field[-1]), float(value))
        window.dat_properties.syncFromOwner()
        return
    window._dat_project_number(field, float(value))
    window._on_view_limit_changed(field)
    window.dat_properties.syncFromOwner()
