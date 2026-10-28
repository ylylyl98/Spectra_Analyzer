"""Focused proof of the supervisor's scheduling and real process accounting."""
import json
from collections import Counter
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from tools.testing import run_suite as runner


class TestRunnerTests(unittest.TestCase):
    def test_parallel_batches_drain_before_exclusive_modules(self):
        active = set()
        observed = []
        import threading
        lock = threading.Lock()

        def run(name):
            with lock:
                if name == 'exclusive':
                    self.assertFalse(active)
                self.assertNotIn('exclusive', active)
                active.add(name)
                observed.append(len(active))
            time.sleep(.04)
            with lock:
                active.remove(name)
            return name

        names = ['a', 'b', 'exclusive', 'c']
        self.assertEqual(list(runner.schedule(names, run, jobs=2, parallel={'a', 'b', 'c'})), names)
        self.assertIn(2, observed)
        observed.clear()
        self.assertEqual(list(runner.schedule(names, run, jobs=1, parallel=set(names))), names)
        self.assertEqual(max(observed), 1)

    def test_unreviewed_and_timing_modules_are_exclusive(self):
        self.assertFalse(runner.parallel_safe('tests.test_unknown'))
        self.assertFalse(runner.parallel_safe('tests.test_graph_documents'))
        self.assertTrue(runner.parallel_safe('tests.test_project_workflow'))
        self.assertFalse(runner.parallel_safe('tests.test_timing', {'tests.test_timing'}))

    def test_four_workers_are_bounded_and_drain_before_exclusive(self):
        import threading
        lock = threading.Lock()
        first_wave = threading.Barrier(4, timeout=3)
        active = set()
        peak = 0
        calls = []
        names = ['exclusive-first', *(f'safe-{i}' for i in range(9)), 'exclusive-last']
        reviewed = {name for name in names if name.startswith('safe-')}

        def run(name):
            nonlocal peak
            with lock:
                if name not in reviewed:
                    self.assertFalse(active)
                self.assertTrue(active <= reviewed)
                active.add(name)
                peak = max(peak, len(active))
                calls.append(name)
            if name in {f'safe-{i}' for i in range(4)}:
                first_wave.wait()
            with lock:
                active.remove(name)
            return dict(module=name, passed=name != 'safe-5')

        records = list(runner.schedule(names, run, jobs=4, parallel=reviewed))
        self.assertEqual(peak, 4)
        self.assertEqual(Counter(calls), Counter(names))
        self.assertTrue(all(name in reviewed for name in calls[:len(reviewed)]))
        self.assertEqual([record['module'] for record in records], names)
        self.assertEqual([record['module'] for record in records if not record['passed']], ['safe-5'])

    def test_full_discovery_is_scheduled_once_with_expanded_policy(self):
        names = [entry['module'] for entry in runner.ast_inventory(runner.REPO)]
        calls = []
        def run(name):
            calls.append(name)
            return name
        self.assertEqual(list(runner.schedule(names, run, jobs=4)), names)
        self.assertEqual(Counter(calls), Counter(names))
        reviewed = [name for name in names if runner.parallel_safe(name)]
        self.assertEqual(Counter(calls[:len(reviewed)]), Counter(reviewed))
        self.assertLessEqual(runner.PARALLEL_MODULES, set(names))

    def test_reviewed_policy_covers_isolated_work_and_preserves_resource_exclusions(self):
        for name in ('background_io', 'background_analysis', 'analysis_navigation',
                     'comparison_export_format', 'graph_batch_export', 'processing', 'project_io'):
            with self.subTest(module=name):
                self.assertTrue(runner.parallel_safe('tests.test_' + name))
        # Include indirect clipboard users and methods that override the headless platform.
        for name in ('analysis_drawing_quick', 'workbook_organization', 'annotation_persistence',
                     'custom_colormaps', 'analysis_workbench', 'graph_documents',
                     'quick_interaction_latency', 'quick_refresh_cost', 'view_responsiveness',
                     'formal_native_popups', 'standalone_build', 'windows_launch', 'test_runner'):
            with self.subTest(module=name):
                self.assertFalse(runner.parallel_safe('tests.test_' + name))
        for name in ('latency', 'responsiveness', 'refresh_cost', 'performance', 'native', 'clipboard'):
            module = 'tests.test_' + name
            self.assertFalse(runner.parallel_safe(module, {module}))

    def test_unreviewed_worker_counts_fail_before_launch(self):
        for jobs in (0, 3, 5):
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                list(runner.schedule(['a'], self.fail, jobs=jobs))

    def _run_source(self, source, timeout=3, cancel_event=None):
        if os.name != 'nt':
            self.skipTest('Owned Windows Job process boundary')
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        (root / 'tests').mkdir()
        (root / 'tests/__init__.py').write_text('', encoding='utf-8')
        (root / 'tests/test_fixture.py').write_text(source, encoding='utf-8')
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(runner.HERE), str(root))),
                   PYTHONDONTWRITEBYTECODE='1', SPECTRA_FINAL_INACTIVITY_SECONDS=str(timeout))
        for key in ('SPECTRA_FINAL_REQUIRE_GUARD', 'SPECTRA_QUICK_MIGRATION_CASE'):
            env.pop(key, None)
        return runner.run_module('tests.test_fixture', root / 'evidence', root, env, Path(sys.executable), timeout, cancel_event)

    def test_process_success_skip_and_stderr_are_visible(self):
        record = self._run_source('''import unittest, sys
class Cases(unittest.TestCase):
    def test_ok(self):
        print('visible stderr', file=sys.stderr)
        self.assertTrue(True)
    @unittest.skip('explicit reason')
    def test_skip(self): pass
''')
        self.assertTrue(record['passed'], record)
        self.assertEqual(len(record['loaded_ids']), 2)
        self.assertEqual([e['reason'] for e in record['outcomes'] if e['event'] == 'skip'], ['explicit reason'])
        self.assertIn('visible stderr', Path(record['stderr']).read_text(encoding='utf-8'))
        self.assertTrue(record['whole_process_tree_completed'])

    def test_nonzero_exit_after_green_report_fails(self):
        record = self._run_source('''import atexit, os, unittest
atexit.register(lambda: os._exit(9))
class Cases(unittest.TestCase):
    def test_ok(self): pass
''')
        self.assertFalse(record['passed'])
        self.assertEqual(record['exit_code'], 9)
        self.assertTrue(record['result']['successful_before_process_teardown'])

    def test_missing_report_fails(self):
        record = self._run_source('import os\nos._exit(0)\n')
        self.assertFalse(record['passed'])
        self.assertIsNone(record['result'])

    def test_timeout_terminates_owned_descendant(self):
        record = self._run_source('''import subprocess, sys, time, unittest
class Cases(unittest.TestCase):
    def test_hang(self):
        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        time.sleep(60)
''', timeout=.5)
        self.assertFalse(record['passed'])
        self.assertTrue(record['timed_out'])
        self.assertTrue(record['whole_process_tree_completed'])
        self.assertEqual(record['owned_job_accounting']['ActiveProcesses'], 0)
        self.assertGreaterEqual(record['owned_job_accounting']['TotalProcesses'], 2)

    def test_incomplete_child_transport_fails(self):
        record = self._run_source('''import subprocess, sys, unittest
class Cases(unittest.TestCase):
    def test_child(self):
        subprocess.Popen([sys.executable, '-c', 'pass']).wait()
''')
        self.assertTrue(record['passed'], record)
        self.assertTrue(record['children'][0]['actual_exit_observed'])
        record['children'][0]['actual_exit_observed'] = False
        self.assertFalse(runner.child_transport_complete(record['children']))

    def test_case_mismatch_and_corrupt_reports_never_pass(self):
        record = self._run_source('''import unittest
class Cases(unittest.TestCase):
    def test_ok(self): pass
''')
        folder = Path(record['stderr']).parent
        (folder / 'loaded.json').write_text(json.dumps({'test_ids': ['missing'], 'loader_errors': []}), encoding='utf-8')
        updated = runner.reconcile(record, folder)
        self.assertFalse(updated['passed'])
        self.assertEqual(updated['missing_started_ids'], ['missing'])
        (folder / 'result.json').write_text('{', encoding='utf-8')
        updated = runner.reconcile(record, folder)
        self.assertFalse(updated['passed'])
        self.assertTrue(updated['report_errors'])

    def test_unknown_duplicate_and_missing_terminal_events_fail(self):
        record = self._run_source('''import unittest
class Cases(unittest.TestCase):
    def test_ok(self): pass
''')
        folder = Path(record['stderr']).parent
        events = folder / 'events.jsonl'
        rows = [json.loads(line) for line in events.read_text(encoding='utf-8').splitlines()]
        success = next(row for row in rows if row['event'] == 'success')
        scenarios = [rows + [success],
                     [dict(row, id='unknown') if row['event'] == 'success' else row for row in rows],
                     [row for row in rows if row['event'] != 'success']]
        for scenario in scenarios:
            with self.subTest(events=scenario):
                events.write_text('\n'.join(json.dumps(row) for row in scenario) + '\n', encoding='utf-8')
                updated = runner.reconcile(record, folder)
                self.assertFalse(updated['passed'])
                self.assertTrue(updated['terminal_outcome_mismatches'])

    def test_zero_case_module_fails(self):
        record = self._run_source('import unittest\n')
        self.assertFalse(record['passed'])
        self.assertEqual(record['loaded_ids'], [])

    def test_class_fixture_error_fails_without_case_start(self):
        record = self._run_source('''import unittest
class Cases(unittest.TestCase):
    @classmethod
    def setUpClass(cls): raise RuntimeError('class fixture failed')
    def test_unreachable(self): pass
''')
        self.assertFalse(record['passed'])
        self.assertEqual(record['started_ids'], [])
        self.assertIn('class fixture failed', record['outcomes'][0]['traceback'])

    def test_cancel_signal_terminates_owned_process_promptly(self):
        import threading
        cancelled = threading.Event()
        timer = threading.Timer(.5, cancelled.set)
        timer.start()
        try:
            started = time.monotonic()
            record = self._run_source('''import time, unittest
class Cases(unittest.TestCase):
    def test_hang(self): time.sleep(60)
''', timeout=30, cancel_event=cancelled)
            self.assertLess(time.monotonic() - started, 5)
            self.assertFalse(record['passed'])
            self.assertTrue(record['cancelled'])
            self.assertEqual(record['owned_job_accounting']['ActiveProcesses'], 0)
        finally:
            timer.cancel()

    def test_scheduler_interrupt_signals_running_workers_before_join(self):
        import threading
        cancelled = threading.Event()
        running = threading.Event()

        def run(name):
            if name == 'a':
                self.assertTrue(running.wait(2))
                raise KeyboardInterrupt()
            running.set()
            self.assertTrue(cancelled.wait(2))
            return name

        with self.assertRaises(KeyboardInterrupt):
            list(runner.schedule(['a', 'b'], run, jobs=2, parallel={'a', 'b'}, cancel_event=cancelled))
        self.assertTrue(cancelled.is_set())

    def test_declared_child_variant_rejects_selector_drift(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        (root / 'tests').mkdir()
        source = root / 'tests/test_variant.py'
        source.write_text('''import os, unittest
_CHILD = os.environ.get('CHILD_FLAG') == '1'
class Child(unittest.TestCase):
    def test_inner(self): pass
class Outer(unittest.TestCase):
    def test_outer(self): pass
def load_tests(loader, tests, pattern):
    case = Child if _CHILD else Outer
    return loader.loadTestsFromTestCase(case)
''', encoding='utf-8')
        variant = dict(module='tests.test_variant', flag='CHILD_FLAG', case_class='Child', expected_cases=1)
        self.assertEqual(runner.declared_child_variant(root, variant, runner.ast_inventory(root)), ['tests.test_variant.Child.test_inner'])
        source.write_text(source.read_text().replace('case = Child if _CHILD else Outer', 'case = Outer if _CHILD else Child'), encoding='utf-8')
        with self.assertRaises(ValueError):
            runner.declared_child_variant(root, variant, runner.ast_inventory(root))

    def test_real_process_phase_overlap_and_exclusive_drain(self):
        if os.name != 'nt':
            self.skipTest('Owned Windows Job process boundary')
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        (root / 'tests').mkdir()
        (root / 'tests/__init__.py').write_text('', encoding='utf-8')
        names = ['tests.test_a', 'tests.test_b', 'tests.test_c', 'tests.test_exclusive', 'tests.test_z']
        for name in names:
            (root / (name.replace('.', '/') + '.py')).write_text('''import time, unittest
class Cases(unittest.TestCase):
    def test_wait(self): time.sleep(.35)
''', encoding='utf-8')
        reviewed = set(names) - {'tests.test_exclusive'}
        for jobs in (2, 4):
            with self.subTest(jobs=jobs):
                run, summary = runner.execute(root, root / 'output', synthetic=True,
                                              parallel=reviewed, jobs=jobs)
                self.assertTrue(summary['passed'], summary)
                records = json.loads((run / 'status.json').read_text(encoding='utf-8'))
                self.assertEqual([r['module'] for r in records], names)
                concurrent = [r for r in records if r['module'] in reviewed]
                first_wave = sorted(concurrent, key=lambda r: r['started_time'])[:jobs]
                self.assertLess(max(r['started_time'] for r in first_wave),
                                min(r['stopped_time'] for r in first_wave))
                exclusive = next(r for r in records if r['module'] not in reviewed)
                self.assertGreaterEqual(exclusive['started_time'], max(r['stopped_time'] for r in concurrent))
                self.assertTrue(summary['source_unchanged'])

    def test_real_child_variant_inventory_and_selector_count_failure(self):
        if os.name != 'nt':
            self.skipTest('Owned Windows Job process boundary')
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        (root / 'tests').mkdir()
        (root / 'tests/__init__.py').write_text('', encoding='utf-8')
        (root / 'tests/test_variant.py').write_text('''import os, unittest
_CHILD = os.environ.get('CHILD_FLAG') == '1'
class Child(unittest.TestCase):
    def test_inner(self): pass
class Outer(unittest.TestCase):
    def test_outer(self): pass
def load_tests(loader, tests, pattern):
    case = Child if _CHILD else Outer
    return loader.loadTestsFromTestCase(case)
''', encoding='utf-8')
        variant = dict(module='tests.test_variant', flag='CHILD_FLAG', case_class='Child', expected_cases=1)
        _, summary = runner.execute(root, root / 'output', synthetic=True, child_variants=[variant])
        self.assertTrue(summary['passed'], summary)
        self.assertEqual(summary['loaded_cases'], 2)
        self.assertEqual(summary['passed_child_variants'], 1)
        _, summary = runner.execute(root, root / 'output', synthetic=True,
                                    child_variants=[dict(variant, expected_cases=2)])
        self.assertFalse(summary['passed'])
        self.assertEqual(summary['attempted_child_variants'], 0)
        self.assertTrue(summary['child_variant_errors'])

    def test_guard_redirects_real_qsettings_to_module_folder(self):
        if os.name != 'nt':
            self.skipTest('Owned Windows Job process boundary')
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        (root / 'tests').mkdir()
        (root / 'tests/__init__.py').write_text('', encoding='utf-8')
        (root / 'tests/test_guard.py').write_text('''import os, unittest
from pathlib import Path
from PySide6.QtCore import QSettings
class Cases(unittest.TestCase):
    def test_settings(self):
        settings = QSettings('SpectraAnalyzer', 'SpectraAnalyzer')
        self.assertEqual(Path(settings.fileName()).resolve(), Path(os.environ['SPECTRA_SUITE_SETTINGS']).resolve() / 'SpectraAnalyzer.ini')
        settings.setValue('runner_guard_proof', 'isolated')
        settings.sync()
        self.assertEqual(settings.value('runner_guard_proof'), 'isolated')
''', encoding='utf-8')
        run, summary = runner.execute(root, root / 'output', modules=['tests.test_guard'])
        self.assertTrue(summary['passed'], summary)
        self.assertTrue((run / 'tests.test_guard/settings/SpectraAnalyzer.ini').is_file())

    def test_native_pytest_parametrization_runs_each_collected_case(self):
        record = self._run_source('''import pytest
@pytest.mark.parametrize('value', [1, 2, 3])
def test_parameter(value):
    assert value > 0
''')
        self.assertTrue(record['passed'], record)
        self.assertEqual(record['result']['framework'], 'pytest')
        self.assertEqual(len(record['loaded_ids']), 3)
        self.assertEqual(record['loaded_ids'], record['started_ids'])
        self.assertEqual(len(record['outcomes']), 3)
        self.assertTrue(all(case.startswith('tests.test_fixture.test_parameter[') for case in record['loaded_ids']))

    def test_native_pytest_failure_is_recorded(self):
        record = self._run_source('''import pytest
@pytest.mark.parametrize('value', [1, -1])
def test_parameter(value):
    assert value > 0
''')
        self.assertFalse(record['passed'])
        self.assertEqual(record['exit_code'], 1)
        self.assertEqual(Counter(e['event'] for e in record['outcomes']), {'success': 1, 'failure': 1})

    def test_native_pytest_collection_error_fails(self):
        record = self._run_source('''import nonexistent_runner_fixture_dependency
def test_unreachable(): pass
''')
        self.assertFalse(record['passed'])
        self.assertTrue(record['result']['loader_errors'])
        self.assertEqual(record['loaded_ids'], [])

    def test_native_pytest_teardown_error_is_terminal(self):
        record = self._run_source('''import pytest
@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError('teardown failed')
def test_cleanup(broken_teardown): pass
''')
        self.assertFalse(record['passed'])
        self.assertEqual([e['event'] for e in record['outcomes']], ['error'])
        self.assertIn('teardown failed', record['outcomes'][0]['traceback'])


if __name__ == '__main__':
    unittest.main()
