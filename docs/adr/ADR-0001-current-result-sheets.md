# ADR-0001: Live current-result Sheets with explicit snapshots

Status: Accepted within the user's approved Workbook organization and current-result/snapshot preference (2026-10-12).

Current DAT cubes and FitResult records are already authoritative and persisted in DatasetSession. Current result Sheets can either copy these arrays into another result store on every mutation or project those records. Copying requires synchronizing two durable representations and duplicates potentially large scientific data. Live projections reuse the existing source ownership and are sufficient for the user's chosen current-result behavior.

Use role and optional Gate identity to define current projections. Synchronize their metadata and refresh open consumers at committed mutation boundaries. Keep cleared result projections empty, rather than silently retaining old values or destroying graph identities. One Fit-curves Sheet per Gate prevents unlike grids and UI Gate selection from changing graph sources.

The user also explicitly wants retained snapshots. These have different lifecycles: materialize them on request, retain captured provenance, and store numeric/text columns without pickle. They are detached from subsequent session mutations and dataset deletion. This is the necessary larger case beyond live projection alone.

Consequences: schema6 distinguishes projection identities and detached snapshot backing; schemas1-5 migrate without source access or refitting. Current-result lifecycle hooks require focused verification. Reconsider per-Gate tab presentation if measured large-batch usability requires it; do not weaken stable graph source identity.
