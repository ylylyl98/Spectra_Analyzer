# Independent graph documents

## Accepted outcome

The user approved improving the existing application and explicitly selected independent graph windows, tabbed/tiled display, retained state, and close/reopen behavior. This is the first implementation slice of that approved direction. Workbook/Sheet, arbitrary XY import, cross-file curve graphs, floating desktop windows, and persisted window geometry are subsequent work.

Each opened formal heatmap graph has its own live PlotPage keyed by `(dataset_id, graph_id)`. Switching datasets or graphs must retain other opened pages. The existing Analysis heatmap, current-Gate spectrum, and scientific controls stay linked in their existing Analysis view.

## User interaction

- Keep Analysis / Plot as outer modes. Plot contains a native QMdiArea, initially showing document tabs.
- Opening/selecting a graph in Plot opens or activates its document. Provide an explicit Open Graph action so a closed, already-selected graph can be reopened.
- Provide Tabbed View, Tile Graphs, and Close Graph View actions. Tiling switches to native subwindows and displays all open graphs together.
- Labels identify the source dataset and graph; renaming changes the label without changing identity.
- The selected graph owns the shared Plot property sidebar and graph commands. Clicking an inactive tile's annotation, property target, or command first selects that graph safely.
- Close Graph View and the subwindow close button finish valid pending edits and close only the view. Graph settings remain in the project and can be reopened. Explicit Remove Graph retains its existing destructive meaning and guards.
- Closing the final view leaves an understandable empty document area. Existing Analysis and project-save paths retain a valid current-owner editor. If other documents remain, select a visible neighbor and bind its properties consistently. Closing never changes scientific data or graph settings; the existing project-dirty behavior for a changed active graph/dataset selection may apply when selecting a neighbor. Closing the last view and changing layout alone leave project content unchanged.

## Implementation boundary

Use native PySide6 widgets and existing app styling; introduce no new dependencies or theme system. A small `graph_documents.py` owns the MDI container, keyed views, and native window lifecycle. MainWindow retains scientific ownership, graph activation, command dispatch, and shared property binding. `self.plot_page` remains the active-owner page alias for existing consumers, but outer Plot-mode checks must refer to the document container.

Cache page/editor ownership separately from visible subwindows. This permits close/reopen without deleting graph data and preserves Analysis/save callers that dereference the active page. On graph/dataset removal or project replacement, release all affected cached pages, detached sidebars, windows, and pending render requests.

No changes to GraphState/DatasetSession persistence or project schemas v1/v2 are required. Preserve source-free project reopening, ordinary DAT sidecars, frozen comparison windows, existing shared-science/per-graph-appearance semantics, and scientific job cancellation.

## Ownership and rendering

- Formal page validity must use explicit document ownership, independent of navigation-only increments of `_data_generation`. Keep that existing generation for scientific background work.
- A graph appearance/view edit retires only that graph's page. Shared scientific changes retire all affected graph pages for that dataset. Inactive format application retires the corresponding graph.
- Use existing detached graph snapshots for inactive documents; never project another graph through current widgets or read the active Matplotlib artists to render an inactive graph.
- Keep formal rendering serial on the GUI thread. Debounce visible dirty documents; in tiled mode include all visible documents, and in tabbed mode render the selected document. Hidden/closed documents may defer rendering until reopened.
- Reject commits for stale tokens, removed owners, or previous project lifetimes. Preserve invalid-draft export blocking.
- Finish outgoing edits before switching aliases. On validation/busy rejection, retain the previous owner, page, sidebar, and MDI selection without recursive activation.
- Page-originated actions carry a document key and activate that owner before dispatch. Shared shortcut, zoom, export, dirty-state and batch-progress connections must follow the selected page exactly once.

## Verification

Add behavior tests through real MainWindow/PlotPage/QMdiArea objects using isolated settings and synthetic DATs. Verify distinct retained pages across two graphs and two datasets; tab/tile identity; selected-owner edits and exports; close/reopen; correct removal/reset cleanup; dataset-wide science invalidation; inactive graph styling; invalid-draft switch rejection. Existing source-free project restoration and named graph tests remain regression boundaries.

Existing tests that select a PlotPage as the outer Plot tab require a mechanical update to select the new Plot container. Preserve their actual scientific, ownership, and editing assertions. Replacement-page expectations should become stronger retained-page and wrong-owner rejection checks.

Run only focused graph, formal-plot, UI ownership, project-reset and interaction tests. No full suite, build/packaging or installer/release validation is required. Inspect offscreen rendered screenshots at a normal and compact size using the application's existing stylesheet; distinguish this from a manual native desktop check. The existing stylesheet overrides dark palette colors, so this change does not claim dark-theme support or validation.
