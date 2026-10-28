"""Fresh module processes with bounded, reviewed concurrency and exit evidence."""
import argparse
import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.testing.inventory import ast_inventory, declared_child_variant, source_inventory
from tools.testing.windows_job import Job

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PARALLEL_MODULES = frozenset((
    # Original reviewed Quick workflows (fresh child per case).
    'tests.test_project_workflow',
    'tests.test_project_daily_workflow', 'tests.test_multi_dataset_workflow',
    'tests.test_formal_plot_workflow', 'tests.test_xy_quick_inspector',
    'tests.test_view_color_limits', 'tests.test_formal_properties_quick',
    'tests.test_plot_state_recall', 'tests.test_daily_comparison_workflow',
    'tests.test_control_improvements', 'tests.test_xy_selection_highlight',
    'tests.test_xy_analysis_workflow', 'tests.test_project_async',
    'tests.test_workspace_shell', 'tests.test_automatic_import',
    'tests.test_curve_symbols', 'tests.test_fixed_processing_regions',
    'tests.test_heatmap_processing', 'tests.test_title_templates',
    # Headless Quick workflows: per-case application/settings/temp directory;
    # helper input targets explicit local windows, never the desktop clipboard.
    'tests.test_background_io', 'tests.test_background_analysis',
    'tests.test_analysis_navigation', 'tests.test_comparison_export_format',
    'tests.test_image_export', 'tests.test_graph_batch_export',
    'tests.test_workflow', 'tests.test_shared_dat_properties_bridge',
    'tests.test_analysis_plot_style', 'tests.test_unified_import',
    'tests.test_signal_titles', 'tests.test_fit_width_labels',
    'tests.test_processing_result_groups', 'tests.test_dialog_layout_compact',
    'tests.test_quick_plots', 'tests.test_heatmap_page_growth',
    'tests.test_graph_publication', 'tests.test_project_tree_hierarchy',
    'tests.test_project_tree_wrap', 'tests.test_project_navigation',
    'tests.test_ui_layout',
    # In-memory bridge/science contracts and temporary-file persistence/export.
    # QSettings users use isolate_settings in addition to the suite guard.
    'tests.test_analysis_navigation_bridge', 'tests.test_ordinary_analysis_bridge',
    'tests.test_xy_analysis_bridge', 'tests.test_analysis_header_bridge',
    'tests.test_common_prompt_bridge', 'tests.test_curve_prompt_bridge',
    'tests.test_peak_menu_bridge', 'tests.test_figure_options_bridge',
    'tests.test_subplot_editor_bridge', 'tests.test_analysis_canvas_bridge',
    'tests.test_analysis_workbench_bridge', 'tests.test_analysis_document_state',
    'tests.test_dataset_state', 'tests.test_document_registry',
    'tests.test_named_graphs_state', 'tests.test_project_model',
    'tests.test_project_io', 'tests.test_workbook_project',
    'tests.test_sheet_metadata', 'tests.test_workbook_state',
    'tests.test_fixed_recipe_reload', 'tests.test_curve_palettes',
    'tests.test_plot_templates', 'tests.test_selective_plot_formats',
    'tests.test_plot_batch', 'tests.test_plot_annotation_geometry',
    'tests.test_hybrid_svg', 'tests.test_analysis_overlay_export',
    'tests.test_fit_scale_reliability', 'tests.test_peak_candidates',
    'tests.test_voigt_fitting', 'tests.test_xy_analysis',
    'tests.test_xy_io', 'tests.test_dat_preamble', 'tests.test_processing',
))
EXCLUSIVE_WORDS = ('performance', 'benchmark', 'native', 'clipboard', 'timing',
                   'latency', 'responsiveness', 'refresh_cost')
WORKER_COUNTS = (1, 2, 4)
CHILD_VARIANTS = (
    dict(module='tests.test_formal_preview_controller', flag='SPECTRA_FORMAL_PREVIEW_QGUI_CHILD',
         case_class='FormalPreviewControllerTests', expected_cases=6),
    dict(module='tests.test_formal_plot_controller', flag='SPECTRA_FORMAL_PLOT_QGUI_CHILD',
         case_class='FormalPlotControllerTests', expected_cases=6),
    dict(module='tests.test_formal_scene_lifetime', flag='SPECTRA_FORMAL_LIFETIME_QGUI_CHILD',
         case_class='FormalSceneLifetimeTests', expected_cases=4),
)
OUTCOMES = ('success', 'failure', 'error', 'skip', 'expected_failure', 'unexpected_success')


def write(path, value):
    """Atomic snapshots remain readable while a run is in progress."""
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    temporary.replace(path)


def parallel_safe(name, reviewed=PARALLEL_MODULES):
    return name in reviewed and not any(word in name.lower() for word in EXCLUSIVE_WORDS)


def schedule(names, run, jobs=2, parallel=PARALLEL_MODULES, cancel_event=None):
    """Run the reviewed phase, drain it, then run exclusive modules serially.

    Only this coordinator writes suite snapshots. Threads only supervise disjoint
    subprocesses; test imports and subprocess instrumentation live in workers.
    """
    if jobs not in WORKER_COUNTS:
        raise ValueError('Only --jobs 1, 2 or 4 is currently reviewed')
    cancel_event = cancel_event or threading.Event()
    if jobs == 1:
        for name in names:
            yield run(name)
        return
    batch = [name for name in names if parallel_safe(name, parallel)]
    completed = {}
    if batch:
        with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix='module-supervisor') as pool:
            futures = {}
            try:
                for name in batch:
                    futures[pool.submit(run, name)] = name
                for future in as_completed(futures):
                    completed[futures[future]] = future.result()
            except BaseException:
                cancel_event.set()
                for future in futures:
                    future.cancel()
                raise
    # Preserve manifest order in snapshots despite the explicitly recorded phase order.
    for name in names:
        yield completed[name] if name in completed else run(name)


def event_rows(path, strict=False):
    rows = []
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or 'event' not in row:
                    raise ValueError('Invalid event row')
                rows.append(row)
            except (ValueError, json.JSONDecodeError):
                if strict:
                    raise
                # An in-flight final line is retried next poll, never accepted at exit.
    return rows


def child_transport_complete(children):
    return all(not child.get('launch_error') and not child.get('communicate_error')
               and child.get('actual_exit_observed') and child.get('exit_code') is not None
               and (not child.get('pipes_requested') or child.get('communicate_completed'))
               and (not child.get('case_id') or child.get('result_present'))
               for child in children)


def reconcile(record, folder):
    errors = []

    def read_report(path, default):
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(result, dict):
                raise ValueError('Report must be an object')
            return result
        except (OSError, ValueError) as error:
            errors.append(dict(path=str(path), error=repr(error)))
            return default

    result = read_report(folder / 'result.json', None)
    loaded = read_report(folder / 'loaded.json', {})
    try:
        rows = event_rows(folder / 'events.jsonl', strict=True)
    except (OSError, ValueError) as error:
        errors.append(dict(path=str(folder / 'events.jsonl'), error=repr(error)))
        rows = []
    ids = loaded.get('test_ids', [])
    if not isinstance(ids, list) or not all(isinstance(case, str) for case in ids):
        errors.append(dict(path=str(folder / 'loaded.json'), error='test_ids must be a list of strings'))
        ids = []
    starts = [e.get('id') for e in rows if e['event'] == 'test_start']
    stops = [e.get('id') for e in rows if e['event'] == 'test_stop']
    outcomes = [e for e in rows if e['event'] in OUTCOMES]
    subtests = [e for e in rows if e['event'] == 'subtest']
    accounted = {e.get('id') for e in outcomes} | {e.get('parent_id') for e in subtests if e.get('outcome') != 'success'}
    terminal_counts = Counter(e.get('id') for e in outcomes)
    terminal_mismatches = {case: dict(started=Counter(starts)[case], outcomes=terminal_counts[case])
                           for case in set(starts) | terminal_counts.keys()
                           if Counter(starts)[case] != terminal_counts[case]}
    children = []
    for path in sorted((folder / 'children').glob('*/process.json')):
        child = read_report(path, {})
        child['evidence'] = str(path.parent)
        child['result_present'] = (path.parent / 'result.json').is_file()
        children.append(child)
    record.update(result=result, loaded_ids=ids, started_ids=starts, stopped_ids=stops,
                  outcomes=outcomes, subtests=subtests, children=children, report_errors=errors,
                  terminal_outcome_mismatches=terminal_mismatches,
                  missing_outcome_ids=sorted(set(starts) - accounted),
                  missing_started_ids=list((Counter(ids) - Counter(starts)).elements()),
                  unexpected_started_ids=list((Counter(starts) - Counter(ids)).elements()),
                  missing_stop_ids=list((Counter(starts) - Counter(stops)).elements()),
                  unexpected_stop_ids=list((Counter(stops) - Counter(starts)).elements()),
                  loaded_duplicate_ids={k: v for k, v in Counter(ids).items() if v > 1},
                  started_duplicate_ids={k: v for k, v in Counter(starts).items() if v > 1},
                  atexit_end=any(e['event'] == 'atexit_end' for e in rows),
                  child_transport_completed=child_transport_complete(children))
    record['passed'] = bool(result and result.get('successful_before_process_teardown')
        and ids and record.get('exit_code') == 0 and not record.get('timed_out') and not record.get('cancelled')
        and record.get('whole_process_tree_completed') and record['child_transport_completed']
        and record['atexit_end'] and not errors and not record.get('supervisor_error')
        and not loaded.get('loader_errors') and not result.get('loader_errors')
        and result.get('test_ids') == ids and result.get('tests_run') == len(starts)
        and not any(record[key] for key in ('missing_started_ids', 'unexpected_started_ids',
            'missing_stop_ids', 'unexpected_stop_ids', 'missing_outcome_ids',
            'loaded_duplicate_ids', 'started_duplicate_ids', 'terminal_outcome_mismatches'))
        and not any(e['event'] in ('failure', 'error', 'unexpected_success') for e in outcomes)
        and not any(e.get('outcome') != 'success' for e in subtests))
    write(folder / 'process.json', record)
    return record


def run_module(name, folder, repo, env, python, timeout, cancel_event=None):
    folder.mkdir(parents=True, exist_ok=False)
    gate = folder / 'start-released'
    env = dict(env, SPECTRA_ACCEPTANCE_START_GATE=str(gate),
               SPECTRA_SUITE_SETTINGS=str(folder / 'settings'), SPECTRA_SUITE_ARTIFACTS=str(folder))
    command = [str(python), '-B', '-X', 'utf8', str(HERE / 'module_runner.py'), name, str(folder)]
    source = repo.joinpath(*name.split('.')).with_suffix('.py')
    tree = ast.parse(source.read_text(encoding='utf-8-sig'))
    function_style = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name.startswith('test_') for node in tree.body)
    if function_style:
        command.append('--pytest')
    started = time.monotonic()
    record = dict(module=name, command=command, cwd=str(repo), started_time=time.time(),
                  started_utc=datetime.now(timezone.utc).isoformat(), timeout_seconds=timeout,
                  timed_out=False, exit_code=None, whole_process_tree_completed=False,
                  stdout=str(folder / 'stdout.log'), stderr=str(folder / 'stderr.log'),
                  env=controlled_environment(env))
    process, job, assigned = None, None, False
    try:
        if cancel_event is not None and cancel_event.is_set():
            record['cancelled'] = True
            raise InterruptedError('Suite cancelled before process launch')
        job = Job()
        with (folder / 'stdout.log').open('wb') as stdout, (folder / 'stderr.log').open('wb') as stderr:
            process = subprocess.Popen(command, cwd=repo, env=env, stdout=stdout, stderr=stderr,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            record['pid'] = process.pid
            job.assign(process)
            assigned = True
            gate.write_text('Owned Job assigned before test and Qt imports.\n', encoding='utf-8')
            write(folder / 'process.json', record)
            last_progress, seen = time.monotonic(), 0
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    record['cancelled'] = True
                    job.terminate()
                    break
                rows = event_rows(folder / 'events.jsonl')
                if len(rows) != seen:
                    seen, last_progress = len(rows), time.monotonic()
                if time.monotonic() - last_progress > timeout:
                    record.update(timed_out=True, inactivity_seconds=time.monotonic() - last_progress,
                                  last_event=rows[-1] if rows else None)
                    write(folder / 'timeout.json', record)
                    job.terminate()
                    break
                time.sleep(.05)
            record['exit_code'] = process.wait(timeout=30)
            # A module's green report does not complete its descendants' teardown.
            deadline = time.monotonic() + 30
            while job.accounting()['ActiveProcesses'] and time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    record['cancelled'] = True
                    job.terminate()
                    break
                time.sleep(.05)
            record['owned_job_accounting'] = job.accounting()
            record['whole_process_tree_completed'] = record['owned_job_accounting']['ActiveProcesses'] == 0
            if not record['whole_process_tree_completed']:
                record['supervisor_error'] = 'Owned descendants remained after module exit'
    except Exception as error:
        record['supervisor_error'] = repr(error)
    finally:
        try:
            if job is not None and assigned:
                if job.accounting()['ActiveProcesses']:
                    job.terminate()
                    deadline = time.monotonic() + 30
                    while job.accounting()['ActiveProcesses'] and time.monotonic() < deadline:
                        time.sleep(.05)
                    write(folder / 'tree-termination.json', dict(reason='Owned tree cleanup', accounting=job.accounting()))
                record['owned_job_accounting'] = job.accounting()
                record['whole_process_tree_completed'] = record['owned_job_accounting']['ActiveProcesses'] == 0
            elif process is not None and process.poll() is None:
                # Assignment failed with the startup gate still closed: no test imported.
                process.kill()
            if process is not None:
                record['exit_code'] = process.wait(timeout=30)
        finally:
            if job is not None:
                job.close()  # KILL_ON_JOB_CLOSE also covers interruption/crash of supervisor.
        record.update(duration_seconds=round(time.monotonic() - started, 3), stopped_time=time.time(),
                      stopped_utc=datetime.now(timezone.utc).isoformat())
        write(folder / 'process.json', record)
    if assigned and not record['whole_process_tree_completed']:
        raise RuntimeError('Owned process tree exit unconfirmed; stop scheduling: ' + str(folder))
    return reconcile(record, folder)


def controlled_environment(env):
    return {k: v for k, v in env.items() if k.startswith(('QT_', 'QSG_', 'SPECTRA_'))
            or k in ('PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED')}


def runtime_metadata():
    versions = {}
    for name in ('PySide6', 'numpy', 'scipy', 'pytest'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return dict(executable=sys.executable, prefix=sys.prefix, base_prefix=sys.base_prefix,
                python_version=sys.version, dependency_versions=versions)


def execute(repo, output_parent, python=None, timeout=180, jobs=2, modules=None, synthetic=False,
            child_variants=None, parallel=PARALLEL_MODULES):
    repo = Path(repo).resolve()
    python = Path(python or sys.executable).resolve()
    if timeout <= 0 or jobs not in WORKER_COUNTS:
        raise ValueError('timeout must be positive and jobs must be 1, 2 or 4')
    run = Path(output_parent).resolve() / ('run-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    source_start = source_inventory(repo)
    write(run / 'source-start.json', source_start)
    discovered = ast_inventory(repo)
    selected = set(modules) if modules is not None else {e['module'] for e in discovered}
    if selected - {e['module'] for e in discovered}:
        raise ValueError('Selected modules not discovered: ' + repr(sorted(selected - {e['module'] for e in discovered})))
    manifest = [e for e in discovered if e['module'] in selected]
    write(run / 'discovered-manifest.json', discovered)
    write(run / 'manifest.json', manifest)
    write(run / 'runtime.json', runtime_metadata())
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
               SPECTRA_FINAL_INACTIVITY_SECONDS=str(timeout),
               PYTHONPATH=os.pathsep.join((str(HERE), str(repo))))
    for key in ('SPECTRA_QUICK_MIGRATION_CASE', 'SPECTRA_ACCEPTANCE_START_GATE',
                *(variant['flag'] for variant in CHILD_VARIANTS),
                *(variant['flag'] for variant in (child_variants or ()))):
        env.pop(key, None)
    if synthetic:
        env.pop('SPECTRA_FINAL_REQUIRE_GUARD', None)
    else:
        env.update(QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   SPECTRA_SUITE_REPO=str(repo), SPECTRA_FINAL_REQUIRE_GUARD='1')
        fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        if fonts.is_dir():
            env['QT_QPA_FONTDIR'] = str(fonts)
    write(run / 'environment.json', controlled_environment(env))
    write(run / 'schedule.json', dict(jobs=jobs, manifest_order=[e['module'] for e in manifest],
          reviewed_parallel_modules=sorted(parallel),
          parallel_modules=[e['module'] for e in manifest if parallel_safe(e['module'], parallel)],
          execution_phases='manifest serial' if jobs == 1 else 'reviewed parallel, then exclusive serial'))
    records, child_records, variant_errors = [], [], []
    write(run / 'status.json', records)
    write(run / 'child-status.json', child_records)

    cancel_event = threading.Event()

    def run_one(name):
        return run_module(name, run / name, repo, env, python, timeout, cancel_event)

    for record in schedule([e['module'] for e in manifest], run_one, jobs, parallel, cancel_event):
        records.append(record)
        write(run / 'status.json', records)
        print(json.dumps({k: record[k] for k in ('module', 'passed', 'exit_code', 'timed_out', 'duration_seconds')}), flush=True)
    variants = (CHILD_VARIANTS if not synthetic else ()) if child_variants is None else child_variants
    variants = [variant for variant in variants if variant['module'] in selected]
    for variant in variants:
        name = variant['module']
        try:
            expected = declared_child_variant(repo, variant, manifest)
        except Exception as error:
            variant_errors.append(dict(variant=variant, error=repr(error)))
            write(run / 'child-variant-errors.json', variant_errors)
            continue
        record = run_module(name, run / (name + '--child-mode'), repo,
                            dict(env, **{variant['flag']: '1'}), python, timeout)
        record.update(variant='child_mode', child_flag=variant['flag'], expected_child_ids=expected,
                      missing_child_ids=sorted(set(expected) - set(record['loaded_ids'])),
                      unexpected_child_ids=sorted(set(record['loaded_ids']) - set(expected)))
        record['passed'] = record['passed'] and Counter(expected) == Counter(record['loaded_ids'])
        write(run / (name + '--child-mode') / 'process.json', record)
        child_records.append(record)
        write(run / 'child-status.json', child_records)
    all_records = records + child_records
    source_end = source_inventory(repo)
    write(run / 'source-end.json', source_end)
    before, after = ({row['path']: row for row in source} for source in (source_start, source_end))
    changes = [dict(path=path, before=before.get(path), after=after.get(path))
               for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]
    write(run / 'source-changes.json', changes)
    loaded = [case for record in all_records for case in record['loaded_ids']]
    starts = [case for record in all_records for case in record['started_ids']]
    outcomes = [event for record in all_records for event in record['outcomes']]
    declared = {case for entry in manifest for case in entry['top_class_ids'] + entry['top_function_ids']}
    normalized_loaded = {case.split('[', 1)[0] for case in loaded}
    ledger = [dict(id=case, loaded=loaded.count(case), started=starts.count(case),
                   outcomes=[e['event'] for e in outcomes if e['id'] == case]) for case in sorted(set(loaded))]
    write(run / 'case-ledger.json', ledger)
    summary = dict(run=str(run), jobs=jobs, complete_suite=modules is None,
        discovered_modules=len(discovered), modules=len(manifest), attempted_modules=len(records),
        passed_modules=sum(r['passed'] for r in records), declared_child_variants=len(variants),
        attempted_child_variants=len(child_records), passed_child_variants=sum(r['passed'] for r in child_records),
        child_variant_errors=variant_errors, process_count=len(all_records), loaded_cases=len(loaded),
        executed_cases=len(starts), outcomes=dict(Counter(e['event'] for e in outcomes)),
        skipped=[e for e in outcomes if e['event'] == 'skip'],
        unique_failed_case_ids=sorted({e['id'] for e in outcomes if e['event'] in ('failure', 'error', 'unexpected_success')}
            | {e['parent_id'] for r in all_records for e in r['subtests'] if e['outcome'] != 'success'}),
        runtime_duplicate_ids={k: v for k, v in Counter(loaded).items() if v > 1},
        current_top_ids_not_loaded=sorted(declared - normalized_loaded),
        function_style_modules=[entry['module'] for entry in manifest if entry['top_function_ids']],
        function_style_loaded_cases=sum(len(r['loaded_ids']) for r in records if r['result'] and r['result'].get('framework') == 'pytest'),
        missing_modules=sorted(selected - {r['module'] for r in records}), reused_modules=[],
        source_manifest_files=len(source_start), source_unchanged=not changes, changed_sources=changes,
        whole_process_tree_completed=all(r['whole_process_tree_completed'] for r in all_records),
        child_transport_completed=all(r['child_transport_completed'] for r in all_records),
        child_processes=sum(len(r['children']) for r in all_records), duration_seconds=round(time.monotonic() - started, 3))
    summary['passed'] = bool(manifest and all(r['passed'] for r in all_records)
        and len(records) == len(manifest) and len(child_records) == len(variants) and not variant_errors
        and not changes and not summary['runtime_duplicate_ids'] and not summary['current_top_ids_not_loaded']
        and not any(e['parse_error'] for e in manifest))
    write(run / 'summary.json', summary)
    print(json.dumps(dict(run=str(run), passed=summary['passed'], modules=len(manifest))), flush=True)
    return run, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', required=True)
    parser.add_argument('--jobs', type=int, choices=WORKER_COUNTS, default=2)
    parser.add_argument('--timeout', type=float, default=180, help='Maximum inactivity between case/lifecycle events')
    parser.add_argument('--output', type=Path, required=True, help='Evidence directory outside source-controlled input trees')
    parser.add_argument('--modules', nargs='+', help='Explicit focused run; default discovers every test*.py module')
    args = parser.parse_args()
    _, summary = execute(REPO, args.output, timeout=args.timeout, jobs=args.jobs, modules=args.modules)
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
