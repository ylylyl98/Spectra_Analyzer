# Persistent project navigation and contextual workspace

## Approved outcome and scope

The user explicitly approved implementing three next improvements: persistent project tree, central Workspace, and operations organized around the current window. A user can import three files with different X/Y mappings, compare them, return to a source Sheet and use another document without losing navigation or applying an action to a hidden owner.

Current state: MainWindow has a roughly 400px shared left Data/Analysis/View/Data-Graphs sidebar; central Analysis/Plot tabs; existing GraphDocuments owns Workbook, XY and heatmap windows. SheetPage and XYPlotPage already own safe local commands. The new structure reuses these owners and guards.

This is one medium shell migration. The approved first step does not include new fitting algorithms, editable spreadsheets, new data/project formats, replacement of the Analysis view, theme infrastructure, custom window chrome or packaging.

## Layout

1. Keep a resizable Project tree panel persistently to the left of the central content. Default near 240 logical pixels, shrinkable for compact windows. It is independent of the detail tabs and remains visible in Analysis and all Workspace document types. Provide a discoverable View toggle to hide/show it and restore a useful prior width.
2. Keep the existing outer tab indices and ownership paths: Analysis at 0 and GraphDocuments at 1. Rename the second visible tab to Workspace. Native free/tabbed/tile/cascade, close/reopen, rectangles and saved active owner remain unchanged.
3. Rehost the existing _sidebar_tabs intact in an optional right QDockWidget, preserving all existing DAT widgets and signals. Show the appropriate Analysis/heatmap context. Automatically hide DAT details for Workbook/XY or an empty Workspace; automatic hides must not overwrite the user's requested DAT detail visibility. A View toggle restores access. Do not make three wide columns mandatory.
4. Compact Project actions into a short header/menu and existing context menu. Import, New comparison, Compare selected Sheets, Merge Workbooks, Open, Rename and Remove remain discoverable and invoke existing guarded handlers. Avoid the old long instruction paragraph and many stacked full-width buttons consuming tree height.
5. Project navigation includes existing Workbook/Sheet/XY rows and UI-only heatmap graph entries so formal graphs remain reachable when the DAT panel is closed. Preserve existing IDs; use explicit node kinds and routing rather than treating unknown kinds as XY. Source paths are tooltips/context, not mandatory wide labels. Reuse existing heatmap graph operations where suitable; any action not valid for a node is disabled.

## Current-window operations

Retain page-owned Workbook and XY controls rather than adding a parallel generic command dispatcher. Workbook's selected Sheet owns New comparison, Add to target, Save snapshot and copying. Target graph remains named in its selector. XY owns Add data, curve/source/export controls and gains a visible native toggle to collapse/restore its inspector without replacing widgets, changing curve selection or committing/discarding drafts. Heatmap commands remain in graph_command_host and the right-side existing controls. The optional details toggle is clearly DAT-specific; it must not expose stale DAT commands over an XY/Workbook window.

Switching to a hidden/inactive document must still pass _finish_current_document, _activate_document and existing busy/editor guards. Rejected switches restore the visible context and leave the previously active document's commands in charge. Changing only panel visibility must not commit invalid inputs, dirty scientific/project content or clear a draft. Reopening a closed document reuses its retained page and controls.

## State and compatibility

Do not change Workbook, Sheet, comparison or schema6 serialization. Shell panel preference/size is application-level state: follow existing QSettings patterns if persisted, validate and clamp restored values, and keep visibility intent separate from automatic contextual hiding. Existing project MDI layout retains its meaning. New/Open/project-install rollback must re-project the correct shell context. Never connect the same page commands again merely when changing visibility.

Use Qt layouts, splitters/dock widgets, native menus/buttons and existing APP_QSS/fonts. Preserve accessibility names or migrate them deliberately; keyboard navigation/copy and focus ownership remain intact. No new custom painting, per-widget styling, dependency or theme claims.

## Observable acceptance

- Project tree stays available across Analysis, two Workbooks, XY and heatmap activation; hide/show restores width. It can open a heatmap while details are closed.
- Three unlike XY inputs import with per-file mappings into separate Workbooks and one comparison. Source navigation returns to the correct Sheet. Current-Sheet commands mutate the selected owner exactly once.
- Workbook/XY activate with DAT details/heatmap commands hidden. DAT contexts restore user's detail visibility and correct editor ownership.
- XY inspector collapse/restore preserves selected curves, unfinished valid/invalid fields, plot values, and current graph. Invalid drafts still block another document, Analysis and close as before.
- Free/tabbed/tile/cascade and close/reopen retain the same pages. Mixed project save/reopen restores document IDs/layout and command owners; shell-only actions do not dirty project content.
- At normal and compact sizes, and at enlarged application font, essential actions and useful content remain reachable; horizontal/vertical scrolling handles remaining dense content. Inspect production-style screenshots.
- Read-only review + focused Qt integration checks are sufficient. Do not run full suite, package, installer or CI. Existing known background cancellation/NaN derivative issues remain out of scope unless this migration causes a new failure.

## Evidence and ownership

Explorer inspect_workspace_shell verified construction order (_build_ui → _build_graph_commands → _build_data_navigator), old width overrides in showEvent/changeEvent, shared editor migration in _on_plot_tab_changed, and existing page-owned command surfaces. Main agent owns design/review; one worker owns implementation/testing. Baseline: calling workspace artifacts/workspace-shell/baseline.json. Preserve all preexisting changes.
