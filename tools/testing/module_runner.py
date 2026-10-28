"""One isolated unittest module; the supervisor owns final exit accounting."""
import atexit
import faulthandler
import json
import os
from pathlib import Path
import sys
import time
import unittest


from child_transport import install_recorder


def main():
    name, folder = sys.argv[1:3]
    folder = Path(folder)
    gate = os.environ.get('SPECTRA_ACCEPTANCE_START_GATE')
    if not gate or not Path(gate).is_file():
        raise RuntimeError('Owned Job startup gate was not released before module execution')
    install_recorder(folder)
    events = folder / 'events.jsonl'

    def emit(event, **fields):
        with events.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event=event, time=time.time(), pid=os.getpid(), **fields)) + '\n')
        if event != 'atexit_end':
            faulthandler.dump_traceback_later(float(os.environ.get('SPECTRA_FINAL_INACTIVITY_SECONDS', '180')) * .8)

    atexit.register(lambda: emit('atexit_end'))
    faulthandler.enable()
    emit('module_start', module=name, executable=sys.executable, prefix=sys.prefix, python_version=sys.version)
    if os.environ.get('SPECTRA_FINAL_REQUIRE_GUARD') == '1':
        guard = sys.modules.get('sitecustomize')
        if guard is None or getattr(getattr(guard, '_qtcore', None), 'QSettings', None) is not getattr(guard, 'SuiteGuardSettings', None):
            raise RuntimeError('Required QSettings sitecustomize guard did not initialize')
        emit('settings_guard_verified', path=os.environ['SPECTRA_SUITE_SETTINGS'])

    if sys.argv[3:] == ['--pytest']:
        from pytest_adapter import run
        return run(name, folder, emit)

    class Recorder(unittest.TextTestResult):
        def startTest(self, test):
            emit('test_start', id=test.id())
            super().startTest(test)

        def stopTest(self, test):
            super().stopTest(test)
            emit('test_stop', id=test.id())

        def addSuccess(self, test):
            super().addSuccess(test)
            emit('success', id=test.id())

        def addError(self, test, err):
            super().addError(test, err)
            emit('error', id=test.id(), traceback=self._exc_info_to_string(err, test))

        def addFailure(self, test, err):
            super().addFailure(test, err)
            emit('failure', id=test.id(), traceback=self._exc_info_to_string(err, test))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            emit('skip', id=test.id(), reason=reason)

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            emit('expected_failure', id=test.id(), traceback=self._exc_info_to_string(err, test))

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            emit('unexpected_success', id=test.id())

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            emit('subtest', id=subtest.id(), parent_id=test.id(),
                 outcome='success' if err is None else 'failure_or_error',
                 kind=None if err is None else ('failure' if issubclass(err[0], test.failureException) else 'error'),
                 traceback='' if err is None else self._exc_info_to_string(err, test))

    def flatten(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from flatten(item)
            else:
                yield item

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName(name)
    ids = [test.id() for test in flatten(suite)]
    (folder / 'loaded.json').write_text(json.dumps(dict(test_ids=ids, loader_errors=loader.errors), indent=2), encoding='utf-8')
    emit('module_loaded', count=len(ids))
    result = unittest.TextTestRunner(verbosity=2, resultclass=Recorder).run(suite)
    payload = dict(module=name, test_ids=ids, tests_run=result.testsRun,
                   successful_before_process_teardown=result.wasSuccessful(),
                   qt_modules_loaded=any(k.startswith(('PySide6', 'PyQt')) for k in sys.modules),
                   loader_errors=loader.errors,
                   failures=[dict(id=t.id(), traceback=s) for t,s in result.failures],
                   errors=[dict(id=t.id(), traceback=s) for t,s in result.errors],
                   skips=[dict(id=t.id(), reason=s) for t,s in result.skipped],
                   expected_failures=[dict(id=t.id(), traceback=s) for t,s in result.expectedFailures],
                   unexpected_successes=[t.id() for t in result.unexpectedSuccesses])
    (folder / 'result.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    emit('summary_before_teardown', **payload)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
