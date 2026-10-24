"""Explicit numeric XY import, independent of heatmap DAT and seed CSV readers."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np


class XYParseError(ValueError):
    """A numeric table has an invalid header, width or body token."""


@dataclass
class XYTable:
    data: np.ndarray
    column_names: list[str]
    source_path: Path | None = None
    has_header: bool = True


_MISSING = {'', 'na', 'n/a', 'nan', 'null', '--'}


def _numeric(token):
    token = token.strip()
    return float('nan') if token.casefold() in _MISSING else float(token)


def _strip_comment(line):
    """Keep hashes inside CSV/TSV quoted fields, including doubled quotes."""
    if '#' not in line:
        return line
    quoted, field_start, index = False, True, 0
    while index < len(line):
        character = line[index]
        if quoted:
            if character == '"':
                if index + 1 < len(line) and line[index + 1] == '"':
                    index += 2
                    continue
                quoted = False
        elif character == '#':
            return line[:index]
        elif character == '"' and field_start:
            quoted, field_start = True, False
        else:
            field_start = character in ',\t'
        index += 1
    return line


def parse_xy(text: str, *, source_path: str | Path | None = None) -> XYTable:
    """Parse one optional header and numeric rows, preserving row/column order.

    Lines beginning with #, ; or // and trailing unquoted # comments are ignored.
    CSV and TSV preserve empty cells; whitespace tables use explicit NA/nan tokens.
    A first row beginning with a nonnumeric label is a header (numeric Gate
    labels are allowed). Rows beginning with a number are measurements.
    """
    rows, names, width, delimiter = [], None, None, None
    for line_number, original in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        if original.lstrip().startswith(('#', ';', '//')):
            continue
        line = _strip_comment(original)
        if not line.strip() and not ('\t' in line and (width is None or delimiter == '\t')):
            continue
        if width is None:
            delimiter = ',' if ',' in line else '\t' if '\t' in line else None
        try:
            tokens = (next(csv.reader([line], delimiter=delimiter, strict=True))
                      if delimiter else re.split(r'\s+', line.strip()))
        except csv.Error as exc:
            raise XYParseError(f'Invalid delimited row at line {line_number}: {exc}') from exc
        tokens = [token.strip() for token in tokens]
        if width is not None and len(tokens) != width:
            raise XYParseError(f'Inconsistent width at line {line_number}: expected {width} columns, got {len(tokens)}.')
        values, invalid = [], []
        for index, token in enumerate(tokens):
            try:
                values.append(_numeric(token))
            except ValueError:
                invalid.append(index)
        if width is None:
            width = len(tokens)
            if width < 2:
                raise XYParseError(f'XY data at line {line_number} needs at least two columns.')
            if invalid and invalid[0] == 0:
                if any(not token or '\x00' in token for token in tokens):
                    raise XYParseError(f'Invalid header at line {line_number}.')
                names = tokens
                continue
        if invalid:
            index = invalid[0]
            raise XYParseError(f'Nonnumeric value at line {line_number}, column {index + 1}: {tokens[index]!r}.')
        rows.append(values)
    if not rows:
        raise XYParseError('The file contains no numeric data rows.')
    return XYTable(np.asarray(rows, dtype=float), names or [f'Column {i + 1}' for i in range(width)],
                   None if source_path is None else Path(source_path), has_header=names is not None)


def load_xy(path: str | Path) -> XYTable:
    path = Path(path)
    if path.suffix.casefold() not in ('.dat', '.txt', '.csv', '.tsv'):
        raise XYParseError('XY import supports DAT, TXT, CSV and TSV files.')
    return parse_xy(path.read_text(encoding='utf-8-sig'), source_path=path)
