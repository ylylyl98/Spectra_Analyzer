# Read-only Workbooks and XY Comparison Implementation Plan

> **For agentic workers:** Use the installed executing-plans skill to implement assigned tasks with focused red/green evidence. The user already selected delegation; no further execution-choice question or commits.

**Goal:** Complete import -> read/copy Sheet -> independent X/Y selection -> cross-file graph -> save/reopen/export.

**Architecture:** Separate plain workbook/curve state from existing DAT scientific sessions. Extend the existing native MDI host with workbook and comparison pages, backed by strict project schema4 and embedded arrays.

**Tech Stack:** Existing Python 3.14, PySide6, NumPy, Matplotlib, unittest.

**Spec:** `docs/superpowers/specs/2026-10-12-sheet-xy-workflow-design.md`

## Global Constraints

- Actual repository: `C:\Users\yanli\Desktop\Spectra_Analyzer`; runtime `.venv\Scripts\python.exe`.
- Preserve every preexisting edit; current baseline: `C:\Users\yanli\AppData\Local\Temp\spectra-sheet-xy-baseline-6mhcr0bv`.
- No added dependency, spreadsheet editing/formulas, normalization/offsets/fitting, commits, or packaging.
- Keep legacy DAT science/sidecars/CSV seed drops, native free-window geometry and transactional project rollback.
- Use offscreen Qt for automated tests and actual APP_QSS for screenshots. Do not claim desktop-native manual checks.
- Foundation then integration are dependency-ordered; parent reviews actual files and outputs after each worker.

### Task 1: Numeric Sheets, live curves and project v4 (foundation worker)

**Files:** Create `spectra_app/workbook_state.py`, `spectra_app/xy_io.py`, `tests/test_workbook_state.py`, `tests/test_xy_io.py`, `tests/test_workbook_project.py`; modify only `spectra_app/dataset_state.py`, `spectra_app/project_io.py`, related project-state tests.

**Interfaces:** Publish exact constructors/helpers in a handoff Markdown under the validation directory. Workspace gains `workbooks`, `sheets`, `comparisons`. Public helpers cover workbook/Sheet registration, DAT Sheet ensure/reconcile/remove, sheet columns/values, mapping validation, comparison create/add/resolve/remove, and document identity conversion. No Qt dependency in these records. Runtime keys/layout contract is fixed in the spec.

- [x] Add parser/domain red tests for independent column mappings and grids, malformed line numbers, missing cells, raw/processed DAT orientation, source removal/reconcile, and unique memberships. Example observable fixture:
  ```python
  # A has X at 1 and Y at 0; B has X at 2 and Y at 1.
  a = np.array([[10., 1.], [20., 2.]])
  b = np.array([[99., 30., 4.], [98., 50., 8.], [97., 70., 12.]])
  np.testing.assert_array_equal(a[:, 1], [1., 2.])
  np.testing.assert_array_equal(b[:, 2], [4., 8., 12.])
  # Resolve registered curves and compare to these independent expected arrays.
  ```
- [x] Run new tests with `.venv\Scripts\python.exe -m unittest tests.test_xy_io tests.test_workbook_state -v`; retain initial failure evidence.
- [x] Implement records/parser/resolution and Workspace additions. Parse explicit XY data without dat_io axis inference/sorting. Use precise error messages and no source rereads during resolution.
- [x] Add schema4 tests: XY-only and mixed roundtrip with source files deleted; dirty stamp changes on mapping/label/visibility/arrays; v1/v2/v3 reads; reject invalid membership/source/index/style/layout and unused arrays.
- [x] Implement explicit encode/decode, canonical snapshot and runtime stamps. Central document helpers upgrade legacy heatmap records. Keep old record shape accepted at input boundaries until integration converts producers.
- [x] Run `.venv\Scripts\python.exe -m unittest tests.test_xy_io tests.test_workbook_state tests.test_workbook_project tests.test_project_io tests.test_named_graphs_state -v`; inspect errors in logs and write exact API handoff. Report changed files and compatibility caveats. No UI edits in this task.

### Task 2: Mixed document UI and complete workflow (integration worker)

**Files:** Create `spectra_app/workbook_page.py`, `spectra_app/xy_plot_page.py`, `spectra_app/xy_export.py` (only if it provides an actual separate rendering boundary), `tests/test_workbook_workflow.py`, `tests/test_xy_plot_workflow.py`; modify `spectra_app/graph_documents.py`, `spectra_app/main_window.py`, focused `test_graph_documents.py`/`test_project_workflow.py`/`test_ui_layout.py`, `README.md`. Foundation corrections go back through the parent.

**Interfaces:** Consume foundation handoff and spec exactly; new page classes expose real optional lifecycle/input roots, not fake PlotPage fields. Keep heatmap tuple2 keys, new tuple3 auxiliary keys, and v4 unified layout. UI actions call domain functions rather than duplicate parser/persistence logic.

- [x] Write integration tests exercising the user-visible three-file route through import, independent per-Sheet combos/checks, New/Add comparison, actual plotted arrays, TSV clipboard, readonly flags, hide/rename/style/remove and source lookup. Begin with one minimal file, then all three to expose ownership errors.
- [x] Run focused new tests to record missing behavior.
- [x] Implement workbook page with virtual table, Sheet tabs, remembered X/Y controls, Gate bridge, Raw/Processed, target comparison selection; navigator import/open/rename/remove. Do not change existing seed-drop semantics.
- [x] Implement XY graph editor/canvas and shared renderer exports. Mask nonfinite pairs with NaN gaps rather than join separated segments. Use exclusive output creation or explicit standard overwrite confirmation.
- [x] Generalize MDI lifecycle and layout. Branch MainWindow heatmap-only rendering/commands/rollback/invalidation by document kind. Keep a visible active workbook/XY page across outer tab changes and target Add commands to the selected comparison.
- [x] Integrate DAT registration/reload/removal/processing refresh, project capture/marker/reset/install/rollback, and XY-only modified state. Undo/heatmap shortcuts must not act on a hidden document.
- [x] Prove source-free roundtrip, mixed close/reopen, allclosed and failed-install rollback using actual MainWindow methods. Verify DAT raw/processed Gate semantics and unchanged seed import.
- [x] Run focused new modules, `tests.test_graph_documents`, `tests.test_project_workflow`, `tests.test_ui_layout`, `tests.test_dat_preamble`, and affected named graph cases in separate processes as needed. Stop expanding checks after required boundaries pass.
- [x] Capture APP_QSS normal/compact workbook/XY/mixed views, inspect pixels and fix clipping. Write readable usage in README, exact logs and changed-file report. Do not build an installer.

### Parent verification and final review

- [x] Compare production/test changes with the saved baseline, inspect all modified boundaries, ensure no unrelated edits.
- [x] Independently execute the three-file workflow and inspect SVG/PNG and screenshots; retain artifacts in the task workspace.
- [x] Fresh read-only explorer reviews requested behavior, data/reference validation, active-owner routing, legacy compatibility and rollback. Assess findings and send warranted corrections to an implementation worker.
- [x] Inspect final focused green evidence and report outcome, workers used, tests and real limitations in concise Chinese.
