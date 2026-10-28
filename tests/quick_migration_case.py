"""Import-safe unittest cases backed by isolated, real production Quick hosts.

Subclass ``QuickMigrationCase`` and use ``self.host`` / ``self.controller`` in
normal test methods. Discovery keeps each method's original unittest identity.
Only its isolated child runs setUp, the method, and all application cleanup.
Modules and setUpClass/tearDownClass still run in the discovery parent: never
create a GUI application or host there. Put GUI fixtures in instance setUp.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


_CHILD_CASE = 'SPECTRA_QUICK_MIGRATION_CASE'


class QuickMigrationCase(unittest.TestCase):
    quick_timeout = 90

    def setUp(self):
        super().setUp()
        from tests.quick_application_helpers import application
        self.host, self.folder, self.quick_errors = self.enterContext(application())
        self.controller = self.host.controller

    def run(self, result=None):
        if os.environ.get(_CHILD_CASE) == self.id():
            return super().run(result)
        result = result if result is not None else self.defaultTestResult()
        result.startTest(self)
        try:
            with tempfile.TemporaryDirectory(prefix='spectra-quick-case-') as folder:
                report = Path(folder) / 'result.json'
                completed = subprocess.run(
                    [sys.executable, '-B', '-X', 'utf8', '-m',
                     'tests.quick_migration_case', self.id(), str(report)],
                    cwd=Path(__file__).resolve().parents[1],
                    env=dict(os.environ, QT_QPA_PLATFORM='offscreen',
                             QT_QUICK_BACKEND='software', **{_CHILD_CASE: self.id()}),
                    capture_output=True, text=True, encoding='utf-8',
                    timeout=self.quick_timeout)
                diagnostics = (f'Quick child exit: {completed.returncode}\n'
                               f'stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}')
                if not report.exists():
                    raise RuntimeError('Quick child produced no unittest result.\n' + diagnostics)
                payload = json.loads(report.read_text(encoding='utf-8'))
                if payload['testsRun'] != 1:
                    raise RuntimeError('Quick child did not execute exactly one case.\n' + diagnostics)
                reported = False
                for key, callback, exception in (
                        ('errors', result.addError, RuntimeError),
                        ('failures', result.addFailure, self.failureException),
                        ('expectedFailures', result.addExpectedFailure, self.failureException)):
                    for detail in payload[key]:
                        message = detail + '\n' + diagnostics
                        callback(self, (exception, exception(message), None))
                        reported = True
                for reason in payload['skipped']:
                    result.addSkip(self, reason)
                    reported = True
                for _ in payload['unexpectedSuccesses']:
                    result.addUnexpectedSuccess(self)
                    reported = True
                expected_exit = 1 if (payload['errors'] or payload['failures']
                                      or payload['unexpectedSuccesses']) else 0
                if completed.returncode != expected_exit:
                    error = RuntimeError('Quick child failed after test cleanup.\n' + diagnostics)
                    result.addError(self, (RuntimeError, error, None))
                    reported = True
                if completed.stderr:
                    error = self.failureException('Quick child emitted diagnostics.\n' + diagnostics)
                    result.addFailure(self, (self.failureException, error, None))
                    reported = True
                if not reported:
                    result.addSuccess(self)
        except self.failureException:
            result.addFailure(self, sys.exc_info())
        except Exception:
            result.addError(self, sys.exc_info())
        finally:
            result.stopTest(self)
        return result


def _main():
    name, destination = sys.argv[1:]
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    # TestResult has already run tearDown and registered application disposal.
    # The parent still waits for process exit, including native Qt destruction.
    payload = dict(testsRun=result.testsRun,
                   failures=[detail for _, detail in result.failures],
                   errors=[detail for _, detail in result.errors],
                   skipped=[reason for _, reason in result.skipped],
                   expectedFailures=[detail for _, detail in result.expectedFailures],
                   unexpectedSuccesses=[test.id() for test in result.unexpectedSuccesses])
    Path(destination).write_text(json.dumps(payload), encoding='utf-8')
    print(output.getvalue(), end='')
    if result.wasSuccessful():
        print(name + ': PASS after Quick teardown')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(_main())
