# Full-suite test supervisor

Use the GitHub Actions **Windows full suite** workflow for complete verification.
It runs on main pushes, pull requests, or a manual **Run workflow** action. Eight
independent `windows-2025` machines run duration-balanced module shards with one
module at a time per machine, preserving native desktop/clipboard isolation.
The required **Windows full-suite gate** job checks the exact union of discovered
modules, child variants and case ledgers against the same commit and source hashes.
Every shard must pass; a missing, duplicated or inconsistent shard fails the gate.
Only dependency downloads are cached; test results are always fresh.
Build or publish a release only after **Windows full-suite gate** passes for the
exact source commit; this is a release-process prerequisite, and this workflow
does not configure branch protection or publish releases.

Download `windows-shard-0` through `windows-shard-7` for raw evidence and
`windows-full-suite-verdict` for `full-suite.json` and `summary.md` (counts and
per-shard durations). A green shard alone is not a complete-suite verdict.
The wrapper commands are `tools/testing/ci_suite.py shard --commit <sha> --count 8
--index <0-7> --output <new-directory>` and `tools/testing/ci_suite.py gate --commit
<sha> --count 8 --evidence <downloaded-artifacts-root> --output <verdict-directory>`.

Keep local runs focused on the affected modules, using the repository's intended environment:

```powershell
.\.venv\Scripts\python.exe -B tools\testing\run_suite.py --execute --jobs 2 --output C:\test-evidence --modules tests.test_graph_batch_export tests.test_project_io
```

`--jobs 1` is the serial baseline; `--jobs 2` remains the conservative local
default. `--jobs 4` is an explicit option for a machine with enough free memory,
after comparing the same focused selection at 1/2/4 workers. Each active module
may also have a fresh case child and background threads. More workers can slow
CPU-heavy tests or exhaust available memory, so four is not the default.
`--modules` selects an explicit focused run; the summary marks it
`complete_suite: false`. Omitting it still discovers every module for a deliberate
full local diagnostic run, but routine full verification belongs on GitHub.
Use an output directory outside `tests`, `spectra_app`, and `tools`. Each invocation
creates a fresh run directory. It never skips cases or reuses test results.

With two or four workers, the explicit `PARALLEL_MODULES` list runs first, with
at most that many module processes alive. After that phase has completely exited, every other
module runs alone. Manifests, status records, and case ledgers use sorted discovery
order; `schedule.json` records the phase policy. One worker follows discovery
order throughout. Unknown modules remain exclusive. The allowlist now contains
75 modules: the original 19 and 56 reviewed additions. The additions accounted
for 585 cases, 178 fresh children, and 1,391.343 seconds of module time in the
October 28 profile. That is workload moved out of the exclusive phase, not a
measured speedup; source-identical runs are required for a speed comparison.

The additional review covers the following boundaries. Names omit `tests.test_`.

| Modules | Isolation reviewed |
| --- | --- |
| `background_io`, `background_analysis`, `workflow`, `fit_width_labels` | Fresh Quick child/application/settings and temporary sources. Worker events, cancellations and bounded completion waits stay local; these tests do not compare elapsed performance. |
| `analysis_navigation`, `analysis_plot_style`, `shared_dat_properties_bridge`, `signal_titles`, `processing_result_groups` | Per-case temporary application profile/data, explicit local Quick controls, local state and project roundtrips. `signal_titles` borrows annotation fixture methods, but does not run its native-platform `setUp`. |
| `comparison_export_format`, `image_export`, `graph_batch_export`, `heatmap_page_growth`, `graph_publication` | Per-case temporary sources/exports/projects and headless rendering. Export error fixtures fail before writing their fixed temporary sentinel names. No clipboard export commands. |
| `unified_import`, `dialog_layout_compact`, `quick_plots`, `project_navigation`, `project_tree_hierarchy`, `project_tree_wrap`, `ui_layout` | Temporary source/settings fixtures and offscreen windows. QTest helpers address retained local receivers. Menu/input routes use the offscreen platform; `native` in some test names means a production control or project format. |
| `analysis_navigation_bridge`, `ordinary_analysis_bridge`, `xy_analysis_bridge`, `analysis_header_bridge`, `common_prompt_bridge`, `curve_prompt_bridge`, `peak_menu_bridge`, `figure_options_bridge`, `subplot_editor_bridge`, `analysis_canvas_bridge`, `analysis_workbench_bridge` | In-memory bridge/controllers and fake services, local Qt objects; temporary source data where needed. No desktop focus or clipboard assertions. |
| `analysis_document_state`, `dataset_state`, `document_registry`, `named_graphs_state`, `project_model`, `project_io`, `workbook_project`, `sheet_metadata`, `workbook_state`, `fixed_recipe_reload`, `curve_palettes` | In-memory state and temporary-file persistence. Shared `test_project_io` archive helpers operate only on caller-supplied temporary paths. |
| `plot_templates`, `selective_plot_formats`, `plot_batch`, `plot_annotation_geometry`, `hybrid_svg`, `analysis_overlay_export`, `fit_scale_reliability`, `peak_candidates`, `voigt_fitting`, `xy_analysis`, `xy_io`, `dat_preamble`, `processing` | Local science/rendering/IO contracts, temporary output files, `isolate_settings` for preference users; pytest parametrization remains intact. |

The transitive review includes `quick_migration_case`, `quick_application_helpers`,
`settings_helpers`, the DAT/ordinary-analysis/header/formal/XY/workbook/Project
input helpers, background wait helpers, scientific fixtures, and borrowed test
fixture methods. The suite's QSettings guard additionally isolates module and
child preferences. Process-local offscreen editor input is allowed; desktop
activation/focus assertions, native Windows popups, shared clipboard users,
performance/latency/responsiveness/refresh-cost measurements, builds and external
resources remain exclusive. In particular:

- `annotation_persistence` and `custom_colormaps` switch selected cases to the
  Windows platform; `analysis_workbench` asserts desktop window focus.
- `graph_documents` and `workbook_workflow` access the clipboard directly.
  `analysis_drawing_quick` also reaches it through inherited workbench cleanup
  and `copy_graph_svg`; `workbook_organization` calls `copy_selection`.
- `standalone_build`, `windows_launch`, and the supervisor's own tests remain
  exclusive. Other unreviewed modules stay exclusive even if their names look safe.

Changing the allowlist requires reviewing module and transitive helper resource
boundaries again. Never infer safety from test names or headless setup alone.

Every module and its descendants belong to a dedicated Windows Job. A startup
gate prevents Qt/test imports before assignment. Timeout or abnormal cleanup
terminates only that Job, and closing the supervisor's Job handle kills remaining
owned processes. This runner requires Windows. The module gets unique settings
and artifact directories. A child-only `sitecustomize` redirects QSettings away
from the application's normal profile and verifies the guard before discovery.
The supervisor never imports Qt or test modules and never patches its own Popen.

Evidence includes source hashes before/after, the complete discovered AST
inventory, selected manifest, loaded case IDs, starts/stops/outcomes, skip reasons,
raw stdout/stderr, pre-teardown test reports, actual exit codes, child transport
reports, Job accounting, runtime/dependency versions, and module/wall timings.
The three formal QGui child selectors run as declared variants; their AST selector
and exact loaded-case inventories are checked. Fresh Quick child processes and all
existing assertions remain unchanged, including stderr and teardown checks.

Modules with top-level test functions use the existing pytest installation in
their isolated worker. Native parametrization, fixtures, assertion rewriting and
teardown are retained; each collected parameter case gets its own ID and terminal
outcome. Other modules keep the unittest path. AST coverage includes top-level
functions and checks their normalized parameter IDs. This closes a legacy gap in
`tests.test_processing`, whose functions the old unittest-only supervisor loaded
as zero cases. The summary reports these additional function-style cases separately.

Zero-case modules, missing/corrupt reports, case mismatches, duplicate or unknown
terminal outcomes, loader errors, incomplete transport,
timeouts, nonzero exit after a green unittest report, incomplete process trees,
duplicate cases, and changed source hashes fail the run. An interrupted run's
per-module files can be inspected; absence of a final passing summary is never a
passing result. Atomic status snapshots support inspection while work continues;
there is no result-resume mode.

Focused supervisor verification:

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_test_runner -v
```
