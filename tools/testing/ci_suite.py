"""Run independent Windows shards and fail closed on their complete evidence union."""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.testing import run_suite
from tools.testing.inventory import ast_inventory, declared_child_variant, source_inventory

REPO = Path(__file__).resolve().parents[2]
WEIGHTS = 'tools/testing/ci_weights.json'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def canonical_manifest(entries):
    return [{key: value for key, value in entry.items() if key != 'path'} for entry in entries]


def inputs(repo):
    rows = source_inventory(repo)
    for relative in (WEIGHTS, 'requirements.txt', 'tools/windows/requirements-standalone.txt',
                     '.github/workflows/tests.yml'):
        path = repo / relative
        if path.is_file():
            rows.append(dict(path=relative, size_bytes=path.stat().st_size,
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return sorted(rows, key=lambda row: row['path'])


def balanced_shards(modules, count, weights):
    """Deterministic LPT bins; unknown modules use the measured median, never disappear."""
    if not isinstance(count, int) or count < 1 or len(modules) < count:
        raise ValueError('Shard count must be between one and the module count')
    if len(set(modules)) != len(modules):
        raise ValueError('Duplicate modules in inventory')
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
           for value in weights.values()):
        raise ValueError('Timing weights must be positive finite seconds')
    fallback = statistics.median(weights.values()) if weights else 30.0
    costs = {module: weights.get(module, fallback) for module in modules}
    bins = [dict(index=index, modules=[], estimated_seconds=0.0) for index in range(count)]
    for module in sorted(modules, key=lambda name: (-costs[name], name)):
        shard = min(bins, key=lambda row: (row['estimated_seconds'], row['index']))
        shard['modules'].append(module)
        shard['estimated_seconds'] += costs[module]
    for shard in bins:
        shard['modules'].sort()
        shard['estimated_seconds'] = round(shard['estimated_seconds'], 3)
    return bins


def make_plan(repo, commit, count=8, variants=None):
    repo = Path(repo)
    manifest = ast_inventory(repo)
    if not manifest or any(entry['parse_error'] for entry in manifest):
        raise ValueError('Test inventory is empty or contains parse errors')
    variants = run_suite.CHILD_VARIANTS if variants is None else variants
    declarations = []
    for variant in variants:
        declarations.append(dict(**variant, expected_ids=declared_child_variant(repo, variant, manifest)))
    weights_path = repo / WEIGHTS
    weights = read(weights_path)['module_seconds'] if weights_path.exists() else {}
    return dict(schema=1, commit=commit, source=inputs(repo),
                manifest=canonical_manifest(manifest), variants=declarations,
                shards=balanced_shards([entry['module'] for entry in manifest], count, weights))


def run_shard(repo, output, commit, index, count=8, timeout=180):
    plan = make_plan(repo, commit, count)
    if index < 0 or index >= count:
        raise ValueError('Shard index is out of range')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = dict(schema=1, shard=index, commit=commit, plan=plan, passed=False)
    run_suite.write(output / 'shard.json', report)
    try:
        run, summary = run_suite.execute(repo, output, modules=plan['shards'][index]['modules'],
                                         jobs=1, timeout=timeout)
        report.update(run=run.name, passed=summary['passed'],
                      modules=summary['modules'], cases=summary['loaded_cases'])
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        report['duration_seconds'] = round(time.monotonic() - started, 3)
        run_suite.write(output / 'shard.json', report)
    return report


def validate_record(record, folder):
    """Reconcile raw reports, including actual descendant exits, using the supervisor."""
    stored = read(folder / 'process.json')
    if stored != record:
        raise ValueError('Status/process evidence differs: ' + str(folder))
    checked = run_suite.reconcile(dict(record), folder)
    if not checked['passed'] or not record['passed']:
        raise ValueError('Failed or incomplete process evidence: ' + str(folder))
    for key in ('loaded_ids', 'started_ids', 'stopped_ids', 'outcomes', 'subtests'):
        if checked[key] != record[key]:
            raise ValueError('Raw case evidence differs: ' + str(folder))
    return checked


def aggregate(repo, evidence, commit, count=8, variants=None):
    """Every module and child variant must have one passing, matching-source owner."""
    plan = make_plan(repo, commit, count, variants)
    evidence = Path(evidence)
    files = sorted(evidence.rglob('shard.json'))
    if len(files) != count:
        raise ValueError(f'Expected {count} shard reports; found {len(files)}')
    seen_shards, seen_modules, seen_variants = [], [], []
    loaded, starts, outcomes, durations, skips = [], [], [], [], []
    for file in files:
        report = read(file)
        index = report['shard']
        if not isinstance(index, int) or index not in range(count):
            raise ValueError('Invalid shard index')
        seen_shards.append(index)
        if report['plan'] != plan or report['commit'] != commit or report['passed'] is not True:
            raise ValueError('Shard failed or commit/source/plan differs: ' + str(index))
        run_name = report['run']
        if Path(run_name).name != run_name or run_name in ('.', '..'):
            raise ValueError('Invalid evidence run path')
        run = file.parent / run_name
        selected = plan['shards'][index]['modules']
        expected_manifest = [entry for entry in plan['manifest'] if entry['module'] in selected]
        if canonical_manifest(read(run / 'manifest.json')) != expected_manifest:
            raise ValueError('Shard manifest differs')
        if canonical_manifest(read(run / 'discovered-manifest.json')) != plan['manifest']:
            raise ValueError('Discovered manifest differs')
        expected_source = source_inventory(Path(repo))
        if read(run / 'source-start.json') != expected_source or read(run / 'source-end.json') != expected_source:
            raise ValueError('Runner source hashes differ')
        if read(run / 'source-changes.json'):
            raise ValueError('Runner source changed during execution')
        summary = read(run / 'summary.json')
        records = read(run / 'status.json')
        children = read(run / 'child-status.json')
        if summary['passed'] is not True or summary['complete_suite'] is not False or summary['jobs'] != 1:
            raise ValueError('Incomplete or nonserial shard summary')
        names = [record['module'] for record in records]
        if Counter(names) != Counter(selected):
            raise ValueError('Missing or duplicate shard modules')
        seen_modules.extend(names)
        expected_variants = [variant for variant in plan['variants'] if variant['module'] in selected]
        child_names = [(row['module'], row['child_flag']) for row in children]
        if Counter(child_names) != Counter((row['module'], row['flag']) for row in expected_variants):
            raise ValueError('Missing or duplicate child variants')
        seen_variants.extend(child_names)
        checked_records = []
        for record in records + children:
            is_child = 'child_flag' in record
            folder = run / (record['module'] + ('--child-mode' if is_child else ''))
            checked = validate_record(record, folder)
            if is_child:
                expected = next(row for row in expected_variants
                                if (row['module'], row['flag']) == (record['module'], record['child_flag']))
                if Counter(checked['loaded_ids']) != Counter(expected['expected_ids']):
                    raise ValueError('Child variant cases differ')
            checked_records.append(checked)
        shard_loaded = [case for record in checked_records for case in record['loaded_ids']]
        shard_starts = [case for record in checked_records for case in record['started_ids']]
        shard_outcomes = [event for record in checked_records for event in record['outcomes']]
        declared = {case for entry in expected_manifest
                    for case in entry['top_class_ids'] + entry['top_function_ids']}
        if declared - {case.split('[', 1)[0] for case in shard_loaded}:
            raise ValueError('Declared case was not loaded')
        if (summary['modules'] != len(records) or summary['attempted_modules'] != len(records)
                or summary['passed_modules'] != len(records)
                or summary['declared_child_variants'] != len(children)
                or summary['attempted_child_variants'] != len(children)
                or summary['passed_child_variants'] != len(children)
                or summary['loaded_cases'] != len(shard_loaded) or summary['executed_cases'] != len(shard_starts)
                or summary['outcomes'] != dict(Counter(event['event'] for event in shard_outcomes))):
            raise ValueError('Shard summary totals differ from raw reports')
        loaded.extend(shard_loaded)
        starts.extend(shard_starts)
        outcomes.extend(shard_outcomes)
        skips.extend(event for event in shard_outcomes if event['event'] == 'skip')
        durations.append(dict(shard=index, seconds=report['duration_seconds'], modules=len(records),
                              variants=len(children), cases=len(shard_loaded)))
    if Counter(seen_shards) != Counter(range(count)):
        raise ValueError('Missing or duplicate shard indexes')
    if Counter(seen_modules) != Counter(entry['module'] for entry in plan['manifest']):
        raise ValueError('Missing or duplicate modules in full suite')
    if Counter(seen_variants) != Counter((row['module'], row['flag']) for row in plan['variants']):
        raise ValueError('Missing or duplicate variants in full suite')
    if Counter(loaded) != Counter(starts) or any(value != 1 for value in Counter(loaded).values()):
        raise ValueError('Missing or duplicate full-suite cases')
    return dict(passed=True, complete_suite=True, commit=commit, modules=len(seen_modules),
                child_variants=len(seen_variants), loaded_cases=len(loaded), executed_cases=len(starts),
                outcomes=dict(Counter(event['event'] for event in outcomes)), skipped=skips,
                shards=sorted(durations, key=lambda row: row['shard']),
                slowest_shard_seconds=max(row['seconds'] for row in durations))


def markdown(summary):
    lines = ['## Windows full suite', '', f"Commit: `{summary['commit']}`", '',
             f"{summary['modules']} modules + {summary['child_variants']} child variants; "
             f"{summary['executed_cases']} cases; outcomes: {summary['outcomes']}.", '',
             '| Shard | Seconds | Modules | Variants | Cases |', '| --- | ---: | ---: | ---: | ---: |']
    lines.extend(f"| {row['shard']} | {row['seconds']:.3f} | {row['modules']} | {row['variants']} | {row['cases']} |"
                 for row in summary['shards'])
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('shard', 'gate'))
    parser.add_argument('--commit', required=True)
    parser.add_argument('--count', type=int, default=8)
    parser.add_argument('--index', type=int)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'shard':
            if args.index is None:
                parser.error('shard requires --index')
            summary = run_shard(REPO, args.output, args.commit, args.index, args.count)
        else:
            if args.evidence is None:
                parser.error('gate requires --evidence')
            summary = aggregate(REPO, args.evidence, args.commit, args.count)
            args.output.mkdir(parents=True, exist_ok=True)
            run_suite.write(args.output / 'full-suite.json', summary)
            (args.output / 'summary.md').write_text(markdown(summary), encoding='utf-8')
            if os.environ.get('GITHUB_STEP_SUMMARY'):
                with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
                    stream.write(markdown(summary))
        print(json.dumps(summary), flush=True)
        return 0 if summary['passed'] else 1
    except Exception as error:
        args.output.mkdir(parents=True, exist_ok=True)
        run_suite.write(args.output / 'ci-error.json', dict(passed=False, error=repr(error)))
        print(repr(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
