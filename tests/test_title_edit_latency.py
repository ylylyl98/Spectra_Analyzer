import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
import sys
from PySide6.QtCore import QCoreApplication
from tests.quick_migration_case import QuickMigrationCase
from unittest.mock import patch
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
import numpy as np
from tests import test_annotation_persistence as helpers
from spectra_app.image_export import _build_heatmap_export_figure
from tests.formal_quick_helpers import click_item
from tests.dat_properties_quick_helpers import (
    edit_label, reveal, choose,
    click, state, set_choice)


class TitleEditLatencyTests(QuickMigrationCase):
    def setUp(self):
        if sys.platform == 'win32' and self._testMethodName == 'test_focus_out_preset_and_auto_each_commit_once':
            # Real preset popup input needs the native Windows presentation.
            self.enterContext(patch.dict(os.environ, QT_QPA_PLATFORM='windows'))
        super().setUp()
        self.settings_path = self.folder / 'profile.ini'
        self.windows = []
        self.host_errors = [self.quick_errors]

    def tearDown(self):
        for errors in self.host_errors:
            self.assertEqual(errors, [])
        super().tearDown()

    make_window = helpers.AnnotationPersistenceTests.make_window
    load_source = helpers.AnnotationPersistenceTests.load_source
    close_window = helpers.AnnotationPersistenceTests.close_window

    def test_colorbar_draft_retains_formal_preview_until_finish(self):
        window = self.make_window()
        path = self.settings_path.parent / 'label.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        window._load_dat_file_direct(path)
        page = window.open_graph_document()
        window.root_window.resize(1500, 920)
        window.root_window.show()
        QCoreApplication.processEvents()
        window._render_plot_page()
        page.set_zoom('150%')
        old = page.rendered_page
        scene = window.graph_documents.content_item(window._document_key())
        size = (scene.width(), scene.height())
        data = window.current_cube.z.copy()
        editor = reveal(window, 'signal_title')
        with patch.object(window, '_render_plot_page', wraps=window._render_plot_page) as render, \
             patch.object(window._annotation_settings, 'sync', wraps=window._annotation_settings.sync) as sync:
            edit_label(window, 'signal_title', 'Custom colorbar label')
            QTest.qWait(400)
            self.assertEqual(page.preview.svg_bytes, old.svg_bytes)
            self.assertEqual((scene.width(), scene.height()), size)
            self.assertIsNone(page.rendered_page, 'Old displayed bytes must not be exportable as current')
            self.assertTrue(page.command_enabled('export_svg'), 'Export remains a reachable commit action')
            self.assertFalse(page.preview.canEdit, 'Stale geometry must not receive editing gestures')
            before = page.presentation
            requests = []
            page.preview.propertyEditRequested.connect(lambda *args: requests.append(args))
            left, bottom, width, height = old.object_bounds['signal-label'][0]
            paper_width, paper_height = page.preview.paper_display_size()
            self.assertFalse(page.preview.doubleClick(
                (left + width / 2) * paper_width, (1 - bottom - height / 2) * paper_height))
            self.assertEqual(requests, [])
            self.assertEqual(page.presentation, before)
            self.assertEqual(render.call_count, 0)
            self.assertEqual(sync.call_count, 0)
            QTest.keyClick(window.root_window, Qt.Key_Return)
            QTest.qWait(100)
            self.assertEqual(render.call_count, 1)
            self.assertEqual(sync.call_count, 1)
            self.assertIn('Custom colorbar label', page.rendered_page.svg_bytes.decode())
            self.assertTrue(page.preview.canEdit)
            reveal(window, 'plot_title')
            QTest.qWait(100)
            self.assertEqual(render.call_count, 1)
        np.testing.assert_array_equal(window.current_cube.z, data)
    def test_typing_waits_for_finish_to_render_and_write_settings(self):
        window = self.make_window()
        self.load_source(window)
        self.assertTrue(window.request_outer_view('analysis'))
        set_choice(window, 'color_scale', '3')
        window.root_window.resize(1500, 920)
        window.root_window.show()
        QTest.qWait(300)
        reveal(window, 'plot_title')
        QTest.qWait(100)
        axes_bounds = [axis.get_position().bounds for axis in window.figure.axes]
        frame = window._heat_ax.get_position().size * window.figure.get_size_inches()
        cube = window.current_cube
        data = cube.z.copy()
        with patch.object(window.canvas, "draw", wraps=window.canvas.draw) as draw, \
             patch.object(window._annotation_settings, "sync", wraps=window._annotation_settings.sync) as sync, \
             patch.object(window, "_layout_live_plots", wraps=window._layout_live_plots) as layout:
            edit_label(window, "plot_title", "Latest title")
            self.assertEqual(window._heat_ax.get_title(), "Latest title")
            self.assertEqual(draw.call_count, 0)
            self.assertEqual(sync.call_count, 0)
            QTest.qWait(400)
            self.assertEqual(draw.call_count, 0)
            self.assertEqual(sync.call_count, 0)
            QTest.keyClick(window.root_window, Qt.Key_Return)
            QTest.qWait(100)
            self.assertGreater(draw.call_count, 0)
            self.assertEqual(sync.call_count, 1)
            # The clipped title is an overlay; finishing it draws without
            # remeasuring unchanged axis/colorbar geometry.
            self.assertEqual(layout.call_count, 0)
        self.assertEqual(window._heat_ax.get_title(), 'Latest title')
        self.assertEqual([axis.get_position().bounds for axis in window.figure.axes], axes_bounds)
        np.testing.assert_array_equal(
            window._heat_ax.get_position().size * window.figure.get_size_inches(), frame)
        self.assertIs(window.current_cube, cube)
        np.testing.assert_array_equal(cube.z, data)

    def test_formal_export_flushes_latest_colorbar_draft(self):
        window = self.make_window()
        path = self.settings_path.parent / 'export-label.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        window._load_dat_file_direct(path)
        page = window.open_graph_document()
        window._render_plot_page()
        window.root_window.resize(1500, 920)
        window.root_window.show()
        edit_label(window, 'signal_title', 'Latest export label')
        self.assertIsNone(page.rendered_page)
        exported = window.export_plot_page('svg')
        self.assertIn('Latest export label', exported.read_text(encoding='utf-8'))
        self.assertFalse(window._annotation_edit_pending)

    def test_quick_export_button_commits_focused_label_before_writing(self):
        window = self.make_window()
        path = self.settings_path.parent / 'clicked-export.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        window._load_dat_file_direct(path)
        page = window.open_graph_document()
        window.root_window.resize(1500, 920)
        window.root_window.show()
        QCoreApplication.processEvents()
        window._render_plot_page()
        editor = reveal(window, 'signal_title')
        edit_label(window, 'signal_title', 'Native export latest label')
        errors = []
        with patch.object(window, '_show_error', errors.append):
            click_item(window, 'formal_export_svg')
        self.assertEqual(errors, [])
        exported = list(self.settings_path.parent.rglob('*.svg'))
        self.assertEqual(len(exported), 1, {
            'export_enabled': page.command_enabled('export_svg'),
            'ready': page.rendered_page is not None,
            'label_pending': window._annotation_edit_pending,
            'label_focused': editor.hasActiveFocus(),
        })
        self.assertIn('Native export latest label', exported[0].read_text(encoding='utf-8'))

    def test_focus_out_preset_and_auto_each_commit_once(self):
        window = self.make_window()
        path = self.settings_path.parent / 'finish-label.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        window._load_dat_file_direct(path)
        page = window.open_graph_document()
        window.root_window.resize(1500, 920)
        window.root_window.show()
        QCoreApplication.processEvents()
        window._render_plot_page()
        editor = reveal(window, 'signal_title')
        with patch.object(window, '_render_plot_page', wraps=window._render_plot_page) as render:
            edit_label(window, 'signal_title', 'Focus out label')
            reveal(window, 'plot_title')
            QTest.qWait(250)
            self.assertEqual(render.call_count, 1)
            self.assertIn('Focus out label', page.rendered_page.svg_bytes.decode())
            revision = window._plot_owner_token()[3]
            choose(window, 'signal_preset', 'PL (arb. units)')
            QTest.qWait(250)
            self.assertEqual(render.call_count, 2)
            self.assertEqual(window._plot_owner_token()[3], revision + 1)
            self.assertIn('PL (arb. units)', page.rendered_page.svg_bytes.decode())
            click(window, 'signal_title_auto')
            QTest.qWait(250)
            self.assertEqual(render.call_count, 3)
            self.assertEqual(window._plot_owner_token()[3], revision + 2)
            self.assertIn('Signal (arb. units)', page.rendered_page.svg_bytes.decode())

    def test_export_and_close_use_title_before_idle(self):
        window = self.make_window()
        self.load_source(window)
        window.root_window.resize(1500, 920)
        window.root_window.show()
        edit_label(window, "plot_title", "Immediate title")
        detached = _build_heatmap_export_figure(
            window.figure, selected_axes=(window._heat_ax, window._heat_colorbar.ax))
        self.assertTrue(any(text.get_text() == "Immediate title" for text in detached.texts))
        self.close_window(window)
        restored = self.make_window()
        self.assertEqual(state(restored)['plot_title'], "Immediate title")
