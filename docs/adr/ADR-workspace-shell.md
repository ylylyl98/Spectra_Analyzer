# ADR: retain document-owned operations while separating project navigation

Status: accepted within the user's explicitly approved shell change.

Context: project navigation shares a wide tabbed DAT editor sidebar, while GraphDocuments already owns all document lifetimes and Workbook/XY pages already own working commands. A shell migration must preserve draft validation and window ownership.

Options considered: (1) move navigation to a slim splitter panel, keep existing detail tabs in a contextual Qt dock and reuse local commands; (2) create a universal inspector/command dispatcher and reparent every document's controls into it.

Decision: option 1. A universal dispatcher would duplicate ownership/validation routing; moving XY inspectors out of pages would also require new GraphDocuments input-root/disposal contracts. The accepted approach confines new lifecycle behavior to shell visibility and a page-owned XY inspector toggle. No project schema change.

Consequences: Analysis remains a dedicated outer view; page-local Workbook/XY command layouts remain recognizable. Optional DAT details and optional XY inspection provide space without a mandatory three-column layout. Future command unification requires separate evidence of remaining workflow friction.
