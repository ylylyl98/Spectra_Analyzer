"""Workbook's production controller and component-local scene boundary."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class WorkbookControllerTests(unittest.TestCase):
    def test_controller_and_two_scenes_under_qguiapplication_without_widgets(self):
        # The normal Quick tests own a QApplication singleton. A fresh process
        # proves this boundary with the weaker, genuinely widget-free app type.
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()),
                                 '--controller-probe'], cwd=Path(__file__).resolve().parents[1],
                                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('QGuiApplication Workbook controller and two scenes: PASS', result.stdout)


def controller_probe():
    import numpy as np
    from unittest.mock import patch
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QPoint, QPointF, Qt, QTimer, QUrl
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from spectra_app.dataset_state import Workspace
    from spectra_app.quick_ui import QuickPanel
    from spectra_app.workbook_page import WorkbookController, SheetPage
    from spectra_app.workbook_state import Workbook, Sheet

    app = QGuiApplication([])
    assert type(app) is QGuiApplication and not isinstance(app, QApplication)

    def pump():
        loop = QEventLoop()
        QTimer.singleShot(30, loop.quit)
        loop.exec()

    def workspace():
        result = Workspace()
        book = Workbook(workbook_id='book', sheet_ids=['first', 'second'])
        result.workbooks[book.workbook_id] = book
        for identity in book.sheet_ids:
            result.sheets[identity] = Sheet(sheet_id=identity, name=identity,
                data=np.arange(6000., dtype=float).reshape(300, 20),
                column_names=[f'Column {i}' for i in range(20)])
        return result

    def find(root, name):
        if root.objectName() == name:
            return root
        for child in root.childItems():
            match = find(child, name)
            if match is not None:
                return match
        return None

    with patch.object(QWidget, '__init__', side_effect=AssertionError('QWidget constructed')), \
         patch.object(QuickPanel, '__init__', side_effect=AssertionError('QuickPanel constructed')):
        owner = QObject()
        left = WorkbookController(workspace(), 'book', owner)
        right = WorkbookController(workspace(), 'book', owner)
        assert left.parent() is owner and isinstance(left, QObject) and not isinstance(left, QWidget)
        first = left.current_sheet
        assert isinstance(first, SheetPage) and first.parent() is left
        assert all(not isinstance(child, QWidget) for child in owner.findChildren(QObject))
        first.beginBody(8, 2, 0)
        first.dragBody(10, 3)
        first.rememberScroll(300., 400.)
        first.resizeColumn(2, 87.)
        saved = first.capture_view_state()
        font = QFont(QGuiApplication.font())
        font.setPointSize(font.pointSize() + 4)
        left.set_font(font)
        assert first._font == font and first.columnWidth(2) == 87
        assert first.editMetadata(1, 'long_name')
        first.setEditorText('two\nlines')
        assert not left.select_sheet('second') and left.current_sheet is first
        try:
            left.finish_editor()
        except ValueError:
            pass
        else:
            raise AssertionError('invalid draft did not block save')
        assert first.editorText == 'two\nlines' and left.has_pending_project_edits()
        first.setEditorText('Emission')
        order = []
        left.sheetAboutToChange.connect(lambda: order.append(('before', left.current_sheet.sheet_id)))
        left.stateChanged.connect(lambda: order.append(('state', left.current_sheet.sheet_id)))
        left.sheetActivated.connect(lambda: order.append(('activated', left.current_sheet.sheet_id)))
        assert left.select_sheet('second')
        assert order == [('before', 'first'), ('state', 'second'), ('activated', 'second')]
        assert first.model.metadata[1]['long_name'] == 'Emission'
        # System save validation visits inactive sheets even when user input is blocked.
        first.editMetadata(1, 'units')
        first.setEditorText('eV')
        left.set_input_guard(lambda: False)
        left.finish_editor()
        assert first.model.metadata[1]['units'] == 'eV' and not left.has_pending_project_edits()
        assert not left.select_sheet('first')
        left.set_input_guard(None)
        assert left.select_sheet('first') and first.capture_view_state() == saved
        first.beginBody(0, 0, 0)
        first.model.values[0][0] = 1.2345678901234567
        assert first.copy_selection() == format(1.2345678901234567, '.17g')
        assert QGuiApplication.clipboard().text() == first.copy_selection()

        engine = QQmlEngine()
        assert engine.rootContext().contextProperty('appBridge') is None
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(
            Path(__file__).resolve().parents[1] / 'spectra_app' / 'qml' / 'Workbook.qml')))
        assert component.isReady(), [error.toString() for error in component.errors()]
        window = QQuickWindow()
        window.resize(1600, 600)
        window.show()

        def mount(controller, x):
            root = component.createWithInitialProperties({'bridge': controller})
            assert root is not None, [error.toString() for error in component.errors()]
            assert isinstance(root, QQuickItem) and root.property('bridge') is controller
            root.setParentItem(window.contentItem())
            root.setPosition(QPointF(x, 0))
            root.setWidth(800)
            root.setHeight(600)
            pump()
            assert root.property('sheet') is controller.current_sheet
            return root

        def click(root, name):
            control = find(root, name)
            assert control is not None, name
            point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2))
            QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
            pump()

        left_root, right_root = mount(left, 0), mount(right, 800)
        assert find(left_root, 'workbookSheet_second') is not find(right_root, 'workbookSheet_second')
        first.beginBody(8, 2, 0)
        first.dragBody(10, 3)
        first.resizeColumn(2, 87.)
        body = find(left_root, 'sheetCells')
        body.setProperty('contentX', 200.)
        body.setProperty('contentY', 300.)
        pump()
        retained = first.capture_view_state()
        right_before = right.capture_view_state()
        click(left_root, 'workbookSheet_second')
        assert left.currentSheetId == 'second' and right.currentSheetId == 'first'
        assert right.capture_view_state() == right_before
        click(left_root, 'workbookSheet_first')
        assert first.capture_view_state() == retained
        assert first.editMetadata(1, 'long_name')
        first.setEditorText('Retained draft')
        pump()
        retained = first.capture_view_state()
        # Leaving the existing editor commits valid input, including when its
        # scene loses focus during detachment. Scene lifetime must not retire it.
        left_root.setParentItem(None)
        left_root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        assert first.model.metadata[1]['long_name'] == 'Retained draft' and not left._retired
        left_root = mount(left, 0)
        assert left.current_sheet is first and first.capture_view_state() == retained, (first.capture_view_state(), retained)
        left.finish_editor()
        assert first.model.metadata[1]['long_name'] == 'Retained draft'
        assert right.current_sheet.model.metadata[1]['long_name'] != 'Retained draft'
        assert first.editMetadata(1, 'long_name')
        first.setEditorText('invalid\ndraft')
        left_root.setParentItem(None)
        left_root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        left_root = mount(left, 0)
        assert first.editorText == 'invalid\ndraft' and not left._retired
        editor = find(left_root, 'sheetMetadataEditor')
        assert editor.isVisible() and editor.property('text') == 'invalid\ndraft', editor.property('text')
        assert not editor.hasActiveFocus(), 'scene initialization focused the retained editor'
        assert first.model.metadata[1]['long_name'] == 'Retained draft'
        assert not left.select_sheet('second')
        try:
            left.finish_editor()
        except ValueError:
            pass
        else:
            raise AssertionError('remounted invalid draft did not block save')
        assert first.editorText == 'invalid\ndraft'
        first.cancelEditor()
        plots = []
        left.quickPlotRequested.connect(lambda *args: plots.append(args))
        mapping = (first.xColumn, first.yColumns)
        left.dispose()
        first.setX(2)
        first.setY(3, True)
        first.plot('line')
        click(left_root, 'workbookSheet_second')
        assert left.current_sheet is first and (first.xColumn, first.yColumns) == mapping and not plots
        click(right_root, 'workbookSheet_second')
        assert right.currentSheetId == 'second' and not right._retired
        assert engine.rootContext().contextProperty('appBridge') is None
        for root in (left_root, right_root):
            root.setParentItem(None)
            root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        right.dispose()
        window.close()
        window.deleteLater()
        pump()
    print('QGuiApplication Workbook controller and two scenes: PASS')


if __name__ == '__main__':
    if '--controller-probe' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        controller_probe()
    else:
        unittest.main()
