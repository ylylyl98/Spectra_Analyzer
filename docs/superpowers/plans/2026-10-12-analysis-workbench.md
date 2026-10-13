# Analysis Workbench Implementation Plan

**Goal:** Implement the user-approved reusable Analysis Graph workflow, candidate filtering, optional seeded fitting, linked heatmap formatting, and resumable project state.

**Approval:** The user approved the consolidated conversation design with “实现”. This plan records that authorization; no further design approval is required for implementation choices within it.

**Architecture:** A source heatmap retains its scientific/render state. An associated analysis document owns its input version, candidates, accepted seeds, fits and progress; its heatmap inherits the source formatting and adds independent result layers. The existing shared Analysis page is the editor for that document. Scientific helpers remain Qt-independent and the formal heatmap renderer is used for the main preview/export.

**Stack:** Existing Python, NumPy/SciPy, PySide6, Matplotlib and project archive serialization. No new dependencies.

## Governing requirements

1. First Analyze creates an associated Analysis Graph; subsequent entry reuses it. Clicking the result graph restores Analysis directly. No new graph from Find/Filter/Fit/Gate changes. Duplicate analysis explicitly makes another independent version.
2. Main Analysis heatmap is the exportable result. The per-Gate linecut, controls and tables are auxiliary and excluded from main SVG/PNG/clipboard export.
3. Source heatmap is not annotated or mutated by analysis. Analysis shows the explicit source/input version; original/processed and selected/all Gates scope must be clear. Auxiliary detection smoothing/derivatives never silently change the source binding.
4. Source formatting continuously propagates to associated analysis graphs (title, labels, fonts, dimensions, axes, colors, regions and normalization). Result overlay styling is separate. Persist the association. Source navigation is explicit.
5. Preserve a loose candidate pool. Loose/Balanced/Strict/Custom filters act on that pool and can recover rejected candidates. Distance suppression does not merge opposite peak/dip kinds. Permit manual accept/exclude/add/move seeds. Count truncation occurs after filtering and is optional.
6. Optional fitting supports Lorentzian/Gaussian/true Voigt with signed components, initial centers/widths and bounded or fixed seeds. Use all valid samples in the scientific fit interval. Voigt retains both sigma and gamma. Changes to accepted seeds/input retire stale fits.
7. Current Gate and All Gates run on the same explicit input. Detection/fitting outcomes distinguish empty/no-seed/failed Gates. Preserve candidate and fit data per Gate.
8. Scatter X is energy/position and Y is physical Gate. Detection and fitted-center layers are independently visible; peak/dip types remain distinct. Fitted centers are continuous coordinates, not snapped to samples.
9. Save Project persists analysis input, candidates/filter/manual seeds/fits/settings/current Gate/graph/source links; reopen and enter Analysis without recomputation. Save is independent of Export. Existing Save/Discard/Cancel semantics remain. Legacy projects continue to load.
10. Retain existing XY graph analysis, add the common numerical capabilities where applicable, and preserve XY analysis progress in project state. No unrelated refactor.

## Constraints

- The checkout is already dirty. Baseline: C:/Users/yanli/Documents/Codex/ScientificAnalysis/artifacts/analysis-workbench-2026-10-12/baseline.
- Never reset/revert unrelated changes, commit, run packaging/installers/CI, or edit original Dropbox data.
- One owner per production file. Workers use focused red/green tests. Parent reviews baseline-relative actual changes and logs.
- Existing known baseline failures in background/control tests are recorded in artifacts/fit-graph-creation-2026-10-12; reproduce any newly encountered unrelated failures against the baseline before classifying them.

## Execution

- [x] A. Numerical engine: candidate collection/filter presets, meaningful candidate metrics, seed fitting and Voigt. Own fitting.py, a new pure candidate module, and focused new numerical tests. Publish exact API to the coordinator before consumers implement.
- [x] B. Analysis document state/render/persistence: independent scientific ownership, source formatting relationship, durable candidate/seed/fit state, formal scatter layers and serialization. Own dataset_state.py, project_io.py, plot_presentation.py, plot_snapshot.py, svg_export.py and focused new tests. Publish owner/helper API to the coordinator. No MainWindow edits.
- [x] C. UI integration: automatic/reused result graph routing, shared Analysis editor, formal upper-heatmap preview/export, filter/seed controls, source navigation, duplicate, full Gate workflow and XY continuation. Own main_window.py, related new analysis UI modules, xy_analysis.py/xy_analysis_panel.py, necessary graph/tree/page integration and focused workflow tests. Integrate A and B after their contracts settle.
- [x] D. Review and correct: inspect exact modified files, focused outputs, original graph isolation, format inheritance, failure handling and persistence. Corrections remain delegated.
- [x] E. Verify with copied Bo DAT fixtures (0T and 8T) and synthetic Voigt: Find-only and optional fit, source vs result export, independent points/styles, save/reopen progress, narrow/default window screenshots. Verify original data hashes remain unchanged.

## Proof boundaries

- Numerical tests: candidate recovery on relaxed filters, finite-gap behavior, nearby peak/dip retention, physical width metrics, true signed Voigt parameters and fixed centers; no source-array mutation.
- State tests: distinct analysis science, source format propagation without overlay leakage, duplicate independence, source deletion behavior, project roundtrip and malformed-record rejection, legacy default compatibility.
- Workflow tests: actual Analyze/Find/Filter/Fit/Export actions; single graph reuse; direct Project result entry; source links; input/seed invalidation; no linecut in formal export; retained candidate/manual/fit state on reopen.
- Visual/export checks: same base heatmap geometry/colors/title before and after analysis, added only selected result layers; physical coordinates under axis exchange; main preview matches exported figure; 1200/1500 width controls usable.

## Progress and contracts

Implementation contracts and evidence are appended here as the responsible workers report them. Requirements above remain authoritative.

### Active ownership (implementation underway)

- `implement_peak_engine`: task A, fitting.py + peak_candidates.py + new numeric tests.
- `implement_analysis_document`: task B, dataset_state/project_io/plot_presentation/plot_snapshot/svg_export + new state tests.
- `implement_analysis_workbench_ui`: DAT portion of C, main_window and new workbench module/panel + routing/workflow tests. Existing XY persistence/integration follows after these contracts land; do not edit its files concurrently.

### Accepted cross-worker contracts

- `PeakCandidate(index, position, amplitude, kind, prominence, prominence_fraction, width, noise_strength, detector)` is frozen, JSON-safe via `to_dict/from_dict`. Amplitude is source Y, width is physical X, kind is `peak|dip`, detector `Original|D2`.
- `CandidateFilter(preset='Balanced', min_prominence_fraction=.03, min_noise_strength=2, min_width=0, max_width=0, min_distance=0, max_count=0)` is frozen and serializable. Distance is physical X; zero count means unlimited. `candidate_filter_preset`, `collect_peak_candidates`, `filter_peak_candidates` are the pure entry points.
- `fit_multi_peak` adds true Voigt and optional `initial_amplitudes`, `initial_widths`, `initial_gammas`, `fixed_centers`, `center_bounds`. FitResult adds optional sigmas/gammas; Voigt widths are sigma and amplitudes are signed peak heights.
- GraphState gains `analysis: AnalysisDocument|None`; `analysis_owner(session, graph=None)` returns the isolated scientific DatasetSession or the original owner. Workspace.active remains the parent dataset; there is no temporary owner swap.
- `ensure_analysis_graph(session, source_graph_id=None, duplicate=False)` creates/reuses/activates; `duplicate_analysis_graph(session, graph_id=None)` copies progress. `graph_format_view` and `graph_presentation` resolve linked source formatting. Formatting does not propagate Gate selection or scientific state.
- AnalysisDocument has source_graph_id, input_version/input_label, science, candidate_pools, filters/filters_by_gate, seeds_by_gate, gate_status, overlay styles. Full input matrix is retained; sparse graph Gate selection only controls display.
- Frozen `AnalysisSeed(position, kind='peak', enabled=True, manual=False, source_index=None, amplitude=None, width=None, gamma=None, fixed_center=False, center_min=None, center_max=None, seed_id=uuid)` has JSON conversion. None initial amplitude/width/gamma means estimate; candidate source Y is not a baseline-subtracted fit amplitude.
- Document methods: `set_candidates(gate,pool,settings=None)`, `apply_filter(settings,gate=None)`, `set_seeds(gate,seeds)`, `accepted_seeds(gate)`, `retire_fit(gate=None)`, `record_fit(gate,fit,status='fitted',message='')`; `job_token=(input_version,revision)` guards late work.
- Project schema 9 for new analysis/progress/Voigt payloads; ordinary old-capability saves can remain schema 8. Load legacy schemas. Source sidecars omit analysis documents and cannot substitute for project persistence.
- Future XY owner may add `Comparison.analysis_state: dict|None`; project serializer handles strict JSON-safe values, panel validates the internal versioned payload.

### Numerical engine acceptance

`implement_peak_engine` changed fitting.py, peak_candidates.py and two numerical test modules. The parent inspected the complete baseline-relative diff and verified it matches the actual files. `numerical-green.txt` records 26 passing focused tests: 17 new numerical cases, seven existing XY numerical cases and two existing fit integration regressions. No packaging or full-suite run. Numeric APIs are available to the state and UI workers.

### XY continuation ownership

`implement_xy_analysis_progress` owns workbook_state.py (the optional Comparison progress field), xy_analysis.py, xy_analysis_panel.py, xy_plot_page.py if needed and focused XY progress tests. MainWindow remains owned by the DAT UI worker; project_io remains owned by the document worker.

### Real-data boundary found during review

Both copied Bo files (0T/8T, 101 Gates by 1340 samples) complete candidate detection without changing source arrays; results are recorded in `real-candidate-engine.json`. The full 0T pool contains 86,426 candidates. The initial per-record JSON representation exceeded the existing 16 MiB manifest limit on Save Project. The document worker is correcting this with validated numeric array tables for candidates/seeds and compact seed identity metadata, retaining every candidate and manual override. `verify_full_bo_state.py` in the artifact directory is the full-pool roundtrip acceptance harness. No acceptance claim until this passes.

### Analysis document acceptance

The compact schema uses nine-column candidate and eleven-column seed NPY tables, strict column/code/coordinate validation, and ordered seed IDs. The parent reviewed the actual state, ownership, renderer and codec changes plus the focused test output. `analysis-document-focused-tests.log`: 83 tests passed using the repository venv and offscreen Qt. `full-bo-state-results.json`: complete 0T and 8T candidate pools (86,426 and 81,574), seeds, filters and scope survived save/reopen; archives are 7.846 and 7.547 MiB. The 100,000-candidate synthetic regression also preserves IDs and exact project digest under the existing manifest limit. Detached render candidates now borrow scientific analysis ownership and copy visuals only.

### XY continuation acceptance

`implement_xy_analysis_progress` changed workbook_state.py, xy_analysis.py, xy_analysis_panel.py, added test_xy_analysis_progress.py and updated one obsolete width-change expectation in test_xy_analysis_workflow.py. xy_plot_page.py remains identical to the task baseline. The parent inspected the baseline-relative diff, restored-progress validation, signed/continuous manual seeds, screenshots and test output. `xy-progress-tests.txt`: 28 focused tests pass; `xy-graph-cleanup-tests.txt`: 13 existing graph tests pass. The graph test runner explicitly clears the clipboard at shutdown; the same post-OK native clipboard shutdown failure was reproduced with unmodified baseline code (`xy-baseline-teardown-tests.txt`). No production cleanup workaround was added.

### DAT review and real-data verification in progress

The parent reviewed the complete new workbench module and baseline-relative MainWindow changes. Corrections include source-role-specific Original/Processed entry, pending numeric/seed-editor capture on Save, immediate invalidation of retired fit overlays and correct interval-participating seed/result pairing. At 1200 and 1500 pixel window widths, the compact sidebar fits without horizontal scrolling; extended seed/filter parameters use dialogs. Original Dropbox files remain byte-for-byte unchanged (`original-data-final-hashes.json`).

Both real Bo DAT fixtures completed all 101 Gates, reversible Strict/Loose filtering, exact source raster/geometry plus scatter export, format propagation and Save Project. Real-data repeated reopening is still under investigation: offscreen Qt intermittently raises native access violations in QSvgRenderer or QSettings constructors. Skipping duplicate preview and disabling automatic cyclic GC did not eliminate the issue; all render calls remained on the GUI thread at depth one, and the captured SVG passes 100 standalone parse/render cycles. No speculative production workaround was added. Baseline/runtime comparison is required before final acceptance.

### DAT focused UI acceptance

`dat-workbench-tests.txt` records 22 workbench/navigation tests passing, exit 0, including signed Gaussian/Voigt fitting, cancellation, failed/no-seed outcomes, fit-overlay invalidation, narrowed-interval result pairing, Save/reopen without recomputation and source input role isolation. The parent inspected the complete production and test changes. A final source-removal navigation correction was delegated to `fix_analysis_source_fallback`: when no ordinary source graph remains, a newly requested Original/Processed source copies the retained Analysis format. `source-fallback-focused-tests.log` records both the new deletion/reentry regression and existing role-binding regression passing, exit 0; the parent verified the actual two-line production change and test assertions. Existing result input, seeds, candidates and fit remain intact.

The bounded baseline control completed the old real-DAT workflow and three new-window project restores with exit 0. Current code still fails under the minimal Qt platform as well as offscreen. The native issue therefore cannot currently be classified as pre-existing or offscreen-only; final real-data acceptance remains open.

### Final native fix and completed acceptance

The investigation above is resolved. `fix_analysis_native_crash` identified stale Shiboken registrations from `QTableWidgetItem.clone()` after Qt replaced the summary cells. A native exception probe showed a table-item wrapper being returned during construction of a new QObject at the reused address. The isolated clone reproducer crashes, while copy construction survives 1,000 table replacement cycles and 10,000 renderer constructions. The production correction uses `QTableWidgetItem(source)` for the summary copy; no renderer, dependency, GC or inheritance workaround is required.

The parent inspected the exact production/test diff, native diagnostic evidence, regression failure/pass output and final logs. `native-diagnostics/focused-gui-tests.log`: all 24 workbench/navigation tests pass, exit 0. Both copied real DAT workflows now pass with normal GC and production rendering, exit 0: all 101 Gates, reversible filters, Gate inspection, exact source raster/axes plus scatter in SVG/PNG, linked formatting, Save Project and three fresh MainWindow restores per DAT without Find. Evidence: `native-diagnostics/fix-results.json`, `real-dat-1-fixed.log`, `real-dat-2-fixed.log`, and `real-ui-verification/dat-{1,2}/verification-native-fix.json`.

All implementation, review and scoped acceptance steps are complete. Validation is focused source/runtime verification, including offscreen Qt; no full suite, packaging, installer or CI run was requested or performed. Start the updated source through `Run_Spectra_Local.bat`. Progress survives restart after Save Project; Export is not required for persistence.
