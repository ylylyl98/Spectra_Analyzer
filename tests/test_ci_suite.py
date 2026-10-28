"""Pure fixture evidence proves CI partitioning and the fail-closed full-suite gate."""
from collections import Counter
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools.testing import ci_suite as ci


class CiSuiteTests(unittest.TestCase):
    def test_balancing_is_deterministic_complete_and_uses_unknown_fallback(self):
        modules = ['d', 'b', 'a', 'new', 'c']
        weights = dict(a=100, b=70, c=30, d=10)
        bins = ci.balanced_shards(modules, 2, weights)
        self.assertEqual(bins, ci.balanced_shards(list(reversed(modules)), 2, weights))
        self.assertEqual(Counter(name for row in bins for name in row['modules']), Counter(modules))
        self.assertEqual(sum(row['estimated_seconds'] for row in bins), 260)
        self.assertLessEqual(max(row['estimated_seconds'] for row in bins), 140)

    def test_invalid_weights_and_duplicate_inventory_fail(self):
        for weights in ({'a': -1}, {'a': float('nan')}, {'a': float('inf')}, {'a': 'fast'}):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                ci.balanced_shards(['a'], 1, weights)
        with self.assertRaises(ValueError):
            ci.balanced_shards(['a', 'a'], 1, {})
        with self.assertRaises(ValueError):
            ci.balanced_shards(['a'], 2, {})

    def make_fixture(self, root):
        repo = root / 'repo'
        (repo / 'tests').mkdir(parents=True)
        (repo / 'tests/test_a.py').write_text(
            "import os\n_CHILD = os.environ.get('TEST_CHILD') == '1'\n"
            'class Parent:\n    def test_parent(self): pass\n'
            'class Child:\n    def test_child(self): pass\n'
            'def load_tests(loader, tests, pattern):\n'
            '    case = Child if _CHILD else Parent\n'
            '    return loader.loadTestsFromTestCase(case)\n', encoding='utf-8')
        (repo / 'tests/test_b.py').write_text('class Other:\n    def test_ok(self): pass\n', encoding='utf-8')
        variants = [dict(module='tests.test_a', flag='TEST_CHILD', case_class='Child', expected_cases=1)]
        plan = ci.make_plan(repo, 'commit-a', count=2, variants=variants)
        evidence = root / 'evidence'
        for shard in plan['shards']:
            folder = evidence / str(shard['index'])
            run = folder / 'run-proof'
            run.mkdir(parents=True)
            selected = shard['modules']
            manifest = [row for row in ci.ast_inventory(repo) if row['module'] in selected]
            self.save(run / 'manifest.json', manifest)
            self.save(run / 'discovered-manifest.json', ci.ast_inventory(repo))
            for name in ('source-start.json', 'source-end.json'):
                self.save(run / name, ci.source_inventory(repo))
            self.save(run / 'source-changes.json', [])
            records, children = [], []
            for name in selected:
                case = name + ('.Parent.test_parent' if name.endswith('test_a') else '.Other.test_ok')
                records.append(self.process(run, name, case))
                if name.endswith('test_a'):
                    children.append(self.process(run, name, name + '.Child.test_child', child=True))
            self.save(run / 'status.json', records)
            self.save(run / 'child-status.json', children)
            total = len(records + children)
            summary = dict(passed=True, complete_suite=False, jobs=1, modules=len(records),
                           attempted_modules=len(records), passed_modules=len(records),
                           declared_child_variants=len(children), attempted_child_variants=len(children),
                           passed_child_variants=len(children), loaded_cases=total, executed_cases=total,
                           outcomes=dict(success=total))
            self.save(run / 'summary.json', summary)
            self.save(folder / 'shard.json', dict(schema=1, shard=shard['index'], commit='commit-a',
                      plan=plan, passed=True, run=run.name, duration_seconds=1.5))
        return repo, evidence, variants

    @staticmethod
    def save(path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def process(self, run, name, case, child=False):
        folder = run / (name + ('--child-mode' if child else ''))
        folder.mkdir()
        rows = [dict(event='test_start', id=case), dict(event='success', id=case),
                dict(event='test_stop', id=case), dict(event='atexit_end')]
        (folder / 'events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
        self.save(folder / 'loaded.json', dict(test_ids=[case], loader_errors=[]))
        self.save(folder / 'result.json', dict(successful_before_process_teardown=True,
                  test_ids=[case], tests_run=1, loader_errors=[]))
        record = dict(module=name, exit_code=0, timed_out=False, whole_process_tree_completed=True)
        if child:
            record.update(variant='child_mode', child_flag='TEST_CHILD', expected_child_ids=[case],
                          missing_child_ids=[], unexpected_child_ids=[])
        return ci.run_suite.reconcile(record, folder)

    def verify(self, repo, evidence, variants):
        return ci.aggregate(repo, evidence, 'commit-a', count=2, variants=variants)

    def test_complete_raw_union_passes_and_reports_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, evidence, variants = self.make_fixture(Path(temp))
            summary = self.verify(repo, evidence, variants)
            self.assertTrue(summary['complete_suite'])
            self.assertEqual((summary['modules'], summary['child_variants'], summary['executed_cases']), (2, 1, 3))
            self.assertEqual(summary['outcomes'], dict(success=3))
            self.assertIn('| Shard |', ci.markdown(summary))

    def test_missing_and_duplicate_shards_fail(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as temp:
                repo, evidence, variants = self.make_fixture(Path(temp))
                files = sorted(evidence.rglob('shard.json'))
                if duplicate:
                    row = ci.read(files[1])
                    row['shard'] = 0
                    self.save(files[1], row)
                else:
                    files[1].unlink()
                with self.assertRaises(ValueError):
                    self.verify(repo, evidence, variants)

    def test_missing_duplicate_module_and_child_variant_fail(self):
        for target, duplicate in (('status.json', False), ('status.json', True),
                                  ('child-status.json', False), ('child-status.json', True)):
            with self.subTest(target=target, duplicate=duplicate), tempfile.TemporaryDirectory() as temp:
                repo, evidence, variants = self.make_fixture(Path(temp))
                path = next(path for path in evidence.rglob(target) if ci.read(path))
                rows = ci.read(path)
                self.save(path, rows + [copy.deepcopy(rows[0])] if duplicate else [])
                with self.assertRaises(ValueError):
                    self.verify(repo, evidence, variants)

    def test_corrupt_raw_report_and_missing_terminal_event_fail(self):
        for damage in ('result', 'event', 'skip'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temp:
                repo, evidence, variants = self.make_fixture(Path(temp))
                folder = next(evidence.rglob('process.json')).parent
                if damage == 'result':
                    (folder / 'result.json').write_text('{broken', encoding='utf-8')
                elif damage == 'event':
                    path = folder / 'events.jsonl'
                    rows = [json.loads(line) for line in path.read_text().splitlines()]
                    path.write_text('\n'.join(json.dumps(row) for row in rows if row['event'] != 'test_stop'), encoding='utf-8')
                else:
                    # Raw changes cannot be hidden behind previously green status/summary files.
                    path = folder / 'events.jsonl'
                    path.write_text(path.read_text().replace('"success"', '"skip"'), encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.verify(repo, evidence, variants)

    def test_duplicate_loaded_cases_and_unobserved_child_exit_fail(self):
        for damage in ('duplicate-case', 'child-exit'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temp:
                repo, evidence, variants = self.make_fixture(Path(temp))
                folder = next(evidence.rglob('process.json')).parent
                if damage == 'duplicate-case':
                    path = folder / 'loaded.json'
                    row = ci.read(path)
                    row['test_ids'] *= 2
                    self.save(path, row)
                else:
                    child = folder / 'children/1'
                    child.mkdir(parents=True)
                    self.save(child / 'process.json', dict(actual_exit_observed=False, exit_code=None))
                with self.assertRaises(ValueError):
                    self.verify(repo, evidence, variants)

    def test_existing_skip_outcomes_are_visible_with_complete_variant_ownership(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, evidence, variants = self.make_fixture(Path(temp))
            status = next(path for path in evidence.rglob('status.json')
                          if ci.read(path)[0]['module'] == 'tests.test_a')
            records = ci.read(status)
            folder = status.parent / records[0]['module']
            events = folder / 'events.jsonl'
            rows = [json.loads(line) for line in events.read_text().splitlines()]
            for row in rows:
                if row['event'] == 'success':
                    row.update(event='skip', reason='QGui child executes the native cases')
            events.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
            records[0] = ci.run_suite.reconcile(records[0], folder)
            self.save(status, records)
            summary_path = status.parent / 'summary.json'
            summary = ci.read(summary_path)
            summary['outcomes'] = dict(success=1, skip=1)
            self.save(summary_path, summary)
            result = self.verify(repo, evidence, variants)
            self.assertEqual(result['outcomes'], dict(success=2, skip=1))
            self.assertEqual(result['skipped'][0]['reason'], 'QGui child executes the native cases')

    def test_failure_exit_source_commit_and_summary_forgery_fail(self):
        for damage in ('failure', 'exit', 'source', 'commit', 'summary', 'failed-shard'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temp:
                repo, evidence, variants = self.make_fixture(Path(temp))
                if damage == 'source':
                    (repo / 'tests/test_b.py').write_text('class Other:\n    def test_changed(self): pass\n', encoding='utf-8')
                elif damage in ('commit', 'failed-shard'):
                    path = next(evidence.rglob('shard.json'))
                    row = ci.read(path)
                    row['commit' if damage == 'commit' else 'passed'] = 'wrong' if damage == 'commit' else False
                    self.save(path, row)
                elif damage == 'summary':
                    path = next(evidence.rglob('summary.json'))
                    row = ci.read(path)
                    row['loaded_cases'] += 1
                    self.save(path, row)
                else:
                    path = next(evidence.rglob('status.json'))
                    rows = ci.read(path)
                    rows[0]['exit_code'] = 1 if damage == 'exit' else 0
                    if damage == 'failure':
                        rows[0]['passed'] = False
                    self.save(path, rows)
                    folder = path.parent / rows[0]['module']
                    self.save(folder / 'process.json', rows[0])
                with self.assertRaises(ValueError):
                    self.verify(repo, evidence, variants)

    def test_parse_error_fails_before_any_shard_can_run(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / 'tests').mkdir()
            (repo / 'tests/test_bad.py').write_text('def broken(', encoding='utf-8')
            with self.assertRaises(ValueError):
                ci.make_plan(repo, 'commit-a', count=1, variants=[])

    def test_real_supervised_shards_aggregate_after_evidence_and_checkout_move(self):
        if os.name != 'nt':
            self.skipTest('Owned Windows Job process boundary')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / 'original-checkout'
            (repo / 'tests').mkdir(parents=True)
            (repo / 'tests/__init__.py').write_text('', encoding='utf-8')
            for name in ('a', 'b'):
                (repo / f'tests/test_{name}.py').write_text(
                    'import unittest\nclass Cases(unittest.TestCase):\n'
                    '    def test_pass(self): self.assertEqual(2 + 2, 4)\n', encoding='utf-8')
            execute = ci.run_suite.execute

            def synthetic_execute(*args, **kwargs):
                return execute(*args, **kwargs, synthetic=True)

            evidence = root / 'original-artifacts'
            with patch.object(ci.run_suite, 'CHILD_VARIANTS', ()), patch.object(
                    ci.run_suite, 'execute', side_effect=synthetic_execute):
                for index in range(2):
                    report = ci.run_shard(repo, evidence / str(index), 'real-commit', index, count=2)
                    self.assertTrue(report['passed'], report)
            downloaded = root / 'downloaded-artifacts'
            checkout = root / 'gate-checkout'
            shutil.copytree(evidence, downloaded)
            shutil.copytree(repo, checkout)
            # The gate must use only downloaded files and relative source identities.
            shutil.rmtree(evidence)
            shutil.rmtree(repo)
            summary = ci.aggregate(checkout, downloaded, 'real-commit', count=2, variants=[])
            self.assertTrue(summary['passed'])
            self.assertEqual(summary['executed_cases'], 2)
            self.assertEqual(summary['outcomes'], dict(success=2))


if __name__ == '__main__':
    unittest.main()
