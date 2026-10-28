"""The actual production composition, exercised in a fresh widget-free process."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class QuickApplicationRootTests(unittest.TestCase):
    def test_actual_entry_exits_cleanly(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--entry'],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn('Actual run_qt.main: PASS; exit 0', result.stdout)

    def test_production_root_render_input_documents_and_shutdown(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--probe'],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
            capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn('Production Quick root: PASS; legacy widgets created: 0', result.stdout)


def probe():
    from tempfile import TemporaryDirectory
    from unittest.mock import patch
    import numpy as np
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtQuickWidgets import QQuickWidget
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget, QMdiArea
    from spectra_app.quick_root import QuickApplication
    from spectra_app.workbook_state import create_comparison, add_curve
    from spectra_app.formal_plot_controller import FormalPlotController
    from spectra_app.workbook_page import WorkbookController
    from spectra_app.xy_plot_controller import XYPlotController

    from tests.quick_application_helpers import isolated_settings
    from run_qt import configure_application_fonts
    app = QGuiApplication([])
    configure_application_fonts(app)
    app.setQuitOnLastWindowClosed(False)
    def forbidden(*args, **kwargs):
        raise AssertionError('Production constructed a legacy QWidget')
    with isolated_settings(), patch.object(QWidget, '__init__', forbidden), patch.object(QQuickWidget, '__init__', forbidden), patch.object(QMdiArea, '__init__', forbidden):
        host = QuickApplication()
        owner = host.controller
        assert type(app) is QGuiApplication and isinstance(host.window, QQuickWindow)
        assert isinstance(owner._fallback_plot_page, FormalPlotController)
        host.show()
        def pump():
            for _ in range(4):
                app.processEvents()
                QTest.qWait(15)
        pump()
        def control(name):
            def visit(item):
                if item.objectName() == name:
                    return item
                for child in item.childItems():
                    found = visit(child)
                    if found is not None:
                        return found
            result = visit(host.window.contentItem())
            assert result is not None, name
            return result
        task = control('task_1')
        point = task.mapToScene(QPointF(task.width()/2, task.height()/2)).toPoint()
        QTest.mouseClick(host.window, Qt.LeftButton, Qt.NoModifier, point)
        pump()
        assert owner.taskIndex == 1
        owner.request_task(0)
        with TemporaryDirectory() as temp:
            path = Path(temp) / '光谱.csv'
            np.savetxt(path, np.column_stack((np.arange(12), np.arange(12)**2)), delimiter=',')
            book = owner.import_xy_files([path])
            pump()
            workbook_key = ('workbook', book.workbook_id, '')
            workbook = owner.graph_documents.document_for(workbook_key)
            assert isinstance(workbook, WorkbookController)
            assert owner.graph_documents.content_item(workbook_key) is not None
            comparison = create_comparison(owner.workspace, 'Measured curve')
            add_curve(owner.workspace, comparison.comparison_id, book.sheet_ids[0], 0, 1)
            xy = owner.open_xy_comparison(comparison.comparison_id)
            pump()
            xy_key = ('xy', comparison.comparison_id, '')
            assert isinstance(xy, XYPlotController)
            assert owner.graph_documents.registry.active_key == xy_key
            assert owner.graph_documents.content_item(xy_key) is not None
            owner.request_task(2)
            pump()
            owner.open_workbook(book.workbook_id)
            pump()
            assert owner.graph_documents.registry.active_key == workbook_key
            assert owner.graph_documents.document_for(xy_key) is xy
            owner.open_xy_comparison(comparison.comparison_id)
            pump()
            assert owner.contextKind == 'xy'
            owner.request_task(1)
            pump()
            assert owner.taskIndex == 1
            owner.reveal_xy_inspector(xy, 2, 'interval')
            pump()
            assert owner.taskIndex == 2
            owner.reveal_xy_inspector(xy, 2, 'width_mm')
            pump()
            assert host.window.activeFocusItem().objectName() == 'xyField_width_mm'
            navigation = control('XYNavigationRoot')
            assert navigation.isVisible()
            pan = control('XYNavigation_pan')
            point = pan.mapToScene(QPointF(pan.width()/2, pan.height()/2)).toPoint()
            QTest.mouseClick(host.window, Qt.LeftButton, Qt.NoModifier, point)
            pump()
            assert xy.command_bridge.mode == 'pan/zoom'
            QTest.mouseClick(host.window, Qt.LeftButton, Qt.NoModifier, point)
            pump()
            assert xy.command_bridge.mode == ''
            owner.floatProperties(True)
            pump()
            floating = host.window.findChild(QQuickWindow, 'floatingPropertiesWindow')
            assert floating.isVisible() and floating.transientParent() is host.window
            owner.floatProperties(False)
            pump()
            owner._analysis_return_key = ('removed', 'source')
            assert owner.request_outer_view('analysis')
            owner._back_to_workspace()
            pump()
            assert owner.outerView == 'workspace'
            dat_path = Path(temp) / '测试.dat'
            dat_path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
            owner._load_dat_file_direct(dat_path)
            formal = owner.open_graph_document()
            pump()
            owner._render_plot_page()
            pump()
            formal_key = owner._document_key()
            assert isinstance(formal, FormalPlotController) and formal.rendered_page is not None
            assert owner.graph_documents.content_item(formal_key) is not None
            assert owner.request_outer_view('analysis')
            pump()
            assert owner.dat_canvas._scene is host.dat_scene
            owner._workbench_open(('heatmap', formal_key))
            pump()
            assert owner.workbenchActive
            assert not owner.processing.enabled and not owner.dat_properties.enabled
            assert not owner.plot_page.enabled
            pending = owner.processing.owner().pending
            owner.processing.setMode('energy_second_derivative')
            assert owner.processing.owner().pending is pending
            owner.rememberLinecutSizes(400, 145)
            owner._workbench_toggle_linecut(False)
            owner._workbench_toggle_linecut(True)
            assert owner._workbench_linecut_sizes == [400, 145]
            owner._back_to_workspace()
            pump()
            assert owner.outerView == 'workspace'
            report = host.show_batch_report('1 exported; 0 failed')
            pump()
            assert isinstance(report, QQuickWindow) and report.transientParent() is host.window
            report.close()
            outcomes = []
            owner._start_background_job(lambda context: (context.report('Real job progress'), 42)[1], outcomes.append, 'Root job probe', owner='processing')
            for _ in range(50):
                pump()
                if not owner._background_jobs.busy:
                    break
            assert outcomes == [42] and not owner._background_jobs.busy
            image = host.window.grabWindow()
            assert not image.isNull()
            capture = os.environ.get('SPECTRA_QUICK_CAPTURE')
            if capture:
                assert image.save(capture)
            # Exercise the actual guarded QWindow close twice: the first queues
            # approval and the second consumes the revision-bound approval.
            with patch.object(owner, '_confirm_project_discard_async', lambda callback: callback()):
                host.window.close()
                pump()
                assert owner._closing and not host.window.isVisible()
            host.dispose()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            pump()
        print('Production Quick root: PASS; legacy widgets created: 0', flush=True)


if __name__ == '__main__':
    if '--probe' in sys.argv or '--entry' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        if '--probe' in sys.argv:
            probe()
        else:
            from unittest.mock import patch
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QWidget, QMdiArea
            from PySide6.QtQuickWidgets import QQuickWidget
            import run_qt
            from tests.quick_application_helpers import isolated_settings
            original = run_qt.QuickApplication.show
            def show_and_close(host):
                original(host)
                QTimer.singleShot(200, host.controller.close)
            def forbidden(*args, **kwargs):
                raise AssertionError('Production entry created a QWidget')
            sys.argv = [str(Path(run_qt.__file__))]
            with isolated_settings(), patch.object(QWidget, '__init__', forbidden), patch.object(QQuickWidget, '__init__', forbidden), patch.object(QMdiArea, '__init__', forbidden), patch.object(run_qt.QuickApplication, 'show', show_and_close):
                assert run_qt.main() == 0
            print('Actual run_qt.main: PASS; exit 0', flush=True)
    else:
        unittest.main()
