from tests import dat_properties_quick_helpers as dat
from tests import quick_migration_case as migration
from tests import analysis_canvas_quick_helpers as ac
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from tests.settings_helpers import wait_for_background_jobs
from tests.quick_application_helpers import application, wait_until
from tests.formal_quick_helpers import item as quick_item, click_item, pump, input_receiver

class OriginInteractionTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        self.w = self.controller
        self.errors = self.quick_errors
        self.window_errors = [self.quick_errors]
        self.path = self.folder / 'graph.dat'
        self.path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)))
        self.w._load_dat_file_direct(self.path)
        self.w.open_graph_document()
        self.w.root_window.resize(1250, 900)
        self.w.request_outer_view('workspace')
        self.w.dat_details_action.setChecked(True)
        self.w.request_task(2)
        self.w.root_window.requestActivate()
        pump()
        self.render()

    def tearDown(self):
        wait_for_background_jobs(self.w)
        for errors in self.window_errors:
            self.assertEqual(errors, [], 'Unexpected production Quick error')
        super().tearDown()

    def render(self):
        self.w._render_plot_page()
        pump()
        page = self.w.plot_page.rendered_page
        self.assertIsNotNone(page, self.w.plot_page.status_text)
        return page

    def canvas(self):
        return self.w.graph_documents.content_item(self.w._document_key())

    def focus_canvas(self):
        viewport = quick_item(self.canvas(), 'formalViewport')
        viewport.forceActiveFocus()
        pump()
        self.assertTrue(viewport.hasActiveFocus())

    def properties(self):
        return quick_item(self.w, 'applicationProperties')

    def formal_properties(self):
        return quick_item(self.w, 'formalProperties')

    def menu_item(self, group, action):
        bar = self.w.root_window.property('menuBar')
        entry = next(item for item in bar.property('contentItem').childItems()
                     if item.property('text') == group)
        self.click_control(entry)
        control = next(item for item in self.w.root_window.findChildren(QQuickItem)
                       if item.inherits('QQuickMenuItem') and item.isVisible()
                       and item.property('text') == action.text())
        self.assertTrue(control.isEnabled())
        return control

    def click_control(self, control):
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        QTest.mouseClick(input_receiver(self.w, control), Qt.LeftButton, pos=point)
        pump()

    def object_point(self, key):
        preview = self.w.plot_page.preview
        self.assertTrue(hasattr(preview.page, 'object_bounds'), 'Formal renderer must expose visible object geometry')
        left, bottom, width, height = preview.page.object_bounds[key][0]
        paper_width, paper_height = preview.paper_display_size()
        return self.paper_point(QPointF((left+width/2)*paper_width,
                                       (1-bottom-height/2)*paper_height))

    def paper_point(self, point):
        preview = self.w.plot_page.preview
        viewport = quick_item(self.canvas(), 'formalViewport')
        for axis, value, extent in (('X', point.x(), viewport.width()),
                                    ('Y', point.y(), viewport.height())):
            maximum = max(0., viewport.property('contentWidth' if axis == 'X' else 'contentHeight') - extent)
            viewport.setProperty('content'+axis, min(max(0., value-extent/2), maximum))
        pump()
        return quick_item(self.canvas(), 'formalPaper').mapToScene(QPointF(point)).toPoint()

    def double_click(self, point):
        QTest.mouseDClick(self.w.root_window, Qt.LeftButton, pos=point)
        QTest.mouseRelease(self.w.root_window, Qt.LeftButton, pos=point)
        pump()

    def test_shared_labels_remain_editable_in_plot_across_modes_and_dataset_switches(self):
        w, p = self.w, self.w.plot_page
        fields = ('plot_title', 'y_axis_name', 'y_axis_unit', 'signal_title')
        editors = tuple(dat.dat_item(w, field) for field in fields)
        for _ in range(3):
            for field, editor in zip(fields, editors):
                dat.reveal(w, field)
                pump()
                self.assertTrue(editor.isVisible(), 'Existing label editor is hidden while Plot is active')
                self.assertTrue(self.properties().isAncestorOf(dat.dat_item(w, 'plot_title')))
            w.request_outer_view('analysis')
            self.assertTrue(self.properties().isAncestorOf(dat.dat_item(w, 'plot_title')))
            w.request_outer_view('workspace')
        w.dat_properties.setLabel('plot_title', 'Shared title', w.dat_properties.ownerToken)
        w.dat_properties.setLabel('y_axis_name', 'Field', w.dat_properties.ownerToken)
        w.dat_properties.setLabel('y_axis_unit', 'T', w.dat_properties.ownerToken)
        w.dat_properties.setLabel('signal_title', 'Custom PL (arb. units)', w.dat_properties.ownerToken)
        self.assertFalse(dat.state(w)['signal_title_auto'])
        page = self.render()
        self.assertIn('Shared title', page.svg_bytes.decode())
        self.assertIn('Field (T)', page.svg_bytes.decode())
        owner = w.workspace.active_dataset_id
        other = self.folder / 'other.dat'
        other.write_bytes(self.path.read_bytes())
        w._load_dat_file_direct(other)
        w.dat_properties.setLabel('plot_title', 'Other', w.dat_properties.ownerToken)
        w.activate_dataset(owner)
        self.assertEqual(dat.state(w)['plot_title'], 'Shared title')
        self.assertIs(editors[0], dat.dat_item(w, 'plot_title'))
        self.assertEqual(w.outerView, 'workspace')
        self.assertEqual(w.graph_documents.registry.active_key, w._document_key())

    def test_doubleclick_title_and_annotation_reveals_and_focuses_existing_editors(self):
        w, p = self.w, self.w.plot_page
        w.dat_properties.setLabel('plot_title', 'Editable title', w.dat_properties.ownerToken)
        self.render()
        w.request_task(0)
        self.double_click(self.object_point('title'))
        pump()
        self.assertEqual(w.taskIndex, 2)
        self.assertTrue(self.formal_properties().isVisible())
        self.assertTrue(dat.dat_item(w, 'plot_title').hasActiveFocus())
        self.assertTrue(dat.dat_item(w, 'plot_title').isVisible())
        p.add_annotation('text', 'Manual note')
        self.render()
        item = w.workspace.active.presentation.annotations[0]
        self.double_click(self.object_point('annotation:'+item.id))
        pump(60)
        editor = quick_item(self.formal_properties(), 'annotation_text')
        self.assertEqual(editor.property('text'), 'Manual note')
        self.assertTrue(editor.hasActiveFocus())
        viewport = quick_item(self.properties(), 'propertiesScroll').property('contentItem')
        point = editor.mapToItem(viewport, QPointF(editor.width()/2, editor.height()/2))
        self.assertTrue(0 <= point.x() <= viewport.width() and 0 <= point.y() <= viewport.height())
        self.assertTrue(self.properties().isAncestorOf(editor))

    def test_doubleclick_displayed_axes_and_regional_bar_uses_zoomed_geometry(self):
        w, p = self.w, self.w.plot_page
        dat.set_choice(w, 'color_scale', '2')
        w.set_plot_presentation(replace(w.workspace.active.presentation, height_in=5.,
            axis=replace(w.workspace.active.presentation.axis, exchange_xy=True, flip_x=True, flip_y=True)))
        self.render()
        p.set_zoom('200%')
        pump()
        self.double_click(self.object_point('horizontal-label'))
        wait_until(lambda: dat.dat_item(w, 'y_axis_name').hasActiveFocus(), timeout=1)
        self.assertTrue(dat.dat_item(w, 'y_axis_name').hasActiveFocus(), 'Exchanged horizontal label represents the original Y label')
        self.double_click(self.object_point('vertical-axis'))
        pump(60)
        self.assertTrue(quick_item(self.formal_properties(), 'axis_y_mode').hasActiveFocus())
        left, bottom, width, height = p.rendered_page.colorbar_bounds['region-2']
        paper_width, paper_height = p.preview.paper_display_size()
        point = self.paper_point(QPointF((left+width/2)*paper_width, (1-bottom-height/2)*paper_height))
        self.double_click(point)
        pump(60)
        self.assertEqual(p.formal.state['selectedBar'], 'region-2')
        self.assertTrue(quick_item(self.formal_properties(), 'tick_values').hasActiveFocus()
                        or quick_item(self.formal_properties(), 'custom_ticks').hasActiveFocus())
        self.double_click(self.object_point('signal-label'))
        wait_until(lambda: dat.dat_item(w, 'signal_title').hasActiveFocus(), timeout=1)
        self.assertTrue(dat.dat_item(w, 'signal_title').hasActiveFocus())

    def test_plot_axis_range_editors_reuse_source_bounds_and_exchange_mapping(self):
        w, p = self.w, self.w.plot_page
        self.assertTrue(self.properties().isAncestorOf(dat.dat_item(w, 'plot_title')), 'Source bounds must remain accessible in Plot')
        w.set_plot_presentation(replace(w.workspace.active.presentation, axis=replace(
            w.workspace.active.presentation.axis, exchange_xy=True)))
        self.render()
        self.double_click(self.object_point('horizontal-range'))
        wait_until(lambda: dat.dat_item(w, 'ymin').hasActiveFocus(), timeout=1)
        self.assertTrue(dat.dat_item(w, 'ymin').hasActiveFocus())
        self.assertTrue(dat.dat_item(w, 'ymin').isVisible())
        left, bottom, width, height = p.rendered_page.axes_bounds
        paper_width, paper_height = p.preview.paper_display_size()
        upper = QPointF((left+width/2)*paper_width, (1-bottom-height)*paper_height)
        right = QPointF((left+width)*paper_width, (1-bottom-height/2)*paper_height)
        self.double_click(self.paper_point(right))
        wait_until(lambda: dat.dat_item(w, 'xmin').hasActiveFocus(), timeout=1)
        self.assertTrue(dat.dat_item(w, 'xmin').hasActiveFocus(), 'Right frame must edit original X after exchange')
        self.double_click(self.paper_point(upper))
        wait_until(lambda: dat.dat_item(w, 'ymin').hasActiveFocus(), timeout=1)
        self.assertTrue(dat.dat_item(w, 'ymin').hasActiveFocus(), 'Upper frame must edit original Y after exchange')
        dat.setup_number(w, 'ymin', .4)
        self.assertEqual(w.outerView, 'workspace')
        self.assertEqual(w.graph_documents.registry.active_key, w._document_key())
        self.assertEqual(w.workspace.active.view.y_limits[0], .4)

    def test_stale_hidden_and_clipped_objects_cannot_open_property_editors(self):
        w, p = self.w, self.w.plot_page
        w.dat_properties.setLabel('plot_title', 'Editable title', w.dat_properties.ownerToken)
        self.render()
        self.assertTrue(hasattr(p, 'propertyEditRequested'), 'Doubleclick editing signal is missing')
        events = []
        p.propertyEditRequested.connect(lambda *args: events.append(args))
        title_point = self.object_point('title')
        w._invalidate_plot_page(schedule=False)
        self.double_click(title_point)
        self.assertEqual(events, [])
        self.render()
        w.request_outer_view('analysis')
        self.double_click(title_point)
        self.assertEqual(events, [])
        w.request_outer_view('workspace')
        w.set_plot_presentation(replace(w.workspace.active.presentation, axis=replace(
            w.workspace.active.presentation.axis, show_x=False, show_y=False),
            colorbar=replace(w.workspace.active.presentation.colorbar, visible=False, show_label=False)))
        page = self.render()
        self.assertNotIn('horizontal-axis', page.object_bounds)
        self.assertNotIn('vertical-axis', page.object_bounds)
        self.assertFalse(any(key.startswith('colorbar:') for key in page.object_bounds))
        w.dat_properties.setLabel('plot_title', 'Long clipped title ' * 8, w.dat_properties.ownerToken)
        w.set_plot_presentation(replace(w.workspace.active.presentation, title_position=(.9,.85)))
        page = self.render()
        left, bottom, width, height = page.axes_bounds
        paper_width, _ = p.preview.paper_display_size()
        outside = self.paper_point(QPointF((left+width)*paper_width+8, p.preview.title_point().y()+5))
        self.double_click(outside)
        self.assertEqual(events, [])

    def test_copy_graph_commits_fresh_hybrid_svg_identical_to_export(self):
        self.addCleanup(QGuiApplication.clipboard().clear)
        w, p = self.w, self.w.plot_page
        self.assertTrue(hasattr(w, 'copy_graph_svg'), 'Formal Copy SVG command is missing')
        before = w.workspace.active.current_cube.z.copy()
        p.formal.setValue('aspect_locked', False)
        p.formal.setValue('width_in', 4.)
        p.formal.setValue('height_in', 4.)
        p.formal.setDraft('heatmap_dpi', '150')
        w.dat_properties.setLabel('plot_title', 'Fresh clipboard title', w.dat_properties.ownerToken)
        page = w.copy_graph_svg()
        mime = QGuiApplication.clipboard().mimeData()
        self.assertEqual(mime.formats(), ['image/svg+xml'])
        self.assertFalse(mime.hasImage())
        self.assertEqual(page.heatmap_dpi, 150)
        self.assertEqual(bytes(mime.data('image/svg+xml')), page.svg_bytes)
        self.assertIn('Fresh clipboard title', bytes(mime.data('image/svg+xml')).decode())
        self.assertEqual(page.svg_bytes.count(b'data:image/png;base64,'), 1)
        self.assertNotIn('editing-handle', page.svg_bytes.decode())
        exported = w.export_plot_page('svg')
        self.assertEqual(bytes(mime.data('image/svg+xml')), exported.read_bytes())
        np.testing.assert_array_equal(w.workspace.active.current_cube.z, before)

    def test_copy_graph_invalid_editor_and_empty_window_preserve_clipboard(self):
        w, p = self.w, self.w.plot_page
        self.assertTrue(hasattr(w, 'copy_graph_svg'), 'Formal Copy SVG command is missing')
        QGuiApplication.clipboard().setText('Keep this clipboard')
        p.formal.setDraft('tick_values', 'bad ticks')
        p.formal.setValue('custom_ticks', True)
        for copy in (w.copy_graph_svg, w.copy_graph_png):
            with self.assertRaises(ValueError):
                copy()
            self.assertEqual(QGuiApplication.clipboard().text(), 'Keep this clipboard')
        empty_host, _, errors = self.enterContext(application())
        self.window_errors.append(errors)
        empty = empty_host.controller
        for copy in (empty.copy_graph_svg, empty.copy_graph_png):
            with self.assertRaises(ValueError):
                copy()
            self.assertEqual(QGuiApplication.clipboard().text(), 'Keep this clipboard')

    def test_formal_right_click_copies_fresh_svg_and_png(self):
        from spectra_app.svg_export import render_svg_image
        self.addCleanup(QGuiApplication.clipboard().clear)
        w, p = self.w, self.w.plot_page
        p.formal.setValue('aspect_locked', False)
        p.formal.setValue('width_in', 4.)
        p.formal.setValue('height_in', 3.)
        p.formal.setDraft('heatmap_dpi', '150')
        w.dat_properties.setLabel('plot_title', 'Clipboard menu title', w.dat_properties.ownerToken)
        w._finish_graph_editors()
        self.render()
        for name, kind in (('formalCopySvg', 'svg'), ('formalCopyPng', 'png')):
            with self.subTest(kind=kind):
                point = self.paper_point(QPointF(30, 30))
                QTest.mouseClick(w.root_window, Qt.RightButton, pos=point)
                pump(60)
                self.assertTrue(self.canvas().findChild(QObject, 'formalFormatMenu').property('visible'))
                self.assertEqual(quick_item(self.canvas(), name).property('text'), 'Copy ' + kind.upper())
                self.assertTrue(quick_item(self.canvas(), name).isEnabled())
                click_item(self.canvas(), name)
                page = p.rendered_page
                mime = QGuiApplication.clipboard().mimeData()
                self.assertEqual(page.heatmap_dpi, 150)
                self.assertIn(b'Clipboard menu title', page.svg_bytes)
                if kind == 'svg':
                    self.assertEqual(mime.formats(), ['image/svg+xml'])
                    self.assertEqual(bytes(mime.data('image/svg+xml')), page.svg_bytes)
                else:
                    encoded = bytes(mime.data('image/png'))
                    self.assertTrue(encoded.startswith(b'\x89PNG\r\n\x1a\n'))
                    decoded = QImage.fromData(encoded, 'PNG')
                    expected = render_svg_image(page)
                    self.assertFalse(decoded.isNull())
                    self.assertTrue(mime.hasImage())
                    self.assertEqual(decoded.size(), expected.size())
                    decoded_rgba = decoded.convertToFormat(QImage.Format_RGBA8888)
                    expected_rgba = expected.convertToFormat(QImage.Format_RGBA8888)
                    self.assertEqual(bytes(decoded_rgba.constBits()), bytes(expected_rgba.constBits()))
                    self.assertEqual(QGuiApplication.clipboard().image().size(), expected.size())
                    self.assertAlmostEqual(decoded.dotsPerMeterX() * .0254, page.heatmap_dpi, delta=.02)
                    self.assertAlmostEqual(decoded.dotsPerMeterY() * .0254, page.heatmap_dpi, delta=.02)

    def test_copy_png_render_and_encoding_failures_preserve_clipboard(self):
        self.addCleanup(QGuiApplication.clipboard().clear)
        for target, kwargs in (
                ('spectra_app.main_window.render_svg_image', {'side_effect': ValueError('Cannot render')}),
                ('PySide6.QtGui.QImage.save', {'return_value': False})):
            with self.subTest(target=target):
                QGuiApplication.clipboard().setText('Preserve on failure')
                with patch(target, **kwargs), self.assertRaises(ValueError):
                    self.w.copy_graph_png()
                self.assertEqual(QGuiApplication.clipboard().text(), 'Preserve on failure')

    def test_shared_menus_import_shortcut_and_native_text_copy(self):
        self.addCleanup(QGuiApplication.clipboard().clear)
        w, p = self.w, self.w.plot_page
        self.assertTrue(hasattr(w, '_graph_command_actions'), 'Shared File/Edit/View commands are missing')
        bar = w.root_window.property('menuBar')
        self.assertEqual([entry.property('text') for entry in bar.property('contentItem').childItems()
                          if entry.property('text')],
                         ['File', 'Edit', 'View', 'Analysis', 'Help', 'Recent projects'])
        self.assertEqual(quick_item(w, 'formal_copy_svg').property('text'), 'Copy SVG')
        self.assertEqual(w._graph_command_actions['copy_graph'].text(), 'Copy Graph as SVG')
        extra = self.folder / 'shortcut.dat'
        extra.write_bytes(self.path.read_bytes())
        self.focus_canvas()
        with patch('spectra_app.main_window.FilePicker.getOpenFileNames', return_value=([str(extra)], 'DAT Files (*.dat)')):
            QTest.keyClick(w.root_window, Qt.Key_O, Qt.ControlModifier)
            wait_for_background_jobs(w)
        imported = w.workspace.dataset_for_path(extra)
        self.assertIsNotNone(imported)
        imported_key = w.graph_documents.registry.active_key
        self.assertEqual(imported_key[0], 'workbook')
        imported_page = w.graph_documents.document_for(imported_key)
        imported_sheet = w.workspace.sheets[imported_page.currentSheetId]
        self.assertEqual(imported_sheet.dataset_id, imported.dataset_id)
        self.assertEqual(imported_sheet.source_path, extra)
        w.open_graph_document(imported.dataset_id)
        self.assertIs(w.workspace.active, imported)
        self.assertEqual(w.graph_documents.registry.active_key, w._document_key())
        p = w.plot_page
        w.dat_properties.setLabel('plot_title', 'Native copy text', w.dat_properties.ownerToken)
        dat.reveal(w, 'plot_title')
        QTest.keyClick(w.root_window, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(w.root_window, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(QGuiApplication.clipboard().text(), 'Native copy text')
        self.assertFalse(QGuiApplication.clipboard().mimeData().hasImage())
        self.focus_canvas()
        QTest.keyClick(w.root_window, Qt.Key_J, Qt.ControlModifier | Qt.AltModifier)
        self.assertEqual(QGuiApplication.clipboard().mimeData().formats(), ['image/svg+xml'], self.errors)
        self.assertFalse(QGuiApplication.clipboard().mimeData().hasImage())
        QGuiApplication.clipboard().setText('Graph shortcut replaces this')
        self.focus_canvas()
        QTest.keyClick(w.root_window, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(QGuiApplication.clipboard().mimeData().formats(), ['image/svg+xml'], self.errors)
        self.assertFalse(QGuiApplication.clipboard().mimeData().hasImage())

    def test_rescale_shortcut_is_text_safe_and_escape_cancels_drag_without_edit(self):
        w, p = self.w, self.w.plot_page
        self.assertTrue(hasattr(w, '_graph_command_actions'), 'Graph shortcuts are missing')
        w._heat_ax.set_xlim(1.2,1.5)
        w._sync_formal_plot_view()
        dat.reveal(w, 'plot_title')
        QTest.keyClick(w.root_window, Qt.Key_R, Qt.ControlModifier)
        self.assertEqual(tuple(w._heat_ax.get_xlim()), (1.2,1.5))
        self.render()
        self.focus_canvas()
        QTest.keyClick(w.root_window, Qt.Key_R, Qt.ControlModifier)
        self.assertLess(w._heat_ax.get_xlim()[0], 1.2)
        self.assertGreater(w._heat_ax.get_xlim()[1], 1.5)
        self.render()
        p.add_annotation('text','Canceled move')
        self.render()
        item = w.workspace.active.presentation.annotations[0]
        start = self.paper_point(p.preview.annotation_point(item))
        QTest.mousePress(w.root_window, Qt.LeftButton, pos=start)
        QTest.mouseMove(w.root_window, start+QPoint(25,15), delay=10)
        QTest.keyClick(w.root_window, Qt.Key_Escape)
        QTest.mouseRelease(w.root_window, Qt.LeftButton, pos=start+QPoint(25,15))
        self.assertEqual(w.workspace.active.presentation.annotations[0], item)
        self.assertIsNone(p.preview.selected_id)

    def test_explicit_rescale_menu_command_runs_while_a_text_editor_keeps_focus(self):
        w = self.w
        w._heat_ax.set_xlim(1.2,1.5)
        dat.reveal(w, 'plot_title')
        editor = dat.dat_item(w, 'plot_title')
        self.assertTrue(editor.hasActiveFocus())
        control = self.menu_item('View', w._graph_command_actions['rescale'])
        self.click_control(control)
        self.assertLess(w._heat_ax.get_xlim()[0], 1.2)
        self.assertGreater(w._heat_ax.get_xlim()[1], 1.5)

    def test_escape_cancels_analysis_peak_drag_before_mouse_release(self):
        w = self.w
        w.request_outer_view('analysis')
        w._add_manual_peak(1.3)
        before = w.peak_indices.copy()
        ac.reveal(w)
        w.canvas.draw()
        y = float(np.mean(w._spec_ax.get_ylim()))
        start = ac.data_point(w, w._spec_ax, (1.3, y))
        end = ac.data_point(w, w._spec_ax, (1.8, y), ensure_visible=False)
        ac.press(w, start)
        self.assertIsNotNone(w._drag_peak_arr_idx)
        ac.move(w, end)
        self.assertIsNotNone(w._drag_scatter)
        QTest.keyClick(w.root_window, Qt.Key_Escape)
        ac.release(w, end)
        np.testing.assert_array_equal(w.peak_indices, before)
        self.assertIsNone(w._motion_cid)
        self.assertIsNone(w._drag_scatter)


if __name__ == '__main__':
    unittest.main()
