import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch
from PySide6.QtTest import QTest
from tests import test_annotation_persistence as helpers
from spectra_app.image_export import _build_heatmap_export_figure


class TitleEditLatencyTests(unittest.TestCase):
    setUp = helpers.AnnotationPersistenceTests.setUp
    tearDown = helpers.AnnotationPersistenceTests.tearDown
    make_window = helpers.AnnotationPersistenceTests.make_window
    load_source = helpers.AnnotationPersistenceTests.load_source
    def test_typing_coalesces_render_and_settings_write(self):
        window = self.make_window()
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
            self.assertGreater(draw.call_count, 0)
            self.assertEqual(sync.call_count, 1)
            self.assertEqual(layout.call_count, 1)

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
