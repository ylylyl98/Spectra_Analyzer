from tests import dat_properties_quick_helpers as dat
import os
import sys
from unittest.mock import patch
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from matplotlib.colors import ListedColormap

from spectra_app.custom_colormaps import CUSTOM_COLORMAP_NAMES, resolve_colormap
from spectra_app.dat_io import DataCube
from spectra_app.dataset_state import DatasetSession
from spectra_app.main_window import ApplicationController
from tests.quick_migration_case import QuickMigrationCase
from tests.quick_application_helpers import pump


_PALETTE_DIR = Path(__file__).resolve().parents[1] / "spectra_app" / "palettes"


def _cube(z: np.ndarray) -> DataCube:
    z = np.asarray(z, dtype=float)
    energy = np.arange(z.shape[1], dtype=float)
    gate = np.arange(z.shape[0], dtype=float)
    return DataCube(
        energy=energy,
        gate=gate,
        z=z,
        gate_label="Gate",
        title="custom palette test",
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


def _read_jasc_palette(path: Path) -> np.ndarray:
    lines = path.read_text(encoding="ascii").splitlines()
    assert lines[:3] == ["JASC-PAL", "0100", "256"]
    colors = np.asarray([[int(value) for value in line.split()] for line in lines[3:]], dtype=float)
    assert colors.shape == (256, 3)
    return colors / 255.0


class CustomColormapTests(QuickMigrationCase):
    def setUp(self):
        if (sys.platform == 'win32' and self._testMethodName ==
                'test_native_selection_of_current_default_preserves_manual_choice_for_fresh_pl'):
            # Offscreen closes Popup.Window during the opening mouse release.
            # This case verifies real popup input, so start its app natively.
            self.enterContext(patch.dict(os.environ, QT_QPA_PLATFORM='windows'))
        super().setUp()

    def tearDown(self):
        self.assertFalse(self.quick_errors)
        super().tearDown()

    def make_window(self) -> ApplicationController:
        window = self.controller
        cube = _cube(np.arange(12, dtype=float).reshape(3, 4))
        source = self.folder / 'custom-palette.dat'
        np.savetxt(source, np.column_stack((cube.energy, cube.z.T)),
                   header='Energy 0 1 2', comments='')
        session = DatasetSession(source_path=source, base_cube=cube, current_cube=cube,
                                 sidecar_writes_enabled=False)
        session.graphs[session.active_graph_id].view = window.snapshot_dataset_defaults().view
        window.register_dataset(session)
        window.activate_dataset(session.dataset_id, capture_outgoing=False)
        window.current_gate = 0.0
        window._set_view_limits_to_auto()
        window._plot_cube()
        window.open_graph_document()
        return window

    def test_bundled_palettes_have_exact_256_colors_in_source_order(self) -> None:
        for name in CUSTOM_COLORMAP_NAMES:
            expected = _read_jasc_palette(_PALETTE_DIR / f"{name}.pal")
            cmap = resolve_colormap(name)

            self.assertIsInstance(cmap, ListedColormap)
            actual = np.asarray(cmap.colors, dtype=float)
            self.assertEqual(actual.shape, (256, 3))
            np.testing.assert_array_equal(actual, expected)

    def test_selector_resolves_custom_and_builtin_colormaps_on_real_heatmap(self) -> None:
        window = self.make_window()
        self.assertEqual(next(entry['label'] for entry in dat.state(window)['colormaps'] if entry['value'] == dat.state(window)['colormap']), "RdBu_r")
        for name in (*CUSTOM_COLORMAP_NAMES, "jet", "viridis"):
            dat.set_choice(window, 'colormap', name)
            cmap = window._heat_mesh.get_cmap()
            if name in CUSTOM_COLORMAP_NAMES:
                expected = _read_jasc_palette(_PALETTE_DIR / f"{name}.pal")
                self.assertIsInstance(cmap, ListedColormap)
                np.testing.assert_array_equal(np.asarray(cmap.colors), expected)
            else:
                self.assertEqual(cmap.name, name)

    def test_common_palettes_have_previews_and_canonical_saved_values(self):
        window = self.make_window()
        self.assertEqual([entry['label'] for entry in dat.state(window)['colormaps'][:3]],
                         ['jet', 'RdBu_r', 'RdBu_r_0.6'])
        window.root_window.show(); pump()
        selector = dat.click(window, 'colormap')
        from PySide6.QtCore import QObject
        popup = next(child for child in selector.findChildren(QObject) if child.inherits('QQuickPopup'))
        view = popup.property('contentItem')
        rows = [child for container in view.childItems() for child in container.childItems()
                if child.inherits('QQuickItemDelegate')]
        pump(80)
        for i in range(3):
            row = next(row for row in rows if row.property('index') == i)
            preview = row.findChild(QObject, f'colormap_preview_{i}')
            self.assertIsNotNone(preview)
            self.assertTrue(preview.property('source').toString().startswith('data:image/png;base64,'))
            self.assertGreater(preview.property('sourceSize').width(), 0)
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        QTest.keyClick(window.root_window, Qt.Key_Escape)
        dat.set_choice(window, 'colormap', dat.state(window)['colormaps'][2]['value'])
        window._capture_dataset_controls()
        self.assertEqual(window.workspace.datasets[window.workspace.active_dataset_id].view.colormap, 'RdBu_r_p0p6')

    def test_native_selection_of_current_default_preserves_manual_choice_for_fresh_pl(self):
        from PySide6.QtGui import QGuiApplication
        import tempfile
        if sys.platform == 'win32':
            self.assertEqual(QGuiApplication.platformName(), 'windows')
        window = self.make_window()
        window.root_window.show(); pump()
        self.assertFalse(window._colormap_manual)
        dat.choose(window, 'colormap', 'RdBu_r')
        self.assertEqual(next(entry['label'] for entry in dat.state(window)['colormaps'] if entry['value'] == dat.state(window)['colormap']), 'RdBu_r')
        self.assertTrue(window._colormap_manual)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fresh_PL730nm.dat'
            path.write_text('Energy(eV) 0 1\n1 1 2\n2 3 4\n3 5 6\n', encoding='utf-8')
            window._load_dat_file_direct(path)
            self.assertEqual(next(entry['label'] for entry in dat.state(window)['colormaps'] if entry['value'] == dat.state(window)['colormap']), 'RdBu_r')
            self.assertEqual(dat.state(window)['signal_title'], 'PL (arb. units)')
            window.current_dat_path = None

    def test_palette_only_selection_preserves_color_limits(self) -> None:
        window = self.make_window()
        dat.setup_number(window, 'vmin', 2.25)
        dat.setup_number(window, 'vmax', 8.75)
        before = (window.dat_properties.number('vmin'), window.dat_properties.number('vmax'))

        dat.set_choice(window, 'colormap', "RdBu_r_p0p45")

        self.assertEqual((window.dat_properties.number('vmin'), window.dat_properties.number('vmax')), before)
        self.assertEqual(
            (window._heat_mesh.norm.vmin, window._heat_mesh.norm.vmax),
            before,
        )

    def test_palette_selection_preserves_exact_norm_and_view_at_subcontrol_precision(self):
        window = self.make_window()
        window._heat_mesh.set_clim(.1234567, 9.8765432)
        window._heat_ax.set_xlim(.1234567, 2.8765432)
        original_norm = window._heat_mesh.norm
        original_limits = window._heat_mesh.get_clim()
        original_view = window._heat_ax.get_xlim()
        data = window.current_cube.z.copy()
        dat.set_choice(window, 'colormap', 'RdBu_r_p0p6')
        self.assertIs(window._heat_mesh.norm, original_norm)
        self.assertEqual(window._heat_mesh.get_clim(), original_limits)
        self.assertEqual(window._heat_ax.get_xlim(), original_view)
        np.testing.assert_array_equal(window.current_cube.z, data)


if __name__ == "__main__":
    unittest.main()
