"""Native pytest collection/execution only for modules with top-level test functions."""
import json
from pathlib import Path


def run(name, folder, emit):
    import pytest

    class Recorder:
        def __init__(self):
            self.ids, self.started, self.states = [], [], {}
            self.collection_errors, self.outcomes = [], []

        def case_id(self, nodeid):
            return name + '.' + '.'.join(nodeid.split('::')[1:])

        def pytest_collection_finish(self, session):
            self.ids = [self.case_id(item.nodeid) for item in session.items]
            (folder / 'loaded.json').write_text(json.dumps(dict(test_ids=self.ids,
                loader_errors=self.collection_errors), indent=2), encoding='utf-8')
            emit('module_loaded', count=len(self.ids), framework='pytest')

        def pytest_collectreport(self, report):
            if report.failed:
                self.collection_errors.append(str(report.longrepr))
                emit('collection_error', id=report.nodeid, traceback=str(report.longrepr))

        def pytest_runtest_logstart(self, nodeid, location):
            case = self.case_id(nodeid)
            self.started.append(case)
            self.states[case] = []
            emit('test_start', id=case)

        def pytest_runtest_logreport(self, report):
            case = self.case_id(report.nodeid)
            if report.failed:
                event = 'failure' if report.when == 'call' else 'error'
                self.states[case].append(dict(event=event, id=case, traceback=str(report.longrepr), phase=report.when))
            elif report.skipped:
                event = 'expected_failure' if hasattr(report, 'wasxfail') else 'skip'
                self.states[case].append(dict(event=event, id=case, reason=str(report.longrepr)))
            elif hasattr(report, 'wasxfail'):
                self.states[case].append(dict(event='unexpected_success', id=case))

        def pytest_runtest_logfinish(self, nodeid, location):
            case = self.case_id(nodeid)
            candidates = self.states[case]
            failures = [row for row in candidates if row['event'] in ('failure', 'error', 'unexpected_success')]
            outcome = dict(failures[0] if failures else candidates[0] if candidates else dict(event='success', id=case))
            if failures:
                outcome['phase_reports'] = failures
            self.outcomes.append(outcome)
            emit(outcome['event'], **{key: value for key, value in outcome.items() if key != 'event'})
            emit('test_stop', id=case)

    recorder = Recorder()
    # A fresh module process preserves native parametrization, fixtures, assertion
    # rewriting and teardown; no test function is wrapped in a unittest stub.
    source = Path(*name.split('.')).with_suffix('.py')
    exit_code = int(pytest.main([str(source), '-v', '--tb=long'], plugins=[recorder]))
    failures = [row for row in recorder.outcomes if row.get('event') in ('failure', 'error', 'unexpected_success')]
    payload = dict(module=name, framework='pytest', test_ids=recorder.ids, tests_run=len(recorder.started),
                   successful_before_process_teardown=exit_code == 0 and not failures,
                   pytest_exit_code=exit_code, loader_errors=recorder.collection_errors)
    (folder / 'result.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    emit('summary_before_teardown', **payload)
    return 0 if payload['successful_before_process_teardown'] else (exit_code or 1)
