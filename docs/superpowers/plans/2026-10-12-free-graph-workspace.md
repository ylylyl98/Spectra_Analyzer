# Free Graph Workspace Implementation Plan

> **For agentic workers:** Use executing-plans with the user's selected fresh-worker workflow. Work in the current checkout; preserve all pre-existing changes. Do not commit, package, or create another checkout.

**Goal:** Provide useful free graph windows with shared commands and saved layouts.

**Architecture:** Extend GraphDocuments for native window arrangement and layout snapshots. Relocate each active PlotPage command bar into a shared MainWindow host. Store validated UI metadata in Workspace/project v3 while retaining v1/v2 readers.

**Tech Stack:** Python 3.14, PySide6 6.11.2, current NumPy/Matplotlib.

**Spec:** `docs/superpowers/specs/2026-10-12-free-graph-workspace-design.md`

## Global constraints

- Approved scope is free graph workspace, shared commands and saved layouts; exclude Workbook/XY/floating desktop windows.
- Preserve baseline files at `C:\Users\yanli\AppData\Local\Temp\spectra-free-windows-baseline-q3tarkc2`.
- Preserve existing science, ownership, save/discard, invalid-edit and busy guards.
- Do not override QMdiArea.eventFilter on arbitrary descendants.
- Native Qt and existing styling only; no new dependencies.
- Only focused tests and offscreen visual verification; no full suite or packaging.

## Task 1: Native free layout and compact graph views

**Files:** `spectra_app/graph_documents.py`, `spectra_app/plot_page.py`, graph/UI portions of `spectra_app/main_window.py`, `tests/test_graph_documents.py`; narrow contract updates in `tests/test_formal_plot_workflow.py` and `tests/test_ui_layout.py`.

**Interfaces:** Preserve existing document keys, open/close and active-page semantics. Add a free-view action and cascade action alongside tile/tab actions. Use a detached command-bar widget analogous to the existing control-scroll handoff; preserve standalone PlotPage layout. Prefer a shared host above plot_tabs so GraphDocuments remains the existing Plot tab child.

- [x] Add focused failing tests using real GraphDocuments/MainWindow. Open at least three graph documents through `open_graph_document`, set distinct overlapping rectangles, switch tabs/free, and assert the same owners and rectangles return. Close/reopen a graph and assert it retains its rectangle. Exercise actual native mouse movement when supported.
- [x] Run the new cases with `.venv\Scripts\python.exe -m unittest tests.test_graph_documents -v` under isolated settings/offscreen Qt, saving RED evidence.
- [x] Implement free/cascade/tile/tab actions, default free mode, staggered new windows and preserved normal rectangles. Keep manual layout stable after main-window resize.
- [x] Move active-page controls to the shared host via `_bind_plot_page`; retain all existing page signals. Hide it in Analysis/empty states and preserve invalid-owner activation guards. Update only tests whose intended control ancestry changes.
- [x] Run focused graph and affected toolbar/UI cases and inspect screenshots at normal and compact sizes.

## Task 2: Project layout persistence and restoration

**Files:** `spectra_app/dataset_state.py`, `spectra_app/project_io.py`, project capture/marker/install/rollback portions of `spectra_app/main_window.py`, `spectra_app/graph_documents.py`, `tests/test_project_io.py`, `tests/test_project_workflow.py`, graph persistence tests, README.

**Interfaces:** `Workspace.graph_workspace` holds detached plain JSON-compatible metadata, or `None` for legacy/uninitialized layout; an explicit record with no open documents means all views are closed. GraphDocuments exports/restores metadata by stable `(dataset_id, graph_id)`. `_capture_project_workspace` and `_project_marker_workspace` capture it; `runtime_workspace_stamp` accounts for it. Schema v3 validates the record and reads v1/v2 with default metadata.

- [x] Add failing project-boundary tests: save multiple free rectangles/open states, load into a new MainWindow with no source files and assert layout and active owner; include all-closed, old-v2 default, invalid metadata and failed-install rollback.
- [x] Implement validated v3 metadata encoding/decoding and inclusion in both persisted and runtime digests. Preserve existing array safety and v1/v2 migration.
- [x] Restore only saved open documents after installing the project; restore closed-document geometry on later opening. Prune removed identities. Clamp unreachable saved positions. Rebind shared commands after success/rollback.
- [x] Verify clean baseline after load/save and dirty state after user layout changes. Keep transient restore geometry events from corrupting the saved normal rectangles.
- [x] Run focused project I/O/workflow and document/toolbar regressions, then update README with user-visible actions and format compatibility.
- [x] Return exact files changed, RED/GREEN commands and logs, screenshots, and remaining limits. Parent reviews actual baseline-relative diff and independently reruns the critical behavior boundary; independent review follows before completion.


## Completed verification

Completed on 2026-10-12. Worker changed the twelve scoped source/test/README files; parent confirmed the actual baseline-relative diff and no unrelated changes. Final focused runs passed: graph documents 22, project I/O and named state 28, project workflow 29, UI layout 12, and affected formal workflow 2 (93 total). Parent independently ran two integration checks on the final implementation, both passing.

Independent review identified viewport scrolling corrupting saved coordinates and Plot commands mismatching a restored Analysis owner; both were reproduced and fixed at their owners. Additional checks fixed all-closed rollback initialization and tabbed maximization. Parent inspected normal and compact free-window screenshots and the corrected tabbed screenshot.

Durable evidence: `C:\Users\yanli\Documents\Codex\ScientificAnalysis\artifacts\free-graph-workspace`. The abandoned combined test run is excluded; final module runs completed independently. Qt offscreen validation only, with no manual desktop, full-suite, packaging or release validation. New project files use v3 and retain v1/v2 read compatibility.
