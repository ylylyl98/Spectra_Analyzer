import os
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from matplotlib.figure import Figure
from PIL import Image
from PySide6.QtWidgets import QApplication

from spectra_app.dat_io import DataCube
from spectra_app.image_export import (
    EXPORT_DPI,
    EXPORT_FIGSIZE,
    REGIONAL_COLORBAR_FONT_SIZE,
    REGIONAL_COLORBAR_TICK_GAP,
    _build_heatmap_export_figure,
    _cb_fmt_regional,
    export_figure_png,
)
from spectra_app.main_window import MainWindow
from spectra_app.svg_export import write_svg_png
from tests.settings_helpers import isolate_settings


_APP = QApplication.instance() or QApplication([])


def _cube(z: np.ndarray, *, title: str = "export test") -> DataCube:
    z = np.asarray(z, dtype=float)
    energy = np.linspace(0.0, 10.0, z.shape[1])
    gate = np.linspace(1.0, 3.0, z.shape[0])
    return DataCube(
        energy=energy,
        gate=gate,
        z=z,
        gate_label="Gate",
        title=title,
        source_path="",
        x_original=energy.copy(),
        x_original_type="pixel",
        x_original_label="X",
        x_detection_source="test",
        x_detection_note="",
        x_is_ambiguous=False,
        x_override=None,
        photon_energy=None,
        photon_energy_note="",
        energy_label="X",
    )


class ImageExportTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_settings(self)
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window.close()

    def make_window(self, *, title: str = "export test") -> MainWindow:
        window = MainWindow()
        window._show_error = lambda message: self.fail(f"unexpected Qt error: {message}")
        window.current_cube = _cube(np.arange(24, dtype=float).reshape(3, 8), title=title)
        window.current_gate = 2.0
        window._set_view_limits_to_auto()
        window._plot_cube()
        self.windows.append(window)
        return window

    def test_detached_export_uses_reference_layout_and_only_heatmap_artists(self) -> None:
        window = self.make_window(title="Very long heatmap export title with several words")
        window.cmap_combo.setCurrentText("RdBu_r_p0p45")
        window._heat_ax.set_xlim(2.0, 7.0)
        window._heat_ax.set_ylim(1.2, 2.8)
        window.vmin_spin.setValue(2.0)
        window.vmax_spin.setValue(19.0)
        window._update_spectrum_only()

        detached = _build_heatmap_export_figure(
            window.figure,
            selected_axes=(window._heat_ax, window._heat_colorbar.ax),
        )
        self.assertIsNot(detached, window.figure)
        self.assertEqual(len(detached.axes), 2)
        heat_ax, colorbar_ax = detached.axes
        detached.canvas.draw()
        plot_box = heat_ax.get_window_extent()
        self.assertAlmostEqual(plot_box.width / detached.dpi, 4.0)
        self.assertAlmostEqual(plot_box.height / detached.dpi, 3.0)
        self.assertEqual(len(heat_ax.collections), 1)
        self.assertFalse(heat_ax.lines)
        self.assertFalse(heat_ax.texts)
        self.assertNotIn(window._spec_ax, detached.axes)
        self.assertEqual(heat_ax.get_xlim(), (2.0, 7.0))
        self.assertEqual(heat_ax.get_ylim(), (1.2, 2.8))
        self.assertEqual(heat_ax.get_xlabel(), "X")
        self.assertEqual(heat_ax.get_ylabel(), "Gate")
        self.assertEqual(heat_ax.get_title(), "")
        self.assertEqual(len(detached.texts), 1)
        title = detached.texts[0]
        self.assertGreaterEqual(title.get_position()[1], heat_ax.get_position().y1)
        self.assertEqual(title.get_fontsize(), 12)
        self.assertEqual(title.get_text(), "")
        self.assertLessEqual(title.get_text().count("\n") + 1, 3)
        self.assertEqual(heat_ax.xaxis.label.get_fontsize(), 12)
        self.assertEqual(heat_ax.yaxis.label.get_fontsize(), 12)
        self.assertTrue(all(label.get_fontsize() == 12 for label in heat_ax.get_xticklabels()))
        self.assertTrue(all(label.get_fontsize() == 12 for label in heat_ax.get_yticklabels()))
        self.assertEqual(colorbar_ax.get_xlabel(), "Signal (arb. units)")
        self.assertEqual(colorbar_ax.title.get_fontsize(), 12)
        self.assertEqual(tuple(colorbar_ax.get_xticks()), (2.0, 19.0))
        self.assertTrue(all(label.get_fontsize() == 12 for label in colorbar_ax.get_xticklabels()))
        self.assertTrue(all(not line.get_visible() for line in colorbar_ax.get_xgridlines()))

    def test_detached_export_copies_mesh_coordinates_array_cmap_norm_and_ranges(self) -> None:
        window = self.make_window()
        window.cmap_combo.setCurrentText("RdBu_r_0.6")
        window._heat_ax.set_xlim(2.0, 7.0)
        window._heat_ax.set_ylim(1.2, 2.8)
        window.vmin_spin.setValue(2.0)
        window.vmax_spin.setValue(19.0)
        window._update_spectrum_only()
        live_mesh = window._heat_mesh
        live_heat = window._heat_ax
        detached = _build_heatmap_export_figure(
            window.figure,
            selected_axes=(live_heat, window._heat_colorbar.ax),
        )
        exported_mesh = detached.axes[0].collections[0]
        np.testing.assert_array_equal(exported_mesh.get_coordinates(), live_mesh.get_coordinates())
        np.testing.assert_array_equal(exported_mesh.get_array(), live_mesh.get_array())
        self.assertIs(exported_mesh.get_cmap(), live_mesh.get_cmap())
        self.assertIsNot(exported_mesh.norm, live_mesh.norm)
        self.assertEqual(exported_mesh.get_clim(), live_mesh.get_clim())

    def test_detached_regional_export_copies_all_meshes_norms_and_colorbars(self) -> None:
        window = self.make_window()
        window.color_scale_combo.setCurrentIndex(window.color_scale_combo.findData("3"))
        detached = _build_heatmap_export_figure(
            window.figure,
            selected_axes=(window._heat_ax, *[bar.ax for bar in window._heat_colorbars]),
        )
        self.assertEqual(len(detached.axes), 4)
        self.assertEqual(len(detached.axes[0].collections), 3)
        self.assertEqual(
            [mesh.get_clim() for mesh in detached.axes[0].collections],
            [mesh.get_clim() for mesh in window._heat_meshes],
        )
        for exported, live in zip(detached.axes[0].collections, window._heat_meshes):
            self.assertIsNot(exported.norm, live.norm)
            np.testing.assert_array_equal(exported.get_array(), live.get_array())
        for axis, live_bar in zip(detached.axes[1:], window._heat_colorbars):
            self.assertEqual(axis.get_xlabel(), live_bar.ax.get_xlabel())

    def test_detached_regional_signal_label_clears_all_ticks_in_fixed_header(self) -> None:
        for region_count in (2, 3):
            with self.subTest(region_count=region_count):
                window = self.make_window(title="Long regional export title with several words")
                window.plot_title_edit.setText("Long regional export title with several words")
                window.signal_title_combo.setCurrentText("d²(ΔR/R)/dE² (a. u.)")
                window.color_scale_combo.setCurrentIndex(
                    window.color_scale_combo.findData(str(region_count))
                )
                window._layout_live_plots()
                detached = _build_heatmap_export_figure(
                    window.figure,
                    selected_axes=(window._heat_ax, *[bar.ax for bar in window._heat_colorbars]),
                )
                detached.canvas.draw()
                renderer = detached.canvas.get_renderer()
                signal_labels = [
                    artist for artist in detached.texts
                    if artist.get_gid() == "heatmap_signal_label"
                ]
                self.assertEqual(len(signal_labels), 1)
                signal_bbox = signal_labels[0].get_window_extent(renderer)
                figure_bbox = detached.bbox
                self.assertGreaterEqual(signal_bbox.x0, figure_bbox.x0)
                self.assertGreaterEqual(signal_bbox.y0, figure_bbox.y0)
                self.assertLessEqual(signal_bbox.x1, figure_bbox.x1)
                self.assertLessEqual(signal_bbox.y1, figure_bbox.y1)
                self.assertTrue(signal_labels[0].get_text())
                self.assertTrue(detached.texts[0].get_text())
                title_bbox = detached.texts[0].get_window_extent(renderer)
                self.assertGreaterEqual(title_bbox.x0, figure_bbox.x0)
                self.assertGreaterEqual(title_bbox.y0, figure_bbox.y0)
                self.assertLessEqual(title_bbox.x1, figure_bbox.x1)
                self.assertLessEqual(title_bbox.y1, figure_bbox.y1)
                self.assertFalse(signal_bbox.overlaps(title_bbox))
                tick_bboxes = []
                for colorbar_axis in detached.axes[1:]:
                    for tick in colorbar_axis.get_xticklabels():
                        tick_bbox = tick.get_window_extent(renderer)
                        tick_bboxes.append(tick_bbox)
                        self.assertFalse(signal_bbox.overlaps(tick_bbox))
                        self.assertFalse(title_bbox.overlaps(tick_bbox))
                for index, tick_bbox in enumerate(tick_bboxes):
                    self.assertTrue(
                        all(not tick_bbox.overlaps(other) for other in tick_bboxes[index + 1:])
                    )
                self.assertAlmostEqual(detached.get_size_inches()[0], EXPORT_FIGSIZE[0])
                self.assertAlmostEqual(detached.get_size_inches()[1], EXPORT_FIGSIZE[1])
                plot_box = detached.axes[0].get_window_extent(renderer)
                self.assertAlmostEqual(plot_box.width / detached.dpi, 4.0)
                self.assertAlmostEqual(plot_box.height / detached.dpi, 3.0)

    def test_regional_endpoint_ticks_are_compact_and_separated_within_each_bar(self) -> None:
        window = self.make_window(title="p3 8T 0.5TG-BG=0")
        window.signal_title_combo.setCurrentText("d²(ΔR/R)/dE² (a. u.)")
        window.color_scale_combo.setCurrentIndex(window.color_scale_combo.findData("3"))
        window._layout_live_plots()
        ranges = ((-0.07239, 1.053), (-0.03265, 0.02476), (-0.006, 0.008))
        for mesh, colorbar, (vmin, vmax) in zip(window._heat_meshes, window._heat_colorbars, ranges):
            mesh.set_clim(vmin, vmax)
            colorbar.mappable.set_clim(vmin, vmax)
        window._layout_live_plots()
        window.canvas.draw()
        live_renderer = window.canvas.get_renderer()
        live_labels = [tick for bar in window._heat_colorbars for tick in bar.ax.get_xticklabels()]
        live_labels += [window._heat_ax.title, window._heat_signal_label]
        live_boxes = [label.get_window_extent(live_renderer) for label in live_labels]
        for index, box in enumerate(live_boxes):
            self.assertGreaterEqual(box.x0, 0)
            self.assertGreaterEqual(box.y0, 0)
            self.assertLessEqual(box.x1, window.figure.bbox.x1)
            self.assertLessEqual(box.y1, window.figure.bbox.y1)
            self.assertTrue(all(not box.overlaps(other) for other in live_boxes[index + 1:]))
        self.assertTrue(all(not bar.ax.get_xlabel() for bar in window._heat_colorbars))
        detached = _build_heatmap_export_figure(
            window.figure,
            selected_axes=(window._heat_ax, *[bar.ax for bar in window._heat_colorbars]),
        )
        detached.canvas.draw()
        renderer = detached.canvas.get_renderer()
        labels = [
            tick
            for axis in detached.axes[1:]
            for tick in axis.get_xticklabels()
            if tick.get_text()
        ]
        self.assertEqual(
            [tick.get_text() for tick in labels],
            ["-0.072", "1.1", "-0.033", "0.025", "-6e-3", "8e-3"],
        )
        self.assertEqual(_cb_fmt_regional(-0.006), "-6e-3")
        self.assertEqual(_cb_fmt_regional(0.008), "8e-3")
        self.assertTrue(all(tick.get_fontsize() <= REGIONAL_COLORBAR_FONT_SIZE for tick in labels))
        boxes = [tick.get_window_extent(renderer) for tick in labels]
        for index, box in enumerate(boxes):
            for other in boxes[index + 1:]:
                separated = (
                    box.x1 + REGIONAL_COLORBAR_TICK_GAP <= other.x0
                    or other.x1 + REGIONAL_COLORBAR_TICK_GAP <= box.x0
                )
                self.assertTrue(separated, (box.bounds, other.bounds))

    def test_repeated_detached_exports_do_not_add_callbacks_to_live_norm(self) -> None:
        window = self.make_window()
        live_norm = window._heat_mesh.norm
        callbacks_before = {
            signal: dict(callbacks)
            for signal, callbacks in live_norm.callbacks.callbacks.items()
        }
        first = _build_heatmap_export_figure(
            window.figure,
            selected_axes=(window._heat_ax, window._heat_colorbar.ax),
        )
        second = _build_heatmap_export_figure(
            window.figure,
            selected_axes=(window._heat_ax, window._heat_colorbar.ax),
        )
        self.assertIsNot(first.axes[0].collections[0].norm, second.axes[0].collections[0].norm)
        self.assertEqual(
            {
                signal: dict(callbacks)
                for signal, callbacks in live_norm.callbacks.callbacks.items()
            },
            callbacks_before,
        )

    def test_export_writes_real_png_with_reference_size_dpi_and_white_background(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            path = export_figure_png(
                window.figure,
                Path(tmp) / "figure",
                selected_axes=(window._heat_ax, window._heat_colorbar.ax),
            )
            self.assertEqual(path.suffix, ".png")
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertAlmostEqual(image.info["dpi"][0], EXPORT_DPI, delta=1.0)
                self.assertEqual(image.getpixel((0, 0))[:3], (255, 255, 255))
                self.assertEqual(image.width, 1545)
                self.assertEqual(image.height, 1242)

    def test_export_preserves_live_figure_and_artists(self) -> None:
        window = self.make_window()
        window.cmap_combo.setCurrentText("RdBu_r_p0p45")
        window._heat_ax.set_xlim(2.0, 7.0)
        window._heat_ax.set_ylim(1.2, 2.8)
        window._update_spectrum_only()
        figure = window.figure
        size_before = figure.get_size_inches().copy()
        face_before = figure.get_facecolor()
        axes_before = [
            (axis.get_position(original=True).bounds, axis.get_position().bounds, axis.get_visible())
            for axis in figure.axes
        ]
        heat_mesh = window._heat_mesh
        cmap_before = heat_mesh.get_cmap()
        norm_before = heat_mesh.norm
        with tempfile.TemporaryDirectory() as tmp:
            export_figure_png(
                figure,
                Path(tmp) / "state.png",
                selected_axes=(window._heat_ax, window._heat_colorbar.ax),
            )
        np.testing.assert_array_equal(figure.get_size_inches(), size_before)
        np.testing.assert_array_equal(figure.get_facecolor(), face_before)
        self.assertEqual(
            [
                (axis.get_position(original=True).bounds, axis.get_position().bounds, axis.get_visible())
                for axis in figure.axes
            ],
            axes_before,
        )
        self.assertIs(heat_mesh.get_cmap(), cmap_before)
        self.assertIs(heat_mesh.norm, norm_before)

    def test_export_rejects_non_png_extension_and_missing_heatmap(self) -> None:
        figure = Figure()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "figure.jpg"
            with self.assertRaises(ValueError):
                export_figure_png(figure, path)
            self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            export_figure_png(Figure(), Path(tempfile.gettempdir()) / "missing.png")

    def test_export_save_error_leaves_live_figure_untouched(self) -> None:
        window = self.make_window()
        figure = window.figure
        size_before = figure.get_size_inches().copy()
        axes_before = [(axis.get_position().bounds, axis.get_visible()) for axis in figure.axes]
        with patch.object(Figure, "savefig", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                export_figure_png(
                    figure,
                    Path(tempfile.gettempdir()) / "export-failure.png",
                    selected_axes=(window._heat_ax, window._heat_colorbar.ax),
                )
        np.testing.assert_array_equal(figure.get_size_inches(), size_before)
        self.assertEqual(
            [(axis.get_position().bounds, axis.get_visible()) for axis in figure.axes],
            axes_before,
        )

    def test_main_window_export_action_saves_directly_in_dat_folder_without_dialog(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.dat"
            source.write_text("Energy(eV) 0 1\n1.0 1 2\n1.1 2 3\n1.2 3 4\n", encoding="utf-8")
            window._load_dat_file_direct(source)
            window.plot_title_edit.setText("Saved title")
            window._render_plot_page()
            self.assertIsNotNone(window.plot_page.rendered_page, window.plot_page.status_label.text())
            browsing_folder = Path(tmp) / "other-folder"
            browsing_folder.mkdir()
            window.current_folder = str(browsing_folder)
            sidecar = source.with_name("source.dat.plot.json")
            self.assertFalse(sidecar.exists())
            existing = Path(tmp) / "source_Graph 1_export01.png"
            existing.write_bytes(b"preserve original export")
            with patch("spectra_app.main_window.QFileDialog.getSaveFileName", side_effect=AssertionError("PNG must save without a dialog")):
                window._on_export_png()
            output = Path(tmp) / "source_Graph 1_export02.png"
            self.assertTrue(output.is_file())
            self.assertEqual(existing.read_bytes(), b"preserve original export")
            self.assertTrue(sidecar.is_file())
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 4)
            self.assertEqual(payload["plot"]["graphs"][0]["plot"]["annotations"]["plot_title"], "Saved title")
            self.assertFalse((browsing_folder / "source_Graph 1_export02.png").exists())
            self.assertIn(str(output), window.statusBar().currentMessage())
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                metadata = json.loads(image.info["SpectraAnalyzer"])
            self.assertEqual(metadata["plot_title"], "Saved title")
            self.assertEqual(metadata["source_path"], str(source))
            window.close()
        self.assertTrue(window.export_png_btn.isEnabled())

    def test_main_window_export_uses_current_svg_without_spectrum_or_editing_handles(self) -> None:
        window = self.make_window()
        window._spec_ax.set_title("Spectrum-only export sentinel")
        window._spec_ax.lines[0].set_gid("spectrum-only-line")
        window._render_plot_page()
        self.assertIsNotNone(window.plot_page.rendered_page, window.plot_page.status_label.text())
        annotation = window.plot_page.add_annotation("arrow", "Formal annotation")
        window._render_plot_page()
        page = window.plot_page.rendered_page
        self.assertIsNotNone(page, window.plot_page.status_label.text())
        self.assertEqual(window.plot_page.preview.selected_id, annotation.id)
        preview_bytes = window.plot_page.preview.svg_bytes
        root = ET.fromstring(preview_bytes)
        self.assertIn("Formal annotation", ''.join(root.itertext()))
        self.assertNotIn("Spectrum-only export sentinel", ''.join(root.itertext()))
        self.assertNotIn("spectrum-only-line", preview_bytes.decode())
        self.assertNotIn("editing-handle", preview_bytes.decode())
        self.assertEqual(len(root.findall('.//{http://www.w3.org/2000/svg}image')), 1)
        with tempfile.TemporaryDirectory() as tmp:
            window.current_folder = tmp
            path = Path(tmp) / "heatmap_Graph 1_export01.png"
            with patch("spectra_app.main_window.QFileDialog.getSaveFileName", side_effect=AssertionError("PNG must save without a dialog")), patch(
                "spectra_app.main_window.write_svg_png", wraps=write_svg_png
            ) as export, patch("spectra_app.main_window.render_hybrid_svg", side_effect=AssertionError("Export must use the current preview")):
                window._on_export_png()
            self.assertIs(export.call_args.args[0], page)
            self.assertEqual(export.call_args.args[0].svg_bytes, preview_bytes)
            self.assertIs(window.plot_page.rendered_page, page)
            self.assertTrue(path.is_file())
            reference = write_svg_png(page, Path(tmp) / "same-page-reference.png")
            with Image.open(path) as actual, Image.open(reference) as expected:
                self.assertEqual(actual.size, expected.size)
                self.assertEqual(actual.convert("RGBA").tobytes(), expected.convert("RGBA").tobytes())

    def test_main_window_export_action_reports_save_failure(self) -> None:
        window = self.make_window()
        window._render_plot_page()
        page = window.plot_page.rendered_page
        self.assertIsNotNone(page, window.plot_page.status_label.text())
        errors: list[str] = []
        window._show_error = errors.append
        with tempfile.TemporaryDirectory() as tmp:
            window.current_folder = tmp
            existing = Path(tmp) / "heatmap_Graph 1_export01.png"
            existing.write_bytes(b"preserve original export")
            with patch("spectra_app.main_window.QFileDialog.getSaveFileName", side_effect=AssertionError("PNG must save without a dialog")), patch(
                "spectra_app.main_window.write_svg_png", side_effect=OSError("disk full")
            ) as export:
                window._on_export_png()
            self.assertIs(export.call_args.args[0], page)
            self.assertEqual(export.call_args.args[1], Path(tmp) / "heatmap_Graph 1_export02.png")
            self.assertFalse((Path(tmp) / "heatmap_Graph 1_export02.png").exists())
            self.assertEqual(existing.read_bytes(), b"preserve original export")
        self.assertTrue(errors)
        self.assertIn("PNG", errors[-1])
        self.assertIn("disk full", errors[-1])
        self.assertIs(window.plot_page.rendered_page, page)

    def test_main_window_export_action_is_guarded_without_data(self) -> None:
        window = MainWindow()
        self.windows.append(window)
        errors: list[str] = []
        window._show_error = errors.append
        self.assertFalse(window.export_png_btn.isEnabled())
        with patch("spectra_app.main_window.write_svg_png") as export:
            window._on_export_png()
        export.assert_not_called()
        self.assertEqual(errors, ["Failed to export PNG: The formal page is not ready. Wait for the current preview."])


if __name__ == "__main__":
    unittest.main()
