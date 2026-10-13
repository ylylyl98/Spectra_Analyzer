# Workspace Shell Implementation Plan

**Goal:** Keep Project navigation accessible while Workbook, XY and heatmap actions follow their active window.

**Architecture:** Reuse the central GraphDocuments/Analysis tabs and page-owned operations. Separate the existing tree from DAT tabs, rehost DAT controls in a contextual native right dock, compact navigation actions and add a page-owned XY inspector toggle.

**Tech stack:** Existing Python, PySide6, Matplotlib and unittest; no new dependencies.

**Spec:** ../specs/2026-10-12-workspace-shell-design.md; ../../ui-spec/workspace-shell.md; ../../adr/ADR-workspace-shell.md.

**Authorization:** User explicitly said to proceed with these three improvements. One fresh implementation worker, inherited model/effort. No additional confirmation, commit or release build is requested. Parent owns review.

## Constraints

Repository C:/Users/yanli/Desktop/Spectra_Analyzer. Preserve preexisting dirty/untracked work. Baseline and logs in C:/Users/yanli/Documents/Codex/ScientificAnalysis/artifacts/workspace-shell. Do not modify models, schema6, fitting algorithms, dependencies or native title bars. Retain plot_tabs indices and existing editor/busy guards. Use current APP_QSS; no theme rebuild.

## One dependent implementation task

Expected production files: spectra_app/main_window.py, xy_plot_page.py; graph_documents.py only for Workspace wording if needed; workbook_page.py only for an observed current-Sheet command defect. Tests: new tests/test_workspace_shell.py plus minimal intentional updates to old layout expectations. Update README usage.

- [x] Add focused failing Qt integration tests for persistent tree/context visibility, the three-file comparison path, current-Sheet ownership, inspector draft retention, invalid-switch rejection and mixed save/reopen. Use isolated_settings; release synthetic Ctrl/Shift keys and capture Qt callback exceptions. Establish RED for missing behavior before changing production code.
- [x] Rehost the tree and detail tabs. _build_ui creates the tree placeholder and right detail dock; _build_data_navigator populates the placeholder after _build_graph_commands. Replace forced old sidebar widths in showEvent/changeEvent so the slim tree remains slim. Rename central visible Plot tab to Workspace while keeping indices 0/1.
- [x] Compact navigation through native controls and existing handlers; route explicit heatmap nodes safely and refresh their titles on existing graph/dataset mutations. Preserve tree state. Project and DAT-details View toggles preserve useful width and user visibility intent; use bounded QSettings only if needed for persisted shell preference.
- [x] Project right-dock visibility from the existing active-document boundary; do not alter underlying current-dataset identity for Workbook/XY focus. Add XY inspector toggle that reuses current widgets/selection/drafts. Retain existing guarded page operations and accurate target graph labels.
- [x] Run new tests and affected UI/workbook/graph-document/import/project cases. For old failing expectations, change only those superseded by the design; preserve original assertions on behavior. Use .venv/Scripts/python.exe -m unittest with QT_QPA_PLATFORM=offscreen. Choose exact adjacent tests where whole modules are excessive.
- [x] Render normal/compact/enlarged-font screenshots using Fusion, APP_QSS and Segoe UI; inspect and correct within scope. Report unsupported theme/native-window/high-DPI claims rather than expanding scope. Supply stable controls/APIs early, final changed files, baseline-relative diff, commands and full test output.
- [x] Parent reviews actual diff and output, independently verifies three-file comparison/source navigation and context/close/reopen/project-save ownership. Send concrete fixes to worker; stop when the approved scope is satisfied.

Status: implementation and parent review complete. 95 affected tests passed; all 8 shell tests passed again after the final native dock-menu guard. Four independent parent acceptance tests passed using a project saved by the original shell. Production-style normal/compact/15pt screenshots inspected. Evidence: calling workspace artifacts/workspace-shell/parent-review.md. No full suite or release build performed.
