# Free graph workspace

## Approved outcome

The user approved the proposed next step: freely movable, resizable, overlapping graph subwindows, explicit arrangement actions, shared graph commands, and project-persisted layout. Extend the existing graph-document implementation rather than replace the application shell. Default new workspaces to free windows. Workbook/Sheet, arbitrary XY import/overlays and windows outside the application remain subsequent work.

## Interaction and ownership

- Use native QMdiArea/QMdiSubWindow behavior, without a window-count or two-column limit. Open additional windows at staggered, usable positions.
- Provide Free Windows, Cascade Graphs, Tile Graphs and Tabbed View actions. Cascade/tile arrange once; subsequent movement is unrestricted. Checked actions communicate free versus tabbed mode.
- Keep normal window rectangles while using tabbed/maximized views. Returning from tabs restores the layout. Close/reopen retains that document's rectangle. Resizing the main window must not continually retile.
- Store rectangles in stable document coordinates. Native viewport scrolling is not a window move; subsequent dragging while scrolled must still update the same coordinate system. Tabbed mode displays the active page maximized in its tab.
- Native title bars provide move, resize, maximize/restore and close. Keep Qt keyboard activation. Clamp restored layouts so a title bar remains reachable after a smaller viewport or changed display scale.
- Place existing per-page commands/dimensions/batch status in a shared command host above the graph work area. Reparent the active page's command widget, preserving existing signals and page ownership. Hide the host in Analysis and when all graph views are closed. Standalone PlotPage retains its existing controls.
- Retain the shared property sidebar. Clicking a graph selects its correct dataset/graph before edits, annotations, zoom, copy or export. Preserve busy/invalid-edit rejection and rollback to the prior active owner. Do not override QMdiArea.eventFilter to filter arbitrary controls; retain the existing dedicated QObject filter.

## Persistence

Add bounded, explicitly validated graph workspace metadata to Workspace and project schema v3. Continue reading v1 and v2 projects and initialize their workspace in free mode. New schema metadata records the mode, document identifiers, open/closed state and normal rectangles; retain active open document and stacking order when practical within the same record. Preserve graph scientific state, embedded arrays and source-free loading. Do not put Qt objects or opaque pickle data into project files.

Only existing dataset/graph identities may restore. Reject malformed types, unsupported modes, duplicate identities or unreasonable dimensions at the serialization boundary; handle well-formed offscreen rectangles by clamping to the current viewport. Empty projects and all-closed graph workspaces are valid. Removed documents must be pruned. Restore the saved open set without accidentally opening all project graphs or reopening an explicitly closed last view.

Layout changes participate in project dirty detection and save snapshots. Successful save/open establishes a clean baseline; failed project installation restores the previous document objects, layout, selected owner and command host. Snapshot data must be detached from live mutation. Existing scientific-job and save/discard guards remain authoritative.

The saved Analysis selection may differ from the last active open graph. When such a project replaces a currently visible Plot workspace, return to Analysis to preserve the saved selection and avoid binding visible graph commands to another owner. A later explicit Plot/graph selection uses the normal guarded activation flow. No additional persistent outer-tab field is needed.

## Scope and verification

Reuse existing application styling, native controls and current dependencies. Do not redesign the theme system. No commits, packaging or full-suite execution for this feature. Existing working-tree changes are user-owned and must be preserved.

Use focused real-widget tests for three or more freely positioned graphs, title-bar movement/resize where supported offscreen, tab/free round trips, close/reopen geometry, shared-toolbar ownership, busy/invalid guards and failed project-open rollback. Exercise real project save/load, old schema migration, malformed metadata and source-free reopen. Verify empty/all-closed state and deletion pruning. Capture normal and compact screenshots with several windows using the actual application stylesheet and inspect them. Existing styling has no supported dark-theme mode; do not claim dark-theme or manual native desktop validation.
