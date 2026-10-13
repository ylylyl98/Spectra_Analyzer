# Independent Graph Documents Implementation Plan

> **For agentic workers:** Use the executing-plans task/checkpoint discipline. The user's AGENTS.md selects a fresh implementation worker and parent review; use the existing working checkout and preserve all pre-existing changes. Do not create commits, worktrees, or run packaging for this task.

**Goal:** Retain independently editable formal graph documents with tabbed/tiled views and close/reopen behavior.

**Architecture:** A native QMdiArea hosts one PlotPage per `(dataset_id, graph_id)`. MainWindow binds the selected page to the existing shared sidebar and controls; explicit graph ownership drives snapshots, invalidation, and command routing. Analysis remains one linked scientific view.

**Tech Stack:** Python 3.14, PySide6 6.11.2, existing NumPy/Matplotlib modules.

**Spec:** `docs/superpowers/specs/2026-10-12-graph-documents-design.md`

## Global constraints

- Preserve pre-existing modified and untracked files; baseline copies are at `C:\Users\yanli\AppData\Local\Temp\spectra-graph-documents-baseline-ps4x1aee`.
- Keep project schemas v1/v2, ordinary sidecars, embedded arrays, existing scientific state and frozen comparisons compatible.
- Native Qt components, existing styling, no dependency or general theme changes.
- Workbook/Sheet, arbitrary XY import, cross-file curve graphs, floating desktop windows, and persisted window geometry are subsequent work.
- Use isolated settings and real behavior tests. Do not weaken old assertions to accommodate ownership errors.

## Task 1: Graph documents, routing, and lifecycle

**Files**

- Create `spectra_app/graph_documents.py` and `tests/test_graph_documents.py`.
- Modify `spectra_app/main_window.py`; a small `spectra_app/plot_page.py` adjustment is allowed if required for activation routing.
- Update outer Plot-tab selection in `tests/test_edit_history.py`, `tests/test_origin_interaction.py`, `tests/test_named_graphs_workflow.py`, `tests/test_render_event_performance.py`, `tests/test_ui_layout.py`.
- Update retained-page semantics in `tests/test_formal_plot_workflow.py` and user instructions in `README.md`.
- Report any additional necessary write path before changing it.

**Interfaces and ownership**

The document key is `tuple[str, str]` with dataset ID followed by graph ID. Provide a MainWindow `open_graph_document(dataset_id: str | None = None, graph_id: str | None = None) -> PlotPage` action path. It selects a valid owner using existing editor/busy guards, then opens/activates its keyed page in the Plot document area without data reads or recomputation.

The implementation may select private helper names, but the real UI and tests must use the same production opening/activation path. Keep `self.plot_page` as the selected page and expose the Plot container separately. MDI close callbacks must close views only; domain deletion continues through existing graph/dataset removal paths.

- [x] Add focused tests first. A representative observable contract is:

```python
first = window.open_graph_document(first_dataset_id, 'default')
second = window.open_graph_document(second_dataset_id, 'default')
self.assertIsNot(first, second)
self.assertIs(window.plot_page, second)
self.assertIsNotNone(first.rendered_page)
self.assertEqual(first.rendered_page.svg_bytes, first_bytes)
# Switch back through the actual document selector, edit, and export.
# Verify the first dataset/graph changed and the second retained its settings.
```

- [x] Run `python -m unittest tests.test_graph_documents -v` with the repository `.venv` interpreter and record the expected missing-behavior failure before production changes.
- [x] Implement native tabs/subwindows, explicit Open/Close/Tile/Tabbed commands, source+graph labels, empty state, and keyed cached pages. Keep Qt focus and keyboard navigation usable; use existing palette and layout policies.
- [x] Integrate graph/dataset activation and shared sidebar reparenting. Finish outgoing editors before changing owner; roll back selection when switching is rejected. Rebind all active-page commands, zoom, dirty detection, and batch status. Direct actions from inactive tiles must route to their own owner.
- [x] Separate formal render ownership from navigation generation. Use graph-specific invalidation and existing detached snapshots; keep shared-science invalidation and stale/removed-result rejection. Render dirty visible tiles serially without moving shared controls or changing the selected owner.
- [x] Integrate close/reopen, graph rename/removal, dataset removal, undo-selected graph reopening, New/Open/reset and failed installation rollback. Clean detached editor/page objects without deleting user graph data on view close.
- [x] Update existing tests only where the accepted container/retention contract changes; preserve all meaningful assertions. Document commands and explain that view close differs from graph removal.
- [x] Run new tests plus relevant existing cases from named-graphs, formal-plot, UI-layout, origin-interaction, edit-history, render-event and project-workflow modules. Expand only to cover a changed path or diagnosed failure; do not run the entire suite.
- [x] Produce offscreen screenshots of two graph tabs and tiled graphs at normal/compact size; record any unverified native desktop behavior. Confirm empty/busy states and that closed views can reopen without changing project content.
- [x] Return exact changed files, RED/GREEN commands and results, test count, screenshots, lifecycle/ownership decisions and remaining limitations. Parent compares actual changes against the saved baseline, performs focused verification and independent review before completion.


## Completed verification

Implementation and parent review completed on 2026-10-12. The worker changed the ten scoped production/README/test files; baseline hashes confirmed no unrelated changes. The parent separately owns this plan and its design document.

Successful focused runs: graph documents 16/16; interaction/history 39/39; rendering 16/16; UI/project workflow 37/37; import/ownership 5/5; import completion/shortcut 2/2. The parent independently checked the actual diff, all four screenshots, and three focused runs of 5, 3, and 4 cases. The latest four-case run includes New → Import, source-free project restore, same-ID two-window routing, and close/reopen.

Review and regression corrections covered stale peak-label exports, outgoing sidecar saves, failed project installation rollback, native input-filter ownership/lifetime, hidden-window render scheduling, and trusted import-result activation. The input filter is a dedicated QObject; native QMdiArea eventFilter is not overridden. The exact previously crashing UI/project ordering passes after these corrections. Earlier interrupted/failing diagnostic runs are not treated as passing verification.

Durable screenshots, final scoped diff, successful logs and the user-facing verification record are under `C:\Users\yanli\Documents\Codex\ScientificAnalysis\artifacts\graph-documents`. Native desktop interaction, dark-theme support, a full repository suite and packaging were not validated. Compact tiled previews are small; tabbed mode remains the default.
