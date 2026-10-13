# Workspace shell UI contract

- Main reading order: Project navigation → Analysis/Workspace content → optional DAT details. Workbook and graph documents retain native child windows.
- Project navigation is persistent by default, independently resizable/collapsible, with compact import/organize actions and existing Ctrl/Shift selection. Data or graph activation does not replace the tree or discard its selection/expansion.
- Central tab says Workspace. Existing Analysis view remains a dedicated peer. Window arrangement commands remain in View.
- Right DAT details reuse current Data/Analysis/View/Plot controls. Hidden on Workbook/XY/empty Workspace; explicit visibility preference returns on DAT context. Toggle cannot expose unrelated DAT actions over a table or comparison.
- Workbook actions stay with the active Sheet. XY's curve inspector has a labeled toggle and retains selection/drafts/width on collapse. Heatmap commands display only for the active heatmap.
- All new controls use native Qt widgets, existing app styling, clear accessible names and keyboard reachability. No icons from a second family, custom painting, or theme infrastructure.
- Empty and busy states disable unavailable actions. Invalid editors block owner changes with existing feedback. Automatic layout changes never count as approving/discarding an editor.
- Validate at 1500×920 and approximately 1100×720, plus a larger application font. Inspect screenshots for tree width, useful table/plot region, menu reachability and detail-panel overflow. Only claim platforms/themes/scales actually checked.
