# Daily Plot Workflow Implementation Plan

**Goal:** Import several files, create a graph, and repeatedly compare selected sources with fewer windows, ambiguous destinations, and interruption dialogs.

**Architecture:** Extend the current Qt Workbook/Sheet/graph ownership and shared toolbar. Retain an implicit DAT graph for analysis while distinguishing explicitly created graph documents in persistence. Keep comparison target pinning as project-session UI state, independent of which document has focus. Execute two sequential worker tasks with parent diff and observable UI review.

**Tech Stack:** Python, PySide6, Matplotlib, existing native project/sidecar serialization, unittest.

**Spec:** The user-approved chat proposal beginning “建议下一步优先做三件事”, followed by “进行这些改进”. This implements the recommended first round. Drag files/columns onto plots was explicitly the second round in that proposal and is not part of this execution.

## Global constraints

- Repository: `C:/Users/yanli/Desktop/Spectra_Analyzer`. All pre-existing dirty/untracked work is retained. Immutable task baseline and logs: `C:/Users/yanli/Documents/Codex/ScientificAnalysis/artifacts/daily-plot-workflow-20261012`.
- No reset, clean, commit, packaging, installer, full suite, live-app restart, or live-app termination. Use isolated QSettings and temporary test sources.
- Preserve stable Project/Workspace/Properties dimensions, explicit panel hiding, free movable subwindows, document identity/geometry, editor guards, style/source bindings, and original independent X grids.
- New ordinary imports use first X/all remaining Ys and one Workbook per file. Explicit advanced grouping and import-with-options remain available.
- Older saved graph documents remain visible/restorable. Do not infer publication from geometry, name, source filename, or whether a document is currently open.
- A selected Workbook contributes Original Sheets only by default. Explicitly selected Sheets retain their role/mode, including result Sheets. A merged Workbook may have multiple Original Sheets and must retain each original source.
- Original/Processed values, scientific calculations, fixed physical heatmap dimensions/fonts, and dataset/curve identities remain unchanged by these workflow edits.

## Task 1 — Import window discipline and explicit graph documents

**Owner:** worker for `dataset_state.py`, `main_window.py`, `project_io.py`, `plot_state.py`, `plot_dialogs.py`, narrowly required lifecycle helpers, focused tests, README.

**Deliverable:** A new multi-file import creates all data owners, opens only the first affected Workbook, and creates no visible heatmap document until an explicit plot action. Saved graph documents retain all existing restore/export behavior.

- [x] Add behavioral RED tests through ordinary `import_data_files` for mixed DAT/XY input: all sources registered, exactly one newly opened Workbook, no implicit heatmap in tree/open set/batch inventory; existing open windows untouched. Single-file import opens its Workbook and repeated source import reuses ownership.
- [x] Add `GraphState.published: bool` at the end of the record. Explicit GraphState construction/create_graph defaults true; fresh DatasetSession analysis placeholder false. Old native versions 1–7 and sidecars 1–5 migrate every saved graph to true. Fresh imports false; recalled collections restore their recorded value. Preserve the implicit graph's analysis state and serialization.
- [x] Update strict native schema to 8 and sidecar schema to 6. Reject non-boolean publication values and persisted open/active/geometry heatmap-document references to unpublished graphs. Keep earlier exact field-set validation and migration tests; do not loosen field checks to accept arbitrary payloads.
- [x] Publish only after successful explicit Open Graph/New Graph/Workbook Heatmap actions. Publish a quick-plot matched graph before saving its sidecar. Creating/duplicating an explicit graph publishes that new graph, not its implicit source. Closing keeps publication true. Analysis activation/rendering, saved project installation, processing, and save alone must not publish.
- [x] Filter only user-visible graph inventories, selector actions, workspace document layout snapshots, and batch export choices. Scientific invalidation/source/serialization loops still include the implicit graph. `_select_graph_page` may bind/cache it but cannot show it, including automatic `show=True` calls when switching outer tabs. Keep a clear explicit open/create route.
- [x] Replace the reverse all-Workbook opening loop with opening the first affected Workbook/Sheet for ordinary import-only. Keep explicit import-and-compare / add-to-graph flows and any existing windows intact.
- [x] GREEN proof: fresh import → process/save/reopen still only Workbook; explicit Heatmap → close/save/reopen remains listed and can reopen; old native/sidecar fixtures preserve every graph; batch inventory excludes placeholder. Relevant styles/source subsets and rollback guards remain verified.
- [x] Return actual incremental diff against `before/`, exact changed paths, commands, RED/GREEN outputs, and final file hashes. Parent reviews and captures `after-import/` before Task 2.

## Task 2 — Consistent target, source defaults, and contextual controls

**Owner:** second worker for `main_window.py`, `workbook_page.py`, `sheet_comparison_dialog.py`, `xy_plot_page.py`, optional inline plot icons, focused tests, README. Preserve Task 1 changes; compare against `after-import/`.

**Deliverable:** Continuous file-to-file comparison has a visible, stable target and no routine duplicate modal, and comparison sources default to original data rather than all result Sheets.

- [x] RED native tests: select target A and pin; activate graph B, import/open another Workbook, switch Sheets; Add still sends to A and source stays active. Explicitly changing the target updates the pin. Renaming A refreshes its displayed name; closing A retains the destination; deleting A clears/disables the destination rather than silently sending to B. New/open project clears session target pin safely, including rollback.
- [x] Reuse existing destination IDs and append routes. Add accessible `Pin target` and `Show target` actions to the current shared toolbar/menu. The pinned destination overrides focus-driven last-target updates. Unpinned behavior retains sensible last-target selection; explicit combo/menu selections agree. Use project-session state, not a new project-schema field. Preserve native overflow so destination/pin/show remain reachable at narrow widths.
- [x] Direct Sheet/Gate additions skip duplicates by default and use the existing added/skipped status report. Keep an explicit `Add again` command for intentional copies and existing review-dialog include-existing options. Do not show a routine Skip/Add/Cancel modal. Existing source/target invalid draft guards and batch validation remain atomic.
- [x] Existing-Sheet append keeps the source active and updates the graph; Show target opens its retained graph when requested. New graph creation still activates the graph. Preserve independent grids, curve styles and custom legends.
- [x] Project multi-selection comparison resolves selected Workbooks to Original-role Sheets only; explicit Sheet selections are honored and deduplicated. Merged Workbooks include all their Original sources. No-Original selection gets clear feedback instead of guessing a result Sheet. Relabel the entry `Compare selected data…` so its file/Sheet scope is evident. The comparison review shows the source and curve count and permits deliberate result selection.
- [x] In the comparison dialog, Workbook-level checking follows the same Original default; individual result-Sheet checkboxes remain available. Add an explicit `Match selected Gates` action for a selected DAT Original/Processed source: apply its selected physical Gate values to checked DAT Original/Processed Sheets, resolving each target's own column indices. Exact values are matched, never equal column positions or silent nearest-Gate substitutions. Validate the whole requested mapping before mutation; if a target is missing a value, keep choices unchanged and name the missing source/value in feedback. Ordinary XY mappings and non-Gate result Sheets remain independent. Do not run this action automatically when merely opening/checking the dialog.
- [x] Rename `Generate Heatmap` / `Generating Heatmap…` to `Apply processing` / `Applying processing…`, including status/tooltips. Place the existing processing controls with Analysis controls, preserving bindings and callback/object identities. Keep the Heatmap toolbar action strictly graph creation from the chosen Sheet.
- [x] Reduce duplicated navigation on the right: move the existing Data Source/import/dataset/graph/folder controls into a collapsed advanced/source-navigation section, retaining their functionality and test-visible owners. Show concise active graph/source context and keep usual appearance controls accessible. Preserve per-context tab preference where reasonable; a fresh Heatmap should initially show its View properties rather than the redundant dataset list. Do not restructure global window modes or rebuild the theme.
- [x] GREEN focused proof covers pin/delete/close/rename/project replacement, direct duplicate skip and explicit copies, Original-only defaults vs explicit results, review cancellation, correct processing behavior, shell geometry/manual hiding, and narrow toolbar reachability.
- [x] Return actual incremental diff, files, commands/logs, exact limitations, and final hashes. Do not claim success from a zero diff or from mocked dispatch alone.

## Parent verification and completion

- [x] Inspect both workers' actual diffs and focused output; reject unrelated edits or weakened tests. Interrupt completed children.
- [x] Independently exercise native Qt input with app stylesheet/font: mixed import, explicit Heatmap, pinned cross-file append, rename/close/reopen, duplicates, and project reload. Compare actual MDI geometry across Workbook/Heatmap/XY and inspect screenshots at normal and narrow widths.
- [x] Record evidence and remaining limitations; update this plan. Explain the concise end-user workflow and BAT restart without touching a running app.
