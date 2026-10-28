"""Semantic XY inspector helpers; these never construct compatibility widgets."""
from PySide6.QtCore import QCoreApplication, QObject, QPointF, QPoint, Qt
from PySide6.QtTest import QTest
from tests import formal_quick_helpers as formal


def application_owner(page):
    """Return the production owner for an XYPlotController, if attached."""
    owner = page.parent()
    return owner if getattr(owner, '_quick_host', None) is not None else None


def inspector_host(page, section=0):
    owner = application_owner(page)
    if owner is None:
        return (page.curves_panel, page.analysis_panel, page.display_panel)[section]
    owner.dat_details_action.setChecked(True)
    formal.click_item(owner, 'task_' + str(section))
    return owner


def set_xy_draft(page, field, value, *, commit=False):
    page.inspector_bridge.setDraft(field, str(value) if not isinstance(value, bool) else value)
    return page.inspector_bridge.commitField(field) if commit else None


def xy_value(page, field):
    return page.inspector_bridge.values_map()[field]


def select_xy_rows(page, rows, current=None):
    # Canonical fixture setup: retain the original state-owner boundary. Actual
    # pointer/keyboard selection is covered by the UI-specific integration tests.
    ids = list(page.comparison.curves)
    current = (rows[0] if rows else None) if current is None else current
    return page.set_curve_selection({ids[row] for row in rows}, ids[current] if current is not None else page._active_curve_id)


def xy_field(page, field):
    owner = application_owner(page)
    if owner is not None:
        section = 0 if field in ('label', 'color', 'width', 'y_offset') or field.startswith('marker_') else 2
        host = inspector_host(page, section)
        owner.reveal_xy_inspector(page, section, field)
        formal.pump(60)
        return host, formal.ensure_visible(formal.item(host, 'xyField_' + field))
    for panel in (page.curves_panel, page.display_panel):
        item = panel.rootObject().findChild(QObject, 'xyField_' + field)
        if item is not None:
            return panel, item
    raise AssertionError('No QML editor: ' + field)


def click_xy_item(panel, item, *, modifiers=Qt.NoModifier):
    if not hasattr(panel, 'quickWindow'):
        formal.ensure_visible(item)
    position = item.mapToScene(QPointF(item.width()/2, item.height()/2))
    receiver = panel if hasattr(panel, 'quickWindow') else formal.input_receiver(panel, item)
    QTest.mouseClick(receiver, Qt.LeftButton, modifiers, QPoint(round(position.x()), round(position.y())))
    QCoreApplication.processEvents()


def type_xy_field(page, field, text):
    panel, item = xy_field(page, field)
    if hasattr(panel, 'quickWindow'):
        # QQuickWidget's owned input route uses QTest text delivery; sending a
        # QInputMethodEvent to this hybrid widget can crash native Qt.
        item.forceActiveFocus()
        panel.setFocus()
        QTest.keyClick(panel, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(panel, text)
        QCoreApplication.processEvents()
        return panel, item
    click_xy_item(panel, item)
    formal.replace_text(formal.input_receiver(panel, item), text)
    QCoreApplication.processEvents()
    return panel, item
