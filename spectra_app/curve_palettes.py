"""Deterministic curve palettes and strict, GUI-independent TXT/CSV imports.

RGB rows use one scale per file: integer tokens mean bytes (0..255). Any
decimal or exponent token selects normalized RGB (0..1) for all RGB rows.
Thus ``1,0,0`` means #010000, while ``1.0,0,0`` means #ff0000. Full-line
comments start with ``# `` (a space is required), ``;``, or ``//``.
"""

import csv
import math
import re
from numbers import Integral
from pathlib import Path

from matplotlib import colormaps
from matplotlib.colors import to_hex


CURVE_PALETTES = (
    'tab10', 'tab20', 'tab20b', 'tab20c', 'Set1', 'Set2', 'Set3',
    'Dark2', 'Paired', 'Accent', 'Pastel1', 'Pastel2', 'viridis',
    'plasma', 'inferno', 'magma', 'cividis', 'turbo', 'coolwarm',
    'RdBu', 'Spectral',
)
_QUALITATIVE_PALETTES = frozenset(CURVE_PALETTES[:12])
_MAX_TEXT_BYTES = 1024 * 1024
_MAX_COLORS = 4096
_HEX = re.compile(r'#?(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\Z')
_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
_HEADERS = {('hex',), ('color',), ('r', 'g', 'b')}


def palette_colors(name: str, count: int) -> list[str]:
    """Return native qualitative colors or evenly spaced continuous colors.

    Qualitative palettes cycle in source order. Continuous palettes use the
    interval [0.1, 0.9], or its midpoint when only one color is requested.
    """
    if not isinstance(name, str) or name not in CURVE_PALETTES:
        raise ValueError(f'Unknown curve palette: {name!r}')
    if isinstance(count, bool) or not isinstance(count, Integral) or count < 0:
        raise ValueError('Color count must be a non-negative integer.')
    if count == 0:
        return []
    cmap = colormaps[name]
    if name in _QUALITATIVE_PALETTES:
        native = [to_hex(color) for color in cmap.colors]
        return [native[index % len(native)] for index in range(count)]
    if count == 1:
        return [to_hex(cmap(.5))]
    return [to_hex(cmap(.1 + .8 * index / (count - 1))) for index in range(count)]


def parse_palette_text(text: str) -> list[str]:
    """Validate a whole HEX/RGB TXT or CSV palette and return #rrggbb colors.

    Optional first-row headers are ``hex``, ``color``, and ``R,G,B``. HEX
    values may have three or six digits, with or without #. Blank lines and
    full-line comments are ignored; inline comments are not accepted. The
    input limit is 1 MiB in UTF-8 and 4096 colors. Errors identify source lines.
    """
    if not isinstance(text, str):
        raise ValueError('Palette text must be a string.')
    if len(text) > _MAX_TEXT_BYTES:
        raise ValueError('Palette text exceeds the 1 MiB limit.')
    try:
        size = len(text.encode('utf-8'))
    except UnicodeEncodeError as exc:
        raise ValueError('Palette text must contain valid UTF-8 characters.') from exc
    if size > _MAX_TEXT_BYTES:
        raise ValueError('Palette text exceeds the 1 MiB limit.')

    entries: list[tuple[int, str | tuple[float, ...]]] = []
    normalized = False
    header_allowed = True
    for line_number, raw_line in enumerate(text.removeprefix('\ufeff').splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith((';', '//')):
            continue
        if line.startswith('#') and (len(line) == 1 or line[1].isspace()):
            continue
        try:
            fields = (next(csv.reader([line], strict=True))
                      if ',' in line or '"' in line else line.split())
        except csv.Error as exc:
            raise ValueError(f'Line {line_number}: Invalid CSV row: {exc}.') from exc
        fields = [field.strip() for field in fields]
        if header_allowed and tuple(field.casefold() for field in fields) in _HEADERS:
            header_allowed = False
            continue
        header_allowed = False

        if len(fields) == 1 and _HEX.fullmatch(fields[0]):
            color: str | tuple[float, ...] = to_hex('#' + fields[0].lstrip('#'))
        else:
            if len(fields) != 3 or not all(_NUMBER.fullmatch(field) for field in fields):
                raise ValueError(
                    f'Line {line_number}: Expected HEX (#RGB or #RRGGBB) or three RGB '
                    'numbers separated by commas or spaces.'
                )
            color = tuple(float(field) for field in fields)
            if not all(math.isfinite(value) for value in color):
                raise ValueError(f'Line {line_number}: RGB values must be finite numbers.')
            normalized |= any('.' in field or 'e' in field.lower() for field in fields)
        entries.append((line_number, color))
        if len(entries) > _MAX_COLORS:
            raise ValueError(f'Line {line_number}: Palette exceeds the 4096-color limit.')

    if not entries:
        raise ValueError('Palette contains no colors.')
    colors = []
    for line_number, color in entries:
        if isinstance(color, str):
            colors.append(color)
            continue
        limit = 1 if normalized else 255
        if any(value < 0 or value > limit for value in color):
            if normalized:
                message = ('Normalized RGB values must be between 0 and 1; decimal or exponent '
                           'notation selects this scale. Use integer values from 0 to 255 '
                           'for byte RGB.')
            else:
                message = 'Byte RGB values must be integers between 0 and 255.'
            raise ValueError(f'Line {line_number}: {message}')
        colors.append(to_hex(tuple(value / limit for value in color)))
    return colors


def read_palette_file(path: str | Path) -> list[str]:
    """Read a size-capped UTF-8 (optionally BOM-prefixed) palette file."""
    path = Path(path)
    try:
        with path.open('rb') as stream:
            content = stream.read(_MAX_TEXT_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"Cannot read palette file '{path.name}': {exc.strerror or exc}.") from exc
    if len(content) > _MAX_TEXT_BYTES:
        raise ValueError(f"Palette file '{path.name}' exceeds the 1 MiB limit.")
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError(f"Palette file '{path.name}' must be encoded as UTF-8.") from exc
    try:
        return parse_palette_text(text)
    except ValueError as exc:
        raise ValueError(f"Invalid palette file '{path.name}': {exc}") from exc
