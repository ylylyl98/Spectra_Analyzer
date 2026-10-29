"""Cheap build-route checks; no real dependency install or packager is invoked."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'tools/windows/standalone_build_environment.py'
POWERSHELL = shutil.which('powershell.exe')


class EnvironmentInspectionTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('standalone_build_environment', HELPER)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lock = Path(self.temp.name) / 'requirements.txt'
        self.lock.write_text('Example_Package==1.2.3\n', encoding='utf-8')

    def distribution(self, version='1.2.3', metadata='metadata'):
        class Distribution:
            def __init__(self):
                self.metadata = {'Name': 'Example-Package'}
                self.version = version

            def locate_file(self, relative):
                return Path('C:/build/Lib/site-packages') / relative

            def read_text(self, name):
                return metadata if name == 'METADATA' else 'record'

        return Distribution()

    def inspect(self, distributions):
        with patch.object(self.helper.importlib.metadata, 'distributions', return_value=distributions):
            return self.helper.inspect_environment(self.lock)

    def test_normalized_locked_versions_match(self):
        result = self.inspect([self.distribution()])
        self.assertTrue(result['lockedVersionsMatch'])
        self.assertEqual(result['dependencies'], ['Example-Package==1.2.3'])

    def test_changed_or_missing_version_invalidates_lock(self):
        self.assertFalse(self.inspect([self.distribution('9.0')])['lockedVersionsMatch'])
        self.assertFalse(self.inspect([])['lockedVersionsMatch'])

    def test_same_version_metadata_drift_changes_fingerprint(self):
        first = self.inspect([self.distribution()])
        second = self.inspect([self.distribution(metadata='changed')])
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])

    def test_duplicate_distribution_invalidates_lock(self):
        self.assertFalse(self.inspect([self.distribution(), self.distribution()])['lockedVersionsMatch'])

    def test_unpinned_requirement_fails(self):
        self.lock.write_text('Example-Package>=1.0\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.inspect([])


@unittest.skipUnless(POWERSHELL, 'Windows PowerShell is required for build routing checks')
class StandaloneBuildRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'source'
        self.windows = self.repo / 'tools/windows'
        self.windows.mkdir(parents=True)
        for name in ('Build-Standalone.ps1', 'standalone_build_environment.py',
                     'requirements-standalone.txt', 'SpectraAnalyzer.spec', 'SpectraAnalyzer.iss',
                     'standalone-owner.ini', 'THIRD_PARTY_NOTICES.txt'):
            shutil.copyfile(ROOT / 'tools/windows' / name, self.windows / name)
        shutil.copytree(ROOT / 'tools/windows/licenses', self.windows / 'licenses')
        (self.repo / 'spectra_app').mkdir()
        (self.repo / 'spectra_app/app_version.py').write_text("APP_VERSION = '0.1.2'\n")
        (self.repo / 'run_qt.py').write_text('print("first")\n')
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        self.python = self.tools / 'python.ps1'
        self.inno = self.tools / 'ISCC.exe'
        self.inno.write_text('fake compiler')
        self.cache = self.root / 'cache'
        self.state = self.root / 'environment.json'
        self.set_environment()
        self.python.write_text(r'''$global:LASTEXITCODE=0
if ($args[0] -eq '-') {
    $source = $input -join "`n"
    if ($source -like '*import ast*') { '0.1.2' }
    elseif ($source -like '*import json, platform, struct, sys*') { @{version='3.14.0';bits=64;prefix=$env:FAKE_TOOLS;base_prefix=($env:FAKE_TOOLS + '\base')} | ConvertTo-Json -Compress }
    else { throw 'Unexpected Python stdin source.' }
} else { Get-Content -Raw -LiteralPath $env:FAKE_STATE }
''', encoding='utf-8')
        self.driver = self.root / 'driver.ps1'
        self.driver.write_text(r'''param([string]$BuildScript,[string]$Output,[string]$Python,[string]$Inno,[string]$Mode,[string]$Cache,[string]$State,[string]$Tools,[string]$Diagnostic)
Import-Module Microsoft.PowerShell.Utility,Microsoft.PowerShell.Archive
$env:FAKE_TOOLS=$Tools
$env:FAKE_STATE=$State
$env:FAKE_INNO=$Inno
$env:PATH='caller-path-must-not-leak'
$env:PYTHONPATH='caller-pythonpath'
$env:PYTHONHOME='caller-pythonhome'
$env:VIRTUAL_ENV='caller-venv'
$env:PYINSTALLER_CONFIG_DIR='caller-config'
$env:SPECTRA_DIAGNOSTICS_SCRIPT='caller-diagnostic'
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$Wait,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
    if ($env:PATH -like '*caller-*' -or $env:PYTHONPATH -or $env:PYTHONHOME -or $env:VIRTUAL_ENV) { throw 'Caller environment leaked.' }
    if ($WindowStyle -ne 'Hidden') { throw 'Build tool window must be hidden.' }
    $arguments=@($ArgumentList | ForEach-Object { $_.Trim('"') })
    $entry=@{arguments=$arguments;config=$env:PYINSTALLER_CONFIG_DIR;diagnostic=$env:SPECTRA_DIAGNOSTICS_SCRIPT}
    $entry | ConvertTo-Json -Compress | Add-Content -LiteralPath ($State + '.calls')
    '' | Set-Content -LiteralPath $RedirectStandardOutput
    '' | Set-Content -LiteralPath $RedirectStandardError
    if ($arguments -contains 'PyInstaller') {
        $dist=$arguments[[array]::IndexOf($arguments,'--distpath') + 1]
        $work=$arguments[[array]::IndexOf($arguments,'--workpath') + 1]
        New-Item -ItemType Directory -Path (Join-Path $dist 'SpectraAnalyzer'),(Join-Path $work 'SpectraAnalyzer') -Force | Out-Null
        'frozen app' | Set-Content -LiteralPath (Join-Path $dist 'SpectraAnalyzer\SpectraAnalyzer.exe')
        'cached analysis' | Set-Content -LiteralPath (Join-Path $work 'SpectraAnalyzer\Analysis-00.toc')
    } elseif ($FilePath -eq $env:FAKE_INNO) {
        'installer' | Set-Content -LiteralPath (Join-Path $Output 'SpectraAnalyzer-0.1.2-Setup.exe')
    } elseif ($arguments -contains 'install') {
        $snapshot=Get-Content -Raw -LiteralPath $State | ConvertFrom-Json
        $snapshot.lockedVersionsMatch=$true
        $snapshot | ConvertTo-Json | Set-Content -LiteralPath $State
    }
    if ($env:FAKE_MUTATE_STAGE -and $RedirectStandardOutput -like ('*\' + $env:FAKE_MUTATE_STAGE + '.stdout.log')) {
        Add-Content -LiteralPath (Join-Path $WorkingDirectory 'run_qt.py') -Value '# changed during build'
    }
    if ($env:FAKE_FAIL_STAGE -and $RedirectStandardOutput -like ('*\' + $env:FAKE_FAIL_STAGE + '.stdout.log')) {
        return [pscustomobject]@{ExitCode=7}
    }
    [pscustomobject]@{ExitCode=0}
}
try {
    $parameters=@{OutputDirectory=$Output;BuildPython=$Python;BuildDate='2026-10-23'}
    if ($Mode) { $parameters.Mode=$Mode }
    if ($Cache) { $parameters.CacheDirectory=$Cache }
    if ($Inno) { $parameters.InnoCompiler=$Inno }
    if ($Diagnostic) { $parameters.DiagnosticScript=$Diagnostic }
    & $BuildScript @parameters | Out-Null
} catch { $failure=$_ } finally {
    if ($env:PATH -ne 'caller-path-must-not-leak' -or $env:PYTHONPATH -ne 'caller-pythonpath' -or $env:PYTHONHOME -ne 'caller-pythonhome' -or $env:VIRTUAL_ENV -ne 'caller-venv' -or $env:PYINSTALLER_CONFIG_DIR -ne 'caller-config' -or $env:SPECTRA_DIAGNOSTICS_SCRIPT -ne 'caller-diagnostic') { throw 'Caller environment was not restored.' }
}
if ($failure) { Write-Error $failure; exit 1 }
''', encoding='utf-8')
        self.counter = 0

    def set_environment(self, fingerprint='original', matched=True):
        self.state.write_text(json.dumps({'fingerprint': fingerprint, 'lockedVersionsMatch': matched,
                                          'dependencies': ['Example==1.0']}), encoding='utf-8')

    def run_build(self, mode='Preview', *, inno=False, cache=None, diagnostic=None, output=None, success=True):
        self.counter += 1
        output = output or self.root / f'output-{self.counter}'
        command = [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                   '-File', str(self.driver), '-BuildScript', str(self.windows / 'Build-Standalone.ps1'),
                   '-Output', str(output), '-Python', str(self.python), '-Mode', mode,
                   '-State', str(self.state), '-Tools', str(self.tools)]
        if inno:
            command += ['-Inno', str(self.inno)]
        if mode in ('Preview', 'Installer') or cache is not None:
            command += ['-Cache', str(cache or self.cache)]
        if diagnostic:
            command += ['-Diagnostic', str(diagnostic)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return output, json.loads((output / 'build-manifest.json').read_text(encoding='utf-8-sig'))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def calls(self):
        return [json.loads(line) for line in Path(str(self.state) + '.calls').read_text(encoding='utf-8-sig').splitlines()]

    def test_preview_reuses_work_and_verified_dependencies_but_hashes_source_changes(self):
        output, first = self.run_build()
        (self.repo / 'run_qt.py').write_text('print("changed")\n')
        second_output, second = self.run_build()
        self.assertFalse(first['cache']['workReused'])
        self.assertTrue(second['cache']['workReused'])
        self.assertTrue(second['cache']['dependencyStampReused'])
        self.assertEqual(first['cache']['identity'], second['cache']['identity'])
        self.assertNotEqual(first['sources'], second['sources'])
        self.assertNotEqual(output, second_output)
        self.assertEqual(len([c for c in self.calls() if 'install' in c['arguments']]), 1)
        self.assertTrue(all('--clean' not in c['arguments'] for c in self.calls()))
        self.assertFalse(list(output.glob('*Setup.exe')))
        self.assertFalse(list(output.glob('*.zip')))
        self.assertIsNone(first['innoCompiler'])
        self.assertEqual([item['name'] for item in first['artifacts']], ['bundle\\SpectraAnalyzer\\SpectraAnalyzer.exe'])

    def test_installer_mode_builds_once_without_zip(self):
        output, manifest = self.run_build('Installer', inno=True)
        self.assertTrue((output / 'SpectraAnalyzer-0.1.2-Setup.exe').is_file())
        self.assertTrue((output / 'SpectraAnalyzer-0.1.2-Setup.exe.sha256').is_file())
        self.assertFalse(list(output.glob('*.zip')))
        self.assertEqual(len([c for c in self.calls() if 'PyInstaller' in c['arguments']]), 1)
        self.assertEqual(manifest['mode'], 'Installer')
        self.assertEqual([item['name'] for item in manifest['artifacts']], ['SpectraAnalyzer-0.1.2-Setup.exe'])
        _, preview = self.run_build()
        self.assertEqual(manifest['cache']['identity'], preview['cache']['identity'])
        self.assertTrue(preview['cache']['workReused'])

    def test_default_release_keeps_clean_installer_and_zip(self):
        output, manifest = self.run_build('', inno=True)
        self.assertTrue((output / 'SpectraAnalyzer-0.1.2-win64.zip').is_file())
        self.assertFalse(manifest['cache']['enabled'])
        self.assertIn('--clean', next(c['arguments'] for c in self.calls() if 'PyInstaller' in c['arguments']))
        self.assertEqual([item['name'] for item in manifest['artifacts']],
                         ['SpectraAnalyzer-0.1.2-Setup.exe', 'SpectraAnalyzer-0.1.2-win64.zip'])

    def test_lock_spec_diagnostic_and_environment_invalidate_work(self):
        _, previous = self.run_build()
        diagnostic = self.root / 'diagnostic.py'
        diagnostic.write_text('print("probe")\n')
        for changed in ('lock', 'spec', 'environment', 'runtime', 'diagnostic'):
            if changed == 'lock':
                with (self.windows / 'requirements-standalone.txt').open('a') as handle:
                    handle.write('\n# changed lock\n')
            elif changed == 'spec':
                with (self.windows / 'SpectraAnalyzer.spec').open('a') as handle:
                    handle.write('\n# changed spec\n')
            elif changed == 'environment':
                self.set_environment('drifted', matched=False)
            elif changed == 'runtime':
                with self.python.open('a') as handle:
                    handle.write('\n# changed build executable\n')
            _, current = self.run_build(diagnostic=diagnostic if changed == 'diagnostic' else None)
            self.assertNotEqual(previous['cache']['identity'], current['cache']['identity'])
            self.assertFalse(current['cache']['workReused'])
            if changed in ('lock', 'environment'):
                self.assertFalse(current['cache']['dependencyStampReused'])
            previous = current
        diagnostic.write_text('print("probe changed")\n')
        _, current = self.run_build(diagnostic=diagnostic)
        self.assertNotEqual(previous['cache']['identity'], current['cache']['identity'])

    def test_tool_requirements_and_nonempty_output_fail_early(self):
        self.assertIn('InnoCompiler', self.run_build('Installer', success=False))
        output = self.root / 'occupied'
        output.mkdir()
        (output / 'keep.txt').write_text('keep')
        self.assertIn('new or empty', self.run_build(output=output, success=False))
        self.assertEqual((output / 'keep.txt').read_text(), 'keep')

    def test_unsafe_cache_and_unowned_cache_are_rejected(self):
        self.assertIn('overlap', self.run_build(cache=self.repo / 'cache', success=False))
        self.assertIn('overlap', self.run_build(cache=self.tools / 'cache', success=False))
        self.assertIn('overlap', self.run_build(output=self.cache / 'output', success=False))
        self.cache.mkdir()
        (self.cache / 'unrelated.txt').write_text('keep')
        self.assertIn('owned', self.run_build(success=False))
        self.assertEqual((self.cache / 'unrelated.txt').read_text(), 'keep')

    def test_cache_lock_fails_without_reusing_busy_work(self):
        self.run_build()
        lock = self.cache / 'build.lock'
        # Windows file sharing rejects the builder's exclusive open while this handle exists.
        with lock.open('rb'):
            self.assertIn('already in use', self.run_build(success=False))

    def test_source_changes_during_freeze_or_installer_fail_without_manifest(self):
        for stage in ('pyinstaller', 'installer'):
            with patch.dict('os.environ', {'FAKE_MUTATE_STAGE': stage}):
                output = self.root / f'mutated-{stage}'
                message = self.run_build('Installer', inno=True, output=output, success=False)
            self.assertIn('sources changed', message)
            self.assertFalse((output / 'build-manifest.json').exists())

    def test_external_failure_restores_environment_and_releases_cache_lock(self):
        with patch.dict('os.environ', {'FAKE_FAIL_STAGE': 'pyinstaller'}):
            self.assertIn('pyinstaller failed (7)', self.run_build(success=False))
        self.run_build()


if __name__ == '__main__':
    unittest.main()
