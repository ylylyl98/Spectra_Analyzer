# Import → plot → compare verification checklist

The approved design is `../specs/2026-10-12-import-compare-flow-design.md`.

- [x] Add focused tests and observe missing-feature failures before implementation.
- [x] Stage the complete batch; validate destination and all mappings before mutation.
- [x] Add batch setup dialog with numeric preview and independent per-file mappings.
- [x] Configure ten matching files once and create one graph containing ten real lines.
- [x] Preserve independent grids and multi-Y mappings across three unlike files.
- [x] Reuse exact unique names across reordered columns; report incompatible files.
- [x] Distinguish headerless columns from literal named “Column N” headers.
- [x] Preview and validate actual retained Sheets for duplicate source paths.
- [x] Verify cancellation and invalid input/mappings leave no partial workspace.
- [x] Verify table header Ctrl/Shift selection, Ctrl+Space and Set as X via Qt events.
- [x] Keep arbitrary cell/row copy selection independent of plotting mappings.
- [x] Collapse fallback X/Y controls and preserve DAT Gate/raw/applied behavior.
- [x] Append without changing source active document, selection, viewport or focus.
- [x] Validate pending target edits before mutation; keep closed targets closed.
- [x] Save/reopen mappings and comparison arrays using the existing project format.
- [x] Run bounded existing workbook, XY, parser and project regression suites.
- [x] Run focused legacy CSV drop/seed regressions.
- [x] Render and inspect compact and normal production-style Qt screenshots.
- [x] Review actual diff against the provided pre-task snapshot.

Validation: 35 tests pass across `tests.test_import_compare_workflow`,
`tests.test_workbook_workflow`, `tests.test_xy_plot_workflow`, `tests.test_xy_io`,
and `tests.test_workbook_project`. Two selected legacy CSV tests pass separately.
The parent additionally ran two independent real modal-dialog checks. Evidence
is in the task workspace's `artifacts/import-compare-flow/` directory.

Offscreen rendering verifies widget layout and Qt focus state, not native desktop
window chrome, dark mode or monitor scaling. No full suite, packaging or CI run
was required for this bounded workstream.
