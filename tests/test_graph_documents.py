"""Graph lifetimes, layout and source ownership in the real Quick application."""
from tests import dat_properties_quick_helpers as dat
from tests import quick_migration_case as migration
from dataclasses import replace
import json
from copy import deepcopy
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QPoint, QPointF, QRect
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

from spectra_app.common_prompts import PromptAction
from spectra_app.project_io import read_project, write_project
from spectra_app.workbook_state import create_comparison, create_workbook
from tests.formal_quick_helpers import edit_field, item, pump
from tests.quick_application_helpers import application


class GraphDocumentTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.window = self.controller
        self.directory = self.folder
        self.errors = self.quick_errors
        self.paths = []
        for name in ('first', 'second'):
            path = self.directory / f'{name}.dat'
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
            self.paths.append(path)
        self.window._load_dat_file_direct(self.paths[0])
        pump()


    def tearDown(self):
        self.assertEqual(self.quick_errors, [], 'Unexpected production Quick error')

    def rectangle(self, key, rectangle):
        area = self.window.graph_documents
        self.window.request_outer_view('workspace')
        area.setMode('free')
        pump()
        frame = area._presentations[key].frame
        self.assertIsNotNone(frame)
        token = area.captureFrameInput(list(key), frame)
        self.assertTrue(token)
        self.assertTrue(area.moveResize(list(key), frame, token, *rectangle.getRect()))
        pump()
        return frame

    def active_document(self):
        area = self.window.graph_documents
        return area.document_for(area.registry.active_key)

    def clean_reopen(self, path):
        self.window._refresh_project_modified()
        self.assertFalse(self.window.projectModified)
        with patch('spectra_app.main_window.prompt_message',
                   side_effect=AssertionError('Clean reopen unexpectedly asked to discard')):
            return self.window.open_project(path)

    def open(self, dataset_id=None, graph_id=None):
        page = self.window.open_graph_document(dataset_id, graph_id)
        pump(65)
        key = self.window._document_key()
        self.assertIs(self.window.graph_documents.document_for(key), page)
        self.assertTrue(self.window.graph_documents.content_item(key).isVisible())
        self.window._render_plot_page()
        pump()
        self.assertIsNotNone(page.rendered_page, page.status_text)
        return page

    def test_three_free_windows_keep_normal_geometry_across_tabs_close_and_resize(self):
        w = self.window
        self.open()
        w.graph_documents.setMode('free')
        for name in ('Detail', 'Overview'):
            w.create_graph(name)
            self.open()
        area = w.graph_documents
        self.assertEqual(area.mode, 'free')
        keys = area.registry.open_keys()
        self.assertEqual(len({area.registry.normal_rectangle(key).topLeft().toTuple() for key in keys}), 3)
        rectangles = [QRect(12+i*55, 14+i*32, 370, 255) for i in range(3)]
        for key, rect in zip(keys, rectangles):
            self.rectangle(key, rect)
        area.setMode('tabbed')
        pump()
        self.assertEqual(area.visible_keys(), (area.registry.active_key,))
        self.assertEqual(area.display_rectangle(area.registry.active_key).size().toTuple(), area._viewport)
        area.setMode('free')
        pump()
        self.assertEqual([area.display_rectangle(key) for key in keys], rectangles)
        area.close_document(keys[-1])
        w.open_graph_document(*keys[-1])
        pump()
        self.assertEqual(area.registry.normal_rectangle(keys[-1]), rectangles[-1])
        self.host.window.resize(1100, 720)
        pump()
        self.assertEqual([area.display_rectangle(key) for key in keys], rectangles)
        self.assertTrue(item(w, 'documentsFree').property('checked'))
        self.assertFalse(item(w, 'documentsTabbed').property('checked'))


    def test_restored_layout_adapts_once_after_final_free_or_tabbed_layout(self):
        w = self.window
        self.open()
        key = w._document_key()
        graph = w.create_graph('Closed rectangle')
        self.open()
        closed = w._document_key()
        area = w.graph_documents
        records = [{'key': list(key), 'open': True, 'rect': [99999, -500, 560, 390]},
                   {'key': list(closed), 'open': False, 'rect': [55, 65, 380, 270]}]
        for mode in ('free', 'tabbed'):
            with self.subTest(mode=mode):
                metadata = {'mode': mode, 'documents': records, 'active': list(key), 'stacking': [list(key)]}
                area.restore_workspace(metadata, w._document_title)
                self.host.window.resize(900, 620)
                pump(100)
                if mode == 'tabbed':
                    self.assertEqual(area.visible_keys(), (key,))
                    self.assertEqual(area.workspace_snapshot((key, closed))['documents'], records)
                    area.setMode('free')
                    pump(80)
                frame = area._presentations[key].frame
                viewport = item(w, 'workspaceDocumentViewport')
                title = QRect(round(frame.x()), round(frame.y()), round(frame.width()), 34)
                visible = QRect(round(viewport.property('contentX')), round(viewport.property('contentY')),
                                round(viewport.width()), round(viewport.height()))
                reachable = visible.intersected(title)
                self.assertGreaterEqual(reachable.width(), 120)
                self.assertEqual(reachable.height(), title.height())
                self.assertEqual((frame.width(), frame.height()), (560, 390))
                self.assertEqual(area.workspace_snapshot((key, closed))['documents'], records)
                self.assertNotIn(closed, area.registry.open_keys())
                manual = QRect(600, 45, 560, 390)
                self.rectangle(key, manual)
                self.host.window.resize(850, 600)
                pump()
                self.assertEqual(area.display_rectangle(key), manual)
                if mode == 'free':
                    parked = area.park()
                    area.restore(parked)
                    area.restore_workspace(metadata, w._document_title)
                    area.restore(parked)
                    pump()
                    self.assertEqual(area.display_rectangle(key), manual)
                    area.restore_workspace(metadata, w._document_title)
                    area.reset_layout()
                    self.rectangle(key, manual)
                    pump()
                    self.assertEqual(area.display_rectangle(key), manual)


    def test_shared_commands_follow_active_owner_with_stable_host(self):
        w = self.window
        first = self.open()
        w.create_graph('Detail')
        second = self.open()
        commands = item(w, 'formalCommands')
        self.assertIs(commands.property('bridge'), second.formal)
        self.assertTrue(item(commands, 'formal_export_svg').isVisible())
        self.assertIsNot(w.graph_documents.content_item(w._document_key()), commands)
        w.open_graph_document(graph_id='default')
        pump()
        self.assertIs(item(w, 'formalCommands'), commands)
        self.assertIs(commands.property('bridge'), first.formal)
        self.assertTrue(item(commands, 'formal_zoom').isVisible())
        w.request_outer_view('analysis')
        pump()
        self.assertFalse(first.formal.allowed())
        w.request_outer_view('workspace')
        for key in tuple(w.graph_documents.registry.open_keys()):
            w.graph_documents.close_document(key)
        pump()
        self.assertTrue(item(w, 'formalCommands').isVisible())
        self.assertFalse(first.formal.allowed())


    def test_scrolled_workspace_stores_document_coordinates_and_keeps_dragged_layout(self):
        w = self.window
        self.open()
        session = w.workspace.active
        second_graph = w.create_graph('Scrolled detail')
        self.open()
        keys = [(session.dataset_id, 'default'), (session.dataset_id, second_graph.graph_id)]
        area = w.graph_documents
        for key, rectangle in zip(keys, (QRect(20,30,400,300), QRect(950,60,400,300))):
            self.rectangle(key, rectangle)
        viewport = item(w, 'workspaceDocumentViewport')
        viewport.setProperty('contentX', 250.)
        pump()
        self.assertEqual(viewport.property('contentX'), 250.)
        snapshot = area.workspace_snapshot(keys)
        self.assertEqual([entry['rect'] for entry in snapshot['documents']],
                         [[20,30,400,300], [950,60,400,300]])
        frame = area._presentations[keys[1]].frame
        token = area.captureFrameInput(list(keys[1]), frame)
        self.assertTrue(area.moveResize(list(keys[1]), frame, token, 980,75,400,300))
        snapshot = area.workspace_snapshot(keys)
        self.assertEqual(snapshot['documents'][1]['rect'], [980,75,400,300])
        area.setMode('tabbed')
        area.setMode('free')
        pump()
        self.assertEqual(area.workspace_snapshot(keys)['documents'], snapshot['documents'])
        self.assertEqual(area.display_rectangle(keys[1]).x()-area.display_rectangle(keys[0]).x(), 960)
        w.workspace.graph_workspace = area.workspace_snapshot(keys)
        destination = self.directory / 'scrolled-layout.spectra'
        write_project(destination, w.workspace)
        restored = read_project(destination).graph_workspace
        self.assertEqual(restored['documents'], snapshot['documents'])
        area.restore_workspace(restored, w._document_title)
        self.assertEqual(area.workspace_snapshot(keys)['documents'], snapshot['documents'])


    def test_native_frame_drag_resize_keeps_scientific_page_and_free_layout(self):
        w = self.window
        page = self.open()
        area = w.graph_documents
        key = area.registry.active_key
        frame = self.rectangle(key, QRect(20, 20, 410, 300))
        self.assertEqual(area.mode, 'free')
        rendered = page.rendered_page
        before = area.registry.normal_rectangle(key)
        receiver = self.host.window

        def drag(control, local, delta):
            start = control.mapToScene(QPointF(local)).toPoint()
            end = start + delta
            QTest.mouseMove(receiver, start)
            QTest.mousePress(receiver, Qt.LeftButton, pos=start)
            # Window QtTest mouseMove omits held buttons on offscreen Qt.
            # Deliver the same native move state as a physical grabbed drag.
            event = QMouseEvent(QEvent.MouseMove, QPointF(end),
                QPointF(receiver.mapToGlobal(end)), Qt.NoButton,
                Qt.LeftButton, Qt.NoModifier)
            QCoreApplication.sendEvent(receiver, event)
            QTest.mouseRelease(receiver, Qt.LeftButton, pos=end)
            pump()

        drag(item(frame, 'documentTitleBar'), QPoint(140, 12), QPoint(40, 32))
        self.assertNotEqual(area.registry.normal_rectangle(key).topLeft(), before.topLeft())
        size = area.registry.normal_rectangle(key).size()
        handle = item(frame, 'documentResizeHandle')
        drag(handle, QPoint(round(handle.width()/2), round(handle.height()/2)), QPoint(28, 25))
        self.assertNotEqual(area.registry.normal_rectangle(key).size(), size)
        self.assertEqual((frame.width(), frame.height()), (438, 325))
        self.assertIs(page.rendered_page, rendered)


    def test_maximize_and_tab_transitions_keep_original_normal_rectangle(self):
        self.open()
        area = self.window.graph_documents
        key = area.registry.active_key
        rect = QRect(35,45,400,285)
        frame = self.rectangle(key, rect)
        token = area.captureFrameInput(list(key), frame)
        self.assertTrue(area.toggleMaximize(list(key), frame, token))
        pump()
        self.assertTrue(area._presentations[key].maximized)
        area.setMode('tabbed')
        area.setMode('free')
        pump()
        self.assertEqual(area.display_rectangle(key), rect)
        frame = area._presentations[key].frame
        token = area.captureFrameInput(list(key), frame)
        self.assertTrue(area.toggleMaximize(list(key), frame, token))
        pump()
        self.assertTrue(area.toggleMaximize(list(key), frame, area.captureFrameInput(list(key), frame)))
        self.assertEqual(area.display_rectangle(key), rect)


    def test_failed_open_after_all_closed_keeps_closed_on_plot_entry(self):
        w = self.window
        self.open()
        w.graph_documents.close_document()
        w.request_outer_view('analysis')
        saved = w.save_project(self.directory / 'closed-rollback.spectra')
        with patch.object(w, '_project_installed_workspace', side_effect=ValueError('install failed')):
            with self.assertRaisesRegex(ValueError, 'install failed'):
                self.clean_reopen(saved)
        w.request_outer_view('workspace')
        pump()
        self.assertEqual(w.graph_documents.registry.open_keys(), ())
        self.assertTrue(item(w, 'formalCommands').isVisible())
        viewport = item(w, 'workspaceDocumentViewport')
        message = next(child for child in viewport.property('contentItem').childItems()
                       if child.metaObject().indexOfProperty('text') >= 0
                       and str(child.property('text')).startswith('Open a Workbook'))
        self.assertTrue(message.isVisible())
        self.assertEqual(message.property('text'), 'Open a Workbook or graph from Project, or start with data files.')


    def test_graphs_retain_pages_bytes_and_zoom_across_tab_and_tile_switches(self):
        w = self.window
        first = self.open()
        first.set_zoom('150%')
        original = first.rendered_page
        graph = w.create_graph('Detail')
        second = self.open(graph_id=graph.graph_id)
        self.assertIsNot(first, second)
        w.set_plot_presentation(replace(w.workspace.active.presentation, width_in=4.2, aspect_locked=False))
        w._render_plot_page()
        second_bytes = second.rendered_page.svg_bytes
        w.graph_documents.tile()
        self.assertEqual(w.graph_documents.mode, 'free')
        self.host.window.resize(1100, 720)
        w.graph_documents.tile()
        pump()
        self.assertEqual((self.host.window.width(), self.host.window.height()), (1100, 720))
        self.assertIs(w.open_graph_document(graph_id='default'), first)
        self.assertIs(first.rendered_page, original)
        self.assertEqual(first.zoom_text, '150%')
        self.assertEqual(second.rendered_page.svg_bytes, second_bytes)
        w.graph_documents.setMode('tabbed')
        self.assertEqual(len(w.graph_documents.registry.open_keys()), 2)

    def test_two_datasets_keep_independent_pages_and_inactive_actions_select_owner(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        original = first.rendered_page
        w._load_dat_file_direct(self.paths[1])
        second_id = w.workspace.active_dataset_id
        second = self.open()
        self.assertIsNot(first, second)
        self.assertIs(first.rendered_page, original)
        w.graph_documents.tile()
        pump()
        key = (first_id, 'default')
        paper = item(w.graph_documents.content_item(key), 'formalPaper')
        point = paper.mapToScene(QPointF(min(40., paper.width()/2), min(40., paper.height()/2))).toPoint()
        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=point)
        pump()
        self.assertEqual(w.workspace.active_dataset_id, first_id)
        self.assertIs(w.plot_page, first)
        first.propertyEditRequested.emit('title', '')
        w.dat_properties.setLabel('plot_title', 'First graph only', w.dat_properties.ownerToken)
        w._finish_graph_editors()
        w._render_plot_page()
        self.assertEqual(w.workspace.datasets[first_id].view.annotations['plot_title'], 'First graph only')
        self.assertNotEqual(w.workspace.datasets[second_id].view.annotations['plot_title'], 'First graph only')
        w.open_graph_document(second_id)
        first.exportRequested.emit('svg')
        self.assertEqual(w.workspace.active_dataset_id, first_id)
        saved, = list(w._output_folder().glob('first*.svg'))
        self.assertEqual(saved.read_bytes(), first.rendered_page.svg_bytes)

    def test_inactive_formal_projection_keeps_active_owner_and_actual_quick_action_authorizes(self):
        w = self.window
        first = self.open()
        graph = w.create_graph('Second projection')
        second = self.open(graph_id=graph.graph_id)
        w.graph_documents.tile()
        pump()
        self.assertIs(w.plot_page, second)
        for _ in range(2):
            first.formal.state
            first.formal.enabled
            first.preview.canEdit
            first.preview.overlay
            first.formal.sync()
            pump()
        self.assertIs(w.plot_page, second)
        self.assertEqual(w.graph_documents.registry.active_key, (w.workspace.active_dataset_id, graph.graph_id))
        copies = []
        first.graphCopyRequested.disconnect()
        first.graphCopyRequested.connect(lambda: copies.append(True))
        # This is the QML popup/controller action route, without a native
        # QWidget event filter activating the pane first.
        first.formal.command('copy_svg')
        self.assertIs(w.plot_page, first)
        self.assertEqual(copies, [True])
        self.assertEqual(w.graph_documents.registry.active_key, (w.workspace.active_dataset_id, 'default'))

    def test_registry_leases_guard_retained_workbook_and_busy_current_dat_native_input(self):
        w = self.window
        dat_page = self.open()
        area = w.graph_documents
        book = create_workbook(w.workspace, 'Registry Workbook')
        page = w.open_workbook(book.workbook_id)
        pump()
        key = ('workbook', book.workbook_id, '')
        old_guard = page._input_guard
        self.assertTrue(old_guard())
        self.assertTrue(area.close_document(key))
        self.assertFalse(old_guard())
        self.assertIs(w.open_workbook(book.workbook_id), page)
        pump()
        reopened_guard = page._input_guard
        self.assertFalse(old_guard())
        self.assertTrue(reopened_guard())
        parked = area.park()
        self.assertFalse(reopened_guard())
        area.restore(parked)
        pump()
        restored_guard = page._input_guard
        self.assertIs(area.document_for(key), page)
        self.assertFalse(reopened_guard())
        self.assertTrue(restored_guard())
        area.restore(parked)
        pump()
        self.assertFalse(restored_guard())
        self.assertTrue(page._input_guard())
        self.assertIs(w.open_graph_document(), dat_page)
        pump()
        key = area.registry.active_key
        frame = area._presentations[key].frame
        token = area.captureFrameInput(list(key), frame)
        self.assertTrue(token)
        with patch.object(w, '_project_busy', return_value=True):
            self.assertTrue(w._activate_document(key))
            self.assertEqual(area.captureFrameInput(list(key), frame), '')
            self.assertFalse(area.activateFrame(list(key), frame, token))
            self.assertFalse(dat_page.formal.allowed())


    def test_native_frame_activation_checks_registry_eligibility_before_domain_activation(self):
        w = self.window
        first = self.open()
        first_key = w._document_key()
        w.create_graph('Second registry frame')
        second = self.open()
        area = w.graph_documents
        area.setMode('free')
        pump()
        active = area.registry.active_key
        frame = area._presentations[first_key].frame
        token = area.captureFrameInput(list(first_key), frame)
        with patch.object(w, '_project_busy', return_value=True), patch.object(area, '_activate', wraps=area._activate) as activate:
            self.assertFalse(area.activateFrame(list(first_key), frame, token))
            self.assertEqual(area.registry.active_key, active)
            self.assertIs(self.active_document(), second)
            activate.assert_not_called()


    def test_retired_native_binding_is_consumed_and_parked_disposal_runs_once(self):
        w = self.window
        old = self.open()
        area = w.graph_documents
        key = area.registry.active_key
        frame = area._presentations[key].frame
        token = area.captureFrameInput(list(key), frame)
        old_scene = area._presentations[key].mount.scene
        mutations, disposals = [], []
        old.graphCopyRequested.connect(lambda: mutations.append('old native handler'))
        with patch.object(old, 'dispose', wraps=old.dispose) as old_dispose:
            area.discard(key)
            self.assertEqual(old_dispose.call_count, 1)
            self.assertTrue(old.retired)
            old.formal.command('copy_svg')
            self.assertFalse(old.preview.sceneKey(old_scene.generation, Qt.Key_C, Qt.ControlModifier.value))
            self.assertFalse(area.activateFrame(list(key), frame, token))
            self.assertTrue(old_scene._retired)
            self.assertIsNone(self.host.engine.imageProvider(old_scene.route))
            self.assertEqual(mutations, [])
        replacement = w.open_graph_document()
        pump()
        self.assertIsNot(replacement, old)
        with patch.object(replacement, 'dispose', wraps=replacement.dispose) as dispose:
            parked = area.park()
            area.discard_parked(parked)
            area.discard_parked(parked)
            self.assertEqual(dispose.call_count, 1)
        self.assertEqual(area.keys(), ())


    def test_tiling_with_application_style_keeps_compact_window_size(self):
        w = self.window
        self.open()
        w.create_graph('Detail')
        self.open()
        self.host.window.resize(1100,720)
        pump()
        w.graph_documents.tile()
        pump()
        self.assertEqual((self.host.window.width(), self.host.window.height()), (1100,720))
        font = item(w, 'formalCommands').property('controlFont')
        from PySide6.QtGui import QGuiApplication
        self.assertEqual(font, QGuiApplication.font())
        visible = QRect(0,0,*w.graph_documents._viewport)
        for key in w.graph_documents.registry.open_keys():
            frame = w.graph_documents._presentations[key].frame
            self.assertTrue(visible.contains(QRect(round(frame.x()), round(frame.y()),round(frame.width()),round(frame.height()))))


    def test_native_events_stay_in_their_window_even_with_identical_document_keys(self):
        w = self.window
        self.open()
        w.dat_properties.setLabel('plot_title', 'Editable title', w.dat_properties.ownerToken)
        graph = w.create_graph('Detail')
        detail = self.open()
        project = w.save_project(self.directory / 'shared.spectra')
        edit_field(w, 'width_in', '-', commit=False)
        self.assertEqual(detail.formal.state['fields']['width_in'], '-')
        other_host, _, errors = self.enterContext(application())
        other = other_host.controller
        other.open_project(project)
        page = other.open_graph_document(graph_id='default')
        other_host.window.resize(1250,900)
        other_host.window.requestActivate()
        other._render_plot_page()
        pump(80)
        self.assertEqual(other.workspace.active_dataset_id, w.workspace.active_dataset_id)
        self.assertIsNotNone(w.graph_documents.document_for((w.workspace.active_dataset_id,'default')))
        preview = other.graph_documents.content_item(other._document_key())
        left,bottom,width,height = page.rendered_page.object_bounds['title'][0]
        paper_width,paper_height = page.preview.paper_display_size()
        canvas = item(preview, 'formalPaper')
        point = canvas.mapToScene(QPointF((left+width/2)*paper_width,(1-bottom-height/2)*paper_height)).toPoint()
        QTest.mouseDClick(other_host.window, Qt.LeftButton, pos=point)
        QTest.mouseRelease(other_host.window, Qt.LeftButton, pos=point)
        pump(80)
        self.assertEqual(other.taskIndex, 2)
        self.assertTrue(dat.dat_item(other,'plot_title').hasActiveFocus())
        self.assertIs(w.plot_page,detail)
        self.assertEqual(w.workspace.active.active_graph_id,graph.graph_id)
        self.assertEqual(detail.formal.state['fields']['width_in'],'-')
        self.assertEqual(errors,[])
        w.request_outer_view('workspace')
        detail.formal.setDraft('width_in','4')
        self.assertTrue(detail.formal.commitField('width_in'))


    def test_close_last_view_and_reopen_retains_graph_and_project_content(self):
        w = self.window
        page = self.open()
        page.set_zoom('150%')
        page.formal.setDraft('width_in', '4.2')
        self.assertTrue(page.formal.commitField('width_in'))
        w._render_plot_page()
        original = page.rendered_page
        controller = page.preview
        generation = controller.gestureGeneration
        w._finish_graph_editors()
        w.save_project(self.directory / 'close-view.spectra')
        before = deepcopy(w.workspace.active.plot_collection())
        cube = w.workspace.active.current_cube
        w.graph_documents.close_document()
        self.assertEqual(len(w.graph_documents.registry.open_keys()), 0)
        self.assertIs(page.preview, controller)
        self.assertFalse(controller._disposed)
        self.assertFalse(controller.canView)
        self.assertEqual(controller.svg_bytes, original.svg_bytes)
        self.assertGreater(controller.gestureGeneration, generation)
        self.assertIs(w.plot_page, page)
        self.assertEqual(w.workspace.active.plot_collection(), before)
        self.assertIs(w.workspace.active.current_cube, cube)
        w._refresh_project_modified()
        self.assertTrue(w.projectModified)
        self.assertIs(w.open_graph_document(), page)
        self.assertIs(page.rendered_page, original)
        self.assertIs(page.preview, controller)
        self.assertEqual(controller.svg_bytes, original.svg_bytes)
        self.assertTrue(controller.canView)
        self.assertEqual(page.zoom_text, '150%')
        self.assertEqual(float(page.formal.state['fields']['width_in']), 4.2)
        self.assertEqual(len(w.graph_documents.registry.open_keys()), 1)

    def test_invalid_draft_rejects_document_switch_and_close_atomically(self):
        w = self.window
        first = self.open()
        graph = w.create_graph('Detail')
        second = self.open()
        edit_field(w, 'width_in', '-', commit=False)
        with self.assertRaises(ValueError):
            w.open_graph_document(graph_id='default')
        self.assertIs(w.plot_page, second)
        self.assertEqual(w.workspace.active.active_graph_id, graph.graph_id)
        self.assertIs(self.active_document(), second)
        self.assertFalse(w.graph_documents.requestActivate((w.workspace.active_dataset_id, 'default')))
        self.assertIs(self.active_document(), second)
        frame = w.graph_documents._presentations[w._document_key()].frame
        close = item(frame, 'documentClose')
        point = close.mapToScene(QPointF(close.width()/2, close.height()/2)).toPoint()
        QTest.mouseClick(self.host.window, Qt.LeftButton, pos=point)
        pump()
        self.assertEqual(len(w.graph_documents.registry.open_keys()), 2)
        second.formal.setDraft('width_in', '4')
        self.assertTrue(second.formal.commitField('width_in'))
        self.assertIs(w.open_graph_document(graph_id='default'), first)

    def test_project_save_commits_hidden_formal_draft_without_switching_auxiliary_owner(self):
        w = self.window
        def restore_cached_draft(field, raw):
            # Restore pending cached editor state as a system setup operation.
            # A real QML setDraft input now authorizes/activates its registry pane.
            page.formal._drafts[field] = raw
            page.formal.draftChanged.emit()
            page.formal.sync()
        self.open()
        session = w.workspace.active
        untouched = session.active_graph.presentation
        graph = w.create_graph('Hidden formal owner')
        page = self.open()
        book = create_workbook(w.workspace, 'Visible Workbook')
        comparison = create_comparison(w.workspace, 'Visible XY')
        for kind, identifier, width in (('workbook', book.workbook_id, 4.3),
                                        ('xy', comparison.comparison_id, 4.7)):
            with self.subTest(kind=kind):
                w.open_graph_document(session.dataset_id, graph.graph_id)
                active = (w.open_workbook(identifier) if kind == 'workbook'
                          else w.open_xy_comparison(identifier))
                self.assertIs(w.plot_page, page)
                self.assertIs(self.active_document(), active)
                restore_cached_draft('width_in', str(width))
                self.assertTrue(page.has_pending_project_edits())
                w._refresh_project_modified()
                self.assertTrue(w.projectModified)
                destination = self.directory / f'hidden-formal-{kind}.spectra'
                w.save_project(destination)
                self.assertIs(self.active_document(), active)
                self.assertIs(w.plot_page, page)
                self.assertEqual(session.active_graph_id, graph.graph_id)
                self.assertEqual(graph.presentation.width_in, width)
                self.assertEqual(session.graphs['default'].presentation, untouched)
                self.assertFalse(page.has_pending_project_edits())
                history = w._history_for(session)
                self.assertEqual(history.undo_stack[-1].graph_id, graph.graph_id)
                history_size = len(history.undo_stack)
                saved = read_project(destination)
                self.assertEqual(saved.datasets[session.dataset_id].graphs[graph.graph_id]
                                 .presentation.width_in, width)
                self.assertEqual(saved.datasets[session.dataset_id].graphs['default']
                                 .presentation, untouched)
                original_bytes = destination.read_bytes()
                restore_cached_draft('width_in', '-')
                with self.assertRaises(ValueError):
                    w.save_project(destination)
                self.assertEqual(destination.read_bytes(), original_bytes)
                self.assertEqual(page.formal.state['fields']['width_in'], '-')
                self.assertTrue(page.has_pending_project_edits())
                self.assertEqual(graph.presentation.width_in, width)
                self.assertEqual(len(history.undo_stack), history_size)
                self.assertIs(self.active_document(), active)
                self.assertIs(w.plot_page, page)
                restore_cached_draft('width_in', str(width))
                page.finish_editor()

    def test_close_active_view_selects_visible_neighbor_before_further_edits(self):
        w = self.window
        first = self.open()
        graph = w.create_graph('Detail')
        detail = self.open()
        w.graph_documents.close_document()
        self.assertIs(w.plot_page, first)
        self.assertEqual(w.workspace.active.active_graph_id, 'default')
        self.assertIs(self.active_document(), first)
        w.dat_properties.setLabel('plot_title', 'Visible neighbor', w.dat_properties.ownerToken)
        w._finish_graph_editors()
        self.assertNotEqual(w.workspace.active.graphs[graph.graph_id].view.annotations['plot_title'],
                            'Visible neighbor')
        self.assertIs(w.open_graph_document(graph_id=graph.graph_id), detail)

    def test_shared_science_invalidates_all_dataset_graphs_only(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        w.create_graph('Detail')
        detail = self.open()
        w._load_dat_file_direct(self.paths[1])
        other = self.open()
        untouched = other.rendered_page
        w.open_graph_document(first_id, 'default')
        w._replace_current_data(replace(w.current_cube, z=w.current_cube.z * 2),
                                mode='original', smooth=True, window_length=21)
        self.assertIsNone(first.rendered_page)
        self.assertIsNone(detail.rendered_page)
        self.assertIs(other.rendered_page, untouched)
        w.graph_documents.tile()
        w._render_visible_plot_page()
        self.assertIsNotNone(first.rendered_page)
        self.assertIsNotNone(detail.rendered_page)
        self.assertEqual(detail.rendered_page.owner_token[-1], detail.preview.snapshot.graph_id)

    def test_inactive_format_application_retires_only_target_dataset_graph(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        w._load_dat_file_direct(self.paths[1])
        source = self.open()
        w.set_plot_presentation(replace(w.workspace.active.presentation, width_in=4.8, aspect_locked=False))
        w.save_plot_format('wide')
        original = source.rendered_page
        outcomes = w.apply_plot_format('wide', [first_id])
        self.assertTrue(outcomes[0]['success'], outcomes)
        self.assertIsNone(first.rendered_page)
        self.assertIs(source.rendered_page, original)
        self.assertIs(w.plot_page, source)
        w.graph_documents.tile()
        w._render_visible_plot_page()
        self.assertEqual(first.rendered_page.figure_size_in[0], 4.8)
        self.assertEqual(float(first.formal.state['fields']['width_in']), 4.8)

    def test_graph_removal_and_project_reset_release_cached_documents(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        graph = w.create_graph('Detail')
        detail = self.open()
        stale = detail.rendered_page
        controller = detail.preview
        route = w.graph_documents._presentations[(first_id, graph.graph_id)].mount.scene.route
        w.remove_graph(graph.graph_id)
        self.assertTrue(controller._disposed)
        self.assertIsNone(detail.rendered_page)
        self.assertFalse(detail.command_enabled('export_svg'))
        self.assertFalse(controller.set_page(stale))
        self.assertIsNone(self.host.engine.imageProvider(route))
        self.assertIsNone(w.graph_documents.document_for((first_id, graph.graph_id)))
        self.assertEqual(len(w.graph_documents.registry.open_keys()), 1)
        self.assertIs(w.plot_page, first)
        self.assertFalse(first.commit_rendered_page(stale))

        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            w.new_project()
        self.assertEqual(len(w.graph_documents.registry.open_keys()), 0)
        self.assertIsNone(w.graph_documents.document_for((first_id, 'default')))
        self.assertIsNone(w.plot_page.rendered_page)

    def test_dataset_removal_releases_all_its_graphs(self):
        w = self.window
        dataset_id = w.workspace.active_dataset_id
        self.open()
        graph = w.create_graph('Detail')
        self.open()
        w._load_dat_file_direct(self.paths[1])
        other = self.open()
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.YES):
            self.assertTrue(w.remove_dataset(dataset_id))
        self.assertIsNone(w.graph_documents.document_for((dataset_id, 'default')))
        self.assertIsNone(w.graph_documents.document_for((dataset_id, graph.graph_id)))
        self.assertIs(w.plot_page, other)
        self.assertEqual(len(w.graph_documents.registry.open_keys()), 1)

    def test_owner_restore_failure_keeps_previous_page_and_controls(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        w._load_dat_file_direct(self.paths[1])
        second_id = w.workspace.active_dataset_id
        second = self.open()
        dat.set_choice(w, 'colormap', 'magma')
        restore = w.restore_dataset_controls

        def fail_first(session):
            if session.dataset_id == first_id:
                raise ValueError('projection failed')
            restore(session)

        with patch.object(w, 'restore_dataset_controls', side_effect=fail_first):
            with self.assertRaisesRegex(ValueError, 'projection failed'):
                w.open_graph_document(first_id)
        self.assertEqual(w.workspace.active_dataset_id, second_id)
        self.assertIs(w.plot_page, second)
        self.assertEqual(next(entry['label'] for entry in dat.state(w)['colormaps'] if entry['value'] == dat.state(w)['colormap']), 'magma')
        self.assertIs(self.active_document(), second)
        self.assertIs(w.open_graph_document(first_id), first)

    def test_document_commands_follow_busy_empty_and_closed_states(self):
        w = self.window
        self.open()
        w._refresh_graph_commands()
        self.assertTrue(w.close_graph_view_action.isEnabled())
        with patch.object(w, '_project_busy', return_value=True):
            w._refresh_graph_commands()
            self.assertFalse(w.open_graph_action.isEnabled())
            self.assertFalse(w.close_graph_view_action.isEnabled())
        w.graph_documents.close_document()
        w._refresh_graph_commands()
        self.assertTrue(w.open_graph_action.isEnabled())
        self.assertFalse(w.close_graph_view_action.isEnabled())
        self.assertFalse(w.tile_graphs_action.isEnabled())

        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            w.new_project()
        self.assertFalse(w.open_graph_action.isEnabled())

    def test_peak_edit_retires_inactive_labeled_graph_but_retains_unlabeled_page(self):
        w = self.window
        labeled = self.open()
        labeled.formal.setValue('include_peak_labels', True)
        w._render_plot_page()
        w.create_graph('No labels')
        plain = self.open()
        plain.formal.setValue('include_peak_labels', False)
        w._render_plot_page()
        unchanged = plain.rendered_page
        w._add_manual_peak(1.5)
        self.assertIsNone(labeled.rendered_page)
        self.assertIs(plain.rendered_page, unchanged)
        w.open_graph_document(graph_id='default')
        w._render_plot_page()
        self.assertEqual([peak.x for peak in labeled.preview.snapshot.peaks], [1.5])

    def test_cross_dataset_document_switch_saves_outgoing_sidecar(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        w._load_dat_file_direct(self.paths[1])
        second_id = w.workspace.active_dataset_id
        self.open()
        w.open_graph_document(first_id)
        first.formal.setValue('aspect_locked', False)
        first.formal.setDraft('width_in', '4.8')
        self.assertTrue(first.formal.commitField('width_in'))
        w.open_graph_document(second_id)
        payload = json.loads(w._plot_state_store.path_for(self.paths[0]).read_text(encoding='utf-8'))
        self.assertEqual(payload['plot']['graphs'][0]['plot']['presentation']['width_in'], 4.8)

    def test_failed_project_install_restores_open_and_closed_document_runtime(self):
        w = self.window
        first_id = w.workspace.active_dataset_id
        first = self.open()
        first.set_zoom('150%')
        graph = w.create_graph('Closed detail')
        closed = self.open()
        closed.set_zoom('100%')
        w.graph_documents.close_document()
        w._load_dat_file_direct(self.paths[1])
        second = self.open()
        second.set_zoom('200%')
        w.graph_documents.tile()
        original_bytes = first.rendered_page.svg_bytes
        note = second.add_annotation('text', 'Retained rollback note')
        w._render_plot_page()
        formal_controllers = (first, closed, second)
        controllers = tuple(page.preview for page in (first, closed, second))
        accepted_bytes = tuple(controller.svg_bytes for controller in controllers)
        project = w.save_project(self.directory / 'restore.spectra')
        controller = second.preview
        attachment = w.graph_documents._presentations[w._document_key()].mount.scene
        point = controller.annotation_point(note)
        generation = controller.scenePress(attachment.generation, point.x(), point.y())
        self.assertGreaterEqual(generation, 0)
        controller.sceneMove(attachment.generation, generation, point.x()+12, point.y()+8)
        edits = []
        controller.annotationMoved.connect(edits.append)
        with patch.object(w, '_project_installed_workspace', side_effect=ValueError('installation failed')):
            with self.assertRaisesRegex(ValueError, 'installation failed'):
                self.clean_reopen(project)
        self.assertIs(w.plot_page, second)
        self.assertIs(w.graph_documents.document_for((first_id, 'default')), first)
        self.assertIs(w.graph_documents.document_for((first_id, graph.graph_id)), closed)
        self.assertEqual(len(w.graph_documents.registry.open_keys()), 2)
        self.assertEqual(w.graph_documents.mode, 'free')
        self.assertEqual((first.zoom_text, second.zoom_text), ('150%', '200%'))
        self.assertEqual(first.rendered_page.svg_bytes, original_bytes)
        for page, formal, retained, svg_bytes in zip((first, closed, second), formal_controllers, controllers, accepted_bytes):
            self.assertIs(page, formal)
            self.assertFalse(formal.retired)
            self.assertIs(page.preview, retained)
            self.assertFalse(retained._disposed)
            self.assertEqual(retained.svg_bytes, svg_bytes)
        self.assertGreater(controller.gestureGeneration, generation)
        controller.sceneRelease(attachment.generation, generation, point.x()+24, point.y()+16)
        self.assertEqual(edits, [])
        self.assertIs(w.open_graph_document(first_id, graph.graph_id), closed)


if __name__ == '__main__':
    unittest.main()
