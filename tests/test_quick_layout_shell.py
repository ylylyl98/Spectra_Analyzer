"""Responsive shell behavior through isolated production Quick windows."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class QuickLayoutShellTests(unittest.TestCase):
    def test_responsive_shell(self):
        result = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--probe'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
            timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn('Responsive shell: PASS after Quick teardown', result.stdout)


def probe():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from spectra_app.workbook_state import add_curve, create_comparison
    from tests.quick_application_helpers import application, click, item, pump

    with application() as (host, folder, errors):
        owner = host.controller
        project = item(host, 'projectNavigator')
        properties = item(host, 'dockedProperties')
        center = item(host, 'workspaceDocuments')

        def resize(width, height):
            host.window.resize(width, height)
            pump(100)

        def drag_divider(panel, offset, delta):
            point = panel.mapToScene(QPointF(offset, panel.height() / 2))
            end = point + QPointF(delta, 0)
            QTest.mousePress(host.window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
            QTest.mouseMove(host.window, end.toPoint(), 30)
            QTest.mouseRelease(host.window, Qt.LeftButton, Qt.NoModifier, end.toPoint())
            pump()

        resize(1440, 900)
        drag_divider(project, project.width() + 2, 70)
        drag_divider(properties, -2, -60)
        preferred = (project.width(), properties.width())
        assert preferred[0] > 280 and preferred[1] > 390, preferred
        stored = tuple(owner._annotation_settings.value('quick_shell/' + key, type=int)
                       for key in ('project_width', 'properties_width'))
        for width, height in ((1280, 800), (900, 620)):
            resize(width, height)
            assert center.width() >= 430, (width, center.width())
            assert project.isVisible() and project.width() >= 170
            assert properties.isVisible() and properties.width() >= 280
            assert center.height() >= 300, (width, center.height())
        # The checked Project command always corresponds to a usable panel.
        toggle = item(host, 'command_project')
        assert toggle.property('checked') and project.isVisible()
        click(host, toggle)
        assert not project.isVisible() and not toggle.property('checked')
        click(host, toggle)
        assert project.isVisible() and toggle.property('checked')
        resize(1440, 900)
        assert abs(project.width() - preferred[0]) <= 1, (project.width(), preferred)
        assert abs(properties.width() - preferred[1]) <= 1, (properties.width(), preferred)
        assert tuple(owner._annotation_settings.value('quick_shell/' + key, type=int)
                     for key in ('project_width', 'properties_width')) == stored

        source = folder / 'layout.csv'
        source.write_text('X,Y\n0,10\n1,20\n2,15\n', encoding='utf-8')
        book = owner.import_xy_files([source])
        graph = create_comparison(owner.workspace, 'Layout graph')
        add_curve(owner.workspace, graph.comparison_id, book.sheet_ids[0], 0, 1)
        owner.open_xy_comparison(graph.comparison_id)
        pump()
        assert owner.contextKind == 'xy'
        assert item(host, 'XYCommandsRoot').isVisible()
        band = item(host, 'contextCommandBand')
        assert 80 <= band.height() <= 82, band.height()
        owner.request_outer_view('analysis')
        pump()
        navigation = item(host, 'AnalysisNavigation')
        assert abs(band.height() - navigation.implicitHeight()) <= 1, (
            band.height(), navigation.implicitHeight())
        for width, height in ((900, 620), (1280, 800), (1440, 900)):
            resize(width, height)
            assert abs(band.height() - navigation.implicitHeight()) <= 1
        assert not errors, errors
    print('Responsive shell: PASS after Quick teardown', flush=True)


if __name__ == '__main__':
    if '--probe' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        probe()
    else:
        unittest.main()
