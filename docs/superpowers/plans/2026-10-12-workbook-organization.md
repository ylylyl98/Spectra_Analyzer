# Workbook Organization Implementation Plan

**Goal:** Organize each imported file and its current/snapshotted results in a Workbook while preserving independent cross-file graphs.

**Architecture:** Extend existing Sheet identity for live result projections and detached snapshots; retain authoritative DatasetSession results and existing bounded JSON/NPY persistence. Default new imports to one Workbook per file and merge by moving stable Sheet memberships.

**Tech stack:** Existing Python, NumPy, PySide6, Matplotlib and unittest only.

**Spec:** ../specs/2026-10-12-workbook-organization-design.md; ../../adr/ADR-0001-current-result-sheets.md; ../../ui-spec/workbook-organization.md.

**Approval:** User requested implementation of the quoted organization proposal and explicitly chose current-result updates with optional snapshots. Parent owns review; substantial code is delegated to a worker. No additional user approval, commit or release build is requested.

## Constraints and evidence

Repository: C:/Users/yanli/Desktop/Spectra_Analyzer. Preserve preexisting changes. Baseline and legacy fixture: C:/Users/yanli/Documents/Codex/ScientificAnalysis/artifacts/workbook-organization. Use its baseline.json manifest to compare only this task; do not mutate baseline source files. Never enable object-array pickle. Existing grouped owners and graphs must survive migration.

## Implementation sequence (one worker, dependent steps)

- [x] **Model and persistence:** Extend `spectra_app/workbook_state.py` and `project_io.py`, with a narrowly scoped result projection helper module if it makes responsibilities clearer. Tests in `tests/test_workbook_organization.py` cover role uniqueness, unequal fit grids, empty/stable results, snapshots and schema6/source-free migration. Establish RED first, then implement and verify this earliest boundary before UI integration.
- [x] **Import and tables:** Update `xy_import_dialog.py`, `workbook_page.py`, `sheet_comparison_dialog.py`, and scoped `main_window.py` import/table paths. Implement default per-file books with explicit grouping, fixed role context, mixed read-only display/copy and numeric-only plotting. Adapt only tests whose grouped-import/mode-selection expectations are explicitly superseded; retain their original behavioral assertions using explicit grouping or separate role selection.
- [x] **Result lifecycle and actions:** Wire committed processing/fitting/clear/selective invalidation paths to projection sync and open-document refresh. Add snapshot and merge UI with preflight/cancel semantics. Tests exercise actual controller callbacks and action entry points, not helper-only simulations. Source replacement/removal and snapshot independence are required.
- [x] **Focused verification:** Run new tests plus affected `test_workbook_state`, `test_workbook_project`, `test_workbook_workflow`, `test_import_compare_workflow`, `test_sheet_batch_comparison`, `test_comparison_display`, `test_curve_manager`, relevant project migrations, background analysis and edit-history cases. Select exact relevant cases where a whole module would be unnecessary. Record full commands/output and actual test counts. No full suite, packaging or CI.
- [x] **Parent review:** Supply stable APIs/control names early, final write set, baseline-relative diff, logs and normal/compact screenshots. Parent checks diff and runs independent genuine-v5 migration, import/result/snapshot/merge save-reopen acceptance. Resolve concrete findings through the worker before completion.

## Required observable proof

Use actual project write/read for persistence; use differing Gate fit X grids; compare raw arrays before/after; prove current output empty after invalidation and snapshot unchanged; verify every existing Curve/Sheet ID through merge; use the supplied genuine-v5 archive. Releasing synthetic Ctrl/Shift modifiers in Qt test gestures avoids leaking input state to later tests. Test empty result mappings without fabricating dummy samples. Keep failures and rollback effects observable.

Status: implemented and reviewed. Final focused run: 99 tests passed; independent parent acceptance: 3 tests passed, including genuine schema5 migration, malformed-archive rejection and actual snapshot/merge/clear/remove/save/reopen UI. Normal/compact production-style screenshots inspected. Existing background-cancellation test failures reproduced on the unmodified baseline; the existing derivative-preview limitation for NaN traces was isolated in the parent fixture using the Original Spectrum control. No full suite, packaging or release validation was run. Evidence: artifacts/workbook-organization in the calling task workspace.
