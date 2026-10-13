import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
import numpy as np
from tests import test_annotation_persistence as helpers
from spectra_app.image_export import _build_heatmap_export_figure


class TitleEditLatencyTests(unittest.TestCase):
    setUp = helpers.AnnotationPersistenceTests.setUp
    tearDown = helpers.AnnotationPersistenceTests.tearDown
    make_window = helpers.AnnotationPersistenceTests.make_window
    load_source = helpers.AnnotationPersistenceTests.load_source

    def test_colorbar_draft_retains_formal_preview_until_finish(self):
        window = self.make_window()
        path = self.settings_path.parent / 'label.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        window._load_dat_file_direct(path)
        page = window.open_graph_document()
        window.resize(1500, 920)
        window.show()
        helpers._APP.processEvents()
        window._render_plot_page()
        page.set_zoom('150%')
        old = page.rendered_page
        size = page.preview.size()
        data = window.current_cube.z.copy()
        editor = window.signal_title_combo.lineEdit()
        editor.setFocus()
        editor.selectAll()
        with patch.object(window, '_render_plot_page', wraps=window._render_plot_page) as render, \
             patch.object(window._annotation_settings, 'sync', wraps=window._annotation_settings.sync) as sync:
            QTest.keyClicks(editor, 'Custom colorbar label')
            QTest.qWait(400)
            self.assertEqual(page.preview.svg_bytes, old.svg_bytes)
            self.assertEqual(page.preview.size(), size)
            self.assertIsNone(page.rendered_page, 'Old displayed bytes must not be exportable as current')
            self.assertTrue(page.export_svg_btn.isEnabled(), 'Export remains a reachable commit action')
            self.assertFalse(page.preview.isEnabled(), 'Stale geometry must not receive editing gestures')
            self.assertEqual(render.call_count, 0)
            self.assertEqual(sync.call_count, 0)
            QTest.keyClick(editor, Qt.Key_Return)
            QTest.qWait(100)
            self.assertEqual(render.call_count, 1)
            self.assertEqual(sync.call_count, 1)
            self.assertIn('Custom colorbar label', page.rendered_page.svg_bytes.decode())
            self.assertTrue(page.preview.isEnabled())
            editor.clearFocus()
            QTest.qWait(100)
            self.assertEqual(render.call_count, 1)
        np.testing.assert_array_equal(window.current_cube.z, data)
    def test_typing_waits_for_finish_to_render_and_write_settings(self):
        window = self.make_window()
        window.plot_tabs.setCurrentIndex(0)
        self.load_source(window)
        window.color_scale_combo.setCurrentIndex(window.color_scale_combo.findData("3"))
        QTest.qWait(300)
        with patch.object(window.canvas, "draw", wraps=window.canvas.draw) as draw, \
             patch.object(window._annotation_settings, "sync", wraps=window._annotation_settings.sync) as sync, \
             patch.object(window, "_layout_live_plots", wraps=window._layout_live_plots) as layout:
            QTest.keyClicks(window.plot_title_edit, "Latest title")
            self.assertEqual(window._heat_ax.get_title(), "Latest title")
            self.assertEqual(draw.call_count, 0)
            self.assertEqual(sync.call_count, 0)
            QTest.qWait(400)
            self.assertEqual(draw.call_count, 0)
            self.assertEqual(sync.call_count, 0)
            QTest.keyClick(window.plot_title_edit, Qt.Key_Return)
            QTest.qWait(100)
            self.assertGreater(draw.call_count, 0)
            self.assertEqual(sync.call_count, 1)
            self.assertEqual(layout.call_count, 1)

    def test_formal_export_flushes_latest_colorbar_draft(self):
        window = self.make_window()
        path = self.settings_path.parent / 'export-label.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        window._load_dat_file_direct(path)
        page = window.open_graph_document()
        window._render_plot_page()
        window.signal_title_combo.setCurrentText('Latest export label')
        self.assertIsNone(page.rendered_page)
        exported = window.export_plot_page('svg')
        self.assertIn('Latest export label', exported.read_text(encoding='utf-8'))
        self.assertFalse(window._annotation_edit_pending)

    def test_native_export_button_commits_focused_label_before_writing(self):
        window = self.make_window()
        path = self.settings_path.parent / 'clicked-export.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        window._load_dat_file_direct(path)
        page = window.open_graph_document()
        window.resize(1500, 920)
        window.show()
        helpers._APP.processEvents()
        window._render_plot_page()
        page.control_scroll.ensureWidgetVisible(window.signal_title_combo)
        editor = window.signal_title_combo.lineEdit()
        editor.setFocus()
        editor.selectAll()
        QTest.keyClicks(editor, 'Native export latest label')
        errors = []
        with patch.object(window, '_show_error', errors.append):
            QTest.mouseClick(page.export_svg_btn, Qt.LeftButton)
        self.assertEqual(errors, [])
        exported = list(self.settings_path.parent.rglob('*.svg'))
        self.assertEqual(len(exported), 1)
        self.assertIn('Native export latest label', exported[0].read_text(encoding='utf-8'))

    def test_focus_out_preset_and_auto_each_commit_once(self):
        window = self.make_window()
        path = self.settings_path.parent / 'finish-label.dat'
        path.write_text('Energy(eV) 0 1\n' + '\n'.join(f'{1+i/20} {i+1} {2*i+2}' for i in range(21)))
        window._load_dat_file_direct(path)
        page = window.open_graph_document()
        window.resize(1500, 920)
        window.show()
        helpers._APP.processEvents()
        window._render_plot_page()
        page.control_scroll.ensureWidgetVisible(window.signal_title_combo)
        editor = window.signal_title_combo.lineEdit()
        editor.setFocus()
        with patch.object(window, '_render_plot_page', wraps=window._render_plot_page) as render:
            editor.selectAll()
            QTest.keyClicks(editor, 'Focus out label')
            window.plot_title_edit.setFocus()
            QTest.qWait(250)
            self.assertEqual(render.call_count, 1)
            self.assertIn('Focus out label', page.rendered_page.svg_bytes.decode())
            revision = window._plot_owner_token()[3]
            window.signal_title_combo.setCurrentIndex(2)
            window.signal_title_combo.activated.emit(2)
            QTest.qWait(250)
            self.assertEqual(render.call_count, 2)
            self.assertEqual(window._plot_owner_token()[3], revision + 1)
            self.assertIn('PL (arb. units)', page.rendered_page.svg_bytes.decode())
            window.signal_title_auto_chk.click()
            QTest.qWait(250)
            self.assertEqual(render.call_count, 3)
            self.assertEqual(window._plot_owner_token()[3], revision + 2)
            self.assertIn('Signal (arb. units)', page.rendered_page.svg_bytes.decode())

    def test_export_and_close_use_title_before_idle(self):
        window = self.make_window()
        self.load_source(window)
        QTest.keyClicks(window.plot_title_edit, "Immediate title")
        detached = _build_heatmap_export_figure(
            window.figure, selected_axes=(window._heat_ax, window._heat_colorbar.ax))
        self.assertTrue(any(text.get_text() == "Immediate title" for text in detached.texts))
        window.close()
        restored = self.make_window()
        self.assertEqual(restored.plot_title_edit.text(), "Immediate title")
