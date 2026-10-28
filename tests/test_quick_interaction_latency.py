"""Observable layout work and fidelity through the production Quick root.

Each contract runs in a fresh QGuiApplication process and checks its real exit
after QML teardown, including any stderr diagnostics.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest


CASES = ('zoom', 'title', 'title_export', 'regional_title', 'hidden_switch', 'mixed_capture', 'failed_activation')


class QuickInteractionLatencyTests(unittest.TestCase):
    def run_case(self, name):
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
        system_fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        if sys.platform == 'win32' and system_fonts.is_dir():
            environment.setdefault('QT_QPA_FONTDIR', str(system_fonts))
        result = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--case', name],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env=environment, timeout=90)
        if os.environ.get('SPECTRA_QUICK_LATENCY_LOGDIR'):
            folder = Path(os.environ['SPECTRA_QUICK_LATENCY_LOGDIR'])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f'{name}-stdout.log').write_text(result.stdout, encoding='utf8')
            (folder / f'{name}-stderr.log').write_text(result.stderr, encoding='utf8')
            (folder / f'{name}-exit-code.txt').write_text(str(result.returncode) + '\n', encoding='ascii')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn(name + ': PASS after Quick teardown', result.stdout)


def _wrapper(name):
    def test(self):
        self.run_case(name)
    return test


for _name in CASES:
    setattr(QuickInteractionLatencyTests, 'test_' + _name, _wrapper(_name))


class ActualInteractions(unittest.TestCase):
    def setUp(self):
        from tests.quick_application_helpers import application, pump
        self.fixture = application()
        self.host, self.folder, self.errors = self.fixture.__enter__()
        self.addCleanup(self.fixture.__exit__, None, None, None)
        self.w = self.host.controller
        self.host.window.resize(1500, 1000)
        self.assertTrue(self.w.request_outer_view('analysis'))
        pump(100)
        self.sessions, self.cubes, self.sources = [], {}, {}
        import numpy as np
        from dataclasses import replace
        for index in range(2):
            path = self.folder / f'owner-{index}.dat'
            energy, gate = np.linspace(1., 5., 61), np.linspace(-4., 4., 5)
            values = 3 + index + np.sin(energy[:, None]) + gate[None, :] / 20
            np.savetxt(path, np.column_stack((energy, values)),
                       header='Energy(eV) ' + ' '.join(map(str, gate)), comments='')
            self.sources[path] = path.read_bytes()
            self.w._load_dat_file_direct(path)
            session = self.w.workspace.active
            self.sessions.append(session)
            self.w.set_plot_presentation(replace(session.presentation,
                frame_width_in=2.5 + index / 5, frame_height_in=1.8 + index / 5))
            self.w.plot_page.set_zoom('100%')
            cube = session.current_cube
            self.cubes[session.dataset_id] = (cube, cube.energy.copy(), cube.gate.copy(), cube.z.copy())
        pump(150)

    def check_data(self):
        import numpy as np
        for session in self.sessions:
            cube, energy, gate, z = self.cubes[session.dataset_id]
            self.assertIs(session.current_cube, cube)
            for actual, expected in ((cube.energy, energy), (cube.gate, gate), (cube.z, z)):
                np.testing.assert_array_equal(actual, expected)
        for path, expected in self.sources.items():
            self.assertEqual(path.read_bytes(), expected)
        self.assertFalse(self.errors)

    def geometry(self):
        w = self.w
        return [ax.get_position().bounds for ax in (w._heat_ax, w._spec_ax, *[b.ax for b in w._heat_colorbars])]

    def text_geometry(self):
        w = self.w
        texts = []
        for ax in (w._heat_ax, *[bar.ax for bar in w._heat_colorbars]):
            texts.extend((ax.xaxis.label, ax.yaxis.label,
                          ax.xaxis.get_offset_text(), ax.yaxis.get_offset_text()))
            texts.extend(ax.get_xticklabels() + ax.get_yticklabels())
        if w._heat_signal_label is not None:
            texts.append(w._heat_signal_label)
        texts = [text for text in texts if text.get_visible() and text.get_text()]
        renderer = w.canvas.get_renderer()
        return ([(t.get_text(), t.get_horizontalalignment(), t.get_verticalalignment(), t.get_fontsize()) for t in texts],
                [(*t.get_position(), *t.get_window_extent(renderer).bounds) for t in texts])

    def check_text_geometry(self, before):
        import numpy as np
        metadata, geometry = self.text_geometry()
        self.assertEqual(metadata, before[0])
        np.testing.assert_allclose(geometry, before[1], rtol=0, atol=1e-10)

    def check_visible(self):
        import numpy as np
        from PySide6.QtCore import QObject
        from PySide6.QtQml import QQmlEngine, QQmlExpression
        from tests.quick_application_helpers import wait_until
        w = self.w
        image = self.host.dat_scene._item.findChild(QObject, 'analysisCanvasImage')
        def ready():
            expression = QQmlExpression(QQmlEngine.contextForObject(image), image, 'status === Image.Ready')
            value, undefined = expression.evaluate()
            return (not undefined and not expression.hasError() and value and image.isVisible()
                    and image.property('source').toString() == w.dat_canvas.imageUrl)
        wait_until(lambda: ready() and not w.canvas._render_timer.isActive())
        self.assertTrue(w.dat_canvas.imageUrl)
        self.assertFalse(w._annotation_layout_pending)
        frame = w._heat_ax.get_position().size * w.figure.get_size_inches()
        saved = w.workspace.active.presentation
        scale = 96 * w._analysis_display_scale() * w.canvas.device_pixel_ratio / w.figure.dpi
        np.testing.assert_allclose(frame, np.array(w._live_heatmap_frame_size(saved)) * scale,
                                   rtol=1e-12, atol=1e-12)
        center = tuple(sum(pair) / 2 for pair in (w._heat_ax.get_xlim(), w._heat_ax.get_ylim()))
        display = w._heat_ax.transData.transform(center)
        np.testing.assert_allclose(w._heat_ax.transData.inverted().transform(display), center, atol=1e-12)
        from matplotlib.backend_bases import MouseEvent
        event = MouseEvent('button_press_event', w.canvas, *display, button=1)
        self.assertIs(event.inaxes, w._heat_ax)
        np.testing.assert_allclose((event.xdata, event.ydata), center, atol=1e-12)

    def case_zoom(self):
        from unittest.mock import patch
        from tests.quick_application_helpers import pump
        w = self.w
        self.prepare_pages()
        w.request_outer_view('analysis')
        pump(120)
        before = w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim()
        with patch.object(w, '_position_live_plots', wraps=w._position_live_plots) as position:
            w.plot_page.set_zoom('150%')
            self.assertEqual(position.call_count, 1, 'One owner zoom needs one live layout')
            pump(120)
            self.check_visible()
        with patch.object(w, '_position_live_plots', wraps=w._position_live_plots) as position:
            w.plot_page.set_zoom('150%')
            w.plot_page.displayScaleChanged.emit(.5)
            pump(80)
            self.assertEqual(position.call_count, 0, 'Formal preview scale is not DAT scale')
        w.plot_page.set_zoom('Fit')
        pump(120)
        self.check_visible()
        old_scale = w._analysis_display_scale()
        self.host.window.resize(1200, 800)
        pump(160)
        self.assertNotEqual(w._analysis_display_scale(), old_scale)
        self.check_visible()
        # Exercise the actual DPR boundary, then restore the native ratio.
        ratio = w.canvas.device_pixel_ratio
        w.dat_canvas.set_device_pixel_ratio(2.)
        self.check_visible()
        w.dat_canvas.set_device_pixel_ratio(ratio)
        self.check_visible()
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim()), before)
        self.check_data()

    def case_title(self):
        import numpy as np
        from unittest.mock import patch
        from tests.quick_application_helpers import pump
        w = self.w
        before = self.geometry()
        text_before = self.text_geometry()
        self.assertNotEqual(w._heat_colorbars[0].ax.xaxis.label.get_position(), (-.10, .8),
                            'Fixture must exercise measured signal-label clearance')
        pixels_before = np.asarray(w.canvas.buffer_rgba()).copy()
        limits = w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim()
        old_image = w.dat_canvas.imageUrl
        with patch.object(w.figure, 'draw_without_rendering', wraps=w.figure.draw_without_rendering) as measure, \
             patch.object(w._annotation_settings, 'sync', wraps=w._annotation_settings.sync) as sync:
            self.assertTrue(w.dat_properties.setLabel('plot_title', 'Latest clipped title', w.dat_properties.ownerToken))
            self.assertTrue(w.dat_properties.finishLabels(w.dat_properties.ownerToken))
            pump(120)
            self.assertEqual(measure.call_count, 0, 'Title overlay does not resize axes or colorbars')
            self.assertEqual(sync.call_count, 1)
        self.assertEqual(w._heat_ax.get_title(), 'Latest clipped title')
        self.assertIs(w._heat_ax.title.get_transform(), w._heat_ax.transAxes)
        self.assertTrue(w._heat_ax.title.get_clip_on())
        self.assertFalse(w._heat_ax.title.get_in_layout())
        self.assertNotEqual(w.dat_canvas.imageUrl, old_image)
        self.assertFalse(np.array_equal(np.asarray(w.canvas.buffer_rgba()), pixels_before),
                         'The title commit must reach the actual raster pixels')
        np.testing.assert_array_equal(self.geometry(), before)
        self.check_text_geometry(text_before)
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim()), limits)
        self.check_visible()
        w._finish_history_edit()
        w.undo_edit()
        self.assertNotEqual(w._heat_ax.get_title(), 'Latest clipped title')
        w.redo_edit()
        self.assertEqual(w._heat_ax.get_title(), 'Latest clipped title')
        self.check_data()

    def case_regional_title(self):
        from dataclasses import replace
        from unittest.mock import patch
        from tests.quick_application_helpers import pump
        w = self.w
        self.assertTrue(w.dat_properties.setChoice('color_scale', '3', w.dat_properties.ownerToken))
        # Auto uses the last prepared owner's frame; a title must retain it.
        w.set_plot_presentation(replace(w.workspace.active.presentation,
                                       frame_width_in=None, frame_height_in=None))
        w.open_graph_document()
        w._render_plot_page()
        page = w.plot_page.rendered_page
        self.assertIsNotNone(page)
        w.request_outer_view('analysis')
        pump(140)
        self.assertEqual(len(w._heat_colorbars), 3)
        frame = w._live_heatmap_frame_size(w.workspace.active.presentation)
        text_before, axes_before = self.text_geometry(), self.geometry()
        with patch.object(w.figure, 'draw_without_rendering', wraps=w.figure.draw_without_rendering) as measure:
            w.dat_properties.setLabel('plot_title', 'Regional title', w.dat_properties.ownerToken)
            w.dat_properties.finishLabels(w.dat_properties.ownerToken)
            pump(120)
            self.assertEqual(measure.call_count, 0)
        self.check_text_geometry(text_before)
        import numpy as np
        np.testing.assert_array_equal(self.geometry(), axes_before)
        self.assertEqual(w._live_heatmap_frame_size(w.workspace.active.presentation), frame)
        self.assertIsNone(w.plot_page.rendered_page)
        self.check_visible()
        self.check_data()

    def case_title_export(self):
        from unittest.mock import patch
        from tests.quick_application_helpers import pump
        import numpy as np
        w = self.w
        self.prepare_pages()
        w.request_outer_view('analysis')
        pump(120)
        self.check_visible()
        w.request_outer_view('workspace')
        self.assertFalse(w._annotation_layout_pending)
        w.dat_properties.setLabel('plot_title', 'Immediate committed export', w.dat_properties.ownerToken)
        w.dat_properties.finishLabels(w.dat_properties.ownerToken)
        self.assertFalse(w._annotation_edit_pending)
        self.assertFalse(w._annotation_layout_pending, 'Title commit does not itself dirty geometry')
        # No event pump between FocusOut/Enter commit and the export action.
        output = w.export_plot_page('svg')
        self.assertIn('Immediate committed export', output.read_text(encoding='utf8'))
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())
        w.request_outer_view('analysis')
        pump(120)
        self.check_visible()
        self.assertTrue(w.dat_properties.setChoice('title_preset', 'Current group', w.dat_properties.ownerToken))
        pump(120)
        pixels_before, text_before = np.asarray(w.canvas.buffer_rgba()).copy(), self.text_geometry()
        with patch.object(w.figure, 'draw_without_rendering', wraps=w.figure.draw_without_rendering) as measure:
            self.assertTrue(w.dat_properties.setTemplate('title_pattern', 'Applied template title', w.dat_properties.ownerToken))
            self.assertTrue(w.dat_properties.command('template_apply', w.dat_properties.ownerToken))
            pump(120)
            self.assertEqual(measure.call_count, 0)
        self.assertEqual(w._heat_ax.get_title(), 'Applied template title')
        self.check_text_geometry(text_before)
        self.assertFalse(np.array_equal(np.asarray(w.canvas.buffer_rgba()), pixels_before))
        output = w.export_plot_page('svg')
        self.assertIn('Applied template title', output.read_text(encoding='utf8'))
        self.assertEqual(w.plot_page.rendered_page.owner_token, w._plot_owner_token())
        self.check_visible()
        self.check_data()

    def prepare_pages(self):
        from tests.quick_application_helpers import pump
        pages = {}
        for session in self.sessions:
            self.w.open_graph_document(session.dataset_id)
            self.w._render_plot_page()
            pages[session.dataset_id] = self.w.plot_page.rendered_page
            self.assertIsNotNone(pages[session.dataset_id])
        pump(150)
        return pages

    def case_hidden_switch(self):
        from unittest.mock import patch
        from tests.quick_application_helpers import pump
        w = self.w
        pages = self.prepare_pages()
        incoming = self.sessions[0]
        page, source_svg = pages[incoming.dataset_id], bytes(pages[incoming.dataset_id].svg_bytes)
        expected_limits = incoming.view.x_limits, incoming.view.y_limits, incoming.view.color_limits
        with patch.object(w.figure, 'draw_without_rendering', wraps=w.figure.draw_without_rendering) as measure:
            w.open_graph_document(incoming.dataset_id)
            pump(100)
            self.assertEqual(measure.call_count, 0, 'Hidden Analysis geometry waits until Analysis is exposed')
            self.assertTrue(w._annotation_layout_pending)
            self.assertIs(w.plot_page.rendered_page, page)
            snapshot = w._capture_project_workspace()
            self.assertIs(snapshot.datasets[incoming.dataset_id].current_cube, incoming.current_cube)
            self.assertEqual(w.export_plot_page('svg').read_bytes(), source_svg)
            self.assertEqual(measure.call_count, 0)
            w.request_outer_view('analysis')
            pump(140)
            self.assertGreaterEqual(measure.call_count, 1)
            self.check_visible()
        self.assertEqual((w._heat_ax.get_xlim(), w._heat_ax.get_ylim(), w._heat_mesh.get_clim()), expected_limits)
        self.assertIs(w.plot_page.rendered_page, page)
        self.assertEqual(page.svg_bytes, source_svg)
        self.check_data()

    def case_mixed_capture(self):
        from unittest.mock import patch
        from tests.quick_application_helpers import pump
        import numpy as np
        w = self.w
        with patch.object(w.figure, 'draw_without_rendering', wraps=w.figure.draw_without_rendering) as measure:
            for field, value in (('plot_title', 'Mixed title'), ('y_axis_name', 'Long axis label'),
                                 ('signal_title', 'Measured signal')):
                self.assertTrue(w.dat_properties.setLabel(field, value, w.dat_properties.ownerToken))
            self.assertTrue(w.dat_properties.finishLabels(w.dat_properties.ownerToken))
            self.assertEqual(measure.call_count, 1, 'Mixed edits still measure tick/offset and signal geometry')
        pump(100)
        self.check_visible()
        # Explicit capture finishes a label draft without drawing; its required
        # layout must survive until a later title-only finish or view exposure.
        w.dat_properties.setLabel('y_axis_unit', 'mV', w.dat_properties.ownerToken)
        w.capture_active_dataset(render=False)
        self.assertTrue(w._annotation_layout_pending)
        with patch.object(w.figure, 'draw_without_rendering', wraps=w.figure.draw_without_rendering) as measure:
            w.dat_properties.setLabel('plot_title', 'Saved title', w.dat_properties.ownerToken)
            w.dat_properties.finishLabels(w.dat_properties.ownerToken)
            self.assertEqual(measure.call_count, 1)
        w._finish_history_edit()
        session = w.workspace.active
        data = session.current_cube.z.copy()
        path = w.save_project(self.folder / 'latency.spectra')
        with patch('spectra_app.main_window.load_dat_cube', side_effect=AssertionError('source reread')):
            self.assertTrue(w.open_project(path))
        pump(140)
        self.assertEqual(w._dat_annotation('plot_title'), 'Saved title')
        self.assertEqual(w._dat_annotation('y_axis_unit'), 'mV')
        self.assertEqual(w._dat_annotation('signal_title'), 'Measured signal')
        self.assertFalse(w._annotation_edit_pending)
        self.assertFalse(w._annotation_edit_requires_layout)
        np.testing.assert_array_equal(w.current_cube.z, data)
        self.assertFalse(self.errors)

    def case_failed_activation(self):
        from unittest.mock import patch
        from tests.quick_application_helpers import pump
        w = self.w
        self.prepare_pages()
        previous, incoming = self.sessions[1], self.sessions[0]
        original = w._restore_effective_plot_limits
        def fail_incoming(*args, **kwargs):
            if w.workspace.active is incoming:
                raise RuntimeError('injected incoming activation failure')
            return original(*args, **kwargs)
        with patch.object(w, '_restore_effective_plot_limits', side_effect=fail_incoming):
            with self.assertRaisesRegex(RuntimeError, 'injected incoming activation failure'):
                w.activate_dataset(incoming.dataset_id)
        self.assertIs(w.workspace.active, previous)
        self.assertFalse(w._restoring_dataset)
        self.assertTrue(w._annotation_layout_pending)
        w.request_outer_view('analysis')
        pump(140)
        self.check_visible()
        self.assertIs(w.current_cube, previous.current_cube)
        w.activate_dataset(incoming.dataset_id)
        pump(120)
        self.check_visible()
        self.check_data()


if __name__ == '__main__':
    if '--case' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        name = sys.argv[sys.argv.index('--case') + 1]
        suite = unittest.TestSuite([ActualInteractions('case_' + name)])
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
        if result.wasSuccessful():
            print(name + ': PASS after Quick teardown', flush=True)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main()
