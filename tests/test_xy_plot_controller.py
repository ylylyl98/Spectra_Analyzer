"""Isolated production XY controller/scene proof under QGuiApplication only."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class XYPlotControllerTests(unittest.TestCase):
    def test_qgui_production_controllers_and_real_scene_lifetimes(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()),
                                 '--controller-probe'], cwd=Path(__file__).resolve().parents[1],
                                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('QGuiApplication production XY controllers and scenes: PASS', result.stdout)


def controller_probe():
    from unittest.mock import patch
    import numpy as np
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QPoint, QPointF, Qt, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication, QAction
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from spectra_app.dataset_state import Workspace
    from spectra_app.quick_ui import QuickPanel
    from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
    from spectra_app.xy_plot_controller import XYPlotController
    from spectra_app.xy_analysis_controller import XYAnalysisController, XYAnalysisServices
    from spectra_app.xy_canvas_scene import XYCanvasScene
    from spectra_app.analysis_canvas import _CanvasImages
    import shiboken6

    global PROBE_APP
    app = PROBE_APP = QGuiApplication([])
    assert type(app) is QGuiApplication and not isinstance(app, QApplication)
    callback_errors = []
    sys.excepthook = lambda *error: (callback_errors.append(str(error[1])), sys.__excepthook__(*error))

    def pump():
        loop = QEventLoop()
        QTimer.singleShot(35, loop.quit)
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
        documents = []
        for index in range(2):
            workspace = Workspace()
            book = create_workbook(workspace)
            x = np.linspace(0, 10, 101)
            sheet = register_xy_sheet(workspace, book.workbook_id, 'Source',
                np.column_stack((x, x if index == 0 else 10-x)), ['X', 'Y'])
            graph = create_comparison(workspace)
            old_id = graph.comparison_id
            graph.comparison_id = 'identical-local-id'
            workspace.comparisons.pop(old_id)
            workspace.comparisons[graph.comparison_id] = graph
            graph.presentation = None
            curve = add_curve(workspace, graph.comparison_id, sheet.sheet_id, 0, 1)
            curve.color = '#ff0000' if index == 0 else '#0000ff'
            controller = XYPlotController(workspace, graph.comparison_id, owner)
            assert controller.parent() is owner and not isinstance(controller, QWidget)
            assert isinstance(controller.analysis_controller, XYAnalysisController)
            assert controller.inspector_bridge.page is controller
            assert controller.command_bridge.page is controller
            assert controller.analysis_controller.page is controller
            documents.append((workspace, graph, curve, controller))
        first, second = [entry[3] for entry in documents]
        engine = QQmlEngine()
        dat = _CanvasImages()
        engine.addImageProvider('analysis-canvas', dat)
        assert engine.rootContext().contextProperty('appBridge') is None
        qml = Path(__file__).resolve().parents[1] / 'spectra_app' / 'qml'
        components = {}
        for name in ('XYCanvas.qml', 'XYCommands.qml', 'XYNavigation.qml',
                     'XYInspectorCurves.qml', 'XYInspectorDisplay.qml', 'XYAnalysisPanel.qml'):
            component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml / name)))
            assert component.isReady(), [error.toString() for error in component.errors()]
            component.setParent(engine)
            components[name] = component
        window = QQuickWindow()
        window.resize(1280, 900)
        window.show()

        def component_root(name, bridge, parent, x=0, y=0, width=600, height=400):
            root = components[name].createWithInitialProperties({'bridge': bridge})
            assert root is not None, [error.toString() for error in components[name].errors()]
            assert isinstance(root, QQuickItem) and root.property('bridge') is bridge
            root.setParentItem(parent)
            root.setPosition(QPointF(x, y))
            root.setWidth(width)
            root.setHeight(height)
            return root

        ancestors = [QQuickItem(window.contentItem()), QQuickItem(window.contentItem())]
        for ancestor in ancestors:
            ancestor.setWidth(600)
            ancestor.setHeight(400)
        ancestors[1].setX(640)
        scenes, roots = [], []
        for controller, ancestor in zip((first, second), ancestors):
            scene = XYCanvasScene(controller, engine, owner)
            root = component_root('XYCanvas.qml', controller.canvas_bridge, ancestor)
            scene.bind_item(root)
            scenes.append(scene)
            roots.append(root)
        pump()
        first.canvas.draw(); second.canvas.draw(); pump()
        assert scenes[0].route != scenes[1].route
        assert first.canvas_bridge.imageUrl != second.canvas_bridge.imageUrl
        first_frame = scenes[0].provider.images[str(scenes[0].provider.revision)]
        second_frame = scenes[1].provider.images[str(scenes[1].provider.revision)]
        assert first_frame != second_frame
        if os.environ.get('XY_PROOF_IMAGE'):
            assert window.grabWindow().save(os.environ['XY_PROOF_IMAGE'])
        assert first.canvas_bridge.enabled and second.canvas_bridge.enabled
        assert engine.imageProvider('analysis-canvas') is dat
        assert len(scenes[0].provider.images) <= 3
        initial_svg = first.svg_bytes()
        line = first.axes.lines[0]
        first.toolbar.push_current(); first.axes.set_xlim(2, 4); first.toolbar.push_current()
        token = first.command_bridge.ownerToken
        first.render(appearance_only=True)
        first.refresh_input_state()
        assert first.command_bridge.ownerToken == token
        assert first.axes.lines[0] is line and first.axes.get_xlim() == (2., 4.)
        assert first.svg_bytes() == initial_svg

        # Real production pointer dispatch selects from the retained full scene.
        first.toolbar.home(); first.canvas.draw(); pump()
        first.set_curve_selection(set(), None)
        x, y = first.axes.transData.transform((4., 4.))
        bridge = first.canvas_bridge
        point = roots[0].mapToScene(QPointF(bridge.contentX+x/bridge.devicePixelRatio,
            bridge.contentY+(first.canvas.get_width_height(physical=True)[1]-y)/bridge.devicePixelRatio))
        QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
        pump()
        assert first._selected_ids() == {documents[0][2].curve_id}

        # Canonical action uses the same real Quick keyboard route.
        shortcut_calls = []
        action = QAction('Rescale', owner)
        action.triggered.connect(lambda: shortcut_calls.append(True))
        first.bind_canvas_actions({'rescale': action})
        roots[0].forceActiveFocus()
        QTest.keyClick(window, Qt.Key_R, Qt.ControlModifier)
        pump()
        assert shortcut_calls == [True]

        # Reparenting within the same visible window retires held input too.
        first.command_bridge.invoke('pan', first.command_bridge.ownerToken)
        QTest.mousePress(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
        pump()
        assert first.canvas_bridge._gesture_owner is not None
        before_reparent = first.canvas_bridge.ownerToken
        same_window_parent = QQuickItem(window.contentItem())
        same_window_parent.setWidth(600); same_window_parent.setHeight(400)
        roots[0].setParentItem(same_window_parent)
        pump()
        assert first.canvas_bridge._gesture_owner is None
        assert first.canvas_bridge.ownerToken != before_reparent
        assert not first.canvas_bridge.keyEvent('press', Qt.Key_Delete.value, '', 0, before_reparent)
        QTest.mouseRelease(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
        roots[0].setParentItem(ancestors[0])
        same_window_parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()

        # Real local navigation controls drive Matplotlib navigation in this engine.
        navigation = component_root('XYNavigation.qml', first.navigation_bridge,
            window.contentItem(), 0, 400, 620, 42)
        commands = component_root('XYCommands.qml', second.command_bridge,
            window.contentItem(), 640, 400, 620, 42)
        pump()
        def click(item):
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
            QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
            pump()
        click(find(navigation, 'XYNavigation_zoom'))
        assert str(first.toolbar.mode) == 'zoom rect' and not second.toolbar.mode
        old_limits = first.axes.get_xlim()
        def point_at(x, y):
            px, py = first.axes.transData.transform((x, y))
            return QPoint(round(first.canvas_bridge.contentX+px/first.canvas_bridge.devicePixelRatio),
                round(first.canvas_bridge.contentY+(first.canvas.get_width_height(physical=True)[1]-py)/first.canvas_bridge.devicePixelRatio))
        start, end = point_at(2, 2), point_at(7, 7)
        QTest.mousePress(window, Qt.LeftButton, pos=start)
        QTest.mouseMove(window, end)
        QTest.mouseRelease(window, Qt.LeftButton, pos=end)
        pump()
        assert first.axes.get_xlim() != old_limits
        click(find(navigation, 'XYNavigation_pan'))
        assert str(first.toolbar.mode) == 'pan/zoom'
        click(find(navigation, 'XYNavigation_home'))
        click(find(navigation, 'XYNavigation_pan'))
        assert not first.toolbar.mode
        style_before = documents[0][2].style
        second.set_curve_selection({documents[1][2].curve_id}, documents[1][2].curve_id)
        second._set_selected_style('scatter')
        style_button = find(commands, 'XYCommand_style_line')
        assert style_button.isVisible() and style_button.isEnabled()
        click(style_button)
        assert documents[1][2].style == 'line' and documents[0][2].style == style_before

        # Component-local nested fields modify only their actual controller.
        display = component_root('XYInspectorDisplay.qml', first.inspector_bridge,
            window.contentItem(), 0, 450, 590, 450)
        curves = component_root('XYInspectorCurves.qml', second.inspector_bridge,
            window.contentItem(), 640, 450, 590, 450)
        pump()
        field = find(display, 'xyField_name')
        assert field is not None
        field.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
        for character in 'LOCAL XY NAME':
            QTest.keyClick(window, ord(character.upper()), Qt.ShiftModifier if character.isupper() else Qt.NoModifier)
        pump()
        assert first.inspector_bridge._drafts['name'] == 'LOCAL XY NAME', first.inspector_bridge._drafts
        assert not second.inspector_bridge._drafts
        first.finish_editor()
        assert documents[0][1].name == 'LOCAL XY NAME'
        retired_field = first.inspector_bridge.inputOwner
        first.set_curve_selection(set(), None)
        assert not first.inspector_bridge.draftFor('name', 'stale name', retired_field)
        first.set_curve_selection({documents[0][2].curve_id}, documents[0][2].curve_id)
        first.inspector_bridge.setDraft('stack_step', 'invalid')
        reveals = []
        first.inspectorSectionRequested.connect(lambda *args: reveals.append(args))
        try:
            first.finish_editor()
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid draft accepted')
        assert reveals[-1] == (2, 'stack_step')
        first.revert_editor()

        # Actual ancestor presentation and window detachment retire old inputs.
        old_token = first.command_bridge.ownerToken
        ancestors[0].setEnabled(False); pump()
        assert not first.canvas_bridge.enabled and second.canvas_bridge.enabled
        ancestors[0].setEnabled(True); pump()
        assert not first.command_bridge.invoke('home', old_token)
        old_token = first.command_bridge.ownerToken
        ancestors[0].setVisible(False); pump()
        assert not first.canvas_bridge.enabled
        ancestors[0].setVisible(True); pump()
        assert not first.command_bridge.invoke('home', old_token)
        roots[0].setParentItem(None); pump()
        assert not first.canvas_bridge.enabled
        roots[0].setParentItem(ancestors[0]); pump()
        assert first.canvas_bridge.enabled

        # Lazy settings remain real Quick windows and science/jobs stay controller-owned.
        panel = first.analysis_controller
        jobs = []
        job_owner = [('xy-analysis', first.comparison_id)]
        panel.services = XYAnalysisServices(lambda: False, lambda: None,
            lambda: first.workspace, lambda: job_owner[0],
            lambda *args, **kwargs: jobs.append((args, kwargs)), lambda *args, **kwargs: None)
        panel.analyze_selected()
        analysis_root = component_root('XYAnalysisPanel.qml', panel.bridge,
            window.contentItem(), 0, 450, 590, 450)
        display.setVisible(False)
        panel.show_settings(); pump()
        assert panel.details_dialog.window().transientParent() is window
        assert panel.details_dialog.rootObject().property('bridge') is panel.bridge
        panel.start('find')
        assert jobs and panel.busy
        work_args, work_kwargs = jobs[-1]
        first.set_curve_selection(set(), None)
        work_args[1](object())
        work_kwargs['on_error'](ValueError('stale error'))
        assert panel.fit_result is None and panel.progress.status != 'failed'
        panel.background_finished(cancelled=True)
        assert not panel.busy
        first.set_curve_selection({documents[0][2].curve_id}, documents[0][2].curve_id)
        panel.analyze_selected()
        from spectra_app.analysis_document import AnalysisSeed
        panel.set_seeds([AnalysisSeed(4., seed_id='manual-seed', manual=True, amplitude=4., width=.5)])
        visited = []
        def seed_window():
            dialog = panel._seeds_dialog
            try:
                assert dialog is not None and dialog.window().transientParent() is window
                visited.append(True)
                transaction = dialog.bridge
                assert transaction.rows[0]['seedId'] == 'manual-seed'
                input_owner = first.command_bridge.ownerToken
                ancestors[0].setVisible(False)
                assert not transaction.edit('manual-seed', 'position', '5')
                assert not transaction.accept()
                ancestors[0].setVisible(True)
                assert first.command_bridge.ownerToken != input_owner
            finally:
                if dialog is not None:
                    dialog.reject()
        QTimer.singleShot(40, seed_window)
        panel.edit_seeds()
        assert visited and panel.progress.seeds[0].position == 4.
        if panel.details_dialog is not None:
            panel.details_dialog.reject(); pump()

        # Unmount preserves accepted raster/domain authority; remount gets a fresh route.
        first.set_curve_selection({documents[0][2].curve_id}, documents[0][2].curve_id)
        first.canvas.draw(); pump()
        retained = first.analysis_controller.progress
        old_scene = scenes[0]
        old_generation = old_scene.generation
        old_root = roots[0]
        old_url = first.canvas_bridge.imageUrl
        old_token = first.canvas_bridge.ownerToken
        old_scene.detach()
        assert engine.imageProvider(old_scene.route) is None
        replacement = XYCanvasScene(first, engine, owner)
        replacement_root = component_root('XYCanvas.qml', first.canvas_bridge, ancestors[0])
        replacement.bind_item(replacement_root); pump()
        assert replacement.route != old_scene.route and first.canvas_bridge.imageUrl != old_url
        assert first.analysis_controller.progress is retained
        viewport = first.canvas_bridge.viewportWidth
        assert not first.canvas_bridge.setSceneViewport(17, 19, old_generation)
        assert first.canvas_bridge.viewportWidth == viewport
        assert not first.canvas_bridge.keyEvent('press', Qt.Key_Delete.value, '', 0, old_token)
        old_root.setWidth(31); pump()
        assert first.canvas_bridge.viewportWidth == viewport
        assert second.canvas_bridge.enabled
        old_root.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); pump()
        assert first.canvas_bridge.enabled
        # Controller-first and item-first destruction both disconnect safely.
        shiboken6.delete(second)
        pump()
        assert engine.imageProvider(scenes[1].route) is None
        roots[1].deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); pump()
        replacement_root.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); pump()
        assert not first.canvas_bridge.enabled and not first._disposed
        first.dispose(); first.dispose()
        assert first.canvas._disposed
        assert not callback_errors, callback_errors
        for item in (display, curves, navigation, commands, analysis_root):
            if shiboken6.isValid(item):
                item.setParentItem(None)
                item.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        third_workspace, third_graph, _curve, _dead = documents[1]
        retained_owner = XYPlotController(third_workspace, third_graph.comparison_id, owner)
        transient_engine = QQmlEngine()
        transient_component = QQmlComponent(transient_engine, QUrl.fromLocalFile(str(qml / 'XYCanvas.qml')))
        transient_component.setParent(transient_engine)
        transient_root = transient_component.createWithInitialProperties({'bridge': retained_owner.canvas_bridge})
        transient_root.setParentItem(window.contentItem())
        transient_root.setWidth(300); transient_root.setHeight(200)
        transient_scene = XYCanvasScene(retained_owner, transient_engine, owner)
        transient_scene.bind_item(transient_root); pump()
        retained_owner.canvas.draw(); pump()
        transient_engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); pump()
        assert not retained_owner.canvas_bridge.enabled and retained_owner._scene is None
        assert not retained_owner._disposed and transient_scene._retired
        assert not shiboken6.isValid(transient_root)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); pump()
        retained_owner.dispose()
        window.close()
        window.deleteLater()
        pump()
        engine.removeImageProvider('analysis-canvas')
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
    print('QGuiApplication production XY controllers and scenes: PASS', flush=True)


if __name__ == '__main__':
    if '--controller-probe' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        controller_probe()
        import gc
        gc.collect()
        import shiboken6
        shiboken6.delete(PROBE_APP)
    else:
        unittest.main()
