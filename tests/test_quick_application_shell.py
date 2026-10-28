"""Shell behavior through real, isolated production Quick windows."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


CASES = ('shortcut_scopes', 'properties_lifetime', 'analysis_scroll', 'layout_recent_menu')


class QuickApplicationShellTests(unittest.TestCase):
    def run_case(self, name):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--case', name],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
            timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn(name + ': PASS after Quick teardown', result.stdout)


def _wrapper(name):
    def test(self):
        self.run_case(name)
    return test


for _name in CASES:
    setattr(QuickApplicationShellTests, 'test_' + _name, _wrapper(_name))


class ActualShell(unittest.TestCase):
    def setUp(self):
        from tests.quick_application_helpers import application
        self.fixture = application()
        self.host, self.folder, self.errors = self.fixture.__enter__()
        self.w = self.host.controller
        from PySide6.QtCore import QObject
        from PySide6.QtTest import QSignalSpy
        import shiboken6
        self.floating_destroyed = QSignalSpy(self.host._floating_properties.destroyed)
        self.floating_owner_address = shiboken6.getCppPointer(QObject.parent(self.host._floating_properties))[0]
        self.addCleanup(self.fixture.__exit__, None, None, None)

    def assert_floating_lifetime(self):
        from PySide6.QtCore import QObject
        import shiboken6
        self.assertTrue(shiboken6.isValid(self.host._floating_properties))
        self.assertEqual(self.floating_destroyed.count(), 0)
        self.assertEqual(shiboken6.getCppPointer(QObject.parent(self.host._floating_properties))[0],
                         self.floating_owner_address)

    def find(self, name):
        def visit(candidate):
            if candidate.objectName() == name:
                return candidate
            for child in candidate.childItems():
                found = visit(child)
                if found is not None:
                    return found
        for window in (self.host.window, self.host._floating_properties):
            found = visit(window.contentItem())
            if found is not None:
                return found
        self.fail('Missing actual Quick item ' + name)

    def click(self, target):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from tests.quick_application_helpers import pump
        window = self.window_for(target)
        point = target.mapToScene(QPointF(target.width()/2, target.height()/2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        pump()

    @staticmethod
    def window_for(target):
        from PySide6.QtCore import QObject
        window = target.window()
        # Repair Shiboken's return-parent heuristic before a temporary item
        # wrapper can invalidate its window wrapper. The C++ parent is unchanged.
        QObject.setParent(window, QObject.parent(window))
        return window

    def xy(self):
        from spectra_app.workbook_state import add_curve, create_comparison
        from tests.quick_application_helpers import pump
        source = self.folder / '测量.csv'
        source.write_text('X,Y\n0,10\n1,20\n2,15\n', encoding='utf-8')
        book = self.w.import_xy_files([source])
        graph = create_comparison(self.w.workspace, 'Measured graph')
        curve = add_curve(self.w.workspace, graph.comparison_id, book.sheet_ids[0], 0, 1)
        page = self.w.open_xy_comparison(graph.comparison_id)
        self.w.request_task(2)
        self.w.selectInspectorSection(2)
        pump()
        return page, graph, curve

    def key(self, key, modifiers=None, window=None):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from tests.quick_application_helpers import pump
        QTest.keyClick(window or self.host.window, key,
                       Qt.NoModifier if modifiers is None else modifiers)
        pump()

    def case_shortcut_scopes(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtTest import QSignalSpy
        from tests.quick_application_helpers import pump
        self.xy()
        clipboard = QGuiApplication.clipboard()
        action = self.w._graph_command_actions['copy_graph']
        self.assertTrue(action.isEnabled())
        for name, floating in (('projectSearch', False), ('xyField_name', False),
                               ('xyField_name', True)):
            self.host.moveProperties(floating)
            pump()
            field = self.find(name)
            field.forceActiveFocus()
            clipboard.setText('before copy')
            self.key(Qt.Key_J, Qt.ControlModifier | Qt.AltModifier, self.window_for(field))
            mime = clipboard.mimeData()
            self.assertTrue(mime.hasFormat('image/svg+xml'))
            self.assertIn(b'<svg', bytes(mime.data('image/svg+xml')))
        self.host.moveProperties(False)
        pump()
        field = self.find('xyField_name')
        field.forceActiveFocus()
        before = field.property('text')
        undo = QSignalSpy(self.w._graph_command_actions['undo'].triggered)
        redo = QSignalSpy(self.w._graph_command_actions['redo'].triggered)
        self.key(Qt.Key_End)
        for key in (Qt.Key_A, Qt.Key_B, Qt.Key_C):
            self.key(key)
        self.assertEqual(field.property('text'), before + 'abc')
        self.key(Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(field.property('text'), before)
        self.key(Qt.Key_Y, Qt.ControlModifier)
        self.assertEqual(field.property('text'), before + 'abc')
        self.assertEqual((undo.count(), redo.count()), (0, 0))
        self.key(Qt.Key_Escape)
        rescale = QSignalSpy(self.w._graph_command_actions['rescale'].triggered)
        self.find('projectSearch').forceActiveFocus()
        self.key(Qt.Key_R, Qt.ControlModifier)
        self.assertEqual(rescale.count(), 0)
        self.find('XYCanvasRoot').forceActiveFocus()
        self.key(Qt.Key_R, Qt.ControlModifier)
        self.assertEqual(rescale.count(), 1)
        field = self.find('xyField_name')
        field.forceActiveFocus()
        clipboard.setText('disabled copy')
        triggered = QSignalSpy(action.triggered)
        from threading import Event
        from tests.quick_application_helpers import wait_until
        release = Event()
        self.w._start_background_job(lambda context: release.wait(10), lambda result: None,
                                     'Clipboard disabled probe', owner='processing')
        try:
            self.assertFalse(action.isEnabled())
            self.key(Qt.Key_J, Qt.ControlModifier | Qt.AltModifier, self.window_for(field))
            self.assertEqual(triggered.count(), 0)
            self.assertEqual(clipboard.text(), 'disabled copy')
            self.assertFalse(action.isEnabled())
        finally:
            release.set()
            wait_until(lambda: not self.w._background_jobs.busy)
        self.key(Qt.Key_Escape)
        self.assertFalse(self.errors)
        self.assert_floating_lifetime()

    def case_properties_lifetime(self):
        from PySide6.QtCore import Qt
        from tests.quick_application_helpers import pump
        page, graph, curve = self.xy()
        self.w.selectInspectorSection(0)
        pump()
        editor = self.find('xyField_width')
        editor.forceActiveFocus()
        original, width = editor.property('text'), curve.width
        self.key(Qt.Key_A, Qt.ControlModifier)
        self.key(Qt.Key_Minus)
        self.assertEqual(editor.property('text'), '-')
        self.assertEqual(page.inspector_bridge.values['width'], '-')
        properties = self.find('applicationProperties')
        self.click(self.find('floatPropertiesButton'))
        floating = self.host._floating_properties
        from PySide6.QtTest import QSignalSpy
        destroyed = QSignalSpy(floating.destroyed)
        from PySide6.QtCore import QObject
        floating_owner = QObject.parent(floating)
        self.assertIsNotNone(floating_owner)
        self.assertTrue(floating.isVisible())
        self.assertIs(self.find('applicationProperties'), properties)
        self.assertIs(self.find('xyField_width'), editor)
        self.assertIs(floating.activeFocusItem(), editor)
        self.assertEqual(editor.property('text'), '-')
        self.assertEqual(curve.width, width)
        self.key(Qt.Key_Z, Qt.ControlModifier, floating)
        self.assertEqual(editor.property('text'), original)
        self.key(Qt.Key_Y, Qt.ControlModifier, floating)
        self.assertEqual(editor.property('text'), '-')
        self.assertEqual(page.inspector_bridge.values['width'], '-')
        self.key(Qt.Key_A, Qt.ControlModifier, floating)
        self.key(Qt.Key_2, window=floating)
        self.key(Qt.Key_Return, window=floating)
        self.assertIs(QObject.parent(floating), floating_owner)
        self.assertEqual(graph.curves[curve.curve_id].width, 2.)
        self.key(Qt.Key_A, Qt.ControlModifier, floating)
        self.key(Qt.Key_Minus, window=floating)
        self.click(self.find('dockPropertiesButton'))
        self.assertFalse(floating.isVisible())
        self.assertEqual(destroyed.count(), 0)
        self.assertIs(self.find('xyField_width'), editor)
        self.assertIs(self.host.window.activeFocusItem(), editor)
        self.assertEqual(editor.property('text'), '-')
        self.assertEqual(page.inspector_bridge.values['width'], '-')
        self.assertEqual(graph.curves[curve.curve_id].width, 2.)
        self.key(Qt.Key_A, Qt.ControlModifier)
        self.key(Qt.Key_3)
        self.key(Qt.Key_Return)
        self.assertIs(QObject.parent(floating), floating_owner)
        self.assertEqual(graph.curves[curve.curve_id].width, 3.)
        self.click(self.find('floatPropertiesButton'))
        floating.close()
        pump()
        self.assertFalse(floating.isVisible())
        self.assertTrue(self.w.propertiesFloating)
        self.assertFalse(self.w.propertiesVisible)
        self.assertFalse(self.w.dat_details_action.isChecked())
        bar = self.host.window.property('menuBar')
        view_entry = next(candidate for candidate in bar.property('contentItem').childItems()
                          if candidate.property('text') == 'View')
        self.click(view_entry)
        properties_action = next(candidate for candidate in self.host.window.findChildren(QObject)
            if 'MenuItem' in candidate.metaObject().className() and candidate.property('text') == 'Properties')
        self.click(properties_action)
        self.assertTrue(floating.isVisible())
        self.assertTrue(self.w.propertiesFloating)
        self.assertIs(self.find('xyField_width'), editor)
        self.click(editor)
        self.key(Qt.Key_A, Qt.ControlModifier, floating)
        self.key(Qt.Key_4, window=floating)
        self.key(Qt.Key_Return, window=floating)
        self.assertEqual(graph.curves[curve.curve_id].width, 4.)
        self.host.moveProperties(False)
        self.host.moveProperties(True)
        self.host.moveProperties(False)
        pump()
        self.assertFalse(self.w.propertiesFloating)
        self.assertFalse(floating.isVisible())
        self.assertEqual(destroyed.count(), 0)
        self.assertIs(QObject.parent(floating), floating_owner)
        self.assertIs(self.find('xyField_width'), editor)
        self.assertEqual(graph.curves[curve.curve_id].width, 4.)
        self.assertFalse(self.errors)
        self.assert_floating_lifetime()

    def case_analysis_scroll(self):
        from PySide6.QtCore import QCoreApplication, QElapsedTimer, QObject, QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtTest import QTest
        from tests.quick_application_helpers import pump
        self.xy()
        self.host.window.resize(900, 620)
        self.w.selectInspectorSection(1)
        pump()
        wheel_clock = QElapsedTimer()
        wheel_clock.start()
        for floating in (False, True):
            self.host.moveProperties(floating)
            if floating:
                self.host._floating_properties.resize(300, 240)
            pump()
            scroll = self.find('xyInspectorAnalysisScroll')
            viewport = scroll.property('contentItem')
            self.assertGreater(viewport.property('contentHeight'), viewport.height())
            bottom = self.find('xy-analysis-graph')
            self.assertGreater(bottom.mapToItem(scroll, QPointF(0, bottom.height())).y(), scroll.height())
            point = scroll.mapToScene(QPointF(4, scroll.height()/2))
            window = self.window_for(scroll)
            for _ in range(24):
                event = QWheelEvent(point, QPointF(window.mapToGlobal(point.toPoint())),
                    QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
                # Flickable measures wheel velocity from input timestamps. A
                # stream of default zero timestamps does not model real input.
                event.setTimestamp(wheel_clock.elapsed() + 1)
                QCoreApplication.sendEvent(window, event)
                pump(10)
            pump(300)
            self.assertGreater(viewport.property('contentY'), 0)
            scrollbar = next(candidate for candidate in scroll.findChildren(QObject)
                if 'ScrollBar' in candidate.metaObject().className()
                and candidate.property('orientation') == Qt.Vertical)
            end = scrollbar.mapToScene(QPointF(scrollbar.width()/2, scrollbar.height() - 2)).toPoint()
            QTest.mouseMove(window, end)
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, end)
            pump(300)
            point = bottom.mapToItem(scroll, QPointF(0, bottom.height()))
            self.assertLessEqual(point.y(), scroll.height() + 1)
            self.assertGreaterEqual(bottom.mapToItem(scroll, QPointF(0, 0)).y(), -1)
        self.assertFalse(self.errors)
        self.assert_floating_lifetime()

    def case_layout_recent_menu(self):
        from unittest.mock import patch
        from PySide6.QtCore import QObject, QPointF, QRect, Qt
        from PySide6.QtTest import QTest
        from spectra_app.quick_root import QuickApplication
        from tests.quick_application_helpers import pump, wait_until
        project = self.find('projectNavigator')
        before = project.width()
        point = project.mapToScene(QPointF(project.width() + 2, project.height()/2))
        QTest.mousePress(self.host.window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        QTest.mouseMove(self.host.window, (point + QPointF(52, 0)).toPoint(), 30)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, Qt.NoModifier, (point + QPointF(52, 0)).toPoint())
        pump()
        self.assertGreater(project.width(), before + 20)
        stored_project = self.w._annotation_settings.value('quick_shell/project_width', type=int)
        self.assertEqual(stored_project, round(project.width()))
        properties = self.find('dockedProperties')
        before = properties.width()
        point = properties.mapToScene(QPointF(-2, properties.height()/2))
        QTest.mousePress(self.host.window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        QTest.mouseMove(self.host.window, (point - QPointF(41, 0)).toPoint(), 30)
        QTest.mouseRelease(self.host.window, Qt.LeftButton, Qt.NoModifier, (point - QPointF(41, 0)).toPoint())
        pump()
        self.assertGreater(properties.width(), before + 20)
        stored_properties = self.w._annotation_settings.value('quick_shell/properties_width', type=int)
        self.assertEqual(stored_properties, round(properties.width()))
        self.w.floatProperties(True)
        pump()
        floating = self.host._floating_properties
        geometry = QRect(75, 85, 420, 510)
        floating.setGeometry(geometry)
        pump()
        restored = QuickApplication()
        try:
            restored.show()
            pump()
            self.assertEqual(restored.projectPanelWidth, stored_project)
            self.assertEqual(restored.propertiesPanelWidth, stored_properties)
            self.assertTrue(restored.controller.propertiesFloating)
            self.assertEqual(restored._floating_properties.geometry(), geometry)
            restored.controller.floatProperties(False)
            pump()
            dock = restored.window.findChild(QObject, 'dockedProperties')
            self.assertEqual(round(dock.width()), stored_properties)
        finally:
            with patch.object(restored.controller, '_confirm_project_discard_async', lambda callback: callback()):
                restored.window.close()
                wait_until(lambda: restored.controller._closing)
            restored.dispose()
            restored.deleteLater()
            pump()
        self.w._remember_recent_project(self.folder / 'first.spectra')
        self.w._remember_recent_project(self.folder / 'second.spectra')
        pump()
        menu = self.host.window.findChild(QObject, 'recentProjectsMenu')
        self.assertEqual(menu.property('count'), 4)
        # Open the real menu from its menu-bar entry, then activate its clear row.
        bar = self.host.window.property('menuBar')
        entries = bar.property('contentItem').childItems()
        entry = next(candidate for candidate in entries if candidate.property('text') == 'Recent projects')
        self.click(entry)
        self.assertTrue(menu.property('visible'))
        self.click(self.find('clearRecentProjects'))
        self.assertEqual(self.w.recentProjects, [])
        self.assertEqual(menu.property('count'), 2)
        self.assertFalse(self.errors)
        self.assert_floating_lifetime()


if __name__ == '__main__':
    if '--case' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        name = sys.argv[sys.argv.index('--case') + 1]
        suite = unittest.TestSuite([ActualShell('case_' + name)])
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
        if result.wasSuccessful():
            print(name + ': PASS after Quick teardown', flush=True)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main()
