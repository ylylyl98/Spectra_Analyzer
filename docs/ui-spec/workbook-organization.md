# Workbook organization UI contract

Approved: one-file-per-Workbook default; current results update in place; explicit named snapshots; manual Workbook merge.

- Import setup: destination combo defaults to One Workbook per file; alternatives are One new Workbook (name entry) and an existing Workbook. Keep per-file preview/mapping and Import / Import and compare. Cancel leaves documents and data untouched.
- Workbook: named Original/Processed/Fit parameters/Gate-specific Fit curves tabs appear as data becomes available. Existing manually named tabs survive. Fixed role/source context replaces the mode switch as the ordinary selection mechanism.
- Table: read-only native Qt selection and TSV copy; numeric columns expose X/Y roles, text columns remain inspectable without plotting affordances. Original/Processed Gate-column linkage remains. Empty current results show an explicit message and keep their stable tab identity.
- Snapshot: Save snapshot… opens a name dialog; disabled/unavailable for empty data. Accept creates and opens an independent Sheet in the same Workbook; cancel changes nothing. Provenance states capture context and immutable snapshot behavior in plain text.
- Navigator: selecting at least two Workbooks enables Merge Workbooks…; destination chooser lists selected Workbooks. Keep destination window/identity and retire only source Workbook windows. Curves continue to refer to unchanged Sheet IDs.
- State/keyboard: native tab order, accessible names, Ctrl/Shift multiselection and focus treatment. Busy/invalid-editor state blocks mutations with feedback; cancelled dialogs are inert. Source labels update after merge.
- Normal and compact validation: readable names/provenance, useful table/plot area, scrollable controls, no destructive or clipboard action routed to an unrelated hidden document.
