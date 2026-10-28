"""Real local-QML source controls under the weaker QGuiApplication boundary."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


from tests.quick_migration_case import QuickMigrationCase


class SourceNavigationQmlTests(QuickMigrationCase):
    def setUp(self):
        if self._testMethodName == 'test_production_mount_removes_native_controls_and_invokes_canonical_services':
            super().setUp()
            self.addCleanup(lambda: self.assertEqual(self.quick_errors, []))

    def test_two_local_scenes_without_widget_construction(self):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--scene-probe'],
            cwd=Path(__file__).resolve().parents[1], env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn('QGuiApplication SourceNavigation two scenes: PASS', result.stdout)

    def test_production_mount_removes_native_controls_and_invokes_canonical_services(self):
        from unittest.mock import patch
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQuick import QQuickItem
        from tests.settings_helpers import wait_for_background_jobs
        from tests import source_navigation_helpers as sn
        directory = self.folder
        paths = [directory / name for name in ('first.dat', 'second.dat')]
        for path in paths:
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
        window = self.controller
        for name in ('import_dats_btn', 'imported_dataset_list', 'imported_dataset_search',
                     'imported_dataset_rows_spin', 'dat_import_status_label', 'graph_combo',
                     'graph_actions_btn', 'new_graph_action', 'rename_graph_action', 'remove_graph_action',
                     'root_edit', 'browse_btn', 'folder_combo', 'file_list', 'load_btn',
                     '_source_context_label', '_source_navigation_content'):
            self.assertFalse(hasattr(window, name), name)
        hosts = [host for host in window.root_window.findChildren(QQuickItem)
                 if host.objectName() == 'sourceNavigation']
        self.assertEqual(len(hosts), 1)
        source = hosts[0]
        self.assertIs(source.property('bridge'), window.source_navigation)
        self.assertTrue(window.request_task(0))
        self.assertTrue(window.request_outer_view('analysis'))
        window._set_dat_details_requested(True)
        sn.pump()
        self.assertTrue(source.isVisible())
        self.assertFalse(source.property('advancedExpanded'))
        sn.click(window, 'sourceAdvancedDisclosure', menu=True)
        self.assertTrue(sn.control(window, 'sourceDatasets').isVisible())
        with patch('spectra_app.main_window.FilePicker.getOpenFileNames', return_value=([str(paths[0])], '')):
            sn.click(window, 'sourceImport')
            wait_for_background_jobs(window)
        first = window.workspace.dataset_for_path(paths[0])
        self.assertIsNotNone(first)
        self.assertEqual(len(window.source_navigation.datasetRows), 1)
        window._load_dat_file_direct(paths[1])
        # Import publishes a Workbook; reopen its DAT graph to test Data controls.
        window.open_graph_document()
        self.assertEqual(window.contextKind, 'dat')
        self.assertNotEqual(window.workspace.active_dataset_id, first.dataset_id)
        with patch.object(window, 'activate_dataset', wraps=window.activate_dataset) as activated:
            sn.click(window, 'sourceDataset_0')
        activated.assert_called_once_with(first.dataset_id)
        self.assertIs(window.workspace.active, first)
        sn.click(window, 'sourceDataset_1', button=Qt.RightButton)
        sn.click(window, 'sourceCopy', menu=True)
        self.assertEqual(QGuiApplication.clipboard().text(), str(paths[1]))
        self.assertIs(window.workspace.active, first)
        sn.click(window, 'sourceGraphActions')
        with patch('spectra_app.main_window.prompt_text', return_value=('View detail', True)):
            sn.click(window, 'sourceNewGraph', menu=True)
        self.assertEqual(first.active_graph.name, 'View detail')
        self.assertTrue(first.active_graph.published)
        window.source_navigation.set_import_available(False, reason='external')
        self.assertFalse(window._graph_command_actions['import'].isEnabled())
        window._set_dat_import_pending(True)
        window._set_dat_import_pending(False)
        self.assertFalse(window.source_navigation.importEnabled)
        self.assertFalse(window._graph_command_actions['import_options'].isEnabled())
        window.source_navigation.set_import_available(True, reason='external')
        self.assertTrue(window._graph_command_actions['import'].isEnabled())


def scene_probe():
    from unittest.mock import patch
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QPoint, QPointF, Qt, QTimer, QUrl
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from spectra_app.quick_ui import QuickPanel
    from spectra_app.dataset_state import DatasetSession
    from tests.test_source_navigation_controller import SourceOperations

    app = QGuiApplication([])
    assert type(app) is QGuiApplication and not isinstance(app, QApplication)

    def pump():
        loop = QEventLoop()
        QTimer.singleShot(25, loop.quit)
        loop.exec()

    def find(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            result = find(child, name)
            if result is not None:
                return result
        return None

    with patch.object(QWidget, '__init__', side_effect=AssertionError('QWidget constructed')), \
            patch.object(QuickPanel, '__init__', side_effect=AssertionError('QuickPanel constructed')):
        left, right = SourceOperations(), SourceOperations()
        engine = QQmlEngine()
        window = QQuickWindow()
        window.resize(720, 1000)
        window.show()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'spectra_app/qml/SourceNavigation.qml')))
        assert not component.isError(), [error.toString() for error in component.errors()]
        roots = []
        for index, operations in enumerate((left, right)):
            root = component.createWithInitialProperties({'bridge': operations.controller})
            assert root is not None, [error.toString() for error in component.errors()]
            root.setParentItem(window.contentItem())
            root.setPosition(QPointF(index * 360, 0))
            root.setWidth(350)
            root.setProperty('advancedExpanded', True)
            root.setProperty('inventoryExpanded', True)
            roots.append(root)
        pump()
        first, second = roots
        assert engine.rootContext().contextProperty('appBridge') is None
        assert all(not isinstance(child, QWidget) for child in engine.findChildren(QObject))
        assert all(not isinstance(child, QWidget) for operations in (left, right)
                   for child in operations.controller.findChildren(QObject))

        def click(root, name, button=Qt.LeftButton):
            item = find(root, name)
            assert item is not None and item.isVisible(), name
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
            QTest.mouseClick(window, button, pos=QPoint(round(point.x()), round(point.y())))
            pump()
            return item

        search = click(first, 'sourceSearch')
        for letter in 'second':
            QTest.keyClick(window, getattr(Qt, 'Key_' + letter.upper()))
        pump()
        assert left.controller.searchText == 'second' and right.controller.searchText == ''
        assert left.workspace.active_dataset_id == 'first'
        datasets = find(first, 'sourceDatasets')
        datasets.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Down)
        pump()
        assert left.workspace.active_dataset_id == 'second'
        assert right.workspace.active_dataset_id == 'first'
        search.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(window, Qt.Key_Backspace)
        pump()
        click(first, 'sourceDataset_2', Qt.RightButton)
        assert left.workspace.active_dataset_id == 'second'
        copy = find(window.contentItem(), 'sourceCopy')
        assert copy is not None
        point = copy.mapToScene(QPointF(copy.width()/2, copy.height()/2))
        QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
        pump()
        assert left.calls[-1] == ('copy', 'third'), left.calls
        click(first, 'sourceDataset_0', Qt.RightButton)
        left.workspace = SourceOperations().workspace
        copy = find(window.contentItem(), 'sourceCopy')
        point = copy.mapToScene(QPointF(copy.width()/2, copy.height()/2))
        before = list(left.calls)
        QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
        pump()
        assert left.calls == before, left.calls
        click(first, 'sourceGraphActions')
        rename = find(window.contentItem(), 'sourceRenameGraph')
        assert rename is not None
        point = rename.mapToScene(QPointF(rename.width()/2, rename.height()/2))
        QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
        pump()
        assert left.calls[-1] == ('rename', 'first', 'default'), left.calls
        detail = left.workspace.active.create_graph('Detail')
        left.workspace.active.active_graph_id = 'default'
        left.controller.sync()
        pump()
        graphs = find(first, 'sourceGraphs')
        graphs.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Down)
        pump()
        assert left.calls[-1] == ('graph', detail.graph_id), left.calls
        root_path = Path('inventory').resolve()
        ticket = left.controller.begin_root(root_path)
        left.controller.apply_folders(ticket, [root_path / 'unused', root_path / 'original'])
        pump()
        folders = find(first, 'sourceFolders')
        folders.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Down)
        pump()
        assert left.controller.selectedFolder == str(root_path / 'original'), left.calls
        left.controller.apply_files(left.controller.inventory_ticket(), ['sample.dat', 'second.dat'])
        # A different owner has the same basename; Load retains the discovery path.
        left.activate_dataset('third')
        left.controller.sync()
        pump()
        click(first, 'sourceLoad')
        assert left.calls[-1] == ('load', [root_path / 'original/sample.dat'])
        files = find(first, 'sourceFiles')
        files.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Down)
        pump()
        click(first, 'sourceLoad')
        assert left.calls[-1] == ('load', [root_path / 'original/second.dat'])
        files.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Up)
        pump()
        click(first, 'sourceLoad')
        assert left.calls[-1] == ('load', [root_path / 'original/sample.dat'])
        left.controller.set_import_available(False, reason='closing')
        left.controller.set_import_pending(True)
        pump()
        assert not find(first, 'sourceImport').isEnabled()
        assert not find(first, 'sourceDatasets').isEnabled()
        left.controller.set_import_pending(False)
        pump()
        assert find(first, 'sourceDatasets').isEnabled() and not find(first, 'sourceImport').isEnabled()
        left.controller.set_import_available(True, reason='closing')
        pump()
        click(first, 'sourceImport')
        assert left.calls[-1] == ('import',)
        # A real keyboard selection beyond the four-row viewport follows its row.
        for index in range(12):
            identity = 'extra' + str(index)
            left.workspace.register(DatasetSession(dataset_id=identity,
                source_path=Path(identity, 'sample.dat').resolve()))
        left.workspace.active_dataset_id = 'first'
        left.controller.setVisibleRows(4)
        left.controller.sync()
        pump()
        datasets.forceActiveFocus()
        for _ in range(8):
            QTest.keyClick(window, Qt.Key_Down)
            pump()
        assert datasets.property('currentIndex') == 8, datasets.property('currentIndex')
        assert datasets.property('contentY') > 0, datasets.property('contentY')
        selected = datasets.property('currentItem')
        top = selected.y() - datasets.property('contentY')
        assert top >= 0 and top + selected.height() <= datasets.height() + 1
        old_height = selected.height()
        capture_dir = os.environ.get('SOURCE_NAVIGATION_CAPTURE_DIR')
        if capture_dir:
            first.setWidth(260)
            pump()
            assert window.grabWindow().save(str(Path(capture_dir) / 'source-compact-normal.png'))
        first.setProperty('controlFont', QFont('Arial', 16))
        pump()
        assert first.property('datasetRowHeight') > old_height
        assert datasets.height() == 4 * first.property('datasetRowHeight')
        selected = datasets.property('currentItem')
        top = selected.y() - datasets.property('contentY')
        assert top >= -1 and top + selected.height() <= datasets.height() + 1
        spinner = find(first, 'sourceVisibleRows')
        assert spinner.width() >= spinner.property('implicitWidth')
        if capture_dir:
            assert window.grabWindow().save(str(Path(capture_dir) / 'source-compact-large-font.png'))
        for root in roots:
            root.setParentItem(None)
            root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        window.close()
        window.deleteLater()
        pump()
    print('QGuiApplication SourceNavigation two scenes: PASS')


if __name__ == '__main__':
    if '--scene-probe' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        scene_probe()
    else:
        unittest.main()
