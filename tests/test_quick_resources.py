"""QML bundle destinations and Preview invalidation without running a packager."""
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from tests import test_standalone_build as build_tests


ROOT = Path(__file__).resolve().parents[1]


class QuickBundleResourceTests(unittest.TestCase):
    def test_spec_keeps_nested_qml_components_and_assets_at_package_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            windows = source / 'tools/windows'
            windows.mkdir(parents=True)
            spec = windows / 'SpectraAnalyzer.spec'
            shutil.copyfile(ROOT / 'tools/windows/SpectraAnalyzer.spec', spec)
            qml = source / 'spectra_app/qml'
            qml.mkdir(parents=True)
            (source / 'spectra_app/app_version.py').write_text("APP_VERSION = '0.1.2'\n")
            resources = {
                'MainCommands.qml': 'import QtQuick\nItem {}\n',
                'TaskNavigation.qml': 'import QtQuick\nItem {}\n',
                'ProcessingPanel.qml': 'import QtQuick\nItem {}\n',
                'controls/CommandButton.qml': 'import QtQuick\nItem {}\n',
                'controls/qmldir': 'CommandButton 1.0 CommandButton.qml\n',
                'images/toggle.svg': '<svg xmlns="http://www.w3.org/2000/svg"/>',
            }
            for name, content in resources.items():
                path = qml / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')

            captured = {}

            def analysis(*args, **kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(pure=[], scripts=[], binaries=[], datas=kwargs['datas'])

            hooks = types.ModuleType('PyInstaller.utils.hooks')
            hooks.copy_metadata = lambda distribution: []
            modules = {name: types.ModuleType(name) for name in ('PyInstaller', 'PyInstaller.utils')}
            modules[hooks.__name__] = hooks
            with patch.dict(sys.modules, modules), patch.dict(os.environ, {'SPECTRA_DIAGNOSTICS_SCRIPT': ''}):
                runpy.run_path(str(spec), init_globals={
                    'SPECPATH': str(windows), 'Analysis': analysis,
                    'PYZ': lambda *args, **kwargs: None,
                    'EXE': lambda *args, **kwargs: None,
                    'COLLECT': lambda *args, **kwargs: None,
                })

            collected = {(Path(path).relative_to(qml).as_posix(), destination)
                         for path, destination in captured['datas']
                         if Path(path).is_relative_to(qml)}
            expected = {(name, (Path('spectra_app/qml') / Path(name).parent).as_posix())
                        for name in resources}
            self.assertEqual(collected, expected)


@unittest.skipUnless(build_tests.POWERSHELL, 'Windows PowerShell is required for Preview routing checks')
class QuickPreviewResourceTests(unittest.TestCase):
    def setUp(self):
        self.build = build_tests.StandaloneBuildRouteTests(methodName='runTest')
        self.build.setUp()
        self.addCleanup(self.build.doCleanups)
        self.qml = self.build.repo / 'spectra_app/qml'
        self.qml.mkdir()
        self.panel = self.qml / 'ProcessingPanel.qml'
        self.panel.write_text('import QtQuick\nItem {}\n')

    def resource_sources(self, manifest):
        return {item['path']: item['sha256'] for item in manifest['sources']
                if item['path'].startswith('spectra_app\\qml\\')}

    def test_preview_invalidates_work_for_changed_added_and_removed_qml_resources(self):
        _, previous = self.build.run_build()
        self.assertEqual(set(self.resource_sources(previous)), {'spectra_app\\qml\\ProcessingPanel.qml'})
        asset = self.qml / 'images/toggle.svg'
        for change in ('edit', 'add asset', 'remove asset'):
            with self.subTest(change=change):
                if change == 'edit':
                    self.panel.write_text('import QtQuick\nItem { width: 200 }\n')
                elif change == 'add asset':
                    asset.parent.mkdir()
                    asset.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
                else:
                    asset.unlink()
                _, current = self.build.run_build()
                self.assertNotEqual(previous['cache']['identity'], current['cache']['identity'])
                # Removing the asset returns to the earlier resource set, whose
                # existing analysis is safe to reuse under that exact identity.
                self.assertEqual(current['cache']['workReused'], change == 'remove asset')
                self.assertTrue(current['cache']['dependencyStampReused'])
                self.assertNotEqual(self.resource_sources(previous), self.resource_sources(current))
                self.assertEqual('spectra_app\\qml\\images\\toggle.svg' in self.resource_sources(current),
                                 asset.exists())
                previous = current
        _, unchanged = self.build.run_build()
        self.assertEqual(previous['cache']['identity'], unchanged['cache']['identity'])
        self.assertTrue(unchanged['cache']['workReused'])

    def test_qml_changed_during_freeze_is_rejected_before_manifest(self):
        driver = self.build.driver.read_text(encoding='utf-8')
        driver = driver.replace("Join-Path $WorkingDirectory 'run_qt.py'",
                                "Join-Path $WorkingDirectory 'spectra_app\\qml\\ProcessingPanel.qml'")
        self.build.driver.write_text(driver, encoding='utf-8')
        output = self.build.root / 'changed-qml'
        with patch.dict(os.environ, {'FAKE_MUTATE_STAGE': 'pyinstaller'}):
            message = self.build.run_build(output=output, success=False)
        self.assertIn('sources changed', message)
        self.assertFalse((output / 'build-manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
