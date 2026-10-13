# File-owned workbooks and current result Sheets

## Approved outcome and scope

User approved the earlier proposal verbatim: default one source file per Workbook; Original, Processed, Fit parameters and Fit curves appear as appropriate inside it; manual Workbook merge remains available; graphs independently reference multiple Workbooks. User then explicitly chose: update current result Sheets by default, with optional named snapshots. This extends the existing Workbook workflow and supersedes the prior shared-new-Workbook import default. No further approval is needed for the implementation described here.

The coherent outcome is being able to locate a file's original data, current scientific outputs and retained snapshots together without exporting intermediate CSV files. This is a Medium change across existing model, Qt UI and persistence boundaries. Existing analytical algorithms and numeric results remain authoritative; this task organizes and presents them. Do not introduce algorithms, dependencies, editable spreadsheet/formula behavior, OPJU support or release packaging.

## Repository constraints and approach

`workbook_state.py` owns Workbook/Sheet/Curve identity and original DAT projections. `DatasetSession` already persists applied cubes, per-gate FitResult records and export status rows. `workbook_page.py` provides read-only virtual tables. `main_window.py` owns import and successful scientific mutation boundaries; `project_io.py` has strict schema5 JSON/NPY validation. The accepted ADR below selects live result projections plus explicit detached snapshots.

Maintain unique Sheet ownership and Curve IDs. Keep existing dictionaries and stable source references; a Workbook merge changes memberships only. Do not copy large DAT arrays merely to create Original or Processed views. Source paths remain provenance, never a restore dependency. Existing grouped workbooks/names/IDs/layouts are retained on loading; do not automatically split old grouped books.

## Source and current-result identities

Extend Sheet with a persisted role (`original`, `processed`, `fit_parameters`, `fit_curves`, `snapshot`) and optional `fit_gate_idx`. Original XY stays numeric; DAT roles reference the owning dataset. Current result uniqueness is `(dataset_id, role, fit_gate_idx)`; only fit_curves has a gate index. The existing `mode` field stays part of Curve compatibility and is fixed to the relevant original/applied view in new Sheet UI. Legacy API paths that explicitly request raw/processed must canonicalize curve sources to the matching Sheet without changing values.

Original is always present. Processed appears after an applied processing result or when needed by a migrated processed reference; once present it remains a stable view of current applied data, including a return to original processing. Pending, failed or cancelled processing never replaces it. Existing user-renamed Sheets retain their names; new roles use descriptive defaults.

Fit parameters is a mixed read-only table built from full-precision retained FitResult numbers and actual status records. Include Gate, model/status, peak identity and the available center/amplitude/width/baseline/RMSE/R-squared values; never parse rounded export strings as successful parameter values. Mark nonnumeric columns as unavailable for X/Y plotting. Table inspection and TSV copying support text cells. Do not invent observed fitting Y, residuals, smoothing history or uncertainties that FitResult does not retain.

Fit curves uses one stable Sheet per fitted Gate. Use that result's own X array, total fitted Y and components; label components consistently with the existing component-plus-baseline plot. Different Gate ranges are independent. Fixed per-Sheet Gate identity prevents another UI Gate selection from retargeting a stored graph. Refit updates the same Sheet. On result clear/invalidation retain an empty current-result Sheet with clear no-result feedback and stable IDs so graphs can recover on refit. Preserve column identity for existing component references: missing components produce unavailable/empty values rather than shifting another component into their index. New component columns may append; original table values must never become stale success data.

Synchronize projections after successful single/batch fitting, Clear Fit, selective peak-fit invalidation, applied-processing/axis replacement, dataset reload, and project installation. Capture active controller state into its DatasetSession before projection if that mutation path uses mirrored runtime fields. Retained per-gate results are authoritative; clearing a transient current-fit pointer must not erase valid per-gate outputs. Refresh open tables/graphs only at committed mutation boundaries, preserving pending graph drafts and other document state. Dataset removal prunes its live projections/references, while detached snapshots remain usable.

## Detached snapshots

Provide `Save snapshot…` on a Sheet. Ask for a nonempty name; cancel makes no mutation. Materialize only at this explicit action, copy values, mapping and provenance into a new standalone read-only snapshot Sheet in the same Workbook, then open it. It must remain unchanged across refit, processing, Clear Fit, source deletion and source dataset removal. Empty current results cannot be snapshotted.

Use the existing bounded numeric NPY storage for numeric snapshot columns. For mixed parameter snapshots, store text columns separately as strictly validated JSON lists keyed by column index; never save object arrays or enable pickle. Suggested minimal Sheet extension is `text_columns` (empty for ordinary/live Sheets) and a frozen `snapshot_note` describing source file/Sheet/role, capture time, relevant applied processing and available fit context. Historical IDs/paths in that note are provenance rather than required live references. Snapshot kind is distinct from imported XY, so duplicate-source lookup cannot mistake an output for an original import. Numeric plotting selects only numeric columns and never coerces model/status strings.

## Import, merge and interaction

The import dialog always defaults to `One Workbook per file`, regardless of the selected navigator item. Also offer an explicit single new Workbook and explicit existing Workbook to preserve intentional grouping. Prevalidate the entire batch before mutation. New per-file Workbooks use source filenames and an Original Sheet; reused sources keep their existing owner and embedded values. Do not create empty duplicate Workbooks. `import_xy_files` retains a single Workbook return value, documented as the first affected owner; add an explicit grouping option rather than returning different types. Import-and-compare still creates one graph from every configured Sheet, even when their Workbooks differ.

Navigator Workbook multiselection exposes `Merge Workbooks…`. Choose one selected Workbook as destination. Require project idle, validate all selected IDs and pending relevant editors before moving anything, then retain destination ID/name/geometry, append source Sheet IDs in deterministic project order, and retire empty source Workbooks/documents. Never call destructive Sheet removal for moved members. Duplicate display names are legal; source/projection provenance distinguishes them. Cancel and failed preflight leave ownership, curves and documents unchanged. Refresh owner labels in comparison managers and the project tree after merge.

Workbook tabs and navigator show role-appropriate names. Original/Processed tabs replace the raw/processed switch as the primary UI. Parameter text columns must not activate DAT Gate-column navigation; original/processed Gate signals keep existing linkage. Fit-curves provenance clearly identifies its Gate. Snapshot provenance is plain text and remains informative if its source no longer exists. Reuse native Qt layouts, read-only model/view, menus and dialogs.

## Persistence and migration

Write schema6; accept schemas1-5 explicitly. Apply legacy Sheet defaults/migration before workbook validation. Preserve legacy source Sheet IDs, each Curve ID/order/style/offset, grouped ownership and layouts. Create the required Processed role and remap legacy processed Curve references to it while preserving actual arrays. Do not recompute fits during restore. New-format role/gate/text-column/snapshot metadata must be strictly validated, including impossible combinations, duplicate projection identities, wrong text lengths/indices, illegal numeric mappings and missing dataset references. Preserve atomic writes, resource bounds, archive member accounting and source-free restore.

The parent supplied a genuine unmodified-writer schema5 fixture in `C:/Users/yanli/Documents/Codex/ScientificAnalysis/artifacts/workbook-organization/legacy-v5-original-writer.spectra`; its source files are already gone. IDs and expected prior layout are adjacent in `legacy-v5-identities.json`. This is an independent migration proof, not a current writer with a patched version number.

## Acceptance and verification

1. Batch import unlike XY column orders/grids into separate named Workbooks by default; explicit grouping still works; reuse/cancel/invalid batch are atomic; compare includes every input.
2. DAT Original/Processed live views retain original arrays and update only on committed operations; raw/processed comparison references resolve the intended data.
3. Successful single and batch fits expose full-precision parameters and per-Gate curves with different grids. Refit replaces values without proliferating Sheets; clear/selective invalidation removes stale values from open views/graphs and later refit repopulates them.
4. Snapshot numeric and mixed result Sheets through the UI; subsequent processing/clear/removal does not modify values or provenance; only numeric columns plot; text TSV copy is readable.
5. Merge mixed Workbooks with open/closed documents; Sheet/Curve IDs, graph values/styles and target layout survive; cancellation/preflight failures do not mutate; removing one DAT later preserves other sources and snapshots.
6. Source-free schema6 roundtrip and genuine schema5 migration retain identities/grouping/layouts and results. Malformed new metadata is rejected before project installation. Existing older schema tests remain active.
7. Run focused new and affected workbook/import/comparison/project/background-analysis/edit-history tests; no full suite/package/installer/CI. Inspect normal and compact styled screenshots. Parent reviews actual baseline-relative diff and runs an independent end-to-end acceptance check.
