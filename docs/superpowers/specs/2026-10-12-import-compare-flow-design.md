# Import → plot → compare, first slice

Approved scope: implement the concrete workflow proposed before the user's
“继续改进”, using existing Workbook, Sheet, comparison and document lifecycle APIs.

The XY file chooser opens one batch setup dialog. Parse all files before any
workspace mutation. Show a file list, the selected file's first 100 numeric rows,
per-file X and multi-Y choices, and an explicit new/existing workbook destination.
Import only and Import and compare are separate commit actions. Cancel leaves
the workspace and its open documents unchanged. Import and compare creates one
graph using every configured original array, without sorting or interpolation.

Mapping reuse requires unique exact names for named files. Headerless files
require identical generated column names/count/order; they do not match named
files whose literal names happen to be “Column 1”, etc. Report applied/skipped
files. `XYTable.has_header` records parsing provenance only, without a project
schema change. Retained Sheets have no persisted header-origin field and are
treated conservatively as named tables.

Reimport preserves existing source identity and original workbook ownership.
The dialog previews retained Sheet values and explains reuse. Validate mappings
against those actual retained columns before committing; source file changes do
not silently replace embedded data.

Workbook headers show letters, current X/Y roles and column names. Header
Ctrl/Shift selection chooses Y columns, excluding X. Ctrl+Space selects the
current column as Y. Ordinary cell/row/range selection remains a copy operation.
A header menu sets X for ordinary XY Sheets; DAT keeps its fixed X axis. Keep
the existing X/Y widgets inside an optional disclosure. Changing mappings emits
header updates without resetting the model. Raw/applied modes and DAT Gate
linkage remain available.

Appending validates pending target graph edits before mutating curves. Update
the target page if cached, but keep the source workbook active and preserve its
table selection, scroll and focus. A closed target stays closed. New comparisons
may activate their graph. Mouse clicks on Add preserve table focus; the button
remains keyboard reachable through Tab.

Excluded: drag/drop routing, navigator redesign, fitting, normalization, persisted
import presets, dependencies and project schema changes.
