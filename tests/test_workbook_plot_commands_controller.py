"""Production Workbook command rows share an engine without sharing authority."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class WorkbookPlotCommandsControllerTests(unittest.TestCase):
    def test_qgui_two_local_command_rows_and_retained_source_authority(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()),
                                 '--controller-probe'], cwd=Path(__file__).resolve().parents[1],
                                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('QGuiApplication Workbook command controllers and two rows: PASS', result.stdout)


def controller_probe():
    import numpy as np
    from unittest.mock import patch
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QPoint, QPointF, Qt, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from spectra_app.dataset_state import Workspace
    from spectra_app.quick_ui import QuickPanel
    from spectra_app.workbook_page import WorkbookController, WorkbookPlotCommandsController
    from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison

    app = QGuiApplication([])
    assert type(app) is QGuiApplication and not isinstance(app, QApplication)

    def pump():
        loop = QEventLoop()
        QTimer.singleShot(30, loop.quit)
        loop.exec()

    def find(root, name):
        if root.objectName() == name:
            return root
        for child in root.childItems():
            result = find(child, name)
            if result is not None:
                return result
        return None

    with patch.object(QWidget, '__init__', side_effect=AssertionError('QWidget constructed')), \
         patch.object(QuickPanel, '__init__', side_effect=AssertionError('QuickPanel constructed')):
        owner = QObject()
        workspace = Workspace()
        book = create_workbook(workspace)
        sheets = [register_xy_sheet(workspace, book.workbook_id, f'Source {index}',
            np.array([[0., 1. + index], [1., 3. + index], [2., 5. + index]]), ['x', 'y'])
            for index in range(2)]
        workbook = WorkbookController(workspace, book.workbook_id, owner)
        first, second = [workbook.sheet_pages[sheet.sheet_id] for sheet in sheets]
        targets = [create_comparison(workspace) for _ in range(2)]
        for page in (first, second):
            page.refresh_targets(targets[0].comparison_id)
        left, right = WorkbookPlotCommandsController(owner), WorkbookPlotCommandsController(owner)
        assert left.parent() is owner and not isinstance(left, QWidget)
        left.bind(first)
        right.bind(second)
        assert left.boundSheet is first and right.boundSheet is second
        events = []
        workbook.quickPlotRequested.connect(lambda *args: events.append(args))
        selected = []
        workbook.targetSelected.connect(selected.append)
        pins, shows, repeats = [], [], []
        left.targetPinChanged.connect(pins.append)
        left.targetShowRequested.connect(lambda: shows.append(True))
        left.repeatRequested.connect(lambda *args: repeats.append(args))

        engine = QQmlEngine()
        assert engine.rootContext().contextProperty('appBridge') is None
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(
            Path(__file__).resolve().parents[1] / 'spectra_app' / 'qml' / 'WorkbookPlotCommands.qml')))
        assert component.isReady(), [error.toString() for error in component.errors()]
        window = QQuickWindow()
        window.resize(1600, 350)
        window.show()

        def mount(controller, x):
            root = component.createWithInitialProperties({'bridge': controller})
            assert root is not None, [error.toString() for error in component.errors()]
            assert isinstance(root, QQuickItem) and root.property('bridge') is controller
            root.setParentItem(window.contentItem())
            root.setPosition(QPointF(x, 0))
            root.setWidth(800)
            root.setHeight(root.implicitHeight())
            pump()
            return root

        def click_item(control):
            assert control is not None and control.isVisible() and control.isEnabled()
            point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2))
            QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
            pump()

        def click(root, name):
            click_item(find(root, name))

        left_root, right_root = mount(left, 0), mount(right, 800)
        click(left_root, 'workbookPlot_line')
        click(right_root, 'workbookPlot_scatter')
        assert events == [(first.sheet_id, 'line', None), (second.sheet_id, 'scatter', None)], events
        assert find(left_root, 'workbookSourceContext').property('text') != find(right_root, 'workbookSourceContext').property('text')
        workbook.set_input_guard(lambda: False)
        click(left_root, 'workbookPlot_line_symbol')
        assert len(events) == 2
        workbook.set_input_guard(None)
        assert first.editMetadata(1, 'long_name')
        first.setEditorText('invalid\ndraft')
        click(left_root, 'workbookPlot_line')
        click(left_root, 'workbookAddAgain')
        assert len(events) == 2 and not repeats and first.editorText == 'invalid\ndraft'
        first.cancelEditor()
        click(left_root, 'workbookPinTarget')
        click(left_root, 'workbookShowTarget')
        click(left_root, 'workbookAddAgain')
        assert pins == [True] and shows == [True]
        assert repeats == [(first.sheet_id, targets[0].comparison_id)]
        click(left_root, 'workbookAppend')
        assert events[-1] == (first.sheet_id, 'line', targets[0].comparison_id)

        # The real open popup captures SheetPage, even after the command row binds another sheet.
        click(left_root, 'workbookChooseTarget')
        menu = left_root.findChild(QObject, 'workbookTargetMenu')
        assert menu is not None and menu.property('ownerSheet') is first and menu.property('visible')
        left.bind(second)
        click_item(find(window.contentItem(), 'workbookTarget_' + targets[1].comparison_id))
        assert first.targetId == targets[1].comparison_id and second.targetId == targets[0].comparison_id
        assert selected == [targets[1].comparison_id], selected
        assert left.boundSheet is second and right.boundSheet is second

        # Scene destruction leaves controller identity, pin, and bound SheetPage intact.
        left_root.setParentItem(None)
        left_root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        assert left.boundSheet is second and left.targetPinned
        left_root = mount(left, 0)
        assert left_root.property('bridge') is left and left.boundSheet is second
        assert find(left_root, 'workbookPinTarget').property('checked')
        click(left_root, 'workbookPlot_line_symbol')
        assert events[-1] == (second.sheet_id, 'line_symbol', None)

        left.bind(first)
        click(left_root, 'workbookChooseTarget')
        menu = left_root.findChild(QObject, 'workbookTargetMenu')
        assert menu.property('ownerSheet') is first
        left.bind(second)
        first.dispose()
        before = list(selected)
        click_item(find(window.contentItem(), 'workbookTarget_' + targets[0].comparison_id))
        assert selected == before and first.targetId == targets[1].comparison_id

        # Disposal unbinds and disconnects the source without retiring another owner's SheetPage.
        left.dispose()
        assert left.boundSheet is None and not left.ready
        states = []
        left.stateChanged.connect(lambda: states.append(True))
        second.stateChanged.emit()
        assert not states
        click(right_root, 'workbookPlot_scatter')
        assert events[-1] == (second.sheet_id, 'scatter', None)
        assert len(events) == 5, events
        assert engine.rootContext().contextProperty('appBridge') is None
        assert all(not isinstance(child, QWidget) for child in owner.findChildren(QObject))
        for root in (left_root, right_root):
            root.setParentItem(None)
            root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        right.dispose()
        workbook.dispose()
        window.close()
        window.deleteLater()
        pump()
    print('QGuiApplication Workbook command controllers and two rows: PASS')


if __name__ == '__main__':
    if '--controller-probe' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        controller_probe()
    else:
        unittest.main()
