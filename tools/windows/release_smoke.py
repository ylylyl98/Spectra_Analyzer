"""Release-only diagnostic, never distributed. Run with --output NEW_DIRECTORY.

Source: build-venv Python tools/windows/release_smoke.py --output DIRECTORY
Frozen: bundle/diagnostics/SpectraAnalyzerDiagnostics.exe --output DIRECTORY
Uses isolated preferences and synthetic data; requires a Windows desktop session.
"""
from pathlib import Path
from dataclasses import replace
import argparse
import json
import sys
import tempfile
import time
import traceback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error('--output must be new or empty')
    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    report = dict(status='failed', frozen=bool(getattr(sys, 'frozen', False)), errors=[],
                  scope='Qt Quick rendering, synthetic XY/DAT export, Paste Format apply/adjust/undo; no high-DPI claim')
    app = host = None
    original_settings = None
    try:
        from PySide6.QtCore import QEvent, QObject, QSettings, QTimer
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlEngine, QQmlExpression
        import numpy as np
        import spectra_app.main_window as owner_module
        from spectra_app.quick_root import QuickApplication
        from spectra_app.workbook_state import create_comparison, add_curve
        from spectra_app.plot_templates import FormatApplicationOptions
        from spectra_app.app_version import APP_VERSION
        from run_qt import configure_application_fonts

        with tempfile.TemporaryDirectory(prefix='spectra-release-') as temp:
            scratch = Path(temp)
            original_settings = owner_module.QSettings

            class IsolatedSettings(QSettings):
                def __init__(self, *args, **kwargs):
                    super().__init__(str(scratch / 'preferences.ini'), QSettings.IniFormat)
                    self.setFallbacksEnabled(False)

            owner_module.QSettings = IsolatedSettings
            app = QGuiApplication(['SpectraAnalyzerDiagnostics'])
            configure_application_fonts(app)
            host = QuickApplication()
            owner = host.controller
            owner._show_error = lambda text: report['errors'].append(str(text))
            report['version'] = APP_VERSION
            assert Path(owner._annotation_settings.fileName()).resolve().parent == scratch
            host.engine.warnings.connect(lambda values: report['errors'].extend(
                error.toString() for error in values))
            host.show()

            def wait(predicate, label):
                deadline = time.monotonic() + 15
                while not predicate():
                    if time.monotonic() > deadline:
                        raise RuntimeError('Timed out: ' + label)
                    app.processEvents()
                    time.sleep(.01)

            def exercise():
                try:
                    wait(lambda: host.window.isVisible() and host.window.isExposed()
                         and host.window.width() > 0 and host.window.height() > 0,
                         'Quick root exposed and laid out')
                    source = scratch / 'curve.csv'
                    x = np.arange(21, dtype=float)
                    np.savetxt(source, np.column_stack((x, x*x + 2)), delimiter=',')
                    book = owner.import_xy_files([source])
                    assert book is not None
                    comparison = create_comparison(owner.workspace, 'Release smoke')
                    add_curve(owner.workspace, comparison.comparison_id, book.sheet_ids[0], 0, 1)
                    xy = owner.open_xy_comparison(comparison.comparison_id)
                    def xy_ready():
                        scene = xy._scene
                        if scene is None or not xy.presentation_eligible():
                            return False
                        item = scene._item
                        if item is None or item.width() < 1 or item.height() < 1:
                            return False
                        image_item = item.findChild(QObject, 'XYCanvasImage')
                        if image_item is None:
                            return False
                        expression = QQmlExpression(QQmlEngine.contextForObject(image_item),
                                                    image_item, 'status === Image.Ready')
                        ready, undefined = expression.evaluate()
                        images = scene.provider.images
                        return (not undefined and not expression.hasError() and ready
                                and bool(images) and not list(images.values())[-1].isNull())

                    wait(xy_ready, 'visible XY frame loaded')
                    assert np.array_equal(xy.axes.lines[0].get_ydata(), x*x + 2)
                    svg = xy.export(output / 'xy-export.svg').read_bytes()
                    assert b'<svg' in svg and len(svg) > 1000
                    image = list(xy._scene.provider.images.values())[-1]
                    assert not image.isNull() and image.save(str(output / 'xy-render.png'))
                    report['xy'] = dict(points=len(x), svg_bytes=len(svg))

                    source = scratch / 'map.dat'
                    source.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                        f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)), encoding='utf-8')
                    owner._load_dat_file_direct(source)
                    session = owner.workspace.datasets[owner.workspace.active_dataset_id]
                    formal = owner.open_graph_document()
                    baseline = session.presentation
                    owner.set_plot_presentation(replace(baseline, width_in=5.1, aspect_locked=False))
                    owner.copy_plot_settings()
                    owner.set_plot_presentation(replace(baseline, width_in=4.1, aspect_locked=False))
                    target_baseline = session.presentation
                    targets = [(session.dataset_id, session.active_graph_id)]
                    options = FormatApplicationOptions(layout=True, typography=False, axis=False,
                                                       colormap=False, colorbar=False)
                    result = owner.paste_plot_settings(graph_targets=targets, options=options)
                    assert result and all(item['success'] for item in result), result
                    assert session.presentation.width_in == 5.1
                    result = owner.adjust_last_paste(replace(options, layout=False))
                    assert all(item['success'] for item in result), result
                    assert session.presentation == target_baseline
                    result = owner.adjust_last_paste(options)
                    assert all(item['success'] for item in result), result
                    assert session.presentation.width_in == 5.1
                    assert owner.undo_last_paste()
                    assert session.presentation == target_baseline
                    dialog = owner.create_paste_settings_dialog(current_only=True)
                    try:
                        assert dialog.bridge is not None
                        dialog.show()
                        wait(lambda: dialog.rootObject() is not None
                             and dialog.window().isVisible() and dialog.window().isExposed(),
                             'Paste Format dialog exposed')
                    finally:
                        dialog.close()
                        dialog.deleteLater()
                        app.sendPostedEvents(None, QEvent.DeferredDelete)
                        app.processEvents()
                    report['paste_format'] = dict(apply=True, adjust=True, undo=True, dialog=True)
                    owner._render_plot_page()
                    wait(lambda: formal.rendered_page is not None and bool(formal.preview.svg_bytes), 'DAT SVG')
                    svg = formal.preview.svg_bytes
                    assert b'<svg' in svg and len(svg) > 1000
                    (output / 'dat-export.svg').write_bytes(svg)
                    report['dat'] = dict(shape=list(owner.current_cube.z.shape), svg_bytes=len(svg))
                    saved = owner.save_project(scratch / 'smoke.spectra')
                    assert saved is not None and saved.is_file() and not owner.projectModified
                    report['normal_close_requested'] = True
                    host.window.close()
                    QTimer.singleShot(5000, lambda: fail('Normal close timed out'))
                except Exception:
                    fail(traceback.format_exc())

            def fail(message):
                report['errors'].append(message)
                app.exit(1)

            QTimer.singleShot(0, exercise)
            QTimer.singleShot(90000, lambda: fail('Diagnostic exceeded 90 seconds'))
            report['exit_code'] = app.exec()
            report['normal_shutdown'] = bool(report.get('normal_close_requested') and owner._closing)
            host.dispose()
            host = None
            owner_module.QSettings = original_settings
            if report['exit_code'] == 0 and report['normal_shutdown'] and not report['errors']:
                report['status'] = 'passed'
    except Exception:
        report['errors'].append(traceback.format_exc())
    finally:
        if host is not None:
            try:
                host.dispose()
            except Exception:
                report['status'] = 'failed'
                report['errors'].append(traceback.format_exc())
        if original_settings is not None:
            owner_module.QSettings = original_settings
        (output / 'release-smoke.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
