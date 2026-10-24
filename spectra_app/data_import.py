"""Ephemeral content-based import preparation; no workspace or Qt mutation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import dat_io
from .background_jobs import DatImportError, LoadRequest, prepare_dat
from .dataset_state import initialize_dataset
from .plot_state import source_fingerprint
from .xy_io import XYTable, parse_xy


DATA_SUFFIXES = ('.dat', '.txt', '.csv', '.tsv', '.xy')
DATA_FILTER = 'Numeric data (*.dat *.txt *.csv *.tsv *.xy)'


@dataclass
class PreparedDataFile:
    path: Path
    kind: str
    reason: str
    tables: dict[str, XYTable]
    session: object = None
    sheet_id: str | None = None
    mapping: tuple | None = None
    option_errors: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.mapping is None:
            self.mapping = (0, tuple(range(1, self.table.data.shape[1])))

    @property
    def table(self):
        return self.tables[self.kind]


def cube_table(cube, path):
    return XYTable(np.column_stack((cube.energy, cube.z.T)),
                   [cube.energy_label] + [f'{cube.gate_label} = {value:g}' for value in cube.gate], path)


def prepare_data_batch(requests, defaults, context, *, preferred_kinds=None):
    """Detect data and prepare optional interpretations, retaining ordered failures."""
    entries, errors = [], []
    for index, request in enumerate(requests, 1):
        path = request.path
        context.report(f'Reading data {index}/{len(requests)}: {path.name}…')
        try:
            fingerprint = source_fingerprint(path)
            tables, option_errors, session = {}, {}, None
            layout = None
            try:
                layout = dat_io._read_dat_layout(path)
                reason = layout[-1]
            except ValueError as exc:
                reason = ''
                option_errors['dat'] = str(exc)
            kind = (preferred_kinds or {}).get(str(path), 'dat' if reason else 'xy')
            if kind not in ('dat', 'xy'):
                raise ValueError('Choose XY table or Gate matrix.')
            try:
                tables['xy'] = parse_xy(path.read_text(encoding='utf-8-sig'), source_path=path)
            except (OSError, ValueError) as exc:
                option_errors['xy'] = str(exc)
            context.check_cancelled()
            if layout is not None:
                try:
                    prepared = prepare_dat(request, context, _layout=layout)
                    session = initialize_dataset(path, prepared, defaults)
                    tables['dat'] = cube_table(session.base_cube, path)
                except (OSError, ValueError) as exc:
                    option_errors['dat'] = str(exc)
            if source_fingerprint(path) != fingerprint:
                raise ValueError('Source content changed while preparing. Import it again.')
            if kind not in tables:
                raise ValueError(option_errors[kind])
            entries.append(PreparedDataFile(path, kind,
                reason or 'No complete Gate layout; preserve all rows and columns as an XY table.',
                tables, session, option_errors=option_errors))
        except (OSError, ValueError) as exc:
            errors.append(DatImportError(path, str(exc)))
        context.check_cancelled()
    return entries, errors
