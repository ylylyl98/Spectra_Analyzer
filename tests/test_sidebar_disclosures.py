"""Sidebar navigation preserves drafts and reveals the editor a user requests."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from copy import deepcopy
from unittest.mock import patch
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from spectra_app.plot_presentation import PlotAnnotation
from tests.quick_migration_case import QuickMigrationCase
from tests.dat_properties_quick_helpers import dat_item, edit_number, click
from tests.formal_quick_helpers import (click_item, edit_field, item, pump,
                                        ensure_visible)


def properties(page):
    return item(page.parent(), 'formalProperties')


def viewport(page):
    return item(page.parent(), 'propertiesScroll').property('contentItem')


def quick_viewport_point(page, control):
    return control.mapToItem(viewport(page), QPointF(control.width() / 2, control.height() / 2))


def reveal_quick(page, control):
    ensure_visible(control)
    pump()


def setup_document(case, width, height):
    QuickMigrationCase.setUp(case)
    case.addCleanup(lambda: case.assertEqual(case.quick_errors, []))
    path = case.folder / 'spectrum.dat'
    path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
        f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(15)))
    case.w = case.controller
    case.w._load_dat_file_direct(path)
    case.page = case.w.open_graph_document()
    case.host.window.resize(width, height)
    case.host.window.requestActivate()
    case.assertTrue(case.w.request_task(2))
    pump()
    case.w._render_plot_page()
    case.w._plot_render_timer.stop()
    pump(100)


class SidebarDisclosureTests(QuickMigrationCase):
    def setUp(self):
        setup_document(self, 1000, 850)
        self.changes = []
        self.page.presentationChanged.connect(self.changes.append)

    def header(self, title):
        sections = {'Font and export': 'formal_font_export',
                    'Frame and title layout': 'formal_frame', 'Axes and ticks': 'formal_axes',
                    'Colorbar layout': 'formal_bar_layout', 'Custom ticks': 'formal_custom_ticks',
                    'Text and arrows': 'formal_annotations'}
        section = item(properties(self.page), sections[title])
        headers = properties(self.page).findChildren(
            QQuickItem, sections[title] + '_toggle')
        self.assertEqual(len(headers), 1, f'{title} must be an accessible disclosure')
        self.assertEqual(section.property('title'), title)
        return section, item(properties(self.page), sections[title] + '_toggle')

    def click_header(self, title):
        _section, header = self.header(title)
        reveal_quick(self.page, header)
        click_item(self.w, header.objectName())
        return header

    def test_common_controls_visible_and_advanced_controls_collapsed(self):
        p = self.page
        # The retained production Properties starts publication sections closed.
        # Native disclosure input makes the same common controls available.
        for section in ('formal_page', 'formal_colorbar'):
            self.assertFalse(item(properties(p), section).property('expanded'))
            click_item(self.w, section + '_toggle')
            self.assertTrue(item(properties(p), section).property('expanded'))
        for field in ('width_in', 'height_in', 'numeric_pt', 'text_pt',
                      'bar_visible', 'bar_label_format', 'bar_precision'):
            self.assertTrue(item(properties(p), field).isVisible(), field)
        for title, field in (('Font and export', 'heatmap_dpi'),
                             ('Frame and title layout', 'title_x'),
                             ('Axes and ticks', 'axis_x_mode'),
                             ('Colorbar layout', 'bar_width_in'),
                             ('Custom ticks', 'tick_values'),
                             ('Text and arrows', 'annotation_text')):
            section, _header = self.header(title)
            self.assertFalse(section.property('expanded'))
            self.assertFalse(item(properties(p), field).isVisible(), field)

    def test_mouse_collapse_preserves_valid_and_invalid_numeric_drafts(self):
        p = self.page
        section, header = self.header('Frame and title layout')
        for draft in ('0.12345678', '-'):
            with self.subTest(draft=draft):
                control = edit_field(self.w, 'title_x', draft)
                old = p.presentation.title_position[0], p.presentation
                self.click_header('Frame and title layout')
                self.assertEqual((p.presentation.title_position[0], p.presentation), old)
                self.assertEqual(control.property('text'), draft)
                self.assertTrue(header.hasActiveFocus())
                QTest.keyClick(self.host.window, Qt.Key_Space)
                pump()
                self.assertTrue(section.property('expanded'))
                self.assertTrue(control.isVisible())
                self.assertEqual(control.property('text'), draft)
                self.assertEqual(self.changes, [])

    def test_header_keyboard_navigation_preserves_other_section_draft(self):
        p = self.page
        control = edit_field(self.w, 'width_in', '7.12345678')
        before = p.presentation
        section, header = self.header('Font and export')
        reveal_quick(p, header)
        header.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(self.host.window, Qt.Key_Return)
        pump()
        self.assertTrue(section.property('expanded'))
        QTest.keyClick(self.host.window, Qt.Key_Left)
        pump()
        self.assertFalse(section.property('expanded'))
        QTest.keyClick(self.host.window, Qt.Key_Right)
        pump()
        self.assertTrue(section.property('expanded'))
        self.assertEqual(control.property('text'), '7.12345678')
        self.assertEqual(p.presentation, before)
        self.assertEqual(self.changes, [])

    def test_hidden_drafts_are_committed_or_revealed_on_validation_error(self):
        p = self.page
        section, _header = self.header('Frame and title layout')
        section.setProperty('expanded', True)
        p.formal.setDraft('title_x', '0.12345678')
        section.setProperty('expanded', False)
        p.finish_editor()
        self.assertEqual(p.presentation.title_position[0], .12345678)
        section.setProperty('expanded', True)
        p.formal.setDraft('title_x', '-')
        section.setProperty('expanded', False)
        with self.assertRaisesRegex(ValueError, 'finite number'):
            p.finish_editor()
        pump(80)
        control = item(properties(p), 'title_x')
        self.assertTrue(section.property('expanded'))
        self.assertTrue(control.isVisible())
        self.assertEqual(control.property('text'), '-')
        self.assertTrue(control.hasActiveFocus())

    def test_property_and_annotation_selection_reveal_existing_editor(self):
        p = self.page
        editor = item(properties(p), 'axis_x_mode')
        self.assertTrue(p.reveal_property('horizontal-axis'))
        pump(70)
        self.assertIs(item(properties(p), 'axis_x_mode'), editor)
        self.assertTrue(editor.isVisible())
        editor = item(properties(p), 'custom_ticks')
        self.assertTrue(p.reveal_property('colorbar', 'global'))
        pump(70)
        self.assertIs(item(properties(p), 'custom_ticks'), editor)
        self.assertTrue(editor.isVisible())
        p.set_presentation(replace(p.presentation, annotations=(PlotAnnotation('note', text='Sample'),)))
        self.assertFalse(item(properties(p), 'annotation_text').isVisible())
        p.select_annotation('note')
        pump(70)
        self.assertTrue(item(properties(p), 'annotation_text').isVisible())
        self.assertFalse(item(properties(p), 'annotation_target_x').isVisible(),
                         'A text note has no arrow head')
        editor = item(properties(p), 'annotation_text')
        self.assertTrue(p.reveal_property('annotation', 'note'))
        pump(70)
        self.assertIs(item(properties(p), 'annotation_text'), editor)
        self.assertTrue(editor.hasActiveFocus())
        self.assertEqual(p.formal.state['selectedAnnotation'], 'note')

    def test_invalid_hidden_custom_ticks_are_revealed_without_losing_text(self):
        p = self.page
        section, _header = self.header('Custom ticks')
        section.setProperty('expanded', True)
        p.formal.setValue('custom_ticks', True)
        pump(70)
        control = edit_field(self.w, 'tick_values', '0, invalid, 10')
        self.click_header('Custom ticks')
        self.assertFalse(control.isVisible())
        with self.assertRaises(ValueError):
            p.finish_editor()
        pump(70)
        self.assertTrue(control.isVisible())
        self.assertTrue(control.hasActiveFocus())
        self.assertEqual(control.property('text'), '0, invalid, 10')


class WorkspaceDisclosureTests(QuickMigrationCase):
    def setUp(self):
        setup_document(self, 1500, 920)

    def test_section_navigation_preserves_graph_and_does_not_render(self):
        w, p = self.w, self.w.plot_page
        before = (deepcopy(w.workspace.active.active_graph.view), p.presentation,
                  deepcopy(w._graph_revisions), w.workspace.active.current_cube.z.copy())
        with patch.object(w, '_render_plot_page') as render, \
                patch.object(w.canvas, 'draw') as draw, \
                patch.object(w, '_layout_live_plots') as layout:
            w.dat_properties.setDisclosure('templates', True, w.dat_properties.ownerToken)
            header = click(w, 'shared_templates_toggle')
            QTest.keyClick(self.host.window, Qt.Key_Space)
            QTest.keyClick(self.host.window, Qt.Key_Left)
            for name in ('formal_frame', 'formal_axes', 'formal_bar_layout'):
                section = item(properties(p), name)
                section.setProperty('expanded', True)
                header = item(properties(p), name + '_toggle')
                reveal_quick(p, header)
                click_item(self.w, header.objectName())
                QTest.keyClick(self.host.window, Qt.Key_Space)
                QTest.keyClick(self.host.window, Qt.Key_Left)
                pump()
            QTest.qWait(250)
            self.assertFalse(w._plot_render_timer.isActive())
            render.assert_not_called()
            draw.assert_not_called()
            layout.assert_not_called()
        self.assertEqual(w.workspace.active.active_graph.view, before[0])
        self.assertEqual(p.presentation, before[1])
        self.assertEqual(w._graph_revisions, before[2])
        self.assertTrue((w.workspace.active.current_cube.z == before[3]).all())

    def test_shared_disclosure_preserves_valid_and_invalid_drafts_and_reveals_error(self):
        w, p = self.w, self.w.plot_page
        section = dat_item(w, 'shared_ranges_section')
        for draft in ('1.1234', '-'):
            with self.subTest(draft=draft):
                before = deepcopy(w.workspace.active.view)
                control = edit_number(w, 'xmin', draft)
                header = click(w, 'shared_ranges_toggle')
                self.assertFalse(section.property('expanded'))
                self.assertFalse(control.isVisible())
                self.assertTrue(header.hasActiveFocus())
                self.assertEqual(control.property('text'), draft)
                self.assertEqual(w.workspace.active.view, before)
                QTest.keyClick(self.host.window, Qt.Key_Right)
                pump()
                self.assertTrue(section.property('expanded'))
                self.assertEqual(control.property('text'), draft)
                click(w, 'shared_ranges_toggle')
                if draft == '-':
                    with self.assertRaisesRegex(ValueError, 'pending xmin editor'):
                        w._finish_graph_editors(capture_science=False)
                    pump(80)
                    self.assertEqual(w.workspace.active.view, before)
                    self.assertTrue(section.property('expanded'))
                    self.assertTrue(control.hasActiveFocus())
                    self.assertEqual(control.property('text'), draft)
                    # Cleanup is a genuine correction of the same user draft.
                    edit_number(w, 'xmin', str(before.bounds['xmin']), commit=True)
                else:
                    w._finish_graph_editors(capture_science=False)
                    self.assertEqual(w.workspace.active.view.x_limits[0], 1.1234)

    def test_property_navigation_reveals_shared_and_page_owned_fields(self):
        w, p = self.w, self.w.plot_page
        for kind, field in (('title', 'plot_title'), ('signal-label', 'signal_title'),
                            ('horizontal-label', 'axis_override'),
                            ('horizontal-range', 'xmin')):
            with self.subTest(kind=kind):
                w._show_plot_property(kind, '')
                pump(80)
                editor = dat_item(w, field)
                self.assertTrue(editor.isVisible())
                self.assertTrue(editor.hasActiveFocus())
                self.assertTrue(QRectF(0, 0, viewport(p).width(), viewport(p).height()).contains(
                    quick_viewport_point(p, editor)))
        for kind, field in (('horizontal-axis', 'axis_x_mode'), ('colorbar', 'custom_ticks')):
            with self.subTest(kind=kind):
                w._show_plot_property(kind, 'global' if kind == 'colorbar' else '')
                pump(80)
                editor = item(properties(p), field)
                self.assertTrue(editor.isVisible())
                self.assertTrue(editor.hasActiveFocus())
                self.assertTrue(QRectF(0, 0, viewport(p).width(), viewport(p).height()).contains(quick_viewport_point(p, editor)))
                self.assertIs(self.host.window.activeFocusItem(), editor)

    def test_preview_doubleclick_reveals_collapsed_axes_and_annotation(self):
        w, p = self.w, self.w.plot_page
        annotation = p.add_annotation('text', 'Manual note')
        w._render_plot_page()
        pump()
        for kind, field in (('vertical-axis', 'axis_y_mode'),
                            ('annotation:' + annotation.id, 'annotation_text')):
            editor = item(properties(p), field)
            self.assertFalse(editor.isVisible())
            left, bottom, width, height = p.rendered_page.object_bounds[kind][0]
            paper_width, paper_height = p.preview.paper_display_size()
            paper = item(w.graph_documents.content_item(w.graph_documents.registry.active_key), 'formalPaper')
            point = paper.mapToScene(QPointF((left + width / 2) * paper_width,
                (1 - bottom - height / 2) * paper_height)).toPoint()
            QTest.mouseDClick(self.host.window, Qt.LeftButton, pos=point)
            QTest.mouseRelease(self.host.window, Qt.LeftButton, pos=point)
            pump(80)
            self.assertTrue(editor.isVisible())
            self.assertTrue(editor.hasActiveFocus())
            self.assertTrue(QRectF(0, 0, viewport(p).width(), viewport(p).height()).contains(quick_viewport_point(p, editor)))


if __name__ == '__main__':
    import unittest
    unittest.main()
