"""Fit readability and scrolling through the production Quick canvas."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class QuickLayoutReadabilityTests(unittest.TestCase):
    def test_readable_fit(self):
        result = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--probe'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
            timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn('Readable Fit: PASS after Quick teardown', result.stdout)


def probe():
    import math
    import numpy as np
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from tests.quick_application_helpers import application, item, pump

    with application() as (host, folder, errors):
        w = host.controller
        source = folder / 'Spectrum - representative generated fixture.dat'
        positions = list(range(-10, 11))
        source.write_text('Energy(eV) ' + ' '.join(map(str, positions)) + '\n' + '\n'.join(
            str(1.2 + i * .0125) + ' ' + ' '.join(str(1 + 3 * math.exp(-((i - 40 - j * .5) / 12)**2)
            + .1 * math.sin(i * .1 + j)) for j in positions) for i in range(81)), encoding='utf8')
        source_before = source.read_bytes()
        w._load_dat_file_direct(source)
        pump(100)
        w.open_graph_document()
        w._render_plot_page()
        pump(100)
        page = w.plot_page.rendered_page
        assert page is not None
        svg = w.export_plot_page('svg').read_bytes()
        saved = w.workspace.active.presentation
        history = w._history_for(w.workspace.active)
        history_before = list(history.undo_stack), list(history.redo_stack)
        cube = w.current_cube
        arrays = [array.copy() for array in (cube.energy, cube.gate, cube.z)]
        limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim()
        norm = w._heat_mesh.norm
        assert w.request_outer_view('analysis')
        w.request_task(2)
        w.plot_page.set_zoom('Fit')

        def resize(width, height):
            host.window.resize(width, height)
            pump(200)
            w.canvas.draw()
            pump(60)

        def capture(name):
            target = os.environ.get('SPECTRA_READABILITY_CAPTURE_DIR')
            if target:
                path = Path(target) / (name + '.png')
                path.parent.mkdir(parents=True, exist_ok=True)
                assert host.window.grabWindow().save(str(path))

        resize(900, 620)
        viewport = item(host, 'analysisCanvasViewport')
        image = item(host, 'analysisCanvasImage')
        bar = item(host, 'analysisCanvasVerticalScrollBar')
        ratio = w.canvas.device_pixel_ratio
        tick_px = min(t.get_fontsize() for t in w._heat_ax.get_xticklabels()
                      if t.get_visible()) * w.figure.dpi / 72 / ratio
        print(f'minimum: scale={w._analysis_display_scale():.6f}, ticks={tick_px:.3f}px, '
              f'viewport={viewport.width()}x{viewport.height()}, '
              f'content={w.dat_canvas.contentWidth}x{w.dat_canvas.contentHeight}', flush=True)
        capture('dat-analysis-view-minimum')
        assert tick_px >= 9 - 1e-8, 'Fit must keep default heatmap ticks at least 9 logical pixels'
        assert w.dat_canvas.contentHeight > viewport.height()
        assert bar.isVisible() and bar.property('size') < 1
        # Use the actual scroll bar, then click through the scrolled image's
        # MouseArea. The resulting Matplotlib event must retain data coordinates.
        point = bar.mapToScene(QPointF(bar.width() / 2, bar.height() * .85)).toPoint()
        QTest.mouseClick(host.window, Qt.LeftButton, Qt.NoModifier, point)
        pump(100)
        assert viewport.property('contentY') > 0, 'Existing vertical scroll bar must move the canvas'
        viewport.setProperty('contentY', min(40., w.dat_canvas.contentHeight - viewport.height()))
        pump(60)
        center = tuple(sum(pair) / 2 for pair in (w._heat_ax.get_xlim(), w._heat_ax.get_ylim()))
        display = w._heat_ax.transData.transform(center)
        point = image.mapToScene(QPointF(display[0] / ratio, image.height() - display[1] / ratio))
        events = []
        connection = w.canvas.mpl_connect('button_press_event', events.append)
        QTest.mouseClick(host.window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        pump(60)
        w.canvas.mpl_disconnect(connection)
        assert events and events[-1].inaxes is w._heat_ax
        # QTest accepts integer scene pixels; allow half a data pixel of rounding.
        inverse = w._heat_ax.transData.inverted()
        tolerance = np.abs(inverse.transform(display + 1.1 * ratio) - inverse.transform(display))
        np.testing.assert_array_less(np.abs(np.array((events[-1].xdata, events[-1].ydata)) - center), tolerance)

        for zoom, scale in (('100%', 1.), ('150%', 1.5)):
            w.plot_page.set_zoom(zoom)
            pump(120)
            assert w._analysis_display_scale() == scale
            np.testing.assert_allclose(w._heat_ax.get_position().size * w.figure.get_size_inches(),
                np.array(w._live_heatmap_frame_size(saved)) * 96 * scale * ratio / w.figure.dpi)
        w.plot_page.set_zoom('Fit')
        resize(1440, 900)
        assert w._analysis_display_scale() > .675
        assert w.dat_canvas.contentHeight == round(viewport.height())
        assert w.dat_canvas.contentWidth == round(viewport.width())
        assert viewport.property('contentY') == 0
        capture('dat-analysis-view-1440')
        assert w.workspace.active.presentation == saved
        assert (history.undo_stack, history.redo_stack) == history_before
        assert w.current_cube is cube
        for actual, expected in zip((cube.energy, cube.gate, cube.z), arrays):
            np.testing.assert_array_equal(actual, expected)
        assert source.read_bytes() == source_before
        assert (w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim()) == limits
        assert w._heat_mesh.norm is norm
        assert w.plot_page.rendered_page is page
        assert w.export_plot_page('svg').read_bytes() == svg
        assert not errors, errors
    print('Readable Fit: PASS after Quick teardown', flush=True)


if __name__ == '__main__':
    if '--probe' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        probe()
    else:
        unittest.main()
