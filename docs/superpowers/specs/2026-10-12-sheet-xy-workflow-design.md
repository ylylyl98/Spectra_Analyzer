# Read-only workbooks and cross-file XY comparison

## Approved outcome

The user approved the next daily workflow: import unlike numeric files, inspect/copy their values, choose each file's X and one or more Y columns, and add those curves to one retained comparison graph. Naming, visibility, color, line width, legend, project save/reopen and SVG/PNG export complete the workflow. Existing DAT/Gate science and free graph windows remain available.

No extra approval is needed. No spreadsheet editing, formulas, arbitrary analysis/fitting, normalization, offsets, or release packaging belongs to this phase.

## Data ownership

Use native PySide6/Qt Widgets, NumPy and Matplotlib already installed; add no dependency. Keep DatasetSession for spectrum matrices. Add plain Python Workbook, Sheet, comparison and curve records alongside it in Workspace. A Workbook groups ordered Sheets. Each workbook has one MDI page with Sheet tabs. Each comparison has its own MDI page. DAT import creates a lightweight workbook/Sheet referring to the dataset. An ordinary XY multi-file import creates one workbook containing one Sheet per file; subsequent imports may add to a selected workbook. Allow naming workbooks, Sheets and comparisons, and removing ordinary Sheets/workbooks with their dependent curves.

Numeric XY arrays retain file row order and independent X grids. An explicit Import XY action accepts DAT/TXT/CSV/TSV, optional one-line headers, comments, whitespace/comma/tab delimiters, numeric scientific notation and missing numeric values. Reject inconsistent row widths or nonnumeric body tokens with useful line information. Do not change CSV peak-seed drag/drop or existing heatmap DAT interpretation. Duplicate source imports must be deliberate/reused without silent array replacement.

DAT Sheets project base_cube/current_cube without duplicating their large arrays. Rows are energy samples and columns are analysis X followed by Gate signals. Raw means imported numeric values in the existing sorted analysis-axis order, not the literal source text order; label that distinction. Processed means the applied current_cube, never uncommitted processing controls. Ordinary XY offers raw data only. DAT table selection of a Gate column activates its dataset through existing guards, then uses the established Gate setter. Reverse Gate indication must not recursively select or change data.

Curves are live references to a Sheet, X/Y column indices, and raw/processed mode. They retain label, visibility, hex color and finite positive width. Different grids are never resampled. DAT processing refreshes processed curves. Removing a dataset/Sheet removes dependent curves; explain this in the removal UI. Replacing source axes/shape preserves valid mappings and removes invalid references deterministically. Source paths are provenance; saved projects must reopen from embedded arrays with files unavailable.

## Runtime and persistence contract

Foundation implementation publishes the exact domain API for integration. Keep existing heatmap runtime keys `(dataset_id, graph_id)`. Auxiliary keys are `('workbook', workbook_id, '')` and `('xy', comparison_id, '')`; tuple length prevents namespace collisions. Central helpers encode/decode these keys and layout identities. Use one graph_workspace layout for every document type; schema4 records use `{key: [...], open: bool, rect: [...] | null}`, with active and stacking as key arrays. Accept and upgrade legacy `{dataset_id, graph_id, open, rect}` heatmap records. Preserve versions 1, 2 AND 3 explicitly when introducing schema4.

Project v4 adds workbooks, Sheets and comparisons to explicit bounded JSON/NPY serialization. Validate IDs, membership, unique ownership, source references, array dimensionality, column bounds, modes, styles, typed document identities and open/active/stacking consistency. Include new data in snapshot canonicalization and runtime dirty stamps. XY-only projects must become modified and save/reopen correctly. Preserve existing atomic writes, no-pickle and archive resource limits.

Generalize GraphDocuments only at its page lifecycle/input roots and layout encoding. Optional external control/command roots must not require new pages to fake heatmap properties. Preserve its dedicated QObject input filter, document-coordinate geometry, close/reopen, free/tabbed/tile/cascade and park/restore. Keep MainWindow.plot_page as the scientific heatmap alias; active MDI identity separately routes document actions. A visible Workbook/XY document must never invoke hidden heatmap export/copy/delete commands. Capture/reset/install/rollback must retain all document kinds, including cached closed pages and XY-only workspaces.

## Interaction

Add a compact Data/Graphs navigator with Workbook -> Sheet entries and comparison graph entries, with Open/New/Remove/Rename actions. Keep established DAT navigation and scientific controls. Double-clicking a Sheet opens its workbook at that tab; double-clicking a comparison reopens its retained page.

Each Sheet uses QAbstractTableModel/QTableView, is read-only, supports cell/row/column ranges and Ctrl+C TSV copying. Offer X combo and checked Y list with selected columns visible, plus Raw/Processed for DAT, New comparison and Add to comparison. Remember mapping per Sheet; target the last selected existing comparison or explicit target combo so selecting a Sheet does not lose the intended graph. DAT users can select several Gate columns and also add the current Gate through a daily action.

Each XY page has an interactive Matplotlib canvas (pan/zoom/home), editable graph name/axis labels, curve list with source provenance, visibility, legend text, color, width, remove, and legend toggle. Use the same figure-building path for preview and SVG/PNG export; preserve gaps for nonfinite pairs. Exports must be proper SVG/PNG and not overwrite an existing file silently. Start with automatic colors and source/column or DAT/Gate names. Core controls must remain usable at 1100x720 and 1440x960; scroll compact inspectors if needed.

## Acceptance and verification

1. Import three files with different column orders/lengths. Independently choose their X/Y columns and add all to one graph. Verify actual plotted arrays without interpolation.
2. Hide one curve, rename another legend, change color/width, copy a table rectangle/row/column. Cells cannot be edited. Export SVG and PNG and verify content/dimensions.
3. Open DAT raw/processed Sheets; verify matrix orientation and Gate links. Apply processing and verify referenced processed curves update without changing raw ones.
4. Save XY-only and mixed projects, remove original sources, reopen: mappings, styles, data, names, open/closed state and layout survive. Versions 1/2/3 remain readable; malformed refs/arrays/mappings/styles are rejected before installation.
5. Switch/close/reopen/tile/tab all document types; actions target visible owners. Failed project installation restores actual previous pages and state. Source removal/reload leaves no dangling references.
6. Run focused new tests plus affected graph/project/DAT regressions in separate processes where useful. Inspect actual styled screenshots at normal/compact sizes and an independent reviewer report. Do not run full suite/build/installer/CI.
