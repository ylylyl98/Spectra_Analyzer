"""Stable native identity and launcher bootstrap contracts."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class WindowsLaunchTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows native command-line boundary')
    def test_native_launcher_quotes_round_trip_through_python(self):
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():
            self.skipTest('Windows .NET Framework compiler is unavailable')
        source = r'''
using System;
using System.Diagnostics;
using System.Text;
internal static class BuildSettings { internal const string Repository = ""; internal const string AppId = ""; }
internal static class QuoteProbe {
    static int Main(string[] args) {
        StringBuilder command = new StringBuilder();
        for (int i = 1; i < args.Length; i++) command.Append(Launcher.QuoteArgument(args[i])).Append(' ');
        var start = new ProcessStartInfo(args[0], command.ToString());
        start.UseShellExecute = false;
        using (var process = Process.Start(start)) { process.WaitForExit(); return process.ExitCode; }
    }
}
'''
        with tempfile.TemporaryDirectory(prefix='spectra quote ') as directory:
            folder = Path(directory)
            harness = folder / 'QuoteProbe.cs'
            harness.write_text(source, encoding='utf-8')
            executable = folder / 'QuoteProbe.exe'
            built = subprocess.run([str(compiler), '/nologo', '/target:exe', '/main:QuoteProbe',
                '/reference:System.Windows.Forms.dll', f'/out:{executable}',
                str(ROOT / 'tools/windows/Launcher.cs'), str(harness)], capture_output=True, text=True, timeout=20)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            expected = ['', '项目 daily.spectra', 'quote"inside', 'tail\\', 'slash\\"quote', '\\\\server\\a b\\']
            result = subprocess.run([str(executable), sys.executable, '-X', 'utf8', '-c',
                'import json,sys; print(json.dumps(sys.argv[1:]))', *expected],
                capture_output=True, text=True, encoding='utf-8', timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), expected)

    def test_identity_and_icon_are_independent_of_working_directory(self):
        script = '''
import ctypes, json
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QMainWindow
from spectra_app.app_identity import APP_ID, ICON_PATH, configure_process_identity, configure_application_icon
configure_process_identity()
app = QApplication([])
configure_application_icon(app)
window = QMainWindow()
assert not window.windowIcon().isNull()
for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
    pixmap = window.windowIcon().pixmap(QSize(size, size))
    assert not pixmap.isNull(), size
if sys.platform == 'win32':
    value = ctypes.c_wchar_p()
    assert ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(value)) == 0
    assert value.value == APP_ID
    ctypes.windll.ole32.CoTaskMemFree(value)
print(json.dumps({'id': APP_ID, 'icon': str(ICON_PATH)}))
'''
        with tempfile.TemporaryDirectory(prefix='spectra launch ') as directory:
            env = dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM='offscreen')
            result = subprocess.run([sys.executable, '-c', 'import sys\n' + script], cwd=directory,
                                    env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout.strip())
        self.assertEqual(value['id'], 'SpectraAnalyzer.Desktop')
        self.assertTrue(Path(value['icon']).is_file())

    def bootstrap(self):
        spec = importlib.util.spec_from_file_location('windows_bootstrap', ROOT / 'tools/windows/bootstrap.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_bootstrap_preserves_unicode_spaces_and_empty_arguments(self):
        module = self.bootstrap()
        with tempfile.TemporaryDirectory(prefix='spectra launch ') as directory:
            root = Path(directory)
            entry = root / '入口 app.py'
            output = root / 'arguments.json'
            entry.write_text('import json, sys\nfrom pathlib import Path\n'
                             'Path(sys.argv[1]).write_text(json.dumps(sys.argv), encoding="utf-8")\n', encoding='utf-8')
            arguments = [str(output), '项目 daily.spectra', '', 'quote"inside', 'tail\\']
            result = module.run(entry, root / 'startup.log', arguments)
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.read_text()), [str(entry), *arguments])

    def test_bootstrap_reports_and_logs_failure(self):
        module = self.bootstrap()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / 'broken.py'
            entry.write_text('raise RuntimeError("startup probe failed")\n', encoding='utf-8')
            log = root / 'startup.log'
            with patch.object(module, 'show_error') as show:
                self.assertEqual(module.run(entry, log, []), 1)
            self.assertIn('startup probe failed', log.read_text(encoding='utf-8'))
            self.assertIn(str(log), show.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
