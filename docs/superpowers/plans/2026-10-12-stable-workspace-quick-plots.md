# Stable Workspace and Quick Plots Implementation Plan

**Goal:** Keep the application shell stable while switching documents, make label edits calm and predictable, and create common plots directly from Workbook columns.

**Architecture:** Retain Qt Widgets, the existing MDI document controller, Sheet mappings, and current render/export paths. Stabilize the shell first, then add curve styles and plotting commands through the same document and project state. Execute sequentially with focused implementation workers; the parent reviews actual incremental diffs and observable evidence.

**Tech Stack:** Python, PySide6, Matplotlib, unittest, existing native project serialization.

**Spec:** User-approved four-part chat plan immediately preceding “进行”: stable shell; consistent graph/title/axis/colorbar labels; commit-on-finish text editing with retained preview; icon-and-text Line, Scatter, Line + Symbol, Heatmap, and Add to commands.

## Global Constraints

- Preserve all pre-existing dirty and untracked work. Baseline is `C:/Users/yanli/Documents/Codex/ScientificAnalysis/artifacts/stable-workspace-quick-plots-20261012/before`.
- No source-data transformation from label or plot-style changes. Preserve automatic import, first-X/all-other-Y mapping, duplicate curve handling, and cross-file curves with independent grids.
- Keep free movable subwindows; no forced tabs, tiling, cascading, or window geometry resets on activation.
- Property panel visibility changes only by user action. Keep user widths, zoom, scroll, and drafts across document activation.
- Fixed heatmap physical frame and fonts remain unchanged; only outer page can grow for long annotations.
- Preserve older project appearance and user-customized strings. New persistent fields must validate input and have backward-compatible defaults.
- No new GUI framework or third-party dependency. No full suite, packaging, installer, release, commit, or restart/termination of a live user app.

## Task 1 — Stable shell and calm annotation editing

**Ownership:** `spectra_app/main_window.py`, `spectra_app/plot_page.py`, minimal adjacent shell/XY-host files if required, focused shell/annotation tests, and README section for this behavior.

- [x] Add a failing integration assertion comparing central MDI geometry and document rectangles while activating Workbook, Heatmap, and XY documents; explicitly test user-hidden Properties stays hidden.
- [x] Replace contextual whole-panel hide/show with a stable Properties dock and content switching. Show meaningful Workbook/XY/empty context; do not expose stale DAT controls as if editing the current object. Keep the command host footprint stable. Move verbose page/export metadata into Properties. Preserve explicit dock actions and editor flush/guard behavior.
- [x] Add a failing test on the screenshot's editable signal combo: typing must keep the last preview and its size; Enter/focus-out should commit once; source data must stay unchanged.
- [x] Remove duplicate eager invalidation, keep the last successful preview during preparation, and commit drafts on Enter/focus-out. Export/save/close/owner changes must flush drafts; stale preview bytes must never be exported as current. Guard interaction using stale geometry and preserve ownership/revision checks.
- [x] Clarify heatmap UI terminology: Graph name, Plot title, Axis label, Colorbar label, Auto from data. Preserve scientifically distinct presets and free text.
- [x] Run focused shell, annotation, preview lifecycle, and project restore tests using `.venv/Scripts/python.exe -m unittest ... -v`; record commands/results, task diff, and changed files under the artifact directory.

## Task 2 — Quick plots, XY styles, and consistent naming

**Ownership:** `spectra_app/workbook_page.py`, `spectra_app/workbook_state.py`, `spectra_app/xy_plot_page.py`, `spectra_app/main_window.py`, serialization and heatmap graph selection files only where the approved plot behavior needs them, local SVG icons, focused tests, README.

- [x] Add failing behavior tests for real column selection -> Line/Scatter/Line + Symbol; selected Gate columns -> Heatmap; Add to existing comparison preserving target/source/deduplication.
- [x] Add Qt-native icon + short-text commands and equivalent column-header context menu actions; no emoji/glyph production icons. Honor Sheet X/Y mapping, using all Ys when no explicit selection. Do not interpret copy-only cell selections as a plot row range. Keep command footprint stable and show chosen columns/target.
- [x] Implement curve plot styles through state validation, creation, renderer, curve manager, native project save/load, and SVG/PNG export. Preserve independent X arrays and display normalization/offset/range logic. Existing curves default to Line.
- [x] Separate XY Graph name (tree/window only) and optional Plot title. Old projects missing the new field keep their prior graph title; new graph names do not force graph redraw.
- [x] Heatmap command must respect available DAT Original/Processed data and selected Gate columns without modifying the scientific source. Disable or explain unsupported ordinary XY heatmaps rather than guessing physical Gate coordinates. Preserve closed/reopened graph and project state.
- [x] Run focused workbook, curve style/render, save/load/export, subset heatmap, and shell geometry regression tests; record task-specific diff/evidence.

## Parent review and completion

- [x] Review actual differences against this turn's baseline and each worker boundary; no unrelated rewrites.
- [x] Inspect focused logs and independently exercise the visible shell/plot workflow with isolated settings and offscreen Qt. Inspect screenshots at useful window sizes using the application stylesheet/font.
- [x] Send any material correction back to a worker; interrupt completed children.
- [x] Report delivered behavior, worker roles, verified tests, and remaining limitations concisely. No claim that a live running app has been reloaded.
